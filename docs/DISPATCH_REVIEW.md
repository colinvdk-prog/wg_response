# Dispatch Engine Review — travel time, time-to-complete-job, fire propagation

**Date:** 2026-07-10 · **Requested by:** founder (request #5) · **Engine state:** 1001 tests green
(verified before and after this review; product code untouched).
**Evidence:** three new seeded experiments `scratch_review/dr_exp1_value_vs_count.py`,
`dr_exp2_roads_vs_tier0.py`, `dr_exp3_plan_replayed_on_roads.py` (+ `dr_common.py`), run with
`py -3` from repo root, plus the Council-1 measured reviews (`docs/review/perf_review.md`,
`docs/review/dispatch_alerts_review.md`) and the Sprint-2 close-out (`docs/SPRINT2_PLAN.md`).

---

## 0. Verdict in five lines

1. The **solver is not the problem anymore.** After Sprint 2 it plans 20 crews / 200 jobs inside 5 s,
   provably never worse than its own greedy fallback, and hits the exact optimum on all 24 brute-forceable
   review instances (gap 0.000%). Small pools (≤ 8 jobs, ≤ 4 crews — launch scale) are solved *exactly*.
2. **The inputs are the problem.** The planner still prices every leg as a straight line
   (haversine × 1.35 ÷ blended 40 mph × 1.4), while real OSM road graphs for all five service
   areas already sit in the repo and the simulator drives crews on them. Measured on those graphs:
   the plan's travel times are optimistic on **76% of legs**, and in Paradise — Camp Fire geography —
   by a **mean factor of 2.43× (+20 min per leg, p90 4.5×)**. Measured road circuity is **1.96–2.43**,
   not the assumed 1.35.
3. Today's defenses (15-min slack floor per stop + re-planning from actual GPS positions every 15 min)
   absorb most of that error at current scale, but they spend safety margin to do it, and the planner's
   **egress-time picture can be ~40–64 min too rosy** on canyon stops (dr_exp3). Planning on the road
   network is the single highest-value upgrade.
4. The objective maximizes **dollars, not homes**: given one $4M home vs two $1.5M homes and time for
   only one choice, it protects the single $4M home (dr_exp1). The founder's triage law
   (`dispatch_objective='homes'`) is already declared — and defaulted — in contracts, but
   **the planner never reads it**. That config-vs-code drift should be closed this sprint.
5. Time-to-complete is a flat 30 min everywhere; fire coupling (P10 deadlines, eroded-P10 corridors,
   per-incident forecast gel windows) is sound and well-tested. The gel window itself remains the
   single most decision-relevant unverified input in the engine (supplier data still open).

---

## 1. How the current analysis works

One paragraph of orientation: every replan cycle (`cfg.replan_period_s` = 900 s, and every simulator
tick), the engine builds one `DispatchJob` per threatened, enrolled home, derives that home's safety
and usefulness windows from the fire forecast, and hands crews, jobs, fire rasters and safe zones to
`dispatch.plan(...)` (`wgresponse/dispatch.py:939`). The solver assigns each crew an ordered stop
list that maximizes protected value subject to a hard safety chain, then a polish pass delays gel
application as late as safely possible. Everything below is where travel time, job time and fire
propagation actually enter that pipeline.

### 1.1 Travel time — tier-0 chords (the biggest realism gap)

**The model** (`wgresponse/travel.py:75`):

```
time_s = haversine_m(a,b) × circuity 1.35 × fire_traffic_factor 1.4 / road_speed 17.9 m/s
```

i.e. an effective straight-line speed of **9.47 m/s = 34.1 km/h = 21.2 mph** on every leg,
everywhere, at all times. It enters the plan in three places:

- **Stop timing / feasibility** — `_Ctx.eval_route` (`dispatch.py:349`): `arrive = prev_depart +
  travel`; a stop is infeasible if `depart = service + 30 min` misses `latest_depart − 15 min slack`
  (`_depart_fail`, `dispatch.py:133`).
- **Objective cost** — `route_contrib` (`dispatch.py:388`): drive hours × λ_travel, where λ_travel =
  1% of the mean potential stop score per drive-hour (`dispatch.py:269`) — a tie-breaker, deliberately small.
- **Corridor pass times** — the safety oracle converts the same model into an along-segment speed
  `road_speed/(circuity × fire_traffic) = 9.47 m/s` (`safety.py:167`) and checks each 300-m sample of the
  **straight chord** against every incident's eroded P10 raster (`safety.LegOracle`, `safety.py:257`).
  Egress zone ordering also uses tier-0 times (`safety.py:382`).

**Caching:** per `plan()` call, travel times and per-leg corridor T\* are memoized on exact rounded
coordinates (`_Ctx._tt`, `_Ctx._tstar`, `dispatch.py:283-307`) — that plus the closed-form T\* is
what took 20/200 from budget-starved to full-serve (Sprint 2 B1). `travel.travel_matrix_s` exists as
the upgrade seam but nothing calls it yet.

**Known error, measured.** Council-1 (dispatch F2) spot-checked five canonical WUI drives: on
mountain/canyon roads the model is **−21% to −33% optimistic under fire conditions** — the whole 1.4×
congestion allowance is consumed just reproducing *normal* drive times — while on freeways it is +43%
pessimistic (wasted capacity). The `slack_min = 15` floor (SAFETY SEMANTICS 7, `contracts.py:68`) was
added expressly to absorb this. New, stronger evidence this review (dr_exp2, 100 sampled
client/zone legs per location on the **real cached road graphs**, `data/roads/*.wgr`):

| location | road/tier-0 time ratio mean · median · p90 | legs where tier-0 optimistic | >1.2× | mean slip | measured circuity (vs 1.35) |
|---|---|---|---|---|---|
| paradise_ca | **2.43 · 2.15 · 4.46** | **97%** | 86% | **+20.1 min** | **2.43** |
| santa_rosa_ca | 1.73 · 1.39 · 2.80 | 92% | 72% | +9.6 min | 1.99 |
| malibu_ca | 1.58 · 1.25 · 2.99 | 79% | 53% | +5.1 min | 2.07 |
| park_city_ut | 1.36 · 1.13 · 2.30 | 63% | 48% | +2.3 min | 1.73 |
| boulder_co | 1.15 · 1.00 · 1.50 | 50% | 28% | −0.3 min | 1.58 |
| **all 500 legs** | **1.65 · 1.30 · 2.91** | **76%** | — | +10.8 min (max +63) | **1.96** |

(Ratio > 1 = the crew arrives later than the plan believes; the comparison bundles OSM free-flow
class speeds with geometry — the circuity column isolates geometry alone.)

**The structural point:** real road graphs now exist for all five locations (`wgresponse/roads.py`,
4.4k–33k nodes each, A\* routing, `route()` at `roads.py:436`) and the **simulator** moves crews along
them and re-plans from actual positions each tick — but **the planner itself still plans on chords**
(division-of-authority comment, `roads.py:13-19`, `contracts.py:203-207`). Divergence self-corrects
across cycles; it does not make any individual plan honest.

### 1.2 Time to complete a job — one number

- **Service duration:** `cfg.deploy_min = 30` minutes, identical for every property
  (`dispatch.py:352,529`). No per-home size/complexity, no access overhead (the spec's
  `access_overhead_min = 5` was never implemented), no crew shift limit (spec C6, also unimplemented).
- **Service window per job** (`safety.derive_job_windows`, `safety.py:211`):
  `earliest_useful_start = eta_p50 − gel_window` (deploying earlier would waste the coating) and
  `latest_service_start = eta_p10 − 30 min egress buffer − 30 min deploy` (SAFETY SEMANTICS 2).
  An empty window ⇒ the home is *lost to dispatch* with an explicit reason.
- **Gel freshness value decay** (`_gel_fresh_frac`, `dispatch.py:179`): full credit if the fire's
  median arrival comes within `gel_full_strength_h = 24 h` of departure, linear decay to zero at the
  window edge; the final **late-shift pass** (`_late_shift`, `dispatch.py:874`) then delays every
  non-frozen service toward `eta_p50 − 24 h`, capped by the slack floor and re-verified corridors —
  gel goes on as late as safely possible (Council-1 verified lead collapses 14.8 h → 5.3 h when the
  window shrinks, freshness stays 1.00).
- **Weather-scaled window (C5):** with `gel_env_model_enabled`, the window is capped by the coating
  life `6.0 h × exp(−0.08(T−25)) × (RH/50)^0.3 / (1+0.05·wind)` evaluated with the incident's own
  forecast (`safety.py:179,234-246`) — real fire weather gives 1.0–1.5 h, which turns the window into
  a *feasibility cliff*: forecast spread p50−p10 > window − 1 h ⇒ structurally unservable
  (`gel_window_closed`). All gel numbers remain **unverified supplier placeholders** (SAFETY SEMANTICS 5).
- **Chaining:** multi-stop routes propagate `depart → travel → arrive → max(arrive,
  earliest_useful) → +30 min → depart` down the route (`eval_route`), so one stop's slip cascades
  into every later stop — which is exactly why per-leg travel truth matters (§1.1).

### 1.3 Fire propagation coupling — where the fire actually bites

- **Deadlines from the pessimistic arrival:** `latest_depart = eta_p10 − (egress 30 + ember 0) min`
  (SAFETY SEMANTICS 2, `safety.py:198`). All *safety* math keys off P10; all *value* math (freshness,
  windows) keys off P50 — "deadline conservative, reward realistic".
- **Corridors against every fire:** each travel leg (ingress *and* egress-to-safe-zone) is sampled
  every 300 m against **every** incident's P10 raster eroded by 500 m (SAFETY SEMANTICS 3;
  `plan()` erodes at `dispatch.py:991`). Sprint-2 B1 made this O(1) per check via the per-leg
  closed-form latest-safe-departure `T* = min_i(arrival_i − 60 min margin − d_i/9.47 m/s)`
  (`safety.py:272`, `dispatch.py:291`). Jobless fires are still checked (Council-1 F1 fix), and a
  crew must always have a safe egress to some `SafeZone` at its planned departure — no zones ⇒
  nothing schedulable, fail closed.
