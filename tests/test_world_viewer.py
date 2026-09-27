"""The W6 thin world viewer (2026-09-26; docs/LIVE_WORLD_PLAN.md §3 step 1,
"W6 thin viewer" under W6): a CONUS AOI for the watcher, /world (the page)
and /api/world/summary (wgresponse.worldview, the read model), and the CONUS
feed checks (the ArcGIS page cap is loud; FIRMS at national scale is one
legal request and a loud note).

No network: feeds use injected http_get fakes, the daemon runs on stub
providers, the page is checked at source level (the tests/test_simulator_ui
style) — tier law, offline shim, external hosts, the pieces each tier draws.
"""
import http.client
import json
import logging
import math
import re
import threading
import urllib.parse
from pathlib import Path

import pytest

from wgresponse import cli, feeds, geo, server, simui, worldview
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    BBox,
    ClientProperty,
    CycleResult,
    EnsembleArrival,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    Raster,
    ResponseConfig,
    TerrainTile,
    ThreatAssessment,
    ThreatEta,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.notify import LogNotifier
from wgresponse.persist import StateStore
from wgresponse.threat import ThreatMonitor

ROOT = Path(__file__).resolve().parent.parent
WORLD_HTML = ROOT / "console" / "world.html"
CONSOLE_HTML = ROOT / "console" / "index.html"
AOI_CONUS = ROOT / "data" / "aoi_conus.json"
CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
KEY = "world-key-123"
T0 = 1_790_000_000.0


# ---------------------------------------------------------------------------
# 1. the CONUS AOI
# ---------------------------------------------------------------------------

def test_aoi_conus_parses_at_conus_scale():
    aoi, zones, desc = cli._parse_aoi(str(AOI_CONUS))
    assert (aoi.west, aoi.south, aoi.east, aoi.north) == (-125.0, 24.0, -66.0, 50.0)
    assert "conus" in desc and "42 safe zone(s)" in desc
    # the union of every simulator preset's zones, each labelled UNVERIFIED
    want = {z.zone_id: z for loc in simui.LOCATIONS.values() for z in loc.safe_zones}
    got = {z.zone_id: z for z in zones}
    assert set(got) == set(want) and len(zones) == len(want), (
        "data/aoi_conus.json is stale — regenerate: py -3 tools/gen_aoi_conus.py")
    for zid, z in got.items():
        assert z.loc == want[zid].loc, zid
        assert z.name == f"UNVERIFIED {want[zid].name}", zid
        assert aoi.contains(z.loc), zid
    for key, loc in simui.LOCATIONS.items():
        assert aoi.contains(loc.center), key
    doc = json.loads(AOI_CONUS.read_text(encoding="utf-8"))
    assert "LIVE_WORLD_PLAN" in doc["_comment"]
    assert "UNVERIFIED" in doc["_comment"] or "NOT ground-verified" in doc["_comment"]


def test_aoi_conus_file_is_the_generators_output():
    from tools import gen_aoi_conus
    assert json.loads(AOI_CONUS.read_text(encoding="utf-8")) == gen_aoi_conus.build(), (
        "data/aoi_conus.json is stale — regenerate: py -3 tools/gen_aoi_conus.py")


def test_conus_aoi_passes_watch_validation_and_threads_into_the_envelope():
    cli._validate_watch_bbox(CONUS)            # no exception at CONUS scale
    urls = []

    def fake_get(url, headers=None):
        urls.append(url)
        return 200, json.dumps({"features": []}).encode("utf-8")

    feed = feeds.WfigsFeed(http_get=fake_get)
    assert feed.fetch_active(CONUS) == [] and feed.last_fetch_ok is True
    assert len(urls) == 2                       # points + perimeters, one page each
    for url in urls:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        env = json.loads(q["geometry"][0])
        assert (env["xmin"], env["ymin"], env["xmax"], env["ymax"]) == (
            -125.0, 24.0, -66.0, 50.0)
        assert q["geometryType"] == ["esriGeometryEnvelope"]


