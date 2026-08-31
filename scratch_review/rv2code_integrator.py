"""RV2-CODE: a FAITHFUL re-implementation of spread.compute_arrival's
relaxation loop (no perimeter, no spotting, ignition-point seeds), with three
pluggable rules so one rule at a time can be flipped:

  edge_rule "source" -> SHIPPED: ROS from the SOURCE cell only
  edge_rule "mean"   -> docs/research/spread_model.md sec 3.3 spec:
                        t_AB = dist / mean(R_A(theta_AB), R_B(theta_AB))
  edge_rule "harm"   -> physically-exact 2-segment traversal (half the span in
                        each cell): t = (d/2)/R_A + (d/2)/R_B

  wx_rule   "frozen"    -> SHIPPED: weather hour bucketed at the SOURCE cell's
                           arrival, applied to the WHOLE edge
  wx_rule   "piecewise" -> advance the weather hour as the front crosses it

  phi_rule  "uspace" -> SHIPPED: wind & slope combined as EQUIVALENT WIND then
                        re-fed through phi_w
  phi_rule  "phivec" -> correct: combine the phi VECTORS (the convention
                        rothermel.effective_wind_from_bed already implements)

Fidelity gate: ("source", "frozen", "uspace") must be BYTE-IDENTICAL to
spread.compute_arrival. verify_identity() enforces it.
"""
from __future__ import annotations

import heapq
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, rothermel, spread
from wgresponse.contracts import Raster, UNREACHED
from wgresponse.fuels import dead_fuel_moisture_frac, is_burnable

_ROS_FLOOR_MPS = spread._ROS_FLOOR_MPS
_OFFSETS = spread._OFFSETS


