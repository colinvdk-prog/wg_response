"""Ember spotting calibration probe (founder 2026-10-09: "begin the
calibration of the ember distance and size of fire calibration"). NOT product
code. Report: docs/review/ember_spotting_calibration_2026-10-09.md.

For each one-click scenario (server.SCENARIOS) and physics variant, through
the EXACT server path (SimSessionManager.new_session, the page's defaults:
spotting ON, fuel-class ROS floor 0.10, plan-on-roads, the 24 h coating hold)
on the committed real tiles:

  1. SPOTTING — the nominal member (the simulator's actual fire) is re-solved
     with spread.compute_arrival(spot_trace=...) under the session's own tile,
     weather and cfg (checked bit-equal to the session's member 0): every
     ember thrown (time, whole-fire area, source intensity, drawn distance,
     SEMANTICS 19 reach, landing) and every SPOT IGNITION (an ember whose
     landing time is its cell's final arrival), its LEAD (distance from the
     landing cell to the nearest cell already burned when the ember was
     thrown), spot-from-spot chains, by fire age / fire size / intensity.
  2. THE DEMO — 24 ticks (6 h): acres, head distance from the ignition,
     first demo home reached (its own cell, or for a home on a non-burnable
     cell the earliest arrival in the simulator's 200 m ember-radius window),
     the crew-safety audit (departure violations + crew-overrun rows per
     tick), homes served and outcomes.

Variants (ros_floor_grass_only=True unless noted — the simulator's new floor
default, set explicitly here):
  as_deployed     the page as shipped (floor on all four classes; reference)
  legacy          SEMANTICS 11 spotting as today
  spotting_off    "Ember spotting" unticked
  law_r<A>_d<D>   SEMANTICS 19 armed: reference fire size A ha, ignition
                  delay D min (exponent 0.43 unless _e<E> is appended)
  dep_*           the same with the as-deployed floor (all four classes):
                  dep_spotting_off, dep_law_r<A>_d<D>[_e<E>]

Usage: py -3 scratch_review/ember_spotting_probe.py [scenarios] [variants]
                                                    [--stats-only]
  (comma lists; default = the three calibration scenarios x every variant)
       py -3 scratch_review/ember_spotting_probe.py --catalog
  (all thirteen one-click scenarios, nominal field only, six variants)
Output: scratch_review/ember_spotting_probe.json (merged by scenario/variant);
        scratch_review/ember_spotting_catalog.json (--catalog).
"""
from __future__ import annotations

import bisect
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, spread  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402
from wgresponse.server import SimSessionManager  # noqa: E402

OUT = os.path.join(HERE, "ember_spotting_probe.json")
ACRES_PER_HA = 2.4710538
INF = math.inf
SCENS = ("park_city_windstorm", "boulder_chinook", "paradise_ne_wind")
BASE = {"ros_floor_grass_only": True}
TIME_EDGES_H = (0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 6.0)
SIZE_EDGES_HA = (0.0, 10.0, 100.0, 1000.0, INF)
INT_EDGES_KWM = (0.0, 500.0, 2000.0, 4000.0, 10000.0, INF)


def law(ref_ha, delay_min, exp=0.43):
    return dict(BASE, spot_fire_size=True, spot_fire_size_ref_ha=float(ref_ha),
                spot_ignition_delay_min=float(delay_min),
                spot_fire_size_exp=float(exp))


VARIANTS = {"as_deployed": {}, "legacy": dict(BASE),
            "spotting_off": dict(BASE, spotting_enabled=False),
            "dep_spotting_off": {"spotting_enabled": False}}
for _ref in (100, 300, 1000):
    for _d in (0, 5, 10):
        VARIANTS[f"law_r{_ref}_d{_d}"] = law(_ref, _d)


