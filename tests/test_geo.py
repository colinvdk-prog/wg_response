"""Tests for wgresponse.geo — spherical geometry + grid/raster helpers."""
import math

import pytest

from wgresponse.contracts import GridSpec, LatLon
from wgresponse import geo


A = LatLon(34.0359, -118.6892)  # Malibu test point (research digest)


# ---------------------------------------------------------------------------
# Great-circle primitives
# ---------------------------------------------------------------------------

def test_haversine_one_degree_lat():
    # 1 degree of latitude on the geo sphere = M_PER_DEG_LAT (~111.19 km)
    d = geo.haversine_m(LatLon(0.0, 0.0), LatLon(1.0, 0.0))
    assert d == pytest.approx(geo.M_PER_DEG_LAT, rel=1e-6)


def test_haversine_zero_and_symmetry():
    assert geo.haversine_m(A, A) == 0.0
    b = LatLon(34.1, -118.5)
    assert geo.haversine_m(A, b) == pytest.approx(geo.haversine_m(b, A), rel=1e-12)


def test_bearing_cardinals():
    assert geo.bearing_deg(LatLon(0.0, 0.0), LatLon(1.0, 0.0)) == pytest.approx(0.0, abs=1e-9)
    assert geo.bearing_deg(LatLon(0.0, 0.0), LatLon(0.0, 1.0)) == pytest.approx(90.0, abs=1e-9)
    assert geo.bearing_deg(LatLon(1.0, 0.0), LatLon(0.0, 0.0)) == pytest.approx(180.0, abs=1e-9)
    assert geo.bearing_deg(LatLon(0.0, 1.0), LatLon(0.0, 0.0)) == pytest.approx(270.0, abs=1e-9)


def test_destination_roundtrip_distance_and_bearing():
    for brg in (0.0, 45.0, 137.0, 250.0):
        p = geo.destination(A, brg, 10_000.0)
        assert geo.haversine_m(A, p) == pytest.approx(10_000.0, abs=1.0)
        # compare as circular angle (0 == 360)
        diff = (geo.bearing_deg(A, p) - brg + 180.0) % 360.0 - 180.0
        assert abs(diff) < 0.1


def test_destination_zero_distance():
    p = geo.destination(A, 123.0, 0.0)
    assert p == LatLon(A.lat, A.lon)


def test_destination_lon_normalization():
    p = geo.destination(LatLon(0.0, 179.9), 90.0, 50_000.0)
    assert -180.0 <= p.lon < 180.0
    assert p.lon < 0.0  # crossed the antimeridian


# ---------------------------------------------------------------------------
# Polyline sampling / bbox
# ---------------------------------------------------------------------------

def test_sample_polyline_spacing_and_endpoints():
    b = geo.destination(A, 90.0, 1000.0)
    pts = geo.sample_polyline(A, b, 300.0)
    assert pts[0] == A
    assert pts[-1] == b
    assert len(pts) == 5  # 0, 300, 600, 900, endpoint
    for p, q in zip(pts, pts[1:]):
        assert geo.haversine_m(p, q) <= 300.0 + 1e-3
    total = sum(geo.haversine_m(p, q) for p, q in zip(pts, pts[1:]))
    assert total == pytest.approx(1000.0, abs=1.0)


def test_sample_polyline_exact_multiple():
    b = geo.destination(A, 0.0, 900.0)
    pts = geo.sample_polyline(A, b, 300.0)
    assert len(pts) == 4  # 0, 300, 600, endpoint(=900)
    assert pts[-1] == b


def test_sample_polyline_degenerate_and_bad_step():
    assert geo.sample_polyline(A, A, 100.0) == [A]
    with pytest.raises(ValueError):
        geo.sample_polyline(A, LatLon(34.1, -118.5), 0.0)


def test_bbox_around_contains_and_pad():
    pts = [A, LatLon(34.10, -118.50), LatLon(33.95, -118.80)]
    box = geo.bbox_around(pts, 1000.0)
    for p in pts:
        assert box.contains(p)
    assert box.north == pytest.approx(34.10 + 1000.0 / geo.M_PER_DEG_LAT, abs=1e-9)
    assert box.south == pytest.approx(33.95 - 1000.0 / geo.M_PER_DEG_LAT, abs=1e-9)
    # a point just inside the pad on the east side is contained
    east_edge = geo.destination(LatLon(34.0, -118.50), 90.0, 900.0)
    assert box.contains(east_edge)


