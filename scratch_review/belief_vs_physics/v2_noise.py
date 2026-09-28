"""Rapid rule v2 -- MEASURE the real perimeter growth noise (2026-09-28).

The legacy rapid rule discounts a window's growth by 1.645 x sd, with the
sd of each END's area = hypot(3 % x A, 10 ac) (fire_belief._area_sd, the
reported-acreage error model). For a perimeter series that makes the noise
scale with the fire's TOTAL area: Little Giant at ~80,000 ac carried
sd(growth) ~3,400 ac, so ~4,400 ac/day read zero beyond noise and the fire
was never rapid before its 14,780-ac run of Aug 7-8.

Consecutive perimeters of one fire share most of their boundary: the
re-mapping noise lives on the part of the edge that is re-drawn, so it
should scale with the perimeter's LENGTH (~ sqrt(area)), not its area.

Data: every capture the live belief would have observed (the watcher-order
replay of bvp_fetch.pick_captures, BEFORE daily thinning; gaps >= 3 h, the
belief's min spacing) for the 11 fires. A fire does not shrink, so a
NEGATIVE apparent growth is pure noise; on the stalled tail (the capture
already within 2 % of the fire's final size, later than its last 10 %/day
window) the apparent growth is noise plus a small creep. Fits:
  sd = k * sqrt(A)          (A acres, sd acres: the perimeter-length proxy)
  sd = c * A                (the legacy area-fraction form, for comparison)
by the RMS of the negative changes (unbiased for a symmetric noise around a
~zero true growth; a lower bound where the true growth is positive) and a
robust 68th percentile of |change| on the stalled tail (an upper bound:
it includes real creep). Perimeter length is measured from the daily
captures' rings (a lower bound: rings are stored at <= 400 vertices) to
check the sqrt(A) proxy.

Writes scratch_review/belief_vs_physics/v2_noise.json and prints a table.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_fetch as F  # noqa: E402

H_S = 3600.0
MIN_SPACING_H = 3.0          # the belief's fire_belief_min_obs_spacing_h


def observed_series(rows: list) -> list:
    """[(t_s, acres)] the live belief would observe: bvp_fetch's watcher-
    order replay (date sanity, newer-time rule, partial-row filter), then
    the belief's own >= 3 h spacing (a closer one is skipped)."""
    out, _drops = [], []
    acc = []
    last_t = None
    for r in sorted(rows, key=lambda r: r["oid"]):
        t = r["t_s"]
        if not (F.T_MIN_S <= t <= F.T_MAX_S) or not r["acres"] > 0.0:
            continue
        if last_t is not None and t <= last_t:
            continue
        acc.append((t, r["acres"]))
        last_t = t
    run_max = 0.0
    kept = []
    for t, a in acc:
        if run_max > 0.0 and a < F.MIN_FRAC_OF_MAX * run_max:
            continue
        run_max = max(run_max, a)
        kept.append((t, a))
    for t, a in kept:
        if out and (t - out[-1][0]) / H_S < MIN_SPACING_H:
            continue
        out.append((t, a))
    return out


def ring_perimeter_m(rings: list) -> float:
    tot = 0.0
    for ring in rings:
        n = len(ring)
        for i in range(n):
            la1, lo1 = ring[i]
            la2, lo2 = ring[(i + 1) % n]
            dy = (la2 - la1) * 111_320.0
            dx = (lo2 - lo1) * 111_320.0 * math.cos(math.radians(0.5 * (la1 + la2)))
            tot += math.hypot(dx, dy)
    return tot