def variant_cfg(name: str) -> dict:
    """VARIANTS entry, or a [dep_]law_r<A>_d<D>[_e<E>] name parsed on the
    fly (dep_ = keep the as-deployed floor on all four fuel classes)."""
    if name in VARIANTS:
        return VARIANTS[name]
    dep = name.startswith("dep_")
    core = name[4:] if dep else name
    if core.startswith("law_r"):
        parts = dict((p[0], float(p[1:])) for p in core[4:].split("_"))
        cfg = law(parts["r"], parts.get("d", 0.0), parts.get("e", 0.43))
        if dep:
            cfg.pop("ros_floor_grass_only")
        return cfg
    raise SystemExit(f"unknown variant {name!r}")


def fire_sizes_at_ignition(data, spec):
    """cell index -> cell count of the connected burned area (8-neighbour)
    the cell joins when it ignites, replayed over the FINAL field in
    (arrival, index) order — exactly the SEMANTICS 19 union-find's state at
    the cell's pop, so it serves the legacy runs (what size of fire threw
    each ember) and cross-checks the engine's own count in the law runs."""
    nr, nc = spec.nrows, spec.ncols
    parent = {}
    size = {}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    out = {}
    for v, i in sorted((v, i) for i, v in enumerate(data) if v < INF):
        parent[i] = i
        size[i] = 1
        rt = i
        r0, c0 = divmod(i, nc)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r0 + dr, c0 + dc
                j = rr * nc + cc
                if 0 <= rr < nr and 0 <= cc < nc and j in parent:
                    rj = root(j)
                    if rj != rt:
                        if size[rj] > size[rt] or (size[rj] == size[rt]
                                                   and rj < rt):
                            rt, rj = rj, rt
                        parent[rj] = rt
                        size[rt] += size[rj]
        out[i] = size[rt]
    return out


def build(scen: str, cfg: dict):
    """The server's own session for `scen` with `cfg` overrides. The page's
    physics toggles are payload flags the server maps onto cfg AFTER the
    overrides (spotting -> spotting_enabled), so they travel that way."""
    mgr = SimSessionManager()
    cfg = dict(cfg)
    payload = {"scenario": scen, "cfg": cfg}
    if "spotting_enabled" in cfg:
        payload["spotting"] = bool(cfg.pop("spotting_enabled"))
    code, body = mgr.new_session(payload)
    if code != 200:
        raise SystemExit(f"{scen}: {body}")
    s = mgr._entry(body["session_id"]).session
    return s, s._fire_order[0]


def q(vals, p):
    """Nearest-rank quantile of a sorted list (None when empty)."""
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, int(math.ceil(p * len(vals))) - 1))]


def r1(x, nd=2):
    return None if x is None else round(x, nd)


def lead_m(data, spec, land, t_u):
    """Distance (m) from cell `land` to the nearest cell burned at or before
    t_u (ring search; the thrower itself is one, so it terminates)."""
    nr, nc = spec.nrows, spec.ncols
    r0, c0 = divmod(land, nc)
    best = INF
    k = 0
    while k <= max(nr, nc):
        if k > best:
            break
        for dr in range(-k, k + 1):
            for dc in (range(-k, k + 1) if abs(dr) == k else (-k, k)):
                rr, cc = r0 + dr, c0 + dc
                if 0 <= rr < nr and 0 <= cc < nc and data[rr * nc + cc] <= t_u:
                    d = math.hypot(dr, dc)
                    if d < best:
                        best = d
        k += 1
    return best * spec.cell_m


