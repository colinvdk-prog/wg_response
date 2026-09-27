"""scratch_review/old_black_ab.py — corridor A/B for the founder rulings of
2026-09-26 ("old burned ground does not count as a violation. crews once
leave a map are no longer part of the simulation"; contracts old_black_refuge
/ left_map_out / escape_prefer_on_map; docs/SIMULATOR_DESIGN.md "Old black is
a refuge; crews that leave the map are out (2026-09-26)").

The pattern of scratch_review/escape_roads_ab.py: runs scratch_review/
corridor_grid.py arms (default local2_road + alerted2_road) on vail_co,
beaver_creek_co, glenwood_springs_co with extra ResponseConfig overrides into
scratch_review/old_black/corridor_<tag>/ (the committed grid of record in
scratch_review/corridor/ is never touched), with the per-tick measurements of
scratch_review/old_black_metrics.py on (CORRIDOR_OLD_BLACK_METRICS=1), then
classifies every safety-audit row of the violating scenarios with
scratch_review/corridor_viol.py over that directory, and prints a per arm x
map summary.

Usage:
  py -3 scratch_review/old_black_ab.py --tag off --set old_black_refuge=false \
      --set left_map_out=false --set escape_prefer_on_map=false
  py -3 scratch_review/old_black_ab.py --tag on
  py -3 scratch_review/old_black_ab.py --summary off on
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "scratch_review" / "old_black"
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
    os.environ["CORRIDOR_OLD_BLACK_METRICS"] = "1"
    os.environ.setdefault("PYTHONHASHSEED", "0")
    from scratch_review import corridor_grid, corridor_viol
    assert corridor_grid.OUT_DIR == out, corridor_grid.OUT_DIR
    for arm in arms:
        corridor_grid.run_arm(arm, list(maps), workers)
    sys.argv = ["corridor_viol.py", "--workers", str(workers), "--arms", *arms]
    corridor_viol.main()


def summary(tags) -> None:
    from scratch_review.old_black_metrics import dist_buckets
    for tag in tags:
        d = BASE / f"corridor_{tag}"
        viol = {}
        vf = d / "violations.json"
        if vf.exists():
            for o in json.load(open(vf, encoding="utf-8")):
                viol.setdefault((o["arm"], o["map"]), []).append(o)
        print(f"\n== {tag} ({d})")
        print("| arm | map | scenarios | served | relocations | crews left the map | "
              "departure rows | 10c rows by reason/mode (every tick) | overrun ticks | "
              "old-black crew-ticks no longer counted (in active band) |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        tot = {}
        for f in sorted(d.glob("*__*.json")):
            j = json.load(open(f, encoding="utf-8"))
            rows = [r for r in j["rows"] if not r.get("error")]
            served = sum(r.get("served") or 0 for r in rows)
            reloc = sum(r.get("relocations") or 0 for r in rows)
            left = sum(r.get("crews_left_map") or 0 for r in rows)
            ovt = sum(r.get("ov_ticks") or 0 for r in rows)
            cls = viol.get((j["arm"], j["map"]), [])
            dep = max((o["departure_rows_max"] for o in cls), default=0)
            kinds = {}
            stopped = in_act = 0
            dists = []
            for r in rows:
                ob = r.get("ob") or {}
                for k, n in (ob.get("rows_by_reason_mode") or {}).items():
                    kinds[k] = kinds.get(k, 0) + n
                stopped += ob.get("stopped_crew_ticks", 0)
                in_act += ob.get("stopped_in_active_band", 0)
                dists += ob.get("stopped_dist_burning_m") or []
            print(f"| {j['arm']} | {j['map']} | {len(rows)} | {served} | {reloc} | "
                  f"{left} | {dep} | {kinds or '-'} | {ovt} | {stopped} ({in_act}) |")
            t = tot.setdefault(j["arm"], {"n": 0, "served": 0, "reloc": 0, "left": 0,
                                          "ovt": 0, "dep": 0, "kinds": {},
                                          "stopped": 0, "in_act": 0, "dists": []})
            t["n"] += len(rows)
            t["served"] += served
            t["reloc"] += reloc
            t["left"] += left
            t["ovt"] += ovt
            t["dep"] = max(t["dep"], dep)
            for k, n in kinds.items():
                t["kinds"][k] = t["kinds"].get(k, 0) + n
            t["stopped"] += stopped
            t["in_act"] += in_act
            t["dists"] += dists
        for arm, t in tot.items():
            print(f"| **{arm} pooled** | — | {t['n']} | {t['served']} | {t['reloc']} | "
                  f"{t['left']} | {t['dep']} | {t['kinds'] or '-'} | {t['ovt']} | "
                  f"{t['stopped']} ({t['in_act']}) |")
            if t["stopped"]:
                print(f"    nearest burning cell for those crew-ticks: "
                      f"{dist_buckets(t['dists'])}")


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
