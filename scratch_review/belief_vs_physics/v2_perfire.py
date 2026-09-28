"""Per-fire v2 summary (harness and wide maps) + Willow step by step.
Appends to v2_tables.md (run after v2_tables.py)."""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    lines = ["", "### Per fire (median P50 area error factor; late-P50 steps "
             "on >= 2 %/day growth in brackets)", ""]
    for name in ("v2_final.json", "v2_final_wide.json"):
        d = json.load(open(os.path.join(_HERE, name)))
        v2 = d["_v2_name"]
        lines += [f"{'Harness' if 'wide' not in name else 'Wide'} maps:", "",
                  "| fire | steps | physics | as built | v2 | v2 slowed steps | "
                  "v2 rapid steps |", "|---|---|---|---|---|---|---|"]
        fires = []
        for s in d["as_built"]["steps"]:
            if s["fire"] not in fires:
                fires.append(s["fire"])
        for f in fires:
            ab = [s for s in d["as_built"]["steps"] if s["fire"] == f]
            vv = [s for s in d[v2]["steps"] if s["fire"] == f]

            def med(ss, arm):
                return math.exp(statistics.median(abs(s[arm]["log_area_p50"])
                                                  for s in ss))

            def late(ss):
                return sum(1 for s in ss if s["bel"]["monotone"] and
                           s["bel"]["n_new_b"] > 0 and s["bel"]["hit_p50"] < 0.5
                           and s["growth_pct_day"] >= 2.0)
            slowed = sum(1 for s in vv if s["applied"] and not s["rapid"])
            lines.append(f"| {f} | {len(ab)} | {med(ab, 'phys'):.2f}x | "
                         f"{med(ab, 'bel'):.2f}x ({late(ab)}) | "
                         f"{med(vv, 'bel'):.2f}x ({late(vv)}) | {slowed} | "
                         f"{sum(1 for s in vv if s['rapid'])} |")
        lines.append("")
    d = json.load(open(os.path.join(_HERE, "v2_final.json")))
    v2 = d["_v2_name"]
    lines += ["Willow step by step (harness map): forecast P50 area at the "
              "next capture / observed area", "",
              "| forecast | observed growth | physics | as built | v2 | v2 "
              "decision |", "|---|---|---|---|---|---|"]
    ab = sorted((s for s in d["as_built"]["steps"] if s["fire"] == "willow"),
                key=lambda s: s["k"])
    vv = sorted((s for s in d[v2]["steps"] if s["fire"] == "willow"),
                key=lambda s: s["k"])
    for a, v in zip(ab, vv):
        t = datetime.fromtimestamp(a["t0_s"], tz=timezone.utc)
        obs = a["phys"]["obs_area_ac"]
        why = ("rapid" if v["rapid"] else
               ("prior" if v["state"] == "prior" else
                (f"slowed {min(v['h_eff'], v['dt_h']):.0f}/{v['dt_h']:.0f} h"
                 if v["applied"] else "physics (new weather at hour 0)")))
        lines.append(f"| {t:%b %d %H:%M} | {a['gis1'] - a['gis0']:+,.0f} ac "
                     f"({a['growth_pct_day']:.1f} %/d) | "
                     f"{a['phys']['area_p50_ac'] / obs:.1f}x | "
                     f"{a['bel']['area_p50_ac'] / obs:.1f}x | "
                     f"{v['bel']['area_p50_ac'] / obs:.1f}x | {why} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(_HERE, "v2_tables.md"), "a", encoding="utf-8",
              newline="\n") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
