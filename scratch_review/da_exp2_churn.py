"""EXP 2 — PLAN STABILITY / CHURN under forecast wobble.

Replays 8 consecutive plan cycles (900 s apart) over 12 jobs / 3 crews.
Between cycles each job's ETA wobbles by a seeded random walk (+/- up to
0.35 h per cycle) around a fixed true arrival — the 'flickering forecast'
regime the anti-thrash machinery exists for. Two configs:

  A) defaults (churn_penalty 5%, adoption_threshold 3%, commitment 20 min)
  B) anti-thrash OFF (churn_penalty 0, adoption_threshold 0)

Measured per transition: stops reassigned to a different crew, order changes
within a crew, frozen-stop violations (stop whose incumbent service_start is
inside now+20 min that moved crew or got dropped while still feasible),
candidate tag adopted (warm vs ils).

Run: py -3 scratch_review/da_exp2_churn.py
"""
from __future__ import annotations

import math

from da_common import T0, all_safe_ensemble, destination, mk_crew, mk_job

from wgresponse import dispatch
from wgresponse.contracts import LatLon, ResponseConfig, SafeZone, seeded_rng

CENTER = LatLon(34.2000, -117.6000)
N_CYCLES = 8
STEP = 900.0


def base_layout():
    rng = seeded_rng("da_exp2", "layout")
    crews = []
    for k in range(2):
        crews.append((f"G{k+1}", destination(CENTER, 150.0 + 60.0 * k,
                                             rng.uniform(2500, 6000))))
    jobs = []
    for j in range(14):
        # 2 jobs close to crews (freeze-eligible first stops), rest spread out
        if j < 2:
            loc = destination(LatLon(*list(crews[j][1])), rng.uniform(0, 360),
                              rng.uniform(1500, 3500))
        else:
            loc = destination(CENTER, rng.uniform(0, 360), rng.uniform(3000, 14000))
        base_p10_h = rng.uniform(2.0, 10.0)
        spread_h = rng.uniform(0.5, 2.0)
        jobs.append(dict(cid=f"J{j+1:02d}", loc=loc,
                         value=rng.uniform(800_000, 3_500_000),
                         pri=2.0 if rng.random() < 0.25 else 1.0,
                         p10_s=T0 + base_p10_h * 3600.0, spread_s=spread_h * 3600.0))
    zones = [SafeZone("Z1", destination(CENTER, 270.0, 25_000.0), "west"),
             SafeZone("Z2", destination(CENTER, 0.0, 25_000.0), "north")]
    return crews, jobs, zones


def jobs_at_cycle(job_specs, k):
    """ETA wobble: seeded random walk +/-0.5 h per cycle per job, plus a
    forecast acceleration at cycle 4 (all arrivals pulled 1.0 h earlier)."""
    out = []
    for spec in job_specs:
        walk = 0.0
        for step in range(k + 1):
            r = seeded_rng("da_exp2.wobble", spec["cid"], step)
            walk += (r.random() * 2.0 - 1.0) * 0.5 * 3600.0
        if k >= 4:
            walk -= 1.0 * 3600.0
        p10 = spec["p10_s"] + walk
        out.append(mk_job(spec["cid"], spec["loc"], spec["value"], spec["pri"],
                          "I1", p10, p10 + spec["spread_s"]))
    return out


def route_map(plan):
    m = {}
    for cid, cp in plan.plans.items():
        for pos, st in enumerate(cp.stops):
            m[(st.client_id, st.incident_id)] = (cid, pos, st.service_start_s)
    return m


def run_config(tag, churn_frac, adopt_frac):
    cfg = ResponseConfig()
    cfg.churn_penalty_frac = churn_frac
    cfg.adoption_threshold_frac = adopt_frac
    cfg.solver_budget_s = 3.0
    crews_spec, job_specs, zones = base_layout()
    ens = {"I1": all_safe_ensemble("I1")}
    incumbent = None
    prev_map = None
    totals = dict(reassigned=0, reordered=0, frozen_viol=0, drops=0, adds=0)
    tags = []
    obj_sum = 0.0
    served_sum = 0
    print(f"\n=== config {tag}: churn_penalty={churn_frac} adoption_threshold={adopt_frac} ===")
    print(f"{'cyc':>3} {'served':>6} {'cand':>5} {'reassign':>8} {'reorder':>7} "
          f"{'drop':>4} {'add':>3} {'frozviol':>8}")
    for k in range(N_CYCLES):
        now = T0 + k * STEP
        crews = [mk_crew(cid, loc, now) for cid, loc in crews_spec]
        jobs = jobs_at_cycle(job_specs, k)
        plan = dispatch.plan(crews, jobs, ens, zones, cfg, now,
                             incumbent=incumbent)
        cur = route_map(plan)
        rs = ro = dr = ad = fv = 0
        if prev_map is None:
            reassign_keys = {}
        if prev_map is not None:
            frozen_keys = {key for key, (cid, pos, ss) in prev_map.items()
                           if ss < now + cfg.commitment_horizon_min * 60.0}
            for key, (cid, pos, ss) in cur.items():
                if key in prev_map:
                    pcid, ppos, _ = prev_map[key]
                    if pcid != cid:
                        rs += 1
                        reassign_keys[key] = reassign_keys.get(key, 0) + 1
                        if key in frozen_keys:
                            fv += 1
                    elif ppos != pos:
                        ro += 1
                else:
                    ad += 1
            for key in prev_map:
                if key not in cur:
                    dr += 1
                    if key in frozen_keys:
                        fv += 1
        totals["reassigned"] += rs
        totals["reordered"] += ro
        totals["drops"] += dr
        totals["adds"] += ad
        totals["frozen_viol"] += fv
        tags.append(plan.objective_breakdown["candidate"])
        obj_sum += plan.objective_breakdown["value_usd"]
        served_sum += plan.objective_breakdown["served"]
        print(f"{k:>3} {plan.objective_breakdown['served']:>6} "
              f"{plan.objective_breakdown['candidate']:>5} {rs:>8} {ro:>7} "
              f"{dr:>4} {ad:>3} {fv:>8}")
        prev_map = cur
        incumbent = plan
    n_tr = N_CYCLES - 1
    print(f"totals over {n_tr} transitions: reassigned={totals['reassigned']} "
          f"reordered={totals['reordered']} drops={totals['drops']} "
          f"adds={totals['adds']} frozen_violations={totals['frozen_viol']}")
    print(f"candidate tags: {tags}")
    pingpong = {k: v for k, v in reassign_keys.items() if v > 1}
    print(f"reassigned keys: { {k[0]: v for k, v in sorted(reassign_keys.items())} }"
          f"  ping-pong (>1 move): { {k[0]: v for k, v in sorted(pingpong.items())} }")
    print(f"mean planned value/cycle: ${obj_sum / N_CYCLES:,.0f}  "
          f"mean served/cycle: {served_sum / N_CYCLES:.2f}")
    totals["mean_value"] = obj_sum / N_CYCLES
    totals["mean_served"] = served_sum / N_CYCLES
    return totals


if __name__ == "__main__":
    a = run_config("A (defaults)", 0.05, 0.03)
    b = run_config("B (anti-thrash OFF)", 0.0, 0.0)
    print("\n=== A vs B ===")
    for k in ("reassigned", "reordered", "drops", "adds", "frozen_viol"):
        if k in a:
            print(f"  {k}: A={a[k]}  B={b[k]}")
