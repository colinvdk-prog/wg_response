"""wgresponse.sim — deterministic scenario builder + closed-loop response simulator.

Public seam (contracts):
    build_scenario(name, seed) -> Scenario
    run(scenario, hours, cfg) -> SimReport

Everything here is deterministic: all randomness flows through
contracts.seeded_rng, all clocks are simulated unix seconds anchored at the
scenario's fixed t0_s. No wall-clock, no network, stdlib only.

Sibling integration: contract-pinned FUNCTION seams (spread.ensemble_arrival,
spread.eta_at, dispatch.plan, safety.*, travel.travel_time_s, geo.erode_min)
plus AlertEngine / CrewTracker / LogNotifier are preferred when importable
(default_stack(use_siblings=True)); each falls back to a self-contained
internal implementation so the simulator runs before siblings land. The mock
data providers (fire feed / weather / terrain / client registry) are built
in-module from the Scenario because sibling mock constructors are not pinned
by contracts; they conform exactly to the FireFeed / WeatherProvider /
TerrainProvider / ClientRegistry protocols.

Units: absolute times unix seconds (_s); durations hours (_h) or minutes
(_min); distances meters (_m); speeds m/s (_mps); bearings compass degrees
(0 = north, clockwise). Wind direction is meteorological (blowing FROM).
"""
from __future__ import annotations

import heapq
import importlib
import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from wgresponse.contracts import (
    SCHEMA_VERSION,
    UNREACHED,
    AlertEvent,
    AlertRecord,
    AlertState,
    BBox,
    ClientProperty,
    CorridorVerdict,
    Crew,
    CrewBeacon,
    CrewPlan,
    CrewStatus,
    DispatchJob,
    DispatchPlan,
    EnsembleArrival,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    PlannedStop,
    Raster,
    ResponseConfig,
    SafeZone,
    TerrainTile,
    ThreatAssessment,
    ThreatEta,
    UnservedJob,
    WeatherForecast,
    WeatherSample,
    assert_valid_transition,
    seeded_rng,
)

_LOG = logging.getLogger("wgresponse.sim")

_M_PER_DEG_LAT = 111_320.0
_EARTH_R_M = 6_371_000.0


# ---------------------------------------------------------------------------
# Local geodesy helpers (self-contained; geo.py owns the canonical versions)
# ---------------------------------------------------------------------------

def _m_per_deg_lon(lat_deg: float) -> float:
    """Meters per degree of longitude at latitude `lat_deg` (degrees)."""
    return _M_PER_DEG_LAT * max(0.05, math.cos(math.radians(lat_deg)))


def _haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in meters."""
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (math.sin((la2 - la1) / 2.0) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2.0) ** 2)
    return 2.0 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def _destination(origin: LatLon, bearing_deg: float, dist_m: float) -> LatLon:
    """Point `dist_m` meters from origin along compass `bearing_deg` (degrees)."""
    br = math.radians(bearing_deg)
    la1 = math.radians(origin.lat)
    lo1 = math.radians(origin.lon)
    ang = dist_m / _EARTH_R_M
    la2 = math.asin(math.sin(la1) * math.cos(ang)
                    + math.cos(la1) * math.sin(ang) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(la1),
                           math.cos(ang) - math.sin(la1) * math.sin(la2))
    return LatLon(math.degrees(la2), math.degrees(lo2))


def _bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial compass bearing (degrees, 0=N clockwise) from a to b."""
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def _interp(a: LatLon, b: LatLon, f: float) -> LatLon:
    """Linear lat/lon interpolation (adequate at the sim's km scales)."""
    return LatLon(a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f)


def _sample_polyline(a: LatLon, b: LatLon, step_m: float) -> list:
    """Points every ~step_m meters from a to b inclusive of both endpoints."""
    d = _haversine_m(a, b)
    n = max(1, int(math.ceil(d / max(step_m, 1.0))))
    return [_interp(a, b, i / n) for i in range(n + 1)]


def _cell_of(spec: GridSpec, p: LatLon):
    """(row, col) of p on spec per the contracts GridSpec convention, or None
    when p is outside the grid."""
    r = int((spec.north_lat - p.lat) * _M_PER_DEG_LAT / spec.cell_m)
    c = int((p.lon - spec.west_lon) * _m_per_deg_lon(p.lat) / spec.cell_m)
    if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
        return (r, c)
    return None


def _cell_center(spec: GridSpec, r: int, c: int) -> LatLon:
    """Center lat/lon of cell (r, c) per the contracts GridSpec docstring."""
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / _M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / _m_per_deg_lon(lat)
    return LatLon(lat, lon)


def _sample_raster(rast: Raster, p: LatLon) -> float:
    """Nearest-cell raster value at p; UNREACHED when p is outside the grid."""
    rc = _cell_of(rast.spec, p)
    if rc is None:
        return UNREACHED
    return rast.get(*rc)


def _grid_for_points(points: list, pad_m: float, cell_m: float):
    """(GridSpec, BBox) covering all points with pad_m meters of margin."""
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    lat_c = (min(lats) + max(lats)) / 2.0
    dlat = pad_m / _M_PER_DEG_LAT
    dlon = pad_m / _m_per_deg_lon(lat_c)
    bbox = BBox(west=min(lons) - dlon, south=min(lats) - dlat,
                east=max(lons) + dlon, north=max(lats) + dlat)
    nrows = max(2, int(math.ceil((bbox.north - bbox.south) * _M_PER_DEG_LAT / cell_m)))
    ncols = max(2, int(math.ceil((bbox.east - bbox.west) * _m_per_deg_lon(lat_c) / cell_m)))
    spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west,
                    cell_m=cell_m, nrows=nrows, ncols=ncols)
    return spec, bbox


def _eff_ground_speed_mps(cfg: ResponseConfig) -> float:
    """Straight-line ground speed (m/s) consistent with the travel-time model:
    covering haversine distance at this speed takes exactly travel_time_s."""
    return cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)


# ---------------------------------------------------------------------------
# JSON helper (dataclasses.asdict semantics + enum + inf/nan handling)
# ---------------------------------------------------------------------------

def jsonable(obj):
    """Recursively convert dataclasses/Enums/containers to JSON-safe values.

    Enums -> .value; inf/-inf/NaN floats -> None (JSON has no Infinity);
    Raster -> omitted marker string (rasters are bulk data, not report data);
    tuple dict keys -> 'a|b' strings. Deterministic for deterministic inputs.
    """
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return None if (math.isinf(obj) or math.isnan(obj)) else obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Raster):
        return f"<raster {obj.spec.nrows}x{obj.spec.ncols}>"
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, tuple):
                k = "|".join(str(x) for x in k)
            out[str(k)] = jsonable(v)
        return out
    if isinstance(obj, (list, tuple, set)):
        seq = sorted(obj, key=str) if isinstance(obj, set) else obj
        return [jsonable(v) for v in seq]
    return str(obj)


_TIMING_KEYS = {"solver_s", "solve_s", "wall_s", "elapsed_s", "runtime_s",
                "solver_wall_s", "computed_wall_s"}


