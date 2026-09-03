# Fix Program 2026-09-03 — acting on the tri-model review

Founder order (2026-09-03): "improve the model with the feedback." The
feedback is the ranked fix program in `docs/TRI_MODEL_REVIEW_2026-08-31.md`.
This document is the record of what was built, what was measured, and what
was deliberately left for a decision. Every engine change is a default-OFF
`ResponseConfig` knob (byte-identity preserved for every existing pin) that
the simulator preset and/or the live watcher arm explicitly; the two
exceptions are bug fixes named as such below.

## Item 1 — corridor fails closed  (BUILT; sim armed; watcher via `--fail-closed`)

`safety.LegOracle` skipped corridor samples that fell off the spread raster
or read UNREACHED; "no bound" was read as safe (4.0% false-clears over 720
geometries, worst 7.85 h; egress legs 24-84% unsampled; truncation
manufactured the UNREACHED value the law trusted).

Built: `corridor_fail_closed`, `corridor_unreached_bound`,
`corridor_offraster_ros_mps` (1.7 m/s). An off-raster sample is BOUNDED by
the earliest arrival the fire could carry to it through a boundary cell it
actually reaches — `min over reached boundary runs of (run's earliest
arrival + distance-to-run / ros_max)` — a rigorous lower bound (never
later than the exact per-cell minimum; at most one run length ≈ 7 min
earlier at 100 m cells). No reached boundary cell (fire contained in the
domain) keeps the legacy skip. A leg with no raster at all is unverifiable
= unsafe. `corridor_unreached_bound` applies the same bound to in-domain
UNREACHED samples (the truncation case) — armed on the LIVE path only: the
simulator's tile is its whole world, so an in-tile UNREACHED cell is
honestly unreached there.

Tests: `tests/test_corridor_fail_closed.py` (12; legacy skip pinned
byte-identical; rigor property-tested against the exact per-cell minimum
on random boundaries; truncation-wall case; no-raster case; run memo).
Sim-level A/B (quick suite, 8 crews): see §Measurements.

## Item 2 — moisture-of-extinction gate on the ROS floor  (BUILT; calibration pending)

