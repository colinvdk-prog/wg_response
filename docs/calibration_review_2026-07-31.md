# Full calibration review — WG Response engine

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [calibration/stacked_rings_fix_2026-09-27.md](calibration/stacked_rings_fix_2026-09-27.md) §4.2–4.4 and §5. **Affected here:** §1.2–1.7 (the calibration-set and OOS tables, the Porcupine blow-up and the regime signature), :37–38, :354–355 and :907 (the corpus is now 42 windows, 41 of them scored, 206,533 cells, 13 incidents), :312–314 and :957–958. The largest corrections: the OOS pool goes from 48.6 to 85.8 % at blend 0.5 and from 64.8 to 100 % with the floor; Porcupine goes from 22.5 km, 13× under, to 3.7 km at 0.52×, with 0 % late under the floor; and the extreme-regime evidence goes from n = 1 to n = 0. The text below is left as written.

**Founder's question:** what is measured, what is assumed, what is pending, and how
much should each number be trusted?

**Review run:** 2026-08-01 (filename carries the requested 07-31 date). Repo
`C:\Users\colin\wg_response`. This document changes no engine behavior and pins no
new numbers — it grades the ones already shipped.

**Tree state during the review.** HEAD moved twice while this was being written: a
parallel session landed `0c5e441` (Rancho Santa Fe = a 6th simulator location) and
`996e566` (simulation census + axis sensitivity + sub-30 mph wind regimes). Both are
folded in below; `996e566` carries a **new, unadjudicated safety finding** that
materially changes the corridor grade (§3.0). Every figure here was read from a
committed artifact or re-run today; where a published doc disagrees with its own
artifact, both numbers are shown (§6).

---

## How to read the grades

| Grade | Means |
|---|---|
| **MEASURED-VALIDATED** | Produced by a measurement **and** checked against data it was not fitted on — out-of-sample hindcast, a matched A/B, or an independent regime. Reproducible from a committed artifact. |
| **MEASURED-LIMITED** | Measured and reproducible, but in-sample only, or on a sample too narrow to generalize (one tile, one season, n < 10 incidents, simulator-only). Directionally trustworthy; the *value* is not. |
| **ASSUMED** | Chosen by judgment, literature, or operational doctrine. Not measured in this repo. Sub-flag **⚠ CONTRADICTED** where the repo's own measurement disagrees with the shipped value. |
| **PENDING** | A required input that does not exist yet — supplier data, an unbuilt job, an unrun validation. |

Two structural facts govern every grade below:

1. **Almost all outcome numbers are simulator numbers.** The homes-protected figures
   come from seeded scenario suites on 5 (now 6) cached LANDFIRE WUI tiles, not from
   field operations. The census (`996e566`) puts the all-time total at **8,080
   scenario simulations / 336,687 home-instances / 114 full-suite invocations**. Zero
   real GraphGel deployments have been scored. No outcome number can exceed
   MEASURED-LIMITED as a claim about the world.
2. **Only the spread model has been scored against reality** — 42 perimeter windows /
   ~177k newly-burned cells across 11 real incidents. That is the engine's one
   genuine out-of-sample anchor, and it is one fire season, mostly Western US, on
   ERA5 reanalysis weather.

**Reproduction check performed today.** `py -3 tools/hindcast_harness.py anchors
--data-dir tools/hindcast_data`, run twice: **bit-identical between runs and
bit-identical to the committed `tools/hindcast_data/anchor_results.json`** (`git diff`
empty after the rerun). The anchor path is deterministic. Full suite:
**1,222 collected, 1 failed** — `tests/test_simui.py::test_locations_registry_shape`
still pins 5 locations after `0c5e441` added a 6th. **The suite is red at HEAD.**

---

## 1. Fire spread physics

### 1.1 Rothermel/Albini core + landmark anchors — MEASURED-LIMITED

Re-run today, `tools/hindcast_data/anchor_results.json`. `ratio` = engine/observed;
< 1 = engine slower than the historical fire (the LATE, dangerous polarity).

| Anchor | Fuel | Spotting | Engine | Observed | Ratio |
|---|---|---|---|---|---|
| Tubbs (head km/h, 3 h) | GR2 | OFF | 2.97 | 6.4 | **0.46×** |
| Tubbs | SH5 | OFF | 5.67 | 6.4 | **0.89×** |
| Tubbs | GR2/SH5 mix | OFF | 3.87 | 6.4 | **0.60×** |
| Tubbs | any | ON | 7.93 (tile-edge floor) | 6.4 | 1.24× |
| Camp (area ac/h, 1.5 h) | GR2 | OFF | 67.0 | 6,600 | **0.01×** |
| Camp | SH5 | OFF | 232.5 | 6,600 | **0.04×** |
| Camp | GR2/SH5 mix | OFF | 134.0 | 6,600 | **0.02×** |
| Camp | GR2 | ON | 458.4 | 6,600 | 0.07× |
| Camp | SH5 | ON | 806.6 | 6,600 | 0.12× |
| Camp | GR2/SH5 mix | ON | 792.3 | 6,600 | 0.12× |

**Grade: MEASURED-LIMITED.** Two hand-parameterized scenarios on flat uniform
synthetic fuel. They are *evidence of direction* (the surface model is systematically
slow, worst in grass), not a fit. The Camp anchor squares any ROS error (area ~ ROS²),
so its 0.01–0.12× is not 50× worse physics than Tubbs — but even allowing for that,
no configuration comes close.

Wind is deliberately **uncapped** (`wgresponse/rothermel.py:15-17`, Andrews, Cruz &
Rothermel 2013): "the original 0.9\*I_R cap wrongly throttles wind-driven grass fires;
conservative-fast is the correct polarity for an alerting product." That is a defended
design choice, pinned by `tests/test_physics_findings.py` (9 tests), which also pins
the load-bearing safety fact that **absolute lateral ROS keeps growing above the L/B
cap** — lateral ETAs do not freeze.

`tests/test_rothermel.py` is explicit that its anchors are "loose order-of-magnitude
bounds … not precision assertions." The one hard gate there is byte-identity between
the `fuel_bed`/`surface_ros_from_bed` memo seam and the classic wrappers.

**Upgrade:** more landmark anchors with documented fire-day conditions, and — the real
fix — accumulated real-perimeter hindcast (§1.2, §2.4).

### 1.2 Gust blend 0.5 — MEASURED-VALIDATED (direction) / MEASURED-LIMITED (value)

Effective 10-m wind = sustained + blend × (gust − sustained). Motivation: max sustained
10-m wind across **every** cached ERA5 series in the first pass was **5.7 m/s** while
real fire runs ride gusts, and gusts were parsed by every adapter then dropped at the
spread seam.

Calibration set — 14 pairs / 60,062 cells (`scratch_review/gust_sweep.json`):

| Blend | Coverage | Late-miss | Early-miss | Median head-ratio |
|---|---|---|---|---|
| 0.0 | 68.8% | 31.2% | 0.0% | 1.32× |
| 0.25 | 71.7% | 28.3% | 0.0% | 1.62× |
| **0.5** | **73.5%** | **26.5%** | **0.0%** | **1.77×** |
| 0.75 | 74.9% | 25.1% | 0.0% | 1.89× |
| 1.0 | 76.3% | 23.7% | 0.0% | 2.03× |

Out-of-sample — 8 windows / 60,249 cells, all captured after the calibration date
(`scratch_review/hindcast_oos_data/oos_pooled.json`):

