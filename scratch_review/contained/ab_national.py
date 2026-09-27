"""A/B of the SAFETY SEMANTICS 13 knobs on ONE recorded national snapshot
(2026-09-27). NOT product code — a measurement harness.

Replays the recorded CONUS feed payloads (feedsnap.py record) through the
REAL adapters, fusion, ThreatMonitor, AlertEngine and planner, with the
W6 demo roster (--registry mock-presets, 40 homes x 13 presets), live NWS
weather cached to disk (so both variants see the SAME forecasts) and the
LANDFIRE tile cache copied from the W6 run. Variant A = the production flags
(legacy); variant B = A + drop_contained_incidents + perimeter_confirmed_
anchor. Each variant runs CYCLES cycles 5 minutes apart at the snapshot
time (the ladder needs >1 cycle to reach IMPACTED / WATCH).

Measured per variant: incidents on the map, removed as contained, domains,
clients by worst ladder state, the Chileno / Willow cases, cycle phase
times, and the fail-closed corridor VETO REACH of every domain: the
distance beyond its domain edge within which an off-raster corridor sample
passed NOW is refused (safety._bound_arrival_s inside the 56-min chord
margin):  R = max(0, (now + margin - t_b) * 1.7 m/s / 0.99 + 0.71 cell)
with t_b the earliest reached boundary arrival of the eroded P10.

    py -3 scratch_review/contained/ab_national.py [--cycles 3] [--options] [--only A|B|C|D|E]

(record the payloads first: py -3 scratch_review/contained/feedsnap.py record;
every data file lives in the gitignored out/contained/)
"""
import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, HERE)

import feedsnap  # noqa: E402
from wgresponse import cli, dispatch, feeds, geo, safety, weather  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

PROD = dict(wind_gust_blend=0.5, grass_ros_floor_frac=0.10,
            ros_floor_extinction_gate=True, model_error_sd_log=0.71,
            extreme_regime_enabled=True, corridor_fail_closed=True,
            corridor_unreached_bound=True, ember_threat_radius_m=200.0,
            ensemble_p50_nominal_bound=True, solver_budget_evals=20000,
            slope_phi_vector=True)
NEW = dict(drop_contained_incidents=True, perimeter_confirmed_anchor=True)
CHILENO = "df96eda9-9b27-47fb-86d5-dfb97a879a22"
WILLOW = "548a8d28-1a04-48ee-9011-aba023175d43"
RANK = {"none": 0, "cleared": 1, "notice_72h": 2, "watch": 3,
        "extreme_24h": 4, "impacted": 5}


class CachingHttp:
    """http_get with a disk cache keyed on the URL (NWS forecasts): the
    first variant fetches live, every later call replays the same bytes."""

    def __init__(self, cache_dir):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.hits = self.misses = 0

    def __call__(self, url, headers=None):
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        path = os.path.join(self.dir, h + ".json")
        if os.path.exists(path):
            self.hits += 1
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d["status"], d["body"].encode("utf-8")
        self.misses += 1
        status, body = weather._default_http_get(url, headers)
        if status == 200:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "status": status,
                           "body": body.decode("utf-8")}, f)
        return status, body


def veto_reach(ens, cfg, now_s):
    """(t_b - now in h, R in km) for one ensemble's eroded P10 (docstring)."""
    eroded = safety._erode(ens.p10, cfg.corridor_erosion_m, shape="chebyshev")
    runs = safety._boundary_runs(eroded)
    if not runs:
        return None, 0.0
    t_b = min(r[0] for r in runs)
    margin_s = cfg.route_margin_min * 60.0
    r_m = ((now_s + margin_s - t_b) * cfg.corridor_offraster_ros_mps
           / safety._FLAT_EARTH_SLACK + 0.71 * ens.spec.cell_m)
    return (t_b - now_s) / 3600.0, max(0.0, r_m) / 1000.0


def burned_km2(ens, now_s):
    """Area of the P50 raster already burned at now (km^2)."""
    n = sum(1 for v in ens.p50.data if v <= now_s)
    return n * (ens.spec.cell_m ** 2) / 1e6


class _ScaledConfirmation:
    """REPORT-ONLY containment-scaling options c / d: wraps a feed and moves
    each record's confirming report time (meta reported_at_s) toward now —
    option c: the free run since the report scaled by (1 - containment);
    option d: to now (a confirmed contained polygon IS the fire at now).
    Only records with a known containment are touched; the anchor's own
    same-size / newer-polygon tests still decide whether it applies."""

    def __init__(self, feed, mode):
        self._feed = feed
        self._mode = mode
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        out = self._feed.fetch_active(bbox)
        self.last_fetch_ok = self._feed.last_fetch_ok
        now = self.now_s
        for inc in out:
            rep_at = (inc.meta or {}).get("reported_at_s")
            c = inc.containment_pct
            if rep_at is None or c is None or rep_at >= now:
                continue
            frac = max(0.0, min(1.0, 1.0 - c / 100.0))
            inc.meta["reported_at_s"] = (now if self._mode == "d" else
                                         now - frac * (now - rep_at))
        return out


