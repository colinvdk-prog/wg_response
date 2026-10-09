"""tests/test_sim_escape_roads.py — SEM-10d ROAD-NETWORK ESCAPE
(contracts escape_road_network; founder "rework the crews fleeing",
2026-09-26; docs/review/escape_roads_2026-09-26.md).

A crew that must flee with no catalog zone lawfully reachable searches the
ROAD GRAPH (a time-dependent Dijkstra: burned samples make an edge
impassable, samples in the eroded-P10 imminent band cost exposure seconds)
and drives, roads end to end, to the node with the least exposure, then the
longest capped safety horizon, then the shortest drive. These tests build a
hand-made road graph on a controlled tile and paint the fire fields cell by
cell, so every rule is exercised exactly; a real-tile test checks the crew
actually drives the searched roads.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

from wgresponse import geo, roads
from wgresponse.contracts import LatLon, Raster, ResponseConfig, SafeZone
from wgresponse.roads import RoadEdge, RoadGraph
from wgresponse.simui import (
    DEFAULT_TILE_DIR,
    LOCATIONS,
    SimSession,
    _SimFire,
)
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

KEY = "boulder_co"
FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
        # a flat 45-min cold-black window: the painted "burned" strips below
        # are unambiguous whatever the synthetic fuel class is
        "transit_cold_black_by_class": False}
SPEED = 7.52          # the crews' escape ground speed (road / (circuity x fire))
LEAD = 900.0          # one 15-min tick of sweep lead
INF = math.inf


def _tile_dir(tmp_path):
    loc = LOCATIONS[KEY]
    bbox = geo.bbox_around([loc.center], pad_m=24_000.0 * 0.5)
    tile = SyntheticTerrain(seed="escape-roads", kind="grass").tile(bbox, 500.0)
    d = tmp_path / "tiles"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{KEY}.wgt"))
    return d


def _pt(bearing, km):
    return geo.destination(LOCATIONS[KEY].center, bearing, km * 1000.0)


#: the hand-built network around the tile centre C (node 0):
#: east 1-2-3 (1.5 / 3 / 4.5 km), west 4-5-6-7 (1.5 / 3 / 4.5 / 6 km),
#: north 8-9 (1.5 / 3 km). Edges carry a mid-point vertex so geometry is
#: exercised.
NODES = {0: (0, 0.0), 1: (90, 1.5), 2: (90, 3.0), 3: (90, 4.5),
         4: (270, 1.5), 5: (270, 3.0), 6: (270, 4.5), 7: (270, 6.0),
         8: (0, 1.5), 9: (0, 3.0)}
EDGES = [(0, 1), (1, 2), (2, 3), (0, 4), (4, 5), (5, 6), (6, 7), (0, 8), (8, 9)]


def _graph():
    nodes = {i: _pt(b, km) for i, (b, km) in NODES.items()}
    edges = []
    for u, v in EDGES:
        a, b = nodes[u], nodes[v]
        mid = LatLon((a.lat + b.lat) / 2.0 + 0.0004, (a.lon + b.lon) / 2.0)
        L = geo.haversine_m(a, mid) + geo.haversine_m(mid, b)
        edges.append(RoadEdge(u=u, v=v, highway_class="residential",
                              length_m=L, geometry=(mid,)))
    return RoadGraph(nodes, edges, {"key": "escape_test"})


class _GraphRoads:
    """The real wgresponse.roads seam over an in-test graph."""

    def __init__(self, g):
        self._g = g

    def load_road_graph(self, key):
        return self._g

    route = staticmethod(roads.route)
    route_on_path = staticmethod(roads.route_on_path)
    route_candidates = staticmethod(roads.route_candidates)


def _session(tmp_path, **cfg):
    g = _graph()
    s = SimSession(KEY, 4, 1, seed="escape-roads", tile_dir=_tile_dir(tmp_path),
                   roads_module=_GraphRoads(g), cfg_overrides=dict(FAST, **cfg))
    # catalog zones on the tile, 8 km N and S, both inside the band below
    # (unsafe) — so every escape here is an AREA escape
    zones = (SafeZone("esc-z1", _pt(0, 8.0), "north zone"),
             SafeZone("esc-z2", _pt(180, 8.0), "south zone"))
    s._loc = dataclasses.replace(s._loc, safe_zones=zones)
    s._zone_by_id = {z.zone_id: z for z in zones}
    return s, g


def _fire(s, paint_nom=(), paint_p10=()):
    """Inject one fire whose nominal member / eroded-P10 fields are painted
    cell by cell: paint_* = [(center LatLon, radius_m, value_s)]. Unpainted
    cells are UNREACHED. The zones are always painted imminent."""
    spec = s._tile.spec
    n = spec.nrows * spec.ncols
    nom = Raster(spec, [INF] * n)
    p10 = Raster(spec, [INF] * n)
    now = s._now_s
    zone_paint = [(z.loc, 600.0, now - 3600.0) for z in s._loc.safe_zones]
    for ras, paints in ((nom, paint_nom), (p10, list(paint_p10) + zone_paint)):
        for center, radius, val in paints:
            for r in range(spec.nrows):
                for c in range(spec.ncols):
                    if geo.haversine_m(center, geo.rc_to_latlon(spec, r, c)) <= radius:
                        i = r * spec.ncols + c
                        ras.data[i] = min(ras.data[i], val)
    ens = type("Ens", (), {})()
    ens.members = [nom]
    ens.p10 = p10
    f = _SimFire(incident_id="F1", origin=_pt(90, 20.0), t_ignite_s=now,
                 intensity="high", wind_dir_deg=270.0, ens=ens)
    f._eroded_src = ens
    f._eroded_p10 = p10
    s._fires = {"F1": f}
    s._fire_order = ["F1"]
    return f


def _node_of(g, loc):
    hit = g.nearest_node(loc, max_m=1.0)
    return None if hit is None else hit[0]


def _route_nodes(g, rr):
    """Graph nodes the route's polyline passes through, in order."""
    out = []
    for p in rr.polyline:
        n = _node_of(g, p)
        if n is not None and (not out or out[-1] != n):
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# the knob
# ---------------------------------------------------------------------------