def _strip_timing(obj):
    """Drop wall-clock timing keys (e.g. a dispatch solver's solver_s) from a
    jsonable tree so report JSON stays byte-identical across runs."""
    if isinstance(obj, dict):
        return {k: _strip_timing(v) for k, v in obj.items() if k not in _TIMING_KEYS}
    if isinstance(obj, list):
        return [_strip_timing(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Scenario / report shapes (defined here per the sim seam)
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    """Fully self-describing deterministic scenario. All times unix seconds."""
    name: str
    seed: str
    t0_s: float
    terrain_kind: str                  # "canyon" | "flat"
    spec: GridSpec
    bbox: BBox
    ignition: LatLon
    weather_script: list               # list[WeatherSample], hourly, ascending
    clients: list                      # list[ClientProperty]
    crews: list                        # list[Crew] (home_base set)
    safe_zones: list                   # list[SafeZone]
    description: str = ""


@dataclass
class ServicedStop:
    """One completed gel application. Times unix seconds; margin hours."""
    crew_id: str
    client_id: str
    incident_id: str
    arrive_s: float
    service_start_s: float
    depart_s: float
    latest_depart_s: float
    margin_h: float
    gel_fresh_frac: float


@dataclass
class SafetyAudit:
    """Post-run check of one serviced stop against the FINAL p10 arrival."""
    crew_id: str
    client_id: str
    depart_s: float
    eta_p10_s: float
    latest_depart_s: float
    margin_h: float
    violation: bool


@dataclass
class SimReport:
    """run() output. All times unix seconds; margins hours."""
    scenario: str
    seed: str
    t0_s: float
    hours: float
    cycles_run: int
    cycle_period_s: float
    events: list                       # list[AlertEvent], chronological
    final_states: dict                 # "client|incident" -> AlertState value
    serviced: list                     # list[ServicedStop]
    unserved: list                     # list[dict{client_id, incident_id, reason}]
    violations: list                   # list[SafetyAudit] with violation=True; MUST be []
    min_margin_h: Optional[float]      # min audited margin (hours); None if no stops
    audit: list                        # list[SafetyAudit], every serviced stop
    plans_recent: list                 # last N adopted plan snapshots (jsonable)
    stack_provenance: dict             # component -> implementation used
    notes: list
    report_path: str = ""
    console_path: str = ""


@dataclass
class SimStack:
    """Injectable component bundle for run(). Every member conforms to the
    corresponding contracts protocol / function seam."""
    fire_feed: object
    weather: object
    terrain: object
    clients: object
    tracker: object
    alert_engine: object
    notifier: object
    ensemble_fn: Callable              # (tile, ignition, wx, t0_s, cfg) -> EnsembleArrival
    eta_fn: Callable                   # (ens, loc) -> ThreatEta
    dispatch_fn: Callable              # (crews, jobs, ens, safe_zones, cfg, now_s, incumbent) -> DispatchPlan
    travel_fn: Callable                # (a, b, cfg) -> float seconds
    latest_departure_fn: Callable      # (eta_p10_s, cfg) -> float unix s
    erode_fn: Callable                 # (Raster, radius_m) -> Raster
    provenance: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Weather scripts
# ---------------------------------------------------------------------------

def _script(t0_s: float, total_h: int, sample_fn) -> list:
    """Hourly WeatherSample list; sample_fn(hour_index) -> (wind_mps, from_deg, rh, temp)."""
    out = []
    for h in range(total_h + 1):
        wind, wdir, rh, temp = sample_fn(h)
        out.append(WeatherSample(t_s=t0_s + h * 3600.0, wind_speed_mps=wind,
                                 wind_gust_mps=wind * 1.4, wind_dir_deg=wdir,
                                 rh_pct=rh, temp_c=temp))
    return out


def _wind_event_script(t0_s: float, calm_h: int = 12, event_h: int = 8,
                       event_wind_mps: float = 15.0, wind_from_deg: float = 270.0,
                       total_h: int = 120) -> list:
    """Calm (2 m/s), then `event_h` hours of strong wind blowing FROM
    `wind_from_deg` (270 = from west = pushes fire east), then moderate."""
    def f(h):
        if h < calm_h:
            return (2.0, wind_from_deg, 30.0, 28.0)
        if h < calm_h + event_h:
            return (event_wind_mps, wind_from_deg, 12.0, 33.0)
        return (3.0, wind_from_deg, 25.0, 29.0)
    return _script(t0_s, total_h, f)


def _steady_script(t0_s: float, wind_mps: float, wind_from_deg: float,
                   rh_pct: float = 40.0, temp_c: float = 24.0,
                   total_h: int = 120) -> list:
    """Constant-wind script."""
    return _script(t0_s, total_h, lambda h: (wind_mps, wind_from_deg, rh_pct, temp_c))


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _build_canyon_wind_event(seed: str) -> Scenario:
    t0 = 1_770_000_000.0
    ignition = LatLon(34.2000, -117.6000)
    rng = seeded_rng("canyon_wind_event", seed, "layout")
    clients = []
    for i in range(12):
        dist = 1500.0 + i * 950.0                       # 1.5 .. 12 km downwind (east)
        brg = 90.0 + (rng.random() * 2.0 - 1.0) * 18.0  # jitter off the wind axis
        loc = _destination(ignition, brg, dist)
        clients.append(ClientProperty(
            client_id=f"C{i + 1:02d}", loc=loc, name=f"Canyon Estate {i + 1:02d}",
            value_usd=800_000.0 + rng.random() * 2_700_000.0,
            priority_weight=2.0 if rng.random() < 0.25 else 1.0))
    crews = [
        Crew(crew_id="G1", name="GraphGel-1", home_base=_destination(ignition, 135.0, 9_000.0)),
        Crew(crew_id="G2", name="GraphGel-2", home_base=_destination(ignition, 150.0, 11_000.0)),
        Crew(crew_id="G3", name="GraphGel-3", home_base=_destination(ignition, 120.0, 13_000.0)),
    ]
    zones = [
        SafeZone("Z-WEST", _destination(ignition, 270.0, 5_000.0), "Upwind staging (west)"),
        SafeZone("Z-SOUTH", _destination(ignition, 180.0, 7_000.0), "South flank lot"),
        SafeZone("Z-SE", _destination(ignition, 140.0, 12_000.0), "SE fairgrounds"),
    ]
    pts = [ignition] + [c.loc for c in clients] + [cr.home_base for cr in crews] + [z.loc for z in zones]
    spec, bbox = _grid_for_points(pts, pad_m=2_500.0, cell_m=250.0)
    return Scenario(
        name="canyon_wind_event", seed=seed, t0_s=t0, terrain_kind="canyon",
        spec=spec, bbox=bbox, ignition=ignition,
        weather_script=_wind_event_script(t0),
        clients=clients, crews=crews, safe_zones=zones,
        description="12 h calm then 8 h of 15 m/s wind from the west pushing the "
                    "fire east through a canyon community of 12 protected homes.")


def _build_slow_creeper(seed: str) -> Scenario:
    t0 = 1_770_100_000.0
    ignition = LatLon(38.4500, -122.7000)
    rng = seeded_rng("slow_creeper", seed, "layout")
    clients = []
    for i in range(6):
        dist = 2_000.0 + i * 2_600.0                      # 2 .. 15 km: near = EXTREME
        brg = 135.0 + (rng.random() * 2.0 - 1.0) * 25.0   # at t0, far = NOTICE first
        loc = _destination(ignition, brg, dist)
        clients.append(ClientProperty(
            client_id=f"S{i + 1:02d}", loc=loc, name=f"Creekside {i + 1:02d}",
            value_usd=600_000.0 + rng.random() * 1_400_000.0))
    crews = [
        Crew(crew_id="G1", name="GraphGel-1", home_base=_destination(ignition, 250.0, 5_000.0)),
        Crew(crew_id="G2", name="GraphGel-2", home_base=_destination(ignition, 200.0, 6_000.0)),
    ]
    zones = [
        SafeZone("Z-NORTH", _destination(ignition, 0.0, 5_000.0), "North staging"),
        SafeZone("Z-WEST", _destination(ignition, 270.0, 5_000.0), "West staging"),
    ]
    pts = [ignition] + [c.loc for c in clients] + [cr.home_base for cr in crews] + [z.loc for z in zones]
    spec, bbox = _grid_for_points(pts, pad_m=2_000.0, cell_m=200.0)
    return Scenario(
        name="slow_creeper", seed=seed, t0_s=t0, terrain_kind="flat",
        spec=spec, bbox=bbox, ignition=ignition,
        weather_script=_steady_script(t0, 1.5, 315.0, rh_pct=45.0, temp_c=22.0),
        clients=clients, crews=crews, safe_zones=zones,
        description="Low-wind creeping fire; 6 clients spread 2-15 km downwind "
                    "(NOTICE band through EXTREME), 2 crews, generous margins.")


def _build_tiny(seed: str) -> Scenario:
    t0 = 1_770_200_000.0
    ignition = LatLon(35.1000, -120.5000)
    rng = seeded_rng("tiny", seed, "layout")
    clients = []
    for i, dist in enumerate((700.0, 1_300.0, 2_100.0)):
        brg = 90.0 + (rng.random() * 2.0 - 1.0) * 10.0
        clients.append(ClientProperty(
            client_id=f"T{i + 1:02d}", loc=_destination(ignition, brg, dist),
            name=f"Tiny {i + 1:02d}"))
    crews = [Crew(crew_id="G1", name="GraphGel-1",
                  home_base=_destination(ignition, 170.0, 3_000.0))]
    zones = [
        SafeZone("Z-WEST", _destination(ignition, 270.0, 2_500.0), "West staging"),
        SafeZone("Z-SOUTH", _destination(ignition, 180.0, 2_800.0), "South staging"),
    ]
    pts = [ignition] + [c.loc for c in clients] + [cr.home_base for cr in crews] + [z.loc for z in zones]
    spec, bbox = _grid_for_points(pts, pad_m=1_200.0, cell_m=200.0)
    return Scenario(
        name="tiny", seed=seed, t0_s=t0, terrain_kind="flat",
        spec=spec, bbox=bbox, ignition=ignition,
        weather_script=_steady_script(t0, 2.0, 270.0, rh_pct=35.0, temp_c=25.0),
        clients=clients, crews=crews, safe_zones=zones,
        description="3 clients / 1 crew micro-scenario for fast tests.")


_BUILDERS = {
    "canyon_wind_event": _build_canyon_wind_event,
    "slow_creeper": _build_slow_creeper,
    "tiny": _build_tiny,
}


def scenario_names() -> list:
    """Names accepted by build_scenario (unitless)."""
    return sorted(_BUILDERS)


def build_scenario(name: str, seed: object = "default") -> Scenario:
    """Build the named deterministic Scenario. `seed` (any printable object)
    perturbs only cosmetic layout jitter; same (name, seed) -> identical
    scenario. Raises ValueError on unknown name."""
    if name not in _BUILDERS:
        raise ValueError(f"unknown scenario {name!r}; available: {', '.join(scenario_names())}")
    return _BUILDERS[name](str(seed))


# ---------------------------------------------------------------------------
# Internal scenario-driven mock providers (protocol-conforming)
# ---------------------------------------------------------------------------

class _ScenarioFireFeed:
    """FireFeed protocol: one active incident at the scenario ignition."""

    def __init__(self, scenario: Scenario):
        self._sc = scenario

    def fetch_active(self, bbox: Optional[BBox] = None) -> list:
        sc = self._sc
        inc = FireIncident(
            incident_id=f"sim-{sc.name}", name=f"SIM {sc.name}",
            source=IncidentSource.MOCK, origin=sc.ignition,
            updated_at_s=sc.t0_s, discovered_at_s=sc.t0_s, active=True)
        if bbox is not None and not bbox.contains(inc.origin):
            return []
        return [inc]


class _ScenarioWeather:
    """WeatherProvider protocol backed by the scenario's scripted samples."""

    def __init__(self, scenario: Scenario):
        self._sc = scenario

    def forecast(self, loc: LatLon, horizon_h: float) -> WeatherForecast:
        n = min(len(self._sc.weather_script), int(horizon_h) + 1)
        return WeatherForecast(loc=loc, samples=list(self._sc.weather_script[:max(2, n)]),
                               source="mock")


class _ScenarioTerrain:
    """TerrainProvider protocol: synthetic tile on the scenario's GridSpec.

    'flat' = uniform grass (FBFM40 code 102), zero slope. 'canyon' = gentle
    east-west valley: slope rises toward the north/south walls."""

    def __init__(self, scenario: Scenario):
        self._sc = scenario

    def tile(self, bbox: BBox, cell_m: float) -> TerrainTile:
        spec = self._sc.spec
        n = spec.nrows * spec.ncols
        fuel = Raster(spec, [102.0] * n)
        slope = Raster(spec, [0.0] * n)
        aspect = Raster(spec, [0.0] * n)
        elev = Raster(spec, [300.0] * n)
        if self._sc.terrain_kind == "canyon":
            mid = (spec.nrows - 1) / 2.0
            for r in range(spec.nrows):
                wall = abs(r - mid) / max(mid, 1.0)          # 0 at floor, 1 at walls
                for c in range(spec.ncols):
                    slope.set(r, c, 25.0 * wall)
                    elev.set(r, c, 300.0 + 400.0 * wall)
                    aspect.set(r, c, 0.0 if r > mid else 180.0)
        return TerrainTile(spec=spec, fuel=fuel, slope_deg=slope,
                           aspect_deg=aspect, elev_m=elev, source="synthetic")


class _ScenarioClients:
    """ClientRegistry protocol over the scenario's client list."""

    def __init__(self, scenario: Scenario):
        self._sc = scenario

    def protected_clients(self, bbox: Optional[BBox] = None) -> list:
        cs = self._sc.clients
        if bbox is None:
            return list(cs)
        return [c for c in cs if bbox.contains(c.loc)]


class _FallbackTracker:
    """Minimal CrewTracker: ingest(beacon) / crews() / crew(id) / stale(now_s)."""

    def __init__(self, cfg: ResponseConfig, seed_crews: Optional[list] = None):
        self._cfg = cfg
        self._crews = {}
        for c in seed_crews or []:
            self._crews[c.crew_id] = Crew(crew_id=c.crew_id, name=c.name,
                                          status=c.status, home_base=c.home_base,
                                          members=c.members)

    def ingest(self, beacon: CrewBeacon) -> None:
        crew = self._crews.setdefault(beacon.crew_id, Crew(crew_id=beacon.crew_id))
        crew.last_beacon = beacon
        crew.status = beacon.status

    def crews(self) -> list:
        return [self._crews[k] for k in sorted(self._crews)]

    def crew(self, crew_id: str) -> Optional[Crew]:
        return self._crews.get(crew_id)

    def stale(self, now_s: float) -> list:
        out = []
        for cid in sorted(self._crews):
            b = self._crews[cid].last_beacon
            if b is None or now_s - b.t_s > self._cfg.beacon_stale_s:
                out.append(cid)
        return out


class _ListNotifier:
    """Notifier protocol: records + debug-logs every event. Always 'delivers'."""

    def __init__(self):
        self.sent = []

    def send(self, event: AlertEvent, client: ClientProperty) -> bool:
        self.sent.append((event, client.client_id))
        _LOG.debug("ALERT %s %s -> %s", event.client_id, event.prev.value, event.state.value)
        return True


# ---------------------------------------------------------------------------
# Fallback spread engine (time-dependent 16-neighbor Dijkstra)
# ---------------------------------------------------------------------------

_NBRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1),
         (-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]


def _dir_ros_mps(w: WeatherSample, travel_bearing_deg: float,
                 wind_scale: float, moisture_scale: float) -> float:
    """Directional rate of spread (m/s) along `travel_bearing_deg`.

    Simplified grass-fire surrogate (head ROS ~ 0.03 + 0.05*U10, elliptical
    shape via Anderson-style L/B capped at 8). The real physics lives in
    wgresponse.rothermel/spread; this exists so sims run standalone."""
    wind = max(0.0, w.wind_speed_mps * wind_scale)
    head = (0.03 + 0.05 * wind) / max(0.25, moisture_scale)
    lb = min(8.0, 1.0 + wind / 3.0)
    ecc = math.sqrt(1.0 - 1.0 / (lb * lb)) if lb > 1.0 else 0.0
    push_deg = (w.wind_dir_deg + 180.0) % 360.0          # meteorological FROM -> toward
    theta = math.radians(travel_bearing_deg - push_deg)
    ros = head * (1.0 - ecc) / (1.0 - ecc * math.cos(theta))
    return max(ros, 0.005)


def _fallback_arrival(tile: TerrainTile, ignition: list, wx: WeatherForecast,
                      t0_s: float, cfg: ResponseConfig,
                      moisture_scale: float = 1.0, wind_scale: float = 1.0) -> Raster:
    """Arrival raster (unix s per cell; UNREACHED beyond horizon). Speeds use
    weather at edge departure time. Note: time-varying speeds make the graph
    non-FIFO in theory; with the smooth scripted ramps used here the label-
    setting error is negligible and always on the late (conservative-alert,
    handled-by-p10-safety) side."""
    spec = tile.spec
    ncols = spec.ncols
    horizon_end = t0_s + cfg.horizon_h * 3600.0
    n = spec.nrows * ncols
    arr = [UNREACHED] * n
    # Precompute neighbor geometry once per spec.
    nbrs = []
    for dr, dc in _NBRS:
        dist = math.hypot(dr, dc) * spec.cell_m
        bearing = math.degrees(math.atan2(dc, -dr)) % 360.0  # row+ = south
        nbrs.append((dr, dc, dist, bearing))
    heap = []
    for p in ignition:
        rc = _cell_of(spec, p)
        if rc is not None:
            i = rc[0] * ncols + rc[1]
            if t0_s < arr[i]:
                arr[i] = t0_s
                heapq.heappush(heap, (t0_s, i))
    while heap:
        t, i = heapq.heappop(heap)
        if t > arr[i]:
            continue
        w = wx.at(t)
        r, c = divmod(i, ncols)
        for dr, dc, dist, bearing in nbrs:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < spec.nrows and 0 <= cc < ncols):
                continue
            ta = t + dist / _dir_ros_mps(w, bearing, wind_scale, moisture_scale)
            if ta > horizon_end:
                continue
            j = rr * ncols + cc
            if ta < arr[j]:
                arr[j] = ta
                heapq.heappush(heap, (ta, j))
    return Raster(spec, arr)


