"""hazard_roadside_fuels — READ-ONLY: what fuel actually sits beside the roads.

For each cached tile + road graph, measures:
  1. tile-wide FBFM40 class histogram
  2. the NEAREST-BURNABLE-FUEL class within R metres of every road-edge sample,
     broken out by OSM highway_class -> the real (roadside fuel, road class)
     pairs the passability table has to cover
  3. how much of each road network is more than R m from ANY burnable cell
     (the "already a fuel break" fraction)

py -3 scratch_review/hazard_roadside_fuels.py
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wgresponse import geo, roads
from wgresponse.contracts import LatLon
from wgresponse.fuels import is_burnable
from wgresponse.terrain import load_ascii_tile

TILE_DIR = Path(__file__).resolve().parent.parent / "data" / "tiles"
KEYS = ["paradise_ca", "malibu_ca", "santa_rosa_ca", "boulder_co", "park_city_ut"]
RADII = (0.0, 45.0, 90.0, 180.0)
SAMPLE_M = 90.0        # one sample per cell-length of road


def group_of(code: int) -> str:
    if 101 <= code <= 109:
        return "GR"
    if 121 <= code <= 124:
        return "GS"
    if 141 <= code <= 149:
        return "SH"
    if 161 <= code <= 165:
        return "TU"
    if 181 <= code <= 189:
        return "TL"
    if 201 <= code <= 204:
        return "SB"
    if 91 <= code <= 99:
        return "NB"
    return "??"


def tile_hist(tile):
    c = Counter()
    for v in tile.fuel.data:
        c[int(round(v))] += 1
    return c


def rc_of(spec, p: LatLon):
    r = int(math.floor((spec.north_lat - p.lat) * 111_320.0 / spec.cell_m))
    mlon = 111_320.0 * math.cos(math.radians(p.lat))
    c = int(math.floor((p.lon - spec.west_lon) * mlon / spec.cell_m))
    if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
        return r, c
    return None


def nearest_burnable(tile, p: LatLon, radius_m: float):
    """(code, dist_m) of the nearest burnable cell centre within radius, else None."""
    spec = tile.spec
    rc = rc_of(spec, p)
    if rc is None:
        return None
    r0, c0 = rc
    k = int(math.ceil(radius_m / spec.cell_m))
    best = None
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < spec.nrows and 0 <= c < spec.ncols):
                continue
            code = int(round(tile.fuel.get(r, c)))
            if not is_burnable(code):
                continue
            d = math.hypot(dr, dc) * spec.cell_m
            if d > radius_m:
                continue
            if best is None or d < best[1]:
                best = (code, d)
    return best


def edge_samples(g, spec):
    """Yield (highway_class, LatLon) every ~SAMPLE_M along every edge."""
    for e in g.edges:
        pts = [g.nodes[e.u]] + [LatLon(*p) if isinstance(p, tuple) else p
                                for p in e.geometry] + [g.nodes[e.v]]
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            d = geo.haversine_m(a, b)
            n = max(1, int(math.ceil(d / SAMPLE_M)))
            for j in range(n):
                f = (j + 0.5) / n
                yield e.highway_class, LatLon(a.lat + (b.lat - a.lat) * f,
                                             a.lon + (b.lon - a.lon) * f)


def main():
    for key in KEYS:
        path = TILE_DIR / f"{key}.wgt"
        if not path.exists():
            print(f"[skip] {key}: no tile")
            continue
        tile = load_ascii_tile(str(path))
        spec = tile.spec
        h = tile_hist(tile)
        n = sum(h.values())
        print("=" * 96)
        print(f"{key}  grid {spec.nrows}x{spec.ncols} cell {spec.cell_m:.0f} m  "
              f"({n} cells)")
        print("=" * 96)
        grp = Counter()
        for code, cnt in h.items():
            grp[group_of(code)] += cnt
        print("  group mix: " + "  ".join(
            f"{k}={100.0*v/n:5.1f}%" for k, v in sorted(grp.items(),
                                                        key=lambda x: -x[1])))
        top = sorted(h.items(), key=lambda x: -x[1])[:8]
        print("  top codes: " + "  ".join(
            f"{c}({group_of(c)})={100.0*v/n:4.1f}%" for c, v in top))

        g = roads.load_road_graph(key)
        if g is None:
            print("  [no road graph]")
            continue
        samples = list(edge_samples(g, spec))
        print(f"  road samples: {len(samples)} "
              f"({len(g.edges)} edges, {len(g.nodes)} nodes)")

        for R in RADII:
            per_class = defaultdict(Counter)
            nofuel = Counter()
            tot = Counter()
            for hc, p in samples:
                tot[hc] += 1
                nb = nearest_burnable(tile, p, R)
                if nb is None:
                    nofuel[hc] += 1
                else:
                    per_class[hc][group_of(nb[0])] += 1
            print(f"\n  --- nearest burnable within {R:.0f} m ---")
            print(f"  {'road class':<18} {'n':>7} {'no-fuel':>8}  mix")
            for hc in sorted(tot, key=lambda x: -tot[x]):
                if tot[hc] < 20:
                    continue
                mix = per_class[hc]
                s = sum(mix.values())
                txt = " ".join(f"{k}:{100.0*v/tot[hc]:.0f}%"
                               for k, v in sorted(mix.items(), key=lambda x: -x[1]))
                print(f"  {hc:<18} {tot[hc]:>7} "
                      f"{100.0*nofuel[hc]/tot[hc]:7.1f}%  {txt}")
        print()


if __name__ == "__main__":
    main()
