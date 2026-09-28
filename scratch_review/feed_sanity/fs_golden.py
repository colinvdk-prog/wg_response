"""Compute the feed-sanity byte-identity goldens (contracts SAFETY SEMANTICS
18) on a given engine tree.

The goldens pinned in tests/test_feed_sanity.py were produced by running this
script against the commit-0bbd430 engine (the main checkout, BEFORE any feed-
sanity code existed), with this worktree's tests/feed_sanity_stack.py:

    py -3 scratch_review/feed_sanity/fs_golden.py C:/Users/colin/wg_response 2

argv[1] = the engine root whose wgresponse package is imported (default:
this worktree); argv[2] = repetitions (proves determinism). It prints the
digests of feed_sanity_stack.hazard_run() (knob OFF), the wgresponse file
actually imported, and a few counts, as JSON. Re-running it on a later tree
with cfg.feed_sanity OFF must print the same digests.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ENGINE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else ROOT
sys.path.insert(0, ENGINE)
sys.path.insert(1, os.path.join(ROOT, "tests"))

import wgresponse  # noqa: E402
import feed_sanity_stack as fss  # noqa: E402

out = {"engine": os.path.dirname(os.path.abspath(wgresponse.__file__)),
       "runs": []}
for rep in range(int(sys.argv[2]) if len(sys.argv) > 2 else 1):
    with tempfile.TemporaryDirectory() as d:
        keep = []
        cycles, api, state, world, belief = fss.hazard_run(
            os.path.join(d, "state"), keep=keep)
        out["runs"].append({
            "cycles": cycles, "api": api, "state": state, "world": world,
            "belief": belief,
            "ensembles": [sorted(r.ensembles) for r in keep],
            "n_assess": [len(r.assessments) for r in keep],
            "events": [len(r.events) for r in keep],
            "states": [sorted({e.state.value for e in r.events})
                       for r in keep]})
print(json.dumps(out, indent=1))
