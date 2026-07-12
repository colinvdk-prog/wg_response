"""dr_exp1 — HOW THE OBJECTIVE TRADES ONE $4M HOME AGAINST TWO $1.5M HOMES.

Geometry (one crew, all corridors safe, exact micro-solver regime):
    crew C1 at O; safe zone 3 km W of O
    H1  $4.0M   6 km N of O
    H2  $1.5M   6 km S of O
    H3  $1.5M   600 m S of H2
    all p10 = T0 + 125 min, p50 = p10 + 3 h

Time math at the tier-0 effective straight-line speed 17.9/(1.35*1.4)=9.47 m/s:
one 6 km leg = 10.6 min; serving the pair back-to-back departs H3 at ~72 min
(+45 min of egress-buffer+slack still fits 125 min); ANY third stop blows the
deadline chain. So the reachable outcomes are exactly {H1} (or any single) vs
{H2,H3}: 1 home @ $4M vs 2 homes @ $3M.

Run A: values as stated  -> the J = priority*value*gel_effectiveness objective
Run B: all values forced equal ($1M) -> what a count-first objective would do
Run C: same as A but priority_weight raised on the pair (the config-side
       workaround available TODAY without touching the objective).

Also checks: contracts.ResponseConfig HAS dispatch_objective='homes' (founder
triage law, contracts simui block law 4) but wgresponse/dispatch.py never
reads it — the law is declared, not yet implemented in the planner.
"""
from __future__ import annotations

import inspect

from dr_common import (T0, all_safe_ensemble, describe_plan, destination,
                       mk_crew, mk_job)

from wgresponse import dispatch
from wgresponse.contracts import LatLon, ResponseConfig, SafeZone


def run(tag, values, priorities):
    cfg = ResponseConfig()
    o = LatLon(39.0, -121.0)
    h1 = destination(o, 0.0, 6_000.0)       # N
    h2 = destination(o, 180.0, 6_000.0)     # S
    h3 = destination(h2, 180.0, 600.0)      # 600 m past H2
    p10 = T0 + 125.0 * 60.0
    p50 = p10 + 3.0 * 3600.0
    jobs = [
        mk_job("H1", h1, values[0], priorities[0], "I-SAFE", p10, p50),
        mk_job("H2", h2, values[1], priorities[1], "I-SAFE", p10, p50),
        mk_job("H3", h3, values[2], priorities[2], "I-SAFE", p10, p50),
    ]
    crews = [mk_crew("C1", o)]
    zones = [SafeZone("Z1", destination(o, 270.0, 3_000.0), "west staging")]
    ens = {"I-SAFE": all_safe_ensemble()}
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, T0)
    served = sorted(s.client_id for cp in plan.plans.values() for s in cp.stops)
    print(f"\n=== {tag} ===")
    print(f"  values: H1=${values[0]/1e6:.1f}M H2=${values[1]/1e6:.1f}M "
          f"H3=${values[2]/1e6:.1f}M   priorities: {priorities}")
    print(describe_plan(plan, jobs))
    print(f"  -> homes saved: {len(served)}  ({', '.join(served) or 'none'})")
    return served


def main():
    a = run("A: as-priced (the shipping objective)",
            [4_000_000.0, 1_500_000.0, 1_500_000.0], [1.0, 1.0, 1.0])
    b = run("B: values forced equal — count-first surrogate",
            [1_000_000.0, 1_000_000.0, 1_000_000.0], [1.0, 1.0, 1.0])
    c = run("C: as-priced, pair priority_weight=3.0 (config workaround)",
            [4_000_000.0, 1_500_000.0, 1_500_000.0], [1.0, 3.0, 3.0])

    src = inspect.getsource(dispatch)
    wired = "dispatch_objective" in src
    print("\n=== founder triage law wiring check ===")
    print(f"  contracts.ResponseConfig.dispatch_objective default: "
          f"{ResponseConfig().dispatch_objective!r}")
    print(f"  dispatch.py reads cfg.dispatch_objective: {wired}")
    assert a == ["H1"], a
    assert b == ["H2", "H3"], b
    assert not wired, "update this experiment: the law landed in the planner"
    print("\nCONCLUSION: the $-weighted objective picks ONE $4M home over TWO"
          "\n$1.5M homes; with equal values (count mode) it picks the pair."
          "\ncfg.dispatch_objective='homes' exists in contracts (default!) but"
          "\nthe planner never reads it — value mode is what actually ships.")


if __name__ == "__main__":
    main()
