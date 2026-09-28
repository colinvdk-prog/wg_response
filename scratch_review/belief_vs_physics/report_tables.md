
### Side experiment: deployed physics vs physics with grassonly (same steps, same scoring)

| phase | steps | area error: deployed / variant / belief as built | P10 adequacy deployed / variant | P50 hit deployed / variant | false alarm P50 (% of fire) deployed / variant |
|---|---|---|---|---|---|
| growing | 70 | 5.6x / 2.7x / 5.3x | 99.7 % / 95.3 % | 98 % / 85 % | 454 % / 176 % |
| moderate | 50 | 4.1x / 2.3x / 2.9x | 99.9 % / 98.2 % | 100 % / 92 % | 311 % / 133 % |
| stalled | 135 | 3.0x / 2.0x / 2.5x | 99.8 % / 98.6 % | 99 % / 94 % | 195 % / 96 % |
| all | 255 | 3.5x / 2.3x / 3.0x | 99.8 % / 97.1 % | 99 % / 90 % | 253 % / 130 % |

### HARNESS domain (4 km pad): 11 fires, 255 scored steps of 276

| phase | steps (fires) | area error, median [IQR] phys -> belief | median forecast/observed area phys -> belief | P10 adequacy phys / belief / unguarded | P50 hit phys -> belief | false alarm P50, median ac (% of fire) phys -> belief | belief better / worse / same |
|---|---|---|---|---|---|---|---|
| growing | 70 (10) | 1.72 [1.33-2.20] -> 1.67 [1.27-2.20] | 5.6x -> 5.3x | 99.7 % / 99.7 % / 97.3 % | 98 % -> 96 % | 71,349 (454 %) -> 67,046 (431 %) | 8 / 0 / 62 |
| moderate | 50 (10) | 1.42 [1.11-1.77] -> 1.07 [0.41-1.32] | 4.1x -> 2.9x | 99.9 % / 99.9 % / 95.5 % | 100 % -> 92 % | 129,546 (311 %) -> 68,606 (189 %) | 25 / 0 / 25 |
| stalled | 135 (11) | 1.09 [0.81-1.31] -> 0.90 [0.50-1.20] | 3.0x -> 2.5x | 99.8 % / 99.9 % / 96.0 % | 99 % -> 93 % | 138,883 (195 %) -> 85,570 (143 %) | 103 / 3 / 29 |
| all | 255 (11) | 1.27 [0.97-1.73] -> 1.09 [0.64-1.54] | 3.5x -> 3.0x | 99.8 % / 99.8 % / 96.5 % | 99 % -> 94 % | 124,036 (253 %) -> 71,069 (198 %) | 136 / 3 / 116 |

| phase | what the applied belief did (steps) |
|---|---|
| growing | rapid: held at physics 40; new weather from hour 0: physics 11; prior: no evidence yet 10; slowed 8; reset: surprise, physics 1 |
| moderate | slowed 25; rapid: held at physics 21; new weather from hour 0: physics 3; reset: surprise, physics 1 |
| stalled | slowed 108; new weather from hour 0: physics 24; rapid: held at physics 3 |
| all | slowed 141; rapid: held at physics 64; new weather from hour 0: physics 38; prior: no evidence yet 10; reset: surprise, physics 2 |

