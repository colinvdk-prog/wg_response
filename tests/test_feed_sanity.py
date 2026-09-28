"""Feed sanity (contracts SAFETY SEMANTICS 18; wgresponse.feedsanity;
docs/review/feed_sanity_2026-09-27.md).

Byte identity with cfg.feed_sanity OFF — pinned against goldens computed on
commit 0bbd430, BEFORE any feed-sanity code existed (tests/feed_sanity_stack
.py; scratch_review/feed_sanity/fs_golden.py), on a world where every guard
bites; what the knob changes on that world (legacy: a terminal IMPACTED from
an ancient stamp, a late P10 from a collapsed seed, a fake surprise reset,
fake rapid flags, a fake known-slow fire; guarded: none of them); the pure
checks; the belief's observers one hazard at a time; the monitor's seed side
(memory, substitution, expiry, one alarm, drop, time fallback, knob off
untouched); the CLI flag; config bounds.

Deterministic; no network.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feed_sanity_stack as fss  # noqa: E402
import fire_belief_stack as fbs  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import cli, feedsanity  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState,
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    UNREACHED,
    validate_config,
)
from wgresponse.fire_belief import ACRE_M2, FireBeliefStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

H = 3600.0
DAY = 86400.0
T0 = ss.T0
CENTER = LatLon(39.0, -121.02)

#: PRE-FEED-SANITY GOLDENS: tests/feed_sanity_stack.hazard_run() digests
#: computed with scratch_review/feed_sanity/fs_golden.py on the 0bbd430
#: engine (main, the belief-vs-physics merge), BEFORE any feed-sanity code
#: existed — five hazard fires (collapse + re-stamp, future stamp, ancient
#: stamp, reported reset, bogus area figure), three clients, one crew, one
#: safe zone, the deployed watcher's posture (belief tracking, fail-closed +
#: per-fire off-domain horizon, confirmed anchor, model error 0.71, p50
#: bound), five cycles four hours apart, through the REAL monitor / ladder /
#: planner / StateStore / _WatchDaemon / worldview. cfg.feed_sanity OFF must
#: reproduce them.
_GOLDEN_CYCLES = [
    "a7f26920e803ad0c7207b3d6f59aac9ccc9c43b7572efa79c40f4f719913e16b",
    "734c0bb4b56476e3821b40093f8bc1a48b9b5a6c87e09486042a5852420005bf",
    "10d8781d85e6719d670600a7c3ab2ae881dc07d3045ed54c364bf40e16793cfc",
    "02c4262e70190ff9171a1377b70d86d08e899a79bafca0f27fc8b967313ec7a8",
    "2605aa62dec4de3132c544b04d1cd92fbef4860b8f3942677c315869cf0a0d41",
]
_GOLDEN_API = "319eea760ecd5281f84605a18e8bb66aeef36ac53131679825b92f5912b0e2d9"
_GOLDEN_STATE = "f4706db50915a8c27d355b4a57f0a0c463ccef3302c3857db4baf196f4333749"
_GOLDEN_WORLD = "dc18ce6011079299893a672c7c67f005f2fb76362efd45182f8c62b48681c407"
_GOLDEN_BELIEF = "1d80de6aab90e5cfe66d3b0f55c438ddd66cb9155a4d7599ffe691cf22fe1052"


# ---------------------------------------------------------------------------
# 1. byte identity (the contracts law) + the hazard world, both ways
# ---------------------------------------------------------------------------

def test_knob_off_reproduces_the_0bbd430_goldens(tmp_path):
    cycles, api, state, world, belief = fss.hazard_run(tmp_path / "state")
    assert cycles == _GOLDEN_CYCLES          # every CycleResult decision
    assert api == _GOLDEN_API                # the /api/state payload
    assert state == _GOLDEN_STATE            # the persisted state bytes
    assert world == _GOLDEN_WORLD            # the /api/world/summary payload
    assert belief == _GOLDEN_BELIEF          # the fire_beliefs.json content


@pytest.fixture(scope="module")
def hazard_runs(tmp_path_factory):
    """(legacy CycleResults, legacy monitor, guarded CycleResults, guarded
    monitor) on the pinned hazard world."""
    out = []
    for cfg in (fss.hazard_cfg(), fss.hazard_cfg(feed_sanity=True)):
        clock = ss.SettableClock(T0)
        daemon, monitor, feed, _eng, _store = fss.make_stack(
            tmp_path_factory.mktemp("s"), cfg=cfg, clock=clock)
        keep = []
        for k in range(fss.N_CYCLES):
            feed.incidents = fss.incidents(k)
            clock.t = fss.t_cycle(k)
            keep.append(daemon.run_cycle())
        out += [keep, monitor]
    return out


def _p10_h(res, client, incident):
    a = next(a for a in res.assessments
             if a.client_id == client and a.incident_id == incident)
    return (a.eta.eta_p10_s - res.t_s) / H


def _windows(monitor, iid, kind=None):
    return [w for w in monitor.fire_beliefs.belief(iid).windows
            if kind is None or w["kind"] == kind]


def test_legacy_hazard_world_shows_every_hazard(hazard_runs):
    """What the audit warned about, on the unguarded engine (the goldens'
    world): each hazard corrupts the belief or mis-anchors the seed."""
    legacy, mon = hazard_runs[0], hazard_runs[1]
    # (b) an ancient stamp burns the domain before now: every client in it
    # EXTREME on the first cycle, then terminal IMPACTED
    impacted = {(e.client_id, e.incident_id) for r in legacy for e in r.events
                if e.state is AlertState.IMPACTED}
    assert impacted == {("c1", "ancient-1"), ("c2", "ancient-1"),
                        ("c3", "ancient-1")}
    assert _p10_h(legacy[0], "c1", "ancient-1") < -20000.0
    # (b) the collapsed polygon is seeded: c1's P10 runs late that cycle
    assert _p10_h(legacy[1], "c1", "collapse-1") > \
        _p10_h(legacy[0], "c1", "collapse-1") + 1.0
    # (a) a fake shrink then a fake jump: a surprise reset + rapid
    grow = [(round(w["aa"] / ACRE_M2), round(w["ab"] / ACRE_M2))
            for w in _windows(mon, "collapse-1", "perimeter")]
    assert grow[:2] == [(6178, 4), (4, 6427)]
    assert any(o.kind == "fire_belief" and "RESET" in o.message
               for r in legacy for o in r.operator_events)
    st = legacy[-1].fire_status
    assert st["collapse-1"].rapid and st["reset-1"].rapid and \
        st["figure-1"].rapid
    # (a) the clamped future stamp is re-observed as zero growth: known, slow
    fut = _windows(mon, "future-1")
    assert len(fut) == 4 and all(w["ab"] == w["aa"] for w in fut)
    assert st["future-1"].known and not st["future-1"].rapid
    assert legacy[-1].offmap["future-1"].kind == "per_fire"   # roads open


def test_guarded_hazard_world_removes_each_hazard(hazard_runs):
    legacy, guarded, mon = hazard_runs[0], hazard_runs[2], hazard_runs[3]
    # no alert of any kind from the ancient fire; its seed took the record's
    # own time instead of three years ago
    assert not [e for r in guarded for e in r.events
                if e.incident_id == "ancient-1"]
    assert all(_p10_h(r, "c1", "ancient-1") > 0.0 for r in guarded)
    # the collapse cycle seeds the last plausible polygon: c1's P10 does not
    # run late, and it is never later than the legacy's
    for k in range(fss.N_CYCLES):
        assert _p10_h(guarded[k], "c1", "collapse-1") <= \
            _p10_h(legacy[k], "c1", "collapse-1") + 1e-9
    assert _p10_h(guarded[1], "c1", "collapse-1") == pytest.approx(
        _p10_h(guarded[0], "c1", "collapse-1"))
    # the belief: no shrink window, no fake jump, no reset, no fake rapid
    for iid in ("collapse-1", "reset-1", "figure-1", "future-1"):
        assert all(w["ab"] >= w["aa"] for w in _windows(mon, iid)), iid
    per = _windows(mon, "collapse-1", "perimeter")
    assert [(round(w["aa"] / ACRE_M2), round(w["ab"] / ACRE_M2))
            for w in per] == [(6178, 6427)]
    assert not any(o.kind == "fire_belief" for r in guarded
                   for o in r.operator_events)
    st = guarded[-1].fire_status
    assert not any(s.rapid for s in st.values())
    assert _windows(mon, "future-1") == [] and not st["future-1"].known
    assert guarded[-1].offmap["future-1"].kind == "unknown"   # the ceiling
    fig = _windows(mon, "figure-1", "perimeter")
    assert len(fig) == 1 and 980.0 < fig[0]["aa"] / ACRE_M2 < 1000.0
    # one OperatorEvent per fire at its FIRST finding, never a degradation
    ev = [(k, o.incident_id) for k, r in enumerate(guarded)
          for o in r.operator_events if o.kind == "feed_sanity"]
    assert ev == [(0, "ancient-1"), (0, "future-1"), (1, "collapse-1")]
    assert [r.degraded for r in guarded] == [r.degraded for r in legacy]
    notes = [n for r in guarded for n in r.notes]
    assert any("seeded the fire's last plausible polygon" in n for n in notes)
    assert any("reported size held" in n for n in notes)


# ---------------------------------------------------------------------------
# 2. the pure checks
# ---------------------------------------------------------------------------

def test_time_problem():
    cfg = ResponseConfig()
    now = T0
    assert feedsanity.time_problem(now + 0.9 * H, None, now, cfg) is None
    assert "in the future" in feedsanity.time_problem(now + 1.1 * H, None,
                                                      now, cfg)
    disc = now - 10 * DAY
    assert feedsanity.time_problem(disc - 23 * H, disc, now, cfg) is None
    assert "before the fire was discovered" in feedsanity.time_problem(
        disc - 25 * H, disc, now, cfg)
    assert "more than a year old" in feedsanity.time_problem(
        now - 366 * DAY, None, now, cfg)
    assert feedsanity.time_problem(now - 300 * DAY, None, now, cfg) is None
    assert feedsanity.time_problem(None, disc, now, cfg) is None
    # a discovery time in the future is ignored (the year rule applies)
    assert feedsanity.time_problem(now - 30 * DAY, now + DAY, now, cfg) is None


def test_feed_area_stands_collapse_drop_restamp():
    cfg = ResponseConfig()
    assert feedsanity.feed_area_stands(1000.0, 1999.0, cfg)
    assert not feedsanity.feed_area_stands(1.0, 2850.0, cfg)   # Bunker
    assert not feedsanity.feed_area_stands(12820.0, 1.57, cfg)  # FIRIS LOST
    assert not feedsanity.feed_area_stands(None, 10.0, cfg)
    assert not feedsanity.feed_area_stands(0.0, 10.0, cfg)
    # the collapse rule: the record's own size, from min acres
    assert "under 10%" in feedsanity.collapse_problem(2.4, 153843.0, cfg)
    assert feedsanity.collapse_problem(2.4, 99.0, cfg) is None  # small fire
    assert feedsanity.collapse_problem(16249.0, 16412.0, cfg) is None
    assert feedsanity.collapse_problem(5.0, None, cfg) is None
    # drops beyond noise (the history: resets yes, re-measurements no)
    a = ACRE_M2
    assert feedsanity.is_drop(700 * a, 15798 * a, cfg)          # Hagen reset
    assert feedsanity.is_drop(7296.4 * a, 12918 * a, cfg)       # Border 2
    assert not feedsanity.is_drop(17902 * a, 19134 * a, cfg)    # -6.4 %
    assert not feedsanity.is_drop(0.5 * a, 20.0 * a, cfg)       # abs noise
    assert not feedsanity.is_drop(20.0 * a, 10.0 * a, cfg)      # growth
    # re-stamps: within 0.1 %
    assert feedsanity.is_restamp(7181.46 * a, 7181.04 * a, cfg)
    assert not feedsanity.is_restamp(7195.8 * a, 7181.0 * a, cfg)
    assert not feedsanity.is_restamp(1.0, 0.0, cfg)


def test_perimeter_memory_expires_and_prunes():
    cfg = ResponseConfig(feed_sanity_memory_h=2.0)
    mem = feedsanity.PerimeterMemory()
    rec = feedsanity.RememberedPerimeter(rings=[[CENTER]], perimeter_at_s=T0,
                                         perimeter_acres=None, geom_acres=5.0,
                                         seen_s=T0)
    mem.remember("f", rec)
    assert mem.get("f", T0 + 2 * H, cfg) is rec
    assert mem.get("f", T0 + 2 * H + 1, cfg) is None
    mem.prune(T0 + H, cfg)
    assert len(mem) == 1
    mem.prune(T0 + 3 * H, cfg)
    assert len(mem) == 0


# ---------------------------------------------------------------------------
# 3. the belief's observers, one hazard at a time
# ---------------------------------------------------------------------------

def on(**kw) -> ResponseConfig:
    base = dict(fire_belief_track=True, feed_sanity=True)
    base.update(kw)
    return ResponseConfig(**base)


def off(**kw) -> ResponseConfig:
    base = dict(fire_belief_track=True)
    base.update(kw)
    return ResponseConfig(**base)


def perim(half_m, pat, t, figure=None, reported=None, disc=T0 - 5 * DAY,
          iid="f1") -> FireIncident:
    meta = {"perimeter_at_s": pat}
    if figure is not None:
        meta["perimeter_acres"] = figure
    if reported is not None:
        meta["reported_acres"] = reported
        meta["reported_at_s"] = t - 0.5 * H
    return FireIncident(incident_id=iid, name="F", source=IncidentSource.MOCK,
                        origin=CENTER, updated_at_s=t - 600.0,
                        discovered_at_s=disc,
                        perimeter=[fbs.square_ring(CENTER, half_m)],
                        meta=meta)


def point(acres, t, iid="p1") -> FireIncident:
    return FireIncident(incident_id=iid, name="P", source=IncidentSource.MOCK,
                        origin=CENTER, updated_at_s=t - 600.0,
                        discovered_at_s=T0 - 5 * DAY, acreage=acres,
                        meta={"reported_acres": acres,
                              "reported_at_s": t - 0.5 * H})


def run(store, cfg, seq):
    """seq = [(t, [incidents])]; returns every event."""
    evs = []
    for t, incs in seq:
        evs += store.observe(incs, t, cfg)
    return evs


def test_belief_future_stamp_is_never_reobserved():
    seq = [(T0 + k * 4 * H,
            [perim(800.0, T0 + 400 * DAY, T0 + k * 4 * H)]) for k in range(5)]
    legacy, guarded = FireBeliefStore(), FireBeliefStore()
    run(legacy, off(), seq)
    evs = run(guarded, on(), seq)
    lw = legacy.belief("f1").windows
    assert len(lw) == 4 and all(w["ab"] == w["aa"] for w in lw)   # the flaw
    assert guarded.belief("f1").obs["perimeter"] == []
    assert guarded.belief("f1").windows == []
    msgs = [e["message"] for e in evs]
    assert len(msgs) == 1 and "in the future" in msgs[0]           # said once
    assert all(e["kind"] == "sanity" for e in evs)


def test_belief_small_skew_keeps_the_legacy_clamp():
    """A stamp inside feed_sanity_future_h is the SEMANTICS 9 clamp: one
    observation at now, never repeated."""
    store = FireBeliefStore()
    run(store, on(), [(T0 + k * 600.0, [perim(800.0, T0 + 1800.0,
                                              T0 + k * 600.0)])
                      for k in range(6)])
    obs = store.belief("f1").obs["perimeter"]
    assert [o["t"] for o in obs] == [T0]


def test_belief_ancient_stamp_is_not_observed():
    store = FireBeliefStore()
    evs = run(store, on(), [(T0, [perim(800.0, T0 - 3 * 365 * DAY, T0,
                                        disc=T0 - 2 * DAY)])])
    assert store.belief("f1").obs["perimeter"] == []
    assert "before the fire was discovered" in evs[0]["message"]
    evs = run(store, on(), [(T0, [perim(800.0, T0 - 400 * DAY, T0,
                                        disc=None, iid="f2")])])
    assert store.belief("f2").obs["perimeter"] == []
    assert "more than a year old" in evs[0]["message"]
    legacy = FireBeliefStore()
    run(legacy, off(), [(T0, [perim(800.0, T0 - 3 * 365 * DAY, T0,
                                    disc=T0 - 2 * DAY)])])
    assert len(legacy.belief("f1").obs["perimeter"]) == 1


def _collapse_seq():
    out = []
    for k, (half, fig, rep) in enumerate(((2500.0, 6178.0, 6200.0),
                                          (60.0, 3.56, 6250.0),
                                          (2550.0, 6427.0, 6400.0))):
        t = T0 + k * 4 * H
        out.append((t, [perim(half, t - H, t, figure=fig, reported=rep)]))
    return out


def test_belief_collapse_is_not_observed_and_recovery_spans_it():
    legacy, guarded = FireBeliefStore(), FireBeliefStore()
    run(legacy, off(), _collapse_seq())
    evs = run(guarded, on(), _collapse_seq())
    lw = [(round(w["aa"] / ACRE_M2), round(w["ab"] / ACRE_M2))
          for w in legacy.belief("f1").windows if w["kind"] == "perimeter"]
    assert lw == [(6178, 4), (4, 6427)]
    gw = [w for w in guarded.belief("f1").windows if w["kind"] == "perimeter"]
    assert [(round(w["aa"] / ACRE_M2), round(w["ab"] / ACRE_M2))
            for w in gw] == [(6178, 6427)]
    assert gw[0]["tb"] - gw[0]["ta"] == pytest.approx(8 * H)
    assert any("under 10%" in e["message"] for e in evs)


def test_belief_restamp_counts_once_per_static_h():
    seq = [(T0 + k * 4 * H, [perim(800.0, T0 + k * 4 * H - H, T0 + k * 4 * H,
                                   figure=633.0)]) for k in range(8)]
    legacy, guarded = FireBeliefStore(), FireBeliefStore()
    run(legacy, off(), seq)
    run(guarded, on(), seq)
    assert len(legacy.belief("f1").windows) == 7          # 7 zero windows
    obs = guarded.belief("f1").obs["perimeter"]
    assert len(obs) == 2 and obs[1]["static"]
    assert obs[1]["t"] - obs[0]["t"] == pytest.approx(24 * H)
    (w,) = guarded.belief("f1").windows
    assert w["static"] and w["ab"] == w["aa"]


def test_belief_perimeter_drop_glitch_is_never_observed():
    seq = []
    for k, half in enumerate((2000.0, 1400.0, 2020.0)):   # -51 %, then back
        t = T0 + k * 4 * H
        seq.append((t, [perim(half, t - H, t)]))
    guarded = FireBeliefStore()
    evs = run(guarded, on(), seq)
    ws = guarded.belief("f1").windows
    assert len(ws) == 1 and ws[0]["ab"] > ws[0]["aa"]
    assert ws[0]["tb"] - ws[0]["ta"] == pytest.approx(8 * H)
    assert any("perimeter held" in e["message"] for e in evs)
    assert guarded.belief("f1").hold_since == {}


def test_belief_perimeter_drop_that_persists_restarts_the_series():
    seq = [(T0, [perim(2000.0, T0 - H, T0)])]
    for k in range(1, 8):                               # 28 h at the smaller
        t = T0 + k * 4 * H
        seq.append((t, [perim(1400.0, t - H, t)]))
    guarded = FireBeliefStore()
    evs = run(guarded, on(), seq)
    bel = guarded.belief("f1")
    assert all(w["ab"] >= w["aa"] for w in bel.windows)   # never a shrink
    assert len(bel.obs["perimeter"]) >= 1
    assert all(round(o["a"] / ACRE_M2) < 2500 for o in bel.obs["perimeter"])
    assert any("series restarts" in e["message"] for e in evs)
    assert bel.hold_since == {}


def test_belief_reported_reset_is_held_and_never_negative():
    seq = [(T0 + k * 4 * H, [point(a, T0 + k * 4 * H)])
           for k, a in enumerate((5000.0, 5000.0, 500.0, 5100.0, 5200.0))]
    legacy, guarded = FireBeliefStore(), FireBeliefStore()
    run(legacy, off(), seq)
    evs = run(guarded, on(), seq[:3])
    assert guarded.belief("p1").latest_acres == 5000.0   # the held size
    st = guarded.statuses([point(500.0, T0 + 8 * H)], T0 + 8 * H, on())["p1"]
    assert any("drop has been held" in r for r in st.reasons)
    evs += run(guarded, on(), seq[3:])
    lw = [(w["aa"] / ACRE_M2, w["ab"] / ACRE_M2)
          for w in legacy.belief("p1").windows]
    assert lw[0] == pytest.approx((5000.0, 500.0))
    gw = [(w["aa"] / ACRE_M2, w["ab"] / ACRE_M2)
          for w in guarded.belief("p1").windows]
    assert gw == [pytest.approx((5000.0, 5100.0)),
                  pytest.approx((5100.0, 5200.0))]
    assert sum("reported size held" in e["message"] for e in evs) == 1
    stl = legacy.statuses([point(5200.0, T0 + 16 * H)], T0 + 16 * H, off())
    stg = guarded.statuses([point(5200.0, T0 + 16 * H)], T0 + 16 * H, on())
    assert stl["p1"].rapid and not stg["p1"].rapid


def test_belief_second_reset_to_the_same_size_is_held_and_said_again():
    """Hagen reset to 700 ac twice: each hold is its own note."""
    seq = [(T0 + k * 4 * H, [point(a, T0 + k * 4 * H)])
           for k, a in enumerate((5685.0, 700.0, 5900.0, 700.0, 6000.0))]
    guarded = FireBeliefStore()
    evs = run(guarded, on(), seq)
    assert sum("reported size held" in e["message"] for e in evs) == 2
    assert all(w["ab"] >= w["aa"] for w in guarded.belief("p1").windows)


def test_belief_reported_correction_restarts_after_static_h():
    seq = [(T0, [point(5000.0, T0)])]
    seq += [(T0 + k * 4 * H, [point(3000.0, T0 + k * 4 * H)])
            for k in range(1, 9)]
    guarded = FireBeliefStore()
    evs = run(guarded, on(), seq)
    bel = guarded.belief("p1")
    assert bel.windows == [] or all(w["ab"] >= w["aa"] for w in bel.windows)
    assert bel.rep_value == 3000.0 and bel.latest_acres == 3000.0
    assert any("reported series restarts at 3,000.0 ac" in e["message"]
               for e in evs)


def test_belief_uses_the_geometry_when_the_area_figure_is_off():
    t = T0
    guarded, legacy = FireBeliefStore(), FireBeliefStore()
    evs = run(guarded, on(), [(t, [perim(1000.0, t - H, t, figure=1.0)])])
    run(legacy, off(), [(t, [perim(1000.0, t - H, t, figure=1.0)])])
    assert "feed_acres" not in guarded.belief("f1").obs["perimeter"][0]
    assert legacy.belief("f1").obs["perimeter"][0]["feed_acres"] == 1.0
    assert "off its geometry" in evs[0]["message"]
    ok = FireBeliefStore()
    run(ok, on(), [(t, [perim(1000.0, t - H, t, figure=1100.0)])])
    assert ok.belief("f1").obs["perimeter"][0]["feed_acres"] == 1100.0


def test_belief_knob_off_is_the_legacy_observer(monkeypatch):
    """With the knob off the sanity observers are never called."""
    called = []
    monkeypatch.setattr(FireBeliefStore, "_observe_perimeter_sane",
                        lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(FireBeliefStore, "_observe_reported_sane",
                        lambda *a, **k: called.append(1) or [])
    run(FireBeliefStore(), off(), _collapse_seq())
    assert called == []


# ---------------------------------------------------------------------------
# 4. the monitor's seed side
# ---------------------------------------------------------------------------

def _mon(cfg, feed):
    return ThreatMonitor(feeds=[feed], weather_provider=ss.StubWeather(),
                         terrain_provider=ss.StubTerrain(),
                         registry=ss.StubRegistry(fss.clients()),
                         alert_engine=AlertEngine(cfg), cfg=cfg,
                         safe_zones=[])


def _burned_acres(res, iid, at_s):
    ens = res.ensembles[iid]
    nom = ens.members[0]
    cell = float(ens.spec.cell_m) ** 2
    n = sum(1 for v in nom.data if v != UNREACHED and v <= at_s)
    return n * cell / ACRE_M2


def _cycles(cfg, fires, times):
    feed = ss.ScriptedFeed([])
    mon = _mon(cfg, feed)
    out = []
    for incs, t in zip(fires, times):
        feed.incidents = incs
        out.append(mon.cycle(t))
    return mon, out


def test_seed_uses_the_last_plausible_polygon_through_a_collapse():
    fires = [[fss.collapse_fire(0)], [fss.collapse_fire(1)],
             [fss.collapse_fire(1)]]
    times = [fss.t_cycle(0), fss.t_cycle(1), fss.t_cycle(1) + 600.0]
    _m, legacy = _cycles(ss.small_cfg(), fires, times)
    mon, guarded = _cycles(ss.small_cfg(feed_sanity=True), fires, times)
    t1 = times[1]
    assert _burned_acres(legacy[1], "collapse-1", t1) < 1000.0
    assert _burned_acres(guarded[1], "collapse-1", t1) >= 0.9 * 6178.0
    assert any("seeded the fire's last plausible polygon" in n
               for n in guarded[1].notes)
    ev = [[o for o in r.operator_events if o.kind == "feed_sanity"]
          for r in guarded]
    assert [len(e) for e in ev] == [0, 1, 0]           # first finding only
    assert any("seeded the fire's last plausible polygon" in n
               for n in guarded[2].notes)          # the note every cycle
    assert len(mon._perimeter_memory) == 1


def test_seed_with_nothing_remembered_is_as_received_and_loud():
    mon, (res,) = _cycles(ss.small_cfg(feed_sanity=True),
                          [[fss.collapse_fire(1)]], [fss.t_cycle(1)])
    assert _burned_acres(res, "collapse-1", fss.t_cycle(1)) < 1000.0
    assert any("no plausible polygon of this fire is remembered" in n
               for n in res.notes)
    (ev,) = [o for o in res.operator_events if o.kind == "feed_sanity"]
    assert ev.incident_id == "collapse-1" and not res.degraded
    assert len(mon._perimeter_memory) == 0          # never remembered


def test_seed_memory_expires():
    cfg = ss.small_cfg(feed_sanity=True, feed_sanity_memory_h=2.0)
    _m, res = _cycles(cfg, [[fss.collapse_fire(0)], [fss.collapse_fire(1)]],
                      [fss.t_cycle(0), fss.t_cycle(1)])
    assert any("no plausible polygon of this fire is remembered" in n
               for n in res[1].notes)


def test_seed_drop_below_memory_without_a_reported_size():
    def fire(half, k):
        t = fss.t_cycle(k)
        return FireIncident(
            incident_id="d1", name="Drop", source=IncidentSource.MOCK,
            origin=CENTER, updated_at_s=t - 600.0,
            discovered_at_s=T0 - 5 * DAY,
            perimeter=[fbs.square_ring(CENTER, half)],
            meta={"perimeter_at_s": t - H})
    _m, res = _cycles(ss.small_cfg(feed_sanity=True),
                      [[fire(2500.0, 0)], [fire(1200.0, 1)]],
                      [fss.t_cycle(0), fss.t_cycle(1)])
    assert any("dropped below the" in n and "last plausible polygon" in n
               for n in res[1].notes)
    assert _burned_acres(res[1], "d1", fss.t_cycle(1)) >= 0.9 * 6178.0


@pytest.mark.parametrize("fire", [fss.ancient_fire, fss.future_fire])
def test_seed_takes_the_record_time_for_an_implausible_stamp(fire):
    t = fss.t_cycle(0)
    _m, (legacy,) = _cycles(ss.small_cfg(), [[fire(0)]], [t])
    _m, (guarded,) = _cycles(ss.small_cfg(feed_sanity=True), [[fire(0)]], [t])
    iid = fire(0).incident_id

    def first_burn(res):
        return min(v for v in res.ensembles[iid].members[0].data
                   if v != UNREACHED)

    assert first_burn(guarded) == pytest.approx(t - 600.0)   # the record
    if fire is fss.ancient_fire:
        assert first_burn(legacy) < T0 - 2 * 365 * DAY
    else:
        assert first_burn(legacy) == pytest.approx(t)        # the clamp
    assert any("perimeter time implausible" in n for n in guarded.notes)


def test_seed_side_untouched_with_the_knob_off():
    mon, res = _cycles(ss.small_cfg(), [[fss.collapse_fire(0)],
                                        [fss.collapse_fire(1)]],
                       [fss.t_cycle(0), fss.t_cycle(1)])
    assert len(mon._perimeter_memory) == 0
    assert not any("SEMANTICS 18" in n for r in res for n in r.notes)
    assert not any(o.kind == "feed_sanity" for r in res
                   for o in r.operator_events)


# ---------------------------------------------------------------------------
# 5. the CLI flag + config bounds
# ---------------------------------------------------------------------------

def test_cli_flag_arms_the_knob():
    p = cli._build_parser()
    a = p.parse_args(["watch", "--aoi", "-121,38,-120,39", "--feed-sanity"])
    cfg = cli._feed_sanity_cfg(ResponseConfig(), a)
    assert cfg == ResponseConfig(feed_sanity=True)
    (note,) = cli._feed_sanity_notes(cfg)
    assert "SAFETY SEMANTICS 18" in note and "INERT" in note
    tracked = cli._feed_sanity_notes(ResponseConfig(feed_sanity=True,
                                                    fire_belief_track=True))
    assert "INERT" not in tracked[0]
    n = p.parse_args(["watch", "--aoi", "-121,38,-120,39"])
    assert cli._feed_sanity_cfg(ResponseConfig(), n) == ResponseConfig()
    assert cli._feed_sanity_notes(ResponseConfig()) == []


@pytest.mark.parametrize("kw", [
    dict(feed_sanity_future_h=-1.0),
    dict(feed_sanity_future_h=float("inf")),
    dict(feed_sanity_area_ratio=1.0),
    dict(feed_sanity_collapse_frac=0.0),
    dict(feed_sanity_collapse_frac=1.0),
    dict(feed_sanity_min_acres=-1.0),
    dict(feed_sanity_restamp_frac=0.1),
    dict(feed_sanity_restamp_frac=-0.001),
    dict(feed_sanity_drop_z=0.0),
    dict(feed_sanity_memory_h=0.0),
])
def test_config_bounds(kw):
    probs = validate_config(ResponseConfig(feed_sanity=True, **kw))
    assert any(next(iter(kw)) in p for p in probs), probs


def test_defaults_validate():
    assert validate_config(ResponseConfig(feed_sanity=True)) == []
