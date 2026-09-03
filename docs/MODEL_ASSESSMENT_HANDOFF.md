# WG Response — Model Assessment Handoff

Audience: an external reviewing agent (AI or human) assessing this fire-spread
/ dispatch model cold. Everything here is reproducible from this repository;
no prior conversation context is assumed. Written 2026-08-31.

## 1. What this system is

`wgresponse` (pure Python, no third-party runtime deps) is operational
software for GraphGel wildfire response crews: live fire feeds (WFIGS/FIRIS)
→ Rothermel/Albini spread forecasts on real LANDFIRE fuels → per-client
threat ETAs → a 72h/24h alert ladder → crew dispatch under hard safety laws
(30-min gel deploy, crews clear ≥30 min before pessimistic fire arrival,
corridor checks on every travel leg). Three runtime surfaces share one
engine:

- **Live watcher** (shadow ops, dry-notify, mock clients):
  https://y372zmpfuj.us-west-2.awsapprunner.com/console — SoCal AOI.
- **Public simulator**: https://m897yqgsmt.us-west-2.awsapprunner.com/simulator
  (6 real-terrain locations; one-click scenarios incl.
  `?scenario=rancho_santa_fe_santa_ana`).
- **Benchmark harnesses**: `scratch_review/` (committed with their result
  JSONs).

`wgresponse/contracts.py` is the law: every type, unit, safety semantic, and
config knob, with provenance comments. Read it first. `README.md` carries a
guarded config table (tests enforce a row per knob). ~1,240 tests; the
byte-identity discipline is that every behavioral knob defaults OFF and the
default path is fingerprint-pinned.

## 2. Model architecture (assessment-relevant summary)

- **Spread**: Rothermel surface ROS (FBFM40 fuel table pinned in
  `fuels.py` with provenance; wind uncapped per Andrews/Cruz/Rothermel 2013
  — deliberately conservative-fast), Albini-style arrival-time integration
  over a 16-neighbor lattice (`spread.compute_arrival`), 90 m cells (some
  large hindcast domains coarser). Byram intensity/flame length are computed
  per cell-hour but only arrival TIMES are persisted.
- **Ensemble**: N members with lead-scaled perturbations (wind ±25 %,
  direction ±30° at max lead), optional Sardoy spotting (feeds P10).
  Quantile rasters P10/P50/P90; **P10 (earliest credible arrival) drives all
  crew-safety math**.
- **Weather**: NWS gridpoint live / Open-Meteo ERA5 archive for hindcasts;
  effective wind = sustained + `wind_gust_blend`×(gust−sustained).
- **Alert ladder** (`alerts.py`): NOTICE_72H (P50≤72h) → WATCH → EXTREME_24H
  (P50≤24h or P10≤12h), hysteresis on downgrades; an armed "extreme spread
  regime" (cured-grass domain + wind) evaluates stages on P10.
- **Dispatch** (`dispatch.py`): count-max ILS over feasible (deadline,
  ingress-corridor, egress-corridor) stops; corridor law samples eroded-P10
  along real road polylines; plan-on-roads travel.
- **Crew safety (SEM-10)**: continuous per-tick sweep — relocation, escape
  rays, cold-black passability, plus a carve-out-free audit (any crew on a
  front-passed cell reports, always).

## 3. As-deployed configuration

Live watcher StartCommand (verify via `aws apprunner describe-service`,
service `wg-response-watch`, us-west-2), image tag `fix1` (2026-09-03,
fix program docs/FIX_PROGRAM_2026-09-03.md): `--gust-blend 0.5
--grass-floor 0.10 --extreme-regime --fail-closed --ember-radius 200
--p50-bound`. The floor-physics and model-error flags (`--floor-gate
--grass-only --model-error`) follow the hindcast calibration matrix. Engine
`ResponseConfig` defaults keep every calibrated knob OFF. The public
simulator additionally defaults (sim sessions only): fuel-tiered corridor
erosion (FAST 550 m GR/GS, SHRUB 200 m, SLOW 150 m, euclidean discs),
escape-target hysteresis 20 min, per-fuel-class cold-black windows,
proactive pre-pocket escape 120 min, corridor fail-closed (off-raster
bound only — the tile is the sim's world), `deploy_min` 15 (3-person
crews), ember_threat_radius 200 m, spotting + grass floor on. The 10c
crew-overrun audit counts imminent exposure in every crew mode (both
paths). Previous lineage: `gf1` = `--gust-blend 0.5 --grass-floor 0.10
--extreme-regime` (the configuration the tri-model review assessed).