| Blend | Coverage | Late-miss | Early-miss | Median head-ratio |
|---|---|---|---|---|
| 0.0 | 43.2% | 56.8% | 0.0% | 0.99× |
| 0.25 | 46.1% | 53.9% | 0.0% | 1.10× |
| **0.5** | **48.6%** | **51.4%** | **0.0%** | **1.26×** |
| 0.75 | 50.4% | 49.6% | 0.0% | 1.40× |
| 1.0 | 52.3% | 47.7% | 0.0% | 1.49× |

**Grade: the *shape* is MEASURED-VALIDATED** — same monotone late-miss reduction on
data the blend never saw, **zero early-miss cost at every blend on both sets**. **The
*value* 0.5 is MEASURED-LIMITED**: the curve is monotone, so 0.5 is not an optimum, it
is a conservatism choice (1.0 buys +3.7 pp OOS coverage at a 1.49× / 2.03× median head).
Nothing in the data picks 0.5 over 0.75.

**Caveat that has never been tested (ASSUMED):** the blend was calibrated in **ERA5
reanalysis** gust units and is applied live to **NWS / Open-Meteo forecast** gusts.
Reanalysis gusts and gridpoint-forecast gusts are not the same distribution. The
extreme-regime detector deliberately hardcodes blend 0.5 independent of
`cfg.wind_gust_blend` because "thresholds were measured in that unit"
(`contracts.py:1037-1039`) — that is the right instinct and it also concedes the
transfer problem. **Upgrade:** an HRRR-analysis or RAWS-station replay, named as the
follow-up in `hindcast_first_pass.md` and never run.

### 1.3 Grass ROS floor 0.10 — MEASURED-VALIDATED (normal regime), MEASURED-LIMITED overall

`grass_ros_floor_frac` = grass head-ROS floor as a fraction of 10-m wind
(Cruz–Alexander anchor). Engine default **0.0 (OFF)**; simulator ~0.10 GR / 0.07 GS;
watch daemon arms it via `--grass-floor`.

Pooled, recomputed today from `scratch_review/hindcast_oos_data/cal_realism.json` and
`oos_realism.json` (cell-weighted; matches the published tables exactly):

| Config | Calibration set (60,062 cells) | OOS pool (60,249 cells) |
|---|---|---|
| blend 0.5, knobs off | 73.5% cov / 26.5% late / 0.0% early / 1.77× head | 48.6% / 51.4% / 0.0% |
| + grass floor 0.10 | **91.5% / 8.5% / 0.0% / 2.78×** | **64.8% / 35.2% / 0.0%** |
| + floor + spotting | 98.8% / 1.2% / 0.0% / 4.16× | 65.5% / 34.5% / 0.0% |

The headline is the regime split, not the pooled number: **on the 7 normal-spread OOS
windows (26,361 cells) the floor takes coverage from 89.1% to ≈100%** — Kaiser
Canyon's worst window 69.1% → 99.9%. The pooled OOS number stays low only because one
blow-up window is 56% of the pool.

**Grade: MEASURED-VALIDATED on normal spread** (out-of-sample, 11 incidents ≥ the
pre-declared 10-incident evidence gate, zero early-miss on both sets).
**MEASURED-LIMITED as a general claim**: one fire season, mostly Western US, ERA5
weather, and it does not touch blow-ups. The cost is real and stated — median forecast
head runs **2.78×** observed on fought fires, so the ladder issues earlier WATCH
entries and more precautionary coats.

**Upgrade:** shadow-ops trigger-rate counting (the reason it was flipped in the watcher
and not the engine), then accumulated hindcast.

### 1.4 Spotting — MEASURED-LIMITED, correctly HELD OFF

Sardoy lognormal ember transport, sampling the distance distribution
(`contracts.py` SAFETY SEMANTICS 11). Buys **+7.3 pp calibration coverage (91.5% →
98.8%)** at a **4.16× median head-ratio** — and only **+0.7 pp OOS** (64.8% → 65.5%).

**Grade: MEASURED-LIMITED; decision HELD.** The hold is the right call on the evidence:
a knob that adds 7 pp in-sample, 0.7 pp out-of-sample, and quadruples the median head
is buying coverage by over-forecasting. The stated design intent — make it an explicit
red-flag / extreme-regime mode rather than an always-on knob — is not yet built.

**Upgrade:** enough hindcast windows to score spotting *within the blow-up regime
only*, where its physics justification lives.

### 1.5 Per-fuel late-miss — MEASURED-VALIDATED (the pattern), MEASURED-LIMITED (the cells)

Calibration set, blend 0, knobs off (`hindcast_first_pass.md`):

| Class | Cells | Late-miss |
|---|---|---|
| GR grass | 8,241 | 52.6% |
| GS grass-shrub | 12,913 | 46.8% |
| SH shrub | 34,337 | 20.1% |
| TU timber-understory | 4,334 | 30.0% |
| TL timber-litter | 237 | 54.0% |
| NB non-burnable | 3,315 | unscorable |

OOS pool, blend 0.5 (`hindcast_oos_2026-07-28.md`):

| Class | Cells | Late-miss |
|---|---|---|
| GS grass-shrub | 26,925 | 64.2% |
| GR grass | 16,502 | 52.2% |
| SH shrub | 8,740 | 47.2% |
| TL timber-litter | 3,609 | 15.7% |
| TU timber-understory | 4,473 | 8.0% |

**Grade: the grass-concentration is MEASURED-VALIDATED** — the same polarity and the
same fuel concentration appear on two disjoint incident sets, and they corroborate the
Tubbs anchor (GR2 0.46× vs SH5 0.89×). **The individual cell numbers are
MEASURED-LIMITED**, and one row is unstable: TL reads 54.0% on 237 calibration cells
vs 15.7% on 3,609 OOS cells. Trust the ordering (timber solved, cured grass/shrub is
the entire remaining gap), not the percentages.

Note ~5.2% of newly-burned cells are LANDFIRE non-burnable — structure-to-structure
spread the surface model cannot represent at all. They are counted and reported,
never silently dropped.

### 1.6 The Porcupine blow-up gap — MEASURED (the gap), ASSUMED (the response)

**Porcupine Ridge ran 22.5 km in 12.9 h (74k ac); the model's median head was 1.7 km —
a 13× under-forecast.** Late-miss 82.8% at blend 0.5; 62.6% with the grass floor;
**81.7% under ensemble widening** (wind ±50%, dir ±45°). 33,888 cells = 56% of the OOS
pool. Neither more gust nor a wider band touches it.

**Grade: the gap is MEASURED** — unambiguously, out-of-sample, and it is the single
largest known physics deficit in the engine. **The response is ASSUMED** (§1.7).

**Honest read:** the engine currently cannot forecast the class of fire that destroys
communities. The alert ladder's answer is to change *posture* (evaluate on p10, banner
the message), not to close the gap. That is a defensible product decision and it is
not a physics fix.

### 1.7 Extreme-regime posture thresholds (0.60 grass-class / 4.0 m/s) — ASSUMED

`extreme_regime_grass_frac=0.60`, `extreme_regime_wind_mps=4.0` (fixed hindcast blend
0.5), `extreme_regime_lookahead_h=24.0`. Default OFF; watch daemon arms via
`--extreme-regime`. When armed: that incident's ladder stages evaluate on the
pessimistic **p10** arrival (IMPACTED stays on the median), messages carry an EXTREME
SPREAD REGIME banner, and each toggle emits an operator event. Crew safety already
runs on p10 and is untouched.