def test_knob_default_off_and_simulator_arms_it(tmp_path):
    assert ResponseConfig().escape_road_network is False
    s, _g = _session(tmp_path)
    assert s._cfg.escape_road_network is True           # sim default
    off, _g2 = _session(tmp_path, escape_road_network=False)
    assert off._cfg.escape_road_network is False        # explicit wins
    assert ResponseConfig().escape_road_horizon_cap_h > 0


def test_knob_off_never_searches_and_matches_legacy(tmp_path, monkeypatch):
    """Knob OFF: the road search / choice never run, no path is pinned, and
    _best_escape + _route_metrics return exactly what the legacy code (the
    verbatim pre-change bodies, reproduced here) returns."""
    s, g = _session(tmp_path, escape_road_network=False)
    now = s._now_s
    _fire(s, paint_p10=[(_pt(90, 1.5), 400.0, now + 60.0)])

    def boom(*a, **k):
        raise AssertionError("road search ran with the knob off")
    monkeypatch.setattr(SimSession, "_road_escape_search", boom)
    monkeypatch.setattr(SimSession, "_net_escape_choice", boom)
    start = g.nodes[0]
    legacy = _legacy_best_escape(s, start, now, SPEED, LEAD)
    assert s._best_escape(start, now, SPEED, LEAD) == legacy
    assert s._escape_pins == {} and s._net_escape == {}
    for tgt in (g.nodes[3], g.nodes[7], _pt(45, 5.0), s._loc.safe_zones[0].loc):
        assert (s._route_metrics(start, tgt, now, SPEED, LEAD)
                == _legacy_route_metrics(s, start, tgt, now, SPEED, LEAD))


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------

