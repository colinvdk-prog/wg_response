"""Compute the knob-OFF goldens of tests/wxfield_stack.wx_world_run (SAFETY
SEMANTICS 17 byte-identity pin). NOT product code.

    py -3 scratch_review/wxfield/golden_wxfield.py [ENGINE_ROOT]

ENGINE_ROOT defaults to this worktree; pass the pre-change engine to
recompute the goldens on it:

    git archive -o out/wxfield/pristine_b5ce62c.tar b5ce62c wgresponse tests/seeded_stack.py
    mkdir out/wxfield/pristine
    tar -xf out/wxfield/pristine_b5ce62c.tar -C out/wxfield/pristine
    py -3 scratch_review/wxfield/golden_wxfield.py out/wxfield/pristine

(the fixture tests/wxfield_stack.py itself is new: it is imported from this
worktree and drives whichever engine is first on the path). Also prints what
that engine does with the world (domains, ETAs, the forecast calls).
"""
import os
import sys
import tempfile

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else WT
sys.path.insert(0, os.path.join(WT, "tests"))
sys.path.insert(0, os.path.join(ENGINE, "tests"))
sys.path.insert(0, ENGINE)

import wgresponse  # noqa: E402
import seeded_stack as ss  # noqa: E402
import wxfield_stack as wf  # noqa: E402

print("engine:", os.path.dirname(wgresponse.__file__))
print("seeded_stack:", ss.__file__)
d = tempfile.mkdtemp(prefix="wx_golden_")
cycles, api, state, summary = wf.wx_world_run(os.path.join(d, "state"))
print("_GOLDEN_CYCLES = [")
for c in cycles:
    print(f'    "{c}",')
print("]")
print(f'_GOLDEN_API = "{api}"')
print(f'_GOLDEN_STATE = "{state}"')
print(f'_GOLDEN_SUMMARY = "{summary}"')

clock = ss.SettableClock(wf.T0)
daemon, monitor, feed, engine, weather = wf.make_stack(
    os.path.join(d, "s2"), wf.small_cfg(), clock=clock)
clock.t = wf.T0 + 60.0
res = daemon.run_cycle()
for iid, ens in sorted(res.ensembles.items()):
    sp = ens.spec
    print("domain", iid, sp.nrows, "x", sp.ncols, "@", round(sp.cell_m, 1), "m")
for a in res.assessments:
    print("eta", a.client_id, round((a.eta.eta_p10_s - clock.t) / 3600.0, 3),
          round((a.eta.eta_p50_s - clock.t) / 3600.0, 3))
print("forecast calls", [(round(l.lat, 4), round(l.lon, 4))
                         for l, _ in weather.calls])
print("plan", res.plan and [(cid, [s.client_id for s in cp.stops])
                            for cid, cp in res.plan.plans.items()],
      res.plan and [(u.client_id, u.reason) for u in res.plan.unserved])
