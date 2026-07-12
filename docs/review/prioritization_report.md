# Dispatch prioritization review — are the *right* homes protected?

**Date:** 2026-07-10 · **Scope:** DIAGNOSIS ONLY (product code read-only; 1047
tests green before and after). **Trigger:** founder ran Malibu (48 clients, **3
crews**, central extreme fire) and felt the wrong homes were protected.

**Harnesses (all read-only, in-process, no server touched, `py -3` from repo
root):** `scratch_review/diag_prio_common.py`, `diag_prio_urgency.py`,
`diag_prio_malibu.py`, `diag_prio_inversions.py`, `diag_prio_decisions.py`,
`diag_prio_timeline.py`, `diag_prio_compression.py`, `diag_prio_fix_sim.py`.
The founder's session is **exactly reproducible**: the server's default seed for
the 7th session is its id (`server.py:258`, `seed = payload.seed or sid`), so
`seed='sim-7'` on Malibu/48/3 reproduces the founder's client set byte-for-byte
(client values match the founder's notes: c6 $1.12M, c47 $2.35M, c44 $1.41M,
c42 $1.89M). Roads load (13 440-node graph), matching the live path. Ignition is
placed at the client-community centroid `(34.0796, -118.6803)`; the phenomenon is
robust to the exact point.

---

## 0. Verdict in six lines

1. **The count-max objective is NOT dropping savable homes for capacity or
   value.** Across every regime (48/3 extreme→moderate, 80/3, 120/2) and all 246
   `dispatch.plan()` calls in sim-7, `capacity`/`triaged` drops = **0** and
   within-plan p50 inversions = **0**. The fire's eroded-P10 corridors prune the
   feasible pool *below* crew capacity faster than crews saturate, so the planner
   never faces a "which feasible home do I drop" choice. TEST2 proves the value
   tiebreak *would* invert under genuine exclusion, but that regime never occurs.
2. **There IS a real, manifest prioritization defect — in the ROUTE/QUEUE ORDER,
   not the selection set.** The objective (`plan_j`) has **no urgency term**:
   in homes-mode every served home is worth ~1 count point, freshness-decoupled,
   so a home 3 h from burning and a home 69 h from burning score identically.
   Nothing makes a crew do the sooner-to-burn savable home first.
3. **Consequence:** crews spend the opening hours gelling *comfortable* far homes
   (measured p50 leads **40–69 h**) while at-risk homes still have open corridors;
   by the time crews free up the fire has closed those corridors, and the at-risk
   homes are lost to `no_safe_corridor` / `infeasible_deadline`. In sim-7 **all
   15 gelled homes are gelled inside the first 6.3 h**, 8 of them 40–69 h before
   their own median fire arrival.
4. **The counterfactual settles it.** Re-running the *identical* engine with one
   change — a p50-urgency tiebreak in the objective, safety gate untouched —
   turns **15 saved into 20** (48/3 extreme), rescuing exactly the high-value
   at-risk homes stock lost (c22 $2.06M, c42 $1.89M, c43 $2.23M, plus c44, c30),
   **violations = 0, min-margin unchanged, deterministic**, a strict superset of
   the stock saves. So 5 of 6 stock losses were **prioritization-caused, not
   physics**; only 1 (c4) is irreducibly unsavable.
5. **P10 compression is real but rank-preserving.** Median (p50−p10) spread =
   **15.2 h** (deadlines ~15 h tighter than the honest median), but Spearman
   rank corr(p10,p50) = **0.92**; ranking on p50 changes ~**19 %** of top-3
   crew-slot priorities. The "many homes p10≈0" premise is only weakly borne out
   (0.3 homes/tick). Compression *tightens deadlines and blocks corridors* — it
   does not scramble the urgency order — so the fix is worth it but the win comes
   from having *any* urgency term, more than from p50-vs-p10 per se.
6. **Today a lost home just shows `impacted`/ungelled with no reason.** Spec below
   adds a per-client outcome tag (saved | protected-then-impacted |
   abandoned-unsavable | triaged | not-threatened).

---

## 1. How urgency does / does not enter dispatch today

Read `dispatch.py`: the objective is
`J = Σ_served _served_value − λ_travel·drive_h − churn − spread` (`route_contrib`
459-492, `plan_j` 494-501). In homes-mode `_served_value` (207-223) is
`priority·(1 + ε·valuefrac·gel_frac)` — the **count credit (priority·1) is fixed
and is NOT scaled by freshness or lead time**. `_potential_score` (185-204) is
the same shape. **Neither reads `eta_p50`/`eta_p10` at all.** So:

- Two savable homes contribute the *same* J whether one burns in 3 h or 30 h.
- When a crew can take only one of two equal-count homes, the tie is broken by
  **value** (the ε term), never urgency.

Urgency reaches the plan only through three *side doors*, none of them the
objective:

| path | urgency signal | limitation |
|---|---|---|
| feasibility chain (`_depart_fail`, corridor/egress T*) | drops a home once its p10 deadline/corridor fails | binary; keyed off compressed **p10**; enforces order only when the late home is *infeasible* |
| greedy-EDF seed (`_edf_insert` 677-720) | appends by ascending `latest_service_start_s` (= p10 − buffers − deploy) | only **one of three** construction seeds; the winner is chosen by `plan_j`, which is urgency-blind, so EDF's order is **not preserved** through seed-argmax / 2-opt / late-shift |
| regret-2 3rd tiebreak (`_regret2_insert` 636) | `latest_service_start_s` after regret and score | a construction tiebreak, again overridden by the objective |

**Net:** the pipeline *selects the final plan by an urgency-blind objective*, so
whatever urgency the EDF seed carries is discarded whenever a count-equal plan
scores as well or better. That is why adding a term to the *objective* (not just
the seed) changes behaviour (§5).

### Constructed proof the objective is urgency-blind (`diag_prio_urgency.py`)

Trivially-safe corridors, so only deadlines + objective bind. `U`=urgent (fire
soon), `C`=comfortable (fire late).

| test | setup | homes-mode result |
|---|---|---|
| TEST1 | 1 crew, both serveable, both visit-orders feasible; U soon / C late | serves **both, U first** — CORRECT, but *forced by feasibility* (C-first would miss U's deadline), not chosen by the objective |
| **TEST2** | 1 crew, **genuine mutual exclusion**, U(soon, **$1.0M**) XOR C(late, **$1.4M**) | protects **C (the less-urgent, pricier home)**, strands U (burns +3 h), reported `capacity` — **INVERSION** |
| TEST3 | same exclusion, **equal $1.2M** | protects **U** — with the $-tiebreak neutralised, urgency wins |

TEST2 is the founder's c47/c44 shape (protect the comfortable pricier home, drop
the urgent cheaper one). It is a **latent** bug: it needs the simultaneously-
feasible pool to exceed crew capacity, which §2 shows never happens in the sim.

---

## 2. (a) Savable-lost-to-worse-priority vs correct triage

**Ground-truth instrumentation** (`diag_prio_decisions.py`, `diag_prio_timeline.py`)
wrapped `dispatch.plan()` and recorded every call's job pool (with p10/p50/value),
served set, and unserved reasons — the real engine, no proxy.

**Within a single plan there are zero mis-prioritizations:**

- `capacity`/`triaged`/`dropped_by_optimizer` drops across 246 calls: **0**.
- Calls that served a home M while leaving a *more-urgent* (earlier-p50) savable
  home L: **0**.
- Same result at 48/3 high & moderate, 80/3 moderate, 120/2 moderate (all
  `cap_triage_drops=0`, `p50_inversions=0`). At 120 clients / 2 crews the pool
  peaks at 29 jobs and **all 29** are queued/served — capacity never binds.

**Why capacity never binds:** the dispatch feed only offers homes inside the
72 h notice window whose eroded-P10 corridor is open (`simui.py:1767-1789`); the
fast fire closes corridors and expires p10 deadlines faster than 2–3 crews
saturate. Unserved is therefore **always** an infeasibility, never a capacity
choice. `scoreboard.unserved_by_reason` in stock sim-7 = `{infeasible_deadline:5,
no_safe_corridor:1}`.

**But the losses are still prioritization-caused — shown by the counterfactual.**
Of the 6 ungelled-threatened homes in stock sim-7:

| home | value | stock outcome | rescued by urgency-first reorder? |
|---|---|---|---|
| c22 | $2.06M | assigned then dropped → `no_safe_corridor` → `infeasible_deadline` | **YES** |
| c42 | $1.89M | corridor blocked ~whole run (p50 lead +70 h, p10 blocked) | **YES** |
| c43 | $2.23M | assigned then dropped as corridor closed | **YES** |
| c44 | $1.41M | never feasible in stock timing | **YES** |
| c30 | — | never feasible in stock timing | **YES** |
| c4  | — | never feasible | **no (irreducible)** |

So the honest count for the founder's scenario:

> **≈5 savable homes lost to prioritization** (crews consumed the corridor-open
> window on comfortable far homes) **vs 1 genuinely-unsavable** correct-triage
> loss.

