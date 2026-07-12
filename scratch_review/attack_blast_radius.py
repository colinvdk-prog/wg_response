"""ATTACK SUITE 7 — blast radius: determinism promises after the Session-1 lands.

  * sim determinism: same (scenario, seed, hours, cfg) run twice must produce
    BYTE-IDENTICAL report + console JSON (the contracts/mocks determinism
    promise — Sprint-2 exit gate 7 'byte-identical promises verified');
  * a second seed must actually change the report (the comparison has power).

The bench/bench_dispatch.py quick-config run and the full-suite pass are
executed by the verifier shell (they spawn child processes); this file holds
the in-process checks.

Run:  py -3 -m pytest -q scratch_review/attack_blast_radius.py
Deterministic; no network. Product code untouched (read-only attack).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from wgresponse import sim
from wgresponse.contracts import ResponseConfig


def _run_tiny(out_dir: str, seed: str = "attack-seed") -> "tuple[bytes, bytes]":
    cfg = ResponseConfig()
    cfg.solver_budget_s = 2.0
    scenario = sim.build_scenario("tiny", seed=seed)
    sim.run(scenario, hours=2.0, cfg=cfg, out_dir=out_dir)
    report = Path(out_dir, "sim_tiny.json").read_bytes()
    console = Path(out_dir, "sim_tiny_console.json").read_bytes()
    return report, console


def test_sim_same_seed_twice_byte_identical():
    with tempfile.TemporaryDirectory() as d1, \
            tempfile.TemporaryDirectory() as d2:
        r1, c1 = _run_tiny(d1)
        r2, c2 = _run_tiny(d2)
    assert r1 == r2, "sim report JSON not byte-identical across identical runs"
    assert c1 == c2, "sim console JSON not byte-identical across identical runs"
    assert len(r1) > 500                     # a real report, not an empty stub


def test_sim_different_seed_changes_report():
    with tempfile.TemporaryDirectory() as d1, \
            tempfile.TemporaryDirectory() as d2:
        r1, _ = _run_tiny(d1, seed="attack-seed")
        r2, _ = _run_tiny(d2, seed="attack-seed-B")
    assert r1 != r2, ("different scenario seeds produced identical bytes — "
                      "the determinism comparison above has no power")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
