"""scratch_review/wind_direction_probe.py — does the simulator's wind DIRECTION
change how the fire propagates? (founder observation 2026-09-25: "changing the
wind direction does not seem to change how the fire propagates").

Controlled experiment on the REAL cached LANDFIRE tiles through the REAL
simulator seams (no forked physics):

  one tile, one ignition (a burnable cell ~5 km from the location center),
  one intensity preset, wind FROM 0 / 90 / 180 / 270, three paths —

  ignite   SimSession.ignite(..., wind_dir_deg=w) then tick (the ignition-time
           path: the field is anchored at ignition under wind w)
  midrun   ignite under the location's default hazard wind, tick to
           --switch-h, SimSession.set_wind(w), tick (the engine's mid-run path:
           the fire re-anchors from its burned perimeter under wind w)
  api      the PUBLIC page's path, in-process through server.SimSessionManager
           exactly as console/simulator.html drives it: POST /api/sim/new
           (spotting + grass floor ON, the page defaults), /ignite with the
           dial value, 'step' ticks, then the dial's mid-run command
           (/config {wind_dir_deg: w}) and more 'step' ticks. A rejected wind
           command is reported (and the fire keeps the old wind).

For every (path, wind, T) the NOMINAL member (members[0] = sim ground truth,
the burned mask the engine re-seeds from) is summarised at T hours after
ignition: burned-cell count, burn-centroid displacement from the ignition
(distance + compass bearing, vs the expected DOWNWIND bearing w+180), the
length/breadth of the burn measured along vs across the wind axis, head/back
runs, a tile-edge flag, and the ensemble P50 arrival (hours after ignition)
at four probe points 3 km N/E/S/W of the ignition.

UI section: the page draws state.burned (rings of the ensemble P50 <= now)
and state.fire_bands h1/h5/h24 (P50 <= now+1/5/24 h), all traced on the
2x MIN-POOLED P50 raster (simui._pool_min). For each wind the probe computes
those four pooled masks at T and the IoU of every wind's mask against the
wind-0 run: an IoU near 1 means that layer looks the same whatever the dial
says.

--lb prints the elongation table: engine length/breadth (Anderson 1983 on the
midflame effective wind, capped at 8) per fuel model per intensity preset on
flat ground, the realised burn L/B on a flat uniform tile, and the literature
comparators (Alexander 1985 grass: LB = 1 + 8.729(1 - e^-0.030U)^2.155, U =
10-m open wind km/h; the brief's rough 1 + 0.25 U mph).

Run from the repo root (py -3 on Windows):
    py -3 scratch_review/wind_direction_probe.py
    py -3 scratch_review/wind_direction_probe.py --tile vail_co --intensity extreme
    py -3 scratch_review/wind_direction_probe.py --paths api
    py -3 scratch_review/wind_direction_probe.py --lb
Deterministic (fixed seeds, fixed sim epoch, no wall clock in results).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, rothermel, spread  # noqa: E402
from wgresponse.contracts import (GridSpec, LatLon, Raster,  # noqa: E402
                                  ResponseConfig, TerrainTile, WeatherForecast,
                                  WeatherSample)
from wgresponse.fuels import FUEL_TABLE, is_burnable  # noqa: E402
from wgresponse.simui import (GUST_FACTOR, INTENSITY_WEATHER, LOCATIONS,  # noqa: E402
                              MPH_TO_MPS, SimSession, _pool_min)

#: The physics the PUBLIC page runs with (server.new_session defaults:
#: spotting + grass_realism both ON). Dispatch knobs do not move the fire.
PUBLIC_PHYSICS = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}
WINDS = (0.0, 90.0, 180.0, 270.0)
PROBE_KM = 3.0


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _xy_m(origin: LatLon, p: LatLon) -> tuple:
    """(east_m, north_m) of p relative to origin (local planar)."""
    return ((p.lon - origin.lon) * geo.m_per_deg_lon(origin.lat),
            (p.lat - origin.lat) * geo.M_PER_DEG_LAT)


def _ang_diff(a: float, b: float) -> float:
    """Signed smallest angle a - b in (-180, 180]."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def find_ignition(tile, center: LatLon, bearing: float, dist_m: float) -> LatLon:
    """Burnable cell near center + (bearing, dist_m): the server scenario
    search (bearing jitter x range shrink), deterministic."""
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.95, 0.9, 1.05, 0.85, 1.1, 0.8):
            p = geo.destination(center, bearing + db, dist_m * shrink)
            rc = geo.latlon_to_rc(tile.spec, p)
            if rc is None:
                continue
            if is_burnable(int(round(tile.fuel.get(*rc)))):
                # snap to the cell center so geometry is exact
                return geo.rc_to_latlon(tile.spec, *rc)
    raise SystemExit("no burnable ignition cell found")


