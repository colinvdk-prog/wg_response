"""Tests for wgresponse.alerts — alert ladder, hysteresis, events, notifier."""
from __future__ import annotations

import dataclasses
import json
import math

import pytest

from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    LatLon,
    ResponseConfig,
    ThreatAssessment,
    ThreatEta,
    assert_valid_transition,
)
from wgresponse.notify import LogNotifier

H = 3600.0  # seconds per hour


def assess(cid, iid, now_s, p50_h, p10_h=None, p90_h=None):
    """Build a ThreatAssessment with ETAs `h` hours after now_s.

    Negative hours mean the arrival time has already passed.
    """
    if p10_h is None:
        p10_h = p50_h * 0.7
    if p90_h is None:
        p90_h = p50_h * 1.3
    eta = ThreatEta(
        eta_p10_s=now_s + p10_h * H,
        eta_p50_s=now_s + p50_h * H,
        eta_p90_s=now_s + p90_h * H,
    )
    return ThreatAssessment(client_id=cid, incident_id=iid, eta=eta, computed_at_s=now_s)


@pytest.fixture
def cfg():
    return ResponseConfig()  # notice 72h, extreme 24h, p10 12h, clear_cycles 2, margin 6h


@pytest.fixture
def clients():
    return {
        "c1": ClientProperty(client_id="c1", loc=LatLon(34.1, -118.2), name="Casa Blanca"),
    }


# ---------------------------------------------------------------- ladder walk


def test_full_ladder_walk_shrinking_eta(cfg, clients):
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier, clients_by_id=clients)
    key = ("c1", "fire-1")

    # 100h out: no alert yet.
    ev = eng.evaluate([assess("c1", "fire-1", 0.0, 100.0)], now_s=0.0)
    assert ev == []
    assert eng.states()[key].state is AlertState.NONE

    # 54h out: one-time NOTICE fires.
    ev = eng.evaluate([assess("c1", "fire-1", 1 * H, 54.0, p10_h=38.0)], now_s=1 * H)
    assert [e.state for e in ev] == [AlertState.NOTICE_72H]
    assert ev[0].prev is AlertState.NONE
    assert ev[0].eta_p50_h == pytest.approx(54.0)
    assert ev[0].eta_p10_h == pytest.approx(38.0)
    assert "~54h (median), 38h (pessimistic)" in ev[0].message
    assert "Casa Blanca" in ev[0].message

    # Next cycle, still inside notice window: auto-progress to WATCH.
    ev = eng.evaluate([assess("c1", "fire-1", 2 * H, 50.0)], now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.WATCH]
    assert ev[0].prev is AlertState.NOTICE_72H

    # 20h out: EXTREME.
    ev = eng.evaluate([assess("c1", "fire-1", 3 * H, 20.0, p10_h=14.0)], now_s=3 * H)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]

    # Median arrival passed: IMPACTED (terminal).
    ev = eng.evaluate([assess("c1", "fire-1", 4 * H, -0.5, p10_h=-1.0)], now_s=4 * H)
    assert [e.state for e in ev] == [AlertState.IMPACTED]
    assert ev[0].eta_p50_h == 0.0  # hours_out clamps passed ETAs to 0

    rec = eng.states()[key]
    assert rec.state is AlertState.IMPACTED
    # Exactly one history entry (and one event) per transition.
    assert [s for _, s in rec.history] == [
        AlertState.NOTICE_72H,
        AlertState.WATCH,
        AlertState.EXTREME_24H,
        AlertState.IMPACTED,
    ]
    assert len(notifier.sent) == 4

    # Terminal: further assessments produce nothing.
    ev = eng.evaluate([assess("c1", "fire-1", 5 * H, 10.0)], now_s=5 * H)
    assert ev == []
    assert eng.states()[key].state is AlertState.IMPACTED
    assert len(notifier.sent) == 4


def test_none_jumps_straight_to_extreme(cfg):
    eng = AlertEngine(cfg)
    ev = eng.evaluate([assess("c1", "f1", 0.0, 10.0)], now_s=0.0)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
    assert ev[0].prev is AlertState.NONE


