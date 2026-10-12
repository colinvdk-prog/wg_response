"""tests/test_crown_fire.py — CROWN FIRE (cfg.crown_fire, contracts SAFETY
SEMANTICS 22; docs/review/crown_fire_model_2026-10-12.md).

Founder 2026-10-12 (option 3 of the Park City speed review): "build a
crown-fire model". The engine modelled surface fire only, so with the
fuel-class floor on grass alone the one-click timber fires crawled (Park
City: 50 acres in 6 h). Armed, on a tile carrying LANDFIRE canopy layers,
each forest cell's surface fire may torch or crown: Van Wagner (1977)
initiation, active vs passive by his critical rate 3.0 / CBD m/min, the
Cruz, Alexander & Wakimoto (2005) crown rate of spread, the crown fire's
intensity, Rothermel's (1991) crown-fire ellipse, the ember launch on the
crown intensity with a torching-tree reach (Albini 1979) under SEMANTICS 19.

Pinned here:
  * OFF is byte-identical — on a tile WITH canopy (legacy fingerprints,
    spotting / size law / any-angle / piecewise / perimeter / kept), and ON
    is byte-identical on a tile without canopy or without crown fuel;
  * the physics functions against their published equations;
  * a uniform conifer tile runs at the Cruz rate; crown never makes a cell
    later (constant weather); a canopy the fire cannot ignite changes
    nothing;
  * the ROS memo stays exact (memo = WGRESP_NO_ROS_MEMO=1 reference with the
    knob on: zones, hour_ros_scale, seasonal FMC, spotting, any-angle,
    piecewise, perimeter), deterministic, re-anchor continuous;
  * the torching reach and its minimum fire size; the fast erosion tier;
  * the canopy-carrying tile format, LfpsTerrain canopy, the committed tiles.
"""
from __future__ import annotations

import dataclasses
import io
import json
import math
import os
import zipfile

import pytest

from tests.test_spread import (
    T0,
    _LEGACY_FINGERPRINTS,
    _fingerprint,
    _spot_setup,
    center_ignition,
    const_wx,
    grass_tile,
)
from tests.test_sim_reanchor_progress import HOUR, _burned, _diff_below, _history
from wgresponse import geo, rothermel, spread, terrain
from wgresponse.contracts import (
    CanopyLayers,
    GridSpec,
    Raster,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
    validate_config,
)

MPH = 2.2369362920544025


def _cfg(**kw) -> ResponseConfig:
    kw.setdefault("ensemble_n", 1)
    kw.setdefault("horizon_h", 12.0)
    return ResponseConfig(**kw)


def _on(**kw) -> ResponseConfig:
    return _cfg(crown_fire=True, **kw)


def _canopy(spec, cbh, cbd, cc, ch, source="test") -> CanopyLayers:
    def mk(v):
        if callable(v):
            return Raster(spec, [float(v(i)) for i in range(spec.nrows * spec.ncols)])
        return geo.make_raster(spec, fill=float(v))
    return CanopyLayers(mk(cbh), mk(cbd), mk(cc), mk(ch), source=source)


def _with_canopy(tile, cbh=1.0, cbd=0.12, cc=60.0, ch=18.0) -> TerrainTile:
    return terrain.attach_canopy(tile, _canopy(tile.spec, cbh, cbd, cc, ch))


def _conifer_tile(nrows=41, ncols=161, cell_m=60.0, fuel=165.0, cbh=1.0,
                  cbd=0.12, cc=60.0, ch=18.0) -> TerrainTile:
    spec = GridSpec(north_lat=39.5, west_lon=-106.5, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    tile = TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=fuel),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=-1.0),
                       elev_m=geo.make_raster(spec, fill=2500.0),
                       source="test-conifer")
    return _with_canopy(tile, cbh, cbd, cc, ch)


def _mosaic(n=45) -> TerrainTile:
    """Bands of TU5 / TL3 / GS2 / SH7 / TU1 / GR2 with slope, aspect, a
    two-column NB road, and a canopy mixing dense conifer, open conifer,
    LANDFIRE's hardwood code (CBH 10 m / CBD 0.01) and no canopy."""
    spec = GridSpec(north_lat=39.5, west_lon=-106.5, cell_m=90.0,
                    nrows=n, ncols=n)
    bands = (165.0, 183.0, 122.0, 147.0, 161.0, 102.0)
    fuel = geo.make_raster(spec, fill=165.0)
    slope = geo.make_raster(spec, fill=0.0)
    aspect = geo.make_raster(spec, fill=-1.0)
    for r in range(n):
        for c in range(n):
            fuel.set(r, c, bands[((r + 2 * c) // 6) % len(bands)])
            slope.set(r, c, float((r * 7 + c * 3) % 25))
            aspect.set(r, c, float((r * 41 + c * 13) % 360))
    if n > 31:
        for r in range(n):
            fuel.set(r, 30, 91.0)
            fuel.set(r, 31, 91.0)
    tile = TerrainTile(spec=spec, fuel=fuel, slope_deg=slope,
                       aspect_deg=aspect,
                       elev_m=Raster(spec, [2000.0 + (i % 97) * 7.0
                                            for i in range(n * n)]),
                       source="test-mosaic")
    kinds = ((0.3, 0.16, 65.0, 20.0),      # dense conifer
             (2.0, 0.06, 35.0, 15.0),      # open conifer
             (10.0, 0.01, 45.0, 15.0),     # LANDFIRE hardwood code
             (0.0, 0.0, 0.0, 0.0),         # no canopy
             (0.6, 0.11, 55.0, 18.0))      # moderate conifer

    def pick(j):
        def f(i):
            r, c = divmod(i, n)
            return kinds[((r // 5) + (c // 7)) % len(kinds)][j]
        return f
    return terrain.attach_canopy(
        tile, _canopy(spec, pick(0), pick(1), pick(2), pick(3)))


def _wx(loc, mph, wdir=270.0, rh=10.0, temp=33.0, hours=40, t0=T0):
    w = mph / MPH
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=t0 + i * HOUR, wind_speed_mps=w,
                      wind_gust_mps=w * 1.3, wind_dir_deg=wdir, rh_pct=rh,
                      temp_c=temp) for i in range(hours)])


def _veer_wx(loc):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0 + i * HOUR,
                      wind_speed_mps=5.0 + float(i % 9),
                      wind_gust_mps=(5.0 + float(i % 9)) * 1.3,
                      wind_dir_deg=(250.0 + 10.0 * (i % 10)) % 360.0,
                      rh_pct=8.0 + 3.0 * (i % 7), temp_c=24.0 + 2.5 * (i % 5))
        for i in range(100)])