def burn_metrics(field, spec, ign: LatLon, t_abs: float, wind_from: float) -> dict:
    """Shape summary of {cells with field <= t_abs}."""
    data = field.data if hasattr(field, "data") else field
    nr, nc = spec.nrows, spec.ncols
    to = math.radians((wind_from + 180.0) % 360.0)
    ux, uy = math.sin(to), math.cos(to)            # downwind unit (east, north)
    vx, vy = math.cos(to), -math.sin(to)           # cross-wind unit
    n = 0
    sx = sy = 0.0
    amin = bmin = math.inf
    amax = bmax = -math.inf
    edge = False
    for r in range(nr):
        base = r * nc
        for c in range(nc):
            if data[base + c] > t_abs:
                continue
            n += 1
            x, y = _xy_m(ign, geo.rc_to_latlon(spec, r, c))
            sx += x
            sy += y
            a = x * ux + y * uy
            b = x * vx + y * vy
            amin, amax = min(amin, a), max(amax, a)
            bmin, bmax = min(bmin, b), max(bmax, b)
            if r in (0, nr - 1) or c in (0, nc - 1):
                edge = True
    if n == 0:
        return {"n": 0}
    cx, cy = sx / n, sy / n
    dist = math.hypot(cx, cy)
    brg = math.degrees(math.atan2(cx, cy)) % 360.0 if dist > 1e-9 else float("nan")
    cell = spec.cell_m
    length = amax - amin + cell
    breadth = bmax - bmin + cell
    return {"n": n, "area_km2": n * cell * cell / 1e6,
            "cdist_km": dist / 1000.0, "cbrg": brg,
            "cerr": (_ang_diff(brg, (wind_from + 180.0) % 360.0)
                     if dist > 1e-9 else float("nan")),
            "lb": length / breadth, "head_km": amax / 1000.0,
            "back_km": -amin / 1000.0, "edge": edge}


def growth_metrics(field, spec, ign: LatLon, t_lo: float, t_hi: float,
                   wind_from: float, ref_xy: tuple) -> dict:
    """Where did the NEW growth go? Cells with t_lo < field <= t_hi (the burn
    added after a mid-run wind switch at t_lo), centroid displacement from
    ref_xy (the centroid of what had burned by the switch), bearing vs the
    new downwind bearing."""
    data = field.data if hasattr(field, "data") else field
    nr, nc = spec.nrows, spec.ncols
    n = 0
    sx = sy = 0.0
    for r in range(nr):
        base = r * nc
        for c in range(nc):
            v = data[base + c]
            if not (t_lo < v <= t_hi):
                continue
            x, y = _xy_m(ign, geo.rc_to_latlon(spec, r, c))
            n += 1
            sx += x
            sy += y
    if n == 0:
        return {"n": 0}
    dx, dy = sx / n - ref_xy[0], sy / n - ref_xy[1]
    dist = math.hypot(dx, dy)
    brg = math.degrees(math.atan2(dx, dy)) % 360.0 if dist > 1e-9 else float("nan")
    return {"n": n, "gdist_km": dist / 1000.0, "gbrg": brg,
            "gerr": _ang_diff(brg, (wind_from + 180.0) % 360.0)}


