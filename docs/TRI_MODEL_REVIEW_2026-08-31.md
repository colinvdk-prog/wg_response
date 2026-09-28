# Tri-Model Independent Review — Consolidated Synthesis (2026-08-31)

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [calibration/stacked_rings_fix_2026-09-27.md](calibration/stacked_rings_fix_2026-09-27.md) §4.5. **Affected here:** :24–25, :39–50, :114–115 and :124. The August figures move little: 93.8 → 93.6 %, 1.85 → 1.88×, sd(log) 0.710 → 0.698, effective incidents 3.24 → 3.07. The July ones move a lot: 64.8 → 100 % with the floor, PIT extreme mass 88.3 → 83.9 %, and the n = 1 blow-up is gone. The text below is left as written.

Two-pass review ordered by the founder: three independent primaries at
highest effort — **Claude/Opus** (science + safety laws), **Luna/OpenAI
Codex** (code correctness), **Grok/xAI** (statistics + operational realism)
— each briefed only by `docs/MODEL_ASSESSMENT_HANDOFF.md`; then every
aspect independently verified by a DIFFERENT model family (code→Claude,
stats→Luna, science→Grok). ≤3 agents concurrent throughout. Full reports:
`scratch_review/rvclaude_REPORT.md`, `rvluna_REPORT.txt`,
`rvgrok_ASSESSMENT.md`; verifications: `rv2code_VERDICTS.md`,
`rv2stats_ASSESSMENT.md`, `rv2sci_ASSESSMENT.md`. Every number below was
derived at least twice by different model families; disagreements were
adjudicated with arithmetic.

## Executive verdict (unanimous across 6 reports)

- **The arithmetic of the published record reproduces exactly, everywhere
  tested.** No number was fabricated or miscomputed at pooling time.
- **The framing of the record does not survive.** The validation metrics
  were one-sided by construction; the headline coverage numbers conflate
  model skill with corpus difficulty and area over-prediction.
- **The physics core is correctly implemented** (Rothermel/Byram/Anderson
  verified against the literature by two families) and **genuinely
  skillful**: 1.85× more area-efficient than a physics-free isotropic
  buffer at equal area budget (null-model control, verified 1.8513×).
- **The engineering discipline is the strongest asset**: 14/14 mutated
  config defaults caught behaviorally; the safety-first rejection culture
  held everywhere it was probed; garbage-data guards work.
- **Shadow alerting: YES** — keep operating, with the record restated.
- **Real-client alerting: NOT YET** — and the binding blockers are now
  precisely identified (below), only one of which was in the prior ledger.
- **Crew dispatch/routing: HOLD** until the corridor law fails closed and
  the spread domain covers the geometry crews actually drive.

## The corrected record (use THESE numbers going forward)

