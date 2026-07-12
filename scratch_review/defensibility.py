"""Defensibility-based home-siting analysis (founder 2026-07-11).

Flips the question from 'given homes, save as many as possible' to 'which home
SITES are defensible, so a planner only permits building there'. For a tile it
lays a candidate-parcel grid over the central buildable area, runs an ENSEMBLE of
fire scenarios (ignitions on a ring around the community × the hazard winds =
'potential fire perimeters'), and scores each site:

  survives(site, scenario) = fire never reaches it  OR  a 3-person/15-min crew
      can reach + coat + clear >=30 min before the fire (time-budget feasibility;
      OPTIMISTIC — ignores ingress/egress CORRIDOR crossing, so real is lower).
  defensibility(site) = mean survives over the scenario ensemble.

Classes: ALLOW (>=0.80), CONDITIONAL (0.50-0.80, needs hardening/defensible
space), AVOID (<0.50, burns too often/fast). Also emits the crew-capacity
spacing rule (max homes per fire corridor) and writes a per-site map JSON."""
import os, sys, json, math, time, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import spread, geo
from wgresponse.contracts import ResponseConfig, LatLon
from wgresponse.simui import (LOCATIONS, load_tile, INTENSITY_WEATHER, MPH_TO_MPS,
                              GUST_FACTOR, DEVELOPED_CODES)
from wgresponse.weather import MockWeather
from wgresponse.sim import _eff_ground_speed_mps

T0 = 1_700_000_000.0            # fixed epoch (determinism)
DEPLOY_MIN = 15.0              # 3-person crew (one member preps next site)
EGRESS_MIN = 30.0             # crew clears >=30 min before fire (founder law)
FALLBACK_M = 200.0            # home footprint + immediate ember exposure radius
IGNITION_RING_M = 7000.0     # fires ignite ~7 km out and run toward the community
BEARINGS = list(range(0, 360, 30))    # 12 potential fire origins around the ring
CANDIDATE_RADIUS_M = 5500.0  # central buildable window
CANDIDATE_STEP_M = 450.0     # candidate parcel spacing

def build_wx(loc, dir_deg, intensity, cfg):
    spec = INTENSITY_WEATHER[intensity]
    mock = MockWeather(seed=f"defens|{loc.key}|{dir_deg}|{intensity}",
                       base_wind_mps=spec["wind_mph"] * MPH_TO_MPS, dir_deg=dir_deg,
                       rh_pct=spec["rh_pct"], temp_c=spec["temp_c"],
                       gust_factor=GUST_FACTOR, t0_s=T0)
    return mock.forecast(loc.center, cfg.horizon_h)

def candidate_sites(tile, center):
    """Grid of candidate parcels within CANDIDATE_RADIUS_M of center, excluding
    water (98). Returns [(lat, lon, fuel_code, dev_bool)]."""
    spec = tile.spec
    out = []
    steps = int(CANDIDATE_RADIUS_M / CANDIDATE_STEP_M)
    for iy in range(-steps, steps + 1):
        for ix in range(-steps, steps + 1):
            dx, dy = ix * CANDIDATE_STEP_M, iy * CANDIDATE_STEP_M
            if math.hypot(dx, dy) > CANDIDATE_RADIUS_M:
                continue
            # offset east(dx)/north(dy) from center
            p = geo.destination(geo.destination(center, 90.0, dx), 0.0, dy)
            rc = geo.latlon_to_rc(spec, p)
            if rc is None:
                continue
            fuel = int(tile.fuel.get(*rc))
            if fuel == 98:                      # water — not buildable
                continue
            out.append((round(p.lat, 6), round(p.lon, 6), fuel,
                        fuel in DEVELOPED_CODES))
    return out