| fire | scored steps (grow/mod/stall) | area error phys -> belief (median) | P10 adequacy phys / belief / unguarded | P50 hit phys -> belief | learned multiplier at the end (P10-P50-P90) | windows modeled (censored) | resets | rapid captures | slowed steps |
|---|---|---|---|---|---|---|---|---|---|
| Willow (CO) | 19 (5/7/7) | 1.80 -> 1.72 | 100.0 % / 100.0 % / 97.6 % | 100 % -> 87 % | 0.03-0.04-0.08x | 19 (19) | 0 | 3 | 13 |
| Grasshopper (OR) | 28 (8/6/14) | 1.05 -> 0.98 | 100.0 % / 100.0 % / 100.0 % | 100 % -> 100 % | 0.07-0.13-0.23x | 30 (30) | 0 | 15 | 13 |
| Hagen (OR) | 36 (8/9/19) | 1.36 -> 1.04 | 99.8 % / 99.8 % / 92.3 % | 100 % -> 88 % | 0.11-0.32-0.93x | 37 (29) | 0 | 9 | 24 |
| Little Giant (WA) | 51 (12/12/27) | 0.97 -> 0.65 | 99.3 % / 99.4 % / 91.0 % | 97 % -> 86 % | 0.07-0.12-0.26x | 52 (24) | 1 | 15 | 29 |
| Sinlahekin (WA) | 27 (8/2/17) | 0.95 -> 0.93 | 100.0 % / 100.0 % / 99.7 % | 100 % -> 99 % | 0.08-0.16-0.36x | 29 (24) | 0 | 11 | 9 |
| Sand Creek (MT) | 16 (7/0/9) | 1.77 -> 1.73 | 100.0 % / 100.0 % / 99.9 % | 100 % -> 100 % | 0.09-0.28-0.82x | 19 (17) | 0 | 6 | 10 |
| Border 2 (WA) | 8 (0/2/6) | 0.81 -> 0.68 | 98.7 % / 98.8 % / 91.5 % | 98 % -> 86 % | 0.15-0.35-0.99x | 14 (8) | 0 | 1 | 8 |
| Bobcat Lakes (MT) | 18 (6/4/8) | 1.89 -> 1.77 | 100.0 % / 100.0 % / 98.5 % | 100 % -> 97 % | 0.06-0.13-0.45x | 20 (20) | 0 | 5 | 9 |
| Timber (CA) | 23 (8/3/12) | 1.31 -> 1.18 | 100.0 % / 100.0 % / 99.7 % | 100 % -> 96 % | 0.05-0.08-0.14x | 24 (20) | 0 | 9 | 14 |
| Plaskett (CA) | 8 (4/1/3) | 1.10 -> 1.10 | 100.0 % / 100.0 % / 100.0 % | 98 % -> 98 % | 0.11-0.27-0.75x | 9 (7) | 0 | 6 | 0 |
| Aspen Acres (CO) | 21 (4/4/13) | 1.20 -> 1.16 | 100.0 % / 100.0 % / 99.9 % | 100 % -> 100 % | 0.08-0.16-0.42x | 23 (23) | 0 | 6 | 12 |

| fire | perimeters until learned < 0.3x / < 0.1x | learned median over the life (min-max) | captures with learned P90 < 1x |
|---|---|---|---|
| Willow (CO) | 2 / 4 | 0.04-0.48x | 90 % |
| Grasshopper (OR) | 2 / - | 0.10-0.32x | 94 % |
| Hagen (OR) | 3 / 10 | 0.06-0.41x | 95 % |
| Little Giant (WA) | 2 / 9 | 0.07-0.31x | 94 % |
| Sinlahekin (WA) | 2 / 21 | 0.09-0.46x | 93 % |
| Sand Creek (MT) | 2 / 6 | 0.06-0.49x | 90 % |
| Border 2 (WA) | 3 / 8 | 0.07-0.62x | 87 % |
| Bobcat Lakes (MT) | 2 / 6 | 0.07-0.40x | 90 % |
| Timber (CA) | 1 / 6 | 0.06-0.30x | 96 % |
| Plaskett (CA) | 5 / - | 0.17-0.51x | 90 % |
| Aspen Acres (CO) | 2 / 8 | 0.08-0.35x | 96 % |

Tracking: 276 modeled windows, 221 censored, 55 with a belief prediction; surprises 1; physics nominal / observed growth median 55.9x; belief-pred error 1.72 vs physics 3.51 on the same windows; observed <= belief P90 in 98 %

Fire-cluster bootstrap, 5-95 % intervals:
  growing   phys_area_err [1.56, 1.84]; bel_area_err [1.53, 1.84]; delta_area_err_median [0.00, 0.00]; phys_p50_hit [0.96, 1.00]; bel_p50_hit [0.91, 0.99]; belu_p10 [0.93, 1.00]; phys_fa_rel [3.77, 5.31]; bel_fa_rel [3.69, 5.31]
  moderate  phys_area_err [1.13, 1.73]; bel_area_err [0.62, 1.23]; delta_area_err_median [-0.71, 0.00]; phys_p50_hit [0.99, 1.00]; bel_p50_hit [0.86, 0.99]; belu_p10 [0.91, 1.00]; phys_fa_rel [2.09, 4.59]; bel_fa_rel [0.84, 2.40]
  stalled   phys_area_err [0.91, 1.27]; bel_area_err [0.71, 1.12]; delta_area_err_median [-0.13, -0.05]; phys_p50_hit [0.99, 1.00]; bel_p50_hit [0.87, 0.99]; belu_p10 [0.93, 0.99]; phys_fa_rel [1.47, 2.53]; bel_fa_rel [1.02, 2.03]
  all       phys_area_err [1.10, 1.55]; bel_area_err [0.98, 1.26]; delta_area_err_median [-0.07, 0.00]; phys_p50_hit [0.98, 1.00]; bel_p50_hit [0.89, 0.99]; belu_p10 [0.94, 1.00]; phys_fa_rel [1.98, 3.68]; bel_fa_rel [1.65, 2.51]

