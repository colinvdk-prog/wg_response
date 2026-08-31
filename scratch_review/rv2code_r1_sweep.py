"""RV2-CODE R1 (blast radius, quantified): over a sweep of realistic
(client, safe-zone, departure-time) geometries, how often does the PRODUCTION
domain's corridor verdict say SAFE where the same physics on a domain wide
enough to cover the leg says UNSAFE?

PRODUCTION domain = threat.py's own construction:
    geo.bbox_around(ignition_geometry + near_clients, _DOMAIN_PAD_M=2 km)
TRUTH domain      = the same ignition / weather / fuel / cell size, on a bbox
                    padded far enough to cover the whole leg.

Only the DOMAIN differs. Any verdict disagreement is caused purely by
samples falling outside the production raster and being skipped
(safety.py ~:353) rather than by different physics.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import T0, cfg_default

from wgresponse import geo, safety, spread, threat
from wgresponse.contracts import (BBox, GridSpec, LatLon, Raster, TerrainTile,
                                  WeatherForecast, WeatherSample)

F = LatLon(34.2000, -117.6000)


def tile_for(bbox: BBox, cell_m: float, code=102.0):
    ctr = bbox.center()
    w_m = (bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat)
    h_m = (bbox.north - bbox.south) * geo.M_PER_DEG_LAT
    nr, nc = max(2, int(h_m / cell_m)), max(2, int(w_m / cell_m))
    spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west, cell_m=cell_m,
                    nrows=nr, ncols=nc)
    n = nr * nc
    return TerrainTile(spec=spec, fuel=Raster(spec, [code] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="rv2")


def wxf(ws, wdir, n=120):
    return WeatherForecast(loc=F, samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=ws,
                      wind_dir_deg=wdir, rh_pct=10.0, temp_c=35.0)
        for i in range(n)], source="rv2")


def main():
    cfg = cfg_default()
    spd = safety._corridor_speed_mps(cfg)
    wind_from = 270.0            # fire runs EAST
    results = []
    print("=" * 92)
    print("R1 BLAST RADIUS SWEEP — production-domain vs wide-domain corridor verdicts")
    print("=" * 92)
    print(f"corridor speed {spd:.2f} m/s | route margin {cfg.route_margin_min:.0f} min | "
          f"erosion {cfg.corridor_erosion_m:.0f} m | wind FROM {wind_from:.0f} deg")

    for ws in (10.0, 14.0, 20.0):
        wx = wxf(ws, wind_from)
        for c_brg, c_km in ((0.0, 1.5), (0.0, 3.0), (45.0, 2.0), (180.0, 1.5),
                            (315.0, 2.0)):
            C = geo.destination(F, c_brg, c_km * 1000.0)
            prod_dom = geo.bbox_around([F, C], threat._DOMAIN_PAD_M)
            prod_tile = tile_for(prod_dom, cfg.grid_cell_m)
            ens_prod = spread.ensemble_arrival(prod_tile, [F], wx, T0, cfg,
                                               incident_id="I-A")
            er_prod = safety._erode(ens_prod.p10, cfg.corridor_erosion_m)
            for z_brg, z_km in ((90.0, 10.0), (90.0, 6.0), (135.0, 8.0),
                                (45.0, 8.0), (180.0, 7.0), (270.0, 5.0)):
                Z = geo.destination(F, z_brg, z_km * 1000.0)
                wide_dom = geo.bbox_around([F, C, Z], 4000.0)
                wide_tile = tile_for(wide_dom, cfg.grid_cell_m)
                ens_wide = spread.ensemble_arrival(wide_tile, [F], wx, T0, cfg,
                                                   incident_id="I-A")
                er_wide = safety._erode(ens_wide.p10, cfg.corridor_erosion_m)
                op = safety.LegOracle(C, Z, spd, er_prod, cfg)
                ow = safety.LegOracle(C, Z, spd, er_wide, cfg)
                off = op.n_pts - len(op.samples)
                for dep_h in (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0):
                    t = T0 + dep_h * 3600.0
                    sp = op.verdict(t).safe
                    sw = ow.verdict(t).safe
                    results.append(dict(ws=ws, c=(c_brg, c_km), z=(z_brg, z_km),
                                        dep=dep_h, prod=sp, truth=sw,
                                        off=off, n=op.n_pts,
                                        tp=op.latest_safe_depart_s,
                                        tw=ow.latest_safe_depart_s))

    n = len(results)
    flips = [r for r in results if r["prod"] and not r["truth"]]
    agree = sum(1 for r in results if r["prod"] == r["truth"])
    conservative = [r for r in results if (not r["prod"]) and r["truth"]]
    print(f"\ncases: {n}   agree: {agree} ({100*agree/n:.1f}%)")
    print(f"FALSE-CLEAR (prod SAFE, truth UNSAFE): {len(flips)} "
          f"({100*len(flips)/n:.1f}%)   <-- crew driven into fire")
    print(f"false-block (prod UNSAFE, truth SAFE): {len(conservative)} "
          f"({100*len(conservative)/n:.1f}%)")

    if flips:
        print(f"\n{'ws':>4s} {'client':>12s} {'zone':>12s} {'dep h':>6s} "
              f"{'off-raster':>12s} {'prod T*':>10s} {'truth T*':>10s} {'lost margin h':>14s}")
        for r in flips[:30]:
            tp = "+inf" if math.isinf(r["tp"]) else f"{(r['tp']-T0)/3600:+.2f}"
            tw = "+inf" if math.isinf(r["tw"]) else f"{(r['tw']-T0)/3600:+.2f}"
            lost = (r["tp"] - r["tw"]) / 3600.0 if math.isfinite(r["tw"]) else math.inf
            print(f"{r['ws']:4.0f} {f'{r['c'][0]:.0f}deg/{r['c'][1]:.1f}km':>12s} "
                  f"{f'{r['z'][0]:.0f}deg/{r['z'][1]:.0f}km':>12s} {r['dep']:6.1f} "
                  f"{f'{r['off']}/{r['n']}':>12s} {tp:>10s} {tw:>10s} "
                  f"{lost if math.isfinite(lost) else float('inf'):14.2f}")
        # worst case: how long AFTER the truth deadline does production still clear?
        worst = max((r for r in flips if math.isfinite(r["tw"])),
                    key=lambda r: (math.inf if math.isinf(r["tp"]) else r["tp"]) - r["tw"],
                    default=None)
        if worst:
            lost = ((math.inf if math.isinf(worst["tp"]) else worst["tp"])
                    - worst["tw"]) / 3600.0
            print(f"\nWORST: production permits departure {lost:.2f} h AFTER the "
                  f"honest latest-safe-departure")

    # unbounded-T* cases: legs with ZERO in-raster samples
    unb = [r for r in results if math.isinf(r["tp"])]
    print(f"\nlegs with T* = +inf under the production domain (ZERO fire "
          f"information): {len({(r['ws'],r['c'],r['z']) for r in unb})} distinct legs")
    zero = [r for r in results if r["off"] == r["n"]]
    print(f"legs with 100% of samples off-raster: "
          f"{len({(r['ws'],r['c'],r['z']) for r in zero})} distinct legs")
    offs = sorted({(r['c'], r['z'], r['off'], r['n']) for r in results},
                  key=lambda x: -x[2] / x[3])
    print(f"\nworst off-raster coverage (production domain):")
    for c, z, off, npts in offs[:8]:
        print(f"  client {c[0]:5.0f}deg/{c[1]:.1f}km -> zone {z[0]:5.0f}deg/{z[1]:.0f}km : "
              f"{off}/{npts} samples skipped ({100*off/npts:.0f}%)")


if __name__ == "__main__":
    sys.exit(main())
