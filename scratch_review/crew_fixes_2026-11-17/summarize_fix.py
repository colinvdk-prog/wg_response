"""Before / after tables for the 2026-11-17 crew-program fixes. NOT product
code.

  python summarize_fix.py valley BEFORE_DIR AFTER_DIR    (run_matrix.py sets)
  python summarize_fix.py lanea  BEFORE_DIR AFTER_DIR    (matrix.py sets)

Valley columns follow the October review's summary table: homes coated,
reached homes coated in time, reached uncoated, passes through the band
(every crew-overrun row; transit rows in brackets), deepest pass (rows that
carry a depth: transit rows before the fix, every row after it), rows > 30
min past the line, time in the band (exposed seconds the rows carry), crews
off the map, relocations, km driven, median closest call (each run's
closest P10 margin a crew stood at)."""
from __future__ import annotations

import json
import os
import sys


def load(d):
    rows = {}
    with open(os.path.join(d, "runs.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                rows[r["run"]] = r
    return rows


def valley_totals(rows, only_loc=None):
    t = {"runs": 0, "coated": 0, "reached": 0, "in_time": 0, "uncoated": 0,
         "rows": 0, "transit": 0, "deepest": None, "over30": 0,
         "band_s": 0.0, "off_map": 0, "reloc": 0, "km": 0.0, "wd": 0,
         "dv": 0}
    margins = []
    for r in rows.values():
        if "error" in r:
            continue
        if only_loc and r["loc"] != only_loc:
            continue
        t["runs"] += 1
        sv, sf = r["service"], r["safety"]
        t["coated"] += sv["served"]
        t["reached"] += sv["homes_reached"]
        t["uncoated"] += sv["reached_unprotected"]
        t["in_time"] += sv["homes_reached"] - sv["reached_unprotected"]
        t["rows"] += sf["overrun_rows"]
        t["transit"] += sf.get("transit_rows", 0)
        t["dv"] += sf["departure_violations_max"]
        for o in sf.get("overrun_detail") or []:
            m = o.get("margin_min")
            if m is not None:
                if t["deepest"] is None or m < t["deepest"]:
                    t["deepest"] = round(m, 1)
                if m < -30.0:
                    t["over30"] += 1
            t["band_s"] = round(t["band_s"] + float(o.get("exposed_s") or 0.0), 1)
        t["off_map"] += sf["left_map"]
        t["reloc"] += sf["relocations"]
        t["wd"] += sf["withdrawn"]
        t["km"] = round(t["km"] + sum((r.get("crews") or {}).get(
            "km_driven", {}).values()), 1)
        m = (sf.get("closest_p10_margin_h") or {})
        if m:
            margins.append(min(m.values()))
    margins.sort()
    t["median_closest_h"] = margins[len(margins) // 2] if margins else None
    return t


def lanea_totals(rows, only=None):
    t = {"runs": 0, "coated": 0, "rows": 0, "transit": 0, "deepest": None,
         "over30": 0, "band_s": 0.0, "off_map": 0, "reloc": 0, "wd": 0,
         "dv": 0, "no_depth": 0}
    for r in rows.values():
        if "error" in r:
            continue
        if only and not only(r):
            continue
        t["runs"] += 1
        sf = r["safety"]
        t["coated"] += r["served"]
        t["rows"] += sf["overrun_rows"]
        t["dv"] += sf["departure_violations_max"]
        for o in r["overruns"]:
            if o.get("transit"):
                t["transit"] += 1
            m = o.get("margin_min")
            if m is None:
                t["no_depth"] += 1
                continue
            if t["deepest"] is None or m < t["deepest"]:
                t["deepest"] = round(m, 1)
            if m < -30.0:
                t["over30"] += 1
            t["band_s"] = round(t["band_s"] + float(o.get("exposed_s") or 0.0), 1)
        t["off_map"] += sf["left_map"]
        t["reloc"] += sf["relocations"]
        t["wd"] += sf["withdrawn"]
    return t


def main(argv):
    kind, a_dir, b_dir = argv[:3]
    a, b = load(a_dir), load(b_dir)
    if kind == "valley":
        ta, tb = valley_totals(a), valley_totals(b)
        print(f"| | before | after |\n|---|---|---|")
        for k in ta:
            print(f"| {k} | {ta[k]} | {tb[k]} |")
        print()
        print("| map | coated before -> after | reached uncoated | rows | off map |")
        print("|---|---|---|---|---|")
        for loc in sorted({r["loc"] for r in a.values() if "loc" in r}):
            x, y = valley_totals(a, loc), valley_totals(b, loc)
            print(f"| {loc} | {x['coated']} -> {y['coated']} | {x['uncoated']} -> "
                  f"{y['uncoated']} | {x['rows']} -> {y['rows']} | {x['off_map']} -> "
                  f"{y['off_map']} |")
        print()
        print("| run | coated | uncoated | rows (deepest) | off map | reloc |")
        print("|---|---|---|---|---|---|")
        for run in sorted(set(a) & set(b)):
            ra, rb = a[run], b[run]
            if "error" in ra or "error" in rb:
                continue

            def deep(r):
                ms = [o.get("margin_min") for o in r["safety"].get("overrun_detail") or []
                      if o.get("margin_min") is not None]
                return min(ms) if ms else None
            ka = (ra["service"]["served"], ra["service"]["reached_unprotected"],
                  ra["safety"]["overrun_rows"], ra["safety"]["left_map"],
                  ra["safety"]["relocations"])
            kb = (rb["service"]["served"], rb["service"]["reached_unprotected"],
                  rb["safety"]["overrun_rows"], rb["safety"]["left_map"],
                  rb["safety"]["relocations"])
            if ka != kb:
                print(f"| {run} | {ka[0]} -> {kb[0]} | {ka[1]} -> {kb[1]} | "
                      f"{ka[2]} ({deep(ra)}) -> {kb[2]} ({deep(rb)}) | {ka[3]} -> {kb[3]} | "
                      f"{ka[4]} -> {kb[4]} |")
    else:
        ta, tb = lanea_totals(a), lanea_totals(b)
        print(f"| | before | after |\n|---|---|---|")
        for k in ta:
            print(f"| {k} | {ta[k]} | {tb[k]} |")
        print()
        print("| map | coated before -> after | rows | off map | reloc |")
        print("|---|---|---|---|---|")
        for loc in sorted({r["loc"] for r in a.values() if "loc" in r}):
            x = lanea_totals(a, lambda r, l=loc: r["loc"] == l)
            y = lanea_totals(b, lambda r, l=loc: r["loc"] == l)
            print(f"| {loc} | {x['coated']} -> {y['coated']} | {x['rows']} -> {y['rows']} | "
                  f"{x['off_map']} -> {y['off_map']} | {x['reloc']} -> {y['reloc']} |")
        print()
        print("| run | coated | rows (deepest) | off map | reloc | 46a fired |")
        print("|---|---|---|---|---|---|")
        for run in sorted(set(a) & set(b)):
            ra, rb = a[run], b[run]
            if "error" in ra or "error" in rb:
                continue

            def deep(r):
                ms = [o.get("margin_min") for o in r["overruns"]
                      if o.get("margin_min") is not None]
                return min(ms) if ms else None
            sa, sb = ra["safety"], rb["safety"]
            ka = (ra["served"], sa["overrun_rows"], sa["left_map"], sa["relocations"])
            kb = (rb["served"], sb["overrun_rows"], sb["left_map"], sb["relocations"])
            if ka != kb:
                print(f"| {run} | {ka[0]} -> {kb[0]} | {ka[1]} ({deep(ra)}) -> "
                      f"{kb[1]} ({deep(rb)}) | {ka[2]} -> {kb[2]} | {ka[3]} -> {kb[3]} | |")


if __name__ == "__main__":
    main(sys.argv[1:])
