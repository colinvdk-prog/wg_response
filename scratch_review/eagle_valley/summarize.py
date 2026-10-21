"""Eagle River valley review: markdown tables from run_matrix.py output, and
a before/after comparison of two iterations. NOT product code.

Usage: py -3 scratch_review/eagle_valley/summarize.py DIR            (one table)
       py -3 scratch_review/eagle_valley/summarize.py DIR_A DIR_B    (A -> B)
"""
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


def order(run):
    loc, ign, inten = run.split("|")
    return (loc, ["canonical", "south", "northwest", "pair", "late"].index(ign),
            ["moderate", "high", "extreme"].index(inten))


def margin(r):
    m = (r.get("safety") or {}).get("closest_p10_margin_h") or {}
    return min(m.values()) if m else None


def util(r):
    """Share of on-map crew-ticks spent coating (deploying), driving (en_route),
    idle (available), escaping / relocating."""
    tot = {}
    for st in ((r.get("crews") or {}).get("status_ticks") or {}).values():
        for k, v in st.items():
            tot[k] = tot.get(k, 0) + v
    on = sum(v for k, v in tot.items() if k not in ("inbound", "left_map"))
    if not on:
        return {}
    return {k: round(100.0 * v / on) for k, v in sorted(tot.items())
            if k not in ("inbound", "left_map")}


def pct_wind(r):
    f = r.get("fire") or {}
    w = f.get("wind_mph_mean")
    k = f.get("fastest_head_kmh")
    if not w or k is None:
        return None
    return round(100.0 * k / (w * 1.609344))


def table(rows):
    print("| run | served | reached / unprotected | first gel h | viol / overrun rows | withdrawn / reloc / off map | closest P10 margin h | acres 1/3/6/12 h | fastest head km/h (% wind) | crowned ac | crew time: coat / drive / idle / reloc % |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for run in sorted(rows, key=order):
        r = rows[run]
        if "error" in r:
            print(f"| {run} | ERROR {r['error'][:60]} |")
            continue
        sv, sf, fi = r["service"], r["safety"], r["fire"]
        a = fi["acres_by_h"]
        pick = [a[i - 1] if len(a) >= i else None for i in (1, 3, 6, 12)]
        u = util(r)
        print(f"| {run} | {sv['served']} | {sv['homes_reached']} / {sv['reached_unprotected']} | "
              f"{sv['first_gel_h']} | {sf['violations_max']} / {sf['overrun_rows']} | "
              f"{sf['withdrawn']} / {sf['relocations']} / {sf['left_map']} | {margin(r)} | "
              f"{' / '.join(str(x) for x in pick)} | {fi['fastest_head_kmh']} ({pct_wind(r)} %) | "
              f"{fi['crowned_acres']['active'] + fi['crowned_acres']['passive']} | "
              f"{u.get('deploying', 0)} / {u.get('en_route', 0)} / {u.get('available', 0)} / "
              f"{u.get('relocating', 0) + u.get('egressing', 0)} |")


def totals(rows):
    t = {"runs": 0, "served": 0, "reached": 0, "unprotected": 0, "violations": 0,
         "overrun_rows": 0, "transit_rows": 0, "withdrawn": 0, "relocations": 0,
         "left_map": 0, "km_driven": 0.0}
    margins = []
    for r in rows.values():
        if "error" in r:
            continue
        t["runs"] += 1
        t["served"] += r["service"]["served"]
        t["reached"] += r["service"]["homes_reached"]
        t["unprotected"] += r["service"]["reached_unprotected"]
        t["violations"] += r["safety"]["violations_max"]
        t["overrun_rows"] += r["safety"]["overrun_rows"]
        t["transit_rows"] += r["safety"].get("transit_rows", 0)
        t["km_driven"] = round(t["km_driven"] + sum(
            (r.get("crews") or {}).get("km_driven", {}).values()), 1)
        t["withdrawn"] += r["safety"]["withdrawn"]
        t["relocations"] += r["safety"]["relocations"]
        t["left_map"] += r["safety"]["left_map"]
        m = margin(r)
        if m is not None:
            margins.append(m)
    t["closest_margin_h_min"] = min(margins) if margins else None
    t["closest_margin_h_median"] = sorted(margins)[len(margins) // 2] if margins else None
    return t


if __name__ == "__main__":
    a = load(sys.argv[1])
    if len(sys.argv) == 2:
        table(a)
        print()
        print("totals:", json.dumps(totals(a)))
    else:
        b = load(sys.argv[2])
        ta, tb = totals(a), totals(b)
        print("| total | " + os.path.basename(sys.argv[1].rstrip("/\\")) + " | "
              + os.path.basename(sys.argv[2].rstrip("/\\")) + " |")
        print("|---|---|---|")
        for k in ta:
            print(f"| {k} | {ta[k]} | {tb.get(k)} |")
        print()
        print("| run | served A -> B | unprotected A -> B | viol+overrun A -> B | closest margin h A -> B | first gel h A -> B |")
        print("|---|---|---|---|---|---|")
        for run in sorted(set(a) & set(b), key=order):
            ra, rb = a[run], b[run]
            if "error" in ra or "error" in rb:
                continue
            print(f"| {run} | {ra['service']['served']} -> {rb['service']['served']} | "
                  f"{ra['service']['reached_unprotected']} -> {rb['service']['reached_unprotected']} | "
                  f"{ra['safety']['violations_max'] + ra['safety']['overrun_rows']} -> "
                  f"{rb['safety']['violations_max'] + rb['safety']['overrun_rows']} | "
                  f"{margin(ra)} -> {margin(rb)} | {ra['service']['first_gel_h']} -> {rb['service']['first_gel_h']} |")
