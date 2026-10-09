"""Does more wind give a SMALLER fire? (integrator check 2026-10-09 of the
city-weather agent's finding; NOT product code)

Flat uniform fuel, one ignition in the middle of a big tile, constant weather,
spotting OFF, the engine's compute_arrival (nominal physics, no ensemble).
Reports the area burned by +6 h and the head / flank / back distances for
wind 5..40 mph, for GR2 grass and SH5 shrub, with the wind blowing along a grid
axis (270 = from the west) and between stencil directions (281.25).
Expectation from fire physics: area GROWS with wind (the head runs much faster
while the flanks change little; the classic elliptical area grows ~ with the
head rate squared over the length-to-breadth ratio).
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))

from test_spread import T0, center_ignition, const_wx, grass_tile  # noqa: E402
from wgresponse import geo, spread  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402

N = 241                 # 241 x 241 cells at 100 m = 24 km square
HOURS = 6.0


def run(fuel_code: float, wind_mph: float, wdir: float):
    tile = grass_tile(nrows=N, ncols=N, cell_m=100.0, fuel_code=fuel_code)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=wind_mph * 0.44704, wind_dir_deg=wdir,
                  rh_pct=15.0, temp_c=30.0, hours=24)
    cfg = ResponseConfig(spotting_enabled=False, horizon_h=12.0)
    r = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    t_end = T0 + HOURS * 3600.0
    burned = [i for i, v in enumerate(r.data) if v <= t_end]
    spec = tile.spec
    rc0 = geo.latlon_to_rc(spec, ign)
    # downwind direction: wind FROM wdir blows TOWARD wdir+180
    to = math.radians((wdir + 180.0) % 360.0)
    ux, uy = math.sin(to), math.cos(to)          # east, north components
    head = back = flank = 0.0
    for i in burned:
        rr, cc = divmod(i, spec.ncols)
        dx = (cc - rc0[1]) * spec.cell_m
        dy = (rc0[0] - rr) * spec.cell_m
        along = dx * ux + dy * uy
        across = abs(-dx * uy + dy * ux)
        head = max(head, along)
        back = max(back, -along)
        flank = max(flank, across)
    acres = len(burned) * (spec.cell_m ** 2) / 4046.86
    return acres, head / 1000.0, back / 1000.0, flank / 1000.0


if __name__ == "__main__":
    for fuel, label in ((102.0, "GR2 grass"), (145.0, "SH5 shrub")):
        for wdir in (270.0, 281.25):
            print(f"== {label}, wind FROM {wdir}")
            for mph in (5, 10, 15, 20, 25, 30, 40):
                a, h, b, f = run(fuel, mph, wdir)
                print(f"   {mph:2d} mph: {a:8.0f} ac at 6 h | head {h:5.1f} km "
                      f"back {b:4.2f} km half-width {f:4.2f} km", flush=True)
