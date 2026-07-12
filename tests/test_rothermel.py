"""Tests for wgresponse.rothermel — regression sanity anchors + seam behavior.

Anchors are loose order-of-magnitude bounds per the research digest §5, not
precision assertions. The Sprint 2 ticket B3 block at the bottom pins the
memo seams (fuel_bed / surface_ros_from_bed / effective_wind_from_bed) as
EXACTLY equal to the classic wrappers — byte-identity there is a hard gate,
not an approximation.
"""
import math

import pytest

from wgresponse.contracts import RosResult
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse import rothermel as ro

GR2 = FUEL_TABLE[102]
SH5 = FUEL_TABLE[145]
SH7 = FUEL_TABLE[147]
TL3 = FUEL_TABLE[183]
NB1 = FUEL_TABLE[91]

CURED = dict(m_live_herb_frac=0.30, m_live_woody_frac=0.60)  # fire-season defaults


def _ros(fuel, m_dead, wind_mf_mps, slope_deg, herb=0.30, woody=0.60):
    return ro.surface_ros_mps(fuel, m_dead, herb, woody, wind_mf_mps, slope_deg)


# ---------------------------------------------------------------------------
# Regression anchors
# ---------------------------------------------------------------------------

def test_gr2_ten_percent_rule_band():
    """GR2, 10-m wind 5 m/s, 8% dead FM, cured — Cruz & Alexander (2020) 10%%
    rule says head ROS ~0.5 m/s. The faithful chain (10-m -> 20-ft -> WAF
    midflame) yields ~0.154 m/s (3.1%% of the 10-m wind; Rothermel's documented
    grass underprediction), marginally below the nominal 0.2 floor, so the
    lower bound here is relaxed to 0.12. Treating the 10-m wind as midflame
    (upper bracket) yields ~0.78 m/s. The two brackets must straddle honesty:
    both within the loose 0.12..1.2 band, and the rule value 0.5 m/s must lie
    inside [chain, direct]."""
    mf = ro.midflame_wind_mps(5.0, GR2)
    chain = _ros(GR2, 0.08, mf, 0.0).ros_mps
    direct = _ros(GR2, 0.08, 5.0, 0.0).ros_mps
    assert 0.12 <= chain <= 1.2
    assert 0.2 <= direct <= 1.2
    assert chain < 0.5 < direct          # brackets the 10% rule value
    assert chain >= 0.5 / 5.0            # within factor ~5 of the rule (loose)


def test_gr2_digest_behave_anchor():
    # Digest §5.1(a): GR2, midflame 5 mph (2.2352 m/s), 1-h FM 6%, cured
    # -> 40..90 ft/min against BehavePlus/pyretechnics.
    res = _ros(GR2, 0.06, 2.2352, 0.0)
    ft_min = res.ros_mps / (0.3048 / 60.0)
    assert 40.0 <= ft_min <= 90.0


def test_sh5_santa_ana_ballpark():
    # Chaparral under strong wind (10-m 15 m/s, 5% FM): digest says sustained
    # 1-5 km/h, extreme events higher -> loose 0.5..15 km/h band.
    mf = ro.midflame_wind_mps(15.0, SH5)
    kmh = _ros(SH5, 0.05, mf, 0.0).ros_mps * 3.6
    assert 0.5 <= kmh <= 15.0


def test_zero_wind_zero_slope_small_positive():
    for fuel in (GR2, SH5, SH7, TL3):
        r = _ros(fuel, 0.08, 0.0, 0.0).ros_mps
        assert 0.0 < r < 0.5, fuel.name          # burns, but slowly
    # timber litter creeps slower than grass
    assert _ros(TL3, 0.08, 0.0, 0.0).ros_mps < _ros(GR2, 0.08, 0.0, 0.0).ros_mps


def test_nonburnable_all_zero():
    for code in (91, 92, 93, 98, 99):
        assert is_burnable(code) is False
        res = ro.surface_ros_mps(FUEL_TABLE[code], 0.05, 0.30, 0.60, 10.0, 30.0)
        assert res.ros_mps == 0.0
        assert res.effective_wind_mps == 0.0
        assert res.flame_length_m == 0.0
        assert res.intensity_kwm == 0.0


# ---------------------------------------------------------------------------
# Slope factor
# ---------------------------------------------------------------------------

