"""RV2-CODE R2 + R3 runtime repros.

R3: edge cost uses SOURCE-cell ROS only, vs docs/research/spread_model.md
    sec 3.3 spec  t_AB = dist / mean(R_A(theta), R_B(theta)).
R2: the weather hour is frozen at the SOURCE cell's arrival for the WHOLE edge.
"""
from __future__ import annotations

import math
import sys

from _common import T0, cfg_default, flat_tile, set_col_band

import rv2code_integrator as RI
from wgresponse import geo, rothermel, spread
from wgresponse.contracts import (LatLon, UNREACHED, WeatherForecast,
                                  WeatherSample)
from wgresponse.fuels import FUEL_TABLE


def wxc(t0, spec_list, wdir=270.0, rh=15.0, temp=32.0):
    """spec_list: list of (hours, wind_mps) blocks."""
    s, h = [], 0
    for nh, ws in spec_list:
        for _ in range(int(nh)):
            s.append(WeatherSample(t_s=t0 + h * 3600.0, wind_speed_mps=ws,
                                   wind_gust_mps=ws, wind_dir_deg=wdir,
                                   rh_pct=rh, temp_c=temp))
            h += 1
    return WeatherForecast(loc=LatLon(34.1, -117.1), samples=s, source="rv2")


def hh(v, t0=T0):
    return math.inf if math.isinf(v) else (v - t0) / 3600.0


# ---------------------------------------------------------------------------
def r3_two_fuel_boundary():
    print("=" * 78)
    print("R3: edge cost uses SOURCE-cell ROS only (spec says mean(R_A,R_B))")
    print("=" * 78)
    cfg = cfg_default()
    wx = wxc(T0, [(200, 8.0)])

    # 1-D corridor: 60 cols, west half GR2 (fast), east half TL6 (slow).
    # Fire lit at the WEST edge -> crosses fast->slow.
    # Then lit at the EAST edge -> crosses slow->fast. Same geometry, same
    # boundary; ONLY the traversal direction changes.
    print("\n  Per-edge asymmetry at the fuel boundary (90 m cell, 8 m/s W wind):")
    for fast_code, slow_code in ((102, 186), (102, 165), (122, 186)):
        tile = flat_tile(34.30, -117.40, 90.0, 3, 60, fast_code)
        set_col_band(tile, 30, 60, slow_code)
        f_fast, f_slow = FUEL_TABLE[fast_code], FUEL_TABLE[slow_code]
        # head ROS along the due-east edge for each fuel at this weather
        def head_east(fuel):
            bed = rothermel.fuel_bed(fuel, 0.0567, cfg.live_herb_frac, cfg.live_woody_frac)
            u = rothermel.midflame_wind_mps(8.0, fuel)
            ue, brg = RI._msv(u, 0.0, 270.0, -1.0)
            res = rothermel.surface_ros_from_bed(bed, ue, 0.0)
            lb = rothermel.ellipse_lb(res.effective_wind_mps)
            return rothermel.directional_ros_mps(res.ros_mps, lb, 90.0 - brg)
        ra, rb = head_east(f_fast), head_east(f_slow)
        d = 90.0
        t_src_fs = d / ra          # fast->slow: shipped uses R_fast
        t_src_sf = d / rb          # slow->fast: shipped uses R_slow
        t_mean = d / (0.5 * (ra + rb))
        t_exact = (d / 2) / ra + (d / 2) / rb
        print(f"\n   {f_fast.name[:12]:12s} R={ra:.4f} m/s  ->  {f_slow.name[:12]:12s} R={rb:.4f} m/s")
        print(f"     boundary edge crossing time (s):")
        print(f"       SHIPPED fast->slow (R_source=R_fast) : {t_src_fs:8.1f}")
        print(f"       SHIPPED slow->fast (R_source=R_slow) : {t_src_sf:8.1f}"
              f"   asymmetry {t_src_sf/t_src_fs:5.2f}x")
        print(f"       spec  mean(R_A,R_B)                  : {t_mean:8.1f}")
        print(f"       exact half-in-each-cell              : {t_exact:8.1f}")
        print(f"     -> shipped fast->slow is {100*(t_mean/t_src_fs - 1):+.0f}% "
              f"vs spec (FAST/early); slow->fast {100*(t_mean/t_src_sf - 1):+.0f}% (SLOW/late)")

    # Domain-level: how far does the front get in 6 h, entering from each side?
    print("\n  Domain-level: 6 h reach across a GR2|TL6 boundary at col 30")
    print(f"  {'direction':>14s} {'rule':>10s} {'reach cells':>12s} {'reach km':>9s}")
    for direction, ign_col in (("W->E (fast->slow)", 1), ("E->W (slow->fast)", 58)):
        tile = flat_tile(34.30, -117.40, 90.0, 3, 60, 102)
        set_col_band(tile, 30, 60, 186)
        wdir = 270.0 if ign_col == 1 else 90.0
        w = wxc(T0, [(200, 8.0)], wdir=wdir)
        ign = [geo.rc_to_latlon(tile.spec, 1, ign_col)]
        RI.verify_identity(tile, ign, w, T0, cfg, label=direction)
        for rule in ("source", "mean", "harm"):
            ras = RI.arrival(tile, ign, w, T0, cfg, edge_rule=rule)
            t_end = T0 + 6.0 * 3600.0
            cols = [c for c in range(60)
                    if ras.data[1 * 60 + c] <= t_end]
            reach = (max(cols) - ign_col) if ign_col == 1 else (ign_col - min(cols))
            print(f"  {direction:>14s} {rule:>10s} {reach:12d} {reach*0.09:9.2f}")


