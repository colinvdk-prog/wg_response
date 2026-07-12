"""Regression pins for two physics-review findings (dimension: physics).

Both findings were confirmed as convention/provenance matters that are FAITHFUL
to the sourced ELMFIRE/FARSITE implementations and are NOT unsafe — the code is
correct. These tests lock the safety-relevant behaviors the findings hinge on so
a future refactor cannot silently turn a benign convention into an unsafe freeze.

Finding 1 — ellipse_lb() L/B saturates at cap=8 by ~8.5 mph effective wind.
  The Anderson(1983)/ELMFIRE formula, clamped to [1, 8] per digest §3.2, is
  correct. The digest PROSE ("L/B ~ 4.2 at 10 mph") was internally inconsistent
  with its own formula (which gives 11.9 at 10 mph) and was corrected in
  docs/research/spread_model.md. The load-bearing safety fact: above the cap the
  eccentricity is frozen, but the ABSOLUTE flank/back rate of spread keeps rising
  with wind (head term grows), so lateral-property ETAs still get earlier in
  stronger wind — they do not freeze.

Finding 2 — spread.py combines wind+slope as a vector sum of equivalent WINDS,
  while rothermel.effective_wind_mps combines the PHI FACTORS. The two agree
  exactly for pure-slope and collinear-aligned cases and diverge modestly
  off-axis; on the HEAD axis spread.py is conservative-fast (correct polarity for
  an alerting product). These pin the exact-agreement cases and the head-axis
  polarity, and guard against any wrong-direction regression.
"""
from __future__ import annotations

import math

import pytest

from wgresponse import rothermel as ro
from wgresponse.fuels import FUEL_TABLE

GR2 = FUEL_TABLE[102]
SH5 = FUEL_TABLE[145]

_MPS_TO_MPH = 2.2369362920544025
_MPH_TO_MPS = 1.0 / _MPS_TO_MPH


def _raw_lb(u_mph: float) -> float:
    """Uncapped Anderson(1983) L/B for effective midflame wind U (mph)."""
    return 0.936 * math.exp(0.2566 * u_mph) + 0.461 * math.exp(-0.1548 * u_mph) - 0.397


# ---------------------------------------------------------------------------
# Finding 1 — cap saturation is faithful, and NOT a lateral-ETA freeze
# ---------------------------------------------------------------------------

def test_ellipse_lb_matches_uncapped_formula_below_cap():
    """Below the cap, ellipse_lb reproduces the sourced formula exactly, so the
    digest's own equation (not its old round-number prose) is what ships."""
    for u_mph in (0.5, 2.0, 4.0, 6.0, 8.0):
        got = ro.ellipse_lb(u_mph * _MPH_TO_MPS)
        assert got == pytest.approx(_raw_lb(u_mph), rel=1e-9)
    # The old prose claim "L/B ~ 4.2 at 10 mph" was wrong; the formula gives ~11.9
    # (which the code then caps to 8). Pin both to prevent the myth returning.
    assert _raw_lb(10.0) == pytest.approx(11.882, abs=0.01)
    assert _raw_lb(6.046) == pytest.approx(4.2, abs=0.01)   # 4.2 is reached at ~6.05 mph


def test_ellipse_lb_cap_binds_near_8p5_mph_effective_wind():
    """The [1, 8] clamp starts biting at ~8.49 mph effective wind (raw = 8.016)."""
    assert ro.ellipse_lb(8.0 * _MPH_TO_MPS) == pytest.approx(7.0278, abs=1e-3)   # below cap
    assert ro.ellipse_lb(8.4 * _MPH_TO_MPS) < 8.0                                 # still below
    assert ro.ellipse_lb(8.6 * _MPH_TO_MPS) == 8.0                                # capped
    assert ro.ellipse_lb(40.0 * _MPH_TO_MPS) == 8.0                               # stays capped


