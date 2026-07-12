"""Robustness regressions for wgresponse.feeds and wgresponse.weather.

Covers three confirmed dirty-input defects (all no-network, no third-party deps):
  1. A malformed perimeter vertex (null/None or non-numeric coord — common in
     esri feature JSON) must NOT discard the whole WFIGS/FIRIS feed batch; only
     the bad vertex is skipped, good incidents survive.
  2. OpenMeteoWeather.forecast must raise RuntimeError (never a bare IndexError)
     on ragged hourly arrays, so the NWS->OpenMeteo fallback degrades cleanly;
     a merely-short value array yields the valid prefix rather than a crash.
  3. _expand_rle_hourly must emit ZERO hourly samples for a zero-duration (PT0H)
     NWS RLE segment, while still flooring positive sub-hour segments to one hour.
"""
import json

import pytest

from wgresponse.contracts import LatLon
from wgresponse.feeds import FirisFeed, WfigsFeed, _ring_from_raw
from wgresponse.weather import OpenMeteoWeather, _expand_rle_hourly


def _json_http(payload_by_fragment):
    """http_get stub: dict of url-fragment -> JSON-able payload. First match wins."""
    def http_get(url, headers):
        for frag, payload in payload_by_fragment.items():
            if frag in url:
                return 200, json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected URL in test: {url}")
    return http_get


# ---------------------------------------------------------------------------
# Finding 1 — a dirty vertex must not blind the whole feed
# ---------------------------------------------------------------------------

def _perim_feature(oid, name, irwin, ring):
    return {"attributes": {"OBJECTID": oid, "poly_IncidentName": name,
                           "poly_IRWINID": irwin, "poly_DateCurrent": 1751793600000},
            "geometry": {"rings": [ring]}}


class TestRingFromRaw:
    def test_skips_none_coordinate_vertex(self):
        raw = [[-118.61, 34.09], [None, 34.09], [-118.59, 34.11], [-118.61, 34.09]]
        ring = _ring_from_raw(raw)
        assert len(ring) == 3                       # the null-lon vertex is dropped
        assert ring[0] == LatLon(34.09, -118.61)    # esri (lon, lat) -> LatLon(lat, lon)

    def test_skips_non_numeric_string_vertex(self):
        raw = [[-118.61, 34.09], ["x", "y"], [-118.59, 34.11]]
        assert _ring_from_raw(raw) == [LatLon(34.09, -118.61), LatLon(34.11, -118.59)]

    def test_skips_short_and_non_sequence_vertices(self):
        raw = [[-118.61, 34.09], [42.0], None, "bad", [-118.59, 34.11]]
        assert _ring_from_raw(raw) == [LatLon(34.09, -118.61), LatLon(34.11, -118.59)]

    def test_all_bad_yields_empty(self):
        assert _ring_from_raw([[None, None], ["a", "b"]]) == []


