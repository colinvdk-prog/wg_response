"""Vail -> Aspen corridor program (founder 2026-09-23 "build the response
program from Vail to Aspen"; docs/vail_aspen_response_program.md): the six
community presets, their sync with the tile tool, the simulator page, the
watcher AOI file, and (when fetched) the corridor road graph.

Plus the WHOLE-VALLEY preset eagle_vail_co (founder 2026-09-25: "expand the
area for Vail so that the entire Eagle to Vail corridor can be modeled at
once"): one ~60x60 km @ 200 m tile from Gypsum to East Vail, its road graph
over the whole tile, road-proximity client placement, the loud no-extended
fallback, and the founder's single-session scenario (fire near Eagle, crews
at the Eagle base, homes from Edwards to Vail)."""
from __future__ import annotations

import pathlib
import time

import pytest

from tools.fetch_sim_tiles import LOCATIONS as TILE_LOCATIONS
from wgresponse import cli, geo, roads
from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.simui import LOCATIONS

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORRIDOR = ("vail_co", "beaver_creek_co", "eagle_co", "glenwood_springs_co",
            "basalt_carbondale_co", "aspen_co")
EAGLE_VALLEY = ("vail_co", "beaver_creek_co", "eagle_co", "glenwood_springs_co")
ROARING_FORK = ("basalt_carbondale_co", "aspen_co")


def test_corridor_presets_registered_and_in_sync_with_tile_tool():
    """KEEP-IN-SYNC law: simui.LOCATIONS and tools/fetch_sim_tiles.LOCATIONS
    carry the same key / name / center for every corridor community."""
    tile = {k: (name, lat, lon) for k, name, lat, lon in TILE_LOCATIONS}
    for key in CORRIDOR:
        loc = LOCATIONS[key]
        assert key in tile, key
        name, lat, lon = tile[key]
        assert loc.name == name
        assert loc.center == LatLon(lat, lon)
        assert loc.tile_path == f"data/tiles/{key}.wgt"
        assert loc.bounds_poly == ()          # whole-band placement, no polygon
        assert 2 <= len(loc.safe_zones) <= 3


def test_corridor_hazard_winds_by_valley():
    for key in EAGLE_VALLEY:
        assert LOCATIONS[key].hazard_wind_dir_deg == 240.0, key   # WSW, E-W valley
    for key in ROARING_FORK:
        assert LOCATIONS[key].hazard_wind_dir_deg == 225.0, key   # SW


def test_corridor_geometry_vail_to_aspen():
    vail, aspen = LOCATIONS["vail_co"].center, LOCATIONS["aspen_co"].center
    assert 55_000.0 < geo.haversine_m(vail, aspen) < 75_000.0   # ~64 km chord
    # the Eagle base sits between the two valleys' communities
    eagle = LOCATIONS["eagle_co"].center
    assert geo.haversine_m(eagle, vail) < geo.haversine_m(aspen, vail)


def test_corridor_aoi_file_covers_every_community_and_zone():
    aoi, zones, desc = cli._parse_aoi(str(ROOT / "data" / "aoi_vail_aspen.json"))
    assert "vail_aspen_corridor" in desc
    ids = {z.zone_id for z in zones}
    n = 0
    for key in CORRIDOR:
        loc = LOCATIONS[key]
        assert aoi.contains(loc.center), key
        for z in loc.safe_zones:
            assert z.zone_id in ids, z.zone_id
            assert aoi.contains(z.loc), z.zone_id
            n += 1
    assert len(zones) == n
    # every zone is labelled unverified -- planning-grade until driven
    for z in zones:
        assert z.name.startswith("UNVERIFIED"), z.zone_id


def test_simulator_page_lists_corridor_presets():
    html = (ROOT / "console" / "simulator.html").read_text(encoding="utf-8")
    for key in CORRIDOR:
        loc = LOCATIONS[key]
        assert f'<option value="{key}">{loc.name}</option>' in html, key
        assert f'{key}: ["{loc.name}", {int(loc.hazard_wind_dir_deg)}]' in html, key


def test_corridor_road_graph_routes_eagle_base_to_aspen():
    """The corridor drive-time graph (scratch_review/corridor_roads.py):
    Eagle base -> Aspen must be a real road route of I-70/SH-82 length."""
    g = roads.load_road_graph("corridor_vail_aspen")
    if g is None:
        pytest.skip("corridor road graph not fetched "
                    "(py -3 scratch_review/corridor_roads.py)")
    base = LOCATIONS["eagle_co"].safe_zones[0].loc
    r = roads.route(g, base, LOCATIONS["aspen_co"].center, ResponseConfig())
    assert r.on_roads
    assert 90_000.0 < r.dist_m < 200_000.0, r.dist_m


