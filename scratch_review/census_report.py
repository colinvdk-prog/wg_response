"""Simulation census (2026-08-01): inventories every recorded simulation
artifact under scratch_review/ (plus the two named hindcast-replay files),
recorded data only -- this script runs NOTHING, it only reads existing JSON.

A "simulation RUN" = one scenario simulation = one row (one SimSession
ignited and ticked to horizon, or a documented off_tile/error attempt that
still consumed a scenario slot). "Home-instances" = homes/clients scored
across those rows (threatened + not_threatened, i.e. every client placed in
the session, not just the ones the fire reached).

Corpora counted as simulation RUNS (three eras, per the task spec):
  bench sweep era      scratch_review/bench_*.json      (bench_dispatch.py)
  ember optimization era  scratch_review/opt_ember_*.json  (opt_ember.py and
                       its pi/rad variant CLIs), EXCLUDING opt_ember_smoke_*
  probes               scratch_review/probe_*.json files whose JSON is
                       row/scenario-shaped (a list, or a dict with a "rows"
                       list, where every entry carries a per-scenario
                       "homes" list or "outcomes" dict) -- covers the three
                       families named in the task spec (probe_impacted,
                       probe_leadtime_*, probe_founder_arith) plus any other
                       probe_*.json matching the same shape (probe_fa2_
                       deploy30.json, structurally identical to
                       probe_founder_arith.json).

Listed but NOT counted as simulation runs -- analysis/diagnostic artifacts,
per the task spec ("defens_*.json, opt_ember smoke files, caught_/hazard_
artifacts") plus files this script found that do not fit any row/scenario
shape (probe_fa2_altroute/corridor/erosion.json are single diagnostic dumps
with no per-scenario array) and three unprefixed loose JSON files
(extreme_signature/feed_candidates/gust_sweep) not covered by any of the
four globs in the task spec:
  scratch_review/defens_*.json
  scratch_review/opt_ember_smoke_*.json
  scratch_review/caught_*.json
  scratch_review/hazard_*_out.txt
  scratch_review/probe_fa2_{altroute,corridor,erosion}.json
  scratch_review/{extreme_signature,feed_candidates,gust_sweep}.json

Counted separately as OBSERVATION-COMPARISONS -- hindcast replay windows
scored against REAL historical fire perimeters, not simulated scenarios
(only the two files the task spec names; sibling replay_b*.json variants
under scratch_review/hindcast_oos_data/ exist but are out of the requested
scope and are only noted, not summed in):
  tools/hindcast_data/replay_results.json
  scratch_review/hindcast_oos_data/replay_b0.5.json

Usage: py -3 scratch_review/census_report.py
Writes scratch_review/census_report.json; prints a summary to stdout.
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))          # .../scratch_review
ROOT = os.path.dirname(HERE)                                # .../wg_response
BENCH_DEFAULT_CLIENTS = 40   # bench_dispatch.N_CLIENTS default (fallback only;
                              # every recorded bench_*.json dict file already
                              # carries explicit threatened/not_threatened
                              # sums, so this fallback is never actually hit
                              # against the current corpus -- kept for any
                              # future bench_*.json missing those fields).


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- bench ---
def census_bench():
    files = sorted(glob.glob(os.path.join(HERE, "bench_*.json")))
    detail = []
    for p in files:
        d = load(p)
        if isinstance(d, list):
            # bench_interface.json / bench_alerted.json style: top-level IS
            # the rows array; each row's own "threatened" field is the only
            # recorded home-count for that row (no not_threatened sibling
            # exists in this shape).
            rows = d
            n_scen = len(rows)
            homes = sum(int(r.get("threatened", 0) or 0)
                       for r in rows if isinstance(r, dict))
            shape = "list-of-rows"
        else:
            rows = d.get("rows", [])
            n_scen = d.get("n_scenarios")
            if n_scen is None:
                n_scen = len(rows)
            has_tnt = bool(rows) and all(
                isinstance(r, dict) and "threatened" in r
                and "not_threatened" in r for r in rows)
            if has_tnt:
                homes = sum(int(r.get("threatened", 0) or 0)
                           + int(r.get("not_threatened", 0) or 0)
                           for r in rows)
            else:
                homes = BENCH_DEFAULT_CLIENTS * n_scen
            shape = "dict.rows" if has_tnt else "dict.rows(fallback-clients*scenarios)"
        detail.append(dict(file=rel(p), shape=shape, scenarios=n_scen,
                           homes=homes, full_suite=(n_scen == 60)))
    return dict(
        corpus="bench sweep era",
        n_files=len(files),
        runs=sum(r["scenarios"] for r in detail),
        homes=sum(r["homes"] for r in detail),
        full_suite_files=sum(1 for r in detail if r["full_suite"]),
        detail=detail,
    )


# ------------------------------------------------------------ opt_ember ---
def census_opt_ember():
    files = sorted(glob.glob(os.path.join(HERE, "opt_ember_*.json")))
    detail = []
    smoke = []
    for p in files:
        base = os.path.basename(p)
        d = load(p)
        rows = d.get("rows", []) if isinstance(d, dict) else []
        n_scen = len(rows)
        homes = sum(len(r.get("homes", [])) for r in rows if isinstance(r, dict))
        row = dict(file=rel(p), tag=d.get("tag"), suite=d.get("suite"),
                  scenarios=n_scen, homes=homes, full_suite=(n_scen == 60))
        if base.startswith("opt_ember_smoke_"):
            smoke.append(row)
        else:
            detail.append(row)
    return dict(
        corpus="ember optimization era",
        n_files=len(detail),
        runs=sum(r["scenarios"] for r in detail),
        homes=sum(r["homes"] for r in detail),
        full_suite_files=sum(1 for r in detail if r["full_suite"]),
        by_suite={s: sum(1 for r in detail if r["suite"] == s)
                 for s in sorted({r["suite"] for r in detail})},
        detail=detail,
    ), smoke


# ---------------------------------------------------------------- probe ---
def _entry_homes(e):
    """Per-scenario-entry home count, however this probe family recorded
    it: an explicit "homes" list (probe_impacted/probe_leadtime_*), or an
    "outcomes" dict keyed by client id (probe_founder_arith/probe_fa2_
    deploy30 -- fixed small hand-built scenarios with no "homes" list).
    Returns None if this entry carries neither (i.e. not scenario-shaped)."""
    if not isinstance(e, dict):
        return None
    if isinstance(e.get("homes"), list):
        return len(e["homes"])
    if isinstance(e.get("outcomes"), dict):
        return len(e["outcomes"])
    if e.get("error"):
        return 0   # off_tile/etc: still a scenario attempt (1 row), 0 homes
    return None


def _classify_probe(d):
    """Returns (scenarios, homes) if `d` is row/scenario-shaped (a list of
    scenario entries, or a dict with a "rows" list of them), else None."""
    entries = d["rows"] if isinstance(d, dict) and isinstance(d.get("rows"), list) \
        else d if isinstance(d, list) else None
    if not entries:
        return None
    hc = [_entry_homes(e) for e in entries]
    if any(h is None for h in hc):
        return None
    return len(entries), sum(hc)


def census_probes():
    files = sorted(glob.glob(os.path.join(HERE, "probe_*.json")))
    detail = []
    non_row = []
    for p in files:
        d = load(p)
        got = _classify_probe(d)
        if got is None:
            non_row.append(dict(file=rel(p), top_level_keys=(
                sorted(d.keys()) if isinstance(d, dict) else None),
                note="no per-scenario rows/homes -- single diagnostic dump"))
            continue
        n_scen, homes = got
        tag = d.get("tag") if isinstance(d, dict) else None
        suite = d.get("suite") if isinstance(d, dict) else None
        detail.append(dict(file=rel(p), tag=tag, suite=suite,
                           scenarios=n_scen, homes=homes,
                           full_suite=(n_scen == 60)))
    return dict(
        corpus="probes",
        n_files=len(detail),
        runs=sum(r["scenarios"] for r in detail),
        homes=sum(r["homes"] for r in detail),
        full_suite_files=sum(1 for r in detail if r["full_suite"]),
        detail=detail,
    ), non_row


# ------------------------------------------------------- artifacts-only ---
def census_defens():
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "defens_*.json"))):
        d = load(p)
        out.append(dict(file=rel(p), bytes=os.path.getsize(p),
                        location=d.get("location"), name=d.get("name"),
                        n_sites=d.get("n_sites"), scenarios=d.get("scenarios"),
                        pct_allow=d.get("pct_allow")))
    return out


def census_caught():
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "caught_*.json"))):
        d = load(p)
        sub = {k: (v.get("n_crews") if isinstance(v, dict) else None)
              for k, v in d.items()} if isinstance(d, dict) else {}
        out.append(dict(file=rel(p), bytes=os.path.getsize(p),
                        sub_runs=sub))
    return out


def census_hazard_txt():
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "hazard_*_out.txt"))):
        out.append(dict(file=rel(p), bytes=os.path.getsize(p)))
    return out


def census_other_loose_json():
    named = set()
    for pat in ("bench_*.json", "opt_ember_*.json", "probe_*.json",
               "defens_*.json", "caught_*.json"):
        named |= {os.path.basename(x) for x in glob.glob(os.path.join(HERE, pat))}
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "*.json"))):
        base = os.path.basename(p)
        if base in named or base.startswith("census_"):
            continue
        d = load(p)
        shape = f"list len={len(d)}" if isinstance(d, list) else f"dict keys={sorted(d.keys())}"
        out.append(dict(file=rel(p), bytes=os.path.getsize(p), shape=shape))
    return out


# ------------------------------------------------------------ hindcast ---
def census_hindcast():
    def one(p):
        d = load(p)
        pairs = d.get("pairs", [])
        cells = sum(int(pr.get("n_new", 0) or 0) for pr in pairs)
        return dict(file=rel(p), pairs=len(pairs), cells=cells,
                   skips=len(d.get("skips", [])))

    a = one(os.path.join(ROOT, "tools", "hindcast_data", "replay_results.json"))
    b = one(os.path.join(HERE, "hindcast_oos_data", "replay_b0.5.json"))
    siblings = []
    for p in sorted(glob.glob(os.path.join(HERE, "hindcast_oos_data", "replay_b*.json"))):
        base = os.path.basename(p)
        if base == "replay_b0.5.json":
            continue
        d = load(p)
        siblings.append(dict(file=rel(p), pairs=len(d.get("pairs", [])),
                             note="exists but not in the task's named scope; "
                                  "not summed into the counted total"))
    return dict(
        label="observation-comparisons (hindcast replay vs REAL historical "
              "fire perimeters) -- NOT simulations, counted separately from "
              "simulation_runs",
        files=dict(tools_hindcast_data_replay_results=a,
                  scratch_review_hindcast_oos_data_replay_b0_5=b),
        combined_pairs=a["pairs"] + b["pairs"],
        combined_cells=a["cells"] + b["cells"],
        other_replay_b_variants_not_counted=siblings,
    )


def main():
    bench = census_bench()
    opt_ember, opt_ember_smoke = census_opt_ember()
    probes, probe_non_row = census_probes()
    defens = census_defens()
    caught = census_caught()
    hazard_txt = census_hazard_txt()
    other_json = census_other_loose_json()
    hindcast = census_hindcast()

    total_runs = bench["runs"] + opt_ember["runs"] + probes["runs"]
    total_homes = bench["homes"] + opt_ember["homes"] + probes["homes"]
    full_suite = dict(bench=bench["full_suite_files"],
                      opt_ember=opt_ember["full_suite_files"],
                      probes=probes["full_suite_files"])
    full_suite["combined"] = sum(full_suite.values())

    report = dict(
        totals=dict(simulation_runs=total_runs, home_instances=total_homes,
                   full_suite_invocations=full_suite),
        corpora=dict(bench_sweep_era=bench, ember_optimization_era=opt_ember,
                    probes=probes),
        analysis_artifacts_not_counted=dict(
            defens=defens,
            opt_ember_smoke=opt_ember_smoke,
            caught=caught,
            hazard_text=hazard_txt,
            probe_non_row_diagnostics=probe_non_row,
            other_loose_json=other_json,
        ),
        hindcast_replay_observation_comparisons=hindcast,
    )

    out = os.path.join(HERE, "census_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    # ---------------------------------------------------------- print ---
    print("=" * 78)
    print("SIMULATION CENSUS -- scratch_review/  (recorded data only)")
    print("=" * 78)
    print(f"TOTAL simulation RUNS (1 run = 1 scenario = 1 row): {total_runs}")
    print(f"TOTAL home-instances simulated:                     {total_homes}")
    print(f"Full-suite (60-scenario) invocations: bench={full_suite['bench']} "
          f"opt_ember={full_suite['opt_ember']} probes={full_suite['probes']} "
          f"combined={full_suite['combined']}")
    print()
    for c in (bench, opt_ember, probes):
        print(f"-- {c['corpus']:<24} files={c['n_files']:<4} "
              f"runs={c['runs']:<6} homes={c['homes']:<7} "
              f"full_suite_files={c['full_suite_files']}")
    if opt_ember.get("by_suite"):
        print(f"     opt_ember by suite: {opt_ember['by_suite']}")
    print()
    print("Listed but NOT counted as simulation runs (analysis/diagnostic "
          "artifacts):")
    print(f"  defens_*.json               {len(defens)} files "
          f"{[d['file'] for d in defens]}")
    print(f"  opt_ember_smoke_*.json      {len(opt_ember_smoke)} files, "
          f"{sum(r['scenarios'] for r in opt_ember_smoke)} scenarios / "
          f"{sum(r['homes'] for r in opt_ember_smoke)} homes (excluded from "
          f"ember-optimization-era totals above) "
          f"{[d['file'] for d in opt_ember_smoke]}")
    print(f"  caught_*.json               {len(caught)} files "
          f"{[d['file'] for d in caught]}")
    print(f"  hazard_*_out.txt            {len(hazard_txt)} files "
          f"{[d['file'] for d in hazard_txt]}")
    print(f"  probe_*.json (non-row)      {len(probe_non_row)} files "
          f"{[d['file'] for d in probe_non_row]}")
    print(f"  other loose *.json          {len(other_json)} files "
          f"{[d['file'] for d in other_json]}")
    print()
    print("Hindcast REPLAY windows (observation-comparisons, NOT "
          "simulations):")
    hf = hindcast["files"]
    a = hf["tools_hindcast_data_replay_results"]
    b = hf["scratch_review_hindcast_oos_data_replay_b0_5"]
    print(f"  {a['file']:<52} pairs={a['pairs']:<4} cells={a['cells']:<6} skips={a['skips']}")
    print(f"  {b['file']:<52} pairs={b['pairs']:<4} cells={b['cells']:<6} skips={b['skips']}")
    print(f"  combined: pairs={hindcast['combined_pairs']} cells={hindcast['combined_cells']}")
    if hindcast["other_replay_b_variants_not_counted"]:
        print("  (sibling replay_b*.json variants exist but are out of the "
              "requested scope, not summed in: " +
              ", ".join(s["file"] for s in hindcast["other_replay_b_variants_not_counted"])
              + ")")
    print()
    print("wrote", rel(out))


if __name__ == "__main__":
    main()
