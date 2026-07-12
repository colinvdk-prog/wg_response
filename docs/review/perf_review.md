# wgresponse — Performance & Scaling Review (measured)

**Date:** 2026-07-09 · **Lane:** performance/scaling · **Verdict up front:**
the spread engine is comfortably inside the 900 s replan budget for realistic
incident loads (worst-case 512x512 full-burn ensemble ~67 s/incident,
~13 concurrent incidents/cycle single-threaded); the **dispatch solver is the
fire**: at the open-question scale N=20/M=200 the 5 s budget returns a plan
**9x worse in collected value than the 0.45 s greedy EDF fallback**, because
regret-2 construction only inserts 11 of 200 jobs before the clock runs out —
and 94% of its time is corridor-geometry recomputation that can be made O(1)
per check with a byte-identical refactor.

Everything below is measured, not estimated. Machine: Windows 11, AMD64,
24 logical CPUs, CPython 3.14.5. Harness (reusable, deterministic seeds, no
network):

```
py -3 bench/bench_spread.py            # spread scaling / ensemble / profile / memory
py -3 bench/bench_spread.py --ignition west-line --sizes 256,512   # full-coverage worst case
py -3 bench/bench_dispatch.py          # solver scaling vs greedy, child-proc timeouts
py -3 bench/bench_dispatch.py --profile 10:100
py -3 bench/bench_cycle.py             # ThreatMonitor.cycle phase breakdown
```

Product code untouched (453 tests unaffected); instrumentation is runtime-only
inside the bench process.

---

## 1. Spread engine — `spread.compute_arrival`

SyntheticTerrain **ridge** (mixed GR2/GS2/SH5/TL3 patches + central ridge so
slope/aspect are exercised), MockWeather 8 m/s sustained westerly, RH 25 %,
30 C, horizon 96 h, cell 90 m, stock `ResponseConfig`.

### 1.1 Single member, center point ignition

| grid | cells | settled | wall s | settled cells/s | edge relax/s |
|---|---|---|---|---|---|
| 128x128 | 16,384 | 6,580 | 0.22 | 29,616 | 473,862 |
| 256x256 | 65,536 | 18,607 | 0.65 | 28,777 | 460,434 |
| 512x512 | 262,144 | 39,236 | 1.44 | 27,295 | 436,716 |
| 768x768 | 589,824 | 60,065 | 2.45 | 24,564 | 393,016 |

**The invariant is throughput per *settled* (burned) cell: ~24–30k cells/s
(~0.4–0.5M edge relaxations/s), mildly degrading with grid size.** Cost scales
with the fire footprint, not the grid area — the horizon bound already acts as
an effective early-termination (unreached cells never enter the heap).

### 1.2 Single member, full-coverage worst case (west-edge line ignition)

| grid | settled | wall s | settled cells/s |
|---|---|---|---|
| 256x256 | 65,536 (100 %) | 2.38 | 27,590 |
| 512x512 | 262,144 (100 %) | 11.2 | 23,507 |

This confirms the ARCHITECTURE.md §7 estimate (“5–15 s per member on 300k
cells”): **11.2 s measured at 262k cells all-burned.**

### 1.3 Ensemble (`ensemble_arrival`, ensemble_n=6)

| case | wall s | notes |
|---|---|---|
| 256x256, center ignition | 4.6 | ~0.77 s/member; per-cell quantile pass ~0.1 s (2 %) |
| 512x512, center ignition | 9.2 | (memory-probe child run) |
| 512x512 full-burn (derived: 6 x 11.2) | ~67 | worst case per incident |

### 1.4 Operational budget (replan_period_s = 900 s, single-threaded)

| grid / coverage | s per incident (6-member) | max concurrent incidents/cycle |
|---|---|---|
| 256x256 center-ignition | 4.6 | ~196 |
| 512x512 center-ignition | 8.6 | ~104 |
| 512x512 full burn | ~67 | ~13 |
| 768x768 full burn (derived) | ~150 | ~6 |

**Spread is NOT the near-term bottleneck.** Even the pathological case (every
cell of a 46x46 km window burning) leaves 13 concurrent incidents per cycle
on one core, before any parallelism.

### 1.5 Memory — 512x512 6-member ensemble (child process, psapi peak)

| metric | value |
|---|---|
| RSS before ensemble | 64.5 MB |
| RSS after / process peak | 101.9 MB |
| tracemalloc python-alloc peak | 31.0 MB |

**Memory is a non-issue** (~37 MB per 262k-cell ensemble incl. 6 member
rasters + 3 quantile rasters). Dozens of concurrent incidents fit in a small
container.

