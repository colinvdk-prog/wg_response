"""Spread reads the LOCAL weather forecast across its domain (founder
2026-09-27, verbatim: "ensure that fire spread includes local weather
forecast"; contracts SAFETY SEMANTICS 17; cfg.weather_grid_km /
weather_points_max / weather_cache_ttl_s; watch --weather-grid-km).

Pinned here:
  * BYTE IDENTITY, knob OFF: cycles, plans, /api/state, the persisted file
    and /api/world/summary reproduce goldens computed on commit b5ce62c
    (before the rule existed) on a world whose forecast varies in space
    (tests/wxfield_stack.py); the companion knobs are inert; spread with no
    field — or a field whose zones all carry the one forecast — is the legacy
    raster bit for bit; the NWS adapter makes exactly the legacy requests.
  * MEMO PARITY, knob ON: the ROS memo path equals the WGRESP_NO_ROS_MEMO=1
    reference path with multi-zone fields over the B3 matrix (tiles x wind
    regimes x perimeter x direction offset), with the two physics knobs,
    with spotting, for the ensemble and for a re-anchor (kept / kept_wx).
  * THE LAW: global nested lattice, the x2 coarsening to the cap, nearest-
    point zones (vs brute force), edges priced by their SOURCE zone, the
    ensemble perturbations alike in every zone, embers on the source zone's
    wind, a failed node -> the centroid with a note, a failed centroid -> the
    legacy provider failure, seeded fires single-point, the centroid forecast
    still the one for dispatch / the extreme-regime posture, the off-domain
    horizon working on zoned rasters, the NWS cache (process-level, static
    points mapping, TTL'd gridpoints, no stale serving), the surfacing and
    the flags.

Deterministic; no network.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
import wxfield_stack as wf  # noqa: E402
from test_spread import (  # noqa: E402  (the B3 matrix tiles, shared)
    _B3_TILES,
    _b3_wx,
    _fingerprint,
    _spot_setup,
    _square_ring,
    const_wx,
    grass_tile,
    set_column,
)
from wgresponse import geo, spread, threat, weather, wxfield  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    UNREACHED,
    GridSpec,
    LatLon,
    ResponseConfig,
    WeatherField,
    WeatherForecast,
    WeatherPoint,
    WeatherSample,
    validate_config,
)

T0 = 1_750_000_000.0          # the test_spread epoch (its tiles / forecasts)

#: KNOB-OFF GOLDENS: tests/wxfield_stack.wx_world_run() digests computed on
#: commit b5ce62c (the per-fire off-domain horizon merge) — the engine BEFORE
#: this rule existed — over the knob-relevant world (a fire whose forecast
#: differs across its ~19 x 19 km domain). Recompute:
#: `py -3 scratch_review/wxfield/golden_wxfield.py <a git-archive of b5ce62c>`
#: (its docstring has the recipe).
_GOLDEN_CYCLES = [
    "d023759101580f852178f134072a52a30273b277408600610ee862c2bd738394",
    "0b09a425cec1a50d36fe03c5a9583874f14549644ee7dc1308766cea12d4fbdf",
    "cabf1cfa55d5198362b41eb8bba54a8649b0d3fb6152c74c6c86057cf494b7ee",
]
_GOLDEN_API = "a6511c21f9be7de7406729b810172af009746b1b58bf638d834bf2178fc72514"
_GOLDEN_STATE = "e70de46a564bdda519facd825b139b763a8a50b0b6d3303dca5f8f7cefeb7e79"
_GOLDEN_SUMMARY = "de8a8f125aef880afb2113bec734e009127d108c91277b0a145b1c2e0ca1096f"

ON = dict(weather_grid_km=5.0)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Zone maps and the process-level NWS cache never leak between tests."""
    wxfield.clear_zone_cache()
    weather.nws_forecast_cache().clear()
    yield
    wxfield.clear_zone_cache()
    weather.nws_forecast_cache().clear()


def _cycle(tmp_path, cfg, cycles=1, weather_provider=None, incidents=None,
           dispatcher=None, name="state"):
    clock = ss.SettableClock(wf.T0)
    kw = {} if dispatcher is None else {"dispatcher": dispatcher}
    daemon, monitor, feed, engine, wx = wf.make_stack(
        tmp_path / name, cfg, clock=clock, weather=weather_provider,
        incidents=incidents, **kw)
    res = None
    for k in range(cycles):
        clock.t = wf.T0 + 60.0 + k * wf.PERIOD_S
        res = daemon.run_cycle()
    return daemon, res, wx


def _hours(eta_s, now_s):
    return (eta_s - now_s) / 3600.0


# ---------------------------------------------------------------------------
# byte identity: the knob OFF is the pre-change engine
# ---------------------------------------------------------------------------

def test_knob_off_reproduces_the_pre_change_engine_byte_for_byte(tmp_path):
    cycles, api, state, summary = wf.wx_world_run(tmp_path / "state")
    assert cycles == _GOLDEN_CYCLES          # every CycleResult decision + plan
    assert api == _GOLDEN_API                # the /api/state payload
    assert state == _GOLDEN_STATE            # the persisted state file bytes
    assert summary == _GOLDEN_SUMMARY        # the /api/world/summary payload


def test_the_companion_knobs_are_inert_with_the_grid_off(tmp_path):
    """Grid 0: the points cap and the cache TTL are never read — changed,
    they still reproduce the pre-change engine."""
    for extra in ({}, dict(weather_points_max=3, weather_cache_ttl_s=5.0)):
        cfg = wf.small_cfg(weather_grid_km=0.0, **extra)
        got = wf.wx_world_run(tmp_path / f"s{len(extra)}", cfg=cfg)
        assert got == (_GOLDEN_CYCLES, _GOLDEN_API, _GOLDEN_STATE,
                       _GOLDEN_SUMMARY)


