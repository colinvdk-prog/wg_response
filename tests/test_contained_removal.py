"""Contained fires leave the map (founder ruling 2026-09-27, verbatim: "a fire
that is contained and no longer spreading is removed from the map"; contracts
SAFETY SEMANTICS 13a-d; cfg.drop_contained_incidents / watch --drop-contained).

Pinned here: the removal definition, signal by signal (a source at >= 100 %,
a containment / control / out timestamp, a contradicting source, the per-
source fusion evidence, the WFIGS + CAL FIRE fields that back them); a
removed fire builds no domain, no ETAs and no job and is absent from
/api/state incidents and the world summary, while the COUNT and its reason
are in the notes, CycleResult.removed_incidents, /api/state and the summary
counts; its pairs take SEMANTICS 6's absence law exactly like a vanished
feed incident's (cycle for cycle), freeze while a feed is blind, and IMPACTED
stays terminal; it returns when a source reports < 100 or it grows (then
stays for containment_no_growth_h), but not when its declaration merely goes
quiet; the recorded Chileno Fire (100 % contained, 2026-09-27 payloads) is
removed; and both knobs OFF reproduce the pre-change engine byte for byte on
a world where both would bite (goldens computed on commit a8bebec).

Deterministic; no network; the REAL monitor / ladder / planner / StateStore /
_WatchDaemon over tests/seeded_stack.py stubs.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contained_stack as cs  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import feeds, threat  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState,
    FireIncident,
    IncidentSource,
    LatLon,
    RemovedIncident,
    ResponseConfig,
    validate_config,
)

T0 = cs.T0
H = cs.H
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "contained_2026_09_27.json")

#: KNOB-OFF GOLDENS: contained_stack.baseline_world_run() digests computed on
#: commit a8bebec (the W6 merge) — the engine BEFORE the removal rule and the
#: anchor existed — over the knob-relevant world (a 100 %-contained fire on a
#: 72-h-old polygon + an 80 %-contained fire on a 100-h-old polygon that its
#: fresh record re-confirms). Recompute: `py -3 scratch_review/contained/golden_world.py
#: <a git-archive of a8bebec>` (see the module docstring of contained_stack).
_GOLDEN_WORLD_CYCLES = [
    "39adc2bd7e3f504def14af819a2ed6a4fbc628b1fff4de06136441f36bbeb581",
    "d7ffe2bff7befd9753cb95b30594d7480b99f78d72a85a1fd4d72f619c880ad9",
    "bf50a078f1c5ae270518d4e9ea6876825568a2e196a0bd2b462442a9e686cb41",
]
_GOLDEN_WORLD_API = "fede12d79b3fa5778dad575a665b43edd60a5e51856303d00f2249cdd0702bbf"
_GOLDEN_WORLD_STATE = "6bd997820d152449f8ed7f3d3b2918072d4dd8defeecb671b80d1f41b7883b94"


def _cfg(**kw):
    return ss.small_cfg(drop_contained_incidents=True, **kw)


def _stack(tmp_path, incidents, cfg=None, clock=None, **kw):
    clock = clock or ss.SettableClock(T0)
    parts = ss.make_stack(tmp_path / "state", cfg=cfg or _cfg(),
                          incidents=incidents, clock=clock, **kw)
    return (clock,) + parts


def _run(daemon, clock, k):
    clock.t = T0 + 60.0 + k * ss.PERIOD_S
    return daemon.run_cycle()


# ---------------------------------------------------------------------------
# byte-identity: both knobs OFF reproduce the pre-change engine
# ---------------------------------------------------------------------------

def test_knobs_off_reproduce_the_pre_change_engine_byte_for_byte(tmp_path):
    cycles, api, state = cs.baseline_world_run(tmp_path / "state")
    assert cycles == _GOLDEN_WORLD_CYCLES      # every CycleResult decision
    assert api == _GOLDEN_WORLD_API            # the /api/state payload
    assert state == _GOLDEN_WORLD_STATE        # the persisted state file bytes


def test_explicit_false_knobs_are_the_default(tmp_path):
    cfg = ss.small_cfg(drop_contained_incidents=False,
                       perimeter_confirmed_anchor=False)
    cycles, api, state = cs.baseline_world_run(tmp_path / "state", cfg=cfg)
    assert (cycles, api, state) == (_GOLDEN_WORLD_CYCLES, _GOLDEN_WORLD_API,
                                    _GOLDEN_WORLD_STATE)


def test_knob_off_keeps_the_contained_fire_and_adds_no_key(tmp_path):
    clock, daemon, *_ = _stack(tmp_path, cs.world_incidents(),
                               cfg=ss.small_cfg())
    res = _run(daemon, clock, 0)
    assert "inc-contained" in res.ensembles        # the legacy law: assessed
    assert res.removed_incidents == []
    st = daemon.state()
    assert "removed_contained" not in st and "removed_incidents" not in st
    assert "removed_contained" not in daemon.world_summary()["counts"]
    assert not any(n.startswith("removed ") for n in res.notes)


def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert cfg.drop_contained_incidents is False
    assert cfg.perimeter_confirmed_anchor is False
    assert cfg.perimeter_confirm_tolerance_frac == 0.02
    assert validate_config(cfg) == []
    for bad in (-0.01, 1.0, 5.0):
        probs = validate_config(dataclasses.replace(
            cfg, perimeter_confirm_tolerance_frac=bad))
        assert any("perimeter_confirm_tolerance_frac" in p for p in probs)
    probs = validate_config(dataclasses.replace(cfg, drop_contained_incidents=1))
    assert any("drop_contained_incidents must be a bool" in p for p in probs)


# ---------------------------------------------------------------------------
# the definition, signal by signal
# ---------------------------------------------------------------------------

def _inc(containment=None, meta=None, source=IncidentSource.WFIGS, iid="x"):
    return FireIncident(incident_id=iid, name="X", source=source,
                        origin=LatLon(39.0, -121.0), updated_at_s=T0,
                        acreage=100.0, containment_pct=containment,
                        meta=dict(meta or {}))


@pytest.mark.parametrize("containment, meta, contained, needle", [
    (100.0, None, True, "wfigs reports 100% contained"),
    (100.4, None, True, "wfigs reports 100.4% contained"),
    (99.9, None, False, "wfigs reports 99.9% contained"),
    (80.0, None, False, "wfigs reports 80% contained"),
    (None, None, False, None),                                   # unknown
    (None, {"contained_at_s": T0 - 3 * H}, True, "declared contained 3.0 h ago"),
    (None, {"controlled_at_s": T0 - 1 * H}, True, "declared controlled 1.0 h ago"),
    (None, {"out_at_s": T0 - 0.5 * H}, True, "declared out 0.5 h ago"),
    # a declaration contradicted by a reported percentage stays on the map
    (95.0, {"contained_at_s": T0 - H}, False, "wfigs reports 95% contained"),
    # garbage timestamps are no declaration
    (None, {"out_at_s": 0.0}, False, None),
    (None, {"out_at_s": float("nan")}, False, None),
    (None, {"out_at_s": True}, False, None),
])
def test_contained_signals_definition(containment, meta, contained, needle):
    signals, contra = threat.contained_signals(_inc(containment, meta), T0)
    assert (bool(signals) and not contra) is contained
    if needle:
        assert any(needle in s for s in signals + contra)


def test_every_source_must_agree_a_contradicting_source_keeps_it():
    """Fusion's per-source evidence: CAL FIRE at 99 % beside WFIGS at 100 %
    is NOT contained — whichever record is freshest."""
    both = _inc(100.0, {"containment_by_source": {"calfire": 99.0,
                                                  "wfigs": 100.0}})
    signals, contra = threat.contained_signals(both, T0)
    assert signals == ["wfigs reports 100% contained"]
    assert contra == ["calfire reports 99% contained"]
    agree = _inc(99.0, {"containment_by_source": {"calfire": 100.0,
                                                  "wfigs": 100.0}})
    s2, c2 = threat.contained_signals(agree, T0)
    assert len(s2) == 2 and c2 == []     # the by-source map, not the field


def test_fusion_carries_every_sources_containment_and_acreage():
    wf = FireIncident(incident_id="abc", name="Volcano", source=IncidentSource.WFIGS,
                      origin=LatLon(34.0, -118.0), updated_at_s=T0 - 3600.0,
                      acreage=500.0, containment_pct=100.0, irwin_id="abc",
                      source_ids={"wfigs": "abc"})
    cf = FireIncident(incident_id="calfire:v", name="Volcano Fire",
                      source=IncidentSource.CALFIRE,
                      origin=LatLon(34.001, -118.001), updated_at_s=T0,
                      acreage=510.0, containment_pct=95.0,
                      source_ids={"calfire": "v"},
                      meta={"out_at_s": T0 - 60.0})
    (fused,) = feeds.fuse_incidents([[wf], [cf]])
    assert fused.containment_pct == 95.0          # legacy field: freshest
    assert fused.meta["containment_by_source"] == {"calfire": 95.0,
                                                   "wfigs": 100.0}
    assert fused.meta["acres_by_source"] == {"calfire": 510.0, "wfigs": 500.0}
    assert fused.meta["out_at_s"] == T0 - 60.0
    signals, contra = threat.contained_signals(fused, T0)
    assert contra == ["calfire reports 95% contained"]       # stays on the map


def test_wfigs_and_calfire_carry_the_declaration_fields():
    """The adapters ask the live layers for ContainmentDateTime /
    ControlDateTime / FireOutDateTime (+ attr_ twins, attr_IncidentSize) and
    carry them in meta; CAL FIRE's ExtinguishedDate is its 'out'."""
    for fld in ("ContainmentDateTime", "ControlDateTime", "FireOutDateTime"):
        assert fld in feeds.WfigsFeed.POINT_OUT_FIELDS.split(",")
        assert "attr_" + fld in feeds.WfigsFeed.PERIM_OUT_FIELDS.split(",")
    assert "attr_IncidentSize" in feeds.WfigsFeed.PERIM_OUT_FIELDS.split(",")
    ms = 1000.0
    points = {"features": [{"attributes": {
        "OBJECTID": 1, "IncidentName": "Canyon", "IrwinID": "{ABC-1}",
        "IncidentSize": 512.0, "PercentContained": 100,
        "ModifiedOnDateTime_dt": (T0 - 600.0) * ms,
        "ContainmentDateTime": (T0 - 5 * H) * ms, "ControlDateTime": None,
        "FireOutDateTime": None, "IncidentTypeCategory": "WF"},
        "geometry": {"x": -118.6, "y": 34.1}}]}
    perims = {"features": [{"attributes": {
        "OBJECTID": 7, "poly_IncidentName": "Canyon", "poly_IRWINID": "{abc-1}",
        "poly_GISAcres": 510.0, "attr_PercentContained": 100,
        "attr_ModifiedOnDateTime_dt": (T0 - 600.0) * ms,
        "poly_DateCurrent": (T0 - 48 * H) * ms,
        "attr_ControlDateTime": (T0 - 2 * H) * ms, "attr_IncidentSize": 512.0},
        "geometry": {"rings": [[[-118.61, 34.09], [-118.59, 34.09],
                                [-118.59, 34.11], [-118.61, 34.09]]]}}]}

    def http(url, headers=None):
        body = points if "Incident_Locations" in url else perims
        return 200, json.dumps(body).encode("utf-8")

    (inc,) = feeds.WfigsFeed(http_get=http).fetch_active()
    assert inc.meta["contained_at_s"] == T0 - 5 * H          # point layer
    assert inc.meta["controlled_at_s"] == T0 - 2 * H         # perimeter layer
    assert "out_at_s" not in inc.meta                        # null: no key
    assert inc.meta["reported_acres"] == 512.0
    assert inc.meta["reported_at_s"] == T0 - 600.0
    assert inc.meta["perimeter_acres"] == 510.0              # poly_GISAcres
    assert inc.meta["perimeter_at_s"] == T0 - 48 * H
    rec = {"Name": "Old Fire", "UniqueId": "u-1", "Latitude": 34.0,
           "Longitude": -118.0, "Updated": "2026-09-27T15:00:00Z",
           "AcresBurned": 40.0, "PercentContained": 100.0, "IsActive": True,
           "ExtinguishedDate": "2026-09-27T12:00:00Z"}
    cal = feeds.CalFireFeed._from_record(rec)
    assert cal.meta["out_at_s"] == feeds._when_s("2026-09-27T12:00:00Z")
    assert cal.meta["reported_acres"] == 40.0
    assert "out_at_s" not in feeds.CalFireFeed._from_record(
        dict(rec, ExtinguishedDate="")).meta


