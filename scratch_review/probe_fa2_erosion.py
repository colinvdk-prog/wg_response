"""probe_fa2_erosion — WHERE does the eroded P10 value at the blocking samples
come from?  For each binding corridor sample, find the actual raster cell that
supplied the morphological minimum: its (row, col), raw P10 value, and its
metric distance from the road. Also characterises the P10 field itself
(ignition point, finite-cell count, the 0.24 h blob) and re-measures the
erosion window's Chebyshev-vs-disk over-reach.

Read-only; writes scratch_review/probe_fa2_erosion.json.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, geo, safety, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402
from scratch_review.probe_founder_arith import (  # noqa: E402
    DIST, MAP, SEED, ignite, pick_homes, variant_cfg,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "probe_fa2_erosion.json")

CAPTURED = {}


def _capture():
    orig = dispatch.plan

    def wrapper(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                forecasts=None, road_provider=None):
        CAPTURED.setdefault("args", dict(
            crews=crews, jobs=jobs, ens=ens, safe_zones=safe_zones, cfg=cfg,
            now_s=now_s, road_provider=road_provider))
        return orig(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                    forecasts=forecasts, road_provider=road_provider)
    dispatch.plan = wrapper
    return orig


def erosion_source(raster, p, radius_m):
    """The cell that supplies safety._erode_min_local's value at p:
    (r, c, value, dist_m, dr, dc) over the k-cell Chebyshev window."""
    spec = raster.spec
    k = int(math.ceil(radius_m / spec.cell_m)) if radius_m > 0 else 0
    rc = safety._latlon_to_rc(spec, p)
    if rc is None:
        return None
    r0, c0 = rc
    best = None
    for r in range(max(0, r0 - k), min(spec.nrows, r0 + k + 1)):
        for c in range(max(0, c0 - k), min(spec.ncols, c0 + k + 1)):
            v = raster.get(r, c)
            if best is None or v < best[2]:
                q = safety._rc_center(spec, r, c)
                best = (r, c, v, geo.haversine_m(p, q), r - r0, c - c0)
    return best


def main():
    orig = _capture()
    try:
        cfg_d = variant_cfg("certain", 15.0)
        homes, truth_h, span = pick_homes(cfg_d)
        s = simui.SimSession(MAP, 4, 1, seed=SEED, cfg_overrides=dict(cfg_d),
                             client_locs=homes)
        t0 = s._now_s
        ignite(s)
        s.tick()
    finally:
        dispatch.plan = orig

    a = CAPTURED["args"]
    cfg, now = a["cfg"], a["now_s"]
    iid = sorted(a["ens"])[0]
    raw = a["ens"][iid].p10
    spec = raw.spec
    fire = next(iter(s._fires.values()))
    truth = (fire.ens.members[0] if getattr(fire.ens, "members", None)
             else fire.ens.p50)

    rep = {}
    loc = LOCATIONS[MAP]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, DIST)
    origin = getattr(fire, "origin", None) or getattr(fire, "loc", None)
    rep["fire"] = dict(
        map_center=[loc.center.lat, loc.center.lon],
        hazard_wind_dir_deg=loc.hazard_wind_dir_deg,
        requested_ignition=[round(ign.lat, 5), round(ign.lon, 5)],
        fire_origin=([round(origin.lat, 5), round(origin.lon, 5)]
                     if origin is not None else None),
        km_ignition_to_home=[round(geo.haversine_m(ign, h) / 1000.0, 2)
                             for h in homes],
        t0=t0, now=now,
    )

    # ---- P10 field characterisation ---------------------------------------
    vals = [(v, i) for i, v in enumerate(raw.data) if math.isfinite(v)]
    vals.sort()
    def cell(i):
        r, c = divmod(i, spec.ncols)
        p = safety._rc_center(spec, r, c)
        return dict(r=r, c=c, lat=round(p.lat, 5), lon=round(p.lon, 5))
    rep["p10_field"] = dict(
        cells_total=spec.nrows * spec.ncols, cell_m=spec.cell_m,
        nrows=spec.nrows, ncols=spec.ncols,
        finite_cells=len(vals),
        min_h=round((vals[0][0] - t0) / 3600.0, 4),
        min_cell=cell(vals[0][1]),
        pct_of_finite_under_0_5h=round(
            100.0 * sum(1 for v, _ in vals if (v - t0) / 3600.0 < 0.5)
            / max(1, len(vals)), 2),
        n_under_0_5h=sum(1 for v, _ in vals if (v - t0) / 3600.0 < 0.5),
        p10_equals_truth=(list(raw.data) == list(truth.data)),
    )
    # the 0.24 h population: where is it?
    blob = [cell(i) | {"h": round((v - t0) / 3600.0, 3)}
            for v, i in vals if (v - t0) / 3600.0 < 0.30]
    rep["p10_field"]["blob_under_0_30h_n"] = len(blob)
    rep["p10_field"]["blob_sample"] = blob[:12]
    if blob:
        rep["p10_field"]["blob_km_to_home4"] = round(min(
            geo.haversine_m(geo.LatLon(b["lat"], b["lon"]), homes[3])
            for b in blob) / 1000.0, 3)
        rep["p10_field"]["blob_km_to_ignition"] = round(min(
            geo.haversine_m(geo.LatLon(b["lat"], b["lon"]), ign)
            for b in blob) / 1000.0, 3)

    # ---- erosion sources at the binding samples ---------------------------
    pts = [("mid_route_shared", geo.LatLon(39.95978, -105.23494)),
           ("mid_route_shared2", geo.LatLon(39.96044, -105.23571)),
           ("at_home4_spur", geo.LatLon(39.95176, -105.23503)),
           ("home4_itself", homes[3]),
           ("home1_itself", homes[0]),
           ("home2_itself", homes[1]),
           ("home3_itself", homes[2])]
    src = {}
    for name, p in pts:
        rows = {}
        for rad in (500.0, 250.0, 90.0, 0.0):
            e = safety._erode(raw, rad) if rad > 0 else raw
            v = safety._raster_value_at(e, p)
            s_ = erosion_source(raw, p, rad)
            rows[str(rad)] = dict(
                eroded_h=(None if v is None or math.isinf(v)
                          else round((v - t0) / 3600.0, 3)),
                src_cell=(None if s_ is None else
                          dict(r=s_[0], c=s_[1],
                               h=(None if math.isinf(s_[2])
                                  else round((s_[2] - t0) / 3600.0, 3)),
                               dist_m=round(s_[3]), dr=s_[4], dc=s_[5])))
        own = safety._raster_value_at(raw, p)
        rows["own_cell_h"] = (None if own is None or math.isinf(own)
                              else round((own - t0) / 3600.0, 3))
        src[name] = rows
    rep["erosion_sources"] = src

    # ---- erosion geometry -------------------------------------------------
    k = int(math.ceil(cfg.corridor_erosion_m / spec.cell_m))
    rep["erosion_geometry"] = dict(
        radius_m=cfg.corridor_erosion_m, cell_m=spec.cell_m, k_cells=k,
        chebyshev_half_width_m=k * spec.cell_m,
        chebyshev_corner_reach_m=round(k * spec.cell_m * math.sqrt(2.0), 1),
        over_reach_vs_radius_m=round(k * spec.cell_m * math.sqrt(2.0)
                                     - cfg.corridor_erosion_m, 1),
        window_cells=(2 * k + 1) ** 2,
        note=("safety._erode_min_local uses a SQUARE Chebyshev window of "
              "ceil(radius/cell) cells: a 500 m radius reaches 764 m into the "
              "corners, 1.53x the stated radius, over 169 cells"))

    json.dump(rep, open(OUT, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
