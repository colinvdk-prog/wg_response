"""wgresponse.geo — spherical geometry + GridSpec/Raster helpers.

Units: distances in meters, angles in compass degrees (0 = north, clockwise),
coordinates in WGS84 decimal degrees. Spherical Earth, R = 6,371,008.8 m (IUGG
mean radius); grid degree<->meter math uses the SAME sphere so haversine_m,
destination and the GridSpec index math are mutually consistent.
"""
from __future__ import annotations

import math
from array import array
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

def point_in_poly(p: LatLon, poly) -> bool:
    """True iff p lies inside the simple polygon `poly` (sequence of LatLon
    vertices, closed implicitly). Even-odd ray cast in plain lat/lon space —
    adequate for the km-scale planning polygons it serves (community
    boundaries on a sim tile); NOT a geodesic operation. Points exactly on
    an edge may fall either side (irrelevant at 90 m cell granularity).
    Empty/short poly (< 3 vertices) is False."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = poly[i].lat, poly[i].lon
        yj, xj = poly[j].lat, poly[j].lon
        if ((yi > p.lat) != (yj > p.lat)) and (
                p.lon < (xj - xi) * (p.lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


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


def _boundary_params(a: float, b: float) -> list:
    """Parameters s in (0, 1) where the linear coordinate a + s (b - a)
    crosses an integer (a cell boundary in grid units), ascending."""
    if a == b:
        return []
    lo, hi = (a, b) if a < b else (b, a)
    out = []
    k = math.floor(lo) + 1
    while k < hi:
        out.append((k - a) / (b - a))
        k += 1
    if a > b:
        out.reverse()
    return out


#: path_cell_runs memo: (GridSpec, tuple(path)) -> runs. Bounded (cleared
#: whole on overflow: simple, deterministic); the geometry of a path on a
#: grid never changes, so every caller shares one table.
_CELL_RUNS_MEMO: dict = {}
_CELL_RUNS_MEMO_MAX = 20_000


def path_cell_runs(spec: GridSpec, path) -> tuple:
    """Every grid cell a polyline passes through, in driving order (corridor
    CELL-EXACT law, contracts corridor_cell_exact / SAFETY SEMANTICS 20).

    `path` is an ordered sequence of LatLon followed LINEARLY in lat/lon
    between consecutive points — exactly how the simulator's crews move
    (sim._interp) — and its along-path distance is the haversine length of
    each span, spread linearly within the span. Returns (exits, idxs), two
    parallel arrays with one entry per maximal stretch of the path inside
    ONE cell of `spec` (a stretch that only clips a cell's corner is a
    stretch of its own): exits[k] the cumulative distance (m) at which the
    path LEAVES the stretch — the latest moment a vehicle on the path is in
    that cell — and idxs[k] its flat index row * ncols + col. Stretches off
    the grid are omitted.

    Row boundaries are linear in latitude (exact); column boundaries follow
    each row's latitude (m_per_deg_lon), so every span is walked in sub-
    spans of at most one cell, within which the column coordinate is
    linear to well under a millimetre. Each stretch's cell is the cell of
    its midpoint (latlon_to_rc — the formula raster_sample uses), so a
    point the vehicle occupies always maps to a cell of some stretch.
    Deterministic; memoised per (spec, path)."""
    key = (spec, tuple(path))
    hit = _CELL_RUNS_MEMO.get(key)
    if hit is not None:
        return hit
    cell = spec.cell_m
    north, west, nc = spec.north_lat, spec.west_lon, spec.ncols
    runs = []
    cur = None                      # flat index of the open run (or -1 off-grid)
    cum = 0.0
    for j in range(1, len(path)):
        p0, p1 = path[j - 1], path[j]
        d = haversine_m(p0, p1)
        if d <= 0.0:
            continue
        n = max(1, int(math.ceil(d / cell)))
        dsub = d / n
        for k in range(n):
            f0, f1 = k / n, (k + 1) / n
            la0 = p0.lat + (p1.lat - p0.lat) * f0
            lo0 = p0.lon + (p1.lon - p0.lon) * f0
            la1 = p0.lat + (p1.lat - p0.lat) * f1
            lo1 = p0.lon + (p1.lon - p0.lon) * f1
            y0 = (north - la0) * M_PER_DEG_LAT / cell
            y1 = (north - la1) * M_PER_DEG_LAT / cell
            x0 = (lo0 - west) * m_per_deg_lon(la0) / cell
            x1 = (lo1 - west) * m_per_deg_lon(la1) / cell
            ss = sorted(set(_boundary_params(y0, y1) + _boundary_params(x0, x1)))
            ss.append(1.0)
            base = cum + k * dsub
            sa = 0.0
            for sb in ss:
                if sb <= sa:
                    continue
                sm = 0.5 * (sa + sb)
                rc = latlon_to_rc(spec, LatLon(la0 + (la1 - la0) * sm,
                                               lo0 + (lo1 - lo0) * sm))
                idx = -1 if rc is None else rc[0] * nc + rc[1]
                exit_m = base + sb * dsub
                if idx == cur and runs:
                    runs[-1] = (exit_m, idx)
                else:
                    runs.append((exit_m, idx))
                    cur = idx
                sa = sb
        cum += d
    out = (array("d", [e for e, i in runs if i >= 0]),
           array("q", [i for _e, i in runs if i >= 0]))
    if len(_CELL_RUNS_MEMO) >= _CELL_RUNS_MEMO_MAX:
        _CELL_RUNS_MEMO.clear()
    _CELL_RUNS_MEMO[key] = out
    return out


def erode_min(r: Raster, radius_m: float, shape: str = "chebyshev") -> Raster:
    """Morphological MINIMUM filter used to erode P10 arrival grids for
    corridor safety: each output cell holds the earliest (minimum) arrival
    within `radius_m`, so a corridor point inherits the worst arrival time
    nearby. Window is clipped at grid borders; radius_m <= 0 returns an
    independent copy.

    shape="chebyshev" (default, legacy): square window of half-width
    ceil(radius_m/cell_m) cells — separable row/col passes, O(n*k). The
    square over-reaches: half-width is ceil'd (540 m for 500 m @ 90 m cells)
    and corners reach sqrt(2)*k cells (764 m) — measured importing arrivals
    from 757 m away onto road cells the fire never reaches
    (docs: founder-arithmetic trace 2026-07-29, commit c05d622).

    shape="euclidean": true metric disc — offset (dr, dc) participates iff
    hypot(dr, dc) * cell_m <= radius_m. Implemented as per-row sliding-window
    minima with disc-clipped half-widths per row offset, O(n*k). Same
    conservative semantics (min over the neighborhood), honest radius.
    """
    spec = r.spec
    nr, nc = spec.nrows, spec.ncols
    if radius_m <= 0:
        return Raster(spec, list(r.data))
    if shape == "chebyshev":
        k = math.ceil(radius_m / spec.cell_m)
        if k <= 0:
            return Raster(spec, list(r.data))
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
    if shape != "euclidean":
        raise ValueError(f"unknown erode_min shape {shape!r}")
    rad_cells = radius_m / spec.cell_m
    kmax = int(rad_cells)                      # floor: honest metric reach
    # Horizontal sliding-window minima at each disc-clipped half-width, built
    # once per needed width and shared across the row offsets that use it.
    widths = {}
    for dr in range(-kmax, kmax + 1):
        w = int(math.sqrt(max(0.0, rad_cells * rad_cells - dr * dr)))
        widths[dr] = w
    row_mins = {}                              # half-width -> flat row-min grid
    for w in sorted(set(widths.values())):
        if w == 0:
            row_mins[w] = r.data
            continue
        g = [0.0] * (nr * nc)
        for i in range(nr):
            base = i * nc
            row = r.data[base:base + nc]
            for j in range(nc):
                lo = j - w if j - w > 0 else 0
                hi = j + w + 1 if j + w + 1 < nc else nc
                g[base + j] = min(row[lo:hi])
        row_mins[w] = g
    out = [math.inf] * (nr * nc)
    for dr in range(-kmax, kmax + 1):
        g = row_mins[widths[dr]]
        for i in range(nr):
            src = i + dr
            if not (0 <= src < nr):
                continue
            base, sbase = i * nc, src * nc
            for j in range(nc):
                v = g[sbase + j]
                if v < out[base + j]:
                    out[base + j] = v
    return Raster(spec, out)
