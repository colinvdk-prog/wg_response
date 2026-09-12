"""Tests for the two tri-model-review physics knobs in wgresponse.spread —
cfg.edge_weather_piecewise and cfg.slope_phi_vector.

Evidence: docs/TRI_MODEL_REVIEW_2026-08-31.md "Additional confirmed defects
(code aspect)"; scratch_review/rvluna_REPORT.txt §2 ("weather changes are not
integrated across an edge": 46.3 min constant offset in a step case, timber
flank edges spanning 25 h on one weather hour) and
scratch_review/rv2code_VERDICTS.md finding 5 ("slope/wind in speed-space":
aligned +21.6..+31.0% fast, OPPOSED -11.1..-33.5% SLOW — the anti-safe
downslope Santa Ana geometry — and EXACTLY ZERO on flat terrain).

Both knobs default OFF and must then be byte-identical to the pre-knob engine;
the OFF gate here re-uses tests/test_spread.py's own pinned legacy
fingerprints, so this file cannot drift from that pin. Tiles are small
(41x41 @ 100 m, ensemble_n=1) so the whole file runs in seconds.
"""
from __future__ import annotations

import math

import pytest

from tests.test_spread import (
    T0,
    _LEGACY_FINGERPRINTS,
    _fingerprint,
    _spot_setup,
    center_ignition,
    const_wx,
    grass_tile,
    make_cfg,
)
from wgresponse import rothermel, spread
from wgresponse.contracts import (
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

# Wind blows FROM 270 => the head runs toward 090 (EAST) on every tile here.
_WDIR = 270.0
# contracts pins aspect_deg as the DOWNSLOPE-facing direction, so upslope =
# aspect + 180: aspect 270 puts upslope EAST (aligned with the head), aspect 90
# puts upslope WEST (directly OPPOSED to the wind), aspect 180 puts upslope
# NORTH (cross-slope).
_ASPECT_UPSLOPE_EAST = 270.0
_ASPECT_UPSLOPE_WEST = 90.0
_ASPECT_UPSLOPE_NORTH = 180.0

_MID = 20                      # centre row/col of the default 41x41 tile


def _cfg(**kw) -> ResponseConfig:
    kw.setdefault("ensemble_n", 1)
    return ResponseConfig(**kw)


def _mins(arr: Raster, r: int, c: int) -> float:
    """Arrival at (r, c) as MINUTES after T0 (inf when UNREACHED)."""
    v = arr.get(r, c)
    return math.inf if math.isinf(v) else (v - T0) / 60.0


def _step_wx(loc: LatLon, before_mps: float, after_mps: float,
             step_hour: int = 2, hours: int = 200) -> WeatherForecast:
    """Constant westerly whose SPEED steps at T0 + step_hour hours."""
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0,
                      wind_speed_mps=(before_mps if i < step_hour else after_mps),
                      wind_gust_mps=(before_mps if i < step_hour else after_mps) * 1.3,
                      wind_dir_deg=_WDIR, rh_pct=20.0, temp_c=30.0)
        for i in range(hours)])


def _soak_wx(loc: LatLon, dead_hours: set, wind_mps: float = 3.0,
             hours: int = 200) -> WeatherForecast:
    """Dry westerly except in `dead_hours`, which are cold + saturated so the
    dead-fuel moisture (0.271) sits above GR2's moisture of extinction (0.15)
    and Rothermel returns ROS = 0 — a weather hour that cannot carry fire."""
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind_mps,
                      wind_gust_mps=wind_mps * 1.3, wind_dir_deg=_WDIR,
                      rh_pct=100.0 if i in dead_hours else 20.0,
                      temp_c=5.0 if i in dead_hours else 30.0)
        for i in range(hours)])


def _sloped_tile(slope_deg: float, aspect_deg: float,
                 fuel_code: float = 102.0) -> TerrainTile:
    return grass_tile(slope_deg=slope_deg, aspect_deg=aspect_deg,
                      fuel_code=fuel_code)


