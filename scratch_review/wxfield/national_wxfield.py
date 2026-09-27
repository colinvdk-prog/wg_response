"""Local weather across the national domains (SAFETY SEMANTICS 17,
2026-09-27). NOT product code — measurements A, B and C of
docs/LIVE_WORLD_PLAN.md "Spread reads the local weather forecast
(2026-09-27)".

One cycle of the recorded CONUS payloads (2026-09-27 15:55Z;
scratch_review/contained/feedsnap.py) through the REAL adapters, fusion,
ThreatMonitor and alert engine with the production flags + --drop-contained
--confirmed-perimeters, the W6 demo roster (mock-presets, 520 homes) and the
W6 LANDFIRE tile cache — and FRESH NWS forecasts (the first run fetches them
live; every later run replays the same bytes from out/wxfield/nws_cache, so
every variant of every run sees ONE forecast vintage):

  OFF        the legacy law: one forecast at each fire centroid
  ON         --weather-grid-km G (default 10) --weather-points-max M
             (default 16): a cold process-level NWS cache
  ON_warm    the same monitor 5 min later (the cache within its TTL)
  ON_ttl     the same monitor after the 30-min TTL (gridpoints refetched,
             the static points mapping kept)
  ON_nocache ON with the NWS cache removed (every point = 2 requests)

Measured: A (per domain) how far the forecast points disagree on wind speed,
direction and RH over the next 48 h; B (per domain) client P10/P50 arrival
changes OFF -> ON, the burned area by hour (P50 and P10) and where the front
differs most; C wall time per cycle and per phase, NWS requests per cycle
for each variant. ON, ON_warm, ON_ttl and ON_nocache must produce IDENTICAL
ensembles (same forecasts, different request paths) — asserted.

    py -3 scratch_review/wxfield/national_wxfield.py [--grid-km 10]
        [--points-max 16] [--live] [--picks NAME,NAME]

Data (gitignored): out/contained/{snapshot,ab_terrain} copied from the
contained-fire study (READ-ONLY sources:
.claude/worktrees/agent-a9bd3368cd94461f0/out/contained/); out/wxfield/
nws_cache (written here). Results: scratch_review/wxfield/
national_wxfield[_g<G>_m<M>].json; figure data out/wxfield/fig_<name>.npz
for the picked domains (wxfig.py composes the figure).
"""
import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import threading
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
from wgresponse import cli, feeds, geo, weather, wxfield  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

NWS_CACHE = os.path.join(WT, "out", "wxfield", "nws_cache")
AREA_HOURS = (0, 3, 6, 12, 24, 36, 48, 72, 96)


