"""W1 seeded incidents — the watch daemon's /api/seed over real HTTP
(contracts SAFETY SEMANTICS 12; wgresponse.server routes to app.seed_api =
cli._WatchDaemon).

201 / 400 / 404 / 405 / 409 / 401 on every route; the seed persists at once
and wakes the loop; it reaches /api/state (source "seeded", a `seeds` key,
its domain and pair states) after the next cycle; DELETE retires it; the
routes answer 404 when no seed API is wired; `watch` wires the API and the
drill-safe notifier. Deterministic; no network; tests/seeded_stack.py.
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
from wgresponse import seeded, server  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402

KEY = "seed-test-key"
INSIDE = {"lat": ss.SEED_LOC.lat, "lon": ss.SEED_LOC.lon}


def _serve(daemon, wire=True):
    srv = server.serve(0, state_provider=daemon.state,
                       plan_provider=daemon.plan)
    if wire:
        srv.app.seed_api = daemon
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
                              state_dir=tmp_path / "state")
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# ---------------------------------------------------------------------------
# 201 + list + get
# ---------------------------------------------------------------------------

def test_post_seed_201_then_listed_and_fetched(api):
    code, out = api.call("POST", "/api/seed",
                         dict(INSIDE, intensity="high", wind_dir_deg=600,
                              note="drill A", created_by="colin"))
    assert code == 201, out
    iid = out["incident_id"]
    assert iid.startswith("seeded:") and out["ok"] is True
    seed = out["seed"]
    assert seed["status"] == "active" and seed["intensity"] == "high"
    assert seed["wind_dir_deg"] == 240.0            # 600 mod 360
    assert seed["weather_mode"] == "preset"
    assert seed["t_ignite_s"] == ss.T0              # the daemon clock
    assert seed["created_by"] == "colin" and seed["note"] == "drill A"
    assert seed["in_last_cycle"] is False           # takes effect next cycle
    assert out["persisted"] is True

    code, listing = api.call("GET", "/api/seed")
    assert code == 200
    assert [s["incident_id"] for s in listing["seeds"]] == [iid]
    assert listing["active"] == 1 and listing["max_active"] == 12

    code, one = api.call("GET", "/api/seed/" + quote(iid, safe=""))
    assert code == 200 and one["seed"]["incident_id"] == iid
    code, one = api.call("GET", "/api/seed/" + iid)  # unencoded ':' works too
    assert code == 200
    assert api.call("GET", "/api/seed/seeded:nope")[0] == 404


def test_live_weather_is_the_default(api):
    code, out = api.call("POST", "/api/seed", INSIDE)
    assert code == 201
    seed = out["seed"]
    assert seed["weather_mode"] == "live" and seed["intensity"] is None
    assert seed["created_by"] == "api"


def test_explicit_weather_gets_the_simulator_gust_factor(api):
    code, out = api.call("POST", "/api/seed", dict(
        INSIDE, wind_dir_deg=240,
        weather={"wind_speed_mps": 10.0, "rh_pct": 15, "temp_c": 30}))
    assert code == 201, out
    w = out["seed"]["weather"]
    assert w["wind_gust_mps"] == pytest.approx(13.0)
    assert out["seed"]["weather_mode"] == "explicit"


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
    code, out = api.call("POST", "/api/seed", body)
    assert code == 400, out
    assert needle in out["error"]
    assert len(api.monitor.seeded) == 0             # nothing half-applied


def test_post_seed_malformed_and_empty_bodies_400(api):
    assert api.call("POST", "/api/seed", raw="{not json")[0] == 400
    assert api.call("POST", "/api/seed", raw="")[0] == 400


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/seed", None),
    ("GET", "/api/seed/seeded:x", None),
    ("POST", "/api/seed", INSIDE),
    ("DELETE", "/api/seed/seeded:x", None),
])
def test_every_seed_route_is_authed(api, method, path, body):
    assert api.call(method, path, body, key=None)[0] == 401
    assert api.call(method, path, body, key="wrong")[0] == 401
    assert len(api.monitor.seeded) == 0


def test_dev_open_serves_without_a_key(api, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    assert api.call("POST", "/api/seed", INSIDE, key=None)[0] == 201
    monkeypatch.delenv("WGRESP_DEV_OPEN")
    assert api.call("GET", "/api/seed", key=None)[0] == 401   # fail closed


# ---------------------------------------------------------------------------
# DELETE, 404/405/409
# ---------------------------------------------------------------------------

def test_delete_retires_then_404_unknown_409_twice(api):
    iid = api.call("POST", "/api/seed", INSIDE)[1]["incident_id"]
    code, out = api.call("DELETE", f"/api/seed/{iid}?by=colin")
    assert code == 200, out
    assert out["seed"]["status"] == "retired"
    assert out["seed"]["retired_by"] == "colin"
    assert out["seed"]["retired_reason"] == "operator"
    assert out["retired_at_s"] == ss.T0
    assert "absence law" in out["ladder"]
    code, out = api.call("DELETE", f"/api/seed/{iid}")
    assert code == 409 and "already retired" in out["error"]
    assert api.call("DELETE", "/api/seed/seeded:nope")[0] == 404
    listing = api.call("GET", "/api/seed")[1]
    assert listing["active"] == 0
    assert listing["seeds"][0]["status"] == "retired"   # kept for audit


def test_method_mismatches_are_405(api):
    iid = api.call("POST", "/api/seed", INSIDE)[1]["incident_id"]
    assert api.call("POST", f"/api/seed/{iid}", INSIDE)[0] == 405
    assert api.call("DELETE", "/api/seed")[0] == 405
    assert api.call("DELETE", "/api/state")[0] == 405
    assert api.call("GET", "/api/seed/a/b")[0] == 404


def test_active_cap_is_409(api):
    api.monitor.seeded.max_active = 2
    for k in range(2):
        body = {"lat": INSIDE["lat"] + 0.01 * k, "lon": INSIDE["lon"]}
        assert api.call("POST", "/api/seed", body)[0] == 201
    code, out = api.call("POST", "/api/seed", INSIDE)
    assert code == 409 and "max 2" in out["error"]
    iid = api.call("GET", "/api/seed")[1]["seeds"][0]["incident_id"]
    assert api.call("DELETE", f"/api/seed/{iid}")[0] == 200
    assert api.call("POST", "/api/seed", INSIDE)[0] == 201    # room again


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
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


# ---------------------------------------------------------------------------
# persistence, wake, and the state surface
# ---------------------------------------------------------------------------

def test_post_persists_at_once_and_wakes_the_loop(api):
    assert not api.daemon._wake.is_set()
    iid = api.call("POST", "/api/seed", INSIDE)[1]["incident_id"]
    rows = StateStore(api.state_dir).load()["seeded_incidents"]
    assert [r["incident_id"] for r in rows] == [iid]   # before any cycle
    assert api.daemon._wake.is_set()
    api.daemon._wake.clear()
    api.call("DELETE", f"/api/seed/{iid}")
    rows = StateStore(api.state_dir).load()["seeded_incidents"]
    assert rows[0]["retired_at_s"] == ss.T0
    assert api.daemon._wake.is_set()


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


def test_seed_reaches_api_state_after_the_next_cycle(api):
    iid = api.call("POST", "/api/seed",
                   dict(INSIDE, intensity="high", wind_dir_deg=225))[1][
                       "incident_id"]
    api.clock.t = ss.T0 + 60.0
    api.daemon.run_cycle()
    code, state = api.call("GET", "/api/state")
    assert code == 200
    (row,) = [i for i in state["incidents"] if i["incident_id"] == iid]
    assert row["source"] == "seeded"
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

    api.call("DELETE", f"/api/seed/{iid}")
    api.clock.t += ss.PERIOD_S
    api.daemon.run_cycle()
    state = api.call("GET", "/api/state")[1]
    assert not [i for i in state["incidents"] if i["incident_id"] == iid]
    assert any("retired by api" in n for n in state["notes"])
    assert state["seeds"][0]["status"] == "retired"
    assert state["seeds"][0]["in_last_cycle"] is False


# ---------------------------------------------------------------------------
# `watch` wires the API and the drill-safe notifier
# ---------------------------------------------------------------------------

def test_cmd_watch_wires_the_seed_api_and_the_drill_safe_notifier(
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
    daemon = captured["httpd"].app.seed_api
    assert isinstance(daemon, cli_mod._WatchDaemon)
    assert isinstance(daemon._engine._notifier, seeded.SeedSafeNotifier)
    assert daemon._sleep == daemon._wakeable_sleep
    out = capsys.readouterr().out
    assert "/api/seed" in out and "seeded fires -> drill log only" in out
