# Crown fire: forest fires may now crown (2026-10-12)

Founder, 2026-10-12: *"go with option 1 ... then continue to option 2 and 3"*
— option 3 of the Park City speed review being a crown-fire model.

This review builds the model (contracts SAFETY SEMANTICS 22, `cfg.crown_fire`,
default OFF), puts LANDFIRE's forest canopy maps on the 13 simulator tiles and
on the live engine's terrain path, and measures it four ways: against
published crown-fire spread rates, against the 10 %-of-the-wind benchmark, on
the 11 real 2026 fires of the belief-vs-physics hindcast, and on the 13
one-click scenarios with the crew-safety audit. Engine: `wgresponse/
rothermel.py` (the physics), `wgresponse/spread.py` (per cell), `wgresponse/
terrain.py` (canopy data), thin wiring in `simui.py`, `threat.py`,
`worlds.py`, `cli.py`. Scripts and results: `scratch_review/crown_fire_*.py`
and `.json` (section 6). Tests: `tests/test_crown_fire.py`.

## For the founder

**What crown fire is.** A forest fire burns in two layers: on the ground
(needles, litter, shrubs) and in the tree crowns. On the ground it creeps,
tens of metres an hour in litter. When the ground fire is hot enough and the
lowest branches are close enough, it climbs into the crowns ("torching");
with wind and a dense enough canopy it runs from crown to crown ("active crown
fire"), tens of times faster. The big runs of western wildfires are crown
fires, and they advance at roughly a tenth of the wind speed.

**What the engine did.** It modelled the ground fire only. The simulator
covered the gap with the "minimum speed" rule, which also lifts timber litter
and understory to a share of the wind. With that rule on grass only — what
the evidence supports — the seven demos that start in timber barely moved
(Park City: 50 acres in 6 hours).

**What the model does.** LANDFIRE maps, for every 30 m of forest, how high
the lowest branches are, how dense and tall the crowns are and how much of
the ground they cover. We now have those maps for all 13 simulator areas and
the live engine can fetch them. With the switch on, every forest square asks
the standard question (Van Wagner 1977): is the ground fire hot enough to
climb into these crowns? If it is, the square burns as a crown fire at the
speed of the best-tested published model (Cruz, Alexander & Wakimoto 2005,
used by the Canadian CFIS tool and the ELMFIRE simulator): a running crown
fire where the canopy is dense enough, a torching fire where it is not. The
crown fire is hotter, broader than the needle-shaped ground fire, and throws
its embers farther; a torching tree can loft one about half a kilometre at a
21 mph wind and nearly a kilometre at 40 mph. Aspen and oak stands, which
LANDFIRE marks as not carrying crown fire, never crown. The model only ever
speeds a fire up. With the switch off nothing changes, and it is off
everywhere until you decide.

**Does it get the speed right?** Where the canopy shades the forest floor, a
running crown fire in the model advances at **8-15 % of the wind speed** at
18-40 mph — the 10 %-of-wind benchmark of forest wildfires (±50 %) — while the
ground fire alone managed 0.1-1.3 %. On the real maps with a steady strong
wind the timber demos' fastest hour is 9-17 % of the wind wherever the fire reaches conifer (Park City 14 %,
Vail 11 %, Paradise 13 %, Glenwood Springs and Basalt 17 %), 8-16 % with the
10-minute spot-fire delay recommended below, against 4-11 % with option 1's
timber floor and 0-5 % with the grass floor alone. Fires that start in aspen
(Beaver Creek, Eagle–Vail) stay at 4-6 %. One caution: the
eight northern-Rockies crown runs that the US model was built from averaged
only 2-6 % of the wind, and the Cruz model runs about 3 times faster than
those averages (1.7 times their fastest stretches). Treat any single crown
run's speed as uncertain by a factor of about two.

**The demos: crown fire with the grass-only rule against option 1.** The 13
one-click scenarios through the page's own path (city weather, calibrated
embers), 6 hours, the crew-safety audit on. "Crown fire" is the recommended setting: grass-only rule, calibrated embers,
crown fire on, spot-fire delay 10 minutes; the next column is the same with
the calibrated 5-minute delay. Percentages: the fastest hour's head advance
as a share of the city weather's mean 10-m wind over the 6 hours.

| Scenario (where it starts) | Acres at 1 h / 3 h / 6 h: option 1 → crown fire | Fastest hour, head km/h (% of the wind): option 1 → crown fire | First home reached (h): option 1 → crown fire | Crown fire with the 5-min spot delay: acres at 6 h; fastest hour | Grass floor alone: acres at 6 h |
|---|---|---|---|---|---|
| park_city_windstorm (TU5 under conifer (CBH 0.3 m, CBD 0.22)) | 48 / 1,984 / 6,593 → 50 / 536 / 6,119 | 3.9 (12 %) → 3.9 (12 %) | 3.4 → 4.2 | 6,821; 5.4 (17 %) | 50 |
| vail_westerly (TL5 under conifer (CBH 1 m, CBD 0.08)) | 38 / 320 / 863 → 284 / 1,021 / 1,857 | 1.7 (5 %) → 3.2 (9 %) | 8.0 → 4.8 | 1,789; 2.9 (9 %) | 34 |
| beaver_creek_westerly (TU1 under aspen / oak (LANDFIRE hardwood code)) | 90 / 1,053 / 3,687 → 2 / 4 / 84 | 2.0 (6 %) → 0.8 (2 %) | 5.0 → 14.5 | 88; 0.8 (2 %) | 14 |
| glenwood_springs_westerly (TL8 under conifer (CBH 0.5 m, CBD 0.11)) | 68 / 953 / 2,504 → 102 / 1,103 / 8,445 | 2.1 (7 %) → 4.8 (16 %) | 3.4 → 3.4 | 14,199; 6.3 (21 %) | 198 |
| basalt_carbondale_southwesterly (TU1 under aspen / oak (LANDFIRE hardwood code)) | 126 / 955 / 3,927 → 2 / 681 / 5,254 | 1.7 (8 %) → 2.9 (13 %) | 6.0 → 5.9 | 5,576; 3.2 (15 %) | 346 |
| aspen_southwesterly (TL1, no canopy) | 70 / 937 / 4,099 → 2 / 2 / 2 | 1.4 (6 %) → 0.0 (0 %) | 5.5 → 41.6 | 2; 0.0 (0 %) | 2 |
| eagle_vail_corridor_westerly (TU2 under aspen / oak (LANDFIRE hardwood code)) | 59 / 593 / 2,550 → 10 / 109 / 287 | 2.0 (6 %) → 1.2 (3 %) | 8.5 → 16.2 | 297; 1.2 (3 %) | 247 |
| eagle_gypsum_westerly (GR2, no canopy) | 16 / 86 / 819 → 16 / 86 / 1,183 | 2.3 (7 %) → 2.5 (7 %) | 0.3 → 0.3 | 1,231; 2.5 (7 %) | 785 |
| boulder_chinook (GR2, no canopy) | 56 / 679 / 1,653 → 26 / 498 / 1,701 | 4.2 (9 %) → 3.3 (7 %) | 10.1 → 10.1 | 1,719; 3.7 (8 %) | 1,297 |
| rancho_santa_fe_santa_ana (GS2, no canopy) | 144 / 973 / 2,066 → 142 / 963 / 2,140 | 1.6 (7 %) → 1.5 (7 %) | 3.2 → 2.5 | 2,144; 1.6 (7 %) | 2,026 |
| malibu_santa_ana (SH5, no canopy) | 488 / 2,592 / 5,536 → 524 / 3,943 / 7,496 | 5.2 (11 %) → 5.7 (12 %) | 2.9 → 2.5 | 7,834; 6.9 (15 %) | 4,826 |
| paradise_ne_wind (SH4 under conifer (CBH 0.3 m, CBD 0.05)) | 68 / 761 / 1,942 → 386 / 1,769 / 4,662 | 2.2 (6 %) → 3.3 (8 %) | 3.3 → 1.8 | 5,068; 4.0 (10 %) | 532 |
| santa_rosa_diablo (GS1 under conifer (CBH 8.4 m, CBD 0.03)) | 70 / 789 / 3,925 → 32 / 426 / 2,084 | 2.9 (7 %) → 2.7 (7 %) | 3.2 → 4.0 | 2,936; 4.4 (11 %) | 570 |

* **Five demos that start in or reach conifer burn as forest fires again.**
  Park City, Vail, Glenwood Springs, Basalt–Carbondale and Paradise cover
  0.9-3.4 times option 1's area in 6 hours, at 8-16 % of the wind in their
  fastest hour, and 20-44 % of each fire burns as a running crown fire.
* **Three demos start where only the timber floor made the fire run** and
  stay small: Beaver Creek and Eagle–Vail ignite in aspen understory, Aspen
  in compact conifer litter on a spot LANDFIRE maps without trees (84, 287
  and 2 acres in 6 hours, against 3,687, 2,550 and 4,099 under option 1).
  That is how fires started there behave; their ignition points should move
  into the conifer.
* **The grass and shrub demos** change little (Boulder, Rancho Santa Fe +3-4 %)
  or grow where they run into forest (Malibu +35 %, Eagle–Gypsum +44 %);
  Santa Rosa, grass under scattered trees, burns about half of option 1's
  area.
* **Crew safety:** every run of every crown-fire setting in this study kept
  the audit at 0 departure violations and 0 crew-overrun rows, except one
  exploratory combination that is not recommended (crown fire on top of the
  timber floor, Beaver Creek: 2 rows, section 3.4).

**The real fires.** On the 11 real 2026 fires (mostly timber, at moderate
winds, with crews on them; the engine models no suppression), the live
engine with the grass-only rule and crown fire forecast 3.50 times the area that burned (median over 255 forecast days), the same as
the deployed engine with its timber floor (3.55 times), and its pessimistic
(P10) footprint held 99.78 % of the newly burned ground, also the same. Its
worst days are worse: on three days the P10 held only 82-89 % of the new
ground, where the deployed engine never held less than 94 %. Crown fire does
not make these forecasts better or worse on average: the over-forecast comes
from suppression and the fires' quiet days, which no spread model carries
(section 3.3).

**Can the simulator go back to the grass-only rule?** **Yes, with crown fire on, for the demos that start in conifer.** Park City,
Vail, Glenwood Springs, Basalt–Carbondale and Paradise burn as forest fires
again with the grass-only rule, at the speed the forest-fire benchmark
expects, with the crew-safety audit clean. Beaver Creek, Eagle–Vail and Aspen
would still crawl, because their ignition points sit in aspen or in litter
without trees, which carried fire only under the timber floor's lift; that is
what such fires really do, and moving those three ignition points into the
conifer fixes the demos. No demo needs the timber floor.

**Recommendation.**

