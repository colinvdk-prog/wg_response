"""tests/test_feeds_faster.py — THE FEEDS FETCHED FASTER (2026-11-18, the
fire -> alert latency review, lane A findings A1-A4; BUILD lane 3; contracts
SAFETY SEMANTICS 57-58; cfg.feeds_cache_bust / feeds_gzip / feeds_parallel;
watch --feeds-cache-bust / --feeds-gzip / --feeds-parallel).

  BUST      a per-query `_t=<int unix s>` on every ArcGIS query and page
            (never on CAL FIRE or FIRMS); OFF = the pinned legacy URLs, byte
            for byte; the records fetched are the same either way.
  GZIP      net.get(gzip=True) asks Accept-Encoding gzip, decodes a gzip
            answer, passes an identity one; a body that does not decompress
            or an encoding we did not ask for raises (never retried) and the
            adapter goes BLIND (SAFETY SEMANTICS 6); OFF = no header, nothing
            decoded; the adapters' records are the same either way.
  TIMINGS   every cycle's timings carry feeds_detail: per adapter its seconds,
            health, records and every request's seconds / status / bytes /
            page (never a URL: FIRMS URLs carry the MAP_KEY).
  PARALLEL  stub feeds that sleep random amounts, raise, report unhealthy:
            the cycle digests (incidents, assessments, alerts, ensembles,
            notes, operator events, degraded, plan) are identical on and off,
            and the fetch overlaps; the live FIRMS sources asked at once give
            the same clusters, the same raw-log file and the same memory.
  CLI       the flags arm the knobs with one note each; no flag = the legacy
            stack and cfg."""
from __future__ import annotations

import dataclasses
import gzip
import io
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import cli, feeds, net, screening  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (BBox, FireIncident, IncidentSource,  # noqa: E402
                                  LatLon, ResponseConfig, validate_config)
from wgresponse.notify import LogNotifier  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
CLOCK_S = 1_795_000_000.7

# The legacy URLs (pinned literally on 9d11793's _ArcGisFeedBase._query).
_ENV = ("geometry=%7B%22xmin%22%3A+-125.0%2C+%22ymin%22%3A+24.0%2C+%22xmax"
        "%22%3A+-66.0%2C+%22ymax%22%3A+50.0%2C+%22spatialReference%22%3A+%7B"
        "%22wkid%22%3A+4326%7D%7D&geometryType=esriGeometryEnvelope&inSR=4326"
        "&spatialRel=esriSpatialRelIntersects")
LEGACY_FIRIS = (feeds.FirisFeed.LAYER_URL + "?where=1%3D1&outFields=%2A"
                "&f=json&returnGeometry=true&outSR=4326&" + _ENV)
LEGACY_POINTS = (feeds.WfigsFeed.POINTS_URL + "?where=1%3D1&outFields="
                 + urllib.parse.quote_plus(feeds.WfigsFeed.POINT_OUT_FIELDS)
                 + "&f=json&returnGeometry=true&outSR=4326&" + _ENV)
LEGACY_PERIMS = (feeds.WfigsFeed.PERIMETERS_URL + "?where=1%3D1&outFields="
                 + urllib.parse.quote_plus(feeds.WfigsFeed.PERIM_OUT_FIELDS)
                 + "&f=json&returnGeometry=true&outSR=4326&" + _ENV)


def _point(oid, irwin, name, lon, lat, mod_ms=1_794_990_000_000):
    return {"attributes": {"IncidentName": name, "IrwinID": "{%s}" % irwin,
                           "IncidentSize": 12.0, "PercentContained": 0,
                           "FireDiscoveryDateTime": mod_ms - 3_600_000,
                           "ModifiedOnDateTime_dt": mod_ms, "POOState": "US-CA",
                           "IncidentTypeCategory": "WF"},
            "geometry": {"x": lon, "y": lat}}


def _poly(oid, name, lon, lat, d=0.01, edit_ms=1_794_990_000_000, **extra):
    a = {"OBJECTID": oid, "incident_name": name, "mission": None,
         "poly_DateCurrent": edit_ms, "EditDate": None,
         "CreationDate": edit_ms, "area_acres": 40.0 + oid,
         "displayStatus": "Inactive"}
    a.update(extra)
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d],
            [lon, lat]]
    return {"attributes": a, "geometry": {"rings": [ring]}}


