"""The off-domain horizon is PER FIRE (founder rule 2026-09-27, verbatim: "the
cap at extrapolation should include the rate of fire spread vs. drive time
through the region. the pathway only becomes unaccessible when the crossing
time between fire and road is less than 1 hour unless the fire has shown
rapid rate of spread"; contracts SAFETY SEMANTICS 15;
cfg.corridor_offraster_per_fire / watch --offmap-per-fire).

Pinned here: the knob OFF reproduces the pre-change engine byte for byte
(cycles, plans, /api/state, the persisted file and /api/world/summary) on a
world where the knob bites — goldens computed on commit 6c748c5; the per-fire
law itself (a known-slow fire extrapolates at its own P90 front speed,
floored and capped; rapid, unknown, stale, unrecognized and contradicted
reports keep the 1.7 m/s ceiling; bounded samples need max(leg margin,
60 min), modelled samples keep theirs; per-fire at the ceiling with no extra
margin IS the legacy law); the status seam (OR-combine with another status
table); the WFIGS behaviour fields; the surfacing (notes, CycleResult.offmap,
/api/state, the world summary); the Willow-shape refusal lifting; and the
flag.

Deterministic; no network; the REAL monitor / ladder / planner / StateStore /
_WatchDaemon over tests/offmap_stack.py + tests/seeded_stack.py stubs.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import offmap_stack as om  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import dispatch, feeds, geo, offmap, safety  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    UNREACHED,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    OffDomainHorizon,
    Raster,
    ResponseConfig,
    validate_config,
)

T0 = om.T0
H = om.H

#: KNOB-OFF GOLDENS: tests/offmap_stack.offmap_world_run() digests computed
#: on commit 6c748c5 (the contained-fire merge) — the engine BEFORE this rule
#: existed — over the knob-relevant world (the Willow-shape fire whose flat
#: 1.7 m/s bound refuses a job 80 km away), with --fail-closed armed.
#: Recompute: `py -3 scratch_review/offmap/golden_offmap.py <a git-archive of
#: 6c748c5>` (its docstring has the recipe).
_GOLDEN_CYCLES = [
    "821de6dfc989003d4f5c386e16f840cd4cbd8ddbfd88c32a08ba466def066331",
    "8faa6507e90e6950a186ee8b89c0d2f3a7a837f9a302acf77ffeffbd643b8f40",
    "05baa96b5f9386fe9fc9b74cf00b82df390339b2379250a2adeb4133e5bb0f56",
]
_GOLDEN_API = "d1f3a8b66917ad786216603f6eb7d4a3b5feec86467d711a21725376ce55a2ff"
_GOLDEN_STATE = "9aa6e1130e3c05c098a6f5f3d201de87d2f0750e758e101b7c7d4e1369e889df"
_GOLDEN_SUMMARY = "99e8f51655edd67cd02b94615e459ae706a987ca0112c32813644a3b8b4a1e2e"

PER_FIRE = dict(corridor_offraster_per_fire=True,
                corridor_offraster_margin_min=60.0)


def _on(**kw):
    return om.fail_closed_cfg(**dict(PER_FIRE, **kw))


def _run(tmp_path, cfg, incidents=None, cycles=1, dispatcher=None):
    clock = ss.SettableClock(T0)
    kw = {} if dispatcher is None else {"dispatcher": dispatcher}
    daemon, monitor, feed, engine = om.make_stack(
        tmp_path / "state", cfg, incidents=incidents, clock=clock, **kw)
    res = None
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * om.PERIOD_S
        res = daemon.run_cycle()
    return daemon, res


def _stops(res):
    return [(cid, st.client_id) for cid, cp in sorted(res.plan.plans.items())
            for st in cp.stops]


def _unserved(res):
    return [(u.client_id, u.reason) for u in res.plan.unserved]


# ---------------------------------------------------------------------------
# byte identity: the knob OFF is the pre-change engine
# ---------------------------------------------------------------------------

def test_knob_off_reproduces_the_pre_change_engine_byte_for_byte(tmp_path):
    cycles, api, state, summary = om.offmap_world_run(tmp_path / "state")
    assert cycles == _GOLDEN_CYCLES          # every CycleResult decision + plan
    assert api == _GOLDEN_API                # the /api/state payload
    assert state == _GOLDEN_STATE            # the persisted state file bytes
    assert summary == _GOLDEN_SUMMARY        # the /api/world/summary payload


def test_explicit_defaults_and_the_inert_companion_knobs_are_legacy(tmp_path):
    """Knob OFF: its three companions are never read — a changed floor,
    margin or behaviour age still reproduces the pre-change engine."""
    for extra in ({}, dict(corridor_offraster_ros_floor_mps=0.9,
                           corridor_offraster_margin_min=240.0,
                           fire_behavior_max_age_h=1.0)):
        cfg = om.fail_closed_cfg(corridor_offraster_per_fire=False, **extra)
        got = om.offmap_world_run(tmp_path / f"s{len(extra)}", cfg=cfg)
        assert got == (_GOLDEN_CYCLES, _GOLDEN_API, _GOLDEN_STATE,
                       _GOLDEN_SUMMARY)


def test_knob_off_adds_no_key_and_no_note(tmp_path):
    daemon, res = _run(tmp_path, om.fail_closed_cfg())
    assert res.offmap == {}
    assert not any("off-domain horizon" in n for n in res.notes)
    st = daemon.state()
    assert "offmap_rule" not in st
    assert all("offmap" not in row for row in st["incidents"])
    summary = daemon.world_summary()
    assert "offmap_rule" not in summary and "offmap" not in summary["counts"]
    assert all("offmap" not in row for row in summary["incidents"])
    # the refusal the rule lifts, as the legacy law makes it
    assert _unserved(res) == [("cB", "no_safe_corridor")]


def test_the_rule_is_inert_without_the_fail_closed_bound(tmp_path):
    """SEMANTICS 15 changes only the fail-closed bound: without it the plan
    is the legacy plan (the notes still say what each fire would get)."""
    legacy = _run(tmp_path / "a", ss.small_cfg())[1]
    armed = _run(tmp_path / "b", ss.small_cfg(**PER_FIRE))[1]
    assert ss._canon(armed.plan) == ss._canon(legacy.plan)
    assert sorted(armed.ensembles) == sorted(legacy.ensembles)
    assert set(armed.offmap) == {"inc-near", "inc-slow"}


# ---------------------------------------------------------------------------
# the Willow-shape world: the refusal lifts for a known-slow fire only
# ---------------------------------------------------------------------------

def test_known_slow_fire_extrapolates_at_its_own_front_and_the_job_is_served(
        tmp_path):
    daemon, res = _run(tmp_path, _on(), cycles=2)
    slow, near = res.offmap["inc-slow"], res.offmap["inc-near"]
    assert slow.kind == "per_fire" and slow.known and not slow.rapid
    assert slow.ros_mps == pytest.approx(0.3725, abs=1e-3)   # ~1.3 km/h
    assert slow.ros_mps == slow.front_p90_mps and slow.front_n > 10
    assert slow.reason == "per-fire: P90 front speed 1.3 km/h"
    assert slow.margin_min == 60.0
    assert near.kind == "unknown" and near.ros_mps == 1.7     # no report
    assert _stops(res) == [("G1", "cB")] and _unserved(res) == []
    # one note per domain fire, in the per-incident note format
    notes = [n for n in res.notes if "off-domain horizon" in n]
    assert len(notes) == 2
    assert any(n.startswith("incident inc-slow: off-domain horizon 1.3 km/h "
                            "(per-fire: P90 front speed 1.3 km/h); bounded "
                            "samples clear max(leg margin, 60 min)") and
               "'Minimal | Smoldering, Creeping' 2.1 h ago" in n for n in notes)


@pytest.mark.parametrize("meta, kind", [
    ({"fire_behavior": "Extreme", "fire_behavior_descriptors": ["Running"],
      "fire_behavior_at_s": T0 - H}, "rapid"),
    ({"fire_behavior": "Minimal", "fire_behavior_descriptors": ["Running"],
      "fire_behavior_at_s": T0 - H}, "rapid"),
    ({"fire_behavior": "Very Active", "fire_behavior_descriptors": [],
      "fire_behavior_at_s": T0 - H}, "rapid"),
    ({"fire_behavior": "Minimal", "fire_behavior_descriptors": ["Creeping"],
      "fire_behavior_at_s": T0 - 60 * H}, "unknown"),          # stale
    ({}, "unknown"),                                           # no report
    ({"fire_behavior": "Minimal", "fire_behavior_descriptors": ["Blowup"],
      "fire_behavior_at_s": T0 - H}, "unknown"),               # unrecognized
    ({"fire_behavior": "Minimal", "fire_behavior_descriptors": []}, "unknown"),
])
def test_rapid_unknown_stale_and_unrecognized_keep_the_ceiling(tmp_path, meta,
                                                               kind):
    incs = [om.slow_incident(meta=meta), om.near_incident()]
    res = _run(tmp_path, _on(), incidents=incs)[1]
    h = res.offmap["inc-slow"]
    assert h.kind == kind and h.ros_mps == 1.7
    assert h.reason == f"ceiling: {kind}"
    assert _unserved(res) == [("cB", "no_safe_corridor")]     # still refused


def test_a_contained_fire_still_reported_extreme_is_rapid():
    """0433 BREWER on 2026-09-27: 100 % contained, 'Extreme | Torching,
    Spotting, Running' — the contradiction resolves toward rapid."""
    inc = om.slow_incident(containment_pct=100.0, meta={
        "fire_behavior": "Extreme",
        "fire_behavior_descriptors": ["Torching", "Spotting", "Running"],
        "fire_behavior_at_s": T0 - 16.3 * H})
    known, rapid, reasons = offmap.status_for(inc, T0, ResponseConfig())
    assert known and rapid
    assert reasons[0].startswith("reported Extreme, Running ('Extreme | "
                                 "Torching, Spotting, Running', 16.3 h ago)")
    assert any("100% contained yet reported rapid" in r for r in reasons)


