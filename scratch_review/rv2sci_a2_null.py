"""PASS-2 independent re-implementation of the A2 isotropic-dilation null.

Exact EDT of T0, no physics, same August as-deployed pairs/masks/burnable
filter as the engine. Writes scratch_review/rv2sci_a2.json only.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")
from tools import hindcast_lib as H
from tools.hindcast_harness import _incident_domain, _load_all_incidents
from wgresponse.contracts import ResponseConfig
from wgresponse.terrain import LfpsTerrain, load_ascii_tile
from wgresponse.spread import _perimeter_interior_cells
from wgresponse.rothermel import is_burnable

DATA = r"C:\Users\colin\wg_response\scratch_review\hindcast_aug_data"
OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a2.json"
INF = float("inf")
RADII_M = [0, 250, 500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000,
           6000, 8000, 10000, 12000, 15000, 20000, 30000]


def edt2(binary, nrows, ncols):
    """Exact squared Euclidean distance transform (Felzenszwalb-Huttenlocher)."""
    f = [0.0 if binary[i] else INF for i in range(nrows * ncols)]
    for c in range(ncols):
        d = _dt1([f[r * ncols + c] for r in range(nrows)])
        for r in range(nrows):
            f[r * ncols + c] = d[r]
    for r in range(nrows):
        d = _dt1(f[r * ncols:(r + 1) * ncols])
        f[r * ncols:(r + 1) * ncols] = d
    return f


def _dt1(fv):
    n = len(fv)
    d = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -INF
    z[1] = INF
    for q in range(1, n):
        if fv[q] == INF:
            continue
        while True:
            p = v[k]
            if fv[p] == INF:
                if k > 0:
                    k -= 1
                    continue
                else:
                    break
            s = ((fv[q] + q * q) - (fv[p] + p * p)) / (2.0 * q - 2.0 * p)
            if s <= z[k] and k > 0:
                k -= 1
            else:
                break
        p = v[k]
        s = (-INF if fv[p] == INF
             else ((fv[q] + q * q) - (fv[p] + p * p)) / (2.0 * q - 2.0 * p))
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        p = v[k]
        d[q] = INF if fv[p] == INF else (q - p) * (q - p) + fv[p]
    return d


def interp(xs, ys, tgt):
    for i in range(1, len(xs)):
        lo, hi = xs[i - 1], xs[i]
        if lo <= tgt <= hi or hi <= tgt <= lo:
            if hi == lo:
                return ys[i]
            f = (tgt - lo) / (hi - lo)
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    return None


def main():
    rep = json.load(open(os.path.join(DATA, "aug_replay.json"), encoding="utf-8"))
    scored = {(r["name"], r["t0_s"], r["t1_s"]): r for r in rep["rows"]
              if r["variant"] == "as-deployed" and r["n_new_burnable"] > 0}
    print(f"scored pairs to reproduce: {len(scored)}", flush=True)
    cfg0 = ResponseConfig()
    agg = {R: {"cov": 0, "area": 0} for R in RADII_M}
    tot_new = 0
    eng_cov = 0
    eng_area_cells = 0
    per_pair = []
    mask_mismatches = []
    for slug, inc in _load_all_incidents(DATA):
        bbox, cell_m = _incident_domain(inc, cfg0)
        lfps = LfpsTerrain(cache_dir=os.path.join(DATA, "lfps"))
        cp = lfps.cache_path(bbox, cell_m)
        if not (cp and os.path.exists(cp)):
            print(f"  SKIP {inc.name}: no tile cache {cp}", flush=True)
            continue
        tile = load_ascii_tile(cp)
        spec = tile.spec
        nr, nc = spec.nrows, spec.ncols
        fdata = tile.fuel.data
        burn = [is_burnable(int(round(fdata[i]))) for i in range(nr * nc)]
        for a, b in zip(inc.snapshots, inc.snapshots[1:]):
            key = (inc.name, a.t_s, b.t_s)
            if key not in scored:
                continue
            row = scored[key]
            rings = [H.subsample_ring(r, H.MAX_RING_VERTS) for r in a.rings]
            t0mask = set(_perimeter_interior_cells(spec, rings))
            t1mask = H.polygon_interior_mask(spec, b.rings)
            new = [i for i in (t1mask - t0mask) if burn[i]]
            if not new:
                continue
            src = [False] * (nr * nc)
            for i in t0mask:
                src[i] = True
            d2 = edt2(src, nr, nc)
            tot_new += len(new)
            eng_cov += row["covered_possible"]
            cell_ac = cell_m * cell_m / 4046.8564224
            eng_area_cells += row["modeled_new_area_ac_p50"] / cell_ac
            match = (len(new) == row["n_new_burnable"])
            if not match:
                mask_mismatches.append(dict(
                    name=inc.name, dt_h=row["dt_h"],
                    derived_n=len(new), published_n=row["n_new_burnable"]))
            pp = {}
            for R in RADII_M:
                rc = (R / cell_m) ** 2
                cov = sum(1 for i in new if d2[i] <= rc)
                area = sum(1 for i in range(nr * nc)
                           if d2[i] <= rc and burn[i] and i not in t0mask)
                agg[R]["cov"] += cov
                agg[R]["area"] += area
                pp[R] = (cov, area)
            per_pair.append(dict(
                name=inc.name, dt_h=row["dt_h"], n_new=len(new),
                published_n_new_burnable=row["n_new_burnable"],
                n_match=match,
                engine_cov=row["covered_possible"],
                engine_area_cells=row["modeled_new_area_ac_p50"] / cell_ac,
                cell_m=cell_m, null_at_R= {str(R): pp[R] for R in RADII_M},
            ))
            print(f"  {inc.name:<16} +{row['dt_h']:5.1f}h new={len(new):6d} "
                  f"pub={row['n_new_burnable']:6d} match={match} "
                  f"engine cov={100*row['covered_possible']/len(new):6.2f}% "
                  f"area_cells={row['modeled_new_area_ac_p50']/cell_ac:9.0f}",
                  flush=True)

    eng_cov_pct = 100.0 * eng_cov / tot_new if tot_new else None
    tgt = eng_area_cells / tot_new if tot_new else None
    xs = [agg[R]["area"] / tot_new for R in RADII_M]
    ys = [100 * agg[R]["cov"] / tot_new for R in RADII_M]
    null_at_budget = interp(xs, ys, tgt)
    null_area_for_eng = interp(ys, xs, eng_cov_pct)
    skill = (null_area_for_eng / tgt) if (null_area_for_eng and tgt) else None
    out = dict(
        n_pairs=len(per_pair), tot_new=tot_new, eng_cov=eng_cov,
        eng_cov_pct=eng_cov_pct, eng_area_cells=eng_area_cells,
        eng_area_x=tgt, null_cov_at_engine_budget_pct=null_at_budget,
        null_area_x_to_match_engine=null_area_for_eng,
        skill_x=skill, mask_mismatches=mask_mismatches,
        n_mask_match=sum(1 for p in per_pair if p["n_match"]),
        null_curve=[{"R_m": R, "cov_pct": 100 * agg[R]["cov"] / tot_new,
                     "pred_cells": agg[R]["area"],
                     "x_obs": agg[R]["area"] / tot_new} for R in RADII_M],
        per_pair=[{k: v for k, v in p.items() if k != "null_at_R"}
                  for p in per_pair],
    )
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("\n================ POOLED ================")
    print(f"ENGINE coverage={eng_cov_pct:.2f}%  area={tgt:.2f}x")
    print(f"NULL at engine budget: {null_at_budget:.2f}%")
    print(f"NULL area to match engine cov: {null_area_for_eng:.2f}x")
    print(f"skill = {skill:.2f}x   mask matches {out['n_mask_match']}/{len(per_pair)}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
