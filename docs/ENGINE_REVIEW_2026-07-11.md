# WG Response — Engine & Results Review (2026-07-06 → 2026-07-11)

Founder-requested retrospective. Every number below is measured (harness named);
nothing is projected. Companion docs written the same day:
`LIVE_OPS_PLAN.md`, `DISPATCH_OPTIMIZATION.md`, `HOA_40_PLAYBOOK.md`.

---

## 1. What was built (6 days)

| Date | Milestone | Verified state |
|---|---|---|
| 07-06 | Engine v1: contracts-law repo, feeds (WFIGS/FIRIS/CAL FIRE/FIRMS/NWS), Rothermel-Albini + Finney MTT spread, P10/P50/P90 ensemble, 72h/24h alert ladder, deadline-TOPTW dispatch, crew-safety corridor law | 453 tests; 96h flagship sim: 12/12 serviced, 0 violations |
| 07-09 | Council 1 (4 parallel review lanes) → THE FIVE defects | review-only; all five confirmed with repro harnesses |
| 07-09 | Sprint 2 (3 sessions): fail-safe ladder ("absence is not clearance"), 40-row FBFM40, ensemble v2 (direction perturbation, 0.000% jitter), corridor T* closed-form (solver 20/200 → 200/200 @ 0.000% gap), staleness gates, watch daemon + StateStore, live soak on real Volcano Fire | 822 tests; warm cycle ≤10.3s |
| 07-10 | Interactive simulator: 5 real LANDFIRE tiles, WUI placement, crews on OSM roads, fire bands (1h/5h/24h), time-scrub replay, regional crews (+1h/+3h), homes-first triage, gel rings, ember spotting, grass+timber ROS floors, SEM-10 continuous crew safety | 1,001 → 1,149 tests |
| 07-10 | **Critical fix**: persisted-arrival-field architecture (per-tick ensemble re-base froze fires twice — founder-visible "fire doesn't grow") | regression test proves fail-on-old-code; ~5× faster ticks |
| 07-11 | Public deploy (App Runner), sim-clock HUD, line-of-fire review, dispatch improvement loop, application-rate analysis, defensibility siting | live at m897yqgsmt.us-west-2.awsapprunner.com/simulator |

**Current suite: 1,149+ passed / 0 failed.**

