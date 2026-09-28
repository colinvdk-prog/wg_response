"""Fire belief v2 (2026-09-28) -- the replay, the threshold search and the
before/after on the 11 real fires of the 2026-09-27 study.

Reuses bvp_run.py's cached physics (out/bvp_data/runs[_wide]/<fire>/phys_k:
the deployed ensemble seeded at each daily capture, its nominal growth
curve, its footprints at the next capture) and drives the REAL
wgresponse.fire_belief store, exactly as bvp_run.run_belief does, under any
ResponseConfig. The LEARNING (posterior, resets) does not depend on the new-
weather or rapid rules; only what before_spread() does with it does. So a
belief-APPLIED forecast at step k is fully determined by (k, h_eff, rapid):
h_eff = min(first new-pattern hour, ceil(step length)) -- hours at or after
t_{k+1} cannot move a footprint scored at t_{k+1} (frozen departure-hour
edge law: an arrival <= t1 departed before t1) -- and the rapid flag. Each
such applied ensemble is computed once and cached (out/bvp_data/
v2cache[_wide]/<fire>/k{k}_h{h}_r{0|1}.json: the guarded and unguarded
scores), seeded from the as-built / what-if runs already on disk.

Commands (repo root, py -3):
  grid     replay every daily-rule threshold set x {legacy, v2} rapid on
           the harness maps; compute the missing applied ensembles; write
           out/bvp_data/v2_grid.json (per config, per step; gitignored)
           and v2_grid_summary.json (per config; committed)
  lofo     leave-one-fire-out threshold choice from out/bvp_data/v2_grid.json;
           writes v2_lofo.json and prints the folds + held-out table
  final    physics vs as built vs v2 (the chosen thresholds) vs both-rules-
           off, harness AND wide maps (--variant wide for the wide run);
           writes v2_final[_wide].json
  rapid    rapid-rule statistics (legacy vs calibrated vs +persistence)
           on the daily hindcast series and on the full live-like series
           (every capture >= 3 h apart); writes v2_rapid.json
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_fetch as F  # noqa: E402
import bvp_run as R  # noqa: E402
from wgresponse import fire_belief as FB  # noqa: E402
from wgresponse import spread  # noqa: E402

H_S = 3600.0
FIRES = list(F.load_fires())
GRID_PATH = os.path.join(F.DATA_DIR, "v2_grid.json")
PHASES = ("growing", "moderate", "stalled")

# the belief as built (legacy rules) and the v2 rapid rule
AS_BUILT = {}
RAPID_V2 = {"fire_belief_rapid_noise": "calibrated",
            "fire_belief_rapid_persist_windows": 2}
NO_RULES = {"fire_belief_novel_severity_ratio": 1e9,
            "fire_belief_novel_min_wind_mps": 1e9,
            "fire_belief_rapid_acres_per_day": 1e12,
            "fire_belief_rapid_frac_per_day": 1e12}
# the daily-rule threshold grid (round, explainable values)
GRID_RATIO = (1.25, 1.5, 2.0)
GRID_SHIFT = (45.0, 60.0, 90.0, 120.0)
GRID_WMIN = (3.0, 4.0, 5.0, 6.0)


def cfg_of(kw: dict):
    return dataclasses.replace(R.CFG_BELIEF, **kw)


def daily_kw(ratio, shift, wmin, rapid_v2=True) -> dict:
    kw = {"fire_belief_novelty": "daily",
          "fire_belief_novel_day_severity_ratio": ratio,
          "fire_belief_novel_day_shift_deg": shift,
          "fire_belief_novel_day_min_wind_mps": wmin}
    if rapid_v2:
        kw.update(RAPID_V2)
    return kw


def cfg_name(kw: dict) -> str:
    if not kw:
        return "as_built"
    if kw == NO_RULES:
        return "no_rules"
    parts = []
    c = cfg_of(kw)
    if kw.get("fire_belief_novelty") == "daily":
        parts.append(f"daily_r{c.fire_belief_novel_day_severity_ratio:g}"
                     f"_s{c.fire_belief_novel_day_shift_deg:g}"
                     f"_w{c.fire_belief_novel_day_min_wind_mps:g}")
    if kw.get("fire_belief_rapid_noise") == "calibrated":
        parts.append(f"cal{kw.get('fire_belief_rapid_perim_noise_k', 1.0):g}")
    if kw.get("fire_belief_rapid_persist_windows"):
        parts.append(f"p{kw['fire_belief_rapid_persist_windows']}")
    return "+".join(parts) or json.dumps(kw, sort_keys=True)


# ---------------------------------------------------------------------------
# the belief replay (bvp_run.run_belief, any cfg, no files)
# ---------------------------------------------------------------------------

_CURVES: dict = {}


def _curve(key: str, k: int) -> dict:
    ck = (R.variant(), key, k)
    c = _CURVES.get(ck)
    if c is None:
        c = _CURVES[ck] = F.read_json(os.path.join(
            R.run_dir(key), f"phys_{k}.json"))["curve"]
    return c


def replay(key: str, kw: dict) -> list:
    cfg = cfg_of(kw)
    fire = R.get_fire(key)
    store = FB.FireBeliefStore(persistent=False)
    n = len(fire.snaps)
    rows = []
    for k in range(n):
        t = float(fire.snaps[k]["t_s"])
        inc = fire.incident(k)
        bel = store.belief(fire.irwin)
        if bel is not None and k > 0:
            wx_prev = fire.wx(k - 1)
            h = math.floor(fire.snaps[k - 1]["t_s"] / H_S) * H_S + H_S
            while h < t:
                store._log_weather(bel, wx_prev, h, cfg)
                h += H_S
        store.observe([inc], t, cfg)
        bel = store.belief(fire.irwin)
        fn = store.before_spread(inc, fire.tile, fire.wx(k), t, cfg)
        pending = bel.pending_nominal
        bel.pending_nominal = None
        h_new = (None if bel.novel_from_s is None
                 else int(round((bel.novel_from_s - t) / H_S)))
        post = None
        if fn is not None:
            post = store._posterior_from(store._evidence(bel, t, cfg), cfg)
        if k < n - 1:
            R._pin_curve(bel, _curve(key, k), t)
        st = store.statuses([inc], t, cfg)[fire.irwin]
        rows.append({
            "k": k, "t_s": t, "state": st.state, "applied": fn is not None,
            "h_new": h_new, "rapid": bool(st.rapid),
            "rapid_reasons": [r for r in st.reasons
                              if "growth" in r or "young" in r or "reset" in r],
            "novel_reason": bel.novel_reason,
            "mult_p10": st.mult_p10, "mult_p50": st.mult_p50,
            "mult_p90": st.mult_p90,
            "nominal_first": (pending[0] if pending else None),
            "p": list(post.p) if post is not None else None,
            "cum": list(post.cum) if post is not None else None})
    return rows


def replay_task(key: str, kw: dict) -> dict:
    t0 = time.time()
    return {"key": key, "kw": kw, "rows": replay(key, kw),
            "s": time.time() - t0}


# ---------------------------------------------------------------------------
# the applied-ensemble cache
# ---------------------------------------------------------------------------

def cache_dir(key: str) -> str:
    d = os.path.join(F.DATA_DIR, "v2cache_wide" if R.variant() else
                     "v2cache", key)
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(key: str, k: int, h_eff: int, rapid: bool) -> str:
    return os.path.join(cache_dir(key), f"k{k}_h{h_eff}_r{int(rapid)}.json")


def h_cap(fire, k: int) -> int:
    info = fire.step_info(k)
    return int(math.ceil(info["dt_h"] - 1e-9))


def eff_hour(fire, k: int, h_new) -> int:
    cap = h_cap(fire, k)
    return cap if h_new is None else min(int(h_new), cap)


def cache_task(key: str, k: int, h_eff: int, rapid: bool, p: list,
               cum: list) -> dict:
    """One belief-APPLIED ensemble (the member_hour_scale before_spread
    builds for this posterior, first new hour h_eff and rapid flag),
    scored guarded + unguarded at t_{k+1}."""
    t0c = time.time()
    fire = R.get_fire(key)
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    cfg = R.CFG_BELIEF
    post = FB.Posterior.__new__(FB.Posterior)
    post.p, post.cum = list(p), list(cum)
    fn = FB.member_scale_fn(post, int(h_eff), FB.horizon_hours(cfg), cfg,
                            bool(rapid))
    ens = spread.ensemble_arrival(fire.tile, [], fire.wx(k), t0, cfg,
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0, member_hour_scale=fn)
    ph = np.load(os.path.join(R.run_dir(key), f"phys_{k}.npz"))
    t0m = R.seed_mask(ens, t0)
    t1m, burn = R.obs_masks(fire, k + 1)
    fp_u = R.footprints(ens, t1)
    fp_g = dict(fp_u)
    fp_g["p10"] = fp_u["p10"] | ph["p10"]
    out = {"key": key, "k": k, "h_eff": h_eff, "rapid": bool(rapid),
           "guarded": R.score(fp_g, t0m, t1m, burn, fire.tile.spec),
           "unguarded": R.score(fp_u, t0m, t1m, burn, fire.tile.spec),
           "elapsed_s": time.time() - t0c}
    R.write_json(cache_path(key, k, h_eff, rapid), out)
    return {"key": key, "k": k, "s": out["elapsed_s"]}


def seed_cache() -> int:
    """Seed the cache from the runs already on disk (as built, and the
    nonovel / norapid / free what-ifs): each apply file is the applied
    ensemble of its belief file's (h_new, rapid)."""
    n = 0
    for key in FIRES:
        fire = R.get_fire(key)
        rd = R.run_dir(key)
        for suf in ("", "_nonovel", "_norapid", "_free"):
            bp = os.path.join(rd, f"belief{suf}.json")
            if not os.path.exists(bp):
                continue
            b = F.read_json(bp)
            for row in b["rows"][:-1]:
                k = row["k"]
                ap = os.path.join(rd, f"apply{suf}_{k}.json")
                if not (row["applied"] and os.path.exists(ap)):
                    continue
                a = row["apply"]
                he = eff_hour(fire, k, a["h_new"] if a["h_new"] <=
                              FB.horizon_hours(R.CFG_BELIEF) else None)
                cp = cache_path(key, k, he, a["rapid"])
                if os.path.exists(cp):
                    continue
                d = F.read_json(ap)
                R.write_json(cp, {"key": key, "k": k, "h_eff": he,
                                  "rapid": bool(a["rapid"]),
                                  "guarded": d["guarded"],
                                  "unguarded": d["unguarded"],
                                  "seeded_from": os.path.basename(ap)})
                n += 1
    return n