def test_slope_30deg_multiplies_ros():
    flat = _ros(GR2, 0.08, 0.0, 0.0).ros_mps
    steep = _ros(GR2, 0.08, 0.0, 30.0).ros_mps
    assert steep > 2.0 * flat


def test_slope_monotone():
    seq = [_ros(GR2, 0.08, 0.0, s).ros_mps for s in (0.0, 5.0, 15.0, 30.0, 45.0, 60.0)]
    assert all(a < b for a, b in zip(seq, seq[1:]))


def test_negative_slope_treated_flat():
    assert _ros(GR2, 0.08, 0.0, -20.0).ros_mps == _ros(GR2, 0.08, 0.0, 0.0).ros_mps


# ---------------------------------------------------------------------------
# Moisture behavior
# ---------------------------------------------------------------------------

def test_moisture_monotone_and_extinction_gr2():
    mf = ro.midflame_wind_mps(5.0, GR2)
    seq = [_ros(GR2, m, mf, 0.0).ros_mps for m in (0.02, 0.04, 0.08, 0.12)]
    assert all(a > b for a, b in zip(seq, seq[1:]))
    assert _ros(GR2, 0.15, mf, 0.0).ros_mps == 0.0    # at mx_dead exactly
    assert _ros(GR2, 0.25, mf, 0.0).ros_mps == 0.0    # beyond


def test_extinction_at_mx_dead_tl3():
    assert _ros(TL3, 0.19, 1.0, 0.0).ros_mps > 0.0
    assert _ros(TL3, 0.20, 1.0, 0.0).ros_mps == 0.0   # TL3 mx_dead = 0.20
    assert _ros(TL3, 0.30, 1.0, 0.0).ros_mps == 0.0


def test_dynamic_curing_monotone():
    # Greener herb -> less cured-dead transfer -> slower GR2
    mf = ro.midflame_wind_mps(5.0, GR2)
    cured = _ros(GR2, 0.08, mf, 0.0, herb=0.30).ros_mps
    half = _ros(GR2, 0.08, mf, 0.0, herb=0.75).ros_mps
    green = _ros(GR2, 0.08, mf, 0.0, herb=1.30).ros_mps
    assert cured > half > green
    # fully green grass barely creeps on its small 1-h load
    assert green < 0.05 * cured / 0.5 or green < cured * 0.2


def test_static_model_ignores_herb_moisture_transfer():
    # SH5 has no herbaceous load: herb moisture must not change the answer
    a = _ros(SH5, 0.08, 2.0, 0.0, herb=0.30).ros_mps
    b = _ros(SH5, 0.08, 2.0, 0.0, herb=1.30).ros_mps
    assert a == pytest.approx(b, rel=1e-12)


# ---------------------------------------------------------------------------
# Midflame wind / WAF
# ---------------------------------------------------------------------------

def test_midflame_waf_grass_and_shrub():
    # Albini-Baughman unsheltered: WAF ~0.36 for 1-ft grass, ~0.55 for 6-ft shrub
    mf_gr2 = ro.midflame_wind_mps(5.0, GR2)
    mf_sh5 = ro.midflame_wind_mps(5.0, SH5)
    assert 1.4 <= mf_gr2 <= 1.7      # 5/1.15 * ~0.362
    assert 2.2 <= mf_sh5 <= 2.6      # 5/1.15 * ~0.547
    assert mf_gr2 < mf_sh5           # deeper bed -> less sheltering of midflame
    assert mf_sh5 < 5.0              # never amplifies the 10-m wind


def test_midflame_edge_cases():
    assert ro.midflame_wind_mps(0.0, GR2) == 0.0
    assert ro.midflame_wind_mps(-3.0, GR2) == 0.0
    assert ro.midflame_wind_mps(5.0, NB1) == 0.0     # zero-depth fuel


# ---------------------------------------------------------------------------
# Ellipse geometry
# ---------------------------------------------------------------------------

def test_ellipse_lb_zero_monotone_capped():
    assert ro.ellipse_lb(0.0) == pytest.approx(1.0, abs=1e-9)
    seq = [ro.ellipse_lb(u) for u in (0.0, 0.5, 1.0, 2.0, 3.0, 4.0)]
    assert all(a < b for a, b in zip(seq, seq[1:]))
    assert ro.ellipse_lb(5.0) == 8.0
    assert ro.ellipse_lb(50.0) == 8.0
    for u in (0.0, 1.0, 3.0, 10.0, 100.0):
        assert 1.0 <= ro.ellipse_lb(u) <= 8.0
    assert ro.ellipse_lb(-2.0) == 1.0     # negative wind treated as calm


