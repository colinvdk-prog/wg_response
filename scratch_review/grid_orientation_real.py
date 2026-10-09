"""Grid-orientation study on the REAL committed tiles (2026-10-09; NOT
product code): does more wind still burn less?

For a one-click scenario's tile, ignition and wind direction (built through
SimSessionManager exactly like the page, then solved directly), constant
weather (13 % RH, 30 C), the simulator's physics (grass floor 0.10, grass-only
floor class) with spotting OFF, nominal compute_arrival: acres at 1 / 3 / 6 h
for wind 10-40 mph — legacy, the any-angle knob, and (optional) the refined
continuum reference (scratch_review/grid_orientation_hetero.py: the same tile
split k x k, any-angle on) with the knob's arrival-time error against it.

Usage: py -3 scratch_review/grid_orientation_real.py SCENARIO [k_ref] [mph,...]
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import grid_orientation_hetero as het  # noqa: E402
from grid_orientation_common import MPH, T0, const_wx  # noqa: E402
from wgresponse import geo, server, spread  # noqa: E402

scen = sys.argv[1]
k_ref = int(sys.argv[2]) if len(sys.argv) > 2 else 0
mphs = ([float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3
        else [10, 15, 20, 25, 30, 40])

mgr = server.SimSessionManager()
code, out = mgr.new_session({"scenario": scen, "cfg": {"spotting_enabled": False}})
s = mgr._entry(out["session_id"]).session
f = next(iter(s._fires.values()))
tile = s._tile
spec = tile.spec
WDIR = float(os.environ.get("WDIR", "nan"))
cfg0 = dataclasses.replace(s._cfg, spotting_enabled=False, horizon_h=6.5)
fix = dataclasses.replace(cfg0, spread_any_angle=True)
rc = geo.latlon_to_rc(spec, f.origin)
wdir = f.wind_dir_deg if WDIR != WDIR else WDIR
cell_ac = spec.cell_m ** 2 / 4046.8564224
hours = (1.0, 3.0, 6.0)


def acres(data, h):
    t = T0 + h * 3600.0
    return sum(1 for v in data if v <= t) * cell_ac


rows = []
print(f"{scen}: tile {spec.nrows}x{spec.ncols} @ {spec.cell_m:.0f} m, wind from "
      f"{wdir:.2f}, floor {cfg0.grass_ros_floor_frac} grass_only "
      f"{cfg0.ros_floor_grass_only}")
for mph in mphs:
    ign = geo.rc_to_latlon(spec, *rc)
    wx = const_wx(ign, mph * MPH, wdir, rh=13.0, temp=30.0)
    t = time.perf_counter()
    leg = spread.compute_arrival(tile, [ign], wx, T0, cfg0)
    t_leg = time.perf_counter() - t
    t = time.perf_counter()
    aa = spread.compute_arrival(tile, [ign], wx, T0, fix)
    t_aa = time.perf_counter() - t
    row = {"mph": mph, "legacy": [acres(leg.data, h) for h in hours],
           "any_angle": [acres(aa.data, h) for h in hours],
           "legacy_s": t_leg, "any_angle_s": t_aa}
    line = (f" {mph:4.0f} mph | legacy {' '.join(f'{a:6.0f}' for a in row['legacy'])}"
            f" | any-angle {' '.join(f'{a:6.0f}' for a in row['any_angle'])}"
            f" | {t_leg:.2f} s -> {t_aa:.2f} s")
    if k_ref:
        ft = het.refine(tile, k_ref)
        t = time.perf_counter()
        rf = spread.compute_arrival(
            ft, [geo.rc_to_latlon(ft.spec, rc[0] * k_ref + k_ref // 2,
                                  rc[1] * k_ref + k_ref // 2)],
            const_wx(ign, mph * MPH, wdir, rh=13.0, temp=30.0), T0, fix)
        t_ref = time.perf_counter() - t
        ref = het.coarse_of(rf, k_ref, spec)
        row["ref"] = [acres(ref, h) for h in hours]
        row["ref_s"] = t_ref
        t6 = T0 + 6 * 3600.0
        row["cmp_legacy"] = het.compare("legacy", leg.data, ref, t6)
        row["cmp_any_angle"] = het.compare("any-angle", aa.data, ref, t6)
        line += (f" | ref k={k_ref} {' '.join(f'{a:6.0f}' for a in row['ref'])}"
                 f" ({t_ref:.0f} s) | median rel err legacy "
                 f"{row['cmp_legacy']['med_rel']:+.2f} any-angle "
                 f"{row['cmp_any_angle']['med_rel']:+.2f} | IoU "
                 f"{row['cmp_legacy']['iou']:.2f} -> {row['cmp_any_angle']['iou']:.2f}")
    print(line, flush=True)
    rows.append(row)
if k_ref:      # only a run with the reference is a result worth keeping
    with open(os.path.join(HERE, f"grid_orientation_real_{scen}.json"), "w") as fh:
        json.dump({"scenario": scen, "k_ref": k_ref, "rows": rows}, fh, indent=1)
