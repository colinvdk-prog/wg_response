"""Tests for wgresponse.weather — canned NWS/Open-Meteo payloads, no network."""
import json
from datetime import datetime, timezone

import pytest

from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.weather import (MockWeather, NwsWeather, OpenMeteoWeather,
                                _parse_duration_s, _parse_iso8601_s)

LOC = LatLon(34.0359, -118.6892)
T0 = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc).timestamp()


def make_http_stub(routes):
    """routes: list of (url_fragment, status, payload dict|bytes); records calls."""
    calls = []

    def http_get(url, headers):
        calls.append((url, headers))
        for frag, status, payload in routes:
            if frag in url:
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                return status, body
        raise AssertionError(f"unexpected URL in test: {url}")

    http_get.calls = calls
    return http_get


NWS_POINTS = {"properties": {
    "gridId": "LOX", "gridX": 139, "gridY": 47,
    "forecastGridData": "https://api.weather.gov/gridpoints/LOX/139,47",
    "fireWeatherZone": "https://api.weather.gov/zones/fire/CAZ362",
}}

NWS_GRID = {"properties": {
    "windSpeed": {"uom": "wmoUnit:km_h-1", "values": [
        {"validTime": "2026-07-06T00:00:00+00:00/PT3H", "value": 36.0},
        {"validTime": "2026-07-06T03:00:00+00:00/PT1H", "value": 18.0},
    ]},
    "windGust": {"uom": "wmoUnit:km_h-1", "values": [
        {"validTime": "2026-07-06T00:00:00+00:00/PT4H", "value": 54.0},
    ]},
    "windDirection": {"uom": "wmoUnit:degree_(angle)", "values": [
        {"validTime": "2026-07-06T00:00:00+00:00/PT4H", "value": 45.0},
    ]},
    "relativeHumidity": {"uom": "wmoUnit:percent", "values": [
        {"validTime": "2026-07-06T00:00:00+00:00/PT2H", "value": 30.0},
        {"validTime": "2026-07-06T02:00:00+00:00/PT2H", "value": 25.0},
    ]},
    "temperature": {"uom": "wmoUnit:degC", "values": [
        {"validTime": "2026-07-06T00:00:00+00:00/PT4H", "value": 28.0},
    ]},
}}


def nws_stub(grid=NWS_GRID, alerts=None):
    return make_http_stub([
        ("alerts/active", 200, alerts if alerts is not None else {"features": []}),
        ("api.weather.gov/points/", 200, NWS_POINTS),
        ("api.weather.gov/gridpoints/", 200, grid),
    ])


