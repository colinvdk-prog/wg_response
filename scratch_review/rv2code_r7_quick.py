"""RV2-CODE R7-R10: the quick checks, verified at RUNTIME.

  7.  contracts.validate_config gaps (~:1116)
  8.  the 1e-4 ROS floor divisor in spread.py (~:641) — does it fabricate
      arrivals in non-spreading fuel?
  9.  contracts.Raster.get negative-index / overflow wrap
 10.  tools/hindcast_lib.py:693 `sorted[n//2]` as the "median head-ratio"
      that the published tables report
"""
from __future__ import annotations

import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from _common import T0, cfg_default, flat_tile

from wgresponse import geo, rothermel, spread
from wgresponse.contracts import (GridSpec, LatLon, Raster, ResponseConfig,
                                  UNREACHED, WeatherForecast, WeatherSample,
                                  validate_config)


def r7_validate_config():
    print("=" * 78)
    print("R7. validate_config gaps — configs that are ACCEPTED but are unsound")
    print("=" * 78)
    checks = [
        ("horizon_h = 0", dict(horizon_h=0.0)),
        ("horizon_h = -5 (negative forecast)", dict(horizon_h=-5.0)),
        ("wind_gust_blend = 5.0 (500% of the gust delta)", dict(wind_gust_blend=5.0)),
        ("wind_gust_blend = -3.0 (subtracts wind)", dict(wind_gust_blend=-3.0)),
        ("circuity = 0.1 (routes SHORTER than the chord)", dict(circuity=0.1)),
        ("circuity = 0 (division by zero downstream)", dict(circuity=0.0)),
        ("road_speed_mps = 0", dict(road_speed_mps=0.0)),
        ("route_margin_min = -60 (negative safety margin)", dict(route_margin_min=-60.0)),
        ("corridor_erosion_m = -500 (DILATES = anti-safe)", dict(corridor_erosion_m=-500.0)),
        ("corridor_sample_m = 100000 (one sample per leg)", dict(corridor_sample_m=100000.0)),
        ("gel_effectiveness = 5.0 (P(save) = 500%)", dict(gel_effectiveness=5.0)),
        ("gel_effectiveness = -1.0", dict(gel_effectiveness=-1.0)),
        ("wind_perturb_frac = 10.0 (1000% band)", dict(wind_perturb_frac=10.0)),
        ("wind_perturb_frac = -0.25 (inverted band)", dict(wind_perturb_frac=-0.25)),
        ("dir_perturb_deg = 400", dict(dir_perturb_deg=400.0)),
        ("grass_ros_floor_frac = 3.0 (ROS = 3x the wind)", dict(grass_ros_floor_frac=3.0)),
        ("live_herb_frac = -1.0", dict(live_herb_frac=-1.0)),
        ("grid_max_cells = 1 (whole domain = 1 cell)", dict(grid_max_cells=1)),
        ("slack_min = -100", dict(slack_min=-100.0)),
        ("egress_buffer_min = 1e9", dict(egress_buffer_min=1e9)),
        ("fire_traffic_factor = 0", dict(fire_traffic_factor=0.0)),
        ("extreme_p10_h = 999 (> extreme_h, > notice_h)", dict(extreme_p10_h=999.0)),
    ]
    accepted = []
    for label, kw in checks:
        cfg = ResponseConfig()
        for k, v in kw.items():
            setattr(cfg, k, v)
        probs = validate_config(cfg)
        mark = "ACCEPTED" if not probs else f"rejected: {probs}"
        if not probs:
            accepted.append(label)
        print(f"  {label:52s} -> {mark}")
    print(f"\n  {len(accepted)}/{len(checks)} unsound configs pass validate_config.")
    print("  validate_config checks only: extreme_h<notice_h, deploy_min>0,")
    print("  egress_buffer_min>=0, gel_window_h>0, ensemble_n>=1, grid_cell_m>=30.")

    # Do the accepted-but-absurd ones actually change safety output?
    print("\n  Do they bite? corridor_erosion_m = -500 (dilate instead of erode):")
    from wgresponse import safety
    spec = GridSpec(north_lat=34.25, west_lon=-117.65, cell_m=90.0,
                    nrows=60, ncols=60)
    ras = Raster(spec, [UNREACHED] * 3600)
    for i in range(1500, 1560):
        ras.data[i] = T0 + 600.0
    for radius in (500.0, 0.0, -500.0):
        er = safety._erode(ras, radius)
        finite = sum(1 for v in er.data if math.isfinite(v))
        print(f"    erosion {radius:+7.0f} m -> {finite:4d} finite (fire-marked) cells")
    print("    (negative radius -> k<=0 -> NO erosion; the corridor shield is")
    print("     silently switched OFF rather than rejected)")


