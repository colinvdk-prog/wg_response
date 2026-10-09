"""Park City one-click scenario: how fast does the simulated fire spread, and
why? (founder 2026-10-09: "I just ran a simulation in park city and the fire
seemed to spread far too quickly. confirm this was correct"). NOT product code.

Replays server.SCENARIOS["park_city_windstorm"] on the committed real tile with
the simulator page's default physics (ember spotting ON, fuel-class ROS floor
0.10 = "Realistic grass speed"), the server's seed and its ignition search, and
measures the simulated ACTUAL fire (the nominal member, what the page shades)
every 15-minute tick for 6 h. Variants change one factor each:

  as_deployed       the page's defaults (what the founder saw)
  floor_grass_only  cfg.ros_floor_grass_only=True (the floor stays on grass and
                    grass-shrub only; timber understory/litter not floored)
  floor_off         "Realistic grass speed" unticked (no floor at all)
  spotting_off      "Ember spotting" unticked
  high              intensity "high" instead of "extreme"

Output: scratch_review/park_city_speed_probe.json + a printed table.
Usage: py -3 scratch_review/park_city_speed_probe.py [ticks]
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo  # noqa: E402
from wgresponse.fuels import is_grass, is_grass_shrub  # noqa: E402
from wgresponse.simui import LOCATIONS, SimSession  # noqa: E402

SCEN = {"location": "park_city_ut", "clients": 40, "crews": 2,
        "intensity": "extreme", "ign_bearing": 225.0, "ign_dist_m": 7000.0}
SEED = "scenario-park_city_windstorm"
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 24


def fuel_class(code: int) -> str:
    if is_grass(code):
        return "grass (GR)"
    if is_grass_shrub(code):
        return "grass-shrub (GS)"
    if 141 <= code <= 149:
        return "shrub (SH)"
    if 161 <= code <= 165:
        return "timber understory (TU)"
    if 181 <= code <= 189:
        return "timber litter (TL)"
    if 201 <= code <= 204:
        return "slash (SB)"
    return "non-burnable / other"


def build(variant: str):
    overrides = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}
    intensity = SCEN["intensity"]
    if variant == "floor_grass_only":
        overrides["ros_floor_grass_only"] = True
    elif variant == "floor_off":
        overrides["grass_ros_floor_frac"] = 0.0
    elif variant == "spotting_off":
        overrides["spotting_enabled"] = False
    elif variant == "high":
        intensity = "high"
    s = SimSession(SCEN["location"], SCEN["clients"], SCEN["crews"], seed=SEED,
                   cfg_overrides=overrides)
    center = LOCATIONS[SCEN["location"]].center
    for db in (0, 10, -10, 20, -20, 30, -30):          # the server's search
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, SCEN["ign_bearing"] + db,
                                SCEN["ign_dist_m"] * shrink)
            try:
                fid = s.ignite(p.lat, p.lon, intensity=intensity,
                               wind_dir_deg=SCEN["ign_bearing"])
                return s, fid, p, intensity
            except ValueError:
                continue
    raise SystemExit("no burnable ignition cell")


def run(variant: str) -> dict:
    t_wall = time.monotonic()
    s, fid, ign, intensity = build(variant)
    tile = s._tile
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    # cell-centre distances from the ignition point (km), computed once
    dist_km = [0.0] * (nr * nc)
    for r in range(nr):
        for c in range(nc):
            p = geo.rc_to_latlon(spec, r, c)
            dist_km[r * nc + c] = geo.haversine_m(ign, p) / 1000.0
    fuel = [f.code for f in tile.fuel_models()] if hasattr(tile, "fuel_models") else None
    if fuel is None:
        fuel = list(getattr(tile, "fuel_codes", None) or getattr(tile, "fuel").data)
    clients = [(c.client_id, c.loc) for c in s._clients] if hasattr(s, "_clients") else []
    wx0 = s._wx.samples[0]
    rows = []
    first_home_h = None
    t_ign = None
    comp_at = {}
    for k in range(TICKS):
        st = s.tick()
        now = st["sim_now_s"]
        if t_ign is None:
            t_ign = s._fires[fid].t0_s if hasattr(s._fires[fid], "t0_s") else (now - 900.0 * (k + 1))
        nom = s._fires[fid].ens.members[0].data
        burned = [i for i, v in enumerate(nom) if v <= now]
        area = st["fire_view"]["areas_km2"]["fire"]
        head_km = max((dist_km[i] for i in burned), default=0.0)
        homes = 0
        for _cid, loc in clients:
            rc = geo.latlon_to_rc(spec, loc)
            if rc is None:
                continue
            v = nom[rc[0] * nc + rc[1]]
            if v <= now:
                homes += 1
        t_h = (k + 1) * 0.25
        if homes and first_home_h is None:
            first_home_h = t_h
        rows.append({"t_h": t_h, "area_km2": round(area, 3),
                     "area_acres": round(area * 247.105), "head_km": round(head_km, 2),
                     "homes_reached": homes})
        if t_h in (1.75, 3.0, 6.0):
            comp = {}
            for i in burned:
                cls = fuel_class(int(fuel[i]))
                comp[cls] = comp.get(cls, 0) + 1
            n = max(1, len(burned))
            comp_at[str(t_h)] = {k2: round(v2 / n, 3) for k2, v2 in sorted(comp.items())}
    head_speeds = []
    prev = (0.0, 0.0)
    for r in rows:
        dt = r["t_h"] - prev[0]
        head_speeds.append(round((r["head_km"] - prev[1]) / dt, 2) if dt > 0 else None)
        prev = (r["t_h"], r["head_km"])
    return {"variant": variant, "intensity": intensity,
            "ignition": [round(ign.lat, 5), round(ign.lon, 5)],
            "weather": {"wind_mps": round(wx0.wind_speed_mps, 2),
                        "gust_mps": round(wx0.wind_gust_mps, 2),
                        "dir_from_deg": wx0.wind_dir_deg, "rh_pct": wx0.rh_pct,
                        "temp_c": wx0.temp_c},
            "cell_m": spec.cell_m, "rows": rows, "head_kmh_per_tick": head_speeds,
            "first_home_h": first_home_h, "fuel_share_burned": comp_at,
            "wall_s": round(time.monotonic() - t_wall, 1)}


if __name__ == "__main__":
    variants = sys.argv[2].split(",") if len(sys.argv) > 2 else [
        "as_deployed", "floor_grass_only", "floor_off", "spotting_off", "high"]
    out = []
    for v in variants:
        res = run(v)
        out.append(res)
        last = res["rows"][-1]
        r175 = next((r for r in res["rows"] if r["t_h"] == 1.75), None)
        print(f"{v:18s} {res['intensity']:8s} T+1h45 {r175['area_acres'] if r175 else '-':>7} ac "
              f"| T+{last['t_h']:.2f}h {last['area_acres']:>7} ac head {last['head_km']:5.1f} km "
              f"| first home {res['first_home_h']} h | wall {res['wall_s']} s", flush=True)
        with open(os.path.join(HERE, "park_city_speed_probe.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
