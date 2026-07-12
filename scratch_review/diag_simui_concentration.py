"""diag_simui_concentration.py — DEFECT 2 mechanism isolation.

Constructed instance: 2 crews staged at OPPOSITE ends of a line of 4 equal
jobs, all deadlines LOOSE (p10 = NOW + 12 h). A count/makespan-balanced
dispatcher would give each crew the 2 jobs on its side (all gel applied
within ~1.3 h). What does the value-objective solver do?

Then the same instance with TIGHT deadlines (p10 = NOW + 2.2 h) where one
crew cannot serve all 4 — the deadline chain forces spreading.

This isolates the concentration mechanism: J = value - lambda_travel*drive_h
- churn has NO completion-time / balance term, and total drive is MINIMIZED
by chaining; only deadline pressure ever forces a second crew in.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety
from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewStatus, DispatchJob, EnsembleArrival,
    GridSpec, Raster, ResponseConfig, SafeZone, ThreatEta,
)

NOW = 1_800_000_000.0
INC = "inc1"
CFG = ResponseConfig()


def main():
    spec = GridSpec(north_lat=34.20, west_lon=-117.20, cell_m=1000.0,
                    nrows=20, ncols=20)
    r = Raster(spec, [UNREACHED] * 400)
    ens = {INC: EnsembleArrival(spec=spec, members=[], p10=r, p50=r, p90=r,
                                t0_s=NOW, incident_id=INC)}
    cc = lambda rr, c: safety._rc_center(spec, rr, c)
    crews = [Crew(crew_id="G1", name="G1", status=CrewStatus.AVAILABLE,
                  home_base=cc(10, 2)),
             Crew(crew_id="G2", name="G2", status=CrewStatus.AVAILABLE,
                  home_base=cc(10, 13))]
    zones = [SafeZone("z1", cc(10, 2)), SafeZone("z2", cc(10, 13))]

    def jobs(p10):
        out = []
        for i, col in enumerate((5, 6, 9, 10), 1):
            out.append(DispatchJob(
                client=ClientProperty(client_id=f"c{i}", loc=cc(10, col),
                                      value_usd=1_000_000.0,
                                      priority_weight=1.0),
                incident_id=INC,
                eta=ThreatEta(eta_p10_s=p10, eta_p50_s=p10 + 1800.0,
                              eta_p90_s=p10 + 5400.0)))
        return out

    for tag, p10 in (("LOOSE deadlines (p10 = NOW+12 h)", NOW + 12 * 3600.0),
                     ("TIGHT deadlines (p10 = NOW+2.2 h)", NOW + 2.2 * 3600.0)):
        p = dispatch.plan(crews, jobs(p10), ens, zones, CFG, NOW)
        stops = {cid: [s.client_id for s in cp.stops]
                 for cid, cp in p.plans.items()}
        last_depart = max((s.depart_s for cp in p.plans.values()
                           for s in cp.stops), default=float("nan"))
        bd = p.objective_breakdown
        print(f"{tag}:")
        print(f"  assignment {stops}  unserved={[(u.client_id, u.reason) for u in p.unserved]}")
        print(f"  all-work-done at NOW+{(last_depart-NOW)/3600.0:.2f} h; "
              f"drive_h {bd['drive_h']:.2f}; candidate {bd['candidate']}; "
              f"objective {p.objective:,.0f}")


if __name__ == "__main__":
    main()
