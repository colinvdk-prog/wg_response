"""RV2-CODE follow-ups:
  R8b: does the 1e-4 ROS floor fabricate arrivals at the MINIMUM LEGAL cell
       size (grid_cell_m = 30, which validate_config explicitly permits) under
       the SHIPPED 96 h horizon?
  R10b: recompute the PUBLISHED median head-ratios with a real median and
       report the correction.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _common import T0, cfg_default, flat_tile

from wgresponse import geo, spread
from wgresponse.contracts import LatLon, WeatherForecast, WeatherSample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def r8b():
    print("=" * 78)
    print("R8b: 1e-4 ROS floor at grid_cell_m = 30 (the minimum validate_config")
    print("     allows) under the SHIPPED horizon_h = 96")
    print("=" * 78)
    print(f"  floored edge cost at 30 m = 30/1e-4 = {30/1e-4:.0f} s = "
          f"{30/1e-4/3600:.1f} h   <  96 h horizon  -> the floor PROPAGATES")
    # A tile of soaking-wet TL6: Rothermel ROS is ~0 but the fuel is 'burnable'
    wx = WeatherForecast(loc=LatLon(34.3, -117.4), samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=0.0, wind_gust_mps=0.0,
                      wind_dir_deg=270.0, rh_pct=99.0, temp_c=1.0)
        for i in range(200)], source="rv2")
    for cell_m in (30.0, 90.0):
        cfg = cfg_default(grid_cell_m=cell_m, horizon_h=96.0)
        tile = flat_tile(34.30, -117.40, cell_m, 5, 30, 186)
        r = spread.compute_arrival(tile, [geo.rc_to_latlon(tile.spec, 2, 1)],
                                   wx, T0, cfg)
        finite = [(i, v) for i, v in enumerate(r.data) if math.isfinite(v)]
        arrivals = sorted((v - T0) / 3600.0 for _, v in finite)
        print(f"\n  cell {cell_m:.0f} m, horizon 96 h, ZERO-ROS wet TL6:")
        print(f"    cells with a FINITE arrival : {len(finite)} "
              f"(1 = seed only, >1 = fabricated spread)")
        if len(arrivals) > 1:
            print(f"    arrival times (h)           : "
                  f"{[f'{a:.1f}' for a in arrivals[:8]]}")
            print(f"    -> {len(arrivals)-1} cells report a fire arrival driven "
                  f"ENTIRELY by the 1e-4 floor, not by any fire physics.")
        else:
            print("    -> floor never binds within the horizon (harmless).")

    # what is the real ROS in that scenario?
    from wgresponse import rothermel
    from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac
    m = dead_fuel_moisture_frac(1.0, 99.0)
    res = rothermel.surface_ros_mps(FUEL_TABLE[186], m, 0.30, 0.60, 0.0, 0.0)
    print(f"\n  (the underlying Rothermel ROS here is {res.ros_mps:.3e} m/s; "
          f"the floor is {spread._ROS_FLOOR_MPS:.0e} m/s)")


def r10b():
    print()
    print("=" * 78)
    print("R10b: the PUBLISHED median head-ratios, recomputed with a real median")
    print("=" * 78)
    arts = [
        ("Calibration 2025 CA (tools/hindcast_data/replay_results.json)",
         os.path.join(ROOT, "tools", "hindcast_data", "replay_results.json")),
    ]
    for label, path in arts:
        if not os.path.exists(path):
            print(f"  {label}: MISSING")
            continue
        d = json.load(open(path))
        for key in ("pairs", "pairs_wide"):
            rows = [p for p in d.get(key, [])
                    if isinstance(p, dict) and p.get("obs_head_m", 0) > 0
                    and p.get("n_new_burnable")]
            if not rows:
                continue
            ratios = sorted(p["head_m_p50"] / p["obs_head_m"] for p in rows
                            if p.get("obs_head_m"))
            n = len(ratios)
            code = ratios[n // 2]
            true = statistics.median(ratios)
            print(f"\n  {label} [{key}]  n = {n} ({'EVEN' if n % 2 == 0 else 'odd'})")
            print(f"    code rule  sorted[n//2] : {code:.4f}")
            print(f"    true median             : {true:.4f}")
            print(f"    overstatement           : "
                  f"{100*(code/true-1):+.2f}%  ({code-true:+.4f}x)")
            print(f"    two central values      : "
                  f"{ratios[n//2-1]:.4f} / {ratios[n//2]:.4f}" if n % 2 == 0
                  else f"    (odd n: rule is exact)")


if __name__ == "__main__":
    r8b()
    r10b()
