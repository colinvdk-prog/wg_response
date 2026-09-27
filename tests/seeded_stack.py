"""Shared deterministic stub stack for the W1 seeded-incident tests.

Used by tests/test_seeded_incidents.py and tests/test_seed_api.py (not a test
module itself — no test_ prefix; imported the way the test_attack_s3_* files
import attack_s3_stack). Mirrors the production watch wiring in
wgresponse.cli (ThreatMonitor + AlertEngine + real dispatch + StateStore +
_WatchDaemon) over stub feeds / weather / terrain / registry, with injected
clocks and no network (contracts determinism discipline).

Geometry: the FEED fire burns at ORIGIN; client c1 sits ~4.3 km due east
(downwind of the stub west wind — reaches EXTREME_24H on the first cycle),
c2 ~12 km north-east. A SEEDED fire in these tests is lit at SEED_LOC, ~4 km
SOUTH-WEST of c1, so a seeded fire and the feed fire both threaten c1 from
different sides. FAR_SEED_LOC sits ~150 km away (outside the capped 60 km
threat radius): shown, never a domain.

cycle_digest / state_digest / api_digest are the canonical fingerprints the
byte-identity test pins: they were computed on the pre-W1 code (commit
09f94b5) for the no-seed scenario in baseline_run(), and the seeded-incident
build must reproduce them exactly with an empty seed store.

TWO WORLDS (2026-09-27, contracts SAFETY SEMANTICS 16): make_stack now builds
the watch daemon's two worlds — the LIVE daemon (its monitor is locked: a
seed offered to it raises seeded.LiveWorldRefused) and a TEST world
(wgresponse.worlds.TestWorld, sync=True so every test cycle runs inline
right after the live cycle, at the live cycle's time) with its OWN stub
weather / terrain / registry instances, its own dispatcher (dispatch module
unless `test_dispatcher` is given) and its own StateStore namespace
(<state_dir>/test_world). Seeds go to daemon.test_world.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from pathlib import Path

from wgresponse import dispatch as dispatch_mod
from wgresponse import geo
from wgresponse.alerts import AlertEngine
from wgresponse.cli import _WatchDaemon
from wgresponse.contracts import (
    BBox,
    ClientProperty,
    Crew,
    CrewBeacon,
    CrewStatus,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    ResponseConfig,
    SafeZone,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.notify import LogNotifier
from wgresponse.persist import StateStore
from wgresponse.sim import jsonable
from wgresponse.threat import ThreatMonitor

T0 = 1_780_000_000.0
H = 3600.0
PERIOD_S = 300.0
AOI = BBox(west=-121.2, south=38.8, east=-120.8, north=39.2)
ORIGIN = LatLon(39.0, -121.0)             # the feed fire
NEAR_LOC = LatLon(39.0, -120.95)          # c1: ~4.3 km due east (downwind)
FAR_LOC = LatLon(39.08, -120.88)          # c2: ~13 km north-east
SAFE_ZONE = SafeZone(zone_id="Z1", loc=LatLon(39.18, -121.05),
                     name="North Fairgrounds")
SEED_LOC = geo.destination(NEAR_LOC, 225.0, 4000.0)   # 4 km SW of c1
FAR_SEED_LOC = LatLon(39.15, -119.3)      # ~150 km east: no client in range


def small_cfg(**kw) -> ResponseConfig:
    """The small, fast, DETERMINISTIC test config: count-based dispatch
    budget (solver_budget_evals) so the real planner stops at the identical
    point on any machine load."""
    base = dict(horizon_h=24.0, ensemble_n=2, grid_cell_m=200.0,
                grid_max_cells=20_000, solver_budget_evals=4000)
    base.update(kw)
    return ResponseConfig(**base)


class ScriptedFeed:
    """Feed stub: settable incident list, togglable health, records bboxes."""

    def __init__(self, incidents=(), ok=True):
        self.incidents = list(incidents)
        self.ok = ok
        self.bboxes = []
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        self.bboxes.append(bbox)
        self.last_fetch_ok = bool(self.ok)
        if not self.ok:
            return []
        return list(self.incidents)


class StubWeather:
    """Constant west wind (blows east), samples anchored at T0; records the
    forecast calls (location + horizon) so tests can see who asked."""

    def __init__(self, wind_mps=8.0, dir_deg=270.0):
        self.wind_mps = wind_mps
        self.dir_deg = dir_deg
        self.calls = []

    def forecast(self, loc, horizon_h):
        self.calls.append((loc, horizon_h))
        hours = int(horizon_h) + 2
        samples = [WeatherSample(t_s=T0 + i * H, wind_speed_mps=self.wind_mps,
                                 wind_gust_mps=self.wind_mps * 1.25,
                                 wind_dir_deg=self.dir_deg,
                                 rh_pct=20.0, temp_c=30.0)
                   for i in range(hours)]
        return WeatherForecast(loc=loc, samples=samples, source="stub")


class StubTerrain:
    """Uniform fuel tile exactly covering the requested bbox (GR2 default);
    records every call."""

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
        return TerrainTile(spec=spec,
                           fuel=geo.make_raster(spec, fill=self.fuel_code),
                           slope_deg=geo.make_raster(spec, fill=0.0),
                           aspect_deg=geo.make_raster(spec, fill=0.0),
                           elev_m=geo.make_raster(spec, fill=100.0),
                           source="stub")


class StubRegistry:
    def __init__(self, clients):
        self._clients = list(clients)

    def protected_clients(self, bbox=None):
        if bbox is None:
            return list(self._clients)
        return [c for c in self._clients if bbox.contains(c.loc)]


class StubTracker:
    """Minimal crews() seam; ThreatMonitor degrades to the full list."""

    def __init__(self, crews):
        self._crews = list(crews)

    def crews(self):
        return list(self._crews)


class RecordingDispatcher:
    """The REAL planner (wgresponse.dispatch.plan), recording exactly what
    the monitor hands it — jobs, every ensemble, the per-incident forecasts."""

    def __init__(self):
        self.calls = []

    def plan(self, crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
             forecasts=None, eroded_rasters=None):
        self.calls.append({"jobs": list(jobs), "ens": dict(ens),
                           "forecasts": dict(forecasts or {}),
                           "now_s": now_s})
        return dispatch_mod.plan(crews, jobs, ens, safe_zones, cfg, now_s,
                                 incumbent=incumbent, forecasts=forecasts,
                                 eroded_rasters=eroded_rasters)


class SettableClock:
    """Deterministic clock the test sets explicitly (t)."""

    def __init__(self, t0=T0):
        self.t = float(t0)

    def __call__(self):
        return self.t


def feed_incident(iid="inc-1", name="Stub Fire", updated_at_s=T0 - 600.0,
                  origin=ORIGIN, acreage=500.0, containment_pct=10.0,
                  irwin_id=None):
    return FireIncident(incident_id=iid, name=name,
                        source=IncidentSource.MOCK, origin=origin,
                        updated_at_s=updated_at_s, acreage=acreage,
                        containment_pct=containment_pct, active=True,
                        irwin_id=irwin_id, source_ids={"mock": iid})


def clients():
    return [ClientProperty(client_id="c1", loc=NEAR_LOC, name="Casa Uno",
                           graphgel_enrolled=True),
            ClientProperty(client_id="c2", loc=FAR_LOC, name="Casa Dos",
                           graphgel_enrolled=True)]


def seed(iid="seeded:test-near", loc=None, t_ignite_s=T0, intensity="high",
         wind_dir_deg=225.0, weather=None, note="", created_by="test"):
    """A SeededIgnition (explicit id — tests pin ids; the API mints them).
    Default: a HIGH preset fire 4 km SW of c1 with the wind FROM 225 deg
    (pushes it north-east, at c1 and c2) — fully synthetic weather."""
    from wgresponse.contracts import SeededIgnition
    return SeededIgnition(incident_id=iid, loc=loc or SEED_LOC,
                          t_ignite_s=t_ignite_s, intensity=intensity,
                          weather=weather, wind_dir_deg=wind_dir_deg,
                          created_by=created_by, note=note)


def crew(now_s=T0, crew_id="G1", loc=LatLon(39.10, -121.05)):
    return Crew(crew_id=crew_id, name=crew_id, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=crew_id, loc=loc, t_s=now_s,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)


def make_test_world(state_dir, cfg, roster, weather=None, terrain=None,
                    clock=None, dispatcher=dispatch_mod, sync=True):
    """A TEST world (SAFETY SEMANTICS 16) over its OWN stub providers and
    registry instance, persisting under <state_dir>/test_world."""
    from wgresponse.worlds import TEST_NAMESPACE, TestWorld
    return TestWorld(
        cfg=cfg, aoi=AOI, safe_zones=[SAFE_ZONE],
        weather_provider=weather if weather is not None else StubWeather(),
        terrain_provider=terrain if terrain is not None else StubTerrain(),
        registry=StubRegistry(roster), dispatcher=dispatcher,
        store=StateStore(Path(state_dir) / TEST_NAMESPACE),
        clients_by_id={c.client_id: c for c in roster},
        clock=clock or SettableClock(), sync=sync,
        aoi_desc="seeded test aoi", period_s=PERIOD_S)


def make_stack(state_dir, cfg=None, incidents=None, weather=None,
               terrain=None, registry_clients=None, notifier=None,
               clock=None, dispatcher=dispatch_mod, feed=None,
               test_world=True, test_weather=None, test_terrain=None,
               test_dispatcher=dispatch_mod, test_clock=None,
               tracker=None):
    """(daemon, monitor, feed, engine, notifier, store, weather, terrain) on
    the REAL monitor / alert engine / dispatch / persist stack — the LIVE
    world. daemon.test_world is the TEST world (None with test_world=False):
    its weather / terrain default to FRESH stub instances configured like
    the live ones (so a test can tell the worlds' provider calls apart),
    its clock to the live SettableClock (a settable clock never advances on
    a read, so sharing it is harmless)."""
    cfg = cfg or small_cfg()
    feed = feed if feed is not None else ScriptedFeed(
        incidents if incidents is not None else [feed_incident()])
    roster = registry_clients if registry_clients is not None else clients()
    registry = StubRegistry(roster)
    notifier = notifier if notifier is not None else LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c for c in roster})
    weather = weather if weather is not None else StubWeather()
    terrain = terrain if terrain is not None else StubTerrain()
    clock = clock or SettableClock()
    monitor = ThreatMonitor(feeds=[feed], weather_provider=weather,
                            terrain_provider=terrain, registry=registry,
                            alert_engine=engine, cfg=cfg,
                            safe_zones=[SAFE_ZONE],
                            crew_tracker=(tracker if tracker is not None
                                          else StubTracker([crew()])),
                            dispatcher=dispatcher, aoi=AOI)
    store = StateStore(state_dir)
    tw = None
    if test_world:
        if test_weather is None and isinstance(weather, StubWeather):
            test_weather = StubWeather(wind_mps=weather.wind_mps,
                                       dir_deg=weather.dir_deg)
        if test_terrain is None and isinstance(terrain, StubTerrain):
            test_terrain = StubTerrain(fuel_code=terrain.fuel_code)
        tw = make_test_world(state_dir, cfg, roster, weather=test_weather,
                             terrain=test_terrain,
                             clock=test_clock or clock,
                             dispatcher=test_dispatcher)
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=tracker, store=store, cfg=cfg, aoi=AOI,
                          period_s=PERIOD_S, aoi_desc="seeded test aoi",
                          safe_zones=[SAFE_ZONE],
                          clock=clock,
                          sleep=lambda s: None, test_world=tw)
    return daemon, monitor, feed, engine, notifier, store, weather, terrain


# ---------------------------------------------------------------------------
# canonical fingerprints (byte-identity pins)
# ---------------------------------------------------------------------------

def _f(v) -> str:
    """Exact float text (repr round-trips; inf/nan spelled out)."""
    if v is None:
        return "None"
    return repr(float(v))


def raster_fingerprint(raster) -> str:
    return hashlib.sha256(
        b"".join(struct.pack("<d", v) for v in raster.data)).hexdigest()


def _mask_wall(obj):
    """Zero the planner's wall-clock telemetry (objective_breakdown.solver_s:
    time.perf_counter seconds the solver ran — the one non-deterministic
    number a DispatchPlan carries even under a count-based budget)."""
    if isinstance(obj, dict):
        return {k: (0.0 if k == "solver_s" else _mask_wall(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_wall(v) for v in obj]
    return obj


def _canon(obj) -> str:
    return json.dumps(_mask_wall(jsonable(obj)), sort_keys=True, default=str)


def cycle_digest(res) -> str:
    """sha256 over everything a cycle decides (never its wall-clock timings):
    incidents, assessments (exact ETA floats), alert events, every ensemble
    raster, notes, operator events, the degraded flag and the plan."""
    parts = {
        "t_s": _f(res.t_s),
        "incidents": [[i.incident_id, i.name, getattr(i.source, "value",
                                                      str(i.source)),
                       _f(i.origin.lat), _f(i.origin.lon), _f(i.updated_at_s),
                       _f(i.acreage), _f(i.containment_pct), bool(i.active)]
                      for i in res.incidents],
        "assessments": [[a.client_id, a.incident_id, _f(a.eta.eta_p10_s),
                         _f(a.eta.eta_p50_s), _f(a.eta.eta_p90_s),
                         _f(a.computed_at_s)] for a in res.assessments],
        "events": [[_f(e.t_s), e.client_id, e.incident_id, e.prev.value,
                    e.state.value, _f(e.eta_p50_h), _f(e.eta_p10_h),
                    e.message] for e in res.events],
        "ensembles": {iid: [[ens.spec.north_lat, ens.spec.west_lon,
                             ens.spec.cell_m, ens.spec.nrows, ens.spec.ncols],
                            [raster_fingerprint(m) for m in ens.members],
                            raster_fingerprint(ens.p10),
                            raster_fingerprint(ens.p50),
                            raster_fingerprint(ens.p90), _f(ens.t0_s)]
                      for iid, ens in sorted(res.ensembles.items())},
        "notes": list(res.notes),
        "operator_events": [[_f(o.t_s), o.kind, o.message, o.incident_id,
                             _canon(o.detail)] for o in res.operator_events],
        "degraded": bool(res.degraded),
        "plan": _canon(res.plan),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode(
        "utf-8")).hexdigest()


#: /api/state keys that carry wall-clock telemetry (time.monotonic) — the only
#: non-deterministic parts of the payload under an injected clock.
_WALL_KEYS = ("last_cycle_duration_s", "last_cycle_phases")


def api_digest(state: dict) -> str:
    clean = {k: v for k, v in state.items() if k not in _WALL_KEYS}
    return hashlib.sha256(_canon(clean).encode("utf-8")).hexdigest()


_SOLVER_S = re.compile(rb'"solver_s": [-+0-9.eE]+')


def state_digest(store) -> str:
    """sha256 of the persisted state file's BYTES, the planner's wall-clock
    solver_s value masked (see _mask_wall) and line endings normalized to LF
    (StateStore writes in text mode: CRLF on Windows, LF elsewhere) — every
    other byte (key order, indentation, float text) is compared as written."""
    raw = store.path.read_bytes().replace(b"\r\n", b"\n")
    raw = _SOLVER_S.sub(b'"solver_s": 0', raw)
    return hashlib.sha256(raw).hexdigest()


def baseline_run(state_dir, cycles=3):
    """The pinned NO-SEED scenario: one feed fire, two clients, one crew,
    one safe zone, the real planner; `cycles` cycles one period apart.
    Returns ([cycle digests], api digest, state digest)."""
    clock = SettableClock(T0)
    daemon, monitor, feed, engine, notifier, store, _, _ = make_stack(
        state_dir, clock=clock)
    digests = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * PERIOD_S
        res = daemon.run_cycle()
        digests.append(cycle_digest(res))
    clock.t += 30.0
    return digests, api_digest(daemon.state()), state_digest(store)
