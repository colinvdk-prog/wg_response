"""List the late-P50 steps (the applied belief's P50 caught < 50 % of the
newly burned ground) of chosen configs from v2_final*.json (beside this
script) or the per-step grid (out/bvp_data/v2_grid.json), side by side with
physics and the belief as built, plus the named runs.

    py -3 scratch_review/belief_vs_physics/v2_late.py v2_final.json as_built daily_r1.5_s60_w4+cal1+p2
    py -3 scratch_review/belief_vs_physics/v2_late.py v2_grid.json as_built daily_r1.5_s90_w5
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
NAMED = [("willow", 11), ("willow", 13), ("grasshopper", 8),
         ("littlegiant", 28), ("timber", 7), ("littlegiant", 16),
         ("willow", 9)]


def main(argv) -> int:
    path = os.path.join(_HERE, argv[0])
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "out",
                            "bvp_data", argv[0])
    grid = json.load(open(path))
    names = argv[1:]
    idx = {n: {(s["fire"], s["k"]): s for s in grid[n]["steps"]} for n in names}
    keys = set()
    for n in names:
        for s in grid[n]["steps"]:
            if s["bel"]["monotone"] and s["bel"]["n_new_b"] > 0 and \
                    s["bel"]["hit_p50"] < 0.5:
                keys.add((s["fire"], s["k"]))
    print("late-P50 steps (P50 caught < 50 % of the new ground) in any config:")
    hdr = " | ".join(f"{n[:28]:28s}" for n in names)
    print(f"{'fire':12s} k  date  phase    growth       | physics | {hdr}")
    for key in sorted(keys, key=lambda x: (x[0], x[1])):
        s0 = idx[names[0]][key]
        t = datetime.fromtimestamp(s0["t0_s"], tz=timezone.utc)
        cells = []
        for n in names:
            s = idx[n][key]
            tag = ("R" if s["rapid"] else ".") + ("A" if s["applied"] else ".")
            cells.append(f"{s['bel']['hit_p50']:5.2f} {tag} h{s['h_eff']:>3}      "
                         f"          "[:28])
        print(f"{key[0]:12s} {key[1]:2d} {t:%m-%d} {s0['phase']:8s} "
              f"{s0['gis1'] - s0['gis0']:+7,.0f} ac {s0['growth_pct_day']:5.1f}%/d | "
              f"{s0['phys']['hit_p50']:5.2f}   | " + " | ".join(cells))
    print("\nnamed runs (P50 catch; R = rapid, A = belief applied, h = hours "
          "of the window the belief covered):")
    for key in NAMED:
        row = []
        for n in names:
            s = idx[n].get(key)
            if s is None:
                continue
            row.append(f"{n[:24]}: {s['bel']['hit_p50']:.2f} "
                       f"{'R' if s['rapid'] else '.'}{'A' if s['applied'] else '.'} "
                       f"h{s['h_eff']}/{s['dt_h']:.0f}")
        s0 = idx[names[0]][key]
        print(f"  {key[0]} k{key[1]} ({s0['growth_pct_day']:.1f} %/d, physics "
              f"{s0['phys']['hit_p50']:.2f}): " + "; ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