def test_status_vocabulary_and_age():
    cfg = ResponseConfig()

    def st(gen, descs, age_h=1.0, **kw):
        inc = om.slow_incident(meta={"fire_behavior": gen,
                                     "fire_behavior_descriptors": descs,
                                     "fire_behavior_at_s": T0 - age_h * H},
                               **kw)
        return offmap.status_for(inc, T0, cfg)[:2]

    assert st("Minimal", ["Smoldering", "Creeping", "Smoldering"]) == (True, False)
    assert st("Active", ["Flanking", "Backing"]) == (True, False)
    assert st("Moderate", []) == (True, False)
    assert st("", ["Creeping"]) == (True, False)
    for rapid in (["Wind Driven Runs"], ["Uphill Runs"], ["Crowning"],
                  ["Long-range Spotting"], ["long range spotting"]):
        assert st("Minimal", rapid) == (True, True)
    assert st("EXTREME", []) == (True, True)
    assert st("Minimal", ["Group Torching", "Spotting"]) == (True, False)
    assert st("Unknown", []) == (False, False)                # not vouched
    assert st("Minimal", ["Creeping"], age_h=48.0) == (True, False)
    assert st("Minimal", ["Creeping"], age_h=48.01) == (False, False)
    # a record stamped in the future (feed clock skew) reads as fresh
    assert st("Minimal", ["Creeping"], age_h=-2.0) == (True, False)
    tight = dataclasses.replace(cfg, fire_behavior_max_age_h=1.0)
    inc = om.slow_incident()                                  # 2 h old
    assert offmap.status_for(inc, T0, tight)[:2] == (False, False)


