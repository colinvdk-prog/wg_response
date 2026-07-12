"""Tests for wgresponse.feeds — canned fixtures, no network, no third-party deps."""
import json
import logging
import math
from datetime import datetime, timezone

import pytest

from wgresponse.contracts import BBox, FireIncident, IncidentSource, LatLon
from wgresponse.feeds import (CalFireFeed, FirisFeed, FirmsFeed, MockFireFeed,
                              WatchDutyEnterpriseFeed, WfigsFeed,
                              _haversine_m, _norm_irwin, _norm_name,
                              fuse_incidents)


def make_http_stub(routes):
    """routes: list of (url_fragment, status, payload) — first match wins.
    payload may be dict (JSON-encoded) or bytes. Records calls."""
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


# ---------------------------------------------------------------------------
# WFIGS
# ---------------------------------------------------------------------------

WFIGS_POINTS = {"features": [
    {"attributes": {"OBJECTID": 1, "IncidentName": "Canyon", "IrwinID": "{ABC-123}",
                    "IncidentSize": 512.0, "PercentContained": 10,
                    "FireDiscoveryDateTime": 1751700000000,
                    "ModifiedOnDateTime_dt": 1751790000000,
                    "POOState": "US-CA", "IncidentTypeCategory": "WF"},
     "geometry": {"x": -118.60, "y": 34.10}},
    {"attributes": {"OBJECTID": 2, "IncidentName": "Rx Burn", "IrwinID": "{RX-1}",
                    "ModifiedOnDateTime_dt": 1751790000000,
                    "IncidentTypeCategory": "RX"},
     "geometry": {"x": -118.00, "y": 34.50}},
]}

WFIGS_PERIMS = {"features": [
    {"attributes": {"OBJECTID": 11, "poly_IncidentName": "Canyon",
                    "attr_IncidentName": "Canyon", "poly_IRWINID": "{abc-123}",
                    "poly_GISAcres": 540.0, "attr_PercentContained": 12,
                    "attr_ModifiedOnDateTime_dt": 1751793600000,
                    "poly_DateCurrent": 1751793600000,
                    "attr_FireDiscoveryDateTime": 1751700000000},
     "geometry": {"rings": [[[-118.61, 34.09], [-118.59, 34.09], [-118.59, 34.11],
                             [-118.61, 34.11], [-118.61, 34.09]]]}},
]}


def wfigs_stub(points=WFIGS_POINTS, perims=WFIGS_PERIMS):
    return make_http_stub([
        ("WFIGS_Incident_Locations_Current", 200, points),
        ("WFIGS_Interagency_Perimeters_Current", 200, perims),
    ])