1. **Simulator: switch crown fire on with the grass-only rule and the
   calibrated embers** (option 3 replaces option 1's timber floor), **with
   the spot-fire delay at 10 minutes**, the top of the ember calibration's
   range. A spot fire that lands in forest needs time to build up to a crown
   fire's speed; at the calibrated 5 minutes the crowning demos ran at up to
   21 % of the wind (Glenwood Springs: 14,199 acres in 6 hours), at 10 minutes
   at 7-16 %.
2. **Move the Beaver Creek, Eagle–Vail and Aspen ignition points into the
   conifer.**
3. **Live engine: keep crown fire off and the timber floor as deployed, for
   now.** On the real fires the pair matches the deployed engine on average,
   but its worst day for crew safety is worse (82 % of the new ground inside
   P10, against 94 %). Next step: a shadow run on live fires
   (`watch --grass-only --crown-fire`) alongside the fire belief.
4. **Never combine crown fire with the timber floor:** it counts the forest's
   speed twice (Glenwood Springs 22,984 acres in 6 hours, Vail 15,414; 4.0
   times the real fires' area).

**What it cannot do.** It has no slope effect on the crown fire itself (the
model is for level ground), no fire-driven "plume" behaviour and none of the
5-16 km plume spotting of the Camp and Dixie fires, and each 90 m square
crowns or not at once, without the minutes a real fire takes to build. The
ground wind under the trees is the engine's open wind, so the canopy
ignites more readily than in the US tools, which under-predict crown fire
for the opposite reasons. LANDFIRE's 2024 maps do not show recent burns,
beetle kill or thinning.

## 1. The model (contracts SAFETY SEMANTICS 22, `cfg.crown_fire`)

### 1.1 Canopy data

LANDFIRE's forest canopy layers of the **LF2024** release — the vintage of
the `LF2024_FBFM40` fuel the engine already uses — through the keyless LFPS
job API: `LF2024_CBH` (canopy base height), `LF2024_CBD` (canopy bulk
density), `LF2024_CC` (canopy cover), `LF2024_CH` (canopy height). LFPS also
offers LF2022, LF2023 and LF2025 (LF2025 only for the south-west and
north-west geographic areas), so LF2024 is the newest release covering every
tile. The LFPS metadata states the encodings, and the decode converts them to
physical units: CBH and CH metres × 10, CBD kg/m³ × 100, CC percent; non-
forest (0) and nodata (−9999) become 0 = no canopy.

* **Simulator tiles.** `py -3 tools/fetch_sim_tiles.py --canopy` submits one
  LFPS canopy job per committed tile over the tile's own bbox (the same bbox
  law, so the same grid), decodes it onto that grid with the terrain's
  nearest-neighbour rule (each cell's canopy is the 30 m pixel its fuel came
  from), attaches it and re-saves the tile. The tool refuses to write unless
  the terrain bands come back byte-identical. The `.wgt` format gains four
  optional bands after `ELEV_M` (`CBH_M`, `CBD_KGM3`, `CC_PCT`, `CH_M`, plus a
  `canopy_source` line); a file without them loads exactly as before with
  `canopy=None`. Per-tile provenance and request counts:
  `data/tiles/provenance_canopy.json`; every request:
  `out/lfps_sim_cache/canopy_requests.jsonl`.
* **Live domains.** `LfpsTerrain(canopy_layer_list=LfpsTerrain.DEFAULT_CANOPY_LAYERS)`
  fetches the canopy with a second job per domain, cached like the terrain
  (its own zip and decoded `.wgcanopy` file, mirrored to S3 the same way);
  `watch --crown-fire` turns it on together with the knob. Without
  `canopy_layer_list` nothing changes (no job, no file). The test world's
  terrain overlay follows the live provider.
* **LANDFIRE's hardwood code.** LANDFIRE codes broadleaf stands (aspen,
  Gambel oak, maple) with CBH 10 m and CBD 0.01 kg/m³ "to prevent false
  simulation of crown fire". These are the commonest canopy values on the
  Park City tile (on SH7, TU1 and GS1 cells). The model treats CBD below
  0.02 kg/m³ as no crown fuel.

### 1.2 Initiation (Van Wagner 1977)

A cell's surface fire (Rothermel, after the fuel-class floor) ignites the
canopy when its Byram fireline intensity reaches

    I_o = (0.010 · CBH · (460 + 25.9 · FMC))^1.5   kW/m

(CBH m, FMC the foliar moisture %). At FMC 100 %: 28 kW/m at CBH 0.3 m,
168 at 1 m, 875 at 3 m, 5,327 at 10 m.

### 1.3 Active or passive, and the crown rate of spread (Cruz et al. 2005)

The crown fire's rate of spread is the model of Cruz, Alexander & Wakimoto
(2005), the empirical model of the Crown Fire Initiation and Spread system
(CFIS) and of ELMFIRE:

    CROS_A = 11.02 · U10^0.90 · CBD^0.19 · exp(−0.17 · EFFM)   m/min

with U10 the 10-m open wind (km/h), CBD kg/m³ and EFFM the estimated fine dead
fuel moisture (%). Van Wagner's (1977) criterion for a continuous (active)
crown fire is a spread rate of at least 3.0 / CBD m/min (a critical mass flow
of 0.05 kg/m²/s through the canopy): CAC = CROS_A / (3.0 / CBD). At CAC ≥ 1
the fire is **active** and spreads at CROS_A; below, **passive** (torching,
intermittent crowning) at CROS_A · exp(−CAC). The cell spreads at the faster
of its surface fire and its crown fire.

EFFM is defined by Cruz et al. through Rothermel's (1983) look-up tables (the
NWCG pocket-guide tables): a reference moisture from temperature and
humidity plus a correction for month, time of day and shading. The engine's
dead fuel moisture already reproduces the reference value for exposed fuel at
midday (102 °F, 6 % RH: table 1 %, engine 1.4 %); under a canopy of 50 % cover
or more the tables add 3 points at midday (May–July, 1200–1559, level
ground), so the model does too.

Why not the US operational model: FARSITE, FlamMap and BehavePlus use
Rothermel's (1991) correlation, 3.34 × the surface spread of fuel model 10
with a 0.4 wind reduction, fitted to eight northern-Rockies runs. Cruz and
Alexander (2010) showed that this chain under-predicts crown fire; Alexander
and Cruz (2006) tested the Cruz model against 57 independent wildfire runs
(42 % within ±25 %, mean absolute percent error 51 % Canada, 60 % US). §3.1
compares both on Rothermel's own runs.

### 1.4 Intensity, shape, embers

* **Intensity.** I = R · (HPA + CFB · CBD · (CH − CBH) · 18,608 kJ/kg): the
  surface fire's heat per unit area plus the canopy fuel load burned
  (Rothermel 1991: 8,000 Btu/lb of needles). CFB = 1 for an active crown fire;
  for a passive one, Van Wagner's (1993) crown fraction burned with FARSITE's
  coefficient (0.9 when the surface spread has gone 90 % of the way from the
  initiation rate to the active threshold).
* **Shape.** Where the crown fire leads, the fire ellipse's length-to-breadth
  ratio is Rothermel's (1991, after Andrews 1986) crown-fire ratio
  1 + 0.125 · U20 (U20 the 20-ft wind in mi/h; 5.35 at a 40 mi/h 10-m wind)
  when it is broader than the surface ellipse (which the engine caps at 8). A
  lower ratio never lowers the spread at any angle.
* **Embers.** A crowning cell's ember (the unchanged SEMANTICS 11 draw, same
  seed) has a mean distance from its crown intensity (Sardoy: mean ∝ I^0.3).
  Under SEMANTICS 19 (`spot_fire_size`), its reach is the size-scaled Albini
  surface maximum at the crown intensity, but at least the distance one
  torching tree of the stand height lofts a brand (Albini 1979, as BehavePlus
  codes it: Douglas-fir coefficients, DBH = height ft / 5, one tree; about
  0.45 km at a 21 mi/h 10-m wind and 0.85 km at 40 mi/h under closed cover),
  once its fire covers 2 ha (`spread._TORCH_MIN_FIRE_HA`): a lone spot fire
  relays no torching ember.

### 1.5 Foliar moisture

`crown_foliar_moisture_pct` = 100 % by default (the US systems' default;
Scott & Reinhardt 2001). `crown_foliar_moisture_seasonal` = True uses the
Canadian FBP System's model instead: per cell, the date of minimum foliar
moisture from its latitude, longitude and elevation (e.g. day 155, early June,
at 2,500 m in the Eagle Valley), 85 % on that date, 120 % from 50 days after
it, on the forecast hour's UTC day.

### 1.6 Crew-safety corridor

With the knob on, `spread.tiered_eroded_p10` (the simulator's
`corridor_erosion_tiered`) puts every crown-fuel cell in the FAST erosion tier
(550 m) instead of the slow timber tier (150 m): the slow tier assumes slow
spread and short flames, which a crowning cell does not have. It only ever
widens the band.

### 1.7 Engine laws kept

* Knob OFF: byte-identical, with a canopy on the tile or not (the legacy
  fingerprints with a canopy attached; the hindcast's stored scores
  reproduce bit for bit on canopy-carrying tiles). Knob ON on a tile without
  canopy or without crown fuel: byte-identical too.
* Deterministic, never wall-clock (no new random draw); the ROS memo exact
  (the canopy values and FMC are in the crown memo key: memo =
  `WGRESP_NO_ROS_MEMO=1` reference with the knob on, with weather zones, the
  hourly ROS multiplier, piecewise edges, any-angle, spotting and seasonal
  FMC); label-setting; a no-op re-anchor is the continuous run cell for cell
  (spotting, the size law, the torching reach and any-angle included).
* Under constant weather and with spotting off no cell burns later than
  without the knob. With spotting on, single cells can (each ember's draw
  re-scales with the crown intensity), while the burned area grows.
* Cost: 1.3–1.4× a one-click scenario's nominal field, 2.3× on the largest
  hindcast domain (it also burns much more).

## 2. Data fetched

LANDFIRE **LF2024** forest canopy (`LF2024_CBH;LF2024_CBD;LF2024_CC;LF2024_CH`,
the vintage of the tiles' `LF2024_FBFM40`), one LFPS job per area, with the
existing etiquette: the job email (env `LFPS_EMAIL`, else the ops address),
a 10 s status poll, sequential jobs (at most two in flight), the raw zip
cached before decode, every request logged (0 responses other than
HTTP 200).

| What | Areas | LFPS requests (submit / status polls / zip downloads) | Bytes downloaded |
|---|---|---|---|
| The 13 standard simulator tiles (`data/tiles/<key>.wgt`, canopy attached) | 13 | 13 / 431 / 13 | 7.4 MB |
| The extended fire-zone tiles (`data/tiles/<key>_ext.wgt`, canopy attached) | 11 | 11 / 359 / 11 | 20.8 MB |
| The 11 hindcast domains (`out/bvp_data/lfps/*.wgcanopy`, not committed) | 11 | 11 / 140 / 11 | 11.2 MB |
| The LFPS product list (layer names) | — | 1 GET | 26 kB |

Per standard tile (`data/tiles/provenance_canopy.json`, which also holds the
extended tiles' records; the forest canopy is where CBD > 0 and CH > CBH;
percentiles over burnable cells with canopy):

| Tile | Forest canopy (% of tile) | Canopy on TU / TL / SH / GS / GR (%) | Burnable canopy CBH P10 / P50 / P90 (m) | CBD P10 / P50 / P90 (kg/m³) | LFPS requests (submit / polls / download) |
|---|---|---|---|---|---|
| aspen_co | 63.7 | 99.6 / 81.5 / 0.4 / 46.0 / 0.0 | 0.3 / 0.7 / 10.0 | 0.01 / 0.06 / 0.16 | 1 / 37 / 1 |
| basalt_carbondale_co | 40.8 | 99.2 / 97.5 / 0.3 / 36.7 / 0.9 | 0.3 / 0.3 / 10.0 | 0.01 / 0.12 / 0.27 | 1 / 41 / 1 |
| beaver_creek_co | 72.9 | 99.9 / 88.0 / 0.7 / 54.8 / 2.8 | 0.3 / 0.8 / 10.0 | 0.01 / 0.06 / 0.16 | 1 / 39 / 1 |
| boulder_co | 7.8 | 88.9 / 86.9 / 38.9 / 12.8 / 0.9 | 0.6 / 2.0 / 10.0 | 0.01 / 0.04 / 0.06 | 1 / 41 / 1 |
| eagle_co | 55.1 | 99.7 / 91.4 / 0.0 / 58.6 / 1.8 | 0.3 / 0.3 / 1.4 | 0.05 / 0.12 / 0.19 | 1 / 42 / 1 |
| eagle_vail_co | 67.3 | 99.7 / 81.9 / 0.2 / 58.6 / 2.2 | 0.3 / 0.6 / 10.0 | 0.01 / 0.09 / 0.16 | 1 / 45 / 1 |
| glenwood_springs_co | 49.0 | 88.3 / 80.7 / 2.9 / 27.9 / 0.4 | 0.3 / 0.6 / 10.0 | 0.01 / 0.06 / 0.19 | 1 / 41 / 1 |
| malibu_ca | 9.8 | 78.8 / 69.9 / 5.2 / 14.6 / 3.8 | 1.2 / 4.1 / 10.0 | 0.01 / 0.06 / 0.11 | 1 / 17 / 1 |
| paradise_ca | 66.9 | 94.9 / 91.4 / 24.7 / 73.1 / 51.4 | 0.4 / 2.4 / 9.0 | 0.03 / 0.06 / 0.12 | 1 / 16 / 1 |
| park_city_ut | 48.4 | 98.3 / 91.3 / 57.5 / 22.8 / 7.9 | 0.3 / 10.0 / 10.0 | 0.01 / 0.01 / 0.16 | 1 / 30 / 1 |
| rancho_santa_fe_ca | 5.2 | 63.4 / 68.7 / 3.0 / 6.8 / 2.3 | 0.6 / 2.0 / 10.0 | 0.01 / 0.06 / 0.09 | 1 / 21 / 1 |
| santa_rosa_ca | 45.1 | 78.7 / 84.0 / 32.0 / 59.1 / 17.1 | 0.8 / 4.3 / 10.0 | 0.03 / 0.08 / 0.16 | 1 / 17 / 1 |
| vail_co | 74.8 | 99.9 / 78.1 / 1.2 / 43.7 / 15.1 | 0.5 / 0.7 / 10.0 | 0.01 / 0.08 / 0.16 | 1 / 44 / 1 |

The 11 extended fire-zone tiles (`<key>_ext.wgt`): forest canopy on 4.8-68.6 % of the tile (aspen_co, basalt_carbondale_co, beaver_creek_co, boulder_co, eagle_co, glenwood_springs_co, malibu_ca, paradise_ca, park_city_ut, santa_rosa_ca, vail_co).

* The canopy sits where the timber is (63-100 % of TU and 69-98 % of TL
  cells) — the check that canopy and fuel came from the same pixels.
* LANDFIRE's canopy base heights are low: on ten tiles the median forest
  cell has its lowest branches at 0.3-2.4 m, so a timber understory fire
  initiates easily. Malibu and Santa Rosa read 4.1-4.3 m and Park City 10 m:
  much of their "forest" is LANDFIRE's hardwood code (aspen, oak, maple:
  CBH 10 m / CBD 0.01), which never crowns here.
