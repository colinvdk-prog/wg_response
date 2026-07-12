"""END-TO-END LIVE DRY RUN — one ThreatMonitor.cycle with ALL live providers.

Live feeds (WFIGS+FIRIS+CALFIRE, bbox-wrapped) + live NWS weather + live LFPS
terrain, against the real VOLCANO incident (Riverside Co., ~33.531,-117.298),
with 3 FAKE clients (MockClientRegistry) at real La Cresta / Santa Rosa
Plateau coords, 2 fake crews beaconed at Murrieta/Temecula, and the real
dispatch module. READ-ONLY GETs only; nothing written to any live system.

NOTE the composition wart this script has to work around: ThreatMonitor calls
feed.fetch_active() with NO bbox — there is no AOI in ResponseConfig — so we
must wrap each feed in a _BBoxFeed adapter or the cycle assesses the whole US.
"""
import logging
import sys
import time

sys.path.insert(0, r"C:\Users\colin\wg_response")

from wgresponse.contracts import (BBox, ClientProperty, CrewBeacon, LatLon,
                                  ResponseConfig, SafeZone)
from wgresponse import dispatch
from wgresponse.alerts import AlertEngine
from wgresponse.clients import MockClientRegistry
from wgresponse.crews import CrewTracker
from wgresponse.feeds import CalFireFeed, FirisFeed, WfigsFeed
from wgresponse.notify import LogNotifier
from wgresponse.terrain import LfpsTerrain
from wgresponse.threat import ThreatMonitor
from wgresponse.weather import NwsWeather

logging.basicConfig(level=logging.WARNING)

VOLCANO = LatLon(33.531, -117.298)
# +-0.08 deg (~9 km): tight enough that only the VOLCANO cluster triggers
# terrain jobs. The first attempt used +-0.25 deg and pulled 20 "active"
# incidents (13 of them 45 h - 95 days stale), EVERY one within 60 km of a
# client -> ~20 sequential LFPS jobs -> the cycle did not finish in 25 min.
# That runaway IS finding OPS-6/OPS-10/OPS-16 evidence, recorded in the review.
AOI = BBox(west=VOLCANO.lon - 0.08, south=VOLCANO.lat - 0.08,
           east=VOLCANO.lon + 0.08, north=VOLCANO.lat + 0.08)

CLIENTS = [
    ClientProperty(client_id="fake-lacresta", loc=LatLon(33.545, -117.280),
                   address="La Cresta (fake)", name="Fake Client A",
                   graphgel_enrolled=True, contact_email="a@example.com"),
    ClientProperty(client_id="fake-plateau", loc=LatLon(33.560, -117.250),
                   address="Santa Rosa Plateau (fake)", name="Fake Client B",
                   graphgel_enrolled=True, contact_email="b@example.com"),
    ClientProperty(client_id="fake-murrieta-w", loc=LatLon(33.590, -117.220),
                   address="W Murrieta (fake)", name="Fake Client C",
                   graphgel_enrolled=True, contact_email="c@example.com"),
]


class _BBoxFeed:
    """Adapter: pin a bbox because ThreatMonitor passes none (finding OPS-x)."""

    def __init__(self, inner, bbox):
        self._inner = inner
        self._bbox = bbox

    def fetch_active(self, bbox=None):
        return self._inner.fetch_active(self._bbox)


def main():
    now = time.time()
    cfg = ResponseConfig(horizon_h=48.0)
    feeds = [_BBoxFeed(WfigsFeed(), AOI), _BBoxFeed(FirisFeed(), AOI),
             _BBoxFeed(CalFireFeed(), AOI)]
    registry = MockClientRegistry("e2e", clients=CLIENTS)
    notifier = LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c for c in CLIENTS})
    tracker = CrewTracker(cfg)
    tracker.ingest(CrewBeacon(crew_id="crew-mur", loc=LatLon(33.554, -117.213),
                              t_s=now - 30.0))
    tracker.ingest(CrewBeacon(crew_id="crew-tem", loc=LatLon(33.494, -117.148),
                              t_s=now - 45.0))
    zones = [SafeZone(zone_id="sz-murrieta", loc=LatLon(33.554, -117.203),
                      name="Murrieta town center")]
    mon = ThreatMonitor(
        feeds=feeds,
        weather_provider=NwsWeather(cfg=cfg),
        terrain_provider=LfpsTerrain(
            cache_dir=r"C:\Users\colin\wg_response\scratch_review\lfps_cache",
            email="ops@thewildfireguardian.com"),
        registry=registry, alert_engine=engine, cfg=cfg,
        safe_zones=zones, crew_tracker=tracker, dispatcher=dispatch)

    for label in ("COLD (terrain fetch)", "WARM (terrain cached)"):
        t0 = time.perf_counter()
        res = mon.cycle(time.time())
        dt = time.perf_counter() - t0
        print(f"\n=== cycle {label}: {dt:.1f}s wall-clock ===")
        print(f" incidents (active, fused, in AOI): {len(res.incidents)}")
        for inc in res.incidents:
            print(f"   {inc.incident_id[:38]:<40} {inc.name[:20]:<22} "
                  f"acres={inc.acreage} perim={'Y' if inc.perimeter else 'n'} "
                  f"age_h={(time.time()-inc.updated_at_s)/3600.0:.1f}")
        print(f" assessments: {len(res.assessments)}")
        for a in res.assessments:
            p10, p50, p90 = a.eta.hours_out(res.t_s)
            fmt = lambda h: "inf" if h == float("inf") else f"{h:.1f}h"
            print(f"   {a.client_id:<18} vs {a.incident_id[:30]:<32} "
                  f"p10={fmt(p10)} p50={fmt(p50)} p90={fmt(p90)}")
        print(f" alert events this cycle: {len(res.events)}")
        for e in res.events:
            print(f"   {e.prev.value}->{e.state.value} {e.client_id}: {e.message[:80]}")
        print(f" alert states: "
              f"{ {k: v.state.value for k, v in engine.states().items()} }")
        if res.plan is not None:
            stops = sum(len(p.stops) for p in res.plan.plans.values())
            print(f" dispatch plan: {stops} stops, {len(res.plan.unserved)} unserved")
            for uj in res.plan.unserved:
                print(f"   unserved {uj.client_id}: {uj.reason}")
        else:
            print(" dispatch plan: None (no pair in a dispatch state)")
        print(f" ensembles computed: {list(res.ensembles)}")
        print(f" notes ({len(res.notes)}):")
        for nline in res.notes:
            print(f"   - {nline}")
    print(f"\n notifier sends: {len(notifier.sent)}")


if __name__ == "__main__":
    main()
