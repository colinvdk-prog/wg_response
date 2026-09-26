"""wgresponse.roads — graph/A*/snap/fallback/cache-format tests + per-tile pins.

NO NETWORK ANYWHERE. Logic tests run on hand-built fixtures; the per-tile
tests load the committed ``data/roads/<key>.wgr`` graphs (written once by
``py -3 tools/fetch_sim_roads.py`` — live Overpass/OSM) and SKIP WITH A LOUD
REASON when a graph is missing: a failed or never-run fetch must stay visible
in the pytest output, because roads.route falls back to straight legs
(on_roads=False) for exactly that location, never silently.

Fixture geometry: a 3x4 lattice (12 nodes, ids r*4+c) near (39, -105) —
row 0 a slow direct "service" street (7 m/s), row 2 a fast "primary" loop
(22 m/s) reached by primary verticals at both ends, row 1 a "residential"
middle (11 m/s). The direct service path n0->n3 is ~3.1 km; the primary
detour is ~7.1 km yet ~29% faster — A* must take it.
"""
from __future__ import annotations

import time

import pytest

from wgresponse import geo, travel
from wgresponse.contracts import LatLon, ResponseConfig, seeded_rng
from wgresponse.roads import (CLASS_SPEED_MPS, SNAP_MAX_M, SNAP_SPEED_MPS,
                              RoadEdge, RoadGraph, RoadRoute, RouteSegment,
                              class_speed_mps, load_road_graph, road_file,
                              route, save_road_graph)
from wgresponse.simui import LOCATIONS

CFG = ResponseConfig()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_LAT0, _LON0 = 39.0, -105.0
_DLAT, _DLON = 0.009, 0.012          # ~1.0 km x ~1.04 km spacing


def _nl(r: int, c: int) -> LatLon:
    """Lattice node coord, pre-rounded to the cache quantum (1e-5 deg) so
    save/load round-trips are exactly identity."""
    return LatLon(round(_LAT0 + r * _DLAT, 5), round(_LON0 + c * _DLON, 5))


def _edge(nodes, u, v, cls, geometry=()):
    """Edge with haversine length pre-rounded to the cache quantum (0.1 m)."""
    return RoadEdge(u, v, cls, round(geo.haversine_m(nodes[u], nodes[v]), 1),
                    geometry=geometry)


def _grid12() -> RoadGraph:
    """The 12-node lattice described in the module docstring."""
    nodes = {r * 4 + c: _nl(r, c) for r in range(3) for c in range(4)}
    edges = []
    for c in range(3):
        edges.append(_edge(nodes, c, c + 1, "service"))          # row 0: slow direct
        edges.append(_edge(nodes, 4 + c, 5 + c, "residential"))  # row 1: middle
        edges.append(_edge(nodes, 8 + c, 9 + c, "primary"))      # row 2: fast loop
    for u, v in ((0, 4), (4, 8), (3, 7), (7, 11)):               # primary verticals
        edges.append(_edge(nodes, u, v, "primary"))
    return RoadGraph(nodes, edges, meta={"key": "grid12"})


_FAST_PATH = (0, 4, 8, 9, 10, 11, 7, 3)     # the primary detour n0 -> n3


def _path_time_s(g: RoadGraph, node_ids, cfg) -> float:
    """Sum of edge times along consecutive node ids (free-flow x fire factor)."""
    total = 0.0
    for u, v in zip(node_ids, node_ids[1:]):
        e = next(e for e in g.edges if {e.u, e.v} == {u, v})
        total += e.length_m * cfg.fire_traffic_factor / e.speed_mps
    return total


def _poly_len_m(poly) -> float:
    return sum(geo.haversine_m(poly[i], poly[i + 1])
               for i in range(len(poly) - 1))


# ---------------------------------------------------------------------------
# A* path choice
# ---------------------------------------------------------------------------

def test_astar_prefers_fast_road_over_short_slow_one():
    g = _grid12()
    r = route(g, g.nodes[0], g.nodes[3], CFG)
    assert r.on_roads
    assert r.polyline == [g.nodes[i] for i in _FAST_PATH]
    # ~7.1 km of primary beats ~3.1 km of service on TIME:
    fast_t = _path_time_s(g, _FAST_PATH, CFG)
    slow_t = _path_time_s(g, (0, 1, 2, 3), CFG)
    assert fast_t < slow_t
    assert r.time_s == pytest.approx(fast_t, abs=1e-9)
    assert r.dist_m > geo.haversine_m(g.nodes[0], g.nodes[3])