def _ab(tile, ign, wx, knob: str, **cfg_kw):
    """(off_raster, on_raster) for one knob on identical inputs."""
    off = spread.compute_arrival(tile, [ign], wx, T0, _cfg(**cfg_kw))
    on = spread.compute_arrival(tile, [ign], wx, T0,
                                _cfg(**dict(cfg_kw, **{knob: True})))
    return off, on


# ---------------------------------------------------------------------------
# (a) BOTH KNOBS OFF => byte-identical to the engine without them
# ---------------------------------------------------------------------------

def test_knobs_off_are_byte_identical_to_the_pinned_legacy_rasters():
    """THE OFF GATE. Passing edge_weather_piecewise=False / slope_phi_vector=
    False explicitly must produce exactly the raster the knobs-absent config
    produces, and that raster must still hash to tests/test_spread.py's pinned
    pre-knob fingerprints — A (flat + wind), B (pure slope, no wind), C
    (spotting over a barrier), D (wind switch). Any float that moves in the
    OFF branch fails here."""
    base = ResponseConfig(ensemble_n=4)
    explicit = ResponseConfig(ensemble_n=4, edge_weather_piecewise=False,
                              slope_phi_vector=False)
    # A: flat GR2, 10 m/s FROM 270
    tile = grass_tile()
    ign = center_ignition(tile)
    a_base = spread.compute_arrival(tile, [ign], const_wx(ign), T0, base)
    a_expl = spread.compute_arrival(tile, [ign], const_wx(ign), T0, explicit)
    assert a_expl.data == a_base.data
    assert _fingerprint(a_expl) == _LEGACY_FINGERPRINTS["A_flat_wind"]
    # B: 30 deg slope facing west (upslope east), zero wind — the pure-slope
    # scenario, i.e. exactly the case where phi-vector and speed-space agree
    # by construction (one term is zero)
    tile = _sloped_tile(30.0, 270.0)
    ign = center_ignition(tile)
    wx0 = const_wx(ign, wind_mps=0.0)
    b_base = spread.compute_arrival(tile, [ign], wx0, T0, base)
    b_expl = spread.compute_arrival(tile, [ign], wx0, T0, explicit)
    assert b_expl.data == b_base.data
    assert _fingerprint(b_expl) == _LEGACY_FINGERPRINTS["B_upslope_nowind"]
    # C: functional spotting over a 2-col NB barrier
    tile, ign, wx = _spot_setup()
    c_base = spread.compute_arrival(
        tile, [ign], wx, T0, ResponseConfig(ensemble_n=1, spotting_enabled=True))
    c_expl = spread.compute_arrival(
        tile, [ign], wx, T0,
        ResponseConfig(ensemble_n=1, spotting_enabled=True,
                       edge_weather_piecewise=False, slope_phi_vector=False))
    assert c_expl.data == c_base.data
    assert _fingerprint(c_expl) == _LEGACY_FINGERPRINTS["C_spotting"]
    # D: wind veers 270 -> 180 at T0+2h (hour buckets really differ, so the
    # piecewise branch would bite if it were live)
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = WeatherForecast(loc=ign, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=10.0,
                      wind_gust_mps=13.0,
                      wind_dir_deg=270.0 if i < 2 else 180.0,
                      rh_pct=20.0, temp_c=30.0) for i in range(100)])
    d_base = spread.compute_arrival(tile, [ign], wx, T0, base)
    d_expl = spread.compute_arrival(tile, [ign], wx, T0, explicit)
    assert d_expl.data == d_base.data
    assert _fingerprint(d_expl) == _LEGACY_FINGERPRINTS["D_wind_switch"]


