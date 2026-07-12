"""Headless dispatch benchmark — the objective function for the improvement loop.

Runs the REAL engine (deployed config: spotting + grass floor on, real roads,
standard zone) over a deterministic matrix of maps x wind x ignition geometry x
intensity. Objective = TOTAL homes protected (served = gel applied) summed across
the suite. HARD GATE = total safety violations + crew overruns must be 0 (the
safety law is inviolable; a lever that trades safety for homes is disqualified).

Usage:
  py -3 scratch_review/bench_dispatch.py --label baseline
  py -3 scratch_review/bench_dispatch.py --label edd --set dispatch_seq_edd=1
  py -3 scratch_review/bench_dispatch.py --quick            # 2-scenario smoke
Overrides after --set are merged onto the deployed base cfg (repeatable).
Writes scratch_review/bench_<label>.json (per-scenario rows + totals)."""
import os, sys, time, json, argparse
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS

BASE = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}

MAPS = ["malibu_ca", "paradise_ca", "santa_rosa_ca", "boulder_co", "park_city_ut"]
WIND_OFFSETS = [-40.0, 0.0, 40.0]        # rotate the line of fire off the hazard axis
DISTS = [6000.0, 8000.0]                 # near vs far ignition (fits standard ±9km tile)
INTENS = ["high", "extreme"]
N_CLIENTS = int(os.environ.get("BENCH_CLIENTS", "40"))
N_CREWS = int(os.environ.get("BENCH_CREWS", "4"))
HORIZON_TICKS = 60                        # 15 h at 15 min/tick — fire sweeps through

def scenarios(quick=False):
    if quick:
        combos = [("boulder_co", 0.0, 7000.0, "high"),
                  ("santa_rosa_ca", 0.0, 7000.0, "high")]
    else:
        combos = [(m, off, d, it) for m in MAPS for off in WIND_OFFSETS
                  for d in DISTS for it in INTENS]
    return [dict(map=m, wind_off=off, dist=d, inten=it,
                 seed=f"{m}|{off:+.0f}|{d:.0f}|{it}")
            for (m, off, d, it) in combos]

def run_scenario(sc, overrides, horizon=HORIZON_TICKS):
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    ign = geo.destination(loc.center, wd, sc["dist"])      # upwind of center
    cfg = dict(BASE); cfg.update(overrides)
    kw = {}
    if os.environ.get("BENCH_NO_ROADS"):
        kw["roads_module"] = None     # crews fly the chords corridors checked
    s = simui.SimSession(sc["map"], N_CLIENTS, N_CREWS, seed=sc["seed"],
                         cfg_overrides=cfg, fire_zone="standard", **kw)
    # guard: if the offset pushes ignition off-tile, pull it in toward center
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        return dict(seed=sc["seed"], map=sc["map"], wind_off=sc["wind_off"],
                    dist=sc["dist"], inten=sc["inten"], served=0, violations=0,
                    overruns=0, withdrawn=0, relocations=0, min_margin_h=None,
                    saved=0, protected_impacted=0, abandoned=0, triaged=0,
                    not_threatened=N_CLIENTS, threatened=0, error="off_tile")
    st = None
    max_viol = 0          # per-TICK max (scoreboard.violations is instantaneous
    ov_ticks = 0          # for overruns — final-tick reads can hide transients)
    for _ in range(horizon):
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        if v > max_viol:
            max_viol = v
        if st.get("crew_overruns"):
            ov_ticks += 1
    sb = st["scoreboard"]
    ov = ov_ticks
    outs = sb.get("outcomes", {}) or {}
    # gel freshness at (projected) fire arrival over every serviced stop — the
    # thing late-shift protects; coat-early must not tank it (LEVER C honesty).
    fr = [s.get("gel_fresh_frac") for s in (st.get("serviced") or [])
          if isinstance(s.get("gel_fresh_frac"), (int, float))]
    avg_fresh = round(sum(fr) / len(fr), 3) if fr else None
    min_fresh = round(min(fr), 3) if fr else None
    return dict(
        seed=sc["seed"], map=sc["map"], wind_off=sc["wind_off"],
        dist=sc["dist"], inten=sc["inten"],
        served=int(sb.get("served", 0)),
        violations=int(max_viol),
        overruns=int(ov),
        withdrawn=int(sb.get("withdrawn", 0)),
        relocations=int(sb.get("relocations", 0)),
        min_margin_h=sb.get("min_margin_h"),
        saved=int(outs.get("saved", 0)),
        protected_impacted=int(outs.get("protected_then_impacted", 0)),
        abandoned=int(outs.get("abandoned_unsavable", 0)),
        triaged=int(outs.get("triaged", 0)),
        not_threatened=int(outs.get("not_threatened", 0)),
        threatened=N_CLIENTS - int(outs.get("not_threatened", 0)),
        avg_fresh=avg_fresh, min_fresh=min_fresh,
    )

def _worker(payload):
    sc, overrides, horizon = payload
    return run_scenario(sc, overrides, horizon)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--horizon", type=int, default=HORIZON_TICKS)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--set", action="append", default=[],
                    help="cfg override key=value (int/float/bool)")
    args = ap.parse_args()
    overrides = {}
    for kv in args.set:
        k, v = kv.split("=", 1)
        if v.lower() in ("1", "true", "on"): val = True
        elif v.lower() in ("0", "false", "off"): val = False
        else:
            try: val = int(v)
            except ValueError:
                try: val = float(v)
                except ValueError: val = v
        overrides[k] = val
    scs = scenarios(args.quick)
    t0 = time.time()
    payloads = [(sc, overrides, args.horizon) for sc in scs]
    rows = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for i, r in enumerate(ex.map(_worker, payloads)):
            rows.append(r)
            print(f"[{i+1:>2}/{len(scs)}] {r['seed']:<34} served={r['served']:>2} "
                  f"threat={r['threatened']:>2} viol={r['violations']} ov={r['overruns']}")
    rows.sort(key=lambda r: r["seed"])
    dt = time.time() - t0
    tot_served = sum(r["served"] for r in rows)
    tot_threat = sum(r["threatened"] for r in rows)
    tot_viol = sum(r["violations"] for r in rows)
    tot_ov = sum(r["overruns"] for r in rows)
    summary = dict(
        label=args.label, overrides=overrides, n_scenarios=len(rows),
        horizon=args.horizon, wall_s=round(dt, 1),
        total_served=tot_served, total_threatened=tot_threat,
        total_violations=tot_viol, total_overruns=tot_ov,
        total_abandoned=sum(r["abandoned"] for r in rows),
        total_triaged=sum(r["triaged"] for r in rows),
        rows=rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"bench_{args.label}.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n==== {args.label} ====")
    print(f"scenarios={len(rows)}  wall={dt:.1f}s  "
          f"TOTAL SERVED={tot_served}/{tot_threat} threatened  "
          f"violations={tot_viol}  overruns={tot_ov}  "
          f"(abandoned={summary['total_abandoned']} triaged={summary['total_triaged']})")
    print(f"wrote {out}")

if __name__ == "__main__":
    main()
