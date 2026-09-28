# Fire belief — each fire's spread rate, learned from the live reports (2026-09-27)

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [calibration/stacked_rings_fix_2026-09-27.md](calibration/stacked_rings_fix_2026-09-27.md) §4.5. **Affected here:** :19–20, :101 and :313. The 0.71 model error is re-measured at 0.698 on the corrected corpus, so the prior is unaffected in practice. The text below is left as written.

Contracts SAFETY SEMANTICS 14 · `wgresponse/fire_belief.py` · knobs `fire_belief_*` ·
watch flags `--fire-belief` / `--fire-belief-apply` / `--fire-belief-v2` (§7) · tests
`tests/test_fire_belief.py`, `tests/test_fire_belief_v2.py` ·
study `scratch_review/fire_belief_study.py`

Founder, 2026-09-27 (verbatim): *"ensure that fire spread includes local weather forecast.
also as a fire ages and becomes contained, the rate of fire spread should be baynsian unless a
new weather pattern emerges."* and *"I will want the map to be updated with the live data when
it becomes available which then so that simulations are always corrected by the real data.
This becomes a baynesian feedback loop for each fire."*

## 1. In plain words

**What it does.** Every time the fire feeds publish a new measurement of a fire — a new
perimeter polygon, or a changed acreage — the watcher compares how much the fire *actually*
grew since the previous measurement with how much the engine *said* it would grow. From that
comparison it keeps one number per fire: how fast this fire really spreads compared with the
physics (for example "0.21× physics"). It starts from what we already know about the engine's
error (the physics can be off by about 2× either way) and narrows down as reports come in —
recent reports count more (a report loses half its weight every 3 days). That is the Bayesian
feedback loop: every new report corrects the belief, and the belief corrects the next forecast.

**When it slows a fire.** Only when the belief is *applied* (`--fire-belief-apply`, off by
default) and only when all of these hold:
- the fire has at least one measured growth window the engine could model;
- it is **not growing fast**: under 1,000 acres a day and under 25 % of its area a day;
- no surprise reset is active;
- the forecast hour belongs to the **same weather pattern** the fire was observed in.

That is the aged, contained fire the founder described. What slows is the fire's projected
footprint, its **median** ETA (P50 — what the alert ladder uses) and its late edge (P90). In the
synthetic study a contained ~19,000-acre fire that really grows ~500 acres a day is forecast by
raw physics to grow ~18,000 acres a day (37x the truth); with the belief applied the forecast
drops to ~6x the truth and its footprint error from 91 % to 13 % (numbers in §5).

**What it does NOT slow: the crew-safety arrival (P10).** By default the engine takes the
earlier of the belief's P10 and today's physics P10 (the *P10 guard*). The study showed why:
when the learned slow rate was also allowed to slow P10, the fire reached 38–43 % of the
contained fire's newly burned cells *before* the P10 (today: ~2 %). A slow fire's front is part
way across its next cells whenever a perimeter is drawn, and every forecast restarts the front
at that perimeter; knowing the fire's average rate does not tell the engine where on its way
across each cell the front is. `fire_belief_apply_p10` removes the guard — not recommended.

**When it refuses to slow a fire** (the full physics and the engine's existing error band apply,
exactly as today):
- **New weather.** From the first forecast hour whose fire weather is clearly worse than
  anything the fire was observed in (1.5× the spread severity of the observed weather) or
  whose wind swings more than 60° at 4 m/s or more, to the end of the forecast. If the watcher
  has no logged weather for the observed period, every hour counts as new.
- **Surprise.** If a new report shows more growth than the belief's own 90 % upper forecast,
  the fire goes straight back to physics for 24 hours and the older evidence is thrown away.
  Slow to trust, quick to distrust.
- **Fast fires.** A fire growing 1,000+ acres a day, or 25 %+ of its area a day (with at least
  100 acres), or a fire under 2 days old that has grown that fast since discovery, is flagged
  **rapid**: the belief may make its forecast *faster* than physics but never slower. Growth is
  counted *beyond measurement noise* (its one-sided 95 % lower bound): 3 % of a 20,000-acre
  fire is ±600 acres per report, and without that discount every big fire read "rapid" from
  report noise alone (measured, §5).
- **No evidence** (a new fire, a fire whose reports never change, a seeded drill fire).
- **Any belief error** — the watcher falls back to physics and notes it.

**What it cannot see.**
- It learns **one number for the whole fire.** A fire 90 % contained with one flank still
  running is learned as "slow on average": the active flank is under-forecast until a report
  shows its growth (then the surprise reset fires). Uneven containment is the biggest limit.
- It learns **only when a report arrives** (perimeters are often nightly; acreage is re-stated
  in discrete updates). An un-forecast flare-up is invisible until the next report shows it —
  measured below: the exposure is the time to the next report.
- **Acreage is noisy.** A record can be modified without its size being re-measured, sizes are
  revised down and up; the belief uses heavy-tailed noise and treats a long-unchanged size as
  weak evidence.
- **One weather point per fire** (the forecast at the fire's centre), as the spread uses.
- **Point fires** (no perimeter) are still started as a point at the current time — the belief
  learns their rate from acreage changes but does not redraw their current extent, so a big
  point-only fire's footprint is still understated (unchanged from today).
- NASA FIRMS hotspots (4–6 satellite passes a day) would shorten the evidence lag; they need the
  free `FIRMS_MAP_KEY` and are not used by the belief in this build.

**What you see.** With `--fire-belief` the world map's incident tooltip gains one sentence, e.g.
*"Spread rate learned from 4 reports: 0.21x physics (0.12x-0.37x). Weather: same pattern until
Tue 14:00Z, then new pattern (physics applies). Last perimeter: +310 ac (belief predicted
+290)."* `/api/state` gains `fire_status`, `/api/world/summary` rows gain `belief`, and the
belief is saved to `<state-dir>/fire_beliefs.json` (mirrored to S3 beside the state file).
Tracking alone changes no forecast, alert or dispatch.

**Weather forecast in the spread (item 1).** Already true before this build and now pinned:
the live spread fetches the local hourly NWS forecast at the fire's centre
(`threat.ThreatMonitor._assess_incident` → `weather.NwsWeather.forecast`, Open-Meteo fallback)
and prices every spread step with the forecast hour it happens in (time-dependent Dijkstra,
`spread.compute_arrival`). New pin: `tests/test_fire_belief.py::
test_mid_horizon_wind_shift_changes_downstream_arrivals` (a wind turn at +12 h of a 24-h
horizon moves the arrivals it steers, through the whole monitor); the spread-level pin is
`tests/test_spread.py::test_wind_switch_bends_fast_axis`.

## 2. The model

**Parameter.** θ = log of a whole-fire multiplier on the engine's head rate of spread — the
same place the ensemble's model-error term acts (`spread.compute_arrival` `ros_scale`).
Prior N(0, σ₀²) with σ₀ = `fire_belief_prior_sd_log`, else the ensemble's
`model_error_sd_log`, else 0.71 (the hindcast-PIT-measured engine error). The posterior lives on
a fixed grid θ ∈ [ln 0.01, ln 5], step 0.02 (311 nodes): exact for heavy-tailed and censored
terms, cheap (every quantity below is a sum over 311 nodes).

**Observations** (`FireBeliefStore.observe`, every cycle, every active non-drill incident). Two
series per fire, never mixed:
- *perimeter* — a polygon with a NEWER own time (`meta.perimeter_at_s`, else the record time;
  clamped ≤ now). Area = `meta.perimeter_acres` when the feed carries it on both ends of a
  window, else the rings' area (equal-area sinusoidal projection; a ring nested in an odd number
  of rings is a hole — the even-odd rule the spread rasterizer uses). Time = the polygon's time.
