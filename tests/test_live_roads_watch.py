"""tests/test_live_roads_watch.py — THE WATCHER PLANS ON THE SERVICE AREAS'
ROADS (founder 2026-11-17: "start with the Eagle valley and Aspen";
wgresponse.liveroads, watch --live-roads).

  PLAN      a fire north of I-70 near Vail: the planner routes its legs on
            the valley graph, serves as many homes as on chords (5 of 6)
            and every planned stop stays lawful.
  I-70      a fire that starts ON I-70 between the crew (Avon) and the homes
            (East Vail): the straight chord skirts it 1.2 km north and serves
            all 6 homes; the road — the only way through — runs through the
            start, so roads serve none (no_safe_corridor). The chord's
            optimism, measured.
  OUTSIDE   a world outside every service area plans exactly as without
            roads (byte-identical plan).
  WATCH     the crew watch's way-out check asks the same provider.
  FLAG      --live-roads arms the 45-min road-egress slack (SAFETY
            SEMANTICS 31) and names the areas with the OSM attribution; no
            flag changes nothing."""
from __future__ import annotations

import dataclasses
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from tests.test_threat import (  # noqa: E402
    NOW,
    StubFeed,
    StubRegistry,
    incident,
    make_cfg,
    make_monitor,
)
from wgresponse import cli, clients, dispatch, liveroads  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    LatLon,
    ResponseConfig,
    SafeZone,
)
from wgresponse.server import jsonable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (liveroads.DEFAULT_LIVE_ROAD_DIR / "areas.json").exists(),
    reason="live service-area graphs not built")


class CountingRoads:
    """The real provider, counting calls and on-road answers."""

    def __init__(self, cfg):
        self._p = liveroads.ServiceAreaRoads(cfg).provider()
        self.calls = 0
        self.on_roads = 0

    def __call__(self, a, b, mode="ingress"):
        self.calls += 1
        rr = self._p(a, b, mode=mode)
        if rr is not None and rr.on_roads:
            self.on_roads += 1
        return rr


def _valley_zones():
    zones = json.loads((liveroads.DEFAULT_LIVE_ROAD_DIR.parent.parent
                        / "aoi_conus.json").read_text(encoding="utf-8"))
    return [SafeZone(z["zone_id"], LatLon(z["lat"], z["lon"]), z["name"])
            for z in zones["safe_zones"]
            if z["zone_id"].startswith(("vail_co-", "beaver_creek_co-"))]


def _vail_homes(n=6):
    return [c for c in clients.preset_roster(40)
            if c.client_id.startswith("vail_co-")][:n]


NORTH_OF_I70 = LatLon(39.655, -106.42)
ON_I70 = LatLon(39.625, -106.47)


def _valley_monitor(cfg, road_provider, origin=NORTH_OF_I70):
    fire = dataclasses.replace(incident("valley-fire"), origin=origin)
    mon = make_monitor(
        feeds=[StubFeed([fire])], cfg=cfg, alert_engine=AlertEngine(cfg),
        registry=StubRegistry(_vail_homes()),
        safe_zones=_valley_zones(),
        crew_tracker=ss.StubTracker([ss.crew(NOW - 60.0, "G1",
                                             LatLon(39.6365, -106.523))]),
        dispatcher=dispatch)
    mon._road_provider = road_provider
    return mon


def test_the_planner_drives_the_valley_roads_and_stays_lawful():
    cfg = make_cfg(route_margin_egress_roads_min=45.0)
    roads = CountingRoads(cfg)
    with_roads = _valley_monitor(cfg, roads).cycle(NOW)
    chords = _valley_monitor(cfg, None).cycle(NOW)
    assert with_roads.plan is not None and chords.plan is not None
    assert roads.calls > 0 and roads.on_roads > 0
    served = [sum(len(cp.stops) for cp in r.plan.plans.values())
              for r in (with_roads, chords)]
    assert served == [5, 5]
    for plan in (with_roads.plan, chords.plan):
        for cp in plan.plans.values():
            for stop in cp.stops:
                assert stop.depart_s <= stop.latest_depart_s + 1e-6


def test_a_fire_on_the_only_road_stops_what_the_chord_would_drive():
    cfg = make_cfg(route_margin_egress_roads_min=45.0)
    roads = CountingRoads(cfg)
    on_roads = _valley_monitor(cfg, roads, ON_I70).cycle(NOW).plan
    chords = _valley_monitor(cfg, None, ON_I70).cycle(NOW).plan
    assert sum(len(cp.stops) for cp in chords.plans.values()) == 6
    assert sum(len(cp.stops) for cp in on_roads.plans.values()) == 0
    assert {u.reason for u in on_roads.unserved} == {"no_safe_corridor"}
    assert roads.on_roads > 0


def test_a_world_outside_every_area_plans_as_without_roads():
    cfg = make_cfg()
    roads = CountingRoads(cfg)

    def run(provider):
        mon = make_monitor(
            cfg=cfg, dispatcher=dispatch,
            crew_tracker=ss.StubTracker([ss.crew(NOW - 60.0)]))
        mon._road_provider = provider
        return mon.cycle(NOW).plan

    a, b = run(roads), run(None)
    assert roads.on_roads == 0
    assert json.dumps(jsonable(a), sort_keys=True) == \
        json.dumps(jsonable(b), sort_keys=True)


def test_the_watch_asks_the_same_roads_for_a_way_out():
    cfg = ResponseConfig()
    roads = CountingRoads(cfg)
    zones = _valley_zones()
    here = LatLon(39.6365, -106.523)                 # Avon
    table = dispatch.egress_table(here, [], zones, cfg, NOW,
                                  road_provider=roads)
    assert roads.calls >= len(zones) and roads.on_roads >= 1
    assert {z for z, _ in table} == {z.zone_id for z in zones}
    chords = dispatch.egress_table(here, [], zones, cfg, NOW)
    assert [z for z, _ in chords] and len(chords) == len(table)


def test_the_flag_arms_the_road_egress_slack_and_names_the_areas():
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-121,38,-120,39", "--live-roads"]))
    cfg, sar, notes = cli._live_roads(ResponseConfig(), args)
    assert cfg.route_margin_egress_roads_min == 45.0
    assert sar.keys == ["aspen_roaring_fork", "eagle_valley"]
    assert len(notes) == 1 and "OpenStreetMap" in notes[0]
    assert "SAFETY SEMANTICS 31" in notes[0]
    plain = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-121,38,-120,39"]))
    assert cli._live_roads(ResponseConfig(), plain) == (ResponseConfig(),
                                                        None, [])
