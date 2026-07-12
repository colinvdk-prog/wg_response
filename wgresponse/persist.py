"""wgresponse.persist — StateStore: alert/notification/plan state across restarts.

Contracts seam (Sprint 2, Council-1 OPS-4):

    class StateStore(dir_path)  .save(alert_records, notified_ledger,
                                      incumbent_plan, meta)
                                .load() -> restored dicts (or None when
                                           absent/corrupt: loud note)

Alert/IMPACTED/notification state survives restart; restart re-fires NOTHING
that was successfully sent: the watch daemon persists after every cycle,
restores on startup via AlertEngine.restore(records, notified_ledger=...,
incident_growth=...) + ThreatMonitor.restore_incumbent(plan), and the next
evaluate() sees the exact pre-restart ladder states and send ledger, so no
transition (and therefore no notification) re-fires for an unchanged world
(tests/test_persist.py restart drill). The one deliberate exception errs the
SAFE way (Session-3 verifier F4): a restored pair holding an ACTIVE threat
state whose send was never ledgered (shutdown landed mid-notification) is
re-fired by the engine — a duplicate beats a permanently swallowed alert.

Durability discipline:
  * ATOMIC WRITE — the JSON is written to `<file>.tmp`, FLUSHED AND FSYNCED
    (Session-3 verifier F1: without the fsync a power loss could durably
    rename a tmp file whose data blocks were never written — stale-sector
    garbage under a valid name), then os.replace()d over the state file, so
    a crash mid-write can never leave a torn/half state file (the previous
    complete state survives).
  * VERSIONED SCHEMA — every file carries {"schema_version": N}; load()
    refuses (loudly, returning None) anything it does not understand rather
    than guessing at field meanings.
  * TOLERANT LOAD — a missing file/dir means "first run" (info note, start
    fresh). An unreadable/corrupt/mis-versioned file is a loud ERROR log +
    None (start fresh) — the daemon must NEVER crash on a bad state file.
    That includes non-UTF-8 bytes (post-power-loss sector garbage) and
    schema-valid files whose alert_records/notified_ledger container is a
    non-list scalar (Session-3 verifier F1a/F1b); a last-resort guard turns
    ANY unexpected decode failure into a loud ERROR + None. Within a
    structurally-valid file, an individually malformed alert record
    / ledger entry is SKIPPED with a loud note while the rest restore: losing
    one record to corruption must not throw away every other pair's state.
    (Losing a record errs re-notify-ward, never silence-ward: a re-tracked
    pair re-alerts, which is the safe failure direction.)
  * NO WALL-CLOCK READS — persist stamps nothing itself; every timestamp in
    the payload comes from the caller (daemon clock / event times), keeping
    the module deterministic under injected clocks.

Payload shapes (all times unix seconds, per contracts):
  alert_records   {(client_id, incident_id): contracts.AlertRecord}
  notified_ledger {(client_id, incident_id, state_value): t_s} — what was
                  ACTUALLY sent (AlertEngine.notified_ledger()).
  incumbent_plan  contracts.DispatchPlan | None (warm-start seed).
  meta            JSON-able dict (watch metadata: aoi, started_at, ...).
  incident_growth {incident_id: {"acreage": float|None, "last_growth_s": s}}
                  — the containment-gate no-growth clock (SAFETY SEMANTICS 8;
                  additive kwarg, engine seam AlertEngine.incident_growth()).

Stdlib only.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from .contracts import (
    AlertRecord,
    AlertState,
    CrewPlan,
    DispatchPlan,
    PlannedStop,
    UnservedJob,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STATE_FILENAME = "wgresponse_state.json"

_PairKey = Tuple[str, str]
_LedgerKey = Tuple[str, str, str]


# ---------------------------------------------------------------------------
# encoding / decoding (explicit field maps — no pickle, no eval)
# ---------------------------------------------------------------------------

def _encode_record(rec: AlertRecord) -> dict:
    return {
        "client_id": rec.client_id,
        "incident_id": rec.incident_id,
        "state": rec.state.value,
        "entered_at_s": float(rec.entered_at_s),
        "last_eval_s": float(rec.last_eval_s),
        "clear_streak": int(rec.clear_streak),
        "blind_cycles": int(rec.blind_cycles),
        "history": [[float(t), s.value if isinstance(s, AlertState) else str(s)]
                    for (t, s) in rec.history],
    }


def _decode_record(d: dict) -> AlertRecord:
    """Rebuild one AlertRecord; raises on any malformed field (the caller
    skips-and-notes per record — see module docstring)."""
    return AlertRecord(
        client_id=str(d["client_id"]),
        incident_id=str(d["incident_id"]),
        state=AlertState(d["state"]),
        entered_at_s=float(d["entered_at_s"]),
        last_eval_s=float(d["last_eval_s"]),
        clear_streak=int(d["clear_streak"]),
        blind_cycles=int(d["blind_cycles"]),
        history=[(float(t), AlertState(s)) for (t, s) in d.get("history", [])],
    )


def _encode_stop(st: PlannedStop) -> dict:
    return {
        "client_id": st.client_id, "incident_id": st.incident_id,
        "arrive_s": st.arrive_s, "service_start_s": st.service_start_s,
        "depart_s": st.depart_s, "latest_depart_s": st.latest_depart_s,
        "margin_h": st.margin_h, "gel_fresh_frac": st.gel_fresh_frac,
    }


def _decode_stop(d: dict) -> PlannedStop:
    return PlannedStop(
        client_id=str(d["client_id"]), incident_id=str(d["incident_id"]),
        arrive_s=float(d["arrive_s"]),
        service_start_s=float(d["service_start_s"]),
        depart_s=float(d["depart_s"]),
        latest_depart_s=float(d["latest_depart_s"]),
        margin_h=float(d["margin_h"]),
        gel_fresh_frac=float(d.get("gel_fresh_frac", 1.0)),
    )


def _encode_plan(plan: Optional[DispatchPlan]) -> Optional[dict]:
    if plan is None:
        return None
    return {
        "computed_at_s": plan.computed_at_s,
        "plans": {
            crew_id: {
                "crew_id": cp.crew_id,
                "stops": [_encode_stop(st) for st in cp.stops],
                "egress_zone_id": cp.egress_zone_id,
                "committed_until_s": cp.committed_until_s,
            }
            for crew_id, cp in plan.plans.items()
        },
        "unserved": [{"client_id": uj.client_id, "incident_id": uj.incident_id,
                      "reason": uj.reason} for uj in plan.unserved],
        "objective": plan.objective,
        "objective_breakdown": dict(plan.objective_breakdown),
        "churn_from_incumbent": plan.churn_from_incumbent,
    }


def _decode_plan(d: Optional[dict]) -> Optional[DispatchPlan]:
    if d is None:
        return None
    return DispatchPlan(
        computed_at_s=float(d["computed_at_s"]),
        plans={
            str(crew_id): CrewPlan(
                crew_id=str(cp["crew_id"]),
                stops=[_decode_stop(st) for st in cp.get("stops", [])],
                egress_zone_id=str(cp.get("egress_zone_id", "")),
                committed_until_s=float(cp.get("committed_until_s", 0.0)),
            )
            for crew_id, cp in (d.get("plans") or {}).items()
        },
        unserved=[UnservedJob(client_id=str(u["client_id"]),
                              incident_id=str(u["incident_id"]),
                              reason=str(u["reason"]))
                  for u in d.get("unserved", [])],
        objective=float(d.get("objective", 0.0)),
        objective_breakdown=dict(d.get("objective_breakdown") or {}),
        churn_from_incumbent=float(d.get("churn_from_incumbent", 0.0)),
    )


def _encode_growth(growth: Optional[dict]) -> dict:
    out = {}
    for iid, g in (growth or {}).items():
        last = g.get("last_growth_s") if isinstance(g, dict) else None
        if not isinstance(last, (int, float)):
            # Never fabricate a no-growth clock (a fake ancient last_growth_s
            # could ENGAGE the containment cap): skip the entry — the engine
            # re-baselines it at next sight, which only DELAYS the gate.
            logger.error("persist: dropping malformed growth entry %r "
                         "(no last_growth_s); it re-baselines on next sight",
                         iid)
            continue
        acr = g.get("acreage")
        out[str(iid)] = {
            "acreage": float(acr) if isinstance(acr, (int, float)) else None,
            "last_growth_s": float(last),
        }
    return out


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------

class StateStore:
    """Disk-JSON persistence for the watch daemon (contracts persist seam).

    One state file per directory (`STATE_FILENAME`); the directory is created
    on first save(). save() raises OSError on disk failure (the daemon guards
    and notes it — a full disk must not kill the alert loop); load() NEVER
    raises (absence/corruption -> loud note + None, start fresh).
    """

    def __init__(self, dir_path) -> None:
        self._dir = Path(dir_path)
        self._path = self._dir / STATE_FILENAME

    @property
    def path(self) -> Path:
        """The state file path (for logs/tests)."""
        return self._path

    # ------------------------------------------------------------------ save

    def save(self, alert_records: Dict[_PairKey, AlertRecord],
             notified_ledger: Dict[_LedgerKey, float],
             incumbent_plan: Optional[DispatchPlan],
             meta: Optional[dict],
             incident_growth: Optional[dict] = None) -> str:
        """Atomically persist one cycle's durable state; returns the file path.

        Tuple keys are flattened into lists (JSON has no tuple keys). Raises
        OSError/TypeError upward on disk/serialization failure — the caller
        decides whether that is fatal (the watch daemon logs + continues).
        """
        payload = {
            "schema_version": SCHEMA_VERSION,
            "alert_records": [_encode_record(rec)
                              for _, rec in sorted(alert_records.items())],
            "notified_ledger": [
                {"client_id": k[0], "incident_id": k[1], "state": k[2],
                 "t_s": float(t)}
                for k, t in sorted(notified_ledger.items())
            ],
            "incumbent_plan": _encode_plan(incumbent_plan),
            "meta": dict(meta or {}),
            "incident_growth": _encode_growth(incident_growth),
        }
        body = json.dumps(payload, sort_keys=True, indent=1)
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(self._path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            # DURABILITY (Session-3 verifier F1): force the tmp file's DATA to
            # disk before the rename. os.replace() alone only orders the
            # metadata — after a power loss the filesystem can replay the
            # rename while the data blocks were never written, leaving
            # stale-sector garbage under the state filename, exactly when the
            # daemon must come back up and alert.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)  # atomic on POSIX and Windows
        return str(self._path)

    # ------------------------------------------------------------------ load

    def load(self) -> Optional[dict]:
        """Restore the persisted state, or None (loud note) when absent/corrupt.

        Returns {"alert_records": {(cid, iid): AlertRecord},
                 "notified_ledger": {(cid, iid, state): t_s},
                 "incumbent_plan": DispatchPlan | None,
                 "meta": dict,
                 "incident_growth": {iid: {"acreage", "last_growth_s"}}}.
        NEVER raises: file absent -> info note; unreadable / non-UTF-8 /
        corrupt JSON / unknown schema / type-corrupt containers -> ERROR
        note; a malformed individual record/ledger entry is skipped with its
        own ERROR note while the rest restore. A last-resort guard converts
        ANY unexpected failure into a loud ERROR + None (Session-3 verifier
        F1: a supervised daemon must never crash-loop on a bad state file).
        """
        try:
            return self._load()
        except Exception:
            logger.exception(
                "persist: state file %s failed to load in an UNEXPECTED way "
                "— STARTING FRESH; previously-alerted pairs may re-notify "
                "(load() never raises)", self._path)
            return None

    def _load(self) -> Optional[dict]:
        """load() body; every known corruption class handled precisely here,
        the caller holds the never-raise last-resort net."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.info("persist: no state file at %s — starting fresh",
                        self._path)
            return None
        except UnicodeDecodeError as exc:
            # Session-3 verifier F1a: post-power-loss stale-sector garbage is
            # rarely valid UTF-8. UnicodeDecodeError is a ValueError, NOT an
            # OSError — without this branch it escaped load() and crashed the
            # watch daemon at startup.
            logger.error("persist: state file %s is not valid UTF-8 (%s) — "
                         "STARTING FRESH; previously-alerted pairs may "
                         "re-notify", self._path, exc)
            return None
        except OSError as exc:
            logger.error("persist: state file %s unreadable (%s) — STARTING "
                         "FRESH; previously-alerted pairs may re-notify",
                         self._path, exc)
            return None
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            logger.error("persist: state file %s is corrupt JSON (%s) — "
                         "STARTING FRESH; previously-alerted pairs may "
                         "re-notify", self._path, exc)
            return None
        if not isinstance(payload, dict) or \
                payload.get("schema_version") != SCHEMA_VERSION:
            logger.error(
                "persist: state file %s has unknown schema_version %r "
                "(this build reads %r) — STARTING FRESH",
                self._path,
                payload.get("schema_version") if isinstance(payload, dict)
                else type(payload).__name__,
                SCHEMA_VERSION)
            return None

        records: Dict[_PairKey, AlertRecord] = {}
        for i, d in enumerate(self._list_field(payload, "alert_records")):
            try:
                rec = _decode_record(d)
            except Exception as exc:
                logger.error("persist: skipping malformed alert record #%d "
                             "in %s (%s); that pair restarts fresh (re-alerts "
                             "rather than silences)", i, self._path, exc)
                continue
            records[(rec.client_id, rec.incident_id)] = rec

        ledger: Dict[_LedgerKey, float] = {}
        for i, d in enumerate(self._list_field(payload, "notified_ledger")):
            try:
                key = (str(d["client_id"]), str(d["incident_id"]),
                       str(d["state"]))
                ledger[key] = float(d["t_s"])
            except Exception as exc:
                logger.error("persist: skipping malformed ledger entry #%d "
                             "in %s (%s)", i, self._path, exc)

        try:
            plan = _decode_plan(payload.get("incumbent_plan"))
        except Exception as exc:
            logger.error("persist: incumbent plan in %s malformed (%s); "
                         "restarting with no incumbent (cold-start plan)",
                         self._path, exc)
            plan = None

        growth: dict = {}
        raw_growth = payload.get("incident_growth")
        if isinstance(raw_growth, dict):
            for iid, g in raw_growth.items():
                try:
                    acr = g.get("acreage")
                    growth[str(iid)] = {
                        "acreage": float(acr) if acr is not None else None,
                        "last_growth_s": float(g["last_growth_s"]),
                    }
                except Exception as exc:
                    logger.error("persist: skipping malformed growth entry "
                                 "%r in %s (%s)", iid, self._path, exc)

        meta = payload.get("meta")
        return {
            "alert_records": records,
            "notified_ledger": ledger,
            "incumbent_plan": plan,
            "meta": dict(meta) if isinstance(meta, dict) else {},
            "incident_growth": growth,
        }

    def _list_field(self, payload: dict, field: str) -> list:
        """payload[field] as a list; a type-corrupt non-list scalar (Session-3
        verifier F1b: {"alert_records": 7}) is a loud ERROR + [] — the
        affected pairs re-track fresh (re-alerts rather than silences),
        never a TypeError out of load()."""
        raw = payload.get(field)
        if raw is None:
            return []
        if not isinstance(raw, list):
            logger.error(
                "persist: %s in %s is %s, not a list — treating as EMPTY "
                "(affected pairs re-track fresh: re-alerts rather than "
                "silences)", field, self._path, type(raw).__name__)
            return []
        return raw