def main() -> int:
    fires = F.load_fires()
    pairs = []
    shape = []
    for key in fires:
        rows = F.read_json(os.path.join(F.DATA_DIR, "rows", key + ".json"))["rows"]
        ser = observed_series(rows)
        final = ser[-1][1]
        # the stalled tail: after the fire's last >= 10 %/day window and
        # within 2 % of its final size
        last_fast = -1
        for i in range(1, len(ser)):
            dt_d = (ser[i][0] - ser[i - 1][0]) / 86400.0
            g = (ser[i][1] - ser[i - 1][1]) / ser[i - 1][1] / dt_d
            if g >= 0.10:
                last_fast = i
        for i in range(1, len(ser)):
            (ta, aa), (tb, ab) = ser[i - 1], ser[i]
            dt_h = (tb - ta) / H_S
            tail = i > last_fast and aa >= 0.98 * final
            pairs.append({"fire": key, "ta": ta, "dt_h": dt_h, "aa": aa,
                          "ab": ab, "d": ab - aa, "tail": tail})
        inc = F.read_json(os.path.join(F.DATA_DIR, "incidents", key + ".json"))
        for s in inc["snapshots"]:
            p = ring_perimeter_m(s["rings"])
            a_m2 = s["acres"] * 4046.8564224
            shape.append({"fire": key, "acres": s["acres"], "perim_km": p / 1000.0,
                          "perim_over_circle": p / (2.0 * math.sqrt(math.pi * a_m2))})

    neg = [p for p in pairs if p["d"] < 0.0]
    tail = [p for p in pairs if p["tail"]]

    def k_sqrt(ps):          # RMS of d / sqrt(A)
        return math.sqrt(statistics.mean((p["d"] / math.sqrt(p["aa"])) ** 2
                                         for p in ps))

    def c_frac(ps):          # RMS of d / A
        return math.sqrt(statistics.mean((p["d"] / p["aa"]) ** 2 for p in ps))

    def q(vals, qq):
        v = sorted(vals)
        pos = qq * (len(v) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(v) - 1)
        return v[lo] + (pos - lo) * (v[hi] - v[lo])

    # by size class: is sqrt(A) or A the better scaling?
    bins = [(0, 5000), (5000, 20000), (20000, 60000), (60000, 1e9)]
    by_bin = []
    for lo, hi in bins:
        b = [p for p in neg if lo <= p["aa"] < hi]
        t = [p for p in tail if lo <= p["aa"] < hi]
        if not b:
            continue
        by_bin.append({
            "acres": [lo, hi], "n_neg": len(b), "n_tail": len(t),
            "median_acres": statistics.median(p["aa"] for p in b),
            "rms_neg_ac": math.sqrt(statistics.mean(p["d"] ** 2 for p in b)),
            "k_sqrt_neg": k_sqrt(b), "c_frac_neg": c_frac(b),
            "tail_abs_p68_ac": q([abs(p["d"]) for p in t], 0.68) if t else None,
            "k_sqrt_tail_p68": (q([abs(p["d"]) / math.sqrt(p["aa"]) for p in t],
                                  0.68) if t else None)})
    out = {
        "n_pairs": len(pairs), "n_negative": len(neg), "n_tail": len(tail),
        "k_sqrt_neg_rms": k_sqrt(neg), "c_frac_neg_rms": c_frac(neg),
        "k_sqrt_neg_p68": q([abs(p["d"]) / math.sqrt(p["aa"]) for p in neg], 0.68),
        "k_sqrt_neg_p95": q([abs(p["d"]) / math.sqrt(p["aa"]) for p in neg], 0.95),
        "k_sqrt_tail_p68": q([abs(p["d"]) / math.sqrt(p["aa"]) for p in tail], 0.68),
        "k_sqrt_tail_p95": q([abs(p["d"]) / math.sqrt(p["aa"]) for p in tail], 0.95),
        "legacy_growth_sd_over_sqrtA_at": {
            str(a): math.hypot(math.hypot(0.03 * a, 10.0),
                               math.hypot(0.03 * a, 10.0)) / math.sqrt(a)
            for a in (1000, 5000, 20000, 80000, 160000)},
        "by_size": by_bin,
        "perimeter_over_circle": {
            "median": statistics.median(s["perim_over_circle"] for s in shape),
            "p10": q([s["perim_over_circle"] for s in shape], 0.10),
            "p90": q([s["perim_over_circle"] for s in shape], 0.90)},
        "negatives": sorted(neg, key=lambda p: p["d"])[:12],
        "pairs": pairs,
    }
    with open(os.path.join(_HERE, "v2_noise.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(f"pairs {len(pairs)}  negative {len(neg)}  stalled-tail {len(tail)}")
    print(f"k = RMS(d/sqrt(A)) on negatives {out['k_sqrt_neg_rms']:.2f}  "
          f"(p68 {out['k_sqrt_neg_p68']:.2f}, p95 {out['k_sqrt_neg_p95']:.2f}); "
          f"tail |d|/sqrt(A) p68 {out['k_sqrt_tail_p68']:.2f}, "
          f"p95 {out['k_sqrt_tail_p95']:.2f}")
    print(f"c = RMS(d/A) on negatives {out['c_frac_neg_rms']:.4f}")
    for b in by_bin:
        print(f"  {b['acres'][0]:>6,.0f}-{b['acres'][1]:>9,.0f} ac: n_neg {b['n_neg']:3d} "
              f"median A {b['median_acres']:>8,.0f}  RMS neg {b['rms_neg_ac']:>7,.0f} ac  "
              f"k_sqrt {b['k_sqrt_neg']:.2f}  c_frac {b['c_frac_neg']:.4f}  "
              f"tail p68 |d| {b['tail_abs_p68_ac'] if b['tail_abs_p68_ac'] is None else round(b['tail_abs_p68_ac'])} ac "
              f"(k {b['k_sqrt_tail_p68'] if b['k_sqrt_tail_p68'] is None else round(b['k_sqrt_tail_p68'], 2)})")
    print("legacy sd(growth)/sqrt(A):", {k: round(v, 1) for k, v in
                                          out["legacy_growth_sd_over_sqrtA_at"].items()})
    print("perimeter / equal-area circle:", {k: round(v, 2) for k, v in
                                             out["perimeter_over_circle"].items()})
    print("most negative changes:")
    for p in out["negatives"]:
        print(f"   {p['fire']:12s} A {p['aa']:>9,.0f}  d {p['d']:>+8,.0f} ac  "
              f"over {p['dt_h']:.1f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
