"""Session-3 persistence attack regressions (verifier finding F1 + brief #1).

Promoted from scratch_review/attack_s3_persist.py after the fixer pass. The
load-bearing guarantee under attack: `wgresponse watch` must NEVER crash at
startup on a bad state file — StateStore.load() never raises (contracts
persist seam), corruption is a loud ERROR + fresh start, and a fresh start
errs re-notify-ward (re-alerts exactly once), never silence-ward.

FINDING F1 (blocking, FIXED here-in):
  (a) NON-UTF-8 bytes (post-power-loss stale-sector garbage — save() now
      fsyncs before os.replace() to shrink that window) raised
      UnicodeDecodeError out of load() and killed the daemon at startup;
      now: loud ERROR + None + fresh start + one re-alert.
  (b) schema-valid JSON whose alert_records/notified_ledger is a non-list
      scalar (7 / 3.5 / true) raised TypeError past the per-record guards;
      now: loud ERROR, container treated EMPTY, affected pairs re-track
      fresh (re-alerts rather than silences).
Plus the corruption corpus that already worked (empty / zeros / truncated /
non-JSON / wrong-schema), the per-record skip, ledger poisoning both ways,
restore->blind continuation, and IMPACTED terminality across restarts.

Deterministic: real ThreatMonitor/AlertEngine/StateStore on the stub stack in
tests/attack_s3_stack.py; injected clocks; no network.
"""
from __future__ import annotations

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_s3_stack import (  # noqa: E402
    H, T0, FakeClock, incident, make_daemon,
)
from wgresponse.contracts import AlertRecord, AlertState  # noqa: E402
from wgresponse.persist import SCHEMA_VERSION, STATE_FILENAME, StateStore  # noqa: E402


def _seed_valid_state(tmp_path):
    """Run one real escalation cycle and persist it; return its bytes."""
    daemon, _, engine, notifier, store = make_daemon(tmp_path)
    daemon.run_cycle()
    assert notifier.sent, "harness must escalate + notify on cycle 1"
    assert store.path.exists()
    return store.path.read_bytes()


# ---------------------------------------------------------------------------
# corrupt / truncated / empty state files: fresh + loud + one re-alert
# ---------------------------------------------------------------------------

CORRUPT_BODIES = [
    ("empty", b""),
    ("all-zeros-powerloss", b"\x00" * 512),     # NUL decodes as UTF-8 fine
    ("json-array", b"[]"),
    ("json-string", b'"hello"'),
    ("json-number", b"123"),
    ("json-null", b"null"),
    ("empty-dict-no-version", b"{}"),
    ("wrong-version", json.dumps({"schema_version": 999}).encode()),
    ("string-version", json.dumps({"schema_version": "1"}).encode()),
]


@pytest.mark.parametrize("label,body", CORRUPT_BODIES,
                         ids=[c[0] for c in CORRUPT_BODIES])
def test_corrupt_state_file_restores_fresh_loudly_never_crashes(
        tmp_path, caplog, label, body):
    _seed_valid_state(tmp_path)                 # a real file existed first
    store_dir = tmp_path / "state"
    (store_dir / STATE_FILENAME).write_bytes(body)

    daemon, _, engine, notifier, store = make_daemon(tmp_path)
    with caplog.at_level(logging.INFO, logger="wgresponse.persist"):
        restored = daemon.restore()             # must not raise
    assert restored is False
    assert engine.states() == {}                # fresh
    assert notifier.sent == []                  # restore itself sends NOTHING
    lvls = {r.levelno for r in caplog.records if "persist" in r.name}
    assert logging.ERROR in lvls, f"{label}: no loud persist note logged"

    # daemon proceeds: next cycle runs and RE-ALERTS (re-notify-ward, the
    # documented safe polarity for a lost state file) — exactly one
    # escalation send, no crash-loop.
    daemon.run_cycle()
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0].state is AlertState.EXTREME_24H
    daemon.run_cycle()                          # unchanged world
    assert len(notifier.sent) == 1              # and it does not re-fire again


