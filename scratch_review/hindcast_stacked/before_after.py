"""Before/after of every documented hindcast calibration number the
stacked-rings fix (2026-09-27) can move.

Three values per number:
  documented  recomputed from the committed result file the document was
              written from (so a number here matches the document's text)
  before      today's engine on the PRE-FIX corpora (worktree at 0bbd430)
  after       today's engine on the REPAIRED corpora (this branch)
before -> after is the fix; documented != before would be engine drift since
the document was written (reported, not attributed to the fix).

Each block recomputes its statistic with the ORIGINAL generator's formula
(named per block), including the upper-middle "median" (sorted[n // 2]) the
July scripts used, so every row compares like with like.

Reads scratch_review/hindcast_stacked/results/{before,after}/*.json (from
run_all.py) and the committed record files (the pinned default replay from
git at 0bbd430); writes before_after.json next to this script. Offline.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import _irwin_slug, _load_all_incidents, _incident_domain  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

RES = os.path.join(HERE, "results")
BEFORE_ROOT = os.path.join(os.path.dirname(ROOT), "hindcast-before")
FIELDS = set(H.PairMetrics.__dataclass_fields__)
OOS_CUT = datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp()
AUG_CUT = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
CORPUS_DIR = {"pinned": "tools/hindcast_data",
              "oos": "scratch_review/hindcast_oos_data",
              "aug": "scratch_review/hindcast_aug_data"}
MATRIX_ORDER = ["F_nofloor", "H_nofloor_me050", "H_nofloor_me071", "A_deployed",
                "B_gate", "C_gate_grass", "D_gate_me035", "D_gate_me050",
                "D_gate_me071", "E_gategrass_me050", "E_gategrass_me071",
                "I_gategrass05_me050", "I_gategrass05_me071", "G_gate_p50",
                "J_regime_I071", "L_regime_I_noband", "M_D071_piecewise",
                "N_D071_phi", "O_D071_both"]


def jload(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def result(state, corpus, label):
    return jload(os.path.join(RES, state, f"{corpus}__{label}.json"))


def pm(pairs):
    return [H.PairMetrics(**{k: v for k, v in p.items() if k in FIELDS})
            for p in pairs]


def upper_mid(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else float("nan")


def fdt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d %H:%M")


def pct(num, den):
    return 100.0 * num / den if den else float("nan")


# ---------------------------------------------------------------------------
# the calibration matrices (calmatrix_report.py -> tools/hindcast_lib.pool_metrics)
# ---------------------------------------------------------------------------

def matrix_flat(m):
    pit = m.get("pit_summary") or {}
    bins = {b["label"]: b.get("p10_adequacy") for b in m.get("window_length_bins", [])}
    return {
        "pairs": m["n_pairs"], "cells": m["n_cells"], "incidents": m.get("n_incidents"),
        "p10_adequacy": 100 * m["p10_adequacy_frac"],
        "late_miss": 100 * m["late_miss_frac"],
        "never_reached": 100 * m.get("never_reached_frac", 0.0),
        "area_p10": m.get("median_area_ratio_p10"),
        "null_adequacy": 100 * (m.get("null_adequacy_frac") or 0.0),
        "skill": m.get("skill"),
        "pit_extreme": 100 * pit.get("extreme_bins_frac", 0.0),
        "pit_early": 100 * pit.get("lo_bin_frac", 0.0),
        "pit_late": 100 * pit.get("hi_bin_frac", 0.0),
        "pit_chi2": pit.get("chi2_flatness"),
        "head_p10": m.get("median_head_ratio_p10"),
        "head_p50": m.get("median_head_ratio_p50"),
        "head_nominal": m.get("median_head_ratio_nominal"),
        "bin_le6": None if bins.get("<=6h") is None else 100 * bins["<=6h"],
        "bin_6_12": None if bins.get("6-12h") is None else 100 * bins["6-12h"],
        "bin_12_24": None if bins.get("12-24h") is None else 100 * bins["12-24h"],
        "bin_gt24": None if bins.get(">24h") is None else 100 * bins[">24h"],
        "eq_incident_mean": 100 * (m.get("equal_incident_mean_p10_adequacy") or 0.0),
        "loo_min": 100 * (m.get("loo_incident_p10_adequacy_min") or 0.0),
        "loo_max": 100 * (m.get("loo_incident_p10_adequacy_max") or 0.0),
    }


def block_matrix():
    out = {}
    for corpus in ("aug", "oos"):
        for label in MATRIX_ORDER:
            doc_path = os.path.join(ROOT, "scratch_review", "calmatrix",
                                    f"{corpus}_{label}.json")
            doc = jload(doc_path)
            bef = result("before", corpus, label)
            aft = result("after", corpus, label)
            out[f"{corpus}/{label}"] = {
                "documented": matrix_flat(H.pool_metrics(pm(doc["pairs"]))),
                "before": matrix_flat(H.pool_metrics(pm(bef["pairs"]))),
                "after": matrix_flat(H.pool_metrics(pm(aft["pairs"]))),
                "documented_equals_before_pairs": doc["pairs"] == bef["pairs"],
            }
    return out


# ---------------------------------------------------------------------------
# pinned corpus: first pass report, gust sweep, calibration-set realism
# ---------------------------------------------------------------------------

def sweep_pool(pairs):
    """scratch_review/gust_sweep.py pooled(): every pair's cells in n."""
    n = sum(p["n_new_burnable"] for p in pairs)
    heads = [p["head_m_p50"] / p["obs_head_m"] for p in pairs if p.get("obs_head_m")]
    return {"pairs_scored": sum(1 for p in pairs if p["n_new_burnable"] > 0),
            "cells": n,
            "coverage": pct(sum(p["covered_possible"] for p in pairs), n),
            "late": pct(sum(p["late_miss"] for p in pairs), n),
            "early": pct(sum(p["early_miss"] for p in pairs), n),
            "p50_in": pct(sum(p["p50_in_window"] for p in pairs), n),
            "never": pct(sum(p["never_reached"] for p in pairs), n),
            "head_upper_mid": upper_mid(heads)}