def test_knob_off_asks_one_forecast_and_surfaces_nothing(tmp_path):
    daemon, res, wx = _cycle(tmp_path, wf.small_cfg())
    assert [(round(l.lat, 4), round(l.lon, 4)) for l, _ in wx.calls] == [
        (wf.ORIGIN.lat, wf.ORIGIN.lon)]                 # the centroid only
    assert res.weather_fields == {}
    assert not any("local weather" in n for n in res.notes)
    st = daemon.state()
    assert "weather_grid" not in st
    assert all("weather" not in row for row in st["incidents"])
    summary = daemon.world_summary()
    assert "weather_grid" not in summary and "weather" not in summary["counts"]
    assert all("weather" not in row for row in summary["incidents"])


def test_defaults_are_off_and_validate():
    cfg = ResponseConfig()
    assert cfg.weather_grid_km == 0.0
    assert cfg.weather_points_max == 16
    assert cfg.weather_cache_ttl_s == 1800.0
    assert validate_config(cfg) == []
    assert validate_config(ResponseConfig(weather_grid_km=10.0,
                                          weather_points_max=9)) == []
    bad = validate_config(ResponseConfig(weather_grid_km=0.5,
                                         weather_points_max=256,
                                         weather_cache_ttl_s=86_400.0))
    assert len(bad) == 3
    assert validate_config(ResponseConfig(weather_grid_km=math.inf))
    assert validate_config(ResponseConfig(weather_points_max=0))


def _tile_and_wx():
    tile = _B3_TILES["mixed"]()
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)
    return tile, ign, _b3_wx("veer", ign)


def test_spread_without_a_field_is_the_legacy_raster():
    tile, ign, wx = _tile_and_wx()
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    legacy = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    assert spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  weather_field=None).data == legacy.data


def _uniform_field(tile, wx, zones=4):
    """A multi-zone field whose every zone carries the SAME forecast."""
    spec = tile.spec
    pts = [geo.rc_to_latlon(spec, r, c)
           for r, c in ((5, 5), (5, 27), (27, 5), (27, 27))][:zones]
    zm = wxfield.compute_zone_map(spec, pts)
    assert len(set(zm)) == zones
    return WeatherField(spec=spec, forecasts=[wx] * zones, zone_of=zm,
                        points=[WeatherPoint(p.lat, p.lon, "lattice")
                                for p in pts])


@pytest.mark.parametrize("memo_off", [False, True])
def test_zones_carrying_one_forecast_are_the_legacy_raster(memo_off,
                                                           monkeypatch):
    """Keying the per-hour weather by (zone, hour) changes no value: zones
    that all carry the one forecast give the legacy raster bit for bit, on
    both the memo and the reference path, for the ensemble too."""
    if memo_off:
        monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    else:
        monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    tile, ign, wx = _tile_and_wx()
    field = _uniform_field(tile, wx)
    for extra in ({}, dict(edge_weather_piecewise=True, slope_phi_vector=True,
                           spotting_enabled=True, wind_gust_blend=0.5)):
        cfg = ResponseConfig(ensemble_n=4, horizon_h=48.0, **extra)
        a = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="u")
        b = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="u",
                                    weather_field=field)
        for m, n in zip(a.members, b.members):
            assert m.data == n.data
        assert a.p10.data == b.p10.data and a.p50.data == b.p50.data
        assert a.p90.data == b.p90.data


def test_nws_knob_off_keeps_the_legacy_requests():
    http = FakeNws()
    w = weather.NwsWeather(http_get=http, cfg=ResponseConfig())
    assert w.cache is None
    a = w.forecast(LatLon(39.02, -121.03), 24.0)
    b = w.forecast(LatLon(39.02, -121.03), 24.0)
    assert [u for u in http.urls] == [
        "https://api.weather.gov/points/39.0200,-121.0300",
        http.grid_url_for(39.02, -121.03)] * 2
    assert a.samples == b.samples
    assert weather.nws_forecast_cache().stats()["grids"] == 0


# ---------------------------------------------------------------------------
# memo parity with zones ON
# ---------------------------------------------------------------------------

