"""Tests for wgresponse.travel (Tier-0 travel-time estimator)."""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from wgresponse import travel
from wgresponse.contracts import LatLon, ResponseConfig

A = LatLon(34.05, -117.10)
B = LatLon(34.05, -117.05)   # ~4.6 km east
C = LatLon(34.10, -117.10)   # ~5.6 km north


def test_travel_time_arithmetic_chain():
    cfg = ResponseConfig()
    d = travel.haversine_m(A, B)
    expected = d * cfg.circuity * cfg.fire_traffic_factor / cfg.road_speed_mps
    assert travel.travel_time_s(A, B, cfg) == pytest.approx(expected, rel=1e-12)
    assert travel.travel_time_s(A, B, cfg) > 0.0


def test_travel_time_zero_distance():
    cfg = ResponseConfig()
    assert travel.travel_time_s(A, A, cfg) == 0.0


def test_travel_time_scales_with_config_factors():
    base = ResponseConfig()
    doubled = ResponseConfig(circuity=base.circuity * 2.0)
    assert travel.travel_time_s(A, B, doubled) == pytest.approx(
        2.0 * travel.travel_time_s(A, B, base), rel=1e-12)
    slower = ResponseConfig(road_speed_mps=base.road_speed_mps / 2.0)
    assert travel.travel_time_s(A, B, slower) == pytest.approx(
        2.0 * travel.travel_time_s(A, B, base), rel=1e-12)
    heavier = ResponseConfig(fire_traffic_factor=base.fire_traffic_factor * 3.0)
    assert travel.travel_time_s(A, B, heavier) == pytest.approx(
        3.0 * travel.travel_time_s(A, B, base), rel=1e-12)


def test_travel_time_monotone_in_distance():
    cfg = ResponseConfig()
    near = LatLon(34.05, -117.09)
    far = LatLon(34.05, -116.90)
    assert travel.travel_time_s(A, near, cfg) < travel.travel_time_s(A, far, cfg)


def test_travel_time_symmetric():
    cfg = ResponseConfig()
    assert travel.travel_time_s(A, C, cfg) == pytest.approx(
        travel.travel_time_s(C, A, cfg), rel=1e-12)


def test_travel_time_bad_speed_raises():
    with pytest.raises(ValueError):
        travel.travel_time_s(A, B, ResponseConfig(road_speed_mps=0.0))


def test_haversine_sanity_one_degree_lat():
    # 1 degree of latitude is ~111 km on any spherical Earth model.
    d = travel.haversine_m(LatLon(34.0, -117.0), LatLon(35.0, -117.0))
    assert 110_000.0 < d < 112_500.0


def test_matrix_matches_pointwise():
    cfg = ResponseConfig()
    origins = [A, C]
    dests = [A, B, C]
    m = travel.travel_matrix_s(origins, dests, cfg)
    assert len(m) == 2 and all(len(row) == 3 for row in m)
    for i, o in enumerate(origins):
        for j, d in enumerate(dests):
            assert m[i][j] == pytest.approx(travel.travel_time_s(o, d, cfg), rel=1e-12)
    assert m[0][0] == 0.0 and m[1][2] == 0.0


def test_matrix_empty():
    cfg = ResponseConfig()
    assert travel.travel_matrix_s([], [A], cfg) == []
    assert travel.travel_matrix_s([A], [], cfg) == [[]]