### HARNESS domain (4 km pad): the belief AS BUILT vs what-if rule settings (same learning, same physics)

| phase | arm | area error (median) | P10 adequacy guarded / unguarded | P50 hit | false alarm P50 (median % of fire) | better / worse / same vs physics |
|---|---|---|---|---|---|---|
| growing | physics | 1.72 | 99.7 % | 98 % | 454 % | - |
| growing | belief, as built | 1.67 | 99.7 % / 97.3 % | 96 % | 431 % | 8 / 0 / 62 |
| growing | belief, new-weather rule off | 1.67 | 99.7 % / 96.0 % | 94 % | 431 % | 9 / 0 / 61 |
| growing | belief, rapid rule off | 1.17 | 99.7 % / 92.7 % | 89 % | 225 % | 48 / 0 / 22 |
| growing | belief, both rules off | 0.47 | 99.7 % / 80.2 % | 66 % | 67 % | 59 / 0 / 11 |
| moderate | physics | 1.42 | 99.9 % | 100 % | 311 % | - |
| moderate | belief, as built | 1.07 | 99.9 % / 95.5 % | 92 % | 189 % | 25 / 0 / 25 |
| moderate | belief, new-weather rule off | 0.60 | 99.9 % / 92.7 % | 88 % | 82 % | 28 / 0 / 22 |
| moderate | belief, rapid rule off | 0.52 | 99.9 % / 90.7 % | 82 % | 69 % | 46 / 0 / 4 |
| moderate | belief, both rules off | 0.31 | 99.9 % / 84.2 % | 74 % | 38 % | 49 / 0 / 1 |
| stalled | physics | 1.09 | 99.8 % | 99 % | 195 % | - |
| stalled | belief, as built | 0.90 | 99.9 % / 96.0 % | 93 % | 143 % | 103 / 3 / 29 |
| stalled | belief, new-weather rule off | 0.27 | 99.9 % / 92.0 % | 84 % | 29 % | 128 / 0 / 7 |
| stalled | belief, rapid rule off | 0.90 | 99.9 % / 96.0 % | 93 % | 143 % | 106 / 3 / 26 |
| stalled | belief, both rules off | 0.27 | 99.9 % / 90.9 % | 82 % | 29 % | 135 / 0 / 0 |
| all | physics | 1.27 | 99.8 % | 99 % | 253 % | - |
| all | belief, as built | 1.09 | 99.8 % / 96.5 % | 94 % | 198 % | 136 / 3 / 116 |
| all | belief, new-weather rule off | 0.42 | 99.8 % / 93.9 % | 89 % | 50 % | 165 / 0 / 90 |
| all | belief, rapid rule off | 0.91 | 99.8 % / 93.5 % | 89 % | 146 % | 200 / 3 / 52 |
| all | belief, both rules off | 0.30 | 99.8 % / 84.9 % | 73 % | 35 % | 243 / 0 / 12 |

### WIDE domain (20 km pad): 11 fires, 255 scored steps of 276

| phase | steps (fires) | area error, median [IQR] phys -> belief | median forecast/observed area phys -> belief | P10 adequacy phys / belief / unguarded | P50 hit phys -> belief | false alarm P50, median ac (% of fire) phys -> belief | belief better / worse / same |
|---|---|---|---|---|---|---|---|
| growing | 70 (10) | 1.84 [1.48-2.37] -> 1.81 [1.42-2.37] | 6.3x -> 6.1x | 99.8 % / 99.8 % / 97.8 % | 98 % -> 96 % | 106,068 (527 %) -> 94,469 (511 %) | 8 / 0 / 62 |
| moderate | 50 (10) | 1.61 [1.26-2.05] -> 1.11 [0.32-1.59] | 5.0x -> 3.0x | 99.9 % / 99.9 % / 93.4 % | 100 % -> 89 % | 141,097 (397 %) -> 101,500 (201 %) | 25 / 0 / 25 |
| stalled | 135 (11) | 1.38 [0.91-1.89] -> 1.00 [0.46-1.67] | 4.0x -> 2.7x | 99.9 % / 99.9 % / 94.7 % | 99 % -> 90 % | 190,036 (297 %) -> 118,053 (170 %) | 107 / 1 / 27 |
| all | 255 (11) | 1.59 [1.11-2.07] -> 1.27 [0.64-1.88] | 4.9x -> 3.6x | 99.9 % / 99.9 % / 95.8 % | 99 % -> 93 % | 152,996 (391 %) -> 110,665 (254 %) | 140 / 1 / 114 |

