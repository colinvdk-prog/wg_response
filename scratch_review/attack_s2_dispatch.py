"""ADVERSARIAL VERIFIER (Session 2) — attack #4: B2 never-worse-than-greedy.

Claims under attack (Sprint 2 ticket B2; dispatch.plan docstring):
  4a. On seeded instances mixing feasible / tight / infeasible jobs, the
      emitted plan's collected value is >= the value of a RAW greedy-EDF
      baseline EVERY time. The baseline here is an INDEPENDENT
      reimplementation of greedy earliest-deadline-first from the documented
      algorithm (not dispatch._edf_insert), using the public safety seams for
      corridors/egress — so a bug in the solver's own greedy cannot vouch for
      itself.
  4b. Frozen (committed) stops are never moved/dropped by the multi-start
      construction, the exact micro-solver, or the ILS.
  4c. Determinism: two identical plan() calls produce identical plan content
      (excluding the wall-clock diagnostic objective_breakdown['solver_s']).
  4d. SAFETY SEMANTICS 7 end-to-end: every emitted stop carries
      margin_h * 60 >= cfg.slack_min.

Run from repo root (takes ~1-2 min):
    py -3 -m pytest scratch_review/attack_s2_dispatch.py -v
"""
from __future__ import annotations

import copy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety
from wgresponse.contracts import (Crew, CrewBeacon, CrewStatus, ClientProperty,
                                  DispatchJob, EnsembleArrival, GridSpec,
                                  LatLon, Raster, ResponseConfig, SafeZone,
                                  ThreatEta, UNREACHED, seeded_rng)
from wgresponse.travel import travel_time_s

NOW = 1_780_000_000.0


def _rc_center(spec, rr, cc):
    return safety._rc_center(spec, rr, cc)


def _mk_ens(spec, origin_rc, ros_mps, horizon_h=96.0):
    """Synthetic radial p10 arrival raster (plan() consumes only ens.p10)."""
    orc = _rc_center(spec, *origin_rc)
    data = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            d = safety._haversine_m(orc, _rc_center(spec, r + 0.0, c + 0.0))
            t = NOW + d / ros_mps
            data.append(t if t <= NOW + horizon_h * 3600.0 else UNREACHED)
    ras = Raster(spec, data)
    return EnsembleArrival(spec=spec, members=[ras], p10=ras, p50=ras,
                           p90=ras, t0_s=NOW, incident_id="")


def _instance(k):
    """Seeded instance: (crews, jobs, ens, zones, cfg). Mix of feasible /
    tight / infeasible deadlines; 1-2 incidents; 2-4 crews; 5-14 jobs."""
    rng = seeded_rng("vf-s2-b2-instance", k)
    spec = GridSpec(north_lat=34.80, west_lon=-119.60, cell_m=250.0,
                    nrows=36, ncols=56)
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    n_crews = rng.randrange(2, 5)
    n_jobs = rng.randrange(5, 15)
    n_inc = 1 if k % 3 else 2

    ens = {}
    for i in range(n_inc):
        origin = (rng.uniform(4, 30), rng.uniform(2, 12))
        ens[f"inc-{i}"] = _mk_ens(spec, origin, rng.uniform(0.15, 0.6))

    # crews + zones in the eastern (later-arrival) half
    crews = []
    for i in range(n_crews):
        loc = _rc_center(spec, rng.uniform(2, 33), rng.uniform(30, 54))
        crews.append(Crew(crew_id=f"crew-{i}", name=f"Crew {i}",
                          status=CrewStatus.AVAILABLE,
                          last_beacon=CrewBeacon(f"crew-{i}", loc, NOW),
                          home_base=loc))
    zones = [SafeZone(f"z{i}", _rc_center(spec, rng.uniform(2, 33),
                                          rng.uniform(44, 55)))
             for i in range(2)]

    jobs = []
    for j in range(n_jobs):
        loc = _rc_center(spec, rng.uniform(2, 33), rng.uniform(18, 52))
        client = ClientProperty(client_id=f"C{j:02d}", loc=loc,
                                value_usd=rng.uniform(0.4e6, 3.5e6),
                                priority_weight=2.0 if rng.random() < 0.3 else 1.0)
        min_tt = min(travel_time_s(c.loc(), loc, cfg) for c in crews)
        need = min_tt + cfg.deploy_min * 60.0 + (cfg.egress_buffer_min
                                                 + cfg.slack_min) * 60.0
        kind = rng.random()
        if kind < 0.45:                     # generous
            p10 = NOW + need + rng.uniform(4.0, 30.0) * 3600.0
        elif kind < 0.75:                   # tight (within ~40 min of the wire)
            p10 = NOW + need + rng.uniform(0.0, 2400.0)
        elif kind < 0.9:                    # infeasible deadline
            p10 = NOW + rng.uniform(0.0, need * 0.8)
        else:                               # fire never arrives at the median
            jobs.append(DispatchJob(client=client, incident_id="inc-0",
                                    eta=ThreatEta(UNREACHED, UNREACHED,
                                                  UNREACHED)))
            continue
        p50 = p10 + rng.uniform(1.0, 8.0) * 3600.0
        p90 = p50 + rng.uniform(1.0, 8.0) * 3600.0
        iid = f"inc-{rng.randrange(n_inc)}"
        jobs.append(DispatchJob(client=client, incident_id=iid,
                                eta=ThreatEta(p10, p50, p90)))
        if n_inc == 2 and rng.random() < 0.25:   # duplicate client, 2nd fire
            jobs.append(DispatchJob(
                client=client, incident_id="inc-1" if iid == "inc-0" else "inc-0",
                eta=ThreatEta(p10 + 3600.0, p50 + 3600.0, p90 + 3600.0)))
    return crews, jobs, ens, zones, cfg