def test_route_same_snap_node_degenerates_gracefully():
    g = _grid12()
    a = LatLon(g.nodes[5].lat + 1e-4, g.nodes[5].lon)
    b = LatLon(g.nodes[5].lat - 1e-4, g.nodes[5].lon)
    r = route(g, a, b, CFG)
    assert r.on_roads
    assert r.polyline == [a, g.nodes[5], b]
    exp = (geo.haversine_m(a, g.nodes[5]) + geo.haversine_m(g.nodes[5], b)) \
        * CFG.fire_traffic_factor / SNAP_SPEED_MPS
    assert r.time_s == pytest.approx(exp, abs=1e-9)


# ---------------------------------------------------------------------------
# Snap legs
# ---------------------------------------------------------------------------

def test_snap_legs_included_in_dist_and_time():
    g = _grid12()
    a = LatLon(g.nodes[0].lat, round(g.nodes[0].lon - 0.002, 5))   # ~173 m W of n0
    b = LatLon(g.nodes[3].lat, round(g.nodes[3].lon + 0.002, 5))   # ~173 m E of n3
    r = route(g, a, b, CFG)
    assert r.on_roads
    assert r.polyline[0] == a and r.polyline[-1] == b
    assert r.polyline[1] == g.nodes[0] and r.polyline[-2] == g.nodes[3]
    da = geo.haversine_m(a, g.nodes[0])
    db = geo.haversine_m(b, g.nodes[3])
    node_route = route(g, g.nodes[0], g.nodes[3], CFG)
    f = CFG.fire_traffic_factor
    assert r.time_s == pytest.approx(
        node_route.time_s + (da + db) * f / SNAP_SPEED_MPS, abs=1e-9)
    assert r.dist_m == pytest.approx(node_route.dist_m + da + db, abs=1e-9)


def test_snap_ties_break_on_smaller_node_id():
    # two nodes at IDENTICAL coords -> bit-exact distance tie -> smaller id.
    same = LatLon(39.0, -105.001)
    nodes = {7: same, 3: same, 5: LatLon(39.0, -104.99)}
    g = RoadGraph(nodes, [RoadEdge(3, 7, "residential", 1.0),
                          RoadEdge(3, 5, "residential", 1000.0)])
    hit = g.nearest_node(LatLon(39.0, -105.0))
    assert hit is not None and hit[0] == 3


# ---------------------------------------------------------------------------
# Edge geometry (curves)
# ---------------------------------------------------------------------------

def test_polyline_follows_edge_geometry_both_directions():
    n = {0: LatLon(39.0, -105.0), 1: LatLon(39.0, -104.98)}
    curve = (LatLon(39.003, -104.995), LatLon(39.003, -104.985))   # u->v order
    e = RoadEdge(0, 1, "residential",
                 round(_poly_len_m([n[0], *curve, n[1]]), 1), geometry=curve)
    g = RoadGraph(n, [e])
    fwd = route(g, n[0], n[1], CFG)
    assert fwd.polyline == [n[0], curve[0], curve[1], n[1]]
    rev = route(g, n[1], n[0], CFG)
    assert rev.polyline == [n[1], curve[1], curve[0], n[0]]
    assert fwd.time_s == pytest.approx(
        e.length_m * CFG.fire_traffic_factor / e.speed_mps, abs=1e-9)


# ---------------------------------------------------------------------------
# Fallbacks (contracts: NEVER silently pretend a road route)
# ---------------------------------------------------------------------------

def _assert_tier0_fallback(r, a, b):
    assert not r.on_roads
    assert r.polyline == [a, b]
    assert r.time_s == travel.travel_time_s(a, b, CFG)
    assert r.dist_m == geo.haversine_m(a, b)


def test_fallback_no_graph_and_empty_graph():
    a, b = _nl(0, 0), _nl(2, 3)
    _assert_tier0_fallback(route(None, a, b, CFG), a, b)
    _assert_tier0_fallback(route(RoadGraph({}, []), a, b, CFG), a, b)


def test_fallback_disconnected_components():
    nodes = {0: _nl(0, 0), 1: _nl(0, 1), 2: _nl(2, 0), 3: _nl(2, 1)}
    g = RoadGraph(nodes, [RoadEdge(0, 1, "residential", 1000.0),
                          RoadEdge(2, 3, "residential", 1000.0)])
    _assert_tier0_fallback(route(g, nodes[0], nodes[3], CFG),
                           nodes[0], nodes[3])