class Layers:
    """A stub ArcGIS + CAL FIRE server: records every URL; pages FIRIS when
    page_size is set (exceededTransferLimit)."""

    def __init__(self, page_size=None, delay=None):
        self.urls = []
        self.page_size = page_size
        self.delay = delay or {}
        self.points = [_point(1, "aaaa-1", "ALPHA", -120.5, 38.5)]
        self.perims = []
        self.firis = [_poly(i, f"FIRIS {i}", -119.0 + i * 0.1, 36.0)
                      for i in range(1, 6)]
        self.calfire = [{"Name": "Gamma Fire", "UniqueId": "u-1",
                         "Latitude": 37.0, "Longitude": -121.0,
                         "IsActive": True, "Updated": "2026-11-18T10:00:00Z",
                         "Started": "2026-11-18T09:00:00Z",
                         "AcresBurned": 30, "PercentContained": 0}]

    def __call__(self, url, headers=None):
        self.urls.append(url)
        base = url.split("?", 1)[0]
        if base in self.delay:
            time.sleep(self.delay[base])
        if url.startswith(feeds.CalFireFeed.LIST_URL):
            return 200, json.dumps(self.calfire).encode()
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        feats = {feeds.WfigsFeed.POINTS_URL: self.points,
                 feeds.WfigsFeed.PERIMETERS_URL: self.perims,
                 feeds.FirisFeed.LAYER_URL: self.firis}[base]
        off = int(q.get("resultOffset", 0))
        if self.page_size and base == feeds.FirisFeed.LAYER_URL:
            page = feats[off:off + self.page_size]
            more = off + self.page_size < len(feats)
            return 200, json.dumps({"features": page,
                                    "exceededTransferLimit": more}).encode()
        return 200, json.dumps({"features": feats}).encode()


def _key(inc):
    return (inc.incident_id, inc.name, inc.origin, inc.updated_at_s,
            inc.acreage, inc.perimeter, inc.meta, inc.source_ids)


# ---------------------------------------------------------------- BUST ---

def test_cache_bust_off_is_the_pinned_legacy_url():
    srv = Layers()
    for feed in (feeds.WfigsFeed(http_get=srv), feeds.FirisFeed(http_get=srv),
                 feeds.WfigsFeed(http_get=srv, cache_bust=False),
                 feeds.FirisFeed(http_get=srv, cache_bust=False)):
        feed.fetch_active(AOI)
    assert srv.urls == [LEGACY_POINTS, LEGACY_PERIMS, LEGACY_FIRIS] * 2


def test_cache_bust_on_adds_one_t_param_per_query_and_page():
    srv = Layers(page_size=2)
    plain = feeds.FirisFeed(http_get=Layers(page_size=2))
    bust = feeds.FirisFeed(http_get=srv, cache_bust=True,
                           clock=lambda: CLOCK_S)
    a, b = plain.fetch_active(AOI), bust.fetch_active(AOI)
    assert [_key(i) for i in a] == [_key(i) for i in b] and len(b) == 5
    assert len(srv.urls) == 3                     # three pages
    for k, url in enumerate(srv.urls):
        q = urllib.parse.parse_qsl(url.split("?", 1)[1])
        assert ("_t", "1795000000") in q
        legacy = [kv for kv in q if kv[0] != "_t"]
        want = LEGACY_FIRIS + (f"&resultOffset={2 * k}" if k else "")
        assert url.split("?", 1)[0] + "?" + urllib.parse.urlencode(legacy) \
            == want


def test_cache_bust_never_touches_cal_fire_or_firms():
    srv = Layers()
    feeds.CalFireFeed(http_get=srv).fetch_active(AOI)
    assert srv.urls == [feeds.CalFireFeed.LIST_URL]
    st = cli._live_feed_stack(None, transport={"cache_bust": True})[0]
    assert [type(f).__name__ for f in st] == ["WfigsFeed", "FirisFeed",
                                              "CalFireFeed"]
    assert st[0].transport["cache_bust"] and st[1].transport["cache_bust"]
    assert st[2].transport == {"gzip": False}