def arrival(tile, ignition, wx, t0_s, cfg, edge_rule="source",
            wx_rule="frozen", phi_rule="uspace", wind_scale=1.0,
            moisture_scale=1.0, dir_offset_deg=0.0):
    spec = tile.spec
    nrows, ncols = spec.nrows, spec.ncols
    n = nrows * ncols
    cell_m = spec.cell_m

    fuel_of = [None] * n
    fdata = tile.fuel.data
    for i in range(n):
        code = int(round(fdata[i]))
        if is_burnable(code):
            fuel_of[i] = spread._fuel_for_code(code)

    best = [UNREACHED] * n
    heap = []
    for p in ignition:
        rc = geo.latlon_to_rc(spec, p)
        if rc is None:
            continue
        idx = rc[0] * ncols + rc[1]
        if t0_s < best[idx]:
            best[idx] = t0_s
            heapq.heappush(heap, (t0_s, idx))

    horizon_end_s = t0_s + cfg.horizon_h * 3600.0
    herb, woody = cfg.live_herb_frac, cfg.live_woody_frac
    slope_d, aspect_d = tile.slope_deg.data, tile.aspect_deg.data
    gust_blend = getattr(cfg, "wind_gust_blend", 0.0)
    hour_wx, bed_cache, ros_cache = {}, {}, {}
    _BED_MISS = spread._BED_MISS

    def cell_params(idx, hour):
        """(head_ros, lb, bearing) — verbatim spread.py cell_params + _pack_params
        (spot omitted: spotting is off in every experiment that uses this)."""
        fuel = fuel_of[idx]
        wt = hour_wx.get(hour)
        if wt is None:
            s = wx.at(t0_s + hour * 3600.0)
            ws_eff = (s.wind_speed_mps
                      + gust_blend * max(0.0, s.wind_gust_mps - s.wind_speed_mps))
            wt = (max(0.0, ws_eff * wind_scale),
                  max(0.01, dead_fuel_moisture_frac(s.temp_c, s.rh_pct) * moisture_scale),
                  s.wind_dir_deg + dir_offset_deg)
            hour_wx[hour] = wt
        wind10, m_dead, wdir = wt
        slope, aspect = slope_d[idx], aspect_d[idx]
        key = (fuel.code, slope, aspect, wind10, m_dead, wdir)
        p = ros_cache.get(key)
        if p is None:
            bkey = (fuel.code, m_dead)
            bed = bed_cache.get(bkey, _BED_MISS)
            if bed is _BED_MISS:
                bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
                bed_cache[bkey] = bed
            u_wind = rothermel.midflame_wind_mps(wind10, fuel)
            if phi_rule == "uspace":
                u_slope = rothermel.effective_wind_from_bed(bed, 0.0, slope)
                u_eff, brg = _msv(u_wind, u_slope, wdir, aspect)
                res = rothermel.surface_ros_from_bed(bed, u_eff, 0.0)
                ros, ueff_for_lb = res.ros_mps, res.effective_wind_mps
            else:
                ros, ueff_for_lb, brg = _phivec(bed, u_wind, wdir, slope, aspect)
            lb = rothermel.ellipse_lb(ueff_for_lb)
            ff = spread._ros_floor_frac(fuel.code, cfg)
            if ff > 0.0 and wind10 > 0.0:
                ros = max(ros, ff * wind10)
            p = (ros, lb, brg)
            ros_cache[key] = p
        return p

    def dros(p, ebrg):
        head, lb, brg = p
        return rothermel.directional_ros_mps(head, lb, ebrg - brg)

    while heap:
        t_u, idx = heapq.heappop(heap)
        if t_u > best[idx]:
            continue
        if fuel_of[idx] is None:
            continue
        r, c = divmod(idx, ncols)
        hour = int((t_u - t0_s) // 3600.0)
        for dr, dc, dist_cells, ebrg, gates in _OFFSETS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < nrows and 0 <= nc < ncols):
                continue
            nidx = nr * ncols + nc
            if fuel_of[nidx] is None or best[nidx] <= t_u:
                continue
            if gates:
                if not any(0 <= r + gr < nrows and 0 <= c + gc < ncols
                           and fuel_of[(r + gr) * ncols + (c + gc)] is not None
                           for gr, gc in gates):
                    continue
            span_m = dist_cells * cell_m

            def speed(hr):
                ra = dros(cell_params(idx, hr), ebrg)
                if edge_rule == "source":
                    return max(ra, _ROS_FLOOR_MPS)
                rb = dros(cell_params(nidx, hr), ebrg)
                if edge_rule == "mean":
                    return max(0.5 * (ra + rb), _ROS_FLOOR_MPS)
                # "harm": exact 2-segment traversal -> effective speed
                ra, rb = max(ra, _ROS_FLOOR_MPS), max(rb, _ROS_FLOOR_MPS)
                return 2.0 * ra * rb / (ra + rb)

            if wx_rule == "frozen":
                t_v = t_u + span_m / speed(hour)
            else:
                t_v, rem, guard = t_u, span_m, 0
                while rem > 0.0 and guard < 100000:
                    guard += 1
                    h = int((t_v - t0_s) // 3600.0)
                    v = speed(h)
                    dt_avail = (t0_s + (h + 1) * 3600.0) - t_v
                    d_avail = v * dt_avail
                    if d_avail >= rem:
                        t_v += rem / v
                        rem = 0.0
                    else:
                        t_v = t0_s + (h + 1) * 3600.0
                        rem -= d_avail
                    if t_v > horizon_end_s:
                        break
            if t_v < best[nidx] and t_v <= horizon_end_s:
                best[nidx] = t_v
                heapq.heappush(heap, (t_v, nidx))
    return Raster(spec, best)


def _msv(u_wind, u_slope, wdir, aspect):
    """spread.py _max_spread_vector, verbatim."""
    to_wind = math.radians((wdir + 180.0) % 360.0)
    to_upslope = math.radians((aspect + 180.0) % 360.0)
    vx = u_wind * math.sin(to_wind) + u_slope * math.sin(to_upslope)
    vy = u_wind * math.cos(to_wind) + u_slope * math.cos(to_upslope)
    u_eff = math.hypot(vx, vy)
    brg = math.degrees(math.atan2(vx, vy)) % 360.0 if u_eff > 1e-12 else 0.0
    return u_eff, brg


def _phivec(bed, u_wind_midflame, wdir, slope_deg, aspect_deg):
    """Correct combination: add the phi VECTORS, then R = R0*(1+|phi|)."""
    if bed is None:
        return 0.0, 0.0, 0.0
    phi_w = rothermel._phi_wind(bed, max(0.0, u_wind_midflame) * rothermel._MPS_TO_FTMIN)
    phi_s = rothermel._phi_slope(bed, slope_deg)
    to_wind = math.radians((wdir + 180.0) % 360.0)
    to_upslope = math.radians((aspect_deg + 180.0) % 360.0)
    px = phi_w * math.sin(to_wind) + phi_s * math.sin(to_upslope)
    py = phi_w * math.cos(to_wind) + phi_s * math.cos(to_upslope)
    phi_e = math.hypot(px, py)
    brg = math.degrees(math.atan2(px, py)) % 360.0 if phi_e > 1e-12 else 0.0
    r_ftmin = bed.r0_ftmin * (1.0 + phi_e)
    u_eff = rothermel._wind_from_phi(bed, phi_e) * rothermel._FTMIN_TO_MPS
    return r_ftmin * rothermel._FTMIN_TO_MPS, u_eff, brg


def verify_identity(tile, ignition, wx, t0_s, cfg, label=""):
    """The fidelity gate: shipped rules must reproduce spread.compute_arrival
    BYTE-for-BYTE (bit patterns, not approx)."""
    ref = spread.compute_arrival(tile, ignition, wx, t0_s, cfg)
    mine = arrival(tile, ignition, wx, t0_s, cfg, "source", "frozen", "uspace")
    bad = [i for i, (a, b) in enumerate(zip(ref.data, mine.data)) if a != b]
    ok = not bad
    print(f"  FIDELITY GATE {label}: {'PASS (byte-identical)' if ok else f'FAIL ({len(bad)} cells differ)'}")
    if bad:
        i = bad[0]
        print(f"    first diff cell {i}: ref={ref.data[i]!r} mine={mine.data[i]!r}")
    return ok
