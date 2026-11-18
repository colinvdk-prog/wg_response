"""Summarise matrix runs.jsonl files: python summarize.py DIR [DIR...]"""
import json
import sys

tot = {"runs": 0, "dv": 0, "rows": 0, "deep30": 0, "left": 0, "wd": 0, "rel": 0}
for d in sys.argv[1:]:
    for line in open(f"{d}/runs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if "error" in r:
            print(d, r["run"], "ERROR", r["error"])
            continue
        sf = r["safety"]
        deep = [o.get("margin_min") for o in r["overruns"] if o.get("margin_min") is not None]
        mins = [v for v in sf["closest_p10_margin_h"].values()]
        print(f"{d:14s} {r['run']:40s} served {r['served']:3d}/{r['n_clients']:3d} "
              f"dv {sf['departure_violations_max']} rows {sf['overrun_rows']} "
              f"worst {min(deep) if deep else '-'} wd {sf['withdrawn']} rel {sf['relocations']} "
              f"left {sf['left_map']} closest_h {min(mins) if mins else '-'} acres {r['acres_end']}")
        tot["runs"] += 1
        tot["dv"] += sf["departure_violations_max"]
        tot["rows"] += sf["overrun_rows"]
        tot["deep30"] += sum(1 for v in deep if v < -30)
        tot["left"] += sf["left_map"]
        tot["wd"] += sf["withdrawn"]
        tot["rel"] += sf["relocations"]
print(tot)
