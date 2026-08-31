"""PASS-2 independent re-derivation of A9: matched-pair 0.00pp deltas and
cold_black_minutes vs stated Anderson derivation.

Writes scratch_review/rv2sci_a9.json.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")
from wgresponse import fuels, rothermel

D = r"C:\Users\colin\wg_response\scratch_review"
OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a9.json"

NAMES = [
    "opt_ember_te_a_c4", "opt_ember_te_a_c8", "opt_ember_te_b_c4",
    "opt_ember_te_b_c8", "opt_ember_te_c_c8",
    "opt_ember_tehy_a_c4", "opt_ember_tehy_a_c8", "opt_ember_tehy_b_c8",
    "opt_ember_rad550_c4", "opt_ember_rad550_c8",
    "opt_ember_rad600_c4", "opt_ember_rad600_c8",
    "opt_ember_hy20_c8", "opt_ember_s_c4", "opt_ember_s_c8",
    "opt_ember_cb_c4", "opt_ember_cb_c8",
    "opt_ember_flip_c4", "opt_ember_flip_c4b", "opt_ember_flip_c8",
    "opt_ember_sem10e_screen_c8",
]


def load(n):
    p = os.path.join(D, n + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def matched_pairs():
    rows = []
    missing = []
    for n in NAMES:
        j = load(n)
        if not j:
            missing.append(n)
            continue
        s, p = j["stats"], j["params"]
        e = s.get("e500", [0, 0, 0])
        rows.append((n, j, p, s, e))
    pairs = []
    for i in range(len(rows)):
        for k in range(i + 1, len(rows)):
            n1, j1, p1, s1, e1 = rows[i]
            n2, j2, p2, s2, e2 = rows[k]
            if j1.get("suite") != j2.get("suite"):
                continue
            if len(j1.get("rows", [])) != len(j2.get("rows", [])):
                continue
            keys = set(p1) | set(p2)
            diff = {kk for kk in keys if p1.get(kk) != p2.get(kk)}
            if len(diff) == 1 and e1[1] == e2[1]:
                kk = next(iter(diff))
                pairs.append(dict(
                    knob=kk,
                    a=str(p1.get(kk)), b=str(p2.get(kk)),
                    e500_a=e1[2], e500_b=e2[2],
                    delta_pp=e2[2] - e1[2],
                    denom=e1[1],
                    pp_per_home=(100.0 / e1[1]) if e1[1] else None,
                    viol=(s1.get("viol"), s2.get("viol")),
                    ovt=(s1.get("ov_ticks"), s2.get("ov_ticks")),
                    left=n1, right=n2,
                    params_left=p1, params_right=p2,
                ))
    zero = [p for p in pairs if abs(p["delta_pp"]) < 1e-9]
    return dict(n_loaded=len(rows), missing=missing, pairs=pairs, zero_delta=zero)


def cold_black_derivation():
    """Recompute stated derivation: max(class floor, 3 * 384 / sigma_coarsest).

    Coarsest timelag class present:
      100-h load > 0 -> SAV=30
      else 10-h load > 0 -> SAV=109
      else 1-h SAV from the model row
    Also compute engine bed sigma_char t_r (the formula surface_ros actually uses).
    """
    SAV_10, SAV_100 = 109.0, 30.0
    recs = []
    for code, row in fuels._IMPERIAL_ROWS.items():
        if not fuels.is_burnable(code):
            continue
        name, w1, w10, w100, wherb, wwoody, s1, sherb, swoody, depth_ft, mx = row
        shipped = fuels.cold_black_minutes(code)
        if w100 > 0:
            sav, klass = SAV_100, "100h"
        elif w10 > 0:
            sav, klass = SAV_10, "10h"
        else:
            sav, klass = s1, "1h"
        t_r = 384.0 / sav
        derived = 3.0 * t_r
        recs.append(dict(
            code=code, name=name, shipped=shipped,
            coarsest_class=klass, coarsest_sav=sav,
            t_r_coarsest=t_r, derived_3tr=derived,
            mismatch=shipped < derived - 0.05,  # permissive vs stated basis
            delta_min=shipped - derived,
            w1=w1, w10=w10, w100=w100, sav_1h=s1,
        ))
    from wgresponse import rothermel as R
    for rec in recs:
        fm = fuels.FUEL_TABLE[rec["code"]]
        try:
            bed = R.fuel_bed(fm, 0.06, 0.50, 0.90)
        except Exception:
            bed = None
        if bed is not None and getattr(bed, "sigma_char", 0):
            tr = 384.0 / bed.sigma_char
            rec["engine_sigma_char"] = bed.sigma_char
            rec["engine_t_r"] = tr
            rec["engine_3tr"] = 3.0 * tr
    permissive = [r for r in recs if r["mismatch"]]
    gs4 = next(r for r in recs if r["code"] == 124)
    return dict(
        sav_10h=SAV_10, sav_100h=SAV_100,
        gs4=gs4,
        permissive_vs_stated=permissive,
        all_models=recs,
    )


def engine_tr_fill(payload):
    """Fill engine sigma_char t_r using the same packing as surface_ros."""
    import inspect
    import wgresponse.rothermel as R
    # Find a builder
    names = [n for n in dir(R) if "bed" in n.lower() or "pack" in n.lower()]
    payload["rothermel_bed_helpers"] = names
    fm = fuels.FUEL_TABLE[124]
    # Call surface_ros_mps and also try to compute sigma from loads
    # Characteristic SAV of dead+live packing (Scott & Burgan / Rothermel):
    # we reuse R's own internal by calling the function that sets bed.sigma_char.
    src = inspect.getsource(R.surface_ros_mps)
    payload["surface_ros_mps_has_tr"] = "384" in src
    # Reconstruct: look at _prep / _fuel_components
    try:
        res = R.surface_ros_mps(fm, 0.06, 0.5, 0.9, 2.0, 0.0)
        payload["gs4_ros_at_probe"] = dict(
            ros_mps=res.ros_mps, intensity=res.intensity_kwm)
    except Exception as e:
        payload["gs4_ros_at_probe"] = f"{type(e).__name__}: {e}"
    # Manual characteristic SAV (dead 1h/10h/100h + live) in imperial:
    # This is a sanity check, not a reimplementation of the full dynamic load.
    return payload


def main():
    mp = matched_pairs()
    cb = cold_black_derivation()
    cb = engine_tr_fill(cb)
    out = dict(matched_pairs=mp, cold_black=cb)
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)
    print(f"matched pairs: {len(mp['pairs'])}  zero-delta: {len(mp['zero_delta'])}")
    for p in mp["pairs"]:
        print(f"  {p['knob']:<16} {p['a']:>12} -> {p['b']:<12}  "
              f"delta={p['delta_pp']:+7.2f}pp  [{p['left']} vs {p['right']}]")
    g = cb["gs4"]
    print(f"GS4 shipped={g['shipped']}  3*t_r(coarsest {g['coarsest_class']}, "
          f"sav={g['coarsest_sav']})={g['derived_3tr']:.1f}  "
          f"t_r={g['t_r_coarsest']:.2f}")
    print("permissive vs stated derivation:")
    for r in cb["permissive_vs_stated"]:
        print(f"  {r['code']} {r['name']}: ship {r['shipped']} vs 3tr {r['derived_3tr']:.1f} "
              f"({r['coarsest_class']})")


if __name__ == "__main__":
    main()