| Claim as previously stated | Corrected statement |
|---|---|
| "0% early-miss in every pool — never provably early" | **Tautology; strike as evidence.** Seeds at T0 force P90 ≥ T0; the early branch is dead code. (Confirmed 3 families; mutation control: an absurd 10×-wind model scores 98.95% "coverage".) |
| "August 93.8% coverage — best score to date" | "On 7 hand-selected fires with no blow-up event, P10 preceded observed burn for 93.8% of new cells **at 5.8× burned-area over-prediction** (1.85× better than a circle); **sub-12 h windows: 69.8%**; one fire (Bug-CA) carries 91% of the residual; same byte-identical config scored 64.8% in July. Effective sample ≈ 3 incidents." |
| "Coverage = [P10,P90] ∩ [T0,T1] interval calibration" | Coverage ≡ P(P10 ≤ T1) exactly — a one-sided P10-adequacy stat. Real two-sided checks: **late_miss, head-ratios, area-ratios, PIT** (PIT computed by the harness all along, never reported: 76–95% of mass in the two extreme bins — biased AND too narrow). |
| "Median head-ratio 1.88× (Aug) / 1.77× (calibration)" | `sorted[n//2]` = upper-middle on even n. True medians **1.837× / 1.61×** (+2–10% overstatement, always the flattering direction). |
| "Ensemble wind ±25%, dir ±30°" | Realized: **±12.5% wind (hard ceiling ~±12.2% at ANY member count — the ×0.5 is structural), ±10% moisture, ±24–30° dir.** Band under-dispersed **8×** vs measured error (sd log = 0.710); observed head inside [P10,P50] in 1/18 pairs. **P10 is ≈ the sample minimum, not a percentile.** |
| "July OOS: 8 windows" | 8 windows = **4 incidents** (equal-incident coverage 84.3%). Whole validation record = 15 distinct fires; effective N by concentration ≈ 2.4–3.2. |
| "Grass floor validated (+16.6pp)" | The floor **overrides the moisture of extinction** (no gate): fuel above extinction walks at 3.6/2.5 km/h with **zero intensity and zero flame length**; 39.4% of cached August fire-hours are in that regime (93.7% of those hours = one series; arithmetic stands). It also floors TIMBER at 19–45× Rothermel ("grass" is a misnomer; "sim-only" label false — it is live). It buys coverage by area inflation (2.3×→6.1×) and **narrowed the band further** (1.43→1.09 synthetic control). Night-time alert fatigue is the predicted live behavior. |
| "Ladder median ETAs (client copy '~X h median')" | **p50 at a head-axis client ≈ 4.18× the engine's own nominal arrival** at every range (direction perturbation rotates the head off fixed points; median member arrives via flank). The **p10 ≤ 12 h rung is the load-bearing near-term catch**, not a backstop. |
| "Corridor law is hard everywhere" | **Fails open off-raster / on UNREACHED**: 4.0% false-clear over 720 realistic geometries; worst case **7.85 h** of lost escape margin; egress legs 24–84% unsampled beyond the 2 km client-hull domain; domain truncation *manufactures* the UNREACHED value the law reads as safe (2,024/2,024 cells behind a 2-cell non-burnable wall). Test suite pins the fail-open as correct. |
| "10c audit: carve-out-free, sweep-independent, can never silently read 0" | **False as stated**: the imminent branch requires `_mode == "idle"` — 106 exempted crew-ticks in a 64-scenario sweep (104 legacy preset / **2 under shipped sim defaults**), worst **5.6 h past eroded-P10**, all mode=egress; the only guarding test pins the zero-exposure case ("tested exactly where it cannot fail"). The per-tick-max harness discipline is real (confirmed) but cannot see ticks that never enter the counter. |
| "Committed data reproduces every table" | The committed calibration artifact is **pre-gust-blend (68.8%)**; the published 73.5% needs a regeneration. (Working tree copies are review-dirtied; use git HEAD for provenance.) |
| "Zero violations everywhere" | True only in the screened **high/extreme** regimes. Moderate (18 mph) carries 4–9 violations (pre-existing, stack exonerated, idle-crew gap chipped); sub-6 h observation windows have **never been scored** (none exist in any corpus). |
| Stage-1/hysteresis "validated no-harm" A/Bs | Archived pairs were genuine A/Bs **at the time they ran** (pre-flip defaults), but the harness flag wiring today cannot produce a stack-off run (`--escape-hyst 0` / `--cold-black -1` omit the key → sim auto-enables). True on/off deltas are **unmeasured under current wiring**; use the `legacy` preset pattern to pin OFF. |

## Additional confirmed defects (code aspect)

Frozen departure-bucket edge weather (late-biased: 46-min constant offset in
a step case; TL flank/backing edges span up to 25 h on one weather hour);
slope+wind combined in wind-speed space instead of φ-vectors (−11 to −33%
too slow on OPPOSED slopes — the downslope Santa Ana geometry; zero effect
on flat terrain); edge cost uses source-cell ROS (bounded boundary term —
and the repo's own spec's mean-of-rates rule would be *worse*; harmonic
mean is correct; fix the doc); `validate_config` accepts 22/22 unsound
configs (negative erosion silently disables the corridor shield) and is
never called on the watcher path; the 1e-4 ROS floor propagates zero-ROS
fuel at `grid_cell_m=30`; `Raster` negative-index wrap (latent);
±45 m quantization ≈ up to 101 min of P10 off-axis (quantified).

## Complete default-off safety-knob inventory (verified)