def spot_stats(s, fid):
    f = s._fires[fid]
    tile, wx, cfg = s._tile, s._wx, s._cfg
    spec = tile.spec
    nc = spec.ncols
    cell_ha = spec.cell_m ** 2 / 1e4
    trace = []
    t_w = time.monotonic()
    data = spread.compute_arrival(tile, [f.origin], wx, f.t_ignite_s, cfg,
                                  incident_id=fid, member_k=0,
                                  spot_trace=trace).data
    solve_s = time.monotonic() - t_w
    t0 = f.t_ignite_s
    fuel = tile.fuel.data
    burn_sorted = sorted(v for v in data if v < INF)

    def fire_ha(t):
        return bisect.bisect_right(burn_sorted, t) * cell_ha

    own = fire_sizes_at_ignition(data, spec)
    size_mismatch = 0
    embers = []
    spot_src = {}                          # landing cell -> ember record
    for (idx, tu, mean, u20, d0, reach, fcells, land, tv) in trace:
        if tu > t0 + TIME_EDGES_H[-1] * 3600.0:
            continue
        if reach < INF and fcells != own[idx]:
            size_mismatch += 1
        inten = (mean / (spread._SPOT_A * u20 ** spread._SPOT_WS_EXP)) \
            ** (1.0 / spread._SPOT_FLIN_EXP)
        won = (land >= 0 and is_burnable(int(round(fuel[land])))
               and data[land] == tv)
        e = {"src": idx, "age_h": (tu - t0) / 3600.0, "tu": tu,
             "fire_ha": fire_ha(tu), "own_ha": own[idx] * cell_ha,
             "I": inten, "u20": u20, "drawn_m": d0, "reach_m": reach,
             "landed": land >= 0, "land": land, "won": won}
        embers.append(e)
        if won:
            spot_src[land] = e
    for e in embers:
        if e["won"]:
            e["lead_m"] = lead_m(data, spec, e["land"], e["tu"])
            e["from_spot"] = e["src"] in spot_src
    depth = {}

    def chain(c):
        if c not in depth:
            e = spot_src.get(c)
            depth[c] = 0 if e is None else 1 + chain(e["src"])
        return depth[c]

    wins = [e for e in embers if e["won"]]
    for e in wins:
        chain(e["land"])

    def block(sel):
        land = sorted(e["drawn_m"] for e in sel if e["landed"])
        w = [e for e in sel if e["won"]]
        wd = sorted(e["drawn_m"] for e in w)
        wl = sorted(e["lead_m"] for e in w)
        return {"burning_cells": len(sel),
                "embers_landed": len(land),
                "launch_frac": r1(len(land) / len(sel), 3) if sel else None,
                "spot_ignitions": len(w),
                "spot_from_spot": sum(1 for e in w if e["from_spot"]),
                "longest_ember_drawn_m": r1(max((e["drawn_m"] for e in sel),
                                                default=None), 0),
                "longest_ember_landed_m": r1(land[-1] if land else None, 0),
                "spot_dist_p50_m": r1(q(wd, 0.5), 0),
                "spot_dist_p90_m": r1(q(wd, 0.9), 0),
                "spot_dist_max_m": r1(wd[-1] if wd else None, 0),
                "lead_p50_m": r1(q(wl, 0.5), 0),
                "lead_p90_m": r1(q(wl, 0.9), 0),
                "lead_max_m": r1(wl[-1] if wl else None, 0)}

    by_time = []
    for a, b in zip(TIME_EDGES_H, TIME_EDGES_H[1:]):
        sel = [e for e in embers if a < e["age_h"] <= b or (a == 0 and e["age_h"] == 0)]
        row = {"window_h": [a, b], "fire_ha_at_end": r1(fire_ha(t0 + b * 3600.0), 1)}
        row.update(block(sel))
        by_time.append(row)
    by_size = []
    for a, b in zip(SIZE_EDGES_HA, SIZE_EDGES_HA[1:]):
        sel = [e for e in embers if a <= e["fire_ha"] < b]
        row = {"fire_ha": [a, None if b == INF else b]}
        row.update(block(sel))
        by_size.append(row)
    by_int = []
    for a, b in zip(INT_EDGES_KWM, INT_EDGES_KWM[1:]):
        sel = [e for e in embers if a <= e["I"] < b]
        row = {"intensity_kwm": [a, None if b == INF else b]}
        row.update(block(sel))
        by_int.append(row)
    # the size of the THROWER's own connected fire (a spot fire is its own
    # small fire until it joins another): < 1 ha = an isolated single cell
    by_own = []
    for a, b in zip((0.0, 1.0) + SIZE_EDGES_HA[1:-1], (1.0,) + SIZE_EDGES_HA[1:]):
        sel = [e for e in embers if a <= e["own_ha"] < b]
        row = {"thrower_fire_ha": [a, None if b == INF else b]}
        row.update(block(sel))
        by_own.append(row)

    def first(pred):
        for e in sorted(wins, key=lambda x: x["tu"]):
            if pred(e):
                return {"age_min": r1(e["age_h"] * 60.0, 1),
                        "fire_ha": r1(e["fire_ha"], 1),
                        "dist_m": r1(e["drawn_m"], 0),
                        "lead_m": r1(e["lead_m"], 0),
                        "I_kwm": r1(e["I"], 0)}
        return None

    thresholds = {}
    for km in (0.5, 1.0, 2.0, 3.0):
        thresholds[f"first_spot_over_{km}km"] = first(
            lambda e, km=km: e["drawn_m"] > km * 1000.0)
        thresholds[f"first_lead_over_{km}km"] = first(
            lambda e, km=km: e["lead_m"] > km * 1000.0)
    allb = block(embers)
    allb["max_chain_depth"] = max((depth[e["land"]] for e in wins), default=0)
    reach = sorted(e["reach_m"] for e in embers if e["reach_m"] < INF)
    allb["reach_p50_m"] = r1(q(reach, 0.5), 0)
    allb["reach_max_m"] = r1(reach[-1] if reach else None, 0)
    ints = sorted(e["I"] for e in embers)
    allb["intensity_p10_p50_p90_kwm"] = [r1(q(ints, p), 0) for p in (0.1, 0.5, 0.9)]
    allb["fire_size_replay_mismatches"] = size_mismatch
    return data, {"solve_s": round(solve_s, 2), "all_6h": allb,
                  "by_time": by_time, "by_fire_size": by_size,
                  "by_thrower_fire": by_own,
                  "by_intensity": by_int, "firsts": thresholds,
                  "u20_mps": r1(embers[0]["u20"] if embers else None, 2)}


