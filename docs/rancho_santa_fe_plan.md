# Rancho Santa Fe Response Plan — 100 protected homes

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [calibration/stacked_rings_fix_2026-09-27.md](calibration/stacked_rings_fix_2026-09-27.md) §4.3–4.4. **Affected here:** :114–115, :172–183, :203–207, :872–878 and :975. The Porcupine 13× under-forecast is 0.52×. The 22-window signature is 28 windows with no blow-up. OOS late miss by fuel goes from GS 64.2 / GR 52.2 % to 12.6 / 20.8 %. The text below is left as written.

**GraphGel Events, Covenant / Rancho Santa Fe, CA (`rancho_santa_fe_ca`)**
Founder deliverable, 2026-08-01. Every number below is traced to a committed
artifact or to a run reproduced today; nothing is projected, estimated, or
carried over from marketing material.

## Label key — read this before quoting any number

| Tag | Means |
|---|---|
| **[RSF]** | Measured **on the Rancho Santa Fe tile**: the 90-run optimization grid (commit `17dda1d`), the cached LANDFIRE tile / road graph (`0c5e441`), or a run reproduced today on that ground. |
| **[5MAP]** | Measured on the **five-map suite** (Malibu / Paradise / Santa Rosa / Boulder / Park City) and applied to RSF as an **analog**. RSF is not in these samples. |
| **[ASSUMED]** | Operational doctrine or literature value. Not measured in this repo. |
| **[PENDING]** | A required input that does not exist yet — almost always supplier data. |

Two boundaries govern every figure in this document and cannot be argued away:

1. **These are COATING RATES, not survival rates.** The engine's outcome
   metric is "did GraphGel get onto this home in time, with the crew safely
   clear." `gel_effectiveness = 0.85` is a **[PENDING] placeholder** that
   never enters these numbers at all — it is a uniform multiplier inside the
   legacy *value* objective, and the shipped objective is `homes` (count).
   Coating rate × real effectiveness = survivability, and the second factor
   does not exist yet.
2. **Zero real GraphGel deployments have ever been scored.** The all-time
   census is 8,080 scenario simulations / 336,687 home-instances
   (`scratch_review/census_report.json`). No outcome number in this plan is a
   claim about the world stronger than "measured in simulation on real
   LANDFIRE fuel and real OSM roads."

---

## 1. Executive summary

**What 100-home RSF protection achieves, measured.** On the Rancho Santa Fe
tile, across 18 stress scenarios (3 wind regimes × 2 ignition distances × 3
wind bearings) × 100 dispersed estates × 5 crew/placement configurations —
**90 runs, 9,000 home-instances**:

| Metric | Result | Source |
|---|---|---|
| Ember-belt coating rate (homes within 1 km of actual burn, or reached by the front) | **69.5 – 70.1%** across 8/10/12 crews | **[RSF]** `rsf_grid_rsf_c{8,10,12}_scatter.json` |
| Coating rate among all *threatened* homes | **76.6 – 77.7%** | **[RSF]** same artifacts, outcome tags |
| Crew-safety violations | **0** in all 90 runs | **[RSF]** |
| Crew overrun ticks (per-tick, not final-tick) | **0** in all 90 runs | **[RSF]** |
| Worst crew safety margin seen | **0.37 h (22 min)** scattered, 0.69 h clustered | **[RSF]** |
| Homes deliberately skipped by the objective (`triaged`) | **0** | **[RSF]** |

**This is the best result in the catalog, and it survives a matched
comparison.** The five-map suite excludes the 18 mph band, so the pooled
numbers are not directly comparable. Restricting RSF to the same intensity
set the five-map suite uses:

| Comparison (belt e1000, `deploy 10`) | RSF | Five-map |
|---|---|---|
| {high, extreme} × all wind bearings | **192/296 = 64.9%** | 435/903 = **48.2%** (best single map: Paradise 62.3%) |
| {high, extreme} × hazard axis only | **72/104 = 69.2%** | 181/321 = **56.4%** |

**[RSF]** / **[5MAP]**. RSF beats the pooled five-map figure by 12.8–16.7 pp
and edges the best individual map in the catalog. Two unmatched factors, both
named: RSF runs **8.3 homes per crew against the five-map suite's 5.0**
(100 homes / 12 crews vs 40 / 8), which the density series says should *hurt*
RSF; and RSF's bearing axis is ±30° against the five-map ±40°, which helps it.
The advantage is real but its exact size is not resolvable to a point
estimate.

**Why the geography is favorable — three measured reasons.**

1. **Dispersed estates de-synchronize the deadlines.** The Covenant's
   real settlement pattern is large-lot, and the engine's scattered placement
   reproduces it. Scattered scoring is tight and high (per-scenario belt rate
   min 50.0% / median 69.0% / max 88.9%, sd 11.4). The same 100 homes in 8
   tight clusters collapse into a lottery: of 15 scored scenarios, **two score
   0% and nine score 100%**, sd 37.7, and 3 more scenarios put nobody in the
   belt at all. **[RSF]** Community-scale deadline synchronization is the
   documented failure mode (`df60648`: a single 40-home cluster scores 24.6%
   against 55.0% for 4×10); RSF's dispersion is structural protection.
2. **Developed ground is a fuel break, at scale.** The tile is **47.81%
   developed and only 43.52% burnable** — the largest WUI band in the catalog
   (28,741 band cells; 17,667 developed-within-3-cells-of-fuel, next highest
   Boulder at 14,143). **[RSF]** Fire crossing the Covenant crosses a
   perforated fuelscape, not a continuous one.
3. **Road density removes the travel excuse.** 29,025 nodes / 35,386 edges,
   largest connected component **98.71%** — the second-densest graph in the
   catalog (Boulder 33,459 nodes; then Santa Rosa 24,184, Malibu 13,440,
   Park City 7,554, Paradise 4,429) and among the best-connected.
   **[RSF]** `data/roads/provenance.json`. Compare Paradise — the map whose
   chord-vs-road mismatch drove the whole plan-on-roads program — at
   4,429 nodes / 95.97% LCC.

**Two decisions this plan asks for, both newly measured on RSF ground.**

1. **Set `ember_threat_radius_m = 300` for RSF.** 63 of every 100 placed RSF
   clients sit on cells LANDFIRE calls non-burnable, and such a home receives
   no arrival time — no ladder state, no dispatch job — unless it inherits one
   from within this radius. **The live engine ships this at 0.0.** Measured
   A/B today: 200 → 300 m buys **+10 belt homes / +38 coated homes at 10
   crews, replicated at 12, with 0 violations and 0 overrun ticks**; 400 m
   starts manufacturing work rather than finding exposure. §4.5, §6.2.
2. **Buy lead time before the 13th crew.** The RSF-measured lead-time curve
   (run today) is **0% coated below 2 h of lead → 20% at 2–4 h → 69% at
   4–6 h → 81% at 6–8 h → 90% at 8–12 h.** Crew count from 8 to 12 moves the
   belt by 3 homes in 1,800. §3.4.

**And the honest boundary.** Every percentage above is an ember-belt **coating
rate** under the model's gel assumptions, on 15 h of simulated response with
detection assumed instantaneous. Specifically: the gel effectiveness that
converts a coat into a save is **[PENDING]**; the 72 h operational window
conflicts 12× with the wgdefense ~6 h coating-life model (`OPEN_QUESTIONS`
P-1); **smoke and visibility — historically the actual killer on evacuation
roads — are not modeled at all**, so no route verdict is a safety guarantee
(`transit_hazard_determination.md`); the engine is measured **late** against
real fires in cured grass (Tubbs GR2 anchor 0.46×) and cannot forecast
blow-ups at all (Porcupine Ridge 13× under-forecast); and the *system-level*
alerted-regime figure remains a **[5MAP] analog that cannot be measured at
RSF** because no extended (`_ext`) tile exists for this location (§6.4) — the
lead-time curve above is the RSF-measured part of that story, not a substitute
for it.

