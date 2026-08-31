# WG RESPONSE — INDEPENDENT MODEL ASSESSMENT (CLAUDE REVIEWER)

Tri-model review, science + safety-law aspect. 2026-08-31. Probe scripts:
scratch_review/rvclaude_*.py (all reproducible, PYTHONHASHSEED=0). Suite at
review time: green (~1,237 passed / 6 skipped).

## 0. Record verification

Documented offline replay reproduces the published calibration row exactly
(73.5190 / 26.4810 / 0.0000 / 1.7724x). Consolidated re-derivation
(rvclaude_final_table.py) reproduces every published pooled column across
Aug/Calibration/July eras to the last digit; per-fuel August late matches
exactly. THE ARITHMETIC IS HONEST. THE FRAMING IS NOT — see the columns the
record does not report (TRUE median head, AREA-ratio pooled/median):
Aug as-deployed 93.79% coverage at AREA 6.06x pooled / 8.90x median-pair;
calibration+floor 91.49% at 5.22x/9.64x; realism pack 98.79% at 19.27x med.

## A. Findings that refute or materially weaken claimed numbers

- **A1 CRITICAL — "0% early-miss" is a tautology; coverage is one-sided.**
  Seeds at T0 + non-negative Dijkstra ⇒ every arrival > T0 in every member ⇒
  early_miss (p90<T0) and covered_certain (p10<=T0) unreachable. Verified on
  all 36 Aug rows: early=0, covered_certain=0, p10_before_window=0,
  covered_possible == p10_not_after_window 36/36. Coverage ≡ P(P10<=T1).
  Strike "0% early" from the record as evidence.
- **A2 CRITICAL — no false-alarm axis anywhere; as-deployed over-predicts
  burned AREA 6.06x pooled (8.90x median pair, max 120x).** Coverage and
  area move together monotonically (one-parameter family). Null-model
  control built (rvclaude_nullmodel.py — exact EDT isotropic dilation of T0,
  no physics, identical masks): at the engine's own area budget the null
  scores 68.18% vs engine 93.79%; null needs 10.72x area to match → **engine
  skill = 1.85x more area-efficient than a circle**. Defensible headline:
  "93.8% coverage at 5.8x area over-prediction, 1.85x better than an
  isotropic buffer" — not "93.8% coverage".
- **A3 CRITICAL — the grass floor overrides the moisture of extinction.**
  spread.py floor has no moisture/curing gate: at m_dead >= mx (GR2/GS2
  0.15) Rothermel = 0.000 km/h but floored head = 3.6/2.5 km/h — WITH ZERO
  INTENSITY AND ZERO FLAME LENGTH (intensity from unfloored result). 39.38%
  of the 56,136 cached August ERA5 fire-hours exceed GR/GS extinction (RH
  crossing 76.4% @25C); two incidents 38-41% of hours. The floor also
  floors TIMBER (TL at 0.30x frac → 19-45x Rothermel; TU 0.50x → 12x); SH
  is the only unfloored class — the per-fuel "SH worst 13.5%" partly
  measures which class was NOT floored. Knob mislabeled "sim-only" in
  contracts while armed on the live watcher. Mechanism behind A2's area
  inflation and predicted night-time alert fatigue.
- **A4 HIGH — August 93.8% vs July 64.8% is the SAME byte-identical config;
  the +29pp is corpus, not model.** July's fastest pair 1.73 km/h
  (Porcupine); August's fastest 1.08 — no event at the known failure mode's
  speed. July late = 100% Porcupine; August late = 91% Bug-CA. "Best score
  to date" invites a false inference.
- **A5 HIGH — 18 windows = 7 fires; leave-one-incident-out swings the
  headline** (drop Bug → 99.32%; drop Tartar (48.3% of cells) → 88.01%).
  Pair-weighted: mean 95.66 / median 100.00. Whole record = 15 distinct
  fires. Cells are autocorrelated; any n=135,081 inference is overconfident.
