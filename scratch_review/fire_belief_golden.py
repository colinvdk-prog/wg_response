"""Compute the fire-belief byte-identity goldens on the CURRENT engine tree.

The goldens pinned in tests/test_fire_belief.py were produced by running
this script on the a8bebec engine (before any fire-belief code existed):

    py -3 scratch_review/fire_belief_golden.py 2

It prints the digests of tests/fire_belief_stack.belief_baseline_run()
(3 cycles; repeated N times to prove determinism) as JSON. Re-running it on
a later tree with every fire-belief knob OFF must print the same digests.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import fire_belief_stack as fb  # noqa: E402

out = []
for rep in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
    with tempfile.TemporaryDirectory() as d:
        keep = []
        cycles, api, state, world = fb.belief_baseline_run(
            os.path.join(d, "state"), keep=keep)
        out.append({"cycles": cycles, "api": api, "state": state,
                    "world": world,
                    "ensembles": [sorted(r.ensembles) for r in keep],
                    "n_assess": [len(r.assessments) for r in keep],
                    "events": [len(r.events) for r in keep],
                    "files": sorted(os.listdir(os.path.join(d, "state")))})
print(json.dumps(out, indent=1))
