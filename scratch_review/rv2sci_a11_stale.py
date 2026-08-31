"""PASS-2 independent pooling of committed tools/hindcast_data/replay_results.json.

Writes scratch_review/rv2sci_a11.json.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")

SRC = r"C:\Users\colin\wg_response\tools\hindcast_data\replay_results.json"
OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a11.json"
PUBLISHED_CAL_BLEND = 73.5  # handoff table, blend 0.5


def pool(pairs):
    scored = [p for p in pairs if p.get("n_new_burnable", 0) > 0]
    tot = sum(p["n_new_burnable"] for p in scored)
    if tot == 0:
        return dict(n_pairs=0, n_cells=0)
    s = lambda k: sum(p.get(k, 0) for p in scored)
    n_bins = max(len(p.get("pit_hist") or []) for p in scored)
    pit = [0] * n_bins
    for p in scored:
        for i, v in enumerate(p.get("pit_hist") or []):
            pit[i] += v
    heads = sorted((p["head_m_p50"] / p["obs_head_m"])
                   for p in scored
                   if p.get("obs_head_m") and p["obs_head_m"] > 0
                   and p.get("head_m_p50"))
    med = heads[len(heads) // 2] if heads else None  # published convention
    true_med = (heads[len(heads) // 2 - 1] + heads[len(heads) // 2]) / 2.0 if (
        heads and len(heads) % 2 == 0) else (heads[len(heads) // 2] if heads else None)
    return dict(
        n_pairs=len(scored),
        n_cells=tot,
        covered_possible=s("covered_possible"),
        late_miss=s("late_miss"),
        early_miss=s("early_miss"),
        covered_possible_frac=s("covered_possible") / tot,
        late_miss_frac=s("late_miss") / tot,
        early_miss_frac=s("early_miss") / tot,
        published_style_median_head_ratio=med,
        true_median_head_ratio=true_med,
        pit_hist=pit,
        labels=sorted({p.get("label") for p in scored}),
        names=sorted({p.get("name") for p in scored}),
    )


def main():
    data = json.load(open(SRC, encoding="utf-8"))
    pairs = data["pairs"] if isinstance(data, dict) and "pairs" in data else data
    # group by label if present
    by_label = {}
    for p in pairs:
        by_label.setdefault(p.get("label", "default"), []).append(p)
    overall = pool(pairs)
    out = dict(
        source=SRC,
        n_raw=len(pairs),
        overall=overall,
        by_label={k: pool(v) for k, v in by_label.items()},
        published_calibration_blend_pct=PUBLISHED_CAL_BLEND,
        overall_coverage_pct=100.0 * overall["covered_possible_frac"],
        delta_vs_published_pp=100.0 * overall["covered_possible_frac"] - PUBLISHED_CAL_BLEND,
    )
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)
    print(f"n_raw={len(pairs)} labels={list(by_label)}")
    print(f"overall coverage={100*overall['covered_possible_frac']:.2f}% "
          f"({overall['covered_possible']}/{overall['n_cells']}) "
          f"n_pairs={overall['n_pairs']}")
    print(f"published 73.5% (blend 0.5)  delta={out['delta_vs_published_pp']:+.2f}pp")
    for k, v in out["by_label"].items():
        print(f"  label={k!r}  {100*v['covered_possible_frac']:.2f}%  "
              f"n={v['n_pairs']} cells={v['n_cells']}")


if __name__ == "__main__":
    main()