# ---------------------------------------------------------------- GZIP ---

class _Resp:
    def __init__(self, body, enc=None, status=200):
        self._b, self._s = body, status
        self.headers = {"Content-Encoding": enc} if enc else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self._s

    def read(self):
        return self._b


def _opener(resp):
    seen = []

    def open_(req, timeout=None):
        seen.append(dict(req.header_items()))
        return resp
    open_.seen = seen
    return open_


def test_net_gzip_asks_and_decodes():
    raw = b'{"features": []}' * 100
    op = _opener(_Resp(gzip.compress(raw), "gzip"))
    assert net.get("https://h.example/q", {"Accept": "application/json"},
                   opener=op, gzip=True) == (200, raw)
    assert op.seen[0].get("Accept-encoding") == "gzip"
    t = net.last_transfer()
    assert t["wire_bytes"] < t["body_bytes"] == len(raw)
    assert t["encoding"] == "gzip" and t["attempts"] == 1
    # an identity answer passes untouched
    op = _opener(_Resp(raw))
    assert net.get("https://h.example/q", opener=op, gzip=True) == (200, raw)


def test_net_gzip_off_is_legacy():
    body = gzip.compress(b"x")
    op = _opener(_Resp(body, "gzip"))
    assert net.get("https://h.example/q", {"Accept": "text/csv"},
                   opener=op) == (200, body)            # nothing decoded
    assert "Accept-encoding" not in op.seen[0]


@pytest.mark.parametrize("resp", [
    _Resp(b"\x1f\x8b\x08\x00garbage", "gzip"),         # corrupt gzip
    _Resp(gzip.compress(b"{}")[:-6], "gzip"),          # truncated
    _Resp(b"{}", "br"),                                # not what we asked
])
def test_net_gzip_corrupt_body_raises_once(resp):
    calls = []

    def open_(req, timeout=None):
        calls.append(1)
        return resp
    with pytest.raises(net.CorruptBodyError):
        net.get("https://h.example/q", opener=open_, gzip=True,
                sleep=lambda s: calls.append("slept"))
    assert calls == [1]                                # never retried


def _patch_urlopen(monkeypatch, bodies):
    """urllib.request.urlopen answering each layer URL from `bodies` (url
    base -> (bytes, encoding)); records Accept-Encoding."""
    asked = []

    def urlopen(req, timeout=None):
        base = req.full_url.split("?", 1)[0]
        asked.append(req.get_header("Accept-encoding"))
        body, enc = bodies[base]
        return _Resp(body, enc)
    monkeypatch.setattr(net.urllib.request, "urlopen", urlopen)
    return asked


def test_adapter_records_identical_with_gzip(monkeypatch):
    srv = Layers()
    payload = srv(LEGACY_FIRIS)[1]
    asked = _patch_urlopen(monkeypatch, {
        feeds.FirisFeed.LAYER_URL: (gzip.compress(payload), "gzip")})
    zipped = feeds.FirisFeed(gzip=True).fetch_active(AOI)
    asked2 = _patch_urlopen(monkeypatch, {
        feeds.FirisFeed.LAYER_URL: (payload, None)})
    plain = feeds.FirisFeed().fetch_active(AOI)
    assert [_key(i) for i in zipped] == [_key(i) for i in plain]
    assert asked == ["gzip"] and asked2 == [None]


def test_a_corrupt_gzip_body_is_a_blind_fetch(monkeypatch):
    _patch_urlopen(monkeypatch, {
        feeds.FirisFeed.LAYER_URL: (b"\x1f\x8b\x08\x00garbage", "gzip")})
    feed = feeds.FirisFeed(gzip=True)
    assert feed.fetch_active(AOI) == [] and feed.last_fetch_ok is False
    # ... and the monitor reads it as blind (SAFETY SEMANTICS 6)
    mon = _monitor([feed])
    res = mon.cycle(ss.T0)
    assert res.degraded
    assert [e.kind for e in res.operator_events] == ["feed_blind"]