### Three corrections to the briefing premises

Cross-checking every number against its source surfaced three premises that
the repo does not support. They are corrected throughout this plan.

| Premise as briefed | What the repo actually says |
|---|---|
| "staffing rule ≤ 10 clients/crew" | **No such rule exists.** The documented law is **≤ 5 homes per committed crew** (`HOA_40_PLAYBOOK.md:100-102`, `tools/onboard.py`); 10 is a *point on the density curve* (51%). Already flagged as review-premise drift in `calibration_review_2026-07-31.md` §4.5 / §6 row 5. See §3.1 for what the RSF measurement actually licenses. |
| "wind_off +30 = more northerly approach" | **Backwards.** The RSF hazard wind is FROM 060°; `+30` = FROM **090° (due east)** — a *more easterly / zonal* approach. `−30` = FROM 030° (NNE) — that is the northerly one, and it is the **best**-scoring bearing, not the worst. The *direction of the finding* is right (+30 is worst); the geographic label was inverted. |
| "SOP triggers P90@24h standard / P50<24h immediate; Overwatch SOP" | **No P90 trigger and no Overwatch SOP exist in this repo.** P90 is explicitly *"presentation only — the optimistic edge of the client-facing band"* (`ARCHITECTURE.md:211`); triggering on it would be the wrong polarity for an alerting product. The shipped ladder is **NOTICE_72H at P50 ≤ 72 h → WATCH → EXTREME_24H at P50 ≤ 24 h OR P10 ≤ 12 h**, and under the extreme-spread-regime posture every stage evaluates on **P10** instead of P50 (`alerts.py`, `contracts.ResponseConfig`). §3.4 maps the real ladder onto RSF. |

---

## 2. The fuel and fire picture

### 2.1 Witch Creek corridor mechanics

The location is pinned to the 2007 Witch Creek geography: center 33.020 /
−117.200, **hazard wind FROM 060°** (Santa Ana, ENE), safe zones placed
coastward because that is the real evacuation direction. **[RSF]**
`simui.py` LOCATIONS, commit `0c5e441`.

The grid ignites **upwind at the hazard bearing** and lets the fire run down
the corridor into the community, at 6 km and 8 km — the two distances that
fit the ±9 km standard tile. Operationally this represents **a fire already
inside the San Dieguito corridor**, i.e. the short-lead reactive tail. The
historical Witch Creek run began ~35 km ENE near Santa Ysabel; that
long-approach case is exactly what the missing `_ext` tile would measure
(§6.4).

### 2.2 A grass-and-grass-shrub fuelscape

Recomputed today from `data/tiles/rancho_santa_fe_ca.wgt` (200×200 @ 90 m):

| Class | Cells | % of burnable | % of tile |
|---|---|---|---|
| **GS** grass-shrub | 11,617 | **66.7%** | 29.04% |
| **GR** grass | 2,242 | **12.9%** | 5.61% |
| **SH** shrub / chaparral | 2,172 | 12.5% | 5.43% |
| **TL** timber-litter | 1,337 | 7.7% | 3.34% |
| **TU** timber-understory | 41 | 0.2% | 0.10% |
| *(burnable total)* | 17,409 | 100% | **43.52%** |

Non-burnable: developed (91/93) **47.81%**, water 8.19%, barren 0.47%.
Top codes: NB1 47.8% · GS2 17.4% · GS1 9.3% · water 8.2% · SH5 5.0% ·
GR1 3.5% · TL3 2.8% · GS3 2.3%. Elevation 0–470 m, max slope 44°. **[RSF]**

**GR + GS = 79.6% of burnable.** The extreme-spread-regime fuel gate is
`extreme_regime_grass_frac = 0.60` — RSF clears it by **20 points**.

For scale, here is the grass-class fraction of every **real incident domain**
ever scored in this repo (`scratch_review/extreme_signature.json`, 22 windows
/ 8 incidents): Porcupine Ridge 88.2 · Madre 68.9 · Kaiser Canyon 52.5 ·
Brewer 42.5 · Stookey 38.9 · Gifford 31.0 · Butler 8.0 · Dillon 3.3. **Only
two of the eight clear the 0.60 gate.** RSF's tile-wide 79.6% is not one of
those windows — it is a simulator tile, not a scored incident — but it would
place **second, behind only Porcupine Ridge, the single true blow-up the
engine cannot forecast.** That is the company RSF's fuelscape keeps.

This is not a comfortable fact. It is the **exact fuel signature the engine
is measured worst on**: OOS late-miss by class is GS 64.2% / GR 52.2% against
TU 8.0% and TL 15.7% (`hindcast_oos_2026-07-28.md`). **RSF is 79.6% of the
fuel the model under-forecasts and 7.9% of the fuel it forecasts well.**

### 2.3 What the extreme-regime posture does on this ground

`extreme_regime_enabled` arms when **(a)** the incident's burnable domain is
≥ 60% GR+GS **and (b)** max effective wind over the next 24 h ≥ 4.0 m/s
(`threat.py::_extreme_regime_now`). Armed, the ladder evaluates **every stage
on the pessimistic P10 arrival** instead of the median, and every client
message carries an EXTREME SPREAD REGIME banner. Crew safety already ran on
P10 and is untouched.

**Consequence at RSF: the posture is effectively always-on during any wind
event.** Clause (a) is a permanent property of the tile (79.6% ≥ 60%), and
clause (b) at 4.0 m/s is only **≈ 8.9 mph** of effective wind — arithmetically
below even the engine's *low* intensity preset (8 mph sustained gives
3.58 m/s; under the simulator's 1.3 gust convention the blended effective wind
is 4.11 m/s, already over the line). Live gusts come from NWS rather than that
convention, so treat the exact crossover as indicative — but a Santa Ana does
not approach this threshold, it obliterates it.

Grade honestly: the *shape* of the detector is measured and good work — a
red-flag-weather clone flags **none** of the blow-ups, while fuel separates
them cleanly. But the *thresholds* 0.60 and 4.0 are **[ASSUMED]**: they are
round numbers placed on one side of **n = 1 true positive** across 22 scored
windows (`calibration_review_2026-07-31.md` §1.7). The failure mode is benign
— a false arm makes the ladder more pessimistic and never touches crew safety
— which is why arming it at RSF is the right call anyway.

**Recommendation:** run the RSF watcher with `--extreme-regime` armed as the
standing posture, and write the always-on behavior into the client
communications so a permanent banner does not become wallpaper (§5.3).

### 2.4 Per-class cold-black: this ground reopens in minutes

Stage-1 transit hazard assigns each fuel class its own cold-black window —
how long after the front passes a cell stays hazardous for **route selection**
(`fuels.cold_black_minutes`; the 10c safety audit never consults it and is
never relaxed). Weighted over RSF's actual burnable cells:

| Window | Cells | Share of burnable |
|---|---|---|
| 5 min (GR1/2/4/5/6/7) | 2,228 | 12.8% |
| 10 min (GS1-3 + GR3/8/9) | 11,631 | 66.8% |
| 20 min (SH1/3/5/6/9; GS4 where present — RSF has none) | 1,996 | 11.5% |
| 30 min (SH2/4/7/8) | 176 | 1.0% |
| 45 min (TU) | 41 | 0.2% |
| 60 min (TL) | 1,337 | 7.7% |

