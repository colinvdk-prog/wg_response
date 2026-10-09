"""Grid-orientation probe (2026-10-09; NOT product code). The measurement
behind docs/review/grid_orientation_2026-10-09.md.

Flat, uniform tile sized to hold the 6-h analytic fire; one ignition;
constant weather (15 % RH, 30 C); spotting OFF; nominal compute_arrival;
100 m cells. For each case: the LEGACY engine, the lattice POLYGON a
16-direction lattice can reach in a uniform medium (the polygon inscribed in
the ellipse with corners on the 16 lattice bearings — grid_orientation_
common.polygon), the engine's own ANALYTIC ellipse, and the engine with
cfg.spread_any_angle (FIX).

  python scratch_review/grid_orientation_probe.py wind    [fuels] [hours]
      wind 0-40 mph at 270 (on a lattice axis) and 281.25 (between)
  python scratch_review/grid_orientation_probe.py dir     [fuels] [mph,..] [hours]
      wind direction swept 270 -> 315 in 1.25-degree steps
  python scratch_review/grid_orientation_probe.py lbcap   [hours]
      GR2 / SH5 at 20-40 mph with the length-to-breadth cap of 8 on and off
Writes scratch_review/grid_orientation_probe_<mode>.json.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from grid_orientation_common import (FUELS, ResponseConfig, rothermel,  # noqa: E402
                                     run_case)

BASE = ResponseConfig(spotting_enabled=False, horizon_h=12.0)
FIX = dataclasses.replace(BASE, spread_any_angle=True)


def row(code, mph, wdir, hours):
    leg = run_case(code, mph, wdir, BASE, hours=hours, want_iou=True)
    fix = run_case(code, mph, wdir, FIX, hours=hours, want_iou=True)
    out = {"code": code, "mph": mph, "wdir": wdir, "hours": hours,
           "head_ros_mps": leg["head_mps"], "lb": leg["lb"],
           "analytic": leg["ana"], "polygon": leg["poly"],
           "legacy": dict(leg["meas"], iou=leg["iou"], solve_s=leg["solve_s"]),
           "fix": dict(fix["meas"], iou=fix["iou"], solve_s=fix["solve_s"])}
    return out


def show(r):
    a, p, l, f = r["analytic"], r["polygon"], r["legacy"], r["fix"]
    print(f"  {r['mph']:4.0f} mph from {r['wdir']:6.2f} lb {r['lb']:4.2f} | acres "
          f"legacy {l['acres']:7.0f} polygon {p['acres']:7.0f} FIX {f['acres']:7.0f} "
          f"analytic {a['acres']:7.0f} | head km {l['head_m']/1e3:5.2f} "
          f"{f['head_m']/1e3:5.2f} {a['head_m']/1e3:5.2f} | half-width km "
          f"{l['half_m']/1e3:4.2f} {f['half_m']/1e3:4.2f} {a['half_m']/1e3:4.2f} | "
          f"IoU {l['iou']:.2f} -> {f['iou']:.3f} | {l['solve_s']:.2f} s -> "
          f"{f['solve_s']:.2f} s", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "wind"
    rows = []
    if mode == "wind":
        names = sys.argv[2].split(",") if len(sys.argv) > 2 else list(FUELS)
        hours = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
        for name in names:
            for wdir in (270.0, 281.25):
                print(f"== {name} wind FROM {wdir} ({hours:g} h)")
                for mph in (0, 5, 10, 15, 20, 25, 30, 40):
                    r = row(FUELS[name], mph, wdir, hours)
                    r["fuel"] = name
                    rows.append(r)
                    show(r)
    elif mode == "dir":
        names = sys.argv[2].split(",") if len(sys.argv) > 2 else ["GR2", "SH5"]
        mphs = ([float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3
                else [20.0, 40.0])
        hours = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
        for name in names:
            for mph in mphs:
                print(f"== {name} {mph:g} mph ({hours:g} h), direction sweep")
                for k in range(37):
                    r = row(FUELS[name], mph, 270.0 + 1.25 * k, hours)
                    r["fuel"] = name
                    rows.append(r)
                    show(r)
    elif mode == "lbcap":
        hours = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
        capped = rothermel.ellipse_lb

        def uncapped(u_eff_mps):
            import math
            u = max(0.0, u_eff_mps) * rothermel._MPS_TO_MPH
            lb = (0.936 * math.exp(0.2566 * u) + 0.461 * math.exp(-0.1548 * u)
                  - 0.397)
            return max(lb, 1.0)

        for label, fn in (("cap 8", capped), ("no cap", uncapped)):
            rothermel.ellipse_lb = fn
            try:
                for name in ("GR2", "SH5"):
                    print(f"== {label}: {name} from 281.25 ({hours:g} h)")
                    for mph in (20, 25, 30, 35, 40):
                        r = row(FUELS[name], mph, 281.25, hours)
                        r["fuel"] = name
                        r["lb_cap"] = label
                        rows.append(r)
                        show(r)
            finally:
                rothermel.ellipse_lb = capped
    with open(os.path.join(HERE, f"grid_orientation_probe_{mode}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
