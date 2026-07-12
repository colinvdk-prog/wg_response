"""Fail-safe alert ladder regressions — SAFETY SEMANTICS 6 (Council-1 OPS-1).

Ports the four provider-outage drills from scratch_review/drill_failures.py
(the review evidence: every drill walked an EXTREME client to a FALSE
'All clear' notification ~10 minutes into the outage, with zero notes) into
permanent regressions against the full live chain:

    MockFireFeed-shaped feed -> MockWeather -> SyntheticTerrain ->
    MockClientRegistry -> real spread -> real AlertEngine -> LogNotifier

The safety invariants pinned here (contracts SAFETY SEMANTICS 6):
  * a provider outage (dead feed / weather raise / terrain raise / registry
    raise) FREEZES the EXTREME pair for >= 3 blind cycles — never CLEARED;
  * ZERO client notifications during the outage;
  * every dead cycle is CycleResult.degraded=True with OperatorEvents
    ('feed_blind'/'provider_error' from the monitor, 'degraded' from the
    frozen-pair alarm) and human-readable notes;
  * recovery resumes the normal ladder and emits OperatorEvent('recovered')
    exactly once;
  * a fire GENUINELY gone (feeds healthy, incident absent) still walks to
    CLEARED with hysteresis — the freeze must not break real clearance;
  * empty-after-nonempty disambiguates on the feed's last_fetch_ok flag:
    False -> blind (freeze), True -> gone (clear).

Also pins the feeds.py `last_fetch_ok` adapter convention and the
server/console degradation surfacing. No network, no third-party deps;
deterministic (contracts.seeded_rng mocks).
"""
from __future__ import annotations

import http.client
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from wgresponse import server
from wgresponse.alerts import AlertEngine
from wgresponse.clients import MockClientRegistry
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
    OperatorEvent,
    ResponseConfig,
)
from wgresponse.feeds import (
    CalFireFeed,
    FirisFeed,
    FirmsFeed,
    MockFireFeed,
    WfigsFeed,
)
from wgresponse.notify import LogNotifier
from wgresponse.terrain import SyntheticTerrain
from wgresponse.threat import ThreatMonitor
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
PERIOD = 300.0                      # cfg.poll_period_s — the drill cadence
CENTER = LatLon(34.20, -118.50)     # drill fire
CLIENT_LOC = LatLon(34.235, -118.50)   # ~3.9 km due north (downwind)
KEY = ("client-A", "drill-fire-1")


# ---------------------------------------------------------------------------
# killable providers (same failure modes as scratch_review/drill_failures.py)
# ---------------------------------------------------------------------------

class SwitchableFeed:
    """Drill feed with three modes:
    'ok'   healthy fetch, one active incident (last_fetch_ok=True);
    'dead' transport failure — [] by robustness design, last_fetch_ok=False;
    'gone' healthy fetch, zero incidents — [] with last_fetch_ok=True.

    Healthy fetches re-stamp updated_at_s = now_s (live-feed refresh
    semantics: an active fire is re-observed every poll). Sprint-2 ticket A5
    anchors the perimeter front at the FEED timestamp, so a permanently
    frozen updated_at_s would model a fire burning unobserved for the whole
    drill — its front would (correctly) overrun the drill client mid-drill
    and terminal IMPACTED would mask the outage semantics under test. The
    Stack sets now_s before each cycle."""

    def __init__(self, incidents):
        self._incidents = list(incidents)
        self.mode = "ok"
        self.last_fetch_ok = True
        self.now_s = T0

    def fetch_active(self, bbox=None):
        if self.mode == "dead":
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = True
        if self.mode == "gone":
            return []
        return [replace(inc, updated_at_s=self.now_s)
                for inc in self._incidents]


class KillableWeather:
    def __init__(self, inner):
        self._inner = inner
        self.dead = False

    def forecast(self, loc, horizon_h):
        if self.dead:
            raise RuntimeError("NWS HTTP 503 (simulated outage)")
        return self._inner.forecast(loc, horizon_h)