* The terrain bands of every tile are byte-identical to the committed ones
  (the tool refuses to write otherwise); a standard tile adds ~0.45 MB.

## 3. Validation

### 3.1 Published crown-fire spread rates (controlled cases, uniform tiles)

`scratch_review/crown_fire_uniform.py r91`. Rothermel (1991, table 1) is the
classic published set of measured wind-driven crown-fire runs with their
fuel moistures and winds: eight runs in the northern Rocky Mountains, the
data the US systems' crown model is fitted to. For each run: the surface
spread of fuel model 10 with a 0.4 wind reduction (R10, the engine's
Rothermel), Rothermel's crown rate 3.34 × R10, the Cruz et al. (2005) rate
at four canopy bulk densities (the runs' CBD is not published; "p" marks a
passive fire), and the ENGINE on a uniform flat tile with the run's wind and
1-h moisture (TU5 surface fuel, CBH 1.5 m, CBD 0.12 kg/m³, CH 20 m, cover
40 %, crown fire and any-angle on). Spread rates in mi/h, as published.

| Run (Rothermel 1991, table 1) | 1-h moisture | 20-ft wind (mi/h) | Observed average (max), mi/h | R10 published / engine | Rothermel 1991 (3.34 R10) | Cruz 2005 at CBD 0.05 / 0.08 / 0.12 / 0.20 | Engine, uniform tile (CBD 0.12) | Engine surface fire alone |
|---|---|---|---|---|---|---|---|---|
| Sundance 2-7 pm | 7 % | 30 | 1.40 (2.50) | 0.45 / 0.45 | 1.52 | 2.63 / 2.87 / 3.10 / 3.42 | 3.10 | 0.32 |
| Sundance 7-9 pm | 8 % | 45 | 3.00 (6.00) | 0.78 / 0.77 | 2.58 | 3.19 / 3.49 / 3.77 / 4.16 | 3.77 | 0.49 |
| Red Bench 4:30-8:30 pm | 5 % | 12 | 0.52 (0.64) | 0.20 / 0.13 | 0.44 | 0.79p / 1.77 / 1.91 / 2.11 | 1.91 | 0.12 |
| Lily Lake 2-5 pm | 5 % | 25 | 0.92 (—) | 0.29 / 0.29 | 0.98 | 3.13 / 3.43 / 3.70 / 4.08 | 3.70 | 0.22 |
| Sandpoint 2:30-5:30 pm | 4 % | 12-20 | 1.04 (1.76) | 0.26 / 0.27 | 0.89 | 3.04 / 3.32 / 3.59 / 3.95 | 3.59 | 0.21 |
| Pattee Canyon 4:25-5:15 pm | 4 % | 25-35 | 1.56 (—) | 0.49 / 0.48 | 1.62 | 5.03 / 5.50 / 5.94 / 6.54 | 5.94 | 0.34 |
| Mink Creek 1-5 pm | 5 % | 10-20 | 0.55 (—) | 0.21 / 0.21 | 0.70 | 2.56 / 2.80 / 3.03 / 3.34 | 3.03 | 0.17 |
| Black Tiger 1-3:30 pm | 3 % | 11 | 0.51 (0.89) | 0.12 / 0.12 | 0.41 | 0.82p / 2.30 / 2.48 / 2.74 | 2.48 | 0.11 |

* **The surface model is right.** The engine's R10 matches Rothermel's
  published R10 on seven of the eight runs to the second decimal (Red Bench:
  0.13 vs 0.20).
* **The engine reproduces the Cruz model exactly** (engine column = the
  Cruz CBD 0.12 column, every run): the crown step, the lattice and the
  ellipse add nothing of their own.
* **Rothermel's 3.34 × R10 matches these runs** — it was fitted to them
  (ratio to the observed average 0.80-1.27).
* **Cruz runs 1.3-5.5 times faster than these runs' averages** (geometric
  mean 3.3×) and 0.6-3.0 times their observed maxima (1.7×). At a low CBD
  two runs turn passive and come close (Black Tiger, sparse ponderosa pine:
  0.82 against 0.51 average / 0.89 maximum). These runs averaged 2.4-5.8 % of
  the 10-m wind, about half the 10 %-of-wind rule. On 57 independent
  wildfire runs (Alexander & Cruz 2006: 43 Canadian, 14 US, more severe than
  its experimental data) the Cruz model's mean absolute percent error was
  51 % (Canada) and 60 % (US), 42 % of runs within ±25 %; the US systems'
  chain under-predicts (Cruz & Alexander 2010).

### 3.2 The 10 %-of-wind benchmark in conifer at strong wind

Cruz & Alexander (2019): wildfires in forest and shrub advance at about 10 %
of the 10-m open wind (mean relative error under 50 % in dry fuel and strong
wind). **Uniform tiles** (`crown_fire_uniform.py wind`; flat, 60 m cells,
any-angle on, head speed between 2 and 14 km from the ignition): four timber
surface fuels under three LANDFIRE-typical canopies (dense CBH 0.5 m / CBD
0.16 / cover 65 %; moderate 1.0 / 0.10 / 55 %; open 2.0 / 0.06 / 35 %), at
the simulator's four intensity presets. Head speed and its share of the 10-m
wind; A active, P passive crown fire, S surface fire only:

| Surface fuel, canopy | low (8 mi/h) | moderate (18 mi/h) | high (28 mi/h) | extreme (40 mi/h) |
|---|---|---|---|---|
| TU1, dense | 0.03 km/h (0.3 %) S | 2.7 km/h (9.2 %) A | 5.3 km/h (11.8 %) A | 9.4 km/h (14.6 %) A |
| TU1, moderate | 0.03 km/h (0.3 %) S | 0.10 km/h (0.3 %) S | 4.9 km/h (10.8 %) A | 8.6 km/h (13.3 %) A |
| TU1, open | 0.03 km/h (0.3 %) S | 0.10 km/h (0.3 %) S | 0.18 km/h (0.4 %) S | 12.9 km/h (20.1 %) A |
| TU5, dense | 0.4 km/h (3.1 %) P | 2.7 km/h (9.2 %) A | 5.3 km/h (11.8 %) A | 9.4 km/h (14.6 %) A |
| TU5, moderate | 0.5 km/h (4.0 %) P | 2.4 km/h (8.4 %) A | 4.9 km/h (10.8 %) A | 8.6 km/h (13.3 %) A |
| TU5, open | 0.8 km/h (6.3 %) P | 3.7 km/h (12.7 %) A | 7.4 km/h (16.4 %) A | 12.9 km/h (20.1 %) A |
| TL3, dense | 0.01 km/h (0.1 %) S | 0.04 km/h (0.1 %) S | 0.08 km/h (0.2 %) S | 9.4 km/h (14.6 %) A |
| TL3, moderate | 0.01 km/h (0.1 %) S | 0.04 km/h (0.1 %) S | 0.08 km/h (0.2 %) S | 0.15 km/h (0.2 %) S |
| TL3, open | 0.01 km/h (0.1 %) S | 0.04 km/h (0.1 %) S | 0.08 km/h (0.2 %) S | 0.15 km/h (0.2 %) S |
| TL8, dense | 0.4 km/h (3.1 %) P | 2.7 km/h (9.2 %) A | 5.3 km/h (11.8 %) A | 9.4 km/h (14.6 %) A |
| TL8, moderate | 0.04 km/h (0.3 %) S | 2.4 km/h (8.4 %) A | 4.9 km/h (10.8 %) A | 8.6 km/h (13.3 %) A |
| TL8, open | 0.04 km/h (0.3 %) S | 0.12 km/h (0.4 %) S | 7.4 km/h (16.4 %) A | 12.9 km/h (20.1 %) A |

* Where the fire crowns at 18-40 mi/h it runs at **8-15 % of the wind under
  a shading canopy** — inside the 10 % ± 50 % band — and 16-20 % under open
  canopies at the high and extreme presets (exposed litter at 1.4-2.5 %
  moisture).
* Without crown fire the same stands burn at 0.1-1.3 % of the wind.
* Timber litter (TL3) under a canopy with its lowest branches at 1-2 m does
  not crown even at 40 mi/h (its surface fire stays below the initiation
  threshold); under a 0.5 m canopy base it crowns at 40 mi/h.

**Real maps** (`crown_fire_scenarios.py ... --generic`): the one-click
scenarios' own tiles, ignitions and wind directions, but the simulator's
generic presets held for 6 h (Park City, Paradise, Santa Rosa "extreme"
40 mi/h; the Colorado towns "high" 28 mi/h), nominal fire only. Fastest 1-h
advance of the head (spotting included) and acres at 6 h:

| Scenario (preset wind) | Fastest 1-h head gain: option 1 / grass-only / grass-only + crown / the same with the 10-min spot delay (share of the 10-m wind) | Acres at 6 h: option 1 / grass-only / grass-only + crown / 10-min delay |
|---|---|---|
| park_city_windstorm (40 mi/h) | 7.1 km/h (11 %) / 0.2 km/h (0 %) / 8.9 km/h (14 %) / 8.0 km/h (12 %) | 11,029 / 50 / 15,914 / 15,162 |
| vail_westerly (28 mi/h) | 1.6 km/h (4 %) / 0.4 km/h (1 %) / 4.8 km/h (11 %) / 3.8 km/h (8 %) | 586 / 32 / 4,788 / 3,355 |
| beaver_creek_westerly (28 mi/h) | 2.4 km/h (5 %) / 0.2 km/h (0 %) / 2.6 km/h (6 %) / 2.0 km/h (5 %) | 2,776 / 12 / 989 / 875 |
| glenwood_springs_westerly (28 mi/h) | 2.5 km/h (5 %) / 1.7 km/h (4 %) / 7.5 km/h (17 %) / 5.8 km/h (13 %) | 1,839 / 298 / 18,548 / 16,673 |
| basalt_carbondale_southwesterly (28 mi/h) | 2.7 km/h (6 %) / 2.0 km/h (5 %) / 7.7 km/h (17 %) / 7.2 km/h (16 %) | 2,496 / 394 / 20,592 / 19,251 |
| aspen_southwesterly (28 mi/h) | 2.3 km/h (5 %) / 0.1 km/h (0 %) / 4.2 km/h (9 %) / 4.1 km/h (9 %) | 2,814 / 6 / 618 / 574 |
| eagle_vail_corridor_westerly (28 mi/h) | 2.4 km/h (5 %) / 1.6 km/h (4 %) / 1.7 km/h (4 %) / 1.7 km/h (4 %) | 2,817 / 395 / 514 / 504 |
| eagle_gypsum_westerly (28 mi/h) | 2.1 km/h (5 %) / 2.1 km/h (5 %) / 2.4 km/h (5 %) / 2.4 km/h (5 %) | 172 / 172 / 208 / 208 |
| paradise_ne_wind (40 mi/h) | 4.1 km/h (6 %) / 2.9 km/h (4 %) / 8.6 km/h (13 %) / 7.3 km/h (11 %) | 2,282 / 1,571 / 19,765 / 18,116 |
| santa_rosa_diablo (40 mi/h) | 3.6 km/h (6 %) / 2.6 km/h (4 %) / 6.4 km/h (10 %) / 5.5 km/h (9 %) | 3,839 / 885 / 9,015 / 8,667 |

