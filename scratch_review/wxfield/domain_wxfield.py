"""Local weather on a REAL domain with a HYPOTHETICAL point fire (SAFETY
SEMANTICS 17, 2026-09-27). NOT product code — measurements A and B of
docs/LIVE_WORLD_PLAN.md "Spread reads the local weather forecast
(2026-09-27)" on a domain whose live fire does not spread today.

The recorded national snapshot's only spreading domain with clients is
Willow (national_wxfield.py); every SoCal domain's fire sits on non-burnable
fuel. This script takes a REAL cached LANDFIRE domain tile, lights a point
fire on the burnable cell nearest to --ignition (a feed-like point incident:
seeds at t0, no perimeter), and spreads it with the production flags twice
through spread.ensemble_arrival — the fire-centroid forecast alone (legacy)
and the local-weather field (wxfield.build_field over REAL NWS forecasts, the
same disk cache as national_wxfield.py; fetched live on first use). Client-
like points: the mock-presets demo homes inside the tile plus a ring of
synthetic points (8 bearings x RING_KM) around the fire.

    py -3 scratch_review/wxfield/domain_wxfield.py --tile PATH
        --ignition LAT,LON --label NAME [--grid-km 10] [--points-max 16]

Results: scratch_review/wxfield/domain_<label>[_g<G>_m<M>].json.
"""
import argparse
import dataclasses
import json
import math
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "scratch_review", "contained"))
sys.path.insert(0, HERE)

import feedsnap  # noqa: E402
import wxfig  # noqa: E402
from ab_national import NEW, PROD  # noqa: E402
from national_wxfield import (NWS_CACHE, Clock, DiskNws,  # noqa: E402
                              measurement_a, measurement_b)
from wgresponse import cli, fuels, geo, spread, threat, weather, wxfield  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import load_ascii_tile  # noqa: E402

RING_KM = (2.0, 5.0, 10.0, 15.0, 20.0)
RING_BEARINGS = tuple(range(0, 360, 45))


