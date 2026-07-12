"""diag_simui_triage.py — DEFECT 3: does dispatch abandon a doomed-close home
to save more homes farther out?

Constructed deterministic instance (test_dispatch.py fixture style: 1 km
cells, uniform UNREACHED corridor raster so ONLY the deadline chain binds,
zone co-located with staging):

    crew at (10,6); fire front to the WEST.
    A at (10,3)  — close to the front:  p10 = NOW + 1.5 h  (early deadline)
    B at (10,10) — farther out:         p10 = NOW + 2.0 h
    C at (10,12) — farther out:         p10 = NOW + 2.0 h

  Feasibility chain (tier-0 speed 9.4709 m/s, deploy 30 min, buffer 30 min,
  slack floor 15 min) makes EXACTLY: {A} feasible, {B,C} feasible,
  {A,B}, {A,C}, {B,A}, ... infeasible. So the crew can save A (1 home) or
  B+C (2 homes), never both.

  CASE 1 — equal values ($1M each):      count-max and value-max agree (B+C).
  CASE 2 — A worth $5M, B=C $700k:       value-max picks A (1 home);
                                         count-max would pick B+C (2 homes).
  CASE 3 — trickle+commitment: plan() sees ONLY A first (B/C not yet jobs);
           verify nothing later preempts A once adopted (simui integration:
           busy crews are never re-planned; frozen stops never dropped).

Also verifies the unserved REASON a triaged-away job gets today.
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety, travel
from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewStatus, DispatchJob, EnsembleArrival,
    GridSpec, LatLon, Raster, ResponseConfig, SafeZone, ThreatEta,
)

NOW = 1_800_000_000.0
INC = "inc1"
CFG = ResponseConfig()


def spec20():
    return GridSpec(north_lat=34.20, west_lon=-117.20, cell_m=1000.0,
                    nrows=20, ncols=20)


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def make_ens(spec):
    r = Raster(spec, [UNREACHED] * (spec.nrows * spec.ncols))
    return EnsembleArrival(spec=r.spec, members=[], p10=r, p50=r, p90=r,
                           t0_s=NOW, incident_id=INC)


def job(client_id, loc, p10_s, value):
    return DispatchJob(
        client=ClientProperty(client_id=client_id, loc=loc, value_usd=value,
                              priority_weight=1.0),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p10_s + 1800.0,
                      eta_p90_s=p10_s + 5400.0))


def show(tag, plan):
    stops = [(cid, [s.client_id for s in cp.stops])
             for cid, cp in plan.plans.items() if cp.stops]
    uns = [(u.client_id, u.reason) for u in plan.unserved]
    print(f"  {tag}: served={sum(len(s[1]) for s in stops)} stops={stops} "
          f"unserved={uns} candidate={plan.objective_breakdown['candidate']}")
    return stops, uns


def main():
    spec = spec20()
    ens = {INC: make_ens(spec)}
    crew_loc = cc(spec, 10, 6)
    zone = SafeZone("z1", crew_loc)
    A, B, C = cc(spec, 10, 3), cc(spec, 10, 10), cc(spec, 10, 12)
    crew = Crew(crew_id="G1", name="G1", status=CrewStatus.AVAILABLE,
                home_base=crew_loc)
    pA = NOW + 1.5 * 3600.0
    pBC = NOW + 2.0 * 3600.0

    # --- sanity: print the feasibility frontier the instance encodes
    v = safety._corridor_speed_mps(CFG)
    print(f"tier-0 effective speed {v:.4f} m/s; deploy {CFG.deploy_min} min; "
          f"slack floor {CFG.slack_min} min; buffer {CFG.egress_buffer_min} min")
    for name, loc, p10 in (("A", A, pA), ("B", B, pBC), ("C", C, pBC)):
        tt = travel.travel_time_s(crew_loc, loc, CFG)
        cap = p10 - (CFG.egress_buffer_min + CFG.ember_buffer_min) * 60.0 \
            - CFG.slack_min * 60.0
        print(f"  {name}: travel {tt:5.0f}s solo-depart {tt+1800:6.0f}s "
              f"cap(NOW+) {cap-NOW:6.0f}s feasible-solo={tt+1800 <= cap-NOW}")
    # chains
    for chain in (("A", "B"), ("B", "C"), ("A", "C"), ("B", "A")):
        locs = {"A": A, "B": B, "C": C}
        p10s = {"A": pA, "B": pBC, "C": pBC}
        t = 0.0
        loc = crew_loc
        ok = True
        for s in chain:
            t += travel.travel_time_s(loc, locs[s], CFG)
            t += 1800.0
            cap = p10s[s] - NOW - 2700.0
            if t > cap:
                ok = False
                break
            loc = locs[s]
        print(f"  chain {'->'.join(chain)}: {'FEASIBLE' if ok else 'infeasible'}")

    print("\nCASE 1 — equal values ($1M each): value-max == count-max")
    jobs = [job("A", A, pA, 1_000_000.0), job("B", B, pBC, 1_000_000.0),
            job("C", C, pBC, 1_000_000.0)]
    p = dispatch.plan([crew], jobs, ens, [zone], CFG, NOW)
    show("plan", p)

    print("\nCASE 2 — A=$5M, B=C=$700k: value-max vs count-max")
    jobs = [job("A", A, pA, 5_000_000.0), job("B", B, pBC, 700_000.0),
            job("C", C, pBC, 700_000.0)]
    p = dispatch.plan([crew], jobs, ens, [zone], CFG, NOW)
    stops, uns = show("plan", p)
    n = sum(len(s[1]) for s in stops)
    print(f"  => solver saved {n} home(s); count-max would save 2 (B+C). "
          f"{'VALUE-FIRST CONFIRMED' if n == 1 else 'unexpected'}")

    print("\nCASE 3 — trickle + commitment: A adopted first, B+C appear later")
    jobs0 = [job("A", A, pA, 700_000.0)]
    p0 = dispatch.plan([crew], jobs0, ens, [zone], CFG, NOW)
    show("t0 plan (only A jobbed)", p0)
    # 2 min later B+C appear. A is still feasible, B->C still feasible as a
    # pair, A+anything still impossible. simui's tick() step 4 would not even
    # call plan() for the (now busy) crew; the ENGINE path with an incumbent
    # FREEZES A (service_start < now + commitment_horizon_min):
    t1 = NOW + 120.0
    jobs1 = [job("A", A, pA, 700_000.0), job("B", B, pBC, 700_000.0),
             job("C", C, pBC, 700_000.0)]
    p1 = dispatch.plan([crew], jobs1, ens, [zone], CFG, t1, incumbent=p0)
    show("t1 WITH incumbent (sim.py path; A frozen by commitment)", p1)
    print("  (simui path is stricter still: crew busy => plan() never called; "
          "A kept, B+C never even considered)")
    # and the same call WITHOUT the incumbent freeze (a free re-plan):
    p2 = dispatch.plan([crew], jobs1, ens, [zone], CFG, t1)
    show("t1 NO incumbent (what a free re-plan could still save)", p2)


if __name__ == "__main__":
    main()