# ---------------------------------------------------------------------------
# Independent greedy-EDF baseline (documented algorithm, public safety seams)
# ---------------------------------------------------------------------------

def _gel_frac(job, depart_s, cfg):
    eta50 = job.eta.eta_p50_s
    if not math.isfinite(eta50):
        return 0.0
    if math.isfinite(job.earliest_useful_start_s):
        window_h = max(0.0, (eta50 - job.earliest_useful_start_s) / 3600.0)
    else:
        window_h = cfg.gel_window_h
    full_h = min(cfg.gel_full_strength_h, window_h)
    dt_h = (eta50 - depart_s) / 3600.0
    if dt_h <= full_h:
        return 1.0
    if dt_h >= window_h:
        return 0.0
    return (window_h - dt_h) / (window_h - full_h)


def _score(job, cfg):
    return job.client.priority_weight * job.client.value_usd * cfg.gel_effectiveness


def greedy_edf_value(crews, jobs, ens, zones, cfg, now_s):
    """Value collected by the documented raw greedy-EDF baseline."""
    eligible = sorted((c for c in crews if c.loc() is not None
                       and c.status not in (CrewStatus.OFF_DUTY,
                                            CrewStatus.UNKNOWN)),
                      key=lambda c: c.crew_id)
    rasters = [safety._erode(ens[iid].p10, cfg.corridor_erosion_m)
               for iid in sorted(ens)]
    speed = safety._corridor_speed_mps(cfg)

    # dedupe per client: earliest p10, ties p50 then incident_id
    surviving = {}
    for job in sorted(jobs, key=lambda j: (j.client.client_id, j.eta.eta_p10_s,
                                           j.eta.eta_p50_s, j.incident_id)):
        surviving.setdefault(job.client.client_id, job)
    jobmap = {}
    for job in sorted(surviving.values(),
                      key=lambda j: (j.client.client_id, j.incident_id)):
        safety.derive_job_windows(job, cfg)
        if not eligible:
            continue
        min_tt = min(travel_time_s(c.loc(), job.client.loc, cfg)
                     for c in eligible)
        if (job.earliest_useful_start_s == UNREACHED
                or job.earliest_useful_start_s > job.latest_service_start_s + 1e-6
                or now_s + min_tt > job.latest_service_start_s + 1e-6):
            continue
        jobmap[(job.client.client_id, job.incident_id)] = job

    def corridors_ok(a, b, depart):
        return all(safety.corridor_safe(a, b, depart, speed, ras, cfg).safe
                   for ras in rasters)

    def egress_ok(loc, depart):
        return any(all(safety.corridor_safe(loc, z.loc, depart, speed, ras,
                                            cfg).safe for ras in rasters)
                   for z in zones)

    def extend(loc, t, job):
        tt = travel_time_s(loc, job.client.loc, cfg)
        arrive = t + tt
        service = arrive if job.earliest_useful_start_s <= arrive \
            else job.earliest_useful_start_s
        depart = service + cfg.deploy_min * 60.0
        if depart > job.latest_depart_s + 1e-6:
            return None
        if depart > job.latest_depart_s - cfg.slack_min * 60.0 + 1e-6:
            return None
        if not corridors_ok(loc, job.client.loc, t):
            return None
        if not egress_ok(job.client.loc, depart):
            return None
        return depart

    ends = {c.crew_id: (c.loc(), now_s) for c in eligible}
    value = 0.0
    for key in sorted(jobmap, key=lambda k: (jobmap[k].latest_service_start_s, k)):
        job = jobmap[key]
        best = None
        for c in eligible:
            loc, t = ends[c.crew_id]
            depart = extend(loc, t, job)
            if depart is None:
                continue
            cand = (depart, c.crew_id)
            if best is None or cand < best:
                best = cand
        if best is not None:
            depart, cid = best
            ends[cid] = (job.client.loc, depart)
            value += _score(job, cfg) * _gel_frac(job, depart, cfg)
    return value


