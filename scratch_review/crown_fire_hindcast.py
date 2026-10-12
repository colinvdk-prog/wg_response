"""Crown-fire study: the 11-fire real hindcast (2026-10-12; NOT product
code; docs/review/crown_fire_model_2026-10-12.md section 3.3).

Re-runs ONLY the physics stage of the 2026-09-27 belief-vs-physics study
(scratch_review/belief_vs_physics/bvp_run.py: 11 real 2026 fires, one
perfect-forecast daily step at a time — the deployed engine ensemble seeded
with perimeter k at its own time under the archive weather, scored at
perimeter k+1) with the LANDFIRE canopy attached to each fire's terrain tile
(scratch_review/crown_fire_hindcast_canopy.py) and these arms:

  deployed      the production posture CFG_PROD (timber floor) — the stored
                scores of the 2026-09-27 run (verified to reproduce)
  grassonly     CFG_PROD + ros_floor_grass_only — the stored scores of the
                study's side experiment (results_phys_grassonly.json,
                verified to reproduce)
  crown         CFG_PROD + ros_floor_grass_only + crown_fire   (run here)
  crown_dep     CFG_PROD + crown_fire (timber floor kept)      (run here)

Scores (bvp_run.score, at t_{k+1} against perimeter k+1): the P50 area ratio
(footprint / burned), P10 adequacy (share of the newly burned cells inside
the P10 footprint — crew safety), P50 hit rate, false-alarm area, by the
observed growth phase. Plus, per step, the share of the nominal forecast's
new area that crowned (crown_trace).

Usage: py -3 scratch_review/crown_fire_hindcast.py verify
       py -3 scratch_review/crown_fire_hindcast.py run [--arms crown,crown_dep] [--workers 4]
       py -3 scratch_review/crown_fire_hindcast.py collect
Inputs: out/bvp_data (copied read-only from the 2026-09-27 study's cache);
stored legacy scores: env BVP_LEGACY_RUNS (default the original study
worktree's out/bvp_data/runs). Outputs: out/crown/hindcast/<arm>_<key>_<k>.json
and, with collect, scratch_review/crown_fire_hindcast.json.
"""
from __future__ import annotations

import dataclasses
import glob
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "belief_vs_physics"))
sys.path.insert(0, ROOT)

import bvp_fetch as F  # noqa: E402
import bvp_run as R  # noqa: E402
from wgresponse import rothermel, spread  # noqa: E402
from wgresponse.terrain import LfpsTerrain, attach_canopy  # noqa: E402

OUT = os.path.join(ROOT, "out", "crown", "hindcast")
LEGACY = os.environ.get("BVP_LEGACY_RUNS", os.path.join(
    os.path.dirname(ROOT), "agent-a4ec5ffe8d93e5bdd", "out", "bvp_data", "runs"))
GRASSONLY = os.path.join(HERE, "belief_vs_physics",
                         "results_phys_grassonly.json")
ARMS = {
    "deployed": R.CFG_PROD,
    "grassonly": dataclasses.replace(R.CFG_PROD, ros_floor_grass_only=True),
    "crown": dataclasses.replace(R.CFG_PROD, ros_floor_grass_only=True,
                                 crown_fire=True),
    "crown_dep": dataclasses.replace(R.CFG_PROD, crown_fire=True),
}
FIRES = ["aspenacres", "bobcatlakes", "border2", "grasshopper", "hagen",
         "littlegiant", "plaskett", "sandcreek", "sinlahekin", "timber",
         "willow"]
_CANOPY_DONE = set()


def fire_with_canopy(key):
    fire = R.get_fire(key)
    if key not in _CANOPY_DONE:
        lf = LfpsTerrain(cache_dir=os.path.join(F.DATA_DIR, "lfps"),
                         canopy_layer_list=LfpsTerrain.DEFAULT_CANOPY_LAYERS)
        cpath = lf.canopy_cache_path(fire.bbox, fire.cell_m)
        if not os.path.exists(cpath):
            raise RuntimeError(f"{key}: no cached canopy ({cpath})")
        fire._tile = attach_canopy(fire.tile, lf.canopy(fire.bbox, fire.cell_m))
        _CANOPY_DONE.add(key)
    return fire


