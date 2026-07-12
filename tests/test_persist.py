"""Tests for wgresponse.persist — StateStore + the restart drill (ticket C2).

The load-bearing guarantee (contracts persist seam, Council-1 OPS-4): alert /
IMPACTED / notification state survives a daemon restart and the restarted
process RE-FIRES NOTHING for an unchanged world — while a genuinely NEW
transition still notifies. Plus durability mechanics: atomic tmp+rename
writes, versioned schema, and a load that NEVER raises (absence/corruption =
loud note + start fresh; a single corrupt record is skipped, the rest
restore). No network, no third-party deps; deterministic.
"""
from __future__ import annotations

import json
import logging
import os

import pytest

from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    AlertRecord,
    AlertState,
    ClientProperty,
    CrewPlan,
    DispatchPlan,
    LatLon,
    PlannedStop,
    ResponseConfig,
    ThreatAssessment,
    ThreatEta,
    UnservedJob,
)
from wgresponse.notify import LogNotifier
from wgresponse.persist import SCHEMA_VERSION, STATE_FILENAME, StateStore

H = 3600.0
T0 = 1_780_000_000.0


def assess(cid, iid, now_s, p50_h, p10_h=None, p90_h=None):
    """ThreatAssessment with ETAs `h` hours after now_s (negative = passed)."""
    if p10_h is None:
        p10_h = p50_h * 0.7
    if p90_h is None:
        p90_h = p50_h * 1.3
    eta = ThreatEta(eta_p10_s=now_s + p10_h * H, eta_p50_s=now_s + p50_h * H,
                    eta_p90_s=now_s + p90_h * H)
    return ThreatAssessment(client_id=cid, incident_id=iid, eta=eta,
                            computed_at_s=now_s)


def sample_plan() -> DispatchPlan:
    return DispatchPlan(
        computed_at_s=T0,
        plans={"G1": CrewPlan(
            crew_id="G1",
            stops=[PlannedStop(client_id="c1", incident_id="f1",
                               arrive_s=T0 + 100.0, service_start_s=T0 + 150.0,
                               depart_s=T0 + 1950.0, latest_depart_s=T0 + 5000.0,
                               margin_h=0.847, gel_fresh_frac=0.93)],
            egress_zone_id="Z1", committed_until_s=T0 + 1200.0)},
        unserved=[UnservedJob(client_id="c9", incident_id="f1",
                              reason="infeasible_slack")],
        objective=1234.5,
        objective_breakdown={"seed": "greedy-edf", "value": 1200.0},
        churn_from_incumbent=0.05)


def sample_records() -> dict:
    rec1 = AlertRecord(client_id="c1", incident_id="f1",
                       state=AlertState.EXTREME_24H, entered_at_s=T0,
                       last_eval_s=T0 + 300.0, clear_streak=0, blind_cycles=2,
                       history=[(T0 - 600.0, AlertState.NOTICE_72H),
                                (T0 - 300.0, AlertState.WATCH),
                                (T0, AlertState.EXTREME_24H)])
    rec2 = AlertRecord(client_id="c2", incident_id="f2",
                       state=AlertState.IMPACTED, entered_at_s=T0 + 60.0,
                       last_eval_s=T0 + 60.0, clear_streak=0, blind_cycles=0,
                       history=[(T0, AlertState.EXTREME_24H),
                                (T0 + 60.0, AlertState.IMPACTED)])
    return {("c1", "f1"): rec1, ("c2", "f2"): rec2}


def sample_ledger() -> dict:
    return {("c1", "f1", "extreme_24h"): T0,
            ("c1", "f1", "notice_72h"): T0 - 600.0,
            ("c2", "f2", "extreme_24h"): T0}


def sample_growth() -> dict:
    return {"f1": {"acreage": 812.0, "last_growth_s": T0 - 3600.0},
            "f2": {"acreage": None, "last_growth_s": T0}}


# ---------------------------------------------------------------------------
# durability mechanics
# ---------------------------------------------------------------------------

