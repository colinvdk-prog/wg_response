# wgresponse — Architecture

Design narrative for the WG Response engine. The governing document is
[`wgresponse/contracts.py`](../wgresponse/contracts.py) — types, units, and safety
semantics quoted here are copied from it, and where this file and contracts disagree,
**contracts wins**. Research provenance lives in `docs/research/` (five digests,
2026-07-06).

## 1. Data flow

```
                 EXTERNAL FEEDS                                WG ESTATE
  ┌───────────────────────────────────────┐        ┌───────────────────────────┐
  │ WFIGS points+perimeters (5-min poll)  │        │ wg-consumer-profiles-dev  │
  │ FIRIS ComboLayer (CA tactical perims) │        │  (graphgel_enrolled=True) │
  │ CAL FIRE IncidentApi (CA metadata)    │        │ wg-technicians-dev roster │
  │ FIRMS VIIRS hotspots (growth signal)  │        └────────────┬──────────────┘
  │ [Watch Duty Enterprise — stub until   │                     │
  │  license lands]                       │                     │
  └───────────────┬───────────────────────┘                     │
                  v                                             v
        wgresponse.feeds                              wgresponse.clients
        fuse_incidents()  ── IRWIN-id join ──> FireIncident[]   ClientProperty[]
                  │                                             │
                  v                                             │
     ┌── per incident ─────────────────────────────┐            │
     │ wgresponse.terrain  LFPS tile (fuel/slope/  │            │
     │                     aspect/elev, 30 m)      │            │
     │ wgresponse.weather  NWS gridpoints (191 h)  │            │
     │                     / Open-Meteo fallback   │            │
     │        │                                    │            │
     │        v                                    │            │
     │ wgresponse.spread   ensemble_arrival():     │            │
     │   fuels -> rothermel -> 16-neighbor         │            │
     │   time-dependent Dijkstra x (1 + ensemble_n)│            │
     │   -> EnsembleArrival {p10, p50, p90}        │            │
     └──────────┬──────────────────────────────────┘            │
                v                                               v
        wgresponse.threat.ThreatMonitor.cycle(now_s)  <── eta_at() per client
                │
                ├──> wgresponse.alerts.AlertEngine ──> AlertEvent[] ──> wgresponse.notify
                │        (NOTICE_72H / WATCH / EXTREME_24H ladder, hysteresis)
                │
                └──> wgresponse.dispatch.plan()  <── wgresponse.crews (GPS beacons)
                         │   deadline-TOPTW, regret-2 + ILS      <── SafeZone list
                         │   every stop gated by wgresponse.safety
                         v
                     DispatchPlan {crew routes, unserved+reasons}
                         │
                         v
                wgresponse.server (console map, /api/plan, /api/beacon)
```

Cycle cadence: feeds poll every `poll_period_s` (300 s); the full
`ThreatMonitor.cycle()` re-plan runs every `replan_period_s` (900 s) and on trigger
events (ETA jumps, corridor flips, stale crews).

## 2. Alert ladder — state machine + hysteresis

Per (client, incident) pair. Legal transitions are pinned in
`contracts.ALERT_TRANSITIONS` and enforced by `assert_valid_transition`; the engine
must reject anything else:

```
 NONE ──────────────> NOTICE_72H ────> WATCH ─────> EXTREME_24H ────> IMPACTED
  │                       │              │  ^            │
  │                       │              │  └────────────┤   (de-escalation)
  │                       v              v               v
  └─────────────────> EXTREME_24H     CLEARED <────── CLEARED
                                         │
                                         └──> (re-threat) NOTICE_72H | EXTREME_24H
```

Semantics (contracts, Safety Semantics rule 1):

- **Evaluation is on P50** (median ensemble arrival):
  - `NOTICE_72H` when `eta_p50` within `notice_h` (72 h). One-time advance notice;
    the pair then rides `WATCH`.
  - `EXTREME_24H` when `eta_p50` within `extreme_h` (24 h) **OR** `eta_p10` within
    `extreme_p10_h` (12 h). The P10 disjunct means a fat-tailed pessimistic branch
    escalates even when the median looks comfortable.
- **Hysteresis on the way down**: downgrades require `clear_cycles` (2) consecutive
  clear evaluations AND `clear_margin_h` (6 h) of headroom beyond the threshold.
  A forecast flickering around the 24 h line must not flap EXTREME on/off — a pair
  leaves EXTREME only after 2 straight cycles with P50 arrival > 30 h out.