def test_p10_triggers_extreme_while_p50_beyond_24h(cfg):
    eng = AlertEngine(cfg)
    # p50 = 40h (> extreme_h) but p10 = 8h (<= extreme_p10_h): EXTREME.
    ev = eng.evaluate([assess("c1", "f1", 0.0, 40.0, p10_h=8.0)], now_s=0.0)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_p10_extreme_beats_p50_beyond_notice_and_never_counts_clear(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 60.0, p10_h=50.0)], now_s=0.0)   # NOTICE
    eng.evaluate([assess("c1", "f1", 1 * H, 60.0, p10_h=50.0)], now_s=1 * H)  # WATCH
    # p50 far beyond notice+margin, but a pessimistic member at 10h: escalate.
    ev = eng.evaluate([assess("c1", "f1", 2 * H, 90.0, p10_h=10.0)], now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
    assert eng.states()[("c1", "f1")].clear_streak == 0


# ------------------------------------------------------------------ hysteresis


def test_oscillating_eta_does_not_flap(cfg):
    eng = AlertEngine(cfg)
    seq = [71.0, 74.0, 71.0, 74.0, 71.0, 74.0]
    all_events = []
    for i, p50 in enumerate(seq):
        now = i * H
        all_events += eng.evaluate([assess("c1", "f1", now, p50, p10_h=p50 - 5)], now_s=now)
    # Exactly NOTICE then WATCH; oscillation around 72h causes no further events.
    assert [e.state for e in all_events] == [AlertState.NOTICE_72H, AlertState.WATCH]
    assert eng.states()[("c1", "f1")].state is AlertState.WATCH


def test_cleared_needs_clear_cycles_with_margin(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 71.0, p10_h=60.0)], now_s=0.0)     # NOTICE
    eng.evaluate([assess("c1", "f1", 1 * H, 71.0, p10_h=60.0)], now_s=1 * H)  # WATCH

    # 74h is beyond notice_h but INSIDE the 72+6h margin: not a clear eval.
    ev = eng.evaluate([assess("c1", "f1", 2 * H, 74.0, p10_h=60.0)], now_s=2 * H)
    assert ev == []
    assert eng.states()[("c1", "f1")].clear_streak == 0

    # One clean eval (>= 78h) is not enough (clear_cycles = 2).
    ev = eng.evaluate([assess("c1", "f1", 3 * H, 80.0, p10_h=70.0)], now_s=3 * H)
    assert ev == []
    assert eng.states()[("c1", "f1")].state is AlertState.WATCH
    assert eng.states()[("c1", "f1")].clear_streak == 1

    # A dip back inside the window resets the streak.
    ev = eng.evaluate([assess("c1", "f1", 4 * H, 71.0, p10_h=60.0)], now_s=4 * H)
    assert ev == []
    assert eng.states()[("c1", "f1")].clear_streak == 0

    # Two consecutive clean evals: CLEARED.
    ev = eng.evaluate([assess("c1", "f1", 5 * H, 80.0, p10_h=70.0)], now_s=5 * H)
    assert ev == []
    ev = eng.evaluate([assess("c1", "f1", 6 * H, 80.0, p10_h=70.0)], now_s=6 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]
    assert eng.states()[("c1", "f1")].state is AlertState.CLEARED


def test_cleared_pair_can_realert(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 60.0)], now_s=0.0)          # NOTICE
    eng.evaluate([assess("c1", "f1", 1 * H, 60.0)], now_s=1 * H)      # WATCH
    eng.evaluate([], now_s=2 * H)
    eng.evaluate([], now_s=3 * H)                                     # CLEARED
    assert eng.states()[("c1", "f1")].state is AlertState.CLEARED
    ev = eng.evaluate([assess("c1", "f1", 4 * H, 60.0)], now_s=4 * H)
    assert [e.state for e in ev] == [AlertState.NOTICE_72H]           # one-time notice again


def test_extreme_deescalates_to_watch_with_hysteresis(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 20.0, p10_h=15.0)], now_s=0.0)  # NONE -> EXTREME
    # Forecast slows: 40h median / 35h pessimistic (clear of both extreme
    # triggers with margin, but still inside the 72h notice window).
    ev = eng.evaluate([assess("c1", "f1", 1 * H, 40.0, p10_h=35.0)], now_s=1 * H)
    assert ev == []  # one clean eval is not enough
    ev = eng.evaluate([assess("c1", "f1", 2 * H, 40.0, p10_h=35.0)], now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.WATCH]
    assert eng.states()[("c1", "f1")].state is AlertState.WATCH


# ------------------------------------------------- disappearing incidents


