"""Crown-fire study: the 13 one-click scenarios (2026-10-12; NOT product
code; docs/review/crown_fire_model_2026-10-12.md section 3.4).

Through the EXACT server path (SimSessionManager.new_session: the page's
defaults — spotting on, ROS floor 0.10, plan-on-roads, the 24 h coating
hold, city weather, the crew-safety laws the simulator arms) on the
committed tiles (now carrying LANDFIRE canopy), 6 h of 15-min ticks with the
crew-safety audit, per scenario x variant:

  opt1        the founder's interim option 1: the timber floor (floor on
              GR / GS / TU / TL) + calibrated embers (spot_fire_size)
  grass_law   floor on grass only + calibrated embers, no crown fire (the
              state that made the timber demos crawl)
  crown       floor on grass only + calibrated embers + crown_fire
  opt1_crown  option 1 + crown_fire (what crown fire adds on top of it)
  crown_aa    crown + spread_any_angle (SEMANTICS 21: heads no longer held
              to the 16 grid directions; recommended for the simulator by
              the grid-orientation review, not armed on main)
  crown_d10   crown with spot_ignition_delay_min 10 (the upper end of the
              ember calibration's 0-10 min): crown-fire embers drive most of
              the spread otherwise (section 3.6)

Reported: acres at 1 / 3 / 6 h, the head's distance and its fastest 1-h
gain, the first demo home reached, the audit (departure violations, crew
overrun rows), homes served; for the nominal fire, the share of burned
forest cells by fire type (surface / passive / active) and the spotting
statistics of the calibration (first spot beyond 0.5 / 1 / 2 km and the size
of the fire that threw it, spot ignitions, those thrown by one-cell fires,
the spot-from-spot chain depth).

Usage: py -3 scratch_review/crown_fire_scenarios.py [scenarios] [variants]
       (comma lists; default every scenario x every variant)
Output: scratch_review/crown_fire_scenarios.json (merged by scenario/variant;
        concurrent processes: pass --out PATH and merge with --merge).
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
sys.path.insert(0, HERE)

import ember_spotting_probe as E  # noqa: E402
from wgresponse import geo, rothermel, spread  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402
from wgresponse.server import SCENARIOS  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_scenarios.json")
INF = math.inf
VARIANTS = {
    "opt1": {"ros_floor_grass_only": False, "spot_fire_size": True},
    "grass_law": {"ros_floor_grass_only": True, "spot_fire_size": True},
    "crown": {"ros_floor_grass_only": True, "spot_fire_size": True,
              "crown_fire": True},
    "opt1_crown": {"ros_floor_grass_only": False, "spot_fire_size": True,
                   "crown_fire": True},
    # the crown variant with any-angle spread (SEMANTICS 21, recommended for
    # the simulator by the grid-orientation review, not armed on main)
    "crown_aa": {"ros_floor_grass_only": True, "spot_fire_size": True,
                 "crown_fire": True, "spread_any_angle": True},
    # the crown variant with a 10-min spot-fire ignition delay (the upper
    # end of the ember calibration's plausible 0-10 min): a spot fire landing
    # in forest needs time to build to a crown fire
    "crown_d10": {"ros_floor_grass_only": True, "spot_fire_size": True,
                  "crown_fire": True, "spot_ignition_delay_min": 10.0},
}


def nominal(s, fid):
    """The session's nominal fire re-solved with spot_trace and crown_trace
    (checked bit-equal to the session's member 0 by E.demo)."""
    f = s._fires[fid]
    spot, crown = [], {}
    tic = time.monotonic()
    data = spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                                  s._cfg, incident_id=fid, member_k=0,
                                  spot_trace=spot, crown_trace=crown).data
    return data, spot, crown, time.monotonic() - tic


