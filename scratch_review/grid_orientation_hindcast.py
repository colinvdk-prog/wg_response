"""Grid-orientation study: the real-fire hindcast with cfg.spread_any_angle
(2026-10-09; NOT product code).

Re-runs ONLY the physics stage of scratch_review/belief_vs_physics/bvp_run.py
(11 real 2026 fires, one perfect-forecast daily step at a time: the deployed
ensemble seeded with perimeter k at its own time under the archive weather,
scored at perimeter k+1) with the production posture CFG_PROD plus
spread_any_angle=True, and compares it with the stored legacy physics scores
of the 2026-09-27 run (docs/review/belief_vs_physics_2026-09-27.md).
`verify` re-runs the LEGACY arm on a few steps first and checks the stored
scores still reproduce (the engine has gained only default-OFF knobs since).

Inputs: out/bvp_data/ (incidents, weather, rows, geoms, lfps *.wgtile —
copied read-only from the study's cache). Stored legacy scores: env
BVP_LEGACY_RUNS (default: the original study worktree's out/bvp_data/runs).
Outputs: out/orient/hindcast/<key>_<k>.json, and with `collect`
scratch_review/grid_orientation_hindcast.json.

Usage: py -3 scratch_review/grid_orientation_hindcast.py verify
       py -3 scratch_review/grid_orientation_hindcast.py run [--workers 3]
       py -3 scratch_review/grid_orientation_hindcast.py collect
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

import bvp_run as R  # noqa: E402
from wgresponse import spread  # noqa: E402

OUT = os.path.join(ROOT, "out", "orient", "hindcast")
LEGACY = os.environ.get("BVP_LEGACY_RUNS", os.path.join(
    os.path.dirname(ROOT), "agent-a4ec5ffe8d93e5bdd", "out", "bvp_data", "runs"))
CFG_FIX = dataclasses.replace(R.CFG_PROD, spread_any_angle=True)
FIRES = ["aspenacres", "bobcatlakes", "border2", "grasshopper", "hagen",
         "littlegiant", "plaskett", "sandcreek", "sinlahekin", "timber",
         "willow"]


def step(args):
    key, k, cfg_name = args
    cfg = CFG_FIX if cfg_name == "fix" else R.CFG_PROD
    fire = R.get_fire(key)
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    tic = time.time()
    ens = spread.ensemble_arrival(fire.tile, [], fire.wx(k), t0, cfg,
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0)
    el = time.time() - tic
    out = {"key": key, "k": k, "arm": cfg_name, "info": info,
           "solve_s": el}
    if info["scoreable"]:
        t0m = R.seed_mask(ens, t0)
        t1m, burn = R.obs_masks(fire, k + 1)
        out["score"] = R.score(R.footprints(ens, t1), t0m, t1m, burn,
                               fire.tile.spec)
    return out


def legacy_score(key, k):
    p = os.path.join(LEGACY, key, f"phys_{k}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    return d.get("phys"), d.get("elapsed_s")


def steps_of(key):
    fire = R.get_fire(key)
    return [k for k in range(fire.n_steps()) if fire.step_info(k)["scoreable"]]


def main():
    mode = sys.argv[1]
    workers = 3
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    os.makedirs(OUT, exist_ok=True)
    if mode == "verify":
        for key in ("willow", "timber", "border2"):
            k = steps_of(key)[1]
            got = step((key, k, "legacy"))["score"]
            want = legacy_score(key, k)[0]
            same = all(got[f] == want[f] for f in ("area_p50_ac", "area_nom_ac",
                                                     "hit_p10", "hit_p50"))
            print(key, k, "legacy reproduces stored score:", same,
                  got["area_p50_ac"], want["area_p50_ac"], flush=True)
        return
    if mode == "run":
        tasks = [(key, k, "fix") for key in FIRES for k in steps_of(key)
                 if not os.path.exists(os.path.join(OUT, f"{key}_{k}.json"))]
        print(len(tasks), "steps to run", flush=True)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(step, tasks, chunksize=1):
                with open(os.path.join(OUT, f"{res['key']}_{res['k']}.json"),
                          "w", encoding="utf-8") as fh:
                    json.dump(res, fh, default=R._jdefault)
                print(res["key"], res["k"], f"{res['solve_s']:.1f} s", flush=True)
        return
    if mode == "collect":
        rows = []
        for path in sorted(glob.glob(os.path.join(OUT, "*.json"))):
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            leg = legacy_score(d["key"], d["k"])
            if not leg or not leg[0] or "score" not in d:
                continue
            rows.append({"key": d["key"], "k": d["k"],
                         "phase": d["info"]["phase"], "legacy": leg[0],
                         "fix": d["score"], "legacy_s": leg[1],
                         "fix_s": d["solve_s"]})

        def pooled(sel, arm):
            la = [r[arm]["log_area_p50"] for r in sel
                  if r[arm]["log_area_p50"] is not None]
            hp10 = [(r[arm]["n_hit_p10"], r[arm]["n_new_b"]) for r in sel
                    if r[arm]["n_hit_p10"] is not None]
            hp50 = [(r[arm]["n_hit_p50"], r[arm]["n_new_b"]) for r in sel
                    if r[arm]["n_hit_p50"] is not None]
            fa = [r[arm]["fa_p50_ac"] / r[arm]["obs_area_ac"] for r in sel
                  if r[arm]["obs_area_ac"] > 0]
            return {
                "steps": len(sel),
                "median_area_ratio_p50": math.exp(statistics.median(la)) if la else None,
                "p10_adequacy": (sum(a for a, _ in hp10) / sum(b for _, b in hp10)
                                 if hp10 and sum(b for _, b in hp10) else None),
                "p50_hit": (sum(a for a, _ in hp50) / sum(b for _, b in hp50)
                            if hp50 and sum(b for _, b in hp50) else None),
                "median_false_alarm_frac": statistics.median(fa) if fa else None,
                "edge_p50_steps": sum(1 for r in sel if r[arm]["edge_p50"]),
            }

        out = {"steps": len(rows), "by_phase": {}, "per_fire": {}}
        for name, sel in (("all", rows),
                          ("growing", [r for r in rows if r["phase"] == "growing"]),
                          ("moderate", [r for r in rows if r["phase"] == "moderate"]),
                          ("stalled", [r for r in rows if r["phase"] == "stalled"])):
            out["by_phase"][name] = {"legacy": pooled(sel, "legacy"),
                                     "fix": pooled(sel, "fix")}
        for key in FIRES:
            sel = [r for r in rows if r["key"] == key]
            if sel:
                out["per_fire"][key] = {"legacy": pooled(sel, "legacy"),
                                        "fix": pooled(sel, "fix")}
        ls = [r["legacy_s"] for r in rows if r["legacy_s"]]
        fs = [r["fix_s"] for r in rows]
        out["solve_s"] = {"legacy_total": sum(ls), "fix_total": sum(fs),
                          "legacy_median": statistics.median(ls),
                          "fix_median": statistics.median(fs)}
        later = sum(1 for r in rows
                    if r["fix"]["hit_p10"] is not None
                    and r["legacy"]["hit_p10"] is not None
                    and r["fix"]["hit_p10"] < r["legacy"]["hit_p10"] - 1e-12)
        out["steps_p10_adequacy_lower"] = later
        with open(os.path.join(HERE, "grid_orientation_hindcast.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print(json.dumps(out["by_phase"], indent=1))
        print(json.dumps(out["solve_s"]), "p10 lower on", later, "steps")


if __name__ == "__main__":
    main()
