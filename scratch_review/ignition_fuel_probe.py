"""Option 2 probe (2026-10-12): where do the one-click scenario fires start,
and where is the nearest grass / grass-shrub / shrub start upwind? NOT product
code. Reads the real committed tiles only (no spread solve).
Usage: py -3 scratch_review/ignition_fuel_probe.py [key,key,...]
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server, simui  # noqa: E402
from wgresponse.fuels import (is_burnable, is_grass, is_grass_shrub,  # noqa: E402
                              is_shrub, is_timber_litter, is_timber_understory)


def cls(code):
    if not is_burnable(code):
        return "NB"
    if is_grass(code):
        return "GR"
    if is_grass_shrub(code):
        return "GS"
    if is_shrub(code):
        return "SH"
    if is_timber_understory(code):
        return "TU"
    if is_timber_litter(code):
        return "TL"
    return "SB"


def frac_fine(tile, rc, radius_m):
    spec = tile.spec
    k = max(1, int(round(radius_m / spec.cell_m)))
    r0, c0 = rc
    n = fine = burn = 0
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            if dr * dr + dc * dc > k * k:
                continue
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < spec.nrows and 0 <= c < spec.ncols):
                continue
            code = int(round(tile.fuel.get(r, c)))
            n += 1
            if is_burnable(code):
                burn += 1
            if cls(code) in ("GR", "GS", "SH"):
                fine += 1
    return fine / n, burn / n


if __name__ == "__main__":
    keys = sys.argv[1].split(",") if len(sys.argv) > 1 else sorted(server.SCENARIOS)
    for key in keys:
        sc = server.SCENARIOS[key]
        loc = simui.LOCATIONS[sc["location"]]
        tile, synth = simui.load_tile(sc["location"])
        spec = tile.spec
        center = loc.center

        def code_at(p):
            rc = geo.latlon_to_rc(spec, p)
            if rc is None:
                return None, None
            return int(round(tile.fuel.get(*rc))), rc

        legacy = None
        for db in (0, 10, -10, 20, -20, 30, -30):
            for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
                p = geo.destination(center, sc["ign_bearing"] + db, sc["ign_dist_m"] * shrink)
                code, rc = code_at(p)
                if code is not None and is_burnable(code):
                    legacy = (db, shrink, code, rc)
                    break
            if legacy:
                break
        db, shrink, code, rc = legacy
        f250, b250 = frac_fine(tile, rc, 250)
        f500, b500 = frac_fine(tile, rc, 500)
        print(f"{key:34s} tile {spec.cell_m:.0f} m {spec.nrows}x{spec.ncols} | legacy db {db:+3d} x{shrink:.1f} "
              f"{cls(code)}({code}) fine250 {f250:.2f} fine500 {f500:.2f}")
        # composition of the whole upwind sector (+-45 deg, 0.3-1.0 x dist)
        counts = {}
        best = []
        for db in range(-45, 46, 5):
            for s100 in range(100, 25, -5):
                shrink = s100 / 100.0
                p = geo.destination(center, sc["ign_bearing"] + db, sc["ign_dist_m"] * shrink)
                code, rc = code_at(p)
                if code is None:
                    continue
                c = cls(code)
                counts[c] = counts.get(c, 0) + 1
                if c in ("GR", "GS", "SH"):
                    f250, _ = frac_fine(tile, rc, 250)
                    f500, _ = frac_fine(tile, rc, 500)
                    best.append((abs(db), -shrink, db, shrink, c, code, f250, f500))
        tot = sum(counts.values())
        print("   sector fuel mix:", ", ".join(f"{k} {v / tot:.0%}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
        best.sort()
        for row in best[:6]:
            _, _, db, shrink, c, code, f250, f500 = row
            print(f"   fine start db {db:+3d} x{shrink:.2f} {c}({code}) fine250 {f250:.2f} fine500 {f500:.2f}")
        good = [r for r in best if r[6] >= 0.5]
        if good:
            _, _, db, shrink, c, code, f250, f500 = good[0]
            print(f"   first with fine250>=0.5: db {db:+3d} x{shrink:.2f} {c}({code}) fine250 {f250:.2f} fine500 {f500:.2f}")
        else:
            print("   NONE with fine250>=0.5")
