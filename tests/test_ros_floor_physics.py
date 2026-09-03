"""ROS-FLOOR PHYSICS (contracts ros_floor_extinction_gate /
ros_floor_grass_only), the ENSEMBLE MODEL-ERROR TERM (model_error_sd_log),
the LADDER MEDIAN BOUND (ensemble_p50_nominal_bound), the zero-ROS
relaxation fix and the Raster index guard — the tri-model review's
(docs/TRI_MODEL_REVIEW_2026-08-31.md) findings A3, A7, N4 and two code
findings. Every knob default-OFF is pinned byte-identical."""
import dataclasses
import hashlib
import math
import struct

import pytest

from wgresponse import geo, rothermel, spread
from wgresponse.contracts import (
    GridSpec,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
    seeded_rng,
)
from wgresponse.fuels import dead_fuel_moisture_frac

T0 = 1_750_000_000.0
NORTH, WEST = 39.5, -121.5
#: RH 95% at 5 C: dead 1-h moisture well above GR2's 15% extinction.
WET = dict(rh_pct=95.0, temp_c=5.0)


def _tile(nrows=31, ncols=31, cell_m=100.0, fuel_code=102.0) -> TerrainTile:
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(
        spec=spec, fuel=geo.make_raster(spec, fill=float(fuel_code)),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=0.0),
        elev_m=geo.make_raster(spec, fill=100.0), source="test")


def _wx(loc, wind_mps=10.0, rh_pct=20.0, temp_c=30.0, hours=100):
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind_mps,
                             wind_gust_mps=wind_mps * 1.3, wind_dir_deg=270.0,
                             rh_pct=rh_pct, temp_c=temp_c)
               for i in range(hours)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


def _center(tile):
    return geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                            tile.spec.ncols // 2)


def _fp(raster) -> str:
    return hashlib.sha256(
        b"".join(struct.pack("<d", v) for v in raster.data)).hexdigest()


def _reached(raster) -> int:
    return sum(1 for v in raster.data if not math.isinf(v))


def _arr(tile, wx, **cfg_kw):
    cfg_kw.setdefault("ensemble_n", 1)
    return spread.compute_arrival(tile, [_center(tile)], wx, T0,
                                  ResponseConfig(**cfg_kw))


# ---------------------------------------------------------------------------
# A3: the extinction gate
# ---------------------------------------------------------------------------

def test_wet_grass_is_out_under_rothermel():
    fuel = spread._fuel_for_code(102)
    m = dead_fuel_moisture_frac(WET["temp_c"], WET["rh_pct"])
    assert m >= fuel.mx_dead_frac
    res = rothermel.surface_ros_mps(fuel, m, 0.30, 0.60, 5.0, 0.0)
    assert res.ros_mps == 0.0 and res.intensity_kwm == 0.0


def test_as_deployed_floor_walks_out_fuel_and_the_gate_holds_it():
    tile = _tile()
    wx = _wx(_center(tile), **WET)
    ungated = _arr(tile, wx, grass_ros_floor_frac=0.10)
    # the review's A3: fuel Rothermel says is OUT walks at 0.1*U10
    assert _reached(ungated) > 1
    gated = _arr(tile, wx, grass_ros_floor_frac=0.10,
                 ros_floor_extinction_gate=True)
    assert _reached(gated) == 1          # only the seed cell


def test_gate_is_identity_where_fuel_can_burn():
    tile = _tile()
    wx = _wx(_center(tile))              # 20% RH, 30 C: well below extinction
    base = _arr(tile, wx, grass_ros_floor_frac=0.10)
    gated = _arr(tile, wx, grass_ros_floor_frac=0.10,
                 ros_floor_extinction_gate=True)
    assert _fp(base) == _fp(gated)


