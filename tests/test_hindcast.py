"""Sprint-2 ticket D1 — units for the hindcast + quantile-calibration harness
(tools/hindcast_lib.py + tools/hindcast_harness.py pure seams).

NO NETWORK: every test here runs offline — parsers get canned payloads, the
engine-integration tests run on tiny synthetic tiles with hand-built weather,
and the landmark anchors use their offline scenario path. The live fetch layer
is exercised only by running the harness CLI manually (ticket D1 run log in
docs/calibration/hindcast_first_pass.md).
"""
from __future__ import annotations

import dataclasses

import pytest

from wgresponse import geo
from wgresponse.contracts import (
    EnsembleArrival,
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    UNREACHED,
    WeatherForecast,
    WeatherSample,
)
from tools import hindcast_lib as H
from tools.hindcast_harness import select_incidents


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _spec(nrows=4, ncols=6, cell_m=100.0, north=40.0, west=-120.0) -> GridSpec:
    return GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)


def _rect_ring(spec: GridSpec, r0: int, r1: int, c0: int, c1: int) -> list:
    """Ring whose interior contains exactly the CENTERS of rows r0..r1 x
    cols c0..c1 (0.2/0.8-cell margins keep centers off the ring itself)."""
    lat_step = spec.cell_m / geo.M_PER_DEG_LAT
    lat_hi = spec.north_lat - (r0 + 0.2) * lat_step
    lat_lo = spec.north_lat - (r1 + 0.8) * lat_step
    mid_lat = 0.5 * (lat_hi + lat_lo)
    lon_step = spec.cell_m / geo.m_per_deg_lon(mid_lat)
    lon_lo = spec.west_lon + (c0 + 0.2) * lon_step
    lon_hi = spec.west_lon + (c1 + 0.8) * lon_step
    return [LatLon(lat_hi, lon_lo), LatLon(lat_hi, lon_hi),
            LatLon(lat_lo, lon_hi), LatLon(lat_lo, lon_lo)]


def _uniform_tile(spec: GridSpec, code: float = 102.0) -> TerrainTile:
    n = spec.nrows * spec.ncols
    return TerrainTile(spec=spec,
                       fuel=Raster(spec, [code] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [-1.0] * n),
                       elev_m=Raster(spec, [100.0] * n),
                       source="test")


def _wx(t0_s: float, hours: int = 16, wind=8.0, wdir=270.0, rh=15.0,
        temp=30.0) -> WeatherForecast:
    samples = [WeatherSample(t_s=t0_s + h * 3600.0, wind_speed_mps=wind,
                             wind_gust_mps=wind * 1.3, wind_dir_deg=wdir,
                             rh_pct=rh, temp_c=temp)
               for h in range(hours)]
    return WeatherForecast(loc=LatLon(40.0, -120.0), samples=samples,
                           source="test")


# ---------------------------------------------------------------------------
# small laws
# ---------------------------------------------------------------------------

def test_subsample_ring_law_matches_threat_convention():
    ring = [LatLon(0.0, i) for i in range(1000)]
    out = H.subsample_ring(ring, 200)
    assert len(out) <= 200
    assert out[0] == ring[0]
    small = [LatLon(0.0, i) for i in range(7)]
    assert H.subsample_ring(small, 200) == small


def test_epoch_s_handles_ms_and_s():
    assert H.epoch_s(1_700_000_000_000) == pytest.approx(1_700_000_000.0)
    assert H.epoch_s(1_700_000_000) == pytest.approx(1_700_000_000.0)


def test_parse_iso8601_naive_is_utc():
    assert H.parse_iso8601_s("2025-08-02T06:00") == pytest.approx(
        H.parse_iso8601_s("2025-08-02T06:00:00Z"))


def test_fuel_class_groups():
    assert H.fuel_class(102) == "GR"
    assert H.fuel_class(122) == "GS"
    assert H.fuel_class(145) == "SH"
    assert H.fuel_class(165) == "TU"
    assert H.fuel_class(183) == "TL"
    assert H.fuel_class(202) == "SB"
    assert H.fuel_class(91) == "NB"
    assert H.fuel_class(99) == "NB"


# ---------------------------------------------------------------------------
# polygon rasterization + geometry stats
# ---------------------------------------------------------------------------

def test_polygon_interior_mask_square():
    spec = _spec()
    ring = _rect_ring(spec, 1, 2, 1, 3)
    mask = H.polygon_interior_mask(spec, [ring])
    expected = {r * spec.ncols + c for r in (1, 2) for c in (1, 2, 3)}
    assert mask == expected


def test_polygon_interior_mask_hole_even_odd():
    spec = _spec(nrows=8, ncols=8)
    outer = _rect_ring(spec, 0, 7, 0, 7)
    hole = _rect_ring(spec, 3, 4, 3, 4)
    mask = H.polygon_interior_mask(spec, [outer, hole])
    assert (3 * 8 + 3) not in mask and (4 * 8 + 4) not in mask
    assert (0 * 8 + 0) in mask and (7 * 8 + 7) in mask
    assert len(mask) == 64 - 4


def test_polygon_interior_mask_degenerate_returns_empty():
    spec = _spec()
    assert H.polygon_interior_mask(spec, [[LatLon(40.0, -120.0),
                                           LatLon(40.0, -119.9)]]) == set()
    assert H.polygon_interior_mask(spec, []) == set()


def test_boundary_cells_of_a_block():
    spec = _spec(nrows=6, ncols=6)
    mask = {r * 6 + c for r in range(1, 5) for c in range(1, 5)}
    b = H.boundary_cells(mask, spec)
    interior = {r * 6 + c for r in (2, 3) for c in (2, 3)}
    assert set(b) == mask - interior
    assert b == sorted(b)


def test_max_dist_m_on_a_strip():
    spec = _spec(nrows=1, ncols=11, cell_m=50.0)
    d = H.max_dist_m([10], [0], spec)
    assert d == pytest.approx(10 * 50.0)
    assert H.max_dist_m([], [0], spec) == 0.0
    assert H.max_dist_m([3], [], spec) == 0.0