# ---------------------------------------------------------------------------
# removal: no domain, no ETAs, counted everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("containment, meta", [
    (100.0, None),
    (None, {"contained_at_s": T0 - 3 * H}),
    (None, {"controlled_at_s": T0 - 3 * H}),
    (None, {"out_at_s": T0 - 3 * H}),
])
def test_each_signal_removes_the_fire_before_any_domain(tmp_path, containment,
                                                         meta):
    inc = cs.contained_incident(containment_pct=containment, meta=meta)
    clock, daemon, monitor, feed, engine, _n, _store, weather, terrain = _stack(
        tmp_path, [inc, cs.anchor_incident()])
    res = _run(daemon, clock, 0)
    assert [i.incident_id for i in res.incidents] == ["inc-anchor"]
    assert "inc-contained" not in res.ensembles
    assert all(a.incident_id != "inc-contained" for a in res.assessments)
    assert not any(k[1] == "inc-contained" for k in engine.states())
    (rem,) = res.removed_incidents
    assert isinstance(rem, RemovedIncident)
    assert rem.incident.incident_id == "inc-contained"
    assert rem.signals and rem.reason == "; ".join(rem.signals)
    assert rem.removed_since_s == clock.t
    # one terrain tile, one weather call: only the anchor fire's domain
    assert len(terrain.calls) == 1 and len(weather.calls) == 1
    note = next(n for n in res.notes if n.startswith("removed "))
    assert note.startswith("removed 1 contained incident(s) from the map")
    assert "inc-contained (Chileno-shape Fire: " in note
    assert "absence law" in note


