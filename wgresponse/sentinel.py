"""wgresponse.sentinel — THE NEW-FIRE SENTINEL, in shadow (contracts SAFETY
SEMANTICS 60; cfg.new_fire_sentinel_s, new_fire_sentinel_km; watch
--new-fire-sentinel S). 2026-11-18, the fire -> alert latency review, lane
A fix 7: a new agency record waited for the next cycle's fetch — 11.4 min
on average and 40 at worst on 2026-11-18, because cycles run back to back
and the feeds alone took 272-498 s.

A daemon thread of the watch process asks, every period, the CHEAP agency
queries for records it has not seen:
  WFIGS   the incident-locations layer's IrwinIDs (attributes only, the
          AOI envelope); an id not seen before -> its record (name, type,
          created / modified / discovery times, size, point)
  FIRIS   the perimeter layer's OBJECTIDs (returnIdsOnly); a new id -> its
          row with a GENERALIZED polygon (~100 m; the sentinel needs only
          where it is)
  CALFIRE the IncidentApi list; a UniqueId not seen before
Every query is cache-busted (`_t`) and gzip-asked: a CDN answer would
defeat the point. The FIRST successful poll of a source is its BASELINE
(nothing in it is new). A record that appears later gets first_seen_s (the
poll that saw it) and, for a non-RX record within new_fire_sentinel_km of a
protected home, ONE OperatorEvent('new_fire_near_homes') at once — handed
to the daemon (its operator log) and kept in the sentinel's own event list
(the daemon's log is a bounded tail that one blind cycle's 'recovered'
events can flush). Never a client message.

After each live cycle the daemon calls observe_cycle(): a record the
cycle's incidents carry (its IRWIN id, its incident_id or a source id)
gets cycle_seen_s (the cycle's clock, when it began fetching) and
cycle_done_s (when its result was ready): cycle_done_s - first_seen_s is
what a priority pass would have saved (best effort: a FIRIS row fused
under another source's id may never match).

SHADOW: the cycle reads NOTHING from here. The sentinel's own failures are
its own — counted in status(), never a degraded cycle, never a feed_blind
— and it writes nothing to the state file (a restart re-baselines). It
holds its own lock; the network is never asked under it. Stdlib only."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Callable, Optional

from . import feeds, net
from .contracts import BBox, LatLon, OperatorEvent

log = logging.getLogger(__name__)

EVENT_KIND = "new_fire_near_homes"
#: kept for /api/state: the newest records / events
KEEP_RECORDS = 300
KEEP_EVENTS = 100
#: an id not seen for this long is forgotten (s)
FORGET_S = 7 * 86400.0
_IRWIN_SAFE = re.compile(r"^[{}0-9A-Za-z-]{1,64}$")
_EARTH_R_M = 6_371_000.0


def _haversine_m(a: LatLon, b: LatLon) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (math.sin((la2 - la1) / 2.0) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2.0) ** 2)
    return 2.0 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def _default_get(url: str, headers: Optional[dict] = None,
                 timeout_s: float = 30.0) -> "tuple[int, bytes]":
    return net.get(url, headers, timeout_s, gzip=True)


def rule_summary(cfg) -> dict:
    """The sentinel's rule for /api/state (SEMANTICS 60)."""
    return {"period_s": float(cfg.new_fire_sentinel_s),
            "radius_km": float(cfg.new_fire_sentinel_km),
            "sources": ["wfigs_points", "firis", "calfire"],
            "event": EVENT_KIND,
            "why_radius": ("the 8 km operator tripwire (1.7 m/s x 75 min, "
                           "SAFETY SEMANTICS 41) + 4.1 km a fire runs at "
                           "1.7 m/s in the worst 40-min wait for the next "
                           "cycle's fetch (2026-11-18)"),
            "shadow": True}


