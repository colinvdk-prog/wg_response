"""hazard_separation_radii — READ-ONLY: the PASS / CAUTION / IMPASS separation
radii per fuel class group and intensity preset. These are the numbers the
corridor rule keys on: distance from the road sample to the nearest ACTIVELY
FLAMING burnable cell.

py -3 scratch_review/hazard_separation_radii.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hazard_transit_physics import CLASS_GROUPS, flux_kwm2, run_fuel, sep_for_flux
from wgresponse.fuels import FUEL_TABLE

SIM_SPEED_MPS = 17.9 / (1.35 * 1.4)      # 9.4710 m/s
CELL_M = 90.0
Q_PASS, Q_CAUT = 5.0, 12.5
D_PASS, D_CAUT = 92.0, 290.0

REPRESENTATIVE = {"GR": 102, "GS": 122, "SH": 142, "TU": 165, "TL": 184, "SB": 202}
WORST = {"GR": 107, "GS": 124, "SH": 145, "TU": 163, "TL": 189, "SB": 204}
# p90-by-tile-abundance models: the heaviest model that is actually >=3% of any
# cached tile (from hazard_roadside_fuels): GR2/GR1, GS2/GS1, SH2/SH5, TU5/TU2,
# TL4/TL3/TL5/TL6/TL8, SB none.
TILE_P90 = {"GR": 102, "GS": 122, "SH": 145, "TU": 165, "TL": 185, "SB": 202}


def dose_at(L, emis, d, t_s):
    q = flux_kwm2(L, d, emis)
    return q, q ** (4.0 / 3.0) * t_s


def radii(L, emis, t_s):
    """(d_pass, d_caut): separation where BOTH the flux and the dose criteria
    are met for an exposure of t_s seconds."""
    d_q_pass = sep_for_flux(L, emis, Q_PASS)
    d_q_caut = sep_for_flux(L, emis, Q_CAUT)
    q_dose_pass = (D_PASS / t_s) ** 0.75 if t_s > 0 else math.inf
    q_dose_caut = (D_CAUT / t_s) ** 0.75 if t_s > 0 else math.inf
    d_d_pass = sep_for_flux(L, emis, q_dose_pass)
    d_d_caut = sep_for_flux(L, emis, q_dose_caut)
    return max(d_q_pass, d_d_pass), max(d_q_caut, d_d_caut)


def main():
    t_1cell = CELL_M / SIM_SPEED_MPS
    print("=" * 100)
    print(f"crew speed {SIM_SPEED_MPS:.3f} m/s ({SIM_SPEED_MPS*3.6:.1f} km/h); "
          f"one 90 m cell = {t_1cell:.2f} s exposure")
    print(f"PASS: q<={Q_PASS} kW/m2 AND dose<={D_PASS} TDU   "
          f"CAUTION: q<={Q_CAUT} AND dose<={D_CAUT}")
    print("=" * 100)

    for tag, table in (("representative", REPRESENTATIVE),
                       ("tile-p90 (heaviest model >=3% of a cached tile)", TILE_P90),
                       ("worst-in-class", WORST)):
        print(f"\n### fuel selection: {tag}")
        print(f"{'grp':>4} {'model':<26} | " + " | ".join(
            f"{p:^25}" for p in ("low", "moderate", "high", "extreme")))
        print(f"{'':>4} {'':<26} | " + " | ".join(
            f"{'L(m)  d_pass  d_caut':^25}" for _ in range(4)))
        for gname, _c in CLASS_GROUPS:
            code = table[gname]
            cells = []
            for preset in ("low", "moderate", "high", "extreme"):
                r = run_fuel(code, preset)
                dp, dc = radii(r["L_m"], r["emis"], t_1cell)
                cells.append(f"{r['L_m']:5.2f} {dp:7.1f} {dc:7.1f}")
            print(f"{gname:>4} {FUEL_TABLE[code].name[:26]:<26} | " +
                  " | ".join(f"{c:^25}" for c in cells))

    print()
    print("=" * 100)
    print("EXPRESSED IN 90-m RASTER CELLS (what the corridor sampler can actually see)")
    print("d_cells = ceil((d_required - road_half_width_assumed) / 90) ; "
          "road half+shoulder taken as 6.5 m (residential far lane)")
    print("=" * 100)
    for tag, table in (("representative", REPRESENTATIVE), ("worst-in-class", WORST)):
        print(f"\n### {tag}")
        print(f"{'grp':>4} | " + " | ".join(f"{p:^19}" for p in
                                            ("low", "moderate", "high", "extreme")))
        for gname, _c in CLASS_GROUPS:
            code = table[gname]
            cells = []
            for preset in ("low", "moderate", "high", "extreme"):
                r = run_fuel(code, preset)
                dp, dc = radii(r["L_m"], r["emis"], t_1cell)
                np_ = max(0, math.ceil((dp - 6.5) / CELL_M))
                nc = max(0, math.ceil((dc - 6.5) / CELL_M))
                cells.append(f"pass>={np_}c caut>={nc}c")
            print(f"{gname:>4} | " + " | ".join(f"{c:^19}" for c in cells))

    print()
    print("=" * 100)
    print("FLUX vs SEPARATION, representative fuels, 'high' preset")
    print("=" * 100)
    print(f"{'grp':>4} {'L':>5} {'eps':>5} | " +
          " ".join(f"{d:>7.0f}m" for d in (3, 6.5, 10, 20, 45, 90, 135, 180)))
    for gname, _c in CLASS_GROUPS:
        r = run_fuel(REPRESENTATIVE[gname], "high")
        qs = [flux_kwm2(r["L_m"], d, r["emis"]) for d in (3, 6.5, 10, 20, 45, 90, 135, 180)]
        print(f"{gname:>4} {r['L_m']:5.2f} {r['emis']:5.3f} | " +
              " ".join(f"{q:8.2f}" for q in qs))
    print()
    print("same, worst-in-class, 'extreme' preset")
    print(f"{'grp':>4} {'L':>5} {'eps':>5} | " +
          " ".join(f"{d:>7.0f}m" for d in (3, 6.5, 10, 20, 45, 90, 135, 180)))
    for gname, _c in CLASS_GROUPS:
        r = run_fuel(WORST[gname], "extreme")
        qs = [flux_kwm2(r["L_m"], d, r["emis"]) for d in (3, 6.5, 10, 20, 45, 90, 135, 180)]
        print(f"{gname:>4} {r['L_m']:5.2f} {r['emis']:5.3f} | " +
              " ".join(f"{q:8.2f}" for q in qs))

    print()
    print("=" * 100)
    print("STANDING (crew working) vs TRANSIT separation — the honest multiplier")
    print("=" * 100)
    print(f"{'grp':>4} {'preset':<9} {'L':>6} {'4xL':>7} {'8xL':>7} "
          f"{'d(7kW)':>8} {'d_transit(1cell)':>17} {'ratio to d7':>12}")
    for preset in ("moderate", "high", "extreme"):
        for gname, _c in CLASS_GROUPS:
            r = run_fuel(REPRESENTATIVE[gname], preset)
            d7 = sep_for_flux(r["L_m"], r["emis"], 7.0)
            dp, dc = radii(r["L_m"], r["emis"], t_1cell)
            ratio = (dc / d7) if d7 > 0 else float("nan")
            print(f"{gname:>4} {preset:<9} {r['L_m']:6.2f} {4*r['L_m']:7.1f} "
                  f"{8*r['L_m']:7.1f} {d7:8.1f} {dc:17.1f} {ratio:12.2f}")


if __name__ == "__main__":
    main()
