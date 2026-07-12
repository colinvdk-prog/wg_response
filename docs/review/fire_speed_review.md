# Fire-Spread-Rate Review — is the SIMULATOR spreading fast enough?

**Date:** 2026-07-10 · **Scope:** the SIMULATOR (a demonstration instrument),
not the live safety-scoring engine · **Method:** read-only diagnostics in
`scratch_review/diag_ros_*.py` (`py -3 scratch_review/diag_ros_{presets,tile,probe,floor}.py`
from repo root, `PYTHONPATH=.`). Product code untouched; suite unaffected.

**The founder's question** (watching Paradise, CA — a timber-litter + grass WUI
tile): *"is the fire spreading fast enough?"* **Answer: no — and the numbers say
so.** The demonstrated Paradise fire runs at **~0.22–0.34× the empirical grass
rate** at the "high" preset a demo would use. This report quantifies it per
fuel × intensity, root-causes it, and recommends one honest, well-labelled
simulator-only calibration knob that keeps the live-engine "no fudge" discipline
intact.

This narrows [F3](model_fidelity_review.md) and the
[hindcast](../calibration/hindcast_first_pass.md) (both already found grass ROS
~2–3× slow) to the specific question the simulator poses.

---

## The presets (as `simui.INTENSITY_WEATHER` → `MockWeather` builds them)

| preset | wind mph | **U10 m/s** | RH % | temp °C | grass 10%-rule anchor (km/h) |
|---|---|---|---|---|---|
| low | 8 | 3.58 | 35 | 28 | 1.29 |
| moderate | 18 | 8.05 | 22 | 31 | 2.90 |
| high | 28 | 12.52 | 12 | 35 | 4.51 |
| extreme | 40 | 17.88 | 6 | 39 | 6.44 |

Anchor = **Cruz & Alexander (2020) cured-grass rule**: head ROS ≈ 10% of the
10-m open wind. (`simui.MPH_TO_MPS = 0.44704`; `MockWeather` uses `wind_mph *
MPH_TO_MPS` as the 10-m sustained wind — `simui.py:1579`.)

---

## 1. Sim vs observed ROS (fuel × intensity, with ratios)

### 1a. Clean single-fuel physics — point / flat-uniform head ROS (km/h)

The single-cell Rothermel chain the sim uses on flat ground (`diag_ros_presets.py`;
a flat-uniform-tile `compute_arrival` reproduces these to 2 decimals, confirming
the Dijkstra realizes the physics — `diag_ros_tile.py`). Ratio = sim/anchor for
grass; `[%U10]` where no single-% rule applies.

| fuel (Paradise share) | low | moderate | high | extreme | anchor basis |
|---|---|---|---|---|---|
| **GR2** grass (17.9%) | 0.38 (**0.29×**) | 1.31 (**0.45×**) | 3.01 (**0.67×**) | 6.35 (**0.99×**) | 10%-rule |
| **GS2** grass-shrub (12.5%) | 0.29 (0.32×) | 0.95 (0.47×) | 1.99 (0.63×) | 3.76 (0.83×) | 7%-rule (GS) |
| **TL4** downed logs (9.9%) | 0.02 | 0.06 | 0.12 | 0.22 | [0.3%U10] near-static |
| **TL3** conifer litter (9.3%) | 0.01 | 0.04 | 0.08 | 0.15 | [0.2%U10] near-static |
| **TU5** timber-shrub (8.1%) | 0.11 | 0.28 | 0.51 | 0.85 | [1.3%U10] |
| SH5 high-load chaparral (Malibu) | 1.13 | 3.13 | 5.81 | 9.97 | [15.5%U10] — **fast** |
| SH2 mod-load chaparral (Malibu, 16.8%) | 0.08 | 0.25 | 0.48 | 0.83 | [1.3%U10] near-static |

**Reading it:**
- **Grass** is 0.29–0.67× the 10%-rule at low/moderate/high, converging to
  **0.99× only at extreme**. The slow bias is *worst at low wind and vanishes at
  the top* — a wind-dependent deficit, not a flat offset (see root cause).
- **Timber-litter (TL3/TL4 ≈ 19% of Paradise) is effectively static** (0.01–0.22
  km/h even at extreme) — it acts as a near-barrier the head must crawl across.