| phase | what the applied belief did (steps) |
|---|---|
| growing | rapid: held at physics 40; new weather from hour 0: physics 11; prior: no evidence yet 10; slowed 8; reset: surprise, physics 1 |
| moderate | slowed 25; rapid: held at physics 21; new weather from hour 0: physics 3; reset: surprise, physics 1 |
| stalled | slowed 108; new weather from hour 0: physics 24; rapid: held at physics 3 |
| all | slowed 141; rapid: held at physics 64; new weather from hour 0: physics 38; prior: no evidence yet 10; reset: surprise, physics 2 |

| fire | scored steps (grow/mod/stall) | area error phys -> belief (median) | P10 adequacy phys / belief / unguarded | P50 hit phys -> belief | learned multiplier at the end (P10-P50-P90) | windows modeled (censored) | resets | rapid captures | slowed steps |
|---|---|---|---|---|---|---|---|---|---|
| Willow (CO) | 19 (5/7/7) | 1.98 -> 1.67 | 100.0 % / 100.0 % / 95.5 % | 100 % -> 83 % | 0.02-0.04-0.07x | 19 (0) | 0 | 3 | 13 |
| Grasshopper (OR) | 28 (8/6/14) | 1.55 -> 1.50 | 100.0 % / 100.0 % / 100.0 % | 100 % -> 100 % | 0.04-0.09-0.20x | 30 (13) | 0 | 15 | 13 |
| Hagen (OR) | 36 (8/9/19) | 1.48 -> 1.03 | 99.9 % / 99.9 % / 89.9 % | 100 % -> 84 % | 0.17-0.45-1.14x | 37 (8) | 0 | 9 | 24 |
| Little Giant (WA) | 51 (12/12/27) | 1.00 -> 0.63 | 99.5 % / 99.5 % / 89.5 % | 97 % -> 83 % | 0.05-0.11-0.34x | 52 (2) | 1 | 15 | 29 |
| Sinlahekin (WA) | 27 (8/2/17) | 1.11 -> 1.11 | 100.0 % / 100.0 % / 99.2 % | 99 % -> 98 % | 0.06-0.18-0.58x | 29 (6) | 0 | 11 | 9 |
| Sand Creek (MT) | 16 (7/0/9) | 2.40 -> 2.15 | 100.0 % / 100.0 % / 99.9 % | 100 % -> 100 % | 0.14-0.38-0.95x | 19 (9) | 0 | 6 | 10 |
| Border 2 (WA) | 8 (0/2/6) | 0.85 -> 0.66 | 98.4 % / 98.4 % / 87.6 % | 98 % -> 82 % | 0.20-0.54-1.37x | 14 (6) | 0 | 1 | 8 |
| Bobcat Lakes (MT) | 18 (6/4/8) | 2.77 -> 2.64 | 100.0 % / 100.0 % / 96.9 % | 100 % -> 95 % | 0.09-0.26-0.69x | 20 (12) | 0 | 5 | 9 |
| Timber (CA) | 23 (8/3/12) | 1.96 -> 1.48 | 100.0 % / 100.0 % / 99.8 % | 100 % -> 97 % | 0.02-0.05-0.19x | 24 (7) | 0 | 9 | 14 |
| Plaskett (CA) | 8 (4/1/3) | 1.51 -> 1.51 | 100.0 % / 100.0 % / 100.0 % | 99 % -> 99 % | 0.17-0.41-1.00x | 9 (2) | 0 | 6 | 0 |
| Aspen Acres (CO) | 21 (4/4/13) | 1.60 -> 1.43 | 100.0 % / 100.0 % / 99.7 % | 100 % -> 99 % | 0.07-0.25-0.72x | 23 (10) | 0 | 6 | 12 |

