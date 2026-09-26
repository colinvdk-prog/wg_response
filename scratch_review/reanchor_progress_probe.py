"""scratch_review/reanchor_progress_probe.py — does a simulator RE-ANCHOR slow
the simulated fire?

Background (2026-09-26). The simulator's ground truth is the NOMINAL ensemble
member; each tick the burned mask is {cells with nominal arrival <= now}. A
RE-ANCHOR re-solves a fire's arrival field from its current burned area — on
a wind-dial change (SimSession.set_wind), a new ignition (shared weather) and
the 0.8 x horizon rebase (tick() step 2). The fire-shading agent measured in
passing that the re-anchor seeds EVERY burned cell AT the anchor time, so a
front cell loses its partial progress into its unburned neighbours: after a
SAME-direction set_wind (no physical change at all) the fire grew 10-24 %
less over the next 1-1.5 h than a continuous run.

This probe measures it on the REAL cached tiles with the page's physics
(server /api/sim/new defaults: ember spotting ON, grass head-ROS floor 0.10),
through the REAL SimSession seams only (no forked physics):

  (a) NO-OP   ignite, run to T = 1 h, set_wind(the SAME direction), tick.
              Reference: the fire's own field from before the re-anchor — the
              continuous run (it was never re-anchored). A correct re-anchor
              reproduces it EXACTLY. Also with spotting OFF (restart time vs
              embers).
  (b) TURN    the same, but set_wind(w0 + 45) / set_wind(w0 + 90). Reference:
              ONE continuous nominal run from the ignition under the same
              scripted weather with the wind shifting at T (MockWeather
              shifts) priced with PIECEWISE edge weather — an edge already
              crossing the shift keeps its progress under the old wind and
              continues at the new rate: exactly "the new wind from T with
              each front cell's partial progress preserved". Limits: the
              shift must fall on a weather-hour boundary (T = 1 h after an
              ignition at the session epoch — it does here); the reference is
              the nominal member only (the members restart from the truth at
              every re-anchor by design); it prices an edge from its SOURCE
              cell only (the engine's law, shared by the simulator). The
              engine's default FROZEN departure-hour law (an edge keeps the
              wind it left under) is printed alongside as a second,
              LATE-biased yardstick.
  (c) IGNITE  run fire 1 to T, light a second fire far away (same intensity,
              same wind -> the shared weather does not change), tick. Fire 1
              re-anchors; reference: fire 1's own continuous field.
  (d) REBASE  horizon_h = 4 so the 0.8 x horizon rebase fires at T+3.25 h;
              reference: the same nominal run solved once to a 96 h horizon.

For each case, at T + 0.5 / 1 / 1.5 / 3 h: burned cells of the re-anchored
field vs the reference, the area ratio, IoU, and the cells burned EARLY
(re-anchored only) / LATE (reference only); plus, over every cell either
field burns by T + 3 h, the arrival-time difference (re-anchored minus
reference, minutes) and, for the no-op cases, the count of cells whose
arrival differs at all (exactness). 'mask@T' checks the burned mask at the
anchor tick itself: cells lost / added by the re-anchor (must be 0 / 0).

Results (docs/SIMULATOR_DESIGN.md "Re-anchor keeps the front's progress";
before/after outputs in scratch_review/reanchor_progress_probe_{before,
after}.txt): BEFORE the 2026-09-26 fix every differing cell was LATE — a
no-op re-anchor grew the fire 2-58% less over the next 0.5-3 h; AFTER it
(spread RE-ANCHOR HISTORY: kept true times + the fire's weather history +
embers aloft) every case, turns included, matches its reference cell for
cell. On the fixed code case (b) also prints the plain approximation the
fix first tried (the kept front re-seeded at its true times but its old
progress priced at the NEW wind, no weather history): early by 4-18 min
mean, IoU down to 0.58 at +0.5 h.

Run from the repo root (py -3 on Windows):
    py -3 scratch_review/reanchor_progress_probe.py
    py -3 scratch_review/reanchor_progress_probe.py --cases a,b --json out.json
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
from wgresponse.weather import MockWeather  # noqa: E402

HOUR = 3600.0
PAGE = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}
#: the p50_vs_nominal_probe / server preset ignitions (bearing, range from
#: the location centre; the wind blows FROM the bearing, so the fire runs at
#: the community)
IGNITION = {"park_city_ut": (225.0, 7000.0), "eagle_vail_co": (240.0, 8000.0)}
#: case (c): the second fire, far from the first (opposite side of the tile)
FAR = {"park_city_ut": (45.0, 7000.0), "eagle_vail_co": (60.0, 12000.0)}
RUNS = (("park_city_ut", "high"), ("park_city_ut", "extreme"),
        ("eagle_vail_co", "high"))
SWITCH_TICKS = 4                   # 4 x 900 s: T = 1 h, a weather-hour boundary
LEADS_H = (0.5, 1.0, 1.5, 3.0)
TURNS = (45.0, 90.0)
REBASE_HORIZON_H = 6.0             # case (d): the rebase fires past 4.8 h


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def _light(s, key, bearing, dist_m, intensity, wind):
    center = simui.LOCATIONS[key].center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, bearing + db, dist_m * shrink)
            try:
                return s.ignite(p.lat, p.lon, intensity=intensity,
                                wind_dir_deg=wind)
            except ValueError:
                continue
    raise RuntimeError(f"{key}: no burnable ignition cell")


def make_session(key, intensity, cfg_extra=None):
    """A page-physics session (roads off: the fire does not depend on them),
    ignited like the server's one-click preset (wind FROM the bearing)."""
    cfg = dict(PAGE)
    cfg.update(cfg_extra or {})
    s = simui.SimSession(key, 8, 1, seed=f"reanchor-{key}", cfg_overrides=cfg,
                         roads_module=None)
    bearing, dist_m = IGNITION[key]
    fid = _light(s, key, bearing, dist_m, intensity, bearing)
    return s, fid