def test_watch_stack_gzip_wiring():
    plain = cli._live_feed_stack("k" * 32, live=True)[0]
    zipped = cli._live_feed_stack("k" * 32, live=True,
                                  transport={"gzip": True})[0]
    assert [f._http_get for f in plain] == [feeds._default_http_get] * 4
    assert [f._http_get for f in zipped] == [feeds._gzip_http_get] * 4
    assert zipped[3].transport == {"gzip": True, "parallel_sources": False}


# ------------------------------------------------------------- TIMINGS ---

def _monitor(feed_list, cfg=None, clients=None):
    cfg = cfg or ss.small_cfg()
    roster = clients if clients is not None else ss.clients()
    engine = AlertEngine(cfg, notifier=LogNotifier(),
                         clients_by_id={c.client_id: c for c in roster})
    return ThreatMonitor(feeds=feed_list, weather_provider=ss.StubWeather(),
                         terrain_provider=ss.StubTerrain(),
                         registry=ss.StubRegistry(roster),
                         alert_engine=engine, cfg=cfg,
                         safe_zones=[ss.SAFE_ZONE],
                         crew_tracker=ss.StubTracker([ss.crew()]),
                         dispatcher=ss.dispatch_mod, aoi=ss.AOI)


def test_feeds_detail_per_adapter_and_request():
    srv = Layers(page_size=2)
    firms_get = _firms_get({})
    mon = _monitor([feeds.WfigsFeed(http_get=srv),
                    feeds.FirisFeed(http_get=srv),
                    feeds.CalFireFeed(http_get=srv),
                    feeds.FirmsFeed(map_key="SECRETKEY" * 4,
                                    http_get=firms_get)])
    res = mon.cycle(ss.T0)
    d = res.timings["feeds_detail"]
    assert d["mode"] == "serial" and d["fetch_s"] >= 0 and d["fuse_s"] >= 0
    rows = d["feeds"]
    assert [r["feed"] for r in rows] == ["WfigsFeed", "FirisFeed",
                                         "CalFireFeed", "FirmsFeed"]
    assert rows[1]["pages"] == 3 and [q["page"] for q in
                                      rows[1]["requests"]] == [0, 1, 2]
    for r in rows:
        assert r["ok"] is True and r["s"] >= 0 and r["n"] is not None
        for q in r["requests"]:
            assert q["status"] == 200 and q["bytes"] > 0 and q["s"] >= 0
    assert rows[0]["bytes"] == sum(q["bytes"] for q in rows[0]["requests"])
    assert "SECRETKEY" not in json.dumps(d)            # never the URL
    assert rows[3]["requests"][0]["what"] == "VIIRS_NOAA20_NRT"


def test_feeds_detail_marks_a_failed_request():
    def boom(url, headers=None):
        raise urllib.error.URLError("down")
    mon = _monitor([feeds.FirisFeed(http_get=boom)])
    rows = mon.cycle(ss.T0).timings["feeds_detail"]["feeds"]
    assert rows[0]["ok"] is False and rows[0]["n"] == 0
    assert rows[0]["requests"][0]["error"] == "URLError"
    assert rows[0]["requests"][0]["status"] is None


# ------------------------------------------------------------ PARALLEL ---

class SleepyFeed:
    """A feed that sleeps a seeded random time, then answers, raises or
    reports unhealthy (and may carry an incremental finding)."""

    def __init__(self, name, incidents, sleep_s, mode="ok", events=()):
        self.name, self.incidents, self.sleep_s = name, incidents, sleep_s
        self.mode = mode
        self.last_fetch_ok = True
        self.events = list(events)
        self.threads = []

    def fetch_active(self, bbox=None):
        self.threads.append(threading.current_thread().name)
        time.sleep(self.sleep_s)
        self.last_fetch_events = list(self.events)
        if self.mode == "raise":
            raise RuntimeError(f"{self.name} exploded")
        self.last_fetch_ok = self.mode != "unhealthy"
        return list(self.incidents)


