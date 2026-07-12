"""Shared helpers for model-fidelity review experiments (throwaway).

Run from repo root:  py -3 scratch_review/exp1_fuel_gap.py  etc.
Deterministic everywhere (contracts.seeded_rng / fixed inputs, no wall clock).
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo
from wgresponse.contracts import (GridSpec, LatLon, Raster, ResponseConfig,
                                  TerrainTile)

T0 = 1_780_000_000.0          # fixed epoch for all experiments (matches MockWeather default)


def flat_tile(north_lat: float, west_lon: float, cell_m: float,
              nrows: int, ncols: int, fuel_code: float) -> TerrainTile:
    """Uniform flat tile: constant fuel code, slope 0, aspect -1 (flat), elev 100 m."""
    spec = GridSpec(north_lat=north_lat, west_lon=west_lon, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(
        spec=spec,
        fuel=Raster(spec, [float(fuel_code)] * n),
        slope_deg=Raster(spec, [0.0] * n),
        aspect_deg=Raster(spec, [-1.0] * n),
        elev_m=Raster(spec, [100.0] * n),
        source="review-flat",
    )


def set_col_band(tile: TerrainTile, col_lo: int, col_hi: int, code: float) -> None:
    """Overwrite fuel code for all rows in columns [col_lo, col_hi)."""
    spec = tile.spec
    for r in range(spec.nrows):
        for c in range(col_lo, col_hi):
            tile.fuel.set(r, c, float(code))


def east_of(origin: LatLon, km: float) -> LatLon:
    return geo.destination(origin, 90.0, km * 1000.0)


def hours(eta_s: float, t0_s: float) -> float:
    if math.isinf(eta_s):
        return math.inf
    return (eta_s - t0_s) / 3600.0


def fmt_h(v: float) -> str:
    return "inf" if math.isinf(v) else f"{v:7.2f}"


def cfg_default(**kw) -> ResponseConfig:
    cfg = ResponseConfig()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg
