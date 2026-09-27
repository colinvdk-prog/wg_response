"""scratch_review/worlds_live_probe.py — record N LIVE cycles of a running
watch daemon (wall time, phases, each live domain's spread time) and the
process memory every 2 s, optionally after one test-map action
(--action reset | none). The TWO WORLDS cost run's controlled check
(docs/LIVE_WORLD_PLAN.md "Test map and live map (2026-09-27)"): does the
live cycle return to its baseline once the drill is wiped?

    py -3 scratch_review/worlds_live_probe.py --base http://127.0.0.1:18989 \
        --pid <daemon pid> --cycles 3 --action reset --out <json>
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("scratch_review", 1)[0] + "scratch_review")
import worlds_cost_run as wc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18989")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--action", choices=("none", "reset"), default="none")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    handle = wc._k32.OpenProcess(0x1000 | 0x0010, False, args.pid)
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            m = wc.mem_mb(handle)
            if m:
                samples.append(m)
            stop.wait(2.0)
    threading.Thread(target=sampler, daemon=True).start()
    out = {"action": args.action, "cycles": []}
    if args.action == "reset":
        out["reset"] = wc.post(base, "/api/test/reset", {"by": "live-probe"})
    seen = wc.get(base, "/api/state")[0].get("cycles_run") or 0
    while len(out["cycles"]) < args.cycles:
        time.sleep(2.0)
        st = wc.get(base, "/api/state")[0]
        if (st.get("cycles_run") or 0) <= seen:
            continue
        seen = st["cycles_run"]
        ph = st.get("last_cycle_phases") or {}
        rec = {"cycle": seen, "duration_s": st.get("last_cycle_duration_s"),
               "phases": {k: v for k, v in ph.items() if k != "incidents"},
               "domains": {iid: {k: v for k, v in d.items()}
                           for iid, d in (ph.get("incidents") or {}).items()},
               "restored": st.get("restored")}
        ts = wc.get(base, "/api/test/state")[0]
        rec["test"] = {"drill": ts.get("drill", {}).get("state"),
                       "cycles_run": ts.get("cycles_run"),
                       "restored": ts.get("restored"),
                       "seeds_active": ts.get("drill", {}).get("seeds_active"),
                       "drill_log_lines": len(ts.get("drill_log") or [])}
        out["cycles"].append(rec)
        print(json.dumps(rec, default=str), flush=True)
    stop.set()
    time.sleep(2.2)
    ws = [s["ws_mb"] for s in samples]
    pv = [s["private_mb"] for s in samples]
    out["memory"] = {"n": len(samples),
                     "ws_median_mb": round(statistics.median(ws), 1),
                     "ws_max_mb": round(max(ws), 1),
                     "private_median_mb": round(statistics.median(pv), 1),
                     "private_max_mb": round(max(pv), 1)}
    print(json.dumps(out["memory"]), flush=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
