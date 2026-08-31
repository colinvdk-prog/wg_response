"""PASS-2 independent re-derivation of A3: grass-floor vs moisture of
extinction, plus August ERA5 fire-hour exceedance.

Writes scratch_review/rv2sci_a3.json. Does not touch rvclaude_* files.
"""
from __future__ import annotations
import dataclasses
import glob
import inspect
import json
import os
import statistics
import sys

sys.path.insert(0, r"C:\Users\colin\wg_response")
from wgresponse import fuels, rothermel, spread
from wgresponse.contracts import ResponseConfig

OUT = r"C:\Users\colin\wg_response\scratch_review\rv2sci_a3.json"
WX = r"C:\Users\colin\wg_response\scratch_review\hindcast_aug_data\weather"


def rh_crossing(mx: float, temp_c: float) -> float:
    lo, hi = 0.0, 100.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if fuels.dead_fuel_moisture_frac(temp_c, mid) > mx:
            hi = mid
        else:
            lo = mid
    return hi


def floor_probe() -> dict:
    cfg_on = dataclasses.replace(ResponseConfig(), grass_ros_floor_frac=0.10)
    U10 = 10.0
    codes = (102, 104, 122, 165, 183)
    rows = []
    extinction_override = []
    for code in codes:
        fm = fuels.FUEL_TABLE[code]
        mx = fm.mx_dead_frac
        sig = inspect.signature(rothermel.midflame_wind_mps)
        mid = (rothermel.midflame_wind_mps(U10, fm)
               if len(sig.parameters) == 2
               else rothermel.midflame_wind_mps(U10))
        ff = spread._ros_floor_frac(code, cfg_on)
        floor_mps = ff * U10
        moistures = (0.03, 0.06, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.60)
        mrows = []
        for m in moistures:
            res = rothermel.surface_ros_mps(fm, m, 0.5, 0.9, mid, 0.0)
            fl = max(res.ros_mps, floor_mps)
            inf = (fl / res.ros_mps) if res.ros_mps > 1e-12 else float("inf")
            flame = (0.0775 * (res.intensity_kwm ** 0.46)
                     if res.intensity_kwm > 0 else 0.0)
            rec = dict(m_dead=m, roth_mps=res.ros_mps, roth_kmh=res.ros_mps * 3.6,
                       floored_kmh=fl * 3.6, inflation=inf,
                       intensity_kwm=res.intensity_kwm, flame_m=flame)
            mrows.append(rec)
            if m + 1e-12 >= mx and res.ros_mps <= 1e-12 and fl > 1e-12:
                extinction_override.append(dict(
                    code=code, name=fm.name, mx=mx, m_dead=m,
                    roth_kmh=res.ros_mps * 3.6,
                    floored_kmh=fl * 3.6,
                    intensity_kwm=res.intensity_kwm,
                    flame_m=flame,
                ))
        rows.append(dict(
            code=code, name=fm.name, mx_dead=mx, midflame_mps=mid,
            floor_frac=ff, floor_kmh=floor_mps * 3.6, rows=mrows,
        ))
    return dict(U10_mps=U10, fuels=rows, extinction_override=extinction_override)


def moisture_stat() -> dict:
    crossings = {}
    for mx, lbl in ((0.15, "GR/GS"), (0.20, "TL3"), (0.25, "TU5")):
        crossings[lbl] = dict(mx=mx, rh_at_25C=rh_crossing(mx, 25.0))
    t_scan = {T: rh_crossing(0.15, T) for T in (5, 10, 15, 25, 30, 40)}
    per_file = []
    allm = []
    for p in sorted(glob.glob(os.path.join(WX, "*.json"))):
        wd = json.load(open(p, encoding="utf-8"))
        h = wd["payload"].get("hourly", {})
        rhs = h.get("relative_humidity_2m") or h.get("relativehumidity_2m") or []
        ts = h.get("temperature_2m") or []
        ms = [fuels.dead_fuel_moisture_frac(t, r)
              for t, r in zip(ts, rhs)
              if t is not None and r is not None]
        if not ms:
            continue
        allm.extend(ms)
        rec = dict(
            file=os.path.basename(p), n=len(ms),
            median=statistics.median(ms), mx=max(ms),
            frac_gt_015=sum(1 for m in ms if m > 0.15) / len(ms),
            frac_gt_020=sum(1 for m in ms if m > 0.20) / len(ms),
        )
        per_file.append(rec)
    n = len(allm)
    pooled = dict(
        n=n,
        median=statistics.median(allm) if allm else None,
        mean=statistics.mean(allm) if allm else None,
        frac_gt_015=sum(1 for m in allm if m > 0.15) / n if n else None,
        frac_gt_020=sum(1 for m in allm if m > 0.20) / n if n else None,
        frac_gt_025=sum(1 for m in allm if m > 0.25) / n if n else None,
        n_files=len(per_file),
    )
    return dict(crossings=crossings, rh_vs_T_GRGS=t_scan,
                per_file=per_file, pooled=pooled)


def main():
    out = dict(floor=floor_probe(), moisture=moisture_stat())
    # highlight GR2/GS2 at m_dead>=0.15
    hits = [h for h in out["floor"]["extinction_override"]
            if h["code"] in (102, 122) and h["m_dead"] >= 0.15]
    out["gr2_gs2_at_extinction"] = hits
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    print("wrote", OUT)
    print("--- extinction override (Rothermel=0, floor>0, I=0) ---")
    for h in out["floor"]["extinction_override"]:
        print(f"  FBFM {h['code']} m={h['m_dead']:.2f} mx={h['mx']:.2f} "
              f"roth={h['roth_kmh']:.4f} floored={h['floored_kmh']:.3f} "
              f"I={h['intensity_kwm']:.3f} L={h['flame_m']:.3f}")
    p = out["moisture"]["pooled"]
    print(f"--- moisture pooled n={p['n']}  >mx0.15={100*p['frac_gt_015']:.2f}%  "
          f">mx0.20={100*p['frac_gt_020']:.2f}%")
    print(f"  GR/GS RH crossing @25C = {out['moisture']['crossings']['GR/GS']['rh_at_25C']:.1f}%")


if __name__ == "__main__":
    main()
