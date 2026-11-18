"""BUILD-3 (feeds): items 1-4 and 6 measured against the LIVE keyless
endpoints (WFIGS points + perimeters, FIRIS, CAL FIRE; FIRMS only when
FIRMS_MAP_KEY is set — never printed) with the CONUS AOI, through the
repo's own code path: ThreatMonitor._fetch_and_fuse over the adapters
cli._live_feed_stack builds for each config. Read-only GETs.

Configs, interleaved per rep (so the CDN / origin see the same minute):
  legacy        today's watcher (fixed URLs, identity, serial)
  bust          --feeds-cache-bust
  bust+gzip     --feeds-cache-bust --feeds-gzip
  all           --feeds-cache-bust --feeds-gzip --feeds-parallel
  incremental   all + --feeds-incremental: fetch 1 (full) then fetch 2
                (incremental) on the same adapters
Per config: the fetch wall time, fuse time, and per feed seconds / bytes /
wire bytes / records; the fused set's fingerprint (ids) per config.

Run from the repo root:  python <this> [reps]   -> measure_feeds_out.json"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from wgresponse import cli  # noqa: E402
from wgresponse.contracts import BBox, ResponseConfig  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
KEY = os.environ.get("FIRMS_MAP_KEY") or None

CONFIGS = {
    "legacy": {},
    "bust": {"cache_bust": True},
    "bust+gzip": {"cache_bust": True, "gzip": True},
    "all": {"cache_bust": True, "gzip": True, "parallel_sources": True},
}


def monitor(transport, parallel):
    feeds_stack, _ = cli._live_feed_stack(KEY, live=bool(KEY),
                                          transport=transport)
    cfg = ResponseConfig(feeds_parallel=parallel)
    return ThreatMonitor(feeds=feeds_stack, weather_provider=None,
                         terrain_provider=None, registry=None,
                         alert_engine=None, cfg=cfg, safe_zones=[], aoi=AOI)


def run(mon):
    notes, events = [], []
    t0 = time.monotonic()
    fused, healthy = mon._fetch_and_fuse(time.time(), notes, events)
    wall = time.monotonic() - t0
    d = mon.feeds_detail
    row = {"wall_s": round(wall, 2), "fetch_s": d["fetch_s"],
           "fuse_s": round(wall - d["fetch_s"], 3), "healthy": healthy,
           "n_fused": len(fused),
           "fused_sha": hashlib.sha256("|".join(sorted(
               i.incident_id for i in fused)).encode()).hexdigest()[:12],
           "events": [e.kind for e in events], "feeds": {}}
    for f in d["feeds"]:
        row["feeds"][f["feed"]] = {
            "s": round(f["s"], 2), "ok": f["ok"], "n": f["n"],
            "bytes": f.get("bytes"), "wire_bytes": f.get("wire_bytes"),
            "requests": [{k: q.get(k) for k in ("what", "s", "bytes",
                                                "wire_bytes", "status")}
                         for q in f.get("requests", [])],
            "incremental": f.get("incremental")}
    return row


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    out = {"firms": bool(KEY), "aoi": "CONUS", "reps": []}
    for rep in range(reps):
        r = {}
        for name, transport in CONFIGS.items():
            mon = monitor(transport, parallel=(name == "all"))
            r[name] = run(mon)
            print(rep, name, r[name]["wall_s"], "s",
                  {k: (v["s"], v["wire_bytes"] or v["bytes"])
                   for k, v in r[name]["feeds"].items()}, flush=True)
        mon = monitor(dict(CONFIGS["all"], incremental=True), parallel=True)
        r["incremental_1_full"] = run(mon)
        r["incremental_2"] = run(mon)
        for k in ("incremental_1_full", "incremental_2"):
            print(rep, k, r[k]["wall_s"], "s",
                  {f: (v["s"], v["wire_bytes"] or v["bytes"])
                   for f, v in r[k]["feeds"].items()}, flush=True)
        out["reps"].append(r)
    names = list(out["reps"][0])
    summ = {}
    for name in names:
        walls = [r[name]["wall_s"] for r in out["reps"]]
        per = {}
        for f in out["reps"][0][name]["feeds"]:
            per[f] = {
                "s_mean": round(sum(r[name]["feeds"][f]["s"]
                                    for r in out["reps"]) / reps, 2),
                "bytes_wire_mean": int(sum(
                    (r[name]["feeds"][f]["wire_bytes"]
                     or r[name]["feeds"][f]["bytes"] or 0)
                    for r in out["reps"]) / reps)}
        summ[name] = {"wall_s_mean": round(sum(walls) / reps, 2),
                      "wall_s": walls, "feeds": per,
                      "fused_sha": [r[name]["fused_sha"] for r in out["reps"]]}
    out["summary"] = summ
    print(json.dumps(summ, indent=1))
    with open(os.path.join(HERE, "measure_feeds_out.json"), "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