* Where the fire reaches conifer it runs at **9-17 % of the wind** (8-16 %
  with the 10-min spot delay), against 4-11 % with option 1's timber floor
  and 0-5 % with the grass floor alone.
* Glenwood Springs and Basalt sit at the top (17 %): long runs through dense
  conifer (canopy bulk density P90 0.19 and 0.27 kg/m³) carried forward by
  spot fires that start 5 min after landing; with 10 min, 13 and 16 %.
* Aspen's ignition (TL1 litter on a cell without canopy) needs about 3 h to
  reach the conifer at 28 mi/h; Beaver Creek's and Eagle–Vail's (understory
  under aspen) barely crown (4-6 % of the wind).

### 3.3 The 11-fire real hindcast

`scratch_review/crown_fire_hindcast.py` re-runs the physics stage of the
2026-09-27 study (`docs/review/belief_vs_physics_2026-09-27.md`): each fire
day by day, the production posture (`CFG_PROD`: gust blend 0.5, floor 0.10
gated, model error 0.71, the p50 bound, phi-vector slope, ensemble of 6 + the
nominal) seeded with perimeter k at its own time under the archive weather,
scored at perimeter k+1 — now on terrain tiles carrying the LF2024 canopy
(`crown_fire_hindcast_canopy.py`). The stored scores of the deployed arm and
of the grass-only side experiment reproduce bit for bit on the canopy tiles
with the knob off (`verify`). Four arms: **deployed** (timber floor, as live
today), **grass-only** floor, **grass-only + crown**, **deployed + crown**.
"P50 area / burned" is the median over steps of the P50 footprint's area
over the observed burned area at the next perimeter (1x = exact); **P10
adequacy** is the share of the newly burned cells inside the P10 footprint
(crew safety; higher is safer); false alarm is the forecast new area outside
the next perimeter as a share of the fire's area.

| Phase (steps) | P50 area / burned area, median: deployed / grass-only / grass-only + crown / deployed + crown | P10 adequacy | P50 hit | False alarm (% of fire area) |
|---|---|---|---|---|
| all (255) | 3.55x / 2.29x / 3.50x / 4.01x | 99.78 % / 97.10 % / 99.78 % / 99.90 % | 98.8 % / 89.7 % / 97.9 % / 99.5 % | 253 % / 130 % / 249 % / 299 % |
| growing (70) | 5.56x / 2.67x / 6.07x / 7.80x | 99.68 % / 95.33 % / 99.71 % / 99.91 % | 98.1 % / 85.1 % / 96.6 % / 99.1 % | 454 % / 176 % / 507 % / 678 % |
| moderate (50) | 4.14x / 2.34x / 4.01x / 5.03x | 99.90 % / 98.21 % / 99.95 % / 99.95 % | 99.7 % / 91.8 % / 99.6 % / 99.9 % | 311 % / 133 % / 300 % / 401 % |
| stalled (135) | 2.97x / 1.98x / 3.03x / 3.32x | 99.83 % / 98.58 % / 99.77 % / 99.87 % | 99.3 % / 93.9 % / 98.4 % / 99.6 % | 195 % / 96 % / 200 % / 230 % |

Per fire (all scored steps):

| Fire (steps) | P50 area / burned (median): deployed / grass-only / grass-only + crown / deployed + crown | P10 adequacy |
|---|---|---|
| aspenacres (21) | 3.32x / 2.70x / 3.42x / 3.43x | 99.99 % / 99.98 % / 99.99 % / 99.99 % |
| bobcatlakes (18) | 6.61x / 3.40x / 6.60x / 7.11x | 100.00 % / 99.95 % / 100.00 % / 100.00 % |
| border2 (8) | 2.25x / 1.25x / 1.74x / 2.74x | 98.74 % / 97.75 % / 98.83 % / 98.74 % |
| grasshopper (28) | 2.86x / 2.14x / 2.78x / 2.94x | 99.96 % / 95.94 % / 99.98 % / 99.96 % |
| hagen (36) | 3.89x / 2.37x / 4.21x / 4.34x | 99.84 % / 99.78 % / 99.88 % / 99.92 % |
| littlegiant (51) | 2.63x / 1.58x / 2.37x / 2.76x | 99.34 % / 92.77 % / 99.61 % / 99.76 % |
| plaskett (8) | 3.00x / 2.37x / 2.51x / 3.02x | 100.00 % / 98.67 % / 99.70 % / 100.00 % |
| sandcreek (16) | 5.86x / 3.63x / 6.20x / 6.22x | 100.00 % / 97.97 % / 100.00 % / 100.00 % |
| sinlahekin (27) | 2.60x / 2.02x / 2.49x / 2.65x | 99.96 % / 99.21 % / 99.98 % / 99.96 % |
| timber (23) | 3.70x / 3.38x / 3.43x / 3.71x | 100.00 % / 97.23 % / 97.74 % / 100.00 % |
| willow (19) | 6.08x / 2.02x / 4.68x / 7.55x | 100.00 % / 96.30 % / 99.62 % / 100.00 % |

Nominal forecast's new cells by fire type (grass-only + crown):
- growing: surface 50 %, passive 23 %, active 27 %
- moderate: surface 50 %, passive 27 %, active 23 %
- stalled: surface 57 %, passive 29 %, active 14 %

Steps whose P10 held less of the new ground than another arm's (of 255): grass-only + crown, 1 against the grass-only floor and 19 against deployed; deployed + crown, 5 and 1.

| Arm | Lowest step's P10 adequacy (fire, dates) | Steps under 90 % |
|---|---|---|
| deployed (timber floor) | 93.9 % (hagen, Jul 28 → Jul 29) | 0 |
| grass-only floor | 53.6 % (littlegiant, Aug 07 → Aug 08) | 18 |
| grass-only + crown | 82.3 % (timber, Aug 21 → Aug 22) | 3 |
| deployed + crown | 96.9 % (hagen, Jul 28 → Jul 29) | 0 |

Steps where an arm other than grass-only held under 90 %:
- littlegiant Sep 05 → Sep 06 (stalled): deployed (timber floor) 98.7 % / grass-only floor 81.4 % / grass-only + crown 89.1 % / deployed + crown 99.0 %
- timber Aug 21 → Aug 22 (growing): deployed (timber floor) 100.0 % / grass-only floor 75.9 % / grass-only + crown 82.3 % / deployed + crown 100.0 %
- timber Aug 25 → Aug 26 (growing): deployed (timber floor) 100.0 % / grass-only floor 87.3 % / grass-only + crown 88.0 % / deployed + crown 100.0 %

Reading:

* **Grass-only floor + crown fire reproduces the deployed engine on these
  fires: 3.50× against 3.55×**, by phase 6.07× / 4.01× / 3.03× against
  5.56× / 4.14× / 2.97× (growing / moderate / stalled), and P10 adequacy
  back at the deployed level (99.78 % pooled; the grass-only floor alone had
  dropped it to 97.10 %, 95.33 % on growing days). Crown fire puts back, for
  a physical reason, the speed the timber floor was adding: 43-50 % of the
  nominal forecast's new cells burned as crown fire (passive 23-29 %, active
  14-27 %).