The condition was **measured, not assumed, in its *shape*** — and that part is
genuinely good work. Recomputed today from `scratch_review/extreme_signature.json`
(22 scored windows):

- A red-flag-weather clone (wind ≥ 11 m/s, RH ≤ 15%) flags **none** of the blow-ups.
  Porcupine ran at 3.5 m/s sustained / RH 27% in reanalysis while **well-forecast**
  windows were windier and drier (Dillon 8.5 m/s eff / RH 9%; Stookey 8.7 / 10).
  Weather-based regime detection is falsified.
- The fuelscape separates cleanly: Porcupine's domain is 88.2% grass-class, the highest
  scored; Madre's grass run 68.9%; every window under 60% grass-class scored ~0% late
  once the grass floor was on.

**But the threshold itself is ASSUMED, because of the denominator.** Applying the
shipped gate to all 22 windows arms exactly **3**: Porcupine (88.2% grass, eff 6.01,
62.6% floored-late) and both Madre windows (68.9% grass, eff 8.56 → **0.0%**
floored-late = a clean false positive; eff 4.28 → 15.4% late). And across the whole
22-window corpus **exactly one window has floored-late > 50%: Porcupine.**

So the detector is fitted and evaluated on the same 22 windows with **n = 1 true
positive**. 0.60 and 4.0 m/s are round numbers placed on one side of a single example.
The direction is right; the numbers carry no statistical weight.

**Mitigating:** the failure mode is benign — a false arm makes the ladder more
pessimistic, and crew safety is untouched. **Upgrade:** accumulate blow-up windows
until the gate has ≥ 5 positives, then re-fit and report the false-positive rate.

### 1.8 Suppression unmodeled — ASSUMED (safe polarity, unquantified)

Every hindcast fire was actively fought; the engine models no suppression. On contained
flanks and late-stage pairs the engine over-runs observed growth (head/area ratios well
above 1). For an alerting product this is the fail-safe direction, and it is stated in
both calibration docs.

**Grade: ASSUMED.** The 1.77× / 2.78× / 4.16× median head-ratios on the fought-fire
calibration set are **part suppression, part genuine over-forecast, and the split is
not separable with the data on hand.** Any future claim of the form "the engine
over-forecasts by X" is unsupportable until that split exists. The safety-relevant
evidence remains the *explosive-run pairs* (first hours of each fire), where the engine
is definitively late.

---

## 2. Forecast uncertainty

### 2.1 Ensemble perturbations — MEASURED-LIMITED

`ensemble_n=6` (+1 nominal), `wind_perturb_frac=0.25`, `moisture_perturb_frac=0.20`,
`dir_perturb_deg=30.0` (lead-scaled). Members are seeded on `(incident_id,
member_index)`, never wall-clock, so consecutive cycles are comparable.

**Grade: ASSUMED as values, MEASURED-LIMITED as a band.** None of 6 / 0.25 / 0.20 / 30°
was derived from a measurement; they are plausible NWP-spread choices. What *has* been
measured is how much of the truth error the resulting band covers — and it is not much.

### 2.2 P10-vs-truth band coverage — MEASURED-LIMITED, and the published figure is wrong

`scratch_review/probe_founder_arith.json` (commit `c05d622`), tick-1 P10 vs
ground-truth arrival, shipped ensemble, 4 simulator homes:

| Home | P10 h | P50 h | Truth h | **P10 / truth** |
|---|---|---|---|---|
| sim-c1 | 1.79 | 5.44 | 3.05 | 0.587 |
| sim-c2 | 1.99 | 4.71 | 3.13 | 0.636 |
| sim-c3 | 0.90 | 4.26 | 2.96 | **0.304** |
| sim-c4 | 1.02 | 4.22 | 2.87 | 0.355 |

**The measured range is 0.304–0.636, not "0.3–0.43".** The string `0.3-0.43x truth`
appears **only in the `c05d622` commit message** and nowhere in any file; it could not
be reproduced from the artifact under any reading. Treat 0.43 as unsourced. The
collapsed-ensemble control gives P10/truth 0.997–1.000, confirming the probe's
arithmetic is sound.

Consequence in the units that matter — tick-1 service window (deploy 30): **32.4 /
44.4 / −21.0 / −13.8 min** under the real ensemble vs **109.2 / 114.0 / 113.4 / 112.2
min** under perfect information. Two of four homes have a *negative* window.

Corroborating real-fire evidence: p10–p90 coverage on the calibration set is 68.8%
(necessary bound), and **widening to wind 0.50 / dir 45° moves it only +1.0 pp.**
Band width is not the lever; ROS level is.

**Grade: MEASURED-LIMITED** (n = 4 homes, one synthetic scenario, one tick). Big enough
to be directionally certain the band is too narrow; far too small to quote a number.
**Upgrade:** run the same P10/truth probe across the census suite (8,080 sims already
exist) and publish a distribution, not four points.

### 2.3 Perfect-information ceiling — MEASURED-LIMITED

Collapsed ensemble (`wind_perturb_frac=0`, `dir_perturb_deg=0`; fires unchanged),
commit `64e2c32`, belt e1000 metric (denominator 321), 20-seed screen suite:

| Arm | Belt homes | % | Δ | Violations | Overrun ticks |
|---|---|---|---|---|---|
| base c4/deploy15 | 146/321 | 45.48% | — | 0 | 0 |
| base + perfect info | 228/321 | 71.03% | **+82 / +25.55 pp** | **3** | 44 |
| c8/deploy10 | 161/321 | 50.16% | — | 0 | 0 |
| c8/d10 + perfect info | 233/321 | 72.59% | **+72 / +22.43 pp** | **7** | 45 |

The measured band is **+22.43 to +25.55 pp** (the "+22–26 pp" shorthand is slightly
wide at the top). Violations **3–7** is exact; all violation rows are `paradise_ca`.

**This is the most important number in the review.** It says forecast quality, not
dispatch cleverness, is where the remaining homes are: every shipped dispatch
optimization since the initial commit has moved single-digit percentages, and perfect
forecasts are worth ~23–26 points. It also says **naively collapsing the ensemble kills
crews** — 3 to 7 safety violations and ~44 overrun ticks. The ceiling is a target for
*better forecasts*, never a licence to narrow the band.

**Grade: MEASURED-LIMITED.** 20-seed simulator screen, one suite. **It is written up
nowhere** — it lives only in the commit message and two JSONs. That is a documentation
gap on the engine's headline result.

### 2.4 Nightly hindcast job — PENDING

`docs/LIVE_OPS_PLAN.md` item **1.4**: "Nightly hindcast regression + weekly report
(coverage %, late-miss, per-fuel bias) — tracks the 68.8% → 90% calibration effort on
real incidents." Recommendation #5 of the OOS review. **Status: planned, not built.**

This is the mechanism that would upgrade half of §1 from MEASURED-LIMITED to
MEASURED-VALIDATED. Without it the corpus is frozen at 11 incidents / 42 windows /
one season / one blow-up.

**Blocker to note:** the hindcast weather path is Open-Meteo, whose **free tier is
non-commercial only** (the harness is explicitly an offline R&D study). A production
nightly job needs the paid API or self-hosting before it can run lawfully.

---

## 3. Corridor and crew safety

