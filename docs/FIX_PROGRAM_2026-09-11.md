# Fix Program 2 — 2026-09-11: the recommended fixes left open on 2026-09-03

Founder order: "perform the recommended fixes." The open recommendations
after `docs/FIX_PROGRAM_2026-09-03.md` were the review's held items
(edge-weather piecewise integration, φ-vector slope/wind combination, solver
determinism), the live-ops leftovers (durable state off `/tmp`, the cycle
budget) and the calibration verdict's own recommendation (let the
extreme-regime posture carry the blow-ups so the honest band can run
elsewhere). Three items are not code and were NOT acted on: closing
`WGRESP_DEV_OPEN` on the public console (product decision), weather
licensing (Open-Meteo's free tier is non-commercial), and gel supplier data.

Discipline unchanged: every engine change is a default-OFF `ResponseConfig`
knob (byte-identical when off, pinned by the legacy fingerprints); the
simulator preset and the watcher flags arm what the measurements justify.

## 1. Solver determinism  (BUILT; sim armed at 20,000; watcher `--solver-evals`)

`solver_budget_s` was a wall clock (`time.monotonic()` at every insertion
scan, local-search pass, ILS iteration and every 256th exact-DFS node), so
a loaded machine cut the ILS at a different point — the review's measured
±1 belt home between identical runs. `dispatch._Budget` now threads every
check: `solver_budget_evals > 0` makes the budget a COUNT of consults, so
identical inputs stop at the identical consult regardless of load; a 10×
wall-clock runaway guard remains and `objective_breakdown` reports
`budget_mode / budget_spent / budget_runaway`.

Calibration (`scratch_review/w9_budget_calib.py`, wall clock, no load):

| scenario | consults per plan | solver_s |
|---|---|---|
| boulder_co 16 clients / 2 crews | 4 – 61 | ≤ 0.01 |
| paradise_ca 40 / 8 | 13 – 870 | ≤ 0.19 |
| rancho_santa_fe 100 / 10 | 14 – 2,008 | ≤ 2.29 |

No unloaded plan reached the 5 s clock; the nondeterminism was purely a
load artefact. The simulator preset arms 20,000 consults (10× the largest
natural spend) so every plan completes deterministically; plans are pinned
identical run-to-run in `tests/test_fix_program_2.py`.

## 2. Regime-conditional floor  (BUILT; calibration below)

`extreme_regime_floor_frac`: when an incident's domain trips the extreme
spread regime detector (cured-grass fuelscape + wind — the posture that
already moves the ladder onto P10), that incident spreads with the blow-up
floor on every floored class; all others keep the calibrated band.
`threat.regime_spread_cfg` is the single rule, used by the live watcher and
by the hindcast replay (which notes each regime pair). Watcher
`--regime-floor 0.10` (requires `--extreme-regime`).

## 3. Edge-weather piecewise integration + φ-vector slope/wind  (BUILT; calibration below)

`edge_weather_piecewise`: a traversal crossing an hour boundary continues
with each subsequent hour's rate for the distance still unburned (the
frozen departure-hour rate was late-biased: 46-min constant offset in a
step case, timber flank edges spanning 25 h on one weather hour).
`slope_phi_vector`: the max-spread magnitude/bearing and the effective wind
come from the Rothermel/FARSITE φ-factor vector sum instead of the speed-
space sum (−11 to −33% too slow on opposed slopes — the downslope Santa Ana
geometry). Both byte-identical when off (`tests/test_spread_physics2.py`).

## 4. Cycle-phase telemetry  (BUILT; live)

`CycleResult.timings` and `state()["last_cycle_phases"]`: feeds / assess /
alerts / dispatch seconds plus per-incident terrain / weather / spread
seconds, cells and clients — the breakdown the 20.8-minute live cycle
never had. The cycle-budget decision is made from these numbers (below).

## 5. Durable state off `/tmp`  (BUILT; infra below)

`StateStore(dir, s3_uri=...)` and `LfpsTerrain(cache_dir, s3_uri=...)`
mirror the alert ladder / notified ledger / incumbent plan and the LANDFIRE
tile + zip cache to S3 (loud-never-fatal; download on a fresh container).
Watcher env `WGRESP_STATE_S3=s3://bucket/prefix`. See
`docs/DURABLE_STATE.md`.

## Measurements

### Regime-conditional floor (matrix round 2, `scratch_review/calmatrix2_run.sh`)

Rule as on 2026-09-03: honesty (PIT extreme mass) subject to late-miss no
worse than the deployed floor on BOTH corpora. J = the honest band (half
floor 0.05, gated, GR/GS only, model-error 0.71) + blow-up floor 0.10 on
regime incidents; L = J without the band.

| variant | Aug adeq / late / area / PIT / 6-12 h | July adeq / late / area / PIT / 6-12 h | regime pairs |
|---|---|---|---|---|
| D gate + band 0.71 (deployed) | 98.8 / 1.2 / 24.7× / 50% / 94% | 83.1 / 16.9 / 14.2× / 72% / 99% | — |
| I honest band (no regime) | 94.1 / 5.9 / 11.0× / 40% / 69% | 68.0 / 32.0 / 10.4× / 67% / 62% | — |
| **J = I + regime floor 0.10** | 95.9 / 4.1 / 14.2× / 48% / 79% | 71.1 / 28.9 / 10.4× / 67% / 62% | Aug 13/27, **July 2/18** |
| L = J without the band | 91.1 / 8.9 / 7.0× / 80% / 60% | 65.7 / 34.3 / 6.9× / 90% / 56% | same |

**Verdict: the regime floor stays built but OFF; D071 stays deployed.** The
detector (cured grass ≥ 0.60 of the burnable domain + wind ≥ 4 m/s, built
from n=1 positive example) trips on only 2 of the 18 July pairs, so the
posture does not carry the July blow-ups — the premise of the 2026-09-03
recommendation does not hold yet. Per-pair diagnosis (grass fraction, max
effective wind, cells; late-miss D vs I):

| July pair | dt h | GR+GS | max wind m/s | regime | cells | late D | late I |
|---|---|---|---|---|---|---|---|
| PorcupineRidge-OR | 12.9 | 0.88 | 9.0 | **yes** | 33,888 | **53.1%** | 62.8% |
| AspenAcres-CO | 7.4 | 0.60 | 11.7 | no (on the threshold) | 11,003 | 3.8% | **86.9%** |
| Willow-CO (timber) | 20.8 | 0.07 | 7.4 | no | 1,231 | 0% | **62.6%** |
| Cottonwood-UT | 8.2 | 0.49 | 10.2 | no | 7,886 | 0% | **34.3%** |
| every other pair | | | | no | | 0% | ≤ 6% |

Three facts: (1) the one true regime pair is beyond every variant — 53%
late even with the full floor — which is what pins July skill at ≈1.0;
(2) the half-floor's July losses are one grass domain sitting exactly on
the 0.60 threshold under strong wind and two non-grass domains where the
ALL-class floor is doing the work; (3) re-tuning the detector on these 18
pairs would be fitting the out-of-sample test. The detector signature
needs more blow-up windows (the monthly review accumulates them) before
the honest band can run outside the regime. Raw results:
`scratch_review/calmatrix/{aug,oos}_{J_regime_I071,L_regime_I_noband}.json`.