class TestNwsWeather:
    def test_rle_decode_pt3h_spans_three_hourly_samples(self):
        wx = NwsWeather(http_get=nws_stub())
        fc = wx.forecast(LOC, horizon_h=4.0)
        assert fc.source == "nws"
        assert len(fc.samples) == 4                        # 00,01,02,03 UTC
        assert [s.t_s for s in fc.samples] == [T0, T0 + 3600, T0 + 7200, T0 + 10800]
        # PT3H @ 36 km/h -> 3 hourly samples of exactly 10 m/s
        assert [round(s.wind_speed_mps, 6) for s in fc.samples] == [10.0, 10.0, 10.0, 5.0]

    def test_kmh_to_mps_and_gust(self):
        fc = NwsWeather(http_get=nws_stub()).forecast(LOC, horizon_h=4.0)
        for s in fc.samples:
            assert round(s.wind_gust_mps, 6) == 15.0       # 54 km/h
            assert s.wind_gust_mps >= s.wind_speed_mps
            assert s.wind_dir_deg == 45.0
            assert s.temp_c == 28.0

    def test_rh_series_alignment(self):
        fc = NwsWeather(http_get=nws_stub()).forecast(LOC, horizon_h=4.0)
        assert [s.rh_pct for s in fc.samples] == [30.0, 30.0, 25.0, 25.0]

    def test_horizon_clips_series(self):
        fc = NwsWeather(http_get=nws_stub()).forecast(LOC, horizon_h=2.0)
        assert len(fc.samples) == 3                        # 00,01,02

    def test_user_agent_header_sent(self):
        cfg = ResponseConfig()
        stub = nws_stub()
        NwsWeather(http_get=stub, cfg=cfg).forecast(LOC, horizon_h=2.0)
        assert stub.calls, "no HTTP calls recorded"
        for _url, headers in stub.calls:
            assert headers.get("User-Agent") == cfg.user_agent

    def test_forecast_at_lookup_contract(self):
        fc = NwsWeather(http_get=nws_stub()).forecast(LOC, horizon_h=4.0)
        assert fc.at(T0 + 1800).wind_speed_mps == pytest.approx(10.0)   # nearest hour
        assert fc.at(T0 + 999999).wind_speed_mps == pytest.approx(5.0)  # clamped end

    def test_mps_uom_passthrough(self):
        grid = {"properties": {"windSpeed": {"uom": "wmoUnit:m_s-1", "values": [
            {"validTime": "2026-07-06T00:00:00+00:00/PT2H", "value": 7.5}]}}}
        fc = NwsWeather(http_get=nws_stub(grid=grid)).forecast(LOC, horizon_h=2.0)
        assert all(s.wind_speed_mps == 7.5 for s in fc.samples)

    def test_http_error_raises_runtime_error(self):
        wx = NwsWeather(http_get=lambda url, headers: (500, b"oops"))
        with pytest.raises(RuntimeError, match="HTTP 500"):
            wx.forecast(LOC, horizon_h=4.0)

    def test_missing_wind_raises(self):
        grid = {"properties": {"temperature": {"uom": "wmoUnit:degC", "values": []}}}
        with pytest.raises(RuntimeError, match="windSpeed"):
            NwsWeather(http_get=nws_stub(grid=grid)).forecast(LOC, horizon_h=4.0)

    def test_red_flag_active_true(self):
        alerts = {"features": [{"properties": {"event": "Red Flag Warning",
                                               "severity": "Severe"}}]}
        assert NwsWeather(http_get=nws_stub(alerts=alerts)).red_flag_active(LOC) is True

    def test_red_flag_ignores_unrelated_alerts(self):
        alerts = {"features": [{"properties": {"event": "Flood Watch"}}]}
        assert NwsWeather(http_get=nws_stub(alerts=alerts)).red_flag_active(LOC) is False

    def test_red_flag_empty_false(self):
        assert NwsWeather(http_get=nws_stub()).red_flag_active(LOC) is False


class TestDurationParser:
    @pytest.mark.parametrize("s,expect", [
        ("PT1H", 3600.0), ("PT3H", 10800.0), ("PT30M", 1800.0),
        ("P1D", 86400.0), ("P1DT2H", 93600.0), ("P1W", 604800.0),
        ("PT15S", 15.0), ("P7DT23H", 7 * 86400.0 + 23 * 3600.0),
    ])
    def test_durations(self, s, expect):
        assert _parse_duration_s(s) == expect

    def test_bad_duration_raises(self):
        with pytest.raises(ValueError):
            _parse_duration_s("3 hours")
        with pytest.raises(ValueError):
            _parse_duration_s("P")

    def test_iso8601_variants(self):
        assert _parse_iso8601_s("2026-07-06T00:00:00Z") == T0
        assert _parse_iso8601_s("2026-07-06T00:00:00+00:00") == T0
        assert _parse_iso8601_s("2026-07-06T00:00") == T0          # naive == UTC
        assert _parse_iso8601_s("2026-07-05T17:00:00-07:00") == T0


OPEN_METEO_PAYLOAD = {
    "hourly": {
        "time": ["2026-07-06T00:00", "2026-07-06T01:00", "2026-07-06T02:00"],
        "temperature_2m": [30.0, 31.0, 32.0],
        "relative_humidity_2m": [20.0, 18.0, 16.0],
        "wind_speed_10m": [5.0, 6.0, 7.0],
        "wind_direction_10m": [40.0, 50.0, 60.0],
        "wind_gusts_10m": [8.0, 9.0, 6.5],
    },
}


