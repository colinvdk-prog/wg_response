"""tests/test_spread_any_angle.py — ANY-ANGLE SPREAD (cfg.spread_any_angle,
contracts SAFETY SEMANTICS 21; docs/review/grid_orientation_2026-10-09.md).

The 16-neighbour lattice moves only along 16 directions, so on a uniform
tile it reaches exactly the 16-sided polygon inscribed in each cell's fire
ellipse; at high wind (length-to-breadth ratio up to its cap of 8) the
ellipse is a needle a few degrees wide, and with the wind between two
lattice directions the head ran at 22-28 % of its own rate and the burned
area FELL as the wind rose (scratch_review/wind_area_inversion_probe.py,
scratch_review/grid_orientation_probe.py). Armed, every burning cell also
offers its neighbours the straight run from its anchor, traced cell by cell
at each crossed cell's own ellipse (_segment_arrival).

Pinned here:
  * OFF (the default, and the new arguments passed) is byte-identical:
    the pinned legacy fingerprints, anchor_log stays empty;
  * uniform tile: the analytic elliptical arrival from the ignition at
    every wind angle (head, area), area grows with wind; the legacy defect
    it removes is asserted too (so the test cannot pass vacuously);
  * never later than the legacy solve under constant weather (every legacy
    path is still offered at the same cost), on a heterogeneous tile;
  * a non-burnable barrier still blocks (no run tunnels through), a gap
    still passes;
  * memo = WGRESP_NO_ROS_MEMO=1 reference with the knob ON (incl. weather
    zones and a perimeter), deterministic, ensemble quantiles ordered;
  * RE-ANCHOR HISTORY: with kept_anchor a no-op re-anchor is the continuous
    run cell for cell (spot fires, embers aloft), a mid-run wind shift the
    continuous piecewise run; the simulator threads the anchors.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

from tests.test_spread import (
    NORTH,
    T0,
    WEST,
    _LEGACY_FINGERPRINTS,
    _fingerprint,
    _spot_setup,
    center_ignition,
    const_wx,
    grass_tile,
    set_column,
)
from tests.test_sim_reanchor_progress import (
    HOUR,
    _burned,
    _diff_below,
    _history,
    _ignite,
    _session,
    _shift_wx,
)
from wgresponse import geo, rothermel, spread
from wgresponse.contracts import (
    GridSpec,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

MPH = 0.44704
ACRE_M2 = 4046.8564224


def _cfg(**kw) -> ResponseConfig:
    kw.setdefault("ensemble_n", 1)
    return ResponseConfig(**kw)


def _on(**kw) -> ResponseConfig:
    return _cfg(spread_any_angle=True, **kw)


def _flat_params(code: int, wind_mps: float, wdir: float, rh: float = 20.0,
                 temp: float = 30.0) -> tuple:
    """(head_ros, lb, bearing) of a flat cell — cell_params' operations."""
    cfg = ResponseConfig()
    fuel = FUEL_TABLE[code]
    m_dead = max(0.01, dead_fuel_moisture_frac(temp, rh))
    bed = rothermel.fuel_bed(fuel, m_dead, cfg.live_herb_frac,
                             cfg.live_woody_frac)
    u = rothermel.midflame_wind_mps(wind_mps, fuel)
    res = rothermel.surface_ros_from_bed(bed, u, 0.0)
    return res.ros_mps, rothermel.ellipse_lb(res.effective_wind_mps), \
        (wdir + 180.0) % 360.0


def _gauge_s(dx: float, dy: float, head: float, lb: float, brg: float) -> float:
    """Analytic elliptical travel time (s) from the rear focus to (dx east,
    dy north) metres."""
    e = math.sqrt(lb * lb - 1.0) / lb
    hx, hy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
    return (math.hypot(dx, dy) - e * (dx * hx + dy * hy)) / (head * (1.0 - e))


