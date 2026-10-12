"""FINE-FUEL STARTS (founder option 2, 2026-10-12: "move the seven
timber-start fires").

Seven one-click scenarios lit on timber because their first burnable cell was
timber; with calibrated embers and the grass-only speed rule those fires barely
moved. They now start on the grass / grass-shrub / shrub patch nearest their
canonical point (SimSession.fine_fuel_start); the six that already started in
grass or brush keep their exact cells, and a payload {"ign_fine_fuel": false}
replays the first-burnable start. docs/review/timber_start_resite_2026-10-12.md
"""
from __future__ import annotations

import pytest

from wgresponse import geo, server, simui
from wgresponse.fuels import (is_burnable, is_grass, is_grass_shrub, is_shrub,
                              is_timber_litter, is_timber_understory)
from wgresponse.simui import DEFAULT_TILE_DIR, SimSession

SEVEN = {"aspen_southwesterly", "basalt_carbondale_southwesterly",
         "beaver_creek_westerly", "eagle_vail_corridor_westerly",
         "glenwood_springs_westerly", "park_city_windstorm", "vail_westerly"}


def _fine(code):
    return is_grass(code) or is_grass_shrub(code) or is_shrub(code)


def test_the_seven_timber_starts_are_the_marked_scenarios():
    marked = {k for k, v in server.SCENARIOS.items() if v.get("ign_fine_fuel")}
    assert marked == SEVEN


# -- the search, on a painted synthetic tile ----------------------------------

TU2, GR2 = 162, 102


def _painted(tmp_path, paint):
    """A boulder_co session on a synthetic 100 m tile, then every cell set to
    timber understory except where paint(point) says grass."""
    from wgresponse.terrain import SyntheticTerrain, save_ascii_tile
    loc = simui.LOCATIONS["boulder_co"]
    bbox = geo.bbox_around([loc.center], pad_m=12_000.0)
    tile = SyntheticTerrain(seed="fine-start", kind="grass").tile(bbox, 100.0)
    d = tmp_path / "tiles"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / "boulder_co.wgt"))
    s = SimSession("boulder_co", 2, 1, seed="fine-start", tile_dir=d,
                   roads_module=None)
    spec = s._tile.spec
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            p = geo.rc_to_latlon(spec, r, c)
            s._tile.fuel.set(r, c, float(GR2 if paint(p) else TU2))
    return s


def _at(bearing, km):
    return geo.destination(simui.LOCATIONS["boulder_co"].center, bearing,
                           km * 1000.0)


def test_a_grass_patch_beats_a_nearer_lone_grass_cell(tmp_path):
    lone, patch = _at(270.0, 6.0), _at(270.0, 4.8)
    s = _painted(tmp_path, lambda p: (geo.haversine_m(p, lone) < 60.0
                                      or geo.haversine_m(p, patch) < 500.0))
    p = s.fine_fuel_start(270.0, 6000.0)
    assert geo.haversine_m(p, patch) < 500.0
    # and it is the patch's nearest point to the canonical start, not its centre
    assert geo.haversine_m(p, lone) < geo.haversine_m(patch, lone)


def test_a_lone_cell_is_taken_when_there_is_no_patch(tmp_path):
    lone = _at(275.0, 5.7)
    s = _painted(tmp_path, lambda p: geo.haversine_m(p, lone) < 60.0)
    p = s.fine_fuel_start(270.0, 6000.0)
    rc = geo.latlon_to_rc(s._tile.spec, p)
    assert _fine(int(round(s._tile.fuel.get(*rc))))
    assert geo.haversine_m(p, lone) < 150.0


def test_no_fine_fuel_in_the_envelope_is_none(tmp_path):
    outside = _at(90.0, 6.0)                      # downwind side: not searched
    s = _painted(tmp_path, lambda p: geo.haversine_m(p, outside) < 800.0)
    assert s.fine_fuel_start(270.0, 6000.0) is None


# -- the server ---------------------------------------------------------------

def test_a_bad_ign_fine_fuel_is_a_400():
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": "park_city_windstorm",
                                 "ign_fine_fuel": "yes"})
    assert code == 400 and "ign_fine_fuel" in out["error"]


def _legacy_point(s, key):
    """The first-burnable search (server.new_session before option 2)."""
    sc = server.SCENARIOS[key]
    center = simui.LOCATIONS[sc["location"]].center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, sc["ign_bearing"] + db,
                                sc["ign_dist_m"] * shrink)
            rc = geo.latlon_to_rc(s._tile.spec, p)
            if rc is not None and is_burnable(int(round(s._tile.fuel.get(*rc)))):
                return p
    raise AssertionError(key)


def _origin(key, **payload):
    mgr = server.SimSessionManager()
    code, out = mgr.new_session(dict({"scenario": key}, **payload))
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    return s, s._fires["sim-f1"].origin


_REAL = all((DEFAULT_TILE_DIR / f"{v['location']}.wgt").exists()
            for v in server.SCENARIOS.values())


@pytest.mark.skipif(not _REAL, reason="real tiles data/tiles/*.wgt not present")
@pytest.mark.parametrize("key", sorted(server.SCENARIOS))
def test_every_one_click_fire_starts_in_grass_or_brush(key):
    """On the real tiles: the seven start on fine fuel within ~1 km of their
    canonical point (measured 300-1,050 m) and no longer on timber; the six
    others keep their exact first-burnable cell (already grass or brush)."""
    s, o = _origin(key)
    code = int(round(s._tile.fuel.get(*geo.latlon_to_rc(s._tile.spec, o))))
    assert _fine(code), (key, code)
    sc = server.SCENARIOS[key]
    canon = geo.destination(simui.LOCATIONS[sc["location"]].center,
                            sc["ign_bearing"], sc["ign_dist_m"])
    legacy = _legacy_point(s, key)
    if key in SEVEN:
        lcode = int(round(s._tile.fuel.get(*geo.latlon_to_rc(s._tile.spec,
                                                             legacy))))
        assert is_timber_understory(lcode) or is_timber_litter(lcode), key
        assert geo.haversine_m(o, canon) < 1500.0, key
        assert s._fires["sim-f1"].wind_dir_deg == sc["ign_bearing"]
    else:
        assert (o.lat, o.lon) == (legacy.lat, legacy.lon), key


@pytest.mark.skipif(not _REAL, reason="real tiles data/tiles/*.wgt not present")
def test_the_payload_replays_the_first_burnable_start():
    s, o = _origin("park_city_windstorm", ign_fine_fuel=False)
    legacy = _legacy_point(s, "park_city_windstorm")
    assert (o.lat, o.lon) == (legacy.lat, legacy.lon)
