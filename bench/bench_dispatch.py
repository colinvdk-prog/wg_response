"""bench_dispatch — dispatch-solver benchmark (wgresponse.dispatch.plan).

Synthetic FEASIBLE deadline-TOPTW instance, deterministic:
  * one incident, planar P10 arrival field sweeping east at 0.4 m/s across a
    220x220 @ 200 m grid (44 km): arrival(col c) = t0 + c*200/0.4, so clients
    in cols 130..205 have pessimistic arrivals 18..28.5 h out — deadlines are
    real but generous, every job is solo-feasible for every crew.
  * M clients seeded uniformly in that band (value 0.6..3.3 M USD, 25 %
    priority-weight 2.0), N crews staged on the east edge (AVAILABLE), 3 safe
    zones on the far east edge; corridor/egress checks run for real against
    the eroded raster.

Per scale (default 5/50, 10/100, 20/200), run in a fresh child process with a
hard timeout so a budget blowout cannot hang the harness:
  cold  dispatch.plan (no incumbent)  — wall vs cfg.solver_budget_s
  warm  dispatch.plan (incumbent = cold plan) — the every-900 s re-solve path
  greedy earliest-deadline-first baseline (wgresponse.sim._fallback_plan)
Quality = served stops + collected value  sum(w * value * gel_frac *
gel_effectiveness), recomputed identically from each returned DispatchPlan.

Usage:  py -3 bench/bench_dispatch.py [--scales 5:50,10:100,20:200]
        [--budget-s 5.0] [--timeout-s 600] [--profile 10:100] [--in-process]
        [--compare]

--compare (Sprint 2 B2 exit-gate 5) appends cold/greedy + warm/greedy value
ratios and a PASS/FAIL guardrail verdict per scale (PASS iff served value >=
greedy on both solves), plus the winning seed/guardrail tags.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import pstats
import subprocess
import sys
import time

from _benchlib import REPO_ROOT, env_banner, fmt_s, table

from wgresponse import dispatch, geo
from wgresponse.contracts import (
    Crew,
    CrewStatus,
    ClientProperty,
    DispatchJob,
    EnsembleArrival,
    GridSpec,
    Raster,
    ResponseConfig,
    SafeZone,
    ThreatEta,
    seeded_rng,
)
from wgresponse import safety
from wgresponse.sim import _fallback_plan

SEED = "bench-dispatch-v1"
T0_S = 1_780_000_000.0
INCIDENT = "bench-fire"
NROWS = 220
NCOLS = 220
CELL_M = 200.0
FRONT_MPS = 0.4                     # planar fire front ground speed, eastward
CLIENT_COL_LO, CLIENT_COL_HI = 130, 205
CLIENT_ROW_LO, CLIENT_ROW_HI = 15, 205


def make_spec() -> GridSpec:
    return GridSpec(north_lat=34.6, west_lon=-118.6, cell_m=CELL_M,
                    nrows=NROWS, ncols=NCOLS)


def planar_raster(spec: GridSpec, offset_s: float = 0.0) -> Raster:
    """Arrival raster: fire hits column c at t0 + offset + c*CELL_M/FRONT_MPS."""
    data = [0.0] * (spec.nrows * spec.ncols)
    for c in range(spec.ncols):
        t = T0_S + offset_s + c * CELL_M / FRONT_MPS
        for r in range(spec.nrows):
            data[r * spec.ncols + c] = t
    return Raster(spec, data)


def arrival_at_col(c: float, offset_s: float = 0.0) -> float:
    return T0_S + offset_s + c * CELL_M / FRONT_MPS


def build_instance(n_crews: int, m_jobs: int, cfg: ResponseConfig):
    """(crews, jobs, ens, safe_zones) — deterministic in (SEED, n, m)."""
    spec = make_spec()
    ens = {INCIDENT: EnsembleArrival(
        spec=spec, members=[],
        p10=planar_raster(spec, 0.0),
        p50=planar_raster(spec, 2.0 * 3600.0),
        p90=planar_raster(spec, 4.0 * 3600.0),
        t0_s=T0_S, incident_id=INCIDENT)}

    rng = seeded_rng(SEED, "clients", n_crews, m_jobs)
    jobs = []
    for i in range(m_jobs):
        r = rng.randrange(CLIENT_ROW_LO, CLIENT_ROW_HI)
        c = rng.randrange(CLIENT_COL_LO, CLIENT_COL_HI)
        loc = geo.rc_to_latlon(spec, r, c)
        client = ClientProperty(
            client_id=f"J{i + 1:03d}", loc=loc, name=f"Bench Estate {i + 1}",
            value_usd=600_000.0 + rng.random() * 2_700_000.0,
            priority_weight=2.0 if rng.random() < 0.25 else 1.0)
        eta = ThreatEta(eta_p10_s=arrival_at_col(c + 0.5),
                        eta_p50_s=arrival_at_col(c + 0.5, 2.0 * 3600.0),
                        eta_p90_s=arrival_at_col(c + 0.5, 4.0 * 3600.0))
        jobs.append(DispatchJob(client=client, incident_id=INCIDENT, eta=eta))

    crews = []
    for k in range(n_crews):
        r = int(10 + (NROWS - 20) * (k + 0.5) / n_crews)
        crews.append(Crew(crew_id=f"G{k + 1:02d}", name=f"GraphGel-{k + 1}",
                          status=CrewStatus.AVAILABLE,
                          home_base=geo.rc_to_latlon(spec, r, 210)))

    zones = [SafeZone(f"Z{z}", geo.rc_to_latlon(spec, rr, 216), f"East zone {z}")
             for z, rr in (("A", 30), ("B", 110), ("C", 190))]
    return crews, jobs, ens, zones


def plan_value(plan, jobs, cfg) -> "tuple[int, float]":
    """(#served stops, collected value USD) recomputed uniformly from a plan."""
    by_id = {j.client.client_id: j.client for j in jobs}
    served = 0
    value = 0.0
    for cp in plan.plans.values():
        for st in cp.stops:
            cl = by_id[st.client_id]
            served += 1
            value += (cl.priority_weight * cl.value_usd
                      * st.gel_fresh_frac * cfg.gel_effectiveness)
    return served, value


def set_front_mps(v: float) -> None:
    """Front speed knob: higher = tighter deadlines = capacity pressure."""
    global FRONT_MPS
    FRONT_MPS = v


def run_case(n_crews: int, m_jobs: int, budget_s: float) -> dict:
    cfg = ResponseConfig()
    cfg.solver_budget_s = budget_s

    # cold solve (fresh jobs — dispatch mutates derived window fields)
    crews, jobs, ens, zones = build_instance(n_crews, m_jobs, cfg)
    t0 = time.perf_counter()
    cold = dispatch.plan(crews, jobs, ens, zones, cfg, T0_S)
    cold_wall = time.perf_counter() - t0
    cold_served, cold_value = plan_value(cold, jobs, cfg)

    # warm re-solve 900 s later with the cold plan as incumbent
    crews2, jobs2, ens2, zones2 = build_instance(n_crews, m_jobs, cfg)
    t0 = time.perf_counter()
    warm = dispatch.plan(crews2, jobs2, ens2, zones2, cfg, T0_S + 900.0,
                         incumbent=cold)
    warm_wall = time.perf_counter() - t0
    warm_served, warm_value = plan_value(warm, jobs2, cfg)

    # greedy EDF baseline (sim fallback); needs windows pre-derived
    crews3, jobs3, ens3, zones3 = build_instance(n_crews, m_jobs, cfg)
    for j in jobs3:
        safety.derive_job_windows(j, cfg)
    t0 = time.perf_counter()
    greedy = _fallback_plan(crews3, jobs3, ens3, zones3, cfg, T0_S)
    greedy_wall = time.perf_counter() - t0
    greedy_served, greedy_value = plan_value(greedy, jobs3, cfg)

    return {
        "n": n_crews, "m": m_jobs, "budget_s": budget_s,
        "cold_wall_s": cold_wall,
        "cold_solver_s": cold.objective_breakdown.get("solver_s"),
        "cold_candidate": cold.objective_breakdown.get("candidate"),
        "cold_seed": cold.objective_breakdown.get("seed"),
        "cold_guardrail": cold.objective_breakdown.get("guardrail"),
        "cold_served": cold_served, "cold_unserved": len(cold.unserved),
        "cold_value": cold_value,
        "warm_wall_s": warm_wall, "warm_served": warm_served,
        "warm_value": warm_value,
        "warm_candidate": warm.objective_breakdown.get("candidate"),
        "warm_seed": warm.objective_breakdown.get("seed"),
        "warm_guardrail": warm.objective_breakdown.get("guardrail"),
        "greedy_wall_s": greedy_wall, "greedy_served": greedy_served,
        "greedy_value": greedy_value,
    }


def run_profile(n_crews: int, m_jobs: int, budget_s: float, top: int = 14):
    cfg = ResponseConfig()
    cfg.solver_budget_s = budget_s
    crews, jobs, ens, zones = build_instance(n_crews, m_jobs, cfg)
    pr = cProfile.Profile()
    pr.enable()
    dispatch.plan(crews, jobs, ens, zones, cfg, T0_S)
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    print(buf.getvalue())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scales", default="5:50,10:100,20:200")
    ap.add_argument("--budget-s", type=float, default=5.0)
    ap.add_argument("--timeout-s", type=float, default=600.0)
    ap.add_argument("--profile", default="", metavar="N:M",
                    help="cProfile one plan() at this scale and exit")
    ap.add_argument("--in-process", action="store_true",
                    help="run cases in-process (no child timeout protection)")
    ap.add_argument("--compare", action="store_true",
                    help="append value ratios vs greedy + guardrail verdict "
                         "(Sprint 2 B2 exit-gate 5: value >= greedy everywhere)")
    ap.add_argument("--front-mps", type=float, default=FRONT_MPS,
                    help="fire-front speed; higher = tighter deadlines")
    ap.add_argument("--case", default="", help=argparse.SUPPRESS)
    args = ap.parse_args()
    set_front_mps(args.front_mps)

    if args.case:                     # child mode: one case, JSON on stdout
        n, m = (int(x) for x in args.case.split(":"))
        print(json.dumps(run_case(n, m, args.budget_s)))
        return

    if args.profile:
        n, m = (int(x) for x in args.profile.split(":"))
        print(f"bench_dispatch cProfile @ N={n} crews / M={m} jobs "
              f"(budget {args.budget_s} s, top cumulative)")
        run_profile(n, m, args.budget_s)
        return

    print("bench_dispatch |", env_banner())
    print(f"instance: planar front {FRONT_MPS} m/s E over {NROWS}x{NCOLS}@"
          f"{CELL_M:.0f} m; clients 18-28.5 h from P10 arrival; "
          f"budget={args.budget_s} s; seed={SEED!r}")
    heads = ["N/M", "cold wall_s", "warm wall_s", "greedy s",
             "served (cold/warm/greedy)", "value $M (cold/warm/greedy)",
             "budget?"]
    widths = [8, 12, 12, 9, 26, 28, 14]
    if args.compare:
        heads += ["cold/greedy", "warm/greedy", "guardrail"]
        widths += [12, 12, 10]
    t = table(heads, widths)
    t.print_header()
    for token in args.scales.split(","):
        n, m = (int(x) for x in token.split(":"))
        if args.in_process:
            d = run_case(n, m, args.budget_s)
        else:
            cmd = [sys.executable, str(REPO_ROOT / "bench" / "bench_dispatch.py"),
                   "--case", f"{n}:{m}", "--budget-s", str(args.budget_s),
                   "--front-mps", str(args.front_mps)]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     cwd=str(REPO_ROOT), timeout=args.timeout_s)
            except subprocess.TimeoutExpired:
                t.print_row([f"{n}/{m}", f">DNF({args.timeout_s:.0f}s)", "-", "-",
                             "-", "-", "TIMEOUT"])
                continue
            line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                t.print_row([f"{n}/{m}", "child-error", "-", "-", "-", "-", "-"])
                print(out.stdout, out.stderr)
                continue
        over = d["cold_wall_s"] / args.budget_s
        row = [
            f"{n}/{m}",
            fmt_s(d["cold_wall_s"]), fmt_s(d["warm_wall_s"]),
            fmt_s(d["greedy_wall_s"]),
            f"{d['cold_served']}/{d['warm_served']}/{d['greedy_served']} of {m}",
            f"{d['cold_value'] / 1e6:.1f}/{d['warm_value'] / 1e6:.1f}/"
            f"{d['greedy_value'] / 1e6:.1f}",
            "OK" if over <= 1.2 else f"{over:.1f}x OVER",
        ]
        if args.compare:
            gv = d["greedy_value"]
            rc = d["cold_value"] / gv if gv else float("inf")
            rw = d["warm_value"] / gv if gv else float("inf")
            ok = (d["cold_value"] >= gv - 1e-6 and d["warm_value"] >= gv - 1e-6)
            row += [f"{rc:.3f}x", f"{rw:.3f}x", "PASS" if ok else "FAIL"]
        t.print_row(row)
        if args.compare:
            print(f"    cold: seed={d.get('cold_seed')} cand={d.get('cold_candidate')} "
                  f"guardrail={d.get('cold_guardrail') or '-'} | "
                  f"warm: seed={d.get('warm_seed')} cand={d.get('warm_candidate')} "
                  f"guardrail={d.get('warm_guardrail') or '-'}")
    print("\nvalue = sum(weight * value_usd * gel_fresh_frac * gel_effectiveness) "
          "over served stops; cold/warm = dispatch.plan without/with incumbent; "
          "greedy = sim EDF fallback.")


if __name__ == "__main__":
    main()