def step(args):
    key, k, arm = args
    cfg = ARMS[arm]
    fire = fire_with_canopy(key)
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    tic = time.time()
    ens = spread.ensemble_arrival(fire.tile, [], fire.wx(k), t0, cfg,
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0)
    el = time.time() - tic
    out = {"key": key, "k": k, "arm": arm, "info": info, "solve_s": el}
    if info["scoreable"]:
        t0m = R.seed_mask(ens, t0)
        t1m, burn = R.obs_masks(fire, k + 1)
        out["score"] = R.score(R.footprints(ens, t1), t0m, t1m, burn,
                               fire.tile.spec)
        if cfg.crown_fire:
            # the nominal member's fire types over the step's new area
            trace = {}
            nom = spread.compute_arrival(fire.tile, [], fire.wx(k), t0, cfg,
                                         incident_id=fire.irwin, member_k=0,
                                         perimeter=fire.seed_rings(k),
                                         perimeter_at_s=t0,
                                         crown_trace=trace).data
            assert nom == ens.members[0].data
            new = [i for i, v in enumerate(nom) if t0 < v <= t1]
            kinds = {"surface": 0, "passive": 0, "active": 0}
            for i in new:
                kk = trace.get(i)
                if kk == rothermel.CROWN_ACTIVE:
                    kinds["active"] += 1
                elif kk == rothermel.CROWN_PASSIVE:
                    kinds["passive"] += 1
                else:
                    kinds["surface"] += 1
            out["nominal_new_cells_by_kind"] = kinds
    return out