- **Per-incident forecasts into windows:** the fire-centroid `WeatherForecast` collected during
  assessment rides `plan(forecasts=...)` into each job's gel-window derivation (`dispatch.py:1031`).
- **Replan cadence & stability:** the live loop re-plans every cycle with the previous plan as
  incumbent (`threat.py:745`); stops starting within `commitment_horizon_min = 20` are frozen
  (safety still trumps commitment, `dispatch.py:1087-1093`); voluntary reassignment pays a churn
  penalty (5% of mean stop score, ×2 for a crew's first stop); a new plan displaces the
  incumbent-shaped plan only if it wins by ≥ 3% (`dispatch.py:1179-1184`). The simulator re-plans
  every tick but passes `incumbent=None` and only *idle* crews (`simui.py:1618-1633`) — in the sim,
  commitment is absolute (an itinerary runs to completion unless a safety withdrawal fires).
- **Fire moves ⇒ plan reacts:** ETA drift re-derives windows next cycle; the simulator additionally
  re-checks every in-flight deploy against the newest P10 each tick and withdraws the crew the moment
  the projected departure would breach (`simui.py:1549-1569`) — 0 violations across the 1001-test suite.

### 1.4 Objective and solver — what "best plan" means today

**Objective** (`dispatch.py:54-59, 373-405`):

```
J = Σ_served  priority_weight × value_usd × gel_fresh_frac × 0.85
  − λ_travel(1% mean score/h) × drive_h  − churn(5% mean score) × reassignments
```

**Solver pipeline** (all feasibility funneled through one chokepoint, `_depart_fail` + corridor/egress T\*):
job dedup to one-per-client (worst-case P10 wins) → prune structurally infeasible jobs with explicit
reasons → repair + freeze the incumbent → **multi-start construction** {repaired-incumbent regret-2,
greedy earliest-deadline-first, cold regret-2} each polished by local search (relocate / inter-crew
swap / 2-opt / drop-and-refill) → **exact micro-solver** for pools ≤ 8 free jobs × ≤ 4 crews (ordered-route
DFS + subset DP — the launch-scale regime gets the TRUE optimum, `dispatch.py:758`) → ILS
(seeded ~20% shakes with one-iteration tabu + variable-neighborhood escalation) under the 5-s budget →
adoption threshold → **never-worse-than-greedy guardrail** on both objective and collected value
(`dispatch.py:1186-1196`). Deterministic given inputs absent budget pressure; unserved jobs carry an
honest reason vocabulary (`infeasible_deadline | infeasible_slack | gel_window_closed |
no_safe_corridor | no_safe_egress | capacity | dropped_by_optimizer`).

**Measured state (Sprint-2 exit gates):** 20 crews / 200 jobs: 200/200 served, value ratio 1.000× vs
greedy, walls ≤ 5.3 s (was 11/200 and 9× *worse* than greedy pre-B1/B2). Optimality-gap harness:
**mean 0.000%, max 0.000%, 0/24 served-count misses** vs brute force. Launch-scale solves run in
0.00–0.03 s. Solver quality is **not** the current bottleneck; input realism is.

### 1.5 What the three new experiments show

**dr_exp1 — value vs homes.** One crew, corridors safe, time for exactly one choice:
H1 = $4M alone north, H2+H3 = $1.5M each south. As-priced objective serves **H1 (1 home, $3.4M
score)**; with values forced equal it serves **H2+H3 (2 homes)**; raising the pair's
`priority_weight` to 3 also flips it (the config workaround available today). And:
`contracts.ResponseConfig.dispatch_objective` **defaults to `'homes'`** (`contracts.py:782`, founder
triage law `contracts.py:235-241`) while `dispatch.py` **never reads the field** — the declared
default and the shipped behavior disagree.

**dr_exp2 — chords vs roads.** Table in §1.1. Tier-0 is optimistic on 76% of the 500 sampled legs;
Paradise mean 2.43×; measured circuity 1.96 pooled vs the assumed 1.35.

**dr_exp3 — a tier-0 plan driven on the real Paradise network.** Planar Camp-Fire-geometry front over
the real tile, 12 WUI clients, 3 crews at the real safe zones. Stock plan serves 12/12; replaying the
exact planned routes at road speeds: arrival slip **mean +15.4 min, max +25.3 min per stop**; margins
erode (e.g. planned 66 → actual 41 min) but this slack-rich instance stays clear of both the hard
deadline and the 15-min floor — the floor and generous P10 leads did their job. Two sharper findings:
(a) **egress truth**: real road egress to the adopted safe zone runs **+40 to +64 min longer than the
tier-0 belief** on 7 of 12 stops — the planner's egress feasibility picture is the rosiest part of its
world model (the simulator compensates with its road-egress departure guard and withdraws; the planner
should have known at plan time); (b) re-planning the same instance with the *measured* circuity 2.43
cuts replay slip to **mean +1.8 min** and — under a 1.3 m/s pressure front — changes the plan itself
(10 served on optimistic times vs 9 on honest ones: one planned save was travel-time fiction).

---

## 2. Improvements, ranked by founder value ÷ effort

| # | Improvement | Effort | Expected effect | Safety-relevant? |
|---|---|---|---|---|
| 1 | Plan on the real road network | M/L | kills a 1.65–2.43× mean travel error; honest egress; fewer phantom saves | YES — strengthens SEM 3/7 inputs (laws unchanged) |
| 1b | (interim) per-area circuity calibration | S | Paradise error 2.43× → ~1.0–1.2×; one config row per area | input-only, conservative |
| 2 | Count-first objective (`dispatch_objective='homes'`) | S/M | founder triage law actually runs; 2 homes beat 1 expensive home | NO — objective only |
| 3 | Objective-side triage/abandonment (`'triaged'`) | M | releases crews from doomed/low-yield commitments to save more homes | NO (withdrawal law untouched) |
| 4 | Anticipatory staging of idle crews | S/M | cuts first-leg response time when EXTREME lands | NO (stage only at safe zones) |
| 5 | Time-dependent fire_traffic (congestion near fire) | M | stops over-penalizing far freeway legs, under-penalizing canyon legs | input-only; keep ≥1.4 near fire |
| 6 | Uncertainty-aware windows/value (E[fresh], P_meet) | M | derates wide-spread forecasts smoothly instead of cliffing | NO — objective only |
| 7 | Solver upgrades (MaxShift/ALNS/CP-SAT harness) | L | none today — gap is already 0.000% at review scale | NO |

**Safety framing for all of the above:** nothing here may weaken SAFETY SEMANTICS 2 (P10 deadlines),
3 (eroded-P10 corridors), or 7 (slack floor). Items 1, 1b and 5 change the *inputs* those laws consume
(travel times / pass times) in the conservative direction; items 2, 3, 4, 6, 7 are objective-side only.
`corridor_safe` stays the public verdict authority; byte-identity discipline applies (new travel
providers land behind a config switch with tier-0 as default until gated).

### 2.1 Plan ON the road network (the one that matters)

**Why:** §1.1/§1.5. The planner's three uses of travel (stop timing, cost, corridor pass times) are all
fed by a model measured 2.43× optimistic in the flagship service geography, while a better source is
already in the repo, cached, deterministic, and dependency-free.

**Design (keeps the `travel_time_s(a,b,cfg)` seam):**
1. `cfg.travel_provider = 'tier0' | 'roads'` (default `'tier0'` until gated). A `RoadTravel` provider
   wraps the location's `RoadGraph`.
2. **Matrix precompute, not per-leg A\*:** properties don't move — build the job×job + job×zone time
   matrix once per incident AOI with one **single-source Dijkstra per job node** (200 sources on a
   4k–33k-node graph is seconds, cacheable across cycles keyed on client ids); refresh only crew rows
   each cycle (N ≤ 20 single-source runs, ~ms each). This is exactly the spec §4.3 caching rule and
   fits the existing `travel_matrix_s` seam. Off-graph endpoints (> 2 km snap) fall back to tier-0
   loudly, as `roads.route` already does.
3. **Corridors along the actual path:** `LegOracle` already reduces a leg to (sample points, cumulative
   distance) — feed it the **route polyline** samples instead of chord samples, with per-edge speeds
   for pass times. T\* math, margins, erosion are unchanged. A canyon road that detours *through* the
   fire's path now fails corridor checks the chord would pass — and vice versa honestly.
4. **Egress:** zone choice + egress T\* on road paths; this is the +40–64 min truth from dr_exp3 and
   it also retires the simulator's compensating road-egress withdrawal churn.
5. Calibrate `fire_traffic_factor` against the OSM free-flow baseline separately (it currently
   double-duties as a circuity patch; on roads it can mean congestion again).

**Expected effect:** Paradise mean per-leg error +20.1 min → ~±2 min (dr_exp3 B); egress error → ~0;
under pressure the plan stops promising saves the roads can't deliver (10 → 9 in the pressure
instance — the honest number) and stops *skipping* freeway-reachable homes the current model
overprices by +43%. **Risk:** runtime (mitigated by the matrix design; measure with `bench_dispatch`),
graph coverage gaps (10% of Paradise legs snapped off-network in dr_exp2 — the loud tier-0 fallback
already handles this), and the live path needs graphs fetched per AOI (the fetch tool exists for the
five sim locations).

