"""Memo / reference parity and cost of the WEATHER ZONES on REAL national
domains (SAFETY SEMANTICS 17, 2026-09-27). NOT product code — measurement D
(and the spread half of C) of docs/LIVE_WORLD_PLAN.md "Spread reads the
local weather forecast (2026-09-27)".

Runs the recorded national cycle ONCE with --weather-grid-km G (the NWS
forecasts replay from out/wxfield/nws_cache — run national_wxfield.py
first), capturing the exact spread.ensemble_arrival arguments of the picked
domains. For each, the NOMINAL member (compute_arrival, the production
flags) is solved four ways:

  legacy_memo   no field, the ROS memo           (today's live path)
  zoned_memo    the real field, the ROS memo     (the rule as built)
  zoned_ref     the real field, WGRESP_NO_ROS_MEMO=1 (the reference path)
  legacy_ref    no field, the reference path

and asserts zoned_memo == zoned_ref and legacy_memo == legacy_ref bit for
bit, reporting each solve's wall time and the ROS-memo sizes (Rothermel
evaluations saved) with and without zones.

    py -3 scratch_review/wxfield/real_memo_parity.py [--grid-km 10] [--picks id,id]

Results: scratch_review/wxfield/real_memo_parity.json.
"""
import argparse
import dataclasses
import json
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
from ab_national import NEW, PROD  # noqa: E402
from national_wxfield import NWS_CACHE, Clock, DiskNws, make_monitor  # noqa: E402
from wgresponse import cli, rothermel, spread, weather  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402

OUT = os.path.join(HERE, "real_memo_parity.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-km", type=float, default=10.0)
    ap.add_argument("--points-max", type=int, default=16)
    ap.add_argument("--picks", default="")
    args = ap.parse_args()
    manifest = feedsnap.ReplayHttp().manifest
    now = float(manifest["recorded_at_s"])
    aoi, zones, _ = cli._parse_aoi(os.path.join(WT, "data", "aoi_conus.json"))
    registry, _ = cli._watch_registry(
        SimpleNamespace(registry="mock-presets", preset_homes=40), aoi)
    cfg = dataclasses.replace(ResponseConfig(), **PROD, **NEW,
                              weather_grid_km=args.grid_km,
                              weather_points_max=args.points_max)
    http = DiskNws(NWS_CACHE)
    mon = make_monitor(cfg, http, weather.NwsForecastCache(clock=Clock()),
                       registry, aoi, zones)
    captured = {}
    orig = spread.ensemble_arrival

    def capture(tile, seeds, wxf, t0, scfg, **kw):
        captured[kw.get("incident_id")] = (tile, list(seeds), wxf, t0, scfg,
                                           dict(kw))
        return orig(tile, seeds, wxf, t0, scfg, **kw)

    spread.ensemble_arrival = capture
    try:
        res = mon.cycle(now)
    finally:
        spread.ensemble_arrival = orig
    names = {i.incident_id: i.name for i in res.incidents}
    sizes = sorted(((t.spec.nrows * t.spec.ncols * t.spec.cell_m ** 2, iid)
                    for iid, (t, *_r) in captured.items()), reverse=True)
    picks = [p for p in args.picks.split(",") if p] or [iid for _a, iid in
                                                        sizes[:2]]
    out = {"now_s": now, "grid_km": args.grid_km, "domains": []}
    for iid in picks:
        tile, seeds, wxf, t0, scfg, kw = captured[iid]
        field = kw.get("weather_field")
        base = dict(perimeter=kw.get("perimeter"),
                    perimeter_at_s=kw.get("perimeter_at_s"),
                    incident_id=iid, member_k=0)
        row = {"incident_id": iid, "name": names.get(iid),
               "cells": tile.spec.nrows * tile.spec.ncols,
               "zones": len(field.forecasts) if field else 1, "solves": {}}
        rasters = {}
        for label, fld, memo_off in (("legacy_memo", None, False),
                                     ("zoned_memo", field, False),
                                     ("zoned_ref", field, True),
                                     ("legacy_ref", None, True)):
            if memo_off:
                os.environ["WGRESP_NO_ROS_MEMO"] = "1"
            else:
                os.environ.pop("WGRESP_NO_ROS_MEMO", None)
            calls = {"full": 0}
            orig_ros = rothermel.surface_ros_from_bed
            orig_phi = rothermel.surface_ros_from_bed_phi

            def cnt(*a, _o=orig_ros, **k):
                calls["full"] += 1
                return _o(*a, **k)

            def cnt_phi(*a, _o=orig_phi, **k):
                calls["full"] += 1
                return _o(*a, **k)

            rothermel.surface_ros_from_bed = cnt
            rothermel.surface_ros_from_bed_phi = cnt_phi
            t = time.monotonic()
            try:
                extra = {} if fld is None else {"weather_field": fld}
                r = spread.compute_arrival(tile, seeds, wxf, t0, scfg,
                                           **base, **extra)
            finally:
                rothermel.surface_ros_from_bed = orig_ros
                rothermel.surface_ros_from_bed_phi = orig_phi
                os.environ.pop("WGRESP_NO_ROS_MEMO", None)
            rasters[label] = r
            row["solves"][label] = {
                "wall_s": round(time.monotonic() - t, 2),
                "rothermel_evals": calls["full"],
                "reached": sum(1 for v in r.data if v != float("inf"))}
            print(names.get(iid), label, row["solves"][label], flush=True)
        row["zoned_memo_eq_ref"] = rasters["zoned_memo"].data == rasters["zoned_ref"].data
        row["legacy_memo_eq_ref"] = rasters["legacy_memo"].data == rasters["legacy_ref"].data
        row["zoned_differs_from_legacy"] = (rasters["zoned_memo"].data
                                            != rasters["legacy_memo"].data)
        assert row["zoned_memo_eq_ref"] and row["legacy_memo_eq_ref"]
        out["domains"].append(row)
        print(row, flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("results:", OUT)


if __name__ == "__main__":
    main()