class TestWfigsFeed:
    def test_parses_and_joins_point_with_perimeter(self):
        feed = WfigsFeed(http_get=wfigs_stub())
        incs = feed.fetch_active()
        assert len(incs) == 1                       # RX incident filtered out
        inc = incs[0]
        assert inc.incident_id == "abc-123"         # canonical: brace-stripped lc IRWIN
        assert inc.irwin_id == "abc-123"
        assert inc.name == "Canyon"
        assert inc.source is IncidentSource.WFIGS
        assert inc.origin == LatLon(34.10, -118.60)  # point layer wins for origin
        assert inc.acreage == 512.0                  # point IncidentSize
        assert inc.containment_pct == 10.0
        assert inc.discovered_at_s == 1751700000.0   # epoch ms -> s
        assert inc.updated_at_s == 1751793600.0      # max(point, perimeter)
        assert inc.perimeter is not None and len(inc.perimeter) == 1
        ring = inc.perimeter[0]
        assert len(ring) == 5
        assert ring[0] == LatLon(34.09, -118.61)     # esri (x=lon, y=lat) swapped
        assert inc.active is True
        assert inc.ignition_points() == ring         # perimeter seeds spread

    def test_perimeter_only_incident_gets_centroid_origin(self):
        feed = WfigsFeed(http_get=wfigs_stub(points={"features": []}))
        incs = feed.fetch_active()
        assert len(incs) == 1
        inc = incs[0]
        assert inc.acreage == 540.0
        assert inc.containment_pct == 12.0
        assert abs(inc.origin.lat - 34.098) < 0.01   # ring vertex mean
        assert abs(inc.origin.lon - (-118.602)) < 0.01

    def test_bbox_becomes_arcgis_envelope_query(self):
        stub = wfigs_stub()
        WfigsFeed(http_get=stub).fetch_active(BBox(-119.0, 34.0, -118.0, 35.0))
        assert len(stub.calls) == 2
        for url, _headers in stub.calls:
            assert "esriGeometryEnvelope" in url
            assert "f=json" in url

    def test_long_ring_is_subsampled(self):
        big_ring = [[-118.0 + i * 1e-5, 34.0 + i * 1e-6] for i in range(1000)]
        perims = {"features": [{
            "attributes": {"OBJECTID": 5, "poly_IncidentName": "Big",
                           "poly_IRWINID": "{BIG-1}",
                           "poly_DateCurrent": 1751790000000},
            "geometry": {"rings": [big_ring]}}]}
        feed = WfigsFeed(http_get=wfigs_stub(points={"features": []}, perims=perims))
        incs = feed.fetch_active()
        assert len(incs) == 1
        assert len(incs[0].perimeter[0]) <= 201

    def test_malformed_json_returns_empty_not_raise(self):
        feed = WfigsFeed(http_get=lambda url, headers: (200, b"<html>not json</html>"))
        assert feed.fetch_active() == []

    def test_http_error_returns_empty_not_raise(self):
        feed = WfigsFeed(http_get=lambda url, headers: (500, b"boom"))
        assert feed.fetch_active() == []

    def test_arcgis_error_payload_returns_empty(self):
        payload = {"error": {"code": 400, "message": "bad query"}}
        feed = WfigsFeed(http_get=make_http_stub([("", 200, payload)]))
        assert feed.fetch_active() == []

    def test_transport_exception_returns_empty(self):
        def exploding(url, headers):
            raise OSError("network down")
        assert WfigsFeed(http_get=exploding).fetch_active() == []


# ---------------------------------------------------------------------------
# FIRIS
# ---------------------------------------------------------------------------

FIRIS_PAYLOAD = {"features": [
    {"attributes": {"OBJECTID": 3, "mission": "BRIDGE", "irwin_id": "{DEF-9}",
                    "gis_acres": 123.4, "date_current": 1751795000000},
     "geometry": {"rings": [[[-121.00, 39.00], [-120.99, 39.00],
                             [-120.99, 39.01], [-121.00, 39.00]]]}},
]}


class TestFirisFeed:
    def test_parses_combo_layer_with_tolerant_fields(self):
        feed = FirisFeed(http_get=make_http_stub([
            ("CA_Perimeters_NIFC_FIRIS_public_view", 200, FIRIS_PAYLOAD)]))
        incs = feed.fetch_active()
        assert len(incs) == 1
        inc = incs[0]
        assert inc.incident_id == "def-9"
        assert inc.irwin_id == "def-9"
        assert inc.name == "BRIDGE"
        assert inc.source is IncidentSource.FIRIS
        assert inc.acreage == 123.4
        assert inc.updated_at_s == 1751795000.0
        assert inc.perimeter[0][0] == LatLon(39.00, -121.00)

    def test_failure_returns_empty(self):
        feed = FirisFeed(http_get=lambda url, headers: (503, b""))
        assert feed.fetch_active() == []


# ---------------------------------------------------------------------------
# CAL FIRE
# ---------------------------------------------------------------------------

CALFIRE_PAYLOAD = [
    {"Name": "Creek Fire", "Final": False, "Updated": "2026-07-05T18:22:00Z",
     "Started": "2026-07-04T12:00:00Z", "AcresBurned": 1500.0,
     "PercentContained": 20, "Longitude": -119.3, "Latitude": 37.2,
     "UniqueId": "AAAA-BBBB", "IsActive": True, "County": "Fresno"},
    {"Name": "No Coords", "UniqueId": "nc-1", "Latitude": None, "Longitude": None},
]


