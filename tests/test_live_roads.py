"""tests/test_live_roads.py — THE LIVE WATCHER'S ROAD GRAPHS, one per service
area (founder 2026-11-17: "start with the Eagle valley and Aspen, download
Colorado"; wgresponse.liveroads).

  AREAS     areas.json lists each area's box; a listed graph that is missing
            raises; describe() reports nodes, edges and the source.
  ROUTING   a leg with both ends in one area is routed on its roads; a leg
            with an end outside every area gets no route (the planner keeps
            its chord); the provider takes the planner's mode hint; routes
            are memoised on the exact endpoints.
  REAL      the committed Eagle valley and Aspen / Roaring Fork graphs: every
            planning safe zone in them snaps to the network, and real legs
            (Vail -> Avon, Aspen -> Basalt, Eagle -> Aspen) are on roads."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wgresponse import geo, liveroads, roads  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402

A = LatLon(39.60, -106.50)


def _line_graph(origin: LatLon, n: int = 6, step_m: float = 500.0):
    """A straight east-west residential road of n nodes, 500 m apart."""
    pts = [geo.destination(origin, 90.0, k * step_m) for k in range(n)]
    nodes = {i: p for i, p in enumerate(pts)}
    edges = [roads.RoadEdge(u=i, v=i + 1, highway_class="residential",
                            length_m=step_m)
             for i in range(n - 1)]
    return roads.RoadGraph(nodes=nodes, edges=edges, meta={"source": "test"})


def _area_dir(tmp_path, graphs: dict):
    """graphs {key: (RoadGraph, bbox_wsen)} -> a road dir with areas.json."""
    index = {"areas": {}}
    for key, (g, bbox) in graphs.items():
        roads.save_road_graph(g, tmp_path / f"{key}.wgr")
        index["areas"][key] = {"name": key.title(), "bbox_wsen": list(bbox),
                               "source": "test"}
    (tmp_path / "areas.json").write_text(json.dumps(index), encoding="utf-8")
    return tmp_path


def _box(g, pad=0.01):
    lats = [p.lat for p in g.nodes.values()]
    lons = [p.lon for p in g.nodes.values()]
    return (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)


# ---------------------------------------------------------------------------
# AREAS / ROUTING
# ---------------------------------------------------------------------------

def test_a_leg_inside_an_area_is_routed_on_its_roads(tmp_path):
    g = _line_graph(A)
    sar = liveroads.ServiceAreaRoads(ResponseConfig(),
                                     _area_dir(tmp_path, {"valley": (g, _box(g))}))
    a, b = g.nodes[0], g.nodes[5]
    rr = sar.route(a, b)
    assert rr is not None and rr.on_roads
    assert rr.dist_m == pytest.approx(2500.0, rel=0.05)
    assert sar.area_for(a, b) == "valley"
    assert sar.provider()(a, b, mode="egress") is rr     # memo, mode accepted
    d = sar.describe()["valley"]
    assert d["nodes"] == 6 and d["edges"] == 5 and d["source"] == "test"


def test_a_leg_leaving_every_area_gets_no_route(tmp_path):
    g = _line_graph(A)
    sar = liveroads.ServiceAreaRoads(ResponseConfig(),
                                     _area_dir(tmp_path, {"valley": (g, _box(g))}))
    far = geo.destination(A, 0.0, 50_000.0)
    assert sar.area_for(g.nodes[0], far) is None
    assert sar.route(g.nodes[0], far) is None
    assert sar.provider()(far, g.nodes[0]) is None


def test_two_areas_each_route_their_own_legs(tmp_path):
    g1 = _line_graph(A)
    B = geo.destination(A, 0.0, 40_000.0)
    g2 = _line_graph(B)
    sar = liveroads.ServiceAreaRoads(ResponseConfig(), _area_dir(
        tmp_path, {"north": (g2, _box(g2)), "south": (g1, _box(g1))}))
    assert sar.keys == ["north", "south"]
    assert sar.area_for(g1.nodes[0], g1.nodes[3]) == "south"
    assert sar.area_for(g2.nodes[0], g2.nodes[3]) == "north"
    assert sar.route(g1.nodes[0], g2.nodes[0]) is None    # between areas


def test_a_listed_area_without_its_graph_raises(tmp_path):
    (tmp_path / "areas.json").write_text(json.dumps(
        {"areas": {"ghost": {"bbox_wsen": [-107, 39, -106, 40]}}}),
        encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="ghost"):
        liveroads.ServiceAreaRoads(ResponseConfig(), tmp_path)


# ---------------------------------------------------------------------------
# REAL — the committed service-area graphs
# ---------------------------------------------------------------------------

REAL = liveroads.DEFAULT_LIVE_ROAD_DIR
needs_real = pytest.mark.skipif(not (REAL / "areas.json").exists(),
                                reason="live service-area graphs not built")


@pytest.fixture(scope="module")
def real():
    return liveroads.ServiceAreaRoads(ResponseConfig())


@needs_real
def test_the_real_areas_are_the_founders(real):
    assert real.keys == ["aspen_roaring_fork", "eagle_valley"]
    for d in real.describe().values():
        assert d["nodes"] > 5_000 and "OpenStreetMap" in d["source"]


@needs_real
def test_every_planning_zone_in_an_area_snaps_to_its_roads(real):
    zones = json.loads((roads.DEFAULT_ROAD_DIR.parent / "aoi_conus.json")
                       .read_text(encoding="utf-8"))["safe_zones"]
    cfg = ResponseConfig()
    checked, far = 0, []
    for z in zones:
        p = LatLon(z["lat"], z["lon"])
        key = real.area_for(p, p)
        if key is None:
            continue
        g = real._areas[key]["graph"]
        snap = g.nearest_node(p, cfg.road_spur_max_m)
        # within the planner's spur cap, so a leg to the zone is on roads
        assert snap is not None, z["zone_id"]
        if snap[1] > 500.0:
            far.append(z["zone_id"])
        checked += 1
    assert checked >= 18
    # unverified map points off the network (the simulator's graphs agree:
    # Brush Creek Rd / Eagle Ranch is 889 m from a road node there too) —
    # the ground check of the zone list is the founder's item 3
    assert sorted(far) == ["eagle_co-z3", "glenwood_springs_co-z2"]


@needs_real
@pytest.mark.parametrize("a, b, km_min, km_max", [
    ((39.6305, -106.287), (39.6365, -106.523), 18.0, 30.0),   # East Vail -> Avon
    ((39.1905, -106.8195), (39.3705, -107.0315), 25.0, 40.0),  # Aspen -> Basalt
    ((39.6605, -106.8285), (39.1905, -106.8195), 95.0, 125.0),  # Eagle -> Aspen
])
def test_real_legs_are_on_roads(real, a, b, km_min, km_max):
    rr = real.route(LatLon(*a), LatLon(*b))
    assert rr is not None and rr.on_roads
    assert km_min <= rr.dist_m / 1000.0 <= km_max