def fill_cache(replays: dict, workers: int) -> None:
    """Compute every (fire, k, h_eff, rapid) applied ensemble the replays
    need that is not cached yet."""
    need = {}
    for (key, _name), rows in replays.items():
        fire = R.get_fire(key)
        for row in rows[:-1]:
            k = row["k"]
            if not (row["applied"] and fire.step_info(k)["scoreable"]):
                continue
            he = eff_hour(fire, k, row["h_new"])
            if he <= 0:
                continue
            ck = (key, k, he, row["rapid"])
            if ck in need or os.path.exists(cache_path(*ck)):
                continue
            need[ck] = (row["p"], row["cum"])
    tasks = [(key, k, he, rp, p, c) for (key, k, he, rp), (p, c) in
             need.items()]
    print(f"[cache] {len(tasks)} applied ensembles to compute", flush=True)
    if tasks:
        R._pool_run(tasks, cache_task, workers, "cache")


# ---------------------------------------------------------------------------
# scoring a config
# ---------------------------------------------------------------------------

_PHYS: dict = {}


def phys_scores(key: str, k: int):
    ck = (R.variant(), key, k)
    v = _PHYS.get(ck)
    if v is None:
        d = F.read_json(os.path.join(R.run_dir(key), f"phys_{k}.json"))
        v = _PHYS[ck] = (d["info"], d.get("phys"))
    return v


