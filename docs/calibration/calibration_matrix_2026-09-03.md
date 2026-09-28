# Hindcast calibration matrix — 2026-09-03

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [stacked_rings_fix_2026-09-27.md](stacked_rings_fix_2026-09-27.md) §4.1. **Affected here:** every row. August moves by at most 3.4 pp (D_gate_me071 98.8 → 98.5 %). July moves 14–22 pp for every floored variant (D_gate_me071 83.1 → 99.7 %), and skill is 1.01–1.11 rather than ≈ 1.0. Full corrected tables in this format: `calibration_matrix_2026-09-27_corrected.md`. The text below is left as written.

Fix-program items 2/3/5 (docs/FIX_PROGRAM_2026-09-03.md): which ROS-floor
physics and which ensemble model-error term the live watcher should run,
judged on the two-sided scorecard (docs/calibration/SCORECARD.md). Every
variant carries the as-deployed gust blend 0.5. Pairing = the harness's
(every consecutive WFIGS snapshot pair with 0 < dt <= horizon-6 h), so the
cell counts differ from the review-era 18-window August table; the
comparison is variant-vs-variant on identical pairs. Dangerous polarity:
late miss UP, PIT extreme mass UP. Area/skill show what coverage costs.

## August 2026 corpus (scratch_review/hindcast_aug_data)