class DiskNws:
    """http_get for NwsWeather: a disk cache keyed on the URL (the first call
    fetches live and stores 200s; later calls replay the bytes; --live
    refetches everything) + thread-safe counters of the requests NwsWeather
    ISSUED (by kind) and of the network fetches (with their wall time)."""

    def __init__(self, cache_dir, live=False):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.live = live
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.requests = {"points": 0, "grid": 0, "other": 0}
        self.net = {"points": 0, "grid": 0, "other": 0}
        self.net_s = 0.0
        self.grid_urls = set()

    def __call__(self, url, headers=None):
        kind = ("points" if "/points/" in url else
                "grid" if "/gridpoints/" in url else "other")
        with self.lock:
            self.requests[kind] += 1
            if kind == "grid":
                self.grid_urls.add(url)
        path = os.path.join(self.dir, hashlib.sha256(
            url.encode("utf-8")).hexdigest()[:24] + ".json")
        if not self.live and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d["status"], d["body"].encode("utf-8")
        t = time.monotonic()
        try:
            status, body = weather._default_http_get(url, headers)
        except Exception as exc:  # noqa: BLE001 — a failed fetch is data here
            status, body = 599, str(exc).encode("utf-8")
        with self.lock:
            self.net[kind] += 1
            self.net_s += time.monotonic() - t
        if status == 200:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "status": status,
                           "body": body.decode("utf-8")}, f)
        return status, body

    def counts(self):
        with self.lock:
            return {"requests": dict(self.requests), "network": dict(self.net),
                    "network_s": round(self.net_s, 1),
                    "requests_total": sum(self.requests.values()),
                    "gridpoints_distinct": len(self.grid_urls)}


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def make_monitor(cfg, http, cache, registry, aoi, zones, nocache=False):
    fe = [feeds.WfigsFeed(http_get=feedsnap.ReplayHttp()),
          feeds.FirisFeed(http_get=feedsnap.ReplayHttp()),
          feeds.CalFireFeed(http_get=feedsnap.ReplayHttp())]
    wx = weather.NwsWeather(http_get=http, cfg=cfg, cache=cache)
    if nocache:
        wx._cache = None                     # ON without the cache
    return ThreatMonitor(
        feeds=fe, weather_provider=wx,
        terrain_provider=LfpsTerrain(
            cache_dir=os.path.join(feedsnap.DATA, "ab_terrain"),
            email=os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"),
        registry=registry, alert_engine=AlertEngine(cfg), cfg=cfg,
        safe_zones=zones, crew_tracker=None, dispatcher=None, aoi=aoi)


def run_cycle(mon, http, now, label):
    http.reset()
    t0 = time.monotonic()
    res = mon.cycle(now)
    wall = time.monotonic() - t0
    inc_t = (res.timings or {}).get("incidents") or {}
    out = {"label": label, "wall_s": round(wall, 1),
           "phases_s": {k: round(v, 2) for k, v in (res.timings or {}).items()
                        if isinstance(v, (int, float))},
           "weather_s": round(sum(d.get("weather_s", 0.0)
                                  for d in inc_t.values()), 1),
           "spread_s": round(sum(d.get("spread_s", 0.0)
                                 for d in inc_t.values()), 1),
           "domains": len(res.ensembles),
           "points": sum(len(f.points) for f in res.weather_fields.values()),
           "points_failed": sum(1 for f in res.weather_fields.values()
                                for p in f.points if not p.ok),
           "nws": http.counts()}
    print(f"[{label}] {out['wall_s']} s; domains {out['domains']}, points "
          f"{out['points']} (failed {out['points_failed']}); weather "
          f"{out['weather_s']} s, spread {out['spread_s']} s; NWS "
          f"{out['nws']['requests']} network {out['nws']['network']} "
          f"({out['nws']['network_s']} s)", flush=True)
    return res, out


def ens_digest(res):
    h = hashlib.sha256()
    for iid in sorted(res.ensembles):
        e = res.ensembles[iid]
        for r in (e.p10, e.p50, e.p90):
            h.update(repr(r.data).encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------------ analysis

def _dist_brg(a, b):
    return geo.haversine_m(a, b), geo.bearing_deg(a, b)


def _ang(a, b):
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def measurement_a(field, now, hours=48):
    """How far the points disagree over the next `hours` hours."""
    pts = field.points
    rows = []
    for h in range(hours + 1):
        t = now + h * 3600.0
        ss = [fc.at(t) for fc in field.forecasts]
        rows.append({
            "h": h,
            "wind_range": max(s.wind_speed_mps for s in ss) - min(
                s.wind_speed_mps for s in ss),
            "gust_range": max(s.wind_gust_mps for s in ss) - min(
                s.wind_gust_mps for s in ss),
            "dir_arc": wxfield._arc_deg([s.wind_dir_deg for s in ss]),
            "rh_range": max(s.rh_pct for s in ss) - min(s.rh_pct for s in ss),
            "wind_max": max(s.wind_speed_mps for s in ss),
            "wind_min": min(s.wind_speed_mps for s in ss),
        })

    def stats(key):
        v = sorted(r[key] for r in rows)
        return {"median": round(v[len(v) // 2], 2), "max": round(v[-1], 2),
                "mean": round(sum(v) / len(v), 2)}

    per_point = []
    c = field.forecasts[0]
    for z, (p, fc) in enumerate(zip(pts, field.forecasts)):
        dw = dd = dr = 0.0
        for h in range(hours + 1):
            t = now + h * 3600.0
            a, b = fc.at(t), c.at(t)
            dw += abs(a.wind_speed_mps - b.wind_speed_mps)
            dd += _ang(a.wind_dir_deg, b.wind_dir_deg)
            dr += abs(a.rh_pct - b.rh_pct)
        n = hours + 1
        d_m, brg = _dist_brg(LatLon(pts[0].lat, pts[0].lon), LatLon(p.lat, p.lon))
        per_point.append({"zone": z, "lat": round(p.lat, 4),
                          "lon": round(p.lon, 4), "ok": p.ok,
                          "cells": p.cells, "km_from_centroid": round(d_m / 1000, 1),
                          "bearing": round(brg), "mean_abs_dwind_mps": round(dw / n, 2),
                          "mean_abs_ddir_deg": round(dd / n, 1),
                          "mean_abs_drh_pct": round(dr / n, 1)})
    return {"hours": hours, "wind_range_mps": stats("wind_range"),
            "gust_range_mps": stats("gust_range"),
            "dir_arc_deg": stats("dir_arc"), "rh_range_pct": stats("rh_range"),
            "per_point_vs_centroid": per_point,
            "hourly": [{k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in r.items()} for r in rows]}


def burned_curve(raster, t0, cell_km2):
    out = {}
    for h in AREA_HOURS:
        lim = t0 + h * 3600.0
        out[h] = round(sum(1 for v in raster.data if v <= lim) * cell_km2, 1)
    return out


def front_difference(e_off, e_on, t0, field, centroid, lead_h):
    """Cells burned by t0+lead in exactly one of OFF / ON (P50): areas,
    their centres, the farthest disagreeing cell from the centroid."""
    spec = e_off.spec
    lim = t0 + lead_h * 3600.0
    cell_km2 = (spec.cell_m / 1000.0) ** 2
    on_only, off_only = [], []
    for i, (a, b) in enumerate(zip(e_off.p50.data, e_on.p50.data)):
        ia, ib = a <= lim, b <= lim
        if ib and not ia:
            on_only.append(i)
        elif ia and not ib:
            off_only.append(i)

    def summarize(cells):
        if not cells:
            return None
        rs = [divmod(i, spec.ncols) for i in cells]
        mr = sum(r for r, _ in rs) / len(rs)
        mc = sum(c for _, c in rs) / len(rs)
        ctr = geo.rc_to_latlon(spec, int(round(mr)), int(round(mc)))
        far = max(cells, key=lambda i: geo.haversine_m(
            centroid, geo.rc_to_latlon(spec, *divmod(i, spec.ncols))))
        far_ll = geo.rc_to_latlon(spec, *divmod(far, spec.ncols))
        d_c, b_c = _dist_brg(centroid, ctr)
        d_f, b_f = _dist_brg(centroid, far_ll)
        zones = {}
        for i in cells:
            z = field.zone_of[i]
            zones[z] = zones.get(z, 0) + 1
        top = sorted(zones.items(), key=lambda kv: -kv[1])[:3]
        return {"km2": round(len(cells) * cell_km2, 1),
                "centre_km": round(d_c / 1000, 1), "centre_bearing": round(b_c),
                "farthest_km": round(d_f / 1000, 1),
                "farthest_bearing": round(b_f),
                "top_zones": [(z, round(n * cell_km2, 1)) for z, n in top]}

    burned_off = sum(1 for v in e_off.p50.data if v <= lim) * cell_km2
    burned_on = sum(1 for v in e_on.p50.data if v <= lim) * cell_km2
    return {"lead_h": lead_h, "burned_off_km2": round(burned_off, 1),
            "burned_on_km2": round(burned_on, 1),
            "on_only": summarize(on_only), "off_only": summarize(off_only)}


def max_front_shift(e_off, e_on, centroid):
    """The cell with the largest |P50 ON - P50 OFF| among cells reached in
    both, and the counts reached in only one."""
    spec = e_off.spec
    best = None
    only_on = only_off = 0
    for i, (a, b) in enumerate(zip(e_off.p50.data, e_on.p50.data)):
        fa, fb = math.isfinite(a), math.isfinite(b)
        if fa and fb:
            d = b - a
            if best is None or abs(d) > abs(best[1]):
                best = (i, d)
        elif fb:
            only_on += 1
        elif fa:
            only_off += 1
    if best is None:
        return None
    ll = geo.rc_to_latlon(spec, *divmod(best[0], spec.ncols))
    d, b = _dist_brg(centroid, ll)
    return {"dP50_h": round(best[1] / 3600.0, 2), "km_from_centroid":
            round(d / 1000, 1), "bearing": round(b),
            "cells_reached_on_only": only_on, "cells_reached_off_only": only_off}


def measurement_b(iid, res_off, res_on, now):
    e_off, e_on = res_off.ensembles[iid], res_on.ensembles[iid]
    field = res_on.weather_fields[iid]
    centroid = LatLon(field.points[0].lat, field.points[0].lon)
    spec = e_off.spec
    cell_km2 = (spec.cell_m / 1000.0) ** 2
    off = {(a.client_id): a.eta for a in res_off.assessments
           if a.incident_id == iid}
    on = {(a.client_id): a.eta for a in res_on.assessments
          if a.incident_id == iid}
    clients = []
    for cid in sorted(off):
        a, b = off[cid], on.get(cid)
        row = {"client_id": cid}
        for q in ("eta_p10_s", "eta_p50_s"):
            va, vb = getattr(a, q), getattr(b, q)
            row[q.replace("_s", "_off_h")] = (None if not math.isfinite(va)
                                              else round((va - now) / 3600, 2))
            row[q.replace("_s", "_on_h")] = (None if not math.isfinite(vb)
                                             else round((vb - now) / 3600, 2))
        clients.append(row)

    def deltas(q):
        both = [(r[f"eta_{q}_on_h"] - r[f"eta_{q}_off_h"]) for r in clients
                if r[f"eta_{q}_on_h"] is not None
                and r[f"eta_{q}_off_h"] is not None]
        return {"n_both": len(both),
                "reached_off_only": sum(1 for r in clients
                                        if r[f"eta_{q}_off_h"] is not None
                                        and r[f"eta_{q}_on_h"] is None),
                "reached_on_only": sum(1 for r in clients
                                       if r[f"eta_{q}_on_h"] is not None
                                       and r[f"eta_{q}_off_h"] is None),
                "mean_h": round(sum(both) / len(both), 2) if both else None,
                "mean_abs_h": (round(sum(abs(x) for x in both) / len(both), 2)
                               if both else None),
                "min_h": round(min(both), 2) if both else None,
                "max_h": round(max(both), 2) if both else None,
                "moved_over_1h": sum(1 for x in both if abs(x) > 1.0)}

    return {"incident_id": iid,
            "clients": clients,
            "p10_delta": deltas("p10"), "p50_delta": deltas("p50"),
            "burned_p50_km2_off": burned_curve(e_off.p50, now, cell_km2),
            "burned_p50_km2_on": burned_curve(e_on.p50, now, cell_km2),
            "burned_p10_km2_off": burned_curve(e_off.p10, now, cell_km2),
            "burned_p10_km2_on": burned_curve(e_on.p10, now, cell_km2),
            "front_24h": front_difference(e_off, e_on, now, field, centroid, 24),
            "front_48h": front_difference(e_off, e_on, now, field, centroid, 48),
            "max_shift": max_front_shift(e_off, e_on, centroid)}


def domain_row(iid, inc, res_off, res_on, now):
    e = res_off.ensembles[iid]
    sp = e.spec
    s, n, w, ea = wxfield._grid_bounds(sp)
    field = res_on.weather_fields.get(iid)
    inc_t_off = res_off.timings["incidents"][iid]
    inc_t_on = res_on.timings["incidents"][iid]
    return {"incident_id": iid, "name": inc.name,
            "grid": [sp.nrows, sp.ncols, round(sp.cell_m, 1)],
            "cells": sp.nrows * sp.ncols,
            "km": [round(sp.nrows * sp.cell_m / 1000, 1),
                   round(sp.ncols * sp.cell_m / 1000, 1)],
            "bbox": [round(s, 3), round(w, 3), round(n, 3), round(ea, 3)],
            "points": len(field.points) if field else 1,
            "spacing_km": field.spacing_km if field else None,
            "failed": sum(1 for p in field.points if not p.ok) if field else 0,
            "spread_s_off": round(inc_t_off.get("spread_s", 0.0), 2),
            "spread_s_on": round(inc_t_on.get("spread_s", 0.0), 2),
            "weather_s_off": round(inc_t_off.get("weather_s", 0.0), 2),
            "weather_s_on": round(inc_t_on.get("weather_s", 0.0), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-km", type=float, default=10.0)
    ap.add_argument("--points-max", type=int, default=16)
    ap.add_argument("--live", action="store_true",
                    help="refetch every NWS URL (network timing)")
    ap.add_argument("--nws-cache", default=NWS_CACHE,
                    help="the NWS disk cache (default out/wxfield/nws_cache; "
                         "give --live runs their own so the measured vintage "
                         "stays put)")
    ap.add_argument("--out-suffix", default="",
                    help="suffix for the results JSON (e.g. _live)")
    ap.add_argument("--picks", default="",
                    help="comma-separated incident ids OR names for the "
                         "detail tables + figure data (default: the two "
                         "largest domains whose fire spreads)")
    args = ap.parse_args()
    manifest = feedsnap.ReplayHttp().manifest
    now = float(manifest["recorded_at_s"])
    aoi, zones, _ = cli._parse_aoi(os.path.join(WT, "data", "aoi_conus.json"))
    registry, _ = cli._watch_registry(
        SimpleNamespace(registry="mock-presets", preset_homes=40), aoi)
    base = dataclasses.replace(ResponseConfig(), **PROD, **NEW)
    on_cfg = dataclasses.replace(base, weather_grid_km=args.grid_km,
                                 weather_points_max=args.points_max)
    http = DiskNws(args.nws_cache, live=args.live)
    out = {"snapshot_utc": manifest["recorded_utc"], "now_s": now,
           "grid_km": args.grid_km, "points_max": args.points_max,
           "cycles": {}}

    mon_off = make_monitor(base, http, None, registry, aoi, zones)
    res_off, out["cycles"]["OFF"] = run_cycle(mon_off, http, now, "OFF")

    clk = Clock(1000.0)
    cache = weather.NwsForecastCache(clock=clk)
    mon_on = make_monitor(on_cfg, http, cache, registry, aoi, zones)
    res_on, out["cycles"]["ON"] = run_cycle(mon_on, http, now, "ON")
    dig = ens_digest(res_on)
    clk.t += 300.0
    r2, out["cycles"]["ON_warm"] = run_cycle(mon_on, http, now + 300.0,
                                             "ON_warm")
    clk.t += 1800.0
    r3, out["cycles"]["ON_ttl"] = run_cycle(mon_on, http, now + 2100.0,
                                            "ON_ttl")
    mon_nc = make_monitor(on_cfg, http, None, registry, aoi, zones,
                          nocache=True)
    r4, out["cycles"]["ON_nocache"] = run_cycle(mon_nc, http, now,
                                                "ON_nocache")
    assert ens_digest(r4) == dig, "the cache changed the forecasts"
    out["cache_stats"] = cache.stats()
    # (ON_warm / ON_ttl run at later clocks: their ensembles are the same
    # forecasts spread from later times — compared for the request counts
    # and wall time only)

    names = {i.incident_id: i.name for i in res_off.incidents}
    incs = {i.incident_id: i for i in res_off.incidents}
    domains = [domain_row(iid, incs[iid], res_off, res_on, now)
               for iid in sorted(res_off.ensembles)]
    domains.sort(key=lambda d: -d["cells"] * (d["grid"][2] ** 2))
    out["domains"] = domains
    print("\ndomains (largest first):")
    for d in domains:
        print(f"  {d['name'][:22]:22s} {d['km'][0]:5.1f} x {d['km'][1]:5.1f} km "
              f"cells {d['cells']:7d} points {d['points']:2d} @ "
              f"{d['spacing_km']} km failed {d['failed']} spread "
              f"{d['spread_s_off']:6.2f} -> {d['spread_s_on']:6.2f} s")
    out["A"] = {}
    out["B"] = {}
    for iid in sorted(res_off.ensembles):
        fld = res_on.weather_fields.get(iid)
        if fld is None or len(fld.points) < 2:
            continue
        a = measurement_a(fld, now)
        b = measurement_b(iid, res_off, res_on, now)
        a["name"] = b["name"] = names.get(iid, iid)
        out["A"][iid] = a
        out["B"][iid] = b
    by_name = {d["name"]: d["incident_id"] for d in domains}
    wanted = [p for p in args.picks.split(",") if p]
    picks = ([by_name.get(p, p) for p in wanted] or
             [d["incident_id"] for d in domains
              if d["incident_id"] in out["B"]
              and out["B"][d["incident_id"]]["burned_p50_km2_on"][96] > 0][:2])
    out["picks"] = picks
    client_locs = {c.client_id: c.loc for c in registry.protected_clients(aoi)}
    for iid in picks:
        b = out["B"][iid]
        clients = [(r["client_id"], client_locs[r["client_id"]].lat,
                    client_locs[r["client_id"]].lon, r["eta_p10_off_h"],
                    r["eta_p10_on_h"]) for r in b["clients"]]
        fld = res_on.weather_fields[iid]
        title = (f"{names.get(iid, iid)} (live fire, national snapshot): "
                 f"{len(fld.points)} points at {fld.spacing_km:.3g} km")
        slug = "".join(ch for ch in names.get(iid, iid).lower()
                       if ch.isalnum())[:24]
        wxfig.dump(os.path.join(WT, "out", "wxfield", f"fig_{slug}.npz"),
                   iid, title, res_off.ensembles[iid].spec,
                   res_off.ensembles[iid], res_on.ensembles[iid], fld,
                   clients, now)
    for iid in picks:
        a, b = out["A"][iid], out["B"][iid]
        print(f"\n== {a['name']} ({iid})")
        print("  A: wind range", a["wind_range_mps"], "dir arc", a["dir_arc_deg"],
              "RH range", a["rh_range_pct"])
        print("  B: p10", b["p10_delta"], "\n     p50", b["p50_delta"])
        print("     burned P50 off", b["burned_p50_km2_off"])
        print("     burned P50 on ", b["burned_p50_km2_on"])
        print("     front 24h", b["front_24h"])
        print("     front 48h", b["front_48h"])
        print("     max shift", b["max_shift"])
    suffix = ("" if (args.grid_km, args.points_max) == (10.0, 16)
              else f"_g{args.grid_km:g}_m{args.points_max}") + args.out_suffix
    path = os.path.join(HERE, f"national_wxfield{suffix}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    print("results:", path)


if __name__ == "__main__":
    main()