def test_truncated_state_file_restores_fresh_loudly(tmp_path, caplog):
    body = _seed_valid_state(tmp_path)
    (tmp_path / "state" / STATE_FILENAME).write_bytes(body[: len(body) // 2])
    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert daemon.restore() is False
    assert any("corrupt" in r.message.lower() for r in caplog.records)
    assert notifier.sent == []
    daemon.run_cycle()                          # runs fine, re-alerts
    assert len(notifier.sent) == 1


def test_state_path_is_a_directory_restores_fresh_not_crash(tmp_path, caplog):
    _seed_valid_state(tmp_path)
    p = tmp_path / "state" / STATE_FILENAME
    p.unlink()
    p.mkdir()                                   # state "file" is a directory
    daemon, _, _, notifier, _ = make_daemon(tmp_path)
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert daemon.restore() is False        # OSError branch, no raise
    assert notifier.sent == []
    daemon.run_cycle()
    assert len(notifier.sent) == 1


def test_structurally_valid_file_with_one_garbage_record_keeps_rest(
        tmp_path, caplog):
    """Per-record skip: one malformed record must not discard the other pair."""
    _seed_valid_state(tmp_path)
    p = tmp_path / "state" / STATE_FILENAME
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["alert_records"], "seed state must carry the c1 record"
    payload["alert_records"].append({"client_id": "cX"})       # malformed
    payload["alert_records"].append(                            # bad enum
        {"client_id": "cY", "incident_id": "iY", "state": "IMPACTED",
         "entered_at_s": T0, "last_eval_s": T0, "clear_streak": 0,
         "blind_cycles": 0, "history": []})
    p.write_text(json.dumps(payload), encoding="utf-8")

    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert daemon.restore() is True
    assert ("c1", "inc-1") in engine.states()   # survivor restored
    assert not any(k[0] in ("cX", "cY") for k in engine.states())
    skips = [r for r in caplog.records if "skipping malformed" in r.message]
    assert len(skips) == 2                      # loud, one per bad record
    daemon.run_cycle()                          # unchanged world
    assert notifier.sent == []                  # intact record: NO re-fire


# ---------------------------------------------------------------------------
# FINDING F1 (FIXED) — the corruption classes that used to make load() raise
# ---------------------------------------------------------------------------

BINARY_POISON = [
    ("invalid-utf8-garbage", b"\x00\xff\x13PK\x03\x04 stale sectors"),
    ("lone-continuation-byte", b'{"schema_version": 1}\x80'),
    ("utf16-encoded-json", '{"schema_version": 1}'.encode("utf-16")),
]


@pytest.mark.parametrize("label,body", BINARY_POISON,
                         ids=[c[0] for c in BINARY_POISON])
def test_f1a_non_utf8_state_file_loads_fresh_loudly(tmp_path, caplog, label,
                                                    body):
    """F1a regression pin: invalid-UTF-8 state bytes (post-power-loss sector
    garbage) load as None with a loud ERROR — the daemon starts FRESH instead
    of crashing at startup, and the next cycle re-alerts exactly once."""
    _seed_valid_state(tmp_path)
    (tmp_path / "state" / STATE_FILENAME).write_bytes(body)
    store = StateStore(tmp_path / "state")
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert store.load() is None             # NEVER raises (contract)
    assert any("utf-8" in r.message.lower() for r in caplog.records)
    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    assert daemon.restore() is False            # fresh start, no crash
    assert notifier.sent == []                  # restore itself sends NOTHING
    daemon.run_cycle()                          # re-alerts once (safe polarity)
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]
    daemon.run_cycle()                          # unchanged world
    assert len(notifier.sent) == 1              # no re-fire, no crash loop


TYPE_POISON = [
    ("alert_records-int", {"schema_version": SCHEMA_VERSION,
                           "alert_records": 7}),
    ("alert_records-true", {"schema_version": SCHEMA_VERSION,
                            "alert_records": True}),
    ("notified_ledger-float", {"schema_version": SCHEMA_VERSION,
                               "notified_ledger": 3.5}),
]


@pytest.mark.parametrize("label,payload", TYPE_POISON,
                         ids=[c[0] for c in TYPE_POISON])
def test_f1b_type_corrupt_scalar_field_loads_empty_loudly(
        tmp_path, caplog, label, payload):
    """F1b regression pin: a schema-valid file whose alert_records /
    notified_ledger is a JSON scalar no longer raises TypeError out of
    load(): loud ERROR, container treated EMPTY, pair re-tracks + re-alerts."""
    store_dir = tmp_path / "state"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / STATE_FILENAME).write_text(json.dumps(payload),
                                            encoding="utf-8")
    store = StateStore(store_dir)
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        loaded = store.load()                   # NEVER raises (contract)
    assert loaded is not None                   # file is structurally valid
    assert loaded["alert_records"] == {}        # corrupt container -> empty
    assert loaded["notified_ledger"] == {}
    assert any("not a list" in r.message for r in caplog.records)
    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    daemon.restore()                            # no crash, however it lands
    assert notifier.sent == []
    daemon.run_cycle()                          # pair re-tracks + re-alerts
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]