- **TU5 (8%)** is 0.1–0.85 km/h. So **~27% of Paradise is slow timber**, plus
  12.5% grass-shrub that is itself 0.32–0.83×.
- **Chaparral is bimodal:** SH5 runs *fast* (up to 15.5%U10, faster than the
  grass rule), but the dominant Malibu fuel SH2 is near-static.

### 1b. Realized head ROS on the REAL tiles (what the founder actually sees)

Nominal-member `compute_arrival` (the "ground-truth" member the fire draws),
median & max over 8 grass ignition points per tile (`diag_ros_probe.py`), head
ROS km/h, ratio vs the 10%-rule:

| tile (burnable mix) | high: median / max | extreme: median / max |
|---|---|---|
| **paradise_ca** (GR/GS 37%, **TL/TU 52%**, SH/SB 11%) | 0.98 (**0.22×**) / 2.28 (0.51×) | 1.91 (**0.30×**) / 5.29 (0.82×) |
| **malibu_ca** (GR/GS 41%, TL/TU 12%, SH/SB 47%) | 1.35 (0.30×) / 3.77 (0.84×) | 3.05 (0.47×) / 6.18 (0.96×) |
| **boulder_co** (GR/GS 87%, TL/TU 11%, SH/SB 2%) | 1.81 (0.40×) / 2.92 (0.65×) | 3.83 (0.59×) / 6.25 (0.97×) |

**The real tile is always slower than the clean single-fuel physics**, because the
head must cross the *slowest* fuel on its path (fuel-mosaic percolation):
- **Paradise is worst** — the **52% timber-litter mosaic throttles the median
  grass run to 0.22–0.30×** the grass rate. A center-cell ignition that lands on
  litter barely moves at all (0.07–0.12 km/h, <0.8 km in 6 h — `diag_ros_tile.py`).
- **Boulder** (87% grass) runs closest to clean physics; its *best* grass
  ignition nearly matches the anchor at extreme (0.97×), but the mosaic still
  costs ~40% at the median.

### 1c. Area growth vs the Camp anchor (acres/hour)

Camp Fire (Paradise, 2018) grew **~6,600 ac/h over its first 90 min**. Sim, first
90 min: flat-uniform GR2 at extreme = **662 ac/h (0.10×)**; the real Paradise tile
from a single grass ignition = **~9–16 ac/h (~0.002×)**. This matches the
hindcast's Camp anchor (0.01–0.12×). *Caveat:* area squares any ROS error
(A ∝ ROS²), a point ignition hasn't reached full frontage in 90 min, and Camp's
6,600 ac/h was a **crown-fire + mass-spotting** event, not a surface fire — so the
ac/h-vs-Camp ratio is expected to be unflatteringly low and is **not** the clean
metric. **The head-ROS ratio (1a/1b) is the honest measure.**

**Verdict: confirmed too slow** for grass/grass-shrub at every preset except the
very top, and **dramatically too slow on Paradise specifically** because of its
timber-litter mosaic.

---

## 2. Root cause of the slow bias

Three separable contributors, isolated in `diag_ros_probe.py` §B / `diag_ros_presets.py`:

### (a) The midflame WAF is faithful physics — NOT the bug

`rothermel.midflame_wind_mps` applies the standard chain: 10-m → 20-ft (÷1.15),
then the unsheltered Albini & Baughman (1979) WAF from fuel-bed depth. For grass
(GR2, depth 1 ft) **WAF = 0.362**, so only **31% of the 10-m wind survives to
midflame** (a 40-mph extreme wind → 5.63 m/s midflame). That *sounds* like the
culprit, but the sensitivity sweep refutes it:

| GR2 @ preset | faithful ROS (WAF) | ROS if fed 20-ft wind (no WAF) | ROS if fed raw U10 | 10%-anchor |
|---|---|---|---|---|
| low | 0.38 | 1.52 (1.2× anchor) | 1.85 | 1.29 |
| moderate | 1.31 | 5.61 (**1.9×**) | 6.87 | 2.90 |
| high | 3.01 | 13.02 (**2.9×**) | 15.95 | 4.51 |
| extreme | 6.35 | 27.63 (**4.3×**) | 33.84 | 6.44 |