def demo(s, fid, ticks, data_trace):
    f = s._fires[fid]
    spec = s._tile.spec
    nc = spec.ncols
    cell_ha = spec.cell_m ** 2 / 1e4
    rows = []
    overrun_rows = 0
    max_overrun = 0
    sb = {}
    st = None
    for k in range(ticks):
        st = s.tick()
        sb = st["scoreboard"]
        overrun_rows += sb["crew_overruns"]
        max_overrun = max(max_overrun, sb["crew_overruns"])
        rows.append({"t_h": round((k + 1) * 0.25, 2),
                     "sim_area_acres": round(st["fire_view"]["areas_km2"]["fire"]
                                             * 247.105),
                     "violations": sb["violations"],
                     "crew_overruns": sb["crew_overruns"]})
    nom = f.ens.members[0].data
    same = list(nom) == list(data_trace)
    t0 = f.t_ignite_s
    ign = f.origin
    order = sorted((v, i) for i, v in enumerate(nom) if v < INF)
    times = [v for v, _i in order]
    cummax = []
    m = 0.0
    for _v, i in order:
        d = geo.haversine_m(ign, geo.rc_to_latlon(spec, *divmod(i, nc))) / 1000.0
        m = max(m, d)
        cummax.append(m)

    def at(t_h):
        j = bisect.bisect_right(times, t0 + t_h * 3600.0)
        return (round(j * cell_ha * ACRES_PER_HA), round(cummax[j - 1], 2)
                if j else 0.0)

    marks = {}
    for t_h in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0):
        a, h = at(t_h)
        marks[str(t_h)] = {"acres": a, "head_km": h}
    heads = [at(i * 0.25)[1] for i in range(25)]
    rate = max(((heads[i + 4] - heads[i]) for i in range(len(heads) - 4)),
               default=0.0)

    def reach_time(km):
        j = next((n for n, v in enumerate(cummax) if v >= km), None)
        return None if j is None else round((times[j] - t0) / 3600.0, 2)

    # homes: own cell, or for a home on a non-burnable cell the earliest
    # arrival in the simulator's ember-radius square window
    rad = int(math.ceil(s._cfg.ember_threat_radius_m / spec.cell_m))
    first_home = INF
    for cl in s._clients:
        rc = geo.latlon_to_rc(spec, cl.loc)
        if rc is None:
            continue
        own = nom[rc[0] * nc + rc[1]]
        t = own
        if own == INF and rad > 0:
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    rr, cc = rc[0] + dr, rc[1] + dc
                    if 0 <= rr < spec.nrows and 0 <= cc < spec.ncols:
                        t = min(t, nom[rr * nc + cc])
        first_home = min(first_home, t)
    fh = None if first_home == INF else round((first_home - t0) / 3600.0, 2)
    return {"nominal_matches_trace_solve": same, "marks": marks,
            "max_head_gain_km_per_h": round(rate, 2),
            "head_reaches_h": {f"{km}km": reach_time(km) for km in (2, 5, 10)},
            "first_home_reached_h": fh,
            "audit": {"departure_violations_final":
                      sb.get("violations", 0) - sb.get("crew_overruns", 0),
                      "crew_overrun_rows_sum": overrun_rows,
                      "crew_overrun_rows_max_tick": max_overrun,
                      "served": sb.get("served"),
                      "withdrawn": sb.get("withdrawn"),
                      "relocations": sb.get("relocations"),
                      "crews_left_map": sb.get("crews_left_map"),
                      "outcomes": sb.get("outcomes")},
            "ticks": rows}