# ---------------------------------------------------------------------------
# ArcGIS query builders + parsers (canned payloads; pins the researched layer)
# ---------------------------------------------------------------------------

def test_daily_layer_url_is_the_time_series_layer():
    # The research finding this harness is built on: the *Daily* public layer
    # is the only WFIGS perimeter service keeping every capture as a row.
    assert "WFIGS_Daily_Perimeters_Public" in H.DAILY_PERIMETERS_URL
    url = H.candidates_query_url(min_acres=5000, state="US-CA")
    assert "poly_GISAcres+%3E%3D+5000" in url
    assert "US-CA" in url
    assert "groupByFieldsForStatistics" in url


def test_parse_candidates_drops_null_irwin_and_sorts():
    payload = {"features": [
        {"attributes": {"attr_IrwinID": None, "attr_IncidentName": "BULK",
                        "n_rows": 999, "max_acres": 1.0,
                        "t_min": 1e12, "t_max": 1e12}},
        {"attributes": {"attr_IrwinID": "{B}", "attr_IncidentName": "Two",
                        "n_rows": 5, "max_acres": 9000.0,
                        "t_min": 1.7e12, "t_max": 1.71e12}},
        {"attributes": {"attr_IrwinID": "{A}", "attr_IncidentName": "One",
                        "n_rows": 12, "max_acres": 20000.0,
                        "t_min": 1.7e12, "t_max": 1.72e12}},
    ]}
    out = H.parse_candidates(payload)
    assert [c[0] for c in out] == ["{A}", "{B}"]
    assert out[0][2] == 12 and out[0][3] == 20000.0
    assert out[0][4] == pytest.approx(1.7e9)


def test_parse_series_attrs_fallback_and_sort():
    payload = {"features": [
        {"attributes": {"OBJECTID": 3, "poly_PolygonDateTime": 2_000_000_000_000,
                        "poly_GISAcres": 300.0}},
        {"attributes": {"OBJECTID": 1, "poly_PolygonDateTime": None,
                        "poly_DateCurrent": 1_000_000_000_000,
                        "poly_GISAcres": 100.0}},
        {"attributes": {"OBJECTID": 9, "poly_PolygonDateTime": None,
                        "poly_DateCurrent": None}},          # dropped
        {"attributes": {"poly_PolygonDateTime": 1_500_000_000_000}},  # no OID
    ]}
    rows = H.parse_series_attrs(payload)
    assert rows == [(1, pytest.approx(1_000_000_000.0), 100.0),
                    (3, pytest.approx(2_000_000_000.0), 300.0)]


def test_pick_snapshot_times_growth_dt_and_chain_break():
    h = 3600.0
    rows = [
        (1, 0.0, 1000.0),
        (2, 2 * h, 5000.0),      # too soon (< 6 h) despite growth
        (3, 8 * h, 1040.0),      # >= 6 h but growth < max(8%, 300 ac)
        (4, 12 * h, 2000.0),     # picked (growth 1000 >= 300)
        (5, 12 * h, 2500.0),     # same capture time: merged into oid group
        (6, 20 * h, 2600.0),     # growth 100 < 300 — not picked
        (7, 200 * h, 9000.0),    # gap > 90 h: chain break, fresh T0
        (8, 210 * h, 12000.0),   # picked (vs fresh T0)
    ]
    picks = H.pick_snapshot_times(rows)
    assert [(t, oids) for t, oids, _ in picks] == [
        (0.0, [1]), (12 * h, [4, 5]), (200 * h, [7]), (210 * h, [8])]
    # acres of a merged group is the max over its rows
    assert picks[1][2] == 2500.0
    # max_snapshots cap
    assert len(H.pick_snapshot_times(rows, max_snapshots=2)) == 2


def test_geometry_parse_skips_dirty_vertices_and_small_rings():
    payload = {"features": [
        {"attributes": {"OBJECTID": 7, "poly_PolygonDateTime": 1.7e12},
         "geometry": {"rings": [
             [[-120.0, 40.0], [None, None], [-120.0, 40.1],
              ["bad", 40.1], [-119.9, 40.05]],
             [[-120.0, 40.0], [-120.0, 40.1]],       # < 3 clean verts: dropped
         ]}},
        {"attributes": {"OBJECTID": 8}, "geometry": {"rings": []}},  # no rings
    ]}
    out = H.parse_snapshot_features(payload)
    assert list(out) == [7]
    t_s, rings = out[7]
    assert t_s == pytest.approx(1.7e9)
    assert len(rings) == 1 and len(rings[0]) == 3
    assert rings[0][0] == LatLon(40.0, -120.0)       # lat/lon order from esri x/y


# ---------------------------------------------------------------------------
# Open-Meteo archive builder + parser
# ---------------------------------------------------------------------------

def test_archive_weather_url_pins_host_and_units():
    url = H.archive_weather_url(LatLon(34.5, -119.7), "2025-07-01", "2025-07-05")
    assert url.startswith(H.ARCHIVE_WEATHER_URL)
    assert "archive-api.open-meteo.com" in url
    assert "wind_speed_unit=ms" in url
    assert "timezone=UTC" in url
    assert "start_date=2025-07-01" in url and "end_date=2025-07-05" in url
    # licensing note must be pinned in the module (non-commercial free tier)
    assert "NON-COMMERCIAL" in H.__doc__


