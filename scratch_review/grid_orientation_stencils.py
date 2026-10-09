"""Grid-orientation study (2026-10-09; NOT product code): could a bigger
lattice neighbourhood fix it? Analytic, no engine solve.

A minimum-travel-time lattice in a uniform medium reaches the convex hull of
the ellipse's ray points along its directions (grid_orientation_common.
polygon). For every neighbourhood of radius N (all primitive offsets with
max(|dr|, |dc|) <= N; N=2 is the engine's 16) and L/B 2-8, sweep the wind
over one 45-degree octant in half-degree steps and report the worst and mean
head reach (largest projection on the head axis) and area, as fractions of
the ellipse.

Usage: py -3 scratch_review/grid_orientation_stencils.py
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from grid_orientation_common import analytic, polygon, rothermel  # noqa: E402


def bearings(n: int) -> list:
    out = set()
    for dr in range(-n, n + 1):
        for dc in range(-n, n + 1):
            if (dr, dc) == (0, 0) or math.gcd(abs(dr), abs(dc)) != 1:
                continue
            out.add(round(math.degrees(math.atan2(dc, -dr)) % 360.0, 9))
    return sorted(out)


def head_reach(lb: float, brg: float, brgs: list) -> float:
    return max(rothermel.directional_ros_mps(1.0, lb, th - brg)
               * math.cos(math.radians(th - brg)) for th in brgs)


if __name__ == "__main__":
    for n in (2, 3, 4, 5, 6, 8):
        b = bearings(n)
        gap = max((b[(i + 1) % len(b)] - b[i]) % 360.0 for i in range(len(b)))
        print(f"N={n}: {len(b)} directions, widest gap {gap:.2f} deg")
        for lb in (2.0, 3.0, 4.5, 6.8, 8.0):
            heads, areas = [], []
            for k in range(91):
                brg = 90.0 + 0.5 * k
                heads.append(head_reach(lb, brg, b))
                areas.append(polygon(1.0, lb, brg, 1.0, bearings=b)["acres"]
                             / analytic(1.0, lb, 1.0)["acres"])
            print(f"   L/B {lb:3.1f}: head worst {min(heads):.2f} mean "
                  f"{sum(heads) / len(heads):.2f} | area worst {min(areas):.2f} "
                  f"mean {sum(areas) / len(areas):.2f}", flush=True)
