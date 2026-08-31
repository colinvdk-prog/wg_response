"""RV2-CODE R4: is the hindcast 'coverage / early-miss' metric one-sided BY
CONSTRUCTION?

Claim: seeds are stamped at exactly T0 (perimeter_at_s = snap0.t_s), so every
NEWLY-burned cell has arrival > T0 in EVERY member => p90 > T0 always =>
  early_miss  := (p90 < T0)                 is IMPOSSIBLE (identically 0)
  covered_possible := (p10<=T1 and p90>=T0) collapses to (p10 <= T1)
                                            == p10_not_after_window
  late_miss   := NOT(p10 <= T1)             == 100% - coverage

Three independent proofs:
  A. ALGEBRA replay over every committed pair artifact (per-pair identities).
  B. RUNTIME: an actual ensemble_arrival on a synthetic tile — show min arrival
     over all members/cells outside the seed >= T0, and that the scorer's
     early branch is dead.
  C. RUNTIME MUTATION: force a genuinely over-predicting model (10x wind) and
     show early_miss STILL reports 0.0% — i.e. the metric cannot detect
     over-prediction even when over-prediction is extreme.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from _common import T0, cfg_default, flat_tile

from wgresponse import spread
from wgresponse.contracts import (LatLon, UNREACHED, WeatherForecast,
                                  WeatherSample)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def proof_a():
    print("=" * 78)
    print("A. ALGEBRA over committed pair artifacts")
    print("=" * 78)
    art = os.path.join(ROOT, "tools", "hindcast_data", "replay_results.json")
    d = json.load(open(art))
    rows = 0
    viol_early = viol_cover = viol_sum = 0
    for key in ("pairs", "pairs_wide"):
        for p in d.get(key, []):
            if not isinstance(p, dict):
                continue
            nb = p.get("n_new_burnable")
            if not nb:
                continue
            rows += 1
            e = p.get("early_miss", 0)
            cov = p.get("covered_possible", 0)
            nec = p.get("p10_not_after_window", 0)
            late = p.get("late_miss", 0)
            if e != 0:
                viol_early += 1
            if cov != nec:
                viol_cover += 1
            if cov + late != nb:
                viol_sum += 1
    print(f"  scored pairs examined            : {rows}")
    print(f"  pairs with early_miss != 0       : {viol_early}   <- expect 0")
    print(f"  pairs with covered != p10_nec    : {viol_cover}   <- expect 0")
    print(f"  pairs with covered+late != n_burn: {viol_sum}   <- expect 0")

    gs = json.load(open(os.path.join(ROOT, "scratch_review", "gust_sweep.json")))
    print("\n  gust_sweep.json (the band-width sweep the calibration cites):")
    print(f"  {'blend':>6s} {'cover':>9s} {'p10_nec':>9s} {'equal':>6s} "
          f"{'late':>9s} {'cov+late':>9s} {'early':>6s}")
    for r in gs:
        print(f"  {r['blend']:6.2f} {r['cover']:9.5f} {r['p10_nec']:9.5f} "
              f"{str(r['cover'] == r['p10_nec']):>6s} {r['late']:9.5f} "
              f"{r['cover']+r['late']:9.5f} {r['early']:6.1f}")


def mock_wx(t0, ws, n=120, wdir=270.0):
    return WeatherForecast(loc=LatLon(34.1, -117.1), samples=[
        WeatherSample(t_s=t0 + i * 3600.0, wind_speed_mps=ws, wind_gust_mps=ws,
                      wind_dir_deg=wdir, rh_pct=12.0, temp_c=35.0)
        for i in range(n)], source="rv2")


def proof_bc():
    print()
    print("=" * 78)
    print("B/C. RUNTIME: ensemble_arrival + the real scorer branch")
    print("=" * 78)
    cfg = cfg_default()
    tile = flat_tile(34.30, -117.30, 90.0, 60, 60, 102)
    spec = tile.spec
    # seed ring = a small square in the middle, stamped at exactly T0 (the
    # production A5 path the harness uses)
    from wgresponse import geo
    ctr = geo.rc_to_latlon(spec, 30, 30)
    d = 4 * spec.cell_m
    ring = [geo.destination(geo.destination(ctr, 0.0, d), 270.0, d),
            geo.destination(geo.destination(ctr, 0.0, d), 90.0, d),
            geo.destination(geo.destination(ctr, 180.0, d), 90.0, d),
            geo.destination(geo.destination(ctr, 180.0, d), 270.0, d)]

    for label, ws in (("nominal 8 m/s", 8.0), ("10x-FAST 80 m/s", 80.0)):
        ens = spread.ensemble_arrival(tile, [], mock_wx(T0, ws), T0, cfg,
                                      incident_id="RV2-COV",
                                      perimeter=[ring], perimeter_at_s=T0)
        nominal = ens.members[0].data
        seeds = {i for i in range(len(nominal)) if nominal[i] == T0}
        p10, p90 = ens.p10.data, ens.p90.data
        non_seed = [i for i in range(len(nominal)) if i not in seeds]
        finite_p90 = [p90[i] for i in non_seed if not math.isinf(p90[i])]
        finite_p10 = [p10[i] for i in non_seed if not math.isinf(p10[i])]
        print(f"\n  --- {label} ---")
        print(f"  seed cells (nominal == T0)           : {len(seeds)}")
        print(f"  non-seed cells                       : {len(non_seed)}")
        print(f"  min p10 over non-seed (finite)       : "
              f"T0 + {(min(finite_p10) - T0):.1f} s")
        print(f"  min p90 over non-seed (finite)       : "
              f"T0 + {(min(finite_p90) - T0):.1f} s")
        print(f"  any non-seed cell with p90 < T0 ?    : "
              f"{any(p90[i] < T0 for i in non_seed)}   <- early_miss trigger")
        # per-member floor: no member can arrive before T0 anywhere off-seed
        worst = min(min(col[i] for i in non_seed if not math.isinf(col[i]))
                    for col in (m.data for m in ens.members))
        print(f"  min arrival over ALL members/cells   : T0 + {(worst - T0):.1f} s")

        # Now run the REAL scorer branch on a synthetic T1 window and report
        # early_miss for both the honest and the 10x-fast model.
        for dt_h in (2.0, 12.0):
            t1 = T0 + dt_h * 3600.0
            early = late = cover = nec = n = 0
            for i in non_seed:
                lo, hi = p10[i], p90[i]
                n += 1
                if lo == UNREACHED or lo > t1:
                    late += 1
                elif hi < T0:
                    early += 1
                if lo <= t1 and hi >= T0:
                    cover += 1
                if lo <= t1:
                    nec += 1
            print(f"  window T0..T0+{dt_h:4.1f}h  n={n:5d}  "
                  f"cover={100*cover/n:6.2f}%  p10_nec={100*nec/n:6.2f}%  "
                  f"late={100*late/n:6.2f}%  EARLY={100*early/n:6.2f}%")


def main():
    proof_a()
    proof_bc()
    print()
    print("=" * 78)
    print("VERDICT: early_miss is a DEAD BRANCH; 'coverage' is an alias for")
    print("p10<=T1. A model 10x too fast still scores 0.0% early.")
    print("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
