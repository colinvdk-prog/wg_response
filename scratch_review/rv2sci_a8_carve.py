"""PASS-2 A8: independently parse Claude's 64-scenario JSON AND rerun one
decisive legacy-preset scenario through the same instrumentation.

Writes scratch_review/rv2sci_a8.json only (does not rewrite Claude's JSON).
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review import rvclaude_audit_carveout as M
from scratch_review.census_modviol_ab import PRESETS

CLAUDE_JSON = r"C:\Users\colin\wg_response\scratch_review\rvclaude_audit_carveout.json"
OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a8.json"


def parse_json():
    rows = json.load(open(CLAUDE_JSON, encoding="utf-8"))
    current = [r for r in rows if r.get("preset") == "current"]
    legacy = [r for r in rows if r.get("preset") == "legacy"]
    cur_ex = [r for r in current if r.get("off_ticks", 0) > 0]
    leg_ex = [r for r in legacy if r.get("off_ticks", 0) > 0]
    tot_off = sum(r.get("off_ticks", 0) for r in rows)
    tot_off_cur = sum(r.get("off_ticks", 0) for r in current)
    tot_off_leg = sum(r.get("off_ticks", 0) for r in legacy)
    tot_burn = sum(r.get("burned_ticks", 0) for r in rows)
    worst = []
    for r in rows:
        for w in r.get("worst") or []:
            # w = [tick, crew, mode, status, lead_min]
            worst.append(dict(map=r["map"], dist=r["dist"], crews=r["crews"],
                              inten=r["inten"], preset=r["preset"],
                              tick=w[0], crew=w[1], mode=w[2],
                              status=w[3], lead_min=w[4]))
    worst_sorted = sorted(worst, key=lambda x: x["lead_min"])
    return dict(
        n_rows=len(rows),
        n_current=len(current), n_legacy=len(legacy),
        n_current_with_exemption=len(cur_ex),
        n_legacy_with_exemption=len(leg_ex),
        tot_off_ticks=tot_off,
        tot_off_current=tot_off_cur,
        tot_off_legacy=tot_off_leg,
        tot_burned_ticks=tot_burn,
        current_exemptions=[{k: r[k] for k in
                             ("map", "dist", "crews", "inten", "preset",
                              "off_ticks", "burned_ticks", "violations",
                              "modes", "worst") if k in r}
                            for r in cur_ex],
        worst_leads=worst_sorted[:12],
        worst_lead_min=worst_sorted[0]["lead_min"] if worst_sorted else None,
    )


def rerun_decisive():
    """paradise_ca 8000 m, 4 crews, extreme, legacy — first row of
    rvclaude_carveout_small.py; JSON claims off_ticks=5, leads to -227.4 min."""
    extra = PRESETS["legacy"]
    r = M.run("paradise_ca", 8000.0, 4, 40, "extreme",
              extra.get("escape_hysteresis_margin_min", 20.0), extra=extra)
    return dict(
        scenario="paradise_ca d=8000 c4 extreme legacy",
        violations=r["violations"],
        burned_ticks=r["burned_ticks"],
        off_ticks=r["off_ticks"],
        modes=r["modes"],
        coated=r.get("coated"),
        worst=r["worst"],
        hundreds_past_p10=any(w[4] <= -100.0 for w in r["worst"]),
        min_lead=min((w[4] for w in r["worst"]), default=None),
    )


def main():
    parsed = parse_json()
    print("=== parsed Claude sweep JSON (independent recount) ===")
    print(f"  n={parsed['n_rows']}  current exemptions scenarios="
          f"{parsed['n_current_with_exemption']} ticks={parsed['tot_off_current']}")
    print(f"  legacy exemption ticks={parsed['tot_off_legacy']}  "
          f"total off={parsed['tot_off_ticks']}")
    for r in parsed["current_exemptions"]:
        print(f"  CURRENT: {r['map']} d={r['dist']} c{r['crews']} {r['inten']} "
              f"off={r['off_ticks']} worst={r['worst'][:3]}")
    print(f"  worst lead in JSON: {parsed['worst_lead_min']}")
    print("=== rerunning decisive legacy scenario ===", flush=True)
    run = rerun_decisive()
    print(f"  viol={run['violations']} burned={run['burned_ticks']} "
          f"EXEMPTED={run['off_ticks']} modes={run['modes']}")
    for w in run["worst"]:
        print(f"    tick {w[0]:3d} crew={w[1]:<10} mode={w[2]:<9} "
              f"status={w[3]:<10} eroded-P10 lead={w[4]:+7.1f} min")
    out = dict(parsed_claude_json=parsed, rerun=run)
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