def stored(arm, key, k):
    if arm == "deployed":
        p = os.path.join(LEGACY, key, f"phys_{k}.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh).get("phys")
    if arm == "grassonly":
        with open(GRASSONLY, encoding="utf-8") as fh:
            d = json.load(fh)
        return (d["fires"].get(key, {}).get(str(k)) or {}).get("phys")
    p = os.path.join(OUT, f"{arm}_{key}_{k}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh).get("score")


def steps_of(key):
    fire = R.get_fire(key)
    return [k for k in range(fire.n_steps()) if fire.step_info(k)["scoreable"]]


def pooled(rows, arm):
    sel = [r for r in rows if r.get(arm)]
    la = [r[arm]["log_area_p50"] for r in sel
          if r[arm]["log_area_p50"] is not None]
    hp10 = [(r[arm]["n_hit_p10"], r[arm]["n_new_b"]) for r in sel
            if r[arm]["n_hit_p10"] is not None]
    hp50 = [(r[arm]["n_hit_p50"], r[arm]["n_new_b"]) for r in sel
            if r[arm]["n_hit_p50"] is not None]
    fa = [r[arm]["fa_p50_ac"] / r[arm]["obs_area_ac"] for r in sel
          if r[arm]["obs_area_ac"] > 0]
    s10 = sum(b for _, b in hp10)
    s50 = sum(b for _, b in hp50)
    return {
        "steps": len(sel),
        "median_area_ratio_p50": math.exp(statistics.median(la)) if la else None,
        "p10_adequacy": sum(a for a, _ in hp10) / s10 if s10 else None,
        "p50_hit": sum(a for a, _ in hp50) / s50 if s50 else None,
        "median_false_alarm_frac": statistics.median(fa) if fa else None,
        "edge_p50_steps": sum(1 for r in sel if r[arm]["edge_p50"]),
    }


def main(argv):
    mode = argv[0]
    workers = 4
    if "--workers" in argv:
        workers = int(argv[argv.index("--workers") + 1])
    arms = ["crown", "crown_dep"]
    if "--arms" in argv:
        arms = argv[argv.index("--arms") + 1].split(",")
    os.makedirs(OUT, exist_ok=True)
    if mode == "verify":
        # the two stored arms still reproduce on this engine (canopy
        # attached, crown off) — first and middle scored step of 3 fires
        for key in ("willow", "timber", "border2"):
            ks = steps_of(key)
            for k in (ks[0], ks[len(ks) // 2]):
                for arm in ("deployed", "grassonly"):
                    got = step((key, k, arm))["score"]
                    want = stored(arm, key, k)
                    same = all(got[f] == want[f] for f in (
                        "area_p50_ac", "area_nom_ac", "hit_p10", "hit_p50"))
                    print(key, k, arm, "reproduces stored:", same,
                          got["area_p50_ac"], want["area_p50_ac"], flush=True)
        return
    fires = FIRES
    if "--fires" in argv:
        fires = argv[argv.index("--fires") + 1].split(",")
    if mode == "run":
        tasks = [(key, k, arm) for arm in arms for key in fires
                 for k in steps_of(key)
                 if not os.path.exists(os.path.join(
                     OUT, f"{arm}_{key}_{k}.json"))]
        print(len(tasks), "steps to run", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(step, tasks, chunksize=1):
                with open(os.path.join(
                        OUT, f"{res['arm']}_{res['key']}_{res['k']}.json"),
                        "w", encoding="utf-8") as fh:
                    json.dump(res, fh, default=R._jdefault)
                print(res["arm"], res["key"], res["k"],
                      f"{res['solve_s']:.1f} s",
                      res.get("nominal_new_cells_by_kind"), flush=True)
        return
    if mode == "collect":
        rows = []
        kinds_tot = {}
        solve = {}
        for key in FIRES:
            for k in steps_of(key):
                row = {"key": key, "k": k}
                for arm in ARMS:
                    row[arm] = stored(arm, key, k)
                p = os.path.join(OUT, f"crown_{key}_{k}.json")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                    row["phase"] = d["info"]["phase"]
                    kk = d.get("nominal_new_cells_by_kind") or {}
                    row["crown_kinds"] = kk
                    for name, v in kk.items():
                        kinds_tot.setdefault(row["phase"], {})
                        kinds_tot[row["phase"]][name] = \
                            kinds_tot[row["phase"]].get(name, 0) + v
                    solve.setdefault("crown", []).append(d["solve_s"])
                else:
                    with open(GRASSONLY, encoding="utf-8") as fh:
                        row["phase"] = json.load(fh)["fires"][key][str(k)]["phase"]
                rows.append(row)
        out = {"steps": len(rows), "by_phase": {}, "per_fire": {},
               "crown_kinds_new_cells_by_phase": kinds_tot}
        for name, sel in (("all", rows),
                          ("growing", [r for r in rows if r["phase"] == "growing"]),
                          ("moderate", [r for r in rows if r["phase"] == "moderate"]),
                          ("stalled", [r for r in rows if r["phase"] == "stalled"])):
            out["by_phase"][name] = {arm: pooled(sel, arm) for arm in ARMS}
        for key in FIRES:
            sel = [r for r in rows if r["key"] == key]
            out["per_fire"][key] = {arm: pooled(sel, arm) for arm in ARMS}
        later = {}
        for arm in ("crown", "crown_dep"):
            later[arm] = {"vs_grassonly": sum(
                1 for r in rows if r.get(arm) and r.get("grassonly")
                and r[arm]["hit_p10"] is not None
                and r["grassonly"]["hit_p10"] is not None
                and r[arm]["hit_p10"] < r["grassonly"]["hit_p10"] - 1e-12),
                "vs_deployed": sum(
                1 for r in rows if r.get(arm) and r.get("deployed")
                and r[arm]["hit_p10"] is not None
                and r["deployed"]["hit_p10"] is not None
                and r[arm]["hit_p10"] < r["deployed"]["hit_p10"] - 1e-12)}
        out["steps_p10_adequacy_lower"] = later
        # the per-step tail: each arm's lowest step and every step where an
        # arm's P10 held less than 90 % of the newly burned ground
        import datetime as _dt

        def _day(t):
            return _dt.datetime.fromtimestamp(
                t, _dt.timezone.utc).strftime("%b %d")

        info = {}
        for r in rows:
            p = os.path.join(OUT, f"crown_{r['key']}_{r['k']}.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    inf = json.load(fh)["info"]
                info[(r["key"], r["k"])] = (f"{_day(inf['t0_s'])} → "
                                            f"{_day(inf['t1_s'])}")
        tail = {}
        for arm in ARMS:
            vals = [(r[arm]["hit_p10"], r["key"], r["k"]) for r in rows
                    if r.get(arm) and r[arm]["hit_p10"] is not None]
            lo = min(vals) if vals else None
            tail[arm] = {
                "min": None if lo is None else {
                    "p10_adequacy": lo[0], "fire": lo[1], "k": lo[2],
                    "dates": info.get((lo[1], lo[2]))},
                "steps_under_90": sum(1 for v in vals if v[0] < 0.9)}
        under = []
        for r in rows:
            got = {arm: r[arm]["hit_p10"] for arm in ARMS
                   if r.get(arm) and r[arm]["hit_p10"] is not None}
            if any(v < 0.9 for a, v in got.items() if a != "grassonly"):
                under.append({"fire": r["key"], "k": r["k"],
                              "phase": r["phase"],
                              "dates": info.get((r["key"], r["k"])),
                              "p10_adequacy": got})
        out["p10_tail"] = tail
        out["p10_steps_under_90_excl_grassonly"] = under
        if solve.get("crown"):
            out["crown_solve_s_median"] = statistics.median(solve["crown"])
        with open(os.path.join(HERE, "crown_fire_hindcast.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        for ph, d in out["by_phase"].items():
            print(ph, json.dumps({a: {k: (round(v, 4) if isinstance(v, float) else v)
                                      for k, v in s.items()}
                                  for a, s in d.items()}))
        print("p10 lower:", later, "kinds:", kinds_tot)


if __name__ == "__main__":
    main(sys.argv[1:])