| variant | pairs / cells / incidents | p10 adequacy | late miss | area p10 (median) | null adequacy | skill | PIT extreme mass (obs earlier than all members / later than all) | PIT chi2 | head ratio p10 / p50 / nominal (median) | p10 adequacy ≤6h / 6-12h / 12-24h / >24h | equal-incident mean [LOO min–max] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F_nofloor — gust blend 0.5, no ROS floor (the raw engine + blend) | 27 / 145,760 / 11 | 78.6% | 21.4% | 4.65x | 61.5% | 1.28x | 82.4% (53% early / 29% late) | 237,701 | 1.45 / 1.26 / 1.27 | 0.0% / 38.3% / 89.3% / 87.9% | 83.9% [73.8%–84.9%] |
| H_nofloor_me050 — F + model-error sd(log) 0.50 (physics + honest band, no floor) | 27 / 145,760 / 11 | 88.1% | 11.9% | 6.23x | 70.6% | 1.25x | 65.0% (43% early / 22% late) | 122,208 | 2.21 / 1.23 / 1.27 | 0.0% / 45.7% / 95.5% / 98.9% | 90.2% [79.8%–93.5%] |
| H_nofloor_me071 — F + model-error sd(log) 0.71 (physics + honest band, no floor) | 27 / 145,760 / 11 | 90.5% | 9.5% | 8.23x | 73.8% | 1.23x | 52.6% (34% early / 18% late) | 67,793 | 2.48 / 1.24 / 1.27 | 0.0% / 53.1% / 97.9% / 99.7% | 92.5% [83.4%–94.5%] |
| A_deployed — as deployed since 2026-07-28: blend 0.5 + floor 0.10 (ungated, all classes) | 27 / 145,760 / 11 | 94.2% | 5.8% | 19.38x | 88.2% | 1.07x | 81.0% (12% early / 69% late) | 361,816 | 3.81 / 3.81 / 3.77 | 0.0% / 70.7% / 99.3% / 100.0% | 96.8% [89.6%–99.4%] |
| B_gate — A + extinction gate | 27 / 145,760 / 11 | 94.2% | 5.8% | 19.38x | 88.2% | 1.07x | 80.9% (12% early / 68% late) | 360,020 | 3.81 / 3.78 / 3.78 | 0.0% / 70.7% / 99.3% / 100.0% | 96.7% [89.6%–99.4%] |
| C_gate_grass — A + extinction gate + GR/GS only | 27 / 145,760 / 11 | 93.6% | 6.4% | 10.71x | 86.5% | 1.08x | 78.9% (15% early / 64% late) | 308,528 | 2.71 / 2.58 / 2.58 | 0.0% / 68.7% / 98.9% / 99.7% | 93.8% [88.5%–98.6%] |
| D_gate_me035 — B + model-error sd(log) 0.35 | 27 / 145,760 / 11 | 96.2% | 3.8% | 22.23x | 90.2% | 1.07x | 66.9% (10% early / 57% late) | 230,646 | 3.92 / 3.71 / 3.78 | 0.0% / 80.5% / 99.8% / 100.0% | 97.8% [93.1%–99.2%] |
| D_gate_me050 — B + model-error sd(log) 0.50 | 27 / 145,760 / 11 | 97.2% | 2.8% | 22.69x | 91.2% | 1.07x | 59.2% (8% early / 51% late) | 179,732 | 3.96 / 3.66 / 3.78 | 0.0% / 85.4% / 99.8% / 100.0% | 98.3% [94.9%–99.2%] |
| D_gate_me071 — B + model-error sd(log) 0.71 (the review's measured error) | 27 / 145,760 / 11 | 98.8% | 1.2% | 24.70x | 92.8% | 1.06x | 50.1% (6% early / 44% late) | 130,091 | 4.06 / 3.71 / 3.78 | 0.0% / 93.8% / 100.0% / 100.0% | 99.2% [97.8%–99.6%] |
| E_gategrass_me050 — C + model-error sd(log) 0.50 | 27 / 145,760 / 11 | 96.9% | 3.1% | 11.89x | 90.1% | 1.08x | 56.4% (10% early / 47% late) | 143,207 | 3.35 / 2.53 / 2.58 | 0.0% / 84.1% / 99.8% / 100.0% | 97.3% [94.4%–98.8%] |
| E_gategrass_me071 — C + model-error sd(log) 0.71 | 27 / 145,760 / 11 | 98.7% | 1.3% | 15.30x | 91.9% | 1.07x | 46.8% (7% early / 40% late) | 99,790 | 3.88 / 2.53 / 2.58 | 0.0% / 93.3% / 100.0% / 100.0% | 98.8% [97.6%–99.4%] |
| I_gategrass05_me050 — HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.50 | 27 / 145,760 / 11 | 91.9% | 8.1% | 9.18x | 84.2% | 1.09x | 52.5% (21% early / 32% late) | 55,395 | 2.81 / 1.53 / 1.53 | 0.0% / 58.8% / 99.2% / 99.9% | 93.7% [85.5%–96.2%] |
| I_gategrass05_me071 — HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.71 | 27 / 145,760 / 11 | 94.1% | 5.9% | 10.98x | 86.7% | 1.09x | 39.8% (14% early / 26% late) | 26,787 | 3.19 / 1.53 / 1.53 | 0.0% / 69.3% / 99.8% / 100.0% | 95.5% [89.3%–97.1%] |
| G_gate_p50 — B + p50 nominal bound (ladder median; p10 metrics unchanged by construction) | 27 / 145,760 / 11 | 94.2% | 5.8% | 19.38x | 88.2% | 1.07x | 80.9% (12% early / 68% late) | 360,020 | 3.81 / 3.82 / 3.78 | 0.0% / 70.7% / 99.3% / 100.0% | 96.7% [89.6%–99.4%] |

## July 2026 OOS corpus (scratch_review/hindcast_oos_data)

| variant | pairs / cells / incidents | p10 adequacy | late miss | area p10 (median) | null adequacy | skill | PIT extreme mass (obs earlier than all members / later than all) | PIT chi2 | head ratio p10 / p50 / nominal (median) | p10 adequacy ≤6h / 6-12h / 12-24h / >24h | equal-incident mean [LOO min–max] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F_nofloor — gust blend 0.5, no ROS floor (the raw engine + blend) | 18 / 109,040 / 7 | 55.7% | 44.3% | 5.38x | 57.5% | 0.97x | 88.3% (56% early / 32% late) | 211,354 | 1.95 / 1.40 / 1.45 | 0.0% / 48.6% / 45.5% / 95.8% | 66.2% [50.3%–73.1%] |
| H_nofloor_me050 — F + model-error sd(log) 0.50 (physics + honest band, no floor) | 18 / 109,040 / 7 | 60.5% | 39.5% | 7.33x | 62.1% | 0.97x | 75.0% (50% early / 25% late) | 139,841 | 2.24 / 1.41 / 1.45 | 0.0% / 53.6% / 50.4% / 99.8% | 69.2% [53.9%–77.1%] |
| H_nofloor_me071 — F + model-error sd(log) 0.71 (physics + honest band, no floor) | 18 / 109,040 / 7 | 63.3% | 36.7% | 8.30x | 64.7% | 0.98x | 66.6% (46% early / 21% late) | 104,863 | 2.50 / 1.43 / 1.45 | 0.0% / 57.9% / 53.3% / 100.0% | 71.4% [56.7%–79.4%] |
| A_deployed — as deployed since 2026-07-28: blend 0.5 + floor 0.10 (ungated, all classes) | 18 / 109,040 / 7 | 76.9% | 23.1% | 12.43x | 76.9% | 1.00x | 91.9% (32% early / 60% late) | 245,709 | 3.71 / 3.44 / 3.44 | 0.0% / 88.1% / 61.9% / 100.0% | 88.7% [71.6%–94.8%] |
| B_gate — A + extinction gate | 18 / 109,040 / 7 | 76.9% | 23.1% | 12.21x | 76.9% | 1.00x | 91.9% (32% early / 60% late) | 245,717 | 3.71 / 3.43 / 3.44 | 0.0% / 88.1% / 61.9% / 100.0% | 88.7% [71.6%–94.8%] |
| C_gate_grass — A + extinction gate + GR/GS only | 18 / 109,040 / 7 | 68.2% | 31.8% | 9.48x | 71.9% | 0.95x | 90.9% (41% early / 50% late) | 211,092 | 3.14 / 2.87 / 2.99 | 0.0% / 63.4% / 59.7% / 99.5% | 74.1% [62.2%–82.1%] |
| D_gate_me035 — B + model-error sd(log) 0.35 | 18 / 109,040 / 7 | 79.7% | 20.3% | 12.83x | 79.4% | 1.00x | 85.4% (28% early / 57% late) | 204,804 | 3.77 / 3.45 / 3.44 | 0.0% / 95.3% / 63.2% / 100.0% | 90.4% [75.1%–97.9%] |
| D_gate_me050 — B + model-error sd(log) 0.50 | 18 / 109,040 / 7 | 81.5% | 18.5% | 13.57x | 81.1% | 1.00x | 80.2% (26% early / 54% late) | 173,829 | 3.90 / 3.40 / 3.44 | 0.0% / 97.6% / 65.2% / 100.0% | 91.4% [77.1%–99.0%] |
| D_gate_me071 — B + model-error sd(log) 0.71 (the review's measured error) | 18 / 109,040 / 7 | 83.1% | 16.9% | 14.19x | 83.6% | 0.99x | 71.6% (22% early / 50% late) | 130,957 | 3.95 / 3.44 / 3.44 | 0.0% / 98.7% / 67.7% / 100.0% | 92.2% [78.8%–99.4%] |
| E_gategrass_me050 — C + model-error sd(log) 0.50 | 18 / 109,040 / 7 | 70.7% | 29.3% | 11.33x | 75.1% | 0.94x | 79.2% (37% early / 43% late) | 139,246 | 3.58 / 3.02 / 2.99 | 0.0% / 65.6% / 63.1% / 99.9% | 75.3% [65.0%–83.4%] |
| E_gategrass_me071 — C + model-error sd(log) 0.71 | 18 / 109,040 / 7 | 73.7% | 26.3% | 12.15x | 77.3% | 0.95x | 72.0% (35% early / 37% late) | 103,554 | 3.82 / 3.00 / 2.99 | 0.0% / 70.9% / 65.9% / 100.0% | 77.6% [68.6%–85.9%] |
| I_gategrass05_me050 — HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.50 | 18 / 109,040 / 7 | 66.5% | 33.5% | 8.80x | 70.0% | 0.95x | 74.9% (42% early / 33% late) | 119,151 | 2.96 / 2.26 / 2.17 | 0.0% / 59.8% / 58.4% / 99.9% | 72.8% [60.1%–80.5%] |
| I_gategrass05_me071 — HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.71 | 18 / 109,040 / 7 | 68.0% | 32.0% | 10.40x | 72.1% | 0.94x | 66.5% (39% early / 28% late) | 84,086 | 3.18 / 2.25 / 2.17 | 0.0% / 62.4% / 59.9% / 100.0% | 74.2% [61.9%–82.0%] |
| G_gate_p50 — B + p50 nominal bound (ladder median; p10 metrics unchanged by construction) | 18 / 109,040 / 7 | 76.9% | 23.1% | 12.21x | 76.9% | 1.00x | 91.9% (32% early / 60% late) | 245,717 | 3.71 / 3.44 / 3.44 | 0.0% / 88.1% / 61.9% / 100.0% | 88.7% [71.6%–94.8%] |

