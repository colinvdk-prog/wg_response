"""Crown-fire study: cost of the crown step (2026-10-12; NOT product code).
Times one nominal compute_arrival with crown_fire off / on (best of 3) on
the committed Park City and Paradise tiles (the one-click sessions' own
weather and cfg, city weather, grass-only floor, calibrated embers) and,
when the hindcast canopy is cached, on the Little Giant hindcast domain
(604 x 498 cells, production posture, 96 h horizon, one perimeter step).

Usage: py -3 scratch_review/crown_fire_cost.py
Output: scratch_review/crown_fire_cost.json
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_scenarios as C  # noqa: E402
import ember_spotting_probe as E  # noqa: E402
from wgresponse import spread  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_cost.json")


def best(fn, n=3):
    t = []
    for _ in range(n):
        tic = time.perf_counter()
        fn()
        t.append(time.perf_counter() - tic)
    return min(t)


def main():
    rows = []
    for scen in ("park_city_windstorm", "paradise_ne_wind"):
        s, fid = E.build(scen, C.VARIANTS["crown"])
        f = s._fires[fid]
        for crown in (False, True):
            cfg = dataclasses.replace(s._cfg, crown_fire=crown)

            def solve():
                spread.compute_arrival(s._tile, [f.origin], s._wx,
                                       f.t_ignite_s, cfg, incident_id=fid)
            sec = best(solve)
            rows.append({"case": scen, "crown_fire": crown,
                         "cells": s._tile.spec.nrows * s._tile.spec.ncols,
                         "horizon_h": cfg.horizon_h, "nominal_s": round(sec, 3)})
            print(rows[-1], flush=True)
    try:
        import crown_fire_hindcast as H
        fire = H.fire_with_canopy("littlegiant")
        k = H.steps_of("littlegiant")[len(H.steps_of("littlegiant")) // 2]
        info = fire.step_info(k)
        for arm in ("grassonly", "crown"):
            cfg = dataclasses.replace(H.ARMS[arm], ensemble_n=1)

            def solve():
                spread.compute_arrival(fire.tile, [], fire.wx(k), info["t0_s"],
                                       cfg, incident_id=fire.irwin,
                                       perimeter=fire.seed_rings(k),
                                       perimeter_at_s=info["t0_s"])
            sec = best(solve, 2)
            rows.append({"case": f"littlegiant step {k}", "arm": arm,
                         "cells": fire.tile.spec.nrows * fire.tile.spec.ncols,
                         "horizon_h": cfg.horizon_h, "nominal_s": round(sec, 3)})
            print(rows[-1], flush=True)
    except Exception as exc:  # noqa: BLE001
        print("hindcast domain skipped:", exc)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
