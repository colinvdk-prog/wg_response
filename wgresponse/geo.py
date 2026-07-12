"""wgresponse.geo — spherical geometry + GridSpec/Raster helpers.

Units: distances in meters, angles in compass degrees (0 = north, clockwise),
coordinates in WGS84 decimal degrees. Spherical Earth, R = 6,371,008.8 m (IUGG
mean radius); grid degree<->meter math uses the SAME sphere so haversine_m,
destination and the GridSpec index math are mutually consistent.
"""
from __future__ import annotations

import math
from typing import Optional

from wgresponse.contracts import BBox, GridSpec, LatLon, Raster

EARTH_RADIUS_M = 6_371_008.8          # IUGG mean Earth radius, meters
M_PER_DEG_LAT = EARTH_RADIUS_M * math.pi / 180.0   # ~111,194.93 m per degree latitude


def m_per_deg_lon(lat_deg: float) -> float:
    """Meters per degree of longitude at latitude `lat_deg` (degrees).

    cos(lat) floored at 1e-6 so polar-degenerate inputs never divide by zero.
    """
    return M_PER_DEG_LAT * max(math.cos(math.radians(lat_deg)), 1e-6)


# ---------------------------------------------------------------------------
# Great-circle primitives
# ---------------------------------------------------------------------------

def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points, meters."""
    phi1 = math.radians(a.lat)
    phi2 = math.radians(b.lat)
    dphi = phi2 - phi1
    dlam = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def destination(origin: LatLon, bearing_deg: float, dist_m: float) -> LatLon:
    """Point `dist_m` meters from `origin` along initial compass bearing `bearing_deg`.

    Great-circle (spherical) forward solution; longitude normalized to [-180, 180).
    """
    if dist_m == 0.0:
        return LatLon(origin.lat, origin.lon)
    delta = dist_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg)
    phi1 = math.radians(origin.lat)
    lam1 = math.radians(origin.lon)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    sin_phi2 = min(1.0, max(-1.0, sin_phi2))
    phi2 = math.asin(sin_phi2)
    lam2 = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * sin_phi2,
    )
    lon = (math.degrees(lam2) + 180.0) % 360.0 - 180.0
    return LatLon(math.degrees(phi2), lon)


def bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial great-circle compass bearing from `a` to `b`, degrees in [0, 360)."""
    phi1 = math.radians(a.lat)
    phi2 = math.radians(b.lat)
    dlam = math.radians(b.lon - a.lon)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def sample_polyline(a: LatLon, b: LatLon, step_m: float) -> list:
    """Sample points every `step_m` meters along the great circle from `a` to `b`.

    Returns [a, a+step, a+2*step, ..., b]; `b` is always the exact final element.
    Degenerate a==b returns [a]. step_m must be > 0.
    """
    if step_m <= 0.0:
        raise ValueError("step_m must be > 0")
    dist = haversine_m(a, b)
    if dist < 1e-9:
        return [a]
    brg = bearing_deg(a, b)
    pts = [a]
    s = step_m
    while s < dist - 1e-6:
        pts.append(destination(a, brg, s))
        s += step_m
    pts.append(b)
    return pts


def bbox_around(points: list, pad_m: float) -> BBox:
    """Axis-aligned BBox containing all `points`, padded outward by `pad_m` meters.

    Longitude padding uses cos(latitude) at the largest-|lat| point so the pad is
    never under-sized within the box.
    """
    if not points:
        raise ValueError("bbox_around requires at least one point")
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    pad_lat = pad_m / M_PER_DEG_LAT
    worst_lat = max(abs(min(lats)), abs(max(lats)))
    pad_lon = pad_m / m_per_deg_lon(worst_lat)
    return BBox(
        west=min(lons) - pad_lon,
        south=min(lats) - pad_lat,
        east=max(lons) + pad_lon,
        north=max(lats) + pad_lat,
    )


# ---------------------------------------------------------------------------
# GridSpec / Raster helpers (geo owns the exact index<->latlon math)
# ---------------------------------------------------------------------------

def make_raster(spec: GridSpec, fill: float = 0.0) -> Raster:
    """New Raster on `spec` with every cell set to `fill` (units caller-defined)."""
    return Raster(spec, fill=fill)


def latlon_to_rc(spec: GridSpec, p: LatLon) -> Optional[tuple]:
    """(row, col) of the cell containing `p`, or None if `p` is outside the grid.

    Row 0 = north edge, col 0 = west edge (contracts convention). Uses the
    contracts GridSpec meter-per-degree model on the geo sphere.
    """
    r = math.floor((spec.north_lat - p.lat) * M_PER_DEG_LAT / spec.cell_m)
    c = math.floor((p.lon - spec.west_lon) * m_per_deg_lon(p.lat) / spec.cell_m)
    if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
        return (int(r), int(c))
    return None


def rc_to_latlon(spec: GridSpec, r: int, c: int) -> LatLon:
    """Center LatLon of cell (r, c) per the contracts GridSpec formulas."""
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / m_per_deg_lon(lat)
    return LatLon(lat, lon)


def raster_sample(r: Raster, p: LatLon) -> Optional[float]:
    """Nearest-cell (containing-cell) raster value at `p`; None outside the grid."""
    rc = latlon_to_rc(r.spec, p)
    if rc is None:
        return None
    return r.get(rc[0], rc[1])


def erode_min(r: Raster, radius_m: float) -> Raster:
    """Morphological MINIMUM filter over a square window of radius ceil(radius_m/cell_m) cells.

    Used to erode P10 arrival grids for corridor safety: each output cell holds the
    earliest (minimum) arrival within the window, so a corridor point inherits the
    worst arrival time within `radius_m`. Separable row/col passes, O(n*k).
    Window is clipped at grid borders. radius_m <= 0 returns an independent copy.
    """
    spec = r.spec
    k = math.ceil(radius_m / spec.cell_m) if radius_m > 0 else 0
    if k <= 0:
        return Raster(spec, list(r.data))
    nr, nc = spec.nrows, spec.ncols
    tmp = [0.0] * (nr * nc)
    for i in range(nr):
        base = i * nc
        row = r.data[base:base + nc]
        for j in range(nc):
            lo = j - k if j - k > 0 else 0
            hi = j + k + 1 if j + k + 1 < nc else nc
            tmp[base + j] = min(row[lo:hi])
    out = [0.0] * (nr * nc)
    for j in range(nc):
        col = tmp[j::nc]
        for i in range(nr):
            lo = i - k if i - k > 0 else 0
            hi = i + k + 1 if i + k + 1 < nr else nr
            out[i * nc + j] = min(col[lo:hi])
    return Raster(spec, out)
