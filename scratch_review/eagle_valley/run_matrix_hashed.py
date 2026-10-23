"""The Eagle River valley matrix (run_matrix.py) with every tick's state()
hashed, for identity across code versions (2026-10-23, the step-cost work:
"its per-run results must be identical"). NOT product code.

Runs run_matrix.run_one for every run of the matrix from the checkout at
ROOT (a directory holding wgresponse/, data/ and this harness), each in a
fresh process, and writes OUT/runs.jsonl (run_matrix's own result row plus
state_hashes: sha1 of json.dumps(state(), sort_keys=True, default=repr) per
tick — every float via repr, so equal means byte-identical) and the per-tick
logs under OUT/ticks/ as run_matrix writes them.

  py -3 scratch_review/eagle_valley/run_matrix_hashed.py ROOT OUT [--workers N]
        [--hours H] [--start-hour H]
  py -3 scratch_review/eagle_valley/run_matrix_hashed.py --compare A B

--compare: per run, the result row (less its wall-clock wall_s), the state
hash of every tick and the per-tick log (less its wall-clock step_s); A and
B are OUT directories, or runs.jsonl files (then without the per-tick logs).

Records (2026-10-23, 07:13 starts, 12 h, 4-6 workers): perf_before_runs.jsonl
(main 4bcb7a4, run from its git archive; equal to iter5_final_runs.jsonl
row for row, wall_s aside) and perf_after_runs.jsonl (the step-cost change):
--compare 33 / 33 identical, per-tick logs included.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys


def _run(args):
    root, run_args = args
    sys.path.insert(0, os.path.join(root, "scratch_review", "eagle_valley"))
    import run_matrix                                    # noqa: E402
    from wgresponse import simui                         # noqa: E402
    hashes = []
    orig = simui.SimSession.tick

    def tick(self, *a, **k):
        st = orig(self, *a, **k)
        hashes.append(hashlib.sha1(json.dumps(
            st, sort_keys=True, default=repr).encode("utf-8")).hexdigest()[:16])
        return st

    simui.SimSession.tick = tick
    res = run_matrix.run_one(run_args)
    res["state_hashes"] = hashes
    return res


def main(argv):
    root, out = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    workers, hours, start_hour = 1, 12.0, None
    i = 2
    while i < len(argv):
        if argv[i] == "--workers":
            workers = int(argv[i + 1])
        elif argv[i] == "--hours":
            hours = float(argv[i + 1])
        elif argv[i] == "--start-hour":
            start_hour = float(argv[i + 1])
        else:
            raise SystemExit(f"unknown arg {argv[i]}")
        i += 2
    sys.path.insert(0, os.path.join(root, "scratch_review", "eagle_valley"))
    import run_matrix
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "runs.jsonl")
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            done = {json.loads(line)["run"] for line in fh if line.strip()}
    todo = [r for r in run_matrix.runs() if r[0] not in done]
    args = [(root, r + (hours, None, out)
             + ((start_hour,) if start_hour is not None else ()))
            for r in todo]
    print(f"{len(args)} runs -> {path}", flush=True)
    import multiprocessing as mp
    pool = mp.get_context("spawn").Pool(max(1, workers), maxtasksperchild=1)
    for res in pool.imap_unordered(_run, args):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True, default=str) + "\n")
        print(f"{res['run']:40s} {res.get('wall_s')} s "
              f"{res.get('error', '')}", flush=True)
    pool.close()
    pool.join()


def _rows(d):
    p = d if os.path.isfile(d) else os.path.join(d, "runs.jsonl")
    with open(p, encoding="utf-8") as fh:
        return {r["run"]: r for r in (json.loads(x) for x in fh if x.strip())}


def _ticklog(d, run):
    """The run's per-tick log less its wall clock; None without one (a
    runs.jsonl file was given)."""
    p = os.path.join(d, "ticks", run.replace("|", "__") + ".json")
    if os.path.isfile(d) or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        log = json.load(fh)
    for row in log:
        row.pop("step_s", None)
    return log


def compare(a, b):
    ra, rb = _rows(a), _rows(b)
    same = diff = 0
    for run in sorted(set(ra) & set(rb)):
        x, y = dict(ra[run]), dict(rb[run])
        x.pop("wall_s", None)
        y.pop("wall_s", None)
        bad = []
        hx, hy = x.pop("state_hashes", []), y.pop("state_hashes", [])
        if hx != hy:
            k = next((i for i, (p, q) in enumerate(zip(hx, hy)) if p != q),
                     min(len(hx), len(hy)))
            bad.append(f"state hashes from tick {k + 1}")
        if x != y:
            bad.append("result row: " + ", ".join(
                sorted(key for key in set(x) | set(y)
                       if x.get(key) != y.get(key))))
        la, lb = _ticklog(a, run), _ticklog(b, run)
        if la is not None and lb is not None and la != lb:
            bad.append("per-tick log")
        if bad:
            diff += 1
            print(f"DIFF {run:40s} {bad}")
        else:
            same += 1
            print(f"same {run:40s} {len(hx)} ticks")
    print(f"{same} identical, {diff} differ, {len(set(ra) ^ set(rb))} unmatched")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"]:
        compare(sys.argv[2], sys.argv[3])
    else:
        main(sys.argv[1:])