def test_count_reported_in_state_and_world_summary(tmp_path):
    clock, daemon, *_ = _stack(tmp_path, cs.world_incidents())
    _run(daemon, clock, 0)
    st = daemon.state()
    assert st["removed_contained"] == 1
    (row,) = st["removed_incidents"]
    assert row["incident_id"] == "inc-contained"
    assert row["containment_pct"] == 100.0
    assert row["reason"] == "mock reports 100% contained"
    assert row["signals"] == ["mock reports 100% contained"]
    assert [r["incident_id"] for r in st["incidents"]] == ["inc-anchor"]
    summary = daemon.world_summary()
    assert summary["counts"]["removed_contained"] == 1
    assert summary["counts"]["incidents"] == 1
    assert [r["incident_id"] for r in summary["incidents"]] == ["inc-anchor"]
    json.dumps(st, default=str)
    # the world page reads the count off the summary (header chip)
    html = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "console", "world.html"),
        encoding="utf-8").read()
    assert "function removedContainedChip(" in html
    assert "c.removed_contained" in html and "d.removed_incidents" in html
    assert "removedContainedChip(c, d)" in html


def test_a_seeded_fire_is_never_removed(tmp_path):
    clock, daemon, monitor, *_ = _stack(tmp_path, [cs.contained_incident()])
    monitor.add_seed(ss.seed())
    res = _run(daemon, clock, 0)
    assert "seeded:test-near" in [i.incident_id for i in res.incidents]
    assert [r.incident.incident_id for r in res.removed_incidents] == [
        "inc-contained"]