class TestCalFireFeed:
    def test_parses_incident_api(self):
        feed = CalFireFeed(http_get=make_http_stub([("IncidentApi/List", 200, CALFIRE_PAYLOAD)]))
        incs = feed.fetch_active()
        assert len(incs) == 1                       # record without coords dropped
        inc = incs[0]
        assert inc.incident_id == "calfire:aaaa-bbbb"
        assert inc.name == "Creek Fire"
        assert inc.source is IncidentSource.CALFIRE
        assert inc.origin == LatLon(37.2, -119.3)
        assert inc.acreage == 1500.0
        assert inc.containment_pct == 20.0
        assert inc.active is True
        assert inc.irwin_id is None
        expect = datetime(2026, 7, 5, 18, 22, tzinfo=timezone.utc).timestamp()
        assert inc.updated_at_s == expect
        expect_started = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc).timestamp()
        assert inc.discovered_at_s == expect_started

    def test_bbox_filter_client_side(self):
        feed = CalFireFeed(http_get=make_http_stub([("IncidentApi", 200, CALFIRE_PAYLOAD)]))
        inside = BBox(-120.0, 37.0, -119.0, 38.0)
        outside = BBox(-118.0, 33.0, -117.0, 34.0)
        assert len(feed.fetch_active(inside)) == 1
        assert feed.fetch_active(outside) == []

    def test_malformed_returns_empty(self):
        feed = CalFireFeed(http_get=lambda url, headers: (200, b"not json at all"))
        assert feed.fetch_active() == []


# ---------------------------------------------------------------------------
# FIRMS
# ---------------------------------------------------------------------------

FIRMS_CSV = "\n".join([
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight",
    "34.100,-118.600,330.1,0.4,0.4,2026-07-05,0130,N20,VIIRS,n,2.0NRT,290.0,12.5,N",
    "34.105,-118.605,331.0,0.4,0.4,2026-07-05,0130,N20,VIIRS,n,2.0NRT,291.0,8.0,N",
    "34.110,-118.610,335.0,0.4,0.4,2026-07-05,1015,N20,VIIRS,n,2.0NRT,295.0,20.0,D",
    "35.500,-117.000,340.0,0.4,0.4,2026-07-05,1015,N20,VIIRS,n,2.0NRT,300.0,30.0,D",
    "35.505,-117.004,341.0,0.4,0.4,2026-07-05,1015,N20,VIIRS,n,2.0NRT,301.0,31.0,D",
]).encode("utf-8")

SOCAL_BBOX = BBox(-121.0, 32.5, -114.0, 36.0)