def _uniform_fire(wind_mph: float, wdir: float, hours: float, cfg,
                  code: int = 102, n: int = 121, cell_m: float = 100.0,
                  c0: int = 15):
    """Burned acres / head (m) at `hours` of a GR2 point fire on a flat
    uniform tile, ignition near the west edge (the head runs east-ish)."""
    tile = grass_tile(nrows=n, ncols=n, cell_m=cell_m, fuel_code=code)
    r0 = n // 2
    ign = geo.rc_to_latlon(tile.spec, r0, c0)
    wx = const_wx(ign, wind_mps=wind_mph * MPH, wind_dir_deg=wdir)
    ras = spread.compute_arrival(tile, [ign], wx, T0,
                                 dataclasses.replace(cfg, horizon_h=hours + 1))
    t_end = T0 + hours * 3600.0
    brg = math.radians((wdir + 180.0) % 360.0)
    hx, hy = math.sin(brg), math.cos(brg)
    head = 0.0
    cells = 0
    for i, v in enumerate(ras.data):
        if v <= t_end:
            cells += 1
            r, c = divmod(i, n)
            head = max(head, (c - c0) * cell_m * hx + (r0 - r) * cell_m * hy)
    return cells * cell_m * cell_m / ACRE_M2, head, ras, (r0, c0)


