"""Belief-vs-physics real-fire study (2026-09-27) -- STEP 3: pool + tables.

Reads scratch_review/belief_vs_physics/results_<key>.json (the harness
domain) and results_wide_<key>.json (the 20 km-pad domain sensitivity), the
what-if rule variants' per-fire files (out/bvp_data/variant_results/) and
the physics side experiment (results_phys_grassonly.json); writes
results_pooled[_wide][_<variant>].json and results_pooled_phys_grassonly.json
beside the scripts and prints the markdown tables the report quotes
(`py -3 bvp_report.py > report_tables.md`).

Definitions (per scored step k -> k+1; see bvp_run.score):
  phase          OBSERVED growth over the step (poly_GISAcres per 24 h):
                 growing >= 10 %/day, moderate 2-10 %/day, stalled < 2 %/day
  area error     |ln(A_P50(t1) / A_obs(t1))|, A = burned area on the grid
                 (seed included); 0 = exact, 0.69 = off by 2x, 2.3 = 10x
  growth ratio   forecast new area by t1 (P50) / observed newly burned area
                 (T1 minus the seed; includes re-mapping noise)
  P10 adequacy   share of the observed newly burned BURNABLE cells inside the
                 P10 footprint by t1 -- crew safety; higher is safer (the
                 harness's p10_not_after_window). Pooled CELL-weighted.
  P50 hit rate   same for P50 (a calibrated median sits at or somewhat above
                 50 %: a cell seen burned at t1 burned at an unknown time
                 before t1). Pooled cell-weighted.
  false alarm    forecast NEW area by t1 (P50) outside perimeter k+1, acres;
                 also as % of the observed perimeter area.
Arms: phys = the deployed engine; bel = belief APPLIED with the P10 guard
(the build's default); belu = the unguarded variant (apply_p10).
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

PHASES = ("growing", "moderate", "stalled")
ARMS = (("phys", "phys"), ("bel", "bel_g"), ("belu", "bel_u"))


def action(step: dict) -> str:
    """What the APPLIED belief did to this step's forecast."""
    b = step["belief_at_k"]
    if b["state"] == "prior":
        return "prior: no evidence yet"
    if b["state"] == "reset":
        return "reset: surprise, physics"
    if not b["applied"]:
        return "new weather from hour 0: physics"
    m = b.get("nominal_mult_first_h")
    med = b.get("mult_p50")
    if b["rapid"] and m is not None and m >= 1.0 - 1e-9 and \
            med is not None and med < 1.0:
        return "rapid: held at physics"
    if m is not None and m < 1.0 - 1e-9:
        return "slowed"
    if m is not None and m > 1.0 + 1e-9:
        return "sped up"
    return "applied at 1.0x"


def _q(vals, q):
    v = sorted(vals)
    if not v:
        return None
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (pos - lo) * (v[hi] - v[lo])


def _med(vals):
    return statistics.median(vals) if vals else None


def step_rows(data: dict) -> list:
    rows = []
    for key, d in data.items():
        for s in d["steps"]:
            if "phys" not in s:
                continue
            r = {"fire": key, "k": s["k"], "phase": s["phase"],
                 "dt_h": s["dt_h"], "t0_s": s["t0_s"], "t1_s": s["t1_s"],
                 "gis0": s["gis0_ac"], "gis1": s["gis1_ac"],
                 "growth_pct_day": 100.0 * s["growth_frac_per_day"],
                 "action": action(s), "regime": s["extreme_regime"],
                 "mult_p50": s["belief_at_k"]["mult_p50"],
                 "rapid": s["belief_at_k"]["rapid"],
                 "h_new": s["belief_at_k"]["h_new"],
                 "state": s["belief_at_k"]["state"]}
            for arm, src in ARMS:
                m = s[src]
                r[arm] = m
            rows.append(r)
    return rows