# ---------------------------------------------------------------------------
# 1. OFF is byte-identical; ON without crown fuel is byte-identical
# ---------------------------------------------------------------------------

def test_knob_off_with_canopy_matches_the_legacy_fingerprints():
    """The pinned legacy scenarios A-D (flat wind, pure slope, spotting over
    a barrier, wind switch) with a dense canopy attached and the knob OFF:
    the same bits as without any canopy."""
    cfg = ResponseConfig(ensemble_n=4)
    tile = _with_canopy(grass_tile(), cbh=0.3, cbd=0.2)
    ign = center_ignition(tile)
    arr = spread.compute_arrival(tile, [ign], const_wx(ign), T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["A_flat_wind"]
    tile = _with_canopy(grass_tile(slope_deg=30.0, aspect_deg=270.0),
                        cbh=0.3, cbd=0.2)
    ign = center_ignition(tile)
    arr = spread.compute_arrival(tile, [ign], const_wx(ign, wind_mps=0.0),
                                 T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["B_upslope_nowind"]
    tile, ign, wx = _spot_setup()
    tile = _with_canopy(tile, cbh=0.3, cbd=0.2)
    arr = spread.compute_arrival(
        tile, [ign], wx, T0, ResponseConfig(ensemble_n=1, spotting_enabled=True))
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["C_spotting"]
    tile = _with_canopy(grass_tile(), cbh=0.3, cbd=0.2)
    ign = center_ignition(tile)
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=10.0,
                             wind_gust_mps=13.0,
                             wind_dir_deg=270.0 if i < 2 else 180.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    wx = WeatherForecast(loc=ign, samples=samples, source="test")
    arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["D_wind_switch"]


@pytest.mark.parametrize("extra", [
    {},
    {"spotting_enabled": True},
    {"spotting_enabled": True, "spot_fire_size": True},
    {"spread_any_angle": True},
    {"edge_weather_piecewise": True},
    {"slope_phi_vector": True, "grass_ros_floor_frac": 0.1,
     "ros_floor_extinction_gate": True},
])
def test_knob_off_canopy_is_inert(extra):
    """Knob OFF: a mosaic with canopy solves exactly as without it, under
    every other physics knob."""
    tile = _mosaic()
    bare = dataclasses.replace(tile, canopy=None)
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = _veer_wx(ign)
    cfg = _cfg(**extra)
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="x")
    b = spread.compute_arrival(bare, [ign], wx, T0, cfg, incident_id="x")
    assert a.data == b.data
    trace = {}
    spread.compute_arrival(tile, [ign], wx, T0, cfg, crown_trace=trace)
    assert trace == {}


@pytest.mark.parametrize("canopy", ["none", "no_crown_fuel"])
def test_knob_on_without_crown_fuel_is_byte_identical(canopy):
    """Knob ON on a tile without canopy layers, or whose canopy holds no
    crown fuel (LANDFIRE's hardwood code CBD 0.01 everywhere, or CH <= CBH):
    exactly the surface solve, spotting and the size law included."""
    tile = _mosaic()
    if canopy == "none":
        t_on = dataclasses.replace(tile, canopy=None)
    else:
        n = tile.spec.nrows * tile.spec.ncols
        t_on = terrain.attach_canopy(tile, _canopy(
            tile.spec, lambda i: 10.0 if i % 2 else 5.0,
            lambda i: 0.01 if i % 2 else 0.3, 60.0,
            lambda i: 20.0 if i % 2 else 4.0))
        assert n
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = _veer_wx(ign)
    for extra in ({}, {"spotting_enabled": True, "spot_fire_size": True},
                  {"spread_any_angle": True}):
        a = spread.compute_arrival(t_on, [ign], wx, T0, _on(**extra),
                                   incident_id="x")
        b = spread.compute_arrival(tile, [ign], wx, T0, _cfg(**extra),
                                   incident_id="x")
        assert a.data == b.data


def test_crown_parameters_inert_while_knob_off():
    """The foliar-moisture knobs change nothing with crown_fire off."""
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = _veer_wx(ign)
    a = spread.compute_arrival(tile, [ign], wx, T0, _cfg())
    b = spread.compute_arrival(tile, [ign], wx, T0, _cfg(
        crown_foliar_moisture_pct=70.0, crown_foliar_moisture_seasonal=True))
    assert a.data == b.data


# ---------------------------------------------------------------------------
# 2. the physics functions against their published equations
# ---------------------------------------------------------------------------

def test_van_wagner_initiation_intensity():
    """I_o = (0.010 CBH (460 + 25.9 FMC))^1.5 kW/m (Van Wagner 1977)."""
    for cbh, fmc in ((0.3, 100.0), (1.0, 100.0), (4.0, 85.0), (10.0, 120.0)):
        want = (0.010 * cbh * (460.0 + 25.9 * fmc)) ** 1.5
        assert rothermel.crown_initiation_kwm(cbh, fmc) == pytest.approx(want)
    assert rothermel.crown_initiation_kwm(1.0, 100.0) == pytest.approx(168.44,
                                                                       abs=0.01)
    assert rothermel.crown_initiation_kwm(0.0, 100.0) == 0.0
    # higher foliar moisture -> harder to ignite
    assert (rothermel.crown_initiation_kwm(2.0, 120.0)
            > rothermel.crown_initiation_kwm(2.0, 85.0))


def test_cruz_crown_rate_of_spread():
    """CROS_A = 11.02 U10^0.90 CBD^0.19 exp(-0.17 EFFM) m/min with U10 in
    km/h (Cruz, Alexander & Wakimoto 2005; the CFIS / firebehavioR cfis
    coefficients); CAC = CROS_A CBD / 3; passive CROS_A exp(-CAC)."""
    u10 = 40.0 / 3.6
    ra = rothermel.cruz_active_crown_ros_mps(u10, 0.15, 6.0) * 60.0
    want = 11.02 * 40.0 ** 0.9 * 0.15 ** 0.19 * math.exp(-0.17 * 6.0)
    assert ra == pytest.approx(want, rel=1e-12)
    assert ra == pytest.approx(76.65, abs=0.01)
    thr = rothermel.crown_active_threshold_mps(0.15) * 60.0
    assert thr == pytest.approx(3.0 / 0.15)
    assert rothermel.cruz_active_crown_ros_mps(0.0, 0.15, 6.0) == 0.0
    assert rothermel.crown_active_threshold_mps(0.0) == math.inf


def test_crown_fire_states_and_monotonicity():
    """NONE below initiation or without crown fuel; ACTIVE at CAC >= 1 at the
    Cruz rate; PASSIVE below at CROS_A exp(-CAC); never lowers the surface
    ROS or intensity; intensity includes the canopy fuel load."""
    u = 30.0 / MPH * 1.15                          # a 30 mi/h 20-ft wind
    # surface fire too weak for a 6 m CBH: NONE
    ros, inten, cfb, kind = rothermel.crown_fire(0.05, 400.0, 8000.0, u, 6.0,
                                                 6.0, 0.15, 20.0, 100.0)
    assert (ros, inten, cfb, kind) == (0.05, 400.0, 0.0, rothermel.CROWN_NONE)
    # hardwood code / no crown fuel: NONE whatever the intensity
    for cbd, ch in ((0.01, 20.0), (0.15, 0.5)):
        assert rothermel.crown_fire(0.05, 9e4, 8000.0, u, 6.0, 1.0, cbd, ch,
                                    100.0)[3] == rothermel.CROWN_NONE
    # dense canopy at strong wind: ACTIVE at the Cruz rate
    ros, inten, cfb, kind = rothermel.crown_fire(0.05, 400.0, 8000.0, u, 6.0,
                                                 0.5, 0.15, 20.0, 100.0)
    ra = rothermel.cruz_active_crown_ros_mps(u, 0.15, 6.0)
    assert kind == rothermel.CROWN_ACTIVE and cfb == 1.0
    assert ros == ra
    assert inten == pytest.approx(ra * (8000.0 + 0.15 * 19.5 * 18608.0))
    # sparse canopy, light wind: PASSIVE at CROS_A exp(-CAC)
    u2 = 6.0 / MPH * 1.15
    ros, inten, cfb, kind = rothermel.crown_fire(0.02, 300.0, 8000.0, u2, 8.0,
                                                 0.5, 0.05, 12.0, 100.0)
    ra = rothermel.cruz_active_crown_ros_mps(u2, 0.05, 8.0)
    cac = ra / rothermel.crown_active_threshold_mps(0.05)
    assert kind == rothermel.CROWN_PASSIVE and cac < 1.0
    assert ros == max(0.02, ra * math.exp(-cac))
    assert 0.0 < cfb < 1.0 and inten > 300.0
    # never lowers: a fast surface fire keeps its rate
    ros, inten, _c, kind = rothermel.crown_fire(5.0, 9e4, 8000.0, u2, 8.0,
                                                0.5, 0.05, 12.0, 100.0)
    assert kind != rothermel.CROWN_NONE and ros == 5.0 and inten >= 9e4


def test_crown_fraction_burned_farsite_form():
    """CFB = 1 - exp(-a_c (R - R_o)), a_c = -ln 0.1 / (0.9 (R'_A - R_o)):
    0 at R_o, 0.9 at 90 % of the way to R'_A, 1 when R'_A <= R_o."""
    assert rothermel.crown_fraction_burned(1.0, 1.0, 21.0) == 0.0
    assert rothermel.crown_fraction_burned(19.0, 1.0, 21.0) == pytest.approx(0.9)
    assert rothermel.crown_fraction_burned(2.0, 1.0, 0.5) == 1.0


def test_effm_shading_correction():
    """Rothermel (1983) tables: +3 points at midday where the canopy shades
    the surface fuel (cover >= 50 %), none in the open."""
    assert rothermel.crown_effm_pct(0.04, 60.0) == pytest.approx(7.0)
    assert rothermel.crown_effm_pct(0.04, 49.0) == pytest.approx(4.0)


def test_crown_ellipse_rothermel_1991():
    """L/W = 1 + 0.125 U20 (mi/h), U20 = U10 / 1.15, capped at 8."""
    u10 = 40.0 / MPH
    assert rothermel.crown_lb(u10) == pytest.approx(1.0 + 0.125 * 40.0 / 1.15)
    assert rothermel.crown_lb(200.0) == 8.0
    assert rothermel.crown_lb(0.0) == 1.0


def test_fbp_foliar_moisture():
    """FBP (ST-X-3) eqs. 1-8: D0 from latitude, longitude and elevation; 85 %
    on D0; 85 + 0.0189 ND^2 below 30 days; 120 % from 50 days."""
    d0 = rothermel.fbp_fmc_min_day(40.7, -111.55, 2200.0)
    latn = 43.0 + 33.7 * math.exp(-0.0351 * (150.0 - 111.55))
    assert d0 == round(142.1 * 40.7 / latn + 0.0172 * 2200.0)
    assert rothermel.foliar_moisture_fbp_pct(40.7, -111.55, 2200.0, d0) == 85.0
    assert rothermel.fbp_fmc_from_nd(20) == pytest.approx(85.0 + 0.0189 * 400)
    assert rothermel.fbp_fmc_from_nd(40) == pytest.approx(
        32.9 + 3.17 * 40 - 0.0288 * 1600)
    assert rothermel.fbp_fmc_from_nd(50) == 120.0
    d0_flat = rothermel.fbp_fmc_min_day(40.7, -111.55, 0.0)
    latn0 = 46.0 + 23.4 * math.exp(-0.0360 * (150.0 - 111.55))
    assert d0_flat == round(151.0 * 40.7 / latn0)


def test_torching_tree_spotting_behaveplus():
    """Albini (1979) as BehavePlus codes it, by hand for one case: a 20 m
    Douglas-fir (DBH = height ft / 5), one tree, 35 mi/h 20-ft wind, closed
    20 m downwind cover."""
    ht = 20.0 / 0.3048
    dbh = ht / 5.0
    zf = 15.7 * dbh ** 0.451
    dur = 10.7 * dbh ** -0.278
    assert ht / zf >= 1.0                          # firebrand row 0
    z = 4.24 * dur ** 0.332 * zf + ht / 2.0
    h = max(ht, 2.2 * z ** 0.337 - 4.0)
    d = 0.000718 * 35.0 * math.sqrt(h) * (0.362 + math.sqrt(z / h) / 2.0
                                          * math.log(z / h)) * 1609.344
    got = rothermel.torching_spot_max_m(20.0, 35.0 / MPH, 20.0)
    assert got == pytest.approx(d, rel=1e-12)
    assert 500.0 < got < 1500.0                    # BehavePlus's usual range
    # open downwind cover flies farther; no wind, no tree: nothing
    assert rothermel.torching_spot_max_m(20.0, 35.0 / MPH, 10.0) > got
    assert rothermel.torching_spot_max_m(20.0, 0.0, 20.0) == 0.0
    assert rothermel.torching_spot_max_m(0.0, 10.0, 20.0) == 0.0


# ---------------------------------------------------------------------------
# 3. the spread
# ---------------------------------------------------------------------------

def _head_kmh(arr, tile, c0=5, km_lo=1.0, km_hi=7.0):
    spec = tile.spec
    r0 = spec.nrows // 2
    pts = [((c - c0) * spec.cell_m / 1000.0, arr[r0 * spec.ncols + c])
           for c in range(c0, spec.ncols)]
    sel = [(d, v) for d, v in pts if km_lo <= d <= km_hi and math.isfinite(v)]
    (d1, v1), (d2, v2) = sel[0], sel[-1]
    return (d2 - d1) / ((v2 - v1) / 3600.0)


@pytest.mark.parametrize("mph", [18.0, 28.0, 40.0])
def test_uniform_conifer_runs_at_the_cruz_rate(mph):
    """Flat uniform TU5 under a dense canopy, wind on the grid axis: every
    burned cell crowns ACTIVELY and the head runs at the Cruz rate (EFFM
    with the shading correction) — 9-16 % of the 10-m wind at 18-40 mi/h —
    while the surface fire alone crawls."""
    tile = _conifer_tile(cbh=0.5, cbd=0.16, cc=65.0, ch=20.0)
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 5)
    wx = _wx(ign, mph, rh=12.0, temp=35.0)
    trace = {}
    on = spread.compute_arrival(tile, [ign], wx, T0, _on(spread_any_angle=True),
                                crown_trace=trace).data
    off = spread.compute_arrival(tile, [ign], wx, T0, _cfg()).data
    head = _head_kmh(on, tile)
    m_dead = spread.dead_fuel_moisture_frac(35.0, 12.0)
    effm = rothermel.crown_effm_pct(max(0.01, m_dead), 65.0)
    want = rothermel.cruz_active_crown_ros_mps(mph / MPH, 0.16, effm) * 3.6
    assert head == pytest.approx(want, rel=0.01)
    assert 0.09 <= head / (mph * 1.609344) <= 0.16
    assert set(trace.values()) == {rothermel.CROWN_ACTIVE}
    burned_on = sum(1 for v in on if v <= T0 + 3 * HOUR)
    burned_off = sum(1 for v in off if v <= T0 + 3 * HOUR)
    assert burned_on > 10 * burned_off


