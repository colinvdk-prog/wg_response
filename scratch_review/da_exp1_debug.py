"""EXP 1 debug — dissect the served-count misses (instances 11, 14).

Run: py -3 scratch_review/da_exp1_debug.py
"""
from __future__ import annotations

from da_common import T0, all_safe_ensemble
from da_exp1_optgap import build_instance, prune_and_lambda, brute_force_opt

from wgresponse import dispatch
from wgresponse.contracts import ResponseConfig

for inst in (11, 14):
    for ils_iters, budget in ((30, 3.0), (300, 30.0)):
        cfg = ResponseConfig()
        cfg.ils_iterations = ils_iters
        cfg.solver_budget_s = budget
        crews, jobs, zones = build_instance(inst)
        jobmap, lam = prune_and_lambda(crews, jobs, cfg)
        opt_j, opt_served = brute_force_opt(crews, jobmap, lam, cfg)
        crews2, jobs2, _ = build_instance(inst)
        plan = dispatch.plan(crews2, jobs2, {"I1": all_safe_ensemble("I1")},
                             zones, cfg, T0)
        bd = plan.objective_breakdown
        print(f"inst {inst} ils={ils_iters:>3}: opt J={opt_j:,.0f} served={opt_served} | "
              f"plan J={plan.objective:,.0f} served={bd['served']} "
              f"cand={bd['candidate']} solver_s={bd['solver_s']:.2f}")
        if ils_iters == 30:
            for cid, cp in sorted(plan.plans.items()):
                seq = [(s.client_id,
                        f"a+{(s.arrive_s-T0)/3600:.2f}h",
                        f"d+{(s.depart_s-T0)/3600:.2f}h",
                        f"m{s.margin_h:.2f}h") for s in cp.stops]
                print(f"    {cid}: {seq}")
            print(f"    unserved: {[(u.client_id, u.reason) for u in plan.unserved]}")
            for k in sorted(jobmap):
                j = jobmap[k]
                print(f"    {k[0]}: lss+{(j.latest_service_start_s-T0)/3600:.2f}h "
                      f"ld+{(j.latest_depart_s-T0)/3600:.2f}h "
                      f"score={j.client.priority_weight*j.client.value_usd*0.85:,.0f}")