- **A6 HIGH — published "median" head-ratio is sorted[n//2]** (upper-middle;
  Aug calibration published 1.098 vs true 0.992). Two code paths; bias
  always toward "we over-predict, so safe".
- **A7 HIGH — ensemble under-dispersed 8x; P10 is not a percentile.**
  Internal band half-width 0.089 log-units vs real error sd 0.710; observed
  head inside [P10,P50] in 1/18 pairs. PIT (computed by the harness, never
  reported): 76-95% of mass in the two extreme bins, every era. The floor
  NARROWED the band (17.2→9.3%) while enlarging bias. Actual perturbations
  are ±12.5% wind / ±10% moisture / ±30 deg (docs say ±25%). n=6 members ⇒
  Hazen P10 ≈ sample min. Crew deadlines are protected by the fixed buffers,
  not the quantile.
- **A8 HIGH — the "carve-out-free" 10c audit has a mode carve-out and is NOT
  sweep-independent.** imminent-branch requires _mode=="idle"
  (simui.py ~3626-3630) — off for every non-idle crew; contract promises
  "any crew ... imminent ... strict superset ... independent of the sweep".
  Measured (rvclaude_carveout_small.py, legacy preset): violations=0 while
  3 crews sat 189-227 MINUTES past eroded-P10 at their own cells (mode
  egress). The audit's silence is a function of the sweep — exactly what
  10c promises it is not. The third regression, inverted: a flag turning
  OFF revealed it.
- **A9 MED-HIGH — two safety-relaxing sim defaults ship at measured 0.00pp
  benefit** (per-class cold-black grass 45→5 min; escape hysteresis 20 —
  can hold a crew on a strictly worse target); tier radii "plan of record"
  chosen on 0.00-0.37pp (noise-floor) deltas; the single largest measured
  gain (+5.88pp) IS a safety relaxation (smaller shrub/timber erosion).
  cold_black_minutes table does not reproduce its own stated Anderson
  derivation (GS4 ships 20 vs derived 38.4 min) — permissive beyond its
  documented basis.
- **A10 MED-HIGH — selection gates on mapping cadence, deleting fast
  fires.** 7 of 94 discovered candidates reached the scoreboard; n_rows>=3
  is a mapping-cadence filter; largest deletion BIG GRASS-OR 575,163 ac
  (rows=1) — 1.6x larger than the largest fire kept. 18/30 exclusions kept
  only the late contained phase of July fires. Mechanism behind A4.
- **A11 MED — the committed calibration JSON contradicts the published
  table** (committed 68.82% pre-gust-blend vs published/reproduced 73.52%)
  — the handoff's own "re-derive from committed JSON" instruction fails for
  that era. Regenerate the artifact.
- **A12 MED — ERA5-calibrated gust blend deployed on NWS gridpoint gusts**
  (different quantity; operational gusts typically exceed reanalysis in
  complex terrain) — engine likely runs faster than validated; safe
  polarity; NOT in the ledger.
- **A13 MED — ember_threat_radius_m=0 live: the ladder cannot alert
  developed-cell homes** (61.5% of the RSF band); ETAs UNREACHED ⇒ no
  escalation, no deadline. Sim numbers (radius 200) are not live numbers.
  Single largest live-operational gap.
- **A14 LOW-MED — corridor law fails OPEN on missing/short raster**
  (samples off-raster unconstrained; None ⇒ latest_safe_depart=UNREACHED).
  Not reachable from production dispatch today (rasters always built), but
  egress legs are only constrained over their in-tile portion. The one
  departure from the repo's fail-closed discipline.

## B. Confirmed under attack

- **B1** Byte-identity pin discipline is real: 14/14 mutated ResponseConfig
  defaults caught, and behaviorally (not just docs-guard) for the sampled
  knobs (mutation harness rvclaude_mutate.py).
- **B2** Rothermel/Byram/Anderson correctly implemented (0.0775·I^0.46 is
  the exact SI Byram; t_r=384/sigma in correct imperial units; uncapped
  wind per Andrews/Cruz/Rothermel 2013; is_burnable namespace fix present).