@pytest.mark.parametrize("wind", [6.0, 14.0])
def test_crown_never_later_under_constant_weather(wind):
    """Constant weather, spotting off: the crown model only raises a cell's
    spread, so no cell of the mosaic burns later than without it — and some
    burn earlier. (With spotting on each ember's draw re-scales with the
    crown intensity, so a single cell can burn later, as with any change
    that moves embers; the burned area still grows.)"""
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = const_wx(ign, wind_mps=wind, wind_dir_deg=265.0, rh_pct=10.0,
                  temp_c=34.0)
    for extra in ({}, {"spread_any_angle": True}):
        off = spread.compute_arrival(tile, [ign], wx, T0, _cfg(**extra),
                                     incident_id="m").data
        on = spread.compute_arrival(tile, [ign], wx, T0, _on(**extra),
                                    incident_id="m").data
        assert all(b <= a for a, b in zip(off, on)), extra
        assert any(b < a for a, b in zip(off, on)), extra
    off = spread.compute_arrival(tile, [ign], wx, T0,
                                 _cfg(spotting_enabled=True),
                                 incident_id="m").data
    on = spread.compute_arrival(tile, [ign], wx, T0,
                                _on(spotting_enabled=True),
                                incident_id="m").data
    for h in (1, 3, 6):
        t = T0 + h * HOUR
        assert sum(1 for v in on if v <= t) >= sum(1 for v in off if v <= t)


