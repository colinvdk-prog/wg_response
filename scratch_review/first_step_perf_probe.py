"""First-step cost of the one-click scenarios, and plan identity across code
versions (2026-10-12; founder "fix the slow first step"; docs/
SIMULATOR_DESIGN.md "The slow first step"). NOT product code.

Builds every server.SCENARIOS key exactly like POST /api/sim/new
{scenario: KEY} (the page's defaults: spotting + grass floor ON, plan on
roads, 24 h coating hold, every simulator default), with the integrator's
"option 1" defaults set EXPLICITLY (cfg.spot_fire_size = True,
cfg.ros_floor_grass_only = False) plus any --cfg overrides; a scenario at an
intensity other than its preset is re-lit at the preset's own ignition point
and wind (as the page does). Then ticks TICKS 15-minute steps and records:

  tick_s        wall seconds of every tick (tick 1 = the first step)
  plans         every dispatch.plan() call: wall s, jobs offered, served,
                budget consults spent, runaway-guard tripped, plan hash
  adopted       hash of the plan each tick ADOPTED (SimSession._adopt)
  state         hash of every tick's state() (crews, stops, scoreboard, ...)
  served / dep_viol_max / overrun_ticks / acres at the end

Plan / state hashes are sha1 of a canonical repr (every float via repr, so
"identical" means byte-identical); objective_breakdown['solver_s'] (a wall
clock) is excluded. --root runs another checkout of the code (a directory
holding wgresponse/ and data/), so the same inputs replay on two versions:

  py -3 scratch_review/first_step_perf_probe.py OUT.jsonl [--root DIR]
        [--ticks N] [--only KEY[,KEY]] [--intens low,moderate,...]
        [--cfg JSON] [--set JSON] [--workers N]
  py -3 scratch_review/first_step_perf_probe.py --compare A.jsonl B.jsonl
  py -3 scratch_review/first_step_perf_probe.py --table A.jsonl [B.jsonl]
  py -3 scratch_review/first_step_perf_probe.py --outcomes A.jsonl B.jsonl

--compare: plan + state identity tick by tick; --table: first-step seconds;
--outcomes: a knob's measurement (homes served, departure violations,
crew-overrun ticks, A -> B).

--cfg '{"solver_budget_s": 10000}' lifts the solver's wall-clock runaway
guard (10 x solver_budget_s) so a slow version still runs its counted budget
to the end — the deterministic plan both versions must reproduce. --set
JSON flips session cfg fields after setup without recording them in the
replay log (e.g. '{"dispatch_ils_skip_all_served": false}' to run the
current code with a simulator default off and compare state hashes with a
checkout that predates it). Every run is a fresh process (cold memos).
Runs already in OUT are skipped (resume).

Records (2026-10-12; "main" = 76c4b41 extracted to a directory with its
data/, run through --root):
  first_step_perf_baseline.jsonl   main, --cfg solver_budget_s 10000, 24
                                   ticks, 6 workers (the deterministic plans)
  first_step_perf_after.jsonl      this change, same cfg, --set
                                   dispatch_ils_skip_all_served false
                                   (--compare with the baseline: 52/52)
  first_step_perf_knob.jsonl       this change, same cfg, simulator defaults
                                   (--outcomes against first_step_perf_after)
  first_step_perf_timing_before.jsonl  main at production settings (the 50 s
                                   runaway guard live), 1 tick, 2 workers
  first_step_perf_timing_after.jsonl   this change at simulator defaults,
                                   3 ticks, 1 worker (--table with the above)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INTENSITIES = ("low", "moderate", "high", "extreme")
OPTION1 = {"spot_fire_size": True, "ros_floor_grass_only": False}


def _h(obj) -> str:
    return hashlib.sha1(repr(obj).encode("utf-8")).hexdigest()[:16]


def _plan_canon(p):
    plans = []
    for cid in sorted(p.plans):
        cp = p.plans[cid]
        plans.append((cid, cp.egress_zone_id, repr(cp.committed_until_s),
                      tuple((s.client_id, s.incident_id, repr(s.arrive_s),
                             repr(s.service_start_s), repr(s.depart_s),
                             repr(s.latest_depart_s), repr(s.margin_h),
                             repr(s.gel_fresh_frac)) for s in cp.stops)))
    ob = {k: (repr(v) if isinstance(v, float) else v)
          for k, v in sorted((p.objective_breakdown or {}).items())
          if k != "solver_s"}
    return (repr(p.computed_at_s), tuple(plans),
            tuple((u.client_id, u.incident_id, u.reason) for u in p.unserved),
            repr(p.objective), tuple(sorted(ob.items())),
            repr(p.churn_from_incumbent))


def run_one(args):
    root, key, inten, ticks, extra, sets = args
    if root:
        sys.path.insert(0, root)
    else:
        sys.path.insert(0, os.path.dirname(HERE))
    from wgresponse import dispatch, server, simui

    plans_log = []
    adopted = []
    orig_plan = dispatch.plan

    def plan_wrap(*a, **k):
        t = time.perf_counter()
        p = orig_plan(*a, **k)
        ob = p.objective_breakdown or {}
        plans_log.append([round(time.perf_counter() - t, 3), len(a[1]),
                          ob.get("served"), ob.get("budget_spent"),
                          bool(ob.get("budget_runaway")), _h(_plan_canon(p))])
        return p

    dispatch.plan = plan_wrap
    orig_adopt = simui.SimSession._adopt

    def adopt_wrap(self, plan, *a, **k):
        adopted.append(_h(_plan_canon(plan)))
        return orig_adopt(self, plan, *a, **k)

    simui.SimSession._adopt = adopt_wrap
    cfg = dict(OPTION1)
    cfg.update(extra or {})
    t0 = time.perf_counter()
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    if code != 200:
        return {"key": key, "intensity": inten, "error": str(out)}
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    if inten != f.intensity:
        lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
        s.reset()
        s.ignite(lat, lon, intensity=inten, wind_dir_deg=wd)
    for name, val in (sets or {}).items():
        # --set: a cfg field flipped on the live session AFTER setup — not a
        # cfg override, so the replay log in state() (cmd_log) stays the
        # baseline's and state hashes remain comparable
        setattr(s._cfg, name, val)
    setup_s = time.perf_counter() - t0
    tick_s, tick_plans, tick_adopted, state_h = [], [], [], []
    dep_max = ov_ticks = 0
    st = None
    for _k in range(ticks):
        plans_log.clear()
        adopted.clear()
        t1 = time.perf_counter()
        st = s.tick()
        tick_s.append(round(time.perf_counter() - t1, 3))
        tick_plans.append(list(plans_log))
        tick_adopted.append(list(adopted))
        state_h.append(_h(json.dumps(st, sort_keys=True, default=repr)))
        dep_max = max(dep_max, len(s._violations))
        if st.get("crew_overruns"):
            ov_ticks += 1
    sb = st["scoreboard"]
    res = {"key": key, "intensity": inten, "ticks": ticks,
           "cfg": extra or {}, "setup_s": round(setup_s, 3),
           "tick_s": tick_s, "first_s": tick_s[0] if tick_s else None,
           "plans": tick_plans, "adopted": tick_adopted, "state": state_h,
           "served": sb["served"], "dep_viol_max": dep_max,
           "viol": sb["violations"], "overrun_ticks": ov_ticks,
           "acres": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
           "runaway": any(p[4] for tp in tick_plans for p in tp)}
    with mgr._lock:
        mgr._sessions.clear()
    return res


def _rows(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    out[(r["key"], r["intensity"])] = r
    return out


def sweep(out_path, root=None, ticks=24, only=None, intens=INTENSITIES,
          extra=None, workers=1, sets=None):
    sys.path.insert(0, root or os.path.dirname(HERE))
    from wgresponse import server
    done = _rows(out_path)
    todo = [(k, i) for k in sorted(server.SCENARIOS) if not only or k in only
            for i in intens if (k, i) not in done]
    args = [(root, k, i, ticks, extra, sets) for k, i in todo]
    print(f"{len(args)} runs -> {out_path}", flush=True)
    # one fresh process per run: no road graph, tree, route or snap memo
    # survives from an earlier run (cold first steps, like a fresh server)
    import multiprocessing as mp
    pool = mp.get_context("spawn").Pool(max(1, workers), maxtasksperchild=1)
    it = pool.imap_unordered(run_one, args)
    for res in it:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True) + "\n")
        print(f"{res['key']:32s} {res['intensity']:8s} first "
              f"{res.get('first_s')} s  later max "
              f"{max(res.get('tick_s', [0])[1:] or [0])} s  served "
              f"{res.get('served')}  runaway {res.get('runaway')}", flush=True)
    if pool is not None:
        pool.close()
        pool.join()


def compare(a_path, b_path):
    a, b = _rows(a_path), _rows(b_path)
    same = diff = 0
    for k in sorted(set(a) & set(b)):
        ra, rb = a[k], b[k]
        n = min(ra["ticks"], rb["ticks"])
        bad = []
        for t in range(n):
            if ra["adopted"][t] != rb["adopted"][t]:
                bad.append(f"tick {t + 1} adopted plan")
            if ra["state"][t] != rb["state"][t]:
                bad.append(f"tick {t + 1} state")
        if bad:
            diff += 1
            print(f"DIFF {k[0]:32s} {k[1]:8s} {bad[:4]}")
        else:
            same += 1
    print(f"{same} identical, {diff} differ, "
          f"{len(set(a) ^ set(b))} unmatched")


def outcomes(a_path, b_path):
    """A knob's measurement: per combination, homes served at the end, the
    per-tick max of serviced-stop departure violations, crew-overrun ticks
    (A -> B), and the totals."""
    a, b = _rows(a_path), _rows(b_path)
    tot = {"served": [0, 0], "dep": [0, 0], "ovt": [0, 0], "same": 0, "n": 0}
    for k in sorted(set(a) & set(b)):
        ra, rb = a[k], b[k]
        tot["n"] += 1
        tot["served"][0] += ra["served"]
        tot["served"][1] += rb["served"]
        tot["dep"][0] += ra["dep_viol_max"]
        tot["dep"][1] += rb["dep_viol_max"]
        tot["ovt"][0] += ra["overrun_ticks"]
        tot["ovt"][1] += rb["overrun_ticks"]
        same = ra["state"] == rb["state"]
        tot["same"] += same
        flag = "identical" if same else (
            f"served {ra['served']}->{rb['served']}")
        print(f"{k[0]:32s} {k[1]:8s} first {ra['first_s']:7.2f} -> "
              f"{rb['first_s']:6.2f} s  dep {ra['dep_viol_max']}->"
              f"{rb['dep_viol_max']}  ovt {ra['overrun_ticks']}->"
              f"{rb['overrun_ticks']}  {flag}")
    print(f"{tot['n']} runs: served {tot['served'][0]} -> {tot['served'][1]}, "
          f"departure violations {tot['dep'][0]} -> {tot['dep'][1]}, "
          f"overrun ticks {tot['ovt'][0]} -> {tot['ovt'][1]}, "
          f"{tot['same']} runs identical tick for tick")


def table(a_path, b_path=None):
    a = _rows(a_path)
    b = _rows(b_path) if b_path else {}
    for k in sorted(a):
        ra = a[k]
        line = (f"{k[0]:32s} {k[1]:8s} first {ra['first_s']:7.2f} s "
                f"(plans {[p[0] for p in ra['plans'][0]]})")
        if k in b:
            rb = b[k]
            line += (f" -> {rb['first_s']:6.2f} s "
                     f"(plans {[p[0] for p in rb['plans'][0]]})")
        print(line)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--compare":
        compare(argv[1], argv[2])
        sys.exit(0)
    if argv and argv[0] == "--table":
        table(argv[1], argv[2] if len(argv) > 2 else None)
        sys.exit(0)
    if argv and argv[0] == "--outcomes":
        outcomes(argv[1], argv[2])
        sys.exit(0)
    out = argv[0]
    opts = {"root": None, "ticks": 24, "only": None, "intens": INTENSITIES,
            "extra": None, "workers": 1, "sets": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--root":
            opts["root"] = os.path.abspath(argv[i + 1])
        elif a == "--ticks":
            opts["ticks"] = int(argv[i + 1])
        elif a == "--only":
            opts["only"] = set(argv[i + 1].split(","))
        elif a == "--intens":
            opts["intens"] = tuple(argv[i + 1].split(","))
        elif a == "--cfg":
            opts["extra"] = json.loads(argv[i + 1])
        elif a == "--set":
            opts["sets"] = json.loads(argv[i + 1])
        elif a == "--workers":
            opts["workers"] = int(argv[i + 1])
        else:
            raise SystemExit(f"unknown option {a}")
        i += 2
    sweep(out, **opts)