class KillableTerrain:
    def __init__(self, inner):
        self._inner = inner
        self.dead = False

    def tile(self, bbox, cell_m):
        if self.dead:
            raise RuntimeError("LFPS job failed (simulated outage)")
        return self._inner.tile(bbox, cell_m)


class KillableRegistry:
    def __init__(self, inner):
        self._inner = inner
        self.dead = False

    def protected_clients(self, bbox=None):
        if self.dead:
            raise RuntimeError("DynamoDB throttled (simulated outage)")
        return self._inner.protected_clients(bbox)


class Stack:
    """The drill stack: full live chain with killable providers."""

    def __init__(self):
        half = 0.005
        ring = [LatLon(CENTER.lat - half, CENTER.lon - half),
                LatLon(CENTER.lat - half, CENTER.lon + half),
                LatLon(CENTER.lat + half, CENTER.lon + half),
                LatLon(CENTER.lat + half, CENTER.lon - half),
                LatLon(CENTER.lat - half, CENTER.lon - half)]
        inc = FireIncident(incident_id="drill-fire-1", name="DRILL FIRE",
                           source=IncidentSource.MOCK, origin=CENTER,
                           updated_at_s=T0 - 3600.0, active=True,
                           perimeter=[ring], irwin_id="drill-fire-1",
                           source_ids={"mock": "drill-fire-1"})
        # ensemble_n/grid coarsened for test speed; physics unchanged.
        self.cfg = ResponseConfig(horizon_h=48.0, poll_period_s=PERIOD,
                                  ensemble_n=2, grid_cell_m=150.0,
                                  grid_max_cells=20_000)
        self.feed = SwitchableFeed([inc])
        # Hot, dry, strong wind blowing straight at the client (from the south).
        self.weather = KillableWeather(MockWeather(
            "drill", base_wind_mps=12.0, dir_deg=180.0, rh_pct=10.0,
            temp_c=35.0, t0_s=T0))
        self.terrain = KillableTerrain(SyntheticTerrain("drill", kind="grass"))
        client = ClientProperty(client_id="client-A", loc=CLIENT_LOC,
                                address="1 Drill Canyon Rd", name="Drill Client",
                                graphgel_enrolled=True)
        self.registry = KillableRegistry(
            MockClientRegistry("drill", clients=[client]))
        self.notifier = LogNotifier()
        self.engine = AlertEngine(self.cfg, notifier=self.notifier,
                                  clients_by_id={"client-A": client})
        self.mon = ThreatMonitor(feeds=[self.feed],
                                 weather_provider=self.weather,
                                 terrain_provider=self.terrain,
                                 registry=self.registry,
                                 alert_engine=self.engine, cfg=self.cfg,
                                 safe_zones=[])
        self.t = T0

    def cycle(self):
        self.feed.now_s = self.t          # live-feed refresh (see SwitchableFeed)
        res = self.mon.cycle(self.t)
        self.t += PERIOD
        return res

    def kill(self, which: str) -> None:
        if which == "feed":
            self.feed.mode = "dead"
        elif which == "weather":
            self.weather.dead = True
        elif which == "terrain":
            self.terrain.dead = True
        elif which == "registry":
            self.registry.dead = True
        else:  # pragma: no cover
            raise ValueError(which)

    def revive(self, which: str) -> None:
        if which == "feed":
            self.feed.mode = "ok"
        elif which == "weather":
            self.weather.dead = False
        elif which == "terrain":
            self.terrain.dead = False
        elif which == "registry":
            self.registry.dead = False

    def escalate_to_extreme(self):
        """Healthy phase: run cycles until the pair is EXTREME (max 4, per the
        drill script); returns the number of healthy cycles run."""
        for i in range(4):
            self.cycle()
            rec = self.engine.states().get(KEY)
            if rec is not None and rec.state is AlertState.EXTREME_24H:
                return i + 1
        rec = self.engine.states().get(KEY)
        raise AssertionError(
            f"drill setup failed to escalate to EXTREME_24H "
            f"(state={rec.state if rec else None})")


