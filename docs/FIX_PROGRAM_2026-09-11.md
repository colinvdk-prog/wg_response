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

(filled in as the runs complete)
