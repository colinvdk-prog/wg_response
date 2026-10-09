"""Every one-click scenario on its REAL committed tile (2026-10-09): does the
preset fire light, and does it run at the community? NOT product code.

Builds each server.SCENARIOS key through SimSessionManager exactly like
POST /api/sim/new {scenario: KEY} (page defaults: spotting + floor ON), then
ticks 15-minute steps for 6 h and reports burned acres at 1 h / 3 h / 6 h and
the first time a demo home is reached (nominal truth).
Usage: py -3 scratch_review/scenario_real_tiles_check.py [key,key,...]
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server  # noqa: E402

keys = sys.argv[1].split(",") if len(sys.argv) > 1 else sorted(server.SCENARIOS)
mgr = server.SimSessionManager()
for key in keys:
    t0 = time.monotonic()
    code, out = mgr.new_session({"scenario": key})
    if code != 200:
        print(f"{key:34s} FAILED {code} {out}")
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
        if first is None and any(rc is not None and nom[rc[0] * nc + rc[1]] <= now for rc in rcs):
            first = t_h
        if t_h in (1.0, 3.0, 6.0):
            marks[t_h] = round(st["fire_view"]["areas_km2"]["fire"] * 247.105)
    with mgr._lock:
        mgr._sessions.pop(out["session_id"], None)
    sc = server.SCENARIOS[key]
    print(f"{key:34s} {sc['intensity']:7s} acres 1h {marks.get(1.0):>6} | 3h {marks.get(3.0):>6} | "
          f"6h {marks.get(6.0):>6} | first home {first} h | tile {spec.cell_m:.0f} m | "
          f"{time.monotonic() - t0:.0f} s", flush=True)