# ---------------------------------------------------------------------------
# THE WHOLE-VALLEY PRESET eagle_vail_co (founder 2026-09-25)
# ---------------------------------------------------------------------------

WHOLE = "eagle_vail_co"
#: valley towns the whole-valley road graph must reach (lat, lon)
VALLEY_TOWNS = {
    "Gypsum": (39.6469, -106.9517),
    "Eagle": (39.6553, -106.8287),
    "Wolcott": (39.7033, -106.6795),
    "Edwards": (39.6450, -106.5942),
    "Beaver Creek village": (39.6042, -106.5165),
    "Avon": (39.6314, -106.5222),
    "Minturn": (39.5864, -106.4306),
    "Vail Village": (39.6403, -106.3742),
}


def _whole_tile_or_skip():
    path = ROOT / "data" / "tiles" / f"{WHOLE}.wgt"
    if not path.exists():
        pytest.skip(f"LOUD SKIP: {path} missing — fetch it with `py -3 "
                    f"tools/fetch_sim_tiles.py --only {WHOLE}` (LIVE LFPS); "
                    f"the whole-valley preset runs on its synthetic fallback "
                    f"until then")
    return path


def _whole_graph_or_skip():
    g = roads.load_road_graph(WHOLE)
    if g is None:
        pytest.skip(f"LOUD SKIP: data/roads/{WHOLE}.wgr missing — fetch it "
                    f"with `py -3 tools/fetch_sim_roads.py --only {WHOLE}` "
                    f"(LIVE Overpass); crews fall back to straight legs")
    return g


def test_whole_valley_in_sync_with_the_tile_tool():
    """KEEP-IN-SYNC law, per-location geometry edition: name / center match
    the tile tool; the registry geometry IS the ladder rung that won the
    fetch (primary, no step-down on 2026-09-25) and the saved tile's header;
    extended_zone mirrors NO_EXTENDED."""
    from tools.fetch_sim_tiles import (NO_EXTENDED, geometry_ladder,
                                       saved_geometry, tile_path)
    tile = {k: (name, lat, lon) for k, name, lat, lon in TILE_LOCATIONS}
    loc = LOCATIONS[WHOLE]
    name, lat, lon = tile[WHOLE]
    assert loc.name == name == "Eagle → Vail corridor, CO"
    assert loc.center == LatLon(lat, lon)
    assert loc.tile_path == f"data/tiles/{WHOLE}.wgt"
    assert geometry_ladder(WHOLE)[0] == (loc.tile_half_m, loc.tile_cell_m)
    assert (WHOLE in NO_EXTENDED) and loc.extended_zone is False
    geom = saved_geometry(tile_path(WHOLE))
    if geom is not None:
        assert geom == (loc.tile_half_m, loc.tile_cell_m), geom
    # the per-community presets it spans are untouched (grid results pin them)
    for key in ("vail_co", "beaver_creek_co", "eagle_co"):
        other = LOCATIONS[key]
        assert (other.tile_half_m, other.tile_cell_m) == (9_000.0, 90.0)
        assert other.extended_zone is True and other.place_near_road_m == 0.0


def test_whole_valley_zones_are_the_corridor_interchanges():
    """One source of truth per interchange: the six anchors are exactly the
    planning-grade I-70 nodes the per-community presets already carry."""
    known = {z.loc: z.name for key in ("vail_co", "beaver_creek_co", "eagle_co")
             for z in LOCATIONS[key].safe_zones}
    zones = LOCATIONS[WHOLE].safe_zones
    assert len(zones) == 6
    for z in zones:
        assert z.loc in known, z.zone_id
        assert known[z.loc] == z.name, z.zone_id
    assert LOCATIONS[WHOLE].hazard_wind_dir_deg == 240.0      # Eagle Valley WSW


def test_simulator_page_lists_whole_valley_preset():
    html = (ROOT / "console" / "simulator.html").read_text(encoding="utf-8")
    loc = LOCATIONS[WHOLE]
    assert f'<option value="{WHOLE}">{loc.name}</option>' in html
    assert f'{WHOLE}: ["{loc.name}", {int(loc.hazard_wind_dir_deg)}]' in html