**91.1% of RSF's burnable ground is transit-passable within 20 minutes of the
front passing; 79.6% within 10 minutes.** **[RSF]** The legacy flat 45-minute
window — numerically the residence time of a 1000-hour log — was treating this
grass-shrub landscape as downed timber.

Operationally this is the **escape-friendly** half of the RSF picture: a crew
cut off behind the front in the Covenant is, in the model, minutes from
drivable black rather than an hour. Caveats that must ride with it: the
constants are **[ASSUMED]** literature residence times, the flip was proven
harmless but its benefit regime has never been exercised by any suite
(`calibration_review` §3.3), and **smoke is not modeled** — a thermal
PASSABLE verdict is not a visibility verdict.

### 2.5 The wind-bearing asymmetry — measured, and the label corrected

Rotating the hazard axis ±30° (c12 scattered, belt e1000):

| Wind FROM | Offset | Belt coated | Threatened homes | Coated / threatened | `abandoned_unsavable` |
|---|---|---|---|---|---|
| 030° NNE | **−30** | **145/198 = 73.2%** | 384 / 600 | 78.4% | 75 |
| 060° ENE (hazard axis) | **0** | 117/164 = 71.3% | 317 / 600 | **83.3%** | 46 |
| 090° E (due east) | **+30** | **73/116 = 62.9%** | 250 / 600 | **69.6%** | 59 |

**[RSF]** all rows. `+30` is worst by 10.3 pp of belt rate — but note the
mechanism, which is not what "worst case" usually means:

- `+30` threatens the **fewest** homes (250 vs 384) — the due-east run
  clips a narrower slice of the WUI band.
- Of the homes it does threaten, the **largest share is unsavable**
  (59/250 = 23.6% vs 14.5% on the hazard axis).
- Crews in `+30` scenarios finish with the **largest** safety margins of the
  whole grid (min margin 1.16 / 1.18 / 1.48 / 2.16 / 2.49 / 3.21 h, against
  0.37–1.10 h on `−30`).

Idle crews plus unsavable homes is the signature of a **fire-clock** loss, not
a capacity loss: the due-east approach reaches its victims faster than any
safe round trip fits, and there is nothing for a spare crew to do. This is the
same physics wall the five-map work found (727 of 878 lost homes unsavable at
any crew count, `DISPATCH_OPTIMIZATION` §3) and it is why §3.1 does not
recommend buying more crews to fix it.

**Plan for `+30` anyway.** It is the worst measured bearing, its lead times
are the shortest, and a due-east Santa Ana is entirely plausible on this
ground. The correct answer is lead time and evacuation discipline (§3.4, §5),
not fleet size.

**Open trace:** the `+30` denominator collapse (116 belt homes vs 198) has not
been decomposed — whether it is genuine geometry or a WUI-band placement
artifact is unknown (§6.3).

---

## 3. Recommended response configuration

### 3.1 Crew count: 10 crews / 30 responders

**Measured, at 100 dispersed estates on the RSF tile:**

| Crews | Belt e1000 | Coated / threatened | Coated (all runs) | Viol | Ovt | Min margin |
|---|---|---|---|---|---|---|
| 8 | 333/478 = **69.7%** | 728/951 = 76.6% | 728/1800 | 0 | 0 | 0.37 h |
| 10 | 332/478 = **69.5%** | 733/951 = 77.1% | 733/1800 | 0 | 0 | 0.37 h |
| 12 | 335/478 = **70.1%** | 739/951 = 77.7% | 739/1800 | 0 | 0 | 0.37 h |

**[RSF]** The 8→12 spread is **3 belt homes and 11 coated homes** across 1,800
home-instances. The measured resolution of this harness is **±1 belt home /
±6 coated homes** from the wall-clock solver budget (`calibration_review`
§5.1). Placement is held fixed across these three arms, so the ±10-home
placement noise does not apply — but the deltas are still small enough that
the honest reading is: **at 100 dispersed RSF estates, response is
travel-bound and clock-bound, not crew-bound, anywhere in 8–12 crews.**

**Recommend 10.** The rationale is not the belt rate — it is:

- **8 is the measured floor of what was tested, not a measured optimum.**
  The RSF grid never ran 4 or 6 crews. Claiming "8 is enough" extrapolates
  below the sampled range; 10 sits mid-band with a tested arm on either side.
- **The five-map crew ladder saturates at exactly 10** (belt 157 @4 → 158 @6
  → **168 @10** → 169 @12 → 167 @16 → 168 @24; 10→24 crews adds *zero* homes,
  commit `7d5b300`). **[5MAP]** Two independent measurements land on the same
  number.
- **10 crews × 3 responders = 30 people** is also the fleet that makes a
  whole-community pre-coat unhurried. Arithmetic, not simulation: 100 homes ×
  10 min ÷ 10 crews = **100 minutes of application per crew**; even allowing
  as much again for inter-stop travel on RSF's dense road network, a full
  community coat is a **~3–4 hour operation**. That fits inside an
  EXTREME_24H window with a day to spare, and inside the 4–6 h lead band
  where RSF measures 69.2% coating. (The five-map anchor for this arithmetic:
  2 crews cover 40 homes in ~5.3 h at 15 min/home. Both figures inherit the
  unverified deploy-time assumption of §3.2.)

**On the density law.** The documented siting rule is **≤ 5 homes per
committed crew** for reactive odds ≥ 57% **[5MAP]** — 100 homes would demand
20 crews. The RSF measurement contradicts it *on this ground*: the belt rate
is flat from 8 to 12, and the five-map ladder says 10→24 buys nothing. Both
statements can be true because they are different experiments (the density
series held crews at 4 and grew clients; the ladder held clients at 40 and
grew crews). **Do not quietly retire the ≤5 rule** — state plainly that RSF is
enrolled at 10 homes/crew, that this is 2× the documented density law, and
that the justification is a direct measurement on this tile plus a saturation
result, not a waiver.

**Surge to 12 when — and only when — the threat is simultaneous or
clustered.** The measured case for the 11th and 12th crew is not the scattered
grid (+3 belt homes, inside noise); it is the **cluster8** arm, where the
extra crews moved coated/threatened 79.3% → **82.9%** and coated homes
338 → 353 (+15) **[RSF]**. Concentrated demand is the only regime in this
grid where crew count did real work. Trigger the surge on: two or more
simultaneous ignitions in the AOI, an EXTREME_24H that lands on a
geographically concentrated subset of the roster, or a red-flag day with
another WG community already committed.

### 3.2 Crew composition and deploy time

- **3-person crews** (two applicators + one prep-runner staging the next
  property) — this is the crew model the 15 min/home figure is defined
  against, `server.py:368-372`. **[ASSUMED]** capability.
- **10 min/home target coats.** Every RSF number in this plan was measured at
  `deploy_min = 10`. **This is a program target, not a demonstrated
  capability** — it requires pre-rigged manifolds and quick-connects, and the
  playbook is explicit that *"if you measure 18 min/home, the plan uses 18, not
  15."* The sensitivity is **[5MAP] MEASURED**: 15 → 10 min = **+73 homes
  (+4.27 pp)** on the 60-scenario bench, comparable to doubling the fleet;
  30 → 10 = +151 (+8.83 pp).
