"""scratch_review/old_black_ticktime.py — per-tick wall time of the founder
rulings of 2026-09-26 (old black is a refuge; a crew that leaves the map is
out; contracts old_black_refuge / left_map_out / escape_prefer_on_map), knobs
off vs the simulator defaults, on scratch_review/escape_roads_bench.py's two
sessions (eagle_vail_co standard HIGH 7 km, 40 ticks; boulder_co extended
EXTREME 14 km, 80 ticks). Sequential, one process — the numbers compare.

Usage: py -3 scratch_review/old_black_ticktime.py
"""
from __future__ import annotations

import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scratch_review.escape_roads_bench import CASES, _ignite, _session  # noqa: E402

OFF = {"old_black_refuge": False, "left_map_out": False,
       "escape_prefer_on_map": False}


def run(case, extra):
    key, zone, intensity, dist, ticks = case
    s = _session(key, zone, extra)
    _ignite(s, key, intensity, dist)
    ms = []
    for _ in range(ticks):
        t0 = time.perf_counter()
        s.tick()
        ms.append((time.perf_counter() - t0) * 1000.0)
    return ms


def main():
    for case in CASES:
        for label, extra in (("off", OFF), ("on", {})):
            ms = run(case, extra)
            q = sorted(ms)
            print(f"{case[0]:<14} {case[1]:<9} {label:<4} median {statistics.median(ms):7.1f} ms"
                  f"  p95 {q[int(0.95 * (len(q) - 1))]:7.1f} ms  total {sum(ms) / 1000.0:6.1f} s")


if __name__ == "__main__":
    main()