Because Rothermel's wind factor is **superlinear** (φ_w ∝ U^~1.5 for grass),
un-throttling the wind **overshoots the anchor by 1.9–4.3× at high/extreme**
(27–34 km/h grass is physically absurd). **Raising the WAF is the wrong knob** —
it would over-speed exactly where the model is already correct.

### (b) Rothermel's structural grass underprediction — the real cause

The flat multiplier that would make faithful GR2 hit the anchor is **wind-dependent**:

| preset | low | moderate | high | extreme |
|---|---|---|---|---|
| required ×multiplier | **3.42** | **2.20** | **1.50** | **1.01** |

A big lift at low wind that **decays to 1.0 at extreme** is the textbook signature
of **Rothermel's documented grass underprediction at low-to-moderate midflame
winds** (the reason operational systems use the purpose-built CSIRO/Cheney
grassland model, or an ELMFIRE-style ADJ, instead of Rothermel for grass). The
moisture chain is faithful (dead-fuel moisture 1.4–2.8% at high/extreme, correct
for the dry presets). **This is an inherent Rothermel-for-grass limitation, not a
WG code defect.** A *flat* ADJ multiplier is therefore also wrong (it would
overshoot at extreme, undershoot at low).

### (c) Fuel-mosaic percolation — Paradise's dominant throttle

Independent of (b): even with perfect grass physics, the realized head must cross
the slowest fuel on its path. Paradise is **52% near-static timber-litter/TU**, so
the demonstrated head is **percolation-limited to ~0.22–0.30×** regardless of the
grass number (§1b; the floor prototype in §3 confirms a grass knob can't lift this
past ~0.32–0.34× on Paradise). Real Camp beat this by **crown fire + mass
spotting** — i.e. `cfg.spotting_enabled` territory (default OFF,
`ember_buffer_min=0`), *not* surface ROS. Surface litter genuinely *is* slow; the
mosaic slowness is partly **correct physics**.

**The ensemble is not implicated** — the nominal member is the ground truth the
fire follows; perturbations only widen the band.

---

## 3. Recommendation for the SIMULATOR

**Ship a sim-only, well-labelled grass head-ROS FLOOR keyed to the empirical
Cruz-Alexander rule — default OFF in `ResponseConfig`, ON in the simulator**
(exactly the `ember_threat_radius_m` pattern). Weighed against the alternatives:

| option | polarity / risk | verdict |
|---|---|---|
| **(a) Cruz-Alexander grass floor** `head_ros = max(rothermel_ros, frac·U10)` for grass/GS cells | Lifts grass only where Rothermel is slow; **defers to Rothermel at extreme and on slope-driven runs** (the max() never *lowers* anything). Shape matches the anchor at *every* preset by construction. | **SHIP (sim)** |
| (b) raise the midflame WAF for grass | **Wrong** — overshoots anchor 1.9–4.3× at high/extreme (§2a); corrupts the faithful WAF. | reject |
| (c) flat grass ROS ADJ multiplier | Wrong shape — the needed factor is 3.4×→1.0× (§2b); a constant overshoots extreme / undershoots low. | reject |
| (d) boost the preset winds | Superlinear φ_w makes this overshoot the top and also distorts RH/moisture and the "WAF is correct" story. | reject |
| (e) turn on `spotting_enabled` for the demo | Physically the *right* mechanism for Paradise's litter/crown mosaic (§2c), but a separate, independently-calibrated lever. | optional add-on |

### Why the floor is the honest choice (measured)

A floor of `0.10·U10` for GR and `0.07·U10` for GS **is** the empirical anchor, so
it reproduces it at all winds without a wind-dependent table, and it self-limits:

- Clean grass (definitional): low `max(0.38,1.29)=1.29` ✓, moderate `2.90` ✓,
  high `4.51` ✓, extreme `max(6.35,6.44)=6.44` ✓ — perfect anchor match.
- **Realized on real tiles, prototyped through the real engine path**
  (`diag_ros_floor.py`, monkeypatch — no product edit), median head ROS km/h:

  | tile | high OFF→ON | extreme OFF→ON |
  |---|---|---|
  | paradise_ca | 0.98 → **1.55** (+58%) | 1.91 → 2.06 (barely — correct) |
  | boulder_co | 1.81 → **2.70** (+49%) | 3.83 → 3.92 |
  | malibu_ca | 1.35 → **2.22** (+64%) | 3.05 → 3.18 |

  The floor lifts the demo most at "high" (where Rothermel is slowest) and
  **correctly barely binds at "extreme"** (Rothermel already ≈ anchor). It never
  touches timber, shrub, slope-driven, or the live product.