`spread._pack_params` applied `head_ros = max(rothermel, frac*U10)` with no
moisture gate, so fuel Rothermel says is OUT (dead moisture at/above the
fuel's moisture of extinction, ros 0) walked at 3.6/2.5 km/h with zero
intensity and zero flame (39.4% of cached August fire-hours); the "grass"
floor also lifted TU/TL timber 19-45× Rothermel.

Built: `ros_floor_extinction_gate` (the floor only ever RAISES a positive
Rothermel ROS; Byram intensity scales with the raised ROS so the spotting
seam sees the same fire the front does — `spread._floored`) and
`ros_floor_grass_only` (floor GR/GS only). Bug fix without a knob: a
zero-ROS cell never relaxes a neighbour (the 1e-4 m/s traversal floor put
an "out" cell 83 h away at 30 m cells — inside the 96 h horizon). Labels
corrected: the floor is LIVE on the watcher, not "sim-only".

Tests: `tests/test_ros_floor_physics.py`. Which variant the watcher runs
is decided by the hindcast matrix (§Measurements), not by argument.

## Item 3 — ensemble honesty  (BUILT; calibration pending)

The input perturbations are damped to the horizon mean (realized wind
spread ±12.5% at any member count), so the band was 8× narrower than the
measured head-ratio error and p10 was in effect the sample minimum.

Built: `model_error_sd_log` — every perturbed member also scales its head
ROS by `exp(sd · z_k)`, `z_k` the stratified standard-normal quantile of a
FOURTH Latin-hypercube dimension drawn after the three legacy ones (their
strata stay byte-identical; the middle stratum reproduces the nominal run
byte for byte). Median-unbiased in log space; the nominal member never
carries the term; the p10 ≤ nominal clamp still holds. The value is the
hindcast PIT calibration's (§Measurements). `ensemble_p50_nominal_bound`
(review N4): p50 = min(Hazen p50, nominal) so the ladder's median never
trails the engine's own best estimate (the rule p10 already obeyed); armed
via `--p50-bound`. The ×0.5 damping is now documented in contracts rather
than removed (removing it would move every pinned ensemble).

## Item 4 — the 10c audit matches its contract  (BUILT; sim tests re-anchored)

The imminent branch of the crew-overrun audit required `_mode == "idle"`:
106 crew-ticks in the 64-scenario sweep (2 under the shipped preset, worst
5.6 h past eroded-P10, all in egress mode) never entered the count, and the
only guarding test pinned the zero-exposure case.

Built: the exemption is gone — burned and imminent both count in every
crew mode; rows keep `mode` for attribution. SAFETY SEMANTICS 10(c)
amended in `contracts.py`. Tests: `tests/test_audit_exposure.py` (imminent
in idle/egress/en_route/deploying; burned; clear; the review's 5.6 h case).
Three existing sim tests went 0 → 1 violation and were re-anchored to
their real subject (serviced-stop departure audits) with the overrun rows
asserted separately — the exposures they now surface are genuine and are
listed under §Sweep exposures for the SEM-10 work.

## Item 5 — two-sided scorecard  (harness rewrite + calibration matrix)

`tools/hindcast_lib.py` / `tools/hindcast_harness.py` rewritten by the
scorecard task (see `docs/calibration/SCORECARD.md`): true medians,
late-miss, p10-adequacy (the honest name for the old coverage), area
ratios, PIT summary, null-model dilation baseline + skill, window-length
bins, incident counts, equal-incident mean, leave-one-incident-out;
early_miss / covered_certain retired from the narrative. Harness variant
flags (`--floor-gate --grass-only --model-error --p50-bound --out
--label`) drive the calibration matrix. `scratch_review/opt_ember.py`
gained `--legacy-stack` (pins every sim-auto knob OFF — the A9 wiring
trap) and `--fail-closed`.

## Item 6 — live hardening  (PARTIAL)

Built: `--ember-radius` on the watcher (the 63%-of-WUI-homes detection
hole), cadence telemetry (`last_cycle_duration_s`), a staleness SLO
(`cycle_stale` when the last cycle is older than 3 poll periods —
surfaced on `/healthz` and as a console banner, deliberately NOT a 503:
state lives on local disk and a restart storm on a merely slow loop would
silence alerting), `validate_config` hardened and now run at watcher
startup (a daemon refuses to start on a config that disables a law).
Left for the founder (product/infra decisions): durable state off `/tmp`
(S3-backed StateStore), closing `WGRESP_DEV_OPEN` on the public console
(the console link was made public on purpose), cycle-budget for the
solver, weather licensing (Open-Meteo's free tier is non-commercial).

## Item 7 — held, unchanged

Gel supplier data (any survivability claim), edge-weather piecewise
integration, φ-vector slope combination, solver determinism.

## Sweep exposures the honest audit now surfaces (for the SEM-10 chips)

- 600× coarse ticks: an EGRESSING crew standing in a cell whose eroded-P10
  passed ~135 h earlier while the nominal front never arrives (phantom-P10
  class; `tests/test_simui_policy.py::test_speed_600x_coarse_jumps_audit_still_clean`).
- 90-min buffer: a DEPLOYING crew whose cell's eroded-P10 is 73 min out
  (nominal 439 min) — a late withdrawal
  (`tests/test_simui.py::test_buffer_binds_unserved_reasons`).
- The homes-mode rug-pull test's victim crew reads imminent one tick
  before the tightening fires (by construction).

## Measurements

### Sim-level A/B (opt_ember quick suite: 5 scenarios, 8 crews, 40 clients; PYTHONHASHSEED=0)

| run | front | e1000 | viol / ovt | coated |
|---|---|---|---|---|
| calibrated stack, corridor fail-closed ON (sim default now) | 18/47 = 38.3% | 34/74 = 45.9% | 0 / 0 | 87 |
| calibrated stack, fail-closed OFF | 19/47 = 40.4% | 34/74 = 45.9% | 0 / 0 | 89 |
| `--legacy-stack 1` (every sim-auto knob OFF; first honest stack-off run under current wiring) | 15/47 = 31.9% | 29/74 = 39.2% | 2 / 2 | 83 |

Fail-closed costs one front-line home and two coatings here (legs that
leave the tile are now bounded instead of trusted); the ember-zone
objective is unchanged. The full calibrated stack is worth +6.4 pp front /
+6.7 pp e1000 AND removes two crew overruns against the honest audit — the
on/off delta the review found unmeasurable is now measured. Files:
`scratch_review/opt_ember_fc1_c8.json`, `opt_ember_fc0_c8.json`,
`opt_ember_legacy_c8.json`.

### Hindcast calibration matrix (items 2, 3, 5)

(pending — see `docs/calibration/` when the matrix lands)
