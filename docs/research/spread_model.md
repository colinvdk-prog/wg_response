# WG Response — Fire-Spread Model Selection for Operational Arrival-Time Forecasting (0–96 h)

**Research digest — 2026-07-06.** Purpose: implementable spec for the "time until fire reaches
client property X" engine. Recommendation up front, full equations after, sanity anchors and
open-source coefficient sources at the end. Anything not independently verified is flagged inline
and in §9.

---

## 0. Recommendation (TL;DR)

**Engine = Rothermel (1972, Albini-1976 adjusted) directional rate-of-spread on a raster, turned
into an arrival-time field with 16-neighbor time-dependent Dijkstra.** This is exactly the
architecture of FlamMap-MTT (Finney 2002) and is the standard cheap approach for "when does the
fire get to point X":

1. Per 30 m LANDFIRE cell: FBFM40 fuel model + slope/aspect (LANDFIRE topo) + forecast
   wind/RH/temp (HRRR/NBM) → head-fire ROS `R_h`, ellipse eccentricity `e` from Anderson (1983)
   L/B, giving directional ROS `R(θ)`.
2. Edges to 16 neighbors, edge cost = edge length / directional ROS evaluated at the tentative
   arrival time (time-dependent Dijkstra so the 0–96 h wind forecast is honored).
3. Output: arrival-time raster `t_arr(x,y)` from the live fire perimeter (NIFC/FIRIS/GOES
   ingest — separate task). Alert triggers: `t_arr(property) ≤ 72 h` → advance notice;
   `≤ 24 h` → EXTREME; crew-clear deadline = `t_arr − 30 min` with a safety factor (§6.5).
4. Spotting: default-OFF flag using the ELMFIRE/Sardoy lognormal parameterization (§4).

Do **not** start with Huygens front propagation (FARSITE-style) — more accurate perimeter shapes
but 10–100× the code complexity (front reconstruction, loop merging) for little gain in
point-arrival-time skill at 0–96 h, where weather forecast error dominates. Dijkstra/MTT gives the
same first-order answer and is embarrassingly re-runnable every weather update.

---

## 1. Rothermel (1972) surface rate of spread — full equation set