| fire | perimeters until learned < 0.3x / < 0.1x | learned median over the life (min-max) | captures with learned P90 < 1x |
|---|---|---|---|
| Willow (CO) | 2 / 3 | 0.04-0.53x | 90 % |
| Grasshopper (OR) | 2 / 3 | 0.05-0.51x | 94 % |
| Hagen (OR) | 3 / 9 | 0.04-0.55x | 92 % |
| Little Giant (WA) | 2 / 9 | 0.06-0.31x | 94 % |
| Sinlahekin (WA) | 2 / 12 | 0.05-0.46x | 90 % |
| Sand Creek (MT) | 2 / 5 | 0.05-0.56x | 85 % |
| Border 2 (WA) | 1 / 5 | 0.06-0.54x | 80 % |
| Bobcat Lakes (MT) | 2 / 4 | 0.04-0.49x | 86 % |
| Timber (CA) | 1 / 7 | 0.04-0.30x | 96 % |
| Plaskett (CA) | 3 / - | 0.25-0.50x | 90 % |
| Aspen Acres (CO) | 2 / 7 | 0.03-0.32x | 96 % |

Tracking: 276 modeled windows, 75 censored, 201 with a belief prediction; surprises 1; physics nominal / observed growth median 133.4x; belief-pred error 2.34 vs physics 4.65 on the same windows; observed <= belief P90 in 100 %

Fire-cluster bootstrap, 5-95 % intervals:
  growing   phys_area_err [1.65, 2.07]; bel_area_err [1.64, 2.07]; delta_area_err_median [0.00, 0.00]; phys_p50_hit [0.96, 1.00]; bel_p50_hit [0.92, 0.99]; belu_p10 [0.94, 1.00]; phys_fa_rel [4.23, 6.90]; bel_fa_rel [4.14, 6.90]
  moderate  phys_area_err [1.26, 1.98]; bel_area_err [0.78, 1.41]; delta_area_err_median [-0.91, 0.00]; phys_p50_hit [1.00, 1.00]; bel_p50_hit [0.81, 0.97]; belu_p10 [0.87, 0.99]; phys_fa_rel [2.52, 6.19]; bel_fa_rel [1.17, 3.08]
  stalled   phys_area_err [1.06, 1.77]; bel_area_err [0.69, 1.43]; delta_area_err_median [-0.35, -0.15]; phys_p50_hit [0.99, 1.00]; bel_p50_hit [0.84, 0.97]; belu_p10 [0.92, 0.99]; phys_fa_rel [1.86, 4.87]; bel_fa_rel [0.99, 3.16]
  all       phys_area_err [1.31, 1.85]; bel_area_err [1.07, 1.57]; delta_area_err_median [-0.19, 0.00]; phys_p50_hit [0.98, 1.00]; bel_p50_hit [0.88, 0.98]; belu_p10 [0.93, 0.99]; phys_fa_rel [2.70, 5.34]; bel_fa_rel [1.90, 3.78]

### WIDE domain (20 km pad): the belief AS BUILT vs what-if rule settings (same learning, same physics)

| phase | arm | area error (median) | P10 adequacy guarded / unguarded | P50 hit | false alarm P50 (median % of fire) | better / worse / same vs physics |
|---|---|---|---|---|---|---|
| growing | physics | 1.84 | 99.8 % | 98 % | 527 % | - |
| growing | belief, as built | 1.81 | 99.8 % / 97.8 % | 96 % | 511 % | 8 / 0 / 62 |
| growing | belief, new-weather rule off | 1.81 | 99.8 % / 96.2 % | 94 % | 511 % | 9 / 0 / 61 |
| moderate | physics | 1.61 | 99.9 % | 100 % | 397 % | - |
| moderate | belief, as built | 1.11 | 99.9 % / 93.4 % | 89 % | 201 % | 25 / 0 / 25 |
| moderate | belief, new-weather rule off | 0.64 | 99.9 % / 89.9 % | 84 % | 89 % | 28 / 0 / 22 |
| stalled | physics | 1.38 | 99.9 % | 99 % | 297 % | - |
| stalled | belief, as built | 1.00 | 99.9 % / 94.7 % | 90 % | 170 % | 107 / 1 / 27 |
| stalled | belief, new-weather rule off | 0.19 | 99.9 % / 88.0 % | 76 % | 20 % | 128 / 0 / 7 |
| all | physics | 1.59 | 99.9 % | 99 % | 391 % | - |
| all | belief, as built | 1.27 | 99.9 % / 95.8 % | 93 % | 254 % | 140 / 1 / 114 |
| all | belief, new-weather rule off | 0.35 | 99.9 % / 92.2 % | 86 % | 40 % | 165 / 0 / 90 |
