"""tests/test_feeds_incremental.py — A LAYER IS ASKED ONLY FOR WHAT CHANGED,
AND CHECKED HOURLY AGAINST THE WHOLE (2026-11-18, the fire -> alert latency
review, lane A fix 6; BUILD lane 3; contracts SAFETY SEMANTICS 59;
cfg.feeds_incremental; watch --feeds-incremental).

  PROPERTY  over seeded random histories of a simulated ArcGIS layer —
            adds, attribute edits, geometry edits that move the layer's
            change fields, deletions, reorders, outages, a failing cheap
            query, growth past the page cap, five-minute cycles over hours —
            the incremental answer (the raw feature list AND the adapter's
            records AND last_fetch_ok) equals the legacy full answer at
            EVERY step, for FIRIS ('*') and the WFIGS perimeter layer.
  SAVES     in steady state only the attributes and the changed features
            are downloaded.
  HOURLY    a geometry edit that moves no attribute is served from the
            cache until the hourly full fetch, which notices it: ONE
            feed_incremental_mismatch event, the full answer used; the
            monitor turns it into a note + an operator event, not degraded.
  FALLBACK  a failing cheap query is the legacy fetch (never blind because
            of the incremental path); a timeout raises at once; a failure
            drops the cache (the next fetch is full).
  OFF       no cheap query is ever asked (tests/test_feeds_faster.py pins
            the legacy URLs)."""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import urllib.error
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from tests.test_feeds_faster import _key, _monitor  # noqa: E402
from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

AOI = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
T0_MS = 1_794_000_000_000

FIRIS_SCHEMA = ["OBJECTID", "GlobalID", "type", "source", "poly_DateCurrent",
                "mission", "incident_name", "incident_number", "area_acres",
                "description", "FireDiscoveryDate", "CreationDate",
                "EditDate", "displayStatus"]
WFIGS_SCHEMA = (["OBJECTID"]
                + feeds.WfigsFeed.PERIM_OUT_FIELDS.split(",")
                + ["attr_IncidentTypeCategory", "poly_PolygonDateTime",
                   "poly_CreateDate", "Shape__Area", "Shape__Length"])


class SimLayer:
    """A simulated ArcGIS FeatureServer layer: outFields (unknown field =
    an ArcGIS error), returnGeometry, objectIds, resultOffset paging (a
    smaller page with geometry), exceededTransferLimit; an outage (HTTP
    500), a failing cheap query, a timing-out cheap query."""

    def __init__(self, url, schema, page=4, page_nogeom=10):
        self.url, self.schema = url, list(schema)
        self.page, self.page_nogeom = page, page_nogeom
        self.feats: list = []
        self.down = False
        self.cheap_error = False
        self.cheap_timeout = False
        self.asked = []          # (geometry?, objectIds?, bytes)

    def __call__(self, url, headers=None):
        base, qs = url.split("?", 1)
        assert base == self.url
        q = dict(urllib.parse.parse_qsl(qs))
        geom = q["returnGeometry"] == "true"
        if self.down:
            return 500, b""
        if not geom and self.cheap_timeout:
            raise urllib.error.URLError(socket.timeout("timed out"))
        if not geom and self.cheap_error:
            return 200, b'{"error": {"code": 400, "message": "Invalid field"}}'
        names = self.schema if q["outFields"] == "*" else \
            q["outFields"].split(",")
        if any(n not in self.schema for n in names):
            return 200, b'{"error": {"code": 400, "message": "Invalid field"}}'
        sel = self.feats
        if "objectIds" in q:
            ids = {int(x) for x in q["objectIds"].split(",")}
            sel = [f for f in sel if f["attributes"]["OBJECTID"] in ids]
        out = []
        for f in sel:
            g = {"attributes": {n: f["attributes"][n] for n in names}}
            if geom:
                g["geometry"] = f["geometry"]
            out.append(g)
        size = self.page if geom else self.page_nogeom
        off = int(q.get("resultOffset", 0))
        payload = {"features": out[off:off + size]}
        if off + size < len(out):
            payload["exceededTransferLimit"] = True
        body = json.dumps(payload).encode()
        self.asked.append((geom, "objectIds" in q, len(body)))
        return 200, body