- *reported* — the record's size (`meta.reported_acres`, else `FireIncident.acreage`) when it
  CHANGES, at `meta.reported_at_s` else `updated_at_s`. An unchanged size becomes a zero-growth
  observation only after the record kept being modified for `fire_belief_static_h` (24 h) since
  the size last changed — at the latest modification time, with 3× the area error (a record can
  be modified without its size being re-measured; a frozen record is never evidence).
- Two observations of one series closer than `fire_belief_min_obs_spacing_h` (3 h) are not both
  kept (the next one spans both). Consecutive observations of one series form a growth
  **window**. Seeded (drill) incidents are never observed.

**Model side** (`after_spread`). After each spread the NOMINAL member's growth curve is kept:
G(s) = burned area with arrival ≤ t_anchor + s, hourly over the horizon (t_anchor = the
perimeter's own time, or now for a point incident), the time the nominal first reached the
domain edge, and the nominal's per-hour multiplier. The curve is **pinned** to the newest
observation of each series that has none yet, if that observation was seen ≤ 6 h ago: a
window's model rate is the forecast the engine made at (or just after) the window's start —
never a hindcast with hindsight (a later curve would hindcast elapsed hours with the current
weather, because the NWS forecast starts at the current hour).

**Likelihood** (size-independent, so point and perimeter fires both learn). Areas become
equivalent-circle radii r = √(A/π). Observed radial rate v_obs = (r_b − r_a)/(t_b − t_a).
Model rate v_nom = the slope of r(G(s)) over the same window on the pinned curve (a point curve
skips its first 2 h: a fire started from one cell is not yet a front).
*Time-scaling approximation:* under multiplier m = e^θ every arrival time scales ≈ 1/m, so the
model's radial rate scales ≈ m: v(θ) = v_nom · e^θ / m̄_nom, with m̄_nom the nominal member's own
mean multiplier over the window (1.0 unless applied). Noise: the area error sd_A =
√((0.03 A)² + (10 ac)²) (×3 for static observations) propagated to r at both ends (the half-width
of the radius interval of A ± sd_A — robust where dr/dA diverges at small A), plus a model
discrepancy of 30 % of v(θ), under a Student-t with ν = 4 (one re-stated acreage cannot swing a
fire). When the pinned curve ends (horizon) or the nominal reaches the domain edge inside the
window, the model's growth is only a **lower bound** L: the term becomes the profile
likelihood — flat wherever L·e^θ/m̄ ≤ v_obs (a model at least that fast could match), the ordinary
density at L·e^θ/m̄ above it — so a censored window can only slow the belief, never speed it.
Each window's log-likelihood is weighted 0.5^(age / `fire_belief_half_life_h`) (age from its
end); where a perimeter window and a reported window cover the same hours each counts
1/(1 + overlap fraction) (one fire's growth is not two independent facts).

**New weather pattern** (`before_spread`, every domain incident every cycle). The fire-centre
forecast's CURRENT hour (effective wind with the cfg gust blend, direction, RH, temperature) is
logged hourly per fire (240 h kept). Severity S = the engine's own Rothermel head ROS for the
incident tile's dominant burnable fuel on flat ground under that hour's wind and dead-fuel
moisture (`fuels.dead_fuel_moisture_frac`, as the spread uses). Evidence severity = the weighted
P90 of S over the logged hours overlapping the posterior's windows (weights = the windows');
evidence direction = the speed-weighted circular mean. A forecast hour is NEW if S >
`fire_belief_novel_severity_ratio` (1.5) × the evidence P90, or its wind ≥
`fire_belief_novel_min_wind_mps` (4) and it turns more than `fire_belief_novel_shift_deg` (60)
from the evidence direction (or the evidence winds had no steady direction: resultant < 0.3).
From the first new hour to the end of the horizon the prior applies
(`FireStatus.novel_from_s`). No logged weather overlapping the evidence ⇒ every hour is new.

**Surprise reset.** Before a new window joins the posterior, the belief's posterior-predictive
distribution of that window's growth (the posterior mixture of the noise model, so measurement
noise alone does not trip it) is computed; growth beyond its P90 resets the fire to the prior for
`fire_belief_reset_h` (24 h, sticky — a later surprise extends it) and drops every window older
than the surprising one (which itself stays, pulling the belief up when the reset ends).