**CORRECTION (same day, SEM-10d):** the long-standing "zero violations across
every sweep" claim was a FINAL-TICK artifact — `scoreboard.violations` is
instantaneous, and per-tick tracking exposed transient crew overruns the
final-tick reads hid (43 overrun-ticks across the 60-scenario reactive sweep;
2 crews actually burned mid-egress + a no-zone-left dead end on the extended
Camp-Fire-scale run). Fixed same day: SEM-10d routed relocation (relocation
targets now corridor-checked over the crew's ACTUAL route), destination-safe-
first least-exposure selection, outward AREA ESCAPE when the fire consumes
every catalog zone, and cold-black passability (crews may drive OUT through
old burn — the front, not the black, is the hazard). Post-fix, per-tick:
extended Camp-Fire run 0 violations end-to-end; reactive sweep overrun-ticks
43 → 3 (each a single tick where the front beat a 15-min tick with <1 tick of
warning — the irreducible tail), and served rose 878 → 893. Benches now track
per-tick maxima; a 120-tick extended regression test pins the scenario.

## 2. Headline results (the numbers that matter)

Benchmark: 60 stress scenarios = 5 real maps × 3 wind offsets × 2 ignition
distances × {high, extreme}, upwind ignition, spotting + grass realism ON,
detection assumed instant. Harness `scratch_review/bench_dispatch.py`.

- **Reactive application rate** (fire ignites, crews respond): **45.7%** of
  threatened homes coated in time at 30-min deploy / 4 crews → **50.6%** with
  3-person/15-min crews → **58.8%** at 8 crews. Per-scenario range 0–82%.
- **Physics ceiling ~54–59%**: 4→8 crews +131 homes; 8→12 only +18. 727/878
  lost homes are unsavable at ANY crew count — fire beats every safe round-trip.
- **Deploy speed dominates**: 30→10 min/home = +8.8pp, equal to tripling the
  fleet. This drove the 3-person-crew decision (15 min → +4.9pp).
- **Coat-early shipped** (`dispatch_late_shift=False` in sim): +~1% homes,
  robust across 8 held-out crew/client ratios, freshness-at-arrival neutral
  (0.625 vs 0.626), 0 violations.
- **Density is the siting law** (`scratch_review/defensibility.py`): survival is
  capacity-bound, not location-bound. 60% @ 2.5 homes/crew → 51% @ 10 →
  40% @ 30. No individual parcel classed "avoid" in isolation; short-lead sites
  occur on ALL compass bearings (no fixed "bad side" to zone).
- **Overfit caught by out-of-sample validation**: urgency-tiebreak looked +2.0%
  in-sample, lost up to −24 homes on held-out ratios — rejected. (Third time
  urgency-steering has failed measurement; it is now a standing dead end.)

## 3. Honest caveats (carry these into every claim)

1. **Gel model — partially resolved same day**: founder confirmed GraphGel is
   **Class A at 1/8" DRY film** — the dried coating is the protection, so
   effectiveness is FLAT (no wet-gel decay); engine updated
   (`gel_full_strength_h == gel_window_h = 72`), late-shift now a structural
   no-op, sim toggle removed. Reactive counts unchanged within noise (flat
   model: +25/−8/0 across ratios — EPS-tiebreak noise, NOT a gain). Still
   unverified: coverage rate (volume/home at 1/8" dry — gates the 15 min/home
   figure), drying time, rain wash-off, weathering horizon beyond 72h, and
   `gel_effectiveness=0.85` (Class A rating ≠ survival probability).
2. **Detection is assumed instant.** Real feed latency (WFIGS 5-min cadence,
   satellite pass gaps) eats lead time directly; real percentages sit below the
   modeled ones. Unmodeled.
3. **Model is slower than the worst real fires**: modeled Paradise median lead
   3.4h (extreme) vs the real Camp Fire ~1.5h to town (spotting-driven). All
   survival numbers are optimistic for wind-driven ember events.
4. **Hindcast coverage 68.8% vs 90% target** (Sprint 2 D): late-miss
   concentrated in grass fuels (Tubbs 0.46×). ROS floors fixed this in the SIM;
   the LIVE engine still needs the calibration decision (DO-NOT-SHIP-A-FUDGE
   verdict stands — needs ≥10 incidents + spotting default decision).
5. **Dispatch plans on straight chords** while crews drive roads: optimistic on
   76% of legs (mean 1.65×; Paradise 2.43×, +20.1 min/leg). Movement/departure
   use roads; planning does not yet. Top-ranked engine improvement.
6. **Defensibility harness ignores corridor crossing** (time-budget only) —
   its per-site savability is optimistic; used for ranking, not absolutes.

## 4. Operational risks found in this review

- **P0 — `wg_response` is NOT a git repository.** 1,149-test flagship engine
  with no version control, no history, no rollback. `git init` + initial commit
  + remote before anything else. (Fixed same-day if approved.)
- **P1 — public simulator has no auth/rate-limit** (accepted for demo; pause
  the App Runner service when the demo window closes; ~$25–50/mo idle).
- **P1 — single-instance in-memory sessions** on App Runner: a deploy or crash
  drops all live sessions (fine for demo, not for ops).
- **P2 — SES sandbox, SMS unwired** — no real crew notification path yet
  (see LIVE_OPS_PLAN).

## 5. Where the next homes come from (ranked by measured leverage)

1. **Lead time** (detection + alert-driven pre-coat): converts unsavable →
   savable 1:1; the ONLY lever that beats the ~54–59% reactive ceiling.
   An alerted event with ≥12h notice lets 1–2 crews coat an entire 40-home
   community (see HOA_40_PLAYBOOK).
2. **Deploy speed**: every 5 min off deploy ≈ +2–4pp reactive.
3. **Crew capacity to ~5 homes/crew**, then it saturates.
4. **Plan-on-roads travel model**: honesty fix; fewer mid-run withdrawals.
5. **Defensible space per parcel**: converts homes to ember-only exposure in
   the model — lengthens lead, compounds with everything above.
