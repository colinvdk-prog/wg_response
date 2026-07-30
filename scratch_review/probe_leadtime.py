"""Per-home LEAD-TIME probe (founder 2026-07-29: survivability vs time from
fire DETECTION to fire ARRIVAL at the home). Detection is t=0 (ignition is
seen on the first tick); a home's lead is the ground-truth arrival hour of
the fire at the home's cell (member-0 truth field), so survivability can be
bucketed by lead (2/3/4/6 h). Homes whose cell never burns in-horizon have
lead=None (fire never arrived at the structure; ember-adjacent exposure is
the e1000 analysis, not this one). Writes raw per-home rows only — the
bucketed statistics are computed downstream (Opus/Grok cross-check).

Usage: py -3 scratch_review/probe_leadtime.py --tag lead_base --suite full
           [--crews 4] [--deploy 15] [--workers 10]
"""
import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, HORIZON_TICKS, scenarios  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402


def run_one(job):
    sc, cfg_over, n_clients, n_crews = job
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    s = simui.SimSession(sc["map"], n_clients, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg_over), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        return dict(seed=sc["seed"], error="off_tile")
    t0 = s._now_s
    st = None
    for _ in range(HORIZON_TICKS):
        st = s.tick()
    by_id = {cl.client_id: cl for cl in s._clients}
    truths = []
    for f in s._fires.values():
        ens = f.ens
        if ens is None:
            continue
        truths.append(ens.members[0] if getattr(ens, "members", None)
                      else ens.p50)
    homes = []
    for row in st["clients"]:
        home = by_id[row["client_id"]].loc
        arr = None
        for truth in truths:
            rc = geo.latlon_to_rc(truth.spec, home)
            if rc is None:
                continue
            t = truth.data[rc[0] * truth.spec.ncols + rc[1]]
            if math.isfinite(t):
                h = (t - t0) / 3600.0
                if arr is None or h < arr:
                    arr = h
        homes.append(dict(arr_h=None if arr is None else round(arr, 3),
                          gelled=bool(row.get("gel_applied")),
                          outcome=row["outcome"]))
    return dict(seed=sc["seed"], map=sc["map"], inten=sc["inten"],
                viol=st["scoreboard"].get("violations", 0), homes=homes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--suite", default="full", choices=("quick", "full"))
    ap.add_argument("--crews", type=int, default=4)
    ap.add_argument("--deploy", type=float, default=15.0)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    cfg = dict(BASE)
    cfg.update({"deploy_min": args.deploy, "dispatch_plan_on_roads": True,
                "dispatch_hold_h": 24.0})
    scen = scenarios()
    if args.suite == "quick":
        scen = [s for s in scen if s["wind_off"] == 0.0
                and s["inten"] == "extreme" and s["dist"] == 6000.0]
    jobs = [(sc, cfg, 40, args.crews) for sc in scen]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(run_one, jobs))
    out = f"scratch_review/probe_leadtime_{args.tag}.json"
    json.dump(dict(tag=args.tag, suite=args.suite,
                   params=dict(crews=args.crews, deploy=args.deploy),
                   rows=rows), open(out, "w"), indent=1)
    reached = [h for r in rows if not r.get("error") for h in r["homes"]
               if h["arr_h"] is not None]
    print(f"{args.tag}: {len(scen)} scenarios, homes with fire arrival: "
          f"{len(reached)}; wrote {out}")


if __name__ == "__main__":
    main()
