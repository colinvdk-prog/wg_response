"""RV2-CODE N1b: the controlled test REFUTED 'domain truncation biases p10'
for uniform terrain (0.00 min over 3095 cells). Two follow-ups:

X1. Does truncation bite when the TRUE fastest path leaves the domain?
    Put a non-burnable barrier inside the production window so the fastest
    route to the far side must detour AROUND it — outside the window.

X2. How much p10 movement does the documented +-45 m raster quantization
    actually cause?  Same domain size, origin shifted by half a cell.
    (This is what my first, uncontrolled N1 pass was really measuring.)
"""
from __future__ import annotations

import math
import statistics
import sys

from _common import T0, cfg_default

from wgresponse import geo, safety, spread
from wgresponse.contracts import (GridSpec, LatLon, Raster, TerrainTile,
                                  WeatherForecast, WeatherSample)

F = LatLon(34.2000, -117.6000)
CELL = 90.0


def mk(spec, barrier=None):
    n = spec.nrows * spec.ncols
    fuel = Raster(spec, [102.0] * n)
    if barrier:
        for (r, c) in barrier:
            if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
                fuel.set(r, c, 99.0)          # NB non-burnable
    return TerrainTile(spec=spec, fuel=fuel,
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="rv2")


def wx():
    return WeatherForecast(loc=F, samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=14.0,
                      wind_gust_mps=14.0, wind_dir_deg=270.0, rh_pct=10.0,
                      temp_c=35.0) for i in range(120)], source="rv2")


def x1_barrier():
    print("=" * 80)
    print("X1. truncation WITH a barrier: does the deleted detour change p10?")
    print("=" * 80)
    cfg = cfg_default()
    half = 12_000.0
    nw = geo.destination(geo.destination(F, 0.0, half), 270.0, half)
    n = int(2 * half / CELL)
    wspec = GridSpec(north_lat=nw.lat, west_lon=nw.lon, cell_m=CELL,
                     nrows=n, ncols=n)
    frc = geo.latlon_to_rc(wspec, F)
    # production window: 44 rows x 78 cols, ignition near the west edge
    prows, pcols = 44, 78
    r0, c0 = frc[0] - 22, frc[1] - 8
    # BARRIER: a non-burnable wall spanning the FULL height of the production
    # window, 30 cols east of the ignition, but only that height -> the fire
    # can go around it OUTSIDE the window, not inside.
    bar_c = c0 + 30
    barrier_w = [(r, bar_c) for r in range(r0, r0 + prows)]
    barrier_w += [(r, bar_c + 1) for r in range(r0, r0 + prows)]
    barrier_p = [(r - r0, bar_c - c0) for r in range(r0, r0 + prows)]
    barrier_p += [(r - r0, bar_c + 1 - c0) for r in range(r0, r0 + prows)]

    wide = mk(wspec, barrier_w)
    pnw = geo.rc_to_latlon(wspec, r0, c0)
    pspec = GridSpec(
        north_lat=pnw.lat + 0.5 * CELL / geo.M_PER_DEG_LAT,
        west_lon=pnw.lon - 0.5 * CELL / geo.m_per_deg_lon(pnw.lat),
        cell_m=CELL, nrows=prows, ncols=pcols)
    prod = mk(pspec, barrier_p)

    print(f"  wide {n}x{n} ({2*half/1000:.0f} km), production window "
          f"{prows}x{pcols} ({prows*CELL/1000:.1f} x {pcols*CELL/1000:.1f} km)")
    print(f"  non-burnable wall 2 cells wide spanning the FULL window height,")
    print(f"  {30*CELL/1000:.1f} km east of the ignition. In the WIDE domain the")
    print(f"  fire can flank around the wall's ends; in the production window it")
    print(f"  cannot (the wall touches both edges).")

    ep = spread.ensemble_arrival(prod, [F], wx(), T0, cfg, incident_id="I-B")
    ew = spread.ensemble_arrival(wide, [F], wx(), T0, cfg, incident_id="I-B")

    east = []
    for r in range(prows):
        for c in range(bar_c - c0 + 2, pcols):
            vp = ep.p10.data[r * pcols + c]
            vw = ew.p10.data[(r0 + r) * n + (c0 + c)]
            east.append((vp, vw))
    unreached_p = sum(1 for vp, vw in east if math.isinf(vp))
    unreached_w = sum(1 for vp, vw in east if math.isinf(vw))
    both = [((vp - vw) / 60.0) for vp, vw in east
            if math.isfinite(vp) and math.isfinite(vw)]
    print(f"\n  cells EAST of the wall (inside the window): {len(east)}")
    print(f"    UNREACHED in the production domain : {unreached_p} "
          f"({100*unreached_p/len(east):.0f}%)")
    print(f"    UNREACHED in the wide (truth) domain: {unreached_w} "
          f"({100*unreached_w/len(east):.0f}%)")
    if both:
        both.sort()
        print(f"    p10 diff where both finite: mean "
              f"{statistics.fmean(both):+.1f} min, max LATE {both[-1]:+.1f} min")
    lost = unreached_p - unreached_w
    print(f"\n  >>> {lost} cells the fire ACTUALLY reaches are reported "
          f"UNREACHED (= 'safe forever') by the production-sized domain "
          f"({100*lost/max(1,len(east)):.0f}% of the far side).")
    print("      Those cells are 'no fire ever' to the alert ladder AND an")
    print("      unconstrained +inf corridor sample to every crew route.")


def x2_quantization():
    print()
    print("=" * 80)
    print("X2. raster +-45 m quantization: how far does p10 move?")
    print("=" * 80)
    cfg = cfg_default()
    half = 12_000.0
    base_nw = geo.destination(geo.destination(F, 0.0, half), 270.0, half)
    n = int(2 * half / CELL)
    out = {}
    for label, (dlat_m, dlon_m) in (("origin", (0.0, 0.0)),
                                    ("+half cell E", (0.0, CELL / 2)),
                                    ("+half cell S", (CELL / 2, 0.0)),
                                    ("+half cell SE", (CELL / 2, CELL / 2))):
        o = geo.destination(geo.destination(base_nw, 180.0, dlat_m), 90.0, dlon_m)
        spec = GridSpec(north_lat=o.lat, west_lon=o.lon, cell_m=CELL,
                        nrows=n, ncols=n)
        ens = spread.ensemble_arrival(mk(spec), [F], wx(), T0, cfg,
                                      incident_id="I-Q")
        out[label] = ens
    base = out["origin"]
    print(f"  p10 at fixed client locations under a half-cell grid shift:")
    print(f"  {'bearing':>8s} {'km':>5s} " + "".join(
        f"{k:>15s}" for k in out) + f"{'spread min':>12s}")
    for brg in (90.0, 75.0, 60.0, 45.0, 30.0, 120.0, 135.0):
        for km in (2.0, 5.0):
            loc = geo.destination(F, brg, km * 1000.0)
            vals = []
            for k in out:
                e = spread.eta_at(out[k], loc)
                vals.append(e.eta_p10_s)
            if any(math.isinf(v) for v in vals):
                continue
            hs = [(v - T0) / 3600.0 for v in vals]
            spread_min = (max(vals) - min(vals)) / 60.0
            print(f"  {brg:8.0f} {km:5.1f} " +
                  "".join(f"{h:15.2f}" for h in hs) + f"{spread_min:12.1f}")
    print("\n  -> the documented '+-45 m quantization' limit is worth TENS OF")
    print("     MINUTES to HOURS of p10 at off-head-axis clients, because the")
    print("     flanking gradient there is nearly flat in time.")


if __name__ == "__main__":
    x1_barrier()
    x2_quantization()