def nearest_burnable(tile, p):
    spec = tile.spec
    r0, c0 = geo.latlon_to_rc(spec, p)
    for rad in range(0, 200):
        for r in range(r0 - rad, r0 + rad + 1):
            for c in range(c0 - rad, c0 + rad + 1):
                if max(abs(r - r0), abs(c - c0)) != rad:
                    continue
                if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
                    k = int(tile.fuel.get(r, c))
                    if fuels.is_shrub(k) or fuels.is_grass_shrub(k) or \
                            fuels.is_grass(k):
                        return geo.rc_to_latlon(spec, r, c), k
    raise SystemExit("no burnable cell near the ignition")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile", required=True)
    ap.add_argument("--ignition", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--grid-km", type=float, default=10.0)
    ap.add_argument("--points-max", type=int, default=16)
    args = ap.parse_args()
    now = float(feedsnap.ReplayHttp().manifest["recorded_at_s"])
    tile = load_ascii_tile(args.tile)
    spec = tile.spec
    lat, lon = (float(x) for x in args.ignition.split(","))
    ign, code = nearest_burnable(tile, LatLon(lat, lon))
    print("ignition", ign, "FBFM40", code, flush=True)
    base = dataclasses.replace(ResponseConfig(), **PROD, **NEW)
    cfg = dataclasses.replace(base, weather_grid_km=args.grid_km,
                              weather_points_max=args.points_max)
    http = DiskNws(NWS_CACHE)
    wx = weather.NwsWeather(http_get=http, cfg=cfg,
                            cache=weather.NwsForecastCache(clock=Clock()))
    wxf = wx.forecast(ign, cfg.horizon_h)
    t = time.monotonic()
    field = wxfield.build_field(wx, wxf, ign, spec, cfg, cfg.horizon_h)
    fetch_s = time.monotonic() - t
    print("points", len(field.points), "spacing", field.spacing_km, "failed",
          sum(1 for p in field.points if not p.ok), "fetch", round(fetch_s, 1),
          "s; NWS", http.counts(), flush=True)
    spread_cfg, flag = threat.regime_spread_cfg(tile, wxf, now, base)
    label = args.label
    runs = {}
    for name, kw in (("off", {}), ("on", {"weather_field": field})):
        t = time.monotonic()
        ens = spread.ensemble_arrival(tile, [ign], wxf, now, spread_cfg,
                                      incident_id=label, **kw)
        runs[name] = (ens, time.monotonic() - t)
        print(name, "spread", round(runs[name][1], 1), "s", flush=True)
    aoi, _zones, _ = cli._parse_aoi(os.path.join(WT, "data", "aoi_conus.json"))
    registry, _ = cli._watch_registry(
        SimpleNamespace(registry="mock-presets", preset_homes=40), aoi)
    s, n, w, e = wxfield._grid_bounds(spec)
    from wgresponse.contracts import BBox
    homes = [c for c in registry.protected_clients(BBox(w, s, e, n))
             if geo.latlon_to_rc(spec, c.loc) is not None]
    ring = []
    for d in RING_KM:
        for b in RING_BEARINGS:
            p = geo.destination(ign, float(b), d * 1000.0)
            if geo.latlon_to_rc(spec, p) is not None:
                ring.append((f"ring-{int(d)}km-{b:03d}", p))
    pts = [(c.client_id, c.loc) for c in homes] + ring

    def result(name):
        ens = runs[name][0]
        return SimpleNamespace(
            ensembles={label: ens}, weather_fields={label: field},
            assessments=[SimpleNamespace(client_id=cid, incident_id=label,
                                         eta=spread.eta_at(ens, loc))
                         for cid, loc in pts])

    a = measurement_a(field, now)
    b = measurement_b(label, result("off"), result("on"), now)
    b["homes"] = len(homes)
    b["ring_points"] = len(ring)
    locs = dict(pts)
    wxfig.dump(os.path.join(WT, "out", "wxfield", f"fig_{label}.npz"), label,
               f"{label} (hypothetical point fire on the real domain): "
               f"{len(field.points)} points at {field.spacing_km:.3g} km",
               spec, runs["off"][0], runs["on"][0], field,
               [(r["client_id"], locs[r["client_id"]].lat,
                 locs[r["client_id"]].lon, r["eta_p10_off_h"],
                 r["eta_p10_on_h"]) for r in b["clients"]], now)
    out = {"label": label, "tile": os.path.basename(args.tile),
           "now_s": now, "ignition": [ign.lat, ign.lon, code],
           "grid": [spec.nrows, spec.ncols, spec.cell_m],
           "km": [round(spec.nrows * spec.cell_m / 1000, 1),
                  round(spec.ncols * spec.cell_m / 1000, 1)],
           "extreme_regime": flag, "points": len(field.points),
           "spacing_km": field.spacing_km, "fetch_s": round(fetch_s, 1),
           "nws": http.counts(),
           "spread_s": {k: round(v[1], 1) for k, v in runs.items()},
           "A": a, "B": b}
    print("A: wind range", a["wind_range_mps"], "dir arc", a["dir_arc_deg"],
          "RH range", a["rh_range_pct"])
    print("B: p10", b["p10_delta"], "\n   p50", b["p50_delta"])
    print("   burned P50 off", b["burned_p50_km2_off"])
    print("   burned P50 on ", b["burned_p50_km2_on"])
    print("   front 24h", b["front_24h"])
    print("   front 48h", b["front_48h"])
    print("   max shift", b["max_shift"])
    suffix = ("" if (args.grid_km, args.points_max) == (10.0, 16)
              else f"_g{args.grid_km:g}_m{args.points_max}")
    path = os.path.join(HERE, f"domain_{label}{suffix}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    print("results:", path)


if __name__ == "__main__":
    main()