- **B3** The engine beats the physics-free null 1.85x at equal area budget.
- **B4** The ±1-home nondeterminism floor is real and correctly stated.
- **B5** Safety-first selection discipline held (best-scoring dirty config
  was correctly rejected).
- **B6** Monotonicity/garbage-row guards work (BaldMountain non-monotone,
  Sinlahekin 5.99-year stray pair caught).
- **B7** escape_proactive_lead_min only tightens (correctly built; +0.09pp).
- **B8** ember_buffer_min adoption-time capture semantics correct.

## C. Top-3 next actions

1. **Gate the ROS floor on moisture of extinction** (m_dead <= mx and
   curing threshold); make floored cells report consistent intensity/flame;
   fix the "sim-only" mislabel + the timber-flooring name; re-derive every
   era with coverage AND area as the paired deliverable.
2. **Publish a two-sided score, retire the tautologies**: area ratios, PIT
   histogram, null-model coverage-at-budget; delete early_miss/
   covered_certain; fix the median; report incident counts + pair-weighted
   + leave-one-out; regenerate the stale calibration artifact.
3. **Widen the ensemble to the measured error** (multiplicative model-error
   term calibrated to sd(log head-ratio) ≈ 0.71) so P10 is a real 10th
   percentile and PIT flattens; correct the documented band widths.
   Runners-up: ember radius > 0 live; make 10c match its contract; retire
   the 0.00pp safety-relaxing sim defaults.

## D. Fitness for purpose

**Shadow alerting today: YES, with the record restated** (defensible
sentence: "on 7 hand-selected August fires containing no blow-up, the
deployed config placed P10 before observed burn for 93.8% of new cells at
5.8x area over-prediction, 1.85x better than an isotropic buffer, 91% of
residual error in one fire").
**Real-client alerting: NO — and the binding blockers are not the ledger's:**
(1) the model cannot represent a fire going out (A3 ⇒ predicted alert
fatigue); (2) P10 is not a percentile (A7 ⇒ the band is decorative;
buffers are doing the protecting); (3) the live default cannot alert the
majority of sellable homes (A13). The safety architecture is otherwise the
strongest part of the codebase; fix its one advertised-but-absent property
(10c sweep-independence) before it is needed.

## ADDENDUM — A8 finalized (64-scenario sweep) + one self-correction

Sweep (rvclaude_audit_carveout.py + rvclaude_run_sweep.py, 64 scenarios):
106 exempted imminent-non-idle crew-ticks (104 legacy preset / **2 under
the shipped CURRENT defaults** — legacy amplifies, does not create), all
mode='egress'; worst -333.4 min (5.6 h) past eroded-P10, three crews at
once; six crews simultaneously at -250 min in one scenario. Exposure
concentrates at MODERATE intensity (the known idle-crew-gap regime).

SELF-CORRECTION: the main report's "scoreboard reads violations=0" leaned
on a final-tick snapshot read; scoreboard.violations is instantaneous and
the benchmark harnesses already take per-tick maxima (opt_ember.py:147-154,
bench_dispatch.py:67-68 documents the trap) — so the 77 BURNED crew-ticks
WOULD be reported by the real harness. **B9 CONFIRM: the repo anticipated
the snapshot trap.** The finding survives sharper: the 106 exempted ticks
never enter _crew_overruns at ANY tick (simui.py ~3629 filters them before
the list is built) — per-tick max cannot see past a mode-based exemption.
SEMANTICS 10c's "independent of the sweep ... can never silently read 0"
is measurably false. Fix: drop the _mode=="idle" restriction or amend the
contract; the two must not stay in contradiction.

Final tally: 14 findings weakening claimed numbers (3 critical / 5 high /
4 medium / 2 low-medium), 9 confirmations under attack. Verdict unchanged:
shadow YES with record restated; clients NO — blocked by A3 (extinction
override), A7 (non-percentile P10), A13 (live ember radius 0).