def test_floor_and_ceiling_clamp_the_front():
    cfg = _on()
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=100.0, nrows=21,
                    ncols=21)

    def planar(v):
        r = Raster(spec, fill=UNREACHED)
        for i in range(21):
            for j in range(21):
                r.set(i, j, T0 + j * 100.0 / v)
        return r

    known = (True, False, ("reported 'Minimal'",))
    slow = offmap.horizon_for("x", planar(0.1), known, cfg)
    assert slow.kind == "floor" and slow.ros_mps == 0.28
    assert slow.reason == ("floor: P90 front speed 0.4 km/h is under the "
                           "1.0 km/h floor")
    fast = offmap.horizon_for("x", planar(3.0), known, cfg)
    assert fast.kind == "ceiling_front" and fast.ros_mps == 1.7
    mid = offmap.horizon_for("x", planar(1.0), known, cfg)
    assert mid.kind == "per_fire" and mid.ros_mps == pytest.approx(1.0)
    assert offmap.horizon_for("x", planar(1.0), (True, True, ()), cfg).kind \
        == "rapid"


def test_a_fire_inside_its_domain_extrapolates_nothing():
    cfg = _on()
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=100.0, nrows=41,
                    ncols=41)
    r = Raster(spec, fill=UNREACHED)
    for i in range(15, 26):
        for j in range(15, 26):
            r.set(i, j, T0)
    h = offmap.horizon_for("x", r, (True, False, ()), cfg)
    assert h.kind == "no_bound" and h.front_n == 0
    er = safety._erode(r, cfg.corridor_erosion_m)
    assert safety._boundary_runs(er) == []          # nothing to extrapolate
    # 'no_bound' never hides a bound: whenever the eroded raster (500 m,
    # 5 cells) reaches the edge, the band (6 cells) says so too
    for row in (4, 5, 6, 7):
        r2 = Raster(spec, list(r.data))
        r2.set(row, 20, T0)
        runs = safety._boundary_runs(safety._erode(r2, cfg.corridor_erosion_m))
        kind = offmap.horizon_for("x", r2, (True, False, ()), cfg).kind
        assert not runs or kind != "no_bound", row
        assert (kind == "no_bound") == (row >= 6), row


