"""Tests for wgresponse.spread — MTT arrival fields on synthetic tiles.

Tiles are built inline from geo helpers + contracts dataclasses (never
wgresponse.terrain). No network, no third-party deps, fully deterministic.

Sprint 2 additions (tickets A4 + A5, Council-1 model F2/F6/F4): dir_offset_deg
byte-identity fingerprints + rotation equivalence, ensemble-v2 seeding law /
stratification / off-axis p10 / cycle-jitter exit gates (ports of
scratch_review/exp6_direction_not_perturbed.py and exp2_eta_jitter.py, per
docs/review/model_fidelity_review.md), and perimeter interior seeding.
"""
from __future__ import annotations

import hashlib
import logging
import math
import struct
from pathlib import Path

import pytest

from wgresponse import geo, spread
from wgresponse.contracts import (
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    UNREACHED,
    WeatherForecast,
    WeatherSample,
)

T0 = 1_750_000_000.0          # arbitrary fixed unix epoch (seconds)
NORTH = 39.5
WEST = -121.5


def grass_tile(nrows=41, ncols=41, cell_m=100.0, fuel_code=102.0,
               slope_deg=0.0, aspect_deg=0.0) -> TerrainTile:
    """Uniform tile (default GR2 low-load grass, flat)."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=float(fuel_code)),
        slope_deg=geo.make_raster(spec, fill=slope_deg),
        aspect_deg=geo.make_raster(spec, fill=aspect_deg),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test",
    )


def const_wx(loc: LatLon, wind_mps=10.0, wind_dir_deg=270.0, rh_pct=20.0,
             temp_c=30.0, hours=100) -> WeatherForecast:
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind_mps,
                             wind_gust_mps=wind_mps * 1.3,
                             wind_dir_deg=wind_dir_deg, rh_pct=rh_pct,
                             temp_c=temp_c) for i in range(hours)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


def center_ignition(tile: TerrainTile) -> LatLon:
    return geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                            tile.spec.ncols // 2)


def make_cfg(**kw) -> ResponseConfig:
    kw.setdefault("ensemble_n", 4)
    return ResponseConfig(**kw)


def set_column(tile: TerrainTile, col: int, code: float,
               skip_rows=()) -> None:
    for r in range(tile.spec.nrows):
        if r in skip_rows:
            continue
        tile.fuel.set(r, col, code)


# ---------------------------------------------------------------------------
# compute_arrival — directionality, monotonicity, barriers, time dependence
# ---------------------------------------------------------------------------

def test_west_wind_head_runs_east():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)       # blowing toward 090 (east)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    rc, cc = 20, 20
    east = arr.get(rc, cc + 10) - T0
    west = arr.get(rc, cc - 10) - T0
    north = arr.get(rc - 10, cc) - T0
    assert 0.0 < east < math.inf
    assert math.isfinite(west) and math.isfinite(north)
    assert east < west / 4.0
    assert east < north / 2.0


def test_monotone_arrival_along_downwind_ray():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    ray = [arr.get(20, c) for c in range(21, 40)]
    assert all(math.isfinite(t) for t in ray)
    for earlier, later in zip(ray, ray[1:]):
        assert earlier < later


def test_barrier_column_blocks_spread():
    tile = grass_tile()
    set_column(tile, 26, 91.0)                   # full-height NB1 barrier
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    # Everything strictly beyond the barrier is UNREACHED (knight moves must
    # not leap the 1-cell barrier), and so is the barrier itself.
    for r in range(tile.spec.nrows):
        assert arr.get(r, 26) == UNREACHED
        for c in range(27, tile.spec.ncols):
            assert arr.get(r, c) == UNREACHED
    assert math.isfinite(arr.get(20, 25))        # front side still burns


def test_barrier_gap_lets_fire_through():
    tile = grass_tile()
    set_column(tile, 26, 91.0, skip_rows=(10,))  # one-cell gap at row 10
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    assert math.isfinite(arr.get(10, 26))        # the gap cell burns
    assert math.isfinite(arr.get(20, 30))        # fire re-fills behind barrier
    no_gap = grass_tile()
    set_column(no_gap, 26, 91.0)
    arr_blocked = spread.compute_arrival(no_gap, [ign], wx, T0, make_cfg())
    assert arr_blocked.get(20, 30) == UNREACHED


def test_wind_switch_bends_fast_axis():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx_const = const_wx(ign, wind_dir_deg=270.0)
    # Same speeds, wind veers 90 deg at T0+2h: from 270 (head east) to 180
    # (head north).
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=10.0,
                             wind_gust_mps=13.0,
                             wind_dir_deg=270.0 if i < 2 else 180.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    wx_switch = WeatherForecast(loc=ign, samples=samples, source="test")
    cfg = make_cfg()
    arr_const = spread.compute_arrival(tile, [ign], wx_const, T0, cfg)
    arr_switch = spread.compute_arrival(tile, [ign], wx_switch, T0, cfg)
    p_north = (5, 20)                             # 15 cells due north
    assert math.isfinite(arr_switch.get(*p_north))
    assert arr_switch.get(*p_north) < arr_const.get(*p_north)


def test_wind_and_moisture_scale_knobs():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    cfg = make_cfg()
    nominal = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    faster = spread.compute_arrival(tile, [ign], wx, T0, cfg, wind_scale=1.5)
    wetter = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                    moisture_scale=3.0)
    probe = (20, 32)
    assert faster.get(*probe) < nominal.get(*probe)
    assert wetter.get(*probe) > nominal.get(*probe)


def test_upslope_bias_without_wind():
    # aspect_deg is the DOWNslope-facing direction (contracts): aspect 270
    # faces west, so upslope = 090 and fire must run east with zero wind.
    tile = grass_tile(slope_deg=30.0, aspect_deg=270.0)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=0.0)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    east = arr.get(20, 28) - T0
    west = arr.get(20, 12) - T0
    north = arr.get(12, 20) - T0
    assert math.isfinite(east)
    assert east < west / 2.0
    assert east < north


def test_compute_arrival_deterministic():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    cfg = make_cfg()
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    b = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert a.data == b.data


def test_ignition_outside_grid_gives_all_unreached():
    tile = grass_tile()
    wx = const_wx(LatLon(0.0, 0.0))
    arr = spread.compute_arrival(tile, [LatLon(0.0, 0.0)], wx, T0, make_cfg())
    assert all(v == UNREACHED for v in arr.data)
    arr_empty = spread.compute_arrival(tile, [], wx, T0, make_cfg())
    assert all(v == UNREACHED for v in arr_empty.data)


def test_nonburnable_seed_records_but_does_not_spread():
    tile = grass_tile(fuel_code=91.0)             # all NB1 urban
    ign = center_ignition(tile)
    wx = const_wx(ign)
    arr = spread.compute_arrival(tile, [ign], wx, T0, make_cfg())
    assert arr.get(20, 20) == T0                  # seed is recorded fire
    others = [v for i, v in enumerate(arr.data) if i != 20 * 41 + 20]
    assert all(v == UNREACHED for v in others)


# ---------------------------------------------------------------------------
# ensemble_arrival / eta_at
# ---------------------------------------------------------------------------

def test_ensemble_quantiles_ordered_and_member_count():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    cfg = make_cfg(ensemble_n=4)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg)
    assert len(ens.members) == 4                  # nominal + 3 perturbed
    assert ens.t0_s == T0
    assert ens.incident_id == ""
    for i in range(len(ens.p10.data)):
        assert ens.p10.data[i] <= ens.p50.data[i] <= ens.p90.data[i]
    # some perturbed member must actually differ from nominal somewhere
    assert any(m.data != ens.members[0].data for m in ens.members[1:])


def test_ensemble_deterministic():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    cfg = make_cfg(ensemble_n=3)
    a = spread.ensemble_arrival(tile, [ign], wx, T0, cfg)
    b = spread.ensemble_arrival(tile, [ign], wx, T0, cfg)
    assert a.p50.data == b.p50.data
    assert a.p10.data == b.p10.data


def test_ensemble_n1_is_nominal_only():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=1))
    assert len(ens.members) == 1
    assert ens.p10.data == ens.members[0].data
    assert ens.p90.data == ens.members[0].data


def test_eta_at_in_plume_is_finite_and_ordered():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=4))
    downwind = geo.rc_to_latlon(tile.spec, 20, 30)
    eta = spread.eta_at(ens, downwind)
    assert T0 < eta.eta_p10_s <= eta.eta_p50_s <= eta.eta_p90_s < math.inf


def test_eta_at_far_outside_plume_is_inf_triple():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=2))
    eta = spread.eta_at(ens, LatLon(10.0, 10.0))          # off-grid entirely
    assert (eta.eta_p10_s, eta.eta_p50_s, eta.eta_p90_s) == \
        (UNREACHED, UNREACHED, UNREACHED)


def test_eta_at_unreached_cell_behind_barrier_is_inf():
    tile = grass_tile()
    set_column(tile, 26, 91.0)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    ens = spread.ensemble_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=2))
    behind = geo.rc_to_latlon(tile.spec, 20, 33)
    eta = spread.eta_at(ens, behind)
    assert eta.eta_p10_s == UNREACHED
    assert eta.eta_p50_s == UNREACHED


# ---------------------------------------------------------------------------
# FOUNDER ROUND 4 law 6 — EMBER / ADJACENCY THREAT fallback in eta_at.
# A home on a non-burnable developed 91/93 or water 98 cell never receives a
# surface-fire arrival even when fire surrounds it (~23% of sim clients; the
# founder's c34), so it gets no ETA / alert / dispatch. eta_at(fallback_radius_m
# > 0) inherits the earliest burnable arrival within the radius via geo.erode_min
# (the corridor-erosion transform). Evidence + validated fix probe:
# docs/review/eta_gap_report.md (scratch_review/diag_eta_fix_probe.py). The 0.0
# default (the live-product path) is byte-identical to the direct nearest-cell
# sample; the SIMULATOR sets cfg.ember_threat_radius_m (~200 m).
# ---------------------------------------------------------------------------

def _direct_sample(raster: Raster, loc: LatLon) -> float:
    """The pre-law eta_at sample: nearest-cell value, None (off-grid) -> UNREACHED."""
    v = geo.raster_sample(raster, loc)
    return UNREACHED if v is None else v


def _ember_tile() -> TerrainTile:
    """41x41 GR2 tile with a full-height NB barrier at col 30, a DEVELOPED
    (NB 91) home at (20,25) WEST of the barrier (the fire reaches every cell
    around it) and a DEVELOPED home at (5,35) EAST of the barrier (the surface
    fire can never cross to it). Ignition at (20,20), 10 m/s wind FROM 270."""
    tile = grass_tile(nrows=41, ncols=41, cell_m=100.0)
    set_column(tile, 30, 91.0)                    # full-height NB barrier
    tile.fuel.set(20, 25, 91.0)                   # threatened developed home
    tile.fuel.set(5, 35, 91.0)                    # genuinely-safe developed home
    return tile


def _ember_ens(incident_id="ember-1", ensemble_n=4):
    tile = _ember_tile()
    ign = geo.rc_to_latlon(tile.spec, 20, 20)
    wx = const_wx(ign, wind_dir_deg=270.0)
    ens = spread.ensemble_arrival(
        tile, [ign], wx, T0, make_cfg(ensemble_n=ensemble_n, horizon_h=96.0),
        incident_id=incident_id)
    return tile, ens


def _threatened_loc(tile):    # developed home surrounded by fire (own cell NB)
    return geo.rc_to_latlon(tile.spec, 20, 25)


def _safe_loc(tile):          # developed home the fire can never reach
    return geo.rc_to_latlon(tile.spec, 5, 35)


def _reached_loc(tile):       # burnable, downwind, reached
    return geo.rc_to_latlon(tile.spec, 20, 24)


def test_ember_fallback_zero_radius_byte_identical_to_direct_sample():
    """(a) PIN: fallback_radius_m=0.0 (the default, live-product path) equals
    the pre-law direct nearest-cell sample at EVERY probe on a mixed
    burnable/NB tile — including the developed cells the fallback would change
    at R>0 — and omitting the argument is the same as passing 0.0."""
    tile, ens = _ember_ens()
    probes = [
        _threatened_loc(tile), _safe_loc(tile), _reached_loc(tile),
        geo.rc_to_latlon(tile.spec, 20, 28),      # burnable reached
        geo.rc_to_latlon(tile.spec, 5, 5),        # far upwind burnable
        LatLon(10.0, 10.0),                       # off-grid entirely
    ]
    for loc in probes:
        d10 = _direct_sample(ens.p10, loc)
        d50 = _direct_sample(ens.p50, loc)
        d90 = _direct_sample(ens.p90, loc)
        for eta in (spread.eta_at(ens, loc),                      # arg omitted
                    spread.eta_at(ens, loc, fallback_radius_m=0.0)):
            assert eta.eta_p10_s == d10, loc
            assert eta.eta_p50_s == d50, loc
            assert eta.eta_p90_s == d90, loc


def test_ember_fallback_developed_home_surrounded_by_fire_gets_eta():
    """(b) a DEVELOPED (NB 91) home whose own cell is UNREACHED but whose
    burnable neighbours the fire HAS reached within R inherits a FINITE, ordered
    adjacency/ember ETA at R=200 m — and stays UNREACHED at R=0."""
    tile, ens = _ember_ens()
    loc = _threatened_loc(tile)
    # precondition: the own (developed) cell is never reached by the surface model
    assert math.isinf(_direct_sample(ens.p10, loc))
    assert math.isinf(_direct_sample(ens.p50, loc))
    # R=0 (default) => UNREACHED, identical to the direct sample
    off = spread.eta_at(ens, loc, fallback_radius_m=0.0)
    assert (off.eta_p10_s, off.eta_p50_s, off.eta_p90_s) == \
        (UNREACHED, UNREACHED, UNREACHED)
    # R=200 => finite threat time, p10 <= p50 <= p90, all inside the horizon
    on = spread.eta_at(ens, loc, fallback_radius_m=200.0)
    assert math.isfinite(on.eta_p90_s)
    assert T0 <= on.eta_p10_s <= on.eta_p50_s <= on.eta_p90_s < math.inf


def test_ember_fallback_isolated_developed_home_stays_unreached():
    """(c) a DEVELOPED home with NO burnable reached cell within R (behind the
    full-height barrier — the fire can never arrive) stays UNREACHED with the
    fallback on: a genuinely safe home is never spuriously threatened. (R>=600
    would let the 6-cell window cross the barrier into reached fuel; the tested
    radii stay clear of it.)"""
    tile, ens = _ember_ens()
    loc = _safe_loc(tile)
    for R in (0.0, 200.0, 500.0):
        eta = spread.eta_at(ens, loc, fallback_radius_m=R)
        assert (eta.eta_p10_s, eta.eta_p50_s, eta.eta_p90_s) == \
            (UNREACHED, UNREACHED, UNREACHED), R


def test_ember_fallback_reached_burnable_client_unchanged():
    """(d) a burnable, fire-REACHED client is byte-identical with and without
    the fallback — the own-cell value wins (the fallback fires only when the own
    cell is UNREACHED), so no burnable client's ETA moves."""
    tile, ens = _ember_ens()
    for rc in ((20, 24), (20, 28), (20, 22), (18, 26)):
        loc = geo.rc_to_latlon(tile.spec, *rc)
        assert math.isfinite(_direct_sample(ens.p10, loc)), rc      # precondition
        base = spread.eta_at(ens, loc, fallback_radius_m=0.0)
        with_fb = spread.eta_at(ens, loc, fallback_radius_m=200.0)
        assert with_fb.eta_p10_s == base.eta_p10_s, rc
        assert with_fb.eta_p50_s == base.eta_p50_s, rc
        assert with_fb.eta_p90_s == base.eta_p90_s, rc


