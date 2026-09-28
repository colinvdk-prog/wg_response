"""hindcast_harness — Sprint-2 D1 (Council-1 model F3+F8): hindcast +
quantile-calibration harness CLI. Evidence engine only — NO engine changes;
the output is docs/calibration/hindcast_first_pass.md plus cached fixtures.

Subcommands (run from the repo root: `py -3 tools/hindcast_harness.py ...`):

    research   probe the WFIGS Daily-perimeters layer for candidate incidents
               (1 read-only GET; prints the table, fetches nothing else)
    fetch      pull perimeter time series + observed weather + LFPS terrain
               for the selected incidents into --data-dir (skips anything
               already cached; every HTTP request counted + logged, hard
               budget --http-budget)
    replay     re-run the shipped engine over every cached snapshot pair and
               score coverage/PIT/head-run (OFFLINE — cache only)
    anchors    run the offline Tubbs/Camp landmark scenario checks
    report     render docs/calibration/hindcast_first_pass.md from cached
               results (OFFLINE)
    repair     rebuild pre-fix (hindcast-incident/1) incident files under
               the snapshot rule at their cached capture times (2 read-only
               GETs per file that stacked same-time rows; see
               hindcast_lib's SNAPSHOT RULE). `replay` refuses an
               unrepaired file that stacks rows.
    all        fetch (unless --offline) -> replay -> anchors -> report

Live network policy (ticket): READ-ONLY GETs to keyless public services only
— NIFC/WFIGS ArcGIS (public domain), Open-Meteo archive (free tier is
NON-COMMERCIAL; this is an offline R&D calibration study — see the licensing
note in tools/hindcast_lib.py), LANDFIRE LFPS (public domain). Never AWS,
never writes. Volume is modest and hard-capped; the measured request count is
printed and written into the report's reachability log.

Determinism: all fetched inputs are cached to disk; replay/anchors/report are
pure functions of the cache + the shipped ResponseConfig (ensemble members
seed on (incident_id, k) per contracts — never wall-clock), so re-runs off
the same cache are byte-identical.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from wgresponse import geo  # noqa: E402
from wgresponse.contracts import BBox, LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402

DEFAULT_DATA_DIR = os.path.join("tools", "hindcast_data")
DEFAULT_REPORT = os.path.join("docs", "calibration", "hindcast_first_pass.md")
_USER_AGENT = ResponseConfig().user_agent
_DOMAIN_PAD_M = 4000.0          # perimeter-union pad: room for over-prediction
_WX_MARGIN_S = 24 * 3600.0      # weather series margin around the snapshot span
# Incident-file schemas. /1 files unioned every row sharing a capture time;
# /2 files follow hindcast_lib's SNAPSHOT RULE (first publication per
# capture time, separate pieces kept, `dropped_oids` recorded).
LEGACY_INCIDENT_SCHEMA = "hindcast-incident/1"
INCIDENT_SCHEMA = "hindcast-incident/2"
SNAPSHOT_RULE = ("first publication (smallest OBJECTID) per capture time; "
                 "later same-time rows dropped as versions unless spatially "
                 "disjoint (separate pieces, kept); growth gating on the "
                 "first publication's acreage")

# Widened-band variant measured alongside the shipped default (RECOMMENDATION
# evidence for the perturbation knobs; config-only, engine untouched).
_WIDE_VARIANT = {"wind_perturb_frac": 0.50, "dir_perturb_deg": 45.0}


class BudgetExceeded(RuntimeError):
    pass


class CountingHttp:
    """Injectable http_get (url, headers) -> (status, bytes) with a hard
    request budget and a provenance log (url, status, bytes)."""

    def __init__(self, budget: int):
        self.budget = int(budget)
        self.n_requests = 0
        self.log: list = []

    def __call__(self, url: str, headers=None, timeout_s: float = 120.0):
        if self.n_requests >= self.budget:
            raise BudgetExceeded(
                f"HTTP budget of {self.budget} requests exhausted (refusing "
                f"{url.split('?')[0]})")
        self.n_requests += 1
        hdrs = {"User-Agent": _USER_AGENT}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
            status = resp.getcode()
        self.log.append({"url": url.split("?")[0], "status": status,
                         "bytes": len(body)})
        return status, body


def _get_json(http: CountingHttp, url: str) -> dict:
    status, body = http(url, {"Accept": "application/json"})
    if status != 200:
        raise RuntimeError(f"HTTP {status} from {url.split('?')[0]}")
    payload = json.loads(body.decode("utf-8"))
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(f"ArcGIS error: {payload['error']}")
    return payload


def _irwin_slug(irwin: str) -> str:
    return "".join(ch for ch in irwin.lower() if ch.isalnum())[:24]


def _ensure_dirs(data_dir: str) -> None:
    for sub in ("incidents", "weather", "lfps"):
        os.makedirs(os.path.join(data_dir, sub), exist_ok=True)


def _write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def select_incidents(cands: list, max_n: int, min_rows: int = 3) -> list:
    """Deterministic pick from parse_candidates output: the (max_n - 1)
    best-sampled incidents (most snapshots) plus the LARGEST-acreage remaining
    candidate — big fast fires (few snapshots, huge growth) are exactly the
    safety-relevant regime and would never win on row count alone."""
    ok = [c for c in cands if c[2] >= min_rows]
    by_rows = sorted(ok, key=lambda c: (-c[2], c[0]))
    picked = by_rows[:max(0, max_n - 1)]
    rest = [c for c in by_rows[len(picked):]]
    if rest and len(picked) < max_n:
        picked.append(max(rest, key=lambda c: (c[3], c[0])))
    return picked[:max_n]


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    _ensure_dirs(args.data_dir)
    http = CountingHttp(args.http_budget)
    notes: list = []
    fetched: list = []
    try:
        cands = H.parse_candidates(_get_json(
            http, H.candidates_query_url(min_acres=args.min_acres)))
    except Exception as exc:
        notes.append(f"BLOCKED: WFIGS Daily-perimeter discovery query failed "
                     f"({exc}) — no hindcast incidents this run")
        _finish_fetch_log(args, http, notes, fetched)
        print("\n".join(notes))
        return 1
    if args.irwin:
        wanted = {w.strip().lower() for w in args.irwin.split(",")}
        chosen = [c for c in cands if c[0].strip("{}").lower() in wanted
                  or c[0].lower() in wanted]
    else:
        chosen = select_incidents(cands, args.max_incidents)
    if not chosen:
        notes.append("BLOCKED: discovery returned no incident with >= 3 "
                     "snapshots at the acreage floor")
    print(f"[fetch] {len(chosen)} incident(s): "
          + ", ".join(f"{c[1]}({c[2]} rows)" for c in chosen))

    for irwin, name, n_rows, max_acres, _t0, _t1 in chosen:
        slug = _irwin_slug(irwin)
        inc_path = os.path.join(args.data_dir, "incidents", f"{slug}.json")
        try:
            if not os.path.exists(inc_path):
                _fetch_incident(http, irwin, name, max_acres, inc_path)
            else:
                print(f"[fetch] {name}: perimeter cache hit")
            _fetch_weather(http, args, slug, name, inc_path)
            _fetch_terrain(http, args, slug, name, inc_path, notes)
            fetched.append({"irwin": irwin, "name": name, "slug": slug})
        except BudgetExceeded as exc:
            notes.append(f"BLOCKED at {name}: {exc}")
            break
        except Exception as exc:
            notes.append(f"DEGRADED: {name} ({irwin}) skipped — {exc}")
    _finish_fetch_log(args, http, notes, fetched)
    print(f"[fetch] done: {http.n_requests} HTTP requests "
          f"(budget {args.http_budget}); {len(fetched)} incident(s) cached")
    for n in notes:
        print("[fetch]", n)
    return 0


def _snapshot_record(t_s: float, oids: list, geoms: dict, acres_by_oid: dict,
                     fallback_acres: float):
    """One capture time -> its incident-file record under hindcast_lib's
    SNAPSHOT RULE (None when no row under it has geometry)."""
    rings, kept, dropped, acres = H.assemble_snapshot(oids, geoms,
                                                      acres_by_oid)
    if not rings:
        return None
    return {"t_s": t_s, "acres": acres or fallback_acres, "oids": kept,
            "dropped_oids": dropped,
            "rings": [[[p.lat, p.lon] for p in ring] for ring in rings]}


def _fetch_incident(http: CountingHttp, irwin: str, name: str,
                    max_acres: float, inc_path: str) -> None:
    rows = H.parse_series_attrs(_get_json(http, H.series_attrs_url(irwin)))
    picks = H.pick_snapshot_times(rows)
    if len(picks) < 2:
        raise RuntimeError(f"only {len(picks)} usable snapshot(s) after "
                           f"growth/dt gating ({len(rows)} raw rows)")
    all_oids = [oid for _t, oids, _a in picks for oid in oids]
    geoms = H.parse_snapshot_features(
        _get_json(http, H.geometry_query_url(all_oids)))
    acres_by_oid = {oid: ac for oid, _t, ac in rows}
    snaps = [rec for rec in (
        _snapshot_record(t_s, oids, geoms, acres_by_oid, acres)
        for t_s, oids, acres in picks) if rec]
    if len(snaps) < 2:
        raise RuntimeError("geometry pull yielded < 2 snapshots with rings")
    _write_json(inc_path, {"schema": INCIDENT_SCHEMA,
                           "snapshot_rule": SNAPSHOT_RULE, "irwin": irwin,
                           "name": name, "max_acres": max_acres,
                           "snapshots": snaps})
    print(f"[fetch] {name}: {len(snaps)} snapshots "
          f"({len(rows)} raw rows -> growth/dt-gated picks)")


def _load_incident(inc_path: str) -> "H.HindcastIncident":
    d = _read_json(inc_path)
    snaps = [H.PerimeterSnapshot(
        t_s=float(s["t_s"]), acres=float(s.get("acres") or 0.0),
        oids=list(s.get("oids") or []),
        rings=[[LatLon(v[0], v[1]) for v in ring] for ring in s["rings"]])
        for s in d["snapshots"]]
    snaps.sort(key=lambda s: s.t_s)
    # A pre-fix file unioned every same-time row into its snapshot; a file
    # written under the SNAPSHOT RULE lists more than one OBJECTID only for
    # genuinely separate pieces.
    stacked = ([s.t_s for s in snaps if len(s.oids) > 1]
               if d.get("schema") == LEGACY_INCIDENT_SCHEMA else [])
    if stacked:
        print(f"[hindcast] WARNING: {d['name']} ({os.path.basename(inc_path)})"
              f" is a pre-fix {LEGACY_INCIDENT_SCHEMA} file whose "
              f"{len(stacked)} multi-row snapshot(s) stack same-time rows — "
              f"run `hindcast_harness.py repair` before scoring it",
              file=sys.stderr)
    pin = d.get("domain_bbox")
    return H.HindcastIncident(irwin=d["irwin"], name=d["name"],
                              snapshots=snaps,
                              max_acres=float(d.get("max_acres") or 0.0),
                              stacked_legacy=stacked,
                              domain_bbox=tuple(pin) if pin else None)


def _incident_centroid(inc: "H.HindcastIncident") -> LatLon:
    pts = [p for ring in inc.snapshots[0].rings for p in ring]
    return LatLon(sum(p.lat for p in pts) / len(pts),
                  sum(p.lon for p in pts) / len(pts))


def _fetch_weather(http: CountingHttp, args, slug: str, name: str,
                   inc_path: str) -> None:
    wx_path = os.path.join(args.data_dir, "weather", f"{slug}.json")
    if os.path.exists(wx_path):
        print(f"[fetch] {name}: weather cache hit")
        return
    inc = _load_incident(inc_path)
    ctr = _incident_centroid(inc)
    t_a = inc.snapshots[0].t_s - _WX_MARGIN_S
    t_b = inc.snapshots[-1].t_s + _WX_MARGIN_S
    d0 = datetime.fromtimestamp(t_a, tz=timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(t_b, tz=timezone.utc).strftime("%Y-%m-%d")
    payload = _get_json(http, H.archive_weather_url(ctr, d0, d1))
    _write_json(wx_path, {"schema": "hindcast-weather/1",
                          "loc": [ctr.lat, ctr.lon],
                          "start_date": d0, "end_date": d1,
                          "payload": payload})
    print(f"[fetch] {name}: weather {d0}..{d1} at "
          f"({ctr.lat:.3f},{ctr.lon:.3f})")


def _incident_domain(inc: "H.HindcastIncident", cfg: ResponseConfig):
    if inc.domain_bbox:
        # pinned by `repair`: the pre-fix domain, which still holds every
        # rebuilt snapshot with at least the _DOMAIN_PAD_M margin
        bbox = BBox(*inc.domain_bbox)
    else:
        bbox = H.rings_bbox([s.rings for s in inc.snapshots], _DOMAIN_PAD_M)
    return bbox, H.pick_cell_m(bbox, cfg)


def _fetch_terrain(http: CountingHttp, args, slug: str, name: str,
                   inc_path: str, notes: list) -> None:
    inc = _load_incident(inc_path)
    cfg = dataclasses.replace(ResponseConfig(), wind_gust_blend=getattr(args, 'gust_blend', 0.0))
    bbox, cell_m = _incident_domain(inc, cfg)
    # LFPS requires an Email job parameter (live-verified 2026-07-09; the
    # 2026-07-06 research digest predates the requirement). Same ops address
    # the review's smoke_lfps.py used — still keyless/no-signup.
    lfps = LfpsTerrain(cache_dir=os.path.join(args.data_dir, "lfps"),
                       http_get=http, poll_interval_s=20.0, timeout_s=900.0,
                       email="ops@thewildfireguardian.com")
    cache_path = lfps.cache_path(bbox, cell_m)
    if cache_path and os.path.exists(cache_path):
        print(f"[fetch] {name}: LFPS tile cache hit")
        return
    tile = lfps.tile(bbox, cell_m)
    notes.append(f"LFPS tile fetched for {name}: "
                 f"{tile.spec.nrows}x{tile.spec.ncols} @ {cell_m:.0f} m")
    print(f"[fetch] {name}: LFPS tile {tile.spec.nrows}x{tile.spec.ncols} "
          f"@ {cell_m:.0f} m")


def _finish_fetch_log(args, http: CountingHttp, notes: list,
                      fetched: list) -> None:
    log_path = os.path.join(args.data_dir, "fetch_log.json")
    prior = {}
    if os.path.exists(log_path):
        try:
            prior = _read_json(log_path)
        except Exception:
            prior = {}
    entry = {
        "fetched_at_utc": datetime.now(timezone.utc)
        .strftime("%Y-%m-%d %H:%M"),
        "n_requests": http.n_requests + int(prior.get("n_requests") or 0),
        "budget": args.http_budget,
        "notes": (prior.get("notes") or []) + notes,
        "incidents": {f["slug"]: f for f in fetched} |
                     (prior.get("incidents") or {}),
        "requests": (prior.get("requests") or []) + http.log,
    }
    _write_json(log_path, entry)


# ---------------------------------------------------------------------------
# repair (network: re-reads the Daily rows behind a pre-fix incident file)
# ---------------------------------------------------------------------------

class RepairMismatch(RuntimeError):
    """The re-read rows do not reproduce the cached file (rows changed or
    went missing since the fetch): the file is left as it is."""


def _centroid_of(rings: list) -> LatLon:
    pts = [p for ring in rings for p in ring]
    return LatLon(sum(p.lat for p in pts) / len(pts),
                  sum(p.lon for p in pts) / len(pts))


def repair_incident(d: dict, rows: list, geoms: dict,
                    weather_loc: LatLon = None) -> tuple:
    """Rebuild one pre-fix (hindcast-incident/1) incident dict under the
    SNAPSHOT RULE at its CACHED capture times. Pure: `rows` =
    parse_series_attrs of the incident's attrs series, `geoms` =
    parse_snapshot_features of its cached OBJECTIDs; `weather_loc` = where
    its cached weather series was fetched (the pre-fix first-snapshot
    centroid).

    Each snapshot is rebuilt from the rows cached under its time (the rows
    the original fetch saw; later same-time revisions are ignored), so the
    corpus keeps its capture times and pairs and only the geometry and the
    recorded acreage change. Fidelity is checked first: the re-read rings
    of each snapshot's cached rows, concatenated in OBJECTID order, must
    equal the cached rings exactly, else RepairMismatch. When the rebuilt
    snapshots would move the replay domain (bbox of all rings + pad), the
    pre-fix domain is pinned so the cached terrain tile keeps serving; it
    still holds every rebuilt ring with at least the pad.

    Returns (new_dict, summary) — summary lists the rebuilt snapshots."""
    acres_by_oid = {oid: ac for oid, _t, ac in rows}
    old_snaps = sorted(d["snapshots"], key=lambda s: s["t_s"])
    for s in old_snaps:
        reread = []
        for oid in sorted(s.get("oids") or []):
            got = geoms.get(oid)
            if got:
                reread.extend([[p.lat, p.lon] for p in ring]
                              for ring in got[1])
        if reread != s["rings"]:
            raise RepairMismatch(
                f"{d['name']}: re-read rings of OBJECTIDs {s.get('oids')} do "
                f"not reproduce the cached snapshot at t_s={s['t_s']}")
    new_snaps, changed = [], []
    for s in old_snaps:
        oids = list(s.get("oids") or [])
        rec = _snapshot_record(s["t_s"], oids, geoms, acres_by_oid,
                               float(s.get("acres") or 0.0))
        if rec is None:
            raise RepairMismatch(f"{d['name']}: no geometry at t_s={s['t_s']}")
        new_snaps.append(rec)
        if rec["dropped_oids"]:
            changed.append({"t_s": s["t_s"], "kept_oids": rec["oids"],
                            "dropped_oids": rec["dropped_oids"],
                            "acres_before": s.get("acres"),
                            "acres_after": rec["acres"]})

    def _rings(snap):
        return [[LatLon(v[0], v[1]) for v in ring] for ring in snap["rings"]]

    old_bbox = H.rings_bbox([_rings(s) for s in old_snaps], _DOMAIN_PAD_M)
    new_bbox = H.rings_bbox([_rings(s) for s in new_snaps], _DOMAIN_PAD_M)
    out = {k: v for k, v in d.items() if k not in ("schema", "snapshots")}
    out.update({"schema": INCIDENT_SCHEMA, "snapshot_rule": SNAPSHOT_RULE,
                "snapshots": new_snaps})
    pinned = new_bbox != old_bbox
    if pinned:
        out["domain_bbox"] = [old_bbox.west, old_bbox.south, old_bbox.east,
                              old_bbox.north]
    first = _centroid_of(_rings(new_snaps[0]))
    t_now = [t for t, _o, _a in H.pick_snapshot_times(rows)]
    summary = {
        "from_schema": d.get("schema"),
        "capture_times": "kept from the pre-fix fetch; each snapshot rebuilt "
                         "from the rows cached under its time",
        "fidelity": f"re-read rings of all {sum(len(s.get('oids') or []) for s in old_snaps)} "
                    f"cached OBJECTIDs reproduce the cached file exactly",
        "rebuilt_snapshots": changed,
        "domain": ("pinned to the pre-fix domain (the rebuilt rings alone "
                   "would give a different bbox)" if pinned else "unchanged"),
        "weather_centroid_shift_m": (round(geo.haversine_m(weather_loc, first), 1)
                                     if weather_loc else None),
        "fixed_picker_today": {
            "capture_times": t_now,
            "same_as_cached": t_now == [s["t_s"] for s in old_snaps]},
    }
    return out, summary


def cmd_repair(args) -> int:
    """Rebuild every pre-fix incident file in --data-dir under the SNAPSHOT
    RULE (see repair_incident). Files with no multi-row snapshot are only
    re-labelled (nothing was stacked; no network). The others re-read their
    rows: 2 read-only GETs each (attrs series + geometry of the cached
    OBJECTIDs, the same query the fetch used), budget --http-budget. A file
    whose rows no longer reproduce it is left untouched and reported."""
    inc_dir = os.path.join(args.data_dir, "incidents")
    if not os.path.isdir(inc_dir):
        print(f"[repair] no incidents under {args.data_dir}")
        return 0
    http = CountingHttp(args.http_budget)
    rc = 0
    log = []
    for fn in sorted(os.listdir(inc_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(inc_dir, fn)
        d = _read_json(path)
        if d.get("schema") != LEGACY_INCIDENT_SCHEMA:
            print(f"[repair] {d.get('name')}: already {d.get('schema')}")
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        if not any(len(s.get("oids") or []) > 1 for s in d["snapshots"]):
            d.update({"schema": INCIDENT_SCHEMA, "snapshot_rule": SNAPSHOT_RULE,
                      "repair": {"repaired_utc": stamp,
                                 "from_schema": LEGACY_INCIDENT_SCHEMA,
                                 "note": "no capture time had more than one "
                                         "row; geometry unchanged"}})
            for s in d["snapshots"]:
                s.setdefault("dropped_oids", [])
            _write_json(path, d)
            print(f"[repair] {d['name']}: no same-time rows — relabelled")
            log.append({"file": fn, "name": d["name"], "relabelled": True})
            continue
        try:
            rows = H.parse_series_attrs(
                _get_json(http, H.series_attrs_url(d["irwin"])))
            oids = sorted({o for s in d["snapshots"] for o in s["oids"]})
            geoms = H.parse_snapshot_features(
                _get_json(http, H.geometry_query_url(oids)))
            wx_path = os.path.join(args.data_dir, "weather", fn)
            wloc = None
            if os.path.exists(wx_path):
                wd = _read_json(wx_path)
                wloc = LatLon(wd["loc"][0], wd["loc"][1])
            new, summary = repair_incident(d, rows, geoms, wloc)
        except BudgetExceeded as exc:
            print(f"[repair] BLOCKED at {d['name']}: {exc}")
            rc = 1
            break
        except Exception as exc:
            print(f"[repair] {d['name']}: NOT repaired — {exc}")
            log.append({"file": fn, "name": d["name"], "error": str(exc)})
            rc = 1
            continue
        summary["repaired_utc"] = stamp
        new["repair"] = summary
        _write_json(path, new)
        log.append({"file": fn, "name": d["name"], **summary})
        for c in summary["rebuilt_snapshots"]:
            t = datetime.fromtimestamp(c["t_s"], tz=timezone.utc)
            print(f"[repair] {d['name']} {t:%Y-%m-%d %H:%M}: kept "
                  f"{c['kept_oids']} ({c['acres_after']:,.0f} ac), dropped "
                  f"{len(c['dropped_oids'])} same-time row(s) "
                  f"(pre-fix acres {c['acres_before']:,.0f})")
        print(f"[repair] {d['name']}: domain {summary['domain']}; weather "
              f"centroid shift {summary['weather_centroid_shift_m']} m; fixed "
              f"picker today same as cached: "
              f"{summary['fixed_picker_today']['same_as_cached']}")
    _write_json(os.path.join(args.data_dir, "repair_log.json"),
                {"schema": "hindcast-repair/1", "n_requests": http.n_requests,
                 "requests": http.log, "incidents": log})
    print(f"[repair] done: {http.n_requests} HTTP requests "
          f"(budget {args.http_budget})")
    return rc


# ---------------------------------------------------------------------------
# replay (offline: cache only)
# ---------------------------------------------------------------------------

def _load_all_incidents(data_dir: str) -> list:
    inc_dir = os.path.join(data_dir, "incidents")
    if not os.path.isdir(inc_dir):
        return []
    out = []
    for fn in sorted(os.listdir(inc_dir)):
        if fn.endswith(".json"):
            out.append((fn[:-5], _load_incident(os.path.join(inc_dir, fn))))
    return out


def _variant_config_kwargs(args) -> dict:
    """The 7 calibration-sweep ResponseConfig fields the `replay` CLI can
    override (tri-model review 2026-08-31, fix-program item 5) — all 7
    already exist on ResponseConfig (wgresponse/contracts.py); nothing new
    is added there. Shared by cmd_replay and the results-file header so a
    calibration matrix can identify which variant produced a given file."""
    return {
        "wind_gust_blend": args.gust_blend,
        "grass_ros_floor_frac": args.grass_floor,
        "ros_floor_extinction_gate": args.floor_gate,
        "ros_floor_grass_only": args.grass_only,
        "model_error_sd_log": args.model_error,
        "ensemble_p50_nominal_bound": args.p50_bound,
        "spotting_enabled": args.spotting,
        "extreme_regime_enabled": getattr(args, "extreme_regime", False),
        "extreme_regime_floor_frac": getattr(args, "regime_floor", 0.0),
        "edge_weather_piecewise": getattr(args, "edge_piecewise", False),
        "slope_phi_vector": getattr(args, "phi_vector", False),
    }


def cmd_replay(args) -> int:
    os.makedirs(args.data_dir, exist_ok=True)
    cfg_kwargs = _variant_config_kwargs(args)
    cfg = dataclasses.replace(ResponseConfig(), **cfg_kwargs)
    wide_cfg = dataclasses.replace(cfg, **_WIDE_VARIANT)
    out_path = os.path.join(args.data_dir, args.out)
    incidents = _load_all_incidents(args.data_dir)
    if not incidents:
        print("[replay] no cached incidents — run fetch first "
              "(or accept the honest-degradation path: anchors only)")
        _write_json(out_path,
                    {"schema": "hindcast-replay/2", "label": args.label,
                     "config": cfg_kwargs, "pairs": [],
                     "pairs_wide": [], "skips": ["no cached incidents"]})
        return 0
    pairs, pairs_wide, skips = [], [], []
    for slug, inc in incidents:
        if inc.stacked_legacy:
            skips.append(f"{inc.name}: pre-fix incident file stacks same-time "
                         f"rows in {len(inc.stacked_legacy)} snapshot(s) — "
                         f"NOT replayed (run `repair` first)")
            continue
        wx_path = os.path.join(args.data_dir, "weather", f"{slug}.json")
        if not os.path.exists(wx_path):
            skips.append(f"{inc.name}: no cached weather")
            continue
        wd = _read_json(wx_path)
        wx = H.parse_archive_weather(wd["payload"],
                                     LatLon(wd["loc"][0], wd["loc"][1]))
        bbox, cell_m = _incident_domain(inc, cfg)
        lfps = LfpsTerrain(cache_dir=os.path.join(args.data_dir, "lfps"))
        cache_path = lfps.cache_path(bbox, cell_m)
        if not (cache_path and os.path.exists(cache_path)):
            skips.append(f"{inc.name}: no cached LFPS tile")
            continue
        tile = load_ascii_tile(cache_path)
        for a, b in zip(inc.snapshots, inc.snapshots[1:]):
            dt_h = (b.t_s - a.t_s) / 3600.0
            if not (0.0 < dt_h <= cfg.horizon_h - 6.0):
                skips.append(f"{inc.name}: pair dt {dt_h:.1f} h outside "
                             f"(0, horizon-6] — skipped")
                continue
            print(f"[replay] {inc.name}: T0+{dt_h:.1f}h pair "
                  f"({tile.spec.nrows}x{tile.spec.ncols} @ {cell_m:.0f} m)")
            pairs.append(H.replay_pair(tile, wx, a, b, cfg, inc.irwin,
                                       name=inc.name))
            pairs_wide.append(H.replay_pair(tile, wx, a, b, wide_cfg,
                                            inc.irwin, name=inc.name,
                                            label="wide"))
    _write_json(out_path,
                {"schema": "hindcast-replay/2", "label": args.label,
                 "config": cfg_kwargs,
                 "pairs": [dataclasses.asdict(p) for p in pairs],
                 "pairs_wide": [dataclasses.asdict(p) for p in pairs_wide],
                 "skips": skips})
    pooled = H.pool_metrics(pairs)
    print(f"[replay] scored {pooled.get('n_pairs', 0)} pairs / "
          f"{pooled.get('n_cells', 0)} cells; skips: {len(skips)}; "
          f"wrote {out_path}"
          + (f" (label={args.label!r})" if args.label else ""))
    for s in skips:
        print("[replay] skip:", s)
    return 0


# ---------------------------------------------------------------------------
# anchors / report
# ---------------------------------------------------------------------------

def cmd_anchors(args) -> int:
    os.makedirs(args.data_dir, exist_ok=True)
    results = H.run_all_anchors(ResponseConfig())
    _write_json(os.path.join(args.data_dir, "anchor_results.json"),
                {"schema": "hindcast-anchors/1",
                 "anchors": [dataclasses.asdict(a) for a in results]})
    for a in results:
        print(f"[anchor] {a.anchor:5s} {a.fuel_label:12s} "
              f"spotting={'ON ' if a.spotting else 'OFF'} engine="
              f"{a.engine:10.2f} observed={a.observed:10.2f} "
              f"ratio={a.ratio:.2f}x")
    return 0


def _load_pairs(d: dict, key: str) -> list:
    return [H.PairMetrics(**p) for p in d.get(key) or []]


def _wx_note(data_dir: str) -> str:
    """Measured parenthetical about the replay weather: the max 10-m wind
    across every cached incident series ('' when no weather is cached)."""
    wx_dir = os.path.join(data_dir, "weather")
    if not os.path.isdir(wx_dir):
        return ""
    peak = None
    for fn in sorted(os.listdir(wx_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            wd = _read_json(os.path.join(wx_dir, fn))
            wx = H.parse_archive_weather(wd["payload"],
                                         LatLon(wd["loc"][0], wd["loc"][1]))
        except Exception:
            continue
        top = max(s.wind_speed_mps for s in wx.samples)
        peak = top if peak is None else max(peak, top)
    if peak is None:
        return ""
    return (f" (max sustained 10-m wind across every cached series here: "
            f"{peak:.1f} m/s)")


def cmd_report(args) -> int:
    data_dir = args.data_dir
    replay_path = os.path.join(data_dir, args.results)
    anchors_path = os.path.join(data_dir, "anchor_results.json")
    rd = _read_json(replay_path) if os.path.exists(replay_path) else {}
    ad = _read_json(anchors_path) if os.path.exists(anchors_path) else {}
    pairs = _load_pairs(rd, "pairs")
    pairs_wide = _load_pairs(rd, "pairs_wide")
    anchors = [H.AnchorResult(**a) for a in ad.get("anchors") or []]
    incidents = [inc for _slug, inc in _load_all_incidents(data_dir)]
    log_path = os.path.join(data_dir, "fetch_log.json")
    fetch_summary = _read_json(log_path) if os.path.exists(log_path) else {}
    fetch_summary = dict(fetch_summary)
    fetch_summary.pop("requests", None)     # provenance detail stays on disk
    for s in rd.get("skips") or []:
        fetch_summary.setdefault("notes", []).append(f"replay skip: {s}")
    wx_note = _wx_note(data_dir)
    text = H.render_report(incidents, pairs, pairs_wide, anchors,
                           fetch_summary, ResponseConfig(), wx_note=wx_note,
                           variant_label=rd.get("label", ""),
                           variant_config=rd.get("config"))
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"[report] wrote {args.report} ({len(text.splitlines())} lines) "
          f"from {replay_path}")
    return 0


def cmd_research(args) -> int:
    http = CountingHttp(args.http_budget)
    cands = H.parse_candidates(_get_json(
        http, H.candidates_query_url(min_acres=args.min_acres)))
    print(f"{'rows':>5} {'max_ac':>9}  irwin / name")
    for c in cands:
        print(f"{c[2]:>5} {c[3]:>9.0f}  {c[0]} / {c[1]}")
    print(f"[research] would select: "
          + ", ".join(c[1] for c in
                      select_incidents(cands, args.max_incidents)))
    return 0


def cmd_all(args) -> int:
    rc = 0
    if not args.offline:
        rc = cmd_fetch(args)
    cmd_replay(args)
    cmd_anchors(args)
    cmd_report(args)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="hindcast_harness",
        description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("all", "fetch", "replay", "anchors",
                                        "report", "research", "repair"))
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--report", default=DEFAULT_REPORT)
    ap.add_argument("--max-incidents", type=int, default=4)
    ap.add_argument("--min-acres", type=float, default=5000.0)
    ap.add_argument("--http-budget", type=int, default=45,
                    help="hard cap on live GETs for this invocation")
    ap.add_argument("--gust-blend", type=float, default=0.0,
                    help="cfg.wind_gust_blend for the replay (calibration sweep)")
    ap.add_argument("--grass-floor", type=float, default=0.0,
                    help="cfg.grass_ros_floor_frac for the replay (calibration sweep)")
    ap.add_argument("--floor-gate", action="store_true",
                    help="cfg.ros_floor_extinction_gate=True (moisture-of-"
                         "extinction gate on the grass ROS floor)")
    ap.add_argument("--grass-only", action="store_true",
                    help="cfg.ros_floor_grass_only=True (restrict the ROS "
                         "floor to GR/GS, no TU/TL lift)")
    ap.add_argument("--model-error", type=float, default=0.0,
                    help="cfg.model_error_sd_log for the replay (ensemble "
                         "model-error term, tri-model review finding A7)")
    ap.add_argument("--p50-bound", action="store_true",
                    help="cfg.ensemble_p50_nominal_bound=True (ladder p50 "
                         "never trails the nominal member)")
    ap.add_argument("--spotting", action="store_true",
                    help="cfg.spotting_enabled=True for the replay")
    ap.add_argument("--extreme-regime", action="store_true",
                    help="cfg.extreme_regime_enabled=True: per-pair regime "
                         "detection (threat._extreme_regime_now) on the "
                         "replay tile + weather")
    ap.add_argument("--regime-floor", type=float, default=0.0,
                    help="cfg.extreme_regime_floor_frac: blow-up floor on "
                         "every floored class for regime pairs (needs "
                         "--extreme-regime)")
    ap.add_argument("--edge-piecewise", action="store_true",
                    help="cfg.edge_weather_piecewise=True (hour-crossing "
                         "traversals integrate each hour's rate)")
    ap.add_argument("--phi-vector", action="store_true",
                    help="cfg.slope_phi_vector=True (phi-factor vector "
                         "slope/wind combination)")
    ap.add_argument("--out", default="replay_results.json",
                    help="`replay` results filename inside --data-dir "
                         "(default replay_results.json; pair with --label "
                         "to run a calibration-matrix variant without "
                         "clobbering the default file)")
    ap.add_argument("--label", default="",
                    help="free-text config-variant name stored in the "
                         "`replay` results file header (e.g. 'grass_floor')")
    ap.add_argument("--results", default="replay_results.json",
                    help="`report` reads this results filename inside "
                         "--data-dir (point at a --out variant file to "
                         "render it instead of the default)")
    ap.add_argument("--irwin", default="",
                    help="comma-separated IRWIN ids to fetch (override "
                         "automatic selection)")
    ap.add_argument("--offline", action="store_true",
                    help="never touch the network (cache only)")
    args = ap.parse_args(argv)
    return {"all": cmd_all, "fetch": cmd_fetch, "replay": cmd_replay,
            "anchors": cmd_anchors, "report": cmd_report,
            "research": cmd_research,
            "repair": cmd_repair}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