# ---------------------------------------------------------------------------
# THE four outage drills (ported from scratch_review/drill_failures.py §1.5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kill", ["feed", "weather", "terrain", "registry"])
def test_provider_outage_freezes_extreme_alarms_operator_and_recovers(kill):
    """Regression for Council-1 OPS-1 (drill evidence: all four kills produced
    EXTREME->CLEARED + a false 'All clear' send within 4 dead cycles, no notes
    for the dead-feed case). Ported from scratch_review/drill_failures.py."""
    stack = Stack()
    stack.escalate_to_extreme()
    sends_healthy = len(stack.notifier.sent)
    assert sends_healthy >= 1                       # the real EXTREME alert

    # -- outage: >= 3 blind cycles ------------------------------------------
    stack.kill(kill)
    dead_results = [stack.cycle() for _ in range(4)]

    rec = stack.engine.states()[KEY]
    assert rec.state is AlertState.EXTREME_24H, (
        "provider outage must FREEZE the EXTREME pair, never clear it")
    assert rec.blind_cycles >= 3
    assert rec.clear_streak == 0
    # ZERO client notifications during the outage (the drill observed the
    # false 'All clear' as a second send).
    assert len(stack.notifier.sent) == sends_healthy
    for res in dead_results:
        assert res.degraded is True
        assert res.events == []                     # no ladder transitions
        assert res.notes, "dead cycle must leave a human-readable note"
        assert res.operator_events, "dead cycle must alarm the operator"
    kinds = {e.kind for res in dead_results for e in res.operator_events}
    assert "degraded" in kinds                      # frozen-pair alarm
    assert ("feed_blind" if kill == "feed" else "provider_error") in kinds

    # -- recovery: ladder resumes, 'recovered' exactly once ------------------
    stack.revive(kill)
    res = stack.cycle()
    assert res.degraded is False
    assert [e.kind for e in res.operator_events] == ["recovered"]
    rec = stack.engine.states()[KEY]
    assert rec.state is AlertState.EXTREME_24H      # fire still burning
    assert rec.blind_cycles == 0
    assert len(stack.notifier.sent) == sends_healthy    # no spurious sends
    res = stack.cycle()
    assert res.operator_events == []                # recovered fired only once

    # -- normal ladder still works post-recovery: genuine clearance ----------
    stack.feed.mode = "gone"                        # healthy fetch, fire out
    stack.cycle()                                   # clear streak 1 of 2
    res = stack.cycle()                             # clear streak 2 -> CLEARED
    assert [e.state for e in res.events] == [AlertState.CLEARED]
    assert stack.engine.states()[KEY].state is AlertState.CLEARED
    assert len(stack.notifier.sent) == sends_healthy + 1   # the genuine all-clear


def test_operator_alarm_never_becomes_a_client_notification():
    """SAFETY SEMANTICS 6: the degradation alarm is operator-facing ONLY."""
    stack = Stack()
    stack.escalate_to_extreme()
    sends = len(stack.notifier.sent)
    stack.kill("feed")
    for _ in range(3):
        res = stack.cycle()
        assert any(e.kind == "degraded" for e in res.operator_events)
        # OperatorEvents never leak onto the client AlertEvent channel.
        assert res.events == []
    assert len(stack.notifier.sent) == sends


# ---------------------------------------------------------------------------
# fire genuinely gone vs blind (the distinction OPS-1 said the engine
# could not represent)
# ---------------------------------------------------------------------------

