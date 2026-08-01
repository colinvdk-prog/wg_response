"""Perfect-information ceiling wrapper around opt_ember.

Collapses forecast ensemble wind/direction perturbations to zero so dispatch
sees near-truth arrival bands. Fires themselves are unchanged (truth member is
the unperturbed draw; spotting stays on). Does not modify opt_ember.py.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.opt_ember import (  # noqa: E402
    BASE,
    pool_stats,
    run_one,
)
from scratch_review.bench_dispatch import scenarios  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--suite", default="screen",
                    choices=("quick", "screen", "full"))
    ap.add_argument("--crews", type=int, default=4)
    ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--deploy", type=float, default=15.0)
    ap.add_argument("--egress", type=float, default=30.0)
    ap.add_argument("--slack", type=float, default=None)
    ap.add_argument("--ember-radius", type=float, default=200.0)
    ap.add_argument("--coat-early", type=int, default=0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--placement", default="scattered",
                    choices=("scattered", "cluster1", "cluster4", "cluster8"))
    ap.add_argument("--staging", default="default",
                    choices=("default", "near"))
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    cfg = dict(BASE)
    cfg.update({"deploy_min": args.deploy,
                "dispatch_plan_on_roads": True,
                "dispatch_hold_h": args.hold,
                "egress_buffer_min": args.egress,
                "ember_threat_radius_m": args.ember_radius})
    cfg.update({"wind_perturb_frac": 0.0, "dir_perturb_deg": 0.0})
    if args.coat_early:
        cfg["dispatch_late_shift"] = False
    if args.slack is not None:
        cfg["slack_min"] = args.slack

    scen = scenarios()
    if args.suite == "quick":
        scen = [s for s in scen if s["wind_off"] == 0.0
                and s["inten"] == "extreme" and s["dist"] == 6000.0]
    elif args.suite == "screen":
        scen = [s for s in scen if s["wind_off"] == 0.0]

    jobs = [(sc, cfg, args.clients, args.crews, args.placement, args.staging)
            for sc in scen]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_one, jobs))
    st = pool_stats(rows)
    payload = dict(tag=args.tag, suite=args.suite,
                   params=dict(crews=args.crews, deploy=args.deploy,
                               egress=args.egress,
                               ember_radius=args.ember_radius,
                               coat_early=args.coat_early, hold=args.hold,
                               slack=args.slack, clients=args.clients,
                               placement=args.placement,
                               staging=args.staging,
                               wind_perturb_frac=0.0, dir_perturb_deg=0.0),
                   stats=st, rows=rows)
    out = f"scratch_review/opt_ember_{args.tag}.json"
    json.dump(payload, open(out, "w"), indent=1)
    f = st["front"]
    e1 = st["e1000"]
    e3 = st["e300"]
    e5 = st["e500"]
    print(f"{args.tag:<24} n={len(scen)} front={f[0]}/{f[1]} {f[2]:5.1f}%  "
          f"e300={e3[2]:5.1f}%  e500={e5[2]:5.1f}%  "
          f"e1000={e1[0]}/{e1[1]} {e1[2]:5.1f}%  viol={st['viol']} "
          f"ovt={st['ov_ticks']} minmarg={st['min_margin_h']} "
          f"coated={st['coated_total']}")
    print("wrote", out)


if __name__ == "__main__":
    main()
