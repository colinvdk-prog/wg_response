"""TRANSIT-HAZARD STAGE 1 (contracts transit_cold_black_by_class) + the
calibrated-stack simulator defaults flip (founder 2026-07-30)."""
from __future__ import annotations

from wgresponse import geo, simui
from wgresponse.fuels import cold_black_minutes


# ---------------------------------------------------------------- table


def test_cold_black_table_grass_opens_timber_stricter():
    assert cold_black_minutes(102) == 5.0     # GR2: 1-h only
    assert cold_black_minutes(103) == 10.0    # GR3: carries 10-h
    assert cold_black_minutes(121) == 10.0    # GS1
    assert cold_black_minutes(124) == 20.0    # GS4: 100-h load
    assert cold_black_minutes(145) == 20.0    # SH5: live woody stems
    assert cold_black_minutes(142) == 30.0    # SH2: 100-h load
    assert cold_black_minutes(163) == 45.0    # TU: unchanged snag floor
    assert cold_black_minutes(186) == 60.0    # TL: STRICTER than flat 45
    assert cold_black_minutes(202) == 90.0    # SB
    assert cold_black_minutes(150) == 90.0    # unknown burnable: fail long


# ---------------------------------------------------------------- helper


def _session(**cfg):
    return simui.SimSession("boulder_co", 2, 1, seed="cb-test",
                            cfg_overrides=cfg)


def test_flag_off_flat_window_everywhere():
    s = _session(transit_cold_black_by_class=False)
    p = geo.rc_to_latlon(s._tile.spec, 5, 5)
    assert s._cold_black_pass_s(p) == simui._COLD_BLACK_PASS_S


def test_flag_on_reads_cell_fuel():
    s = _session(transit_cold_black_by_class=True)
    spec = s._tile.spec
    p = geo.rc_to_latlon(spec, 5, 5)
    rc = geo.latlon_to_rc(spec, p)
    idx = rc[0] * spec.ncols + rc[1]
    s._tile.fuel.data[idx] = 102.0            # GR2
    assert s._cold_black_pass_s(p) == 5.0 * 60.0
    s._tile.fuel.data[idx] = 186.0            # TL6
    assert s._cold_black_pass_s(p) == 60.0 * 60.0
    s._tile.fuel.data[idx] = 91.0             # NB urban: explicit flat
    # (irrelevant in practice — NB cells never carry a finite nominal
    # arrival, so the burned-test never reaches the window there)
    assert s._cold_black_pass_s(p) == 45.0 * 60.0


def test_off_tile_point_falls_back_flat():
    s = _session(transit_cold_black_by_class=True)
    far = geo.LatLon(10.0, 10.0)
    assert s._cold_black_pass_s(far) == simui._COLD_BLACK_PASS_S


# ---------------------------------------------------------------- flip


def test_sim_defaults_flip_calibrated_stack():
    s = _session()
    assert s._cfg.corridor_erosion_tiered is True
    assert s._cfg.corridor_erosion_fast_m == 550.0
    assert s._cfg.corridor_erosion_shrub_m == 200.0
    assert s._cfg.corridor_erosion_slow_m == 150.0
    assert s._cfg.escape_hysteresis_margin_min == 20.0
    assert s._cfg.transit_cold_black_by_class is True
    # fix-program item 1 (2026-09-03): the sim bounds off-raster corridor
    # samples; the in-tile UNREACHED bound is the LIVE daemon's (the sim tile
    # is its whole world)
    assert s._cfg.corridor_fail_closed is True
    assert s._cfg.corridor_unreached_bound is False


def test_cold_black_nonburnable_flat_window():
    assert cold_black_minutes(91) == 45.0     # NB urban: explicit flat
    assert cold_black_minutes(98) == 45.0     # water


def test_sim_defaults_explicit_overrides_win():
    s = _session(corridor_erosion_tiered=False,
                 escape_hysteresis_margin_min=0.0)
    assert s._cfg.corridor_erosion_tiered is False
    assert s._cfg.escape_hysteresis_margin_min == 0.0


def test_engine_defaults_stay_off():
    from wgresponse.contracts import ResponseConfig
    cfg = ResponseConfig()
    assert cfg.corridor_erosion_tiered is False
    assert cfg.escape_hysteresis_margin_min == 0.0
    assert cfg.transit_cold_black_by_class is False
    assert cfg.corridor_fail_closed is False
    assert cfg.corridor_unreached_bound is False
    assert cfg.ros_floor_extinction_gate is False
    assert cfg.model_error_sd_log == 0.0
    assert cfg.ensemble_p50_nominal_bound is False
