"""attack_r3_gel.py — FOUNDER ROUND 3 law 5 (GEL RING): every serviced client
carries gel_applied=true + a sane, immutable gelled_at_s that PERSISTS through
later alert-state changes (incl. IMPACTED); a NEVER-serviced home never shows
gel_applied.

Attacks:
  1. gel_applied IFF in the append-only serviced ledger — checked for EVERY
     client on EVERY tick (this alone proves 'never-serviced never gelled',
     including a home a crew started then WITHDREW from).
  2. gelled_at_s is SANE (finite, within [sim start, sim now], equals the
     serviced depart_s) and IMMUTABLE (set once, never changes / never moves
     backward across ticks).
  3. PERSISTENCE THROUGH IMPACTED: a gelled home that the fire later reaches
     (state -> impacted) keeps gel_applied=true with an UNCHANGED gelled_at_s.
  4. WITHDRAWN != GELLED: a crew that aborts an in-progress deploy (safety
     withdrawal) records NO ServicedStop, so the abandoned home shows
     gel_applied=false — the gel really was not applied (honesty).
"""
from __future__ import annotations

import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_r3_common import make_session, tile_present, skip_loud       # noqa: E402
from attack_sim_common import pick_ignition                             # noqa: E402
from wgresponse import geo                                              # noqa: E402
from wgresponse.contracts import LatLon                                 # noqa: E402


def _serviced_ledger(st):
    """client_id -> first serviced depart_s, from the state's append-only
    serviced list (mirrors state()'s gel_at setdefault)."""
    led = {}
    for s in st["serviced"]:
        led.setdefault(s["client_id"], s["depart_s"])
    return led


# ===========================================================================
# 1+2+3. core invariants over a rich real-tile run
# ===========================================================================
def _run_gel(key, seed, intensity):
    """One full run; the per-tick LAW-5 invariants (1)+(2) are asserted here
    (they must hold on EVERY attempted config). Returns (ever_serviced,
    impacted_while_gelled) so the caller can require the persistence path was
    exercised."""
    sess = make_session(key, 40, 4, seed=seed)
    p, _t = pick_ignition(sess, key, cluster=False)
    sess.ignite(p.lat, p.lon, intensity=intensity)
    first_gel = {}
    ever_serviced = 0
    impacted_while_gelled = 0
    for _ in range(70):
        st = sess.tick()
        t0, now = st["t0_s"], st["sim_now_s"]
        led = _serviced_ledger(st)
        for c in st["clients"]:
            cid = c["client_id"]
            gelled = cid in led
            # (1) gel_applied IFF serviced
            assert c["gel_applied"] is gelled, \
                f"{key}: {cid} gel_applied={c['gel_applied']} but " \
                f"serviced={gelled} (gel flag decoupled from the ledger)"
            if not gelled:
                assert c["gelled_at_s"] is None, \
                    f"{key}: un-serviced {cid} has gelled_at_s=" \
                    f"{c['gelled_at_s']}"
                continue
            ga = c["gelled_at_s"]
            # (2) sane: equals the serviced depart, finite, within the clock
            assert ga == led[cid], \
                f"{key}: {cid} gelled_at_s {ga} != serviced depart {led[cid]}"
            assert ga is not None and math.isfinite(ga), \
                f"{key}: {cid} gelled_at_s not finite: {ga}"
            assert t0 - 1e-6 <= ga <= now + 1e-6, \
                f"{key}: {cid} gelled_at_s {ga} outside [t0={t0}, now={now}]"
            # (2) immutable: set once, never moves
            if cid in first_gel:
                assert ga == first_gel[cid], \
                    f"{key}: {cid} gelled_at_s CHANGED {first_gel[cid]} -> {ga}"
            else:
                first_gel[cid] = ga
            # (3) persistence through IMPACTED (a gelled home the fire reached)
            if c["state"] == "impacted":
                impacted_while_gelled += 1
        ever_serviced = max(ever_serviced, len(led))
    return ever_serviced, impacted_while_gelled


# deterministic fallback order: seed varies placement so a hard tile still puts
# gelled homes in the fire's path (persistence-through-IMPACTED needs the fire
# to actually overrun a serviced home).
_GEL_CONFIGS = (("g2", "extreme"), ("g3", "extreme"), ("g2", "high"),
                ("g3", "high"), ("r3-gel", "extreme"))


