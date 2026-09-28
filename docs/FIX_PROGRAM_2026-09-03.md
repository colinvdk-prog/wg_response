# Fix Program 2026-09-03 — acting on the tri-model review

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [calibration/stacked_rings_fix_2026-09-27.md](calibration/stacked_rings_fix_2026-09-27.md) §4.1, §5 and §7. **Affected here:** :155–216, the matrix comparison. D071 still leads, but July skill is 1.01–1.11, not ≈ 1.0, and the July blow-ups behind the reasoning that I is held back were one artifact. Also :40–41 and :58–59 (sd(log) 0.71 is re-measured at 0.698). The text below is left as written.

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

Runner `scratch_review/calmatrix_run.sh`, table
`docs/calibration/calibration_matrix_2026-09-03.md` (built by
`scratch_review/calmatrix_report.py` from `scratch_review/calmatrix/`).
Corpora: August 2026 (12 incidents; harness pairing = 27 pairs / 145,760
cells / 11 incidents scored — NOT the review-era 18-window table, so the
comparison is variant-vs-variant on identical pairs) and July OOS.

**Decision rule, written down before the last rows landed** (so the
choice is not post-hoc): the live watcher runs the variant that
(1) minimises PIT extreme-bin mass — the honesty of the band — subject to
(2) pooled late-miss no worse than the as-deployed floor's (~6%) and
(3) the least median p10 area inflation among those; skill over the
equal-area circle breaks ties. A variant that meets (1)-(3) on August but
fails (2) on July is rejected.

**What the first six rows already established.** The extinction gate is
free (B_gate ≡ A_deployed to three figures on this corpus — the out-fuel
hours were not where the fires ran); the timber flooring is the inflation
engine (grass-only halves median p10 area 19.4× → 10.7× for −0.6 pp
adequacy); and the PIT split shows the floor did not calibrate the engine
— it flipped the bias sign: with no floor 53% of newly burned cells burn
EARLIER than every member (too slow / too narrow), with the floor 69% burn
LATER than every member (too fast). The model-error term flattens the PIT
(81% → 59% → 50% extreme mass at sd 0 / 0.5 / 0.71) but on top of the
floor it only widens an already-overshooting band (23-25× area).

**The full matrix (14 variants × 2 corpora).** August, key rows —
adequacy / late / median p10 area / skill / PIT extreme (early/late) /
6-12 h bin:

| variant | Aug | July (OOS, the blow-up corpus) |
|---|---|---|
| A as deployed (floor 0.10 all classes) | 94.2 / 5.8 / 19.4× / 1.07 / 81% (12/69) / 71% | 76.9 / 23.1 / 12.4× / 1.00 / 92% (32/60) / 88% |
| B + extinction gate | identical to A | identical to A |
| C gate + grass-only | 93.6 / 6.4 / 10.7× / 1.08 / 79% / 69% | 68.2 / 31.8 / 9.5× / 0.95 / 91% / 63% |
| E gate + grass-only + band 0.50 | 96.9 / 3.1 / 11.9× / 1.08 / 56% (10/47) / 84% | 70.7 / 29.3 / 11.3× / 0.94 / 79% (37/43) / 66% |
| I HALF floor 0.05 gate grass-only + band 0.71 | 94.1 / 5.9 / 11.0× / 1.09 / **40%** (14/26) / 69% | 68.0 / 32.0 / 10.4× / 0.94 / 67% / 62% |
| H no floor + band 0.71 | 90.5 / 9.5 / 8.2× / 1.23 / 53% (34/18) / 53% | 63.3 / 36.7 / 8.3× / 0.98 / 67% (46/21) / 58% |
| **D gate + band 0.71 (CHOSEN)** | **98.8 / 1.2 / 24.7× / 1.06 / 50% (6/44) / 94%** | **83.1 / 16.9 / 14.2× / 0.99 / 72% (22/50) / 99%** |
| D gate + band 0.50 | 97.2 / 2.8 / 22.7× / 1.07 / 59% / 85% | 81.5 / 18.5 / 13.6× / 1.00 / 80% / 98% |

**Decision (the rule applied).** Late-miss no worse than the deployed
floor on BOTH corpora admits only the all-class gated floor with the
error band (D family) — every grass-only or half-floor variant, however
attractive on August, loses 6-9 pp of adequacy on the July blow-ups,
where the engine has NO directional skill over a circle of equal area
(skill ≈ 1.0 for every variant: on those fires coverage is area budget,
not physics, and the timber floor's contribution there is area). Among
the compliant variants the 0.71 band (the review's measured head-ratio
error) has the flattest PIT. The live watcher therefore runs
`--grass-floor 0.10 --floor-gate --model-error 0.71` (with the
already-armed `--gust-blend 0.5 --extreme-regime --fail-closed
--ember-radius 200 --p50-bound`): late-miss 5.8% → 1.2% (Aug) and 23.1% →
16.9% (July); the 6-12 h bin — the P10 rung's own horizon — 71% → 94% and
88% → 99%; PIT extreme mass 81% → 50% and 92% → 72%; P10 is now the 10th
percentile of a band whose width is the measured error, not the sample
minimum of a ±12.5% wind spread. The price, stated plainly: median p10
area +27% (Aug) / +14% (July) over an already 19×/12× over-prediction.
What the matrix could NOT deliver: a variant that is both honest and
tight. The half-floor + band point (I) is the best-calibrated band in the
matrix on August (40% extreme mass, near-symmetric) and would be the
choice on physics alone; it is held back solely by the July blow-ups,
i.e. by the extreme-regime problem the review already isolated (n=1
positive example). When the extreme-regime posture can carry those fires
on its own, the floor should be re-cut toward I.

**The full table** (all metrics, both corpora):
`docs/calibration/calibration_matrix_2026-09-03.md`. Raw results:
`scratch_review/calmatrix/`. Integrity: every file's stored label/config
is checked against its filename by the report script (three July files
were regenerated after a driver-editing race corrupted them).