def _mosaic_tile(n: int = 41) -> TerrainTile:
    """Heterogeneous: four fuels in patches, continuous slope/aspect, a
    non-burnable blob (gates exercised)."""
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=100.0,
                    nrows=n, ncols=n)
    tile = TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=102.0),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0),
                       source="test-aa-mosaic")
    codes = (102.0, 122.0, 145.0, 183.0)
    for r in range(n):
        for c in range(n):
            tile.fuel.set(r, c, codes[((r // 5) + (c // 4)) % 4])
            tile.slope_deg.set(r, c, 3.0 + 12.0 * abs(math.sin(0.41 * r + 0.17 * c)))
            tile.aspect_deg.set(r, c, (31.0 * r + 13.7 * c) % 360.0)
    for r in range(18, 23):
        for c in range(24, 27):
            tile.fuel.set(r, c, 91.0)
    return tile


# ---------------------------------------------------------------------------
# 1. OFF is the legacy engine, byte for byte
# ---------------------------------------------------------------------------

def test_knob_off_is_byte_identical_and_logs_nothing():
    """spread_any_angle=False explicitly, with kept_anchor / anchor_log
    passed, leaves the pinned legacy rasters (tests/test_spread.py A-D)
    bit for bit and records no anchor."""
    off = ResponseConfig(ensemble_n=4, spread_any_angle=False)
    tile = grass_tile()
    ign = center_ignition(tile)
    log = {}
    a = spread.compute_arrival(tile, [ign], const_wx(ign), T0, off,
                               anchor_log=log, kept_anchor={0: 0})
    assert _fingerprint(a) == _LEGACY_FINGERPRINTS["A_flat_wind"]
    assert log == {}
    tile_b = grass_tile(slope_deg=30.0, aspect_deg=270.0)
    ign_b = center_ignition(tile_b)
    b = spread.compute_arrival(tile_b, [ign_b], const_wx(ign_b, wind_mps=0.0),
                               T0, off)
    assert _fingerprint(b) == _LEGACY_FINGERPRINTS["B_upslope_nowind"]
    tile_c, ign_c, wx_c = _spot_setup()
    c = spread.compute_arrival(
        tile_c, [ign_c], wx_c, T0,
        ResponseConfig(ensemble_n=1, spotting_enabled=True,
                       spread_any_angle=False))
    assert _fingerprint(c) == _LEGACY_FINGERPRINTS["C_spotting"]
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=10.0,
                             wind_gust_mps=13.0,
                             wind_dir_deg=270.0 if i < 2 else 180.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    wx_d = WeatherForecast(loc=ign, samples=samples, source="test")
    d = spread.compute_arrival(tile, [ign], wx_d, T0, off)
    assert _fingerprint(d) == _LEGACY_FINGERPRINTS["D_wind_switch"]
    e0 = spread.ensemble_arrival(tile_c, [ign_c], wx_c, T0,
                                 ResponseConfig(ensemble_n=3,
                                                spotting_enabled=True),
                                 incident_id="x")
    elog = {}
    e1 = spread.ensemble_arrival(tile_c, [ign_c], wx_c, T0,
                                 ResponseConfig(ensemble_n=3,
                                                spotting_enabled=True),
                                 incident_id="x", anchor_log=elog,
                                 kept_anchor=None)
    for ra, rb in zip(e0.members + [e0.p10, e0.p50, e0.p90],
                      e1.members + [e1.p10, e1.p50, e1.p90]):
        assert ra.data == rb.data
    assert elog == {}


# ---------------------------------------------------------------------------
# 2. uniform tile: the engine's own ellipse at any wind angle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wdir", [270.0, 276.0, 281.25, 290.0, 300.0, 315.0])
def test_uniform_tile_arrival_is_the_analytic_ellipse(wdir):
    """GR2 at 40 mph (lb = 8, the cap): every cell the fire reaches more than
    ~5 cells from the ignition burns at the analytic elliptical time from the
    ignition to within 0.1 % (cells within ~10 cells of the ignition may burn
    later: a run is never offered before the cell offering it burned).
    The legacy lattice is far off whenever the wind sits between its
    directions (asserted, so the test cannot pass vacuously)."""
    head_ros, lb, brg = _flat_params(102, 40 * MPH, wdir)
    assert lb == 8.0
    _, _, on, (r0, c0) = _uniform_fire(40, wdir, 1.5, _on())
    _, _, off, _ = _uniform_fire(40, wdir, 1.5, _cfg())
    n = on.spec.ncols
    cm = on.spec.cell_m
    worst_on = 0.0
    worst_off = 0.0
    checked = 0
    for i, v in enumerate(on.data):
        if not math.isfinite(v):
            continue
        r, c = divmod(i, n)
        dx, dy = (c - c0) * cm, (r0 - r) * cm
        if math.hypot(dx, dy) < 10.0 * cm:
            continue
        ex = _gauge_s(dx, dy, head_ros, lb, brg)
        worst_on = max(worst_on, abs(v - T0 - ex) / ex)
        if math.isfinite(off.data[i]):
            worst_off = max(worst_off, (off.data[i] - T0 - ex) / ex)
        checked += 1
    assert checked > 200
    assert worst_on < 1e-3, worst_on
    if wdir not in (270.0, 315.0):
        assert worst_off > 0.5, "the legacy lattice should be far off here"


def test_head_and_area_do_not_depend_on_the_grid_angle():
    """40 mph GR2, 2 h, wind swept through a lattice gap: with the knob the
    head and the area stay within 2 % of the analytic ellipse at every
    angle; the legacy head swings by more than 2x across the same sweep."""
    head_ros, lb, _ = _flat_params(102, 40 * MPH, 270.0)
    e = math.sqrt(lb * lb - 1.0) / lb
    t = 2.0 * 3600.0
    a_m = 0.5 * (head_ros + head_ros * (1 - e) / (1 + e)) * t
    ana_acres = math.pi * a_m * (a_m / lb) / ACRE_M2
    ana_head = head_ros * t
    legacy_heads = []
    for wdir in (270.0, 274.0, 278.0, 281.25, 285.0, 290.0, 296.57, 305.0):
        acres, head, _, _ = _uniform_fire(40, wdir, 2.0, _on(), n=101, c0=10)
        assert abs(head / ana_head - 1.0) < 0.02, (wdir, head, ana_head)
        assert abs(acres / ana_acres - 1.0) < 0.02, (wdir, acres, ana_acres)
        legacy_heads.append(_uniform_fire(40, wdir, 2.0, _cfg(), n=101,
                                          c0=10)[1])
    assert max(legacy_heads) > 2.0 * min(legacy_heads)


def test_area_grows_with_wind():
    """The probe's finding: on a flat uniform tile the legacy area FELL from
    20 to 25 mph (the lattice misses the narrowing ellipse's width). With
    the knob, area grows monotonically with wind (GR2, wind between lattice
    directions, 2 h)."""
    def acres(mph, cfg):
        return _uniform_fire(mph, 281.25, 2.0, cfg, n=121, c0=12)[0]
    on = [acres(m, _on()) for m in (5, 10, 15, 20, 25, 30, 40)]
    assert all(b > a for a, b in zip(on, on[1:])), on
    assert acres(25, _cfg()) < acres(20, _cfg())


# ---------------------------------------------------------------------------
# 3. heterogeneous terrain: never later than legacy, barriers hold
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wind", [4.0, 12.0])
def test_never_later_than_legacy_under_constant_weather(wind):
    """Every legacy path is still offered at the same cost, so under
    constant weather no cell burns later with the knob than without it —
    exactly (float), on a mosaic tile with slope and a barrier blob — and
    somewhere it burns earlier."""
    tile = _mosaic_tile()
    ign = geo.rc_to_latlon(tile.spec, 20, 4)
    wx = const_wx(ign, wind_mps=wind, wind_dir_deg=281.0)
    off = spread.compute_arrival(tile, [ign], wx, T0, _cfg(horizon_h=24.0))
    on = spread.compute_arrival(tile, [ign], wx, T0, _on(horizon_h=24.0))
    assert all(b <= a for a, b in zip(off.data, on.data))
    assert sum(1 for a, b in zip(off.data, on.data) if b < a) > 20


def test_a_barrier_still_blocks_and_a_gap_still_passes():
    """A full-height non-burnable column: nothing east of it ever burns,
    with the knob (no straight run tunnels through it). Open one cell: the
    fire gets through, as on the lattice."""
    tile = grass_tile(nrows=41, ncols=41)
    set_column(tile, 25, 91.0)
    ign = geo.rc_to_latlon(tile.spec, 20, 5)
    wx = const_wx(ign, wind_mps=12.0, wind_dir_deg=281.0)
    on = spread.compute_arrival(tile, [ign], wx, T0, _on(horizon_h=48.0))
    n = tile.spec.ncols
    assert all(math.isinf(on.data[r * n + c])
               for r in range(41) for c in range(26, 41))
    assert any(math.isfinite(on.data[r * n + 24]) for r in range(41))
    gap = grass_tile(nrows=41, ncols=41)
    set_column(gap, 25, 91.0, skip_rows=(23,))
    on_gap = spread.compute_arrival(gap, [ign], wx, T0, _on(horizon_h=48.0))
    off_gap = spread.compute_arrival(gap, [ign], wx, T0, _cfg(horizon_h=48.0))
    east = [r * n + c for r in range(41) for c in range(26, 41)]
    assert any(math.isfinite(on_gap.data[i]) for i in east)
    assert all(b <= a for a, b in zip(off_gap.data, on_gap.data))


def test_a_diagonal_barrier_blocks_a_straight_run():
    """A two-cell-thick diagonal wall of non-burnable cells, which the
    lattice cannot cross (every knight / diagonal edge over it has both gate
    cells in the wall; a ONE-cell diagonal wall the lattice's gate rule lets
    knight moves jump): no straight run crosses it either, with the wind
    blowing straight at it."""
    n = 41
    tile = grass_tile(nrows=n, ncols=n)
    for k in range(n):
        tile.fuel.set(k, k, 91.0)            # NW-SE diagonal wall ...
        if k + 1 < n:
            tile.fuel.set(k, k + 1, 91.0)    # ... two cells thick
    ign = geo.rc_to_latlon(tile.spec, 30, 5)   # below/left of the wall
    wx = const_wx(ign, wind_mps=12.0, wind_dir_deg=225.0)  # head to NE
    above = [r * n + c for r in range(n) for c in range(n) if c >= r + 2]
    for cfg in (_cfg(horizon_h=48.0), _on(horizon_h=48.0)):
        ras = spread.compute_arrival(tile, [ign], wx, T0, cfg)
        assert all(math.isinf(ras.data[i]) for i in above)
        assert sum(1 for v in ras.data if math.isfinite(v)) > 200


# ---------------------------------------------------------------------------
# 4. engine laws: memo exactness, determinism, ensemble ordering
# ---------------------------------------------------------------------------

def _veer_wx(loc):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0 + i * 3600.0,
                      wind_speed_mps=4.0 + float(i % 9),
                      wind_gust_mps=(4.0 + float(i % 9)) * 1.3,
                      wind_dir_deg=(250.0 + 10.0 * (i % 10)) % 360.0,
                      rh_pct=15.0 + 3.0 * (i % 7), temp_c=22.0 + 2.5 * (i % 5))
        for i in range(100)])


@pytest.mark.parametrize("piecewise", [False, True])
@pytest.mark.parametrize("kind", ["const", "veer"])
def test_memo_and_reference_paths_agree(monkeypatch, kind, piecewise):
    """The ROS memo stays exact with the knob ON: arrival rasters are
    byte-identical to the WGRESP_NO_ROS_MEMO=1 reference path (mosaic
    tile, constant or veering weather, a stale perimeter, a direction
    offset, piecewise edge weather on and off)."""
    tile = _mosaic_tile()
    ign = geo.rc_to_latlon(tile.spec, 20, 4)
    wx = const_wx(ign, wind_mps=9.0, wind_dir_deg=281.0) if kind == "const" \
        else _veer_wx(ign)
    ring = [geo.destination(geo.rc_to_latlon(tile.spec, 10, 10), b, 250.0)
            for b in range(0, 360, 30)]
    cfg = _on(horizon_h=24.0, edge_weather_piecewise=piecewise)
    kw = dict(perimeter=[ring], perimeter_at_s=T0 - 2 * 3600.0,
              dir_offset_deg=17.3, wind_scale=1.1)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    memo = spread.compute_arrival(tile, [ign], wx, T0, cfg, **kw)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], wx, T0, cfg, **kw)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert memo.data == ref.data
    assert sum(1 for v in memo.data if math.isfinite(v)) > 100


