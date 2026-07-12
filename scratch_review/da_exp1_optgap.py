"""EXP 1 — SOLVER OPTIMALITY GAP.

24 seeded instances, 2 crews x 5-7 jobs, tight deadlines (p10 arrival 1.5-6 h
out), corridors trivially safe, gel always fresh (p50 within full-strength of
any feasible depart). In that regime the plan objective reduces EXACTLY to

    J = sum(priority * value * gel_effectiveness) - lambda_travel * drive_h

(lambda_travel = 1% of mean potential stop score / drive-hour, churn = 0), so a
subset-DP brute force over ALL (subset, ordered-route) splits across the 2
crews yields the true optimum. Compares dispatch.plan and a naive
earliest-deadline-first (EDF) greedy against it.

Run: py -3 scratch_review/da_exp1_optgap.py
"""
from __future__ import annotations

import math
from itertools import permutations

from da_common import T0, all_safe_ensemble, destination, mk_crew, mk_job

from wgresponse import dispatch
from wgresponse import safety
from wgresponse.contracts import LatLon, ResponseConfig, SafeZone, seeded_rng
from wgresponse.travel import travel_time_s

CENTER = LatLon(34.2000, -117.6000)
_EPS = 1e-6


def build_instance(i: int):
    rng = seeded_rng("da_exp1", i)
    n_jobs = 5 + (i % 3)
    crews = []
    for k in range(2):
        loc = destination(CENTER, rng.uniform(0, 360), rng.uniform(4000, 14000))
        crews.append(mk_crew(f"G{k+1}", loc))
    jobs = []
    for j in range(n_jobs):
        loc = destination(CENTER, rng.uniform(0, 360), rng.uniform(2000, 12000))
        p10 = T0 + rng.uniform(1.0, 3.2) * 3600.0   # latest svc start 0-2.2 h out
        p50 = p10 + rng.uniform(0.3, 1.5) * 3600.0
        jobs.append(mk_job(f"J{j+1:02d}", loc, rng.uniform(800_000, 3_500_000),
                           2.0 if rng.random() < 0.25 else 1.0, "I1", p10, p50))
    zones = [SafeZone("Z1", destination(CENTER, 270.0, 20_000.0), "west"),
             SafeZone("Z2", destination(CENTER, 180.0, 20_000.0), "south")]
    return crews, jobs, zones


def prune_and_lambda(crews, jobs, cfg):
    """Replicate plan() step-1 pruning + lambda_travel derivation exactly."""
    jobmap = {}
    for job in sorted(jobs, key=lambda j: (j.client.client_id, j.incident_id)):
        key = (job.client.client_id, job.incident_id)
        if key in jobmap:
            continue
        safety.derive_job_windows(job, cfg)
        min_tt = min(travel_time_s(c.loc(), job.client.loc, cfg) for c in crews)
        if (job.earliest_useful_start_s == math.inf
                or job.earliest_useful_start_s > job.latest_service_start_s + _EPS
                or T0 + min_tt > job.latest_service_start_s + _EPS):
            continue
        jobmap[key] = job
    scores = [j.client.priority_weight * j.client.value_usd * cfg.gel_effectiveness
              for j in jobmap.values()]
    mean_score = (sum(scores) / len(scores)) if scores else 1.0
    return jobmap, 0.01 * mean_score


def crew_best_by_set(crew, jobmap, cfg):
    """DFS over all ordered feasible routes for one crew; per served frozenset
    record the MIN total drive_h (value per set is constant: gel_frac==1)."""
    keys = sorted(jobmap)
    best = {frozenset(): 0.0}

    def dfs(loc, t, served, drive_h):
        for key in keys:
            if key in served:
                continue
            job = jobmap[key]
            tt = travel_time_s(loc, job.client.loc, cfg)
            arrive = t + tt
            service = max(arrive, job.earliest_useful_start_s)
            depart = service + cfg.deploy_min * 60.0
            if depart > job.latest_depart_s + _EPS:
                continue
            # gel-freshness sanity: this experiment is designed so frac == 1
            dt_h = (job.eta.eta_p50_s - depart) / 3600.0
            assert dt_h <= cfg.gel_full_strength_h + 1e-9, "design violated"
            ns = served | {key}
            nd = drive_h + tt / 3600.0
            fs = frozenset(ns)
            if fs not in best or nd < best[fs]:
                best[fs] = nd
            dfs(job.client.loc, depart, ns, nd)

    dfs(crew.loc(), T0, set(), 0.0)
    return best


