"""attack_r5_common — shared helpers for the realism+safety wave adversarial
verification (round 5). READ-ONLY on product code; drives SimSession IN-PROCESS
only (NO :8878 server touched). Physics-on is turned on EXACTLY as server.py
does it: cfg_overrides={'spotting_enabled': True, 'grass_ros_floor_frac': 0.10}.

Deterministic: every session is seeded; no wall-clock anywhere.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wgresponse import geo, spread, fuels                # noqa: E402
from wgresponse.simui import SimSession, LOCATIONS        # noqa: E402
from wgresponse.contracts import CrewStatus              # noqa: E402

INF = float("inf")


def ignite_point(s, bearing_deg=None, km=9.0):
    """A BURNABLE ignition point ~km from the tile center along bearing_deg
    (default = the location's hazard-wind FROM bearing => upwind). Scans a few
    distances/bearing offsets until the sampled fuel cell is burnable, so no
    harness ever crashes on an urban/water cell. Deterministic (no rng)."""
    loc = LOCATIONS[s._loc.key]
    brg0 = loc.hazard_wind_dir_deg if bearing_deg is None else bearing_deg
    for dkm in (km, km + 1.5, km - 1.5, km + 3.0, km - 3.0, km + 5.0):
        for dbrg in (0.0, 12.0, -12.0, 25.0, -25.0, 40.0, -40.0):
            pt = geo.destination(loc.center, (brg0 + dbrg) % 360.0, dkm * 1000.0)
            code = geo.raster_sample(s._tile.fuel, pt)
            if code is not None and fuels.is_burnable(int(code)):
                return pt
    raise RuntimeError(f"no burnable ignition near {s._loc.key}")

# EXACT server physics-on mapping (server.py L304-306).
PHYS_ON = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}
PHYS_OFF = {"spotting_enabled": False, "grass_ros_floor_frac": 0.0}


def make_session(location="paradise_ca", n_clients=64, n_crews=5, seed="r5",
                 fire_zone="extended", physics=True, extra=None,
                 n_crews_1h=0, n_crews_3h=0):
    over = dict(PHYS_ON if physics else PHYS_OFF)
    if extra:
        over.update(extra)
    return SimSession(location, n_clients=n_clients, n_crews=n_crews, seed=seed,
                      fire_zone=fire_zone, cfg_overrides=over,
                      n_crews_1h=n_crews_1h, n_crews_3h=n_crews_3h)


def engine_exposure(s, pos):
    """Authoritative parity: call the ENGINE'S OWN sampler (_crew_exposure) so
    there is no 'different p10' argument. (min_nominal_s, min_eroded_p10_s)."""
    return s._crew_exposure(pos)


def raw_exposure(s, pos):
    """Secondary cross-check using the raw eta_at p10 + ember fallback (the
    metric the ORIGINAL founder repro used). Strictly >= eroded p10, so a
    weaker/independent lens on 'imminent'."""
    cfg = s._cfg
    best_nom = INF
    best_p10 = INF
    for f in s._fires.values():
        if f.ens is None:
            continue
        nom = geo.raster_sample(f.ens.members[0], pos)
        if nom is not None and nom < best_nom:
            best_nom = nom
        eta = spread.eta_at(f.ens, pos, fallback_radius_m=cfg.ember_threat_radius_m)
        if eta.eta_p10_s < best_p10:
            best_p10 = eta.eta_p10_s
    return best_nom, best_p10


def check_invariant(s, st):
    """THE headline invariant, evaluated on the just-emitted state() `st`.

    For every ARRIVED crew (INBOUND reinforcements are off-tile, skipped),
    using the ENGINE'S OWN eroded-P10 sampler and the SAME buffer the audit
    uses (zero lead):
      burned   = nominal member[0] arrival <= now
      imminent = eroded-P10 arrival < now + egress_buffer

    A crew is COVERED iff it appears in _crew_overruns (the honest 10c row).
    Violations:
      V1 (hidden burn)  : a BURNED crew that is NOT in _crew_overruns.
      V2 (idle-in-fire) : an IDLE crew (mode=='idle', the founder's g1/g2) that
                          is burned-or-imminent and NOT in _crew_overruns.
      V3 (hidden zero)  : any crew burned/idle-imminent while scoreboard
                          violations reads 0.
      V4 (accounting)   : scoreboard.violations != serviced-viol + overruns, or
                          crew_overruns count mismatch.
    Returns (ok, findings, stats).
    """
    now = s._now_s
    buffer_s = s._cfg.egress_buffer_min * 60.0
    overruns = s._crew_overruns
    overrun_ids = {o["crew_id"] for o in overruns}
    sb = st["scoreboard"]
    findings = []

    n_burned = n_idle_imm = n_exposed = 0
    for cid in sorted(s._crew_sims):
        cs = s._crew_sims[cid]
        if cs.status == CrewStatus.INBOUND:
            continue
        nom, p10 = engine_exposure(s, cs.pos)
        burned = (not math.isinf(nom)) and nom <= now
        imminent = (not math.isinf(p10)) and p10 < now + buffer_s
        mode = cs._mode
        relocating = cid in s._relocating
        if burned or imminent:
            n_exposed += 1
        if burned:
            n_burned += 1
        if mode == "idle" and (burned or imminent):
            n_idle_imm += 1

        # V1: a physically-burned crew MUST be an honest overrun (any mode).
        if burned and cid not in overrun_ids:
            findings.append(
                f"V1 HIDDEN BURN: {cid} mode={mode} status={cs.status.value} "
                f"nom_arr={(nom-now)/3600.0:+.2f}h NOT in crew_overruns "
                f"(reloc={relocating})")
        # V2: an IDLE crew in a burned/imminent cell MUST be an honest overrun
        # (the sweep should have relocated it OR it is genuinely stuck -> 10c).
        if mode == "idle" and (burned or imminent) and cid not in overrun_ids:
            findings.append(
                f"V2 IDLE-IN-FIRE: {cid} burned={burned} imminent={imminent} "
                f"p10_arr={(p10-now)/3600.0:+.2f}h NOT in crew_overruns")

    # V3: hidden zero.
    if (n_burned > 0 or n_idle_imm > 0) and sb["violations"] == 0:
        findings.append(
            f"V3 HIDDEN ZERO: {n_burned} burned + {n_idle_imm} idle-imminent "
            f"crews but scoreboard.violations==0")
    # V4: accounting.
    want_viol = len(s._violations) + len(overruns)
    if sb["violations"] != want_viol:
        findings.append(
            f"V4 ACCOUNTING: scoreboard.violations={sb['violations']} != "
            f"len(_violations)+len(overruns)={want_viol}")
    if sb.get("crew_overruns") != len(overruns):
        findings.append(
            f"V4 ACCOUNTING: scoreboard.crew_overruns={sb.get('crew_overruns')} "
            f"!= len(overruns)={len(overruns)}")

    stats = {
        "n_exposed": n_exposed, "n_burned": n_burned,
        "n_idle_imm": n_idle_imm, "overruns": len(overruns),
        "violations": sb["violations"], "relocations": sb["relocations"],
    }
    return (not findings), findings, stats


def drive(s, ignite_calls, max_ticks, invariant=True, extra_ignite=None):
    """Ignite per `ignite_calls` [(lat,lon,intensity,tick_to_fire), ...] where
    tick_to_fire=0 fires before the first tick; run max_ticks. Returns a per-run
    summary dict. If invariant, checks check_invariant every tick and records
    every failing tick.
    """
    fired = set()
    # fire the t=0 ignitions
    for i, (lat, lon, inten, at_tick) in enumerate(ignite_calls):
        if at_tick <= 0:
            s.ignite(lat, lon, intensity=inten)
            fired.add(i)

    fails = []
    max_overruns = 0
    max_reloc = 0
    total_exposed_ticks = 0
    ever_burned = False
    alarms = 0
    for tk in range(1, max_ticks + 1):
        for i, (lat, lon, inten, at_tick) in enumerate(ignite_calls):
            if i not in fired and at_tick == tk:
                s.ignite(lat, lon, intensity=inten)
                fired.add(i)
        if extra_ignite:
            extra_ignite(s, tk)
        st = s.tick()
        if invariant:
            ok, findings, stats = check_invariant(s, st)
            if stats["n_exposed"] > 0:
                total_exposed_ticks += 1
            if stats["n_burned"] > 0:
                ever_burned = True
            max_overruns = max(max_overruns, stats["overruns"])
            max_reloc = max(max_reloc, stats["relocations"])
            if not ok:
                fails.append((tk, findings, stats))
        # count fail-closed alarms in notes (cumulative note list)
    # scan notes once at the end for the no_safe_zone alarm
    alarm_notes = [n for n in s._notes if "no_safe_zone" in n
                   or "CREW SAFETY ALARM" in n]
    return {
        "fails": fails, "max_overruns": max_overruns,
        "max_reloc": max_reloc, "exposed_ticks": total_exposed_ticks,
        "ever_burned": ever_burned, "final_reloc": s._n_relocations,
        "alarm_notes": len(alarm_notes),
    }
