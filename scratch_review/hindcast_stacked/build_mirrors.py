"""Mirror trees of the committed hindcast RECORD files, one per state, in
their original formats, so the review-era scripts (rvclaude_*, rvgrok_*,
rv2sci_*, rv2code_*) can be re-run unmodified on each state
(run_review_scripts.py substitutes the mirror for their data paths).

  results/mirror/documented/  the committed files as of 0bbd430 (git show)
  results/mirror/before/      rebuilt from results/before/ (pre-fix corpora)
  results/mirror/after/       rebuilt from results/after/  (repaired corpora)

Each rebuilt file follows its generator exactly: aug_replay.json
(aug_hindcast_replay.py: guards dt / t0 >= 08-01 / weather span; rows for
both configs, zero-growth rows logged as excluded), cal_realism.json
(oos_realism_cal.py), oos_realism.json (oos_realism.py), replay_b*.json
(harness replays), oos_pooled.json (oos_pool.py), gust_sweep.json
(gust_sweep.py), extreme_signature.json (extreme_signature.py),
tools/hindcast_data/replay_results.json (the default replay) and
calmatrix/<corpus>_<label>.json. Offline.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import before_after as BA  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import _irwin_slug, _load_all_incidents, _incident_domain  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

MIRROR = os.path.join(HERE, "results", "mirror")
RECORD_FILES = [
    "scratch_review/hindcast_aug_data/aug_replay.json",
    "scratch_review/hindcast_oos_data/cal_realism.json",
    "scratch_review/hindcast_oos_data/oos_realism.json",
    "scratch_review/hindcast_oos_data/oos_pooled.json",
    "scratch_review/gust_sweep.json",
    "scratch_review/extreme_signature.json",
    "scratch_review/aug_hindcast_discover.json",
    "tools/hindcast_data/replay_results.json",
] + [f"scratch_review/hindcast_oos_data/replay_b{b}.json"
     for b in ("0.0", "0.25", "0.5", "0.75", "1.0")] + [
    f"scratch_review/calmatrix/{c}_{lab}.json"
    for c in ("aug", "oos") for lab in BA.MATRIX_ORDER]


def write(root, rel, obj):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)


def documented():
    root = os.path.join(MIRROR, "documented")
    for rel in RECORD_FILES:
        blob = subprocess.run(["git", "show", f"0bbd430:{rel}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob)


def incident_pairs(corpus_root, rel):
    """Every consecutive cached snapshot pair per incident (name, irwin,
    slug, a, b) in the harness's iteration order."""
    cwd = os.getcwd()
    os.chdir(corpus_root)
    try:
        incs = _load_all_incidents(rel)
    finally:
        os.chdir(cwd)
    return incs