# ---------------------------------------------------------------------------
# 2. CONUS feed checks: the ArcGIS page cap is loud; FIRMS is one request
# ---------------------------------------------------------------------------

def _paged_get(pages_with_more, per_page=3):
    """ArcGIS fake: `pages_with_more` full pages flagged
    exceededTransferLimit, then (if finite) a final short page."""
    calls = []

    def get(url, headers=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        k = int(q.get("resultOffset", ["0"])[0]) // per_page
        calls.append(k)
        more = k < pages_with_more
        feats = [{"attributes": {"IncidentName": f"F{k}-{i}",
                                 "IrwinID": f"{{{k:04d}-{i:04d}}}",
                                 "IncidentTypeCategory": "WF",
                                 "ModifiedOnDateTime_dt": T0 * 1000.0},
                  "geometry": {"x": -110.0 + k * 0.1, "y": 40.0 + i * 0.01}}
                 for i in range(per_page if more else 1)]
        body = {"features": feats}
        if more:
            body["exceededTransferLimit"] = True
        return 200, json.dumps(body).encode("utf-8")
    return get, calls


def test_arcgis_pager_within_the_cap_is_unchanged():
    get, calls = _paged_get(pages_with_more=2)        # 3 pages, last short
    feed = feeds.WfigsFeed(http_get=get)
    feed.PERIMETERS_URL = feed.POINTS_URL             # both layers: same fake
    out = feed.fetch_active(CONUS)
    assert feed.last_fetch_ok is True and feed.last_fetch_truncated is False
    assert len(out) == 3 + 3 + 1


def test_arcgis_page_cap_is_loud_and_keeps_the_data(caplog):
    get, calls = _paged_get(pages_with_more=10 ** 6)  # the server never ends
    feed = feeds.WfigsFeed(http_get=get)
    with caplog.at_level(logging.ERROR, logger="wgresponse.feeds"):
        out = feed.fetch_active(CONUS)
    # every fetched feature is kept (partial data is real data) ...
    assert len(out) == feeds._ArcGisFeedBase._MAX_PAGES * 3
    # ... but the fetch is BLIND, never 'genuinely clear'
    assert feed.last_fetch_truncated is True
    assert feed.last_fetch_ok is False
    assert any("page cap reached" in r.message for r in caplog.records)
    # the next healthy fetch recovers
    ok_get, _ = _paged_get(pages_with_more=0)
    feed._http_get = ok_get
    feed.fetch_active(CONUS)
    assert feed.last_fetch_ok is True and feed.last_fetch_truncated is False


def test_firis_page_cap_is_loud_too(caplog):
    def get(url, headers=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        k = int(q.get("resultOffset", ["0"])[0])
        ring = [[-120.0 + k * 1e-3, 38.0], [-120.0 + k * 1e-3, 38.01],
                [-119.99 + k * 1e-3, 38.01], [-120.0 + k * 1e-3, 38.0]]
        body = {"features": [{"attributes": {"OBJECTID": k,
                                             "poly_IncidentName": f"P{k}"},
                              "geometry": {"rings": [ring]}}],
                "exceededTransferLimit": True}
        return 200, json.dumps(body).encode("utf-8")

    feed = feeds.FirisFeed(http_get=get)
    with caplog.at_level(logging.ERROR, logger="wgresponse.feeds"):
        out = feed.fetch_active(CONUS)
    assert len(out) == feeds._ArcGisFeedBase._MAX_PAGES
    assert feed.last_fetch_ok is False and feed.last_fetch_truncated is True


def test_firms_conus_is_one_area_request():
    """FIRMS area API docs (checked 2026-09-26): any bbox up to the world is
    legal ('Maximum boundary: -180, -90, 180, 90') — so a CONUS AOI is ONE
    request with the AOI's own coordinates, never tiled, never skipped."""
    urls = []

    def get(url, headers=None):
        urls.append(url)
        return 200, b"latitude,longitude,acq_date,acq_time,frp\n"

    feed = feeds.FirmsFeed(map_key="KEY", http_get=get)
    assert feed.fetch_active(CONUS) == [] and feed.last_fetch_ok is True
    assert len(urls) == 1
    assert "/-125.0,24.0,-66.0,50.0/1" in urls[0]


def test_firms_national_scale_note_is_loud_once():
    stack = [feeds.WfigsFeed(), feeds.FirmsFeed(map_key="KEY")]
    (note,) = cli._firms_scale_notes(stack, CONUS)
    assert "NATIONAL AOI" in note and "one area request per cycle" in note
    assert "never name-join" in note
    # regional AOIs and a FIRMS-less stack stay quiet
    socal = BBox(west=-119.3, south=33.6, east=-117.5, north=34.6)
    assert cli._firms_scale_notes(stack, socal) == []
    assert cli._firms_scale_notes([feeds.WfigsFeed()], CONUS) == []


# ---------------------------------------------------------------------------
# 3. the read model (wgresponse.worldview) on a synthetic cycle
# ---------------------------------------------------------------------------

SPEC = GridSpec(north_lat=39.10, west_lon=-121.12, cell_m=500.0, nrows=40, ncols=40)
CENTER = geo.rc_to_latlon(SPEC, 20, 20)


def _ens(iid, ros_mps=0.5, burned_m=1500.0):
    data = []
    for r in range(SPEC.nrows):
        for c in range(SPEC.ncols):
            d = geo.haversine_m(geo.rc_to_latlon(SPEC, r, c), CENTER)
            data.append(T0 + (d - burned_m) / ros_mps)
    p50 = Raster(SPEC, data)
    return EnsembleArrival(spec=SPEC, members=[p50], p10=p50, p50=p50,
                           p90=p50, t0_s=T0, incident_id=iid)


def _inc(iid, acres, source=IncidentSource.WFIGS, perimeter=None, lat=39.0,
         lon=-121.0):
    return FireIncident(incident_id=iid, name=f"Fire {iid}", source=source,
                        origin=LatLon(lat, lon), updated_at_s=T0 - 3600.0,
                        acreage=acres, containment_pct=10.0, active=True,
                        perimeter=perimeter, source_ids={source.value: iid})


def _assess(cid, iid, p10_h):
    t = math.inf if p10_h is None else T0 + p10_h * 3600.0
    return ThreatAssessment(client_id=cid, incident_id=iid,
                            eta=ThreatEta(t, t + (0 if p10_h is None else 3600.0),
                                          math.inf), computed_at_s=T0)


def _square(lat, lon, half_deg):
    return [LatLon(lat - half_deg, lon - half_deg), LatLon(lat - half_deg, lon + half_deg),
            LatLon(lat + half_deg, lon + half_deg), LatLon(lat + half_deg, lon - half_deg),
            LatLon(lat - half_deg, lon - half_deg)]


def _cycle():
    many_rings = [_square(39.0, -121.0, 0.05 / (k + 1)) for k in range(20)]
    incs = [
        _inc("A", 100.0),
        _inc("B", 50.0),
        _inc("C", 10.0),
        _inc("D", 5000.0, perimeter=many_rings),
        _inc("E", None, source=IncidentSource.CALFIRE),
        _inc("firms:34.100,-118.200", 500.0, source=IncidentSource.FIRMS,
             lat=34.1, lon=-118.2),
    ]
    ens = {"A": _ens("A"), "B": _ens("B"), "C": _ens("C")}
    assessments = [_assess("c1", "A", 3.0), _assess("c2", "A", 5.0),
                   _assess("c3", "B", 1.0), _assess("c4", "C", None)]
    notes = [
        "excluded 7 stale incident(s) from alerting/dispatch (updated_at older "
        "than 168 h — SAFETY SEMANTICS 9): x (age 200 h)",
        "incident D: no protected clients within 60 km; spread skipped",
        "incident E: assessment skipped (terrain provider failed (LFPS 500))",
        "incident firms:34.100,-118.200: no protected clients within 60 km; "
        "spread skipped",
        "incident A: tracked client(s) c9 missing from the registry roster "
        "while the incident is active — pair(s) treated as BLIND",
    ]
    timings = {"incidents": {"A": {"terrain_s": 1.5, "weather_s": 0.2,
                                   "spread_s": 3.25, "cells": 1600, "clients": 2}}}
    return CycleResult(t_s=T0, incidents=incs, assessments=assessments,
                       ensembles=ens, notes=notes, timings=timings)


STATE = {"cycles_run": 3, "aoi": {"west": -125.0, "south": 24.0, "east": -66.0,
                                  "north": 50.0},
         "aoi_desc": "zones file aoi_conus.json (conus; union bbox "
                     "-125,24,-66,50; 42 safe zone(s))",
         "clients": [{"client_id": "c1", "state": "watch"},
                     {"client_id": "c2", "state": "none"},
                     {"client_id": "c3", "state": "extreme_24h"}],
         "crews": [{"crew_id": "G1"}], "safe_zones": [{"zone_id": "Z1"}] * 4}


def test_incident_notes_match_ids_with_colons():
    res = _cycle()
    m = worldview.incident_notes(res.notes, [i.incident_id for i in res.incidents])
    assert m["D"] == ["no protected clients within 60 km; spread skipped"]
    assert m["firms:34.100,-118.200"] == [
        "no protected clients within 60 km; spread skipped"]
    assert m["E"][0].startswith("assessment skipped")
    assert "B" not in m
    assert worldview.count_excluded_stale(res.notes) == 7
    assert worldview.count_excluded_stale(["nothing"]) == 0


def test_summary_ranks_domains_by_first_p10_then_the_rest_by_acreage():
    s = worldview.build_summary(STATE, _cycle())
    order = [r["incident_id"] for r in s["incidents"]]
    # B (first client P10 +1 h) < A (+3 h) < C (every client unreached),
    # then by acreage: D 5000 > FIRMS 500 > E (no acreage)
    assert order == ["B", "A", "C", "D", "firms:34.100,-118.200", "E"]
    assert [r["rank"] for r in s["incidents"]] == [1, 2, 3, 4, 5, 6]
    by = {r["incident_id"]: r for r in s["incidents"]}
    assert by["A"]["domain"]["clients"] == 2
    assert by["A"]["domain"]["eta_p10_s"] == T0 + 3 * 3600.0
    assert by["A"]["domain"]["first_client_id"] == "c1"
    assert by["A"]["domain"]["spread_s"] == 3.25
    assert by["A"]["domain"]["cells"] == 1600 and by["A"]["domain"]["cell_m"] == 500.0
    assert by["C"]["domain"]["eta_p10_s"] is None           # unreached
    # why no domain? — the cycle's own words
    assert by["D"]["why_no_domain"] == "no protected clients within 60 km; spread skipped"
    assert by["E"]["why_no_domain"].startswith("assessment skipped")
    assert by["A"]["why_no_domain"] is None
    assert any("missing from the registry roster" in n for n in by["A"]["notes"])
    c = s["counts"]
    assert c == {"incidents": 6, "with_domain": 3, "with_perimeter": 1,
                 "by_source": {"calfire": 1, "firms": 1, "wfigs": 4},
                 "excluded_stale": 7, "clients": 3, "clients_in_domains": 4,
                 "clients_alerted": 2, "crews": 1, "safe_zones": 4}
    assert s["aoi_name"] == "conus"
    assert s["tiers"] == {"national_max_zoom": 6, "regional_max_zoom": 11}
    json.dumps(s, allow_nan=False)          # strict JSON: no inf / NaN leaks


def test_summary_geometry_p50_rings_and_display_perimeter():
    s = worldview.build_summary(STATE, _cycle())
    by = {r["incident_id"]: r for r in s["incidents"]}
    leads = [iso["h"] for iso in by["A"]["p50_rings"]]
    assert leads[0] == 0.0 and set(leads) <= set(worldview.P50_LEADS_H)
    assert len(leads) >= 2
    for iso in by["A"]["p50_rings"]:
        for ring in iso["rings"]:
            assert ring[0] == ring[-1] and len(ring) <= worldview.RING_MAX_VERTS + 1
            assert all(SPEC.west_lon - 0.01 <= p[1] <= -120.8 and 38.9 <= p[0] <= 39.11
                       for p in ring)
    assert by["D"]["p50_rings"] == []                     # no domain, no rings
    # the display perimeter: capped, largest first, closed, rounded
    per = by["D"]["perimeter"]
    assert by["D"]["perimeter_rings_total"] == 20
    assert 1 <= len(per) <= worldview.PERIM_MAX_RINGS
    areas = [abs(p[2][0] - p[0][0]) for p in per]
    assert areas == sorted(areas, reverse=True)
    for ring in per:
        assert ring[0] == ring[-1] and len(ring) <= worldview.PERIM_MAX_VERTS + 1
        assert all(round(v, 5) == v for p in ring for v in p)
    # the engine's own rings are untouched
    assert len(_cycle().incidents[3].perimeter) == 20
    # geometry=False skips both
    s2 = worldview.build_summary(STATE, _cycle(), geometry=False)
    assert all(r["perimeter"] is None and r["p50_rings"] == [] for r in s2["incidents"])


def test_display_perimeter_drops_specks_but_says_so():
    big = _square(39.0, -121.0, 0.1)
    speck = _square(39.2, -121.2, 0.0001)            # 1e-8 of the big area
    rings, total = worldview.display_perimeter([speck, big])
    assert total == 2 and len(rings) == 1
    assert rings[0][0] == [38.9, -121.1]


def test_summary_from_state_claims_no_domains():
    st = {"incidents": [{"incident_id": "x", "name": "X", "source": "wfigs",
                         "lat": 1.0, "lon": 2.0, "acreage": 10.0},
                        {"incident_id": "y", "name": "Y", "source": "firms",
                         "lat": 1.0, "lon": 2.0, "acreage": 900.0},
                        {"name": "no id"}],
          "last_cycle_t_s": T0, "clients": [], "crews": []}
    s = worldview.summary_from_state(st)
    assert [r["incident_id"] for r in s["incidents"]] == ["y", "x"]
    assert all(r["domain"] is None and "domains unknown" in r["why_no_domain"]
               for r in s["incidents"])
    assert s["counts"]["with_domain"] == 0 and s["t_s"] == T0
    assert worldview.summary_from_state(None)["incidents"] == []


# ---------------------------------------------------------------------------
# 4. the daemon's /api/world/summary provider (real monitor, stub providers)
# ---------------------------------------------------------------------------

ORIGIN = LatLon(39.0, -121.0)
AOI = BBox(west=-121.2, south=38.8, east=-120.8, north=39.2)


class _Feed:
    def __init__(self, incidents):
        self._incidents = incidents
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        return list(self._incidents)


class _Weather:
    def forecast(self, loc, horizon_h):
        return WeatherForecast(loc=loc, source="stub", samples=[
            WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=8.0,
                          wind_gust_mps=10.0, wind_dir_deg=270.0, rh_pct=20.0,
                          temp_c=30.0) for i in range(int(horizon_h) + 2)])


class _Terrain:
    def tile(self, bbox, cell_m):
        ctr = bbox.center()
        nrows = max(1, math.ceil((bbox.north - bbox.south) * geo.M_PER_DEG_LAT / cell_m))
        ncols = max(1, math.ceil((bbox.east - bbox.west) * geo.m_per_deg_lon(ctr.lat) / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west, cell_m=cell_m,
                        nrows=nrows, ncols=ncols)
        return TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=102.0),
                           slope_deg=geo.make_raster(spec, fill=0.0),
                           aspect_deg=geo.make_raster(spec, fill=0.0),
                           elev_m=geo.make_raster(spec, fill=100.0), source="stub")


