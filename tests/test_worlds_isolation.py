"""TWO WORLDS — the isolation proof (contracts SAFETY SEMANTICS 16; founder
ruling 2026-09-27: "we need a test map and a live map so all simulations or
drills do not impact real conditions").

The core proof runs the SAME mocked watch daemon (real ThreatMonitor, real
AlertEngine, real dispatch planner, real CrewTracker with GPS beacons, real
StateStore with an S3 mirror, the real HTTP server) through the same live
script TWICE — once with no test activity, once with heavy test activity
interleaved over HTTP (seeds near the live fire's clients, retirements, crew
resets and placements, a full test-map reset, extra test cycles, test-state
and test-summary reads, the refused live seed POST) — and asserts the LIVE
world's per-cycle CycleResult, /api/state, /api/plan, /healthz and
/api/world/summary bytes, persisted state file, S3 objects, notifier calls,
plan, provider calls and even its clock reads are identical between the two
runs, while the test world demonstrably did its work (a drill ran, seeded
domains were computed, the test ladder and plan moved, the drill log filled)
and never wrote a live key.

Then: the test world with no seed mirrors the live ladder and plan exactly
(the read-only snapshot is faithful); the live loop never waits for a hung
test cycle; a live restart re-fires nothing and a test restart restores the
drill; a live state file written by the pre-two-worlds build is migrated
(its seeds never run live); the shared rasters are never mutated.

Deterministic; no network; tests/seeded_stack.py.
"""
from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import os
import re
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import crews as crews_mod  # noqa: E402
from wgresponse import dispatch as dispatch_mod  # noqa: E402
from wgresponse import seeded, server, worlds  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertRecord,
    AlertState,
    CrewBeacon,
    CrewPlan,
    CrewStatus,
    DispatchPlan,
    LatLon,
    PlannedStop,
    UnservedJob,
    is_seeded_id,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402

KEY = "worlds-key"
S3_URI = "s3://wg-bucket/live-prefix"
LIVE_KEY = "live-prefix/state.json"
TEST_PREFIX = "live-prefix/test_world/"
N_CYCLES = 8
INC2_LOC = LatLon(39.08, -120.93)          # ~4 km west of c2 (downwind: c2)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)


# ---------------------------------------------------------------------------
# recording fakes
# ---------------------------------------------------------------------------

class FakeS3:
    """boto3-shaped S3 client: records every put (key, body) in order."""

    def __init__(self):
        self.objects: dict = {}
        self.puts: list = []

    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        self.puts.append((Bucket, Key, bytes(Body)))
        self.objects[(Bucket, Key)] = bytes(Body)
        return {}

    def get_object(self, Bucket=None, Key=None, **kw):
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError:
            err = Exception("missing")
            err.response = {"Error": {"Code": "NoSuchKey"}}
            raise err from None
        import io
        return {"Body": io.BytesIO(data)}


class RecordingNotifier:
    """The REAL (client-channel) notifier: every send it receives."""

    def __init__(self):
        self.sent: list = []

    def send(self, event, client) -> bool:
        self.sent.append((event.t_s, event.client_id, event.incident_id,
                          event.prev.value, event.state.value,
                          repr(event.eta_p50_h), repr(event.eta_p10_h),
                          event.message, client.client_id))
        return True


class RecordingRegistry(ss.StubRegistry):
    def __init__(self, clients):
        super().__init__(clients)
        self.calls: list = []

    def protected_clients(self, bbox=None):
        self.calls.append(None if bbox is None else
                          (bbox.west, bbox.south, bbox.east, bbox.north))
        return super().protected_clients(bbox)


class CountingClock(ss.SettableClock):
    """A settable clock that counts its reads (the test world must never
    read the live clock)."""

    def __init__(self, t0=ss.T0):
        super().__init__(t0)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.t


# ---------------------------------------------------------------------------
# the two-world stack (mirrors cli._cmd_watch's wiring)
# ---------------------------------------------------------------------------