def test_knobs_off_leave_the_ensemble_byte_identical():
    """The OFF gate through ensemble_arrival too (quantiles included) — the
    knobs are read inside compute_arrival, so every member must be untouched."""
    tile = _sloped_tile(20.0, _ASPECT_UPSLOPE_WEST)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=6.0, wind_dir_deg=_WDIR, hours=120)
    base = spread.ensemble_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=3),
                                   incident_id="off-gate")
    expl = spread.ensemble_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=3, edge_weather_piecewise=False,
                 slope_phi_vector=False),
        incident_id="off-gate")
    for k, (a, b) in enumerate(zip(base.members, expl.members)):
        assert a.data == b.data, k
    assert (base.p10.data, base.p50.data, base.p90.data) == \
           (expl.p10.data, expl.p50.data, expl.p90.data)


# ---------------------------------------------------------------------------
# (b) edge_weather_piecewise
# ---------------------------------------------------------------------------

def test_piecewise_step_up_makes_far_downwind_cells_arrive_earlier():
    """Wind steps UP 3 -> 9 m/s at T0+2h. The frozen law prices the edge that
    is in flight at the step with the 3 m/s departure hour for its whole run;
    piecewise finishes it at 9 m/s, so every cell DOWNWIND of the step front
    arrives EARLIER — and by the same constant offset, because exactly one
    edge straddles the boundary and the saving propagates downstream (the
    review's "46.3 min constant offset" signature)."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = _step_wx(ign, 3.0, 9.0, step_hour=2)
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise")
    # cells reached before the step are untouched (no hour boundary crossed)
    for c in (_MID + 2, _MID + 4):
        assert _mins(off, _MID, c) < 120.0
        assert on.get(_MID, c) == off.get(_MID, c)
    # cells whose traversal straddles the step arrive earlier, by one offset
    deltas = []
    for c in (_MID + 8, _MID + 12, _MID + 16, _MID + 20):
        d = _mins(on, _MID, c) - _mins(off, _MID, c)
        assert math.isfinite(d)
        assert d < -5.0, (c, d)          # measured -11.34 min
        deltas.append(d)
    assert max(deltas) - min(deltas) < 1e-6      # one constant offset


def test_piecewise_step_down_makes_far_downwind_cells_arrive_later():
    """The mirror image: wind steps DOWN 3 -> 1 m/s at T0+2h. The frozen law
    keeps spending the fast 3 m/s departure hour after the wind has died;
    piecewise finishes the in-flight edge at 1 m/s, so downwind cells arrive
    LATER. Late is the honest answer here, and the knob has to be willing to
    give it — a fix that could only ever pull arrivals earlier would be a
    bias, not an integration."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = _step_wx(ign, 3.0, 1.0, step_hour=2)
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise")
    for c in (_MID + 2, _MID + 4):
        assert on.get(_MID, c) == off.get(_MID, c)
    deltas = []
    for c in (_MID + 8, _MID + 12, _MID + 16, _MID + 20):
        d = _mins(on, _MID, c) - _mins(off, _MID, c)
        assert math.isfinite(d)
        assert d > 10.0, (c, d)          # measured +33.46 min
        deltas.append(d)
    assert max(deltas) - min(deltas) < 1e-6