### 3.0 ⚠ NEW (commit `996e566`, landed during this review): the corridor stack has never been screened at moderate wind — and it fails there

The entire tiered-erosion calibration below was run on a screen suite whose wind
intensities were **high** and **extreme**. The census session just measured the
sub-30 mph band for the first time (`scratch_review/census_wind_*.json`, deploy 10,
40 clients, scattered):

| Wind regime | Crews | Belt e1000 | **Violations** | **Overrun ticks** | Min margin h |
|---|---|---|---|---|---|
| low (8 mph) | 4 | 189/215 = 87.9% | 0 | 0 | 0.67 |
| low (8 mph) | 8 | 191/215 = 88.8% | 0 | 0 | 0.93 |
| **moderate (18 mph)** | 4 | 173/217 = 79.7% | **4** | **14** | 0.30 |
| **moderate (18 mph)** | 8 | 182/217 = 83.9% | **9** | **13** | 0.30 |

The commit flags it itself: *"a regime the corridor stack was never screened in;
attribution A/B in flight."*

**Effect on this ledger:** every "0 violations / 0 overrun ticks everywhere" claim in
§3.1–3.4 must be read as **"0 violations in the screened high/extreme regime."** The
corridor calibration's coverage of the parameter space is narrower than its language
suggested. Until the attribution A/B lands, the shipped SIM-default corridor stack
carries **4–9 measured safety violations in an operationally common wind band**, and
more crews made it worse, not better (9 > 4). Grade for the stack drops accordingly.

### 3.1 Erosion history: square 500 → euclid 550 → tiered 550/200/150

**The defect (MEASURED-VALIDATED).** `wgresponse/geo.py:159-164`: the legacy square
(Chebyshev) erosion at a stated 500 m ceils the half-width to 6 cells = 540 m, and
**corners reach √2 × 540 = 764 m = 1.53× the stated radius** — "measured importing
arrivals from 757 m away onto road cells the fire never reaches." Recomputed from
`scratch_review/probe_fa2_corridor.json`: **135 of 208 constrained ingress samples
(64.9%)** carry a finite eroded-P10 where truth never arrives. Pinned by
`tests/test_corridor_honesty.py:42-50`.

**The honest fix relaxes the law (MEASURED-VALIDATED).** `erode_min` is a MIN filter,
so a true-metric disc removes protection the square was accidentally providing — the
diagonal slop was absorbing the P10-vs-truth quantile gap of §2.2. Matched A/B at
radius 500 (commit `cb25253`, belt e1000/321): erosion-only vs both-knobs-off =
**+12 homes @ c4** (157→169, 0 viol) and **+17 @ c8** (161→178, **2 viol**). Those 2
violations are why the radius had to be re-calibrated before shipping.

**Radius ladder (MEASURED-VALIDATED, commit `60dda33`)**, euclid disc, deploy 10:

| Radius | c4 belt | c8 belt | Viol |
|---|---|---|---|
| 550 | 161/321 | 171/321 | 0 / 0 |
| 600 | 160/321 | 168/321 | 0 / 0 |

550 chosen: clean at both crew counts with more belt gain than 600.

**Tiered matrix (MEASURED-VALIDATED, commit `d7f490f`)**, deploy 10, 20-seed screen:

| FAST,SHRUB,SLOW | Hyst | c4 belt | c8 belt | Viol |
|---|---|---|---|---|
| uniform legacy square | 20 | — | 161/321 (50.16%) | 0 |
| uniform euclid 550 | 0 | 161/321 | 171/321 (53.27%) | 0 |
| **550,200,150** | 0 | 164/321 | **181/321 (56.39%)** | **0** |
| **550,200,150** | **20** | 164/321 | **181/321 (56.39%)** | **0** |
| 550,150,100 | 0 | 163/321 (−1) | 181/321 | 0 |
| 550,150,100 | 20 | — | 181/321 | 0 |
| **500,200,150** | 0 | — | 187/321 (58.26%) | **2 viol / 1 ovt** |

Shipped point **550/200/150** = +20 homes / +6.23 pp vs legacy square @ c8, +10 /
+3.12 pp vs uniform euclid 550, 0 violations in the screened regime.

**The gate did its job.** FAST=500 (`te_c`) scored the *highest* belt count (187) and
was rejected for 2 crews burned mid-egress, all on one seed
(`paradise_ca|+0|8000|high`). The **grass tier is load-bearing**; the slow tier is not
(SLOW=100 is byte-identical to 150 at 8 crews, and costs exactly −1 home at 4 crews on
a single seed flip). The founder's chaparral/timber instinct was validated; the
grass instinct was the one that mattered.

**Grade: MEASURED-VALIDATED within the screened regime; downgraded to MEASURED-LIMITED
as a shipped safety claim by §3.0.**

**Critical scoping fact:** all three calibrated knobs (`corridor_erosion_euclidean`,
`corridor_erosion_tiered`, `escape_hysteresis_margin_min`) are **SIM defaults only**
(commit `9e295c2`). `ResponseConfig` / the **live** engine still run the **legacy
square at 500 m** — i.e. the live watcher is running the configuration measured to
import phantom arrivals from 764 m. That is the conservative direction for crew safety
and the wrong direction for honesty; it is a deliberate default-OFF posture, not an
oversight, but it means **the calibrated stack is not what the live system runs.**

### 3.2 Escape hysteresis 20 min — ASSUMED (harm ruled out, benefit unmeasured)

Once fleeing, a crew keeps its adopted escape target unless a proposal improves the
routed destination margin by > 20 min (a clean route always beats a burned route).

Verified byte-identity across the artifacts: `tehy_a_c8 == te_a_c8`,
`tehy_b_c8 == te_b_c8`, `tehy_a_c4 == te_a_c4`, `hy20_c8 == o_c8d10` — **row-for-row
identical**. The commit calls this "provably free."

**Grade: ASSUMED.** "Provably free" means **zero measured harm *and* zero measured
benefit** on this suite. The justification is a single forensic observation from the
caught-crew dissection — one crew zigzagged between near-equal escape rays for ~5.5 h.
The value 20 min was not swept. `tests/test_escape_hysteresis.py` (6 tests) pins
behavior, not the constant.

**Upgrade:** a scenario that actually exercises target churn (the screen suite does
not), then sweep 5/10/20/40.

### 3.3 Cold-black stage 1 (per-class windows) — ASSUMED constants, MEASURED-VALIDATED flip

Per-class cold-black replaces the flat 45 min — which is numerically the Anderson
residence time of a **1000-hour log**, i.e. "assume everything is downed timber."
Pinned exactly by `tests/test_cold_black_by_class.py`: GR 5 / GS 10 (code 121) / 20
(code 124,145) / SH 30 / TU 45 / TL 60 / SB + unknown-burnable 90 min. Grass opens up
2–9×; **timber gets stricter** — the honest direction on both sides. Non-burnable codes
return the flat 45 (defense-in-depth). The 10c audit never consults this and is never
relaxed.

Validation (commit `f94d319`): stage 1 is **provably active — 36.8% of paradise cells
stricter, 47.9% looser — yet outcomes are byte-identical at 4 and 8 crews, 0 viol/ovt.**

**Grade: the flip is MEASURED-VALIDATED as harmless. The constants are ASSUMED** —
they are literature residence times, not outcomes measured in this engine, and the
commit says so plainly: *"no harm proven, benefit regime (escape through cooling grass)
not exercised by this suite."*

