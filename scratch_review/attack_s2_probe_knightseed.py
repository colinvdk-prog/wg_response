"""Probe: does the perimeter boundary-seed optimization (8-neighbor boundary
detection) drop 16-neighbor knight-move shortcuts from cells one ring INSIDE
the mask?

Method: two perimeters with the SAME outer boundary —
  M  = solid square (interior fully burned; one-ring-inside cells are
       'interior-of-interior' by the 8-neighbor test and are NOT seeded);
  M' = same square with a small interior HOLE (even-odd), which turns the
       one-ring-inside cells into hole-boundary cells that DO get seeded.
The hole only removes burned area, so exterior arrivals under M' can never be
legitimately earlier than under M. If any exterior cell arrives EARLIER under
M', the solid-mask run lost a knight-edge relaxation (late bias).

Strong wind chosen so the ellipse is narrow and the head bearing (~153 deg)
aligns with a knight vector crossing the south boundary obliquely.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread
from wgresponse.contracts import (GridSpec, Raster, ResponseConfig,
                                  TerrainTile)
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
PERIM_T = T0 - 2 * 3600.0


def _tile(nrows=80, ncols=80, cell_m=100.0):
    spec = GridSpec(north_lat=34.60, west_lon=-119.20, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="probe")


def _rect_ring(spec, r0, c0, r1, c1):
    """Ring through the CENTERS of the rectangle corners (r0..r1, c0..c1)."""
    return [geo.rc_to_latlon(spec, r0, c0), geo.rc_to_latlon(spec, r0, c1),
            geo.rc_to_latlon(spec, r1, c1), geo.rc_to_latlon(spec, r1, c0)]


def main():
    tile = _tile()
    spec = tile.spec
    # head toward ~153 deg => wind FROM ~333 (met convention: +180)
    wx = MockWeather("probe-knight", base_wind_mps=14.0, dir_deg=333.0,
                     rh_pct=15.0, temp_c=35.0, t0_s=T0).forecast(
                         geo.rc_to_latlon(spec, 20, 40), 110)
    cfg = ResponseConfig(horizon_h=96.0)
    # Hole reaches within ONE ring of the outer boundary, so the solid run's
    # 'interior-of-interior' cells (rows/cols one inside the edge) become
    # hole-adjacent boundary cells in the holed run and get seeded.
    outer = _rect_ring(spec, 10, 10, 30, 60)
    hole = _rect_ring(spec, 12, 12, 28, 58)
    solid = spread.compute_arrival(tile, [], wx, T0, cfg,
                                   perimeter=[outer], perimeter_at_s=PERIM_T)
    holed = spread.compute_arrival(tile, [], wx, T0, cfg,
                                   perimeter=[outer, hole],
                                   perimeter_at_s=PERIM_T)
    mask_solid = set(spread._perimeter_interior_cells(spec, [outer]))
    worst = 0.0
    worst_idx = None
    n_earlier = 0
    for i in range(spec.nrows * spec.ncols):
        if i in mask_solid:
            continue                      # compare EXTERIOR cells only
        a, b = solid.data[i], holed.data[i]
        if math.isinf(a) and math.isinf(b):
            continue
        if b < a - 1e-6:                  # holed run EARLIER outside => loss
            n_earlier += 1
            if a - b > worst:
                worst, worst_idx = a - b, i
    print(f"exterior cells earlier under the HOLED mask: {n_earlier}")
    if worst_idx is not None:
        r, c = divmod(worst_idx, spec.ncols)
        print(f"worst late-bias of the solid-mask run: {worst:.1f} s "
              f"({worst / 60.0:.2f} min) at cell ({r},{c}) "
              f"[solid {solid.data[worst_idx] - T0:.0f}s vs "
              f"holed {holed.data[worst_idx] - T0:.0f}s after t0]")


if __name__ == "__main__":
    main()
