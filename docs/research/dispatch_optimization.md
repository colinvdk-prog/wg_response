# WG Response — Dispatch Optimization Algorithm Spec

**Status:** research digest + concrete algorithm spec (v0.1, 2026-07-06)
**Scope:** crew-to-property dispatch/routing for GraphGel deployment under fire-arrival deadlines.
**Author:** recon subagent (Claude), for the WG Response project.

---

## 1. Problem statement (formalized)

- **N crews**, live GPS positions `p_i(t)` (phone GPS, driving), each with status `idle | enroute | on_site | returning | off_shift`.
- **M client properties**, locations `x_j`, insured/contract value `V_j`, priority weight `w_j`, and a
  **projected fire arrival time** `ETA_j` (hours from now) produced by the propagation forecaster,
  refreshed every ~15 min, with growing uncertainty at longer lead times.
- **Service**: applying GraphGel takes `s ≈ 30 min` on site (config `service_minutes`, default 30).
- **Hard safety rule**: a crew must *depart* property j at least `m_safe = 30 min` before `ETA_j`
  (config `safety_margin_minutes`, default 30, applied against a *conservative* arrival quantile — see §5),
  **and** the egress route must be verified safe (§7).
- **Gel effectiveness window** `W_gel` once applied — **config `gel_effective_hours`, default 72 h,
  ⚠ UNVERIFIED** (no GraphGel effectiveness-window spec exists on disk; the only documented GraphGel
  data is E84 Class A + the ABC7 burn demo — the window MUST be confirmed with the
  supplier/formulator before this default is trusted). Deploying earlier than `ETA_j − W_gel` is wasted;
  deploying as late as safely possible is preferred, risk-adjusted.
- Crews serve **multiple properties sequentially** → this is routing, not assignment.
- Forecast updates every ~15 min → **continuous re-optimization with plan stability** (no thrash).

### 1.1 Problem class (confirmed by literature)

This is a **Team Orienteering Problem with Time Windows (TOPTW)** — more precisely a *deadline*
TOPTW (windows are `[release_j, deadline_j]` where the deadline is safety-derived and the release
is freshness-derived), dynamic/rolling-horizon variant:

- Select a **subset** of properties (not all can be saved — that is the orienteering/"prize-collecting"
  part), build one route per crew, maximize collected "profit," respect windows. TOPTW is NP-hard;
  the workhorse practical heuristic is **Iterated Local Search (ILS)** with a cheap insertion step and
  a "shake" perturbation (Vansteenwegen et al.; confirmed as the standard efficient method in the
  TOPTW literature — see Sources). Cluster-based ILS extensions exist for geographically clumped
  prizes (relevant: WUI subdivisions are clumps).
- **Regret-2 insertion** (Potvin & Rousseau 1993; popularized by Ropke & Pisinger 2006 in ALNS):
  for each unrouted property compute best and second-best insertion cost across all routes; insert
  the property with the **largest regret** (gap between best and 2nd-best) at its best position first.
  This look-ahead avoids the classic greedy failure where a property that only fits in one place loses
  its slot. Confirmed standard practice for VRPTW/DARP construction.
- **Dynamic re-planning stability**: the literature pattern is to split stops into **committed** (frozen,
  cannot be reassigned) and **flexible** orders, re-optimize on a rolling horizon, and penalize
  deviation from the incumbent plan (consistent-VRP-style penalties). Event-driven re-planning on
  top of a fixed cadence is the recommended stability approach. Confirmed (see Sources).
- Alternative solver backend: **Google OR-Tools routing solver** with `AddDisjunction(node, penalty)`
  models exactly "serve or drop at a price = property score," plus time-dimension windows. Viable
  drop-in for the custom heuristic below at N·M ≲ a few thousand with a 5–10 s time limit.
  (⚠ well-established but not re-verified this session.)

---

## 2. Objective function

Maximize, over plans P (a set of routes, one per crew, each an ordered stop list with planned times):

```
J(P) =  Σ_{j ∈ served(P)}  w_j · V_j · P̂save(j, t_complete_j)          (value term)
      − λ_travel · Σ_i drive_time_i                                     (efficiency, tie-break)
      − λ_risk   · Σ_i RouteRiskExposure_i                              (price margin proximity)
      − λ_stab   · Σ_j churn_j                                          (plan-stability penalty)
```

