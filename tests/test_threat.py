"""Tests for wgresponse.threat — ThreatMonitor.cycle with hand stubs.

All collaborators are simple stubs conforming to the contracts Protocols;
no network, no third-party deps, deterministic.
"""
from __future__ import annotations

import math

import pytest

from wgresponse import geo
from wgresponse.contracts import (
    AlertEvent,
    AlertRecord,
    AlertState,
    BBox,
    ClientProperty,
    Crew,
    CrewStatus,
    DispatchPlan,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    ResponseConfig,
    SafeZone,
    TerrainTile,
    UNREACHED,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.threat import ThreatMonitor

NOW = 1_760_000_000.0
ORIGIN = LatLon(39.0, -121.0)


# ---------------------------------------------------------------------------
# stubs (contracts Protocol shapes)
# ---------------------------------------------------------------------------

class StubFeed:
    def __init__(self, incidents):
        self._incidents = incidents
        self.calls = 0

    def fetch_active(self, bbox=None):
        self.calls += 1
        return list(self._incidents)


class RaisingFeed:
    def fetch_active(self, bbox=None):
        raise RuntimeError("feed offline")


class StubWeather:
    """Constant hourly west wind (blows east) starting at NOW."""

    def __init__(self, wind_mps=8.0, wind_dir_deg=270.0):
        self.wind_mps = wind_mps
        self.wind_dir_deg = wind_dir_deg
        self.calls = []

    def forecast(self, loc, horizon_h):
        self.calls.append((loc, horizon_h))
        hours = int(horizon_h) + 2
        samples = [WeatherSample(t_s=NOW + i * 3600.0,
                                 wind_speed_mps=self.wind_mps,
                                 wind_gust_mps=self.wind_mps * 1.3,
                                 wind_dir_deg=self.wind_dir_deg,
                                 rh_pct=20.0, temp_c=30.0)
                   for i in range(hours)]
        return WeatherForecast(loc=loc, samples=samples, source="stub")


class RaisingWeather:
    def forecast(self, loc, horizon_h):
        raise RuntimeError("nws down")


class StubTerrain:
    """Uniform GR2 grass tile exactly covering the requested bbox."""

    def __init__(self, fuel_code=102.0):
        self.fuel_code = fuel_code
        self.calls = []

    def tile(self, bbox, cell_m):
        self.calls.append((bbox, cell_m))
        ctr = bbox.center()
        h_m = (bbox.north - bbox.south) * geo.M_PER_DEG_LAT
        w_m = (bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat)
        nrows = max(1, math.ceil(h_m / cell_m))
        ncols = max(1, math.ceil(w_m / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west,
                        cell_m=cell_m, nrows=nrows, ncols=ncols)
        return TerrainTile(
            spec=spec,
            fuel=geo.make_raster(spec, fill=self.fuel_code),
            slope_deg=geo.make_raster(spec, fill=0.0),
            aspect_deg=geo.make_raster(spec, fill=0.0),
            elev_m=geo.make_raster(spec, fill=100.0),
            source="stub",
        )


class RaisingTerrain:
    def tile(self, bbox, cell_m):
        raise RuntimeError("landfire down")


class StubRegistry:
    def __init__(self, clients):
        self._clients = clients
        self.calls = []

    def protected_clients(self, bbox=None):
        self.calls.append(bbox)
        return list(self._clients)


class StubAlertEngine:
    def __init__(self, states=None, events=None):
        self.seen = []
        self._states = dict(states or {})
        self._events = list(events or [])

    def evaluate(self, assessments, now_s):
        self.seen.append((list(assessments), now_s))
        return list(self._events)

    def states(self):
        return dict(self._states)


class RaisingAlertEngine:
    def evaluate(self, assessments, now_s):
        raise RuntimeError("ladder broken")

    def states(self):
        return {}


class RichStubAlertEngine(StubAlertEngine):
    """Sprint-2 seam stub: records the blind_pairs/feeds_healthy kwargs and
    serves queued operator events via pop_operator_events()."""

    def __init__(self, states=None, events=None, op_events=None):
        super().__init__(states=states, events=events)
        self.kw = []
        self._op = list(op_events or [])

    def evaluate(self, assessments, now_s, blind_pairs=None, feeds_healthy=True):
        self.seen.append((list(assessments), now_s))
        self.kw.append({"blind_pairs": set(blind_pairs or ()),
                        "feeds_healthy": feeds_healthy})
        return list(self._events)

    def pop_operator_events(self):
        out = self._op
        self._op = []
        return out


class BlindFeed:
    """A feed whose transport failed: returns [] per the robustness design but
    reports last_fetch_ok=False (contracts health convention)."""

    def __init__(self):
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        self.last_fetch_ok = False
        return []


class RaisingRegistry:
    def protected_clients(self, bbox=None):
        raise RuntimeError("dynamo throttled")


class StubDispatcher:
    def __init__(self):
        self.calls = []

    def plan(self, crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None):
        plan = DispatchPlan(computed_at_s=now_s)
        self.calls.append(dict(crews=list(crews), jobs=list(jobs),
                               ens=dict(ens), safe_zones=list(safe_zones),
                               cfg=cfg, now_s=now_s, incumbent=incumbent,
                               plan=plan))
        return plan


class StubCrewTracker:
    def __init__(self, crews):
        self._crews = crews

    def crews(self):
        return list(self._crews)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def incident(iid="inc-1", active=True, origin=ORIGIN, irwin="IRWIN-1"):
    return FireIncident(incident_id=iid, name=f"Test Fire {iid}",
                        source=IncidentSource.MOCK, origin=origin,
                        updated_at_s=NOW - 1800.0, active=active,
                        irwin_id=irwin)


def near_client():
    # 1.5 km due east of the origin: directly downwind of the west wind.
    return ClientProperty(client_id="c-near",
                          loc=geo.destination(ORIGIN, 90.0, 1500.0))


def far_client():
    # 100 km away: outside the 60 km capped threat radius.
    return ClientProperty(client_id="c-far",
                          loc=geo.destination(ORIGIN, 90.0, 100_000.0))


def make_cfg(**kw):
    kw.setdefault("ensemble_n", 2)
    kw.setdefault("horizon_h", 24.0)
    kw.setdefault("grid_cell_m", 150.0)
    kw.setdefault("grid_max_cells", 20_000)
    return ResponseConfig(**kw)


def make_monitor(feeds=None, weather=None, terrain=None, registry=None,
                 alert_engine=None, cfg=None, safe_zones=None,
                 crew_tracker=None, dispatcher=None):
    return ThreatMonitor(
        feeds=feeds if feeds is not None else [StubFeed([incident()])],
        weather_provider=weather or StubWeather(),
        terrain_provider=terrain or StubTerrain(),
        registry=registry if registry is not None
        else StubRegistry([near_client(), far_client()]),
        alert_engine=alert_engine or StubAlertEngine(),
        cfg=cfg or make_cfg(),
        safe_zones=safe_zones if safe_zones is not None
        else [SafeZone("z1", geo.destination(ORIGIN, 180.0, 20_000.0))],
        crew_tracker=crew_tracker,
        dispatcher=dispatcher,
    )


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------

def test_cycle_assesses_near_client_only():
    engine = StubAlertEngine()
    mon = make_monitor(alert_engine=engine)
    result = mon.cycle(NOW)
    pairs = {(a.client_id, a.incident_id) for a in result.assessments}
    assert pairs == {("c-near", "inc-1")}
    assert result.t_s == NOW
    assert [i.incident_id for i in result.incidents] == ["inc-1"]
    assert "inc-1" in result.ensembles
    assert result.ensembles["inc-1"].incident_id == "inc-1"


def test_downwind_client_gets_finite_ordered_eta():
    mon = make_monitor()
    result = mon.cycle(NOW)
    (a,) = result.assessments
    assert NOW < a.eta.eta_p10_s <= a.eta.eta_p50_s <= a.eta.eta_p90_s
    assert math.isfinite(a.eta.eta_p50_s)
    assert a.computed_at_s == NOW


def test_assessments_passed_to_alert_engine_and_events_returned():
    ev = AlertEvent(t_s=NOW, client_id="c-near", incident_id="inc-1",
                    prev=AlertState.NONE, state=AlertState.NOTICE_72H,
                    eta_p50_h=30.0, eta_p10_h=20.0)
    engine = StubAlertEngine(events=[ev])
    mon = make_monitor(alert_engine=engine)
    result = mon.cycle(NOW)
    assert len(engine.seen) == 1
    seen_assessments, seen_now = engine.seen[0]
    assert seen_now == NOW
    assert seen_assessments == result.assessments
    assert result.events == [ev]


def test_inactive_incident_skipped():
    mon = make_monitor(feeds=[StubFeed([incident(active=False)])])
    result = mon.cycle(NOW)
    assert result.incidents == []
    assert result.assessments == []
    assert result.ensembles == {}


def test_incident_fusion_dedupes_same_incident():
    mon = make_monitor(feeds=[StubFeed([incident()]), StubFeed([incident()])])
    result = mon.cycle(NOW)
    # fuse_incidents joins on the IRWIN id (and may canonicalize incident_id
    # to it) — the seam guarantee is ONE incident, consistently identified.
    assert len(result.incidents) == 1
    fused_id = result.incidents[0].incident_id
    pairs = {(a.client_id, a.incident_id) for a in result.assessments}
    assert pairs == {("c-near", fused_id)}
    assert set(result.ensembles) == {fused_id}


# ---------------------------------------------------------------------------
# dispatch wiring
# ---------------------------------------------------------------------------

def _watch_states():
    return {("c-near", "inc-1"): AlertRecord(client_id="c-near",
                                             incident_id="inc-1",
                                             state=AlertState.WATCH)}


def test_dispatcher_called_for_active_alert_states():
    dispatcher = StubDispatcher()
    crew = Crew(crew_id="crew-1", status=CrewStatus.AVAILABLE,
                home_base=geo.destination(ORIGIN, 180.0, 10_000.0))
    zones = [SafeZone("z1", geo.destination(ORIGIN, 180.0, 20_000.0))]
    mon = make_monitor(alert_engine=StubAlertEngine(states=_watch_states()),
                       safe_zones=zones, dispatcher=dispatcher,
                       crew_tracker=StubCrewTracker([crew]))
    result = mon.cycle(NOW)
    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert result.plan is call["plan"]
    assert [j.client.client_id for j in call["jobs"]] == ["c-near"]
    assert call["jobs"][0].incident_id == "inc-1"
    assert math.isfinite(call["jobs"][0].eta.eta_p50_s)
    assert set(call["ens"]) == {"inc-1"}
    assert call["safe_zones"] == zones
    assert [c.crew_id for c in call["crews"]] == ["crew-1"]
    assert call["now_s"] == NOW
    assert call["incumbent"] is None


def test_dispatcher_not_called_without_active_states():
    dispatcher = StubDispatcher()
    states = {("c-near", "inc-1"): AlertRecord(client_id="c-near",
                                               incident_id="inc-1",
                                               state=AlertState.NONE)}
    mon = make_monitor(alert_engine=StubAlertEngine(states=states),
                       dispatcher=dispatcher)
    result = mon.cycle(NOW)
    assert dispatcher.calls == []
    assert result.plan is None


def test_dispatcher_receives_incumbent_on_second_cycle():
    dispatcher = StubDispatcher()
    mon = make_monitor(alert_engine=StubAlertEngine(states=_watch_states()),
                       dispatcher=dispatcher)
    first = mon.cycle(NOW)
    second = mon.cycle(NOW + 900.0)
    assert len(dispatcher.calls) == 2
    assert dispatcher.calls[0]["incumbent"] is None
    assert dispatcher.calls[1]["incumbent"] is first.plan
    assert second.plan is dispatcher.calls[1]["plan"]


def test_stale_alert_pair_without_assessment_is_skipped():
    # Pair in WATCH for an incident that no feed reports anymore.
    states = {("c-gone", "inc-gone"): AlertRecord(client_id="c-gone",
                                                  incident_id="inc-gone",
                                                  state=AlertState.WATCH)}
    dispatcher = StubDispatcher()
    mon = make_monitor(alert_engine=StubAlertEngine(states=states),
                       dispatcher=dispatcher)
    result = mon.cycle(NOW)
    assert dispatcher.calls == []
    assert result.plan is None
    assert any("c-gone" in n and "skipped" in n for n in result.notes)


def test_no_dispatcher_means_no_plan():
    mon = make_monitor(alert_engine=StubAlertEngine(states=_watch_states()))
    result = mon.cycle(NOW)
    assert result.plan is None


# ---------------------------------------------------------------------------
# crew staleness in the live dispatch path (regression: stale phone-GPS crews
# must not be routed on out-of-date positions)
# ---------------------------------------------------------------------------

def _crew_beacon(cid, age_s, status=CrewStatus.AVAILABLE):
    from wgresponse.contracts import CrewBeacon
    return CrewBeacon(crew_id=cid, loc=geo.destination(ORIGIN, 180.0, 8_000.0),
                      t_s=NOW - age_s, status=status)


def test_stale_gps_crew_excluded_from_dispatch():
    """A crew whose last beacon is far older than cfg.beacon_stale_s must NOT
    be handed to the dispatcher (its last-known position is out of date)."""
    from wgresponse.crews import CrewTracker
    cfg = make_cfg(beacon_stale_s=600.0)
    tracker = CrewTracker(cfg)
    tracker.ingest(_crew_beacon("fresh", age_s=60.0))       # 1 min old
    tracker.ingest(_crew_beacon("stale", age_s=7200.0))     # 2 h old
    dispatcher = StubDispatcher()
    mon = make_monitor(alert_engine=StubAlertEngine(states=_watch_states()),
                       dispatcher=dispatcher, crew_tracker=tracker, cfg=cfg)
    result = mon.cycle(NOW)
    assert len(dispatcher.calls) == 1
    routed = {c.crew_id for c in dispatcher.calls[0]["crews"]}
    assert routed == {"fresh"}, routed
    # the stale crew is flipped to UNKNOWN by the monitor's stale() call...
    assert tracker.crew("stale").status == CrewStatus.UNKNOWN
    # ...and the degradation is surfaced to the operator via notes
    assert any("stale" in n.lower() for n in result.notes)


def test_stale_crew_rejected_by_dispatch_filter_defense_in_depth():
    """Even if an UNKNOWN crew reaches dispatch.plan directly, its stale
    last-known location must not make it eligible."""
    from wgresponse import dispatch
    from wgresponse.contracts import CrewBeacon
    beacon = CrewBeacon(crew_id="ghost", loc=ORIGIN, t_s=NOW - 9999.0,
                        status=CrewStatus.UNKNOWN)
    crew = Crew(crew_id="ghost", status=CrewStatus.UNKNOWN, last_beacon=beacon)
    assert crew.loc() is not None                       # stale position present
    plan = dispatch.plan(
        crews=[crew], jobs=[], ens={}, safe_zones=[], cfg=make_cfg(), now_s=NOW)
    # No crew is eligible; nothing is scheduled.
    assert plan.plans == {}


def test_crew_tracker_without_eligible_seam_degrades_gracefully():
    """A tracker exposing only .crews() (older seam) still works — the
    dispatcher's own filter is the safety net for UNKNOWN/OFF_DUTY."""
    crew = Crew(crew_id="crew-1", status=CrewStatus.AVAILABLE,
                home_base=geo.destination(ORIGIN, 180.0, 10_000.0))
    dispatcher = StubDispatcher()
    mon = make_monitor(alert_engine=StubAlertEngine(states=_watch_states()),
                       dispatcher=dispatcher,
                       crew_tracker=StubCrewTracker([crew]))
    result = mon.cycle(NOW)
    assert len(dispatcher.calls) == 1
    assert [c.crew_id for c in dispatcher.calls[0]["crews"]] == ["crew-1"]


# ---------------------------------------------------------------------------
# degradation paths (notes, never raises)
# ---------------------------------------------------------------------------

def test_feed_failure_noted_and_other_feeds_still_used():
    mon = make_monitor(feeds=[RaisingFeed(), StubFeed([incident()])])
    result = mon.cycle(NOW)
    assert any("fetch failed" in n for n in result.notes)
    assert len(result.assessments) == 1


def test_all_feeds_down_yields_empty_cycle():
    mon = make_monitor(feeds=[RaisingFeed()])
    result = mon.cycle(NOW)
    assert result.incidents == []
    assert result.assessments == []
    assert any("fetch failed" in n for n in result.notes)


def test_no_clients_in_range_skips_spread_with_note():
    terrain = StubTerrain()
    mon = make_monitor(registry=StubRegistry([far_client()]), terrain=terrain)
    result = mon.cycle(NOW)
    assert result.assessments == []
    assert result.ensembles == {}
    assert terrain.calls == []                     # no compute wasted
    assert any("no protected clients" in n for n in result.notes)


def test_terrain_failure_noted_not_raised():
    mon = make_monitor(terrain=RaisingTerrain())
    result = mon.cycle(NOW)
    assert result.assessments == []
    assert any("inc-1" in n and "landfire down" in n for n in result.notes)


def test_weather_failure_noted_not_raised():
    mon = make_monitor(weather=RaisingWeather())
    result = mon.cycle(NOW)
    assert result.assessments == []
    assert any("inc-1" in n and "nws down" in n for n in result.notes)


def test_alert_engine_failure_noted_not_raised():
    mon = make_monitor(alert_engine=RaisingAlertEngine())
    result = mon.cycle(NOW)
    assert result.events == []
    assert len(result.assessments) == 1            # assessments still made
    assert any("alert evaluation failed" in n for n in result.notes)


# ---------------------------------------------------------------------------
# domain construction
# ---------------------------------------------------------------------------

def test_grid_coarsens_to_respect_max_cells():
    terrain = StubTerrain()
    client = ClientProperty(client_id="c-30km",
                            loc=geo.destination(ORIGIN, 90.0, 30_000.0))
    cfg = make_cfg(grid_max_cells=2_000)
    mon = make_monitor(registry=StubRegistry([client]), terrain=terrain,
                       cfg=cfg)
    mon.cycle(NOW)
    assert len(terrain.calls) == 1
    bbox, cell_m = terrain.calls[0]
    assert cell_m > cfg.grid_cell_m                # coarsened
    ctr = bbox.center()
    w_m = (bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat)
    h_m = (bbox.north - bbox.south) * geo.M_PER_DEG_LAT
    cells = math.ceil(w_m / cell_m) * math.ceil(h_m / cell_m)
    assert cells <= cfg.grid_max_cells * 1.15      # ceil slack only


def test_domain_covers_ignition_and_near_client():
    terrain = StubTerrain()
    mon = make_monitor(terrain=terrain)
    mon.cycle(NOW)
    bbox, cell_m = terrain.calls[0]
    assert cell_m == make_cfg().grid_cell_m        # small domain: no coarsening
    assert bbox.contains(ORIGIN)
    assert bbox.contains(near_client().loc)
    assert not bbox.contains(far_client().loc)


def test_weather_requested_at_fire_centroid_with_cfg_horizon():
    weather = StubWeather()
    cfg = make_cfg()
    mon = make_monitor(weather=weather, cfg=cfg)
    mon.cycle(NOW)
    (loc, horizon_h), = weather.calls
    assert horizon_h == cfg.horizon_h
    assert abs(loc.lat - ORIGIN.lat) < 0.1
    assert abs(loc.lon - ORIGIN.lon) < 0.1


def test_weather_fetched_at_fire_centroid_not_domain_center():
    """Council-1 model F7 (docs/review/model_fidelity_review.md; evidence
    scratch_review/exp3_weather_sensitivity.py): the forecast point must be
    the FIRE centroid (mean of ignition points). A client far downwind pulls
    the domain-bbox center tens of km from the flames — a marine-layer-biased
    center under-drives a wind-driven inland fire (LATE alert polarity)."""
    weather = StubWeather()
    # Client 30 km east: the ignition+client bbox center sits ~15 km east of
    # the fire; the forecast must ignore that shift.
    client = ClientProperty(client_id="c-30km",
                            loc=geo.destination(ORIGIN, 90.0, 30_000.0))
    mon = make_monitor(weather=weather, registry=StubRegistry([client]))
    mon.cycle(NOW)
    (loc, _), = weather.calls
    # Fire centroid == ORIGIN (single-point ignition); the domain center is
    # ~0.17 deg east. Assert we are at the fire, not the domain center.
    assert abs(loc.lat - ORIGIN.lat) < 0.01
    assert abs(loc.lon - ORIGIN.lon) < 0.01


def test_weather_centroid_is_mean_of_perimeter_ignition_points():
    weather = StubWeather()
    half = 0.01
    ring = [LatLon(ORIGIN.lat - half, ORIGIN.lon - half),
            LatLon(ORIGIN.lat - half, ORIGIN.lon + half),
            LatLon(ORIGIN.lat + half, ORIGIN.lon + half),
            LatLon(ORIGIN.lat + half, ORIGIN.lon - half)]
    inc = FireIncident(incident_id="inc-1", name="Ringed Fire",
                       source=IncidentSource.MOCK, origin=LatLon(0.0, 0.0),
                       updated_at_s=NOW - 1800.0, active=True,
                       perimeter=[ring], irwin_id="IRWIN-1")
    mon = make_monitor(feeds=[StubFeed([inc])], weather=weather)
    mon.cycle(NOW)
    (loc, _), = weather.calls
    # Mean of the ring vertices — NOT the (bogus 0,0) origin field, and not
    # the client-shifted domain center.
    assert loc.lat == pytest.approx(ORIGIN.lat, abs=1e-9)
    assert loc.lon == pytest.approx(ORIGIN.lon, abs=1e-9)


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 6 wiring — provider health -> degraded flag, operator
# events, and the assessed/blind seam into the alert engine (Council-1 OPS-1;
# drills ported end-to-end in tests/test_failsafe.py).
# ---------------------------------------------------------------------------

def test_healthy_cycle_is_not_degraded():
    engine = RichStubAlertEngine()
    result = make_monitor(alert_engine=engine).cycle(NOW)
    assert result.degraded is False
    assert result.operator_events == []
    assert engine.kw == [{"blind_pairs": set(), "feeds_healthy": True}]


def test_unhealthy_feed_marks_cycle_degraded_with_feed_blind_event():
    """A feed returning [] with last_fetch_ok=False is BLIND, not clear."""
    engine = RichStubAlertEngine()
    mon = make_monitor(feeds=[BlindFeed()], alert_engine=engine)
    result = mon.cycle(NOW)
    assert result.degraded is True
    assert [e.kind for e in result.operator_events] == ["feed_blind"]
    assert any("last_fetch_ok" in n for n in result.notes)
    assert engine.kw[0]["feeds_healthy"] is False


def test_raising_feed_marks_cycle_degraded_with_feed_blind_event():
    engine = RichStubAlertEngine()
    mon = make_monitor(feeds=[RaisingFeed(), StubFeed([incident()])],
                       alert_engine=engine)
    result = mon.cycle(NOW)
    assert result.degraded is True
    assert [e.kind for e in result.operator_events] == ["feed_blind"]
    assert engine.kw[0]["feeds_healthy"] is False
    assert len(result.assessments) == 1        # healthy feed still assessed


@pytest.mark.parametrize("kill,step", [
    ("weather", "weather"), ("terrain", "terrain"), ("registry", "registry"),
])
def test_provider_failure_emits_provider_error_and_blind_pairs(kill, step):
    """weather/terrain/registry exceptions per incident: degraded=True, a
    step-tagged OperatorEvent, and the tracked pair fed to the alert engine
    as BLIND (its incident could not be assessed)."""
    tracked = {("c-near", "inc-1"): AlertRecord(client_id="c-near",
                                                incident_id="inc-1",
                                                state=AlertState.EXTREME_24H)}
    engine = RichStubAlertEngine(states=tracked)
    kw = dict(alert_engine=engine)
    if kill == "weather":
        kw["weather"] = RaisingWeather()
    elif kill == "terrain":
        kw["terrain"] = RaisingTerrain()
    else:
        kw["registry"] = RaisingRegistry()
    result = make_monitor(**kw).cycle(NOW)
    assert result.degraded is True
    assert result.assessments == []
    (ev,) = result.operator_events
    assert ev.kind == "provider_error"
    assert ev.incident_id == "inc-1"
    assert ev.detail["step"] == step
    assert engine.kw[0]["blind_pairs"] == {("c-near", "inc-1")}
    assert engine.kw[0]["feeds_healthy"] is True


def test_alert_engine_operator_events_surface_on_cycle_result():
    from wgresponse.contracts import OperatorEvent
    op = OperatorEvent(t_s=NOW, kind="degraded", message="pair frozen",
                       incident_id="inc-1")
    engine = RichStubAlertEngine(op_events=[op])
    result = make_monitor(alert_engine=engine).cycle(NOW)
    assert op in result.operator_events
    assert result.degraded is True     # a 'degraded' operator event flags the cycle


def test_legacy_two_arg_alert_engine_still_evaluated():
    """StubAlertEngine keeps the pre-Sprint-2 evaluate(assessments, now_s)
    signature on purpose: the monitor must fall back to it."""
    engine = StubAlertEngine()
    result = make_monitor(alert_engine=engine).cycle(NOW)
    assert len(engine.seen) == 1
    assert len(result.assessments) == 1


# ---------------------------------------------------------------------------
# Council-1 dispatch F1 regression (A2): the dispatcher must receive EVERY
# assessed ensemble, not just those of incidents that produced jobs.
# Ported from scratch_review/da_exp4_multiincident.py part B, which showed
# dropping a jobless incident's raster routes a crew through its fire.
# ---------------------------------------------------------------------------

def test_dispatcher_receives_all_assessed_ensembles_not_just_job_incidents():
    inc2_origin = geo.destination(ORIGIN, 90.0, 20_000.0)
    feeds = [StubFeed([incident("inc-1"),
                       incident("inc-2", origin=inc2_origin,
                                irwin="IRWIN-2")])]
    dispatcher = StubDispatcher()
    # Only inc-1 has an alert pair -> only inc-1 produces a DispatchJob;
    # inc-2 is assessed (client within threat radius) but jobless.
    mon = make_monitor(feeds=feeds, registry=StubRegistry([near_client()]),
                       alert_engine=StubAlertEngine(states=_watch_states()),
                       dispatcher=dispatcher)
    result = mon.cycle(NOW)
    assert set(result.ensembles) == {"inc-1", "inc-2"}
    (call,) = dispatcher.calls
    assert {j.incident_id for j in call["jobs"]} == {"inc-1"}
    # THE regression: jobless inc-2's raster still reaches corridor checks.
    assert set(call["ens"]) == {"inc-1", "inc-2"}


# ---------------------------------------------------------------------------
# Sprint 2 ticket A5 (Council-1 model F4) — perimeter interior seeding through
# the monitor: inside clients ride the IMPACTED ladder path, hotspots merge
# even when a perimeter exists, rings are subsampled, and no-perimeter
# incidents stay byte-identical to the legacy ignition_points() seed law.
# Evidence: scratch_review/exp5_perimeter_seeding.py; review §F4.
# ---------------------------------------------------------------------------

def _square_ring_m(center, half_m):
    """4-corner square ring around `center` (interior rasterization fills it;
    vertex density deliberately minimal, per the exp5 sparsity finding)."""
    nw = geo.destination(geo.destination(center, 0.0, half_m), 270.0, half_m)
    ne = geo.destination(nw, 90.0, 2.0 * half_m)
    se = geo.destination(ne, 180.0, 2.0 * half_m)
    sw = geo.destination(se, 270.0, 2.0 * half_m)
    return [nw, ne, se, sw]


def perimeter_incident(updated_age_s=1800.0, hotspots=None, ring_half_m=1500.0):
    return FireIncident(incident_id="inc-1", name="Perimeter Fire",
                        source=IncidentSource.MOCK, origin=ORIGIN,
                        updated_at_s=NOW - updated_age_s, active=True,
                        perimeter=[_square_ring_m(ORIGIN, ring_half_m)],
                        hotspots=hotspots, irwin_id="IRWIN-1")


def test_client_inside_perimeter_rides_impacted_ladder_path():
    """A5 acceptance: a client 200 m inside the downwind edge of the fused
    perimeter reads arrival == the feed's updated_at_s => hours_out == 0
    (ThreatEta assert), and through a REAL AlertEngine the pair escalates
    NONE -> EXTREME_24H on the first cycle and EXTREME_24H -> IMPACTED on the
    next evaluate (IMPACTED is only reachable from EXTREME per the contracts
    ladder). Pre-fix exp5 measured +4.3 h of phantom runway for this client
    (alerts showed EXTREME with hours to spare while the property burned)."""
    from wgresponse.alerts import AlertEngine
    inside_edge = ClientProperty(
        client_id="c-inside", loc=geo.destination(ORIGIN, 90.0, 1300.0))
    inside_center = ClientProperty(client_id="c-center", loc=ORIGIN)
    cfg = make_cfg()
    engine = AlertEngine(cfg)
    mon = make_monitor(feeds=[StubFeed([perimeter_incident()])],
                       registry=StubRegistry([inside_edge, inside_center]),
                       alert_engine=engine, cfg=cfg)
    first = mon.cycle(NOW)
    etas = {a.client_id: a.eta for a in first.assessments}
    assert set(etas) == {"c-inside", "c-center"}
    for eta in etas.values():
        assert eta.eta_p50_s == NOW - 1800.0          # burned at feed time
        assert eta.hours_out(NOW) == (0.0, 0.0, 0.0)  # ~0 h out NOW
    fused_id = first.incidents[0].incident_id
    states = engine.states()
    assert states[("c-inside", fused_id)].state is AlertState.EXTREME_24H
    assert {(e.prev, e.state) for e in first.events} == \
        {(AlertState.NONE, AlertState.EXTREME_24H)}
    second = mon.cycle(NOW + 900.0)                   # the next evaluate
    states = engine.states()
    assert states[("c-inside", fused_id)].state is AlertState.IMPACTED
    assert states[("c-center", fused_id)].state is AlertState.IMPACTED
    assert {(e.prev, e.state) for e in second.events} == \
        {(AlertState.EXTREME_24H, AlertState.IMPACTED)}


def test_hotspots_seed_even_when_perimeter_exists():
    """A5: fresh FIRMS detections beyond a stale ring must advance the seeds
    (legacy ignition_points() DISCARDED hotspots whenever a perimeter
    existed). A hotspot 5 km downwind of the ring pulls a far client's ETA
    strictly earlier vs the same incident without hotspots."""
    hotspot = geo.destination(ORIGIN, 90.0, 5000.0)
    far_east = ClientProperty(client_id="c-east",
                              loc=geo.destination(ORIGIN, 90.0, 8000.0))
    cfg = make_cfg()

    def eta_for(inc):
        mon = make_monitor(feeds=[StubFeed([inc])],
                           registry=StubRegistry([far_east]), cfg=cfg)
        result = mon.cycle(NOW)
        (a,) = result.assessments
        return a.eta

    eta_with = eta_for(perimeter_incident(hotspots=[hotspot]))
    eta_without = eta_for(perimeter_incident(hotspots=None))
    assert math.isfinite(eta_with.eta_p50_s)
    assert math.isfinite(eta_without.eta_p50_s)
    assert eta_with.eta_p50_s < eta_without.eta_p50_s - 3600.0


def test_perimeter_rings_subsampled_and_stamped_with_updated_at(monkeypatch):
    """A5 plumbing: threat passes perimeter rings SUBSAMPLED to <= 200
    vertices (contracts law: 'rings subsampled <= 200 verts upstream'),
    perimeter_at_s == the incident's updated_at_s, seeds == hotspots (ring
    vertices no longer seed), and incident_id == the fused id (the ensemble
    seeding law)."""
    from wgresponse import spread as spread_mod
    dense_ring = [geo.destination(ORIGIN, k * 360.0 / 1200.0, 1500.0)
                  for k in range(1200)]
    hotspot = geo.destination(ORIGIN, 90.0, 5000.0)
    inc = FireIncident(incident_id="inc-1", name="Dense Ring Fire",
                       source=IncidentSource.MOCK, origin=ORIGIN,
                       updated_at_s=NOW - 7200.0, active=True,
                       perimeter=[dense_ring], hotspots=[hotspot],
                       irwin_id="IRWIN-1")
    captured = {}
    orig = spread_mod.ensemble_arrival

    def spy(tile, ignition, wx, t0_s, cfg, incident_id="", perimeter=None,
            perimeter_at_s=None):
        captured.update(ignition=list(ignition), incident_id=incident_id,
                        perimeter=perimeter, perimeter_at_s=perimeter_at_s)
        return orig(tile, ignition, wx, t0_s, cfg, incident_id=incident_id,
                    perimeter=perimeter, perimeter_at_s=perimeter_at_s)

    monkeypatch.setattr(spread_mod, "ensemble_arrival", spy)
    mon = make_monitor(feeds=[StubFeed([inc])],
                       registry=StubRegistry([near_client()]))
    result = mon.cycle(NOW)
    assert captured, "spread.ensemble_arrival never called"
    assert captured["perimeter_at_s"] == NOW - 7200.0
    assert len(captured["perimeter"]) == 1
    assert len(captured["perimeter"][0]) <= 200
    assert captured["ignition"] == [hotspot]           # hotspots, not verts
    assert captured["incident_id"] == result.incidents[0].incident_id


def test_subsample_ring_helper():
    from wgresponse.threat import _subsample_ring
    ring = [LatLon(39.0, -121.0 + i * 1e-4) for i in range(1200)]
    out = _subsample_ring(ring)
    assert len(out) <= 200
    assert out[0] == ring[0]
    assert all(p in ring for p in out)
    small = ring[:37]
    assert _subsample_ring(small) == small             # under cap: unchanged


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 9 — staleness gate (Sprint 2 ticket C3a, Council-1 OPS-6).
# Live FIRIS "current" perimeters measured at MEDIAN 49 days old, all
# active=True: the MONITOR gates them out of alerting/dispatch (feeds still
# return them — data preserved); logged + CycleResult note; no compute spent.
# ---------------------------------------------------------------------------

def aged_incident(age_h, iid="inc-zombie", irwin="IRWIN-Z", origin=ORIGIN,
                  name=None):
    return FireIncident(incident_id=iid, name=name or f"Zombie {iid}",
                        source=IncidentSource.MOCK, origin=origin,
                        updated_at_s=NOW - age_h * 3600.0, active=True,
                        irwin_id=irwin)


def test_49_day_zombie_excluded_from_alerting_and_dispatch():
    """The measured FIRIS-median zombie (49 days) near a client: never
    assessed, no ensemble, no terrain compute, excluded from
    CycleResult.incidents, noted + NOT a degradation (hygiene, not
    blindness)."""
    terrain = StubTerrain()
    engine = RichStubAlertEngine()
    zombie = aged_incident(49 * 24.0)                  # 1176 h
    mon = make_monitor(feeds=[StubFeed([incident(), zombie])],
                       terrain=terrain, alert_engine=engine)
    result = mon.cycle(NOW)
    pairs = {(a.client_id, a.incident_id) for a in result.assessments}
    assert pairs == {("c-near", "inc-1")}              # zombie never assessed
    assert set(result.ensembles) == {"inc-1"}
    assert [i.incident_id for i in result.incidents] == ["inc-1"]
    assert len(terrain.calls) == 1                     # no compute wasted (OPS-6)
    note = next(n for n in result.notes if "stale" in n)
    assert "excluded 1 stale incident(s)" in note
    assert "inc-zombie" in note and "1176 h" in note
    assert result.degraded is False                    # hygiene != provider failure
    assert result.operator_events == []
    assert engine.kw == [{"blind_pairs": set(), "feeds_healthy": True}]


def test_six_day_old_incident_still_assessed():
    """Age 144 h < incident_stale_h 168 h: assessed exactly like a fresh one."""
    mon = make_monitor(feeds=[StubFeed([aged_incident(
        6 * 24.0, iid="inc-1", irwin="IRWIN-1")])])
    result = mon.cycle(NOW)
    assert {(a.client_id, a.incident_id) for a in result.assessments} == \
        {("c-near", "inc-1")}
    assert not any("stale" in n for n in result.notes)


def test_staleness_boundary_exact_age_kept_just_over_excluded():
    cfg = make_cfg()
    max_age_s = cfg.incident_stale_h * 3600.0

    def cycle_with(updated_at_s):
        inc = aged_incident(0.0, iid="inc-1", irwin="IRWIN-1")
        inc.updated_at_s = updated_at_s
        mon = make_monitor(feeds=[StubFeed([inc])], cfg=cfg)
        return mon.cycle(NOW)

    exact = cycle_with(NOW - max_age_s)                # exactly 168 h old
    assert [i.incident_id for i in exact.incidents] == ["inc-1"]
    assert len(exact.assessments) == 1                 # kept: strictly-older excludes
    over = cycle_with(NOW - max_age_s - 1.0)           # one second older
    assert over.incidents == [] and over.assessments == []
    assert any("excluded 1 stale" in n for n in over.notes)


def test_staleness_gate_respects_config_knob():
    cfg = make_cfg(incident_stale_h=48.0)
    mon = make_monitor(feeds=[StubFeed([aged_incident(
        72.0, iid="inc-1", irwin="IRWIN-1")])], cfg=cfg)
    result = mon.cycle(NOW)
    assert result.incidents == []
    assert any("48 h" in n and "stale" in n for n in result.notes)


def test_stale_incident_watch_pair_spawns_no_dispatch_job():
    """A pair already at WATCH whose incident has gone stale: excluded from
    DISPATCH too — no job, no plan (the pair was not assessed this cycle)."""
    zombie = aged_incident(49 * 24.0, iid="inc-1", irwin="IRWIN-1")
    dispatcher = StubDispatcher()
    mon = make_monitor(feeds=[StubFeed([zombie])],
                       alert_engine=StubAlertEngine(states=_watch_states()),
                       dispatcher=dispatcher)
    result = mon.cycle(NOW)
    assert dispatcher.calls == []
    assert result.plan is None
    assert any("excluded 1 stale" in n for n in result.notes)


def test_tracked_pair_walks_to_cleared_when_incident_goes_stale():
    """Characterization (Council-visible policy pin): a tracked pair whose
    incident crosses incident_stale_h with EVERY feed healthy is an ordinary
    trustworthy absence (SAFETY SEMANTICS 6 — the monitor itself withheld the
    incident after 7 days of multi-source silence), so it walks to CLEARED
    through normal clear_cycles hysteresis with blind_cycles untouched.
    Freezing instead would alarm operators forever on every long-dead FIRIS
    zombie that ever escalated a pair. During an outage the same pair
    freezes like any other unassessed pair (pinned in test_failsafe.py /
    test_attack_false_clear.py — the gate feeds the SAME absence machinery,
    so feeds_healthy=False keeps overriding toward FREEZE)."""
    from wgresponse.alerts import AlertEngine
    cfg = make_cfg()
    engine = AlertEngine(cfg)
    feed = StubFeed([incident()])                      # fresh at NOW - 1800 s
    mon = make_monitor(feeds=[feed], alert_engine=engine, cfg=cfg)
    first = mon.cycle(NOW)
    (key,) = {(a.client_id, a.incident_id) for a in first.assessments}
    assert engine.states()[key].state is not AlertState.NONE   # pair tracked hot
    # the SAME incident record goes stale: no source has updated it in 8 days
    feed._incidents = [aged_incident(8 * 24.0, iid="inc-1", irwin="IRWIN-1")]
    for k in range(1, cfg.clear_cycles + 1):
        res = mon.cycle(NOW + k * 900.0)
        assert res.degraded is False
        rec = engine.states()[key]
        assert rec.blind_cycles == 0                   # NOT blind: hygiene absence
        if k < cfg.clear_cycles:
            assert rec.state is not AlertState.CLEARED  # hysteresis intact
    assert engine.states()[key].state is AlertState.CLEARED


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 9 — clock clamp (Sprint 2 ticket C3e): a feed clock
# running ahead of ours must not stamp a perimeter interior in the future.
# ---------------------------------------------------------------------------

def test_future_stamped_perimeter_seeds_at_now_not_the_future(monkeypatch):
    """Skewed feed clock (+2 h): perimeter_at_s reaching spread is clamped to
    now_s, and a client inside the polygon reads hours_out == 0 — NOT a
    phantom 2 h of runway from a fire it is already standing in."""
    from wgresponse import spread as spread_mod
    inc = perimeter_incident(updated_age_s=-7200.0)    # updated 2 h in the FUTURE
    inside = ClientProperty(client_id="c-inside", loc=ORIGIN)
    captured = {}
    orig = spread_mod.ensemble_arrival

    def spy(tile, ignition, wx, t0_s, cfg, incident_id="", perimeter=None,
            perimeter_at_s=None):
        captured["perimeter_at_s"] = perimeter_at_s
        return orig(tile, ignition, wx, t0_s, cfg, incident_id=incident_id,
                    perimeter=perimeter, perimeter_at_s=perimeter_at_s)

    monkeypatch.setattr(spread_mod, "ensemble_arrival", spy)
    mon = make_monitor(feeds=[StubFeed([inc])],
                       registry=StubRegistry([inside]))
    result = mon.cycle(NOW)
    assert captured["perimeter_at_s"] == NOW           # clamped (was NOW + 7200)
    (a,) = result.assessments
    assert a.eta.eta_p50_s == NOW                      # burned NOW, not in 2 h
    assert a.eta.hours_out(NOW) == (0.0, 0.0, 0.0)
    # a future stamp is fresh, never stale-gated (negative age)
    assert [i.incident_id for i in result.incidents] == ["inc-1"]


def test_past_stamped_perimeter_unaffected_by_clamp(monkeypatch):
    """min(updated_at_s, now_s) is the identity for every past stamp — the
    A5 stale-perimeter law (interior burned at the FEED time) is untouched."""
    from wgresponse import spread as spread_mod
    captured = {}
    orig = spread_mod.ensemble_arrival

    def spy(tile, ignition, wx, t0_s, cfg, incident_id="", perimeter=None,
            perimeter_at_s=None):
        captured["perimeter_at_s"] = perimeter_at_s
        return orig(tile, ignition, wx, t0_s, cfg, incident_id=incident_id,
                    perimeter=perimeter, perimeter_at_s=perimeter_at_s)

    monkeypatch.setattr(spread_mod, "ensemble_arrival", spy)
    mon = make_monitor(feeds=[StubFeed([perimeter_incident(
        updated_age_s=7200.0)])])
    mon.cycle(NOW)
    assert captured["perimeter_at_s"] == NOW - 7200.0  # feed time preserved


def test_no_perimeter_incident_byte_identical_to_legacy_seed_law():
    """A5 hard gate: an incident WITHOUT a perimeter must flow through the
    explicit-seed path byte-identically to the legacy law — seeds are exactly
    ignition_points() (hotspots, else origin) and no perimeter kwargs reach
    spread: every ensemble member equals a direct ensemble_arrival() call on
    the same tile/forecast with those seeds."""
    hotspots = [geo.destination(ORIGIN, 45.0, 700.0),
                geo.destination(ORIGIN, 200.0, 900.0)]
    inc = FireIncident(incident_id="inc-1", name="Points Fire",
                       source=IncidentSource.MOCK, origin=ORIGIN,
                       updated_at_s=NOW - 1800.0, active=True,
                       hotspots=list(hotspots), irwin_id="IRWIN-1")
    terrain = StubTerrain()
    weather = StubWeather()
    cfg = make_cfg()
    mon = make_monitor(feeds=[StubFeed([inc])], terrain=terrain,
                       weather=weather, cfg=cfg)
    result = mon.cycle(NOW)
    fused_id = result.incidents[0].incident_id
    ens = result.ensembles[fused_id]
    # rebuild the identical tile + forecast from the recorded provider calls
    tile = StubTerrain().tile(*terrain.calls[0])
    wx = StubWeather().forecast(*weather.calls[0])
    from wgresponse import spread as spread_mod
    expected = spread_mod.ensemble_arrival(
        tile, inc.ignition_points(), wx, NOW, cfg, incident_id=fused_id)
    assert len(ens.members) == len(expected.members)
    for got, want in zip(ens.members, expected.members):
        assert got.data == want.data                   # byte-identical
    assert ens.p10.data == expected.p10.data
    assert ens.p50.data == expected.p50.data
    assert ens.p90.data == expected.p90.data
