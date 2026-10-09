"""Grid-orientation study, heterogeneous terrain (2026-10-09; NOT product code).

On a uniform tile the engine's own analytic ellipse is the reference. On a
heterogeneous tile (fuel mosaic, cell-to-cell slope / aspect) there is no
closed form, so the reference is the CONTINUUM LIMIT of the engine's own
per-cell physics, approximated by solving the same tile REFINED k x k (every
cell split into k x k sub-cells that inherit its fuel, slope and aspect) with
the any-angle relaxation on: inside each original cell the fire then runs
straight at its exact elliptical time, and it can bend at sub-cell resolution
where cells meet. Arrival at an original cell = the refined arrival at its
centre sub-cell (k odd). Convergence is checked by comparing k = 5 / 7 / 9.

Usage: py -3 scratch_review/grid_orientation_hetero.py KIND [k ...]
  KIND = noise | mosaic | real:<tile key>:<rows>:<cols>
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from grid_orientation_common import (MPH, T0, const_wx, flat_tile)  # noqa: E402
from wgresponse import geo, spread, terrain  # noqa: E402
from wgresponse.contracts import (GridSpec, ResponseConfig,  # noqa: E402
                                  TerrainTile)


def refine(tile: TerrainTile, k: int) -> TerrainTile:
    s = tile.spec
    spec = GridSpec(north_lat=s.north_lat, west_lon=s.west_lon,
                    cell_m=s.cell_m / k, nrows=s.nrows * k, ncols=s.ncols * k)

    def up(r):
        out = []
        for rr in range(s.nrows):
            row = r.data[rr * s.ncols:(rr + 1) * s.ncols]
            fine = [v for v in row for _ in range(k)]
            for _ in range(k):
                out.extend(fine)
        return geo.make_raster(spec, fill=0.0).__class__(spec, out)

    return TerrainTile(spec=spec, fuel=up(tile.fuel), slope_deg=up(tile.slope_deg),
                       aspect_deg=up(tile.aspect_deg), elev_m=up(tile.elev_m),
                       source=tile.source + f"-x{k}")


def coarse_of(fine_raster, k: int, spec) -> list:
    """Arrival of each original cell = its centre sub-cell's."""
    fn = spec.ncols * k
    h = k // 2
    out = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            out.append(fine_raster.data[(r * k + h) * fn + c * k + h])
    return out


def noise_tile(n=61, cell_m=100.0, code=122, seed=1, smooth=0):
    """Uniform fuel, cell-to-cell slope ~ |N(8, 6)| deg and random aspect
    (the spread of the real mountain tiles' head bearings: median 2-3 deg,
    p90 8-15 deg off the wind)."""
    rng = random.Random(seed)
    t = flat_tile(n, n, cell_m, code)
    for i in range(n * n):
        t.slope_deg.data[i] = min(35.0, abs(rng.gauss(8.0, 6.0)))
        t.aspect_deg.data[i] = rng.uniform(0.0, 360.0)
    return t


def mosaic_tile(n=61, cell_m=100.0, seed=2):
    """Fuel mosaic: GS2 grass-shrub with SH2 / TL3 / GR2 patches of 1-4
    cells and a few non-burnable blobs; mild noisy slope."""
    rng = random.Random(seed)
    t = flat_tile(n, n, cell_m, 122)
    codes = (122, 122, 122, 142, 183, 102)
    for _ in range(int(n * n * 0.08)):
        r0, c0 = rng.randrange(n), rng.randrange(n)
        code = rng.choice(codes[3:])
        w, h = rng.randint(1, 4), rng.randint(1, 4)
        for r in range(r0, min(n, r0 + h)):
            for c in range(c0, min(n, c0 + w)):
                t.fuel.data[r * n + c] = float(code)
    for _ in range(int(n * n * 0.004)):
        r0, c0 = rng.randrange(n), rng.randrange(n)
        for r in range(r0, min(n, r0 + 2)):
            for c in range(c0, min(n, c0 + 2)):
                t.fuel.data[r * n + c] = 91.0
    for i in range(n * n):
        t.slope_deg.data[i] = min(25.0, abs(rng.gauss(4.0, 3.0)))
        t.aspect_deg.data[i] = rng.uniform(0.0, 360.0)
    return t


