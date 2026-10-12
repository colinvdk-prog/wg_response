"""Crown-fire study: the hindcast steps where grass-only floor + crown fire
covers LESS of the newly burned ground with P10 than the deployed engine
(2026-10-12; NOT product code; docs/review/crown_fire_model_2026-10-12.md
section 3.3). For a step, re-solves both arms and tabulates the newly
burned burnable cells the deployed P10 holds and the crown P10 misses: their
fuel class, canopy (crown fuel or not, LANDFIRE hardwood code) and the
crown arm's nominal fire type there.

Usage: py -3 scratch_review/crown_fire_hindcast_misses.py timber:7,timber:10,littlegiant:40
Output: scratch_review/crown_fire_hindcast_misses.json
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "belief_vs_physics"))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_hindcast as H  # noqa: E402
import bvp_run as R  # noqa: E402
from wgresponse import rothermel, spread  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_hindcast_misses.json")


def cls(code):
    return ("GR" if 101 <= code <= 109 else "GS" if 121 <= code <= 124
            else "SH" if 141 <= code <= 149 else "TU" if 161 <= code <= 165
            else "TL" if 181 <= code <= 189 else "SB" if 201 <= code <= 204
            else "NB")


def run(key, k):
    fire = H.fire_with_canopy(key)
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    ens = {}
    for arm in ("deployed", "crown"):
        ens[arm] = spread.ensemble_arrival(
            fire.tile, [], fire.wx(k), t0, H.ARMS[arm], incident_id=fire.irwin,
            perimeter=fire.seed_rings(k), perimeter_at_s=t0)
    trace = {}
    spread.compute_arrival(fire.tile, [], fire.wx(k), t0, H.ARMS["crown"],
                           incident_id=fire.irwin, member_k=0,
                           perimeter=fire.seed_rings(k), perimeter_at_s=t0,
                           crown_trace=trace)
    t0m = R.seed_mask(ens["deployed"], t0)
    t1m, burn = R.obs_masks(fire, k + 1)
    new_b = t1m & ~t0m & burn
    dep = [v <= t1 for v in ens["deployed"].p10.data]
    cro = [v <= t1 for v in ens["crown"].p10.data]
    cp = fire.tile.canopy
    tally = {}
    n_miss = 0
    for i, nb in enumerate(new_b):
        if not nb or not dep[i] or cro[i]:
            continue
        n_miss += 1
        code = int(round(fire.tile.fuel.data[i]))
        b, d, h = cp.cbh_m.data[i], cp.cbd_kgm3.data[i], cp.ch_m.data[i]
        canopy = ("hardwood code" if d == 0.01 and b == 10.0 else
                  "crown fuel" if d >= rothermel.CROWN_MIN_CBD_KGM3 and h > b
                  else "no canopy")
        kind = {None: "-", 0: "surface", 1: "passive", 2: "active"}[
            trace.get(i)]
        key2 = f"{cls(code)} / {canopy} / crown arm nominal: {kind}"
        tally[key2] = tally.get(key2, 0) + 1
    gained = sum(1 for i, nb in enumerate(new_b) if nb and cro[i] and not dep[i])
    out = {"key": key, "k": k, "phase": info["phase"],
           "new_burnable_cells": int(new_b.sum()),
           "missed_by_crown_held_by_deployed": n_miss,
           "held_by_crown_missed_by_deployed": gained,
           "missed_by_class": dict(sorted(tally.items(),
                                          key=lambda kv: -kv[1]))}
    print(json.dumps(out), flush=True)
    return out


def main(argv):
    pairs = [(a.split(":")[0], int(a.split(":")[1]))
             for a in argv[0].split(",")]
    rows = [run(key, k) for key, k in pairs]
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
