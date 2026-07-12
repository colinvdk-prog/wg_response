# Dispatch & Alert Quality Review — wgresponse

**Reviewer lane:** dispatch-optimizer decision quality + alert-ladder behavior under messy conditions
**Date:** 2026-07-09 · **Engine state:** 453 tests green (verified before and after; product code untouched)
**Experiments:** `scratch_review/da_exp1_optgap.py` … `da_exp6_travel.py` (+ `da_common.py`, `da_exp1_debug.py`), all seeded/deterministic, run with `py -3` from repo root.

---

## Verdict in one paragraph

The dispatch core is near-optimal in the common regime (mean optimality gap 0.006% when everything fits) and the alert ladder passes every hysteresis/terminality edge case thrown at it. The problems live at the **seams**: the orchestrator silently drops the fire rasters of incidents that produced no dispatch jobs this cycle, so corridor safety checks can miss a fire the system has already modeled (demonstrated route-through-fire, F1 — the one safety-polarity finding); the flat travel model's entire 1.4× congestion allowance is consumed just reproducing *normal* drive times on winding WUI roads (F2); and the unverified gel window is not a tuning knob but a coverage cliff — at the wgdefense 6 h coating life, every client whose forecast spread (p50−p10) exceeds ~5 h becomes structurally unservable (F5).

---

## Findings (severity order; safety polarity first)

### F1 — P1 · SAFETY · Corridor checks ignore incidents that produced no jobs this cycle

`threat.ThreatMonitor._maybe_dispatch` forwards only the ensembles of incidents that generated a `DispatchJob`:

```python
ens_used = {job.incident_id: ensembles[job.incident_id] for job in jobs}   # threat.py:279
```

`dispatch.plan` checks every leg against every raster **it is given** — but an active incident whose client pairs are all IMPACTED / CLEARED / not-yet-alerted contributes no job, so its raster is dropped, and crews can be routed straight through its spread path. Demonstrated (`da_exp4_multiincident.py`, part B): identical job + geometry, incident B's fire footprint sitting mid-route with arrival = now:

| ensembles passed | result |
|---|---|
| `{A}` (today's `ens_used` behavior) | **served=1**, crew routed through B's footprint |
| `{A, B}` (all assessed incidents) | served=0, `no_safe_corridor` (correct) |

Deeper variant of the same root cause: `_assess_incident` skips spread entirely for incidents with no protected client within 60 km, so a fire that threatens only *roads* (not clients) is never modeled at all and is invisible to every corridor check.

- **Polarity:** safety (crew routed through known fire).
- **Fix 1 (S):** pass `ensembles` (all assessed) instead of `ens_used` in `_maybe_dispatch`; the only cost is a few extra raster erosions per cycle. One-line change + one regression test reproducing part B.
- **Fix 2 (M, follow-up):** spread any active incident whose ignition geometry is within the threat radius of a *planned route corridor* (not just of a client), or at minimum note the un-modeled incident in `CycleResult.notes` as a dispatch caveat.

### F2 — P1/P2 · SAFETY-ADJACENT · Flat travel model is optimistic on exactly the roads that matter

Effective straight-line speed of the model = 17.9 / (1.35 × 1.4) = **34.1 km/h (21.2 mph)**. Spot-check against five canonical WUI pairs (`da_exp6_travel.py`; reference times are analyst route-knowledge estimates for NORMAL conditions — no routing API in the review environment — so treat ±10% on the refs):

| pair | hav km | model min | normal ref | err vs normal | err vs fire-condition ref (×1.4) |
|---|---|---|---|---|---|
| Paradise→Magalia (Skyway) | 6.9 | 12.2 | 11 | +11% | **−21%** |
| Boulder→Nederland (canyon) | 21.3 | 37.5 | 35 | +7% | **−23%** |
| Hemet→Idyllwild (SR-74 switchbacks) | 24.0 | 42.3 | 45 | −6% | **−33%** |
| Santa Rosa→Calistoga (Mark West grade) | 19.3 | 34.0 | 35 | −3% | **−31%** |
| Sylmar→Acton (freeway) | 28.5 | 50.1 | 25 | +100% | +43% |

Reading: on 2-lane/mountain/canyon pairs the model lands **within ±11% of the *normal* drive time** — i.e. the 1.4× `fire_traffic_factor` is fully consumed by under-modeled circuity/speed and there is **~zero remaining allowance for evacuation congestion** on exactly the road class where congestion is worst (one-road-out canyons). Bias polarity on mountain roads is therefore **EARLY (optimistic)** under fire conditions, −21% to −33%: crews arrive later than planned, corridor pass times are later than the times the eroded-P10 check was run at, and departures get pushed toward `latest_depart`. On freeway pairs the model is LATE (+43%), which only costs efficiency.

Is it inside the safety margins? Mostly-but-not-everywhere:
- The 60-min `route_margin_min` absorbs a 33% error on legs up to ~3 h modeled — corridor checks stay conservative for realistic leg lengths. ✔
- The 30-min egress buffer is protected only if the *planned stop margin* absorbs the arrival slip. **EXP1 observed planned stops with `margin_h` = 0.17 h (10.2 min)** — a 20–30% travel slip on a 40-min inbound leg (~10 min) fully consumes that. The 15-min replan cadence is the real backstop, but a crew already deploying is committed. ✘
- Note the simulator audits with the *same* travel model (`_eff_ground_speed_mps`), so this bias is structurally invisible to `SimReport.violations`.

- **Polarity:** mixed — EARLY on mountain roads (threatens margin), LATE on highways (efficiency loss, and the +100%-vs-normal case also *under-serves*: jobs dropped that were actually reachable).
- **Fix (S):** add a planned-slack floor (`slack_min`, default 10–15 min) to stop feasibility — the spec (§2 `P_meet`) already calls for exactly this; it was never implemented.
- **Fix (M):** the one-time per-service-area circuity calibration script from the spec (§4.1, sample ~200 OSRM routes offline); raise mountain-area circuity to measured (likely 1.6–2.0).
- **Fix (L):** Tier-1 self-hosted Valhalla per the documented upgrade path.

### F3 — P2 · MISSION (unprotected home) · ILS gets stuck below plans a naive greedy finds

24 seeded 2-crew × 5–7-job instances with tight deadlines (`da_exp1_optgap.py`), true optimum by exhaustive subset/ordering enumeration under the identical objective:

| solver | mean gap | max gap | served-count misses |
|---|---|---|---|
| `dispatch.plan` (regret-2 + ILS) | **0.79%** | **9.64%** | **2/24** |
| naive earliest-deadline greedy | 6.76% | 34.66% | 3/24 |

(In the loose-deadline regime — everything fits — the plan gap is mean 0.006% / max 0.04%, pure travel-ordering noise. The regret-2+ILS lift over EDF is real and large: EDF loses up to 35% of protectable value on tight instances.)

But on instances 11 and 14 the solver leaves a **feasible, high-value job unserved** (unserved reason honestly reports `capacity`) at a 9.3–9.6% objective cost — one unprotected $1.2–2.2M home each — while **plain EDF finds the full-serve plan**. Raising `ils_iterations` 30→300 and the budget 3 s→30 s does not recover it (`da_exp1_debug.py`: identical plan, solver_s 0.03 — the search converges, it doesn't time out). Root cause is structural: the shake removes `max(1, 0.2·stops)` = 1 stop on small instances, regret-2 deterministically re-inserts it into the same shape, and serving the missed job requires simultaneously restructuring *both* routes — outside every neighborhood (relocate/swap/2-opt/drop-refill are all 1–2 stop moves).

- **Polarity:** efficiency/mission (a servable home goes ungelled); crew safety unaffected.
- **Fix (S):** multi-start construction — also build an EDF-seeded and a pure-greedy-seeded plan alongside the incumbent-seeded one, run local search on each, keep the best. EDF is already effectively free and provably wins on both failure instances.
- **Fix (M):** strengthen the shake (remove 2 + 20% stops, or segment-based removal biased to route boundaries).

### F4 — P2 · EFFICIENCY + REPORTING · One client under two incidents = double gel, double-counted value

Per-pair `(client, incident)` job identity is never collapsed to per-property. Demonstrated (`da_exp4_multiincident.py`, part C): client C1 threatened by INC-A (p50 6 h) and INC-B (p50 8 h) yields **two planned stops at the same house back-to-back** (service +0.20 h and +0.70 h — 60 min of crew time to apply gel twice) and `value_usd` = **$3.4M for a $2.0M×0.85 = $1.7M property**. The inflated objective also distorts every downstream comparison (adoption threshold, churn pricing, drop decisions — a double-counted house outbids two real houses).

- **Fix (S):** dedupe jobs per `client_id` at plan entry (keep the pair with the earliest `latest_service_start_s`; corridor checks already run against every incident raster, so safety is unaffected). Alternatively dedupe in `threat._maybe_dispatch`.

### F5 — HIGH (business blocker, now quantified) · Gel window: 6 h vs 72 h is a coverage cliff, and the env model never sees weather

`da_exp3_gelwindow.py`, 2 crews / 8 jobs, median arrivals 5–40 h out, ensemble spreads 1–8 h:

| setting | served | planned value | mean/max gel-complete lead (h before p50) | window-caused unserved |
|---|---|---|---|---|
| window = 72 h | 8/8 | $9.85M | 14.8 / 24.0 | — |
| window = 24 h | 8/8 | $9.85M | 14.8 / 23.5 | — |
| **window = 6 h** | **5/8** | **$7.23M (−27%)** | **5.3 / 5.5** | **W06, W07, W08** |
| env model ON (ref 6 h) | 5/8 | $7.23M | 5.3 / 5.5 | W06, W07, W08 |

Two clean results:
1. **The optimizer behaves correctly:** with a short window it deploys as late as safely possible (lead collapses 14.8 h → 5.3 h; gel freshness at arrival stays 1.00 in every configuration — the late-shift pass works).
2. **The window is a feasibility constraint, not a decay knob:** a job is servable only if `p50 − p10 ≤ window − (egress 0.5 h + deploy 0.5 h)`. At 6 h that means **forecast spread > 5 h ⇒ structurally unservable**, no matter how many crews exist. Every long-lead client (26–40 h out, where ensemble spread naturally grows) fell off the plan. If the supplier confirms anything near wgdefense's 6 h base life, the *product promise* changes shape — service becomes a last-minute sprint gated by forecast sharpness. This is why the unverified 72 h number is the single most decision-relevant open input in the engine.

**Code defect on top (M):** `dispatch.plan` calls `safety.derive_job_windows(job, cfg)` **without a weather forecast** (dispatch.py:658), and `ThreatMonitor` never provides one, so with `gel_env_model_enabled` the coating life is always the 25 °C/50%RH/0-wind reference **6.0 h**. Under actual fire weather the same model gives **1.47 h** (33 °C/12%/8 m/s) or **1.00 h** (38 °C/8%/15 m/s) — 4–6× shorter. Polarity: gel could be expired at fire arrival while the plan reports `gel_fresh_frac=1.0` (effectiveness, not crew safety). Fix: thread the incident-domain `WeatherForecast` from the cycle into `derive_job_windows` (the parameter already exists and is tested in safety.py; it's a plumbing gap).

### F6 — Alert ladder: passes the edge cases; two client-comms defects

Verified good (`da_exp5_alerts.py`):
- **Hysteresis (b):** p50 oscillating 24 h ± 1.5 h over 10 cycles → **1 transition total** (entry), zero EXTREME↔WATCH flapping. Same for p10 oscillating 12 h ± 1.5 h, and for p50 oscillating 30 h ± 1.5 h (stays EXTREME — sticky on the safe side; a client can sit in EXTREME indefinitely on a hovering forecast, acceptable polarity, worth knowing for comms tone). Clean de-escalation (steady 33 h) works: EXTREME→WATCH after `clear_cycles`.
- **Terminality (c):** EXTREME→IMPACTED on median passage, then 5 "contained" (absent) cycles → stays IMPACTED, **0 further events**.
- **NOTICE one-shot (d):** steady 60 h × 10 cycles → exactly **1** NOTICE_72H, auto-progress to WATCH on the next cycle (+15 min), nothing further; recede-then-return correctly re-notices after CLEARED; fast fires legitimately skip NOTICE (first-ever message is EXTREME).
- **Independence (a):** states per `(client, incident)` pair are fully independent; ladder-legal throughout.

Defects:
- **F6a (M):** contradictory-sounding client messaging under multi-incident. Measured stream for one house: `EXTREME (~20h)` at cyc0, then `"Monitoring Casa Uno … under continuous watch; crews scheduled as needed" (~60h)` 15 min later (other incident), then `IMPACTED`, then `"All clear for Casa Uno …"`. Only the CLEARED template names its incident; NOTICE/WATCH/EXTREME don't, so a lower-severity message can read as a downgrade of the active EXTREME. **Fix (S):** include incident name in every template + suppress (or reframe as "second fire") any message for a client whose max state across pairs is higher than the event's state. Notifier-side aggregation keyed on `client_id`.
- **F6b (L):** forecast jump-past: WATCH pair whose p50 goes from 40 h to already-passed in one cycle emits `EXTREME — "Crew dispatch underway — ensure property access now"` **at p50 = 0.0 h** (fire arriving), with IMPACTED only one cycle (15 min) later — the ladder must step through EXTREME to reach IMPACTED. **Fix (S):** when `impacted_now` is already true on entry to EXTREME, swap in the impact-imminent message (state machine unchanged).

### F7 — L/M · Plan churn: commitment honored; anti-thrash machinery shows no measurable lift; order churn undamped

8-cycle replay, 14 jobs / 2 crews, ±0.5 h/cycle ETA wobble + a 1 h forecast acceleration at cycle 4 (`da_exp2_churn.py`):

| config | reassigned (7 transitions) | ping-pong stops (moved >1×) | reorders | frozen violations | mean value/cycle |
|---|---|---|---|---|---|
| A: defaults (churn 5%, adoption 3%) | 7 | 2 (J01, J08) | 40 | **0** | $22.81M |
| B: anti-thrash OFF | 6 | 1 (J14 ×3) | 41 | **0** | $22.81M |

- **Good:** the 20-min commitment horizon is honored perfectly (0 frozen-stop violations in both configs, including through the cycle-4 acceleration); cross-crew reassignment is modest (~1/cycle) under realistic wobble; the adoption threshold held the warm plan 5/8 cycles at **zero** measured value cost.
- **Weak:** the churn penalty produced *no measurable reduction* in reassignments or ping-pong vs penalties-off (7/2 vs 6/1 — noise-level, slightly worse), i.e. the 5%-of-mean-score price is either not binding or is being paid anyway; and **intra-route order changes are entirely unpriced**: ~5.7 stop-position changes per 15-min cycle (~41% of stops reshuffle each replan). Crews following a phone itinerary see their stop list reshuffle every cycle even when their assignment is stable. The spec (§8.2) prices "crew **or position class** changed"; the implementation prices only crew changes (`route_contrib` churn term).
- **Fix (S):** add an order-churn term (position-class change = downstream sequence delta beyond the frozen prefix) at a small weight; add a replay test asserting bounded reorders under pure-noise wobble. Alternatively: only re-emit a crew's itinerary when its own route changed materially (presentation-layer damping).

---

## Optimality-gap detail (EXP1, tight-deadline regime)

22/24 instances solved to exact optimum (gap ≤ 0.04%, all from travel-order ties). The distribution is bimodal: essentially-zero everywhere except the two structural misses (9.33%, 9.64%). EDF baseline: mean 6.8%, max 34.7%, 3 served-count misses — regret-2+ILS is clearly worth its complexity, it just needs a multi-start escape hatch (F3). Solver wall time never exceeded 0.03 s on these sizes; the 5 s budget is idle headroom at launch scale.

---

## Top 5 sprint candidates (ranked)

| # | ticket | effort | polarity |
|---|---|---|---|
| 1 | **Pass all assessed ensembles to the dispatcher** — `threat._maybe_dispatch`: `ens_used` → `ensembles`; add regression test reproducing EXP4-B (route through jobless incident's fire) | **S** | safety |
| 2 | **Planned-slack floor** — reject/penalize stops with `margin_h < slack_min` (default 10–15 min, config) in `dispatch` feasibility; spec §2 `P_meet` already prescribes it; covers the measured −21…−33% mountain travel optimism | **S** | safety margin |
| 3 | **Thread weather into gel windows** — `ThreatMonitor` passes the incident-domain `WeatherForecast` through `plan()` into `derive_job_windows(job, cfg, wx)`; without it the env gel model is a constant 6 h and fire-weather life (1.0–1.5 h) is ignored. Keep default-OFF; unblocks honest supplier-number sensitivity runs | **M** | effectiveness |
| 4 | **Client-level job dedupe** — collapse multi-incident jobs per `client_id` (earliest `latest_service_start_s` wins) before planning; kills the double-gel stop and the 2× objective inflation | **S** | efficiency/reporting |
| 5 | **Multi-start construction** — seed local search from EDF and pure-greedy in addition to the incumbent-shaped start; keep best. Fixes both EXP1 structural misses (one unprotected home each) for microseconds of solver time | **S/M** | mission value |

(Next tier: order-churn pricing F7, per-area circuity calibration F2-M, alert message incident-tagging F6a, spread-near-routes F1-fix2.)

---

## Method notes / limitations

- EXP1's brute force is exact only because instances were designed so gel freshness is constant (fires within full-strength horizon) — the objective then decomposes to value − λ·travel, enumerable per crew by subset. The 24 instances cover 2 crews × 5–7 jobs; larger regimes were not brute-forced.
- EXP6 reference drive times are estimates from route knowledge, not measured API times (review env has no routing service); the ±10% ref uncertainty does not change the polarity conclusion (the 1.4× factor being consumed by circuity error is robust to it).
- EXP2 keeps crew positions fixed at base across cycles (isolates optimizer churn from kinematics); a moving-crew replay would additionally exercise commitment under en-route beacons.
- Nothing in this review touched product code; all artifacts live in `scratch_review/da_*` and this file. Full suite re-run after the review: **453 passed**.
