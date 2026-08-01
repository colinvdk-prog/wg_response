"""hazard_passability_table — READ-ONLY: the (roadside fuel class x road class)
PASSABILITY matrix for the transit-hazard model spec.

Bands (see the spec):
  PASSABLE   q_peak <= 5.0 kW/m^2  AND transit dose <= 92 TDU   (pain threshold)
  CAUTION    q_peak <= 12.5 kW/m^2 AND transit dose <= 290 TDU  (blister threshold)
  IMPASSABLE otherwise

py -3 scratch_review/hazard_passability_table.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hazard_transit_physics import (CLASS_GROUPS, E_BLACK_KWM2, KAPPA,
                                    flux_kwm2, run_fuel, sep_for_flux)
from wgresponse.fuels import FUEL_TABLE

# road class -> (paved width m, assumed cleared shoulder m, free-flow m/s)
ROADS = {
    "track":        (4.0,  0.0,  7.0),
    "service":      (5.0,  0.0,  7.0),
    "residential":  (8.0,  0.5, 11.0),
    "unclassified": (9.0,  1.0, 11.0),
    "tertiary":     (9.0,  1.0, 15.0),
    "secondary":    (11.0, 1.5, 18.0),
    "primary":      (13.0, 2.5, 22.0),
    "trunk":        (13.0, 2.5, 22.0),
    "motorway":     (24.0, 5.0, 29.0),
}
FIRE_TRAFFIC = 1.4          # cfg.fire_traffic_factor
CELL_M = 90.0
#: sim.py _eff_ground_speed_mps with shipped defaults:
#: 17.9 / (1.35 * 1.4) = 9.4709 m/s = 34.1 km/h — the ONE speed every crew
#: moves at today (chord and road legs alike).
SIM_SPEED_MPS = 17.9 / (1.35 * 1.4)
USE_SIM_SPEED = True        # False => per-class free-flow / fire_traffic

Q_PASS, D_PASS = 5.0, 92.0
Q_CAUT, D_CAUT = 12.5, 290.0

# representative model per class group: the load-median burnable actually seen
# on the cached tiles (see hazard_roadside_fuels output)
REPRESENTATIVE = {
    "GR": 102,   # GR2 low load grass  — 17.9% paradise, 43.6% boulder
    "GS": 122,   # GS2 moderate grass-shrub — 12.5% paradise, 14.3% malibu
    "SH": 142,   # SH2 moderate load shrub — 16.8% malibu
    "TU": 165,   # TU5 very high timber-shrub — 8.1% paradise
    "TL": 184,   # TL4 small downed logs — 9.9% paradise
    "SB": 202,
}
WORST = {"GR": 107, "GS": 124, "SH": 145, "TU": 163, "TL": 189, "SB": 204}


def band(q, dose):
    if q <= Q_PASS and dose <= D_PASS:
        return "PASS"
    if q <= Q_CAUT and dose <= D_CAUT:
        return "CAUT"
    return "IMPASS"


def evaluate(code, preset, road, lane="far", run_cells=1.0):
    r = run_fuel(code, preset)
    if r is None:
        return None
    paved, shoulder, v_free = ROADS[road]
    d = shoulder + (0.75 if lane == "far" else 0.25) * paved
    q = flux_kwm2(r["L_m"], d, r["emis"])
    v = SIM_SPEED_MPS if USE_SIM_SPEED else v_free / FIRE_TRAFFIC
    t = run_cells * CELL_M / v
    dose = q ** (4.0 / 3.0) * t
    return q, dose, band(q, dose), r["L_m"], r["emis"], d, t


def main():
    print("=" * 108)
    print("PASSABILITY MATRIX — leading-edge / ESTABLISHED FRONT alongside the road")
    print("far-lane travel, ONE 90-m cell of exposed run, crew speed = class free-flow / 1.4")
    print(f"bands: PASS q<={Q_PASS} & dose<={D_PASS} | CAUT q<={Q_CAUT} & dose<={D_CAUT} | else IMPASS")
    print("=" * 108)
    for preset in ("moderate", "high", "extreme"):
        for tag, table in (("representative", REPRESENTATIVE), ("worst-in-class", WORST)):
            print(f"\n### preset={preset}  fuel={tag}")
            print(f"{'fuel':<6} {'model':<28} {'L(m)':>6} {'eps':>5} | " +
                  " ".join(f"{r[:11]:>13}" for r in ROADS))
            for gname, _codes in CLASS_GROUPS:
                code = table[gname]
                cells = []
                L = e = 0.0
                for road in ROADS:
                    out = evaluate(code, preset, road)
                    if out is None:
                        cells.append("     -")
                        continue
                    q, dose, b, L, e, _d, _t = out
                    cells.append(f"{b:<6}{q:5.1f}/{dose:4.0f}")
                print(f"{gname:<6} {FUEL_TABLE[code].name[:28]:<28} {L:6.2f} {e:5.3f} | " +
                      " ".join(f"{c:>13}" for c in cells))

    print()
    print("=" * 108)
    print("SENSITIVITY: exposed-run length (representative fuels, 'high' preset, secondary road)")
    print("=" * 108)
    for gname, _c in CLASS_GROUPS:
        code = REPRESENTATIVE[gname]
        row = []
        for rc in (1, 2, 3, 5):
            out = evaluate(code, "high", "secondary", run_cells=rc)
            if out:
                q, dose, b, _L, _e, _d, t = out
                row.append(f"{rc}cell:{b:<6}{dose:5.0f}TDU({t:4.1f}s)")
        print(f"  {gname:<4} {FUEL_TABLE[code].name[:26]:<26} " + "  ".join(row))

    print()
    print("=" * 108)
    print("SENSITIVITY: near lane vs far lane (high preset, residential 8 m)")
    print("=" * 108)
    for gname, _c in CLASS_GROUPS:
        code = REPRESENTATIVE[gname]
        n = evaluate(code, "high", "residential", lane="near")
        f = evaluate(code, "high", "residential", lane="far")
        if n and f:
            print(f"  {gname:<4} near d={n[5]:4.1f}m q={n[0]:6.2f} dose={n[1]:6.0f} {n[2]:<6} |"
                  f"  far d={f[5]:4.1f}m q={f[0]:6.2f} dose={f[1]:6.0f} {f[2]}")

    print()
    print("=" * 108)
    print("RESIDUAL (post-frontal) PHASE — areal heat release of the heavy dead classes")
    print("q_area = w_class * heat / t_res ; t_res = 384/sigma (Anderson per size class)")
    print("=" * 108)
    print(f"{'code':>4} {'model':<32} {'w10h':>7} {'w100h':>7} {'q10h':>8} {'q100h':>8} "
          f"{'t_cold_surface':>15}")
    for gname, codes in CLASS_GROUPS:
        for code in codes:
            if code not in FUEL_TABLE:
                continue
            f = FUEL_TABLE[code]
            h = f.heat_kjkg
            q10 = f.load_10h_kgm2 * h / (384.0 / 109.0 * 60.0) if f.load_10h_kgm2 else 0.0
            q100 = f.load_100h_kgm2 * h / (384.0 / 30.0 * 60.0) if f.load_100h_kgm2 else 0.0
            t_cold = 384.0 / 2000.0
            if f.load_10h_kgm2 > 0:
                t_cold = 384.0 / 109.0
            if f.load_100h_kgm2 > 0:
                t_cold = 384.0 / 30.0
            print(f"{code:>4} {f.name[:32]:<32} {f.load_10h_kgm2:7.3f} "
                  f"{f.load_100h_kgm2:7.3f} {q10:8.1f} {q100:8.1f} {t_cold:12.1f} min")

    print()
    print("=" * 108)
    print("FLAME DEPTH / CELL — 'flame density' at the leading edge (D_f / 90 m cell)")
    print("=" * 108)
    print(f"{'grp':>4} " + " ".join(f"{p:>26}" for p in
                                    ("low", "moderate", "high", "extreme")))
    for gname, codes in CLASS_GROUPS:
        cells = []
        for preset in ("low", "moderate", "high", "extreme"):
            rows = [run_fuel(c, preset) for c in codes if c in FUEL_TABLE]
            rows = [r for r in rows if r]
            ds = sorted(r["depth_m"] for r in rows)
            n = len(ds)
            med = ds[n // 2] if n % 2 else 0.5 * (ds[n // 2 - 1] + ds[n // 2])
            cells.append(f"D={med:7.2f}m  {100*min(1.0, med/CELL_M):5.1f}% cell")
        print(f"{gname:>4} " + " ".join(f"{c:>26}" for c in cells))


if __name__ == "__main__":
    main()