- **The capability claim is gated on a supplier number.** Wet coverage rate
  (gallons/home at 1/8" dry film) is **[PENDING]** and it gates both the
  minutes-per-home figure and all inventory sizing (§6.1). Until it lands,
  10 min/home is an assumption load-bearing on ~4 pp of the headline.
- **Gel and water logistics:** **100 × per-home dose + 25% reserve**, split
  across **two storage points** so one blocked road cannot strand the whole
  inventory. The per-home dose itself is **[PENDING]** — coverage is quoted
  *wet* while the rated film is *dry*, so inventory math needs the wet
  gal/home number, not a conversion.
- **Field-drill acceptance:** measure real min/home in a water-only EXTREME
  exercise and push the measured value into the config before go-live. If it
  comes back at 15, expect roughly the 5-map deploy sensitivity to apply and
  re-run this grid before quoting §4 to anyone.

### 3.3 Staging: three coastward anchors, and a hard rule about forward staging

The three catalog safe zones, distances and bearings computed today from
`simui.LOCATIONS`:

| Zone | Node | Distance from center | Bearing |
|---|---|---|---|
| `-z1` | I-5 / Via de la Valle (Del Mar) | **6.30 km** | 220.6° (SW) |
| `-z2` | Encinitas Blvd / El Camino Real | **6.43 km** | 297.8° (WNW) |
| `-z3` | Del Mar Heights Rd / El Camino Real | **8.44 km** | 202.8° (SSW) |

**[RSF]** geometry. All three sit **130–240° off the ignition bearing band
(030–090°)** — i.e. coastward, downwind of the community and away from every
modeled approach. That is the correct topology, and it is the standing
condition behind the grid's clean safety record: 90 runs, 0 violations,
0 overrun ticks.

**They are PLANNING-GRADE.** The source comment says so: *"plausible highway /
arterial nodes, not field-verified staging areas."* The engine is **fail-closed
on zones — no verified zone, no schedule** — so ground-verification is a hard
onboarding gate, not a nicety (§6.5).

**Forward staging doctrine: alert-stage only, never post-ignition.**

- Under the coating hold (§3.4), idle crews **preposition to the safe zone
  nearest their deferred first stop** — that is the engine's own behavior and
  it is measured net-positive **[5MAP]**.
- Do **not** park crews among the homes once a fire is running. The near-staging
  experiment deleted the entire crew approach leg and moved the belt **within
  placement noise** (c4 −10 / c8 +12 / c10 +1 belt homes) while the
  front-reached unsavable floor never moved at all — *and it created a new
  exposure*: **one crew staged directly into burn at tick 1**, caught by the
  SEM-10 sweep (`opt_ember_s_c10.json` carries `viol=1 / ovt=1`). **[5MAP]**
  Depots were already inside the 30-minute assumption (median 1.7 km to
  nearest client). Forward staging buys nothing measurable and adds a way to
  burn a crew.
- At RSF specifically the argument is even weaker: 29,025 road nodes at 98.71%
  connectivity is the second-densest, best-connected network in the catalog,
  and the crew-count flatness of §3.1 says travel capacity is already not the
  binding constraint here.

### 3.4 Alert ladder mapped to RSF

The shipped ladder (`alerts.py`, `ResponseConfig`) — these are the real
triggers, not P90:

| Rung | Trigger | Under armed extreme-regime |
|---|---|---|
| **NOTICE_72H** | P50 ≤ 72 h (one-time; auto-progresses to WATCH next cycle) | **P10** ≤ 72 h |
| **WATCH** | ongoing threat inside the notice window | ongoing, evaluated on P10 |
| **EXTREME_24H** | P50 ≤ 24 h **OR** P10 ≤ 12 h | **P10 ≤ 24 h** (or P10 ≤ 12 h) |
| **IMPACTED** | median arrival has passed | median (posture never moves impact) |
| **CLEARED** | 2 consecutive clear cycles with 6 h margin | same |

**At RSF, treat the extreme-regime column as the operating column.** Per §2.3
the fuel gate is permanently satisfied and the wind gate is satisfied by any
fire weather, so the standing RSF posture is: **alert on the pessimistic
(earliest credible) arrival, in every wind event.** That is the "immediate"
behavior the briefing was reaching for, expressed in the mechanism the engine
actually has.

**The coating hold is the operating doctrine, not an option.**
`dispatch_hold_h = 24`: while a home's fire is more than 24 h out (P50), crews
do **not** coat — they go on alert and preposition — but only while a per-cycle
feasibility probe (deferred starts, deadlines tightened by a 6 h weather
buffer) confirms deferral still covers every home the coat-now plan would.
Gate failure releases the hold, latched. Measured **[5MAP]**: served
882 → 931 (+5.6%), OOS 60/4 +4.1%, 30/3 +5.5%, **alerted-regime coverage
76.0% → 79.5%**, zero violations. It also avoids speculative coats on fires
that never arrive — which matters because the coating is **opaque** and owners
will not accept it preventively.

**Expected lead-time value — now partly RSF-measured.** The full alerted
benchmark still cannot be run here (no `_ext` tile, §6.4), but the *per-home
lead-time curve* can, and it was run today on the RSF grid's own parameters
(`probe_leadtime.run_one` unmodified, 18 scenarios × 100 homes, 10 crews,
deploy 10; lead = detection-to-ground-truth-arrival at the home's own cell,
detection at t = 0):

| Lead (detection → arrival) | **[RSF]** coated / reached | **[5MAP]** same probe, c8/d10 |
|---|---|---|
| 0–1 h | 0/7 = **0%** | 0/9 = 0% |
| 1–2 h | 0/2 = **0%** | 2/21 = 9.5% |
| 2–4 h | 2/10 = **20.0%** | 10/48 = 20.8% |
| **4–6 h** | 9/13 = **69.2%** | *(4–8 h)* 40/100 = 40.0% |
| **6–8 h** | 13/16 = **81.2%** | — |
| **8–12 h** | 35/39 = **89.7%** | *(8–16 h)* 89/171 = 52.0% |
| 12–16 h | 22/28 = 78.6% | — |
| 16–24 h | 39/50 = 78.0% | *(16–32 h)* 163/248 = 65.7% |
| 24–32 h | 37/48 = 77.1% | — |
| 32 h+ | 115/199 = 57.8% | 328/485 = 67.6% |

**This is the plan's central operational finding, and it is measured on RSF
ground: in the 4.4–6.7 h lead band that the five-map alerted benchmark used,
RSF homes coat at 69–81%; at 8–12 h of lead they coat at 89.7%.** Below 2 h
of lead the coating rate is **zero** — that is not a resourcing problem, it is
the 30-minute egress law plus travel plus cure time refusing to fit. The
evacuate-first protocol (§5.5) is what covers that band, and nothing else can.

Two readings that must ride with the table:

- **The 32 h+ band is truncated, not failing.** With `dispatch_hold_h = 24`
  and a 15 h simulated horizon, homes whose fire is more than a day out are
  deliberately held and the deferral never resolves inside the run. Read the
  curve between 2 h and ~24 h; beyond that it is measuring the hold, not
  capability.
- Homes whose cell never burns in-horizon were coated at **33.2%
  (461/1,388)** — precautionary ember-belt work, correctly not counted as a
  save.

**The remaining cross-map extrapolation.** The pooled alerted-regime figure
**[5MAP] 79.5%** (136/171 coated/threatened; `bench_alerted.json`: Boulder
100% @ 4.39 h lead, Santa Rosa 93.5% @ 6.65 h, Paradise 87.2% @ 6.7 h, Park
City 82.5% @ 4.88 h, Malibu 46.2% @ 0.79 h) is the *system-level* alerted
number and stays an analog until RSF has an extended tile. Note the RSF
reactive figure (77.1% coated/threatened) already sits within 2.4 pp of the
five-map *alerted* pooled figure — so the honest expectation for alerted RSF
is the high 80s / low 90s, **not** the 42% → 79.5% jump the five-map framing
suggests. Do not quote the five-map lift as an RSF lift.

*(Doc-vs-artifact drift noted while sourcing this: `HOA_40_PLAYBOOK.md`
publishes Paradise at 84.6%; `bench_alerted.json` says **87.2%**. Minor, but
it belongs on the §6 drift list alongside the six already recorded in
`calibration_review_2026-07-31.md` §6.)*

**The lever ranking is not in dispute, on any map.** Lead time converts
unsavable homes to savable ones 1:1 and is the only thing that beats the
reactive ceiling; perfect forecasts are worth **+22.43 to +25.55 pp**
**[5MAP]**, an order of magnitude more than every dispatch optimization ever
shipped combined. Buy detection before you buy the 13th crew.

---

## 4. Expected outcomes

**Read the columns carefully — they are different denominators.** "Belt" =
coated among homes within 1 km of actual burn (or front-reached). "Of
threatened" = coated among homes the forecast gave a live arrival to.

### 4.1 By wind regime

The engine's intensity presets *are* wind speeds (`simui.INTENSITY_WEATHER`):
low 8 mph / moderate 18 / high 28 / extreme 40. So the RSF intensity axis is
the wind-regime axis.

| Wind | **[RSF]** all bearings (100 homes, 12 crews) | **[RSF]** hazard axis only — *matched to the 5-map column* | **[5MAP]** analog (40 homes, 8 crews, hazard axis) |
|---|---|---|---|
| **8 mph** (low) | *not run at RSF* | *not run at RSF* | 88.8% (191/215) |
| **18 mph** (moderate) | **78.6%** (143/182) | **75.0%** (45/60) | 83.9% (182/217) ⚠ **9 violations / 13 overrun ticks** |
| **28 mph** (high) | **68.4%** (93/136) | **75.6%** (34/45) | 69.0% (107/155) |
| **40 mph** (extreme) | **61.9%** (99/160) | **64.4%** (38/59) | 44.6% (74/166) |

Sources: **[RSF]** `rsf_grid_rsf_c12_scatter.json`; **[5MAP]**
`census_axis_sensitivity.json` `wind_speed_curve` (crews 8, deploy 10,
wind_off 0 screen). Even matched on bearing the arms differ in client and crew
count (100/12 vs 40/8) — RSF runs 8.3 homes/crew against 5.0.

**Read the table by row, not as a single verdict.**

- **40 mph is where RSF's geography pays: 64.4% matched, against 44.6% —
  +19.8 pp in the regime that destroys communities.** That is the row that
  matters commercially and it is the row where the advantage is largest.
- **28 mph is a wash matched** (75.6% vs 69.0%, and 68.4% pooled across
  bearings).
- **18 mph, RSF scores *below* the five-map analog** (75.0% matched vs
  83.9%). Do not claim RSF is better everywhere. The most likely reading is
  that at low wind speeds every map has slack and RSF's heavier client load
  per crew starts to show — but that is an interpretation, not a measurement.
- **The flattest curve wins the argument.** RSF moves 78.6% → 61.9% across
  18→40 mph (−16.7 pp); the five-map suite moves 83.9% → 44.6% (−39.3 pp).
  RSF degrades roughly half as fast with wind. **[RSF]/[5MAP]**

For the alerted regime, do not read a wind row — read the RSF-measured
lead-time curve in §3.4. `bench_alerted.json` pools five maps at one
intensity; splitting it by wind band would manufacture precision that does
not exist.

**⚠ One safety finding you must carry into the moderate row.** The census
session measured the sub-30 mph band for the first time and found the
calibrated corridor stack **failing there on the five-map suite**: 18 mph
produced **4 violations / 14 overrun ticks at 4 crews and 9 / 13 at 8 crews**,
and more crews made it *worse* (`calibration_review` §3.0; attribution A/B
still in flight). **RSF's 30 moderate-wind runs produced 0 violations and 0
overrun ticks** — the failure did **not** reproduce on this tile. That is
encouraging and it is **not** a clearance: different fuel, different roads,
different client count, and the general finding remains unadjudicated. Until
the attribution A/B lands, treat 18 mph RSF operations as the least-validated
band despite scoring the highest.

### 4.2 By approach bearing (RSF measured)

| Wind FROM | Belt | Of threatened |
|---|---|---|
| 030° NNE (`−30`) | **73.2%** | 78.4% |
| 060° ENE (hazard axis) | **71.3%** | 83.3% |
| 090° E (`+30`) | **62.9%** | 69.6% |

### 4.3 By ignition distance (RSF measured)

| Ignition | Belt |
|---|---|
| 6 km | 164/230 = **71.3%** |
| 8 km | 171/248 = **69.0%** |

Distance barely moves the result inside the tile — consistent with the
five-map finding that lead time only becomes the dominant variable at
detection ranges the standard tile cannot represent.

### 4.4 Where the uncoated belt homes go (RSF measured, c12 scattered)

| Outcome | Homes | Share of belt |
|---|---|---|
| **Coated** (`saved` + `protected_then_impacted`) | 335 | **70.1%** |
| `abandoned_unsavable` — fire beat every feasible crew | 116 | 24.3% |
| `not_threatened` — **the engine never projected an arrival** | 27 | **5.6%** |
| `triaged` — deliberately skipped by the objective | **0** | 0% |

The zero in the last row matters: at RSF the count-max objective never had to
sacrifice a home. The 5.6% in the third row is not physics — it is the
detection gap described next, and it is recoverable.

### 4.5 A measured improvement found while writing this plan

**The problem.** RSF is 47.81% developed, and a home on a non-burnable cell
**never receives an arrival time** from the surface model. Measured today
across all 18 grid seeds: **56–69 of every 100 placed RSF clients sit on
non-burnable cells (mean 63.2)** — far above the 23–46% measured on Malibu,
the only tile where this had ever been counted (`eta_gap_report.md`), and
exactly the census `calibration_review` §5.7 flags as required *"before any
client-facing coverage claim."* **[RSF, new]**

The mitigation is `ember_threat_radius_m`: such a home inherits the earliest
arrival within the radius. The simulator ships **200 m**; the **live engine
ships 0.0 — off.** Measured nearest-burnable distance for those RSF clients
(n = 1,137): median 90 m, **p90 255 m, p99 325 m, max 382 m** — so 200 m
covers **80.2%** of them and 300 m covers **97.4%**. **[RSF, new]**

**The A/B.** Same 18 scenarios, same seeds, same placement, varying
`ember_threat_radius_m` only. **Both control arms reproduced their committed
grid stats exactly** (`rsf_c10_scatter` and `rsf_c12_scatter`) — determinism
confirmed before reading any delta:

| Crews | Radius | Belt e1000 | Coated (of 1,800) | Viol | Ovt | Min margin |
|---|---|---|---|---|---|---|
| 10 | 200 m (committed) | 332/478 = 69.46% | 733 | 0 | 0 | 0.37 h |
| 10 | **300 m** | **342/478 = 71.55%** | **771** | 0 | 0 | 0.26 h |
| 10 | 400 m | 343/478 = 71.76% | 802 | 0 | 0 | 0.26 h |
| 12 | 200 m (committed) | 335/478 = 70.08% | 739 | 0 | 0 | 0.37 h |
| 12 | **300 m** | **343/478 = 71.76%** | **776** | 0 | 0 | 0.26 h |

**200 → 300 m buys +10 belt homes (+2.09 pp) at 10 crews and +8 (+1.67 pp) at
12, with +37/+38 coated homes and zero safety cost.** By wind regime at 10
crews: 18 mph 78.0 → 80.8%, 28 mph 67.6 → 68.4%, 40 mph 61.2 → 63.8%.
**[RSF, new]** With placement held fixed the applicable noise floor is ±1 belt
home / ±6 coated homes — this is comfortably outside it, and it replicates at
a second crew count.

**300 m is the operating point, not 400.** Going to 400 m adds **+1 belt home
— inside the noise floor — while coating 31 more homes overall.** Those extra
coats land on homes that were never near the burn: the radius has started
manufacturing jobs rather than finding real exposure. That is the documented
ember-radius dilution failure (`df60648`: radius 1000 costs −6.2 pp on the
screen suite). Stop at 300, which is also where the measured geometry says to
stop (p99 = 325 m).

**The cost is real and stated:** the worst crew margin tightens from 0.37 h to
0.26 h (22 → 16 min). More homes threatened means crews working closer to the
line. No violation or overrun tick appeared, but this is a conservative-
direction knob with a safety-adjacent price, and it should be adopted as a
**config recommendation pending founder sign-off**, not slipped in.

**Recommendation:** run RSF with `ember_threat_radius_m = 300`, and — far more
important — **the live engine's default of 0.0 is not survivable at this
location.** At 0, roughly two-thirds of RSF clients are invisible to the alert
ladder entirely. See §6.2.

---

## 5. The 100-home protocol

### 5.1 Onboarding

- **Enroll for dispersion, and say why.** RSF's measured advantage is that
  its estates are spread across a perforated fuelscape; that is what produces
  a 50–89% per-scenario band instead of a 0%-or-100% lottery (§1). When sales
  adds a tight pocket of adjacent parcels, it is importing the cluster
  failure mode. Run `tools/onboard.py` neighborhood-by-neighborhood before
  accepting concentrated enrollment, and read its **fleet-impact** output —
  nonzero means the existing roster loses home-scenarios if the candidates
  join.
- **Per-parcel data the engine needs** (unchanged from the HOA packet):
  verified structure lat/lon, gate codes and driveway clearance for the rig,
  turn-around, spigots, propane/LPG and solar/battery cutoffs, animals,
  contact ladder with an evacuation-needs flag, and a **coating consent that
  states the coating is opaque, is applied only under live threat, and comes
  with a post-event wash-down**.
- **Defensible space is the single per-home lever** — the model treats a
  cleared parcel as un-burnable by surface fire, i.e. ember-only exposure.
  Annual pre-season inspection; pass required for the "protected" tier.
- **Two verified safe zones minimum, on opposite sides.** Fail-closed law.
  The three RSF anchors are planning-grade until someone drives them (§6.5).
- **Note the 90 m honesty limit at intake.** A parcel classified on a 90 m
  raster carries ±45 m of positional error — *"the single largest error term,
  bigger than any physics uncertainty."* Two-thirds of RSF clients land on
  cells the model calls non-burnable (§4.5); that is a modeling artifact of
  cell size, not a statement that the home is safe.

### 5.2 Coating-hold policy — what the client is told

> *While the fire is projected more than 24 hours away, we do not coat. Crews
> go on alert and move to staging. The system re-checks every 15 minutes that
> waiting still lets us reach every home on the list — with a six-hour
> allowance for the weather turning worse. The moment that check fails, coating
> begins immediately and does not stop.*

Three things clients must understand, because each has bitten before:

1. **Not coating is not inaction.** It is a re-verified decision, made every
   cycle, that keeps the whole roster coverable.
2. **The coating is opaque and it is not seasonal.** Owners will not accept
   preventive application, so the WATCH rung is the earliest viable coat. There
   is no pre-coat product.
3. **Re-coat only if rain has hit the coating, or if application-to-projected-
   arrival would exceed the 72 h window.** Age alone inside 72 h is not a
   trigger — the dried film is the protection. ⚠ The 72 h figure is
   **[PENDING]** and conflicts 12× with a ~6 h model for evaporative gels
   (§6.1). If the supplier comes back short, this entire policy inverts.

### 5.3 Communication ladder

The engine emits these; every message names its incident:

| Rung | Client-facing text (shipped wording) |
|---|---|
| **NOTICE_72H** | *"Projected fire arrival at [property]: ~X h (median), Y h (pessimistic) — [incident]. Advance notice — crews being scheduled."* |
| **WATCH** | *"Monitoring [property] for [incident]: projected fire arrival ~X h (median), Y h (pessimistic). Under continuous watch; crews scheduled as needed."* |
| **EXTREME_24H** | *"EXTREME alert for [property] — [incident]: projected fire arrival … Crew dispatch underway — ensure property access now."* (If the median has already passed: *"fire projected AT the property NOW — do not approach; crews withdrawing."*) |
| **IMPACTED** | *"Projected fire arrival time has passed at [property]. Crews are clear; post-impact assessment to follow."* |
| **CLEARED** | *"All clear for [property]: no projected fire arrival within the 72 h alert window."* Suppressed while the same client holds any other active incident — a false all-clear during incident-ID churn is worse than silence. |

**Under the armed extreme-spread regime every message carries the banner:**
*"[EXTREME SPREAD REGIME — cured-grass wind conditions; the fire may move much
faster than the median projection; this alert is based on the pessimistic
(earliest credible) arrival.]"* Because §2.3 makes this **permanent at RSF
during wind events**, the onboarding packet must explain it once, properly, or
it will be read as boilerplate by the third Santa Ana.