## 4. Validation record (the numbers under review)

**Reality anchor — hindcast vs real fires.** Method: WFIGS Daily-perimeter
snapshot pairs (T0→T1) replayed on LANDFIRE LFPS terrain with ERA5 archive
weather; interval-censored scoring per newly-burned burnable cell:
`covered_possible` = [P10,P90] intersects [T0,T1]; `late_miss` = P10 > T1
(the dangerous polarity); `early_miss` = P90 < T0. Cell-count-weighted
pooling. Suppression is unmodeled → over-prediction is the safe polarity for
an alerting product.

| Era | Windows / cells | Config | Coverage | Late | Early | Med head-ratio |
|---|---|---|---|---|---|---|
| Calibration (2025 CA fires) | 14 / 60k | blend 0.5 | 73.5 % | 26.5 % | 0 % | 1.77× |
| Calibration | 14 / 60k | + grass floor | 91.5 % | 8.5 % | 0 % | 2.78× |
| July 2026 OOS (UT/OR/WA) | 8 / 60k | blend 0.5 | 48.6 % | 51.4 % | 0 % | 1.26× |
| July OOS | 8 / 60k | + floor | 64.8 % | 35.2 % | 0 % | — |
| July OOS, excl. one blow-up | 7 / 26k | + floor | ≈100 % | ≈0 % | 0 % | — |
| **Aug 2026 OOS (6 states)** | **18 / 135k** | **as-deployed** | **93.8 %** | **6.2 %** | **0 %** | **1.88×** |

Named failure case: Porcupine Ridge OR (2026-07-17), 22.5 km in 12.9 h
observed vs 1.7 km modeled median (13× under) — grass/shrub blow-up;
motivated the grass floor + the extreme-regime posture. August's worst pair:
Bug-CA 10.2 km/9.5 h at 0.69× (54.8 % late), under the blow-up threshold.
Per-fuel late (Aug, deployed): GR 3.7 / GS 5.3 / **SH 13.5 (current worst)**
/ TL 3.0 / TU 0.2 %. **Early-miss is 0.0 % in every pool ever scored.**
Point anchors: Tubbs 0.46× (spotting off) / 1.24× (on); Camp ~0.01–0.12×
(regenerate: `py -3 tools/hindcast_harness.py anchors --data-dir
tools/hindcast_data`).

**Simulator corpus**: 8,080 scenario simulations / 336,687 home-instances
(census: `scratch_review/census_report.json`). Key measured results —
survivability-vs-wind (belt = coated among homes within 1 km of actual
burn): 8 mph ≈88 %, 18 ≈80–84, 28 ≈61–69, 40 ≈42–45; lead-time curve
(survival among truth-reached homes): 0 % <1.5 h lead → ~33 % @4 h → 50–63 %
@10–14 h; crews saturate at ~10 (zero capacity-blocked homes at any fleet
size — the wall is `infeasible_deadline`); perfect-information ceiling +22–26
pp but naive P10-collapse produces crew violations (band is load-bearing);
Rancho Santa Fe 100-home city-limits plan of record 74–78 % with crew
scaling restored at density (docs/rancho_santa_fe_plan.md).

## 5. How to reproduce / assess independently

Environment: Windows, `py -3` (3.14), `PYTHONIOENCODING=utf-8`; engine is
dependency-free. Prefix shell work with the repo cd. Benchmarks:
`PYTHONHASHSEED=0` by convention.

