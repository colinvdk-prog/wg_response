"""Shared helpers for the DISPATCH ENGINE review experiments (dr_*, throwaway).

Founder request #5 (2026-07-10): review the dispatch engine — travel time,
time-to-complete-job, fire propagation. These scripts are EVIDENCE for
docs/DISPATCH_REVIEW.md; product code is untouched.

Run from repo root:  py -3 scratch_review/dr_exp1_value_vs_count.py  etc.
Deterministic: all randomness via contracts.seeded_rng; fixed epoch; no wall clock.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse.contracts import (
    UNREACHED, ClientProperty, Crew, CrewBeacon, CrewStatus, DispatchJob,
    EnsembleArrival, GridSpec, LatLon, Raster, ResponseConfig, SafeZone,
    ThreatEta, seeded_rng,
)

T0 = 1_780_000_000.0

M_PER_DEG_LAT = 111_320.0
_EARTH_R_M = 6_371_000.0


def destination(origin: LatLon, bearing_deg: float, dist_m: float) -> LatLon:
    br = math.radians(bearing_deg)
    la1, lo1 = math.radians(origin.lat), math.radians(origin.lon)
    ang = dist_m / _EARTH_R_M
    la2 = math.asin(math.sin(la1) * math.cos(ang)
                    + math.cos(la1) * math.sin(ang) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(la1),
                           math.cos(ang) - math.sin(la1) * math.sin(la2))
    return LatLon(math.degrees(la2), math.degrees(lo2))


def all_safe_ensemble(incident_id: str = "I-SAFE") -> EnsembleArrival:
    """Ensemble far away + all UNREACHED: corridor/egress checks trivially
    safe, so an experiment can isolate the deadline/objective math."""
    spec = GridSpec(north_lat=44.0, west_lon=-100.0, cell_m=1000.0, nrows=2, ncols=2)
    inf_r = Raster(spec, [UNREACHED] * 4)
    return EnsembleArrival(spec=spec, members=[inf_r],
                           p10=Raster(spec, [UNREACHED] * 4),
                           p50=Raster(spec, [UNREACHED] * 4),
                           p90=Raster(spec, [UNREACHED] * 4),
                           t0_s=T0, incident_id=incident_id)


def mk_job(client_id: str, loc: LatLon, value_usd: float, priority: float,
           incident_id: str, p10_s: float, p50_s: float) -> DispatchJob:
    return DispatchJob(
        client=ClientProperty(client_id=client_id, loc=loc, name=client_id,
                              value_usd=value_usd, priority_weight=priority),
        incident_id=incident_id,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s,
                      eta_p90_s=p50_s + (p50_s - p10_s)))


def mk_crew(crew_id: str, loc: LatLon, now_s: float = T0) -> Crew:
    return Crew(crew_id=crew_id, name=crew_id, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=crew_id, loc=loc, t_s=now_s,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)


def describe_plan(plan, jobs) -> str:
    """One-line-per-crew rendering of a DispatchPlan + unserved reasons."""
    val = {j.client.client_id: j.client.value_usd for j in jobs}
    lines = []
    for cid in sorted(plan.plans):
        cp = plan.plans[cid]
        if not cp.stops:
            continue
        stops = " -> ".join(
            f"{s.client_id}(${val.get(s.client_id, 0)/1e6:.1f}M, "
            f"margin {s.margin_h*60:.0f}min, fresh {s.gel_fresh_frac:.2f})"
            for s in cp.stops)
        lines.append(f"  {cid}: {stops}")
    if not any(cp.stops for cp in plan.plans.values()):
        lines.append("  (no stops planned)")
    for u in plan.unserved:
        lines.append(f"  unserved {u.client_id}: {u.reason}")
    b = plan.objective_breakdown
    lines.append(f"  served={b['served']} value=${b['value_usd']/1e6:.2f}M "
                 f"drive={b['drive_h']:.2f}h travel_cost=${b['travel_cost_usd']:,.0f} "
                 f"J=${plan.objective:,.0f} candidate={b['candidate']}/"
                 f"{b['seed']} guardrail={b['guardrail'] or '-'} "
                 f"solver={b['solver_s']:.2f}s")
    return "\n".join(lines)