class History:
    """Random edits to a SimLayer, every one moving the layer's change
    fields as the real layers do (editor tracking / Shape__ fields)."""

    def __init__(self, layer, kind, seed):
        self.layer, self.kind = layer, kind
        self.rng = random.Random(seed)
        self.next_oid = 1
        self.t_ms = T0_MS

    def _ring(self):
        r = self.rng
        lon, lat = -121 + r.random() * 4, 35 + r.random() * 4
        d = 0.002 + r.random() * 0.05
        n = r.randint(4, 260)            # > 200 vertices exercises the thin
        ring = [[lon + d * (k % 7) / 7.0, lat + d * (k % 5) / 5.0]
                for k in range(n)]
        return {"rings": [ring + [ring[0]]]}

    def add(self):
        oid = self.next_oid
        self.next_oid += 1
        geom = self._ring()
        if self.kind == "firis":
            a = {"OBJECTID": oid, "GlobalID": f"g-{oid}", "type": "Heat Perimeter",
                 "source": "USFS", "poly_DateCurrent": self.t_ms,
                 "mission": f"CA-XXX-F{oid}-N1", "incident_name": None,
                 "incident_number": None, "area_acres": 10.0 + oid,
                 "description": "d", "FireDiscoveryDate": None,
                 "CreationDate": self.t_ms, "EditDate": None,
                 "displayStatus": "Active"}
        else:
            a = {n: None for n in WFIGS_SCHEMA}
            a.update(OBJECTID=oid, poly_IncidentName=f"F{oid}",
                     poly_IRWINID="{%08d-0000-0000-0000-000000000000}" % oid,
                     poly_GISAcres=5.0 + oid, attr_PercentContained=0,
                     attr_ModifiedOnDateTime_dt=self.t_ms,
                     poly_DateCurrent=self.t_ms, poly_CreateDate=self.t_ms,
                     attr_IncidentTypeCategory="WF",
                     Shape__Area=1000.0 + oid, Shape__Length=100.0 + oid)
        pos = self.rng.randint(0, len(self.layer.feats))
        self.layer.feats.insert(pos, {"attributes": a, "geometry": geom})

    def _pick(self):
        return self.rng.choice(self.layer.feats) if self.layer.feats else None

    def edit_attr(self):
        f = self._pick()
        if f is None:
            return
        a = f["attributes"]
        if self.kind == "firis":
            a["incident_name"] = f"Named {self.rng.randint(0, 99)}"
            a["displayStatus"] = self.rng.choice(["Active", "Inactive"])
            a["EditDate"] = self.t_ms
        else:
            a["attr_PercentContained"] = self.rng.choice([0, 20, 100])
            a["attr_ModifiedOnDateTime_dt"] = self.t_ms

    def edit_geom(self):
        f = self._pick()
        if f is None:
            return
        f["geometry"] = self._ring()
        a = f["attributes"]
        if self.kind == "firis":
            a["EditDate"] = self.t_ms
            a["area_acres"] = round(self.rng.random() * 500, 3)
        else:
            a["Shape__Area"] = round(self.rng.random() * 1e6, 3)
            a["Shape__Length"] = round(self.rng.random() * 1e4, 3)

    def touch(self):
        """A field only the cheap query reads moves (FIRIS description,
        WFIGS poly_PolygonDateTime)."""
        f = self._pick()
        if f is None:
            return
        if self.kind == "firis":
            f["attributes"]["description"] = f"d{self.rng.random()}"
        else:
            f["attributes"]["poly_PolygonDateTime"] = self.t_ms

    def delete(self):
        if self.layer.feats:
            self.layer.feats.pop(self.rng.randrange(len(self.layer.feats)))

    def reorder(self):
        self.rng.shuffle(self.layer.feats)

    def step(self):
        self.t_ms += 300_000
        r = self.rng.random()
        ops = [self.add, self.edit_attr, self.edit_geom, self.touch,
               self.delete, self.reorder]
        weights = [5, 4, 3, 2, 2, 1]
        for _ in range(self.rng.choice([0, 0, 1, 1, 2, 3])):
            self.rng.choices(ops, weights)[0]()
        return r


