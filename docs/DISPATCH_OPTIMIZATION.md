# Dispatch Optimization — Complete Analysis (2026-07-11)

Synthesis of every dispatch measurement to date: the improvement loop
(`scratch_review/bench_dispatch.py`, 60-scenario suite = 5 maps × 3 winds ×
2 distances × 2 intensities, hard gate 0 safety violations), the capacity and
density probes, and the Council/field reviews. Objective throughout: TOTAL
homes coated in time, never trading safety.

## 1. Current solver (what we ship)

Deadline-TOPTW: dedupe → feasibility gates (P10 deadline, slack floor SEM-7,
eroded-P10 corridors both ways, fail-closed zones) → greedy-EDF seed + frozen-
skeleton regret-2 insertion → local search → ILS(30) → emit with count floor
(never fewer homes than greedy). Homes mode: count primary, value an EPS-band
tiebreak; anti-concentration keeps all crews working; per-tick replan with
20-min commitment freeze. Solver optimality gap at launch scale: **0.000%**
(post-T*-precompute, 200/200 served vs exact micro-solver).

## 2. Measured lever table (what works, what doesn't)

| Lever | In-sample Δhomes | Out-of-sample | Verdict |
|---|---|---|---|
| **Coat-early** (drop gel-freshness late-shift) | +7 (+0.9%) | **+2…+18 positive on all 8 held-out ratios**, freshness neutral | ✅ SUPERSEDED BY PRODUCT DATA 2026-07-11: GraphGel is Class A at 1/8" DRY (flat effectiveness, `gel_full_strength_h == gel_window_h`), which makes the late-shift a structural NO-OP — coat-at-earliest is now simply what the engine does, no toggle. The sim toggle was removed the day it shipped. |
| **Deploy 30→15 min** (3-person crews) | **+84 (+4.9pp)** | consistent | ✅ ADOPTED as operating model |
| Deploy 15→10 min | +67 more | consistent | equipment/process target |
| Crews 4→6 (40 homes) | +97 | consistent | $ decision, see §4 |
| Urgency in objective (any weight) | +10 @0.2–0.3 | **−9…−24** | ❌ DEAD END (3rd failure: −113, −4, −24) |
| EDD/at-risk-first emit order | −4 | — | ❌ dynamic commitment feedback |
| ILS 30→80/120/160/240/320 | +3/+19/+10/+10/+10 | non-monotonic | ❌ fragile, don't chase |
| Commitment 20→10 min | +4 w/ coat-early, −2 stacked | mixed | ❌ neutral |
| Churn penalty tuning | 0 | — | ❌ neutral |
| value_weight → 0 | +1 | **1 safety violation** | ❌ keep 0.05 |

**Standing law from three independent failures:** urgency-steering the served
SET always loses homes under replan feedback. The count-maximal set, chosen
urgency-blind, is the correct objective. Do not revisit without a
fundamentally different mechanism (e.g. lookahead simulation, not weighting).

## 3. The ceiling, decomposed

Reactive response (detect at ignition) saturates:

- 40 homes: 2 crews 41.7% → 4: 50.6% → 6: 56.3% → 8: 58.8% → 12: ~59%+ε.
- 727 of 878 lost homes are lost at ANY capacity — the fire arrives before
  travel + 15 deploy + 30 egress fits. **This is a physics wall, not a solver
  gap** (solver gap is 0.000%).

Therefore optimization effort now splits three ways:

**(a) More minutes** — the only thing that beats the wall:
1. **Alert-driven pre-coat** (biggest — STRENGTHENED by 2026-07-11 product
   data): an event visible on the 72h/24h ladder lets crews coat the WHOLE
   community before arrival. GraphGel is Class A at 1/8" DRY film — the dried
   coating IS the protection, so early application holds (no freshness race
   within the 72h operational window). The trigger moves from EXTREME_24H up
   to **WATCH (P50 ≤ 48h)**: 2 crews finish 40 homes in ~5.3h, unhurried, in
   daylight, fire still 1–2 days out. Reactive 50% → alerted ~100% coated
   (survival then = gel effectiveness, the remaining supplier number).
   SEASONAL pre-coating is OFF THE TABLE (founder 2026-07-11): the coating is
   OPAQUE and owners won't accept it preventively — application happens only
   under live threat, which the WATCH trigger already is. Post-event removal/
   wash-down joins the service model. Engine note: with the flat gel model the
   existing planner already coats at the earliest feasible moment (window
   opens at p50−72h), so "precoat mode" reduces to validating long-lead
   coverage + crew-shift scheduling for multi-day ops.
2. **Detection latency** — every minute of detection delay is a minute off
   every lead; p10 leads are ~1–2h, so 15 min of latency is material. Cameras/
   ALERTWildfire + FIRMS + Watch Duty enterprise; measure feed-to-cycle lag.