**Interim (this week, S):** keep chords but set per-area circuity from dr_exp2's measured column
(paradise 2.43, santa_rosa 1.99, malibu 2.07, park_city 1.73, boulder 1.58 — pooled 1.96). One config
value; instantly conservative everywhere; costs some efficiency on freeway-heavy areas until #5 lands.

### 2.2 "As many homes as possible" — implement the declared objective

**Why:** dr_exp1; and the founder's law is already contracts-default (`dispatch_objective='homes'`)
while the code does value — silent drift between the law and the engine.

**Design:** `_potential_score` (`dispatch.py:162`) is the single scoring chokepoint (objective,
regret ties, drop-refill ordering, diagnosis). In `'homes'` mode:
`score = priority_weight × (1.0 + value_usd / value_cap × ε)` with ε ≈ 0.01 — i.e. every home is
worth ~1 point, value only breaks ties (lexicographic count-then-value without touching the
arithmetic shape; λ_travel/churn auto-scale off the mean score). The greedy-EDF guardrail is already
count-natural (it appends *everything feasible*, no value filter — `dispatch.py:554`), so the
never-worse floor gets stronger, not weaker. The exact micro-solver's tie-break already prefers more
served stops. Re-baseline the 24-instance optimality harness in both modes (brute force honors the
same score function); expect served-count ↑ on tight instances, planned $ slightly ↓ — report both
numbers per plan (`objective_breakdown` already carries served + value).