def _variant_wx(kind: str, loc: LatLon, k: int) -> WeatherForecast:
    """Zone k's forecast: distinct wind / direction / RH per zone (and per
    hour for 'veer')."""
    if kind == "const":
        return const_wx(loc, wind_mps=6.0 + 2.5 * k,
                        wind_dir_deg=(270.0 - 20.0 * k) % 360.0,
                        rh_pct=18.0 + 6.0 * k)
    samples = [WeatherSample(t_s=T0 + i * 3600.0,
                             wind_speed_mps=3.0 + float((i + 2 * k) % 9),
                             wind_gust_mps=(3.0 + float((i + 2 * k) % 9)) * 1.4,
                             wind_dir_deg=(240.0 + 13.0 * k
                                           + 10.0 * ((i + k) % 10)) % 360.0,
                             rh_pct=14.0 + 3.0 * ((i + 3 * k) % 7),
                             temp_c=21.0 + 2.5 * ((i + k) % 5))
               for i in range(100)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


def _zoned_field(tile, kind: str, ign: LatLon) -> WeatherField:
    """The ignition as zone 0 plus three off-centre points, each its own
    forecast; nearest-point zones (wxfield.compute_zone_map)."""
    spec = tile.spec
    pts = [ign] + [geo.rc_to_latlon(spec, r, c)
                   for r, c in ((4, 24), (26, 10), (24, 28))]
    fcs = [_b3_wx(kind, ign)] + [_variant_wx(kind, p, k)
                                 for k, p in enumerate(pts[1:], start=1)]
    zm = wxfield.compute_zone_map(spec, pts)
    assert len(set(zm)) == 4
    return WeatherField(spec=spec, forecasts=fcs, zone_of=zm,
                        points=[WeatherPoint(p.lat, p.lon, "lattice")
                                for p in pts])


def _memo_and_ref(monkeypatch, fn):
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    memo = fn()
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = fn()
    monkeypatch.delenv("WGRESP_NO_ROS_MEMO", raising=False)
    return memo, ref


@pytest.mark.parametrize("wx_kind", ["const", "veer"])
@pytest.mark.parametrize("tile_name", sorted(_B3_TILES))
def test_zone_memo_matches_the_reference_path(tile_name, wx_kind, monkeypatch):
    """THE B3 GATE WITH ZONES: memo == WGRESP_NO_ROS_MEMO=1 reference, every
    cell, across tiles x wind regimes x {no perimeter, stale perimeter} x
    {dir_offset 0, +17.3} (+ the ensemble-style scales under 'veer') — and
    the zones really move the raster."""
    tile = _B3_TILES[tile_name]()
    spec = tile.spec
    ign = geo.rc_to_latlon(spec, spec.nrows // 2, 4)
    field = _zoned_field(tile, wx_kind, ign)
    wx = field.forecasts[0]
    ring = _square_ring(geo.rc_to_latlon(spec, spec.nrows // 2,
                                         spec.ncols // 2),
                        half_m=400.0, step_m=120.0)
    scales = {} if wx_kind == "const" else \
        {"wind_scale": 1.15, "moisture_scale": 0.9}
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    moved = 0
    for perimeter, pat in ((None, None), ([ring], T0 - 6 * 3600.0)):
        for off in (0.0, 17.3):
            kw = dict(dir_offset_deg=off, perimeter=perimeter,
                      perimeter_at_s=pat, **scales)
            memo, ref = _memo_and_ref(monkeypatch, lambda: spread.compute_arrival(
                tile, [ign], wx, T0, cfg, weather_field=field, **kw))
            case = (tile_name, wx_kind, off, perimeter is not None)
            assert memo.data == ref.data, case
            assert _fingerprint(memo) == _fingerprint(ref), case
            assert sum(1 for v in memo.data if math.isfinite(v)) > 50, case
            legacy = spread.compute_arrival(tile, [ign], wx, T0, cfg, **kw)
            moved += memo.data != legacy.data
    assert moved == 4          # every case differs from the single forecast


@pytest.mark.parametrize("tile_name", sorted(_B3_TILES))
def test_zone_memo_parity_with_the_physics_knobs(tile_name, monkeypatch):
    tile = _B3_TILES[tile_name]()
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)
    field = _zoned_field(tile, "veer", ign)
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0,
                         edge_weather_piecewise=True, slope_phi_vector=True,
                         wind_gust_blend=0.5, grass_ros_floor_frac=0.1,
                         ros_floor_extinction_gate=True)
    memo, ref = _memo_and_ref(monkeypatch, lambda: spread.compute_arrival(
        tile, [ign], field.forecasts[0], T0, cfg, weather_field=field,
        ros_scale=1.3))
    assert memo.data == ref.data


def test_zone_memo_parity_with_spotting(monkeypatch):
    tile, ign, wx = _spot_setup()
    spec = tile.spec
    pts = [ign, geo.rc_to_latlon(spec, 5, 30), geo.rc_to_latlon(spec, 35, 30)]
    fcs = [wx, const_wx(pts[1], wind_mps=18.0, wind_dir_deg=250.0),
           const_wx(pts[2], wind_mps=22.0, wind_dir_deg=300.0)]
    field = WeatherField(spec=spec, forecasts=fcs,
                         zone_of=wxfield.compute_zone_map(spec, pts),
                         points=[WeatherPoint(p.lat, p.lon, "x") for p in pts])
    for off in (0.0, 17.3):
        cfg = ResponseConfig(ensemble_n=1, spotting_enabled=True)
        memo, ref = _memo_and_ref(monkeypatch, lambda: spread.compute_arrival(
            tile, [ign], wx, T0, cfg, dir_offset_deg=off, weather_field=field,
            incident_id="spot", member_k=2))
        assert memo.data == ref.data, off


def test_zone_memo_parity_for_the_ensemble(monkeypatch):
    tile = _B3_TILES["mixed"]()
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)
    field = _zoned_field(tile, "veer", ign)
    cfg = ResponseConfig(ensemble_n=5, horizon_h=96.0, model_error_sd_log=0.7)
    a, b = _memo_and_ref(monkeypatch, lambda: spread.ensemble_arrival(
        tile, [ign], field.forecasts[0], T0, cfg, incident_id="ens-z",
        weather_field=field))
    for k, (m, n) in enumerate(zip(a.members, b.members)):
        assert m.data == n.data, k
    assert a.p10.data == b.p10.data and a.p50.data == b.p50.data
    assert a.p90.data == b.p90.data


def test_zone_memo_parity_for_a_re_anchor_with_history(monkeypatch):
    """kept / kept_wx (the simulator's re-anchor seam) with a field: history
    spans stay domain-wide, the continuation reads the zones — memo ==
    reference, with and without the piecewise walker."""
    tile = _B3_TILES["grass"]()
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)
    field = _zoned_field(tile, "veer", ign)
    wx = field.forecasts[0]
    cfg0 = ResponseConfig(ensemble_n=1, horizon_h=48.0)
    first = spread.compute_arrival(tile, [ign], wx, T0, cfg0,
                                   weather_field=field)
    t1 = T0 + 3 * 3600.0
    kept = {i: t for i, t in enumerate(first.data) if t <= t1}
    assert len(kept) > 5
    kept_wx = [(T0, wx.samples[0]), (T0 + 3600.0, wx.samples[1]),
               (T0 + 7200.0, wx.samples[2])]
    for pw in (False, True):
        cfg = ResponseConfig(ensemble_n=1, horizon_h=48.0,
                             edge_weather_piecewise=pw)
        memo, ref = _memo_and_ref(monkeypatch, lambda: spread.compute_arrival(
            tile, [], wx, t1, cfg, kept=kept, kept_wx=kept_wx,
            weather_field=field))
        assert memo.data == ref.data, pw
        assert all(memo.data[i] == t for i, t in kept.items())