"Never feasible in the stock run" turned out **not** to mean "unsavable" — c44
and c30 look infeasible only because, under stock ordering, no crew was pointed
at them while their corridor was still open. Reordering makes them feasible. The
only true c6-style loss is c4 (fire arrives before any crew can service+egress
from any position, regardless of order).

**Comfortable-first is measurable** (`diag_prio_timeline.py` Q2, gel completion
times): all 15 stock gels finish inside +6.3 h; leads at gel include c8 **+69 h**,
c19 +65 h, c17 +62 h, c40 +51 h, c23 +49 h, c12 +45 h, c39 +41 h. Because
homes-mode counts these as "protected" regardless of freshness, a home gelled
69 h early (gel ~6 % fresh at arrival) is booked as a save while its crew-cycle
is spent — and an at-risk home's corridor closes in the meantime.

---

## 3. (b) Does urgency ordering exist, and the 1-crew/2-home result

**As a designed property: no.** The objective has no urgency term (§1). Ordering
emerges only from the feasibility gate and the EDF seed, and the seed's order is
overwritten by the urgency-blind `plan_j` argmax.

**1-crew / 2-home result:**
- Both-serveable (TEST1): the crew **does the urgent home first** — correct, but
  because serving it second is *infeasible*, not because the engine values urgency.
- Cannot-do-both, urgency ≠ value (TEST2): the engine **takes the less-urgent
  pricier home** and strands the urgent one. Equalise value (TEST3) and it takes
  the urgent one. Urgency ordering is thus an accident of the value tiebreak.

---

## 4. (c) p50 vs p10 ranking (`diag_prio_compression.py`)

Everything urgency-relevant the planner uses today keys off **p10**
(`latest_service_start_s`, EDF order, the late-shift cap). Measured over sim-7:

| metric | value | reading |
|---|---|---|
| median (p50 − p10) spread | **15.2 h** | the p10 deadline is ~15 h earlier than the honest median arrival — this is what blocks corridors and caps late-shift early |
| Spearman rank corr(p10, p50) | **0.92** | the *order* is largely preserved; compression shifts times, not ranks |
| top-3 (=crews) protect set differing p10 vs p50 | **19 % of slots** | ~1 in 5 priorities would change under honest p50 ranking |
| homes with p10 ≤ +2 h while p50 ≥ +10 h | 0.3 / tick avg | the "many homes p10≈0" premise is only weakly present here |

**Implication for the fix:** rank on **p50** (honest median urgency) for the
*value/urgency* ordering, and keep **p10** for the *safety* gate — exactly the
task's split. The larger lever, though, is that the objective has *no* urgency
term at all; adding one (on p50) is what recovers the 5 homes, not the 0.92→1.0
ranking refinement alone.

---

## 5. (d) Fix spec

Two independent changes; the first is the high-value one and is **validated
end-to-end** below.

### 5.1 Urgency-aware objective (protect the sooner-to-burn savable home first)

**Principle:** keep homes-mode count-max and the never-worse-than-greedy COUNT
guardrail; keep the P10 safety gate byte-identical. Replace the value-only
tiebreak with an **urgency-primary, value-secondary** tiebreak, both far below
one home:

```
homes-mode score(job) = priority · (1 + w_urg·urgency + w_val·valuefrac)
  urgency   = 1 − clamp((eta_p50_s − now_s) / (notice_h·3600), 0, 1)   # sooner ⇒ larger
  w_urg ≈ 1e-2   (dominates)      w_val ≈ 1e-4   (sub-tiebreak)
```

**Files / functions (`wgresponse/dispatch.py`):**

- `_potential_score` (185-204) and `_served_value` (207-223): add the `urgency`
  term in the `dispatch_objective=='homes'` branch. Both need `now_s`; thread it
  in (they are only ever called from within `plan()`, where `ctx.now_s` exists).
  Call sites to pass `now_s` through: `_Ctx.__init__` mean_score (327),
  `route_contrib` (473), `_regret2_insert` (631), `_try_drop_refill` (797/803/806),
  `_exact_small_solve` (950), and the emit loop (1372). Simplest: compute a
  per-job `urgency` once in `_Ctx.__init__` (it has `now_s` and the jobmap),
  cache it on a dict keyed by job key, and have the two scoring fns read it.
- `_Ctx.__init__` (326-352): keep `lam_travel`/`churn_unit`/`spread_unit` derived
  from the *value component only* (they already are). Fold `w_urg·urgency` into
  the same bounded "tiebreak" bucket so the derived travel/churn units stay small
  (both `w_urg·urgency` and `w_val·valuefrac` are ≤ ~1e-2, so `mean_score`
  stays ≈ priority and the units do not balloon — the exact concern the existing
  homes-mode comment guards against).
