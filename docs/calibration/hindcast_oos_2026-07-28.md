# Out-of-sample hindcast — model vs live fires (2026-07-28)

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [stacked_rings_fix_2026-09-27.md](stacked_rings_fix_2026-09-27.md) §4.3–4.4 and §5. **Affected here:** nearly every number. The OOS pool is 13 windows / 71,261 cells / 6 fires (was 8 / 60,249 / 4). Blend-0.5 coverage is 85.8 % (was 48.6 %), and 100 % with the grass floor (was 64.8 %). The Porcupine Ridge blow-up was an artifact: its T0 cancelled to zero cells and its T1 stacked 31 versions of one record. Corrected, it is 3.7 km / 11,139 ac / 0.52× with 54.7 % late at blend 0.5, 0 % late with the floor, and 7.5 % of the pool. The Crosswhite and BISCAR exclusions were artifacts. The extreme-regime signature now has no window above 50 % floored-late (was n = 1). The text below is left as written.

**Question** (founder): how is the propagation model performing against live
data — including whether the gust-blend 0.5 chosen on 2026-07-13 holds up on
fires it has never seen.

**Method.** The 2026-07-13 calibration used four 2025 California incidents
(Gifford, Dillon, Madre, Butler — 15 windows). Every perimeter window captured
**after** that date is therefore out-of-sample. WFIGS discovery (min 1,000 ac,
captures since 2026-07-13) across CA/UT/CO/OR/WA/NV/ID/MT/WY/NM produced the
scoreable pool; AZ was dropped for future-dated polygon timestamps. Guards on
every scored window: window start ≥ 2026-07-13 UTC, spatially real growth
(n_new_burnable > 0), and the full window inside the incident's non-null ERA5
archive series (ERA5 lags ~5 days; scoring against stale weather flatters
nobody). Replays are offline on cached perimeters/weather/LFPS tiles —
`scratch_review/oos_fetch_states.py`, `oos_pool.py`, `oos_realism.py`; data in
`scratch_review/hindcast_oos_data/`.

**Pool**: 8 windows / 60,249 newly-burned burnable cells across 4 fires —
Stookey (UT, 11.9k ac), Porcupine Ridge (OR, 80k ac), Brewer (OR, 71k ac),
Kaiser Canyon (WA, 126k ac). Excluded honestly: ELEPHANT (CA — captures 110/134 h
apart, beyond the 72 h forecast horizon), BISCAR (CA — attribute acres grew
+10.4k but the mapped perimeter did not), Crosswhite (OR — no spatial growth
between usable captures), Aspen Acres / Willow / Cottonwood (growth phases
pre-date the cutoff; kept for the pooled evidence base, not the OOS headline).

## Gust-blend validation (out-of-sample)

| Blend | Coverage | Late-miss | Early-miss | Median head-ratio |
|---|---|---|---|---|
| 0.0 | 43.2% | 56.8% | 0.0% | 0.99× |
| 0.25 | 46.1% | 53.9% | 0.0% | 1.10× |
| **0.5 (live)** | **48.6%** | **51.4%** | **0.0%** | **1.26×** |
| 0.75 | 50.4% | 49.6% | 0.0% | 1.40× |
| 1.0 | 52.3% | 47.7% | 0.0% | 1.49× |

Same shape as the calibration curve: monotone late-miss reduction with blend,
**zero early-miss cost even at full gust**. Blend 0.5 confirmed directionally
correct out-of-sample; no evidence to move it (1.0 buys +3.7 pp coverage at a
1.49× median head-ratio here and 2.03× on the calibration set).

## The result is two regimes, not one number

**Normal spread (7 of 8 windows, 26,361 cells): coverage 89.1%, late-miss
10.9%** — at the 90% program target, out-of-sample:

| Window | Cells | Late @ blend 0 → 0.5 | Coverage @ 0.5 |
|---|---|---|---|
| Stookey-UT 07-13 +16.4h | 835 | 0.0 → 0.0% | 100.0% |
| Stookey-UT 07-14 +23.1h | 1,934 | 2.5 → 0.0% | 100.0% |
| Brewer-OR 07-17 +48.4h | 2,627 | 11.0 → 0.0% | 100.0% |
| Brewer-OR 07-19 +23.1h | 3,273 | 8.6 → 0.5% | 99.5% |
| KaiserCanyon-WA 07-19 +11.2h | 4,943 | 37.8 → 30.9% | 69.1% |
| KaiserCanyon-WA 07-19 +35.6h | 6,468 | 26.7 → 13.0% | 87.0% |
| KaiserCanyon-WA 07-21 +23.9h | 6,281 | 9.1 → 7.8% | 92.2% |

**The blow-up regime (1 of 8 windows, 33,888 cells — 56% of the pool):
Porcupine Ridge ran 22.5 km in 12.9 h (74k ac); the model's median head was
1.7 km — a 13× under-forecast.** Late-miss 82.8% at blend 0.5. Neither more
gust (blend 1.0) nor ensemble widening (wind ±50%, dir ±45° → Porcupine late
81.7%) touches it: the deficit is base rate-of-spread in cured grass/shrub,
not wind input.

Fuel-class late-miss across the OOS pool (blend 0.5) makes the address exact:

| Fuel | Cells | Late-miss |
|---|---|---|
| GS grass-shrub | 26,925 | 64.2% |
| GR grass | 16,502 | 52.2% |
| SH shrub | 8,740 | 47.2% |
| TU timber-understory | 4,473 | 8.0% |
| TL timber-litter | 3,609 | 15.7% |

Timber is solved; cured grass/shrub wind-runs are the entire remaining gap —
the same polarity and fuel concentration the first-pass hindcast flagged.

## Evidence gate: MET

Incidents with scoreable growth windows now total **11** (Gifford, Dillon,
Madre, Butler, Stookey, Cottonwood, Aspen Acres, Willow, Porcupine Ridge,
Brewer, Kaiser Canyon) — past the ≥10-incident bar set for deciding the
default-off realism knobs (`grass_ros_floor_frac`, `spotting_enabled`) for the
live watcher. Realism-pack replay results on the OOS pool: see
`scratch_review/hindcast_oos_data/oos_realism.json`.

Replaying the strict-OOS pool with the default-off knobs at blend 0.5
(`scratch_review/oos_realism.py`):

| Config (all at blend 0.5) | Coverage | Late-miss | Early-miss | Porcupine late |
|---|---|---|---|---|
| shipped (knobs off) | 48.6% | 51.4% | 0.0% | 82.8% |
| + grass floor (0.10) | 64.8% | 35.2% | 0.0% | 62.6% |
| + grass floor + spotting | 65.5% | 34.5% | 0.0% | 61.4% |

The grass floor takes **every normal window to ≈100% coverage** (Kaiser
Canyon's worst window 69.1% → 99.9%; the 7-pair normal subset goes from
89.1% to ≈100%) and cuts the blow-up window from 82.8% → 62.6% late, at zero
early-miss cost on this pool. It does **not** solve the blow-up: a 22.5 km /
12.9 h observed run remains far beyond even the floored model — plume-driven
runs likely need an explicit extreme-regime flag (red-flag wind + cured grass
⇒ alert on p-worst, not p50), not more ROS tuning.

Early-miss cost on the fought-fire calibration set
(`scratch_review/oos_realism_cal.py` → `cal_realism.json`):

| Config (calibration set, 14 pairs) | Coverage | Late-miss | Early-miss | Median head-ratio |
|---|---|---|---|---|
| blend 0.5 (shipped) | 73.5% | 26.5% | 0.0% | 1.77× |
| + grass floor (0.10) | 91.5% | 8.5% | 0.0% | 2.78× |
| + grass floor + spotting | 98.8% | 1.2% | 0.0% | 4.16× |

**Early-miss is 0.0% under every variant on both sets** — the p90 band never
lands entirely before the true window, so the floor/pack never produce a
provably-wrong "already passed" forecast. The cost is trigger timing, not
correctness: the median forecast head runs 2.78× (floor) / 4.16× (pack)
ahead of observed on fought fires (part physics-honest — suppression is
unmodeled — part genuine over-forecast). For the alert ladder that means
earlier WATCH entries and earlier P90-crossings ⇒ more precautionary coats.