3. **Pre-positioning on red-flag days** (staging at community safe zones before
   ignition) — removes the +60/+180 min mobilization tiers from the critical
   path exactly when fires are likeliest.

**(b) More honesty** — same homes, fewer surprises:
4. **Plan on the road network — BUILT + SHIPPED 2026-07-12** (founder build
   order after the chords-only control attributed ~73% of relocations + the
   whole residual overrun tail to the chord-vs-road mismatch). Dispatch
   travel = the road route's own time (kinematic truth); corridor T* oracles
   sample along the route polyline (`safety.LegOracle path=`); road legs use
   `route_margin_roads_min=30` (the chord margin's extra 30 was the F2
   flat-travel-error compensator — redundant under honest geometry; the
   isolation sweep proved travel-honesty ALONE is dangerous: +30 homes but
   7× the overrun-ticks — corridor honesty is the safety component).
   MEASURED (15-min crews, 60-scenario sweep, per-tick): relocations
   383→121 (−68%), overrun-ticks 3→0, withdrawals 7→2, served 893→882
   (−1.2% honest-capacity cost); OOS 60/4: 1245→1189 (−4.5%, ov 14→0);
   30/3: 655→657 (+2, ov 4→0); extended Camp-Fire + alerted regime clean
   (0 violations on every map). Default-OFF in the engine
   (`dispatch_plan_on_roads`); the simulator ships it ON.
5. Corridor-aware defensibility scoring (close the harness's optimism).

**(c) More throughput** — squeeze inside the wall:
6. Deploy 15→10 min (equipment: pre-rigged manifolds, two applicators + one
   prep runner rotating).
7. Anticipatory staging of momentarily-idle crews toward the threat centroid
   (respecting SEM-10 safe zones) — helps second-wave assignments; measure
   before shipping (same OOS discipline).
8. Micro: egress-zone pair caching and replan-rate tuning are done (T*
   precompute landed; 94% of plan() cost eliminated).

## 4. Crew economics (40-home community, reactive regime)

| Crews | People (3/crew) | Homes saved of 40 threatened* | Marginal homes/crew |
|---|---|---|---|
| 2 | 6 | ~17 | — |
| 4 | 12 | ~20 | +1.8 |
| 6 | 18 | ~22.5 | +1.2 |
| 8 | 24 | ~23.5 | +0.5 |

*Scaled from 60-scenario aggregate (1710 threatened). Marginal crew value
collapses past ~5 homes/crew — buy detection and pre-coat capability before
the 7th crew, every time.

## 4b. COATING HOLD (FOUNDER ROUND 5 — built + shipped 2026-07-12)

"If fire is more than 24 hours away, do not coat homes; place crews on alert
and preposition — as long as all homes can be coated per the current spread
model plus a weather buffer." Implemented as a per-cycle feasibility GATE:
held jobs (p50 > 24h) are withheld from the emitted plan only while a probe
re-plan — held starts deferred to their release time, held deadlines
tightened by 6h — still serves every home the coat-now plan would. Gate
failure releases the hold (latched). Idle crews preposition to the safe zone
nearest their deferred first stop (lawful routes only). MEASURED (roads
config, 15-min crews, per-tick 0 violations everywhere): served 882→931
(+5.6%), OOS 60/4 1189→1238 (+4.1%), 30/3 657→693 (+5.5%); alerted regime
76.0→79.5%. The founder's policy beats per-tick count-max myopia: withholding
far work sequences near-deadline homes first and prepositioned crews strike
closer at release — the first lever that is BOTH safety-clean and
served-positive out-of-sample. It also avoids speculative coats on fires
that never arrive (opaque product; owners accept coating only under live
threat). Knobs: dispatch_hold_h (0=off; sim ships 24),
dispatch_hold_weather_buffer_h (6).

## 5. Recommended order of work

1. Alert-driven pre-coat mode (engine + protocol; pairs with HOA playbook).
2. Plan-on-roads travel model.
3. Detection-latency instrumentation + camera/Watch Duty integration (ops).
4. Remaining gel supplier data (decay question ANSWERED 2026-07-11: Class A at
   1/8" dry, flat effectiveness — engine updated, late-shift now a no-op).
   Still open: coverage rate (volume/home — gates 15 min/home + inventory),
   drying time, rain wash-off, weathering horizon beyond 72h.
5. Pre-positioning triggers off NWS red-flag in the watch daemon.
6. Anticipatory staging experiment (sweep + OOS gate before ship).

Dead ends (do not re-litigate without new mechanism): urgency weighting, EDD
emit ordering, ILS budget, commitment shortening, churn tuning, value_weight=0.
