"""Ember-zone survivability objective (founder 2026-07-29): maximize the
fraction of homes COATED among homes within EMBER RANGE of actual burn —
reached(R) = front_reached OR nearest-burned-cell distance <= R — with the
headline objective at R = 1000 m, on the standard 60-scenario suite.

CLI (every knob is a real ResponseConfig field or session parameter):
  py -3 scratch_review/opt_ember.py --tag NAME [--suite screen|full|quick]
      [--crews 4] [--deploy 15] [--egress 30] [--slack SLACK]
      [--ember-radius 200] [--coat-early 0|1] [--hold 24] [--workers N]
Writes scratch_review/opt_ember_<tag>.json and prints one summary line:
  TAG ... front=A/B p% e300=..% e500=..% e1000=C/D p% viol=V ovt=T minmarg=M
Safety honesty: `viol` counts scoreboard violations under the CONFIGURED
egress buffer (lowering --egress redefines the law — flag it, don't hide it);
min_margin_h is the worst crew safety margin seen (h). Deterministic seeds =
the bench suite's; results across configs are directly comparable."""
import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, HORIZON_TICKS, scenarios  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.contracts import seeded_rng  # noqa: E402
from wgresponse.simui import LOCATIONS, load_tile, wui_cells  # noqa: E402


def cluster_locs(key: str, n: int, k: int, seed: str):
    """n homes in k tight WUI clusters (founder placement axis: 'homes set up
    in clusters' / 'chosen to reduce travel'). Anchors are farthest-point
    sampled from the map's WUI band (clusters spread across the community,
    deterministic via seeded_rng — hash-seed independent); each cluster is
    the n/k nearest WUI-band cells to its anchor (90 m grid => intra-cluster
    spacing ~90-250 m, near-zero drive time). k=1 => a single 40-home
    cluster, travel between homes effectively eliminated."""
    tile, _syn = load_tile(key, cell_m=90.0)
    band = [geo.rc_to_latlon(tile.spec, r, c) for (r, c) in wui_cells(tile)]
    rng = seeded_rng("cluster", key, seed, n, k)
    anchors = [band[rng.randrange(len(band))]]
    while len(anchors) < k:
        far = max(band, key=lambda p: min(geo.haversine_m(p, a)
                                          for a in anchors))
        anchors.append(far)
    per = [n // k + (1 if i < n % k else 0) for i in range(k)]
    taken = set()
    out = []
    for anchor, cnt in zip(anchors, per):
        near = sorted(band, key=lambda p: geo.haversine_m(p, anchor))
        got = 0
        for p in near:
            kkey = (round(p.lat, 6), round(p.lon, 6))
            if kkey in taken:
                continue
            taken.add(kkey)
            out.append(p)
            got += 1
            if got >= cnt:
                break
    return out

DIST_CAP_M = 1200.0
RADII = (300.0, 500.0, 1000.0)


def _burn_dist_m(session, loc, now_s):
    best = None
    for f in session._fires.values():
        ens = f.ens
        if ens is None:
            continue
        truth = ens.members[0] if getattr(ens, "members", None) else ens.p50
        spec = truth.spec
        rc = geo.latlon_to_rc(spec, loc)
        if rc is None:
            continue
        r0, c0 = rc
        max_k = int(DIST_CAP_M / spec.cell_m) + 1
        for k in range(max_k + 1):
            if best is not None and k * spec.cell_m > best:
                break
            hit = None
            for r in range(r0 - k, r0 + k + 1):
                if not (0 <= r < spec.nrows):
                    continue
                cols = (range(c0 - k, c0 + k + 1)
                        if r in (r0 - k, r0 + k) else (c0 - k, c0 + k))
                for c in cols:
                    if not (0 <= c < spec.ncols):
                        continue
                    t = truth.data[r * spec.ncols + c]
                    if math.isfinite(t) and t <= now_s:
                        d = geo.haversine_m(loc, geo.rc_to_latlon(spec, r, c))
                        if hit is None or d < hit:
                            hit = d
            if hit is not None:
                best = hit if best is None else min(best, hit)
                break
    return best


def run_one(job):
    sc, cfg_over, n_clients, n_crews, placement, staging = job
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    locs = None
    if placement and placement != "scattered":
        k = {"cluster1": 1, "cluster4": 4, "cluster8": 8}[placement]
        locs = cluster_locs(sc["map"], n_clients, k, sc["seed"])
    crew_starts = None
    if staging == "near":
        # founder assumption "all crews within 30 minutes or less": crews
        # start PARKED among the client homes (evenly spread over the seeded
        # order). The client draw is deterministic per seed, so a throwaway
        # session yields the same homes the scored session will have.
        probe = simui.SimSession(sc["map"], n_clients, n_crews,
                                 seed=sc["seed"], cfg_overrides=dict(cfg_over),
                                 fire_zone="standard", client_locs=locs)
        homes = [c.loc for c in probe._clients]
        crew_starts = [homes[(i * len(homes)) // n_crews]
                       for i in range(n_crews)]
    s = simui.SimSession(sc["map"], n_clients, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg_over), fire_zone="standard",
                         client_locs=locs, crew_locs=crew_starts)
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        return dict(seed=sc["seed"], error="off_tile")
    st = None
    max_viol = 0
    ov_ticks = 0
    for _ in range(HORIZON_TICKS):
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        max_viol = max(max_viol, v)
        if st.get("crew_overruns"):
            ov_ticks += 1
    now_s = s._now_s
    by_id = {cl.client_id: cl for cl in s._clients}
    homes = []
    for row in st["clients"]:
        p50 = row.get("eta_p50_s")
        front = (row.get("state") == "impacted"
                 or (isinstance(p50, (int, float)) and p50 <= now_s))
        d = _burn_dist_m(s, by_id[row["client_id"]].loc, now_s)
        homes.append(dict(gelled=bool(row.get("gel_applied")),
                          front=bool(front),
                          dist=None if d is None else round(d, 1),
                          outcome=row["outcome"]))
    return dict(seed=sc["seed"], map=sc["map"], inten=sc["inten"],
                viol=max_viol, ov_ticks=ov_ticks,
                min_margin_h=st["scoreboard"].get("min_margin_h"),
                homes=homes)


def pool_stats(rows):
    out = {}
    homes = [h for r in rows if not r.get("error") for h in r["homes"]]
    nf = sum(1 for h in homes if h["front"])
    gf = sum(1 for h in homes if h["front"] and h["gelled"])
    out["front"] = (gf, nf, 100.0 * gf / nf if nf else 0.0)
    for R in RADII:
        n = sum(1 for h in homes
                if h["front"] or (h["dist"] is not None and h["dist"] <= R))
        g = sum(1 for h in homes
                if (h["front"] or (h["dist"] is not None and h["dist"] <= R))
                and h["gelled"])
        out[f"e{R:.0f}"] = (g, n, 100.0 * g / n if n else 0.0)
    out["viol"] = sum(r.get("viol", 0) for r in rows if not r.get("error"))
    out["ov_ticks"] = sum(r.get("ov_ticks", 0) for r in rows
                          if not r.get("error"))
    margins = [r["min_margin_h"] for r in rows
               if not r.get("error")
               and isinstance(r.get("min_margin_h"), (int, float))]
    out["min_margin_h"] = round(min(margins), 2) if margins else None
    out["coated_total"] = sum(1 for h in homes if h["gelled"])
    out["homes_total"] = len(homes)
    return out


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
    ap.add_argument("--erosion-euclid", type=int, default=0,
                    help="cfg.corridor_erosion_euclidean (CORRIDOR HONESTY)")
    ap.add_argument("--tiered", default="",
                    help="fuel-tiered erosion 'FAST,SHRUB,SLOW' radii in m "
                         "(e.g. '550,200,150'); empty = off")
    ap.add_argument("--escape-hyst", type=float, default=0.0,
                    help="cfg.escape_hysteresis_margin_min (churn fix); 0=off")
    ap.add_argument("--route-alts", type=int, default=1,
                    help="cfg.route_alternatives: 1 legacy, 2-3 candidates")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

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
    if args.erosion_euclid:
        cfg["corridor_erosion_euclidean"] = True
    if args.route_alts and args.route_alts > 1:
        cfg["route_alternatives"] = int(args.route_alts)
    if args.tiered:
        fast_m, shrub_m, slow_m = (float(x) for x in args.tiered.split(","))
        cfg.update({"corridor_erosion_tiered": True,
                    "corridor_erosion_fast_m": fast_m,
                    "corridor_erosion_shrub_m": shrub_m,
                    "corridor_erosion_slow_m": slow_m})
    if args.escape_hyst:
        cfg["escape_hysteresis_margin_min"] = float(args.escape_hyst)

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
                               erosion_euclid=args.erosion_euclid,
                               route_alts=args.route_alts,
                               tiered=args.tiered,
                               escape_hyst=args.escape_hyst),
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