def test_parse_archive_weather_conventions():
    payload = {"hourly": {
        "time": ["2025-07-01T00:00", "2025-07-01T01:00", "2025-07-01T02:00"],
        "temperature_2m": [30.0, None, 32.0],
        "relative_humidity_2m": [20.0, 21.0, 18.0],
        "wind_speed_10m": [8.0, 9.0, 10.0],
        "wind_direction_10m": [270.0, 275.0, 360.0],
        "wind_gusts_10m": [6.0, 12.0, None],
    }}
    wx = H.parse_archive_weather(payload, LatLon(34.5, -119.7))
    assert wx.source == "open-meteo-archive"
    assert len(wx.samples) == 2                      # null-temp row skipped
    s0, s2 = wx.samples
    assert s0.wind_gust_mps == 8.0                   # gust floored at wind
    assert s2.wind_gust_mps == 10.0                  # null gust -> wind
    assert s2.wind_dir_deg == 0.0                    # % 360
    assert s2.t_s - s0.t_s == pytest.approx(7200.0)
    with pytest.raises(RuntimeError):
        H.parse_archive_weather({"hourly": {"time": []}}, LatLon(0, 0))
    with pytest.raises(RuntimeError):
        H.parse_archive_weather({}, LatLon(0, 0))


# ---------------------------------------------------------------------------
# pair scoring (hand-built ensemble; every classification branch)
# ---------------------------------------------------------------------------

def _hand_ens(spec, nominal, p10, p50, p90, members=None, t0_s=0.0):
    mem = [Raster(spec, list(nominal))]
    for m in members or []:
        mem.append(Raster(spec, list(m)))
    return EnsembleArrival(spec=spec, members=mem,
                           p10=Raster(spec, list(p10)),
                           p50=Raster(spec, list(p50)),
                           p90=Raster(spec, list(p90)), t0_s=t0_s)


def test_score_pair_classification_branches():
    # 4x6 grid: engine-T0 = cols 0..1 (nominal == t0 there), T1 polygon
    # covers cols 0..3 => newly-burned = cols 2..3 x 4 rows = 8 cells.
    # Cell-by-cell design (col, row -> branch under test):
    #   c2 r0 certain           band [t0-10, t1+10] contains [t0, t1]
    #   c2 r1 possible-only     band starts inside the window
    #   c2 r2 late miss         p10 > t1
    #   c2 r3 early miss        p90 < t0
    #   c3 r0 never-reached     p10 = inf (also a late miss)
    #   c3 r1 p90-unreached     p10 <= t0, p90 = inf (certain via inf band)
    #   c3 r2 certain
    #   c3 r3 non-burnable      NB 91 — counted, excluded from scoring
    spec = _spec(nrows=4, ncols=6)
    n = spec.nrows * spec.ncols
    t0, t1 = 1000.0, 1000.0 + 12 * 3600.0
    inside = t0 + 6 * 3600.0
    nominal = [UNREACHED] * n
    p10 = [UNREACHED] * n
    p50 = [UNREACHED] * n
    p90 = [UNREACHED] * n
    fuel = [102.0] * n
    col2 = {0: (t0 - 10.0, inside, t1 + 10.0),
            1: (inside, inside + 3600.0, t1 + 10.0),
            2: (t1 + 100.0, t1 + 200.0, t1 + 300.0),
            3: (t0 - 300.0, t0 - 200.0, t0 - 100.0)}
    col3 = {0: (UNREACHED, UNREACHED, UNREACHED),
            1: (t0 - 10.0, inside, UNREACHED),
            2: (t0 - 10.0, inside, t1 + 10.0),
            3: (t0 - 10.0, inside, t1 + 10.0)}
    for r in range(4):
        base = r * 6
        nominal[base + 0] = t0
        nominal[base + 1] = t0
        p10[base + 2], p50[base + 2], p90[base + 2] = col2[r]
        p10[base + 3], p50[base + 3], p90[base + 3] = col3[r]
    fuel[3 * 6 + 3] = 91.0
    tile = _uniform_tile(spec)
    tile.fuel = Raster(spec, fuel)
    snap0 = H.PerimeterSnapshot(t_s=t0, rings=[])
    snap1 = H.PerimeterSnapshot(t_s=t1, rings=[_rect_ring(spec, 0, 3, 0, 3)])
    ens = _hand_ens(spec, nominal, p10, p50, p90,
                    members=[p50, p90], t0_s=t0)
    m = H.score_pair(tile, ens, snap0, snap1, "inc-1", name="T")
    assert m.n_new == 8
    assert m.n_new_nonburnable == 1          # c3 r3
    assert m.n_new_burnable == 7
    assert m.covered_certain == 3            # c2r0, c3r1 (inf band), c3r2
    assert m.covered_possible == 4           # + c2r1
    assert m.late_miss == 2                  # c2r2 + never-reached c3r0
    assert m.never_reached == 1              # c3r0
    assert m.early_miss == 1                 # c2r3
    assert m.p90_unreached == 1              # c3r1
    assert m.p10_not_after_window == 5       # c2r0, c2r1, c2r3, c3r1, c3r2
    assert m.p10_before_window == 4          # c2r0, c2r3, c3r1, c3r2
    assert m.p50_in_window == 4              # c2r0, c2r1, c3r1, c3r2
    assert m.frac(m.covered_certain) == pytest.approx(3 / 7)
    assert sum(m.pit_hist) == m.n_new_burnable
    assert m.by_fuel_class["NB"]["n"] == 1
    assert m.by_fuel_class["GR"]["n"] == 7


def test_score_pair_late_never_and_possible_counts_precise():
    # Same fixture, pinned arithmetic — kept separate so a mis-count in the
    # branchy test above cannot hide behind its own comment.
    spec = _spec(nrows=4, ncols=6)
    n = spec.nrows * spec.ncols
    t0, t1 = 0.0, 3600.0
    nominal = [UNREACHED] * n
    for r in range(4):
        nominal[r * 6 + 0] = t0
    p10 = [UNREACHED] * n
    p50 = [UNREACHED] * n
    p90 = [UNREACHED] * n
    for r in range(4):
        i = r * 6 + 1
        p10[i], p50[i], p90[i] = (t1 + 1.0, t1 + 2.0, t1 + 3.0)  # late
    tile = _uniform_tile(spec)
    snap1 = H.PerimeterSnapshot(t_s=t1, rings=[_rect_ring(spec, 0, 3, 0, 1)])
    ens = _hand_ens(spec, nominal, p10, p50, p90, t0_s=t0)
    m = H.score_pair(tile, ens, H.PerimeterSnapshot(t_s=t0, rings=[]),
                     snap1, "inc-2")
    assert m.n_new_burnable == 4
    assert m.late_miss == 4 and m.never_reached == 0
    assert m.covered_possible == 0 and m.covered_certain == 0
    assert m.p10_not_after_window == 0
    # never-reached variant
    p10b = [UNREACHED] * n
    ens2 = _hand_ens(spec, nominal, p10b, p10b, p10b, t0_s=t0)
    m2 = H.score_pair(tile, ens2, H.PerimeterSnapshot(t_s=t0, rings=[]),
                      snap1, "inc-2")
    assert m2.never_reached == 4 and m2.late_miss == 4


