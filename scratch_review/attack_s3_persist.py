"""ADVERSARIAL VERIFIER (Session 3) — attack #1: persistence.

Claims under attack (verifier brief #1):
  1a. Corrupt/truncated/empty state file -> daemon-equivalent restore starts
      FRESH with a loud note, NEVER crashes, and restore itself never sends.
      (A fresh start after corruption re-alerts by design — persist.py:
      "re-alerts rather than silences" — so the never-re-fires guarantee is
      pinned only for INTACT state files, per the restart drill.)
  1b. Ledger poisoning: ledger says sent while the alert records are older
      (or missing) -> no swallowed NEW transitions AND no duplicate sends
      for an unchanged world.
  1c. Restore then an immediately-blind cycle -> pair FREEZES with
      blind_cycles CONTINUING from the persisted count (not reset).
  1d. IMPACTED persisted terminal: restored IMPACTED pair never transitions
      or notifies again, even under a hot assessment.

FINDINGS documented in this file (see test names marked FINDING):
  F1  [FIXED by the Session-3 fixer; tests below assert the FIXED behavior
      and are promoted to tests/test_attack_s3_persist.py]
      persist.StateStore.load() RAISED (contract: "load() NEVER raises") and
      _WatchDaemon.restore() called it unguarded -> `wgresponse watch`
      CRASHED AT STARTUP, on two corruption classes:
      (a) NON-UTF-8 BYTES in the state file: read_text(encoding='utf-8')
          raised UnicodeDecodeError (a ValueError — NOT caught by the
          FileNotFoundError/OSError handlers around the read). Realistic:
          save() never fsynced before os.replace(), so a power loss could
          durably rename a tmp file whose data blocks were never flushed.
          FIX: UnicodeDecodeError branch + a last-resort never-raise net in
          load(), fh.flush()+os.fsync() before os.replace() in save(), and a
          defense-in-depth guard around the load() call in
          _WatchDaemon.restore().
      (b) schema-valid JSON whose "alert_records"/"notified_ledger" is a
          non-iterable scalar (7 / 3.5 / true): enumerate(<scalar>) raised
          TypeError past the per-record guards. FIX: non-list containers are
          a loud ERROR + treated EMPTY (affected pairs re-track fresh —
          re-alerts rather than silences).
      All-zeros / truncated / non-JSON text corruption was already handled
      (loud note + fresh start).
  F4-adjacent flip: records-EXTREME + empty-ledger restore now RE-FIRES the
      unsent state once (see attack_s3_watch.py F4 — the ledger reconcile).

Run from repo root:
    py -3 -m pytest scratch_review/attack_s3_persist.py -v
"""
from __future__ import annotations

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_s3_common import (  # noqa: E402
    H, T0, incident, make_daemon, small_cfg,
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
# 1a. corrupt / truncated / empty state files
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
    # loud note (ERROR for corruption; the empty-file case decodes as corrupt
    # JSON -> ERROR too)
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
# FINDING F1 — corrupt state files that make load() raise -> daemon crash
# ---------------------------------------------------------------------------

BINARY_POISON = [
    ("invalid-utf8-garbage", b"\x00\xff\x13PK\x03\x04 stale sectors"),
    ("lone-continuation-byte", b'{"schema_version": 1}\x80'),
    ("utf16-encoded-json", '{"schema_version": 1}'.encode("utf-16")),
]


@pytest.mark.parametrize("label,body", BINARY_POISON,
                         ids=[c[0] for c in BINARY_POISON])
def test_FINDING_f1a_non_utf8_state_file_loads_fresh_loudly(tmp_path, caplog,
                                                            label, body):
    """FINDING F1a — FIXED: a state file containing invalid-UTF-8 bytes
    (post-power-loss stale-sector garbage) now loads as None with a loud
    ERROR; the daemon starts FRESH instead of crashing at startup, and the
    next cycle re-alerts exactly once (re-notify-ward)."""
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
def test_FINDING_f1b_type_corrupt_scalar_field_loads_empty_loudly(
        tmp_path, caplog, label, payload):
    """FINDING F1b — FIXED: a schema-valid file whose alert_records /
    notified_ledger is a JSON scalar (int/float/bool) no longer raises
    TypeError out of load(): the non-list container is a loud ERROR treated
    as EMPTY, the daemon starts, and the affected pairs re-track fresh
    (re-alerts rather than silences)."""
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


# ---------------------------------------------------------------------------
# 1b. ledger poisoning
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
    """Reverse poisoning — FLIPPED by the F4 fix (restored-unsent reconcile):
    records already EXTREME, ledger lost/empty. The restored occupancy has no
    ledgered send, so the engine re-fires the EXTREME message EXACTLY ONCE on
    the next cycle (re-notify-ward: a duplicate beats silence), ledgers it,
    and never fires again for the unchanged world."""
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
# 1c. restore -> immediately blind cycle: frozen, blind_cycles CONTINUES
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
    daemon2, feed2, engine2, notifier2, _ = make_daemon(tmp_path)
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
    (deg,) = [e for e in res.operator_events if e.kind == "degraded"]
    assert deg.detail["blind_cycles"] == 3

    # Feed recovers with the fire still hot: recovered event, state resumes
    # EXTREME with NO re-send (it never left EXTREME).
    feed2.ok = True
    res2 = daemon2.run_cycle()
    assert engine2.states()[("c1", "inc-1")].blind_cycles == 0
    assert any(e.kind == "recovered" for e in res2.operator_events)
    assert notifier2.sent == []
    assert sends_before == len(notifier1.sent)  # process 1 sent nothing extra


# ---------------------------------------------------------------------------
# 1d. IMPACTED persisted terminal
# ---------------------------------------------------------------------------

def test_impacted_restored_pair_is_terminal_under_hot_assessment(tmp_path):
    rec = AlertRecord(client_id="c1", incident_id="inc-1",
                      state=AlertState.IMPACTED, entered_at_s=T0 - 2 * H,
                      last_eval_s=T0 - 2 * H, clear_streak=0, blind_cycles=0,
                      history=[(T0 - 3 * H, AlertState.EXTREME_24H),
                               (T0 - 2 * H, AlertState.IMPACTED)])
    _write_state(tmp_path, {("c1", "inc-1"): rec},
                 {("c1", "inc-1", "extreme_24h"): T0 - 3 * H})
    daemon, feed, engine, notifier, store = make_daemon(tmp_path)
    assert daemon.restore() is True

    # Hot assessment (finite ETA hours out), then a receding fire, then a
    # vanished incident: IMPACTED never moves, never notifies.
    daemon.run_cycle()
    feed.incidents = [incident(updated_at_s=T0 + 3600.0)]
    daemon.run_cycle()
    feed.incidents = []
    daemon.run_cycle()
    rec_after = engine.states()[("c1", "inc-1")]
    assert rec_after.state is AlertState.IMPACTED
    assert [s for _, s in rec_after.history][-1] is AlertState.IMPACTED
    assert all(e.state is not AlertState.CLEARED for e, _ in notifier.sent)
    assert all(e.incident_id != "inc-1" or e.client_id != "c1"
               for e, _ in notifier.sent), "IMPACTED pair must stay silent"

    # And it round-trips terminal through ANOTHER restart.
    daemon2, _, engine2, notifier2, _ = make_daemon(tmp_path)
    assert daemon2.restore() is True
    assert engine2.states()[("c1", "inc-1")].state is AlertState.IMPACTED
    daemon2.run_cycle()
    assert engine2.states()[("c1", "inc-1")].state is AlertState.IMPACTED
    assert notifier2.sent == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