### 5.4 Crew-safety laws the client sees

These are coded laws (SEMANTICS 2/3/7/10), not operator-overridable downward.
An operator may HOLD a dispatch; never extend a deadline.

1. **The 30-minute rule.** Crews are off the property ≥ 30 minutes before the
   *earliest credible* (P10) arrival, not the median. Every stop carries a
   latest-depart time: **leave by it, finished or not.**
2. **15-minute slack floor.** A stop with under 15 minutes of planned slack is
   never dispatched. If reality eats the slack, call it in — the next tick
   replans. Do not improvise.
3. **Corridors both ways.** Route in *and* route to the egress zone are checked
   against the eroded pessimistic fire surface every cycle. **"Withdraw
   mid-coat" means withdraw mid-coat** — the home is recorded `withdrawn`, gel
   unfinished, because the engine already determined the alternative is worse.
4. **Relocation orders (SEM-10).** An idle or staging position that becomes
   unsafe generates a relocate order. Comply immediately, acknowledge.
5. **ACK discipline.** Un-ACKed dispatches escalate at 10 minutes and the crew
   is dropped from the plan as unavailable.
6. **Beacon on at shift start.** A stale beacon bars dispatch by design — the
   engine will not send a crew it cannot see.

**What a client should be told this buys:** across all 90 RSF runs the safety
audit recorded **zero violations and zero overrun ticks**, with the tightest
observed margin at 22 minutes. **What it costs them:** 24.3% of ember-belt
homes end `abandoned_unsavable` — the fire beat every safe round trip. The
engine will not trade a crew for a house, and the client agreement should say
so in those words.

