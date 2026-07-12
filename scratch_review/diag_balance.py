"""DIAGNOSIS ONLY (Defect 2, root cause at the planner level).

Isolated dispatch.plan() probe: 4 crews staged at N/E/S/W ~10 km from a tight
central cluster of 8 equal-value jobs, generous (all-feasible) deadlines,
trivially-safe corridors. A load-balanced planner spreads ~2 jobs/crew; the
current value-objective planner (lambda_travel ~ 1% of mean score, no balance
term) MINIMIZES travel by piling jobs on the ONE nearest crew.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety, travel
from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewStatus, DispatchJob, GridSpec,
    Raster, SafeZone, ThreatEta, ResponseConfig, EnsembleArrival,
)

NOW = 1_790_000_000.0
INC = "inc1"


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def main():
    cfg = ResponseConfig()
    spec = GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                    nrows=40, ncols=60)
    ras = Raster(spec, [UNREACHED] * (spec.nrows * spec.ncols))
    ens = {INC: EnsembleArrival(spec=spec, members=[], p10=ras, p50=ras,
                                p90=ras, t0_s=NOW, incident_id=INC)}
    # 4 crews around the center (20,30): W(20,20) E(20,40) N(10,30) S(30,30)
    crews = [
        Crew(crew_id="gW", name="gW", status=CrewStatus.AVAILABLE, home_base=cc(spec, 20, 20)),
        Crew(crew_id="gE", name="gE", status=CrewStatus.AVAILABLE, home_base=cc(spec, 20, 40)),
        Crew(crew_id="gN", name="gN", status=CrewStatus.AVAILABLE, home_base=cc(spec, 10, 30)),
        Crew(crew_id="gS", name="gS", status=CrewStatus.AVAILABLE, home_base=cc(spec, 30, 30)),
    ]
    # 8 equal-value jobs in a tight cluster around (20,30); GENEROUS deadlines
    # (eta far out) so every job is feasible on ANY crew and even all-on-one.
    cluster = [(20, 29), (20, 31), (19, 30), (21, 30), (19, 29), (21, 31),
               (19, 31), (21, 29)]
    jobs = []
    for i, (r, c) in enumerate(cluster):
        p10 = NOW + 40 * 3600.0     # very loose deadline
        jobs.append(DispatchJob(
            client=ClientProperty(client_id=f"j{i}", loc=cc(spec, r, c),
                                  value_usd=1_000_000.0, priority_weight=1.0),
            incident_id=INC,
            eta=ThreatEta(eta_p10_s=p10, eta_p50_s=p10 + 3600.0,
                          eta_p90_s=p10 + 7200.0)))
    zones = [SafeZone("Z", cc(spec, 20, 58))]

    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
    per = {c.crew_id: 0 for c in crews}
    for cid, cp in plan.plans.items():
        per[cid] = len(cp.stops)
    print("cfg dispatch_objective=%r  lambda_travel=1%% of mean score (travel "
          "nearly free)" % cfg.dispatch_objective)
    print("\n8 equal jobs in a central cluster, 4 crews at N/E/S/W, all feasible:")
    print("  stops per crew:", per)
    print("  served:", plan.objective_breakdown["served"],
          " candidate:", plan.objective_breakdown["candidate"])
    busy = sum(1 for v in per.values() if v > 0)
    print(f"  => {busy}/4 crews used; max on one crew = {max(per.values())} "
          f"(balanced would be 2 each)")
    # show travel arithmetic: one-crew vs spread
    t_cross = travel.travel_time_s(cc(spec, 20, 20), cc(spec, 20, 30), cfg)
    print(f"\n  crew->cluster leg = {t_cross/60:.1f} min; intra-cluster hop ~"
          f"{travel.travel_time_s(cc(spec,20,29),cc(spec,20,31),cfg)/60:.2f} min")
    print("  one crew serving all 8 = 1 long inbound leg + 7 ~0 hops (LOW total "
          "travel); spreading = 4 long inbound legs (HIGHER travel) -> the "
          "objective PREFERS concentration.")


if __name__ == "__main__":
    main()