def step_table(key: str, rows: list) -> list:
    """Per scored step: phase, physics scores, the config's applied scores
    (physics when the belief did not apply) and the belief's decision."""
    fire = R.get_fire(key)
    out = []
    for row in rows[:-1]:
        k = row["k"]
        info, phys = phys_scores(key, k)
        if not info["scoreable"]:
            continue
        he = eff_hour(fire, k, row["h_new"])
        if row["applied"] and he > 0:
            c = F.read_json(cache_path(key, k, he, row["rapid"]))
            g, u = c["guarded"], c["unguarded"]
        else:
            g = u = phys
        out.append({"fire": key, "k": k, "t0_s": info["t0_s"],
                    "phase": info["phase"], "dt_h": info["dt_h"],
                    "growth_pct_day": 100.0 * info["growth_frac_per_day"],
                    "gis0": info["gis0_ac"], "gis1": info["gis1_ac"],
                    "applied": row["applied"], "state": row["state"],
                    "h_new": row["h_new"], "h_eff": he, "rapid": row["rapid"],
                    "novel_reason": row["novel_reason"],
                    "mult_p50": row["mult_p50"],
                    "phys": phys, "bel": g, "belu": u})
    return out


def summarize(steps: list) -> dict:
    def med(v):
        return statistics.median(v) if v else None

    def arm(a):
        la = [abs(s[a]["log_area_p50"]) for s in steps]
        mono = [s for s in steps if s[a]["monotone"] and s[a]["n_new_b"] > 0]
        nc = sum(s[a]["n_new_b"] for s in mono)
        late = [s for s in mono if s[a]["hit_p50"] < 0.5]
        return {
            "n": len(steps),
            "area_err_median": med(la),
            "area_factor_median": math.exp(med(la)) if la else None,
            "p50_catch_cells": (sum(s[a]["n_hit_p50"] for s in mono) / nc
                                if nc else None),
            "p10_adequacy_cells": (sum(s[a]["n_hit_p10"] for s in mono) / nc
                                   if nc else None),
            "late_steps": len(late),
            "late_steps_grow2": sum(1 for s in late
                                    if s["growth_pct_day"] >= 2.0),
            "late_under25": sum(1 for s in late if s[a]["hit_p50"] < 0.25),
            "fa_rel_median": med([s[a]["fa_p50_ac"] / s[a]["obs_area_ac"]
                                  for s in steps if s[a]["obs_area_ac"] > 0]),
            "missed_new_ac": sum((1.0 - s[a]["hit_p50"]) * s[a]["obs_new_ac"]
                                 for s in mono),
        }
    applied = [s for s in steps if s["applied"] and s["h_eff"] > 0]
    hn = [min(s["h_new"], 97) if s["h_new"] is not None else 97
          for s in steps if s["state"] == "learned"]
    cov = [min(1.0, (s["h_eff"] if s["applied"] else 0) / s["dt_h"])
           if s["state"] == "learned" else None for s in steps]
    cov = [c for c in cov if c is not None]
    return {"phys": arm("phys"), "bel": arm("bel"), "belu": arm("belu"),
            "n_applied": len(applied),
            "n_rapid": sum(1 for s in steps if s["rapid"]),
            "h_new_median": med(hn),
            "full_window_share": (sum(1 for c in cov if c >= 0.999) / len(cov)
                                  if cov else None),
            "window_share_median": med(cov),
            "better": sum(1 for s in steps if abs(s["bel"]["log_area_p50"])
                          < abs(s["phys"]["log_area_p50"]) - 1e-9),
            "worse": sum(1 for s in steps if abs(s["bel"]["log_area_p50"])
                         > abs(s["phys"]["log_area_p50"]) + 1e-9)}