# ---------------------------------------------------------------------------
# the law, at the spread seam
# ---------------------------------------------------------------------------

def _split_field(tile, west_wx, east_wx, split_col):
    """Zone 0 = columns < split_col, zone 1 = the rest."""
    spec = tile.spec
    zm = bytearray(spec.nrows * spec.ncols)
    for r in range(spec.nrows):
        base = r * spec.ncols
        zm[base + split_col:base + spec.ncols] = b"\x01" * (spec.ncols - split_col)
    return WeatherField(spec=spec, forecasts=[west_wx, east_wx],
                        zone_of=bytes(zm),
                        points=[WeatherPoint(0.0, 0.0, "primary"),
                                WeatherPoint(0.0, 0.0, "lattice")])


def test_an_edge_is_priced_by_its_source_cell_zone():
    """West half calm (2 m/s), east half windy (12 m/s), both from the west:
    the fire lit in the west crawls to the split, then runs. Cells west of
    the split arrive exactly as under the calm forecast alone (their edges
    are all priced west); the east half arrives far earlier than calm and
    later than all-windy (it had to cross the calm half first)."""
    tile = grass_tile(nrows=21, ncols=61, cell_m=100.0)
    ign = geo.rc_to_latlon(tile.spec, 10, 2)
    calm = const_wx(ign, wind_mps=2.0, wind_dir_deg=270.0)
    windy = const_wx(ign, wind_mps=12.0, wind_dir_deg=270.0)
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    split = 30
    zoned = spread.compute_arrival(tile, [ign], calm, T0, cfg,
                                   weather_field=_split_field(tile, calm,
                                                              windy, split))
    only_calm = spread.compute_arrival(tile, [ign], calm, T0, cfg)
    only_windy = spread.compute_arrival(tile, [ign], windy, T0, cfg)
    for c in range(split - 2):          # every edge into these starts west
        assert zoned.get(10, c) == only_calm.get(10, c)
    far = zoned.get(10, 60)
    assert only_windy.get(10, 60) < far < only_calm.get(10, 60)
    # the east half's own run: the split -> far edge at the windy rate
    run_z = far - zoned.get(10, split)
    run_w = only_windy.get(10, 60) - only_windy.get(10, split)
    assert run_z == pytest.approx(run_w, rel=0.02)


def test_a_zone_wind_direction_steers_its_own_cells():
    """The east zone's wind blows from the NORTH: once the front enters it,
    the fire runs south there, not east."""
    tile = grass_tile(nrows=61, ncols=61, cell_m=100.0)
    ign = geo.rc_to_latlon(tile.spec, 10, 10)
    west = const_wx(ign, wind_mps=8.0, wind_dir_deg=270.0)
    north = const_wx(ign, wind_mps=8.0, wind_dir_deg=0.0)
    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0)
    zoned = spread.compute_arrival(tile, [ign], west, T0, cfg,
                                   weather_field=_split_field(tile, west,
                                                              north, 30))
    plain = spread.compute_arrival(tile, [ign], west, T0, cfg)
    # in the east zone, south of the entry row arrives much sooner than
    # under the west wind alone, and the far east much later
    assert zoned.get(50, 40) < plain.get(50, 40)
    assert zoned.get(10, 60) > plain.get(10, 60)


def _wx_exact(loc, wind, gust, wdir):
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind,
                             wind_gust_mps=gust, wind_dir_deg=wdir,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


def test_ensemble_perturbations_apply_alike_in_every_zone():
    """dir_offset_deg rotates EVERY zone (== rotating each zone's forecast;
    37.5 deg is dyadic, exact) and wind_scale 2.0 scales every zone (==
    doubling each zone's wind and gust; x2 is exact, gust blend included) —
    bit for bit."""
    tile = grass_tile(nrows=41, ncols=41, cell_m=100.0)
    ign = geo.rc_to_latlon(tile.spec, 20, 5)
    zones = ((5.0, 6.5, 270.0), (9.0, 11.7, 230.0))

    def fcs(rot=0.0, mul=1.0):
        return [_wx_exact(ign, w * mul, g * mul, d + rot) for w, g, d in zones]

    cfg = ResponseConfig(ensemble_n=1, horizon_h=96.0, wind_gust_blend=0.5)
    base, rot, dbl = fcs(), fcs(rot=37.5), fcs(mul=2.0)
    via_off = spread.compute_arrival(
        tile, [ign], base[0], T0, cfg, dir_offset_deg=37.5,
        weather_field=_split_field(tile, *base, 20))
    via_fc = spread.compute_arrival(
        tile, [ign], rot[0], T0, cfg,
        weather_field=_split_field(tile, *rot, 20))
    assert via_off.data == via_fc.data
    via_scale = spread.compute_arrival(
        tile, [ign], base[0], T0, cfg, wind_scale=2.0,
        weather_field=_split_field(tile, *base, 20))
    via_dbl = spread.compute_arrival(
        tile, [ign], dbl[0], T0, cfg,
        weather_field=_split_field(tile, *dbl, 20))
    assert via_scale.data == via_dbl.data
    plain = spread.compute_arrival(
        tile, [ign], base[0], T0, cfg,
        weather_field=_split_field(tile, *base, 20))
    assert via_off.data != plain.data and via_scale.data != plain.data