def test_head_relative_fractions_frozen_above_cap():
    """Above the cap every ellipse shares e ~ 0.99216, so backing/flank fractions
    OF HEAD are identical at 9 mph and 40 mph effective wind (documented freeze)."""
    lb_lo = ro.ellipse_lb(9.0 * _MPH_TO_MPS)
    lb_hi = ro.ellipse_lb(40.0 * _MPH_TO_MPS)
    assert lb_lo == lb_hi == 8.0
    for theta in (90.0, 180.0):
        frac_lo = ro.directional_ros_mps(1.0, lb_lo, theta)
        frac_hi = ro.directional_ros_mps(1.0, lb_hi, theta)
        assert frac_lo == pytest.approx(frac_hi, rel=1e-12)
    ecc = math.sqrt(8.0 * 8.0 - 1.0) / 8.0
    assert ecc == pytest.approx(0.99216, abs=1e-4)


def test_absolute_lateral_ros_keeps_growing_above_cap():
    """THE safety-critical fact behind Finding 1: once the cap freezes the
    eccentricity, absolute flank(90) and back(180) ROS still rise monotonically
    with wind (the head term grows) — lateral-property ETAs get earlier, never
    frozen, in stronger wind. All sampled winds put GR2 well above the ~8.5 mph
    effective-wind cap (verified L/B == 8 at each)."""
    prev_head = prev_flank = prev_back = -1.0
    for wind_kmh in (60.0, 65.0, 80.0, 100.0, 120.0):
        mf = ro.midflame_wind_mps(wind_kmh / 3.6, GR2)
        res = ro.surface_ros_mps(GR2, 0.06, 0.30, 0.60, mf, 0.0)
        lb = ro.ellipse_lb(res.effective_wind_mps)
        assert lb == 8.0, f"{wind_kmh} km/h not above cap"
        head = res.ros_mps
        flank = ro.directional_ros_mps(head, lb, 90.0)
        back = ro.directional_ros_mps(head, lb, 180.0)
        assert head > prev_head
        assert flank > prev_flank
        assert back > prev_back
        prev_head, prev_flank, prev_back = head, flank, back


# ---------------------------------------------------------------------------
# Finding 2 — wind-vector vs phi-vector combination
# ---------------------------------------------------------------------------

def _spread_style_ros(fuel, wind10_mps, slope_deg, wind_slope_angle_deg,
                      m_dead=0.08, herb=0.30, woody=0.60):
    """Replicate spread.compute_arrival.cell_params: vector-add the equivalent
    WINDS (midflame wind + slope-equivalent virtual wind), feed magnitude to
    surface_ros_mps as a pure wind on flat ground."""
    u_wind = ro.midflame_wind_mps(wind10_mps, fuel)
    u_slope = ro.effective_wind_mps(fuel, 0.0, slope_deg, m_dead, herb, woody)
    to_wind = 0.0
    to_up = math.radians(wind_slope_angle_deg)
    vx = u_wind * math.sin(to_wind) + u_slope * math.sin(to_up)
    vy = u_wind * math.cos(to_wind) + u_slope * math.cos(to_up)
    u_eff = math.hypot(vx, vy)
    return ro.surface_ros_mps(fuel, m_dead, herb, woody, u_eff, 0.0).ros_mps


def _phi_style_ros(fuel, wind10_mps, slope_deg, wind_slope_angle_deg,
                   m_dead=0.08, herb=0.30, woody=0.60):
    """rothermel's own PHI-factor vector combination (ELMFIRE convention)."""
    u_mf = ro.midflame_wind_mps(wind10_mps, fuel)
    u_eff = ro.effective_wind_mps(fuel, u_mf, slope_deg, m_dead, herb, woody,
                                  wind_slope_angle_deg)
    return ro.surface_ros_mps(fuel, m_dead, herb, woody, u_eff, 0.0).ros_mps


