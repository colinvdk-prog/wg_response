"""WUI interface-geometry capacity benchmark (founder 2026-07-12).

Question: how many homes can be protected when the WUI is a LINE of homes
PERPENDICULAR to the approaching fire wall — the worst geometry, because the
front reaches every home in the line within a narrow window (deadlines do not
stagger), vs a depth column (parallel to spread — staggered arrivals) vs the
default scattered WUI placement.

Placements are exact (SimSession client_locs seam); everything else is the
SHIPPED sim config (plan-on-roads, 24h coating hold, 3-person/15-min crews,
spotting + grass realism). Scenario catalog:
  head_on      interface line perp-to-spread, reactive ignition (6 km)
  head_on_x    same, extreme intensity
  flank_column line parallel-spread (staggered deadlines), reactive
  scattered    seeded WUI placement, reactive (reference)
  second_fire  scattered + second ignition from +60° at T+2h
  alerted      interface line on the EXTENDED tile, ignition ~20 km (hold regime)
Capacity sweep: head_on homes-per-crew at N=10..60, 2 crews.

Usage: py -3 scratch_review/bench_interface.py [--quick]"""
import os, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS, load_tile, wui_cells
from wgresponse.contracts import LatLon

CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10, "deploy_min": 15.0,
       "dispatch_plan_on_roads": True, "dispatch_hold_h": 24.0}


def line_locs(center, bearing_deg, n, spacing_m=100.0, key=None,
              fire_zone="standard", snap_m=400.0):
    """n points on a line through `center` along bearing (centered), each
    SNAPPED to the nearest WUI-band cell within snap_m (real interface homes
    sit on the developed edge with road access — raw brush points measure
    access failure, not geometry). Unsnappable points keep the raw coordinate."""
    raw = []
    half = (n - 1) / 2.0
    for i in range(n):
        d = (i - half) * spacing_m
        b = bearing_deg if d >= 0 else (bearing_deg + 180.0) % 360.0
        raw.append(geo.destination(center, b, abs(d)))
    if key is None:
        return raw
    tile, _syn = load_tile(key, cell_m=90.0)
    band = [geo.rc_to_latlon(tile.spec, r, c) for (r, c) in wui_cells(tile)]
    out = []
    seen = set()
    for p in raw:
        best, bd = None, snap_m
        for q in band:
            dd = geo.haversine_m(p, q)
            if dd < bd and (round(q.lat, 6), round(q.lon, 6)) not in seen:
                best, bd = q, dd
        if best is not None:
            seen.add((round(best.lat, 6), round(best.lon, 6)))
            out.append(best)
        else:
            out.append(p)
    return out


def ignite_upwind(s, loc, wd, dist_m, intensity):
    err = None
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6):
            p = geo.destination(loc.center, wd + db, dist_m * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity=intensity, wind_dir_deg=wd)
                return None
            except ValueError as e:
                err = str(e)
    return err


def run(key, placement, n, crews, dist_m, intensity, ticks,
        fire_zone="standard", second_fire=False, seed="iface"):
    loc = LOCATIONS[key]
    wd = loc.hazard_wind_dir_deg
    locs = None
    if placement == "line":            # perp to spread: bearing wd+90
        locs = line_locs(loc.center, (wd + 90.0) % 360.0, n, key=key,
                         fire_zone=fire_zone)
    elif placement == "column":        # parallel to spread: bearing wd
        locs = line_locs(loc.center, wd, n, key=key, fire_zone=fire_zone)
    s = simui.SimSession(key, n, crews, seed=f"{seed}|{key}|{placement}",
                         cfg_overrides=dict(CFG), fire_zone=fire_zone,
                         client_locs=locs)
    err = ignite_upwind(s, loc, wd, dist_m, intensity)
    if err:
        return dict(error=err)
    max_viol = 0
    ov = 0
    st = None
    for t in range(ticks):
        if second_fire and t == 8:      # T+2h at 15-min ticks
            p2 = geo.destination(loc.center, (wd + 60.0) % 360.0, dist_m * 0.8)
            try:
                s.ignite(p2.lat, p2.lon, intensity=intensity)
            except ValueError:
                pass
        st = s.tick()
        v = st["scoreboard"].get("violations", 0)
        max_viol = max(max_viol, v)
        if st.get("crew_overruns"):
            ov += 1
    sb = st["scoreboard"]
    outs = sb.get("outcomes", {}) or {}
    threatened = n - int(outs.get("not_threatened", 0))
    lead = None
    etas = [c.get("eta_p10_s") for c in st["clients"]
            if isinstance(c.get("eta_p10_s"), (int, float)) and c["eta_p10_s"] < 1e17]
    reasons = {}
    for u in st.get("unserved", []):
        reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
    return dict(served=int(sb.get("served", 0)), threatened=threatened,
                viol=max_viol, ov=ov,
                withdrawn=int(sb.get("withdrawn", 0)), reasons=reasons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    key = "paradise_ca"                 # Camp-Fire geography, hardest map
    rows = []

    print("=== SCENARIO CATALOG (paradise_ca, 40 homes, shipped config) ===")
    catalog = [
        ("head_on (line perp-to-spread, high, 6km)",  "line",    "high",    6000.0, 60, "standard", False),
        ("head_on_extreme (line perp, extreme)",   "line",    "extreme", 6000.0, 60, "standard", False),
        ("flank_column (line parallel-spread, high)",  "column",  "high",    6000.0, 60, "standard", False),
        ("scattered (seeded WUI, high)",        "default", "high",    6000.0, 60, "standard", False),
        ("second_fire (+60° at T+2h, high)",    "default", "high",    6000.0, 60, "standard", True),
        ("alerted (line perp, extended 20km)",     "line",    "high",   20000.0, 240, "extended", False),
    ]
    for label, pl, inten, dist, ticks, fz, sf in catalog:
        if args.quick and ticks > 60:
            continue
        for crews in (2, 4):
            r = run(key, pl, 40, crews, dist, inten, ticks, fire_zone=fz,
                    second_fire=sf)
            if "error" in r:
                print(f"  {label:<38} crews={crews}: ERROR {r['error'][:60]}")
                continue
            pct = 100.0 * r["served"] / r["threatened"] if r["threatened"] else 0.0
            print(f"  {label:<38} crews={crews}: {r['served']:>2}/{r['threatened']:<2} "
                  f"({pct:5.1f}%)  viol={r['viol']} ov={r['ov']} wd={r['withdrawn']}  {r['reasons']}")
            rows.append(dict(scenario=label, crews=crews, **r))

    print("\n=== PERPENDICULAR-INTERFACE CAPACITY (head_on, 2 crews, high, 6km) ===")
    for n in (10, 20, 30, 40, 60):
        r = run(key, "line", n, 2, 6000.0, "high", 60, seed=f"cap{n}")
        if "error" in r:
            print(f"  N={n}: ERROR")
            continue
        pct = 100.0 * r["served"] / r["threatened"] if r["threatened"] else 0.0
        print(f"  N={n:<3} -> served {r['served']:>2}/{r['threatened']:<3} ({pct:5.1f}%)  "
              f"per-crew={r['served'] / 2.0:.1f}  viol={r['viol']} ov={r['ov']}  {r['reasons']}")
        rows.append(dict(scenario=f"capacity_N{n}", crews=2, **r))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "bench_interface.json")
    json.dump(rows, open(out, "w"), indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