def test_embers_fly_on_their_source_zone_wind():
    """Spotting across a 2-cell barrier east of the ignition: with the
    ignition zone's wind from the WEST the embers jump it; with the
    ignition zone's wind from the NORTH they fly south and the barrier holds
    — even though the zone BEYOND the barrier blows from the west."""
    tile, ign, west = _spot_setup()           # barrier at cols 20-21
    north = const_wx(ign, wind_mps=25.0, wind_dir_deg=0.0)
    cfg = ResponseConfig(ensemble_n=1, spotting_enabled=True)
    beyond = [(r, c) for r in range(tile.spec.nrows)
              for c in range(22, tile.spec.ncols)]
    jumped = spread.compute_arrival(
        tile, [ign], west, T0, cfg,
        weather_field=_split_field(tile, west, west, 20))
    assert any(math.isfinite(jumped.get(r, c)) for r, c in beyond)
    held = spread.compute_arrival(
        tile, [ign], north, T0, cfg,
        weather_field=_split_field(tile, north, west, 20))
    assert all(held.get(r, c) == UNREACHED for r, c in beyond)


def test_a_field_on_another_grid_is_refused():
    tile, ign, wx = _tile_and_wx()
    other = grass_tile(nrows=10, ncols=10)
    field = _uniform_field(tile, wx)
    bad = dataclasses.replace(field, spec=other.spec)
    cfg = ResponseConfig(ensemble_n=1)
    with pytest.raises(ValueError):
        spread.compute_arrival(tile, [ign], wx, T0, cfg, weather_field=bad)
    short = dataclasses.replace(field, zone_of=field.zone_of[:-1])
    with pytest.raises(ValueError):
        spread.compute_arrival(tile, [ign], wx, T0, cfg, weather_field=short)
    missing = dataclasses.replace(field, forecasts=field.forecasts[:2])
    with pytest.raises(ValueError):
        spread.compute_arrival(tile, [ign], wx, T0, cfg, weather_field=missing)


# ---------------------------------------------------------------------------
# the lattice and the zones
# ---------------------------------------------------------------------------

def _spec(north=39.3, west=-121.2, cell=200.0, nrows=120, ncols=150):
    return GridSpec(north_lat=north, west_lon=west, cell_m=cell, nrows=nrows,
                    ncols=ncols)


def test_lattice_nodes_follow_the_global_formula():
    spec = _spec()
    s_m = 7.0 * 1000.0
    dlat = s_m / geo.M_PER_DEG_LAT
    nodes = wxfield.lattice_nodes(spec, 7.0)
    assert nodes
    for p in nodes:
        i = round(p.lat / dlat)
        assert p.lat == i * dlat
        dlon = s_m / geo.m_per_deg_lon(p.lat)
        assert p.lon == round(p.lon / dlon) * dlon
        assert geo.latlon_to_rc(spec, p) is not None
    # reading order: north to south, west to east
    assert nodes == sorted(nodes, key=lambda p: (-p.lat, p.lon))


def test_overlapping_domains_share_nodes_and_coarser_levels_nest():
    a = _spec(north=39.3, west=-121.2)
    b = _spec(north=39.2, west=-121.05, nrows=130, ncols=140)
    na, nb = set(wxfield.lattice_nodes(a, 5.0)), set(wxfield.lattice_nodes(b, 5.0))
    in_both = {p for p in na | nb if geo.latlon_to_rc(a, p) is not None
               and geo.latlon_to_rc(b, p) is not None}
    assert in_both and in_both <= na and in_both <= nb
    for spacing in (2.5, 5.0, 10.0):
        fine = set(wxfield.lattice_nodes(a, spacing))
        coarse = set(wxfield.lattice_nodes(a, 2 * spacing))
        assert coarse and coarse <= fine
    for level in (0, 1, 2):          # every second sqrt-2 level nests
        fine = set(wxfield.lattice_nodes(a, wxfield.level_spacing_km(1.5, level)))
        coarse = set(wxfield.lattice_nodes(
            a, wxfield.level_spacing_km(1.5, level + 2)))
        assert coarse and coarse <= fine


def test_the_lattice_coarsens_in_sqrt2_steps_until_the_cap():
    spec = _spec()                  # 24 x 30 km
    for level in range(10):          # sqrt 2 per level, exactly x2 per two
        assert wxfield.level_spacing_km(2.0, level + 2) == \
            2.0 * wxfield.level_spacing_km(2.0, level)
        assert wxfield.level_spacing_km(2.0, level + 1) == pytest.approx(
            math.sqrt(2.0) * wxfield.level_spacing_km(2.0, level))
    cfg = ResponseConfig(weather_grid_km=2.0, weather_points_max=6)
    nodes, spacing, level = wxfield.lattice_points(spec, cfg)
    assert level >= 1 and spacing == wxfield.level_spacing_km(2.0, level)
    assert len(nodes) <= 5
    finer = wxfield.level_spacing_km(2.0, level - 1)
    assert len(wxfield.lattice_nodes(spec, finer)) > 5   # the finer one did not fit
    roomy = ResponseConfig(weather_grid_km=10.0, weather_points_max=255)
    nodes, spacing, level = wxfield.lattice_points(spec, roomy)
    assert (spacing, level) == (10.0, 0) and nodes
    assert wxfield.lattice_points(
        spec, ResponseConfig(weather_grid_km=10.0, weather_points_max=1)) == (
        [], 10.0, 0)


def _brute_zone_map(spec, points):
    out = bytearray(spec.nrows * spec.ncols)
    for r in range(spec.nrows):
        lat_r = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
        mlon = geo.m_per_deg_lon(lat_r)
        for c in range(spec.ncols):
            u = (c + 0.5) * spec.cell_m
            best, bd = 0, None
            for i, p in enumerate(points):
                dx = (p.lon - spec.west_lon) * mlon - u
                dy = (p.lat - lat_r) * geo.M_PER_DEG_LAT
                d = dx * dx + dy * dy
                if bd is None or d < bd:
                    best, bd = i, d
            out[r * spec.ncols + c] = best
    return bytes(out)