def test_known_slow_without_a_measurable_front_keeps_the_ceiling():
    cfg = _on()
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=100.0, nrows=21,
                    ncols=21)
    r = Raster(spec, fill=UNREACHED)
    r.set(3, 10, T0)                       # inside the band, never on the edge
    h = offmap.horizon_for("x", r, (True, False, ()), cfg)
    assert h.kind == "no_front" and h.ros_mps == 1.7
    assert h.reason == "ceiling: no front crosses the domain edge to measure"


# ---------------------------------------------------------------------------
# the front statistic
# ---------------------------------------------------------------------------

def _spec(n=21, cell=100.0):
    return GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=cell, nrows=n,
                    ncols=n)


def test_planar_front_speed_is_recovered_exactly():
    spec = _spec()
    v = 0.5
    r = Raster(spec, [T0 + (k % 21) * 100.0 / v for k in range(21 * 21)])
    speeds = offmap.front_speeds_mps(r)
    assert len(speeds) == 21                  # east edge only: the rest lag
    assert all(s == pytest.approx(v) for s in speeds)
    assert offmap.front_speed_stats(r) == (pytest.approx(v), pytest.approx(v),
                                           21)


def test_an_oblique_edge_reads_fast_never_slow():
    """A front crossing an edge at an angle reads v / cos(angle) there:
    conservative (faster), never slower than the front runs."""
    spec = _spec()
    v, th = 0.5, math.radians(30.0)
    r = Raster(spec, fill=UNREACHED)
    for i in range(21):
        for j in range(21):
            r.set(i, j, T0 + (j * math.cos(th) + i * math.sin(th)) * 100.0 / v)
    speeds = sorted(offmap.front_speeds_mps(r))
    assert min(speeds) == pytest.approx(v / math.cos(th))    # east edge
    assert max(speeds) == pytest.approx(v / math.sin(th))    # south edge
    assert all(s >= v for s in speeds)


def test_p90_ignores_one_spotting_teleport_that_max_takes():
    spec = _spec()
    v = 0.5
    r = Raster(spec, [T0 + (k % 21) * 100.0 / v for k in range(21 * 21)])
    r.set(10, 19, r.get(10, 20) - 1.0)        # an ember lands 1 s ahead
    p90, mx, n = offmap.front_speed_stats(r)
    assert n == 21 and p90 == pytest.approx(v) and mx == pytest.approx(100.0)
    assert offmap.percentile_nearest_rank([3.0], 0.9) == 3.0
    assert offmap.percentile_nearest_rank(list(range(1, 11)), 0.9) == 9
    assert offmap.front_speed_stats(Raster(_spec(), fill=UNREACHED)) == (
        None, None, 0)


# ---------------------------------------------------------------------------
# the corridor law (safety.LegOracle) under the rule
# ---------------------------------------------------------------------------

CELL = 100.0
SPEED = 15.0
CFG_FC = ResponseConfig(corridor_fail_closed=True, corridor_unreached_bound=True)


def _fire(spec, ros=1.0, radius_cells=None):
    r = Raster(spec, fill=UNREACHED)
    n = spec.nrows
    cr = cc = n // 2
    for i in range(n):
        for j in range(n):
            dc = math.hypot(i - cr, j - cc)
            if radius_cells is None or dc <= radius_cells:
                r.set(i, j, T0 + dc * CELL / ros)
    return r


def _pt(spec, r, c):
    return geo.rc_to_latlon(spec, r, c)