def _sleepy_feeds(seed, scale=0.25):
    rng = random.Random(seed)
    o = ss.ORIGIN
    inc = [ss.feed_incident(iid="inc-1"),
           ss.feed_incident(iid="inc-2", name="Second",
                            origin=LatLon(o.lat + 0.01, o.lon + 0.01)),
           ss.feed_incident(iid="inc-1", name="Stub Fire",
                            updated_at_s=ss.T0 - 300.0, acreage=600.0)]
    ev = {"kind": "feed_incremental_mismatch", "message": "cache differed",
          "detail": {"layer": "X", "order": True}}
    return [SleepyFeed("a", [inc[0]], rng.random() * scale),
            SleepyFeed("b", [], rng.random() * scale, mode="raise"),
            SleepyFeed("c", [inc[1]], rng.random() * scale,
                       mode="unhealthy"),
            SleepyFeed("d", [inc[2]], rng.random() * scale, events=[ev]),
            SleepyFeed("e", [], rng.random() * scale)]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_parallel_cycles_are_byte_identical(seed):
    digests = {}
    for on in (False, True):
        cfg = ss.small_cfg(feeds_parallel=on)
        mon = _monitor(_sleepy_feeds(seed), cfg=cfg)
        out = []
        for k in range(3):
            res = mon.cycle(ss.T0 + 60.0 + k * ss.PERIOD_S)
            out.append((ss.cycle_digest(res), list(res.notes),
                        [(e.kind, e.message, e.detail)
                         for e in res.operator_events]))
        digests[on] = out
        assert mon.feeds_detail["mode"] == ("parallel" if on else "serial")
    assert digests[False] == digests[True]
    notes = digests[True][0][1]
    assert any("feed[1] SleepyFeed: fetch failed (b exploded)" == n
               for n in notes)
    assert any(n.startswith("feed[2] SleepyFeed: fetch unhealthy")
               for n in notes)
    assert "feed[3] SleepyFeed: cache differed" in notes
    kinds = [k for k, _, _ in digests[True][0][2]]
    assert kinds[:3] == ["feed_blind", "feed_blind",
                         "feed_incremental_mismatch"]


def test_parallel_fetch_overlaps():
    fs = [SleepyFeed(str(i), [], 0.4) for i in range(5)]
    mon = _monitor(fs, cfg=ss.small_cfg(feeds_parallel=True))
    t0 = time.monotonic()
    mon.cycle(ss.T0)
    wall = mon.feeds_detail["fetch_s"]
    assert wall < 1.2 < 5 * 0.4 and time.monotonic() - t0 >= 0.4
    assert len({f.threads[0] for f in fs}) == 5
    assert all(t.startswith("feed") for f in fs for t in f.threads)


def test_parallel_one_feed_stays_serial():
    f = SleepyFeed("solo", [], 0.0)
    mon = _monitor([f], cfg=ss.small_cfg(feeds_parallel=True))
    mon.cycle(ss.T0)
    assert mon.feeds_detail["mode"] == "serial"
    assert f.threads == [threading.current_thread().name]


def test_parallel_base_exception_propagates():
    class Stop(SleepyFeed):
        def fetch_active(self, bbox=None):
            raise KeyboardInterrupt()
    mon = _monitor([SleepyFeed("a", [], 0.0), Stop("b", [], 0.0)],
                   cfg=ss.small_cfg(feeds_parallel=True))
    with pytest.raises(KeyboardInterrupt):
        mon.cycle(ss.T0)


# -------------------------------------------- PARALLEL FIRMS SOURCES ---

from tests.test_screen_shadow import (N20, N21, SITE, T, _csv,  # noqa: E402
                                      _night, _pt, _row)


def _firms_get(delays, fail=()):
    hist = [r for _, r in (_night(SITE, d) for d in (3, 2, 1))]
    now_rows = {N20: [_row(SITE, T - 1800.0), _row(_pt(SITE, 900.0),
                                                   T - 1700.0)],
                N21: [_row(_pt(SITE, 300.0), T - 4800.0)]}
    five = {N20: hist + now_rows[N20], N21: now_rows[N21]}
    lock = threading.Lock()
    state = {"inflight": 0, "max": 0}

    def get(url, headers=None):
        src = next(s for s in (N20, N21) if f"/{s}/" in url)
        with lock:
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
        try:
            time.sleep(delays.get(src, 0.0))
            if src in fail:
                return 503, b""
            rows = five if url.endswith("/5") else now_rows
            return 200, _csv(rows.get(src, [])).encode("utf-8")
        finally:
            with lock:
                state["inflight"] -= 1
    get.state = state
    return get