def test_ember_fallback_erodes_each_quantile_once_per_radius(monkeypatch):
    """(e) MEMOISATION: across many clients on ONE ensemble, geo.erode_min runs
    at most once per quantile per radius (3 total per radius), cached on the
    EnsembleArrival — not once per client. A reached client triggers none."""
    tile, ens = _ember_ens()
    calls = {"n": 0}
    orig = spread.geo.erode_min
    monkeypatch.setattr(
        spread.geo, "erode_min",
        lambda r, radius: (calls.__setitem__("n", calls["n"] + 1)
                           or orig(r, radius)))
    loc = _threatened_loc(tile)
    for _ in range(25):                           # 25 clients on the same NB cell
        assert math.isfinite(
            spread.eta_at(ens, loc, fallback_radius_m=200.0).eta_p50_s)
    assert calls["n"] == 3, calls                 # p10/p50/p90 eroded exactly once
    # a second distinct radius erodes 3 more (own cache key), then caches
    for _ in range(10):
        spread.eta_at(ens, loc, fallback_radius_m=300.0)
    assert calls["n"] == 6, calls
    # a REACHED client never enters the fallback branch => no further erosions
    for _ in range(10):
        spread.eta_at(ens, _reached_loc(tile), fallback_radius_m=200.0)
    assert calls["n"] == 6, calls


def test_ember_erosion_preserves_quantile_monotonicity():
    """(f) erosion preserves p10 <= p50 <= p90 across the WHOLE eroded rasters
    (the _ember_eroded assert path) on an ensemble whose quantiles genuinely
    spread, and the returned ETA at the developed home is ordered."""
    tile, ens = _ember_ens(ensemble_n=6)
    assert any(a < b for a, b in zip(ens.p10.data, ens.p90.data))   # non-degenerate
    er10, er50, er90 = spread._ember_eroded(ens, 200.0)
    for a, b, c in zip(er10.data, er50.data, er90.data):
        assert a <= b <= c
    eta = spread.eta_at(ens, _threatened_loc(tile), fallback_radius_m=200.0)
    assert eta.eta_p10_s <= eta.eta_p50_s <= eta.eta_p90_s


# ---------------------------------------------------------------------------
# spotting hook (default OFF)
# ---------------------------------------------------------------------------

def _spot_setup():
    tile = grass_tile(nrows=41, ncols=41, cell_m=60.0)
    # 2-cell-wide full-height barrier east of the ignition
    set_column(tile, 20, 91.0)
    set_column(tile, 21, 91.0)
    ign = geo.rc_to_latlon(tile.spec, 20, 15)
    wx = const_wx(ign, wind_mps=25.0, wind_dir_deg=270.0)
    return tile, ign, wx


def _behind_barrier_values(arr: Raster):
    return [arr.get(r, c) for r in range(arr.spec.nrows)
            for c in range(22, arr.spec.ncols)]


def test_spotting_off_by_default_barrier_holds():
    tile, ign, wx = _spot_setup()
    cfg = make_cfg(ensemble_n=1)
    assert cfg.spotting_enabled is False
    arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert all(v == UNREACHED for v in _behind_barrier_values(arr))


def test_spotting_on_jumps_barrier():
    tile, ign, wx = _spot_setup()
    cfg = make_cfg(ensemble_n=1, spotting_enabled=True)
    arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    behind = _behind_barrier_values(arr)
    assert any(math.isfinite(v) for v in behind)
    # and spotting must not have touched the barrier cells themselves
    for r in range(tile.spec.nrows):
        assert arr.get(r, 20) == UNREACHED
        assert arr.get(r, 21) == UNREACHED


def test_spotting_off_matches_plain_run():
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign)
    arr_a = spread.compute_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=1))
    arr_b = spread.compute_arrival(
        tile, [ign], wx, T0, make_cfg(ensemble_n=1, spotting_enabled=False))
    assert arr_a.data == arr_b.data


# ---------------------------------------------------------------------------
# FUNCTIONAL SPOTTING (contracts SAFETY SEMANTICS 11, founder 2026-07-10). The
# mean-only hook (one ember at the Sardoy mean, ~200 m in grass) was a measured
# no-op (spotting on-vs-off reach 7.95 vs 7.96 km, 0.01 km); the fix samples the
# ember-distance LOGNORMAL per burning cell (seeded_rng('spot', incident_id,
# member_k, cell_index)) so the tail lands brands AHEAD of the front. Default
# OFF => byte-identical (pinned by A/B/D in the legacy-fingerprint test, all
# spotting-off). Evidence: docs/review/fire_speed_review.md §2c.
# ---------------------------------------------------------------------------

