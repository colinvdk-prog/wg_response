"""Founder probe: survivability among homes the fire ACTUALLY reached.

Replays the standard 60-scenario suite under the SHIPPED stack (bench_hold24
overrides: spotting + grass floor + deploy 15 + plan-on-roads + hold 24) and
records, per home at horizon end:
  outcome tag, gel_applied, front-reached (median arrival passed — the sim's
  own IMPACTED definition), and distance to the nearest ground-truth BURNED
  cell (ring search over the persisted member-0 arrival field) so "reached"
  can also be counted at ember radii (embers reach far beyond the front).
Output: scratch_review/probe_impacted.json (per-scenario, per-home rows).
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import (BASE, HORIZON_TICKS, N_CLIENTS,  # noqa: E402
                                           N_CREWS, scenarios)
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

OVERRIDES = {"deploy_min": 15, "dispatch_plan_on_roads": True,
             "dispatch_hold_h": 24}
DIST_CAP_M = 2000.0                     # ring-search ceiling; beyond -> None


def _burn_dist_m(session, loc, now_s):
    """Distance (m) from `loc` to the nearest cell whose GROUND-TRUTH arrival
    (persisted member-0 field) is <= now_s, ring-searching outward per fire;
    min over fires. None when nothing burned within DIST_CAP_M."""
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
                        if r in (r0 - k, r0 + k)
                        else (c0 - k, c0 + k))
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
    return None if best is None else round(best, 1)


def run_one(sc):
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(BASE)
    cfg.update(OVERRIDES)
    s = simui.SimSession(sc["map"], N_CLIENTS, N_CREWS, seed=sc["seed"],
                         cfg_overrides=cfg, fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        return dict(seed=sc["seed"], error="off_tile", homes=[])
    st = None
    for _ in range(HORIZON_TICKS):
        st = s.tick()
    now_s = s._now_s
    homes = []
    by_id = {cl.client_id: cl for cl in s._clients}
    for row in st["clients"]:
        cl = by_id[row["client_id"]]
        p50 = row.get("eta_p50_s")
        front = (row.get("state") == "impacted"
                 or (isinstance(p50, (int, float)) and p50 <= now_s))
        homes.append(dict(
            client_id=row["client_id"], outcome=row["outcome"],
            gelled=bool(row.get("gel_applied")), front_reached=bool(front),
            dist_burn_m=_burn_dist_m(s, cl.loc, now_s)))
    return dict(seed=sc["seed"], map=sc["map"], wind_off=sc["wind_off"],
                dist=sc["dist"], inten=sc["inten"], homes=homes)


def main():
    out = []
    scen = scenarios()
    for i, sc in enumerate(scen):
        r = run_one(sc)
        out.append(r)
        n_front = sum(1 for h in r.get("homes", []) if h["front_reached"])
        n_300 = sum(1 for h in r.get("homes", [])
                    if h["front_reached"] or (h["dist_burn_m"] is not None
                                              and h["dist_burn_m"] <= 300.0))
        print(f"[{i + 1}/{len(scen)}] {r['seed']}: front={n_front} "
              f"front|<=300m={n_300}", flush=True)
        json.dump(out, open("scratch_review/probe_impacted.json", "w"),
                  indent=1)
    print("wrote scratch_review/probe_impacted.json")


if __name__ == "__main__":
    main()
