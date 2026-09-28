"""Run every replay behind a documented calibration number, BEFORE (pre-fix
corpora, worktree at 0bbd430) and AFTER (repaired corpora, this checkout),
on the same engine (wgresponse/ is byte-identical in both checkouts).

Jobs (each one replay_job.py process, offline):
  matrix  the 19 calibration-matrix variants of scratch_review/calmatrix_run.sh
          and calmatrix2_run.sh (docs/calibration/calibration_matrix_2026-09-03
          and -09-11) on the August and July-OOS corpora, gust blend 0.5.
          F_nofloor = the OOS doc's blend-0.5 row and the August "calibration"
          config; A_deployed = the "grass floor" / "as-deployed" configs.
  pinned  tools/hindcast_data: the gust sweep (scratch_review/gust_sweep.py,
          blends 0..1; blend 0 with the widened band = hindcast_first_pass.md)
          and the calibration-set realism configs (oos_realism_cal.py).
  oos     the rest of the July-OOS blend sweep (replay_b*.json) and the
          realism pack (oos_realism.py); F_nofloor carries the widened band.

    py -3 scratch_review/hindcast_stacked/run_all.py --state both --jobs 8
Outputs: scratch_review/hindcast_stacked/results/<state>/<corpus>__<label>.json
(existing outputs are skipped); progress in results/run_log.txt.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
FIX_ROOT = os.path.dirname(os.path.dirname(HERE))
BEFORE_ROOT = os.path.join(os.path.dirname(FIX_ROOT), "hindcast-before")
RESULTS = os.path.join(HERE, "results")
CORPUS = {"pinned": "tools/hindcast_data",
          "oos": "scratch_review/hindcast_oos_data",
          "aug": "scratch_review/hindcast_aug_data"}


def cfg(blend=0.5, floor=0.0, gate=False, grass=False, me=0.0, p50=False,
        spot=False, regime=False, regime_floor=0.0, piecewise=False,
        phi=False) -> dict:
    """The 11 ResponseConfig fields the harness's `replay` exposes
    (hindcast_harness._variant_config_kwargs), all else shipped default."""
    return {"wind_gust_blend": blend, "grass_ros_floor_frac": floor,
            "ros_floor_extinction_gate": gate, "ros_floor_grass_only": grass,
            "model_error_sd_log": me, "ensemble_p50_nominal_bound": p50,
            "spotting_enabled": spot, "extreme_regime_enabled": regime,
            "extreme_regime_floor_frac": regime_floor,
            "edge_weather_piecewise": piecewise, "slope_phi_vector": phi}


MATRIX = {
    "A_deployed": cfg(floor=0.10),
    "F_nofloor": cfg(),
    "B_gate": cfg(floor=0.10, gate=True),
    "C_gate_grass": cfg(floor=0.10, gate=True, grass=True),
    "D_gate_me035": cfg(floor=0.10, gate=True, me=0.35),
    "D_gate_me050": cfg(floor=0.10, gate=True, me=0.5),
    "D_gate_me071": cfg(floor=0.10, gate=True, me=0.71),
    "E_gategrass_me050": cfg(floor=0.10, gate=True, grass=True, me=0.5),
    "G_gate_p50": cfg(floor=0.10, gate=True, p50=True),
    "H_nofloor_me050": cfg(me=0.5),
    "H_nofloor_me071": cfg(me=0.71),
    "E_gategrass_me071": cfg(floor=0.10, gate=True, grass=True, me=0.71),
    "I_gategrass05_me050": cfg(floor=0.05, gate=True, grass=True, me=0.5),
    "I_gategrass05_me071": cfg(floor=0.05, gate=True, grass=True, me=0.71),
    "J_regime_I071": cfg(floor=0.05, gate=True, grass=True, me=0.71,
                         regime=True, regime_floor=0.10),
    "L_regime_I_noband": cfg(floor=0.05, gate=True, grass=True, regime=True,
                             regime_floor=0.10),
    "M_D071_piecewise": cfg(floor=0.10, gate=True, me=0.71, piecewise=True),
    "N_D071_phi": cfg(floor=0.10, gate=True, me=0.71, phi=True),
    "O_D071_both": cfg(floor=0.10, gate=True, me=0.71, piecewise=True,
                       phi=True),
}


def jobs() -> list:
    out = []                                   # (corpus, label, cfg, wide)
    for corpus in ("aug", "oos"):
        for label, c in MATRIX.items():
            out.append((corpus, label, c, corpus == "oos" and label == "F_nofloor"))
    for b in (0.0, 0.25, 0.5, 0.75, 1.0):
        out.append(("pinned", f"b{b}", cfg(blend=b), b == 0.0))
    out.append(("pinned", "b0.5_floor", cfg(floor=0.10), False))
    out.append(("pinned", "b0.5_floor_spot", cfg(floor=0.10, spot=True), False))
    for b in (0.0, 0.25, 0.75, 1.0):
        out.append(("oos", f"b{b}", cfg(blend=b), False))
    out.append(("oos", "b0.5_floor_spot", cfg(floor=0.10, spot=True), False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", choices=("before", "after", "both"),
                    default="both")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    states = ("after", "before") if args.state == "both" else (args.state,)
    todo = []
    for state in states:
        root = FIX_ROOT if state == "after" else BEFORE_ROOT
        for corpus, label, c, wide in jobs():
            if args.only and args.only not in f"{corpus}__{label}":
                continue
            out = os.path.join(RESULTS, state, f"{corpus}__{label}.json")
            if os.path.exists(out):
                continue
            todo.append((state, root, corpus, label, c, wide, out))
    # the August corpus is the slowest: start it first
    todo.sort(key=lambda j: (j[2] != "aug", j[2] != "oos"))
    os.makedirs(RESULTS, exist_ok=True)
    log_path = os.path.join(RESULTS, "run_log.txt")

    def log(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def run(job):
        state, root, corpus, label, c, wide, out = job
        t0 = time.time()
        cmd = [sys.executable, os.path.join(HERE, "replay_job.py"),
               "--root", root, "--data-dir", CORPUS[corpus], "--label", label,
               "--cfg", json.dumps(c), "--out", out] + (["--wide"] if wide else [])
        env = dict(os.environ, PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8")
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode != 0:
            tail = "FAILED: " + (r.stderr.strip().splitlines() or ["?"])[-1]
        log(f"{state:6s} {corpus}__{label}: {time.time() - t0:6.0f} s  {tail}")
        return r.returncode

    log(f"start: {len(todo)} job(s), {args.jobs} worker(s)")
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        codes = list(ex.map(run, todo))
    log(f"done: {sum(1 for c in codes if c == 0)} ok, "
        f"{sum(1 for c in codes if c != 0)} failed")
    return 0 if all(c == 0 for c in codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
