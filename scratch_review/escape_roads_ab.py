"""scratch_review/escape_roads_ab.py — corridor A/B for the SEM-10d road-network
escape (contracts escape_road_network; docs/review/escape_roads_2026-09-26.md).

Runs scratch_review/corridor_grid.py arms (default local2_road + alerted2_road)
on a map subset with extra ResponseConfig overrides, writing the arm files
under scratch_review/escape_roads/corridor_<tag>/ (the committed grid of
record in scratch_review/corridor/ is never touched), then classifies every
safety-audit row of the violating scenarios with scratch_review/corridor_
viol.py over that directory (violations.json there), and prints a per arm x
map summary. The overrides reach the spawned workers through corridor_grid's
environment hooks (CORRIDOR_EXTRA_CFG / CORRIDOR_OUT_DIR).

Usage:
  py -3 scratch_review/escape_roads_ab.py --tag off --set escape_road_network=false
  py -3 scratch_review/escape_roads_ab.py --tag on
  py -3 scratch_review/escape_roads_ab.py --summary off on
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "scratch_review" / "escape_roads"
MAPS = ("vail_co", "beaver_creek_co", "glenwood_springs_co")
ARMS = ("local2_road", "alerted2_road")


def _val(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        return float(v)


def run(tag: str, sets, arms, maps, workers: int) -> None:
    out = BASE / f"corridor_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    extra = {}
    for kv in sets:
        k, v = kv.split("=", 1)
        extra[k] = _val(v)
    os.environ["CORRIDOR_OUT_DIR"] = str(out)
    os.environ["CORRIDOR_EXTRA_CFG"] = json.dumps(extra)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    from scratch_review import corridor_grid, corridor_viol
    assert corridor_grid.OUT_DIR == out, corridor_grid.OUT_DIR
    for arm in arms:
        corridor_grid.run_arm(arm, list(maps), workers)
    sys.argv = ["corridor_viol.py", "--workers", str(workers), "--arms", *arms]
    corridor_viol.main()


def summary(tags) -> None:
    for tag in tags:
        d = BASE / f"corridor_{tag}"
        viol = {}
        vf = d / "violations.json"
        if vf.exists():
            for o in json.load(open(vf, encoding="utf-8")):
                viol.setdefault((o["arm"], o["map"]), []).append(o)
        print(f"\n== {tag} ({d})")
        print("| arm | map | scenarios | served | max viol (sum) | overrun ticks | "
              "departure rows | 10c rows by reason/mode | worst min past P10 |")
        print("|---|---|---|---|---|---|---|---|---|")
        tot = {}
        for f in sorted(d.glob("*__*.json")):
            j = json.load(open(f, encoding="utf-8"))
            rows = [r for r in j["rows"] if not r.get("error")]
            served = sum(r.get("served") or 0 for r in rows)
            v = sum(r.get("viol") or 0 for r in rows)
            ovt = sum(r.get("ov_ticks") or 0 for r in rows)
            cls = viol.get((j["arm"], j["map"]), [])
            dep = max((o["departure_rows_max"] for o in cls), default=0)
            kinds = {}
            for o in cls:
                for k, n in o["kinds"].items():
                    kinds[k] = kinds.get(k, 0) + n
            worst = max((o["worst_past_p10_min"] for o in cls), default=0.0)
            print(f"| {j['arm']} | {j['map']} | {len(rows)} | {served} | {v} | {ovt} | "
                  f"{dep} | {kinds or '-'} | {worst} |")
            t = tot.setdefault(j["arm"], {"n": 0, "served": 0, "viol": 0, "ovt": 0,
                                          "dep": 0, "rows": 0})
            t["n"] += len(rows)
            t["served"] += served
            t["viol"] += v
            t["ovt"] += ovt
            t["dep"] = max(t["dep"], dep)
            t["rows"] += sum(kinds.values())
        for arm, t in tot.items():
            print(f"| **{arm} pooled** | — | {t['n']} | {t['served']} | {t['viol']} | "
                  f"{t['ovt']} | {t['dep']} | {t['rows']} rows | |")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--arms", nargs="*", default=list(ARMS))
    ap.add_argument("--maps", nargs="*", default=list(MAPS))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--summary", nargs="*")
    a = ap.parse_args()
    if a.summary is not None:
        summary(a.summary)
    elif a.tag:
        run(a.tag, a.set, a.arms, a.maps, a.workers)
    else:
        ap.error("--tag or --summary")


if __name__ == "__main__":
    main()