**Rapid / known** (the off-map rule's inputs). known = at least one completed window.
rapid = a window ending within `fire_belief_rapid_lookback_h` (48 h) grew ≥
`fire_belief_rapid_acres_per_day` (1,000, 24-h equivalent) or ≥ `fire_belief_rapid_frac_per_day`
(25 %) of its starting area per 24 h with ≥ `fire_belief_rapid_min_acres` (100) grown; or a
fire discovered within the lookback reached that rate since discovery (elapsed floored at 3 h);
or a surprise reset is active. Growth is counted at its lower bound beyond measurement noise,
grown − `fire_belief_rapid_noise_z` (1.645) × sd(grown) under the belief's own area-error model
(0 = the raw observed growth). The price: for big fires the detection floor rises — a
20,000-acre fire's 12-h window must show ≥ ~2,000 acres (≈ 4,000 ac/day) to be flagged on one
window; slower sustained growth is still learned by the posterior (the forecast follows it) but
not flagged rapid. The first-pass study (raw growth) flagged 100 % of cycles rapid in every
aged/contained case, i.e. the belief could never slow a big fire.

**Applying** (`fire_belief_apply`; with `fire_belief_apply_p10` also the P10 safety-law change). `before_spread` returns a
`spread.ensemble_arrival(member_hour_scale=…)` callable; each member k gets a per-HOUR head-ROS
multiplier list (`spread.compute_arrival(hour_ros_scale=…)`, folded into the per-hour weather
tuple and the ROS memo key so the memo stays exact; hours before t0 — perimeter hindcast — use
the first hour's value):
- same-pattern hours: nominal = the posterior median; member k = the posterior quantile at its
  model-error stratum level q_k (the level whose normal quantile z_k the legacy model-error term
  uses); for z_k > 0 never below median·e^(0.35·z_k) (`fire_belief_fast_sd_floor`); a rapid fire
  takes max(prior quantile e^(σ₀ z_k), posterior quantile); nothing below 0.05
  (`fire_belief_mult_floor`);
- new-pattern hours: the member's exact legacy multiplier (1.0 nominal, e^(`model_error_sd_log`·z_k)).
It returns None — every member the unchanged legacy call — whenever the state is prior or
reset, or the first forecast hour is already new. With a callable the monitor runs
`fire_belief.applied_ensemble`: the members follow the belief, and unless
`fire_belief_apply_p10` the **P10 guard** replaces the P10 raster by min(belief P10, physics
P10) — the physics ensemble being the exact legacy call (a second ensemble, run only for
belief-applied fires; slow fires are cheap). p10 ≤ p50 ≤ p90 is preserved (the min only lowers
p10). So by default the belief moves the nominal, P50 (alert timing) and P90, and P10 — crew
deadlines, the eroded-P10 corridors, the extreme-P10 ladder rung — is never later than today's.

**Status** (`contracts.FireStatus`, on `CycleResult.fire_status` for every active incident):
`incident_id, known, rapid, reasons, mult_p10/p50/p90` (posterior multiplier quantiles; None =
the prior), `n_obs` (windows in the posterior), `novel_from_s` (None = no new pattern within
the horizon *or* no forecast consulted this cycle — the reasons say which), `state`
(`prior` | `learned` | `reset`), `last_obs` (the latest window: time, acres, series, observed
growth, the belief's median predicted growth made before it was seen).

**Persistence and isolation.** `FireBeliefStore.to_json/from_json`; `fire_belief.BeliefFile`
writes `<state-dir>/fire_beliefs.json` atomically after every cycle and mirrors it through
`persist.S3Mirror` as `<prefix>/fire_beliefs.json`; on start it restores local first, then the
mirror; failures are loud and start from the prior. Knob off ⇒ no file written or read. Bounded:
fires unseen 14 days are dropped; ≤ 16 observations and 64 windows per series/fire; 240 weather
hours; at most one growth curve per series. **Test worlds / drills:** give the TEST world
`ThreatMonitor(..., fire_beliefs=live_monitor.fire_beliefs.copy())` and no `belief_file` —
`copy()` is a deep copy with `persistent=False`, and `BeliefFile.save` refuses to write a
non-persistent store, so a drill can learn but never change the live belief
(`tests/test_fire_belief.py::test_a_drill_world_copy_never_changes_the_live_belief`).

## 3. Knobs (all in `ResponseConfig`; every one OFF/legacy by default)

| Knob | Default | Role |
|---|---|---|
| `fire_belief_track` | False | observe, learn, publish; no physics change |
| `fire_belief_apply` | False | the posterior drives the ensemble in same-pattern hours (requires track); P10 guarded |
| `fire_belief_apply_p10` | False | lift the P10 guard: the belief moves P10 too (measured unsafe for contained fires) |
| `fire_belief_prior_sd_log` | 0.0 | prior sd; 0 = `model_error_sd_log` if > 0 else 0.71 |
| `fire_belief_half_life_h` | 72.0 | evidence half-life |
| `fire_belief_min_obs_spacing_h` | 3.0 | min gap between observations of one series |
| `fire_belief_area_rel_err` / `_abs_err_acres` | 0.03 / 10.0 | area error sd |
| `fire_belief_noise_nu` | 4.0 | Student-t degrees of freedom |
| `fire_belief_discrepancy_frac` | 0.30 | model discrepancy, fraction of predicted rate |
| `fire_belief_fast_sd_floor` | 0.35 | apply: fast-tail floor (log sd) |
| `fire_belief_mult_floor` | 0.05 | apply: no multiplier below |
| `fire_belief_novel_severity_ratio` | 1.5 | new pattern: severity ratio |
| `fire_belief_novel_shift_deg` / `_min_wind_mps` | 60.0 / 4.0 | new pattern: wind shift at a min wind |
| `fire_belief_reset_h` | 24.0 | surprise reset duration |
| `fire_belief_static_h` | 24.0 | unchanged-acreage window |
| `fire_belief_rapid_acres_per_day` / `_frac_per_day` / `_min_acres` / `_lookback_h` | 1000.0 / 0.25 / 100.0 / 48.0 | rapid thresholds |
| `fire_belief_rapid_noise_z` | 1.645 | rapid counts growth beyond noise (lower bound); 0 = raw |

**Module constants (heuristics, `wgresponse/fire_belief.py`):** point-start transient 2 h (a
one-cell start needs ~2 h to become a front at 90–240 m cells); minimum model span 1 h (the
curve is hourly); curve pin lag 6 h; static-observation error ×3 (a not-re-measured size makes
a window about a third as informative); evidence-direction resultant 0.3 (below it the observed
winds had no steady direction, so any hour ≥ the min wind counts as new — conservative);
young-fire elapsed floor 3 h (= the observation spacing); prune 14 days; 16 observations / 64
windows / 240 weather hours / 337 curve hours; windows lighter than 1e-3 leave the posterior.

## 4. Interfaces

- `contracts.FireStatus` / `FireObservation` / `FIRE_BELIEF_STATES`; `CycleResult.fire_status`.
- `threat.ThreatMonitor(..., fire_beliefs=store)` and `.fire_beliefs`; three hooks
  (`observe` after the staleness gate, `before_spread`/`after_spread` around each domain's
  ensemble, `statuses` into the result). A surprise reset is a cycle note + an
  `OperatorEvent(kind='fire_belief')` (not a degradation).
- `spread.compute_arrival(hour_ros_scale=)` / `spread.ensemble_arrival(member_hour_scale=)`;
  `fire_belief.applied_ensemble` (the belief members + the P10 guard).
- `cli watch --fire-belief` / `--fire-belief-apply`; `_WatchDaemon(belief_file=)`;
  `/api/state` key `fire_status`; `worldview.build_summary` row key `belief`
  (`fire_belief.status_row`); `console/world.html` `beliefText()` in the incident tooltip.

### 4a. How it joins the other 2026-09-27 rules (integrator)

- **The road rule (SAFETY SEMANTICS 15).** The belief's status table is
  computed before the planner and combined with the feed's behaviour report.
  Growth beyond measurement noise, or an active surprise reset, makes a fire
  rapid, so roads beyond its modelled area keep the 6 km/h cap. A completed
  growth window makes an unreported fire known, so it extrapolates at its own
  forecast front. With both rules armed, tracking alone can therefore change
  which roads open. Pinned in `tests/test_belief_offmap_worlds.py`.
- **The test map (SAFETY SEMANTICS 16).** Before every drill cycle the test
  world takes a fresh, non-persistent copy of the live store. Drills start
  from the rates the live world has learned and never write back.
- **Local weather (SAFETY SEMANTICS 17).** A belief-applied fire with a
  weather field prices both of its ensembles, the belief's members and the
  physics run behind the P10 guard, per zone and hour. The belief's own
  weather log and new-pattern test still read the centroid forecast.
- **Feed sanity (SAFETY SEMANTICS 18; `cfg.feed_sanity`, watch
  `--feed-sanity`; audit and measurement in
  `docs/review/feed_sanity_2026-09-27.md`).** The live feeds re-issue one
  polygon under newer times (45 % of the perimeter windows on 11 real fires
  were zero-growth re-stamps), publish polygons under 10 % of their own
  record's size, reset reported sizes, and carry area figures off their own
  geometry. With the knob on, the observers skip what the feed cannot have
  meant (future or pre-fire own times, collapsed polygons), count a
  re-stamped polygon once per `fire_belief_static_h` as a static
  observation, hold a drop beyond noise until it comes back or persists
  (then the series restarts, never a negative window), and read a polygon's
  area from its rings when the feed's figure is more than 2× off. The seed
  side uses the fire's last plausible polygon instead of a collapsed one.
  Knob off = the observers above, unchanged.

## 5. Measured results

### A. Synthetic truth study

`scratch_review/fire_belief_study.py` (tables: `scratch_review/fire_belief_study.out`; every
cycle's row: `scratch_review/fire_belief_study.json`; ~26 min on the dev box). A **hidden truth
fire** burns on a real cached LANDFIRE tile (`data/tiles/paradise_ca_ext.wgt`: 48 km, 240 m
cells, GR2 / GS2 / TU5 / TL3 mix) under the **same spread engine** with a hidden whole-fire
multiplier m_true (its edges integrated piecewise across hours, so a hidden rate change acts at
once). The live watcher's real code path (ThreatMonitor + AlertEngine, hourly cycles for 84 h,
ensemble 6, model error 0.71, p50 median bound, 48 h horizon, a perfect weather forecast) watches
it through a stub feed: a perimeter re-traced from the truth every 12 h and a reported acreage
with Student-t(4) 3 % noise every 12 h (6 h after each perimeter). Three arms, each its own
monitor and belief: **OFF** = tracking only (today's forecast), **ON** = apply with the P10
guard (the default), **ON+P10** = apply with `fire_belief_apply_p10` (the belief also moves P10).
Scores, over the cells the truth burns in the next 24 h (48 h): the nominal's 24-h burned-area
error, the forecast/truth 24-h growth ratio, the **P10 miss rate** (the truth arrived before the
forecast P10 — the crew-safety failure), the median P10 lead, client P10 misses, and the P50
calibration (share of cells the truth reaches before the P50: ~50 % = calibrated, ~0 % =
over-warning). Two regimes: GROWING fires (establish 24 h at m = 1, ~1,800 ac, then m_true under
diurnal 6 ± 2.5 m/s wind) and AGED / contained fires (establish 72 h, ~19,000 ac, then calm
3 ± 1 m/s weather).

**Learning** (posterior median at the end of the run):

| case | m_true | truth growth, ac/24 h (median) | posterior P50 | rapid (share of cycles) |
|---|---|---|---|---|
| grow-m0.5 | 0.5 | 6,021 | 0.53 | 100 % |
| grow-m1.0 | 1.0 | 23,371 | 1.06–1.07 | 100 % |
| grow-m2.0 | 2.0 | 84,802 | 1.91–1.97 | 100 % |
| aged-m0.01 | 0.01 | 128 | 0.14–0.16 | 0 % |
| aged-m0.03 | 0.03 | 498 | 0.17–0.20 | 11 % |
| aged-m0.1 | 0.1 | 1,366 | 0.19–0.20 | 69 % |

Growing fires are learned within ~6 %. Contained fires are resolved only to "at most ~0.2×
physics": at ~19,000 acres one report's 3 % error is ±600 acres, more than the fire grows in a
window, and the N(0, 0.71²) prior (the engine's error on *running* fires) holds the belief there
— slow to trust. Rapid is right where it should be: every growing fire, 69 % of cycles of the
1,366 ac/day fire (1.4× the threshold, judged beyond noise), at most 11 % of the contained ones.

**Forecast quality and P10 safety** (cycles after the first learned day):

| case | arm | 24-h area error | 24-h growth ratio | P10 miss 24 h | P10 miss 48 h | P10 lead 24 h (h) | client P10 misses | P50 late 24 h |
|---|---|---|---|---|---|---|---|---|
| grow-m0.5 | OFF / ON / ON+P10 | 58 % | 2.38 | 2.19 % | 0.88 % | −12.2 | 3/177 | 18 % |
| grow-m1.0 | OFF | 7 % | 0.85 | 3.13 % | 1.68 % | −7.4 | 4/375 | 88 % |
| grow-m1.0 | ON | 4 % | 0.93 | 3.11 % | 1.53 % | −7.4 | 4/375 | 75 % |
| grow-m1.0 | ON+P10 | 4 % | 0.93 | 3.12 % | 1.54 % | −7.4 | 4/375 | 75 % |
| grow-m2.0 | OFF | 34 % | 0.34 | **53.09 %** | **65.38 %** | +0.3 | **188/342** | 100 % |
| grow-m2.0 | ON | 1 % | 0.99 | 6.15 % | 3.61 % | −4.2 | 31/342 | 81 % |
| grow-m2.0 | ON+P10 | 1 % | 0.99 | 6.15 % | 3.61 % | −4.2 | 31/342 | 81 % |
| aged-m0.01 | OFF | 96 % | 143.8 | 2.86 % | 1.26 % | −14.8 | – | 11 % |
| aged-m0.01 | ON | 15 % | 21.2 | 2.86 % | 1.26 % | −14.8 | – | 64 % |
| aged-m0.01 | ON+P10 | 15 % | 21.2 | **43.29 %** | **26.39 %** | −5.2 | – | 64 % |
| aged-m0.03 | OFF | 91 % | 36.7 | 1.63 % | 0.80 % | −14.8 | – | 9 % |
| aged-m0.03 | ON | 13 % | 5.9 | 1.63 % | 0.80 % | −14.8 | – | 64 % |
| aged-m0.03 | ON+P10 | 13 % | 5.9 | **38.53 %** | **24.19 %** | −4.4 | – | 64 % |
| aged-m0.1 | OFF | 81 % | 15.6 | 2.12 % | 1.06 % | −14.0 | 0/48 | 11 % |
| aged-m0.1 | ON | 70 % | 11.6 | 2.12 % | 1.06 % | −14.0 | 0/48 | 27 % |
| aged-m0.1 | ON+P10 | 70 % | 11.6 | **15.13 %** | **11.20 %** | −11.3 | **14/48** | 27 % |

- **Growing fires:** the belief never slows them (all rapid) but *does* speed up a fire that
  outruns physics: grow-m2.0's P10 misses fall from 53 % to 6 % and client P10 misses from 188
  to 31 of 342 — today's engine is dangerously late on such a fire and the belief fixes most of
  it. (grow-m0.5: the belief learned 0.53× but, the fire being rapid, applies physics.)
- **Contained fires:** the forecast footprint error falls from 91–96 % to 13–15 % and the P50
  goes from over-warning (9–11 % of cells reached before it) to roughly calibrated (64 %).
- **P10:** with the guard (ON) the P10 miss rate equals today's in every case and phase (the
  min with physics). Without it (ON+P10) a contained fire's P10 is too late on 38–43 % of the
  newly burned cells (15 % for the 0.1× fire): the front of a slow fire is part way across its
  next cells whenever a perimeter is drawn and every forecast restarts it there, so at the
  learned slow rate the first ring's arrivals come late. Edge integration was ruled out as the
  cause: running the aged-m0.03 forecasts with `edge_weather_piecewise` gave 36.85 % (vs
  38.53 %). This is why `fire_belief_apply_p10` defaults OFF.

**Weather-change case** (aged fire at 0.03×, calm evidence; a *forecast* 11 m/s north-east wind
event at +120 h for 18 h that also breaks containment, m_true 1.0 from then):

| phase | arm | 24-h area error | growth ratio | P10 miss 24 h | client P10 misses | P50 late 24 h |
|---|---|---|---|---|---|---|
| before (forecasts reaching into the event) | OFF | 23 % | 1.39 | 1.53 % | 2/203 | 51 % |
| before | ON | 16 % | 0.77 | 1.52 % | 2/203 | 80 % |
| before | ON+P10 | 16 % | 0.77 | 17.65 % | 45/203 | 80 % |
| before | ON+P10, new-pattern rule off | 43 % | 0.20 | **53.92 %** | **147/203** | 80 % |
| event (18 h) | all arms | 1 % | 0.98 | 3.76 % | 1/120 | 76 % |
| after | all arms | 1 % | 0.98 | 7.33 % | 1/138 | 78 % |

The prior takes over at the event: every forecast issued before it switches to physics from
the event hour (`novel_from_s`), and during and after the event the belief is reset or the fire
is rapid, so all arms are physics (identical rows). Without the new-pattern rule the belief
would have carried the calm-days rate into the wind event: area error 43 %, P10 too late on
54 % of cells and 147 of 203 client arrivals. With it and the P10 guard, P10 coverage holds
through the event (ON = OFF). The rule is not complete on its own: in ON+P10 the slowed front
at the event start still leaves 17.65 % late cells — the guard covers that.

**Un-forecast flare-up** (aged fire at 0.03×, jumps to 1.0× at +122 h with no weather change):
the belief is **exposed for 10 hours** — the first report after the jump (acreage at +126 h,
4 h of flare) did not beat the belief's P90; the next perimeter (+132 h) did and reset the fire
to physics. During the exposure, and for every forecast issued in the day before the jump, the
belief under-predicts: forecasts issued in the 26 h before the flare have the P50 late on 88 % of
cells (20 % OFF). With the P10 guard crew safety is unchanged (P10 misses 1.29 % ON = OFF; 71 %
ON+P10, client misses 78/88). After the reset the fire runs as physics (P10 misses 3.84 % ON vs
3.85 % OFF). The alert ladder, not crew safety, carries this exposure — the price of a slower
P50.

**First-pass finding (fixed).** With rapid judged on *raw* observed growth every aged fire was
flagged rapid on 100 % of cycles (report noise alone: a 20,000-acre fire's 3 % is ±600 acres,
so 12-h windows of a fire truly growing ~600 ac/day read 1,400–2,500 ac/day) and the belief could
never slow a big fire. `fire_belief_rapid_noise_z` (1.645) now judges growth beyond noise.

**Engine note (not the belief).** The truth integrates edges piecewise; the forecasts use the
live default frozen departure-hour edge law, which is late-biased (the tri-model review's
finding). That is part of why the physics P50 is late on 88 % of cells even for a fire running
exactly at physics (grow-m1.0 OFF).

### B. Byte-identity (all fire-belief knobs OFF)

`tests/test_fire_belief.py::test_all_knobs_off_reproduce_the_a8bebec_goldens`: the digests of
3 cycles of a perimeter fire whose polygon and acreage change plus a point fire whose acreage
changes (3 clients, a crew, a safe zone, model error 0.71, p50 bound) through the real monitor /
ladder / planner / StateStore / _WatchDaemon / worldview — every CycleResult decision, the
/api/state payload, the persisted state bytes and the /api/world/summary payload — equal the
goldens computed on **a8bebec** before any fire-belief code existed
(`scratch_review/fire_belief_golden.py`), and no `fire_beliefs.json` is written. Tracking ON
reproduces the same cycle and state-file goldens byte for byte (it changes no arrival, ETA,
alert or dispatch); apply ON reproduces them while no evidence exists and for a rapid fire. The
pre-existing W1 seeded goldens and the spread fingerprint / memo-matrix pins pass unchanged.

### C. Test suite

Full suite (`py -3 -m pytest`, serial; pytest-xdist is not installed on this box):
**1993 passed, 12 skipped, 0 failed** (baseline a8bebec: 1920 passed, 12 skipped; +73 in
`tests/test_fire_belief.py`).

## 6. Limits (honest)

- **Whole-fire multiplier vs uneven containment.** The belief is one number per fire; real
  containment is uneven around the perimeter. A mostly-contained fire with one active flank is
  learned slow on average and that flank is under-forecast until a report shows it (then the
  surprise reset). The rapid rule and the reset are the only protection.
- **Acreage re-statement noise.** IncidentSize is re-stated in discrete updates, revised down and
  up, and a record can be modified without a new size; heavy tails, the 3 % + 10 ac error and the
  static rule (3× error, 24 h) absorb it, but a stalled size report can still read as "no growth".
- **Time-scaling approximation.** v(θ) ∝ e^θ assumes arrival times scale 1/m. They do not
  exactly: a slowed fire meets later weather, fuels are uneven, and the lattice keeps up to one
  cell of old progress after a rate change (visible in the study's first day after the hidden
  multiplier drops). The discrepancy term (30 %) carries it.
- **One weather point per fire** — the fire-centre forecast drives spread and the new-pattern rule.
- **Evidence lag between reports** — a flare-up is invisible until the next report (measured).
- **FIRMS hotspots** would shorten that lag (4–6 passes a day) and need a free `FIRMS_MAP_KEY`.
- **Point incidents** are still seeded as a point at now: the belief learns their rate, not their
  extent.
- **The multiplier floor** (0.05) bounds how slow a big contained fire can be forecast: a fire
  whose physics runs 20,000 acres a day is never forecast under ~1,000 a day.
- **The P10 guard keeps crew conservatism.** By design the belief relieves over-warning in the
  ladder (P50) and the map, not the crew deadlines: a contained fire's P10 stays physics. A
  sounder P10 for slow fires needs the front's partial progress (re-anchoring from the burn's own
  arrival times, the simulator's `kept` seam) rather than a rate — a follow-up, not this build.
  The guard runs a second (legacy) ensemble for belief-applied fires only.
- **Alert timing is still a safety-relevant change.** A slower P50 means later NOTICE/WATCH for
  clients of a learned-slow fire; EXTREME still fires on the (guarded) P10 ≤ 12 h rung. During
  an un-forecast flare-up the ladder lags until the surprise reset (measured: 10 h).
- **The synthetic study uses the engine as its own truth** (only the multiplier and the reports'
  noise are hidden); real fires deviate structurally. The next step is a real-fire hindcast over
  WFIGS history (perimeter and acreage series against the engine's forecasts) to calibrate the
  discrepancy, the noise and the rapid thresholds.

## 7. v2: the new-weather rule by day, and a rapid rule that sees big fires (2026-09-28)

Founder, 2026-09-28: *"recalibrate the new-weather rule and fix the rapid blind spot"*. Both
findings come from the real-fire study (`docs/review/belief_vs_physics_2026-09-27.md`: 11 fires
replayed day by day, perfect-forecast weather). Scripts, results and tables are in
`scratch_review/belief_vs_physics/v2_*.py`, `v2_*.json` and `v2_tables.md`. Code:
`wgresponse/fire_belief.py` ("V2 RULES"). Tests: `tests/test_fire_belief_v2.py`. Watch flag:
`--fire-belief-v2`.

### 7.1 In plain words

**What changed.** Two rules decide when the learned rate may *not* be used. v2 recalibrates both.
Both stay off until armed (`--fire-belief-v2`); off, the engine is byte-identical to today.

- **New weather, judged by day.** The rule as built compared every forecast hour with the average
  of the observed hours. The observed hours mix afternoon winds with night winds. In the mountains
  those blow from opposite directions, so nearly every afternoon looked like "new weather". On the
  11 fires the belief was cut back to physics at a median of 13 hours into its forecast. It covered
  the whole next day in only 30 % of forecasts.

  v2 compares whole days in the fire's local time. Each day is described by its worst fire
  weather and its driving wind, the wind blowing during its hottest, driest, windiest hours. A
  forecast day is new weather if it is 1.5 times harsher than the harshest day the fire was seen
  in, or if its driving wind comes from a direction more than 60 degrees away from every observed
  day's driving wind, at 4 m/s or more. These are the old rule's own thresholds, now applied to
  days; the tuning below chose them.

  Now 40 of 243 forecasts see new weather within four days, 17 of them within the next day. The
  belief acts over the whole next day in 93 % of forecasts.
- **Rapid, judged against the real measurement noise.** The rapid rule ignores growth that could
  be measurement error, and it modelled that error as 3 % of the fire's area per report. For a
  big fire that is thousands of acres: at ~80,000 acres, Little Giant's 3,000–5,000 acres a day
  read as noise. It was not flagged rapid at the forecast before its 14,780-acre run, so the
  belief slowed it into the run.

  Measured on the 11 fires, two perimeters of one fire differ by re-mapping of about √(area)
  acres at most (95th percentile of pure-noise changes: 1.6 × √area). That is ~450 acres on an
  80,000-acre fire, not 3,400. v2 uses the measured noise. It also flags a fire rapid when two
  consecutive perimeters both grew at a rapid rate (a backstop). Little Giant is then rapid at
  the Aug 7 forecast, the belief leaves it at physics, and the P50 holds 60 % of the run (15 %
  before).

**What it does, on 11 real fires** (harness maps; every arm keeps the P10 guard):

| | P50 area error (median) | P50 caught the new ground | days the P50 caught under half of it (on growing days) | false-alarm area (median, % of the fire) |
|---|---|---|---|---|
| physics | 3.55x | 99 % | 0 (0) | 253 % |
| belief as built | 2.98x | 94 % | 7 (3) | 198 % |
| **belief v2** | **2.57x** | **95 %** | **9 (3)** | **153 %** |
| stalled days only: physics / as built / v2 | 2.97x / 2.45x / **1.46x** | 99 / 93 / 90 % | 0 / 4 / 6 (all tiny growth) | 195 / 143 / **45 %** |

On maps 20 km wider (closer to a live domain) the all-phase error is 4.90x for physics, 3.55x as
built and **2.68x** with v2. Late days on growing ground are 0, 7 and **4**.

**Crew safety (P10)** is unchanged: 99.8 % of the newly burned ground inside P10 for physics, as
built and v2. The guard (`applied_ensemble`) is untouched.

**What v2 no longer catches.** Two moderate runs were caught by the old rule only because it
handed back to physics every afternoon:
- Willow on Jul 12: +385 ac, 7.6 %/day. The P50 caught 38 % (as built 100 %).
- Timber on Aug 21: +1,994 ac, 28.7 %/day. The P50 caught 19 % (as built 65 %).

Neither day brought new weather, and neither fire had grown rapidly just before. Their P10 held
100 %. EXTREME alerts still fire on the P10 rung, so what these misses cost is later NOTICE/WATCH
alerts on those two days.

**What v2 catches that the old rules missed.** On the harness maps:
- Little Giant's Aug 7 run: 15 % → 60 % (= physics).
- Hagen on Aug 9: 48 % → 100 %.

On the wide maps it also catches Hagen on Aug 6, Little Giant on Aug 27 and Willow on Jul 6.

Willow's Jul 10 flare-up after a slow week is missed by both: 3 % caught, P10 100 %.

**The price of the rapid fix.** Big fires growing 1,000+ acres a day are now flagged rapid even at
1–2 % of their size a day, so the belief leaves them at physics. Rapid flags rose from 80 to 117 of
255 forecasts. Moderate-day accuracy fell a little: 2.92x → 3.10x harness, 3.03x → 3.56x wide.
Grasshopper, Hagen and Little Giant got slightly worse per fire. Every other fire got better,
Willow most (5.6x → 1.55x).

**Recommendation.**
- **Tracking with v2:** yes. It changes no forecast and gives better rapid flags and new-pattern
  times. Rapid flags feed the road rule (SEMANTICS 15) when `--offmap-per-fire` is armed; v2
  flags more fires rapid, which is the conservative direction there.
- **Apply:** never with the as-built rules. With v2, it is the first version worth applying. It
  beats the belief as built on nearly every aggregate and fixes its biggest miss. What remains is
  a late NOTICE/WATCH on un-forecast runs of learned-slow fires: 3 of 120 growing or moderate days
  here, against 0 for physics. Physics pays for that with a false-alarm area of two to five times
  the fire.
- **Before apply goes live:** a short live shadow period with `--fire-belief --fire-belief-v2`.
  The daily rule has only been tested on observed weather, and live it will judge NWS forecast
  days. That is the founder's call.

### 7.2 The rules as built (`fire_belief.py` "V2 RULES")

- **Daily new-weather test** (`fire_belief_novelty = "daily"`, `FireBeliefStore._novelty_daily`).
  - *Days.* Days are local solar days, with the offset from the forecast point's longitude.
  - *Evidence days.* The logged hours that overlap an evidence window, grouped by day. Each
    weighs as its heaviest window.
  - *Day summary.* Each day's peak severity is the engine's own Rothermel head ROS for the
    dominant fuel. Its DRIVING hours are those at ≥ `DAY_DRIVE_FRAC` (0.5) × that peak. The
    driving wind is their mean speed, speed-weighted circular mean direction and resultant
    (`_day_summary`).
  - *Reference.* The weighted P90 of the evidence days' peaks.
  - *Sectors.* The evidence days that could drive the fire (peak ≥ 0.5 × reference), with a
    meaningful (≥ min wind) and steady (resultant ≥ 0.3) driving wind, each give a sector.
  - *New day.* A forecast day is new if its peak > ratio × reference. It is also new if the day
    can drive the fire and its steady, meaningful driving wind is > shift° from every sector, or
    there are no sectors at all.
  - *Current day.* The current day's summary includes its logged hours before now.
  - *Where the prior starts.* From the first qualifying hour of the first new day to the end of
    the horizon: an hour over the severity threshold, or a driving hour with a new-sector wind.
    If the day's driving hours are already past, from hour 0.
  - *Unchanged.* No evidence, no logged weather and no fuel give the hourly rule's answers.
- **Calibrated rapid noise** (`fire_belief_rapid_noise = "calibrated"`). For PERIMETER windows,
  sd(growth) = hypot(k√A, √2 × `fire_belief_area_abs_err_acres`) acres, with A the larger end and
  k = `fire_belief_rapid_perim_noise_k` (`perimeter_growth_sd_acres`). Reported windows keep the
  legacy sd, since no real-fire data calibrates them. The z (1.645), the thresholds and the young
  fire rule are unchanged.
- **Persistence** (`fire_belief_rapid_persist_windows = N ≥ 2`, `_persistent_growth`). N
  consecutive perimeter windows (each starting where the previous ended), each growing RAW at ≥
  `fire_belief_rapid_acres_per_day` or at ≥ `fire_belief_rapid_frac_per_day` with ≥
  `fire_belief_rapid_min_acres`, with the latest ending inside the lookback, make the fire rapid.
- **Byte identity.** Defaults are `hourly` / `area_frac` / 0: the legacy code path, untouched.
  - The a8bebec goldens (`test_all_knobs_off_reproduce_the_a8bebec_goldens`) pass unchanged.
  - With the v2 knobs SET and tracking off, the cycles, `/api/state`, state bytes and world
    summary equal the goldens.
  - With tracking on plus v2, every cycle decision and the state file equal the goldens.
  - Apply plus v2 on the pinned rapid fire reproduces the golden cycles.

  All four are in `tests/test_fire_belief_v2.py`. The P10 guard (`applied_ensemble`) and the
  observation code are untouched.
- **Test suite** (serial, `py -3 -m pytest`, 2026-09-28): **2262 passed, 12 skipped, 0 failed**.
  That includes the 27 new tests in `tests/test_fire_belief_v2.py`; `tests/test_fire_belief.py`
  and `tests/test_docs.py` are unchanged and pass.

| Knob | Default | v2 (`--fire-belief-v2`) |
|---|---|---|
| `fire_belief_novelty` | hourly | daily |
| `fire_belief_novel_day_severity_ratio` | 1.5 | (as default) |
| `fire_belief_novel_day_shift_deg` | 60.0 | (as default) |
| `fire_belief_novel_day_min_wind_mps` | 4.0 | (as default) |
| `fire_belief_rapid_noise` | area_frac | calibrated |
| `fire_belief_rapid_perim_noise_k` | 1.0 | (as default) |
| `fire_belief_rapid_persist_windows` | 0 | 2 |

### 7.3 Calibration

**Perimeter noise** (`v2_noise.py` → `v2_noise.json`).
- *Data.* Every capture the live belief would have observed on the 11 fires (watcher-order
  replay, ≥ 3 h apart): 347 consecutive pairs.
- *Pure noise.* A fire does not shrink, so the 27 negative changes are pure noise. They measure
  |ΔA|/√A = 0.24 (68th percentile) and 1.62 (95th).
- *Stalled tails.* On 101 pairs within 2 % of the final size, |ΔA|/√A is 0.26 (68th) and 1.11
  (95th). It is flat across a 25× range of fire size (0.21–0.28 in the 5k–20k, 20k–60k and
  > 60k bins). The same changes as a fraction of the area vary 7× (0.4 % → 0.06 %). The noise
  follows the edge, not the area.
- *The chosen k.* k = 1.0 gives z × k = 1.645 √A, covering the 95th percentile of pure noise.
  The legacy model equals 12 √A at 80,000 acres and 17 √A at 160,000.

**The daily thresholds** (`v2_eval.py grid` / `lofo` → `v2_grid.json`, `v2_lofo.json`).
- *Grid.* 48 threshold sets: ratio {1.25, 1.5, 2}, shift {45, 60, 90, 120}°, min wind {3, 4, 5,
  6} m/s, each with the v2 rapid rule, replayed on all 11 fires. Every belief-applied forecast
  was scored (cached per step, first new hour and rapid flag).
- *Leave-one-fire-out.* For each fire, the set was chosen on the other 10: the lowest median P50
  area error with no more late-P50 days on growing ground (≥ 2 %/day) than the belief as built
  on the same fires. It was then scored on the held-out fire.
  - Shift 60° was among the best sets in 9 of 11 folds (120° in 2).
  - Ratio 1.5 in 10 (2.0 in 11; they tie).
  - Min wind 3, 4 and 5 m/s tied in all 11 folds, so the data cannot tell them apart and the
    hourly rule's 4 m/s is kept.
- *Held-out result* (each fire scored under the set chosen without it):

  | phase | P50 area error, physics → v2 | P50 catch | late on growing ground | false alarm |
  |---|---|---|---|---|
  | growing | 5.56x → 5.32x | 97 % | 2 | 454 → 431 % |
  | moderate | 4.14x → 3.10x | 97 % | 2 | 311 → 209 % |
  | stalled | 2.97x → 1.46x | 90 % | 0 | 195 → 45 % |
  | all | 3.55x → 2.57x | 95 % | 4 | 253 → 153 % |
- *Sensitivity.* Over all 48 sets: area error 2.54–2.63x, late-P50 days 9–10 (3–4 on growing
  ground), false alarm 152–159 %. The thresholds barely matter. On these fires a genuinely new
  weather day was rare, and the daily view is what matters.

### 7.4 Results

Full tables: `scratch_review/belief_vs_physics/v2_tables.md` (from `v2_final.json`,
`v2_final_wide.json`). Figures: `docs/review/belief_vs_physics/v2_pooled.png` and `v2_willow.png`.

**Rapid flags** (`v2_rapid.py` → `v2_rapid.json`). The flag is taken at each daily forecast time
and judged against the next day's observed phase.

| rapid rule | growing days flagged | moderate | stalled days flagged | … of them grew ≥ 1,000 ac/day |
|---|---|---|---|---|
| legacy | 52/72 (72 %) | 25/55 (45 %) | 9/149 (6 %) | 1/10 |
| v2 (calibrated + persistence) | 55/72 (76 %) | 39/55 (71 %) | 29/149 (19 %) | 10/10 |

- *The stalled-day flags.* Every v2 flag on a stalled day follows real growth of ≥ 1,000 ac/day in
  the 48-h lookback, or a big fire creeping at ≥ 1,000 ac/day. No contained fire's re-mapping noise
  was flagged, so what the noise discount cured stays cured: on 166,000 acres ±400 acres is never
  rapid.
- *Against the recent window.* v2 flags 98 % of forecasts whose last day's growth really met the
  rapid thresholds (legacy 72 %). It flags 11 % of the others, through the 48-h lookback (legacy
  6 %).
- *Every perimeter observed.* On the live-like series (every capture ≥ 3 h apart) the rates are
  81 / 71 / 22 %.
- *Little Giant.* The legacy rule was rapid on Aug 4–6 (from the early-August runs) but NOT at the
  Aug 7 22:33 forecast. There, the last two windows (+3,225 and +5,249 ac) read zero beyond the
  area-fraction noise. v2 is rapid there.
- *Persistence.* It changed no flag on these fires; the calibrated noise already sees every such
  run. It is kept as a backstop for when the noise model is wrong, where it adds flags.

**Named cases** (P50 catch of the newly burned ground):

| case | physics | as built | v2 |
|---|---|---|---|
| Willow Jul 12 (+385 ac) | 100 % | 100 % (new weather, hour 10) | 38 % (slowed all day) |
| Willow Jul 14 (+350 ac) | 100 % | 77 % (new weather, hour 9) | 75 % (new weather, hour 13) |
| Grasshopper Aug 4 (+4,780 ac) | 100 % | 99 % (new weather, hour 9) | 100 % (rapid) |
| Little Giant Aug 21 (+1,839 ac in 10 h) | 98 % | 98 % (new weather, hour 0) | 98 % (rapid) |
| Timber Aug 21 (+1,994 ac) | 99 % | 65 % (new weather, hour 5) | 19 % (slowed all day) |
| Little Giant Aug 7 (+14,780 ac) | 60 % | 15 % | 60 % (rapid) |
| Hagen Aug 9 (+3,111 ac) | 100 % | 48 % | 100 % (rapid) |
| Willow Jul 10 (+852 ac) | 100 % | 3 % | 3 % |

Where v2 misses (Willow Jul 12, Timber Aug 21), no day-level signal existed. Timber's Aug 21-22
peaks were 1.0× the harshest evidence day. Its driving wind (301°) was 33° from the evidence days'
sectors (209–268°), so no shift above 33° would call it new, and the hourly rule's catch was its
generic afternoon cut.

### 7.5 Limits (v2)

- **Perfect-forecast weather.** The daily rule was judged on observed days. Live, it judges NWS
  forecast days, and a mis-forecast wind shift is invisible to it until the next perimeter or
  surprise.
- **The rapid threshold is absolute (1,000 ac/day).** With the noise now measured, big fires
  creeping at 1–2 %/day count as rapid and keep physics. Whether 1,000 ac/day on a 150,000-acre
  fire is "rapid" is a policy choice for `fire_belief_rapid_acres_per_day`, not a noise question.
- **The likelihood's area noise** (3 %, `_complete`, observation code) is left alone: it would
  change the learning, and it sits in the code another session is hardening. The same
  measurement suggests it is also over-stated for perimeters, which is why big stalled fires
  learn slowly.
- **11 fires, mostly timber; one hindcast weather point per fire; whole-fire multiplier.** All
  the first study's limits apply.