### 1.6 Hot spots — cProfile, 256x256 single member (2.056 s profiled)

| cum s | % | function |
|---|---|---|
| 1.571 | 76 % | `spread.cell_params` (once per settled cell) |
| 1.148 | 56 % | `rothermel._bed` — **called 2x per settled cell** (37,214 calls) |
| 0.672 | 33 % | `rothermel.effective_wind_mps` (slope virtual wind; bed-build #1) |
| 0.640 | 31 % | `rothermel.surface_ros_mps` (bed-build #2) |
| 0.609 | 30 % | `rothermel._category` (74,428 calls) + 574k `sum()` calls (0.52 s) |
| 0.178 | 9 % | `rothermel.directional_ros_mps` (149,892 calls — per edge) |
| 0.13 | 6 % | `heapq` push+pop **(bucket-queue candidate is dead: heap is only 6 %)** |

### 1.7 ROS cache accounting (measured via runtime wrappers, 256x256)

- `surface_ros_mps` calls = settled cells = 18,607 → **the existing
  per-(cell,hour) cache hit rate is 0.0 %**. Each cell settles exactly once in
  Dijkstra, so a cache keyed on `(cell_idx, hour)` can never hit. It is pure
  dict overhead today.
- Two fuel-bed builds per settled cell (`effective_wind_mps` + `surface_ros_mps`)
  — the bed depends only on `(fuel_model, m_dead)`, and `m_dead` varies only
  per hour-bucket (x member scale), **not per cell**. The correct memo key
  collapses 37,214 bed builds to ~(4 fuels x hours-touched) ≈ hundreds.
- Sharing potential of a `(fuel, slope, aspect, hour)` memo: on this synthetic
  ridge (continuous slope/aspect) 9 %; **quantized at 1 degree — which is what
  LANDFIRE SlpD/Asp actually deliver (integer degrees) — 83.2 % of all ROS
  evaluations are duplicates.** On production LFPS tiles an exact-key memo is
  byte-identical AND gets the 83 % hit rate.

---

## 2. Full cycle — `ThreatMonitor.cycle` (canyon_wind_event stack)

Scenario providers (deterministic mocks) + real siblings everywhere:
`spread.ensemble_arrival`, `alerts.AlertEngine`, `crews.CrewTracker`,
`dispatch.plan`. 12 clients, 3 crews, 1 incident, ensemble_n=6.

### A. As-built scenario grid (68x87 @ 250 m = 5,916 cells)

| cycle | total s | spread s | dispatch s | terrain s | alerts s | other s | stops |
|---|---|---|---|---|---|---|---|
| 0 | 2.28 | 0.83 | 1.43 | 0.00 | 0.00 | 0.03 | 12 |
| 1 | 1.59 | 0.82 | 0.76 | 0.00 | 0.00 | 0.00 | 12 |
| 2 | 1.34 | 0.85 | 0.48 | 0.00 | 0.00 | 0.00 | 12 |
| 3 | 1.95 | 0.91 | 1.03 | 0.00 | 0.00 | 0.00 | 12 |

Split over 4 cycles: **spread 48 %, dispatch 52 %, everything else < 1 %**
(feeds/weather/terrain/alerts/eta sampling are noise). Cycle uses ~0.2 % of
the 900 s budget at this toy scale.

### B. Same incident on the operational 90 m grid (~44k cells)

| cycle | total s | spread s | dispatch s | terrain s |
|---|---|---|---|---|
| 0 | 5.44 | 3.90 | 1.48 | 0.06 |
| 1 | 5.42 | 4.87 | 0.48 | 0.05 |

At production resolution **spread becomes 81 % of the cycle** — but 5.4 s
against a 900 s cadence is still 0.6 % utilization for one small incident.
Overhead outside spread+dispatch stays < 2 % in both configurations: **no
optimization work is warranted anywhere except spread and dispatch.**

---

## 3. Dispatch solver — `dispatch.plan` (T-3 answer)

Synthetic FEASIBLE deadline-TOPTW instance (`bench_dispatch.py`): planar P10
front sweeping east at 0.4 m/s over 220x220 @ 200 m; M clients with P10
arrivals 18–28.5 h out (generous but real deadlines — every job solo-feasible
for every crew); N crews staged east, AVAILABLE; 3 safe zones; corridor and
egress checks run for real against the eroded raster. Greedy baseline =
`sim._fallback_plan` (earliest-deadline-first, honors all safety rules).
Value = Σ weight·value·gel_fresh_frac·gel_effectiveness over served stops,
recomputed identically from each returned plan.

### 3.1 At the contract budget (solver_budget_s = 5.0)

| N/M | cold wall s | warm wall s | greedy s | served cold/warm/greedy | value $M cold/warm/greedy |
|---|---|---|---|---|---|
| 5/50 | 5.32 | 5.84 | 0.05 | 22 / 30 / **50** | 45.9 / 59.8 / **105.8** |
| 10/100 | 5.35 | 5.75 | 0.12 | 16 / 21 / **100** | 56.2 / 65.8 / **208.8** |
| 20/200 | 5.25 | 5.35* | 0.45 | 11 / 15 / **200** | 47.6 / 58.6 / **427.6** |

(*warm re-solve ~ same cost as cold — warm start does not rescue the scan.)

### 3.2 At 12x the budget (solver_budget_s = 60)

| N/M | cold wall s | served cold/warm/greedy | value $M cold/warm/greedy |
|---|---|---|---|
| 10/100 | 60.7 | 55 / 100 / 100 | 124.2 / 208.8 / 208.8 |
| 20/200 | 62.5 | 31 / 51 / **200** | 107.7 / 152.3 / **427.6** |

### 3.3 Findings (measured)

1. **The budget clock is respected (±7 %), but construction never finishes.**
   Regret-2 inserts ~1 job per full-pool scan; at 20/200 only 11 scans fit in
   5 s, and even 60 s completes only 31 of 200 insertions. The ILS/local-search
   phases never run at scale — all budget dies in construction.
2. **The 0.45 s greedy EDF fallback strictly dominates the 5 s solver at every
   tested scale** — 9x the collected value at 20/200 — on a fully feasible
   instance. Today the “fallback if the budget blows” (ARCHITECTURE §6) would
   be the better *primary* at scale.
3. **Root cause is corridor geometry, not optimization logic.** cProfile at
   10/100: 94 % of `plan()` is inside `safety.corridor_safe` (12,186 calls,
   6.47 s of 6.98 s), which itself is dominated by re-sampling the leg
   polyline every call — `geo.destination` 469,832 calls (2.48 s),
   `haversine_m` ~498k calls (1.35 s), raster sampling ~494k calls (1.57 s).
   The `_Ctx` corridor cache keys on `(a, b, depart_s)`; every candidate
   insertion shifts departure times, so it almost never hits during
   construction scans.

Top-5 cumulative (dispatch profile, 10/100): `_regret2_insert` 6.92 s →
`_best_insertions` 6.91 s → `eval_route` 6.86 s → `safety.corridor_safe`
6.47 s → `geo.sample_polyline`/`geo.destination` 2.82/2.48 s.

---

## 4. Optimization candidates (each: expected speedup / effort / risk / results-change)

### Dispatch lane

| # | Candidate | Expected speedup | Effort | Risk | Changes results? |
|---|---|---|---|---|---|
| D1 | **Closed-form corridor precompute.** `corridor_safe(a,b,depart)` is affine in `depart`: safe ⇔ `depart ≤ T*(a,b)` where `T* = min_i(eroded_arrival_i − margin − d_i/speed)`; `worst_margin_h(depart)` = `min_i(arrival_i − d_i/speed) − depart`. Sample each unique (a,b) leg ONCE per raster, store `T*` and the margin constant; every corridor/egress check becomes O(1) for any departure time. | **~10–30x on plan()** (kills the 94 % corridor share; leg geometry reuse across the whole solve) | M | Low — same samples, same math, factored | **No — byte-identical verdicts** |
| D2 | **Greedy-seed + never-worse guardrail.** Construct with the 0.45 s EDF greedy first, then spend the remaining budget on regret-2/LS/ILS improvement; adopt solver output only if it beats the greedy plan. | Converts the measured **9x value regression into a guaranteed ≥ greedy** | S | Low | Yes (better plans; determinism preserved) |
| D3 | **O(1) insertion feasibility (TOPTW wait/MaxShift recursion).** The docstring already claims it; measured behavior is full-route re-evaluation per candidate position (O(route) x positions x jobs x crews per scan). Maintain forward slack per stop; evaluate an insertion in O(1) and re-time only on commit. | ~5–10x on construction (multiplies with D1) | M/L | Medium — classic algorithm, needs careful tests | No (same accept/reject decisions) |
| D4 | **In-scan budget checks.** Deadline is only tested between whole-pool scans; add checks inside `_best_insertions` loops so a scan cannot overrun (measured +5–7 % today, unbounded at larger M). | Correctness of the budget, not speed | S | Low | Only under budget pressure (already non-deterministic there) |
| D5 | Incremental re-solve / warm-start only. | Measured: warm ≈ cold (5.75–6.18 s vs 5.25–5.35 s) — **does not rescue construction; not a fix by itself** | — | — | — |

### Spread lane

| # | Candidate | Expected speedup | Effort | Risk | Changes results? |
|---|---|---|---|---|---|
| S1 | **Fuel-bed memo keyed `(fuel_model, m_dead)`** (+ reuse the bed between `effective_wind_mps` and `surface_ros_mps` in `cell_params`). Bed inputs vary per hour/member, not per cell: 37,214 builds → hundreds. Bed building is 56 % of member time. | **~2x per member, all terrains** | S | Low — exact-value keys | **No — byte-identical** |
| S2 | **Exact ROS memo keyed `(fuel, slope, aspect, hour)`** replacing the dead `(cell, hour)` cache (measured 0.0 % hit rate — pure overhead today). On LANDFIRE tiles slope/aspect are integer degrees → measured 83.2 % duplicate evals. | **~2.5–3x per member on production tiles** (9 % on continuous synthetic slopes; combines with S1) | S | Low | **No** on any tile (exact keys); big wins on LFPS data |
| S3 | **Ensemble/incident multiprocessing.** Members are embarrassingly parallel (deterministic per-member seeds); 24 logical CPUs on this box. | **~4–5x ensemble wall** (6 members), ~Nx across incidents | M | Low-Medium (Windows spawn pickling of tile/wx/cfg — all plain dataclasses) | No (same member seeds, same quantiles) |
| S4 | **numpy hourly ROS pre-vectorization / numba or C inner loop** (estate precedent: the firerisk calculator perf review also landed on numba). Keep the pure-Python path as the byte-identity oracle per ARCHITECTURE §7. | 10x+ on the whole member | L | Medium — needs an oracle regression gate; optional dependency policy | Should be identical; must be gated by A/B fingerprint tests |
| S5 | Dial's algorithm / bucket queue for the Dijkstra heap. | **Dead on the data: heapq is 6 % of member time.** Ceiling < 1.06x. | — | — | — |
| S6 | Coarsen-then-refine two-pass grids. | Unneeded: budget already fits at 90 m (67 s worst case vs 900 s); coarsening changes arrival times (safety-relevant). | M | High (results change) | Yes — **do not** |
| S7 | Early termination beyond the horizon bound (e.g. stop once all client cells settle). | Already effectively in place — cost scales with settled cells (measured §1.1); further truncation would corrupt corridor rasters. | — | High | Yes — **do not** |

---

## 5. TOP 5 sprint candidates (ranked by speedup x confidence / effort)

1. **D1 — closed-form corridor precompute (`T*` per leg).** 10–30x on
   `dispatch.plan`, byte-identical, directly attacks the measured 94 % share.
   This alone probably lets regret-2 + ILS complete at 20/200 inside 5 s.
2. **D2 — greedy-seed + never-worse-than-greedy guardrail.** Small change,
   eliminates the production risk that the “smart” solver ships 9x less
   protected value than its own fallback while D1/D3 land.
3. **S1+S2 — rothermel bed memo + exact ROS memo (replace the dead
   `(cell,hour)` cache).** ~2.5–3x per member on LANDFIRE tiles, byte-identical,
   two small diffs in `spread.cell_params`/`rothermel`.
4. **S3 — multiprocess ensemble members (and incidents).** ~4–5x ensemble
   wall for an M-effort, results-identical change; pushes worst-case
   512x512 full-burn from ~67 s to ~15 s per incident.
5. **D3 — O(1) TOPTW insertion feasibility (MaxShift).** The right
   algorithmic fix for construction cost; do after D1 (they multiply) and
   gate with plan-equivalence tests.

Explicitly **rejected on the evidence**: bucket queue (S5 — heap is 6 %),
coarsen-then-refine (S6 — budget already fits; changes safety-relevant
arrivals), warm-start-as-fix (D5 — measured no rescue), any work on
feeds/alerts/terrain/eta overhead (< 2 % of cycle).

## 6. Re-run instructions / harness notes

- All benches print deterministic-seeded instances; wall-clock numbers vary
  with machine load (solver iteration counts under a wall-clock budget are
  inherently load-sensitive — contracts acknowledges this).
- `bench_dispatch.py` runs each scale in a child process with `--timeout-s`
  so a budget blowout cannot hang the harness; `--in-process` for debugging;
  `--front-mps` tightens deadlines (capacity pressure); `--budget-s` sweeps
  the contract budget.
- `bench_spread.py --mem-child N` is the memory-isolation entry point
  (psapi peak working set; `--tracemalloc` adds python-alloc peak at ~10x
  runtime cost).
- `bench_cycle.py` monkeypatches timing wrappers around module seams at
  runtime (restored in `finally`) — product files are never modified.