def _quantile(sorted_vals: list, q: float) -> float:
    """Linear-interpolated quantile of an ascending list that may end in inf."""
    n = len(sorted_vals)
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    vlo, vhi = sorted_vals[lo], sorted_vals[hi]
    if math.isinf(vlo):
        return UNREACHED
    if math.isinf(vhi):
        return vlo if frac == 0.0 else UNREACHED
    return vlo + (vhi - vlo) * frac


def _fallback_ensemble(tile: TerrainTile, ignition: list, wx: WeatherForecast,
                       t0_s: float, cfg: ResponseConfig) -> EnsembleArrival:
    """cfg.ensemble_n perturbed members + 1 nominal; per-cell p10/p50/p90
    quantile rasters (p10 = earliest/pessimistic arrival, unix s)."""
    members = [_fallback_arrival(tile, ignition, wx, t0_s, cfg, 1.0, 1.0)]
    for i in range(cfg.ensemble_n):
        rng = seeded_rng("sim-ensemble", t0_s, i)
        wscale = 1.0 + cfg.wind_perturb_frac * (2.0 * rng.random() - 1.0)
        mscale = 1.0 + cfg.moisture_perturb_frac * (2.0 * rng.random() - 1.0)
        members.append(_fallback_arrival(tile, ignition, wx, t0_s, cfg, mscale, wscale))
    spec = tile.spec
    n = spec.nrows * spec.ncols
    p10 = [UNREACHED] * n
    p50 = [UNREACHED] * n
    p90 = [UNREACHED] * n
    for i in range(n):
        vals = sorted(m.data[i] for m in members)
        p10[i] = _quantile(vals, 0.10)
        p50[i] = _quantile(vals, 0.50)
        p90[i] = _quantile(vals, 0.90)
    return EnsembleArrival(spec=spec, members=members,
                           p10=Raster(spec, p10), p50=Raster(spec, p50),
                           p90=Raster(spec, p90), t0_s=t0_s)