@pytest.mark.parametrize("fail", [(), (N20,), (N21,)])
def test_parallel_firms_sources_same_clusters_log_and_memory(tmp_path, fail):
    out = {}
    for on in (False, True):
        d = tmp_path / ("on" if on else "off")
        log = screening.FirmsPollLog(d)
        mem = screening.HeatSiteMemory(feeds.FirmsFeed.LIVE_SOURCES)
        # N20 answers LAST: the parallel path must still apply N20 first
        get = _firms_get({N20: 0.3, N21: 0.0}, fail=fail)
        feed = feeds.FirmsFeed.live("k" * 32, http_get=get, clock=lambda: T,
                                    recorder=log, memory=mem,
                                    parallel_sources=on)
        runs = []
        for _ in range(2):
            incs = feed.fetch_active()
            runs.append(([(i.incident_id, i.hotspots, i.meta) for i in incs],
                         feed.last_fetch_ok))
        files = {p.name: p.read_text(encoding="utf-8")
                 for p in sorted(d.glob("firms_*.jsonl"))}
        out[on] = (runs, files, mem.status(), log.status()["keys"],
                   get.state["max"])
    assert out[False][:4] == out[True][:4]
    assert out[False][4] == 1 and out[True][4] == 2      # asked at once
    assert out[True][1]                                   # a log was written


# ------------------------------------------------------------------ CLI ---

def test_flags_arm_the_knobs_with_notes():
    p = cli._build_parser()
    args = p.parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-125,24,-66,50", "--feeds-cache-bust",
         "--feeds-gzip", "--feeds-parallel"]))
    cfg, notes = cli._feeds_cfg(ResponseConfig(), args)
    assert cfg.feeds_cache_bust and cfg.feeds_gzip and cfg.feeds_parallel
    assert not cfg.feeds_incremental and cfg.new_fire_sentinel_s == 0.0
    assert [n.split(":")[0] for n in notes] == [
        "FEEDS CACHE-BUSTED", "FEEDS GZIP", "FEEDS IN PARALLEL"]
    assert cli._feed_transport(cfg) == {"cache_bust": True, "gzip": True,
                                        "parallel_sources": True}
    assert validate_config(cfg) == []


def test_no_flag_is_the_legacy_cfg_and_stack():
    p = cli._build_parser()
    args = p.parse_args(cli._normalize_argv(["watch", "--aoi",
                                             "-125,24,-66,50"]))
    cfg, notes = cli._feeds_cfg(ResponseConfig(), args)
    assert cfg == ResponseConfig() and notes == []
    assert cli._feed_transport(cfg) == {}
    st = cli._live_feed_stack("k" * 32, live=True, transport={})[0]
    assert [f.transport for f in st] == [
        {"cache_bust": False, "gzip": False, "incremental": False},
        {"cache_bust": False, "gzip": False, "incremental": False},
        {"gzip": False}, {"gzip": False, "parallel_sources": False}]


def test_config_defaults_and_bounds():
    c = ResponseConfig()
    assert (c.feeds_cache_bust, c.feeds_gzip, c.feeds_parallel,
            c.feeds_incremental, c.new_fire_sentinel_s) == (
        False, False, False, False, 0.0)
    for bad in (dict(new_fire_sentinel_s=5.0),
                dict(new_fire_sentinel_s=float("nan")),
                dict(new_fire_sentinel_km=0.0),
                dict(new_fire_sentinel_km=61.0),
                dict(feeds_gzip=1)):
        assert validate_config(dataclasses.replace(c, **bad)), bad
    assert validate_config(dataclasses.replace(
        c, new_fire_sentinel_s=60.0, new_fire_sentinel_km=8.0)) == []