def mask_centroid(field, spec, ign: LatLon, t_abs: float) -> tuple:
    data = field.data if hasattr(field, "data") else field
    nr, nc = spec.nrows, spec.ncols
    n = 0
    sx = sy = 0.0
    for r in range(nr):
        base = r * nc
        for c in range(nc):
            if data[base + c] <= t_abs:
                x, y = _xy_m(ign, geo.rc_to_latlon(spec, r, c))
                n += 1
                sx += x
                sy += y
    return (sx / n, sy / n) if n else (0.0, 0.0)


def probe_p50(ens, ign: LatLon, t_ign: float) -> dict:
    """Ensemble P50 arrival (h after ignition) at PROBE_KM N/E/S/W."""
    out = {}
    for name, brg in (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)):
        p = geo.destination(ign, brg, PROBE_KM * 1000.0)
        v = geo.raster_sample(ens.p50, p)
        out[name] = (math.inf if v is None or math.isinf(v)
                     else (v - t_ign) / 3600.0)
    return out


def ui_masks(ens, t_abs: float) -> dict:
    """The four masks the page draws at sim-now = t_abs (pooled P50)."""
    spec2, vals = _pool_min(ens.p50)
    out = {}
    for key, lead_h in (("burned", 0.0), ("h1", 1.0), ("h5", 5.0), ("h24", 24.0)):
        thr = t_abs + lead_h * 3600.0
        out[key] = frozenset(i for i, v in enumerate(vals) if v <= thr)
    return out


def iou(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))


# ---------------------------------------------------------------------------
# the three paths
# ---------------------------------------------------------------------------

def _session(tile_key, seed):
    return SimSession(tile_key, 1, 1, seed=seed,
                      cfg_overrides=dict(PUBLIC_PHYSICS), roads_module=None)


def run_ignite(tile_key, ign, intensity, w):
    s = _session(tile_key, "wind-probe")
    fid = s.ignite(ign.lat, ign.lon, intensity=intensity, wind_dir_deg=w)
    s.tick()
    f = s._fires[fid]
    return f.ens, f.t_ignite_s, s._tile.spec, {"state_wind": s.state()["wind_dir_deg"]}


def run_midrun(tile_key, ign, intensity, w, switch_h):
    s = _session(tile_key, "wind-probe")
    fid = s.ignite(ign.lat, ign.lon, intensity=intensity)   # default hazard wind
    f = s._fires[fid]
    while s._now_s - f.t_ignite_s < switch_h * 3600.0:
        s.tick()
    ens_before = f.ens
    t_switch = s._now_s
    s.set_wind(w)
    s.tick()                                                # re-anchor tick
    info = {"t_switch_h": (t_switch - f.t_ignite_s) / 3600.0,
            "reanchored": f.ens is not ens_before,
            "anchor_h": (f.anchor_t0_s - f.t_ignite_s) / 3600.0,
            "state_wind": s.state()["wind_dir_deg"]}
    # re-seed fidelity: what burned by t_switch under the OLD field vs what
    # the re-anchored field says had burned by then
    old = ens_before.members[0].data
    new = f.ens.members[0].data
    old_b = {i for i, v in enumerate(old) if v <= t_switch}
    new_b = {i for i, v in enumerate(new) if v <= t_switch}
    info["reseed_lost"] = len(old_b - new_b)
    info["reseed_added"] = len(new_b - old_b)
    info["burned_at_switch"] = len(old_b)
    info["_t_switch"] = t_switch
    info["_ref_xy"] = mask_centroid(ens_before.members[0], s._tile.spec, ign,
                                    t_switch)
    info["_truth"] = continuous_shift_truth(s, f, intensity, w, t_switch)
    return f.ens, f.t_ignite_s, s._tile.spec, info


