"""Independent re-derivation of hindcast pooled tables + methodology attacks.

Read-only on committed JSONs. Writes scratch_review/rvgrok_stats_audit_out.json.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "scratch_review", "rvgrok_stats_audit_out.json")

JUL_CUT = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc).timestamp()
AUG_CUT = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def head_ratio(r):
    obs = r.get("obs_head_m") or 0.0
    if obs <= 0:
        return None
    return r["head_m_p50"] / obs


def median_head(pairs):
    hs = sorted(h for r in pairs if (h := head_ratio(r)) is not None)
    if not hs:
        return None
    return hs[len(hs) // 2]


def pool_cell(pairs):
    n = sum(r["n_new_burnable"] for r in pairs)
    if n == 0:
        return {"n_pairs": 0, "n_cells": 0}
    def w(k):
        return sum(r.get(k, 0) for r in pairs) / n
    fuel = {}
    for r in pairs:
        for cls, rec in (r.get("by_fuel_class") or {}).items():
            agg = fuel.setdefault(cls, {"n": 0, "late": 0, "cert": 0, "poss": 0})
            for kk in agg:
                agg[kk] += rec.get(kk, 0)
    fuel_late = {}
    for cls, rec in fuel.items():
        nn = rec["n"]
        # n includes NB; late is among all class cells including NB which
        # were not scored as late in the engine (NB skipped). Report both.
        fuel_late[cls] = {
            "n": nn,
            "late": rec["late"],
            "late_pct": 100.0 * rec["late"] / nn if nn else None,
            "poss": rec["poss"],
        }
    return {
        "n_pairs": len(pairs),
        "n_cells": n,
        "coverage_possible_pct": 100.0 * w("covered_possible"),
        "coverage_certain_pct": 100.0 * w("covered_certain"),
        "late_miss_pct": 100.0 * w("late_miss"),
        "early_miss_pct": 100.0 * w("early_miss"),
        "never_reached_pct": 100.0 * w("never_reached"),
        "p90_unreached_pct": 100.0 * w("p90_unreached"),
        "p50_in_window_pct": 100.0 * w("p50_in_window"),
        "p10_not_after_window_pct": 100.0 * w("p10_not_after_window"),
        "p10_before_window_pct": 100.0 * w("p10_before_window"),
        "nonburnable_n": sum(r.get("n_new_nonburnable", 0) for r in pairs),
        "median_head_ratio": median_head(pairs),
        "mean_head_ratio": (
            statistics.mean([h for r in pairs if (h := head_ratio(r)) is not None])
            if any(head_ratio(r) is not None for r in pairs) else None
        ),
        "by_fuel": fuel_late,
        "sum_check": {
            "late_plus_early": w("late_miss") + w("early_miss"),
            "late_eq_one_minus_cover": abs(w("late_miss") - (1.0 - w("covered_possible"))),
            "p10_nec_eq_cover": abs(w("p10_not_after_window") - w("covered_possible")),
        },
    }


def pool_pair(pairs):
    """Equal-weight each pair (unweighted mean of per-pair rates)."""
    if not pairs:
        return {"n_pairs": 0}
    def rate(k):
        return statistics.mean(r[k] / r["n_new_burnable"] for r in pairs)
    return {
        "n_pairs": len(pairs),
        "coverage_possible_pct": 100.0 * rate("covered_possible"),
        "late_miss_pct": 100.0 * rate("late_miss"),
        "early_miss_pct": 100.0 * rate("early_miss"),
        "p50_in_window_pct": 100.0 * rate("p50_in_window"),
        "p10_before_window_pct": 100.0 * rate("p10_before_window"),
        "p90_unreached_pct": 100.0 * rate("p90_unreached"),
        "median_of_pair_head_ratios": median_head(pairs),
        "median_of_pair_coverage_pct": 100.0 * statistics.median(
            r["covered_possible"] / r["n_new_burnable"] for r in pairs
        ),
    }


def pair_rows(pairs):
    out = []
    tot = sum(r["n_new_burnable"] for r in pairs) or 1
    for r in sorted(pairs, key=lambda x: -x["n_new_burnable"]):
        n = r["n_new_burnable"]
        t0 = dt.datetime.fromtimestamp(r["t0_s"], tz=dt.timezone.utc)
        out.append({
            "name": r.get("name"),
            "t0": t0.strftime("%Y-%m-%d %H:%M"),
            "dt_h": r["dt_h"],
            "cells": n,
            "cell_share_pct": 100.0 * n / tot,
            "cover_pct": 100.0 * r["covered_possible"] / n,
            "late_pct": 100.0 * r["late_miss"] / n,
            "early": r["early_miss"],
            "certain": r["covered_certain"],
            "p10_before": r.get("p10_before_window", 0),
            "p90_unreached": r.get("p90_unreached", 0),
            "p50_in": r.get("p50_in_window", 0),
            "never_reached": r.get("never_reached", 0),
            "obs_head_km": (r["obs_head_m"] or 0) / 1000.0,
            "p50_head_km": (r["head_m_p50"] or 0) / 1000.0,
            "head_ratio": head_ratio(r),
            "obs_rate_kmh": (
                (r["obs_head_m"] / r["dt_h"] / 1000.0) if r.get("dt_h") else None
            ),
            "obs_new_ac": r.get("obs_new_area_ac"),
            "modeled_new_ac_p50": r.get("modeled_new_area_ac_p50"),
            "notes": r.get("notes") or [],
        })
    return out


def leave_one_out(pairs):
    tot = sum(r["n_new_burnable"] for r in pairs) or 1
    rows = []
    for drop in pairs:
        rest = [r for r in pairs if r is not drop]
        p = pool_cell(rest)
        rows.append({
            "dropped": drop.get("name"),
            "dropped_cells": drop["n_new_burnable"],
            "dropped_share_pct": 100.0 * drop["n_new_burnable"] / tot,
            "dropped_late_pct": 100.0 * drop["late_miss"] / drop["n_new_burnable"],
            "rest_n_pairs": p["n_pairs"],
            "rest_cells": p["n_cells"],
            "rest_cover_pct": p["coverage_possible_pct"],
            "rest_late_pct": p["late_miss_pct"],
        })
    rows.sort(key=lambda x: -abs(x["rest_cover_pct"] - 0))
    return rows


def window_duration_bins(pairs):
    bins = [(0, 6), (6, 12), (12, 24), (24, 48), (48, 96)]
    out = []
    for lo, hi in bins:
        sub = [r for r in pairs if lo < r["dt_h"] <= hi]
        p = pool_cell(sub)
        out.append({
            "dt_h": f"({lo},{hi}]",
            "n_pairs": p["n_pairs"],
            "n_cells": p.get("n_cells", 0),
            "cover_pct": p.get("coverage_possible_pct"),
            "late_pct": p.get("late_miss_pct"),
            "p90_unreached_pct": p.get("p90_unreached_pct"),
        })
    return out


def herfindahl(pairs):
    tot = sum(r["n_new_burnable"] for r in pairs) or 1
    shares = [r["n_new_burnable"] / tot for r in pairs]
    hhi = sum(s * s for s in shares)
    # equivalent number of equal-sized fires
    return {
        "hhi": hhi,
        "eff_n": (1.0 / hhi) if hhi else None,
        "largest_share_pct": 100.0 * max(shares) if shares else None,
        "top3_share_pct": 100.0 * sum(sorted(shares, reverse=True)[:3]),
    }


# ---------------------------------------------------------------------------
# August
# ---------------------------------------------------------------------------
aug = _load("scratch_review/hindcast_aug_data/aug_replay.json")
aug_rows = aug["rows"]
aug_as = [r for r in aug_rows if r["variant"] == "as-deployed" and r["n_new_burnable"] > 0]
aug_cal = [r for r in aug_rows if r["variant"] == "calibration" and r["n_new_burnable"] > 0]

# published pooled block (if present)
aug_published = aug.get("pooled")

# ---------------------------------------------------------------------------
# July OOS
# ---------------------------------------------------------------------------
jul_b05 = _load("scratch_review/hindcast_oos_data/replay_b0.5.json")
jul_pairs_all = jul_b05["pairs"]
jul_b05_scored = [
    r for r in jul_pairs_all
    if r["n_new_burnable"] > 0 and r["t0_s"] >= JUL_CUT
]
jul_real = _load("scratch_review/hindcast_oos_data/oos_realism.json")
jul_floor = [r for r in jul_real if r["variant"] == "grass_floor" and r["n_new_burnable"] > 0]
jul_pack = [r for r in jul_real if r["variant"] == "realism_pack" and r["n_new_burnable"] > 0]

# Identify Porcupine
def is_porc(r):
    return "porcupine" in (r.get("name") or "").lower()

jul_floor_ex_porc = [r for r in jul_floor if not is_porc(r)]
jul_b05_ex_porc = [r for r in jul_b05_scored if not is_porc(r)]

# ---------------------------------------------------------------------------
# Calibration (for completeness vs handoff table)
# ---------------------------------------------------------------------------
gust = _load("scratch_review/gust_sweep.json")
cal_real = _load("scratch_review/hindcast_oos_data/cal_realism.json")
cal_b05 = [r for r in cal_real if r.get("variant") == "blend0.5" and r["n_new_burnable"] > 0]
cal_floor = [r for r in cal_real if r.get("variant") == "grass_floor" and r["n_new_burnable"] > 0]
# gust_sweep may be a list of blend pools
gust_shape = type(gust).__name__
if isinstance(gust, dict):
    gust_keys = list(gust.keys())
else:
    gust_keys = list(gust[0].keys()) if gust else []

# ---------------------------------------------------------------------------
# Discover / selection
# ---------------------------------------------------------------------------
aug_disc = _load("scratch_review/aug_hindcast_discover.json")
all_inc = aug_disc.get("all") or aug_disc.get("incidents") or []
if not all_inc and isinstance(aug_disc, dict):
    # maybe nested
    for k, v in aug_disc.items():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "kept" in v[0]:
            all_inc = v
            break

kept = [x for x in all_inc if x.get("kept")]
dropped = [x for x in all_inc if not x.get("kept")]

def acre_stats(xs):
    ac = [x.get("max_acres") or 0 for x in xs]
    if not ac:
        return {}
    acs = sorted(ac)
    return {
        "n": len(ac),
        "min": min(ac),
        "p50": acs[len(acs) // 2],
        "mean": statistics.mean(ac),
        "max": max(ac),
        "ge_1000": sum(1 for a in ac if a >= 1000),
        "ge_10000": sum(1 for a in ac if a >= 10000),
        "single_row": sum(1 for x in xs if (x.get("n_rows") or 0) <= 1),
        "span0": sum(1 for x in xs if (x.get("span_h") or 0) <= 0),
    }

# drop reasons
drop_reasons = defaultdict(int)
for x in dropped:
    if (x.get("n_rows") or 0) <= 1:
        drop_reasons["n_rows<=1 (no pair)"] += 1
    elif (x.get("span_h") or 0) <= 0:
        drop_reasons["span_h=0"] += 1
    else:
        drop_reasons["other_or_filter"] += 1

# ---------------------------------------------------------------------------
# Early-miss structural check
# ---------------------------------------------------------------------------
def structural_early(pairs, label):
    n = sum(r["n_new_burnable"] for r in pairs)
    early = sum(r["early_miss"] for r in pairs)
    p10b = sum(r.get("p10_before_window", 0) for r in pairs)
    cert = sum(r.get("covered_certain", 0) for r in pairs)
    p90u = sum(r.get("p90_unreached", 0) for r in pairs)
    return {
        "label": label,
        "n_cells": n,
        "early_miss": early,
        "p10_before_window": p10b,
        "covered_certain": cert,
        "p90_unreached": p90u,
        "early_miss_pct": 100.0 * early / n if n else None,
        "p10_before_pct": 100.0 * p10b / n if n else None,
        "certain_pct": 100.0 * cert / n if n else None,
        "p90_unreached_pct": 100.0 * p90u / n if n else None,
        "late_equals_one_minus_cover": all(
            r["late_miss"] + r["covered_possible"] == r["n_new_burnable"]
            or abs((r["late_miss"] + r["covered_possible"]) - r["n_new_burnable"]) <= r["early_miss"]
            for r in pairs
        ) if pairs else None,
        "pairs_where_late_plus_cover_ne_n": [
            r.get("name") for r in pairs
            if r["late_miss"] + r["covered_possible"] != r["n_new_burnable"]
        ],
    }

# ---------------------------------------------------------------------------
# Fuel late (Aug as-deployed): exclude NB from denominator like the handoff
# ---------------------------------------------------------------------------
def fuel_late_burnable_only(pairs):
    fuel = {}
    for r in pairs:
        for cls, rec in (r.get("by_fuel_class") or {}).items():
            if cls == "NB":
                continue
            agg = fuel.setdefault(cls, {"n": 0, "late": 0, "poss": 0})
            agg["n"] += rec.get("n", 0)
            agg["late"] += rec.get("late", 0)
            agg["poss"] += rec.get("poss", 0)
    out = {}
    for cls, rec in fuel.items():
        out[cls] = {
            "n": rec["n"],
            "late": rec["late"],
            "late_pct": 100.0 * rec["late"] / rec["n"] if rec["n"] else None,
            "cover_pct": 100.0 * rec["poss"] / rec["n"] if rec["n"] else None,
        }
    return out


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------
report = {
    "august_as_deployed_cell": pool_cell(aug_as),
    "august_as_deployed_pair": pool_pair(aug_as),
    "august_calibration_cell": pool_cell(aug_cal),
    "august_calibration_pair": pool_pair(aug_cal),
    "august_published_pooled": aug_published,
    "august_pairs": pair_rows(aug_as),
    "august_loo": leave_one_out(aug_as),
    "august_hhi": herfindahl(aug_as),
    "august_window_bins": window_duration_bins(aug_as),
    "august_fuel_late": fuel_late_burnable_only(aug_as),
    "august_structural": structural_early(aug_as, "aug_as_deployed"),
    "august_excluded": aug.get("excluded"),
    "july_b05_cell": pool_cell(jul_b05_scored),
    "july_b05_pair": pool_pair(jul_b05_scored),
    "july_b05_pairs": pair_rows(jul_b05_scored),
    "july_b05_hhi": herfindahl(jul_b05_scored),
    "july_b05_loo": leave_one_out(jul_b05_scored),
    "july_floor_cell": pool_cell(jul_floor),
    "july_floor_pair": pool_pair(jul_floor),
    "july_floor_pairs": pair_rows(jul_floor),
    "july_floor_hhi": herfindahl(jul_floor),
    "july_floor_ex_porcupine_cell": pool_cell(jul_floor_ex_porc),
    "july_floor_ex_porcupine_pair": pool_pair(jul_floor_ex_porc),
    "july_floor_loo": leave_one_out(jul_floor),
    "july_pack_cell": pool_cell(jul_pack),
    "july_b05_ex_porcupine_cell": pool_cell(jul_b05_ex_porc),
    "july_structural_b05": structural_early(jul_b05_scored, "july_b05"),
    "july_structural_floor": structural_early(jul_floor, "july_floor"),
    "july_window_bins_floor": window_duration_bins(jul_floor),
    "july_fuel_late_b05": fuel_late_burnable_only(jul_b05_scored),
    "july_fuel_late_floor": fuel_late_burnable_only(jul_floor),
    "cal_b05_cell": pool_cell(cal_b05),
    "cal_floor_cell": pool_cell(cal_floor),
    "cal_structural_floor": structural_early(cal_floor, "cal_floor"),
    "gust_shape": gust_shape,
    "gust_keys": gust_keys,
    "selection_aug": {
        "n_discovered": len(all_inc),
        "n_kept": len(kept),
        "n_dropped": len(dropped),
        "kept_acres": acre_stats(kept),
        "dropped_acres": acre_stats(dropped),
        "drop_reasons": dict(drop_reasons),
        "kept_names": [x.get("name") for x in kept],
        "dropped_names_sample": [
            {"name": x.get("name"), "acres": x.get("max_acres"),
             "n_rows": x.get("n_rows"), "span_h": x.get("span_h"),
             "state": x.get("state")}
            for x in sorted(dropped, key=lambda z: -(z.get("max_acres") or 0))[:25]
        ],
        "kept_detail": [
            {"name": x.get("name"), "acres": x.get("max_acres"),
             "n_rows": x.get("n_rows"), "span_h": x.get("span_h"),
             "state": x.get("state")}
            for x in sorted(kept, key=lambda z: -(z.get("max_acres") or 0))
        ],
    },
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)
print("wrote", OUT)

def show(title, d):
    print(f"\n=== {title} ===")
    if not d:
        print("  (empty)")
        return
    keys = [
        "n_pairs", "n_cells", "coverage_possible_pct", "coverage_certain_pct",
        "late_miss_pct", "early_miss_pct", "never_reached_pct",
        "p90_unreached_pct", "p50_in_window_pct", "p10_not_after_window_pct",
        "p10_before_window_pct", "median_head_ratio", "mean_head_ratio",
        "median_of_pair_coverage_pct",
    ]
    for k in keys:
        if k in d and d[k] is not None:
            v = d[k]
            if isinstance(v, float):
                print(f"  {k:32s} {v:.4f}")
            else:
                print(f"  {k:32s} {v}")
    if "sum_check" in d:
        print("  sum_check", d["sum_check"])

show("AUG as-deployed CELL", report["august_as_deployed_cell"])
show("AUG as-deployed PAIR", report["august_as_deployed_pair"])
show("AUG calibration CELL", report["august_calibration_cell"])
show("JULY b0.5 CELL", report["july_b05_cell"])
show("JULY b0.5 PAIR", report["july_b05_pair"])
show("JULY floor CELL", report["july_floor_cell"])
show("JULY floor PAIR", report["july_floor_pair"])
show("JULY floor ex-Porcupine CELL", report["july_floor_ex_porcupine_cell"])
show("JULY floor ex-Porcupine PAIR", report["july_floor_ex_porcupine_pair"])
show("CAL b0.5 CELL", report["cal_b05_cell"])
show("CAL floor CELL", report["cal_floor_cell"])

print("\n=== AUG HHI ===", report["august_hhi"])
print("=== JUL b05 HHI ===", report["july_b05_hhi"])
print("=== JUL floor HHI ===", report["july_floor_hhi"])
print("\n=== AUG structural ===", json.dumps(report["august_structural"], indent=2))
print("\n=== JUL structural b05 ===", json.dumps(report["july_structural_b05"], indent=2))
print("\n=== AUG fuel late ===", json.dumps(report["august_fuel_late"], indent=2))
print("\n=== AUG pair table ===")
for r in report["august_pairs"]:
    print(f"  {r['name']:<18} cells={r['cells']:6d} share={r['cell_share_pct']:5.1f}% "
          f"cover={r['cover_pct']:5.1f} late={r['late_pct']:5.1f} "
          f"ratio={r['head_ratio'] and round(r['head_ratio'],2)} "
          f"dt={r['dt_h']:.1f}h p90u={r['p90_unreached']} certain={r['certain']} "
          f"p10b={r['p10_before']}")
print("\n=== JUL floor pair table ===")
for r in report["july_floor_pairs"]:
    print(f"  {r['name']:<18} cells={r['cells']:6d} share={r['cell_share_pct']:5.1f}% "
          f"cover={r['cover_pct']:5.1f} late={r['late_pct']:5.1f} "
          f"ratio={r['head_ratio'] and round(r['head_ratio'],2)} dt={r['dt_h']:.1f}h")
print("\n=== selection ===")
print(" discovered", report["selection_aug"]["n_discovered"],
      "kept", report["selection_aug"]["n_kept"],
      "dropped", report["selection_aug"]["n_dropped"])
print(" kept acres", report["selection_aug"]["kept_acres"])
print(" dropped acres", report["selection_aug"]["dropped_acres"])
print(" drop reasons", report["selection_aug"]["drop_reasons"])
print("\n=== AUG LOO (biggest cover deltas) ===")
base = report["august_as_deployed_cell"]["coverage_possible_pct"]
for r in sorted(report["august_loo"],
                key=lambda x: abs(x["rest_cover_pct"] - base), reverse=True)[:6]:
    print(f"  drop {r['dropped']:<18} share={r['dropped_share_pct']:5.1f}% "
          f"late={r['dropped_late_pct']:5.1f} rest_cover={r['rest_cover_pct']:.2f} "
          f"dCover={r['rest_cover_pct']-base:+.2f}")
print("\n=== JUL floor LOO ===")
basej = report["july_floor_cell"]["coverage_possible_pct"]
for r in sorted(report["july_floor_loo"] if "july_floor_loo" in report else report["july_b05_loo"],
                key=lambda x: abs(x["rest_cover_pct"] - basej), reverse=True)[:6]:
    print(f"  drop {r['dropped']:<18} share={r['dropped_share_pct']:5.1f}% "
          f"late={r['dropped_late_pct']:5.1f} rest_cover={r['rest_cover_pct']:.2f} "
          f"dCover={r['rest_cover_pct']-basej:+.2f}")
