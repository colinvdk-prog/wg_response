"""wg_response onboarding & marketing tool (founder 2026-07-12).

Grades candidate homes by how well the CURRENT fleet can actually protect
them — the acceptance filter for onboarding and the targeting map for
marketing. Runs the REAL engine (shipped sim config: plan-on-roads, 24h
coating hold, 3-person/15-min crews, spotting + grass) over a deterministic
ensemble of fire bearings and reports, per candidate:

  reactive%   share of REACTIVE scenarios (fire detected ~6 km out, high
              intensity, one ignition per compass bearing) in which the home
              is coated in time by the fleet
  alerted     coated when the fire is detected far out (extended tile,
              ~20 km — the WATCH/hold regime the alert ladder provides)
  grade       A reactive >= 75% and alerted
              B reactive >= 50% and alerted
              C reactive <  50% but alerted  (sell WITH detection + ladder)
              D not even alerted-coverable   (decline or require hardening)
  fleet Δ     homes the EXISTING portfolio loses across the ensemble when
              the candidates join (capacity impact; 0 = capacity-safe)

Usage:
  py -3 tools/onboard.py --location paradise_ca \
      --candidates "39.760,-121.610;39.755,-121.595" \
      [--portfolio "39.770,-121.600;..."] [--crews 2] [--bearings 6]

Interpretation for marketing (see docs/HOA_40_PLAYBOOK.md):
  - Grade C is NOT a rejection — it is the normal state of perpendicular-
    interface homes (reactive head-on capacity is ~1-2 homes/crew regardless
    of fleet size; the SAME homes are 100% coverable in the alerted regime).
    Sell the detection + alert-ladder + hold/preposition package, not crews.
  - A nonzero fleet Δ means the candidates dilute existing protection: add a
    crew, or cap enrollment (density law: ~<=5 homes/crew reactive)."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import geo, simui                      # noqa: E402
from wgresponse.contracts import LatLon                # noqa: E402
from wgresponse.simui import LOCATIONS                 # noqa: E402

CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
       "deploy_min": 15.0, "dispatch_plan_on_roads": True,
       "dispatch_hold_h": 24.0}
REACTIVE_KM = 6.0
ALERTED_KM = 20.0


def parse_locs(s):
    out = []
    for tok in (s or "").split(";"):
        tok = tok.strip()
        if not tok:
            continue
        lat, lon = tok.split(",")
        out.append(LatLon(float(lat), float(lon)))
    return out


def ignite_from(sess, loc, bearing, dist_m, intensity):
    for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
        p = geo.destination(loc.center, bearing, dist_m * shrink)
        try:
            sess.ignite(p.lat, p.lon, intensity=intensity,
                        wind_dir_deg=bearing)
            return True
        except ValueError:
            continue
    return False


def run_scenario(key, locs, crews, bearing, dist_m, ticks, fire_zone,
                 seed):
    loc = LOCATIONS[key]
    s = simui.SimSession(key, len(locs), crews, seed=seed,
                         cfg_overrides=dict(CFG), fire_zone=fire_zone,
                         client_locs=locs)
    if not ignite_from(s, loc, bearing, dist_m, "high"):
        return None
    st = None
    for _ in range(ticks):
        st = s.tick()
    served = {x["client_id"] for x in st.get("serviced", [])}
    viol = st["scoreboard"].get("violations", 0)
    return served, viol


def evaluate(key, portfolio, candidates, crews, bearings):
    loc = LOCATIONS[key]
    all_locs = portfolio + candidates
    ids = [f"sim-c{k}" for k in range(1, len(all_locs) + 1)]
    port_ids = set(ids[:len(portfolio)])
    cand_ids = ids[len(portfolio):]
    step = 360 // bearings
    hits = {i: 0 for i in ids}
    runs = 0
    port_only_served = {i: 0 for i in ids[:len(portfolio)]}
    for b in range(0, 360, step):
        r = run_scenario(key, all_locs, crews, float(b), REACTIVE_KM * 1000,
                         60, "standard", seed=f"onboard|{key}|{b}")
        if r is None:
            continue
        served, _v = r
        runs += 1
        for i in ids:
            if i in served:
                hits[i] += 1
        if portfolio:
            rp = run_scenario(key, portfolio, crews, float(b),
                              REACTIVE_KM * 1000, 60, "standard",
                              seed=f"onboard|{key}|{b}|port")
            if rp is not None:
                for k2, i in enumerate(ids[:len(portfolio)]):
                    if f"sim-c{k2 + 1}" in rp[0]:
                        port_only_served[i] += 1
    # alerted check: hazard-wind bearing, far ignition, extended tile
    alerted_served = set()
    ra = run_scenario(key, all_locs, crews, loc.hazard_wind_dir_deg,
                      ALERTED_KM * 1000, 240, "extended",
                      seed=f"onboard|{key}|alerted")
    if ra is not None:
        alerted_served = ra[0]
    report = []
    for k, i in enumerate(ids):
        reactive = 100.0 * hits[i] / runs if runs else 0.0
        al = i in alerted_served
        grade = ("A" if reactive >= 75.0 and al else
                 "B" if reactive >= 50.0 and al else
                 "C" if al else "D")
        p = all_locs[k]
        report.append(dict(
            id=i, lat=round(p.lat, 6), lon=round(p.lon, 6),
            kind=("portfolio" if i in port_ids else "candidate"),
            reactive_pct=round(reactive, 1), alerted=al, grade=grade))
    fleet_delta = 0
    if portfolio and runs:
        with_c = sum(hits[i] for i in port_ids)
        without_c = sum(port_only_served[i] for i in port_ids)
        fleet_delta = without_c - with_c        # >0 = candidates cost coverage
    return report, runs, fleet_delta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--location", required=True, choices=sorted(LOCATIONS))
    ap.add_argument("--candidates", required=True,
                    help='"lat,lon;lat,lon;..."')
    ap.add_argument("--portfolio", default="",
                    help='existing enrolled homes "lat,lon;..."')
    ap.add_argument("--crews", type=int, default=2)
    ap.add_argument("--bearings", type=int, default=6,
                    help="reactive fire bearings (6=every 60 deg)")
    ap.add_argument("--json", help="also write the report here")
    args = ap.parse_args()
    candidates = parse_locs(args.candidates)
    portfolio = parse_locs(args.portfolio)
    if not candidates:
        ap.error("no candidates parsed")
    report, runs, fleet_delta = evaluate(
        args.location, portfolio, candidates, args.crews, args.bearings)
    print(f"\nONBOARDING REPORT — {args.location}, {args.crews} crews, "
          f"{runs} reactive bearings + 1 alerted scenario")
    print(f"{'id':<9} {'kind':<10} {'lat':>10} {'lon':>11} "
          f"{'reactive%':>9} {'alerted':>8} {'grade':>6}")
    for r in report:
        print(f"{r['id']:<9} {r['kind']:<10} {r['lat']:>10.5f} "
              f"{r['lon']:>11.5f} {r['reactive_pct']:>9.1f} "
              f"{str(r['alerted']):>8} {r['grade']:>6}")
    cand = [r for r in report if r["kind"] == "candidate"]
    a = sum(1 for r in cand if r["grade"] == "A")
    b = sum(1 for r in cand if r["grade"] == "B")
    c = sum(1 for r in cand if r["grade"] == "C")
    d = sum(1 for r in cand if r["grade"] == "D")
    print(f"\ncandidates: A={a} B={b} C={c} D={d}")
    if fleet_delta > 0:
        print(f"FLEET IMPACT: existing portfolio loses {fleet_delta} "
              f"home-scenarios with these candidates added — add a crew or "
              f"cap enrollment (density law ~<=5 homes/crew reactive).")
    elif portfolio:
        print("FLEET IMPACT: capacity-safe (existing portfolio coverage "
              "unchanged).")
    print("\nGrades: A/B = strong reactive + alerted cover. C = alerted-only "
          "(NORMAL for interface homes — sell detection + alert ladder; the "
          "hold/preposition regime covers them 100% given >=12-24h lead). "
          "D = not coverable even alerted: decline, require hardening, or "
          "add access/crews.")
    if args.json:
        json.dump(dict(location=args.location, crews=args.crews, runs=runs,
                       fleet_delta=fleet_delta, report=report),
                  open(args.json, "w"), indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
