"""Compute the knob-OFF byte-identity goldens pinned in
tests/test_fuse_colocated.py (contracts SAFETY SEMANTICS 24).

Run on the PRE-CHANGE engine (commit bf2a868, before co-located fusion
existed): the fused-list digests of the recorded 2026-10-15 payloads and of
the reconstructed 2026-10-14 Bike pair through the legacy
fuse_incidents(batches), and the three-cycle world run of the re-homed Bike
pair (cycle / api / state digests) through the real watch stack.

    py -3 scratch_review/colocated/golden.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import colocated_stack as cst  # noqa: E402
from wgresponse import feeds  # noqa: E402


def main() -> int:
    print("fixture fused  ", cst.fused_digest(feeds.fuse_incidents(
        cst.fixture_batches())))
    print("bike 10-14     ", cst.fused_digest(feeds.fuse_incidents(
        cst.bike_2026_10_14_batches())))
    with tempfile.TemporaryDirectory() as td:
        cycles, api, state = cst.baseline_world_run(Path(td) / "state")
    for c in cycles:
        print("world cycle    ", c)
    print("world api      ", api)
    print("world state    ", state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