def crown_stats(s, fid, data, crown):
    f = s._fires[fid]
    t0 = f.t_ignite_s
    spec = s._tile.spec
    cell_ac = spec.cell_m ** 2 / 4046.8564224
    out = {}
    for t_h in (1.0, 3.0, 6.0):
        t = t0 + t_h * 3600.0
        burned = [i for i, v in enumerate(data) if v <= t]
        kinds = {"surface_no_crown_fuel": 0, "surface": 0, "passive": 0,
                 "active": 0}
        for i in burned:
            k = crown.get(i)
            if k is None:
                kinds["surface_no_crown_fuel"] += 1
            elif k == rothermel.CROWN_ACTIVE:
                kinds["active"] += 1
            elif k == rothermel.CROWN_PASSIVE:
                kinds["passive"] += 1
            else:
                kinds["surface"] += 1
        out[f"{t_h:g}h"] = {k: round(v * cell_ac) for k, v in kinds.items()}
    # the ignition cell
    rc = geo.latlon_to_rc(spec, f.origin)
    i0 = rc[0] * spec.ncols + rc[1]
    cp = s._tile.canopy
    out["ignition_cell"] = {
        "fuel": int(s._tile.fuel.data[i0]),
        "canopy": None if cp is None else {
            "cbh_m": cp.cbh_m.data[i0], "cbd_kgm3": cp.cbd_kgm3.data[i0],
            "cc_pct": cp.cc_pct.data[i0], "ch_m": cp.ch_m.data[i0]},
        "kind": crown.get(i0)}
    return out


def spot_stats(s, fid, data, trace):
    f = s._fires[fid]
    t0 = f.t_ignite_s
    spec = s._tile.spec
    cell_ha = spec.cell_m ** 2 / 1e4
    fuel = s._tile.fuel.data
    burn_sorted = sorted(v for v in data if v < INF)

    def fire_ha(t):
        return bisect.bisect_right(burn_sorted, t) * cell_ha

    wins = []
    src_of = {}
    for (idx, tu, mean, u20, d0, reach, fcells, land, tv) in trace:
        if tu > t0 + 6 * 3600.0:
            continue
        won = (land >= 0 and is_burnable(int(round(fuel[land])))
               and data[land] == tv)
        if won:
            e = {"src": idx, "tu": tu, "d": d0, "fire_ha": fire_ha(tu),
                 "own_cells": fcells}
            wins.append(e)
            src_of[land] = e
    depth = {}

    def chain(c):
        if c not in depth:
            e = src_of.get(c)
            depth[c] = 0 if e is None else 1 + chain(e["src"])
        return depth[c]

    out = {"spot_ignitions_6h": len(wins),
           "thrown_by_one_cell_fire": sum(1 for e in wins
                                          if e["own_cells"] == 1),
           "longest_spot_m": round(max((e["d"] for e in wins), default=0.0)),
           "max_chain_depth": max((chain(c) for c in src_of), default=0)}
    for km in (0.5, 1.0, 2.0):
        first = None
        for e in sorted(wins, key=lambda x: x["tu"]):
            if e["d"] > km * 1000.0:
                first = {"age_min": round((e["tu"] - t0) / 60.0, 1),
                         "fire_ha": round(e["fire_ha"], 1)}
                break
        out[f"first_spot_over_{km:g}km"] = first
    return out


def nominal_marks(s, fid, data):
    """acres / head km at 1 / 3 / 6 h and the fastest 1-h head gain of the
    nominal field alone (no ticks: the --generic strong-wind check)."""
    f = s._fires[fid]
    spec = s._tile.spec
    cell_ac = spec.cell_m ** 2 / 4046.8564224
    t0 = f.t_ignite_s
    order = sorted((v, i) for i, v in enumerate(data) if v < INF)
    times = [v for v, _ in order]
    heads = []
    m = 0.0
    for _v, i in order:
        m = max(m, geo.haversine_m(f.origin, geo.rc_to_latlon(
            spec, *divmod(i, spec.ncols))) / 1000.0)
        heads.append(m)

    def at(t_h):
        j = bisect.bisect_right(times, t0 + t_h * 3600.0)
        return round(j * cell_ac), (round(heads[j - 1], 2) if j else 0.0)
    hq = [at(q * 0.25)[1] for q in range(25)]
    return {"marks": {str(h): {"acres": at(h)[0], "head_km": at(h)[1]}
                      for h in (1.0, 3.0, 6.0)},
            "max_head_gain_km_per_h": round(max(hq[i + 4] - hq[i]
                                                for i in range(21)), 2)}


