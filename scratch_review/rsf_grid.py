"""scratch_review/rsf_grid.py — Rancho Santa Fe / Covenant, CA (rancho_santa_fe_ca)
100-home optimization grid, on the real fetched LANDFIRE tile
(data/tiles/rancho_santa_fe_ca.wgt).

Mirrors scratch_review/opt_ember.py's run_one/pool_stats/_burn_dist_m
machinery EXACTLY (imported unmodified — this file makes no wgresponse/ or
opt_ember.py edits) over a CUSTOM 18-scenario matrix specific to this
location instead of bench_dispatch.scenarios()'s 5-map suite:
    intensity in (moderate, high, extreme)
    x dist    in (6000, 8000)                 meters, upwind of center
    x wind_off in (0, +30, -30)                degrees off the hazard axis
  = 18 scenarios, seed f"rsf|{wind_off:+d}|{dist}|{inten}".

CLI mirrors opt_ember.py's knobs, defaulted for this sweep (--clients 100
--deploy 10 --workers 10). Corridor-erosion / cold-black knobs are
DELIBERATELY left untouched (never added to cfg here) per the founder note
2026-08-01: "sim defaults already carry the calibrated corridor stack +
stage-1 cold-black — do not override them."

Usage — run once per CONFIG tag (sequentially; each is its own process pool):
  PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c8_scatter  --crews 8  --placement scattered
  PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c10_scatter --crews 10 --placement scattered
  PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c12_scatter --crews 12 --placement scattered
  PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c10_cluster --crews 10 --placement cluster8
  PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c12_cluster --crews 12 --placement cluster8
Writes scratch_review/rsf_grid_<tag>.json (same params+stats+rows shape as
opt_ember_<tag>.json) and prints the same summary line format as opt_ember.py.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.opt_ember import BASE, pool_stats, run_one  # noqa: E402

MAP_KEY = "rancho_santa_fe_ca"
INTENS = ("moderate", "high", "extreme")
DISTS = (6000, 8000)
WIND_OFFS = (0, 30, -30)


def scenarios():
    """The 18 RSF scenarios: intensity x dist x wind_off (map fixed)."""
    out = []
    for inten in INTENS:
        for dist in DISTS:
            for wind_off in WIND_OFFS:
                out.append(dict(map=MAP_KEY, wind_off=wind_off, dist=dist,
                                inten=inten,
                                seed=f"rsf|{wind_off:+d}|{dist}|{inten}"))
    return out


def _worker(job):
    """Top-level (picklable under Windows spawn) shim around opt_ember.run_one."""
    return run_one(job)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--crews", type=int, default=10)
    ap.add_argument("--clients", type=int, default=100)
    ap.add_argument("--deploy", type=float, default=10.0)
    ap.add_argument("--egress", type=float, default=30.0)
    ap.add_argument("--slack", type=float, default=None)
    ap.add_argument("--ember-radius", type=float, default=200.0)
    ap.add_argument("--coat-early", type=int, default=0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--placement", default="scattered",
                    choices=("scattered", "cluster1", "cluster4", "cluster8"))
    ap.add_argument("--staging", default="default", choices=("default", "near"))
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    # Same cfg-construction law as opt_ember.main(): BASE (spotting_enabled,
    # grass_ros_floor_frac) + the args this sweep actually varies. NOTHING
    # else is added -- corridor_erosion_*/transit_cold_black_by_class/
    # route_alternatives/escape_hysteresis_margin_min all stay at
    # ResponseConfig's own session defaults (the calibrated corridor stack +
    # stage-1 cold-black), exactly per the "do not override them" note.
    cfg = dict(BASE)
    cfg.update({"deploy_min": args.deploy,
                "dispatch_plan_on_roads": True,
                "dispatch_hold_h": args.hold,
                "egress_buffer_min": args.egress,
                "ember_threat_radius_m": args.ember_radius})
    if args.coat_early:
        cfg["dispatch_late_shift"] = False
    if args.slack is not None:
        cfg["slack_min"] = args.slack

    scen = scenarios()
    jobs = [(sc, cfg, args.clients, args.crews, args.placement, args.staging)
            for sc in scen]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_worker, jobs))
    st = pool_stats(rows)
    payload = dict(tag=args.tag, suite="rsf18", map=MAP_KEY,
                   params=dict(crews=args.crews, deploy=args.deploy,
                               egress=args.egress,
                               ember_radius=args.ember_radius,
                               coat_early=args.coat_early, hold=args.hold,
                               slack=args.slack, clients=args.clients,
                               placement=args.placement,
                               staging=args.staging),
                   stats=st, rows=rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"rsf_grid_{args.tag}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    errs = [r["seed"] for r in rows if r.get("error")]
    f_ = st["front"]
    e1 = st["e1000"]
    e3 = st["e300"]
    e5 = st["e500"]
    print(f"{args.tag:<20} n={len(scen)} front={f_[0]}/{f_[1]} {f_[2]:5.1f}%  "
          f"e300={e3[2]:5.1f}%  e500={e5[2]:5.1f}%  "
          f"e1000={e1[0]}/{e1[1]} {e1[2]:5.1f}%  viol={st['viol']} "
          f"ovt={st['ov_ticks']} minmarg={st['min_margin_h']} "
          f"coated={st['coated_total']}/{st['homes_total']}")
    if errs:
        print(f"  !! off_tile errors on {len(errs)} scenario(s): {errs}")
    print("wrote", out)


if __name__ == "__main__":
    main()