- Full suite: `py -3 -m pytest tests/ -q` (must be green; default-path pins).
- Hindcast replay (offline, committed data):
  `py -3 tools/hindcast_harness.py replay --offline --data-dir tools/hindcast_data --gust-blend 0.5`
  and the same against `scratch_review/hindcast_oos_data` /
  `scratch_review/hindcast_aug_data` (their `lfps/` tile caches are
  gitignored ~60–166 MB — refetch via the committed
  `scratch_review/*_fetch*.py` patterns, live LANDFIRE/ERA5/WFIGS).
- Custom-config replays (floor variants): pattern in
  `scratch_review/oos_realism.py` / `aug_hindcast_replay.py`.
- Benchmark suites: `py -3 scratch_review/opt_ember.py --tag X --suite
  screen|full --workers 10 [flags per README rows]`; pooled stats are
  self-computed and every cited table's JSON is committed.
- Fresh-month scoring is automated: scheduled task
  `monthly-livefire-model-review` (1st of month; procedure documented in its
  SKILL.md under `~/.claude/scheduled-tasks/`).

**Nondeterminism caveat**: the dispatch ILS stops on a wall-clock budget
(`solver_budget_s`, `time.monotonic`) → identical runs differ by ±1 belt
home / ±6 coated on ~10 % of scenarios. Treat sub-0.5 pp deltas as noise;
`min_margin_h` is a final-tick snapshot, not a fine metric. (Fix in flight.)

## 6. Known defects & limits (do not rediscover — challenge)

Full ledger: `docs/calibration_review_2026-07-31.md` (trust grades per
number). Highlights: gel product model is the only BLOCKER-grade unknown
(supplier data pending; 72 h protection window vs ~6 h alternative = 12×
conflict; `gel_effectiveness` 0.85 is a placeholder); `circuity` 1.35
contradicted by measured 1.96–2.43 (absorbed by chord margin; pair must move
together); smoke/visibility unmodeled (historically the real evacuation
killer — all transit verdicts are thermal-only); raster ±45 m quantization;
flame lengths wind-uncapped; arrival uses floored ROS while flame length
uses unfloored (documented, deliberate); ~37 watcher instance restarts in
Aug (ephemeral /tmp state — production persistence blocker); ember-radius
0 in the live engine leaves developed-cell homes (63 % at RSF) invisible to
the ladder until raised; WFIGS supplies garbage rows (future dates, stray
years, non-monotone complexes) — guards exist, stay vigilant; extreme-regime
thresholds fitted on n=1 true positive; escape hysteresis validated only as
harmless; sub-30 mph regime was unscreened until 2026-08-01 (pre-existing
idle-crew response gap found there; fix in flight).

## 7. Suggested adversarial checks for a reviewing agent

1. Re-derive every pooled table in §4 from its committed JSON (paths above)
   — the arithmetic should reproduce exactly.
2. Byte-identity: run the suite; then flip each default-off knob and confirm
   the suite CATCHES it (mutation-style check of the pin discipline).
3. Challenge the hindcast semantics: interval-censoring, cell-weighting,
   incident selection (rows-biased discovery), ERA5-vs-NWS gust character.
4. Challenge the band: P10/truth ratios ~0.30–0.64 at 3 h leads; is 0.5
   gust-blend + ±25 %/30° the right width given 0 % early / 6-51 % late?
5. Physics spot-checks: Rothermel tables vs RMRS-GTR-153; Byram L=0.0775·I^0.46;
   Anderson t_r=384/σ (cold-black windows derive from it).
6. Safety-law audit: prove (or refute) that no code path weakens the 10c
   audit or the P10 egress law when flags are on.
7. The stated falsifiable prediction for the unbuilt transit-hazard stage 2
   (docs/transit_hazard_determination.md §validation): Paradise relief from
   flux term, Malibu from cold-black term.

Key docs: `docs/calibration_review_2026-07-31.md`,
`docs/transit_hazard_determination.md`, `docs/calibration/hindcast_*.md`,
`docs/rancho_santa_fe_plan.md`, `README.md` config table, `git log` for the
dated ship record.
