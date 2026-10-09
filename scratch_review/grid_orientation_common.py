"""Shared helpers for the grid-orientation study (2026-10-09; NOT product code).

A flat, uniform tile sized to hold the 6-h analytic fire, one ignition, a
constant forecast, spotting OFF, the engine's nominal compute_arrival — and
the two references every measurement is compared with:

  ANALYTIC   the engine's OWN elliptical fire for the same fuel / wind: head
             rate R_h (rothermel + the fuel-class floor, exactly as
             spread.cell_params computes it on flat ground), length-to-breadth
             lb = rothermel.ellipse_lb(U_eff), ignition at the rear focus.
             Travel time to offset x is the ellipse gauge
                 F(x) = (|x| - e x.h) / (R_h (1 - e)),   e = sqrt(lb^2-1)/lb
             (the focus-directrix form of directional_ros_mps' polar law).
  POLYGON    what a lattice of stencil directions CAN reach in a uniform
             medium: the convex hull of R(theta_i - brg) * t along each
             stencil bearing theta_i (MTT on a lattice = the polygon
             inscribed in the ellipse with vertices on the stencil rays).

Everything scales linearly with time on a uniform tile (self-similar), so the
ratios measured/analytic do not depend on the hour picked.
"""
from __future__ import annotations

import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from wgresponse import geo, rothermel, spread  # noqa: E402
from wgresponse.contracts import (GridSpec, LatLon, ResponseConfig,  # noqa: E402
                                  TerrainTile, WeatherForecast,
                                  WeatherSample)
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac  # noqa: E402

T0 = 1_750_000_000.0
NORTH, WEST = 39.5, -121.5
MPH = 0.44704
ACRE_M2 = 4046.8564224
FUELS = {"GR2": 102, "GS2": 122, "SH5": 145, "TU5": 165}


def flat_tile(nrows: int, ncols: int, cell_m: float, code: float) -> TerrainTile:
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    return TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=float(code)),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0), source="orient")


def const_wx(loc: LatLon, wind_mps: float, wdir: float, rh: float = 15.0,
             temp: float = 30.0, hours: int = 48) -> WeatherForecast:
    return WeatherForecast(loc=loc, source="orient", samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind_mps,
                      wind_gust_mps=wind_mps * 1.3, wind_dir_deg=wdir,
                      rh_pct=rh, temp_c=temp) for i in range(hours)])


def engine_params(code: int, wind_mps: float, wdir: float, cfg: ResponseConfig,
                  rh: float = 15.0, temp: float = 30.0) -> tuple:
    """(head_ros m/s, lb, bearing deg) on FLAT ground, the operations of
    spread.compute_arrival's cell_params (memo path) for one cell."""
    fuel = FUEL_TABLE[code]
    m_dead = max(0.01, dead_fuel_moisture_frac(temp, rh))
    bed = rothermel.fuel_bed(fuel, m_dead, cfg.live_herb_frac,
                             cfg.live_woody_frac)
    u_wind = rothermel.midflame_wind_mps(wind_mps, fuel)
    to = math.radians((wdir + 180.0) % 360.0)
    vx, vy = u_wind * math.sin(to), u_wind * math.cos(to)
    u_eff = math.hypot(vx, vy)
    brg = math.degrees(math.atan2(vx, vy)) % 360.0 if u_eff > 1e-12 else 0.0
    res = rothermel.surface_ros_from_bed(bed, u_eff, 0.0)
    lb = rothermel.ellipse_lb(res.effective_wind_mps)
    head = res.ros_mps
    ff = spread._ros_floor_frac(code, cfg)
    if ff > 0.0 and wind_mps > 0.0 and head < ff * wind_mps:
        head = ff * wind_mps
    return head, lb, brg


def ecc(lb: float) -> float:
    lb = max(1.0, lb)
    return math.sqrt(lb * lb - 1.0) / lb


def gauge_s(dx: float, dy: float, head: float, lb: float, brg: float) -> float:
    """Analytic travel time (s) from the ignition (rear focus) to offset
    (dx east, dy north) metres."""
    e = ecc(lb)
    hx, hy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
    d = math.hypot(dx, dy)
    return (d - e * (dx * hx + dy * hy)) / (head * (1.0 - e))


def analytic(head: float, lb: float, t_s: float) -> dict:
    """Head / back / half-width (m) and area (acres) of the analytic ellipse
    after t_s seconds."""
    e = ecc(lb)
    back = head * (1.0 - e) / (1.0 + e)
    a = 0.5 * (head + back)
    b = a / max(1.0, lb)
    return {"head_m": head * t_s, "back_m": back * t_s, "half_m": b * t_s,
            "acres": math.pi * a * b * t_s * t_s / ACRE_M2,
            "a_m": a * t_s, "b_m": b * t_s, "c_m": a * e * t_s}


def stencil_bearings(offsets=None) -> list:
    offs = spread._OFFSETS if offsets is None else offsets
    return sorted(o[3] for o in offs)


