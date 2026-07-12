# Simulator field report — three founder-observed behaviour defects

**Date:** 2026-07-10 · **Scope:** DIAGNOSIS ONLY (product code read-only).
**Harnesses (all read-only, in-process, no server touched):**
`scratch_review/diag_roads.py`, `diag_util.py`, `diag_balance.py`,
`diag_triage.py`, `diag_triage_fix.py`. Ran on the REAL cached tiles
(`data/tiles/*.wgt`) and road graphs (`data/roads/*.wgr`), Python 3.14.5.

**Headline:** the three founder-observed defects are real. Two of them
(utilization concentration, value-not-count triage) are *unimplemented pinned
laws* — `cfg.dispatch_objective='homes'`, the FULL-UTILIZATION law, and
`cfg.road_spur_max_m` were added to `contracts.py` (five-laws block, lines
214–244; config fields 782–784) but **nothing in `dispatch.py` or `simui.py`
reads them yet** (grep: they appear only in `contracts.py`). The roads defect
is a genuine but *location-specific / terminal-approach* straight-line issue,
not a wholesale "crews ignore roads."

---

## DEFECT 1 — "the crews do not stay on real roads"

### Root cause (3 sentences)
Road graphs load and are used for every leg (all leg types route — see below),
but `roads.route()` renders a crew on a **straight line** in two situations:
(a) a **whole-leg chord fallback** — when *either* endpoint is farther than
`SNAP_MAX_M`=2000 m from any graph node it returns the 2-point polyline `[a,b]`
with `on_roads=False` (`roads.py:447-448`), throwing away the road network for
the *entire* leg even when most of it is routable; and (b) a **straight snap
spur** on *every* on-road leg — the sub-legs `a→nearest_node` and
`nearest_node→b` are charged as straight lines (`roads.py:456,468-470`) yet the
whole route is flagged `on_roads=True`, so the visible "last-mile" approach into
every off-network home and safe zone is a straight cut mislabelled as on-road.

### Leg-TYPE audit (suspect b — CLEARED)
Every movement leg consults the roads seam. In `_SimCrew.advance`
(`simui.py:924-994`): travel mode begins an `"ingress"` leg (943-948), egress
mode begins an `"egress"` leg (982-987), and **stop→stop is routed** because a
completed deploy sets `_mode="idle"` and the next iteration re-enters travel and
re-begins an ingress leg from the actual position. `_refresh_egress_plan`
(`simui.py:804-833`) also routes the stop→safe-zone escape. **No leg builder
bypasses `_leg_route`.** "egress→restage" simply does not exist — a crew idles
at its zone after egress (that is the Defect-2 lifecycle matter, not a roads
gap).

### Measured numbers
`diag_roads.py` Part 1/3 — distance from each of 40 placed client homes and
each safe zone to the nearest road node (drives the fallback), and the
home-approach spur census over 80 ingress+egress legs/location:

| location | client dist med / max (m) | clients >2 km (chord) | **whole-leg chords** (of 80) | home-approach spur med / p90 / max (m) | legs w/ >300 m straight approach |
|---|---|---|---|---|---|
| malibu_ca | 61 / 908 | 0/40 | 0 (0.0%) | 61 / 176 / **908** | 4 |
| **paradise_ca** | 107 / **5544** | **4/40 (10%)** | **8 (10.0%)** | 107 / 1108 / **5544** | **20** |
| santa_rosa_ca | 53 / 1398 | 0/40 | 0 (0.0%) | 53 / 215 / 1398 | 6 |
| boulder_co | 42 / 1060 | 0/40 | 0 (0.0%) | 42 / 99 / 1060 | 2 |
| park_city_ut | 79 / 682 | 0/40 | 0 (0.0%) | 79 / 204 / 682 | 4 |

`diag_roads.py` Part 2 — legs the crews *actually* drove (40 clients / 4 crews /
high / 35 ticks, 900 s/tick):
- boulder_co: 8 legs, **0% whole-leg chords**; followed-path samples 2.4% lie
  >75 m off the nearest road vertex (max 224 m); on-road-leg off-road straight
  fraction med 0.6%.
- malibu_ca: 41 legs, **0% whole-leg chords**; 1.8% of samples >75 m off-road
  (max **424 m**); off-road straight fraction med 2.1%, **max 18.6%** (a leg
  whose 908 m snap spur is a fifth of the drive).

