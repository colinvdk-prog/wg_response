"""CORRIDOR HONESTY (contracts knob block; founder-arithmetic trace
2026-07-29 + founder "perform 1 ... 2-3 safety routes chosen to be safe from
fire, not speed"): Euclidean erosion + per-leg route candidates."""
from __future__ import annotations

import dataclasses
import math

from wgresponse import geo, roads
from wgresponse.contracts import GridSpec, LatLon, Raster, ResponseConfig
from wgresponse.roads import RoadEdge, RoadGraph


# ---------------------------------------------------------------- erosion


def _raster_with_hot(nrows=21, ncols=21, cell_m=90.0, hot=(10, 10)):
    spec = GridSpec(north_lat=34.2, west_lon=-118.3, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    r = Raster(spec, [math.inf] * (nrows * ncols))
    r.data[hot[0] * ncols + hot[1]] = 100.0
    return r


def test_euclidean_erosion_honest_radius():
    # one hot cell; 500 m radius @ 90 m cells: euclidean must reach exactly
    # the cells within 500 m and no farther.
    r = _raster_with_hot()
    er = geo.erode_min(r, 500.0, shape="euclidean")
    nc = r.spec.ncols
    for dr in range(-8, 9):
        for dc in range(-8, 9):
            i, j = 10 + dr, 10 + dc
            v = er.data[i * nc + j]
            dist = math.hypot(dr, dc) * 90.0
            if dist <= 500.0:
                assert v == 100.0, (dr, dc, dist)
            else:
                assert math.isinf(v), (dr, dc, dist)


def test_chebyshev_corner_overreach_documented():
    # the legacy square DOES import beyond the stated radius (the defect the
    # euclidean shape fixes): corner cell at 6,6 = 764 m still inherits.
    r = _raster_with_hot()
    er = geo.erode_min(r, 500.0)          # default chebyshev, unchanged
    nc = r.spec.ncols
    assert er.data[(10 + 6) * nc + (10 + 6)] == 100.0     # 764 m away
    eu = geo.erode_min(r, 500.0, shape="euclidean")
    assert math.isinf(eu.data[(10 + 6) * nc + (10 + 6)])  # honest disc: no


def test_chebyshev_default_byte_identical():
    r = _raster_with_hot()
    a = geo.erode_min(r, 500.0)
    b = geo.erode_min(r, 500.0, shape="chebyshev")
    assert a.data == b.data


def test_euclidean_zero_radius_copy():
    r = _raster_with_hot()
    er = geo.erode_min(r, 0.0, shape="euclidean")
    assert er.data == r.data and er.data is not r.data


# ---------------------------------------------------------------- candidates
#
# Synthetic 4-node diamond: S -> A -> G is fast but A is HOT (fire clock
# early); S -> B -> G is slower but cold. Geometry ~1 km legs.

_S = LatLon(34.000, -118.300)
_A = LatLon(34.005, -118.295)   # hot midpoint (fast path)
_B = LatLon(33.995, -118.295)   # cold midpoint (detour)
_G = LatLon(34.000, -118.290)


def _diamond_graph():
    nodes = {0: _S, 1: _A, 2: _B, 3: _G}
    def edge(u, v, speed):
        return RoadEdge(u=u, v=v, highway_class="residential",
                        length_m=geo.haversine_m(nodes[u], nodes[v]),
                        speed_mps=speed)
    edges = [edge(0, 1, 15.0), edge(1, 3, 15.0),
             edge(0, 2, 10.0), edge(2, 3, 10.0)]
    return RoadGraph(nodes, edges)


def _clock_fn(hot_arrival_s=600.0):
    def fn(p: LatLon) -> float:
        return hot_arrival_s if geo.haversine_m(p, _A) < 200.0 else math.inf
    return fn


def test_candidates_off_by_default():
    g = _diamond_graph()
    cfg = ResponseConfig()
    out = roads.route_candidates(g, _S, _G, cfg, _clock_fn())
    assert len(out) == 1                      # route_alternatives=1 -> legacy


def test_candidates_offer_cold_detour():
    g = _diamond_graph()
    cfg = dataclasses.replace(ResponseConfig(), route_alternatives=3)
    out = roads.route_candidates(g, _S, _G, cfg, _clock_fn())
    assert len(out) >= 2
    fastest = out[0]
    assert fastest.time_s == min(rr.time_s for rr in out)
    # at least one alternative avoids the hot midpoint entirely
    def touches_hot(rr):
        return any(geo.haversine_m(LatLon(p.lat, p.lon), _A) < 200.0
                   for p in rr.polyline)
    assert touches_hot(fastest)
    assert any(not touches_hot(rr) for rr in out[1:])


def test_widest_clock_prefers_cold_path_even_when_slower():
    g = _diamond_graph()
    found = roads._dijkstra_widest(g, 0, 3, {0: math.inf, 1: 600.0,
                                             2: math.inf, 3: math.inf})
    assert found is not None
    node_path, _ = found
    assert node_path == [0, 2, 3]             # detour via cold B


def test_astar_excluding_blocks_intermediate_only():
    g = _diamond_graph()
    found = roads._astar_excluding(g, 0, 3, {1})
    assert found is not None and found[0] == [0, 2, 3]
    # endpoints always allowed even when listed
    found2 = roads._astar_excluding(g, 0, 3, {0, 3, 1})
    assert found2 is not None and found2[0] == [0, 2, 3]


def test_no_fire_all_candidates_identical_choice():
    # with an all-cold clock the widest-clock route degenerates; candidates
    # dedupe to the fastest path only
    g = _diamond_graph()
    cfg = dataclasses.replace(ResponseConfig(), route_alternatives=3)
    out = roads.route_candidates(g, _S, _G, cfg, lambda p: math.inf)
    times = [rr.time_s for rr in out]
    assert times[0] == min(times)
