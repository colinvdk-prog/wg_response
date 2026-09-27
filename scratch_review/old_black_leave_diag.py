"""scratch_review/old_black_leave_diag.py — why does a crew leave the map?
(founder ruling 2026-09-26 "crews once leave a map are no longer part of the
simulation"; contracts left_map_out / escape_prefer_on_map.)

Runs one offroad-probe session with the simulator defaults and, every time the
SEM-10d road-network search picks a destination OFF the map, re-runs the same
search with off-map nodes excluded and prints both: exposure, safety horizon
(capped key step), drive time, distance past the tile edge — i.e. what the
best ON-map refuge would have been and why it lost (the off-tile field is
unknown, so an off-tile node reads an unbounded horizon).

Usage: py -3 scratch_review/old_black_leave_diag.py KEY [--zone standard]
         [--ticks 40] [--intensity high] [--dist 7000] [--set k=v ...]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from scratch_review.old_black_trace import _cfg  # noqa: E402
from scratch_review.roads_offroad_probe import (EXT_DIST_M, N_1H, N_3H,  # noqa: E402
                                                N_CLIENTS, N_CREWS, PAGE_CFG,
                                                SEED, _ignite)
from wgresponse import geo, simui  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--zone", default="standard")
    ap.add_argument("--ticks", type=int, default=40)
    ap.add_argument("--intensity", default="high")
    ap.add_argument("--dist", type=float, default=7000.0)
    ap.add_argument("--ext-dist", type=float, default=EXT_DIST_M)
    ap.add_argument("--set", default="")
    a = ap.parse_args()
    loc = simui.LOCATIONS[a.key]
    s = simui.SimSession(a.key, N_CLIENTS, N_CREWS, seed=SEED,
                         cfg_overrides=dict(PAGE_CFG, **_cfg(a.set)),
                         fire_zone=a.zone, n_crews_1h=N_1H, n_crews_3h=N_3H)
    _ignite(s, loc, a.zone, a.intensity, a.dist, a.ext_dist)
    orig = s._road_escape_search
    tick = {"k": 0}

    def search(pos, now, speed, lead, lawful_only=False, min_p10_s=-math.inf):
        found = orig(pos, now, speed, lead, lawful_only=lawful_only,
                     min_p10_s=min_p10_s)
        if found is None or s._on_map(found["loc"]):
            return found
        # the best ON-map alternative: the same search with off-map nodes
        # made unlawful (a scratch patch of the key, this call only)
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
        cap = s._cfg.escape_road_horizon_cap_h

        def desc(f):
            if f is None or f["key"][0] == 2:
                return "none"
            hz = f["horizon_s"]
            return (f"exp {f['exposure_s']:.0f}s horizon "
                    f"{'inf' if math.isinf(hz) else f'{hz / 3600.0:.1f}h'} "
                    f"(cap {cap:g} h) drive {f['total_s'] / 60.0:.1f} min "
                    f"{geo.haversine_m(pos, f['loc']) / 1000.0:.1f} km key {f['key'][:5]}")
        print(f"t{tick['k']} {'SEM-10e' if lawful_only else 'SEM-10d'} from "
              f"({pos.lat:.5f},{pos.lon:.5f}) OFF-MAP pick "
              f"{s._off_tile_m(found['loc']):.0f} m past the edge: {desc(found)}"
              f"\n      best on-map: {desc(alt)}")
        return found
    s._road_escape_search = search
    for k in range(1, a.ticks + 1):
        tick["k"] = k
        st = s.tick()
    print("left the map:", st["scoreboard"].get("crews_left_map"),
          "served", st["scoreboard"]["served"],
          "relocations", st["scoreboard"]["relocations"])


if __name__ == "__main__":
    main()