def test_disappeared_incident_clears_after_streak(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)          # NOTICE
    eng.evaluate([assess("c1", "f1", 1 * H, 50.0)], now_s=1 * H)      # WATCH
    # Incident vanishes from assessments (contained / feed dropped it).
    ev = eng.evaluate([], now_s=2 * H)
    assert ev == []                                                   # streak 1 of 2
    ev = eng.evaluate([], now_s=3 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]
    rec = eng.states()[("c1", "f1")]
    assert rec.state is AlertState.CLEARED
    assert math.isinf(ev[0].eta_p50_h)


def test_disappeared_while_extreme_clears_after_streak(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 10.0)], now_s=0.0)          # EXTREME
    eng.evaluate([], now_s=1 * H)
    ev = eng.evaluate([], now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]


# ------------------------------------------------------------ transition guard


def test_illegal_transition_raises():
    with pytest.raises(ValueError):
        assert_valid_transition(AlertState.NONE, AlertState.WATCH)
    with pytest.raises(ValueError):
        assert_valid_transition(AlertState.IMPACTED, AlertState.WATCH)
    with pytest.raises(ValueError):
        assert_valid_transition(AlertState.CLEARED, AlertState.WATCH)


def test_engine_transition_routes_through_guard(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 100.0)], now_s=0.0)  # record exists, NONE
    rec = eng.states()[("c1", "f1")]
    with pytest.raises(ValueError):
        eng._transition(rec, AlertState.WATCH, 1 * H, 10.0, 20.0)  # NONE->WATCH illegal
    assert rec.state is AlertState.NONE


# ------------------------------------------------------------ events & notify


def test_every_transition_emits_exactly_one_event(cfg):
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    schedule = [100.0, 54.0, 50.0, 20.0, -1.0]
    events = []
    for i, p50 in enumerate(schedule):
        now = i * H
        events += eng.evaluate([assess("c1", "f1", now, p50)], now_s=now)
    rec = eng.states()[("c1", "f1")]
    assert len(events) == len(rec.history) == 4
    assert [e.state for e in events] == [s for _, s in rec.history]
    assert len(notifier.sent) == len(events)  # exactly once per event


def test_notifier_exception_is_swallowed(cfg):
    class BoomNotifier:
        def __init__(self):
            self.calls = 0

        def send(self, event, client):
            self.calls += 1
            raise RuntimeError("smtp down")

    boom = BoomNotifier()
    eng = AlertEngine(cfg, notifier=boom)
    ev = eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)
    assert [e.state for e in ev] == [AlertState.NOTICE_72H]  # event still emitted
    assert boom.calls == 1
    assert eng.states()[("c1", "f1")].state is AlertState.NOTICE_72H


def test_notifier_gets_placeholder_client_when_unknown(cfg):
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)  # no clients_by_id
    eng.evaluate([assess("c9", "f1", 0.0, 54.0)], now_s=0.0)
    (event, client), = notifier.sent
    assert client.client_id == "c9"
    assert client.name == "c9"


# -------------------------------------------------------------- multi-pair etc.


def test_pairs_are_independent(cfg):
    eng = AlertEngine(cfg)
    ev = eng.evaluate(
        [
            assess("c1", "f1", 0.0, 54.0),
            assess("c2", "f1", 0.0, 10.0),
            assess("c1", "f2", 0.0, 100.0),
        ],
        now_s=0.0,
    )
    by_pair = {(e.client_id, e.incident_id): e.state for e in ev}
    assert by_pair == {
        ("c1", "f1"): AlertState.NOTICE_72H,
        ("c2", "f1"): AlertState.EXTREME_24H,
    }
    assert eng.states()[("c1", "f2")].state is AlertState.NONE


def test_duplicate_assessments_use_freshest(cfg):
    eng = AlertEngine(cfg)
    stale = assess("c1", "f1", 0.0, 10.0)
    stale.computed_at_s = -100.0
    fresh = assess("c1", "f1", 0.0, 100.0)
    ev = eng.evaluate([stale, fresh], now_s=0.0)
    assert ev == []  # freshest says 100h out: no alert
    assert eng.states()[("c1", "f1")].state is AlertState.NONE