class Recording:
    """Mixin: keep _query's raw answer (None when it raised)."""

    def _query(self, *a, **k):
        self.raw = None
        self.raw = super()._query(*a, **k)
        return self.raw


class RecFiris(Recording, feeds.FirisFeed):
    pass


class RecWfigs(Recording, feeds.WfigsFeed):
    pass


def _points_stub():
    return lambda url, headers=None: (200, b'{"features": []}')


def _pair(kind, seed, page=4):
    url = (feeds.FirisFeed.LAYER_URL if kind == "firis"
           else feeds.WfigsFeed.PERIMETERS_URL)
    layer = SimLayer(url, FIRIS_SCHEMA if kind == "firis" else WFIGS_SCHEMA,
                     page=page)
    hist = History(layer, kind, seed)
    clock = {"t": 1_794_000_000.0}

    def route(url, headers=None):
        if url.startswith(feeds.WfigsFeed.POINTS_URL):
            return 200, b'{"features": []}'
        return layer(url, headers)
    if kind == "firis":
        legacy = RecFiris(http_get=route)
        inc = RecFiris(http_get=route, incremental=True,
                       clock=lambda: clock["t"])
    else:
        legacy = RecWfigs(http_get=route, keep_rx=True)
        inc = RecWfigs(http_get=route, keep_rx=True, incremental=True,
                       clock=lambda: clock["t"])
    return layer, hist, clock, legacy, inc


def _modes(feed):
    det = feed.last_fetch_detail or {}
    return [n["mode"] for n in det.get("incremental", [])]


@pytest.mark.parametrize("kind", ["firis", "wfigs"])
@pytest.mark.parametrize("seed", range(6))
def test_incremental_equals_full_at_every_step(kind, seed):
    layer, hist, clock, legacy, inc = _pair(kind, seed)
    for _ in range(3):
        hist.add()
    modes = []
    for step in range(60):                   # five hours of 5-min cycles
        r = hist.step()
        layer.down = r < 0.04                # an outage: both blind
        layer.cheap_error = 0.04 <= r < 0.08 # a failing cheap query
        if step in (30, 31, 32) and seed % 2 == 0:
            for _ in range(8):               # growth past the 20-feature cap
                hist.add()
        if step == 40 and seed % 2 == 0:
            del layer.feats[12:]             # ... and back under it
        clock["t"] += 300.0
        a = legacy.fetch_active(AOI)
        b = inc.fetch_active(AOI)
        assert inc.raw == legacy.raw, (kind, seed, step)
        assert [_key(i) for i in b] == [_key(i) for i in a]
        assert inc.last_fetch_ok == legacy.last_fetch_ok
        assert inc.last_fetch_events == []   # every edit moved a field
        modes += _modes(inc)
    assert "incremental" in modes and "full" in modes


def test_steady_state_downloads_only_attributes_and_changes():
    layer, hist, clock, legacy, inc = _pair("firis", 7, page=50)
    for _ in range(30):
        hist.add()
    inc.fetch_active(AOI)                     # the first fetch is full
    full_geom = sum(b for g, ids, b in layer.asked if g and not ids)
    layer.asked.clear()
    clock["t"] += 300.0
    inc.fetch_active(AOI)                     # nothing changed
    assert {(g, ids) for g, ids, _ in layer.asked} == {(False, False)}
    assert len(layer.asked) == 3              # 30 attribute rows, 10 a page
    assert _modes(inc) == ["incremental"]
    layer.asked.clear()
    hist.edit_attr()
    clock["t"] += 300.0
    inc.fetch_active(AOI)                     # one changed: one feature
    geom = [(ids, b) for g, ids, b in layer.asked if g]
    assert len(geom) == 1 and geom[0][0] is True
    assert geom[0][1] < full_geom / 10
    det = inc.last_fetch_detail
    assert det["bytes"] == sum(b for _, _, b in layer.asked)