def test_per_fire_at_the_ceiling_with_no_margin_is_the_legacy_law():
    spec = _spec()
    fire = _fire(spec)
    a, b = _pt(spec, 10, 10), _pt(spec, 10, 40)
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_FC)
    same = safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=1.7,
                            offraster_margin_min=0.0)
    assert same.sample_margin_s is None and legacy.sample_margin_s is None
    assert same.samples == legacy.samples
    assert same.latest_safe_depart_s == legacy.latest_safe_depart_s
    for dt in (-7200.0, -1.0, 0.0, 1.0, 3600.0):
        t = legacy.latest_safe_depart_s + dt
        assert same.verdict(t) == legacy.verdict(t)


def test_bounded_samples_need_the_crossing_margin_modelled_keep_theirs():
    spec = _spec()
    fire = _fire(spec)
    a, b = _pt(spec, 10, 10), _pt(spec, 10, 40)
    o = safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=1.7,
                         offraster_margin_min=60.0)
    leg_m = CFG_FC.route_margin_min * 60.0
    inside = [k for k, (_i, p, _v, _dt) in enumerate(o.samples)
              if safety._raster_value_at(fire, p) is not None]
    outside = [k for k in range(len(o.samples)) if k not in inside]
    assert inside and outside
    assert all(o.sample_margin_s[k] == leg_m for k in inside)
    assert all(o.sample_margin_s[k] == 3600.0 for k in outside)
    expect = min(v - m - dt for (_i, _p, v, dt), m in
                 zip(o.samples, o.sample_margin_s))
    assert o.latest_safe_depart_s == expect
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_FC)
    assert o.latest_safe_depart_s <= legacy.latest_safe_depart_s
    # the verdict agrees with T* (the O(1) fast path law) ...
    ts = o.latest_safe_depart_s
    assert o.verdict(ts - 1e-3).safe and not o.verdict(ts + 1.0).safe
    # ... and each failure names its law: a leg wholly off the raster fails
    # on the bound at 60 min, a leg wholly inside on the arrival at 56
    off = safety.LegOracle(_pt(spec, 10, 30), _pt(spec, 10, 40), SPEED, fire,
                           CFG_FC, offraster_ros_mps=1.7,
                           offraster_margin_min=60.0)
    bad = off.verdict(off.latest_safe_depart_s + 0.5)
    assert bad.reason.startswith(f"corridor unsafe at sample 1/{off.n_pts}")
    assert "off-domain bound (SAFETY SEMANTICS 15)" in bad.reason
    assert bad.reason.endswith("margin 3600s")
    ins = safety.LegOracle(_pt(spec, 10, 10), _pt(spec, 10, 18), SPEED, fire,
                           CFG_FC, offraster_ros_mps=1.7,
                           offraster_margin_min=60.0)
    bad = ins.verdict(ins.latest_safe_depart_s + 0.5)
    assert "eroded p10 arrival" in bad.reason
    assert bad.reason.endswith("margin 3360s")
    # a margin at or under the leg's own is the legacy law exactly
    o56 = safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=1.7,
                           offraster_margin_min=56.0)
    assert o56.sample_margin_s is None
    assert o56.latest_safe_depart_s == legacy.latest_safe_depart_s


@pytest.mark.parametrize("seed", range(12))
def test_slower_is_later_and_the_ceiling_is_never_looser(seed):
    rnd = random.Random(seed)
    spec = _spec(n=25)
    fire = _fire(spec, ros=rnd.uniform(0.2, 2.0),
                 radius_cells=rnd.choice([None, 14.0, 20.0]))
    a = _pt(spec, rnd.randint(0, 24), rnd.randint(0, 24))
    b = _pt(spec, rnd.randint(-40, 64), rnd.randint(-40, 64))
    legacy = safety.LegOracle(a, b, SPEED, fire, CFG_FC).latest_safe_depart_s
    ts = [safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=ros,
                           offraster_margin_min=60.0).latest_safe_depart_s
          for ros in (0.28, 0.6, 1.0, 1.7)]
    assert all(x >= y for x, y in zip(ts, ts[1:]))   # faster = earlier T*
    # rapid / unknown fires: the ceiling + the 60-min margin is never looser
    ceil60 = safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=1.7,
                              offraster_margin_min=60.0).latest_safe_depart_s
    assert ceil60 <= legacy
    slow = safety.LegOracle(a, b, SPEED, fire, CFG_FC, offraster_ros_mps=0.28,
                            offraster_margin_min=0.0).latest_safe_depart_s
    assert slow >= legacy                       # a slower bound only loosens


