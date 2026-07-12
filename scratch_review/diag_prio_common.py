"""Shared helpers for the PRIORITIZATION / URGENCY-ORDERING review (diag_prio_*).

DIAGNOSIS ONLY (product code read-only). Founder request: on Malibu 48/3 extreme
the "wrong homes" were protected. This review asks a question distinct from the
count-vs-value (Defect 3) and concentration (Defect 2) reviews already on file:
WITHIN homes-mode (now the live default), does dispatch protect the SOONER-to-burn
savable home first when it cannot do both?

Run from repo root:  py -3 scratch_review/diag_prio_urgency.py  etc.
Deterministic: fixed epoch, all randomness via contracts.seeded_rng, no wall clock
dependence except the solver budget (which does not change these tiny instances).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety, travel  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    UNREACHED, ClientProperty, Crew, CrewBeacon, CrewStatus, CrewPlan,
    DispatchJob, DispatchPlan, EnsembleArrival, GridSpec, LatLon, PlannedStop,
    Raster, ResponseConfig, SafeZone, ThreatEta,
)

NOW = 1_790_000_000.0
INC = "inc1"


def spec_big():
    # 1 km cells, 60 wide -> 60 km span
    return GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                    nrows=40, ncols=60)


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def uniform(spec, v):
    return Raster(spec, [v] * (spec.nrows * spec.ncols))


def make_ens(raster):
    return EnsembleArrival(spec=raster.spec, members=[], p10=raster, p50=raster,
                           p90=raster, t0_s=NOW, incident_id=INC)


def mk_job(cid, loc, p10_s, p50_s, value, weight=1.0):
    return DispatchJob(
        client=ClientProperty(client_id=cid, loc=loc, name=cid, value_usd=value,
                              priority_weight=weight),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s,
                      eta_p90_s=p50_s + (p50_s - p10_s)))


def mk_crew(cid, loc):
    return Crew(crew_id=cid, name=cid, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=cid, loc=loc, t_s=NOW,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)


def tt_min(a, b, cfg):
    return travel.travel_time_s(a, b, cfg) / 60.0


def served_order(plan):
    """crew_id -> [client_id,...] in visit order."""
    out = {}
    for cid in sorted(plan.plans):
        out[cid] = [st.client_id for st in plan.plans[cid].stops]
    return out


def served_set(plan):
    s = set()
    for cp in plan.plans.values():
        for st in cp.stops:
            s.add(st.client_id)
    return s


def describe(plan, jobs):
    val = {j.client.client_id: j.client.value_usd for j in jobs}
    p50 = {j.client.client_id: j.eta.eta_p50_s for j in jobs}
    lines = []
    for cid in sorted(plan.plans):
        cp = plan.plans[cid]
        if not cp.stops:
            continue
        seq = " -> ".join(
            f"{st.client_id}(${val.get(st.client_id,0)/1e6:.2f}M,"
            f" p50+{(p50.get(st.client_id,NOW)-NOW)/3600:.1f}h,"
            f" depart+{(st.depart_s-NOW)/3600:.1f}h,"
            f" fresh {st.gel_fresh_frac:.2f}, margin {st.margin_h*60:.0f}m)"
            for st in cp.stops)
        lines.append(f"  {cid}: {seq}")
    for u in plan.unserved:
        lines.append(f"  UNSERVED {u.client_id}: {u.reason}")
    b = plan.objective_breakdown
    lines.append(f"  served={b['served']} value=${b['value_usd']/1e6:.3f}M "
                 f"cand={b['candidate']}/{b['seed']} guardrail={b['guardrail'] or '-'}")
    return "\n".join(lines)