def test_destination_is_a_road_node_and_the_drive_is_roads_end_to_end(tmp_path):
    s, g = _session(tmp_path)
    now = s._now_s
    _fire(s, paint_p10=[(_pt(90, 1.5), 400.0, now + 60.0)])
    start = LatLon(g.nodes[0].lat + 0.001, g.nodes[0].lon)   # ~110 m off-road
    loc, zid = s._best_escape(start, now, SPEED, LEAD)
    assert zid == "escape" and s._escape_kind_last == "network"
    node = _node_of(g, loc)
    assert node is not None and g.nodes[node] == loc          # ON a node
    assert geo.haversine_m(start, loc) >= 2_000.0
    rr = s._leg_route(start, loc)                              # the pinned path
    assert rr is s._escape_pins[(start.lat, start.lon, loc.lat, loc.lon)]
    kinds = [seg.kind for seg in rr.segments]
    assert kinds == ["spur", "road"]          # origin spur, then roads to the end
    assert rr.polyline[-1] == loc and rr.on_roads
    # the search's own numbers are exactly the SEM-10 route law's
    found = s._road_escape_search(start, now, SPEED, LEAD)
    b, e, _w, t, dm = s._route_metrics(start, loc, now, SPEED, LEAD)
    assert not b
    assert e == found["exposure_s"] and t == found["total_s"] and dm == found["dm_s"]


def test_burned_edges_are_never_used(tmp_path):
    """The west branch is the nearest way out but burns as the crew would
    pass (nominal front inside the cold-black window at its pass time): the
    search routes east and no burned sample is ever on the chosen path."""
    s, g = _session(tmp_path)
    now = s._now_s
    # east: a mild exposure patch (still a candidate); west: burning NOW
    _fire(s, paint_nom=[(_pt(270, 1.5), 450.0, now + 1.0)],
          paint_p10=[(_pt(90, 1.5), 400.0, now + 60.0),
                     (_pt(270, 1.5), 450.0, now - 600.0),
                     (_pt(0, 1.5), 450.0, now + 60.0)])
    found = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    assert found is not None
    path = _route_nodes(g, found["route"])
    assert 4 not in path and 5 not in path
    b, _e, _w, _t, _dm = s._route_metrics(g.nodes[0], found["loc"], now,
                                          SPEED, LEAD)
    assert b is False
    # the unconstrained fastest road route WOULD cross the burn to node 5
    b5, *_ = s._route_metrics(g.nodes[0], g.nodes[5], now, SPEED, LEAD)
    assert b5 is True


def test_exposure_minimal_choice(tmp_path):
    """East is a straight 3 km run through the imminent band; west is clean
    but reaches its first >= 2 km node at 3 km too and the far one at 6 km:
    zero exposure wins over any exposed alternative."""
    s, g = _session(tmp_path)
    now = s._now_s
    _fire(s, paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],       # north burned
          paint_p10=[(_pt(90, 1.5), 500.0, now + 60.0)])         # east exposed
    found = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    assert found["exposure_s"] == 0.0
    assert _node_of(g, found["loc"]) in (5, 6, 7)
    b, e, *_ = s._route_metrics(g.nodes[0], g.nodes[2], now, SPEED, LEAD)
    assert not b and e > 0.0                  # the east option was exposed


def test_horizon_preference_and_cap(tmp_path):
    """No exposure anywhere. West node 5 (3 km) and east nodes 2/3 are
    reached by the P10 front 3 h out; west node 6 (4.5 km) never: the
    long-safe node wins over the nearer marginal ones. Then with every node
    beyond the horizon cap (30 h vs the 24 h default), the NEAREST wins."""
    s, g = _session(tmp_path)
    now = s._now_s
    short = now + 3 * 3600.0
    _fire(s, paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],
          paint_p10=[(g.nodes[2], 400.0, short), (g.nodes[3], 400.0, short),
                     (g.nodes[5], 400.0, short)])
    found = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    assert found["exposure_s"] == 0.0
    assert _node_of(g, found["loc"]) == 6
    assert math.isinf(found["horizon_s"])
    # every node beyond the cap: the nearest (3 km) wins over the 4.5 km one
    s2, g2 = _session(tmp_path)
    far = s2._now_s + 30 * 3600.0
    _fire(s2, paint_nom=[(_pt(0, 1.5), 450.0, s2._now_s + 1.0)],
          paint_p10=[(g2.nodes[2], 400.0, far), (g2.nodes[3], 400.0, far),
                     (g2.nodes[5], 400.0, far)])
    near = s2._road_escape_search(g2.nodes[0], s2._now_s, SPEED, LEAD)
    assert _node_of(g2, near["loc"]) in (2, 5)
    assert abs(geo.haversine_m(g2.nodes[0], near["loc"]) - 3000.0) < 50.0