def test_removal_runs_after_the_staleness_gate(tmp_path):
    """A contained fire whose record is > 7 days old is the staleness gate's
    (counted there), never double-counted as removed."""
    old = cs.contained_incident(updated_at_s=T0 - 200 * H,
                                perimeter_at_s=T0 - 300 * H)
    clock, daemon, *_ = _stack(tmp_path, [old])
    res = _run(daemon, clock, 0)
    assert res.removed_incidents == [] and res.incidents == []
    assert any(n.startswith("excluded 1 stale incident") for n in res.notes)


# ---------------------------------------------------------------------------
# the absence law — exactly a vanished feed incident's path
# ---------------------------------------------------------------------------

def _threatening(containment):
    """A fire 3 km upwind of c1 with a FRESH point record: EXTREME at once."""
    loc = ss.geo.destination(ss.NEAR_LOC, 270.0, 3000.0)
    return ss.feed_incident(iid="inc-t", name="Threat Fire", origin=loc,
                            updated_at_s=T0, acreage=300.0,
                            containment_pct=containment)


def _ladder(engine):
    return {k: r.state.value for k, r in sorted(engine.states().items())}


def test_removed_pairs_walk_exactly_the_vanished_incident_path(tmp_path):
    """Side by side: world A's fire reports 100 % from cycle 1 on; world B's
    fire vanishes from the feed at cycle 1. Same states, same events, cycle
    for cycle — CLEARED after clear_cycles with every feed healthy."""
    ca, da, ma, fa, ea, *_ = _stack(tmp_path / "a", [_threatening(40.0)])
    cb, db, mb, fb, eb, *_ = _stack(tmp_path / "b", [_threatening(40.0)])
    ra, rb = _run(da, ca, 0), _run(db, cb, 0)
    assert _ladder(ea) == _ladder(eb)
    assert ea.states()[("c1", "inc-t")].state is AlertState.EXTREME_24H
    fa.incidents = [_threatening(100.0)]     # the feed now says contained
    fb.incidents = []                         # the feed dropped the fire
    for k in (1, 2, 3):
        ra, rb = _run(da, ca, k), _run(db, cb, k)
        assert [(e.client_id, e.incident_id, e.prev, e.state) for e in ra.events] \
            == [(e.client_id, e.incident_id, e.prev, e.state) for e in rb.events]
        assert _ladder(ea) == _ladder(eb)
        assert [r.incident.incident_id for r in ra.removed_incidents] == ["inc-t"]
        assert not ra.degraded and not rb.degraded
    assert ea.states()[("c1", "inc-t")].state is AlertState.CLEARED
    assert ea.states()[("c1", "inc-t")].blind_cycles == 0


