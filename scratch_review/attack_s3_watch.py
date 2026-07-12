"""ADVERSARIAL VERIFIER (Session 3) — attack #5: watch daemon (C1/C2).

Claims under attack (verifier brief #5, mock feeds only — no network):
  5a. AOI threading: every feed fetch is called WITH the bbox; a feed stub
      without a bbox parameter degrades to feed_blind (never a crash) when
      an AOI is set, and the aoi=None legacy path still calls no-arg.
  5b. Poll period honored under an injected clock (sleep = period - elapsed,
      floored at 0 when a cycle overruns).
  5c. A dead feed surfaces degraded=true on /healthz within ONE cycle (and
      recovery clears it), over real HTTP.
  5d. Clean shutdown persists: Ctrl-C in the sleep AND mid-cycle both land a
      final persist; on-disk state equals in-memory state.
  5e. --dry-notify forces LogNotifier even with WGRESP_NOTIFY_SES=1.

FINDINGS documented here:
  F4  [FIXED by the Session-3 fixer; the test below asserts the FIXED
      behavior and is promoted to tests/test_attack_s3_watch.py]
      (major, silence-ward, narrow window) Ctrl-C landing INSIDE
      AlertEngine.evaluate() between transition application and the notify
      loop (e.g. during a slow SES send — notifier exceptions are caught
      but KeyboardInterrupt is a BaseException and propagates) is caught by
      _WatchDaemon.run(), which then runs the FINAL PERSIST: the
      applied-but-never-notified transition is persisted as current state,
      so the restarted daemon saw no transition and the client alert was
      swallowed PERMANENTLY. FIX (restored-unsent reconcile, alerts.py):
      restore(..., notified_ledger=...) flags every restored pair holding an
      ACTIVE threat state with no ledgered send at/after entered_at_s; the
      next evaluate() re-fires exactly that state's message (fresh ETAs when
      assessed), retrying until the notifier succeeds. Consistent
      record+ledger restores flag nothing — the restart drill still re-fires
      nothing. Hard kill (no final persist) stays safe (counterfactual
      below).

Run from repo root:
    py -3 -m pytest scratch_review/attack_s3_watch.py -v
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_s3_common import (  # noqa: E402
    AOI, T0, FakeClock, NoBboxFeed, ScriptedFeed, StubRegistry, StubTerrain,
    StubWeather, client, incident, make_daemon, small_cfg,
)
from wgresponse import cli as cli_mod  # noqa: E402
from wgresponse import server  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import AlertState  # noqa: E402
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402


# ---------------------------------------------------------------------------
# 5a. AOI threading
# ---------------------------------------------------------------------------

def test_aoi_bbox_reaches_every_fetch_including_blind_cycles(tmp_path):
    daemon, feed, _, _, _ = make_daemon(tmp_path)
    daemon.run_cycle()
    feed.ok = False                          # dead cycles still pass the bbox
    daemon.run_cycle()
    feed.ok = True
    daemon.run_cycle()
    assert feed.bboxes == [AOI, AOI, AOI]


def test_bboxless_feed_with_aoi_degrades_to_feed_blind_not_crash():
    """A legacy no-bbox adapter under an AOI'd monitor: fetch raises
    TypeError -> guarded as a blind feed (degraded + feed_blind), never a
    crash and never silently treated as clear."""
    cfg = small_cfg()
    engine = AlertEngine(cfg)
    mon = ThreatMonitor(feeds=[NoBboxFeed([incident()])],
                        weather_provider=StubWeather(),
                        terrain_provider=StubTerrain(),
                        registry=StubRegistry([client()]),
                        alert_engine=engine, cfg=cfg, safe_zones=[], aoi=AOI)
    res = mon.cycle(T0)
    assert res.degraded is True
    assert any(e.kind == "feed_blind" for e in res.operator_events)
    assert res.assessments == []


def test_aoi_none_keeps_legacy_no_arg_fetch():
    cfg = small_cfg()
    mon = ThreatMonitor(feeds=[NoBboxFeed([incident()])],
                        weather_provider=StubWeather(),
                        terrain_provider=StubTerrain(),
                        registry=StubRegistry([client()]),
                        alert_engine=AlertEngine(cfg), cfg=cfg,
                        safe_zones=[], aoi=None)
    res = mon.cycle(T0)
    assert res.degraded is False
    assert len(res.assessments) == 1


# ---------------------------------------------------------------------------
# 5b. poll period honored (injected clock)
# ---------------------------------------------------------------------------

def test_period_honored_sleep_fills_to_period_and_cadence_holds(tmp_path):
    clock = FakeClock(T0, step=1.0)
    sleeps = []

    def sim_sleep(s):
        sleeps.append(s)
        clock.t += s                          # sleeping advances "time"

    daemon, _, _, _, _ = make_daemon(tmp_path, clock=clock, sleep=sim_sleep)
    cycle_times = []
    real_cycle = daemon._monitor.cycle

    def spy_cycle(now_s):
        cycle_times.append(now_s)
        return real_cycle(now_s)

    daemon._monitor.cycle = spy_cycle
    daemon.run(max_cycles=3)
    # loop reads the clock 3x/iteration (step 1 s): elapsed measures 2 s,
    # so each inter-cycle sleep must be period - elapsed = 298 s...
    assert sleeps == [298.0, 298.0]
    # ...and consecutive cycle timestamps sit one period + loop-reads apart.
    t1, t2, t3 = cycle_times
    assert t2 - t1 == pytest.approx(301.0)
    assert t3 - t2 == pytest.approx(301.0)


def test_overrunning_cycle_sleeps_zero_never_negative(tmp_path):
    clock = FakeClock(T0, step=200.0)         # 3 reads -> "cycle takes 400 s"
    sleeps = []
    daemon, _, _, _, _ = make_daemon(tmp_path, clock=clock,
                                     sleep=sleeps.append)
    daemon.run(max_cycles=3)
    assert sleeps == [0.0, 0.0]               # floored, never negative


# ---------------------------------------------------------------------------
# 5c. /healthz degraded within one cycle, over real HTTP
# ---------------------------------------------------------------------------

def _get_healthz(daemon):
    srv = server.serve(0, state_provider=daemon.state,
                       plan_provider=daemon.plan)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1",
                                          srv.server_address[1], timeout=10)
        conn.request("GET", "/healthz")
        body = json.loads(conn.getresponse().read())
        conn.close()
        return body
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


def test_dead_feed_flips_healthz_degraded_within_one_cycle(tmp_path,
                                                           monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    daemon, feed, _, _, _ = make_daemon(tmp_path)
    res1 = daemon.run_cycle()
    healthy = _get_healthz(daemon)            # healthz needs no auth
    assert healthy["ok"] is True and healthy["degraded"] is False
    assert healthy["cycles_run"] == 1
    assert healthy["last_cycle_t_s"] == res1.t_s

    feed.ok = False
    res2 = daemon.run_cycle()                 # ONE cycle after the feed dies
    sick = _get_healthz(daemon)
    assert sick["degraded"] is True
    assert sick["operator_event_count"] >= 1
    assert sick["last_cycle_t_s"] == res2.t_s
    assert "operator_events" not in sick      # bodies stay behind auth

    feed.ok = True
    daemon.run_cycle()                        # recovery clears it next cycle
    assert _get_healthz(daemon)["degraded"] is False


# ---------------------------------------------------------------------------
# 5d. clean shutdown persists (sleep-KI covered in tests/test_cli.py;
#      here: KI raised INSIDE monitor.cycle, and disk == memory)
# ---------------------------------------------------------------------------

def test_interrupt_inside_cycle_still_lands_final_persist(tmp_path):
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

    # restart re-fires nothing
    daemon2, _, _, notifier2, _ = make_daemon(tmp_path)
    assert daemon2.restore() is True
    daemon2.run_cycle()
    assert notifier2.sent == []


class InterruptedSesLikeNotifier:
    """Simulates Ctrl-C during a hanging notifier send (SES socket wait).
    KeyboardInterrupt is a BaseException: alerts._notify's `except
    Exception` does NOT catch it, so it propagates mid-evaluate."""

    def __init__(self):
        self.sent = []

    def send(self, event, client):
        raise KeyboardInterrupt


def test_FINDING_f4_ctrl_c_during_send_re_fires_after_restart(tmp_path):
    """FINDING F4 — FIXED (restored-unsent reconcile): the EXTREME transition
    is applied, Ctrl-C interrupts its send, run() catches the KI and
    FINAL-PERSISTS the applied-but-unnotified state. The restarted daemon now
    detects the active state with no ledgered send for its occupancy and
    RE-FIRES the EXTREME notification exactly once — with the fresh cycle's
    real ETAs — then never again for the unchanged world."""
    import math
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
    # wall clock later than the first process — as in reality) reconciles
    # records vs ledger and re-fires the swallowed EXTREME once:
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


# ---------------------------------------------------------------------------
# 5e. --dry-notify suppresses the real notifier
# ---------------------------------------------------------------------------

def test_dry_notify_forces_lognotifier_even_with_ses_env(tmp_path,
                                                         monkeypatch):
    from wgresponse.notify import SesNotifier
    monkeypatch.setenv(SesNotifier.ENV_FLAG, "1")
    monkeypatch.setenv("SES_SENDER", "alerts@example.com")
    dry, desc_dry = cli_mod._watch_notifier(
        argparse.Namespace(dry_notify=True), tmp_path)
    assert isinstance(dry, LogNotifier)
    assert "--dry-notify" in desc_dry
    wet, desc_wet = cli_mod._watch_notifier(
        argparse.Namespace(dry_notify=False), tmp_path)
    assert isinstance(wet, SesNotifier)       # env flag honored without --dry
    monkeypatch.delenv(SesNotifier.ENV_FLAG)
    off, desc_off = cli_mod._watch_notifier(
        argparse.Namespace(dry_notify=False), tmp_path)
    assert isinstance(off, LogNotifier)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
