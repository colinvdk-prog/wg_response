"""Tests for the persist.StateStore S3 MIRROR — durable state off /tmp.

The live watcher runs on App Runner with its state dir on /tmp: every
container restart (~37 in August, tri-model review 2026-08-31) wiped the
alert ladder, the notified ledger and the incumbent plan, so the restarted
process could re-fire notifications it had already sent. StateStore(dir,
s3_uri=...) mirrors the state file to s3://bucket/prefix/state.json so the
NEXT container restores it.

Load-bearing guarantees exercised here:
  * the restart drill survives a CONTAINER swap (store A saves with the
    mirror on; store B, in a brand-new EMPTY dir, restores the identical
    records / ledger / plan / growth clock and re-fires nothing);
  * the local atomic write stays the authority — a failed upload is LOUD but
    save() still succeeds;
  * a missing object is "first run" (info), any other S3 error and a corrupt
    object are LOUD + start fresh, and load() still never raises;
  * with s3_uri=None NOTHING changes (no client touched, no imports).

No AWS, no boto3, no botocore: the fake S3 client below is a dict with
botocore-SHAPED errors (the production code duck-types them on purpose).
tests/test_terrain_s3.py imports FakeS3/S3Missing/S3Boom from this module.
"""
from __future__ import annotations

import json
import logging
import os
import sys

import pytest

from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
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
from wgresponse.persist import (
    S3_STATE_OBJECT,
    STATE_FILENAME,
    S3Mirror,
    StateStore,
    _is_missing_object,
    parse_s3_uri,
)

H = 3600.0
T0 = 1_780_000_000.0
URI = "s3://wg-response-state/wgresp/prod"
BUCKET = "wg-response-state"
KEY = "wgresp/prod/state.json"


# ---------------------------------------------------------------------------
# Fake S3 client (in-memory; botocore-shaped errors, botocore NOT imported)
# ---------------------------------------------------------------------------

class S3Missing(Exception):
    """"That key is not there" — shaped like botocore's NoSuchKey ClientError
    (the mirror duck-types Error.Code / HTTPStatusCode; see
    persist._is_missing_object). Means FIRST RUN, not failure."""

    def __init__(self, key: str = "?"):
        super().__init__(f"An error occurred (NoSuchKey): {key}")
        self.response = {"Error": {"Code": "NoSuchKey",
                                   "Message": "The specified key does not exist."},
                         "ResponseMetadata": {"HTTPStatusCode": 404}}


class S3Boom(Exception):
    """Any OTHER S3 failure (AccessDenied, throttling, 500, network). These
    are REAL failures: loud, never silently treated as "first run"."""

    def __init__(self, code: str = "AccessDenied", http: int = 403):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code},
                         "ResponseMetadata": {"HTTPStatusCode": http}}