def test_weather_zones_memo_exact_and_deterministic(monkeypatch):
    """With a two-zone WeatherField the knob stays memo-exact and
    deterministic."""
    from wgresponse.contracts import WeatherField
    tile = _mosaic_tile()
    ign = geo.rc_to_latlon(tile.spec, 20, 4)
    a = const_wx(ign, wind_mps=9.0, wind_dir_deg=281.0)
    b = const_wx(ign, wind_mps=13.0, wind_dir_deg=250.0, rh_pct=12.0)
    n = tile.spec.nrows * tile.spec.ncols
    zone_of = bytes(0 if (i % tile.spec.ncols) < 20 else 1 for i in range(n))
    wf = WeatherField(spec=tile.spec, points=[], forecasts=[a, b],
                      zone_of=zone_of)
    cfg = _on(horizon_h=24.0)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    m1 = spread.compute_arrival(tile, [ign], a, T0, cfg, weather_field=wf)
    m2 = spread.compute_arrival(tile, [ign], a, T0, cfg, weather_field=wf)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], a, T0, cfg, weather_field=wf)
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    assert m1.data == m2.data == ref.data


def test_ensemble_quantiles_ordered_and_nominal_logs_anchors():
    """Ensemble with the knob: p10 <= p50 <= p90 everywhere, p10 never later
    than the nominal member, and anchor_log carries the NOMINAL member's
    anchors (every reached cell, each anchor itself reached)."""
    tile = _mosaic_tile()
    ign = geo.rc_to_latlon(tile.spec, 20, 4)
    wx = const_wx(ign, wind_mps=9.0, wind_dir_deg=281.0)
    log = {}
    ens = spread.ensemble_arrival(tile, [ign], wx, T0,
                                  _on(ensemble_n=5, horizon_h=24.0),
                                  incident_id="aa", anchor_log=log)
    nom = ens.members[0].data
    for i in range(len(nom)):
        assert ens.p10.data[i] <= ens.p50.data[i] <= ens.p90.data[i]
        assert ens.p10.data[i] <= nom[i]
    reached = {i for i, v in enumerate(nom) if math.isfinite(v)}
    assert set(log) == reached
    assert all(a in reached for a in log.values())
    alone = spread.compute_arrival(tile, [ign], wx, T0, _on(horizon_h=24.0),
                                   incident_id="aa")
    assert alone.data == nom


