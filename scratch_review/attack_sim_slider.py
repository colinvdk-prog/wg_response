"""ADVERSARIAL VERIFIER — simulator attack #2: THE SAFETY SLIDER.

Brief item 2, three claims under attack:

  B1. MONOTONICITY (real santa_rosa_ca tile, fixed seed + ignition script):
      buffer sweep 10 -> 120: served count must be monotonically
      NON-INCREASING; violations 0 on every tick of every run; the slider
      must demonstrably BIND somewhere (served drop OR the marginal client's
      unserved reason escalating to the hard deadline at high buffers).
  B2. LIVE RE-PLAN (real tile): changing the buffer mid-run revokes every
      unstarted stop immediately and re-plans at the very next tick, with
      tightened latest_depart for re-planned stops.
  B3. FROZEN-STOP RUG-PULL (the blocking probe; controlled checkerboard
      geometry — the suite's own — because the engine's corridor law keeps
      real-tile service margins in hours): raise the buffer to 120 while a
      crew is MID-DEPLOY on a stop whose departure violates the new rule.
      The brief requires the stop be withdrawn/aborted or loudly flagged;
      silently completing it is a BLOCKING finding. simui.py's module
      docstring DOCUMENTS grandfathering (audit uses the buffer captured at
      adoption), so this test is EXPECTED TO FAIL — its failure IS the
      finding, with the repro numbers in the assertion message.

Run from repo root:
    py -3 -m pytest scratch_review/attack_sim_slider.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import (  # noqa: E402
    client_p10_map, pick_ignition, skip_reason, tile_present)
from wgresponse import geo  # noqa: E402
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile  # noqa: E402

KEY = "santa_rosa_ca"
SEED = "atk-slider"
SWEEP = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 75.0, 90.0, 105.0, 120.0)


def _need_tile(key=KEY):
    if not tile_present(key):
        pytest.skip(skip_reason(key))


def _sweep_session(buf):
    s = SimSession(KEY, 60, 1, seed=SEED,
                   cfg_overrides={"egress_buffer_min": float(buf)})
    ign, _ = pick_ignition(s, KEY, min_m=1500, max_m=3000, cluster=True)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    return s


# ---------------------------------------------------------------------------
# B1 — monotonic safety trade-off (real tile)
# ---------------------------------------------------------------------------

def test_served_monotonic_nonincreasing_in_buffer():
    _need_tile()
    served = {}
    unserved = {}
    for buf in SWEEP:
        s = _sweep_session(buf)
        for _ in range(36):
            st = s.tick()
            assert st["scoreboard"]["violations"] == 0, (buf, st["audit"])
        served[buf] = st["scoreboard"]["served"]
        unserved[buf] = st["scoreboard"]["unserved_by_reason"]
    sys.stderr.write(f"\n[slider sweep] served: {served}\n"
                     f"[slider sweep] unserved: {unserved}\n")
    assert served[SWEEP[0]] >= 1, f"vacuous sweep — nothing served: {served}"
    for lo, hi in zip(SWEEP, SWEEP[1:]):
        assert served[hi] <= served[lo], (
            f"MONOTONICITY BROKEN: buffer {hi} served {served[hi]} > "
            f"buffer {lo} served {served[lo]} — full sweep {served} "
            f"(seed={SEED}, 36 ticks, speed 2x)")
    # binding evidence: a served drop OR the hard deadline reason appearing
    # at the high end while absent at the low end (reason escalation).
    dropped = served[SWEEP[-1]] < served[SWEEP[0]]
    escalated = ("infeasible_deadline" in unserved[SWEEP[-1]]
                 and "infeasible_deadline" not in unserved[SWEEP[0]])
    assert dropped or escalated, (
        f"slider never bound at all: served {served}, unserved {unserved}")


def test_sweep_is_deterministic_at_fixed_buffer():
    _need_tile()
    outs = []
    for _ in range(2):
        s = _sweep_session(30.0)
        h = []
        for _ in range(12):
            st = s.tick()
            h.append((st["scoreboard"]["served"],
                      st["scoreboard"]["min_margin_h"],
                      len(st["serviced"])))
        outs.append(h)
    assert outs[0] == outs[1], "same seed+script must replay identically"


# ---------------------------------------------------------------------------
# B2 — mid-run change re-plans within one tick (real tile)
# ---------------------------------------------------------------------------

def test_buffer_change_replans_within_one_tick_real_tile():
    _need_tile()
    # seed 'rug-2' + 1 crew: probed to hold an en-route 5-stop itinerary at
    # tick 1 (queue pressure makes the revocation observable).
    s = SimSession(KEY, 24, 1, seed="rug-2")
    ign, _ = pick_ignition(s, KEY, min_m=1500, max_m=3000, cluster=True)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    st1 = None
    armed = False
    for _ in range(30):
        st1 = s.tick()
        if st1["plan"] is not None and st1["plan"]["stops"] and any(
                (c["status"] == "en_route" and c["itinerary"])
                or (c["status"] == "deploying" and len(c["itinerary"]) >= 2)
                for c in st1["crews"]):
            armed = True
            break
    if not armed:
        pytest.skip("no revocable itinerary within 30 ticks — B2 not armed "
                    "(report this)")
    t_before = st1["plan"]["computed_at_s"]
    stops1 = {r["client_id"]: r for r in st1["plan"]["stops"]}

    s.set_buffer(90.0)
    st_now = s.state()
    for c in st_now["crews"]:
        if c["status"] == "en_route":
            assert not c["itinerary"], (
                f"crew {c['crew_id']} kept an unstarted itinerary through a "
                f"buffer change: {c['itinerary']}")
        elif c["status"] == "deploying":
            assert len(c["itinerary"]) <= 1, (
                f"crew {c['crew_id']} kept QUEUED stops through a buffer "
                f"change: {c['itinerary']}")

    st2 = s.tick()
    assert st2["scoreboard"]["buffer_min"] == 90.0
    assert st2["scoreboard"]["violations"] == 0
    if st2["plan"] is not None and st2["plan"]["stops"]:
        assert st2["plan"]["computed_at_s"] > t_before, "no re-plan next tick"
        assert st2["plan"]["computed_at_s"] == st1["sim_now_s"]
        stops2 = {r["client_id"]: r for r in st2["plan"]["stops"]}
        for cid in set(stops1) & set(stops2):
            assert stops2[cid]["latest_depart_s"] < stops1[cid]["latest_depart_s"], (
                f"{cid}: latest_depart not tightened by the +60 min buffer")


# ---------------------------------------------------------------------------
# B3 — the frozen-stop rug-pull (BLOCKING probe)
# ---------------------------------------------------------------------------

FAST_TIGHT = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
              "slack_min": 0.0, "egress_buffer_min": 10.0}


def _checkerboard_dir(base, key="boulder_co", size_km=20.0, cell_m=500.0,
                      hamlet_half=3):
    """The suite's own porous-hamlet geometry (tests/test_simui.py), rebuilt
    here so the rug-pull runs on controlled terrain where near-deadline
    service margins are constructible."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(
        bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = os.path.join(base, "tiles")
    os.makedirs(d, exist_ok=True)
    save_ascii_tile(tile, os.path.join(d, f"{key}.wgt"))
    return d