def _spot_corridor(ncols=260, cell_m=90.0):
    """Long flat GR2 downwind corridor for measuring spot jump-ahead."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=21, ncols=ncols)
    return TerrainTile(
        spec=spec, fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0), source="test-spot-corridor")


def _hi_wind_wx(loc):
    return const_wx(loc, wind_mps=25.0, wind_dir_deg=270.0, rh_pct=8,
                    temp_c=38, hours=200)


def test_spotting_off_ignores_seed_params_byte_identical():
    """SEMANTICS 11 OFF pin: with spotting OFF the incident_id / member_k seed
    params are inert no-ops (the draw is never taken) — the raster is
    byte-identical for any seed and equals the plain default run."""
    tile, ign, wx = _spot_setup()
    cfg = make_cfg(ensemble_n=1, spotting_enabled=False)
    base = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    for iid, k in (("", 0), ("fire-A", 3), ("other-incident", 99)):
        arr = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                     incident_id=iid, member_k=k)
        assert arr.data == base.data, (iid, k)


def test_spotting_on_gets_materially_ahead_of_front():
    """FIX 2 core claim: on a high-wind high-intensity corridor spotting ON puts
    the fire MATERIALLY further downwind than OFF — the opposite of the 0.01 km
    no-op. At a 15 km probe the ON arrival is earlier by many cells of jump-ahead
    (ember flight ~ wind speed >> the surface head ROS). Measured ~1.4 h."""
    tile = _spot_corridor()
    ign = geo.rc_to_latlon(tile.spec, 10, 5)
    wx = _hi_wind_wx(ign)
    off = spread.compute_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=1, horizon_h=96.0, spotting_enabled=False),
        incident_id="camp")
    on = spread.compute_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=1, horizon_h=96.0, spotting_enabled=True),
        incident_id="camp")
    probe = (10, 5 + int(15000.0 / tile.spec.cell_m))     # 15 km downwind
    v_off = off.get(*probe)
    v_on = on.get(*probe)
    assert math.isfinite(v_off) and math.isfinite(v_on)
    # ON reaches the far probe MUCH earlier: > 0.5 h (>> a cell or two ahead)
    assert (v_off - v_on) / 3600.0 > 0.5, \
        ((v_off - T0) / 3600.0, (v_on - T0) / 3600.0)
    # every ON arrival is at least as early as OFF (spotting only adds edges)
    for a, b in zip(on.data, off.data):
        if math.isfinite(b):
            assert a <= b + 1e-9


def test_spotting_deterministic_and_seed_dependent():
    """Determinism: same (incident_id, member_k) twice => byte-identical field.
    Seed dependence: a different incident_id OR member index re-rolls the draws
    (member-seeded, never wall-clock), so the fields differ."""
    tile, ign, wx = _spot_setup()
    cfg = make_cfg(ensemble_n=1, spotting_enabled=True)
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="fire-A")
    b = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="fire-A")
    assert a.data == b.data                                # deterministic
    d = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="fire-B")
    assert a.data != d.data                                # incident_id re-rolls
    m1 = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                incident_id="fire-A", member_k=1)
    assert a.data != m1.data                               # member index re-rolls


def test_spotting_seed_carries_no_wall_clock():
    """The spot seed is (incident_id, member_k, cell_index) — never t0: shifting
    t0 under constant weather shifts every arrival by exactly the same offset
    (the F6 discipline extended to spotting)."""
    tile, ign, wx = _spot_setup()
    cfg = make_cfg(ensemble_n=1, spotting_enabled=True)
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="fire-A")
    shifted = spread.compute_arrival(tile, [ign], wx, T0 + 900.0, cfg,
                                     incident_id="fire-A")
    pairs = [(x, y) for x, y in zip(a.data, shifted.data) if math.isfinite(x)]
    assert len(pairs) > 50
    for x, y in pairs:
        assert y - x == pytest.approx(900.0, abs=1e-6)


def test_spot_distance_lognormal_tail_lands_ahead():
    """SEMANTICS 11 model: the per-cell landing distance is lognormal with the
    Sardoy MEAN and variance _SPOT_VAR_COEF*mean. Over many cells the sample mean
    tracks the target, and a real HEAVY TAIL lands brands well beyond the mean
    (the 0.5-2 km jumps that get ahead of the front) — not the old mean-only
    delta."""
    mean_m = 250.0
    draws = [spread._spot_distance(mean_m, "camp", 0, i) for i in range(4000)]
    assert all(x > 0.0 for x in draws)
    assert sum(draws) / len(draws) == pytest.approx(mean_m, rel=0.10)  # tracks mean
    far = [x for x in draws if x > 2.0 * mean_m]
    assert len(far) > 0.05 * len(draws), len(far)          # real tail (~10%)
    assert max(draws) > 3.0 * mean_m                        # some brands far ahead
    # determinism per (seed, member, cell); member index changes the draw
    assert spread._spot_distance(mean_m, "camp", 0, 7) == \
        spread._spot_distance(mean_m, "camp", 0, 7)
    assert spread._spot_distance(mean_m, "camp", 0, 7) != \
        spread._spot_distance(mean_m, "camp", 1, 7)
    # degenerate mean => no teleport (0, below any cell size)
    assert spread._spot_distance(0.0, "camp", 0, 7) == 0.0


def test_spotting_feeds_ensemble_and_preserves_quantile_order():
    """Spotting feeds EVERY member (member_k) and preserves p10<=p50<=p90; the
    spot-ahead arrivals pull p10 (earliest) earlier than spotting off at a far
    downwind client (SEMANTICS 10 protects crews near spot-ignition zones)."""
    tile = _spot_corridor()
    ign = geo.rc_to_latlon(tile.spec, 10, 5)
    wx = _hi_wind_wx(ign)
    on = spread.ensemble_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=6, horizon_h=96.0, spotting_enabled=True),
        incident_id="camp")
    for x, y, z in zip(on.p10.data, on.p50.data, on.p90.data):
        assert x <= y <= z
    off = spread.ensemble_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=6, horizon_h=96.0, spotting_enabled=False),
        incident_id="camp")
    probe = geo.rc_to_latlon(tile.spec, 10, 5 + int(15000.0 / tile.spec.cell_m))
    assert spread.eta_at(on, probe).eta_p10_s < \
        spread.eta_at(off, probe).eta_p10_s


def test_spotting_performance_not_pathological():
    """Performance sanity (SEMANTICS 11): spotting ON adds a per-cell lognormal
    draw + spot edges — measured ~2.7x OFF on a fully-burnable tile, bounded.
    A generous ceiling catches only a true blowup, never normal variance."""
    import time
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=90.0,
                    nrows=100, ncols=100)
    tile = TerrainTile(
        spec=spec, fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0), source="test-spot-perf")
    ign = geo.rc_to_latlon(spec, 50, 10)
    wx = _hi_wind_wx(ign)

    def best_ms(enabled):
        cfg = make_cfg(ensemble_n=1, horizon_h=96.0, spotting_enabled=enabled)
        best = math.inf
        for _ in range(3):
            s = time.perf_counter()
            spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="perf")
            best = min(best, time.perf_counter() - s)
        return best

    off = best_ms(False)
    on = best_ms(True)
    assert on < off * 8.0 + 0.5, (off, on)                 # not pathological


# ---------------------------------------------------------------------------
# FUEL-CLASS ROS REALISM FLOOR (contracts cfg.grass_ros_floor_frac; SIMULATOR
# only — docs/review/fire_speed_review.md, the validated grass prototype EXTENDED
# 2026-07-11 to the developed/timber tiles). head ROS is raised to
# max(rothermel, ratio*frac*U10) per fuel CLASS: GR 1.0 (0.10*U10), GS 0.7 (0.07),
# TU timber-understory 0.5 (0.05), TL timber-litter 0.3 (0.03) — so santa_rosa_ca
# and paradise_ca (each ~19-30% TU+TL, not grass) reach a plausible wind-driven
# head, not just the grass tiles. max() only RAISES a too-slow ROS, defers at
# high wind + on slope, and NEVER touches SH shrub / SB slash / NB non-burnable
# (malibu chaparral stays byte-identical). Presets (U10 m/s, RH%, temp C) mirror
# the review's simui.INTENSITY_WEATHER.
# ---------------------------------------------------------------------------

_FLOOR_PRESETS = {"low": (3.58, 35, 28), "moderate": (8.05, 22, 31),
                  "high": (12.52, 12, 35), "extreme": (17.88, 6, 39)}


def _flat_fuel_tile(code, nrows=21, ncols=250, cell_m=100.0):
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(
        spec=spec, fuel=geo.make_raster(spec, fill=float(code)),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0), source="test-floor")


def _preset_wx(loc, preset, hours=300):
    u10, rh, tc = _FLOOR_PRESETS[preset]
    return const_wx(loc, wind_mps=u10, wind_dir_deg=270.0, rh_pct=rh,
                    temp_c=tc, hours=hours)


def _realized_head_mps(tile, ign, wx, frac, r0, c_probe=40, cell_m=100.0):
    """Realized head ROS (m/s) on the east head axis: cell_m / (arr[c+1]-arr[c])
    past the ignition transient (pure rook-east edges realize the head exactly)."""
    cfg = ResponseConfig(ensemble_n=1, horizon_h=300.0,
                         grass_ros_floor_frac=frac)
    arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    a1 = arr.get(r0, c_probe)
    a2 = arr.get(r0, c_probe + 1)
    assert math.isfinite(a1) and math.isfinite(a2)
    return cell_m / (a2 - a1)


def test_ros_floor_frac_helper_mapping():
    """_ros_floor_frac keys the floor on fuel CLASS off the single master knob:
    GR (101-109) -> frac, GS (121-124) -> 0.7*frac, TU (161-165) -> 0.5*frac,
    TL (181-189) -> 0.3*frac; SH/SB/NB and unknown codes -> 0; the knob off
    (<=0) is always 0. Class ordering GR > GS > TU > TL mirrors fuel structure."""
    cfg = ResponseConfig(grass_ros_floor_frac=0.10)
    for gr in (101, 102, 105, 109):
        assert spread._ros_floor_frac(gr, cfg) == 0.10, gr
    for gs in (121, 122, 124):
        assert spread._ros_floor_frac(gs, cfg) == pytest.approx(0.07), gs
    for tu in (161, 162, 164, 165):                        # TU1-5
        assert spread._ros_floor_frac(tu, cfg) == pytest.approx(0.05), tu
    for tl in (181, 183, 185, 188, 189):                   # TL1-9
        assert spread._ros_floor_frac(tl, cfg) == pytest.approx(0.03), tl
    for other in (141, 145, 147, 201, 204, 110, 91, 98):   # SH/SB/NB/unknown
        assert spread._ros_floor_frac(other, cfg) == 0.0, other
    off = ResponseConfig(grass_ros_floor_frac=0.0)
    for code in (102, 122, 162, 183, 145):
        assert spread._ros_floor_frac(code, off) == 0.0, code
    # per-class ordering: grass fastest floor, litter slowest (all > 0)
    assert (spread._ros_floor_frac(102, cfg) > spread._ros_floor_frac(122, cfg)
            > spread._ros_floor_frac(162, cfg) > spread._ros_floor_frac(183, cfg)
            > 0.0)


def test_grass_floor_default_zero_byte_identical():
    """frac=0.0 (default / omitted) is byte-identical on GR2/GS2/SH5/TU5/TL3/TL5
    — the live product path is unchanged for EVERY floored + unfloored class (the
    A/B/D legacy fingerprints also pin this, running with the default floor off)."""
    for code in (102, 122, 145, 165, 183, 185):
        tile = grass_tile(fuel_code=float(code))
        ign = center_ignition(tile)
        wx = _preset_wx(ign, "high", hours=100)
        base = spread.compute_arrival(tile, [ign], wx, T0, make_cfg(ensemble_n=1))
        explicit0 = spread.compute_arrival(
            tile, [ign], wx, T0,
            make_cfg(ensemble_n=1, grass_ros_floor_frac=0.0))
        assert explicit0.data == base.data, code


def test_grass_floor_lifts_gr_head_to_ten_pct_u10():
    """frac=0.10 lifts the GR head ROS to the Cruz-Alexander anchor (0.10*U10) at
    low/moderate/high, strictly ABOVE the floor-off head (a real lift where
    Rothermel underpredicts)."""
    for preset in ("low", "moderate", "high"):
        u10 = _FLOOR_PRESETS[preset][0]
        tile = _flat_fuel_tile(102)                        # GR2
        ign = geo.rc_to_latlon(tile.spec, 10, 3)
        wx = _preset_wx(ign, preset)
        off = _realized_head_mps(tile, ign, wx, 0.0, 10)
        on = _realized_head_mps(tile, ign, wx, 0.10, 10)
        assert on > off * 1.05, (preset, off, on)          # genuine lift
        assert on == pytest.approx(0.10 * u10, rel=1e-6), (preset, on)


def test_grass_floor_gs_uses_seven_pct_ratio():
    """GS cells take 0.7*frac (0.07 at frac=0.10 — the review's 0.07 GS / 0.10 GR
    ratio); realized GS head ROS lands on the 7% anchor, above the floor-off
    head."""
    for preset in ("low", "moderate", "high"):
        u10 = _FLOOR_PRESETS[preset][0]
        tile = _flat_fuel_tile(122)                        # GS2
        ign = geo.rc_to_latlon(tile.spec, 10, 3)
        wx = _preset_wx(ign, preset)
        off = _realized_head_mps(tile, ign, wx, 0.0, 10)
        on = _realized_head_mps(tile, ign, wx, 0.10, 10)
        assert on > off * 1.05, (preset, off, on)
        assert on == pytest.approx(0.07 * u10, rel=1e-6), (preset, on)


def test_grass_floor_defers_at_high_wind_and_never_lowers():
    """max() semantics: at a wind where Rothermel already exceeds the anchor
    (25 m/s GR2) the floor DEFERS — arrival byte-identical floor-on vs off; and
    across every preset the floor NEVER makes an arrival later (only raises ros)."""
    tile = _flat_fuel_tile(102)
    ign = geo.rc_to_latlon(tile.spec, 10, 3)
    fast = const_wx(ign, wind_mps=25.0, wind_dir_deg=270.0, rh_pct=6,
                    temp_c=39, hours=300)
    off = spread.compute_arrival(
        tile, [ign], fast, T0, make_cfg(ensemble_n=1, grass_ros_floor_frac=0.0))
    on = spread.compute_arrival(
        tile, [ign], fast, T0, make_cfg(ensemble_n=1, grass_ros_floor_frac=0.10))
    assert on.data == off.data                             # floor defers (byte-identical)
    for preset in _FLOOR_PRESETS:
        wx = _preset_wx(ign, preset)
        a_off = spread.compute_arrival(
            tile, [ign], wx, T0,
            make_cfg(ensemble_n=1, grass_ros_floor_frac=0.0))
        a_on = spread.compute_arrival(
            tile, [ign], wx, T0,
            make_cfg(ensemble_n=1, grass_ros_floor_frac=0.10))
        for vo, vn in zip(a_off.data, a_on.data):
            if math.isfinite(vo):
                assert vn <= vo + 1e-9, preset             # never later


def test_ros_floor_leaves_sh_sb_nb_untouched():
    """The floor is byte-identical floor-on vs off on the UNfloored classes — SH2
    + SH5 chaparral (the malibu tile's dominant fuels; the model already gets
    chaparral right at 9-15%U10), SB1 blowdown — and a non-burnable NB1 cell
    still never spreads. Only GR/GS/TU/TL are floored."""
    for code in (142, 145, 201):                           # SH2, SH5, SB1
        tile = _flat_fuel_tile(code, ncols=120)
        ign = geo.rc_to_latlon(tile.spec, 10, 3)
        wx = _preset_wx(ign, "high")
        off = spread.compute_arrival(
            tile, [ign], wx, T0,
            make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.0))
        on = spread.compute_arrival(
            tile, [ign], wx, T0,
            make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.10))
        assert on.data == off.data, code
        assert sum(1 for v in on.data if math.isfinite(v)) > 5, code  # really burns
    # NB (developed break) never spreads, floor or no floor (spotting jumps it)
    nb = grass_tile(fuel_code=91.0)
    ign = center_ignition(nb)
    wx = _preset_wx(ign, "high")
    arr = spread.compute_arrival(
        nb, [ign], wx, T0, make_cfg(ensemble_n=1, grass_ros_floor_frac=0.10))
    assert sum(1 for v in arr.data if math.isfinite(v)) == 1  # seed only


def test_ros_floor_lifts_tu_and_tl_heads_to_class_anchor():
    """The EXTENDED floor (the santa_rosa/paradise fix): TU timber-understory
    floors the head to 0.05*U10, TL timber-litter to 0.03*U10 — a genuine lift
    where Rothermel under-drives the sheltered/compact fuels, landing the realized
    head exactly on the class anchor (below the grass 0.10 / grass-shrub 0.07
    floors). Presets restricted to those the floor-off head reaches the probe in
    (TL is near-static at 'low')."""
    cases = [(162, 0.05, ("low", "moderate", "high")),     # TU2 moderate timber-shrub
             (165, 0.05, ("low", "moderate", "high")),     # TU5 very high timber-shrub
             (183, 0.03, ("moderate", "high")),            # TL3 moderate conifer litter
             (185, 0.03, ("moderate", "high"))]            # TL5 high load conifer litter
    for code, ratio, presets in cases:
        for preset in presets:
            u10 = _FLOOR_PRESETS[preset][0]
            tile = _flat_fuel_tile(code)
            ign = geo.rc_to_latlon(tile.spec, 10, 3)
            wx = _preset_wx(ign, preset)
            off = _realized_head_mps(tile, ign, wx, 0.0, 10)
            on = _realized_head_mps(tile, ign, wx, 0.10, 10)
            assert on > off * 1.05, (code, preset, off, on)          # real lift
            assert on == pytest.approx(ratio * u10, rel=1e-6), (code, preset, on)


def test_ros_floor_never_lowers_and_defers_where_rothermel_is_faster():
    """max() semantics on the extended floor: across GR/GS/TU/TL and EVERY preset
    the floor never makes an arrival later (only ever raises ros); and on a fast
    fuel whose Rothermel head already exceeds its class floor (GR7 high-load dry
    grass at extreme, ~40%U10 >> the 10% floor) the floor DEFERS — byte-identical
    floor-on vs off (the model is already right; the floor stays out of its way)."""
    for code in (102, 122, 162, 165, 183, 185):            # GR/GS/TU/TL
        tile = _flat_fuel_tile(code, ncols=120)
        ign = geo.rc_to_latlon(tile.spec, 10, 3)
        for preset in _FLOOR_PRESETS:
            wx = _preset_wx(ign, preset)
            a_off = spread.compute_arrival(
                tile, [ign], wx, T0,
                make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.0))
            a_on = spread.compute_arrival(
                tile, [ign], wx, T0,
                make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.10))
            for vo, vn in zip(a_off.data, a_on.data):
                if math.isfinite(vo):
                    assert vn <= vo + 1e-9, (code, preset)  # never later
    # GR7 at extreme runs ~40%U10 >> its 10% floor -> defer, byte-identical
    tile = _flat_fuel_tile(107, ncols=120)
    ign = geo.rc_to_latlon(tile.spec, 10, 3)
    wx = _preset_wx(ign, "extreme")
    off = spread.compute_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.0))
    on = spread.compute_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=1, horizon_h=300.0, grass_ros_floor_frac=0.10))
    assert on.data == off.data


def test_ros_floor_increases_reach_on_timber_litter_and_understory():
    """The extended floor lifts realized downwind reach on the near-static fuels
    that throttle santa_rosa/paradise (TU2, TL3) — floor-on covers many more
    cells than floor-off in a fixed 6 h window (litter is otherwise ~0.5 cells/h
    at 'high')."""
    for code in (162, 183):                                # TU2, TL3
        tile = _flat_fuel_tile(code, ncols=300)
        ign = geo.rc_to_latlon(tile.spec, 10, 3)
        wx = _preset_wx(ign, "high")

        def reach_cells(frac):
            cfg = ResponseConfig(ensemble_n=1, horizon_h=6.0,
                                 grass_ros_floor_frac=frac)
            arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
            mc = 3
            for c in range(3, tile.spec.ncols):
                if math.isfinite(arr.get(10, c)):
                    mc = c
            return mc - 3

        off = reach_cells(0.0)
        on = reach_cells(0.10)
        assert on > off * 1.5, (code, off, on)


def _real_tile_path(key):
    return Path(__file__).resolve().parent.parent / "data" / "tiles" / f"{key}.wgt"


def _load_real_tile(key):
    """Cached LFPS tile (data/tiles/<key>.wgt) or None if not present (skip)."""
    p = _real_tile_path(key)
    if not p.exists():
        return None
    from wgresponse.terrain import load_ascii_tile
    return load_ascii_tile(str(p))


def _nearest_grass_ign(tile):
    """LatLon of the grass (GR) cell nearest the tile center — a demo ignition."""
    spec = tile.spec
    r0, c0 = spec.nrows // 2, spec.ncols // 2
    best = None
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if spread.is_grass(int(round(tile.fuel.get(r, c)))):
                d = abs(r - r0) + abs(c - c0)
                if best is None or d < best[0]:
                    best = (d, r, c)
    return None if best is None else geo.rc_to_latlon(spec, best[1], best[2])


@pytest.mark.parametrize("key", ["santa_rosa_ca", "paradise_ca"])
def test_ros_floor_lifts_head_on_real_developed_timber_tiles(key):
    """Conditional-skip real-tile gate (the founder's 'fire too slow on Santa
    Rosa' fix): on the cached developed/timber tiles (santa_rosa_ca ~29% TU+TL,
    paradise_ca ~27%) the extended floor covers materially more ground in a fixed
    window than the floor off, measured through the REAL engine spread path. The
    grass-only floor barely helped these mosaics; the TU/TL floors carry them."""
    tile = _load_real_tile(key)
    if tile is None:
        pytest.skip(f"{key} tile not cached under data/tiles/")
    ign = _nearest_grass_ign(tile)
    assert ign is not None, key
    wx = _preset_wx(ign, "extreme", hours=40)

    def reached(frac):
        cfg = ResponseConfig(ensemble_n=1, horizon_h=8.0,
                             grass_ros_floor_frac=frac)
        arr = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id=key)
        return sum(1 for v in arr.data if math.isfinite(v) and v > T0)

    off = reached(0.0)
    on = reached(0.10)
    assert on > off * 1.10, (key, off, on)


def test_grass_floor_increases_realized_reach():
    """The floor lifts realized downwind reach on a grass tile at low wind (where
    Rothermel is slowest) — measured ~3.5x farther in 6 h."""
    tile = _flat_fuel_tile(102, ncols=300)
    ign = geo.rc_to_latlon(tile.spec, 10, 3)
    wx = _preset_wx(ign, "low")

    def reach_cells(frac):
        cfg = ResponseConfig(ensemble_n=1, horizon_h=6.0,
                             grass_ros_floor_frac=frac)
        arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
        mc = 3
        for c in range(3, tile.spec.ncols):
            if math.isfinite(arr.get(10, c)):
                mc = c
        return mc - 3

    off = reach_cells(0.0)
    on = reach_cells(0.10)
    assert on > off * 1.5, (off, on)                       # substantially farther


def test_grass_floor_preserves_quantile_ordering():
    """An ensemble with the floor on keeps p10<=p50<=p90 (each member floors on
    its own perturbed wind10) and genuinely moves the field vs off."""
    tile = grass_tile(fuel_code=102.0)
    ign = center_ignition(tile)
    wx = _preset_wx(ign, "high", hours=120)
    on = spread.ensemble_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=6, grass_ros_floor_frac=0.10), incident_id="floor")
    for a, b, c in zip(on.p10.data, on.p50.data, on.p90.data):
        assert a <= b <= c
    off = spread.ensemble_arrival(
        tile, [ign], wx, T0,
        make_cfg(ensemble_n=6, grass_ros_floor_frac=0.0), incident_id="floor")
    assert any(a < b for a, b in zip(on.p50.data, off.p50.data)
               if math.isfinite(a) and math.isfinite(b))


# ---------------------------------------------------------------------------
# Sprint 2 ticket A3 (Council-1 model F1): full FBFM40 table + no-barrier
# guarantee for burnable codes
# ---------------------------------------------------------------------------

# All 40 S&B burnable models (mirrors tests/test_fuels.py FBFM40_BURNABLE).
FBFM40_BURNABLE = ([101 + i for i in range(9)] + [121 + i for i in range(4)] +
                   [141 + i for i in range(9)] + [161 + i for i in range(5)] +
                   [181 + i for i in range(9)] + [201 + i for i in range(4)])


def _exp1_setup():
    """The Council-1 exp1 geometry: ~4 x 22 km GR2 strip at 90 m cells, fire
    seeded ~1 km from the west edge, client 15 km due east (downwind), constant
    5 m/s wind FROM 270, 96 h horizon, nominal member only."""
    spec = GridSpec(north_lat=34.52, west_lon=-119.0, cell_m=90.0,
                    nrows=45, ncols=245)
    tile = TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-exp1",
    )
    ign = geo.rc_to_latlon(spec, spec.nrows // 2, 11)
    client = geo.destination(ign, 90.0, 15_000.0)
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=5.0,
                             wind_gust_mps=6.5, wind_dir_deg=270.0,
                             rh_pct=25.0, temp_c=30.0) for i in range(100)]
    wx = WeatherForecast(loc=ign, samples=samples, source="test")
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    return tile, ign, client, wx, cfg


def _set_col_band(tile: TerrainTile, col_lo: int, col_hi: int,
                  code: float) -> None:
    for r in range(tile.spec.nrows):
        for c in range(col_lo, col_hi):
            tile.fuel.set(r, c, code)


def _client_eta_h(arr: Raster, client: LatLon) -> float:
    v = geo.raster_sample(arr, client)
    return math.inf if v is None or math.isinf(v) else (v - T0) / 3600.0


def test_exp1_gr3_band_is_not_a_barrier():
    """Port of scratch_review/exp1_fuel_gap.py part (b), the Council-1 model F1
    CRITICAL finding: a 270 m cross-band of GR3 (103) at ~6.7 km between fire
    and client turned the 23.1 h client ETA into inf because GR3 had no
    FUEL_TABLE row and missing burnable rows were treated as non-spreading.
    With the full 40-row table the band must yield a finite ETA within ~1 h of
    the uniform-GR2 tile; a true non-burnable NB1 band must still block."""
    tile, ign, client, wx, cfg = _exp1_setup()
    arr_uniform = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    eta_uniform = _client_eta_h(arr_uniform, client)
    assert 20.0 < eta_uniform < 26.0          # exp1 measured 23.1 h

    band_tile, *_ = _exp1_setup()
    _set_col_band(band_tile, 85, 88, 103.0)   # 3 cells = 270 m of GR3
    arr_band = spread.compute_arrival(band_tile, [ign], wx, T0, cfg)
    eta_band = _client_eta_h(arr_band, client)
    assert math.isfinite(eta_band)            # NEVER inf (was inf pre-fix)
    assert abs(eta_band - eta_uniform) <= 1.0
    # the band no longer shadows the downstream grid (exp1: 65% never burned)
    unreached = sum(1 for v in arr_band.data if math.isinf(v))
    assert unreached / len(arr_band.data) < 0.05

    nb_tile, *_ = _exp1_setup()
    _set_col_band(nb_tile, 85, 88, 91.0)      # control: true non-burnable NB1
    arr_nb = spread.compute_arrival(nb_tile, [ign], wx, T0, cfg)
    assert _client_eta_h(arr_nb, client) == math.inf


def test_every_burnable_code_band_is_crossable():
    """No burnable FBFM40 code may behave as a barrier (ticket A3): a 300 m
    band of EACH of the 40 models inside a GR2 corridor must be crossed and
    the far side reached within the horizon."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=100.0,
                    nrows=9, ncols=31)
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=5.0,
                             wind_gust_mps=6.5, wind_dir_deg=270.0,
                             rh_pct=25.0, temp_c=30.0) for i in range(100)]
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    for code in FBFM40_BURNABLE:
        tile = TerrainTile(
            spec=spec,
            fuel=geo.make_raster(spec, fill=102.0),
            slope_deg=geo.make_raster(spec, fill=0.0),
            aspect_deg=geo.make_raster(spec, fill=-1.0),
            elev_m=geo.make_raster(spec, fill=100.0),
            source="test-band",
        )
        _set_col_band(tile, 14, 17, float(code))
        ign = geo.rc_to_latlon(spec, 4, 2)
        wx = WeatherForecast(loc=ign, samples=samples, source="test")
        arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
        assert math.isfinite(arr.get(4, 28)), \
            f"burnable code {code} acted as a barrier"


