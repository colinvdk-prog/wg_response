"""Live-watcher cost probe (founder 2026-10-14: "put crown fire on live
watcher. perform the spread-direction fix"): ONE real watch cycle (live feeds,
live LANDFIRE terrain via LFPS, NWS weather, the mock-presets registry, dry
notify) over a Colorado box holding the two largest live Colorado fires
(Willow, White Hill), with the deployed StartCommand flags plus VARIANT's
extra flags; per-incident terrain / weather / spread seconds and the cycle's
wall time. NOT product code.

Usage: py -3 scratch_review/watch_cost_probe.py VARIANT
       VARIANT: deployed | crown | crown_aa | aa
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import logging  # noqa: E402

from wgresponse import cli  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
import faulthandler  # noqa: E402
faulthandler.dump_traceback_later(120, repeat=True)

DEPLOYED = ("--registry mock-presets --dry-notify --gust-blend 0.5 --grass-floor 0.10 "
            "--floor-gate --model-error 0.71 --extreme-regime --fail-closed "
            "--ember-radius 200 --p50-bound --solver-evals 20000 --phi-vector "
            "--drop-contained --confirmed-perimeters --offmap-per-fire --fire-belief "
            "--feed-sanity --fire-belief-v2").split()
EXTRA = {"deployed": [], "crown": ["--crown-fire"], "crown_aa": ["--crown-fire", "--any-angle"],
         "aa": ["--any-angle"]}
variant = sys.argv[1]
out_dir = os.path.join(ROOT, "out", "watch_cost")
os.makedirs(out_dir, exist_ok=True)
zones = json.load(open(os.path.join(ROOT, "data", "aoi_conus.json"), encoding="utf-8"))
aoi = {"aoi": [-107.6, 38.95, -106.1, 39.95],
       "safe_zones": [s for s in zones["safe_zones"]
                      if -108.0 <= s["lon"] <= -105.9 and 38.9 <= s["lat"] <= 40.0]}
aoi_path = os.path.join(out_dir, "aoi_colorado.json")
with open(aoi_path, "w", encoding="utf-8") as fh:
    json.dump(aoi, fh)
state_dir = os.path.join(out_dir, f"state_{variant}")
result = {}


def one_cycle(self, max_cycles=0):
    t0 = time.monotonic()
    res = self.run_cycle()
    tm = (res.timings or {})
    result.update({"variant": variant, "cycle_s": round(time.monotonic() - t0, 1),
                   "incidents": {iid: {k: (round(v, 2) if isinstance(v, float) else v)
                                       for k, v in d.items()}
                                 for iid, d in (tm.get("incidents") or {}).items()},
                   "names": {i.incident_id: getattr(i, "name", "") for i in res.incidents}})
    return 1


cli._WatchDaemon.run = one_cycle
os.environ.setdefault("LFPS_EMAIL", "ops@thewildfireguardian.com")
rc = cli.main(["watch", "--aoi", aoi_path, "--state-dir", state_dir, "--port", "0",
               "--max-cycles", "1"] + DEPLOYED + EXTRA[variant])
with open(os.path.join(out_dir, f"cost_{variant}.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=1, default=str)
print(json.dumps({"variant": variant, "cycle_s": result.get("cycle_s"),
                  "per_incident": {result["names"].get(k, k)[:20]: {kk: v.get(kk) for kk in ("terrain_s", "weather_s", "spread_s", "cells")}
                                   for k, v in result.get("incidents", {}).items()}}, indent=1), flush=True)