**Reading it honestly:** on the two *road-dense* tiles the crews are ~98 % on
road; the visible straight-line behaviour is (1) concentrated on **sparse-road
tiles — paradise_ca has 10 % whole-leg chords and 20 legs with a >300 m straight
approach**, and (2) the **universal terminal spur** into every off-network home
/ zone (up to 908 m malibu, 5.5 km paradise), which today is silently folded
into an `on_roads=True` leg. The `on_roads` flag is a single whole-leg boolean
(`RoadRoute.on_roads`, `roads.py:172-176`; surfaced once per crew as
`route_on_roads`/`egress_on_roads`, `simui.py:1984-1986`) and **cannot express
"road with a straight spur,"** which is exactly the common case.

### Coupling to the FIRE ZONE
Road graphs were fetched on the **standard** bbox (`data/roads/provenance.json`).
On an *extended* session ingress/egress endpoints are unchanged (clients stay
central, zones are the same anchors), so normal legs are unaffected — but a
far-side safety **withdrawal** target (near a tens-of-km ignition) lands outside
graph coverage and hits the same >2000 m whole-leg-chord path. Same mechanism,
just the extended-zone trigger.

### Fix spec
Aligns with pinned **law #2 (ROADS ADHERENCE)**: every leg routes; off-network
endpoints join the graph via explicit **short SPUR** segments flagged
`spur=true`; whole-leg chord is legal *only* when no graph is loaded or the
endpoint is beyond `cfg.road_spur_max_m`.

1. **Two-phase legs with an explicit, capped spur** *(M)* — `roads.py:route`
   (436-471). Instead of the all-or-nothing fallback at 447-448: always snap
   each endpoint to its nearest node; if the snap distance ≤ `cfg.road_spur_max_m`
   emit an explicit spur sub-segment (straight, the only straight part) + the
   A\* road path; if it is beyond the cap, keep the honest chord for *that spur
   only* while still routing the on-network portion. This alone removes
   paradise's 10 % whole-leg chords (the far homes still get roads for the
   routable middle).
2. **Per-segment spur metadata on the polyline** *(M)* — add a `spur_mask`
   (or `segments: list[(on_roads|spur|chord)]`) to `RoadRoute`
   (`roads.py:168-176`); carry it through `_SimCrew._begin_leg`
   (`simui.py:755-774`, which today stores a single `on_roads`) into the state
   serialization (`simui.py:1940-1986`) so the UI can draw the road portion
   solid and the spur dashed, per law #2's `spur=true` flag. The tri-state
   `route_on_roads` becomes per-segment.
3. **Wire the cap to config** *(S)* — replace the hardcoded `SNAP_MAX_M=2000`
   (`roads.py:115`) with `cfg.road_spur_max_m` (already defaults 2000;
   `contracts.py:784`) so the law's knob is live.

**Effort: M** overall. No dispatch coupling (roads times do not feed the planner
— see the coupling section).

---

## DEFECT 2 — "crews are not consistent in their work load. All should be working until the threat is passed"

### Root cause (3 sentences)
`dispatch.plan`'s objective is value collected minus a **near-free** travel term
(`lam_travel = 0.01 × mean stop score` per drive-hour, `dispatch.py:270`) with
**no workload-balancing / crew-spreading term**, so concatenating many stops on
the single cheapest crew *minimises* total travel and wins — the planner
actively prefers concentration. Compounding it, the simulator re-plans **only
idle crews against still-unassigned jobs with `incumbent=None`** every tick
(`simui.py:1618-1636`), so once one crew adopts a long itinerary those clients
are `self._assigned` and the backlog is **never redistributed** to crews that go
idle. The `_CrewSim` lifecycle itself is fine — a crew that egresses returns to
`AVAILABLE` and *is* eligible again — it just starves because there is nothing
left unassigned to pick up.

### Measured numbers
`diag_util.py` (900 s/tick, threat active all 60 ticks, violations 0 throughout):

| scenario | served | **stops per crew** | crews at 0 stops | busiest crew | idle-crew-ticks while jobs unserved |
|---|---|---|---|---|---|
| malibu 40/4 | 13 | g1:0 **g2:11** g3:2 g4:0 | **2/4** | g2 = **100 %** busy | 173 |
| malibu 80/8 | 16 | **g5:13**, five crews:0, g3:1 g8:2 | **5/8** | g5 = **100 %** busy | 410 |
| boulder 40/4 | 2 | g1:1 g2:0 g3:1 g4:0 | 2/4 | 6.7 % | 18 |