## Recommendation (founder decision)

1. **Keep gust blend 0.5** — validated out-of-sample, no early cost, no
   evidence for more.
2. **Flip `grass_ros_floor_frac=0.10` ON in the shadow watcher** — the
   evidence gate (≥10 incidents) is met; it takes normal windows to ≈100%
   and calibration coverage to 91.5% at zero early-miss. Shadow dry-notify
   mode is precisely the place to observe the trigger-rate/coat-count
   inflation before any client-facing flip. Engine default stays OFF
   (byte-identity).
3. **Hold spotting OFF** pending the extreme-regime design: the marginal
   coverage (+7.3 pp on calibration) costs a 4.16× median head — better
   addressed as an explicit red-flag-conditions mode than an always-on knob.
4. **Blow-ups (Porcupine-class, 22.5 km/13 h) are not a ROS-tuning problem.**
   Detect the regime (red-flag wind + cured grass/shrub fuelscape) and alert
   on the pessimistic band with a MODEL-EXCEEDED banner, per SAFETY SEMANTICS.
5. Keep accumulating: the nightly-hindcast job (Phase 1.4) re-runs this
   scorecard as fires land, catching drift and growing the per-fuel evidence.

## Addendum (same day): founder flipped the floor + the regime flag shipped

Founder decision: grass floor ON in the shadow watcher; extreme-regime
condition to be generated. The condition was **measured, not assumed**
(`scratch_review/extreme_signature.py` → `extreme_signature.json`): across
all 22 scored windows, a red-flag-weather clone (wind ≥ 11 m/s, RH ≤ 15%)
flags NONE of the blow-ups — Porcupine ran at 3.5 m/s sustained / RH 27% in
reanalysis while several perfectly-forecast windows were windier and drier
(Dillon 8.5 m/s eff / RH 9%; Stookey 8.7 / 10). The measured separator is the
FUELSCAPE: Porcupine's domain is 88% grass-class (GR+GS), the highest scored,
and Madre's grass run 69%; every window under 60% grass-class scored ~0%
late once the grass floor was on. 3 of the 4 residual-late windows are FIRST
capture pairs of young fires (initial-attack mapping lag) — a data artifact
the live path already mitigates by seeding perimeters at their own provenance
time.

**Shipped condition** (`threat._extreme_regime_now`, contracts knobs,
default OFF): burnable domain ≥ 0.60 grass-class (GR+GS) AND max effective
wind (fixed hindcast blend 0.5) ≥ 4.0 m/s within 24 h. On the 22 windows it
arms Porcupine (88%, eff 6.0) and both Madre windows (69%, eff 8.6/4.3) —
catching the two grass blow-ups at one benign-window false positive; it
deliberately does not claim the two mapping-lag windows. **Effect when
armed**: that incident's alert-ladder stages evaluate on the pessimistic
(p10) arrival instead of the median (IMPACTED stays on the median), every
message carries an EXTREME SPREAD REGIME banner, and each regime toggle
emits an operator event so shadow ops can count posture frequency. Crew
safety already runs on p10 and is untouched.

**Live watcher status (same date).** wg-response-watch healthy on the
gust-calibrated image: 45 real SoCal WFIGS incidents tracked, 12 stale FIRIS
incidents correctly excluded by the 168 h staleness gate, one feed hiccup
self-healed (341 pairs blind exactly one cycle, all resumed), zero client
threats active (correct — no major fire near the mock clients). Finding:
effective cycle ≈ 26 min (fetch-bound over 45 incidents), not the 5-min poll
target — the cadence constraint is per-incident fetch fan-out, not the poll
loop.

**Standing caveat.** Suppression is unmodeled; on heavily-fought fires the
safe polarity is over-prediction, and covered/late here score the model's
uncertainty band against interval-censored observation windows — see
`docs/calibration/hindcast_first_pass.md` for semantics.
