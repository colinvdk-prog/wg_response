"""Gel-window / lead-time / simulator-claim arithmetic from committed JSONs.

Read-only. Writes scratch_review/rvgrok_gel_lead_ops_out.json.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "scratch_review", "rvgrok_gel_lead_ops_out.json")


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def homes_from_probe(path):
    d = load(path)
    rows = d["rows"]
    homes = []
    for r in rows:
        if r.get("error"):
            continue
        for h in r.get("homes") or []:
            homes.append({
                "map": r.get("map"),
                "inten": r.get("inten"),
                "seed": r.get("seed"),
                "viol": r.get("viol"),
                **h,
            })
    return d.get("params") or {}, homes


def bucket_surv(homes, edges):
    """Survival among truth-reached homes, by arr_h buckets.

    Survival here = gelled among reached (the probe's outcome is also kept).
    """
    reached = [h for h in homes if h.get("arr_h") is not None]
    out = []
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        sub = [h for h in reached if lo <= h["arr_h"] < hi]
        if not sub:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        gelled = sum(1 for h in sub if h["gelled"])
        saved = sum(1 for h in sub if h.get("outcome") in
                    ("saved", "protected_then_impacted", "gelled"))
        out.append({
            "lo": lo, "hi": hi, "n": len(sub),
            "gelled": gelled,
            "gelled_pct": 100.0 * gelled / len(sub),
            "outcome_saved": saved,
            "outcome_counts": _counts(h.get("outcome") for h in sub),
        })
    return out, reached


def _counts(xs):
    d = defaultdict(int)
    for x in xs:
        d[str(x)] += 1
    return dict(d)


params, homes = homes_from_probe("scratch_review/probe_leadtime_lead_c8d10.json")
edges = [0, 1.5, 2, 3, 4, 6, 8, 10, 14, 24, 48, 72, 200]
buckets, reached = bucket_surv(homes, edges)

gelled_all = [h for h in homes if h["gelled"]]
gelled_reached = [h for h in reached if h["gelled"]]
gelled_unreached = [h for h in homes if h["gelled"] and h.get("arr_h") is None]
reached_ungelled = [h for h in reached if not h["gelled"]]

arrs = [h["arr_h"] for h in gelled_reached]
# If coated at detection (t=0), 6h window expired iff arr_h > 6
# If coated at 24h hold line (coat at max(0, arr-24)), remaining at arrival:
#   coat_lead = min(arr_h, 24)   # hours of gel age at arrival under hold=24
#   6h window survives iff coat_lead <= 6  i.e. arr_h <= 6
# That's the same condition: hold=24 never coats LATER than t=0 when arr_h<=24,
# and when arr_h>24 it coats 24h before arrival — 18h too early for a 6h window.
#
# Optimistic 6h-window case: crews delay coating until 6h before arrival.
# Then ALL gelled+reached homes with arr_h>=deploy_h would still be protected
# IF the safety law still allows a 6h service window. That's a posture change,
# not the shipped hold=24 + window=72 policy.
#
# Conservative: shipped policy coats ~24h before P50 when arr_h > 24, else ASAP.
# Remaining gel age at arrival ≈ min(arr_h, 24). Expired under 6h iff that > 6.

n_gr = len(gelled_reached) or 1
frac_arr_gt = {}
for thr in (1.5, 4, 6, 10, 14, 24, 48, 72):
    frac_arr_gt[f">{thr}h"] = {
        "n": sum(1 for a in arrs if a > thr),
        "pct_of_gelled_reached": 100.0 * sum(1 for a in arrs if a > thr) / n_gr,
    }

# gelled_unreached: fire never arrived — 6h vs 72h is about whether the coat
# was wasted, not whether the home burned. Under 6h those coats expire during
# the sim (horizon typically 72-96h) unless re-coated.

# outcome breakdown
outcomes_all = _counts(h.get("outcome") for h in homes)
outcomes_reached = _counts(h.get("outcome") for h in reached)
outcomes_gelled = _counts(h.get("outcome") for h in gelled_all)

# lead-time survival among reached: gelled_pct is the coating rate
# handoff: 0% <1.5h, ~33% @4h, 50-63% @10-14h
handoff_check = {
    "<1.5h": {
        "n": sum(1 for h in reached if h["arr_h"] < 1.5),
        "gelled": sum(1 for h in reached if h["arr_h"] < 1.5 and h["gelled"]),
    },
    "3-5h (~4h)": {
        "n": sum(1 for h in reached if 3 <= h["arr_h"] < 5),
        "gelled": sum(1 for h in reached if 3 <= h["arr_h"] < 5 and h["gelled"]),
    },
    "10-14h": {
        "n": sum(1 for h in reached if 10 <= h["arr_h"] < 14),
        "gelled": sum(1 for h in reached if 10 <= h["arr_h"] < 14 and h["gelled"]),
    },
}
for k, v in handoff_check.items():
    v["pct"] = 100.0 * v["gelled"] / v["n"] if v["n"] else None

# wind-regime census files
wind_files = {
    "low_8mph_c4": "scratch_review/census_wind_low_c4.json",
    "low_8mph_c8": "scratch_review/census_wind_low_c8.json",
    "mod_18mph_c4": "scratch_review/census_wind_moderate_c4.json",
    "mod_18mph_c8": "scratch_review/census_wind_moderate_c8.json",
}
wind = {}
for k, p in wind_files.items():
    d = load(p)
    st = d.get("stats") or {}
    e = st.get("e1000")
    wind[k] = {
        "wind_mph": d.get("wind_mph"),
        "params": d.get("params"),
        "e1000": e,
        "e1000_pct": 100.0 * e[0] / e[1] if e and e[1] else None,
        "viol": st.get("viol"),
        "ov_ticks": st.get("ov_ticks"),
        "min_margin_h": st.get("min_margin_h"),
        "coated_total": st.get("coated_total"),
        "homes_total": st.get("homes_total"),
    }

# look for high/extreme wind in census_axis or opt_ember
axis = load("scratch_review/census_axis_sensitivity.json")
# extract by_intensity if present
axis_by_inten = {}
tables = (axis.get("axis_tables") or {})
for tag, rec in tables.items():
    t = rec.get("tables") or {}
    if "by_inten" in t:
        axis_by_inten[tag] = t["by_inten"]
    elif "by_intensity" in t:
        axis_by_inten[tag] = t["by_intensity"]

# RSF city-limits plan-of-record files
rsf_files = [
    "scratch_review/rsf_city_c8_scatter.json",
    "scratch_review/rsf_city_c10_scatter.json",
    "scratch_review/rsf_city_c12_scatter.json",
    "scratch_review/rsf_grid_rsf_c8_scatter.json",
    "scratch_review/rsf_grid_rsf_c10_scatter.json",
    "scratch_review/rsf_grid_rsf_c12_scatter.json",
]
rsf = {}
for p in rsf_files:
    fp = os.path.join(ROOT, p)
    if not os.path.exists(fp):
        rsf[p] = "MISSING"
        continue
    d = load(p)
    # try common shapes
    summary = {}
    if isinstance(d, dict):
        for key in ("stats", "pooled", "summary", "totals"):
            if key in d:
                summary[key] = d[key]
        # rows with e1000?
        rows = d.get("rows") or d.get("scenarios") or []
        if rows and isinstance(rows[0], dict):
            # collect belt if present
            gelled = 0
            total = 0
            viol = 0
            threatened_g = 0
            threatened_n = 0
            for r in rows:
                st = r.get("stats") or r
                e = st.get("e1000")
                if isinstance(e, list) and len(e) >= 2:
                    gelled += e[0]
                    total += e[1]
                viol += st.get("viol") or 0
                # threatened coating
                for kk in ("threatened", "front"):
                    v = st.get(kk)
                    if isinstance(v, list) and len(v) >= 2:
                        threatened_g += v[0]
                        threatened_n += v[1]
            summary["sum_e1000"] = [gelled, total, 100.0 * gelled / total if total else None]
            summary["sum_viol"] = viol
            summary["sum_threatened"] = [
                threatened_g, threatened_n,
                100.0 * threatened_g / threatened_n if threatened_n else None,
            ]
            summary["n_rows"] = len(rows)
            summary["row0_keys"] = sorted(rows[0].keys())[:40]
    rsf[p] = summary

# circuity calibration
circ = None
cpath = "scratch_review/circuity_calibration.json"
if os.path.exists(os.path.join(ROOT, cpath)):
    cd = load(cpath)
    if isinstance(cd, dict):
        circ = {k: cd[k] for k in list(cd.keys())[:15]}
        # look for pooled median/mean
        for k, v in cd.items():
            if isinstance(v, dict) and any(x in str(k).lower() for x in
                                           ("pool", "median", "summary", "result")):
                circ[k] = v

# gel_effectiveness never enters count objective — confirm via a note
# da_exp3 is a live experiment we can run separately

report = {
    "probe_params": params,
    "n_homes": len(homes),
    "n_reached": len(reached),
    "n_gelled": len(gelled_all),
    "n_gelled_reached": len(gelled_reached),
    "n_gelled_unreached": len(gelled_unreached),
    "n_reached_ungelled": len(reached_ungelled),
    "arr_h_gelled_reached": {
        "n": len(arrs),
        "min": min(arrs) if arrs else None,
        "p10": sorted(arrs)[int(0.1 * (len(arrs) - 1))] if arrs else None,
        "p50": sorted(arrs)[len(arrs) // 2] if arrs else None,
        "p90": sorted(arrs)[int(0.9 * (len(arrs) - 1))] if arrs else None,
        "mean": statistics.mean(arrs) if arrs else None,
        "max": max(arrs) if arrs else None,
        "gt_thresholds": frac_arr_gt,
    },
    "6h_window_attack": {
        "policy": "probe uses dispatch_hold_h=24 and default gel_window_h=72, gel_full_strength_h=72 (flat)",
        "gelled_reached_arr_gt_6h": frac_arr_gt[">6h"],
        "gelled_reached_arr_gt_24h": frac_arr_gt[">24h"],
        "interpretation": (
            "Under shipped hold=24 + window=72, coats land ~24h before P50 when "
            "lead > 24h, else ASAP. Remaining gel age at truth arrival ≈ min(arr_h, 24). "
            "A 6h window with NO policy change expires on every gelled+reached home with "
            "arr_h > 6h. An optimistic re-policy (coat in the last 6h) is a different "
            "product (day-of, inside the safety-law squeeze)."
        ),
        "gelled_unreached_n": len(gelled_unreached),
        "gelled_unreached_note": (
            "These coats are on homes the truth fire never reached. Under a 6h window "
            "they expire during a multi-day sim unless re-applied; under 72h they persist."
        ),
    },
    "leadtime_buckets": buckets,
    "handoff_leadtime_check": handoff_check,
    "outcomes_all": outcomes_all,
    "outcomes_reached": outcomes_reached,
    "outcomes_gelled": outcomes_gelled,
    "wind_census": wind,
    "axis_by_inten": axis_by_inten,
    "rsf": rsf,
    "circuity_preview": circ,
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)
print("wrote", OUT)

print("\n=== PROBE c8d10 ===")
print("params", params, "homes", len(homes), "reached", len(reached),
      "gelled", len(gelled_all), "gelled_reached", len(gelled_reached),
      "gelled_unreached", len(gelled_unreached))
print("arr_h gelled+reached", report["arr_h_gelled_reached"])
print("\n6h attack: arr>6h", frac_arr_gt[">6h"], "arr>24h", frac_arr_gt[">24h"])
print("\nHandoff lead-time check:")
for k, v in handoff_check.items():
    print(f"  {k}: {v}")
print("\nBuckets:")
for b in buckets:
    if b.get("n"):
        print(f"  [{b['lo']},{b['hi']}) n={b['n']:5d} gelled={b['gelled_pct']:5.1f}% "
              f"outcomes={b['outcome_counts']}")
print("\nWind census:")
for k, v in wind.items():
    print(f"  {k}: e1000={v['e1000_pct']:.2f}% viol={v['viol']} ovt={v['ov_ticks']} "
          f"margin={v['min_margin_h']}")
print("\nOutcomes reached", outcomes_reached)
print("axis tags with by_inten", list(axis_by_inten.keys())[:12])
print("\nRSF:")
for p, s in rsf.items():
    if isinstance(s, dict):
        print(f"  {os.path.basename(p)}: e1000={s.get('sum_e1000')} "
              f"threatened={s.get('sum_threatened')} viol={s.get('sum_viol')} "
              f"nrows={s.get('n_rows')} keys={s.get('row0_keys')}")
    else:
        print(f"  {os.path.basename(p)}: {s}")
