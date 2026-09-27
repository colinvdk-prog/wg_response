"""wgresponse.feeds — live fire-incident feed adapters + multi-source fusion.

Adapters (all satisfy contracts.FireFeed):
    WfigsFeed                 NIFC/WFIGS ArcGIS current perimeters + incident points (US)
    FirisFeed                 CAL FIRE FIRIS/WFIGS ComboLayer (fast CA perimeters)
    CalFireFeed               incidents.fire.ca.gov IncidentApi (CA metadata)
    FirmsFeed                 NASA FIRMS area CSV hotspots -> clustered incidents
    WatchDutyEnterpriseFeed   STUB — enterprise API not yet licensed (raises RuntimeError)
    MockFireFeed              deterministic synthetic incidents (contracts.seeded_rng)

Fusion:
    fuse_incidents(batches) -> list[FireIncident]
        Join on IRWIN id (case-insensitive, brace-stripped), else NORMALIZED
        name + origin distance < 5 km (Sprint 2 ticket C3b, Council-1 OPS-16:
        the same physical fire arrived as four fused incidents — 'VOLCANO' via
        WFIGS+FIRIS, 'Volcano Fire' via CAL FIRE — producing four parallel
        alert/notification streams per client). Name normalization: case-fold,
        strip punctuation/whitespace, strip trailing 'Fire'/'Incident' tokens.
        Newest updated_at_s wins per field; hotspots unioned; source_ids keeps
        per-source provenance. The canonical incident_id of a fused group is a
        STABLE preference — IRWIN id when any member has one, else the id of
        the most-preferred source present (wfigs > firis > calfire > firms >
        watchduty > mock; lexicographic tie-break) — so the SAME physical fire
        keeps ONE incident_id across cycles even as sources phase in/out or
        their relative freshness shuffles (alert pairs key on incident_id; an
        id flip is a client-visible rename).

Endpoint URLs, field names, and payload shapes are encoded exactly per
docs/research/fire_feeds.md (live-verified 2026-07-06).

Conventions: all times unix seconds (float); distances meters; coordinates
contracts.LatLon (WGS84 degrees). Network access goes through an injectable
``http_get(url, headers) -> (status_code, body_bytes)`` so tests never touch
the network. ``fetch_active`` NEVER raises on transport/parse failure — it
logs and returns [] (a dead feed must not kill the alert loop). Stdlib only.

Health convention (Sprint 2, contracts SAFETY SEMANTICS 6): every adapter
maintains ``last_fetch_ok: bool`` after each fetch_active call — True iff
HTTP + parse succeeded end to end. An empty result with last_fetch_ok=True
means genuinely zero incidents; last_fetch_ok=False means the adapter is
BLIND this cycle (it still returns [] per the robustness design above), and
consumers (threat.ThreatMonitor) must treat the cycle as degraded — absence
of an incident is then NOT clearance for its alert pairs.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from .contracts import BBox, FireIncident, IncidentSource, LatLon, seeded_rng

log = logging.getLogger(__name__)

# http_get(url, headers) -> (status_code, body_bytes)
HttpGet = Callable[[str, dict], "tuple[int, bytes]"]

_EARTH_R_M = 6_371_000.0


def _default_http_get(url: str, headers: Optional[dict] = None,
                      timeout_s: float = 30.0) -> "tuple[int, bytes]":
    """Stdlib urllib GET. Returns (HTTP status code, raw body bytes)."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec: fixed feed URLs
        return resp.getcode(), resp.read()


def _haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in meters (local copy; geo.py owns the shared one)."""
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (math.sin((la2 - la1) / 2.0) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2.0) ** 2)
    return 2.0 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def _norm_irwin(raw: Optional[str]) -> Optional[str]:
    """Canonical IRWIN id: brace-stripped, lowercased. None-safe."""
    if not raw:
        return None
    s = str(raw).strip().strip("{}").strip().lower()
    return s or None


_NAME_STRIP_TOKENS = frozenset({"fire", "incident"})


def _norm_name(raw) -> str:
    """Normalized incident-name join key (Sprint 2 ticket C3b, OPS-16).

    Case-folds, replaces every non-alphanumeric character with a space,
    collapses whitespace, then strips trailing 'fire'/'incident' tokens
    ('Volcano Fire' -> 'volcano' == 'VOLCANO' -> 'volcano'). Only TRAILING
    tokens are stripped — 'Fire Mountain' keeps its leading word. Returns ''
    for empty/None/suffix-only names; an empty key must never join."""
    s = str(raw or "").casefold()
    tokens = "".join(ch if ch.isalnum() else " " for ch in s).split()
    while tokens and tokens[-1] in _NAME_STRIP_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _when_s(v) -> Optional[float]:
    """Coerce a feed timestamp to unix seconds. Accepts epoch ms (ArcGIS),
    epoch s, or ISO-8601 strings ('Z' ok; naive treated as UTC)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 1000.0 if f > 1e11 else f   # epoch ms vs s
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _subsample_ring(ring: list, max_vertices: int = 200) -> list:
    """Thin a perimeter ring to <= max_vertices+1 points, keeping the first."""
    if len(ring) <= max_vertices:
        return ring
    stride = math.ceil(len(ring) / float(max_vertices))
    return ring[::stride]


