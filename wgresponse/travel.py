"""wgresponse.travel — Tier-0 travel-time estimation.

Public seam (contracts.MODULE SEAMS):
    travel_time_s(a, b, cfg) -> float                                # seconds
    travel_matrix_s(origins, destinations, cfg) -> list[list[float]] # matrix helper

Tier-0 model (docs/research/dispatch_optimization.md section 4.1): great-circle
(haversine) distance in meters, inflated by a road-circuity factor and a
fire/evacuation traffic factor, divided by a blended road-class speed:

    time_s = haversine_m(a, b) * cfg.circuity * cfg.fire_traffic_factor / cfg.road_speed_mps

UPGRADE PATH (Tier 1 — self-hosted routing engine; keep this seam stable):
  * Valhalla (recommended): POST /route and POST /sources_to_targets (matrix).
    Dynamic runtime costing means road closures and the live fire polygon can be
    passed per-request as ``exclude_locations`` / ``exclude_polygons`` with no
    graph recontraction — routes then structurally avoid the fire, with the
    grid corridor check (wgresponse.safety) kept as belt-and-braces.
  * OSRM: GET /route/v1/driving/... and /table/v1/driving/... (fastest matrices).
  * The PUBLIC OSRM DEMO SERVER (router.project-osrm.org) is DEVELOPMENT ONLY:
    ~1 req/s, tables capped at 10,000 durations/request, non-commercial usage
    policy, no uptime guarantee. WG is commercial, so production MUST self-host
    (osrm-backend or Valhalla via Docker + a Geofabrik state extract, e.g.
    california-latest.osm.pbf).
  A Tier-1 implementation should keep the (a, b, cfg) -> seconds signature,
  cache the crew x property matrix, and refresh a crew's row only when the
  crew moves > 1 km from the position its cached row was computed at.

geo.py note: geo.haversine_m is the contracted distance seam; it is resolved
lazily so this module works (with a byte-equivalent local fallback) while
geo.py is still being built by a sibling cluster.
"""
from __future__ import annotations

import math

from wgresponse.contracts import LatLon, ResponseConfig

_GEO = None
_GEO_RESOLVED = False


def _geo():
    """Lazily resolve wgresponse.geo (built concurrently); None if unavailable."""
    global _GEO, _GEO_RESOLVED
    if not _GEO_RESOLVED:
        try:
            from wgresponse import geo as _g  # type: ignore
            _GEO = _g
        except Exception:
            _GEO = None
        _GEO_RESOLVED = True
    return _GEO


def _haversine_m_local(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in meters (mean Earth radius 6,371,000 m)."""
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(h)))


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Distance in meters between a and b; delegates to geo.haversine_m when
    geo.py exists, else uses the local haversine fallback."""
    g = _geo()
    if g is not None and hasattr(g, "haversine_m"):
        return g.haversine_m(a, b)
    return _haversine_m_local(a, b)


def travel_time_s(a: LatLon, b: LatLon, cfg: ResponseConfig) -> float:
    """Estimated drive time in SECONDS from a to b (Tier-0 model).

    time_s = haversine_m * cfg.circuity * cfg.fire_traffic_factor / cfg.road_speed_mps
    (cfg.road_speed_mps is a blended road-class speed in m/s; circuity and
    fire_traffic_factor are dimensionless multipliers >= 1).
    """
    if cfg.road_speed_mps <= 0.0:
        raise ValueError("cfg.road_speed_mps must be > 0")
    return haversine_m(a, b) * cfg.circuity * cfg.fire_traffic_factor / cfg.road_speed_mps


def travel_matrix_s(origins: list, destinations: list, cfg: ResponseConfig) -> list:
    """Travel-time matrix in SECONDS: result[i][j] = travel_time_s(origins[i], destinations[j], cfg).

    Row-major list-of-lists (len(origins) x len(destinations)). A Tier-1
    engine-backed version should map to one OSRM /table or Valhalla
    /sources_to_targets call.
    """
    return [[travel_time_s(o, d, cfg) for d in destinations] for o in origins]
