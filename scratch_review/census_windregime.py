"""Sub-30-mph wind-regime extension to the ember-zone belt-survivability
sweep (axis-sensitivity census, 2026-08-01).

opt_ember.py has NO --intensity flag: its scenario grid comes from
scratch_review.bench_dispatch.scenarios(), whose INTENS axis is fixed to
("high", "extreme") -- confirmed via `py -3 scratch_review/opt_ember.py
--help` (no such flag) and reading bench_dispatch.py source. The suite has
never been run at "low" (8 mph) or "moderate" (18 mph) -- the other two
presets in wgresponse.simui.INTENSITY_WEATHER.

This script does NOT edit opt_ember.py or bench_dispatch.py. It mirrors
their scenario-grid shape and REUSES opt_ember.run_one / opt_ember.pool_stats
by import (identical physics/scoring code, zero duplication risk) over a
grid built directly here: 5 maps x wind_off 0 x dist 6000/8000 x ONE
intensity (low or moderate) = 10 scenarios x 40 homes = 400 home-instances
per invocation -- half of opt_ember's 20-scenario --suite screen grid (which
is wind_off=0 x dist 6000/8000 x intensity high/extreme = 20). Two
invocations (low + moderate) reconstruct a grid the same size (20 scenarios)
as the screen suite, just swapping the intensity axis.

Config knobs (deploy=10, egress=30, ember_radius=200, hold=24) are pinned to
match opt_ember_flip_c4.json / opt_ember_flip_c8.json exactly (both
--suite screen --deploy 10 runs) so low/moderate results are directly
comparable to those recorded high/extreme numbers -- together they complete
the wind-speed curve at c4/c8.

CLI:
  py -3 scratch_review/census_windregime.py --intensity low|moderate
      --crews 4|8 [--clients 40] [--deploy 10] [--egress 30]
      [--ember-radius 200] [--hold 24] [--workers 10]
Writes scratch_review/census_wind_<intensity>_c<crews>.json (same stats
block opt_ember writes, produced by opt_ember.pool_stats) and prints one
summary line in opt_ember's format.

Run all 4 configs (SEQUENTIALLY, PYTHONHASHSEED=0 PYTHONIOENCODING=utf-8):
  --intensity low      --crews 4
  --intensity low      --crews 8
  --intensity moderate --crews 4
  --intensity moderate --crews 8
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE  # noqa: E402
from scratch_review.opt_ember import run_one, pool_stats  # noqa: E402

MAPS = ["malibu_ca", "paradise_ca", "santa_rosa_ca", "boulder_co",
        "park_city_ut"]
DISTS = (6000.0, 8000.0)
WIND_MPH = {"low": 8.0, "moderate": 18.0}   # wgresponse.simui.INTENSITY_WEATHER


def scenarios(intensity):
    """5 maps x wind_off 0 x dist 6000/8000 x `intensity` = 10 scenarios,
    same seed-string convention as bench_dispatch/opt_ember
    ('map|+off|dist|intensity') so rows are drop-in comparable/poolable."""
    return [dict(map=m, wind_off=0.0, dist=d, inten=intensity,
                 seed=f"{m}|+0|{d:.0f}|{intensity}")
            for m in MAPS for d in DISTS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intensity", required=True, choices=("low", "moderate"))
    ap.add_argument("--crews", type=int, required=True)
    ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--deploy", type=float, default=10.0)
    ap.add_argument("--egress", type=float, default=30.0)
    ap.add_argument("--ember-radius", type=float, default=200.0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    cfg = dict(BASE)
    cfg.update({"deploy_min": args.deploy,
                "dispatch_plan_on_roads": True,
                "dispatch_hold_h": args.hold,
                "egress_buffer_min": args.egress,
                "ember_threat_radius_m": args.ember_radius})

    scen = scenarios(args.intensity)
    jobs = [(sc, cfg, args.clients, args.crews, "scattered", "default")
            for sc in scen]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_one, jobs))
    st = pool_stats(rows)
    tag = f"wind_{args.intensity}_c{args.crews}"
    payload = dict(tag=tag, intensity=args.intensity,
                   wind_mph=WIND_MPH[args.intensity],
                   params=dict(crews=args.crews, deploy=args.deploy,
                               egress=args.egress,
                               ember_radius=args.ember_radius,
                               hold=args.hold, clients=args.clients,
                               placement="scattered", staging="default"),
                   stats=st, rows=rows)
    out = f"scratch_review/census_wind_{args.intensity}_c{args.crews}.json"
    json.dump(payload, open(out, "w"), indent=1)
    f = st["front"]
    e1 = st["e1000"]
    e3 = st["e300"]
    e5 = st["e500"]
    print(f"{tag:<24} n={len(scen)} front={f[0]}/{f[1]} {f[2]:5.1f}%  "
          f"e300={e3[2]:5.1f}%  e500={e5[2]:5.1f}%  "
          f"e1000={e1[0]}/{e1[1]} {e1[2]:5.1f}%  viol={st['viol']} "
          f"ovt={st['ov_ticks']} minmarg={st['min_margin_h']} "
          f"coated={st['coated_total']}")
    print("wrote", out)


if __name__ == "__main__":
    main()
