"""RV2-CODE NEW FINDINGS (verifier sweep of the same modules).

N1. DOMAIN-EDGE CONTAMINATION OF P10. The arrival raster is Dijkstra on the
    tile only: the tile boundary is ABSORBING. Cells near the edge get no
    contribution from paths that would leave and re-enter, and — more
    importantly — the whole neighbourhood inside the erosion radius of the
    edge is min-filtered against off-grid nothing. threat.py puts that edge
    only _DOMAIN_PAD_M = 2 km beyond the client hull, so REAL CLIENTS sit
    inside the contaminated band. Measure the p10 error at cells present in
    both a production-sized and a wide domain.

N2. REALIZED PESSIMISM MARGIN. p10 = min(Hazen p10 over 6 members, nominal),
    with the R6 band (wind +-10%, dir +-24%). How much earlier than the
    nominal run is p10 actually, at client ranges? That number IS the crew
    safety margin the whole SEM-10 law is built on.

N3. validate_config is never called on the live watcher / CLI path.
"""
from __future__ import annotations

import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import T0, cfg_default

from wgresponse import geo, safety, spread, threat
from wgresponse.contracts import (BBox, GridSpec, LatLon, Raster, TerrainTile,
                                  WeatherForecast, WeatherSample)

F = LatLon(34.2000, -117.6000)


def tile_for(bbox, cell_m, code=102.0):
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


def wxf(ws=14.0, wdir=270.0, n=120):
    return WeatherForecast(loc=F, samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=ws,
                      wind_dir_deg=wdir, rh_pct=10.0, temp_c=35.0)
        for i in range(n)], source="rv2")


def n1_edge_contamination():
    print("=" * 80)
    print("N1. DOMAIN-EDGE CONTAMINATION OF P10 (clients live inside the band)")
    print("=" * 80)
    cfg = cfg_default()
    wx = wxf()
    # production domain: fire + a client 3 km east, +2 km pad
    C = geo.destination(F, 90.0, 3000.0)
    prod = geo.bbox_around([F, C], threat._DOMAIN_PAD_M)
    wide = geo.bbox_around([F, C], 25_000.0)
    pw = (prod.east - prod.west) * geo.m_per_deg_lon(prod.center().lat) / 1000
    ph = (prod.north - prod.south) * geo.M_PER_DEG_LAT / 1000
    print(f"  production domain {pw:.1f} x {ph:.1f} km   (pad "
          f"{threat._DOMAIN_PAD_M/1000:.0f} km)")
    print(f"  wide domain       50.0 x 50.0 km  (same ignition/weather/fuel/cell)")
    tp, tw = tile_for(prod, cfg.grid_cell_m), tile_for(wide, cfg.grid_cell_m)
    ep = spread.ensemble_arrival(tp, [F], wx, T0, cfg, incident_id="I-A")
    ew = spread.ensemble_arrival(tw, [F], wx, T0, cfg, incident_id="I-A")

    # compare p10 at a grid of probe points inside the production domain,
    # bucketed by distance to the nearest production-domain EDGE
    print(f"\n  p10 error vs the wide-domain truth, by distance from the "
          f"production domain edge:")
    print(f"  {'edge dist':>12s} {'n':>5s} {'mean LATE (min)':>17s} "
          f"{'max LATE (min)':>16s} {'worst cell':>26s}")
    buckets = {}
    sp = tp.spec
    for r in range(0, sp.nrows):
        for c in range(0, sp.ncols):
            p = geo.rc_to_latlon(sp, r, c)
            vp = safety._raster_value_at(ep.p10, p)
            vw = safety._raster_value_at(ew.p10, p)
            if vp is None or vw is None or math.isinf(vp) or math.isinf(vw):
                continue
            # distance to the nearest production edge, in metres
            d_n = (prod.north - p.lat) * geo.M_PER_DEG_LAT
            d_s = (p.lat - prod.south) * geo.M_PER_DEG_LAT
            mlon = geo.m_per_deg_lon(p.lat)
            d_e = (prod.east - p.lon) * mlon
            d_w = (p.lon - prod.west) * mlon
            d = min(d_n, d_s, d_e, d_w)
            key = ("<250 m" if d < 250 else "250-500 m" if d < 500 else
                   "500-1000 m" if d < 1000 else "1-2 km" if d < 2000 else ">2 km")
            buckets.setdefault(key, []).append(((vp - vw) / 60.0, r, c))
    for key in ("<250 m", "250-500 m", "500-1000 m", "1-2 km", ">2 km"):
        b = buckets.get(key)
        if not b:
            continue
        vals = [x[0] for x in b]
        worst = max(b, key=lambda x: x[0])
        print(f"  {key:>12s} {len(b):5d} {statistics.fmean(vals):17.2f} "
              f"{max(vals):16.2f} {f'r{worst[1]},c{worst[2]}':>26s}")
    allv = [x[0] for bs in buckets.values() for x in bs]
    print(f"\n  overall: {len(allv)} cells, mean {statistics.fmean(allv):+.2f} min, "
          f"max LATE {max(allv):+.2f} min, min {min(allv):+.2f} min")
    print("  (+ve = the production raster's p10 is LATER than truth = ANTI-SAFE:")
    print("   later p10 -> later latest_depart -> crews leave later than the law allows)")

    # what does that do to a client sitting near the edge?
    print("\n  a client at the production-domain edge:")
    for brg, km in ((90.0, 4.8), (0.0, 1.8), (45.0, 2.5)):
        loc = geo.destination(F, brg, km * 1000.0)
        if not prod.contains(loc):
            continue
        a = spread.eta_at(ep, loc)
        b = spread.eta_at(ew, loc)
        if math.isinf(a.eta_p10_s) or math.isinf(b.eta_p10_s):
            continue
        print(f"    {brg:5.0f}deg / {km:.1f} km : prod p10 "
              f"{(a.eta_p10_s-T0)/3600:6.2f} h  truth p10 "
              f"{(b.eta_p10_s-T0)/3600:6.2f} h  LATE by "
              f"{(a.eta_p10_s-b.eta_p10_s)/60:+7.1f} min")