class _Registry:
    def __init__(self, clients):
        self._clients = clients

    def protected_clients(self, bbox=None):
        return [c for c in self._clients if bbox is None or bbox.contains(c.loc)]


class _Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        self.t += 1.0
        return self.t


def _daemon(tmp_path):
    cfg = ResponseConfig(horizon_h=24.0, ensemble_n=2, grid_cell_m=200.0,
                         grid_max_cells=20_000)
    near = _inc("near-1", 400.0, lat=ORIGIN.lat, lon=ORIGIN.lon)
    near.updated_at_s = T0 - 600.0
    far = _inc("far-1", 9000.0, lat=45.0, lon=-110.0)
    far.updated_at_s = T0 - 600.0
    client = ClientProperty(client_id="c1", loc=LatLon(39.0, -120.95),
                            name="Casa Uno")
    registry = _Registry([client])
    engine = AlertEngine(cfg, notifier=LogNotifier(), clients_by_id={"c1": client})
    monitor = ThreatMonitor(feeds=[_Feed([near, far])], weather_provider=_Weather(),
                            terrain_provider=_Terrain(), registry=registry,
                            alert_engine=engine, cfg=cfg, safe_zones=[],
                            crew_tracker=None, dispatcher=None, aoi=AOI)
    return cli._WatchDaemon(monitor=monitor, engine=engine, registry=registry,
                            tracker=None, store=StateStore(tmp_path / "state"),
                            cfg=cfg, aoi=AOI, period_s=300.0,
                            aoi_desc="zones file t.json (test; union bbox x)",
                            clock=_Clock(), sleep=lambda s: None)