def rebuilt(state):
    root = os.path.join(MIRROR, state)
    corpus_root = BA.BEFORE_ROOT if state == "before" else ROOT
    R = lambda corpus, label: BA.result(state, corpus, label)

    # --- August review-era table (aug_hindcast_replay.py)
    F, A = R("aug", "F_nofloor")["pairs"], R("aug", "A_deployed")["pairs"]
    byk = {(p["incident"], p["t0_s"]): p for p in A}
    rows, excluded = [], []
    for slug, inc in incident_pairs(corpus_root, BA.CORPUS_DIR["aug"]):
        span = BA.wx_span(corpus_root, "aug", slug)
        for a, b in zip(inc.snapshots, inc.snapshots[1:]):
            dt_h = (b.t_s - a.t_s) / 3600.0
            why = None
            if not (0.0 < dt_h <= ResponseConfig().horizon_h - 6.0):
                why = f"dt {dt_h:.1f}h outside (0, horizon-6]"
            elif a.t_s < BA.AUG_CUT:
                why = "t0 before 2026-08-01 (pre-cutoff)"
            elif span is None or a.t_s < span[0] or b.t_s > span[1] + 3600:
                why = "window outside archived weather"
            if why:
                excluded.append({"incident": inc.name, "t0": a.t_s, "t1": b.t_s, "why": why})
                continue
            pf = next(p for p in F if p["incident"] == inc.irwin and p["t0_s"] == a.t_s)
            pa = byk[(inc.irwin, a.t_s)]
            rows.append(pf | {"variant": "calibration"})
            rows.append(pa | {"variant": "as-deployed"})
            if pf["n_new_burnable"] <= 0:
                excluded.append({"incident": inc.name, "t0": a.t_s, "t1": b.t_s,
                                 "why": (pf.get("notes") or ["no scorable (burnable) growth"])[0]})
    pooled = {}
    for v in ("calibration", "as-deployed"):
        ps = [r for r in rows if r["variant"] == v and r["n_new_burnable"] > 0]
        p = BA.aug_pool(ps)
        pooled[v] = {"n_pairs": p["pairs"], "n_cells": p["cells"],
                     "coverage_possible": p["coverage"], "late_miss": p["late"],
                     "early_miss": p["early"], "p50_in_window": p["p50_in"],
                     "median_head_ratio": p["head_upper_mid"]}
    write(root, "scratch_review/hindcast_aug_data/aug_replay.json",
          {"rows": rows, "excluded": excluded, "pooled": pooled})

    # --- calibration-set realism (oos_realism_cal.py)
    good = {(p["incident"], p["t0_s"]) for p in R("pinned", "b0.0")["pairs"] if p["n_new_burnable"] > 0}
    var = {"blend0.5": R("pinned", "b0.5")["pairs"], "grass_floor": R("pinned", "b0.5_floor")["pairs"],
           "realism_pack": R("pinned", "b0.5_floor_spot")["pairs"]}
    cal = []
    for p0 in R("pinned", "b0.5")["pairs"]:
        k = (p0["incident"], p0["t0_s"])
        if k not in good:
            continue
        for v, ps in var.items():
            p = next(q for q in ps if (q["incident"], q["t0_s"]) == k)
            if p["n_new_burnable"] > 0:
                cal.append(p | {"variant": v})
    write(root, "scratch_review/hindcast_oos_data/cal_realism.json", cal)

    # --- OOS realism (oos_realism.py)
    good = {(p["incident"], p["t0_s"]) for p in R("oos", "F_nofloor")["pairs"]
            if p["n_new_burnable"] > 0 and p["t0_s"] >= BA.OOS_CUT}
    var = {"grass_floor": R("oos", "A_deployed")["pairs"], "realism_pack": R("oos", "b0.5_floor_spot")["pairs"]}
    oosr = []
    for p0 in R("oos", "F_nofloor")["pairs"]:
        k = (p0["incident"], p0["t0_s"])
        if k not in good:
            continue
        for v, ps in var.items():
            p = next(q for q in ps if (q["incident"], q["t0_s"]) == k)
            if p["n_new_burnable"] > 0:
                oosr.append(p | {"variant": v})
    write(root, "scratch_review/hindcast_oos_data/oos_realism.json", oosr)

    # --- OOS blend sweep files + oos_pooled.json (oos_pool.py)
    pooled_rows = []
    for b in ("0.0", "0.25", "0.5", "0.75", "1.0"):
        d = R("oos", BA.oos_label(b))
        write(root, f"scratch_review/hindcast_oos_data/replay_b{b}.json", d)
        keep, _drop = BA.oos_keep(d["pairs"], corpus_root)
        if not keep:
            continue
        p = BA.sweep_pool(keep)
        pooled_rows.append({"n_pairs": len(keep), "n": p["cells"], "cover": p["coverage"],
                            "p10_nec": p["coverage"], "late": p["late"], "early": p["early"],
                            "p50_in": p["p50_in"], "head": p["head_upper_mid"], "blend": b})
    write(root, "scratch_review/hindcast_oos_data/oos_pooled.json", pooled_rows)

    # --- pinned gust sweep (gust_sweep.py) + default replay
    sweep = []
    for b in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = BA.sweep_pool(R("pinned", f"b{b}")["pairs"])
        sweep.append({"n": p["cells"], "cover": p["coverage"], "p10_nec": p["coverage"],
                      "late": p["late"], "early": p["early"], "p50_in": p["p50_in"],
                      "head": p["head_upper_mid"], "blend": b})
    write(root, "scratch_review/gust_sweep.json", sweep)
    write(root, "tools/hindcast_data/replay_results.json", R("pinned", "b0.0"))

    # --- extreme signature (extreme_signature.py): windows = grass_floor rows
    sig = []
    for set_name, corpus, rows_ in (("cal", "pinned", [r for r in cal if r["variant"] == "grass_floor"]),
                                    ("oos", "oos", [r for r in oosr if r["variant"] == "grass_floor"])):
        lates = {(r["incident"], r["t0_s"]): 100.0 * r["late_miss"] / r["n_new_burnable"] for r in rows_}
        for slug, inc in incident_pairs(corpus_root, BA.CORPUS_DIR[corpus]):
            wx_path = os.path.join(corpus_root, BA.CORPUS_DIR[corpus], "weather", f"{slug}.json")
            if not os.path.exists(wx_path):
                continue
            wd = BA.jload(wx_path)
            wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
            bbox, cell_m = _incident_domain(inc, ResponseConfig())
            cp = LfpsTerrain(cache_dir=os.path.join(corpus_root, BA.CORPUS_DIR[corpus], "lfps")).cache_path(bbox, cell_m)
            if not (cp and os.path.exists(cp)):
                continue
            mix = BA.fuel_mix(load_ascii_tile(cp))
            for a, b in zip(inc.snapshots, inc.snapshots[1:]):
                key = (inc.irwin, a.t_s)
                if key not in lates:
                    continue
                w = BA.wx_window(wx, a.t_s, b.t_s)
                if w is None:
                    continue
                sig.append(dict(set=set_name, name=inc.name, t0_s=a.t_s,
                                floored_late=lates[key], mix=mix, **w))
    write(root, "scratch_review/extreme_signature.json", sig)

    # --- matrices + the unchanged discovery file
    for c in ("aug", "oos"):
        for lab in BA.MATRIX_ORDER:
            write(root, f"scratch_review/calmatrix/{c}_{lab}.json", R(c, lab))
    shutil.copy(os.path.join(ROOT, "scratch_review", "aug_hindcast_discover.json"),
                os.path.join(root, "scratch_review", "aug_hindcast_discover.json"))


def main() -> int:
    documented()
    for state in ("before", "after"):
        rebuilt(state)
    print("wrote", MIRROR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