**Also fix the drift now:** until the mode is implemented, either wire it or flip the contracts
default to `'value'` — a declared-but-ignored safety-adjacent config default is a trust bug.

### 2.3 Objective-side triage / abandonment (distinct from safety withdrawal)

**Why:** founder law 4: a crew committed to a stop that has become a poor use of its remaining time
(fire accelerated elsewhere; two savable homes appeared) should be re-taskable *before* safety forces
anything. Today: non-frozen stops already get displaced (`dropped_by_optimizer`), but (a) frozen
stops (< 20 min out) can never be dropped for value, only for safety, and (b) in the simulator a
dispatched itinerary is never re-planned at all — only idle crews enter `plan()` (`simui.py:1618`),
so mid-route re-tasking is structurally impossible there.

**Design:** (1) let the simulator pass en-route crews + their uncommitted tail stops back into
`plan()` with a real incumbent, so the existing churn/commitment machinery — built for exactly this —
actually operates in the sim; (2) add a triage rule on frozen-but-unstarted stops: droppable only if
the replacement plan serves **strictly more homes** (count mode) and pays the churn price; emit
unserved reason `'triaged'`; (3) anti-thrash: a stop may be triaged at most once per commitment
horizon, and never while the crew is on-site (`DEPLOYING` is the withdrawal law's jurisdiction,
untouched). Guardrail: `served_homes(new) > served_homes(incumbent-repaired)` or no triage.