@pytest.mark.parametrize("seed", range(6))
def test_zone_map_is_the_nearest_point(seed):
    rng = random.Random(seed)
    spec = _spec(north=38.0 + seed, cell=150.0 + 20 * seed,
                 nrows=40 + 7 * seed, ncols=55 + 5 * seed)
    s, n, w, e = wxfield._grid_bounds(spec)
    pts = [LatLon(rng.uniform(s - 0.05, n + 0.05), rng.uniform(w - 0.05, e + 0.05))
           for _ in range(rng.randint(2, 18))]
    assert wxfield.compute_zone_map(spec, pts) == _brute_zone_map(spec, pts)


def test_zone_map_ties_go_to_the_lower_index_and_is_cached():
    spec = _spec(nrows=30, ncols=30)
    p = geo.rc_to_latlon(spec, 15, 15)
    assert wxfield.compute_zone_map(spec, [p, p]) == bytes(900)
    assert wxfield.compute_zone_map(spec, [p]) == bytes(900)
    q = geo.rc_to_latlon(spec, 3, 3)
    first = wxfield.zone_map(spec, [p, q])
    assert wxfield.zone_map(spec, [p, q]) is first        # cached
    with pytest.raises(ValueError):
        wxfield.compute_zone_map(spec, [p] * 257)


# ---------------------------------------------------------------------------
# the law, in the monitor
# ---------------------------------------------------------------------------

def test_knob_on_fetches_the_lattice_and_spreads_on_it(tmp_path):
    rec = ss.RecordingDispatcher()
    daemon, res, wx = _cycle(tmp_path, wf.small_cfg(**ON), dispatcher=rec)
    fld = res.weather_fields["inc-wx"]
    asked = {(round(l.lat, 4), round(l.lon, 4)) for l, _ in wx.calls}
    assert len(wx.calls) == len(fld.points) >= 3
    assert asked == {(round(p.lat, 4), round(p.lon, 4)) for p in fld.points}
    assert fld.points[0].role == "primary"
    assert (fld.points[0].lat, fld.points[0].lon) == (wf.ORIGIN.lat,
                                                      wf.ORIGIN.lon)
    assert all(p.ok and p.role == "lattice" for p in fld.points[1:])
    assert sum(p.cells for p in fld.points) == len(fld.zone_of)
    assert len(fld.points) <= 16
    note = [n for n in res.notes if "local weather" in n]
    assert len(note) == 1 and f"{len(fld.points)} forecast points" in note[0]
    # the gradient forecast blows harder to the east: the downwind client's
    # arrival moves earlier than under the centroid forecast alone
    off_daemon, off_res, _ = _cycle(tmp_path, wf.small_cfg(), name="off")
    now = wf.T0 + 60.0
    eta = {a.client_id: a.eta for a in res.assessments}
    eta_off = {a.client_id: a.eta for a in off_res.assessments}
    assert _hours(eta["cE"].eta_p10_s, now) < _hours(eta_off["cE"].eta_p10_s,
                                                     now) - 1.0
    # ... and the ONE forecast everything else reads is still the centroid's
    assert rec.calls[-1]["forecasts"]["inc-wx"] is fld.forecasts[0]
    assert rec.calls[-1]["forecasts"]["inc-wx"].loc == wf.ORIGIN


def test_the_extreme_regime_posture_reads_the_centroid_forecast(tmp_path,
                                                               monkeypatch):
    seen = []
    orig = threat._extreme_regime_now

    def spy(tile, wxf, now_s, cfg):
        seen.append(wxf.loc)
        return orig(tile, wxf, now_s, cfg)

    monkeypatch.setattr(threat, "_extreme_regime_now", spy)
    _cycle(tmp_path, wf.small_cfg(extreme_regime_enabled=True, **ON))
    assert seen == [wf.ORIGIN]


def test_a_failed_node_falls_back_to_the_centroid_with_a_note(tmp_path):
    _, probe, _ = _cycle(tmp_path, wf.small_cfg(**ON), name="probe")
    node = probe.weather_fields["inc-wx"].points[1]
    bad = LatLon(node.lat, node.lon)
    daemon, res, wx = _cycle(tmp_path, wf.small_cfg(**ON),
                             weather_provider=wf.GradientWeather(fail_at=[bad]))
    fld = res.weather_fields["inc-wx"]
    (z,) = [z for z, p in enumerate(fld.points) if not p.ok]
    assert (fld.points[z].lat, fld.points[z].lon) == (bad.lat, bad.lon)
    assert fld.forecasts[z] is fld.forecasts[0]
    assert "stub weather failure" in fld.points[z].error
    named = [n for n in res.notes if f"{bad.lat:.4f},{bad.lon:.4f}" in n]
    assert len(named) == 1 and "fire-centroid forecast" in named[0]
    assert "1 failed" in [n for n in res.notes if "forecast points" in n][0]
    assert not res.degraded and not res.operator_events
    assert len(res.assessments) == 3


def test_a_failed_centroid_is_the_legacy_provider_failure(tmp_path):
    daemon, res, wx = _cycle(
        tmp_path, wf.small_cfg(**ON),
        weather_provider=wf.GradientWeather(fail_at=[wf.ORIGIN]))
    assert res.degraded and res.weather_fields == {}
    assert [e.detail.get("step") for e in res.operator_events] == ["weather"]
    assert not res.assessments and "inc-wx" not in res.ensembles
    assert len(wx.calls) == 1                    # no lattice without a centroid


def test_a_domain_with_no_node_inside_spreads_on_the_centroid_alone(tmp_path):
    daemon, res, wx = _cycle(tmp_path, wf.small_cfg(weather_grid_km=400.0))
    _, off, _ = _cycle(tmp_path, wf.small_cfg(), name="off")
    fld = res.weather_fields["inc-wx"]
    assert len(fld.points) == 1 and len(wx.calls) == 1
    assert [n for n in res.notes if "no 400 km lattice node" in n]
    assert ss.cycle_digest(dataclasses.replace(res, notes=off.notes)) == \
        ss.cycle_digest(off)