def test_states_records_are_asdict_serializable(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)
    eng.evaluate([assess("c1", "f1", 1 * H, 50.0)], now_s=1 * H)
    for rec in eng.states().values():
        d = dataclasses.asdict(rec)
        assert d["client_id"] == "c1"
        assert d["state"] == AlertState.WATCH  # str Enum
        json.dumps(d, default=str)  # round-trippable with default=str


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 6 — absence is not clearance (Sprint 2, Council-1 OPS-1).
# Blind pairs freeze; only trustworthy absence progresses the clear streak.
# Evidence: scratch_review/drill_failures.py (end-to-end drills ported to
# tests/test_failsafe.py; these are the engine-level unit semantics).
# ---------------------------------------------------------------------------


def _escalate_to_extreme(eng, now0=0.0):
    """Drive ('c1','f1') to EXTREME_24H with one assessed evaluation."""
    ev = eng.evaluate([assess("c1", "f1", now0, 10.0, p10_h=8.0)], now_s=now0)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_blind_pair_freezes_state_and_streaks(cfg):
    """A pair listed in blind_pairs must FREEZE: no transition, clear_streak
    untouched, blind_cycles counting up — for as many cycles as it stays blind."""
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    _escalate_to_extreme(eng)
    sends_before = len(notifier.sent)
    for k in range(1, 5):
        ev = eng.evaluate([], now_s=k * H, blind_pairs={("c1", "f1")})
        assert ev == []                                        # no transition
        rec = eng.states()[("c1", "f1")]
        assert rec.state is AlertState.EXTREME_24H             # frozen
        assert rec.blind_cycles == k
        assert rec.clear_streak == 0                           # untouched
    assert len(notifier.sent) == sends_before                  # ZERO client sends


def test_feeds_unhealthy_freezes_every_absent_pair(cfg):
    """feeds_healthy=False makes ALL absent pairs blind (a dead feed cannot
    name which fires it lost)."""
    eng = AlertEngine(cfg)
    _escalate_to_extreme(eng)
    for k in range(1, 4):
        ev = eng.evaluate([], now_s=k * H, feeds_healthy=False)
        assert ev == []
    rec = eng.states()[("c1", "f1")]
    assert rec.state is AlertState.EXTREME_24H
    assert rec.blind_cycles == 3


def test_blind_alarm_fires_at_threshold_not_before():
    cfg = ResponseConfig(blind_alarm_cycles=2)
    eng = AlertEngine(cfg)
    _escalate_to_extreme(eng)
    eng.pop_operator_events()                                  # drop escalation-cycle noise
    eng.evaluate([], now_s=1 * H, blind_pairs={("c1", "f1")})
    assert eng.pop_operator_events() == []                     # 1 < threshold 2
    eng.evaluate([], now_s=2 * H, blind_pairs={("c1", "f1")})
    events = eng.pop_operator_events()
    assert [e.kind for e in events] == ["degraded"]
    assert events[0].incident_id == "f1"
    assert events[0].detail["client_id"] == "c1"
    assert events[0].detail["blind_cycles"] == 2


def test_blind_pair_recovery_emits_recovered_once_and_resumes_ladder(cfg):
    eng = AlertEngine(cfg)
    _escalate_to_extreme(eng)
    for k in range(1, 4):
        eng.evaluate([], now_s=k * H, blind_pairs={("c1", "f1")})
    eng.pop_operator_events()
    # Assessed again: recovered fires exactly once, blind_cycles resets, and
    # the ladder resumes (still EXTREME while the threat persists).
    ev = eng.evaluate([assess("c1", "f1", 4 * H, 10.0, p10_h=8.0)], now_s=4 * H)
    assert ev == []                                            # still EXTREME, no flap
    kinds = [e.kind for e in eng.pop_operator_events()]
    assert kinds == ["recovered"]
    rec = eng.states()[("c1", "f1")]
    assert rec.blind_cycles == 0
    ev = eng.evaluate([assess("c1", "f1", 5 * H, 10.0, p10_h=8.0)], now_s=5 * H)
    assert eng.pop_operator_events() == []                     # recovered only once
    # Normal downgrade hysteresis works again after recovery.
    eng.evaluate([], now_s=6 * H)                              # trustworthy absence
    ev = eng.evaluate([], now_s=7 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]


def test_absent_with_healthy_feeds_still_clears_while_other_pair_blind(cfg):
    """The blind freeze must be pair-scoped: an assessed-clear/absent-healthy
    pair keeps its hysteresis walk to CLEARED even when a sibling is blind."""
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 10.0), assess("c2", "f2", 0.0, 10.0)],
                 now_s=0.0)
    ev1 = eng.evaluate([], now_s=1 * H, blind_pairs={("c2", "f2")})
    ev2 = eng.evaluate([], now_s=2 * H, blind_pairs={("c2", "f2")})
    states = eng.states()
    assert states[("c2", "f2")].state is AlertState.EXTREME_24H   # frozen
    assert states[("c2", "f2")].blind_cycles == 2
    assert states[("c1", "f1")].state is AlertState.CLEARED       # genuinely gone
    assert [(e.client_id, e.state) for e in ev1 + ev2] == [
        ("c1", AlertState.CLEARED)]


def test_legacy_two_arg_evaluate_keeps_old_absence_behavior(cfg):
    """Backward compat: evaluate(assessments, now_s) — no blind info — behaves
    exactly as before Sprint 2 (absence progresses the clear streak)."""
    eng = AlertEngine(cfg)
    _escalate_to_extreme(eng)
    eng.evaluate([], now_s=1 * H)
    ev = eng.evaluate([], now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]
    assert eng.states()[("c1", "f1")].blind_cycles == 0
    assert eng.pop_operator_events() == []


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 8 — containment gates the ladder, never the crew math
# (Sprint 2 D2, default OFF). The gate caps NEW escalations at WATCH for an
# incident at/above containment_watch_ceiling_pct with no acreage growth for
# containment_no_growth_h; it lifts IMMEDIATELY on growth or containment
# drop, never downgrades a pair already above WATCH, and never engages on
# unknown containment/acreage.
# ---------------------------------------------------------------------------


def imeta(iid="f1", name=None, containment=None, acreage=None):
    """One-incident incident_meta dict for evaluate(..., incident_meta=...)."""
    return {iid: {"name": name, "containment_pct": containment,
                  "acreage": acreage}}


def _gate_cfg(**kw):
    kw.setdefault("containment_gate_enabled", True)
    return ResponseConfig(**kw)


CONTAINED = dict(containment=95.0, acreage=800.0)


def test_containment_gate_default_off_ignores_meta():
    """Default config: the gate is OFF pending founder approval — meta with
    high containment + ancient no-growth history must not cap anything."""
    eng = AlertEngine(ResponseConfig())          # containment_gate_enabled=False
    eng.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    ev = eng.evaluate([assess("c1", "f1", 100 * H, 10.0, p10_h=8.0)],
                      now_s=100 * H, incident_meta=imeta(**CONTAINED))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_containment_cap_blocks_new_escalation_at_watch():
    eng = AlertEngine(_gate_cfg())
    # Track the incident at t=0 (containment 95%, acreage steady): the
    # no-growth clock starts here.
    eng.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    # 49 h later (>= containment_no_growth_h=48): extreme trigger arrives.
    t1 = 49 * H
    ev = eng.evaluate([assess("c1", "f1", t1, 10.0, p10_h=8.0)], now_s=t1,
                      incident_meta=imeta(**CONTAINED))
    assert [e.state for e in ev] == [AlertState.NOTICE_72H]   # not EXTREME
    # Next cycle: NOTICE auto-progresses to the WATCH ceiling...
    t2 = t1 + 300.0
    ev = eng.evaluate([assess("c1", "f1", t2, 10.0, p10_h=8.0)], now_s=t2,
                      incident_meta=imeta(**CONTAINED))
    assert [e.state for e in ev] == [AlertState.WATCH]
    # ...and holds there, cycle after cycle, while the cap is engaged.
    for k in (2, 3, 4):
        tk = t1 + k * 300.0
        ev = eng.evaluate([assess("c1", "f1", tk, 10.0, p10_h=8.0)],
                          now_s=tk, incident_meta=imeta(**CONTAINED))
        assert ev == []
        assert eng.states()[("c1", "f1")].state is AlertState.WATCH


def _capped_watch_engine():
    """Engine with ('c1','f1') held at the WATCH ceiling by the gate."""
    eng = AlertEngine(_gate_cfg())
    eng.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    t1 = 49 * H
    eng.evaluate([assess("c1", "f1", t1, 10.0, p10_h=8.0)], now_s=t1,
                 incident_meta=imeta(**CONTAINED))
    t2 = t1 + 300.0
    eng.evaluate([assess("c1", "f1", t2, 10.0, p10_h=8.0)], now_s=t2,
                 incident_meta=imeta(**CONTAINED))
    assert eng.states()[("c1", "f1")].state is AlertState.WATCH
    return eng, t2


