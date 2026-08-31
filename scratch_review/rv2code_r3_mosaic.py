"""RV2-CODE R3 follow-up: does the source-only edge rule ACCUMULATE error over
a realistic fuel mosaic, or is it a boundary term?

Algebra: along a path c_0..c_n with per-cell directional rate R_i and equal
edge length d,
    source rule total = sum_{i=0}^{n-1} d / R_i
    exact (half the span in each cell) = sum_i [ (d/2)/R_i + (d/2)/R_{i+1} ]
                                       = (d/2)/R_0 + d*sum_{i=1}^{n-1} 1/R_i + (d/2)/R_n
    source - exact = (d/2)*(1/R_0 - 1/R_n)      <-- BOUNDARY TERM ONLY
    spec rule (arithmetic mean of RATES) total = sum d/mean(R_i,R_{i+1})
      -- arithmetic-mean-of-rates != harmonic; this error DOES accumulate.

Test on a random fuel mosaic with realistic patch sizes.
"""
from __future__ import annotations

import math
import statistics
import sys

from _common import T0, cfg_default, flat_tile

import rv2code_integrator as RI
from wgresponse import geo
from wgresponse.contracts import (LatLon, Raster, UNREACHED, WeatherForecast,
                                  WeatherSample)
from wgresponse.contracts import seeded_rng


def wxf(t0, ws=8.0, n=200, wdir=270.0):
    return WeatherForecast(loc=LatLon(34.1, -117.1), samples=[
        WeatherSample(t_s=t0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=ws,
                      wind_dir_deg=wdir, rh_pct=15.0, temp_c=32.0)
        for i in range(n)], source="rv2")


def mosaic_tile(nrows, ncols, patch_cells, codes, seed="rv2-mosaic"):
    """Blocky random mosaic: patch_cells x patch_cells blocks drawn from codes."""
    t = flat_tile(34.40, -117.50, 90.0, nrows, ncols, codes[0])
    rng = seeded_rng(seed, patch_cells, ncols)
    pr = (nrows + patch_cells - 1) // patch_cells
    pc = (ncols + patch_cells - 1) // patch_cells
    block = [[codes[rng.randrange(len(codes))] for _ in range(pc)] for _ in range(pr)]
    for r in range(nrows):
        for c in range(ncols):
            t.fuel.set(r, c, float(block[r // patch_cells][c // patch_cells]))
    return t


def stats(a, b, t0):
    """Compare two arrival rasters over cells finite in BOTH."""
    da, db = a.data, b.data
    diffs = [(da[i] - db[i]) / 60.0 for i in range(len(da))
             if math.isfinite(da[i]) and math.isfinite(db[i])]
    if not diffs:
        return None
    diffs.sort()
    n = len(diffs)
    return dict(n=n, mean=statistics.fmean(diffs), med=diffs[n // 2],
                p05=diffs[int(0.05 * n)], p95=diffs[int(0.95 * n)],
                mn=diffs[0], mx=diffs[-1],
                n_a_inf=sum(1 for x in da if math.isinf(x)),
                n_b_inf=sum(1 for x in db if math.isinf(x)))


def main():
    cfg = cfg_default()
    wx = wxf(T0)
    print("=" * 84)
    print("R3 mosaic: does source-only edge cost accumulate error? "
          "(minutes; +ve = rule LATER than exact)")
    print("=" * 84)
    print(f"{'mosaic':>26s} {'rule':>7s} {'n':>6s} {'mean':>8s} {'med':>8s} "
          f"{'p05':>8s} {'p95':>8s} {'min':>9s} {'max':>9s}")

    cases = [
        ("GR2/TL6 90m patches (1 cell)", 1, (102, 186)),
        ("GR2/TL6 450m patches (5)", 5, (102, 186)),
        ("GR2/TL6 1.8km patches (20)", 20, (102, 186)),
        ("GR2/GS2/SH2/TU5/TL6 5-cell", 5, (102, 122, 142, 165, 186)),
    ]
    for label, pc, codes in cases:
        tile = mosaic_tile(60, 60, pc, codes)
        ign = [geo.rc_to_latlon(tile.spec, 30, 2)]
        ok = RI.verify_identity(tile, ign, wx, T0, cfg, label=label)
        assert ok
        exact = RI.arrival(tile, ign, wx, T0, cfg, edge_rule="harm")
        for rule in ("source", "mean"):
            got = RI.arrival(tile, ign, wx, T0, cfg, edge_rule=rule)
            s = stats(got, exact, T0)
            print(f"{label:>26s} {rule:>7s} {s['n']:6d} {s['mean']:8.2f} "
                  f"{s['med']:8.2f} {s['p05']:8.2f} {s['p95']:8.2f} "
                  f"{s['mn']:9.2f} {s['mx']:9.2f}")

    # Boundary-term prediction: |source - exact| <= (d/2)*(1/R_last) roughly.
    print("\n  Boundary-term check: half-cell traversal time in the SLOWEST fuel")
    print("  is the predicted scale of the source-rule error.")
    from wgresponse import rothermel
    from wgresponse.fuels import FUEL_TABLE
    for code in (102, 186):
        f = FUEL_TABLE[code]
        bed = rothermel.fuel_bed(f, 0.0567, cfg.live_herb_frac, cfg.live_woody_frac)
        u = rothermel.midflame_wind_mps(8.0, f)
        ue, brg = RI._msv(u, 0.0, 270.0, -1.0)
        res = rothermel.surface_ros_from_bed(bed, ue, 0.0)
        lb = rothermel.ellipse_lb(res.effective_wind_mps)
        rh_ = rothermel.directional_ros_mps(res.ros_mps, lb, 0.0)
        print(f"    {f.name[:14]:14s} head R={rh_:.4f} m/s -> half-cell (45 m) = "
              f"{45.0/rh_/60:6.2f} min")

    # Same test but measuring what the CONSUMER sees: p10 at 3/6/10 km out.
    print("\n  Consumer view: arrival at fixed ranges east of the ignition")
    tile = mosaic_tile(60, 120, 5, (102, 122, 142, 165, 186))
    ign = [geo.rc_to_latlon(tile.spec, 30, 2)]
    assert RI.verify_identity(tile, ign, wx, T0, cfg, label="consumer")
    rasters = {r: RI.arrival(tile, ign, wx, T0, cfg, edge_rule=r)
               for r in ("source", "mean", "harm")}
    print(f"    {'col':>4s} {'km':>6s} {'source h':>10s} {'spec-mean h':>12s} "
          f"{'exact h':>9s} {'src-exact min':>14s} {'mean-exact min':>15s}")
    for c in (20, 40, 60, 90, 118):
        vals = {r: rasters[r].data[30 * 120 + c] for r in rasters}
        if any(math.isinf(v) for v in vals.values()):
            continue
        h = {r: (vals[r] - T0) / 3600.0 for r in vals}
        print(f"    {c:4d} {(c-2)*0.09:6.2f} {h['source']:10.3f} {h['mean']:12.3f} "
              f"{h['harm']:9.3f} {(h['source']-h['harm'])*60:14.2f} "
              f"{(h['mean']-h['harm'])*60:15.2f}")


if __name__ == "__main__":
    sys.exit(main())
