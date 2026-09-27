"""W1 seeded incidents — the seeding API over real HTTP (contracts SAFETY
SEMANTICS 12), since TWO WORLDS (2026-09-27, SAFETY SEMANTICS 13) served by
the TEST world: /api/test/seed routes to app.test_api = cli._WatchDaemon,
which forwards to its worlds.TestWorld; the legacy /api/seed keeps GET as a
read-only alias of the test list and answers POST / DELETE with 409.

201 / 400 / 404 / 405 / 409 / 401 on every route; the seed persists at once
in the TEST namespace and re-runs the test world — it never wakes the live
loop and never writes a live byte; it reaches /api/test/state (source
"seeded", a `seeds` key, its domain and pair states) — never /api/state —
after the next cycle; DELETE retires it; the routes answer 404 when no test
API is wired; `watch` wires both worlds and the drill-only notifiers.
Deterministic; no network; tests/seeded_stack.py.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import cli as cli_mod  # noqa: E402
from wgresponse import seeded, server, worlds  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402

KEY = "seed-test-key"
INSIDE = {"lat": ss.SEED_LOC.lat, "lon": ss.SEED_LOC.lon}
SEED = "/api/test/seed"


def _serve(daemon, wire=True):
    srv = server.serve(0, state_provider=daemon.state,
                       plan_provider=daemon.plan)
    if wire:
        srv.app.seed_api = daemon
        srv.app.test_api = daemon
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread


def _caller(port):
    def call(method, path, body=None, key=KEY, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["X-Internal-API-Key"] = key
        data = raw if raw is not None else (
            None if body is None else json.dumps(body))
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            text = resp.read()
            return resp.status, (json.loads(text) if text else {})
        finally:
            conn.close()
    return call


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    clock = ss.SettableClock(ss.T0)
    daemon, monitor, feed, engine, notifier, store, weather, terrain = \
        ss.make_stack(tmp_path / "state", incidents=[], clock=clock)
    srv, thread = _serve(daemon)
    try:
        yield SimpleNamespace(call=_caller(srv.server_address[1]),
                              daemon=daemon, monitor=monitor, store=store,
                              engine=engine, clock=clock,
                              tw=daemon.test_world,
                              state_dir=tmp_path / "state")
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# ---------------------------------------------------------------------------
# 201 + list + get (the TEST world), and the /api/seed read-only alias
# ---------------------------------------------------------------------------

def test_post_seed_201_then_listed_and_fetched(api):
    code, out = api.call("POST", SEED,
                         dict(INSIDE, intensity="high", wind_dir_deg=600,
                              note="drill A", created_by="colin"))
    assert code == 201, out
    iid = out["incident_id"]
    assert iid.startswith("seeded:") and out["ok"] is True
    assert out["world"] == "test"
    seed = out["seed"]
    assert seed["status"] == "active" and seed["intensity"] == "high"
    assert seed["wind_dir_deg"] == 240.0            # 600 mod 360
    assert seed["weather_mode"] == "preset"
    assert seed["t_ignite_s"] == ss.T0              # the TEST clock
    assert seed["created_by"] == "colin" and seed["note"] == "drill A"
    assert seed["in_last_cycle"] is False           # no live cycle yet
    assert out["persisted"] is True
    assert out["drill"]["state"] == "active"        # the seed started a drill

    code, listing = api.call("GET", SEED)
    assert code == 200
    assert [s["incident_id"] for s in listing["seeds"]] == [iid]
    assert listing["active"] == 1 and listing["max_active"] == 12

    code, one = api.call("GET", SEED + "/" + quote(iid, safe=""))
    assert code == 200 and one["seed"]["incident_id"] == iid
    code, one = api.call("GET", f"{SEED}/{iid}")    # unencoded ':' works too
    assert code == 200
    assert api.call("GET", f"{SEED}/seeded:nope")[0] == 404

    # the legacy route: a READ-ONLY alias of the test list for one release
    code, alias = api.call("GET", "/api/seed")
    assert code == 200 and alias["alias_of"] == "/api/test/seed"
    assert alias["seeds"] == listing["seeds"]
    assert "read-only alias" in alias["deprecated"]
    code, one = api.call("GET", f"/api/seed/{iid}")
    assert code == 200 and one["seed"]["incident_id"] == iid
    assert api.call("GET", "/api/seed/seeded:nope")[0] == 404


def test_live_weather_is_the_default(api):
    code, out = api.call("POST", SEED, INSIDE)
    assert code == 201
    seed = out["seed"]
    assert seed["weather_mode"] == "live" and seed["intensity"] is None
    assert seed["created_by"] == "api"


def test_explicit_weather_gets_the_simulator_gust_factor(api):
    code, out = api.call("POST", SEED, dict(
        INSIDE, wind_dir_deg=240,
        weather={"wind_speed_mps": 10.0, "rh_pct": 15, "temp_c": 30}))
    assert code == 201, out
    w = out["seed"]["weather"]
    assert w["wind_gust_mps"] == pytest.approx(13.0)
    assert out["seed"]["weather_mode"] == "explicit"


# ---------------------------------------------------------------------------
# the LIVE world refuses (SAFETY SEMANTICS 13a)
# ---------------------------------------------------------------------------

def test_live_seed_post_and_delete_are_refused_with_the_pointer(api):
    code, out = api.call("POST", "/api/seed", INSIDE)
    assert code == 409
    assert "LIVE world accepts no seeded fires" in out["error"]
    assert out["use"] == "/api/test/seed" and out["world"] == "live"
    assert api.call("DELETE", "/api/seed/seeded:x")[0] == 409
    assert len(api.tw.monitor.seeded) == 0          # nothing lit anywhere
    assert len(api.monitor.seeded) == 0
    with pytest.raises(seeded.LiveWorldRefused):
        api.monitor.add_seed(ss.seed())             # the monitor is locked
    assert not api.store.path.exists()              # no live write
    assert not (api.state_dir / "test_world").exists()


# ---------------------------------------------------------------------------
# 400s
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, needle", [
    ([1, 2], "JSON object"),
    ({"lon": -121.0}, "required"),
    ({"lat": "39", "lon": -121.0}, "number"),
    ({"lat": True, "lon": -121.0}, "number"),
    ({"lat": 39.0, "lon": float("nan")}, "finite"),
    ({"lat": 39.0, "lon": float("inf")}, "finite"),
    ({"lat": 95.0, "lon": -121.0}, "out of range"),
    ({"lat": 38.5, "lon": -121.0}, "outside the watch AOI"),
    ({"lat": 39.0, "lon": -120.0}, "outside the watch AOI"),
    (dict(INSIDE, intensity="biblical"), "intensity"),
    (dict(INSIDE, wind_dir_deg="west"), "wind_dir_deg"),
    (dict(INSIDE, wind_dir_deg=float("nan")), "finite"),
    (dict(INSIDE, colour="red"), "unknown seed keys"),
    (dict(INSIDE, note="x" * 281), "note"),
    (dict(INSIDE, created_by=""), "created_by"),
    (dict(INSIDE, weather={"wind_speed_mps": 5}), "rh_pct"),
    (dict(INSIDE, weather={"wind_speed_mps": 5, "rh_pct": 10, "temp_c": 30,
                           "sun": 1}), "unknown weather keys"),
    (dict(INSIDE, weather={"wind_speed_mps": 500, "rh_pct": 10,
                           "temp_c": 30}), "wind_speed_mps"),
    (dict(INSIDE, intensity="high",
          weather={"wind_speed_mps": 5, "rh_pct": 10, "temp_c": 30}),
     "exclusive"),
])
def test_post_seed_400(api, body, needle):
    code, out = api.call("POST", SEED, body)
    assert code == 400, out
    assert needle in out["error"]
    assert len(api.tw.monitor.seeded) == 0          # nothing half-applied
    assert api.tw.drill_state == "idle"             # no drill started


def test_post_seed_malformed_and_empty_bodies_400(api):
    assert api.call("POST", SEED, raw="{not json")[0] == 400
    assert api.call("POST", SEED, raw="")[0] == 400


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/seed", None),
    ("GET", "/api/seed/seeded:x", None),
    ("POST", "/api/seed", INSIDE),
    ("DELETE", "/api/seed/seeded:x", None),
    ("GET", SEED, None),
    ("GET", f"{SEED}/seeded:x", None),
    ("POST", SEED, INSIDE),
    ("DELETE", f"{SEED}/seeded:x", None),
    ("GET", "/api/test/state", None),
    ("POST", "/api/test/crews/reset", {}),
    ("POST", "/api/test/crews/place", {"crew_id": "T1", "lat": 39.0,
                                       "lon": -121.0}),
    ("POST", "/api/test/reset", {}),
    ("GET", "/api/world/summary?world=test", None),
])
def test_every_seed_and_test_route_is_authed(api, method, path, body):
    assert api.call(method, path, body, key=None)[0] == 401
    assert api.call(method, path, body, key="wrong")[0] == 401
    assert len(api.tw.monitor.seeded) == 0
    assert api.tw.drill_state == "idle"


def test_dev_open_serves_without_a_key(api, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    assert api.call("POST", SEED, INSIDE, key=None)[0] == 201
    monkeypatch.delenv("WGRESP_DEV_OPEN")
    assert api.call("GET", SEED, key=None)[0] == 401    # fail closed


# ---------------------------------------------------------------------------
# DELETE, 404/405/409
# ---------------------------------------------------------------------------

def test_delete_retires_then_404_unknown_409_twice(api):
    iid = api.call("POST", SEED, INSIDE)[1]["incident_id"]
    code, out = api.call("DELETE", f"{SEED}/{iid}?by=colin")
    assert code == 200, out
    assert out["seed"]["status"] == "retired"
    assert out["seed"]["retired_by"] == "colin"
    assert out["seed"]["retired_reason"] == "operator"
    assert out["retired_at_s"] == ss.T0
    assert "absence law" in out["ladder"]
    code, out = api.call("DELETE", f"{SEED}/{iid}")
    assert code == 409 and "already retired" in out["error"]
    assert api.call("DELETE", f"{SEED}/seeded:nope")[0] == 404
    listing = api.call("GET", SEED)[1]
    assert listing["active"] == 0
    assert listing["seeds"][0]["status"] == "retired"   # kept for audit


def test_method_mismatches_are_405(api):
    iid = api.call("POST", SEED, INSIDE)[1]["incident_id"]
    assert api.call("POST", f"{SEED}/{iid}", INSIDE)[0] == 405
    assert api.call("DELETE", SEED)[0] == 405
    assert api.call("DELETE", "/api/state")[0] == 405
    assert api.call("GET", f"{SEED}/a/b")[0] == 404
    assert api.call("GET", "/api/seed/a/b")[0] == 404
    assert api.call("POST", f"/api/seed/{iid}", INSIDE)[0] == 405
    assert api.call("DELETE", "/api/seed")[0] == 405


def test_active_cap_is_409(api):
    api.tw.monitor.seeded.max_active = 2
    for k in range(2):
        body = {"lat": INSIDE["lat"] + 0.01 * k, "lon": INSIDE["lon"]}
        assert api.call("POST", SEED, body)[0] == 201
    code, out = api.call("POST", SEED, INSIDE)
    assert code == 409 and "max 2" in out["error"]
    assert "/api/test/seed/<id>" in out["error"]
    iid = api.call("GET", SEED)[1]["seeds"][0]["incident_id"]
    assert api.call("DELETE", f"{SEED}/{iid}")[0] == 200
    assert api.call("POST", SEED, INSIDE)[0] == 201    # room again


def test_seed_routes_404_without_a_seed_api(tmp_path, monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    daemon, *_ = ss.make_stack(tmp_path / "s", incidents=[])
    srv, thread = _serve(daemon, wire=False)          # `serve` / `simui`
    try:
        call = _caller(srv.server_address[1])
        code, out = call("GET", "/api/seed")
        assert code == 404 and "watch daemon" in out["error"]
        assert call("POST", "/api/seed", INSIDE)[0] == 404
        assert call("DELETE", "/api/seed/seeded:x")[0] == 404
        assert call("GET", "/api/seed", key=None)[0] == 401   # auth first
        code, out = call("GET", SEED)
        assert code == 404 and "watch daemon" in out["error"]
        assert call("POST", SEED, INSIDE)[0] == 404
        assert call("GET", "/api/test/state")[0] == 404
        assert call("GET", "/api/world/summary?world=test")[0] == 404
        assert call("GET", SEED, key=None)[0] == 401          # auth first
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# ---------------------------------------------------------------------------
# persistence (the TEST namespace), never a live wake, and the state surface
# ---------------------------------------------------------------------------

def test_post_persists_at_once_and_never_wakes_the_live_loop(api):
    assert not api.daemon._wake.is_set()
    iid = api.call("POST", SEED, INSIDE)[1]["incident_id"]
    test_dir = api.state_dir / worlds.TEST_NAMESPACE
    rows = StateStore(test_dir).load()["seeded_incidents"]
    assert [r["incident_id"] for r in rows] == [iid]   # before any cycle
    assert not api.daemon._wake.is_set()            # the live loop: untouched
    assert not api.store.path.exists()              # no live file written
    api.call("DELETE", f"{SEED}/{iid}")
    rows = StateStore(test_dir).load()["seeded_incidents"]
    assert rows[0]["retired_at_s"] == ss.T0
    assert not api.daemon._wake.is_set()
    assert not api.store.path.exists()


def test_wakeable_sleep_is_the_live_default_and_returns_on_wake(tmp_path):
    daemon, *_ = ss.make_stack(tmp_path / "s", incidents=[])
    live = cli_mod._WatchDaemon(
        monitor=daemon._monitor, engine=daemon._engine,
        registry=daemon._registry, tracker=None, store=daemon._store,
        cfg=daemon._cfg, aoi=ss.AOI, period_s=300.0)       # default sleep
    assert live._sleep == live._wakeable_sleep
    threading.Timer(0.2, live.wake).start()
    t0 = time.monotonic()
    live._sleep(30.0)
    assert time.monotonic() - t0 < 10.0
    assert not live._wake.is_set()                   # consumed
    t0 = time.monotonic()
    live._sleep(0.3)                                 # un-woken: full duration
    assert time.monotonic() - t0 >= 0.25


def test_seed_reaches_the_test_state_never_the_live_state(api):
    iid = api.call("POST", SEED,
                   dict(INSIDE, intensity="high", wind_dir_deg=225))[1][
                       "incident_id"]
    api.clock.t = ss.T0 + 60.0
    api.daemon.run_cycle()
    code, state = api.call("GET", "/api/test/state")
    assert code == 200 and state["world"] == "test"
    assert state["banner"] == worlds.TEST_BANNER
    (row,) = [i for i in state["incidents"] if i["incident_id"] == iid]
    assert row["source"] == "seeded" and row["world"] == "test"
    assert row["acreage"] == 0.0 and row["containment_pct"] is None
    assert row["seed"]["weather_mode"] == "preset"
    assert "FROM 225" in row["seed"]["weather_label"]
    (srow,) = state["seeds"]
    assert srow["in_last_cycle"] is True and srow["domain"]["cells"] > 0
    assert srow["domain"]["clients"] == 2
    assert srow["pairs"].get("extreme_24h", 0) >= 1
    assert any(c["state"] == "extreme_24h" for c in state["clients"])
    assert any(n.startswith(f"seeded incident {iid}: weather preset")
               for n in state["notes"])
    assert state["drill_log"] and all(
        line["incident_id"] == iid for line in state["drill_log"])
    # the LIVE world never saw it
    code, live = api.call("GET", "/api/state")
    assert code == 200 and "seeds" not in live
    assert not [i for i in live["incidents"] if i["incident_id"] == iid]
    assert all(c["state"] == "none" for c in live["clients"])

    api.call("DELETE", f"{SEED}/{iid}")
    api.clock.t += ss.PERIOD_S
    api.daemon.run_cycle()
    state = api.call("GET", "/api/test/state")[1]
    assert not [i for i in state["incidents"] if i["incident_id"] == iid]
    assert state["seeds"][0]["status"] == "retired"
    assert state["seeds"][0]["in_last_cycle"] is False


# ---------------------------------------------------------------------------
# `watch` wires both worlds and the drill-only notifiers
# ---------------------------------------------------------------------------

def test_cmd_watch_wires_the_test_world_and_the_drill_only_notifiers(
        tmp_path, monkeypatch, capsys):
    from wgresponse import server as server_mod
    from wgresponse import weather as weather_mod
    captured = {}
    real_serve = server_mod.serve

    def spy_serve(*a, **kw):
        captured["httpd"] = real_serve(*a, **kw)
        return captured["httpd"]

    monkeypatch.setattr(server_mod, "serve", spy_serve)
    monkeypatch.setattr(cli_mod, "_live_feed_stack",
                        lambda key: ([ss.ScriptedFeed([])], []))
    monkeypatch.setattr(cli_mod, "_watch_terrain",
                        lambda args, sd: (ss.StubTerrain(), ["stub terrain"]))
    monkeypatch.setattr(weather_mod, "NwsWeather",
                        lambda cfg=None: ss.StubWeather())
    monkeypatch.delenv("WGRESP_STATE_S3", raising=False)
    rc = cli_mod.main(["watch", "--aoi", "-121.2,38.8,-120.8,39.2",
                       "--state-dir", str(tmp_path / "st"), "--port", "0",
                       "--dry-notify", "--max-cycles", "1"])
    assert rc == 0
    app = captured["httpd"].app
    daemon = app.seed_api
    assert isinstance(daemon, cli_mod._WatchDaemon)
    assert app.test_api is daemon
    # the live notifier keeps the seeded wrapper (defense in depth) ...
    assert isinstance(daemon._engine._notifier, seeded.SeedSafeNotifier)
    assert daemon._monitor.accepts_seeds is False            # locked live
    assert daemon._sleep == daemon._wakeable_sleep
    # ... and the TEST world's notifier holds no channel at all
    tw = daemon.test_world
    assert isinstance(tw, worlds.TestWorld)
    assert isinstance(tw.engine._notifier, seeded.DrillOnlyNotifier)
    assert tw.store.path.parent == tmp_path / "st" / "test_world"
    assert tw._weather is not daemon._monitor._weather      # own instance
    assert tw._thread is not None and not tw._thread.is_alive()   # stopped
    out = capsys.readouterr().out
    assert "/api/test/seed" in out and "seeded fires -> drill log only" in out
    assert "the TEST map's alerts -> drill log only" in out
    assert "/world?world=test" in out and "409" in out
