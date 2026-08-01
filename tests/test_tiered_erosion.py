"""FUEL-TIERED corridor erosion (contracts corridor_erosion_tiered; founder
2026-07-30 "in chaparral which will burn slower 100m is likely sufficient").
Each SOURCE cell's arrival projects by its own fuel class; per-tier euclidean
discs, min-combined; dispatch consumes pre-eroded rasters via the
eroded_rasters seam and stays fuel-blind."""
from __future__ import annotations

import dataclasses
import math

from wgresponse import spread
from wgresponse.contracts import GridSpec, Raster, ResponseConfig, TerrainTile
from wgresponse import geo


def _grid(nrows=25, ncols=25, cell_m=90.0):
    return GridSpec(north_lat=34.2, west_lon=-118.3, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)


def _tile_fuel(spec, fill_code):
    return Raster(spec, [float(fill_code)] * (spec.nrows * spec.ncols))


def _p10_hot(spec, hot_rc, t=100.0):
    r = Raster(spec, [math.inf] * (spec.nrows * spec.ncols))
    r.data[hot_rc[0] * spec.ncols + hot_rc[1]] = t
    return r


def _cfg(**over):
    return dataclasses.replace(
        ResponseConfig(), corridor_erosion_tiered=True,
        corridor_erosion_fast_m=450.0,      # 5 cells @ 90 m
        corridor_erosion_shrub_m=180.0,     # 2 cells
        corridor_erosion_slow_m=90.0,       # 1 cell
        **over)


def _reach_cells(er, spec, hot_rc):
    out = []
    for dr in range(-8, 9):
        for dc in range(-8, 9):
            i, j = hot_rc[0] + dr, hot_rc[1] + dc
            if 0 <= i < spec.nrows and 0 <= j < spec.ncols:
                if math.isfinite(er.data[i * spec.ncols + j]):
                    out.append((dr, dc))
    return out


def test_grass_source_projects_wide():
    spec = _grid()
    er = spread.tiered_eroded_p10(_p10_hot(spec, (12, 12)),
                                  _tile_fuel(spec, 102), _cfg())   # GR2
    reach = _reach_cells(er, spec, (12, 12))
    assert (5, 0) in reach            # 450 m along axis
    assert (0, 5) in reach
    assert (6, 0) not in reach        # beyond the fast radius
    assert (4, 4) not in reach        # 509 m diagonal > 450 (true disc)


def test_shrub_source_projects_flame_standoff_scale():
    spec = _grid()
    er = spread.tiered_eroded_p10(_p10_hot(spec, (12, 12)),
                                  _tile_fuel(spec, 145), _cfg())   # SH5
    reach = _reach_cells(er, spec, (12, 12))
    assert (2, 0) in reach            # 180 m
    assert (3, 0) not in reach        # 270 m > shrub tier
    assert (0, 3) not in reach


def test_timber_source_projects_small():
    spec = _grid()
    er = spread.tiered_eroded_p10(_p10_hot(spec, (12, 12)),
                                  _tile_fuel(spec, 186), _cfg())   # TL6
    reach = _reach_cells(er, spec, (12, 12))
    assert (1, 0) in reach and (0, 1) in reach
    assert (2, 0) not in reach and (1, 1) not in reach   # 127 m > 90


def test_mixed_sources_min_combined():
    # grass hot cell at (6,6) t=200; timber hot cell at (18,18) t=100.
    # A probe near the timber cell inside BOTH halos must take the MIN (100);
    # a probe 3 cells from timber but far from grass sees nothing.
    spec = _grid()
    fuel = _tile_fuel(spec, 102)                      # grass everywhere...
    fuel.data[18 * spec.ncols + 18] = 186.0           # ...except one TL cell
    p10 = Raster(spec, [math.inf] * (spec.nrows * spec.ncols))
    p10.data[6 * spec.ncols + 6] = 200.0              # grass source
    p10.data[18 * spec.ncols + 18] = 100.0            # timber source
    er = spread.tiered_eroded_p10(p10, fuel, _cfg())
    assert er.data[19 * spec.ncols + 18] == 100.0     # 1 cell from TL source
    assert math.isinf(er.data[21 * spec.ncols + 18])  # 3 cells: outside both
    # near the grass source, its 450 m halo carries 200
    assert er.data[6 * spec.ncols + 10] == 200.0      # 4 cells away


def test_unclassified_burnable_fails_safe_into_fast_tier():
    spec = _grid()
    er = spread.tiered_eroded_p10(_p10_hot(spec, (12, 12)),
                                  _tile_fuel(spec, 150), _cfg())  # gap code
    reach = _reach_cells(er, spec, (12, 12))
    assert (5, 0) in reach            # treated as FAST, not slow


def test_spec_mismatch_falls_back_to_widest_uniform():
    spec = _grid()
    other = _grid(nrows=11, ncols=11)
    er = spread.tiered_eroded_p10(_p10_hot(spec, (12, 12)),
                                  _tile_fuel(other, 186), _cfg())
    reach = _reach_cells(er, spec, (12, 12))
    assert (5, 0) in reach            # widest (fast) radius, NOT slow


def test_flag_off_uniform_path_unchanged():
    # tiered fn is never consulted with the flag off — the accessor branch
    # is covered by the full-suite byte-identity; here we pin the uniform
    # erosion itself as the reference the flag-off path uses.
    spec = _grid()
    p10 = _p10_hot(spec, (12, 12))
    legacy = geo.erode_min(p10, 500.0)
    again = geo.erode_min(p10, 500.0, shape="chebyshev")
    assert legacy.data == again.data


def test_dispatch_uses_provided_rasters(monkeypatch):
    # plan(eroded_rasters=...) must NOT call safety._erode for provided ids.
    from wgresponse import dispatch as dispatch_mod
    from wgresponse import safety as safety_mod
    calls = []
    real = safety_mod._erode

    def spy(r, radius_m, shape="chebyshev"):
        calls.append(radius_m)
        return real(r, radius_m, shape=shape)

    monkeypatch.setattr(safety_mod, "_erode", spy)
    spec = _grid(nrows=5, ncols=5)
    flat = Raster(spec, [math.inf] * 25)
    ens = type("E", (), {})()
    ens.p10 = flat
    ens.p50 = flat
    ens.p90 = flat
    cfg = ResponseConfig()
    plan = dispatch_mod.plan([], [], {"f1": ens}, [], cfg, 0.0,
                             eroded_rasters={"f1": flat})
    assert calls == []                # provided raster used, no re-erosion
    plan2 = dispatch_mod.plan([], [], {"f1": ens}, [], cfg, 0.0)
    assert calls == [cfg.corridor_erosion_m]   # legacy path erodes
    assert plan is not None and plan2 is not None