with:

- **`P̂save(j, t_c)`** — expected save probability given gel completed at `t_c`:

  ```
  P̂save = P_hit(j) · E_gel · freshness(ETA_j − t_c) · P_meet(j)
  ```
  - `P_hit(j)` = forecast probability the fire actually reaches j while gel is active
    (from the propagation ensemble; if only a deterministic forecast exists, use a calibrated
    proxy: 0.9 within 12 h lead decaying to ~0.4 at 72 h lead — config table `p_hit_by_lead`).
  - `E_gel` = gel effectiveness given fresh application — config `gel_effectiveness`,
    default 0.85. **⚠ UNVERIFIED placeholder; no measured save-rate exists.**
  - `freshness(Δt)`: 1.0 for `Δt ≤ F_full` (config `gel_full_strength_hours`, default 24 h — ⚠ assumed),
    linear decay to 0 at `Δt = W_gel`. Negative Δt (fire arrives before completion) is infeasible anyway.
    This is what makes "as late as safely possible" optimal.
  - `P_meet(j)` = probability the crew actually completes before the departure deadline given
    travel-time variance; with the deterministic estimator use
    `P_meet = Φ((deadline_j − planned_depart_j) / σ_travel)` or simply require planned slack ≥
    `slack_min` (config, default 10 min) and set `P_meet = 1` — start simple.
- **`RouteRiskExposure_i`** — sum over route legs of a closeness-to-fire measure (§7.3); the hard
  constraint already excludes unsafe routes, this term prefers *comfortably* safe ones.
- **`churn_j`** = 1 if property j's assigned crew or its position class changed vs the incumbent
  plan (× 2 if the crew was already enroute to it). `λ_stab` default = 5 % of the mean property
  score — see §8.
- `λ_travel` small (tie-breaker only — fuel/fatigue), e.g. value-units per hour equal to 1 % of the
  median property score per drive-hour.

**Units**: express `V_j` in dollars or normalized [0,1] "protection points"; keep λ's in the same unit.
Recommended: normalize `w_j·V_j` to [0,100] per property so penalty tuning is portfolio-independent.

---

## 3. Constraints

Hard (violating any ⇒ stop is infeasible / dropped):

| # | Constraint | Formula |
|---|-----------|---------|
| C1 | Departure safety | `depart_j = t_c(j) ≤ Q_j − m_safe` where `Q_j` = conservative arrival quantile (§5) |
| C2 | Service duration | `t_c(j) = arrive_j + wait_j + s` |
| C3 | Gel window (release) | `t_c(j) ≥ ETA_j − W_gel` (else the deploy is worthless ⇒ treat as release time; waiting allowed but reordering preferred) |
| C4 | Ingress safety | route leg → j passes the corridor check (§7) at pass-through times |
| C5 | Egress safety | a verified safe route exists from j to the designated safe zone at `depart_j`, margin `m_route` |
| C6 | Crew shift | route ends (crew at safe zone / base) before `shift_end_i` (config, default 12 h) |
| C7 | Commitment | frozen stops (§8) keep crew, order, and planned times (± small slack) |

Soft (in the objective): freshness, route-risk exposure, churn, drive time.

**Effective time window per property:** `[ max(now, ETA_j − W_gel),  Q_j − m_safe − s ]` on *service start*.
If the window is empty ⇒ the property is **lost to dispatch** (alerting subsystem handles notification;
the optimizer must emit an explicit `unservable` list with reasons — this feeds the 24 h EXTREME alert logic).

---

## 4. Travel-time estimation

### 4.1 Tier 0 — no routing API (launch default)

```
τ(a,b) = haversine_km(a,b) · circuity / speed_kmh(road_class_context) · fire_traffic_factor
```

- **Circuity factor**: config `circuity_factor`, default **1.35**. Literature: measured network/geodesic
  ratio is **1.3 ± 0.1** for road networks (US metro average ≈ 1.3). WUI/mountain terrain (WG's actual
  service area) is worse — canyon roads commonly hit 1.5–2.0. **Recommendation:** default 1.35
  globally, but calibrate per service area by sampling ~200 OSRM routes offline and fitting the local
  ratio (one-time script, no runtime dependency). ⚠ per-area value unverified until calibrated.