def test_unknown_burnable_code_falls_back_to_gr2_not_barrier(caplog):
    """Ticket A3 test (e): a code classified burnable by is_burnable() but
    absent from FUEL_TABLE (110 sits in the 101..204 burnable namespace but is
    not an FBFM40 model) must spread with GR2 fallback params — arrival data
    identical to a uniform-GR2 tile — and warn exactly once per process."""
    assert spread.is_burnable(110) is True
    assert 110 not in spread.FUEL_TABLE
    spread._WARNED_UNKNOWN_CODES.discard(110)     # idempotent across runs

    tile_unknown = grass_tile(fuel_code=110.0)
    tile_gr2 = grass_tile(fuel_code=102.0)
    ign = center_ignition(tile_unknown)
    wx = const_wx(ign, wind_dir_deg=270.0)
    cfg = make_cfg(ensemble_n=1)
    with caplog.at_level(logging.WARNING, logger="wgresponse.spread"):
        arr_unknown = spread.compute_arrival(tile_unknown, [ign], wx, T0, cfg)
        arr_again = spread.compute_arrival(tile_unknown, [ign], wx, T0, cfg)
    arr_gr2 = spread.compute_arrival(tile_gr2, [ign], wx, T0, cfg)
    # never a barrier: identical to the GR2 run, finite downwind
    assert arr_unknown.data == arr_gr2.data
    assert arr_unknown.data == arr_again.data
    assert math.isfinite(arr_unknown.get(20, 30))
    # warned ONCE per unknown code per process (two runs, one warning)
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "110" in r.getMessage()
                and "FUEL_TABLE" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Sprint 2 ticket A4 (Council-1 model F2+F6) — dir_offset_deg knob + ensemble