def _eta_at(ens: EnsembleArrival, loc: LatLon) -> ThreatEta:
    """ThreatEta (absolute unix s; UNREACHED=inf) at loc, nearest-cell sample."""
    return ThreatEta(eta_p10_s=_sample_raster(ens.p10, loc),
                     eta_p50_s=_sample_raster(ens.p50, loc),
                     eta_p90_s=_sample_raster(ens.p90, loc))


def _erode_min(r: Raster, radius_m: float) -> Raster:
    """Morphological min filter (square window, radius in cells) — corridor
    conservatism per contracts rule 3. geo.erode_min is the canonical disc."""
    spec = r.spec
    k = int(radius_m // spec.cell_m)
    if k <= 0:
        return Raster(spec, list(r.data))
    nrows, ncols = spec.nrows, spec.ncols
    tmp = [UNREACHED] * (nrows * ncols)
    for row in range(nrows):                   # horizontal pass
        base = row * ncols
        for col in range(ncols):
            lo, hi = max(0, col - k), min(ncols, col + k + 1)
            tmp[base + col] = min(r.data[base + lo:base + hi])
    out = [UNREACHED] * (nrows * ncols)
    for col in range(ncols):                   # vertical pass
        for row in range(nrows):
            lo, hi = max(0, row - k), min(nrows, row + k + 1)
            out[row * ncols + col] = min(tmp[rr * ncols + col] for rr in range(lo, hi))
    return Raster(spec, out)


# ---------------------------------------------------------------------------
# Fallback safety / travel / dispatch
# ---------------------------------------------------------------------------

def _latest_departure_s(eta_p10_s: float, cfg: ResponseConfig) -> float:
    """Latest safe crew departure (unix s): p10 arrival minus (egress + ember)
    buffer. Mirrors safety.latest_departure_s exactly (contracts SAFETY
    SEMANTICS 2); ember_buffer_min is default-OFF but binds in wind events, so
    omitting it here would strip the ember margin in safety-module-absent mode."""
    return eta_p10_s - (cfg.egress_buffer_min + cfg.ember_buffer_min) * 60.0


def _travel_time_s(a: LatLon, b: LatLon, cfg: ResponseConfig) -> float:
    """Drive time seconds: haversine * circuity * fire_traffic / road speed."""
    return _haversine_m(a, b) * cfg.circuity * cfg.fire_traffic_factor / cfg.road_speed_mps


def _corridor_safe(a: LatLon, b: LatLon, depart_s: float, speed_mps: float,
                   eroded_p10, cfg: ResponseConfig) -> CorridorVerdict:
    """Sample the leg every corridor_sample_m; eroded p10 arrival must exceed
    the crew pass time by route_margin_min at every sample. eroded_p10=None
    (no grid data) verdicts safe with reason 'no_grid_data'."""
    if eroded_p10 is None:
        return CorridorVerdict(True, "no_grid_data", UNREACHED)
    pts = _sample_polyline(a, b, cfg.corridor_sample_m)
    worst = UNREACHED
    dist = 0.0
    prev = pts[0]
    speed = max(speed_mps, 0.1)
    for p in pts:
        dist += _haversine_m(prev, p)
        prev = p
        t_pass = depart_s + dist / speed
        arrival = _sample_raster(eroded_p10, p)
        margin_h = UNREACHED if math.isinf(arrival) else (arrival - t_pass) / 3600.0
        if margin_h < worst:
            worst = margin_h
        if not math.isinf(arrival) and arrival - t_pass < cfg.route_margin_min * 60.0:
            return CorridorVerdict(False, "corridor_unsafe", worst)
    return CorridorVerdict(True, "", worst)


def _gel_fresh_frac(depart_s: float, eta_p50_s: float, cfg: ResponseConfig) -> float:
    """Projected gel effectiveness fraction remaining at (p50) fire arrival.
    Gel window/full-strength come from cfg — UNVERIFIED PRODUCT DATA."""
    if math.isinf(eta_p50_s):
        return 1.0
    age_h = max(0.0, (eta_p50_s - depart_s) / 3600.0)
    if age_h <= cfg.gel_full_strength_h:
        return 1.0
    if age_h >= cfg.gel_window_h:
        return 0.0
    return 1.0 - (age_h - cfg.gel_full_strength_h) / (cfg.gel_window_h - cfg.gel_full_strength_h)


def _fallback_plan(crews: list, jobs: list, ens: dict, safe_zones: list,
                   cfg: ResponseConfig, now_s: float,
                   incumbent: Optional[DispatchPlan] = None) -> DispatchPlan:
    """Greedy earliest-deadline-first dispatch honoring every contracts safety
    rule (deadline window, ingress corridor, egress corridor to a SafeZone).
    Deterministic. The real solver lives in wgresponse.dispatch."""
    eroded = {}
    for iid, e in ens.items():
        eroded[iid] = _erode_min(e.p10, cfg.corridor_erosion_m)
    speed = _eff_ground_speed_mps(cfg)
    state = {}
    for cr in crews:
        loc = cr.loc()
        if loc is not None:
            state[cr.crew_id] = {"avail_s": now_s, "loc": loc, "stops": [], "zone": ""}
    committed = set()
    if incumbent is not None:
        horizon = now_s + cfg.commitment_horizon_min * 60.0
        for cid, cp in incumbent.plans.items():
            for st in cp.stops:
                if st.service_start_s <= horizon:
                    committed.add((cid, st.client_id))
    plans = {}
    unserved = []
    objective = 0.0
    ordered = sorted(jobs, key=lambda j: (j.latest_service_start_s, j.client.client_id))
    for job in ordered:
        er = eroded.get(job.incident_id)
        best = None
        reasons = set()
        if not state:
            reasons.add("capacity")
        for cid in sorted(state):
            st = state[cid]
            t_travel = _travel_time_s(st["loc"], job.client.loc, cfg)
            arrive = st["avail_s"] + t_travel
            service_start = max(arrive, job.earliest_useful_start_s, now_s)
            if service_start > job.latest_service_start_s:
                reasons.add("infeasible_deadline")
                continue
            depart = service_start + cfg.deploy_min * 60.0
            ingress = _corridor_safe(st["loc"], job.client.loc, st["avail_s"], speed, er, cfg)
            if not ingress.safe:
                reasons.add("no_safe_corridor")
                continue
            zone_pick = None
            for z in sorted(safe_zones, key=lambda z: _haversine_m(job.client.loc, z.loc)):
                v = _corridor_safe(job.client.loc, z.loc, depart, speed, er, cfg)
                if v.safe:
                    zone_pick = z
                    break
            if zone_pick is None:
                reasons.add("no_safe_corridor")
                continue
            cand = (depart, cid, arrive, service_start, zone_pick)
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is None:
            if "infeasible_deadline" in reasons:
                why = "infeasible_deadline"
            elif "no_safe_corridor" in reasons:
                why = "no_safe_corridor"
            else:
                why = "capacity"
            unserved.append(UnservedJob(client_id=job.client.client_id,
                                        incident_id=job.incident_id, reason=why))
            continue
        depart, cid, arrive, service_start, zone = best
        fresh = _gel_fresh_frac(depart, job.eta.eta_p50_s, cfg)
        stop = PlannedStop(
            client_id=job.client.client_id, incident_id=job.incident_id,
            arrive_s=arrive, service_start_s=service_start, depart_s=depart,
            latest_depart_s=job.latest_depart_s,
            margin_h=(job.latest_depart_s - depart) / 3600.0,
            gel_fresh_frac=fresh)
        st = state[cid]
        st["avail_s"] = depart
        st["loc"] = job.client.loc
        st["stops"].append(stop)
        st["zone"] = zone.zone_id
        objective += job.client.value_usd * job.client.priority_weight * fresh * cfg.gel_effectiveness
    for cid in sorted(state):
        st = state[cid]
        if st["stops"]:
            plans[cid] = CrewPlan(crew_id=cid, stops=st["stops"],
                                  egress_zone_id=st["zone"],
                                  committed_until_s=now_s + cfg.commitment_horizon_min * 60.0)
    churn = 0.0
    if incumbent is not None and committed:
        kept = sum(1 for cid, cp in plans.items()
                   for s in cp.stops if (cid, s.client_id) in committed)
        churn = 1.0 - kept / len(committed)
    return DispatchPlan(computed_at_s=now_s, plans=plans, unserved=unserved,
                        objective=objective,
                        objective_breakdown={"expected_protected_value_usd": objective,
                                             "stops": sum(len(p.stops) for p in plans.values()),
                                             "unserved": len(unserved)},
                        churn_from_incumbent=churn)


# ---------------------------------------------------------------------------
# Fallback alert engine (contracts ladder + hysteresis)
# ---------------------------------------------------------------------------

_RANK = {AlertState.NONE: 0, AlertState.CLEARED: 0, AlertState.NOTICE_72H: 1,
         AlertState.WATCH: 2, AlertState.EXTREME_24H: 3, AlertState.IMPACTED: 4}


class _FallbackAlertEngine:
    """AlertEngine seam: evaluate(pairs, now_s) -> events; states() -> records.

    NOTICE_72H on eta_p50 <= notice_h; EXTREME_24H on eta_p50 <= extreme_h OR
    eta_p10 <= extreme_p10_h; IMPACTED once p50 arrival has passed (reached
    stepwise through EXTREME so every transition is ladder-legal). Downgrades
    need clear_cycles consecutive clear evals with clear_margin_h headroom."""

    def __init__(self, cfg: ResponseConfig):
        self._cfg = cfg
        self._records = {}

    def _desired(self, eta: ThreatEta, now_s: float) -> AlertState:
        cfg = self._cfg
        if not math.isinf(eta.eta_p50_s) and eta.eta_p50_s <= now_s:
            return AlertState.IMPACTED
        p10_h, p50_h, _ = eta.hours_out(now_s)
        if p50_h <= cfg.extreme_h or p10_h <= cfg.extreme_p10_h:
            return AlertState.EXTREME_24H
        if p50_h <= cfg.notice_h:
            return AlertState.NOTICE_72H
        return AlertState.NONE

    def _clear_headroom(self, state: AlertState, eta: ThreatEta, now_s: float) -> bool:
        cfg = self._cfg
        p10_h, p50_h, _ = eta.hours_out(now_s)
        if state == AlertState.EXTREME_24H:
            return (p50_h > cfg.extreme_h + cfg.clear_margin_h
                    and p10_h > cfg.extreme_p10_h + cfg.clear_margin_h)
        if state in (AlertState.NOTICE_72H, AlertState.WATCH):
            return p50_h > cfg.notice_h + cfg.clear_margin_h
        return False

    def evaluate(self, pairs: list, now_s: float) -> list:
        from wgresponse.contracts import ALERT_TRANSITIONS
        events = []
        for ta in pairs:
            key = (ta.client_id, ta.incident_id)
            rec = self._records.get(key)
            if rec is None:
                rec = AlertRecord(client_id=ta.client_id, incident_id=ta.incident_id)
                self._records[key] = rec
            rec.last_eval_s = now_s
            cur = rec.state
            if cur == AlertState.IMPACTED:
                continue
            desired = self._desired(ta.eta, now_s)
            new = cur
            if _RANK[desired] > _RANK[cur]:
                legal = [s for s in ALERT_TRANSITIONS[cur]
                         if _RANK[cur] < _RANK[s] <= _RANK[desired]]
                if legal:
                    new = max(legal, key=lambda s: _RANK[s])
                rec.clear_streak = 0
            elif _RANK[desired] < _RANK[cur] and self._clear_headroom(cur, ta.eta, now_s):
                rec.clear_streak += 1
                if rec.clear_streak >= self._cfg.clear_cycles:
                    new = AlertState.CLEARED
                    rec.clear_streak = 0
            else:
                rec.clear_streak = 0
            if new != cur:
                assert_valid_transition(cur, new)
                p10_h, p50_h, _ = ta.eta.hours_out(now_s)
                ev = AlertEvent(
                    t_s=now_s, client_id=ta.client_id, incident_id=ta.incident_id,
                    prev=cur, state=new, eta_p50_h=p50_h, eta_p10_h=p10_h,
                    message=(f"{ta.client_id}: {cur.value} -> {new.value}"
                             f" (ETA p50 {'-' if math.isinf(p50_h) else format(p50_h, '.1f')}h,"
                             f" p10 {'-' if math.isinf(p10_h) else format(p10_h, '.1f')}h)"))
                rec.state = new
                rec.entered_at_s = now_s
                rec.history.append((now_s, new))
                events.append(ev)
        return events

    def states(self) -> dict:
        return dict(self._records)


# ---------------------------------------------------------------------------
# Stack assembly
# ---------------------------------------------------------------------------

def _try_import(mod: str):
    try:
        return importlib.import_module(f"wgresponse.{mod}")
    except Exception:
        return None


def _try_construct(mod, cls_name: str, cfg: ResponseConfig):
    """Instantiate a sibling class trying (cfg) then () ctor shapes."""
    cls = getattr(mod, cls_name, None) if mod is not None else None
    if cls is None:
        return None
    for args in ((cfg,), ()):
        try:
            return cls(*args)
        except Exception:
            continue
    return None


def default_stack(scenario: Scenario, cfg: ResponseConfig,
                  use_siblings: bool = True) -> SimStack:
    """Assemble the component stack for run(). With use_siblings=True the
    contract-pinned sibling seams are preferred when importable; every slot
    degrades to the internal deterministic fallback. provenance records the
    implementation chosen per slot."""
    prov = {"fire_feed": "sim-internal", "weather": "sim-internal",
            "terrain": "sim-internal", "clients": "sim-internal"}
    spread = _try_import("spread") if use_siblings else None
    dispatch = _try_import("dispatch") if use_siblings else None
    safety = _try_import("safety") if use_siblings else None
    travel = _try_import("travel") if use_siblings else None
    geo = _try_import("geo") if use_siblings else None
    alerts = _try_import("alerts") if use_siblings else None
    crews_mod = _try_import("crews") if use_siblings else None
    notify = _try_import("notify") if use_siblings else None

    ensemble_fn = getattr(spread, "ensemble_arrival", None) or _fallback_ensemble
    eta_fn = getattr(spread, "eta_at", None) or _eta_at
    dispatch_fn = getattr(dispatch, "plan", None) or _fallback_plan
    latest_fn = getattr(safety, "latest_departure_s", None) or _latest_departure_s
    travel_fn = getattr(travel, "travel_time_s", None) or _travel_time_s
    erode_fn = getattr(geo, "erode_min", None) or _erode_min
    prov["spread"] = "wgresponse.spread" if ensemble_fn is not _fallback_ensemble else "sim-internal"
    prov["dispatch"] = "wgresponse.dispatch" if dispatch_fn is not _fallback_plan else "sim-internal"
    prov["safety"] = "wgresponse.safety" if latest_fn is not _latest_departure_s else "sim-internal"
    prov["travel"] = "wgresponse.travel" if travel_fn is not _travel_time_s else "sim-internal"
    prov["geo"] = "wgresponse.geo" if erode_fn is not _erode_min else "sim-internal"

    engine = _try_construct(alerts, "AlertEngine", cfg)
    prov["alerts"] = "wgresponse.alerts" if engine is not None else "sim-internal"
    if engine is None:
        engine = _FallbackAlertEngine(cfg)
    tracker = _try_construct(crews_mod, "CrewTracker", cfg)
    prov["crews"] = "wgresponse.crews" if tracker is not None else "sim-internal"
    if tracker is None:
        tracker = _FallbackTracker(cfg, scenario.crews)
    notifier = _try_construct(notify, "LogNotifier", cfg)
    prov["notify"] = "wgresponse.notify" if notifier is not None else "sim-internal"
    if notifier is None:
        notifier = _ListNotifier()

    return SimStack(
        fire_feed=_ScenarioFireFeed(scenario), weather=_ScenarioWeather(scenario),
        terrain=_ScenarioTerrain(scenario), clients=_ScenarioClients(scenario),
        tracker=tracker, alert_engine=engine, notifier=notifier,
        ensemble_fn=ensemble_fn, eta_fn=eta_fn, dispatch_fn=dispatch_fn,
        travel_fn=travel_fn, latest_departure_fn=latest_fn, erode_fn=erode_fn,
        provenance=prov)


# ---------------------------------------------------------------------------
# Crew kinematics
# ---------------------------------------------------------------------------

class _CrewSim:
    """Schedule-following crew: straight-line legs at the effective ground
    speed, deploy dwell of cfg.deploy_min at each site, egress to the plan's
    SafeZone after the last stop. Event-driven inside each cycle step."""

    def __init__(self, crew: Crew, speed_mps: float):
        self.crew = crew
        self.speed = speed_mps
        self.pos = crew.home_base
        self.status = CrewStatus.AVAILABLE
        self.itinerary = []            # list of dict stops (see _adopt)
        self.egress_loc = None
        self.egress_zone_id = ""
        self._mode = "idle"            # idle | travel | deploy | egress
        self._deploy_end_s = 0.0
        self._arrive_s = 0.0

    def idle(self) -> bool:
        return self._mode == "idle" and not self.itinerary and self.egress_loc is None

    def adopt(self, stops: list, egress_loc, egress_zone_id: str) -> None:
        self.itinerary = list(stops)
        self.egress_loc = egress_loc
        self.egress_zone_id = egress_zone_id

    def advance(self, t_start_s: float, t_end_s: float, serviced_out: list) -> None:
        """Advance the crew from t_start_s to t_end_s (unix s), appending any
        completed ServicedStop records to serviced_out."""
        t = t_start_s
        guard = 0
        while t < t_end_s and guard < 1000:
            guard += 1
            if self._mode == "idle":
                if self.itinerary:
                    self._mode = "travel"
                    self.status = CrewStatus.EN_ROUTE
                elif self.egress_loc is not None:
                    self._mode = "egress"
                    self.status = CrewStatus.EGRESSING
                else:
                    return
            elif self._mode == "travel":
                target = self.itinerary[0]["loc"]
                remaining = _haversine_m(self.pos, target)
                budget = (t_end_s - t) * self.speed
                if budget >= remaining:
                    t += remaining / self.speed
                    self.pos = target
                    self._arrive_s = t
                    stop = self.itinerary[0]
                    service_start = max(t, stop["service_start_s"])
                    self._deploy_end_s = service_start + stop["deploy_s"]
                    stop["actual_arrive_s"] = t
                    stop["actual_service_start_s"] = service_start
                    self._mode = "deploy"
                    self.status = CrewStatus.DEPLOYING
                else:
                    self.pos = _interp(self.pos, target, budget / max(remaining, 1e-9))
                    t = t_end_s
            elif self._mode == "deploy":
                if self._deploy_end_s <= t_end_s:
                    t = max(t, self._deploy_end_s)
                    stop = self.itinerary.pop(0)
                    serviced_out.append(ServicedStop(
                        crew_id=self.crew.crew_id, client_id=stop["client_id"],
                        incident_id=stop["incident_id"],
                        arrive_s=stop["actual_arrive_s"],
                        service_start_s=stop["actual_service_start_s"],
                        depart_s=t, latest_depart_s=stop["latest_depart_s"],
                        margin_h=(stop["latest_depart_s"] - t) / 3600.0,
                        gel_fresh_frac=stop["gel_fresh_frac"]))
                    self._mode = "idle"
                else:
                    t = t_end_s
            elif self._mode == "egress":
                target = self.egress_loc
                remaining = _haversine_m(self.pos, target)
                budget = (t_end_s - t) * self.speed
                if budget >= remaining:
                    t += remaining / self.speed if self.speed > 0 else 0.0
                    self.pos = target
                    self.egress_loc = None
                    self._mode = "idle"
                    self.status = CrewStatus.AVAILABLE
                else:
                    self.pos = _interp(self.pos, target, budget / max(remaining, 1e-9))
                    t = t_end_s


# ---------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------

def run(scenario: Scenario, hours: float, cfg: Optional[ResponseConfig] = None,
        *, stack: Optional[SimStack] = None, use_siblings: bool = True,
        out_dir="out", keep_plans: int = 10) -> SimReport:
    """Run the closed-loop response simulation for `hours` simulated hours
    (clock steps of cfg.replan_period_s seconds) and write
    out/sim_<name>.json + out/sim_<name>_console.json.

    Deterministic: same (scenario, hours, cfg, stack) -> byte-identical JSON.
    The SAFETY AUDIT compares every serviced stop's actual departure (unix s)
    against the final pessimistic p10 arrival at that cell minus the egress
    buffer; `violations` MUST be empty for shipped scenarios."""
    cfg = cfg or ResponseConfig()
    stack = stack or default_stack(scenario, cfg, use_siblings=use_siblings)
    t0 = scenario.t0_s
    step = cfg.replan_period_s
    n_cycles = max(1, int(round(hours * 3600.0 / step)))
    speed = _eff_ground_speed_mps(cfg)

    incidents = stack.fire_feed.fetch_active(scenario.bbox)
    tile = stack.terrain.tile(scenario.bbox, scenario.spec.cell_m)
    wx = stack.weather.forecast(scenario.ignition, cfg.horizon_h)
    ensembles = {}
    for inc in incidents:
        ensembles[inc.incident_id] = stack.ensemble_fn(tile, inc.ignition_points(), wx, t0, cfg)

    clients = stack.clients.protected_clients(scenario.bbox)
    client_by_id = {c.client_id: c for c in clients}
    zone_by_id = {z.zone_id: z for z in scenario.safe_zones}

    crew_sims = {}
    tracks = {}
    for cr in scenario.crews:
        if cr.home_base is None:
            continue
        crew_sims[cr.crew_id] = _CrewSim(cr, speed)
        tracks[cr.crew_id] = []

    serviced = []
    assigned = set()
    jobbed = {}                       # client_id -> incident_id (ever under threat)
    unserved_reason = {}              # client_id -> latest dispatch reason
    events_all = []
    plans_recent = deque(maxlen=keep_plans)
    routes = []                       # console: adopted itineraries
    notes = [scenario.description]

    for k in range(n_cycles):
        now = t0 + k * step
        # -- synthetic beacons -> tracker
        for cid in sorted(crew_sims):
            cs = crew_sims[cid]
            stack.tracker.ingest(CrewBeacon(crew_id=cid, loc=cs.pos, t_s=now,
                                            status=cs.status, speed_mps=speed))
            tracks[cid].append({"t_s": now, "lat": round(cs.pos.lat, 6),
                                "lon": round(cs.pos.lon, 6), "status": cs.status.value})
        # -- threat assessments + alerts
        pairs = []
        etas = {}
        for inc in incidents:
            ens = ensembles[inc.incident_id]
            for cl in clients:
                eta = stack.eta_fn(ens, cl.loc)
                etas[(cl.client_id, inc.incident_id)] = eta
                pairs.append(ThreatAssessment(client_id=cl.client_id,
                                              incident_id=inc.incident_id,
                                              eta=eta, computed_at_s=now))
        for ev in stack.alert_engine.evaluate(pairs, now):
            events_all.append(ev)
            cl = client_by_id.get(ev.client_id)
            if cl is not None:
                stack.notifier.send(ev, cl)
        # -- build jobs for threatened, unhandled clients
        jobs = []
        for inc in incidents:
            for cl in clients:
                if not cl.graphgel_enrolled:
                    continue
                if cl.client_id in assigned or cl.client_id in {s.client_id for s in serviced}:
                    continue
                eta = etas[(cl.client_id, inc.incident_id)]
                if math.isinf(eta.eta_p50_s) or math.isinf(eta.eta_p10_s):
                    continue
                p50_h = (eta.eta_p50_s - now) / 3600.0
                if p50_h > cfg.notice_h:
                    continue
                jobbed.setdefault(cl.client_id, inc.incident_id)
                if eta.eta_p50_s <= now:
                    unserved_reason.setdefault(cl.client_id, "fire_arrived")
                    continue
                latest_depart = stack.latest_departure_fn(eta.eta_p10_s, cfg)
                jobs.append(DispatchJob(
                    client=cl, incident_id=inc.incident_id, eta=eta,
                    latest_depart_s=latest_depart,
                    latest_service_start_s=latest_depart - cfg.deploy_min * 60.0,
                    earliest_useful_start_s=max(now, eta.eta_p50_s - cfg.gel_window_h * 3600.0)))
        idle = [crew_sims[cid] for cid in sorted(crew_sims) if crew_sims[cid].idle()]
        if jobs and idle:
            crew_args = []
            for cs in idle:
                crew_args.append(Crew(
                    crew_id=cs.crew.crew_id, name=cs.crew.name,
                    status=CrewStatus.AVAILABLE,
                    last_beacon=CrewBeacon(crew_id=cs.crew.crew_id, loc=cs.pos,
                                           t_s=now, status=CrewStatus.AVAILABLE),
                    home_base=cs.crew.home_base))
            plan = stack.dispatch_fn(crew_args, jobs, ensembles,
                                     scenario.safe_zones, cfg, now, None)
            job_by_client = {j.client.client_id: j for j in jobs}
            for cid in sorted(plan.plans):
                cp = plan.plans[cid]
                if not cp.stops or cid not in crew_sims:
                    continue
                stops = []
                planned = []
                pts = [{"lat": round(crew_sims[cid].pos.lat, 6),
                        "lon": round(crew_sims[cid].pos.lon, 6)}]
                for st in cp.stops:
                    job = job_by_client.get(st.client_id)
                    cl = client_by_id.get(st.client_id)
                    if job is None or cl is None:
                        continue
                    stops.append({
                        "client_id": st.client_id, "incident_id": st.incident_id,
                        "loc": cl.loc, "service_start_s": st.service_start_s,
                        "deploy_s": cfg.deploy_min * 60.0,
                        "latest_depart_s": st.latest_depart_s,
                        "gel_fresh_frac": st.gel_fresh_frac,
                        "eta_p50_s": job.eta.eta_p50_s})
                    planned.append(st)
                    assigned.add(st.client_id)
                    pts.append({"lat": round(cl.loc.lat, 6), "lon": round(cl.loc.lon, 6)})
                if not stops:
                    continue
                zone = zone_by_id.get(cp.egress_zone_id)
                if zone is None and scenario.safe_zones:
                    zone = min(scenario.safe_zones,
                               key=lambda z: _haversine_m(stops[-1]["loc"], z.loc))
                if zone is not None:
                    pts.append({"lat": round(zone.loc.lat, 6), "lon": round(zone.loc.lon, 6)})
                crew_sims[cid].adopt(stops, zone.loc if zone else None,
                                     zone.zone_id if zone else "")
                routes.append({
                    "crew_id": cid, "adopted_cycle": k, "adopted_at_s": now,
                    "points": pts, "egress_zone_id": zone.zone_id if zone else "",
                    "stops": [{"client_id": s["client_id"],
                               "arrive_s": st.arrive_s, "depart_s": st.depart_s,
                               "margin_h": round(st.margin_h, 3)}
                              for s, st in zip(stops, planned)]})
            for uj in plan.unserved:
                unserved_reason[uj.client_id] = uj.reason
            plans_recent.append({"cycle": k, "now_s": now,
                                 "plan": _strip_timing(jsonable(plan))})
        # -- advance crews to the end of this cycle
        for cid in sorted(crew_sims):
            crew_sims[cid].advance(now, now + step, serviced)

    end_s = t0 + n_cycles * step
    for cid in sorted(crew_sims):
        cs = crew_sims[cid]
        tracks[cid].append({"t_s": end_s, "lat": round(cs.pos.lat, 6),
                            "lon": round(cs.pos.lon, 6), "status": cs.status.value})

    # ---- safety audit against the FINAL p10 grids
    audit = []
    violations = []
    min_margin = None
    for st in serviced:
        cl = client_by_id.get(st.client_id)
        ens = ensembles.get(st.incident_id)
        if cl is None or ens is None:
            continue
        eta = stack.eta_fn(ens, cl.loc)
        latest = stack.latest_departure_fn(eta.eta_p10_s, cfg)
        margin_h = UNREACHED if math.isinf(latest) else (latest - st.depart_s) / 3600.0
        entry = SafetyAudit(crew_id=st.crew_id, client_id=st.client_id,
                            depart_s=st.depart_s, eta_p10_s=eta.eta_p10_s,
                            latest_depart_s=latest, margin_h=margin_h,
                            violation=(not math.isinf(margin_h)) and margin_h < 0.0)
        audit.append(entry)
        if entry.violation:
            violations.append(entry)
        if not math.isinf(margin_h) and (min_margin is None or margin_h < min_margin):
            min_margin = margin_h

    # ---- unserved rollup
    serviced_ids = {s.client_id for s in serviced}
    unserved = []
    for cid in sorted(jobbed):
        if cid in serviced_ids:
            continue
        if cid in assigned:
            reason = "in_progress_at_sim_end"
        else:
            reason = unserved_reason.get(cid, "capacity")
        unserved.append({"client_id": cid, "incident_id": jobbed[cid], "reason": reason})

    final_states = {}
    if hasattr(stack.alert_engine, "states"):
        for key, rec in stack.alert_engine.states().items():
            kk = "|".join(key) if isinstance(key, tuple) else str(key)
            final_states[kk] = rec.state.value if isinstance(rec.state, AlertState) else str(rec.state)

    report = SimReport(
        scenario=scenario.name, seed=scenario.seed, t0_s=t0, hours=hours,
        cycles_run=n_cycles, cycle_period_s=step,
        events=events_all, final_states=final_states, serviced=serviced,
        unserved=unserved, violations=violations, min_margin_h=min_margin,
        audit=audit, plans_recent=list(plans_recent),
        stack_provenance=dict(stack.provenance), notes=notes)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / f"sim_{scenario.name}.json"
    console_path = out / f"sim_{scenario.name}_console.json"
    report_path.write_text(json.dumps(_report_dict(report), indent=2, sort_keys=True),
                           encoding="utf-8")
    console_path.write_text(
        json.dumps(_console_dict(scenario, cfg, report, ensembles, clients,
                                 tracks, routes, stack, end_s),
                   indent=2, sort_keys=True),
        encoding="utf-8")
    report.report_path = str(report_path)
    report.console_path = str(console_path)
    return report


def _report_dict(report: SimReport) -> dict:
    d = jsonable(report)
    d.pop("report_path", None)
    d.pop("console_path", None)
    d["schema_version"] = SCHEMA_VERSION
    return d


def _trace_contour(p50: Raster, thr_s: float, ignition: LatLon) -> list:
    """Coarse p50 isochrone ring: boundary-cell centers (arrival <= thr with a
    4-neighbor beyond thr / off-grid), sorted by angle around the ignition."""
    spec = p50.spec
    pts = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            v = p50.get(r, c)
            if v > thr_s:
                continue
            boundary = False
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < spec.nrows and 0 <= cc < spec.ncols):
                    boundary = True
                    break
                if p50.get(rr, cc) > thr_s:
                    boundary = True
                    break
            if boundary:
                pts.append(_cell_center(spec, r, c))
    if len(pts) < 3:
        return []
    cosl = math.cos(math.radians(ignition.lat))
    pts.sort(key=lambda p: math.atan2(p.lat - ignition.lat,
                                      (p.lon - ignition.lon) * cosl))
    ring = [{"lat": round(p.lat, 5), "lon": round(p.lon, 5)} for p in pts]
    ring.append(ring[0])
    return ring


