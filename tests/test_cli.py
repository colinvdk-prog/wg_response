"""Tests for wgresponse.cli — in-process main() calls, no network."""
import json
from pathlib import Path

from wgresponse import cli


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

def test_simulate_writes_both_jsons(tmp_path, capsys):
    rc = cli.main(["simulate", "--scenario", "tiny", "--hours", "1",
                   "--out", str(tmp_path)])
    assert rc == 0
    report = tmp_path / "sim_tiny.json"
    console = tmp_path / "sim_tiny_console.json"
    assert report.exists() and console.exists()
    json.loads(report.read_text(encoding="utf-8"))
    json.loads(console.read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    assert "sim_tiny.json" in out
    assert "0 violations" in out


def test_simulate_unknown_scenario_errors(tmp_path, capsys):
    rc = cli.main(["simulate", "--scenario", "nope", "--out", str(tmp_path)])
    assert rc == 2
    assert "unknown scenario" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# pull-feeds
# ---------------------------------------------------------------------------

def test_pull_feeds_offline_no_incidents(capsys):
    rc = cli.main(["pull-feeds", "--bbox", "-120,34,-119,35", "--offline"])
    assert rc == 0
    assert "no incidents" in capsys.readouterr().out


def test_pull_feeds_bad_bbox(capsys):
    rc = cli.main(["pull-feeds", "--bbox", "1,2,3", "--offline"])
    assert rc == 2
    assert "bbox" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# forecast (mock path)
# ---------------------------------------------------------------------------

def test_forecast_mock(capsys):
    rc = cli.main(["forecast", "--lat", "35.1", "--lon", "-120.5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "p50" in out and "p10" in out


# ---------------------------------------------------------------------------
# plan (re-plan from console JSON)
# ---------------------------------------------------------------------------

def test_plan_from_console_json(tmp_path, capsys):
    rc = cli.main(["simulate", "--scenario", "tiny", "--hours", "1",
                   "--out", str(tmp_path)])
    assert rc == 0
    capsys.readouterr()
    console = tmp_path / "sim_tiny_console.json"
    rc = cli.main(["plan", "--in", str(console)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "crew" in out and "client" in out


def test_plan_rejects_non_console_json(tmp_path, capsys):
    bad = tmp_path / "not_console.json"
    bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    rc = cli.main(["plan", "--in", str(bad)])
    assert rc == 2
    assert "console" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# watch — AOI parsing (ticket C1)
# ---------------------------------------------------------------------------

import http.client
import logging
import math
import threading

import pytest

from wgresponse import geo, server
from wgresponse.alerts import AlertEngine
from wgresponse.cli import _WatchDaemon, _live_feed_stack, _parse_aoi
from wgresponse.contracts import (
    BBox,
    DispatchPlan,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    ClientProperty,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.notify import LogNotifier
from wgresponse.persist import StateStore
from wgresponse.threat import ThreatMonitor

T0 = 1_780_000_000.0
AOI = BBox(west=-121.2, south=38.8, east=-120.8, north=39.2)
ORIGIN = LatLon(39.0, -121.0)
CLIENT_LOC = LatLon(39.0, -120.95)     # ~4.3 km due east (downwind)


def test_parse_aoi_bbox_string():
    bbox, zones, desc = _parse_aoi("-121.2,38.8,-120.8,39.2")
    assert bbox == AOI
    assert zones == []
    assert "bbox" in desc


@pytest.mark.parametrize("bad", [
    "1,2,3",                       # 3 parts
    "-120.8,38.8,-121.2,39.2",     # west > east
    "-121.2,39.2,-120.8,38.8",     # south > north
    "-121.2,38.8,abc,39.2",        # non-numeric
    "-500,38.8,-120.8,39.2",       # out of range
])
def test_parse_aoi_rejects_bad_bboxes(bad):
    with pytest.raises(ValueError):
        _parse_aoi(bad)


def test_parse_aoi_zones_file_union_and_safe_zones(tmp_path):
    zf = tmp_path / "zones.json"
    zf.write_text(json.dumps({
        "zones": [{"name": "north", "bbox": [-121.2, 39.0, -120.8, 39.2]},
                  {"name": "south", "bbox": [-121.1, 38.8, -120.7, 39.05]}],
        "safe_zones": [{"zone_id": "Z1", "lat": 39.1, "lon": -121.0,
                        "name": "Fairgrounds"}],
    }), encoding="utf-8")
    bbox, zones, desc = _parse_aoi(str(zf))
    assert bbox == BBox(west=-121.2, south=38.8, east=-120.7, north=39.2)
    assert [z.zone_id for z in zones] == ["Z1"]
    assert zones[0].loc == LatLon(39.1, -121.0)
    assert "north" in desc and "south" in desc


def test_parse_aoi_zones_file_plain_aoi_key(tmp_path):
    zf = tmp_path / "aoi.json"
    zf.write_text(json.dumps({"aoi": [-121.2, 38.8, -120.8, 39.2]}),
                  encoding="utf-8")
    bbox, zones, _ = _parse_aoi(str(zf))
    assert bbox == AOI and zones == []


def test_parse_aoi_missing_file_and_bad_json_raise(tmp_path):
    with pytest.raises(ValueError):
        _parse_aoi(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):                 # JSONDecodeError subclass
        _parse_aoi(str(bad))
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"safe_zones": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        _parse_aoi(str(empty))


def test_cmd_watch_bad_aoi_exits_2_before_any_network(capsys):
    rc = cli.main(["watch", "--aoi", "garbage"])
    assert rc == 2
    assert "aoi" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# watch — FIRMS graceful skip (ticket C3d wiring)
# ---------------------------------------------------------------------------

def test_live_feed_stack_skips_firms_without_key():
    stack, notes = _live_feed_stack(None)
    names = [type(f).__name__ for f in stack]
    assert names == ["WfigsFeed", "FirisFeed", "CalFireFeed"]
    assert len(notes) == 1
    assert "FIRMS_MAP_KEY" in notes[0]
    assert "firms.modaps.eosdis.nasa.gov" in notes[0]   # signup pointer


def test_live_feed_stack_includes_firms_with_key():
    stack, notes = _live_feed_stack("abc123")
    names = [type(f).__name__ for f in stack]
    assert names == ["WfigsFeed", "FirisFeed", "CalFireFeed", "FirmsFeed"]
    assert notes == []
    assert stack[-1]._map_key == "abc123"


# ---------------------------------------------------------------------------
# watch — the daemon loop against a deterministic stub stack (tickets C1+C2)
# ---------------------------------------------------------------------------

class BboxRecordingFeed:
    """Healthy stub feed that records the bbox of every fetch."""

    def __init__(self, incidents):
        self._incidents = list(incidents)
        self.bboxes = []
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        self.bboxes.append(bbox)
        return list(self._incidents)


class StubWeather:
    def forecast(self, loc, horizon_h):
        hours = int(horizon_h) + 2
        samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=8.0,
                                 wind_gust_mps=10.0, wind_dir_deg=270.0,
                                 rh_pct=20.0, temp_c=30.0)
                   for i in range(hours)]
        return WeatherForecast(loc=loc, samples=samples, source="stub")


class StubTerrain:
    def tile(self, bbox, cell_m):
        ctr = bbox.center()
        h_m = (bbox.north - bbox.south) * geo.M_PER_DEG_LAT
        w_m = (bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat)
        nrows = max(1, math.ceil(h_m / cell_m))
        ncols = max(1, math.ceil(w_m / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west,
                        cell_m=cell_m, nrows=nrows, ncols=ncols)
        return TerrainTile(spec=spec,
                           fuel=geo.make_raster(spec, fill=102.0),
                           slope_deg=geo.make_raster(spec, fill=0.0),
                           aspect_deg=geo.make_raster(spec, fill=0.0),
                           elev_m=geo.make_raster(spec, fill=100.0),
                           source="stub")


class StubRegistry:
    def __init__(self, clients):
        self._clients = list(clients)

    def protected_clients(self, bbox=None):
        return list(self._clients)


class RecordingDispatcher:
    """Dispatcher stub WITH the Sprint-2 forecasts kwarg; records calls."""

    def __init__(self):
        self.calls = []

    def plan(self, crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
             forecasts=None):
        self.calls.append({"jobs": list(jobs), "ens": dict(ens),
                           "incumbent": incumbent,
                           "forecasts": dict(forecasts or {})})
        return DispatchPlan(computed_at_s=now_s)


class FakeClock:
    """Deterministic clock: starts at t0, advances 1 s per read."""

    def __init__(self, t0=T0):
        self.t = t0

    def __call__(self):
        self.t += 1.0
        return self.t


def _incident(updated_at_s=T0 - 600.0):
    return FireIncident(incident_id="inc-1", name="Stub Fire",
                        source=IncidentSource.MOCK, origin=ORIGIN,
                        updated_at_s=updated_at_s, acreage=500.0,
                        containment_pct=10.0, active=True)


def _client():
    return ClientProperty(client_id="c1", loc=CLIENT_LOC, name="Casa Uno",
                          graphgel_enrolled=True)


def _daemon(tmp_path, cfg=None, dispatcher=None, notifier=None,
            incidents=None, clock=None):
    cfg = cfg or ResponseConfig(horizon_h=24.0, ensemble_n=2,
                                grid_cell_m=200.0, grid_max_cells=20_000)
    feed = BboxRecordingFeed(incidents if incidents is not None
                             else [_incident()])
    registry = StubRegistry([_client()])
    notifier = notifier if notifier is not None else LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={"c1": _client()})
    monitor = ThreatMonitor(feeds=[feed], weather_provider=StubWeather(),
                            terrain_provider=StubTerrain(), registry=registry,
                            alert_engine=engine, cfg=cfg, safe_zones=[],
                            crew_tracker=None, dispatcher=dispatcher, aoi=AOI)
    store = StateStore(tmp_path / "state")
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=None, store=store, cfg=cfg, aoi=AOI,
                          period_s=300.0, aoi_desc="test aoi",
                          clock=clock or FakeClock(),
                          sleep=lambda s: None)
    return daemon, feed, engine, notifier, store


def test_watch_daemon_threads_aoi_into_every_feed_fetch(tmp_path):
    daemon, feed, _, _, _ = _daemon(tmp_path)
    daemon.run_cycle()
    daemon.run_cycle()
    assert feed.bboxes == [AOI, AOI]        # REQUIRED aoi on every fetch


def test_watch_daemon_state_carries_cycle_and_console_keys(tmp_path):
    daemon, _, _, _, _ = _daemon(tmp_path)
    res = daemon.run_cycle()
    state = daemon.state()
    assert state["live"] is True
    assert state["cycles_run"] == 1
    assert state["last_cycle_t_s"] == res.t_s
    assert state["last_cycle_age_s"] >= 0.0
    assert state["degraded"] is False
    assert state["poll_period_s"] == 300.0
    # STALENESS SLO + cadence telemetry (tri-model review live-ops finding)
    assert state["cycle_stale"] is False
    assert state["stale_after_s"] == 3.0 * 300.0
    assert state["last_cycle_duration_s"] >= 0.0
    daemon._clock = lambda: res.t_s + 4.0 * 300.0     # four periods later
    assert daemon.state()["cycle_stale"] is True
    assert state["bbox"] == {"west": AOI.west, "south": AOI.south,
                             "east": AOI.east, "north": AOI.north}
    assert state["incidents"][0]["incident_id"] == "inc-1"
    assert state["incidents"][0]["containment_pct"] == 10.0
    (client_row,) = [c for c in state["clients"] if c["client_id"] == "c1"]
    assert client_row["lat"] == pytest.approx(CLIENT_LOC.lat)
    assert client_row["state"] in ("notice_72h", "watch", "extreme_24h")
    assert client_row["eta_p50_s"] is not None
    assert state["events"], "alert events must reach the console feed"


def test_watch_daemon_healthz_surfaces_age_degraded_and_cycles(tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "test-key-123")
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    daemon, _, _, _, _ = _daemon(tmp_path)
    daemon.run_cycle()
    srv = server.serve(0, state_provider=daemon.state,
                       plan_provider=daemon.plan)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1",
                                          srv.server_address[1], timeout=10)
        conn.request("GET", "/healthz")
        body = json.loads(conn.getresponse().read())
        conn.close()
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()
    assert body["ok"] is True
    assert body["degraded"] is False
    assert body["cycles_run"] == 1
    assert body["last_cycle_t_s"] > 0
    assert body["last_cycle_age_s"] >= 0.0
    assert "operator_event_count" in body
    assert "operator_events" not in body     # bodies stay behind auth


def test_watch_daemon_degraded_feed_surfaces_in_state(tmp_path):
    daemon, feed, _, _, _ = _daemon(tmp_path)
    daemon.run_cycle()

    def dead_fetch(bbox=None):
        feed.last_fetch_ok = False
        return []

    feed.fetch_active = dead_fetch
    daemon.run_cycle()
    state = daemon.state()
    assert state["degraded"] is True
    kinds = {e.kind for e in state["operator_events"]}
    assert "feed_blind" in kinds


def test_watch_daemon_persists_every_cycle_and_restart_refires_nothing(tmp_path):
    daemon, _, engine, notifier, store = _daemon(tmp_path)
    daemon.run_cycle()      # escalation cycle: sends happen here
    daemon.run_cycle()      # stable cycle
    assert store.path.exists()
    sends = len(notifier.sent)
    assert sends >= 1
    states_before = {k: r.state for k, r in engine.states().items()}
    ledger_before = engine.notified_ledger()

    # Process-equivalent restart: fresh engine/monitor/notifier, same disk.
    daemon2, _, engine2, notifier2, _ = _daemon(tmp_path)
    assert daemon2.restore() is True
    assert {k: r.state for k, r in engine2.states().items()} == states_before
    assert engine2.notified_ledger() == ledger_before
    daemon2.run_cycle()     # same world, ~seconds later
    assert notifier2.sent == []          # RESTART RE-FIRES NOTHING
    assert engine2.notified_ledger() == ledger_before


def test_watch_daemon_restore_seeds_dispatch_incumbent(tmp_path):
    disp1 = RecordingDispatcher()
    daemon, _, _, _, _ = _daemon(tmp_path, dispatcher=disp1)
    daemon.run_cycle()
    assert disp1.calls, "alerted pair must produce a dispatch call"
    plan1 = daemon.plan()
    assert plan1 is not None

    disp2 = RecordingDispatcher()
    daemon2, _, _, _, _ = _daemon(tmp_path, dispatcher=disp2)
    assert daemon2.restore() is True
    daemon2.run_cycle()
    assert disp2.calls[0]["incumbent"] is not None
    assert disp2.calls[0]["incumbent"].computed_at_s == plan1.computed_at_s


def test_watch_daemon_threads_forecasts_into_dispatch(tmp_path):
    disp = RecordingDispatcher()
    daemon, _, _, _, _ = _daemon(tmp_path, dispatcher=disp)
    daemon.run_cycle()
    call = disp.calls[0]
    assert set(call["forecasts"]) == {"inc-1"}
    wxf = call["forecasts"]["inc-1"]
    assert isinstance(wxf, WeatherForecast)      # the fire-centroid forecast
    assert wxf.samples[0].wind_dir_deg == 270.0
    assert set(call["ens"]) == {"inc-1"}


def test_watch_daemon_run_honors_max_cycles_and_interrupt(tmp_path):
    daemon, _, _, _, store = _daemon(tmp_path)
    ran = daemon.run(max_cycles=2)
    assert ran == 2 and daemon.cycles_run == 2

    # Ctrl-C mid-loop: clean return + final persist (no exception escapes).
    calls = {"n": 0}

    def interrupting_sleep(_s):
        calls["n"] += 1
        raise KeyboardInterrupt

    daemon._sleep = interrupting_sleep
    ran = daemon.run()                       # unbounded until "Ctrl-C"
    assert ran == 1 and calls["n"] == 1
    assert daemon.cycles_run == 3
    assert store.path.exists()
    loaded = StateStore(tmp_path / "state").load()
    assert loaded is not None
    assert loaded["meta"]["cycles_run"] == 3
    assert loaded["meta"]["final"] is True   # the Ctrl-C persist was the last


def test_watch_daemon_persist_failure_is_loud_not_fatal(tmp_path, caplog):
    daemon, _, _, _, _ = _daemon(tmp_path)

    class BrokenStore:
        path = tmp_path / "nope"

        def save(self, *a, **kw):
            raise OSError("disk full")

    daemon._store = BrokenStore()
    with caplog.at_level(logging.ERROR, logger="wgresponse.cli"):
        res = daemon.run_cycle()             # must not raise
    assert res is not None
    assert any("persist FAILED" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# D2 end-to-end: the containment cap changes the LADDER, never the dispatch
# inputs (jobs + ensembles byte-identical capped vs uncapped).
# ---------------------------------------------------------------------------

def test_containment_cap_never_touches_dispatch_inputs(tmp_path):
    contained = FireIncident(
        incident_id="inc-1", name="Contained Fire", source=IncidentSource.MOCK,
        origin=ORIGIN, updated_at_s=T0 - 600.0, acreage=800.0,
        containment_pct=95.0, active=True)
    growth_history = {"inc-1": {"acreage": 800.0,
                                "last_growth_s": T0 - 100 * 3600.0}}

    def run(gate_on):
        cfg = ResponseConfig(horizon_h=24.0, ensemble_n=2, grid_cell_m=200.0,
                             grid_max_cells=20_000,
                             containment_gate_enabled=gate_on)
        disp = RecordingDispatcher()
        daemon, _, engine, _, _ = _daemon(
            tmp_path / ("on" if gate_on else "off"), cfg=cfg,
            dispatcher=disp, incidents=[contained],
            clock=FakeClock(T0))
        # Seed the no-growth clock (100 h of tracked history, as persisted).
        engine.restore({}, incident_growth=growth_history)
        daemon.run_cycle()
        return disp.calls[0], engine.states()[("c1", "inc-1")].state

    call_on, state_on = run(gate_on=True)
    call_off, state_off = run(gate_on=False)

    # The ladder DID diverge (the cap engaged)...
    from wgresponse.contracts import AlertState
    assert state_off is AlertState.EXTREME_24H
    assert state_on in (AlertState.NOTICE_72H, AlertState.WATCH)
    # ...but the crew-safety inputs are identical: same job deadlines,
    # byte-identical P10 arrival rasters for the corridor checks.
    (job_on,), (job_off,) = call_on["jobs"], call_off["jobs"]
    assert job_on.client.client_id == job_off.client.client_id
    assert job_on.eta == job_off.eta
    p10_on = call_on["ens"]["inc-1"].p10
    p10_off = call_off["ens"]["inc-1"].p10
    assert p10_on.data == p10_off.data
    assert [m.data for m in call_on["ens"]["inc-1"].members] == \
        [m.data for m in call_off["ens"]["inc-1"].members]


# ---------------------------------------------------------------------------
# console static conformance: the last-cycle age chip (ticket C1)
# ---------------------------------------------------------------------------

def test_console_has_last_cycle_age_chip():
    html = (Path(__file__).resolve().parent.parent
            / "console" / "index.html").read_text(encoding="utf-8")
    assert "agechip" in html
    assert "last_cycle_t_s" in html            # bound to the live state key
    assert "renderAgeChip" in html
    assert "poll_period_s" in html             # staleness threshold source
    assert "2.5 * pollPeriod" in html          # overdue -> stale styling
    # Live mode renders incident markers + tolerates roster gaps.
    assert "d.incidents" in html


def test_normalize_argv_joins_negative_aoi_and_bbox():
    from wgresponse.cli import _normalize_argv
    assert _normalize_argv(["watch", "--aoi", "-121,32,-114,36"]) == [
        "watch", "--aoi=-121,32,-114,36"]
    assert _normalize_argv(["pull-feeds", "--bbox", "-120,34,-119,35"]) == [
        "pull-feeds", "--bbox=-120,34,-119,35"]
    # zones-file values do not start with '-' and pass through untouched.
    assert _normalize_argv(["watch", "--aoi", "zones.json"]) == [
        "watch", "--aoi", "zones.json"]


# ---------------------------------------------------------------------------
# watch terrain builder: LFPS email threading (Session-3 exit-gate soak
# finding, 2026-07-09 — the live LFPS API 400s any submit without an Email
# parameter, so the daemon's fresh-tile fetches all went provider_error/
# DEGRADED; the hindcast harness already submitted with the ops address)
# ---------------------------------------------------------------------------

def _watch_terrain_args(**kw):
    from types import SimpleNamespace
    base = {"synthetic_terrain": False, "live_terrain": False}
    base.update(kw)
    return SimpleNamespace(**base)


def test_watch_terrain_threads_lfps_email_default(tmp_path, monkeypatch):
    from wgresponse import terrain as terrain_mod
    monkeypatch.setattr(terrain_mod, "probe_live_decode_deps", lambda: None)
    monkeypatch.delenv(cli._LFPS_EMAIL_ENV, raising=False)
    provider, notes = cli._watch_terrain(_watch_terrain_args(), tmp_path)
    assert isinstance(provider, terrain_mod.LfpsTerrain)
    assert provider._email == cli._LFPS_EMAIL_DEFAULT
    assert any(cli._LFPS_EMAIL_DEFAULT in n for n in notes)


def test_watch_terrain_lfps_email_env_override(tmp_path, monkeypatch):
    from wgresponse import terrain as terrain_mod
    monkeypatch.setattr(terrain_mod, "probe_live_decode_deps", lambda: None)
    monkeypatch.setenv(cli._LFPS_EMAIL_ENV, "soak-ops@example.com")
    provider, notes = cli._watch_terrain(_watch_terrain_args(), tmp_path)
    assert provider._email == "soak-ops@example.com"
    assert any("soak-ops@example.com" in n for n in notes)


def test_watch_terrain_empty_env_email_falls_back_to_default(tmp_path,
                                                             monkeypatch):
    # An empty LFPS_EMAIL must not submit Email="" (the API would 400 again).
    from wgresponse import terrain as terrain_mod
    monkeypatch.setattr(terrain_mod, "probe_live_decode_deps", lambda: None)
    monkeypatch.setenv(cli._LFPS_EMAIL_ENV, "")
    provider, _ = cli._watch_terrain(_watch_terrain_args(), tmp_path)
    assert provider._email == cli._LFPS_EMAIL_DEFAULT


def test_watch_terrain_synthetic_flag_ignores_email_seam(tmp_path, monkeypatch):
    from wgresponse import terrain as terrain_mod
    monkeypatch.setenv(cli._LFPS_EMAIL_ENV, "soak-ops@example.com")
    provider, notes = cli._watch_terrain(
        _watch_terrain_args(synthetic_terrain=True), tmp_path)
    assert isinstance(provider, terrain_mod.SyntheticTerrain)
    assert any("SYNTHETIC TERRAIN FORCED" in n for n in notes)


# ---------------------------------------------------------------------------
# simui — the interactive simulator UI (SIM-3)
# ---------------------------------------------------------------------------

import os


def test_simui_help_lists_flags(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["simui", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--port", "--open", "--smoke", "--host"):
        assert flag in out, flag


def test_simui_smoke_runs_clean_and_prints_scoreboard(capsys, monkeypatch):
    """--smoke: ephemeral-port server, new session over HTTP, ignite, 3 real
    engine ticks, scoreboard printed, exit 0 with 0 safety violations. Also
    covers the required startup printout (URL + five locations + tile
    availability) and the dev-open env hygiene."""
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    rc = cli.main(["simui", "--smoke"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "/simulator" in out                     # the URL line
    assert "DEV-OPEN by default" in out            # loud posture note
    for key in ("malibu_ca", "paradise_ca", "santa_rosa_ca", "boulder_co",
                "park_city_ut"):
        assert key in out, key                     # the five locations
    assert "tile" in out                           # availability column
    assert "scoreboard" in out
    assert "violations     0" in out
    assert "smoke OK (violations=0)" in out
    # --smoke must not leak the dev-open posture into the calling process
    assert os.environ.get("WGRESP_DEV_OPEN") is None


def test_simui_smoke_keeps_preexisting_dev_open(capsys, monkeypatch):
    """An operator-set WGRESP_DEV_OPEN is honored, never popped by --smoke."""
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    rc = cli.main(["simui", "--smoke"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DEV-OPEN (no auth!)" in out
    assert os.environ.get("WGRESP_DEV_OPEN") == "1"