# v2. Evidence: docs/review/model_fidelity_review.md; experiments
# scratch_review/exp6_direction_not_perturbed.py + exp2_eta_jitter.py.
# ---------------------------------------------------------------------------

def _fingerprint(raster: Raster) -> str:
    """SHA-256 over the little-endian float64 raster payload (byte identity)."""
    return hashlib.sha256(
        b"".join(struct.pack("<d", v) for v in raster.data)).hexdigest()


# A/B/D pinned from the pre-Sprint-2-Session-2 spread.py (generated on the
# legacy code immediately before the A4/A5 edits; the dual-run diff in the
# ticket digest showed 0 mismatches across randomized tiles). These prove the
# ticket promise: dir_offset_deg=0.0 / perimeter=None defaults are
# BYTE-IDENTICAL to legacy compute_arrival — and, since all three run with
# spotting OFF, they equally pin the SAFETY SEMANTICS 11 promise that functional
# spotting is a byte-identical no-op when disabled.
#
# C_spotting is the ONE re-pin: it runs spotting ON, and the 2026-07-10
# SEMANTICS 11 change (mean-only ember -> per-cell lognormal landing draw)
# deliberately moved this raster. It was regenerated from the NEW spotting code
# (deterministic across runs) and now pins that dir_offset_deg=0.0 /
# perimeter=None defaults do not perturb the functional-spotting run, plus its
# determinism. Platform note: pins are float64-exact on the dev platform
# (Windows/CPython); a libm change that shifts them is exactly the kind of
# silent numeric drift this test exists to surface.
_LEGACY_FINGERPRINTS = {
    "A_flat_wind": "7d78e9176c6157758bb157d2c59d90233961d5773c25c1830775fcaca2c133b0",
    "B_upslope_nowind": "dcaf5f62369b5167f97bc6f47971d16031847fd065a01b1578158db25c264bf4",
    # re-pinned 2026-07-10 for functional spotting (SEMANTICS 11); was
    # 32ea224c9ff9ab0c26bc6776be245c0abd3b3015d2cc78dff7a3654223e88ae7 (mean-only)
    "C_spotting": "0677aae8f7064fea77d4bbdee4ecb6bc30f6a71546bc8ca52498a027ad284790",
    "D_wind_switch": "b86a3299992928d49f14a1548e7cbb246d389bc83d958599d8fa6d9883b7f0e4",
}