Law-weakening when ON: `corridor_erosion_euclidean` (#1, known),
`route_alternatives` (#2, rejected as built), the hardcoded 10c idle filter
(#3), **`corridor_erosion_tiered` (#4 — sim auto-ON)**, plus
`transit_cold_black_by_class` (grass 45→5–20 min; sim auto-ON),
`escape_hysteresis_margin_min` (sim auto-ON at 20), and
`containment_gate_enabled` (ladder ceiling; OFF, held for founder).
Tighten-only (verified): `escape_proactive_lead_min`, `ember_buffer_min`,
`extreme_regime_enabled`, `spotting_enabled`, `wind_gust_blend`,
`dispatch_hold_h`, `crew_shift_*`, `gel_env_model_enabled`.
`ember_threat_radius_m` OFF is the live detection hole (63% of RSF-class
WUI homes invisible to the ladder).

## What survived attack (unanimous confirms)

Rothermel/Byram/Anderson implementation; unit conversions; lattice geometry;
gust blend in both spread paths; erosion shapes; LegOracle closed form;
byte-identity mutation resistance (14/14 behavioral); ±1-home
nondeterminism floor as stated; safety-first config rejection (best-scoring
dirty config correctly refused); WFIGS garbage guards; per-tick-max
violation accounting; `escape_proactive_lead_min` tighten-only;
`ember_buffer_min` adoption-time capture; the wind-vs-survivability curve,
RSF city-limits 74–78% (as sim-config coating rates), gel-window
sensitivity counts, and the live-config gap claims — all exact.

## Live operations (probed 4× during review, three families)

Watcher loop chronically slow: ~21% of target cadence (mean ~24 min vs
5-min poll), observed 33–47 min stale repeatedly **with `degraded=false`**
— healthz cannot distinguish slow/stalled and needs a staleness SLO (503
past 2× period). `/api/state` is **unauthenticated** (dev-open inherited
from the Dockerfile ENV). State on `/tmp` (restart-wiped; ~37 restarts in
August). Solver alone consumes 161 s of the 300-s budget. Simulator
healthz exposes no cadence telemetry. Nightly hindcast automation still
absent (monthly scheduled task exists, app-open-dependent); note
Open-Meteo's free tier is non-commercial — a production weather path needs
licensing.

## The ranked fix program (consensus of six reports)

1. **Corridor fails closed** (`safety.py` off-raster/UNREACHED ≠ safe) +
   spread domains must cover safe zones, crew bases, and route geometry.
   Blocks dispatch use until done.
2. **Moisture-of-extinction gate on the ROS floor** (+ floored cells report
   consistent intensity/flame; fix the "sim-only"/"grass" labels). Blocks
   client alerting (predicted alert fatigue).
3. **Ensemble honesty**: add a model-error term calibrated to the measured
   sd(log head-ratio) ≈ 0.71 so P10 is a real percentile; remove the ×0.5
   wind ceiling or document it; restate band widths. Then p50-rung/copy fix
   (the 4.18× inflation) or re-anchor client ETAs to nominal-arrival.
4. **10c audit matches its contract** (drop the idle filter or amend
   SEMANTICS 10c; add a non-zero-exposure guarding test).
5. **Two-sided scorecard**: retire early_miss/covered_certain; publish
   late + head-ratio + area-ratio + PIT + null-baseline + per-window-length
   bins + incident counts + leave-one-out; true median; regenerate the
   stale calibration artifact; score dt≤12 h as the primary bin; fix the
   harness stack-off wiring; accumulate blow-ups (extreme-regime n=1).
6. **Live hardening**: ember radius ON (300 RSF / 200 default), durable
   state, staleness→503, kill dev-open, cycle budget, weather licensing.
7. Held items unchanged: gel supplier data (12× window conflict — item 1
   of the product, distinct from item 2 of the model), edge-weather
   piecewise integration, φ-vector slope combination, solver determinism.

## Fitness verdicts (unanimous)

**Shadow alerting now:** yes — errors are late-biased (over-warning), not
silencing; nothing client-facing consumes the numbers. Operate with the
corrected record and fix the live-ops basics.
**Client alerting:** no — blocked by fixes 2, 3, and ember-radius (6), in
that order; the prior ledger's gel-data blocker remains for any
*survivability* (vs coating) claim.
**Dispatch:** hold on fix 1. The safety architecture is otherwise the
best-engineered part of the system, which is exactly why its two
advertised-but-absent properties (fail-closed corridors, sweep-independent
audit) must be made true before anyone relies on them.
