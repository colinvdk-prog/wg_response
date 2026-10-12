"""Crown fire on the fine-fuel starts (2026-10-12, after founder option 2):
the 13 one-click scenarios, option 1 (live world11) against grass-only +
crown fire (5-min and 10-min spot delay). Reads the three batch files of
scratch_review/crown_fire_scenarios.py --out crown_fire_finestart_{1,2,3}.json.
NOT product code. Usage: py -3 scratch_review/crown_fire_finestart_table.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from crown_fire_tables import load, start_label  # noqa: E402

rows = {}
for i in (1, 2, 3):
    for r in load(f"crown_fire_finestart_{i}.json") or []:
        rows[(r["scenario"], r["variant"])] = r
wind = load("crown_fire_city_wind.json") or {}


def pct(sc, kmh):
    w = wind.get(sc)
    return "" if not w else f" ({100 * kmh / (w['mean'] * 1.609344):.0f} %)"


print("| Scenario (start) | Acres 1 h / 3 h / 6 h: option 1 (live) | crown, 5-min delay | crown, 10-min delay | Fastest hour, km/h (% of wind): option 1 / crown 5 / crown 10 | First home (h): option 1 / crown 5 / crown 10 | Crew audit (viol / overrun rows) |")
print("|---|---|---|---|---|---|---|")
for sc in sorted({k[0] for k in rows}):
    rs = [rows.get((sc, v)) for v in ("opt1", "crown", "crown_d10")]
    if any(r is None for r in rs):
        print(f"| {sc} | (incomplete) |")
        continue
    def acres(r):
        m = r["demo"]["marks"]
        return f"{m['1.0']['acres']:,} / {m['3.0']['acres']:,} / {m['6.0']['acres']:,}"
    fast = " / ".join(f"{r['demo']['max_head_gain_km_per_h']:.1f}{pct(sc, r['demo']['max_head_gain_km_per_h'])}" for r in rs)
    home = " / ".join("–" if r["demo"]["first_home_reached_h"] is None else f"{r['demo']['first_home_reached_h']:.1f}" for r in rs)
    aud = " · ".join(f"{r['demo']['audit']['departure_violations_final']} / {r['demo']['audit']['crew_overrun_rows_sum']}" for r in rs)
    print(f"| {sc} ({start_label(rs[1])}) | {acres(rs[0])} | {acres(rs[1])} | {acres(rs[2])} | {fast} | {home} | {aud} |")