def arm_summary(rows: list, arm: str) -> dict:
    la = [abs(r[arm]["log_area_p50"]) for r in rows
          if r[arm]["log_area_p50"] is not None]
    sla = [r[arm]["log_area_p50"] for r in rows
           if r[arm]["log_area_p50"] is not None]
    gr = [r[arm]["grow_p50_ac"] / r[arm]["obs_new_ac"] for r in rows
          if r[arm]["obs_new_ac"] > 0]
    mono = [r for r in rows if r[arm]["monotone"] and r[arm]["n_new_b"] > 0]
    n_cells = sum(r[arm]["n_new_b"] for r in mono)
    h10 = sum(r[arm]["n_hit_p10"] for r in mono)
    h50 = sum(r[arm]["n_hit_p50"] for r in mono)
    fa = [r[arm]["fa_p50_ac"] for r in rows]
    fa_rel = [r[arm]["fa_p50_ac"] / r[arm]["obs_area_ac"] for r in rows
              if r[arm]["obs_area_ac"] > 0]
    fa10 = [r[arm]["fa_p10_ac"] for r in rows]
    step_h10 = [r[arm]["hit_p10"] for r in mono]
    step_h50 = [r[arm]["hit_p50"] for r in mono]
    return {
        "n_steps": len(rows), "n_cell_steps": len(mono), "n_cells": n_cells,
        "area_err_median": _med(la), "area_err_q25": _q(la, 0.25),
        "area_err_q75": _q(la, 0.75),
        "area_signed_median": _med(sla),
        "area_ratio_median": (math.exp(_med(sla)) if sla else None),
        "growth_ratio_median": _med(gr),
        "p10_adequacy_cells": (h10 / n_cells) if n_cells else None,
        "p10_adequacy_step_median": _med(step_h10),
        "p10_adequacy_step_min": min(step_h10) if step_h10 else None,
        "p10_steps_below_90pct": sum(1 for v in step_h10 if v < 0.9),
        "p50_hit_cells": (h50 / n_cells) if n_cells else None,
        "p50_hit_step_median": _med(step_h50),
        "fa_p50_ac_median": _med(fa), "fa_p50_ac_mean": (
            statistics.mean(fa) if fa else None),
        "fa_p50_rel_median": _med(fa_rel),
        "fa_p10_ac_median": _med(fa10),
        "edge_p50_share": (sum(1 for r in rows if r[arm]["edge_p50"])
                           / len(rows)) if rows else None,
    }


def paired(rows: list, a: str = "phys", b: str = "bel") -> dict:
    d = [abs(r[b]["log_area_p50"]) - abs(r[a]["log_area_p50"]) for r in rows
         if r[a]["log_area_p50"] is not None and
         r[b]["log_area_p50"] is not None]
    return {"n": len(d), "better": sum(1 for x in d if x < -1e-9),
            "worse": sum(1 for x in d if x > 1e-9),
            "same": sum(1 for x in d if abs(x) <= 1e-9),
            "median_delta_area_err": _med(d)}


def group(rows: list, keyf) -> dict:
    g: dict = {}
    for r in rows:
        g.setdefault(keyf(r), []).append(r)
    return g


def summarize(data: dict) -> dict:
    rows = step_rows(data)
    out = {"n_fires": len(data), "n_steps_scored": len(rows),
           "n_steps_total": sum(len(d["steps"]) for d in data.values()),
           "by_phase": {}, "by_fire": {}, "actions": {}, "all": {}}
    for ph in PHASES + ("all",):
        rr = rows if ph == "all" else [r for r in rows if r["phase"] == ph]
        if not rr:
            continue
        rec = {"n_steps": len(rr), "n_fires": len({r["fire"] for r in rr}),
               "fires": sorted({r["fire"] for r in rr})}
        for arm, _src in ARMS:
            rec[arm] = arm_summary(rr, arm)
        rec["paired_area"] = paired(rr)
        acts: dict = {}
        for r in rr:
            acts[r["action"]] = acts.get(r["action"], 0) + 1
        rec["actions"] = acts
        # per-fire medians of the area error (between-fire spread)
        pf = {}
        for fire, fr in group(rr, lambda r: r["fire"]).items():
            pf[fire] = {
                "n": len(fr),
                "phys_area_err": _med([abs(r["phys"]["log_area_p50"]) for r in fr]),
                "bel_area_err": _med([abs(r["bel"]["log_area_p50"]) for r in fr])}
        rec["per_fire_area_err"] = pf
        if ph == "all":
            out["all"] = rec
        else:
            out["by_phase"][ph] = rec
    for fire, fr in group(rows, lambda r: r["fire"]).items():
        d = data[fire]
        rec = {"name": d["name"], "n_scored": len(fr),
               "n_steps": len(d["steps"]),
               "phases": {ph: sum(1 for r in fr if r["phase"] == ph)
                          for ph in PHASES},
               "regime_steps": sum(1 for r in fr if r["regime"])}
        for arm, _src in ARMS:
            rec[arm] = arm_summary(fr, arm)
        rec["paired_area"] = paired(fr)
        acts: dict = {}
        for r in fr:
            acts[r["action"]] = acts.get(r["action"], 0) + 1
        rec["actions"] = acts
        br = d["belief_rows"]
        rec["final_mult"] = [br[-1]["status"]["mult_p10"],
                             br[-1]["status"]["mult_p50"],
                             br[-1]["status"]["mult_p90"]]
        rec["n_resets"] = sum(1 for b in br for e in (b["events"] or [])
                              if e.get("kind") == "reset")
        meds = [(i, b["status"]["mult_p50"]) for i, b in enumerate(br)
                if b["status"]["mult_p50"] is not None]
        rec["captures_to_below_0p3"] = next(
            (i for i, m in meds if m < 0.3), None)
        rec["captures_to_below_0p1"] = next(
            (i for i, m in meds if m < 0.1), None)
        rec["mult_p50_range"] = ([min(m for _i, m in meds),
                                  max(m for _i, m in meds)] if meds else None)
        rec["share_captures_p90_below_1"] = (
            sum(1 for b in br if b["status"]["mult_p90"] is not None
                and b["status"]["mult_p90"] < 1.0) / len(br))
        rec["n_rapid"] = sum(1 for b in br if b["status"]["rapid"])
        rec["n_learned"] = sum(1 for b in br if b["status"]["state"] == "learned")
        wins = [b["window"] for b in br if b.get("window")]
        rec["n_windows"] = len(wins)
        rec["n_windows_censored"] = sum(1 for w in wins if w.get("cens"))
        rec["n_windows_modeled"] = sum(1 for w in wins
                                       if w.get("vnom") is not None)
        out["by_fire"][fire] = rec
    for r in rows:
        out["actions"][r["action"]] = out["actions"].get(r["action"], 0) + 1
    out["tracking"] = tracking(data)
    return out