def by_phase(steps: list) -> dict:
    out = {ph: summarize([s for s in steps if s["phase"] == ph])
           for ph in PHASES if any(s["phase"] == ph for s in steps)}
    out["all"] = summarize(steps)
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def run_replays(configs: list, workers: int) -> dict:
    """{(fire, name): rows} for every config, in a process pool."""
    tasks = [(key, kw) for kw in configs for key in FIRES]
    out = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(replay_task, *t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            out[(r["key"], cfg_name(r["kw"]))] = r["rows"]
            if i % 25 == 0 or i == len(tasks):
                print(f"[replay] {i}/{len(tasks)} ({time.time() - t0:.0f} s)",
                      flush=True)
    return out


def grid_configs() -> list:
    cfgs = [AS_BUILT, NO_RULES, dict(RAPID_V2),
            daily_kw(1.5, 90.0, 5.0, rapid_v2=False)]
    for r, s, w in itertools.product(GRID_RATIO, GRID_SHIFT, GRID_WMIN):
        cfgs.append(daily_kw(r, s, w, rapid_v2=True))
    return cfgs


def cmd_grid(args) -> int:
    print(f"[grid] seeded {seed_cache()} cache entries from existing runs")
    configs = grid_configs()
    replays = run_replays(configs, args.workers)
    fill_cache(replays, args.workers)
    res = {}
    for kw in configs:
        name = cfg_name(kw)
        steps = []
        for key in FIRES:
            steps += step_table(key, replays[(key, name)])
        res[name] = {"kw": kw, "steps": [{k: v for k, v in s.items()
                                          if k not in ("phys", "bel", "belu")}
                                         | {"phys": _slim(s["phys"]),
                                            "bel": _slim(s["bel"]),
                                            "belu": _slim(s["belu"])}
                                         for s in steps],
                     "summary": by_phase(steps)}
        a = res[name]["summary"]["all"]
        print(f"{name:40s} area {a['bel']['area_factor_median']:.2f}x  "
              f"catch {100 * a['bel']['p50_catch_cells']:.0f}%  late "
              f"{a['bel']['late_steps']:2d} ({a['bel']['late_steps_grow2']:2d} "
              f"grow>=2%)  fa {100 * a['bel']['fa_rel_median']:.0f}%  "
              f"h_new med {a['h_new_median']}  full {100 * (a['full_window_share'] or 0):.0f}%",
              flush=True)
    # the per-step grid (17 MB) stays in the gitignored data dir; the per-
    # config summaries are committed beside the scripts
    path = GRID_PATH
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, sort_keys=True, separators=(",", ":"))
    with open(os.path.join(_HERE, "v2_grid_summary.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({n: {"kw": r["kw"], "summary": r["summary"]}
                   for n, r in res.items()}, f, indent=1, sort_keys=True)
    print("wrote", path, "and v2_grid_summary.json")
    return 0