def test_cornered_falls_back_to_the_ray_escape_loudly(tmp_path):
    """Every road out burns: NO passable road escape — the legacy ray logic
    decides (kind 'ray_fallback') and the sweep says so in the notes."""
    s, g = _session(tmp_path)
    now = s._now_s
    burning = [(_pt(b, 1.5), 450.0, now + 1.0) for b in (0, 90, 270)]
    _fire(s, paint_nom=burning,
          paint_p10=[(c, r, now - 600.0) for c, r, _v in burning])
    assert s._road_escape_search(g.nodes[0], now, SPEED, LEAD) is None
    loc, zid = s._best_escape(g.nodes[0], now, SPEED, LEAD)
    assert s._escape_kind_last == "ray_fallback"
    assert (loc, zid) == _legacy_best_escape(s, g.nodes[0], now, SPEED, LEAD)
    assert s._escape_pins == {}
    # the sweep: a crew standing at the centre, imminent, must flee
    cid = next(iter(s._crew_sims))
    cs = s._crew_sims[cid]
    cs.pos = g.nodes[0]
    ens = s._fires["F1"].ens
    ens.p10.data[_cell(s, g.nodes[0])] = now          # its own cell imminent
    s._crew_safety_sweep(lead_s=LEAD)
    notes = [n for n in s._notes if "road-network escape" in n]
    assert notes and "no passable road leads" in notes[0]


def _cell(s, p):
    spec = s._tile.spec
    r, c = geo.latlon_to_rc(spec, p)
    return r * spec.ncols + c


def test_search_is_deterministic(tmp_path):
    s, g = _session(tmp_path)
    now = s._now_s
    paint = dict(paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],
                 paint_p10=[(_pt(90, 1.5), 500.0, now + 60.0)])
    _fire(s, **paint)
    a = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    s._esc_edge_cache.clear()
    b = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    s2, g2 = _session(tmp_path)
    _fire(s2, **paint)
    c = s2._road_escape_search(g2.nodes[0], now, SPEED, LEAD)
    for x in (b, c):
        assert x["node"] == a["node"] and x["key"] == a["key"]
        assert x["route"].polyline == a["route"].polyline


def test_sweep_pins_the_path_and_the_crew_drives_it(tmp_path):
    """End to end through the sweep and a tick of kinematics: the crew is
    relocated to a road node, drives the pinned searched path (every driven
    point lies on it), and the sweep re-judges its REMAINING leg."""
    s, g = _session(tmp_path)
    now = s._now_s
    _fire(s, paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],
          paint_p10=[(_pt(90, 1.5), 500.0, now + 60.0),
                     (g.nodes[0], 400.0, now + 600.0)])      # the crew's cell
    cid = next(iter(s._crew_sims))
    cs = s._crew_sims[cid]
    cs.pos = g.nodes[0]
    cs._mode = "idle"
    s._crew_safety_sweep(lead_s=LEAD)
    assert cs.egress_zone_id == "escape" and s._net_escape[cid] == cs.egress_loc
    assert any("area escape, by road" in n for n in s._notes)
    rr = s._leg_route(cs.pos, cs.egress_loc)
    assert rr is not None and rr.polyline[-1] == cs.egress_loc
    served = []
    cs.advance(now, now + 300.0, served)
    for p in cs.driven:
        assert min(geo.haversine_m(p, q) for q in _dense(rr.polyline)) < 30.0
    # the remaining leg is what the sweep judges (not a fresh fastest route)
    m = s._crew_target_metrics(cs, now + 300.0, LEAD)
    assert m[0] is False and m[4] >= 0.0