def _console_dict(scenario: Scenario, cfg: ResponseConfig, report: SimReport,
                  ensembles: dict, clients: list, tracks: dict, routes: list,
                  stack: SimStack, end_s: float) -> dict:
    """Compact JSON with everything console/index.html needs."""
    ens = next(iter(ensembles.values()), None)
    isochrones = []
    perimeter = []
    client_rows = []
    if ens is not None:
        for h in (24.0, 48.0, 72.0):
            ring = _trace_contour(ens.p50, scenario.t0_s + h * 3600.0, scenario.ignition)
            if ring:
                isochrones.append({"h": h, "points": ring})
        perimeter = _trace_contour(ens.p50, end_s, scenario.ignition)
    state_by_client = {}
    for key, val in report.final_states.items():
        state_by_client[key.split("|", 1)[0]] = val
    for cl in clients:
        eta = stack.eta_fn(ens, cl.loc) if ens is not None else ThreatEta(UNREACHED, UNREACHED, UNREACHED)
        client_rows.append({
            "client_id": cl.client_id, "name": cl.name,
            "lat": round(cl.loc.lat, 6), "lon": round(cl.loc.lon, 6),
            "state": state_by_client.get(cl.client_id, AlertState.NONE.value),
            "eta_p10_s": None if math.isinf(eta.eta_p10_s) else eta.eta_p10_s,
            "eta_p50_s": None if math.isinf(eta.eta_p50_s) else eta.eta_p50_s,
            "eta_p90_s": None if math.isinf(eta.eta_p90_s) else eta.eta_p90_s})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "wgresponse-sim-console",
        "name": scenario.name,
        "seed": scenario.seed,
        "description": scenario.description,
        "t0_s": scenario.t0_s,
        "now_s": end_s,
        "cycle_period_s": report.cycle_period_s,
        "bbox": jsonable(scenario.bbox),
        "cell_m": scenario.spec.cell_m,
        "ignition": {"lat": round(scenario.ignition.lat, 6),
                     "lon": round(scenario.ignition.lon, 6)},
        "incidents": [{"incident_id": iid} for iid in sorted(ensembles)],
        "perimeter": perimeter,
        "isochrones": isochrones,
        "clients": client_rows,
        "crews": [{"crew_id": cid, "name": next((c.name for c in scenario.crews
                                                 if c.crew_id == cid), cid),
                   "status": tracks[cid][-1]["status"] if tracks[cid] else "unknown",
                   "track": tracks[cid]}
                  for cid in sorted(tracks)],
        "routes": routes,
        "safe_zones": [{"zone_id": z.zone_id, "name": z.name,
                        "lat": round(z.loc.lat, 6), "lon": round(z.loc.lon, 6)}
                       for z in scenario.safe_zones],
        "events": [jsonable(e) for e in report.events],
        "serviced": [jsonable(s) for s in report.serviced],
        "unserved": report.unserved,
        "safety": {"violations": [jsonable(v) for v in report.violations],
                   "min_margin_h": report.min_margin_h},
    }


