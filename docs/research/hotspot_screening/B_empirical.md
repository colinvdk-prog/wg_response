# FIRMS hotspot screening: empirical measurements (researcher B)

Prepared 2026-10-29 for the WG Response screening-protocol work. Everything below was measured on real data; numbers are copied from the
scripts' outputs (`B_*.py`, `B_*.json` in this folder). The folder `scratchpad\hotspot_screen\` holds every script, intermediate file and the 5-day FIRMS pull (`B_data5\`).

## 0. Bottom line

1. **Baseline reproduced exactly.** With the analysis clock fixed at 2026-10-29 18:45 UTC, the last 24 h hold **4,763 VIIRS detections in 1,312 clusters**; my index-keeping
   re-implementation of `FirmsFeed._cluster` returns identical cluster ids and sizes to the repo code. **27 detections / 5 clusters** lie on the live reported wildfires (1 km perimeter buffer; identical at 0.5 and 2 km),
   and **26 clusters** have their centroid within 60 km of one of the 520 demo homes.
2. **What the 1,312 clusters are.** 5 are on live reported wildfires (WFIGS/FIRIS/CAL FIRE); 9 more are on a reported wildfire in the WFIGS year-to-date layer (any containment state); **39 (3.0 %, 381 detections = 8 %) are on a reported prescribed (RX) burn**
   that `WfigsFeed` deliberately drops, so FIRMS re-creates them as orphan fires; **1,261 (96 %) are on nothing reported**. NLCD 2021 sampled at all 1,312 clusters puts 26 % on cultivated crops (34 % with pasture/hay), 19 % on developed land and 22 % on forest.
3. **The near-client set is mostly agriculture and industry, not wildland.** Of the 26 near-client clusters 15 (58 %; 16 of 26 by 5-point majority) sit on cultivated crops, versus 20 % in a random 100 other clusters; 4 are single-pixel night detections of FRP < 2 MW on developed land.
   Over the four 24-h windows (W0-W3) the near-client count is 26 / 28 / 30 / 20 clusters per day; in W1 and W2 about a third of them are nightly refinery/flare/landfill sites around Los Angeles, San Diego, the Bay Area and Denver.
4. **No single threshold separates reported wildfire from the rest.** Pooled over W0-W3, the 45 clusters on any reported wildfire have median max-FRP 3.6 MW (all clusters 3.4, unexplained 3.3) and median 2 detections (all: 2).
   They are only mildly more persistent (56 % seen on >= 2 orbit passes vs 44 % of all clusters) and more often involve a night detection (49 % vs 43 %; the 15 live-fire clusters 73 %).
5. **FRP thresholds are day/night filters and the wrong way round for ag burns.** FRP >= 5 MW keeps 3.2 % of night detections and 69 % of day detections (FRP >= 10: 0.7 % / 42 %). It removes 57 % of clusters, 11 of 26 near-client clusters and 3 of the 5 known fires (10 of 15 pooled).
   High-FRP clusters are disproportionately cropland: FRP >= 20 clusters are 42 % cropland, and 60 % if seen on one pass only; high-FRP clusters with a night detection are 4 % cropland / 73 % forest.
6. **Confidence is nearly uninformative.** Low confidence occurs only by day (215 of 215), only 12 clusters are all-low, and such events are re-observed 8 % of the time. High confidence is the saturated I-4 pixel (205 of 205 have bright_ti4 = 367 K), is 98 % daytime, and clusters with it are 49 % cropland. "High only" removes 91 % of clusters and loses 4 of 5 known fires: reject.
7. **Persistence (>= 2 orbit passes) is cheap in time but removes a lot.** It drops 704 of 1,312 clusters (54 %; 21 % of detections), 12 of 26 near-client clusters, and loses 2 of 5 known-fire clusters (6 of 15 pooled; 20 of 45 reported-wildfire clusters pooled). Of new events with 24 h of follow-up, **47 % are ever seen again;
   when they are, the median gap is 0.62 h and 81 % come within 1 h** (the other satellites, 20-45 min later); the remainder come 12 h later. It does not preferentially remove cropland (28 % of removed clusters vs 23 % of kept). Use it to **tier** clusters, not to delete them.
8. **The one clear hard-filter candidate is the static/industrial signature** (same 750 m site on >= 3 of the last 5 UTC dates, >= 80 % night, site FRP <= 5 MW, <= 8 detections/day): **10.7 % of clusters and 8.4 % of detections in W0 (11.3 % / 9.1 % pooled W0-W2)**, 0 of 10 known-fire clusters (W0-W2) and 0 of 86 RX clusters lost,
   and 23 of 84 near-client clusters pooled (2 of 26 in W0, 10 of 28 in W1, 11 of 30 in W2). Four reported-wildfire clusters pooled are touched; all are two LA County incident ids sitting on nightly-recurring refinery-district flares (chance coincidence in a dense incident area).
9. **A layered shape is measurably gentler than any gate.** Industrial signature dropped; all-low and tier D (one pixel, one pass, FRP < 5 MW) display-only; clusters within 10 km of a home exempt: 60 % of W0 clusters (84 % of detections) stay forecast-eligible, 20 of 26 near-client clusters stay, 4 of 5 known fires stay (pooled W0-W2: 57 % of clusters, 82 % of detections, 52 of 84 near-client, 8 of 10 known).
10. **Implementation findings.** (a) `day_range=5` returned exactly five UTC dates (10-25 ... 10-29), so `FirmsFeed`'s default `day_range=1` means "today (UTC) only": a persistence or size rule would have no memory just after 00:00 UTC. Pull >= 2 days (>= 5 for a static mask) and window by timestamp. (b) URT rows are the newest 0-2 h (`version` 2.xURT, 6.5 % of the 5-day pull); the newest granule is ingested over minutes (18 of 20 late rows were one N20 granule at 18:43 Z), so the first poll of a pass can see a partial cluster.
   (c) RX burns are more persistent and hotter than the average cluster (W0: 77 % of the 39 RX clusters have >= 2 orbit passes vs 46 % of all; pooled W0-W3 median FRP 9.4 MW vs 3.4): persistence removes only 23 % of them and FRP >= 5 only 26 %, so only registry matching removes them.

**Limits, stated up front.** One week of late-October data; W0 is the busiest of the four days (4,763 detections vs 2,257-3,482). The reported-wildfire proxy is tiny (5 live fires / 27 detections in W0; 15 cluster-days over W0-W3; 45 including the year-to-date layer) and is by construction the set of fires that agencies already know, which is the set FIRMS adds least to.
There is no ground truth for unreported starts, so "removed" below never means "false alarm", only "not like the reported fires / removed from the stream". Parameters of the static rule were chosen on the same three days they are evaluated on (the parameter sweep is a plateau, but there is no holdout).

## 1. Data and method