### 2.4 Anticipatory positioning of idle crews

**Why:** idle crews sit wherever they finished (sim: staged at their last safe zone). When the 24-h
EXTREME band sweeps a subdivision, response time is paid up front in the first leg.

**Design (cheap heuristic, no new safety surface):** after each plan, for every idle crew compute the
deadline-weighted centroid of (unserved-feasible jobs + clients inside the forecast h5/h24 bands —
the rasters are already in hand), and stage the crew at the **safe zone nearest that centroid**
(stay on safe zones only; the leg is corridor-checked like any other). Emit as a zero-value staging
stop / `egress_zone_id` recommendation. Measure in the simulator: time-to-first-gel and served count
on pressure scenarios. Later upgrade: k-median over zones with per-crew assignment.

### 2.5 Time-dependent travel (congestion where the evacuation is)

**Why:** the flat 1.4× penalizes a freeway leg 30 km from the fire exactly like a canyon road inside
the evacuation zone. The spec (§4.1) prescribed a congestion radius; it was never implemented.

**Design:** `LegOracle` already samples every leg against the eroded arrival rasters — reuse those
samples: legs whose minimum (arrival − pass_time) falls below a threshold (i.e. near-band at
pass time) get `fire_traffic_factor` (≥ 1.4, calibratable up), far legs decay toward 1.0×. One
fixed-point pass (compute factor from unadjusted pass times) keeps it deterministic and cheap.
Polarity guard: never *reduce* below 1.0, never reduce inside the congestion radius; net effect is
conservative near fire, efficient far away.