def test_hardwood_code_never_crowns():
    """LANDFIRE codes broadleaf stands CBH 10 m / CBD 0.01 'to prevent false
    simulation of crown fire': under the most intense shrub fire (SH7, 40
    mi/h) such a canopy changes nothing."""
    tile = _conifer_tile(fuel=147.0, cbh=10.0, cbd=0.01, cc=55.0, ch=15.0)
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 5)
    wx = _wx(ign, 40.0, rh=6.0, temp=39.0)
    trace = {}
    on = spread.compute_arrival(tile, [ign], wx, T0, _on(), crown_trace=trace)
    off = spread.compute_arrival(tile, [ign], wx, T0, _cfg())
    assert on.data == off.data and trace == {}


def test_crown_fire_is_broader_than_the_surface_needle(monkeypatch):
    """Where the crown leads, the ellipse is Rothermel's (1991) crown ratio
    (5.35 at 40 mi/h) instead of the surface cap of 8: the same head, a
    wider fire (more cells burned by the time the head reaches a column)."""
    assert rothermel.crown_lb(40.0 / MPH) == pytest.approx(5.35, abs=0.01)
    tile = _conifer_tile(nrows=81, ncols=81, cell_m=90.0, cbh=0.5, cbd=0.16)
    ign = geo.rc_to_latlon(tile.spec, 40, 10)
    wx = _wx(ign, 40.0, rh=12.0, temp=35.0)
    cfg = _on(spread_any_angle=True)
    broad = spread.compute_arrival(tile, [ign], wx, T0, cfg).data
    monkeypatch.setattr(rothermel, "crown_lb", lambda w: 8.0)
    needle = spread.compute_arrival(tile, [ign], wx, T0, cfg).data
    n = tile.spec.ncols
    t_head = broad[40 * n + 70]
    assert needle[40 * n + 70] == pytest.approx(t_head, rel=1e-9)
    burned_broad = sum(1 for v in broad if v <= t_head)
    burned_needle = sum(1 for v in needle if v <= t_head)
    assert burned_broad > 1.3 * burned_needle
    assert all(b <= a for a, b in zip(needle, broad))