def test_fallback_endpoint_beyond_snap_cap():
    g = _grid12()
    far = LatLon(_LAT0 + 0.05, _LON0)            # ~5.6 km from every node
    assert geo.haversine_m(far, g.nodes[8]) > SNAP_MAX_M
    _assert_tier0_fallback(route(g, g.nodes[0], far, CFG), g.nodes[0], far)
    _assert_tier0_fallback(route(g, far, g.nodes[0], CFG), far, g.nodes[0])


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_route_deterministic_repeat_and_across_reload(tmp_path):
    g = _grid12()
    a = LatLon(39.001, -104.999)
    b = LatLon(39.017, -104.966)
    r1 = route(g, a, b, CFG)
    r2 = route(g, a, b, CFG)
    assert r1 == r2
    save_road_graph(g, tmp_path / "grid12.wgr")
    g2 = load_road_graph("grid12", road_dir=tmp_path)
    r3 = route(g2, a, b, CFG)
    assert r3 == r1


def test_equal_cost_tie_breaks_deterministically():
    # symmetric diamond: two exactly-equal-cost paths s->t; the path through
    # the SMALLER intermediate node id must win, every time.
    nodes = {0: LatLon(39.0, -105.0), 1: LatLon(39.009, -105.012),
             2: LatLon(38.991, -105.012), 3: LatLon(39.0, -105.024)}
    edges = [RoadEdge(0, 1, "residential", 1500.0),
             RoadEdge(1, 3, "residential", 1500.0),
             RoadEdge(0, 2, "residential", 1500.0),
             RoadEdge(2, 3, "residential", 1500.0)]
    g = RoadGraph(nodes, edges)
    for _ in range(3):
        r = route(g, nodes[0], nodes[3], CFG)
        assert r.polyline == [nodes[0], nodes[1], nodes[3]]


# ---------------------------------------------------------------------------
# Speed table + fire-traffic factor at route time
# ---------------------------------------------------------------------------

def test_speed_table_sane_and_class_derivation():
    assert all(v > 0 for v in CLASS_SPEED_MPS.values())
    # the pinned OSM-routing-convention anchors:
    assert CLASS_SPEED_MPS["motorway"] == 29.0
    assert CLASS_SPEED_MPS["trunk"] == CLASS_SPEED_MPS["primary"] == 22.0
    assert CLASS_SPEED_MPS["secondary"] == 18.0
    assert CLASS_SPEED_MPS["tertiary"] == 15.0
    assert CLASS_SPEED_MPS["residential"] == CLASS_SPEED_MPS["unclassified"] == 11.0
    assert CLASS_SPEED_MPS["service"] == CLASS_SPEED_MPS["track"] == 7.0
    e = RoadEdge(0, 1, "secondary", 100.0)          # speed omitted -> table
    assert e.speed_mps == 18.0
    assert class_speed_mps("no_such_class") == 11.0  # conservative default


def _cfg_with_factor(f: float) -> ResponseConfig:
    cfg = ResponseConfig()
    cfg.fire_traffic_factor = f
    return cfg


def test_fire_traffic_factor_applied_at_route_time_not_in_cache(tmp_path):
    g = _grid12()
    save_road_graph(g, tmp_path / "grid12.wgr")
    raw = (tmp_path / "grid12.wgr").read_text(encoding="utf-8")
    assert "speed" not in raw            # speeds are NEVER baked into the cache
    g2 = load_road_graph("grid12", road_dir=tmp_path)
    a = LatLon(39.001, -104.999)
    b = LatLon(39.017, -104.966)
    t1 = route(g2, a, b, _cfg_with_factor(1.0)).time_s
    t2 = route(g2, a, b, _cfg_with_factor(2.5)).time_s
    assert t2 == pytest.approx(2.5 * t1, rel=1e-12)


# ---------------------------------------------------------------------------
# Cache format
# ---------------------------------------------------------------------------

def test_save_load_roundtrip_exact(tmp_path):
    g = _grid12()
    path = tmp_path / "grid12.wgr"
    save_road_graph(g, path)
    g2 = load_road_graph("grid12", road_dir=tmp_path)
    assert g2 is not None
    assert g2.nodes == g.nodes                       # coords exact (1e-5 grid)
    assert len(g2.edges) == len(g.edges)
    assert set(g2.edges) == set(g.edges)             # save canonicalizes order
    assert g2.meta == g.meta
    # byte-stable: saving the loaded graph reproduces the identical file
    save_road_graph(g2, tmp_path / "again.wgr")
    assert (tmp_path / "again.wgr").read_bytes() == path.read_bytes()


