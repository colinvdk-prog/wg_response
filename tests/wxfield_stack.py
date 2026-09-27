"""Shared deterministic fixtures for the 2026-09-27 LOCAL WEATHER tests
(tests/test_weather_field.py; contracts SAFETY SEMANTICS 17,
cfg.weather_grid_km / watch --weather-grid-km).

Not a test module (no test_ prefix). Builds on tests/seeded_stack.py — the
REAL monitor / alert engine / dispatch / StateStore / _WatchDaemon stack over
stub feeds, terrain (uniform GR2) and registry, injected clocks, no network —
with a weather provider whose forecast DEPENDS ON WHERE IT IS ASKED.

THE KNOB-RELEVANT WORLD: one feed fire at ORIGIN with three protected
clients 12-16 km to its east and north-east, so the spread domain (bbox of
fire + clients, padded 2 km) is ~20 x 16 km — wide enough for a 5 km weather
lattice to put several forecast points inside it. GradientWeather blows
harder toward the east, veers the wind toward the south-west to the north,
and moistens the air to the north (a deterministic function of the rounded
point, varying by hour), so single-point (the fire centroid) and multi-point
spread genuinely differ in this world.

wx_world_run() fingerprints three cycles of that world; with the knob OFF
the digests were computed on commit b5ce62c (BEFORE the rule existed) with
scratch_review/wxfield/golden_wxfield.py and are pinned in the test.
"""
from __future__ import annotations

import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import dispatch as dispatch_mod  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.cli import _WatchDaemon  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    BBox,
    ClientProperty,
    LatLon,
    SafeZone,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

T0 = ss.T0
H = ss.H
PERIOD_S = ss.PERIOD_S

ORIGIN = LatLon(39.0, -121.0)                          # the feed fire
C_EAST = geo.destination(ORIGIN, 90.0, 12_000.0)       # 12 km east (downwind)
C_NE = geo.destination(ORIGIN, 45.0, 14_000.0)         # 14 km north-east
C_ESE = geo.destination(ORIGIN, 110.0, 16_000.0)       # 16 km east-south-east
CREW_LOC = geo.destination(ORIGIN, 0.0, 12_000.0)      # G1, 12 km north
ZONE = SafeZone(zone_id="Z1", loc=geo.destination(CREW_LOC, 0.0, 3_000.0),
                name="North Grange")
AOI = BBox(west=-121.4, south=38.7, east=-120.5, north=39.3)


class GradientWeather:
    """A forecast that depends on WHERE it is asked (the point rounded to
    1e-4 deg, like the NWS points URL) and varies by hour: the sustained wind
    grows 0.35 m/s per km east of ORIGIN (from 5 m/s), the direction veers
    3 deg per km north (from 270, a west wind), RH rises 0.8 % per km north
    (from 22 %). Records every (loc, horizon_h) asked. fail_at: points
    (rounded) for which forecast() raises RuntimeError (a provider failure
    at that point)."""

    def __init__(self, fail_at=()):
        self.calls = []
        self.fail_at = {(round(p.lat, 4), round(p.lon, 4)) for p in fail_at}

    def forecast(self, loc, horizon_h):
        self.calls.append((loc, horizon_h))
        key = (round(loc.lat, 4), round(loc.lon, 4))
        if key in self.fail_at:
            raise RuntimeError(f"stub weather failure at {key}")
        km_e = (key[1] - ORIGIN.lon) * geo.m_per_deg_lon(ORIGIN.lat) / 1000.0
        km_n = (key[0] - ORIGIN.lat) * geo.M_PER_DEG_LAT / 1000.0
        base = max(1.0, 5.0 + 0.35 * km_e)
        wdir = (270.0 - 3.0 * km_n) % 360.0
        rh = min(90.0, max(8.0, 22.0 + 0.8 * km_n))
        samples = []
        for i in range(int(horizon_h) + 2):
            w = base * (1.0 + 0.15 * math.sin(i / 3.0))
            samples.append(WeatherSample(
                t_s=T0 + i * H, wind_speed_mps=w, wind_gust_mps=w * 1.3,
                wind_dir_deg=(wdir + 2.0 * math.sin(i / 5.0)) % 360.0,
                rh_pct=rh, temp_c=29.0))
        return WeatherForecast(loc=loc, samples=samples, source="gradient")


