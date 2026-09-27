"""Tests for wgresponse.server — loopback only, no third-party deps.

A ThreadingHTTPServer is started on an ephemeral port per fixture; auth env
vars are managed with monkeypatch so tests are order-independent.
"""
import http.client
import json
import threading

import pytest

from wgresponse import server
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon, ResponseConfig
from wgresponse.sim import _FallbackTracker, jsonable

KEY = "test-key-123"


class _Ctx:
    def __init__(self, srv, thread, tracker):
        self.srv = srv
        self.thread = thread
        self.tracker = tracker
        self.port = srv.server_address[1]

    def request(self, method, path, body=None, key=None, raw_body=None,
                headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            hdrs = dict(headers or {})
            if key is not None:
                hdrs["X-Internal-API-Key"] = key
            payload = raw_body
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
            if payload is not None:
                hdrs.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def get_json(self, path, key=None):
        status, data = self.request("GET", path, key=key)
        return status, (json.loads(data) if data else None)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    tracker = _FallbackTracker(ResponseConfig())

    def state():
        return {
            "incidents": [{"incident_id": "inc-1", "name": "Test Fire"}],
            "clients": [{"client_id": "C01", "state": "extreme_24h",
                         "lat": 34.2, "lon": -117.6}],
            "crews": [jsonable(c) for c in tracker.crews()],
            "plan": {"computed_at_s": 1.0, "plans": {}},
            "etas": {"C01|inc-1": {"eta_p10_s": float("inf"), "eta_p50_s": 123.0}},
        }

    srv = server.serve(0, state_provider=state, beacon_sink=tracker)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    c = _Ctx(srv, thread, tracker)
    yield c
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_state_requires_key(ctx):
    status, _ = ctx.get_json("/api/state")
    assert status == 401


def test_state_wrong_key_rejected(ctx):
    status, _ = ctx.get_json("/api/state", key="wrong")
    assert status == 401


def test_state_with_key_ok(ctx):
    status, data = ctx.get_json("/api/state", key=KEY)
    assert status == 200
    assert data["incidents"][0]["incident_id"] == "inc-1"
    assert data["clients"][0]["state"] == "extreme_24h"
    # inf must serialize to null, never Infinity
    assert data["etas"]["C01|inc-1"]["eta_p10_s"] is None


def test_fail_closed_when_key_unset(ctx, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    status, _ = ctx.get_json("/api/state", key="anything")
    assert status == 401


def test_dev_open_bypasses_auth(ctx, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    status, data = ctx.get_json("/api/state")
    assert status == 200
    assert "clients" in data


def test_healthz_always_open(ctx):
    status, data = ctx.get_json("/healthz")
    assert status == 200
    assert data == {"ok": True}


# ---------------------------------------------------------------------------
# Beacon ingest
# ---------------------------------------------------------------------------

def test_beacon_post_appears_in_state(ctx):
    beacon = {"crew_id": "G7", "lat": 34.21, "lon": -117.55,
              "t_s": 1_770_000_000.0, "status": "en_route", "speed_mps": 9.5}
    status, data = ctx.request("POST", "/api/beacon", body=beacon, key=KEY)
    assert status == 200
    assert json.loads(data)["ok"] is True
    # tracker got a real CrewBeacon
    crew = ctx.tracker.crew("G7")
    assert crew is not None
    assert crew.last_beacon.status == CrewStatus.EN_ROUTE
    assert crew.last_beacon.loc.lat == pytest.approx(34.21)
    # and it shows up through /api/state
    status, state = ctx.get_json("/api/state", key=KEY)
    assert status == 200
    ids = [c["crew_id"] for c in state["crews"]]
    assert "G7" in ids


def test_beacon_requires_auth(ctx):
    status, _ = ctx.request("POST", "/api/beacon",
                            body={"crew_id": "G1", "lat": 0, "lon": 0, "t_s": 1})
    assert status == 401


def test_beacon_malformed_json_400(ctx):
    status, data = ctx.request("POST", "/api/beacon", raw_body=b"{not json!",
                               key=KEY, headers={"Content-Type": "application/json"})
    assert status == 400
    assert "error" in json.loads(data)


@pytest.mark.parametrize("payload", [
    {},                                                       # missing everything
    {"crew_id": "", "lat": 1, "lon": 2, "t_s": 3},            # empty crew id
    {"crew_id": "G1", "lat": 999, "lon": 2, "t_s": 3},        # lat out of range
    {"crew_id": "G1", "lat": 1, "lon": 2, "t_s": "abc"},      # non-numeric time
    {"crew_id": "G1", "lat": 1, "lon": 2, "t_s": 3,
     "status": "napping"},                                    # unknown status
    [1, 2, 3],                                                # not an object
])
def test_beacon_invalid_payloads_400(ctx, payload):
    status, _ = ctx.request("POST", "/api/beacon", body=payload, key=KEY)
    assert status == 400


def test_parse_beacon_defaults():
    b = server.parse_beacon({"crew_id": " G2 ", "lat": 1.0, "lon": 2.0, "t_s": 5})
    assert isinstance(b, CrewBeacon)
    assert b.crew_id == "G2"
    assert b.status == CrewStatus.AVAILABLE
    assert b.accuracy_m == 30.0


# ---------------------------------------------------------------------------
# Other routes
# ---------------------------------------------------------------------------

def test_plan_endpoint(ctx):
    status, data = ctx.get_json("/api/plan", key=KEY)
    assert status == 200
    assert data["plan"]["computed_at_s"] == 1.0


def test_console_served(ctx):
    status, data = ctx.request("GET", "/console")
    assert status == 200
    text = data.decode("utf-8")
    assert "leaflet" in text.lower()
    assert "WG Response" in text


def test_world_viewer_served_and_summary_authed(ctx):
    """W6 thin world viewer (docs/LIVE_WORLD_PLAN.md): /world is open like
    /console (a key-prompt UI, no data); /api/world/summary stays authed and,
    with no world_provider attached, derives a domain-less summary from the
    state provider (the full suite lives in tests/test_world_viewer.py)."""
    status, data = ctx.request("GET", "/world")
    assert status == 200 and "WG Response — World" in data.decode("utf-8")
    status, _ = ctx.get_json("/api/world/summary")
    assert status == 401
    status, body = ctx.get_json("/api/world/summary", key=KEY)
    assert status == 200
    assert [r["incident_id"] for r in body["incidents"]] == ["inc-1"]
    assert body["incidents"][0]["domain"] is None


def test_unknown_path_404(ctx):
    status, _ = ctx.get_json("/nope", key=KEY)
    assert status == 404


def test_get_beacon_405(ctx):
    status, _ = ctx.get_json("/api/beacon", key=KEY)
    assert status == 405


def test_no_cors_headers(ctx):
    conn = http.client.HTTPConnection("127.0.0.1", ctx.port, timeout=10)
    try:
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        resp.read()
        assert resp.getheader("Access-Control-Allow-Origin") is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# healthz: watch-daemon cycle metadata (Sprint 2 C1)
# ---------------------------------------------------------------------------

def test_healthz_surfaces_cycle_age_and_count(ctx, monkeypatch):
    """When the state provider (the watch daemon) supplies last_cycle_age_s /
    cycles_run, the open healthz endpoint carries them — coarse liveness
    only, still no operator-event bodies."""
    state = {
        "degraded": False,
        "last_cycle_t_s": 1_780_000_000.0,
        "last_cycle_age_s": 12.5,
        "cycles_run": 7,
        "operator_events": [{"kind": "recovered"}],
    }
    ctx.srv.app.state_provider = lambda: state
    status, body = ctx.get_json("/healthz")
    assert status == 200
    assert body == {"ok": True, "degraded": False,
                    "last_cycle_t_s": 1_780_000_000.0,
                    "last_cycle_age_s": 12.5, "cycles_run": 7,
                    "operator_event_count": 1}


def test_healthz_surfaces_staleness_slo(ctx):
    """STALENESS SLO (tri-model review 2026-08-31 live-ops finding: the loop
    ran 33-47 min stale while degraded=false): the daemon's cycle_stale /
    stale_after_s / last_cycle_duration_s / poll_period_s pass through the
    open endpoint — surfaced, still a 200 (no restart storm on a slow loop)."""
    ctx.srv.app.state_provider = lambda: {
        "degraded": False, "last_cycle_t_s": 1_780_000_000.0,
        "last_cycle_age_s": 2_400.0, "cycles_run": 3,
        "cycle_stale": True, "stale_after_s": 900.0,
        "last_cycle_duration_s": 1_440.2, "poll_period_s": 300.0,
        "operator_events": []}
    status, body = ctx.get_json("/healthz")
    assert status == 200
    assert body["cycle_stale"] is True
    assert body["stale_after_s"] == 900.0
    assert body["last_cycle_duration_s"] == 1_440.2
    assert body["poll_period_s"] == 300.0


def test_healthz_ignores_malformed_cycle_keys(ctx):
    """Garbage-typed watch keys are dropped, never 500s (open endpoint)."""
    ctx.srv.app.state_provider = lambda: {
        "last_cycle_age_s": "soon", "cycles_run": True, "degraded": False}
    status, body = ctx.get_json("/healthz")
    assert status == 200
    assert body == {"ok": True, "degraded": False}


# ---------------------------------------------------------------------------
# Interactive simulator API — /api/sim/* + /simulator (SIM-3;
# docs/SIMULATOR_DESIGN.md). Sessions run on controlled synthetic tiles
# (sim_tile_dir seam) so results never depend on the committed real tiles.
# ---------------------------------------------------------------------------

import re
import time
from pathlib import Path

from wgresponse import geo
from wgresponse.simui import LOCATIONS
from wgresponse.terrain import SyntheticTerrain, load_ascii_tile, save_ascii_tile

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
SIMULATOR_HTML = CONSOLE_DIR / "simulator.html"

# Config-only speedups shared by the API tests (same as tests/test_simui.py).
SIM_FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0}

SIM_KEY = "boulder_co"
# Ignition 8 km upwind of center: the hazard wind then drives the fire back
# across the WUI hamlet (same geometry the SIM-2 engine tests pin).
SIM_IGN = geo.destination(LOCATIONS[SIM_KEY].center,
                          LOCATIONS[SIM_KEY].hazard_wind_dir_deg, 8_000.0)


def _sim_tile_dir(tmp_path, key=SIM_KEY, size_km=20.0, cell_m=500.0,
                  hamlet_half=2):
    """Controlled uniform-grass tile with a POROUS (checkerboard) developed
    hamlet at the center — the exact test geometry tests/test_simui.py uses
    (interleaved homes/fuel so the head fire runs through the interface).
    Also writes the FIRE ZONE extended companion <key>_ext.wgt (48 km @
    1200 m, hamlet at center + one ~15 km east) so /api/sim/new
    fire_zone='extended' exercises a real wide-area tile; standard sessions
    never look at it."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(
        bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = tmp_path / "sim_tiles"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))

    ext = SyntheticTerrain(seed=f"simui-test-ext|{key}", kind="grass").tile(
        geo.bbox_around([loc.center], pad_m=24_000.0), 1200.0)

    def _stamp(rc, half=2):
        for r in range(rc[0] - half, rc[0] + half):
            for c in range(rc[1] - half, rc[1] + half):
                if (0 <= r < ext.spec.nrows and 0 <= c < ext.spec.ncols
                        and (r + c) % 2 == 0):
                    ext.fuel.set(r, c, 91.0)

    _stamp(geo.latlon_to_rc(ext.spec, loc.center))
    _stamp(geo.latlon_to_rc(ext.spec,
                            geo.destination(loc.center, 90.0, 15_000.0)))
    save_ascii_tile(ext, str(d / f"{key}_ext.wgt"))
    return d


@pytest.fixture
def sim_ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    # Pin the roads loader to "no cached graph" for the same reason the tile
    # seam exists (header comment): these tests pin kinematics-sensitive
    # semantics (revoke/replan timing, statuses) that must not depend on the
    # concurrently-built data/roads/ caches. Sessions fall back to straight
    # tier-0 legs with a loud note; road-following itself is covered by
    # tests/test_simui.py's fake-roads + real-graph integration tests.
    try:
        import wgresponse.roads as _roads_mod
    except ImportError:
        pass                        # module absent: already straight-leg
    else:
        monkeypatch.setattr(_roads_mod, "load_road_graph",
                            lambda key, road_dir=None: None)
    tile_dir = _sim_tile_dir(tmp_path)
    srv = server.serve(0, sim_tile_dir=tile_dir, sim_synth_size_km=6.0)
    # short poll_interval: shutdown() latency is pure teardown overhead
    thread = threading.Thread(target=lambda: srv.serve_forever(0.05),
                              daemon=True)
    thread.start()
    c = _Ctx(srv, thread, None)
    c.tile_dir = tile_dir
    yield c
    srv.app.sim_manager.stop_all()
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def _sim_new(ctx, **kw):
    body = {"location": SIM_KEY, "clients": 6, "crews": 2, "seed": "t1",
            "cfg": dict(SIM_FAST)}
    body.update(kw)
    status, data = ctx.request("POST", "/api/sim/new", body=body, key=KEY)
    return status, (json.loads(data) if data else None)


def _sim_post(ctx, sid, verb, body):
    status, data = ctx.request("POST", f"/api/sim/{sid}/{verb}", body=body,
                               key=KEY)
    return status, (json.loads(data) if data else None)


def _sim_state(ctx, sid, query=""):
    return ctx.get_json(f"/api/sim/{sid}/state{query}", key=KEY)


# -- happy path ---------------------------------------------------------------

def test_sim_new_returns_session_and_state(sim_ctx):
    status, data = _sim_new(sim_ctx)
    assert status == 200
    assert data["session_id"] == "sim-1"
    st = data["state"]
    assert st["kind"] == "wgresponse-simui-state"
    assert st["seed"] == "t1"
    assert st["location"] == SIM_KEY
    assert len(st["clients"]) == 6 and len(st["crews"]) == 2
    assert st["tick"] == 0 and st["fires"] == []
    assert st["terrain"]["synthetic"] is False        # controlled tile file
    assert st["scoreboard"]["violations"] == 0
    # server-added clock keys: born paused, default 300x => one 900 s cycle/3 s
    assert st["running"] is False
    assert st["speed_x"] == 300.0
    assert st["tick_interval_s"] == pytest.approx(3.0)


def test_sim_happy_path_new_ignite_step_state(sim_ctx):
    """The ticket's happy path: new -> ignite -> step x3 -> state has fires/
    clients/scoreboard and the per-tick SAFETY AUDIT stayed at 0 violations."""
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    status, out = _sim_post(sim_ctx, sid, "ignite",
                            {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                             "intensity": "high"})
    assert status == 200
    assert out["ok"] is True and out["incident_id"] == "sim-f1"
    for _ in range(3):
        status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
        assert status == 200
        assert out["state"]["scoreboard"]["violations"] == 0
    st = out["state"]
    assert st["tick"] == 3
    assert [f["incident_id"] for f in st["fires"]] == ["sim-f1"]
    assert len(st["clients"]) == 6
    assert st["alert_feed"], "real AlertEngine events expected by tick 3"
    assert any(c["state"] != "none" for c in st["clients"])
    assert st["fire_bands"]["h24"], "p50 projection bands expected"
    # GET state returns the same picture
    status, got = _sim_state(sim_ctx, sid)
    assert status == 200
    assert got["tick"] == 3
    assert got["scoreboard"] == st["scoreboard"]


def test_sim_state_safety_surface_on_demand(sim_ctx):
    """?safety=1 adds the eroded-P10 rings (geo.erode_min over the live p10 —
    the corridor-check surface); the plain state never carries them."""
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite", {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                                       "intensity": "high"})
    _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    status, st = _sim_state(sim_ctx, sid)
    assert status == 200 and "safety_surface" not in st
    status, st = _sim_state(sim_ctx, sid, "?safety=1")
    assert status == 200
    rings = st["safety_surface"]
    assert rings, "high-intensity fire must yield eroded-P10 rings"
    for ring in rings:
        assert ring["incident_id"] == "sim-f1"
        assert ring["h"] in (6.0, 12.0, 24.0, 48.0, 72.0)
        assert len(ring["points"]) >= 3


# -- config: validate + echo + re-plan ---------------------------------------

def test_sim_config_echoes_applied_and_replans(sim_ctx):
    # 1 crew staged km out: still EN_ROUTE after tick 1, so the buffer change
    # revokes its unstarted stops and the NEXT tick re-plans (SIM-2 semantics).
    _, data = _sim_new(sim_ctx, clients=6, crews=1)
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite", {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                                       "intensity": "high"})
    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    st1 = out["state"]
    assert st1["plan"] is not None and st1["plan"]["stops"]
    t_plan1 = st1["plan"]["computed_at_s"]

    status, out = _sim_post(sim_ctx, sid, "config",
                            {"egress_buffer_min": 90, "slack_min": 20,
                             "speed": 600})
    assert status == 200
    assert out["applied"] == {"egress_buffer_min": 90.0, "slack_min": 20.0,
                              "speed": 600.0}
    st = out["state"]
    assert st["scoreboard"]["buffer_min"] == 90.0
    assert st["scoreboard"]["slack_min"] == 20.0
    assert st["speed_x"] == 600.0
    assert st["tick_interval_s"] == pytest.approx(1.5)   # 900 s / 600x

    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    st2 = out["state"]
    assert st2["plan"]["computed_at_s"] > t_plan1        # re-planned
    assert st2["scoreboard"]["violations"] == 0


@pytest.mark.parametrize("body,frag", [
    ({}, "at least one"),
    ({"foo": 1}, "unknown config keys"),
    ({"egress_buffer_min": -5}, ">= 0"),
    ({"slack_min": -1}, ">= 0"),
    ({"speed": 0}, "speed"),
    ({"speed": 601}, "speed"),
    ({"egress_buffer_min": "wide"}, "number"),
])
def test_sim_config_invalid_400(sim_ctx, body, frag):
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    status, out = _sim_post(sim_ctx, sid, "config", body)
    assert status == 400
    assert frag in out["error"]


def test_sim_config_rejects_nan(sim_ctx):
    """json.loads accepts bare NaN — the API must not (poisons safety math)."""
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    status, raw = sim_ctx.request("POST", f"/api/sim/{sid}/config",
                                  raw_body=b'{"slack_min": NaN}', key=KEY)
    assert status == 400
    assert "finite" in json.loads(raw)["error"]


# -- auth (same fail-closed posture as every data endpoint) --------------------

def test_sim_endpoints_require_key(sim_ctx):
    status, _ = sim_ctx.request("POST", "/api/sim/new",
                                body={"location": SIM_KEY, "clients": 1,
                                      "crews": 1})
    assert status == 401
    status, _ = sim_ctx.get_json("/api/sim/sim-1/state")
    assert status == 401


def test_sim_fail_closed_when_key_unset(sim_ctx, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    status, _ = sim_ctx.request("POST", "/api/sim/new",
                                body={"location": SIM_KEY, "clients": 1,
                                      "crews": 1}, key="anything")
    assert status == 401


def test_sim_dev_open_bypasses_auth(sim_ctx, monkeypatch):
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    status, data = sim_ctx.request(
        "POST", "/api/sim/new",
        body={"location": SIM_KEY, "clients": 2, "crews": 1, "seed": "d",
              "cfg": dict(SIM_FAST)})
    assert status == 200
    assert json.loads(data)["state"]["seed"] == "d"


# -- malformed / invalid requests ---------------------------------------------

def test_sim_malformed_json_400(sim_ctx):
    status, data = sim_ctx.request("POST", "/api/sim/new",
                                   raw_body=b"{not json!", key=KEY)
    assert status == 400
    assert "malformed JSON" in json.loads(data)["error"]
    _, out = _sim_new(sim_ctx)
    sid = out["session_id"]
    status, data = sim_ctx.request("POST", f"/api/sim/{sid}/ctl",
                                   raw_body=b"[[[", key=KEY)
    assert status == 400


def test_sim_new_validation_400s(sim_ctx):
    for body, frag in [
        ({"location": "narnia", "clients": 5, "crews": 1}, "location"),
        ({"location": SIM_KEY, "crews": 1}, "required integers"),
        ({"location": SIM_KEY, "clients": 0, "crews": 1}, "n_clients"),
        ({"location": SIM_KEY, "clients": 5, "crews": 99}, "n_crews"),
        ({"location": SIM_KEY, "clients": 5, "crews": 1,
          "cfg": {"nope": 1}}, "unknown ResponseConfig"),
        ({"location": SIM_KEY, "clients": 5, "crews": 1, "cfg": 7}, "cfg"),
    ]:
        status, data = sim_ctx.request("POST", "/api/sim/new", body=body,
                                       key=KEY)
        assert status == 400, body
        assert frag in json.loads(data)["error"], body


# -- FIRE ZONE (contracts wgresponse.simui; founder request 2026-07-10) -------

def test_sim_new_fire_zone_extended_happy_path(sim_ctx):
    """fire_zone='extended' loads the wide-area tile: echoed in state, the
    terrain bbox ~2-3x the standard session's, clients pinned to the central
    community window, a ~20 km-out ignition accepted, audit green; a client
    re-roll KEEPS the zone (the session re-configures with it)."""
    _, std = _sim_new(sim_ctx, seed="fz-std")
    assert std["state"]["fire_zone"] == "standard"        # default echoed
    b_s = std["state"]["terrain"]["bbox"]

    status, data = _sim_new(sim_ctx, seed="fz-ext", fire_zone="extended")
    assert status == 200
    st = data["state"]
    sid = data["session_id"]
    assert st["fire_zone"] == "extended"
    b_e = st["terrain"]["bbox"]
    for lo, hi in (("south", "north"), ("west", "east")):
        ratio = (b_e[hi] - b_e[lo]) / (b_s[hi] - b_s[lo])
        assert 2.0 <= ratio <= 3.0, f"extent ratio {ratio} out of ~2-3x"
    center = LOCATIONS[SIM_KEY].center
    for c in st["clients"]:
        d_km = geo.haversine_m(center, LatLon(c["lat"], c["lon"])) / 1000.0
        assert d_km <= 13.0, f"client {d_km:.2f} km out of the central window"

    # far ignition (outside the standard tile, on the extended one) + a step
    far = geo.destination(center, 0.0, 20_000.0)
    status, out = _sim_post(sim_ctx, sid, "ignite",
                            {"lat": far.lat, "lon": far.lon,
                             "intensity": "high"})
    assert status == 200 and out["incident_id"] == "sim-f1"
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    assert status == 200
    assert out["state"]["scoreboard"]["violations"] == 0
    assert out["state"]["fire_zone"] == "extended"

    # re-roll keeps the requested zone
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reroll_clients"})
    assert status == 200
    assert out["state"]["fire_zone"] == "extended"
    for c in out["state"]["clients"]:
        d_km = geo.haversine_m(center, LatLon(c["lat"], c["lon"])) / 1000.0
        assert d_km <= 13.0


def test_sim_new_fire_zone_invalid_400(sim_ctx):
    for fz in ("huge", "", 7, ["extended"]):
        status, data = sim_ctx.request(
            "POST", "/api/sim/new",
            body={"location": SIM_KEY, "clients": 2, "crews": 1,
                  "seed": "fz-bad", "cfg": dict(SIM_FAST), "fire_zone": fz},
            key=KEY)
        assert status == 400, fz
        err = json.loads(data)["error"]
        assert "fire_zone" in err and "extended" in err, err


# -- REGIONAL / MUTUAL-AID CREWS (contracts FOUNDER ROUND 5 SEMANTICS 9) ------

def test_sim_new_accepts_reinforcement_tiers(sim_ctx):
    """/api/sim/new accepts crews_1h / crews_3h: total crews = crews +
    crews_1h + crews_3h; in-zone crews AVAILABLE, reinforcements INBOUND on
    standby (available_at_s None until the first ignition)."""
    status, data = _sim_new(sim_ctx, crews=2, crews_1h=2, crews_3h=1)
    assert status == 200
    crews = data["state"]["crews"]
    assert len(crews) == 5
    assert sorted(c["region"] for c in crews) == \
        ["in_zone", "in_zone", "reg_1h", "reg_1h", "reg_3h"]
    for c in crews:
        if c["region"] == "in_zone":
            assert c["inbound"] is False and c["available_at_s"] is None
        else:
            assert c["inbound"] is True and c["status"] == "inbound"
            assert c["available_at_s"] is None and c["arrival_eta_s"] is None


def test_sim_new_reinforcement_alias_keys(sim_ctx):
    """The python-style n_crews_1h / n_crews_3h aliases are accepted too."""
    status, data = _sim_new(sim_ctx, crews=1, n_crews_1h=1, n_crews_3h=1)
    assert status == 200
    assert sorted(c["region"] for c in data["state"]["crews"]) == \
        ["in_zone", "reg_1h", "reg_3h"]


def test_sim_new_reinforcement_validation_400s(sim_ctx):
    for body, frag in [
        ({"location": SIM_KEY, "clients": 5, "crews": 1, "crews_1h": -1},
         "crews_1h"),
        ({"location": SIM_KEY, "clients": 5, "crews": 1, "crews_3h": 99},
         "crews_3h"),
        ({"location": SIM_KEY, "clients": 5, "crews": 1, "crews_1h": "x"},
         "must be integers"),
    ]:
        status, data = sim_ctx.request("POST", "/api/sim/new", body=body,
                                       key=KEY)
        assert status == 400, body
        assert frag in json.loads(data)["error"], body


def test_sim_reroll_preserves_reinforcement_tiers(sim_ctx):
    """reroll_clients re-configures the session but KEEPS the three tiers."""
    _, data = _sim_new(sim_ctx, crews=1, crews_1h=1, crews_3h=1, seed="rr")
    sid = data["session_id"]
    assert sorted(c["region"] for c in data["state"]["crews"]) == \
        ["in_zone", "reg_1h", "reg_3h"]
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reroll_clients"})
    assert status == 200
    crews = out["state"]["crews"]
    assert sorted(c["region"] for c in crews) == ["in_zone", "reg_1h", "reg_3h"]
    for c in crews:
        if c["region"] != "in_zone":
            assert c["inbound"] is True and c["available_at_s"] is None


# -- REALISTIC-FIRE PHYSICS TOGGLES (contracts SAFETY SEMANTICS 11 ember
# spotting + cfg.grass_ros_floor_frac grass head-ROS floor). /api/sim/new
# accepts `spotting` + `grass_realism` bools, BOTH default TRUE on this demo
# path; mapped into cfg_overrides and echoed as state.spotting/grass_realism.
# A direct simui.SimSession leaves both OFF (the engine suite is byte-identical).

def _burned_cells(state):
    """Burned-cell count from terrain.burned (rle01: 1-runs are the odd
    indices, first run counts 0-cells)."""
    return sum(state["terrain"]["burned"]["runs"][1::2])


def _threatened(state):
    return sum(1 for c in state["clients"] if c["eta_p50_s"] is not None)


def _earliest_p10(state):
    p10 = [c["eta_p10_s"] for c in state["clients"] if c["eta_p10_s"] is not None]
    return min(p10) if p10 else None


def test_sim_new_physics_default_on(sim_ctx):
    """No spotting/grass_realism in the body -> the demo default is realistic
    fire ON: the state echoes spotting=True and grass_realism=True so the UI
    toggle reflects it (a direct simui.SimSession leaves both OFF)."""
    status, data = _sim_new(sim_ctx)
    assert status == 200
    st = data["state"]
    assert st["spotting"] is True and st["grass_realism"] is True


def test_sim_new_physics_toggles_echo_independently(sim_ctx):
    """spotting / grass_realism are independent bools, each echoed in state."""
    _, off = _sim_new(sim_ctx, spotting=False, grass_realism=False)
    assert off["state"]["spotting"] is False
    assert off["state"]["grass_realism"] is False
    _, mix = _sim_new(sim_ctx, spotting=True, grass_realism=False)
    assert mix["state"]["spotting"] is True
    assert mix["state"]["grass_realism"] is False


def test_sim_new_physics_validation_400(sim_ctx):
    """Non-bool spotting / grass_realism is a loud 400 (never silently coerced;
    1/0/'true' are NOT booleans)."""
    for extra in ({"spotting": 1}, {"grass_realism": "yes"},
                  {"spotting": "true"}, {"grass_realism": 0}):
        status, data = _sim_new(sim_ctx, **extra)
        assert status == 400, extra
        assert "boolean" in data["error"], extra


def test_sim_new_physics_delta_on_vs_off(sim_ctx):
    """The demo default (physics ON) yields a MATERIALLY faster fire than an
    explicit OFF session over a short run, identical seed + geometry: more
    burned area, more homes threatened, and the earliest arrival (embers) lands
    sooner — while the crew-safety audit still reads 0 violations under the
    faster fire (SEMANTICS 10/11: spotting feeds the P10, protecting crews)."""
    def run(**flags):
        _, data = _sim_new(sim_ctx, seed="phys", clients=12,
                           cfg={"ensemble_n": 3, "grid_cell_m": 500.0,
                                "horizon_h": 12.0}, **flags)
        sid = data["session_id"]
        _sim_post(sim_ctx, sid, "ignite",
                  {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
        st = None
        for _ in range(3):
            _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
            st = out["state"]
            assert st["scoreboard"]["violations"] == 0
        return st

    st_on = run()                                    # default: physics ON
    st_off = run(spotting=False, grass_realism=False)
    assert st_on["spotting"] is True and st_off["spotting"] is False
    assert _burned_cells(st_on) > _burned_cells(st_off)      # faster fire
    assert _threatened(st_on) > _threatened(st_off)          # more homes threatened
    p_on, p_off = _earliest_p10(st_on), _earliest_p10(st_off)
    assert p_on is not None and p_off is not None
    assert p_on < p_off, (p_on, p_off)                       # embers land ahead


def test_sim_reset_and_reroll_preserve_physics(sim_ctx):
    """reset and reroll_clients re-configure the session but KEEP the physics
    toggles (asymmetric on/off), flowing through the entry cfg + session args."""
    _, data = _sim_new(sim_ctx, spotting=True, grass_realism=False, seed="pp")
    sid = data["session_id"]
    assert data["state"]["spotting"] is True
    assert data["state"]["grass_realism"] is False
    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reset"})
    assert out["state"]["spotting"] is True
    assert out["state"]["grass_realism"] is False
    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reroll_clients"})
    assert out["state"]["spotting"] is True
    assert out["state"]["grass_realism"] is False


def test_sim_ignite_rejections_400(sim_ctx):
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    # off-tile: loud rejection straight from the engine
    status, out = _sim_post(sim_ctx, sid, "ignite", {"lat": 0.0, "lon": 0.0})
    assert status == 400 and "outside" in out["error"]
    # non-burnable developed cell (scan the controlled tile for FBFM40 91)
    tile = load_ascii_tile(str(sim_ctx.tile_dir / f"{SIM_KEY}.wgt"))
    spec = tile.spec
    dev_rc = next((r, c) for r in range(spec.nrows) for c in range(spec.ncols)
                  if int(round(tile.fuel.get(r, c))) == 91)
    p = geo.rc_to_latlon(spec, *dev_rc)
    status, out = _sim_post(sim_ctx, sid, "ignite", {"lat": p.lat, "lon": p.lon})
    assert status == 400 and "non-burnable" in out["error"]
    # bad intensity / missing lat / non-finite lat
    status, out = _sim_post(sim_ctx, sid, "ignite",
                            {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                             "intensity": "apocalyptic"})
    assert status == 400 and "intensity" in out["error"]
    status, _ = _sim_post(sim_ctx, sid, "ignite", {"lon": SIM_IGN.lon})
    assert status == 400
    status, raw = sim_ctx.request("POST", f"/api/sim/{sid}/ignite",
                                  raw_body=b'{"lat": NaN, "lon": 1.0}', key=KEY)
    assert status == 400


def test_sim_ctl_unknown_action_400(sim_ctx):
    _, data = _sim_new(sim_ctx)
    status, out = _sim_post(sim_ctx, data["session_id"], "ctl",
                            {"action": "explode"})
    assert status == 400
    assert "run|pause|step|reset|reroll_clients" in out["error"]


def test_sim_unknown_session_404(sim_ctx):
    status, _ = _sim_state(sim_ctx, "sim-999")
    assert status == 404
    for verb, body in [("ignite", {"lat": 1, "lon": 2}),
                       ("config", {"slack_min": 5}),
                       ("ctl", {"action": "step"})]:
        status, out = _sim_post(sim_ctx, "sim-999", verb, body)
        assert status == 404, verb
        assert "unknown session" in out["error"]


def test_sim_wrong_method_405(sim_ctx):
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    status, out = sim_ctx.get_json(f"/api/sim/{sid}/ctl", key=KEY)
    assert status == 405
    status, out = _sim_post(sim_ctx, sid, "state", {})
    assert status == 405


# -- session registry: LRU cap of 4 -------------------------------------------

def test_sim_lru_cap_evicts_least_recently_used(sim_ctx):
    small = {"clients": 1, "crews": 1}
    sids = []
    for k in range(5):
        _, data = _sim_new(sim_ctx, seed=f"lru{k}", **small)
        sids.append(data["session_id"])
    # 5 created, cap 4: the oldest fell out
    assert _sim_state(sim_ctx, sids[0])[0] == 404
    for sid in sids[1:]:
        assert _sim_state(sim_ctx, sid)[0] == 200
    # touching sids[1] refreshes it; the next new evicts sids[2] instead
    assert _sim_state(sim_ctx, sids[1])[0] == 200
    _, data = _sim_new(sim_ctx, seed="lru5", **small)
    assert _sim_state(sim_ctx, sids[2])[0] == 404
    assert _sim_state(sim_ctx, sids[1])[0] == 200
    assert _sim_state(sim_ctx, data["session_id"])[0] == 200


# -- ctl: run/pause ticker, reset, reroll --------------------------------------

def test_sim_ctl_run_pause_background_ticker(sim_ctx):
    """run starts the per-session ticker thread (auto-tick at speed); pause
    stops it. Short replan period + max speed => 0.25 s floor ticks."""
    cfg = dict(SIM_FAST)
    cfg["replan_period_s"] = 60.0
    _, data = _sim_new(sim_ctx, clients=2, crews=1, cfg=cfg)
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "config", {"speed": 600})
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "run"})
    assert status == 200
    assert out["state"]["running"] is True
    assert out["state"]["tick_interval_s"] == pytest.approx(0.25)

    deadline = time.monotonic() + 15.0
    ticks = 0
    while time.monotonic() < deadline:
        _, st = _sim_state(sim_ctx, sid)
        ticks = st["tick"]
        if ticks >= 2:
            break
        time.sleep(0.1)
    assert ticks >= 2, "background ticker did not advance the session"

    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "pause"})
    assert status == 200
    assert out["state"]["running"] is False
    entry = sim_ctx.srv.app.sim_manager._sessions[sid]
    entry._thread.join(timeout=5.0)
    assert not entry._thread.is_alive()
    _, st1 = _sim_state(sim_ctx, sid)
    time.sleep(0.6)
    _, st2 = _sim_state(sim_ctx, sid)
    assert st2["tick"] == st1["tick"], "ticks advanced while paused"


def test_sim_ctl_reset_rearms_scenario(sim_ctx):
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite", {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                                       "intensity": "high"})
    _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reset"})
    assert status == 200
    st = out["state"]
    assert st["tick"] == 0 and st["fires"] == []
    assert st["seed"] == "t1"                       # same scenario, re-armed
    assert st["scoreboard"]["served"] == 0
    assert st["running"] is False


def test_sim_ctl_reroll_clients_new_seed_new_layout(sim_ctx):
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    before = [(c["lat"], c["lon"]) for c in data["state"]["clients"]]
    status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reroll_clients"})
    assert status == 200
    assert out["seed"] == "t1#r1"
    st = out["state"]
    assert st["seed"] == "t1#r1" and st["tick"] == 0
    after = [(c["lat"], c["lon"]) for c in st["clients"]]
    assert len(after) == len(before) and after != before
    # deterministic reroll chain: r2 next
    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "reroll_clients"})
    assert out["seed"] == "t1#r2"


# -- determinism through the HTTP surface --------------------------------------

def test_sim_api_scripted_session_reproducible(sim_ctx):
    """Same seed + same command script through the API => byte-identical
    state (rule 3), modulo the server's session id."""
    def run(seed_note):
        _, data = _sim_new(sim_ctx, seed="repro")
        sid = data["session_id"]
        _sim_post(sim_ctx, sid, "ignite", {"lat": SIM_IGN.lat,
                                           "lon": SIM_IGN.lon,
                                           "intensity": "high"})
        _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
        _sim_post(sim_ctx, sid, "config", {"egress_buffer_min": 45})
        _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
        _, st = _sim_state(sim_ctx, sid, "?safety=1")
        st.pop("session_id")
        return st

    a, b = run("a"), run("b")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# -- /simulator page + structural conformance ----------------------------------

def test_simulator_page_served_openly(sim_ctx):
    status, data = sim_ctx.request("GET", "/simulator")   # no key: open page
    assert status == 200
    text = data.decode("utf-8")
    assert "WG Simulator" in text
    assert "leaflet" in text.lower()


def test_simulator_html_controls_present():
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    for marker in (
            'id="loc"', 'id="clients"', 'id="reroll"', 'id="crews"',
            'id="int-low"', 'id="int-moderate"', 'id="int-high"',
            'id="int-extreme"', 'id="wind"', 'id="buffer"', 'id="slack"',
            'id="run"', 'id="pause"', 'id="step"', 'id="reset"', 'id="speed"',
            'id="seed"', 'id="copyseed"', 'id="safetytoggle"',
            'id="sb_violations"', 'id="sb_withdrawn"', 'id="alertlist"',
            'id="itinlist"', 'id="synthbanner"', 'id="ignitehint"',
            'id="withdrawbanner"'):
        assert marker in html, marker
    # the five design-doc locations
    for key in ("malibu_ca", "paradise_ca", "santa_rosa_ca", "boulder_co",
                "park_city_ut"):
        assert f'value="{key}"' in html, key
    # THE SAFETY SLIDER: 10-120 min, default 30, labeled as the explorer
    assert 'id="buffer" min="10" max="120" step="5" value="30"' in html
    assert "Safety threshold explorer" in html
    assert "Crews clear" in html
    # slack 0-60, clients 1-200, crews 1-12, speed 1-600
    assert 'id="slack" min="0" max="60"' in html
    assert 'id="clients" min="1" max="200"' in html
    assert 'id="crews" min="1" max="12"' in html
    assert 'id="speed" min="1" max="600"' in html
    # wired to the sim API + adaptive 1 s polling while running
    for frag in ('"/api/sim/new"', "/ignite", "/config", "/ctl", "/state",
                 "POLL_MS", "safety_surface", "SYNTHETIC",
                 # safety-withdrawal observability (state.withdrawn +
                 # scoreboard.withdrawn + notes banner + itinerary badge)
                 "SAFETY WITHDRAWAL", "policy_superseded", "deficit_min"):
        assert frag in html, frag


def test_simulator_html_fire_visuals_read_instantly():
    """Founder UX contract (contracts.py STATE VISUAL CONTRACT, 2026-07-10):
    the fire must read at a glance — dark-red burned fill that grows tick
    over tick, plus h1/h5/h24 projection bands with the agreed border colors
    (RED <1 h, ORANGE <5 h, YELLOW <24 h), updated IN PLACE (no flicker)."""
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    # state.fire_bands {h1,h5,h24} -> the three projection band layers
    for frag in ("fire_bands", '"h1"', '"h5"', '"h24"'):
        assert frag in html, frag
    # band colors + weights: RED 3px / ORANGE 2.5px / YELLOW 2px solid borders
    for frag in ('color: "#e53935", weight: 3.0',
                 'color: "#ff8c1a", weight: 2.5',
                 'color: "#f2b04c", weight: 2.0',
                 "fillOpacity: 0.10", "fillOpacity: 0.06", "fillOpacity: 0.04"):
        assert frag in html, frag
    # the fire itself: state.burned rings -> dark-red fill, hot edge
    for frag in ("st.burned", '"#7f0f0f"', '"#ff3b30"', "fillOpacity: 0.55",
                 "BURNED_STYLE"):
        assert frag in html, frag
    # in-place layer updates + graceful degradation to the RLE mask
    for frag in ("setLatLngs", "syncRingPool", "burnedSegments"):
        assert frag in html, frag
    # old absolute-hour isochrone rings are GONE (bands replace them)
    assert "st.isochrones" not in html
    assert "ISO_DASH" not in html
    # legend replaced: burning-now + the three relative-time bands
    for frag in ("burning now", "reached in &lt;1 h (red)",
                 "reached in &lt;5 h (orange)", "reached in &lt;24 h (yellow)",
                 "emergency egress route"):
        assert frag in html, frag
    # pulsing fire-origin marker (CSS pulse + SMIL pulse in the offline shim)
    assert "fire-origin" in html
    assert "firepulse" in html


def test_simulator_html_crews_on_roads_and_animation():
    """Crews visibly DRIVE ROADS: route_polyline (solid, crew-colored) +
    egress_polyline (dashed amber emergency route), markers tweened ALONG the
    polyline between polls via requestAnimationFrame, and an honest
    straight-line chip whenever on_roads=false / no road route exists."""
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    for frag in ("route_polyline", "egress_polyline", "on_roads",
                 "emergency egress", "CREW_COLORS"):
        assert frag in html, frag
    # dashed amber egress styling
    assert '"#ffb300"' in html
    assert 'dashArray: "8 5"' in html
    # rAF tween along the road path (not a straight hop)
    for frag in ("requestAnimationFrame", "startCrewTween", "subPathAlong",
                 "projectOnPath", "tweenPointAt"):
        assert frag in html, frag
    # travel-direction arrow + deploying countdown chip + off-road fallback
    for frag in ("crew-arrow", "bearingDeg", "deploychip", "gel deploy",
                 "straight-line leg", "deploy_min"):
        assert frag in html, frag
    # defensive geometry decoding: contract fields may be absent/odd-shaped
    for frag in ("function pt(", "function ptList(", "function ringList("):
        assert frag in html, frag


def test_simulator_html_offline_shim_renders_new_layers():
    """Every new layer must render in the air-gapped SVG shim too: polygons
    (burned + bands), dashed polylines (egress), marker setLatLng for the
    rAF tween, setLatLngs for in-place updates, and className-aware markers
    (pulsing fire origin, safe-zone circle) — with no new external hosts."""
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    shim = html[html.index("installFallbackLeaflet"):
                html.index("const SIM_LOCATIONS")]
    for frag in ("setLatLng(", "setLatLngs(", "fire-origin", "zone-icon",
                 "stroke-dasharray", "_redrawNow", "animate"):
        assert frag in shim, frag
    # emptied pooled layers (setLatLngs([])) draw nothing instead of erroring
    assert "!sp.latlngs || !sp.latlngs.length" in shim
    # the shim stays self-contained: no external hosts beyond the SVG xmlns
    assert re.findall(r"https?://[A-Za-z0-9.-]+", shim) == ["http://www.w3.org"]


def test_simulator_html_offline_shim_matches_console_pattern():
    """Same air-gapped discipline as console/index.html: inline SVG shim
    installed as window.L only when the CDN is unreachable; ?nomap=1
    rehearsal switch; the shim itself touches no external host."""
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    assert "installFallbackLeaflet" in html
    assert 'typeof window.L === "undefined"' in html
    assert "_fallback: true" in html
    assert "no basemap offline" in html
    assert 'get("nomap")' in html
    for api in ("map:", "layerGroup:", "circleMarker:", "polygon:",
                "polyline:", "rectangle:", "marker:"):
        assert api in html, api
    shim = html[html.index("installFallbackLeaflet"):
                html.index("const SIM_LOCATIONS")]
    assert "unpkg.com" not in shim
    assert "openstreetmap" not in shim.lower()
    assert "tile." not in shim
    # basemap tileLayer call is guarded exactly like the console's
    idx = html.index("tile.openstreetmap.org")
    assert html.rindex("try", 0, idx) < idx < html.index("catch", idx)


def test_simulator_html_no_external_hosts_beyond_console():
    """The simulator may reference exactly the console's external hosts
    (unpkg Leaflet + OSM tiles) and nothing else."""
    sim_html = SIMULATOR_HTML.read_text(encoding="utf-8")
    con_html = (CONSOLE_DIR / "index.html").read_text(encoding="utf-8")
    hosts = lambda h: set(re.findall(r"https?://[A-Za-z0-9.-]+", h))
    assert hosts(sim_html) == hosts(con_html)
    # http://www.w3.org is the inert SVG namespace IDENTIFIER inside the
    # offline shim (never fetched); the console carries the same string.
    assert hosts(sim_html) == {"https://unpkg.com",
                               "https://tile.openstreetmap.org",
                               "http://www.w3.org"}


def test_simulator_html_fire_zone_control():
    """FIRE ZONE UI (founder request 2026-07-10): a compact zone selector
    next to the location dropdown, passed on session create, re-created on
    change (same UX as a location change), a small chip while extended is
    active, and the zone serialized into the copy-repro line."""
    html = SIMULATOR_HTML.read_text(encoding="utf-8")
    for frag in (
            'id="firezone"',
            'value="standard"', 'value="extended"',
            "Standard — 18 km community",
            "Extended — wide-area",
            'id="fzchip"', "extended wide-area",          # the active chip
            "fire_zone: $(\"firezone\").value",           # sent on create
            '$("firezone").addEventListener("change"',    # re-create on change
            'fire_zone=${s.fire_zone || "standard"}',     # repro line
    ):
        assert frag in html, frag
    # honesty on fallback: requested extended but standard loaded -> surfaced
    assert "extended fire zone unavailable" in html
    # the chip/select track what ACTUALLY loaded
    assert 'st.fire_zone || "standard"' in html


# ---------------------------------------------------------------------------
# LAW 1 TIME SCRUB server half (contracts FOUNDER ROUND 3 #1): GET
# /api/sim/<id>/state?tick=k returns the RECORDED tick-k snapshot verbatim, and
# POST /api/sim/<id>/ctl {action: goto, tick} returns the same for the UI
# slider. Byte-identical to the frame served live at tick k (minus the live-
# only server clock keys the scrub deliberately omits); evicted/future/non-
# integer -> 404; reset() clears the ring. (In-process state_at coverage lives
# in tests/test_simui.py.)
# ---------------------------------------------------------------------------

#: live-only keys SimSessionManager._state_payload adds to the pure
#: SimSession.state(); a scrubbed historical frame carries none of them.
_SCRUB_SERVER_KEYS = {"session_id", "running", "speed_x", "tick_interval_s"}


def _pure_frame(state):
    return {k: v for k, v in state.items() if k not in _SCRUB_SERVER_KEYS}


def _sim_step_capture(ctx, sid, n):
    """Step the session n times over HTTP; return {tick: pure live frame}."""
    frames = {}
    for _ in range(n):
        _, out = _sim_post(ctx, sid, "ctl", {"action": "step"})
        frames[out["state"]["tick"]] = _pure_frame(out["state"])
    return frames


def _js(obj):
    return json.dumps(obj, sort_keys=True)


def test_sim_scrub_get_tick_byte_identical(sim_ctx):
    """GET ?tick=k replays the frame served live at tick k VERBATIM (the pure
    recorded snapshot, without the live server clock keys)."""
    _, data = _sim_new(sim_ctx, clients=6, crews=2, seed="scrub")
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite",
              {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
    frames = _sim_step_capture(sim_ctx, sid, 6)
    _, live = _sim_state(sim_ctx, sid)
    assert live["history"] == {"first_tick": 1, "last_tick": 6,
                               "current_tick": 6}
    for k in range(1, 7):
        code, scrub = _sim_state(sim_ctx, sid, f"?tick={k}")
        assert code == 200, (k, scrub)
        assert _js(scrub) == _js(frames[k]), f"tick {k} not served verbatim"
        # a scrubbed frame is the pure recording: no live server clock keys,
        # its own history.current_tick pins the frame's tick
        assert not (_SCRUB_SERVER_KEYS & set(scrub))
        assert scrub["tick"] == k and scrub["history"]["current_tick"] == k


def test_sim_scrub_get_tick_errors(sim_ctx):
    """Evicted/future/tick-0/negative/non-integer ticks are all 404 (JSON
    error); no tick param -> the live state (unchanged); unknown session 404."""
    _, data = _sim_new(sim_ctx, clients=4, crews=1, seed="scruberr")
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite",
              {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
    for _ in range(3):
        _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    for q in ("?tick=99", "?tick=0", "?tick=-1", "?tick=abc", "?tick=3.5"):
        code, out = _sim_state(sim_ctx, sid, q)
        assert code == 404, (q, code)
        assert "error" in out
    assert _sim_state(sim_ctx, "sim-999", "?tick=1")[0] == 404   # unknown session
    # no tick param -> the live state (server clock keys present), never a 404
    code, live = _sim_state(sim_ctx, sid)
    assert code == 200 and live["tick"] == 3 and "session_id" in live


def test_sim_scrub_eviction_at_history_ticks(sim_ctx):
    """The snapshot ring is bounded by cfg.history_ticks: with 3, after 6
    steps only ticks [4,5,6] replay; the evicted tick 3 -> 404."""
    cfg = dict(SIM_FAST)
    cfg["history_ticks"] = 3
    _, data = _sim_new(sim_ctx, clients=4, crews=1, seed="eviction", cfg=cfg)
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite",
              {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
    for _ in range(6):
        _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    _, live = _sim_state(sim_ctx, sid)
    assert live["history"] == {"first_tick": 4, "last_tick": 6,
                               "current_tick": 6}
    assert _sim_state(sim_ctx, sid, "?tick=3")[0] == 404          # evicted
    for k in (4, 5, 6):
        assert _sim_state(sim_ctx, sid, f"?tick={k}")[0] == 200
    assert _sim_state(sim_ctx, sid, "?tick=7")[0] == 404          # future


def test_sim_scrub_reset_clears_history(sim_ctx):
    """ctl reset clears the scrub ring: a previously retrievable tick 404s and
    history goes back to first/last None."""
    _, data = _sim_new(sim_ctx, clients=4, crews=1, seed="scrubreset")
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite",
              {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
    for _ in range(3):
        _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
    assert _sim_state(sim_ctx, sid, "?tick=2")[0] == 200
    _sim_post(sim_ctx, sid, "ctl", {"action": "reset"})
    _, live = _sim_state(sim_ctx, sid)
    assert live["history"] == {"first_tick": None, "last_tick": None,
                               "current_tick": 0}
    assert _sim_state(sim_ctx, sid, "?tick=2")[0] == 404          # cleared


def test_sim_ctl_goto_returns_recorded_snapshot(sim_ctx):
    """POST ctl {action: goto, tick} returns the recorded tick-k snapshot (the
    same bytes as GET ?tick=k), is READ-ONLY (never advances the live clock),
    and shares the scrub error posture (missing tick 400; non-integer / future
    / unknown session 404)."""
    _, data = _sim_new(sim_ctx, clients=6, crews=2, seed="goto")
    sid = data["session_id"]
    _sim_post(sim_ctx, sid, "ignite",
              {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon, "intensity": "high"})
    frames = _sim_step_capture(sim_ctx, sid, 5)
    code, g = _sim_post(sim_ctx, sid, "ctl", {"action": "goto", "tick": 3})
    assert code == 200 and g["action"] == "goto" and g["tick"] == 3
    assert _js(g["state"]) == _js(frames[3])
    _, scrub = _sim_state(sim_ctx, sid, "?tick=3")
    assert _js(g["state"]) == _js(scrub)          # goto == GET ?tick
    # read-only: the live clock never advanced past the 5 real steps
    _, live = _sim_state(sim_ctx, sid)
    assert live["tick"] == 5
    # error posture
    assert _sim_post(sim_ctx, sid, "ctl", {"action": "goto"})[0] == 400
    assert _sim_post(sim_ctx, sid, "ctl",
                     {"action": "goto", "tick": "nope"})[0] == 404
    assert _sim_post(sim_ctx, sid, "ctl",
                     {"action": "goto", "tick": 99})[0] == 404
    assert _sim_post(sim_ctx, "sim-999", "ctl",
                     {"action": "goto", "tick": 1})[0] == 404
    # the unknown-action message now advertises goto
    _, out = _sim_post(sim_ctx, sid, "ctl", {"action": "explode"})
    assert "goto" in out["error"]


def test_scenario_presets_build_and_ignite(sim_ctx):
    """ONE-CLICK SCENARIOS (founder 2026-07-12): {scenario: KEY} fills the
    preset (location/clients/crews/reinforcement), ignites the preset fire on
    creation (deterministic seed), and echoes state; unknown key -> 400.
    Runs against the fixture's synthetic tile — the ignition search shrinks
    range until a burnable cell takes, so the invariant under test is
    'a preset session is born with its fire lit', not exact geometry."""
    import json as _json
    for key, loc in (("park_city_windstorm", "park_city_ut"),
                     ("boulder_chinook", "boulder_co")):
        status, raw = sim_ctx.request("POST", "/api/sim/new",
                                      body={"scenario": key}, key=KEY)
        assert status == 200, raw
        st = _json.loads(raw)["state"]
        assert st["location"] == loc
        assert len(st["clients"]) == 40
        assert len(st["fires"]) == 1, "preset fire must be lit on creation"
        assert len(st["crews"]) == 3, "2 in-zone + 1 inbound reinforcement"
    status, raw = sim_ctx.request("POST", "/api/sim/new",
                                  body={"scenario": "nope"}, key=KEY)
    assert status == 400


# -- ROADS LEGIBILITY (roads inspection 2026-09-25) ---------------------------

_MAP_VIEW_KEYS = {"moved_path", "jumped", "trail", "route_why", "egress_why",
                  "egress_preview", "queue"}


def test_sim_state_carries_display_only_map_view(sim_ctx):
    """Every crew in the /api/sim state carries the additive map_view block
    (the road path driven this tick, trail, jumps, queued legs, relocation
    preview, reasons) and the LAW 1 recorded snapshot serves it verbatim."""
    _, data = _sim_new(sim_ctx)
    sid = data["session_id"]
    for c in data["state"]["crews"]:
        assert set(c["map_view"]) == _MAP_VIEW_KEYS
        assert c["map_view"]["moved_path"] == [] and c["map_view"]["trail"] == []
    _sim_post(sim_ctx, sid, "ignite", {"lat": SIM_IGN.lat, "lon": SIM_IGN.lon,
                                       "intensity": "high"})
    live = None
    for _ in range(3):
        status, out = _sim_post(sim_ctx, sid, "ctl", {"action": "step"})
        assert status == 200
        live = out["state"]
        for c in live["crews"]:
            mv = c["map_view"]
            assert set(mv) == _MAP_VIEW_KEYS
            if mv["moved_path"]:
                # the drive ends where the crew is now
                assert mv["moved_path"][-1] == pytest.approx(
                    [c["lat"], c["lon"]], abs=1e-6)
    status, snap = _sim_post(sim_ctx, sid, "ctl", {"action": "goto",
                                                   "tick": live["tick"]})
    assert status == 200
    assert [c["map_view"] for c in snap["state"]["crews"]] == \
        [c["map_view"] for c in live["crews"]]
