"""RV2-CODE R1 (decisive): END-TO-END proof that the off-raster fail-open is
REACHABLE in the live pipeline, i.e. dispatch approves an egress leg that
drives a crew into ground the fire actually reaches.

Construction (all live code paths, no stubs of the safety math):
  * fire F, one protected client C 2 km EAST of F  ->  threat.py's domain is
    bbox(F, C) + _DOMAIN_PAD_M = ~6 x 4 km.
  * wind from the north: the fire runs SOUTH, fast.
  * the nearest safe zone Z sits 7 km SOUTH of F (the sim's own Z-SOUTH
    geometry, wgresponse/sim.py:374) -- i.e. DOWNWIND, outside the domain.
  * TRUTH raster: the same ignition/weather/fuel on a WIDE domain, so we know
    when the fire actually reaches the egress corridor.

Then: run the REAL safety.LegOracle / dispatch corridor check on the NARROW
(production) raster and on the WIDE (truth) raster and compare verdicts.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import T0, cfg_default

from da_common import mk_crew, mk_job

from wgresponse import dispatch, geo, safety, spread, threat
from wgresponse.contracts import (BBox, GridSpec, LatLon, Raster, SafeZone,
                                  TerrainTile, UNREACHED, WeatherForecast,
                                  WeatherSample)

F = LatLon(34.2000, -117.6000)
C = geo.destination(F, 90.0, 2000.0)              # client 2 km E
Z = geo.destination(F, 180.0, 7000.0)             # safe zone 7 km S (sim geometry)
BASE = geo.destination(F, 90.0, 3000.0)           # crew staging 3 km E


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


def wx(t0, ws=14.0, wdir=0.0, n=120):
    """wdir=0 -> wind FROM the north -> fire runs SOUTH."""
    return WeatherForecast(loc=F, samples=[
        WeatherSample(t_s=t0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=ws,
                      wind_dir_deg=wdir, rh_pct=10.0, temp_c=35.0)
        for i in range(n)], source="rv2")


def main():
    cfg = cfg_default()
    cfg.solver_budget_s = 3.0

    # ---- 1. the PRODUCTION domain, exactly as threat.py builds it ----------
    prod_domain = geo.bbox_around([F, C], threat._DOMAIN_PAD_M)
    w_km = (prod_domain.east - prod_domain.west) * geo.m_per_deg_lon(prod_domain.center().lat) / 1000
    h_km = (prod_domain.north - prod_domain.south) * geo.M_PER_DEG_LAT / 1000
    print("=" * 78)
    print("E. END-TO-END: does the fail-open actually let a crew drive into fire?")
    print("=" * 78)
    print(f"  fire F        {F.lat:.4f},{F.lon:.4f}")
    print(f"  client C      2.0 km E of F")
    print(f"  safe zone Z   7.0 km S of F  (wgresponse/sim.py:374 geometry)")
    print(f"  wind          14 m/s FROM the north -> fire runs SOUTH, over the "
          f"C->Z egress leg")
    print(f"\n  PRODUCTION domain = bbox(F,C) + {threat._DOMAIN_PAD_M/1000:.0f} km "
          f"= {w_km:.1f} x {h_km:.1f} km")
    print(f"    contains Z? {prod_domain.contains(Z)}")

    prod_tile = tile_for(prod_domain, 90.0)
    wide_domain = geo.bbox_around([F, C, Z, BASE], 6000.0)
    ww = (wide_domain.east - wide_domain.west) * geo.m_per_deg_lon(wide_domain.center().lat) / 1000
    wh = (wide_domain.north - wide_domain.south) * geo.M_PER_DEG_LAT / 1000
    print(f"  TRUTH domain (same physics, wide enough to cover the leg) = "
          f"{ww:.1f} x {wh:.1f} km")
    wide_tile = tile_for(wide_domain, 90.0)

    w = wx(T0)
    print("\n  running ensembles (identical ignition/weather/fuel, only the "
          "domain differs) ...")
    ens_prod = spread.ensemble_arrival(prod_tile, [F], w, T0, cfg, incident_id="I-PROD")
    ens_wide = spread.ensemble_arrival(wide_tile, [F], w, T0, cfg, incident_id="I-PROD")

    er_prod = safety._erode(ens_prod.p10, cfg.corridor_erosion_m)
    er_wide = safety._erode(ens_wide.p10, cfg.corridor_erosion_m)

    # ---- 2. what does the TRUTH raster say about the egress corridor? ------
    print("\n  TRUTH: eroded-P10 arrival along the C->Z egress leg")
    pts = safety._sample_polyline(C, Z, cfg.corridor_sample_m)
    print(f"    {'i':>3s} {'km along':>9s} {'PROD raster':>14s} {'TRUTH raster':>14s}")
    for i, p in enumerate(pts):
        if i % 4 and i != len(pts) - 1:
            continue
        vp = safety._raster_value_at(er_prod, p)
        vw = safety._raster_value_at(er_wide, p)
        f_ = "OFF-RASTER" if vp is None else ("UNREACHED" if math.isinf(vp)
                                              else f"{(vp-T0)/3600:.2f} h")
        t_ = "OFF-RASTER" if vw is None else ("UNREACHED" if math.isinf(vw)
                                              else f"{(vw-T0)/3600:.2f} h")
        d = geo.haversine_m(C, p) / 1000.0
        print(f"    {i:3d} {d:9.2f} {f_:>14s} {t_:>14s}")

    # ---- 3. the REAL corridor verdicts -------------------------------------
    spd = safety._corridor_speed_mps(cfg)
    print(f"\n  corridor speed {spd:.2f} m/s, route margin "
          f"{cfg.route_margin_min:.0f} min, erosion {cfg.corridor_erosion_m:.0f} m")
    for label, ras in (("PRODUCTION raster", er_prod), ("TRUTH raster", er_wide)):
        o = safety.LegOracle(C, Z, spd, ras, cfg)
        v = o.verdict(T0)
        tstar = ("+inf (UNBOUNDED)" if math.isinf(o.latest_safe_depart_s)
                 else f"T0{(o.latest_safe_depart_s - T0)/3600:+.2f} h")
        print(f"    {label:18s}: samples {len(o.samples):3d}/{o.n_pts:3d}  "
              f"SAFE={str(v.safe):5s}  T*={tstar}")
        if not v.safe:
            print(f"      reason: {v.reason[:100]}")

    # ---- 4. does the full dispatcher approve the stop? ---------------------
    print("\n  FULL dispatch.plan() with the PRODUCTION raster vs the TRUTH raster:")
    crews = [mk_crew("CREW-1", BASE)]
    eta_c = spread.eta_at(ens_wide, C)
    jobs = [mk_job("C1", C, 2_000_000.0, 1.0, "I-PROD",
                   eta_c.eta_p10_s, eta_c.eta_p50_s)]
    zones = [SafeZone("Z-SOUTH", Z, "South flank lot")]
    print(f"    client C threat ETA: p10 {(eta_c.eta_p10_s-T0)/3600:.2f} h  "
          f"p50 {(eta_c.eta_p50_s-T0)/3600:.2f} h")
    for label, ens in (("PRODUCTION", ens_prod), ("TRUTH", ens_wide)):
        plan = dispatch.plan(crews, jobs, {"I-PROD": ens}, zones, cfg, T0)
        served = [s for r in plan.routes for s in r.stops]
        print(f"    {label:10s}: stops served = {len(served)}  "
              f"unserved = {[(u.client_id, u.reason) for u in plan.unserved]}")
        for r in plan.routes:
            for s in r.stops:
                print(f"        {s.client_id}: arrive "
                      f"{(s.arrive_s-T0)/3600:+.2f} h  depart "
                      f"{(s.depart_s-T0)/3600:+.2f} h  egress zone "
                      f"{getattr(s,'egress_zone_id','?')}")

    print("\n  If PRODUCTION serves the stop and TRUTH does not, the domain")
    print("  truncation + off-raster fail-open put a crew on a road the fire")
    print("  crosses, with no warning anywhere in the verdict.")


if __name__ == "__main__":
    sys.exit(main())