def test_pure_slope_two_conventions_agree_exactly():
    """With zero wind there is only one vector; both conventions must be identical
    (this is why spread.py can borrow effective_wind_mps for the slope term)."""
    for slope in (5.0, 15.0, 25.0, 40.0, 60.0):
        s = _spread_style_ros(SH5, 0.0, slope, 0.0)
        p = _phi_style_ros(SH5, 0.0, slope, 0.0)
        assert s == pytest.approx(p, rel=1e-9)


def test_pure_wind_flat_two_conventions_agree_exactly():
    """With zero slope there is only the wind vector; both conventions coincide
    exactly (the other single-component case besides pure slope)."""
    for wind_kmh in (5.0, 10.0, 20.0, 40.0):
        s = _spread_style_ros(GR2, wind_kmh / 3.6, 0.0, 0.0)
        p = _phi_style_ros(GR2, wind_kmh / 3.6, 0.0, 0.0)
        assert s == pytest.approx(p, rel=1e-9)


def test_general_collinear_wind_plus_slope_diverges_conservative_fast():
    """When BOTH wind and slope are nonzero, the two conventions do NOT coincide
    even collinearly, because phi_w = C*U^B is nonlinear in U, so
    U_eff(phi_w + phi_s) != U_eff(phi_w) + U_eff(phi_s). spread.py's wind-sum is
    the larger (conservative-fast) of the two on the aligned axis. This documents
    the finding precisely: exact agreement is a single-component property, not a
    general-collinear one."""
    for wind_kmh, slope in ((10.0, 25.0), (20.0, 40.0), (5.0, 10.0)):
        s = _spread_style_ros(GR2, wind_kmh / 3.6, slope, 0.0)
        p = _phi_style_ros(GR2, wind_kmh / 3.6, slope, 0.0)
        assert s > p * (1.0 + 1e-6)          # genuinely diverges, not a rounding tie
        assert s == pytest.approx(p, rel=0.5)  # but the gap stays modest


def test_head_axis_wind_sum_is_conservative_fast():
    """Off-axis the two diverge; on the HEAD axis (aligned) spread.py's wind-sum
    is >= the phi-sum — conservative-fast, the correct polarity for alerting."""
    for fuel in (GR2, SH5):
        for wind_kmh in (5.0, 10.0, 20.0):
            for slope in (10.0, 25.0, 40.0):
                s = _spread_style_ros(fuel, wind_kmh / 3.6, slope, 0.0)
                p = _phi_style_ros(fuel, wind_kmh / 3.6, slope, 0.0)
                assert s >= p - 1e-12, (fuel.name, wind_kmh, slope, s, p)


def test_off_axis_divergence_is_bounded_and_correct_polarity():
    """Reproduce the finding's SH5 8 km/h + 25 deg measurement: aligned high,
    opposing low, perpendicular in between — a modest, well-behaved divergence
    with no NaN/negative and the expected ordering by wind/slope alignment."""
    wind10 = 8.0 / 3.6
    aligned = _spread_style_ros(SH5, wind10, 25.0, 0.0)
    perp = _spread_style_ros(SH5, wind10, 25.0, 90.0)
    opposing = _spread_style_ros(SH5, wind10, 25.0, 180.0)
    p_aligned = _phi_style_ros(SH5, wind10, 25.0, 0.0)
    p_perp = _phi_style_ros(SH5, wind10, 25.0, 90.0)
    p_opposing = _phi_style_ros(SH5, wind10, 25.0, 180.0)
    # ordering by alignment holds for both conventions
    assert opposing < perp < aligned
    assert p_opposing < p_perp < p_aligned
    # head-axis conservative-fast, opposing-axis conservative-slow, all modest
    assert 0.0 < (aligned - p_aligned) / p_aligned < 0.20
    assert -0.35 < (opposing - p_opposing) / p_opposing < 0.0
    for v in (aligned, perp, opposing, p_aligned, p_perp, p_opposing):
        assert math.isfinite(v) and v > 0.0