def test_removed_pairs_freeze_while_a_feed_is_blind(tmp_path):
    blind = ss.ScriptedFeed([], ok=False)
    clock, daemon, monitor, feed, engine, *_ = _stack(
        tmp_path, [_threatening(40.0)])
    monitor._feeds.append(blind)
    blind.ok = True
    _run(daemon, clock, 0)
    assert engine.states()[("c1", "inc-t")].state is AlertState.EXTREME_24H
    feed.incidents = [_threatening(100.0)]
    blind.ok = False
    for k in (1, 2, 3):
        res = _run(daemon, clock, k)
        assert res.degraded
        assert [r.incident.incident_id for r in res.removed_incidents] == ["inc-t"]
    rec = engine.states()[("c1", "inc-t")]
    assert rec.state is AlertState.EXTREME_24H          # FROZEN, never cleared
    assert rec.blind_cycles == 3


def test_impacted_stays_terminal_after_removal(tmp_path):
    """The legacy engine burned c1 with the 72-h-old polygon (IMPACTED);
    removing the fire later does not un-burn the pair."""
    clock, daemon, monitor, feed, engine, *_ = _stack(
        tmp_path, [cs.contained_incident(containment_pct=60.0)])
    for k in (0, 1):
        _run(daemon, clock, k)
    assert engine.states()[("c1", "inc-contained")].state is AlertState.IMPACTED
    feed.incidents = [cs.contained_incident(containment_pct=100.0)]
    for k in (2, 3, 4):
        res = _run(daemon, clock, k)
        assert len(res.removed_incidents) == 1
    assert engine.states()[("c1", "inc-contained")].state is AlertState.IMPACTED


# ---------------------------------------------------------------------------
# return to the map
# ---------------------------------------------------------------------------