def run(scen, vname, stats_only=False, ticks=24):
    t_w = time.monotonic()
    cfg = variant_cfg(vname)
    s, fid = build(scen, cfg)
    w0 = s._wx.samples[0]
    data, stats = spot_stats(s, fid) if s._cfg.spotting_enabled else (None, None)
    res = {"scenario": scen, "variant": vname, "cfg": cfg,
           "weather": {"wind_mps": round(w0.wind_speed_mps, 2),
                       "dir_from_deg": w0.wind_dir_deg, "rh_pct": w0.rh_pct,
                       "temp_c": w0.temp_c},
           "spotting": stats}
    if not stats_only:
        if data is None:     # spotting off: the nominal field itself
            f = s._fires[fid]
            data = spread.compute_arrival(s._tile, [f.origin], s._wx,
                                          f.t_ignite_s, s._cfg,
                                          incident_id=fid, member_k=0).data
        res["demo"] = demo(s, fid, ticks, data)
    res["wall_s"] = round(time.monotonic() - t_w, 1)
    return res


CATALOG_OUT = os.path.join(HERE, "ember_spotting_catalog.json")
CATALOG_VARIANTS = ("as_deployed", "legacy", "spotting_off", "law_r300_d5",
                    "dep_spotting_off", "dep_law_r300_d5")