A knob that is provably active and provably changes nothing has, so far, bought
nothing. Its value is entirely in the regimes the suite does not contain — which, per
§3.0, is now known to be a larger set than assumed.

### 3.4 Transit-hazard stage 2 — PENDING

Spec only (`docs/transit_hazard_determination.md`). Gate B: per-sample thermal verdict
— nearest burnable fuel within `transit_hazard_scan_m` (270 m) → lifecycle state → flame
length and emissivity from the engine's own `RosResult` → view-factor flux + Hymes
thermal dose at pass speed → **PASSABLE (≤ 5 kW/m², ≤ 92 TDU) / CAUTION (≤ 12.5, ≤ 290)
/ IMPASSABLE**. 14 knobs. Route-selection policy only; the 10c audit and the SEM-10
sweep are never relaxed.

The physics that motivates it is derived and credible:
- Flaming-zone depth `D_f = ROS × residence_time` spans **0.1–1.2 m in timber litter**
  (0.1–1.3% of a 90 m cell) to **11–49 m in grass** (12–55% of the cell). The arrival
  raster marks both identically.
- **The transit credit is 0.55×, not 4×.** Standing separation (7 kW/m², Butler &
  Cohen) = 8.34 × flame length; transiting one 90 m cell at 9.47 m/s at the 12.5 kW/m²
  piloted-ignition cap = 4.60 × L. You may drive where you may not stand — by a factor
  of two, not ten.
- Non-negotiable floor: **never below 1.5 × flame length** of clear setback. A 60 s
  stall converts transit to standing exactly; a stalled vehicle at 1.48 × L takes the
  1% fatality dose in **under 10 s**.

**Grade: PENDING.** The 4-step validation plan — byte-identity, a per-tick overrun
tracer (the raw dicts are printed today but never persisted), classification of every
recorded overrun, and the pre-registered falsifiable prediction (Paradise relief from
the flux term, Malibu from cold-black) — **has not been run.**

**Supporting determination that IS done (MEASURED-VALIDATED):** all six reproduced
caught-crew events across two flagged scenarios collapse to **one** physical event —
a crew on a 99.1%-real-road route at a WUI-edge TL6 cell whose N/NE/E neighbours are
non-burnable, on ground the front swept **73 minutes earlier** (past even the flat
45-min threshold), with **6.1 seconds of further driving** to clear ground; the flag
cleared within one tick and never recurred. **Zero of six show the entrapment
signature.** The audit is still right to report them — carve-out-free by doctrine —
but the interpretation is fire-at-roadside, not entrapment.

### 3.5 The 30-minute egress law — ASSUMED law, MEASURED-VALIDATED dominance

`egress_buffer_min=30`. c4 / deploy 15, 20-seed screen, commit `df60648`:

| Egress buffer | Belt e1000 | Viol | Min margin h |
|---|---|---|---|
| **30 (shipped)** | 146/321 (45.48%) | 0 | 0.28 |
| 20 | 153/321 (47.66%, +7) | 0 | 0.32 |
| 15 | 156/321 (48.60%, +10) | 0 | 0.33 |

**The `viol=0` rows at 15 and 20 prove nothing.** The harness itself says so:
*"`viol` counts scoreboard violations under the CONFIGURED egress buffer (lowering
`--egress` redefines the law — flag it, don't hide it)."* Scoring a 15-minute law
against a 15-minute yardstick is circular; `min_margin_h` rises for the same reason.

**The result that does count (MEASURED-VALIDATED): the safe levers dominate the unsafe
one.** At the unchanged 30-min law, deploy 10 alone scores **157/321 (+11)** and
8 crews + deploy 10 scores **161/321 (+15)** — both beat cutting the buffer to 15
(+10). Commit verdict: *"Egress cuts dominated by safe levers (keep 30-min law)."*

**Grade: the 30-minute value is ASSUMED** (an operational rule, never derived).
**The decision to keep it is MEASURED-VALIDATED** — there is nothing to buy by cutting
it.

### 3.6 Route alternatives — MEASURED-VALIDATED rejection

Matched A/B with euclid erosion ON in both arms, deploy 10 (commit `cb25253`):

| Arm | route_alts | Belt e1000 | Viol | Δ homes | Δ viol |
|---|---|---|---|---|---|
| c4 erosion-only | 1 | 169/321 | 0 | — | — |
| c4 + alternatives | 3 | 168/321 | 3 | **−1** | **+3** |
| c8 erosion-only | 1 | 178/321 | 2 | — | — |
| c8 + alternatives | 3 | 182/321 | 5 | **+4** | **+3** |

**Grade: MEASURED-VALIDATED.** −1 to +4 homes for +3 crews in burned cells at both crew
counts, both violation deltas on the same seed. Root cause is named in
`contracts.py:865-868`: *"the max-latest-depart egress rule optimizes when you must
LEAVE, not how exposed the DRIVE is."* Keep `route_alternatives=1` until the choice
rule judges in-transit exposure — which is precisely what stage-2 transit hazard would
supply.

The mechanism artifact confirms the opportunity is real and was simply mis-harvested:
`probe_fa2_altroute.json` found a corridor-clean alternative on 3 of 4 legs at
−219 m / +1.3 min, lifting `min_slack_min` from ~4.8–6.5 min to ~41.9–42.3 min; the
4th leg returned an honest `null`.

---

## 4. Product and operational parameters

### 4.1 Gel model — one MEASURED fact, one ASSUMED mechanism, four PENDING numbers

