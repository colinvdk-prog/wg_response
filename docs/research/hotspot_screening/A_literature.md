# Screening NASA FIRMS VIIRS hotspots into "potential fires"

**Researcher A: remote-sensing science and how operational systems vet hotspots**
Prepared 2026-10-29. Read-only research; nothing under `C:\Users\colin\wg_response` was touched.

Evidence labels: **[S#]** = published or online source (URLs in section 7.1). **[M#]** = my own measurement on data pulled today (method and caveats in section 7.2). "Unexplained" below means: not FIRMS-static, not inside a WFIGS wildfire perimeter, and not within 3 km of a WFIGS wildfire or prescribed-fire (RX) incident point. It is **not** a synonym for "false alarm": it contains agricultural burns, private prescribed fire, escaped burns and unreported small wildfires.

---

## 0. Findings in ten lines

1. A VIIRS 375 m detection means "a pixel hotter than its surroundings in the 3.7 and 11 um bands". The product team's published false-alarm figures (nominal confidence under 1.2% in the worst region, 0.03% for global daytime urban false alarms, night negligible outside the South Atlantic anomaly) count **sensor artifacts only**; real heat that is not a wildfire (flares, mills, volcanoes, ag and prescribed fire) is outside that statistic [S2].
2. Measured composition of CONUS detections (S-NPP, standard processing, 2024, 468,103 detections) [M2, M3]: FIRMS-static 16.5%, reported wildfire 31.6%, registered prescribed burn 3.3%, unexplained 48.7%. By month the split swings from 66% reported wildfire in July to 4% in November (October: static 16 / wildfire 33 / RX 1 / unexplained 50 percent; November 21 / 4 / 4 / 71). The live probe of 28-29 Oct 2026 agrees: 0.6% of detections within 5 km of a WFIGS wildfire point versus 6.7% within 5 km of an open RX point [M1].
3. Detection-level properties **cannot** separate future large fires from background heat on the first look. At 259 large 2024 wildfires (2,000+ acres) the first S-NPP/NOAA-20 look had a single pixel for 12%, at most 3 pixels for 33%, maximum FRP under 10 MW for 43% (under 3 MW for 16%), and happened at night for 49% [M4]. Sep-Nov 2024 "unexplained" episodes: 55% single-detection, 80% maximum FRP under 10 MW, 85% seen on one day only [M5]. Hard floors on confidence, FRP, pixel count or number of days would delay real fires.
4. Only two filters have a clean benefit/risk ratio: **history-based static-source suppression** (a cell with 10+ detection-days in the preceding six months flags 12% of later detections, and flagged **0 of 124,608** detections inside 2024 wildfire perimeters of 2,000+ acres [M2]) and **over-water pixels**. Both should keep a log and a re-arm guard.
5. Controlled-burn context (cropland plus season plus daytime-only, WFIGS RX registry, CARB and air-district burn-day data) can lower an alert **ceiling** but must never delete a detection: escapes are the failure mode.
6. GOES ABI fire detection (FDC) is the fast, free confirmer: the 2024 Park Fire (Chico) was flagged in the GOES scan that started 3.6 min after the recorded discovery time, 12.5 h before the first VIIRS pixel (S-NPP had no CONUS data that week) [M6]. But FDC confirms only 18.8% of VIIRS detections (0% at night, 1.8% of singleton clusters, 77% of detections of 30+ MW) [M7]. It is an **upgrade** signal, never a requirement.
7. Nobody in operations auto-vets hotspots to "confirmed". NOAA's HMS uses human analysts (adds 1-3 h and normally removes industrial and urban anomalies) [S25]; FIRMS and DEA Hotspots publish detections without static-source removal, and no industrial-removal step is documented for GWIS or EFFIS [S7, S28, S48]; Watch Duty and CAL FIRE start from 911 traffic, cameras and on-scene reports [S26, S27]. "Confirmed" should mean agency- or human-corroborated.
8. Feed changes that matter: S-NPP stops on 2026-11-02 13:00 UTC [S10], leaving NOAA-20 and NOAA-21 about 50 minutes apart and about 12 h between day and night windows; S-NPP also returned zero CONUS detections on 2024-07-24 to 07-28 [M2]. NOAA-20 saturates the I4 band about 4 times less often than S-NPP or NOAA-21 (2.4% versus 9.8% and 12.4% of daytime detections) and NOAA-21 FRP runs about 1.4x S-NPP on matched pixels [M1], so thresholds and the "high" class are not comparable across satellites.
9. FIRMS NRT rows carry no `type` field and the FIRMS Static Thermal Anomalies mask is "not available for distribution" [S7, S4]. A static mask has to be built in-house from the standard-processing (SP) archive, which is free (about 31-44 MB per satellite-year for the US) [S42].
10. Suggested structure (section 8): **H0 heat-only / P1 potential fire / P2 probable fire / CF confirmed fire**, with alert ceilings per tier, a proximity escalator to human triage, and expiry only when observability is shown.

---

## 1. VIIRS 375 m: what the algorithm does and what that means for screening

### 1.1 Detection logic
- Contextual algorithm built on the MODIS heritage; driven by I4 (3.74 um) and I5 (11.45 um) [S1, S2]. Candidate pixel: day BT4 > 325 K and BT4-BT5 > 25 K; night BT4 > 295 K and BT4-BT5 > 10 K, then background tests (mean and absolute-deviation windows of 10-35 pixels) [S1 Table 2].
- I4 saturates at 367 K; at 375 m this happens often (8% of daytime fire pixels in the 2013 evaluation) [S2]. In today's feed every `h` pixel has `bright_ti4` = 367.0 K [M1], i.e. the **high class is a saturation flag, not a probability**.
- The algorithm was designed to find small and cool fires. Simulation: a 1.25 m-radius bonfire at about 1000 K (about 5 m2) produced a +10 K I4 anomaly at night and satisfied all night tests; daytime detection probability is lower for the same heat source [S2]. Practical consequence: **night detections include very small heat sources** (median night FRP 1.2 MW versus 7.4 MW by day in the probe [M1]).
- Daytime false-alarm defences: glint test (glint angle < 15 degrees with reflectance sum > 0.35, or < 25 degrees with > 0.40), bright-desert-boundary rejection, and a 3x3 stand-alone test [S2, S3]. Sources of residual daytime false alarms named by the authors: metallic rooftops in industrial parks, bright pavement, specular water bodies [S2].
- Over water, a **persistence test** is built in: a water pixel without an M13 signature must have at least 3 co-located detections in the previous 30 days, else it is dropped [S3]. The swath QA bits list a persistence test only for water pixels; static land sources are labelled in the monthly product's `Type` field, whose derivation the guide does not describe [S1, S3].

### 1.2 Confidence classes (what `l`, `n`, `h` mean)
| Class | Official meaning | What it means for screening (and what I measured) |
|---|---|---|
| `l` low | Daytime pixels typically near sun glint, or with a weak (< 15 K) I4 anomaly; includes lone pixels whose I4 is under 15 K above every neighbour [S1, S2] | **Not "probably false".** In the probe 263 of 7,287 detections (3.6%) are `l`, all daytime; their FRP is *higher* than nominal-day (median 16.8 vs 7.4 MW) and 58% exceed 10 MW; only 16 of 263 (6%) have no nominal/high pixel within 1 km and 12 h [M1]. Schroeder et al. likewise found low-confidence pixels in southern Africa sit on the edges of nominal clusters, i.e. real low-intensity fire [S2]. Share rises at the swath edge: 9.5% of detections with scan >= 0.65 km are `l` versus 2-4% inward [M1]. |
| `n` nominal | No glint, strong (> 15 K) anomaly day or night [S1] | 92% of detections [M1]. |
| `h` high | Saturated pixel (nominal saturation or DN folding), day or night [S1] | A *brightness-saturation* flag; 306 of 312 are daytime [M1]. NOAA-20 produces far fewer (section 1.9). |

Further facts: low-confidence pixels over water and in the South Atlantic anomaly are re-labelled as land/water and **removed from the product** in Collection 2, so they never reach FIRMS [S1]. The anomaly region is 7N-55S, 11E-110W, so it cannot affect CONUS [S4]. **At night there is no `l` class** (0 of 3,142 night detections in the probe; 0 of 227,641 night detections in the 2024 SP file) [M1, M2], consistent with an independent preprint [S43]. FEDS (NASA/UCI near-real-time fire tracker) uses only nominal and high detections [S23].

### 1.3 Day versus night
- The 375 m product finds about 3x more daytime and about 17x more night-time fire pixels than the 750 m product (Collection 2 global 2021); 85% of the night 375 m pixels have no 750 m match, and a slightly larger share of those are non-vegetation fire (19% vs 13%) [S1]. So **night-time 375 m detections are where static industrial heat concentrates**: in the 2024 US file, FIRMS `type` 2 (other static land source) is 28% of night detections versus 2.8% of day detections [M2].
- Wildfires persist at night; agricultural and prescribed fire mostly do not: MODIS CONUS 2003-2020, 94% of night-time FRP was inside wildfires (95% of that in fires over 2,023 ha), and night-time FRP proportion outside wildfires was only 3% under wet fuels [S22]. With VIIRS 375 m the night channel is far more sensitive, so night detection is a **supporting** signal, not a wildfire test (33% of "unexplained" Sep-Nov 2024 episodes have a night detection versus 65% of reported-wildfire episodes) [M5].

### 1.4 Pixel size and swath edge
- Footprint grows from 383 x 360 m at nadir to 795 x 784 m at the maximum scan angle (56.28 degrees): area 0.14 km2 to 0.62 km2 (4.5x). Aggregation zones: 3:1 to 31.7 degrees, 2:1 to 44.9 degrees, none beyond; bow-tie deletion removes about half the redundancy, but residual duplicate pixels remain at the edge [S2, S1].
- In the probe, 8.8% of detections have `scan` > 0.6 km; median pixel area 0.205 km2, p90 0.35 km2, max 0.64 km2 [M1]. `scan` and `track` give the real pixel dimensions, so **count area (scan x track), not pixels, when judging cluster size**, and buffer home matches by the footprint.
- Geolocation: nominal error under 100 m, but several km after spacecraft manoeuvres or space weather [S7]. Night-time plume detections of large explosive fires can be displaced beyond the perimeter, away from nadir [S1, S7].

### 1.5 FRP
- Computed from the coincident 750 m M13 radiance and split among the 375 m fire pixels inside that 750 m pixel (so neighbouring pixels share the same FRP); can be 0/null for rare M13 saturation or poor background [S1]. In the probe, `h` day FRP median 19 MW, `n` day 7.4, `n` night 1.2 (p90 3.0) [M1].

### 1.6 Product classes: URT, RT, NRT, SP
| Class | Latency and coverage | Properties relevant to screening |
|---|---|---|
| URT | About 50 s from observation; CONUS, Puerto Rico, Hawaii direct-broadcast antennas; merged and de-duplicated at UW-SSEC; same algorithm as LANCE [S5] | Replaced by NRT after processing or after 6 h [S5, S6]. My probe saw `version` strings 2.0URT (S-NPP, NOAA-21) and 2.1URT (NOAA-20) with the newest NOAA-20 pixel 1-2 min old [M1]. FIRMS says it filters anomalies in real time (for example linear streaks after lunar calibration) and these "can briefly appear and be removed" [S7], so **a single URT pixel can be retracted**. |
| RT | 20-30 min per the URT article, "within 60 minutes" per the API page [S5, S6] | Same replacement rule. |
| NRT | Within about 3 h, best effort; "possible coverage gaps" [S1, S7] | No `type` field [S4]. |
| SP / monthly | Standard processing; monthly VNP14IMGML (S-NPP), VJ114IMGML (NOAA-20), VJ214IMGML (NOAA-21) are posted 1-2 months after month end on the UMD SFTP; FIRMS country-year CSVs lag about 9-10 months (2025 files dated 2026-10-10) [S1, S42] | **Has `type`** (0 vegetation fire, 1 volcano, 2 other static land source, 3 offshore); a bug mislabelled static sources as type 0 in versions 1-2, fixed May 2025 (version 3) [S1]. NOAA-21 country-year files were not found at the usual path (404 for 2023-2025) [M2]. |

Area API: sources `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT` (+ `_SP`), `MODIS_NRT/SP`, `LANDSAT_NRT` (US/Canada); 1-5 days per call; 5,000 transactions per 10 min; free MAP_KEY [S6]. The engine must keep its own history (the API only returns 5 days).

### 1.7 Published error rates, and what they do and do not cover
| Study | Result | Caveat |
|---|---|---|
| Schroeder et al. 2014 (global, Aug 2013) [S2] | Commission error < 1.2% for nominal confidence (worst region: eastern China, industrial rooftops); 0.03% for daytime nominal urban false alarms globally; night-time commission negligible outside the South Atlantic anomaly. Low-confidence daytime pixels were 11% of daytime detections, about 6% of them urban rooftops, and up to about 40% were false in eastern China. | Counts non-thermal artifacts. States that night detections coincide with "active volcanoes, gas flares, iron mills" or biomass burning, i.e. real heat. |
| VIIRS C2 user guide FAQ [S1] | 13-19% of night 375 m pixels are in the non-vegetation-fire category. | Global. |
| Landsat-8 reference over Indonesian peat (Int. J. Digital Earth 2020, via search summary) [S44] | Daytime commission about 35% single-time, 16% with cluster buffering. | Peat region; not CONUS. |
| Gao et al. 2026 (Remote Sens. 18:904) [S20] | In a stratified high-interference sample, false-positive rate 88.6% in industrial/urban clusters (22.7% after a persistence/stability prior) and 100% over volcanoes (57.3%); forest/grassland fires F1 about 0.999 retained; crop-residue omission 11.2% to 1.3%. | Stratified to hard cases; East Asia. |
| Sofan et al. 2022 (Indonesia) [S21] | A zone map of permanent features removed about 13% of VIIRS active fires as non-biomass-burning. | Indonesia. |
| Li et al. 2020 (SE US, prescribed fire records) [S18] | VIIRS 375 m detected only 25% of Georgia planned burns over 100 acres (ABI 29%). | **Omission, not commission**: VIIRS misses most low-intensity planned burns. |
| This work (CONUS 2024, S-NPP) [M2, M3] | FIRMS-static 15.2% of detections (28% at night); only 31.6% of detections are on reported wildfires; 49% unexplained. | Not a false-alarm rate. |

### 1.8 Artifacts a live feed should expect
- Plume/parallax detections beyond a large fire's perimeter at night and high scan angle; HMS analysts correct these by hand [S1, S25].
- Lunar-calibration streaks and other anomalies filtered in near real time, occasionally visible for minutes [S7].
- Residual bow-tie duplicates at the swath edge [S1].
- Coverage gaps: S-NPP returned **no** CONUS detections 2024-07-24 to 07-28 (while NOAA-20 returned 3,744 to 6,666 per day) and four days in 2024 (01-24, 02-16, 04-01, 11-28) fell below 25% of the rolling median for both satellites [M2]. FIRMS warns of "temporary interruptions in the NRT data processing chain" [S1].
- FIRMS removes detections it deems anomalous; the engine's de-duplication key must not include `version`.

### 1.9 Satellite-to-satellite differences that affect thresholds (measured, indicative)
From the 28-29 Oct 2026 probe, daytime only [M1]:

| | S-NPP | NOAA-20 | NOAA-21 |
|---|---|---|---|
| Day detections with I4 = 367 K (saturated, class `h`) | 9.8% | 2.4% | 12.4% |
| Same fire pixel pairs (within 400 m, 1.2 h): share saturated | 10.5% (vs N20 4.3%) | 4.1-4.3% | 12-15% |
| Day `bright_ti4` median / p90 (K) | 340.5 / 357.7 | 336.5 / 351.2 | 339.7 / 367.0 |
| Matched-pair FRP ratio (n pairs) | 1.00 | 1.11 vs S-NPP (794) | 1.37 vs S-NPP (614) |

NOAA-20's `h` class (1.8% of day detections) is a much rarer event than S-NPP's (9.1%) or NOAA-21's (11.8%), and the effect shows in both NRT and URT versions. Use FRP and `bright_ti4` with per-satellite calibration; do not treat `h` as a cross-satellite severity.

---

## 2. Persistent and static heat sources

### 2.1 What they are and how common they are in CONUS
- Typical CONUS static or quasi-static VIIRS sources: oil and gas flares, refineries and petrochemical plants, steel and coke works, smelters and mines, power plants (including biomass), landfills, geothermal fields, plus daytime glint from solar farms and reflective roofs [S2, S14, S25]. No active volcano (`type` 1) appears in CONUS in the 2024 file (686 type-1 rows, all outside CONUS) [M2].
- Measured, 2024 S-NPP standard processing, CONUS [M2]: `type` 2 (other static land source) is **15.2%** of detections and `type` 3 (offshore) 1.3%; type 2 is 28% of night detections versus 2.8% of day detections. Counting distinct detection-days per 0.004 degree cell (about 440 m): 2,101 cells have 10 or more detection-days and hold **17.7%** of all detections (89% of them at night); 92.5% of all type-2 detections fall in those cells. Seasonal behaviour: 347 of those 2,101 cells (17%) have month-of-year entropy under 0.6, i.e. are seasonal burners rather than year-round sources.
- Two-day probe, 28-29 Oct 2026: 153 cells with 4+ detections across both days hold 10.6% of detections [M1]. Examples (approximate locations): Cleveland OH 41.452N 81.676W, Mobile AL area 30.94N 88.01W, Pittsburgh area 40.39N 79.86W, Soda Springs ID 42.685N 111.592W, Linden NJ 40.636N 74.220W [M1]. Some of the "persistent" cells in a 2-day window are real fires (for example 42.316N 120.520W, 1.2 km from a registered RX incident) which is why a 2-day proxy is **not** safe and a 6-12 month history is.

### 2.2 Data sources that can feed a mask
| Source | What it gives | Format / access | Cadence | Cost and licence | Verdict |
|---|---|---|---|---|---|
| FIRMS SP country-year CSV (S-NPP `viirs-snpp`, NOAA-20 `viirs-jpss1`, `modis`) [S42] | Every detection with `type` 0/1/2/3 | `https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2024/viirs-snpp_2024_United_States.csv` (38.7 MB); NOAA-20 2024 43.8 MB; 2025 files 37.5 / 39.7 MB (posted 2026-10-10); no key | Yearly, about 9-10 months lag; NOAA-21 not at this path | Free | **Primary source for history and for `type`.** |
| UMD monthly VNP14IMGML / VJ114IMGML / VJ214IMGML [S1] | Same plus `PixArea`, glint angle, `Type` (version 3 after May 2025 fix) | SFTP (guest login printed in the user guide) | 1-2 months after month end | Free | Fills the lag; only version 3 is trustworthy for `Type` [S1]. |
| Local accumulation of the NRT feed | Detection-days per cell, night share, FRP percentiles | Own database | Real time | Free | **Required**: the Area API returns only 5 days [S6]. |
| FIRMS Static Thermal Anomalies mask [S8, S9, S7] | Provisional polygons from 2023 MODIS and S-NPP detections (400 m grid, 5+ detections), filtered against industrial and power-plant inventories | FIRMS map viewer, "Advanced Mode" | "Further refined" (no schedule) | "Reference only"; "not available for distribution" [S7] | Cannot be consumed; use only to sanity-check your own mask. |
| NOAA/EOG VIIRS Nightfire (VNF) v4.0 [S38] | Nightly high-temperature source detections (flares), annual flare catalogue; S-NPP, NOAA-20, NOAA-21 | CSV (gzip), ezCSV, KMZ; registration | Nightly | **Licensed since 2025-01-10**: free one-year academic licence; commercial use is a paid one-year subscription, price not published (eog@mines.edu) [S38] | Excellent for flares but adds licence cost and the same sources recur in the SP history; low priority. |
| Global Industrial Heat Sources, GIHS (Sci. Data 2024) [S39] | 25,544 industrial heat-source polygons, 2012-2021, user accuracy 90.95-93.46% | Shapefile, Zenodo 10.5281/zenodo.8308133 | Static | Open | Seed list for new-source onset and for the facility-proximity prior. Includes closed plants; authors warn that biomass and straw burning in North America can be misclassified [S39]. |
| USGS/LBNL US Large-Scale Solar PV Database v4.0 (Apr 2026) [S40] | 6,611 facilities of 1 MW and up, hand-drawn polygons | GeoJSON about 2 MB, shapefile about 7 MB zipped | Annual (lags installs by over a year) | Open | Mask for daytime glint (lone `l` pixels, GOES false alarms near solar facilities [S14]). |
| EPA Envirofacts / GHGRP facilities (lat, lon, NAICS) and WRI Global Power Plant DB [S41, S8] | Facility points | REST (JSON/CSV), no key (EPA) | Annual | Open | Cheap facility prior. Not verified end-to-end. |

### 2.3 A recipe that I tested (and the results)
Rule tested: grid 0.004 degrees; count **distinct UTC detection-days per cell**; call a cell *static* when it has 10 or more detection-days in the trailing 365 days (or 5+ in the trailing 180 days), is active in 3+ calendar months, and had a hit in the last 180 days (otherwise "dormant"). Published analogues: persistent-fire mask PFM (10+ hit-days in a year, active in 2 of 3 years, month-entropy 0.6 or more) plus a short-term heat-source mask (stable night anomaly in a rolling 14 days) [S20]; FIRMS STA (5+ detections a year on a 400 m grid plus inventory filter) [S8]; FEDS near-real-time (a fire under 20 km2 with more than 20 fire pixels per km2 accumulated since ignition is labelled static) [S23].

Forward test [M2], S-NPP 2024, cells defined from January-June and applied to July-December CONUS detections (n = 292,529):

| Hit-days in Jan-Jun | Share of Jul-Dec detections flagged | Of flagged, FIRMS `type` 2 | Flagged inside 2,000+ acre wildfire perimeters (n = 124,608 time-valid detections) | Earliest pixel of 203 large fires flagged |
|---|---|---|---|---|
| >= 3 | 15.9% | 82% | 52 (0.04%) | 1.0% |
| >= 5 | 14.5% | 85% | 5 | 0% |
| >= 10 | 12.2% | 88% | **0** | 0% |
| >= 20 | 8.6% | 89% | 0 | 0% |
| >= 30 | 6.5% | 90% | 0 | 0% |

Reading: a six-month history of 5-10 detection-days removes 12-15% of later detections at essentially zero cost on large wildfires, which are the fires that matter for a 60 km forecast. Caveat: 2,000-acre wildfire perimeters are the easy case; the dangerous case is a **small** fire that starts next to a static source (below).

### 2.4 Failure modes and guards
1. **Ignition at or beside a static site** (flare ignites grass, landfill or power-plant fire spreads to wildland): the 440 m cell mask would hide the first pixel. Guard: un-flag (re-arm to P1) when two or more non-static pixels appear within 1.5 km in the same overpass, when FRP exceeds max(15 MW, 3x the cell's historical p95), or when `bright_ti4` saturates where the cell never saturated.
2. **Long-lived "controlled" heat that is not controlled.** The 2022 Hermits Peak and Calf Canyon fires (New Mexico, 341,000 acres, 900+ structures) began from an escaped Forest Service prescribed burn and a January pile fire that smouldered for months before resurfacing [S45]. A mask or label that *silences* persistent registered heat would have hidden it. Guard: persistence + registration lower the ceiling; they never delete, and any growth re-arms.
3. **New static sources** not yet in the history. In the probe, about eight stationary night sources in the Sacramento Valley (for example 40.07N 122.19W, seen by all three satellites on the night of 28 Oct and by two on 29 Oct, FRP 1.5-6.5 MW) had **no** 2024 S-NPP history at their cells [M1, M2]. They need a short-term rule (3+ nights in 14 days with an unchanged footprint) that first lowers the ceiling and after about 5 nights moves them to H0, with growth re-arming.
4. **Closed or abandoned plants** stay in inventories [S39]: require recency (dormant after 180 days silent).
5. **Seasonal repeat burners** (rice fields, RX units): month-entropy gate; ag burns are mostly daytime while 89% of detections in 10+ day cells are at night [M2].
6. **Feed gaps** shrink hit counts (S-NPP outage 2024-07-24 to 07-28 [M2]): count detection-days across all satellites, not per satellite. Satellites have different pixel grids, so use the 440 m cell.
7. **SP `type` is bug-prone**: version 1-2 monthly files mislabel static sources as type 0; use version 3 only [S1].

---

## 3. Agricultural and prescribed burning

### 3.1 How much of the detection stream is controlled or unexplained? [M3]
CONUS 2024, S-NPP standard processing. "Reported wildfire" = inside a time-valid WFIGS wildfire perimeter of 2,000+ acres or within 3 km of a WFIGS wildfire incident point from 1 day before discovery to its out/containment date (capped 60 days). "Reported RX" = within 3 km of a WFIGS RX incident point from 1 day before discovery to 21 days. Priority order static, wildfire, RX, unexplained.

| Month | Detections | Static (type 2/3) | Reported wildfire | Reported RX | Unexplained |
|---|---|---|---|---|---|
| Jan | 12,861 | 20.6% | 0.3% | 6.0% | 73.0% |
| Feb | 39,510 | 12.6% | 9.3% | 9.4% | 68.7% |
| Mar | 40,089 | 12.9% | 4.5% | 10.1% | 72.5% |
| Apr | 36,425 | 17.2% | 1.9% | 7.6% | 73.4% |
| May | 20,954 | 29.1% | 8.2% | 4.7% | 58.0% |
| Jun | 25,735 | 31.2% | 19.7% | 1.5% | 47.6% |
| Jul | 41,672 | 12.2% | 66.2% | 0.2% | 21.5% |
| Aug | 73,908 | 14.2% | 59.1% | 0.1% | 26.6% |
| Sep | 68,907 | 12.4% | 58.6% | 0.4% | 28.7% |
| Oct | 66,340 | 16.3% | 33.0% | 1.3% | 49.5% |
| Nov | 23,144 | 21.3% | 3.9% | 3.9% | 70.9% |
| Dec | 18,558 | 22.9% | 1.5% | 2.6% | 73.1% |
| **Year** | **468,103** | **16.5%** | **31.6%** | **3.3%** | **48.7%** |

Notes: the 3 km point match under-counts spread of large fires beyond the initial point and perimeters exist only for fires of 2,000+ acres in this test, so "unexplained" is an upper bound on non-wildfire heat. Even so the pattern is clear: outside the July-September wildfire peak, most detections are neither static nor reported wildfire. October "unexplained" (32,810 detections): 70% daytime, median FRP 4.9 MW, p90 26.5 MW, 89% nominal confidence [M3].

Episode view, Sep-Nov 2024 (components of detections within 1.5 km and 2 days of each other; 28,294 episodes) [M5]:

| Class | Episodes | Single-detection | Seen on 1 day only | Seen on 3+ days | Any night detection | Max FRP median / under 10 MW |
|---|---|---|---|---|---|---|
| Unexplained | 23,507 | 55% | 85% | 4.4% | 33% | 3.7 MW / 80% |
| Static (type 2/3) | 3,481 | 36% | 45% | 35% | 96% | 1.2 MW / 98% |
| Reported wildfire | 980 | 35% | 62% | 22% | 65% | 6.3 MW / 61% |
| Reported RX | 326 | 30% | 72% | 7.7% | 34% | 10.4 MW / 49% |

So unexplained episodes outnumber reported-wildfire episodes 24 to 1, but are small and short; and 62% of *reported* wildfire episodes are also one-day events (mostly small, quickly contained fires), so a multi-day persistence gate would hide most reported fires.

### 3.2 Land-cover masks
- **USDA NASS Cropland Data Layer (CDL)**: annual, 30 m (and 10 m), CONUS; the 2025 national 30 m file is a 1.9 GB zip (10 m: 9.8 GB), so do not bulk-download for this task; clip to the client AOI [S36]. NASS now promotes the CroplandCROS portal; a CropScape GetCDLValue point service existed (a single test call from this machine returned "Failed to get value", so treat it as unverified) [S36]. Rice is a distinct CDL class, which matters for the Sacramento Valley.
- **Annual NLCD Collection 1.1**: 1985-2024, 30 m, six products (land cover, change, confidence, impervious fraction, descriptor, spectral change day), distributed via EarthExplorer, MRLC, ScienceBase, web services and AWS S3 (us-west-2), no use restrictions [S37].
- Use: a 3x3 km neighbourhood lookup around each detection (share cultivated; presence of forest, shrub or grass within 500 m; developed-intensity class for WUI). Both layers are annual: they miss fallow-to-crop changes and say nothing about whether a burn is permitted.

### 3.3 Calendars and rules that are documented
- **Sacramento Valley rice straw (Butte County AQMD conditional permit, 2024 form)**: burning only on days the ARB/District allows; "intensive fall rice burn season" 1 Sep-31 Dec with acres allocated field by field each day from a ready-to-burn list; ignition hours Sept-Feb **10:00-15:00 with flames out by 16:00** (Mar-Aug 10:00-17:00); unburned allocated acres returned by 14:00; up to 25% of planted acres per grower [S34]; the district FAQ also cites a 125,000-acre basinwide annual allocation but flags its figures as possibly outdated [S34]. CARB publishes the daily basin burn decision and a "Basinwide Ag-Burn Acreage Allocation" PDF: on 2026-10-29 it was a permissive burn day (fair) with a **2,000-acre basinwide allocation** [S33].
- Consistency check [M1, M2]: 2,000 acres is about 8 km2, i.e. a few dozen 0.15-0.4 km2 VIIRS pixels' worth of burning, in line with 26-31 daytime detections per pass in the valley box. In Oct-Nov 2024 the valley box (38.7-40.4N, 122.6-121.2W) had **921 day versus 116 night** S-NPP detections (89% day), while Jul-Aug (Park Fire) had 1,293 day versus 2,760 night (32% day). In the probe only 14 of 87 daytime valley detections were followed by a night detection within 1.5 km.
- **Southeast prescribed fire**: peak February-March, least May-November (12-state permit database, 2010-2020) [S24]. VIIRS 375 m caught only 25% of Georgia planned burns over 100 acres (ABI 29%) [S18], so absence of a hotspot says nothing about a planned burn.
- **Fuel dryness shifts the mix**: as 1000-h fuel moisture falls, active-fire pixels move from agricultural and prescribed fire into wildfires [S22].

### 3.4 Registries and whether they have usable APIs
| Source | Content | Access | Verdict |
|---|---|---|---|
| WFIGS Incident Locations (current) `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0` [M3] | 725 rows on 2026-10-29: **RX 489**, WF 235, CX 1; fields include `IncidentTypeCategory`, `FireDiscoveryDateTime`, `IncidentSize`, `POOState`, `ModifiedOnDateTime_dt`, `ActiveFireCandidate` (=1 for all RX); max 2,000 rows per call; no key | ArcGIS REST JSON, near real time | **Usable now.** Of the 490 RX rows 94 were modified within 24 h, 58 discovered within 2 days, 238 older than 30 days (pile burns). States: MT 58, AZ 49, OR 48, ID 47, WA 43, UT 34, CA 31, TN 30, CO 21, NM 21. Federal and state agencies only: no Florida or Georgia private burns, little California. |
| WFIGS perimeters, RX category | Only 154 RX perimeters of 100+ acres in all of 2024; 123 July-Dec detections inside them (0.04%) [M2] | ArcGIS REST | Too sparse; use points. |
| CARB PFIRS burn-day page `https://ssl.arb.ca.gov/pfirs/cb3/cb3.php?id=3` [S33] | 24/48/72/96-h burn decision per air basin, "updated by 3 p.m."; **no per-burn locations, no CSV/JSON** | HTML | Context only (scrapable). |
| CARB allocation PDF `https://ssl.arb.ca.gov/pfirs/met/allo/allo.pdf` [S33] | Daily basinwide acre allocation, burn decision, PM2.5 by district | PDF, parseable | Context only. |
| BCAQMD burn status lines [S34] | Ag burn line (530) 332-9406; harvested-fields ready list (530) 332-9408 | Phone | Ask the district whether a feed exists. |
| Florida Forest Service active burn authorizations `https://gis.fdacs.gov/mapping/rest/services/FFS/ActiveOBAPoints/FeatureServer` [S35] | Pile and broadcast authorization points | ArcGIS REST | **Returned "Token Required" (code 499) on 2026-10-29**; request access from FFS. |
| Southeast permit database (12 states) [S24] and Georgia permits [S18] | Location, size, day | "Available on request... not publicly available due to private landowner privacy concerns"; Georgia permits recorded at county level [S24] | Not an API. |
| Forest-level RX maps (for example Wallowa-Whitman, Malheur, Umatilla) | Planned, active, completed units | Web maps | Not machine-readable. |

### 3.5 Risks of using "controlled burn" labels as filters
- An **escape** looks like a controlled burn until the registry flips category (WFIGS `IncidentTypeCategory` RX to WF) or the footprint exceeds the unit [S45].
- A 3 km RX buffer can mask an unrelated wildfire that ignites nearby.
- Ag fields touch homes in the Chico-Paradise valley margin, and stubble or straw fires can run into grass and woodland.
- Therefore these labels lower the **ceiling** (NOTICE only, no crew dispatch, longer expiry) and arm re-arm triggers; they do not remove the detection from the forecast pipeline near client homes.

---

## 4. How operational systems screen or vet hotspots

| System | What it does with satellite hotspots | Screening / vetting | Time to "confirmed" | Source |
|---|---|---|---|---|
| **NOAA Hazard Mapping System (HMS) fire** | Combines GOES, VIIRS (375 m), MODIS, AVHRR and NGFS points into daily text/shapefile/KML files (`Lon, Lat, YearDay, Time, Satellite, Method, Ecosystem, FRP`); the 2026-10-28 file holds NGFS GOES-East/West points, the three VIIRS satellites and 5 analyst-added points [M8] | **Human analysts** QC the automated detections, "identify plume-induced detections that extend beyond the real fire perimeter and correct them"; industrial and urban thermal anomalies "normally removed" | Polar L1 data 1.5-2.5 h after observation + 5-10 min processing + **analyst QC 1-3 h**; GOES L1 10-30 min + 5-15 min processing. Initial daily detections typically by 08:00 Eastern. Positions are "general guidance and strategic planning"; tactical decisions "require corroborating information" | [S25] |
| **NOAA NGFS** (experimental, GOES-R and VIIRS) | Event-centric fire pixels with terrain-corrected geolocation; each pixel carries `type`: 0 presumed vegetation fire, 1 co-located with a known NIFC incident (`known_incident_type` WF or RX), 2 co-located with a persistent emitter, 3 known solar farm, 4 solar-farm-like reflection not in database, 5 likely urban; plus `quality_flag` (robust vs persistence-based), feature tracking id and duration, county and GACC, NWS fire-weather code | Automatic classification against persistent-emitter, solar and urban databases and NIFC incidents; "non-operational and should be considered experimental" | Alert claims of about one minute from energy reaching the satellite and 0.25-1 acre sensitivity (NOAA statements); no SLA, outages possible; NOAA expected operational status in 2026 | [S46, S47, S49] |
| **NASA FIRMS** | Publishes URT/NRT/SP detections; GOES layer shows only mask codes 10, 11, 30, 31 | Real-time anomaly filtering only (for example lunar-calibration streaks); NRT data "don't attribute" volcano/flare/industrial types; experimental Static Thermal Anomalies layers "for reference only" | URT under 60 s, NRT up to 3 h | [S5, S7, S8] |
| **USFS Active Fire Mapping (GTAC) and FIRMS US/Canada** | Detections, shapefiles, KMZ for NICC and the GACCs; informs planning of NIROPS infrared flights | Satellite hotspots are leads; airborne IR (NIROPS) and dispatch provide tactical truth | Product-level; no confirmation clock | [S30] |
| **NIFC / GACC dispatch (IRWIN, WFIGS)** | Incidents are created by dispatch/agency report, not from hotspots; ICS-209 is required above 100 acres (timber) or 300 acres (grass/brush) | Human reporting chain | Minutes to hours after discovery; this is the "confirmed fire" feed the engine already has | [S31, S32] |
| **CAL FIRE with ALERTCalifornia** | Camera network of about 1,260 mountaintop cameras, 915 with AI smoke detection (July 2026 report) | Alerts are vetted and confirmed by trained watchstanders before response; AI flagged fires before the first 911 call more than 30% of the time in its first season | Minutes | [S27] |
| **Watch Duty** | Starts from automated monitoring of 911 dispatch traffic; regional human reporters then check scanners, cameras, satellites and public sources and wait for an on-scene report; app lists VIIRS/MODIS hotspots as a layer | Human; "operated by real people, not machines or crowdsourcing" | No time frames published | [S26] |
| **Canada CWFIS (Fire M3)** | Hotspots from AVHRR, MODIS, VIIRS via NOAA, NASA FIRMS, USFS AFM and UMD, plus fire-weather indices at hotspot locations | Documentation I could reach states a hotspot "may stand for one fire or part of a larger one" and not all fires are visible; **I could not verify a static-source filter** | Not stated | [S29] |
| **Australia DEA Hotspots** (Sentinel Hotspots is not described on the pages I could reach) | MODIS, VIIRS (S-NPP, NOAA-20, NOAA-21), Himawari-9; updated every 10 min; at best 17 min old | **No filtering** of non-bushfire sources (gas fires, heavy industry, furnaces, smoke plumes, hot rocks); all confidences shown; "not for safety-of-life decisions"; fires need roughly 1,000 m2; geostationary data unreliable within about an hour of sunrise and sunset | Not applicable | [S28] |
| **GWIS / EFFIS (EU JRC)** | Redistribute FIRMS hotspots; GWIS clusters thermal anomalies in space and time | No industrial-removal step documented in the pages found | Not stated | [S48] |
| **FEDS (UCI/NASA near-real-time fire tracker)** | Tracks fires with 375 m VIIRS at 12-hour steps | NRT uses only nominal and high confidence; detections within 5 km (forest) or 1 km (other cover) of a fire's perimeter are appended to it; inactive after 5 days with no new pixels; small fires (under 20 km2) with more than 20 cumulative pixels per km2 since ignition are labelled static and invalid | Twice daily | [S23] |

### 4.1 Lessons
1. **No operator promotes a raw hotspot to "confirmed" automatically.** Confirmation comes from dispatch, cameras, on-scene reports or analysts. Satellite hotspots feed situational awareness.
2. Where machine screening exists it is (a) a **static / industrial / solar / urban mask** (HMS analysts, NGFS `type`, FIRMS STA) and (b) **tracking over time** (FEDS, NGFS features, GOES temporal filter), not a confidence threshold.
3. Human vetting is slow for satellite data (HMS adds 1-3 h) and fast only where the trigger is a camera or a 911 call (minutes). The engine should therefore use humans for triage near clients, not as a gate for every hotspot.
4. **What an analyst-vetted layer actually removes** (my comparison, HMS daily file for 2026-10-28 against the same-day FIRMS probe, 4,708 VIIRS detections, ±10 min and 300 m match) [M8]: HMS retains **71.2%**: day 90.2% but **night 32.2%**; by FRP under 1 MW 28.9%, 1-3 MW 47.1%, 3-10 MW 83.6%, 10-30 MW 94.6%, 30+ MW 96.3%; cells with persistent detections 41.8% versus 74.0% elsewhere; stand-alone pixels 56.8% versus 73.1% with neighbours. It retained **99.5%** of detections within 5 km of an RX incident, 98.2% in the Sacramento Valley box (rice burning) and 100% near reported wildfire points. So HMS removes night-time low-FRP, persistent and stand-alone heat, and **does not remove agricultural or prescribed burning**. Caveats: HMS VIIRS points come from NOAA's own processing, so part of the gap may be algorithmic rather than analyst action; HMS timestamps are granule-level (0 to -6 min offset).

---

## 5. Multi-sensor confirmation

### 5.1 GOES-R ABI Fire/Hot Spot Characterization (FDC / FHS)
**Access and latency (verified today).** NOAA open data on AWS, no key: `s3://noaa-goes19/ABI-L2-FDCC/YYYY/DDD/HH/` (GOES-East since 2025-04-07) and `noaa-goes18` (GOES-West) [S16, S17]. CONUS scans every **5 min** (12 files an hour), the scan itself takes about 2 min 37 s (for example start 18:01:17, end 18:03:54), the object appears on S3 about **20-40 s after scan end** (listing `LastModified` minus scan end, 24 files checked across both satellites) [M7]. Files are small: about 0.53 MB (G19) and 0.29 MB (G18) per scan. Products: Mask, Power (FRP), Temp, Area, DQF [S14, S15]. Google Earth Engine's catalogue lists a 10-minute cadence for FDCC; the AWS listing shows 5 minutes, so read the bucket, not the catalogue [M7, S15]. FIRMS' own GOES layer lags (20-30 min) and is not in the Area API source list [S7, S6].

**Mask classes** (add 20 for the temporally filtered version): 10 processed, 11 saturated, 12 cloud-contaminated, 13 high, 14 medium, 15 low probability; NOAA's guidance for operational users with low false-alarm tolerance is codes **10, 11, 30, 31** [S14]. Filtered codes mean a fire was found in the same pixel in the last 12 h [S14]. Pixel codes also give *observability*: 100 = clear and no fire; 200-245 = cloud; 150-153 = invalid surface or water [S15, M7].

**Documented false alarms** [S14]: water clouds (mainly higher latitudes), solar-power facilities (recurring on cloud-free summer days in the south-west), surface heterogeneity (bare ground among vegetation, cooling lakes, urban areas, coastlines), cold surfaces; L2 locations are **not terrain-corrected**, so GOES pixels are offset in mountains. Misalignment with VIIRS affects about 12% of fire events; with a 3x3 buffer the apparent false-alarm rate falls from 26-36% to 7-15% [S19].

**Performance.**
- Li et al. 2020 (south-east US, 2018): detection probability above 95% only for ABI pixels containing 11+ VIIRS 375 m fire pixels (or 114+ Landsat-8 30 m); minimum practical fire about 34.5 MW; commission error 14-21% for classes 10-14 and **71% for low probability**; ABI detected only 29% of Georgia planned burns over 100 acres (VIIRS 25%) [S18].
- Vanunu et al. 2026 (Western US, Amazon, Patagonia): precision of codes 10/11/30/31 0.74-0.79 unbuffered, 0.85-0.93 buffered; probability that FDC fires in a pixel holding at least 1 VIIRS detection only **0.10 unbuffered and 0.25 with a 5x5 window in California-Oregon**; ABI pixel about 4 km there [S19].

**My measurement, 28-29 Oct 2026 CONUS** (89 GOES-18/19 scans matched to 7,177 VIIRS detections; match = FDC fire pixel within one pixel of the VIIRS pixel or its neighbours, scan nearest in time) [M7]:

| VIIRS subset | n | FDC any class within 2 px | FDC codes 10/11/30/31 within 1 px | Clear sky and no FDC fire | Cloud or invalid |
|---|---|---|---|---|---|
| All | 7,177 | 18.8% | 13.2% | 64.2% | 17.0% |
| Daytime | 4,099 | 32.8% | 23.2% | 60.2% | 7.0% |
| **Night** | 3,078 | **0.0%** | 0.0% | 69.6% | 30.3% |
| FRP < 3 MW | 3,259 | 0.1-0.9% | 0.1-0.5% | 72-80% | 20-28% |
| FRP 3-10 MW | 2,187 | 14.1% | 8.6% | 73.3% | 12.6% |
| FRP 10-30 MW | 1,125 | 49.0% | 33.2% | 43.1% | 7.9% |
| FRP 30+ MW | 606 | 77.1% | 62.2% | 15.3% | 7.6% |
| Cluster = 1 pixel | 636 | 1.6% (1.8% of those in clear sky) | | | |
| Cluster = 10+ pixels | 162 | 44.5% (47% of clear-sky) | | | |
| Sacramento Valley box, day, FRP 10+ | 32 | 62.5% | | | |

The CONUS-wide count of FDC fire pixels per scan is only about 40-70 for GOES-East and 8-17 for GOES-West by day and 0-9 at night, against hundreds of VIIRS detections per overpass [M7]. Reverse check: FDC codes 10/11/30/31 had a VIIRS detection within 6 km and ±6 h for 85% (GOES-East, n = 584) and 100% (GOES-West, n = 63); the low-probability code 15 only 29% (n = 49) [M7], matching the literature.

**Case study: 2024 Park Fire (Butte County).** WFIGS discovery 2024-07-24 21:52:43 UTC at 39.8186N 121.8026W [S32]. GOES-18 FDC had mask code 10 (processed fire, 60 MW) near the origin in the scan starting **21:56:17 (3.6 min after the recorded discovery)**, GOES-16 126 MW in the same scan; one later scan (22:06) showed no fire pixel (cause not examined; smoke or cloud is likely), so continuity is not guaranteed [M6]. The first VIIRS pixels inside the final perimeter were NOAA-20 at **2024-07-25 10:20 UTC (12.5 h later)**, with dozens of pixels; S-NPP returned no CONUS detections between 2024-07-24 and 07-28 [M2, M6].

**Implications.** (a) FDC is the only free source that can reveal a *large, intense* new fire between VIIRS windows. (b) Requiring FDC confirmation would miss virtually every small fire and every night-time fire (false-negative rate about 98% for singleton clusters and 100% at night in this sample). (c) FDC says nothing about whether the fire is controlled: 62% of Sacramento Valley daytime detections of 10+ MW were FDC-confirmed and are rice burns. (d) Use `Mask` 100/200-245 to know whether a *missing* GOES detection is informative (clear sky) or not (cloud).

### 5.2 NOAA NGFS (experimental)
GOES-based, terrain-corrected, with a per-pixel `type` that separates vegetation fire, known incident, persistent emitter, solar and urban, and with feature tracking [S46]. Portal: `https://fire.data.nesdis.noaa.gov/data/` offers 12 GOES collections (scene detections and features for GOES-East/West CONUS and two mesoscale sectors) as GeoJSON, KML, CSV or shapefile, up to 24 h per request, no login, "beta", no licence statement, outages without notice [S47]. The README also documents JPSS VIIRS pixel fields with the same `type` codes but the portal collection list I saw contains only GOES [S46, S47]. NGFS also appears in HMS: **26,994 of 30,719 rows (88%) in the 2026-10-28 HMS file are `Method = NGFS` GOES points** [M8]. No API is documented. Treat as a candidate second opinion and as a template for the classification scheme, not a dependency, until NOAA declares it operational.

### 5.3 Repeat VIIRS passes
- Orbit geometry [M1, S10]: S-NPP, NOAA-20 and NOAA-21 share one orbital plane; at a given site NOAA-20 and NOAA-21 pass about **40-56 min apart** (40-45 min at Chico, 54-58 min in the east); day and night windows are **about 11-12 h apart**. Per satellite and site there are 3-4 looks a day at mid latitudes [S1], but they cluster in two windows (about 12:20-14:20 and 01:15-03:00 local at Chico).
- After **S-NPP stops (2026-11-02 13:00 UTC)** [S10] only two satellites remain, so the "second look in the same window" is NOAA-20 vs NOAA-21 and the next independent look is ~12 h away.
- A second satellite in the same window is independent for **sensor artifacts, glint and geolocation**, not for fire behaviour. In the 37 h probe window 47% of 1,702 clusters were seen by one satellite, 25% by two and 28% by three; 67% appeared in a single overpass window, 21% in two, 12% in three or more; 17% spanned 12 h or more [M1].
- False-negative implication: a rule that needs "two independent windows" delays a genuine new fire by 11-12 h; the Park Fire had no VIIRS pixel until 12.5 h after ignition even with one satellite missing [M6]. For large 2024 fires with S-NPP + NOAA-20, the first detection came a median 5.8 h after the recorded discovery (80% within 12 h, 93% within 24 h; 20% before the recorded discovery time) [M4].

### 5.4 Landsat NRT (FIRMS `LANDSAT_NRT`)
30 m active fire from Landsat 8/9 OLI, CONUS, southern Canada, northern Mexico; detections reach FIRMS about 30 minutes (other pages: 30-60 min) after the overpass, using USGS EROS Sioux Falls direct readout only [S7, S13, S6]. Spatial detail is excellent for deciding whether a hotspot is a field, a pile or a front, but revisit per site is sparse (each satellite is on a multi-day cycle), so it is a **display / opportunistic confirmer**, not a gate.

### 5.5 MODIS (Terra, Aqua)
1 km pixels, drifting orbits, and both missions are to shut down "late 2026/early 2027" per NASA's transition page [S12]; Terra is currently the only morning-orbit source [S10]. Not worth building on.

### 5.6 Non-satellite confirmers
911/CAD-derived reports (Watch Duty workflow), camera networks with AI detection and human watchstanders (ALERTCalifornia: 636 of 1,668 detections preceded the first 911 call in one annual tally; more than 900 fires on state land flagged before 911 in 2026, per press reports) [S26, S27]. These are the fastest "human" confirmations but have no open API that I could verify.

### 5.7 Latency and false-negative summary
| Source | Typical delay from fire to signal | What a rule built on it would miss |
|---|---|---|
| VIIRS URT | Next overpass (up to ~12 h), then under 2 min | Fires between windows; cloud and smoke; small cool fires (e.g. VIIRS caught 25% of 100+ acre GA planned burns [S18]) |
| Second VIIRS satellite | +40-56 min after the first | Fires outside the overlapping swath edge |
| Next VIIRS window | +11-12 h | Anything that dies, is suppressed or is obscured by then |
| GOES FDC | About 3-6 min after ignition for 30+ MW fires (Park Fire 3.6 min) | Almost all fires under ~10 MW and all night-time small fires [M7] |
| NGFS | Claimed ~1 min | Unknown; experimental |
| HMS | Hours (1.5-2.5 h L1 + 1-3 h QC) | Night-time low-FRP detections (68% dropped) [M8] |
| Landsat NRT | 30-60 min after a sparse overpass | Everything between overpasses |
| Cameras / 911 | Minutes | Fires out of sight or not reported |

---

## 6. Candidate criteria

Decision vocabulary: **HARD** = hard filter into the heat-only tier (still logged and mapped, with a re-arm guard); **CEILING** = tier / alert-ceiling only (lowers what the detection may trigger, never deletes it); **UP** = evidence that raises the tier; **DISPLAY** = display-only; **REJECT**.

| ID | Criterion | Decision |
|---|---|---|
| C1 | History-based static-source mask (recurrence) | **HARD**, with guards |
| C2 | Over-water / offshore | **HARD** (1 km shore buffer) |
| C3 | Short-term stationary night source | CEILING, then HARD after about 5 stable nights |
| C4 | Facility / solar-polygon proximity prior | CEILING (support only); DISPLAY for solar-only daytime singles |
| C5 | Low-confidence (`l`) pixels cannot seed | CEILING (no seeding); dropping `l` outright REJECT |
| C6 | FRP floor | REJECT as filter (use as modifier) |
| C7 | Minimum pixel count / cluster size | REJECT as filter (use area as modifier) |
| C8 | Swath-edge singleton | CEILING |
| C9 | Cropland + season + daytime ag-burn presumption | CEILING |
| C10 | Registered prescribed burn (WFIGS RX) | CEILING |
| C11 | Burn-day and acreage-allocation context | modifier to C9 |
| C12 | Non-static night-time detection | UP |
| C13 | Re-detection in a later window | UP (never a gate) |
| C14 | Two-satellite agreement in one window | UP (artifact guard) |
| C15 | GOES FDC codes 10/11/30/31 nearby | UP (fast); REJECT as requirement |
| C16 | GOES/NGFS-only new start | P1 plus immediate triage |
| C17 | Growth / spread signature | UP |
| C18 | Attach to reported fire (lag- and plume-aware) | path to Confirmed |
| C19 | Proximity-to-home escalator | response rule |
| C20 | URT provisional handling | CEILING (cap) |
| C21 | Weather / fuel-dryness prior | modifier only |
| C22 | Observability and coverage-gap monitor | safety net |
| C23-C26 | Developed-land suppression; agency-report gate; multi-day gate; `h` as severity, MODIS, Landsat | REJECT / DISPLAY |

### C1. History-based static-source mask: HARD
- **Rule.** Cell = 0.004 degrees (about 440 m). Static if it has 10 or more distinct detection-days in the trailing 365 days (all satellites pooled), is active in 3 or more calendar months, and has a hit in the last 180 days (dormant otherwise); lighter variant 5+ days in 180 days. Seed with cells that carry SP `type` 2 in prior years. A detection in a static cell goes to H0 **unless a guard trips**: (a) two or more non-static pixels within 1.5 km in the same overpass; (b) FRP above max(15 MW, 3x the cell's p95) or `bright_ti4` saturated where the cell never saturated; (c) a GOES FDC 10/11 pixel over the cell above its history.
- **Data.** FIRMS SP country-year CSVs (S-NPP and NOAA-20, 31-44 MB per satellite-year, free) plus UMD monthly files for the lag plus local accumulation of the NRT feed [S42, S1]. Rebuild monthly.
- **Benefit.** Static `type` 2 is 15.2% of CONUS detections (28% at night). A 10-day, six-month history flags 12.2% of later detections (88% of them `type` 2) and flagged **0 of 124,608** detections inside 2,000+ acre 2024 wildfire perimeters; the earliest pixel of 203 large fires was never flagged at 5+ days [M2].
- **Risk.** A fire igniting in or beside a static site (first pixel hidden until the guard trips); a brand-new static source; a long-lived "controlled" heat source that later escapes (Calf Canyon began as a pile fire that smouldered for months [S45]).
- **Recommendation.** HARD into H0, always logged, mapped and re-armable.

### C2. Over-water / offshore: HARD
- **Rule.** Pixel and all 8 neighbours are water and at least 1 km from land, or SP `type` 3. **Data.** NLCD water class or any coastline mask; SP `type`. **Benefit.** `type` 3 is 1.3% of CONUS detections [M2]; Collection 2 swath products already drop low-confidence water pixels and require 3 co-located detections in 30 days for water pixels without an M13 signature [S1, S3]. **Risk.** Shoreline homes, piers and moored vessels, hence the 1 km buffer. **Recommendation.** HARD.

### C3. Short-term stationary night source: CEILING, then HARD
- **Rule.** Same cell on 3 or more distinct nights in 14 days, night-only, FRP under 10 MW, footprint of at most 2 pixels and not growing. First lowers the ceiling to NOTICE and removes crew dispatch; after about 5 nights without growth moves to H0.
- **Data.** Own NRT accumulation. **Benefit.** About eight stationary night sources in the Sacramento Valley in the probe had no 2024 history and would re-trigger forecasts nightly [M1, M2]; a stability prior cut industrial/urban false positives from 88.6% to 22.7% in a stratified test [S20].
- **Risk.** A smouldering real fire, again the Calf Canyon pattern [S45]. **Recommendation.** CEILING first, HARD only after the 5-night test, growth or FRP rise re-arms.

### C4. Facility / solar-polygon prior: CEILING (support only)
- **Rule.** Within 750 m of a mapped heat-source facility (GIHS, EPA/EIA/WRI points) adds weight to C1/C3; a lone daytime singleton within 300 m of a USPVDB solar polygon goes to display-only (reflective glint).
- **Data.** Zenodo GIHS, USGS USPVDB v4.0 (2 MB GeoJSON), EPA Envirofacts, WRI [S39, S40, S41]. **Benefit.** Catches new sources before history accrues; glint false alarms are the documented daytime failure [S2, S14].
- **Risk.** Closed plants stay in inventories; GIHS misclassifies North American straw burning; a facility beside wildland [S39]. **Recommendation.** Never alone.

### C5. Low-confidence pixels cannot seed: CEILING
- **Rule.** An `l` pixel may attach to an existing cluster but cannot create a P1 unless a nominal/high pixel lies within 1 km in the same or adjacent look, or it is re-detected.
- **Benefit.** Mirrors FEDS (NRT uses nominal/high only [S23]). Isolated `l` pixels are rare (16 of 263 in 2 days) so the gain is small but free [M1].
- **Risk.** Of 259 large 2024 fires, 1 had only `l` pixels on the first look (0.4%) [M4]; `l` has the **higher** FRP (median 16.8 MW day) and is 45% GOES-confirmed [M1, M7]. Dropping `l` outright would remove real fire fringe. **Recommendation.** CEILING for seeding; REJECT hard drop.

### C6. FRP floor: REJECT as filter
- **Benefit if used.** 42% of unexplained episodes have maximum FRP under 3 MW and 80% under 10 MW [M5]. **Risk.** At the first look of 259 large 2024 fires, 16% had maximum FRP under 3 MW, 43% under 10 MW, and 49% were first seen at night when FRP is small (night median 1.2 MW) [M4, M1]. **Recommendation.** Keep FRP as a modifier (for example 20 MW outside ag/RX context goes straight to P2); calibrate per satellite (NOAA-21 FRP about 1.4x S-NPP on matched pixels) [M1].

### C7. Minimum pixel count: REJECT as filter
- **Benefit if used.** Singleton clusters are 37% of clusters but 8.7% of detections [M1]; 55% of unexplained episodes are single-detection [M5]. **Risk.** 12% of large fires are a single pixel on the first look and 33% at most 3 [M4]. **Recommendation.** Use cluster **area** (sum of scan x track) as a modifier only.

### C8. Swath-edge singleton: CEILING
- **Rule.** A single pixel with scan >= 0.6 km (pixel up to 0.62 km2) and no neighbour stays P1 and display-only until the next look (about 45 min) unless within 3 km of a home. **Evidence.** 8.8% of detections have scan > 0.6 km; `l` share is 9.5% at scan >= 0.65 km versus 2-4% inward; bow-tie duplicates and plume parallax concentrate there [M1, S1, S7]. **Risk.** Short delay for a real fire at the edge. **Recommendation.** CEILING.

### C9. Cropland + season + daytime ag-burn presumption: CEILING
- **Rule.** Label `ag_presumed` when: the pixel and at least 70% of its 3x3 km neighbourhood are cultivated (CDL/NLCD); no forest, shrub or grass patch within 500 m; the VIIRS day pass local time is 10:00-16:00; no night detection within 1.5 km in the adjacent night window; cluster area at most about 1 km2; inside the regional season (Sacramento Valley 1 Sep-31 Dec); CARB burn day permissive or marginal. Effect: ceiling NOTICE (no WATCH or dispatch without an upgrade), TTL 24 h.
- **Data.** CDL/NLCD clipped to the client AOI; CARB page and allocation PDF [S36, S37, S33]. **Benefit.** Valley box Oct-Nov 2024 is 89% daytime; 85% of unexplained episodes last one day; permit hours end with flames out by 16:00 [M2, M5, S34].
- **Risk.** Escapes into adjacent grass and trees, fields on the WUI margin, straw and stubble fire spread, CDL annual error. **Recommendation.** CEILING with re-arm on night persistence, growth, contact with non-crop fuel, a no-burn day, or distance to a home under 3 km.

### C10. Registered prescribed burn (WFIGS RX): CEILING
- **Rule.** Within 3 km of an open WFIGS RX incident (discovered within 21 days) or inside an RX perimeter: `rx_registered`, ceiling NOTICE. Re-arm if the footprint exceeds 3 km from the point or the unit, FRP grows, `IncidentTypeCategory` flips to WF, or night persistence continues after the RX is out.
- **Data.** WFIGS Incident Locations (free, ArcGIS REST, 489 open RX on 2026-10-29) [M3]. **Benefit.** RX registry explains 3.3% of 2024 CONUS detections (about 10% in February-March) and 6.7% of live detections within 5 km; HMS analysts keep these (99.5%), so nothing else will remove them [M3, M8].
- **Risk.** Escapes (Hermits Peak [S45]); stale or misplaced points; a 3 km buffer masking an unrelated fire; private and state burns (Florida, Georgia) are absent. **Recommendation.** CEILING.

### C11. Burn-day and allocation context: modifier
- CARB's daily basin decision and the Sacramento Valley allocation PDF (2,000 acres on 2026-10-29) strengthen or weaken C9: a **no-burn day** switches the ag presumption off; the number of daytime pixels can be compared with the allocation [S33]. Brittle HTML/PDF scraping; local districts may differ from CARB. **Recommendation.** Modifier.

### C12. Non-static night-time detection: UP
- **Rule.** A non-H0 cluster that is first seen at night with 3 or more pixels or FRP at least 5 MW, or that persists into the next night window, rises to P2. **Evidence.** 94% of MODIS night FRP lies inside wildfires [S22]; 65% of reported-wildfire episodes versus 33% of unexplained ones have a night detection [M5]; only 14 of 87 valley daytime detections were followed by a night detection [M1]. **Risk.** Night VIIRS sees tiny static heat (93% of static episodes are night), so apply after C1 and C3; a day-seen fire needs ~12 h to be re-seen at night. **Recommendation.** UP.

### C13. Re-detection in a later window: UP (never a gate)
- **Rule.** Re-detection 6 or more hours later within 1 km raises P1 to P2. **Benefit.** 85% of unexplained episodes are one-day events [M5]. **Risk.** 62% of reported wildfire episodes are also one-day (mostly small), and the wait is 11-12 h (Park Fire: first VIIRS pixel 12.5 h after ignition [M6]). **Recommendation.** UP; see C25 for why not a gate.

### C14. Two-satellite agreement in one window: UP
- **Rule.** Same cluster seen by NOAA-20 and NOAA-21 (40-56 min apart) with at least 2 pixels or rising FRP. **Benefit.** Independent against sensor artifacts, glint, lunar-calibration streaks, retracted URT pixels and geolocation anomalies [S7]; 53% of probe clusters were seen by two or more satellites [M1]. **Risk.** 47% were single-satellite (swath geometry); it says nothing about whether the burn is controlled; after 2 Nov only two satellites remain. **Recommendation.** UP.

### C15. GOES FDC codes 10/11/30/31 within 5 km: UP, never a requirement
- **Rule.** Any FDC pixel with those codes within about 5 km (2 ABI pixels; parallax in terrain) in the last 15 min raises P1 to P2. **Data.** `noaa-goes19` and `noaa-goes18` S3, free, 5-min scans, 20-40 s after scan end [M7]. **Benefit.** Fast, high-precision on large fires: 77% of 30+ MW daytime detections and 85% of 100+ MW; precision 85-100% (VIIRS-supported) for the high classes [M7, S19]. **Risk.** If *required*: 98% of singleton clusters and 100% of night detections would be unconfirmed [M7]; it also confirms rice burns. **Recommendation.** UP; REJECT as requirement.

### C16. GOES/NGFS-only new start: P1 with immediate triage
- **Rule.** FDC codes 10/11 only (not temporally filtered 30/31, not 12-15) with FRP at least 30 MW (10 MW within 15 km of a client) in two consecutive scans, or one scan at 60 MW or more; exclude cells on a GOES false-alarm list (solar polygons, repeat day-time hits in the same cell on 3+ days); creates P1 and a human check; P2 once VIIRS or NGFS corroborates. **Evidence.** Park Fire: +3.6 min versus +12.5 h [M6]. **Risk.** Water-cloud and solar false alarms, missing terrain correction, intermittent continuity; code 15 is only 29% VIIRS-supported [S14, M7]. **Recommendation.** Adopt.

### C17. Growth / spread signature: UP
- **Rule.** Between looks: FRP or area at least doubles, or new pixels appear 1 km or more beyond earlier ones. **Evidence.** VIIRS-based object tracking at 12-hour steps works at fire scale [S23]; 97% of large fires have 2+ detections in the 24 h after the first [M4]. **Risk.** Needs two clear looks. **Recommendation.** UP.

### C18. Attach to a reported fire (lag- and plume-aware): path to Confirmed
- **Rule.** Attach, rather than create a synthetic incident, when within 1 km of a perimeter (non-forest) or 3-5 km (forest, perimeter older than 6 h, or FRP at least 30 MW); mark `plume_suspect` for night, scan >= 0.6 km, 1-5 km outside. **Evidence.** Near 310 large 2024 fires, 9.6% of detections lie outside the *final* perimeter, 7.5% within 500 m, only 1.7% beyond 1 km, 0.8% beyond 5 km [M9]; live perimeters lag spread, so a radius of 1 km is a minimum; FEDS uses 5 km (forest) and 1 km (other) [S23]; night plumes displace pixels outward [S1]. **Risk.** A wide radius can hide an unrelated new fire; keep a forced analyst check for P1 within 5 km of a reported fire. **Recommendation.** Path to CF (confirmed).

### C19. Proximity escalator: response rule
- **Rule.** Any non-H0 cluster (including `ag_presumed` and `rx_registered`) within 3 km of a client home triggers analyst review within a fixed SLA and, meanwhile, a provisional forecast. **Evidence.** In the user's measurement one single-detection cluster lay 1.0 km from a demo home; the workload is a handful per day. **Recommendation.** Mandatory; this is the practical form of "absence is not clearance".

### C20. URT provisional handling: CEILING
- **Rule.** A URT-only cluster cannot exceed P1 or NOTICE until a second look, GOES high class, NRT replacement or proximity escalation; dedupe on lat, lon and time, never `version`; log retractions. **Evidence.** URT pixels are replaced by NRT within 6 h and FIRMS removes anomalies after they appear [S5, S7]. **Recommendation.** CEILING.

### C21. Weather and fuel dryness: modifier
- **Rule.** SPC extreme/critical outlook, Red Flag Warning, or very low 1000-h fuel moisture raises the prior; high fuel moisture lowers only the ceiling of ag/RX-labelled clusters. **Evidence.** As fuels dry, active-fire pixels shift from ag/prescribed fire to wildfire [S22]; NGFS carries `nws_fire_wx_code` [S46]. **Recommendation.** Modifier; never suppress (wildfires occur in wet fuels too).

### C22. Observability and coverage-gap monitor: safety net
- **Rule.** Track expected passes per satellite (S-NPP delivered nothing on 2024-07-24 to 07-28; four other 2024 days were below 25% of the median for both satellites [M2]); alarm on gaps and raise the GOES weight. Close a P1/P2 only after two missed re-detections with clear-sky evidence (GOES `Mask` 100) or a human close; cloud (codes 200-245) or no pass leaves it "unresolved". **Evidence.** 17% of VIIRS detections sat under GOES cloud/invalid pixels [M7]. **Recommendation.** Mandatory.

### C23-C26. Rejected or display-only
- **C23 Developed-land suppression: REJECT.** Nominal daytime urban false alarms are 0.03% [S2]; homes live in developed classes; the persistent mask already handles industry.
- **C24 Require an agency report or HMS confirmation as a gate: REJECT.** Reported wildfires explain 0.6% of live detections and 32% of 2024 detections; HMS adds hours and keeps ag/RX [M3, M8, S25]. Use them as the **Confirmed** path and as labels for backtests.
- **C25 Require detections on 2+ days: REJECT as gate.** It would remove 85% of unexplained episodes but also 62% of reported-wildfire episodes (small fires put out in a day), and it makes a growing large fire wait roughly a day for its second detection-day [M5]. Use the faster evidence (C13, C14, C17: later window, second satellite, growth) as upgrades instead.
- **C26 `h` as cross-satellite severity: REJECT** (NOAA-20 saturates about 4 times less often than S-NPP or NOAA-21: 2.4% versus 9.8% and 12.4% of daytime detections) [M1]. **MODIS: DISPLAY/REJECT** (retiring, 1 km) [S12]. **Landsat 30 m: DISPLAY** (sparse revisit, 30-60 min latency) [S7, S13].

---

## 7. Sources and measurement notes

### 7.1 Sources
- [S1] VIIRS C2 375 m Active Fire User's Guide v1.2 (June 2025): https://www.earthdata.nasa.gov/s3fs-public/2025-06/VIIRS_C2_AF-375m_User_Guide_1.2.pdf
- [S2] Schroeder et al. 2014, RSE 143:85-96: https://directreadout.sci.gsfc.nasa.gov/publications_documents/Schroeder_et_al_2014b_RSE.pdf (abstract: https://www.sciencedirect.com/science/article/abs/pii/S0034425713004483)
- [S3] VIIRS 375 m ATBD: https://viirsland.gsfc.nasa.gov/PDF/VIIRS_activefire_375m_ATBD.pdf
- [S4] FIRMS active fire attributes (MODIS/VIIRS): https://earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs
- [S5] FIRMS URT and RT data: https://www.earthdata.nasa.gov/learn/articles/firms-urt-data
- [S6] FIRMS Area API: https://firms.modaps.eosdis.nasa.gov/api/area/
- [S7] FIRMS FAQ: https://www.earthdata.nasa.gov/data/tools/firms/faq
- [S8] FIRMS Static Thermal Anomalies blog (2025-02-28): https://wiki.earthdata.nasa.gov/spaces/FIRMS/blog/2025/02/28/425855667/FIRMS+incorporates+static+thermal+anomalies+data+to+help+users+differentiate+between+vegetation+and+non+vegetation+fires.
- [S9] FIRMS new fire-type features: https://www.earthdata.nasa.gov/news/blog/firms-releases-new-features-identify-active-fires-type
- [S10] FIRMS: Transitioning from Suomi NPP VIIRS (S-NPP production ends 13:00 UTC 2026-11-02): https://www.earthdata.nasa.gov/news/blog/transitioning-from-suomi-npp-viirs-omps-what-firms-users-need-know
- [S11] NOAA CoastWatch S-NPP cessation (13:00 UTC 2026-11-01 for NOAA delivery): https://coastwatch.noaa.gov/cwn/news/2026-08-10/cessation-suomi-national-polar-orbiting-partnership-s-npp-data.html
- [S12] NASA MODIS to VIIRS transition: https://www.earthdata.nasa.gov/data/alerts-outages/transition-from-modis-viirs
- [S13] FIRMS Landsat NRT active fire: https://www.earthdata.nasa.gov/learn/blog/new-nrt-product-firms-landsat-active-fire-data
- [S14] GOES-18 ABI L2+ FHS (FDC) read-me, full maturity: https://star.nesdis.noaa.gov/atmospheric-composition-training/documents/G18_ABI_FDC_ReadMe.pdf
- [S15] GOES-19 FDCC band and mask descriptions: https://developers.google.com/earth-engine/datasets/catalog/NOAA_GOES_19_FDCC
- [S16] GOES-19 operational as GOES-East: https://www.nesdis.noaa.gov/news/noaas-goes-19-now-operational-goes-east-providing-critical-new-data-forecasters
- [S17] NOAA GOES open data: https://noaa-goes19.s3.amazonaws.com/index.html and https://noaa-goes18.s3.amazonaws.com/index.html
- [S18] Li et al. 2020, RSE 237:111600: https://repository.library.noaa.gov/view/noaa/41607
- [S19] Vanunu et al. 2026, Remote Sens. 18:906: https://doi.org/10.3390/rs18060906
- [S20] Gao, Sun, Miao 2026, Remote Sens. 18:904: https://doi.org/10.3390/rs18060904
- [S21] Sofan et al. 2022, ISPRS Int. J. Geo-Inf. 11:601: https://doi.org/10.3390/ijgi11120601
- [S22] Freeborn et al. 2022, RSE 268:112777: https://www.fs.usda.gov/rm/pubs_journals/2022/rmrs_2022_freeborn_p001.pdf
- [S23] Chen et al. 2022 (FEDS), Sci. Data 9:249: https://pmc.ncbi.nlm.nih.gov/articles/PMC9151742/
- [S24] Cummins et al. 2023, Southeastern US Prescribed Fire Permit Database, Fire 6:372: https://mdpi-res.com/d_attachment/fire/fire-06-00372/article_deploy/fire-06-00372.pdf
- [S25] NOAA HMS fire and smoke product: https://www.ospo.noaa.gov/products/land/hms.html ; data: https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/2026/10/
- [S26] Watch Duty how it works: https://www.watchduty.org/how-it-works/overview
- [S27] ALERTCalifornia AI cameras: https://www.firerescue1.com/artificial-intelligence/over-1-000-ai-cameras-help-spot-calif-wildfires-before-the-first-911-call ; https://dronexl.co/2026/07/25/california-ai-cameras-firesat-drones-wildfire/
- [S28] DEA Hotspots: https://knowledge.dea.ga.gov.au/data/product/dea-hotspots/
- [S29] CWFIS Fire M3 metadata: https://cwfis.cfs.nrcan.gc.ca/datamart/metadata/fm3buffered
- [S30] USFS Active Fire Mapping Program (from search summaries): https://data.fs.usda.gov/geodata/maps/active-fire.php
- [S31] NIFC ICS-209 reporting thresholds: https://www.nifc.gov/sites/default/files/document-media/ICS-209%20When%20to%20Report%20Wildland%20Fire%20Incidents%20Flowchart.pdf
- [S32] WFIGS ArcGIS services: https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0 ; .../WFIGS_Incident_Locations/FeatureServer/0 ; .../WFIGS_Interagency_Perimeters/FeatureServer/0
- [S33] CARB burn-day page and Sacramento Valley allocation: https://ssl.arb.ca.gov/pfirs/cb3/cb3.php?id=3 ; https://ssl.arb.ca.gov/pfirs/met/allo/allo.pdf
- [S34] Butte County AQMD conditional rice straw permit 2024 and FAQ: https://bcaqmdca.specialdistrict.org/files/60e8f9dd8/365%2C+Rice+Burn+Permit+2024.pdf ; https://bcaqmdca.specialdistrict.org/burning-faqs
- [S35] Florida Forest Service active burn authorizations: https://gis.fdacs.gov/mapping/rest/services/FFS/ActiveOBAPoints/FeatureServer
- [S36] USDA NASS Cropland Data Layer: https://www.nass.usda.gov/Research_and_Science/Cropland/Release/index.php
- [S37] Annual NLCD: https://www.usgs.gov/centers/eros/news/annual-nlcd-lengthens-land-cover-record-40-years
- [S38] EOG VIIRS Nightfire and licensing: https://eogdata.mines.edu/products/vnf/ ; https://payneinstitute.mines.edu/viirs-nightfire-licensing/
- [S39] Global Industrial Heat Sources (Sci. Data 2024): https://pmc.ncbi.nlm.nih.gov/articles/PMC11178788/ (Zenodo 10.5281/zenodo.8308133)
- [S40] USGS US Large-Scale Solar PV Database v4.0: https://www.usgs.gov/data/united-states-large-scale-solar-photovoltaic-database-ver-40-april-2026
- [S41] EPA Envirofacts REST (from search summary): https://enviro.epa.gov/enviro/efservice/
- [S42] FIRMS standard-processing country-year CSVs: https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2024/viirs-snpp_2024_United_States.csv ; https://firms.modaps.eosdis.nasa.gov/data/country/viirs-jpss1/2024/viirs-jpss1_2024_United_States.csv
- [S43] Preprint on absent low-confidence night detections: https://arxiv.org/abs/2510.26816
- [S44] VIIRS 375 m vs Landsat-8 over Indonesian peat (search summary): https://www.tandfonline.com/doi/full/10.1080/17538947.2020.1791268
- [S45] Calf Canyon / Hermits Peak Fire: https://en.wikipedia.org/wiki/Calf_Canyon/Hermits_Peak_Fire ; https://www.kob.com/new-mexico/new-usfs-report-details-errors-with-prescribed-burn-that-led-to-calf-canyon-hermits-peak-fire/
- [S46] NGFS README (Feb 2025): https://bin.ssec.wisc.edu/pub/volcat/ngfs_docs/NGFS_README_Feb2025.pdf
- [S47] NOAA Wildland Fire Data Portal (NGFS): https://fire.data.nesdis.noaa.gov/ and https://fire.data.nesdis.noaa.gov/data/
- [S48] EFFIS active fire detection: https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/active-fire-detection
- [S49] NGFS public statements (about 1-minute alerts, 0.25-1 acre): https://www.preventionweb.net/news/noaa-unveils-powerful-convergence-ai-and-science-revolutionary-next-generation-fire-system ; https://cimss.ssec.wisc.edu/ngfs

### 7.2 Measurements (all run 2026-10-29; scripts in `...\scratchpad\tools\`, data in sibling folders; read-only on everything else)
- **[M1]** The session's FIRMS probe (`scratchpad\firms_probe\VIIRS_*.csv`: 7,287 VIIRS rows from 2026-10-28 00:00 UTC to 2026-10-29 18:42 UTC; 4,765 in the final 24 h) joined to current WFIGS points. Scripts `firms_stats.py`, `firms_join.py`, `firms_more.py`, `sac_night.py`, `sat_diff*.py`, `sat_pairs.py`, `funnel.py`. Clusters are 2 km greedy.
- **[M2]** FIRMS standard-processing 2024 US CSVs for S-NPP (484,736 rows, 468,103 in CONUS) and NOAA-20 (535,136 rows), `sp2024_analysis.py`, `gapcheck.py`, `sac_static_check.py`.
- **[M3]** WFIGS 2024 incidents (41,513 WF, 6,267 RX, 27 CX), perimeters of 2,000+ acres (310) and RX 100+ acres (154), current incidents; `sp2024_compose.py`.
- **[M4]** First-detection timing for 2024 wildfires of 2,000+ acres, S-NPP plus NOAA-20 (259 fires with detections): `sp2024_firstdet_multi.py`. S-NPP alone gives longer lags because of its July 2024 outage.
- **[M5]** Space-time episodes (within 1.5 km, at most 2 days), Sep-Nov 2024, S-NPP: `sp2024_episodes.py`.
- **[M6]** Park Fire: WFIGS origin and discovery time; GOES-18 and GOES-16 FDCC scans 21:46-22:11 UTC; NOAA-20 SP: `goes_parkfire.py`, `parkfire2.py`.
- **[M7]** GOES FDCC: 89 scans (44 GOES-19, 45 GOES-18, 34 MB) nearest in time to the probe detections; `goes_plan.py`, `goes_fetch.py`, `goes_match.py`, `goes_scan_counts.py`, `goes_box.py`, `goes_precision.py`. Latency from S3 listings.
- **[M8]** HMS daily file for 2026-10-28 (3 MB) against the same-day FIRMS probe: `hms_compare_t10.py`, `hms_compare2.py`.
- **[M9]** Distance from S-NPP 2024 detections to final perimeters of 310 large wildfires: `sp2024_outside.py`.

**Limitations.** (1) Everything about 2024 is S-NPP (and NOAA-20 for timing) standard processing; the NRT feed may differ slightly. (2) "Unexplained" is not "false alarm" (section top). (3) The 3 km WFIGS point match under-counts large-fire spread; perimeters exist for 2,000+ acre fires only. (4) The forward static-mask test uses one year (H1 to H2); other years and regions should be backtested. (5) WFIGS discovery times are noisy (20% of large fires had a satellite detection before the recorded discovery). (6) The HMS comparison is confounded by HMS using NOAA's own VIIRS processing. (7) I did not test the CDL (1.9 GB) or pull NGFS data, and I could not verify a CWFIS static filter or any Florida API access. (8) Downloads: each file stayed under 50 MB (largest 43.8 MB) but the cumulative volume was about 125 MB, all under the scratchpad.

---

## 8. Suggested protocol structure

**Tiers and ceilings**

| Tier | Entry | May trigger |
|---|---|---|
| **H0 Heat only** | C1 static cell (no guard tripped), C2 over water, C3 after about 5 stable nights, solar-polygon daytime singleton, lone `l` without support | Log and map only. No incident, no forecast, no alert. Re-arms to P1 on any guard. |
| **P1 Potential fire** | Every other non-attached cluster. Labels: `ag_presumed` (C9), `rx_registered` (C10), `swath_edge_single` (C8), `urt_only` (C20), `stationary_night` (C3) | Synthetic incident; provisional forecast for clusters within 60 km of clients; client ceiling NOTICE_72H; no crew dispatch; TTL 24-48 h; analyst page if within 3 km of a home (C19) |
| **P2 Probable fire** | P1 plus any of: re-detection in a later window or non-static night detection (C12, C13); second-satellite agreement with 2+ pixels or growth (C14, C17); GOES FDC high class or NGFS vegetation-fire pixel (C15); FRP at least 20 MW or 6+ pixels outside ag/RX context; analyst says real | Full ensemble forecast; ladder up to WATCH, EXTREME_24H when forecast arrival is within 24 h; crew pre-staging; dispatch only with human approval |
| **CF Confirmed fire** | Attached to an agency incident or perimeter (WFIGS WF/CX, CAL FIRE, FIRIS) under C18, or confirmed by analyst, camera or dispatch | Full ladder including IMPACTED and dispatch |

**Cross-cutting rules**
1. **Never silent.** H0, `ag_presumed` and `rx_registered` stay in the log, on the map and in the re-arm loop.
2. **Escalators beat filters.** A non-H0 cluster within 3 km of a client home gets a human check regardless of labels (C19). A GOES/NGFS-only large new fire gets one too (C16).
3. **Expiry requires observability.** Close only after two consecutive missed re-detections with GOES clear-sky evidence, or by a human; otherwise "unresolved" (C22).
4. **Feed hygiene.** Dedupe on lat/lon/time, not `version`; URT-only cannot exceed P1 (C20); count cluster area, not pixels; per-satellite thresholds (NOAA-20 vs NOAA-21 vs S-NPP differ); plan the **2026-11-02 13:00 UTC** S-NPP cutover and the loss of the third look.
5. **Build order.** (1) C1/C2 mask from the SP archive plus local accumulation, (2) tier labels and the proximity escalator, (3) GOES FDCC ingestion from S3, (4) WFIGS RX and CDL/NLCD labels, (5) backtest on 2024 using WFIGS truth: report time-to-P2 for the 259 large fires, the number of detections suppressed inside wildfire perimeters (target zero), and P2 volume per day near clients.
