"""Corridor re-site probe (founder 2026-10-12: "Move its start"): where on the
Eagle -> Vail corridor tile can a grass / brush start lead a WSW-wind fire into
conifer that crowns? Static map reading only (no spread solve). NOT product
code. Usage: py -3 scratch_review/corridor_conifer_probe.py
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server, simui  # noqa: E402
from wgresponse.fuels import is_burnable, is_grass, is_grass_shrub, is_shrub  # noqa: E402

KEY = "eagle_vail_corridor_westerly"
sc = server.SCENARIOS[KEY]
tile, _ = simui.load_tile(sc["location"])
spec, cp = tile.spec, tile.canopy
center = simui.LOCATIONS[sc["location"]].center
WIND_FROM = sc["ign_bearing"]
DOWN = (WIND_FROM + 180.0) % 360.0


def idx(p):
    rc = geo.latlon_to_rc(spec, p)
    return None if rc is None else rc[0] * spec.ncols + rc[1]


def fine(code):
    return is_grass(code) or is_grass_shrub(code) or is_shrub(code)


def conifer(i):
    return cp.cbd_kgm3.data[i] >= 0.02 and cp.ch_m.data[i] > cp.cbh_m.data[i]


def hardwood(i):
    return abs(cp.cbd_kgm3.data[i] - 0.01) < 1e-9 and abs(cp.cbh_m.data[i] - 10.0) < 1e-9


def patch(i, k):
    r0, c0 = divmod(i, spec.ncols)
    n = f = 0
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            r, c = r0 + dr, c0 + dc
            if dr * dr + dc * dc > k * k or not (0 <= r < spec.nrows and 0 <= c < spec.ncols):
                continue
            n += 1
            f += fine(int(round(tile.fuel.data[r * spec.ncols + c])))
    return f / n


def swath(p, length_m=6000.0, half_w=400.0, step=200.0):
    con = hw = burn = tot = 0
    for a in range(0, int(length_m / step) + 1):
        q = geo.destination(p, DOWN, a * step)
        for w in range(-int(half_w / step), int(half_w / step) + 1):
            r = geo.destination(q, (DOWN + 90.0) % 360.0, w * step)
            i = idx(r)
            if i is None:
                continue
            tot += 1
            code = int(round(tile.fuel.data[i]))
            if is_burnable(code):
                burn += 1
                con += conifer(i)
                hw += hardwood(i)
    return con / max(1, tot), hw / max(1, tot), burn / max(1, tot)


# the whole tile's canopy mix
n = spec.nrows * spec.ncols
print(f"tile {spec.nrows}x{spec.ncols} @ {spec.cell_m:.0f} m: conifer "
      f"{sum(conifer(i) for i in range(n)) / n:.1%}, hardwood code "
      f"{sum(hardwood(i) for i in range(n)) / n:.1%}")
canon = geo.destination(center, WIND_FROM, sc["ign_dist_m"])
print("canonical start swath (conifer, hardwood, burnable):",
      tuple(round(x, 2) for x in swath(canon)))
k = max(1, int(round(250.0 / spec.cell_m)))
rows = []
for db in range(-60, 61, 5):
    for s100 in range(20, 181, 5):
        d = sc["ign_dist_m"] * s100 / 100.0
        p = geo.destination(center, WIND_FROM + db, d)
        i = idx(p)
        if i is None:
            continue
        code = int(round(tile.fuel.data[i]))
        if not fine(code) or patch(i, k) < 0.5:
            continue
        con, hw, burn = swath(p)
        disp = geo.haversine_m(p, canon)
        rows.append((disp, db, s100, code, round(patch(i, k), 2), round(con, 2), round(hw, 2),
                     round(geo.haversine_m(p, center) / 1000.0, 1), p))
print(len(rows), "fine-fuel patch starts in the wide envelope")
print("nearest 12 to the canonical start:")
for r in sorted(rows)[:12]:
    print(f"  moved {r[0]:6.0f} m db {r[1]:+3d} dist {r[2]:3d}% fuel {r[3]} patch {r[4]} "
          f"downwind conifer {r[5]:.0%} hardwood {r[6]:.0%} from centre {r[7]} km")
print("best downwind conifer (>= 25 %), nearest first:")
for r in sorted([r for r in rows if r[5] >= 0.25])[:12]:
    print(f"  moved {r[0]:6.0f} m db {r[1]:+3d} dist {r[2]:3d}% fuel {r[3]} patch {r[4]} "
          f"downwind conifer {r[5]:.0%} hardwood {r[6]:.0%} from centre {r[7]} km  "
          f"{r[8].lat:.5f},{r[8].lon:.5f}")
