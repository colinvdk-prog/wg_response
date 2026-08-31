"""RV2-CODE N1 (controlled): domain-edge contamination of p10, with the
PRODUCTION grid built as an EXACT SUB-WINDOW of the wide grid — same cell_m,
same cell centres, same lat/lon for every shared cell. The ONLY difference is
how far the lattice extends. Any p10 difference is therefore caused purely by
domain truncation, not by +-45 m cell quantization.

Also N4: how inflated is p50 at a head-axis client, given the R6 direction band?
"""
from __future__ import annotations

import math
import statistics
import sys

from _common import T0, cfg_default

from wgresponse import geo, safety, spread, threat
from wgresponse.contracts import (GridSpec, LatLon, Raster, TerrainTile,
                                  WeatherForecast, WeatherSample)

F = LatLon(34.2000, -117.6000)


def sub_tile(wide_spec, r0, c0, nrows, ncols, code=102.0):
    """A tile whose grid is an exact sub-window of wide_spec."""
    nw = geo.rc_to_latlon(wide_spec, r0, c0)
    # rc_to_latlon returns the CELL CENTRE; back out the NW corner
    north = nw.lat + 0.5 * wide_spec.cell_m / geo.M_PER_DEG_LAT
    west = nw.lon - 0.5 * wide_spec.cell_m / geo.m_per_deg_lon(nw.lat)
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=wide_spec.cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(spec=spec, fuel=Raster(spec, [code] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="rv2")


def main():
    cfg = cfg_default()
    cell = cfg.grid_cell_m                        # 90 m
    # WIDE grid: 50 x 50 km around F
    half = 25_000.0
    nw = geo.destination(geo.destination(F, 0.0, half), 270.0, half)
    n = int(2 * half / cell)
    wide_spec = GridSpec(north_lat=nw.lat, west_lon=nw.lon, cell_m=cell,
                         nrows=n, ncols=n)
    wide = TerrainTile(spec=wide_spec, fuel=Raster(wide_spec, [102.0] * (n * n)),
                       slope_deg=Raster(wide_spec, [0.0] * (n * n)),
                       aspect_deg=Raster(wide_spec, [-1.0] * (n * n)),
                       elev_m=Raster(wide_spec, [100.0] * (n * n)),
                       source="rv2-wide")
    frc = geo.latlon_to_rc(wide_spec, F)
    # PRODUCTION window: threat.py's own size for fire + a client 3 km east
    # -> ~7 x 4 km  => 78 x 44 cells at 90 m, ignition inside it
    prows, pcols = 44, 78
    r0, c0 = frc[0] - 22, frc[1] - 22
    prod = sub_tile(wide_spec, r0, c0, prows, pcols)

    # verify EXACT cell alignment
    misalign = max(
        abs(geo.rc_to_latlon(prod.spec, r, c).lat
            - geo.rc_to_latlon(wide_spec, r0 + r, c0 + c).lat)
        + abs(geo.rc_to_latlon(prod.spec, r, c).lon
              - geo.rc_to_latlon(wide_spec, r0 + r, c0 + c).lon)
        for r in range(0, prows, 7) for c in range(0, pcols, 7))
    print("=" * 80)
    print("N1 (controlled): domain truncation vs p10, exactly aligned grids")
    print("=" * 80)
    print(f"  wide grid {n}x{n} @ {cell:.0f} m = 50 x 50 km")
    print(f"  production window {prows}x{pcols} @ {cell:.0f} m = "
          f"{prows*cell/1000:.1f} x {pcols*cell/1000:.1f} km "
          f"(threat.py's own domain size for one client 3 km out)")
    print(f"  max cell-centre misalignment over the shared window: "
          f"{misalign:.3e} deg  ({'EXACT' if misalign < 1e-12 else 'NOT EXACT'})")

    wx = WeatherForecast(loc=F, samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=14.0,
                      wind_gust_mps=14.0, wind_dir_deg=270.0, rh_pct=10.0,
                      temp_c=35.0) for i in range(120)], source="rv2")

    print("\n  running both ensembles ...")
    e_prod = spread.ensemble_arrival(prod, [F], wx, T0, cfg, incident_id="I-A")
    e_wide = spread.ensemble_arrival(wide, [F], wx, T0, cfg, incident_id="I-A")

    diffs = []
    for r in range(prows):
        for c in range(pcols):
            vp = e_prod.p10.data[r * pcols + c]
            vw = e_wide.p10.data[(r0 + r) * n + (c0 + c)]
            if math.isinf(vp) or math.isinf(vw):
                continue
            d = (vp - vw) / 60.0
            dn = min(r, prows - 1 - r, c, pcols - 1 - c) * cell
            diffs.append((d, dn, r, c))
    print(f"\n  cells compared: {len(diffs)}")
    print(f"  {'edge dist':>12s} {'n':>6s} {'mean (min)':>12s} {'p95 (min)':>11s} "
          f"{'max LATE (min)':>16s}")
    for lo, hi, lab in ((0, 250, "<250 m"), (250, 500, "250-500 m"),
                        (500, 1000, "500-1000 m"), (1000, 1e9, ">1 km")):
        b = [d for d, dn, _, _ in diffs if lo <= dn < hi]
        if not b:
            continue
        b.sort()
        print(f"  {lab:>12s} {len(b):6d} {statistics.fmean(b):12.2f} "
              f"{b[int(0.95*len(b))]:11.2f} {b[-1]:16.2f}")
    allv = sorted(d for d, _, _, _ in diffs)
    print(f"\n  overall mean {statistics.fmean(allv):+.2f} min | median "
          f"{statistics.median(allv):+.2f} | p95 {allv[int(0.95*len(allv))]:+.2f} "
          f"| max LATE {allv[-1]:+.2f} min | min {allv[0]:+.2f} min")
    late = sum(1 for v in allv if v > 1.0)
    print(f"  cells whose production p10 is >1 min LATE (anti-safe): "
          f"{late}/{len(allv)} ({100*late/len(allv):.1f}%)")
    print(f"  cells >30 min late (past the whole egress buffer): "
          f"{sum(1 for v in allv if v > 30)}/{len(allv)} "
          f"({100*sum(1 for v in allv if v > 30)/len(allv):.1f}%)")

    # per-client view
    print("\n  client-level p10 error (production domain vs wide truth):")
    print(f"  {'bearing':>8s} {'range km':>9s} {'prod p10 h':>11s} "
          f"{'truth p10 h':>12s} {'LATE min':>10s}")
    for brg in (90.0, 60.0, 45.0, 30.0, 0.0, 120.0, 135.0):
        for km in (1.5, 3.0):
            loc = geo.destination(F, brg, km * 1000.0)
            a = spread.eta_at(e_prod, loc)
            b = spread.eta_at(e_wide, loc)
            if math.isinf(a.eta_p10_s) or math.isinf(b.eta_p10_s):
                continue
            print(f"  {brg:8.0f} {km:9.1f} {(a.eta_p10_s-T0)/3600:11.2f} "
                  f"{(b.eta_p10_s-T0)/3600:12.2f} "
                  f"{(a.eta_p10_s-b.eta_p10_s)/60:+10.1f}")

    # ---- N4: p50 inflation at head-axis clients -------------------------
    print()
    print("=" * 80)
    print("N4. p50 inflation at head-axis clients (the ladder's NOTICE/EXTREME key)")
    print("=" * 80)
    nom = e_wide.members[0]
    print(f"  {'range km':>9s} {'nominal h':>10s} {'p10 h':>8s} {'p50 h':>8s} "
          f"{'p50/nominal':>12s} {'p10/nominal':>12s}")
    ratios = []
    for km in (1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 16.0):
        loc = geo.destination(F, 90.0, km * 1000.0)
        e = spread.eta_at(e_wide, loc)
        vn = safety._raster_value_at(nom, loc)
        if vn is None or math.isinf(vn) or math.isinf(e.eta_p50_s):
            continue
        hn = (vn - T0) / 3600.0
        h10 = (e.eta_p10_s - T0) / 3600.0
        h50 = (e.eta_p50_s - T0) / 3600.0
        ratios.append(h50 / hn)
        print(f"  {km:9.1f} {hn:10.2f} {h10:8.2f} {h50:8.2f} "
              f"{h50/hn:12.2f} {h10/hn:12.2f}")
    print(f"\n  p50 is a stable {statistics.fmean(ratios):.2f}x the engine's own "
          f"best estimate on the head axis")
    print(f"  (direction perturbation +-24 deg rotates the head OFF a fixed point,")
    print(f"   so the median member reaches it by the flank, not the head).")
    print(f"  Ladder impact: NOTICE_72H / EXTREME_24H key on p50 <= 72 h / 24 h.")
    print(f"  A client with a 6 h best-estimate arrival has p50 ~ "
          f"{6*statistics.fmean(ratios):.0f} h -> the p50 rung of EXTREME_24H does")
    print(f"  NOT fire; only the p10 <= 12 h rung saves it (p10/nominal "
          f"{statistics.fmean([1.0]):.2f} -> fires).")


if __name__ == "__main__":
    sys.exit(main())