class TestWfigsMalformedVertexResilience:
    def test_bad_vertex_incident_does_not_drop_good_incident(self):
        good = _perim_feature(11, "Good", "{GOOD-1}",
                              [[-118.61, 34.09], [-118.59, 34.09],
                               [-118.59, 34.11], [-118.61, 34.09]])
        bad = _perim_feature(12, "Bad", "{BAD-1}",
                             [[-118.61, 34.09], [None, 34.09],
                              [-118.59, 34.11], [-118.61, 34.09]])
        feed = WfigsFeed(http_get=_json_http({
            "WFIGS_Incident_Locations_Current": {"features": []},
            "WFIGS_Interagency_Perimeters_Current": {"features": [good, bad]},
        }))
        incs = feed.fetch_active()
        # Pre-fix this returned 0 (the TypeError nuked the whole batch).
        assert sorted(i.name for i in incs) == ["Bad", "Good"]
        bad_inc = next(i for i in incs if i.name == "Bad")
        assert len(bad_inc.perimeter[0]) == 3       # null vertex skipped, ring kept

    def test_entirely_bad_ring_feature_is_dropped_others_survive(self):
        good = _perim_feature(11, "Good", "{GOOD-1}",
                              [[-118.61, 34.09], [-118.59, 34.09],
                               [-118.59, 34.11], [-118.61, 34.09]])
        allbad = _perim_feature(12, "AllBad", "{BAD-2}",
                                [[None, None], ["a", "b"]])
        feed = WfigsFeed(http_get=_json_http({
            "WFIGS_Incident_Locations_Current": {"features": []},
            "WFIGS_Interagency_Perimeters_Current": {"features": [good, allbad]},
        }))
        incs = feed.fetch_active()
        assert [i.name for i in incs] == ["Good"]   # empty-ring feature dropped

    def test_malformed_point_feature_does_not_drop_batch(self):
        # A point feature whose geometry is not a dict would raise inside
        # _from_point; the per-feature guard must isolate it.
        bad_point = {"attributes": {"OBJECTID": 1, "IncidentName": "BadPt"},
                     "geometry": ["not", "a", "dict"]}
        good_point = {"attributes": {"OBJECTID": 2, "IncidentName": "GoodPt",
                                     "IrwinID": "{GP-1}",
                                     "ModifiedOnDateTime_dt": 1751790000000},
                      "geometry": {"x": -118.6, "y": 34.1}}
        feed = WfigsFeed(http_get=_json_http({
            "WFIGS_Incident_Locations_Current":
                {"features": [bad_point, good_point]},
            "WFIGS_Interagency_Perimeters_Current": {"features": []},
        }))
        incs = feed.fetch_active()
        assert [i.name for i in incs] == ["GoodPt"]


class TestFirisMalformedVertexResilience:
    def test_bad_vertex_feature_does_not_drop_good_feature(self):
        good = {"attributes": {"OBJECTID": 3, "mission": "GOODF",
                               "irwin_id": "{G-9}", "date_current": 1751795000000},
                "geometry": {"rings": [[[-121.0, 39.0], [-120.99, 39.0],
                                        [-120.99, 39.01], [-121.0, 39.0]]]}}
        bad = {"attributes": {"OBJECTID": 4, "mission": "BADF",
                              "irwin_id": "{B-9}", "date_current": 1751795000000},
               "geometry": {"rings": [[[-121.0, 39.0], ["x", "y"],
                                       [-120.99, 39.01], [-121.0, 39.0]]]}}
        feed = FirisFeed(http_get=_json_http({
            "CA_Perimeters_NIFC_FIRIS_public_view": {"features": [good, bad]}}))
        incs = feed.fetch_active()
        assert sorted(i.name for i in incs) == ["BADF", "GOODF"]
        bad_inc = next(i for i in incs if i.name == "BADF")
        assert len(bad_inc.perimeter[0]) == 3


# ---------------------------------------------------------------------------
# Finding 2 — ragged hourly arrays raise RuntimeError, not IndexError
# ---------------------------------------------------------------------------

def _om_http(hourly):
    return _json_http({"open-meteo": {"hourly": hourly},
                       "api.open-meteo.com": {"hourly": hourly}})


_LOC = LatLon(34.0359, -118.6892)


