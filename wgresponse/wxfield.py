"""wgresponse.wxfield — LOCAL WEATHER across a spread domain (contracts SAFETY
SEMANTICS 17; founder 2026-09-27, verbatim: "ensure that fire spread includes
local weather forecast"; cfg.weather_grid_km, default 0 = OFF; watch
--weather-grid-km KM [--weather-points-max N]).

The legacy law prices every edge of a domain with ONE hourly forecast fetched
at the fire centroid. National domains measure 50-75 km across (bbox of the
fire + the protected clients within the threat radius, padded 2 km), so one
point stands in for ridges, valleys and coast at once. With the knob on:

  1. POINTS (lattice_points). The centroid stays the PRIMARY forecast (point
     0, zone 0). The others are the nodes of a GLOBAL lat/lon lattice that
     fall on the domain's grid (geo.latlon_to_rc is not None). At level L the
     spacing is s_L = cfg.weather_grid_km x 2^(L//2) x (sqrt 2 if L is odd)
     km (level_spacing_km: each level sqrt 2 coarser, every second level
     exactly x2); lattice row i (an integer, counted from the equator) sits
     at lat_i = i x s / geo.M_PER_DEG_LAT and holds the nodes lon = j x s /
     geo.m_per_deg_lon(lat_i) (j an integer, counted from Greenwich) — nodes
     ~s apart east-west at their own latitude, rows s apart north-south. A
     node is a function of (s, i, j) only, never of the domain, so
     overlapping domains at one level ask the SAME points (and hit the same
     forecast-cache entries), and level L+2's nodes are exactly level L's
     rows 2i, columns 2j (float-exact: x2 is exact in binary), a subset. L
     starts at 0 and grows until centroid + nodes <= cfg.weather_points_max.
     Why sqrt 2 steps: a step halves the node count instead of quartering
     it, so a domain keeps at least about half its point budget (measured:
     a 40 x 53 km SoCal domain with 16 points went from ~20 nodes at 10 km
     straight to 4 at 20 km under x2 steps; sqrt 2 steps keep 11 at 14.1
     km). Order: north to south, west to east.
  2. FETCH (build_field). Every node through the SAME WeatherProvider as the
     centroid (the watch daemon: NwsWeather, which with the knob on caches
     the static points -> gridpoint mapping and each gridpoint's series for
     cfg.weather_cache_ttl_s), FETCH_WORKERS at a time, results kept by
     index. A node whose fetch fails (or returns no samples) falls back to
     the centroid forecast for its zone: WeatherPoint.ok = False and one
     cycle note naming it (threat.py). The centroid's own failure never gets
     here: it is the legacy provider failure (SEMANTICS 6).
  3. ZONES (zone_map). Every cell takes its NEAREST point — a Voronoi
     partition in local metres: north-south (lat - lat_row) x M_PER_DEG_LAT,
     east-west (lon - lon_cell) x m_per_deg_lon(lat_row), i.e. measured at
     the cell's own row latitude; ties go to the lower point index. Per row
     the squared distance to point p is, up to a term common to all points,
     the LINE A_p + B_p x c in the column c, so each row is filled by
     walking the lower envelope of <= 255 lines (a handful of switches per
     row) instead of testing every point at every cell; bytes, one per cell,
     cached per (grid, points) (_ZONE_CACHE_MAX entries). Zones are discrete
     — never blended per cell — so spread's ROS memo keeps sharing entries.
  4. SPREAD. spread.compute_arrival / ensemble_arrival(weather_field=...)
     price every edge with its SOURCE cell's zone forecast: the per-hour
     effective weather becomes per (zone, hour); ensemble perturbations
     apply identically in every zone; an ember flies on its source cell's
     zone wind. A domain with no lattice node inside prices from the
     centroid alone — the legacy law (threat.py passes no field then).
  5. SURFACING (field_row / rule_summary). The /api/state incident rows and
     the /api/world/summary rows gain a "weather" block (knob on only): the
     points (lat, lon, role, source, ok, cells), each point's wind, gust,
     direction, RH and temperature at LEADS_H hours after the cycle, and
     how far the points disagree at each lead.

What it cannot see: winds finer than the forecast's own grid (NWS 2.5 km),
anything between lattice nodes (nearest point, no interpolation — a zone
edge is a discontinuity), the fire's own wind, and the forecast's error.

Units: km for the lattice spacing, m for distances, unix seconds, m/s, deg
FROM, %, C (contracts). Stdlib only; deterministic.
"""
from __future__ import annotations

