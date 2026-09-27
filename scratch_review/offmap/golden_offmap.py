"""Compute the knob-OFF goldens of tests/offmap_stack.offmap_world_run
(SAFETY SEMANTICS 15 byte-identity pin). NOT product code.

    py -3 scratch_review/offmap/golden_offmap.py [ENGINE_ROOT]

ENGINE_ROOT defaults to this worktree; pass the pre-change engine to
recompute the goldens on it:

    git archive -o out/offmap/pristine_6c748c5.tar 6c748c5 wgresponse tests/seeded_stack.py
    tar -xf out/offmap/pristine_6c748c5.tar -C out/offmap/pristine
    py -3 scratch_review/offmap/golden_offmap.py out/offmap/pristine

(the fixture tests/offmap_stack.py itself is new: it is imported from this
worktree and drives whichever engine is first on the path). Also prints what
that engine does with the world (the refusal the rule lifts).
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
import offmap_stack as om  # noqa: E402
import seeded_stack as ss  # noqa: E402

print("engine:", os.path.dirname(wgresponse.__file__))
print("seeded_stack:", ss.__file__)
d = tempfile.mkdtemp(prefix="om_golden_")
cycles, api, state, summary = om.offmap_world_run(os.path.join(d, "state"))
print("_GOLDEN_CYCLES = [")
for c in cycles:
    print(f'    "{c}",')
print("]")
print(f'_GOLDEN_API = "{api}"')
print(f'_GOLDEN_STATE = "{state}"')
print(f'_GOLDEN_SUMMARY = "{summary}"')

clock = ss.SettableClock(om.T0)
daemon, monitor, feed, engine = om.make_stack(os.path.join(d, "s2"),
                                              om.fail_closed_cfg(), clock=clock)
for k in range(3):
    clock.t = om.T0 + 60.0 + k * ss.PERIOD_S
    res = daemon.run_cycle()
    plan = res.plan
    print("cycle", k, "domains", sorted(res.ensembles),
          "stops", [(cid, st.client_id) for cid, cp in plan.plans.items()
                    for st in cp.stops] if plan else None,
          "unserved", [(u.client_id, u.reason) for u in plan.unserved]
          if plan else None)