def test_run_length_cap_restarts_runs(monkeypatch):
    """Past _ANY_ANGLE_MAX_CELLS a run restarts at the cell it reached: with
    a tiny cap the solve is still valid (never later than legacy, earlier
    somewhere) and every anchor lies within the cap of its cell."""
    monkeypatch.setattr(spread, "_ANY_ANGLE_MAX_CELLS_SQ", 6.0 * 6.0)
    tile = grass_tile(nrows=61, ncols=61)
    ign = geo.rc_to_latlon(tile.spec, 30, 5)
    wx = const_wx(ign, wind_mps=12.0, wind_dir_deg=281.0)
    off = spread.compute_arrival(tile, [ign], wx, T0, _cfg(horizon_h=12.0))
    log = {}
    on = spread.compute_arrival(tile, [ign], wx, T0, _on(horizon_h=12.0),
                                anchor_log=log)
    assert all(b <= a for a, b in zip(off.data, on.data))
    assert any(b < a for a, b in zip(off.data, on.data))
    n = tile.spec.ncols
    for i, a in log.items():
        assert math.hypot(i // n - a // n, i % n - a % n) <= 6.0 + 2.3


# ---------------------------------------------------------------------------
# 5. RE-ANCHOR HISTORY stays continuous
# ---------------------------------------------------------------------------

def test_noop_reanchor_with_kept_anchor_is_the_continuous_run():
    """Re-solve a spotting fire from its burn at 8 anchors, same weather,
    knob on: with the anchors of the continuous solve (kept_anchor) every
    re-solve is the continuous solve bit for bit — spot fires and embers in
    flight included. Without them the kept cells start new runs and the
    re-solve drifts late (why the simulator threads them)."""
    tile = grass_tile(nrows=81, ncols=81, cell_m=100.0)
    ign = geo.rc_to_latlon(tile.spec, 40, 10)
    wx = const_wx(ign, wind_mps=16.0, wind_dir_deg=281.0, rh_pct=10.0,
                  temp_c=36.0)
    cfg = _on(spotting_enabled=True, horizon_h=8.0)
    log, alog = [], {}
    cont = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="f",
                                  ember_log=log, anchor_log=alog).data
    t_hi = T0 + cfg.horizon_h * HOUR
    drifted = 0
    for k in range(1, 9):
        t_a = T0 + k * 600.0
        kept, aloft = _history(cont, t_a, log)
        kw = dict(incident_id="f", seed_spotting=False, kept=kept,
                  kept_wx=[(T0, wx.at(T0))], embers_aloft=aloft)
        got = spread.compute_arrival(tile, [], wx, t_a, cfg,
                                     kept_anchor=alog, **kw).data
        assert _diff_below(got, cont, t_hi) == [], k
        assert _burned(got, t_a) == set(kept), k
        bare = spread.compute_arrival(tile, [], wx, t_a, cfg, **kw).data
        if _diff_below(bare, cont, t_hi):
            drifted += 1
    assert drifted >= 1, "kept_anchor is never needed — test is vacuous"


