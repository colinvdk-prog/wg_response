"""RV2-CODE R5: slope+wind combined as EQUIVALENT WIND SPEED then re-fed
through the nonlinear phi_w, vs the correct phi-VECTOR combination that
rothermel.effective_wind_from_bed(..., wind_slope_angle_deg) already implements.

Code path (spread.py cell_params ~:578-582):
    u_wind  = midflame_wind_mps(wind10, fuel)
    u_slope = effective_wind_from_bed(bed, 0.0, slope)     # phi_s -> equivalent U
    u_eff,brg = _max_spread_vector(u_wind, u_slope, wdir, aspect)   # VECTOR SUM IN U-SPACE
    ros     = surface_ros_from_bed(bed, u_eff, 0.0)        # phi_w(u_eff), slope=0

Correct (Rothermel/FARSITE/ELMFIRE, and what the repo's own helper does):
    phi_E = |phi_w_vec + phi_s_vec|;  R = R0 * (1 + phi_E)

Because phi_w ~ C * U^B with B ~ 1.4-1.9 (convex), summing in U-space and then
raising to the power B is NOT the same as summing the phis:
  aligned   -> (a+b)^B  >  a^B + b^B    => code runs FAST
  opposed   -> |a-b|^B  <  |a^B - b^B|  => code runs SLOW (anti-safe polarity)
"""
from __future__ import annotations

import math
import sys

from _common import cfg_default  # noqa: F401  (path bootstrap)

from wgresponse import rothermel
from wgresponse.fuels import FUEL_TABLE


def code_ros(fuel, m_dead, herb, woody, wind10, slope_deg, angle_deg):
    """Exactly the spread.py cell_params computation for one cell."""
    bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
    u_wind = rothermel.midflame_wind_mps(wind10, fuel)
    u_slope = rothermel.effective_wind_from_bed(bed, 0.0, slope_deg)
    # _max_spread_vector: wind vector at `angle_deg` off the upslope direction
    a = math.radians(angle_deg)
    vx = u_wind * math.sin(a) + 0.0
    vy = u_wind * math.cos(a) + u_slope
    u_eff = math.hypot(vx, vy)
    brg = math.degrees(math.atan2(vx, vy)) % 360.0 if u_eff > 1e-12 else 0.0
    res = rothermel.surface_ros_from_bed(bed, u_eff, 0.0)
    return res.ros_mps, u_eff, brg


def phi_ros(fuel, m_dead, herb, woody, wind10, slope_deg, angle_deg):
    """Correct phi-vector combination (same convention as
    rothermel.effective_wind_from_bed's wind_slope_angle_deg)."""
    bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
    u_wind = rothermel.midflame_wind_mps(wind10, fuel)
    phi_w = rothermel._phi_wind(bed, max(0.0, u_wind) * rothermel._MPS_TO_FTMIN)
    phi_s = rothermel._phi_slope(bed, slope_deg)
    a = math.radians(angle_deg)
    # phi_s points upslope (+y); phi_w points at `angle_deg` from upslope
    px = phi_w * math.sin(a)
    py = phi_w * math.cos(a) + phi_s
    phi_e = math.hypot(px, py)
    brg = math.degrees(math.atan2(px, py)) % 360.0 if phi_e > 1e-12 else 0.0
    r = bed.r0_ftmin * (1.0 + phi_e) * rothermel._FTMIN_TO_MPS
    # sanity: repo helper reproduces phi_e exactly
    u_check = rothermel.effective_wind_from_bed(bed, u_wind, slope_deg, angle_deg)
    phi_check = rothermel._phi_wind(bed, u_check * rothermel._MPS_TO_FTMIN)
    assert abs(phi_check - phi_e) < 1e-9 * max(1.0, phi_e), (phi_check, phi_e)
    return r, phi_e, brg, phi_w, phi_s


def bexp(fuel, m_dead, herb, woody):
    return rothermel.fuel_bed(fuel, m_dead, herb, woody).b