def test_floored_helper_semantics():
    # out fuel (ros 0): as-deployed walks it with zero intensity; gate holds
    assert spread._floored(0.0, 0.0, 1.0, False) == (1.0, 0.0)
    assert spread._floored(0.0, 0.0, 1.0, True) == (0.0, 0.0)
    # binding floor on live fuel: gate scales Byram intensity linearly
    assert spread._floored(0.5, 100.0, 1.0, False) == (1.0, 100.0)
    assert spread._floored(0.5, 100.0, 1.0, True) == (1.0, 200.0)
    # non-binding floor: identity either way
    assert spread._floored(2.0, 100.0, 1.0, False) == (2.0, 100.0)
    assert spread._floored(2.0, 100.0, 1.0, True) == (2.0, 100.0)


def test_default_off_knobs_are_byte_identical_with_floor_on():
    tile = _tile()
    wx = _wx(_center(tile), wind_mps=4.0)
    a = _arr(tile, wx, grass_ros_floor_frac=0.10)
    b = _arr(tile, wx, grass_ros_floor_frac=0.10,
             ros_floor_extinction_gate=False, ros_floor_grass_only=False)
    assert _fp(a) == _fp(b)


# ---------------------------------------------------------------------------
# A3 (class set): grass-only floor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code, floored_under_grass_only",
                         [(102, True), (122, True), (183, False), (163, False)])
def test_grass_only_floors_grass_classes_and_leaves_timber(
        code, floored_under_grass_only):
    tile = _tile(fuel_code=float(code))
    wx = _wx(_center(tile), wind_mps=4.0)
    nofloor = _arr(tile, wx)
    floor = _arr(tile, wx, grass_ros_floor_frac=0.10)
    assert _fp(floor) != _fp(nofloor)    # the floor binds on every class here
    grass_only = _arr(tile, wx, grass_ros_floor_frac=0.10,
                      ros_floor_grass_only=True)
    if floored_under_grass_only:
        assert _fp(grass_only) == _fp(floor)
    else:
        assert _fp(grass_only) == _fp(nofloor)


# ---------------------------------------------------------------------------
# code finding: zero-ROS fuel never relaxes a neighbour (30 m cells)
# ---------------------------------------------------------------------------

def test_zero_ros_never_relaxes_a_neighbour_at_30m_cells():
    tile = _tile(cell_m=30.0)
    wx = _wx(_center(tile), **WET)
    arr = _arr(tile, wx)                  # no floor: pure Rothermel, all OUT
    # pre-fix: 30 m / 1e-4 m/s = 83 h < the 96 h horizon, so the seed's
    # neighbours "burned" and the front tunnelled through out fuel.
    assert _reached(arr) == 1


# ---------------------------------------------------------------------------
# A7: the model-error term
# ---------------------------------------------------------------------------

def test_fourth_stratum_leaves_the_legacy_three_dimension_law_untouched():
    m = 5
    draws = spread._stratified_member_draws("inc-x", m)
    assert all(len(d) == 4 for d in draws)
    keys = []
    for k in range(1, m + 1):
        rng = seeded_rng("ensemble-v2", "inc-x", k)
        keys.append((rng.random(), rng.random(), rng.random()))
    for d in range(3):
        order = sorted(range(m), key=lambda i: keys[i][d])
        for rank, i in enumerate(order):
            assert draws[i][d] == 2.0 * (rank + 0.5) / m - 1.0
    strata = [2.0 * (j + 0.5) / m - 1.0 for j in range(m)]
    assert sorted(d[3] for d in draws) == strata


def test_model_error_zero_is_byte_identical():
    tile = _tile()
    wx = _wx(_center(tile))
    a = spread.ensemble_arrival(tile, [_center(tile)], wx, T0,
                                ResponseConfig(ensemble_n=4), incident_id="i1")
    b = spread.ensemble_arrival(tile, [_center(tile)], wx, T0,
                                ResponseConfig(ensemble_n=4,
                                               model_error_sd_log=0.0),
                                incident_id="i1")
    for ra, rb in zip(a.members + [a.p10, a.p50, a.p90],
                      b.members + [b.p10, b.p50, b.p90]):
        assert _fp(ra) == _fp(rb)