class TestFirmsFeed:
    def _feed(self):
        return FirmsFeed("TESTKEY", http_get=make_http_stub([("firms", 200, FIRMS_CSV)]))

    def test_url_shape(self):
        stub = make_http_stub([("firms", 200, FIRMS_CSV)])
        FirmsFeed("TESTKEY", http_get=stub).fetch_active(SOCAL_BBOX)
        url = stub.calls[0][0]
        assert "/api/area/csv/TESTKEY/VIIRS_NOAA20_NRT/" in url
        assert "-121.0,32.5,-114.0,36.0/1" in url

    def test_clusters_hotspots_into_incidents(self):
        incs = self._feed().fetch_active(SOCAL_BBOX)
        assert len(incs) == 2                        # ~1 km cluster + ~60 km away
        by_n = sorted(incs, key=lambda i: len(i.hotspots))
        small, big = by_n
        assert len(big.hotspots) == 3
        assert len(small.hotspots) == 2
        assert all(i.source is IncidentSource.FIRMS for i in incs)
        # newest detection first in .hotspots
        assert big.hotspots[0] == LatLon(34.110, -118.610)
        # updated_at_s = newest acquisition (2026-07-05 10:15 UTC)
        expect = datetime(2026, 7, 5, 10, 15, tzinfo=timezone.utc).timestamp()
        assert big.updated_at_s == expect
        # origin ~ centroid of the cluster
        assert abs(big.origin.lat - 34.105) < 0.01
        assert abs(big.origin.lon - (-118.605)) < 0.01
        assert big.meta["n_detections"] == 3
        assert big.ignition_points() == big.hotspots  # no perimeter -> hotspots seed

    def test_malformed_csv_returns_empty(self):
        feed = FirmsFeed("K", http_get=lambda url, headers: (200, b"garbage no header"))
        assert feed.fetch_active(SOCAL_BBOX) == []

    def test_http_error_returns_empty(self):
        feed = FirmsFeed("K", http_get=lambda url, headers: (429, b"rate limited"))
        assert feed.fetch_active(SOCAL_BBOX) == []

    # -- keyless wiring (Sprint 2 ticket C3d, ops review OPS-13) --------------

    def test_constructs_without_map_key(self):
        """FirmsFeed() must never raise at construction (the CLI's bare cls()
        construction used to die with TypeError and silently drop hotspots)."""
        feed = FirmsFeed()
        assert feed.last_fetch_ok is True              # optimistic pre-fetch

    def test_keyless_fetch_is_blind_never_touches_network(self):
        def forbidden(url, headers):
            raise AssertionError(f"keyless FIRMS fetch hit the network: {url}")
        feed = FirmsFeed(http_get=forbidden)
        assert feed.fetch_active(SOCAL_BBOX) == []     # robustness design holds
        assert feed.last_fetch_ok is False             # ...but the fetch is BLIND

    def test_empty_string_key_treated_as_missing(self):
        feed = FirmsFeed(map_key="", http_get=lambda u, h: (200, FIRMS_CSV))
        assert feed.fetch_active(SOCAL_BBOX) == []
        assert feed.last_fetch_ok is False

    def test_keyless_fetch_logs_signup_url_once(self, caplog):
        """ONE loud log naming the free MAP_KEY signup — once per adapter
        instance, not once per poll (a 300 s cadence must not spam)."""
        feed = FirmsFeed(http_get=lambda u, h: (_ for _ in ()).throw(
            AssertionError("network touched")))
        with caplog.at_level(logging.WARNING, logger="wgresponse.feeds"):
            feed.fetch_active(SOCAL_BBOX)
            feed.fetch_active()                        # second keyless poll
        signup = [r for r in caplog.records
                  if "firms.modaps.eosdis.nasa.gov" in r.getMessage()]
        assert len(signup) == 1
        msg = signup[0].getMessage()
        assert "MAP_KEY" in msg
        assert FirmsFeed.SIGNUP_URL in msg
        assert feed.last_fetch_ok is False             # still blind on poll 2

    def test_keyed_fetch_unaffected_by_keyless_wiring(self):
        feed = self._feed()
        incs = feed.fetch_active(SOCAL_BBOX)
        assert len(incs) == 2
        assert feed.last_fetch_ok is True


# ---------------------------------------------------------------------------
# Watch Duty stub
# ---------------------------------------------------------------------------

class TestWatchDutyStub:
    def test_raises_until_licensed(self):
        with pytest.raises(RuntimeError, match="not yet licensed"):
            WatchDutyEnterpriseFeed().fetch_active()

    def test_message_names_tos_prohibition(self):
        with pytest.raises(RuntimeError, match="ToS-prohibited"):
            WatchDutyEnterpriseFeed(api_key="future-key").fetch_active(SOCAL_BBOX)


# ---------------------------------------------------------------------------
# Mock feed
# ---------------------------------------------------------------------------

class TestMockFireFeed:
    def test_deterministic_same_seed(self):
        a = MockFireFeed("seed-1").fetch_active()
        b = MockFireFeed("seed-1").fetch_active()
        assert [(i.incident_id, i.origin, i.updated_at_s, i.acreage) for i in a] == \
               [(i.incident_id, i.origin, i.updated_at_s, i.acreage) for i in b]

    def test_different_seed_differs(self):
        a = MockFireFeed("seed-1").fetch_active()
        b = MockFireFeed("seed-2").fetch_active()
        assert [i.origin for i in a] != [i.origin for i in b]

    def test_bbox_filter(self):
        feed = MockFireFeed("seed-1", center=LatLon(34.2, -118.5), spread_km=10.0)
        near = BBox(-119.5, 33.2, -117.5, 35.2)
        far = BBox(-90.0, 20.0, -89.0, 21.0)
        assert len(feed.fetch_active(near)) == 3
        assert feed.fetch_active(far) == []

    def test_explicit_incident_list(self):
        inc = FireIncident(incident_id="x", name="X", source=IncidentSource.MOCK,
                           origin=LatLon(1.0, 2.0), updated_at_s=0.0)
        assert MockFireFeed("s", incidents=[inc]).fetch_active() == [inc]

    def test_incidents_have_perimeters_and_irwin(self):
        for inc in MockFireFeed("seed-1").fetch_active():
            assert inc.perimeter and len(inc.perimeter[0]) == 5
            assert inc.irwin_id
            assert inc.active


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def _mk(iid, name, source, origin, updated, irwin=None, **kw):
    return FireIncident(incident_id=iid, name=name, source=source, origin=origin,
                        updated_at_s=updated, irwin_id=irwin, **kw)