# ---------------------------------------------------------------------------
def r2_frozen_weather():
    print()
    print("=" * 78)
    print("R2: departure-bucket weather frozen for the whole edge")
    print("=" * 78)
    cfg = cfg_default()

    # (a) Luna's two-cell arithmetic: 0.01 -> 1.0 m/s step at hour 1.
    print("\n  (a) two-cell step case (hour 0 near-calm, hour 1+ strong):")
    tile = flat_tile(34.30, -117.40, 90.0, 3, 40, 102)
    wx = wxc(T0, [(1, 0.01), (200, 12.0)])
    ign = [geo.rc_to_latlon(tile.spec, 1, 1)]
    RI.verify_identity(tile, ign, wx, T0, cfg, label="step")
    a_frozen = RI.arrival(tile, ign, wx, T0, cfg, wx_rule="frozen")
    a_piece = RI.arrival(tile, ign, wx, T0, cfg, wx_rule="piecewise")
    for c in (2, 3, 5, 10, 20, 39):
        f = hh(a_frozen.data[1 * 40 + c])
        p = hh(a_piece.data[1 * 40 + c])
        d = (f - p) * 3600.0 if math.isfinite(f) and math.isfinite(p) else float("nan")
        print(f"    col {c:2d} ({(c-1)*90:5d} m): frozen {f:8.3f} h  "
              f"piecewise {p:8.3f} h   LATE by {d:9.1f} s")

    # (b) realistic ramp: calm dawn -> 8 m/s afternoon
    print("\n  (b) realistic diurnal ramp (0.5 m/s at t0, +0.75 m/s per hour to 8):")
    ramp = []
    for h in range(200):
        ramp.append((1, min(8.0, 0.5 + 0.75 * h)))
    wx2 = wxc(T0, ramp)
    tile2 = flat_tile(34.30, -117.40, 90.0, 3, 80, 102)
    ign2 = [geo.rc_to_latlon(tile2.spec, 1, 1)]
    RI.verify_identity(tile2, ign2, wx2, T0, cfg, label="ramp")
    f2 = RI.arrival(tile2, ign2, wx2, T0, cfg, wx_rule="frozen")
    p2 = RI.arrival(tile2, ign2, wx2, T0, cfg, wx_rule="piecewise")
    print(f"    {'col':>4s} {'dist km':>8s} {'frozen h':>9s} {'piecewise h':>12s} "
          f"{'late by min':>12s} {'late %':>8s}")
    for c in (5, 10, 20, 40, 60, 79):
        f = hh(f2.data[1 * 80 + c])
        p = hh(p2.data[1 * 80 + c])
        if math.isfinite(f) and math.isfinite(p):
            print(f"    {c:4d} {(c-1)*0.09:8.2f} {f:9.3f} {p:12.3f} "
                  f"{(f-p)*60:12.2f} {100*(f/p-1):8.2f}")

    # (c) slow fuel, where edges take a long time -> the frozen hour spans more
    print("\n  (c) SLOW fuel (TL6) under the same ramp — long edge traversals:")
    tile3 = flat_tile(34.30, -117.40, 90.0, 3, 40, 186)
    ign3 = [geo.rc_to_latlon(tile3.spec, 1, 1)]
    RI.verify_identity(tile3, ign3, wx2, T0, cfg, label="ramp-TL6")
    f3 = RI.arrival(tile3, ign3, wx2, T0, cfg, wx_rule="frozen")
    p3 = RI.arrival(tile3, ign3, wx2, T0, cfg, wx_rule="piecewise")
    for c in (5, 10, 20, 39):
        f = hh(f3.data[1 * 40 + c])
        p = hh(p3.data[1 * 40 + c])
        if math.isfinite(f) and math.isfinite(p):
            print(f"    col {c:2d} ({(c-1)*0.09:5.2f} km): frozen {f:8.3f} h  "
                  f"piecewise {p:8.3f} h  late by {(f-p)*60:7.2f} min "
                  f"({100*(f/p-1):+.2f}%)")

    # (d) how long IS a typical edge? if << 1 h the finding is mostly moot
    print("\n  (d) edge traversal time distribution (what the freeze actually spans):")
    for code, ws in ((102, 8.0), (102, 2.0), (186, 8.0), (186, 2.0), (142, 8.0)):
        f = FUEL_TABLE[code]
        bed = rothermel.fuel_bed(f, 0.0567, cfg.live_herb_frac, cfg.live_woody_frac)
        u = rothermel.midflame_wind_mps(ws, f)
        ue, brg = RI._msv(u, 0.0, 270.0, -1.0)
        res = rothermel.surface_ros_from_bed(bed, ue, 0.0)
        lb = rothermel.ellipse_lb(res.effective_wind_mps)
        rh_ = rothermel.directional_ros_mps(res.ros_mps, lb, 0.0)
        rf_ = rothermel.directional_ros_mps(res.ros_mps, lb, 90.0)
        rb_ = rothermel.directional_ros_mps(res.ros_mps, lb, 180.0)
        print(f"    {f.name[:14]:14s} U10={ws:4.1f}  head-edge {90/rh_/60:7.2f} min | "
              f"flank {90/max(rf_,1e-9)/60:8.2f} min | back {90/max(rb_,1e-9)/60:9.2f} min")


if __name__ == "__main__":
    r3_two_fuel_boundary()
    r2_frozen_weather()