def test_seeded_incidents_keep_their_single_point(tmp_path):
    clock = ss.SettableClock(wf.T0)
    daemon, monitor, feed, engine, wx = wf.make_stack(
        tmp_path / "s", wf.small_cfg(**ON), clock=clock, incidents=[])
    monitor.add_seed(ss.seed(loc=geo.destination(wf.C_EAST, 270.0, 3000.0)))
    clock.t = wf.T0 + 60.0
    res = daemon.run_cycle()
    iid = "seeded:test-near"
    assert iid in res.ensembles and res.weather_fields == {}
    assert [n for n in res.notes if n.startswith(f"incident {iid}: local "
                                                 f"weather not applied")]
    assert wx.calls == []              # the seed's synthetic weather: no fetch
    row = [r for r in daemon.state()["incidents"] if r["incident_id"] == iid]
    assert row and row[0]["weather"] is None


def test_the_off_domain_horizon_works_on_zoned_rasters(tmp_path):
    """SAFETY SEMANTICS 15 with zones on: every domain fire still gets its
    OffDomainHorizon from its P10 raster. Under the offmap world's uniform
    stub weather every zone carries the same forecast, so the zoned run is
    the per-fire run bit for bit."""
    import offmap_stack as om
    per_fire = dict(corridor_offraster_per_fire=True,
                    corridor_offraster_margin_min=60.0)
    clock = ss.SettableClock(om.T0)
    runs = {}
    for name, extra in (("plain", {}), ("zoned", dict(weather_grid_km=5.0))):
        daemon, monitor, feed, engine = om.make_stack(
            tmp_path / name, om.fail_closed_cfg(**per_fire, **extra),
            clock=clock)
        clock.t = om.T0 + 60.0
        runs[name] = daemon.run_cycle()
    plain, zoned = runs["plain"], runs["zoned"]
    assert set(zoned.offmap) == set(plain.offmap) == {"inc-near", "inc-slow"}
    assert set(zoned.weather_fields) == {"inc-near", "inc-slow"}
    assert any(len(f.points) > 1 for f in zoned.weather_fields.values())
    assert zoned.offmap == plain.offmap
    for iid in plain.ensembles:
        assert zoned.ensembles[iid].p10.data == plain.ensembles[iid].p10.data
    assert ss._canon(zoned.plan) == ss._canon(plain.plan)


# ---------------------------------------------------------------------------
# the NWS cache (process-level; knob on only)
# ---------------------------------------------------------------------------

class FakeNws:
    """api.weather.gov stub: points resolve to 0.05-deg 'gridpoints' whose
    wind grows with longitude; records every URL; fail_grids fails those
    gridpoint URLs (HTTP 503)."""

    GRID = "https://api.weather.gov/gridpoints/TST/{x},{y}"

    def __init__(self):
        self.urls = []
        self.fail_grids = set()

    def grid_url_for(self, lat, lon):
        return self.GRID.format(x=math.floor(lon / 0.05) + 5000,
                                y=math.floor(lat / 0.05))

    def __call__(self, url, headers):
        self.urls.append(url)
        if "/points/" in url:
            lat, lon = (float(v) for v in url.rsplit("/", 1)[1].split(","))
            body = {"properties": {"forecastGridData":
                                   self.grid_url_for(lat, lon)}}
            return 200, json.dumps(body).encode("utf-8")
        if url in self.fail_grids:
            return 503, b"unavailable"
        x = int(url.rsplit("/", 1)[1].split(",")[0])
        kmh = 10.0 + (x % 7) * 3.0
        grid = {"properties": {
            "windSpeed": {"uom": "wmoUnit:km_h-1", "values": [
                {"validTime": "2026-07-06T00:00:00+00:00/PT24H", "value": kmh}]},
            "windGust": {"uom": "wmoUnit:km_h-1", "values": [
                {"validTime": "2026-07-06T00:00:00+00:00/PT24H",
                 "value": kmh * 1.5}]},
            "windDirection": {"uom": "wmoUnit:degree_(angle)", "values": [
                {"validTime": "2026-07-06T00:00:00+00:00/PT24H", "value": 250.0}]},
            "relativeHumidity": {"uom": "wmoUnit:percent", "values": [
                {"validTime": "2026-07-06T00:00:00+00:00/PT24H", "value": 20.0}]},
            "temperature": {"uom": "wmoUnit:degC", "values": [
                {"validTime": "2026-07-06T00:00:00+00:00/PT24H", "value": 30.0}]},
        }}
        return 200, json.dumps(grid).encode("utf-8")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_the_cache_is_process_level_and_armed_only_by_the_knob():
    on = ResponseConfig(weather_grid_km=10.0)
    a = weather.NwsWeather(http_get=FakeNws(), cfg=on)
    b = weather.NwsWeather(http_get=FakeNws(), cfg=on)
    assert a.cache is b.cache is weather.nws_forecast_cache()
    assert weather.NwsWeather(http_get=FakeNws()).cache is None


def test_the_cache_reuses_the_static_mapping_and_ttls_the_gridpoints():
    http = FakeNws()
    clock = Clock()
    cache = weather.NwsForecastCache(clock=clock)
    cfg = ResponseConfig(weather_grid_km=10.0, weather_cache_ttl_s=1800.0)
    w = weather.NwsWeather(http_get=http, cfg=cfg, cache=cache)
    plain = weather.NwsWeather(http_get=FakeNws())
    p, q = LatLon(39.021, -121.031), LatLon(39.022, -121.032)  # one gridpoint
    first = w.forecast(p, 12.0)
    assert first.samples == plain.forecast(p, 12.0).samples   # same decode
    assert len(http.urls) == 2                                 # points + grid
    w.forecast(p, 12.0)
    assert len(http.urls) == 2                                 # both cached
    w.forecast(q, 24.0)                                        # new points URL,
    assert len(http.urls) == 3 and "/points/" in http.urls[-1]  # same gridpoint
    clock.t += 1800.0                                          # TTL expires
    w.forecast(p, 12.0)
    assert len(http.urls) == 4 and "/gridpoints/" in http.urls[-1]
    clock.t += 1800.0
    http.fail_grids.add(http.grid_url_for(p.lat, p.lon))
    with pytest.raises(RuntimeError):                          # never stale
        w.forecast(p, 12.0)
    st = cache.stats()
    # p's kept mapping was dropped with its failed refresh; q's stays
    assert st["points"] == 1 and st["grids"] == 1