# ---------------------------------------------------------------------------
# Ad-hoc helpers used by the CLI
# ---------------------------------------------------------------------------

def quick_forecast(loc: LatLon, cfg: Optional[ResponseConfig] = None,
                   seed: object = "forecast"):
    """Mock point forecast: synthetic ignition 5 km upwind (west) of `loc`,
    wind-event weather, internal ensemble. Returns (ThreatEta, t0_s) with
    absolute unix-second ETAs anchored at the fixed mock t0."""
    cfg = cfg or ResponseConfig(ensemble_n=4)
    t0 = 1_770_300_000.0
    ignition = _destination(loc, 270.0, 5_000.0)
    spec, bbox = _grid_for_points([loc, ignition], pad_m=1_500.0, cell_m=250.0)
    sc = Scenario(name=f"forecast-{seed}", seed=str(seed), t0_s=t0,
                  terrain_kind="flat", spec=spec, bbox=bbox, ignition=ignition,
                  weather_script=_wind_event_script(t0), clients=[], crews=[],
                  safe_zones=[])
    tile = _ScenarioTerrain(sc).tile(bbox, spec.cell_m)
    wx = _ScenarioWeather(sc).forecast(ignition, cfg.horizon_h)
    ens = _fallback_ensemble(tile, [ignition], wx, t0, cfg)
    return _eta_at(ens, loc), t0