def _dense(poly, step=10.0):
    out = []
    for a, b in zip(poly, poly[1:]):
        n = max(1, int(geo.haversine_m(a, b) // step))
        for k in range(n + 1):
            f = k / n
            out.append(LatLon(a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f))
    return out


def test_proactive_decamp_takes_a_lawful_durable_road_node(tmp_path):
    """SEM-10e over the road network: an idle crew whose spot the P10 front
    reaches inside the proactive window, with no catalog zone left, decamps
    along a FULLY LAWFUL road path to a durable node; exposed or burned
    paths are never taken by this voluntary move."""
    s, g = _session(tmp_path, escape_proactive_lead_min=120.0)
    now = s._now_s
    _fire(s, paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],        # north burns
          paint_p10=[(_pt(90, 1.5), 500.0, now + 60.0),          # east exposed
                     (g.nodes[0], 400.0, now + 2 * 3600.0)])     # 2 h out
    cid = next(iter(s._crew_sims))
    cs = s._crew_sims[cid]
    cs.pos = g.nodes[0]
    cs._mode = "idle"
    s._crew_safety_sweep(lead_s=LEAD)
    assert s._relocating.get(cid) == "proactive_escape"
    assert cs.egress_zone_id == "escape"
    assert _node_of(g, cs.egress_loc) in (5, 6, 7)                # west, clean
    b, e, w, _t, dm = s._route_metrics(cs.pos, cs.egress_loc, now, SPEED, LEAD)
    assert not b and e == 0.0 and w >= 0.0 and dm >= 0.0
    assert any("proactive escape by road" in n for n in s._notes)
    # every road out exposed or burning: no voluntary move at all
    s2, g2 = _session(tmp_path, escape_proactive_lead_min=120.0)
    _fire(s2, paint_nom=[(_pt(0, 1.5), 450.0, now + 1.0)],
          paint_p10=[(_pt(90, 1.5), 500.0, now + 60.0),
                     (_pt(270, 1.5), 500.0, now + 60.0),
                     (g2.nodes[0], 400.0, now + 2 * 3600.0)])
    assert s2._road_escape_search(g2.nodes[0], now, SPEED, LEAD,
                                  lawful_only=True, min_p10_s=now) is None


def _scripted_session(**cfg):
    """tests/test_escape_proactive.py's scripted world: the real boulder_co
    tile + road graph, no fire, _crew_exposure replaced so each catalog
    zone's cell carries a scripted P10 and every other point is `elsewhere`."""
    return SimSession("boulder_co", 2, 1, seed="pro-test", cfg_overrides=cfg)


def _script(s, zone_p10, elsewhere=(INF, INF)):
    zones = {(round(z.loc.lat, 6), round(z.loc.lon, 6)): z.zone_id
             for z in s._loc.safe_zones}

    def fake(pos):
        zid = zones.get((round(pos.lat, 6), round(pos.lon, 6)))
        if zid is not None:
            return INF, zone_p10.get(zid, INF)
        return elsewhere
    s._crew_exposure = fake


def test_proactive_counterpart_no_durable_zone_decamps_by_road():
    """The zone-only picker's 'no durable zone -> None' world
    (test_escape_proactive.py, now pinned with the knob off): every zone
    inside the proactive horizon, every other point safe. Armed, the picker
    takes a FULLY LAWFUL road path to a durable road node >= 2 km out — and
    only when no zone rule yields a move."""
    if not (DEFAULT_TILE_DIR.parent / "roads" / "boulder_co.wgr").exists():
        pytest.skip("LOUD SKIP: boulder_co road graph not cached")
    s = _scripted_session()
    cs = next(iter(s._crew_sims.values()))
    now = s._now_s
    _script(s, {z.zone_id: now + 1800.0 for z in s._loc.safe_zones})
    pick = s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0, 7200.0)
    assert pick is not None and pick.zone_id == "escape"
    assert s._road_graph.nearest_node(pick.loc, max_m=0.01) is not None
    assert geo.haversine_m(cs.pos, pick.loc) >= 2_000.0
    b, e, w, _t, dm = s._route_metrics(cs.pos, pick.loc, now, cs.speed, 900.0)
    assert not b and e == 0.0 and dm >= 0.0
    # the sweep counterpart of test_sweep_holds_when_no_durable_exit
    s2 = _scripted_session(escape_proactive_lead_min=120.0)
    cs2 = next(iter(s2._crew_sims.values()))
    z1, z2, z3 = sorted(s2._loc.safe_zones, key=lambda z: z.zone_id)
    _script(s2, {z1.zone_id: s2._now_s + 2 * 3600.0,
                 z2.zone_id: s2._now_s + 1800.0,
                 z3.zone_id: s2._now_s + 1800.0})
    s2._crew_safety_sweep(lead_s=900.0)
    assert cs2._mode == "egress" and cs2.egress_zone_id == "escape"
    assert s2._relocating.get(cs2.crew.crew_id) == "proactive_escape"


