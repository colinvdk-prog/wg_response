"""BUILD-3 (feeds), item 7: the new-fire sentinel's own poll, timed against
the LIVE keyless endpoints (CONUS AOI; no client roster - timing only).
Read-only GETs (cache-busted, gzip). n polls `gap` s apart; prints per
source seconds / bytes / requests and the estimated publish -> first_seen
latency at a 60 s period (uniform wait 0-60 s + the measured poll time).

Run from the repo root: python <this> [n] [gap]  -> measure_sentinel_out.json"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from wgresponse import sentinel  # noqa: E402
from wgresponse.contracts import BBox, ResponseConfig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    cfg = ResponseConfig(new_fire_sentinel_s=60.0)
    s = sentinel.NewFireSentinel(cfg, clients_fn=lambda: [], aoi=AOI)
    polls = []
    for k in range(n):
        t0 = time.monotonic()
        s.poll_once()
        wall = time.monotonic() - t0
        st = s.status()["sources"]
        row = {"poll": k, "wall_s": round(wall, 2),
               "sources": {src: {"s": v["last"]["s"], "ok": v["last"]["ok"],
                                 "requests": v["last"]["requests"],
                                 "bytes": v["last"]["bytes"],
                                 "seen": v["seen"],
                                 "error": v["last"].get("error")}
                           for src, v in st.items()}}
        polls.append(row)
        print(json.dumps(row), flush=True)
        if k < n - 1:
            time.sleep(gap)
    walls = [p["wall_s"] for p in polls[1:]] or [polls[0]["wall_s"]]
    mean_q = sum(walls) / len(walls)
    est = {"period_s": 60.0, "poll_wall_s_mean": round(mean_q, 2),
           "poll_wall_s_max": max(walls),
           "publish_to_first_seen_mean_s": round(30.0 + mean_q, 1),
           "publish_to_first_seen_max_s": round(60.0 + max(walls), 1),
           "note": "serial sources; uniform publish within a 60 s period"}
    print(json.dumps(est, indent=1))
    with open(os.path.join(HERE, "measure_sentinel_out.json"), "w") as fh:
        json.dump({"polls": polls, "estimate": est,
                   "status_records": s.status()["n_records"]}, fh, indent=1)


if __name__ == "__main__":
    main()
