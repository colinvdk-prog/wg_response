"""ATTACK SUITE 5 — fuel-band exit gate (Sprint-2 A3, Council-1 model F1).

Ports scratch_review/exp1_fuel_gap.py (the Council-1 evidence: a 270 m band of
GR3=103 — burnable, but missing from the then-14-row FUEL_TABLE — turned a
23.1 h client ETA into inf: the alert NEVER fires) and then fuzzes the
no-barrier guarantee far beyond the review geometry:

  * GR3 band: client ETA finite and sane vs the table-complete baseline;
  * true non-burnable NB band must STILL be a barrier (the fix must not have
    made urban/water cells flammable);
  * fuzz: 200 seeded-random draws of (band code in the burnable namespace
    100..204 — including every codepoint with NO table row, e.g. 100,
    110..120, 125..140, 150..160, 166..180, 190..200 —, band position/width,
    tile size, wind speed): the client ETA must be finite in EVERY draw in
    which the uniform-GR2 baseline is finite. ZERO inf-behind-band artifacts.

Run:  py -3 -m pytest -q scratch_review/attack_fuel_band.py
Deterministic (contracts.seeded_rng); no network. Product code untouched.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from wgresponse import geo, spread
from wgresponse.contracts import (
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    seeded_rng,
)
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0


def _flat_tile(north, west, cell_m, nrows, ncols, code) -> TerrainTile:
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(spec=spec, fuel=Raster(spec, [float(code)] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n), source="attack-flat")


def _set_band(tile, col_lo, col_hi, code):
    for r in range(tile.spec.nrows):
        for c in range(col_lo, col_hi):
            tile.fuel.set(r, c, float(code))


def _cfg(**kw) -> ResponseConfig:
    cfg = ResponseConfig()
    cfg.ensemble_n = 1
    cfg.horizon_h = 96.0
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# ===========================================================================
# 5a — the review band geometry (exp1): GR3 band no longer a wall
# ===========================================================================

def test_gr3_band_finite_eta_matches_table_complete_baseline():
    """exp1_fuel_gap.py geometry: 20x4 km GR2 strip @90 m, fire 1 km in, client
    15 km downwind, 5 m/s tailwind, 270 m GR3 band at ~6.7 km."""
    CELL, NROWS, NCOLS = 90.0, 45, 245
    NORTH, WEST = 34.52, -119.0
    wx = MockWeather("exp1", base_wind_mps=5.0, dir_deg=270.0, rh_pct=25.0,
                     temp_c=30.0, t0_s=T0)
    cfg = _cfg()
    spec = _flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0).spec
    ign = geo.rc_to_latlon(spec, NROWS // 2, 11)
    client = geo.destination(ign, 90.0, 15_000.0)
    forecast = wx.forecast(ign, cfg.horizon_h + 2)

    def eta_with_band(code):
        tile = _flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0)
        if code is not None:
            _set_band(tile, 85, 88, code)
        arr = spread.compute_arrival(tile, [ign], forecast, T0, cfg)
        v = geo.raster_sample(arr, client)
        return math.inf if v is None else (v - T0) / 3600.0

    eta_base = eta_with_band(None)          # uniform GR2 (review: ~23.9 h)
    eta_gr3 = eta_with_band(103.0)          # the Council-1 killer band
    eta_nb = eta_with_band(91.0)            # true non-burnable control

    assert math.isfinite(eta_base) and eta_base < cfg.horizon_h
    assert math.isfinite(eta_gr3), (
        "A3 REGRESSION: GR3 band is a fireproof wall again (ETA=inf, "
        "alert never fires)")
    # GR3 is a FASTER grass than GR2 — crossing 270 m of it must not slow the
    # run by more than a few percent, and must never be later than +10% of
    # the uniform-GR2 baseline.
    assert eta_gr3 <= eta_base * 1.10, (eta_gr3, eta_base)
    # the fix must NOT have made genuinely non-burnable cells flammable:
    assert math.isinf(eta_nb), "NB1 (urban) band no longer blocks spread"


# ===========================================================================
# 5b — fuzz: 200 random burnable codes on random band geometries
# ===========================================================================

def test_fuzz_200_burnable_band_codes_zero_inf_behind_band():
    NORTH, WEST = 34.52, -119.0
    burnable_codes = [c for c in range(100, 205) if is_burnable(c)]
    assert len(burnable_codes) == 105
    missing_row = [c for c in burnable_codes if c not in FUEL_TABLE]
    assert missing_row, "fuzz loses power: every burnable code has a row?"

    rng = seeded_rng("attack.fuel_band.fuzz", "v1")
    cfg = _cfg()
    artifacts = []
    tested_missing = 0
    for i in range(200):
        # bias half the draws onto the row-less codes (the dangerous ones)
        if i % 2 == 0:
            code = missing_row[rng.randrange(len(missing_row))]
            tested_missing += 1
        else:
            code = burnable_codes[rng.randrange(len(burnable_codes))]
        nrows = rng.randrange(15, 30)
        ncols = rng.randrange(50, 70)
        cell = 90.0
        band_lo = rng.randrange(15, 35)
        band_w = rng.randrange(1, 4)                     # 90..270 m band
        wind = 3.0 + 7.0 * rng.random()                  # 3..10 m/s tailwind

        wx = MockWeather(("fuzz", i), base_wind_mps=wind, dir_deg=270.0,
                         rh_pct=25.0, temp_c=30.0, t0_s=T0)
        spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell,
                        nrows=nrows, ncols=ncols)
        ign = geo.rc_to_latlon(spec, nrows // 2, 3)
        client = geo.rc_to_latlon(spec, nrows // 2, ncols - 5)
        forecast = wx.forecast(ign, cfg.horizon_h + 2)

        base_tile = _flat_tile(NORTH, WEST, cell, nrows, ncols, 102.0)
        arr = spread.compute_arrival(base_tile, [ign], forecast, T0, cfg)
        v = geo.raster_sample(arr, client)
        eta_base = math.inf if v is None else v
        assert math.isfinite(eta_base), f"draw {i}: GR2 baseline unreachable?!"

        band_tile = _flat_tile(NORTH, WEST, cell, nrows, ncols, 102.0)
        _set_band(band_tile, band_lo, band_lo + band_w, code)
        arr2 = spread.compute_arrival(band_tile, [ign], forecast, T0, cfg)
        v2 = geo.raster_sample(arr2, client)
        eta_band = math.inf if v2 is None else v2
        if not math.isfinite(eta_band):
            artifacts.append((i, code, band_lo, band_w, round(wind, 2)))

    assert tested_missing >= 90
    assert artifacts == [], (
        f"{len(artifacts)} inf-behind-band artifacts (draw, code, band_lo, "
        f"band_w, wind): {artifacts[:10]} — a burnable band is a wall again")


# ===========================================================================
# 5c — every FBFM40 spec code has a real row; fallback only for the gaps
# ===========================================================================

def test_fbfm40_table_complete_and_fallback_reserved_for_gaps():
    fbfm40 = list(range(101, 110)) + list(range(121, 125)) + \
        list(range(141, 150)) + list(range(161, 166)) + \
        list(range(181, 190)) + list(range(201, 205))
    assert len(fbfm40) == 40
    missing = [c for c in fbfm40 if c not in FUEL_TABLE]
    assert missing == [], f"A3 REGRESSION: FBFM40 rows missing: {missing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