def test_containment_cap_lifts_immediately_on_growth():
    eng, t2 = _capped_watch_engine()
    t3 = t2 + 300.0
    ev = eng.evaluate([assess("c1", "f1", t3, 10.0, p10_h=8.0)], now_s=t3,
                      incident_meta=imeta(containment=95.0, acreage=900.0))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]  # same cycle


def test_containment_cap_lifts_immediately_on_containment_drop():
    eng, t2 = _capped_watch_engine()
    t3 = t2 + 300.0
    ev = eng.evaluate([assess("c1", "f1", t3, 10.0, p10_h=8.0)], now_s=t3,
                      incident_meta=imeta(containment=60.0, acreage=800.0))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_containment_cap_never_downgrades_existing_extreme():
    """A pair already EXTREME when the gate engages is untouched: the cap
    stops NEW escalations only — no forced downgrade, normal hysteresis."""
    eng = AlertEngine(_gate_cfg())
    # Escalate BEFORE the no-growth window has elapsed (gate not engaged yet).
    eng.evaluate([assess("c1", "f1", 0.0, 10.0, p10_h=8.0)], now_s=0.0,
                 incident_meta=imeta(**CONTAINED))
    assert eng.states()[("c1", "f1")].state is AlertState.EXTREME_24H
    # 49 h later the gate engages — the EXTREME pair must hold, not drop.
    for k in range(3):
        tk = 49 * H + k * 300.0
        ev = eng.evaluate([assess("c1", "f1", tk, 10.0, p10_h=8.0)],
                          now_s=tk, incident_meta=imeta(**CONTAINED))
        assert ev == []
        assert eng.states()[("c1", "f1")].state is AlertState.EXTREME_24H


def test_containment_cap_requires_no_growth_window_elapsed():
    """Containment alone is not enough: first-sight incidents (or any growth
    within containment_no_growth_h) escalate normally."""
    eng = AlertEngine(_gate_cfg())
    eng.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    t1 = 10 * H                                   # only 10 h of history
    ev = eng.evaluate([assess("c1", "f1", t1, 10.0, p10_h=8.0)], now_s=t1,
                      incident_meta=imeta(**CONTAINED))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_containment_cap_unknown_containment_or_acreage_never_gates():
    for meta in (imeta(containment=None, acreage=800.0),
                 imeta(containment=95.0, acreage=None)):
        eng = AlertEngine(_gate_cfg())
        eng.evaluate([], now_s=0.0, incident_meta=meta)
        t1 = 49 * H
        ev = eng.evaluate([assess("c1", "f1", t1, 10.0, p10_h=8.0)],
                          now_s=t1, incident_meta=meta)
        assert [e.state for e in ev] == [AlertState.EXTREME_24H], meta


def test_containment_cap_p10_only_trigger_holds_none():
    """A capped incident whose only trigger is pessimistic-p10 (median beyond
    the notice window) may not enter the ladder at all: NONE->WATCH is
    illegal, and the cap forbids EXTREME — the pair holds NONE."""
    eng = AlertEngine(_gate_cfg())
    eng.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    t1 = 49 * H
    ev = eng.evaluate([assess("c1", "f1", t1, 90.0, p10_h=8.0)], now_s=t1,
                      incident_meta=imeta(**CONTAINED))
    assert ev == []
    assert eng.states()[("c1", "f1")].state is AlertState.NONE
    # Control (gate off): the p10 trigger escalates straight to EXTREME.
    ctrl = AlertEngine(ResponseConfig())
    ctrl.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    ev = ctrl.evaluate([assess("c1", "f1", t1, 90.0, p10_h=8.0)], now_s=t1,
                       incident_meta=imeta(**CONTAINED))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]


def test_containment_cap_is_per_incident():
    """Only the capped incident's pairs are ceilinged; a sibling fire on the
    same client escalates normally in the same cycle."""
    eng = AlertEngine(_gate_cfg())
    meta = {**imeta("f1", **CONTAINED),
            **imeta("f2", containment=10.0, acreage=100.0)}
    eng.evaluate([], now_s=0.0, incident_meta=meta)
    t1 = 49 * H
    ev = eng.evaluate([assess("c1", "f1", t1, 10.0, p10_h=8.0),
                       assess("c1", "f2", t1, 10.0, p10_h=8.0)], now_s=t1,
                      incident_meta=meta)
    by_pair = {(e.client_id, e.incident_id): e.state for e in ev}
    assert by_pair == {("c1", "f1"): AlertState.NOTICE_72H,
                       ("c1", "f2"): AlertState.EXTREME_24H}