def _stack(state_dir, s3=None, sync=True, test_terrain=None):
    cfg = ss.small_cfg(beacon_stale_s=600.0)
    clock = CountingClock()
    test_clock = ss.SettableClock()
    roster = ss.clients()
    real = RecordingNotifier()
    notifier = seeded.SeedSafeNotifier(real)
    tracker = crews_mod.CrewTracker(cfg)
    s3 = s3 if s3 is not None else FakeS3()
    feed = ss.ScriptedFeed([ss.feed_incident()])
    weather = ss.StubWeather()
    terrain = ss.StubTerrain()
    registry = RecordingRegistry(roster)
    from wgresponse.alerts import AlertEngine
    from wgresponse.cli import _WatchDaemon
    from wgresponse.threat import ThreatMonitor
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c for c in roster})
    monitor = ThreatMonitor(feeds=[feed], weather_provider=weather,
                            terrain_provider=terrain, registry=registry,
                            alert_engine=engine, cfg=cfg,
                            safe_zones=[ss.SAFE_ZONE], crew_tracker=tracker,
                            dispatcher=dispatch_mod, aoi=ss.AOI)
    store = StateStore(state_dir, s3_uri=S3_URI, s3_client=s3)
    tw = worlds.TestWorld(
        cfg=cfg, aoi=ss.AOI, safe_zones=[ss.SAFE_ZONE],
        weather_provider=ss.StubWeather(),
        terrain_provider=test_terrain or ss.StubTerrain(),
        registry=ss.StubRegistry(roster), dispatcher=dispatch_mod,
        store=StateStore(state_dir / worlds.TEST_NAMESPACE,
                         s3_uri=worlds.child_s3_uri(S3_URI,
                                                    worlds.TEST_NAMESPACE),
                         s3_client=s3),
        clients_by_id={c.client_id: c for c in roster}, clock=test_clock,
        sync=sync, aoi_desc="isolation aoi", period_s=ss.PERIOD_S)
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=tracker, store=store, cfg=cfg, aoi=ss.AOI,
                          period_s=ss.PERIOD_S, aoi_desc="isolation aoi",
                          safe_zones=[ss.SAFE_ZONE], clock=clock,
                          sleep=lambda s: None, test_world=tw)
    return dict(cfg=cfg, clock=clock, test_clock=test_clock, real=real,
                notifier=notifier, tracker=tracker, s3=s3, feed=feed,
                weather=weather, terrain=terrain, registry=registry,
                engine=engine, monitor=monitor, store=store, tw=tw,
                daemon=daemon)


def _serve(daemon):
    srv = server.serve(0, state_provider=daemon.state,
                       beacon_sink=daemon._tracker, plan_provider=daemon.plan)
    srv.app.seed_api = daemon
    srv.app.test_api = daemon
    srv.app.world_provider = daemon.world_summary
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th