def test_model_error_widens_the_band_and_keeps_p10_pessimistic():
    """Isolated term: input perturbations zeroed, so members differ ONLY by
    the model-error scale. (With direction perturbation on, a faster but
    rotated member need not beat the nominal run on the head axis — the
    review's N4 finding — so that combined case proves nothing here.)"""
    tile = _tile(nrows=41, ncols=61)
    ign = _center(tile)
    wx = _wx(ign)
    zero_inputs = dict(ensemble_n=6, wind_perturb_frac=0.0,
                       moisture_perturb_frac=0.0, dir_perturb_deg=0.0)
    narrow = spread.ensemble_arrival(tile, [ign], wx, T0,
                                     ResponseConfig(**zero_inputs),
                                     incident_id="i2")
    wide = spread.ensemble_arrival(tile, [ign], wx, T0,
                                   ResponseConfig(model_error_sd_log=0.5,
                                                  **zero_inputs),
                                   incident_id="i2")
    # nominal member never carries the term
    assert _fp(narrow.members[0]) == _fp(wide.members[0])
    # the middle stratum (z = 0, scale exp(0) = 1.0) is the nominal run,
    # byte for byte — the knob multiplies nothing at scale 1.0
    draws = spread._stratified_member_draws("i2", 5)
    mid = 1 + [k for k, d in enumerate(draws) if abs(d[3]) < 1e-12][0]
    assert _fp(wide.members[mid]) == _fp(wide.members[0])
    r, c = tile.spec.nrows // 2, tile.spec.ncols // 2 + 12   # downwind probe
    i = r * tile.spec.ncols + c
    nom = wide.members[0].data[i]
    assert not math.isinf(nom)
    # zero input perturbation => the legacy band has NO width at all
    assert narrow.p90.data[i] - narrow.p10.data[i] == 0.0
    assert wide.p10.data[i] < nom < wide.p90.data[i]
    assert wide.p10.data[i] <= wide.p50.data[i] <= wide.p90.data[i]
    arrivals = [mem.data[i] for mem in wide.members]
    assert min(arrivals) < nom and max(arrivals) > nom


def test_model_error_scale_is_lognormal_median_unbiased():
    m = 5
    draws = spread._stratified_member_draws("i3", m)
    from statistics import NormalDist
    zs = sorted(NormalDist().inv_cdf(0.5 * (d[3] + 1.0)) for d in draws)
    assert abs(zs[m // 2]) < 1e-12               # middle stratum = the median
    assert zs[0] == pytest.approx(-zs[-1])       # symmetric in log space


# ---------------------------------------------------------------------------
# N4: the ladder median bound
# ---------------------------------------------------------------------------

def test_p50_nominal_bound_binds_on_the_head_axis_and_is_off_by_default():
    tile = _tile(nrows=41, ncols=61)
    ign = _center(tile)
    wx = _wx(ign)
    off = spread.ensemble_arrival(tile, [ign], wx, T0,
                                  ResponseConfig(ensemble_n=6), incident_id="i4")
    on = spread.ensemble_arrival(tile, [ign], wx, T0,
                                 ResponseConfig(ensemble_n=6,
                                                ensemble_p50_nominal_bound=True),
                                 incident_id="i4")
    nom = on.members[0].data
    assert _fp(on.p10) == _fp(off.p10) and _fp(on.p90) == _fp(off.p90)
    binds = 0
    for i in range(len(nom)):
        expect = off.p50.data[i] if off.p50.data[i] <= nom[i] else nom[i]
        assert on.p50.data[i] == expect
        assert on.p10.data[i] <= on.p50.data[i] <= on.p90.data[i]
        if off.p50.data[i] > nom[i]:
            binds += 1
    assert binds > 0     # the review's N4: the raw median trails the nominal run


# ---------------------------------------------------------------------------
# code finding: Raster index guard
# ---------------------------------------------------------------------------

def test_raster_rejects_out_of_grid_indices():
    r = geo.make_raster(GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=100.0,
                                 nrows=3, ncols=3))
    r.set(2, 2, 1.0)
    assert r.get(2, 2) == 1.0
    for rr, cc in ((-1, 0), (0, -1), (3, 0), (0, 3)):
        with pytest.raises(IndexError):
            r.get(rr, cc)
        with pytest.raises(IndexError):
            r.set(rr, cc, 1.0)