def test_returns_when_containment_drops_below_100(tmp_path):
    clock, daemon, monitor, feed, *_ = _stack(tmp_path, [cs.contained_incident()])
    assert len(_run(daemon, clock, 0).removed_incidents) == 1
    feed.incidents = [cs.contained_incident(containment_pct=90.0)]
    res = _run(daemon, clock, 1)
    assert res.removed_incidents == []
    assert "inc-contained" in res.ensembles                   # a domain again
    assert any(n.startswith("incident inc-contained: back on the map — mock "
                            "reports 90% contained") for n in res.notes)
    # re-declared later: removed again, with a fresh first-removal time
    feed.incidents = [cs.contained_incident()]
    res = _run(daemon, clock, 2)
    (rem,) = res.removed_incidents
    assert rem.removed_since_s == clock.t


def test_returns_when_it_grows_and_stays_for_the_no_growth_window(tmp_path):
    cfg = _cfg(containment_no_growth_h=1.0)
    base = cs.ring_acres(cs.CONTAINED_RING)
    clock, daemon, monitor, feed, *_ = _stack(tmp_path, [cs.contained_incident()],
                                              cfg=cfg)
    assert len(_run(daemon, clock, 0).removed_incidents) == 1
    feed.incidents = [cs.contained_incident(acreage=base * 1.3)]   # still 100 %
    res = _run(daemon, clock, 1)
    assert res.removed_incidents == [] and "inc-contained" in res.ensembles
    note = next(n for n in res.notes if n.startswith("incident inc-contained:"))
    assert "back on the map" in note and "GREW" in note
    assert f"{base * 1.3:,.1f}" in note
    # no further growth, still inside the 1-h window: kept on the map
    res = _run(daemon, clock, 2)                                   # +5 min
    assert res.removed_incidents == []
    assert any("kept on the map" in n for n in res.notes)
    # the window passes without growth: contained and not spreading again
    clock.t = T0 + 60.0 + ss.PERIOD_S + 1.5 * H
    res = daemon.run_cycle()
    assert [r.incident.incident_id for r in res.removed_incidents] == [
        "inc-contained"]


def test_growth_is_per_source_not_a_fused_source_flip(tmp_path):
    """Two sources disagreeing on size (fused acreage flips with freshness)
    is not growth; one source's own rise is."""
    def fused(acres_by_source):
        return cs.contained_incident(meta={"acres_by_source": acres_by_source,
                                           "containment_by_source": {
                                               "calfire": 100.0,
                                               "wfigs": 100.0}})
    clock, daemon, monitor, feed, *_ = _stack(
        tmp_path, [fused({"calfire": 345.0, "wfigs": 341.0})])
    assert len(_run(daemon, clock, 0).removed_incidents) == 1
    feed.incidents = [fused({"calfire": 345.0, "wfigs": 341.0,
                             "firis": 360.0})]                 # a new source
    assert len(_run(daemon, clock, 1).removed_incidents) == 1
    feed.incidents = [fused({"calfire": 345.0, "wfigs": 350.0,
                             "firis": 360.0})]                 # wfigs grew
    res = _run(daemon, clock, 2)
    assert res.removed_incidents == []
    assert any("GREW (wfigs 341.0 -> 350.0 ac" in n for n in res.notes)


def test_a_quiet_declaration_keeps_it_removed(tmp_path):
    """The fire left the WFIGS Current layer (by that layer's definition a
    declared-contained fire does) and only its old FIRIS polygon remains —
    no source reports containment any more, none contradicts it: removed
    stays removed, and says why."""
    clock, daemon, monitor, feed, *_ = _stack(tmp_path, [cs.contained_incident()])
    assert len(_run(daemon, clock, 0).removed_incidents) == 1
    feed.incidents = [cs.contained_incident(containment_pct=None)]
    res = _run(daemon, clock, 1)
    (rem,) = res.removed_incidents
    assert rem.reason.startswith("last reported contained (mock reports 100% "
                                 "contained); no source contradicts it")
    # ... but a monitor that never saw the declaration keeps the legacy law
    clock2, daemon2, *_ = _stack(tmp_path / "fresh",
                                 [cs.contained_incident(containment_pct=None)])
    res2 = _run(daemon2, clock2, 0)
    assert res2.removed_incidents == [] and "inc-contained" in res2.ensembles