def test_fire_genuinely_gone_still_walks_to_cleared_with_hysteresis():
    """Feeds healthy + incident absent = trustworthy clearance: the pair must
    still downgrade through the normal clear_cycles hysteresis (the fail-safe
    freeze must not break real all-clears)."""
    stack = Stack()
    stack.escalate_to_extreme()
    sends = len(stack.notifier.sent)
    stack.feed.mode = "gone"
    res1 = stack.cycle()
    assert res1.degraded is False
    assert res1.events == []                        # hysteresis: 1 of 2
    rec = stack.engine.states()[KEY]
    assert rec.state is AlertState.EXTREME_24H
    assert rec.blind_cycles == 0                    # NOT blind — feeds healthy
    res2 = stack.cycle()
    assert [e.state for e in res2.events] == [AlertState.CLEARED]
    assert len(stack.notifier.sent) == sends + 1    # exactly one all-clear


def test_empty_after_nonempty_disambiguated_by_last_fetch_ok():
    """The ticket's razor: same [] batch, opposite meanings.
    last_fetch_ok=False -> BLIND (freeze); last_fetch_ok=True -> GONE (clear).
    """
    blind = Stack()
    blind.escalate_to_extreme()
    blind.feed.mode = "dead"                        # [] + last_fetch_ok=False
    for _ in range(2):
        blind.cycle()
    rec = blind.engine.states()[KEY]
    assert rec.state is AlertState.EXTREME_24H
    assert rec.blind_cycles == 2

    gone = Stack()
    gone.escalate_to_extreme()
    gone.feed.mode = "gone"                         # [] + last_fetch_ok=True
    for _ in range(2):
        gone.cycle()
    rec = gone.engine.states()[KEY]
    assert rec.state is AlertState.CLEARED
    assert rec.blind_cycles == 0


# ---------------------------------------------------------------------------
# feeds.py adapter health-flag convention (contracts feeds seam)
# ---------------------------------------------------------------------------

def _http_raising(url, headers=None, **kw):
    raise OSError("connection refused (simulated)")


def _http_500(url, headers=None, **kw):
    return 500, b"upstream sad"


def _http_empty_arcgis(url, headers=None, **kw):
    return 200, b'{"features": []}'


def _http_garbage(url, headers=None, **kw):
    return 200, b"<html>not json</html>"


@pytest.mark.parametrize("feed_ctor", [
    lambda hg: WfigsFeed(http_get=hg),
    lambda hg: FirisFeed(http_get=hg),
    lambda hg: CalFireFeed(http_get=hg),
    lambda hg: FirmsFeed("test-map-key", http_get=hg),
], ids=["wfigs", "firis", "calfire", "firms"])
def test_adapter_transport_failure_sets_last_fetch_ok_false(feed_ctor):
    feed = feed_ctor(_http_raising)
    assert feed.last_fetch_ok is True               # optimistic pre-fetch
    assert feed.fetch_active() == []                # robustness design holds
    assert feed.last_fetch_ok is False              # ...but the fetch is BLIND


@pytest.mark.parametrize("feed_ctor", [
    lambda hg: WfigsFeed(http_get=hg),
    lambda hg: FirisFeed(http_get=hg),
    lambda hg: CalFireFeed(http_get=hg),
    lambda hg: FirmsFeed("test-map-key", http_get=hg),
], ids=["wfigs", "firis", "calfire", "firms"])
def test_adapter_http_error_sets_last_fetch_ok_false(feed_ctor):
    feed = feed_ctor(_http_500)
    assert feed.fetch_active() == []
    assert feed.last_fetch_ok is False


def test_adapter_parse_failure_sets_last_fetch_ok_false():
    for feed in (WfigsFeed(http_get=_http_garbage),
                 FirisFeed(http_get=_http_garbage),
                 CalFireFeed(http_get=_http_garbage)):
        assert feed.fetch_active() == []
        assert feed.last_fetch_ok is False, type(feed).__name__


