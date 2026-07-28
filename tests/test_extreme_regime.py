"""EXTREME SPREAD REGIME (contracts knob block) — detector + ladder posture.

Measured basis: docs/calibration/hindcast_oos_2026-07-28.md — blow-up windows
are cured-grass-led (Porcupine 88% GR+GS, 13x under-forecast) at MODEST wind,
so the detector is fuel-led with a low wind assist, and flagged incidents
evaluate ladder stages on the pessimistic (p10) arrival."""
from __future__ import annotations

import dataclasses

from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    GridSpec,
    LatLon,
    ResponseConfig,
    TerrainTile,
    ThreatAssessment,
    ThreatEta,
    WeatherForecast,
    WeatherSample,
)
from wgresponse import geo
from wgresponse.notify import LogNotifier
from wgresponse.threat import _extreme_regime_now

H = 3600.0
NOW = 1_700_000_000.0


def _tile(fuel_code: float, nrows: int = 10, ncols: int = 10) -> TerrainTile:
    spec = GridSpec(north_lat=34.2, west_lon=-118.3, cell_m=90.0,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(spec=spec,
                       fuel=geo.make_raster(spec, fill=fuel_code),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0),
                       source="stub")


def _wx(wind_mps: float, gust_mps: float, hours: int = 24) -> WeatherForecast:
    samples = [WeatherSample(t_s=NOW + i * H, wind_speed_mps=wind_mps,
                             wind_gust_mps=gust_mps, wind_dir_deg=225.0,
                             rh_pct=25.0, temp_c=30.0)
               for i in range(hours)]
    return WeatherForecast(loc=LatLon(34.1, -118.2), samples=samples)


def _cfg(**over) -> ResponseConfig:
    return dataclasses.replace(ResponseConfig(), extreme_regime_enabled=True,
                               **over)


# ---------------------------------------------------------------- detector


def test_detector_grass_domain_with_wind_arms():
    # GR2 (102) sea + 3 m/s sustained / 6 m/s gust -> eff 4.5 >= 4.0
    assert _extreme_regime_now(_tile(102.0), _wx(3.0, 6.0), NOW, _cfg())


def test_detector_grass_shrub_counts_as_grass():
    # GS2 (122) counts toward the grass fraction (Porcupine was GS-led)
    assert _extreme_regime_now(_tile(122.0), _wx(3.0, 6.0), NOW, _cfg())


def test_detector_timber_domain_never_arms():
    # TL8 (188) sea: fuel gate fails regardless of wind
    assert not _extreme_regime_now(_tile(188.0), _wx(12.0, 20.0), NOW, _cfg())


def test_detector_calm_grass_does_not_arm():
    # dead calm: eff 1.0 < 4.0 — a grass sea alone is not the regime
    assert not _extreme_regime_now(_tile(102.0), _wx(1.0, 1.0), NOW, _cfg())


def test_detector_wind_beyond_lookahead_ignored():
    # wind arrives at +30 h; lookahead 24 h must not see it
    samples = [WeatherSample(t_s=NOW + i * H, wind_speed_mps=1.0,
                             wind_gust_mps=1.0, wind_dir_deg=225.0,
                             rh_pct=25.0, temp_c=30.0) for i in range(30)]
    samples.append(WeatherSample(t_s=NOW + 30 * H, wind_speed_mps=15.0,
                                 wind_gust_mps=20.0, wind_dir_deg=225.0,
                                 rh_pct=25.0, temp_c=30.0))
    wxf = WeatherForecast(loc=LatLon(34.1, -118.2), samples=samples)
    assert not _extreme_regime_now(_tile(102.0), wxf, NOW, _cfg())


def test_detector_mixed_fuel_threshold():
    # 50/50 GR/TL is under the 0.60 gate; 70/30 is over
    spec = GridSpec(north_lat=34.2, west_lon=-118.3, cell_m=90.0,
                    nrows=1, ncols=10)
    half = TerrainTile(spec=spec,
                       fuel=geo.make_raster(spec, fill=102.0),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0),
                       source="stub")
    for i in range(5):
        half.fuel.data[i] = 188.0
    assert not _extreme_regime_now(half, _wx(3.0, 6.0), NOW, _cfg())
    for i in range(5, 7):
        half.fuel.data[i - 5] = 102.0
    half.fuel.data[3] = 188.0
    half.fuel.data[4] = 188.0
    seventy = half
    n_grass = sum(1 for v in seventy.fuel.data if 101 <= int(v) <= 109)
    assert n_grass == 7
    assert _extreme_regime_now(seventy, _wx(3.0, 6.0), NOW, _cfg())