def continuous_shift_truth(s, f, intensity, w, t_switch):
    """The yardstick for a mid-run wind change: ONE continuous nominal run
    from the ignition under the same scripted weather with the wind shifting
    to w at the switch hour (MockWeather shifts), priced with piecewise edge
    weather so an edge already crossing the shift continues at the new rate
    (without it the old burn's flanks never feel the new wind)."""
    import dataclasses
    from wgresponse.simui import T0_S
    from wgresponse.weather import MockWeather
    p = INTENSITY_WEATHER[intensity]
    u = p["wind_mph"] * MPH_TO_MPS
    hz = LOCATIONS[s._loc.key].hazard_wind_dir_deg
    shift_h = int(round((t_switch - T0_S) / 3600.0))
    wx = MockWeather(seed=f"simui|{s._seed}|wx", base_wind_mps=u, dir_deg=hz,
                     rh_pct=p["rh_pct"], temp_c=p["temp_c"],
                     gust_factor=GUST_FACTOR, t0_s=T0_S,
                     shifts=[(shift_h, u, w, p["rh_pct"])]).forecast(
        s._loc.center, s._cfg.horizon_h)
    cfg = dataclasses.replace(s._cfg, edge_weather_piecewise=True)
    return spread.compute_arrival(s._tile, [f.origin], wx, f.t_ignite_s, cfg,
                                  incident_id=f.incident_id, member_k=0)


def run_api(tile_key, ign, intensity, w, switch_h):
    """The public page's command sequence, in-process (no HTTP)."""
    from wgresponse.server import SimSessionManager
    mgr = SimSessionManager()
    # page newSession(): location/clients/crews/fire_zone/tiers + physics
    code, out = mgr.new_session({"location": tile_key, "clients": 1, "crews": 1,
                                 "fire_zone": "standard", "crews_1h": 0,
                                 "crews_3h": 0, "spotting": True,
                                 "grass_realism": True, "seed": "wind-probe"})
    assert code == 200, out
    sid = out["session_id"]
    entry = mgr._entry(sid)
    sess = entry.session
    hz = LOCATIONS[tile_key].hazard_wind_dir_deg
    # page igniteAt(): the dial value rides the ignite call (dial = hazard)
    code, out = mgr.ignite(sid, {"lat": ign.lat, "lon": ign.lon,
                                 "intensity": intensity, "wind_dir_deg": hz})
    assert code == 200, out
    fid = out["incident_id"]
    f = sess._fires[fid]
    while sess._now_s - f.t_ignite_s < switch_h * 3600.0:
        code, out = mgr.ctl(sid, {"action": "step"})
        assert code == 200, out
    ens_before = f.ens
    t_switch = sess._now_s
    # the dial moved mid-run: whatever command the page sends for it
    code, out = mgr.config(sid, {"wind_dir_deg": w})
    cmd = "config wind -> %d%s" % (code, "" if code == 200
                                   else " (" + str(out.get("error"))[:60] + ")")
    code2, out2 = mgr.ctl(sid, {"action": "step"})
    assert code2 == 200, out2
    mgr.stop_all()
    return f.ens, f.t_ignite_s, sess._tile.spec, {
        "cmd": cmd, "state_wind": out2["state"]["wind_dir_deg"],
        "reanchored": f.ens is not ens_before, "_t_switch": t_switch,
        "_ref_xy": mask_centroid(ens_before.members[0], sess._tile.spec, ign,
                                 t_switch)}


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _fmt_h(v):
    return "  -- " if math.isinf(v) else f"{v:5.2f}"


