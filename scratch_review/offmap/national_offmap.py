"""The per-fire off-domain horizon over the recorded national snapshot
(SAFETY SEMANTICS 15, 2026-09-27). NOT product code — the P90-vs-max evidence
and the status census of docs/LIVE_WORLD_PLAN.md "The off-domain horizon is
per fire (2026-09-27)".

One cycle of the recorded CONUS payloads (2026-09-27 15:55Z; the incident-
points body carries the behaviour fields of the same record revisions, see
behavior_snapshot.py) through the REAL adapters, fusion, ThreatMonitor and
alert engine with the production flags + --drop-contained
--confirmed-perimeters + the rule, the W6 demo roster (mock-presets, 520
homes), cached NWS forecasts and the W6 tile cache. Reported:

  * every incident on the map by behaviour status (known slow / rapid /
    unknown: no report, stale, unrecognized) — who the rule can touch;
  * every domain: its front statistics (P90, max, P50, count), the speed
    and kind the rule picks, and the veto reach for a pass one hour from now
    under the legacy law (1.7 m/s, 56-min chord margin) and under the rule.

    py -3 scratch_review/offmap/national_offmap.py

Results: scratch_review/offmap/national_offmap.json.
"""
import dataclasses
import json
import os
import sys
import time
from collections import Counter
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "scratch_review", "contained"))
sys.path.insert(0, HERE)

import feedsnap  # noqa: E402
from ab_national import NEW, PROD, CachingHttp  # noqa: E402
from willow_offmap import PER_FIRE, AugReplayHttp, reach_km  # noqa: E402
from wgresponse import cli, feeds, offmap, safety, weather  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

KMH = 3.6
OUT = os.path.join(HERE, "national_offmap.json")


def main():
    aug = AugReplayHttp()
    now = float(aug.manifest["recorded_at_s"])
    aoi, zones, _ = cli._parse_aoi(os.path.join(WT, "data", "aoi_conus.json"))
    registry, _ = cli._watch_registry(
        SimpleNamespace(registry="mock-presets", preset_homes=40), aoi)
    cfg = dataclasses.replace(ResponseConfig(), **PROD, **NEW, **PER_FIRE)
    fe = [feeds.WfigsFeed(http_get=aug),
          feeds.FirisFeed(http_get=feedsnap.ReplayHttp()),
          feeds.CalFireFeed(http_get=feedsnap.ReplayHttp())]
    mon = ThreatMonitor(
        feeds=fe, weather_provider=weather.NwsWeather(
            http_get=CachingHttp(os.path.join(feedsnap.DATA, "nws_cache")),
            cfg=cfg),
        terrain_provider=LfpsTerrain(
            cache_dir=os.path.join(feedsnap.DATA, "ab_terrain"),
            email=os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"),
        registry=registry, alert_engine=AlertEngine(cfg), cfg=cfg,
        safe_zones=zones, crew_tracker=None, dispatcher=None, aoi=aoi)
    t0 = time.monotonic()
    res = mon.cycle(now)
    wall = time.monotonic() - t0
    print(f"cycle {wall:.1f} s; incidents {len(res.incidents)}, removed "
          f"{len(res.removed_incidents)}, domains {len(res.ensembles)}")

    census = Counter()
    by_reason = Counter()
    rapid_names = []
    for inc in res.incidents:
        known, rapid, reasons = offmap.status_for(inc, now, cfg)
        k = ("rapid" if rapid else "known slow" if known else "unknown")
        census[k] += 1
        if not known:
            r = reasons[0]
            by_reason[("no report" if r.startswith("no fire-behaviour") else
                       "stale" if "stale beyond" in r else
                       "unrecognized term" if "outside the known" in r else
                       "no report time")] += 1
        if rapid:
            rapid_names.append((inc.name, inc.containment_pct,
                                offmap.behavior_label(
                                    *offmap.behavior_report(inc)[:2])))
    print("status census:", dict(census), "unknown by reason:", dict(by_reason))
    print("rapid:", rapid_names)

    domains = []
    names = {i.incident_id: i for i in res.incidents}
    for iid, ens in sorted(res.ensembles.items()):
        inc = names[iid]
        h = res.offmap[iid]
        speeds = sorted(offmap.front_speeds_mps(ens.p10))
        er = safety._erode(ens.p10, cfg.corridor_erosion_m, shape="chebyshev")
        runs = safety._boundary_runs(er)
        t_b = min(r[0] for r in runs) if runs else None
        cell = ens.spec.cell_m
        p50 = offmap.percentile_nearest_rank(speeds, 0.5)
        p95 = offmap.percentile_nearest_rank(speeds, 0.95)
        row = {
            "incident_id": iid, "name": inc.name,
            "containment_pct": inc.containment_pct,
            "behavior": offmap.behavior_label(*offmap.behavior_report(inc)[:2]),
            "kind": h.kind, "reason": h.reason,
            "speed_kmh": round(h.ros_mps * KMH, 2),
            "front_cells": len(speeds),
            "front_p50_kmh": None if p50 is None else round(p50 * KMH, 2),
            "front_p90_kmh": (None if h.front_p90_mps is None
                              else round(h.front_p90_mps * KMH, 2)),
            "front_p95_kmh": None if p95 is None else round(p95 * KMH, 2),
            "front_max_kmh": (None if h.front_max_mps is None
                              else round(h.front_max_mps * KMH, 1)),
            "max_over_ceiling": (h.front_max_mps is not None
                                 and h.front_max_mps > cfg.corridor_offraster_ros_mps),
            "edge_reached_h_rel_now": (None if t_b is None
                                       else round((t_b - now) / 3600.0, 1)),
            "reach_km_legacy_pass_plus_1h": (
                0.0 if t_b is None else
                round(reach_km(t_b, now + 3600.0, cfg.route_margin_min, 1.7,
                               cell), 1)),
            "reach_km_rule_pass_plus_1h": (
                0.0 if t_b is None else
                round(reach_km(t_b, now + 3600.0,
                               max(cfg.route_margin_min, h.margin_min),
                               h.ros_mps, cell), 1)),
        }
        domains.append(row)
        print(" ", row["name"][:22].ljust(22), row["kind"].ljust(13),
              "speed", row["speed_kmh"], "p50/p90/p95/max",
              row["front_p50_kmh"], row["front_p90_kmh"],
              row["front_p95_kmh"], row["front_max_kmh"], "n",
              row["front_cells"], "edge", row["edge_reached_h_rel_now"],
              "R legacy/rule", row["reach_km_legacy_pass_plus_1h"],
              row["reach_km_rule_pass_plus_1h"])
    out = {"snapshot_utc": aug.manifest["recorded_utc"], "now_s": now,
           "cycle_wall_s": round(wall, 1),
           "behavior_augment": aug.aug_counts,
           "incidents_on_map": len(res.incidents),
           "removed_contained": len(res.removed_incidents),
           "status_census": dict(census),
           "unknown_by_reason": dict(by_reason),
           "rapid": rapid_names, "domains": domains}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
