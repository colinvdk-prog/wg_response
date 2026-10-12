"""Option 2 measurement (2026-10-12): the seven re-sited one-click fires on
their REAL tiles. Each scenario x {fine-fuel start, old first-burnable start} x
{timber floor (option 1, the simulator default), grass-only floor}: burned
acres at 1 / 3 / 6 h and the first demo home reached (nominal truth), under
the simulator defaults otherwise (city weather, calibrated embers). NOT product
code. Usage: py -3 scratch_review/timber_start_resite_probe.py OUT.jsonl
[--workers N] | --table OUT.jsonl
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SEVEN = ["park_city_windstorm", "vail_westerly", "beaver_creek_westerly",
         "glenwood_springs_westerly", "basalt_carbondale_southwesterly",
         "aspen_southwesterly", "eagle_vail_corridor_westerly"]


def run(job):
    from wgresponse import geo, server, simui
    from wgresponse.fuels import (is_grass, is_grass_shrub, is_shrub,
                                  is_timber_litter, is_timber_understory)
    key, start, floor = job
    t0 = time.monotonic()
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key,
                                 "ign_fine_fuel": start == "fine",
                                 "cfg": {"ros_floor_grass_only": floor == "grass"}})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    fid = "sim-f1"
    o = s._fires[fid].origin
    spec = s._tile.spec
    fc = int(round(s._tile.fuel.get(*geo.latlon_to_rc(spec, o))))
    cls = ("GR" if is_grass(fc) else "GS" if is_grass_shrub(fc) else
           "SH" if is_shrub(fc) else "TU" if is_timber_understory(fc) else
           "TL" if is_timber_litter(fc) else str(fc))
    sc = server.SCENARIOS[key]
    center = simui.LOCATIONS[sc["location"]].center
    canon = geo.destination(center, sc["ign_bearing"], sc["ign_dist_m"])
    nc = spec.ncols
    rcs = [geo.latlon_to_rc(spec, c.loc) for c in s._clients]
    marks, first, step1 = {}, None, None
    for k in range(24):
        t1 = time.monotonic()
        st = s.tick()
        if k == 0:
            step1 = round(time.monotonic() - t1, 1)
        now = st["sim_now_s"]
        nom = s._fires[fid].ens.members[0].data
        t_h = (k + 1) * 0.25
        if first is None and any(rc is not None and nom[rc[0] * nc + rc[1]] <= now
                                 for rc in rcs):
            first = t_h
        if t_h in (1.0, 3.0, 6.0):
            marks[str(t_h)] = round(st["fire_view"]["areas_km2"]["fire"] * 247.105)
    return {"key": key, "start": start, "floor": floor, "fuel": f"{cls}({fc})",
            "moved_m": round(geo.haversine_m(o, canon)),
            "from_centre_km": round(geo.haversine_m(o, center) / 1000.0, 2),
            "acres": marks, "first_home_h": first, "step1_s": step1,
            "secs": round(time.monotonic() - t0, 1)}


def table(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    by = {(r["key"], r["start"], r["floor"]): r for r in rows}
    print("| scenario | start | fuel | moved | floor | 1 h | 3 h | 6 h | first home |")
    print("|---|---|---|---|---|---|---|---|---|")
    for key in SEVEN:
        for start in ("legacy", "fine"):
            for floor in ("timber", "grass"):
                r = by.get((key, start, floor))
                if r is None:
                    continue
                a = r["acres"]
                fh = "-" if r["first_home_h"] is None else f"{r['first_home_h']:.2f} h"
                print(f"| {key} | {start} | {r['fuel']} | {r['moved_m']} m | {floor} | "
                      f"{a.get('1.0'):,} | {a.get('3.0'):,} | {a.get('6.0'):,} | {fh} |")


if __name__ == "__main__":
    if sys.argv[1] == "--table":
        table(sys.argv[2])
        sys.exit(0)
    out = sys.argv[1]
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 2
    done = set()
    if os.path.exists(out):
        for l in open(out, encoding="utf-8"):
            r = json.loads(l)
            done.add((r["key"], r["start"], r["floor"]))
    jobs = [(k, s, f) for k in SEVEN for s in ("fine", "legacy") for f in ("timber", "grass")
            if (k, s, f) not in done]
    print(len(jobs), "runs ->", out, flush=True)
    import multiprocessing as mp
    with mp.Pool(workers) as pool, open(out, "a", encoding="utf-8") as fh:
        for r in pool.imap_unordered(run, jobs):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(json.dumps(r, sort_keys=True), flush=True)