import dataclasses
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from wgresponse import geo
from wgresponse.contracts import LatLon, WeatherField, WeatherPoint

#: coarsening safety cap: level <= this (spacing = weather_grid_km x 2^12)
_MAX_LEVEL = 24
#: concurrent node fetches (results are collected by index, so the field
#: never depends on completion order; NWS is comfortable at a handful)
FETCH_WORKERS = 4
#: forecast leads (hours after the cycle) each point reports on the surface
LEADS_H = (0.0, 3.0, 6.0, 12.0, 24.0)
#: zone maps kept (one byte per cell each; ~0.3 MB for a 300k-cell domain)
_ZONE_CACHE_MAX = 32

_ZONE_CACHE: dict = {}
_ZONE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 1. the global lattice
# ---------------------------------------------------------------------------

def _grid_bounds(spec) -> "tuple[float, float, float, float]":
    """(south, north, west, east) degrees enclosing the grid (east at the
    row with the fewest metres per degree of longitude, i.e. the widest)."""
    south = spec.north_lat - spec.nrows * spec.cell_m / geo.M_PER_DEG_LAT
    east = spec.west_lon + spec.ncols * spec.cell_m / min(
        geo.m_per_deg_lon(spec.north_lat), geo.m_per_deg_lon(south))
    return south, spec.north_lat, spec.west_lon, east


def lattice_nodes(spec, spacing_km: float) -> list:
    """The GLOBAL lattice nodes at `spacing_km` that fall on `spec`'s grid
    (module docstring, step 1), north to south, west to east."""
    s_m = float(spacing_km) * 1000.0
    dlat = s_m / geo.M_PER_DEG_LAT
    south, north, west, east = _grid_bounds(spec)
    out = []
    for i in range(math.floor(north / dlat), math.ceil(south / dlat) - 1, -1):
        lat = i * dlat
        dlon = s_m / geo.m_per_deg_lon(lat)
        for j in range(math.ceil(west / dlon), math.floor(east / dlon) + 1):
            p = LatLon(lat, j * dlon)
            if geo.latlon_to_rc(spec, p) is not None:
                out.append(p)
    return out