@pytest.mark.parametrize("key", ["malibu_ca", "boulder_co"])
def test_gel_invariants_over_run(key):
    if not tile_present(key):
        pytest.skip(skip_loud(key, "data/tiles/*.wgt"))
    best = (0, 0)
    for seed, intensity in _GEL_CONFIGS:
        served, impacted = _run_gel(key, seed, intensity)   # asserts invariants
        best = (max(best[0], served), max(best[1], impacted))
        if served > 0 and impacted > 0:
            return                    # persistence path exercised + all invariants held
    if best[0] == 0:
        pytest.skip(f"SKIP-LOUDLY: {key} serviced nothing across "
                    f"{len(_GEL_CONFIGS)} configs (developed-cell limitation); "
                    f"the gel-iff-serviced invariant still held every tick")
    assert best[1] > 0, \
        f"{key}: homes were gelled ({best[0]}) but none ever reached IMPACTED " \
        f"across {len(_GEL_CONFIGS)} configs — persistence path not exercised"


# ===========================================================================
# 4. a WITHDRAWN (aborted) deploy is NOT gelled — replicates the pinned
#    rug-pull trigger (checkerboard tile + spot-fire eta collapse + set_buffer)
# ===========================================================================
def _checkerboard_dir(tmp, key="boulder_co", size_km=20.0, cell_m=500.0,
                      hamlet_half=3):
    from wgresponse.terrain import SyntheticTerrain, save_ascii_tile
    from wgresponse.simui import LOCATIONS
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(bbox,
                                                                         cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = os.path.join(tmp, "tiles")
    os.makedirs(d, exist_ok=True)
    save_ascii_tile(tile, os.path.join(d, f"{key}.wgt"))
    return d


def _arm_mid_deploy(s, max_ticks=60):
    for _ in range(max_ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        for c in st["crews"]:
            if c["status"] == "deploying" and c["itinerary"]:
                return st, c, c["itinerary"][0]
    raise AssertionError("scenario failed to arm a mid-deploy stop")


def _inject_spot_fire(s, st, stop, dist_m=1250.0, step_m=250.0):
    victim = stop["client_id"]
    cl = next(c for c in st["clients"] if c["client_id"] == victim)
    vloc = LatLon(cl["lat"], cl["lon"])
    f1 = next(f for f in st["fires"] if f["incident_id"] == stop["incident_id"])
    w_new = (geo.bearing_deg(LatLon(f1["lat"], f1["lon"]), vloc) + 180.0) % 360.0
    d = float(dist_m)
    for _ in range(60):
        p = geo.destination(vloc, w_new, d)
        try:
            return s.ignite(p.lat, p.lon, intensity="high", wind_dir_deg=w_new)
        except ValueError:
            d += step_m
    raise AssertionError("no burnable cell upwind of the deploying client")


def test_withdrawn_home_is_not_gelled():
    from wgresponse.simui import SimSession, LOCATIONS
    KEY = "boulder_co"
    FAST_TIGHT = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
                  "dispatch_objective": "value"}
    with tempfile.TemporaryDirectory() as tmp:
        d = _checkerboard_dir(tmp, KEY)
        loc = LOCATIONS[KEY]
        s = SimSession(KEY, 16, 1, seed="m1", cfg_overrides=FAST_TIGHT,
                       tile_dir=d, roads_module=None)
        ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 4_600.0)
        s.ignite(ign.lat, ign.lon, intensity="high")

        st, crew, cur = _arm_mid_deploy(s)
        victim = cur["client_id"]
        # the home is being gelled RIGHT NOW but is NOT yet in the ledger
        pre = next(c for c in st["clients"] if c["client_id"] == victim)
        assert pre["gel_applied"] is False, \
            "a home mid-deploy (not yet finished) already shows gel_applied"

        _inject_spot_fire(s, st, cur)
        s.tick()                      # eta collapses onto the victim
        s.set_buffer(120.0)           # tighten -> in-flight deploy withdraws
        now = s.state()

        assert now["scoreboard"]["withdrawn"] == 1, \
            "the rug-pull did not withdraw the in-flight deploy (re-pin)"
        wids = {w["client_id"] for w in now["withdrawn"]}
        assert victim in wids
        # THE HONESTY LAW: the withdrawn home was NEVER gelled
        vic = next(c for c in now["clients"] if c["client_id"] == victim)
        assert vic["gel_applied"] is False and vic["gelled_at_s"] is None, \
            f"withdrawn home {victim} falsely shows gel_applied=" \
            f"{vic['gel_applied']} / gelled_at_s={vic['gelled_at_s']} — the " \
            f"gel was aborted, this is a safety-honesty violation"
        # and it never entered the serviced ledger
        assert victim not in _serviced_ledger(now)