def fuel_late(pairs):
    agg = {}
    for p in pairs:
        for cls, rec in (p.get("by_fuel_class") or {}).items():
            a = agg.setdefault(cls, {"n": 0, "late": 0})
            a["n"] += rec.get("n", 0)
            a["late"] += rec.get("late", 0)
    return {c: {"cells": a["n"], "late_pct": pct(a["late"], a["n"])}
            for c, a in sorted(agg.items())}


def pit_bins(pairs):
    tot = None
    for p in pairs:
        h = p.get("pit_hist") or []
        if not h:
            continue
        tot = list(h) if tot is None else [a + b for a, b in zip(tot, h)]
    return tot


def pair_rows(pairs):
    rows = []
    for p in pairs:
        nb = p["n_new_burnable"]
        rows.append({"name": p["name"], "t0": fdt(p["t0_s"]), "t0_s": p["t0_s"],
                     "dt_h": round(p["dt_h"], 1), "new_burnable": nb,
                     "new_nonburnable": p.get("n_new_nonburnable", 0),
                     "p10_adequacy": pct(p["p10_not_after_window"], nb),
                     "late": pct(p["late_miss"], nb),
                     "head_p50": (p["head_m_p50"] / p["obs_head_m"]) if p.get("obs_head_m") else None,
                     "area_p50": (p["modeled_new_area_ac_p50"] / p["obs_new_area_ac"]) if p.get("obs_new_area_ac") else None,
                     "obs_new_area_ac": p.get("obs_new_area_ac"),
                     "obs_head_km": (p.get("obs_head_m") or 0) / 1000.0,
                     "p50_head_km": (p.get("head_m_p50") or 0) / 1000.0,
                     "notes": p.get("notes") or []})
    return rows