def test_frozen_stop_rechecked_against_new_buffer():
    """THE BLOCKING PROBE (expected to fail on the current engine).

    Arm: checkerboard hamlet, 16 clients, 1 crew, high fire 8 km upwind,
    buffer 10 / slack 0 — tick until the crew is MID-DEPLOY on a stop whose
    (current p10 - planned_depart) < 100 min. Pull: set_buffer(120). The new
    rule's latest_depart = p10 - 120 min is now BEFORE the stop's departure.
    Required behavior (verifier brief): withdraw/abort the frozen stop, or at
    minimum flag it; silently completing it means the safety slider does not
    protect the crew currently on a property."""
    base = tempfile.mkdtemp(prefix="atk_rug_")
    d = _checkerboard_dir(base)
    loc = LOCATIONS["boulder_co"]
    s = SimSession("boulder_co", 16, 1, seed="m1",
                   cfg_overrides=dict(FAST_TIGHT), tile_dir=d)
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 8000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")

    pulled = None
    completed = None
    verdicts = []
    for _ in range(60):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        p10 = client_p10_map(st)
        if pulled is None:
            for c in st["crews"]:
                if c["status"] != "deploying" or not c["itinerary"]:
                    continue
                cur = c["itinerary"][0]
                v = p10.get(cur["client_id"])
                if v is None:
                    continue
                if (v - cur["planned_depart_s"]) / 60.0 <= 100.0:
                    s.set_buffer(120.0)
                    st_after = s.state()
                    crew_after = next(x for x in st_after["crews"]
                                      if x["crew_id"] == c["crew_id"])
                    pulled = {
                        "tick": st["tick"], "crew": c["crew_id"],
                        "client": cur["client_id"],
                        "gap_min": round((v - cur["planned_depart_s"]) / 60.0, 1),
                        "kept": bool(crew_after["itinerary"] and
                                     crew_after["itinerary"][0]["client_id"]
                                     == cur["client_id"]),
                    }
                    break
        else:
            for sv in st["serviced"]:
                if sv["client_id"] == pulled["client"]:
                    completed = sv
            if completed is not None:
                v = p10.get(pulled["client"])
                if v is not None:
                    aud = [a for a in st["audit"]
                           if a["client_id"] == pulled["client"]]
                    verdicts.append({
                        "tick": st["tick"],
                        "deficit_min_vs_new_buffer": round(
                            (completed["depart_s"] - (v - 120.0 * 60.0)) / 60.0, 2),
                        "engine_violations": st["scoreboard"]["violations"],
                        "audit_flag": aud[0]["violation"] if aud else None,
                    })
                if len(verdicts) >= 5:
                    break
    if pulled is None:
        pytest.skip("could not arm a mid-deploy rug-pull — PROBE "
                    "INCONCLUSIVE (report this)")
    sys.stderr.write(f"\n[rug-pull] {pulled} completed={completed} "
                     f"verdicts={verdicts}\n")
    if completed is None:
        assert not pulled["kept"], (
            f"frozen stop kept in itinerary but never completed: {pulled}")
        return  # withdrawn/aborted — compliant

    silent = [v for v in verdicts
              if v["deficit_min_vs_new_buffer"] > 1e-6
              and v["engine_violations"] == 0 and v["audit_flag"] is False]
    assert not silent, (
        "BLOCKING — FROZEN STOP SILENTLY KEPT UNDER NEW BUFFER: buffer was "
        f"raised 10->120 at tick {pulled['tick']} while {pulled['crew']} was "
        f"mid-deploy on {pulled['client']} (p10 - planned_depart = "
        f"{pulled['gap_min']} min < 120); set_buffer kept the stop "
        f"(kept={pulled['kept']}), the crew stayed on site past the new "
        f"rule's latest-depart and the stop completed at "
        f"depart_s={completed['depart_s']}, violating the new 120-min rule "
        f"by {silent[0]['deficit_min_vs_new_buffer']} min at completion — "
        f"yet scoreboard.violations == 0 and the audit entry says "
        f"violation=False (margin computed under the superseded 10-min rule; "
        f"no payload field marks the stop as pre-change). "
        f"Repro: checkerboard boulder_co geometry, cfg {FAST_TIGHT}, "
        f"seed 'm1', ignite 8 km upwind at 'high', speed 1, pull at first "
        f"mid-deploy gap <= 100 min. Full verdicts: {verdicts}")


def test_completed_history_not_retro_relabeled():
    """The flip side (must hold even after B3 is fixed): stops fully
    COMPLETED before the slider moves keep violation=False — history is not
    retroactively re-labeled by a later policy change."""
    _need_tile()
    # the sweep scenario at buffer 10 — probed to serve by tick 2.
    s = _sweep_session(10.0)
    st = None
    for _ in range(36):
        st = s.tick()
        if st["scoreboard"]["served"] >= 1:
            break
    if st["scoreboard"]["served"] < 1:
        pytest.skip("nothing serviced in 36 ticks — retro check not armed "
                    "(report this)")
    pre = {a["client_id"] for a in st["audit"]}
    s.set_buffer(120.0)
    for _ in range(4):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, (
            f"RETROACTIVE RE-LABEL: completed stops flagged after a later "
            f"buffer change: {st['audit']}")
        for a in st["audit"]:
            if a["client_id"] in pre:
                assert a["violation"] is False