def test_detector_nonburnable_cells_excluded_from_denominator():
    # 6 GR cells + 4 NB(91) cells: grass fraction of BURNABLE = 1.0 -> arms
    t = _tile(102.0, nrows=1, ncols=10)
    for i in range(4):
        t.fuel.data[i] = 91.0
    assert _extreme_regime_now(t, _wx(3.0, 6.0), NOW, _cfg())


def test_detector_all_nonburnable_fail_safe_false():
    t = _tile(91.0)
    assert not _extreme_regime_now(t, _wx(10.0, 15.0), NOW, _cfg())


# ---------------------------------------------------------------- ladder


def _assess(p50_h: float, p10_h: float) -> ThreatAssessment:
    eta = ThreatEta(eta_p10_s=NOW + p10_h * H, eta_p50_s=NOW + p50_h * H,
                    eta_p90_s=NOW + p50_h * 1.5 * H)
    return ThreatAssessment(client_id="c1", incident_id="fire-1", eta=eta,
                            computed_at_s=NOW)


def _engine():
    clients = {"c1": ClientProperty(client_id="c1", loc=LatLon(34.1, -118.2),
                                    name="Casa")}
    return AlertEngine(ResponseConfig(), notifier=LogNotifier(),
                       clients_by_id=clients)


def test_ladder_regime_escalates_on_p10():
    # p50 30 h / p10 20 h: normal ladder -> NOTICE_72H; flagged incident
    # evaluates on p10 (20 <= extreme_h 24) -> straight to EXTREME_24H.
    eng = _engine()
    meta = {"fire-1": {"name": "GRASSY", "extreme_regime": True}}
    events = eng.evaluate([_assess(30.0, 20.0)], NOW, incident_meta=meta)
    assert [e.state for e in events] == [AlertState.EXTREME_24H]
    assert "EXTREME SPREAD REGIME" in events[0].message
    assert "pessimistic" in events[0].message


def test_ladder_without_flag_is_unchanged():
    eng = _engine()
    meta = {"fire-1": {"name": "GRASSY"}}
    events = eng.evaluate([_assess(30.0, 20.0)], NOW, incident_meta=meta)
    assert [e.state for e in events] == [AlertState.NOTICE_72H]
    assert "EXTREME SPREAD REGIME" not in events[0].message


def test_ladder_regime_notice_on_far_p10():
    # p50 100 h / p10 60 h: normal -> no state (p50 beyond notice);
    # flagged -> NOTICE_72H on p10 60 <= 72.
    eng = _engine()
    events = eng.evaluate([_assess(100.0, 60.0)], NOW,
                          incident_meta={"fire-1": {}})
    assert events == []
    eng2 = _engine()
    meta = {"fire-1": {"extreme_regime": True}}
    events2 = eng2.evaluate([_assess(100.0, 60.0)], NOW, incident_meta=meta)
    assert [e.state for e in events2] == [AlertState.NOTICE_72H]


def test_ladder_regime_blocks_clear_while_p10_near():
    # Enter EXTREME under the flag, then a forecast with p50 far but p10
    # still inside the extreme window: the pair must NOT clear or de-escalate.
    eng = _engine()
    meta = {"fire-1": {"extreme_regime": True}}
    eng.evaluate([_assess(30.0, 20.0)], NOW, incident_meta=meta)
    for i in (1, 2, 3):
        events = eng.evaluate([_assess(90.0, 22.0)], NOW + i, incident_meta=meta)
        assert events == []


def test_ladder_regime_impact_still_on_median():
    # Flagged pair at EXTREME: p10 already passed but p50 8 h out — IMPACTED
    # must wait for the MEDIAN to pass (impact is an observation, not posture).
    eng = _engine()
    meta = {"fire-1": {"extreme_regime": True}}
    eng.evaluate([_assess(9.0, 1.0)], NOW, incident_meta=meta)
    events = eng.evaluate([_assess(8.0, -0.5)], NOW + 1.0, incident_meta=meta)
    assert events == []           # still EXTREME_24H, not IMPACTED
    events = eng.evaluate([_assess(-0.1, -1.0)], NOW + 2.0, incident_meta=meta)
    assert [e.state for e in events] == [AlertState.IMPACTED]