def experiment(tile_key, intensity, paths, times_h, switch_h, dist_km, bearing):
    loc = LOCATIONS[tile_key]
    probe = _session(tile_key, "wind-probe")
    tile = probe._tile
    ign_brg = loc.hazard_wind_dir_deg if bearing is None else bearing
    ign = find_ignition(tile, loc.center, ign_brg, dist_km * 1000.0)
    rc = geo.latlon_to_rc(tile.spec, ign)
    code = int(round(tile.fuel.get(*rc)))
    print(f"\n=== {tile_key} | intensity={intensity} "
          f"({INTENSITY_WEATHER[intensity]['wind_mph']:.0f} mph) | ignition "
          f"{ign.lat:.5f},{ign.lon:.5f} cell {rc} FBFM40 {code} "
          f"({geo.haversine_m(loc.center, ign) / 1000:.1f} km @ "
          f"{geo.bearing_deg(loc.center, ign):.0f} deg from center) | "
          f"hazard wind {loc.hazard_wind_dir_deg:.0f} | tile "
          f"{tile.spec.nrows}x{tile.spec.ncols} @ {tile.spec.cell_m:.0f} m ===")
    results = {}
    for path in paths:
        for w in WINDS:
            t = time.time()
            if path == "ignite":
                ens, t_ign, spec, info = run_ignite(tile_key, ign, intensity, w)
            elif path == "midrun":
                ens, t_ign, spec, info = run_midrun(tile_key, ign, intensity, w,
                                                    switch_h)
            else:
                ens, t_ign, spec, info = run_api(tile_key, ign, intensity, w,
                                                 switch_h)
            info["secs"] = time.time() - t
            results[(path, w)] = (ens, t_ign, spec, info)

    hdr = (f"{'path':7s} {'FROM':>4s} {'T h':>4s} {'cells':>6s} {'km2':>6s} "
           f"{'ctr km':>6s} {'ctr brg':>7s} {'dnwind':>6s} {'err':>6s} "
           f"{'L/B':>5s} {'head':>5s} {'back':>5s} {'edge':>4s} | "
           f"P50 h @3km  N     E     S     W")
    for T in times_h:
        print(f"\n-- nominal member (ground truth) at T = {T:g} h after ignition --")
        print(hdr)
        for path in paths:
            for w in WINDS:
                ens, t_ign, spec, info = results[(path, w)]
                m = burn_metrics(ens.members[0], spec, ign, t_ign + T * 3600.0, w)
                pr = probe_p50(ens, ign, t_ign)
                if m["n"] == 0:
                    print(f"{path:7s} {w:4.0f} {T:4g}  (nothing burned)")
                    continue
                print(f"{path:7s} {w:4.0f} {T:4g} {m['n']:6d} {m['area_km2']:6.1f} "
                      f"{m['cdist_km']:6.2f} {m['cbrg']:7.1f} "
                      f"{(w + 180) % 360:6.0f} {m['cerr']:6.1f} {m['lb']:5.2f} "
                      f"{m['head_km']:5.2f} {m['back_km']:5.2f} "
                      f"{'Y' if m['edge'] else '.':>4s} |            "
                      f"{_fmt_h(pr['N'])} {_fmt_h(pr['E'])} {_fmt_h(pr['S'])} "
                      f"{_fmt_h(pr['W'])}")
                # the SAME summary for the ensemble P50 field — the layer the
                # page paints as the fire (state.burned = P50 <= now)
                u = burn_metrics(ens.p50, spec, ign, t_ign + T * 3600.0, w)
                if u["n"]:
                    print(f"{'  p50':7s} {'':4s} {'':4s} {u['n']:6d} "
                          f"{u['area_km2']:6.1f} {u['cdist_km']:6.2f} "
                          f"{u['cbrg']:7.1f} {'':6s} {u['cerr']:6.1f} "
                          f"{u['lb']:5.2f} {u['head_km']:5.2f} "
                          f"{u['back_km']:5.2f} {'Y' if u['edge'] else '.':>4s} |"
                          f"  (UI burned fill)")
    mid_paths = [p for p in paths if p != "ignite"]
    if mid_paths:
        print("\n-- growth AFTER the mid-run switch (cells t_switch < arrival <= T): "
              "centroid shift from the burned-at-switch centroid --")
        print(f"{'path':7s} {'FROM':>4s} {'T h':>4s} {'new cells':>9s} "
              f"{'shift km':>8s} {'brg':>6s} {'dnwind':>6s} {'err':>6s} "
              f"{'burned vs continuous-shift run':>30s}")
        for T in times_h:
            for path in mid_paths:
                for w in WINDS:
                    ens, t_ign, spec, info = results[(path, w)]
                    g = growth_metrics(ens.members[0], spec, ign, info["_t_switch"],
                                       t_ign + T * 3600.0, w, info["_ref_xy"])
                    if g["n"] == 0:
                        print(f"{path:7s} {w:4.0f} {T:4g}  (no growth)")
                        continue
                    tr = ""
                    if "_truth" in info:
                        t_abs = t_ign + T * 3600.0
                        a = {i for i, v in enumerate(ens.members[0].data)
                             if v <= t_abs}
                        b = {i for i, v in enumerate(info["_truth"].data)
                             if v <= t_abs}
                        tr = (f"{len(a):6d} vs {len(b):6d} cells "
                              f"IoU {iou(frozenset(a), frozenset(b)):.2f}")
                    print(f"{path:7s} {w:4.0f} {T:4g} {g['n']:9d} "
                          f"{g['gdist_km']:8.2f} {g['gbrg']:6.1f} "
                          f"{(w + 180) % 360:6.0f} {g['gerr']:6.1f} {tr:>30s}")
    print("\n-- path notes --")
    for path in paths:
        for w in WINDS:
            info = results[(path, w)][3]
            extras = " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                              for k, v in info.items() if not k.startswith("_"))
            print(f"{path:7s} {w:4.0f}  {extras}")

    # UI layers: pooled P50 masks per wind, IoU vs the FROM-0 run
    print("\n-- what the page draws (state.burned = pooled P50 <= now; bands h1/h5/h24 "
          "= pooled P50 <= now+1/5/24 h) --")
    for path in paths:
        for T in times_h:
            ref = None
            rows = []
            for w in WINDS:
                ens, t_ign, spec, info = results[(path, w)]
                masks = ui_masks(ens, t_ign + T * 3600.0)
                nom_n = sum(1 for v in ens.members[0].data if v <= t_ign + T * 3600.0)
                if ref is None:
                    ref = masks
                rows.append((w, masks, nom_n))
            print(f"{path:7s} T={T:g} h   layer cells (pooled 2x) and IoU vs FROM-0:")
            for w, masks, nom_n in rows:
                cells = " ".join(f"{k}={len(masks[k]):5d}/{iou(masks[k], ref[k]):4.2f}"
                                 for k in ("burned", "h1", "h5", "h24"))
                print(f"          FROM {w:3.0f}: nominal={nom_n:5d}  {cells}")
    return results


