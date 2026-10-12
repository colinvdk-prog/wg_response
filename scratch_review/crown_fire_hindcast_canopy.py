"""Crown-fire study: LANDFIRE canopy for the 11-fire hindcast domains
(2026-10-12; NOT product code; docs/review/crown_fire_model_2026-10-12.md).

The 2026-09-27 belief-vs-physics study (scratch_review/belief_vs_physics/)
replays 11 real 2026 fires on LFPS terrain tiles (one per fire, the harness
domain: every kept perimeter's bbox + 4 km, 90-109 m cells). The crown-fire
model needs the LANDFIRE canopy (LF2024 CBH;CBD;CC;CH) on the same grids:
one LFPS job per fire over the same AOI, through the shipped
LfpsTerrain.canopy() (its own zip + decoded cache beside the terrain tiles in
out/bvp_data/lfps). Every LFPS round-trip is logged to
out/bvp_data/lfps/canopy_requests.jsonl.

    py -3 scratch_review/crown_fire_hindcast_canopy.py copy     # terrain tiles
    py -3 scratch_review/crown_fire_hindcast_canopy.py fetch    # canopy jobs
    py -3 scratch_review/crown_fire_hindcast_canopy.py summary  # canopy stats

`copy` copies each fire's cached terrain tile (read only) from the original
study's worktree (env BVP_SRC_DATA, default ../agent-a4ec5ffe8d93e5bdd/out/
bvp_data) — the inputs incidents/, weather/, rows/ must already be copied.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "belief_vs_physics"))
sys.path.insert(0, ROOT)

import bvp_fetch as F  # noqa: E402
from wgresponse.terrain import (LfpsTerrain, _default_http_get,  # noqa: E402
                                attach_canopy, load_ascii_tile)

SRC = os.environ.get("BVP_SRC_DATA", os.path.join(
    os.path.dirname(ROOT), "agent-a4ec5ffe8d93e5bdd", "out", "bvp_data"))
LFPS_DIR = os.path.join(F.DATA_DIR, "lfps")
LOG = os.path.join(LFPS_DIR, "canopy_requests.jsonl")
OUT = os.path.join(HERE, "crown_fire_hindcast_canopy.json")
FIRES = ["aspenacres", "bobcatlakes", "border2", "grasshopper", "hagen",
         "littlegiant", "plaskett", "sandcreek", "sinlahekin", "timber",
         "willow"]


def domain(key):
    d = F.read_json(os.path.join(F.DATA_DIR, "incidents", key + ".json"))
    return F.domain_of(d["snapshots"])


def logging_get(tag, counts):
    def get(url, headers=None, timeout_s=180.0):
        kind = ("submit" if "/submit" in url
                else "status-poll" if "/status" in url else "zip-download")
        t = time.monotonic()
        status, body = None, b""
        try:
            status, body = _default_http_get(url, headers, timeout_s=timeout_s)
            return status, body
        finally:
            counts[kind] = counts.get(kind, 0) + 1
            shown = "&".join(p for p in url.split("&")
                             if not p.lower().startswith("email="))
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "t_utc": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"), "fire": tag, "kind": kind,
                    "url": shown, "status": status,
                    "bytes": len(body or b""),
                    "s": round(time.monotonic() - t, 2)}) + "\n")
            if kind != "status-poll":
                print(f"  [{tag}] LFPS {kind} -> {status}", flush=True)
    return get


def main(mode):
    os.makedirs(LFPS_DIR, exist_ok=True)
    if mode == "copy":
        for key in FIRES:
            bbox, cell_m = domain(key)
            cp = LfpsTerrain(cache_dir=LFPS_DIR).cache_path(bbox, cell_m)
            src = os.path.join(SRC, "lfps", os.path.basename(cp))
            if not os.path.exists(cp):
                shutil.copyfile(src, cp)
            print(key, os.path.basename(cp), cell_m)
        return
    if mode == "fetch":
        email = os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"
        rec = {}
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as fh:
                rec = json.load(fh)
        for key in FIRES:
            bbox, cell_m = domain(key)
            counts = {}
            lf = LfpsTerrain(cache_dir=LFPS_DIR, http_get=logging_get(key, counts),
                             email=email, poll_interval_s=10.0,
                             timeout_s=1800.0,
                             canopy_layer_list=LfpsTerrain.DEFAULT_CANOPY_LAYERS)
            t0 = time.monotonic()
            print(f"[{key}] canopy {bbox.east - bbox.west:.3f} x "
                  f"{bbox.north - bbox.south:.3f} deg @ {cell_m:.0f} m", flush=True)
            for attempt in range(3):
                try:
                    lf.canopy(bbox, cell_m)
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! {key} attempt {attempt + 1}: {exc}", flush=True)
                    time.sleep(20)
            r = rec.setdefault(key, {})
            if counts:
                r["requests"] = counts
                r["fetch_s"] = round(time.monotonic() - t0, 1)
            with open(OUT, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=1, sort_keys=True)
        return
    if mode == "summary":
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from tools.fetch_sim_tiles import summarize_canopy
        rec = {}
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as fh:
                rec = json.load(fh)
        for key in FIRES:
            bbox, cell_m = domain(key)
            lf = LfpsTerrain(cache_dir=LFPS_DIR,
                             canopy_layer_list=LfpsTerrain.DEFAULT_CANOPY_LAYERS)
            tile = load_ascii_tile(lf.cache_path(bbox, cell_m))
            cpath = lf.canopy_cache_path(bbox, cell_m)
            if not os.path.exists(cpath):
                print(key, "no canopy yet")
                continue
            tile = attach_canopy(tile, lf.canopy(bbox, cell_m))
            s = summarize_canopy(tile)
            rec.setdefault(key, {})["summary"] = s
            rec[key]["grid"] = [tile.spec.nrows, tile.spec.ncols, cell_m]
            print(key, s["forest_canopy_pct_of_tile"],
                  s["canopy_pct_by_fuel_class"],
                  s["burnable_canopy_p10_p50_p90"]["cbh_m"],
                  s["burnable_canopy_p10_p50_p90"]["cbd_kgm3"])
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1])