### 5.5 What RSF residents should expect during a Santa Ana

1. **The banner is normal here.** Any wind event on this fuelscape arms the
   pessimistic posture. Alerts will look more urgent than the median forecast
   justifies. That is deliberate.
2. **NOTICE / WATCH:** confirm gate codes and access, clear the driveway,
   verify defensible space, charge phones, move vehicles out or nose-out, open
   gates, review the evacuation route **coastward** — I-5 / Via de la Valle,
   Encinitas Blvd, or Del Mar Heights. Every modeled approach comes from the
   NNE-through-E arc; every safe anchor is to the SW/WNW.
3. **You will probably see crews staging before you see them coating.** That is
   the hold working.
4. **EXTREME_24H: evacuate when told. Do not wait to watch the coating.** Leave
   gates open. The coating decision is not yours to wait for and the crew's
   departure deadline is not negotiable.
5. **A no-notice ignition close by is the one case the service cannot cover.**
   Measured on this ground: with under 2 hours between detection and arrival,
   **0% of homes get coated** — travel plus application plus the 30-minute
   egress law simply does not fit (§3.4). In that scenario the product is the
   alert and the evacuation, not the coating. Leave.
6. **Expect losses, and expect them explained.** Roughly one in four
   ember-belt homes cannot be reached in time in the reactive regime — not
   because they were deprioritized (the engine deliberately skipped **zero**
   homes across all 90 RSF runs) but because the fire beat every safe round
   trip. The post-event report states which homes were coated, when, what gel
   was used, and what road blockages crews observed.