def block_pinned():
    out = {"first_pass": {}, "gust_sweep": {}, "cal_realism": {}}
    # the default replay_results.json was rewritten by this fix's re-run, so
    # the documented one is read from git (0bbd430 = pre-fix main)
    doc_rr = json.loads(subprocess.run(
        ["git", "show", "0bbd430:tools/hindcast_data/replay_results.json"],
        cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8"))
    for state, d in (("documented", doc_rr),
                     ("before", result("before", "pinned", "b0.0")),
                     ("after", result("after", "pinned", "b0.0"))):
        pairs, wide = d["pairs"], d.get("pairs_wide") or []
        out["first_pass"][state] = {
            "pool": sweep_pool(pairs), "wide": sweep_pool(wide) if wide else None,
            "fuel": fuel_late(pairs), "pit": pit_bins(pairs),
            "pairs": pair_rows(pairs), "skips": d.get("skips"),
            "true_median_head_p50": H.pool_metrics(pm(pairs)).get("median_head_ratio_p50")}
    doc_sweep = {str(r["blend"]): r for r in jload(os.path.join(ROOT, "scratch_review", "gust_sweep.json"))}
    for b in ("0.0", "0.25", "0.5", "0.75", "1.0"):
        r = doc_sweep[str(float(b))] if str(float(b)) in doc_sweep else doc_sweep.get(b)
        out["gust_sweep"][b] = {
            "documented": {"coverage": r["cover"], "late": r["late"],
                           "early": r["early"], "head_upper_mid": r["head"],
                           "cells": r["n"]},
            "before": sweep_pool(result("before", "pinned", f"b{b}")["pairs"]),
            "after": sweep_pool(result("after", "pinned", f"b{b}")["pairs"])}
    # oos_realism_cal.py: GOOD = default replay pairs with growth; per variant
    # only rows with n_new_burnable > 0
    doc_rows = jload(os.path.join(ROOT, "scratch_review", "hindcast_oos_data", "cal_realism.json"))
    var_label = {"blend0.5": "b0.5", "grass_floor": "b0.5_floor",
                 "realism_pack": "b0.5_floor_spot"}
    for var, lab in var_label.items():
        res = {"documented": realism_pool([r for r in doc_rows if r["variant"] == var])}
        for state in ("before", "after"):
            good = {(p["incident"], p["t0_s"]) for p in result(state, "pinned", "b0.0")["pairs"]
                    if p["n_new_burnable"] > 0}
            rows = [p for p in result(state, "pinned", lab)["pairs"]
                    if (p["incident"], p["t0_s"]) in good and p["n_new_burnable"] > 0]
            res[state] = realism_pool(rows)
        out["cal_realism"][var] = res
    return out


def realism_pool(rows):
    n = sum(r["n_new_burnable"] for r in rows)
    heads = [r["head_m_p50"] / r["obs_head_m"] for r in rows if r.get("obs_head_m")]
    return {"pairs": len(rows), "cells": n,
            "coverage": pct(sum(r["covered_possible"] for r in rows), n),
            "late": pct(sum(r["late_miss"] for r in rows), n),
            "early": pct(sum(r["early_miss"] for r in rows), n),
            "head_upper_mid": upper_mid(heads),
            "per_pair": {f"{r['name']} {fdt(r['t0_s'])}": {
                "cells": r["n_new_burnable"],
                "late": pct(r["late_miss"], r["n_new_burnable"]),
                "coverage": pct(r["covered_possible"], r["n_new_burnable"])}
                for r in rows}}


# ---------------------------------------------------------------------------
# July OOS: oos_pool.py (blend table, regimes, fuel), oos_realism.py
# ---------------------------------------------------------------------------

def wx_span(root, corpus, slug):
    p = os.path.join(root, CORPUS_DIR[corpus], "weather", f"{slug}.json")
    if not os.path.exists(p):
        return None
    wd = jload(p)
    wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
    return wx.samples[0].t_s, wx.samples[-1].t_s


def oos_keep(pairs, root):
    keep, drop = [], []
    for p in pairs:
        span = wx_span(root, "oos", _irwin_slug(p["incident"]))
        why = None
        if p["t0_s"] < OOS_CUT:
            why = "pre-cutoff"
        elif p["n_new_burnable"] <= 0:
            why = "no spatial growth" + (f" ({p['notes'][0][:60]})" if p.get("notes") else "")
        elif span is None or p["t0_s"] < span[0] or p["t1_s"] > span[1] + 3600:
            why = "window outside archived weather"
        (drop if why else keep).append((p, why))
    return [p for p, _ in keep], [(p, w) for p, w in drop]


def oos_label(b):
    return "F_nofloor" if b == "0.5" else f"b{b}"


def oos_doc_pairs(b):
    return jload(os.path.join(ROOT, "scratch_review", "hindcast_oos_data", f"replay_b{b}.json"))


def is_porcupine(p):
    return p["name"].startswith("PorcupineRidge")


def block_oos():
    out = {"blend": {}, "per_pair": {}, "regimes": {}, "fuel_b05": {},
           "excluded": {}, "realism": {}, "wide_porcupine": {}}
    states = {"documented": (ROOT, lambda b: oos_doc_pairs(b)),
              "before": (BEFORE_ROOT, lambda b: result("before", "oos", oos_label(b))),
              "after": (ROOT, lambda b: result("after", "oos", oos_label(b)))}
    for state, (root, get) in states.items():
        for b in ("0.0", "0.25", "0.5", "0.75", "1.0"):
            keep, drop = oos_keep(get(b)["pairs"], root)
            pool = sweep_pool(keep)
            pool["incidents"] = sorted({p["name"] for p in keep})
            out["blend"].setdefault(b, {})[state] = pool
            if b == "0.5":
                out["excluded"][state] = [f"{p['name']} {fdt(p['t0_s'])} +{p['dt_h']:.1f}h ({w})"
                                          for p, w in drop]
                out["fuel_b05"][state] = fuel_late(keep)
                normal = [p for p in keep if not is_porcupine(p)]
                porc = [p for p in keep if is_porcupine(p)]
                out["regimes"][state] = {
                    "normal": sweep_pool(normal) | {"windows": len(normal)},
                    "porcupine": pair_rows(porc),
                    "porcupine_share_of_cells": pct(sum(p["n_new_burnable"] for p in porc),
                                                    sum(p["n_new_burnable"] for p in keep))}
        keep0 = {(p["incident"], p["t0_s"]): p for p in oos_keep(get("0.0")["pairs"], root)[0]}
        rows = []
        for p in sorted(oos_keep(get("0.5")["pairs"], root)[0], key=lambda p: p["t0_s"]):
            q = keep0.get((p["incident"], p["t0_s"]))
            nb = p["n_new_burnable"]
            rows.append({"window": f"{p['name']} {fdt(p['t0_s'])} +{p['dt_h']:.1f}h",
                         "cells": nb,
                         "late_b0": pct(q["late_miss"], q["n_new_burnable"]) if q else None,
                         "late_b05": pct(p["late_miss"], nb),
                         "coverage_b05": pct(p["covered_possible"], nb)})
        out["per_pair"][state] = rows
    # widened band at blend 0.5 (documented in the text only: Porcupine 81.7 %)
    for state, d in (("documented", oos_doc_pairs("0.5")),
                     ("before", result("before", "oos", "F_nofloor")),
                     ("after", result("after", "oos", "F_nofloor"))):
        w = [p for p in d.get("pairs_wide") or [] if is_porcupine(p) and p["n_new_burnable"] > 0]
        out["wide_porcupine"][state] = [pct(p["late_miss"], p["n_new_burnable"]) for p in w]
    # oos_realism.py: GOOD = blend-0.5 pairs with growth and t0 >= cut (no
    # weather-span guard); rows with n_new_burnable > 0
    doc_rows = jload(os.path.join(ROOT, "scratch_review", "hindcast_oos_data", "oos_realism.json"))
    for var, lab in (("grass_floor", "A_deployed"), ("realism_pack", "b0.5_floor_spot")):
        res = {"documented": realism_pool([r for r in doc_rows if r["variant"] == var])}
        for state in ("before", "after"):
            good = {(p["incident"], p["t0_s"]) for p in result(state, "oos", "F_nofloor")["pairs"]
                    if p["n_new_burnable"] > 0 and p["t0_s"] >= OOS_CUT}
            rows = [p for p in result(state, "oos", lab)["pairs"]
                    if (p["incident"], p["t0_s"]) in good and p["n_new_burnable"] > 0]
            res[state] = realism_pool(rows)
        out["realism"][var] = res
    shipped = {}
    for state in ("before", "after"):
        good = [p for p in result(state, "oos", "F_nofloor")["pairs"]
                if p["n_new_burnable"] > 0 and p["t0_s"] >= OOS_CUT]
        shipped[state] = realism_pool(good)
    shipped["documented"] = realism_pool([p for p in oos_doc_pairs("0.5")["pairs"]
                                          if p["n_new_burnable"] > 0 and p["t0_s"] >= OOS_CUT])
    out["realism"]["shipped"] = shipped
    return out


# ---------------------------------------------------------------------------
# extreme-regime signature (scratch_review/extreme_signature.py) + the
# shipped regime condition (>= 0.60 grass-class burnable domain AND max
# effective wind (blend 0.5) >= 4.0 m/s in the window)
# ---------------------------------------------------------------------------

def fuel_mix(tile):
    counts, burnable = {}, 0
    for v in tile.fuel.data:
        code = int(v)
        if not H.is_burnable(code):
            continue
        burnable += 1
        cls = H.fuel_class(code)
        counts[cls] = counts.get(cls, 0) + 1
    return {c: n / burnable for c, n in counts.items()} if burnable else {}


def wx_window(wx, t0, t1):
    sel = [s for s in wx.samples if t0 - 3600 <= s.t_s <= t1 + 3600]
    if not sel:
        return None
    eff = [s.wind_speed_mps + 0.5 * max(0.0, s.wind_gust_mps - s.wind_speed_mps) for s in sel]
    return {"max_wind": max(s.wind_speed_mps for s in sel),
            "max_gust": max(s.wind_gust_mps for s in sel),
            "max_eff": max(eff), "min_rh": min(s.rh_pct for s in sel)}


def block_extreme(pinned, oos):
    """Windows = the grass-floor realism rows of each set (as the script);
    the fuel mix of each incident's replay tile and the window weather do
    not depend on the fix (same tiles, same capture times)."""
    def lates(rows_by_state, state):
        return {k: v["late"] for k, v in rows_by_state[state]["per_pair"].items()}

    out = {}
    doc = jload(os.path.join(ROOT, "scratch_review", "extreme_signature.json"))
    out["documented"] = [{"window": f"{r['name']} {fdt(r['t0_s'])}", "floored_late": r["floored_late"],
                          "grass": 100 * (r["mix"].get("GR", 0) + r["mix"].get("GS", 0)),
                          "max_eff": r["max_eff"],
                          "armed": (r["mix"].get("GR", 0) + r["mix"].get("GS", 0)) >= 0.60 and r["max_eff"] >= 4.0}
                         for r in doc]
    for state in ("before", "after"):
        root = BEFORE_ROOT if state == "before" else ROOT
        rows = []
        for set_name, corpus, realism in (("cal", "pinned", pinned["cal_realism"]["grass_floor"]),
                                          ("oos", "oos", oos["realism"]["grass_floor"])):
            lat = lates(realism, state)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                incs = _load_all_incidents(CORPUS_DIR[corpus])
            finally:
                os.chdir(cwd)
            for slug, inc in incs:
                wx_path = os.path.join(root, CORPUS_DIR[corpus], "weather", f"{slug}.json")
                if not os.path.exists(wx_path):
                    continue
                wd = jload(wx_path)
                wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
                bbox, cell_m = _incident_domain(inc, ResponseConfig())
                cp = LfpsTerrain(cache_dir=os.path.join(root, CORPUS_DIR[corpus], "lfps")).cache_path(bbox, cell_m)
                if not (cp and os.path.exists(cp)):
                    continue
                mix = fuel_mix(load_ascii_tile(cp))
                for a, b in zip(inc.snapshots, inc.snapshots[1:]):
                    key = f"{inc.name} {fdt(a.t_s)}"
                    if key not in lat:
                        continue
                    w = wx_window(wx, a.t_s, b.t_s)
                    if w is None:
                        continue
                    g = mix.get("GR", 0) + mix.get("GS", 0)
                    rows.append({"window": key, "set": set_name, "dt_h": round((b.t_s - a.t_s) / 3600, 1),
                                 "floored_late": lat[key], "grass": 100 * g, "max_eff": w["max_eff"],
                                 "armed": g >= 0.60 and w["max_eff"] >= 4.0})
        out[state] = rows
    return out


# ---------------------------------------------------------------------------
# August review-era table (scratch_review/aug_hindcast_replay.py)
# ---------------------------------------------------------------------------

def aug_rows(pairs, root):
    keep, excluded = [], []
    for p in pairs:
        span = wx_span(root, "aug", _irwin_slug(p["incident"]))
        why = None
        if p["t0_s"] < AUG_CUT:
            why = "t0 before 2026-08-01"
        elif span is None or p["t0_s"] < span[0] or p["t1_s"] > span[1] + 3600:
            why = "window outside archived weather"
        elif p["n_new_burnable"] <= 0:
            why = (p.get("notes") or ["no scorable growth"])[0][:70]
        if why:
            excluded.append(f"{p['name']} {fdt(p['t0_s'])} +{p['dt_h']:.1f}h ({why})")
        else:
            keep.append(p)
    return keep, excluded


def aug_pool(rows):
    n = sum(r["n_new_burnable"] for r in rows)
    heads = [r["head_m_p50"] / r["obs_head_m"] for r in rows if r.get("obs_head_m")]
    return {"pairs": len(rows), "cells": n,
            "coverage": pct(sum(r["covered_possible"] for r in rows), n),
            "late": pct(sum(r["late_miss"] for r in rows), n),
            "early": pct(sum(r["early_miss"] for r in rows), n),
            "p50_in": pct(sum(r["p50_in_window"] for r in rows), n),
            "head_upper_mid": upper_mid(heads)}


def block_aug():
    out = {}
    doc = jload(os.path.join(ROOT, "scratch_review", "hindcast_aug_data", "aug_replay.json"))
    for var, lab in (("calibration", "F_nofloor"), ("as-deployed", "A_deployed")):
        docrows = [r for r in doc["rows"] if r["variant"] == var and r["n_new_burnable"] > 0]
        res = {"documented": aug_pool(docrows) | {"per_pair": pair_rows(docrows)}}
        for state in ("before", "after"):
            root = BEFORE_ROOT if state == "before" else ROOT
            keep, excl = aug_rows(result(state, "aug", lab)["pairs"], root)
            res[state] = aug_pool(keep) | {"per_pair": pair_rows(keep), "excluded": excl,
                                           "fuel": fuel_late(keep)}
        res["documented"]["fuel"] = fuel_late(docrows)
        res["documented"]["excluded"] = [f"{e['incident']} ({e['why']})" for e in doc["excluded"]]
        out[var] = res
    return out


# ---------------------------------------------------------------------------

def main() -> int:
    data = {"matrix": block_matrix(), "pinned": block_pinned()}
    data["oos"] = block_oos()
    data["extreme"] = block_extreme(data["pinned"], data["oos"])
    data["aug"] = block_aug()
    with open(os.path.join(HERE, "before_after.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True, default=str)
    print("wrote", os.path.join(HERE, "before_after.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