def analyze(key, intensities=("high",), ensemble_n=6, grid_cell_m=180.0):
    loc = LOCATIONS[key]
    cfg = ResponseConfig(ensemble_n=ensemble_n, grid_cell_m=grid_cell_m,
                         spotting_enabled=True, grass_ros_floor_frac=0.10,
                         deploy_min=DEPLOY_MIN, ember_threat_radius_m=FALLBACK_M)
    tile, synth = load_tile(key, cell_m=90.0)
    speed = _eff_ground_speed_mps(cfg)
    sites = candidate_sites(tile, loc.center)
    sz = [z.loc for z in loc.safe_zones]
    def travel_in_s(p):
        d = min(geo.haversine_m(p, z) for z in sz)
        return d / speed
    # precompute per-site nearest-safe-zone travel + the savable lead threshold
    site_ll = [LatLon(la, lo) for (la, lo, _f, _d) in sites]
    need_lead = [travel_in_s(p) + (DEPLOY_MIN + EGRESS_MIN) * 60.0 for p in site_ll]
    n = len(sites)
    survive = [0] * n; threat = [0] * n; leads = [[] for _ in range(n)]
    scenarios = 0
    t0 = time.time()
    for intensity in intensities:
        for b in BEARINGS:
            ign = geo.destination(loc.center, b, IGNITION_RING_M)   # upwind origin
            wx = build_wx(loc, b, intensity, cfg)                   # wind FROM b
            ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                          incident_id=f"{key}-{intensity}-{b}")
            scenarios += 1
            for i, p in enumerate(site_ll):
                eta = spread.eta_at(ens, p, FALLBACK_M)
                a = eta.eta_p10_s
                if not math.isfinite(a):
                    survive[i] += 1                # fire never reaches -> survives
                    continue
                threat[i] += 1
                lead = a - T0
                leads[i].append(lead)
                if lead >= need_lead[i]:
                    survive[i] += 1               # crew-savable in the time budget
    rows = []
    for i, (la, lo, fuel, dev) in enumerate(sites):
        defens = survive[i] / scenarios
        expo = threat[i] / scenarios
        med_lead = (sorted(leads[i])[len(leads[i]) // 2] / 3600.0) if leads[i] else None
        cls = "allow" if defens >= 0.80 else ("conditional" if defens >= 0.50 else "avoid")
        rows.append(dict(lat=la, lon=lo, fuel=fuel, developed=dev,
                         defensibility=round(defens, 3), exposure=round(expo, 3),
                         med_lead_h=(round(med_lead, 2) if med_lead is not None else None),
                         cls=cls))
    # crew-capacity spacing rule: homes a crew can coat in one event's window
    # window ~ median lead across threatened sites; per-home time = 15 min +
    # travel between neighbouring parcels (CANDIDATE_STEP_M apart).
    all_leads = [l for L in leads for l in L]
    med_window_h = (sorted(all_leads)[len(all_leads) // 2] / 3600.0) if all_leads else 0.0
    per_home_min = DEPLOY_MIN + (CANDIDATE_STEP_M / speed) / 60.0
    homes_per_crew = max(1, int((med_window_h * 60.0) / per_home_min))
    summary = dict(
        location=key, name=loc.name, synthetic=synth, scenarios=scenarios,
        n_sites=n, wall_s=round(time.time() - t0, 1),
        allow=sum(r["cls"] == "allow" for r in rows),
        conditional=sum(r["cls"] == "conditional" for r in rows),
        avoid=sum(r["cls"] == "avoid" for r in rows),
        pct_allow=round(100 * sum(r["cls"] == "allow" for r in rows) / n, 1),
        pct_avoid=round(100 * sum(r["cls"] == "avoid" for r in rows) / n, 1),
        med_window_h=round(med_window_h, 2),
        homes_per_crew_per_event=homes_per_crew,
        center=dict(lat=loc.center.lat, lon=loc.center.lon),
        rows=rows)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"defens_{key}.json")
    json.dump(summary, open(out, "w"), indent=2)
    print(f"{key:<15} sites={n} scen={scenarios} "
          f"ALLOW={summary['pct_allow']}% CONDITIONAL={round(100*summary['conditional']/n,1)}% "
          f"AVOID={summary['pct_avoid']}%  homes/crew/event~{homes_per_crew}  ({summary['wall_s']}s)")
    return summary

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", nargs="*", default=["paradise_ca", "boulder_co"])
    ap.add_argument("--intensities", nargs="*", default=["high"])
    ap.add_argument("--ensemble", type=int, default=6)
    args = ap.parse_args()
    for k in args.maps:
        analyze(k, intensities=tuple(args.intensities), ensemble_n=args.ensemble)
