"""tests/test_alert_early.py — NEW FIRES ALERT EARLY (2026-11-18, the fire ->
alert latency review, lane C fix 1; contracts alert_new_fires_early, SAFETY
SEMANTICS 49; watch --alert-new-fires-early).

  SAME      across mixed worlds (one or three new fires; an existing fire
            with assessed, roster-blind, provider-blind and absent-clearing
            pairs; a feed-blind cycle; a restored-unsent ledger) the knob
            on and off give the SAME events in the same order, the same
            operator events, notes and assessments, and the same engine
            state after each of two cycles — the sends are the same
            multiset, only sooner.
  SOONER    a new fire's notification goes out before the next incident's
            terrain call (off: after the last one).
  ONCE      no pair steps twice in a cycle; NOTICE -> WATCH still takes the
            next cycle.
  ORDER     warm terrain before cold, then the nearest home.
  SEAMS     an engine without only_incidents falls back to the end-of-cycle
            evaluate; a failing mid-cycle evaluate is noted and retried at
            the end; the TEST world never evaluates early; the registry is
            asked once per incident; the watch flag; config validation."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alert_path_stack as S  # noqa: E402
from tests.test_threat import (  # noqa: E402
    NOW,
    StubAlertEngine,
    StubFeed,
    StubRegistry,
    StubWeather,
)
from wgresponse import cli, geo, worlds  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState,
    ResponseConfig,
    SafeZone,
    validate_config,
)
from wgresponse.threat import ThreatMonitor  # noqa: E402

WORLDS = {
    "one_new": dict(new=("N1",)),
    "three_new": dict(new=("N1", "N2", "N3")),
    "feed_blind": dict(new=("N1", "N2"), feed_blind=True),
    "restored_unsent": dict(new=("N1", "N3"), unsent=(("c-e1", "E"),)),
    "everything": dict(new=("N1", "N2", "N3"), feed_blind=True,
                       unsent=(("c-e1", "E"),)),
}


#: every other knob of the latency build (SAFETY SEMANTICS 50-52 and the
#: step timings), armed together
ALL_OTHER = dict(fuse_hotspots=True, fuse_hotspots_keep_point=True,
                 alert_recovery_summary=True, alert_prune_idle_pairs=True,
                 operator_log_priority=True, cycle_step_timings=True,
                 stale_gate_missing_time=True, fuse_name_join_strict=True,
                 notify_urgent_first=True)


def _run(on, cycles=2, extra=None, **world):
    cfg = S.ladder_cfg(alert_new_fires_early=on, **(extra or {}))
    mon, eng, log = S.build(cfg, **world)
    snaps, results = [], []
    for k in range(cycles):
        res = mon.cycle(NOW + 300.0 * k)
        results.append(res)
        snaps.append(S.snapshot(res, eng, log))
    return snaps, results, log


@pytest.mark.parametrize("name", sorted(WORLDS))
def test_same_events_and_engine_state_on_and_off(name):
    off, _, log_off = _run(False, **WORLDS[name])
    on, res_on, log_on = _run(True, **WORLDS[name])
    for k, (a, b) in enumerate(zip(off, on)):
        for key in ("events", "ops", "notes", "assessments", "degraded",
                    "states", "ledger", "growth", "deesc", "pending"):
            assert a[key] == b[key], (name, k, key)
        assert sorted(a["sends"]) == sorted(b["sends"]), (name, k)
    # the worlds bite: events, blind pairs and (feed-blind) degradations
    first = off[0]
    assert {e.incident_id for e in first["events"]} >= set(
        WORLDS[name]["new"])
    assert any(o[0] == "degraded" for o in first["ops"])
    assert any(o[0] == "recovered" for o in first["ops"])
    # ... and the early path really ran
    assert [r["incident_id"] for r in
            res_on[0].timings["early_alerts"]["evaluated"]] != []


def test_same_with_every_other_latency_knob_armed():
    world = WORLDS["everything"]
    off, _, _ = _run(False, cycles=3, extra=ALL_OTHER, **world)
    on, res_on, _ = _run(True, cycles=3, extra=ALL_OTHER, **world)
    for k, (a, b) in enumerate(zip(off, on)):
        for key in ("events", "ops", "notes", "assessments", "degraded",
                    "states", "ledger", "growth", "deesc", "pending"):
            assert a[key] == b[key], (k, key)
        assert sorted(a["sends"]) == sorted(b["sends"]), k
    assert res_on[0].timings["steps"]["early_plan_s"] >= 0.0
    assert any(o[0] == "degraded" for o in off[0]["ops"])


def test_restored_unsent_refires_at_the_end_with_its_own_fresh_etas():
    off, _, _ = _run(False, cycles=1, new=("N1",), unsent=(("c-e1", "E"),))
    on, _, log = _run(True, cycles=1, new=("N1",), unsent=(("c-e1", "E"),))
    refire = [s for s in on[0]["sends"] if s[1:3] == ("c-e1", "E")]
    assert len(refire) == 1 and "n/a" not in refire[0][4]
    assert refire == [s for s in off[0]["sends"] if s[1:3] == ("c-e1", "E")]
    # sent after E's own terrain call (E is assessed in the legacy order)
    i_send = log.index(refire[0])
    i_terrain_e = next(i for i, r in enumerate(log)
                       if r[0] == "terrain" and abs(r[1] - 39.0) < 0.05)
    assert i_send > i_terrain_e


def test_a_suppressed_all_clear_stays_suppressed():
    """c-n1's WATCH pair on the vanished G clears this cycle while N1
    escalates c-n1: the all-clear is never sent, knob on or off."""
    for on in (False, True):
        snaps, _, _ = _run(on, cycles=1, new=("N1",))
        cleared = [e for e in snaps[0]["events"]
                   if (e.client_id, e.incident_id) == ("c-n1", "G")]
        assert [e.state for e in cleared] == [AlertState.CLEARED]
        assert not [s for s in snaps[0]["sends"]
                    if s[1:3] == ("c-n1", "G")]


def test_notify_goes_out_before_the_next_incidents_terrain_call():
    _, res, log = _run(True, cycles=1, new=("N1", "N2", "N3"))
    early = [r["incident_id"]
             for r in res[0].timings["early_alerts"]["evaluated"]]
    assert sorted(early) == ["N1", "N2", "N3"]
    terrain_idx = [i for i, r in enumerate(log) if r[0] == "terrain"]
    for iid in early:
        i_notify = next(i for i, r in enumerate(log)
                        if r[0] == "notify" and r[2] == iid)
        # its own terrain call came before it, the next one after it
        assert any(t < i_notify for t in terrain_idx)
        assert any(t > i_notify for t in terrain_idx)
    # every new fire's alert is out before the existing fires are assessed
    last_new = max(i for i, r in enumerate(log)
                   if r[0] == "notify" and r[2] in early)
    assert last_new < terrain_idx[3]
    # knob off: every send after the last terrain call
    _, _, log_off = _run(False, cycles=1, new=("N1", "N2", "N3"))
    last_terrain = max(i for i, r in enumerate(log_off) if r[0] == "terrain")
    assert all(i > last_terrain for i, r in enumerate(log_off)
               if r[0] == "notify")


class CountingEngine(AlertEngine):
    """Counts _step per pair per evaluate clock."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.steps = {}

    def _step(self, key, rec, a, now_s, **kw):
        k = (key, now_s)
        self.steps[k] = self.steps.get(k, 0) + 1
        return super()._step(key, rec, a, now_s, **kw)


