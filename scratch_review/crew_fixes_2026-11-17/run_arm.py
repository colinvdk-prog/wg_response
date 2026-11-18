"""Run a harness (matrix.py or eagle_valley/run_matrix.py) in ONE process with
a simulator module constant overridden — the design arms of the 2026-11-17
fixes. NOT product code.

Usage: python run_arm.py ARM HARNESS OUT_DIR [harness args...]
ARM: t2 (REFUGE_EXIT_UNTIL_LEAVE = True, as shipped) | t1 (False)
HARNESS: lanea | valley
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scratch_review", "eagle_valley"))


def main(argv):
    arm, harness = argv[0], argv[1]
    from wgresponse import simui
    simui.REFUGE_EXIT_UNTIL_LEAVE = (arm != "t1")
    if harness == "lanea":
        import matrix as h
    else:
        import run_matrix as h
    h.main(argv[2:] + ["--workers", "1"])


if __name__ == "__main__":
    main(sys.argv[1:])