### Exact change

1. **Config knob** (`contracts.ResponseConfig`, alongside `ember_threat_radius_m`):
   ```python
   grass_ros_floor_frac: float = 0.0   # grass/GS head-ROS floor as frac of 10-m wind
                                       # (0 = off / live product byte-identical;
                                       #  sim sets ~0.10 GR — Cruz & Alexander 2020)
   ```
   Optionally a second `gs_ros_floor_frac: float = 0.0` (sim 0.07), or reuse one
   knob with a fixed GR:GS ratio.

2. **Physics insertion** — `spread.compute_arrival`, inside `cell_params`
   (spread.py ~441–445) and its byte-identity twin `cell_params_reference`
   (~459–465), applied to the head ROS before the ellipse directional step:
   ```python
   res = rothermel.surface_ros_from_bed(bed, u_eff, 0.0)
   frac = _grass_floor_frac(fuel.code, cfg)          # 0 for non-grass
   if frac > 0.0 and wind10 > 0.0:
       floor = frac * wind10                          # wind10 already carries wind_scale
       if res.ros_mps < floor:
           res = replace(res, ros_mps=floor)          # only raises; never lowers
   p = _pack_params(res, wind10, wdir, brg)
   ```
   Uses the cell's (perturbed) `wind10`, so ensemble members scale consistently;
   `max()` semantics preserve slope-driven and extreme-wind runs untouched.

3. **Sim opt-in** — `simui.py`: `SIM_GRASS_ROS_FLOOR_FRAC = 0.10` and, in
   `configure()` (mirroring lines 1346–1347):
   ```python
   if "grass_ros_floor_frac" not in overrides:
       cfg.grass_ros_floor_frac = SIM_GRASS_ROS_FLOOR_FRAC
   ```
   An explicit `cfg_overrides` value always wins.

4. **Label** (UI + contracts): *"Grass spread calibrated to the Cruz-Alexander
   cured-grass rule (≈10% of 10-m wind); simulator demonstration setting — the
   live safety engine runs uncalibrated Rothermel."*

**Effort: S/M** — ~15–25 lines across contracts/spread/simui, a floor-off
byte-identity fingerprint test (live path unchanged), a floor-on regression, and a
one-line update to the F3 hindcast note. No new dependency.

### Honest-numbers caveat (keeps the live "no fudge" discipline)

- **The floor is a SIMULATOR demonstration knob, not a validated live
  calibration.** `grass_ros_floor_frac` defaults **0.0** in `ResponseConfig`; the
  live scoring/alerting engine stays **byte-identical** and keeps running
  uncalibrated Rothermel. This does **not** discharge the hindcast's binding
  *DO-NOT-SHIP-A-FUDGE* verdict for the live product — that still requires ≥10
  incidents across fuel regimes + the spotting decision before any live default
  changes.
- **The floor fixes grass, not Paradise's mosaic.** Even armed, Paradise's
  realized head stays ~0.32–0.34× because 52% of the tile is near-static
  timber-litter (§2c). For a Paradise demo that *looks* like Camp, pair the floor
  with **igniting in / steering the head through connected grass** and/or flipping
  `spotting_enabled` — the litter throttle is arguably realistic surface physics,
  and Camp's speed came from spotting/crowning, not surface ROS.
- **`0.10` is the rule-of-thumb, not a fit.** It is the widely-cited
  Cruz-Alexander cured-grass coefficient; the GS `0.07` is a soft interpolation.
  Both are labelled as such, and the knob is a single dial an operator can turn
  down.

---

## Reproduce

```
PYTHONPATH=. py -3 scratch_review/diag_ros_presets.py   # presets, WAF, point ROS table
PYTHONPATH=. py -3 scratch_review/diag_ros_tile.py      # realized head ROS + ac/h on real tiles
PYTHONPATH=. py -3 scratch_review/diag_ros_probe.py     # multi-ignition robustness + WAF sensitivity
PYTHONPATH=. py -3 scratch_review/diag_ros_floor.py     # prototype the floor through the real engine
```