def test_proactive_counterpart_every_road_exposed_holds():
    """Every point off the zones inside the eroded-P10 band (and long burned):
    no lawful road path exists, so the armed picker still returns None — a
    voluntary move never buys exposure (the scripted _crew_exposure is what
    the search judges).

    Pinned with old_black_refuge OFF: "long burned" (4 h) is OLD BLACK, and
    under the founder's 2026-09-26 ruling old black is a refuge that costs
    no exposure, so the armed simulator decamps over it; with the band on
    UNBURNED ground it still holds — tests/test_sim_old_black.py::test_armed_
    proactive_road_decamp_rides_old_black_not_the_band."""
    if not (DEFAULT_TILE_DIR.parent / "roads" / "boulder_co.wgr").exists():
        pytest.skip("LOUD SKIP: boulder_co road graph not cached")
    s = _scripted_session(old_black_refuge=False)
    cs = next(iter(s._crew_sims.values()))
    now = s._now_s
    _script(s, {z.zone_id: now + 1800.0 for z in s._loc.safe_zones},
            elsewhere=(now - 4 * 3600.0, now - 4 * 3600.0))
    assert s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0,
                                    7200.0) is None


# ---------------------------------------------------------------------------
# real tile + real graph
# ---------------------------------------------------------------------------

def test_real_tile_escape_drives_roads_to_a_node():
    """glenwood_springs_co, the roads-offroad probe's session (40 homes, 6
    in-zone crews + 1 h + 3 h reinforcements, page defaults, HIGH 7 km
    upwind) — where the ray escape drove 6 km straight spurs into open
    ground. With the simulator defaults each area escape ends ON a road
    node, its pinned path has no chord and no destination spur, and on the
    next tick the crew drives exactly that path."""
    key = "glenwood_springs_co"
    if not (DEFAULT_TILE_DIR / f"{key}.wgt").exists() or \
            not (DEFAULT_TILE_DIR.parent / "roads" / f"{key}.wgr").exists():
        pytest.skip("LOUD SKIP: glenwood_springs_co tile/road graph not cached")
    loc = LOCATIONS[key]
    s = SimSession(key, 40, 6, seed="roads-probe", n_crews_1h=1, n_crews_3h=1,
                   cfg_overrides={"spotting_enabled": True,
                                  "grass_ros_floor_frac": 0.10,
                                  # the legacy presets this probe was derived
                                  # under (city weather: 2026-10-09)
                                  "sim_weather_profile": "generic",
                                  "dispatch_late_shift": True,
                                  "dispatch_plan_on_roads": True,
                                  "dispatch_hold_h": 24.0, "deploy_min": 15.0,
                                  "dispatch_sequence": "auto"})
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 7000.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    g = s._road_graph
    checked = 0
    pending = {}
    for _ in range(8):
        s.tick()
        for cid, rr in pending.items():                 # last tick's escapes
            cs = s._crew_sims[cid]
            dense = _dense(rr.polyline, step=5.0)
            for p in cs.driven:
                assert min(geo.haversine_m(p, q) for q in dense) < 15.0
            checked += 1
        pending = {}
        for cid, cs in s._crew_sims.items():
            if (cs.egress_zone_id == "escape" and cs._leg is None
                    and s._net_escape.get(cid) == cs.egress_loc):
                rr = s._leg_route(cs.pos, cs.egress_loc)      # the pin
                assert g.nearest_node(cs.egress_loc, max_m=0.01) is not None
                kinds = [seg.kind for seg in rr.segments]
                assert "chord" not in kinds and kinds[-1] == "road"
                assert rr.polyline[-1] == cs.egress_loc
                pending[cid] = rr
    assert checked >= 2, "scenario no longer produces road-network escapes"