class TestOpenMeteoRaggedArrays:
    def _base_hourly(self):
        return {
            "time": ["2026-07-06T00:00", "2026-07-06T01:00", "2026-07-06T02:00"],
            "temperature_2m": [30.0, 31.0, 32.0],
            "relative_humidity_2m": [20.0, 18.0, 16.0],
            "wind_speed_10m": [5.0, 6.0, 7.0],
            "wind_direction_10m": [40.0, 50.0, 60.0],
            "wind_gusts_10m": [8.0, 9.0, 6.5],
        }

    def test_short_value_array_yields_prefix_not_indexerror(self):
        hourly = self._base_hourly()
        hourly["temperature_2m"] = [30.0, 31.0]       # one short vs. time
        wx = OpenMeteoWeather(http_get=_om_http(hourly))
        fc = wx.forecast(_LOC, horizon_h=3.0)          # pre-fix: raw IndexError
        assert len(fc.samples) == 2                    # only the covered prefix
        assert fc.samples[0].temp_c == 30.0
        assert fc.samples[1].temp_c == 31.0

    def test_all_value_arrays_empty_raises_runtimeerror(self):
        hourly = self._base_hourly()
        for k in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                  "wind_direction_10m", "wind_gusts_10m"):
            hourly[k] = []
        wx = OpenMeteoWeather(http_get=_om_http(hourly))
        with pytest.raises(RuntimeError):              # never a bare IndexError
            wx.forecast(_LOC, horizon_h=3.0)

    def test_gust_array_short_does_not_indexerror(self):
        # gust is indexed via gust[i] before the None guard covers it.
        hourly = self._base_hourly()
        hourly["wind_gusts_10m"] = [8.0]              # two short
        wx = OpenMeteoWeather(http_get=_om_http(hourly))
        fc = wx.forecast(_LOC, horizon_h=3.0)
        assert len(fc.samples) == 1
        assert fc.samples[0].wind_gust_mps >= fc.samples[0].wind_speed_mps

    def test_ragged_never_leaks_indexerror(self):
        # Whichever single array is short, the method raises RuntimeError or
        # returns a shorter WeatherForecast — but never IndexError.
        for short_key in ("temperature_2m", "relative_humidity_2m",
                          "wind_speed_10m", "wind_direction_10m",
                          "wind_gusts_10m"):
            hourly = self._base_hourly()
            hourly[short_key] = hourly[short_key][:1]
            wx = OpenMeteoWeather(http_get=_om_http(hourly))
            try:
                fc = wx.forecast(_LOC, horizon_h=3.0)
                assert len(fc.samples) <= 1
            except RuntimeError:
                pass   # acceptable degraded path
            except IndexError as e:   # pragma: no cover
                pytest.fail(f"IndexError leaked for short {short_key}: {e}")


# ---------------------------------------------------------------------------
# Finding 3 — PT0H segment emits zero hours
# ---------------------------------------------------------------------------

class TestExpandRlePt0h:
    def test_pt0h_emits_no_hours(self):
        out = _expand_rle_hourly(
            [{"validTime": "2026-07-06T00:00:00+00:00/PT0H", "value": 42.0}],
            lambda v: v)
        assert out == {}                              # pre-fix: {one hour: 42.0}

    def test_positive_sub_hour_still_floors_to_one(self):
        out = _expand_rle_hourly(
            [{"validTime": "2026-07-06T00:00:00+00:00/PT30M", "value": 42.0}],
            lambda v: v)
        assert len(out) == 1                          # 30-min segment occupies its hour

    def test_multi_hour_segment_unchanged(self):
        out = _expand_rle_hourly(
            [{"validTime": "2026-07-06T00:00:00+00:00/PT3H", "value": 42.0}],
            lambda v: v)
        assert len(out) == 3

    def test_missing_duration_defaults_to_one_hour(self):
        out = _expand_rle_hourly(
            [{"validTime": "2026-07-06T00:00:00+00:00", "value": 42.0}],
            lambda v: v)
        assert len(out) == 1

    def test_pt0h_does_not_shadow_a_real_later_segment(self):
        # A stray PT0H at hour 0 must not inject a value; the real PT2H starting
        # at hour 1 must be the only data present.
        out = _expand_rle_hourly([
            {"validTime": "2026-07-06T00:00:00+00:00/PT0H", "value": 99.0},
            {"validTime": "2026-07-06T01:00:00+00:00/PT2H", "value": 7.0},
        ], lambda v: v)
        assert 99.0 not in out.values()
        assert set(out.values()) == {7.0}
        assert len(out) == 2
