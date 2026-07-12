"""Alerted-regime validation (founder protocol claim, HOA_40_PLAYBOOK §1).

Claim under test: an event visible on the alert ladder BEFORE arrival (fire
detected 12-36h out — the WATCH window) lets a small crew count coat essentially
the whole threatened community, vs ~50% in the reactive (short-notice) regime.

Method: extended-zone tiles (fire ignites tens of km out), 40 homes / 2 crews /
3-person 15-min deploy, hazard wind, high intensity, real engine end-to-end
(ladder, dispatch, corridors, spotting, grass floors). Run until the fire has
swept past (or horizon); measure coated-before-arrival among THREATENED homes,
plus lead time at first alert. Violations must stay 0.

Usage: py -3 scratch_review/bench_alerted.py [--dist 15000] [--maps ...]"""
import os, sys, json, math, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS

CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10, "deploy_min": 15.0}

def run(key, dist_m, ticks, n_clients=40, n_crews=2, seed="alerted"):
    loc = LOCATIONS[key]
    wd = loc.hazard_wind_dir_deg
    s = simui.SimSession(key, n_clients, n_crews, seed=f"{seed}|{key}",
                         cfg_overrides=dict(CFG), fire_zone="extended")
    # find a burnable ignition cell near the target range along the hazard
    # bearing (extended tiles include developed/water cells the surface model
    # cannot ignite — scan bearing +/- and range shrink until one takes)
    err = None
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6):
            ign = geo.destination(loc.center, wd + db, dist_m * shrink)
            try:
                s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=wd)
                err = None
                break
            except ValueError as e:
                err = str(e)
        if err is None:
            break
    if err is not None:
        return dict(map=key, error=err)
    first_alert_lead_h = None
    st = None
    max_viol = 0
    ov_ticks = 0
    for t in range(ticks):
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        if v > max_viol:
            max_viol = v
        if st.get("crew_overruns"):
            ov_ticks += 1
        if first_alert_lead_h is None and st["alert_feed"]:
            # lead at FIRST ladder event: earliest client p10 minus now
            etas = [c.get("eta_p10_s") for c in st["clients"]
                    if isinstance(c.get("eta_p10_s"), (int, float))
                    and math.isfinite(c.get("eta_p10_s"))]
            if etas:
                first_alert_lead_h = round(
                    (min(etas) - st["sim_now_s"]) / 3600.0, 2)
    sb = st["scoreboard"]
    outs = sb.get("outcomes", {}) or {}
    threatened = 40 - int(outs.get("not_threatened", 0))
    coated = int(sb.get("served", 0))
    t_ign = min(f["t_ignite_s"] for f in st["fires"])
    return dict(
        map=key, dist_km=dist_m / 1000.0,
        sim_hours=round((st["sim_now_s"] - t_ign) / 3600.0, 1),
        first_alert_lead_h=first_alert_lead_h,
        threatened=threatened, coated=coated,
        pct=round(100.0 * coated / threatened, 1) if threatened else None,
        saved=int(outs.get("saved", 0)),
        protected_impacted=int(outs.get("protected_then_impacted", 0)),
        abandoned=int(outs.get("abandoned_unsavable", 0)),
        violations=int(max_viol),
        overruns=int(ov_ticks),
        withdrawn=int(sb.get("withdrawn", 0)))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", nargs="*",
                    default=["malibu_ca", "paradise_ca", "santa_rosa_ca",
                             "boulder_co", "park_city_ut"])
    ap.add_argument("--dist", type=float, default=15000.0)
    ap.add_argument("--ticks", type=int, default=240)   # 60 h at 15 min
    args = ap.parse_args()
    rows = []
    for k in args.maps:
        r = run(k, args.dist, args.ticks)
        rows.append(r)
        if "error" in r:
            print(f"{k:<15} ERROR {r['error']}")
        else:
            print(f"{k:<15} lead@alert={r['first_alert_lead_h']}h  "
                  f"coated={r['coated']}/{r['threatened']} ({r['pct']}%)  "
                  f"viol={r['violations']} ov={r['overruns']} wd={r['withdrawn']}  "
                  f"[{r['sim_hours']}h sim]")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "bench_alerted.json")
    json.dump(rows, open(out, "w"), indent=2)
    ok = [r for r in rows if "error" not in r and r["threatened"]]
    if ok:
        tot_c = sum(r["coated"] for r in ok); tot_t = sum(r["threatened"] for r in ok)
        print(f"\nALERTED TOTAL: {tot_c}/{tot_t} = {100.0*tot_c/tot_t:.1f}% coated "
              f"(2 crews; reactive comparator ~42%)")