def test_score_pair_monotonicity_guard_unscored():
    spec = _spec(nrows=4, ncols=6)
    n = spec.nrows * spec.ncols
    t0, t1 = 0.0, 3600.0
    nominal = [UNREACHED] * n
    for r in range(4):                       # engine T0 = cols 0..1
        nominal[r * 6 + 0] = t0
        nominal[r * 6 + 1] = t0
    tile = _uniform_tile(spec)
    # T1 polygon covers cols 4..5 only — T0 not contained: partial upload
    snap1 = H.PerimeterSnapshot(t_s=t1, rings=[_rect_ring(spec, 0, 3, 4, 5)])
    ens = _hand_ens(spec, nominal, nominal, nominal, nominal, t0_s=t0)
    m = H.score_pair(tile, ens, H.PerimeterSnapshot(t_s=t0, rings=[]),
                     snap1, "inc-3")
    assert m.n_new_burnable == 0
    assert any("NON-MONOTONE" in note for note in m.notes)


def test_pool_metrics_weighting_and_empty():
    assert H.pool_metrics([]) == {"n_pairs": 0, "n_cells": 0}
    a = H.PairMetrics(n_new_burnable=10, covered_certain=5,
                      covered_possible=10, p10_not_after_window=10,
                      p10_before_window=5, late_miss=0, early_miss=0,
                      pit_hist=[10, 0], obs_head_m=1000.0, head_m_p50=500.0)
    b = H.PairMetrics(n_new_burnable=30, covered_certain=0,
                      covered_possible=30, p10_not_after_window=15,
                      p10_before_window=0, late_miss=15, early_miss=0,
                      pit_hist=[0, 30], obs_head_m=1000.0, head_m_p50=1000.0)
    pooled = H.pool_metrics([a, b])
    assert pooled["n_pairs"] == 2 and pooled["n_cells"] == 40
    assert pooled["covered_certain_frac"] == pytest.approx(5 / 40)
    assert pooled["p10_not_after_window_frac"] == pytest.approx(25 / 40)
    assert pooled["late_miss_frac"] == pytest.approx(15 / 40)
    assert pooled["pit_hist"] == [10, 30]
    # TRI-MODEL REVIEW 2026-08-31 fix: head ratios here are [0.5, 1.0]; the
    # TRUE median (statistics.median) of two values is their average, 0.75.
    # This assertion used to read 1.0 because pool_metrics computed
    # sorted[n//2] (the UPPER-MIDDLE value on an even n) instead of the
    # median — exactly the published-record bug the fix corrects, so the
    # expectation here is deliberately updated, not preserved.
    assert pooled["median_head_ratio_p50"] == pytest.approx(0.75)
    # honest rename of covered_possible (== p10_not_after_window_frac by
    # construction in REAL score_pair output — see
    # test_score_pair_late_never_and_possible_counts_precise; this fixture
    # hand-sets covered_possible independently of p10_not_after_window, so
    # that identity is not asserted here)
    assert pooled["p10_adequacy_frac"] == pytest.approx(25 / 40)
    assert set(pooled["deprecated_metrics"]) == {
        "covered_certain_frac", "covered_possible_frac", "early_miss_frac"}
    # no null-model coverage was computed for these hand-built pairs
    # (null_new_covered defaults to 0) -> skill is undefined, not 0
    assert pooled["null_adequacy_frac"] == 0.0
    assert pooled["skill"] is None


# ---------------------------------------------------------------------------
# TRI-MODEL REVIEW 2026-08-31 fix-program item 5: the honest scorecard
# (true medians, null-model dilation, PIT summary, window-length bins,
# incident concentration) — see docs/calibration/SCORECARD.md.
# ---------------------------------------------------------------------------