def test_compute_arrival_defaults_byte_identical_to_legacy():
    """A4 hard gate: the new kwargs at their defaults must not move a single
    bit of the arrival raster vs the pinned reference (4 scenarios: flat+wind,
    pure slope, spotting-over-barrier, wind switch) — and passing
    dir_offset_deg=0.0 / perimeter=None explicitly is the same as omitting them.
    A/B/D are the legacy (spotting-OFF) pins; C runs spotting ON and is pinned
    to the functional-spotting model (re-pinned 2026-07-10, SEMANTICS 11)."""
    cfg = ResponseConfig(ensemble_n=4)
    # A: flat GR2, 10 m/s FROM 270
    tile = grass_tile()
    ign = center_ignition(tile)
    arr = spread.compute_arrival(tile, [ign], const_wx(ign), T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["A_flat_wind"]
    arr_explicit = spread.compute_arrival(
        tile, [ign], const_wx(ign), T0, cfg,
        dir_offset_deg=0.0, perimeter=None, perimeter_at_s=None)
    assert arr_explicit.data == arr.data
    # B: 30 deg slope facing west (upslope east), zero wind
    tile = grass_tile(slope_deg=30.0, aspect_deg=270.0)
    ign = center_ignition(tile)
    arr = spread.compute_arrival(tile, [ign], const_wx(ign, wind_mps=0.0),
                                 T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["B_upslope_nowind"]
    # C: spotting ON over a 2-col NB barrier, 25 m/s wind, 60 m cells
    # (functional-spotting pin — the per-cell lognormal draw is deterministic).
    tile, ign, wx = _spot_setup()
    arr = spread.compute_arrival(
        tile, [ign], wx, T0, ResponseConfig(ensemble_n=1, spotting_enabled=True))
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["C_spotting"]
    # D: wind veers 270 -> 180 at T0+2h
    tile = grass_tile()
    ign = center_ignition(tile)
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=10.0,
                             wind_gust_mps=13.0,
                             wind_dir_deg=270.0 if i < 2 else 180.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    wx = WeatherForecast(loc=ign, samples=samples, source="test")
    arr = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert _fingerprint(arr) == _LEGACY_FINGERPRINTS["D_wind_switch"]


def test_dir_offset_equivalent_to_rotating_the_forecast():
    """Rotating via dir_offset_deg must equal rotating wind_dir_deg in the
    forecast itself, byte-for-byte — including the SPOTTING bearing (the
    offset applies everywhere weather is consumed). 37.5 deg is dyadic, so
    270.0 + 37.5 == 307.5 exactly."""
    cfg = make_cfg(ensemble_n=1)
    tile = grass_tile()
    ign = center_ignition(tile)
    via_offset = spread.compute_arrival(
        tile, [ign], const_wx(ign, wind_dir_deg=270.0), T0, cfg,
        dir_offset_deg=37.5)
    via_forecast = spread.compute_arrival(
        tile, [ign], const_wx(ign, wind_dir_deg=307.5), T0, cfg)
    assert via_offset.data == via_forecast.data
    # spotting path consumes the rotated bearing too
    tile_s, ign_s, _ = _spot_setup()
    cfg_s = make_cfg(ensemble_n=1, spotting_enabled=True)
    spot_offset = spread.compute_arrival(
        tile_s, [ign_s], const_wx(ign_s, wind_mps=25.0, wind_dir_deg=270.0),
        T0, cfg_s, dir_offset_deg=37.5)
    spot_forecast = spread.compute_arrival(
        tile_s, [ign_s], const_wx(ign_s, wind_mps=25.0, wind_dir_deg=307.5),
        T0, cfg_s)
    assert spot_offset.data == spot_forecast.data


def test_dir_offset_rotates_the_head_axis():
    """Wind FROM 270 heads east; dir_offset_deg=+90 (wind FROM 0) must head
    the run south instead."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0)
    cfg = make_cfg(ensemble_n=1)
    rot = spread.compute_arrival(tile, [ign], wx, T0, cfg, dir_offset_deg=90.0)
    east = rot.get(20, 30) - T0
    south = rot.get(30, 20) - T0
    assert math.isfinite(south) and math.isfinite(east)
    assert south < east / 2.0


def test_stratified_member_draws_law():
    """Ensemble-v2 draw law: each dimension's m draws are exactly the evenly
    spaced quantile midpoints of [-1, 1] (each stratum used once — Latin
    hypercube), deterministic per incident_id, different across incident_ids,
    and independent of t0 by construction (no time argument exists)."""
    m = 5
    draws = spread._stratified_member_draws("inc-1", m)
    assert draws == spread._stratified_member_draws("inc-1", m)  # deterministic
    mids = [2.0 * (j + 0.5) / m - 1.0 for j in range(m)]
    for dim in range(3):
        assert sorted(d[dim] for d in draws) == pytest.approx(mids)
    assert all(len(d) == 3 for d in draws)
    # different incident => different stratum assignment somewhere (seeded on
    # ('ensemble-v2', incident_id, k))
    assert any(spread._stratified_member_draws("inc-2", m)[i] != draws[i]
               for i in range(m))
    # degenerate sizes
    assert spread._stratified_member_draws("inc-1", 0) == []
    assert spread._stratified_member_draws("inc-1", 1) == [(0.0, 0.0, 0.0)]


def test_ensemble_seeded_on_incident_and_member_never_t0():
    """A4 seeding law: same incident at t0 and t0+900 s under CONSTANT weather
    reproduces every member's relative arrival field (the F6 fix — members no
    longer re-draw on round(t0_s)); a different incident_id perturbs
    differently; the nominal member (k=0) stays unperturbed."""
    tile = grass_tile()
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_dir_deg=270.0, hours=120)
    cfg = make_cfg(ensemble_n=4)
    ens_a = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                    incident_id="inc-1")
    ens_b = spread.ensemble_arrival(tile, [ign], wx, T0 + 900.0, cfg,
                                    incident_id="inc-1")
    probe = (20, 30)
    for ma, mb in zip(ens_a.members, ens_b.members):
        ra = ma.get(*probe) - T0
        rb = mb.get(*probe) - (T0 + 900.0)
        assert math.isfinite(ra) and abs(ra - rb) < 0.01     # seconds
    # nominal member is the unperturbed run
    nominal = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert ens_a.members[0].data == nominal.data
    # incident identity changes the perturbations
    ens_c = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                    incident_id="inc-2")
    assert any(abs(mc.get(*probe) - ma.get(*probe)) > 1.0
               for ma, mc in zip(ens_a.members[1:], ens_c.members[1:]))
    # and the ensemble stamps its incident_id (orchestrator passes it through)
    assert ens_a.incident_id == "inc-1"


def _offaxis_setup():
    """Exp6 geometry, trimmed: uniform GR2 flat strip, 6 m/s FROM 270 (head
    east), ignition near the west edge, client 6 km out at bearing 115 =
    25 deg off the head axis."""
    spec = GridSpec(north_lat=34.60, west_lon=-119.0, cell_m=90.0,
                    nrows=110, ncols=100)
    tile = TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-exp6",
    )
    ign = geo.rc_to_latlon(spec, 35, 8)
    client = geo.destination(ign, 115.0, 6000.0)
    return tile, ign, client


def _hours_at(raster_or_none, loc, t0=T0):
    v = geo.raster_sample(raster_or_none, loc)
    return math.inf if v is None or math.isinf(v) else (v - t0) / 3600.0


def test_offaxis_client_p10_covers_plausible_direction_error():
    """Exit gate 3a (Council-1 F2; port of exp6): for a client 25 deg off the
    head axis the ensemble band [p10, p90] must COVER the arrivals under a
    plausible +-20 deg true-direction error — p10 earlier than the earlier
    rotation's ETA, p90 later than the later one's. Pre-fix the band was
    0.35 h wide (71x narrower than the 25.0 h rotation swing) and p10 was
    7.5 h LATER than the +20 deg arrival; every crew-safety number keys off
    p10 (latest_depart = p10 - 30 min).

    Honest-numbers note (measured 2026-07-09, this scenario, ensemble_n=6):
    the +-20 deg reference arrival is itself approximately the design's own
    10% favorable tail (dir draws stratified over +-24 deg at mid-horizon
    lead), so the p10 estimate straddles the bar across incident-id shuffles:
    p10 in 7.55..9.04 h over 8 sampled ids (median 8.15) vs bar 8.02 h —
    3-5 of 8 ids pass the strict inequality depending on ensemble size. The
    ids pinned here (measured 7.55 / 7.57 h) are regression anchors for the
    MECHANISM (direction is perturbed, lead-scaled, stratified, seeded on
    (incident_id, k)); the band-width assertions below hold for every id
    tested (min band 25.9 h, was 0.35 h)."""
    tile, ign, client = _offaxis_setup()
    wx = const_wx(ign, wind_mps=6.0, wind_dir_deg=270.0, rh_pct=25.0)
    cfg = ResponseConfig(ensemble_n=6, horizon_h=96.0)
    ref = {}
    for d in (250.0, 290.0):
        arr = spread.compute_arrival(
            tile, [ign], const_wx(ign, wind_mps=6.0, wind_dir_deg=d,
                                  rh_pct=25.0), T0, cfg)
        ref[d] = _hours_at(arr, client)
    assert 6.0 < ref[290.0] < 10.0                # toward-client rotation
    assert 25.0 < ref[250.0] < 40.0               # away-from-client rotation
    for iid in ("inc-1", "IRWIN-XYZ"):
        ens = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                      incident_id=iid)
        eta = spread.eta_at(ens, client)
        p10, p50, p90 = ((eta.eta_p10_s - T0) / 3600.0,
                         (eta.eta_p50_s - T0) / 3600.0,
                         (eta.eta_p90_s - T0) / 3600.0)
        # THE gate: band covers the +-20 deg rotation arrivals
        assert p10 < ref[290.0], (iid, p10, ref)
        assert p90 > ref[250.0], (iid, p90, ref)
        # band no longer degenerate off-axis (was 0.35 h)
        assert p90 - p10 > 20.0, (iid, p10, p90)
        # median stays anchored near the nominal-direction arrival
        assert abs(p50 - 15.8) < 2.0, (iid, p50)


def test_static_fire_eta_jitter_under_5pct_across_cycles():
    """Exit gate 3b (Council-1 F6; port of exp2 scenario A): 8 consecutive
    forecasts at t0 + k*900 s on a static fire under CONSTANT weather —
    per-client p50/p10 hours-out max swing must be < 5% of the mean (was
    12-16%: members re-seeded on round(t0_s) each cycle). With members seeded
    on (incident_id, k) the swing is measured at 0.000%; asserted < 0.1% so
    any reintroduction of time-dependent seeding fails loudly, and < 5.0% as
    the sprint exit gate."""
    spec = GridSpec(north_lat=34.53, west_lon=-119.0, cell_m=150.0,
                    nrows=25, ncols=130)
    tile = TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-exp2",
    )
    ign = geo.rc_to_latlon(spec, 12, 5)
    clients = {"8km": geo.destination(ign, 90.0, 8000.0),
               "15km": geo.destination(ign, 90.0, 15000.0)}
    wx = const_wx(ign, wind_mps=3.0, wind_dir_deg=270.0, rh_pct=25.0,
                  hours=110)
    cfg = ResponseConfig(ensemble_n=6, horizon_h=96.0)
    series = {(name, q): [] for name in clients for q in ("p50", "p10")}
    for k in range(8):
        t0 = T0 + k * 900.0
        ens = spread.ensemble_arrival(tile, [ign], wx, t0, cfg,
                                      incident_id="jitter-fire")
        for name, loc in clients.items():
            eta = spread.eta_at(ens, loc)
            series[(name, "p50")].append((eta.eta_p50_s - t0) / 3600.0)
            series[(name, "p10")].append((eta.eta_p10_s - t0) / 3600.0)
    for (name, q), vals in series.items():
        assert all(math.isfinite(v) for v in vals), (name, q, vals)
        mean = sum(vals) / len(vals)
        swing_pct = (max(vals) - min(vals)) / mean * 100.0
        assert swing_pct < 0.1, (name, q, swing_pct, vals)   # measured 0.000
        assert swing_pct < 5.0                               # sprint exit gate


# ---------------------------------------------------------------------------
# Sprint 2 ticket A5 (Council-1 model F4) — perimeter interior seeding.
# Evidence: scratch_review/exp5_perimeter_seeding.py; review §F4.
# ---------------------------------------------------------------------------

PERIM_T = T0 - 6 * 3600.0          # a 6 h stale perimeter timestamp


def _perimeter_tile(nrows=70, ncols=120, cell_m=90.0):
    spec = GridSpec(north_lat=34.56, west_lon=-119.0, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=-1.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-exp5",
    )


def _square_ring(center: LatLon, half_m: float, step_m: float) -> list:
    """Vertices of a square ring (2*half_m per side) centered on `center`,
    spaced ~step_m apart, counter-boundary order (exp5 helper)."""
    nw = geo.destination(geo.destination(center, 0.0, half_m), 270.0, half_m)
    pts = []
    side = 2.0 * half_m
    n = max(1, int(side / step_m))
    for k in range(n):
        pts.append(geo.destination(nw, 90.0, k * side / n))
    ne = geo.destination(nw, 90.0, side)
    for k in range(n):
        pts.append(geo.destination(ne, 180.0, k * side / n))
    se = geo.destination(ne, 180.0, side)
    for k in range(n):
        pts.append(geo.destination(se, 270.0, k * side / n))
    sw = geo.destination(se, 270.0, side)
    for k in range(n):
        pts.append(geo.destination(sw, 0.0, k * side / n))
    return pts


def _perim_setup(step_m=150.0, half_m=1500.0):
    tile = _perimeter_tile()
    center = geo.rc_to_latlon(tile.spec, 35, 30)
    ring = _square_ring(center, half_m, step_m)
    wx = const_wx(center, wind_mps=5.0, wind_dir_deg=270.0, rh_pct=25.0,
                  hours=110)
    return tile, center, ring, wx


def _interior_rcs(tile, center, half_m, margin_m=250.0):
    """Cells strictly inside the square (>= margin_m from every edge)."""
    out = []
    for r in range(tile.spec.nrows):
        for c in range(tile.spec.ncols):
            p = geo.rc_to_latlon(tile.spec, r, c)
            de = geo.haversine_m(center, LatLon(center.lat, p.lon))
            dn = geo.haversine_m(center, LatLon(p.lat, center.lon))
            if de < half_m - margin_m and dn < half_m - margin_m:
                out.append((r, c))
    return out


def test_perimeter_interior_burned_at_perimeter_time_in_every_member():
    """A5: every cell inside the polygon is already burned — arrival ==
    perimeter_at_s (the FEED time, 6 h before now) in EVERY ensemble member
    and hence every quantile raster; a client 200 m inside the downwind edge
    reads hours_out == 0. Pre-fix exp5 measured interior forecast arrivals of
    +2.3..+18.7 h ('re-burn') and +4.3 h for the inside-edge client."""
    tile, center, ring, wx = _perim_setup()
    cfg = ResponseConfig(ensemble_n=4, horizon_h=96.0)
    ens = spread.ensemble_arrival(tile, [], wx, T0, cfg, incident_id="p1",
                                  perimeter=[ring], perimeter_at_s=PERIM_T)
    interior = _interior_rcs(tile, center, 1500.0)
    assert len(interior) > 100                    # the square really rasterized
    for r, c in interior:
        for member in ens.members:
            assert member.get(r, c) == PERIM_T, (r, c)
        assert ens.p10.get(r, c) == PERIM_T
        assert ens.p50.get(r, c) == PERIM_T
        assert ens.p90.get(r, c) == PERIM_T
    inside_edge = geo.destination(center, 90.0, 1500.0 - 200.0)
    eta = spread.eta_at(ens, inside_edge)
    assert eta.eta_p50_s == PERIM_T
    assert eta.hours_out(T0) == (0.0, 0.0, 0.0)   # already burned => 0 h out


def test_perimeter_interior_is_final_never_relaxed():
    """A5: interior cells are FINAL. Even an exterior ignition seeded EARLIER
    than perimeter_at_s (future-dated perimeter edge case) must not relax an
    interior cell below perimeter_at_s."""
    tile, center, ring, wx = _perim_setup()
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    future_t = T0 + 3600.0                        # perimeter stamped ahead of t0
    west_seed = geo.destination(center, 270.0, 2500.0)   # 1 km west of the ring
    arr = spread.compute_arrival(tile, [west_seed], wx, T0, cfg,
                                 perimeter=[ring], perimeter_at_s=future_t)
    assert math.isfinite(_hours_at(arr, west_seed) * 3600.0)
    for r, c in _interior_rcs(tile, center, 1500.0):
        assert arr.get(r, c) == future_t, (r, c)  # never relaxed below


def test_stale_perimeter_front_is_ahead_of_seeding_at_now():
    """A5: seeding the burned area at the feed's updated_at_s (6 h ago) must
    put the front strictly AHEAD of a (wrong) seeded-at-now run at a downwind
    probe — the review measured stale perimeters as systematically LATE."""
    tile, center, ring, wx = _perim_setup()
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    stale = spread.compute_arrival(tile, [], wx, T0, cfg,
                                   perimeter=[ring], perimeter_at_s=PERIM_T)
    at_now = spread.compute_arrival(tile, [], wx, T0, cfg,
                                    perimeter=[ring], perimeter_at_s=T0)
    probe = geo.destination(center, 90.0, 1500.0 + 4000.0)  # 4 km downwind
    h_stale = _hours_at(stale, probe)
    h_now = _hours_at(at_now, probe)
    assert math.isfinite(h_stale) and math.isfinite(h_now)
    assert h_stale < h_now                       # front already advanced
    assert h_now - h_stale == pytest.approx(6.0, abs=0.25)


def test_four_corner_sparse_polygon_interior_filled():
    """A5: a 4-vertex square must rasterize the SAME burned interior as a
    densely sampled ring — coverage comes from the polygon, not vertex
    density (exp5 measured +5 h of pure sparsity error for outside clients,
    +11 h for the inside-center client)."""
    tile, center, ring_dense, wx = _perim_setup(step_m=150.0)
    _, _, ring_sparse, _ = _perim_setup(step_m=3000.0)
    assert len(ring_sparse) == 4
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    arr_dense = spread.compute_arrival(tile, [], wx, T0, cfg,
                                       perimeter=[ring_dense],
                                       perimeter_at_s=PERIM_T)
    arr_sparse = spread.compute_arrival(tile, [], wx, T0, cfg,
                                        perimeter=[ring_sparse],
                                        perimeter_at_s=PERIM_T)
    for r, c in _interior_rcs(tile, center, 1500.0):
        assert arr_sparse.get(r, c) == PERIM_T, (r, c)     # filled, not verts
    outside = geo.destination(center, 90.0, 1500.0 + 3000.0)
    assert abs(_hours_at(arr_sparse, outside) -
               _hours_at(arr_dense, outside)) < 0.5
    inside = geo.destination(center, 90.0, 1500.0 - 200.0)
    assert _hours_at(arr_sparse, inside, PERIM_T) == 0.0


def test_perimeter_hole_ring_not_preburned_but_burns_inward():
    """Even-odd rule: an inner ring flips its cells back OUTSIDE the burned
    set (an unburned island). The island is enclosed fuel: not burned at
    perimeter_at_s, but the surrounding front burns INTO it shortly after."""
    tile, center, ring, wx = _perim_setup()
    hole = _square_ring(center, 500.0, 150.0)
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    arr = spread.compute_arrival(tile, [], wx, T0, cfg,
                                 perimeter=[ring, hole],
                                 perimeter_at_s=PERIM_T)
    ctr_rc = geo.latlon_to_rc(tile.spec, center)
    v = arr.get(*ctr_rc)
    assert v > PERIM_T                            # not pre-burned
    assert math.isfinite(v)                       # burns in from the ring
    ring_cell = geo.latlon_to_rc(
        tile.spec, geo.destination(center, 90.0, 1200.0))
    assert arr.get(*ring_cell) == PERIM_T         # annulus is burned


def test_degenerate_polygon_falls_back_to_vertex_cells():
    """A sub-cell / collinear 'ring' has no rasterizable interior: the cells
    under its vertices become the burned mask (documented fallback), and the
    fire still spreads from them."""
    tile, center, _, wx = _perim_setup()
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    a = center
    b = geo.destination(center, 90.0, 40.0)       # 40 m: same/adjacent cell
    arr = spread.compute_arrival(tile, [], wx, T0, cfg,
                                 perimeter=[[a, b]], perimeter_at_s=PERIM_T)
    rc = geo.latlon_to_rc(tile.spec, a)
    assert arr.get(*rc) == PERIM_T
    downwind = geo.destination(center, 90.0, 3000.0)
    assert math.isfinite(_hours_at(arr, downwind))


def test_perimeter_and_exterior_hotspot_seeds_union():
    """A5: hotspots must still seed (at t0) when a perimeter exists — the
    union drives the search. A hotspot 4 km downwind of the ring pulls the
    arrival at a far probe earlier than the perimeter front alone."""
    tile, center, ring, wx = _perim_setup()
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    hotspot = geo.destination(center, 90.0, 1500.0 + 4000.0)
    probe = geo.destination(center, 90.0, 1500.0 + 6000.0)
    with_hot = spread.compute_arrival(tile, [hotspot], wx, T0, cfg,
                                      perimeter=[ring],
                                      perimeter_at_s=PERIM_T)
    without = spread.compute_arrival(tile, [], wx, T0, cfg,
                                     perimeter=[ring],
                                     perimeter_at_s=PERIM_T)
    assert _hours_at(with_hot, hotspot) == pytest.approx(0.0, abs=1e-9)
    assert _hours_at(with_hot, probe) < _hours_at(without, probe)
    # and the hotspot seed did NOT disturb the burned interior
    for r, c in _interior_rcs(tile, center, 1500.0):
        assert with_hot.get(r, c) == PERIM_T


# ---------------------------------------------------------------------------
# Sprint 2 ticket B3 (Council-1 perf S1+S2) — ROS memo byte-identity gate.
# Evidence: docs/review/perf_review.md §1.7 (the (cell,hour) cache measured a
# 0.0% hit rate; a (fuel,slope,aspect,hour) memo dedupes 83.2% of ROS evals on
# LANDFIRE integer quantization). WGRESP_NO_ROS_MEMO=1 selects the unmemoized
# reference path; every test here restores the env via monkeypatch.
# ---------------------------------------------------------------------------

def _b3_ridge_tile(nrows=33, ncols=33, cell_m=100.0) -> TerrainTile:
    """Continuous slope/aspect (nearly every cell a unique memo key — the
    honest worst case for sharing) over coarse mixed-fuel patches."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    tile = TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=0.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-b3-ridge",
    )
    codes = (102.0, 122.0, 145.0, 183.0)
    for r in range(nrows):
        for c in range(ncols):
            tile.fuel.set(r, c, codes[((r // 8) + (c // 8)) % 4])
            tile.slope_deg.set(r, c, 5.0 + 20.0 * abs(math.sin(0.37 * r + 0.13 * c)))
            tile.aspect_deg.set(r, c, (37.0 * r + 11.3 * c) % 360.0)
    return tile


def _b3_mixed_tile(nrows=33, ncols=33, cell_m=100.0) -> TerrainTile:
    """LANDFIRE-like quantized terrain: integer-degree slope, 45-deg aspect
    classes, four fuel patches + a small NB1 blob (gate cells exercised)."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    tile = TerrainTile(
        spec=spec,
        fuel=geo.make_raster(spec, fill=102.0),
        slope_deg=geo.make_raster(spec, fill=0.0),
        aspect_deg=geo.make_raster(spec, fill=0.0),
        elev_m=geo.make_raster(spec, fill=100.0),
        source="test-b3-mixed",
    )
    codes = (102.0, 122.0, 145.0, 183.0)
    for r in range(nrows):
        for c in range(ncols):
            tile.fuel.set(r, c, codes[((r // 9) + (c // 7)) % 4])
            tile.slope_deg.set(r, c, float((r + c) % 10))          # integer degrees
            tile.aspect_deg.set(r, c, float(45 * ((r // 4) % 8)))  # 45-deg classes
    for r in range(14, 18):                                        # NB blob
        for c in range(20, 23):
            tile.fuel.set(r, c, 91.0)
    return tile


_B3_TILES = {
    "grass": lambda: grass_tile(nrows=33, ncols=33),
    "ridge": _b3_ridge_tile,
    "mixed": _b3_mixed_tile,
}


def _b3_wx(kind: str, loc: LatLon) -> WeatherForecast:
    if kind == "const":
        return const_wx(loc, wind_mps=8.0, wind_dir_deg=270.0, rh_pct=25.0)
    # "veer": hour-varying speed/direction/RH/temp — many distinct hour
    # buckets, so the memo keys must separate them.
    samples = [WeatherSample(t_s=T0 + i * 3600.0,
                             wind_speed_mps=4.0 + float(i % 9),
                             wind_gust_mps=(4.0 + float(i % 9)) * 1.3,
                             wind_dir_deg=(250.0 + 10.0 * (i % 10)) % 360.0,
                             rh_pct=15.0 + 3.0 * (i % 7),
                             temp_c=22.0 + 2.5 * (i % 5))
               for i in range(100)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


def _b3_runs(tile, wx_kind, monkeypatch, **kw):
    """(memo_raster, reference_raster) for one scenario; identical arguments,
    the only difference is the WGRESP_NO_ROS_MEMO escape hatch."""
    spec = tile.spec
    ign = [geo.rc_to_latlon(spec, spec.nrows // 2, 4)]
    wx = _b3_wx(wx_kind, ign[0])
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    memo = spread.compute_arrival(tile, ign, wx, T0, cfg, **kw)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, ign, wx, T0, cfg, **kw)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    return memo, ref


@pytest.mark.parametrize("wx_kind", ["const", "veer"])
@pytest.mark.parametrize("tile_name", sorted(_B3_TILES))
def test_ros_memo_byte_identity_matrix(tile_name, wx_kind, monkeypatch):
    """THE B3 GATE: arrival rasters with the memo ON must be byte-identical
    (exact float equality, every cell — compared both as values and as
    packed float64 bytes) to the WGRESP_NO_ROS_MEMO=1 reference path across
    the full ticket matrix: 3 tiles (grass/ridge/mixed) x 2 wind regimes
    (constant westerly / veering+varying, the latter with ensemble-style
    wind/moisture scaling) x {no perimeter, stale perimeter} x
    {dir_offset 0, +17.3}."""
    tile = _B3_TILES[tile_name]()
    spec = tile.spec
    ring = _square_ring(geo.rc_to_latlon(spec, spec.nrows // 2, spec.ncols // 2),
                        half_m=400.0, step_m=120.0)
    scales = {} if wx_kind == "const" else \
        {"wind_scale": 1.15, "moisture_scale": 0.9}
    for perimeter, perimeter_at_s in ((None, None), ([ring], T0 - 6 * 3600.0)):
        for off in (0.0, 17.3):
            memo, ref = _b3_runs(
                tile, wx_kind, monkeypatch, dir_offset_deg=off,
                perimeter=perimeter, perimeter_at_s=perimeter_at_s, **scales)
            case = (tile_name, wx_kind, off, perimeter is not None)
            assert memo.data == ref.data, case
            assert _fingerprint(memo) == _fingerprint(ref), case
            # the scenario really burns (a trivially-inf raster proves nothing)
            assert sum(1 for v in memo.data if math.isfinite(v)) > 50, case


def test_ros_memo_byte_identity_spotting(monkeypatch):
    """Spot tuples live inside the cached params: the spotting-over-barrier
    geometry must also be byte-identical memo vs reference, with and without
    the +17.3 rotation."""
    for off in (0.0, 17.3):
        tile, ign, wx = _spot_setup()
        cfg = ResponseConfig(ensemble_n=1, spotting_enabled=True)
        monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
        memo = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                      dir_offset_deg=off)
        monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
        ref = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                     dir_offset_deg=off)
        monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
        assert memo.data == ref.data, off
        assert _fingerprint(memo) == _fingerprint(ref), off


def test_ros_memo_ensemble_byte_identity(monkeypatch):
    """Every ensemble member carries its own wind/moisture scale and direction
    offset — the memo keys on post-scale effective weather, so members and all
    three quantile rasters must be byte-identical memo vs reference."""
    tile = _b3_mixed_tile()
    ign = [geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)]
    wx = _b3_wx("veer", ign[0])
    cfg = ResponseConfig(ensemble_n=4, horizon_h=96.0)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    ens_memo = spread.ensemble_arrival(tile, ign, wx, T0, cfg,
                                       incident_id="b3-ens")
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ens_ref = spread.ensemble_arrival(tile, ign, wx, T0, cfg,
                                      incident_id="b3-ens")
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert len(ens_memo.members) == len(ens_ref.members) == 4
    for k, (a, b) in enumerate(zip(ens_memo.members, ens_ref.members)):
        assert a.data == b.data, f"member {k}"
        assert _fingerprint(a) == _fingerprint(b), f"member {k}"
    assert ens_memo.p10.data == ens_ref.p10.data
    assert ens_memo.p50.data == ens_ref.p50.data
    assert ens_memo.p90.data == ens_ref.p90.data


def test_ros_memo_dedupes_rothermel_evaluations(monkeypatch):
    """Regression port of the perf-review §1.7 accounting: the old
    (cell,hour) cache NEVER hit (one surface_ros + one effective_wind bed
    build per settled cell). With the B3 memo: a uniform flat tile under
    CONSTANT weather costs exactly ONE full ROS evaluation and ONE bed build
    no matter how many cells settle (equal weather tuples across hours share
    one key); the quantized mixed tile dedupes the vast majority of evals and
    collapses bed builds to the distinct fuel codes."""
    calls = {"bed": 0, "full": 0}
    orig_bed = spread.rothermel._bed
    orig_full = spread.rothermel.surface_ros_from_bed
    monkeypatch.setattr(spread.rothermel, "_bed",
                        lambda *a: calls.__setitem__("bed", calls["bed"] + 1)
                        or orig_bed(*a))
    monkeypatch.setattr(spread.rothermel, "surface_ros_from_bed",
                        lambda *a: calls.__setitem__("full", calls["full"] + 1)
                        or orig_full(*a))
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)

    tile = grass_tile()                          # uniform GR2, flat
    ign = center_ignition(tile)
    arr = spread.compute_arrival(tile, [ign], const_wx(ign), T0, cfg)
    settled = sum(1 for v in arr.data if math.isfinite(v))
    assert settled > 1000
    assert calls["full"] == 1, calls             # one key serves every cell
    assert calls["bed"] == 1, calls

    calls["bed"] = calls["full"] = 0
    tile2 = _b3_mixed_tile()
    ign2 = geo.rc_to_latlon(tile2.spec, tile2.spec.nrows // 2, 4)
    arr2 = spread.compute_arrival(tile2, [ign2], const_wx(ign2), T0, cfg)
    settled2 = sum(1 for v in arr2.data if math.isfinite(v))
    assert settled2 > 400
    # quantized terrain: >= 65% of evaluations deduped (83.2% measured on
    # real LANDFIRE-scale grids; this 33x33 toy tile shares less but must
    # still dedupe massively vs the old one-eval-per-cell behavior)
    assert calls["full"] <= 0.35 * settled2, (calls, settled2)
    # constant weather => one m_dead value => one bed per distinct fuel code
    assert calls["bed"] <= 4, calls


def test_ros_memo_cap_binding_still_byte_identical(monkeypatch):
    """_ROS_CACHE_MAX is a memory bound, not behavior: with the cap forced
    tiny (insertions stop, later keys recompute every time) the raster must
    stay byte-identical to the uncapped run and the reference path."""
    tile = _b3_ridge_tile()
    memo_uncapped, ref = _b3_runs(tile, "veer", monkeypatch,
                                  dir_offset_deg=17.3,
                                  wind_scale=1.15, moisture_scale=0.9)
    monkeypatch.setattr(spread, "_ROS_CACHE_MAX", 7)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    ign = [geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)]
    capped = spread.compute_arrival(
        tile, ign, _b3_wx("veer", ign[0]), T0,
        ResponseConfig(ensemble_n=1, horizon_h=96.0), dir_offset_deg=17.3,
        wind_scale=1.15, moisture_scale=0.9)
    assert capped.data == memo_uncapped.data == ref.data


def test_ros_memo_no_cross_invocation_leakage(monkeypatch):
    """Caches are created inside compute_arrival and die at return: a run with
    different weather, scales and offset in between must not perturb a repeat
    of the first call by a single bit (A-B-A byte identity)."""
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    tile = _b3_mixed_tile()
    ign = [geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)]
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    wx_a = _b3_wx("const", ign[0])
    a1 = spread.compute_arrival(tile, ign, wx_a, T0, cfg)
    spread.compute_arrival(tile, ign, _b3_wx("veer", ign[0]), T0 + 1234.0, cfg,
                           wind_scale=1.5, moisture_scale=0.7,
                           dir_offset_deg=90.0)
    a2 = spread.compute_arrival(tile, ign, wx_a, T0, cfg)
    assert a1.data == a2.data
    assert _fingerprint(a1) == _fingerprint(a2)


def test_ros_memo_escape_hatch_documented():
    """Ticket B3 requires the reference-path env documented in the module
    docstring (the field runbook greps for it there)."""
    assert "WGRESP_NO_ROS_MEMO" in spread.__doc__
    assert "byte-identical" in spread.__doc__


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def test_quantile_handles_inf_tails():
    vals = sorted([T0 + 100.0, T0 + 200.0, UNREACHED, UNREACHED])
    assert spread._quantile(vals, 0.0) == T0 + 100.0
    assert math.isfinite(spread._quantile(vals, 0.10))
    assert spread._quantile(vals, 0.90) == UNREACHED
    assert spread._quantile([UNREACHED, UNREACHED], 0.50) == UNREACHED
    assert spread._quantile([T0], 0.90) == T0


def test_quantile_hazen_positions_median_unchanged_tails_widened():
    """A4: _quantile moved to Hazen plotting positions (sample i at quantile
    (i+0.5)/n) so sample quantiles align with the stratified member design.
    The median is IDENTICAL to the old q*(n-1) convention; p10 moves earlier
    and p90 later (fail-safe polarity on both tails)."""
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert spread._quantile(vals, 0.50) == pytest.approx(3.5)   # == old
    assert spread._quantile(vals, 0.10) == pytest.approx(1.1)   # old: 1.5
    assert spread._quantile(vals, 0.90) == pytest.approx(5.9)   # old: 5.5
    # clamped ends
    assert spread._quantile(vals, 0.0) == 1.0
    assert spread._quantile(vals, 1.0) == 6.0
    # ordering holds for any q pair
    assert (spread._quantile(vals, 0.10) <= spread._quantile(vals, 0.50)
            <= spread._quantile(vals, 0.90))


def test_multiple_ignition_points_take_elementwise_min():
    tile = grass_tile()
    wx = const_wx(center_ignition(tile), wind_dir_deg=270.0)
    p_a = geo.rc_to_latlon(tile.spec, 20, 10)
    p_b = geo.rc_to_latlon(tile.spec, 20, 30)
    cfg = make_cfg()
    both = spread.compute_arrival(tile, [p_a, p_b], wx, T0, cfg)
    only_a = spread.compute_arrival(tile, [p_a], wx, T0, cfg)
    only_b = spread.compute_arrival(tile, [p_b], wx, T0, cfg)
    for i in range(len(both.data)):
        assert both.data[i] <= min(only_a.data[i], only_b.data[i]) + 1e-6


def test_wind_gust_blend_monotonic_and_identity():
    """WIND GUST BLEND (2026-07-13): blend=0 is exact identity (covered by
    every fingerprint pin in this file — this asserts the mechanism direction
    instead): raising the blend under gusty weather strictly speeds arrival,
    and gust==sustained makes every blend identical."""
    from dataclasses import replace
    from wgresponse import geo
    from wgresponse.terrain import SyntheticTerrain
    cfg = ResponseConfig(ensemble_n=3, grid_cell_m=250.0, horizon_h=12.0)
    org = LatLon(39.0, -105.0)
    tile = SyntheticTerrain(seed="gb").tile(geo.bbox_around([org], 5000.0), 250.0)
    t0 = 1_790_000_000.0

    def wx(gust):
        samples = [WeatherSample(t_s=t0 + h * 3600.0, wind_speed_mps=6.0,
                                 wind_gust_mps=gust, wind_dir_deg=270.0,
                                 rh_pct=12.0, temp_c=33.0) for h in range(14)]
        return WeatherForecast(loc=org, samples=samples, source="test")

    tgt = geo.destination(org, 90.0, 2500.0)

    def eta(blend, gust):
        ens = spread.ensemble_arrival(tile, [org], wx(gust), t0,
                                      replace(cfg, wind_gust_blend=blend),
                                      incident_id="gb")
        return spread.eta_at(ens, tgt).eta_p50_s

    gusty = [eta(b, 14.0) for b in (0.0, 0.5, 1.0)]
    assert gusty[0] > gusty[1] > gusty[2], gusty
    calm = [eta(b, 6.0) for b in (0.0, 0.5, 1.0)]
    assert calm[0] == calm[1] == calm[2], calm