class TestFuseIncidents:
    def test_joins_on_irwin_case_insensitive_brace_stripped(self):
        a = _mk("abc-123", "Canyon", IncidentSource.WFIGS, LatLon(34.10, -118.60),
                2000.0, irwin="{ABC-123}", containment_pct=10.0,
                hotspots=[LatLon(34.1, -118.6)], source_ids={"wfigs": "abc-123"})
        b = _mk("abc-123", "Canyon", IncidentSource.FIRIS, LatLon(34.101, -118.601),
                3000.0, irwin="ABC-123",
                perimeter=[[LatLon(34.09, -118.61), LatLon(34.11, -118.59)]],
                hotspots=[LatLon(34.102, -118.602)], source_ids={"firis": "3"})
        c = _mk("calfire:xyz", "CANYON", IncidentSource.CALFIRE,
                LatLon(34.105, -118.605), 4000.0, containment_pct=25.0,
                acreage=600.0, source_ids={"calfire": "xyz"})
        e = _mk("calfire:far", "Other Fire", IncidentSource.CALFIRE,
                LatLon(36.0, -120.0), 1000.0, source_ids={"calfire": "far"})
        fused = fuse_incidents([[a], [b], [c, e]])
        assert len(fused) == 2
        f = next(i for i in fused if i.incident_id == "abc-123")
        assert f.irwin_id == "abc-123"
        assert f.updated_at_s == 4000.0
        assert f.containment_pct == 25.0            # newest member wins per field
        assert f.acreage == 600.0
        assert f.perimeter == b.perimeter           # newest member WITH a perimeter
        assert set(f.source_ids) == {"wfigs", "firis", "calfire"}  # provenance kept
        hk = {(round(p.lat, 6), round(p.lon, 6)) for p in f.hotspots}
        assert hk == {(34.1, -118.6), (34.102, -118.602)}          # union
        other = next(i for i in fused if i.incident_id == "calfire:far")
        assert other.name == "Other Fire"

    def test_name_join_requires_proximity(self):
        a = _mk("calfire:1", "Ridge Fire", IncidentSource.CALFIRE,
                LatLon(34.0, -118.0), 100.0)
        b = _mk("calfire:2", "Ridge Fire", IncidentSource.CALFIRE,
                LatLon(34.5, -118.0), 200.0)     # ~55 km away: distinct fire
        assert len(fuse_incidents([[a], [b]])) == 2

    def test_name_join_within_5km(self):
        a = _mk("calfire:1", "Ridge Fire", IncidentSource.CALFIRE,
                LatLon(34.00, -118.00), 100.0)
        b = _mk("firms:x", "ridge fire", IncidentSource.FIRMS,
                LatLon(34.02, -118.00), 200.0)   # ~2.2 km
        fused = fuse_incidents([[a], [b]])
        assert len(fused) == 1
        assert fused[0].updated_at_s == 200.0

    def test_empty_and_singleton_batches(self):
        assert fuse_incidents([]) == []
        assert fuse_incidents([[], []]) == []
        a = _mk("only:1", "Solo", IncidentSource.MOCK, LatLon(1.0, 2.0), 5.0)
        assert fuse_incidents([[a], []]) == [a]

    def test_output_sorted_by_incident_id(self):
        a = _mk("zzz:1", "Z", IncidentSource.MOCK, LatLon(1.0, 1.0), 1.0)
        b = _mk("aaa:1", "A", IncidentSource.MOCK, LatLon(2.0, 2.0), 1.0)
        assert [i.incident_id for i in fuse_incidents([[a, b]])] == ["aaa:1", "zzz:1"]