def catalog():
    """Every one-click scenario x CATALOG_VARIANTS, nominal field only (no
    ticks): acres and head distance at 1 / 3 / 6 h and the first spot
    ignition beyond 1 km — which demos does each spotting law carry?"""
    from wgresponse.server import SCENARIOS
    rows = []
    for scen in SCENARIOS:
        for v in CATALOG_VARIANTS:
            s, fid = build(scen, variant_cfg(v))
            f = s._fires[fid]
            spec = s._tile.spec
            nc = spec.ncols
            trace = []
            data = spread.compute_arrival(s._tile, [f.origin], s._wx,
                                          f.t_ignite_s, s._cfg,
                                          incident_id=fid, member_k=0,
                                          spot_trace=trace).data
            t0 = f.t_ignite_s
            cell_ac = spec.cell_m ** 2 / 1e4 * ACRES_PER_HA
            order = sorted((x, i) for i, x in enumerate(data) if x < INF)
            times = [x for x, _i in order]
            heads = []
            m = 0.0
            for _x, i in order:
                m = max(m, geo.haversine_m(f.origin, geo.rc_to_latlon(
                    spec, *divmod(i, nc))) / 1000.0)
                heads.append(m)
            row = {"scenario": scen, "variant": v,
                   "intensity": s._intensity}
            for t_h in (1.0, 3.0, 6.0):
                j = bisect.bisect_right(times, t0 + t_h * 3600.0)
                row[f"acres_{t_h:g}h"] = round(j * cell_ac)
                row[f"head_km_{t_h:g}h"] = round(heads[j - 1], 2) if j else 0.0
            first_km = None
            for (idx, tu, _m, _u, d0, _r, _c, land, tv) in trace:
                if (land >= 0 and d0 > 1000.0 and data[land] == tv
                        and tu <= t0 + 6 * 3600.0):
                    if first_km is None or tu < first_km[0]:
                        first_km = (tu, bisect.bisect_right(times, tu)
                                    * cell_ac)
            row["first_spot_over_1km"] = (
                None if first_km is None else
                {"age_min": round((first_km[0] - t0) / 60.0, 1),
                 "fire_acres": round(first_km[1])})
            rows.append(row)
            print(f"{scen:34s} {v:16s} {s._intensity:8s} ac 1h "
                  f"{row['acres_1h']:>6} 3h {row['acres_3h']:>6} 6h "
                  f"{row['acres_6h']:>6} | head 1h {row['head_km_1h']:5.2f} "
                  f"6h {row['head_km_6h']:5.2f} | first spot >1 km "
                  f"{row['first_spot_over_1km']}", flush=True)
            with open(CATALOG_OUT, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=1)


def main(argv):
    if "--catalog" in argv:
        catalog()
        return
    stats_only = "--stats-only" in argv
    args = [a for a in argv if not a.startswith("--")]
    scens = args[0].split(",") if args else list(SCENS)
    names = args[1].split(",") if len(args) > 1 else list(VARIANTS)
    try:
        with open(OUT, encoding="utf-8") as fh:
            store = {(r["scenario"], r["variant"]): r for r in json.load(fh)}
    except (OSError, ValueError):
        store = {}
    for scen in scens:
        for v in names:
            res = run(scen, v, stats_only=stats_only)
            old = store.get((scen, v))
            if stats_only and old and "demo" in old:
                res["demo"] = old["demo"]
            store[(scen, v)] = res
            sp = res.get("spotting") or {}
            a6 = sp.get("all_6h") or {}
            d = res.get("demo") or {}
            mk = d.get("marks", {})
            au = d.get("audit", {})
            print(f"{scen:20s} {v:16s} spots {a6.get('spot_ignitions', '-'):>5} "
                  f"(from spot {a6.get('spot_from_spot', '-')}) dist max "
                  f"{a6.get('spot_dist_max_m', '-')} lead max {a6.get('lead_max_m', '-')} "
                  f"| ac 1h {mk.get('1.0', {}).get('acres', '-')} 3h "
                  f"{mk.get('3.0', {}).get('acres', '-')} 6h {mk.get('6.0', {}).get('acres', '-')} "
                  f"head 1h {mk.get('1.0', {}).get('head_km', '-')} 6h "
                  f"{mk.get('6.0', {}).get('head_km', '-')} | home "
                  f"{d.get('first_home_reached_h', '-')} | viol "
                  f"{au.get('departure_violations_final', '-')} ovr "
                  f"{au.get('crew_overrun_rows_sum', '-')} | {res['wall_s']} s",
                  flush=True)
            with open(OUT, "w", encoding="utf-8") as fh:
                json.dump(sorted(store.values(),
                                 key=lambda r: (r["scenario"], r["variant"])),
                          fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