### 2.6 Uncertainty-aware service value

**Why:** deadlines already use P10 and value uses P50 (good), but value is a point estimate: a job
whose p10–p90 spans 20 h gets the same freshness credit as a sharp forecast, and `P_meet` from the
spec became a binary slack floor rather than a probability.

**Design (objective-only):** expected freshness over the three quantiles the ensemble already emits —
`E[fresh] ≈ 0.3·fresh(p10) + 0.4·fresh(p50) + 0.3·fresh(p90)` — replacing `fresh(p50)`; optionally
multiply by `P_meet = Φ(planned_slack / σ_travel)` with σ_travel fit from the dr_exp2 road-vs-model
residuals (the data now exists). Deadlines/corridors stay pure P10. Effect: wide-spread long-lead jobs
get honestly derated instead of competing at full value, and slack becomes continuously valuable
above the floor rather than free.

### 2.7 Solver upgrades — explicitly not now

Honest note: opt-gap is 0.000% on all 24 brute-forceable instances, value ≥ greedy is guaranteed *by
construction* on every instance, exact optimum below 8 jobs × 4 crews, and 20/200 finishes in budget.
O(1) MaxShift (B4), ALNS, or an OR-Tools CP-SAT cross-check harness only earn their keep if the
portfolio grows toward 500+ simultaneous jobs or the road-matrix work (#1) re-tightens the budget.
Build the CP-SAT harness, if ever, as an *audit* tool (independent optimum reference), not a runtime
dependency.

---

## 3. Smaller gaps noticed in passing (S-sized, listed for completeness)

- `deploy_min` is uniform 30 min; the spec's `access_overhead_min = 5` (gates, hose-out) and crew
  shift limits (C6) were never implemented. Fine at launch scale; add when crews report real times.
- The simulator plans with `incumbent=None` each tick (commitment lives in crew itineraries instead) —
  intentional, but it means warm-start/churn behavior is only exercised on the live `threat.py` path.
- `P_hit` (probability the fire actually reaches the home) from spec §2 was never implemented;
  freshness stands in for it. Folded into improvement #6.
- Gel window / full-strength / effectiveness are still unverified supplier placeholders
  (SAFETY SEMANTICS 5) — every objective number inherits that caveat. Unchanged external blocker.

## 4. Re-run instructions

```
py -3 -m pytest -q                                      # 1001 green (verified this review)
py -3 scratch_review/dr_exp1_value_vs_count.py           # objective: $4M home vs 2×$1.5M homes
py -3 scratch_review/dr_exp2_roads_vs_tier0.py           # 100 legs × 5 locations, chord vs road
py -3 scratch_review/dr_exp3_plan_replayed_on_roads.py   # tier-0 Paradise plan driven on real roads
```

All three are seeded/deterministic and touch no product code.
