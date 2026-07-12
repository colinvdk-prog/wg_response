# Model Fidelity Review — wgresponse spread/threat forecast lane

**Date:** 2026-07-09 · **Reviewer:** model-fidelity lane · **Scope:** the MODEL (fuels,
Rothermel, spread, ensemble, threat orchestration), not code plumbing.
**Method:** small deterministic experiments in `scratch_review/` (exp1, exp2, exp3, exp5,
exp6 — all `py -3 scratch_review/<name>.py` from repo root, fixed seeds, reproducible).
Product code untouched; 453-test suite green before and after.

**Polarity convention:** LATE = model predicts arrival later than truth → missed/late
alerts and unsafe crew margins (the dangerous one). EARLY = false alarms → cost/cry-wolf.

## Ranked findings

| # | Finding | Polarity | Severity | Fix effort |
|---|---------|----------|----------|-----------|
| F1 | 26/40 burnable FBFM40 codes act as fireproof walls | LATE (alert never fires) | CRITICAL | S |
| F2 | Wind direction never perturbed — P10 is not pessimistic off-axis | LATE (crew-safety P10) | CRITICAL | S/M |
| F3 | Grass ROS ~2–3× slow vs empirical anchors; spotting OFF; no hindcast harness exists | LATE | HIGH | M |
| F4 | Perimeter ring-vertex seeding: inside-perimeter clients get +2–11 h ETAs; sparse vertices add ~5 h; seeded at now, not perimeter time; hotspots shadowed | LATE | HIGH | M |
| F5 | containment_pct parsed but never modulates anything | EARLY (false alarms) | MEDIUM-HIGH | S/M |
| F6 | ETA jitter 12–16% across 15-min cycles (re-seed on round(t0_s), 6-member uniform draws) | BOTH (threshold noise) | MEDIUM | S |
| F7 | Single-point weather at domain center for up to ~120 km domains | BOTH (LATE when center is marine, fire inland) | MEDIUM | S/M |
| F8 | No empirical quantile calibration; perturbation scales constant-in-lead, 6 samples | LATE risk at long lead | MEDIUM | M/L |
| F9 | Live-fuel-moisture seasonality hardcoded to cured fire-season; `live_moisture_defaults()` is dead code | EARLY off-season | LOW-MED | S |
| F10 | Edge cost uses departure-cell ROS only (digest §3.3 spec'd endpoint mean) | both, boundary-local | LOW | S |

---

## F1 — FUEL_TABLE covers 14/40 burnable models; the other 26 are fireproof walls

**Evidence (exp1_fuel_gap.py).** `fuels.FUEL_TABLE` holds 14 burnable rows
(101, 102, 104, 121, 122, 142, 145, 147, 161, 165, 183, 185, 188, 201) + 5 NB rows.
**Missing 26 burnable codes:** 103, 105–109 (GR3, GR5–9), 123–124 (GS3–4), 141, 143,
144, 146, 148, 149 (SH1, SH3, SH4, SH6, SH8, SH9), 162–164 (TU2–4), 181, 182, 184,
186, 187, 189 (TL1, TL2, TL4, TL6, TL7, TL9), 202–204 (SB2–4).
`spread.compute_arrival` builds `fuel_of[i] = FUEL_TABLE.get(code)` only when
`is_burnable(code)` (spread.py:152–155); a burnable code with no row yields `None` =
never receives arrival, never propagates — identical to NB1 urban.

Band experiment (GR2 corridor, 5 m/s tailwind, client 15 km downwind, 270 m
cross-band at 6.7 km):

| band fuel | client ETA |
|---|---|
| none (uniform GR2) | 23.1 h |
| GR1 101 (present, sparse grass) | 23.9 h (+0.8 h) |
| **GR3 103 (burnable, MISSING row)** | **inf — never fires; 65% of grid never burns** |
| NB1 91 (true non-burnable) | inf (identical behavior) |

A **270 m** strip of any missing code fully suppresses every downstream alert
(NOTICE/EXTREME never trigger; client shows unreached forever), where a complete
table would have cost 0.8 h.

**WUI-CA exposure.** The Malibu LFPS sample in the research digest (91/102/122/183)
is covered, and most missing codes are humid-climate variants — but genuinely
CA-relevant missing rows exist: **GR7** (high-load dry grass, foothills), **SH1**
(low-load dry shrub — post-fire regen chaparral/coastal sage, ubiquitous in recent
SoCal burn scars), **TL6** (broadleaf/oak-woodland litter, classic CA WUI), TL2/TL4/TL7/TL9
(litter variants under mixed conifer/oak), TU4, and **SB2/SB3** (Sierra logging slash).
Any LFPS tile containing these renders those cells fireproof.

**Alerting risk in one sentence:** one LANDFIRE pixel-band of oak-litter or regen-chaparral
between fire and client silently converts a 23-hour threat into "no threat, ever" — the
worst possible failure mode for an alerting product.

**Ticket (S):** import all 40 FBFM40 rows from pyretechnics, numerically diff vs
firelab/behave, pin with a fingerprint test (T-1 discipline); add a unit test asserting
`is_burnable(c) ⇒ c in FUEL_TABLE` for all 101–204 codes present in the FBFM40 spec,
and a spread-level regression that no burnable code behaves as a barrier.

---

## F2 — wind DIRECTION is not in the ensemble; P10 is not pessimistic off-axis

**Evidence (exp6_direction_not_perturbed.py).** `ensemble_arrival` perturbs only two
scalars — wind SPEED (±12.5% effective) and moisture (±10%) (spread.py:296–301). The
research digest (§6.4) prescribed ±30° direction perturbation; none exists. Uniform GR2,
6 m/s wind, clients 6 km out; true direction error of only ±20° (routine at 24–72 h lead):

| client | ensemble p10–p90 band | ETA @ dir 250/270/290 | rotation swing |
|---|---|---|---|
| on-axis | 6.0–7.8 h (1.8 h band) | 12.5 / 7.2 / 12.5 h | 5.3 h |
| 25° off-axis | 15.5–15.9 h (**0.35 h band**) | 33.0 / 15.8 / **8.0 h** | **25.0 h** |
| 60° off-axis | 49.7–55.4 h (5.6 h band) | 80.3 / 51.8 / **28.6 h** | **51.7 h** |

At 25° off-axis the ensemble band is **71× narrower** than the ETA swing from a 20°
direction error, and eta_p10 (15.5 h) is **7.5 h LATER** than the arrival under a
plausible rotation (8.0 h). Every crew-safety number keys off P10
(`latest_depart = p10 − 30 min`); for flanking properties that P10 is fiction. Worse,
the band is *narrowest* exactly where real uncertainty is largest (speed scaling moves
head ROS and ellipse eccentricity in partially cancelling ways laterally).

**Ticket (S/M):** add a per-member direction offset (e.g. uniform ±30° at max lead,
same lead-mean-scalar treatment as speed) drawn from the member RNG; rerun exp6 as the
acceptance test (band must cover the ±20° rotation ETAs). One-line perturbation in
`compute_arrival` (rotate `s.wind_dir_deg`), plumbed as a third scalar knob.

---

## F3 — core grass ROS runs ~2–3× slow vs empirical anchors, spotting OFF, and the
## promised hindcast harness does not exist

**Evidence.** The repo's own anchor test (tests/test_rothermel.py:30–45) documents that
the faithful chain (10-m → 20-ft → WAF midflame) yields **0.154 m/s = 3.1% of the 10-m
wind** for GR2 at 5 m/s vs the Cruz & Alexander (2020) 10% rule (~0.5 m/s) — "Rothermel's
documented grass underprediction" — and passes by bracketing. exp3 at Santa Ana
conditions (12 m/s, 10% RH): GR2 = 2.98 km/h vs 10%-rule 4.3 km/h vs Tubbs observed
6.4 km/h sustained — **0.69× the rule, 0.47× Tubbs**, before considering that spotting
(the mechanism that made Camp/Tubbs beat all no-spotting models) is default-OFF with
`ember_buffer_min = 0`. The ARCHITECTURE §4 regression harness (Camp/Tubbs/East
Troublesome, ±50% landmark arrival) exists **only as prose** — no test or script runs it
(test_docs.py merely asserts the words appear in the doc).

**Polarity/severity:** systematic LATE on the fastest common approach fuel. A grass fire
the 10% rule puts 24 h out is modeled at ~50–70 h — the entire NOTICE→EXTREME ladder
compresses or vanishes.

**Ticket (M):** build the hindcast harness (see F8 — same infrastructure): seed Tubbs
ignition with archived weather + LANDFIRE fuels, assert Coffey-Park-class landmark
arrival within ±50%. If it fails LATE (expected), evaluate (a) grass-specific midflame
treatment / uncapped-wind check, (b) an explicit grass calibration multiplier
(ELMFIRE-style ADJ factor per fuel class, config-pinned), (c) turning spotting ON for
wind ≥ threshold with the Sardoy defaults.

---

## F4 — perimeter seeding: interior is forecast-late, inside clients get hours-out
## ETAs, sparse vertices add hours, and the seed time is wrong

**Evidence (exp5_perimeter_seeding.py).** 3×3 km square perimeter on GR2, 5 m/s wind
pushing east, seeds = ring vertices at `t0 = now_s` (threat.py:201; contracts.py
`ignition_points()`):

| client | densified ring (150 m) | 4-corner ring |
|---|---|---|
| center, 1.5 km inside | p50 **+2.34 h** (truth: burned/IMPACTED) | **+11.10 h** |
| 200 m inside downwind edge | p50 **+4.26 h** (truth: burning now) | +9.90 h |
| outside, 3 km E of edge | +4.54 h | **+9.73 h (+5.2 h from sparsity alone)** |
| outside, 8 km E of edge | +12.23 h | +17.21 h (+5.0 h) |
| interior cells | median +2.28 h, max +4.57 h | median +6.50 h, max +18.67 h |

Four distinct model errors:
1. **Interior re-burn:** cells inside the ring get forecast arrivals of +2…+19 h instead
   of "already burned". A client inside the perimeter is told fire arrives in 4–11 h
   (alert ladder shows EXTREME with hours of runway, not IMPACTED), and dispatch math
   could still schedule a gel stop onto a burning property (the 500 m corridor erosion
   only partially papers over this near the ring).
2. **Downwind-edge clients:** a client 200 m inside the east edge is "reached" not from
   the adjacent east-ring seeds (backing spread against the wind ≈ 1.5% of head) but by a
   head run from the west ring — hence +4.3 h. LATE by construction.
3. **Vertex sparsity:** seeds are ring VERTICES, not cells-under-polygon. A coarse or
   simplified polygon (4 corners here) adds **+5 h** to outside clients because mid-edge
   fire position is lost. WFIGS perimeters are usually dense, but FIRIS tactical or
   simplified rings are not guaranteed to be.
4. **Seed time = now_s, not perimeter time:** ARCHITECTURE §4(6) says "cells under the
   current fused perimeter at the perimeter timestamp"; the code seeds vertices at
   `now_s`. Nightly-IR perimeters are routinely 6–24 h stale (fire_feeds digest: IR
   flights once-nightly), so the real front is `age × ROS` beyond the seed — at Santa Ana
   chaparral ROS ~3 km/h a 12 h-old perimeter is **~36 km short**. Compounding: when a
   perimeter exists, `ignition_points()` DISCARDS hotspots (contracts.py:252–259), so
   fresh FIRMS detections beyond the stale ring — the exact growth signal the feeds lane
   was built for — never advance the seeds. All LATE.

**Ticket (M):** replace vertex seeding with polygon rasterization: mark all cells under
the fused perimeter as `arrival = updated_at_s` (or `min(updated_at_s, now)`) —
interior becomes already-burned, edges seed densely, and any client inside goes straight
to IMPACTED; union in hotspots newer than the perimeter as additional seeds. Point-in-
polygon on the grid is ~30 lines; keep ring vertices as the fallback for degenerate
polygons.

---

## F5 — containment_pct never modulates spread or alerting

**Evidence (exp5, part d).** `containment_pct` is parsed by every feed adapter
(feeds.py:281, 316, 440) and fused (feeds.py:258–259), then read only by the CLI
display (cli.py:294). Neither `spread.compute_arrival` nor `threat.py` mention it
(verified by source inspection in exp5 — both `False`). The exp5 incident with
`containment_pct=95.0` produces byte-identical ensembles to 0% by construction: a
95%-contained mop-up fire with a big perimeter projects the same 12 h ETA at an 8 km
client as a 0% wind-driven fire, and will hold NOTICE/EXTREME states for days.

**Polarity/severity:** EARLY — a chronic false-alarm source (most active-incident days
are high-containment days), eroding client trust in the 72 h notice and burning crew
capacity on non-threats. Not safety-dangerous, but it is the largest *credibility* gap.

**Ticket (S/M, part policy):** containment is a scalar (no contained-sector geometry),
so do NOT alter physics per-cell; instead gate the alert/dispatch layer: e.g. require
(containment < X%) OR (FIRMS hotspot within the last N hours) for NEW escalations, and
annotate alert messages with containment. Keep spread untouched (fail-dangerous). The
threshold X is a Colin/policy decision; plumb as `ResponseConfig` knobs.

---

## F6 — ETA jitter across 15-min cycles: 12–16%, not the claimed 2–5%

**Evidence (exp2_eta_jitter.py).** Static point fire, uniform GR2, CONSTANT 3 m/s wind,
default ensemble (n=6), 8 consecutive cycles t0 = T +k·900 s. Hours-to-impact should be
flat; measured per-cycle p50/p10 (client at 8 km, mean p50 23.5 h — i.e. straddling the
24 h EXTREME threshold):

```
cycle: 0     1     2     3     4     5     6     7
p50:  22.91 24.53 20.80 24.56 23.74 24.47 22.50 24.51   swing 3.76 h (16.0%)
p10:  21.01 21.96 20.37 22.93 20.55 23.05 21.34 20.73   swing 2.69 h (12.5%)
```

Same relative swing at every distance (4 km: 115 min; 20 km: 566 min = **9.4 h** p50
swing on a ~59 h ETA). Root causes, in order: (1) members re-seed on `round(t0_s)`
(spread.py:297) — every cycle draws 5 brand-new uniform scales; (2) n=6 members make the
0.10/0.50 sample quantiles extremely noisy for uniform draws; (3) GR2's wind exponent
(φ_w ∝ U^~1.5) amplifies ±12.5% wind scale into ~±19% ETA. The start-relative weather
bucketing adds a smaller deterministic sawtooth (nominal-only with one wind shift:
53 min stepwise, scenario B).

**Ladder interaction (measured):** at the 24 h line the pair escalates EXTREME on the
first low draw (cycle 0, 22.9 h — escalation is immediate) and hysteresis then locks it
in (de-escalation needs p50 > 30 h twice — never met): jitter → premature-but-sticky
EXTREME (EARLY). At the 72 h line (client at 26 km, mean p50 ~76 h; first exp2 run):
p50 wandered 67.9–80.2 h, crossing 72 h four times in two hours — first-NOTICE timing is
effectively a coin flip ±6 h, and near-boundary clients can cycle
NOTICE→WATCH→CLEARED→NOTICE (repeat client notifications). `clear_cycles=2 +
clear_margin_h=6` absorbs downgrade flapping at 24 h scale (3.8 h swing < 6 h margin)
but NOT at 72 h scale (12.3 h swing > 6 h margin).

**Ticket (S):** (a) seed members on (incident_id, member index) only — drop `round(t0_s)`
from the seed so each member's perturbation persists across cycles and the ensemble
evolves smoothly; (b) replace random uniform draws with fixed stratified scales
(e.g. member k at quantile (k+0.5)/n of the ±frac range — deterministic Latin sample),
which also makes p10/p50 stable estimators at n=6. Acceptance: exp2 scenario A swing
< 15 min at all three clients.

---

## F7 — one point forecast for a domain that can span ~120 km

**Evidence (exp3_weather_sensitivity.py + code).** threat.py:199:
`wxf = self._weather.forecast(domain.center(), horizon_h)` — one forecast for the whole
tile; clients up to `_MAX_THREAT_RADIUS_M = 60 km` from the ignition are folded into the
same bbox, so the center can sit tens of km from the fire. Plausible SoCal same-moment
split (marine layer at the center vs inland canyon at the fire): GR2 ROS 0.24 vs
0.84 km/h (**3.5× ⇒ ETA 3.5× too long**), SH5 0.82 vs 2.18 km/h (2.7×); vs Santa Ana
core 12 m/s/10% RH: 12.3× (GR2). Polarity is condition-dependent; the dangerous case
(marine-biased center, wind-driven fire) is LATE and entirely plausible for a coastal
client book. NWS forecastGridData is already fetched per-point but only at one point —
the 2.5 km NWS grid would support multi-point sampling with zero new provider work.

**Ticket (S then M):** step 1 (S, one line): forecast at the **ignition centroid**, not
`domain.center()` — weather at the fire drives spread; clients' weather is irrelevant to
arrival physics. Step 2 (M): 4-quadrant (or fire + client-cluster) point forecasts with
nearest-point lookup in `cell_params` — the `wx.at()` seam already takes a time; extend
to (time, cell) with a small provider-side cache.

---

## F8 — quantiles are uncalibrated design guesses (and structurally too narrow)

**Evidence.** p10/p50/p90 come from n=6 members with ONE constant scalar wind scale
(uniform ±12.5%) and ONE moisture scale (±10%) per member (spread.py:283–301) — no
direction error (F2), no lead-time growth (the ramp is collapsed to its horizon-mean,
so the band is too wide at 6 h and too narrow at 72+ h), no spatial error, 6-sample
quantiles. OPEN_QUESTIONS T-4 already flags P10 as "should be VERIFIED pessimistic";
nothing has been verified. Given F2/F3, current P10 is demonstrably optimistic in at
least two regimes (off-axis, grass).

**Ticket (M/L) — calibration harness sketch (sprint-ticket shape):**
1. **Data:** WFIGS perimeter time series (feeds already parse `perimeter` +
   `updated_at_s`); archive N≥10 CA incidents with ≥3 perimeter updates each (one-time
   pull script, store as JSON fixtures). Weather: Open-Meteo historical API (or archived
   HRRR) at the fire centroid; terrain: LFPS tiles (cache once).
2. **Replay:** for each consecutive perimeter pair (P_t, P_{t+Δ}): seed from P_t at its
   timestamp, run `ensemble_arrival`, and for every cell newly inside P_{t+Δ} compute
   PIT rank of observed arrival (bounded by [t, t+Δ]) within the modeled member arrivals.
3. **Score:** coverage curves (fraction of observed arrivals ≤ modeled p10 / p50 / p90 —
   target ≥90% of observed arrivals AFTER modeled p10) + landmark ETA error vs the
   Tubbs/Camp anchors (F3 shares this harness).
4. **Fit:** grid-search wind_perturb_frac / moisture_perturb_frac / direction_frac /
   grass ADJ multiplier to hit p10 coverage; pin results as config + a regression fixture.
   Deliverable: `scripts/calibrate_quantiles.py` + fixtures + a doc table of before/after
   coverage.

---

## F9 — live-fuel-moisture seasonality is dead code; engine is cured-summer year-round

**Evidence.** `fuels.live_moisture_defaults(season)` (winter 1.20/1.20 … summer
0.30/0.60) is exported per contracts but called by NOTHING in the engine — spread.py:169
reads `cfg.live_herb_frac/live_woody_frac`, whose defaults are the cured fire-season
values, and threat.py never sets them. In January green-up a GR2/GS2 cell still burns as
fully cured dynamic grass (curing fraction = clamp((1.20−0.30)/0.90)=1.0 → all herb load
transferred to dead). EARLY polarity (over-alerting in the off-season); acceptable
fail-dangerous default, but the function's existence suggests wiring was intended and
forgotten.

**Ticket (S):** derive season from the forecast month at cycle time (or a config
override), call `live_moisture_defaults`, and log the chosen scenario into
CycleResult.notes; upgrade path = WFAS NFMD live fuel moisture (digest §1.8).

---

## F10 — edge traversal uses departure-cell ROS only (minor)

`compute_arrival` prices edge u→v entirely from cell u's fuel/params
(spread.py:213–234); digest §3.3 specifies the endpoint MEAN. Crossing fast→slow fuel
boundaries is EARLY by ~one cell, slow→fast LATE by ~one cell; net effect is
boundary-local (≤ minutes at 90 m cells) and dwarfed by F1–F4. Note for the eventual
numba rewrite; not worth a standalone fix now (S if bundled).

---

## Top 5 sprint candidates (in order)

1. **Full FBFM40 table import + no-barrier regression (F1, S).** Import all 40 rows from
   pyretechnics, diff vs behave, fingerprint-pin; test that every burnable code spreads.
   Removes the only failure mode that silently suppresses alerts entirely.
2. **Ensemble v2: direction perturbation + stable stratified members (F2+F6, S/M).**
   Per-member ±30°-at-max-lead direction offset; seed members on (incident, k), not
   round(t0_s); stratified scales instead of uniform draws. Fixes the off-axis P10 fiction
   AND the 16% cycle jitter in one change; acceptance = exp6 band covers ±20° rotation,
   exp2 swing < 15 min.
3. **Perimeter seeding overhaul (F4, M).** Rasterize cells-under-polygon at
   `updated_at_s` (interior = already burned → IMPACTED for inside clients), merge
   hotspots newer than the perimeter, fallback to vertices for degenerate rings.
4. **Hindcast + quantile calibration harness (F3+F8, M/L).** WFIGS perimeter replay with
   PIT/coverage scoring + Tubbs/Camp landmark anchors; outputs calibrated perturbation
   fractions and a grass ADJ decision with evidence.
5. **Weather at the fire, not the domain center (F7, S first step).** One-line change to
   forecast at the ignition centroid now; per-quadrant sampling as the follow-on.

(F5 containment gating is a strong 6th — small build, but it needs a policy decision on
the escalation gate before engineering.)