- **Speed by road class** (config map `speed_kmh_by_class`, design defaults; the OSM wiki + the
  `osm-legal-default-speeds` project is the standard source for gap-filling):

  | class | default km/h |
  |---|---|
  | motorway | 100 |
  | trunk | 90 |
  | primary | 80 |
  | secondary | 65 |
  | tertiary | 50 |
  | residential / unclassified | 40 |
  | service / track (driveways, fire roads) | 20 |

  Tier 0 has no per-leg road class, so use a **context speed**: legs > 15 km assume mixed
  primary (config `long_leg_speed`, default 70 km/h); legs ≤ 15 km assume 45 km/h
  (`short_leg_speed`). Crude but monotone and cheap.
- **`fire_traffic_factor`**: multiplier ≥ 1 for smoke/evacuation congestion near active fire, config
  default **1.4** applied to any leg whose corridor comes within `congestion_radius_km` (default 15 km)
  of the current fire perimeter. ⚠ heuristic, no source — tune from after-action GPS traces.
- Add fixed per-stop overhead `access_overhead_min` (gate codes, driveway, hose-out), default 5 min.

### 4.2 Tier 1 — routing engine (upgrade path)

- **OSRM** (fastest matrices, ~5–10 ms/route):
  - Route: `GET https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson&annotations=duration`
  - Matrix: `GET https://router.project-osrm.org/table/v1/driving/{lon,lat;...}?annotations=duration&sources=...&destinations=...`
  - **Public demo server policy (verified):** ≤ 1 req/s, non-commercial / reasonable use, no uptime
    guarantee, table capped at **10,000 durations per request**, access withdrawable at any time.
    **WG is a commercial product ⇒ the demo server is acceptable for development only; production
    MUST self-host** (`osrm-backend` Docker + a state extract from Geofabrik, e.g.
    `california-latest.osm.pbf`; `osrm-extract`/`osrm-contract` with the `car.lua` profile, serve on :5000
    — same API paths, no limits). A 220×220 matrix (20 crews + 200 properties) = 48,400 durations
    → exceeds the demo cap in one call anyway; self-hosted has no such cap.
