"""Belief-vs-physics real-fire study (2026-09-27) -- STEP 1: fetch.

Pulls, for each chosen 2026 fire, its WHOLE daily-perimeter history from the
NIFC WFIGS_Daily_Perimeters_Public layer (tools/hindcast_lib.py
DAILY_PERIMETERS_URL), thinned to one capture per UTC day, plus observed
(reanalysis) hourly weather from the Open-Meteo ARCHIVE at each kept
capture's fire centroid, plus one LANDFIRE LFPS terrain tile per fire.

Reuses the hindcast harness's fetch layer (tools/hindcast_harness.py
CountingHttp: hard request budget + per-request provenance log;
tools/hindcast_lib.py URL builders + parsers; wgresponse.terrain.LfpsTerrain
with its disk cache) and its domain law (bbox of every kept perimeter + 4 km
pad; cell size = hindcast_lib.pick_cell_m: 90 m coarsened so the domain
stays <= cfg.grid_max_cells = 300k cells).

NEW here (each measured on these fires; see the report's Method section):
  * discovery without the harness's CA-only state filter (one GET);
  * WATCHER-ORDER CAPTURE REPLAY. Rows sharing one poly_PolygonDateTime are
    NOT parts of a multipart polygon: the Daily layer re-captures the
    current perimeter every day, so one polygon time appears on many rows,
    sometimes EDITED in place (Willow 06-29 17:22: 1,262 -> 1,919 -> 2,011
    ac under one timestamp) or even back-dated (Sinlahekin: 20+ rows stamped
    08-10 00:00 were published after 08-10 20:05, carrying the 08-24..08-28
    polygons). Unioning their rings (the harness's _fetch_incident law)
    stacks duplicate rings that the even-odd rasterizer turns into holes.
    Here rows are replayed in OBJECTID order (the layer's append order =
    publication order) and a row is a NEW capture only when its polygon time
    is strictly newer than the last accepted one -- the live belief's own
    rule (fire_belief._observe_perimeter). Later revisions of an accepted
    time are ignored, as the belief ignores them.
  * DATA-ERROR FILTERS: polygon times outside 2026-05-01..now are dropped
    (Sinlahekin and Plaskett each carry one row stamped 2020 / 2002), and a
    capture under half the running maximum of accepted captures is dropped
    as a sub-feature/partial row (Sinlahekin 08-17..08-27: 0-3 ac polygons
    while IncidentSize read ~155,000 ac). Every drop is logged in the
    incident file.
  * DAILY THINNING: the last accepted capture of each UTC day; a capture
    under 6 h after the previous kept one is skipped.
  * WEATHER at each kept capture's vertex centroid (the live watcher's
    fire-centroid law, threat._assess_incident), deduplicated on a 0.05 deg
    grid, fetched in ONE multi-location archive request per fire.

Data dir: out/bvp_data (gitignored). tools/hindcast_data (the pinned corpus)
is never touched.

Usage (repo root):
    py -3 scratch_review/belief_vs_physics/bvp_fetch.py discover
    py -3 scratch_review/belief_vs_physics/bvp_fetch.py fetch --fires willow,grasshopper
    py -3 scratch_review/belief_vs_physics/bvp_fetch.py fetch --fires willow --no-terrain
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import tools.hindcast_harness as HH  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain  # noqa: E402

DATA_DIR = os.path.join(_REPO, "out", "bvp_data")
DISCOVER_PATH = os.path.join(DATA_DIR, "discover.json")
FIRES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fires.json")
WX_ROUND_DEG = 0.05          # weather points deduplicated on this grid
MIN_STEP_H = 6.0             # consecutive kept captures at least this apart
MIN_FRAC_OF_MAX = 0.5        # a capture under this x running max = partial
T_MIN_S = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
T_MAX_S = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc).timestamp()
ARCHIVE_LAST_DAY = "2026-09-26"   # never ask the archive past yesterday
H_S = 3600.0


def load_fires() -> dict:
    with open(FIRES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _log_requests(http: "HH.CountingHttp", tag: str, notes: list) -> None:
    """Per-invocation provenance log (one file per tag, so parallel fetch
    processes never race on one log)."""
    path = os.path.join(DATA_DIR, "fetch_logs", f"{tag}.json")
    prior = read_json(path) if os.path.exists(path) else {}
    entry = {
        "tag": tag,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "n_requests": http.n_requests + int(prior.get("n_requests") or 0),
        "budget_per_invocation": http.budget,
        "notes": (prior.get("notes") or []) + notes,
        "requests": (prior.get("requests") or []) + http.log,
    }
    write_json(path, entry)


# ---------------------------------------------------------------------------
# discovery (1 GET, no state filter)
# ---------------------------------------------------------------------------

def discovery_url(min_acres: float, since_iso: str, limit: int) -> str:
    stats = [
        {"statisticType": "count", "onStatisticField": "OBJECTID",
         "outStatisticFieldName": "n_rows"},
        {"statisticType": "max", "onStatisticField": "poly_GISAcres",
         "outStatisticFieldName": "max_acres"},
        {"statisticType": "min", "onStatisticField": "poly_GISAcres",
         "outStatisticFieldName": "min_acres"},
        {"statisticType": "min", "onStatisticField": "poly_PolygonDateTime",
         "outStatisticFieldName": "t_min"},
        {"statisticType": "max", "onStatisticField": "poly_PolygonDateTime",
         "outStatisticFieldName": "t_max"},
    ]
    q = {
        "where": (f"poly_GISAcres >= {min_acres:g} AND "
                  f"poly_PolygonDateTime >= TIMESTAMP '{since_iso}'"),
        "outStatistics": json.dumps(stats),
        "groupByFieldsForStatistics":
            "attr_IrwinID,attr_IncidentName,attr_POOState",
        "orderByFields": "n_rows DESC",
        "f": "json",
        "resultRecordCount": str(limit),
    }
    return H.DAILY_PERIMETERS_URL + "?" + urllib.parse.urlencode(q)


def cmd_discover(args) -> int:
    http = HH.CountingHttp(args.http_budget)
    payload = HH._get_json(http, discovery_url(args.min_acres, args.since,
                                               args.limit))
    rows = []
    for f in payload.get("features") or []:
        a = f.get("attributes") or {}
        if not a.get("attr_IrwinID"):
            continue
        rows.append({
            "irwin": str(a["attr_IrwinID"]),
            "name": str(a.get("attr_IncidentName") or ""),
            "state": str(a.get("attr_POOState") or ""),
            "n_rows": int(a.get("n_rows") or 0),
            "max_acres": float(a.get("max_acres") or 0.0),
            "min_acres": float(a.get("min_acres") or 0.0),
            "t_min_s": H.epoch_s(a["t_min"]) if a.get("t_min") else None,
            "t_max_s": H.epoch_s(a["t_max"]) if a.get("t_max") else None,
        })
    rows.sort(key=lambda r: -r["n_rows"])
    write_json(DISCOVER_PATH, {"schema": "bvp-discover/1",
                               "query": {"min_acres": args.min_acres,
                                         "since": args.since},
                               "rows": rows})
    for r in rows:
        d0 = datetime.fromtimestamp(r["t_min_s"], tz=timezone.utc)
        d1 = datetime.fromtimestamp(r["t_max_s"], tz=timezone.utc)
        print(f"{r['n_rows']:4d}  {r['state']:6s} {r['name'][:24]:24s} "
              f"{d0:%m-%d}..{d1:%m-%d}  {r['min_acres']:>9,.0f} -> "
              f"{r['max_acres']:>9,.0f}  {r['irwin']}")
    _log_requests(http, "discover", [])
    print(f"[discover] {http.n_requests} request(s); wrote {DISCOVER_PATH}")
    return 0


# ---------------------------------------------------------------------------
# capture selection (pure; documented in the module docstring)
# ---------------------------------------------------------------------------

def series_all_url(irwin: str) -> str:
    """Every row of one incident, ALL attributes, no geometry."""
    q = {
        "where": f"attr_IrwinID = '{irwin}'",
        "outFields": "*",
        "returnGeometry": "false",
        "orderByFields": "poly_PolygonDateTime ASC",
        "f": "json",
    }
    return H.DAILY_PERIMETERS_URL + "?" + urllib.parse.urlencode(q)


_KEEP_ATTRS = ("OBJECTID", "poly_PolygonDateTime", "poly_DateCurrent",
               "poly_GISAcres", "attr_PercentContained", "attr_IncidentSize",
               "attr_ContainmentDateTime", "attr_ControlDateTime",
               "attr_FireOutDateTime", "attr_FireDiscoveryDateTime",
               "poly_MapMethod", "poly_FeatureCategory", "attr_IncidentName",
               "attr_POOState", "attr_FireBehaviorGeneral")


def parse_rows(payload: dict) -> list:
    rows = []
    for f in payload.get("features") or []:
        a = f.get("attributes") or {}
        oid = a.get("OBJECTID")
        t_raw = a.get("poly_PolygonDateTime") or a.get("poly_DateCurrent")
        if oid is None or t_raw is None:
            continue
        r = {k: a.get(k) for k in _KEEP_ATTRS if k in a}
        r["oid"] = int(oid)
        r["t_s"] = H.epoch_s(t_raw)
        r["acres"] = float(a.get("poly_GISAcres") or 0.0)
        rows.append(r)
    rows.sort(key=lambda r: r["oid"])
    return rows


def pick_captures(rows: list) -> tuple:
    """(kept [(t_s, row, n_ignored_revisions)], drops [str]) -- see the
    module docstring: date sanity, OBJECTID-order watcher replay, partial-
    row filter, daily thinning."""
    drops = []
    acc = []                      # (t_s, row) accepted in publication order
    revs: dict = {}
    last_t = None
    for r in sorted(rows, key=lambda r: r["oid"]):
        t = r["t_s"]
        if not (T_MIN_S <= t <= T_MAX_S) or not r["acres"] > 0.0:
            drops.append(f"oid {r['oid']}: polygon time "
                         f"{datetime.fromtimestamp(t, tz=timezone.utc):%Y-%m-%d %H:%M}"
                         f" / {r['acres']:,.0f} ac outside the sane window")
            continue
        if last_t is not None and t <= last_t:
            revs[t] = revs.get(t, 0) + 1      # a revision or back-dated row
            continue
        acc.append((t, r))
        last_t = t
    kept = []
    run_max = 0.0
    for t, r in acc:
        if run_max > 0.0 and r["acres"] < MIN_FRAC_OF_MAX * run_max:
            drops.append(f"oid {r['oid']}: {r['acres']:,.0f} ac < "
                         f"{MIN_FRAC_OF_MAX:g} x running max {run_max:,.0f} "
                         f"(partial / sub-feature row)")
            continue
        run_max = max(run_max, r["acres"])
        kept.append((t, r))
    by_day: dict = {}
    for t, r in kept:
        day = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[day] = (t, r)                  # ascending -> the day's last
    out = []
    for day in sorted(by_day):
        t, r = by_day[day]
        if out and (t - out[-1][0]) / H_S < MIN_STEP_H:
            continue
        out.append((t, r, revs.get(t, 0)))
    return out, drops


def centroid(rings: list) -> LatLon:
    """The live watcher's fire centroid: the mean of the (<= 200-vertex
    subsampled) perimeter ring vertices (threat._assess_incident)."""
    pts = [p for ring in rings
           for p in H.subsample_ring(ring, H.MAX_RING_VERTS)]
    return LatLon(sum(p.lat for p in pts) / len(pts),
                  sum(p.lon for p in pts) / len(pts))


def round_pt(p: LatLon) -> list:
    return [round(round(p.lat / WX_ROUND_DEG) * WX_ROUND_DEG, 4),
            round(round(p.lon / WX_ROUND_DEG) * WX_ROUND_DEG, 4)]


def archive_multi_url(points: list, start_date: str, end_date: str) -> str:
    q = {
        "latitude": ",".join(f"{la:.4f}" for la, _lo in points),
        "longitude": ",".join(f"{lo:.4f}" for _la, lo in points),
        "start_date": start_date,
        "end_date": end_date,
        "hourly": H._ARCHIVE_HOURLY,
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    return H.ARCHIVE_WEATHER_URL + "?" + urllib.parse.urlencode(q)


def pad_m() -> float:
    """Domain pad: the harness's 4 km (HH._DOMAIN_PAD_M) unless the WIDE
    sensitivity variant is selected (env BVP_VARIANT=wide: 20 km, closer to
    a live watcher domain, which spans the fire plus every protected client
    within the 60 km threat radius + 2 km)."""
    return WIDE_PAD_M if os.environ.get("BVP_VARIANT") == "wide" \
        else HH._DOMAIN_PAD_M


WIDE_PAD_M = 20_000.0


def domain_of(snaps: list):
    """The harness domain law: bbox of every kept perimeter + pad (4 km, the
    harness's; 20 km in the wide variant), hindcast_lib.pick_cell_m (90 m,
    coarsened so the domain stays <= 300k cells)."""
    rings_list = [[[LatLon(v[0], v[1]) for v in ring] for ring in s["rings"]]
                  for s in snaps]
    bbox = H.rings_bbox(rings_list, pad_m())
    return bbox, H.pick_cell_m(bbox, ResponseConfig())


# ---------------------------------------------------------------------------
# per-fire fetch
# ---------------------------------------------------------------------------

def fetch_fire(http, key: str, irwin: str, name: str, notes: list,
               terrain: bool = True, lfps_timeout_s: float = 1800.0) -> None:
    rows_path = os.path.join(DATA_DIR, "rows", f"{key}.json")
    geom_path = os.path.join(DATA_DIR, "geoms", f"{key}.json")
    inc_path = os.path.join(DATA_DIR, "incidents", f"{key}.json")
    wx_path = os.path.join(DATA_DIR, "weather", f"{key}.json")

    if os.path.exists(rows_path):
        rows = read_json(rows_path)["rows"]
    else:
        rows = parse_rows(HH._get_json(http, series_all_url(irwin)))
        if not rows:
            raise RuntimeError("no rows for this IRWIN id")
        write_json(rows_path, {"schema": "bvp-rows/1", "irwin": irwin,
                               "rows": rows})
    picks, drops = pick_captures(rows)
    geoms = read_json(geom_path) if os.path.exists(geom_path) else {}
    need = [r["oid"] for _t, r, _n in picks if str(r["oid"]) not in geoms]
    for i in range(0, len(need), 8):
        got = H.parse_snapshot_features(HH._get_json(
            http, H.geometry_query_url(need[i:i + 8])))
        for oid, (_t, rings) in got.items():
            geoms[str(oid)] = [[[p.lat, p.lon] for p in ring]
                               for ring in rings]
        write_json(geom_path, geoms)
    snaps = []
    for t_s, r, n_rev in picks:
        rings = geoms.get(str(r["oid"]))
        if not rings:
            drops.append(f"oid {r['oid']}: no geometry returned")
            continue
        pct = r.get("attr_PercentContained")
        size = r.get("attr_IncidentSize")
        snaps.append({
            "t_s": t_s, "oid": r["oid"], "acres": r["acres"],
            "ignored_revisions": n_rev,
            "pct_contained": pct if isinstance(pct, (int, float)) else None,
            "incident_size_ac": size if isinstance(size, (int, float))
            else None,
            "map_method": r.get("poly_MapMethod"),
            "rings": rings})
    write_json(inc_path, {"schema": "bvp-incident/2", "key": key,
                          "irwin": irwin, "name": name,
                          "n_raw_rows": len(rows), "drops": drops,
                          "snapshots": snaps})
    print(f"[fetch] {name}: {len(rows)} raw rows -> {len(snaps)} daily "
          f"captures ({len(drops)} rows dropped as data errors)", flush=True)

    pts = []
    for s in snaps:
        rings = [[LatLon(v[0], v[1]) for v in ring] for ring in s["rings"]]
        rp = round_pt(centroid(rings))
        if rp not in pts:
            pts.append(rp)
    t_a = snaps[0]["t_s"] - 2 * 86400.0
    t_b = snaps[-1]["t_s"] + 5 * 86400.0
    d0 = datetime.fromtimestamp(t_a, tz=timezone.utc).strftime("%Y-%m-%d")
    d1 = min(datetime.fromtimestamp(t_b, tz=timezone.utc).strftime("%Y-%m-%d"),
             ARCHIVE_LAST_DAY)
    have = read_json(wx_path) if os.path.exists(wx_path) else None
    if not (have and have.get("start_date") == d0 and have.get("end_date") == d1
            and all(p in have["points"] for p in pts)):
        payload = HH._get_json(http, archive_multi_url(pts, d0, d1))
        if isinstance(payload, dict):
            payload = [payload]
        write_json(wx_path, {"schema": "bvp-weather/2", "points": pts,
                             "start_date": d0, "end_date": d1,
                             "payloads": payload})
        print(f"[fetch] {name}: weather {d0}..{d1} at {len(pts)} point(s)",
              flush=True)
    else:
        print(f"[fetch] {name}: weather cache hit", flush=True)

    if not terrain:
        return
    bbox, cell_m = domain_of(snaps)
    lfps = LfpsTerrain(cache_dir=os.path.join(DATA_DIR, "lfps"),
                       http_get=http, poll_interval_s=20.0,
                       timeout_s=lfps_timeout_s,
                       email="ops@thewildfireguardian.com")
    cp = lfps.cache_path(bbox, cell_m)
    if cp and os.path.exists(cp):
        print(f"[fetch] {name}: LFPS tile cache hit", flush=True)
        return
    print(f"[fetch] {name}: LFPS job {bbox.east - bbox.west:.3f} x "
          f"{bbox.north - bbox.south:.3f} deg @ {cell_m:.0f} m ...", flush=True)
    tile = lfps.tile(bbox, cell_m)
    notes.append(f"LFPS tile fetched for {name}: {tile.spec.nrows}x"
                 f"{tile.spec.ncols} @ {cell_m:.0f} m")
    print(f"[fetch] {name}: LFPS tile {tile.spec.nrows}x{tile.spec.ncols} "
          f"@ {cell_m:.0f} m", flush=True)


def cmd_fetch(args) -> int:
    fires = load_fires()
    keys = [k.strip() for k in args.fires.split(",") if k.strip()]
    http = HH.CountingHttp(args.http_budget)
    notes: list = []
    tag = args.tag or ("fetch_" + "_".join(keys))
    rc = 0
    for key in keys:
        if key not in fires:
            print(f"[fetch] unknown fire key {key!r} (fires.json)")
            rc = 1
            continue
        irwin, name = fires[key]["irwin"], fires[key]["name"]
        try:
            fetch_fire(http, key, irwin, name, notes,
                       terrain=not args.no_terrain)
        except HH.BudgetExceeded as exc:
            notes.append(f"BLOCKED at {name}: {exc}")
            print(f"[fetch] BLOCKED at {name}: {exc}", flush=True)
            rc = 1
            break
        except Exception as exc:
            notes.append(f"DEGRADED: {name} ({irwin}) -- {exc}")
            print(f"[fetch] DEGRADED: {name}: {exc}", flush=True)
            rc = 1
    _log_requests(http, tag, notes)
    print(f"[fetch] done: {http.n_requests} HTTP requests (budget "
          f"{args.http_budget})", flush=True)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("discover", "fetch"))
    ap.add_argument("--min-acres", type=float, default=3000.0)
    ap.add_argument("--since", default="2026-06-01 00:00:00")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--fires", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-terrain", action="store_true")
    ap.add_argument("--http-budget", type=int, default=150)
    ap.add_argument("--variant", choices=("", "wide"), default="")
    args = ap.parse_args(argv)
    if args.variant:
        os.environ["BVP_VARIANT"] = args.variant
    os.makedirs(DATA_DIR, exist_ok=True)
    return {"discover": cmd_discover, "fetch": cmd_fetch}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