def run_variant(label, cfg, aoi, zones, registry, now0, cycles, wx_http,
                terrain_dir, pre_growth_h=None, scale_mode=None):
    fe = [feeds.WfigsFeed(http_get=feedsnap.ReplayHttp()),
          feeds.FirisFeed(http_get=feedsnap.ReplayHttp()),
          feeds.CalFireFeed(http_get=feedsnap.ReplayHttp())]
    if scale_mode:
        fe[0] = _ScaledConfirmation(fe[0], scale_mode)
        fe[0].now_s = now0
    engine = AlertEngine(cfg)
    mon = ThreatMonitor(feeds=fe, weather_provider=weather.NwsWeather(
                            http_get=wx_http, cfg=cfg),
                        terrain_provider=LfpsTerrain(cache_dir=terrain_dir, email=os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"),
                        registry=registry, alert_engine=engine, cfg=cfg,
                        safe_zones=zones, crew_tracker=None,
                        dispatcher=dispatch, aoi=aoi)
    if pre_growth_h is not None:
        # REPORT-ONLY option b (SAFETY SEMANTICS 8 WATCH ceiling): simulate a
        # daemon that has already tracked pre_growth_h hours of NO acreage
        # growth for every incident (the gate needs containment_no_growth_h
        # of tracked history before it can cap).
        incs, _ok = mon._fetch_and_fuse(now0, [], [])
        engine.restore({}, incident_growth={
            i.incident_id: {"acreage": i.acreage,
                            "last_growth_s": now0 - pre_growth_h * 3600.0}
            for i in incs})
    out = {"label": label, "cycles": []}
    res = None
    for k in range(cycles):
        now = now0 + k * 300.0
        if scale_mode:
            fe[0].now_s = now
        t0 = time.monotonic()
        res = mon.cycle(now)
        wall = time.monotonic() - t0
        ph = {kk: round(v, 2) for kk, v in (res.timings or {}).items()
              if isinstance(v, (int, float))}
        terrain_s = sum(float(v.get("terrain_s", 0.0)) for v in
                        (res.timings.get("incidents") or {}).values())
        spread_s = sum(float(v.get("spread_s", 0.0)) for v in
                       (res.timings.get("incidents") or {}).values())
        out["cycles"].append({"k": k, "now_s": now, "wall_s": round(wall, 1),
                              "phases_s": ph, "terrain_s": round(terrain_s, 1),
                              "spread_s": round(spread_s, 1),
                              "incidents": len(res.incidents),
                              "removed": len(res.removed_incidents),
                              "domains": len(res.ensembles),
                              "events": len(res.events),
                              "degraded": res.degraded})
        print(f"[{label}] cycle {k}: {wall:.1f}s incidents={len(res.incidents)} "
              f"removed={len(res.removed_incidents)} domains={len(res.ensembles)} "
              f"events={len(res.events)} degraded={res.degraded}", flush=True)
    now = res.t_s
    states = engine.states()
    worst = {}
    per_inc = {}
    for (cid, iid), rec in states.items():
        sv = rec.state.value
        if RANK[sv] > RANK.get(worst.get(cid, "none"), 0):
            worst[cid] = sv
        per_inc.setdefault(iid, {}).setdefault(sv, 0)
        per_inc[iid][sv] += 1
    roster = registry.protected_clients(aoi)
    by_state = {}
    for c in roster:
        s = worst.get(c.client_id, "none")
        by_state[s] = by_state.get(s, 0) + 1
    domains = {}
    for iid, ens in sorted(res.ensembles.items()):
        inc = next(i for i in res.incidents if i.incident_id == iid)
        tb_h, reach_km = veto_reach(ens, cfg, now)
        tm = (res.timings.get("incidents") or {}).get(iid) or {}
        domains[iid] = {"name": inc.name, "acreage": inc.acreage,
                        "containment_pct": inc.containment_pct,
                        "clients": tm.get("clients"), "cells": tm.get("cells"),
                        "cell_m": ens.spec.cell_m,
                        "burned_p50_km2_at_now": round(burned_km2(ens, now), 1),
                        "boundary_reached_h_rel_now": (None if tb_h is None
                                                       else round(tb_h, 1)),
                        "veto_reach_km": round(reach_km, 1),
                        "pair_states": per_inc.get(iid, {})}
    notes_by = {}
    for n in res.notes:
        if n.startswith("incident "):
            head = n[len("incident "):].split(": ", 1)[0]
            if head in (CHILENO, WILLOW):
                notes_by.setdefault(head, []).append(n)
    removed = [{"incident_id": r.incident.incident_id, "name": r.incident.name,
                "acreage": r.incident.acreage,
                "containment_pct": r.incident.containment_pct,
                "reason": r.reason} for r in res.removed_incidents]
    out.update({
        "now_s": now,
        "incidents_on_map": len(res.incidents),
        "removed_contained": len(res.removed_incidents),
        "removed": removed,
        "domains": domains,
        "clients_by_worst_state": dict(sorted(by_state.items())),
        "pairs_by_state": {s: sum(1 for r in states.values() if r.state.value == s)
                           for s in sorted(RANK)},
        "chileno": {"on_map": any(i.incident_id == CHILENO for i in res.incidents),
                    "removed": any(r["incident_id"] == CHILENO for r in removed),
                    "domain": domains.get(CHILENO),
                    "pair_states": per_inc.get(CHILENO, {}),
                    "notes": notes_by.get(CHILENO, [])},
        "willow": {"on_map": any(i.incident_id == WILLOW for i in res.incidents),
                   "domain": domains.get(WILLOW),
                   "pair_states": per_inc.get(WILLOW, {}),
                   "notes": notes_by.get(WILLOW, [])},
        "anchor_notes": [n for n in res.notes if "SAFETY SEMANTICS 13e" in n
                         or "SAFETY SEMANTICS 13f" in n],
        "stale_note_count": next((int(n.split()[1]) for n in res.notes
                                  if n.startswith("excluded ")), 0),
        "degraded": res.degraded,
        "operator_events": [(e.kind, e.incident_id, e.message[:200])
                            for e in res.operator_events],
        "failure_notes": [n[:300] for n in res.notes
                          if "skipped (" in n or "failed" in n
                          or "unhealthy" in n or "missing from" in n],
        "unserved": (len(res.plan.unserved) if res.plan else None),
        "unserved_reasons": (
            {r: sum(1 for u in res.plan.unserved if u.reason == r)
             for r in sorted({u.reason for u in res.plan.unserved})}
            if res.plan else None),
    })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--options", action="store_true",
                    help="also run the report-only containment-gate variants")
    ap.add_argument("--terrain", default=os.path.join(feedsnap.DATA, "ab_terrain"))
    ap.add_argument("--out", default=os.path.join(feedsnap.DATA, "ab_results.json"))
    args = ap.parse_args()
    manifest = feedsnap.ReplayHttp().manifest
    now0 = float(manifest["recorded_at_s"])
    aoi, zones, _desc = cli._parse_aoi(os.path.join(WT, "data", "aoi_conus.json"))
    registry, rdesc = cli._watch_registry(
        SimpleNamespace(registry="mock-presets", preset_homes=40), aoi)
    print("registry:", rdesc, flush=True)
    wx_http = CachingHttp(os.path.join(feedsnap.DATA, "nws_cache"))
    results = {"snapshot_utc": manifest["recorded_utc"], "now0_s": now0,
               "variants": {}}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            results = json.load(f)
    base = dataclasses.replace(ResponseConfig(), **PROD)
    variants = [("A_legacy", base, None),
                ("B_contained_anchor", dataclasses.replace(base, **NEW), None)]
    if args.options:
        # REPORT-ONLY containment-scaling option b: the existing SEMANTICS 8
        # WATCH ceiling on a daemon with 49 h of tracked no-growth history
        gate = dict(containment_gate_enabled=True)
        variants += [
            ("C_gate90_legacy", dataclasses.replace(base, **gate), 49.0),
            ("C_gate80_legacy", dataclasses.replace(
                base, containment_watch_ceiling_pct=80.0, **gate), 49.0)]
    both = dataclasses.replace(base, **NEW)
    scaled = {"D_optc_scaled_freerun": "c", "E_optd_polygon_is_fire": "d"}
    if args.options:
        variants += [(k, both, None) for k in scaled]
    for label, cfg, pre_growth_h in variants:
        if args.only and not label.startswith(args.only):
            continue
        t0 = time.monotonic()
        r = run_variant(label, cfg, aoi, zones, registry, now0, args.cycles,
                        wx_http, args.terrain, pre_growth_h=pre_growth_h,
                        scale_mode=scaled.get(label))
        r["variant_wall_s"] = round(time.monotonic() - t0, 1)
        results["variants"][label] = r
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)
        print(f"[{label}] done in {r['variant_wall_s']} s; nws hits "
              f"{wx_http.hits} misses {wx_http.misses}", flush=True)


if __name__ == "__main__":
    main()