- **Valhalla** (verified: open source, Docker-distributed, matrix + isochrone + route + elevation):
  - `POST http://<host>:8002/route`, `POST /sources_to_targets` (matrix), `POST /isochrone`.
  - Advantages over OSRM for WG: **dynamic runtime costing** (no recontraction needed — important
    for road closures during incidents: pass `exclude_locations` / `exclude_polygons` with the fire
    polygon to get fire-avoiding routes natively) and elevation awareness. ~10–30 ms/route,
    thousands of qps. Docker image: official images are published from the `valhalla/valhalla` GitHub
    org (⚠ exact image tag/name not verified this session — historically the community
    `gis-ops/docker-valhalla` image, since moved under the valhalla org; confirm current name at
    https://github.com/valhalla/valhalla and valhalla.github.io/valhalla docs before pinning).
  - **Recommendation:** Tier 1 = self-hosted **Valhalla** precisely because `exclude_polygons`
    lets the safe-access check (§7) be pushed into the router; keep OSRM as the low-latency matrix
    fallback if Valhalla matrix throughput disappoints.
- **Caching:** travel times change slowly; cache the full crew×property + property×property matrix,
  refresh changed rows only (crews move; properties don't). Recompute a crew's row when it moves
  > 1 km from the position used for its cached row.

---

## 5. Handling forecast uncertainty (safety side)

- The forecaster should emit, per refresh, a **fire arrival-time raster** `A(cell)` (minutes from now
  until the fire front reaches each cell; ∞/NoData if unreached within horizon). Preferably two:
  `A_med` (median) and `A_q10` (10th-percentile / pessimistic-early). If only deterministic `A` exists,
  synthesize `A_q10 = A · (1 − u(lead))` with `u` = 0.15 at 6 h growing to 0.35 at 48 h
  (config `eta_uncertainty_by_lead`; ⚠ placeholder curve — calibrate against forecast verification).
- **All hard safety math uses `Q_j = A_q10(cell(x_j))`** (pessimistic). All *value* math (freshness,
  P_hit) uses the median `ETA_j`. This cleanly implements "deadline conservative, reward realistic."
- This mirrors the wildfire evacuation **trigger-point / trigger-buffer** literature (PERIL model:
  fire-spread travel-time network + shortest path + explicit safety factor) — same construct, applied
  to crews instead of residents (see Sources).

---

## 6. Algorithm

### 6.1 Architecture: two tiers

- **Strategic tier (horizon = `W_gel`, runs each forecast cycle, cheap):** filter the portfolio to the
  **active set** — properties with a non-empty effective window inside the next
  `planning_horizon_hours` (default 24 h; properties with `ETA_j − W_gel > now + 24 h` are deferred —
  deploying now would waste freshness). Emits: active set, unservable list, staffing-gap estimate
  (demand-hours vs crew-hours — feeds the 72 h alert/callout decision).
- **Tactical tier (the TOPTW solver below, ≤ 24 h horizon, budget ≤ 5 s wall-clock):** builds actual
  routes over the active set.

### 6.2 Tactical solver: deadline-ordered construction + regret-2 + ILS

```text
── PLAN_CYCLE (every 15 min on forecast update, or on trigger events §8.3) ──

INPUT : crews[1..N] (pos, status, frozen prefix), active properties[1..M]
        (x_j, V_j, w_j, ETA_j, Q_j), incumbent plan P_prev, matrices τ
OUTPUT: adopted plan + unservable list + per-stop timeline

1  refresh arrival grids A_med, A_q10; recompute ETA_j, Q_j, windows
2  P0 ← REPAIR(P_prev):            # keep incumbent as much as possible
      drop stops whose window became empty or whose safety check now fails
      recompute timeline; record dropped set
3  FROZEN ← commitments(P0)        # §8.1 — in-service stops + stops reachable < H_commit
4  U ← active properties not in P0 (incl. newly threatened + dropped)
5  P ← REGRET2_INSERT(P0, U)       # construction / repair
6  P ← LOCAL_SEARCH(P)             # relocate, swap, 2-opt*, or-opt (respect FROZEN)
7  best ← P
8  for it in 1..ILS_iters (default 30, or until 5 s budget):
9      P' ← SHAKE(best)            # remove R random non-frozen stops, R ~ U(2, 0.2·|stops|)
10     P' ← REGRET2_INSERT(P', unrouted(P'))
11     P' ← LOCAL_SEARCH(P')
12     if J(P') > J(best): best ← P'
13 ADOPT best only if J(best) − J(P0_repaired) ≥ adoption_threshold   # §8.2
14 emit plan, unservable list (with reason codes), crew instructions
```

```text
── REGRET2_INSERT(P, U) ──
while U not empty:
    for j in U:
        for every route i, every non-frozen gap g in route i:
            c(i,g,j) ← Δcost of inserting j at g          # Δ of the full objective J
                       (−∞ if infeasible: C1..C6 checked via MaxShift, §6.3, + corridor cache §7)
        best1_j ← max feasible c ; best2_j ← 2nd-best over *distinct routes*
    if no j has a feasible insertion: break
    j* ← argmax_j [ best1_j − best2_j ]        # regret-2; ties → larger w·V·P̂save, then earlier Q_j
    insert j* at its best1 position; update timeline + MaxShift for that route; remove j* from U
```

Construction detail: seed `REGRET2_INSERT` order with **deadline-ordered construction** — when two
regrets tie (common early on), prefer the property with the earliest `Q_j`. Pure deadline-greedy
(EDF) is the fallback mode if the 5 s budget is ever blown (`solver_mode=fast` config).

```text
── LOCAL_SEARCH(P) ──   # first-improvement, loop until no improving move
moves, in order:
  RELOCATE : move one stop to any other feasible position (any route)
  SWAP     : exchange two stops between routes
  2-OPT    : reverse a segment within a route (deadline TWs make most reversals
             infeasible — cheap to reject via MaxShift, keep the move anyway)
  OR-OPT   : move a chain of 2–3 consecutive stops to another position
  DROP/ADD : remove the lowest-scoring stop if that makes ≥1 unrouted property
             with higher score insertable (orienteering-specific — essential)
frozen stops: never moved, never reordered before their planned time
```

### 6.3 O(1) feasibility per candidate insertion — MaxShift

Standard TOPTW machinery (Vansteenwegen ILS): for each routed stop k maintain

```
wait_k     = max(0, release_k − arrive_k)
MaxShift_k = min( deadline_k − start_k , wait_{k+1} + MaxShift_{k+1} )
```

Insertion of j between k and k+1 is time-feasible iff
`shift = τ(k,j) + wait_j + s + τ(j,k+1) − τ(k,k+1)` satisfies `shift ≤ wait_{k+1} + MaxShift_{k+1}`
and j's own deadline holds. Update wait/MaxShift only for the affected route (O(route length)).
This is what keeps regret-2 at O(M²·N) per construction sweep and the whole cycle under the 5 s budget
for N ≤ 20, M ≤ 200 (⚠ budget estimate — verify with a synthetic benchmark before trusting).

### 6.4 Deliberate waiting

Because the release time `ETA_j − W_gel` can be in the future, the timeline permits `wait_j > 0`.
Local search naturally converts long waits into extra serviced properties. Cap planned waiting at a
stop to `max_stage_wait` (default 45 min) — beyond that, route the crew to a staging point instead
(safe-zone node with `V = 0`) so GPS tracking expectations stay sane.

---

## 7. Safe-access (corridor) check

Cheap grid test, PERIL-style, no routing-engine dependency:

### 7.1 Precompute per forecast cycle (once, vectorized)

```
D(cell) = min over cells c' within buffer_radius (default 500 m) of A_q10(c')
```
i.e. a **morphological min-filter (erosion) of the pessimistic arrival grid** — `D(cell)` = earliest
time fire could be within 500 m of that cell. One raster op per cycle (SciPy `minimum_filter` /
`grey_erosion` on the arrival raster; at 100 m cells over a 100 km² incident this is milliseconds).

### 7.2 Per-leg check (used inside insertion feasibility, cached)

```text
── CORRIDOR_SAFE(leg a→b, depart_time t0) ──
poly ← route polyline (Tier 1) or straight segment a→b (Tier 0)
samples ← points every sample_spacing_m (default 300 m) along poly
for each sample k at fraction f:
    t_pass ← t0 + f · τ(a,b)
    if D(cell(k)) < t_pass + m_route:  return UNSAFE     # m_route default 60 min, config
return SAFE
```

- `m_route` (route margin, default **60 min**) is deliberately larger than the 30 min on-property
  margin: a crew on the road can't shelter, and Tier 0 doesn't know the real road geometry.
- **Egress check (C5):** run `CORRIDOR_SAFE(j → nearest safe zone, depart_j)`; safe zones are a
  configured node list (staging areas, town centers). A property with no safe egress at its planned
  departure is infeasible *even if the deadline holds* — this is the constraint that actually bites in
  canyon communities with one road out.
- **Tier 0 caveat (⚠):** a straight segment can cross terrain the road doesn't. Mitigate with the wide
  min-filter buffer, or in Tier 1 pass the fire polygon as Valhalla `exclude_polygons` and let the router
  avoid it structurally, keeping the grid check as a belt-and-braces verify.
- Cache corridor results keyed on (a, b, 15-min time bucket, forecast version).

### 7.3 Risk exposure (soft term)

`RouteRiskExposure_i = Σ_samples max(0, 2·m_route − (D(cell) − t_pass)) / m_route` — zero when
comfortably clear, grows as any sample approaches the margin. Prices "barely safe" routes without
banning them.

---

## 8. Re-plan cadence and stability

### 8.1 Commitment horizon (frozen set)

- Stop being serviced right now: frozen (obviously).
- Next stop, if crew reaches it within `H_commit` (config `commit_horizon_min`, default **20 min**):
  frozen — crews hate U-turns, and the phone-GPS nav flow shouldn't rewrite mid-leg.
- Everything else: flexible. This is the committed/flexible split from the dynamic-VRP
  re-optimization literature (verified pattern; see Sources).
- **Safety overrides commitment:** if a frozen stop fails C1/C4/C5 on refresh, it is dropped and the
  crew is redirected immediately (safety trumps stability, always).

### 8.2 Anti-thrash mechanisms (all three, they're cheap)

1. **Warm start**: seed from the repaired incumbent (step 2) — most of the plan survives by default.
2. **Churn penalty** `λ_stab` in J (default 5 % of mean property score per reassigned stop, ×2 if
   crew enroute to it) — reassignments must pay for themselves.
3. **Adoption threshold**: replace the incumbent only if
   `J(new) − J(repaired incumbent) ≥ max(ε_abs, ε_rel · J(incumbent))`, defaults
   `ε_rel = 3 %`, `ε_abs = 0.5 ·` mean property score. Below threshold, keep the incumbent
   (its timeline is still refreshed against new ETAs).

### 8.3 Cadence

- **Scheduled:** every forecast refresh (~15 min), aligned to grid publication (subscribe, don't poll).
- **Event-driven immediate re-plan (bypasses threshold, not safety):** any stop's `Q_j` moved earlier
  by > `eta_shift_trigger` (default 30 min); crew ≥ 10 min behind timeline; crew GPS stale > 5 min
  (treat crew as unavailable-pending); new property enters the active set with `Q_j < now + 4 h`;
  corridor check on any planned leg flips to UNSAFE; crew completes a stop early.
- Solver budget 5 s ⇒ worst case plan latency ≈ forecast latency + 5 s. Fine.

---

## 9. Config parameter table (single source of truth)

| key | default | verified? |
|---|---|---|
| `service_minutes` | 30 | given by ops (treat as per-property overridable) |
| `safety_margin_minutes` (`m_safe`) | 30 | given by ops (hard floor; applied to Q10 arrival) |
| `route_margin_minutes` (`m_route`) | 60 | ⚠ design choice |
| `gel_effective_hours` (`W_gel`) | 72 | ⚠ **UNVERIFIED — confirm with GraphGel supplier** |
| `gel_full_strength_hours` (`F_full`) | 24 | ⚠ unverified |
| `gel_effectiveness` (`E_gel`) | 0.85 | ⚠ unverified placeholder |
| `circuity_factor` | 1.35 | literature 1.3±0.1; calibrate per area |
| `speed_kmh_by_class` | table §4.1 | OSM-wiki-style defaults; ⚠ tune |
| `fire_traffic_factor` | 1.4 | ⚠ unverified heuristic |
| `access_overhead_min` | 5 | ⚠ design choice |
| `planning_horizon_hours` | 24 | design choice (ties to EXTREME alert) |
| `commit_horizon_min` | 20 | design choice |
| `stability_lambda` (`λ_stab`) | 5 % mean score | design choice |
| `adoption_eps_rel` / `abs` | 3 % / 0.5·mean score | design choice |
| `buffer_radius_m` (min-filter) | 500 | design choice |
| `sample_spacing_m` | 300 | design choice |
| `max_stage_wait_min` | 45 | design choice |
| `eta_uncertainty_by_lead` | 0.15@6h → 0.35@48h | ⚠ placeholder, calibrate |
| `ils_iters` / `solver_budget_s` | 30 / 5 | benchmark before trusting |
| `replan_period_min` | 15 | matches forecast cadence |

---

## 10. Interfaces (what this module needs / emits)

**Needs:** arrival rasters `A_med`, `A_q10` (GeoTIFF or in-memory numpy + affine transform, forecast
version id); property table (id, lat/lon, V, w, structure notes); crew feed (id, lat/lon, ts, status —
from the phone GPS tracker, expect 30–60 s update period); safe-zone node list; travel-time provider
(Tier 0 function or Tier 1 URL); config above.

**Emits:** adopted plan `{crew_id: [ {property_id, planned_arrive, planned_start, planned_depart,
deadline, egress_route_id} ]}`; `unservable: [ {property_id, reason ∈ {window_empty, no_safe_egress,
capacity, dominated} } ]`; per-cycle diagnostics (J breakdown, churn count, solver time); plan version id
(monotonic) so the crew app can detect updates.

**Alert-engine tie-in:** the strategic tier's staffing-gap output feeds the 72 h notice; `unservable`
with `window_empty` at < 24 h lead feeds the EXTREME alert path.

---

## 11. What was verified vs. not

**Verified this session (web):** TOPTW framing + ILS as standard heuristic; regret-2 provenance
(Potvin & Rousseau 1993, Ropke & Pisinger 2006); committed/flexible + rolling-horizon +
event-driven re-plan stability pattern; circuity ≈ 1.3±0.1; OSRM demo server policy (1 req/s,
non-commercial, 10k-duration table cap, endpoints as in §4.2); Valhalla capabilities (matrix,
isochrone, dynamic costing, Docker distribution); PERIL/trigger-buffer construct (arrival-time
raster + safety factor) as precedent for §5/§7.

**NOT verified / flagged:** GraphGel `W_gel`=72 h, `F_full`, `E_gel` (no product data on disk —
**blocker-grade open question for the supplier**); Valhalla exact Docker image name/tag;
`fire_traffic_factor`, uncertainty curve, and all "design choice" defaults in §9; the 5 s solver budget
at N=20/M=200 (needs a synthetic benchmark); OR-Tools alternative (well-known, not re-checked);
per-service-area circuity (needs the one-time OSRM calibration script).

## Sources

- [Well-tuned algorithms for the TOPTW (JORS)](https://link.springer.com/article/10.1057/s41274-017-0244-1)
- [Cluster-based heuristics for TOPTW (ILS baseline description)](https://www.researchgate.net/publication/328788537_Efficient_Cluster-Based_Heuristics_for_the_Team_Orienteering_Problem_with_Time_Windows)
- [Cooperative TOP with Time Windows heuristic (arXiv)](https://arxiv.org/pdf/1608.05485)
- [Bräysy & Gendreau — VRPTW route construction & improvement survey (regret/Potvin-Rousseau)](https://cepac.cheme.cmu.edu/pasi2011/library/cerda/braysy-gendreau-vrp-review.pdf)
- [Ropke & Pisinger — ALNS for PDPTW (regret-k)](https://ideas.repec.org/a/inm/ortrsc/v40y2006i4p455-472.html)
- [Diana & Dessouky — regret insertion for large-scale DARP](https://ideas.repec.org/a/eee/transb/v38y2004i6p539-557.html)
- [Iterative re-optimization framework for dynamic VRP (committed vs flexible orders)](https://www.researchgate.net/publication/336185687_An_iterative_re-optimization_framework_for_the_dynamic_vehicle_routing_problem_with_roaming_delivery_locations)
- [Fast re-optimization for dynamic vehicle routing](https://www.researchgate.net/publication/265294201_A_fast_re-optimization_approach_for_dynamic_vehicle_routing)
- [OSRM API docs](https://project-osrm.org/docs/v5.24.0/api/) · [OSRM API usage policy](https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy) · [OSRM demo server](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server) · [table limit issue](https://github.com/Project-OSRM/osrm-backend/issues/2326)
- [Valhalla repo](https://github.com/valhalla/valhalla) · [Valhalla docs](https://valhalla.github.io/valhalla/) · [self-hosted Valhalla via Docker walkthrough](https://blog.rtwilson.com/simple-self-hosted-openstreetmap-routing-using-valhalla-and-docker/)
- [Circuity ≈ 1.3 scaling (npj Complexity)](https://www.nature.com/articles/s44260-024-00023-x) · [Country circuity factors](https://www.sciencedirect.com/science/article/abs/pii/S0965856401000441) · [Road network circuity in metros](https://www.researchgate.net/publication/276167411_Road_network_circuity_in_metropolitan_areas)
- [OSM tags for routing / Maxspeed](https://wiki.openstreetmap.org/wiki/OSM_tags_for_routing/Maxspeed) · [osm-legal-default-speeds](https://github.com/westnordost/osm-legal-default-speeds)
- [PERIL trigger-buffer model (Safety Science)](https://www.sciencedirect.com/science/article/pii/S0925753522002533) · [Trigger modeling with fire-spread + GIS](https://www.researchgate.net/publication/220605706_Setting_Wildfire_Evacuation_Trigger_Points_Using_Fire_Spread_Modeling_and_GIS) · [Coupled fire + traffic trigger setting](https://www.researchgate.net/publication/327476427_Setting_Wildfire_Evacuation_Triggers_by_Coupling_Fire_and_Traffic_Simulation_Models_A_Spatiotemporal_GIS_Approach)