def main():
    herb, woody = 0.30, 0.60
    m_dead = 0.06
    print("phi_w exponent B per fuel (phi ~ U^B):")
    for code in (102, 122, 142, 165, 186):
        print(f"  {FUEL_TABLE[code].name:>5s}({code}) B = {bexp(FUEL_TABLE[code], m_dead, herb, woody):.3f}")

    slope_pct = 30.0
    slope_deg = math.degrees(math.atan(slope_pct / 100.0))
    print(f"\n=== 30% slope ({slope_deg:.1f} deg), 5 m/s 10-m wind, m_dead={m_dead} ===")
    hdr = f"{'fuel':>10s} {'angle':>6s} {'code ROS':>10s} {'phi ROS':>10s} {'err %':>8s} {'code brg':>9s} {'phi brg':>8s}"
    print(hdr)
    for code in (102, 122, 142, 165, 186):
        f = FUEL_TABLE[code]
        for ang in (0.0, 45.0, 90.0, 135.0, 180.0):
            rc, ue, bc = code_ros(f, m_dead, herb, woody, 5.0, slope_deg, ang)
            rp, pe, bp, pw, ps = phi_ros(f, m_dead, herb, woody, 5.0, slope_deg, ang)
            err = (rc / rp - 1.0) * 100.0 if rp > 0 else float("nan")
            print(f"{f.name:>10s} {ang:6.0f} {rc:10.5f} {rp:10.5f} {err:+8.1f} "
                  f"{bc:9.1f} {bp:8.1f}")

    print("\n=== sweep: worst aligned over-speed / worst opposed under-speed ===")
    print(f"{'fuel':>10s} {'slope%':>7s} {'U10':>5s} {'aligned err%':>13s} {'opposed err%':>13s} {'90deg err%':>11s}")
    worst_hi = worst_lo = None
    for code in (102, 122, 142, 165, 186):
        f = FUEL_TABLE[code]
        for sp in (10.0, 30.0, 60.0, 100.0):
            sd = math.degrees(math.atan(sp / 100.0))
            for u10 in (2.0, 5.0, 10.0, 20.0):
                r0 = code_ros(f, m_dead, herb, woody, u10, sd, 0.0)[0]
                p0 = phi_ros(f, m_dead, herb, woody, u10, sd, 0.0)[0]
                r180 = code_ros(f, m_dead, herb, woody, u10, sd, 180.0)[0]
                p180 = phi_ros(f, m_dead, herb, woody, u10, sd, 180.0)[0]
                r90 = code_ros(f, m_dead, herb, woody, u10, sd, 90.0)[0]
                p90 = phi_ros(f, m_dead, herb, woody, u10, sd, 90.0)[0]
                e0 = (r0 / p0 - 1) * 100
                e180 = (r180 / p180 - 1) * 100
                e90 = (r90 / p90 - 1) * 100
                if worst_hi is None or e0 > worst_hi[0]:
                    worst_hi = (e0, f.name, sp, u10)
                if worst_lo is None or e180 < worst_lo[0]:
                    worst_lo = (e180, f.name, sp, u10)
                if sp == 30.0 and u10 in (5.0, 20.0):
                    print(f"{f.name:>10s} {sp:7.0f} {u10:5.0f} {e0:+13.1f} {e180:+13.1f} {e90:+11.1f}")
    print(f"\nWORST aligned (code FAST): {worst_hi[0]:+.1f}%  {worst_hi[1]} slope {worst_hi[2]:.0f}% U10 {worst_hi[3]:.0f}")
    print(f"WORST opposed (code SLOW): {worst_lo[0]:+.1f}%  {worst_lo[1]} slope {worst_lo[2]:.0f}% U10 {worst_lo[3]:.0f}")

    # Is the flat case (slope=0) unaffected?  phi_s = 0 -> both identical.
    f = FUEL_TABLE[102]
    r = code_ros(f, m_dead, herb, woody, 5.0, 0.0, 0.0)[0]
    p = phi_ros(f, m_dead, herb, woody, 5.0, 0.0, 0.0)[0]
    print(f"\nflat (slope=0) identity check: code={r!r} phi={p!r} equal={r == p}")
    # And zero-wind case
    r = code_ros(f, m_dead, herb, woody, 0.0, slope_deg, 0.0)[0]
    p = phi_ros(f, m_dead, herb, woody, 0.0, slope_deg, 0.0)[0]
    print(f"zero-wind identity check:      code={r!r} phi={p!r} equal={r == p}")


if __name__ == "__main__":
    sys.exit(main())