def test_whole_valley_road_bbox_covers_the_tile_and_legacy_bboxes_hold():
    """Road BBOX LAW with per-location half-width: the whole ~60 km tile
    (plus zone pads) for the corridor; every other key keeps the 9 km law
    byte-for-byte — its recorded provenance bbox is reproduced exactly."""
    import json

    from tools.fetch_sim_roads import PROVENANCE_PATH, bbox_for
    from wgresponse.simui import central_window_bbox
    loc = LOCATIONS[WHOLE]
    b = bbox_for(loc)
    tile_box = central_window_bbox(loc.center, half_m=loc.tile_half_m)
    assert b.west <= tile_box.west and b.east >= tile_box.east
    assert b.south <= tile_box.south and b.north >= tile_box.north
    assert b.west < -106.96 and b.east > -106.28, (b.west, b.east)
    with open(PROVENANCE_PATH, "r", encoding="utf-8") as f:
        recorded = json.load(f)["locations"]
    for key, other in LOCATIONS.items():
        rec = recorded.get(key)
        if rec is None:
            continue
        ob = bbox_for(other)
        assert rec["bbox_wsen"] == [round(v, 6) for v in
                                    (ob.west, ob.south, ob.east, ob.north)], key


def test_whole_valley_road_graph_covers_zones_and_towns():
    g = _whole_graph_or_skip()
    t0 = time.perf_counter()
    roads.load_road_graph(WHOLE)
    assert time.perf_counter() - t0 < 2.0                  # test_roads budget
    assert len(g.nodes) >= 3_000 and len(g.edges) >= 3_000
    assert g.meta.get("key") == WHOLE
    for z in LOCATIONS[WHOLE].safe_zones:                  # interchanges
        hit = g.nearest_node(z.loc, max_m=300.0)
        assert hit is not None, z.zone_id
    for name, (lat, lon) in VALLEY_TOWNS.items():          # Gypsum .. Vail
        hit = g.nearest_node(LatLon(lat, lon), max_m=500.0)
        assert hit is not None, name


def test_whole_valley_route_eagle_base_to_east_vail():
    """The crews' drive across the whole valley is a real I-70 road route
    (exit 147 -> exit 180 ~ 53 km of interstate), fast to compute."""
    g = _whole_graph_or_skip()
    zones = LOCATIONS[WHOLE].safe_zones
    base, east_vail = zones[0].loc, zones[-1].loc
    t0 = time.perf_counter()
    r = roads.route(g, base, east_vail, ResponseConfig())
    assert time.perf_counter() - t0 < 1.0
    assert r.on_roads
    assert 45_000.0 < r.dist_m < 70_000.0, r.dist_m
    chord = geo.haversine_m(base, east_vail)
    assert r.dist_m < 1.5 * chord                          # the interstate, not a detour


def test_whole_valley_places_homes_on_driveways_across_the_valley():
    """ROAD-PROXIMITY PLACEMENT armed at 300 m: every seeded home sits within
    300 m of a road node (the rule BINDS — the raw WUI band holds roadless
    cells), the note says so, and the 40 homes span the valley."""
    from wgresponse.simui import SimSession, wui_cells
    _whole_tile_or_skip()
    _whole_graph_or_skip()
    s = SimSession(WHOLE, 40, 8, seed="driveways",
                   cfg_overrides={"ensemble_n": 2})
    g = s._road_graph
    assert g is not None
    assert len(s._clients) == 40
    for c in s._clients:
        assert g.nearest_node(c.loc, max_m=300.0) is not None, c.client_id
    assert any("within 300 m of a road node" in n for n in s._notes)
    spec = s._tile.spec
    roadless = [rc for rc in wui_cells(s._tile)
                if g.nearest_node(geo.rc_to_latlon(spec, *rc), 300.0) is None]
    assert roadless, "the 300 m rule should exclude some roadless WUI cells"
    lons = [c.loc.lon for c in s._clients]
    assert min(lons) < -106.75 and max(lons) > -106.45, (min(lons), max(lons))
    # in-zone crews stage round-robin from the Eagle base eastward
    assert s._crew_sims["sim-g1"].pos == LOCATIONS[WHOLE].safe_zones[0].loc


def test_whole_valley_placement_without_roads_falls_back_loudly():
    """No road graph (roads disabled) -> the whole WUI band, byte-identical
    to the legacy draw, and a LOUD note — never a silent downgrade."""
    from wgresponse.simui import SimSession, place_clients
    _whole_tile_or_skip()
    s = SimSession(WHOLE, 10, 2, seed="noroads", roads_module=None,
                   cfg_overrides={"ensemble_n": 2})
    assert any("ROAD-PROXIMITY PLACEMENT (300 m)" in n and "no road graph" in n
               for n in s._notes)
    legacy = place_clients(s._tile, 10, seed=f"{WHOLE}|noroads")
    assert [c.loc for c in s._clients] == [c.loc for c in legacy]