def test_load_never_raises_last_resort_net(tmp_path, caplog, monkeypatch):
    """The last-resort guard: even an UNEXPECTED exception inside the load
    body (simulated decoder regression in a spot no targeted guard covers)
    becomes a loud ERROR + None, never a raise — a supervised daemon must
    not crash-loop on a state file."""
    _seed_valid_state(tmp_path)

    def exploding(self, payload, field):
        raise RuntimeError("simulated decoder regression")

    monkeypatch.setattr(StateStore, "_list_field", exploding)
    store = StateStore(tmp_path / "state")
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert store.load() is None             # net catches, never raises
    assert any("unexpected" in r.message.lower() for r in caplog.records)


def test_watch_restore_guards_even_a_raising_store(tmp_path, caplog):
    """Defense-in-depth (F1): _WatchDaemon.restore() survives a store whose
    load() raises (the never-raise contract regressing must still not kill
    the daemon at startup) — loud log, fresh start, alert loop lives."""
    daemon, _, _, notifier, _ = make_daemon(tmp_path)

    class RaisingStore:
        path = tmp_path / "state" / STATE_FILENAME

        def load(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

        def save(self, *a, **kw):
            return str(self.path)

    daemon._store = RaisingStore()
    with caplog.at_level(logging.ERROR, logger="wgresponse.cli"):
        assert daemon.restore() is False        # guarded, no raise
    assert any("load raised unexpectedly" in r.message for r in caplog.records)
    daemon.run_cycle()                          # daemon proceeds + re-alerts
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]


def test_save_tmp_file_never_lingers_and_reload_roundtrips(tmp_path):
    """save() fsync+replace hygiene: no .tmp leftovers, and the written file
    round-trips through load() (the fsync must not change the payload)."""
    daemon, _, engine, _, store = make_daemon(tmp_path)
    daemon.run_cycle()
    leftovers = [p for p in os.listdir(tmp_path / "state")
                 if p.endswith(".tmp")]
    assert leftovers == []
    loaded = StateStore(tmp_path / "state").load()
    assert loaded is not None
    assert set(loaded["alert_records"]) == set(engine.states())
    assert loaded["notified_ledger"] == engine.notified_ledger()


# ---------------------------------------------------------------------------
# ledger poisoning (brief 1b) — includes the F4-fix polarity flip
# ---------------------------------------------------------------------------

def _write_state(tmp_path, records, ledger, growth=None):
    store = StateStore(tmp_path / "state")
    store.save(records, ledger, None, {"poisoned": True},
               incident_growth=growth or {})
    return store


