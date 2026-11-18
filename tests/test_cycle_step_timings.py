"""tests/test_cycle_step_timings.py — CYCLE STEP TIMINGS (2026-11-18, the
fire -> alert latency review's lane C: 181 of the live assess phase's 200 s
were unattributed; contracts cycle_step_timings, telemetry only; watch
--cycle-step-timings).

  ON    CycleResult.timings["steps"] times every pre-loop gate, the loop,
        the blind-pair pass, the alert evaluate, dispatch...; each incident
        with a domain gets assess_s on its row, the rest one aggregate; the
        daemon adds persist_s and the previous /api/state build.
  OFF   no key anywhere; the cycle's decisions are the same either way."""
from __future__ import annotations

from types import SimpleNamespace

from tests.test_threat import (
    NOW,
    StubFeed,
    StubRegistry,
    far_client,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, geo
from wgresponse.alerts import AlertEngine
from wgresponse.cli import _WatchDaemon
from wgresponse.contracts import BBox, ResponseConfig, validate_config
from wgresponse.persist import StateStore

GATES = ("gate_seeded_s", "gate_stale_s", "gate_contained_s",
         "gate_attach_s", "gate_labels_s", "gate_heat_split_s",
         "gate_learn_collect_s", "gate_belief_observe_s")
LATER = ("loop_setup_s", "assess_loop_s", "post_loop_s", "blind_pairs_s",
         "meta_regime_s", "alert_crew_context_s", "alert_evaluate_s",
         "alert_after_s", "belief_offmap_s", "crew_rasters_s",
         "dispatch_plan_s", "crew_watch_snapshot_s")


def _incs():
    far = incident("inc-far", origin=geo.destination(
        far_client().loc, 90.0, 200_000.0), irwin="IRWIN-F")
    return [incident(), far]


def _run(on, **kw):
    cfg = make_cfg(cycle_step_timings=on, **kw)
    mon = make_monitor(feeds=[StubFeed(_incs())], cfg=cfg,
                       alert_engine=AlertEngine(cfg),
                       registry=StubRegistry([near_client(), far_client()]))
    return mon.cycle(NOW)


def test_on_every_step_is_timed():
    res = _run(True)
    st = res.timings["steps"]
    for k in GATES + LATER:
        assert isinstance(st[k], float) and st[k] >= 0.0, k
    assert res.timings["incidents"]["inc-1"]["assess_s"] >= 0.0
    agg = st["assess_other"]
    assert agg["incidents"] == 1 and agg["max_id"] == "inc-far"
    # the segments add up to (at most) the cycle after the feeds
    total = sum(v for v in st.values() if isinstance(v, float))
    assert total <= (res.timings["assess_s"] + res.timings["alerts_s"]
                     + res.timings["dispatch_s"] + 1.0)


def test_with_new_fires_early_the_plan_and_the_incidents_are_timed():
    res = _run(True, alert_new_fires_early=True)
    st = res.timings["steps"]
    assert "early_plan_s" in st
    assert "assess_s" in res.timings["incidents"]["inc-1"]
    assert st["assess_other"]["incidents"] == 1


def test_off_no_key_and_the_same_decisions():
    off, on = _run(False), _run(True)
    assert "steps" not in off.timings
    assert "assess_s" not in off.timings["incidents"]["inc-1"]
    assert off.events == on.events and off.notes == on.notes
    assert [(a.client_id, a.eta) for a in off.assessments] == \
        [(a.client_id, a.eta) for a in on.assessments]


class _Monitor:
    def __init__(self, on):
        self.on = on

    def cycle(self, now):
        from wgresponse.contracts import CycleResult
        t = {"steps": {"gate_stale_s": 0.0}} if self.on else {}
        return CycleResult(t_s=now, timings=t)


def _daemon(tmp_path, on):
    cfg = ResponseConfig(cycle_step_timings=on)
    return _WatchDaemon(
        monitor=_Monitor(on), engine=AlertEngine(cfg),
        registry=SimpleNamespace(protected_clients=lambda b: []),
        tracker=None, store=StateStore(tmp_path), cfg=cfg,
        aoi=BBox(-121.0, 38.0, -120.0, 39.0), period_s=300.0,
        clock=lambda: NOW, sleep=lambda s: None)


def test_daemon_persist_and_state_build(tmp_path):
    d = _daemon(tmp_path, True)
    res = d.run_cycle()
    assert res.timings["steps"]["persist_s"] >= 0.0
    first = d.state()["last_cycle_phases"]["steps"]
    assert first["state_build_s"] is None and "persist_s" in first
    second = d.state()["last_cycle_phases"]["steps"]
    assert isinstance(second["state_build_s"], float)
    # the cycle's own timings are not written by the state build
    assert "state_build_s" not in res.timings["steps"]
    off = _daemon(tmp_path / "off", False)
    off.run_cycle()
    assert "steps" not in off.state()["last_cycle_phases"]


def test_default_off_validated_and_armed_by_the_watch_flag():
    assert ResponseConfig().cycle_step_timings is False
    assert validate_config(ResponseConfig(cycle_step_timings=True)) == []
    assert any("cycle_step_timings" in p for p in validate_config(
        ResponseConfig(cycle_step_timings="y")))
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "x.json", "--cycle-step-timings"]))
    cfg, notes = cli._latency_cfg(ResponseConfig(), args)
    assert cfg.cycle_step_timings is True
    assert any("CYCLE STEP TIMINGS" in n for n in notes)