def test_no_raster_stays_unverifiable_under_the_rule():
    spec = _spec()
    o = safety.LegOracle(_pt(spec, 1, 1), _pt(spec, 5, 5), SPEED, None, CFG_FC,
                         offraster_ros_mps=0.28, offraster_margin_min=60.0)
    assert o.unverifiable and o.latest_safe_depart_s == -math.inf
    assert not o.verdict(T0).safe


# ---------------------------------------------------------------------------
# dispatch.plan(offmap=...)
# ---------------------------------------------------------------------------

def _plan_inputs(tmp_path):
    """One cycle of the world with the recorder, to replay dispatch.plan."""
    rec = ss.RecordingDispatcher()
    cfg = om.fail_closed_cfg()
    _run(tmp_path, cfg, dispatcher=rec)
    call = rec.calls[-1]
    return cfg, call


def test_dispatch_offmap_none_and_ceiling_rows_are_byte_identical(tmp_path):
    cfg, call = _plan_inputs(tmp_path)
    crews = [ss.crew(crew_id="G1", loc=om.CREW_LOC)]
    args = (crews, call["jobs"], call["ens"], [om.Z1, om.Z2], cfg,
            call["now_s"])
    base = ss._canon(dispatch.plan(*args))
    assert ss._canon(dispatch.plan(*args, offmap=None)) == base
    assert ss._canon(dispatch.plan(*args, offmap={})) == base
    same = {iid: (1.7, 0.0, "ceiling") for iid in call["ens"]}
    assert ss._canon(dispatch.plan(*args, offmap=same)) == base
    # malformed rows are dropped: that incident keeps the flat law
    junk = {"inc-slow": (0.0, 60.0), "inc-near": ("x", 1), "zz": None,
            "inc-other": (float("nan"), 0.0)}
    assert ss._canon(dispatch.plan(*args, offmap=junk)) == base
    assert dispatch._offmap_table(junk) == {}
    # the per-fire row lifts the refusal; an OffDomainHorizon works as-is
    row = OffDomainHorizon(incident_id="inc-slow", ros_mps=0.3725,
                           margin_min=60.0, reason="per-fire", kind="per_fire")
    p = dispatch.plan(*args, offmap={"inc-slow": row})
    assert [(u.client_id, u.reason) for u in p.unserved] == []
    assert [st.client_id for cp in p.plans.values() for st in cp.stops] == ["cB"]


# ---------------------------------------------------------------------------
# the status seam (the per-fire spread belief OR-combines here)
# ---------------------------------------------------------------------------

def test_merge_status_or_combines_tuples_and_objects():
    feed_slow = (True, False, ("reported 'Minimal'",))
    belief_rapid = SimpleNamespace(known=True, rapid=True,
                                   reasons=["belief: 4.1 km/h"])
    assert offmap.merge_status(feed_slow, belief_rapid) == (
        True, True, ("reported 'Minimal'", "belief: 4.1 km/h"))
    unknown = (False, False, ("no fire-behaviour report in the feed",))
    belief_slow = SimpleNamespace(known=True, rapid=False, reasons="belief")
    assert offmap.merge_status(unknown, belief_slow)[:2] == (True, False)
    assert offmap.merge_status(unknown, None) == unknown
    assert offmap.merge_status() == (False, False, ())


def test_horizons_take_another_status_table(tmp_path):
    daemon, res = _run(tmp_path, _on())
    incs = res.incidents
    rapid = {"inc-slow": SimpleNamespace(known=True, rapid=True, reasons=())}
    table = offmap.horizons(incs, res.ensembles, res.t_s, _on(), status=rapid)
    assert table["inc-slow"].kind == "rapid" and table["inc-slow"].ros_mps == 1.7
    knows = {"inc-near": (True, False, ("belief: slow",))}
    table = offmap.horizons(incs, res.ensembles, res.t_s, _on(), status=knows)
    assert table["inc-near"].kind in ("per_fire", "floor", "ceiling_front")
    assert table["inc-near"].status_reasons[-1] == "belief: slow"
    feed = offmap.feed_statuses(incs, res.t_s, _on())
    assert feed["inc-slow"][:2] == (True, False)
    assert feed["inc-near"][:2] == (False, False)


