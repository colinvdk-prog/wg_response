"""DIAGNOSIS ONLY (Defect 3 — triage: protect as many homes as possible).

Pure dispatch.plan() experiment. Constructs a deterministic 1-crew triage
dilemma: one HIGH-VALUE home A that is feasible now but whose service burns
the window for two LOWER-VALUE homes B+C farther out. Measures whether the
solver serves {A} (1 home, value-max) or {B,C} (2 homes, count-max).

Corridors trivially safe (uniform UNREACHED raster) so ONLY the objective and
deadlines bind — isolates the value-vs-count behaviour.
Read-only: imports the product modules, mutates nothing on disk.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety, travel
from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewStatus, DispatchJob, GridSpec,
    Raster, SafeZone, ThreatEta, ResponseConfig, DispatchPlan, CrewPlan,
    PlannedStop,
)

NOW = 1_790_000_000.0
INC = "inc1"


def spec_big():
    # 1 km cells, 60 wide -> 60 km span, room to put A on the far WEST and the
    # B/C cluster on the far EAST so any A-detour blows the whole cluster.
    return GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                    nrows=40, ncols=60)


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def uniform(spec, v):
    return Raster(spec, [v] * (spec.nrows * spec.ncols))


def make_ens(raster):
    from wgresponse.contracts import EnsembleArrival
    return EnsembleArrival(spec=raster.spec, members=[], p10=raster, p50=raster,
                           p90=raster, t0_s=NOW, incident_id=INC)


def make_job(cid, loc, p10_s, p50_s, value, weight=1.0):
    return DispatchJob(
        client=ClientProperty(client_id=cid, loc=loc, value_usd=value,
                              priority_weight=weight),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s, eta_p90_s=p50_s + 3600.0))


def served_set(plan):
    out = {}
    for cid, cp in plan.plans.items():
        for st in cp.stops:
            out[st.client_id] = cid
    return out


def run_case(label, cfg, crew_loc, A, B, C, zones, spec, incumbent=None):
    ens = {INC: make_ens(uniform(spec, UNREACHED))}
    crew = Crew(crew_id="G1", name="G1", status=CrewStatus.AVAILABLE,
                home_base=crew_loc)
    jobs = [A, B, C]
    plan = dispatch.plan([crew], jobs, ens, zones, cfg, NOW, incumbent=incumbent)
    s = served_set(plan)
    bd = plan.objective_breakdown
    print(f"\n--- {label} (dispatch_objective={cfg.dispatch_objective!r}) ---")
    print(f"  served homes : {sorted(s)}  (count={len(s)})")
    print(f"  value_usd    : ${bd['value_usd']:,.0f}")
    print(f"  candidate/seed: {bd['candidate']}/{bd['seed']} guardrail={bd['guardrail']!r}")
    for u in plan.unserved:
        print(f"  UNSERVED {u.client_id}: reason={u.reason!r}")
    return s, plan


def main():
    cfg = ResponseConfig()
    print("cfg defaults: deploy_min=%.1f egress_buffer_min=%.1f slack_min=%.1f "
          "road_speed_mps=%.2f circuity=%.2f fire_traffic=%.2f gel_window_h=%.1f "
          "notice_h=%.1f dispatch_objective=%r"
          % (cfg.deploy_min, cfg.egress_buffer_min, cfg.slack_min,
             cfg.road_speed_mps, cfg.circuity, cfg.fire_traffic_factor,
             cfg.gel_window_h, cfg.notice_h, cfg.dispatch_objective))

    # STAR topology: crew central; A far WEST, {B,C} cluster far EAST, both
    # ~equidistant so neither is "on the way" to the other. Tight deadlines
    # give the crew time for only ONE direction -> {A} XOR {B,C}.
    spec = spec_big()
    crew_loc = cc(spec, 20, 30)  # center
    A_loc = cc(spec, 20, 6)      # ~24 km W
    B_loc = cc(spec, 20, 54)     # ~24 km E
    C_loc = cc(spec, 21, 54)     # ~24 km E, next to B

    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)

    deploy_s = cfg.deploy_min * 60.0
    egr = cfg.egress_buffer_min * 60.0
    slack = cfg.slack_min * 60.0

    t_crew_A = tt(crew_loc, A_loc)
    t_crew_B = tt(crew_loc, B_loc)
    t_A_B = tt(A_loc, B_loc)
    t_A_C = tt(A_loc, C_loc)
    t_B_C = tt(B_loc, C_loc)
    t_C_A = tt(C_loc, A_loc)
    print("\ntravel times (min): crew->A %.1f  crew->B %.1f  A->B %.1f  B->C %.1f"
          % (t_crew_A / 60, t_crew_B / 60, t_A_B / 60, t_B_C / 60))

    # job.latest_depart_s = eta_p10 - egress (the SAFETY deadline).
    # A: TIGHT — feasible direct (crew->A) with just enough slack.
    A_latest_depart = NOW + t_crew_A + deploy_s + slack + 120.0
    A_p10 = A_latest_depart + egr
    A_p50 = A_p10 + 2 * 3600.0
    # B,C: TIGHT — feasible ONLY in the direct crew->B->C chain.
    direct_B_depart = NOW + t_crew_B + deploy_s
    B_latest_depart = direct_B_depart + slack + 120.0
    direct_C_depart = direct_B_depart + t_B_C + deploy_s
    C_latest_depart = direct_C_depart + slack + 120.0
    B_p10, B_p50 = B_latest_depart + egr, B_latest_depart + egr + 2 * 3600.0
    C_p10, C_p50 = C_latest_depart + egr, C_latest_depart + egr + 2 * 3600.0

    # Prove the exclusivity with the travel arithmetic:
    assert direct_B_depart <= B_latest_depart - slack + 1e-6, "direct B fits"
    assert direct_C_depart <= C_latest_depart - slack + 1e-6, "direct C fits"
    assert NOW + t_crew_A + deploy_s + t_A_B + deploy_s > B_latest_depart, \
        "A-first blows B"
    assert NOW + t_crew_A + deploy_s + t_A_C + deploy_s > C_latest_depart, \
        "A-first blows C"
    assert (NOW + t_crew_B + deploy_s + t_B_C + deploy_s + t_C_A + deploy_s
            > A_latest_depart), "cluster-first strands A"
    print("  MUTUAL EXCLUSION verified: {A} XOR {B,C} are the only maximal "
          "feasible sets  (value: A=$5.0M vs B+C=$1.4M ; count: 1 vs 2)")

    # Values: A dominates B+C in dollars, loses in COUNT.
    A = make_job("A_near_hi", A_loc, A_p10, A_p50, value=5_000_000.0)
    B = make_job("B_far_lo", B_loc, B_p10, B_p50, value=700_000.0)
    C = make_job("C_far_lo", C_loc, C_p10, C_p50, value=700_000.0)

    # far egress zone (trivially safe corridor anyway)
    zones = [SafeZone("Z1", cc(spec, 20, 39))]

    # sanity: can a single crew serve BOTH B and C but not with A in front?
    print("\nfeasibility check:")
    print("  direct crew->B depart = NOW+%.1f min ; B latest_depart = NOW+%.1f min"
          % ((direct_B_depart - NOW) / 60, (B_latest_depart - NOW) / 60))
    print("  via-A    ->B  depart = NOW+%.1f min  (misses B deadline)"
          % ((NOW + t_crew_A + deploy_s + t_A_B + deploy_s - NOW) / 60))

    # CASE 1: current default (dispatch_objective='homes' in cfg, but unimplemented)
    run_case("CURRENT DEFAULT", cfg, crew_loc, A, B, C, zones, spec)

    # CASE 2: explicitly value (what the code actually does regardless)
    cfg_val = ResponseConfig(dispatch_objective="value")
    run_case("EXPLICIT value", cfg_val, crew_loc, A, B, C, zones, spec)

    # CASE 3: preemption — incumbent already serving B+C, then A appears.
    # Does the solver keep 2 homes or switch to the single high-value A?
    inc_stops = [
        PlannedStop(client_id="B_far_lo", incident_id=INC,
                    arrive_s=NOW + t_crew_B, service_start_s=NOW + t_crew_B,
                    depart_s=direct_B_depart, latest_depart_s=B_latest_depart,
                    margin_h=(B_latest_depart - direct_B_depart) / 3600.0),
        PlannedStop(client_id="C_far_lo", incident_id=INC,
                    arrive_s=direct_B_depart + t_B_C,
                    service_start_s=direct_B_depart + t_B_C,
                    depart_s=direct_B_depart + t_B_C + deploy_s,
                    latest_depart_s=C_latest_depart,
                    margin_h=1.0),
    ]
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"G1": CrewPlan(crew_id="G1", stops=inc_stops)})
    run_case("PREEMPTION (incumbent=B,C; A appears)", cfg, crew_loc, A, B, C,
             zones, spec, incumbent=incumbent)


if __name__ == "__main__":
    main()