def test_pool_metrics_true_medians_not_upper_middle():
    """statistics.median for every ratio family, not sorted[n//2] (the old
    upper-middle selector overstated the published head-ratio median by
    2-10%, always in the flattering direction)."""
    heads_p50 = [100.0, 200.0, 300.0, 900.0]
    heads_p10 = [50.0, 150.0, 250.0, 850.0]
    heads_nom = [80.0, 180.0, 280.0, 880.0]
    areas_p50 = [10.0, 20.0, 30.0, 90.0]
    areas_p10 = [5.0, 15.0, 25.0, 85.0]
    pairs = [H.PairMetrics(n_new_burnable=1, p10_not_after_window=1,
                           obs_head_m=1000.0, head_m_p50=heads_p50[i],
                           head_m_p10=heads_p10[i],
                           head_m_nominal=heads_nom[i],
                           obs_new_area_ac=100.0,
                           modeled_new_area_ac_p50=areas_p50[i],
                           modeled_new_area_ac_p10=areas_p10[i])
             for i in range(4)]
    pooled = H.pool_metrics(pairs)
    # true median of 4 sorted ratios = average of the middle two
    assert pooled["median_head_ratio_p50"] == pytest.approx((0.2 + 0.3) / 2)
    assert pooled["median_head_ratio_p10"] == pytest.approx((0.15 + 0.25) / 2)
    assert pooled["median_head_ratio_nominal"] == pytest.approx((0.18 + 0.28) / 2)
    assert pooled["median_area_ratio_p50"] == pytest.approx((0.2 + 0.3) / 2)
    assert pooled["median_area_ratio_p10"] == pytest.approx((0.15 + 0.25) / 2)
    # the deprecated upper-middle selector (sorted[n//2] on n=4 -> index 2)
    # would have picked the THIRD ratio, strictly ABOVE the true median here
    old_upper_middle = sorted(p.head_ratio_p50() for p in pairs)[4 // 2]
    assert old_upper_middle == pytest.approx(0.3)
    assert pooled["median_head_ratio_p50"] < old_upper_middle


def test_squared_edt_matches_euclidean_distance():
    """Single-cell seed -> squared_edt is exactly the squared Euclidean
    distance from that cell (Felzenszwalb-Huttenlocher two-pass transform,
    ported from the reviewed scratch_review/rv2sci_a2_null.py `edt2`)."""
    nrows, ncols = 11, 11
    seed = {5 * ncols + 5}
    d2 = H.squared_edt(seed, nrows, ncols)
    for r, c in [(5, 5), (4, 5), (5, 6), (4, 4), (0, 0), (2, 5)]:
        assert d2[r * ncols + c] == pytest.approx((r - 5) ** 2 + (c - 5) ** 2)


def test_null_model_covered_square_t0_known_dilation():
    """A 3x3 SQUARE T0 region in a 15x15 all-burnable grid has EXACTLY 12
    cells at squared-distance 1 (the 4-connected unit ring around each
    edge; diagonal-adjacent corners are at distance 2, strictly farther).
    Budget=12 dilates to exactly that ring, unambiguously (no ties need
    breaking since precisely 12 cells share the minimum distance). Of five
    target cells, exactly the two on the unit ring should be covered."""
    nrows, ncols = 15, 15
    t0 = {r * ncols + c for r in range(6, 9) for c in range(6, 9)}
    burnable = [True] * (nrows * ncols)

    def idx(r, c):
        return r * ncols + c

    # d2==1 (covered at budget=12): directly above/right of an edge cell
    covered_targets = [idx(5, 7), idx(7, 9)]
    # d2==2 (diagonal corners) and d2==9 (far): NOT covered at budget=12
    uncovered_targets = [idx(5, 5), idx(9, 9), idx(7, 11)]
    targets = covered_targets + uncovered_targets
    n_covered = H.null_model_covered(t0, burnable, targets, nrows, ncols, 12)
    assert n_covered == len(covered_targets) == 2
    # the T0 seed itself can never be "covered" (it is not newly-burned)
    assert H.null_model_covered(t0, burnable, [idx(7, 7)], nrows, ncols,
                                nrows * ncols) == 0
    # zero budget / empty seed dilate to nothing
    assert H.null_model_covered(t0, burnable, targets, nrows, ncols, 0) == 0
    assert H.null_model_covered(set(), burnable, targets, nrows, ncols, 12) == 0


def test_null_model_covered_excludes_nonburnable_and_ties_by_index():
    """Non-burnable cells can never be dilated into (mirrors the engine,
    which never reports an arrival for a non-burnable cell), and ties at
    equal distance break deterministically by ascending cell index."""
    nrows, ncols = 15, 15
    seed = {7 * ncols + 7}

    def idx(r, c):
        return r * ncols + c

    burnable = [True] * (nrows * ncols)
    # the four unit-distance neighbors, ascending by index: (6,7),(7,6),(7,8),(8,7)
    assert idx(6, 7) < idx(7, 6) < idx(7, 8) < idx(8, 7)
    n = H.null_model_covered(seed, burnable, [idx(6, 7)], nrows, ncols, 1)
    assert n == 1        # budget=1 selects the smallest-index unit neighbor
    burnable[idx(6, 7)] = False
    n2 = H.null_model_covered(seed, burnable, [idx(6, 7)], nrows, ncols, 1)
    assert n2 == 0       # excluded from candidates even though closest
    n3 = H.null_model_covered(seed, burnable, [idx(7, 6)], nrows, ncols, 1)
    assert n3 == 1        # budget now lands on the next-smallest index


def test_pool_metrics_pit_summary_extremes_and_flatness():
    a = H.PairMetrics(n_new_burnable=100, p10_not_after_window=100,
                      pit_hist=[40, 5, 5, 50])
    pooled = H.pool_metrics([a])
    ps = pooled["pit_summary"]
    assert ps["n_bins"] == 4
    assert ps["n_total"] == 100
    assert ps["lo_bin_frac"] == pytest.approx(0.4)
    assert ps["hi_bin_frac"] == pytest.approx(0.5)
    assert ps["extreme_bins_frac"] == pytest.approx(0.9)
    assert ps["chi2_df"] == 3
    # chi2 = sum((observed-expected)^2/expected), expected=100/4=25
    assert ps["chi2_flatness"] == pytest.approx(66.0)
    # a perfectly flat/uniform PIT histogram has chi2 == 0
    uniform = H.pool_metrics([H.PairMetrics(n_new_burnable=100,
                                            p10_not_after_window=100,
                                            pit_hist=[25, 25, 25, 25])])
    assert uniform["pit_summary"]["chi2_flatness"] == pytest.approx(0.0)
    assert ps["chi2_flatness"] > uniform["pit_summary"]["chi2_flatness"]


def test_pool_metrics_window_length_bins():
    mk = lambda dt, n, late: H.PairMetrics(dt_h=dt, n_new_burnable=n,
                                           p10_not_after_window=n - late,
                                           late_miss=late)
    pairs = [mk(3.0, 100, 50),       # <=6h bin (boundary: dt_h<=6.0)
             mk(6.0, 999, 0),        # also <=6h (inclusive boundary)
             mk(9.0, 200, 20),       # 6-12h bin
             mk(18.0, 300, 3),       # 12-24h bin
             mk(48.0, 400, 1)]       # >24h bin
    pooled = H.pool_metrics(pairs)
    bins = {b["label"]: b for b in pooled["window_length_bins"]}
    assert [b["label"] for b in pooled["window_length_bins"]] == [
        "<=6h", "6-12h", "12-24h", ">24h"]
    assert bins["<=6h"]["pairs"] == 2
    assert bins["<=6h"]["cells"] == 100 + 999
    assert bins["<=6h"]["late_miss"] == pytest.approx(50 / 1099)
    assert bins["6-12h"]["pairs"] == 1 and bins["6-12h"]["cells"] == 200
    assert bins["6-12h"]["p10_adequacy"] == pytest.approx(180 / 200)
    assert bins["12-24h"]["cells"] == 300
    assert bins[">24h"]["cells"] == 400
    assert bins[">24h"]["p10_adequacy"] == pytest.approx(399 / 400)


def test_pool_metrics_equal_incident_mean_and_leave_one_out():
    # incident A: 2 pairs, cell-weighted p10_adequacy = (250+200)/900 = 0.5
    # incident B: 1 pair, 95/100 = 0.95
    a1 = H.PairMetrics(incident="A", n_new_burnable=500,
                       p10_not_after_window=250, late_miss=250)
    a2 = H.PairMetrics(incident="A", n_new_burnable=400,
                       p10_not_after_window=200, late_miss=200)
    b1 = H.PairMetrics(incident="B", n_new_burnable=100,
                       p10_not_after_window=95, late_miss=5)
    pooled = H.pool_metrics([a1, a2, b1])
    assert pooled["n_incidents"] == 2
    # overall cell-weighted = (250+200+95)/1000 = 0.545
    assert pooled["p10_adequacy_frac"] == pytest.approx(0.545)
    # equal-incident mean = (0.5 + 0.95) / 2 = 0.725 -- deliberately DIFFERENT
    # from the cell-weighted number: A's 900 cells dominate the pool while
    # counting equally with B's 100 cells here (the exact "cell count is not
    # inferential sample size" finding).
    assert pooled["equal_incident_mean_p10_adequacy"] == pytest.approx(0.725)
    # leave A out -> only B remains: 95/100=0.95; leave B out -> only A: 0.5
    assert pooled["loo_incident_p10_adequacy_min"] == pytest.approx(0.5)
    assert pooled["loo_incident_p10_adequacy_max"] == pytest.approx(0.95)
    # incident concentration: A has 900/1000 = 90% of cells and 450/455 of
    # all late-miss cells (250+200 of a 250+200+5 pooled total)
    assert pooled["largest_incident_share_of_cells"] == pytest.approx(0.9)
    assert pooled["largest_incident_share_of_late_miss"] == pytest.approx(
        450 / 455)


def test_pool_metrics_leave_one_out_single_incident_is_none():
    """With only one incident, leaving it out leaves zero cells -- the
    leave-one-out range is undefined (None), not a divide-by-zero crash."""
    a = H.PairMetrics(incident="ONLY", n_new_burnable=10,
                      p10_not_after_window=9, late_miss=1)
    pooled = H.pool_metrics([a])
    assert pooled["n_incidents"] == 1
    assert pooled["loo_incident_p10_adequacy_min"] is None
    assert pooled["loo_incident_p10_adequacy_max"] is None


# ---------------------------------------------------------------------------
# engine-integration replay on a tiny synthetic tile (offline, deterministic)
# ---------------------------------------------------------------------------

def test_replay_pair_runs_engine_deterministically():
    spec = _spec(nrows=40, ncols=40, cell_m=100.0)
    tile = _uniform_tile(spec)
    t0 = 1_755_000_000.0
    wx = _wx(t0 - 3600.0, hours=16)
    snap0 = H.PerimeterSnapshot(t_s=t0, rings=[_rect_ring(spec, 15, 24, 2, 6)])
    snap1 = H.PerimeterSnapshot(t_s=t0 + 6 * 3600.0,
                                rings=[_rect_ring(spec, 12, 27, 2, 20)])
    cfg = ResponseConfig()
    m1 = H.replay_pair(tile, wx, snap0, snap1, cfg, "IRWIN-T", name="tiny")
    m2 = H.replay_pair(tile, wx, snap0, snap1, cfg, "IRWIN-T", name="tiny")
    assert dataclasses.asdict(m1) == dataclasses.asdict(m2)   # deterministic
    assert m1.n_new > 0 and m1.n_new_burnable == m1.n_new     # uniform GR2
    assert m1.covered_certain <= m1.covered_possible <= m1.n_new_burnable
    assert m1.late_miss + m1.early_miss <= m1.n_new_burnable
    assert m1.obs_head_m > 0.0
    assert sum(m1.pit_hist) == m1.n_new_burnable
    assert len(m1.pit_hist) == cfg.ensemble_n + 1
    # engine seeded the T0 interior (the A5 law: nominal arrival == t0 there),
    # and those cells are excluded from newly-burned
    assert m1.n_new < 40 * 40


def test_replay_pair_different_incident_id_changes_members_not_law():
    spec = _spec(nrows=30, ncols=30, cell_m=100.0)
    tile = _uniform_tile(spec)
    t0 = 1_755_000_000.0
    wx = _wx(t0 - 3600.0, hours=12)
    snap0 = H.PerimeterSnapshot(t_s=t0, rings=[_rect_ring(spec, 12, 17, 2, 5)])
    snap1 = H.PerimeterSnapshot(t_s=t0 + 4 * 3600.0,
                                rings=[_rect_ring(spec, 10, 19, 2, 15)])
    cfg = ResponseConfig()
    ma = H.replay_pair(tile, wx, snap0, snap1, cfg, "IRWIN-A")
    mb = H.replay_pair(tile, wx, snap0, snap1, cfg, "IRWIN-B")
    # observed side identical; modeled dispersion may differ (member seeds
    # are a pure function of incident_id per contracts)
    assert ma.n_new == mb.n_new
    assert ma.obs_head_m == mb.obs_head_m


# ---------------------------------------------------------------------------
# landmark anchors (offline)
# ---------------------------------------------------------------------------

def test_point_ros_reproduces_review_f3_measurement():
    """The Council-1 review (model_fidelity_review.md F3 / exp3) measured GR2
    at Santa Ana conditions (12 m/s, 10% RH) = 2.98 km/h -> 0.47x the Tubbs
    6.4 km/h sustained head. The anchor scenario must reproduce that number
    through the production chain."""
    cfg = ResponseConfig()
    ros = H.point_ros_kmh(102, H.TUBBS, cfg)
    assert ros == pytest.approx(2.98, abs=0.05)
    assert ros / H.TUBBS.observed_head_kmh == pytest.approx(0.47, abs=0.01)


def test_run_anchor_deterministic_and_documents_late_polarity():
    quick = dataclasses.replace(H.TUBBS, cell_m=200.0, width_km=8.0,
                                height_km=3.0, duration_h=1.0)
    cfg = ResponseConfig()
    r1 = H.run_anchor(quick, cfg, spotting=False)
    r2 = H.run_anchor(quick, cfg, spotting=False)
    assert [dataclasses.asdict(a) for a in r1] == \
           [dataclasses.asdict(a) for a in r2]
    labels = [a.fuel_label for a in r1]
    assert labels == ["GR2", "SH5", "GR2/SH5 mix"]
    gr2 = r1[0]
    # The F3 finding: engine grass head runs well under the Tubbs anchor.
    assert 0.1 < gr2.ratio < 0.9
    assert gr2.point_ros_kmh == pytest.approx(2.98, abs=0.05)
    for a in r1:
        assert a.engine > 0.0
        assert not a.spotting


def test_camp_anchor_scores_area_growth():
    quick = dataclasses.replace(H.CAMP, cell_m=200.0, width_km=8.0,
                                height_km=4.0, duration_h=0.5)
    out = H.run_anchor(quick, ResponseConfig(), spotting=False)
    assert all("ac/h" in a.metric for a in out)
    assert all(a.observed == 6600.0 for a in out)
    assert all(a.engine >= 0.0 for a in out)


# ---------------------------------------------------------------------------
# recommendations + report rendering
# ---------------------------------------------------------------------------

def test_recommendations_no_data_is_honest():
    recs = H.recommendations({"n_pairs": 0, "n_cells": 0}, {}, [])
    joined = " ".join(recs)
    assert "NO REPLAY EVIDENCE" in joined
    assert "Keep" in joined
    # No unconditional tuning advice without data
    assert "INCREASE" not in joined


def test_recommendations_below_target_points_at_widening():
    pooled = {"n_pairs": 4, "n_cells": 1000, "nonburnable_frac": 0.1,
              "covered_certain_frac": 0.2, "covered_possible_frac": 0.6,
              "late_miss_frac": 0.4, "early_miss_frac": 0.02,
              "never_reached_frac": 0.2, "p90_unreached_frac": 0.1,
              "p50_in_window_frac": 0.3,
              "p10_not_after_window_frac": 0.55,
              "p10_before_window_frac": 0.25,
              "pit_hist": [400, 100, 100, 100, 100, 100, 100],
              "median_head_ratio_p50": 0.5}
    wide = dict(pooled, p10_not_after_window_frac=0.8)
    anchors = [H.AnchorResult(anchor="tubbs", fuel_label="GR2",
                              spotting=False, metric="head km/h over 3 h",
                              observed=6.4, engine=3.0, ratio=0.47,
                              point_ros_kmh=2.98)]
    recs = H.recommendations(pooled, wide, anchors)
    joined = " ".join(recs)
    assert "INCREASE wind_perturb_frac" in joined    # wide gain 25pp: helps
    assert "80%" in joined                           # widened-variant evidence
    assert "LATE-dominant" in joined
    assert "STRUCTURALLY ~0" in joined               # sufficient-bound honesty
    assert "0.47x" in joined
    assert "do not ship" in joined.lower()


def test_recommendations_tiny_wide_gain_is_a_level_problem():
    """When widening the band barely moves the necessary p10 bound, the
    honest conclusion is an ROS-LEVEL deficit (F3), not band width — the
    harness must NOT tell anyone to inflate the perturbation knobs."""
    pooled = {"n_pairs": 14, "n_cells": 1000,
              "p10_not_after_window_frac": 0.69,
              "p10_before_window_frac": 0.0,
              "late_miss_frac": 0.31, "early_miss_frac": 0.0,
              "pit_hist": [400, 50, 50, 50, 50, 50, 350],
              "by_fuel_class": {"GR": {"n": 600, "late": 315, "cert": 0,
                                       "poss": 280},
                                "SH": {"n": 600, "late": 120, "cert": 0,
                                       "poss": 480}}}
    wide = dict(pooled, p10_not_after_window_frac=0.70)
    anchors = [
        H.AnchorResult(anchor="tubbs", fuel_label="GR2", spotting=False,
                       metric="head km/h over 3 h", observed=6.4,
                       engine=2.97, ratio=0.46, point_ros_kmh=2.98),
        H.AnchorResult(anchor="tubbs", fuel_label="SH5", spotting=False,
                       metric="head km/h over 3 h", observed=6.4,
                       engine=5.67, ratio=0.89, point_ros_kmh=5.67)]
    recs = H.recommendations(pooled, wide, anchors)
    joined = " ".join(recs)
    assert "does NOT close" in joined
    assert "ROS LEVEL" in joined
    assert "INCREASE wind_perturb_frac" not in joined
    assert "under-dispersed" in joined               # PIT bimodality (40%/35%)
    assert "Fuel-class split" in joined
    assert "GR2 0.46x vs SH5 0.89x" in joined        # anchor-fed, not hardcoded


def test_render_report_sections_and_byte_stability():
    # EXPECTATION REGENERATED (tri-model review 2026-08-31 fix-program item
    # 5): render_report was rewritten to retire covered_certain/covered_
    # possible/early_miss from the narrative into a clearly-labelled
    # "Deprecated one-sided metrics" appendix, rename the per-pair/pooled
    # table to the honest p10_adequacy framing, and add the null-model
    # skill, window-length bins, and incident-concentration sections. The
    # required-substrings list below is regenerated against that new
    # output; text1 == text2 (byte-stability / no-wall-clock) is unchanged
    # and is still the point of this test.
    cfg = ResponseConfig()
    anchors = H.run_anchor(
        dataclasses.replace(H.TUBBS, cell_m=200.0, width_km=8.0,
                            height_km=3.0, duration_h=1.0), cfg, False)
    text1 = H.render_report([], [], [], anchors,
                            {"n_requests": 7, "budget": 45,
                             "notes": ["BLOCKED: example"]}, cfg)
    text2 = H.render_report([], [], [], anchors,
                            {"n_requests": 7, "budget": 45,
                             "notes": ["BLOCKED: example"]}, cfg)
    assert text1 == text2                        # no wall-clock in the render
    for required in ("# Hindcast first pass", "## Method",
                     "## RECOMMENDATION", "DO NOT SHIP A FUDGE",
                     "NON-COMMERCIAL", "CC-BY-4.0",
                     "WFIGS_Daily_Perimeters_Public",
                     "## Reachability / degradation log",
                     "BLOCKED: example",
                     "no scorable pairs this run",
                     "docs/calibration/SCORECARD.md",
                     "DEPRECATED",
                     "Deprecated one-sided metrics",
                     "Null-model baseline",
                     "STRUCTURALLY IMPOSSIBLE"):
        assert required in text1, f"report missing: {required}"
    assert "Tubbs" in text1 or "tubbs" in text1


def test_render_report_with_pairs_tables():
    spec = _spec()
    p = H.PairMetrics(incident="{X}", name="Demo", t0_s=1.7e9,
                      t1_s=1.7e9 + 43200.0, dt_h=12.0, cell_m=90.0,
                      n_new=100, n_new_burnable=90, n_new_nonburnable=10,
                      covered_certain=30, covered_possible=60, late_miss=20,
                      early_miss=5, never_reached=10, p90_unreached=4,
                      p50_in_window=25, p10_not_after_window=70,
                      p10_before_window=40, pit_hist=[50, 10, 10, 10, 5, 5],
                      obs_head_m=2000.0, head_m_p50=1000.0, head_m_p10=1500.0,
                      head_m_nominal=900.0, obs_new_area_ac=180.0,
                      modeled_new_area_ac_p50=90.0,
                      by_fuel_class={"GR": {"n": 50, "late": 15, "cert": 20,
                                            "poss": 40},
                                     "NB": {"n": 10, "late": 0, "cert": 0,
                                            "poss": 0}})
    inc = H.HindcastIncident(irwin="{X}", name="Demo", max_acres=12000.0,
                             snapshots=[H.PerimeterSnapshot(t_s=1.7e9,
                                                            rings=[])])
    text = H.render_report([inc], [p], [p], [], {}, ResponseConfig())
    assert "| Demo |" in text
    assert "0.50/0.75" in text                   # head ratios p50/p10/nom
    assert "### Pooled (cell-weighted)" in text
    assert "By FBFM40 fuel class" in text
    assert "non-burnable" in text
    # TRI-MODEL REVIEW 2026-08-31 fix-program item 5: the honest scorecard
    assert "p10 adequacy" in text
    assert "null adeq" in text and "skill" in text
    assert "### Deprecated one-sided metrics" in text
    assert "### Window-length bins" in text
    assert "### Incident concentration" in text
    assert "null-model skill" in text
    # the deprecated coverage/early-miss framing must not appear in the
    # PRIMARY per-pair table any more -- only inside the labelled appendix
    main_table, appendix = text.split("### Deprecated one-sided metrics", 1)
    assert "coverage [certain..possible]" not in main_table
    assert "coverage [certain..possible]" in appendix
    assert "p10<=T0 'sufficient'" in appendix


# ---------------------------------------------------------------------------
# harness pure seams
# ---------------------------------------------------------------------------

def test_select_incidents_top_rows_plus_largest_acres():
    cands = [
        ("{A}", "MostRows", 67, 132000.0, 0.0, 1.0),
        ("{B}", "Second", 39, 21000.0, 0.0, 1.0),
        ("{C}", "Third", 29, 12000.0, 0.0, 1.0),
        ("{D}", "Fourth", 27, 49000.0, 0.0, 1.0),
        ("{E}", "FastHuge", 7, 79000.0, 0.0, 1.0),
        ("{F}", "TooFew", 2, 999000.0, 0.0, 1.0),      # < 3 rows: ineligible
    ]
    picked = select_incidents(cands, 4)
    assert [c[0] for c in picked] == ["{A}", "{B}", "{C}", "{E}"]
    # the largest-acres slot goes to FastHuge (79k) over Fourth (49k)
    picked2 = select_incidents(cands, 2)
    assert [c[0] for c in picked2] == ["{A}", "{E}"]
    assert select_incidents([], 4) == []


def test_pick_cell_m_matches_threat_coarsening_law():
    cfg = ResponseConfig()
    small = H.rings_bbox([[[LatLon(34.0, -119.0), LatLon(34.02, -119.0),
                            LatLon(34.02, -118.98), LatLon(34.0, -118.98)]]],
                         2000.0)
    assert H.pick_cell_m(small, cfg) == cfg.grid_cell_m
    huge = H.rings_bbox([[[LatLon(34.0, -120.0), LatLon(35.0, -120.0),
                           LatLon(35.0, -118.5), LatLon(34.0, -118.5)]]],
                        2000.0)
    cell = H.pick_cell_m(huge, cfg)
    assert cell > cfg.grid_cell_m
    ctr = huge.center()
    w_m = (huge.east - huge.west) * geo.m_per_deg_lon(ctr.lat)
    h_m = (huge.north - huge.south) * geo.M_PER_DEG_LAT
    assert (w_m / cell) * (h_m / cell) <= cfg.grid_max_cells * 1.01
