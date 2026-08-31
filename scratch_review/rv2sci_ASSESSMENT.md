# PASS-2 INDEPENDENT VERIFIER — SCIENCE + SAFETY-LAW

Aspect: science + safety-law. Primary reviewer: Claude (`scratch_review/rvclaude_REPORT.md`).
Verifier: independent re-derivation (not Claude's JSON). 2026-08-31.

Probe scripts (this pass only, `scratch_review/rv2sci_*`):
`rv2sci_a2_null.py`, `rv2sci_a3_floor.py`, `rv2sci_a7_disp.py`,
`rv2sci_a8_carve.py`, `rv2sci_a9_relax.py`, `rv2sci_a11_stale.py`,
`rv2sci_knobs.py`. Outputs: `rv2sci_a2.json` … `rv2sci_a11.json`,
`rv2sci_knobs.json`, `rv2sci_replay_committed.json`.

Method: re-implemented or re-ran every cited calculation against engine source
and committed corpora. `wgresponse/` and `docs/` were not modified. Claude's
`rvclaude_*.json` were treated as untrusted except as a recount target for A8's
64-scenario sweep file (independently re-summed; one scenario re-executed).

---

## 1. A2 null-model skill — CONFIRMED

Independent isotropic dilation of each August as-deployed T0 mask (exact
Felzenszwalb–Huttenlocher EDT, no weather/fuel/Rothermel), same 18 pairs,
same burnable filter.

| Quantity | Claude | This pass |
|---|---|---|
| Engine coverage (as-deployed) | 93.79% | **93.7889%** (126691 / 135081) |
| Engine area budget | ~5.8× (headline) | **5.7909×** predicted-new-cells / obs new-burnable |
| Null coverage at that budget | 68.18% | **68.1828%** |
| Null area to match engine cov | 10.72× | **10.7206×** |
| Skill (null-area / engine-area) | 1.85× | **1.8513×** |
| Mask `n_new` vs published `n_new_burnable` | (claimed match) | **18/18 exact; 0 mismatches** |

Verdict: **CONFIRMED**. The engine is 1.85× more area-efficient than a circle
at equal area budget. Claude's 6.06× / 8.90× acre-ratio framing is a different
statistic (pair-pooled acres, including cell-size mix); the skill numbers that
were asked for use the cell-budget interpolation and match to 0.01 pp / 0.01×.

---

## 2. A3 extinction override — CONFIRMED

### Floor probe (independent, `ResponseConfig(grass_ros_floor_frac=0.10)`, U10=10 m/s)

At `m_dead >= mx`:

| Fuel | mx | Rothermel km/h | Floored km/h | Intensity | Flame |
|---|---|---|---|---|---|
| GR2 (102) | 0.15 | **0.000** | **3.600** | **0** | **0** |
| GS2 (122) | 0.15 | **0.000** | **2.520** (~2.5) | **0** | **0** |
| TU5 (165) | 0.25 | 0.000 | 1.800 | 0 | 0 |
| TL3 (183) | 0.20 | 0.000 | 1.080 | 0 | 0 |

Mechanism: `spread._pack_params` does `head_ros = max(rothermel, frac*U10)` with
**no moisture/curing gate**. Intensity and flame length are taken from the
**unfloored** `RosResult`, so a physically extinct cell still walks at the
floor with I=0, L=0. SH is unfloored (ratio 0). TL inflation at m=0.15 is
**45.1×**; at m=0.10 ~32× — matches Claude's "19–45× TL / ~12× TU".

Claude's "3.6/2.5 km/h" is GR2 3.600 / GS2 2.520, rounded.

### Moisture exceedance (own pass over `hindcast_aug_data/weather/*.json`)

Simard EMC×1.03 via `fuels.dead_fuel_moisture_frac`.

- Pooled **n = 56,136** hours, fraction `m_dead > 0.15` = **39.3758% → 39.38%**
- GR/GS extinction (mx=0.15) crossed at **RH = 76.39% ≈ 76.4% at 25 °C**
- Two incidents in the 38–41% band: `78d35d3bf791…` **40.95%** (n=52,584) and
  `a60ca689bd59…` **38.84%** (n=672)

Nuance (does not refute): 52,584 / 56,136 = 93.7% of the pooled hours are one
cached series. The 39.38% is still the number Claude computed; it is
cell-unweighted over cached ERA5 hours, not over scored fire-windows.

Verdict: **CONFIRMED**.

---

## 3. A7 dispersion — CONFIRMED

Independent pass over `hindcast_aug_data/aug_replay.json` as-deployed
(n=18, `n_new_burnable>0`, `obs_head_m` present).

| Quantity | Claude | This pass |
|---|---|---|
| median `head_P10/head_P50` | ~1.093 | **1.0927** |
| sd(log `head_P50/obs`) | ~0.710 | **0.7102** |
| band half-width (P50→P10, log) | 0.089 | **0.0886** |
| under-dispersion | 8× | **8.01×** |
| observed inside [P10, P50] | 1/18 | **1/18** (Bug-CA +12.97 h only) |

### PIT extreme-bin masses (harness `pit_hist`, burnable new cells)

| Era | rank-0 | rank-n | both extremes |
|---|---|---|---|
| Aug as-deployed | 13.24% | 66.41% | **79.65%** |
| Aug calibration | 55.63% | 27.03% | **82.66%** |
| July OOS blend 0.5 (`replay_b0.5.json`) | 55.80% | 32.45% | **88.26%** |

Claude's "76–95% of mass in the two extreme bins, every era" holds
(79.7 / 82.7 / 88.3). Floor-on (as-deployed) *moves* mass from rank-0 into
rank-n (model-early) without flattening the U-shape.

### Fresh ensemble instrumentation (synthetic 61×61 GR2, 8 m/s, 3 h)

- Realized perturbations from source, not docs: `wind_scale = 1 + u·0.25·0.5`
  → **±12.5% wind**, `moisture` **±10%**, direction **±30°** at 96 h
  (docs/handoff still say "wind ±25%"). **CONFIRMED**.
- Hazen p10 position for n=6 is `0.10*6 - 0.5 = 0.10` → interpolates 10% off
  the sample min. Nominal-bounded clamp then sets p10 = min(Hazen, nominal).
  Synth 3 h heads: p10 = nominal = 3818 m, p50 = 2673 m, ratio **1.43**
  (wider than the floored August 1.093 — consistent with Claude's "floor
  narrowed the band 17.2→9.3%"). Member-head CV only **0.097**.

Verdict: **CONFIRMED**. P10 is not a 10th percentile of forecast error.

---

## 4. A8 audit carve-out — CONFIRMED

### Independent recount of `rvclaude_audit_carveout.json` (64 scenarios)

| | Claude addendum | This pass |
|---|---|---|
| Total exempted imminent-non-idle ticks | 106 | **106** |
| Legacy | 104 | **104** |
| Current-preset | **2** | **2** |
| Worst lead | −333.4 min | **−333.4 min** |

The two current-preset exemptions exist:

1. `malibu_ca` d=6000 c4 **moderate** current: `off_ticks=1`, mode=egress,
   tick 0, crew sim-g2, lead **+15.0 min** (inside the 30 min buffer, not
   hundreds past).
2. `malibu_ca` d=6000 c4 **extreme** current: `off_ticks=1`, mode=egress,
   tick 0, crew sim-g1, lead **−5.3 min**.

### Fresh rerun of the decisive legacy scenario
`paradise_ca` d=8000 c4 extreme, `PRESETS['legacy']`, PYTHONHASHSEED=0:

```
viol=0  burned_ct=0  EXEMPTED=5  modes={'egress': 5}
  tick 13  sim-g2/g4  egress  +24.3 min
  tick 17  sim-g1     egress  −189.5 min
  tick 22  sim-g2/g4  egress  −227.4 min
```

Byte-identical to Claude's stored worst-list for that row. Hundreds of
minutes past eroded-P10 (`−189.5`, `−227.4`) while the scoreboard reads 0.
Cause (source, not JSON): `simui.py:3629`

```
if not (burned or (imminent and cs._mode == "idle")): continue
```

Contract SEMANTICS 10c: "any crew … burned OR imminent". Implementation
exempts every non-idle imminent tick. The two current-preset exemptions
show the hole is not legacy-only; legacy amplifies it.

Verdict: **CONFIRMED**.

---

## 5. A9 zero-benefit relaxations — CONFIRMED numbers, CORRECTED interpretation

### JSON matched-pair arithmetic (recomputed from `opt_ember_*.json`)

Zero-delta pairs (e500, denom 272, 1 home = 0.37 pp):

| Knob (params label) | A → B | Δ e500 |
|---|---|---|
| `escape_hyst` | 0.0 → 20.0 | **+0.00 pp** (te_a_c8 vs tehy_a_c8; te_b_c8 vs tehy_b_c8) |
| `cold_black` | 1 → −1 | **+0.00 pp** (cb_c4 vs flip_c4 / flip_c4b; cb_c8 vs flip_c8) |
| `tiered` radii | 550,200,150 → 550,150,100 | **+0.00 pp** at 8 crews; **−0.37 pp** at 4 crews |
| `tiered` on vs unlabeled | 550,200,150 → `""` | **−5.88 pp** (tehy_a_c8 vs hy20_c8) i.e. labeled-on is **+5.88 pp** |

Claude's "0.00–0.37 pp plan-of-record" and "+5.88 pp is a safety relaxation
(smaller shrub/timber erosion)" **arithmetically CONFIRMED**.

### Interpretation CORRECTION

`opt_ember.py` only writes the override when the flag is truthy /
`cold_black >= 0`. `SimSession.configure` then **auto-enables** any absent key:

- `escape_hysteresis_margin_min` missing → **20** (`SIM_ESCAPE_HYSTERESIS_MIN`)
- `transit_cold_black_by_class` missing → **True**
- `corridor_erosion_tiered` missing → **True** (radii 550/200/150)

So:

- `escape_hyst=0.0` in the JSON params is **not** OFF; the key is omitted and
  the sim runs 20. The 0.00 pp "0 vs 20" pair is **20 vs 20**.
- `cold_black=-1` means "leave to session default" = True. The 0.00 pp
  "1 vs −1" pair is **True vs True**.
- True OFF requires pinning the key (`PRESETS['legacy']` does this;
  `opt_ember --escape-hyst 0` does not).

The 0.00 pp figures are real equal-e500 numbers. They are **not** evidence
that turning those relaxations on vs off is a no-op. Claude's *qualitative*
claim that the sim ships safety-relaxing defaults (hyst 20, per-class
cold-black, tiered 550/200/150) is still true — those are the sim auto-ons.

### cold_black_minutes vs stated derivation — CONFIRMED (GS4), with extras

Stated basis (`fuels.cold_black_minutes` docstring): `max(class floor, 3 ×
Anderson t_r)` with `t_r = 384/σ` of the **coarsest timelag class present**.
100-h SAV = 30 → t_r = 12.8 min → 3 t_r = **38.4 min**.

| Code | Shipped | 3 t_r (coarsest) | Δ |
|---|---|---|---|
| **GS4 (124)** | **20.0** | **38.4** | **−18.4 min (permissive)** |
| SH2/4/7/8 | 30.0 | 38.4 | −8.4 min (also permissive) |
| GR3/8/9, GS2/3 | 10.0 | 10.57 | −0.57 (rounding of 384/109) |

GS4 **CONFIRMED** 20 vs 38.4.

**NEW:** the engine's actual `fuel_bed(...).sigma_char` for GS4 at
(m_dead=0.06, herb=0.5, woody=0.9) is 1631 ft⁻¹ → t_r = 0.24 min →
3 t_r = **0.71 min**. Under *that* formula the 20 min class floor is the
binding (conservative) term. The mismatch Claude found is against the
docstring's coarsest-class formula — which is also what the GS4 comment
("carries 100-h load") claims to implement. Two formulas are written as if
they were one; only the coarsest-class reading produces 38.4.

Verdict: **CONFIRMED** (GS4 20 vs 38.4; 0.00 pp JSON arithmetic) /
**CORRECTED** (0.00 pp hyst/cold-black pairs are not on/off A/Bs under
current wiring).

---

## 6. A11 stale artifact — CONFIRMED (git HEAD)

`tools/hindcast_data/replay_results.json` is **modified in the working tree**.
Pooling the two copies separately:

| Copy | Coverage | Late | Med head (code `sorted[n//2]`) |
|---|---|---|---|
| **git HEAD (committed)** | **68.8189%** (41334/60062) | 31.1811% | **1.3205×** |
| Working tree (now) | 73.5190% (44157/60062) | 26.4810% | 1.7724× |
| Published table (blend 0.5) | 73.5% | 26.5% | 1.77× |

HEAD matches the calibration-review **blend = 0.0** row (68.8% / 31.2% /
1.32×). The working copy has been regenerated to the published blend-0.5
row — Claude's own `rvclaude_repro_check.py` compared a scratchpad
`old_replay.json` against this regenerated file.

Verdict: **CONFIRMED** for the committed (HEAD) artifact: **68.82% vs
published 73.5%**. Do not pool the dirty working copy. The handoff
instruction "re-derive from committed JSON" still fails against HEAD.

---

## 7. OPEN ITEM — DEFAULT-OFF knob inventory (the fourth)

Prior hunt: (1) erosion-relaxation `corridor_erosion_euclidean`,
(2) `route_alternatives`, (3) 10c idle-filter (hardcoded, not a knob).

`ResponseConfig` defaults were byte-checked (17/17 match). Full intercept
table:

| Knob | Default | Intercept | Flag-ON weakens a law? |
|---|---|---|---|
| `corridor_erosion_euclidean` | False | corridor (`geo.erode_min` disc vs Chebyshev square) | **YES — RELAXES.** Min-filter disc is smaller than the square's 1.53× diagonal. Prior #1. |
| `route_alternatives` | 1 | egress/corridor candidate routing | **YES — CAN WEAKEN.** Measured +3 caught crews. Prior #2. Rejected as built. |
| `corridor_erosion_tiered` | False | corridor (`spread.tiered_eroded_p10`; always euclidean discs; SH 200 / SLOW 150 vs uniform 500) | **YES — RELAXES** SH/timber spatial buffer, and forces disc erosion even when `corridor_erosion_euclidean` is off. Sim auto-ON. **FOURTH.** |
| `transit_cold_black_by_class` | False | SEM-10 route-selection passability (`simui._cold_black_pass_s`); 10c never reads it | **YES for grass** (45→5–20 min). Tightens timber. Sim auto-ON. |
| `escape_hysteresis_margin_min` | 0.0 | egress target adoption (`_escape_switch_ok`) | **YES — CAN WEAKEN** (hold a strictly-better-but-subthreshold target). Sim auto-ON at 20. |
| `escape_proactive_lead_min` | 0.0 | idle pre-pocket escape (SEM-10e) | NO — tightens only. Sim auto-ON at 120. |
| `ember_buffer_min` | 0.0 | P10 deadline (`latest_depart = p10 − (egress+ember)·60`) | NO — tightens. |
| `containment_gate_enabled` | False | alert ladder (SEMANTICS 8): caps NEW escalations at WATCH | **YES — WEAKENS the ladder.** Does not touch crew math. Default OFF pending founder approval. **Ladder-path fourth.** |
| `extreme_regime_enabled` | False | ladder on P10 when grass+wind detector fires | NO — tightens alerts. |
| `ember_threat_radius_m` | 0.0 | ladder/dispatch ETA for developed-cell homes | NO — ON enables alerts. OFF is the live A13 gap. |
| `spotting_enabled` | False | spread teleport edges → P10 | NO — conservative. Sim ON. |
| `grass_ros_floor_frac` | 0.0 | spread head ROS | NO law-relaxation (makes fire faster). Science defect = A3. Live watcher arms 0.10. |
| `wind_gust_blend` | 0.0 | effective 10-m wind | NO — conservative-fast. |
| `dispatch_plan_on_roads` | False | corridor T* along polylines; road legs then use `route_margin_roads_min=30` vs chord 56 | MIXED (honesty of geometry; numerically smaller margin). Sim ON. |
| `dispatch_hold_h` | 0.0 | coating deferral | NO crew-law relaxation. |
| `crew_shift_max_on_h` | 0.0 | fatigue rest (SEM-10 still governs) | NO. |
| `gel_env_model_enabled` | False | gel life ~6 h vs 72 h placeholder | NO (shorter gel is tighter for homes). |
| **10c idle-filter** | hardcoded `simui.py:3629` | audit `_crew_overruns` | **YES.** Not a knob. Prior #3. |
| corridor fail-open | hardcoded `safety.py` CorridorOracle | skip `v is None or inf`; T* stays +∞ | YES (Claude A14). Not a knob. |

### The fourth

Two new weaken-on-ON items sit in the same class as the prior three:

1. **`corridor_erosion_tiered` (recommended fourth, corridor path).** Distinct
   from `corridor_erosion_euclidean`: it both (a) switches to discs and (b)
   cuts SH/TU/TL/SB radii from 500 m to 200/150 m. The simulator silently
   forces it ON. This is the missing sibling of "erosion-relaxation".
2. **`containment_gate_enabled` (ladder path).** The only default-off flag
   whose ON state is *specified* as a ceiling on the alert ladder. Not armed
   today; still a law-weakener when flipped.

`transit_cold_black_by_class` and `escape_hysteresis_margin_min` are
additional weakeners; both are sim-auto-ON.

---

## 8. NEW findings (not in Claude, or sharpening)

1. **A9 wiring trap.** `opt_ember --escape-hyst 0` / `--cold-black -1` /
   `--tiered ""` omit the override key, so `SimSession` auto-enables the
   calibrated stack. Several "0.00 pp A/Bs" in the committed JSON are
   same-config tautologies, not measured zero benefit of a relaxation.
2. **Cold-black docstring vs engine t_r.** GS4 20 vs 38.4 is real against
   the coarsest-class formula. Against `fuel_bed.sigma_char` (the formula
   `surface_ros_from_bed` actually uses) 3 t_r = 0.71 min and 20 min is
   *stricter*. SH2/4/7/8 are also 30 vs 38.4 under the coarsest-class
   reading. Two derivations are published as one.
3. **A11 working-tree trap.** `replay_results.json` is dirty; naive pooling
   of the workspace file yields the published 73.52% and *hides* the 68.82%
   HEAD contradiction. Claude's finding is about HEAD.
4. **A3 sample concentration.** 93.7% of the 56,136 "August fire-hours" is
   one weather file. Arithmetic stands; the "two incidents 38–41%" are that
   giant series plus one n=672 file.
5. **Synth ensemble vs August band.** Unfloored synthetic grass at 3 h has
   head_P10/P50 = 1.43 vs August as-deployed 1.093 — independent support
   that the floor, not the perturbation recipe, is what crushed the band.

No refutations of Claude's three criticals (A1 not in scope; A2 and A3
hold) or of A7/A8/A11 as stated.

---

## 9. Verdict table

| Item | Verdict | Own numbers |
|---|---|---|
| A2 null skill | **CONFIRMED** | engine 93.79% vs null 68.18% at 5.79× area; skill **1.85×**; 18/18 mask match |
| A3 extinction override | **CONFIRMED** | GR2/GS2 at m≥0.15 → 0.000 vs **3.600 / 2.520** km/h, I=0; **39.38%** of 56,136 h; RH **76.4%** @25 °C |
| A7 dispersion | **CONFIRMED** | median P10/P50 **1.093**; sd(log err) **0.710**; inside **1/18**; PIT both-extremes 79.7 / 82.7 / 88.3%; wind **±12.5%** |
| A8 audit carve-out | **CONFIRMED** | rerun EXEMPTED=5, leads **−189.5 / −227.4** min, viol=0; sweep **2** current-preset exemptions (106 total) |
| A9 zero-benefit | **CONFIRMED** numbers / **CORRECTED** interpretation | 0.00 pp JSON real; hyst/cb pairs are not on/off; GS4 **20 vs 38.4** |
| A11 stale artifact | **CONFIRMED** | git HEAD **68.82%** vs published **73.5%** (working tree is 73.52% and dirty) |
| Open knob inventory | **DONE** | fourth = **`corridor_erosion_tiered`** (plus `containment_gate_enabled` on the ladder) |

---

## 10. Five-line aspect summary

The as-deployed engine has real directional skill (1.85× a circle at equal
area) but no moisture-of-extinction: 39% of cached August hours would be
physically out and are still walked at 3.6/2.5 km/h with zero intensity.
P10 is an 8×-too-narrow sample minimum, not a percentile; crew deadlines
are protected by the fixed 30-min buffers. The 10c audit's idle-only
imminent branch is a real, current-preset-reachable carve-out (two ticks
in the 64-scenario sweep; hundreds of minutes in legacy). Several
"0.00 pp safety relaxations" in the sim JSONs do not actually toggle the
knobs — but the sim *does* silently turn on tiered erosion, hysteresis,
and per-class cold-black, of which tiered-erosion is the fourth
default-off law-weakener. Shadow alerting still needs the record restated;
the binding science/safety blocks remain A3, A7, and the 10c contract gap.