def _emitted_value(plan_obj, jobs, cfg):
    by_key = {}
    for j in jobs:
        by_key[(j.client.client_id, j.incident_id)] = j
    total = 0.0
    for cp in plan_obj.plans.values():
        for st in cp.stops:
            job = by_key[(st.client_id, st.incident_id)]
            total += _score(job, cfg) * st.gel_fresh_frac
    return total


# ---------------------------------------------------------------------------
# 4a + 4d — guardrail sweep over 30 seeded instances
# ---------------------------------------------------------------------------

def test_b2_final_value_never_below_independent_greedy_30_instances():
    failures = []
    slack_viol = []
    served_any = 0
    for k in range(30):
        crews, jobs, ens, zones, cfg = _instance(k)
        p = dispatch.plan(crews, copy.deepcopy(jobs), ens, zones, cfg, NOW)
        pv = _emitted_value(p, jobs, cfg)
        gv = greedy_edf_value(crews, copy.deepcopy(jobs), ens, zones, cfg, NOW)
        if pv < gv - max(1.0, 1e-9 * abs(gv)):
            failures.append((k, pv, gv))
        for cid, cp in p.plans.items():
            served_any += len(cp.stops)
            for st in cp.stops:
                if st.margin_h * 60.0 < cfg.slack_min - 1e-9:
                    slack_viol.append((k, cid, st.client_id, st.margin_h * 60.0))
    assert served_any >= 60, f"sweep degenerate: only {served_any} stops emitted"
    assert not slack_viol, (
        f"SAFETY SEMANTICS 7 broken on emitted stops: {slack_viol[:5]}")
    assert not failures, (
        "plan value fell below the independent raw greedy-EDF baseline on "
        f"{len(failures)}/30 instances: "
        + "; ".join(f"k={k} plan={pv:,.0f} greedy={gv:,.0f}"
                    for k, pv, gv in failures))


# ---------------------------------------------------------------------------
# 4b — frozen stops survive the multi-start / exact solver / ILS
# ---------------------------------------------------------------------------