def test_adapter_empty_but_successful_fetch_stays_healthy():
    """Genuinely zero incidents is NOT a failure: [] with last_fetch_ok=True."""
    wfigs = WfigsFeed(http_get=_http_empty_arcgis)
    assert wfigs.fetch_active() == []
    assert wfigs.last_fetch_ok is True
    firis = FirisFeed(http_get=_http_empty_arcgis)
    assert firis.fetch_active() == []
    assert firis.last_fetch_ok is True
    calfire = CalFireFeed(http_get=lambda url, headers=None, **kw: (200, b"[]"))
    assert calfire.fetch_active() == []
    assert calfire.last_fetch_ok is True
    firms = FirmsFeed("test-map-key", http_get=lambda url, headers=None, **kw:
                      (200, b"latitude,longitude,acq_date,acq_time,frp\n"))
    assert firms.fetch_active() == []
    assert firms.last_fetch_ok is True


def test_wfigs_partial_fetch_is_unhealthy():
    """One dead layer out of two can silently drop incidents — a partial WFIGS
    fetch must read as blind, not clear."""
    calls = {"n": 0}

    def flaky(url, headers=None, **kw):
        calls["n"] += 1
        if "Incident_Locations" in url:
            raise OSError("points layer down")
        return 200, b'{"features": []}'

    feed = WfigsFeed(http_get=flaky)
    assert feed.fetch_active() == []
    assert feed.last_fetch_ok is False


def test_mock_feed_reports_healthy():
    feed = MockFireFeed("x", n=1)
    feed.fetch_active()
    assert feed.last_fetch_ok is True


# ---------------------------------------------------------------------------
# server + console degradation surfacing
# ---------------------------------------------------------------------------

@pytest.fixture
def degraded_server(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "test-key-123")
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    state = {
        "degraded": True,
        "t_s": T0,
        "operator_events": [OperatorEvent(
            t_s=T0, kind="feed_blind", message="feed[0] WfigsFeed fetch raised",
            detail={"feed": "WfigsFeed"})],
        "clients": [],
    }
    srv = server.serve(0, state_provider=lambda: state)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def _get(port, path, key=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"X-Internal-API-Key": key} if key else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_healthz_surfaces_degraded_flag_and_cycle_age(degraded_server):
    status, body = _get(degraded_server, "/healthz")     # open endpoint
    assert status == 200
    assert body["ok"] is True
    assert body["degraded"] is True
    assert body["last_cycle_t_s"] == T0
    # Coarse only: counts, never operator-event bodies on the open endpoint.
    assert body["operator_event_count"] == 1
    assert "operator_events" not in body


def test_api_state_carries_operator_events_and_degraded(degraded_server):
    status, body = _get(degraded_server, "/api/state", key="test-key-123")
    assert status == 200
    assert body["degraded"] is True
    (ev,) = body["operator_events"]
    assert ev["kind"] == "feed_blind"
    assert "WfigsFeed" in ev["message"]


def test_healthz_without_degradation_keys_stays_plain(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "test-key-123")
    srv = server.serve(0, state_provider=lambda: {"clients": []})
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(srv.server_address[1], "/healthz")
        assert status == 200
        assert body == {"ok": True}
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()


def test_console_renders_degradation_banner():
    """The ops console must show a red 'DEGRADED — forecasts may be stale'
    banner when the state reports degraded (OPS-1: notes were rendered
    nowhere). Static conformance in the spirit of
    tests/test_repro_console_offline.py — plain DOM, offline-shim safe."""
    html = (Path(__file__).resolve().parent.parent
            / "console" / "index.html").read_text(encoding="utf-8")
    assert "degradedbar" in html
    assert "DEGRADED — forecasts may be stale" in html
    # Wired to the state payload, not hardcoded-on.
    assert "d.degraded" in html
    assert "operator_events" in html
    # Plain DOM (fixed-position div), independent of Leaflet/the SVG shim.
    banner_css = next(ln for ln in html.splitlines() if "#degradedbar" in ln
                      and "position" in ln)
    assert "fixed" in banner_css