def test_containment_cap_acreage_correction_reads_as_growth():
    """Acreage mapped DOWN then back up reads as growth (ambiguity lifts the
    cap — the fail-safe direction)."""
    eng, t2 = _capped_watch_engine()
    t3 = t2 + 300.0
    eng.evaluate([assess("c1", "f1", t3, 10.0, p10_h=8.0)], now_s=t3,
                 incident_meta=imeta(containment=95.0, acreage=700.0))
    assert eng.states()[("c1", "f1")].state is AlertState.WATCH  # still capped
    t4 = t3 + 300.0
    ev = eng.evaluate([assess("c1", "f1", t4, 10.0, p10_h=8.0)], now_s=t4,
                      incident_meta=imeta(containment=95.0, acreage=750.0))
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]     # lifted


def test_containment_cap_leaves_assessments_untouched():
    """The gate is ladder-only: the ThreatAssessment/ThreatEta objects the
    crew-safety math consumes are never mutated by evaluation (P10-derived
    deadlines stay identical capped or not)."""
    a_capped = assess("c1", "f1", 49 * H, 10.0, p10_h=8.0)
    a_free = assess("c1", "f1", 49 * H, 10.0, p10_h=8.0)
    capped = AlertEngine(_gate_cfg())
    capped.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    capped.evaluate([a_capped], now_s=49 * H, incident_meta=imeta(**CONTAINED))
    free = AlertEngine(ResponseConfig())
    free.evaluate([], now_s=0.0, incident_meta=imeta(**CONTAINED))
    free.evaluate([a_free], now_s=49 * H, incident_meta=imeta(**CONTAINED))
    assert a_capped.eta == a_free.eta            # byte-equal safety inputs
    assert a_capped.eta.eta_p10_s == 49 * H + 8.0 * H


# ---------------------------------------------------------------------------
# D3 — comms polish: every message names its incident; jump-past wording.
# ---------------------------------------------------------------------------


LONG_IID = "irwin-7e683f91-f94e"       # > 12 chars -> shortened in messages
LONG_META = {LONG_IID: {"name": "Volcano", "containment_pct": None,
                        "acreage": None}}
LONG_LABEL = "Volcano (irwin-7e683f…)"


def test_every_message_names_incident_with_name_and_short_id(cfg, clients):
    eng = AlertEngine(cfg, clients_by_id=clients)
    # NOTICE
    ev = eng.evaluate([assess("c1", LONG_IID, 0.0, 54.0)], now_s=0.0,
                      incident_meta=LONG_META)
    assert LONG_LABEL in ev[0].message
    assert "Casa Blanca" in ev[0].message
    # WATCH (auto-progress)
    ev = eng.evaluate([assess("c1", LONG_IID, 1 * H, 50.0)], now_s=1 * H,
                      incident_meta=LONG_META)
    assert [e.state for e in ev] == [AlertState.WATCH]
    assert LONG_LABEL in ev[0].message
    # EXTREME
    ev = eng.evaluate([assess("c1", LONG_IID, 2 * H, 10.0, p10_h=8.0)],
                      now_s=2 * H, incident_meta=LONG_META)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
    assert LONG_LABEL in ev[0].message
    # IMPACTED
    ev = eng.evaluate([assess("c1", LONG_IID, 3 * H, -0.5, p10_h=-1.0)],
                      now_s=3 * H, incident_meta=LONG_META)
    assert [e.state for e in ev] == [AlertState.IMPACTED]
    assert LONG_LABEL in ev[0].message


def test_cleared_message_names_incident(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", LONG_IID, 0.0, 54.0)], now_s=0.0,
                 incident_meta=LONG_META)
    eng.evaluate([assess("c1", LONG_IID, 1 * H, 50.0)], now_s=1 * H,
                 incident_meta=LONG_META)
    eng.evaluate([], now_s=2 * H)
    ev = eng.evaluate([], now_s=3 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]
    assert LONG_LABEL in ev[0].message
    assert "All clear" in ev[0].message