def test_no_pair_steps_twice_and_notice_to_watch_takes_the_next_cycle():
    cfg = S.ladder_cfg(alert_new_fires_early=True)
    mon, eng, _ = S.build(cfg, new=("N1", "N2", "N3"),
                          engine_cls=CountingEngine)
    r1 = mon.cycle(NOW)
    r2 = mon.cycle(NOW + 300.0)
    assert eng.steps and max(eng.steps.values()) == 1
    n2 = [(e.prev, e.state) for e in r1.events if e.client_id == "c-n2"]
    assert n2 == [(AlertState.NONE, AlertState.NOTICE_72H)]
    n2 = [(e.prev, e.state) for e in r2.events if e.client_id == "c-n2"]
    assert n2 == [(AlertState.NOTICE_72H, AlertState.WATCH)]
    # the second cycle has no new fire: nothing is evaluated early
    assert r2.timings["early_alerts"]["evaluated"] == []


class CachedTerrain(S.LogTerrain):
    """A provider with a local tile cache: N2's domain is cached."""

    def __init__(self, log, root):
        super().__init__(log)
        self.root = root

    def cache_path(self, bbox, cell_m):
        name = "warm" if bbox.contains(S.N2) else "cold"
        return os.path.join(self.root, f"{name}-{bbox.west:.3f}.wgtile")


