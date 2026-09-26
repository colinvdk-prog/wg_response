"""Which homes inside the shaded fire still read EXTREME (not IMPACTED) with
the ladder median tied to the fire (ensemble_p50_nominal_bound, simulator
default since 2026-09-26)? Rebuilds the park_city_windstorm demo exactly as
the server does (POST /api/sim/new {scenario}), runs to T+1h45, and for
every home prints its ladder state, its own cell's fuel / nominal arrival,
the earliest nominal arrival within the ember-threat radius, and its ETA
fields. Usage: py -3 scratch_review/impacted_inside_fire_probe.py
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse import geo  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402
from wgresponse.server import SimSessionManager  # noqa: E402

mgr = SimSessionManager()
code, out = mgr.new_session({"scenario": "park_city_windstorm"})
assert code == 200, out
sid = out["session_id"]
entry = mgr._sessions[sid]
s = entry.session
for _ in range(7):
    st = s.tick()
now = st["sim_now_s"]
fire = next(iter(s._fires.values()))
nom = fire.ens.members[0]
spec = nom.spec
R = s._cfg.ember_threat_radius_m
by_id = {c.client_id: c for c in s._clients}
rows = []
for row in st["clients"]:
    p = by_id[row["client_id"]].loc
    rc = geo.latlon_to_rc(spec, p)
    if rc is None:
        continue
    r0, c0 = rc
    own = nom.data[r0 * spec.ncols + c0]
    fuel = int(s._tile.fuel.data[r0 * s._tile.spec.ncols + c0]) if s._tile.spec == spec else None
    k = int(math.ceil(R / spec.cell_m))
    near = math.inf
    for r in range(max(0, r0 - k), min(spec.nrows, r0 + k + 1)):
        for c in range(max(0, c0 - k), min(spec.ncols, c0 + k + 1)):
            q = geo.rc_to_latlon(spec, r, c)
            if geo.haversine_m(p, q) <= R:
                near = min(near, nom.data[r * spec.ncols + c])
    inside = own <= now or near <= now
    if inside:
        rows.append(dict(id=row["client_id"], state=row["state"],
                         outcome=row.get("outcome"),
                         own_burned=own <= now,
                         own_burnable=None if fuel is None else is_burnable(fuel),
                         own_h=None if not math.isfinite(own) else round((own - now) / 3600.0, 2),
                         near_h=None if not math.isfinite(near) else round((near - now) / 3600.0, 2),
                         p50_h=None if row.get("eta_p50_s") is None else round((row["eta_p50_s"] - now) / 3600.0, 2),
                         p10_h=None if row.get("eta_p10_s") is None else round((row["eta_p10_s"] - now) / 3600.0, 2)))
print(f"T+{(now - fire.t_ignite_s) / 3600.0:.2f} h, ember radius {R:.0f} m, "
      f"homes whose cell or ember radius the fire has reached: {len(rows)}")
for r in rows:
    print(json.dumps(r))