def tracking(data: dict) -> dict:
    """What TRACKING alone would have displayed and how good its numbers
    were: per completed window, the belief's posterior-predictive median
    growth (made before the window was seen; None when the window was
    censored -- the store then makes no prediction) against the observed
    growth and against the physics nominal's growth for the same window."""
    rows = []
    for key, d in data.items():
        for b in d["belief_rows"]:
            w = b.get("window")
            if not w or w.get("vnom") is None:
                continue
            rows.append({"fire": key, "t_s": w["tb"],
                         "dt_h": (w["tb"] - w["ta"]) / 3600.0,
                         "obs": w["obs_growth_ac"],
                         "a0": w["aa"] / 4046.8564224,
                         "phys_nom": w.get("phys_nominal_growth_ac"),
                         "pred50": w.get("pred50"), "pred90": w.get("pred90"),
                         "cens": bool(w.get("cens")),
                         "surprise": bool(w.get("surprise"))})

    def err(pred, obs, a0):
        f = max(10.0, 0.005 * a0)          # floor: 0.5 % of the fire / 10 ac
        return abs(math.log((max(pred, 0.0) + f) / (max(obs, 0.0) + f)))
    with_pred = [r for r in rows if r["pred50"] is not None]
    out = {"n_windows_modeled": len(rows),
           "n_censored": sum(1 for r in rows if r["cens"]),
           "n_with_belief_prediction": len(with_pred),
           "n_surprise": sum(1 for r in rows if r["surprise"])}
    if with_pred:
        out["belief_pred_err_median"] = _med(
            [err(r["pred50"], r["obs"], r["a0"]) for r in with_pred])
        out["phys_nom_err_median_same_windows"] = _med(
            [err(r["phys_nom"], r["obs"], r["a0"]) for r in with_pred])
        out["obs_inside_belief_p90_share"] = sum(
            1 for r in with_pred if r["obs"] <= r["pred90"]) / len(with_pred)
    out["phys_nom_over_obs_median_all"] = _med(
        [(r["phys_nom"] + max(10.0, 0.005 * r["a0"])) /
         (max(r["obs"], 0.0) + max(10.0, 0.005 * r["a0"])) for r in rows
         if r["phys_nom"] is not None])
    out["rows"] = rows
    return out


def _p(v, nd=0):
    return "-" if v is None else f"{100 * v:.{nd}f} %"


def _n(v, fmt="{:.2f}"):
    return "-" if v is None else fmt.format(v)