def test_ledger_poisoned_ahead_of_records_new_transition_not_swallowed(
        tmp_path):
    """Ledger claims the EXTREME send already happened while the records are
    older (WATCH). The alert RECORDS are authoritative for transition gating:
    the WATCH->EXTREME transition must still fire and notify (re-notify-ward
    beats a poisoned ledger), exactly once — then never again while the world
    is unchanged (no dup sends)."""
    rec = AlertRecord(client_id="c1", incident_id="inc-1",
                      state=AlertState.WATCH, entered_at_s=T0 - 2 * H,
                      last_eval_s=T0 - H, clear_streak=0, blind_cycles=0,
                      history=[(T0 - 2 * H, AlertState.NOTICE_72H),
                               (T0 - H, AlertState.WATCH)])
    poisoned_ledger = {
        ("c1", "inc-1", "notice_72h"): T0 - 2 * H,
        ("c1", "inc-1", "extreme_24h"): T0 - 0.5 * H,   # LIE: never at EXTREME
        ("c1", "inc-1", "watch"): T0 - H,               # watch itself WAS sent
    }
    _write_state(tmp_path, {("c1", "inc-1"): rec}, poisoned_ledger)

    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    assert daemon.restore() is True
    assert engine.states()[("c1", "inc-1")].state is AlertState.WATCH
    daemon.run_cycle()                          # hot world: p10 ~hours out
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    sends = [e for e, _ in notifier.sent]
    assert len(sends) == 1                      # NEW transition NOT swallowed
    assert sends[0].state is AlertState.EXTREME_24H
    daemon.run_cycle()                          # unchanged world
    daemon.run_cycle()
    assert len(notifier.sent) == 1              # no dup sends


def test_records_extreme_ledger_empty_unchanged_world_refires_once(tmp_path):
    """Reverse poisoning — polarity FLIPPED by the F4 fix (restored-unsent
    reconcile): records already EXTREME, ledger lost/empty. The restored
    occupancy has no ledgered send, so the engine re-fires the EXTREME
    message EXACTLY ONCE on the next cycle (re-notify-ward: a duplicate
    beats silence), ledgers it, and never fires again."""
    rec = AlertRecord(client_id="c1", incident_id="inc-1",
                      state=AlertState.EXTREME_24H, entered_at_s=T0 - H,
                      last_eval_s=T0 - H, clear_streak=0, blind_cycles=0,
                      history=[(T0 - H, AlertState.EXTREME_24H)])
    _write_state(tmp_path, {("c1", "inc-1"): rec}, {})
    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    assert daemon.restore() is True
    daemon.run_cycle()
    daemon.run_cycle()
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    assert [e.state for e, _ in notifier.sent] == [AlertState.EXTREME_24H]
    assert ("c1", "inc-1", "extreme_24h") in engine.notified_ledger()


def test_ledger_only_state_no_records_re_alerts_once(tmp_path):
    """State file carries ONLY ledger entries (records list lost): the pair
    re-tracks fresh and re-alerts once — re-notify-ward, never silence-ward."""
    _write_state(tmp_path, {}, {("c1", "inc-1", "extreme_24h"): T0 - H})
    daemon, _, engine, notifier, _ = make_daemon(tmp_path)
    assert daemon.restore() is True
    assert engine.states() == {}
    daemon.run_cycle()
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    assert len(notifier.sent) == 1              # re-alert happened (safe)
    daemon.run_cycle()
    assert len(notifier.sent) == 1              # exactly once


# ---------------------------------------------------------------------------
# restore -> immediately blind cycle: frozen, blind_cycles CONTINUES
# ---------------------------------------------------------------------------