| Item | Value | Grade |
|---|---|---|
| Class A at 1/8" **dry** film | E84 rating (supplier, 2026-07-11) | **MEASURED** (supplier document) |
| Effectiveness **flat** across the window, no decay ramp (`gel_full_strength_h == gel_window_h`) | 72 h | **ASSUMED** — reasoned from the dry-film mechanism, not measured |
| Coating is **opaque** → no seasonal pre-coat; application only under live threat | — | **MEASURED** (product truth; a strategy constraint, not a number) |
| Coverage quoted **wet**, not dry | — | **MEASURED** (product truth) |
| `gel_window_h` outer weathering / rain wash-off horizon | 72.0 | **PENDING** |
| Wet coverage rate (gal/home at 1/8" dry) | — | **PENDING** — gates 15 min/home *and* all inventory math |
| Drying time to rated film | — | **PENDING** |
| `gel_effectiveness` | **0.85** | **PENDING / PLACEHOLDER** |

`docs/OPEN_QUESTIONS.md` P-1 is blunt and correct: the dispatch placeholder assumes
72 h while the wgdefense coating-life model says **~6 h at 25 °C** — a **12× conflict
in the parameter that decides the entire dispatch posture.** 72 h means calm daylight
deployment days ahead; 6 h means deployment inside the final hours, exactly when the
safety rules bite and crew capacity is scarcest. `gel_effectiveness=0.85` is described
in the same document as **"invented"** — and a Class A flame-spread rating is not a
survival probability.

The engine's own resolution is sound as far as it goes: the wgdefense ~6 h model
describes *evaporative* gels and does not apply to a dried film, so
`gel_env_model_enabled` stays default-OFF. But that argument justifies "not 6 h"; it
does not produce "72."

**Nothing downstream of these numbers can be graded above PENDING** — plan freshness
math, the 24-hour coating hold, inventory sizing, crew sizing, and any client-facing
P(save).

### 4.2 Deploy time 15 vs 10 min — MEASURED-VALIDATED sensitivity, ASSUMED capability

60-scenario bench, 1,710 threatened homes, 0 violations in all four arms:

| `deploy_min` | Served | Abandoned |
|---|---|---|
| 30 (engine default) | 781 | 878 |
| 20 | 842 | 819 |
| **15 (simulator / server default)** | **859** | **801** |
| 10 (aspirational) | **932** | **727** |

15 → 10 = **+73 homes (+4.27 pp)**; 30 → 10 = +151 (+8.83 pp).

**Grade: the sensitivity is MEASURED-VALIDATED in simulation. The 15 min/home
capability is ASSUMED** — `server.py:368-372` ties it to a specific 3-person crew
model (two applicators + one prep-runner staging the next property), and both
`ENGINE_REVIEW_2026-07-11.md:69` and `HOA_40_PLAYBOOK.md:91-92` state it is **gated on
the missing supplier coverage rate**. The playbook says the honest thing outright:
*"if you measure 18 min/home, the plan uses 18, not 15."* 10 min/home is a program
target requiring pre-rigged manifolds and quick-connects, "worth +4 pp reactive; equals
adding ~2 crews."

Deploy time is the **cheapest measured lever in the engine** — +4.27 pp for 5 minutes,
comparable to doubling the fleet — and it is bottlenecked on one unanswered supplier
question.

### 4.3 Crew speed model — ASSUMED, and ⚠ CONTRADICTED by the repo's own measurement

Corridor pass speed is **derived, not independent**: `safety.py:193-198`,
`road_speed_mps / (circuity × fire_traffic_factor)` = 17.9 / (1.35 × 1.4) =
**9.4709 m/s** (34.1 km/h / 21.2 mph). Duplicated at `sim.py:173`.

| Constant | Value | Grade |
|---|---|---|
| `road_speed_mps` | 17.9 (~40 mph blended class speed) | ASSUMED |
| `circuity` | 1.35 | **ASSUMED ⚠ CONTRADICTED** |
| `fire_traffic_factor` | 1.4 | **ASSUMED** — README: "heuristic, uncalibrated"; OPEN_QUESTIONS: "pure heuristic, no source" |

**The contradiction.** `docs/DISPATCH_REVIEW.md:21` measures actual road circuity at
**1.96–2.43** against the assumed 1.35 — the tier-0 model is optimistic by mean
**2.43×** on Paradise, **+20 min per leg**, p90 **4.5×**. The same review notes
`fire_traffic_factor` "currently double-duties as a circuity patch."

**This is compensated, not fixed, and the compensation is a trap.**
`contracts.py:960-967` states that `route_margin_min=60` was calibrated to *also*
absorb the travel model's mountain-road optimism, which is why road-sampled legs under
plan-on-roads use the leaner `route_margin_roads_min=30`. So the 60-minute chord margin
is load-bearing for a reason not visible at its definition site. **Anyone who tunes
`route_margin_min` down without first fixing `circuity` removes the only thing standing
between the engine and a 2.43× travel-time error.** That coupling deserves a comment at
`route_margin_min` itself.

> **[RESOLVED 2026-08-02]** — measured on all six tiles and shipped as the
> calibrated pair `circuity=1.7` + `route_margin_min=56`; the 1.96–2.43 figures
> were per-leg **means** (pooled medians 1.63–1.76). Full resolution note in the
> "Just below the line" section; measurement of record
> `scratch_review/circuity_calibration.py` (+ JSON). Per-area circuity stays open.

### 4.4 Road width — ABSENT

Not modeled anywhere. `RoadEdge` (`roads.py:185-206`) carries exactly
`u, v, highway_class, length_m, speed_mps, geometry` — **no width, no lane count**.
Speed is a pure function of OSM highway class (`CLASS_SPEED_MPS`: motorway 29 →
service/track 7.0; `DEFAULT_SPEED_MPS = 11.0`). A grep for width/lanes across
`wgresponse/` returns no routing or hazard hits.

**Grade: PENDING.** Road width appears in the repo only as founder intent — *"routes
can be evaluated on fuel, road width etc."* — and is a named input to the stage-2
passability table (roads measured **46–93% non-burnable** at the sample cell, and
grass flips PASSABLE with one 90 m cell of separation while SH5 chaparral needs
~112 m). Stage 2 cannot be fully honest without it.

### 4.5 Staffing rule — ⚠ the review premise is wrong by 2×

**There is no "≤ 10 clients per crew" rule in this repo.** A full grep finds nothing.
The documented law is **≤ 5 homes per committed crew**, for reactive odds ≥ 57%
(`docs/HOA_40_PLAYBOOK.md:100-102`, `tools/onboard.py:32,179`). The number 10 appears
only as a *point on the density curve*: 60% @ 2.5 homes/crew → **51% @ 10** → 40% @ 30
(`ENGINE_REVIEW_2026-07-11.md:53-55`).

Independent density measurement at 4 crews (belt e1000):

| Clients | Clients/crew | Belt coated |
|---|---|---|
| 20 | 5 | 49.34% |
| 60 | 15 | 39.92% |
| 80 | 20 | 30.29% |

Crew-scaling ladder (deploy 10, 40 clients, belt/321), commit `7d5b300`:

| Crews | 4 | 6 | 10 | 12 | 16 | 24 |
|---|---|---|---|---|---|---|
| Belt | 157 | 158 | **168** | 169 | 167 | 168 |
| % | 48.91 | 49.22 | **52.34** | 52.65 | 52.02 | 52.34 |
| Unsavable floor | 157 | 156 | 146 | 145 | 147 | 146 |

**Saturation at ~10 crews; 10 → 24 crews adds exactly zero homes.** The unsavable floor
flatlines at 145–147. Capacity is not the binding constraint above 10 crews — the fire
clock is.

**Grade: MEASURED-LIMITED** (simulator, one client-count, one deploy time). Note the
±10-home placement noise of §5.5 is comparable to several of these deltas — the c6/c4
and c16/c24 differences are inside the noise floor.

### 4.6 Crew shifts 12 on / 8 rest — ASSUMED

`crew_shift_max_on_h=0` (off) in the engine; the sim server offers 12 h on / 8 h rest
via `crew_shifts: true`. Rest never interrupts a job; an A/B first-shift stagger keeps
the whole fleet from bedding down at once; resting crews stay under the SEM-10 safety
sweep.

**Grade: ASSUMED.** 12/8 has no measured basis in the repo — no fatigue study, no
sweep of alternative rotations, no measurement of the homes cost. The *structure*
(stagger, no mid-job rest, still SEM-10 protected) is sound design; the *numbers* are
placeholders.

---

## 5. Known defects and limits

### 5.1 Solver wall-clock nondeterminism — MEASURED-VALIDATED defect, quantified

`dispatch.py:1384-1385` sets `deadline = time.monotonic() + max(0.05,
cfg.solver_budget_s)` with `solver_budget_s = 5.0`. The plan depends on how many ILS
iterations fit in five wall-clock seconds — so it depends on machine load.

Measured noise floor, purpose-built twin baselines with identical parameters
(`opt_ember_v_base_a` vs `_b`, 60 rows each): belt **380 vs 379** (±1 belt home);
`coated_total` **936 vs 930** (**6 homes** on the raw total). Independently, on the 20
seeds shared between `opt_ember_x_base` and `x_base_unpinned`: 334 vs 333 coated, with
**exactly one scenario differing** (`santa_rosa_ca|+0|6000|extreme`, coated 15 → 14).

**Grade: MEASURED-VALIDATED** — the magnitude is known and it is small. But the
consequence is large: **any A/B whose delta is ≤ ±1 belt home / ±6 coated homes is not
resolvable.** That directly touches shipped conclusions — the SLOW=100 "−1 home"
result (§3.1) and the hysteresis "byte-identical" result (§3.2) both sit at or inside
the noise floor. `f94d319` records exactly this: *"the 1-home wobble = the known
wall-clock solver flake, exact re-run matches."*

Chip in flight. **Upgrade: replace the wall-clock deadline with a deterministic
iteration/node budget.** This is cheap and it retroactively upgrades the confidence of
every small A/B in the repo.

### 5.2 Raster ±45 m quantization — MEASURED (geometry), consequence ASSUMED

90 m working cells (`grid_cell_m=90.0`, never below LANDFIRE-native 30 m) ⇒ **±45 m**
positional quantization on every arrival, corridor sample, and fuel lookup.
`docs/transit_hazard_determination.md` calls it **"the single largest error term
(bigger than any physics uncertainty)"** — and the supporting fact is stark: roads
themselves measured **46–93% non-burnable at the sample cell**, so the model must sample
fuel at a radius rather than at a point.

**Grade: the quantization is MEASURED (arithmetic); its downstream cost is ASSUMED.**
No experiment in the repo re-runs a scenario at 30 m to price it. **Upgrade:** one
30 m-cell replay of a screen scenario would convert this from an assertion to a number.

### 5.3 Flame lengths wind-uncapped, and computed-but-discarded — MEASURED

Byram 1959 flame length (`rothermel.py:315-322`, `L = 0.0775 × I^0.46`) has **no cap**,
and the wind factor is uncapped by design (§1.1). The only cap in the module is
`ellipse_lb` L/B ∈ [1, 8], which is eccentricity, not flame length.

`flame_length_m` is **computed on every RosResult and discarded** — its only consumers
are scratch physics scripts. Stage 2 would be its first production use, and the spec
correctly requires a **flame cap before quoting absolute lengths**.

**Grade: MEASURED** (code-confirmed, deliberate, conservative polarity). **Do not quote
absolute flame lengths to anyone until the cap ships** — the 9.6 m Malibu chaparral and
0.67 m Paradise TL figures in the transit determination are internally consistent for
*ranking* hazard, not for publication.

### 5.4 Arrival-floored vs flame-unfloored ROS — MEASURED inconsistency

`spread.py:510-517, 529-535`: the grass ROS floor raises `head_ros` only. **`lb` (from
the *unfloored* effective wind) and spotting (from *unfloored* intensity) are untouched,
and `res.flame_length_m` is never recomputed.**

So when the floor binds, arrival times are conservative-fast while flame lengths and
ellipse eccentricity still describe the *slower*, unfloored fire.

**Grade: MEASURED and disclosed** — `transit_hazard_determination.md:110-112` states it
explicitly "so it is not 'discovered.'" That is the right posture. It is nonetheless a
real inconsistency that becomes load-bearing the moment stage 2 uses flame length for
route selection while arrival uses the floored ROS: **the hazard model would then read
a fire that arrives fast but burns short.** Directionally that under-states roadside
flux in exactly the grass regime the floor exists for.

**Upgrade:** recompute `flame_length_m` and `lb` from the floored head ROS, or
explicitly document the asymmetry as a stage-2 precondition.

### 5.5 Placement-seed sensitivity — MEASURED-VALIDATED noise floor

Near-staging experiment (crews parked among homes vs at depots), commit `858fca7`,
belt e1000/321:

| Crews | Depot baseline | Staged near | Δ |
|---|---|---|---|
| 4 | 157 | 147 | **−10** |
| 8 | 161 | 173 | **+12** |
| 10 | 168 | 169 | +1 |

**Deleting the entire crew approach leg moves the belt within placement noise.** The
front-reached unsavable floor (112–124 homes) never moves — the wall is the fire clock
plus cure time, not travel. (Note `opt_ember_s_c10` carries `viol=1 / ovt=1`: one crew
staged into burn at tick 1, caught by the SEM-10 sweep. The staged-near runs are not
violation-clean.)

By contrast, *placement geometry* is not noise: 4 × 10-home clusters score **55.03%**
vs a single 40-home cluster **24.63%**.

**Grade: MEASURED-VALIDATED.** Combined with §5.1, **the practical resolution of the
optimization harness is roughly ±10 homes across placement seeds and ±1 home across
runs.** Several shipped conclusions are smaller than the first of those bounds. Any
future A/B must either hold placement fixed (as the screen suite does) or run enough
seeds to average it out.

### 5.6 Smoke and visibility — ABSENT

Not modeled. `transit_hazard_determination.md` is unusually honest here: smoke is
**"historically the actual killer on evacuation roads,"** so a PASSABLE verdict is
**thermal only**. Also absent or deliberately un-credited: cab shielding (not
credited), spotting cutting a road ahead of a vehicle (separate model), civilian
traffic jams (priced as time, not stop-probability). Snags keep TU/TL/SB cold-black
floors at 45/60/90 min.

**Grade: ABSENT.** No PASSABLE verdict should ever be presented to a crew as a
route-safety guarantee.

### 5.7 90 m WUI classification — MEASURED-LIMITED, structurally significant

**23–46% of all placed clients sit ON non-burnable cells** (developed / urban FBFM40
91), and such a home **never receives an arrival time** even when fire has burned up to
7 of its 8 neighbours. Source: `docs/review/eta_gap_report.md`, 8 seeds on the Malibu
tile (48 clients each): 11 (23%) / 17 (35%) / 18 (38%) / 16 (33%) / 13 (27%) / **22
(46%)** / 21 (44%) / 17 (35%). Class (a) NB always equals the on-NB count exactly —
100% of NB-cell homes are no-ETA. The barrier-hole artifact is 0 in every seed. Tile
context: Malibu 200 × 200 @ 90 m is **42.9% non-burnable** (30% ocean, 9% urban).

Mitigation shipped: `ember_threat_radius_m` (a home on a non-burnable cell inherits the
earliest fire arrival within the radius) — **0 = off in the live engine**, ~200 m in the
simulator. So the live path still has the gap the simulator patches.

Latent secondary defect recorded in the same report: the configure-time NB note counts
only codes 91/93, so it **undercounts** when the overflow placement pool lands a client
on 92/98/99.

**Grade: MEASURED-LIMITED** — 8 seeds, **one tile**. There is no per-location NB census
across the (now six) tiles, and the newest tile is the most WUI-heavy in the catalog
(Rancho Santa Fe: 43.5% burnable / **47.8% developed**, 17,667 developed cells —
"largest WUI band in catalog"). **This measurement should be redone across all six
tiles before any client-facing coverage claim.**

### 5.8 Location registry pins a stale count — live defect

`tests/test_simui.py::test_locations_registry_shape` asserts exactly five locations;
commit `0c5e441` added a sixth without updating it. **The suite is red at HEAD
(1,222 collected, 1 failed).** Trivial to fix, but worth recording: a pinned-registry
test is the correct design, and it caught a real drift — it just needs closing.

---

## 6. Documentation-vs-artifact drift found during this review

Every one of these is a case where a published number does not match the artifact it
came from. None is large; all of them erode the "honest numbers" discipline if left.

| # | Claim | Published | Artifact / re-run today | Where |
|---|---|---|---|---|
| 1 | Camp anchor, spotting ON | 471 / 775 / 780 ac/h | **458.4 / 806.6 / 792.3** | `hindcast_first_pass.md` vs `anchor_results.json` (+ two identical reruns today) |
| 2 | Ensemble P10/truth at 3 h | "0.3–0.43×" | **0.304–0.636** | commit `c05d622` message; **0.43 appears in no file and could not be reproduced** |
| 3 | Perfect-information ceiling | "+22–26 pp" | **+22.43 to +25.55 pp** | `opt_ember_pi_*.json` |
| 4 | Deploy split 30→15→10 | +84 then +67 | **+78 then +73** (endpoints agree exactly at +151 / +8.83 pp) | `DISPATCH_OPTIMIZATION.md:24-25` vs `bench_dep*.json` |
| 5 | Staffing rule | "≤ 10 clients/crew" (review premise) | repo says **≤ 5 homes/crew**; 10 is a curve point (51%) | `HOA_40_PLAYBOOK.md:100` |
| 6 | Test count | 453 (README), 1,149 (ENGINE_REVIEW) | **1,222 collected, 1 failed** | today's run |

The Tubbs anchor rows and every spotting-OFF row reproduce **exactly**, and the anchor
path is deterministic across repeated runs — so #1 is doc staleness, not
nondeterminism.

---

## 7. TOP-5 CALIBRATION DEBT, ranked by product risk

### 1. Gel supplier data — the only BLOCKER-grade item

`gel_window_h` 72 h vs the wgdefense ~6 h model is a **12× conflict in the parameter
that decides the entire dispatch posture**; `gel_effectiveness=0.85` is documented as
invented; wet coverage rate (gal/home at 1/8" dry) gates both the 15 min/home
capability — the cheapest measured lever in the engine, +4.27 pp for five minutes — and
all inventory sizing; drying time and rain wash-off are unknown.

**Why #1:** it is the only debt where the engine is *quantitatively wrong in an unknown
direction* on a client-facing promise. Plan freshness, the 24-hour coating hold, crew
sizing, inventory, and any P(save) quoted to a homeowner or an insurer all ride on it.
It is also the only item on this list that **no amount of engineering can close** — it
requires a document from the supplier.

**Closes with:** a formulator data sheet or test report — effective life vs
temp/RH/wind, re-wet behavior, coverage rate at rated dry film, and any measured
save-rate basis.

### 2. Nightly hindcast accumulation (LIVE_OPS_PLAN 1.4)

The corpus is frozen at 11 incidents / 42 windows / **one fire season / one blow-up**.
That single blow-up is the sole positive example behind the extreme-regime thresholds
(§1.7), and the gust blend's specific value (§1.2) and spotting's fate (§1.4) are both
waiting on more windows.

**Why #2:** it is the one investment that upgrades *many* grades at once —
extreme-regime from ASSUMED toward MEASURED, the blend value from LIMITED toward
VALIDATED, spotting from held to decided — and it is the only mechanism that would
catch calibration **drift** as fuels, seasons, and geographies change.

**Closes with:** the nightly job + weekly scorecard, plus a paid or self-hosted
Open-Meteo path (the free tier is non-commercial and cannot lawfully back a production
job).

### 3. Stage-2 transit validation — now urgent because of the moderate-wind finding

The spec, physics, thresholds, and a pre-registered falsifiable prediction all exist;
**none of the four validation steps has been run.** The overrun tracer it needs (per-tick
crew, position, roadside fuel, flux, dose, band) does not persist today.

**Why #3, and why it moved up:** the new moderate-wind census (§3.0) puts **4–9 measured
safety violations** in a wind band the corridor stack was never screened in, and the
route-alternatives rejection (§3.6) failed for exactly the reason stage 2 would fix —
the current rule optimizes *when you must leave*, not *how exposed the drive is*. Stage 2
is the missing in-transit exposure model, and there is now direct evidence the corridor
law needs one.

**Closes with:** the per-tick overrun tracer, byte-identity proof, classification of
every recorded overrun (acceptance: zero true entrapments, rad550 runs stay clean), and
the moderate-wind attribution A/B already in flight.

### 4. Solver determinism

A wall-clock 5-second budget yields **±1 belt home / ±6 coated homes** run to run. Two
shipped conclusions (SLOW=100 "−1 home"; hysteresis "byte-identical") sit at or inside
that floor, and placement-seed noise adds ±10 on top.

**Why #4:** it is the cheapest item on this list — replace the `time.monotonic`
deadline with a deterministic iteration/node budget — and it **retroactively raises the
confidence of every small A/B already in the repo.** Everything else on this list will
be measured with this harness; the harness should be exact first.

**Closes with:** deterministic budget + a re-run of the twin baselines showing exact
equality.

### 5. Forecast-band tightening program

Perfect information is worth **+22.4 to +25.6 pp** of homes protected — an order of
magnitude more than any dispatch optimization shipped to date — while the shipped band
covers only **0.30–0.64** of truth error at 3 h leads, leaving two of four probe homes
with *negative* service windows. Widening the perturbations is proven not to be the
answer (+1.0 pp for wind 0.50 / dir 45°); the deficit is ROS **level**, not band width.
And collapsing the band naively kills crews (3–7 violations).

**Why #5:** it is the largest available prize and the least tractable — it is really a
research program (better ROS level in cured grass, better wind input, possibly HRRR),
not a fix. It ranks below the four above because those are *known-wrong numbers* while
this is a *known-hard problem*.

**Closes with:** the P10/truth probe re-run across the census suite to publish a
distribution rather than four points; an HRRR-analysis or RAWS replay to split
input-side from physics-side late bias; then a level correction earned on accumulated
hindcast (never a fudge shipped from one document — the DO NOT SHIP A FUDGE discipline
in `hindcast_first_pass.md` stands).

### Just below the line — and one trap worth closing now

**`circuity=1.35` is contradicted by the repo's own measurement of 1.96–2.43** (§4.3),
and the compensator is invisible: `route_margin_min=60` was calibrated to absorb that
optimism, a fact documented at `route_margin_roads_min` rather than at
`route_margin_min` itself. **Anyone who tunes the 60-minute chord margin down without
first fixing circuity silently removes the engine's only protection against a 2.43×
travel-time error.** This is not top-5 because it is currently compensated — but it is a
one-line comment away from being safe, and it should be written today.

> **[RESOLVED 2026-08-02.]** Measured properly on all six tiles
> (`scratch_review/circuity_calibration.py`: 300 routed pairs/tile, uniform +
> planner-representative populations): pooled **median** 1.63–1.76, pooled mean
> 1.95–2.14 — the 1.96–2.43 above were **means** (Paradise mean 2.5–2.7 confirmed).
> Shipped as the calibrated pair **`circuity=1.7` + `route_margin_min=56`**
> (= 60 − 3.9 min at the representative 8.5 km leg; end-of-leg conservatism
> preserved, long legs strictly more conservative). The coupling warning now
> lives at `route_margin_min` itself. Per-area circuity stays open (T-7).

Also below the line: road width absent (§4.4, a stage-2 precondition); the ±45 m
quantization never priced by a 30 m replay (§5.2); the NB-cell census measured on one
tile out of six, with the newest tile the most WUI-heavy in the catalog (§5.7); and the
arrival-floored / flame-unfloored asymmetry (§5.4), which becomes load-bearing the
moment stage 2 ships.