def test_seasonal_foliar_moisture_moves_initiation():
    """A canopy whose initiation threshold the surface fire meets at the
    spring-dip FMC (85 %) but not at 120 % (late summer): seasonal FMC on a
    spring day crowns, on an autumn day does not; the constant 100 % sits
    in between."""
    import calendar
    spring = float(calendar.timegm((2026, 5, 20, 12, 0, 0)))
    autumn = float(calendar.timegm((2026, 9, 25, 12, 0, 0)))
    tile = _conifer_tile(fuel=161.0, cbh=1.25, cbd=0.12, cc=60.0, ch=18.0)
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 5)
    res = {}
    for name, t0 in (("spring", spring), ("autumn", autumn)):
        wx = _wx(ign, 28.0, rh=12.0, temp=35.0, t0=t0)
        trace = {}
        spread.compute_arrival(tile, [ign], wx, t0, _on(
            crown_foliar_moisture_seasonal=True, horizon_h=4.0),
            crown_trace=trace)
        res[name] = set(trace.values())
    d0 = rothermel.fbp_fmc_min_day(39.5, -106.5, 2500.0)
    assert 120 <= d0 <= 170          # the spring dip falls in May-June here
    assert rothermel.CROWN_NONE not in res["spring"]
    assert res["autumn"] == {rothermel.CROWN_NONE}


# ---------------------------------------------------------------------------
# 4. memo exact, deterministic, re-anchor continuous
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {},
    {"spread_any_angle": True},
    {"edge_weather_piecewise": True},
    {"spotting_enabled": True, "spot_fire_size": True},
    {"crown_foliar_moisture_seasonal": True, "slope_phi_vector": True},
    {"grass_ros_floor_frac": 0.1, "ros_floor_extinction_gate": True,
     "spread_any_angle": True, "spotting_enabled": True},
])
def test_memo_equals_reference_with_the_knob_on(monkeypatch, extra):
    """memo = WGRESP_NO_ROS_MEMO=1 reference with crown fire on: mosaic
    canopy, veering weather, a stale perimeter, a direction offset and wind
    scale, every other physics knob in turn."""
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = _veer_wx(ign)
    ring = [geo.destination(geo.rc_to_latlon(tile.spec, 10, 10), b, 250.0)
            for b in range(0, 360, 30)]
    cfg = _on(horizon_h=24.0, **extra)
    kw = dict(perimeter=[ring], perimeter_at_s=T0 - 2 * HOUR,
              dir_offset_deg=17.3, wind_scale=1.1, incident_id="memo")
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    t1, t2 = {}, {}
    memo = spread.compute_arrival(tile, [ign], wx, T0, cfg, crown_trace=t1,
                                  **kw)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], wx, T0, cfg, crown_trace=t2,
                                 **kw)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert memo.data == ref.data
    assert t1 == t2
    assert rothermel.CROWN_ACTIVE in t1.values() or \
        rothermel.CROWN_PASSIVE in t1.values()


def test_memo_exact_with_zones_and_hour_scale(monkeypatch):
    """Weather zones and the per-hour ROS multiplier with crown fire on:
    memo = reference, deterministic."""
    from wgresponse.contracts import WeatherField
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    a = _wx(ign, 20.0, wdir=281.0)
    b = _wx(ign, 30.0, wdir=250.0, rh=8.0)
    n = tile.spec.nrows * tile.spec.ncols
    zone_of = bytes(0 if (i % tile.spec.ncols) < 20 else 1 for i in range(n))
    wf = WeatherField(spec=tile.spec, points=[], forecasts=[a, b],
                      zone_of=zone_of)
    cfg = _on(horizon_h=24.0)
    kw = dict(weather_field=wf, hour_ros_scale=[0.7, 1.3, 1.0, 0.5])
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    m1 = spread.compute_arrival(tile, [ign], a, T0, cfg, **kw)
    m2 = spread.compute_arrival(tile, [ign], a, T0, cfg, **kw)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], a, T0, cfg, **kw)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert m1.data == m2.data == ref.data


def test_t0_shift_shifts_every_arrival():
    """Never wall-clock: shifting t0 and the forecast by 24 h shifts every
    arrival by exactly 24 h (constant FMC; spotting with the size law on)."""
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    cfg = _on(spotting_enabled=True, spot_fire_size=True, horizon_h=10.0)
    a = spread.compute_arrival(tile, [ign], _wx(ign, 25.0), T0, cfg,
                               incident_id="s").data
    day = 86400.0
    b = spread.compute_arrival(tile, [ign], _wx(ign, 25.0, t0=T0 + day),
                               T0 + day, cfg, incident_id="s").data
    assert [x + day if math.isfinite(x) else x for x in a] == b


def test_ensemble_quantiles_ordered_with_crown_fire():
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    ens = spread.ensemble_arrival(tile, [ign], _wx(ign, 22.0), T0,
                                  _on(ensemble_n=5, horizon_h=12.0,
                                      model_error_sd_log=0.5),
                                  incident_id="ens")
    nom = ens.members[0].data
    for i in range(len(nom)):
        assert ens.p10.data[i] <= ens.p50.data[i] <= ens.p90.data[i]
        assert ens.p10.data[i] <= nom[i]


@pytest.mark.parametrize("any_angle", [False, True])
def test_noop_reanchor_is_the_continuous_run(any_angle):
    """Re-solving a crowning, spotting fire (size law + torching reach) from
    its burn at six instants under unchanged weather — kept, kept_wx, the
    embers aloft and (any-angle) the anchors — IS the continuous run, cell
    for cell."""
    tile = _mosaic()
    ign = geo.rc_to_latlon(tile.spec, 22, 3)
    wx = _wx(ign, 24.0, wdir=265.0)
    cfg = _on(spotting_enabled=True, spot_fire_size=True,
              spread_any_angle=any_angle, horizon_h=8.0)
    log, alog = [], {}
    cont = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="re",
                                  ember_log=log, anchor_log=alog).data
    t_hi = T0 + cfg.horizon_h * HOUR
    for k in range(1, 7):
        t_a = T0 + k * 900.0
        kept, aloft = _history(cont, t_a, log)
        kw = dict(incident_id="re", seed_spotting=False, kept=kept,
                  kept_wx=[(T0, wx.at(T0))], embers_aloft=aloft)
        if any_angle:
            kw["kept_anchor"] = alog
        got = spread.compute_arrival(tile, [], wx, t_a, cfg, **kw).data
        assert _diff_below(got, cont, t_hi) == [], k
        assert _burned(got, t_a) == set(kept), k