def _generous_instance(k):
    """All-generous deadlines: nothing can legally become infeasible within a
    5-minute replan gap, so a dropped/moved frozen stop can only mean the
    multi-start / exact solver / ILS violated the commitment (the product is
    ALLOWED to drop now-infeasible frozen stops — 'safety trumps commitment' —
    which the mixed generator triggers legitimately; this generator cannot)."""
    rng = seeded_rng("vf-s2-b2-frozen", k)
    spec = GridSpec(north_lat=34.80, west_lon=-119.60, cell_m=250.0,
                    nrows=36, ncols=56)
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    ens = {"inc-0": _mk_ens(spec, (rng.uniform(4, 30), rng.uniform(2, 8)),
                            rng.uniform(0.15, 0.35))}
    crews = []
    for i in range(rng.randrange(2, 4)):
        loc = _rc_center(spec, rng.uniform(2, 33), rng.uniform(34, 54))
        crews.append(Crew(crew_id=f"crew-{i}", name=f"Crew {i}",
                          status=CrewStatus.AVAILABLE,
                          last_beacon=CrewBeacon(f"crew-{i}", loc, NOW),
                          home_base=loc))
    zones = [SafeZone(f"z{i}", _rc_center(spec, rng.uniform(2, 33),
                                          rng.uniform(46, 55)))
             for i in range(2)]
    jobs = []
    for j in range(rng.randrange(5, 9)):
        loc = _rc_center(spec, rng.uniform(2, 33), rng.uniform(28, 52))
        p10 = NOW + rng.uniform(14.0, 30.0) * 3600.0
        p50 = p10 + rng.uniform(2.0, 8.0) * 3600.0
        jobs.append(DispatchJob(
            client=ClientProperty(client_id=f"C{j:02d}", loc=loc,
                                  value_usd=rng.uniform(0.4e6, 3.5e6),
                                  priority_weight=1.0),
            incident_id="inc-0", eta=ThreatEta(p10, p50, p50 + 4 * 3600.0)))
    return crews, jobs, ens, zones, cfg


def test_b2_frozen_stops_never_moved_or_dropped():
    violated = []
    checked = 0
    for k in range(10):
        crews, jobs, ens, zones, cfg = _generous_instance(k)
        cold = dispatch.plan(crews, copy.deepcopy(jobs), ens, zones, cfg, NOW)
        now2 = NOW + 300.0
        frozen = {}                        # crew -> committed prefix keys
        for cid, cp in cold.plans.items():
            pre = []
            for st in cp.stops:
                if st.service_start_s < now2 + cfg.commitment_horizon_min * 60.0:
                    pre.append((st.client_id, st.incident_id))
                else:
                    break
            if pre:
                frozen[cid] = pre
        if not frozen:
            continue
        checked += 1
        # tempt the solver: add a high-value job right next to each crew
        jobs2 = copy.deepcopy(jobs)
        for i, c in enumerate(crews):
            loc = c.loc()
            jobs2.append(DispatchJob(
                client=ClientProperty(client_id=f"TEMPT-{i}",
                                      loc=LatLon(loc.lat + 0.002, loc.lon),
                                      value_usd=9e6, priority_weight=2.0),
                incident_id="inc-0",
                eta=ThreatEta(NOW + 30 * 3600.0, NOW + 40 * 3600.0,
                              NOW + 50 * 3600.0)))
        warm = dispatch.plan(crews, jobs2, ens, zones, cfg, now2,
                             incumbent=cold)
        for cid, pre in frozen.items():
            got = [(st.client_id, st.incident_id)
                   for st in warm.plans.get(cid, dispatch.CrewPlan(cid)).stops]
            if got[:len(pre)] != pre:
                violated.append((k, cid, pre, got[:len(pre) + 1]))
    assert checked >= 5, f"only {checked} instances had committed stops"
    assert not violated, f"frozen commitments broken: {violated}"


# ---------------------------------------------------------------------------
# 4c — determinism
# ---------------------------------------------------------------------------

def _normalize(p):
    plans = {}
    for cid, cp in sorted(p.plans.items()):
        plans[cid] = [(st.client_id, st.incident_id, st.arrive_s,
                       st.service_start_s, st.depart_s, st.latest_depart_s,
                       st.margin_h, st.gel_fresh_frac) for st in cp.stops] + \
                     [cp.egress_zone_id, cp.committed_until_s]
    bd = {k: v for k, v in p.objective_breakdown.items() if k != "solver_s"}
    uns = sorted((u.client_id, u.incident_id, u.reason) for u in p.unserved)
    return (p.computed_at_s, plans, uns, p.objective, bd,
            p.churn_from_incumbent)


def test_b2_identical_calls_identical_plans():
    for k in (1, 4, 9, 13, 22, 27):
        crews, jobs, ens, zones, cfg = _instance(k)
        p1 = dispatch.plan(crews, copy.deepcopy(jobs), ens, zones, cfg, NOW)
        p2 = dispatch.plan(crews, copy.deepcopy(jobs), ens, zones, cfg, NOW)
        assert _normalize(p1) == _normalize(p2), (
            f"instance {k}: two identical plan() calls disagree")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
