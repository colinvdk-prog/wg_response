"""tests/test_operator_flood.py — THE OPERATOR-EVENT FLOOD (2026-11-18, the
fire -> alert latency review's B4 / C9, verified live: one blind cycle gave
15,894 'recovered' events, 15,259 of them pairs at NONE; contracts
alert_recovery_summary / alert_prune_idle_pairs / operator_log_priority,
SAFETY SEMANTICS 51; watch --summarize-recoveries / --prune-idle-pairs /
--operator-log-priority).

  (a) SUMMARY   the NONE pairs of one incident that go blind or come back
                give ONE 'degraded' / 'recovered' event with the count;
                threatened, cleared and impacted pairs keep their own; the
                records, the AlertEvents and the cycle's degraded flag are
                unchanged.
  (b) PRUNE     a record at NONE carrying nothing is dropped when its
                incident is off the map and every feed is healthy — never
                on a blind cycle, never one with a blind run, history, a
                ledgered send or an active incident; a pair that comes back
                steps exactly as a fresh pair (and as the kept one); the
                state file round-trips without it; a TEST-world action
                re-run prunes nothing.
  (c) PRIORITY  a bulk cycle cannot evict satellite heat, feed-blind or
                provider-error events from /api/state operator_log_priority;
                /healthz counts the satellite heat of the last 24 h; both
                keys absent with the knob off.
  SURFACE       the watch flags; config validation."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from tests.test_alerts import assess
from wgresponse import cli, server, worlds
from wgresponse.alerts import AlertEngine
from wgresponse.cli import _WatchDaemon, operator_priority
from wgresponse.contracts import (
    AlertRecord,
    AlertState,
    BBox,
    CycleResult,
    OperatorEvent,
    ResponseConfig,
    validate_config,
)
from wgresponse.persist import StateStore

NOW = 1_760_000_000.0


def _records():
    """50 NONE pairs of X, 3 of Y, a WATCH pair of X, a CLEARED and an
    IMPACTED pair of Y."""
    recs = {}
    for k in range(50):
        recs[(f"c{k:02d}", "X")] = AlertRecord(f"c{k:02d}", "X")
    for k in range(3):
        recs[(f"d{k}", "Y")] = AlertRecord(f"d{k}", "Y")
    t0 = NOW - 7200.0
    recs[("w", "X")] = AlertRecord("w", "X", AlertState.WATCH, t0,
                                   history=[(t0, AlertState.WATCH)])
    recs[("cl", "Y")] = AlertRecord("cl", "Y", AlertState.CLEARED, t0,
                                    history=[(t0, AlertState.CLEARED)])
    recs[("im", "Y")] = AlertRecord("im", "Y", AlertState.IMPACTED, t0,
                                    history=[(t0, AlertState.IMPACTED)])
    return recs


def _engine(**kw):
    eng = AlertEngine(ResponseConfig(**kw))
    recs = _records()
    eng.restore(copy.deepcopy(recs), notified_ledger={
        ("w", "X", "watch"): NOW - 7200.0})
    return eng


def _blind_then_back(summary):
    eng = _engine(alert_recovery_summary=summary)
    out = []
    # a feed-blind cycle, then a healthy one where X is assessed (w far)
    ev1 = eng.evaluate([], NOW, feeds_healthy=False)
    op1 = eng.pop_operator_events()
    ev2 = eng.evaluate([assess("w", "X", NOW + 300.0, 60.0)], NOW + 300.0)
    op2 = eng.pop_operator_events()
    out.append((ev1, op1, ev2, op2))
    return eng, ev1, op1, ev2, op2


def test_a_summary_one_event_per_incident_for_the_pairs_at_none():
    eng_off, ev1o, op1o, ev2o, op2o = _blind_then_back(False)
    eng_on, ev1n, op1n, ev2n, op2n = _blind_then_back(True)
    assert len(op1o) == 56 and {o.kind for o in op1o} == {"degraded"}
    assert len(op2o) == 56 and {o.kind for o in op2o} == {"recovered"}
    for ops, kind in ((op1n, "degraded"), (op2n, "recovered")):
        assert {o.kind for o in ops} == {kind}
        summ = {o.incident_id: o.detail for o in ops
                if o.detail.get("summary")}
        assert summ["X"]["pairs"] == 50 and summ["Y"]["pairs"] == 3
        assert summ["X"]["state"] == "none"
        assert summ["X"]["clients"] == ["c00", "c01", "c02", "c03", "c04"]
        single = sorted((o.detail["client_id"], o.detail["state"])
                        for o in ops if not o.detail.get("summary"))
        assert single == [("cl", "cleared"), ("im", "impacted"),
                          ("w", "watch")]
        assert all("51a" in o.message for o in ops if o.detail.get("summary"))
    # the ladder is untouched: the same AlertEvents and records
    assert ev1o == ev1n and ev2o == ev2n
    assert eng_off.states() == eng_on.states()


def test_a_summary_respects_the_alarm_threshold():
    eng = _engine(alert_recovery_summary=True, blind_alarm_cycles=2)
    eng.evaluate([], NOW, feeds_healthy=False)
    assert eng.pop_operator_events() == []          # no alarm yet
    eng.evaluate([], NOW + 300.0, feeds_healthy=False)
    ops = eng.pop_operator_events()
    summ = [o for o in ops if o.detail.get("summary")]
    assert {o.incident_id: o.detail["pairs"] for o in summ} == {"X": 50,
                                                                "Y": 3}
    assert all(o.detail["blind_cycles_max"] == 2 for o in summ)


# ---------------------------------------------------------------------------
# (b) prune
# ---------------------------------------------------------------------------

def test_b_prune_drops_only_records_that_carry_nothing():
    eng = _engine(alert_prune_idle_pairs=True)
    eng.evaluate([], NOW)                       # healthy, X and Y gone
    dropped = eng.prune_idle_pairs({"Z"}, True)
    assert len(dropped) == 53
    assert set(eng.states()) == {("w", "X"), ("cl", "Y"), ("im", "Y")}
    assert all(k not in eng._deesc_streak for k in dropped)


@pytest.mark.parametrize("case", ["blind_cycle", "incident_active",
                                  "blind_run", "ledgered"])
def test_b_prune_keeps(case):
    eng = AlertEngine(ResponseConfig(alert_prune_idle_pairs=True))
    rec = AlertRecord("c", "X")
    healthy, active, ledger = True, set(), {}
    if case == "blind_cycle":
        healthy = False
    elif case == "incident_active":
        active = {"X"}
    elif case == "blind_run":
        rec.blind_cycles = 1
    elif case == "ledgered":
        ledger = {("c", "X", "notice_72h"): NOW}
    eng.restore({("c", "X"): rec}, notified_ledger=ledger)
    assert eng.prune_idle_pairs(active, healthy) == []
    assert ("c", "X") in eng.states()


def _seq(eng, prune):
    """X far (NONE), X gone a cycle, X back far, then near (EXTREME)."""
    out = []
    t = NOW
    steps = [[assess("c", "X", t, 90.0)], [],
             [assess("c", "X", t + 600.0, 90.0)],
             [assess("c", "X", t + 900.0, 5.0)]]
    for k, a in enumerate(steps):
        now = NOW + 300.0 * k
        a = [assess("c", "X", now, x.eta.eta_p50_s / 3600.0 - now / 3600.0)
             for x in a]
        out.append(eng.evaluate(a, now))
        if prune:
            eng.prune_idle_pairs({"X"} if a else set(), True)
    return out


def test_b_a_pair_that_comes_back_steps_as_a_fresh_pair():
    kept = AlertEngine(ResponseConfig())
    pruned = AlertEngine(ResponseConfig(alert_prune_idle_pairs=True))
    ev_kept = _seq(kept, False)
    ev_pruned = _seq(pruned, True)
    assert ev_kept == ev_pruned
    assert [e.state for e in ev_pruned[3]] == [AlertState.EXTREME_24H]
    # the pruned engine's record is a fresh one's: a new engine that only
    # sees the last two cycles
    fresh = AlertEngine(ResponseConfig())
    for k in (2, 3):
        now = NOW + 300.0 * k
        fresh.evaluate([assess("c", "X", now, 90.0 if k == 2 else 5.0)],
                       now)
    assert pruned.states() == fresh.states()
    a, b = kept.states()[("c", "X")], pruned.states()[("c", "X")]
    assert (a.state, a.history, a.blind_cycles) == (b.state, b.history,
                                                    b.blind_cycles)


def test_b_state_file_round_trip_without_the_pruned_pairs(tmp_path):
    eng = _engine(alert_prune_idle_pairs=True)
    eng.evaluate([], NOW)
    eng.prune_idle_pairs(set(), True)
    store = StateStore(tmp_path)
    store.save(eng.states(), eng.notified_ledger(), None, {})
    loaded = store.load()
    back = AlertEngine(ResponseConfig(alert_prune_idle_pairs=True))
    back.restore(loaded["alert_records"],
                 notified_ledger=loaded["notified_ledger"])
    assert set(back.states()) == {("w", "X"), ("cl", "Y"), ("im", "Y")}
    assert back.states() == eng.states()
    assert back._resend_pending == set()


def test_b_monitor_prunes_after_the_evaluate_with_a_note():
    from tests.test_threat import StubFeed, make_cfg, make_monitor
    cfg = make_cfg(alert_prune_idle_pairs=True)
    eng = AlertEngine(cfg)
    eng.restore({("gone-c", "gone"): AlertRecord("gone-c", "gone")},
                notified_ledger={})
    mon = make_monitor(feeds=[StubFeed([])], cfg=cfg, alert_engine=eng)
    res = mon.cycle(NOW)
    assert eng.states() == {}
    assert any("51b" in n and "dropped 1 idle pair" in n for n in res.notes)
    # off: kept, no note
    cfg = make_cfg()
    eng = AlertEngine(cfg)
    eng.restore({("gone-c", "gone"): AlertRecord("gone-c", "gone")},
                notified_ledger={})
    res = make_monitor(feeds=[StubFeed([])], cfg=cfg,
                       alert_engine=eng).cycle(NOW)
    assert ("gone-c", "gone") in eng.states()
    assert not any("51b" in n for n in res.notes)


def test_b_a_test_world_action_rerun_prunes_nothing():
    calls = []

    class Probe(worlds._TestWorldMonitor):
        def __init__(self):
            pass

    p = Probe()
    p._alert_engine = SimpleNamespace(
        prune_idle_pairs=lambda a, h: calls.append((a, h)) or [])
    p._seeded_ladder_only = True
    p._prune_idle_pairs({"X"}, True, [])
    assert calls == []
    p._seeded_ladder_only = False
    p._prune_idle_pairs({"X"}, True, [])
    assert calls == [({"X"}, True)]


# ---------------------------------------------------------------------------
# (c) priority rings
# ---------------------------------------------------------------------------

def _ev(kind, t, state=None, iid="X"):
    detail = {"state": state} if state else {}
    return OperatorEvent(t_s=t, kind=kind, message=f"{kind} {iid}",
                         incident_id=iid, detail=detail)


def test_c_the_priority_rule():
    for kind in ("satellite_heat", "feed_blind", "provider_error",
                 "extreme_regime", "crew_at_risk", "crew_unjudged",
                 "crew_must_leave", "crew_no_way_out", "fire_belief",
                 "feed_sanity", "seed_retired", "a_future_kind"):
        assert operator_priority(_ev(kind, NOW))
    for st in ("notice_72h", "watch", "extreme_24h"):
        assert operator_priority(_ev("degraded", NOW, st))
        assert operator_priority(_ev("recovered", NOW, st))
    for st in ("none", "cleared", "impacted", None):
        assert not operator_priority(_ev("degraded", NOW, st))
        assert not operator_priority(_ev("recovered", NOW, st))


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class _Monitor:
    """cycle(now) hands back the queued operator events."""

    def __init__(self):
        self.queue = []

    def cycle(self, now):
        ops, self.queue = self.queue, []
        return CycleResult(t_s=now, operator_events=ops)


def _daemon(tmp_path, on):
    cfg = ResponseConfig(operator_log_priority=on)
    clock = _Clock(NOW)
    mon = _Monitor()
    d = _WatchDaemon(monitor=mon, engine=AlertEngine(cfg),
                     registry=SimpleNamespace(protected_clients=lambda b: []),
                     tracker=None, store=StateStore(tmp_path), cfg=cfg,
                     aoi=BBox(-121.0, 38.0, -120.0, 39.0), period_s=300.0,
                     clock=clock, sleep=lambda s: None)
    return d, mon, clock


def test_c_a_bulk_cycle_cannot_evict_the_operator_events(tmp_path):
    d, mon, clock = _daemon(tmp_path, True)
    mon.queue = ([_ev("satellite_heat", NOW), _ev("feed_blind", NOW),
                  _ev("provider_error", NOW)]
                 + [_ev("recovered", NOW, "none", f"i{k}")
                    for k in range(500)])
    d.run_cycle()
    st = d.state()
    assert {e.kind for e in st["operator_log"]} == {"recovered"}
    assert [e.kind for e in st["operator_log_priority"]] == [
        "satellite_heat", "feed_blind", "provider_error"]
    assert st["satellite_heat_24h"] == 1
    # a ring is bounded per kind: 60 provider errors keep the last 50 and
    # never push the satellite heat out
    clock.t = NOW + 600.0
    mon.queue = [_ev("provider_error", NOW + 600.0, iid=f"p{k}")
                 for k in range(60)]
    d.run_cycle()
    st = d.state()
    kinds = [e.kind for e in st["operator_log_priority"]]
    assert kinds.count("provider_error") == 50 and "satellite_heat" in kinds
    # the healthz count is the last 24 h only
    clock.t = NOW + 25 * 3600.0
    mon.queue = []
    d.run_cycle()
    assert d.state()["satellite_heat_24h"] == 0
    app = SimpleNamespace(state_provider=d.state)
    h = server._Handler.__new__(server._Handler)
    h.server = SimpleNamespace(app=app)
    assert h._health_payload()["satellite_heat_24h"] == 0


def test_c_off_no_new_keys(tmp_path):
    d, mon, _ = _daemon(tmp_path, False)
    mon.queue = [_ev("satellite_heat", NOW)]
    d.run_cycle()
    st = d.state()
    assert "operator_log_priority" not in st and "satellite_heat_24h" not in st
    h = server._Handler.__new__(server._Handler)
    h.server = SimpleNamespace(app=SimpleNamespace(state_provider=d.state))
    assert "satellite_heat_24h" not in h._health_payload()
    json.dumps(st, default=str)


def test_knobs_default_off_validated_and_armed_by_the_watch_flags():
    for k in ("alert_recovery_summary", "alert_prune_idle_pairs",
              "operator_log_priority"):
        assert getattr(ResponseConfig(), k) is False
        assert validate_config(ResponseConfig(**{k: True})) == []
        assert any(k in p for p in validate_config(
            ResponseConfig(**{k: "on"})))
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "x.json", "--summarize-recoveries",
         "--prune-idle-pairs", "--operator-log-priority"]))
    cfg, notes = cli._latency_cfg(ResponseConfig(), args)
    assert (cfg.alert_recovery_summary and cfg.alert_prune_idle_pairs
            and cfg.operator_log_priority)
    for tag in ("51a", "51b", "51c"):
        assert any(tag in n for n in notes)