# ---------------------------------------------------------------------------
# legacy references (verbatim pre-change bodies) for the knob-off identity
# ---------------------------------------------------------------------------

def _legacy_route_metrics(s, a, b, now, speed_mps, lead_s):
    buffer_s = s._cfg.egress_buffer_min * 60.0
    pts = []
    rr = s._leg_route(a, b)
    if rr is not None and getattr(rr, "segments", None):
        for seg in rr.segments:
            for p in seg.points:
                q = p if isinstance(p, LatLon) else LatLon(p[0], p[1])
                if not pts or (pts[-1].lat, pts[-1].lon) != (q.lat, q.lon):
                    pts.append(q)
    if len(pts) < 2:
        pts = [a, b]
    out = [(pts[0], 0.0)]
    t = 0.0
    step = 275.0
    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        d = geo.haversine_m(p0, p1)
        if d <= 0.0:
            continue
        n_sub = max(1, int(d // step))
        for k in range(1, n_sub + 1):
            f = k / n_sub
            q = LatLon(p0.lat + (p1.lat - p0.lat) * f,
                       p0.lon + (p1.lon - p0.lon) * f)
            t += (d / n_sub) / max(speed_mps, 0.1)
            out.append((q, t))
    burned = False
    exposure = 0.0
    worst = math.inf
    dest_margin = math.inf
    prev_dt = 0.0
    for q, dt in out:
        pass_t = now + dt
        nom, p10 = s._crew_exposure(q)
        if ((not math.isinf(nom)) and nom <= pass_t and dt > 0.0
                and (pass_t - nom) < s._cold_black_pass_s(q)):
            burned = True
        m = math.inf
        if not math.isinf(p10):
            m = p10 - (pass_t + buffer_s + lead_s)
            worst = min(worst, m)
            if m < 0.0:
                exposure += max(0.0, dt - prev_dt)
        dest_margin = m
        prev_dt = dt
    total = out[-1][1] if out else 0.0
    return burned, exposure, worst, total, dest_margin


def _legacy_best_escape(s, pos, now, speed_mps, lead_s):
    zones = list(s._loc.safe_zones)
    safe_zones, fail_closed = s._currently_safe_zones(now, lead_s=lead_s)
    if not fail_closed:
        z = s._reachable_zone(pos, safe_zones, now, speed_mps, lead_s)
        if z is not None:
            return z.loc, z.zone_id
    cands = []
    for z in sorted(zones, key=lambda z: z.zone_id):
        burned, expo, _w, total, dm = _legacy_route_metrics(
            s, pos, z.loc, now, speed_mps, lead_s)
        if not burned:
            cands.append((dm, expo, total, 0, z.zone_id, z.loc))
    for _bearing, tgt in s._escape_ray_targets(pos, now, speed_mps):
        burned, expo, _w, total, dm = _legacy_route_metrics(
            s, pos, tgt, now, speed_mps, lead_s)
        if not burned:
            cands.append((dm, expo, total, 1, "escape", tgt))
    safe_dest = [c for c in cands if c[0] >= 0.0]
    if safe_dest:
        _dm, _e, _t, _k, zid, loc = min(
            safe_dest, key=lambda c: (c[1], c[2], c[3], c[4]))
        return loc, zid
    if cands:
        _dm, _e, _t, _k, zid, loc = min(
            cands, key=lambda c: (-c[0], c[1], c[2], c[3], c[4]))
        return loc, zid
    zfb = s._nearest_zone(pos, zones)
    return zfb.loc, zfb.zone_id
