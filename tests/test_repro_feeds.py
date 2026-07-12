"""Regression tests for wgresponse.feeds dirty-geometry tolerance.

A single malformed perimeter vertex from a live ArcGIS feed (WFIGS / FIRIS)
must NOT crash the whole feed batch and drop every valid active fire. These
tests pin the tolerant-vertex behaviour. No network, no third-party deps.
"""
from __future__ import annotations

import json

from wgresponse.contracts import LatLon
from wgresponse.feeds import FirisFeed, WfigsFeed, _ring_from_raw


def _http_stub(routes):
    """routes: list of (url_fragment, status, payload_dict). First match wins."""

    def http_get(url, headers):
        for frag, status, payload in routes:
            if frag in url:
                return status, json.dumps(payload).encode("utf-8")
        raise AssertionError(f"unexpected URL: {url}")

    return http_get


def _wfigs_perims():
    """A batch of [good1, bad, good2] perimeters; the middle ring holds a
    2-element NON-NUMERIC vertex ['x', 'y'] that float() cannot parse."""
    return {"features": [
        {"attributes": {"OBJECTID": 11, "poly_IncidentName": "Good1",
                        "poly_IRWINID": "{g1}"},
         "geometry": {"rings": [[[-118.61, 34.09], [-118.59, 34.09],
                                 [-118.59, 34.11], [-118.61, 34.09]]]}},
        {"attributes": {"OBJECTID": 12, "poly_IncidentName": "Bad",
                        "poly_IRWINID": "{bad}"},
         "geometry": {"rings": [[[-118.61, 34.09], ["x", "y"],
                                 [-118.59, 34.11], [-118.61, 34.09]]]}},
        {"attributes": {"OBJECTID": 13, "poly_IncidentName": "Good2",
                        "poly_IRWINID": "{g2}"},
         "geometry": {"rings": [[[-117.61, 33.09], [-117.59, 33.09],
                                 [-117.59, 33.11], [-117.61, 33.09]]]}},
    ]}


def test_wfigs_malformed_geometry_ring_partial_coords():
    """One bad vertex must not discard the ring, incident, or batch."""
    feed = WfigsFeed(http_get=_http_stub([
        ("WFIGS_Incident_Locations_Current", 200, {"features": []}),
        ("WFIGS_Interagency_Perimeters_Current", 200, _wfigs_perims()),
    ]))
    incs = feed.fetch_active()
    names = sorted(i.name for i in incs)
    # All three incidents survive; the bad vertex is silently dropped.
    assert names == ["Bad", "Good1", "Good2"], names
    by_name = {i.name: i for i in incs}
    # The "Bad" ring keeps its 3 parseable vertices (the ['x','y'] is dropped).
    assert len(by_name["Bad"].perimeter[0]) == 3
    for good in ("Good1", "Good2"):
        assert len(by_name[good].perimeter[0]) == 4


def test_firis_malformed_geometry_ring_partial_coords():
    """FIRIS shares the same tolerant path; verify it too."""
    feats = {"features": [
        {"attributes": {"objectid": 1, "incidentname": "OK",
                        "irwinid": "{ok}"},
         "geometry": {"rings": [[[-120.0, 38.0], [-119.9, 38.0],
                                 [-119.9, 38.1], [-120.0, 38.0]]]}},
        {"attributes": {"objectid": 2, "incidentname": "Dirty",
                        "irwinid": "{dirty}"},
         "geometry": {"rings": [[[-121.0, 39.0], [None, None],
                                 [-120.9, 39.1], ["nan_x", 39.2],
                                 [-121.0, 39.0]]]}},
    ]}
    feed = FirisFeed(http_get=_http_stub([("CA_Perimeters", 200, feats)]))
    incs = feed.fetch_active()
    names = sorted(i.name for i in incs)
    assert names == ["Dirty", "OK"], names
    by_name = {i.name: i for i in incs}
    # Dirty ring had 5 raw verts, 2 unparseable ([None,None] and ['nan_x',...]).
    assert len(by_name["Dirty"].perimeter[0]) == 3


def test_ring_from_raw_skips_bad_vertices_keeps_good():
    raw = [
        [-118.0, 34.0],        # good
        ["x", "y"],            # non-numeric -> skipped
        [None, 34.5],          # None lon -> skipped
        [-118.5],              # too short -> skipped
        "not-a-vertex",        # not a sequence -> skipped
        [-118.2, 34.2],        # good
    ]
    ring = _ring_from_raw(raw)
    assert ring == [LatLon(34.0, -118.0), LatLon(34.2, -118.2)]


def test_ring_from_raw_all_bad_returns_empty():
    assert _ring_from_raw([["a", "b"], [None, None], [1.0]]) == []
    assert _ring_from_raw([]) == []