def test_place_clients_road_rule_off_is_byte_identical(tmp_path):
    """The rule is inert unless BOTH a graph and a positive distance are
    given: near_road_m=0 with a graph, or a distance with no graph, draws
    exactly the legacy clients (values included)."""
    from wgresponse.roads import RoadEdge, RoadGraph
    from wgresponse.simui import place_clients
    from wgresponse.terrain import SyntheticTerrain
    bbox = geo.bbox_around([LatLon(39.6, -106.6)], pad_m=4_000.0)
    tile = SyntheticTerrain(seed="road-rule", kind="mixed").tile(bbox, 250.0)
    for r in range(10, 20):
        for c in range(10, 20):
            if (r + c) % 3 == 0:
                tile.fuel.set(r, c, 91.0)
    g = RoadGraph({0: LatLon(39.6, -106.62), 1: LatLon(39.6, -106.58)},
                  [RoadEdge(0, 1, "residential", 3_400.0)])
    legacy = place_clients(tile, 12, seed="k")
    same_a = place_clients(tile, 12, seed="k", road_graph=g, near_road_m=0.0)
    same_b = place_clients(tile, 12, seed="k", road_graph=None, near_road_m=300.0)
    key = [(c.client_id, c.loc, c.value_usd) for c in legacy]
    assert [(c.client_id, c.loc, c.value_usd) for c in same_a] == key
    assert [(c.client_id, c.loc, c.value_usd) for c in same_b] == key
    notes = []
    near = place_clients(tile, 3, seed="k", road_graph=g, near_road_m=600.0,
                         notes=notes)
    for c in near:
        assert g.nearest_node(c.loc, max_m=600.0) is not None
    assert any("within 600 m of a road node" in n for n in notes)


def test_whole_valley_extended_request_falls_back_loudly():
    """No extended tile BY DESIGN: a session asking for 'extended' loads the
    standard tile, says why (no misleading refetch hint), and gets no
    extended horizon floor; state.fire_zone reports what actually loaded."""
    from wgresponse.simui import SimSession
    _whole_tile_or_skip()
    s = SimSession(WHOLE, 6, 1, seed="fz", fire_zone="extended",
                   cfg_overrides={"ensemble_n": 2}, roads_module=None)
    st = s.state()
    assert st["fire_zone"] == "standard"
    assert st["cmd_log"][0]["fire_zone"] == "extended"
    note = next(n for n in st["notes"] if "FIRE ZONE" in n)
    assert "by design" in note and f"{WHOLE}_ext.wgt" in note
    assert "--extended" not in note
    assert s._cfg.horizon_h == ResponseConfig().horizon_h
    assert st["terrain"]["nrows"] == 300 and st["terrain"]["cell_m"] == 200.0


def test_tile_tool_never_fetches_an_extended_corridor_tile(monkeypatch, capsys):
    """--extended --only eagle_vail_co is a loud no-op: no LFPS job, no
    provenance_ext.json write, exit 0."""
    import tools.fetch_sim_tiles as ft

    def _boom(*_a, **_k):
        raise AssertionError("must not fetch or write anything")

    monkeypatch.setattr(ft, "fetch_one", _boom)
    monkeypatch.setattr(ft, "write_provenance", _boom)
    assert ft.main(["--extended", "--only", WHOLE]) == 0
    out = capsys.readouterr().out
    assert "BY DESIGN" in out and "nothing to fetch" in out


def test_whole_valley_founder_scenario_one_session():
    """THE REQUEST: a fire ignited near Eagle, crews based at the Eagle base,
    homes from Edwards to Vail — ONE session on the real engine, the safety
    audit at 0 violations."""
    from wgresponse.simui import SimSession
    _whole_tile_or_skip()
    _whole_graph_or_skip()
    s = SimSession(WHOLE, 40, 2, seed="founder",
                   cfg_overrides={"ensemble_n": 2, "horizon_h": 48.0})
    base = LOCATIONS[WHOLE].safe_zones[0].loc
    assert s._crew_sims["sim-g1"].pos == base                  # crew at Eagle
    east = [c for c in s._clients if c.loc.lon > -106.62]       # Edwards .. Vail
    assert len(east) >= 5, len(east)
    lit = None
    for k in range(12):                     # WSW of the base, first burnable
        p = geo.destination(base, 240.0, 3_000.0 + 500.0 * k)
        try:
            s.ignite(p.lat, p.lon, intensity="high")
            lit = p
            break
        except ValueError:
            continue
    assert lit is not None and lit.lon < -106.80                # near Eagle
    for _ in range(4):
        st = s.tick()
    assert st["roads_on"] is True
    assert st["terrain"]["nrows"] == 300
    assert st["scoreboard"]["violations"] == 0
    assert st["location"] == WHOLE
