"""Integrator conformance suite: every adapter satisfies its contracts.py
Protocol seam, module-level function seams match the pinned signatures,
the default ResponseConfig validates clean, and the ALERT_TRANSITIONS
ladder is a sane closed graph.

No network, no third-party deps: adapters are only CONSTRUCTED (with a
poisoned http_get where injectable) and inspected — live fetch methods are
never invoked. Times unix seconds, distances meters per contracts.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from typing import Protocol, runtime_checkable

import pytest

from wgresponse import (
    alerts,
    clients,
    contracts,
    crews,
    dispatch,
    feeds,
    fuels,
    geo,
    notify,
    rothermel,
    safety,
    sim,
    spread,
    terrain,
    threat,
    travel,
    weather,
)
from wgresponse.contracts import (
    ALERT_TRANSITIONS,
    AlertState,
    FuelModel,
    ResponseConfig,
    assert_valid_transition,
    validate_config,
)


def _boom_http_get(url, headers=None):  # pragma: no cover - must never run
    raise AssertionError(f"network call attempted during conformance test: {url}")


# ---------------------------------------------------------------------------
# runtime_checkable mirrors of the contracts protocols (subclassing does not
# mutate contracts.py; isinstance checks method PRESENCE, signatures below)
# ---------------------------------------------------------------------------

@runtime_checkable
class _RFireFeed(contracts.FireFeed, Protocol):
    pass


@runtime_checkable
class _RWeatherProvider(contracts.WeatherProvider, Protocol):
    pass


@runtime_checkable
class _RTerrainProvider(contracts.TerrainProvider, Protocol):
    pass


@runtime_checkable
class _RClientRegistry(contracts.ClientRegistry, Protocol):
    pass


@runtime_checkable
class _RNotifier(contracts.Notifier, Protocol):
    pass


def _fire_feeds():
    return [
        feeds.WfigsFeed(http_get=_boom_http_get),
        feeds.FirisFeed(http_get=_boom_http_get),
        feeds.CalFireFeed(http_get=_boom_http_get),
        feeds.FirmsFeed(map_key="TESTKEY", http_get=_boom_http_get),
        feeds.WatchDutyEnterpriseFeed(),
        feeds.MockFireFeed(seed="conformance"),
    ]


def _weather_providers():
    return [
        weather.NwsWeather(http_get=_boom_http_get),
        weather.OpenMeteoWeather(http_get=_boom_http_get),
        weather.MockWeather(seed="conformance"),
    ]


def _terrain_providers():
    return [
        terrain.LfpsTerrain(http_get=_boom_http_get),
        terrain.SyntheticTerrain(seed="conformance"),
    ]


def _client_registries():
    return [
        clients.DynamoClientRegistry(scan_fn=lambda: []),
        clients.MockClientRegistry(seed="conformance"),
    ]


def _notifiers():
    return [notify.LogNotifier(), notify.SesNotifier(), notify.SmsNotifier()]


def _params(fn):
    """Parameter names of fn, minus self."""
    names = list(inspect.signature(fn).parameters)
    return [n for n in names if n != "self"]


def _required_params(fn):
    """Names of parameters without defaults (minus self / var-args)."""
    sig = inspect.signature(fn)
    return [
        n for n, p in sig.parameters.items()
        if n != "self"
        and p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    ]


# ---------------------------------------------------------------------------
# Adapter x Protocol conformance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("obj", _fire_feeds(), ids=lambda o: type(o).__name__)
def test_fire_feed_protocol(obj):
    assert isinstance(obj, _RFireFeed)
    sig = inspect.signature(obj.fetch_active)
    params = list(sig.parameters.values())
    # exactly the pinned seam: fetch_active(bbox: Optional[BBox] = None)
    assert [p.name for p in params] == ["bbox"]
    assert params[0].default is None


@pytest.mark.parametrize("obj", _weather_providers(), ids=lambda o: type(o).__name__)
def test_weather_provider_protocol(obj):
    assert isinstance(obj, _RWeatherProvider)
    assert _params(obj.forecast) == ["loc", "horizon_h"]


@pytest.mark.parametrize("obj", _terrain_providers(), ids=lambda o: type(o).__name__)
def test_terrain_provider_protocol(obj):
    assert isinstance(obj, _RTerrainProvider)
    assert _params(obj.tile) == ["bbox", "cell_m"]


@pytest.mark.parametrize("obj", _client_registries(), ids=lambda o: type(o).__name__)
def test_client_registry_protocol(obj):
    assert isinstance(obj, _RClientRegistry)
    sig = inspect.signature(obj.protected_clients)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["bbox"]
    assert params[0].default is None


@pytest.mark.parametrize("obj", _notifiers(), ids=lambda o: type(o).__name__)
def test_notifier_protocol(obj):
    assert isinstance(obj, _RNotifier)
    assert _params(obj.send) == ["event", "client"]


def test_crew_tracker_seam():
    t = crews.CrewTracker(ResponseConfig())
    for method, req in (("ingest", ["beacon"]), ("crews", []),
                        ("crew", ["crew_id"]), ("stale", ["now_s"])):
        fn = getattr(t, method, None)
        assert callable(fn), f"CrewTracker.{method} missing"
        assert _required_params(fn) == req, f"CrewTracker.{method} signature drift"


def test_alert_engine_seam():
    eng = alerts.AlertEngine(ResponseConfig())
    assert callable(eng.evaluate) and callable(eng.states)
    assert len(_required_params(eng.evaluate)) == 2  # (assessments, now_s)
    assert _required_params(eng.states) == []
    assert eng.states() == {}


def test_threat_monitor_seam():
    assert callable(getattr(threat.ThreatMonitor, "cycle"))
    assert _params(threat.ThreatMonitor.cycle) == ["now_s"]


# ---------------------------------------------------------------------------
# Module-level function seams pinned in the contracts header
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,expected", [
    (geo.haversine_m, ["a", "b"]),
    (geo.destination, ["origin", "bearing_deg", "dist_m"]),
    (geo.bearing_deg, ["a", "b"]),
    (geo.sample_polyline, ["a", "b", "step_m"]),
    (geo.bbox_around, ["points", "pad_m"]),
    (geo.erode_min, ["r", "radius_m"]),
    (fuels.is_burnable, ["code"]),
    (fuels.dead_fuel_moisture_frac, ["temp_c", "rh_pct"]),
    (fuels.live_moisture_defaults, ["season"]),
    (rothermel.midflame_wind_mps, ["wind10_mps", "fuel"]),
    (rothermel.ellipse_lb, ["effective_wind_mps"]),
    (rothermel.directional_ros_mps, ["head_ros_mps", "lb", "theta_deg"]),
    (feeds.fuse_incidents, ["batches"]),
    (safety.latest_departure_s, ["eta_p10_s", "cfg"]),
    (travel.travel_time_s, ["a", "b", "cfg"]),
    (spread.eta_at, ["ens", "loc"]),
], ids=lambda x: getattr(x, "__qualname__", str(x)))
def test_function_seam_signatures(fn, expected):
    assert _params(fn)[: len(expected)] == expected


def test_rothermel_surface_ros_seam():
    assert _params(rothermel.surface_ros_mps) == [
        "fuel", "m_dead_frac", "m_live_herb_frac", "m_live_woody_frac",
        "midflame_wind_mps", "slope_deg",
    ]


def test_spread_seams():
    sig = inspect.signature(spread.compute_arrival)
    assert list(sig.parameters)[:5] == ["tile", "ignition", "wx", "t0_s", "cfg"]
    assert sig.parameters["moisture_scale"].default == 1.0
    assert sig.parameters["wind_scale"].default == 1.0
    assert _params(spread.ensemble_arrival)[:5] == [
        "tile", "ignition", "wx", "t0_s", "cfg"]


def test_safety_seams():
    assert _params(safety.corridor_safe) == [
        "a", "b", "depart_s", "speed_mps", "eroded_p10", "cfg"]
    assert _params(safety.egress_ok) == [
        "stop_loc", "depart_s", "safe_zones", "eroded_p10", "cfg"]


def test_dispatch_plan_seam():
    # Contract seam prefix (same prefix-pin style as test_spread_seams, whose
    # seam also gained documented additive kwargs in Sprint 2): the law's
    # positional shape is frozen; `forecasts` is the Sprint 2 C5 additive
    # per-incident WeatherForecast map (default None = prior behavior).
    sig = inspect.signature(dispatch.plan)
    assert list(sig.parameters)[:7] == [
        "crews", "jobs", "ens", "safe_zones", "cfg", "now_s", "incumbent"]
    assert sig.parameters["incumbent"].default is None
    assert sig.parameters["forecasts"].default is None


def test_sim_seams():
    assert _params(sim.build_scenario)[0] == "name"
    assert _params(sim.run)[:2] == ["scenario", "hours"]


def test_fuel_table_shape():
    assert isinstance(fuels.FUEL_TABLE, dict) and fuels.FUEL_TABLE
    for code, fm in fuels.FUEL_TABLE.items():
        assert isinstance(fm, FuelModel) and fm.code == code
    # NB 91..99 non-burnable, listed grass burnable
    for nb in range(91, 100):
        assert not fuels.is_burnable(nb)
    assert fuels.is_burnable(102)  # GR2


# ---------------------------------------------------------------------------
# Config + alert-ladder law
# ---------------------------------------------------------------------------

def test_default_config_is_valid():
    assert validate_config(ResponseConfig()) == []


def test_validate_config_flags_bad_values():
    bad = ResponseConfig(extreme_h=80.0, gel_window_h=0.0, ensemble_n=0,
                         grid_cell_m=10.0, deploy_min=0.0)
    problems = validate_config(bad)
    assert len(problems) == 5


def test_alert_transitions_closure():
    states = set(AlertState)
    # every state has an entry; every target is a legal state and not a self-loop
    assert set(ALERT_TRANSITIONS) == states
    for src, targets in ALERT_TRANSITIONS.items():
        assert targets <= states
        assert src not in targets
    # NONE is an origin only — no ladder path returns to NONE
    for targets in ALERT_TRANSITIONS.values():
        assert AlertState.NONE not in targets
    # IMPACTED is terminal
    assert ALERT_TRANSITIONS[AlertState.IMPACTED] == set()
    # every non-NONE state is reachable from NONE (transitive closure)
    reached = {AlertState.NONE}
    frontier = [AlertState.NONE]
    while frontier:
        nxt = ALERT_TRANSITIONS[frontier.pop()] - reached
        reached |= nxt
        frontier.extend(nxt)
    assert reached == states


def test_assert_valid_transition_semantics():
    assert_valid_transition(AlertState.NONE, AlertState.NOTICE_72H)
    assert_valid_transition(AlertState.WATCH, AlertState.WATCH)  # same-state ok
    with pytest.raises(ValueError):
        assert_valid_transition(AlertState.NONE, AlertState.IMPACTED)
    with pytest.raises(ValueError):
        assert_valid_transition(AlertState.IMPACTED, AlertState.WATCH)


# ---------------------------------------------------------------------------
# Package init stays lazy
# ---------------------------------------------------------------------------

def test_package_init_imports_nothing_heavy():
    """`import wgresponse` must not drag in submodules (lazy package root)."""
    code = (
        "import sys, wgresponse;"
        "mods = [m for m in sys.modules if m.startswith('wgresponse.')];"
        "assert mods == [], f'wgresponse/__init__.py imports: {mods}';"
        "assert wgresponse.__version__"
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