def test_piecewise_is_byte_identical_under_constant_weather():
    """THE PIECEWISE OFF-BY-CONSTRUCTION GATE. Crossing an hour boundary is
    not itself a change: when every hour carries the same weather the
    integrator's segment merge keeps ONE constant-rate run and solves it from
    the traversal's own start, so the result is `t_u + dist/r` in exactly the
    frozen law's float operations. Proven on two regimes that really do cross
    boundaries — fast grass (many multi-hour flank/backing edges) and TL3
    timber litter at 3 m/s, where even the FIRST edge out of the seed spans
    7+ hours (the review's 25-h flank-edge case)."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=6.0, wind_dir_deg=_WDIR, hours=300)
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise", horizon_h=240.0)
    assert max(v for v in off.data if math.isfinite(v)) > T0 + 3600.0
    assert on.data == off.data
    assert _fingerprint(on) == _fingerprint(off)

    tl = grass_tile(fuel_code=183.0)        # TL3 compact timber litter
    itl = center_ignition(tl)
    wtl = const_wx(itl, wind_mps=3.0, wind_dir_deg=_WDIR, hours=300)
    off_tl, on_tl = _ab(tl, itl, wtl, "edge_weather_piecewise", horizon_h=240.0)
    # ONE edge, seed -> east neighbour, already spans several weather hours
    assert _mins(off_tl, _MID, _MID + 1) > 4 * 60.0
    assert sum(1 for v in off_tl.data if math.isfinite(v)) > 50
    assert on_tl.data == off_tl.data
    assert _fingerprint(on_tl) == _fingerprint(off_tl)


def test_piecewise_actually_bites_when_an_hour_really_differs():
    """Guard against the previous test passing vacuously: the SAME tile and
    the SAME knob must move bits as soon as one crossed hour differs."""
    tile = grass_tile()
    ign = center_ignition(tile)
    off, on = _ab(tile, ign, _step_wx(ign, 3.0, 9.0), "edge_weather_piecewise")
    assert on.data != off.data


def test_piecewise_two_rate_edge_matches_hand_arithmetic():
    """INDEPENDENT ARITHMETIC CHECK, raster-only. The BACKING (west) edge out
    of the seed is one 100 m rook edge that takes 7487.16 s at a constant
    3 m/s, so it is still in flight when the wind changes at T0+1h.

    Recover the two directional rates from two CONSTANT-weather runs
    (r = 100 m / that run's west-neighbour time — no engine internals, no
    Rothermel re-derivation here), then predict the two-rate answer by hand:

        t = 3600 + (100 - r0*3600) / r1

    and require the knob to reproduce it. This pins the integrator's actual
    arithmetic — distance carried across the seam, not time — independently of
    every other test in this file. (Note r1 < r0 here: a stronger wind
    elongates the ellipse and makes the BACKING rate slower, so this case
    lands LATE, which is also why it exercises the honest-both-ways claim.)"""
    tile = grass_tile()
    ign = center_ignition(tile)
    dist_m = tile.spec.cell_m
    w0, w1 = 3.0, 7.0
    west = (_MID, _MID - 1)
    c0 = spread.compute_arrival(tile, [ign],
                                const_wx(ign, wind_mps=w0, wind_dir_deg=_WDIR,
                                         hours=300), T0, _cfg(horizon_h=240.0))
    c1 = spread.compute_arrival(tile, [ign],
                                const_wx(ign, wind_mps=w1, wind_dir_deg=_WDIR,
                                         hours=300), T0, _cfg(horizon_h=240.0))
    t_r0 = c0.get(*west) - T0
    t_r1 = c1.get(*west) - T0
    assert t_r0 > 3600.0                   # the edge really spans the boundary
    r0, r1 = dist_m / t_r0, dist_m / t_r1
    wx = WeatherForecast(loc=ign, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0,
                      wind_speed_mps=(w0 if i < 1 else w1),
                      wind_gust_mps=(w0 if i < 1 else w1) * 1.3,
                      wind_dir_deg=_WDIR, rh_pct=20.0, temp_c=30.0)
        for i in range(300)])
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise", horizon_h=240.0)
    assert off.get(*west) - T0 == pytest.approx(t_r0, abs=1e-6)   # frozen law
    predicted = 3600.0 + (dist_m - r0 * 3600.0) / r1
    assert on.get(*west) - T0 == pytest.approx(predicted, abs=1e-5)


def test_piecewise_stalls_across_a_zero_ros_hour_and_resumes_after_it():
    """DOCUMENTED SEMANTICS for a later hour whose ROS is 0: the traversal
    STALLS (no progress that hour) and resumes with the distance still
    unburned — it is not deleted, and it is not credited with creep.

    Setup: GR2, dry 3 m/s except hours 1-3, which are cold and saturated
    (m_dead 0.271 > GR2's 0.15 extinction => Rothermel ROS 0). The BACKING
    (west) neighbour of the seed needs 124.79 min at the hour-0 rate, so its
    traversal is in flight when the soak arrives. Hours 1-3 contribute exactly
    zero distance, and hour 4 is byte-identical weather to hour 0, so the
    arrival must land EXACTLY three hours later than the frozen law's — the
    cleanest possible statement of the stall rule."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = _soak_wx(ign, {1, 2, 3})
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise", horizon_h=240.0)
    back_off = off.get(_MID, _MID - 1)
    back_on = on.get(_MID, _MID - 1)
    assert math.isfinite(back_off) and math.isfinite(back_on)
    assert back_on - back_off == pytest.approx(3 * 3600.0, abs=1e-3)
    # the head-side neighbour completes inside hour 0 and is untouched
    assert on.get(_MID, _MID + 1) == off.get(_MID, _MID + 1)


def test_piecewise_keeps_the_departure_hour_zero_ros_rule():
    """The DEPARTURE hour's rule is unchanged: fuel that cannot carry fire at
    t_u still relaxes nothing (the knob only integrates hours AFTER the
    departure hour). With hour 0 soaked, the seed is recorded and the fire
    goes nowhere — identical with the knob ON and OFF."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = _soak_wx(ign, {0})
    off, on = _ab(tile, ign, wx, "edge_weather_piecewise", horizon_h=240.0)
    assert on.data == off.data
    assert sum(1 for v in on.data if math.isfinite(v)) == 1      # the seed only


def test_piecewise_respects_the_horizon():
    """A traversal that has not completed by t0 + horizon_h relaxes nothing:
    the integrator returns None rather than an arrival past the horizon, so no
    cell can carry a finite time later than the horizon end."""
    tile = grass_tile(fuel_code=183.0)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=3.0, wind_dir_deg=_WDIR, hours=300)
    arr = spread.compute_arrival(tile, [ign], wx, T0,
                                 _cfg(horizon_h=12.0,
                                      edge_weather_piecewise=True))
    end = T0 + 12.0 * 3600.0
    assert all((not math.isfinite(v)) or v <= end for v in arr.data)
    assert sum(1 for v in arr.data if math.isfinite(v)) > 1


# ---------------------------------------------------------------------------
# (c) slope_phi_vector
# ---------------------------------------------------------------------------

def test_phi_vector_on_flat_ground_is_byte_identical():
    """phi_s = 0 on flat ground, so the phi-vector sum degenerates to the wind
    term alone and must reproduce the speed-space result bit-for-bit — which
    is also why the review measured EXACTLY ZERO effect on the flat hindcast
    corpus (finding 5). Aspect is irrelevant when the slope is zero, so a
    non-zero aspect must not perturb it either."""
    for wind, aspect in ((0.0, 0.0), (5.0, 0.0), (12.0, 0.0), (7.0, 137.0)):
        tile = _sloped_tile(0.0, aspect)
        ign = center_ignition(tile)
        wx = const_wx(ign, wind_mps=wind, wind_dir_deg=_WDIR)
        off, on = _ab(tile, ign, wx, "slope_phi_vector")
        assert on.data == off.data, (wind, aspect)
        assert _fingerprint(on) == _fingerprint(off), (wind, aspect)


def test_phi_vector_with_wind_straight_up_the_slope_agrees_closely():
    """Wind blowing directly UP the slope (upslope EAST, wind FROM 270): the
    collinear case, where both conventions are summing two parallel
    contributions and must land in the same place. Measured 3.45% on a 5 deg
    slope under a 12 m/s wind.

    They are NOT identical, and cannot be: phi_w = C*U^B is SUPERLINEAR
    (B ~ 1.5 in grass), so adding the equivalent WINDS over-adds relative to
    adding the phi factors. The residual is therefore signed — OFF is the
    FASTER of the two on the aligned head axis, which is the conservative-fast
    polarity the CONVENTION NOTE in spread.py already records — and it GROWS
    with the smaller of the two terms (measured +35.6% at 30 deg / 5 m/s,
    where neither term is anywhere near zero). Both statements are asserted."""
    tile = _sloped_tile(5.0, _ASPECT_UPSLOPE_EAST)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=12.0, wind_dir_deg=_WDIR, hours=200)
    off, on = _ab(tile, ign, wx, "slope_phi_vector")
    for c in (_MID + 10, _MID + 15):
        a, b = _mins(off, _MID, c), _mins(on, _MID, c)
        assert math.isfinite(a) and math.isfinite(b)
        assert b / a == pytest.approx(1.0, rel=0.05), (c, a, b)
        assert b >= a                     # OFF is the fast one when aligned

    steep = _sloped_tile(30.0, _ASPECT_UPSLOPE_EAST)
    ign_s = center_ignition(steep)
    wx_s = const_wx(ign_s, wind_mps=5.0, wind_dir_deg=_WDIR, hours=300)
    off_s, on_s = _ab(steep, ign_s, wx_s, "slope_phi_vector", horizon_h=240.0)
    ratio = _mins(on_s, _MID, _MID + 15) / _mins(off_s, _MID, _MID + 15)
    assert ratio > 1.15                   # measured 1.356 — far from "a few %"


def test_phi_vector_makes_a_wind_opposing_the_slope_much_faster():
    """THE ANTI-SAFE CASE the review flagged (finding 5: OPPOSED slopes
    -11.1..-33.5% SLOW): wind FROM 270 blowing EAST, upslope pointing WEST
    (aspect 90) — the downslope-wind Santa Ana geometry. In speed space the
    slope-equivalent wind CANCELS most of the midflame wind before the
    superlinear phi_w is applied, which throws away far more spread than the
    honest phi-vector difference |phi_w - phi_s| does.

    ON must therefore be FASTER downwind (earlier arrival) by a margin well
    outside noise — measured off/on = 1.90x on a 25 deg slope at 5 m/s
    (GR2), i.e. the frozen convention is HALF the honest speed. This is the
    polarity that matters: OFF is late, and every crew deadline keys off
    early arrival."""
    tile = _sloped_tile(25.0, _ASPECT_UPSLOPE_WEST)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=5.0, wind_dir_deg=_WDIR, hours=300)
    off, on = _ab(tile, ign, wx, "slope_phi_vector", horizon_h=240.0)
    for c in (_MID + 5, _MID + 10, _MID + 15):
        a, b = _mins(off, _MID, c), _mins(on, _MID, c)
        assert math.isfinite(a) and math.isfinite(b)
        assert b < a, (c, a, b)                       # ON arrives earlier
        assert a / b > 1.11, (c, a / b)               # review's lower bound


def _gr2_bed():
    fuel = FUEL_TABLE[102]
    m_dead = max(0.01, dead_fuel_moisture_frac(30.0, 20.0))
    return fuel, rothermel.fuel_bed(fuel, m_dead, 0.30, 0.60)


def test_phi_vector_rotates_the_max_spread_bearing_toward_upslope():
    """CROSS-SLOPE geometry: wind blows EAST (090), upslope points NORTH (000,
    aspect 180), 30 deg slope under a light 3 m/s wind so the slope term
    dominates (phi_s 12.18 vs phi_w 6.81).

    Analytic half: the speed-space head bearing is atan2(U_wind, U_slope) and
    the phi-vector head bearing is atan2(phi_w, phi_s). Because
    phi_w/phi_s = (U_wind/U_slope)^B with B > 1, the phi bearing swings toward
    whichever term is LARGER — here the slope — so the max-spread axis rotates
    TOWARD UPSLOPE (measured 33.85 deg -> 29.21 deg off north, a 4.6 deg
    rotation). (Under a wind-dominated cross-slope the same law rotates the
    axis toward the WIND instead; the claim is "toward the dominant term",
    and this test pins the slope-dominated half the speed-space convention
    gets most wrong.)

    Engine half: the consequence in the arrival field — the upslope (north)
    probe gets relatively faster than the downwind (east) probe."""
    fuel, bed = _gr2_bed()
    u_wind = rothermel.midflame_wind_mps(3.0, fuel)
    u_slope = rothermel.effective_wind_from_bed(bed, 0.0, 30.0)
    phi_w = rothermel._phi_wind(bed, u_wind * rothermel._MPS_TO_FTMIN)
    phi_s = rothermel._phi_slope(bed, 30.0)
    brg_off = math.degrees(math.atan2(u_wind, u_slope)) % 360.0
    brg_on = math.degrees(math.atan2(phi_w, phi_s)) % 360.0
    assert phi_s > phi_w                      # slope-dominated by construction
    assert 0.0 < brg_on < brg_off < 90.0      # upslope is 0 deg, wind is 90 deg
    assert brg_off - brg_on > 2.0             # measured 4.64 deg

    tile = _sloped_tile(30.0, _ASPECT_UPSLOPE_NORTH)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=3.0, wind_dir_deg=_WDIR, hours=300)
    off, on = _ab(tile, ign, wx, "slope_phi_vector", horizon_h=240.0)
    n_off, n_on = _mins(off, _MID - 12, _MID), _mins(on, _MID - 12, _MID)
    e_off, e_on = _mins(off, _MID, _MID + 12), _mins(on, _MID, _MID + 12)
    assert all(math.isfinite(v) for v in (n_off, n_on, e_off, e_on))
    assert (n_on / e_on) < (n_off / e_off)    # measured 0.401 vs 0.502


def test_surface_ros_from_bed_phi_reduces_to_the_wind_only_seam():
    """The new rothermel helper is the same arithmetic as
    surface_ros_from_bed once the phi factors are combined, so feeding it the
    WIND-only phi must reproduce surface_ros_from_bed(bed, u, 0.0) exactly —
    every field, bit for bit. That is what makes the flat-ground byte-identity
    above structural rather than lucky."""
    _fuel, bed = _gr2_bed()
    for u in (0.0, 0.5, 2.0, 6.0, 13.7):
        ref = rothermel.surface_ros_from_bed(bed, u, 0.0)
        phi = rothermel._phi_wind(bed, u * rothermel._MPS_TO_FTMIN)
        got = rothermel.surface_ros_from_bed_phi(bed, phi)
        assert got == ref, u
    # slope-only likewise
    for slope in (0.0, 12.0, 30.0):
        ref = rothermel.surface_ros_from_bed(bed, 0.0, slope)
        got = rothermel.surface_ros_from_bed_phi(
            bed, rothermel._phi_slope(bed, slope))
        assert got == ref, slope
    # an unburnable bed yields the all-zero RosResult, like the other seams
    none_res = rothermel.surface_ros_from_bed_phi(None, 7.0)
    assert (none_res.ros_mps, none_res.effective_wind_mps,
            none_res.flame_length_m, none_res.intensity_kwm) == (0.0, 0.0, 0.0, 0.0)
    # phi is floored at 0: a negative magnitude cannot be faster than no-wind
    assert rothermel.surface_ros_from_bed_phi(bed, -3.0) == \
        rothermel.surface_ros_from_bed_phi(bed, 0.0)


# ---------------------------------------------------------------------------
# (d) memo parity + determinism with the knobs ON
# ---------------------------------------------------------------------------

def _mixed_tile(slope_deg: float, aspect_deg: float) -> TerrainTile:
    """Small mixed-fuel, mixed-terrain tile: four fuel classes and an integer
    slope/aspect pattern, so the ROS memo really does share and really does
    separate keys under both knobs."""
    tile = grass_tile(nrows=25, ncols=25)
    codes = (102.0, 122.0, 145.0, 183.0)
    for r in range(25):
        for c in range(25):
            tile.fuel.set(r, c, codes[((r // 6) + (c // 5)) % 4])
            tile.slope_deg.set(r, c, slope_deg + float((r + c) % 7))
            tile.aspect_deg.set(r, c, (aspect_deg + 45.0 * ((r // 3) % 8)) % 360.0)
    return tile


@pytest.mark.parametrize("knobs", [
    {"edge_weather_piecewise": True},
    {"slope_phi_vector": True},
    {"edge_weather_piecewise": True, "slope_phi_vector": True},
])
def test_knobs_on_memo_path_matches_the_reference_path(knobs, monkeypatch):
    """The B3 gate extended to the new knobs: with the knobs ON the memoised
    cell_params and the WGRESP_NO_ROS_MEMO=1 reference path must still produce
    byte-identical rasters. (The phi path builds its bed fresh in the
    reference branch; fuel_bed is pure, so the phi factors match exactly.)"""
    tile = _mixed_tile(10.0, 90.0)
    ign = center_ignition(tile)
    wx = WeatherForecast(loc=ign, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=3.0 + float(i % 7),
                      wind_gust_mps=(3.0 + float(i % 7)) * 1.3,
                      wind_dir_deg=(250.0 + 12.0 * (i % 9)) % 360.0,
                      rh_pct=15.0 + 4.0 * (i % 6), temp_c=20.0 + 2.0 * (i % 5))
        for i in range(200)])
    cfg = _cfg(horizon_h=96.0, **knobs)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    memo = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert sum(1 for v in memo.data if math.isfinite(v)) > 50
    assert memo.data == ref.data
    assert _fingerprint(memo) == _fingerprint(ref)


@pytest.mark.parametrize("knobs", [
    {"edge_weather_piecewise": True},
    {"slope_phi_vector": True},
    {"edge_weather_piecewise": True, "slope_phi_vector": True},
])
def test_knobs_on_runs_are_deterministic(knobs):
    """Same inputs, same bytes, run after run — the knobs add no wall-clock,
    no set/dict iteration order and no RNG to the arrival field."""
    tile = _mixed_tile(15.0, 90.0)
    ign = center_ignition(tile)
    wx = _step_wx(ign, 4.0, 11.0, step_hour=1, hours=120)
    cfg = _cfg(horizon_h=96.0, **knobs)
    first = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    for _ in range(2):
        again = spread.compute_arrival(tile, [ign], wx, T0, cfg)
        assert again.data == first.data
        assert _fingerprint(again) == _fingerprint(first)
    assert sum(1 for v in first.data if math.isfinite(v)) > 50


def test_both_knobs_together_move_the_field_and_stay_finite():
    """Sanity that the two knobs compose: on an opposed-slope tile with a
    stepping wind, turning both on produces a field that differs from the
    legacy one, from each knob alone, and still contains no NaN."""
    tile = _sloped_tile(25.0, _ASPECT_UPSLOPE_WEST)
    ign = center_ignition(tile)
    wx = _step_wx(ign, 4.0, 9.0, step_hour=2, hours=200)
    base = spread.compute_arrival(tile, [ign], wx, T0, _cfg(horizon_h=240.0))
    pw = spread.compute_arrival(tile, [ign], wx, T0,
                                _cfg(horizon_h=240.0,
                                     edge_weather_piecewise=True))
    phi = spread.compute_arrival(tile, [ign], wx, T0,
                                 _cfg(horizon_h=240.0, slope_phi_vector=True))
    both = spread.compute_arrival(tile, [ign], wx, T0,
                                  _cfg(horizon_h=240.0,
                                       edge_weather_piecewise=True,
                                       slope_phi_vector=True))
    assert both.data != base.data
    assert both.data != pw.data
    assert both.data != phi.data
    assert not any(math.isnan(v) for v in both.data)
    assert sum(1 for v in both.data if math.isfinite(v)) > 50