def _envelope_params(bbox: BBox) -> dict:
    """ArcGIS spatial-filter query params for a WGS84 envelope."""
    return {
        "geometry": json.dumps({"xmin": bbox.west, "ymin": bbox.south,
                                "xmax": bbox.east, "ymax": bbox.north,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def _ring_centroid(ring: list) -> LatLon:
    """Vertex-mean representative point of a perimeter ring (degrees)."""
    n = max(1, len(ring))
    return LatLon(sum(p.lat for p in ring) / n, sum(p.lon for p in ring) / n)


def _ring_from_raw(raw: list) -> list:
    """Convert one esri ring (list of [lon, lat, ...] verts) to list[LatLon].

    Esri feature JSON is dirty: a vertex may be a null/None coordinate or a
    non-numeric string. Each vertex is converted defensively — a bad vertex is
    skipped, not allowed to raise, so one dirty coordinate cannot discard the
    whole ring/incident/feed batch. Returns [] for empty/all-bad input.
    Longitude is v[0], latitude is v[1] (esri x/y order)."""
    ring = []
    for v in raw:
        if not (isinstance(v, (list, tuple)) and len(v) >= 2):
            continue
        lon = _maybe_float(v[0])
        lat = _maybe_float(v[1])
        if lon is None or lat is None:
            continue
        ring.append(LatLon(lat, lon))
    return ring


class _ArcGisFeedBase:
    """Shared ArcGIS FeatureServer query plumbing (f=json, pagination).

    PAGE CAP IS LOUD (W6 CONUS check, 2026-09-26): the layers page at
    maxRecordCount = 2000 and the pager stops after _MAX_PAGES pages. On
    2026-09-26 the national (CONUS-envelope) queries needed ONE page each
    (378 incident points, 129 perimeters, 439 FIRIS polygons — every count
    matched the layer's returnCountOnly), but a pager that runs out of pages
    while the server still says exceededTransferLimit has DROPPED the tail.
    That case now logs an ERROR, keeps every feature it did fetch, and sets
    ``last_fetch_truncated`` — fetch_active then reports last_fetch_ok=False
    (a partial fetch is a blind fetch: absence is not clearance). Any fetch
    that ends within the cap is byte-identical to before."""

    _MAX_PAGES = 5

    def __init__(self, http_get: Optional[HttpGet] = None):
        self._http_get = http_get or _default_http_get
        # Health flag per the module docstring convention; True until a fetch
        # proves otherwise (optimistic pre-first-fetch default).
        self.last_fetch_ok = True
        self.last_fetch_truncated = False

    def _query(self, layer_url: str, out_fields: str,
               bbox: Optional[BBox]) -> list:
        """Return the feature dict list for one layer query; raises on failure."""
        params = {"where": "1=1", "outFields": out_fields, "f": "json",
                  "returnGeometry": "true", "outSR": "4326"}
        if bbox is not None:
            params.update(_envelope_params(bbox))
        features: list = []
        offset = 0
        for _page in range(self._MAX_PAGES):
            page_params = dict(params)
            if offset:
                page_params["resultOffset"] = str(offset)
            url = layer_url + "?" + urllib.parse.urlencode(page_params)
            status, body = self._http_get(url, {"Accept": "application/json"})
            if status != 200:
                raise RuntimeError(f"HTTP {status} from {layer_url}")
            payload = json.loads(body.decode("utf-8"))
            if "error" in payload:
                raise RuntimeError(f"ArcGIS error from {layer_url}: {payload['error']}")
            page = payload.get("features") or []
            features.extend(page)
            if not payload.get("exceededTransferLimit") or not page:
                break
            offset += len(page)
        else:
            # every page was full and the server still has more: the tail
            # beyond _MAX_PAGES pages is NOT in `features` — never silent.
            self.last_fetch_truncated = True
            log.error("ArcGIS page cap reached for %s: %d features over %d "
                      "pages and the server still reports "
                      "exceededTransferLimit — the rest are MISSING from "
                      "this fetch (marked unhealthy: absence is not "
                      "clearance)", layer_url, len(features), self._MAX_PAGES)
        return features


class WfigsFeed(_ArcGisFeedBase):
    """NIFC WFIGS current interagency perimeters + incident locations (US-wide).

    Queries both layers (f=json), joins them on IRWIN id, and returns one
    FireIncident per incident: origin from the point layer, perimeter rings
    (subsampled to ~200 vertices) from the polygon layer. Prescribed burns
    (IncidentTypeCategory == 'RX') are dropped unless include_rx=True.
    Timestamps arrive as epoch milliseconds -> converted to unix seconds.
    """

    PERIMETERS_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                      "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query")
    POINTS_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                  "WFIGS_Incident_Locations_Current/FeatureServer/0/query")
    POINT_OUT_FIELDS = ("IncidentName,IrwinID,IncidentSize,PercentContained,"
                        "FireDiscoveryDateTime,ModifiedOnDateTime_dt,POOState,"
                        "IncidentTypeCategory")
    PERIM_OUT_FIELDS = ("poly_IncidentName,attr_IncidentName,poly_IRWINID,attr_IrwinID,"
                        "poly_GISAcres,attr_CalculatedAcres,attr_FinalAcres,"
                        "attr_PercentContained,attr_FireDiscoveryDateTime,"
                        "attr_ModifiedOnDateTime_dt,poly_DateCurrent")

    def __init__(self, http_get: Optional[HttpGet] = None,
                 max_ring_vertices: int = 200, include_rx: bool = False):
        super().__init__(http_get)
        self._max_ring_vertices = max_ring_vertices
        self._include_rx = include_rx

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        """Active incidents (optionally bbox-filtered). Never raises: transport or
        parse failure logs a warning and yields []. Times unix s, acreage acres.
        Sets self.last_fetch_ok — True only when BOTH layer queries and the
        parse succeeded (a partial fetch is a blind fetch: it can silently
        drop incidents, so it must not read as 'genuinely clear')."""
        ok = True
        points: list = []
        perims: list = []
        self.last_fetch_truncated = False
        try:
            points = self._query(self.POINTS_URL, self.POINT_OUT_FIELDS, bbox)
        except Exception:
            ok = False
            log.warning("WFIGS incident-locations query failed", exc_info=True)
        try:
            perims = self._query(self.PERIMETERS_URL, self.PERIM_OUT_FIELDS, bbox)
        except Exception:
            ok = False
            log.warning("WFIGS perimeters query failed", exc_info=True)
        try:
            out = self._build(points, perims)
        except Exception:
            log.warning("WFIGS parse failed", exc_info=True)
            self.last_fetch_ok = False
            return []
        # a page-capped (truncated) layer is a partial fetch: keep the data,
        # but it must not read as 'genuinely clear' (class docstring)
        self.last_fetch_ok = ok and not self.last_fetch_truncated
        return out

    # -- parsing ------------------------------------------------------------

    def _build(self, points: list, perims: list) -> "list[FireIncident]":
        incidents: dict = {}   # join key -> FireIncident
        order: list = []
        for feat in points:
            try:
                inc = self._from_point(feat)
            except Exception:
                # One malformed point feature must not discard the whole batch.
                log.warning("WFIGS skipping malformed point feature", exc_info=True)
                continue
            if inc is None:
                continue
            key = _norm_irwin(inc.irwin_id) or inc.incident_id
            if key not in incidents:
                incidents[key] = inc
                order.append(key)
        for feat in perims:
            try:
                item = self._from_perimeter(feat)
            except Exception:
                # One malformed perimeter feature must not discard the whole batch.
                log.warning("WFIGS skipping malformed perimeter feature", exc_info=True)
                continue
            if item is None:
                continue
            inc, key = item
            base = incidents.get(key)
            if base is None:
                incidents[key] = inc
                order.append(key)
                continue
            base.perimeter = inc.perimeter
            base.updated_at_s = max(base.updated_at_s, inc.updated_at_s)
            # F3: the joined record keeps the freshest updated_at_s (point OR
            # perimeter), but the perimeter's OWN provenance time rides along
            # so a nightly-IR polygon on a minutes-fresh point record is not
            # rasterized as freshly burned (see _from_perimeter).
            pat = _perimeter_provenance_s(inc)
            if pat is not None:
                base.meta["perimeter_at_s"] = pat
            if base.acreage is None:
                base.acreage = inc.acreage
            if base.containment_pct is None:
                base.containment_pct = inc.containment_pct
        return [incidents[k] for k in order]

    def _from_point(self, feat: dict):
        attrs = feat.get("attributes") or {}
        geom = feat.get("geometry") or {}
        if geom.get("x") is None or geom.get("y") is None:
            return None
        if not self._include_rx and attrs.get("IncidentTypeCategory") == "RX":
            return None
        irwin = _norm_irwin(attrs.get("IrwinID"))
        name = attrs.get("IncidentName") or ""
        updated = _when_s(attrs.get("ModifiedOnDateTime_dt")) or 0.0
        native = attrs.get("OBJECTID")
        return FireIncident(
            incident_id=irwin or f"wfigs:{native if native is not None else name.lower()}",
            name=name,
            source=IncidentSource.WFIGS,
            origin=LatLon(float(geom["y"]), float(geom["x"])),
            updated_at_s=updated,
            discovered_at_s=_when_s(attrs.get("FireDiscoveryDateTime")),
            acreage=_maybe_float(attrs.get("IncidentSize")),
            containment_pct=_maybe_float(attrs.get("PercentContained")),
            active=True,
            irwin_id=irwin,
            source_ids={"wfigs": irwin or str(native)},
            meta={"poo_state": attrs.get("POOState")},
        )

    def _from_perimeter(self, feat: dict):
        attrs = feat.get("attributes") or {}
        geom = feat.get("geometry") or {}
        rings_raw = geom.get("rings") or []
        rings = []
        for raw in rings_raw:
            ring = _ring_from_raw(raw)
            if ring:
                rings.append(_subsample_ring(ring, self._max_ring_vertices))
        if not rings:
            return None
        irwin = _norm_irwin(attrs.get("poly_IRWINID") or attrs.get("attr_IrwinID"))
        name = attrs.get("poly_IncidentName") or attrs.get("attr_IncidentName") or ""
        native = attrs.get("OBJECTID")
        key = irwin or f"wfigs:{native if native is not None else name.lower()}"
        updated = (_when_s(attrs.get("attr_ModifiedOnDateTime_dt"))
                   or _when_s(attrs.get("poly_DateCurrent")) or 0.0)
        acreage = (_maybe_float(attrs.get("poly_GISAcres"))
                   or _maybe_float(attrs.get("attr_CalculatedAcres"))
                   or _maybe_float(attrs.get("attr_FinalAcres")))
        # PERIMETER PROVENANCE (Session-3 verifier F3): poly_DateCurrent is
        # the POLYGON's own currency time; attr_ModifiedOnDateTime_dt is the
        # incident record's. The perimeter must be rasterized as burned at
        # ITS OWN age (meta["perimeter_at_s"], read by threat._assess_incident)
        # even when the incident record — or a fused fresh member — is newer,
        # or the modeled front starts up to (fresh - perimeter age) hours LATE.
        perimeter_at = _when_s(attrs.get("poly_DateCurrent")) or updated
        inc = FireIncident(
            incident_id=key,
            name=name,
            source=IncidentSource.WFIGS,
            origin=_ring_centroid(rings[0]),
            updated_at_s=updated,
            discovered_at_s=_when_s(attrs.get("attr_FireDiscoveryDateTime")),
            acreage=acreage,
            containment_pct=_maybe_float(attrs.get("attr_PercentContained")),
            active=True,
            perimeter=rings,
            irwin_id=irwin,
            source_ids={"wfigs": irwin or str(native)},
            meta=({"perimeter_at_s": float(perimeter_at)} if perimeter_at
                  else {}),
        )
        return inc, key


class FirisFeed(_ArcGisFeedBase):
    """CAL FIRE 'FIRIS WFIGS ComboLayer' — fastest public CA perimeters.

    Field schema of the combo layer is not pinned (per the research digest), so
    attributes are extracted tolerantly from candidate key lists,
    case-insensitively. Geometry is esri polygon rings (lon, lat)."""

    LAYER_URL = ("https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
                 "CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0/query")
    _NAME_KEYS = ("poly_incidentname", "attr_incidentname", "incidentname",
                  "incident_name", "mission", "name")
    _IRWIN_KEYS = ("poly_irwinid", "attr_irwinid", "irwinid", "irwin_id")
    _UPDATED_KEYS = ("poly_datecurrent", "attr_modifiedondatetime_dt", "datecurrent",
                     "date_current", "last_edited_date", "editdate", "updated")
    _ACRES_KEYS = ("poly_gisacres", "gisacres", "gis_acres", "acres", "area_acres")

    def __init__(self, http_get: Optional[HttpGet] = None, max_ring_vertices: int = 200):
        super().__init__(http_get)
        self._max_ring_vertices = max_ring_vertices

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        """Active CA perimeter incidents; never raises (logs + []).
        Sets self.last_fetch_ok per the module health convention (a
        page-capped fetch keeps its features but reports unhealthy)."""
        self.last_fetch_truncated = False
        try:
            feats = self._query(self.LAYER_URL, "*", bbox)
            out = []
            for feat in feats:
                try:
                    inc = self._from_feature(feat)
                except Exception:
                    # One malformed feature must not discard the whole batch.
                    log.warning("FIRIS skipping malformed feature", exc_info=True)
                    continue
                if inc is not None:
                    out.append(inc)
            self.last_fetch_ok = not self.last_fetch_truncated
            return out
        except Exception:
            log.warning("FIRIS ComboLayer fetch failed", exc_info=True)
            self.last_fetch_ok = False
            return []

    def _from_feature(self, feat: dict) -> Optional[FireIncident]:
        attrs = {str(k).lower(): v for k, v in (feat.get("attributes") or {}).items()}
        geom = feat.get("geometry") or {}
        rings = []
        for raw in geom.get("rings") or []:
            ring = _ring_from_raw(raw)
            if ring:
                rings.append(_subsample_ring(ring, self._max_ring_vertices))
        if not rings:
            return None
        pick = lambda keys: next((attrs[k] for k in keys if attrs.get(k) not in (None, "")), None)
        irwin = _norm_irwin(pick(self._IRWIN_KEYS))
        name = str(pick(self._NAME_KEYS) or "")
        native = attrs.get("objectid")
        return FireIncident(
            incident_id=irwin or f"firis:{native if native is not None else name.lower()}",
            name=name,
            source=IncidentSource.FIRIS,
            origin=_ring_centroid(rings[0]),
            updated_at_s=_when_s(pick(self._UPDATED_KEYS)) or 0.0,
            acreage=_maybe_float(pick(self._ACRES_KEYS)),
            active=True,
            perimeter=rings,
            irwin_id=irwin,
            source_ids={"firis": irwin or str(native)},
        )


class CalFireFeed:
    """incidents.fire.ca.gov IncidentApi/List?inactive=false — CA incident
    metadata (duty-officer curated; hours cadence). Point + narrative only,
    no perimeters. Timestamps ISO-8601 (naive treated as UTC)."""

    LIST_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false"

    def __init__(self, http_get: Optional[HttpGet] = None):
        self._http_get = http_get or _default_http_get
        self.last_fetch_ok = True

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        """Active CA incidents (bbox filter applied client-side); never raises.
        Sets self.last_fetch_ok per the module health convention."""
        try:
            status, body = self._http_get(self.LIST_URL, {"Accept": "application/json"})
            if status != 200:
                raise RuntimeError(f"HTTP {status} from CAL FIRE IncidentApi")
            records = json.loads(body.decode("utf-8"))
            out = []
            for rec in records:
                inc = self._from_record(rec)
                if inc is None:
                    continue
                if bbox is not None and not bbox.contains(inc.origin):
                    continue
                out.append(inc)
            self.last_fetch_ok = True
            return out
        except Exception:
            log.warning("CAL FIRE IncidentApi fetch failed", exc_info=True)
            self.last_fetch_ok = False
            return []

    @staticmethod
    def _from_record(rec: dict) -> Optional[FireIncident]:
        lat = _maybe_float(rec.get("Latitude"))
        lon = _maybe_float(rec.get("Longitude"))
        if lat is None or lon is None:
            return None
        uid = str(rec.get("UniqueId") or "").lower()
        active = rec.get("IsActive")
        if not isinstance(active, bool):
            active = not bool(rec.get("Final"))
        return FireIncident(
            incident_id=f"calfire:{uid}",
            name=str(rec.get("Name") or ""),
            source=IncidentSource.CALFIRE,
            origin=LatLon(lat, lon),
            updated_at_s=_when_s(rec.get("Updated")) or 0.0,
            discovered_at_s=_when_s(rec.get("Started")),
            acreage=_maybe_float(rec.get("AcresBurned")),
            containment_pct=_maybe_float(rec.get("PercentContained")),
            active=active,
            irwin_id=None,   # CAL FIRE API does not expose IRWIN ids
            source_ids={"calfire": uid},
            meta={"county": rec.get("County"), "url": rec.get("Url")},
        )


class FirmsFeed:
    """NASA FIRMS area API — satellite hotspot CSV, clustered into incidents.

    URL: https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/
         {west,south,east,north|world}/{day_range}
    Default source VIIRS_NOAA20_NRT (375 m). Detections are greedily grouped by
    centroid distance (cluster_km, default ~2 km) into synthetic incidents whose
    .hotspots list (newest first) seeds the spread model between perimeter
    updates. updated_at_s = newest acquisition time (UTC). Rate limit: 5000
    transactions / 10 min per MAP_KEY — respect cfg.poll_period_s upstream.

    MAP_KEY (Sprint 2 ticket C3d, OPS-13): the free key comes from the
    5-minute signup form at SIGNUP_URL. ``FirmsFeed()`` constructs WITHOUT a
    key — construction never raises — but every keyless fetch_active is BLIND:
    it returns [] with last_fetch_ok=False (consumers must apply SAFETY
    SEMANTICS 6 — absence is not clearance) and logs ONE loud warning naming
    the signup URL (once per adapter instance, not per poll)."""

    BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
    SIGNUP_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/"

    def __init__(self, map_key: Optional[str] = None,
                 source: str = "VIIRS_NOAA20_NRT",
                 day_range: int = 1, cluster_km: float = 2.0,
                 http_get: Optional[HttpGet] = None):
        self._map_key = map_key or None      # '' and None both mean "no key"
        self._source = source
        self._day_range = max(1, min(5, int(day_range)))
        self._cluster_m = cluster_km * 1000.0
        self._http_get = http_get or _default_http_get
        self._warned_no_key = False
        self.last_fetch_ok = True

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        """Hotspot-cluster incidents in bbox (or 'world'); never raises.
        Sets self.last_fetch_ok per the module health convention. Without a
        MAP_KEY the adapter is BLIND: [] + last_fetch_ok=False + one loud
        log naming the signup URL (see class docstring)."""
        if not self._map_key:
            if not self._warned_no_key:
                log.warning(
                    "FIRMS MAP_KEY missing — FirmsFeed is BLIND (returns [] "
                    "with last_fetch_ok=False; hotspot data unavailable). Get "
                    "a free MAP_KEY in ~5 minutes at %s and pass it as "
                    "FirmsFeed(map_key=...).", self.SIGNUP_URL)
                self._warned_no_key = True
            self.last_fetch_ok = False
            return []
        area = ("world" if bbox is None else
                f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}")
        url = f"{self.BASE_URL}/{self._map_key}/{self._source}/{area}/{self._day_range}"
        try:
            status, body = self._http_get(url, {"Accept": "text/csv"})
            if status != 200:
                raise RuntimeError(f"HTTP {status} from FIRMS")
            detections = self._parse_csv(body.decode("utf-8", errors="replace"))
            out = self._cluster(detections)
            self.last_fetch_ok = True
            return out
        except Exception:
            log.warning("FIRMS fetch failed", exc_info=True)
            self.last_fetch_ok = False
            return []

    @staticmethod
    def _parse_csv(text: str) -> list:
        """-> list[(t_s, LatLon, frp)] sorted newest first (tie: lat, lon)."""
        out = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                d = datetime.strptime(row["acq_date"], "%Y-%m-%d")
                hm = int(row["acq_time"])
                t = datetime(d.year, d.month, d.day, hm // 100, hm % 100,
                             tzinfo=timezone.utc).timestamp()
            except (KeyError, ValueError, TypeError):
                continue
            frp = _maybe_float(row.get("frp"))
            out.append((t, LatLon(lat, lon), frp))
        out.sort(key=lambda x: (-x[0], x[1].lat, x[1].lon))
        return out

    def _cluster(self, detections: list) -> "list[FireIncident]":
        clusters: list = []   # each: {"pts": [(t, LatLon, frp)], "lat": ..., "lon": ...}
        for det in detections:
            _, p, _ = det
            placed = False
            for cl in clusters:
                if _haversine_m(p, LatLon(cl["lat"], cl["lon"])) <= self._cluster_m:
                    cl["pts"].append(det)
                    n = len(cl["pts"])
                    cl["lat"] += (p.lat - cl["lat"]) / n
                    cl["lon"] += (p.lon - cl["lon"]) / n
                    placed = True
                    break
            if not placed:
                clusters.append({"pts": [det], "lat": p.lat, "lon": p.lon})
        out = []
        for cl in clusters:
            pts = cl["pts"]                       # already newest first
            centroid = LatLon(cl["lat"], cl["lon"])
            newest = max(t for t, _, _ in pts)
            frp_max = max((f for _, _, f in pts if f is not None), default=None)
            cid = f"firms:{centroid.lat:.3f},{centroid.lon:.3f}"
            out.append(FireIncident(
                incident_id=cid,
                name=f"FIRMS cluster {centroid.lat:.3f},{centroid.lon:.3f}",
                source=IncidentSource.FIRMS,
                origin=centroid,
                updated_at_s=newest,
                hotspots=[p for _, p, _ in pts],
                active=True,
                source_ids={"firms": cid},
                meta={"n_detections": len(pts), "max_frp": frp_max,
                      "satellite_source": self._source},
            ))
        out.sort(key=lambda i: i.incident_id)
        return out


class WatchDutyEnterpriseFeed:
    """STUB adapter slot for the Watch Duty Enterprise API.

    Watch Duty's API is an Enterprise addon requiring a negotiated license
    (docs/research/fire_feeds.md §1). The unofficial app API is expressly
    ToS-prohibited for automated/commercial use — this stub exists so a
    licensed integration drops in without touching callers."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.last_fetch_ok = True

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        self.last_fetch_ok = False   # unlicensed stub can never fetch
        raise RuntimeError(
            "Watch Duty enterprise API access not yet licensed — see "
            "docs/research/fire_feeds.md; unofficial api.watchduty.org is "
            "ToS-prohibited for commercial/automated use")


class MockFireFeed:
    """Deterministic synthetic incidents for tests/simulation.

    Same (seed, center, n, spread_km, t0_s) always yields identical incidents
    (contracts.seeded_rng; no wall-clock). Pass ``incidents=[...]`` to serve a
    fixed list instead. Units: spread_km kilometers, t0_s unix seconds."""

    def __init__(self, seed: object, incidents: "Optional[list[FireIncident]]" = None,
                 center: LatLon = LatLon(34.20, -118.50), n: int = 3,
                 spread_km: float = 25.0, t0_s: float = 1_780_000_000.0):
        self.last_fetch_ok = True   # mock fetches always succeed
        if incidents is not None:
            self._incidents = list(incidents)
            return
        self._incidents = []
        for i in range(n):
            rng = seeded_rng(seed, "mockfire", i)
            dlat = rng.uniform(-spread_km, spread_km) * 1000.0 / 111_320.0
            dlon = (rng.uniform(-spread_km, spread_km) * 1000.0
                    / (111_320.0 * max(0.2, math.cos(math.radians(center.lat)))))
            origin = LatLon(center.lat + dlat, center.lon + dlon)
            half_deg = rng.uniform(0.5, 2.0) * 1000.0 / 111_320.0  # ~0.5-2 km box
            ring = [
                LatLon(origin.lat - half_deg, origin.lon - half_deg),
                LatLon(origin.lat - half_deg, origin.lon + half_deg),
                LatLon(origin.lat + half_deg, origin.lon + half_deg),
                LatLon(origin.lat + half_deg, origin.lon - half_deg),
                LatLon(origin.lat - half_deg, origin.lon - half_deg),
            ]
            irwin = f"mock-{seed}-{i}"
            self._incidents.append(FireIncident(
                incident_id=irwin,
                name=f"MOCK FIRE {i + 1}",
                source=IncidentSource.MOCK,
                origin=origin,
                updated_at_s=t0_s - rng.uniform(0.0, 6.0) * 3600.0,
                discovered_at_s=t0_s - rng.uniform(6.0, 48.0) * 3600.0,
                acreage=round(rng.uniform(50.0, 5000.0), 1),
                containment_pct=round(rng.uniform(0.0, 60.0), 0),
                active=True,
                perimeter=[ring],
                irwin_id=irwin,
                source_ids={"mock": irwin},
            ))

    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]":
        self.last_fetch_ok = True
        if bbox is None:
            return list(self._incidents)
        return [i for i in self._incidents if bbox.contains(i.origin)]


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

_NAME_JOIN_DIST_M = 5_000.0

# Canonical-id source preference (Sprint 2 ticket C3b): when a fused group has
# no IRWIN id, the group's incident_id comes from the most-preferred source
# present. Preference reflects id stability: WFIGS native ids are IRWIN-backed
# interagency records, FIRIS rows are mission products, CAL FIRE UniqueIds are
# curated, FIRMS ids are synthetic centroid coordinates. Lower rank wins.
_CANONICAL_SOURCE_RANK = {
    IncidentSource.WFIGS: 0,
    IncidentSource.FIRIS: 1,
    IncidentSource.CALFIRE: 2,
    IncidentSource.FIRMS: 3,
    IncidentSource.WATCHDUTY: 4,
    IncidentSource.MOCK: 5,
}


def fuse_incidents(batches: "list[list[FireIncident]]") -> "list[FireIncident]":
    """Fuse per-feed incident batches into one deduplicated list.

    Join key: IRWIN id (case-insensitive, brace-stripped); fallback join:
    same NORMALIZED name (_norm_name: case-fold, punctuation/whitespace
    stripped, trailing 'Fire'/'Incident' tokens dropped) AND origin distance
    < 5 km. The name join applies whenever IRWIN ids cannot decide — absent on
    either side or differing (ticket C3b; a shared normalized name within 5 km
    is the same physical fire even when two sources minted different ids).
    Per field the value from the member with the newest updated_at_s (that has
    the field set) wins; hotspots are unioned; source_ids merges provenance
    from every member; the perimeter owner's own currency time rides along as
    meta["perimeter_at_s"] (Session-3 F3 — an old polygon fused onto a fresh
    record must not be rasterized as freshly burned). The fused incident_id
    is STABLE across cycles: IRWIN id
    when known, else the most-preferred source's id (_CANONICAL_SOURCE_RANK,
    lexicographic tie-break) — never "whichever member is freshest today".
    Output sorted by canonical incident_id. All times unix seconds."""
    incidents = [inc for batch in batches for inc in batch]
    groups: "list[list[FireIncident]]" = []
    group_norms: "list[list[str]]" = []      # parallel: normalized member names
    by_irwin: dict = {}
    for inc in incidents:
        nm = _norm_name(inc.name)
        gi = None
        key = _norm_irwin(inc.irwin_id)
        if key is not None and key in by_irwin:
            gi = by_irwin[key]
        if gi is None and nm:
            for j, group in enumerate(groups):
                if any(gnm == nm
                       and _haversine_m(inc.origin, m.origin) < _NAME_JOIN_DIST_M
                       for m, gnm in zip(group, group_norms[j])):
                    gi = j
                    break
        if gi is None:
            groups.append([inc])
            group_norms.append([nm])
            gi = len(groups) - 1
        else:
            groups[gi].append(inc)
            group_norms[gi].append(nm)
        if key is not None:
            by_irwin.setdefault(key, gi)
    fused = [_merge_group(g) for g in groups]
    fused.sort(key=lambda i: i.incident_id)
    return fused


def _canonical_group_id(group: "list[FireIncident]",
                        irwin: Optional[str]) -> str:
    """Stable canonical incident_id for a fused group (ticket C3b).

    IRWIN id when the group has one; else the lexicographically-smallest
    incident_id among the members of the most-preferred source present
    (_CANONICAL_SOURCE_RANK). Deliberately independent of updated_at_s
    ordering: alert pairs key on incident_id, so an id that flips with
    per-cycle freshness is a client-visible incident rename every cycle."""
    if irwin:
        return irwin
    best_rank = min(_CANONICAL_SOURCE_RANK.get(m.source, 99) for m in group)
    return min(m.incident_id for m in group
               if _CANONICAL_SOURCE_RANK.get(m.source, 99) == best_rank)


def _perimeter_provenance_s(inc: FireIncident) -> Optional[float]:
    """The perimeter's own provenance time for `inc`, unix s, or None.

    Session-3 verifier F3: a fused record carries ONE updated_at_s (the
    freshest member's), so the member that contributed the PERIMETER must
    also contribute the time that polygon was current — else an old ring on
    a fresh record is rasterized as freshly burned and the modeled front
    runs up to (fresh - perimeter age) hours LATE (measured 47.8 h). Prefers
    the member's own meta["perimeter_at_s"] (WFIGS poly_DateCurrent path),
    falls back to the member's updated_at_s (single-timestamp feeds: FIRIS /
    CAL FIRE — for them the record time IS the perimeter time). Non-numeric
    / non-positive values yield None (never fabricate an epoch-0 stamp: a
    bogus ancient provenance would burn the whole domain decades ago)."""
    pat = (inc.meta or {}).get("perimeter_at_s")
    if isinstance(pat, (int, float)) and not isinstance(pat, bool) and pat > 0:
        return float(pat)
    upd = inc.updated_at_s
    if isinstance(upd, (int, float)) and not isinstance(upd, bool) and upd > 0:
        return float(upd)
    return None


def _merge_group(group: "list[FireIncident]") -> FireIncident:
    if len(group) == 1:
        return group[0]
    newest_first = sorted(group, key=lambda i: -i.updated_at_s)

    def pick(attr):
        for m in newest_first:
            v = getattr(m, attr)
            if v is not None and v != "":
                return v
        return None

    # Stable irwin choice: sorted, not newest-first (two sources reporting
    # DIFFERENT IRWIN ids for one name-joined fire must not flip the fused id
    # as their relative freshness shuffles cycle to cycle).
    irwin = min((k for k in (_norm_irwin(m.irwin_id) for m in group) if k),
                default=None)
    # hotspots: union in member-recency order, dedupe on rounded coords
    hotspots: list = []
    seen = set()
    for m in newest_first:
        for p in (m.hotspots or []):
            k = (round(p.lat, 6), round(p.lon, 6))
            if k not in seen:
                seen.add(k)
                hotspots.append(p)
    source_ids: dict = {}
    meta: dict = {}
    for m in reversed(newest_first):        # oldest first so newest wins
        source_ids.update(m.source_ids or {})
        meta.update(m.meta or {})
    head = newest_first[0]
    perimeter = pick("perimeter")
    # PERIMETER PROVENANCE (Session-3 verifier F3): the fused updated_at_s is
    # the FRESHEST member's, but the perimeter may come from an OLDER member
    # (e.g. a nightly-IR FIRIS polygon fused with a minutes-fresh WFIGS point
    # record). Stamp the fused meta with the perimeter OWNER's provenance so
    # threat._assess_incident rasterizes the interior at the polygon's own
    # age — never the fused freshness. The blind meta-merge above may have
    # pulled another member's stamp: always overwrite from the owner.
    meta.pop("perimeter_at_s", None)
    if perimeter is not None and perimeter != "":
        owner = next(m for m in newest_first
                     if m.perimeter is not None and m.perimeter != "")
        pat = _perimeter_provenance_s(owner)
        if pat is not None:
            meta["perimeter_at_s"] = pat
    return FireIncident(
        incident_id=_canonical_group_id(group, irwin),
        name=pick("name") or head.name,
        source=head.source,
        origin=pick("origin") or head.origin,
        updated_at_s=head.updated_at_s,
        discovered_at_s=pick("discovered_at_s"),
        acreage=pick("acreage"),
        containment_pct=pick("containment_pct"),
        active=head.active,
        perimeter=perimeter,
        hotspots=hotspots or None,
        irwin_id=irwin,
        source_ids=source_ids,
        meta=meta,
    )


def _maybe_float(v) -> Optional[float]:
    """float() with None/blank/garbage -> None (feeds are dirty)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
