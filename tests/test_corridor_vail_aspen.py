"""Vail -> Aspen corridor program (founder 2026-09-23 "build the response
program from Vail to Aspen"; docs/vail_aspen_response_program.md): the six
community presets, their sync with the tile tool, the simulator page, the
watcher AOI file, and (when fetched) the corridor road graph."""
from __future__ import annotations

import pathlib

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
