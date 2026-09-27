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
    the module deterministic under injected clocks (that includes the S3
    mirror status stamp: save(now_s=...) or it stays None).
  * OPTIONAL S3 MIRROR (durable state off /tmp) — see below.

S3 MIRROR (tri-model review 2026-08-31 fix program, "durable state off
/tmp"): the live watcher runs on App Runner with its state dir on /tmp, so
every container restart (~37 in August) wiped the ladder/ledger/plan and the
restarted process could re-fire notifications. StateStore(dir, s3_uri=...)
mirrors the state file to ONE S3 object:

    s3://<bucket>/<prefix>/state.json     (S3_STATE_OBJECT)

    save()  local atomic write FIRST (unchanged, still the authority), THEN
            put_object. A failed upload is a LOUD ERROR and save() still
            returns success — the mirror is a convenience for the NEXT
            container, never a correctness dependency of this one.
    load()  when the LOCAL file is absent (fresh container) and a mirror is
            configured, get_object -> write it to the local path (same
            tmp+fsync+replace discipline) and then run the normal tolerant
            load. A missing object is "first run" (info); ANY other S3
            failure is a loud ERROR + start fresh; a corrupt object lands on
            disk and the existing tolerant load rejects it loudly. load()
            still NEVER raises.
    mirror_status()  ops surface: enabled/uri/key, last upload ok + the
            caller-supplied timestamp, last error, and whether THIS process
            restored from S3.

boto3 is an OPTIONAL dependency (requirements.txt, the clients.py pattern):
the client is INJECTABLE (s3_client=), imported LAZILY on first S3 use, and
its absence is a clear RuntimeError raised ONLY on the S3 path — i.e. only
when a mirror was actually configured. Nothing about the local-disk path
changes when s3_uri is None. Operator contract: docs/DURABLE_STATE.md.

Payload shapes (all times unix seconds, per contracts):
  alert_records   {(client_id, incident_id): contracts.AlertRecord}
  notified_ledger {(client_id, incident_id, state_value): t_s} — what was
                  ACTUALLY sent (AlertEngine.notified_ledger()).
  incumbent_plan  contracts.DispatchPlan | None (warm-start seed).
  meta            JSON-able dict (watch metadata: aoi, started_at, ...).
  incident_growth {incident_id: {"acreage": float|None, "last_growth_s": s}}
                  — the containment-gate no-growth clock (SAFETY SEMANTICS 8;
                  additive kwarg, engine seam AlertEngine.incident_growth()).
  seeded_incidents [row, ...] — W1 seeded incidents (contracts SAFETY
                  SEMANTICS 12h): the rows of wgresponse.seeded.
                  SeededIncidentStore.rows(), stored VERBATIM (the explicit
                  field map lives in seeded.encode_entry/decode_entry).
                  ADDITIVE: written only when the caller passes the kwarg
                  (the watch daemon does once any seed exists), so a daemon
                  that never seeded writes a byte-identical file; load()
                  returns [] when the key is absent and a loud [] when it is
                  not a list (a type-corrupt key never costs the alert state).

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
# The mirrored object's name under the configured prefix. Deliberately NOT
# the local filename: the S3 key is the operator-facing contract
# (docs/DURABLE_STATE.md); the local name is an implementation detail.
S3_STATE_OBJECT = "state.json"

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
# S3 mirror (shared by StateStore and terrain.LfpsTerrain's tile cache)
# ---------------------------------------------------------------------------

def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """``s3://bucket/some/prefix`` -> ``("bucket", "some/prefix")``.

    The prefix is optional (``s3://bucket`` -> ``("bucket", "")``) and never
    carries leading/trailing slashes. Raises ValueError on anything that is
    not an s3:// URI — a mis-typed WGRESP_STATE_S3 must fail LOUDLY at
    startup rather than silently degrade to "no mirror", which is the exact
    failure mode the durable-state fix exists to prevent."""
    s = str(uri).strip()
    if not s.lower().startswith("s3://"):
        raise ValueError(
            "S3 URI must look like s3://bucket/prefix — got %r" % (uri,))
    rest = s[len("s3://"):].strip()
    bucket, _, prefix = rest.partition("/")
    bucket = bucket.strip()
    if not bucket or any(ch.isspace() for ch in bucket):
        raise ValueError("S3 URI has no usable bucket name: %r" % (uri,))
    return bucket, prefix.strip("/")


def _is_missing_object(exc: BaseException) -> bool:
    """True when a boto3 error means "that key is not there" (first run).

    Duck-typed on purpose: botocore is NOT imported (it is an optional
    dependency and tests inject a fake client), so this reads the
    botocore-shaped ``response`` dict and the exception class name. Only key
    ABSENCE counts — NoSuchBucket / AccessDenied / throttling are REAL
    failures and must stay loud, so an explicit error Code is AUTHORITATIVE
    (NoSuchBucket also answers HTTP 404: reading the status first would
    silently swallow a mis-typed bucket as "first run")."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = str((resp.get("Error") or {}).get("Code") or "")
        if code:
            return code in ("NoSuchKey", "NotFound", "404")
        http = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if str(http) == "404":
            return True
    return type(exc).__name__ in ("NoSuchKey", "NotFound", "404")


def mirror_off_status() -> dict:
    """The mirror_status() shape when no S3 mirror is configured."""
    return {"enabled": False, "uri": None, "bucket": None, "prefix": None,
            "last_upload_ok": None, "last_upload_s": None,
            "last_upload_key": None, "last_error": None}


class S3Mirror:
    """One-object-per-file S3 mirror for a local durable file.

    Constructed from ``s3://bucket/prefix``; ``key_for(name)`` composes keys
    under that prefix. ``client`` is any boto3-style S3 client exposing
    ``put_object(Bucket=, Key=, Body=bytes)`` and
    ``get_object(Bucket=, Key=) -> {"Body": <.read()>}``; tests inject a fake
    so no AWS is ever touched. With no client injected boto3 is imported
    LAZILY on first use and its absence is a clear RuntimeError — raised only
    here, i.e. only when a mirror was actually configured (the
    wgresponse.clients optional-dependency pattern).

    Failure discipline:
      upload()   NEVER raises. Any failure (including the missing-boto3
                 RuntimeError) is a LOUD ERROR log + False; the caller's
                 local write has already succeeded and still counts.
      download() returns None for a MISSING object (first run) and RAISES
                 for every other failure, so each caller can log its own
                 domain-specific "starting fresh" / "falling back" ERROR.
    No wall-clock reads: the status timestamp is whatever the caller passes
    as ``now_s`` (None when the caller has no clock to offer)."""

    def __init__(self, s3_uri: str, client: Optional[object] = None) -> None:
        self._uri = str(s3_uri)
        self._bucket, self._prefix = parse_s3_uri(s3_uri)
        self._client = client
        self._last_upload_ok: Optional[bool] = None
        self._last_upload_s: Optional[float] = None
        self._last_upload_key: Optional[str] = None
        self._last_error: Optional[str] = None

    # -- identity ------------------------------------------------------------

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def prefix(self) -> str:
        return self._prefix

    def key_for(self, name: str) -> str:
        """Object key for ``name`` under the configured prefix."""
        name = str(name).lstrip("/")
        return "%s/%s" % (self._prefix, name) if self._prefix else name

    # -- client --------------------------------------------------------------

    def client(self):
        """The S3 client: the injected one, else a lazily built boto3 client.

        Raises RuntimeError (naming the pip) when boto3 is absent. Region and
        credentials come from the standard AWS environment / task role."""
        if self._client is None:
            try:
                import boto3
            except ImportError as e:
                raise RuntimeError(
                    "the S3 durable-state mirror (%s) requires boto3 — pip "
                    "install boto3, inject s3_client for tests/offline use, "
                    "or leave the mirror unconfigured to run on local disk "
                    "only" % (self._uri,)) from e
            self._client = boto3.client("s3")
        return self._client

    # -- transfers -----------------------------------------------------------

    def download(self, key: str) -> Optional[bytes]:
        """Object bytes, or None when the object does not exist (first run).
        Raises on every OTHER failure (auth, bucket, network, no boto3)."""
        try:
            resp = self.client().get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing_object(exc):
                return None
            raise
        body = resp["Body"] if isinstance(resp, dict) else resp
        return body.read() if hasattr(body, "read") else bytes(body)

    def upload(self, key: str, body: bytes,
               now_s: Optional[float] = None) -> bool:
        """PUT body at key; True on success. NEVER raises: any failure is a
        LOUD ERROR + False (the caller's local write already succeeded)."""
        try:
            self.client().put_object(Bucket=self._bucket, Key=key, Body=body)
        except Exception as exc:
            self._last_upload_ok = False
            self._last_upload_s = now_s
            self._last_upload_key = key
            self._last_error = "%s: %s" % (type(exc).__name__, exc)
            logger.error(
                "S3 mirror: upload to s3://%s/%s FAILED (%s) — the LOCAL copy "
                "is written and authoritative, but a CONTAINER RESTART WILL "
                "NOT FIND THIS STATE (durable-state mirror degraded)",
                self._bucket, key, exc)
            return False
        self._last_upload_ok = True
        self._last_upload_s = now_s
        self._last_upload_key = key
        self._last_error = None
        logger.debug("S3 mirror: uploaded %d bytes to s3://%s/%s",
                     len(body), self._bucket, key)
        return True

    # -- status --------------------------------------------------------------

    def status(self) -> dict:
        """Ops surface: last upload ok/failed + the caller-supplied stamp."""
        return {"enabled": True, "uri": self._uri, "bucket": self._bucket,
                "prefix": self._prefix,
                "last_upload_ok": self._last_upload_ok,
                "last_upload_s": self._last_upload_s,
                "last_upload_key": self._last_upload_key,
                "last_error": self._last_error}


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------

class StateStore:
    """Disk-JSON persistence for the watch daemon (contracts persist seam).

    One state file per directory (`STATE_FILENAME`); the directory is created
    on first save(). save() raises OSError on disk failure (the daemon guards
    and notes it — a full disk must not kill the alert loop); load() NEVER
    raises (absence/corruption -> loud note + None, start fresh).

    ``s3_uri`` (``s3://bucket/prefix``) turns on the S3 mirror described in
    the module docstring: save() uploads to ``<prefix>/state.json`` AFTER the
    local atomic write, load() pulls that object down when the local file is
    absent (fresh container). ``s3_client`` injects a boto3-style client
    (tests); otherwise boto3 is imported lazily on first S3 use. With
    ``s3_uri=None`` the class behaves EXACTLY as before — no imports, no
    network, no behaviour change.

    A malformed s3_uri raises ValueError HERE (startup), so a typo in
    WGRESP_STATE_S3 can never degrade silently into "no mirror".
    """

    def __init__(self, dir_path, s3_uri: Optional[str] = None,
                 s3_client: Optional[object] = None) -> None:
        self._dir = Path(dir_path)
        self._path = self._dir / STATE_FILENAME
        self._mirror = S3Mirror(s3_uri, client=s3_client) if s3_uri else None
        self._s3_key = (self._mirror.key_for(S3_STATE_OBJECT)
                        if self._mirror else None)
        self._restored_from_s3 = False
        self._last_download: Optional[str] = None   # hit|missing|error|None
        if s3_uri is None and s3_client is not None:
            logger.warning("persist: s3_client was given without s3_uri — the "
                           "S3 mirror stays OFF (state is local-disk only)")

    @property
    def path(self) -> Path:
        """The state file path (for logs/tests)."""
        return self._path

    @property
    def s3_key(self) -> Optional[str]:
        """The mirrored object key (``<prefix>/state.json``); None when the
        mirror is off."""
        return self._s3_key

    @property
    def s3_enabled(self) -> bool:
        """True when an S3 mirror is configured."""
        return self._mirror is not None

    def mirror_status(self) -> dict:
        """S3 mirror surface for the daemon/console (never raises).

        {"enabled", "uri", "bucket", "prefix", "key", "last_upload_ok"
         (True / False / None = never attempted), "last_upload_s" (the
         timestamp the CALLER passed to save(now_s=...); None if it passed
         none — persist reads no clock), "last_upload_key", "last_error",
         "restored_from_s3", "last_download": "hit"|"missing"|"error"|None}.
        """
        st = self._mirror.status() if self._mirror else mirror_off_status()
        st["key"] = self._s3_key
        st["restored_from_s3"] = self._restored_from_s3
        st["last_download"] = self._last_download
        return st

    def probe_mirror(self) -> None:
        """Raise RuntimeError if a mirror is configured but unusable (boto3
        missing). No-op when the mirror is off. For a STARTUP check by the
        CLI: save()/load() themselves never raise on S3 trouble, so without
        this an operator could run a whole shift believing state was durable.
        """
        if self._mirror is not None:
            self._mirror.client()

    # ------------------------------------------------------------------ save

    def save(self, alert_records: Dict[_PairKey, AlertRecord],
             notified_ledger: Dict[_LedgerKey, float],
             incumbent_plan: Optional[DispatchPlan],
             meta: Optional[dict],
             incident_growth: Optional[dict] = None,
             now_s: Optional[float] = None,
             seeded_incidents: Optional[list] = None) -> str:
        """Atomically persist one cycle's durable state; returns the file path.

        ``seeded_incidents`` (W1, SAFETY SEMANTICS 12h): JSON-able seed rows
        (seeded.SeededIncidentStore.rows()); None = the key is not written
        at all (byte-identical to the pre-W1 file).

        Tuple keys are flattened into lists (JSON has no tuple keys). Raises
        OSError/TypeError upward on disk/serialization failure — the caller
        decides whether that is fatal (the watch daemon logs + continues).

        With an S3 mirror configured the same bytes are then PUT to
        ``<prefix>/state.json``; that upload NEVER raises and never changes
        the return value — a failed mirror is a loud ERROR while the local
        save still counts as success. ``now_s`` (the caller's clock, unix
        seconds) is only stamped into mirror_status(); persist itself reads
        no wall clock.
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
        if seeded_incidents is not None:
            payload["seeded_incidents"] = [dict(r) for r in seeded_incidents]
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
        if self._mirror is not None:
            # The local save has ALREADY succeeded and is authoritative; the
            # mirror only has to survive for the NEXT container, so its
            # failure is loud (inside upload()) but never fatal here.
            self._mirror.upload(self._s3_key, body.encode("utf-8"),
                                now_s=now_s)
        return str(self._path)

    # ------------------------------------------------------------------ load

    def load(self) -> Optional[dict]:
        """Restore the persisted state, or None (loud note) when absent/corrupt.

        Returns {"alert_records": {(cid, iid): AlertRecord},
                 "notified_ledger": {(cid, iid, state): t_s},
                 "incumbent_plan": DispatchPlan | None,
                 "meta": dict,
                 "incident_growth": {iid: {"acreage", "last_growth_s"}},
                 "seeded_incidents": [row, ...]}   (W1; [] when absent).
        NEVER raises: file absent -> info note; unreadable / non-UTF-8 /
        corrupt JSON / unknown schema / type-corrupt containers -> ERROR
        note; a malformed individual record/ledger entry is skipped with its
        own ERROR note while the rest restore. A last-resort guard converts
        ANY unexpected failure into a loud ERROR + None (Session-3 verifier
        F1: a supervised daemon must never crash-loop on a bad state file).

        S3 mirror: when the local file is ABSENT and a mirror is configured,
        the mirrored object is downloaded to the local path first (missing
        object -> info "first run"; any S3 error -> loud ERROR + start
        fresh), then the tolerant load above proceeds unchanged — so a
        corrupt mirrored object is rejected by exactly the same loud,
        never-fatal path as a corrupt local file.
        """
        try:
            self._restore_from_s3()
        except Exception:
            # Belt-and-braces: _restore_from_s3 already swallows everything.
            logger.exception(
                "persist: S3 restore failed in an UNEXPECTED way — STARTING "
                "FRESH from local disk; previously-alerted pairs may re-notify")
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
            # W1 (SAFETY SEMANTICS 12h): rows verbatim; the seeded store
            # decodes them tolerantly (a malformed row is skipped loudly).
            "seeded_incidents": [r for r in self._list_field(
                payload, "seeded_incidents") if isinstance(r, dict)],
        }

    # ------------------------------------------------------------ S3 mirror

    def _restore_from_s3(self) -> bool:
        """Fill an ABSENT local state file from the mirror. Never raises.

        True only when the local file now exists because of this download.
        A present local file always wins: a live container's own write is
        newer than anything in S3, and re-downloading could rewind the
        ladder/ledger."""
        if self._mirror is None:
            return False
        if self._path.exists():
            return False
        uri = "s3://%s/%s" % (self._mirror.bucket, self._s3_key)
        try:
            body = self._mirror.download(self._s3_key)
        except Exception as exc:
            self._last_download = "error"
            logger.error(
                "persist: restore from %s FAILED (%s) — STARTING FRESH; "
                "previously-alerted pairs may re-notify (check the task "
                "role's s3:GetObject on this bucket)", uri, exc)
            return False
        if body is None:
            self._last_download = "missing"
            logger.info(
                "persist: no mirrored state at %s — first run for this "
                "prefix, starting fresh", uri)
            return False
        try:
            self._write_bytes_atomically(body)
        except OSError as exc:
            self._last_download = "error"
            logger.error(
                "persist: mirrored state from %s could not be written to %s "
                "(%s) — STARTING FRESH", uri, self._path, exc)
            return False
        self._restored_from_s3 = True
        self._last_download = "hit"
        logger.info(
            "persist: restored %d bytes of state from %s -> %s (container "
            "restart keeps the alert ladder, notified ledger and incumbent "
            "plan)", len(body), uri, self._path)
        return True

    def _write_bytes_atomically(self, body: bytes) -> None:
        """Same durability discipline as save(): tmp + flush + fsync +
        os.replace, so a crash mid-restore cannot leave a torn state file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(self._path) + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)

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