| Item | What was used |
|---|---|
| Detections | VIIRS S-NPP, NOAA-20, NOAA-21 NRT/URT, CONUS box (-125, 24, -66, 50). W0 uses the two-day files already in `firms_probe\` (reproduces the brief); W1-W3 and all history use my 5-day pull (`B_data5\`, 4 FIRMS requests: 3 VIIRS + MODIS_NRT; 15,224 VIIRS rows, 880 MODIS rows, UTC dates 10-25 ... 10-29). No duplicate rows. |
| Windows | W0 = 24 h ending 2026-10-29 18:45 UTC; W1, W2, W3 = the preceding three non-overlapping 24-h windows (ending 10-28, 10-27, 10-26 18:45 UTC). W0 4,763 dets / 1,312 clusters; W1 3,482 / 1,101; W2 2,898 / 845; W3 2,257 / 722. |
| Clustering | The engine's greedy 2 km centroid algorithm (newest first), re-implemented with a grid index so that detection indices are kept; verified identical to `FirmsFeed._cluster` on W0 (same 1,312 ids and sizes). |
| Pass | `d['orbit']` = satellite + orbit crossing: per satellite, distinct acquisition times are split wherever the gap exceeds 40 min (orbits are ~101 min apart, one CONUS crossing spans <= ~10 min). The literal "distinct acq_date + acq_time" gives the **same counts in W0** (704 single-pass clusters) and differs by one cluster pooled; both are in the tables (P1/P2). |
| Known fires (the brief's proxy) | Live fused WFIGS + FIRIS + CAL FIRE (`fuse_incidents`), active and updated within 168 h: 219 fires (90 with perimeters) fetched at 18:57 Z (the brief had 217). Attach rule as in the probe: within 1 km of a perimeter (inside = 0), or within max(2 km, 2 x equivalent radius) + 1 km of a point. W0: 27 detections on 5 fires. The same live list is used for W1-W3, so fires that were active earlier but have since dropped out are missed. |
| Extended truth ("reported WF", "RX") | WFIGS **YearToDate** incident points discovered since 10-18 or modified since 10-22, any containment state: 3,410 (2,535 WF, 875 RX). Same attach radius; incident discovered <= 24 h after the detection and not out > 48 h before it. Live RX (490 records in the current layer; the adapter drops them) added. Reported WF = live known **or** extended WF. |
| Near-client | Cluster centroid within 60 km of any of the 520 homes of `preset_client_registry(40)` (13 communities: 8 in CO, 4 in CA, 1 in UT). |
| Land cover | USGS MRLC NLCD 2021 via public WMS GetFeatureInfo (no key), 30 m pixel at each cluster's max-FRP detection: **all 1,312 W0 clusters** (4 threads, 268 s, 0 errors). Plus a 5-point sample (centre and 4 x 200 m) for the 26 near-client and a random 100 others: single-point and 5-point majority class agree for 111 of 126. |
| New events | Greedy 2 km clusters over all 5 days; kept if the first detection falls in [10-26 00:00 Z, 10-28 18:45 Z] (>= 24 h of earlier quiet and >= 24 h of follow-up exist): **1,569 events** (33 near-client). Used for time-to-confirm and re-observation rates. |
| Static sites | Greedy 750 m clusters of all detections up to the window end; "days" = distinct UTC dates, "night fraction" = share of night detections, per-site FRP maximum and maximum detections on any one day. Evaluated at each window's own clock (W0 five dates of history, W1 four, W2 three). |
| Rule semantics | Every rule is applied to the whole 24-h cluster ("any detection passes" for detection-level criteria), so thinning of clusters is not modelled. Detection-level (pre-cluster) versions are in section 3.5. A cluster is "lost" if the rule removes it. |

Reading the numbers: *removed* means removed from the stream of potential fires. "Pending" matters for persistence: 188 of the 704 W0 clusters that fail >= 2 passes were first seen < 6 h before the window end and may still be confirmed; none of the 12 near-client failures is pending (all were first seen >= 9 h ago; 21 of the 26 near-client clusters were first seen 21-23 h before 18:45 Z and are about to age out of the 24-h window).

## 2. Baseline composition (W0)

| Class (mutually exclusive, in this order) | Clusters | Share |
|---|---|---|
| on a live reported wildfire (WFIGS/FIRIS/CAL FIRE) | 5 | 0.4 % |
| on a reported wildfire in the WFIGS YTD layer only | 9 | 0.7 % |
| on a reported RX burn only | 37 (39 counting the two that also touch a reported wildfire) | 2.8 % |
| on nothing reported ("unexplained") | 1,261 | 96.1 % |

Per-window counts (W0 / W1 / W2 / W3): clusters 1,312 / 1,101 / 845 / 722; near-client 26 / 28 / 30 / 20; live known-fire clusters 5 / 2 / 3 / 5; reported-WF clusters (live + extended) 14 / 5 / 12 / 14; RX-explained 39 / 24 / 23 / 6.
Cluster size in W0: 1 detection 534 (41 %), 2: 254, 3: 146, 4-5: 165, 6-10: 124, 11-25: 76, >= 26: 13. Orbit passes: 1: 704 (54 %), 2: 328, 3: 157, 4-5: 87, >= 6: 36. Satellites: 1: 722, 2: 346, 3: 244. Day-only 854 (65 %), night-only 347 (26 %), both 111 (8 %).
Clusters within 5 / 10 / 20 / 30 / 60 km of a demo home (W0): 1 / 2 / 4 / 7 / 26.

## 3. Single-criterion results

### 3.0 One row per criterion (W0, the brief's day; pooled and per-rule detail in 3.1-3.3)

| Criterion | Rule (what is removed) | CONUS clusters removed (of 1,312) | CONUS dets in removed clusters (of 4,763) | Near-client clusters removed (of 26) | Near-client dets removed (of 88) | Known-fire clusters lost (of 5) | Reported-WF clusters lost (live+ext, of 14) | RX clusters removed (of 39) | Time-to-confirm cost | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|
| 1a Confidence: drop low | remove clusters whose detections are ALL low confidence | 12 (0.9%) | 12 (0.3%) | 0 | 0 | 0 | 0 | 0 | none (97.6% of new events pass at first look) | display-only until a n/h detection or 2nd pass (hard drop safe but <1% yield) |
| 1b Confidence: high only | remove clusters with no high-confidence (saturated I-4) detection | 1199 (91.4%) | 3,665 (76.9%) | 23 | 60 | 4 | 13 | 32 | only 9.8% of new events ever pass | REJECT |
| 2a FRP >= 5 MW | remove clusters whose max detection FRP < 5 MW | 742 (56.6%) | 1,634 (34.3%) | 11 | 17 | 3 | 6 | 10 | 42% ever pass; 576 of 659 pass at first look | REJECT as gate (it is a day/night filter) |
| 2b FRP >= 10 MW | remove clusters whose max detection FRP < 10 MW | 988 (75.3%) | 2,441 (51.2%) | 18 | 37 | 4 | 11 | 19 | 25% ever pass | REJECT as gate; promoter only with persistence/night |
| 2c FRP >= 20 MW | remove clusters whose max detection FRP < 20 MW | 1128 (86.0%) | 3,152 (66.2%) | 22 | 57 | 4 | 13 | 29 | 14% ever pass | REJECT as gate; do NOT fast-track alone (42% cropland) |
| 2d Cluster FRP sum >= 10 MW | remove clusters whose summed FRP < 10 MW | 792 (60.4%) | 1,286 (27.0%) | 15 | 23 | 4 | 10 | 9 | 39% ever pass | REJECT |
| 3a Cluster size >= 2 | remove single-detection clusters | 534 (40.7%) | 534 (11.2%) | 9 | 9 | 2 | 5 | 5 | 58% of new events ever pass in 24 h; median wait 0.63 h | REJECT as gate; input to tier D/U |
| 3b Cluster size >= 3 | remove clusters with < 3 detections | 788 (60.1%) | 1,042 (21.9%) | 16 | 23 | 2 | 9 | 15 | 39% of new events ever pass in 24 h; median wait 0.73 h | REJECT |
| 4a Persistence >= 2 passes | remove clusters seen on only one satellite orbit pass in 24 h | 704 (53.7%) | 994 (20.9%) | 12 | 15 | 2 | 7 | 9 | 47% of new events ever pass in 24 h; median wait 0.62 h | TIER GATE: unconfirmed = ceiling / not forecast; never silently deleted |
| 4b Persistence >= 2 satellites | remove clusters seen by only one VIIRS satellite in 24 h | 722 (55.0%) | 1,058 (22.2%) | 12 | 15 | 2 | 7 | 11 | 44% of new events ever pass in 24 h; median wait 0.60 h | same as 4a (p90 wait only 1.4 h) |
| 5a Static/industrial signature | remove clusters whose every detection is in a 750 m site seen on >=3 of last 5 UTC dates, >=80% night, site FRP <= 5 MW, <= 8 dets/day | 140 (10.7%) | 400 (8.4%) | 2 | 3 | 0 | 0 | 0 | none (history is known at first look) | HARD FILTER with release valve |
| 5b Persistent site only (no night/FRP test) | remove clusters with any detection in a 750 m site seen on >=3 of last 5 UTC dates | 243 (18.5%) | 1,261 (26.5%) | 4 | n/a | 1 | 1 | 5 | none | TIER only (flags long-lived real fires as often as industrial) |
| 6a Night detections only | remove clusters with no night detection (keep night-involved) | 854 (65.1%) | 2,575 (54.1%) | 19 | 61 | 1 | 9 | 23 | waits for next night pass (~12 h after an afternoon start; not measured) | REJECT |
| 6b Day detections only | remove clusters with no day detection (night-only) | 347 (26.4%) | 838 (17.6%) | 5 | 6 | 3 | 4 | 6 | n/a | REJECT (3 of 5 known fires are night-only) |
| 7a Combo K1 | remove unless (n or h) AND (>=2 passes OR FRP>=10) | 613 (46.7%) | 750 (15.7%) | 10 | 12 | 2 | 6 | 7 | 55% of new events ever pass in 24 h; median wait 0.62 h | tier gate candidate |
| 7b Combo K2 | remove unless (n or h) AND (>=2 dets OR FRP>=10) | 506 (38.6%) | 506 (10.6%) | 8 | 8 | 2 | 5 | 4 | 60% of new events ever pass in 24 h; median wait 0.63 h | tier gate candidate (lightest) |
| 7c Combo K6 | remove unless >=3 dets OR >=2 passes OR FRP>=10 | 597 (45.5%) | 689 (14.5%) | 10 | 12 | 2 | 6 | 6 | 57% of new events ever pass in 24 h; median wait 0.62 h | tier gate candidate |
| 7d Layered L | drop industrial signature + all-low + tier D (1 pixel, 1 pass, FRP<5) unless centroid <10 km from a home | 524 (39.9%) | 784 (16.5%) | 6 | 7 | 1 | 3 | 4 | D tier: 47% of new events ever get a 2nd pass; median wait 0.62 h | RECOMMENDED SHAPE: IS hard, D/low display-only, 10 km exempt |
| 8a Pixel size (near-nadir only) | remove clusters with no pixel < 0.25 km2 | 128 (9.8%) | 326 (6.8%) | 1 | 2 | 1 | 1 | 1 | n/a | REJECT (larger pixels are re-observed MORE often) |
| 8b MODIS corroboration added | remove unless >=2 VIIRS passes OR a MODIS detection <=2.5 km, +-3 h | 689 (52.5%) | 969 (20.3%) | 12 | 15 | 2 | 7 | 9 | n/a (MODIS adds only 15 clusters) | optional |
| 8c Regional swarm | remove clusters with >=3 other clusters within 25 km and +-6 h | 236 (18.0%) | 981 (20.6%) | 10 | 35 | 0 | 0 | 1 | none | ceiling modifier only (weak) |
| 8d Cropland + no night | remove clusters on NLCD cultivated crops (82) with no night detection | 300 (22.9%) | 970 (20.4%) | 13 | 42 | 0 | 0 | 0 | none | ceiling modifier (use the engine's own fuel layer); not a silent drop |

Known-fire clusters in W0 (the 5): Kelsey (17 dets, 6 passes, 3 sats, FRP 118.6 MW, day+night), BEE HAVEN (2) (4 dets, 2 passes, FRP 1.9, night), Skid Road (4 dets, 2 passes, FRP 2.7, night), South Tank Trail (1 det, 1 pass, FRP 7.0, day), MUSE (1 det, 1 pass, FRP 1.2, night).
Every persistence or size rule loses the two single-detection fires (South Tank Trail, MUSE); every FRP >= 5 rule loses three (BEE HAVEN, Skid Road, MUSE). Per-fire matrix over all four windows in 3.7.

### 3.1 All single criteria (W0 and pooled W0-W3)

| Rule id | Keep a cluster if ... | CONUS clusters removed (of 1312) | CONUS dets in removed clusters (of 4,763) | Near-client removed (of 26) | Known-fire clusters lost (of 5) | Reported-WF clusters lost, live+ext (of 14) | Pooled W0-W3: clusters removed | Pooled: near-client removed (of 104) | Pooled: known lost (of 15) | Pooled: reported-WF lost (of 45) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 no-low | keep if >=1 detection n or h (drop all-low clusters) | 12 (0.9%) | 12 (0.3%) | 0 | 0 | 0 | 54 (1.4%) | 0 | 0 | 0 | display-only (hard drop is safe here but yields <1%) |
| C2 high-only | keep if >=1 detection h | 1,199 (91.4%) | 3,665 (76.9%) | 23 | 4 | 13 | 3,698 (92.9%) | 92 | 14 | 42 | REJECT |
| F1 frp>=1 | keep if max detection FRP >= 1 MW | 180 (13.7%) | 268 (5.6%) | 3 | 0 | 0 | 721 (18.1%) | 26 | 2 | 5 | display-only for night <1 MW; not a hard filter |
| F1 frp>=2 | keep if max detection FRP >= 2 MW | 368 (28.0%) | 654 (13.7%) | 6 | 2 | 3 | 1,355 (34.0%) | 48 | 7 | 18 | REJECT as filter |
| F1 frp>=3 | keep if max detection FRP >= 3 MW | 514 (39.2%) | 1,001 (21.0%) | 7 | 3 | 4 | 1,849 (46.5%) | 57 | 9 | 22 | REJECT as filter |
| F1 frp>=5 | keep if max detection FRP >= 5 MW | 742 (56.6%) | 1,634 (34.3%) | 11 | 3 | 6 | 2,500 (62.8%) | 64 | 10 | 29 | REJECT as filter |
| F1 frp>=10 | keep if max detection FRP >= 10 MW | 988 (75.3%) | 2,441 (51.2%) | 18 | 4 | 11 | 3,158 (79.3%) | 77 | 13 | 38 | REJECT as filter (use only as a promoter) |
| F1 frp>=20 | keep if max detection FRP >= 20 MW | 1,128 (86.0%) | 3,152 (66.2%) | 22 | 4 | 13 | 3,539 (88.9%) | 90 | 13 | 41 | promoter only (fast-track), never a gate |
| F2 sumfrp>=5 | keep if cluster FRP sum >= 5 MW | 587 (44.7%) | 838 (17.6%) | 8 | 1 | 4 | 1,983 (49.8%) | 53 | 6 | 22 | REJECT as filter |
| F2 sumfrp>=10 | keep if cluster FRP sum >= 10 MW | 792 (60.4%) | 1,286 (27.0%) | 15 | 4 | 10 | 2,601 (65.4%) | 68 | 9 | 31 | REJECT as filter |
| F2 sumfrp>=20 | keep if cluster FRP sum >= 20 MW | 956 (72.9%) | 1,867 (39.2%) | 18 | 4 | 12 | 3,069 (77.1%) | 76 | 10 | 35 | REJECT as filter |
| F2 sumfrp>=50 | keep if cluster FRP sum >= 50 MW | 1,104 (84.1%) | 2,695 (56.6%) | 21 | 4 | 12 | 3,488 (87.6%) | 89 | 14 | 41 | promoter only |
| S1 n>=2 | keep if cluster has >=2 detections | 534 (40.7%) | 534 (11.2%) | 9 | 2 | 5 | 1,673 (42.0%) | 34 | 5 | 15 | REJECT as filter; tier input |
| S2 n>=3 | keep if cluster has >=3 detections | 788 (60.1%) | 1,042 (21.9%) | 16 | 2 | 9 | 2,476 (62.2%) | 53 | 5 | 24 | REJECT as filter |
| P1 lit-passes>=2 | keep if >=2 distinct acq_date+acq_time (literal) | 704 (53.7%) | 994 (20.9%) | 12 | 2 | 7 | 2,220 (55.8%) | 42 | 6 | 20 | same as P2 in this data |
| P2 orbits>=2 | keep if >=2 distinct satellite orbit passes | 704 (53.7%) | 994 (20.9%) | 12 | 2 | 7 | 2,221 (55.8%) | 42 | 6 | 20 | TIER GATE (unconfirmed = ceiling), not a drop |
| P3 sats>=2 | keep if >=2 distinct VIIRS satellites | 722 (55.0%) | 1,058 (22.2%) | 12 | 2 | 7 | 2,266 (56.9%) | 42 | 6 | 20 | TIER GATE (equivalent to P2) |
| P4 orbits>=3 | keep if >=3 distinct orbit passes | 1,032 (78.7%) | 2,219 (46.6%) | 18 | 4 | 12 | 3,154 (79.2%) | 71 | 10 | 34 | REJECT |
| N1 night-only | keep only clusters with >=1 night detection | 854 (65.1%) | 2,575 (54.1%) | 19 | 1 | 9 | 2,285 (57.4%) | 47 | 4 | 23 | REJECT |
| N2 day-only | keep only clusters with >=1 day detection | 347 (26.4%) | 838 (17.6%) | 5 | 3 | 4 | 1,414 (35.5%) | 48 | 9 | 19 | REJECT |
| X1 nadir pix<0.25 | keep if smallest pixel in cluster has scan*track < 0.25 km2 (near-nadir) | 128 (9.8%) | 326 (6.8%) | 1 | 1 | 1 | 386 (9.7%) | 14 | 2 | 2 | REJECT |
| X2 saturated ti4 | keep if any pixel I-4 saturated (>=367 K) | 1,194 (91.0%) | 3,633 (76.3%) | 23 | 4 | 13 | 3,689 (92.7%) | 92 | 14 | 42 | REJECT (= high-only) |
| X3 not-saturated | keep if no pixel I-4 saturated (<367 K only) | 118 (9.0%) | 1,130 (23.7%) | 3 | 1 | 1 | 291 (7.3%) | 12 | 1 | 3 | REJECT |
| P5 orbits>=2 or modis | keep if >=2 VIIRS orbit passes OR a MODIS (Terra/Aqua) detection within 2.5 km and +-3 h | 689 (52.5%) | 969 (20.3%) | 12 | 2 | 7 | 2,176 (54.7%) | 42 | 6 | 20 | optional add-on (+15 clusters only) |

P1 and P2 differ by one cluster pooled (a cluster straddling two acquisition minutes inside one orbit pass).

### 3.2 Combinations

| Rule id | Keep a cluster if ... | CONUS clusters removed | CONUS dets in removed | Near-client removed (of 26) | Known lost (of 5) | Reported-WF lost (of 14) | Pooled clusters removed | Pooled near-client removed (of 104) | Pooled known lost (of 15) | Pooled reported-WF lost (of 45) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| K1 nh&(orb>=2 or frp>=10) | (n or h) AND (>=2 orbit passes OR FRP>=10) | 613 (46.7%) | 750 (15.7%) | 10 | 2 | 6 | 1,902 (47.8%) | 35 | 6 | 18 | tier gate candidate |
| K2 nh&(n>=2 or frp>=10) | (n or h) AND (>=2 detections OR FRP>=10) | 506 (38.6%) | 506 (10.6%) | 8 | 2 | 5 | 1,590 (39.9%) | 32 | 5 | 14 | tier gate candidate (lightest) |
| K3 orb>=2 or frp>=20 | >=2 orbit passes OR FRP>=20 | 649 (49.5%) | 841 (17.7%) | 11 | 2 | 7 | 2,052 (51.6%) | 39 | 6 | 20 | tier gate candidate |
| K4 nh&orb>=2 | (n or h) AND >=2 orbit passes | 704 (53.7%) | 994 (20.9%) | 12 | 2 | 7 | 2,224 (55.9%) | 42 | 6 | 20 | = P2 (the n/h clause removes nothing extra) |
| K5 sats>=2 or frp>=10 | >=2 satellites OR FRP>=10 | 627 (47.8%) | 804 (16.9%) | 10 | 2 | 6 | 1,934 (48.6%) | 35 | 6 | 18 | tier gate candidate |
| K6 n>=3 or orb>=2 or frp>=10 | >=3 detections OR >=2 orbit passes OR FRP>=10 | 597 (45.5%) | 689 (14.5%) | 10 | 2 | 6 | 1,838 (46.2%) | 35 | 5 | 16 | tier gate candidate (lightest persistence form) |
| K7 orb>=2 or n>=2&frp>=5 | >=2 orbit passes OR (>=2 detections AND FRP>=5) | 592 (45.1%) | 662 (13.9%) | 10 | 2 | 5 | 1,852 (46.5%) | 36 | 6 | 18 | tier gate candidate |
| K8 nh&(orb>=2 or n>=3 or frp>=20) | (n or h) AND (>=2 passes OR >=3 dets OR FRP>=20) | 620 (47.3%) | 721 (15.1%) | 11 | 2 | 6 | 1,935 (48.6%) | 38 | 5 | 17 | tier gate candidate |
| K9 nh&(orb>=2 or modis or frp>=10) | (n or h) AND (>=2 orbit passes OR MODIS corroboration OR FRP>=10) | 605 (46.1%) | 741 (15.6%) | 10 | 2 | 6 | 1,878 (47.2%) | 35 | 6 | 18 | tier gate candidate (needs MODIS feed) |
| T-strong | FRP>=20 OR >=5 detections OR >=3 orbit passes | 871 (66.4%) | 1,372 (28.8%) | 16 | 4 | 11 | 2,719 (68.3%) | 65 | 7 | 28 | promoter (fast-track), never a gate |

K4 equals P2 in W0 (pooled it removes three more clusters: multi-pass clusters whose detections are all low confidence). K6 is identical to "tier moderate-or-better". None is free: every combination that keeps the "unconfirmed" pool out loses 5-6 of 15 known-fire clusters pooled.

### 3.3 Time to confirm and eventual confirmation (1,569 new events; first detection 10-26 00:00 Z - 10-28 18:45 Z; follow-up window 24 h)

| Rule id | New events confirmed within 24 h | Confirmed in the same pass (0 h) | Confirmed after a wait: n | median wait (h) | p90 wait (h) | Near-client events confirmed | Known-fire events confirmed |
|---|---|---|---|---|---|---|---|
| C1 no-low | 1,532 / 1,569 (97.6%) | 1,525 | 7 | 0.57 | 9.8 | 33 / 33 | 3 / 3 |
| C2 high-only | 153 / 1,569 (9.8%) | 96 | 57 | 1.07 | 23.3 | 9 / 33 | 0 / 3 |
| F1 frp>=5 | 659 / 1,569 (42.0%) | 576 | 83 | 0.63 | 12.3 | 19 / 33 | 0 / 3 |
| F1 frp>=10 | 395 / 1,569 (25.2%) | 327 | 68 | 0.63 | 22.5 | 15 / 33 | 0 / 3 |
| F1 frp>=20 | 215 / 1,569 (13.7%) | 161 | 54 | 0.63 | 22.8 | 9 / 33 | 0 / 3 |
| S1 n>=2 | 907 / 1,569 (57.8%) | 462 | 445 | 0.63 | 23.1 | 24 / 33 | 2 / 3 |
| S2 n>=3 | 610 / 1,569 (38.9%) | 195 | 415 | 0.73 | 22.5 | 22 / 33 | 1 / 3 |
| P2 orbits>=2 | 737 / 1,569 (47.0%) | 0 | 737 | 0.62 | 12.7 | 21 / 33 | 1 / 3 |
| P3 sats>=2 | 697 / 1,569 (44.4%) | 0 | 697 | 0.60 | 1.4 | 21 / 33 | 0 / 3 |
| P4 orbits>=3 | 375 / 1,569 (23.9%) | 0 | 375 | 1.07 | 23.1 | 13 / 33 | 0 / 3 |
| K1 nh&(orb>=2 or frp>=10) | 860 / 1,569 (54.8%) | 326 | 534 | 0.62 | 13.6 | 25 / 33 | 1 / 3 |
| K2 nh&(n>=2 or frp>=10) | 946 / 1,569 (60.3%) | 552 | 394 | 0.63 | 22.7 | 25 / 33 | 2 / 3 |
| K5 sats>=2 or frp>=10 | 830 / 1,569 (52.9%) | 327 | 503 | 0.62 | 1.4 | 25 / 33 | 0 / 3 |
| K6 n>=3 or orb>=2 or frp>=10 | 887 / 1,569 (56.5%) | 379 | 508 | 0.62 | 12.8 | 25 / 33 | 2 / 3 |
| K7 orb>=2 or n>=2&frp>=5 | 861 / 1,569 (54.9%) | 325 | 536 | 0.63 | 18.1 | 24 / 33 | 1 / 3 |
| K8 nh&(orb>=2 or n>=3 or frp>=20) | 837 / 1,569 (53.3%) | 268 | 569 | 0.62 | 13.0 | 24 / 33 | 2 / 3 |

Known-fire events: only 3 in this population, so the last column is anecdotal. "Confirmed in the same pass" means the rule is true at the first moment the cluster is visible (cost 0).

Second-look gap (any later orbit pass, any satellite): **737 of 1,569 new events (47.0 %) are seen again within 24 h**; 601 (38.3 % of all, 81.5 % of the re-observed) within 1 h, 634 within 2 h, 655 within 12 h. Quantiles of the gap among the re-observed: p10 0.32 h, p25 0.35 h, **p50 0.62 h**, p75 0.73 h, p90 12.7 h.
The 0.3-0.75 h cluster is the other two satellites (S-NPP, NOAA-20 and NOAA-21 cross a given longitude within ~20-60 min of each other); the 12 h tail is the next day or night pass. Time is acquisition time; URT delivery adds minutes, NRT up to ~3 h.

Which events get a second look (re-observation by what the first 24 h showed):

| First-24 h max FRP | New events | Seen on >=2 orbit passes within 24 h |
|---|---|---|
| <1 MW | 295 | 77 (26%) |
| 1-2 MW | 187 | 88 (47%) |
| 2-5 MW | 428 | 150 (35%) |
| 5-10 MW | 264 | 153 (58%) |
| 10-20 MW | 180 | 111 (62%) |
| 20-50 MW | 142 | 101 (71%) |
| 50+ MW | 73 | 57 (78%) |

| Best confidence in cluster | New events | Seen on >=2 orbit passes |
|---|---|---|
| high (saturated I-4) | 153 | 136 (89%) |
| nominal | 1379 | 598 (43%) |
| low (all detections low) | 37 | 3 (8%) |

Other cuts of the same 1,569 events: first look by day 47.3 % re-observed, by night 46.3 %; by first satellite N21 55 %, S-NPP 43 %, NOAA-20 40 %; by size of the first-look pixel 32 % (< 0.17 km2, near nadir) rising to 69 % (0.25-0.35 km2): larger off-nadir pixels are re-observed *more* often, so pixel size is not a false-alarm discriminator;
by reported class: reported WF 50 % (12 events), RX 51 % (39), unexplained 47 % (1,518). 57 single-orbit events had a 24-h max FRP >= 20 MW (all daytime, 2 near-client): intense, short burns.

### 3.4 Static or persistent heat (5-day pull)

Sites: 3,580 greedy 750 m sites over 15,224 detections; the 366 sites seen on >= 3 UTC dates hold 5,789 detections (38 % of all 5-day detections). Sites by days detected: 1 d 2,780; 2 d 434; 3 d 165; 4 d 104; 5 d 97.
Class definitions per cluster (all detections in site type): static-small = every detection in a >= 3-day site with <= 3 detections on any day; persistent-large = every detection in a >= 3-day site, not small; partly = some detections.

| Window (UTC dates of history) | Class | Clusters | Detections | Near-client | Known live fires | Reported-WF (live+ext) | RX |
|---|---|---|---|---|---|---|---|
| W0 (5) | static-small | 84 (6.4%) | 159 (3.3%) | 1 | 0 | 0 | 1 |
| W0 (5) | persistent-large | 123 (9.4%) | 681 (14.3%) | 2 | 0 | 0 | 3 |
| W0 (5) | partly | 36 (2.7%) | 421 (8.8%) | 1 | 1 | 1 | 1 |
| W0 (5) | none | 1,069 (81.5%) | 3,502 (73.5%) | 22 | 4 | 13 | 34 |
| W1 (4) | static-small | 71 (6.4%) | 130 (3.7%) | 6 | 0 | 1 | 1 |
| W1 (4) | persistent-large | 109 (9.9%) | 547 (15.7%) | 5 | 1 | 2 | 1 |
| W1 (4) | partly | 16 (1.5%) | 330 (9.5%) | 0 | 0 | 0 | 3 |
| W1 (4) | none | 905 (82.2%) | 2,475 (71.1%) | 17 | 1 | 2 | 19 |
| W2 (3) | static-small | 49 (5.8%) | 86 (3.0%) | 7 | 0 | 1 | 0 |
| W2 (3) | persistent-large | 87 (10.3%) | 448 (15.5%) | 4 | 0 | 1 | 0 |
| W2 (3) | partly | 15 (1.8%) | 171 (5.9%) | 1 | 1 | 1 | 0 |
| W2 (3) | none | 694 (82.1%) | 2,193 (75.7%) | 18 | 2 | 9 | 23 |

The persistent classes are dominated by developed land (persistent-large: 62 of 123 W0 clusters on NLCD developed, 89 % night-involved, median FRP 2.3 MW; static-small: 34 of 84 developed, median FRP 0.9 MW, 87 % night-involved).
**A bare "3 of 5 days" test does not isolate industry:** pooled W0-W2 it flags 7 of 31 reported-wildfire clusters (23 %) against 18 % of all clusters, because long-lived real fires (BEE HAVEN burned in all four windows) also recur. In W1 and W2 the near-client clusters in the persistent classes were almost all nightly Los Angeles, San Diego and Bay Area sites (site night fraction 0.93-1.00, site FRP <= 3.2 MW), plus one large high-FRP site on Tehama/Glenn cropland (the location of W0 near-client #20); the discriminating attributes are **night dominance and low FRP**.

Industrial-signature parameter sweep (pooled W0-W2, 3,258 clusters, 11,143 detections; K = distinct UTC dates, nf = night fraction, fmax = site FRP max, mpd = max detections on any one day):

| K | nf | fmax (MW) | mpd | Clusters removed | Detections removed | Near-client removed (of 84) | Known live lost (of 10) | Reported-WF lost (of 31) | RX lost (of 86) |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 0 | any | any | 521 (16.0 %) | 2,045 (18.4 %) | 25 | 1 | 5 | 5 |
| 3 | 0 | any | 3 | 204 (6.3 %) | 375 (3.4 %) | 14 | 0 | 2 | 2 |
| 3 | 0.5 | any | any | 486 (14.9 %) | 1,921 (17.2 %) | 24 | 1 | 5 | 1 |
| 3 | 0.8 | any | any | 456 (14.0 %) | 1,727 (15.5 %) | 23 | 1 | 5 | 0 |
| 3 | 0.8 | 5 | any | 396 (12.2 %) | 1,211 (10.9 %) | 23 | 0 | 4 | 0 |
| **3** | **0.8** | **5** | **8** | **369 (11.3 %)** | **1,018 (9.1 %)** | **23** | **0** | **4** | **0** |
| 3 | 0.8 | 5 | 3 | 182 (5.6 %) | 322 (2.9 %) | 14 | 0 | 2 | 0 |
| 3 | 0.8 | 3 | 8 | 323 (9.9 %) | 815 (7.3 %) | 21 | 0 | 4 | 0 |
| 3 | 0.9 | 5 | 8 | 351 (10.8 %) | 941 (8.4 %) | 23 | 0 | 4 | 0 |
| 4 | 0.8 | 5 | 8 | 153 (4.7 %) | 444 (4.0 %) | 10 | 0 | 2 | 0 |
| 3 | 0.8 | 8 | 8 | 376 (11.5 %) | 1,039 (9.3 %) | 23 | 0 | 4 | 0 |
| 3 | 0.8 | 10 | 12 | 420 (12.9 %) | 1,358 (12.2 %) | 23 | 0 | 4 | 0 |

Headline rule IS(3, 0.8, 5, 8) per window: W0 140 of 1,312 clusters (10.7 %), 400 of 4,763 detections (8.4 %), near-client 2 of 26, known 0 of 5, reported-WF 0 of 14; W1 126 of 1,101 (11.4 %), 324 detections, near-client 10 of 28, reported-WF 2 of 5; W2 103 of 845 (12.2 %), 294 detections, near-client 11 of 30, reported-WF 2 of 12.
The "reported-WF lost" pairs in W1/W2 are LAC-384049 and LAC-379048, small LA County incidents inside the 3 km attach radius of refinery-district sites that glow at 1-2 MW every night; they are almost certainly not the same objects. In W0 the 140 flagged clusters sit on developed land (66), have no NLCD value (15: border, coast or offshore), barren/water (15), shrub/grass (14), crop (11), forest (8), pasture (7), wetland (4).
The sites are all-night, FRP ~0.5-1.5 MW, 7-14 detections in 5 days from all three satellites. By position they are in refinery, port, landfill and oil-field districts (Gulf Coast Louisiana, Niagara, Orange County, Long Beach/Carson, Santa Clarita, Richmond CA, Otay Mesa, Denver); I did not identify individual facilities.

### 3.5 Day vs night, confidence and FRP at detection level

- Night detections are cool: median FRP 1.27 MW (p90 3.1) vs day 8.2 MW (p90 36.4). Share of detections kept by FRP >= 1 / 2 / 5 / 10 / 20 MW: night 65 / 25 / 3.2 / 0.7 / 0 %, day 99.9 / 96 / 69 / 42 / 22.5 %. An absolute FRP threshold of 5 MW or more therefore deletes the night.
- Pre-cluster (detection-level) versions of the confidence and FRP rules, W0 (4,763 detections; 88 in near-client clusters; 27 on live known fires; 45 on any reported wildfire). A filter applied before clustering also thins the surviving clusters:

| Detection-level filter (detections that FAIL) | Failing detections | Of the 88 near-client dets | Of the 27 known-fire dets | Of the 45 reported-WF dets | Failing dets that are night | Clusters left after re-clustering |
|---|---|---|---|---|---|---|
| drop low confidence | 215 (4.5 %) | 4 | 0 | 0 | 0 % | 1,300 |
| FRP < 1 MW | 560 (11.8 %) | 5 | 8 | 11 | 99.5 % | 1,133 |
| FRP < 2 MW | 1,330 (27.9 %) | 14 | 14 | 20 | 90.3 % | 945 |
| FRP < 5 MW | 2,527 (53.1 %) | 43 | 16 | 25 | 61.3 % | 572 |
| FRP < 10 MW | 3,427 (72.0 %) | 63 | 18 | 33 | 46.4 % | 324 |
| FRP < 20 MW | 4,051 (85.1 %) | 74 | 19 | 37 | 39.5 % | 186 |
| not high confidence | 4,558 (95.7 %) | 82 | 25 | 43 | 35.0 % | 113 |

- Confidence: 'l' 215 detections, all day (median FRP 18 MW; 29 in one 1-degree cell of the Arkansas/Missouri Delta, others in the Gulf States and southern Oregon; 17-22 Z); 'h' 205 detections, all with bright_ti4 = 367.0 K (saturation), 202 by day; 14 further saturated pixels are 'n'. In the 5-day pull night detections are 7,389 nominal + 11 high and no low.
- Day/night of clusters: 65 % day-only, 26 % night-only. Cropland clusters are 89 % day-only (single-pass 59 %, median FRP 7.5 MW); non-crop clusters 57 % day-only (single-pass 52 %, median FRP 3.4 MW). Night-involved clusters: 8 % cropland, 41 % developed, 17 % forest; night-only reported fires: 3 of the 5 live W0 fires.
  "Night only" would remove 65 % of clusters and 19 of 26 near-client clusters and delay every afternoon start to the next night pass; "day only" would remove the night-only 26 % including 3 of 5 known fires. Reject both as screens; use day/night to set thresholds.

### 3.6 Tiers (building blocks for a gate that does not delete)

Tier D = n == 1, one orbit pass, FRP < 5 MW (a lone weak pixel seen once); U = one pass but >= 2 pixels or FRP >= 5; C = >= 2 orbit passes; S (strong) = FRP >= 20 or >= 5 detections or >= 3 passes (overlaps U and C).

| Scope | Tier | Clusters | Detections | Near-client | Known live | Reported-WF (live+ext) | RX |
|---|---|---|---|---|---|---|---|
| W0 | D display-only (1 pixel, 1 pass, FRP<5) | 425 (32.4%) | 425 (8.9%) | 6 / 26 | 1 / 5 | 3 / 14 | 4 / 39 |
| W0 | U unconfirmed (1 pass, >=2 pixels or FRP>=5) | 279 (21.3%) | 569 (11.9%) | 6 / 26 | 1 / 5 | 4 / 14 | 5 / 39 |
| W0 | C confirmed (>=2 orbit passes) | 608 (46.3%) | 3,769 (79.1%) | 14 / 26 | 3 / 5 | 7 / 14 | 30 / 39 |
| W0 | S strong (FRP>=20 or >=5 dets or >=3 passes) | 441 (33.6%) | 3,391 (71.2%) | 10 / 26 | 1 / 5 | 3 / 14 | 17 / 39 |
| pooled W0-W3 | D display-only (1 pixel, 1 pass, FRP<5) | 1,371 (34.4%) | 1,371 (10.2%) | 29 / 104 | 4 / 15 | 12 / 45 | 16 / 92 |
| pooled W0-W3 | U unconfirmed (1 pass, >=2 pixels or FRP>=5) | 850 (21.4%) | 1,820 (13.6%) | 13 / 104 | 2 / 15 | 8 / 45 | 21 / 92 |
| pooled W0-W3 | C confirmed (>=2 orbit passes) | 1,759 (44.2%) | 10,209 (76.2%) | 62 / 104 | 9 / 15 | 25 / 45 | 55 / 92 |
| pooled W0-W3 | S strong (FRP>=20 or >=5 dets or >=3 passes) | 1,261 (31.7%) | 9,179 (68.5%) | 39 / 104 | 8 / 15 | 17 / 45 | 42 / 92 |

Land cover by tier (W0): D 19 % crop / 22 % developed / 21 % forest; **U 42 % crop** (one-pass multi-pixel high-FRP burns: the ag-burn signature) / 10 % developed / 18 % forest; C 23 % crop / 21 % developed / 25 % forest.

### 3.7 Known-fire clusters, all four windows (15 cluster-days, 10 distinct fires)

| Window | Fire (WFIGS) | Dets | Passes | Sats | FRP max MW | Conf l/n/h | D/N | n≥2 | ≥2 passes | FRP≥5 | FRP≥10 | K1 | K2 | Tier |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W0 | BEE HAVEN (2) (24-06) | 4 | 2 | 2 | 1.9 | 0/4/0 | N | keep | keep | DROP | DROP | keep | keep | C |
| W0 | South Tank Trail | 1 | 1 | 1 | 7.0 | 0/1/0 | D | DROP | DROP | keep | DROP | DROP | DROP | U |
| W0 | Kelsey | 17 | 6 | 3 | 118.6 | 0/15/2 | DN | keep | keep | keep | keep | keep | keep | C |
| W0 | MUSE | 1 | 1 | 1 | 1.2 | 0/1/0 | N | DROP | DROP | DROP | DROP | DROP | DROP | D |
| W0 | Skid Road | 4 | 2 | 2 | 2.7 | 0/4/0 | N | keep | keep | DROP | DROP | keep | keep | C |
| W1 | BEE HAVEN (2) (24-06) | 10 | 2 | 2 | 1.6 | 0/10/0 | N | keep | keep | DROP | DROP | keep | keep | C |
| W1 | Redbud | 5 | 1 | 1 | 1.4 | 0/5/0 | N | keep | DROP | DROP | DROP | DROP | keep | U |
| W2 | BEE HAVEN (2) (24-06) | 18 | 3 | 3 | 2.6 | 0/18/0 | N | keep | keep | DROP | DROP | keep | keep | C |
| W2 | Range  47B | 4 | 3 | 3 | 1.6 | 0/4/0 | N | keep | keep | DROP | DROP | keep | keep | C |
| W2 | LAC-387250 | 1 | 1 | 1 | 0.3 | 0/1/0 | N | DROP | DROP | DROP | DROP | DROP | DROP | D |
| W3 | BEE HAVEN (2) (24-06) | 21 | 4 | 3 | 8.5 | 2/19/0 | DN | keep | keep | keep | DROP | keep | keep | C |
| W3 | Range  47B | 6 | 3 | 3 | 8.9 | 1/5/0 | D | keep | keep | keep | DROP | keep | keep | C |
| W3 | Hewitt Lane Fire | 3 | 2 | 2 | 21.4 | 1/2/0 | D | keep | keep | keep | keep | keep | keep | C |
| W3 | LAC-387250 | 1 | 1 | 1 | 0.2 | 0/1/0 | N | DROP | DROP | DROP | DROP | DROP | DROP | D |
| W3 | Cherry Plain | 1 | 1 | 1 | 4.1 | 0/1/0 | D | DROP | DROP | DROP | DROP | DROP | DROP | D |

### 3.8 Layered protocols (not hard-filter-only)

| Layers applied | Scope | Forecast-eligible clusters | Detections in them | Near-client eligible | Known live eligible | Reported-WF eligible | RX eligible |
|---|---|---|---|---|---|---|---|
| IS only | W0 | 1,172 / 1,312 (89.3%) | 4,363 / 4,763 | 24 / 26 | 5 / 5 | 14 / 14 | 39 / 39 |
| IS only | pooled W0-W2 | 2,889 / 3,258 (88.7%) | 10,125 / 11,143 | 61 / 84 | 10 / 10 | 27 / 31 | 86 / 86 |
| IS + LOW | W0 | 1,160 / 1,312 (88.4%) | 4,351 / 4,763 | 24 / 26 | 5 / 5 | 14 / 14 | 39 / 39 |
| IS + LOW | pooled W0-W2 | 2,838 / 3,258 (87.1%) | 10,069 / 11,143 | 61 / 84 | 10 / 10 | 27 / 31 | 86 / 86 |
| D only | W0 | 888 / 1,312 (67.7%) | 4,339 / 4,763 | 21 / 26 | 4 / 5 | 11 / 14 | 35 / 39 |
| D only | pooled W0-W2 | 2,137 / 3,258 (65.6%) | 10,022 / 11,143 | 65 / 84 | 8 / 10 | 22 / 31 | 72 / 86 |
| IS + LOW + D (no proximity exemption) | W0 | 787 / 1,312 (60.0%) | 3,978 / 4,763 | 19 / 26 | 4 / 5 | 11 / 14 | 35 / 39 |
| IS + LOW + D (no proximity exemption) | pooled W0-W2 | 1,867 / 3,258 (57.3%) | 9,098 / 11,143 | 50 / 84 | 8 / 10 | 20 / 31 | 72 / 86 |
| IS + LOW + D, 10 km proximity exemption | W0 | 788 / 1,312 (60.1%) | 3,979 / 4,763 | 20 / 26 | 4 / 5 | 11 / 14 | 35 / 39 |
| IS + LOW + D, 10 km proximity exemption | pooled W0-W2 | 1,869 / 3,258 (57.4%) | 9,100 / 11,143 | 52 / 84 | 8 / 10 | 20 / 31 | 72 / 86 |

IS = industrial signature (dropped, release valve: any day detection, FRP > 5 MW or > 8 detections/day). LOW = all-low confidence (display-only). D = tier D (display-only). Proximity exemption = centroid within 10 km of a home (2 clusters in W0, 2 in W1, 1 in W2).
Most of the cost of "IS + LOW + D" is tier D itself (pooled W0-W2: 1,121 clusters = 34 %, but only 10 % of detections; 2 of 10 known live fires and 9 of 31 reported-WF clusters are tier D); IS alone is nearly free on the proxy but yields only 11 % of clusters.

## 4. The 26 near-client clusters (W0)

All 26 are on the demo roster's communities: Paradise 17, Santa Rosa 3, Park City 2, Malibu 2, Boulder 1, Rancho Santa Fe 1. Times are UTC; "h since last" is hours from the last detection to 18:45 Z on 10-29. 5-day history counts detections within 2 km of the centroid.

| # | Centroid (lat, lon) | Nearest demo community, km | Dets | Passes | Sats | FRP max / sum MW | Conf l/n/h | D/N | First-last UTC (h since last) | MODIS ≤2.5 km ±3 h | 5-day history within 2 km (days / dets) | Reported incident | NLCD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 39.847, -121.705 | paradise_ca 1 | 1 | 1 | N21 | 3.5 / 3.5 | 0/1/0 | D | 10-28 21:23-21:23 (21) | 0 | 2 / 12 | - | evergreen forest |
| 2 | 38.439, -122.515 | santa_rosa_ca 6 | 2 | 2 | N20+SNPP | 4.7 / 7.8 | 0/2/0 | D | 10-28 20:21-20:40 (22) | 1 | 1 / 2 | - | shrub/scrub |
| 3 | 39.611, -121.790 | paradise_ca 14 | 3 | 2 | N20+N21 | 2.7 / 7.1 | 0/3/0 | D | 10-28 20:40-21:23 (21) | 0 | 1 / 3 | - | CROPLAND |
| 4 | 39.703, -121.872 | paradise_ca 15 | 6 | 3 | N20+N21+SNPP | 8.0 / 31.8 | 1/4/1 | D | 10-28 20:21-21:23 (21) | 0 | 2 / 7 | - | developed-open |
| 5 | 39.542, -121.904 | paradise_ca 26 | 2 | 1 | N20 | 1.9 / 3.8 | 1/1/0 | D | 10-28 20:40-20:40 (22) | 0 | 1 / 2 | - | CROPLAND |
| 6 | 40.889, -111.906 | park_city_ut 28 | 1 | 1 | N21 | 1.4 / 1.4 | 0/1/0 | N | 10-29 09:43-09:43 (9) | 0 | 1 / 1 | - | developed-high |
| 7 | 39.753, -122.050 | paradise_ca 30 | 1 | 1 | N20 | 3.7 / 3.7 | 0/1/0 | D | 10-28 20:42-20:42 (22) | 0 | 2 / 12 | - | CROPLAND |
| 8 | 38.233, -122.322 | santa_rosa_ca 32 | 1 | 1 | N21 | 12.5 / 12.5 | 0/1/0 | D | 10-28 21:23-21:23 (21) | 0 | 1 / 1 | - | CROPLAND |
| 9 | 34.431, -118.644 | malibu_ca 34 | 2 | 2 | N21+SNPP | 0.8 / 1.4 | 0/2/0 | N | 10-29 09:45-10:21 (8) | 0 | 5 / 14 | - | shrub/scrub |
| 10 | 39.637, -122.098 | paradise_ca 35 | 4 | 3 | N20+N21+SNPP | 11.3 / 26.9 | 0/4/0 | D | 10-28 20:21-21:23 (21) | 0 | 3 / 13 | - | CROPLAND |
| 11 | 39.496, -122.050 | paradise_ca 38 | 2 | 1 | SNPP | 7.1 / 9.6 | 0/2/0 | D | 10-28 20:21-20:21 (22) | 0 | 1 / 2 | - | CROPLAND |
| 12 | 33.793, -118.351 | malibu_ca 39 | 1 | 1 | N21 | 0.4 / 0.4 | 0/1/0 | N | 10-29 09:45-09:45 (9) | 0 | 2 / 2 | - | developed-low |
| 13 | 39.823, -122.161 | paradise_ca 39 | 6 | 3 | N20+N21 | 12.6 / 30.1 | 0/6/0 | DN | 10-28 20:42-09:43 (9) | 1 | 2 / 6 | - | CROPLAND |
| 14 | 39.690, -122.161 | paradise_ca 39 | 4 | 4 | N20+N21+SNPP | 5.1 / 13.6 | 0/4/0 | D | 10-28 20:21-22:22 (20) | 0 | 1 / 4 | - | CROPLAND |
| 15 | 39.654, -104.707 | boulder_co 40 | 1 | 1 | SNPP | 0.7 / 0.7 | 0/1/0 | N | 10-29 08:39-08:39 (10) | 0 | 3 / 4 | - | developed-open |
| 16 | 39.474, -122.073 | paradise_ca 42 | 7 | 2 | N20+SNPP | 32.0 / 117.3 | 1/4/2 | D | 10-28 20:21-20:40 (22) | 0 | 1 / 7 | - | CROPLAND |
| 17 | 39.429, -122.024 | paradise_ca 42 | 2 | 1 | N20 | 43.6 / 85.3 | 1/1/0 | D | 10-28 20:40-20:40 (22) | 0 | 1 / 2 | - | CROPLAND |
| 18 | 33.262, -116.806 | rancho_santa_fe_ca 42 | 1 | 1 | N20 | 6.7 / 6.7 | 0/1/0 | D | 10-28 20:40-20:40 (22) | 0 | 1 / 1 | - | woody wetland |
| 19 | 40.882, -110.868 | park_city_ut 50 | 7 | 4 | N20+N21+SNPP | 39.1 / 150.6 | 0/7/0 | D | 10-28 19:45-21:25 (21) | 0 | 1 / 7 | - | evergreen forest |
| 20 | 40.079, -122.193 | paradise_ca 50 | 15 | 6 | N20+N21+SNPP | 38.9 / 214.6 | 0/12/3 | DN | 10-28 20:21-09:43 (9) | 4 | 4 / 59 | - | CROPLAND |
| 21 | 39.251, -121.616 | paradise_ca 50 | 2 | 2 | N20+N21 | 4.4 / 7.7 | 0/2/0 | D | 10-28 20:40-21:23 (21) | 0 | 1 / 2 | - | CROPLAND |
| 22 | 39.221, -121.627 | paradise_ca 53 | 4 | 3 | N20+N21+SNPP | 5.1 / 14.0 | 0/4/0 | D | 10-28 20:21-21:23 (21) | 0 | 1 / 4 | - | CROPLAND |
| 23 | 39.328, -122.073 | paradise_ca 53 | 1 | 1 | N21 | 9.1 / 9.1 | 0/1/0 | D | 10-28 21:23-21:23 (21) | 0 | 1 / 1 | - | CROPLAND |
| 24 | 39.986, -120.923 | paradise_ca 56 | 2 | 2 | N20+N21 | 6.5 / 9.1 | 0/2/0 | D | 10-28 20:42-21:23 (21) | 0 | 1 / 2 | RX | evergreen forest |
| 25 | 38.545, -121.899 | santa_rosa_ca 57 | 9 | 4 | N20+N21+SNPP | 18.6 / 73.2 | 0/9/0 | D | 10-28 20:21-22:22 (20) | 0 | 3 / 22 | - | CROPLAND |
| 26 | 40.154, -122.244 | paradise_ca 58 | 1 | 1 | N21 | 1.7 / 1.7 | 0/1/0 | N | 10-29 09:43-09:43 (9) | 0 | 1 / 1 | RX | developed-low |

Rule outcomes (keep / DROP) for each of the 26:

| # | C1 not-low | C2 high | F≥5 | F≥10 | n≥2 | n≥3 | ≥2 passes | ≥2 sats | K1 | K2 | strong | Tier | Static-small |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D |  |
| 2 | keep | DROP | DROP | DROP | keep | DROP | keep | keep | keep | keep | DROP | C |  |
| 3 | keep | DROP | DROP | DROP | keep | keep | keep | keep | keep | keep | DROP | C |  |
| 4 | keep | keep | keep | DROP | keep | keep | keep | keep | keep | keep | keep | C |  |
| 5 | keep | DROP | DROP | DROP | keep | DROP | DROP | DROP | DROP | keep | DROP | U |  |
| 6 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D |  |
| 7 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D |  |
| 8 | keep | DROP | keep | keep | DROP | DROP | DROP | DROP | keep | keep | DROP | U |  |
| 9 | keep | DROP | DROP | DROP | keep | DROP | keep | keep | keep | keep | DROP | C |  |
| 10 | keep | DROP | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 11 | keep | DROP | keep | DROP | keep | DROP | DROP | DROP | DROP | keep | DROP | U |  |
| 12 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D |  |
| 13 | keep | DROP | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 14 | keep | DROP | keep | DROP | keep | keep | keep | keep | keep | keep | keep | C |  |
| 15 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D | yes |
| 16 | keep | keep | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 17 | keep | DROP | keep | keep | keep | DROP | DROP | DROP | keep | keep | keep | U |  |
| 18 | keep | DROP | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | U |  |
| 19 | keep | DROP | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 20 | keep | keep | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 21 | keep | DROP | DROP | DROP | keep | DROP | keep | keep | keep | keep | DROP | C |  |
| 22 | keep | DROP | keep | DROP | keep | keep | keep | keep | keep | keep | keep | C |  |
| 23 | keep | DROP | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | U |  |
| 24 | keep | DROP | keep | DROP | keep | DROP | keep | keep | keep | keep | DROP | C |  |
| 25 | keep | DROP | keep | keep | keep | keep | keep | keep | keep | keep | keep | C |  |
| 26 | keep | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | DROP | D |  |

Reading the near-client set:
- 15 of 26 on cultivated crops (the Sacramento Valley clusters, afternoon passes 20:21-22:22 Z on 10-28, roughly 13-15 h local: rice-straw hours); 9 of the 15 are seen on >= 2 passes, 5 are tier U, 1 is tier D. Persistence therefore keeps 9/15 cropland vs 5/11 non-crop clusters: it does not sort agriculture from the rest.
- Single pixel / single pass / weak (tier D, 6): #1 (1.0 km from a Paradise home, evergreen forest, FRP 3.5, day), #7 (cropland, FRP 3.7, day) and #6, #12, #15, #26 (night, FRP 0.4-1.7, developed land; #15 matches the static-small test). #1 is the one that matters: a lone pixel 1 km from homes in forest, with 12 detections in the 5-day history within 2 km on 2 dates. This is why the proximity exemption exists.
- Strongest: #20 (40.079, -122.193; 15 dets, 6 passes, 3 sat, FRP max 38.9 / sum 214.6, 3 high-conf, day and night until 09:43 Z, 4 MODIS, 59 detections on 4 dates in 5 days, cropland, 50 km from Paradise) and #19 (Utah, 7 dets, 4 passes, FRP 39.1, evergreen forest, 50 km from Park City). Neither is on a reported incident; both pass every gate except high-only (#19 has no saturated pixel). The #20 site also appears as a persistent-large site in W1 (21 dets, FRP 74.8): a multi-day high-FRP source on cropland that no screen should delete.
- #24 (RX, forest, 56 km) and #26 (RX, developed) are matched to reported prescribed burns.
- 12 of 26 fail >= 2 passes; 11 fail FRP >= 5; 18 fail FRP >= 10; 9 fail n >= 2; 23 fail the high-only rule. K1 keeps 16, K2 keeps 18, "IS + LOW + D, 10 km exemption" keeps 20.
- Time-to-confirm for the near-client events (33 in 2.7 days): 21 are confirmed by a second pass within 24 h: 5 after 0.35 h, 12 after 0.6 h, 3 after 0.63-1.07 h, 1 after 22.6 h.

## 5. Land cover (NLCD 2021, MRLC public WMS)

Reachable and fast (0.8 s per point, 4 threads); the whole W0 set took 4.5 min, so the 15-minute skip clause did not apply.

| Population (W0) | n | cropland (NLCD 82) | pasture/hay | forest | shrub/grass | developed | wetland | barren/water | outside NLCD |
|---|---|---|---|---|---|---|---|---|---|
| all clusters | 1312 | 337 (26%) | 110 (8%) | 289 (22%) | 103 (8%) | 252 (19%) | 82 (6%) | 41 (3%) | 97 (7%) |
| near-client (26) | 26 | 15 (58%) | 0 (0%) | 3 (12%) | 2 (8%) | 5 (19%) | 1 (4%) | 0 (0%) | 0 (0%) |
| random 100 other clusters | 100 | 20 (20%) | 12 (12%) | 25 (25%) | 5 (5%) | 23 (23%) | 6 (6%) | 2 (2%) | 7 (7%) |
| known live wildfire | 5 | 0 (0%) | 0 (0%) | 2 (40%) | 2 (40%) | 0 (0%) | 1 (20%) | 0 (0%) | 0 (0%) |
| extended reported wildfire only | 9 | 0 (0%) | 0 (0%) | 4 (44%) | 4 (44%) | 1 (11%) | 0 (0%) | 0 (0%) | 0 (0%) |
| reported RX burn | 39 | 0 (0%) | 1 (3%) | 25 (64%) | 9 (23%) | 2 (5%) | 2 (5%) | 0 (0%) | 0 (0%) |
| unexplained | 1261 | 337 (27%) | 109 (9%) | 259 (21%) | 89 (7%) | 249 (20%) | 79 (6%) | 41 (3%) | 97 (8%) |

The 14 W0 clusters on reported wildfires (5 live + 9 extended) have **no** cropland; with a 26 % base rate that is about a 1.6 % chance event (0.743^14), so the signal is probably real but small-sample.

Cropland share of clusters removed vs kept by each rule (W0):

| Rule | Removed clusters | cropland share of removed | cropland share of kept | forest share of removed | forest share of kept |
|---|---|---|---|---|---|
| C1 no-low | 12 | 33% | 26% | 17% | 22% |
| F1 frp>=5 | 742 | 17% | 36% | 18% | 28% |
| F1 frp>=10 | 988 | 20% | 44% | 20% | 30% |
| S1 n>=2 | 534 | 23% | 28% | 21% | 22% |
| S2 n>=3 | 788 | 25% | 27% | 20% | 25% |
| P2 orbits>=2 | 704 | 28% | 23% | 20% | 25% |
| P3 sats>=2 | 722 | 28% | 23% | 20% | 24% |
| K1 nh&(orb>=2 or frp>=10) | 613 | 24% | 27% | 20% | 23% |
| K9 nh&(orb>=2 or modis or frp>=10) | 605 | 23% | 28% | 20% | 23% |
| N1 night-only | 854 | 35% | 8% | 25% | 17% |
| N2 day-only | 347 | 8% | 32% | 12% | 26% |

FRP and high-FRP promoters keep *more* cropland than they remove (FRP >= 10: 44 % of kept vs 20 % of removed); a night detection is the strongest cropland discriminator (kept 8 % vs removed 35 %) but cannot be used for early detection. Persistence, size and the combinations are close to neutral on cropland.
Cropland + no night detection (an ag-burn signature that needs only a land-cover or fuel-model layer): removes 300 clusters (22.9 %), 970 detections (20.4 %), 13 of 26 near-client clusters, **0 of 5 known and 0 of 14 reported-wildfire clusters, 0 of 39 RX**. As a *ceiling* (not a deletion) this is the strongest single context signal in the data; stubble and grass burns can escape, so it should lower priority and cap alert level, not silence.

## 6. Other findings

- **Prescribed burns come back through FIRMS.** The live WFIGS layer has 490 RX records and 238 wildfires; the adapter drops RX. In W0, 381 detections (8 %) and 39 clusters lie on RX burns; 77 % of those clusters have >= 2 passes (46 % of all clusters do) and their pooled median max-FRP is 9.4 MW (all clusters 3.4), so persistence removes only 9 of the 39, FRP >= 5 only 10, the industrial signature none. Matching FIRMS detections to RX records (live layer plus state registries) is the only screen for them.
- **Swarm.** A cluster with >= 3 other clusters within 25 km and +-6 h (regional burning burst): W0 236 clusters (18 %), 981 detections, 10 of 26 near-client, 0 known, 0 reported-WF, 1 RX; known wildfires are isolated (swarm 0 for all 15 cluster-days). Weak (a wind event can ignite many fires at once): ceiling modifier at most.
- **MODIS corroboration** (Terra/Aqua detection within 2.5 km and +-3 h) rescues only 15 more clusters than >= 2 VIIRS passes (689 vs 704 removed): not worth a second feed for this purpose.
- **Pixel size / scan angle**: not a false-alarm discriminator (see 3.3). **Saturation** = high confidence (section 3.5).
- **Late arrival and URT**: 997 of 15,224 rows (6.5 %) are URT, aged 0-2.1 h (median 0.4 h); NRT rows first appear at 2.7 h. 20 rows with acquisition <= 18:45 Z (0.4 % of W0) arrived between my two pulls 20 minutes apart (one N20 granule at 18:42-18:43 Z).
- **Day-to-day variability**: detections per 24-h window 4,763 / 3,482 / 2,898 / 2,257; NOAA-20 had only 529 rows on 10-27 against ~1,100 on neighbouring days (a missing or reduced pass). Removal fractions are stable (e.g. >= 2 passes: 53.7 % in W0, 55.8 % pooled), but the near-client composition is not (W0 is cropland-heavy, W1-W2 flare-heavy).

## 7. Recommendation by criterion

| Criterion | Disposition | Why (measured) |
|---|---|---|
| Confidence: drop low | **display-only** (hard drop acceptable, negligible yield) | 12 clusters (0.9 %); 0 of 45 reported-WF lost; re-observed 8 % |
| Confidence: high only | **reject** | removes 91 %, loses 4 of 5 known; 'h' = saturated pixel, 49 % cropland |
| FRP thresholds (1, 2, 5, 10, 20), cluster FRP sum | **reject as gate**; FRP >= 20 only as a promoter *combined with* persistence or night | day/night filter; 3 of 5 (10 of 15) known fires lost at >= 5 MW; keeps cropland |
| Cluster size >= 2 / >= 3 | **reject as gate**; use as tier input | loses the two single-detection known fires; 41 % / 60 % of clusters |
| Persistence >= 2 passes (or >= 2 satellites) | **tier gate / ceiling**, never silent deletion | 54 % of clusters, 21 % of detections; 2 of 5 (6 of 15) known; median wait 0.62 h; 53 % of new events never confirm |
| Static / industrial signature | **hard filter with release valve** | 11 % of clusters, 9 % of detections; 0 known, 0 RX lost; large share of near-client in W1-W2 |
| Persistent site only (>= 3 of 5 days) | **tier only** | does not separate industry from long-lived real fires |
| Day vs night only | **reject** | removes 65 % / 26 %; 3 of 5 known fires are night-only; use for thresholds |
| Combinations (K1, K2, K6, ...) | **tier gate candidates**, not filters | remove 39-48 % of clusters but still lose 2 of 5 known |
| Layered L (IS hard; low + tier D display-only; 10 km exempt) | **recommended shape** | 60 % of clusters / 84 % of detections stay; 20 of 26 near-client, 4 of 5 known stay |
| Cropland + no night (land-cover context) | **ceiling modifier** | 23 % of clusters, 13 of 26 near-client, 0 of 14 reported WF; strongest context signal |
| Swarm, nadir pixel, MODIS | swarm: weak ceiling; pixel size: reject; MODIS: optional | see section 6 |
| RX registry match | **needed** (not a threshold) | 8 % of detections pass every gate |

### 7b. The shape the numbers support, in words

1. Form clusters from all detections; no detection-level confidence or FRP thresholds (they thin real clusters and, for FRP, delete the night).
2. Hard-drop only clusters matching the industrial signature (>= 3 of the last 5 UTC dates at the same 750 m site, >= 80 % night, site FRP <= 5 MW, <= 8 detections in a day); release on any daytime detection, FRP > 5 MW, > 8 detections in a day, or growth beyond the site. Needs a 5-day detection memory.
3. Tier D (one pixel, one pass, FRP < 5 MW) and all-low clusters: shown, not forecast, no client alert. Promoted by a second pass, a second pixel or FRP >= 5 MW; 47 % of new events get that second pass, at a median 0.62 h.
4. Tier U (one pass but larger or hotter; 42 % cropland): forecast-eligible with an alert ceiling until a second pass. Cropland + no night detection lowers priority and caps the ladder; it never deletes.
5. Tier C (>= 2 passes) is the normal pipeline; strong (FRP >= 20, >= 5 detections or >= 3 passes) goes first, but FRP alone never fast-tracks (high FRP is where the ag burns are).
6. Anything within ~10 km of a client home skips tiers D and LOW (W0: 2 clusters; W0-W3: 5 in total, about 1.3 per day).
7. Match to known fires first, then to RX records (live WFIGS RX plus state registries); matched RX clusters get the treatment the adapter already gives RX incidents (dropped), unless an escape or containment-loss flag is raised.
8. Fetch `day_range >= 2` (`5` for the static memory) and window by timestamp.

## 8. Limits and what would firm this up

- **Season.** Late October. Summer fire clusters will have far higher FRP, more detections and more passes; re-run the same scripts on a peak-season week before fixing any number. The pipeline is date-agnostic (`B_build.py` reads whatever is in `B_data5\`).
- **Proxy.** 5 live fires / 27 detections (W0) or 15 cluster-days (W0-W3) or 45 (with the YTD layer, which also collects chance matches in dense LA County); known fires are the ones agencies already know, so this measures how fire-like the screened-out clusters are, not how many unreported starts a rule would miss. A count of 0 lost out of n = 14 is compatible (95 %, rule of three) with a true loss rate up to ~21 %, and out of n = 5 with up to ~60 %.
- **Single-pass clusters.** A second look may simply not have happened (swath geometry, a pass gap); the data cannot tell a short fire from an unobserved one. I did not model orbit geometry.
- **No holdout** for the static signature (plateau across the sweep, but tuned on its test days). The static mask would be sturdier built from 30-60 days of archive; the area API allows up to 10 days per request.
- **Land cover** is NLCD 2021 at the max-FRP pixel (88 % agreement with a 5-point majority); cropland on the map is not proof of a crop-residue burn.
- **Rule semantics** are cluster-level "any"; real deployments that screen detections before clustering lose pixels from surviving clusters (section 3.5).
- **Time-to-confirm** is in acquisition time; delivery latency (URT minutes, NRT hours) is extra. Opportunity was not equalised across satellites.

## 9. Reproduction

`B_fetch_fires.py`, `B_fetch_rx.py`, `B_fetch_ytd.py` (feeds; no FIRMS key) -> `B_pull5.py` (4 FIRMS requests; key read from `deploy\firms_key.txt`, never printed or written) -> `B_build.py`, `B_build2.py` (windows, clusters, flags) -> `B_rules.py`, `B_confirm.py`, `B_detect.py`, `B_static.py`, `B_static2.py`, `B_static3.py`, `B_tiers.py`, `B_protocol.py`, `B_swarm.py`, `B_landcover.py` + `B_lc_analysis.py`, `B_near.py`, `B_summary_table.py`, `B_report_tables.py` -> this file (`B_write_report.py`).
Run each with `py -3 -I`. Results: `B_rules_results.json`, `B_confirm_results.json`, `B_static2.json`, `B_static3.json`, `B_tiers.json`, `B_protocol.json`, `B_lc_results.json`, `B_near.json`, `B_summary.json`.
Verified: no output file contains the FIRMS key.