def feed_incident(iid="inc-wx", origin=ORIGIN):
    return ss.feed_incident(iid=iid, name="Gradient Fire", origin=origin,
                            acreage=300.0, containment_pct=5.0)


def clients() -> list:
    return [ClientProperty(client_id="cE", loc=C_EAST, name="Casa Este",
                           graphgel_enrolled=True),
            ClientProperty(client_id="cNE", loc=C_NE, name="Casa Noreste",
                           graphgel_enrolled=True),
            ClientProperty(client_id="cESE", loc=C_ESE, name="Casa Sureste",
                           graphgel_enrolled=True)]


def make_stack(state_dir, cfg, incidents=None, clock=None, weather=None,
               dispatcher=dispatch_mod):
    """(daemon, monitor, feed, engine, weather) on the REAL stack, this
    world's crew, zone and gradient weather."""
    feed = ss.ScriptedFeed(incidents if incidents is not None
                           else [feed_incident()])
    roster = clients()
    registry = ss.StubRegistry(roster)
    engine = AlertEngine(cfg, notifier=LogNotifier(),
                         clients_by_id={c.client_id: c for c in roster})
    weather = weather if weather is not None else GradientWeather()
    monitor = ThreatMonitor(
        feeds=[feed], weather_provider=weather,
        terrain_provider=ss.StubTerrain(), registry=registry,
        alert_engine=engine, cfg=cfg, safe_zones=[ZONE],
        crew_tracker=ss.StubTracker([ss.crew(crew_id="G1", loc=CREW_LOC)]),
        dispatcher=dispatcher, aoi=AOI)
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=None, store=StateStore(state_dir), cfg=cfg,
                          aoi=AOI, period_s=PERIOD_S,
                          aoi_desc="weather-field test aoi", safe_zones=[ZONE],
                          clock=clock or ss.SettableClock(),
                          sleep=lambda s: None)
    return daemon, monitor, feed, engine, weather


def small_cfg(**kw):
    """seeded_stack.small_cfg with a 30 000-cell budget (the ~20 x 16 km
    domain at ~105 m cells)."""
    kw.setdefault("grid_max_cells", 30_000)
    return ss.small_cfg(**kw)


#: /api/world/summary keys that carry wall-clock telemetry (each domain's
#: rounded phase times) — masked like seeded_stack's solver_s.
_SUMMARY_WALL_KEYS = ("terrain_s", "weather_s", "spread_s")


def _mask_summary_wall(obj):
    if isinstance(obj, dict):
        return {k: (0.0 if k in _SUMMARY_WALL_KEYS else _mask_summary_wall(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_summary_wall(v) for v in obj]
    return obj


def summary_digest(summary: dict) -> str:
    """sha256 of the /api/world/summary payload (canonical JSON, the
    domains' wall-clock phase times masked)."""
    return hashlib.sha256(ss._canon(_mask_summary_wall(summary)).encode(
        "utf-8")).hexdigest()


def wx_world_run(state_dir, cfg=None, cycles=3):
    """Three cycles of the knob-relevant world one period apart; returns
    ([cycle digests], api digest, state digest, world-summary digest)."""
    cfg = cfg or small_cfg()
    clock = ss.SettableClock(T0)
    daemon, *_ = make_stack(state_dir, cfg, clock=clock)
    digests = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * PERIOD_S
        res = daemon.run_cycle()
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    return (digests, ss.api_digest(daemon.state()),
            ss.state_digest(daemon._store),
            summary_digest(daemon.world_summary()))
