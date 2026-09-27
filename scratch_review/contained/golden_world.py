"""Compute the knob-OFF goldens of tests/contained_stack.baseline_world_run.

    py -3 scratch_review/contained/golden_world.py [ENGINE_ROOT]

ENGINE_ROOT defaults to the repo; pass a `git archive a8bebec wgresponse
tests/seeded_stack.py | tar -x -C <dir>` copy, e.g. out/contained/pristine (the
`git archive a8bebec` engine) to recompute the goldens on the pre-change
engine. Also shows what that engine does to the two knob-relevant fires."""
import os
import sys
import tempfile

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else WT
sys.path.insert(0, os.path.join(WT, "tests"))
sys.path.insert(0, ENGINE)

import wgresponse  # noqa: E402
import contained_stack as cs  # noqa: E402
import seeded_stack as ss  # noqa: E402

print("engine:", os.path.dirname(wgresponse.__file__))
d = tempfile.mkdtemp(prefix="cs_golden_")
cycles, api, state = cs.baseline_world_run(os.path.join(d, "state"))
print("_GOLDEN_WORLD_CYCLES = [")
for c in cycles:
    print(f'    "{c}",')
print("]")
print(f'_GOLDEN_WORLD_API = "{api}"')
print(f'_GOLDEN_WORLD_STATE = "{state}"')

clock = ss.SettableClock(cs.T0)
daemon, monitor, feed, engine, *_ = ss.make_stack(
    os.path.join(d, "s2"), incidents=cs.world_incidents(), clock=clock)
for k in range(3):
    clock.t = cs.T0 + 60.0 + k * ss.PERIOD_S
    res = daemon.run_cycle()
    print("cycle", k, "incidents", [i.incident_id for i in res.incidents],
          "domains", sorted(res.ensembles))
    for a in res.assessments:
        p10, p50, _ = a.eta.hours_out(clock.t)
        print("   ", a.client_id, a.incident_id, "p10_h", round(p10, 2),
              "p50_h", round(p50, 2))
for key, rec in sorted(engine.states().items()):
    print("state", key, rec.state.value)
