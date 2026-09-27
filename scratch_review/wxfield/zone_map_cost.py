"""Cost of the nearest-point zone map on a real 300k-cell domain (SAFETY
SEMANTICS 17, 2026-09-27). NOT product code — part of measurement C.

Times wxfield.compute_zone_map (the lower-envelope row walk) on Willow's
national domain (512 x 587 cells at 129.5 m) with its 16 forecast points and
with a 64-point lattice, against a brute-force nearest-point loop, and
checks they agree cell for cell.

    py -3 scratch_review/wxfield/zone_map_cost.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)

from wgresponse import geo, wxfield  # noqa: E402
from wgresponse.contracts import GridSpec, LatLon, ResponseConfig  # noqa: E402

# Willow's national domain grid (national_wxfield.json domains[0])
SPEC = GridSpec(north_lat=39.75375, west_lon=-107.14103, cell_m=129.5,
                nrows=512, ncols=587)


def brute(spec, points):
    out = bytearray(spec.nrows * spec.ncols)
    for r in range(spec.nrows):
        lat_r = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
        mlon = geo.m_per_deg_lon(lat_r)
        xs = [(p.lon - spec.west_lon) * mlon for p in points]
        dys = [((p.lat - lat_r) * geo.M_PER_DEG_LAT) ** 2 for p in points]
        base = r * spec.ncols
        for c in range(spec.ncols):
            u = (c + 0.5) * spec.cell_m
            best, bd = 0, None
            for i in range(len(points)):
                dx = xs[i] - u
                d = dx * dx + dys[i]
                if bd is None or d < bd:
                    best, bd = i, d
            out[base + c] = best
    return bytes(out)


def main():
    centroid = LatLon(39.2213, -106.4257)
    for grid_km, cap in ((10.0, 16), (5.0, 64)):
        cfg = ResponseConfig(weather_grid_km=grid_km, weather_points_max=cap)
        nodes, spacing, level = wxfield.lattice_points(SPEC, cfg)
        pts = [centroid] + nodes
        t = time.perf_counter()
        zm = wxfield.compute_zone_map(SPEC, pts)
        fast = time.perf_counter() - t
        t = time.perf_counter()
        ref = brute(SPEC, pts)
        slow = time.perf_counter() - t
        print(f"{len(pts)} points at {spacing:.3g} km: envelope walk "
              f"{fast * 1000:.0f} ms, brute force {slow:.1f} s, identical "
              f"{zm == ref}")


if __name__ == "__main__":
    main()