# ---------------------------------------------------------------------------
# elongation table (the physics question)
# ---------------------------------------------------------------------------

def alexander_1985_lb(u10_kmh: float) -> float:
    return 1.0 + 8.729 * (1.0 - math.exp(-0.030 * u10_kmh)) ** 2.155


def _flat_tile(code, cell_m=90.0, n=161, lat=40.0, lon=-111.0):
    spec = GridSpec(north_lat=lat, west_lon=lon, cell_m=cell_m, nrows=n, ncols=n)
    k = n * n
    return TerrainTile(spec=spec, fuel=Raster(spec, [float(code)] * k),
                       slope_deg=Raster(spec, [0.0] * k),
                       aspect_deg=Raster(spec, [-1.0] * k),
                       elev_m=Raster(spec, [100.0] * k), source="probe-flat")


def lb_table(codes):
    cfg = ResponseConfig()
    for k, v in PUBLIC_PHYSICS.items():
        setattr(cfg, k, v)
    cfg.spotting_enabled = False          # shape of the surface ellipse only
    herb, woody = cfg.live_herb_frac, cfg.live_woody_frac
    print("\n=== elongation: engine ellipse vs literature (flat ground) ===")
    print(f"{'fuel':>5s} {'preset':>9s} {'U10mph':>6s} {'midfl':>5s} "
          f"{'LBeng':>5s} {'head m/s':>8s} {'flank':>6s} {'back':>6s} "
          f"{'realised L/B':>12s} {'Alex85':>6s} {'1+.25U':>6s}")
    for code in codes:
        fm = FUEL_TABLE.get(code)
        if fm is None:
            continue
        tile = _flat_tile(code)
        spec = tile.spec
        ign = geo.rc_to_latlon(spec, spec.nrows // 2, 20)   # west edge, room east
        for name in ("low", "moderate", "high", "extreme"):
            p = INTENSITY_WEATHER[name]
            u10 = p["wind_mph"] * MPH_TO_MPS
            m_dead = max(0.01, __import__("wgresponse.fuels", fromlist=["x"])
                         .dead_fuel_moisture_frac(p["temp_c"], p["rh_pct"]))
            bed = rothermel.fuel_bed(fm, m_dead, herb, woody)
            u_mid = rothermel.midflame_wind_mps(u10, fm)
            res = rothermel.surface_ros_from_bed(bed, u_mid, 0.0)
            lb = rothermel.ellipse_lb(res.effective_wind_mps)
            head = res.ros_mps
            ff = spread._ros_floor_frac(code, cfg)
            if ff > 0:
                head = max(head, ff * u10)
            flank = rothermel.directional_ros_mps(head, lb, 90.0)
            back = rothermel.directional_ros_mps(head, lb, 180.0)
            # realised: run the real engine on a flat uniform tile, wind FROM
            # 270 (head runs east); measure L/B when the head has run ~6 km
            wx = WeatherForecast(loc=ign, samples=[WeatherSample(
                t_s=1.79e9 + h * 3600.0, wind_speed_mps=u10,
                wind_gust_mps=u10 * GUST_FACTOR, wind_dir_deg=270.0,
                rh_pct=p["rh_pct"], temp_c=p["temp_c"]) for h in range(97)])
            cfg.horizon_h = 96.0
            fld = spread.compute_arrival(tile, [ign], wx, 1.79e9, cfg)
            # time the head needs to reach 6 km east of the ignition
            tgt = geo.destination(ign, 90.0, 6000.0)
            t6 = geo.raster_sample(fld, tgt)
            real = "   n/a"
            if t6 is not None and math.isfinite(t6):
                m = burn_metrics(fld, spec, ign, t6, 270.0)
                real = f"{m['lb']:5.2f}@{(t6 - 1.79e9) / 3600:4.1f}h"
            print(f"{code:5d} {name:>9s} {p['wind_mph']:6.0f} "
                  f"{u_mid * 2.23694:5.1f} {lb:5.2f} {head:8.3f} {flank:6.3f} "
                  f"{back:6.3f} {real:>12s} "
                  f"{alexander_1985_lb(p['wind_mph'] * 1.609344):6.2f} "
                  f"{1 + 0.25 * p['wind_mph']:6.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tile", default="park_city_ut")
    ap.add_argument("--intensity", default="high",
                    choices=sorted(INTENSITY_WEATHER))
    ap.add_argument("--paths", default="ignite,midrun,api")
    ap.add_argument("--times", default="3,6")
    ap.add_argument("--switch-h", type=float, default=1.0)
    ap.add_argument("--dist-km", type=float, default=5.0)
    ap.add_argument("--bearing", type=float, default=None,
                    help="ignition bearing from center (default: hazard wind)")
    ap.add_argument("--lb", action="store_true", help="elongation table only")
    a = ap.parse_args()
    if a.lb:
        lb_table((102, 121, 122, 147, 161, 165, 183, 188))
        return
    experiment(a.tile, a.intensity, [p for p in a.paths.split(",") if p],
               [float(t) for t in a.times.split(",")], a.switch_h, a.dist_km,
               a.bearing)


if __name__ == "__main__":
    main()
