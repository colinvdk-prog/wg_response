"""ATTACK SUITE 6 — planned-slack floor + client dedupe (Sprint-2 A6/A7,
Council-1 dispatch F2/F4).

Attacks:
  * 50 seeded-random dispatch instances (tight deadlines, multi-crew, ILS
    shakes running for real, half re-planned against an incumbent): EVERY
    emitted stop must carry >= cfg.slack_min minutes of slack
    (latest_depart_s - depart_s), raw-field-checked — not just margin_h;
  * slack-only-infeasible jobs must surface as 'infeasible_slack';
  * dedupe under THREE incidents threatening the same client: one stop, value
    counted once, the surviving job honors the TIGHTEST (earliest-p10)
    deadline;
  * dedupe + slack interaction: when the tightest incident's deadline is
    slack-infeasible, the client must go UNSERVED (never quietly re-planned
    against a looser sibling incident's deadline — the real fire arrives at
    the earliest p10 regardless of which incident label survives).

Origin evidence: scratch_review/da_exp4_multiincident.py part C (double-gel /
double-counted value) and the dispatch_alerts_review F2 margin measurements.

Run:  py -3 -m pytest -q scratch_review/attack_slack_dedupe.py
Deterministic (contracts.seeded_rng); no network. Product code untouched.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from da_common import (T0, all_safe_ensemble, destination, mk_crew, mk_job)

from wgresponse import dispatch
from wgresponse.contracts import ResponseConfig, SafeZone, seeded_rng

CENTER_LAT, CENTER_LON = 34.20, -117.60
_EPS_S = 1e-6


def _cfg(**kw) -> ResponseConfig:
    cfg = ResponseConfig()
    cfg.solver_budget_s = 2.0
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _center():
    from wgresponse.contracts import LatLon
    return LatLon(CENTER_LAT, CENTER_LON)


def _assert_slack_floor(plan, cfg, tag):
    """THE invariant: every emitted stop keeps >= slack_min minutes of slack,
    computed from the raw absolute fields (not the derived margin_h)."""
    for cid, cp in plan.plans.items():
        for st in cp.stops:
            slack_min = (st.latest_depart_s - st.depart_s) / 60.0
            assert slack_min >= cfg.slack_min - 1e-9, (
                f"{tag}: crew {cid} stop {st.client_id} violates the slack "
                f"floor: {slack_min:.2f} min < {cfg.slack_min} min")
            assert st.margin_h * 60.0 >= cfg.slack_min - 1e-9, (
                f"{tag}: margin_h disagrees: {st.margin_h * 60.0:.2f} min")
            # internal consistency: depart = service + deploy, service >= arrive
            assert st.depart_s >= st.service_start_s - _EPS_S
            assert st.service_start_s >= st.arrive_s - _EPS_S


def _random_instance(i):
    """Seeded random instance with deadlines drawn TIGHT so the slack floor
    binds: p10 uniform in [now+50 min, now+5 h] => latest_depart in
    [now+20 min, now+4.5 h] against ~4-25 min travel legs."""
    rng = seeded_rng("attack.slack.fuzz", i)
    c = _center()
    n_crews = rng.randrange(2, 4)
    n_jobs = rng.randrange(6, 10)
    crews = [mk_crew(f"G{k+1}",
                     destination(c, rng.uniform(0, 360), rng.uniform(4, 12) * 1000.0),
                     T0)
             for k in range(n_crews)]
    jobs = []
    for j in range(n_jobs):
        loc = destination(c, rng.uniform(0, 360), rng.uniform(2, 10) * 1000.0)
        p10 = T0 + rng.uniform(50.0, 300.0) * 60.0
        p50 = p10 + rng.uniform(1.0, 10.0) * 3600.0
        jobs.append(mk_job(f"C{j+1}", loc, rng.uniform(0.8, 3.0) * 1e6,
                           2.0 if rng.random() < 0.25 else 1.0,
                           "INC-1", p10, p50))
    zones = [SafeZone(f"Z{k+1}",
                      destination(c, rng.uniform(0, 360), rng.uniform(9, 15) * 1000.0),
                      f"zone {k+1}") for k in range(2)]
    return crews, jobs, zones


# ===========================================================================
# 6a — 50 seeded plans: slack floor under construction + local search + ILS
# ===========================================================================

def test_slack_floor_holds_across_50_seeded_plans_and_replans():
    cfg = _cfg()
    ens = {"INC-1": all_safe_ensemble("INC-1")}
    total_stops = 0
    slack_unserved = 0
    ils_adopted = 0
    for i in range(50):
        crews, jobs, zones = _random_instance(i)
        plan = dispatch.plan(crews, jobs, ens, zones, cfg, T0)
        _assert_slack_floor(plan, cfg, f"plan[{i}]")
        total_stops += sum(len(cp.stops) for cp in plan.plans.values())
        slack_unserved += sum(1 for u in plan.unserved
                              if u.reason == "infeasible_slack")
        if plan.objective_breakdown.get("candidate") == "ils":
            ils_adopted += 1
        if i % 2 == 0:
            # replan 15 min later with the incumbent: warm repair + frozen
            # commitments must uphold the same floor
            jobs2 = [mk_job(j.client.client_id, j.client.loc,
                            j.client.value_usd, j.client.priority_weight,
                            j.incident_id, j.eta.eta_p10_s, j.eta.eta_p50_s)
                     for j in jobs]
            replan = dispatch.plan(crews, jobs2, ens, zones, cfg,
                                   T0 + 900.0, incumbent=plan)
            _assert_slack_floor(replan, cfg, f"replan[{i}]")
    # the fuzz must have exercised something real:
    assert total_stops >= 100, f"fuzz too weak: only {total_stops} stops emitted"
    assert slack_unserved >= 1, (
        "fuzz never produced a slack-only-infeasible job — deadlines not tight "
        "enough to bind the floor")


def test_slack_only_failure_reported_as_infeasible_slack():
    """A job whose deadline fits but whose slack cannot: crew 5 km out
    (~8.8 min travel), deploy 30 min, latest_depart = now + 45 min => depart
    now+38.8 min beats the deadline by 6.2 min < 15 min floor."""
    cfg = _cfg()
    c = _center()
    crew = mk_crew("G1", destination(c, 270.0, 5_000.0), T0)
    loc = c
    p10 = T0 + 75.0 * 60.0                    # latest_depart = now + 45 min
    job = mk_job("C1", loc, 2e6, 1.0, "INC-1", p10, p10 + 4 * 3600.0)
    zones = [SafeZone("Z1", destination(c, 180.0, 9_000.0), "S")]
    plan = dispatch.plan([crew], [job], {"INC-1": all_safe_ensemble("INC-1")},
                         zones, cfg, T0)
    assert [s.client_id for cp in plan.plans.values() for s in cp.stops] == []
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("C1", "infeasible_slack")]


# ===========================================================================
# 6b — dedupe: three incidents, one client
# ===========================================================================

def test_three_incidents_one_client_single_stop_value_once_tightest_deadline():
    cfg = _cfg()
    c = _center()
    crew = mk_crew("G1", destination(c, 180.0, 6_000.0), T0)
    loc = destination(c, 90.0, 3_000.0)
    zones = [SafeZone("Z1", destination(c, 270.0, 10_000.0), "W")]
    # three incidents, three deadlines; INC-B carries the earliest p10
    p10s = {"INC-A": T0 + 6.0 * 3600.0,
            "INC-B": T0 + 4.0 * 3600.0,
            "INC-C": T0 + 9.0 * 3600.0}
    jobs = [mk_job("C1", loc, 2_000_000.0, 1.0, iid, p10, p10 + 3600.0)
            for iid, p10 in sorted(p10s.items())]
    ens = {iid: all_safe_ensemble(iid) for iid in p10s}
    plan = dispatch.plan([crew], jobs, ens, zones, cfg, T0)

    stops = [s for cp in plan.plans.values() for s in cp.stops]
    assert len(stops) == 1, f"double-gel: {[(s.client_id, s.incident_id) for s in stops]}"
    st = stops[0]
    assert st.incident_id == "INC-B", (
        "dedupe survivor is not the earliest-p10 (tightest-deadline) job")
    # the emitted deadline must be the TIGHTEST one, with the slack floor on it
    expect_latest = p10s["INC-B"] - cfg.egress_buffer_min * 60.0
    assert abs(st.latest_depart_s - expect_latest) < 1.0
    assert (st.latest_depart_s - st.depart_s) / 60.0 >= cfg.slack_min - 1e-9
    bd = plan.objective_breakdown
    assert bd["dedup_dropped_jobs"] == 2
    # value counted ONCE: 2.0M * 0.85 = 1.7M (times gel_frac <= 1)
    assert bd["value_usd"] <= 2_000_000.0 * cfg.gel_effectiveness + 1e-6
    assert bd["served"] == 1
    # no ghost unserved entries for the deduped siblings
    assert not [u for u in plan.unserved if u.client_id == "C1"]


def test_dedupe_tightest_slack_infeasible_never_relaxes_to_looser_incident():
    """INC-B (earliest p10) is slack-infeasible; INC-A (looser) would fit.
    The client must be UNSERVED — serving under A's deadline would have the
    crew on the property past the REAL earliest pessimistic arrival."""
    cfg = _cfg()
    c = _center()
    crew = mk_crew("G1", destination(c, 270.0, 5_000.0), T0)
    loc = c
    p10_tight = T0 + 75.0 * 60.0     # slack-infeasible (see 6a helper math)
    p10_loose = T0 + 8.0 * 3600.0    # comfortably feasible
    jobs = [mk_job("C1", loc, 2e6, 1.0, "INC-A", p10_loose, p10_loose + 3600.0),
            mk_job("C1", loc, 2e6, 1.0, "INC-B", p10_tight, p10_tight + 3600.0)]
    ens = {"INC-A": all_safe_ensemble("INC-A"),
           "INC-B": all_safe_ensemble("INC-B")}
    zones = [SafeZone("Z1", destination(c, 180.0, 9_000.0), "S")]
    plan = dispatch.plan([crew], jobs, ens, zones, cfg, T0)
    stops = [s for cp in plan.plans.values() for s in cp.stops]
    assert stops == [], (
        "SLACK LAUNDERING: dedupe re-served the client under the looser "
        f"incident's deadline: {[(s.client_id, s.incident_id) for s in stops]}")
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("C1", "infeasible_slack")]
    assert plan.objective_breakdown["dedup_dropped_jobs"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