# ---------------------------------------------------------------------------
# 5. embers: the torching reach and its minimum fire size
# ---------------------------------------------------------------------------

def test_torching_reach_floor_and_minimum_fire_size():
    """With spotting and the size law, every ember a crowning cell throws
    has reach max(size-scaled Albini, torching distance) once its fire
    covers _TORCH_MIN_FIRE_HA, the size-scaled reach alone before."""
    tile = _conifer_tile(nrows=61, ncols=121, cell_m=90.0, cbh=0.5,
                         cbd=0.16, cc=65.0, ch=22.0)
    ign = geo.rc_to_latlon(tile.spec, 30, 5)
    wx = _wx(ign, 30.0, rh=10.0, temp=34.0)
    cfg = _on(spotting_enabled=True, spot_fire_size=True, horizon_h=3.0)
    trace = []
    spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="tr",
                           spot_trace=trace)
    cell_ha = 90.0 * 90.0 / 1e4
    d_torch = rothermel.torching_spot_max_m(22.0, (30.0 / MPH) / 1.15, 22.0)
    small = big = 0
    for (idx, tu, mean, u20, drawn, reach, cells, land, tv) in trace:
        intensity = (mean / (spread._SPOT_A * u20 ** spread._SPOT_WS_EXP)) \
            ** (1.0 / spread._SPOT_FLIN_EXP)
        scaled = spread._albini_surface_max_spot_m(intensity, u20) * (
            (cells * cell_ha) / cfg.spot_fire_size_ref_ha) ** cfg.spot_fire_size_exp
        if cells * cell_ha >= spread._TORCH_MIN_FIRE_HA:
            assert reach == pytest.approx(max(scaled, d_torch), rel=1e-9)
            big += 1
        else:
            assert reach == pytest.approx(scaled, rel=1e-9)
            small += 1
    assert small >= 1 and big >= 10


# ---------------------------------------------------------------------------
# 6. crew-safety corridor: crown fuel joins the fast erosion tier
# ---------------------------------------------------------------------------

def test_tiered_erosion_puts_crown_fuel_in_the_fast_tier():
    """cfg.crown_fire + the canopy: a timber cell holding crown fuel erodes
    at the FAST radius (550 m) instead of the slow one (150 m) — never a
    thinner shield; the knob off (or no canopy) is byte-identical."""
    tile = _conifer_tile(nrows=41, ncols=41, cell_m=90.0, fuel=183.0)
    spec = tile.spec
    p10 = Raster(spec, [math.inf] * (spec.nrows * spec.ncols))
    p10.set(20, 20, T0)
    off_cfg = _cfg(corridor_erosion_tiered=True)
    on_cfg = _on(corridor_erosion_tiered=True)
    base = spread.tiered_eroded_p10(p10, tile.fuel, off_cfg)
    assert spread.tiered_eroded_p10(p10, tile.fuel, off_cfg,
                                    canopy=tile.canopy).data == base.data
    assert spread.tiered_eroded_p10(p10, tile.fuel, on_cfg).data == base.data
    wide = spread.tiered_eroded_p10(p10, tile.fuel, on_cfg,
                                    canopy=tile.canopy)
    n_base = sum(1 for v in base.data if v == T0)
    n_wide = sum(1 for v in wide.data if v == T0)
    assert n_wide > n_base
    assert all(w <= b for w, b in zip(wide.data, base.data))


# ---------------------------------------------------------------------------
# 7. config, tile format, LfpsTerrain canopy, the committed tiles
# ---------------------------------------------------------------------------

def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert cfg.crown_fire is False
    assert cfg.crown_foliar_moisture_pct == 100.0
    assert cfg.crown_foliar_moisture_seasonal is False
    assert validate_config(cfg) == []
    for bad in (49.0, 301.0, float("nan")):
        probs = validate_config(dataclasses.replace(
            cfg, crown_foliar_moisture_pct=bad))
        assert any("crown_foliar_moisture_pct" in p for p in probs)
    assert any("crown_fire must be a bool" in p for p in validate_config(
        dataclasses.replace(cfg, crown_fire=1)))


def test_ascii_round_trip_with_and_without_canopy(tmp_path):
    tile = _mosaic(n=13)
    path = tmp_path / "c.wgt"
    terrain.save_ascii_tile(tile, str(path))
    back = terrain.load_ascii_tile(str(path))
    for name in ("cbh_m", "cbd_kgm3", "cc_pct", "ch_m"):
        assert getattr(back.canopy, name).data == getattr(tile.canopy, name).data
    assert back.canopy.source == "test"
    assert back.fuel.data == tile.fuel.data
    # the terrain part of the file is byte for byte the canopy-less file
    bare = dataclasses.replace(tile, canopy=None)
    p2 = tmp_path / "b.wgt"
    terrain.save_ascii_tile(bare, str(p2))
    with_lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                  if not ln.startswith("canopy_source ")]
    cut = with_lines.index("CBH_M")
    assert with_lines[:cut] == p2.read_text(encoding="utf-8").splitlines()
    assert terrain.load_ascii_tile(str(p2)).canopy is None


