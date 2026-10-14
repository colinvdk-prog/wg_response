"""The simulator's fire-physics defaults (founder decisions).

2026-10-09 the founder limited the fuel-class speed rule to grass ("limit the
speed rule to grass in the simulator"); 2026-10-12 the founder chose "option
1": calibrated embers (cfg.spot_fire_size, SAFETY SEMANTICS 19) plus the timber
floor restored (cfg.ros_floor_grass_only=False) until a crown-fire model
existed; the same day the crown-fire model landed (SAFETY SEMANTICS 22) and the
founder chose "Switch, 10-min delay": crown fire on, the speed floor back on
grass only, a landed ember lighting its spot fire after 10 min. Crown fire
stands in place of the timber floor, never on top of it: a session that
explicitly keeps the timber floor keeps the pre-crown physics. All armed in
SimSession.configure's simulator defaults; an explicit cfg_overrides key wins,
and the live engine's ResponseConfig defaults are unchanged. The Park City
comparison below keeps the 2026-10-09 measurement
(docs/review/park_city_speed_2026-10-09.md).
"""
from __future__ import annotations

import pytest

from wgresponse import geo
from wgresponse.contracts import ResponseConfig
from wgresponse.simui import DEFAULT_TILE_DIR, LOCATIONS, SimSession

PAGE = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}


def test_simulator_defaults_to_crown_fire():
    """Founder option 3 (2026-10-12, "Switch, 10-min delay"): crown fire on,
    calibrated embers with a 10-min spot delay, the floor on grass only; and
    the spread-direction fix (2026-10-14, "perform the spread-direction
    fix")."""
    s = SimSession("boulder_co", 2, 1, seed="floor-default",
                   cfg_overrides=dict(PAGE), roads_module=None)
    assert s._cfg.crown_fire is True
    assert s._cfg.spot_fire_size is True
    assert s._cfg.spot_ignition_delay_min == 10.0
    assert s._cfg.ros_floor_grass_only is True
    assert s._cfg.spread_any_angle is True


def test_an_explicit_override_still_wins():
    s = SimSession("boulder_co", 2, 1, seed="floor-default",
                   cfg_overrides=dict(PAGE, ros_floor_grass_only=True,
                                      spot_fire_size=False, crown_fire=False,
                                      spot_ignition_delay_min=5.0),
                   roads_module=None)
    assert s._cfg.ros_floor_grass_only is True
    assert s._cfg.spot_fire_size is False
    assert s._cfg.crown_fire is False
    assert s._cfg.spot_ignition_delay_min == 5.0


def test_crown_fire_never_rides_on_an_explicit_timber_floor():
    """Crown fire stands in place of the timber floor: a session that keeps
    the timber floor keeps the pre-crown physics, unless it asks for both."""
    s = SimSession("boulder_co", 2, 1, seed="floor-default",
                   cfg_overrides=dict(PAGE, ros_floor_grass_only=False),
                   roads_module=None)
    assert s._cfg.crown_fire is False
    s = SimSession("boulder_co", 2, 1, seed="floor-default",
                   cfg_overrides=dict(PAGE, ros_floor_grass_only=False,
                                      crown_fire=True),
                   roads_module=None)
    assert s._cfg.crown_fire is True


def test_the_live_engine_defaults_are_unchanged():
    cfg = ResponseConfig()
    assert cfg.ros_floor_grass_only is False
    assert cfg.spot_fire_size is False
    assert cfg.crown_fire is False
    assert cfg.spot_ignition_delay_min == 5.0
    assert cfg.spread_any_angle is False


def _park_city_acres_at_1h45(grass_only: bool) -> float:
    """The founder's Park City one-click frame (server.SCENARIOS
    park_city_windstorm: extreme, fire 7 km SW, wind FROM 225) at T+1h45."""
    # the 2026-10-09 measurement's conditions, pinned: the generic presets,
    # the legacy ember draw and no crown fire (the simulator defaults have
    # since moved on)
    s = SimSession("park_city_ut", 40, 2, seed="scenario-park_city_windstorm",
                   cfg_overrides=dict(PAGE, ros_floor_grass_only=grass_only,
                                      sim_weather_profile="generic",
                                      spot_fire_size=False, crown_fire=False),
                   roads_module=None)
    center = LOCATIONS["park_city_ut"].center
    for db in (0, 10, -10, 20, -20, 30, -30):          # the server's search
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, 225.0 + db, 7000.0 * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=225.0)
                break
            except ValueError:
                continue
        else:
            continue
        break
    st = None
    for _ in range(7):                                  # 7 x 15 min = 1 h 45
        st = s.tick()
    return st["fire_view"]["areas_km2"]["fire"] * 247.105


@pytest.mark.skipif(not (DEFAULT_TILE_DIR / "park_city_ut.wgt").exists(),
                    reason="real tile data/tiles/park_city_ut.wgt not present")
def test_park_city_windstorm_no_longer_runs_away_in_the_first_hours():
    """Measured 2026-10-09: 3,054 acres at T+1h45 with timber floored, 36 acres
    grass-only. The bounds leave room for small physics changes elsewhere."""
    assert _park_city_acres_at_1h45(grass_only=True) < 500.0
    assert _park_city_acres_at_1h45(grass_only=False) > 1500.0