class TestFusionNameNormalization:
    """Sprint 2 ticket C3b (Council-1 OPS-16): the live VOLCANO fire arrived
    as FOUR fused incidents — WFIGS+FIRIS 'VOLCANO', CAL FIRE 'Volcano Fire',
    two extra FIRIS perimeter rows — driving 4 terrain jobs + up to 4 parallel
    alert/notification streams per client for ONE fire. Names are now
    normalized before the fallback join and the fused id is a stable source
    preference, so the same physical fire fuses to one incident and KEEPS one
    incident_id across cycles (alert pairs key on incident_id: an id that
    flips with freshness is a client-visible rename every cycle)."""

    V_ORIGIN = LatLon(33.531, -117.298)     # the live VOLCANO fire (ops review §1.4)

    def _near(self, dlat, dlon):
        return LatLon(self.V_ORIGIN.lat + dlat, self.V_ORIGIN.lon + dlon)

    def _volcano_members(self, wfigs_irwin):
        """WFIGS once (with/without IRWIN) + FIRIS twice + CAL FIRE once —
        all the same physical fire, every origin within ~1 km."""
        wfigs = _mk("volcano-irwin-1" if wfigs_irwin else "wfigs:801",
                    "VOLCANO", IncidentSource.WFIGS, self.V_ORIGIN, 3000.0,
                    irwin=wfigs_irwin, source_ids={"wfigs": "801"})
        firis_a = _mk("firis:192", "VOLCANO", IncidentSource.FIRIS,
                      self._near(0.004, 0.0), 5000.0,
                      perimeter=[[self._near(0.004, 0.0),
                                  self._near(0.005, 0.001)]],
                      source_ids={"firis": "192"})
        firis_b = _mk("firis:195", "Volcano", IncidentSource.FIRIS,
                      self._near(-0.003, 0.002), 4000.0,
                      source_ids={"firis": "195"})
        calfire = _mk("calfire:vol-1", "Volcano Fire", IncidentSource.CALFIRE,
                      self._near(0.008, -0.004), 6000.0, containment_pct=35.0,
                      source_ids={"calfire": "vol-1"})
        return wfigs, firis_a, firis_b, calfire

    def test_volcano_pattern_fuses_to_one_with_irwin(self):
        wfigs, fa, fb, cf = self._volcano_members("{VOLCANO-IRWIN-1}")
        fused = fuse_incidents([[wfigs], [fa, fb], [cf]])
        assert len(fused) == 1, [i.incident_id for i in fused]
        f = fused[0]
        assert f.incident_id == "volcano-irwin-1"      # canonical: IRWIN wins
        assert f.irwin_id == "volcano-irwin-1"
        assert set(f.source_ids) == {"wfigs", "firis", "calfire"}   # provenance
        assert f.updated_at_s == 6000.0                # newest member wins
        assert f.containment_pct == 35.0
        assert f.perimeter == fa.perimeter             # newest member WITH one

    def test_volcano_pattern_fuses_to_one_without_irwin(self):
        wfigs, fa, fb, cf = self._volcano_members(None)
        fused = fuse_incidents([[wfigs], [fa, fb], [cf]])
        assert len(fused) == 1, [i.incident_id for i in fused]
        f = fused[0]
        # Canonical id = the most-preferred SOURCE present (wfigs), NOT the
        # freshest member (calfire at 6000.0).
        assert f.incident_id == "wfigs:801"
        assert f.irwin_id is None
        assert set(f.source_ids) == {"wfigs", "firis", "calfire"}

    def test_canonical_id_stable_across_freshness_shuffles(self):
        """The SAME physical fire keeps ONE incident_id across cycles even as
        the relative freshness of its sources shuffles poll to poll."""
        def fused_id(wfigs_t, firis_t, calfire_t):
            wfigs, fa, fb, cf = self._volcano_members(None)
            wfigs.updated_at_s = wfigs_t
            fa.updated_at_s, fb.updated_at_s = firis_t, firis_t - 500.0
            cf.updated_at_s = calfire_t
            return fuse_incidents([[wfigs], [fa, fb], [cf]])[0].incident_id
        assert {fused_id(9000, 2000, 1000),            # wfigs freshest
                fused_id(1000, 9000, 2000),            # firis freshest
                fused_id(2000, 1000, 9000)} == {"wfigs:801"}   # calfire freshest

    def test_canonical_id_stable_as_sources_phase_in_and_out(self):
        """firis+calfire only (wfigs absent this cycle): the firis id wins by
        source preference regardless of calfire being newest — and it is the
        lexicographic MIN of the firis members, not the freshest one."""
        _, fa, fb, cf = self._volcano_members(None)
        assert fa.updated_at_s > fb.updated_at_s       # freshness != tie-break
        f = fuse_incidents([[fa, fb], [cf]])
        assert len(f) == 1
        assert f[0].incident_id == "firis:192"
        # calfire alone next cycle would keep its own id (nothing to prefer)
        assert fuse_incidents([[cf]])[0].incident_id == "calfire:vol-1"

    def test_name_join_when_irwin_ids_differ(self):
        """Two sources minted DIFFERENT IRWIN ids for one nearby same-named
        fire: the name join still fuses them and the fused irwin/id is the
        stable (sorted) choice, not the freshest member's."""
        a = _mk("aaa-1", "Ridge Fire", IncidentSource.WFIGS,
                LatLon(34.0, -118.0), 100.0, irwin="{AAA-1}")
        b = _mk("bbb-2", "RIDGE", IncidentSource.FIRIS,
                LatLon(34.02, -118.0), 200.0, irwin="BBB-2")   # ~2.2 km, newer
        fused = fuse_incidents([[a], [b]])
        assert len(fused) == 1
        assert fused[0].incident_id == "aaa-1"
        assert fused[0].irwin_id == "aaa-1"

    def test_normalized_join_still_requires_proximity(self):
        a = _mk("calfire:1", "Volcano Fire", IncidentSource.CALFIRE,
                LatLon(34.0, -118.0), 100.0)
        b = _mk("wfigs:2", "VOLCANO", IncidentSource.WFIGS,
                LatLon(34.5, -118.0), 200.0)           # ~55 km: distinct fire
        assert len(fuse_incidents([[a], [b]])) == 2

    def test_suffix_only_name_never_joins(self):
        """A name that normalizes to '' (e.g. literally 'Fire') must never
        become a wildcard join key."""
        a = _mk("calfire:1", "Fire", IncidentSource.CALFIRE,
                LatLon(34.0, -118.0), 100.0)
        b = _mk("wfigs:2", "FIRE", IncidentSource.WFIGS,
                LatLon(34.005, -118.0), 200.0)         # 500 m apart
        assert len(fuse_incidents([[a], [b]])) == 2


