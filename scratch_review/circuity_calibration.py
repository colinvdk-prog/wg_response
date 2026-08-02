"""circuity_calibration — measure road-network circuity per sim tile, properly.

Calibration review 2026-07-31 flagged cfg.circuity=1.35 as CONTRADICTED by the
repo's own measurement (DISPATCH_REVIEW: 1.96-2.43); the error is absorbed by
route_margin_min=60 on chord legs. This script is the calibration measurement
of record for the (circuity, route_margin_min) pair:

  * ALL SIX tiles (dr_exp2 predates rancho_santa_fe_ca) x TWO populations:
      uniform  random on-tile point pairs (uniform over the tile bbox — the
               review's reproduction recipe; includes non-WUI terrain)
      wui      planner-representative legs (60 seeded WUI clients + the
               location's safe zones — the exact population dispatch prices
               as chords; dr_exp2's population at 2x the client draw)
  * circuity = roads.route(g,a,b,cfg).dist_m / haversine_m(a,b), on-network
    routes only (on_roads=False fallbacks counted + excluded: a whole-leg
    chord has circuity 1.0 by construction and would dilute the geometry
    signal). dist_m includes the short snap spurs a real crew also drives.
  * hav >= 400 m floor (dr_exp2: sub-cell neighbors are snap noise).
  * spur cap: default cfg (road_spur_max_m=2000) for the primary tables; the
    wui population is re-run at the simulator's 8000 m cap as a sensitivity
    row (long straight spurs dilute measured circuity — the primary table is
    the honest geometry measurement).

Output: per-map median/p90/mean + pooled (equal n per map => equal-weight
pooling), the representative chord-leg length L_rep (pooled wui median), and
the derived calibrated pair (see PAIR ARITHMETIC at the bottom of main()).
Deterministic: seeded_rng only, no wall-clock; run under PYTHONHASHSEED=0.
Writes scratch_review/circuity_calibration.json (per-pair (hav, dist) rows so
the stats are re-derivable without re-routing).
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import geo, roads  # noqa: E402
from wgresponse.contracts import ResponseConfig, seeded_rng  # noqa: E402
from wgresponse.geo import M_PER_DEG_LAT, m_per_deg_lon  # noqa: E402
from wgresponse.simui import LOCATIONS, load_tile, place_clients  # noqa: E402

N_PAIRS = 300          # accepted pairs per (map, population)
N_CLIENTS = 60         # wui population client draw (dr_exp2 used 30)
HAV_FLOOR_M = 400.0    # sub-cell neighbors: snap noise dominates (dr_exp2)
MAX_ATTEMPT_FACTOR = 40


def _frac_rc_to_latlon(spec, rf: float, cf: float):
    """LatLon at FRACTIONAL grid coords (rf, cf) in [0, nrows] x [0, ncols] —
    geo.rc_to_latlon's formulas without the +0.5 cell-center shift, so a
    uniform draw over the index box is uniform over the tile."""
    lat = spec.north_lat - rf * spec.cell_m / M_PER_DEG_LAT
    lon = spec.west_lon + cf * spec.cell_m / m_per_deg_lon(lat)
    return geo.LatLon(lat, lon)


def _sample_points(key: str, population: str, tile):
    """Candidate point pool for one (map, population)."""
    if population == "uniform":
        return None  # drawn on the fly (continuous)
    loc = LOCATIONS[key]
    clients = place_clients(tile, N_CLIENTS, seed=f"circ_cal|{key}")
    return [c.loc for c in clients] + [z.loc for z in loc.safe_zones]


def measure(job):
    """(key, population, spur_cap) -> row dict with per-pair (hav, dist)."""
    key, population, spur_cap = job
    cfg = ResponseConfig()
    if spur_cap is not None:
        cfg.road_spur_max_m = spur_cap
    tile, synthetic = load_tile(key)
    assert not synthetic, f"{key}: real tile missing — fetch data/tiles first"
    g = roads.load_road_graph(key)
    assert g is not None, f"{key}: no cached road graph"
    pts = _sample_points(key, population, tile)
    rng = seeded_rng("circ_cal", key, population, spur_cap)
    spec = tile.spec
    pairs = []
    fallbacks = 0
    attempts = 0
    seen = set()
    while len(pairs) < N_PAIRS and attempts < N_PAIRS * MAX_ATTEMPT_FACTOR:
        attempts += 1
        if population == "uniform":
            a = _frac_rc_to_latlon(spec, rng.random() * spec.nrows,
                                   rng.random() * spec.ncols)
            b = _frac_rc_to_latlon(spec, rng.random() * spec.nrows,
                                   rng.random() * spec.ncols)
        else:
            i = rng.randrange(len(pts))
            j = rng.randrange(len(pts))
            if i == j or (i, j) in seen:
                continue
            seen.add((i, j))
            if len(seen) >= len(pts) * (len(pts) - 1):
                break
            a, b = pts[i], pts[j]
        hav = geo.haversine_m(a, b)
        if hav < HAV_FLOOR_M:
            continue
        rr = roads.route(g, a, b, cfg)
        if not rr.on_roads:
            fallbacks += 1
            continue
        pairs.append((round(hav, 1), round(rr.dist_m, 1)))
    return dict(key=key, population=population,
                spur_cap=spur_cap if spur_cap is not None
                else cfg.road_spur_max_m,
                n=len(pairs), fallbacks=fallbacks, attempts=attempts,
                pairs=pairs)


def pctl(v, q):
    """Linear-interpolation percentile, q in [0, 1]."""
    s = sorted(v)
    n = len(s)
    if n == 0:
        return float("nan")
    if n == 1:
        return s[0]
    x = q * (n - 1)
    i = int(x)
    f = x - i
    return s[i] if i + 1 >= n else s[i] * (1.0 - f) + s[i + 1] * f


def stats_row(pairs):
    circ = [d / h for h, d in pairs]
    hav_km = [h / 1000.0 for h, _d in pairs]
    return dict(median=pctl(circ, 0.5), p90=pctl(circ, 0.9),
                mean=st.mean(circ), med_leg_km=pctl(hav_km, 0.5),
                p90_leg_km=pctl(hav_km, 0.9))


def main():
    keys = sorted(LOCATIONS)
    jobs = ([(k, "uniform", None) for k in keys]
            + [(k, "wui", None) for k in keys]
            + [(k, "wui", 8000.0) for k in keys])   # sim spur-cap sensitivity
    with ProcessPoolExecutor(max_workers=min(10, len(jobs))) as ex:
        rows = list(ex.map(measure, jobs))
    by = {(r["key"], r["population"], r["spur_cap"]): r for r in rows}

    cfg = ResponseConfig()
    print(f"model constant circuity={cfg.circuity}  "
          f"(fire_traffic={cfg.fire_traffic_factor}, "
          f"road_speed={cfg.road_speed_mps} m/s)\n")
    pooled = {}
    for population, cap in (("uniform", None), ("wui", None), ("wui", 8000.0)):
        cap_v = cap if cap is not None else cfg.road_spur_max_m
        label = f"{population} @ spur {cap_v:.0f} m"
        print(f"--- {label} ---")
        print(f"{'map':<20}{'n':>5}{'fb':>5}{'med leg km':>11}"
              f"{'circ med':>9}{'p90':>7}{'mean':>7}")
        allp = []
        for k in keys:
            r = by[(k, population, cap_v)]
            s = stats_row(r["pairs"])
            allp.extend(r["pairs"])
            print(f"{k:<20}{r['n']:>5}{r['fallbacks']:>5}"
                  f"{s['med_leg_km']:>11.1f}{s['median']:>9.2f}"
                  f"{s['p90']:>7.2f}{s['mean']:>7.2f}")
        ps = stats_row(allp)
        pooled[(population, cap_v)] = ps
        print(f"{'POOLED':<20}{len(allp):>5}{'':>5}{ps['med_leg_km']:>11.1f}"
              f"{ps['median']:>9.2f}{ps['p90']:>7.2f}{ps['mean']:>7.2f}\n")

    # ---- PAIR ARITHMETIC (the calibrated pair moves together) ----
    # Chord-leg total conservatism at the representative leg:
    #   t_model(L) + margin,  t_model(L) = L * circuity * ftf / v
    # Raising circuity C_old -> C_new adds dt(L) = L*(C_new-C_old)*ftf/v to
    # every chord pass/arrival time; reducing the margin by dt(L_rep) keeps
    # the end-of-leg total exactly where the old pair put it at L_rep.
    uni = pooled[("uniform", cfg.road_spur_max_m)]
    wui = pooled[("wui", cfg.road_spur_max_m)]
    c_new = round((uni["median"] + wui["median"]) / 2.0, 1)
    l_rep = wui["med_leg_km"] * 1000.0
    dt_min = (l_rep * (c_new - cfg.circuity) * cfg.fire_traffic_factor
              / cfg.road_speed_mps / 60.0)
    print("--- calibrated pair derivation ---")
    print(f"measured medians: uniform {uni['median']:.2f}, "
          f"planner-representative {wui['median']:.2f} "
          f"-> C_new = {c_new}")
    print(f"L_rep (pooled wui median leg) = {l_rep / 1000.0:.1f} km")
    print(f"dt(L_rep) = {l_rep:.0f} * ({c_new} - {cfg.circuity}) * "
          f"{cfg.fire_traffic_factor} / {cfg.road_speed_mps} = "
          f"{dt_min:.1f} min")
    print(f"route_margin_min: 60 - {dt_min:.1f} ~= {60.0 - dt_min:.1f} "
          f"(round to preserve-or-exceed old total conservatism)")
    p90_leg = wui["p90_leg_km"] * 1000.0
    dt90 = (p90_leg * (c_new - cfg.circuity) * cfg.fire_traffic_factor
            / cfg.road_speed_mps / 60.0)
    print(f"sensitivity: at p90 leg {p90_leg / 1000.0:.1f} km, "
          f"dt = {dt90:.1f} min (longer legs shed MORE margin than the pair "
          f"removes -> net more conservative there)")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "circuity_calibration.json")
    json.dump(dict(n_pairs=N_PAIRS, n_clients=N_CLIENTS,
                   hav_floor_m=HAV_FLOOR_M,
                   pooled={f"{p}@{c:.0f}": s for (p, c), s in pooled.items()},
                   rows=rows),
              open(out, "w"), indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