@pytest.mark.parametrize("spotting", [False, True])
def test_wind_shift_reanchor_is_the_continuous_piecewise_run(spotting):
    """Wind FROM 270 veering to FROM 200 at T0 + 1 h, knob on: re-solving at
    the shift from the 270-run's burn with its weather history, anchors and
    the new wind IS the continuous run under the shifted forecast with
    piecewise edge weather — cell for cell."""
    tile = grass_tile(nrows=81, ncols=81, cell_m=100.0)
    ign = center_ignition(tile)
    cfg = _on(spotting_enabled=spotting, horizon_h=8.0)
    t_a = T0 + HOUR
    wx_old = _shift_wx(ign, T0, 30, 270.0, 270.0, 99)
    wx_new = _shift_wx(ign, T0, 30, 200.0, 200.0, 99)
    ref = spread.compute_arrival(
        tile, [ign], _shift_wx(ign, T0, 30, 270.0, 200.0, 1), T0,
        dataclasses.replace(cfg, edge_weather_piecewise=True),
        incident_id="f").data
    log, alog = [], {}
    before = spread.compute_arrival(tile, [ign], wx_old, T0, cfg,
                                    incident_id="f", ember_log=log,
                                    anchor_log=alog).data
    kept, aloft = _history(before, t_a, log)
    assert kept == {i: v for i, v in enumerate(ref) if v <= t_a}
    got = spread.compute_arrival(
        tile, [], wx_new, t_a, cfg, incident_id="f", seed_spotting=False,
        kept=kept, kept_wx=[(T0, wx_old.at(T0))], embers_aloft=aloft,
        kept_anchor=alog).data
    assert _diff_below(got, ref, T0 + cfg.horizon_h * HOUR) == []