def test_a_failing_horizon_is_a_note_and_the_flat_law(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(offmap, "horizons", boom)
    daemon, res = _run(tmp_path, _on())
    assert res.offmap == {}
    assert any(n.startswith("off-domain horizons failed (synthetic)")
               for n in res.notes)
    assert _unserved(res) == [("cB", "no_safe_corridor")]


def test_a_dispatcher_without_the_kwarg_keeps_the_flat_law_loudly(tmp_path):
    rec = ss.RecordingDispatcher()         # its plan() takes no offmap
    daemon, res = _run(tmp_path, _on(), dispatcher=rec)
    assert any("takes no per-fire off-domain horizons" in n for n in res.notes)
    assert _unserved(res) == [("cB", "no_safe_corridor")]


def test_seeded_fires_are_unknown():
    from wgresponse.contracts import SeededIgnition
    ign = SeededIgnition(incident_id="seeded:x1", loc=om.B_LOC, t_ignite_s=T0)
    inc = ign.to_incident(T0)
    assert offmap.status_for(inc, T0, ResponseConfig())[:2] == (False, False)


# ---------------------------------------------------------------------------
# surfacing: /api/state, the world summary
# ---------------------------------------------------------------------------

def test_state_and_world_summary_carry_the_horizon_when_armed(tmp_path):
    daemon, res = _run(tmp_path, _on())
    st = daemon.state()
    assert st["offmap_rule"] == {
        "ceiling_mps": 1.7, "floor_mps": 0.28, "margin_min": 60.0,
        "fire_behavior_max_age_h": 48.0,
        "statistic": "P90 (nearest rank) of the raw-P10 boundary front speed",
        "semantics": "SAFETY SEMANTICS 15"}
    rows = {r["incident_id"]: r for r in st["incidents"]}
    slow = rows["inc-slow"]["offmap"]
    assert slow["kind"] == "per_fire" and slow["ros_kmh"] == pytest.approx(1.34)
    assert slow["margin"] == "bounded samples clear max(leg margin, 60 min)"
    assert slow["known"] is True and slow["rapid"] is False
    assert slow["status"] == ["reported 'Minimal | Smoldering, Creeping' 2.0 h ago"]
    assert rows["inc-near"]["offmap"]["reason"] == "ceiling: unknown"
    json.dumps(st, default=str)                       # JSON-able
    summary = daemon.world_summary()
    srows = {r["incident_id"]: r for r in summary["incidents"]}
    assert srows["inc-slow"]["offmap"] == slow
    assert summary["offmap_rule"] == st["offmap_rule"]
    assert summary["counts"]["offmap"] == {"per_fire": 1, "unknown": 1}


def test_world_page_shows_the_horizon_only_when_present():
    html = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "console", "world.html"),
        encoding="utf-8").read()
    assert "function offmapLine(r)" in html and "const om = offmapLine(r);" in html
    assert "if (!o) return" in html                   # absent = no line
    assert "removedContainedChip(c, d) + offmapChip(c)" in html
    assert "if (!c || !c.offmap) return" in html      # absent = no chip
    assert "off-domain ${Number(r.offmap.ros_kmh).toFixed(1)} km/h" in html
    assert "esc(o.reason)" in html and "esc(o.status.join" in html


def test_an_incident_without_a_domain_reads_null(tmp_path):
    far = FireIncident(incident_id="inc-far", name="Far Fire",
                       source=IncidentSource.MOCK,
                       origin=LatLon(38.0, -118.0), updated_at_s=T0 - 600.0,
                       acreage=5.0, containment_pct=0.0, active=True,
                       source_ids={"mock": "inc-far"})
    daemon, res = _run(tmp_path, _on(),
                       incidents=om.world_incidents() + [far])
    assert "inc-far" not in res.offmap
    rows = {r["incident_id"]: r for r in daemon.state()["incidents"]}
    assert rows["inc-far"]["offmap"] is None


# ---------------------------------------------------------------------------
# the feed: WFIGS behaviour fields
# ---------------------------------------------------------------------------

def _wfigs(attrs):
    points = {"features": [{"attributes": dict({
        "OBJECTID": 1, "IncidentName": "Willow",
        "IrwinID": "{548A8D28-1A04-48EE-9011-ABA023175D43}",
        "IncidentSize": 7389, "PercentContained": 80,
        "ModifiedOnDateTime_dt": 1790453660570, "IncidentTypeCategory": "WF"},
        **attrs), "geometry": {"x": -106.44, "y": 39.234}}]}

    def http(url, headers=None):
        body = points if "Incident_Locations" in url else {"features": []}
        return 200, json.dumps(body).encode("utf-8")
    (inc,) = feeds.WfigsFeed(http_get=http).fetch_active()
    return inc