class TestOpenMeteoWeather:
    def test_parses_hourly_arrays(self):
        wx = OpenMeteoWeather(http_get=make_http_stub([("open-meteo", 200, OPEN_METEO_PAYLOAD)]))
        fc = wx.forecast(LOC, horizon_h=3.0)
        assert fc.source == "open-meteo"
        assert len(fc.samples) == 3
        s0 = fc.samples[0]
        assert s0.t_s == T0
        assert s0.wind_speed_mps == 5.0        # already m/s (wind_speed_unit=ms)
        assert s0.wind_dir_deg == 40.0
        assert s0.rh_pct == 20.0
        assert s0.temp_c == 30.0
        # gust never below sustained
        assert fc.samples[2].wind_gust_mps == 7.0

    def test_url_requests_ms_utc_and_days(self):
        stub = make_http_stub([("open-meteo", 200, OPEN_METEO_PAYLOAD)])
        OpenMeteoWeather(http_get=stub).forecast(LOC, horizon_h=48.0)
        url = stub.calls[0][0]
        assert "wind_speed_unit=ms" in url
        assert "timezone=UTC" in url
        assert "forecast_days=2" in url

    def test_error_raises(self):
        wx = OpenMeteoWeather(http_get=lambda url, headers: (500, b""))
        with pytest.raises(RuntimeError):
            wx.forecast(LOC, horizon_h=24.0)

    def test_missing_arrays_raise(self):
        wx = OpenMeteoWeather(http_get=make_http_stub([("open-meteo", 200, {"hourly": {}})]))
        with pytest.raises(RuntimeError):
            wx.forecast(LOC, horizon_h=24.0)


class TestMockWeather:
    def test_deterministic_same_seed(self):
        a = MockWeather("s1").forecast(LOC, 24.0)
        b = MockWeather("s1").forecast(LOC, 24.0)
        assert a.samples == b.samples

    def test_prefix_stable_across_horizons(self):
        short = MockWeather("s1").forecast(LOC, 5.0)
        long = MockWeather("s1").forecast(LOC, 10.0)
        assert long.samples[:len(short.samples)] == short.samples

    def test_base_values_and_gust_factor(self):
        fc = MockWeather("s1", base_wind_mps=5.0, dir_deg=90.0, rh_pct=30.0,
                         temp_c=27.0, gust_factor=1.5).forecast(LOC, 3.0)
        for s in fc.samples:
            assert s.wind_speed_mps == 5.0
            assert s.wind_gust_mps == 7.5
            assert s.wind_dir_deg == 90.0
            assert s.rh_pct == 30.0
            assert s.temp_c == 27.0

    def test_scripted_shift_applies_from_hour(self):
        fc = MockWeather("s1", base_wind_mps=5.0, dir_deg=90.0, rh_pct=30.0,
                         shifts=[(5, 20.0, 270.0, 8.0)]).forecast(LOC, 10.0)
        assert len(fc.samples) == 11
        for s in fc.samples[:5]:
            assert (s.wind_speed_mps, s.wind_dir_deg, s.rh_pct) == (5.0, 90.0, 30.0)
        for s in fc.samples[5:]:
            assert (s.wind_speed_mps, s.wind_dir_deg, s.rh_pct) == (20.0, 270.0, 8.0)

    def test_multiple_shifts_in_order(self):
        fc = MockWeather("s1", base_wind_mps=5.0, dir_deg=0.0, rh_pct=40.0,
                         shifts=[(2, 10.0, 45.0, 20.0), (4, 30.0, 315.0, 5.0)]
                         ).forecast(LOC, 6.0)
        winds = [s.wind_speed_mps for s in fc.samples]
        assert winds == [5.0, 5.0, 10.0, 10.0, 30.0, 30.0, 30.0]

    def test_hourly_cadence_and_t0(self):
        fc = MockWeather("s1", t0_s=1_000_000.0).forecast(LOC, 2.0)
        assert [s.t_s for s in fc.samples] == [1_000_000.0, 1_003_600.0, 1_007_200.0]

    def test_red_flag_scripted(self):
        assert MockWeather("s1").red_flag_active(LOC) is False
        assert MockWeather("s1", red_flag=True).red_flag_active(LOC) is True

    def test_jitter_stays_deterministic(self):
        a = MockWeather("s1", jitter_frac=0.1).forecast(LOC, 12.0)
        b = MockWeather("s1", jitter_frac=0.1).forecast(LOC, 12.0)
        assert a.samples == b.samples
        c = MockWeather("s2", jitter_frac=0.1).forecast(LOC, 12.0)
        assert a.samples != c.samples