`diag_balance.py` — isolated planner probe (8 equal-value, all-feasible jobs in
one cluster, 4 crews at N/E/S/W, generous deadlines): **the planner puts all 8
on a single crew, 3 crews get 0** ("balanced" would be 2 each). Travel
arithmetic confirms the incentive — one crew = 1 inbound leg + 7 ~0 intra-cluster
hops (low total travel); spreading = 4 inbound legs (higher travel), so the
objective *prefers* concentration.

**Lifecycle check (suspect a — CLEARED):** in malibu 40/4, crew g3 returned
`AVAILABLE` after working *and* later picked up new work; the return-to-eligible
path (`_CrewSim.advance` egress→idle→AVAILABLE, then `idle()`
`simui.py:1618-1619`) works. In 80/8 the freed crews (g3, g8) got **no** new
work because everything was already committed to g5. The unserved jobs read
reason `in_progress` (queued behind the busy crew), not `capacity` — which is
why "idle-while-capacity" is 0 but "idle-while-ANY-unserved" is 173–410: the
waste is crews sitting idle while savable homes sit in *another crew's* backlog.

### Fix spec
Aligns with **law #3 (FULL UTILIZATION)**: no crew idles while a feasible pending
job exists; the planner exhausts eligible crews before leaving jobs unserved for
capacity.

1. **Global re-plan each tick over ALL eligible crews** *(M–L, principled fix)* —
   `simui.py` tick dispatch block (1618-1636). Today: `idle_ids` + unassigned
   `jobs` + `incumbent=None`. Change to: crews = all eligible (idle **and**
   busy), jobs = all threatened-and-not-yet-serviced clients (including
   currently-assigned-but-not-frozen), and pass the current itineraries as the
   `incumbent` `DispatchPlan` so near-term committed/in-flight stops freeze
   (`commitment_horizon_min`, already honoured `dispatch.py:1073`) while future
   stops rebalance onto crews that would otherwise idle. Requires reconstructing
   an incumbent `DispatchPlan` from the live `_SimCrew` itineraries (the sim
   currently keeps `_plan_snapshot` as a dict, `simui.py:1744-1751`).
2. **Anti-concentration term in the objective** *(M)* — `dispatch.py` `_Ctx`
   (add to `route_contrib`/`plan_j`, 373-405). Add a mild convex penalty on
   per-crew stop count (or a per-additional-stop-on-the-same-crew cost) so, at
   equal homes/value, spreading wins. Keep it strictly below the safety/deadline
   terms. This fixes the planner even for a single plan() call (the
   `diag_balance` 8-on-one case).

Both together satisfy law #3. Note the natural merge with Defect 3 below (one
coherent "homes, spread across crews" objective).

---

## DEFECT 3 — "dispatch to protect as many homes as possible — may require abandoning homes too close to the fire front to protect homes farther away"

### Root cause (3 sentences)
The objective is **value-weighted, not count-maximising**: every stop
contributes `priority_weight × value_usd × gel_effectiveness × gel_fresh_frac`
(`_potential_score`, `dispatch.py:162-164`; summed in `route_contrib` 385-393),
and the exact micro-solver maximises that J with only a *near-tie* count
tiebreak (`dispatch.py:861`). So a single high-value home beats two lower-value
homes. `cfg.dispatch_objective` (default `'homes'`, `contracts.py:782`) is
**never read** anywhere in `dispatch.py`, and there is **no objective-side
preemption for triage and no distinct unserved reason** — a triaged home is
reported `capacity`/`dropped_by_optimizer`, indistinguishable from a plain miss.

### Measured numbers
`diag_triage.py` — a deterministic, **mutually-exclusive** dilemma (1 crew;
home A $5 M feasible-now near the crew; homes B+C $0.7 M each far in the opposite
direction; travel arithmetic *proves* `{A}` XOR `{B,C}` are the only maximal
feasible sets):

| case | served | homes | value collected |
|---|---|---|---|
| current default (`dispatch_objective='homes'`) | **{A}** | **1** | $4.25 M |
| explicit `'value'` | {A} | 1 | $4.25 M |
| **preemption** (incumbent already serving B+C, A appears) | **{A}** | **1** | drops B **and** C, reason `dropped_by_optimizer` |

The default that is *nominally* `'homes'` protects **1** home, not 2 — and in
the preemption case it **actively abandons two already-committed savable homes**
to take one higher-value home, with no `triaged` signal.

**Fix-direction validated** — `diag_triage_fix.py` monkeypatches
`_potential_score` to a count-first form (equal large per-home credit, dollars
as tiebreak) and the same dilemma **flips to `{B,C}` = 2 homes**. The fix lands
in the objective.

