"""Shared deterministic fixtures for the 2026-09-27 per-fire off-domain
horizon tests (tests/test_offmap_horizon.py; contracts SAFETY SEMANTICS 15).

Not a test module (no test_ prefix). Builds on tests/seeded_stack.py — the
REAL monitor / alert engine / dispatch / StateStore / _WatchDaemon stack over
stub feeds, weather (a west wind), terrain (uniform GR2) and registry,
injected clocks, no network — with its own crew and safe zones.

THE KNOB-RELEVANT WORLD (the Willow / 69-km shape, scaled down):

  inc-slow   the Willow shape: an 80 %-contained fire whose 1 km-half-side
             polygon at ORIGIN_A burns from 20 h ago (its own time), with a
             FRESH WFIGS behaviour report "Minimal | Smoldering, Creeping".
             Client cA sits 10 km north of it (so it builds a domain); its
             front crossed the 2 km pad beyond the polygon ~19 h before now,
             so under --fail-closed its off-raster bound reaches far east.
  inc-near   a fresh point fire (no behaviour report: UNKNOWN) 5 km upwind
             of client cB, which sits 80 km east of inc-slow — outside its
             60 km threat radius, i.e. outside its domain, like client-001
             and Willow.
  crew G1    staged 50 km east-north-east of inc-slow (44 km north-west of
             cB); zones Z1 (by the crew) and Z2 (10 km north of cB).

With the fail-closed law armed and the per-fire knob OFF, inc-slow's flat
1.7 m/s bound vetoes the G1 -> cB job (no_safe_corridor), exactly the live
Willow refusal; with the knob ON inc-slow extrapolates at its own ~1.3 km/h
front and the job is served, while inc-near (unknown) keeps the ceiling.

offmap_world_run() fingerprints three cycles of that world with the knob
OFF; the digests were computed on commit 6c748c5 (BEFORE the rule existed)
with scratch_review/offmap/golden_offmap.py and are pinned in the test.
"""
from __future__ import annotations

import hashlib
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
    FireIncident,
    IncidentSource,
    LatLon,
    SafeZone,
)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

T0 = ss.T0
H = ss.H
PERIOD_S = ss.PERIOD_S

ORIGIN_A = LatLon(39.0, -121.0)                        # inc-slow's polygon centre
CA_LOC = geo.destination(ORIGIN_A, 0.0, 10_000.0)      # its client, 10 km north
CB_LOC = geo.destination(ORIGIN_A, 90.0, 80_000.0)     # 80 km east: off its domain
B_LOC = geo.destination(CB_LOC, 270.0, 5_000.0)        # inc-near, 5 km upwind of cB
CREW_LOC = geo.destination(ORIGIN_A, 60.0, 50_000.0)   # G1, 44 km north-west of cB
Z1 = SafeZone(zone_id="Z1", loc=geo.destination(CREW_LOC, 0.0, 5_000.0),
              name="Crew Base")
Z2 = SafeZone(zone_id="Z2", loc=geo.destination(CB_LOC, 0.0, 10_000.0),
              name="North Grange")
AOI = BBox(west=-121.3, south=38.8, east=-119.9, north=39.3)

#: the fresh known-slow report (Willow's own words on 2026-09-27)
SLOW_BEHAVIOR = {"fire_behavior": "Minimal",
                 "fire_behavior_descriptors": ["Smoldering", "Creeping"],
                 "fire_behavior_at_s": T0 - 2 * H}


def square_ring(center: LatLon, half_m: float) -> list:
    n = geo.destination(center, 0.0, half_m)
    s = geo.destination(center, 180.0, half_m)
    e = geo.destination(center, 90.0, half_m)
    w = geo.destination(center, 270.0, half_m)
    return [LatLon(n.lat, w.lon), LatLon(n.lat, e.lon),
            LatLon(s.lat, e.lon), LatLon(s.lat, w.lon)]


def slow_incident(meta=None, iid="inc-slow", containment_pct=80.0,
                  perimeter_at_s=T0 - 20 * H) -> FireIncident:
    """The Willow shape (module docstring); `meta` replaces the behaviour
    report (pass {} for none)."""
    m = {"perimeter_at_s": perimeter_at_s}
    m.update(SLOW_BEHAVIOR if meta is None else meta)
    return FireIncident(incident_id=iid, name="Willow-shape Fire",
                        source=IncidentSource.MOCK, origin=ORIGIN_A,
                        updated_at_s=T0 - 600.0, acreage=500.0,
                        containment_pct=containment_pct, active=True,
                        perimeter=[square_ring(ORIGIN_A, 1_000.0)],
                        source_ids={"mock": iid}, meta=m)


def near_incident(meta=None) -> FireIncident:
    """A fresh point fire 5 km upwind of cB; no behaviour report."""
    return FireIncident(incident_id="inc-near", name="Near Fire",
                        source=IncidentSource.MOCK, origin=B_LOC,
                        updated_at_s=T0 - 600.0, acreage=50.0,
                        containment_pct=0.0, active=True,
                        source_ids={"mock": "inc-near"},
                        meta=dict(meta or {}))


def world_incidents() -> list:
    return [slow_incident(), near_incident()]


def clients() -> list:
    return [ClientProperty(client_id="cA", loc=CA_LOC, name="Casa A"),
            ClientProperty(client_id="cB", loc=CB_LOC, name="Casa B")]


def make_stack(state_dir, cfg, incidents=None, clock=None,
               dispatcher=dispatch_mod):
    """(daemon, monitor, feed, engine) on the REAL stack, this world's crew
    and zones."""
    feed = ss.ScriptedFeed(incidents if incidents is not None
                           else world_incidents())
    roster = clients()
    registry = ss.StubRegistry(roster)
    engine = AlertEngine(cfg, notifier=LogNotifier(),
                         clients_by_id={c.client_id: c for c in roster})
    monitor = ThreatMonitor(
        feeds=[feed], weather_provider=ss.StubWeather(),
        terrain_provider=ss.StubTerrain(), registry=registry,
        alert_engine=engine, cfg=cfg, safe_zones=[Z1, Z2],
        crew_tracker=ss.StubTracker([ss.crew(crew_id="G1", loc=CREW_LOC)]),
        dispatcher=dispatcher, aoi=AOI)
    daemon = _WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                          tracker=None, store=StateStore(state_dir), cfg=cfg,
                          aoi=AOI, period_s=PERIOD_S,
                          aoi_desc="offmap test aoi", safe_zones=[Z1, Z2],
                          clock=clock or ss.SettableClock(),
                          sleep=lambda s: None)
    return daemon, monitor, feed, engine


def fail_closed_cfg(**kw):
    """seeded_stack.small_cfg + the watch daemon's --fail-closed pair."""
    return ss.small_cfg(corridor_fail_closed=True,
                        corridor_unreached_bound=True, **kw)


#: /api/world/summary keys that carry wall-clock telemetry (each domain's
#: rounded phase times, time.monotonic) — masked like seeded_stack's solver_s.
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


def offmap_world_run(state_dir, cfg=None, cycles=3):
    """Three cycles of the knob-relevant world one period apart; returns
    ([cycle digests], api digest, state digest, world-summary digest)."""
    cfg = cfg or fail_closed_cfg()
    clock = ss.SettableClock(T0)
    daemon, *_ = make_stack(state_dir, cfg, clock=clock)
    digests = []
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * PERIOD_S
        res = daemon.run_cycle()
        digests.append(ss.cycle_digest(res))
    clock.t += 30.0
    return (digests, ss.api_digest(daemon.state()), ss.state_digest(
        daemon._store), summary_digest(daemon.world_summary()))