def test_wfigs_carries_the_behaviour_report():
    for fld in ("FireBehaviorGeneral", "FireBehaviorGeneral1",
                "FireBehaviorGeneral2", "FireBehaviorGeneral3"):
        assert fld in feeds.WfigsFeed.POINT_OUT_FIELDS.split(",")
    inc = _wfigs({"FireBehaviorGeneral": "Minimal",
                  "FireBehaviorGeneral1": "Smoldering",
                  "FireBehaviorGeneral2": "Creeping",
                  "FireBehaviorGeneral3": "Smoldering"})
    assert inc.meta["fire_behavior"] == "Minimal"
    assert inc.meta["fire_behavior_descriptors"] == ["Smoldering", "Creeping",
                                                     "Smoldering"]
    assert inc.meta["fire_behavior_at_s"] == 1790453660.57
    # the live Willow record at the 2026-09-27 snapshot: known, not rapid
    known, rapid, _ = offmap.status_for(inc, 1790524542.7, ResponseConfig())
    assert (known, rapid) == (True, False)
    empty = _wfigs({"FireBehaviorGeneral": None, "FireBehaviorGeneral1": "",
                    "FireBehaviorGeneral2": "  ", "FireBehaviorGeneral3": None})
    assert not any(k.startswith("fire_behavior") for k in empty.meta)
    no_time = _wfigs({"FireBehaviorGeneral": "Minimal",
                      "ModifiedOnDateTime_dt": None})
    assert "fire_behavior_at_s" not in no_time.meta
    assert offmap.status_for(no_time, T0, ResponseConfig())[:2] == (False, False)


def test_fusion_keeps_the_wfigs_behaviour():
    w = _wfigs({"FireBehaviorGeneral": "Extreme",
                "FireBehaviorGeneral1": "Running"})
    firis = FireIncident(incident_id="firis:9", name="Willow",
                         source=IncidentSource.FIRIS, origin=w.origin,
                         updated_at_s=w.updated_at_s + 3600.0, acreage=7400.0,
                         irwin_id=w.irwin_id, source_ids={"firis": "9"},
                         meta={"perimeter_acres": 7300.0})
    (fused,) = feeds.fuse_incidents([[w], [firis]])
    assert fused.meta["fire_behavior"] == "Extreme"
    assert fused.meta["fire_behavior_descriptors"] == ["Running"]


# ---------------------------------------------------------------------------
# config, flag, simulator
# ---------------------------------------------------------------------------

def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert cfg.corridor_offraster_per_fire is False
    assert cfg.corridor_offraster_ros_floor_mps == 0.28
    assert cfg.corridor_offraster_margin_min == 0.0
    assert cfg.fire_behavior_max_age_h == 48.0
    assert validate_config(cfg) == []
    assert validate_config(_on()) == []
    bad = [dict(corridor_offraster_ros_floor_mps=0.0),
           dict(corridor_offraster_ros_floor_mps=float("nan")),
           dict(corridor_offraster_margin_min=-1.0),
           dict(corridor_offraster_margin_min=1e9),
           dict(fire_behavior_max_age_h=0.0),
           dict(corridor_offraster_per_fire=True,
                corridor_offraster_ros_floor_mps=2.0)]
    for kw in bad:
        probs = validate_config(dataclasses.replace(cfg, **kw))
        assert any(k in p for p in probs for k in kw), (kw, probs)
    # the floor above the ceiling only matters when the rule is armed
    assert validate_config(dataclasses.replace(
        cfg, corridor_offraster_ros_floor_mps=2.0)) == []
    probs = validate_config(dataclasses.replace(cfg,
                                                corridor_offraster_per_fire=1))
    assert any("corridor_offraster_per_fire must be a bool" in p for p in probs)


def test_watch_flag_parses_and_defaults_off():
    from wgresponse.cli import _build_parser
    base = ["watch", "--aoi", "data/aoi_vail_aspen.json"]
    assert _build_parser().parse_args(base).offmap_per_fire is False
    assert _build_parser().parse_args(
        base + ["--fail-closed", "--offmap-per-fire"]).offmap_per_fire is True


def test_the_simulator_never_passes_per_fire_horizons():
    import inspect
    from wgresponse import simui
    src = inspect.getsource(simui)
    assert "offmap" not in src and "corridor_offraster_per_fire" not in src