def test_save_is_atomic_and_versioned(tmp_path):
    store = StateStore(tmp_path / "state")
    path = store.save(sample_records(), sample_ledger(), sample_plan(),
                      {"aoi": {"west": -121.0}}, incident_growth=sample_growth())
    assert os.path.exists(path)
    assert os.path.basename(path) == STATE_FILENAME
    # tmp file must be gone (rename completed), payload is valid JSON.
    leftovers = [p for p in os.listdir(tmp_path / "state") if p.endswith(".tmp")]
    assert leftovers == []
    payload = json.loads((tmp_path / "state" / STATE_FILENAME)
                         .read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION


def test_round_trip_full_fidelity(tmp_path):
    store = StateStore(tmp_path)
    store.save(sample_records(), sample_ledger(), sample_plan(),
               {"aoi_desc": "bbox -121,39,-120,40", "started_at_s": T0},
               incident_growth=sample_growth())
    loaded = StateStore(tmp_path).load()
    assert loaded is not None
    # AlertRecords are dataclasses: equality covers every field incl. history
    # tuples/enums and blind_cycles.
    assert loaded["alert_records"] == sample_records()
    assert loaded["notified_ledger"] == sample_ledger()
    assert loaded["incumbent_plan"] == sample_plan()
    assert loaded["meta"]["aoi_desc"] == "bbox -121,39,-120,40"
    assert loaded["incident_growth"] == sample_growth()


def test_load_absent_file_returns_none(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="wgresponse.persist"):
        assert StateStore(tmp_path / "never_saved").load() is None
    assert any("starting fresh" in r.message.lower() for r in caplog.records)


def test_load_corrupt_json_returns_none_loudly(tmp_path, caplog):
    (tmp_path / STATE_FILENAME).write_text("{not json!!", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert StateStore(tmp_path).load() is None
    assert any("corrupt" in r.message.lower() for r in caplog.records)


def test_load_unknown_schema_returns_none_loudly(tmp_path, caplog):
    (tmp_path / STATE_FILENAME).write_text(
        json.dumps({"schema_version": 999, "alert_records": []}),
        encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert StateStore(tmp_path).load() is None
    assert any("schema_version" in r.message for r in caplog.records)


def test_load_skips_malformed_record_keeps_rest(tmp_path, caplog):
    store = StateStore(tmp_path)
    store.save(sample_records(), sample_ledger(), None, {})
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["alert_records"][0]["state"] = "not_a_state"   # corrupt ONE record
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        loaded = StateStore(tmp_path).load()
    assert loaded is not None
    assert len(loaded["alert_records"]) == 1               # the other survived
    assert any("skipping malformed alert record" in r.message
               for r in caplog.records)


def test_load_unreadable_path_returns_none(tmp_path, caplog):
    # A directory where the state FILE should be -> OSError on read; the
    # daemon must start fresh, never crash.
    (tmp_path / STATE_FILENAME).mkdir()
    with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
        assert StateStore(tmp_path).load() is None


def test_save_none_plan_and_empty_state_round_trips(tmp_path):
    store = StateStore(tmp_path)
    store.save({}, {}, None, {})
    loaded = store.load()
    assert loaded["alert_records"] == {}
    assert loaded["notified_ledger"] == {}
    assert loaded["incumbent_plan"] is None
    assert loaded["incident_growth"] == {}


# ---------------------------------------------------------------------------
# THE RESTART DRILL (ticket C2 acceptance): NOTICE+EXTREME sent -> persist ->
# process-equivalent restore -> next evaluate re-fires NOTHING; IMPACTED stays
# terminal; a NEW transition still notifies.
# ---------------------------------------------------------------------------

CLIENTS = {
    "c1": ClientProperty(client_id="c1", loc=LatLon(34.1, -118.2), name="Casa Uno"),
    "c2": ClientProperty(client_id="c2", loc=LatLon(34.2, -118.3), name="Casa Dos"),
    "c3": ClientProperty(client_id="c3", loc=LatLon(34.3, -118.4), name="Casa Tres"),
}


def _build_pre_restart_engine(cfg):
    """Engine with: (c1,f1) WATCH after its NOTICE send, (c2,f1) EXTREME sent,
    (c3,f2) IMPACTED (terminal). Returns (engine, notifier)."""
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier, clients_by_id=CLIENTS)
    # t0: c1 gets the one-time NOTICE; c2 jumps to EXTREME; c3 to EXTREME.
    eng.evaluate([assess("c1", "f1", T0, 54.0),
                  assess("c2", "f1", T0, 10.0, p10_h=8.0),
                  assess("c3", "f2", T0, 5.0, p10_h=4.0)], now_s=T0)
    # t1: c1 auto-progresses NOTICE->WATCH; c2 holds EXTREME; c3's median
    # arrival passes -> IMPACTED (terminal).
    eng.evaluate([assess("c1", "f1", T0 + 300.0, 53.9),
                  assess("c2", "f1", T0 + 300.0, 9.9, p10_h=7.9),
                  assess("c3", "f2", T0 + 300.0, -0.5, p10_h=-1.0)],
                 now_s=T0 + 300.0)
    states = {k: r.state for k, r in eng.states().items()}
    assert states == {("c1", "f1"): AlertState.WATCH,
                      ("c2", "f1"): AlertState.EXTREME_24H,
                      ("c3", "f2"): AlertState.IMPACTED}
    return eng, notifier


def _same_world_assessments(now_s):
    """The unchanged world at now_s: same fires, ETAs still inside the same
    ladder bands (WATCH band for c1, EXTREME for c2)."""
    return [assess("c1", "f1", now_s, 53.8),
            assess("c2", "f1", now_s, 9.8, p10_h=7.8)]


def test_restart_drill_refires_nothing_and_new_transition_still_notifies(tmp_path):
    cfg = ResponseConfig()
    eng1, notifier1 = _build_pre_restart_engine(cfg)
    sends_before = len(notifier1.sent)
    assert sends_before == 5   # NOTICE, EXTREME, EXTREME, WATCH, IMPACTED
    ledger_before = eng1.notified_ledger()
    assert set(ledger_before) == {
        ("c1", "f1", "notice_72h"), ("c1", "f1", "watch"),
        ("c2", "f1", "extreme_24h"),
        ("c3", "f2", "extreme_24h"), ("c3", "f2", "impacted"),
    }

    # -- persist ------------------------------------------------------------
    store = StateStore(tmp_path)
    store.save(eng1.states(), eng1.notified_ledger(), None,
               {"started_at_s": T0}, incident_growth=eng1.incident_growth())

    # -- process-equivalent restart ------------------------------------------
    loaded = StateStore(tmp_path).load()
    notifier2 = LogNotifier()
    eng2 = AlertEngine(cfg, notifier=notifier2, clients_by_id=CLIENTS)
    eng2.restore(loaded["alert_records"],
                 notified_ledger=loaded["notified_ledger"],
                 incident_growth=loaded["incident_growth"])
    assert eng2.notified_ledger() == ledger_before          # ledger respected

    # -- next evaluate on the UNCHANGED world: re-fires NOTHING ---------------
    t2 = T0 + 600.0
    events = eng2.evaluate(_same_world_assessments(t2), now_s=t2)
    assert events == []                                     # zero transitions
    assert notifier2.sent == []                             # zero sends
    assert eng2.notified_ledger() == ledger_before          # ledger unchanged
    states = {k: r.state for k, r in eng2.states().items()}
    assert states[("c1", "f1")] is AlertState.WATCH
    assert states[("c2", "f1")] is AlertState.EXTREME_24H

    # -- IMPACTED stays terminal even if the fire "re-threatens" -------------
    t3 = T0 + 900.0
    events = eng2.evaluate(_same_world_assessments(t3)
                           + [assess("c3", "f2", t3, 10.0)], now_s=t3)
    assert events == []
    assert eng2.states()[("c3", "f2")].state is AlertState.IMPACTED
    assert notifier2.sent == []

    # -- a NEW transition still notifies (and extends the ledger) ------------
    t4 = T0 + 1200.0
    events = eng2.evaluate(_same_world_assessments(t4)
                           + [assess("c9", "f3", t4, 10.0, p10_h=8.0)],
                           now_s=t4)
    assert [(e.client_id, e.state) for e in events] == [
        ("c9", AlertState.EXTREME_24H)]
    assert len(notifier2.sent) == 1
    assert ("c9", "f3", "extreme_24h") in eng2.notified_ledger()
    # The pre-restart entries are still intact.
    for key in ledger_before:
        assert key in eng2.notified_ledger()


def test_restart_drill_restored_records_preserve_blind_and_history(tmp_path):
    cfg = ResponseConfig()
    eng1, _ = _build_pre_restart_engine(cfg)
    # Freeze c2 blind for two cycles pre-restart (outage mid-flight).
    eng1.evaluate([assess("c1", "f1", T0 + 600.0, 53.7)], now_s=T0 + 600.0,
                  blind_pairs={("c2", "f1"), ("c3", "f2")})
    eng1.evaluate([assess("c1", "f1", T0 + 900.0, 53.6)], now_s=T0 + 900.0,
                  blind_pairs={("c2", "f1"), ("c3", "f2")})
    assert eng1.states()[("c2", "f1")].blind_cycles == 2

    store = StateStore(tmp_path)
    store.save(eng1.states(), eng1.notified_ledger(), None, {})
    loaded = store.load()
    eng2 = AlertEngine(cfg, notifier=LogNotifier(), clients_by_id=CLIENTS)
    eng2.restore(loaded["alert_records"],
                 notified_ledger=loaded["notified_ledger"])
    rec = eng2.states()[("c2", "f1")]
    assert rec.blind_cycles == 2                        # outage state survives
    assert [s for _, s in rec.history] == [AlertState.EXTREME_24H]
    rec1 = eng2.states()[("c1", "f1")]
    assert [s for _, s in rec1.history] == [AlertState.NOTICE_72H,
                                            AlertState.WATCH]


def test_restore_resets_deescalation_streak_conservatively():
    """The EXTREME->WATCH de-escalation streak is not persisted: a restored
    EXTREME pair must need the FULL clear_cycles of fresh de-esc evaluations
    (restart can only delay a downgrade, never accelerate one)."""
    cfg = ResponseConfig()   # clear_cycles=2
    eng1 = AlertEngine(cfg)
    eng1.evaluate([assess("c1", "f1", T0, 10.0, p10_h=8.0)], now_s=T0)
    # One clean de-esc evaluation pre-restart (streak 1 of 2, engine-side).
    eng1.evaluate([assess("c1", "f1", T0 + 300.0, 40.0, p10_h=35.0)],
                  now_s=T0 + 300.0)
    assert eng1.states()[("c1", "f1")].state is AlertState.EXTREME_24H

    eng2 = AlertEngine(cfg)
    eng2.restore(eng1.states())
    # First post-restore clean eval: streak restarts at 0 -> no downgrade yet.
    ev = eng2.evaluate([assess("c1", "f1", T0 + 600.0, 40.0, p10_h=35.0)],
                       now_s=T0 + 600.0)
    assert ev == []
    assert eng2.states()[("c1", "f1")].state is AlertState.EXTREME_24H
    # Second: the full fresh streak is met -> WATCH.
    ev = eng2.evaluate([assess("c1", "f1", T0 + 900.0, 40.0, p10_h=35.0)],
                       now_s=T0 + 900.0)
    assert [e.state for e in ev] == [AlertState.WATCH]


def test_restore_skips_garbage_entries_loudly(caplog):
    eng = AlertEngine(ResponseConfig())
    with caplog.at_level(logging.ERROR, logger="wgresponse.alerts"):
        eng.restore({("c1", "f1"): {"not": "a record"},
                     ("c2", "f2"): AlertRecord(client_id="c2",
                                               incident_id="f2",
                                               state=AlertState.WATCH)})
    assert set(eng.states()) == {("c2", "f2")}
    assert any("skipping non-AlertRecord" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# containment-gate growth clock survives restart (ticket D2: "persist it")
# ---------------------------------------------------------------------------

def test_growth_clock_survives_restart_so_cap_does_not_rewind(tmp_path):
    """A restart must not reset the containment no-growth clock: pre-restart
    the incident had 100 h of tracked no-growth (capped); a fresh engine
    WITHOUT the restored clock would treat the incident as first-sight (not
    capped) and escalate to EXTREME."""
    cfg = ResponseConfig(containment_gate_enabled=True)
    meta = {"f1": {"name": "Volcano", "containment_pct": 95.0,
                   "acreage": 800.0}}
    eng1 = AlertEngine(cfg)
    # Track the incident far in the past: the no-growth clock starts here.
    eng1.evaluate([], now_s=T0 - 100 * H, incident_meta=meta)
    store = StateStore(tmp_path)
    store.save(eng1.states(), eng1.notified_ledger(), None, {},
               incident_growth=eng1.incident_growth())
    loaded = store.load()

    restored = AlertEngine(cfg)
    restored.restore(loaded["alert_records"],
                     notified_ledger=loaded["notified_ledger"],
                     incident_growth=loaded["incident_growth"])
    ev = restored.evaluate([assess("c1", "f1", T0, 10.0, p10_h=8.0)],
                           now_s=T0, incident_meta=meta)
    # Cap engaged straight after restart (no 48 h re-wait): NOTICE, not EXTREME.
    assert [e.state for e in ev] == [AlertState.NOTICE_72H]

    fresh = AlertEngine(cfg)   # control: no restored clock -> first sight
    ev = fresh.evaluate([assess("c1", "f1", T0, 10.0, p10_h=8.0)],
                        now_s=T0, incident_meta=meta)
    assert [e.state for e in ev] == [AlertState.EXTREME_24H]
