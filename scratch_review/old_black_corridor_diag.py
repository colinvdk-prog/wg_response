"""scratch_review/old_black_corridor_diag.py — where and why crews leave the map
in the corridor subset (founder ruling 2026-09-26 "crews once leave a map are
no longer part of the simulation"; contracts left_map_out /
escape_prefer_on_map). Re-runs corridor_grid.py scenarios (same config,
seeds and client placement as scratch_review/old_black_ab.py) with the
simulator defaults and, whenever the SEM-10d road search picks a destination
OFF the map, prints it next to the best ON-map alternative (the search re-run
with off-map nodes made unlawful), plus every crew that left.

Usage: py -3 scratch_review/old_black_corridor_diag.py [--arm local2_road]
         [--maps glenwood_springs_co ...] [--limit 6] [--set k=v,...]
Writes scratch_review/old_black/corridor_leave_diag.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONHASHSEED", "0")

from scratch_review import corridor_grid as cg  # noqa: E402
from scratch_review.old_black_trace import _cfg  # noqa: E402
from wgresponse import geo, simui  # noqa: E402


def run(sc, arm, extra, log):
    n_local, n_eagle, fire_zone, ticks, base = cg.ARMS[cg.base_arm(arm)]
    loc = simui.LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(cg.CFG, **extra)
    locs = cg.road_homes(sc["map"], sc["seed"], cg.N_CLIENTS) if arm.endswith("_road") else None
    s = simui.SimSession(sc["map"], cg.N_CLIENTS, n_local, seed=sc["seed"],
                         cfg_overrides=cfg, fire_zone=fire_zone, n_crews_1h=n_eagle,
                         client_locs=locs, place_near_road_m=0.0)
    if not cg._ignite(s, loc, wd, sc["dist"], sc["inten"], fire_zone == "extended"):
        return None
    orig = s._road_escape_search
    tick = {"k": 0}
    picks = []

    def search(pos, now, speed, lead, lawful_only=False, min_p10_s=-math.inf):
        found = orig(pos, now, speed, lead, lawful_only=lawful_only,
                     min_p10_s=min_p10_s)
        if found is None or s._on_map(found["loc"]):
            return found
        key_fn = s._esc_net_key

        def only_on_map(expo, total, dm, lead_s, kind, ident, off_map=False,
                        **kw):
            if off_map or (kind == 1 and not s._on_map(s._road_graph.nodes[ident])):
                return (2, 0, 0, 0, 0, 0, ident)
            return key_fn(expo, total, dm, lead_s, kind, ident, off_map=off_map,
                          **kw)
        s._esc_net_key = only_on_map
        try:
            alt = orig(pos, now, speed, lead, lawful_only=lawful_only,
                       min_p10_s=min_p10_s)
        finally:
            del s._esc_net_key

        def d(f):
            if f is None or f["key"][0] == 2:
                return None
            return {"exposure_s": round(f["exposure_s"], 1),
                    "horizon_h": (None if math.isinf(f["horizon_s"])
                                  else round(f["horizon_s"] / 3600.0, 2)),
                    "drive_min": round(f["total_s"] / 60.0, 1),
                    "key": list(f["key"][:5])}
        picks.append({"tick": tick["k"], "kind": "SEM-10e" if lawful_only else "SEM-10d",
                      "edge_m": round(s._off_tile_m(found["loc"]), 1),
                      "off_map": d(found), "best_on_map": d(alt)})
        return found
    s._road_escape_search = search
    for k in range(1, ticks + 1):
        tick["k"] = k
        st = s.tick()
    out = {"seed": sc["seed"], "served": st["scoreboard"]["served"],
           "left": {cid: rec for cid, rec in sorted(s._left_map.items())},
           "off_map_picks": picks}
    log.append(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="local2_road")
    ap.add_argument("--maps", nargs="*", default=["glenwood_springs_co", "beaver_creek_co", "vail_co"])
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--set", default="")
    a = ap.parse_args()
    log = []
    for m in a.maps:
        n = 0
        for sc in cg.scenarios(m, a.arm):
            r = run(sc, a.arm, _cfg(a.set), log)
            if r is None or not r["left"]:
                continue
            n += 1
            print(f"\n{sc['seed']}: served {r['served']}, left {len(r['left'])}")
            for cid, rec in r["left"].items():
                print(f"   {cid} t+{(rec['at_s'] - 1_790_000_000) / 3600.0:.2f}h "
                      f"{rec['edge_m']:.0f} m past the edge, {rec['zone_m']:.0f} m from a zone")
            for p in r["off_map_picks"][:6]:
                print(f"   t{p['tick']} {p['kind']} off-map pick {p['edge_m']:.0f} m past: "
                      f"{p['off_map']}\n        best on-map: {p['best_on_map']}")
            if n >= a.limit:
                break
    dest = ROOT / "scratch_review" / "old_black" / "corridor_leave_diag.json"
    json.dump(log, open(dest, "w", encoding="utf-8"), indent=1)
    print("\nwrote", dest)


if __name__ == "__main__":
    main()