def test_a_silent_geometry_edit_waits_for_the_hourly_full_fetch():
    layer, hist, clock, legacy, inc = _pair("firis", 3, page=50)
    for _ in range(5):
        hist.add()
    inc.fetch_active(AOI)
    victim = layer.feats[2]
    victim["geometry"] = hist._ring()          # no attribute moves
    stale_steps = 0
    for _ in range(12):
        clock["t"] += 300.0
        a, b = legacy.fetch_active(AOI), inc.fetch_active(AOI)
        if inc.last_fetch_events:
            break
        assert [_key(i) for i in b] != [_key(i) for i in a]
        stale_steps += 1
    ev = inc.last_fetch_events
    assert len(ev) == 1 and ev[0]["kind"] == "feed_incremental_mismatch"
    assert ev[0]["detail"]["stale_features"] == [
        victim["attributes"]["OBJECTID"]]
    assert inc.raw == legacy.raw                # the full answer is used
    assert stale_steps * 300.0 < feeds.FirisFeed.INCREMENTAL_FULL_EVERY_S
    # the monitor: a note + an operator event, never a degradation
    mon = _monitor([inc])
    inc.fetch_active(ss.AOI)                     # the monitor's AOI, cached
    victim["geometry"] = hist._ring()
    clock["t"] += 3600.0
    res = mon.cycle(ss.T0)
    kinds = [e.kind for e in res.operator_events]
    assert kinds == ["feed_incremental_mismatch"] and not res.degraded
    assert any("incremental cache differed" in n for n in res.notes)


def test_a_failing_cheap_query_is_the_legacy_fetch_never_blind():
    layer, hist, clock, legacy, inc = _pair("wfigs", 1)
    for _ in range(6):
        hist.add()
    layer.cheap_error = True
    b = inc.fetch_active(AOI)
    a = legacy.fetch_active(AOI)
    assert inc.last_fetch_ok and [_key(i) for i in b] == [_key(i) for i in a]
    assert _modes(inc) == ["full"]
    assert "incremental query failed" in \
        inc.last_fetch_detail["incremental"][0]["note"]


def test_a_cheap_query_timeout_raises_and_drops_the_cache():
    layer, hist, clock, legacy, inc = _pair("firis", 2)
    for _ in range(4):
        hist.add()
    inc.fetch_active(AOI)
    assert inc._inc_state
    layer.cheap_timeout = True
    clock["t"] += 300.0
    assert inc.fetch_active(AOI) == [] and inc.last_fetch_ok is False
    assert not inc._inc_state                  # the next fetch is full
    layer.cheap_timeout = False
    clock["t"] += 300.0
    inc.fetch_active(AOI)
    assert _modes(inc) == ["full"] and inc.last_fetch_ok


def test_a_failure_elsewhere_in_the_adapter_drops_the_cache():
    layer, hist, clock, legacy, inc = _pair("wfigs", 4)
    for _ in range(4):
        hist.add()
    inc.fetch_active(AOI)
    assert inc._inc_state
    real = inc._http_get

    def points_down(url, headers=None):
        if url.startswith(feeds.WfigsFeed.POINTS_URL):
            return 503, b""
        return real(url, headers)
    inc._http_get = points_down
    clock["t"] += 300.0
    inc.fetch_active(AOI)
    assert inc.last_fetch_ok is False and not inc._inc_state


def test_the_cache_is_compact():
    layer, hist, clock, legacy, inc = _pair("firis", 5, page=50)
    for _ in range(20):
        hist.add()
    inc.fetch_active(AOI)
    (st,) = inc._inc_state.values()
    assert all(isinstance(v, bytes) for v in st["feats"].values())
    raw = len(json.dumps([f["geometry"] for f in layer.feats]))
    assert sum(len(v) for v in st["feats"].values()) < raw