def test_restore_then_blind_cycle_freezes_and_continues_blind_count(tmp_path):
    # Process 1: escalate, then two dead-feed cycles -> blind_cycles=2.
    daemon1, feed1, engine1, notifier1, store = make_daemon(tmp_path)
    daemon1.run_cycle()
    assert engine1.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    feed1.ok = False
    daemon1.run_cycle()
    daemon1.run_cycle()
    rec1 = engine1.states()[("c1", "inc-1")]
    assert rec1.blind_cycles == 2
    assert rec1.state is AlertState.EXTREME_24H
    sends_before = len(notifier1.sent)

    # Process 2: restore into a STILL-DEAD feed; first cycle is blind.
    daemon2, feed2, engine2, notifier2, _ = make_daemon(
        tmp_path, clock=FakeClock(T0 + 600.0))
    feed2.ok = False
    assert daemon2.restore() is True
    assert engine2.states()[("c1", "inc-1")].blind_cycles == 2   # restored
    res = daemon2.run_cycle()
    rec2 = engine2.states()[("c1", "inc-1")]
    assert rec2.state is AlertState.EXTREME_24H                  # FROZEN
    assert rec2.blind_cycles == 3               # CONTINUES, not reset to 1
    assert rec2.clear_streak == 0
    assert notifier2.sent == []                 # no client sends while blind
    assert res.degraded is True
    kinds = {e.kind for e in res.operator_events}
    assert "feed_blind" in kinds and "degraded" in kinds

    # Feed recovers with the fire still hot: recovered event, state resumes
    # EXTREME with NO re-send (it never left EXTREME and its send was
    # ledgered pre-restart).
    feed2.ok = True
    res2 = daemon2.run_cycle()
    assert engine2.states()[("c1", "inc-1")].blind_cycles == 0
    assert any(e.kind == "recovered" for e in res2.operator_events)
    assert notifier2.sent == []
    assert sends_before == len(notifier1.sent)  # process 1 sent nothing extra


# ---------------------------------------------------------------------------
# IMPACTED persisted terminal — never re-fired, not even by the F4 reconcile
# ---------------------------------------------------------------------------

def test_impacted_restored_pair_is_terminal_under_hot_assessment(tmp_path):
    """IMPACTED stays terminal + silent across restarts EVEN with no ledger
    entry for the impacted send (the F4 restored-unsent reconcile re-fires
    ACTIVE threat states only — a terminal pair never notifies again)."""
    rec = AlertRecord(client_id="c1", incident_id="inc-1",
                      state=AlertState.IMPACTED, entered_at_s=T0 - 2 * H,
                      last_eval_s=T0 - 2 * H, clear_streak=0, blind_cycles=0,
                      history=[(T0 - 3 * H, AlertState.EXTREME_24H),
                               (T0 - 2 * H, AlertState.IMPACTED)])
    _write_state(tmp_path, {("c1", "inc-1"): rec},
                 {("c1", "inc-1", "extreme_24h"): T0 - 3 * H})
    daemon, feed, engine, notifier, store = make_daemon(tmp_path)
    assert daemon.restore() is True

    daemon.run_cycle()                          # hot assessment
    feed.incidents = [incident(updated_at_s=T0 + 3600.0)]
    daemon.run_cycle()                          # receding fire
    feed.incidents = []
    daemon.run_cycle()                          # vanished incident
    rec_after = engine.states()[("c1", "inc-1")]
    assert rec_after.state is AlertState.IMPACTED
    assert [s for _, s in rec_after.history][-1] is AlertState.IMPACTED
    assert all(e.state is not AlertState.CLEARED for e, _ in notifier.sent)
    assert all(e.incident_id != "inc-1" or e.client_id != "c1"
               for e, _ in notifier.sent), "IMPACTED pair must stay silent"

    # And it round-trips terminal through ANOTHER restart.
    daemon2, _, engine2, notifier2, _ = make_daemon(
        tmp_path, clock=FakeClock(T0 + 600.0))
    assert daemon2.restore() is True
    assert engine2.states()[("c1", "inc-1")].state is AlertState.IMPACTED
    daemon2.run_cycle()
    assert engine2.states()[("c1", "inc-1")].state is AlertState.IMPACTED
    assert notifier2.sent == []