def r8_ros_floor():
    print()
    print("=" * 78)
    print("R8. the 1e-4 m/s ROS floor divisor (spread.py ~:641)")
    print("=" * 78)
    cfg = cfg_default()
    print(f"  _ROS_FLOOR_MPS = {spread._ROS_FLOOR_MPS}")
    for cell_m in (30.0, 90.0, 300.0, 1000.0):
        t = cell_m / spread._ROS_FLOOR_MPS
        print(f"  cell {cell_m:6.0f} m -> floored edge cost {t:12.0f} s = "
              f"{t/3600:9.1f} h  (horizon {cfg.horizon_h:.0f} h "
              f"{'-> REJECTED, harmless' if t > cfg.horizon_h*3600 else '-> ACCEPTED, FABRICATED ARRIVAL'})")
    print("\n  horizon at which the floor starts fabricating arrivals:")
    for cell_m in (30.0, 90.0, 300.0, 1000.0):
        print(f"    cell {cell_m:6.0f} m : horizon_h > "
              f"{cell_m/spread._ROS_FLOOR_MPS/3600:.1f} h")
    print(f"  shipped horizon_h = {cfg.horizon_h:.0f} h, grid_cell_m = "
          f"{cfg.grid_cell_m:.0f} m -> floor NEVER binds within the horizon.")
    print("  BUT validate_config does not bound horizon_h (see R7): horizon_h=300")
    print("  at 90 m cells would let a zero-ROS cell 'spread' at 1e-4 m/s.")

    # runtime demonstration
    print("\n  runtime: 90 m cells, horizon 300 h, fuel whose ROS is ~0 (very wet)")
    tile = flat_tile(34.30, -117.40, 90.0, 3, 20, 186)
    wx = WeatherForecast(loc=LatLon(34.3, -117.4), samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=0.0, wind_gust_mps=0.0,
                      wind_dir_deg=270.0, rh_pct=99.0, temp_c=1.0)
        for i in range(400)], source="rv2")
    for horizon in (96.0, 300.0):
        c = cfg_default(horizon_h=horizon)
        r = spread.compute_arrival(tile, [geo.rc_to_latlon(tile.spec, 1, 1)],
                                   wx, T0, c)
        reached = sum(1 for v in r.data if math.isfinite(v))
        far = r.data[1 * 20 + 5]
        print(f"    horizon {horizon:5.0f} h -> {reached:3d} cells reached; "
              f"cell 5 arrival = "
              f"{'UNREACHED' if math.isinf(far) else f'{(far-T0)/3600:.1f} h'}")


def r9_raster_wrap():
    print()
    print("=" * 78)
    print("R9. contracts.Raster.get index wrap (no bounds check)")
    print("=" * 78)
    spec = GridSpec(north_lat=34.25, west_lon=-117.65, cell_m=90.0, nrows=4, ncols=5)
    data = [float(i) for i in range(20)]
    r = Raster(spec, data)
    print("  4x5 raster, data[i] = i.  get(r,c) = data[r*ncols + c] (no guard)")
    for rc in ((0, 0), (3, 4), (0, -1), (-1, 0), (1, 7), (0, 5), (3, 5), (-1, -1)):
        try:
            v = r.get(*rc)
            legal = 0 <= rc[0] < 4 and 0 <= rc[1] < 5
            note = "" if legal else "  <-- OUT OF BOUNDS, returned silently"
            print(f"    get{rc} = {v:5.1f}{note}")
        except IndexError as e:
            print(f"    get{rc} -> IndexError: {e}")
    print("\n  set() has the same hole (silent cross-row / wrap writes).")
    print("  Reachability: safety._raster_value_at delegates to geo.raster_sample")
    print("  which returns None outside the grid, so the SAFETY path is guarded.")
    from wgresponse import safety
    far = LatLon(40.0, -100.0)
    print(f"    safety._raster_value_at(raster, far point) = "
          f"{safety._raster_value_at(r, far)}  (guarded)")
    print("  -> latent API footgun for any NEW caller that indexes directly;")
    print("     not currently exploited on the shipped paths I traced.")
    # who calls .get directly?
    import subprocess
    print("\n  direct .get( callers in wgresponse/:")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import re
    for fn in sorted(os.listdir(os.path.join(root, "wgresponse"))):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(root, "wgresponse", fn), encoding="utf-8").read()
        for m in re.finditer(r"^.*\.get\((?!\")\s*[a-z_]+\s*,\s*[a-z_]+\s*\).*$",
                             src, re.M):
            line = m.group(0).strip()
            if "raster" in line.lower() or "fuel" in line.lower() or \
               "slope" in line.lower() or "aspect" in line.lower() or \
               "elev" in line.lower() or ".get(r" in line or ".get(rr" in line:
                print(f"    {fn}: {line[:90]}")


def r10_median():
    print()
    print("=" * 78)
    print("R10. tools/hindcast_lib.py:693 median head-ratio = sorted[n//2]")
    print("=" * 78)
    print("  head_ratios = sorted(...); med = head_ratios[len(head_ratios)//2]")
    print("  For EVEN n this is the UPPER of the two central values, not their")
    print("  mean — biased HIGH (the flattering direction for a head-ratio).")
    for vals in ([0.5, 1.0, 2.0, 4.0], [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
                 [0.69, 1.2, 1.8, 2.4]):
        s = sorted(vals)
        code = s[len(s) // 2]
        true = statistics.median(s)
        print(f"    n={len(s)} {s} -> code {code:.3f} vs true median "
              f"{true:.3f}  ({'+' if code>true else ''}{100*(code/true-1):.1f}%)")
    print("\n  The published tables report 14, 8, and 18 windows: 14 and 18 are")
    print("  EVEN, so the cited 1.77x / 2.78x / 1.88x median head-ratios are")
    print("  upper-central-value statistics, biased high by this rule.")


if __name__ == "__main__":
    r7_validate_config()
    r8_ros_floor()
    r9_raster_wrap()
    r10_median()
