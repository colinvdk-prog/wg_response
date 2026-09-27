"""scratch_review/old_black_bench_compare.py — compare arms of the 60-scenario
dispatch bench (scratch_review/bench_dispatch.py, BENCH_OLD_BLACK_METRICS=1)
for the founder rulings of 2026-09-26 (old black is a refuge; a crew that
leaves the map is out): totals and every scenario that changed.

Usage: py -3 scratch_review/old_black_bench_compare.py LABEL_A LABEL_B [...]
(reads scratch_review/bench_<label>.json)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from scratch_review.old_black_metrics import dist_buckets  # noqa: E402


def load(label):
    with open(os.path.join(HERE, f"bench_{label}.json"), encoding="utf-8") as f:
        d = json.load(f)
    return {r["seed"]: r for r in d["rows"]}, d


def totals(rows):
    t = {"served": 0, "departure_rows": 0, "rows": {}, "overrun_ticks": 0,
         "relocations": 0, "left": 0, "stopped": 0, "in_active": 0,
         "hist_only": 0, "dist": [], "dist_front": []}
    for r in rows.values():
        t["served"] += r["served"]
        t["departure_rows"] += r.get("departure_viol_max", 0)
        t["overrun_ticks"] += r.get("overruns", 0)
        t["relocations"] += r.get("relocations", 0)
        t["left"] += r.get("crews_left_map", 0) or 0
        ob = r.get("ob") or {}
        for k, n in (ob.get("rows_by_reason_mode") or {}).items():
            t["rows"][k] = t["rows"].get(k, 0) + n
        t["stopped"] += ob.get("stopped_crew_ticks", 0)
        t["in_active"] += ob.get("stopped_in_active_band", 0)
        t["hist_only"] += ob.get("imminent_rows_history_only", 0)
        t["dist"] += ob.get("stopped_dist_burning_m") or []
        t["dist_front"] += ob.get("stopped_dist_front_m") or []
    return t


def main(argv):
    arms = [load(a) for a in argv]
    print("| arm | served | departure rows | 10c rows by reason/mode | overrun ticks | "
          "relocations | crews left the map | old-black crew-ticks no longer counted "
          "(in an active band) | history-only imminent rows |")
    print("|---|---|---|---|---|---|---|---|---|")
    tots = []
    for label, (rows, _d) in zip(argv, arms):
        t = totals(rows)
        tots.append(t)
        print(f"| {label} | {t['served']} | {t['departure_rows']} | "
              f"{sum(t['rows'].values())} {t['rows'] or ''} | {t['overrun_ticks']} | "
              f"{t['relocations']} | {t['left']} | {t['stopped']} ({t['in_active']}) | "
              f"{t['hist_only']} |")
    for label, t in zip(argv, tots):
        if t["stopped"]:
            print(f"\n{label}: nearest actively burning cell of the stopped crew-ticks "
                  f"{dist_buckets(t['dist'])}; nearest front cell (+-15 min) "
                  f"{dist_buckets(t['dist_front'])}")
    base_rows = arms[0][0]
    for label, (rows, _d) in zip(argv[1:], arms[1:]):
        print(f"\nscenarios that changed, {argv[0]} -> {label}:")
        n = 0
        for seed in sorted(base_rows):
            a, b = base_rows[seed], rows.get(seed)
            if b is None:
                continue
            ka = (a["served"], a.get("relocations"), a.get("overrun_rows"),
                  a.get("crews_left_map", 0))
            kb = (b["served"], b.get("relocations"), b.get("overrun_rows"),
                  b.get("crews_left_map", 0))
            if ka != kb:
                n += 1
                print(f"  {seed:<34} served {a['served']}->{b['served']}  "
                      f"reloc {a.get('relocations')}->{b.get('relocations')}  "
                      f"10c {a.get('overrun_rows')}->{b.get('overrun_rows')}  "
                      f"left {a.get('crews_left_map', 0)}->{b.get('crews_left_map', 0)}  "
                      f"stopped {((a.get('ob') or {}).get('stopped_crew_ticks', 0))}->"
                      f"{((b.get('ob') or {}).get('stopped_crew_ticks', 0))}")
        print(f"  ({n} of {len(base_rows)} changed)")


if __name__ == "__main__":
    main(sys.argv[1:])