_SLIM = ("log_area_p50", "hit_p50", "hit_p10", "n_hit_p50", "n_hit_p10",
         "n_new_b", "monotone", "fa_p50_ac", "obs_area_ac", "obs_new_ac",
         "area_p50_ac", "grow_p50_ac", "edge_p50")


def _slim(m: dict) -> dict:
    return {k: m[k] for k in _SLIM if k in m}


def objective(steps: list, base_late_grow2: int) -> tuple:
    """LOFO training objective: the median P50 area error, subject to no
    more late-P50 steps on growing ground (catch < 50 % on steps growing
    >= 2 %/day) than the belief as built on the same fires. Infeasible
    configs rank after every feasible one (then by fewer late steps)."""
    s = summarize(steps)["bel"]
    feasible = s["late_steps_grow2"] <= base_late_grow2
    return (0 if feasible else 1,
            0 if feasible else s["late_steps_grow2"], s["area_err_median"])


def cmd_lofo(args) -> int:
    grid = json.load(open(GRID_PATH))
    base = grid["as_built"]["steps"]
    # the v2 family: the daily rule's threshold sets, all with the v2 rapid
    # rule (the legacy-rapid daily config is a reference arm, not a candidate)
    cands = [n for n in grid if n.startswith("daily_") and
             n.endswith("+cal1+p2")]
    folds = []
    held = []
    for f in FIRES:
        train_base = [s for s in base if s["fire"] != f]
        bl = summarize(train_base)["bel"]["late_steps_grow2"]
        objs = {n: objective([s for s in grid[n]["steps"] if s["fire"] != f],
                             bl) for n in cands}
        best = min(cands, key=lambda n: (objs[n], n))
        ties = sorted(n for n in cands if objs[n] == objs[best])
        test = [s for s in grid[best]["steps"] if s["fire"] == f]
        held += test
        folds.append({"held_out": f, "chosen": best, "ties": ties,
                      "train_base_late_grow2": bl,
                      "train": summarize([s for s in grid[best]["steps"]
                                          if s["fire"] != f])["bel"],
                      "test": summarize(test)["bel"] if test else None})
        print(f"[lofo] hold out {f:12s} -> {best}", flush=True)
    counts: dict = {}
    for fo in folds:
        counts[fo["chosen"]] = counts.get(fo["chosen"], 0) + 1
    # how often each threshold VALUE is among a fold's best (ties included)
    val_counts = {"ratio": {}, "shift": {}, "wmin": {}}
    for fo in folds:
        seen = {"ratio": set(), "shift": set(), "wmin": set()}
        for n in fo["ties"]:
            r, s, w = n.split("+")[0].split("_")[1:]
            seen["ratio"].add(r[1:])
            seen["shift"].add(s[1:])
            seen["wmin"].add(w[1:])
        for k2, vals in seen.items():
            for v in vals:
                val_counts[k2][v] = val_counts[k2].get(v, 0) + 1
    print("threshold values among each fold's best (ties included):",
          val_counts)
    out = {"folds": folds, "choice_counts": counts,
           "value_counts_ties_included": val_counts,
           "held_out_summary": by_phase(held),
           "as_built_summary": grid["as_built"]["summary"],
           "no_rules_summary": grid["no_rules"]["summary"]}
    # sensitivity: every candidate's pooled score (all fires)
    out["sensitivity"] = {n: {"all": grid[n]["summary"]["all"]["bel"],
                              "h_new_median": grid[n]["summary"]["all"][
                                  "h_new_median"],
                              "full_window_share": grid[n]["summary"]["all"][
                                  "full_window_share"]}
                          for n in cands}
    with open(os.path.join(_HERE, "v2_lofo.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print("choices:", counts)
    return 0


def cmd_final(args) -> int:
    """physics / as built / v2 / both-rules-off on this domain variant."""
    print(f"[final] seeded {seed_cache()} cache entries ({R.variant() or 'harness'})")
    v2 = daily_kw(args.ratio, args.shift, args.wmin, rapid_v2=True)
    configs = [AS_BUILT, v2, NO_RULES,
               daily_kw(args.ratio, args.shift, args.wmin, rapid_v2=False),
               dict(RAPID_V2)]
    replays = run_replays(configs, args.workers)
    fill_cache(replays, args.workers)
    res = {}
    for kw in configs:
        name = cfg_name(kw)
        steps = []
        for key in FIRES:
            steps += step_table(key, replays[(key, name)])
        res[name] = {"kw": kw, "summary": by_phase(steps),
                     "steps": [{k: v for k, v in s.items()
                                if k not in ("phys", "bel", "belu")}
                               | {"phys": _slim(s["phys"]), "bel": _slim(s["bel"]),
                                  "belu": _slim(s["belu"])} for s in steps],
                     # the belief's own rows: the two arms the tables read
                     "belief_rows": ({key: [{k: v for k, v in r.items()
                                             if k not in ("p", "cum")}
                                            for r in replays[(key, name)]]
                                      for key in FIRES}
                                     if kw in (AS_BUILT, v2) else {})}
    res["_v2_name"] = cfg_name(v2)
    path = os.path.join(_HERE, f"v2_final{'_wide' if R.variant() else ''}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, sort_keys=True, separators=(",", ":"))
    print("wrote", path)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("grid", "lofo", "final"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--variant", choices=("", "wide"), default="")
    ap.add_argument("--ratio", type=float, default=1.5)
    ap.add_argument("--shift", type=float, default=90.0)
    ap.add_argument("--wmin", type=float, default=5.0)
    args = ap.parse_args(argv)
    if args.variant:
        os.environ["BVP_VARIANT"] = args.variant
    return {"grid": cmd_grid, "lofo": cmd_lofo, "final": cmd_final}[
        args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