def test_warm_terrain_first_then_the_nearest_home(tmp_path):
    cfg = S.ladder_cfg(alert_new_fires_early=True)
    mon, _, log = S.build(cfg, new=("N1", "N2", "N3"))
    mon._terrain = CachedTerrain(log, str(tmp_path))
    plan = mon._early_plan(
        [i for i in mon._feeds[0].fetch_active()], NOW)
    # nothing cached: nearest first (N1 / N3 at 1.5 km, N2 at 6 km)
    assert plan.head[-1] == "N2"
    for name in os.listdir(tmp_path):
        os.remove(tmp_path / name)
    incs = mon._feeds[0].fetch_active()
    n2 = next(i for i in incs if i.incident_id == "N2")
    geom = mon._seed_geometry(n2, NOW)[5]
    near = [c for c in S.clients() if c.client_id == "c-n2"]
    dom = mon._forecast_domain(geom, near)
    open(mon._terrain.cache_path(dom, mon._pick_cell_m(dom)), "w").close()
    plan = mon._early_plan(incs, NOW)
    assert plan.head[0] == "N2"            # warm before cold
    assert set(plan.head) == {"N1", "N2", "N3"}
    assert plan.order[:3] == [incs.index(next(
        i for i in incs if i.incident_id == iid)) for iid in plan.head]


def test_registry_asked_once_per_incident():
    calls = {}
    for on in (False, True):
        cfg = S.ladder_cfg(alert_new_fires_early=on)
        mon, _, _ = S.build(cfg, new=("N1", "N2", "N3"))
        mon.cycle(NOW)
        calls[on] = len(mon._registry.calls)
    assert calls[True] == calls[False]


def test_an_engine_without_the_seam_takes_every_pair_at_the_end():
    eng = StubAlertEngine()
    cfg = S.ladder_cfg(alert_new_fires_early=True)
    mon = ThreatMonitor(
        feeds=[StubFeed([S.fire("N1", S.N1)])],
        weather_provider=StubWeather(), terrain_provider=S.LogTerrain([]),
        registry=StubRegistry(S.clients()), alert_engine=eng, cfg=cfg,
        safe_zones=[SafeZone("z1", geo.destination(S.ORIGIN, 180.0,
                                                   20_000.0))])
    res = mon.cycle(NOW)
    assert len(eng.seen) == 1 and eng.seen[0][0] == res.assessments
    assert res.timings["early_alerts"]["evaluated"] == []


class FailingOnce(AlertEngine):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.failed = False

    def evaluate(self, *a, **kw):
        if kw.get("flush_unsent") is False and not self.failed:
            self.failed = True
            raise RuntimeError("ladder hiccup")
        return super().evaluate(*a, **kw)


def test_a_failed_mid_cycle_evaluate_is_noted_and_retried_at_the_end():
    off, _, _ = _run(False, cycles=1, new=("N1",))
    cfg = S.ladder_cfg(alert_new_fires_early=True)
    mon, eng, log = S.build(cfg, new=("N1",), engine_cls=FailingOnce)
    res = mon.cycle(NOW)
    assert eng.failed
    assert any("mid-cycle alert evaluation failed" in n for n in res.notes)
    assert any(o.kind == "provider_error"
               and o.detail.get("step") == "alerts_early"
               for o in res.operator_events)
    assert res.events == off[0]["events"]
    assert dict(eng.states()) == off[0]["states"]


def test_the_test_world_never_evaluates_early():
    assert ThreatMonitor.early_alerts_ok is True
    assert worlds._TestWorldMonitor.early_alerts_ok is False


def test_default_off_and_validated():
    assert ResponseConfig().alert_new_fires_early is False
    assert validate_config(ResponseConfig(alert_new_fires_early=True)) == []
    bad = validate_config(ResponseConfig(alert_new_fires_early="yes"))
    assert any("alert_new_fires_early" in p for p in bad)


def test_the_watch_flag_arms_it_with_a_startup_note():
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "x.json", "--alert-new-fires-early"]))
    cfg, notes = cli._latency_cfg(ResponseConfig(), args)
    assert cfg.alert_new_fires_early is True
    assert any("SAFETY SEMANTICS 49" in n for n in notes)
    cfg, notes = cli._latency_cfg(ResponseConfig(), SimpleNamespace())
    assert cfg == ResponseConfig() and notes == []
