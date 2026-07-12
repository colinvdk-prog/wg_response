"""LOCAL failure drills — no network. Kill each provider mid-scenario and
observe whether cycle() degrades with notes AND what happens to alert states.

The safety question: does a dead provider read as 'all clear'?

Sequence per drill:
  phase 1 (cycles 1..N): healthy mock providers, fire close enough that the
          client escalates (NOTICE -> WATCH or EXTREME).
  phase 2 (cycles N+1..): the named provider fails
          (feed -> [], weather -> raises, terrain -> raises, registry -> raises)
  observe: notes content, alert events emitted, final alert state.
"""
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")

from wgresponse.contracts import (BBox, ClientProperty, LatLon, ResponseConfig)
from wgresponse.alerts import AlertEngine
from wgresponse.clients import MockClientRegistry
from wgresponse.feeds import MockFireFeed
from wgresponse.notify import LogNotifier
from wgresponse.terrain import SyntheticTerrain
from wgresponse.threat import ThreatMonitor
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
CENTER = LatLon(34.20, -118.50)
CLIENT_LOC = LatLon(34.235, -118.50)   # ~3.9 km north of the fire


def build(feed, weather, terrain, registry, notifier):
    cfg = ResponseConfig(horizon_h=48.0, poll_period_s=300.0)
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c
                                        for c in registry_clients(registry)})
    mon = ThreatMonitor(feeds=[feed], weather_provider=weather,
                        terrain_provider=terrain, registry=registry,
                        alert_engine=engine, cfg=cfg, safe_zones=[])
    return mon, engine


def registry_clients(registry):
    try:
        return registry.protected_clients()
    except Exception:
        return []


def healthy_parts():
    incident_origin = CENTER
    ring_half = 0.005
    ring = [LatLon(incident_origin.lat - ring_half, incident_origin.lon - ring_half),
            LatLon(incident_origin.lat - ring_half, incident_origin.lon + ring_half),
            LatLon(incident_origin.lat + ring_half, incident_origin.lon + ring_half),
            LatLon(incident_origin.lat + ring_half, incident_origin.lon - ring_half),
            LatLon(incident_origin.lat - ring_half, incident_origin.lon - ring_half)]
    from wgresponse.contracts import FireIncident, IncidentSource
    inc = FireIncident(incident_id="drill-fire-1", name="DRILL FIRE",
                       source=IncidentSource.MOCK, origin=incident_origin,
                       updated_at_s=T0 - 3600.0, active=True,
                       perimeter=[ring], irwin_id="drill-fire-1",
                       source_ids={"mock": "drill-fire-1"})
    feed = MockFireFeed("drill", incidents=[inc])
    # hot, dry, strong wind blowing straight at the client (from the south)
    weather = MockWeather("drill", base_wind_mps=12.0, dir_deg=180.0,
                          rh_pct=10.0, temp_c=35.0, t0_s=T0)
    terrain = SyntheticTerrain("drill", kind="grass")
    client = ClientProperty(client_id="client-A", loc=CLIENT_LOC,
                            address="1 Drill Canyon Rd", name="Drill Client",
                            graphgel_enrolled=True)
    registry = MockClientRegistry("drill", clients=[client])
    return feed, weather, terrain, registry


class DeadFeed:
    def fetch_active(self, bbox=None):
        return []


class RaisingWeather:
    def forecast(self, loc, horizon_h):
        raise RuntimeError("NWS HTTP 503 (simulated outage)")

    def red_flag_active(self, loc):
        raise RuntimeError("NWS HTTP 503 (simulated outage)")


class RaisingTerrain:
    def tile(self, bbox, cell_m):
        raise RuntimeError("LFPS job failed (simulated outage)")


class RaisingRegistry:
    def protected_clients(self, bbox=None):
        raise RuntimeError("DynamoDB throttled (simulated outage)")


def run_drill(name, kill):
    feed, weather, terrain, registry = healthy_parts()
    notifier = LogNotifier()
    mon, engine = build(feed, weather, terrain, registry, notifier)

    period = 300.0
    print(f"\n=== drill: {name} ===")
    # phase 1: healthy cycles until state escalates (max 4)
    state_of = lambda: {k: v.state.value for k, v in engine.states().items()}
    t = T0
    for i in range(4):
        res = mon.cycle(t)
        t += period
    print(f" after healthy phase: states={state_of()}")
    print(f"  last healthy notes: {res.notes}")

    # phase 2: kill the provider
    if kill == "feed":
        mon._feeds = [DeadFeed()]
    elif kill == "weather":
        mon._weather = RaisingWeather()
    elif kill == "terrain":
        mon._terrain = RaisingTerrain()
    elif kill == "registry":
        mon._registry = RaisingRegistry()

    events_after = []
    notes_after = []
    for i in range(4):
        res = mon.cycle(t)
        events_after.extend(res.events)
        notes_after.append(list(res.notes))
        t += period
    print(f" after 4 dead cycles: states={state_of()}")
    for i, notes in enumerate(notes_after, 1):
        print(f"  dead cycle {i} notes: {notes if notes else '(NO NOTES)'}")
    if events_after:
        for e in events_after:
            print(f"  EVENT emitted during outage: {e.prev.value}->{e.state.value} "
                  f"client={e.client_id} msg={e.message[:90]!r}")
    else:
        print("  no events during outage")
    n_alerts_sent = len(notifier.sent)
    print(f"  notifier sends total: {n_alerts_sent}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    for name, kill in (("dead feed (returns [])", "feed"),
                       ("weather provider raises", "weather"),
                       ("terrain provider raises", "terrain"),
                       ("client registry raises", "registry")):
        run_drill(name, kill)