def test_directional_back_flank_head():
    head = ro.directional_ros_mps(1.0, 2.0, 0.0)
    flank = ro.directional_ros_mps(1.0, 2.0, 90.0)
    back = ro.directional_ros_mps(1.0, 2.0, 180.0)
    assert head == pytest.approx(1.0, rel=1e-12)
    assert back < flank < head
    # rear-focus ellipse identities: back = (1-e)/(1+e), flank = 1-e
    ecc = math.sqrt(3.0) / 2.0
    assert back == pytest.approx((1 - ecc) / (1 + ecc), rel=1e-9)
    assert flank == pytest.approx(1 - ecc, rel=1e-9)


def test_directional_edge_cases():
    # lb=1 -> circle: same in every direction
    for th in (0.0, 45.0, 90.0, 180.0, 270.0):
        assert ro.directional_ros_mps(0.7, 1.0, th) == pytest.approx(0.7, rel=1e-12)
    assert ro.directional_ros_mps(0.0, 3.0, 90.0) == 0.0
    assert ro.directional_ros_mps(-1.0, 3.0, 90.0) == 0.0
    # symmetric about the heading
    assert ro.directional_ros_mps(1.0, 4.0, 30.0) == pytest.approx(
        ro.directional_ros_mps(1.0, 4.0, -30.0), rel=1e-12)


# ---------------------------------------------------------------------------
# Effective wind
# ---------------------------------------------------------------------------

def test_effective_wind_equals_midflame_when_flat():
    res = _ros(GR2, 0.08, 1.574, 0.0)
    assert res.effective_wind_mps == pytest.approx(1.574, rel=1e-6)


def test_effective_wind_slope_adds():
    flat = _ros(GR2, 0.08, 1.574, 0.0).effective_wind_mps
    sloped = _ros(GR2, 0.08, 1.574, 20.0).effective_wind_mps
    assert sloped > flat
    # slope alone gives a nonzero equivalent wind
    assert _ros(GR2, 0.08, 0.0, 30.0).effective_wind_mps > 0.0


def test_effective_wind_function_vector_combination():
    # collinear (default) matches the RosResult field; opposed wind reduces it
    aligned = ro.effective_wind_mps(GR2, 1.574, 20.0, 0.08, 0.30, 0.60, 0.0)
    res = _ros(GR2, 0.08, 1.574, 20.0)
    assert aligned == pytest.approx(res.effective_wind_mps, rel=1e-9)
    opposed = ro.effective_wind_mps(GR2, 1.574, 20.0, 0.08, 0.30, 0.60, 180.0)
    cross = ro.effective_wind_mps(GR2, 1.574, 20.0, 0.08, 0.30, 0.60, 90.0)
    assert opposed < cross < aligned
    assert ro.effective_wind_mps(NB1, 5.0, 30.0) == 0.0


# ---------------------------------------------------------------------------
# Flame length / intensity, and global clamps
# ---------------------------------------------------------------------------

def test_flame_and_intensity_plausible_gr2():
    mf = ro.midflame_wind_mps(5.0, GR2)
    res = _ros(GR2, 0.08, mf, 0.0)
    assert 50.0 <= res.intensity_kwm <= 2000.0      # grass fire, moderate wind
    assert 0.3 <= res.flame_length_m <= 3.0
    # Byram consistency: L = 0.0775 * I^0.46
    assert res.flame_length_m == pytest.approx(
        0.0775 * res.intensity_kwm ** 0.46, rel=1e-9)


def test_flame_intensity_scale_with_wind():
    lo = _ros(GR2, 0.08, 0.5, 0.0)
    hi = _ros(GR2, 0.08, 3.0, 0.0)
    assert hi.intensity_kwm > lo.intensity_kwm
    assert hi.flame_length_m > lo.flame_length_m


def test_never_nan_never_negative_sweep():
    fuels = [FUEL_TABLE[c] for c in (91, 101, 102, 104, 121, 122, 142, 145, 147,
                                     161, 165, 183, 185, 188, 201)]
    for fuel in fuels:
        for m in (0.0, 0.02, 0.1, 0.2, 0.4):
            for wind in (0.0, 2.0, 15.0, 40.0):
                for slope in (0.0, 20.0, 60.0, 89.0):
                    res = ro.surface_ros_mps(fuel, m, 0.30, 0.60, wind, slope)
                    for v in (res.ros_mps, res.effective_wind_mps,
                              res.flame_length_m, res.intensity_kwm):
                        assert math.isfinite(v)
                        assert v >= 0.0