7. **A saved home comes back opaque.** Post-event wash-down is part of the
   service.

---

## 6. Open items

Ranked by what blocks the most.

### 6.1 Gel supplier data — the only blocker-grade item **[PENDING]**

Everything downstream of these numbers is ungradeable: plan freshness math,
the 24-hour coating hold, crew sizing, inventory, and any P(save) quoted to a
homeowner or an insurer.

| Needed | Status |
|---|---|
| Effective life vs temp / RH / wind | `gel_window_h = 72 h` vs the wgdefense **~6 h at 25 °C** coating-life model — a **12× conflict** in the parameter that decides the entire dispatch posture. 72 h means calm daylight deployment days ahead; 6 h means deploying inside the final hours, exactly when the safety rules bite hardest. |
| Wet coverage rate (gal/home at 1/8" dry film) | Gates the **10 min/home** capability — the cheapest measured lever in the engine — *and* all inventory sizing. |
| Drying time to rated film | Unknown. |
| Rain wash-off / weathering horizon | Unknown; the re-coat rule depends on it. |
| Any measured save-rate basis | `gel_effectiveness = 0.85` is documented as **invented**. A Class A flame-spread rating is not a survival probability. |

**Closes with:** a formulator data sheet or test report. No amount of
engineering closes this one.

### 6.2 Deploy-10 verification and the live `ember_threat_radius_m` default

Two config facts that would change RSF numbers materially:

- **`deploy_min = 10` is unverified capability.** Every RSF figure in this
  plan assumes it. Field-drill it (§3.2); if it measures 15 or 18, re-run the
  grid before quoting §4.
- **⚠ The live engine ships `ember_threat_radius_m = 0.0`.** At RSF that
  leaves ~63 of 100 clients with no projected arrival, no ladder state, and no
  dispatch job — invisible. The simulator's 200 m covers 80.2% of them; the
  measured 300 m (§4.5) covers 97.4% and adds +10 belt / +38 coated homes at
  zero measured safety cost. **This is the single highest-value config change
  identified for RSF.** It needs founder sign-off (min crew margin tightens
  0.37 → 0.26 h) and it needs the same A/B on a second map before it becomes
  a global default rather than an RSF override.

### 6.3 The `+30` asymmetry trace

The due-east approach threatens 250 homes where the NNE approach threatens
384, and scores 10.3 pp worse on the belt. The mechanism has not been
decomposed. Specifically unknown: whether the belt-denominator collapse
(116 vs 198) is real corridor geometry or an artifact of where the WUI band
sits relative to a due-west fire run, and whether the elevated
`abandoned_unsavable` share is arrival-clock or corridor-gate driven. The
per-home records needed for this already exist in the grid JSONs.

### 6.4 No extended tile — the alerted regime cannot be measured at RSF

Every other location in the catalog has an `_ext` tile; **RSF does not**
(`data/tiles/`). Consequences:

- Ignitions beyond ~9 km from center are **off-tile**. The grid's 6/8 km
  ignitions represent a fire already inside the corridor — the historical
  Witch Creek run started ~35 km ENE.
- The alerted-regime benchmark (`bench_alerted.py`, 20 km ignition, 60 h)
  **cannot be run here**. The pooled 79.5% figure in §3.4 stays a cross-map
  analog until the tile exists. (The *per-home lead-time curve* in §3.4 **is**
  RSF-measured — it reads ground-truth arrival off the standard tile and
  needs no extended ignition — but it is a per-home relationship, not a
  system-level alerted coverage number.)
- **Action:** `tools/fetch_sim_tiles.py --extended` for `rancho_santa_fe_ca`
  (plus the road-graph refetch it implies), then run the alerted benchmark and
  the long-lead hold validation on RSF's own ground. This is the cheapest way
  to convert the plan's last remaining analog into a measurement.
- **Second action, same tile:** the RSF lead-time curve is truncated above
  ~24 h by the 15 h simulated horizon interacting with the 24 h coating hold.
  A 60 h extended run resolves the long-lead bands the hold is actually
  designed for.

### 6.5 Field verification of the three staging anchors

The zones are planning-grade highway/arterial nodes, explicitly *"not
field-verified staging areas."* The engine is fail-closed on zones, so this is
an onboarding gate. Verify for each: rig access and turn-around, water,
comms coverage, defensibility of the node itself, and whether it stays
reachable when I-5 and El Camino Real are carrying an evacuation. Two zones on
opposite sides are the minimum; RSF's three are well-placed *on the map*.

### 6.6 Smoke, visibility, and the limits of any route verdict

Not modeled anywhere in the engine. `transit_hazard_determination.md` states
it plainly: smoke is *"historically the actual killer on evacuation roads,"*
so a PASSABLE verdict is **thermal only**. Also absent or deliberately
un-credited: cab shielding, spotting cutting a road ahead of a moving vehicle,
and civilian traffic jams (priced as time, never as stop-probability). Road
**width is not modeled at all** — `RoadEdge` carries no width or lane count,
and speed is a pure function of OSM highway class.

**No PASSABLE verdict may ever be presented to a crew as a route-safety
guarantee.** Stage-2 transit hazard is specified with physics, thresholds and
a pre-registered falsifiable prediction, and **none of its four validation
steps has been run**.

### 6.7 Standing model limits that ride on every number above

- **The engine is late in grass.** Tubbs GR2 anchor 0.46×; OOS late-miss GS
  64.2% / GR 52.2%. RSF is 79.6% GR+GS. The grass ROS floor and the 0.5 gust
  blend are the mitigations, both measured out-of-sample, neither a fix.
- **Blow-ups are not forecastable today.** Porcupine Ridge ran 22.5 km in
  12.9 h against a modeled 1.7 km median head — **13× under-forecast** —
  and neither more gust nor a wider ensemble touches it. The answer is
  posture (§2.3), not physics.
- **Detection is assumed instantaneous** in every figure here. Real feed
  latency comes straight off every lead time.
- **The forecast band is too narrow**: P10/truth measured 0.304–0.636 at 3 h
  leads, leaving 2 of 4 probe homes with *negative* service windows. Perfect
  information is worth +22.43 to +25.55 pp — but naively collapsing the
  ensemble produced 3–7 crew-safety violations. The band is a target for
  better forecasts, never a licence to narrow it.
- **`circuity = 1.35` is contradicted by the repo's own measurement of
  1.96–2.43.** It is compensated by `route_margin_min = 60`, invisibly.
  Nobody may tune that margin down before circuity is fixed.
  *[RESOLVED 2026-08-02: shipped as the calibrated pair (1.7, 56) — pooled
  median across all six tiles; the 1.96–2.43 figures were means. See
  `scratch_review/circuity_calibration.py` + the `contracts.py` circuity
  comment. Rancho Santa Fe itself measures LOW (median 1.55), so this
  plan's travel figures were conservative, not optimistic.]*
- **The suite is red at HEAD**: `test_locations_registry_shape` still pins
  five locations after RSF made six. Trivial, and it should be closed before
  anything ships from this branch.
- **The solver budget is wall-clock**, so any A/B smaller than ±1 belt home /
  ±6 coated homes is unresolvable. Both new results in this plan clear that
  floor; several older shipped conclusions do not.

### 6.8 Documentation drift found while sourcing this plan

One item, new, for the ledger in `calibration_review_2026-07-31.md` §6:
**`HOA_40_PLAYBOOK.md:23` publishes the Paradise alerted-regime figure as
84.6%; `scratch_review/bench_alerted.json` records 87.2%** (34 coated / 39
threatened). Santa Rosa (93.5%) and Boulder (100%) reproduce exactly; Park
City (82.5%) is measured but never published. Small, but the same class of
drift the review already catalogued six times.

---

## Appendix A — exact configuration behind every [RSF] number

| Parameter | Value |
|---|---|
| Harness | `scratch_review/rsf_grid.py` (commit `17dda1d`), machinery imported unmodified from `opt_ember.py` |
| Scenarios | 18 = intensity {moderate, high, extreme} × dist {6, 8 km} × wind_off {0, +30, −30}; seed `rsf\|<off>\|<dist>\|<inten>` |
| Clients / crews | 100 / {8, 10, 12} scattered; {10, 12} cluster8 |
| Horizon | 60 ticks × 15 min = **15 h** of simulated response |
| `deploy_min` | 10.0 min |
| `egress_buffer_min` | 30.0 min · `slack_min` 15.0 (default) |
| `dispatch_plan_on_roads` | True (real OSM graph) |
| `dispatch_hold_h` | 24.0 · `dispatch_hold_weather_buffer_h` 6.0 |
| `ember_threat_radius_m` | 200.0 (the §4.5 A/B varies this to 300) |
| `spotting_enabled` | True · `grass_ros_floor_frac` 0.10 |
| Corridor stack (SimSession defaults) | tiered erosion 550 / 200 / 150 m; escape hysteresis 20 min; `transit_cold_black_by_class` True; `road_spur_max_m` 8000 |
| `dispatch_objective` | `homes` (count primary) · `dispatch_late_shift` True |
| Fire zone | `standard` (±9 km) |
| Determinism | Control arms re-run today reproduced **both** `rsf_grid_rsf_c10_scatter.json` (332/478, coated 733, min margin 0.37) and `rsf_grid_rsf_c12_scatter.json` (335/478, coated 739) **exactly** |

**Reproducing the two new measurements.** Both were run today against the
committed harnesses with no edit to `wgresponse/`; neither wrote an artifact
into the repo.

```bash
# 4.5 ember-radius A/B — the committed grid harness already exposes the knob
PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c10_e300 \
    --crews 10 --placement scattered --ember-radius 300
PYTHONHASHSEED=0 py -3 scratch_review/rsf_grid.py --tag rsf_c12_e300 \
    --crews 12 --placement scattered --ember-radius 300
# controls are the committed --ember-radius 200 runs (rsf_c{10,12}_scatter)

# 3.4 lead-time curve — probe_leadtime.run_one is reusable as-is; its main()
# hardcodes 40 clients and the 5-map suite, so drive it with rsf_grid's
# scenarios() and n_clients=100, cfg = BASE + {deploy_min 10,
# dispatch_plan_on_roads, dispatch_hold_h 24, egress_buffer_min 30,
# ember_threat_radius_m 200}, then bucket rows[].homes[].arr_h.

# NB-cell census — SimSession client draw is seeded on (map, n, seed) only,
# so the placement is identical to the grid's; look up each client's cell in
# data/tiles/rancho_santa_fe_ca.wgt and test fuels.is_burnable.
```

**Live-engine divergence to remember:** `ResponseConfig` defaults keep the
tiered corridor stack, cold-black-by-class, escape hysteresis, grass ROS
floor, spotting, plan-on-roads, the coating hold and the ember threat radius
all **OFF**. The calibrated stack measured here is **not what the live watcher
runs today** — that is a deliberate conservative posture, and it means the
live configuration for RSF must be set explicitly, not inherited.

## Appendix B — artifacts cited

| Artifact | Carries |
|---|---|
| `scratch_review/rsf_grid_rsf_c{8,10,12}_scatter.json` | the headline RSF grid (scattered) |
| `scratch_review/rsf_grid_rsf_c{10,12}_cluster.json` | the cluster8 contrast |
| `data/tiles/rancho_santa_fe_ca.wgt` + `provenance.json` | fuel / slope / elevation, burnable & developed fractions |
| `data/roads/rancho_santa_fe_ca.wgr` + `provenance.json` | 29,025 nodes / 35,386 edges / LCC 98.71% |
| `wgresponse/simui.py` LOCATIONS (`0c5e441`) | center, hazard bearing 060°, three safe zones |
| `scratch_review/census_axis_sensitivity.json` | the 5-map wind-speed curve and axis effects |
| `scratch_review/census_wind_{low,moderate}_c{4,8}.json` | the moderate-wind violation finding |
| `scratch_review/bench_alerted.json` | alerted-regime 136/171 = 79.5% pooled |
| `scratch_review/bench_dens_*.json` | the density series (4 crews, 10→120 clients) |
| `scratch_review/probe_leadtime.py` + `probe_leadtime_lead_c8d10.json` | the lead-time probe, and its 5-map result; the RSF curve in §3.4 is this probe re-driven on the RSF matrix |
| `scratch_review/extreme_signature.json` | grass-class fraction of all 22 scored hindcast windows |
| `docs/review/eta_gap_report.md` | the Malibu 23–46% non-burnable-cell census this plan re-measures for RSF |
| `scratch_review/opt_ember_s_c{4,8,10}.json` | near-staging, incl. the crew staged into burn |
| `docs/calibration_review_2026-07-31.md` | the grade ledger every caveat here is drawn from |
| `docs/calibration/hindcast_oos_2026-07-28.md` | out-of-sample coverage, per-fuel late-miss, extreme-regime signature |
| `docs/transit_hazard_determination.md` | cold-black windows, passability, the smoke limit |
| `docs/HOA_40_PLAYBOOK.md`, `docs/DISPATCH_OPTIMIZATION.md` | the 40-home protocol this plan scales from |

---

## Addendum (2026-08-02): city-limits placement — numbers improve

Founder directive: the 100 homes must sit **within RSF city limits**. A
planning-grade boundary polygon (92067 core: Covenant / village / RSF Farms
/ Fairbanks edge) now constrains every RSF client draw engine-wide (commit
822dec2; verified 1,800/1,800 seeded homes inside across the rerun). Mean
home spacing tightens from 877 m to **356 m (2.46x denser)** — and every
scattered number improves:

| Crews | Whole-tile (superseded) | **City-limits (plan of record)** |
|---|---|---|
| 8  | 69.7% | **74.2%** |
| 10 | 69.5% | **76.8%** |
| 12 | 70.1% | **78.0%** |

All 90 city-limits runs: **0 violations, 0 overruns**. Winner (12 crews)
splits: moderate **87.3%** / high **84.2%** / extreme **62.0%**; the
due-east (+30 deg) approach remains the hard sector (63.9% vs 81-88%);
ignition distance is nearly irrelevant at this density (77.1 vs 78.9%).

**Crew-scaling correction:** at city density the response is no longer
purely clock-bound — 8->12 crews buys **+3.8 pp** (was +0.4 flat on the
whole tile). The 10-crew recommendation STANDS (+2.6 pp over 8 crews;
the 12th and 11th crews add a further +1.2 pp — a defensible surge tier,
no longer a pure contingency).

Caveats: the cluster-placement comparison rows do NOT respect the polygon
(the cluster generator bypasses the bounded draw — follow-up chip filed);
they are retained only as whole-tile artifacts. The rerun also bundled the
SEM-10e proactive-escape sim default landed by the parallel fix session —
isolated at 0.2 pp (noise), so the deltas above are density-driven.