def run_to_switch(s):
    for _ in range(SWITCH_TICKS):
        s.tick()
    return s._now_s


# ---------------------------------------------------------------------------
# comparisons
# ---------------------------------------------------------------------------

def burned(values, t):
    return {i for i, v in enumerate(values) if v <= t}


def compare(new, ref, t_sw):
    """Per-lead mask comparison + arrival-time error over the 3 h window."""
    rows = []
    for lead in LEADS_H:
        t = t_sw + lead * HOUR
        a = burned(new, t)
        b = burned(ref, t)
        grown_a = len(a) - len(burned(new, t_sw))
        grown_b = len(b) - len(burned(ref, t_sw))
        rows.append({
            "lead_h": lead, "cells": len(a), "ref_cells": len(b),
            "ratio": len(a) / len(b) if b else float("nan"),
            "growth_ratio": grown_a / grown_b if grown_b else float("nan"),
            "iou": len(a & b) / len(a | b) if (a | b) else 1.0,
            "early": len(a - b), "late": len(b - a)})
    t_hi = t_sw + LEADS_H[-1] * HOUR
    d = []
    n_diff = 0
    worst = 0.0
    for x, y in zip(new, ref):
        if x > t_hi and y > t_hi:
            continue
        if x != y:
            n_diff += 1
            if math.isinf(x) or math.isinf(y):
                worst = math.inf
            else:
                worst = max(worst, abs(x - y))
        if math.isfinite(x) and math.isfinite(y):
            d.append((x - y) / 60.0)
    d.sort()
    err = {"n_window": len(d), "n_diff": n_diff,
           "max_abs_min": worst / 60.0 if math.isfinite(worst) else math.inf}
    if d:
        err.update(mean_min=statistics.fmean(d), median_min=d[len(d) // 2],
                   p05_min=d[int(0.05 * (len(d) - 1))],
                   p95_min=d[int(0.95 * (len(d) - 1))],
                   frac_early=sum(1 for v in d if v < 0) / len(d),
                   frac_late=sum(1 for v in d if v > 0) / len(d))
    return rows, err


def mask_at_anchor(old, new, t_sw):
    a = burned(old, t_sw)
    b = burned(new, t_sw)
    return {"burned": len(a), "lost": len(a - b), "added": len(b - a)}


def shift_reference(s, f, intensity, w0, w_new, t_sw, piecewise=True):
    """ONE continuous nominal run from the ignition, wind FROM w0 shifting to
    w_new at t_sw (MockWeather shifts — the simulator's own weather script
    with the change at that hour)."""
    p = simui.INTENSITY_WEATHER[intensity]
    u = p["wind_mph"] * simui.MPH_TO_MPS
    shift_h = (t_sw - simui.T0_S) / HOUR
    assert shift_h == int(shift_h), "the shift must sit on a weather hour"
    wx = MockWeather(seed=f"simui|{s._seed}|wx", base_wind_mps=u, dir_deg=w0,
                     rh_pct=p["rh_pct"], temp_c=p["temp_c"],
                     gust_factor=simui.GUST_FACTOR, t0_s=simui.T0_S,
                     shifts=[(int(shift_h), u, w_new, p["rh_pct"])]).forecast(
        s._loc.center, s._cfg.horizon_h)
    cfg = dataclasses.replace(s._cfg, edge_weather_piecewise=piecewise)
    return spread.compute_arrival(s._tile, [f.origin], wx, f.t_ignite_s, cfg,
                                  incident_id=f.incident_id, member_k=0).data


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

def case_noop(key, intensity, spotting=True):
    s, fid = make_session(key, intensity,
                          None if spotting else {"spotting_enabled": False})
    t_sw = run_to_switch(s)
    f = s._fires[fid]
    old = f.ens.members[0].data
    s.set_wind(f.wind_dir_deg)                 # the SAME direction
    s.tick()
    assert f.anchor_t0_s == t_sw, "the no-op set_wind must re-anchor"
    new = f.ens.members[0].data
    rows, err = compare(new, old, t_sw)
    return {"case": "a", "variant": "spotting on" if spotting else "spotting off",
            "mask": mask_at_anchor(old, new, t_sw), "rows": rows, "err": err}


def case_turn(key, intensity, turn):
    s, fid = make_session(key, intensity)
    t_sw = run_to_switch(s)
    f = s._fires[fid]
    old = f.ens.members[0].data
    w0 = s._wind_dir_deg
    w_new = (w0 + turn) % 360.0
    s.set_wind(w_new)
    approx = None
    if hasattr(s, "_reanchor_history"):
        # (after the 2026-09-26 fix) the same re-anchor WITHOUT the fire's
        # weather history: the kept front re-seeded at its true times but
        # its old progress priced at the NEW wind — the plain approximation
        kept, aloft = s._reanchor_history(f, t_sw)
        approx = spread.compute_arrival(
            s._tile, [], s._wx, t_sw, s._cfg, incident_id=fid, member_k=0,
            seed_spotting=False, kept=kept, embers_aloft=aloft).data
    s.tick()
    assert f.anchor_t0_s == t_sw
    new = f.ens.members[0].data
    ref = shift_reference(s, f, intensity, w0, w_new, t_sw, piecewise=True)
    frozen = shift_reference(s, f, intensity, w0, w_new, t_sw, piecewise=False)
    rows, err = compare(new, ref, t_sw)
    frows, ferr = compare(new, frozen, t_sw)
    rrows, _ = compare(frozen, ref, t_sw)
    out = {"case": "b", "variant": f"turn +{turn:g} deg",
           "mask": mask_at_anchor(old, new, t_sw), "rows": rows, "err": err,
           "vs_frozen_rows": frows, "vs_frozen_err": ferr,
           "frozen_vs_piecewise_rows": rrows}
    if approx is not None:
        out["approx_rows"], out["approx_err"] = compare(approx, ref, t_sw)
        out["approx_mask"] = mask_at_anchor(old, approx, t_sw)
    return out


def case_second_ignition(key, intensity):
    s, fid = make_session(key, intensity)
    t_sw = run_to_switch(s)
    f = s._fires[fid]
    old = f.ens.members[0].data
    bearing, dist_m = FAR[key]
    fid2 = _light(s, key, bearing, dist_m, intensity, None)   # wind unchanged
    s.tick()
    assert f.anchor_t0_s == t_sw, "the second ignition must re-anchor fire 1"
    new = f.ens.members[0].data
    rows, err = compare(new, old, t_sw)
    f2 = s._fires[fid2]
    return {"case": "c", "variant": f"second fire {fid2} "
            f"{geo.haversine_m(f.origin, f2.origin) / 1000:.1f} km away",
            "mask": mask_at_anchor(old, new, t_sw), "rows": rows, "err": err}


def case_rebase(key, intensity):
    # a short horizon so the rebase fires early (the alert ladder's EXTREME
    # P10 rung must sit inside it — config validation; the fire ignores it)
    s, fid = make_session(key, intensity, {"horizon_h": REBASE_HORIZON_H,
                                           "extreme_p10_h": REBASE_HORIZON_H})
    f = s._fires[fid]
    s.tick()
    first = f.ens
    old = first.members[0].data
    while f.ens is first:
        t_sw = s._now_s
        s.tick()
    assert f.anchor_t0_s == t_sw
    new = f.ens.members[0].data
    long_cfg = dataclasses.replace(s._cfg, horizon_h=96.0)
    ref = spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                                 long_cfg, incident_id=fid, member_k=0).data
    rows, err = compare(new, ref, t_sw)
    return {"case": "d", "variant": f"rebase at T+{(t_sw - f.t_ignite_s) / HOUR:g} h "
            f"(horizon {REBASE_HORIZON_H:g} h)",
            "mask": mask_at_anchor(old, new, t_sw), "rows": rows, "err": err}


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _f(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "   -"
    if isinstance(v, float) and math.isinf(v):
        return " inf"
    return f"{v:.{nd}f}"


def _print_table(rows, e):
    print(f"   {'lead h':>6s} {'cells':>7s} {'ref':>7s} {'area':>6s} "
          f"{'growth':>6s} {'IoU':>5s} {'early':>6s} {'late':>6s}")
    for row in rows:
        print(f"   {row['lead_h']:6.2f} {row['cells']:7d} {row['ref_cells']:7d} "
              f"{_f(row['ratio']):>6s} {_f(row['growth_ratio']):>6s} "
              f"{_f(row['iou']):>5s} {row['early']:6d} {row['late']:6d}")
    print(f"   arrival re-anchored - reference over the {e['n_window']} cells "
          f"burned by T+{LEADS_H[-1]:g} h: {e['n_diff']} differ, max |d| "
          f"{_f(e['max_abs_min'], 1)} min", end="")
    if "mean_min" in e:
        print(f"; mean {_f(e['mean_min'])} / median {_f(e['median_min'])} / "
              f"p05 {_f(e['p05_min'])} / p95 {_f(e['p95_min'])} min; "
              f"early {_f(100 * e['frac_early'], 1)}% late "
              f"{_f(100 * e['frac_late'], 1)}%")
    else:
        print()


def print_result(name, r):
    m = r["mask"]
    print(f"\n[{name}] ({r['case']}) {r['variant']} — mask@T: {m['burned']} burned, "
          f"lost {m['lost']}, added {m['added']}")
    _print_table(r["rows"], r["err"])
    if "approx_rows" in r:
        m = r["approx_mask"]
        print(f"   ... the plain approximation (no weather history: the new "
              f"wind applied to the old progress) — mask@T lost {m['lost']}, "
              f"added {m['added']}:")
        _print_table(r["approx_rows"], r["approx_err"])
    if "vs_frozen_rows" in r:
        fr = r["vs_frozen_rows"]
        rr = r["frozen_vs_piecewise_rows"]
        print("   vs the FROZEN-law shift run (IoU / area) by lead: " + "  ".join(
            f"{x['lead_h']:g} h {_f(x['iou'])}/{_f(x['ratio'])}" for x in fr))
        print("   (frozen-law vs piecewise reference, IoU / area: " + "  ".join(
            f"{x['lead_h']:g} h {_f(x['iou'])}/{_f(x['ratio'])}" for x in rr)
            + ")")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default="a,b,c,d")
    ap.add_argument("--runs", default=",".join(f"{k}/{i}" for k, i in RUNS))
    ap.add_argument("--json", help="write the raw numbers to this JSON file")
    args = ap.parse_args(argv)
    cases = set(args.cases.split(","))
    out = {}
    for spec in args.runs.split(","):
        key, intensity = spec.split("/")
        name = f"{key}/{intensity}"
        t0 = time.time()
        jobs = []
        if "a" in cases:
            jobs.append(lambda: case_noop(key, intensity, spotting=True))
            jobs.append(lambda: case_noop(key, intensity, spotting=False))
        if "b" in cases:
            for turn in TURNS:
                jobs.append(lambda turn=turn: case_turn(key, intensity, turn))
        if "c" in cases:
            jobs.append(lambda: case_second_ignition(key, intensity))
        if "d" in cases and intensity == "high":
            jobs.append(lambda: case_rebase(key, intensity))
        res = []
        for job in jobs:
            r = job()
            print_result(name, r)
            sys.stdout.flush()
            res.append(r)
        print(f"   ({name}: {time.time() - t0:.0f} s)", flush=True)
        out[name] = res
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