- `IMPACTED` is terminal (empty transition set).

Design lineage: this is the long-horizon analog of wgdefense
`preposition.py::ReadinessPlanner.stage()` (minute-scale standby/watch/preposition/
active ladder), re-anchored at 72 h / 24 h.

## 3. Safety semantics (the load-bearing rules)

Quoted from `contracts.py` "SAFETY SEMANTICS — the load-bearing rules; do not
reinterpret". Alerting is on P50; **everything that puts a crew near fire is on P10**
(the earliest/pessimistic ensemble arrival), because embers precede the modeled front.

**Rule 2 — the 30/30 minute budgets:**

```
latest_depart_s        = eta_p10_s - egress_buffer_min*60      (30 min rule)
latest_service_start_s = latest_depart_s - deploy_min*60       (30 min gel deploy)
```

A crew must be off the property 30 minutes before the pessimistic arrival, and gel
takes 30 minutes to apply, so service must start >= 60 minutes before P10 arrival.
`ember_buffer_min` (default 0) adds on top during wind events.

**Rule 3 — corridor erosion + the 56-minute route margin:** every travel leg
(ingress AND egress-to-safe-zone) is checked against the **ERODED** P10 arrival grid:
a morphological min filter over `corridor_erosion_m` (500 m), so `D(cell)` = earliest
time fire could be within 500 m of the cell. At each sample point (every
`corridor_sample_m` = 300 m along the leg) the eroded arrival time must exceed the
crew's pass time by `route_margin_min` (56 min) on chord legs. The route margin is
deliberately nearly 2x the on-property margin: a crew on the road cannot shelter, and
straight-line legs still carry residual geometry error beyond the measured circuity
(the 500 m erosion is the belt-and-braces for that too). The (`circuity`,
`route_margin_min`) pair was calibrated together 2026-08-02: (1.35, 60) -> (1.7, 56)
prices the measured road-network circuity in the multiplier and preserves the old
end-of-leg conservatism at the representative 8.5 km chord leg. Road-sampled legs
under plan-on-roads use the leaner `route_margin_roads_min` (30 min) — their geometry
is honest. This is the PERIL trigger-buffer construct applied to crews instead of
evacuees.

**Rule 4 — stop feasibility:** a plan stop is feasible only if ALL of:

1. service fits inside `[max(now, eta_p50_s - gel_window), latest_service_start_s]`,
2. ingress corridor safe (Rule 3),
3. egress corridor to a `SafeZone` safe (Rule 3).

An empty window => the property goes on the `unserved` list with a reason code
(`infeasible_deadline` / `no_safe_corridor` / `capacity`), which feeds the EXTREME
alert path — dispatch failure is an alerting input, not a silent drop.

**Rule 5 — gel window is UNVERIFIED product data.** Defaults (`gel_window_h=72`,
`gel_full_strength_h=24`, `gel_effectiveness=0.85`) are placeholders pending supplier
confirmation. wgdefense's environment-scaled coating-life model (~6 h @ 25 C base,
temp/RH/wind scaled) is available behind `gel_env_model_enabled` and disagrees with
the 72 h placeholder by 12x. Code treats the window as config, never a constant.
See `docs/OPEN_QUESTIONS.md` P-1 — this is the single most consequential open number
in the system: it decides whether crews deploy days ahead (calm logistics) or inside
the final hours (dangerous, capacity-constrained).

**Safety overrides stability:** committed/frozen dispatch stops are dropped and crews
redirected immediately if any safety rule fails on refresh. Stability mechanisms
never outrank Rules 2–4.

## 4. Spread method

Engine = Rothermel (1972, Albini-1976 adjusted coefficients) directional rate of
spread on a raster, turned into an arrival-time field with time-dependent 16-neighbor
Dijkstra — the FlamMap-MTT (Finney 2002) architecture. Chosen over Huygens front
propagation (FARSITE) deliberately: 10–100x less code for the same first-order
point-arrival-time skill at 0–96 h, where weather forecast error dominates anyway.

Per cell, per timestep:

1. **Fuel**: FBFM40 code -> `fuels.FUEL_TABLE` params (Scott & Burgan RMRS-GTR-153;
   table cross-validated against two independent sources — pyretechnics + firelab
   behave — and pinned). NB codes 91–99 are non-burnable: ROS 0, infinite traversal
   cost. Dynamic models (GR/GS/SH, TU1/TU3) transfer cured herb load to dead —
   mandatory, this is what makes autumn grass explosive.