def brute_force_opt(crews, jobmap, lam, cfg):
    score = {k: j.client.priority_weight * j.client.value_usd * cfg.gel_effectiveness
             for k, j in jobmap.items()}
    b1 = crew_best_by_set(crews[0], jobmap, cfg)
    b2 = crew_best_by_set(crews[1], jobmap, cfg)
    best_j, best_served = 0.0, 0
    for s1, d1 in b1.items():
        for s2, d2 in b2.items():
            if s1 & s2:
                continue
            val = sum(score[k] for k in s1) + sum(score[k] for k in s2)
            j = val - lam * (d1 + d2)
            if j > best_j + 1e-9 or (abs(j - best_j) <= 1e-9
                                     and len(s1) + len(s2) > best_served):
                best_j, best_served = j, len(s1) + len(s2)
    return best_j, best_served


def edf_greedy(crews, jobmap, lam, cfg):
    """Naive earliest-deadline-first append-only greedy (the obvious baseline)."""
    state = {c.crew_id: {"loc": c.loc(), "t": T0, "drive_h": 0.0, "n": 0} for c in crews}
    score = {k: j.client.priority_weight * j.client.value_usd * cfg.gel_effectiveness
             for k, j in jobmap.items()}
    value, served = 0.0, 0
    for key in sorted(jobmap, key=lambda k: (jobmap[k].latest_service_start_s, k)):
        job = jobmap[key]
        cand = None
        for cid in sorted(state):
            st = state[cid]
            tt = travel_time_s(st["loc"], job.client.loc, cfg)
            arrive = st["t"] + tt
            service = max(arrive, job.earliest_useful_start_s)
            depart = service + cfg.deploy_min * 60.0
            if depart > job.latest_depart_s + _EPS:
                continue
            if cand is None or depart < cand[0]:
                cand = (depart, cid, tt)
        if cand is None:
            continue
        depart, cid, tt = cand
        st = state[cid]
        st["loc"], st["t"] = job.client.loc, depart
        st["drive_h"] += tt / 3600.0
        value += score[key]
        served += 1
    drive = sum(st["drive_h"] for st in state.values())
    return value - lam * drive, served


def main():
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    ens = {"I1": all_safe_ensemble("I1")}
    rows = []
    for i in range(24):
        crews, jobs, zones = build_instance(i)
        jobmap, lam = prune_and_lambda(crews, jobs, cfg)
        opt_j, opt_served = brute_force_opt(crews, jobmap, lam, cfg)
        # rebuild fresh jobs for plan() (derive_job_windows mutates)
        crews2, jobs2, _ = build_instance(i)
        plan = dispatch.plan(crews2, jobs2, ens, zones, cfg, T0)
        for cp in plan.plans.values():
            for st in cp.stops:
                assert abs(st.gel_fresh_frac - 1.0) < 1e-9, "gel frac drifted from design"
        edf_j, edf_served = edf_greedy(crews, jobmap, lam, cfg)
        gap = 100.0 * (opt_j - plan.objective) / opt_j if opt_j > 0 else 0.0
        edf_gap = 100.0 * (opt_j - edf_j) / opt_j if opt_j > 0 else 0.0
        rows.append(dict(i=i, n=len(jobmap), opt_j=opt_j, opt_served=opt_served,
                         plan_j=plan.objective,
                         plan_served=plan.objective_breakdown["served"],
                         gap_pct=gap, edf_j=edf_j, edf_served=edf_served,
                         edf_gap_pct=edf_gap,
                         solver_s=plan.objective_breakdown["solver_s"]))
    print(f"{'inst':>4} {'n':>2} {'opt_J($)':>12} {'plan_J($)':>12} {'gap%':>7} "
          f"{'srv opt/plan/edf':>17} {'edf_gap%':>9} {'solver_s':>8}")
    for r in rows:
        print(f"{r['i']:>4} {r['n']:>2} {r['opt_j']:>12,.0f} {r['plan_j']:>12,.0f} "
              f"{r['gap_pct']:>7.3f} {r['opt_served']:>6}/{r['plan_served']}/{r['edf_served']:<6} "
              f"{r['edf_gap_pct']:>9.3f} {r['solver_s']:>8.2f}")
    gaps = [r["gap_pct"] for r in rows]
    egaps = [r["edf_gap_pct"] for r in rows]
    misses = sum(1 for r in rows if r["plan_served"] < r["opt_served"])
    emisses = sum(1 for r in rows if r["edf_served"] < r["opt_served"])
    print(f"\nplan  gap: mean {sum(gaps)/len(gaps):.3f}%  max {max(gaps):.3f}%  "
          f"served-count misses {misses}/{len(rows)}")
    print(f"EDF   gap: mean {sum(egaps)/len(egaps):.3f}%  max {max(egaps):.3f}%  "
          f"served-count misses {emisses}/{len(rows)}")


if __name__ == "__main__":
    main()
