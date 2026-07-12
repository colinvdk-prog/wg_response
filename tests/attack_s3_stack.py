"""Shared deterministic stub stack for the promoted Session-3 attack tests.

Ported from scratch_review/attack_s3_common.py (adversarial verifier harness)
so tests/test_attack_s3_*.py are self-contained inside tests/: a scripted
feed / stub weather / stub terrain / stub registry stack plus a _WatchDaemon
builder mirroring the production wiring in wgresponse.cli, so the promoted
persistence / containment-gate / staleness / watch pins exercise the REAL
ThreatMonitor, AlertEngine, fusion and persist code with zero network and
injected clocks (contracts determinism discipline).

Geometry matches tests/test_cli.py: fire at ORIGIN, client ~4.3 km due east,
wind FROM 270 deg (pushes the head due east), uniform GR2 fuel -> the client
pair reaches EXTREME_24H on the first cycle with the small_cfg() grid.

Not a test module (no test_ prefix); imported by the test_attack_s3_* files.
"""
from __future__ import annotations

import math

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
from wgresponse.threat import ThreatMonitor

T0 = 1_780_000_000.0
H = 3600.0
AOI = BBox(west=-121.2, south=38.8, east=-120.8, north=39.2)
ORIGIN = LatLon(39.0, -121.0)
CLIENT_LOC = LatLon(39.0, -120.95)      # ~4.3 km due east (downwind)


def small_cfg(**kw) -> ResponseConfig:
    cfg = ResponseConfig(horizon_h=24.0, ensemble_n=2, grid_cell_m=200.0,
                         grid_max_cells=20_000)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


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
    def forecast(self, loc, horizon_h):
        hours = int(horizon_h) + 2
        samples = [WeatherSample(t_s=T0 + i * H, wind_speed_mps=8.0,
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


class StubTracker:
    """Minimal crews() seam; ThreatMonitor degrades to the full list."""

    def __init__(self, crews):
        self._crews = list(crews)

    def crews(self):
        return list(self._crews)


class FakeClock:
    """Deterministic clock: starts at t0, advances `step` s per read."""

    def __init__(self, t0=T0, step=1.0):
        self.t = float(t0)
        self.step = float(step)

    def __call__(self):
        self.t += self.step
        return self.t


def incident(iid="inc-1", name="Stub Fire", updated_at_s=T0 - 600.0,
             origin=ORIGIN, acreage=500.0, containment_pct=10.0,
             source=IncidentSource.MOCK, irwin_id=None, perimeter=None,
             hotspots=None, active=True):
    return FireIncident(incident_id=iid, name=name, source=source,
                        origin=origin, updated_at_s=updated_at_s,
                        acreage=acreage, containment_pct=containment_pct,
                        active=active, perimeter=perimeter, hotspots=hotspots,
                        irwin_id=irwin_id,
                        source_ids={source.value: iid})


def client(cid="c1", loc=CLIENT_LOC, name="Casa Uno"):
    return ClientProperty(client_id=cid, loc=loc, name=name,
                          graphgel_enrolled=True)


def fresh_crew(now_s, crew_id="G1", loc=LatLon(39.10, -121.05)):
    return Crew(crew_id=crew_id, name=crew_id, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=crew_id, loc=loc, t_s=now_s,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)


SAFE_ZONE_N = SafeZone(zone_id="Z1", loc=LatLon(39.18, -121.05),
                       name="North Fairgrounds")


def make_daemon(tmp_path, cfg=None, dispatcher=None, notifier=None,
                incidents=None, clock=None, feed=None, tracker=None,
                safe_zones=(), sleep=None, clients=None):
    """(daemon, feed, engine, notifier, store) on the real monitor stack."""
    cfg = cfg or small_cfg()
    feed = feed if feed is not None else ScriptedFeed(
        incidents if incidents is not None else [incident()])
    clients = clients if clients is not None else [client()]
    registry = StubRegistry(clients)
    notifier = notifier if notifier is not None else LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c for c in clients})
    monitor = ThreatMonitor(feeds=[feed], weather_provider=StubWeather(),
                            terrain_provider=StubTerrain(), registry=registry,
                            alert_engine=engine, cfg=cfg,
                            safe_zones=list(safe_zones), crew_tracker=tracker,
                            dispatcher=dispatcher, aoi=AOI)
    store = StateStore(tmp_path / "state")
    sleeps = []
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=tracker, store=store, cfg=cfg, aoi=AOI,
                          period_s=300.0, aoi_desc="attack aoi",
                          safe_zones=list(safe_zones),
                          clock=clock or FakeClock(),
                          sleep=sleep if sleep is not None
                          else (lambda s: sleeps.append(s)))
    daemon._attack_sleeps = sleeps
    return daemon, feed, engine, notifier, store


def km_east(origin, km):
    return geo.destination(origin, 90.0, km * 1000.0)
