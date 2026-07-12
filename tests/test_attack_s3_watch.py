"""Session-3 watch-daemon attack regressions (verifier finding F4 + brief #5).

Promoted from scratch_review/attack_s3_watch.py after the fixer pass.

FINDING F4 (major, silence-ward, FIXED here-in): Ctrl-C landing inside a
notifier send (KeyboardInterrupt is a BaseException — alerts._notify's
`except Exception` deliberately lets it propagate) aborts evaluate() AFTER
the cycle's transitions were applied but BEFORE the send was ledgered;
_WatchDaemon.run() catches the KI and runs the FINAL PERSIST, durably
recording an applied-but-never-notified state. Pre-fix, the restarted daemon
saw no transition and the client alert was swallowed PERMANENTLY (a hard
kill was ironically safe — no persist meant the restart re-alerted).

FIX (RESTORED-UNSENT RECONCILE, wgresponse.alerts): restore(...,
notified_ledger=...) flags every restored pair holding an ACTIVE threat
state (NOTICE_72H/WATCH/EXTREME_24H) with no ledgered send at/after its
entered_at_s; the next evaluate() re-fires exactly that state's message
(fresh ETAs when assessed that cycle), retrying until the notifier reports
success. Consistent record+ledger restores flag nothing — the Sprint-2
restart drill ("re-fires nothing") stays intact — and CLEARED/IMPACTED are
never re-fired (downgrades can never hide a threat; IMPACTED is terminal).

Deterministic: real ThreatMonitor/AlertEngine/StateStore on the stub stack
in tests/attack_s3_stack.py; injected clocks; no network.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_s3_stack import (  # noqa: E402
    H, T0, FakeClock, client, make_daemon, small_cfg,
)
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertRecord, AlertState, ThreatAssessment, ThreatEta,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402


class InterruptedSesLikeNotifier:
    """Simulates Ctrl-C during a hanging notifier send (SES socket wait).
    KeyboardInterrupt is a BaseException: alerts._notify's `except
    Exception` does NOT catch it, so it propagates mid-evaluate."""

    def __init__(self):
        self.sent = []

    def send(self, event, client):
        raise KeyboardInterrupt


class FlakyNotifier:
    """Fails the first `fail_n` sends (returns False), then succeeds."""

    def __init__(self, fail_n=1):
        self.fail_n = int(fail_n)
        self.attempts = []
        self.sent = []

    def send(self, event, client):
        self.attempts.append(event)
        if len(self.attempts) <= self.fail_n:
            return False
        self.sent.append((event, client))
        return True


# ---------------------------------------------------------------------------
# F4 end-to-end: interrupted send -> clean-shutdown persist -> restart
# re-fires the swallowed alert exactly once
# ---------------------------------------------------------------------------

def test_f4_ctrl_c_during_send_re_fires_after_restart(tmp_path):
    daemon, _, engine, notifier, store = make_daemon(
        tmp_path, notifier=InterruptedSesLikeNotifier())
    ran = daemon.run()                        # KI inside cycle 1's evaluate
    assert ran == 0
    assert notifier.sent == []                # nothing went out
    rec = engine.states()[("c1", "inc-1")]
    assert rec.state is AlertState.EXTREME_24H     # transition applied...
    assert engine.notified_ledger() == {}          # ...send never happened
    loaded = StateStore(tmp_path / "state").load()
    assert loaded is not None and loaded["meta"]["final"] is True
    assert loaded["alert_records"][("c1", "inc-1")].state \
        is AlertState.EXTREME_24H             # the silent state IS durable...

    # ...but the restarted daemon (healthy notifier, unchanged hot world,
    # wall clock later than process 1 — as in reality) reconciles records vs
    # ledger and re-fires the swallowed EXTREME exactly once:
    daemon2, _, engine2, notifier2, _ = make_daemon(
        tmp_path, clock=FakeClock(T0 + 600.0))
    assert daemon2.restore() is True
    daemon2.run_cycle()
    assert engine2.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    assert [e.state for e, _ in notifier2.sent] == [AlertState.EXTREME_24H]
    ev = notifier2.sent[0][0]
    assert math.isfinite(ev.eta_p50_h)        # fresh ETAs, not 'n/a'
    assert ("c1", "inc-1", "extreme_24h") in engine2.notified_ledger()
    daemon2.run_cycle()                       # unchanged world
    assert len(notifier2.sent) == 1           # exactly once, then silence

    # A THIRD restart with the now-consistent state re-fires NOTHING (the
    # reconcile is ledger-driven, not unconditional).
    daemon2b, _, _, notifier2b, _ = make_daemon(
        tmp_path, clock=FakeClock(T0 + 1200.0))
    assert daemon2b.restore() is True
    daemon2b.run_cycle()
    assert notifier2b.sent == []

    # Counterfactual: a HARD KILL (no final persist) stays safe — the restart
    # re-evaluates, re-transitions, re-notifies.
    store.path.unlink()                       # simulate no-persist crash
    daemon3, _, _, notifier3, _ = make_daemon(tmp_path)
    assert daemon3.restore() is False
    daemon3.run_cycle()
    assert [e.state for e, _ in notifier3.sent] == [AlertState.EXTREME_24H]


def test_f4_refire_retries_until_notifier_succeeds(tmp_path):
    """The re-fire is retried each cycle while the notifier keeps failing
    (an outage right after the restart must not re-swallow the alert), and
    stops permanently once one send succeeds."""
    daemon, _, _, _, _ = make_daemon(tmp_path,
                                     notifier=InterruptedSesLikeNotifier())
    daemon.run()                              # swallowed EXTREME persisted

    flaky = FlakyNotifier(fail_n=2)
    daemon2, _, engine2, _, _ = make_daemon(
        tmp_path, notifier=flaky, clock=FakeClock(T0 + 600.0))
    assert daemon2.restore() is True
    daemon2.run_cycle()                       # attempt 1: fails
    daemon2.run_cycle()                       # attempt 2: fails
    assert flaky.sent == [] and len(flaky.attempts) == 2
    daemon2.run_cycle()                       # attempt 3: succeeds
    assert [e.state for e, _ in flaky.sent] == [AlertState.EXTREME_24H]
    assert ("c1", "inc-1", "extreme_24h") in engine2.notified_ledger()
    daemon2.run_cycle()
    assert len(flaky.attempts) == 3           # no further attempts


def test_interrupt_inside_cycle_still_lands_final_persist(tmp_path):
    """Brief 5d: Ctrl-C raised INSIDE monitor.cycle() still returns cleanly
    from run() with a final persist, and disk state == memory state."""
    daemon, _, engine, notifier, store = make_daemon(tmp_path)
    real_cycle = daemon._monitor.cycle
    calls = {"n": 0}

    def flaky_cycle(now_s):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt          # Ctrl-C lands mid-cycle 2
        return real_cycle(now_s)

    daemon._monitor.cycle = flaky_cycle
    ran = daemon.run()                        # must return, not raise
    assert ran == 1 and daemon.cycles_run == 1
    loaded = StateStore(tmp_path / "state").load()
    assert loaded is not None
    assert loaded["meta"]["final"] is True
    assert loaded["meta"]["cycles_run"] == 1
    # disk state == in-memory state at interrupt time
    assert {k: r.state for k, r in loaded["alert_records"].items()} == \
        {k: r.state for k, r in engine.states().items()}
    assert loaded["notified_ledger"] == engine.notified_ledger()
    assert ("c1", "inc-1", "extreme_24h") in loaded["notified_ledger"]

    # restart re-fires nothing: the ledger is CONSISTENT with the records
    # (cycle 1's send completed before the interrupt), so the F4 reconcile
    # must flag nothing.
    daemon2, _, _, notifier2, _ = make_daemon(
        tmp_path, clock=FakeClock(T0 + 600.0))
    assert daemon2.restore() is True
    daemon2.run_cycle()
    assert notifier2.sent == []


# ---------------------------------------------------------------------------
# engine-level reconcile pins (unit surface of the F4 fix)
# ---------------------------------------------------------------------------

def _hot(now_s, cid="c1", iid="f1", p10_h=6.0, p50_h=10.0):
    eta = ThreatEta(eta_p10_s=now_s + p10_h * H, eta_p50_s=now_s + p50_h * H,
                    eta_p90_s=now_s + p50_h * 1.5 * H)
    return ThreatAssessment(client_id=cid, incident_id=iid, eta=eta,
                            computed_at_s=now_s)


def _watch_rec(entered_at_s, state=AlertState.WATCH, cid="c1", iid="f1"):
    return AlertRecord(client_id=cid, incident_id=iid, state=state,
                       entered_at_s=entered_at_s, last_eval_s=entered_at_s,
                       clear_streak=0, blind_cycles=0,
                       history=[(entered_at_s, state)])


def test_reconcile_refires_unledgered_watch_with_fresh_etas():
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier,
                      clients_by_id={"c1": client()})
    eng.restore({("c1", "f1"): _watch_rec(T0 - H)}, notified_ledger={})
    eng.evaluate([_hot(T0, p10_h=20.0, p50_h=40.0)], now_s=T0)  # WATCH band
    assert [e.state for e, _ in notifier.sent] == [AlertState.WATCH]
    ev = notifier.sent[0][0]
    assert ev.eta_p50_h == pytest.approx(40.0)   # THIS cycle's fresh ETA
    assert ev.prev is AlertState.WATCH           # a re-send, not a transition
    eng.evaluate([_hot(T0 + 300.0, p10_h=20.0, p50_h=40.0)], now_s=T0 + 300.0)
    assert len(notifier.sent) == 1               # once, then silence


def test_reconcile_skips_occupancies_the_ledger_already_covers():
    """Ledger entry at/after entered_at_s == sent: nothing re-fires (the
    restart drill's 'restart re-fires nothing' for consistent state)."""
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.restore({("c1", "f1"): _watch_rec(T0 - H)},
                notified_ledger={("c1", "f1", "watch"): T0 - H})
    eng.evaluate([_hot(T0, p10_h=20.0, p50_h=40.0)], now_s=T0)
    assert notifier.sent == []


def test_reconcile_refires_re_entered_state_with_stale_ledger_entry():
    """A pair that legally re-entered EXTREME (EXTREME->WATCH->EXTREME) with
    only the FIRST extreme send ledgered: the ledger entry predates the
    current entered_at_s, so the second occupancy is re-fired."""
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    rec = AlertRecord(client_id="c1", incident_id="f1",
                      state=AlertState.EXTREME_24H, entered_at_s=T0 - H,
                      last_eval_s=T0 - H, clear_streak=0, blind_cycles=0,
                      history=[(T0 - 5 * H, AlertState.EXTREME_24H),
                               (T0 - 3 * H, AlertState.WATCH),
                               (T0 - H, AlertState.EXTREME_24H)])
    eng.restore({("c1", "f1"): rec}, notified_ledger={
        ("c1", "f1", "extreme_24h"): T0 - 5 * H,   # the FIRST occupancy only
        ("c1", "f1", "watch"): T0 - 3 * H,
    })
    eng.evaluate([_hot(T0)], now_s=T0)
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]


def test_reconcile_never_refires_cleared_or_impacted():
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.restore({
        ("c1", "f1"): _watch_rec(T0 - H, state=AlertState.CLEARED),
        ("c2", "f2"): _watch_rec(T0 - H, state=AlertState.IMPACTED,
                                 cid="c2", iid="f2"),
    }, notified_ledger={})
    eng.evaluate([], now_s=T0)
    assert notifier.sent == []


def test_reconcile_defers_to_a_same_cycle_transition():
    """A restored-unsent WATCH pair that escalates to EXTREME on the very
    next cycle sends exactly the EXTREME transition message — the stale
    WATCH re-fire is dropped (pair left the unsent occupancy)."""
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.restore({("c1", "f1"): _watch_rec(T0 - H)}, notified_ledger={})
    eng.evaluate([_hot(T0)], now_s=T0)           # hot: WATCH -> EXTREME
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]
    eng.evaluate([_hot(T0 + 300.0)], now_s=T0 + 300.0)
    assert len(notifier.sent) == 1


def test_reconcile_absent_pair_refires_with_na_etas():
    """The pair's incident is absent on the post-restart cycle (trustworthy
    absence): the owed message still goes out, with 'n/a' ETAs — the
    already-applied transition owes its client a message either way."""
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.restore({("c1", "f1"): _watch_rec(T0 - H)}, notified_ledger={})
    eng.evaluate([], now_s=T0)                   # nothing assessed
    assert [e.state for e, _ in notifier.sent] == [AlertState.WATCH]
    assert math.isinf(notifier.sent[0][0].eta_p50_h)


def test_legacy_restore_without_ledger_kwarg_never_reconciles():
    """restore(records) without the notified_ledger kwarg (legacy seam) has
    no send knowledge — it must not guess: no re-fire."""
    cfg = small_cfg()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    eng.restore({("c1", "f1"): _watch_rec(T0 - H)})
    eng.evaluate([_hot(T0, p10_h=20.0, p50_h=40.0)], now_s=T0)
    assert notifier.sent == []