def _http(srv, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1],
                                      timeout=60)
    try:
        conn.request(method, path,
                     body=None if body is None else json.dumps(body),
                     headers={"X-Internal-API-Key": KEY,
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


#: wall-clock telemetry — the only non-deterministic numbers a payload
#: carries under injected clocks (time.monotonic / perf_counter)
_WALL = re.compile(rb'"(last_cycle_duration_s|solver_s|terrain_s|weather_s|'
                   rb'spread_s|feeds_s|assess_s|alerts_s|dispatch_s)": '
                   rb'-?[0-9][0-9.]*([eE][-+]?[0-9]+)?')


def _mask(raw: bytes) -> bytes:
    return _WALL.sub(lambda m: b'"' + m.group(1) + b'": 0', raw)


def _h(raw: bytes) -> str:
    return hashlib.sha256(_mask(raw.replace(b"\r\n", b"\n"))).hexdigest()


# ---------------------------------------------------------------------------
# the live script (identical in both runs) + the drill (run B only)
# ---------------------------------------------------------------------------

def _live_script(s, k):
    """Feeds and beacons before live cycle k: inc-2 appears at 2, vanishes
    at 5; the feeds go BLIND at 6 and recover at 7; G1 beacons every cycle,
    G2 only at 0-1 (its beacon goes stale in the live tracker)."""
    feed = s["feed"]
    feed.ok = k != 6
    incs = [ss.feed_incident()]
    if 2 <= k <= 4:
        incs.append(ss.feed_incident(iid="inc-2", name="Second Fire",
                                     origin=INC2_LOC, acreage=80.0))
    feed.incidents = incs
    t = s["clock"].t
    s["tracker"].ingest(CrewBeacon(crew_id="G1",
                                   loc=LatLon(39.10, -121.05), t_s=t - 30.0))
    if k <= 1:
        s["tracker"].ingest(CrewBeacon(crew_id="G2",
                                       loc=LatLon(39.02, -121.02),
                                       t_s=t - 30.0))


def _drill(s, srv, k, log):
    """Heavy TEST activity after live cycle k — over HTTP, at the TEST
    clock (the live clock is never touched)."""
    tc = s["test_clock"]
    base = ss.T0 + 60.0 + k * ss.PERIOD_S

    def call(method, path, body=None, at=0.0):
        tc.t = base + at
        code, raw = _http(srv, method, path, body)
        log.append((k, method, path, code))
        return code, (json.loads(raw) if raw else {})

    near = {"lat": ss.SEED_LOC.lat, "lon": ss.SEED_LOC.lon}
    if k == 0:
        code, out = call("POST", "/api/test/seed",
                         dict(near, intensity="high", wind_dir_deg=225,
                              note="drill one", created_by="colin"), at=30)
        assert code == 201, out
        s["seed1"] = out["incident_id"]
        assert call("POST", "/api/test/crews/place",
                    {"crew_id": "G1", "lat": 39.05, "lon": -121.02},
                    at=45)[0] == 200
        assert call("POST", "/api/test/crews/place",
                    {"crew_id": "T9", "lat": 39.12, "lon": -120.99,
                     "name": "Virtual Nine"}, at=50)[0] == 200
    elif k == 1:
        code, out = call("POST", "/api/test/seed",
                         {"lat": 39.03, "lon": -120.96}, at=40)
        assert code == 201, out
        s["seed2"] = out["incident_id"]
    elif k == 2:
        assert call("POST", "/api/test/crews/reset", {"by": "colin"},
                    at=20)[0] == 200
        assert call("DELETE", f"/api/test/seed/{s['seed1']}?by=colin",
                    at=60)[0] == 200
    elif k == 3:
        code, out = call("POST", "/api/test/reset", {}, at=10)
        assert code == 200 and out["drill"]["state"] == "idle"
    elif k == 4:
        code, out = call("POST", "/api/test/seed",
                         dict(near, wind_dir_deg=200,
                              weather={"wind_speed_mps": 11.0, "rh_pct": 9,
                                       "temp_c": 33}), at=15)
        assert code == 201, out
        s["tw"].run_cycle()                       # an extra test cycle
    elif k == 5:
        code, out = call("POST", "/api/seed", near, at=5)   # the legacy route
        assert code == 409 and out["use"] == "/api/test/seed"
        assert call("POST", "/api/test/crews/place",
                    {"crew_id": "G2", "lat": 39.0, "lon": -121.1,
                     "status": "off_duty"}, at=25)[0] == 200
    elif k == 6:
        code, out = call("POST", "/api/test/seed",
                         {"lat": ss.FAR_SEED_LOC.lat,
                          "lon": ss.FAR_SEED_LOC.lon}, at=35)
        assert code == 400                        # outside the AOI
        code, out = call("POST", "/api/test/seed",
                         {"lat": 39.15, "lon": -121.15,
                          "intensity": "extreme", "wind_dir_deg": 315},
                         at=40)
        assert code == 201, out
    # reads, every cycle
    for path in ("/api/test/state", "/api/test/seed",
                 "/api/world/summary?world=test", "/api/seed"):
        assert call("GET", path, at=90)[0] == 200


def _run(tmp_path, with_drill: bool):
    """The live script through N_CYCLES cycles; returns every LIVE
    observable plus the test world for the non-vacuity checks."""
    s = _stack(tmp_path)
    daemon, store = s["daemon"], s["store"]
    srv, th = _serve(daemon)
    obs = {"cycles": [], "api_state": [], "api_plan": [], "healthz": [],
           "summary": [], "state_file": [], "plan": []}
    log: list = []
    try:
        for k in range(N_CYCLES):
            s["clock"].t = ss.T0 + 60.0 + k * ss.PERIOD_S
            _live_script(s, k)
            res = daemon.run_cycle()
            obs["cycles"].append(ss.cycle_digest(res))
            obs["plan"].append(ss._canon(daemon.plan()))
            obs["state_file"].append(ss.state_digest(store))
            s["clock"].t += 120.0         # the page polls mid-period
            for key, path in (("api_state", "/api/state"),
                              ("api_plan", "/api/plan"),
                              ("healthz", "/healthz"),
                              ("summary", "/api/world/summary")):
                code, raw = _http(srv, "GET", path)
                assert code == 200, (path, raw[:200])
                obs[key].append(_h(raw))
            if with_drill:
                _drill(s, srv, k, log)
    finally:
        srv.shutdown()
        th.join(timeout=5)
        srv.server_close()
    live_puts = [(b, key, _h(body)) for b, key, body in s["s3"].puts
                 if not key.startswith(TEST_PREFIX)]
    obs.update({
        "notifier": list(s["real"].sent),
        "live_drill_log": list(s["notifier"].drill.sent),
        "live_s3": live_puts,
        "live_keys": sorted({key for _b, key, _d in live_puts}),
        "weather": [(l.lat, l.lon, h) for l, h in s["weather"].calls],
        "terrain": [(b.west, b.south, b.east, b.north, c)
                    for b, c in s["terrain"].calls],
        "registry": list(s["registry"].calls),
        "feed_bboxes": [(b.west, b.south, b.east, b.north)
                        for b in s["feed"].bboxes],
        "live_clock_reads": s["clock"].reads,
        "live_wake": s["daemon"]._wake.is_set(),
        "live_files": sorted(p.name for p in tmp_path.iterdir()
                             if p.is_file()),
    })
    return obs, s, log


# ---------------------------------------------------------------------------
# THE PROOF
# ---------------------------------------------------------------------------

def test_heavy_drill_activity_changes_no_live_byte(tmp_path):
    quiet, s_q, _ = _run(tmp_path / "quiet", with_drill=False)
    busy, s_b, log = _run(tmp_path / "busy", with_drill=True)

    # the drill really ran (the proof is not vacuous)
    tw = s_b["tw"]
    assert len(log) > 40
    assert tw.drill_state == "active" and tw.cycles_run >= 6
    assert len(tw.drill_log.rows()) >= 3            # drill alerts were raised
    seeded_domains = [iid for iid in tw.last_result.ensembles
                      if is_seeded_id(iid)]
    assert seeded_domains                           # a seeded domain computed
    assert any(is_seeded_id(k[1]) for k in tw.engine.states())
    assert tw.last_result.plan is not None
    assert {c["crew_id"] for c in tw.crews.state_rows()} >= {"G1", "G2"}
    test_keys = {key for _b, key, _d in s_b["s3"].puts
                 if key.startswith(TEST_PREFIX)}
    assert test_keys == {TEST_PREFIX + "state.json"}
    assert s_q["tw"].drill_state == "idle" and s_q["tw"].cycles_run == 0
    assert not [k for _b, k, _d in s_q["s3"].puts if k.startswith(TEST_PREFIX)]

    # the drill's alerts about a client the LIVE fire also threatens went to
    # the drill log only — the live notifier saw exactly what it saw alone
    assert any(line["client_id"] == "c1" for line in tw.drill_log.rows())

    # EVERY live observable is byte-identical
    for key in ("cycles", "plan", "state_file", "api_state", "api_plan",
                "healthz", "summary"):
        assert busy[key] == quiet[key], key
    assert busy["notifier"] == quiet["notifier"] and quiet["notifier"]
    assert busy["live_drill_log"] == quiet["live_drill_log"] == []
    assert busy["live_s3"] == quiet["live_s3"]
    assert busy["live_keys"] == quiet["live_keys"] == [LIVE_KEY]
    for key in ("weather", "terrain", "registry", "feed_bboxes"):
        assert busy[key] == quiet[key], key
    assert busy["live_clock_reads"] == quiet["live_clock_reads"]
    assert busy["live_wake"] is quiet["live_wake"] is False
    assert busy["live_files"] == quiet["live_files"] == [
        "wgresponse_state.json"]
    # the live file never carries a test key
    live_doc = json.loads(s_b["store"].path.read_text("utf-8"))
    assert not ({"seeded_incidents", "virtual_crews", "drill_log"}
                & set(live_doc))
    assert not any(is_seeded_id(r["incident_id"])
                   for r in live_doc["alert_records"])


def test_test_persistence_writes_only_its_namespace(tmp_path):
    s = _stack(tmp_path / "st")
    tw = s["tw"]
    s["clock"].t = ss.T0 + 60.0
    s["daemon"].run_cycle()
    before = sorted(p.relative_to(tmp_path).as_posix()
                    for p in (tmp_path / "st").rglob("*") if p.is_file())
    live_bytes = s["store"].path.read_bytes()
    n_puts = len(s["s3"].puts)
    s["test_clock"].t = ss.T0 + 100.0
    assert tw.seed_create({"lat": ss.SEED_LOC.lat,
                           "lon": ss.SEED_LOC.lon})[0] == 201
    assert tw.crews_reset({})[0] == 200
    assert tw.crews_place({"crew_id": "X", "lat": 39.0, "lon": -121.0})[0] \
        == 200
    tw.run_cycle()
    assert tw.reset({})[0] == 200
    after = sorted(p.relative_to(tmp_path).as_posix()
                   for p in (tmp_path / "st").rglob("*") if p.is_file())
    new = set(after) - set(before)
    assert new and all(p.startswith("st/test_world/") for p in new)
    assert s["store"].path.read_bytes() == live_bytes
    keys = [k for _b, k, _d in s["s3"].puts[n_puts:]]
    assert keys and all(k.startswith(TEST_PREFIX) for k in keys)
    # the wiped namespace restarts IDLE
    tw2 = worlds.TestWorld(
        cfg=s["cfg"], aoi=ss.AOI, safe_zones=[ss.SAFE_ZONE],
        weather_provider=ss.StubWeather(), terrain_provider=ss.StubTerrain(),
        registry=ss.StubRegistry(ss.clients()), dispatcher=dispatch_mod,
        store=StateStore(tmp_path / "st" / worlds.TEST_NAMESPACE),
        clock=ss.SettableClock(), sync=True)
    assert tw2.restore() is True and tw2.drill_state == "idle"
    assert len(tw2.monitor.seeded) == 0 and tw2.engine.states() == {}


# ---------------------------------------------------------------------------
# the test world sees the live world exactly (the snapshot is faithful)
# ---------------------------------------------------------------------------

def test_without_seeds_the_test_world_mirrors_the_live_ladder_and_plan(
        tmp_path):
    """With no seed and a crew roster that agrees with the live one (both
    live crews keep beaconing from where they were cloned — virtual crews
    are frozen at the clone and never go stale), the test world is the
    live world: the same ladder (states AND streaks) cycle for cycle, the
    same events, the same plan, the SAME ensemble objects. The drill-start
    re-run (an action) touched no live pair."""
    s = _stack(tmp_path / "m")
    daemon, tw, engine = s["daemon"], s["tw"], s["engine"]

    def script(k):
        _live_script(s, k)
        s["tracker"].ingest(CrewBeacon(crew_id="G2",
                                       loc=LatLon(39.02, -121.02),
                                       t_s=s["clock"].t - 20.0))
    s["clock"].t = ss.T0 + 60.0
    script(0)
    daemon.run_cycle()
    # a drill with NO seed: crews + ladder + plan cloned from live
    s["test_clock"].t = s["clock"].t + 10.0
    assert tw.crews_reset({})[0] == 200
    assert tw.drill_state == "active"
    n_live_before = len(s["real"].sent)
    for k in range(1, 6):
        s["clock"].t = ss.T0 + 60.0 + k * ss.PERIOD_S
        s["test_clock"].t = s["clock"].t
        script(k)
        live = daemon.run_cycle()
        test = tw.last_result
        assert test.t_s == live.t_s                 # lockstep
        assert {k2: (r.state, r.clear_streak, r.blind_cycles)
                for k2, r in tw.engine.states().items()} == \
            {k2: (r.state, r.clear_streak, r.blind_cycles)
             for k2, r in engine.states().items()}
        assert [(e.client_id, e.incident_id, e.prev, e.state, e.message)
                for e in test.events] == \
            [(e.client_id, e.incident_id, e.prev, e.state, e.message)
             for e in live.events]
        assert ss._canon(test.plan) == ss._canon(live.plan), k
        assert test.ensembles.keys() == live.ensembles.keys()
        for iid, ens in live.ensembles.items():
            assert test.ensembles[iid] is ens       # shared, never recomputed
    # every notification the live world sent to its client channel since
    # the clone, the test world sent to its DRILL LOG — the same sequence
    live_sends = [(c, i, st) for (_t, c, i, _p, st, *_r)
                  in s["real"].sent[n_live_before:]]
    drill = [(r["client_id"], r["incident_id"], r["state"])
             for r in tw.drill_log.rows()]
    assert drill == live_sends


def test_the_shared_rasters_are_never_mutated(tmp_path):
    s = _stack(tmp_path / "r")
    daemon, tw = s["daemon"], s["tw"]
    s["clock"].t = ss.T0 + 60.0
    _live_script(s, 0)
    live = daemon.run_cycle()
    ens = live.ensembles["inc-1"]
    fp = ([ss.raster_fingerprint(m) for m in ens.members],
          ss.raster_fingerprint(ens.p10), ss.raster_fingerprint(ens.p50),
          ss.raster_fingerprint(ens.p90), ens.t0_s, ens.incident_id)
    inc = live.incidents[0]
    meta_before = json.dumps(inc.meta, sort_keys=True, default=str)
    s["test_clock"].t = ss.T0 + 120.0
    tw.seed_create({"lat": ss.SEED_LOC.lat, "lon": ss.SEED_LOC.lon,
                    "intensity": "extreme", "wind_dir_deg": 225})
    tw.run_cycle()
    tw.world_summary()
    tw.state()
    assert ([ss.raster_fingerprint(m) for m in ens.members],
            ss.raster_fingerprint(ens.p10), ss.raster_fingerprint(ens.p50),
            ss.raster_fingerprint(ens.p90), ens.t0_s, ens.incident_id) == fp
    assert json.dumps(inc.meta, sort_keys=True, default=str) == meta_before
    assert tw.last_result.incidents[0] is not inc   # the test map's own copy


# ---------------------------------------------------------------------------
# the live world never waits (SEMANTICS 16g)
# ---------------------------------------------------------------------------

class _GateTerrain(ss.StubTerrain):
    """A test-world terrain provider that blocks until released (a hung
    LFPS job, say)."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def tile(self, bbox, cell_m):
        self.entered.set()
        assert self.release.wait(30.0), "never released"
        return super().tile(bbox, cell_m)


def test_the_live_loop_never_waits_for_a_hung_test_cycle(tmp_path):
    gate = _GateTerrain()
    s = _stack(tmp_path / "w", sync=False, test_terrain=gate)
    daemon, tw = s["daemon"], s["tw"]
    tw.start()
    try:
        s["clock"].t = ss.T0 + 60.0
        _live_script(s, 0)
        daemon.run_cycle()
        s["test_clock"].t = ss.T0 + 100.0
        t0 = time.monotonic()
        code, out = tw.seed_create({"lat": ss.SEED_LOC.lat,
                                    "lon": ss.SEED_LOC.lon})
        assert code == 201 and time.monotonic() - t0 < 5.0   # never blocks
        assert gate.entered.wait(20.0)          # the test cycle is now HUNG
        # three live cycles run to completion while the test cycle hangs
        for k in (1, 2, 3):
            s["clock"].t = ss.T0 + 60.0 + k * ss.PERIOD_S
            _live_script(s, k)
            t0 = time.monotonic()
            res = daemon.run_cycle()
            assert res.t_s == s["clock"].t
            assert time.monotonic() - t0 < 20.0
        assert daemon.cycles_run == 4
        # a reset behind the hung cycle is queued, never blocking
        code, out = tw.reset({})
        assert code == 202 and out["queued"] is True
    finally:
        gate.release.set()
        tw.stop()
    assert tw.drill_state == "idle"              # the queued reset applied
    assert daemon.is_busy() is False


def test_a_failing_test_world_never_breaks_the_live_cycle(tmp_path, caplog):
    s = _stack(tmp_path / "f")
    daemon, tw = s["daemon"], s["tw"]

    def boom(*a, **kw):
        raise RuntimeError("test world exploded")
    tw.run_cycle = boom
    tw._drill["state"] = "active"
    s["clock"].t = ss.T0 + 60.0
    _live_script(s, 0)
    res = daemon.run_cycle()                     # no exception escapes
    assert res.ensembles and daemon.cycles_run == 1
    assert any("TEST world failed" in r.getMessage()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# restarts: the live world re-fires nothing; the drill comes back
# ---------------------------------------------------------------------------

def test_live_restart_refires_nothing_and_test_restart_restores_the_drill(
        tmp_path):
    state_dir = tmp_path / "rs"
    s3 = FakeS3()
    s = _stack(state_dir, s3=s3)
    daemon, tw = s["daemon"], s["tw"]
    for k in range(3):
        s["clock"].t = ss.T0 + 60.0 + k * ss.PERIOD_S
        _live_script(s, k)
        daemon.run_cycle()
        if k == 0:
            s["test_clock"].t = s["clock"].t + 30.0
            assert tw.seed_create({"lat": ss.SEED_LOC.lat,
                                   "lon": ss.SEED_LOC.lon,
                                   "intensity": "high",
                                   "wind_dir_deg": 225})[0] == 201
            assert tw.crews_place({"crew_id": "T1", "lat": 39.11,
                                   "lon": -121.0})[0] == 200
    live_sent = len(s["real"].sent)
    drill_sent = len(tw.drill_log.sent)
    assert live_sent >= 1 and drill_sent >= 1
    live_states = {k: r.state for k, r in s["engine"].states().items()}
    test_states = {k: r.state for k, r in tw.engine.states().items()}
    seeds = [e.ignition.incident_id for e in tw.monitor.seeded.entries()]
    test_crews = tw.crews.rows()

    # a process-equivalent restart of BOTH worlds (same disk, same S3)
    s2 = _stack(state_dir, s3=s3)
    daemon2, tw2 = s2["daemon"], s2["tw"]
    assert daemon2.restore() is True
    assert tw2.restored is True and tw2.drill_state == "active"
    assert [e.ignition.incident_id for e in tw2.monitor.seeded.entries()] \
        == seeds
    assert tw2.crews.rows() == test_crews
    assert tw2.drill_log.rows() == tw.drill_log.rows()
    s2["clock"].t = ss.T0 + 60.0 + 3 * ss.PERIOD_S
    _live_script(s2, 2)
    daemon2.run_cycle()
    assert s2["real"].sent == []                 # LIVE: re-fires nothing
    assert list(tw2.drill_log.sent) == []        # TEST: re-fires nothing
    assert {k: r.state for k, r in s2["engine"].states().items()} \
        == live_states
    assert {k: r.state for k, r in tw2.engine.states().items()} \
        == test_states
    assert not any(is_seeded_id(k[1]) for k in s2["engine"].states())


# ---------------------------------------------------------------------------
# migration of a pre-two-worlds live file (SEMANTICS 16a)
# ---------------------------------------------------------------------------

def _legacy_live_file(state_dir):
    """A LIVE state file as the pre-two-worlds build wrote it: a feed pair
    AND a seeded fire's rows, pairs, ledgered drill send, growth clock and
    plan stop, all in the live namespace."""
    seed_store = seeded.SeededIncidentStore()
    ign = seed_store.add(ss.seed(t_ignite_s=ss.T0 - 600.0))
    sid = ign.incident_id
    recs = {
        ("c1", "inc-1"): AlertRecord(client_id="c1", incident_id="inc-1",
                                     state=AlertState.EXTREME_24H,
                                     entered_at_s=ss.T0 - 300.0,
                                     last_eval_s=ss.T0 - 300.0,
                                     history=[(ss.T0 - 300.0,
                                               AlertState.EXTREME_24H)]),
        ("c1", sid): AlertRecord(client_id="c1", incident_id=sid,
                                 state=AlertState.EXTREME_24H,
                                 entered_at_s=ss.T0 - 300.0,
                                 last_eval_s=ss.T0 - 300.0,
                                 history=[(ss.T0 - 300.0,
                                           AlertState.EXTREME_24H)]),
    }
    ledger = {("c1", "inc-1", "extreme_24h"): ss.T0 - 300.0,
              ("c1", sid, "extreme_24h"): ss.T0 - 300.0}
    stop = lambda iid: PlannedStop(client_id="c1", incident_id=iid,
                                   arrive_s=ss.T0, service_start_s=ss.T0,
                                   depart_s=ss.T0 + 900.0,
                                   latest_depart_s=ss.T0 + 9000.0,
                                   margin_h=2.25)
    plan = DispatchPlan(computed_at_s=ss.T0 - 300.0,
                        plans={"G1": CrewPlan(crew_id="G1",
                                              stops=[stop(sid)],
                                              egress_zone_id="Z1")},
                        unserved=[UnservedJob("c2", sid, "capacity"),
                                  UnservedJob("c2", "inc-1", "capacity")])
    StateStore(state_dir).save(
        recs, ledger, plan, {"aoi": None},
        incident_growth={sid: {"acreage": 0.0,
                               "last_growth_s": ss.T0 - 300.0},
                         "inc-1": {"acreage": 500.0,
                                   "last_growth_s": ss.T0 - 300.0}},
        seeded_incidents=seed_store.rows())
    return sid


def test_a_pre_two_worlds_live_file_is_migrated_never_run_live(
        tmp_path, caplog):
    state_dir = tmp_path / "mig"
    sid = _legacy_live_file(state_dir)
    s = _stack(state_dir)
    daemon, tw, engine = s["daemon"], s["tw"], s["engine"]
    import logging
    with caplog.at_level(logging.WARNING):
        assert daemon.restore() is True
    # the LIVE world holds nothing seeded
    assert set(engine.states()) == {("c1", "inc-1")}
    assert set(engine.notified_ledger()) == {("c1", "inc-1", "extreme_24h")}
    assert set(engine.incident_growth()) == {"inc-1"}
    inc = s["monitor"].incumbent
    assert all(not is_seeded_id(st.incident_id)
               for cp in inc.plans.values() for st in cp.stops)
    assert [u.incident_id for u in inc.unserved] == ["inc-1"]
    assert len(s["monitor"].seeded) == 0
    # ... the TEST world has the drill
    assert tw.drill_state == "active"
    assert [i.incident_id for i in tw.monitor.seeded.active()] == [sid]
    assert tw.engine.states()[("c1", sid)].state is AlertState.EXTREME_24H
    assert ("c1", sid, "extreme_24h") in tw.engine.notified_ledger()
    assert any(is_seeded_id(st.incident_id)
               for cp in tw.monitor.incumbent.plans.values()
               for st in cp.stops)
    assert tw.migrated["seeds"] == 1 and tw.migrated["alert_pairs"] == 1
    # ... loudly
    assert daemon.migration_note and "MIGRATED 1 seeded" in \
        daemon.migration_note
    assert any("MIGRATED" in r.getMessage() for r in caplog.records)
    assert daemon.migration_note in daemon.state()["startup_notes"]
    test_doc = json.loads(tw.store.path.read_text("utf-8"))
    assert [r["incident_id"] for r in test_doc["seeded_incidents"]] == [sid]
    # the next live cycle: the seeded pair NEVER reaches the live notifier
    # (no drill all-clear on a client channel) and the live file is clean
    s["clock"].t = ss.T0 + 60.0
    _live_script(s, 0)
    live = daemon.run_cycle()
    assert all(not is_seeded_id(i.incident_id) for i in live.incidents)
    assert all(e[2] != sid for e in s["real"].sent)
    live_doc = json.loads(s["store"].path.read_text("utf-8"))
    assert "seeded_incidents" not in live_doc
    assert all(not is_seeded_id(r["incident_id"])
               for r in live_doc["alert_records"])
    # the drill carries on in the test world (sync: it ran after the cycle)
    assert sid in [i.incident_id for i in tw.last_result.incidents]


def test_split_legacy_seeds_is_a_no_op_on_a_live_only_file():
    loaded = {"alert_records": {}, "notified_ledger": {},
              "incumbent_plan": None, "incident_growth": {},
              "seeded_incidents": []}
    out, legacy = worlds.split_legacy_seeds(loaded)
    assert out is loaded and legacy is None


# ---------------------------------------------------------------------------
# the live monitor is locked; the test notifier holds no channel
# ---------------------------------------------------------------------------

def test_the_live_monitor_refuses_every_seed_and_unions_none(tmp_path):
    s = _stack(tmp_path / "l")
    mon = s["monitor"]
    assert mon.accepts_seeds is False
    for call in (lambda: mon.add_seed(ss.seed()),
                 lambda: mon.create_seed(39.0, -121.0, ss.T0),
                 lambda: mon.restore_seeds([{"incident_id": "seeded:x"}])):
        with pytest.raises(seeded.LiveWorldRefused) as ei:
            call()
        assert "/api/test/seed" in str(ei.value)
    # defense in depth: even a seed smuggled into the store is never unioned
    mon.seeded.add(ss.seed())
    s["clock"].t = ss.T0 + 60.0
    _live_script(s, 0)
    res = s["daemon"].run_cycle()
    assert all(not is_seeded_id(i.incident_id) for i in res.incidents)


def test_only_incidents_evaluates_just_those_pairs():
    """AlertEngine.evaluate(only_incidents=...) — the action re-run's
    ladder: the listed incidents' pairs evaluate, every other tracked pair
    is untouched (no transition, streaks and blind count as they were),
    and None is the unchanged full evaluation."""
    from wgresponse.alerts import AlertEngine
    from wgresponse.contracts import ThreatAssessment, ThreatEta
    cfg = ss.small_cfg()
    eng = AlertEngine(cfg, notifier=LogNotifier())

    def a(cid, iid, h):
        t = ss.T0 + h * 3600.0
        return ThreatAssessment(client_id=cid, incident_id=iid,
                                eta=ThreatEta(t, t, t), computed_at_s=ss.T0)
    eng.evaluate([a("c1", "inc-1", 2.0), a("c2", "seeded:x", 200.0)], ss.T0)
    before = {k: dataclasses.replace(r, history=list(r.history))
              for k, r in eng.states().items()}
    # the live pair's fire is gone (absent) AND the feeds are blind: a full
    # evaluation would freeze it (blind_cycles + 1) — only_incidents leaves
    # it exactly as it was, while the seeded pair escalates
    ev = eng.evaluate([a("c2", "seeded:x", 1.0)], ss.T0 + 300.0,
                      feeds_healthy=False, only_incidents={"seeded:x"})
    assert [(e.client_id, e.state) for e in ev] == [
        ("c2", AlertState.EXTREME_24H)]
    assert eng.states()[("c1", "inc-1")] == before[("c1", "inc-1")]
    eng.evaluate([], ss.T0 + 600.0, feeds_healthy=False)   # None: every pair
    assert eng.states()[("c1", "inc-1")].blind_cycles == 1


def test_drill_only_notifier_has_no_channel_and_ledgers():
    n = seeded.DrillOnlyNotifier()
    assert not any(hasattr(n, a) for a in ("_real", "real", "channel"))
    from wgresponse.contracts import AlertEvent
    ev = AlertEvent(t_s=ss.T0, client_id="c1", incident_id="inc-1",
                    prev=AlertState.NONE, state=AlertState.EXTREME_24H,
                    eta_p50_h=2.0, eta_p10_h=1.0, message="live fire, test map")
    assert n.send(ev, ss.clients()[0]) is True      # reported delivered
    (row,) = n.drill.rows()
    assert row["incident_id"] == "inc-1" and row["state"] == "extreme_24h"
    other = seeded.DrillLog()
    assert other.restore(n.drill.rows() + [{"bad": 1}]) == 1