def replan_console(console: dict, cfg: Optional[ResponseConfig] = None,
                   now_s: Optional[float] = None) -> DispatchPlan:
    """Re-plan dispatch from a sim console JSON dict (as written by run()).

    DEBUG/PREVIEW ONLY — the returned plan is NOT safety-validated: the console
    JSON carries no arrival grids, so corridor checks are skipped (every
    ingress/egress leg verdicts 'no_grid_data'/safe by default). The plan is
    flagged objective_breakdown['corridor_validated']=False with a safety_note;
    callers must not treat it as an operational plan.

    Departure deadlines use the canonical ember-inclusive rule
    (safety.latest_departure_s when importable, else the matching internal
    fallback) so preview deadlines carry the full crew-safety margin. Times in
    the returned plan are unix seconds anchored at `now_s` (default: the
    console's now_s, else t0_s)."""
    cfg = cfg or ResponseConfig()
    now = now_s if now_s is not None else float(console.get("now_s", console.get("t0_s", 0.0)))
    incident = (console.get("incidents") or [{"incident_id": "console"}])[0]["incident_id"]
    # Prefer the canonical safety deadline; the internal fallback is byte-equal
    # (both = p10 - (egress_buffer_min + ember_buffer_min)*60) but importing the
    # real module keeps replan_console consistent with the operational path.
    _safety = _try_import("safety")
    latest_depart_fn = getattr(_safety, "latest_departure_s", None) or _latest_departure_s
    jobs = []
    for row in console.get("clients", []):
        p10, p50 = row.get("eta_p10_s"), row.get("eta_p50_s")
        if p10 is None or p50 is None or p50 <= now:
            continue
        eta = ThreatEta(eta_p10_s=float(p10), eta_p50_s=float(p50),
                        eta_p90_s=float(row.get("eta_p90_s") or p50))
        cl = ClientProperty(client_id=row["client_id"],
                            loc=LatLon(float(row["lat"]), float(row["lon"])),
                            name=row.get("name", ""))
        latest_depart = latest_depart_fn(eta.eta_p10_s, cfg)
        jobs.append(DispatchJob(
            client=cl, incident_id=incident, eta=eta,
            latest_depart_s=latest_depart,
            latest_service_start_s=latest_depart - cfg.deploy_min * 60.0,
            earliest_useful_start_s=max(now, eta.eta_p50_s - cfg.gel_window_h * 3600.0)))
    crews = []
    for row in console.get("crews", []):
        track = row.get("track") or []
        if not track:
            continue
        last = track[-1]
        pos = LatLon(float(last["lat"]), float(last["lon"]))
        crews.append(Crew(crew_id=row["crew_id"], name=row.get("name", ""),
                          status=CrewStatus.AVAILABLE,
                          last_beacon=CrewBeacon(crew_id=row["crew_id"], loc=pos, t_s=now),
                          home_base=pos))
    zones = [SafeZone(z["zone_id"], LatLon(float(z["lat"]), float(z["lon"])),
                      z.get("name", "")) for z in console.get("safe_zones", [])]
    # Empty ensemble -> _corridor_safe has no eroded p10 grid, so every leg
    # verdicts safe without an actual check. Flag the plan as corridor-
    # unvalidated so this preview cannot be mistaken for an operational plan.
    plan = _fallback_plan(crews, jobs, {}, zones, cfg, now, None)
    plan.objective_breakdown["corridor_validated"] = False
    plan.objective_breakdown["safety_note"] = (
        "PREVIEW: corridor (ingress/egress) checks skipped — console JSON "
        "carries no arrival grids. Not a safety-validated operational plan.")
    return plan