class TestNormName:
    def test_suffix_and_case_and_punctuation(self):
        assert _norm_name("Volcano Fire") == "volcano"
        assert _norm_name("VOLCANO") == "volcano"
        assert _norm_name("  Creek   FIRE ") == "creek"
        assert _norm_name("Route Incident") == "route"
        assert _norm_name("Bridge Fire Incident") == "bridge"   # both trailing tokens
        assert _norm_name("St. Anne's Fire") == "st anne s"

    def test_punctuation_and_whitespace_equivalence(self):
        assert _norm_name("3-1 PIT") == _norm_name("3 1 Pit") == "3 1 pit"

    def test_leading_fire_token_is_kept(self):
        assert _norm_name("Fire Mountain") == "fire mountain"

    def test_empty_and_suffix_only_normalize_to_empty(self):
        assert _norm_name("Fire") == ""
        assert _norm_name("FIRE INCIDENT") == ""
        assert _norm_name("") == ""
        assert _norm_name(None) == ""
        assert _norm_name("  -  ") == ""


class TestHelpers:
    def test_norm_irwin(self):
        assert _norm_irwin("{AbC-1}") == "abc-1"
        assert _norm_irwin(" ABC ") == "abc"
        assert _norm_irwin(None) is None
        assert _norm_irwin("") is None
        assert _norm_irwin("{}") is None

    def test_haversine_sanity(self):
        # one degree of latitude ~ 111 km
        d = _haversine_m(LatLon(34.0, -118.0), LatLon(35.0, -118.0))
        assert 110_000 < d < 112_500
        assert _haversine_m(LatLon(34.0, -118.0), LatLon(34.0, -118.0)) == 0.0