def test_messages_fall_back_to_short_incident_id_without_meta(cfg):
    """Legacy callers (no incident_meta) still get incident naming — the id
    itself — closing the two-fire message ambiguity even meta-less."""
    eng = AlertEngine(cfg)
    ev = eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)
    assert "incident f1" in ev[0].message


def test_jump_past_extreme_reads_as_imminent_impact_not_stale_access(cfg):
    """p50 crossing to <= 0 between cycles while not yet IMPACTED must read
    as imminent impact — never 'ensure property access now' for a fire the
    forecast says is already AT the property (ticket D3)."""
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)      # NOTICE
    eng.evaluate([assess("c1", "f1", 1 * H, 50.0)], now_s=1 * H)  # WATCH
    # Forecast jumps past the property: median arrival now in the past.
    ev = eng.evaluate([assess("c1", "f1", 2 * H, -0.2, p10_h=-0.5)],
                      now_s=2 * H)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
    msg = ev[0].message
    assert "AT the property NOW" in msg
    assert "do not approach" in msg
    assert "crews withdrawing" in msg
    assert "ensure property access" not in msg
    # The pair then rides the normal terminal path.
    ev = eng.evaluate([assess("c1", "f1", 2 * H + 300.0, -0.3, p10_h=-0.6)],
                      now_s=2 * H + 300.0)
    assert [e.state for e in ev] == [AlertState.IMPACTED]


def test_normal_extreme_keeps_access_wording(cfg):
    eng = AlertEngine(cfg)
    ev = eng.evaluate([assess("c1", "f1", 0.0, 20.0, p10_h=15.0)], now_s=0.0)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
    assert "ensure property access now" in ev[0].message
    assert "AT the property NOW" not in ev[0].message


# ---------------------------------------------------------------------------
# C2 — notified-ledger seam semantics (what was ACTUALLY sent).
# ---------------------------------------------------------------------------


def test_notified_ledger_records_only_successful_sends(cfg):
    class FlakyNotifier:
        def __init__(self):
            self.fail = True

        def send(self, event, client):
            return not self.fail

    flaky = FlakyNotifier()
    eng = AlertEngine(cfg, notifier=flaky)
    eng.evaluate([assess("c1", "f1", 0.0, 54.0)], now_s=0.0)   # send fails
    assert eng.notified_ledger() == {}                         # not ledgered
    flaky.fail = False
    ev = eng.evaluate([assess("c1", "f1", 1 * H, 50.0)], now_s=1 * H)  # WATCH
    assert [e.state for e in ev] == [AlertState.WATCH]
    assert eng.notified_ledger() == {("c1", "f1", "watch"): 1 * H}


def test_notified_ledger_empty_without_notifier(cfg):
    eng = AlertEngine(cfg)
    eng.evaluate([assess("c1", "f1", 0.0, 10.0)], now_s=0.0)
    assert eng.notified_ledger() == {}                # nothing actually sent


def test_notified_ledger_raising_notifier_not_ledgered(cfg):
    class Boom:
        def send(self, event, client):
            raise RuntimeError("smtp down")

    eng = AlertEngine(cfg, notifier=Boom())
    eng.evaluate([assess("c1", "f1", 0.0, 10.0)], now_s=0.0)
    assert eng.notified_ledger() == {}


def test_notified_ledger_skips_suppressed_all_clear(cfg):
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.evaluate([assess("c1", "f1", 0.0, 60.0),
                  assess("c1", "f2", 0.0, 10.0, p10_h=8.0)], now_s=0.0)
    eng.evaluate([assess("c1", "f1", 1 * H, 60.0),
                  assess("c1", "f2", 1 * H, 10.0, p10_h=8.0)], now_s=1 * H)
    # f1 clears while f2 is still EXTREME: CLEARED event emitted, send
    # suppressed (active-alert gate) -> ledger must NOT record it.
    eng.evaluate([assess("c1", "f2", 2 * H, 10.0, p10_h=8.0)], now_s=2 * H)
    ev = eng.evaluate([assess("c1", "f2", 3 * H, 10.0, p10_h=8.0)], now_s=3 * H)
    assert [e.state for e in ev] == [AlertState.CLEARED]
    assert ("c1", "f1", "cleared") not in eng.notified_ledger()
    sent_states = [e.state for e, _ in notifier.sent]
    assert AlertState.CLEARED not in sent_states