def _iso(t_s: Optional[float]) -> str:
    if t_s is None:
        return "?"
    return datetime.fromtimestamp(t_s, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%SZ")


class NewFireSentinel:
    """The shadow new-fire poller (module docstring). clients_fn() -> the
    protected homes (objects with .client_id and .loc, or (id, LatLon)
    pairs); on_event(OperatorEvent) -> the daemon's operator log (its
    failure is logged, never raised); http_get(url, headers) -> (status,
    bytes) and clock() are injectable (tests)."""

    def __init__(self, cfg, clients_fn: Callable[[], list],
                 aoi: Optional[BBox] = None,
                 http_get: Optional[Callable] = None,
                 clock: Callable[[], float] = time.time,
                 on_event: Optional[Callable] = None):
        self._period_s = float(cfg.new_fire_sentinel_s)
        self._radius_m = float(cfg.new_fire_sentinel_km) * 1000.0
        self._rule = rule_summary(cfg)
        self._clients_fn = clients_fn
        self._aoi = aoi
        self._get = http_get or _default_get
        self._clock = clock
        self._on_event = on_event
        self._lock = threading.Lock()
        self._seen: dict = {"wfigs": None, "firis": None, "calfire": None}
        self._records: dict = {}          # key -> row
        self._events: list = []
        self._polls = 0
        self._started_s: Optional[float] = None
        self._last_poll_s: Optional[float] = None
        self._src: dict = {s: {"errors": 0, "last": None}
                           for s in self._seen}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the daemon thread: a poll at once, then every period."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="new-fire-sentinel")
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout_s)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            if self._stop.wait(self._period_s):
                break

    # -- one poll ------------------------------------------------------------

    def poll_once(self) -> list:
        """Ask every source once; returns the operator events raised. Never
        raises."""
        t = float(self._clock())
        with self._lock:
            if self._started_s is None:
                self._started_s = t
        events: list = []
        for src, fn in (("wfigs", self._poll_wfigs),
                        ("firis", self._poll_firis),
                        ("calfire", self._poll_calfire)):
            t0 = time.monotonic()
            rows: list = []
            try:
                found = fn(t, rows)
                err = None
            except Exception as exc:          # noqa: BLE001 - its own failure
                found, err = [], f"{type(exc).__name__}: {str(exc)[:160]}"
                log.warning("new-fire sentinel: %s poll failed (%s)", src,
                            err)
            last = {"t_s": t, "s": round(time.monotonic() - t0, 3),
                    "ok": err is None, "new": len(found),
                    "requests": rows}
            if err is not None:
                last["error"] = err
            with self._lock:
                self._src[src]["last"] = last
                if err is not None:
                    self._src[src]["errors"] += 1
                for rec in found:
                    ev = self._admit(rec, t)
                    if ev is not None:
                        events.append(ev)
        with self._lock:
            self._polls += 1
            self._last_poll_s = t
            self._forget(t)
        for ev in events:
            if self._on_event is not None:
                try:
                    self._on_event(ev)
                except Exception:              # noqa: BLE001
                    log.exception("new-fire sentinel: the operator log "
                                  "refused an event")
        return events

    def _ask(self, url: str, rows: list, what: str) -> dict:
        status, body = feeds._timed_get(self._get, url,
                                        {"Accept": "application/json"}, rows,
                                        what)
        if status != 200:
            raise RuntimeError(f"HTTP {status} ({what})")
        payload = json.loads(body.decode("utf-8"))
        if isinstance(payload, dict) and "error" in payload:
            raise RuntimeError(f"ArcGIS error ({what}): {payload['error']}")
        return payload

    def _params(self, t: float, **kw) -> dict:
        p = {"where": "1=1", "f": "json", "outSR": "4326"}
        if self._aoi is not None:
            p.update(feeds._envelope_params(self._aoi))
        p.update(kw)
        p["_t"] = str(int(t))
        return p

    def _arc(self, layer: str, t: float, rows: list, what: str,
             **kw) -> list:
        """Every page (up to 5) of one ArcGIS query's features."""
        feats: list = []
        off = 0
        for _ in range(5):
            p = self._params(t, **kw)
            if off:
                p["resultOffset"] = str(off)
            payload = self._ask(layer + "?" + urllib.parse.urlencode(p),
                                rows, what)
            page = payload.get("features") or []
            feats += page
            if not payload.get("exceededTransferLimit") or not page:
                return feats
            off += len(page)
        raise RuntimeError(f"{what}: more than 5 pages")

    def _poll_wfigs(self, t: float, rows: list) -> list:
        layer = feeds.WfigsFeed.POINTS_URL
        ids = [str((f.get("attributes") or {}).get("IrwinID") or "")
               for f in self._arc(layer, t, rows, "wfigs ids",
                                  outFields="IrwinID",
                                  returnGeometry="false")]
        ids = [i for i in ids if i]
        new = self._new_ids("wfigs", ids, t, key=feeds._norm_irwin)
        out = []
        safe = [i for i in new if _IRWIN_SAFE.match(i)]
        for k in range(0, len(safe), 50):
            chunk = safe[k:k + 50]
            where = "IrwinID IN (" + ",".join(f"'{i}'" for i in chunk) + ")"
            for f in self._arc(layer, t, rows, "wfigs new", where=where,
                               outFields=("IrwinID,IncidentName,"
                                          "IncidentTypeCategory,"
                                          "CreatedOnDateTime_dt,"
                                          "ModifiedOnDateTime_dt,"
                                          "FireDiscoveryDateTime,"
                                          "IncidentSize"),
                               returnGeometry="true"):
                a = f.get("attributes") or {}
                g = f.get("geometry") or {}
                irwin = feeds._norm_irwin(a.get("IrwinID"))
                if irwin is None or g.get("x") is None or g.get("y") is None:
                    continue
                out.append({"source": "wfigs", "id": irwin,
                            "key": f"wfigs:{irwin}",
                            "match": {irwin},
                            "name": a.get("IncidentName") or "",
                            "type": a.get("IncidentTypeCategory"),
                            "loc": LatLon(float(g["y"]), float(g["x"])),
                            "created_s": feeds._when_s(
                                a.get("CreatedOnDateTime_dt")),
                            "modified_s": feeds._when_s(
                                a.get("ModifiedOnDateTime_dt")),
                            "discovered_s": feeds._when_s(
                                a.get("FireDiscoveryDateTime")),
                            "acres": feeds._maybe_float(
                                a.get("IncidentSize"))})
        return out

    def _poll_firis(self, t: float, rows: list) -> list:
        layer = feeds.FirisFeed.LAYER_URL
        p = self._params(t, returnIdsOnly="true")
        payload = self._ask(layer + "?" + urllib.parse.urlencode(p), rows,
                            "firis ids")
        ids = [str(i) for i in (payload.get("objectIds") or [])]
        new = self._new_ids("firis", ids, t)
        out = []
        for k in range(0, len(new), 50):
            chunk = new[k:k + 50]
            for f in self._arc(layer, t, rows, "firis new",
                               objectIds=",".join(chunk),
                               outFields=("OBJECTID,mission,incident_name,"
                                          "CreationDate,poly_DateCurrent,"
                                          "FireDiscoveryDate,area_acres,"
                                          "type,displayStatus"),
                               returnGeometry="true",
                               maxAllowableOffset="0.001",
                               geometryPrecision="5"):
                a = f.get("attributes") or {}
                rings = (f.get("geometry") or {}).get("rings") or []
                ring = feeds._ring_from_raw(rings[0]) if rings else []
                if not ring:
                    continue
                oid = str(a.get("OBJECTID"))
                name = a.get("incident_name") or a.get("mission") or ""
                out.append({"source": "firis", "id": oid,
                            "key": f"firis:{oid}",
                            "match": {f"firis:{oid}", oid},
                            "name": str(name), "type": a.get("type"),
                            "loc": feeds._ring_centroid(ring),
                            "created_s": feeds._when_s(a.get("CreationDate")),
                            "modified_s": feeds._when_s(
                                a.get("poly_DateCurrent")),
                            "discovered_s": feeds._when_s(
                                a.get("FireDiscoveryDate")),
                            "acres": feeds._maybe_float(a.get("area_acres")),
                            "status": a.get("displayStatus")})
        return out

    def _poll_calfire(self, t: float, rows: list) -> list:
        status, body = feeds._timed_get(
            self._get, feeds.CalFireFeed.LIST_URL,
            {"Accept": "application/json"}, rows, "calfire list")
        if status != 200:
            raise RuntimeError(f"HTTP {status} (calfire list)")
        recs = json.loads(body.decode("utf-8"))
        by_id = {}
        for r in recs if isinstance(recs, list) else []:
            uid = str(r.get("UniqueId") or "").lower()
            lat = feeds._maybe_float(r.get("Latitude"))
            lon = feeds._maybe_float(r.get("Longitude"))
            if not uid or lat is None or lon is None:
                continue
            loc = LatLon(lat, lon)
            if self._aoi is not None and not self._aoi.contains(loc):
                continue
            by_id[uid] = (r, loc)
        new = self._new_ids("calfire", list(by_id), t)
        out = []
        for uid in new:
            r, loc = by_id[uid]
            out.append({"source": "calfire", "id": uid,
                        "key": f"calfire:{uid}", "match": {f"calfire:{uid}"},
                        "name": str(r.get("Name") or ""),
                        "type": r.get("Type"), "loc": loc,
                        "created_s": None,
                        "modified_s": feeds._when_s(r.get("Updated")),
                        "discovered_s": feeds._when_s(r.get("Started")),
                        "acres": feeds._maybe_float(r.get("AcresBurned"))})
        return out

    def _new_ids(self, src: str, ids: list, t: float, key=None) -> list:
        """The ids not seen before (none on the source's baseline poll);
        every id answered is (re)marked seen at t."""
        norm = [(i, (key(i) if key else i)) for i in ids]
        with self._lock:
            seen = self._seen[src]
            baseline = seen is None
            if baseline:
                seen = self._seen[src] = {}
            new = [raw for raw, k in norm if k and k not in seen]
            for _, k in norm:
                if k:
                    seen[k] = t
        return [] if baseline else new

    def _forget(self, t: float) -> None:
        for src, seen in self._seen.items():
            if seen:
                for k in [k for k, s in seen.items() if t - s > FORGET_S]:
                    del seen[k]
        if len(self._records) > KEEP_RECORDS:
            keep = sorted(self._records.values(),
                          key=lambda r: r["first_seen_s"])[-KEEP_RECORDS:]
            self._records = {r["key"]: r for r in keep}

    # -- a new record -------------------------------------------------------

    def _nearest(self, loc: LatLon):
        best = None
        try:
            homes = list(self._clients_fn() or [])
        except Exception:                      # noqa: BLE001
            log.exception("new-fire sentinel: the home roster failed")
            return None
        for c in homes:
            cid, cloc = ((c[0], c[1]) if isinstance(c, tuple)
                         else (c.client_id, c.loc))
            d = _haversine_m(loc, cloc)
            if best is None or d < best[1]:
                best = (cid, d)
        return best

    def _admit(self, rec: dict, t: float):
        """Under the lock: keep the record; an event when it is a non-RX
        fire within the radius of a home."""
        key = rec["key"]
        if key in self._records:
            return None
        near = self._nearest(rec["loc"])
        row = {"key": key, "source": rec["source"], "id": rec["id"],
               "name": rec["name"], "type": rec.get("type"),
               "lat": round(rec["loc"].lat, 5), "lon": round(rec["loc"].lon, 5),
               "first_seen_s": t, "created_s": rec.get("created_s"),
               "modified_s": rec.get("modified_s"),
               "discovered_s": rec.get("discovered_s"),
               "acres": rec.get("acres"),
               "nearest_client": None if near is None else near[0],
               "nearest_km": None if near is None else round(near[1] / 1000.0,
                                                             2),
               "event": False, "cycle_seen_s": None, "cycle_done_s": None,
               "_match": sorted(rec.get("match") or ())}
        if rec.get("created_s") is not None:
            row["created_to_seen_s"] = round(t - rec["created_s"], 1)
        self._records[key] = row
        rx = str(rec.get("type") or "").upper() == "RX"
        if near is None or near[1] > self._radius_m or rx:
            return None
        row["event"] = True
        km = near[1] / 1000.0
        ev = OperatorEvent(
            t_s=t, kind=EVENT_KIND,
            message=(f"NEW agency fire '{rec['name'] or rec['id']}' "
                     f"({rec['source']}) {km:.1f} km from client {near[0]} — "
                     f"first seen {_iso(t)} by the sentinel, before any "
                     f"cycle (shadow: the cycle is unchanged; SAFETY "
                     f"SEMANTICS 60)"),
            incident_id=(rec["id"] if rec["source"] == "wfigs" else key),
            detail={"source": rec["source"], "id": rec["id"],
                    "name": rec["name"], "lat": row["lat"], "lon": row["lon"],
                    "client": near[0], "km": round(km, 2),
                    "first_seen_s": t, "created_s": rec.get("created_s"),
                    "discovered_s": rec.get("discovered_s")})
        self._events = (self._events + [ev])[-KEEP_EVENTS:]
        return ev

    # -- the cycle, observed --------------------------------------------------

    def observe_cycle(self, res, fetched_s: float, done_s: float) -> int:
        """The daemon, after a live cycle: the records this cycle carried get
        cycle_seen_s / cycle_done_s. Returns how many were matched. Reads
        the result only; never raises."""
        try:
            keys: set = set()
            incs = list(getattr(res, "incidents", None) or [])
            incs += [getattr(r, "incident", None) for r in
                     (getattr(res, "removed_incidents", None) or [])]
            for inc in incs:
                if inc is None:
                    continue
                keys.add(str(inc.incident_id))
                if inc.irwin_id:
                    keys.add(str(inc.irwin_id))
                for src, v in (inc.source_ids or {}).items():
                    keys.add(f"{src}:{v}")
                    keys.add(str(v))
            n = 0
            with self._lock:
                for row in self._records.values():
                    if row["cycle_seen_s"] is not None:
                        continue
                    if any(m in keys for m in row["_match"]):
                        row["cycle_seen_s"] = float(fetched_s)
                        row["cycle_done_s"] = float(done_s)
                        n += 1
            return n
        except Exception:                      # noqa: BLE001
            log.exception("new-fire sentinel: observe_cycle failed")
            return 0

    # -- the /api/state block ---------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            recs = sorted(self._records.values(),
                          key=lambda r: -r["first_seen_s"])
            rows = [{k: v for k, v in r.items() if not k.startswith("_")}
                    for r in recs[:100]]
            leads = [r["cycle_done_s"] - r["first_seen_s"] for r in recs
                     if r["cycle_done_s"] is not None]
            return {
                "rule": dict(self._rule),
                "started_s": self._started_s,
                "polls": self._polls,
                "last_poll_s": self._last_poll_s,
                "sources": {s: {"baseline": self._seen[s] is not None,
                                "seen": len(self._seen[s] or {}),
                                "errors": d["errors"],
                                "last": (None if d["last"] is None else
                                         {k: v for k, v in d["last"].items()
                                          if k != "requests"}
                                         | {"requests": len(
                                             d["last"]["requests"]),
                                            "bytes": sum(
                                                q.get("wire_bytes")
                                                or q.get("bytes") or 0
                                                for q in d["last"][
                                                    "requests"])})}
                            for s, d in self._src.items()},
                "records": rows,
                "n_records": len(recs),
                "events": [{"t_s": e.t_s, "kind": e.kind,
                            "message": e.message,
                            "incident_id": e.incident_id,
                            "detail": dict(e.detail)}
                           for e in self._events],
                "lead": {"n": len(leads),
                         "mean_s": (round(sum(leads) / len(leads), 1)
                                    if leads else None),
                         "max_s": round(max(leads), 1) if leads else None,
                         "what": "cycle_done_s - first_seen_s: what a "
                                 "priority pass would have saved"},
            }