def level_spacing_km(grid_km: float, level: int) -> float:
    """Lattice spacing (km) at coarsening `level`: grid_km x 2^(level//2),
    times sqrt 2 on odd levels — each level sqrt 2 coarser, every second
    level EXACTLY twice the one two below it (so those nest)."""
    s = float(grid_km) * (2 ** (level // 2))
    return s * math.sqrt(2.0) if level % 2 else s


def lattice_points(spec, cfg) -> "tuple[list, float, int]":
    """(nodes, spacing_km, level): the lattice nodes on `spec`'s grid at the
    finest level whose centroid + nodes <= cfg.weather_points_max (module
    docstring, step 1). weather_points_max = 1 leaves no room for a node."""
    g = float(cfg.weather_grid_km)
    cap = max(0, int(cfg.weather_points_max) - 1)
    if g <= 0.0 or cap == 0:
        return [], g, 0
    level = 0
    while True:
        spacing = level_spacing_km(g, level)
        nodes = lattice_nodes(spec, spacing)
        if len(nodes) <= cap or level >= _MAX_LEVEL:
            break
        level += 1
    return nodes[:cap], spacing, level


# ---------------------------------------------------------------------------
# 3. the zones (nearest point per cell)
# ---------------------------------------------------------------------------

def _argmin_at(a: list, b: list, c: int) -> int:
    """Index of the lowest line a[i] + b[i] * c (ties to the lower index)."""
    best = 0
    bv = a[0] + b[0] * c
    for i in range(1, len(a)):
        v = a[i] + b[i] * c
        if v < bv:
            best, bv = i, v
    return best


def _fill_row(a: list, b: list, ncols: int, out: bytearray, base: int) -> None:
    """Fill out[base:base+ncols] with the lowest line's index per column c
    (the lower envelope of the lines a[i] + b[i] * c). From the current
    winner `cur` at column c, a line q can only overtake it where it falls
    faster (b[q] < b[cur]), first at c* = (a[q] - a[cur]) / (b[cur] - b[q]);
    the run [c, min floor(c*)) is `cur`'s, and the next run's winner is
    re-evaluated there (the floor makes the step conservative: a column that
    has not switched yet just advances by one)."""
    k = len(a)
    c = 0
    cur = _argmin_at(a, b, 0)
    while True:
        nxt = ncols
        a0 = a[cur]
        b0 = b[cur]
        for q in range(k):
            if q == cur:
                continue
            db = b0 - b[q]
            if db > 0.0:
                cx = (a[q] - a0) / db
                if cx < nxt:
                    cand = math.floor(cx) if cx > c else c + 1
                    if cand <= c:
                        cand = c + 1
                    if cand < nxt:
                        nxt = cand
        out[base + c:base + nxt] = bytes((cur,)) * (nxt - c)
        if nxt >= ncols:
            return
        c = nxt
        cur = _argmin_at(a, b, c)


def compute_zone_map(spec, points: list) -> bytes:
    """Zone (point index) of every cell of `spec`, row-major, one byte per
    cell: the NEAREST point in local metres at the cell's row latitude, ties
    to the lower index (module docstring, step 3). Uncached."""
    n = spec.nrows * spec.ncols
    k = len(points)
    if k > 256:
        raise ValueError(f"{k} weather points: zones are one byte per cell "
                         f"(<= 256)")
    if k <= 1:
        return bytes(n)
    out = bytearray(n)
    cell = float(spec.cell_m)
    for r in range(spec.nrows):
        lat_r = spec.north_lat - (r + 0.5) * cell / geo.M_PER_DEG_LAT
        mlon = geo.m_per_deg_lon(lat_r)
        a = []
        b = []
        for p in points:
            x = (p.lon - spec.west_lon) * mlon          # metres east of the west edge
            dy = (p.lat - lat_r) * geo.M_PER_DEG_LAT     # metres north of the row
            # d^2 = ((c + 0.5) * cell - x)^2 + dy^2 ; drop the common
            # ((c + 0.5) * cell)^2 -> x^2 + dy^2 - x*cell - 2*x*cell * c
            a.append(x * x + dy * dy - x * cell)
            b.append(-2.0 * x * cell)
        _fill_row(a, b, spec.ncols, out, r * spec.ncols)
    return bytes(out)


def zone_map(spec, points: list) -> bytes:
    """compute_zone_map, cached per (grid, points) — the same domain with the
    same points (every cycle of a fire that did not grow) is computed once."""
    key = (spec, tuple((p.lat, p.lon) for p in points))
    with _ZONE_LOCK:
        hit = _ZONE_CACHE.get(key)
    if hit is not None:
        return hit
    zm = compute_zone_map(spec, points)
    with _ZONE_LOCK:
        _ZONE_CACHE[key] = zm
        while len(_ZONE_CACHE) > _ZONE_CACHE_MAX:
            _ZONE_CACHE.pop(next(iter(_ZONE_CACHE)))
    return zm


def clear_zone_cache() -> None:
    """Forget every cached zone map (tests)."""
    with _ZONE_LOCK:
        _ZONE_CACHE.clear()


# ---------------------------------------------------------------------------
# 2 + 4. fetch and assemble the field
# ---------------------------------------------------------------------------

def _fetch_one(provider, loc: LatLon, horizon_h: float):
    """(forecast, "") or (None, error text) — a node's own fetch."""
    try:
        fc = provider.forecast(loc, horizon_h)
    except Exception as exc:  # any provider failure is this node's failure
        return None, (str(exc) or type(exc).__name__)
    if fc is None or not getattr(fc, "samples", None):
        return None, "the provider returned no hourly samples"
    return fc, ""


def fetch_nodes(provider, nodes: list, horizon_h: float,
                workers: int = FETCH_WORKERS) -> list:
    """[(forecast | None, error)] per node, in node order."""
    if workers <= 1 or len(nodes) <= 1:
        return [_fetch_one(provider, p, horizon_h) for p in nodes]
    with ThreadPoolExecutor(max_workers=min(workers, len(nodes))) as ex:
        return list(ex.map(lambda p: _fetch_one(provider, p, horizon_h),
                           nodes))


def build_field(provider, primary_wx, primary_loc: LatLon, spec, cfg,
                horizon_h: float, workers: int = FETCH_WORKERS) -> WeatherField:
    """The WeatherField of one domain (module docstring, steps 1-3): zone 0 =
    `primary_wx` at `primary_loc` (the fire-centroid forecast the caller
    already fetched), zones 1.. = the lattice nodes on `spec`'s grid, each
    fetched through `provider` (a failed node carries `primary_wx`,
    ok=False, with the error). WeatherPoint.cells counts each zone's cells."""
    nodes, spacing, level = lattice_points(spec, cfg)
    fetched = fetch_nodes(provider, nodes, horizon_h, workers)
    forecasts = [primary_wx]
    points = [WeatherPoint(lat=primary_loc.lat, lon=primary_loc.lon,
                           role="primary", ok=True,
                           source=str(getattr(primary_wx, "source", "")))]
    for node, (fc, err) in zip(nodes, fetched):
        if fc is None:
            forecasts.append(primary_wx)
            points.append(WeatherPoint(
                lat=node.lat, lon=node.lon, role="lattice", ok=False,
                source=str(getattr(primary_wx, "source", "")), error=err))
        else:
            forecasts.append(fc)
            points.append(WeatherPoint(lat=node.lat, lon=node.lon,
                                       role="lattice", ok=True,
                                       source=str(getattr(fc, "source", ""))))
    zm = zone_map(spec, [primary_loc] + list(nodes))
    points = [dataclasses.replace(p, cells=zm.count(bytes((z,))))
              for z, p in enumerate(points)]
    return WeatherField(spec=spec, forecasts=forecasts, zone_of=zm,
                        points=points, spacing_km=spacing, level=level)


def field_notes(incident_id: str, field: WeatherField, cfg) -> list:
    """The cycle notes of one domain's field: one summary line, plus one line
    per failed node naming it (SEMANTICS 17b/f)."""
    n = len(field.points)
    failed = [(z, p) for z, p in enumerate(field.points) if not p.ok]
    if n <= 1:
        head = (f"incident {incident_id}: local weather — no "
                f"{float(cfg.weather_grid_km):g} km lattice node falls in "
                f"this domain: spread prices the fire-centroid forecast alone "
                f"(legacy; SAFETY SEMANTICS 17)")
        return [head]
    spacing = (f"{field.spacing_km:.3g} km lattice" + (
        f" (coarsened from {float(cfg.weather_grid_km):g} km to fit "
        f"{int(cfg.weather_points_max)} points)" if field.level else ""))
    out = [f"incident {incident_id}: local weather — {n} forecast points "
           f"(fire centroid + {n - 1} nodes of the {spacing}), "
           f"{len(failed)} failed; every cell spreads on its nearest point's "
           f"forecast (SAFETY SEMANTICS 17)"]
    for z, p in failed:
        out.append(f"incident {incident_id}: weather point "
                   f"{p.lat:.4f},{p.lon:.4f} (lattice zone {z}, {p.cells} "
                   f"cells) failed ({p.error}): its zone spreads on the "
                   f"fire-centroid forecast (SAFETY SEMANTICS 17b)")
    return out


# ---------------------------------------------------------------------------
# 5. surfacing
# ---------------------------------------------------------------------------

def _arc_deg(dirs: list) -> float:
    """Smallest arc (deg) containing every direction in `dirs`."""
    if len(dirs) <= 1:
        return 0.0
    a = sorted(d % 360.0 for d in dirs)
    gaps = [a[i + 1] - a[i] for i in range(len(a) - 1)] + [a[0] + 360.0 - a[-1]]
    return round(360.0 - max(gaps), 1)


def _sample_row(s, lead_h: float, t_s: float) -> dict:
    return {"h": lead_h, "t_s": t_s,
            "wind_mps": round(s.wind_speed_mps, 2),
            "gust_mps": round(s.wind_gust_mps, 2),
            "dir_deg": round(s.wind_dir_deg % 360.0, 1),
            "rh_pct": round(s.rh_pct, 1),
            "temp_c": round(s.temp_c, 1)}


def field_row(field: Optional[WeatherField], now_s: float,
              leads_h=LEADS_H) -> Optional[dict]:
    """The "weather" block of one incident row (/api/state and the world
    summary; SEMANTICS 17f): the lattice spacing, every point with its zone
    size and its forecast at each lead (hours after now_s), and how far the
    points disagree at each lead (wind and RH min/max, the smallest arc
    holding every direction). None for no field."""
    if field is None:
        return None
    pts = []
    by_lead = {h: [] for h in leads_h}
    for z, (p, fc) in enumerate(zip(field.points, field.forecasts)):
        hours = []
        for h in leads_h:
            t = float(now_s) + float(h) * 3600.0
            s = fc.at(t)
            hours.append(_sample_row(s, float(h), t))
            by_lead[h].append(s)
        pts.append({"zone": z, "lat": round(p.lat, 5), "lon": round(p.lon, 5),
                    "role": p.role, "source": p.source, "ok": bool(p.ok),
                    "error": p.error or None, "cells": int(p.cells),
                    "hours": hours})
    across = []
    for h in leads_h:
        ss = by_lead[h]
        across.append({
            "h": float(h),
            "wind_mps": [round(min(s.wind_speed_mps for s in ss), 2),
                         round(max(s.wind_speed_mps for s in ss), 2)],
            "gust_mps": [round(min(s.wind_gust_mps for s in ss), 2),
                         round(max(s.wind_gust_mps for s in ss), 2)],
            "dir_arc_deg": _arc_deg([s.wind_dir_deg for s in ss]),
            "rh_pct": [round(min(s.rh_pct for s in ss), 1),
                       round(max(s.rh_pct for s in ss), 1)],
        })
    return {"spacing_km": field.spacing_km, "level": field.level,
            "points_n": len(field.points),
            "failed": sum(1 for p in field.points if not p.ok),
            "zones_used": sum(1 for p in field.points if p.cells > 0),
            "points": pts, "across_points": across}


def rule_summary(cfg) -> dict:
    """The rule's parameters for /api/state "weather_grid" (SEMANTICS 17f)."""
    return {"grid_km": float(cfg.weather_grid_km),
            "points_max": int(cfg.weather_points_max),
            "cache_ttl_s": float(cfg.weather_cache_ttl_s),
            "leads_h": [float(h) for h in LEADS_H],
            "rule": ("fire centroid + the global lattice nodes inside each "
                     "domain (coarsened in sqrt-2 steps until within "
                     "points_max); every cell spreads on its nearest point's "
                     "forecast; a failed node falls back to the centroid "
                     "(SAFETY SEMANTICS 17)")}
