"""City weather vs the generic intensity presets: the thirteen one-click
scenarios before / after (founder 2026-10-09, "change the weather conditions
for each city specific"; docs/review/city_weather_2026-10-09.md). NOT product
code.

Builds every server.SCENARIOS key exactly like POST /api/sim/new
{scenario: KEY} (the page's defaults: ember spotting + "Realistic grass
speed" ON, plan on roads, 24 h coating hold), with
cfg.ros_floor_grass_only=True set EXPLICITLY (the integrator is making it the
simulator default in parallel) and the weather PROFILE named on the command
line:

  legacy   no profile key at all (the code before city weather; run on the
           base commit for the "before" column)
  generic  cfg.sim_weather_profile = "generic" (the legacy table, kept)
  city     cfg.sim_weather_profile = "city"    (per-community weather)

then ticks 15-minute steps for HOURS (default 24) and reports, per scenario:
burned acres (the simulated actual fire the page shades) at 1 / 3 / 6 / 12 /
24 h; the first demo home reached (the burned cell under a home, nominal
truth — homes on built-up cells reached only through the ember-radius rule
are not counted, as in scenario_real_tiles_check.py); homes reached by 6 h
and 24 h; and the crew-safety audit with scratch_review/bench_dispatch.py's
conventions — violations = the per-tick MAX of scoreboard.violations,
departure_viol_max = the per-tick max of the departure-audit rows alone,
overrun_ticks = ticks with a SEM-10c crew-overrun row, overrun_rows = the rows
summed over every tick; plus withdrawn, relocations, crews_left_map, served
and the outcome counts at the end. The first 30 hourly weather samples at the
community centre are recorded too (the city profile's shape).

Usage: py -3 scratch_review/city_weather_scenarios.py PROFILE [HOURS] [key,...]
Output: scratch_review/city_weather_scenarios_<PROFILE>.json
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server  # noqa: E402

MPS_TO_MPH = 1.0 / 0.44704
MARKS_H = (1.0, 3.0, 6.0, 12.0, 24.0)


def run(key: str, profile: str, hours: float) -> dict:
    t_wall = time.monotonic()
    cfg = {"ros_floor_grass_only": True}
    if profile != "legacy":
        cfg["sim_weather_profile"] = profile
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    if code != 200:
        return {"key": key, "error": f"{code} {out}"}
    s = mgr._entry(out["session_id"]).session
    fid = next(iter(s._fires))
    f = s._fires[fid]
    spec = s._tile.spec
    nc = spec.ncols
    rcs = [geo.latlon_to_rc(spec, c.loc) for c in s._clients]
    marks, first = {}, None
    reached_at = {}
    max_viol = dep_max = ov_ticks = ov_rows = 0
    n_ticks = int(round(hours * 4))
    st = None
    for k in range(n_ticks):
        st = s.tick()
        now = st["sim_now_s"]
        nom = s._fires[fid].ens.members[0].data
        t_h = (k + 1) * 0.25
        n_reached = sum(1 for rc in rcs if rc is not None
                        and nom[rc[0] * nc + rc[1]] <= now)
        if first is None and n_reached:
            first = t_h
        if t_h in MARKS_H:
            marks[str(t_h)] = round(st["fire_view"]["areas_km2"]["fire"]
                                    * 247.105)
            reached_at[str(t_h)] = n_reached
        sb = st["scoreboard"]
        v = int(sb.get("violations", 0))
        max_viol = max(max_viol, v)
        dep_max = max(dep_max, v - int(sb.get("crew_overruns", 0)))
        rows = st.get("crew_overruns") or []
        if rows:
            ov_ticks += 1
        ov_rows += len(rows)
    sb = st["scoreboard"]
    wx = s._wx.samples
    t0 = f.t_ignite_s
    shape = []
    for smp in wx:
        if smp.t_s < t0 - 1.0:
            continue
        shape.append([round((smp.t_s - t0) / 3600.0, 2),
                      round(smp.wind_speed_mps * MPS_TO_MPH, 1),
                      round(smp.wind_gust_mps * MPS_TO_MPH, 1),
                      round(smp.rh_pct, 1), round(smp.temp_c, 1)])
        if len(shape) >= 30:
            break
    with mgr._lock:
        mgr._sessions.pop(out["session_id"], None)
    return {
        "key": key, "profile": profile,
        "location": s._loc.key, "intensity": f.intensity,
        "ignition": [round(f.origin.lat, 5), round(f.origin.lon, 5)],
        "acres": marks, "first_home_h": first, "homes_reached": reached_at,
        "violations": max_viol, "departure_viol_max": dep_max,
        "overrun_ticks": ov_ticks, "overrun_rows": ov_rows,
        "withdrawn": int(sb.get("withdrawn", 0)),
        "relocations": int(sb.get("relocations", 0)),
        "crews_left_map": int(sb.get("crews_left_map", 0) or 0),
        "served": int(sb.get("served", 0)),
        "outcomes": sb.get("outcomes", {}),
        "weather_label": st.get("weather", {}).get("label")
        if isinstance(st.get("weather"), dict) else None,
        "wx_first_30h": shape,
        "wall_s": round(time.monotonic() - t_wall, 1),
    }


if __name__ == "__main__":
    profile = sys.argv[1]
    if profile not in ("legacy", "generic", "city"):
        raise SystemExit("PROFILE must be legacy | generic | city")
    hours = float(sys.argv[2]) if len(sys.argv) > 2 else 24.0
    keys = (sys.argv[3].split(",") if len(sys.argv) > 3
            else sorted(server.SCENARIOS))
    path = os.path.join(HERE, f"city_weather_scenarios_{profile}.json")
    results = []
    for key in keys:
        r = run(key, profile, hours)
        results.append(r)
        if "error" in r:
            print(f"{key:34s} ERROR {r['error']}", flush=True)
            continue
        a = r["acres"]
        print(f"{key:34s} {r['intensity']:8s} ac 1h {a.get('1.0')!s:>6} "
              f"3h {a.get('3.0')!s:>6} 6h {a.get('6.0')!s:>6} "
              f"24h {a.get('24.0')!s:>7} | home {r['first_home_h']} h | "
              f"viol {r['violations']} ovt {r['overrun_ticks']} "
              f"wd {r['withdrawn']} rel {r['relocations']} "
              f"left {r['crews_left_map']} | {r['wall_s']} s", flush=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
