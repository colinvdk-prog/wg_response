"""TWO WORLDS — the TEST map's API over real HTTP, and the pages
(contracts SAFETY SEMANTICS 13; founder ruling 2026-09-27).

/api/test/state (IDLE mirror of the live picture, then the drill's own
state), /api/test/crews/reset + /place (virtual crews, validated, never the
live tracker), /api/test/reset (back to IDLE, restart-safe), the
/api/world/summary world parameter (live default unchanged, test marked,
`rev`-based ?since=), every 400 / 404 / 405 on the /api/test routes, a drill
fire never appearing in ANY live route's bytes — and the pages at source
level (the tests/test_simulator_ui style): world.html's LIVE | TEST switch,
banner, amber frame, TEST-only seed control and retire buttons, distinct
test crews with ghosted live crews; index.html live-only; the simulator's
TEST sandbox chip.

Deterministic; no network; tests/seeded_stack.py.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import crews as crews_mod  # noqa: E402
from wgresponse import server, worlds  # noqa: E402
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402

KEY = "test-world-key"
ROOT = Path(__file__).resolve().parent.parent
WORLD_HTML = ROOT / "console" / "world.html"
CONSOLE_HTML = ROOT / "console" / "index.html"
SIM_HTML = ROOT / "console" / "simulator.html"
NEAR = {"lat": ss.SEED_LOC.lat, "lon": ss.SEED_LOC.lon}


def _caller(port):
    def call(method, path, body=None, raw=None, key=KEY):
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
            return resp.status, text
        finally:
            conn.close()

    def jcall(*a, **kw):
        code, text = call(*a, **kw)
        return code, (json.loads(text) if text else {})
    return call, jcall


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    cfg = ss.small_cfg()
    clock = ss.SettableClock(ss.T0 + 60.0)
    tracker = crews_mod.CrewTracker(cfg)
    tracker.ingest(CrewBeacon(crew_id="G1", loc=LatLon(39.10, -121.05),
                              t_s=clock.t - 30.0))
    tracker.ingest(CrewBeacon(crew_id="G9", loc=LatLon(39.12, -121.10),
                              t_s=clock.t - 3600.0))          # stale beacon
    daemon, monitor, feed, engine, notifier, store, weather, terrain = \
        ss.make_stack(tmp_path / "state", cfg=cfg, clock=clock,
                      tracker=tracker)
    srv = server.serve(0, state_provider=daemon.state, beacon_sink=tracker,
                       plan_provider=daemon.plan)
    srv.app.seed_api = daemon
    srv.app.test_api = daemon
    srv.app.world_provider = daemon.world_summary
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    call, jcall = _caller(srv.server_address[1])
    try:
        yield SimpleNamespace(call=call, jcall=jcall, daemon=daemon,
                              tw=daemon.test_world, tracker=tracker,
                              clock=clock, store=store, engine=engine,
                              state_dir=tmp_path / "state")
    finally:
        srv.shutdown()
        th.join(timeout=5)
        srv.server_close()


# ---------------------------------------------------------------------------
# /api/test/state: IDLE mirrors the live picture read-only
# ---------------------------------------------------------------------------

def test_idle_test_state_mirrors_the_live_picture_read_only(api):
    code, st = api.jcall("GET", "/api/test/state")
    assert code == 200 and st["world"] == "test" and st["live"] is False
    assert st["notes"] == ["no live cycle has completed yet"]
    api.daemon.run_cycle()
    code, st = api.jcall("GET", "/api/test/state")
    assert st["banner"] == worlds.TEST_BANNER
    assert st["drill"]["state"] == "idle" and st["cycles_run"] == 0
    assert [(i["incident_id"], i["world"], i["read_only"])
            for i in st["incidents"]] == [("inc-1", "live", True)]
    assert st["crews"] == []                       # no clone yet
    assert {c["crew_id"] for c in st["live_crews"]} == {"G1", "G9"}
    live = api.jcall("GET", "/api/state")[1]
    assert {c["client_id"]: c["state"] for c in st["clients"]} == \
        {c["client_id"]: c["state"] for c in live["clients"]}
    assert st["plan"] is None and st["seeds"] == []
    assert "no drill running" in st["notes"][0]
    # IDLE costs nothing: no test cycle ran, nothing was written
    assert not (api.state_dir / worlds.TEST_NAMESPACE).exists()


def test_world_summary_world_parameter(api):
    api.daemon.run_cycle()
    code, raw_default = api.call("GET", "/api/world/summary")
    assert code == 200
    code, raw_live = api.call("GET", "/api/world/summary?world=live")
    assert code == 200 and raw_live == raw_default   # the default is live
    live = json.loads(raw_default)
    assert "world" not in live and "rev" not in live  # live bytes unchanged
    assert api.call("GET", "/api/world/summary?world=mars")[0] == 400
    code, test = api.jcall("GET", "/api/world/summary?world=test")
    assert code == 200 and test["world"] == "test"
    assert test["banner"] == worlds.TEST_BANNER
    assert test["drill"]["state"] == "idle"
    assert [(r["incident_id"], r["world"], r["read_only"])
            for r in test["incidents"]] == [("inc-1", "live", True)]
    code, same = api.jcall(
        "GET", f"/api/world/summary?world=test&since={test['rev']}")
    assert same == {"unchanged": True, "t_s": test["t_s"],
                    "rev": test["rev"], "cycles_run": test["cycles_run"],
                    "world": "test"}
    # a seed moves the rev even at the same cycle time
    api.clock.t += 10.0
    assert api.jcall("POST", "/api/test/seed", NEAR)[0] == 201
    code, moved = api.jcall(
        "GET", f"/api/world/summary?world=test&since={test['rev']}")
    assert "unchanged" not in moved and moved["rev"] != test["rev"]
    assert any(r["source"] == "seeded" and r["world"] == "test"
               and r["read_only"] is False for r in moved["incidents"])
    # the live summary never saw it
    assert api.call("GET", "/api/world/summary")[1] == raw_default


# ---------------------------------------------------------------------------
# virtual crews
# ---------------------------------------------------------------------------

def test_crews_reset_starts_a_drill_and_clones_the_live_roster(api):
    # BEFORE any live cycle: the live tracker still says G9 is AVAILABLE
    # although its beacon is an hour old (only the live cycle's stale()
    # flips it) — the clone must read that, never flip it itself
    before = [(c.crew_id, c.status, c.last_beacon) for c in
              api.tracker.crews()]
    assert api.tracker.crew("G9").status is CrewStatus.AVAILABLE
    code, out = api.jcall("POST", "/api/test/crews/reset", raw="")
    assert code == 200, out                        # the body is optional
    assert out["drill"]["state"] == "active"
    rows = {c["crew_id"]: c for c in out["crews"]}
    assert set(rows) == {"G1", "G9"}
    assert all(c["virtual"] and c["world"] == "test"
               and c["origin"] == "live-snapshot" for c in rows.values())
    assert rows["G1"]["status"] == "available" and not rows["G1"]["cloned_stale"]
    # G9's live beacon is stale: the live dispatcher would not use it
    assert rows["G9"]["status"] == "unknown" and rows["G9"]["cloned_stale"]
    assert rows["G9"]["live_status"] == "available"
    # the clone only READ the live tracker (stale() flips statuses — never)
    assert [(c.crew_id, c.status, c.last_beacon) for c in
            api.tracker.crews()] == before
    assert api.tracker.crew("G9").status is CrewStatus.AVAILABLE
    # the live cycle then applies ITS OWN staleness, unaffected
    api.daemon.run_cycle()
    assert api.tracker.crew("G9").status is CrewStatus.UNKNOWN
    assert api.jcall("POST", "/api/test/crews/reset",
                     {"by": 7})[0] == 400
    assert api.jcall("POST", "/api/test/crews/reset",
                     {"colour": "red"})[0] == 400


@pytest.mark.parametrize("body, needle", [
    ([1], "JSON object"),
    ({"crew_id": "T1", "lat": 39.0, "lon": -121.0, "boss": 1}, "unknown"),
    ({"lat": 39.0, "lon": -121.0}, "crew_id"),
    ({"crew_id": "", "lat": 39.0, "lon": -121.0}, "crew_id"),
    ({"crew_id": "T1", "lat": "39", "lon": -121.0}, "number"),
    ({"crew_id": "T1", "lat": True, "lon": -121.0}, "number"),
    ({"crew_id": "T1", "lat": float("nan"), "lon": -121.0}, "finite"),
    ({"crew_id": "T1", "lat": 91.0, "lon": -121.0}, "out of range"),
    ({"crew_id": "T1", "lat": 39.0, "lon": -121.0, "status": "flying"},
     "status"),
    ({"crew_id": "T1", "lat": 39.0, "lon": -121.0, "name": "x" * 65},
     "name"),
])
def test_crews_place_400(api, body, needle):
    code, out = api.jcall("POST", "/api/test/crews/place", body)
    assert code == 400, out
    assert needle in out["error"]
    assert api.tw.drill_state == "idle"            # nothing started


def test_crews_place_stages_virtual_crews_never_the_live_tracker(api):
    api.daemon.run_cycle()
    code, out = api.jcall("POST", "/api/test/crews/place",
                          {"crew_id": "G1", "lat": 39.02, "lon": -121.03})
    assert code == 200, out
    assert out["crew"]["origin"] == "placed" and out["crew"]["virtual"]
    assert (out["crew"]["lat"], out["crew"]["lon"]) == (39.02, -121.03)
    code, out = api.jcall("POST", "/api/test/crews/place",
                          {"crew_id": "T9", "lat": 39.05, "lon": -121.0,
                           "status": "off_duty", "name": "Virtual Nine"})
    assert code == 200 and out["crew"]["status"] == "off_duty"
    st = api.jcall("GET", "/api/test/state")[1]
    assert {c["crew_id"] for c in st["crews"]} == {"G1", "G9", "T9"}
    # the LIVE tracker never heard of any of it
    assert api.tracker.crew("T9") is None
    assert api.tracker.crew("G1").loc() == LatLon(39.10, -121.05)
    live = api.jcall("GET", "/api/state")[1]
    assert {c["crew_id"] for c in live["crews"]} == {"G1", "G9"}
    assert all(c["lat"] != 39.02 for c in live["crews"])


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_wipes_back_to_idle_and_a_restart_stays_idle(api):
    api.daemon.run_cycle()
    assert api.jcall("POST", "/api/test/seed",
                     dict(NEAR, intensity="high", wind_dir_deg=225))[0] == 201
    api.clock.t += ss.PERIOD_S
    api.daemon.run_cycle()
    assert api.tw.cycles_run >= 1 and len(api.tw.drill_log.rows()) >= 1
    code, out = api.jcall("POST", "/api/test/reset", {"by": "colin"})
    assert code == 200 and out["drill"]["state"] == "idle"
    st = api.jcall("GET", "/api/test/state")[1]
    assert st["seeds"] == [] and st["crews"] == [] and st["drill_log"] == []
    assert st["cycles_run"] == 0
    doc = json.loads(api.tw.store.path.read_text("utf-8"))
    assert doc["meta"]["drill"]["state"] == "idle"
    assert doc["seeded_incidents"] == [] and doc["alert_records"] == []
    # restart: comes back IDLE
    tw2 = ss.make_test_world(api.state_dir, ss.small_cfg(), ss.clients())
    assert tw2.restore() is True and tw2.drill_state == "idle"
    # IDLE again = zero cost: the next live cycle runs no test cycle
    api.clock.t += ss.PERIOD_S
    api.daemon.run_cycle()
    assert api.tw.cycles_run == 0
    assert api.jcall("POST", "/api/test/reset", {"x": 1})[0] == 400


# ---------------------------------------------------------------------------
# route errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, path, body, code", [
    ("GET", "/api/test/reset", None, 405),
    ("GET", "/api/test/crews/reset", None, 405),
    ("GET", "/api/test/crews/place", None, 405),
    ("POST", "/api/test/state", {}, 405),
    ("DELETE", "/api/test/state", None, 405),
    ("DELETE", "/api/test/seed", None, 405),
    ("POST", "/api/test/seed/seeded:x", NEAR, 405),
    ("GET", "/api/test", None, 404),
    ("GET", "/api/test/nope", None, 404),
    ("POST", "/api/test/crews/nope", {}, 404),
    ("GET", "/api/test/seed/a/b", None, 404),
    ("DELETE", "/api/test/seed/seeded:nope", None, 404),
])
def test_test_route_errors(api, method, path, body, code):
    assert api.jcall(method, path, body)[0] == code


def test_test_route_bodies(api):
    assert api.jcall("POST", "/api/test/seed", raw="")[0] == 400
    assert api.jcall("POST", "/api/test/seed", raw="{nope")[0] == 400
    assert api.jcall("POST", "/api/test/crews/place", raw="")[0] == 400
    assert api.jcall("POST", "/api/test/reset", raw="{nope")[0] == 400
    assert api.jcall("POST", "/api/test/reset", raw="")[0] == 200


# ---------------------------------------------------------------------------
# a drill fire never appears in a single live byte
# ---------------------------------------------------------------------------

def test_a_drill_fire_never_reaches_any_live_route(api):
    api.daemon.run_cycle()
    assert api.jcall("POST", "/api/test/seed",
                     dict(NEAR, intensity="extreme",
                          wind_dir_deg=225))[0] == 201
    assert api.jcall("POST", "/api/test/crews/place",
                     {"crew_id": "T1", "lat": 39.0, "lon": -121.0})[0] == 200
    for _ in range(2):
        api.clock.t += ss.PERIOD_S
        api.daemon.run_cycle()
    st = api.jcall("GET", "/api/test/state")[1]
    assert any(i["source"] == "seeded" for i in st["incidents"])
    assert st["plan"] is not None
    for path in ("/api/state", "/api/plan", "/healthz",
                 "/api/world/summary", "/console", "/world"):
        code, raw = api.call("GET", path)
        assert code == 200, path
        if path.startswith("/api") or path == "/healthz":
            assert b"seeded:" not in raw, path
            assert b'"T1"' not in raw, path
    doc = api.store.path.read_bytes()
    assert b"seeded:" not in doc and b'"T1"' not in doc


# ---------------------------------------------------------------------------
# the pages (source level)
# ---------------------------------------------------------------------------

def _html(path=WORLD_HTML):
    return path.read_text(encoding="utf-8")


def _fn(html, head, stop="\n}\n"):
    i = html.index(head)
    return html[i:html.index(stop, i) + len(stop)]


def test_world_page_has_the_live_test_switch_live_by_default():
    html = _html()
    assert 'id="worldswitch"' in html
    assert re.search(r'data-world="live" class="on"', html)
    assert 'data-world="test"' in html
    assert 'let WORLD = QS.get("world") === "test" ? "test" : "live";' in html
    # the switch changes worlds; the page keeps it in the URL
    sw = _fn(html, "function setWorld(w)")
    assert 'u.searchParams.set("world", "test")' in sw and "tick()" in sw
    # the two data sources
    fa = _fn(html, "async function fetchAll()")
    assert '"/api/test/state"' in fa and '"/api/state"' in fa
    assert '"?world=test"' in fa and '"&world=test"' in fa


def test_world_page_test_map_is_unmistakable():
    html = _html()
    banner = ("TEST MAP — drills and simulations. Nothing here affects real "
              "conditions, crews or clients.")
    assert f'<div id="testbanner" role="alert">{banner}</div>' in html
    assert banner == worlds.TEST_BANNER
    css = html[:html.index("</style>")]
    assert re.search(r"#testbanner \{ display: none;", css)
    assert "body.test-world #testbanner { display: block; }" in css
    # the amber frame: over the map, never catching a click, TEST only
    fr = css[css.index("#testframe {"):]
    fr = fr[:fr.index("}") + 1]
    assert "display: none" in fr and "border: 6px solid var(--amber)" in fr
    assert "pointer-events: none" in fr
    assert "body.test-world #testframe { display: block; }" in css
    assert "--amber: #ffb300" in css
    assert 'id="testframe"' in html
    aw = _fn(html, "function applyWorld()")
    assert 'document.body.classList.toggle("test-world", test)' in aw
    assert "TEST MAP" in aw


def test_world_page_seed_control_exists_only_on_the_test_map():
    html = _html()
    css = html[:html.index("</style>")]
    assert re.search(r"#drillwrap \{ display: none;", css)
    assert "body.test-world #drillwrap { display: block; }" in css
    drill = html[html.index('<div id="drillwrap">'):html.index('<div id="rankwrap">')]
    for frag in ('id="seedbtn"', 'id="seedform"', 'id="seedint"',
                 'id="seedwind"', 'id="seedgo"', 'id="crewresetbtn"',
                 'id="testresetbtn"', 'id="seedlist"', 'id="drilllog"'):
        assert frag in drill, frag
    for opt in ("low", "moderate", "high", "extreme"):
        assert f'<option value="{opt}"' in drill
    # EVERY drill handler refuses to act off the TEST map
    guard = 'if (WORLD !== "test"'
    for head in ('$("seedbtn").addEventListener', 'map.on("click", (ev) =>',
                 '$("seedgo").addEventListener', "async function retireSeed(",
                 '$("crewresetbtn").addEventListener',
                 '$("testresetbtn").addEventListener',
                 "function renderDrillPanel()"):
        i = html.index(head)
        assert guard in html[i:i + 400], head
    # click the map -> intensity -> seed, against the TEST route only
    assert 'apiSend("POST", "/api/test/seed", body)' in html
    assert 'apiSend("DELETE", "/api/test/seed/"' in html
    assert '"/api/test/crews/reset"' in html and '"/api/test/reset"' in html
    assert not re.search(r'apiSend\("(POST|DELETE)", "/api/seed', html)
    # leaving the TEST map disarms the seed control
    assert "if (!test) armSeed(false);" in _fn(html, "function applyWorld()")
    # retire buttons: on seeded rows, TEST map only
    rk = _fn(html, "function renderRankList()")
    assert 'WORLD === "test" && r.source === "seeded"' in rk
    assert 'data-retire=' in rk
    # the offline shim delivers map clicks with a latlng (seeding offline)
    shim = html[html.index("installFallbackLeaflet"):
                html.index("// world viewer app")]
    assert 'this._fire("click", { latlng: { lat: ll[0], lng: ll[1] } })' in shim


def test_world_page_test_crews_distinct_live_crews_ghosted():
    html = _html()
    dc = _fn(html, "function drawCrewsAndZones(tier)")
    test_branch = dc[dc.index('if (WORLD === "test")'):dc.index("return;")]
    assert "STATE.live_crews" in test_branch
    assert 'className: "ghost-crew-icon"' in test_branch
    assert 'className: "test-crew-icon"' in test_branch
    assert "read-only on the TEST map" in test_branch
    css = html[:html.index("</style>")]
    assert ".test-crew-icon {" in css and "dashed" in css[css.index(".test-crew-icon {"):]
    ghost = css[css.index(".ghost-crew-icon {"):]
    ghost = ghost[:ghost.index("}")]
    assert "opacity: .72" in ghost and "dotted" in ghost
    # seeded fires: the seeded colour + a dashed amber ring on the TEST map
    di = _fn(html, "function drawIncidents(tier)")
    assert 'WORLD === "test" && r.source === "seeded"' in di
    assert 'seeded: "#2ec4ff"' in html
    # the LIVE legend has no SEEDED source
    lg = _fn(html, "function renderLegend()")
    assert 'WORLD === "test" ? ["wfigs", "firis", "calfire", "firms", "seeded"]' in lg


def test_ops_console_is_live_only():
    html = _html(CONSOLE_HTML)
    assert 'if (inc.source === "seeded") return;' in html
    assert "#b388ff" not in html and "SEEDED (drill)" not in html
    assert "/api/test" not in html


def test_simulator_carries_the_test_sandbox_chip():
    html = _html(SIM_HTML)
    h1 = html[html.index("<h1>WG Simulator"):html.index("</h1>")]
    assert 'id="sandboxchip"' in h1 and ">TEST</span>" in h1
    assert "never touches the watcher's live map or its test map" in h1
    body = html[html.index('class="lg-body"'):]
    assert ("sandbox — this simulator never touches the watcher's live map "
            "or its test map") in body
    assert ".sandboxchip {" in html
    assert "/api/test" not in html               # a sandbox: no watcher API