### Fix spec
Aligns with **law #4 (TRIAGE)**: `dispatch_objective='homes'` maximises COUNT
(value only as tiebreak) and may skip/abandon a planned-not-started job whose
service costs strictly more savable homes elsewhere, unserved reason `'triaged'`;
never weakens SAFETY SEMANTICS 2/3/7.

1. **Count-first objective under the flag** *(M)* — `dispatch.py`. When
   `cfg.dispatch_objective=='homes'`, make the served-stop credit a large equal
   constant with `value×gel_fresh_frac` as a small lexicographic tiebreak.
   **Critical implementation notes:** apply it in the ONE place
   (`_potential_score`, 162-164) that feeds the objective, greedy-EDF,
   `_state_value` guardrail (585-594) and `mean_score` — but derive
   `lam_travel`/`churn_unit` (270-271) from the *value* component only, not the
   count constant, or they balloon to ~K and over-penalise travel/churn. Prefer
   an explicit lexicographic (primary = Σ served, secondary = Σ value·gel) rather
   than a single inflated scalar. Keep `'value'` byte-identical to today
   (legacy).
2. **`'triaged'` unserved reason + objective-side preemption** *(M)* —
   `dispatch.py` `diagnose()` (409-441) and the reason vocabulary: when a job is
   feasible-for-someone but deliberately dropped because serving it would cost
   ≥1 net home, report `'triaged'` (distinct from `capacity`/
   `dropped_by_optimizer`/`no_safe_*`). Under `'homes'`, allow the optimizer to
   preempt a planned-not-started (non-frozen) doomed stop when doing so frees
   capacity to save *more* homes — an OBJECTIVE-side abandon that leaves the
   SAFETY withdrawal law (`simui._recheck_deploy`, road-egress guard) and its
   `withdrawn_unsafe` vocabulary completely untouched.
3. **Surface it in the sim** *(S)* — map the new `'triaged'` reason through
   `simui`'s unserved rollup (`_unserved_reason`, state() 2006-2018) so the
   scoreboard distinguishes *abandoned-for-triage* from *capacity*.

---

## Coupling between the fixes

- **Defects 2 and 3 are the same objective.** Both live in `dispatch.py`'s
  objective and are best solved as one coherent rule: *maximise homes protected,
  spread across crews.* A count-first objective (Defect 3) with an
  anti-concentration term (Defect 2) is a single edit to `_Ctx.route_contrib` /
  `_potential_score`; implementing them separately risks them fighting (e.g. a
  pure count objective still concentrates count on one crew, exactly the
  `diag_balance` failure). Do them together.
- **Roads times do NOT feed dispatch today** (by design: `dispatch.plan` uses the
  tier-0 `travel.travel_time_s`; roads only move the crew visually and the tick
  re-plans from actual positions — `contracts.py:203-207`,
  `simui.py` docstring ROADS block). So the **Defect 1 fix is independent** of
  2/3. *Latent* coupling to watch: if the Defect-2 fix switches to a
  global-per-tick re-plan from actual (road-moved) crew positions, dispatch sees
  those positions through the straight-line tier-0 model — the intended
  self-correcting divergence, but worth a regression pin. And should road travel
  times ever be fed into the planner, the triage feasibility frontier (which
  homes are reachable) and the concentration pattern both shift, so land Defects
  2/3 on the tier-0 model first.
- **Safety is never touched.** All three fixes are objective/rendering-side. The
  per-tick SAFETY AUDIT (`simui._run_audit`), the road-egress departure guard,
  and the withdrawal law (`withdrawn_unsafe`) keep their own vocabulary; law #4
  explicitly forbids the triage abandon from weakening SEMANTICS 2/3/7. Every
  measured run above held `violations=0`.

## Pinned-law alignment summary

| law (contracts 214-244) | status today | fix |
|---|---|---|
| #2 ROADS ADHERENCE (spur=true, cap = `road_spur_max_m`) | spur straight but unflagged; whole-leg chord >2 km; cap hardcoded | Defect 1 fix 1–3 |
| #3 FULL UTILIZATION | violated — 2–5 crews idle at 0 % while one saturates | Defect 2 fix 1–2 |
| #4 TRIAGE (`dispatch_objective='homes'`, `'triaged'`) | flag ignored; value-max serves 1 over 2; no `triaged` reason | Defect 3 fix 1–3 |
| #1 TIME-SCRUB / #5 GEL RING | not in scope of the three defects; `history_ticks`/`state_at`/`gel_applied` also unimplemented (config-only) | (separate) |