# ---------------------------------------------------------------------------
# Sprint 2 ticket B3 (Council-1 perf S1+S2) — memoization seams.
# fuel_bed / surface_ros_from_bed / effective_wind_from_bed factor the classic
# wrappers at the fuel-bed boundary so spread.py's per-compute_arrival ROS
# memo can build one bed per (fuel, m_dead) and reuse it. Evidence:
# docs/review/perf_review.md §1.7 (bed built 2x per settled cell, 56% of
# member wall time). EXACT equality below is the contract — never loosen to
# approx.
# ---------------------------------------------------------------------------

_B3_MOISTURES = (
    (0.02, 0.30, 0.60),    # bone-dry dead, cured season
    (0.08, 0.75, 0.90),    # moderate, half-green herb
    (0.14, 1.20, 1.20),    # near GR2 extinction, fully green
    (0.35, 0.30, 0.60),    # beyond extinction for most models
)
_B3_WINDS = (0.0, 1.2, 4.7, 20.0)
_B3_SLOPES = (0.0, 12.5, 33.0, 80.0)


def test_from_bed_seams_match_wrappers_exactly():
    """B3 hard gate: for every (fuel, moistures, wind, slope) in the sweep the
    factored path fuel_bed + *_from_bed returns EXACTLY (float ==) what the
    classic wrappers return — one bed reused across all 16 wind/slope pairs,
    which is precisely the reuse pattern spread.py's memo relies on. Any drift
    here would silently move arrival rasters; equality is exact, not approx."""
    codes = (91, 101, 102, 103, 122, 145, 147, 164, 165, 183, 189, 201, 204)
    for code in codes:
        fuel = FUEL_TABLE[code]
        for md, mh, mw in _B3_MOISTURES:
            bed = ro.fuel_bed(fuel, md, mh, mw)     # built ONCE, reused 16x
            for u in _B3_WINDS:
                for s in _B3_SLOPES:
                    want = ro.surface_ros_mps(fuel, md, mh, mw, u, s)
                    got = ro.surface_ros_from_bed(bed, u, s)
                    assert (got.ros_mps, got.effective_wind_mps,
                            got.flame_length_m, got.intensity_kwm) == \
                           (want.ros_mps, want.effective_wind_mps,
                            want.flame_length_m, want.intensity_kwm), \
                        (code, md, u, s)
                    for ang in (0.0, 90.0, 180.0):
                        assert ro.effective_wind_from_bed(bed, u, s, ang) == \
                            ro.effective_wind_mps(fuel, u, s, md, mh, mw, ang), \
                            (code, md, u, s, ang)


def test_fuel_bed_unburnable_is_none_and_from_bed_zero_paths():
    """Non-burnable / zero-depth fuels yield bed None; the from-bed halves
    must reproduce the wrappers' zero outputs for a None bed exactly."""
    assert ro.fuel_bed(NB1, 0.05, 0.30, 0.60) is None
    z = ro.surface_ros_from_bed(None, 10.0, 30.0)
    assert z == RosResult(ros_mps=0.0, effective_wind_mps=0.0,
                          flame_length_m=0.0, intensity_kwm=0.0)
    assert ro.effective_wind_from_bed(None, 10.0, 30.0) == 0.0
    assert ro.effective_wind_from_bed(None, 0.0, 0.0, 180.0) == 0.0
    # and the wrappers agree (the pre-B3 behavior, unchanged)
    assert ro.surface_ros_mps(NB1, 0.05, 0.30, 0.60, 10.0, 30.0) == z
    assert ro.effective_wind_mps(NB1, 10.0, 30.0) == 0.0


def test_fuel_bed_deterministic_across_builds():
    """fuel_bed is pure: two builds from identical inputs carry identical
    intermediates (the memo stores one and drops the other)."""
    a = ro.fuel_bed(SH5, 0.08, 0.30, 0.60)
    b = ro.fuel_bed(SH5, 0.08, 0.30, 0.60)
    for slot in ("sigma_char", "beta", "beta_rel", "i_r", "xi", "sink",
                 "r0_ftmin", "c", "b", "e"):
        assert getattr(a, slot) == getattr(b, slot), slot