def polygon(head: float, lb: float, brg: float, t_s: float,
            bearings=None) -> dict:
    """Head / back / half-width / area of the LATTICE-REACHABLE polygon: the
    convex hull of the ray points R(theta_i - brg) t along each stencil
    bearing (all on the ellipse, hence in convex position)."""
    bearings = stencil_bearings() if bearings is None else sorted(bearings)
    pts = []
    for th in bearings:
        r = rothermel.directional_ros_mps(head, lb, th - brg) * t_s
        pts.append((r * math.sin(math.radians(th)), r * math.cos(math.radians(th))))
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += x1 * y2 - x2 * y1
    area = abs(area) * 0.5
    hx, hy = math.sin(math.radians(brg)), math.cos(math.radians(brg))

    def ray(ux, uy):
        best = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            # solve s*u = p1 + w*(p2 - p1)
            ex, ey = x2 - x1, y2 - y1
            den = ux * ey - uy * ex
            if abs(den) < 1e-15:
                continue
            s = (x1 * ey - y1 * ex) / den
            w = (x1 * uy - y1 * ux) / den
            if -1e-9 <= w <= 1.0 + 1e-9 and s > best:
                best = s
        return best

    half = max(abs(-x * hy + y * hx) for x, y in pts)
    return {"head_m": ray(hx, hy), "back_m": ray(-hx, -hy), "half_m": half,
            "acres": area / ACRE_M2}


def tile_for(head: float, lb: float, brg: float, t_s: float, cell_m: float,
             code: float, margin_m: float = 1500.0):
    """(tile, ignition LatLon, (r0, c0)) sized so the analytic ellipse after
    t_s fits with `margin_m` to spare on every side."""
    an = analytic(head, lb, t_s)
    a, b, c = an["a_m"], an["b_m"], an["c_m"]
    ux, uy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
    vx, vy = uy, -ux
    hx = math.sqrt((a * ux) ** 2 + (b * vx) ** 2)
    hy = math.sqrt((a * uy) ** 2 + (b * vy) ** 2)
    cx, cy = c * ux, c * uy                    # ellipse centre from ignition
    west = max(hx - cx, 0.0) + margin_m        # room needed west of ignition
    east = max(hx + cx, 0.0) + margin_m
    north = max(hy + cy, 0.0) + margin_m
    south = max(hy - cy, 0.0) + margin_m
    c0 = int(math.ceil(west / cell_m)) + 1
    r0 = int(math.ceil(north / cell_m)) + 1
    ncols = c0 + int(math.ceil(east / cell_m)) + 2
    nrows = r0 + int(math.ceil(south / cell_m)) + 2
    tile = flat_tile(nrows, ncols, cell_m, code)
    return tile, geo.rc_to_latlon(tile.spec, r0, c0), (r0, c0)


def measure(raster, rc0, brg: float, t_end_s: float) -> dict:
    """Burned acres / head / back / half-width (m) of the cells reached by
    t_end_s (cell centres, relative to the ignition cell centre)."""
    spec = raster.spec
    nc = spec.ncols
    cm = spec.cell_m
    hx, hy = math.sin(math.radians(brg)), math.cos(math.radians(brg))
    head = back = half = 0.0
    n = 0
    data = raster.data
    for i, v in enumerate(data):
        if v <= t_end_s:
            n += 1
            r, c = divmod(i, nc)
            dx = (c - rc0[1]) * cm
            dy = (rc0[0] - r) * cm
            al = dx * hx + dy * hy
            ac = abs(-dx * hy + dy * hx)
            if al > head:
                head = al
            if -al > back:
                back = -al
            if ac > half:
                half = ac
    return {"acres": n * cm * cm / ACRE_M2, "head_m": head, "back_m": back,
            "half_m": half, "cells": n}


def iou_vs_analytic(raster, rc0, head: float, lb: float, brg: float,
                    t_s: float) -> float:
    """IoU of the burned set at t_s with the set of cell centres inside the
    analytic ellipse (gauge <= t_s)."""
    spec = raster.spec
    nc = spec.ncols
    cm = spec.cell_m
    inter = union = 0
    t_end = T0 + t_s
    for i, v in enumerate(raster.data):
        r, c = divmod(i, nc)
        dx = (c - rc0[1]) * cm
        dy = (rc0[0] - r) * cm
        ina = (dx == 0 and dy == 0) or gauge_s(dx, dy, head, lb, brg) <= t_s
        inm = v <= t_end
        if ina or inm:
            union += 1
            if ina and inm:
                inter += 1
    return inter / union if union else 1.0


def run_case(code: int, wind_mph: float, wdir: float, cfg: ResponseConfig,
             hours: float = 6.0, cell_m: float = 100.0, want_iou: bool = False,
             **arrival_kw) -> dict:
    """One flat uniform fire: engine measurement + analytic + polygon."""
    wind = wind_mph * MPH
    head, lb, brg = engine_params(code, wind, wdir, cfg)
    t_s = hours * 3600.0
    tile, ign, rc0 = tile_for(head, lb, brg, t_s, cell_m, code)
    wx = const_wx(ign, wind, wdir)
    run_cfg = cfg
    if cfg.horizon_h < hours + 1.0:
        import dataclasses
        run_cfg = dataclasses.replace(cfg, horizon_h=hours + 1.0)
    tic = time.perf_counter()
    r = spread.compute_arrival(tile, [ign], wx, T0, run_cfg, **arrival_kw)
    dt = time.perf_counter() - tic
    m = measure(r, rc0, brg, T0 + t_s)
    out = {"code": code, "mph": wind_mph, "wdir": wdir, "head_mps": head,
           "lb": lb, "brg": brg, "meas": m, "ana": analytic(head, lb, t_s),
           "poly": polygon(head, lb, brg, t_s), "solve_s": dt,
           "nrows": tile.spec.nrows, "ncols": tile.spec.ncols}
    if want_iou:
        out["iou"] = iou_vs_analytic(r, rc0, head, lb, brg, t_s)
    out["raster"] = r
    out["rc0"] = rc0
    return out
