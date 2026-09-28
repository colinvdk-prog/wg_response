"""Fire belief v2 (2026-09-28): the before/after tables from v2_final.json
(harness maps) and v2_final_wide.json (20 km-pad maps), the LOFO folds
(v2_lofo.json) and the rapid statistics (v2_rapid.json). Writes
v2_tables.md beside itself and prints it.

Arms (all P10 GUARDED except the 'unguarded' column):
  physics       the deployed engine
  as built      the belief as built (hourly new-weather rule, legacy rapid)
  v2            daily rule (ratio 1.5, 60 deg, 4 m/s) + calibrated rapid
                noise (k 1.0) + persistence 2  (watch --fire-belief-v2)
  daily only    the daily rule with the legacy rapid rule
  rapid v2 only the hourly rule with the v2 rapid rule
  both off      the what-if of the first study: no new-weather or rapid rule
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import v2_eval as V  # noqa: E402

PHASES = ("growing", "moderate", "stalled", "all")
NAMED = [("willow", 11, "Willow Jul 12"), ("willow", 13, "Willow Jul 14"),
         ("grasshopper", 8, "Grasshopper Aug 4"),
         ("littlegiant", 28, "Little Giant Aug 21"),
         ("timber", 7, "Timber Aug 21"),
         ("littlegiant", 16, "Little Giant Aug 7 (the 14,780-ac run)"),
         ("willow", 9, "Willow Jul 10 (flare-up after a slow week)"),
         ("hagen", 17, "Hagen Aug 9")]


def arms(final: dict) -> list:
    v2 = final["_v2_name"]
    daily_only = v2.split("+")[0]
    return [("physics", "as_built", "phys"), ("as built", "as_built", "bel"),
            ("v2", v2, "bel"), ("daily only", daily_only, "bel"),
            ("rapid v2 only", "cal1+p2", "bel"),
            ("both rules off", "no_rules", "bel")]


def fx(v):
    return "-" if v is None else f"{v:.2f}x"


def pc(v, nd=0):
    return "-" if v is None else f"{100 * v:.{nd}f} %"


def table(final: dict, title: str) -> list:
    out = [f"### {title}", "",
           "| phase | arm | P50 area error (median factor) | P50 catch | "
           "late-P50 steps (< 50 % caught; of them on >= 2 %/day growth) | "
           "new ground outside P50 (acres, summed) | "
           "false alarm (median % of fire) | P10 adequacy (guarded / "
           "unguarded) | belief applied | new-pattern hour (median) | whole "
           "window covered | rapid flags |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for ph in PHASES:
        for label, cname, arm in arms(final):
            if cname not in final:
                continue
            s = final[cname]["summary"].get(ph)
            if not s:
                continue
            a = s[arm]
            u = s["belu"] if arm == "bel" else s["phys"]
            if label == "physics":
                extra = "- | - | - | -"
            else:
                extra = (f"{s['n_applied']} / {a['n']} | "
                         f"{'-' if s['h_new_median'] is None else round(s['h_new_median'])} | "
                         f"{pc(s['full_window_share'])} | {s['n_rapid']}")
            out.append(
                f"| {ph} | {label} | {fx(a['area_factor_median'])} | "
                f"{pc(a['p50_catch_cells'])} | {a['late_steps']} "
                f"({a['late_steps_grow2']}) | {a['missed_new_ac']:,.0f} | "
                f"{pc(a['fa_rel_median'])} | "
                f"{pc(a['p10_adequacy_cells'], 1)} / "
                f"{pc(u['p10_adequacy_cells'], 1)} | {extra} |")
    return out


def named(final: dict) -> list:
    out = ["| case | observed | physics | as built | v2 | daily only | "
           "both rules off |", "|---|---|---|---|---|---|---|"]
    idx = {}
    for label, cname, arm in arms(final):
        if cname in final:
            idx[label] = {(s["fire"], s["k"]): s for s in final[cname]["steps"]}
    for fire, k, name in NAMED:
        s0 = idx["as built"].get((fire, k))
        if s0 is None:
            continue
        cells = [f"{s0['phys']['hit_p50']:.0%}"]
        for label in ("as built", "v2", "daily only", "both rules off"):
            s = idx.get(label, {}).get((fire, k))
            if s is None:
                cells.append("-")
                continue
            why = ("rapid" if s["rapid"] else
                   ("new weather h" + str(s["h_eff"]) if s["applied"] and
                    s["h_eff"] < math.ceil(s["dt_h"] - 1e-9) else
                    ("slowed all window" if s["applied"] else "physics")))
            cells.append(f"{s['bel']['hit_p50']:.0%} ({why})")
        out.append(f"| {name} | +{s0['gis1'] - s0['gis0']:,.0f} ac "
                   f"({s0['growth_pct_day']:.1f} %/day) | " + " | ".join(cells)
                   + " |")
    return out


def lofo_lines() -> list:
    p = os.path.join(_HERE, "v2_lofo.json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p))
    out = ["| held-out fire | chosen (ties broken by name) | best-tied set size | "
           "held-out P50 area | held-out P50 catch | held-out late steps |",
           "|---|---|---|---|---|---|"]
    for f in d["folds"]:
        t = f["test"]
        out.append(f"| {f['held_out']} | {f['chosen']} | {len(f['ties'])} | "
                   f"{fx(t['area_factor_median'])} | {pc(t['p50_catch_cells'])} | "
                   f"{t['late_steps']} ({t['late_steps_grow2']}) |")
    out += ["", "Threshold values in each fold's best-tied set (of 11 folds): "
            + json.dumps(d["value_counts_ties_included"], sort_keys=True)]
    h = d["held_out_summary"]
    out += ["", "| phase | physics | LOFO held-out v2 | P50 catch | late (>= 2 %/day) "
            "| false alarm physics -> v2 |", "|---|---|---|---|---|---|"]
    for ph in PHASES:
        s = h[ph]
        out.append(f"| {ph} | {fx(s['phys']['area_factor_median'])} | "
                   f"{fx(s['bel']['area_factor_median'])} | "
                   f"{pc(s['bel']['p50_catch_cells'])} | {s['bel']['late_steps']} "
                   f"({s['bel']['late_steps_grow2']}) | "
                   f"{pc(s['phys']['fa_rel_median'])} -> {pc(s['bel']['fa_rel_median'])} |")
    # sensitivity band over the whole grid
    sens = d["sensitivity"]
    af = sorted(v["all"]["area_factor_median"] for v in sens.values())
    lt = sorted(v["all"]["late_steps"] for v in sens.values())
    lg = sorted(v["all"]["late_steps_grow2"] for v in sens.values())
    fa = sorted(v["all"]["fa_rel_median"] for v in sens.values())
    out += ["", f"Sensitivity over all {len(sens)} daily threshold sets (each with "
            f"the v2 rapid rule), all 11 fires: P50 area error "
            f"{af[0]:.2f}-{af[-1]:.2f}x, late-P50 steps {lt[0]}-{lt[-1]} "
            f"({lg[0]}-{lg[-1]} on >= 2 %/day growth), false alarm "
            f"{100 * fa[0]:.0f}-{100 * fa[-1]:.0f} % of the fire's area."]
    return out


def rapid_lines() -> list:
    p = os.path.join(_HERE, "v2_rapid.json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p))
    out = ["| series | rapid rule | growing steps flagged | moderate | stalled "
           "steps flagged | of them grew >= 1,000 ac/day | recent window truly "
           "rapid: flagged | recent window not rapid: flagged |",
           "|---|---|---|---|---|---|---|---|"]
    for series in ("daily", "live"):
        for v in ("legacy", "calibrated_k1", "persist_only",
                  "v2 (calibrated k1 + persist 2)", "calibrated_k2"):
            t = d["variants"][v][series]

            def f(x):
                return (f"{t[x]['flagged']}/{t[x]['n']} ({100 * t[x]['rate']:.0f} %)"
                        if t[x]["n"] else "-")
            out.append(f"| {series} | {v} | {f('growing')} | {f('moderate')} | "
                       f"{f('stalled')} | {f('stalled_with_1000ac_day')} | "
                       f"{f('recent_true')} | {f('recent_false')} |")
    out += ["", "Little Giant, the forecasts of Aug 4-8 (daily series):"]
    for v, rows in d["little_giant"].items():
        out.append(f"- {v}: " + "; ".join(
            f"{r['t']} {'RAPID' if r['rapid'] else 'not rapid'}" for r in rows))
    return out


def main() -> int:
    lines = ["# Fire belief v2: tables (generated by v2_tables.py)", ""]
    for name, title in (("v2_final.json", "Harness maps (bbox + 4 km)"),
                        ("v2_final_wide.json", "Wide maps (bbox + 20 km)")):
        p = os.path.join(_HERE, name)
        if not os.path.exists(p):
            continue
        final = json.load(open(p))
        lines += table(final, title) + [""]
        lines += [f"Named cases, {title} (P50 catch of the newly burned ground; "
                  "why the belief did what it did):", ""] + named(final) + [""]
    lines += ["### Leave-one-fire-out (harness maps)", ""] + lofo_lines() + [""]
    lines += ["### Rapid rule", ""] + rapid_lines() + [""]
    text = "\n".join(lines)
    with open(os.path.join(_HERE, "v2_tables.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
