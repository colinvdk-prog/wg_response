"""Corridor re-site, solved (founder 2026-10-12: "Move its start"): for every
grass / brush patch start in a wide envelope around the Eagle -> Vail
corridor's canonical point, the nominal fire under the simulator's new
defaults (crown fire, grass-only floor, calibrated embers with a 10-min spot
delay, city weather, the preset 'high' event, wind FROM 240): acres at 1/3/6 h,
acres crowned by 6 h, and the first demo home reached. Nearest first.
NOT product code. Usage: py -3 scratch_review/corridor_resite_solve.py [N]
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, rothermel, server, simui, spread  # noqa: E402
from wgresponse.fuels import is_grass, is_grass_shrub, is_shrub  # noqa: E402

KEY = "eagle_vail_corridor_westerly"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 24
sc = server.SCENARIOS[KEY]
mgr = server.SimSessionManager()
code, out = mgr.new_session({"scenario": KEY})
assert code == 200, out
s = mgr._entry(out["session_id"]).session
assert s._cfg.crown_fire and s._cfg.ros_floor_grass_only and s._cfg.spot_ignition_delay_min == 10.0
spec = s._tile.spec
center = simui.LOCATIONS[sc["location"]].center
canon = geo.destination(center, sc["ign_bearing"], sc["ign_dist_m"])
k = max(1, int(round(simui.FINE_START_PATCH_RADIUS_M / spec.cell_m)))
cell_ac = spec.cell_m ** 2 / 4046.8564224
homes = [geo.latlon_to_rc(spec, c.loc) for c in s._clients]


def fine(code):
    return is_grass(code) or is_grass_shrub(code) or is_shrub(code)


def patch(r0, c0):
    n = f = 0
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            r, c = r0 + dr, c0 + dc
            if dr * dr + dc * dc > k * k or not (0 <= r < spec.nrows and 0 <= c < spec.ncols):
                continue
            n += 1
            f += fine(int(round(s._tile.fuel.get(r, c))))
    return f / n


cands = []
for db in range(-45, 46, 5):
    for s100 in range(30, 151, 5):
        p = geo.destination(center, sc["ign_bearing"] + db, sc["ign_dist_m"] * s100 / 100.0)
        rc = geo.latlon_to_rc(spec, p)
        if rc is None or not fine(int(round(s._tile.fuel.get(*rc)))) or patch(*rc) < 0.5:
            continue
        cands.append((round(geo.haversine_m(p, canon)), db, s100, p))
cands.sort(key=lambda t: t[:3])
print(len(cands), "patch starts; solving the nearest", N, flush=True)
rows = []
for disp, db, s100, p in cands[:N]:
    s.reset()
    fid = s.ignite(p.lat, p.lon, intensity=sc["intensity"], wind_dir_deg=sc["ign_bearing"])
    f = s._fires[fid]
    crown = {}
    t0 = time.monotonic()
    data = spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s, s._cfg,
                                  incident_id=fid, member_k=0, crown_trace=crown).data
    t = f.t_ignite_s
    ac = {h: round(sum(1 for v in data if v <= t + h * 3600.0) * cell_ac) for h in (1, 3, 6)}
    crowned = round(sum(1 for i, kd in crown.items()
                        if kd in (rothermel.CROWN_ACTIVE, rothermel.CROWN_PASSIVE)
                        and data[i] <= t + 6 * 3600.0) * cell_ac)
    first = min((data[r * spec.ncols + c] for r, c in homes if (r, c) is not None), default=float("inf"))
    first_h = None if first == float("inf") else round((first - t) / 3600.0, 2)
    row = {"moved_m": disp, "db": db, "dist_pct": s100, "lat": round(p.lat, 5), "lon": round(p.lon, 5),
           "fuel": int(round(s._tile.fuel.get(*geo.latlon_to_rc(spec, p)))),
           "acres": ac, "crowned_6h": crowned, "first_home_h": first_h,
           "solve_s": round(time.monotonic() - t0, 1)}
    rows.append(row)
    print(json.dumps(row), flush=True)
with open(os.path.join(HERE, "corridor_resite_solve.json"), "w", encoding="utf-8") as fh:
    json.dump(rows, fh, indent=1)