Reference of record: **Andrews, P.L. 2018. "The Rothermel surface fire spread model and associated
developments: A comprehensive explanation." USDA Forest Service RMRS-GTR-371**
(https://www.fs.usda.gov/research/treesearch/55928). All equations below are the Albini (1976)
adjusted forms that BehavePlus/FARSITE/FlamMap/ELMFIRE use. **English units throughout** (the
coefficients are unit-bound): σ in ft²/ft³, w in lb/ft², ρ in lb/ft³, Q in Btu/lb, I_R in
Btu/ft²/min, R and U in ft/min. Convert at the end: `m/min = ft/min × 0.3048`;
`km/h = ft/min × 0.018288`; `mph = ft/min / 88`.

### 1.1 Core equation

```
R = I_R · ξ · (1 + φ_w + φ_s) / (ρ_b · ε · Q_ig)          [ft/min]
```

- `I_R`  reaction intensity (Btu/ft²/min)
- `ξ`    propagating flux ratio (–)
- `φ_w`  wind factor (–), `φ_s` slope factor (–)
- `ρ_b`  fuel-bed bulk density = w_o_total / δ (lb/ft³), δ = fuel bed depth (ft)
- `ε`    effective heating number = `exp(−138/σ)` (–)
- `Q_ig` heat of preignition = `250 + 1116·M_f` (Btu/lb), M_f = moisture fraction (lb water/lb fuel)

### 1.2 Reaction intensity

```
I_R = Γ' · w_n · h · η_M · η_s

Γ'      = Γ'_max · (β/β_op)^A · exp[A·(1 − β/β_op)]        [min⁻¹]  optimum reaction velocity
Γ'_max  = σ^1.5 / (495 + 0.0594·σ^1.5)                     [min⁻¹]
A       = 133 · σ^(−0.7913)                                (Albini 1976; replaces Rothermel's
                                                            A = 1/(4.774σ^0.1 − 7.27))
β       = ρ_b / ρ_p            packing ratio; particle density ρ_p = 32 lb/ft³
β_op    = 3.348 · σ^(−0.8189)  optimum packing ratio
w_n     = w_o · (1 − S_T)      net fuel load; total mineral content S_T = 0.0555
h       = low heat content, 8000 Btu/lb for all standard fuel models (18 608 kJ/kg)
η_s     = 0.174 · S_e^(−0.19)  mineral damping; effective mineral content S_e = 0.010 → η_s ≈ 0.4174
η_M     = 1 − 2.59·r_M + 5.11·r_M² − 3.52·r_M³   moisture damping, r_M = min(M_f / M_x, 1)
M_x     = dead moisture of extinction (fuel-model parameter, 0.12–0.40)
```

### 1.3 Propagating flux ratio

```
ξ = exp[(0.792 + 0.681·σ^0.5) · (β + 0.1)] / (192 + 0.2595·σ)
```

### 1.4 Wind factor φ_w

```
φ_w = C · U^B · (β/β_op)^(−E)

C = 7.47  · exp(−0.133 · σ^0.55)
B = 0.02526 · σ^0.54
E = 0.715 · exp(−3.59×10⁻⁴ · σ)
```

- `U` = **midflame** wind speed in ft/min. Convert from forecast 10-m/20-ft wind via a wind
  adjustment factor (WAF): `U_midflame = WAF × U_20ft` (`U_20ft ≈ U_10m / 1.15`). Unsheltered WAF
  (Albini & Baughman 1979): `WAF = 1.83 / ln[(20 + 0.36·δ) / (0.13·δ)]` with δ = fuel depth (ft)
  → ≈0.36 for 1-ft grass, ≈0.44–0.55 for 6-ft shrub; use 0.1–0.2 under timber canopy.
- **Wind limit:** Rothermel capped `U ≤ 0.9·I_R` (U in ft/min, I_R in Btu/ft²/min). Andrews,
  Cruz & Rothermel (2013) recommend *removing* the cap (it wrongly throttles wind-driven grass
  fires). For a 72-h alerting product, run **uncapped** (conservative-fast); expose a flag.

### 1.5 Slope factor φ_s

```
φ_s = 5.275 · β^(−0.3) · (tan φ_slope)²        φ_slope = slope angle
```

Upslope only; combine with wind vectorially (§3.2). Note tan² — a 30° slope (tan = 0.58) with
β ≈ 0.01 gives φ_s ≈ 7, comparable to a stiff wind.

### 1.6 Multi-size-class weighting (Albini 1976) — needed for FBFM40

Standard fuel models carry up to 5 particle classes: dead 1-h/10-h/100-h + live herbaceous +
live woody, each with its own `w_o` and `σ`. Weighting (full detail in RMRS-GTR-371 §"fuel bed
description"; crib code rather than re-deriving — see §7):

```
A_ij   = σ_ij · w_o_ij / ρ_p                      surface area, class j of category i (dead/live)
f_ij   = A_ij / Σ_j A_ij                          within-category weights
f_i    = A_i / (A_dead + A_live)                  category weights
σ_char = Σ_i f_i · Σ_j f_ij · σ_ij                characteristic SAV for Γ', ξ, φ_w, ε
```

Net loads for I_R use Albini's `g_ij` size-class-group weights (not `f_ij`) — this is the #1
implementation bug in home-rolled Rothermels; copy from BehavePlus source (§7.2).

**Live fuel moisture of extinction** (dynamic, per Albini):

```
W        = Σ_dead w_o·exp(−138/σ) / Σ_live w_o·exp(−500/σ)
M'_f,dead= Σ_dead w_o·M_f·exp(−138/σ) / Σ_dead w_o·exp(−138/σ)
M_x,live = max[ 2.9·W·(1 − M'_f,dead/M_x,dead) − 0.226 ,  M_x,dead ]
```

**Dynamic fuel models (FBFM40 GR/GS/SH):** live herbaceous load transfers to dead as a function
of herb moisture — fully cured at ≤30%, fully green at ≥120%, linear between:
`fraction_cured = clamp((1.20 − M_herb)/0.90, 0, 1)`; move that fraction of herb load into a new
dead class at the herb SAV. This curing transfer is why grass models explode in autumn.

### 1.7 Dead fuel moisture from RH/temp (Fosberg/NFDRS simple method)

Equilibrium moisture content, Simard (1968) piecewise (T in °F, H in %RH, EMC in %) — verified
against WSL Wikifire (https://wikifire.wsl.ch/tiki-indexf2f0.html?page=Equilibrium+moisture+content):

```
H < 10:        EMC = 0.03229 + 0.281073·H − 0.000578·H·T
10 ≤ H < 50:   EMC = 2.22749 + 0.160107·H − 0.01478·T
H ≥ 50:        EMC = 21.0606 + 0.005565·H² − 0.00035·H·T − 0.483199·H
```

Fosberg/NFDRS shortcut (Fosberg & Deeming 1971; Rothermel 1983 field tables):

- **1-h fuel moisture:** `M_1h = 1.03 × EMC` computed with *fuel-level* T/RH. Correct
  instrument-shelter readings for insolation before applying: sky cover 0–10% → T +25 °F,
  RH ×0.75; 10–50% → +19 °F, ×0.83; 60–90% → +12 °F, ×0.91; 90–100% → +5 °F, ×1.00
  (Wikifire 1-h timelag page). At night use +5 °F/×1.0.
- **10-h:** `M_10h ≈ 1.28 × EMC`;  **100-h:** `M_100h ≈ 1.43 × EMC` (or lag toward a 24-h
  boundary EMC; for a 0–96 h forecast the 1.28/1.43 multipliers are fine — 1-h class dominates ROS).
- Floor at 1–2% in extreme conditions; typical alert-worthy values: 1-h FM 2–6%.

### 1.8 Live fuel moisture defaults by season (inputs, % dry weight)

Standard moisture-scenario defaults (Scott & Burgan 2005 D/L scenarios; NWCG PMS-437):

| Season / condition            | Live herbaceous | Live woody |
|-------------------------------|-----------------|------------|
| Spring green-up               | 120–150         | 120–150    |
| Early summer                  | 90–120          | 90–120     |
| Late summer (partly cured)    | 60–90           | 75–90      |
| Fire season / cured (default) | **30–60**       | **60–90**  |
| Extreme (Santa Ana, drought)  | 30              | 60         |

Operational upgrade: pull real live fuel moisture from the National Fuel Moisture Database
(WFAS, https://www.wfas.net) nearest chamise/sagebrush site; chaparral "critical" threshold
≈ 60% LFM (SoCal predictive services convention). For WG's CA client base default the engine to
**herb 30 / woody 60** June–November unless NFMD says otherwise (fail-dangerous-fast is the
correct polarity for an alerting product).

---

## 2. Fuel model parameter set

Use **Scott & Burgan FBFM40** (RMRS-GTR-153, 2005 — https://www.frames.gov/catalog/792), which is
what LANDFIRE ships wall-to-wall for CONUS at 30 m
(`LF2022/2023 FBFM40`, https://landfire.gov/fuel/fbfm40). Anderson-13 is the legacy fallback.
Per model you need exactly 13 numbers (+ static/dynamic flag):

```
{fuel_bed_depth δ (ft), M_x_dead (%), heat h (Btu/lb, =8000),
 w_o: 1h, 10h, 100h, live_herb, live_woody      (lb/ft²; tons/ac × 0.0459137 = lb/ft²)
 σ:   1h, 10h(=109), 100h(=30), live_herb, live_woody   (ft²/ft³)}
```

Spot-check rows (verified against pyretechnics `fuel_models.py`, loads in lb/ft²):

| Model | δ (ft) | M_x | 1-h load | 10-h | 100-h | herb | woody | σ_1h | σ_herb | σ_woody |
|-------|--------|-----|----------|------|-------|------|-------|------|--------|---------|
| GR2 (102, dynamic grass) | 1.0 | 15% | 0.0046 | 0 | 0 | 0.0459 | 0 | ~2000 | 1800 | – |
| SH5 (145, high-load shrub ≈ chaparral) | 6.0 | 15% | 0.1653 | 0.0964 | 0 | 0 | 0.1331 | 750 | – | 1600 |
| TL3 (183, timber litter) | 0.3 | 20% | 0.0230 | 0.1010 | 0.1286 | 0 | 0 | 2000 | – | – |

(In tons/acre those are GR2: 0.10/–/–/1.0; SH5: 3.6/2.1/–/2.9 woody; TL3: 0.5/2.2/2.8 — matches
RMRS-GTR-153.) **Do not hand-type the 40-row table — import it from one of the sources in §7 and
checksum it.** Also honor the non-burnable models NB1/91–NB9/99 (urban, water, bare) → ROS 0,
infinite traversal cost.

---

## 3. Growth geometry: from head-fire ROS to an arrival-time field

### 3.1 Options considered

| Approach | Used by | Verdict for WG Response |
|----------|---------|-------------------------|
| Huygens elliptical wavefront (Richards 1990 ODEs) | FARSITE, Prometheus | Most faithful perimeters; hardest to implement (front topology). Skip for v1. |
| Eulerian level set | ELMFIRE, pyretechnics | Clean math, moderate complexity; good v2 target (crib pyretechnics `eulerian_level_set`). |
| **Minimum travel time / Dijkstra on rate grid** | FlamMap-MTT (Finney 2002, Can. J. For. Res. 32:1420–1424), WildfireAnalyst | **Standard cheap approach for arrival time at a point. Build this.** MTT ≡ shortest-path search where node burn time = length of fastest path from ignition (confirmed: https://cdnsciencepub.com/doi/abs/10.1139/x02-068). |

### 3.2 Directional ROS from the ellipse

Compute head ROS `R_h` with the **effective** wind+slope factor. Vector-combine wind and slope
(FARSITE/ELMFIRE convention — ELMFIRE tech ref splits φ into x/y components:
`φ_x = φ_w·sin θ_w + φ_s·sin θ_slope`, etc., https://elmfire.io/tech_ref.html), giving a combined
magnitude `φ_E` and a heading direction `θ_h`. Then the **effective windspeed** (needed for L/B) is
the wind that alone would give φ_E — invert §1.4:

```
U_eff = [ φ_E · (β/β_op)^E / C ]^(1/B)          [ft/min]
```

**Length/breadth ratio — Anderson (1983), as implemented in ELMFIRE/FARSITE**
(verified at https://elmfire.io/verification/verification_01.html):

```
L/B = min[ 0.936·exp(0.2566·U) + 0.461·exp(−0.1548·U) − 0.397 ,  8.0 ]
      U = effective midflame windspeed in mph;  L/B = 1 at U = 0
```

Eccentricity and directional spread (ignition at the rear focus):

```
e      = sqrt((L/B)² − 1) / (L/B)
R(θ)   = R_h · (1 − e) / (1 − e·cos θ)         θ = angle from heading direction
R_back = R(π)  = R_h · (1 − e)/(1 + e)          backing fraction
R_flank= (R_h + R_back) / (2 · L/B)             flanking (semi-minor axis rate)
```

Sanity (evaluate the formula above, not round numbers): the **uncapped** L/B is 4.2 at U ≈ 6.05
mph (e ≈ 0.9712), and the raw curve is already 7.03 at 8 mph and 11.9 at 10 mph. Two distinct
"flank" fractions of head appear above and must not be confused: the **rear-focus** R(90°) =
(1 − e) ≈ 2.9% (what the engine's `directional_ros_mps(θ=90°)` computes) versus the **semi-minor
axis** R_flank = (R_h + R_back)/(2·L/B) ≈ 12.1% (the wider "flanking" number). Backing R(π) =
(1 − e)/(1 + e) ≈ 1.5% of head. Because the raw curve exceeds 8 well before 10 mph, the
`min[…, 8.0]` cap **binds at U ≈ 8.49 mph** (raw = 8.016 there): above that, every ellipse shares
the same eccentricity e ≈ 0.99216, freezing the head-*relative* backing/flank fractions
(back ≈ 0.39%, rear-focus θ=90° ≈ 0.78%) regardless of whether the effective wind is 9 mph or
40 mph. This is the intended ELMFIRE/FARSITE convention — the cap keeps very-high-wind ellipses
from degenerating to a line — and it is **not** a freeze of the lateral arrival times themselves:
head ROS, and therefore the *absolute* flank/back ROS, keep rising with wind above the cap (the
head term grows while the fraction is fixed), so lateral-property ETAs still get earlier in stronger
wind. The head-relative fractions matter for shape; the absolute lateral rate matters for arrival
time. Earlier drafts of this note quoted "L/B ≈ 4.2 at 10 mph," which contradicted this section's
own formula (that curve gives 11.9 at 10 mph) — corrected here to match `ellipse_lb()` in
`wgresponse/rothermel.py`.

### 3.3 Dijkstra arrival-time construction

- **Nodes:** 30 m cells (resample to 60–90 m beyond ~30 km radius for speed; 96 h × 6 km/h ≈
  600 km worst-case domain is overkill — clip domain to 100 km around active fires).
- **Edges:** 16 neighbors = 8 standard + 8 knight moves (±1,±2)/(±2,±1), lengths
  `d, d√2, d√5` (d = cell size).
- **Edge cost:** `t_AB = dist / mean(R_A(θ_AB), R_B(θ_AB))` with θ_AB the edge bearing measured
  from each cell's local heading; evaluate weather at the tentative arrival time at A
  (time-dependent Dijkstra: bucket forecast into 1-h steps 0–36 h [HRRR], 3–6 h steps to 96 h
  [NBM/GFS]). Non-FIFO effects are negligible at these step sizes.
- **Sources:** all cells under the current fire perimeter, t = t_obs (perimeter age matters —
  see ingest task).
- **Angular discretization error (why 16 neighbors):** shortest grid path can miss the true
  straight line by up to half the angular spacing → travel-time overestimate factor
  `1/cos(Δθ/2)`: 8-neighbor (Δθ = 45°) → up to **+8.2%**; 16-neighbor (Δθ = 22.5°) → up to
  **+2.0%**. 16-neighbor is the accepted sweet spot (32-neighbor buys +0.5%). Note the bias
  direction: pure grid error makes predicted arrival *later* than truth — apply the §6.5 safety
  factor on top, never rely on the grid error canceling it.
- **Cost:** ~3M cells × 24 edges with a binary heap ≈ seconds in optimized Python/Rust per
  weather refresh. Re-run the whole field every forecast cycle (hourly); no incremental updates
  needed at v1.

---

## 4. Spotting / ember transport — `SPOTTING_ENABLED=false` flag

Deterministic Dijkstra cannot jump barriers; wind-driven ember spotting is why real arrival times
beat model arrival times in extreme events (Camp, Tubbs both spotted across canyons/rivers).
Ship a **default-OFF** flag with the ELMFIRE parameterization (Sardoy et al. 2008 lognormal;
verified at https://elmfire.io/user_guide/spotting.html):

```
mean spot distance      m = a · Q'^b · u20^c        [m]
variance                v = d · m
lognormal params        σ_ln² = ln(1 + v/m²);  μ_ln = ln(m) − σ_ln²/2
ELMFIRE defaults: a = 5.0 (range 5–10), b (SPOT_FLIN_EXP) = 0.3 (0.2–0.4),
                  c (SPOT_WS_EXP) = 0.7 (0.4–0.7), d = 250 (250–600)
Q' = fireline intensity kW/m (Byram: Q' = H·w_consumed·ROS), u20 = 20-ft wind
Trigger: 0.2–0.8% of burning cells per timestep (GLOBAL_SURFACE_FIRE_SPOTTING_PERCENT),
         ignition probability on landing PIGN default 100%.
```

Cheap deterministic integration for arrival time: give every burning cell a zero-cost teleport
edge to cells downwind within the **median** spot distance (or P90 for conservative alerts).
For the crew-safety deadline specifically, also compute **Albini's maximum spotting distance**
(Albini 1979/1983, RP-INT-309 — https://www.frames.gov/documents/behaveplus/publications/Albini_1983_INT-RP-309_ocr.pdf)
as a hazard radius: under strong winds max spot distance is routinely 1–3 km, extreme events > 5 km.
A property can be hit by embers ~30–60 min before the flame front arrival-time field says so —
fold this into the 30-min-clear rule (§6.5).

---

## 5. Sanity anchors for unit/regression tests

### 5.1 Rate-of-spread ballparks (head fire)

| Fuel / condition | ROS | Source/basis |
|---|---|---|
| Grass, wind-driven (the "10% rule") | head ROS ≈ **10% of 10-m open wind speed** — 30 km/h wind → ~3 km/h; verified as the best simple predictor by Cruz & Alexander 2020 (https://www.sciencedirect.com/science/article/abs/pii/S1364815220306964) | field-obs meta-analysis |
| Grass, typical fire-season range | 1–6 km/h; extreme Australian grassfires 10–20+ km/h | task prior, consistent with 10% rule |
| Chaparral (SH5/SH7), Santa Ana | sustained 1–5 km/h; Thomas Fire had periods of ~1 acre/second growth | Thomas Fire Wikipedia; 10%-of-wind rule at 30–50 km/h winds |
| Timber litter surface fire (TL models) | 0.01–0.5 km/h | BehavePlus TL outputs, "low" ROS class in RMRS-GTR-153 |
| Active crown fire runs, conifer | 1–3 km/h (Camp Fire front averaged ~1.5–3 km/h toward Paradise) | timeline-derived |

Unit tests: (a) GR2, midflame 5 mph, 1-h FM 6%, cured → ROS should land ~40–90 ft/min
(≈0.7–1.6 km/h); assert against BehavePlus/pyretechnics output for identical inputs, not a
hand-picked number. (b) φ_w monotone in U, ROS → R_0 as U,slope → 0. (c) η_M → 0 as M_f → M_x
(fire extinguishes). (d) NB models → ∞ traversal time. (e) L/B(0 mph) = 1; L/B capped at 8.

### 5.2 Historical growth-rate regression anchors (acres/hour)

| Fire | Anchor | Verified figure |
|---|---|---|
| **Camp Fire (Nov 2018, Butte Co. CA)** | 10,000 ac in ~90 min (**≈6,600 ac/h**; "a football field per second"); 20,000 ac in first <14 h (avg ≈1,400 ac/h) | CNN/PBS Frontline "by the numbers" |
| **Tubbs Fire (Oct 2017, Sonoma CA)** | front advanced **>12 miles in ~3 h ≈ 6.4 km/h** sustained through oak/grass/WUI; "a mile in 20 minutes" (~4.8 km/h) | CHDS timeline, Wikipedia |
| **East Troublesome (Oct 2020, CO)** | sustained **~6,000 ac/h** overnight run; +87k–140k ac in ~24 h (sources conflict — Wikipedia says ~87k, CPR/KRDO imply 30k→170k; treat 24-h total as 87–140k, use the 6,000 ac/h rate as the anchor) | wildfiretoday.com, CPR, KRDO |

Regression harness: seed the engine at each fire's ignition point with archived HRRR/RAWS weather
+ LANDFIRE fuels of that year; assert modeled 24-h burned area within a factor of ~3 of the
anchor and modeled arrival at a known landmark (e.g., Coffey Park for Tubbs) within ±50% of the
observed elapsed time. Factor-of-3 sounds loose but is honest for no-spotting Rothermel; tighten
with the spotting flag ON.

---

## 6. Implementation notes specific to WG Response

- **6.1 Grids:** LANDFIRE 2023 FBFM40 + slope + aspect + canopy cover (for WAF sheltering),
  30 m, single national download, refresh annually.
- **6.2 Weather:** HRRR (hourly, 0–48 h) then NBM/GFS to 96 h: 10-m wind U/V, 2-m T, 2-m RH →
  §1.7/§1.8 moistures per timestep. Cache per-cell static Rothermel intermediates (β, β_op, ξ,
  Γ'_max, C, B, E, ε per fuel model) — only moisture and wind terms vary per timestep.
- **6.3 Outputs per client property:** `t_arr` (h), heading-relative bearing, effective ROS at
  the property cell, and the alert tier (72 h notice / watch / 24 h EXTREME).
- **6.4 The 72-h alert is weather-limited, not model-limited.** Beyond ~48 h wind forecasts
  rotate; present arrival time as a band: run the Dijkstra field on {P10, P50, P90} wind/FM
  perturbations (±20% wind speed, ±30° direction, ±2 pts dead FM) — 3–9 runs, still seconds each.
- **6.5 Crew-safety margin:** the hard rule "clear ≥30 min before arrival" must key off the
  **P10 (earliest) arrival time minus 30 min**, and when winds ≥ 20 mph subtract an additional
  ember-hazard buffer (§4: embers precede the front). Never dispatch when P10 arrival − now <
  deploy time (30 min) + egress + 30-min clearance.
- **6.6 GraphGel deployment window:** gel effectiveness degrades after application (hours-scale
  for hydrogels unless re-wetted) — dispatch optimizer should target application inside a window
  [t_arr − X, t_arr − 30 min] where X = product-specific effective life; get X from the GraphGel
  spec sheet (not researched here — flag for the product task).

---

## 7. Open-source implementations to crib validated coefficients from

| Repo | What to take | Exact location | License |
|---|---|---|---|
| **pyregence/pyretechnics** (https://github.com/pyregence/pyretechnics) | Fuel model table (Anderson 13 + FBFM40, dict keyed by model number, 13-tuple exactly matching §2; loads in **lb/ft²**), Rothermel surface equations, Anderson L/B, ELMFIRE-style level set, Sardoy spotting — all in Python, ideal reference implementation | `src/pyretechnics/fuel_models.py` (table — fetched and verified), `src/pyretechnics/surface_fire.py`, `spot_fire.py`, `eulerian_level_set.py`; literate source `org/pyretechnics.org` | EPL-2.0 |
| **lautenberger/elmfire** (https://github.com/lautenberger/elmfire) | Operational-grade Fortran: spread rate, spotting defaults, L/B; fuel tables as CSV | `build/source/fuel_models.csv` (+ `fuel_models_nfdrs.csv`, `building_fuel_models.csv`), `build/source/elmfire_spread_rate.f90`, `elmfire_spotting.f90`, `elmfire_level_set.f90` (directory listing verified; CSV path = same dir, re-confirm on clone) | EPL-2.0 |
| **firelab/behave** (https://github.com/firelab/behave) | The authoritative USFS C++ (BehavePlus engine). The 53-model table (13+40) and the canonical Albini weighting incl. `g_ij` | `src/behave/fuelModels.cpp/.h` (table), `src/behave/surfaceFire.cpp`, `surfaceFireReactionIntensity.cpp`, `surfaceFuelbedIntermediates.cpp` (verified via repo listing) | public-domain-ish USFS (check LICENSE) |
| **EcoFire/firebehavioR** (https://github.com/EcoFire/firebehavioR) | R port of Rothermel + crown fire + WAF + fine-fuel-moisture helpers; handy for cross-checking outputs, `data(fuelModels)` table | R package, CRAN mirror github.com/cran/firebehavioR; `rothermel()` function | GPL (CRAN) |
| **firestarter-io/fuelmodels** (https://github.com/firestarter-io/fuelmodels) | TypeScript constants for Anderson 13 + FBFM40, sourced from Andrews RMRS-GTR-371 — useful if any WG dashboard needs client-side numbers | repo root JSON/TS constants | check repo |

**Cross-validation rule:** import the FBFM40 table from two independent sources (pyretechnics +
behave), diff them numerically, and pin the result as a fingerprinted CSV in wg_response (same
byte-identity discipline as the firerisk engine).

---

## 8. Primary references

- Rothermel, R.C. 1972. *A mathematical model for predicting fire spread in wildland fuels.* INT-115.
- Albini, F.A. 1976. *Estimating wildfire behavior and effects.* INT-30. (adjusted coefficients)
- Andrews, P.L. 2018. *RMRS-GTR-371* — single best implementation reference. https://www.fs.usda.gov/research/treesearch/55928
- Scott, J.H. & Burgan, R.E. 2005. *Standard fire behavior fuel models.* RMRS-GTR-153. https://www.frames.gov/catalog/792
- Anderson, H.E. 1983. *Predicting wind-driven wild land fire size and shape.* INT-305. https://www.frames.gov/catalog/27010
- Finney, M.A. 2002. *Fire growth using minimum travel time methods.* CJFR 32:1420–1424. https://cdnsciencepub.com/doi/abs/10.1139/x02-068
- Finney, M.A. 1998/2004. *FARSITE model development.* RMRS-RP-4. https://www.fs.usda.gov/rm/pubs/rmrs_rp004.pdf (elliptical dimensions, Huygens)
- Albini, F.A. 1983. *Potential spotting distance from wind-driven surface fires.* INT-RP-309.
- Sardoy et al. 2008 (lognormal firebrand ground distribution) via ELMFIRE docs https://elmfire.io/user_guide/spotting.html
- Fosberg & Deeming 1971; Simard 1968 EMC via https://wikifire.wsl.ch
- Cruz, M.G. & Alexander, M.E. 2020. *10% wind speed rule of thumb.* Env. Mod. & Software. https://www.sciencedirect.com/science/article/abs/pii/S1364815220306964
- Andrews, Cruz & Rothermel 2013. *Reexamination of the wind limit.* (uncap rationale)

---

## 9. Not verified / flags

1. **ELMFIRE `fuel_models.csv` path** — file confirmed present in the `build/source/` listing;
   exact contents/columns not opened. Re-confirm on clone.
2. **pyretechnics fuel-load units** — the table values are **lb/ft²** (GR2 1-h 0.0046 lb/ft² =
   0.10 t/ac); one summarizer mislabeled them tons/acre. Assert `GR2 1h ≈ 0.0046 lb/ft²` in the
   import test.
3. **East Troublesome 24-h growth** — sources conflict (87k vs ~140k acres/24 h); the ~6,000 ac/h
   peak-sustained rate is consistently reported — use that as the anchor.
4. **Camp Fire ignition→Paradise elapsed time** (~90 min) is widely reported but I anchored on the
   verified acreage rates instead.
5. **10-h/100-h EMC multipliers (1.28/1.43)** — standard NFDRS practice from memory; the 1-h
   `1.03×EMC` and insolation corrections were verified (Wikifire). Low impact (1-h dominates).
6. **WAF Albini–Baughman formula** — from standard references, not re-verified this session;
   cross-check against `firebehavioR::waf()` or behave's `windAdjustmentFactor`.
7. **Anderson-13 vs FBFM40 in LANDFIRE**: assumed FBFM40 everywhere for CONUS — true for LF2022+
   products (verified via landfire.gov listing), but confirm the specific raster WG downloads.
8. **GraphGel effective-life X (§6.6)** — product parameter, not researched here.
9. **behave license** — USFS code, likely public domain, but the repo LICENSE was not read.