def test_partial_canopy_rejected(tmp_path):
    tile = _mosaic(n=7)
    path = tmp_path / "p.wgt"
    terrain.save_ascii_tile(tile, str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    cut = lines.index("CH_M")
    path.write_text("\n".join(lines[:cut]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="partial canopy"):
        terrain.load_ascii_tile(str(path))


def test_attach_canopy_requires_the_tile_grid():
    tile = grass_tile(nrows=9, ncols=9)
    other = GridSpec(north_lat=1.0, west_lon=1.0, cell_m=90.0, nrows=9,
                     ncols=9)
    with pytest.raises(ValueError):
        terrain.attach_canopy(tile, _canopy(other, 1.0, 0.1, 50.0, 10.0))


class _FakeLfps:
    """Scripted LFPS: records every URL; jobs succeed at once; the zip
    carries a stub tif + tfw (the decode is monkeypatched)."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, headers=None, timeout_s=None):
        self.urls.append(url)
        if "/submit" in url:
            return 200, json.dumps({"jobId": f"j{len(self.urls)}"}).encode()
        if "/status" in url:
            return 200, json.dumps({"status": "Succeeded",
                                    "outputFile": "https://x/out.zip"}).encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.tif", b"TIF")
            zf.writestr("a.tfw", "0.001\n0\n0\n-0.001\n-106.5\n39.5\n")
        return 200, buf.getvalue()


def test_lfps_canopy_job_decodes_converts_and_caches(tmp_path, monkeypatch):
    """canopy_layer_list: tile() submits ONE more job with the canopy layers,
    converts LANDFIRE's integer encodings (CBH, CH m x 10; CBD kg/m3 x 100;
    CC %; <= 0 -> no canopy) and caches it; a second tile() is network-free.
    Without canopy_layer_list no canopy job and canopy=None."""
    np = pytest.importorskip("numpy")
    calls = {"n": 0}

    def fake_decode(_b):
        calls["n"] += 1
        a = np.zeros((4, 50, 50), dtype=np.int16)
        a[0, :, :] = 165          # terrain job: fuel / slope / aspect / elev
        a[1, :, :] = 0
        a[2, :, :] = -1
        a[3, :, :] = 2500
        if calls["n"] % 2 == 0:   # the canopy job's bands
            a[0, :, :] = 35       # CBH 3.5 m
            a[1, :, :] = 12       # CBD 0.12 kg/m3
            a[2, :, :] = 45       # CC 45 %
            a[3, :, :] = 185      # CH 18.5 m
            a[0, :5, :] = -9999   # nodata strip
        return a

    monkeypatch.setattr(terrain.LfpsTerrain, "_decode_geotiff",
                        staticmethod(fake_decode))
    from wgresponse.contracts import BBox
    bbox = BBox(west=-106.5, south=39.47, east=-106.46, north=39.5)
    http = _FakeLfps()
    lf = terrain.LfpsTerrain(cache_dir=str(tmp_path), http_get=http,
                             email="x@y", dep_probe=lambda: None,
                             sleep_fn=lambda s: None,
                             canopy_layer_list=terrain.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    t = lf.tile(bbox, 90.0)
    submits = [u for u in http.urls if "/submit" in u]
    assert len(submits) == 2
    assert "LF2024_CBH" in submits[1] and "LF2024_FBFM40" in submits[0]
    cp = t.canopy
    assert cp is not None and cp.source.startswith("lfps:LF2024_CBH")
    n = t.spec.nrows * t.spec.ncols
    vals = {(cp.cbh_m.data[i], cp.cbd_kgm3.data[i], cp.cc_pct.data[i],
             cp.ch_m.data[i]) for i in range(n)}
    assert (3.5, 0.12, 45.0, 18.5) in vals
    assert all(v[0] in (0.0, 3.5) for v in vals)
    assert os.path.exists(lf.canopy_cache_path(bbox, 90.0))
    http.urls.clear()
    again = lf.tile(bbox, 90.0)
    assert http.urls == []
    assert again.canopy.cbh_m.data == cp.cbh_m.data
    # without canopy layers: no canopy job, canopy=None
    http2 = _FakeLfps()
    plain = terrain.LfpsTerrain(cache_dir=str(tmp_path / "p"), http_get=http2,
                                email="x@y", dep_probe=lambda: None,
                                sleep_fn=lambda s: None)
    assert plain.tile(bbox, 90.0).canopy is None
    assert sum(1 for u in http2.urls if "/submit" in u) == 1
    assert plain.canopy_cache_path(bbox, 90.0) is None


def test_lfps_canopy_layer_list_must_name_four_layers():
    with pytest.raises(ValueError):
        terrain.LfpsTerrain(canopy_layer_list="LF2024_CBH;LF2024_CBD")


_PROV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "data", "tiles", "provenance_canopy.json")


def test_committed_tiles_carry_landfire_canopy():
    """Every standard simulator tile carries the LF2024 canopy layers on its
    grid, in physical ranges (CBH 0-10 m, CBD 0-0.5 kg/m3, CC 0-100 %, CH
    0-60 m), crown fuel mostly on timber, and provenance with the requests."""
    from tools.fetch_sim_tiles import LOCATIONS, tile_path
    if not os.path.exists(_PROV):
        pytest.skip("data/tiles/provenance_canopy.json missing")
    with open(_PROV, encoding="utf-8") as fh:
        prov = json.load(fh)
    for key, _name, _lat, _lon in LOCATIONS:
        path = tile_path(key)
        if not os.path.exists(path):
            pytest.skip(f"committed tile {path} missing")
        tile = terrain.load_ascii_tile(path)
        cp = tile.canopy
        assert cp is not None, key
        assert cp.source == "lfps:" + terrain.LfpsTerrain.DEFAULT_CANOPY_LAYERS
        for band in (cp.cbh_m, cp.cbd_kgm3, cp.cc_pct, cp.ch_m):
            assert band.spec == tile.spec
        assert 0.0 <= min(cp.cbh_m.data) and max(cp.cbh_m.data) <= 10.0
        assert 0.0 <= min(cp.cbd_kgm3.data) and max(cp.cbd_kgm3.data) <= 0.5
        assert 0.0 <= min(cp.cc_pct.data) and max(cp.cc_pct.data) <= 100.0
        assert 0.0 <= min(cp.ch_m.data) and max(cp.ch_m.data) <= 60.0
        rec = prov["tiles"][key]
        assert rec["status"] in ("fetched", "cached"), key
        by_cls = rec["summary"]["canopy_pct_by_fuel_class"]
        if by_cls.get("TU", 0.0) or by_cls.get("TL", 0.0):
            assert max(by_cls.get("TU", 0.0), by_cls.get("TL", 0.0)) > \
                by_cls.get("NB", 0.0), key


def test_committed_extended_tiles_carry_landfire_canopy():
    """Every committed extended fire-zone tile (<key>_ext.wgt) carries the
    same LF2024 canopy layers on its own grid, so an extended session crowns
    exactly like a standard one, with its provenance record."""
    from tools.fetch_sim_tiles import EXT_SUFFIX, LOCATIONS, tile_path
    if not os.path.exists(_PROV):
        pytest.skip("data/tiles/provenance_canopy.json missing")
    with open(_PROV, encoding="utf-8") as fh:
        prov = json.load(fh)
    seen = 0
    for key, _name, _lat, _lon in LOCATIONS:
        path = tile_path(key, EXT_SUFFIX)
        if not os.path.exists(path):
            continue
        seen += 1
        tile = terrain.load_ascii_tile(path)
        cp = tile.canopy
        assert cp is not None, key
        assert cp.source == "lfps:" + terrain.LfpsTerrain.DEFAULT_CANOPY_LAYERS
        for band in (cp.cbh_m, cp.cbd_kgm3, cp.cc_pct, cp.ch_m):
            assert band.spec == tile.spec
        assert 0.0 <= min(cp.cbd_kgm3.data) and max(cp.cbd_kgm3.data) <= 0.5
        assert max(cp.ch_m.data) > 0.0, key
        rec = prov["tiles"][key + EXT_SUFFIX]
        assert rec["status"] in ("fetched", "cached"), key
    if not seen:
        pytest.skip("no committed extended tiles")


# ---------------------------------------------------------------------------
# 8. live wiring: watch --crown-fire, the test world's terrain overlay
# ---------------------------------------------------------------------------

def test_watch_crown_fire_flag_arms_the_knob_and_the_canopy(tmp_path,
                                                            monkeypatch):
    """watch --crown-fire parses; the watch terrain provider fetches the
    LF2024 canopy layers only with it."""
    from types import SimpleNamespace
    from wgresponse import cli
    monkeypatch.setattr(terrain, "probe_live_decode_deps", lambda: None)
    aoi = "--aoi=-107,39,-106,40"
    args = cli._build_parser().parse_args(["watch", aoi, "--crown-fire"])
    assert args.crown_fire is True
    assert cli._build_parser().parse_args(["watch", aoi]).crown_fire is False
    base = {"synthetic_terrain": False, "live_terrain": False}
    on, notes = cli._watch_terrain(SimpleNamespace(crown_fire=True, **base),
                                   tmp_path)
    assert on.canopy_layer_list == terrain.LfpsTerrain.DEFAULT_CANOPY_LAYERS
    assert any("canopy layers" in n for n in notes)
    off, notes_off = cli._watch_terrain(SimpleNamespace(**base), tmp_path)
    assert off.canopy_layer_list is None
    assert not any("canopy layers" in n for n in notes_off)


def test_test_world_overlay_follows_the_live_canopy(tmp_path):
    """The test world's own LfpsTerrain fetches the live provider's canopy
    layers; a tile read from the live cache gets the live cached canopy."""
    from wgresponse import worlds
    from wgresponse.contracts import BBox
    live = terrain.LfpsTerrain(
        cache_dir=str(tmp_path / "live"),
        canopy_layer_list=terrain.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    overlay = worlds.test_terrain_for(live, str(tmp_path / "test"))
    assert overlay._own.canopy_layer_list == live.canopy_layer_list
    plain = worlds.test_terrain_for(terrain.LfpsTerrain(
        cache_dir=str(tmp_path / "p")), str(tmp_path / "t2"))
    assert plain._own.canopy_layer_list is None
    bbox = BBox(west=-106.5, south=39.49, east=-106.48, north=39.5)
    tile = terrain._grid_for_bbox(bbox, 90.0)
    t = TerrainTile(spec=tile, fuel=geo.make_raster(tile, fill=165.0),
                    slope_deg=geo.make_raster(tile, fill=0.0),
                    aspect_deg=geo.make_raster(tile, fill=-1.0),
                    elev_m=geo.make_raster(tile, fill=2000.0), source="lfps:x")
    os.makedirs(tmp_path / "live", exist_ok=True)
    terrain.save_ascii_tile(t, live.cache_path(bbox, 90.0))
    cp = _canopy(tile, 1.0, 0.1, 55.0, 15.0, source="lfps:canopy")
    terrain.save_ascii_canopy(cp, tile, live.canopy_cache_path(bbox, 90.0))
    got = overlay.tile(bbox, 90.0)
    assert got.canopy is not None
    assert got.canopy.cbd_kgm3.data == cp.cbd_kgm3.data


# ---------------------------------------------------------------------------
# 9. the simulator: Park City's timber ignition with the floor on grass only
# ---------------------------------------------------------------------------

def _park_city_session(**cfg):
    from wgresponse.simui import LOCATIONS, SimSession
    page = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
            "ros_floor_grass_only": True, "spot_fire_size": True}
    page.update(cfg)
    s = SimSession("park_city_ut", 40, 2, seed="scenario-park_city_windstorm",
                   cfg_overrides=page, roads_module=None)
    center = LOCATIONS["park_city_ut"].center
    for db in (0, 10, -10, 20, -20, 30, -30):          # the server's search
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, 225.0 + db, 7000.0 * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=225.0)
                return s
            except ValueError:
                continue
    raise AssertionError("no ignition")


def test_simulator_does_not_arm_crown_fire_by_default():
    from wgresponse.simui import SimSession
    s = SimSession("boulder_co", 2, 1, seed="crown-default",
                   cfg_overrides={"spotting_enabled": True}, roads_module=None)
    assert s._cfg.crown_fire is False


def test_simulator_park_city_timber_fire_runs_as_a_crown_fire():
    """The founder's Park City frame, floor on grass only, calibrated embers:
    the timber ignition crawls without crown fire (measured 2026-10-12: 10
    acres at 3 h) and runs as a crown fire with it (735 acres) — the
    nominal fire crowns actively, and the crew-safety audit stays clean."""
    from wgresponse.simui import DEFAULT_TILE_DIR
    if not (DEFAULT_TILE_DIR / "park_city_ut.wgt").exists():
        pytest.skip("real tile data/tiles/park_city_ut.wgt not present")
    acres = {}
    for crown in (False, True):
        s = _park_city_session(crown_fire=crown)
        st = None
        for _ in range(12):                             # 12 x 15 min = 3 h
            st = s.tick()
            assert st["scoreboard"]["crew_overruns"] == 0
        acres[crown] = st["fire_view"]["areas_km2"]["fire"] * 247.105
        f = s._fires[s._fire_order[0]]
        trace = {}
        spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                               s._cfg, incident_id=f.incident_id,
                               crown_trace=trace)
        assert (rothermel.CROWN_ACTIVE in trace.values()) is crown
    assert acres[False] < 60.0
    assert acres[True] > 300.0