def test_daemon_world_summary_is_built_once_per_cycle(tmp_path):
    d = _daemon(tmp_path)
    before = d.world_summary()
    assert before["incidents"] == [] and before["note"] == "no cycle has completed yet"
    res = d.run_cycle()
    s1 = d.world_summary()
    assert s1["t_s"] == res.t_s and s1["aoi_name"] == "test"
    ids = [r["incident_id"] for r in s1["incidents"]]
    assert ids == ["near-1", "far-1"]                  # the domain ranks first
    near, far = s1["incidents"]
    assert near["domain"]["clients"] == 1 and near["domain"]["eta_p10_s"] > res.t_s
    assert near["p50_rings"]                            # traced from the p50 raster
    assert {iso["h"] for iso in near["p50_rings"]} <= set(worldview.P50_LEADS_H)
    assert far["domain"] is None
    assert far["why_no_domain"].startswith("no protected clients within")
    assert s1["counts"]["with_domain"] == 1
    assert d.world_summary() is s1                     # cached, not rebuilt
    unchanged = d.world_summary(since=repr(res.t_s))
    assert unchanged == {"unchanged": True, "t_s": res.t_s, "cycles_run": 1}
    assert d.world_summary(since="garbage") is s1
    res2 = d.run_cycle()
    s2 = d.world_summary(since=repr(res.t_s))           # the cycle moved on
    assert s2 is not s1 and s2["t_s"] == res2.t_s and s2["cycles_run"] == 2