2. **Moisture**: Simard EMC from forecast temp/RH, 1-h fuel moisture = 1.03 x EMC;
   live herb/woody from season defaults (`live_herb_frac`=0.30, `live_woody_frac`=0.60
   cured fire-season defaults).
3. **Rothermel** head ROS with wind factor (midflame wind via WAF from 10-m forecast
   wind) and slope factor (tan^2 — a 30 deg slope rivals a stiff wind), combined
   vectorially (ELMFIRE convention). Wind limit runs uncapped per Andrews/Cruz/
   Rothermel 2013 (conservative-fast is the right polarity for alerting).
4. **Anderson (1983) ellipse**: L/B = f(effective midflame wind), capped at 8;
   eccentricity gives directional ROS `R(theta)` — backing ~1.5% and flanking ~12%
   of head at 10 mph midflame, which matters for properties lateral to the run.
5. **16-neighbor Dijkstra** (8 standard + 8 knight moves, lengths d/d*sqrt2/d*sqrt5):
   edge cost = length / mean directional ROS at the two endpoints, with weather
   evaluated at the *tentative arrival time* at the tail cell (time-dependent, so the
   0–96 h wind forecast is honored). 16 neighbors bounds angular discretization error
   at +2.0% (8-neighbor is +8.2%); the bias makes predicted arrival *later* than
   truth, so the P10 safety margins must never be assumed to absorb it.
6. **Sources**: all cells under the current fused perimeter at the perimeter
   timestamp; hotspots or origin point if no perimeter.

**Spotting** (`spotting_enabled`, default OFF): Sardoy lognormal parameterization
with ELMFIRE defaults; deterministic integration = zero-cost teleport edges within
the median spot distance. Off by default because it is the main un-calibrated
accuracy lever; the P10 ensemble + ember_buffer_min carry the safety load meanwhile.

**Regression anchors** (harness target: modeled 24-h burned area within factor ~3 of
anchor, no-spotting; landmark arrival within +/-50% of observed):

| Fire | Anchor |
|---|---|
| Camp (2018) | 10,000 ac in ~90 min (~6,600 ac/h); 20,000 ac in <14 h |
| Tubbs (2017) | front >12 mi in ~3 h (~6.4 km/h sustained) |
| East Troublesome (2020) | ~6,000 ac/h sustained overnight (24-h total sources conflict 87k–140k ac; use the rate) |
| Grass 10% rule | head ROS ~10% of 10-m open wind speed (Cruz & Alexander 2020) — unit-test anchor |

## 5. Ensemble & uncertainty — why P10/P50/P90

The 72 h alert is **weather-limited, not model-limited**: beyond ~48 h lead, wind
forecasts rotate and forecast error dominates every other error source in the stack.
A single deterministic arrival time at 72 h lead is false precision; presenting a
band is the honest product.

Mechanism: `ensemble_arrival` runs the nominal member + `ensemble_n` (6) perturbed
members, scaling wind by +/-`wind_perturb_frac` (0.25 at max lead, growing with lead
time) and moisture by +/-`moisture_perturb_frac` (0.20), each member seeded via
`seeded_rng` (deterministic — same inputs, same band). Per-cell quantiles across
members give the `p10/p50/p90` rasters in `EnsembleArrival`.

