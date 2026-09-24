# Vail -> Aspen response program — the measured grid (2026-09-23)

Six corridor community presets on real LANDFIRE tiles + OSM roads, 40 homes
each, 18 scenarios per community (moderate/high/extreme x 6/8 km x wind
offset 0/+30/-30), LIVE watcher physics (model-error band 0.71, extinction
gate, phi-vector, spotting, grass floor 0.10, plan-on-roads, 24 h hold, deploy
15). Corridor crews from the Eagle base arrive after the measured all-season
OSM drive time under fire traffic + 15 min mobilization.
Belt = coated among homes within 1 km of actual burn or front-reached;
threatened = coated among homes the forecast gave a live arrival; 3-5 h =
coated among homes the fire reached 3-5 h after detection (the '4 hours of
notice' band). Violations / overrun ticks must be 0. scratch_review/corridor_grid.py.

## Reactive arms — pooled over the six communities

| arm | scenarios | belt e1000 | front-reached | coated / threatened | **3-5 h** | 6-8 h | viol / ovt | min margin h |
|---|---|---|---|---|---|---|---|---|
| local2 — 2 in-zone crews at the community's safe zones | 108 | 626/2130 = 29.4% | 306/1341 = 22.8% | 1422/3510 = 40.5% | **40/155 = 25.8%** | 28/111 = 25.2% | 59 / 100 | 0.25 |
| local4 — 4 in-zone crews | 108 | 910/2130 = 42.7% | 491/1341 = 36.6% | 1875/3510 = 53.4% | **63/155 = 40.6%** | 43/111 = 38.7% | 113 / 136 | 0.25 |
| mixed2p2 — 2 in-zone + 2 corridor crews from the Eagle base (measured drive + 15 min) | 108 | 677/2130 = 31.8% | 329/1341 = 24.5% | 1544/3510 = 44.0% | **43/155 = 27.7%** | 27/111 = 24.3% | 120 / 170 | 0.25 |
| base1p3 — 1 in-zone + 3 corridor crews from the Eagle base | 108 | 541/2130 = 25.4% | 260/1341 = 19.4% | 1343/3510 = 38.3% | **34/155 = 21.9%** | 27/111 = 24.3% | 123 / 177 | 0.25 |
| local2_road — 2 in-zone crews at the community's safe zones — homes within 300 m of a road | 108 | 734/2185 = 33.6% | 368/1295 = 28.4% | 1544/3537 = 43.7% | **37/150 = 24.7%** | 18/103 = 17.5% | 54 / 80 | 0.25 |
| local4_road — 4 in-zone crews — homes within 300 m of a road | 108 | 1035/2185 = 47.4% | 550/1295 = 42.5% | 2026/3537 = 57.3% | **55/150 = 36.7%** | 38/103 = 36.9% | 113 / 123 | 0.25 |
| mixed2p2_road — 2 in-zone + 2 corridor crews from the Eagle base (measured drive + 15 min) — homes within 300 m of a road | 108 | 803/2185 = 36.8% | 400/1295 = 30.9% | 1686/3537 = 47.7% | **38/150 = 25.3%** | 27/103 = 26.2% | 115 / 152 | 0.25 |
| base1p3_road — 1 in-zone + 3 corridor crews from the Eagle base — homes within 300 m of a road | 108 | 677/2185 = 31.0% | 327/1295 = 25.3% | 1482/3537 = 41.9% | **32/150 = 21.3%** | 26/103 = 25.2% | 118 / 164 | 0.25 |
| mixed2p2_rf_road — 2 in-zone + 2 corridor crews from a Basalt / El Jebel base — homes within 300 m of a road | 54 | 296/933 = 31.7% | 158/602 = 26.2% | 844/1768 = 47.7% | **11/67 = 16.4%** | 13/44 = 29.5% | 54 / 76 | 0.26 |
| base1p3_rf_road — 1 in-zone + 3 corridor crews from a Basalt / El Jebel base — homes within 300 m of a road | 54 | 232/933 = 24.9% | 119/602 = 19.8% | 741/1768 = 41.9% | **11/67 = 16.4%** | 9/44 = 20.5% | 60 / 77 | 0.25 |

## Per community (belt e1000 · coated/threatened · 3-5 h band)

| community | Eagle delay min | local2 | local4 | mixed2p2 | base1p3 | local2_road | local4_road | mixed2p2_road | base1p3_road | mixed2p2_rf_road | base1p3_rf_road |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Vail / East Vail / Minturn, CO | 59 | 25.5% · 202/642 · 15/43 = 34.9% ⚠ viol 6/ovt 7 | 33.7% · 260/642 · 19/43 = 44.2% ⚠ viol 6/ovt 7 | 28.5% · 228/642 · 15/43 = 34.9% ⚠ viol 9/ovt 11 | 22.8% · 198/642 · 14/43 = 32.6% ⚠ viol 10/ovt 12 | 34.9% · 244/583 · 12/42 = 28.6% ⚠ viol 7/ovt 7 | 47.7% · 313/583 · 20/42 = 47.6% ⚠ viol 11/ovt 7 | 38.9% · 271/583 · 13/42 = 31.0% ⚠ viol 10/ovt 9 | 35.4% · 251/583 · 13/42 = 31.0% ⚠ viol 11/ovt 10 | not run | not run |
| Beaver Creek / Avon / Edwards, CO | 44 | 29.1% · 221/608 · 2/19 = 10.5% ⚠ viol 12/ovt 24 | 44.3% · 302/608 · 5/19 = 26.3% ⚠ viol 25/ovt 34 | 34.2% · 250/608 · 6/19 = 31.6% ⚠ viol 25/ovt 35 | 28.6% · 226/608 · 4/19 = 21.1% ⚠ viol 26/ovt 35 | 31.6% · 244/627 · 9/28 = 32.1% ⚠ viol 12/ovt 21 | 45.2% · 329/627 · 9/28 = 32.1% ⚠ viol 25/ovt 34 | 35.7% · 269/627 · 8/28 = 28.6% ⚠ viol 25/ovt 31 | 30.0% · 246/627 · 6/28 = 21.4% ⚠ viol 25/ovt 30 | not run | not run |
| Eagle / Gypsum, CO (corridor base) | 23 | 49.3% · 319/539 · 6/14 = 42.9% ⚠ viol 11/ovt 17 | 61.3% · 381/539 · 7/14 = 50.0% ⚠ viol 27/ovt 23 | 53.6% · 346/539 · 5/14 = 35.7% ⚠ viol 25/ovt 20 | 51.3% · 337/539 · 5/14 = 35.7% ⚠ viol 24/ovt 32 | 49.6% · 322/559 · 6/13 = 46.2% ⚠ viol 9/ovt 11 | 62.8% · 404/559 · 8/13 = 61.5% ⚠ viol 21/ovt 21 | 56.5% · 373/559 · 7/13 = 53.8% ⚠ viol 19/ovt 19 | 56.5% · 367/559 · 5/13 = 38.5% ⚠ viol 17/ovt 30 | not run | not run |
| Glenwood Springs, CO | 49 | 22.5% · 183/582 · 6/15 = 40.0% ⚠ viol 20/ovt 36 | 35.5% · 270/582 · 10/15 = 66.7% ⚠ viol 37/ovt 45 | 22.9% · 203/582 · 6/15 = 40.0% ⚠ viol 34/ovt 56 | 14.3% · 166/582 · 4/15 = 26.7% ⚠ viol 38/ovt 53 | 23.9% · 179/580 · 4/17 = 23.5% ⚠ viol 20/ovt 31 | 36.9% · 268/580 · 3/17 = 17.6% ⚠ viol 40/ovt 33 | 24.6% · 203/580 · 4/17 = 23.5% ⚠ viol 33/ovt 52 | 17.9% · 164/580 · 2/17 = 11.8% ⚠ viol 39/ovt 52 | 24.6% · 203/580 · 4/17 = 23.5% ⚠ viol 33/ovt 52 | 17.9% · 164/580 · 2/17 = 11.8% ⚠ viol 39/ovt 52 |
| Basalt / El Jebel / Carbondale, CO | 23 | 26.7% · 273/498 · 6/25 = 24.0% ⚠ viol 4/ovt 4 | 47.0% · 347/498 · 13/25 = 52.0% ⚠ viol 7/ovt 9 | 26.7% · 283/498 · 6/25 = 24.0% ⚠ viol 8/ovt 18 | 18.3% · 244/498 · 3/25 = 12.0% ⚠ viol 10/ovt 18 | 28.9% · 284/544 · 3/21 = 14.3% ⚠ viol 1/ovt 1 | 47.0% · 352/544 · 7/21 = 33.3% ⚠ viol 4/ovt 6 | 30.1% · 291/544 · 3/21 = 14.3% ⚠ viol 8/ovt 16 | 20.9% · 257/544 · 2/21 = 9.5% ⚠ viol 10/ovt 18 | 34.5% · 318/544 · 3/21 = 14.3% ⚠ viol 4/ovt 5 | 29.7% · 308/544 · 3/21 = 14.3% ⚠ viol 5/ovt 4 |
| Aspen / Snowmass Village, CO | 54 | 23.3% · 224/641 · 5/39 = 12.8% ⚠ viol 6/ovt 12 | 39.2% · 315/641 · 9/39 = 23.1% ⚠ viol 11/ovt 18 | 23.3% · 234/641 · 5/39 = 12.8% ⚠ viol 19/ovt 30 | 14.1% · 172/641 · 4/39 = 10.3% ⚠ viol 15/ovt 27 | 29.8% · 271/644 · 3/29 = 10.3% ⚠ viol 5/ovt 9 | 43.3% · 360/644 · 8/29 = 27.6% ⚠ viol 12/ovt 22 | 30.5% · 279/644 · 3/29 = 10.3% ⚠ viol 20/ovt 25 | 19.6% · 197/644 · 4/29 = 13.8% ⚠ viol 16/ovt 24 | 35.5% · 323/644 · 4/29 = 13.8% ⚠ viol 17/ovt 19 | 27.2% · 269/644 · 6/29 = 20.7% ⚠ viol 16/ovt 21 |

## Alerted regime — ALERTED: fire detected ~15 km out (extended tile, 60 h), 2 in-zone crews (fire detected ~15 km out, 60 h)

| community | runs | first-alert lead h (min-max) | coated / threatened | belt e1000 | viol / ovt | errors |
|---|---|---|---|---|---|---|
| Vail / East Vail / Minturn, CO | 3 | (1.2, 3.1) | 90/120 = 75.0% | 89/116 = 76.7% | 0 / 0 | 0 |
| Beaver Creek / Avon / Edwards, CO | 3 | (1.8, 3.3) | 94/116 = 81.0% | 94/112 = 83.9% | 4 / 24 | 0 |
| Eagle / Gypsum, CO (corridor base) | 3 | (0.9, 2.5) | 99/110 = 90.0% | 99/113 = 87.6% | 5 / 9 | 0 |
| Glenwood Springs, CO | 3 | (1.7, 3.1) | 75/111 = 67.6% | 75/99 = 75.8% | 3 / 139 | 0 |
| Basalt / El Jebel / Carbondale, CO | 3 | (1.2, 2.6) | 90/111 = 81.1% | 89/102 = 87.3% | 0 / 0 | 0 |
| Aspen / Snowmass Village, CO | 3 | (2.3, 3.5) | 94/118 = 79.7% | 93/118 = 78.8% | 4 / 3 | 0 |
| **pooled** | 18 | (0.9, 3.5) | **542/686 = 79.0%** | 539/660 = 81.7% | 16 / 175 | 0 |

## Alerted regime — ALERTED: fire detected ~15 km out (extended tile, 60 h), 2 in-zone crews — homes within 300 m of a road (fire detected ~15 km out, 60 h)

| community | runs | first-alert lead h (min-max) | coated / threatened | belt e1000 | viol / ovt | errors |
|---|---|---|---|---|---|---|
| Vail / East Vail / Minturn, CO | 3 | (1.0, 2.9) | 79/118 = 66.9% | 79/109 = 72.5% | 1 / 2 | 0 |
| Beaver Creek / Avon / Edwards, CO | 3 | (1.1, 3.3) | 90/116 = 77.6% | 90/117 = 76.9% | 3 / 24 | 0 |
| Eagle / Gypsum, CO (corridor base) | 3 | (0.7, 2.9) | 105/110 = 95.5% | 105/114 = 92.1% | 4 / 9 | 0 |
| Glenwood Springs, CO | 3 | (1.5, 3.6) | 79/114 = 69.3% | 77/100 = 77.0% | 3 / 139 | 0 |
| Basalt / El Jebel / Carbondale, CO | 3 | (0.7, 2.8) | 82/111 = 73.9% | 81/101 = 80.2% | 0 / 0 | 0 |
| Aspen / Snowmass Village, CO | 3 | (2.9, 4.0) | 91/118 = 77.1% | 89/113 = 78.8% | 2 / 2 | 0 |
| **pooled** | 18 | (0.7, 4.0) | **526/687 = 76.6%** | 521/654 = 79.7% | 13 / 176 | 0 |

## Alerted regime — ALERTED: 1 in-zone + 3 corridor crews from the Eagle base — homes within 300 m of a road (fire detected ~15 km out, 60 h)

| community | runs | first-alert lead h (min-max) | coated / threatened | belt e1000 | viol / ovt | errors |
|---|---|---|---|---|---|---|
| Vail / East Vail / Minturn, CO | 3 | (1.0, 2.9) | 96/118 = 81.4% | 94/109 = 86.2% | 0 / 0 | 0 |
| Beaver Creek / Avon / Edwards, CO | 3 | (1.1, 3.3) | 105/116 = 90.5% | 103/117 = 88.0% | 9 / 25 | 0 |
| Eagle / Gypsum, CO (corridor base) | 3 | (0.7, 2.9) | 102/110 = 92.7% | 102/114 = 89.5% | 6 / 9 | 0 |
| Glenwood Springs, CO | 3 | (1.5, 3.6) | 87/114 = 76.3% | 84/100 = 84.0% | 5 / 139 | 0 |
| Basalt / El Jebel / Carbondale, CO | 3 | (0.7, 2.8) | 85/111 = 76.6% | 84/101 = 83.2% | 0 / 0 | 0 |
| Aspen / Snowmass Village, CO | 3 | (2.9, 4.0) | 104/118 = 88.1% | 102/113 = 90.3% | 6 / 3 | 0 |
| **pooled** | 18 | (0.7, 4.0) | **579/687 = 84.3%** | 569/654 = 87.0% | 26 / 176 | 0 |

## Alerted regime — ALERTED: 1 in-zone + 3 corridor crews from a Basalt / El Jebel base — homes within 300 m of a road (fire detected ~15 km out, 60 h)

| community | runs | first-alert lead h (min-max) | coated / threatened | belt e1000 | viol / ovt | errors |
|---|---|---|---|---|---|---|
| Glenwood Springs, CO | 3 | (1.5, 3.6) | 87/114 = 76.3% | 84/100 = 84.0% | 5 / 139 | 0 |
| Basalt / El Jebel / Carbondale, CO | 3 | (0.7, 2.8) | 87/111 = 78.4% | 86/101 = 85.1% | 0 / 0 | 0 |
| Aspen / Snowmass Village, CO | 3 | (2.9, 4.0) | 104/118 = 88.1% | 102/113 = 90.3% | 6 / 3 | 0 |
| **pooled** | 9 | (0.7, 4.0) | **278/343 = 81.0%** | 272/314 = 86.6% | 11 / 142 | 0 |

## Lead-time bands, pooled (coated / reached)

| arm | 0-1 h | 1-2 h | 2-3 h | 3-4 h | 4-5 h | 5-6 h | 6-8 h | 8-12 h | 12-16 h |
|---|---|---|---|---|---|---|---|---|---|
| local2 | 0/37 = 0.0% | 4/73 = 5.5% | 10/69 = 14.5% | 21/73 = 28.8% | 19/82 = 23.2% | 12/67 = 17.9% | 28/111 = 25.2% | 49/196 = 25.0% | 62/164 = 37.8% |
| local4 | 0/37 = 0.0% | 9/73 = 12.3% | 22/69 = 31.9% | 31/73 = 42.5% | 32/82 = 39.0% | 24/67 = 35.8% | 43/111 = 38.7% | 81/196 = 41.3% | 87/164 = 53.0% |
| mixed2p2 | 0/37 = 0.0% | 3/73 = 4.1% | 12/69 = 17.4% | 19/73 = 26.0% | 24/82 = 29.3% | 16/67 = 23.9% | 27/111 = 24.3% | 54/196 = 27.6% | 67/164 = 40.9% |
| base1p3 | 0/37 = 0.0% | 0/73 = 0.0% | 7/69 = 10.1% | 13/73 = 17.8% | 21/82 = 25.6% | 15/67 = 22.4% | 27/111 = 24.3% | 48/196 = 24.5% | 55/164 = 33.5% |
| local2_road | 0/29 = 0.0% | 4/67 = 6.0% | 18/85 = 21.2% | 20/77 = 26.0% | 17/73 = 23.3% | 16/65 = 24.6% | 18/103 = 17.5% | 44/172 = 25.6% | 58/146 = 39.7% |
| local4_road | 0/29 = 0.0% | 10/67 = 14.9% | 27/85 = 31.8% | 23/77 = 29.9% | 32/73 = 43.8% | 27/65 = 41.5% | 38/103 = 36.9% | 84/172 = 48.8% | 81/146 = 55.5% |
| mixed2p2_road | 0/29 = 0.0% | 4/67 = 6.0% | 18/85 = 21.2% | 18/77 = 23.4% | 20/73 = 27.4% | 16/65 = 24.6% | 27/103 = 26.2% | 54/172 = 31.4% | 60/146 = 41.1% |
| base1p3_road | 0/29 = 0.0% | 7/67 = 10.4% | 10/85 = 11.8% | 15/77 = 19.5% | 17/73 = 23.3% | 14/65 = 21.5% | 26/103 = 25.2% | 46/172 = 26.7% | 49/146 = 33.6% |
| mixed2p2_rf_road | 0/14 = 0.0% | 1/27 = 3.7% | 9/52 = 17.3% | 4/34 = 11.8% | 7/33 = 21.2% | 3/24 = 12.5% | 13/44 = 29.5% | 13/75 = 17.3% | 30/69 = 43.5% |
| base1p3_rf_road | 0/14 = 0.0% | 2/27 = 7.4% | 3/52 = 5.8% | 3/34 = 8.8% | 8/33 = 24.2% | 1/24 = 4.2% | 9/44 = 20.5% | 11/75 = 14.7% | 23/69 = 33.3% |
| alerted2 | — | 1/2 = 50.0% | 2/2 = 100.0% | 4/9 = 44.4% | 10/11 = 90.9% | 10/10 = 100.0% | 21/28 = 75.0% | 38/46 = 82.6% | 37/42 = 88.1% |
| alerted2_road | — | — | 2/3 = 66.7% | 3/7 = 42.9% | 5/6 = 83.3% | 5/5 = 100.0% | 16/17 = 94.1% | 27/37 = 73.0% | 37/48 = 77.1% |
| alerted1p3_road | — | — | 0/3 = 0.0% | 4/7 = 57.1% | 5/6 = 83.3% | 5/5 = 100.0% | 16/17 = 94.1% | 34/37 = 91.9% | 43/48 = 89.6% |
| alerted1p3_rf_road | — | — | 0/1 = 0.0% | 4/6 = 66.7% | 2/2 = 100.0% | 3/3 = 100.0% | 9/9 = 100.0% | 20/22 = 90.9% | 20/23 = 87.0% |