def real_tile(key: str, r0: int, c0: int, size: int):
    """A size x size window of a committed real tile."""
    full = terrain.load_ascii_tile(os.path.join(os.path.dirname(HERE), "data",
                                                "tiles", f"{key}.wgt"))
    s = full.spec
    lat = s.north_lat - r0 * s.cell_m / geo.M_PER_DEG_LAT
    lon = s.west_lon + c0 * s.cell_m / geo.m_per_deg_lon(lat)
    spec = GridSpec(north_lat=lat, west_lon=lon, cell_m=s.cell_m,
                    nrows=size, ncols=size)

    def cut(r):
        out = []
        for rr in range(r0, r0 + size):
            out.extend(r.data[rr * s.ncols + c0:rr * s.ncols + c0 + size])
        return r.__class__(spec, out)

    return TerrainTile(spec=spec, fuel=cut(full.fuel), slope_deg=cut(full.slope_deg),
                       aspect_deg=cut(full.aspect_deg), elev_m=cut(full.elev_m),
                       source=f"{key}-window")


def solve(tile, ign_rc, mph, wdir, cfg, hours):
    ign = geo.rc_to_latlon(tile.spec, *ign_rc)
    wx = const_wx(ign, mph * MPH, wdir, rh=13.0, temp=30.0)
    c = dataclasses.replace(cfg, horizon_h=hours + 0.5)
    tic = time.perf_counter()
    r = spread.compute_arrival(tile, [ign], wx, T0, c)
    return r, time.perf_counter() - tic


def compare(name, data, ref, t_end_s):
    """Errors of `data` against `ref` on cells either reaches by t_end."""
    burned_ref = sum(1 for v in ref if v <= t_end_s)
    burned = sum(1 for v in data if v <= t_end_s)
    inter = sum(1 for a, b in zip(data, ref) if a <= t_end_s and b <= t_end_s)
    union = sum(1 for a, b in zip(data, ref) if a <= t_end_s or b <= t_end_s)
    rel = sorted((a - b) / (b - T0) for a, b in zip(data, ref)
                 if b <= t_end_s and b > T0 and math.isfinite(a))
    late = sum(1 for x in rel if x > 0.05) / max(1, len(rel))
    early = sum(1 for x in rel if x < -0.05) / max(1, len(rel))
    med = rel[len(rel) // 2] if rel else float("nan")
    p90 = rel[int(0.9 * len(rel))] if rel else float("nan")
    p10 = rel[int(0.1 * len(rel))] if rel else float("nan")
    return {"name": name, "area_ratio": burned / max(1, burned_ref),
            "iou": inter / max(1, union), "med_rel": med, "p10_rel": p10,
            "p90_rel": p90, "late_gt5pct": late, "early_gt5pct": early}


if __name__ == "__main__":
    kind = sys.argv[1]
    ks = [int(x) for x in sys.argv[2:]] or [5]
    hours = 3.0
    if kind == "noise":
        tile = noise_tile()
        ign_rc = (30, 8)
        cases = [(20.0, 270.0), (30.0, 281.25)]
    elif kind == "mosaic":
        tile = mosaic_tile()
        ign_rc = (30, 8)
        cases = [(20.0, 270.0), (30.0, 281.25)]
    else:
        _, key, rr, cc = kind.split(":")
        tile = real_tile(key, int(rr), int(cc), 61)
        ign_rc = (30, 30)
        cases = [(15.0, 240.0), (25.0, 240.0)]
    base = ResponseConfig(spotting_enabled=False, grass_ros_floor_frac=0.10,
                          ros_floor_grass_only=True)
    fix = dataclasses.replace(base, spread_any_angle=True)
    t_end = T0 + hours * 3600.0
    out = []
    for mph, wdir in cases:
        legacy, s_leg = solve(tile, ign_rc, mph, wdir, base, hours)
        strict, s_fix = solve(tile, ign_rc, mph, wdir, fix, hours)
        refs = {}
        for k in ks:
            ft = refine(tile, k)
            rf, s_ref = solve(ft, (ign_rc[0] * k + k // 2, ign_rc[1] * k + k // 2),
                              mph, wdir, fix, hours)
            refs[k] = (coarse_of(rf, k, tile.spec), s_ref)
        kref = max(ks)
        ref = refs[kref][0]
        print(f"== {kind} {mph:.0f} mph from {wdir}: reference k={kref} "
              f"({refs[kref][1]:.1f} s); legacy {s_leg:.2f} s, any-angle {s_fix:.2f} s")
        rows = [compare("legacy", legacy.data, ref, t_end),
                compare("any-angle", strict.data, ref, t_end)]
        for k in ks:
            if k != kref:
                rows.append(compare(f"ref k={k}", refs[k][0], ref, t_end))
        for row in rows:
            print(f"   {row['name']:12s} area {row['area_ratio']:.3f} IoU {row['iou']:.3f} | "
                  f"rel err median {row['med_rel']:+.3f} p10 {row['p10_rel']:+.3f} "
                  f"p90 {row['p90_rel']:+.3f} | late>5% {row['late_gt5pct']:.2f} "
                  f"early>5% {row['early_gt5pct']:.2f}", flush=True)
        out.append({"kind": kind, "mph": mph, "wdir": wdir, "rows": rows})