- **Untouched (safety):** `safety.latest_departure_s/latest_service_start_s`,
  `_depart_fail` (140-162), `LegOracle`/corridor T*, `egress`, and the
  `latest_service_start_s` used by EDF and the guardrail. Deadlines and corridors
  stay pure p10. `'value'` mode stays byte-identical (legacy).

**Effort: S–M** (one scoring concept, threaded through ~7 call sites; re-baseline
the byte-identity + optimality-gap pins in both modes).

**Measured effect (`diag_prio_fix_sim.py`, monkeypatched — no product change):**

| scenario | stock served | urgency-aware | violations | notes |
|---|---|---|---|---|
| Malibu 48/3 **extreme** (sim-7) | **15** | **20** | 0 → 0 | rescues c22/c42/c43/c44/c30; strict superset; min-margin 2.26→2.31 h |
| Malibu 80/3 moderate | 21 | 23 | 0 | |
| Malibu 120/2 moderate | 29 | 31 | 0 | |

Robust to `w_urg ∈ {5e-3, 1e-2, 2e-2}` (all give 20 on sim-7); deterministic.
The mechanism: prioritising sooner-p50 homes makes crews reach at-risk homes
**while their corridor is still open**, converting `no_safe_corridor` /
`infeasible_deadline` losses into saves; comfortable homes (which have slack) are
still served, just later — hence the strict superset.

### 5.2 Per-client outcome tag ("why not protected")

**Files (`wgresponse/simui.py`):**

- `state()` clients loop (2184-2201): add `"outcome"` per client, derived from
  existing state (`self._serviced` / `gel_at`, `self._client_etas` for the
  impacted check `p50 ≤ now`, and `self._unserved_reason`):

  | outcome | rule |
  |---|---|
  | `not-threatened` | never jobbed / eta always UNREACHED |
  | `saved` | `gel_applied` and not impacted |
  | `protected-then-impacted` | `gel_applied` and fire reached it (p50 ≤ now) |
  | `abandoned-unsavable` | ungelled, threatened, terminal reason ∈ {`no_safe_corridor`, `no_safe_egress`, `infeasible_deadline`, `infeasible_slack`, `gel_window_closed`, `fire_arrived`} |
  | `triaged` | ungelled, threatened, terminal reason ∈ {`capacity`, `triaged`, `dropped_by_optimizer`} (feasible-for-someone but deprioritised) |

- `scoreboard` (2304-2313): add `outcome_counts` rollup for the UI.
- The engine already carries the reason vocabulary (`diagnose()` emits `triaged`
  in homes-mode) and `_serviced`; this is pure rendering. **Effort: S.**

Note: in sim-7 the `triaged` bucket is ~0 today (capacity never binds, §2), but
the tag makes the distinction visible the moment §5.1 or a higher-capacity fleet
puts homes into it — and immediately distinguishes the founder's burned homes
(`abandoned-unsavable`, corridor closed) from any true triage.

---

## 6. Honest limits

- The exact founder ignition point is unrecoverable; the client *set* is exact
  (seed), the ignition is a representative central-extreme point. Loss counts
  shift ±a few with the point; the *mechanism* and the fix's direction/robustness
  do not (confirmed across 5 regimes).
- The urgency fix was validated by monkeypatch, not a product edit; re-baselining
  the byte-identity and 24-instance optimality-gap harnesses in both modes is
  required before ship (the guardrail guarantees served-count ≥ greedy, so the
  downside is bounded).
- Gel-effectiveness / window remain unverified supplier placeholders (SAFETY
  SEMANTICS 5); "false save" (§2, gel applied 69 h early) inherits that caveat and
  is a second reason §5.1's deferral-by-urgency is the right policy.

## 7. Re-run

```
py -3 scratch_review/diag_prio_urgency.py       # TEST1/2/3 objective urgency-blindness
py -3 scratch_review/diag_prio_malibu.py        # reproduce sim-7 census
py -3 scratch_review/diag_prio_decisions.py     # 0 within-plan capacity/p50 inversions
py -3 scratch_review/diag_prio_timeline.py      # utilization + gel-order + why-lost
py -3 scratch_review/diag_prio_compression.py   # p50-vs-p10 ranking
py -3 scratch_review/diag_prio_fix_sim.py       # STOCK vs urgency-aware (15 -> 20, viol 0)
```
All deterministic; product code untouched (1047 tests green).
