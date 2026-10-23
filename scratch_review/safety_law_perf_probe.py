"""Step cost of the one-click scenarios after the Eagle River valley crew
review (SAFETY SEMANTICS 25-38), and state identity across code versions
(2026-10-23; integrator brief: "make them faster WITHOUT changing any
decision ... byte-identical results"). NOT product code.

Builds every server.SCENARIOS key exactly like POST /api/sim/new
{scenario: KEY} (every simulator default, no cfg override unless --cfg),
ticks TICKS 15-minute steps and records per tick:

  tick_s     wall seconds (tick 1 = the first step)
  plans      every dispatch.plan() call: wall s, jobs offered, served,
             runaway guard tripped, plan hash
  adopted    hash of the plan the tick ADOPTED (SimSession._adopt)
  state      hash of the tick's state() — sha1 of json.dumps(sort_keys,
             default=repr): every float via repr, so "identical" means
             byte-identical

--root runs another checkout (a directory holding wgresponse/ and data/),
so the same inputs replay on two versions. --profile DIR writes a cProfile
of tick 1 (DIR/<key>.t1.prof) and of ticks 2..N (DIR/<key>.rest.prof).
Every run is a fresh process (cold memos, like a fresh server).

  py -3 scratch_review/safety_law_perf_probe.py OUT.jsonl [--root DIR]
        [--ticks N] [--only KEY[,KEY]] [--cfg JSON] [--workers N]
        [--profile DIR]
  py -3 scratch_review/safety_law_perf_probe.py --compare A.jsonl B.jsonl
  py -3 scratch_review/safety_law_perf_probe.py --table A.jsonl [B.jsonl]

Records (2026-10-23; "4bcb7a4" = main at the brief and "f70df84" = the
world14 image's tree, each extracted with git archive to a directory and run
through --root; no run tripped the solver's runaway guard):
  safety_law_perf_before.jsonl          4bcb7a4, 48 ticks, 6 workers,
                                        Python 3.14
  safety_law_perf_after.jsonl           this change, the same (--compare
                                        with the above: 13 / 13 identical)
  safety_law_perf_timing_world14.jsonl  f70df84, 24 ticks, 2 workers,
                                        Python 3.13 (the image's)
  safety_law_perf_timing_before.jsonl   4bcb7a4, the same
  safety_law_perf_timing_after.jsonl    this change, the same (--table; and
                                        --compare with _before: 13 / 13)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


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
    root, key, ticks, extra, prof_dir = args
    sys.path.insert(0, root or os.path.dirname(HERE))
    from wgresponse import dispatch, server, simui

    plans_log = []
    adopted = []
    orig_plan = dispatch.plan

    def plan_wrap(*a, **k):
        t = time.perf_counter()
        p = orig_plan(*a, **k)
        ob = p.objective_breakdown or {}
        plans_log.append([round(time.perf_counter() - t, 3), len(a[1]),
                          ob.get("served"), bool(ob.get("budget_runaway")),
                          _h(_plan_canon(p))])
        return p

    dispatch.plan = plan_wrap
    orig_adopt = simui.SimSession._adopt

    def adopt_wrap(self, plan, *a, **k):
        adopted.append(_h(_plan_canon(plan)))
        return orig_adopt(self, plan, *a, **k)

    simui.SimSession._adopt = adopt_wrap
    t0 = time.perf_counter()
    mgr = server.SimSessionManager()
    payload = {"scenario": key}
    if extra:
        payload["cfg"] = dict(extra)
    code, out = mgr.new_session(payload)
    if code != 200:
        return {"key": key, "error": str(out)}
    s = mgr._entry(out["session_id"]).session
    setup_s = time.perf_counter() - t0
    prof = None
    if prof_dir:
        import cProfile
        os.makedirs(prof_dir, exist_ok=True)
    tick_s, tick_plans, tick_adopted, state_h = [], [], [], []
    dep_max = ov_ticks = 0
    st = None
    for k in range(ticks):
        plans_log.clear()
        adopted.clear()
        if prof_dir and k in (0, 1):
            if prof is not None:
                prof.disable()
                prof.dump_stats(os.path.join(prof_dir, f"{key}.t1.prof"))
            prof = cProfile.Profile()
            prof.enable()
        t1 = time.perf_counter()
        st = s.tick()
        tick_s.append(round(time.perf_counter() - t1, 3))
        tick_plans.append(list(plans_log))
        tick_adopted.append(list(adopted))
        state_h.append(_h(json.dumps(st, sort_keys=True, default=repr)))
        dep_max = max(dep_max, len(s._violations))
        if st.get("crew_overruns"):
            ov_ticks += 1
    if prof is not None:
        prof.disable()
        prof.dump_stats(os.path.join(
            prof_dir, f"{key}.{'t1' if ticks == 1 else 'rest'}.prof"))
    sb = st["scoreboard"]
    res = {"key": key, "ticks": ticks, "cfg": extra or {},
           "setup_s": round(setup_s, 3), "tick_s": tick_s,
           "first_s": tick_s[0] if tick_s else None,
           "later_mean_s": (round(sum(tick_s[1:]) / (len(tick_s) - 1), 3)
                            if len(tick_s) > 1 else None),
           "plans": tick_plans, "adopted": tick_adopted, "state": state_h,
           "served": sb["served"], "dep_viol_max": dep_max,
           "viol": sb["violations"], "overrun_ticks": ov_ticks,
           "acres": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
           "runaway": any(p[3] for tp in tick_plans for p in tp)}
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
                    out[r["key"]] = r
    return out


def sweep(out_path, root=None, ticks=48, only=None, extra=None, workers=1,
          prof_dir=None):
    sys.path.insert(0, root or os.path.dirname(HERE))
    from wgresponse import server
    done = _rows(out_path)
    todo = [k for k in sorted(server.SCENARIOS)
            if (not only or k in only) and k not in done]
    args = [(root, k, ticks, extra, prof_dir) for k in todo]
    print(f"{len(args)} runs -> {out_path}", flush=True)
    import multiprocessing as mp
    pool = mp.get_context("spawn").Pool(max(1, workers), maxtasksperchild=1)
    for res in pool.imap_unordered(run_one, args):
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True) + "\n")
        ts = res.get("tick_s") or [0]
        print(f"{res['key']:34s} first {res.get('first_s')} s  later mean "
              f"{res.get('later_mean_s')} s max {max(ts[1:] or [0])} s  "
              f"served {res.get('served')}  runaway {res.get('runaway')}"
              f" {res.get('error', '')}", flush=True)
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
            if [p[4] for p in ra["plans"][t]] != [p[4] for p in rb["plans"][t]]:
                bad.append(f"tick {t + 1} plan calls")
            if ra["state"][t] != rb["state"][t]:
                bad.append(f"tick {t + 1} state")
        if bad:
            diff += 1
            print(f"DIFF {k:34s} {bad[:4]}")
        else:
            same += 1
            print(f"same {k:34s} {n} ticks")
    print(f"{same} identical, {diff} differ, "
          f"{len(set(a) ^ set(b))} unmatched")


def table(a_path, b_path=None):
    a = _rows(a_path)
    b = _rows(b_path) if b_path else {}
    tot = [0.0, 0.0, 0.0, 0.0]
    for k in sorted(a):
        ra = a[k]
        line = (f"{k:34s} first {ra['first_s']:7.2f} s  later "
                f"{ra['later_mean_s']:6.3f} s")
        tot[0] += ra["first_s"]
        tot[1] += ra["later_mean_s"]
        if k in b:
            rb = b[k]
            line += (f"  ->  first {rb['first_s']:7.2f} s  later "
                     f"{rb['later_mean_s']:6.3f} s")
            tot[2] += rb["first_s"]
            tot[3] += rb["later_mean_s"]
        print(line)
    n = max(1, len(a))
    print(f"{'mean':34s} first {tot[0] / n:7.2f} s  later {tot[1] / n:6.3f} s"
          + (f"  ->  first {tot[2] / n:7.2f} s  later {tot[3] / n:6.3f} s"
             if b else ""))


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--compare":
        compare(argv[1], argv[2])
        sys.exit(0)
    if argv and argv[0] == "--table":
        table(argv[1], argv[2] if len(argv) > 2 else None)
        sys.exit(0)
    out = argv[0]
    opts = {"root": None, "ticks": 48, "only": None, "extra": None,
            "workers": 1, "prof_dir": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--root":
            opts["root"] = os.path.abspath(argv[i + 1])
        elif a == "--ticks":
            opts["ticks"] = int(argv[i + 1])
        elif a == "--only":
            opts["only"] = set(argv[i + 1].split(","))
        elif a == "--cfg":
            opts["extra"] = json.loads(argv[i + 1])
        elif a == "--workers":
            opts["workers"] = int(argv[i + 1])
        elif a == "--profile":
            opts["prof_dir"] = os.path.abspath(argv[i + 1])
        else:
            raise SystemExit(f"unknown option {a}")
        i += 2
    sweep(out, **opts)