class _Body:
    """Stand-in for botocore's StreamingBody (only .read() is used)."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self, *a):
        return self._data


class FakeS3:
    """In-memory boto3-style S3 client: put_object / get_object / head_object.

    ``objects`` maps (bucket, key) -> bytes. ``fail_put`` / ``fail_get`` make
    every call of that kind raise S3Boom (the "S3 is broken" drills). Every
    call is recorded so tests can assert that a path really was (or never
    was) touched."""

    def __init__(self, objects: dict = None, fail_put: bool = False,
                 fail_get: bool = False):
        self.objects = dict(objects or {})
        self.puts: list = []
        self.gets: list = []
        self.fail_put = fail_put
        self.fail_get = fail_get

    # boto3 keyword spelling on purpose (Bucket/Key/Body)
    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        self.puts.append((Bucket, Key, len(Body or b"")))
        if self.fail_put:
            raise S3Boom("SlowDown", 503)
        if not isinstance(Body, (bytes, bytearray)):
            raise TypeError(f"Body must be bytes, got {type(Body).__name__}")
        self.objects[(Bucket, Key)] = bytes(Body)
        return {"ETag": '"fake-etag"'}

    def get_object(self, Bucket=None, Key=None, **kw):
        self.gets.append((Bucket, Key))
        if self.fail_get:
            raise S3Boom()
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError:
            raise S3Missing(Key) from None
        return {"Body": _Body(data), "ContentLength": len(data)}

    def head_object(self, Bucket=None, Key=None, **kw):
        if (Bucket, Key) not in self.objects:
            raise S3Missing(Key)
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    # test helpers
    def body(self, key: str, bucket: str = BUCKET):
        return self.objects.get((bucket, key))

    def keys(self, bucket: str = BUCKET):
        return sorted(k for (b, k) in self.objects if b == bucket)


class ExplodingS3:
    """A client that must never be called (mirror-off assertions)."""

    def put_object(self, **kw):
        raise AssertionError("S3 put_object called with the mirror OFF")

    def get_object(self, **kw):
        raise AssertionError("S3 get_object called with the mirror OFF")


# ---------------------------------------------------------------------------
# fixtures (kept local so this file does not depend on test_persist internals)
# ---------------------------------------------------------------------------

def assess(cid, iid, now_s, p50_h, p10_h=None, p90_h=None):
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


def sample_growth() -> dict:
    return {"f1": {"acreage": 812.0, "last_growth_s": T0 - 3600.0},
            "f2": {"acreage": None, "last_growth_s": T0}}


CLIENTS = {
    "c1": ClientProperty(client_id="c1", loc=LatLon(34.1, -118.2), name="Uno"),
    "c2": ClientProperty(client_id="c2", loc=LatLon(34.2, -118.3), name="Dos"),
}


def _engine_with_sent_alerts():
    """Engine that has ALREADY notified: c1 NOTICE->WATCH, c2 EXTREME."""
    notifier = LogNotifier()
    eng = AlertEngine(ResponseConfig(), notifier=notifier, clients_by_id=CLIENTS)
    eng.evaluate([assess("c1", "f1", T0, 54.0),
                  assess("c2", "f1", T0, 10.0, p10_h=8.0)], now_s=T0)
    eng.evaluate([assess("c1", "f1", T0 + 300.0, 53.9),
                  assess("c2", "f1", T0 + 300.0, 9.9, p10_h=7.9)],
                 now_s=T0 + 300.0)
    return eng, notifier


def _unchanged_world(now_s):
    return [assess("c1", "f1", now_s, 53.8),
            assess("c2", "f1", now_s, 9.8, p10_h=7.8)]


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def _text(records) -> str:
    """Fully-interpolated log text (the interesting details — the S3 error,
    the missing pip — arrive as %-args, not in the format string)."""
    return " | ".join(r.getMessage() for r in records)


# ---------------------------------------------------------------------------
# URI parsing / missing-object discrimination
# ---------------------------------------------------------------------------

class TestUriAndErrorShapes:
    def test_parse_uri_bucket_and_prefix(self):
        assert parse_s3_uri("s3://b/p/q") == ("b", "p/q")
        assert parse_s3_uri("s3://b/p/q/") == ("b", "p/q")
        assert parse_s3_uri("s3://b") == ("b", "")
        assert parse_s3_uri("s3://b/") == ("b", "")
        assert parse_s3_uri("  S3://b/p  ") == ("b", "p")

    @pytest.mark.parametrize("bad", ["bucket/prefix", "https://b/p", "s3://",
                                     "s3:///prefix", "", "s3:// /p"])
    def test_bad_uri_raises_valueerror_loudly(self, bad):
        """A typo in WGRESP_STATE_S3 must fail at STARTUP, never degrade
        silently into an unmirrored (restart-loses-everything) daemon."""
        with pytest.raises(ValueError):
            parse_s3_uri(bad)

    def test_store_rejects_bad_uri_at_construction(self, tmp_path):
        with pytest.raises(ValueError):
            StateStore(tmp_path, s3_uri="my-bucket/prefix")

    def test_missing_object_only_for_key_absence(self):
        assert _is_missing_object(S3Missing("k")) is True
        assert _is_missing_object(S3Boom()) is False
        # A mis-typed BUCKET also answers 404 — it is a config error, not a
        # first run, and must stay loud (the explicit Code wins).
        assert _is_missing_object(S3Boom("NoSuchBucket", 404)) is False
        # A bare exception with no botocore shape is NOT "missing" (loud).
        assert _is_missing_object(RuntimeError("network down")) is False

    def test_key_composition(self, tmp_path):
        assert StateStore(tmp_path, s3_uri=URI).s3_key == KEY
        assert StateStore(tmp_path, s3_uri="s3://b").s3_key == S3_STATE_OBJECT
        assert S3Mirror("s3://b/p").key_for("/x/y") == "p/x/y"


# ---------------------------------------------------------------------------
# mirror OFF: byte-identical behaviour
# ---------------------------------------------------------------------------

class TestMirrorOff:
    def test_no_s3_uri_never_touches_a_client(self, tmp_path):
        store = StateStore(tmp_path)
        assert store.s3_enabled is False
        assert store.s3_key is None
        store.save({}, {}, sample_plan(), {"k": 1}, incident_growth=sample_growth())
        loaded = store.load()
        assert loaded["incumbent_plan"] == sample_plan()
        assert loaded["incident_growth"] == sample_growth()
        assert store.mirror_status() == {
            "enabled": False, "uri": None, "bucket": None, "prefix": None,
            "last_upload_ok": None, "last_upload_s": None,
            "last_upload_key": None, "last_error": None, "key": None,
            "restored_from_s3": False, "last_download": None}

    def test_client_without_uri_is_ignored_with_a_warning(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="wgresponse.persist"):
            store = StateStore(tmp_path, s3_client=ExplodingS3())
        assert store.s3_enabled is False
        store.save({}, {}, None, {})        # ExplodingS3 must never be called
        assert store.load() is not None
        assert any("stays OFF" in r.message for r in caplog.records)

    def test_probe_mirror_is_a_noop_when_off(self, tmp_path):
        StateStore(tmp_path).probe_mirror()


# ---------------------------------------------------------------------------
# save(): local write first, then the mirror
# ---------------------------------------------------------------------------

class TestSaveUploads:
    def test_upload_happens_after_the_local_atomic_write(self, tmp_path):
        fake = FakeS3()
        store = StateStore(tmp_path / "state", s3_uri=URI, s3_client=fake)
        path = store.save({}, {}, sample_plan(), {"aoi_desc": "socal"},
                          incident_growth=sample_growth(), now_s=T0)
        # local file intact, atomic (no .tmp left), and byte-identical to S3
        assert os.path.basename(path) == STATE_FILENAME
        assert [p for p in os.listdir(tmp_path / "state")
                if p.endswith(".tmp")] == []
        # ONE put, to <prefix>/state.json, carrying the same JSON document as
        # the local file. (Bytes match exactly on Linux/App Runner; on Windows
        # the local text-mode write expands \n to \r\n while the object keeps
        # the serialized LF bytes — which is why the restore writes the object
        # back in BINARY mode and json parses either.)
        assert [(b, k) for (b, k, _n) in fake.puts] == [(BUCKET, KEY)]
        assert json.loads(fake.body(KEY).decode("utf-8")) == json.loads(
            (tmp_path / "state" / STATE_FILENAME).read_text(encoding="utf-8"))
        assert json.loads(fake.body(KEY).decode("utf-8"))["meta"]["aoi_desc"] \
            == "socal"

    def test_second_save_overwrites_the_object(self, tmp_path):
        fake = FakeS3()
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        store.save({}, {}, None, {"cycles": 1}, now_s=T0)
        store.save({}, {}, None, {"cycles": 2}, now_s=T0 + 300.0)
        assert fake.keys() == [KEY]
        assert json.loads(fake.body(KEY))["meta"]["cycles"] == 2

    def test_mirror_status_stamps_only_the_caller_clock(self, tmp_path):
        """persist reads NO wall clock: the status timestamp is exactly what
        the caller passed (None when it passed none)."""
        fake = FakeS3()
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        store.save({}, {}, None, {})
        st = store.mirror_status()
        assert st["enabled"] is True and st["last_upload_ok"] is True
        assert st["last_upload_s"] is None
        assert st["uri"] == URI and st["bucket"] == BUCKET and st["key"] == KEY
        store.save({}, {}, None, {}, now_s=T0 + 900.0)
        assert store.mirror_status()["last_upload_s"] == T0 + 900.0

    def test_upload_failure_is_loud_but_save_still_succeeds(self, tmp_path, caplog):
        """The local atomic write is the authority — a broken mirror must not
        break the alert loop, but the operator MUST hear about it."""
        fake = FakeS3(fail_put=True)
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            path = store.save({}, {}, sample_plan(), {"x": 1}, now_s=T0)
        assert os.path.exists(path)                       # local save counted
        assert StateStore(tmp_path).load()["incumbent_plan"] == sample_plan()
        msgs = _text(_errors(caplog))
        assert "FAILED" in msgs and "RESTART" in msgs
        st = store.mirror_status()
        assert st["last_upload_ok"] is False
        assert st["last_upload_s"] == T0
        assert "SlowDown" in st["last_error"]


# ---------------------------------------------------------------------------
# THE RESTART DRILL THROUGH S3 (a container swap, not just a process restart)
# ---------------------------------------------------------------------------

class TestRestartDrillThroughS3:
    def test_new_container_empty_dir_restores_identical_state(self, tmp_path):
        fake = FakeS3()
        eng1, _ = _engine_with_sent_alerts()
        records_before = eng1.states()
        ledger_before = eng1.notified_ledger()
        growth_before = eng1.incident_growth()

        store_a = StateStore(tmp_path / "container_a", s3_uri=URI,
                             s3_client=fake)
        store_a.save(records_before, ledger_before, sample_plan(),
                     {"started_at_s": T0}, incident_growth=growth_before,
                     now_s=T0 + 300.0)

        # A NEW container: different, EMPTY state dir (the /tmp wipe), same
        # bucket+prefix. Nothing but S3 carries the state across.
        store_b = StateStore(tmp_path / "container_b", s3_uri=URI,
                             s3_client=fake)
        assert not (tmp_path / "container_b").exists()
        loaded = store_b.load()
        assert loaded is not None
        assert loaded["alert_records"] == records_before
        assert loaded["notified_ledger"] == ledger_before
        assert loaded["incumbent_plan"] == sample_plan()
        assert loaded["incident_growth"] == growth_before
        assert loaded["meta"]["started_at_s"] == T0
        assert store_b.mirror_status()["restored_from_s3"] is True
        assert store_b.mirror_status()["last_download"] == "hit"
        # the object was pulled down to the new container's local path
        assert os.path.exists(tmp_path / "container_b" / STATE_FILENAME)

    def test_restored_container_refires_nothing(self, tmp_path):
        """The point of the whole exercise: after a container swap the
        restarted engine sends NOTHING for an unchanged world."""
        fake = FakeS3()
        eng1, _ = _engine_with_sent_alerts()
        StateStore(tmp_path / "a", s3_uri=URI, s3_client=fake).save(
            eng1.states(), eng1.notified_ledger(), None, {},
            incident_growth=eng1.incident_growth(), now_s=T0 + 300.0)

        loaded = StateStore(tmp_path / "b", s3_uri=URI, s3_client=fake).load()
        notifier2 = LogNotifier()
        eng2 = AlertEngine(ResponseConfig(), notifier=notifier2,
                           clients_by_id=CLIENTS)
        eng2.restore(loaded["alert_records"],
                     notified_ledger=loaded["notified_ledger"],
                     incident_growth=loaded["incident_growth"])
        assert eng2.notified_ledger() == eng1.notified_ledger()
        assert eng2.evaluate(_unchanged_world(T0 + 600.0), now_s=T0 + 600.0) == []
        assert notifier2.sent == []
        # ... while a genuinely NEW threat still notifies
        ev = eng2.evaluate(_unchanged_world(T0 + 900.0)
                           + [assess("c9", "f3", T0 + 900.0, 10.0, p10_h=8.0)],
                           now_s=T0 + 900.0)
        assert [e.client_id for e in ev] == ["c9"]
        assert len(notifier2.sent) == 1

    def test_local_file_wins_over_the_mirror(self, tmp_path):
        """A live container's own file is NEWER than S3: load() must not
        download over it (that would rewind the ladder/ledger)."""
        fake = FakeS3()
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        store.save({}, {}, None, {"generation": "local"}, now_s=T0)
        fake.objects[(BUCKET, KEY)] = json.dumps(
            {"schema_version": 1, "meta": {"generation": "STALE-S3"}}
        ).encode("utf-8")
        loaded = store.load()
        assert loaded["meta"]["generation"] == "local"
        assert fake.gets == []                      # no download attempted
        assert store.mirror_status()["restored_from_s3"] is False


# ---------------------------------------------------------------------------
# load(): failure semantics (never raises, always loud in the right key)
# ---------------------------------------------------------------------------

class TestLoadFailureSemantics:
    def test_missing_object_is_first_run_info_not_error(self, tmp_path, caplog):
        fake = FakeS3()                                   # empty bucket
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.INFO, logger="wgresponse.persist"):
            assert store.load() is None
        assert _errors(caplog) == []                      # NOT an error
        assert any("no mirrored state" in r.message for r in caplog.records)
        assert store.mirror_status()["last_download"] == "missing"
        assert not os.path.exists(tmp_path / STATE_FILENAME)

    def test_s3_error_is_loud_and_starts_fresh(self, tmp_path, caplog):
        fake = FakeS3(fail_get=True)
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            assert store.load() is None                   # never raises
        msgs = _text(_errors(caplog))
        assert "FAILED" in msgs and "STARTING FRESH" in msgs
        assert "AccessDenied" in msgs and "s3:GetObject" in msgs
        assert store.mirror_status()["last_download"] == "error"

    def test_corrupt_object_is_loud_and_starts_fresh(self, tmp_path, caplog):
        fake = FakeS3({(BUCKET, KEY): b"{not json at all!!"})
        store = StateStore(tmp_path, s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            assert store.load() is None
        assert any("corrupt" in r.message.lower() for r in _errors(caplog))
        assert store.mirror_status()["last_download"] == "hit"
        # a later save simply overwrites the garbage object
        store.save({}, {}, None, {"fixed": True}, now_s=T0)
        assert json.loads(fake.body(KEY))["meta"]["fixed"] is True

    def test_mirrored_object_with_unknown_schema_starts_fresh(self, tmp_path,
                                                              caplog):
        fake = FakeS3({(BUCKET, KEY): json.dumps(
            {"schema_version": 999, "alert_records": []}).encode("utf-8")})
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            assert StateStore(tmp_path, s3_uri=URI, s3_client=fake).load() is None
        assert any("schema_version" in r.message for r in _errors(caplog))

    def test_load_never_raises_even_if_the_client_is_insane(self, tmp_path,
                                                            caplog):
        class Insane:
            def get_object(self, **kw):
                raise KeyboardInterrupt   # not even an Exception subclass

        store = StateStore(tmp_path, s3_uri=URI, s3_client=Insane())
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            with pytest.raises(KeyboardInterrupt):
                store.load()              # BaseException still propagates
        # ...but every ordinary failure is swallowed:
        class Rude:
            def get_object(self, **kw):
                raise ValueError("nonsense from the SDK")

        store2 = StateStore(tmp_path, s3_uri=URI, s3_client=Rude())
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            assert store2.load() is None


# ---------------------------------------------------------------------------
# boto3 is OPTIONAL: lazy import, clear RuntimeError, only when configured
# ---------------------------------------------------------------------------

class TestOptionalBoto3:
    @staticmethod
    def _block_boto3(monkeypatch):
        # `import boto3` raises ImportError while boto3 is None in sys.modules
        monkeypatch.setitem(sys.modules, "boto3", None)

    def test_probe_mirror_raises_a_clear_runtimeerror(self, tmp_path, monkeypatch):
        self._block_boto3(monkeypatch)
        store = StateStore(tmp_path, s3_uri=URI)          # no client injected
        with pytest.raises(RuntimeError) as ei:
            store.probe_mirror()
        msg = str(ei.value)
        assert "boto3" in msg and "pip install boto3" in msg and URI in msg

    def test_missing_boto3_never_breaks_save_or_load(self, tmp_path, monkeypatch,
                                                     caplog):
        self._block_boto3(monkeypatch)
        store = StateStore(tmp_path, s3_uri=URI)
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            assert store.load() is None                   # loud, no crash
            path = store.save({}, {}, None, {"n": 1}, now_s=T0)
        assert os.path.exists(path)                       # local save works
        assert StateStore(tmp_path).load()["meta"]["n"] == 1
        assert "pip install boto3" in _text(_errors(caplog))
        assert store.mirror_status()["last_upload_ok"] is False

    def test_no_boto3_needed_when_the_mirror_is_off(self, tmp_path, monkeypatch,
                                                    caplog):
        self._block_boto3(monkeypatch)
        store = StateStore(tmp_path)
        with caplog.at_level(logging.ERROR, logger="wgresponse.persist"):
            store.save({}, {}, None, {"n": 2}, now_s=T0)
            assert store.load()["meta"]["n"] == 2
        assert _errors(caplog) == []
        store.probe_mirror()                              # no-op, no import