def test_memory_forgets_fires_unseen_for_the_stale_window(tmp_path):
    clock, daemon, monitor, feed, *_ = _stack(tmp_path, [cs.contained_incident()])
    _run(daemon, clock, 0)
    assert "inc-contained" in monitor._contained
    feed.incidents = []
    clock.t = T0 + 200 * H
    daemon.run_cycle()
    assert "inc-contained" not in monitor._contained


# ---------------------------------------------------------------------------
# recorded data: the Chileno Fire, 2026-09-27
# ---------------------------------------------------------------------------

def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _replay_http(fx, points=(), perims=(), firis=()):
    bodies = {"Incident_Locations": {"features": list(points)},
              "Interagency_Perimeters": {"features": list(perims)},
              "FIRIS": {"features": list(firis)}}

    def http(url, headers=None):
        for frag, body in bodies.items():
            if frag in url:
                return 200, json.dumps(body).encode("utf-8")
        raise AssertionError(url)
    return http


def test_recorded_chileno_is_removed_and_its_homes_never_alert(tmp_path):
    """The W6 finding: Chileno Fire, 100 % contained, a 158-h-old FIRIS
    polygon fused onto a 15-h-old WFIGS record — the legacy engine spreads
    the polygon forward 158 h. Recorded payloads through the REAL adapters
    and fusion: with the knob it leaves the map before any domain."""
    fx = _fixture()
    ch = fx["chileno"]
    http = _replay_http(fx, points=[ch["point"]], firis=[ch["firis"]])
    batches = [feeds.WfigsFeed(http_get=http).fetch_active(),
               feeds.FirisFeed(http_get=http).fetch_active()]
    (fused,) = feeds.fuse_incidents(batches)
    now = fx["recorded_at_s"]
    assert fused.name.upper() == "CHILENO"
    assert fused.containment_pct == 100.0
    assert (now - fused.meta["perimeter_at_s"]) / H > 150.0   # the FIRIS polygon
    assert (now - fused.updated_at_s) / H < 16.0               # the WFIGS record
    signals, contra = threat.contained_signals(fused, now)
    assert signals == ["wfigs reports 100% contained"] and contra == []
    home = LatLon(fused.origin.lat, fused.origin.lon + 0.03)   # ~2.6 km east
    from wgresponse.contracts import ClientProperty
    feed = ss.ScriptedFeed([fused])
    clock = ss.SettableClock(now)
    daemon, monitor, _f, engine, *_ = ss.make_stack(
        tmp_path / "s", cfg=_cfg(), feed=feed, clock=clock,
        registry_clients=[ClientProperty(client_id="h1", loc=home)])
    res = daemon.run_cycle()
    assert res.incidents == [] and res.ensembles == {}
    assert [r.incident.incident_id for r in res.removed_incidents] == [
        fused.incident_id]
    assert engine.states() == {}
    # the same payloads without the knob: the legacy law builds the domain
    clock_b = ss.SettableClock(now)
    daemon_b, *_ = ss.make_stack(
        tmp_path / "legacy", cfg=ss.small_cfg(), feed=ss.ScriptedFeed([fused]),
        clock=clock_b, registry_clients=[ClientProperty(client_id="h1",
                                                        loc=home)])
    assert fused.incident_id in daemon_b.run_cycle().ensembles


# ---------------------------------------------------------------------------
# the watch flags
# ---------------------------------------------------------------------------

def test_watch_flags_parse_and_default_off():
    from wgresponse.cli import _build_parser
    from tests.test_fix_program_2 import PRODUCTION_WATCH_ARGS
    off = _build_parser().parse_args(PRODUCTION_WATCH_ARGS)
    assert off.drop_contained is False and off.confirmed_perimeters is False
    on = _build_parser().parse_args(list(PRODUCTION_WATCH_ARGS)
                                    + ["--drop-contained",
                                       "--confirmed-perimeters"])
    assert on.drop_contained is True and on.confirmed_perimeters is True
    assert on.fail_closed and on.phi_vector and on.solver_evals == 20000