Division of labor (mirrors the dispatch digest's "deadline conservative, reward
realistic"):

- **P50** -> alert ladder + gel-freshness/value math (realistic expectations).
- **P10** (earliest) -> all crew-safety math: departure deadlines, corridor checks
  (pessimistic hard constraints).
- **P90** -> presentation only (the optimistic edge of the client-facing band).

Quantile calibration against forecast-verification data is an open item (T-4).

## 6. Dispatch

Problem class: **deadline Team Orienteering Problem with Time Windows** (deadline-
TOPTW), dynamic rolling-horizon variant. Not assignment — crews serve properties
sequentially; not full coverage — some properties cannot be saved, and choosing which
to serve *is* the optimization (prize-collecting).

- **Objective**: maximize sum of `w_j * V_j * P_save(j, completion)` where P_save
  folds gel effectiveness x freshness at fire arrival (`PlannedStop.gel_fresh_frac`)
  x hit probability, minus small penalties for drive time, route-risk exposure
  (closeness to the eroded margin, priced not banned), and churn.
- **Construction**: repair the incumbent plan (drop newly-infeasible stops), then
  **regret-2 insertion** (Potvin & Rousseau 1993 / Ropke & Pisinger 2006): insert
  the unrouted job with the largest best-vs-second-best gap first, so jobs that only
  fit one place get their slot. Ties break to earliest P10 deadline.
- **Improvement**: **ILS** — local search (relocate / swap / 2-opt / or-opt /
  drop-add, the orienteering-essential move) + shake (remove 2..20% random non-frozen
  stops, reinsert), `ils_iterations` (30) restarts inside `solver_budget_s` (5 s).
  O(1) insertion feasibility via the standard TOPTW wait/MaxShift recursion.
- **Stability** (anti-thrash, all three, all cheap):
  1. warm-start from the repaired incumbent;
  2. churn penalty `churn_penalty_frac` (5% of mean stop score per reassignment,
     doubled if the crew is already en route);
  3. adoption threshold `adoption_threshold_frac` (3%) — a new plan must beat the
     repaired incumbent by 3% of objective or the incumbent stands.
  Stops inside `commitment_horizon_min` (20 min) are frozen (`CrewPlan.
  committed_until_s`) — crews hate U-turns. **Safety still overrides commitment**
  (section 3).
- **Cadence**: every `replan_period_s` (900 s) plus event triggers (ETA moved earlier
  >30 min, corridor flip, crew >=10 min behind, beacon stale, early completion).
- **Unserved is a first-class output**: every dropped job carries a reason code and
  feeds the alert path.

Solver benchmark at N=20 crews / M=200 jobs inside the 5 s budget is unverified —
open item T-3. Fallback mode if the budget blows: pure earliest-deadline-first
construction, no ILS.

## 7. Scaling notes

- **Grid coarsening ladder**: work at `grid_cell_m` (90 m; LANDFIRE native is 30 m —
  `validate_config` rejects anything below 30 m as fabricated precision). When the
  operational window exceeds `grid_max_cells` (300k cells), coarsen: 90 -> 120 ->
  180 -> 270 m until under budget. At 90 m, 300k cells ~ a 49 x 49 km window —
  ample for a 96 h horizon around one incident; multi-incident cycles run one grid
  per incident rather than one continental grid.
- **Pure-Python Dijkstra cost**: 16-neighbor on 300k cells is ~4.8M edge relaxations
  through `heapq`; expect roughly 5–15 s per member on stock CPython. A full
  7-member ensemble is then ~0.5–2 min per incident per cycle — acceptable against a
  15-min replan cadence for a handful of concurrent incidents, but the budget goes
  first here as client count and incident count grow.
- **Upgrade path**, in order of leverage:
  1. numpy vectorization of the per-cell Rothermel precompute (static intermediates
     per fuel model are cached; only moisture/wind terms vary per timestep);
  2. numba/C-extension for the Dijkstra inner loop (this is the ELMFIRE/pyretechnics
     lane — pyretechnics' level-set core is the reference if we ever want perimeter
     shapes, not just arrival times);
  3. member-parallelism (ensemble members are embarrassingly parallel across
     processes).
  All three keep the contracts seam byte-identical: `compute_arrival` in, `Raster`
  out; the pure-Python path remains the test oracle.
- **Feeds/weather are not the bottleneck**: WFIGS/FIRIS polls are 5-min cadence
  JSON; NWS gridpoint pulls are per-2.5-km-cell and cached per `Last-Modified`;
  LFPS tiles are fetched once per incident window and cached via
  `terrain.save_ascii_tile`.

## 8. Server & console

Stdlib-only HTTP (`wgresponse.server`): `POST /api/beacon` (crew phone GPS),
`GET /api/state` (incidents/assessments/alerts), `GET /api/plan` (current
DispatchPlan), `GET /console` (single-file Leaflet ops map: perimeters, arrival
isochrones from the p50 raster, clients colored by alert state, crew markers,
routes). Auth: `X-Internal-API-Key` header, fail-closed unless `WGRESP_DEV_OPEN=1`
(same pattern as the firerisk portal gates). The console seed is
`graphgel_dispatch/frontend/index.html` from the firerisk repo (see INTEGRATION.md).
