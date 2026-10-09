"""The 13 one-click scenarios on their REAL committed tiles, with and without
a ResponseConfig override (grid-orientation study 2026-10-09; NOT product
code). Same procedure as scratch_review/scenario_real_tiles_check.py: build
each server.SCENARIOS key through SimSessionManager exactly like POST
/api/sim/new {scenario: KEY} (page defaults: spotting + floor ON, city
weather, ros_floor_grass_only per the simulator defaults), tick 15-minute
steps for 6 h, and record burned acres at 1 / 3 / 6 h (the page's fire area),
the first time a demo home is reached by the NOMINAL truth, and the wall time
of every spread solve the session ran (spread.ensemble_arrival is wrapped).

Usage: py -3 scratch_review/grid_orientation_scenarios.py OUT.json
           ['{"cfg_knob": value}'] [key,key,...]
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server, spread  # noqa: E402

out_path = sys.argv[1]
overrides = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
keys = (sys.argv[3].split(",") if len(sys.argv) > 3 and sys.argv[3]
        else sorted(server.SCENARIOS))

_solves = []
_orig = spread.ensemble_arrival


def _timed(*a, **k):
    t = time.perf_counter()
    try:
        return _orig(*a, **k)
    finally:
        _solves.append(time.perf_counter() - t)


spread.ensemble_arrival = _timed

mgr = server.SimSessionManager()
rows = {}
for key in keys:
    t0 = time.monotonic()
    _solves.clear()
    payload = {"scenario": key}
    if overrides:
        payload["cfg"] = dict(overrides)
    code, out = mgr.new_session(payload)
    if code != 200:
        print(f"{key:34s} FAILED {code} {out}", flush=True)
        rows[key] = {"error": f"{code} {out}"}
        continue
    entry = mgr._entry(out["session_id"])
    s = entry.session
    fid = next(iter(s._fires))
    spec = s._tile.spec
    nc = spec.ncols
    rcs = [geo.latlon_to_rc(spec, c.loc) for c in s._clients]
    marks, first = {}, None
    for k in range(24):
        st = s.tick()
        now = st["sim_now_s"]
        nom = s._fires[fid].ens.members[0].data
        t_h = (k + 1) * 0.25
        if first is None and any(rc is not None and nom[rc[0] * nc + rc[1]] <= now
                                 for rc in rcs):
            first = t_h
        if t_h in (1.0, 3.0, 6.0):
            marks[t_h] = round(st["fire_view"]["areas_km2"]["fire"] * 247.105)
    members = len(s._fires[fid].ens.members)
    with mgr._lock:
        mgr._sessions.pop(out["session_id"], None)
    sc = server.SCENARIOS[key]
    rows[key] = {"intensity": sc["intensity"], "acres_1h": marks.get(1.0),
                 "acres_3h": marks.get(3.0), "acres_6h": marks.get(6.0),
                 "first_home_h": first, "cell_m": spec.cell_m,
                 "solves": len(_solves), "members": members,
                 "solve_s_total": round(sum(_solves), 3),
                 "solve_s_max": round(max(_solves), 3) if _solves else None,
                 "wall_s": round(time.monotonic() - t0, 1)}
    r = rows[key]
    print(f"{key:34s} {sc['intensity']:7s} acres 1h {r['acres_1h']:>6} | 3h "
          f"{r['acres_3h']:>6} | 6h {r['acres_6h']:>6} | first home {first} h"
          f" | {r['solves']} solves x {members} members {r['solve_s_total']:.1f} s"
          f" (max {r['solve_s_max']:.2f}) | {r['wall_s']:.0f} s", flush=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"overrides": overrides, "rows": rows}, fh, indent=1)