* **It does not make these forecasts better.** The engine over-forecasts
  these mostly suppressed timber fires by 3.5× with or without it — the gap
  is suppression and the fires' long quiet stretches, which no spread model
  carries (the fire belief's job). On growing days crown fire is a little
  bigger than the timber floor (6.07× against 5.56×), on moderate and
  stalled days a little smaller or equal.
* **The P10 tail is worse than the deployed engine's.** Per step, the
  deployed P10 never held less than 93.9 % of the newly burned ground; with
  grass-only + crown three steps held 82-89 % (Timber, Aug 21-22 and Aug
  25-26; Little Giant, Sep 5-6), and 19 of 255 steps held less than the
  deployed P10 (by 0.04-17.7 points), while it held more on others. The
  misses are timber litter under a canopy the litter fire cannot ignite
  (Timber Aug 21-22: 161 of the 178 cells the deployed P10 held and crown
  missed were TL cells whose surface fire stayed below Van Wagner's
  threshold) and passive crown fire slower than the timber floor's 5 % of
  the wind (Little Giant: 116 of 187 were timber understory under passive
  crown fire): ground the real fires crossed and the timber floor's lift
  carried. Compared with the grass-only floor alone (18 steps under 90 %,
  down to 54 %), crown fire removes most of that loss, not all of it
  (`crown_fire_hindcast_misses.py`). For crews: against the grass-only floor
  crown fire moves P10 arrivals earlier (safer); against the deployed timber
  floor it moves them earlier in crowning forest and later in timber that
  does not crown.
* **Timber floor + crown fire** (crown on top of the deployed posture)
  over-forecasts further (4.01× pooled, 7.80× on growing days), with P10 at
  99.90 %: no step under 96.9 %, one step 0.04 points below the deployed P10.

### 3.4 The 13 one-click scenarios

`scratch_review/crown_fire_scenarios.py`: exactly the page's POST
/api/sim/new {scenario} (city weather, spotting on, the 24 h coating hold,
plan-on-roads, the crew-safety laws the simulator arms), 24 ticks of 15 min,
the crew-safety audit every tick. Variants: **opt1** = option 1 (timber
floor + calibrated embers `spot_fire_size`); **grass_law** = grass-only floor
+ calibrated embers; **crown** = grass-only floor + calibrated embers +
`crown_fire`; **crown_d10** = crown with `spot_ignition_delay_min` 10 (the
recommended setting); **crown_aa** = crown + `spread_any_angle`;
**opt1_crown** = option 1 + `crown_fire`. "Crowned at 6 h" = the nominal
fire's area that burned as an active / passive crown fire. First home
reached = the nominal fire at a client's own cell or, for a home on a
non-burnable cell, within the 200 m ember window. The share of the wind is
the fastest 1-h head gain over the city weather's mean 10-m sustained wind
in the first 6 h (`crown_fire_city_wind.py`; the simulator runs no gust
blend).

| Scenario (city wind, mean 0-6 h) | Variant | Acres 1 h / 3 h / 6 h | Fastest 1-h head gain, km/h (% of the wind) | First home reached | Crowned at 6 h (active / passive acres) | Departure violations / overrun rows | Homes served |
|---|---|---|---|---|---|---|---|
| park_city_windstorm (20 mi/h) | opt1 | 48 / 1,984 / 6,593 | 3.9 (12 %) | 3.44 h | 0 / 0 | 0 / 0 | 35 |
| park_city_windstorm (20 mi/h) | grass_law | 4 / 10 / 50 | 0.1 (0 %) | 14.15 h | 0 / 0 | 0 / 0 | 34 |
| park_city_windstorm (20 mi/h) | crown | 50 / 735 / 6,821 | 5.4 (17 %) | 3.64 h | 1,271 / 10 | 0 / 0 | 33 |
| park_city_windstorm (20 mi/h) | crown_d10 | 50 / 536 / 6,119 | 3.9 (12 %) | 4.19 h | 1,249 / 10 | 0 / 0 | 33 |
| park_city_windstorm (20 mi/h) | crown_aa | 50 / 783 / 7,180 | 5.2 (16 %) | 3.60 h | 1,293 / 10 | 0 / 0 | 34 |
| park_city_windstorm (20 mi/h) | opt1_crown | 160 / 4,495 / 12,602 | 4.8 (15 %) | 2.19 h | 1,849 / 14 | 0 / 0 | 35 |
| vail_westerly (21 mi/h) | opt1 | 38 / 320 / 863 | 1.7 (5 %) | 7.98 h | 0 / 0 | 0 / 0 | 37 |
| vail_westerly (21 mi/h) | grass_law | 2 / 8 / 34 | 0.2 (1 %) | 73.76 h | 0 / 0 | 0 / 0 | 0 |
| vail_westerly (21 mi/h) | crown | 330 / 1,013 / 1,789 | 2.9 (9 %) | 6.56 h | 719 / 0 | 0 / 0 | 36 |
| vail_westerly (21 mi/h) | crown_d10 | 284 / 1,021 / 1,857 | 3.2 (9 %) | 4.78 h | 729 / 0 | 0 / 0 | 37 |
| vail_westerly (21 mi/h) | crown_aa | 338 / 1,019 / 1,825 | 3.1 (9 %) | 6.38 h | 733 / 0 | 0 / 0 | 39 |
| vail_westerly (21 mi/h) | opt1_crown | 374 / 2,078 / 15,414 | 4.7 (14 %) | 2.58 h | 6,783 / 4 | 0 / 0 | 26 |
| beaver_creek_westerly (21 mi/h) | opt1 | 90 / 1,053 / 3,687 | 2.0 (6 %) | 5.01 h | 0 / 0 | 0 / 0 | 34 |
| beaver_creek_westerly (21 mi/h) | grass_law | 2 / 4 / 14 | 0.2 (1 %) | 36.84 h | 0 / 0 | 0 / 0 | 21 |
| beaver_creek_westerly (21 mi/h) | crown | 2 / 4 / 88 | 0.8 (2 %) | 14.18 h | 10 / 0 | 0 / 0 | 36 |
| beaver_creek_westerly (21 mi/h) | crown_d10 | 2 / 4 / 84 | 0.8 (2 %) | 14.52 h | 10 / 0 | 0 / 0 | 38 |
| beaver_creek_westerly (21 mi/h) | crown_aa | 2 / 6 / 400 | 1.6 (5 %) | 12.71 h | 94 / 0 | 0 / 0 | 39 |
| beaver_creek_westerly (21 mi/h) | opt1_crown | 180 / 2,740 / 12,334 | 5.2 (15 %) | 3.08 h | 3,549 / 16 | 0 / 2 | 27 |
| glenwood_springs_westerly (19 mi/h) | opt1 | 68 / 953 / 2,504 | 2.1 (7 %) | 3.36 h | 0 / 0 | 0 / 0 | 29 |
| glenwood_springs_westerly (19 mi/h) | grass_law | 6 / 10 / 198 | 1.5 (5 %) | 8.09 h | 0 / 0 | 0 / 0 | 32 |
| glenwood_springs_westerly (19 mi/h) | crown | 118 / 1,409 / 14,199 | 6.3 (21 %) | 3.20 h | 6,137 / 22 | 0 / 0 | 34 |
| glenwood_springs_westerly (19 mi/h) | crown_d10 | 102 / 1,103 / 8,445 | 4.8 (16 %) | 3.41 h | 3,693 / 8 | 0 / 0 | 24 |
| glenwood_springs_westerly (19 mi/h) | crown_aa | 122 / 1,501 / 15,860 | 5.7 (19 %) | 2.94 h | 6,811 / 26 | 0 / 0 | 33 |
| glenwood_springs_westerly (19 mi/h) | opt1_crown | 254 / 2,262 / 22,984 | 5.8 (19 %) | 2.46 h | 8,284 / 34 | 0 / 0 | 26 |
| basalt_carbondale_southwesterly (14 mi/h) | opt1 | 126 / 955 / 3,927 | 1.7 (8 %) | 6.02 h | 0 / 0 | 0 / 0 | 37 |
| basalt_carbondale_southwesterly (14 mi/h) | grass_law | 2 / 4 / 346 | 1.5 (7 %) | 14.57 h | 0 / 0 | 0 / 0 | 31 |
| basalt_carbondale_southwesterly (14 mi/h) | crown | 2 / 759 / 5,576 | 3.2 (15 %) | 5.49 h | 1,167 / 326 | 0 / 0 | 32 |
| basalt_carbondale_southwesterly (14 mi/h) | crown_d10 | 2 / 681 / 5,254 | 2.9 (13 %) | 5.93 h | 1,113 / 320 | 0 / 0 | 35 |
| basalt_carbondale_southwesterly (14 mi/h) | crown_aa | 2 / 1,581 / 6,593 | 4.4 (20 %) | 4.54 h | 1,337 / 376 | 0 / 0 | 36 |
| basalt_carbondale_southwesterly (14 mi/h) | opt1_crown | 276 / 3,509 / 9,710 | 3.4 (16 %) | 3.71 h | 1,721 / 548 | 0 / 0 | 28 |
| aspen_southwesterly (14 mi/h) | opt1 | 70 / 937 / 4,099 | 1.4 (6 %) | 5.50 h | 0 / 0 | 0 / 0 | 39 |
| aspen_southwesterly (14 mi/h) | grass_law | 2 / 2 / 2 | 0.0 (0 %) | 88.27 h | 0 / 0 | 0 / 0 | 0 |
| aspen_southwesterly (14 mi/h) | crown | 2 / 2 / 2 | 0.0 (0 %) | 41.29 h | 0 / 0 | 0 / 0 | 35 |
| aspen_southwesterly (14 mi/h) | crown_d10 | 2 / 2 / 2 | 0.0 (0 %) | 41.61 h | 0 / 0 | 0 / 0 | 36 |
| aspen_southwesterly (14 mi/h) | crown_aa | 2 / 2 / 2 | 0.0 (0 %) | 33.54 h | 0 / 0 | 0 / 0 | 37 |
| aspen_southwesterly (14 mi/h) | opt1_crown | 132 / 2,606 / 8,002 | 4.3 (20 %) | 3.50 h | 2,196 / 170 | 0 / 0 | 35 |
| eagle_vail_corridor_westerly (21 mi/h) | opt1 | 59 / 593 / 2,550 | 2.0 (6 %) | 8.48 h | 0 / 0 | 0 / 0 | 20 |
| eagle_vail_corridor_westerly (21 mi/h) | grass_law | 10 / 109 / 247 | 1.2 (3 %) | 26.59 h | 0 / 0 | 0 / 0 | 1 |
| eagle_vail_corridor_westerly (21 mi/h) | crown | 10 / 109 / 297 | 1.2 (3 %) | 16.24 h | 10 / 0 | 0 / 0 | 18 |
| eagle_vail_corridor_westerly (21 mi/h) | crown_d10 | 10 / 109 / 287 | 1.2 (3 %) | 16.23 h | 10 / 0 | 0 / 0 | 18 |
| eagle_vail_corridor_westerly (21 mi/h) | crown_aa | 10 / 128 / 316 | 1.6 (5 %) | 13.20 h | 10 / 0 | 0 / 0 | 18 |
| eagle_vail_corridor_westerly (21 mi/h) | opt1_crown | 59 / 1,137 / 6,247 | 3.0 (9 %) | 5.22 h | 1,601 / 59 | 0 / 0 | 21 |
| eagle_gypsum_westerly (21 mi/h) | opt1 | 16 / 86 / 819 | 2.3 (7 %) | 0.26 h | 0 / 0 | 0 / 0 | 24 |
| eagle_gypsum_westerly (21 mi/h) | grass_law | 16 / 86 / 785 | 2.3 (7 %) | 0.26 h | 0 / 0 | 0 / 0 | 22 |
| eagle_gypsum_westerly (21 mi/h) | crown | 16 / 86 / 1,231 | 2.5 (7 %) | 0.26 h | 334 / 0 | 0 / 0 | 25 |
| eagle_gypsum_westerly (21 mi/h) | crown_d10 | 16 / 86 / 1,183 | 2.5 (7 %) | 0.26 h | 318 / 0 | 0 / 0 | 27 |
| eagle_gypsum_westerly (21 mi/h) | crown_aa | 16 / 104 / 1,465 | 2.5 (7 %) | 0.26 h | 438 / 0 | 0 / 0 | 27 |
| eagle_gypsum_westerly (21 mi/h) | opt1_crown | 16 / 86 / 1,233 | 2.5 (7 %) | 0.26 h | 334 / 0 | 0 / 0 | 25 |
| boulder_chinook (29 mi/h) | opt1 | 56 / 679 / 1,653 | 4.2 (9 %) | 10.07 h | 0 / 0 | 0 / 0 | 23 |
| boulder_chinook (29 mi/h) | grass_law | 26 / 266 / 1,297 | 3.9 (8 %) | 10.26 h | 0 / 0 | 0 / 0 | 23 |
| boulder_chinook (29 mi/h) | crown | 26 / 544 / 1,719 | 3.7 (8 %) | 10.06 h | 68 / 10 | 0 / 0 | 24 |
| boulder_chinook (29 mi/h) | crown_d10 | 26 / 498 / 1,701 | 3.3 (7 %) | 10.06 h | 62 / 10 | 0 / 0 | 22 |
| boulder_chinook (29 mi/h) | crown_aa | 42 / 847 / 1,986 | 3.8 (8 %) | 9.49 h | 76 / 10 | 0 / 0 | 24 |
| boulder_chinook (29 mi/h) | opt1_crown | 178 / 1,019 / 2,136 | 4.0 (8 %) | 9.75 h | 78 / 10 | 0 / 0 | 24 |
| rancho_santa_fe_santa_ana (14 mi/h) | opt1 | 144 / 973 / 2,066 | 1.6 (7 %) | 3.19 h | 0 / 0 | 0 / 0 | 100 |
| rancho_santa_fe_santa_ana (14 mi/h) | grass_law | 144 / 973 / 2,026 | 1.6 (7 %) | 3.19 h | 0 / 0 | 0 / 0 | 99 |
| rancho_santa_fe_santa_ana (14 mi/h) | crown | 144 / 1,001 / 2,144 | 1.6 (7 %) | 2.42 h | 42 / 4 | 0 / 0 | 99 |
| rancho_santa_fe_santa_ana (14 mi/h) | crown_d10 | 142 / 963 / 2,140 | 1.5 (7 %) | 2.50 h | 42 / 4 | 0 / 0 | 99 |
| rancho_santa_fe_santa_ana (14 mi/h) | crown_aa | 160 / 1,081 / 2,204 | 1.6 (7 %) | 2.40 h | 46 / 4 | 0 / 0 | 99 |
| rancho_santa_fe_santa_ana (14 mi/h) | opt1_crown | 144 / 1,003 / 2,262 | 1.6 (7 %) | 2.42 h | 50 / 4 | 0 / 0 | 100 |
| malibu_santa_ana (29 mi/h) | opt1 | 488 / 2,592 / 5,536 | 5.2 (11 %) | 2.85 h | 0 / 0 | 0 / 0 | 29 |
| malibu_santa_ana (29 mi/h) | grass_law | 446 / 2,320 / 4,826 | 5.2 (11 %) | 3.04 h | 0 / 0 | 0 / 0 | 29 |
| malibu_santa_ana (29 mi/h) | crown | 667 / 4,499 / 7,834 | 6.9 (15 %) | 2.26 h | 903 / 0 | 0 / 0 | 28 |
| malibu_santa_ana (29 mi/h) | crown_d10 | 524 / 3,943 / 7,496 | 5.7 (12 %) | 2.55 h | 895 / 0 | 0 / 0 | 27 |
| malibu_santa_ana (29 mi/h) | crown_aa | 773 / 4,736 / 8,122 | 6.9 (15 %) | 2.01 h | 947 / 0 | 0 / 0 | 23 |
| malibu_santa_ana (29 mi/h) | opt1_crown | 703 / 4,528 / 7,874 | 6.9 (15 %) | 2.11 h | 915 / 0 | 0 / 0 | 27 |
| paradise_ne_wind (24 mi/h) | opt1 | 68 / 761 / 1,942 | 2.2 (6 %) | 3.33 h | 0 / 0 | 0 / 0 | 26 |
| paradise_ne_wind (24 mi/h) | grass_law | 30 / 214 / 532 | 1.4 (4 %) | 6.39 h | 0 / 0 | 0 / 0 | 27 |
| paradise_ne_wind (24 mi/h) | crown | 424 / 1,829 / 5,068 | 4.0 (10 %) | 1.57 h | 1,457 / 10 | 0 / 0 | 26 |
| paradise_ne_wind (24 mi/h) | crown_d10 | 386 / 1,769 / 4,662 | 3.3 (8 %) | 1.82 h | 1,341 / 10 | 0 / 0 | 25 |
| paradise_ne_wind (24 mi/h) | crown_aa | 424 / 1,875 / 5,332 | 4.0 (10 %) | 1.57 h | 1,543 / 10 | 0 / 0 | 25 |
| paradise_ne_wind (24 mi/h) | opt1_crown | 538 / 2,512 / 8,040 | 4.3 (11 %) | 1.45 h | 2,282 / 12 | 0 / 0 | 20 |
| santa_rosa_diablo (25 mi/h) | opt1 | 70 / 789 / 3,925 | 2.9 (7 %) | 3.23 h | 0 / 0 | 0 / 0 | 26 |
| santa_rosa_diablo (25 mi/h) | grass_law | 32 / 112 / 570 | 1.3 (3 %) | 10.84 h | 0 / 0 | 0 / 0 | 25 |
| santa_rosa_diablo (25 mi/h) | crown | 32 / 520 / 2,936 | 4.4 (11 %) | 3.74 h | 657 / 0 | 0 / 0 | 25 |
| santa_rosa_diablo (25 mi/h) | crown_d10 | 32 / 426 / 2,084 | 2.7 (7 %) | 3.99 h | 474 / 0 | 0 / 0 | 24 |
| santa_rosa_diablo (25 mi/h) | crown_aa | 34 / 729 / 4,349 | 3.6 (9 %) | 3.31 h | 929 / 0 | 0 / 0 | 24 |
| santa_rosa_diablo (25 mi/h) | opt1_crown | 166 / 2,554 / 8,463 | 3.8 (9 %) | 1.82 h | 1,661 / 0 | 0 / 0 | 23 |

Reading:

* **Crew safety.** Every run of the recommended setting (`crown_d10`), of
  `crown` and of `crown_aa` kept the audit at 0 departure violations and 0
  crew-overrun rows. The one exception in this study is the exploratory
  **option 1 + crown fire** on Beaver Creek: 2 "imminent" rows at 5.25-5.5 h,
  a crew whose staging spot the pessimistic front reached while a 17 km crown
  run surrounded it; it was re-routed (SEMANTICS 20b) and the audit reported
  it as designed (`crown_fire_overrun_probe.py`). That variant is not
  recommended for other reasons too (below).
* **Timber starts in conifer burn again.** With the 10-minute spot delay,
  Park City covers 6,119 acres in 6 h (option 1: 6,593), Vail 1,857 (863),
  Glenwood Springs 8,445 (2,504), Basalt–Carbondale 5,254 (3,927) and
  Paradise 4,662 (1,942); 20-44 % of each fire's 6-h area burns as active
  crown fire. Basalt's ignition sits in aspen (TU1 under the hardwood code),
  but its fire reaches the conifer within 3 hours.
* **Three ignitions sit where only the timber floor made the fire run.**
  Beaver Creek (TU1) and Eagle–Vail (TU2) start in understory under
  LANDFIRE's hardwood code, which never crowns, and Aspen's ignition cell is
  compact conifer litter (TL1) on a pixel LANDFIRE maps without trees. Their
  fires cover 84, 287 and 2 acres in 6 h (option 1: 3,687, 2,550, 4,099):
  what fires ignited there do at 14-21 mi/h. At the generic "high" preset
  (28 mi/h held for 6 h) Aspen's fire does reach the conifer and crowns
  (574 acres), Beaver Creek's and Eagle–Vail's barely (875, 504).
* **Outside timber** (Boulder, Rancho Santa Fe: grass and grass-shrub
  starts) little changes (+3-4 % against option 1). Paradise (shrub under a
  sparse conifer canopy) and Malibu (chaparral with patches of LANDFIRE
  forest canopy) gain:
  Paradise reaches its first home in 1.8 h instead of 3.3 h (the Camp Fire
  reached the town in about 1.5 h), Malibu's 6-h area is 35 % larger.
  Santa Rosa (grass under scattered trees) burns about half of option 1's
  area (2,084 against 3,925 acres).
* **Crown fire on top of the timber floor (`opt1_crown`)** is far too big:
  Park City 12,602 acres in 6 h, Vail 15,414, Glenwood Springs 22,984,
  Beaver Creek 12,334 — the "as deployed" runaway the Park City review
  measured.
* **Any-angle** spread adds 2-48 % to the crown fires' 6-h area (`crown_aa`
  against `crown`; Santa Rosa 2,936 → 4,349 acres, Basalt +18 %, Park City
  +5 %, Vail +2 %; Beaver Creek's slow fire 88 → 400).
* **Embers carry much of a crown fire's growth.** A crowning cell's ember
  flies on the crown intensity (20-100 MW/m), and every spot fire spreads at
  the full crown rate once its delay has passed. On Glenwood Springs the fire
  covers 3,375 acres in 6 h without spotting and 14,199 with it (Park City
  4,812 → 6,821, Vail 1,271 → 1,789; section 3.6). With the spot-fire delay
  at 10 minutes, the top of the ember calibration's range (`crown_d10`),
  Glenwood Springs covers 8,445 acres and its fastest hour falls from 21 %
  to 16 % of the wind, Park City's from 17 % to 12 %; every demo that crowns
  then runs at 7-16 % of the wind in its fastest hour (Santa Rosa 7 %,
  Paradise 8 %, Vail 9 %, Park City and Malibu 12 %, Basalt 13 %, Glenwood
  Springs 16 %), and the grass-start demos move by 0-4 % in area.

### 3.5 Embers: the torching reach and its minimum fire size

`scratch_review/crown_fire_torch_floor.py`: the crown variant's nominal fire
re-solved with the torching floor's minimum fire size at 0, 2 (shipped),
10 ha and none (the SEMANTICS 19 reach alone).

| Scenario | Minimum fire size for the torching reach | Acres 1 h / 3 h / 6 h | Fastest hour | Spot ignitions (6 h) | of which thrown by a one-cell fire | Spot-from-spot chain depth | First spot > 0.5 km (age, fire size) |
|---|---|---|---|---|---|---|---|
| park_city_windstorm | 0 ha | 50 / 735 / 6,825 | 5.4 km/h | 614 | 7 | 5 | 124.3 min, 31.6 ha |
| park_city_windstorm | 2 ha | 50 / 735 / 6,821 | 5.4 km/h | 608 | 1 | 5 | 124.3 min, 31.6 ha |
| park_city_windstorm | 10 ha | 50 / 735 / 6,821 | 5.4 km/h | 605 | 1 | 5 | 124.3 min, 31.6 ha |
| park_city_windstorm | none (no floor) | 50 / 735 / 6,821 | 5.4 km/h | 605 | 1 | 5 | 124.3 min, 31.6 ha |
| paradise_ne_wind | 0 ha | 424 / 1,829 / 5,074 | 4.0 km/h | 404 | 9 | 3 | 15.7 min, 23.5 ha |
| paradise_ne_wind | 2 ha | 424 / 1,829 / 5,068 | 4.0 km/h | 396 | 0 | 3 | 15.7 min, 23.5 ha |
| paradise_ne_wind | 10 ha | 424 / 1,829 / 5,068 | 4.0 km/h | 396 | 0 | 3 | 15.7 min, 23.5 ha |
| paradise_ne_wind | none (no floor) | 424 / 1,829 / 5,072 | 4.0 km/h | 396 | 0 | 3 | 15.7 min, 23.5 ha |

At the demos' city winds (21-24 mph) a single torching tree lofts a brand
about 0.45 km, and the size-scaled reach already exceeds that once the fire
is a few tens of hectares, so the floor changes little: 0-1 % of the 6-h
area. With no minimum (0 ha) lone one-cell spot fires relay torching embers
(7-9 spot ignitions thrown by one-cell fires, against 0-1); 2 ha stops that
(the calibration's target C4) without delaying a young main fire.

### 3.6 Sensitivity of the model's choices

`scratch_review/crown_fire_sensitivity.py` (nominal fire, the `crown` variant
with the calibrated 5-min spot delay, city weather; one change per row;
Aspen's ignition never leaves its cell under any row, so Basalt — an ignition
under aspen that reaches the conifer — stands in for it):

| Scenario | Change | Acres 1 h / 3 h / 6 h | Fastest hour | Crowned at 6 h (active / passive acres) |
|---|---|---|---|---|
| park_city_windstorm | default | 50 / 735 / 6,821 | 5.4 km/h | 1,271 / 10 |
| park_city_windstorm | fmc85 | 50 / 743 / 6,823 | 5.4 km/h | 1,287 / 10 |
| park_city_windstorm | fmc120 | 50 / 735 / 6,787 | 5.4 km/h | 1,257 / 10 |
| park_city_windstorm | seasonal | 50 / 735 / 6,787 | 5.4 km/h | 1,257 / 10 |
| park_city_windstorm | no_shade | 66 / 2,386 / 9,635 | 5.5 km/h | 1,791 / 16 |
| park_city_windstorm | hardwood | 50 / 765 / 7,418 | 5.4 km/h | 1,307 / 965 |
| park_city_windstorm | any_angle | 50 / 783 / 7,180 | 5.2 km/h | 1,293 / 10 |
| park_city_windstorm | spotting_off | 48 / 422 / 4,812 | 2.7 km/h | 1,201 / 8 |
| park_city_windstorm | delay10 | 50 / 536 / 6,119 | 3.9 km/h | 1,249 / 10 |
| park_city_windstorm | delay20 | 50 / 430 / 5,594 | 3.2 km/h | 1,231 / 10 |
| park_city_windstorm | delay30 | 50 / 422 / 5,340 | 3.2 km/h | 1,221 / 10 |
| vail_westerly | default | 330 / 1,013 / 1,789 | 2.9 km/h | 719 / 0 |
| vail_westerly | fmc85 | 356 / 1,067 / 2,230 | 3.6 km/h | 927 / 2 |
| vail_westerly | fmc120 | 2 / 358 / 1,101 | 2.7 km/h | 428 / 0 |
| vail_westerly | seasonal | 2 / 358 / 1,101 | 2.7 km/h | 428 / 0 |
| vail_westerly | no_shade | 550 / 1,247 / 4,281 | 5.8 km/h | 2,082 / 2 |
| vail_westerly | hardwood | 330 / 1,025 / 2,092 | 2.9 km/h | 823 / 6 |
| vail_westerly | any_angle | 338 / 1,019 / 1,825 | 3.1 km/h | 733 / 0 |
| vail_westerly | spotting_off | 252 / 829 / 1,271 | 1.8 km/h | 580 / 0 |
| vail_westerly | delay10 | 284 / 1,021 / 1,857 | 3.2 km/h | 729 / 0 |
| vail_westerly | delay20 | 262 / 991 / 1,677 | 2.3 km/h | 679 / 0 |
| vail_westerly | delay30 | 256 / 971 / 1,619 | 2.1 km/h | 661 / 0 |
| glenwood_springs_westerly | default | 118 / 1,409 / 14,199 | 6.3 km/h | 6,137 / 22 |
| glenwood_springs_westerly | fmc85 | 118 / 1,415 / 14,583 | 6.0 km/h | 6,387 / 22 |
| glenwood_springs_westerly | fmc120 | 118 / 1,381 / 12,802 | 5.9 km/h | 5,452 / 12 |
| glenwood_springs_westerly | seasonal | 118 / 1,381 / 12,802 | 5.9 km/h | 5,452 / 12 |
| glenwood_springs_westerly | no_shade | 192 / 1,889 / 18,278 | 5.7 km/h | 7,494 / 26 |
| glenwood_springs_westerly | hardwood | 118 / 1,409 / 14,203 | 6.3 km/h | 6,137 / 30 |
| glenwood_springs_westerly | any_angle | 122 / 1,501 / 15,860 | 5.7 km/h | 6,811 / 26 |
| glenwood_springs_westerly | spotting_off | 92 / 823 / 3,375 | 1.6 km/h | 632 / 6 |
| glenwood_springs_westerly | delay10 | 102 / 1,103 / 8,445 | 4.8 km/h | 3,693 / 8 |
| glenwood_springs_westerly | delay20 | 96 / 901 / 5,782 | 3.4 km/h | 2,080 / 6 |
| glenwood_springs_westerly | delay30 | 94 / 865 / 4,924 | 3.0 km/h | 1,523 / 6 |
| basalt_carbondale_southwesterly | default | 2 / 759 / 5,576 | 3.2 km/h | 1,167 / 326 |
| basalt_carbondale_southwesterly | fmc85 | 2 / 1,185 / 7,860 | 3.5 km/h | 2,456 / 514 |
| basalt_carbondale_southwesterly | fmc120 | 2 / 4 / 486 | 1.5 km/h | 18 / 16 |
| basalt_carbondale_southwesterly | seasonal | 2 / 4 / 486 | 1.5 km/h | 18 / 16 |
| basalt_carbondale_southwesterly | no_shade | 2 / 759 / 5,598 | 3.2 km/h | 1,175 / 326 |
| basalt_carbondale_southwesterly | hardwood | 2 / 759 / 5,576 | 3.2 km/h | 1,167 / 326 |
| basalt_carbondale_southwesterly | any_angle | 2 / 1,581 / 6,593 | 4.4 km/h | 1,337 / 376 |
| basalt_carbondale_southwesterly | spotting_off | 2 / 663 / 4,784 | 2.5 km/h | 1,047 / 292 |
| basalt_carbondale_southwesterly | delay10 | 2 / 681 / 5,254 | 2.9 km/h | 1,113 / 320 |
| basalt_carbondale_southwesterly | delay20 | 2 / 663 / 4,966 | 2.5 km/h | 1,071 / 306 |
| basalt_carbondale_southwesterly | delay30 | 2 / 663 / 4,864 | 2.5 km/h | 1,049 / 300 |

* **Embers and the spot-fire delay** move the crowning fires most. Without
  spotting the four fires cover 4,812 / 1,271 / 3,375 / 4,784 acres in 6 h
  (Park City / Vail / Glenwood Springs / Basalt) instead of 6,821 / 1,789 /
  14,199 / 5,576, and their fastest hours fall to 8 / 5 / 5 / 11 % of the
  wind: on the real maps the continuous front alone runs below the 10 %
  rule (aspen, meadow, rock and terrain break it up), and spotting makes up
  the difference — at a 5-min delay more than makes it up (Glenwood Springs
  21 %). A delay of 10 / 20 / 30 min gives Glenwood Springs 8,445 / 5,782 /
  4,924 acres and 16 / 11 / 10 % of the wind.
* The **EFFM shading correction**: without it Park City's 6-h area is 41 %
  larger, Vail's 2.4 times, Glenwood Springs' 29 % (Basalt unchanged); it is
  how Cruz et al. define their moisture input, so it stays.
* **Foliar moisture** matters where the fire sits near the initiation
  threshold: at 120 % Vail has 2 acres at 1 h instead of 330 and Basalt 486
  acres at 6 h instead of 5,576, while Park City and Glenwood Springs move by
  0.5-10 %. The simulator's date (21 September) is late season, where the
  seasonal model reads 120 %; the default stays at the US systems' 100 %, the
  fire-favourable side.
* Letting LANDFIRE's **hardwood code** crown adds passive crown fire in the
  aspen and oak (Park City +9 %, Vail +17 % area; Glenwood Springs and
  Basalt unchanged); honouring LANDFIRE's intent is right.
* **Any-angle** spread adds 2-18 % here (Vail +2 %, Park City +5 %, Glenwood
  Springs +12 %, Basalt +18 %); on all 13 scenarios 2-48 % (section 3.4).

### 3.7 Cost

| What | Crown fire off | On |
|---|---|---|
| Park City one-click, nominal field (40,000 cells, 96 h) | 0.63 s | 0.84 s |
| Paradise one-click, nominal field | 0.79 s | 1.12 s |
| Little Giant hindcast domain, one member (300,792 cells) | 1.33 s | 3.10 s (it also burns far more) |

### 3.8 Addendum: the one-click scenarios on the fine-fuel starts (option 2)

Measured after the merge onto main, where founder option 2 had moved the seven
timber-start fires onto the nearest grass or brush patch
(`docs/review/timber_start_resite_2026-10-12.md`). Section 3.4 above used the old
timber starts. Same script and path (`scratch_review/crown_fire_scenarios.py`, the
server's own session, city weather, calibrated embers, 6 h, the crew-safety audit), three
settings: **option 1** (the timber floor; live as simulator image `world11`), **crown 5**
(grass-only floor + crown fire, the calibrated 5-min spot delay) and **crown 10** (the
same with the 10-min delay recommended in section 4). The start label is the ignition
cell's fuel and canopy; percentages are the fastest hour's head gain as a share of the
city weather's mean 10-m wind over 0-6 h (`crown_fire_city_wind.json`). Raw rows:
`scratch_review/crown_fire_finestart_{1,2,3}.json`; table:
`scratch_review/crown_fire_finestart_table.py`.

| Scenario (start) | Acres 1 h / 3 h / 6 h: option 1 (live) | crown, 5-min delay | crown, 10-min delay | Fastest hour, km/h (% of wind): option 1 / crown 5 / crown 10 | First home (h): option 1 / crown 5 / crown 10 | Crew audit (viol / overrun rows) |
|---|---|---|---|---|---|---|
| aspen_southwesterly (SH4, no canopy) | 74 / 1,061 / 4,173 | 116 / 1,263 / 2,946 | 112 / 1,213 / 2,874 | 1.6 (7 %) / 2.3 (10 %) / 2.0 (9 %) | 5.7 / 5.9 / 6.2 | 0 / 0 · 0 / 0 · 0 / 0 |
| basalt_carbondale_southwesterly (GS3, no canopy) | 170 / 1,021 / 3,955 | 200 / 2,826 / 8,124 | 190 / 2,602 / 7,284 | 1.8 (8 %) / 3.5 (16 %) / 2.7 (12 %) | 5.6 / 3.7 / 4.2 | 0 / 0 · 0 / 0 · 0 / 0 |
| beaver_creek_westerly (SH4, no canopy) | 42 / 618 / 2,566 | 244 / 1,631 / 4,395 | 214 / 1,337 / 3,693 | 1.9 (5 %) / 3.0 (9 %) / 2.3 (7 %) | 5.2 / 3.1 / 3.4 | 0 / 0 · 0 / 0 · 0 / 0 |
| boulder_chinook (GR2, no canopy) | 56 / 679 / 1,653 | 26 / 544 / 1,719 | 26 / 498 / 1,701 | 4.2 (9 %) / 3.7 (8 %) / 3.3 (7 %) | 10.1 / 10.1 / 10.1 | 0 / 0 · 0 / 0 · 0 / 0 |
| eagle_gypsum_westerly (GR2, no canopy) | 16 / 86 / 819 | 16 / 86 / 1,231 | 16 / 86 / 1,183 | 2.3 (7 %) / 2.5 (7 %) / 2.5 (7 %) | 0.3 / 0.3 / 0.3 | 0 / 0 · 0 / 0 · 0 / 0 |
| eagle_vail_corridor_westerly (SH4, no canopy) | 59 / 573 / 2,698 | 49 / 109 / 277 | 49 / 109 / 277 | 2.7 (8 %) / 1.2 (3 %) / 1.2 (3 %) | 8.3 / 14.2 / 14.4 | 0 / 0 · 0 / 0 · 0 / 0 |
| glenwood_springs_westerly (SH4, no canopy) | 58 / 905 / 2,032 | 48 / 482 / 9,910 | 42 / 430 / 5,338 | 1.8 (6 %) / 5.5 (18 %) / 4.4 (14 %) | 2.9 / 3.3 / 3.4 | 0 / 0 · 0 / 0 · 0 / 0 |
| malibu_santa_ana (SH5, no canopy) | 488 / 2,592 / 5,536 | 667 / 4,499 / 7,834 | 524 / 3,943 / 7,496 | 5.2 (11 %) / 6.9 (15 %) / 5.7 (12 %) | 2.9 / 2.3 / 2.5 | 0 / 0 · 0 / 0 · 0 / 0 |
| paradise_ne_wind (SH4 under conifer (CBH 0.3 m, CBD 0.05)) | 68 / 761 / 1,942 | 424 / 1,829 / 5,068 | 386 / 1,769 / 4,662 | 2.2 (6 %) / 4.0 (10 %) / 3.3 (8 %) | 3.3 / 1.6 / 1.8 | 0 / 0 · 0 / 0 · 0 / 0 |
| park_city_windstorm (GS1 under aspen / oak (LANDFIRE hardwood code)) | 192 / 2,904 / 6,945 | 110 / 3,447 / 11,061 | 108 / 3,066 / 8,847 | 3.4 (10 %) / 4.7 (14 %) / 3.8 (12 %) | 3.1 / 2.5 / 2.8 | 0 / 0 · 0 / 0 · 0 / 0 |
| rancho_santa_fe_santa_ana (GS2, no canopy) | 144 / 973 / 2,066 | 144 / 1,001 / 2,144 | 142 / 963 / 2,140 | 1.6 (7 %) / 1.6 (7 %) / 1.5 (7 %) | 3.2 / 2.4 / 2.5 | 0 / 0 · 0 / 0 · 0 / 0 |
| santa_rosa_diablo (GS1 under conifer (CBH 8.4 m, CBD 0.03)) | 70 / 789 / 3,925 | 32 / 520 / 2,936 | 32 / 426 / 2,084 | 2.9 (7 %) / 4.4 (11 %) / 2.7 (7 %) | 3.2 / 3.7 / 4.0 | 0 / 0 · 0 / 0 · 0 / 0 |
| vail_westerly (SH4, no canopy) | 86 / 546 / 1,171 | 22 / 114 / 1,497 | 22 / 112 / 1,427 | 1.6 (5 %) / 2.7 (8 %) / 2.2 (7 %) | 7.4 / 7.8 / 8.6 | 0 / 0 · 0 / 0 · 0 / 0 |

* **The moved starts change who crowns.** Aspen and Beaver Creek, which crawled from
  their old timber-litter / aspen-understory starts (2 and 84 acres at 6 h, section 3.4),
  now start in shrub and run into conifer: 2,874 and 3,693 acres with crown 10, of
  which about 1,000-1,200 acres each burned as active crown fire.
* **The Eagle → Vail corridor still crawls**: 277 acres at 6 h under either crown
  setting, against 2,698 under option 1. No acre on its path crowns:
  LANDFIRE maps the stands between its start and the valley as aspen / hardwood, which
  never crown. That is what the canopy data say, not a defect; the demo is dull.
* **Speed against the 10 %-of-wind benchmark.** Crown 10: fastest hours 7-14 % of the
  wind in twelve of thirteen (the corridor 3 %). Crown 5: 7-18 % (Glenwood Springs 18 %,
  Basalt 16 %). Option 1: 5-11 %.
* **Size.** Crown 10 against option 1 at 6 h: larger at Glenwood Springs (2,032 → 5,338),
  Paradise (1,942 → 4,662), Basalt (3,955 → 7,284), Beaver Creek (2,566 → 3,693), Park City
  (6,945 → 8,847), Malibu (5,536 → 7,496), Eagle / Gypsum (819 → 1,183), Vail (1,171 →
  1,427); smaller at Santa Rosa (3,925 → 2,084), Aspen (4,173 → 2,874) and the corridor;
  Boulder and Rancho Santa Fe unchanged.
* **Crew safety.** All 39 runs: 0 departure violations, 0 crew-overrun rows.
* **Founder decision (2026-10-12):** "Switch, 10-min delay" (crown fire armed in the
  simulator with the floor on grass only and a 10-min spot delay; the live engine
  unchanged) and, for the corridor, "Move its start". The corridor now starts 8.8 km out on
  grass-shrub, 800 m beyond its old point, where its fire runs into conifer: 405 / 1,651 /
  5,367 acres at 1 / 3 / 6 h, 1,532 acres crowned (`scratch_review/corridor_resite_solve.py`).
  The live-configuration crew-safety sweep (52 runs): 0 departure violations, 0 overrun
  ticks. `docs/SIMULATOR_DESIGN.md` "Founder option 3: crown fire in the simulator".

## 4. Recommendation

1. **Simulator: arm crown fire with the grass-only floor and the calibrated
   embers; spot-fire delay 10 minutes.** In `SimSession`'s configuration
   block, beside `ros_floor_grass_only = True` (the simulator's setting since
   2026-10-09): `crown_fire = True`, `spot_fire_size = True`,
   `spot_ignition_delay_min = 10.0`, each unless the caller overrides it.
   Nothing else is needed: the 13 standard tiles and the extended tiles carry
   the canopy, and the corridor law already widens around crown fuel.
   * The timber demos that start in conifer burn as forest fires again
     (section 3.4), at 7-16 % of the wind in their fastest hour, the band of
     the forest-fire benchmark (section 3.2), with 0 departure violations and
     0 crew-overrun rows.
   * Crown fire ties a forest's speed to what LANDFIRE maps there; the
     timber floor lifted every timber cell to 3-5 % of the wind, pine stand
     and aspen grove alike.
   * The 10-minute delay. The ember calibration's literature range is
     0-10 minutes (docs/review/ember_spotting_calibration_2026-10-09.md). The
     crown rate (Cruz et al. 2005) was fitted to fires whose short-range
     spotting is part of their measured speed, so a spot fire that runs at
     that full rate 5 minutes after landing counts some of it twice; and a
     fire lit at a point accelerates — the Canadian FBP System's
     acceleration (ST-X-3: rate × (1 − e^(−0.115 t)), t in minutes) leaves
     a steady fire about 1/0.115 ≈ 9 minutes behind one born at full speed.
     With 10 minutes the crowning demos' fastest hours fall from 9-21 % to
     7-16 % of the wind; the grass-start demos move by 0-4 % in area. If the
     calibrated 5 minutes is kept, crown fire still works; its fires are
     larger (section 3.4, `crown`).
   * Foliar moisture stays at 100 % (the US systems' default, the fire-
     favourable side). The seasonal option reads 120 % on the simulator's
     date (21 September) and shrinks the fires that start near the
     initiation threshold (Basalt 486 acres instead of 5,576; section 3.6).
2. **Re-site three one-click ignitions** (Beaver Creek, Eagle–Vail, Aspen)
   onto conifer cells (crown fuel with a low canopy base) upwind of town.
   Under the grass-only floor their fires crawl with or without crown fire
   (84 / 287 / 2 acres in 6 h). The scenario definitions belong to the
   simulator work and are not changed here.
3. **Live engine: crown fire off and the timber floor as deployed, until a
   shadow run says otherwise.** On the 11 real fires grass-only + crown
   reproduces the deployed engine's pooled skill (3.50× against 3.55×) and
   P10 adequacy (99.78 % both), but its P10 tail is worse: three forecast
   days under 90 % (82 % worst) against none under 94 %, in timber litter
   that does not initiate and in passive crown fire slower than the floor
   (section 3.3). Next: run `watch --grass-only --crown-fire` in shadow on
   live fires next to the deployed posture, and look at the tail with the
   fire belief (docs/review/belief_vs_physics_2026-09-27.md) before any
   switch. `watch --crown-fire` fetches one extra LFPS job per domain
   (cached, mirrored like the terrain).
4. **Never arm crown fire on top of the timber floor.** It double counts:
   1.9-18 times option 1's 6-h area in the timber demos (Glenwood Springs
   22,984 acres, Vail 15,414), 4.01× the real fires' area pooled (7.80× on
   growing days), and the only crew-overrun rows of the study.
5. **The knob stays OFF everywhere in this change** (`ResponseConfig.
   crown_fire = False`; the simulator does not arm it; `watch` arms it only
   with `--crown-fire`). With it off the engine is byte-identical, with or
   without canopy on the tiles.

## 5. Limits

* **Two benchmarks disagree, and the model sits with the faster one.** On
  uniform conifer tiles at 18-40 mi/h the crown fire runs at 8-15 % of the
  10-m wind where the canopy shades the litter (the 10 %-of-wind rule
  ± 50 %), up to 20 % in open stands at the "extreme" preset's 1.4 % fuel
  moisture; on the real maps it runs at 9-17 % at the generic presets, and
  the demos' fastest hours sit at 7-16 % with the 10-min spot delay. The
  eight northern-Rockies crown runs Rothermel (1991) documented averaged
  2.4-5.8 % of the wind, and the Cruz
  model runs 1.3-5.5 times faster than those averages (geometric mean 3.3×;
  1.7× their observed maxima). Their canopy density is not published, the
  winds are the upper end of reported ranges, and the averages include
  hours of slower burning; the Cruz model's independent test on 57
  wildfire runs (Alexander & Cruz 2006) found a mean absolute error of
  51-60 %. Treat any single crown run's speed as uncertain by a factor of
  about 2.
* **The real-fire hindcast is not where crown fire helps.** The 11 fires were
  mostly suppressed timber fires at moderate winds; the engine models no
  suppression and already over-forecast them. With the grass-only floor,
  crown fire restores the deployed engine's average but not its worst day
  (section 3.3).
* **Spotting and the crown rate overlap.** The Cruz rate contains the
  short-range spotting of the fires it was fitted to, and the engine's spot
  fires add their own at the full crown rate after the spot delay, with no
  build-up. The recommended 10-min delay is a calibration inside the
  literature's range (against the 10 %-of-wind benchmark), not a model of a
  spot fire's growth.
* **No slope term in the crown rate.** The Cruz model is for level terrain
  (CFIS says so); a crown run up a steep slope gets the flat-ground crown
  rate, while its surface fire keeps the slope effect.
* **The surface wind under the canopy is the engine's unsheltered wind.**
  FlamMap and FARSITE shelter it (a wind adjustment from canopy cover and
  height), which with Rothermel's 1991 crown model is part of the US systems'
  documented under-prediction (Cruz & Alexander 2010). Here the crown
  initiates more readily than it would in FlamMap; LANDFIRE's canopy fuels
  were built for the US systems.
* **Per cell, at steady state.** Each 90 m square crowns or not by the
  threshold, with no acceleration from an ignition point, no transition
  zone, no plume-dominated fire and no long-range plume spotting (the
  5-16 km Camp and Dixie spot fires). A crowning cell throws one ember, as
  every cell does (SEMANTICS 11); the torching reach is one tree of the
  stand height with a generic species and diameter.
* **Data.** LF2024 canopy, so stands changed since (recent burns, beetle
  kill, treatments) are not reflected; resampled nearest-neighbour from 30 m
  to the tile grid. Foliar moisture defaults to 100 %; the seasonal option is
  a Canadian model. The passive crown fraction burned (FARSITE's) only sets
  the crown fire's intensity, i.e. its ember distances.

## 6. Reproduction

| Script | What |
|---|---|
| `py -3 tools/fetch_sim_tiles.py --canopy [--only KEY] [--extended]` | the LF2024 canopy onto the committed tiles (`data/tiles/provenance_canopy.json`) |
| `scratch_review/crown_fire_hindcast_canopy.py copy / fetch / summary` | the canopy for the 11 hindcast domains |
| `scratch_review/crown_fire_uniform.py r91 / wind` | sections 3.1 and 3.2 (`crown_fire_uniform.json`) |
| `scratch_review/crown_fire_scenarios.py all VARIANTS [--generic]` | sections 3.2 (real maps) and 3.4 (`crown_fire_scenarios.json`, `crown_fire_scenarios_generic.json`) |
| `scratch_review/crown_fire_hindcast.py verify / run / collect` | section 3.3 (`crown_fire_hindcast.json`; stored deployed scores read from the 2026-09-27 study's cache, env `BVP_LEGACY_RUNS`) |
| `scratch_review/crown_fire_torch_floor.py` | section 3.5 (`crown_fire_torch_floor.json`) |
| `scratch_review/crown_fire_sensitivity.py [SCENARIOS [ROWS]] [--out PATH]`, `--merge PATH...` | section 3.6 (`crown_fire_sensitivity.json`) |
| `scratch_review/crown_fire_city_wind.py` | the city weather's 10-m wind per scenario, 0-6 h (`crown_fire_city_wind.json`) |
| `scratch_review/crown_fire_laws_real.py` | the engine laws on the real tiles (`crown_fire_laws_real.json`) |
| `scratch_review/crown_fire_overrun_probe.py SCENARIO VARIANT` | the dissection of a crew-overrun row |
| `scratch_review/crown_fire_cost.py` | section 3.7 (`crown_fire_cost.json`) |
| `scratch_review/crown_fire_tables.py` | the tables of this review |

## Sources

* Albini, F.A. 1979. *Spot fire distance from burning trees — a predictive model.* USDA FS Gen. Tech. Rep. INT-56. Implemented as in BehavePlus: firelab/behave `spot.cpp`, `calculateSpottingDistanceFromTorchingTrees` (species flame height / duration coefficients, firebrand height factors, critical cover height, flat-terrain distance). [GitHub](https://github.com/firelab/behave)
* Alexander, M.E., Cruz, M.G. 2006. Evaluating a model for predicting active crown fire rate of spread using wildfire observations. *Can. J. For. Res.* 36: 3015-3028 (57 wildfire runs; 42 % within ±25 %; mean absolute percent error 51 % Canada, 60 % US). [FRAMES](https://www.frames.gov/catalog/3657)
* Alexander, M.E., Cruz, M.G., Lopes, A.M.G. 2006. CFIS: a software tool for simulating crown fire initiation and spread. *V Int. Conf. Forest Fire Research.* [PDF](https://www.frames.gov/documents/catalog/alexander_et_al_2006.pdf)
* Alexander, M.E., Cruz, M.G. 2014. Energy release rates, flame dimensions, and spotting characteristics of crown fires. *Fire Management Today* 73(4) (cited by the 2026-10-09 ember calibration for the spot-fire ignition delay, up to 5-10 min).
* Andrews, P.L. 1986. *BEHAVE: fire behavior prediction and fuel modeling system — BURN subsystem, part 1.* USDA FS Gen. Tech. Rep. INT-194 (the linear length-to-width law Rothermel 1991 adopted).
* Anderson, H.E. 1982. *Aids to determining fuel models for estimating fire behavior.* USDA FS Gen. Tech. Rep. INT-122 (fuel model 10).
* Cruz, M.G., Alexander, M.E. 2010. Assessing crown fire potential in coniferous forests of western North America: a critique of current approaches and recent simulation studies. *Int. J. Wildland Fire* 19: 377-398 (the US systems' under-prediction bias). [FRAMES](https://www.frames.gov/catalog/8109)
* Cruz, M.G., Alexander, M.E. 2019. The 10 % wind speed rule of thumb for estimating a wildfire's forward rate of spread in forests and shrublands. *Annals of Forest Science* 76: 44.
* Cruz, M.G., Alexander, M.E., Wakimoto, R.H. 2004. Modeling the likelihood of crown fire occurrence in conifer forest stands. *Forest Science* 50: 640-658.
* Cruz, M.G., Alexander, M.E., Wakimoto, R.H. 2005. Development and testing of models for predicting crown fire rate of spread in conifer forest stands. *Can. J. For. Res.* 35: 1626-1639. Coefficients as coded in CFIS and in the R package firebehavioR (`cfis`: `crosa <- 11.02 * u10^0.9 * cbd^0.19 * exp(-0.17 * effm)`, `crosp <- crosa * exp(-crosa * cbd / 3)`). [FRAMES](https://www.frames.gov/catalog/3832), [firebehavioR](https://search.r-project.org/CRAN/refmans/firebehavioR/html/cfis.html)
* ELMFIRE technical reference (crown fire: Van Wagner initiation, the Cruz et al. 2005 rate of spread, R0 = 3.0 / CBD, passive × exp(−CAC)). [elmfire.io](https://elmfire.io/tech_ref.html)
* Finney, M.A. 1998. *FARSITE: Fire Area Simulator — model development and evaluation.* USDA FS Res. Pap. RMRS-RP-4 (the crown fraction burned coefficient a_c = −ln(0.1) / (0.9 (R'_A − R_o)); as coded in firebehavioR `cfb.farsite`).
* Forestry Canada Fire Danger Group 1992. *Development and structure of the Canadian Forest Fire Behavior Prediction System.* Inf. Rep. ST-X-3 (foliar moisture eqs. 1-8, coded in cffdrs `foliar_moisture_content`; the point-ignition acceleration ROS(t) = ROS (1 − e^(−αt)), α = 0.115 /min in open stands, coded in cffdrs `ROStcalc` / `DISTtcalc`). [cffdrs](https://github.com/cran/cffdrs)
* LANDFIRE. Forest Canopy Base Height / Bulk Density / Cover / Height (LF2024), via the LANDFIRE Product Service (lfps.usgs.gov); hardwood stands coded CBH 10 m / CBD 0.01 "to prevent false simulation of crown fire". [landfire.gov](https://landfire.gov/fuel/cbh)
* NWCG Incident Response Pocket Guide / Rothermel (1983) fine dead fuel moisture tables (reference moisture; May–July corrections, exposed vs ≥ 50 % shaded). [Kestrel reproduction](https://kestrelinstruments.com/mwdownloads/download/link/id/251)
* Rothermel, R.C. 1983. *How to predict the spread and intensity of forest and range fires.* USDA FS Gen. Tech. Rep. INT-143.
* Rothermel, R.C. 1991. *Predicting behavior and size of crown fires in the Northern Rocky Mountains.* USDA FS Res. Pap. INT-438 (crown ROS = 3.34 × fuel model 10 with a 0.4 wind reduction; table 1, eight wind-driven runs; eq. 10, L/W = 1 + 0.125 U20). [PDF](https://www.fs.usda.gov/rm/pubs_int/int_rp438.pdf)
* Scott, J.H., Reinhardt, E.D. 2001. *Assessing crown fire potential by linking models of surface and crown fire behavior.* USDA FS Res. Pap. RMRS-RP-29 (foliar moisture default 100 %).
* Van Wagner, C.E. 1977. Conditions for the start and spread of crown fire. *Can. J. For. Res.* 7: 23-34.
* Van Wagner, C.E. 1993. Prediction of crown fire behavior in two stands of jack pine. *Can. J. For. Res.* 23: 442-449.
* Earlier reviews this builds on: `docs/review/park_city_speed_2026-10-09.md`, `docs/review/ember_spotting_calibration_2026-10-09.md`, `docs/review/belief_vs_physics_2026-09-27.md`, `docs/review/grid_orientation_2026-10-09.md`.