def test_a_kept_mapping_whose_gridpoint_fails_is_re_resolved():
    http = FakeNws()
    clock = Clock()
    w = weather.NwsWeather(http_get=http, cache=weather.NwsForecastCache(
        clock=clock), cfg=ResponseConfig(weather_grid_km=10.0))
    p = LatLon(39.0, -121.0)
    w.forecast(p, 6.0)                                   # points + grid
    clock.t += 3600.0                                    # past the TTL
    http.fail_grids.add(http.grid_url_for(p.lat, p.lon))
    with pytest.raises(RuntimeError):
        w.forecast(p, 6.0)                               # kept mapping, grid fails
    assert [u.split("/")[3] for u in http.urls] == [
        "points", "gridpoints", "gridpoints"]
    http.fail_grids.clear()
    w.forecast(p, 6.0)                                   # re-resolves the point
    assert [u.split("/")[3] for u in http.urls[3:]] == ["points", "gridpoints"]


def test_a_zero_ttl_refetches_every_gridpoint_but_keeps_the_mapping():
    http = FakeNws()
    w = weather.NwsWeather(http_get=http, cache=weather.NwsForecastCache(),
                           cfg=ResponseConfig(weather_grid_km=10.0,
                                              weather_cache_ttl_s=0.0))
    for _ in range(3):
        w.forecast(LatLon(39.0, -121.0), 6.0)
    assert sum("/points/" in u for u in http.urls) == 1
    assert sum("/gridpoints/" in u for u in http.urls) == 3


# ---------------------------------------------------------------------------
# surfacing + flags
# ---------------------------------------------------------------------------

def test_state_and_world_summary_carry_the_points_only_when_armed(tmp_path):
    daemon, res, wx = _cycle(tmp_path, wf.small_cfg(**ON))
    st = daemon.state()
    rule = st["weather_grid"]
    assert rule["grid_km"] == 5.0 and rule["points_max"] == 16
    (row,) = st["incidents"]
    blk = row["weather"]
    fld = res.weather_fields["inc-wx"]
    assert blk["points_n"] == len(fld.points) and blk["failed"] == 0
    assert blk["spacing_km"] == fld.spacing_km
    p0 = blk["points"][0]
    assert p0["role"] == "primary" and p0["ok"] is True
    assert p0["source"] == "gradient"
    assert (p0["lat"], p0["lon"]) == (round(wf.ORIGIN.lat, 5),
                                      round(wf.ORIGIN.lon, 5))
    assert [h["h"] for h in p0["hours"]] == list(wxfield.LEADS_H)
    assert set(p0["hours"][0]) == {"h", "t_s", "wind_mps", "gust_mps",
                                   "dir_deg", "rh_pct", "temp_c"}
    across = blk["across_points"][0]
    # the gradient world: the points genuinely disagree
    assert across["wind_mps"][1] > across["wind_mps"][0]
    assert across["dir_arc_deg"] > 0.0 and across["rh_pct"][1] > across["rh_pct"][0]
    json.dumps(st, default=str)
    summary = daemon.world_summary()
    assert summary["weather_grid"] == rule
    (srow,) = summary["incidents"]
    assert srow["weather"] == blk
    assert summary["counts"]["weather"] == {"domains": 1,
                                            "points": len(fld.points),
                                            "failed": 0}


def test_arc_of_directions_wraps_north():
    assert wxfield._arc_deg([350.0, 10.0]) == 20.0
    assert wxfield._arc_deg([90.0]) == 0.0
    assert wxfield._arc_deg([0.0, 120.0, 240.0]) == 240.0


def test_world_page_draws_the_points_only_when_present():
    html = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "console", "world.html"),
        encoding="utf-8").read()
    assert "layers.weather = L.layerGroup().addTo(map);" in html
    assert "function drawWeather()" in html and "drawWeather();" in html
    assert "if (!w || !Array.isArray(w.points)) continue;" in html
    assert "function weatherLine(r)" in html and "const wl = weatherLine(r);" in html
    assert "if (!w) return \"\";" in html


def test_watch_flags_parse_and_default_off():
    from wgresponse.cli import _build_parser
    base = ["watch", "--aoi", "data/aoi_vail_aspen.json"]
    args = _build_parser().parse_args(base)
    assert args.weather_grid_km == 0.0 and args.weather_points_max is None
    args = _build_parser().parse_args(base + ["--weather-grid-km", "10",
                                              "--weather-points-max", "9"])
    assert args.weather_grid_km == 10.0 and args.weather_points_max == 9


def test_points_max_without_the_grid_is_refused(capsys):
    from wgresponse.cli import _build_parser, _cmd_watch
    aoi = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "aoi_vail_aspen.json")
    args = _build_parser().parse_args(
        ["watch", "--aoi", aoi, "--weather-points-max", "9"])
    assert _cmd_watch(args) == 2
    assert "--weather-points-max needs --weather-grid-km" in capsys.readouterr().err


def test_the_simulator_never_arms_local_weather():
    import inspect
    from wgresponse import simui
    src = inspect.getsource(simui)
    assert "weather_field" not in src and "weather_grid_km" not in src
