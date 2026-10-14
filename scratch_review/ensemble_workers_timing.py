"""Ensemble workers (2026-10-14): wall time of one live-style ensemble on a
large real hindcast domain (crown fire + the spread-direction fix + the
production posture), in-process vs N workers, and the identity check. NOT
product code. Usage: py -3 scratch_review/ensemble_workers_timing.py [KEY K]
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crown_fire_hindcast as H  # noqa: E402
from wgresponse import spread  # noqa: E402

key = sys.argv[1] if len(sys.argv) > 1 else "littlegiant"
k = int(sys.argv[2]) if len(sys.argv) > 2 else 25
if __name__ == "__main__":
    fire = H.fire_with_canopy(key)
    info = fire.step_info(k)
    t0 = info["t0_s"]
    base = H.ARMS["crown_dep_aa"]
    print(key, k, "cells", fire.tile.spec.nrows * fire.tile.spec.ncols, "members", base.ensemble_n, flush=True)
    res = {}
    for w in (1, 3, 5):
        cfg = dataclasses.replace(base, ensemble_workers=w)
        if w > 1:   # warm the pool (spawn start-up) outside the timing
            spread._member_pool(w)
        tic = time.monotonic()
        ens = spread.ensemble_arrival(fire.tile, [], fire.wx(k), t0, cfg, incident_id=fire.irwin,
                                      perimeter=fire.seed_rings(k), perimeter_at_s=t0)
        res[w] = (time.monotonic() - tic, ens)
        print(f"workers {w}: {res[w][0]:.1f} s", flush=True)
    a = res[1][1]
    for w in (3, 5):
        b = res[w][1]
        same = all(ma.data == mb.data for ma, mb in zip(a.members, b.members)) and a.p10.data == b.p10.data
        print(f"workers {w} identical to in-process: {same}; speed-up {res[1][0] / res[w][0]:.2f}x", flush=True)
