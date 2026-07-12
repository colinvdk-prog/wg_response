"""ADVERSARIAL VERIFIER (Session 2) — attack #6: end-to-end safety invariants.

Claims under attack (verifier brief #6):
  6a. Flagship sim (canyon_wind_event, 96 h) still reports ZERO safety
      violations, 12/12 serviced, 0 unserved.
  6b. Every PLANNED stop in the sim's adopted plans carries
      margin_h * 60 >= cfg.slack_min (SAFETY SEMANTICS 7 end-to-end).
  6c. The four provider-outage drills stay green (rerun via
      tests/test_failsafe.py in the main suite; asserted here by importing
      and executing the drill parametrizations through pytest is redundant —
      the suite run is the gate; this file re-checks the flagship path).

Run from repo root (takes ~1 min):
    py -3 -m pytest scratch_review/attack_s2_endtoend.py -v
"""
from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import sim
from wgresponse.contracts import ResponseConfig

_REPORT_CACHE = {}


def _flagship():
    if "r" not in _REPORT_CACHE:
        out_dir = os.path.join(tempfile.gettempdir(), "wgresp_attack_s2_sim")
        scenario = sim.build_scenario("canyon_wind_event", "default")
        _REPORT_CACHE["r"] = sim.run(scenario, 96.0, out_dir=out_dir)
    return _REPORT_CACHE["r"]


def test_flagship_sim_zero_violations_all_serviced():
    r = _flagship()
    assert r.violations == [], (
        f"flagship sim now has {len(r.violations)} safety violations: "
        f"{r.violations[:3]}")
    assert len(r.serviced) == 12, f"serviced {len(r.serviced)}/12"
    assert r.unserved == [], f"unserved: {r.unserved}"
    assert r.min_margin_h is not None and r.min_margin_h > 0.0
    # audited (actual) departures each beat their latest_depart deadline
    for a in r.audit:
        assert not a.violation
        assert a.depart_s <= a.latest_depart_s + 1e-6


def _iter_plan_stops(plan_dict):
    plans = plan_dict.get("plans", {})
    it = plans.values() if isinstance(plans, dict) else plans
    for cp in it:
        for st in cp.get("stops", []):
            yield st


def test_flagship_planned_stops_respect_slack_floor():
    r = _flagship()
    cfg = ResponseConfig()
    checked = 0
    bad = []
    for snap in r.plans_recent:
        plan_dict = snap.get("plan", {})
        for st in _iter_plan_stops(plan_dict):
            m_h = st.get("margin_h")
            if m_h is None:
                continue
            checked += 1
            if not math.isfinite(m_h) or m_h * 60.0 < cfg.slack_min - 1e-9:
                bad.append((snap.get("cycle"), st.get("client_id"), m_h))
    assert checked > 0, "no planned stops found in plans_recent snapshots"
    assert not bad, (
        f"planned stops below the {cfg.slack_min}-min slack floor: {bad[:5]}")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