def n2_pessimism_margin():
    print()
    print("=" * 80)
    print("N2. REALIZED PESSIMISM MARGIN (p10 vs nominal) — the crew-safety cushion")
    print("=" * 80)
    cfg = cfg_default()
    wx = wxf()
    dom = geo.bbox_around([F], 20_000.0)
    tile = tile_for(dom, cfg.grid_cell_m)
    ens = spread.ensemble_arrival(tile, [F], wx, T0, cfg, incident_id="I-A")
    nom = ens.members[0]
    print(f"  ensemble_n = {cfg.ensemble_n} (6 members), realized wind band "
          f"+-10%, dir +-24 deg (see rv2code_r6_ensemble.py)")
    print(f"\n  {'range':>8s} {'bearing':>8s} {'nominal h':>10s} {'p10 h':>8s} "
          f"{'p50 h':>8s} {'margin min':>11s} {'margin % of lead':>17s}")
    clamped = total = 0
    margins = []
    for km in (1.0, 3.0, 5.0, 8.0, 12.0):
        for brg in (90.0, 45.0, 135.0, 0.0):
            loc = geo.destination(F, brg, km * 1000.0)
            e = spread.eta_at(ens, loc)
            v_nom = safety._raster_value_at(nom, loc)
            if v_nom is None or math.isinf(v_nom) or math.isinf(e.eta_p10_s):
                continue
            total += 1
            lead_h = (e.eta_p50_s - T0) / 3600.0
            marg = (v_nom - e.eta_p10_s) / 60.0
            margins.append(marg)
            if abs(e.eta_p10_s - v_nom) < 1e-6:
                clamped += 1
            if brg in (90.0, 45.0):
                print(f"  {km:6.1f}km {brg:8.0f} {(v_nom-T0)/3600:10.2f} "
                      f"{(e.eta_p10_s-T0)/3600:8.2f} {(e.eta_p50_s-T0)/3600:8.2f} "
                      f"{marg:11.1f} "
                      f"{100*marg/60/max(lead_h,1e-9):17.1f}")
    print(f"\n  p10 == nominal (band gave NO pessimism at all): "
          f"{clamped}/{total} probe points")
    if margins:
        print(f"  pessimism margin over all probes: mean "
              f"{statistics.fmean(margins):.1f} min, median "
              f"{statistics.median(margins):.1f} min, max {max(margins):.1f} min")
    print(f"\n  Compare: cfg.egress_buffer_min = {cfg.egress_buffer_min:.0f} min, "
          f"route_margin_min = {cfg.route_margin_min:.0f} min, deploy_min = "
          f"{cfg.deploy_min:.0f} min")
    print("  -> the ensemble's own pessimism is SMALLER than the fixed buffers it")
    print("     is supposed to justify; the band is not doing the work the")
    print("     '+-25% / +-30deg' description implies.")


def n3_validate_never_called():
    print()
    print("=" * 80)
    print("N3. validate_config is NEVER called on the live watcher / CLI path")
    print("=" * 80)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import re
    hits = []
    for fn in sorted(os.listdir(os.path.join(root, "wgresponse"))):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(root, "wgresponse", fn), encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            if "validate_config" in line and not line.strip().startswith("#"):
                hits.append((fn, i, line.strip()))
    for fn, i, line in hits:
        print(f"  {fn}:{i}  {line[:80]}")
    print("\n  cli.py (the `watch` entrypoint the live service runs) does NOT")
    print("  call it, and --gust-blend / --grass-floor are plain `type=float`")
    print("  with no range check, so an operator typo silently ships an")
    print("  out-of-range safety config.")


if __name__ == "__main__":
    n1_edge_contamination()
    n2_pessimism_margin()
    n3_validate_never_called()