# ---------------------------------------------------------------------------
# 5. the HTTP surface: /world open, /api/world/summary authed + read-only
# ---------------------------------------------------------------------------

@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    state = {"incidents": [{"incident_id": "i1", "name": "One", "source": "wfigs",
                            "lat": 39.0, "lon": -121.0, "acreage": 12.0}],
             "clients": [], "crews": [], "last_cycle_t_s": T0}
    httpd = server.serve(0, state_provider=lambda: state)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield httpd
    httpd.shutdown()
    th.join(timeout=5)
    httpd.server_close()


def _get(httpd, path, key=None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    try:
        conn.request("GET", path, headers={"X-Internal-API-Key": key} if key else {})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def test_world_page_is_served_openly(srv):
    status, ctype, body = _get(srv, "/world")
    assert status == 200 and ctype.startswith("text/html")
    text = body.decode("utf-8")
    assert "WG Response — World" in text and "installFallbackLeaflet" in text
    assert _get(srv, "/world/")[0] == 200             # trailing slash tolerated


def test_world_summary_is_authed(srv, monkeypatch):
    assert _get(srv, "/api/world/summary")[0] == 401
    assert _get(srv, "/api/world/summary", key="wrong")[0] == 401
    monkeypatch.delenv("WGRESP_API_KEY")
    assert _get(srv, "/api/world/summary", key=KEY)[0] == 401    # fail closed


def test_world_summary_without_a_provider_derives_from_state(srv):
    status, _, body = _get(srv, "/api/world/summary", key=KEY)
    assert status == 200
    s = json.loads(body)
    assert s["schema"] == worldview.SCHEMA
    assert [r["incident_id"] for r in s["incidents"]] == ["i1"]
    assert s["incidents"][0]["domain"] is None


def test_world_summary_provider_gets_since_and_errors_are_json(srv):
    seen = []

    def provider(since=None):
        seen.append(since)
        return {"unchanged": True, "t_s": T0}

    srv.app.world_provider = provider
    status, _, body = _get(srv, f"/api/world/summary?since={T0}", key=KEY)
    assert status == 200 and json.loads(body)["unchanged"] is True
    assert seen == [str(T0)]
    _get(srv, "/api/world/summary", key=KEY)
    assert seen[-1] is None

    def broken(since=None):
        raise RuntimeError("boom")

    srv.app.world_provider = broken
    status, _, body = _get(srv, "/api/world/summary", key=KEY)
    assert status == 500 and "world summary: boom" in json.loads(body)["error"]


def test_other_routes_untouched_by_the_world_routes(srv):
    assert _get(srv, "/console")[0] == 200
    assert _get(srv, "/api/state")[0] == 401
    assert _get(srv, "/api/world")[0] == 404
    assert _get(srv, "/worlds")[0] == 404


# ---------------------------------------------------------------------------
# 6. the page (source-level, the test_simulator_ui style)
# ---------------------------------------------------------------------------

def _html():
    return WORLD_HTML.read_text(encoding="utf-8")


def _shim(html):
    return html[html.index("installFallbackLeaflet"):
                html.index("// world viewer app")]


def test_tier_law_matches_the_read_model():
    html = _html()
    nat = int(re.search(r"const TIER_NATIONAL_MAX_ZOOM = (\d+);", html).group(1))
    reg = int(re.search(r"const TIER_REGIONAL_MAX_ZOOM = (\d+);", html).group(1))
    assert (nat, reg) == (worldview.TIER_NATIONAL_MAX_ZOOM,
                          worldview.TIER_REGIONAL_MAX_ZOOM) == (6, 11)
    fn = html[html.index("function tierFor(z)"):]
    fn = fn[:fn.index("\n}") + 2]
    assert 'if (z <= TIER_NATIONAL_MAX_ZOOM) return "national";' in fn
    assert 'if (z <= TIER_REGIONAL_MAX_ZOOM) return "regional";' in fn
    assert 'return "local";' in fn
    # the three tiers drive what renderMap draws
    rm = html[html.index("function renderMap()"):html.index("function renderAll()")]
    nat_branch = rm[rm.index('if (tier === "national")'):rm.index("} else {")]
    assert "drawClientClusters()" in nat_branch and "drawIncidents(tier)" in nat_branch
    assert "drawPerimetersAndRings" not in nat_branch
    rest = rm[rm.index("} else {"):]
    for call in ("drawPerimetersAndRings()", "drawClients(tier)",
                 'if (tier === "local") drawLocal()'):
        assert call in rest, call


def test_national_tier_markers_badges_clusters_and_ranked_list():
    html = _html()
    assert "function radiusForAcres(a)" in html          # scaled by acreage
    for src in ("wfigs:", "firis:", "calfire:", "firms:", "seeded:"):
        assert src in html[html.index("const SOURCE_COLORS"):], src
    assert 'className: "dom-badge"' in html               # domain badge
    assert "String(r.domain.clients)" in html
    assert 'className: "cl-icon"' in html                  # client clusters
    assert "Incidents threatening clients" in html         # the ranked list
    assert "flyToIncident(r)" in html and 'closest(".rk")' in html
    assert "no clients in range — by acreage" in html


def test_regional_and_local_tiers_draw_what_the_state_carries():
    html = _html()
    per = html[html.index("function drawPerimetersAndRings()"):
               html.index("function drawClientClusters()")]
    assert "r.perimeter" in per and "r.p50_rings" in per and "RING_STYLE" in per
    cl = html[html.index("function drawClients(tier)"):
              html.index("function drawCrewsAndZones(tier)")]
    assert "colorFor(c.state)" in cl                         # the ladder colours
    loc = html[html.index("function drawLocal()"):html.index("function renderRankList()")]
    for frag in ("planIndex()", "egress", "unserved", '$("cardlist")',
                 "local detail arrives with the world merge (W1)"):
        assert frag in loc, frag
    assert 'id="w1chip"' in html                              # visible map note
    assert "fire_view" not in html and "imageOverlay" not in html   # no W1 port


def test_header_clock_counts_and_why_no_domain():
    html = _html()
    for frag in ("agechip", "last_cycle_t_s", "2.5 * pollPeriod", "renderAgeChip",
                 "with domain", "clients", "crews", "aoi_name", "srclegend",
                 "why no domain?", "last_cycle_phases", "cycle_stale", "degradedbar"):
        assert frag in html, frag
    assert '"/api/state"' in html and '"/api/world/summary"' in html
    assert "?since=" in html and "sm.unchanged" in html
    assert "function esc(" in html and "esc(r.name" in html   # feed text escaped


def test_offline_shim_supports_zoom_and_stays_self_contained():
    html = _html()
    assert 'get("nomap") === "1"' in html
    assert 'typeof window.L === "undefined"' in html
    shim = _shim(html)
    for frag in ("_fallback: true", "no basemap offline", "getZoom()", "_zoomAbout",
                 '"wheel"', "fitBounds(", "flyTo(", "flyToBounds(", "getBounds()",
                 "zoomend", "moveend", "dom-badge", "cl-icon", "w1-note",
                 "Math.log((1 + sn) / (1 - sn))"):
        assert frag in shim, frag
    for api in ("map:", "layerGroup:", "circleMarker:", "polygon:", "polyline:",
                "marker:", "divIcon:", "tileLayer:"):
        assert api in shim, api
    assert "unpkg.com" not in shim and "openstreetmap" not in shim.lower()
    assert re.findall(r"https?://[A-Za-z0-9.-]+", shim) == ["http://www.w3.org"]
    # the basemap tileLayer call is guarded exactly like the console's
    idx = html.index("tile.openstreetmap.org")
    assert html.rindex("try", 0, idx) < idx < html.index("catch", idx)


def test_no_external_hosts_beyond_the_console():
    hosts = lambda h: set(re.findall(r"https?://[A-Za-z0-9.-]+", h))
    world = hosts(_html())
    assert world == hosts(CONSOLE_HTML.read_text(encoding="utf-8"))
    assert world == {"https://unpkg.com", "https://tile.openstreetmap.org",
                     "http://www.w3.org"}
    # the same pinned Leaflet build as the console (SRI hashes identical)
    con = CONSOLE_HTML.read_text(encoding="utf-8")
    for sri in re.findall(r'integrity="(sha256-[^"]+)"', con):
        assert sri in _html()
