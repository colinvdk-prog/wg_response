"""DIAGNOSIS ONLY (Defect 3 fix-direction validation).

Re-runs the SAME mutually-exclusive triage dilemma from diag_triage.py, but
monkeypatches dispatch._potential_score to a COUNT-FIRST form (every home worth
an equal large constant, dollar value only as a tiny tiebreak) — a prototype of
cfg.dispatch_objective='homes'. Confirms the decision flips from {A} (1 home,
value-max) to {B,C} (2 homes, count-max). Proves the fix lands in the objective.
Read-only: monkeypatch is in-process, product files untouched.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wgresponse.dispatch as dispatch
import diag_triage as T  # reuse the exact scenario builder pieces
from wgresponse.contracts import ResponseConfig


def main():
    cfg = ResponseConfig()
    spec = T.spec_big()
    from wgresponse import travel, safety
    cc = T.cc

    crew_loc = cc(spec, 20, 30)
    A_loc, B_loc, C_loc = cc(spec, 20, 6), cc(spec, 20, 54), cc(spec, 21, 54)

    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)
    deploy_s, egr, slack = cfg.deploy_min * 60.0, cfg.egress_buffer_min * 60.0, cfg.slack_min * 60.0
    t_crew_A, t_crew_B, t_B_C = tt(crew_loc, A_loc), tt(crew_loc, B_loc), tt(B_loc, C_loc)
    A_latest = T.NOW + t_crew_A + deploy_s + slack + 120.0
    dB = T.NOW + t_crew_B + deploy_s
    B_latest = dB + slack + 120.0
    dC = dB + t_B_C + deploy_s
    C_latest = dC + slack + 120.0
    A = T.make_job("A_near_hi", A_loc, A_latest + egr, A_latest + egr + 7200, 5_000_000.0)
    B = T.make_job("B_far_lo", B_loc, B_latest + egr, B_latest + egr + 7200, 700_000.0)
    C = T.make_job("C_far_lo", C_loc, C_latest + egr, C_latest + egr + 7200, 700_000.0)
    zones = [__import__("wgresponse.contracts", fromlist=["SafeZone"]).SafeZone("Z1", cc(spec, 20, 58))]

    print("BASELINE (current value objective):")
    T.run_case("value baseline", cfg, crew_loc, A, B, C, zones, spec)

    # --- prototype count-first objective ---
    _orig = dispatch._potential_score
    K = 1_000_000_000.0   # each served home worth a large equal constant

    def _count_first(job, cfg):
        # equal large per-home credit + tiny dollar tiebreak (keeps determinism)
        return K + job.client.priority_weight * job.client.value_usd * cfg.gel_effectiveness * 1e-3

    dispatch._potential_score = _count_first
    try:
        print("\nPROTOTYPE (count-first objective, cfg.dispatch_objective='homes'):")
        s, _ = T.run_case("count-first", cfg, crew_loc, A, B, C, zones, spec)
        n = len(s)
        print(f"\n  RESULT: served {sorted(s)} (count={n}) -> "
              f"{'FLIPPED to 2 homes (B+C) — fix works' if n == 2 and 'A_near_hi' not in s else 'unexpected'}")
    finally:
        dispatch._potential_score = _orig


if __name__ == "__main__":
    main()