def test_bbox_around_empty_raises():
    with pytest.raises(ValueError):
        geo.bbox_around([], 100.0)


# ---------------------------------------------------------------------------
# GridSpec / Raster helpers
# ---------------------------------------------------------------------------

SPEC = GridSpec(north_lat=34.2, west_lon=-118.9, cell_m=90.0, nrows=40, ncols=50)


def test_grid_rc_latlon_roundtrip():
    for rc in [(0, 0), (39, 49), (17, 23), (0, 49), (39, 0)]:
        p = geo.rc_to_latlon(SPEC, *rc)
        assert geo.latlon_to_rc(SPEC, p) == rc


def test_rc_to_latlon_inside_grid_extent():
    p = geo.rc_to_latlon(SPEC, 0, 0)
    assert p.lat < SPEC.north_lat
    assert p.lon > SPEC.west_lon


def test_latlon_to_rc_outside_none():
    assert geo.latlon_to_rc(SPEC, LatLon(34.21, -118.85)) is None    # north of grid
    assert geo.latlon_to_rc(SPEC, LatLon(20.0, -118.85)) is None     # far south
    assert geo.latlon_to_rc(SPEC, LatLon(34.19, -119.5)) is None     # west of grid
    assert geo.latlon_to_rc(SPEC, LatLon(34.19, -100.0)) is None     # far east


def test_make_raster_and_sample():
    r = geo.make_raster(SPEC, fill=3.5)
    assert all(v == 3.5 for v in r.data)
    r.set(2, 3, 7.0)
    assert geo.raster_sample(r, geo.rc_to_latlon(SPEC, 2, 3)) == 7.0
    assert geo.raster_sample(r, geo.rc_to_latlon(SPEC, 5, 5)) == 3.5
    assert geo.raster_sample(r, LatLon(0.0, 0.0)) is None


# ---------------------------------------------------------------------------
# erode_min
# ---------------------------------------------------------------------------

SMALL = GridSpec(north_lat=34.2, west_lon=-118.9, cell_m=90.0, nrows=5, ncols=5)


def test_erode_min_basic_chebyshev_1():
    r = geo.make_raster(SMALL, fill=10.0)
    r.set(2, 2, 1.0)
    e = geo.erode_min(r, 90.0)  # ceil(90/90) = 1 cell
    for i in range(5):
        for j in range(5):
            expected = 1.0 if max(abs(i - 2), abs(j - 2)) <= 1 else 10.0
            assert e.get(i, j) == expected
    # original untouched
    assert r.get(1, 1) == 10.0


def test_erode_min_radius_zero_is_copy():
    r = geo.make_raster(SMALL, fill=4.0)
    e = geo.erode_min(r, 0.0)
    assert e.data == r.data
    assert e.data is not r.data
    e.set(0, 0, -1.0)
    assert r.get(0, 0) == 4.0


def test_erode_min_ceil_radius():
    r = geo.make_raster(SMALL, fill=10.0)
    r.set(2, 2, 1.0)
    e = geo.erode_min(r, 91.0)  # ceil(91/90) = 2 cells -> whole 5x5 grid
    assert all(v == 1.0 for v in e.data)


def test_erode_min_handles_inf():
    r = geo.make_raster(SMALL, fill=math.inf)
    r.set(1, 1, 5.0)
    e = geo.erode_min(r, 90.0)
    assert e.get(0, 0) == 5.0
    assert e.get(2, 2) == 5.0
    assert e.get(4, 4) == math.inf


def test_erode_min_monotone_never_increases():
    r = geo.make_raster(SMALL)
    for i in range(5):
        for j in range(5):
            r.set(i, j, float((i * 7 + j * 13) % 11))
    e = geo.erode_min(r, 180.0)
    assert all(ev <= rv for ev, rv in zip(e.data, r.data))
