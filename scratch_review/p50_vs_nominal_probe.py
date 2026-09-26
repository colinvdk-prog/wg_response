"""scratch_review/p50_vs_nominal_probe.py — why the simulator's pooled ensemble
P50 draws a much smaller fire than the NOMINAL member (sim ground truth).

Founder decision 2026-09-26 ("shade the fire"): the page's burned fill and
h1/h5/h24 bands were drawn from the pooled ensemble P50 (per-cell Hazen median
over the 6 members), while the simulated fire the crews are safety-audited
against is the nominal member (members[0]). In the extreme park_city_windstorm
scenario the fill showed ~0.6 km2 at T+1h45 against ~12 km2 actually burned.

This probe INVESTIGATES (no physics change):

  1. burned area by nominal / P50 / P10 / P90 and per member at T = 1, 1.75,
     2, 4, 8 h after ignition, for HIGH and EXTREME on park_city_ut (the
     park_city_windstorm preset ignition) and eagle_vail_co (a WSW fire 8 km
     up-valley of the tile centre), with the page's session physics
     (POST /api/sim/new defaults: ember spotting ON, grass floor 0.10);
  2. the arrival lag along the nominal head axis (P50 / nominal, P10 /
     nominal) at 0.5-6 km downwind;
  3. cause attribution — the same first field recomputed with one knob
     changed at a time (no direction perturbation; no spotting; direction-
     only; the existing default-OFF ensemble_p50_nominal_bound; the
     calibrated model_error_sd_log 0.71);
  4. the re-anchor sawtooth (a same-direction set_wind re-anchors the field
     from the nominal burned mask: P50 jumps to the true burn, then lags
     again);
  5. what the truth-bounded bands look like (band minus nominal burned) and
     how many "overdue" cells (P50 <= now, nominal not yet) land in the
     <1 h band.

Every field is the REAL engine's (spread.ensemble_arrival through
SimSession); the first field of a fire is anchored at its ignition time and
reused until a world change, so thresholding it at t_ign + T is exactly what
the page drew at that tick.

Run from the repo root (py -3 on Windows):
    py -3 scratch_review/p50_vs_nominal_probe.py            # all sections
    py -3 scratch_review/p50_vs_nominal_probe.py --quick    # no ablations
    py -3 scratch_review/p50_vs_nominal_probe.py --json out.json
Deterministic (fixed seeds, fixed sim epoch, no wall clock in results).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, simui, spread  # noqa: E402
from wgresponse.spread import _stratified_member_draws  # noqa: E402

HOUR = 3600.0
TIMES_H = (1.0, 1.75, 2.0, 4.0, 8.0)
AXIS_KM = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0)

# The page's session physics (server.SimManager.new defaults): spotting ON,
# grass head-ROS floor 0.10. Everything else is the SimSession default.
PAGE_CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}

SCENARIOS = {
    # server.SCENARIOS['park_city_windstorm']: bearing 225, 7 km, wind FROM 225
    "park_city_ut": {"bearing": 225.0, "dist_m": 7000.0},
    # an up-valley (WSW) fire running ENE at Edwards / Avon, wind FROM 240
    "eagle_vail_co": {"bearing": 240.0, "dist_m": 8000.0},
}


def make_session(key: str, intensity: str, cfg_extra=None):
    """A page-physics session (roads off: the fire field does not depend on
    them) ignited like the server's one-click preset: bearing jitter +
    range shrink until a burnable cell takes, wind FROM the bearing."""
    cfg = dict(PAGE_CFG)
    cfg.update(cfg_extra or {})
    s = simui.SimSession(key, 8, 1, seed=f"p50probe-{key}", cfg_overrides=cfg,
                         roads_module=None)
    sc = SCENARIOS[key]
    center = simui.LOCATIONS[key].center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, sc["bearing"] + db,
                                sc["dist_m"] * shrink)
            try:
                fid = s.ignite(p.lat, p.lon, intensity=intensity,
                               wind_dir_deg=sc["bearing"])
                return s, fid
            except ValueError:
                continue
    raise RuntimeError(f"{key}: no burnable ignition cell")


def count_le(values, t):
    return sum(1 for v in values if v <= t)


def pooled_count(raster, t):
    spec2, vals = simui._pool_min(raster)
    return sum(1 for v in vals if v <= t), spec2.cell_m


def member_perturbations(cfg, incident_id: str):
    """(w_scale, m_scale, dir_offset_deg, ros_scale) per member, as
    spread.ensemble_arrival applies them (member 0 = nominal)."""
    n = max(1, int(cfg.ensemble_n))
    out = [(1.0, 1.0, 0.0, 1.0)]
    draws = _stratified_member_draws(incident_id, n - 1)
    dir_lead = min(1.0, (cfg.horizon_h * 0.5) / 48.0)
    sd_log = float(cfg.model_error_sd_log or 0.0)
    from statistics import NormalDist
    for u_w, u_m, u_d, u_e in draws:
        w = max(0.05, 1.0 + u_w * cfg.wind_perturb_frac * 0.5)
        m = max(0.05, 1.0 + u_m * cfg.moisture_perturb_frac * 0.5)
        d = u_d * cfg.dir_perturb_deg * dir_lead
        r = (math.exp(sd_log * NormalDist().inv_cdf(0.5 * (u_e + 1.0)))
             if sd_log > 0 else 1.0)
        out.append((w, m, d, r))
    return out


def area_row(ens, t, cell_km2):
    nom = ens.members[0].data
    p50 = ens.p50.data
    p10 = ens.p10.data
    p90 = ens.p90.data
    n_nom = count_le(nom, t)
    n_p50 = count_le(p50, t)
    members = [count_le(m.data, t) for m in ens.members]
    inf_drag = sum(1 for a, b in zip(nom, p50) if a <= t and math.isinf(b))
    overdue = sum(1 for a, b in zip(nom, p50) if b <= t and a > t)
    behind = sum(1 for a, b in zip(nom, p50) if a <= t and b > t)
    p10_late = sum(1 for a, b in zip(nom, p10) if b > a)   # must be 0
    pooled_n, pooled_m = pooled_count(ens.p50, t)
    return {
        "nominal_km2": n_nom * cell_km2,
        "p50_km2": n_p50 * cell_km2,
        "p50_pooled_km2": pooled_n * (pooled_m / 1000.0) ** 2,
        "p10_km2": count_le(p10, t) * cell_km2,
        "p90_km2": count_le(p90, t) * cell_km2,
        "members_km2": [m * cell_km2 for m in members],
        "median_member_km2": statistics.median(members) * cell_km2,
        "p50_over_nominal": (n_p50 / n_nom) if n_nom else None,
        "nominal_cells": n_nom, "p50_cells": n_p50,
        "p50_inf_on_burned_cells": inf_drag,
        "overdue_cells": overdue,          # p50 <= t, nominal not yet
        "behind_cells": behind,            # nominal burned, p50 not yet
        "p10_later_than_nominal_cells": p10_late,
    }


def band_row(ens, t, cell_km2):
    """The truth-bounded bands the page now draws at sim-now = t: each band
    is {p50 <= t + h} MINUS the nominal burned mask (overdue cells land in
    <1 h), disjoint rings of the ladder median."""
    nom = ens.members[0].data
    p50 = ens.p50.data
    out = {}
    lo = -math.inf
    for key, h in (("h1", 1.0), ("h5", 5.0), ("h24", 24.0)):
        hi = t + h * HOUR
        n = sum(1 for a, b in zip(nom, p50) if a > t and lo < b <= hi)
        out[key + "_km2"] = n * cell_km2
        # raw (unclipped) P50 band area inside the nominal burn = the part the
        # old page painted OVER the actual fire
        n_in = sum(1 for a, b in zip(nom, p50) if a <= t and lo < b <= hi
                   and b > t)
        out[key + "_raw_inside_fire_km2"] = n_in * cell_km2
        lo = hi
    return out


def axis_lag(ens, s, origin, wind_from, t_ign):
    """Arrival (h after ignition) along the nominal head axis (downwind =
    wind_from + 180) for nominal / P50 / P10 and each member."""
    spec = s._tile.spec
    rows = []
    for km in AXIS_KM:
        p = geo.destination(origin, (wind_from + 180.0) % 360.0, km * 1000.0)
        rc = geo.latlon_to_rc(spec, p)
        if rc is None:
            continue
        i = rc[0] * spec.ncols + rc[1]

        def h(v):
            return None if math.isinf(v) else round((v - t_ign) / HOUR, 2)
        rows.append({
            "km": km, "nominal_h": h(ens.members[0].data[i]),
            "p50_h": h(ens.p50.data[i]), "p10_h": h(ens.p10.data[i]),
            "members_h": [h(m.data[i]) for m in ens.members],
        })
    return rows


def variant_ens(s, fid, extra):
    """The fire's FIRST field recomputed under a cfg variant (same tile,
    weather script, ignition point, anchor time and incident id)."""
    f = s._fires[fid]
    cfg = dataclasses.replace(s._cfg, **extra)
    return spread.ensemble_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                                   cfg, incident_id=fid, perimeter=None,
                                   perimeter_at_s=None)


ABLATIONS = (
    ("baseline (page physics)", {}),
    ("no direction perturbation (dir_perturb_deg=0)", {"dir_perturb_deg": 0.0}),
    ("no spotting", {"spotting_enabled": False}),
    ("direction only (wind/moisture perturb 0)",
     {"wind_perturb_frac": 0.0, "moisture_perturb_frac": 0.0}),
    ("no direction perturbation AND no spotting",
     {"dir_perturb_deg": 0.0, "spotting_enabled": False}),
    ("ensemble_p50_nominal_bound=True (existing knob)",
     {"ensemble_p50_nominal_bound": True}),
    ("model_error_sd_log=0.71 (calibrated knob)", {"model_error_sd_log": 0.71}),
)


def sawtooth(key: str, intensity: str):
    """Re-anchor sawtooth: tick 4 x 15 min, then set_wind(same direction) —
    a world change with no physical change — and tick on. The re-anchor seeds
    every member from the NOMINAL burned mask at that tick, so P50 == nominal
    at the anchor and lags again as the members diverge. The continuous run
    (same session, no set_wind) is the reference for the nominal itself:
    `cont_km2` and the IoU of the two nominal burns show what the re-anchor
    does to the simulated truth."""
    s, fid = make_session(key, intensity)
    c, cfid = make_session(key, intensity)          # continuous twin
    f = s._fires[fid]
    cf = c._fires[cfid]
    cell_km2 = (s._tile.spec.cell_m / 1000.0) ** 2
    rows = []
    for k in range(1, 11):
        if k == 5:
            s.set_wind(f.wind_dir_deg)
        s.tick()
        c.tick()
        now = s._now_s
        ens = f.ens
        a = {i for i, v in enumerate(ens.members[0].data) if v <= now}
        b = {i for i, v in enumerate(cf.ens.members[0].data) if v <= now}
        rows.append({
            "tick": k, "t_h": round((now - f.t_ignite_s) / HOUR, 2),
            "anchor_h": round((f.anchor_t0_s - f.t_ignite_s) / HOUR, 2),
            "nominal_km2": len(a) * cell_km2,
            "p50_km2": count_le(ens.p50.data, now) * cell_km2,
            "p10_km2": count_le(ens.p10.data, now) * cell_km2,
            "cont_km2": len(b) * cell_km2,
            "iou_vs_cont": (len(a & b) / len(a | b)) if (a | b) else 1.0,
        })
    return rows


def run(quick: bool) -> dict:
    out = {"times_h": list(TIMES_H), "scenarios": {}}
    for key in SCENARIOS:
        for intensity in ("high", "extreme"):
            t0 = time.time()
            s, fid = make_session(key, intensity)
            s.tick()
            f = s._fires[fid]
            ens = f.ens
            spec = s._tile.spec
            cell_km2 = (spec.cell_m / 1000.0) ** 2
            rec = {
                "ignition": {"lat": round(f.origin.lat, 6),
                             "lon": round(f.origin.lon, 6),
                             "wind_from_deg": f.wind_dir_deg},
                "cell_m": spec.cell_m, "grid": [spec.nrows, spec.ncols],
                "members": [
                    {"k": k, "wind_scale": round(w, 4), "moist_scale": round(m, 4),
                     "dir_offset_deg": round(d, 2), "ros_scale": round(r, 4)}
                    for k, (w, m, d, r) in enumerate(
                        member_perturbations(s._cfg, fid))],
                "areas": {}, "bands": {},
            }
            for T in TIMES_H:
                t = f.t_ignite_s + T * HOUR
                rec["areas"][str(T)] = area_row(ens, t, cell_km2)
                rec["bands"][str(T)] = band_row(ens, t, cell_km2)
            rec["axis"] = axis_lag(ens, s, f.origin, f.wind_dir_deg,
                                   f.t_ignite_s)
            if not quick:
                abl = {}
                for label, extra in ABLATIONS:
                    e2 = ens if not extra else variant_ens(s, fid, extra)
                    abl[label] = {str(T): area_row(e2, f.t_ignite_s + T * HOUR,
                                                   cell_km2)
                                  for T in (1.75, 4.0)}
                    abl[label]["axis"] = axis_lag(e2, s, f.origin,
                                                  f.wind_dir_deg, f.t_ignite_s)
                rec["ablations"] = abl
            rec["wall_s"] = round(time.time() - t0, 1)
            out["scenarios"][f"{key}/{intensity}"] = rec
            print(f"[{key}/{intensity}] done in {rec['wall_s']} s", flush=True)
    out["sawtooth"] = {f"{key}/{intensity}": sawtooth(key, intensity)
                       for key, intensity in (("park_city_ut", "extreme"),
                                              ("park_city_ut", "high"),
                                              ("eagle_vail_co", "high"))}
    return out


def fmt(v, nd=2):
    if v is None:
        return "   -"
    return f"{v:.{nd}f}"


def report(out: dict) -> None:
    for name, rec in out["scenarios"].items():
        print(f"\n=== {name}  ignition {rec['ignition']}  cell {rec['cell_m']} m "
              f"grid {rec['grid']} ===")
        print("members (k, wind x, moisture x, dir offset deg, ROS x):")
        for m in rec["members"]:
            print(f"   k={m['k']}  w={m['wind_scale']:.3f}  m={m['moist_scale']:.3f}"
                  f"  dir={m['dir_offset_deg']:+6.1f}  ros={m['ros_scale']:.3f}")
        print(f"{'T h':>5s} {'nominal':>8s} {'P50':>7s} {'P50pool':>7s} "
              f"{'P10':>7s} {'P90':>7s} {'P50/nom':>7s} {'med mem':>7s}  "
              f"per-member km2 (k=0..)   inf-drag overdue p10>nom")
        for T in out["times_h"]:
            a = rec["areas"][str(T)]
            mem = " ".join(f"{x:6.2f}" for x in a["members_km2"])
            print(f"{T:5.2f} {a['nominal_km2']:8.2f} {a['p50_km2']:7.2f} "
                  f"{a['p50_pooled_km2']:7.2f} {a['p10_km2']:7.2f} "
                  f"{a['p90_km2']:7.2f} {fmt(a['p50_over_nominal'], 3):>7s} "
                  f"{a['median_member_km2']:7.2f}  {mem}   "
                  f"{a['p50_inf_on_burned_cells']:5d} {a['overdue_cells']:6d} "
                  f"{a['p10_later_than_nominal_cells']:5d}")
        print("truth-bounded bands (km2) = {p50 in (prev, now+h]} minus nominal "
              "burned; raw = the old P50 band painted inside the actual fire:")
        for T in out["times_h"]:
            b = rec["bands"][str(T)]
            print(f"   T={T:5.2f}  h1 {b['h1_km2']:7.2f} (raw inside fire "
                  f"{b['h1_raw_inside_fire_km2']:6.2f})  h5 {b['h5_km2']:7.2f} "
                  f"({b['h5_raw_inside_fire_km2']:6.2f})  h24 {b['h24_km2']:7.2f} "
                  f"({b['h24_raw_inside_fire_km2']:6.2f})")
        print("head-axis arrival (h after ignition): km  nominal  P50  P10 | members")
        for r in rec["axis"]:
            mem = " ".join(fmt(x) if x is not None else "  inf"
                           for x in r["members_h"])
            ratio = (r["p50_h"] / r["nominal_h"]
                     if r["p50_h"] and r["nominal_h"] else None)
            print(f"   {r['km']:4.1f} {fmt(r['nominal_h']):>7s} {fmt(r['p50_h']):>6s}"
                  f" {fmt(r['p10_h']):>6s}  (P50/nom {fmt(ratio)}) | {mem}")
        if "ablations" in rec:
            print("cause attribution (first field recomputed, one knob changed):")
            print(f"   {'variant':50s} {'T':>5s} {'nominal':>8s} {'P50':>7s} "
                  f"{'P10':>7s} {'P50/nom':>7s} {'med mem':>7s}")
            for label, ab in rec["ablations"].items():
                for T in ("1.75", "4.0"):
                    a = ab[T]
                    print(f"   {label:50s} {T:>5s} {a['nominal_km2']:8.2f} "
                          f"{a['p50_km2']:7.2f} {a['p10_km2']:7.2f} "
                          f"{fmt(a['p50_over_nominal'], 3):>7s} "
                          f"{a['median_member_km2']:7.2f}")
                ax = [r for r in ab["axis"] if r["km"] in (2.0, 4.0)]
                axs = "  ".join(
                    f"{r['km']:.0f} km nom {fmt(r['nominal_h'])} P50 {fmt(r['p50_h'])}"
                    for r in ax)
                print(f"   {'':50s} axis: {axs}")
    for name, rows in out.get("sawtooth", {}).items():
        print(f"\n=== re-anchor sawtooth {name}: set_wind(same dir) before tick 5 ===")
        print(f"{'tick':>4s} {'T h':>5s} {'anchor h':>8s} {'nominal':>8s} "
              f"{'P50':>7s} {'P10':>7s} {'continuous':>10s} {'IoU':>5s}")
        for r in rows:
            print(f"{r['tick']:4d} {r['t_h']:5.2f} {r['anchor_h']:8.2f} "
                  f"{r['nominal_km2']:8.2f} {r['p50_km2']:7.2f} {r['p10_km2']:7.2f}"
                  f" {r['cont_km2']:10.2f} {r['iou_vs_cont']:5.2f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the ablations")
    ap.add_argument("--json", help="write the raw numbers to this JSON file")
    args = ap.parse_args(argv)
    out = run(args.quick)
    report(out)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