def test_kept_anchor_ignores_anchors_outside_the_kept_burn():
    """A kept_anchor entry whose anchor is not itself kept history (or whose
    cell is not kept) is ignored: the cell anchors itself, exactly as with
    no kept_anchor."""
    tile = grass_tile(nrows=41, ncols=41)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=10.0, wind_dir_deg=281.0)
    cfg = _on(horizon_h=6.0)
    cont = spread.compute_arrival(tile, [ign], wx, T0, cfg).data
    t_a = T0 + 1800.0
    kept = {i: v for i, v in enumerate(cont) if v <= t_a}
    outside = next(i for i, v in enumerate(cont) if v > t_a)
    bogus = {i: outside for i in kept}
    bogus[outside] = next(iter(kept))
    kw = dict(kept=kept, kept_wx=[(T0, wx.at(T0))], seed_spotting=False)
    a = spread.compute_arrival(tile, [], wx, t_a, cfg, **kw).data
    b = spread.compute_arrival(tile, [], wx, t_a, cfg, kept_anchor=bogus,
                               **kw).data
    assert a == b


def test_simulator_same_wind_reanchor_reproduces_the_continuous_run(tmp_path):
    """The simulator threads the nominal field's anchors (_SimFire._anchors)
    into every re-anchor: with the knob on, a same-direction set_wind (no
    physical change) leaves the nominal truth untouched cell for cell."""
    s = _session(tmp_path, spread_any_angle=True)
    fid = _ignite(s)
    for _ in range(2):
        s.tick()
    f = s._fires[fid]
    assert f._anchors, "the knob must record the nominal field's anchors"
    t_sw = s._now_s
    old = f.ens.members[0].data
    s.set_wind(f.wind_dir_deg)
    s.tick()
    assert f.anchor_t0_s == t_sw, "the no-op set_wind must re-anchor"
    new = f.ens.members[0].data
    assert _diff_below(new, old, f.t_ignite_s + s._cfg.horizon_h * HOUR) == []


def test_simulator_knob_off_records_no_anchors(tmp_path):
    """Knob off: the simulator passes no anchor keyword (legacy calls)."""
    s = _session(tmp_path)
    fid = _ignite(s)
    s.tick()
    assert s._fires[fid]._anchors is None