def print_tables(s: dict, title: str) -> None:
    print(f"\n### {title}: {s['n_fires']} fires, {s['n_steps_scored']} scored "
          f"steps of {s['n_steps_total']}\n")
    print("| phase | steps (fires) | area error, median [IQR] phys -> belief | "
          "median forecast/observed area phys -> belief | P10 adequacy phys / "
          "belief / unguarded | P50 hit phys -> belief | false alarm P50, "
          "median ac (% of fire) phys -> belief | belief better / worse / same |")
    print("|---|---|---|---|---|---|---|---|")
    for ph in PHASES + ("all",):
        rec = s["all"] if ph == "all" else s["by_phase"].get(ph)
        if not rec:
            continue
        p, b, u = rec["phys"], rec["bel"], rec["belu"]
        pa = rec["paired_area"]
        print(f"| {ph} | {rec['n_steps']} ({rec['n_fires']}) | "
              f"{_n(p['area_err_median'])} [{_n(p['area_err_q25'])}-"
              f"{_n(p['area_err_q75'])}] -> {_n(b['area_err_median'])} "
              f"[{_n(b['area_err_q25'])}-{_n(b['area_err_q75'])}] | "
              f"{_n(p['area_ratio_median'], '{:.1f}x')} -> "
              f"{_n(b['area_ratio_median'], '{:.1f}x')} | "
              f"{_p(p['p10_adequacy_cells'], 1)} / {_p(b['p10_adequacy_cells'], 1)}"
              f" / {_p(u['p10_adequacy_cells'], 1)} | "
              f"{_p(p['p50_hit_cells'])} -> {_p(b['p50_hit_cells'])} | "
              f"{_n(p['fa_p50_ac_median'], '{:,.0f}')} "
              f"({_p(p['fa_p50_rel_median'])}) -> "
              f"{_n(b['fa_p50_ac_median'], '{:,.0f}')} "
              f"({_p(b['fa_p50_rel_median'])}) | "
              f"{pa['better']} / {pa['worse']} / {pa['same']} |")
    print("\n| phase | what the applied belief did (steps) |")
    print("|---|---|")
    for ph in PHASES + ("all",):
        rec = s["all"] if ph == "all" else s["by_phase"].get(ph)
        if rec:
            print(f"| {ph} | " + "; ".join(
                f"{k} {v}" for k, v in sorted(rec["actions"].items(),
                                              key=lambda kv: -kv[1])) + " |")
    print("\n| fire | scored steps (grow/mod/stall) | area error phys -> belief "
          "(median) | P10 adequacy phys / belief / unguarded | P50 hit phys "
          "-> belief | learned multiplier at the end (P10-P50-P90) | windows "
          "modeled (censored) | resets | rapid captures | slowed steps |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for fire, rec in s["by_fire"].items():
        ph = rec["phases"]
        fm = rec["final_mult"]
        fm_s = ("prior" if fm[1] is None else
                f"{fm[0]:.2f}-{fm[1]:.2f}-{fm[2]:.2f}x")
        print(f"| {rec['name']} | {rec['n_scored']} ({ph['growing']}/"
              f"{ph['moderate']}/{ph['stalled']}) | "
              f"{_n(rec['phys']['area_err_median'])} -> "
              f"{_n(rec['bel']['area_err_median'])} | "
              f"{_p(rec['phys']['p10_adequacy_cells'], 1)} / "
              f"{_p(rec['bel']['p10_adequacy_cells'], 1)} / "
              f"{_p(rec['belu']['p10_adequacy_cells'], 1)} | "
              f"{_p(rec['phys']['p50_hit_cells'])} -> "
              f"{_p(rec['bel']['p50_hit_cells'])} | {fm_s} | "
              f"{rec['n_windows_modeled']} ({rec['n_windows_censored']}) | "
              f"{rec['n_resets']} | {rec['n_rapid']} | "
              f"{rec['actions'].get('slowed', 0)} |")
    print("\n| fire | perimeters until learned < 0.3x / < 0.1x | learned median "
          "over the life (min-max) | captures with learned P90 < 1x |")
    print("|---|---|---|---|")
    for fire, rec in s["by_fire"].items():
        rng = rec["mult_p50_range"]
        print(f"| {rec['name']} | {_n(rec['captures_to_below_0p3'], '{:d}')} / "
              f"{_n(rec['captures_to_below_0p1'], '{:d}')} | "
              f"{'-' if rng is None else f'{rng[0]:.2f}-{rng[1]:.2f}x'} | "
              f"{_p(rec['share_captures_p90_below_1'])} |")
    t = s["tracking"]
    print(f"\nTracking: {t['n_windows_modeled']} modeled windows, "
          f"{t['n_censored']} censored, {t['n_with_belief_prediction']} with a "
          f"belief prediction; surprises {t['n_surprise']}; physics nominal / "
          f"observed growth median {_n(t.get('phys_nom_over_obs_median_all'), '{:.1f}x')}; "
          f"belief-pred error {_n(t.get('belief_pred_err_median'))} vs physics "
          f"{_n(t.get('phys_nom_err_median_same_windows'))} on the same windows; "
          f"observed <= belief P90 in "
          f"{_p(t.get('obs_inside_belief_p90_share'))}")


def load_set(domain: str, bvar: str) -> dict:
    fires = json.load(open(os.path.join(_HERE, "fires.json")))
    tag = ("results_wide" if domain else "results") + \
        (f"_{bvar}" if bvar else "")
    base = _HERE if not bvar else os.path.join(
        os.path.dirname(os.path.dirname(_HERE)), "out", "bvp_data",
        "variant_results")
    out = {}
    for key in fires:
        p = os.path.join(base, f"{tag}_{key}.json")
        if os.path.exists(p):
            out[key] = json.load(open(p))
    return out


def bootstrap(data: dict, n_boot: int = 2000, seed: int = 20260927) -> dict:
    """Fire-cluster bootstrap (fires resampled with replacement; each
    fire's steps kept together -- steps within a fire are not independent)
    of the per-phase headline numbers; 5th-95th percentile intervals."""
    import random
    rng = random.Random(seed)
    rows = step_rows(data)
    by_fire = group(rows, lambda r: r["fire"])
    fires = sorted(by_fire)
    stats: dict = {}

    def calc(rr):
        res = {}
        for ph in PHASES + ("all",):
            x = rr if ph == "all" else [r for r in rr if r["phase"] == ph]
            if len(x) < 3:
                continue
            lp = [abs(r["phys"]["log_area_p50"]) for r in x]
            lb = [abs(r["bel"]["log_area_p50"]) for r in x]
            dd = [b - a for a, b in zip(lp, lb)]
            mono = [r for r in x if r["phys"]["monotone"]
                    and r["phys"]["n_new_b"] > 0]
            nc = sum(r["phys"]["n_new_b"] for r in mono)
            res[ph] = {
                "phys_area_err": statistics.median(lp),
                "bel_area_err": statistics.median(lb),
                "delta_area_err_median": statistics.median(dd),
                "phys_p50_hit": (sum(r["phys"]["n_hit_p50"] for r in mono) / nc
                                 if nc else None),
                "bel_p50_hit": (sum(r["bel"]["n_hit_p50"] for r in mono) / nc
                                if nc else None),
                "belu_p10": (sum(r["belu"]["n_hit_p10"] for r in mono) / nc
                             if nc else None),
                "phys_fa_rel": statistics.median(
                    [r["phys"]["fa_p50_ac"] / r["phys"]["obs_area_ac"]
                     for r in x]),
                "bel_fa_rel": statistics.median(
                    [r["bel"]["fa_p50_ac"] / r["bel"]["obs_area_ac"]
                     for r in x])}
        return res
    for _ in range(n_boot):
        pick = [rng.choice(fires) for _f in fires]
        rr = [r for f in pick for r in by_fire[f]]
        for ph, rec in calc(rr).items():
            for k, v in rec.items():
                if v is not None:
                    stats.setdefault(ph, {}).setdefault(k, []).append(v)
    out = {}
    for ph, recs in stats.items():
        out[ph] = {k: [_q(v, 0.05), _q(v, 0.95)] for k, v in recs.items()}
    return out


VARIANT_NAMES = {"": "as built", "nonovel": "new-weather rule off",
                 "norapid": "rapid rule off", "free": "both rules off"}


def print_variants(summaries: dict, title: str) -> None:
    print(f"\n### {title}: the belief AS BUILT vs what-if rule settings "
          "(same learning, same physics)\n")
    print("| phase | arm | area error (median) | P10 adequacy guarded / "
          "unguarded | P50 hit | false alarm P50 (median % of fire) | "
          "better / worse / same vs physics |")
    print("|---|---|---|---|---|---|---|")
    for ph in PHASES + ("all",):
        base = summaries.get("")
        if not base:
            return
        rec0 = base["all"] if ph == "all" else base["by_phase"].get(ph)
        if not rec0:
            continue
        p = rec0["phys"]
        print(f"| {ph} | physics | {_n(p['area_err_median'])} | "
              f"{_p(p['p10_adequacy_cells'], 1)} | {_p(p['p50_hit_cells'])} | "
              f"{_p(p['fa_p50_rel_median'])} | - |")
        for bv, s in summaries.items():
            rec = s["all"] if ph == "all" else s["by_phase"].get(ph)
            if not rec:
                continue
            b, u, pa = rec["bel"], rec["belu"], rec["paired_area"]
            print(f"| {ph} | belief, {VARIANT_NAMES[bv]} | "
                  f"{_n(b['area_err_median'])} | "
                  f"{_p(b['p10_adequacy_cells'], 1)} / "
                  f"{_p(u['p10_adequacy_cells'], 1)} | "
                  f"{_p(b['p50_hit_cells'])} | {_p(b['fa_p50_rel_median'])} | "
                  f"{pa['better']} / {pa['worse']} / {pa['same']} |")


def physics_variant_table(name: str = "grassonly") -> dict:
    """The side experiment: the deployed physics vs the same physics with
    one knob changed (bvp_physics_variant.py), same steps and scoring."""
    p = os.path.join(_HERE, f"results_phys_{name}.json")
    if not os.path.exists(p):
        return {}
    var = json.load(open(p))["fires"]
    base = load_set("", "")
    rows = []
    for key, d in base.items():
        for s in d["steps"]:
            if "phys" not in s:
                continue
            v = var.get(key, {}).get(str(s["k"]))
            if v is None:
                continue
            rows.append({"fire": key, "phase": s["phase"], "phys": s["phys"],
                         "bel": s["bel_g"], "var": v["phys"]})
    out = {}
    print(f"\n### Side experiment: deployed physics vs physics with "
          f"{name} (same steps, same scoring)\n")
    print("| phase | steps | area error: deployed / variant / belief as built "
          "| P10 adequacy deployed / variant | P50 hit deployed / variant | "
          "false alarm P50 (% of fire) deployed / variant |")
    print("|---|---|---|---|---|---|")
    for ph in PHASES + ("all",):
        rr = rows if ph == "all" else [r for r in rows if r["phase"] == ph]
        if not rr:
            continue
        a, v, b = (arm_summary(rr, "phys"), arm_summary(rr, "var"),
                   arm_summary(rr, "bel"))
        out[ph] = {"deployed": a, "variant": v, "belief": b}
        print(f"| {ph} | {len(rr)} | {math.exp(a['area_err_median']):.1f}x / "
              f"{math.exp(v['area_err_median']):.1f}x / "
              f"{math.exp(b['area_err_median']):.1f}x | "
              f"{_p(a['p10_adequacy_cells'], 1)} / {_p(v['p10_adequacy_cells'], 1)} | "
              f"{_p(a['p50_hit_cells'])} / {_p(v['p50_hit_cells'])} | "
              f"{_p(a['fa_p50_rel_median'])} / {_p(v['fa_p50_rel_median'])} |")
    with open(os.path.join(_HERE, f"results_pooled_phys_{name}.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    return out


def main(argv) -> int:
    physics_variant_table("grassonly")
    for domain in ("", "wide"):
        summaries = {}
        for bvar in ("", "nonovel", "norapid", "free"):
            data = load_set(domain, bvar)
            if not data:
                continue
            s = summarize(data)
            if not bvar:
                s["bootstrap"] = bootstrap(data)
            summaries[bvar] = s
            name = ("results_pooled" + ("_wide" if domain else "")
                    + (f"_{bvar}" if bvar else "") + ".json")
            with open(os.path.join(_HERE, name), "w", encoding="utf-8",
                      newline="\n") as f:
                json.dump(s, f, indent=1, sort_keys=True)
        if not summaries:
            continue
        label = ("WIDE domain (20 km pad)" if domain else
                 "HARNESS domain (4 km pad)")
        if "" in summaries:
            print_tables(summaries[""], label)
            bs = summaries[""]["bootstrap"]
            print("\nFire-cluster bootstrap, 5-95 % intervals:")
            for ph, rec in bs.items():
                print(f"  {ph:9s} " + "; ".join(
                    f"{k} [{_n(v[0])}, {_n(v[1])}]" for k, v in rec.items()))
        if len(summaries) > 1:
            print_variants(summaries, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