def run(scen, vname, ticks=24, generic=False):
    t_w = time.monotonic()
    cfg = dict(VARIANTS[vname])
    if generic:
        cfg["sim_weather_profile"] = "generic"
    s, fid = E.build(scen, cfg)
    data, strace, ctrace, solve_s = nominal(s, fid)
    w0 = s._wx.samples[0]
    res = {"scenario": scen, "variant": vname + ("@generic" if generic else ""),
           "cfg": cfg, "intensity": s._intensity,
           "weather0": {"wind_mph": round(w0.wind_speed_mps * 2.2369, 1),
                        "rh_pct": w0.rh_pct, "temp_c": w0.temp_c},
           "nominal_solve_s": round(solve_s, 2),
           "crown": crown_stats(s, fid, data, ctrace),
           "spotting": spot_stats(s, fid, data, strace)}
    if generic:
        res["nominal"] = nominal_marks(s, fid, data)
    else:
        res["demo"] = E.demo(s, fid, ticks, data)
    res["wall_s"] = round(time.monotonic() - t_w, 1)
    return res


def main(argv):
    out = OUT
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
        argv = [a for a in argv if a not in ("--out", out)]
    if "--merge" in argv:
        rows = {}
        for p in argv[argv.index("--merge") + 1:]:
            with open(p, encoding="utf-8") as fh:
                for r in json.load(fh):
                    rows[(r["scenario"], r["variant"])] = r
        if os.path.exists(OUT):
            with open(OUT, encoding="utf-8") as fh:
                for r in json.load(fh):
                    rows.setdefault((r["scenario"], r["variant"]), r)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(sorted(rows.values(), key=lambda r: (r["scenario"],
                                                            r["variant"])),
                      fh, indent=1)
        print(len(rows), "rows ->", OUT)
        return
    args = [a for a in argv if not a.startswith("--")]
    scens = (args[0].split(",") if args and args[0] != "all"
             else list(SCENARIOS))
    names = args[1].split(",") if len(args) > 1 else list(VARIANTS)
    try:
        with open(out, encoding="utf-8") as fh:
            store = {(r["scenario"], r["variant"]): r for r in json.load(fh)}
    except (OSError, ValueError):
        store = {}
    generic = "--generic" in argv
    for scen in scens:
        for v in names:
            res = run(scen, v, generic=generic)
            store[(scen, res["variant"])] = res
            c6 = res["crown"]["6h"]
            if generic:
                nm = res["nominal"]
                mk = nm["marks"]
                print(f"{scen:32s} {res['variant']:18s} "
                      f"{res['weather0']['wind_mph']:5.1f} mph | ac 1h "
                      f"{mk['1.0']['acres']:>6} 3h {mk['3.0']['acres']:>6} 6h "
                      f"{mk['6.0']['acres']:>6} | fastest "
                      f"{nm['max_head_gain_km_per_h']:5.2f} km/h | crown 6h "
                      f"act {c6['active']} pas {c6['passive']}", flush=True)
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(sorted(store.values(), key=lambda r: (
                        r["scenario"], r["variant"])), fh, indent=1)
                continue
            d = res["demo"]
            mk = d["marks"]
            au = d["audit"]
            print(f"{scen:32s} {v:10s} ac 1h {mk['1.0']['acres']:>6} 3h "
                  f"{mk['3.0']['acres']:>6} 6h {mk['6.0']['acres']:>6} | head "
                  f"6h {mk['6.0']['head_km']:5.2f} km fastest "
                  f"{d['max_head_gain_km_per_h']:5.2f} km/h | home "
                  f"{d['first_home_reached_h']} | viol "
                  f"{au['departure_violations_final']} ovr "
                  f"{au['crew_overrun_rows_sum']} served {au['served']} | "
                  f"crown 6h act {c6['active']} pas {c6['passive']} | "
                  f"same {d['nominal_matches_trace_solve']} | "
                  f"{res['wall_s']} s", flush=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(sorted(store.values(),
                                 key=lambda r: (r["scenario"], r["variant"])),
                          fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