def test_load_missing_returns_none_never_raises(tmp_path):
    assert load_road_graph("no_such_key", road_dir=tmp_path) is None
    assert load_road_graph("grid12", road_dir=tmp_path / "absent_dir") is None


def test_load_corrupt_raises_loudly(tmp_path):
    (tmp_path / "bad1.wgr").write_text("GARBAGE\n{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not a .wgr"):
        load_road_graph("bad1", road_dir=tmp_path)
    (tmp_path / "bad2.wgr").write_text("WGR 999\n{}", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_road_graph("bad2", road_dir=tmp_path)
    (tmp_path / "bad3.wgr").write_text("WGR 1\nnot json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        load_road_graph("bad3", road_dir=tmp_path)


def test_versioned_header_present(tmp_path):
    g = _grid12()
    save_road_graph(g, tmp_path / "grid12.wgr")
    first = (tmp_path / "grid12.wgr").read_text(encoding="utf-8").split("\n")[0]
    assert first == "WGR 1"


# ---------------------------------------------------------------------------
# Per-tile pins on the committed real road graphs (data/roads/<key>.wgr)
# ---------------------------------------------------------------------------

KEYS = tuple(sorted(LOCATIONS))

_graphs: dict = {}
_load_s: dict = {}


def _graph(key: str):
    """Load (and memoize) a committed road graph; loud skip when missing."""
    path = road_file(key)
    if not path.exists():
        pytest.skip(
            f"SIMULATOR ROAD GRAPH MISSING: {path} — the {key!r} Overpass "
            f"fetch failed or was never run. Recreate it with `py -3 "
            f"tools/fetch_sim_roads.py --only {key}` (LIVE network). Until "
            f"then roads.route falls back to STRAIGHT legs (on_roads=False) "
            f"for this location — this skip is that failure staying visible.")
    if key not in _graphs:
        t0 = time.perf_counter()
        _graphs[key] = load_road_graph(key)
        _load_s[key] = time.perf_counter() - t0
    return _graphs[key]


def _wui_pair(g, key: str):
    """Two seeded 'WUI-ish' points: inside the middle 60% of the graph's
    bbox, each within 1 km of a road node, >= 3 km apart. Deterministic
    (contracts.seeded_rng); fails loudly if the graph can't support it."""
    lats = [p.lat for p in g.nodes.values()]
    lons = [p.lon for p in g.nodes.values()]
    lat_lo, lat_hi = min(lats), max(lats)
    lon_lo, lon_hi = min(lons), max(lons)
    lat_lo, lat_hi = (lat_lo + 0.2 * (lat_hi - lat_lo),
                      lat_hi - 0.2 * (lat_hi - lat_lo))
    lon_lo, lon_hi = (lon_lo + 0.2 * (lon_hi - lon_lo),
                      lon_hi - 0.2 * (lon_hi - lon_lo))
    rng = seeded_rng("roads-wui-pair", key)
    pts = []
    for _ in range(400):
        p = LatLon(lat_lo + rng.random() * (lat_hi - lat_lo),
                   lon_lo + rng.random() * (lon_hi - lon_lo))
        if g.nearest_node(p, max_m=1000.0) is None:
            continue
        if pts and geo.haversine_m(pts[0], p) < 3000.0:
            continue
        pts.append(p)
        if len(pts) == 2:
            return pts
    raise AssertionError(
        f"{key}: could not draw two road-adjacent points >=3 km apart in "
        f"400 seeded draws — road graph implausibly sparse?")


@pytest.mark.parametrize("key", KEYS)
class TestRoadTiles:
    def test_loads_fast_and_plausibly_sized(self, key):
        g = _graph(key)
        assert _load_s[key] < 2.0, (
            f"{key}: load took {_load_s[key]:.2f} s (>2 s budget)")
        assert len(g.nodes) >= 300, f"{key}: only {len(g.nodes)} nodes"
        assert len(g.edges) >= 300, f"{key}: only {len(g.edges)} edges"
        assert g.meta.get("key") == key
        assert "overpass" in str(g.meta.get("source", "")).lower()
        assert g.meta.get("largest_component_node_pct", 0) >= 50.0

    def test_safe_zones_and_center_snap_to_roads(self, key):
        """Every travel endpoint the simulator stages (safe-zone anchors,
        location center) must snap — crews visibly start on real roads."""
        g = _graph(key)
        loc = LOCATIONS[key]
        assert g.nearest_node(loc.center) is not None
        for z in loc.safe_zones:
            hit = g.nearest_node(z.loc)
            assert hit is not None, f"{key}: safe zone {z.zone_id} off-network"

    def test_route_between_wui_points(self, key):
        g = _graph(key)
        a, b = _wui_pair(g, key)
        r = route(g, a, b, CFG)
        assert r.on_roads, f"{key}: expected a road route between WUI points"
        hav = geo.haversine_m(a, b)
        plen = _poly_len_m(r.polyline)
        assert plen >= hav - 1e-6, f"{key}: polyline shorter than haversine?"
        assert plen <= 4.0 * hav, (
            f"{key}: polyline {plen:.0f} m > 4x haversine {hav:.0f} m")
        assert r.time_s >= hav / 29.0, f"{key}: faster than max class speed"
        # sane upper bound: whole distance at the slowest class + fire factor
        assert r.time_s <= r.dist_m * CFG.fire_traffic_factor / 7.0 + 1e-6
        assert r.polyline[0] == a and r.polyline[-1] == b
        assert route(g, a, b, CFG) == r                  # deterministic


# ---------------------------------------------------------------------------
# EXTENDED fire-zone road graphs (data/roads/<key>_ext.wgr; roads inspection
# 2026-09-25, tools/fetch_sim_roads.py --extended): every key with an
# extended tile gets a graph over the whole ~48 km tile so an extended
# session's escape legs past the 18 km core route on roads instead of
# straight chords. Two-tier (all classes in the standard bbox + major +
# unclassified outside) for the three urban tiles, full elsewhere.
# ---------------------------------------------------------------------------

def _ext_keys():
    from wgresponse.simui import DEFAULT_TILE_DIR
    return tuple(k for k in sorted(LOCATIONS)
                 if getattr(LOCATIONS[k], "extended_zone", True)
                 and (DEFAULT_TILE_DIR / f"{k}_ext.wgt").exists())


EXT_KEYS = _ext_keys()
_ext_graphs: dict = {}
_ext_load_s: dict = {}


def _ext_graph(key: str):
    gkey = f"{key}_ext"
    path = road_file(gkey)
    if not path.exists():
        pytest.skip(
            f"EXTENDED ROAD GRAPH MISSING: {path} — recreate it with `py -3 "
            f"tools/fetch_sim_roads.py --extended --only {key}` (LIVE "
            f"network). Until then an extended {key!r} session routes on the "
            f"standard ~18 km graph (loud state note) and legs past it are "
            f"straight chords.")
    if key not in _ext_graphs:
        t0 = time.perf_counter()
        _ext_graphs[key] = load_road_graph(gkey)
        _ext_load_s[key] = time.perf_counter() - t0
    return _ext_graphs[key]


def test_every_extended_tile_has_an_extended_road_graph():
    """The fetch covered every key with an extended tile (11 on 2026-09-25)
    and recorded it in its own provenance file."""
    import json
    from tools.fetch_sim_roads import PROVENANCE_EXT_PATH
    assert len(EXT_KEYS) >= 11
    missing = [k for k in EXT_KEYS if not road_file(f"{k}_ext").exists()]
    assert not missing, f"extended road graphs missing: {missing}"
    with open(PROVENANCE_EXT_PATH, encoding="utf-8") as f:
        prov = json.load(f)
    for k in EXT_KEYS:
        assert prov["locations"][k]["status"] in ("fetched", "cached"), k


@pytest.mark.parametrize("key", EXT_KEYS)
class TestExtendedRoadGraphs:
    def test_loads_fast_and_is_one_component(self, key):
        from tools.fetch_sim_roads import EXT_TWO_TIER
        g = _ext_graph(key)
        assert _ext_load_s[key] < 2.0, (
            f"{key}_ext: load took {_ext_load_s[key]:.2f} s (>2 s budget)")
        assert g.meta.get("key") == f"{key}_ext"
        assert g.meta.get("extended") is True
        assert g.meta.get("tiers") == (
            "two-tier" if key in EXT_TWO_TIER else "full")
        # the largest-component law: one connected component, no islands
        start = next(iter(g.nodes))
        seen = {start}
        stack = [start]
        while stack:
            n = stack.pop()
            for m, _ei in g.neighbors(n):
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        assert len(seen) == len(g.nodes)

    def test_reaches_well_beyond_the_standard_core(self, key):
        """The point of the graph: road nodes far outside the standard road
        bbox, and the core's own safe zones + center still snap."""
        from tools.fetch_sim_roads import bbox_for
        g = _ext_graph(key)
        loc = LOCATIONS[key]
        core = bbox_for(loc)
        outside = [p for p in g.nodes.values()
                   if not (core.south <= p.lat <= core.north
                           and core.west <= p.lon <= core.east)]
        assert len(outside) >= 200, (key, len(outside))
        far = max(geo.haversine_m(loc.center, p) for p in outside)
        assert far > 15_000.0, (key, far)
        assert g.nearest_node(loc.center) is not None
        for z in loc.safe_zones:
            assert g.nearest_node(z.loc) is not None, z.zone_id

    def test_keeps_the_core_detail_of_the_standard_graph(self, key):
        """Inside the core the extended graph carries the standard graph's
        detail (two-tier: every class inside the standard bbox): a seeded
        sample of standard-graph nodes finds an extended node within 50 m
        (OSM edits between the two fetch dates allowed at the margin)."""
        g = _ext_graph(key)
        std = _graph(key)
        rng = seeded_rng("roads-ext-core", key)
        ids = sorted(std.nodes)
        sample = [std.nodes[ids[rng.randrange(len(ids))]] for _ in range(300)]
        hit = sum(1 for p in sample if g.nearest_node(p, max_m=50.0) is not None)
        assert hit / len(sample) >= 0.9, (key, hit)

    def test_routes_from_the_core_to_the_outer_ring_on_roads(self, key):
        g = _ext_graph(key)
        loc = LOCATIONS[key]
        a = g.nodes[g.nearest_node(loc.center)[0]]
        b = max(g.nodes.values(), key=lambda p: (geo.haversine_m(loc.center, p),
                                                 p.lat, p.lon))
        r = route(g, a, b, CFG)
        assert r.on_roads, f"{key}_ext: core -> outer ring is not a road route"
        assert r.dist_m >= geo.haversine_m(a, b) - 1e-6


# ---------------------------------------------------------------------------
# ROADS ADHERENCE (FOUNDER ROUND 3 law 2): three-phase legs, spur metadata,
# cfg.road_spur_max_m cap, loud whole-leg chord. See roads.py SPUR METADATA.
# ---------------------------------------------------------------------------

def _cfg_with_cap(cap_m: float) -> ResponseConfig:
    cfg = ResponseConfig()
    cfg.road_spur_max_m = cap_m
    return cfg


def _flatten_segments(segments) -> list:
    """Reconstruct the flat polyline from segments per the documented rule:
    consecutive segments share their boundary vertex."""
    flat = list(segments[0].points)
    for seg in segments[1:]:
        assert seg.points[0] == flat[-1], "segments must share boundary vertex"
        flat.extend(seg.points[1:])
    return flat


def _segments_from_polyline_and_mask(poly, mask) -> list:
    """Rebuild (points, spur) groups from the flat polyline + per-edge mask by
    grouping maximal equal runs — the inverse of RoadRoute.spur_mask."""
    out = []
    i = 0
    while i < len(mask):
        j = i
        while j < len(mask) and mask[j] == mask[i]:
            j += 1
        out.append((tuple(poly[i:j + 1]), mask[i]))
        i = j
    return out


def test_off_network_endpoint_gets_spur_then_multi_vertex_road_middle():
    # endpoint ~100 m off-network -> a short spur segment (spur=True) then a
    # multi-vertex road middle (spur=False); flat polyline stays contiguous.
    g = _grid12()
    a = geo.destination(g.nodes[0], 270.0, 100.0)        # ~100 m west of n0
    b = g.nodes[3]                                        # exactly on n3
    r = route(g, a, b, CFG)
    assert r.on_roads
    assert r.polyline[0] == a and r.polyline[-1] == b     # back-compat endpoints
    # leading segment = the ONLY straight part at this end: a short spur
    assert isinstance(r.segments[0], RouteSegment)
    assert r.segments[0].kind == "spur" and r.segments[0].spur is True
    assert r.segments[0].points == (a, g.nodes[0])
    spur_len = geo.haversine_m(*r.segments[0].points)
    assert 90.0 < spur_len < 130.0                        # ~100 m connector
    # a real, multi-vertex road middle follows, flagged NOT a spur
    road = [s for s in r.segments if s.kind == "road"]
    assert len(road) == 1
    assert road[0].spur is False and len(road[0].points) >= 3
    # b sits on n3 -> NO trailing spur; kinds are only spur/road (no chord)
    assert r.segments[-1].kind == "road"
    assert {s.kind for s in r.segments} == {"spur", "road"}


def test_endpoint_exactly_on_node_has_no_spur():
    g = _grid12()
    r = route(g, g.nodes[0], g.nodes[3], CFG)             # both on nodes
    assert r.on_roads
    assert all(s.kind == "road" and s.spur is False for s in r.segments)
    assert r.spur_mask == [False] * (len(r.polyline) - 1)
    assert not any(r.spur_mask)                            # zero spur edges


def test_beyond_cap_endpoint_is_whole_leg_chord_with_loud_note(caplog):
    # endpoint ~5544 m off-network (paradise-style) with road_spur_max_m=2000
    # (the default) -> whole-leg chord, on_roads=False, loud note + warning.
    g = _grid12()
    far = geo.destination(g.nodes[8], 0.0, 5544.0)        # 5.5 km N of the top row
    assert CFG.road_spur_max_m == 2000.0
    assert g.nearest_node(far, CFG.road_spur_max_m) is None
    with caplog.at_level("WARNING", logger="wgresponse.roads"):
        r = route(g, g.nodes[3], far, CFG)
    assert r.on_roads is False
    assert r.polyline == [g.nodes[3], far]                # honest straight chord
    assert [s.kind for s in r.segments] == ["chord"]
    assert r.segments[0].spur is True
    assert r.segments[0].points == (g.nodes[3], far)
    assert r.spur_mask == [True]
    # loud note names the off-network distance (~5544 m) and the cap
    assert r.note is not None
    assert "off-network" in r.note and "554" in r.note and "2000" in r.note
    assert any(rec.levelname == "WARNING" and "fallback" in rec.getMessage()
               for rec in caplog.records)


def test_raised_cap_routes_paradise_point_with_a_long_spur():
    # road_spur_max_m raised to 6000 -> the 5544 m point now routes with a long
    # spur (spur=True), on_roads=True, no chord.
    g = _grid12()
    far = geo.destination(g.nodes[8], 0.0, 5544.0)
    r = route(g, g.nodes[3], far, _cfg_with_cap(6000.0))
    assert r.on_roads is True
    assert r.polyline[0] == g.nodes[3] and r.polyline[-1] == far
    spurs = [s for s in r.segments if s.kind == "spur"]
    assert len(spurs) == 1                                 # only the far end
    long_spur = spurs[0]
    assert long_spur.spur is True and long_spur.points[-1] == far
    assert 5000.0 < geo.haversine_m(*long_spur.points) < 6000.0
    assert any(s.kind == "road" for s in r.segments)
    assert all(s.kind != "chord" for s in r.segments)


def test_route_uses_cfg_road_spur_max_m_not_the_hardcoded_constant():
    # the cap is the LIVE config knob: same point, same graph, flag flips only
    # because road_spur_max_m moved across the endpoint's off-network distance.
    g = _grid12()
    off = geo.destination(g.nodes[8], 0.0, 3000.0)        # ~3.0 km off-network
    assert route(g, g.nodes[3], off, _cfg_with_cap(2000.0)).on_roads is False
    r_hi = route(g, g.nodes[3], off, _cfg_with_cap(4000.0))
    assert r_hi.on_roads is True
    assert any(s.kind == "spur" for s in r_hi.segments)


def test_segments_and_spur_mask_reconstruct_the_flat_polyline():
    g = _grid12()
    a = geo.destination(g.nodes[0], 200.0, 150.0)         # off-network BOTH ends
    b = geo.destination(g.nodes[3], 20.0, 180.0)
    r = route(g, a, b, CFG)
    assert r.on_roads
    # (1) segments flatten back to the EXACT flat polyline (back-compat field)
    assert _flatten_segments(r.segments) == r.polyline
    # (2) spur_mask is per-EDGE, aligned to the flat polyline
    assert len(r.spur_mask) == len(r.polyline) - 1
    # (3) rebuild segment groups from (polyline, spur_mask) — inverse holds
    assert (_segments_from_polyline_and_mask(r.polyline, r.spur_mask)
            == [(s.points, s.spur) for s in r.segments])
    # (4) the two ends are spurs, the middle is road
    assert r.segments[0].kind == "spur" and r.segments[-1].kind == "spur"
    assert any(s.kind == "road" for s in r.segments)
    # (5) mask flag equals 'is this a straight, off-network edge'
    assert r.spur_mask == [True] + [False] * (len(r.polyline) - 3) + [True]


def test_back_compat_polyline_is_full_ordered_contiguous_path():
    # a legacy consumer reading ONLY .polyline gets the full ordered a..b path
    # with no metadata knowledge required, no stutter, spur legs included.
    g = _grid12()
    a = geo.destination(g.nodes[0], 270.0, 120.0)
    b = geo.destination(g.nodes[3], 90.0, 140.0)
    r = route(g, a, b, CFG)
    assert r.polyline[0] == a and r.polyline[-1] == b
    assert len(r.polyline) >= 4                            # spur + road... + spur
    for p, q in zip(r.polyline, r.polyline[1:]):
        assert p != q                                     # contiguous, no dupes
    assert _flatten_segments(r.segments) == r.polyline    # metadata agrees


def test_no_graph_and_empty_graph_carry_single_chord_segment():
    a, b = _nl(0, 0), _nl(2, 3)
    for r in (route(None, a, b, CFG), route(RoadGraph({}, []), a, b, CFG)):
        assert r.on_roads is False
        assert r.polyline == [a, b]
        assert [s.kind for s in r.segments] == ["chord"]
        assert r.segments[0].spur is True and r.segments[0].points == (a, b)
        assert r.spur_mask == [True]
        assert r.note                                     # loud reason present


def test_disconnected_components_fallback_names_the_snapped_nodes():
    nodes = {0: _nl(0, 0), 1: _nl(0, 1), 2: _nl(2, 0), 3: _nl(2, 1)}
    g = RoadGraph(nodes, [RoadEdge(0, 1, "residential", 1000.0),
                          RoadEdge(2, 3, "residential", 1000.0)])
    r = route(g, nodes[0], nodes[3], CFG)
    assert r.on_roads is False
    assert [s.kind for s in r.segments] == ["chord"]
    assert r.note and "disconnected" in r.note


def test_spur_metadata_deterministic_across_reload(tmp_path):
    g = _grid12()
    a = geo.destination(g.nodes[0], 200.0, 150.0)
    b = geo.destination(g.nodes[3], 20.0, 180.0)
    r1 = route(g, a, b, CFG)
    save_road_graph(g, tmp_path / "grid12.wgr")
    g2 = load_road_graph("grid12", road_dir=tmp_path)
    r2 = route(g2, a, b, CFG)
    assert r2 == r1                                        # segments included
    assert [(s.points, s.kind, s.spur) for s in r2.segments] \
        == [(s.points, s.kind, s.spur) for s in r1.segments]


def test_real_graph_spur_smoke_boulder():
    """One real-graph smoke: a point nudged a few hundred m off the boulder_co
    network routes with a leading spur + real road middle, on_roads=True, and
    its spur metadata reconstructs the flat polyline. SKIP LOUDLY when the
    committed graph is missing (that failure must stay visible in pytest)."""
    key = "boulder_co"
    path = road_file(key)
    if not path.exists():
        pytest.skip(
            f"SIMULATOR ROAD GRAPH MISSING: {path} — the {key!r} Overpass "
            f"fetch failed or was never run. Recreate it with `py -3 "
            f"tools/fetch_sim_roads.py --only {key}` (LIVE network). Until then "
            f"roads.route falls back to STRAIGHT legs for this location.")
    g = load_road_graph(key)
    assert g is not None
    a, b = _wui_pair(g, key)
    na = g.nearest_node(a, max_m=1000.0)
    assert na is not None
    a_off = geo.destination(g.nodes[na[0]], 37.0, 250.0)  # ~250 m off a node
    r = route(g, a_off, b, CFG)
    assert r.on_roads
    assert r.polyline[0] == a_off and r.polyline[-1] == b
    assert _flatten_segments(r.segments) == r.polyline
    assert len(r.spur_mask) == len(r.polyline) - 1
    # the introduced off-network approach shows up as a leading spur segment
    assert r.segments[0].kind == "spur" and r.segments[0].points[0] == a_off
    assert any(s.kind == "road" for s in r.segments)      # real road middle
