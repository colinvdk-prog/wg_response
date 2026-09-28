# Stacked perimeter rows in the hindcast corpora: fix, repair, corrected numbers (2026-09-27)

Record for branch `fix/hindcast-stacked-rings`. No engine code, no `wgresponse/contracts.py`
change, no deployed configuration change. The documents that cite the old numbers carry a dated
correction note that points here; their text is otherwise unchanged (§6).

## Summary

**The bug.** `tools/hindcast_harness._fetch_incident` built each perimeter snapshot by unioning
the rings of every `WFIGS_Daily_Perimeters_Public` row that shares a capture time
(`poly_PolygonDateTime`). Those rows are **versions of one polygon**, not pieces of one fire. The
layer appends a row each time a source record changes, so a capture time accumulates re-copies,
in-place edits that were never re-stamped, and re-uploads under a new record id. Both
rasterizers (the engine's seed and the scoring mask) use the even-odd rule, so stacked copies
cancel and near-copies leave slivers. **42 snapshots** across the three cached corpora were
stacks. 5 rasterized to nothing and 21 to under half their real area; some stacked 19–47
versions spanning up to 26 days of later growth. The belief-vs-physics study found it
(`docs/review/belief_vs_physics_2026-09-27.md` §8, item 1).

**The fix.** Each capture time is now its **first-published row**. A later row under the same
time is kept only if it is spatially disjoint (a genuine separate piece; none exist in these
corpora). Growth gating uses the first publication's acreage. The cached corpora were rebuilt at
their cached capture times, with an exact-fidelity check, and every documented replay was re-run
offline: 100 replays, before and after, on a byte-identical engine.

**What the corrected numbers say:**

- **August corpus and the deployed variant: essentially unchanged.** D_gate_me071 moves from
  98.8 % to 98.5 % p10 adequacy, and late miss from 1.2 % to 1.5 %. The August 93.8 % headline
  becomes 93.6 %. The measured model error sd(log) goes from 0.710 to 0.698, so the deployed
  `--model-error 0.71` still stands. The null-model area efficiency goes from 1.85× to 1.88×.
- **July OOS corpus: the headline changes a lot.** The blend-0.5 strict pool goes from 48.6 % to
  **85.8 %** coverage (8 → 13 windows, 4 → 6 fires). With the grass floor it goes from 64.8 % to
  **100 %**. The deployed D071 on the July matrix goes from 83.1 % to **99.7 %**, with late miss
  16.9 % → 0.3 %.
- **The Porcupine Ridge "blow-up" was an artifact.** It was documented as 22.5 km in 12.9 h,
  74k ac, a 13× under-forecast, 56 % of the pool, and "beyond every variant". Its T0 was two
  identical 4,595-ac records that cancelled to **zero cells**. Its T1 was 31 versions of one
  record, edited over 12 days. Corrected: **3.7 km, 11,139 ac, head ratio 0.52×, 54.7 % late at
  blend 0.5, 0 % late with the grass floor, 7.5 % of the pool.**
- **The extreme-regime posture now has no positive example.** Its thresholds were placed on
  n = 1 (Porcupine). No corrected window has floored late miss above 50 %. The detector arms 5
  windows: four at 0 % floored-late, and one first-capture pair (Madre 07-03) at 15.4 %. The
posture is live in the watcher (`--extreme-regime`). It
  errs conservative, so this is an owner decision and nothing was changed (§7).
- **Pinned first pass: small move.** 68.8 % → 69.4 %. The named Madre pair now scores 27,023 ac of
  new growth instead of 35,071 (the GIS acreages imply about 27,100), and its NON-MONOTONE pair is
  now scored. **16 of the 20 NON-MONOTONE "garbage" pairs across the corpora were made by the
  bug.**

## 1. The snapshot rule, and why this revision rule

**Rule** (`tools/hindcast_lib.py`, SNAPSHOT RULE):
1. A capture time is represented by its **first-published row**, the smallest OBJECTID.
   OBJECTID order is the layer's append order.
2. A later row under the same capture time is kept only if it is a genuinely separate piece: its
   interior and the rows already kept overlap by at most 5 % of either polygon's area
   (`same_time_overlap_frac`). Overlapping rows are versions; they are dropped and recorded as
   `dropped_oids`. A kept row's own rings (multipolygon parts, holes) stay whole.
3. Growth gating in `pick_snapshot_times` uses the first publication's acreage, never the group
   maximum. The maximum let a later revision raise the bar and skip the next capture: Madre
   2025-07-05 20:53 was skipped this way.

**Why the first publication, not the latest revision** (the alternatives the ticket named:
latest `poly_DateCurrent`, largest OBJECTID). The attribute series of every corpus incident was
re-read on 2026-09-27 into `scratch_review/hindcast_stacked/daily_rows_2026-09-27/`, one GET
each. Over the 42 stacked snapshots (`rule_evidence.py` → `rule_evidence.md`):

- The kept first publication's edit time trails its stamp by a median **6.7 h** (max 66 h). The
  latest dropped version trails it by a median **37.9 h** (max **635 h**, 26 days).
- A later version is more than 10 % larger than the first publication in **26 of 42**. In-place
  edits pile growth onto the original stamp for days:
  - Crosswhite's 2026-07-17 06:43 record: 13,883 → 134,128 ac over 19 versions in 6.6 days.
  - Tartar's 2026-07-25 15:33 record: 1,937 → 97,737 ac over 47 versions in 8.5 days.
  - Kaiser Canyon's 2026-07-16 20:44 record: 2,170 → 46,103 ac.
- A latest-revision rule would date days of later growth to the original stamp. That inflates
  the observed growth when the snapshot is T1 and seeds too large a fire when it is T0.
- The first publication is also:
  - what a live system could have seen at the time; `wgresponse.fire_belief._observe_perimeter`
    ignores a perimeter whose time is not newer than the last one seen;
  - immutable as the layer accumulates edits (Willow's 2026-07-21 record was revised again on
    2026-09-16), so a re-fetch reproduces the corpus.
- The source-record id (`poly_SourceGlobalID`) cannot tell pieces from versions. 11 of the 42
  groups mix records, and every such record is another version of the whole fire, overlapping
  the first publication (Crosswhite 07-24 is two records of 175,079 ac each). Hence the spatial test.
- **No genuinely separate same-time piece exists anywhere in the three corpora.** Every rebuilt
  snapshot kept exactly one row, so the piece branch is pinned by synthetic tests only.
- The repaired polygons rasterize to their GIS acreage within a median **0.3 %**. Before the fix,
  5 stacks rasterized to **0 cells** and 21 to under half the first publication's area.

**Deliberately not changed.** The belief-vs-physics study also dropped rows published after a
newer capture ("watcher order"). The harness does not. About 30 such rows exist in these corpora,
and most are genuine captures published hours late: three of Gifford's five pinned captures would
go. The bogus-year rows (Cottonwood 2024, Sinlahekin 2020) sit outside every replay window, so no
scored pair uses them. The live-feed side of that hazard is a separate open item.

## 2. What was repaired

`py -3 tools/hindcast_harness.py repair --data-dir <corpus>` ran on each corpus: **40 read-only
GETs** in total, two per stacked incident (its attribute series, and the geometry of its cached
OBJECTIDs through the fetch's own query). Each file was rebuilt at its **cached capture times**
from the rows cached under each time. So every corpus keeps its pairs, and only geometry and
recorded acreage change.

Before writing anything, `repair` checked that the re-read rings, concatenated in OBJECTID order,
reproduce every cached snapshot exactly. All 20 re-read files passed; the other 6 had nothing
stacked and were only relabelled. Where the rebuilt rings
alone would move the replay domain, the pre-fix domain is pinned in the file (`domain_bbox`), so
the cached LANDFIRE tile keeps serving. That domain still contains every rebuilt ring with at
least the 4 km pad.

| corpus | incident files | snapshots rebuilt | same-time rows dropped | domains pinned | files only relabelled (nothing stacked) |
|---|---|---|---|---|---|
| `tools/hindcast_data` (pinned) | 4 | 2 (Madre) | 4 | Madre | Dillon, Gifford, Butler |
| `scratch_review/hindcast_oos_data` (July OOS) | 10 | 17 | 92 | Crosswhite, Willow, Stookey, Kaiser Canyon, Porcupine Ridge | Aspen Acres |
| `scratch_review/hindcast_aug_data` (August) | 12 | 23 | 195 | Little Giant, Crosswhite, Ptarmigan, Bald Mountain, Coleman Creek, Tartar | Bug, Sand Creek |
| `tools/hindcast_data_live` | 0 | — | — | — | (no cached incidents) |

Per-snapshot detail (kept and dropped OBJECTIDs, acreage before and after) is in each incident
file's `repair` block and in each corpus's `repair_log.json`. Weather series are kept as cached.
They were fetched at the pre-fix first-snapshot vertex centroid, which is 0–7.5 km from the
repaired one: Kaiser Canyon 7.5 km, Tartar 3.2 km, ELEPHANT 2.3 km, the rest under 1.6 km. A fresh
fetch would sample reanalysis at the new point; the before/after keeps the old point on purpose.

**The fixed picker would choose some capture times differently.** Rule 3 changes the selection for
7 incident-corpora on today's layer. The repair keeps the cached times, so a fresh `fetch` of
these incidents would not reproduce the repaired files exactly:

| corpus | incident | cached capture times (kept) | fixed picker on today's layer |
|---|---|---|---|
| pinned | Madre | … 07-04 23:37, **07-08 20:48** | … 07-04 23:37, **07-05 20:53** |
| July OOS | Crosswhite | 4 captures to 07-26 04:14 | adds 07-28 05:02 (partly rows published after the 07-28 fetch) |
| July OOS | Willow | 06-28, **06-29 17:22, 07-02 06:56**, 07-03, 07-05 | 06-28, **07-01 20:07**, 07-03, 07-05, **07-07 07:19** |
| July OOS | ELEPHANT | 07-12, **07-17 08:55**, 07-22 | 07-12, **07-15 00:34, 07-18 05:05**, 07-22 |
| August | Little Giant | …, **07-22 12:15**, 07-24 21:07 | …, 07-24 21:07, **07-26 09:51** |
| August | Crosswhite | …, **07-30 07:38** | …, **07-28 05:02** |
| August | Grasshopper | …, **08-01 04:13** | …, **07-31 09:31** |

## 3. How the numbers were re-run

Every documented number traces to one of these generators, all re-run **offline**:

- `scratch_review/calmatrix_run.sh` and `calmatrix2_run.sh`: both calibration matrices, 19
  variants × 2 corpora.
- `gust_sweep.py` and `oos_realism_cal.py`: the pinned corpus.
- `oos_pool.py` and `oos_realism.py`: July OOS.
- `extreme_signature.py`.
- `aug_hindcast_replay.py`: the August review-era table.
- The 2026-08-31 tri-model review's scripts (`rvclaude_*`, `rvgrok_stats_audit`, `rv2sci_a7`,
  `rv2sci_a2_null`, …), run unmodified except for their data paths.

The scripts live in `scratch_review/hindcast_stacked/`:

- `run_all.py` runs `replay_job.py`, the same loop as `hindcast_harness.py replay`.
- `before_after.py` recomputes each statistic with the original script's formula, including the
  upper-middle "median" (`sorted[n // 2]`) the July scripts used.
- `build_mirrors.py` rebuilds every record file in its original format for each state.
- `run_review_scripts.py` and `run_a2.py` re-run the review scripts on those mirrors.
- `registry.py` assembles §4.6.

Each number has three values:

- **documented**: recomputed from the committed result file the document was written from, so
  it matches the document's text.
- **before**: today's engine on the pre-fix corpora (a worktree at `0bbd430`).
- **after**: today's engine on the repaired corpora.

`wgresponse/` is byte-identical in both checkouts. **Every documented value is reproduced exactly
by "before"**: all 38 matrix runs match pair-for-pair, and every registry row matches. So there
is no engine drift, and **before → after is the fix and nothing else**.

The literal `hindcast_harness.py replay` was also re-run on every corpus. That rewrote each
corpus's `replay_results.json`:

| corpus | scored pairs / new burnable cells, before → after |
|---|---|
| pinned | 14 / 60,062 → 15 / 65,078 |
| July OOS | 18 / 109,040 → 26 / 134,157 |
| August | new file: 34 / 190,223 |
| live | still no incidents |

## 4. Before / after

### 4.1 Calibration matrices (`calibration_matrix_2026-09-03.md`, `-09-11.md`)

Before = documented. The full 12-column tables for the repaired corpora, in the matrices' own
format, are in `calibration_matrix_2026-09-27_corrected.md`.

**August**: pairs / cells / incidents 27 / 145,760 / 11 → 34 / 190,223 / 12 (every variant)

| variant | p10 adequacy | late miss | never reached | area p10 | skill | PIT extreme mass | 6–12 h bin | equal-incident mean |
|---|---|---|---|---|---|---|---|---|
| F_nofloor | 78.6 → **75.2** | 21.4 → **24.8** | 0.2 → 3.9 | 4.6× → 4.9× | 1.28 → 1.23 | 82.4 → 83.5 | 38.3 → 38.5 | 83.9 → 74.4 |
| H_nofloor_me050 | 88.1 → **86.2** | 11.9 → **13.8** | 0.4 → 2.3 | 6.2× → 6.9× | 1.25 → 1.21 | 65.0 → 65.4 | 45.7 → 45.9 | 90.2 → 82.4 |
| H_nofloor_me071 | 90.5 → **89.7** | 9.5 → **10.3** | 0.2 → 1.4 | 8.2× → 9.1× | 1.23 → 1.19 | 52.6 → 53.0 | 53.1 → 53.3 | 92.5 → 86.3 |
| A_deployed | 94.2 → **95.0** | 5.8 → **5.0** | 0.0 → 0.6 | 19.4× → 18.5× | 1.07 → 1.05 | 81.0 → 82.6 | 70.7 → 71.0 | 96.8 → 94.7 |
| B_gate | 94.2 → **95.0** | 5.8 → **5.0** | 0.0 → 0.6 | 19.4× → 18.5× | 1.07 → 1.05 | 80.9 → 82.5 | 70.7 → 71.0 | 96.7 → 94.7 |
| C_gate_grass | 93.6 → **91.6** | 6.4 → **8.4** | 0.0 → 2.6 | 10.7× → 11.5× | 1.08 → 1.06 | 78.9 → 81.7 | 68.7 → 68.9 | 93.8 → 86.4 |
| D_gate_me035 | 96.2 → **96.5** | 3.8 → **3.5** | 0.0 → 0.6 | 22.2× → 22.5× | 1.07 → 1.05 | 66.9 → 68.0 | 80.5 → 80.8 | 97.8 → 95.7 |
| D_gate_me050 | 97.2 → **97.3** | 2.8 → **2.7** | 0.0 → 0.6 | 22.7× → 23.4× | 1.07 → 1.05 | 59.2 → 60.3 | 85.4 → 85.7 | 98.3 → 96.1 |
| D_gate_me071 (deployed) | 98.8 → **98.5** | 1.2 → **1.5** | 0.0 → 0.6 | 24.7× → 25.8× | 1.06 → 1.05 | 50.1 → 50.5 | 93.8 → 94.0 | 99.2 → 96.9 |
| E_gategrass_me050 | 96.9 → **94.8** | 3.1 → **5.2** | 0.0 → 1.2 | 11.9× → 13.4× | 1.08 → 1.06 | 56.4 → 58.9 | 84.1 → 84.4 | 97.3 → 91.0 |
| E_gategrass_me071 | 98.7 → **96.9** | 1.3 → **3.1** | 0.0 → 0.9 | 15.3× → 16.0× | 1.07 → 1.06 | 46.8 → 48.7 | 93.3 → 93.5 | 98.8 → 93.8 |
| I_gategrass05_me050 | 91.9 → **90.7** | 8.1 → **9.3** | 0.0 → 1.4 | 9.2× → 10.5× | 1.09 → 1.08 | 52.5 → 54.6 | 58.8 → 59.1 | 93.7 → 87.2 |
| I_gategrass05_me071 | 94.1 → **93.2** | 5.9 → **6.8** | 0.0 → 1.2 | 11.0× → 12.1× | 1.09 → 1.07 | 39.8 → 42.1 | 69.3 → 69.6 | 95.5 → 90.3 |
| G_gate_p50 | 94.2 → **95.0** | 5.8 → **5.0** | 0.0 → 0.6 | 19.4× → 18.5× | 1.07 → 1.05 | 80.9 → 82.5 | 70.7 → 71.0 | 96.7 → 94.7 |
| J_regime_I071 | 95.9 → **94.5** | 4.1 → **5.5** | 0.0 → 1.2 | 14.2× → 14.4× | 1.07 → 1.05 | 47.5 → 49.3 | 79.0 → 79.1 | 96.7 → 91.4 |
| L_regime_I_noband | 91.1 → **89.4** | 8.9 → **10.6** | 0.0 → 2.9 | 7.0× → 7.6× | 1.07 → 1.05 | 79.7 → 82.2 | 60.2 → 60.4 | 91.4 → 83.9 |
| M_D071_piecewise | 98.8 → **98.5** | 1.2 → **1.5** | 0.0 → 0.6 | 24.6× → 25.7× | 1.06 → 1.05 | 50.1 → 50.7 | 93.5 → 93.7 | 99.2 → 96.9 |
| N_D071_phi | 97.7 → **97.6** | 2.3 → **2.4** | 0.0 → 0.6 | 23.7× → 25.0× | 1.06 → 1.05 | 50.5 → 50.5 | 87.9 → 87.9 | 98.5 → 96.3 |
| O_D071_both | 97.6 → **97.6** | 2.4 → **2.4** | 0.0 → 0.6 | 23.6× → 25.0× | 1.06 → 1.05 | 50.3 → 50.6 | 87.7 → 87.8 | 98.5 → 96.3 |

**July OOS**: pairs / cells / incidents 18 / 109,040 / 7 → 26 / 134,157 / 9 (every variant)

| variant | p10 adequacy | late miss | never reached | area p10 | skill | PIT extreme mass | 6–12 h bin | equal-incident mean |
|---|---|---|---|---|---|---|---|---|
| F_nofloor | 55.7 → **70.7** | 44.3 → **29.3** | 17.7 → 0.9 | 5.4× → 6.8× | 0.97 → 1.09 | 88.3 → 83.9 | 48.6 → 52.2 | 66.2 → 76.7 |
| H_nofloor_me050 | 60.5 → **80.3** | 39.5 → **19.7** | 16.9 → 0.1 | 7.3× → 10.4× | 0.97 → 1.10 | 75.0 → 63.5 | 53.6 → 59.2 | 69.2 → 85.7 |
| H_nofloor_me071 | 63.3 → **85.7** | 36.7 → **14.3** | 15.5 → 0.1 | 8.3× → 11.6× | 0.98 → 1.11 | 66.6 → 51.2 | 57.9 → 63.6 | 71.4 → 90.2 |
| A_deployed | 76.9 → **97.1** | 23.1 → **2.9** | 3.9 → 0.0 | 12.4× → 16.5× | 1.00 → 1.03 | 91.9 → 84.4 | 88.1 → 89.7 | 88.7 → 98.2 |
| B_gate | 76.9 → **97.1** | 23.1 → **2.9** | 4.1 → 0.0 | 12.2× → 16.5× | 1.00 → 1.03 | 91.9 → 84.3 | 88.1 → 89.7 | 88.7 → 98.2 |
| C_gate_grass | 68.2 → **84.5** | 31.8 → **15.5** | 4.2 → 0.0 | 9.5× → 10.0× | 0.95 → 1.03 | 90.9 → 85.7 | 63.4 → 68.4 | 74.1 → 91.4 |
| D_gate_me035 | 79.7 → **98.8** | 20.3 → **1.2** | 3.6 → 0.0 | 12.8× → 17.5× | 1.00 → 1.04 | 85.4 → 75.4 | 95.3 → 95.9 | 90.4 → 99.3 |
| D_gate_me050 | 81.5 → **99.4** | 18.5 → **0.6** | 0.4 → 0.0 | 13.6× → 18.1× | 1.00 → 1.04 | 80.2 → 67.3 | 97.6 → 98.0 | 91.4 → 99.6 |
| D_gate_me071 (deployed) | 83.1 → **99.7** | 16.9 → **0.3** | 0.0 → 0.0 | 14.2× → 19.1× | 0.99 → 1.04 | 71.6 → 56.4 | 98.7 → 98.9 | 92.2 → 99.8 |
| E_gategrass_me050 | 70.7 → **91.2** | 29.3 → **8.8** | 0.5 → 0.0 | 11.3× → 13.8× | 0.94 → 1.01 | 79.2 → 68.3 | 65.6 → 70.3 | 75.3 → 94.9 |
| E_gategrass_me071 | 73.7 → **92.6** | 26.3 → **7.4** | 0.0 → 0.0 | 12.1× → 15.3× | 0.95 → 1.01 | 72.0 → 56.9 | 70.9 → 74.8 | 77.6 → 95.6 |
| I_gategrass05_me050 | 66.5 → **88.0** | 33.5 → **12.0** | 9.4 → 0.0 | 8.8× → 11.8× | 0.95 → 1.07 | 74.9 → 61.8 | 59.8 → 65.3 | 72.8 → 93.1 |
| I_gategrass05_me071 | 68.0 → **90.4** | 32.0 → **9.6** | 8.3 → 0.0 | 10.4× → 13.4× | 0.94 → 1.02 | 66.5 → 50.0 | 62.4 → 67.6 | 74.2 → 94.5 |
| G_gate_p50 | 76.9 → **97.1** | 23.1 → **2.9** | 4.1 → 0.0 | 12.2× → 16.5× | 1.00 → 1.03 | 91.9 → 84.3 | 88.1 → 89.7 | 88.7 → 98.2 |
| J_regime_I071 | 71.1 → **90.4** | 28.9 → **9.6** | 0.0 → 0.0 | 10.4× → 14.9× | 0.93 → 1.02 | 67.0 → 50.8 | 62.4 → 67.6 | 75.6 → 94.5 |
| L_regime_I_noband | 65.7 → **79.9** | 34.3 → **20.1** | 4.2 → 0.0 | 6.9× → 8.2× | 0.93 → 1.06 | 89.5 → 84.0 | 56.0 → 62.0 | 72.2 → 88.6 |
| M_D071_piecewise | 83.3 → **99.7** | 16.7 → **0.3** | 0.0 → 0.0 | 14.2× → 19.0× | 1.00 → 1.04 | 71.4 → 56.1 | 98.8 → 98.9 | 92.2 → 99.8 |
| N_D071_phi | 82.8 → **99.3** | 17.2 → **0.7** | 0.1 → 0.0 | 14.1× → 18.6× | 1.00 → 1.04 | 72.3 → 57.0 | 97.1 → 97.5 | 91.9 → 99.5 |
| O_D071_both | 82.6 → **99.2** | 17.4 → **0.8** | 0.2 → 0.0 | 14.1× → 18.6× | 1.00 → 1.04 | 72.2 → 56.7 | 96.9 → 97.3 | 91.8 → 99.5 |

What moves: **August barely changes.** Every variant stays within 3.4 pp, and the variant
ordering holds, with the half-floor and grass-only variants losing 1–2 pp. **July changes
throughout.** Eight previously NON-MONOTONE pairs become scoreable, and the Porcupine pair
shrinks to its real size. Every floored variant gains 14–22 pp. Skill rises from ≈ 1.0 to
1.01–1.11. The D-family still leads grass-only and half-floor by 7–15 pp on July (D071 99.7 vs I071
90.4 vs C 84.5), so **the deployed choice (D071) is unaffected**. What holds I back on July is no
longer Porcupine. It is Aspen Acres 06-29 +7.4 h (86.9 % late under I) and Cottonwood 06-24 +8.2 h
(34.3 %). J's regime detector arms 2 of 26 July pairs, versus 2/18 documented.

**One new August signal.** Never-reached rises from 0.0 % to 0.6 % for every floored variant, the
dangerous polarity. Almost all of it is one newly scoreable pair, Bald Mountain 07-27 06:46 +25.6
h (NON-MONOTONE before the fix). There, 1,175 of 3,263 new cells (36 %) are never reached by any
member, even under D071. Its T1 (07-28 08:25) was first published 26.3 h after its stamp, so part
of that growth may post-date the stamp, which is the residual timing ambiguity the
first-publication rule narrows but cannot remove. The unfloored variants also pick up Little Giant
07-22 +56.9 h (4,467 cells never reached under F; none under D071).

### 4.2 Pinned corpus (Gifford / Dillon / Madre / Butler)

| number | documented = before | after |
|---|---|---|
| scored pairs / new burnable cells (default replay) | 14 / 60,062 | 15 / 65,078 |
| p10 adequacy / late / p50 in window (blend 0) | 68.8 / 31.2 / 64.7 % | 69.4 / 30.6 / 64.2 % |
| median head ratio p50 (published rule) | 1.32× | 1.11× |
| widened band (wind 0.50 / dir 45) | 69.8 % (+1.0 pp) | 71.2 % (+1.8 pp) |
| late by fuel class (GR / GS / SH / TL / TU) | 52.6 / 46.8 / 20.1 / 54.0 / 30.0 % | 44.6 / 42.4 / 21.0 / 54.0 / 30.0 % |
| gust sweep 0 / 0.25 / 0.5 / 0.75 / 1.0: coverage | 68.8 / 71.7 / 73.5 / 74.9 / 76.3 % | 69.4 / 72.9 / 74.7 / 75.8 / 77.3 % |
| gust sweep median head | 1.32 / 1.62 / 1.77 / 1.89 / 2.03× | 1.11 / 1.34 / 1.45 / 1.54 / 1.60× |
| calibration-set realism: blend 0.5 / + floor / + floor + spotting | 73.5 / 91.5 / 98.8 % (1.77 / 2.78 / 4.16×) | 74.7 / 92.1 / 98.9 % (1.45 / 2.72 / 3.98×) |
| Madre 07-02 +17.8 h: new growth / p10 adequacy | 35,071 ac / 69.9 % (the 8,396-ac T0 rasterized to 0 cells) | 27,023 ac / 60.3 % |
| Madre 07-03 16:16 +31.4 h | NON-MONOTONE, unscored (T1 was 4 stacked versions) | scored: 18,869 ac, 87.1 % |

### 4.3 July OOS corpus (`hindcast_oos_2026-07-28.md` and its citers)

| number | documented = before | after |
|---|---|---|
| strict pool (t0 ≥ 07-13): windows / cells / fires | 8 / 60,249 / 4 | 13 / 71,261 / 6 (adds BISCAR, Crosswhite, Kaiser 07-16, Brewer 07-20 and 07-21) |
| blend 0 / 0.25 / 0.5 / 0.75 / 1.0: coverage | 43.2 / 46.1 / 48.6 / 50.4 / 52.3 % | 72.9 / 80.7 / 85.8 / 89.0 / 90.7 % |
| "normal regime" (all but Porcupine), blend 0.5 | 7 windows / 26,361 cells / 89.1 % | 12 / 65,918 / 89.1 % |
| Porcupine Ridge 07-17 +12.9 h | 33,888 cells; 22.5 km head, 74k ac; p50 head 1.7 km (0.08×); 82.8 % late; 56.2 % of the pool | 5,343 cells; 3.7 km, 11,139 ac; p50 head 1.9 km (0.52×); 54.7 % late; 7.5 % of the pool |
| Porcupine late: widened band / + floor / + floor + spotting | 81.7 / 62.6 / 61.4 % | 52.0 / 0.0 / 0.0 % |
| late by fuel, blend 0.5 (GR / GS / SH / TL / TU) | 52.2 / 64.2 / 47.2 / 15.7 / 8.0 % | 20.8 / 12.6 / 10.4 / 16.5 / 10.0 % |
| realism on the strict pool: shipped / + floor / + floor + spotting | 48.6 / 64.8 / 65.5 % | 85.8 / 100.0 / 100.0 % |
| Kaiser Canyon's worst window, blend 0.5 → + floor | 69.1 → 99.9 % | 69.1 → 99.9 % (unchanged) |
| strict-pool windows excluded as "no spatial growth" | 6, all NON-MONOTONE: Crosswhite 07-16 and 07-24, Kaiser 07-16, Brewer 07-20 and 07-21, BISCAR 07-21 | 1: Crosswhite 07-16 |
| incidents with a scored window, pinned + OOS (evidence gate ≥ 10) | 11 | 13 (+ BISCAR, Crosswhite) |
| "42 windows / ~177k cells / 11 incidents" | 42 replayed (32 scored) / 177,047 / 11 | 42 replayed (41 scored) / 206,533 / 13 |

### 4.4 Extreme-regime signature (`extreme_signature.py`; the shipped condition GR+GS ≥ 0.60 and
max effective wind ≥ 4.0 m/s)

| number | documented = before | after |
|---|---|---|
| windows scored under the grass floor | 22 | 28 |
| windows the condition arms (grass %, eff wind, floored late) | Madre 07-02 (68.9, 8.55, 0.0), Madre 07-03 (68.9, 4.28, 15.4), Porcupine (88.2, 6.01, **62.6**) | Madre ×3 (0.0 / 15.4 / 0.0), BISCAR 07-21 (92.7, 7.76, 0.0), Porcupine (88.2, 6.01, **0.0**) |
| windows with floored late > 50 % ("n = 1") | 1 (Porcupine) | **0** |
| residual-late windows (floored late ≥ 15 %) | 4: Porcupine plus 3 first-capture pairs | 3, all first-capture pairs (Dillon 08-26 19.4 %, Gifford 08-01 25.8 %, Madre 07-03 15.4 %) |
| max floored late among < 60 %-grass windows (documented as "~0 %") | 25.8 % (Gifford 08-01, first capture) | 25.8 % (unchanged) |

### 4.5 August review-era table and the tri-model review's statistics

| number | documented = before | after |
|---|---|---|
| review-era table (t0 ≥ 08-01): pairs / cells / fires | 18 / 135,081 / 7 | 18 / 129,630 / 7 |
| calibration (blend 0.5) → as-deployed coverage | 77.2 → 93.8 % (+16.6 pp) | 74.1 → 93.6 % (+19.5 pp) |
| as-deployed late / median head (published rule → true median) | 6.2 % / 1.88× → 1.837× | 6.4 % / 1.79× → 1.769× |
| worst pair | Bug 10.2 km / 9.5 h, 0.69×, 54.8 % late | unchanged |
| as-deployed late by fuel (GR / GS / SH / TL / TU) | 3.7 / 5.3 / 13.5 / 3.0 / 0.2 % | 3.7 / 5.4 / 14.7 / 3.0 / 0.2 % |
| null-model area efficiency / p10 area budget | 1.85× / 5.79× | 1.88× / 5.94× |
| dt 6–12 h bin: pairs / cells / p10 adequacy | 5 / 27,089 / 69.8 % | 5 / 26,921 / 70.1 % |
| Bug's share of late cells / effective incidents (1/HHI) | 91.1 % / 3.24 | 92.4 % / 3.07 |
| PIT extreme mass: Aug as-deployed / Aug calibration / July blend 0.5 | 79.7 / 82.7 / 88.3 % | 77.5 / 81.9 / 83.9 % |
| model error sd(log head_P50/obs) / band under-dispersion / obs inside [P10, P50] | 0.710 / 8.0× / 1 of 18 | 0.698 / 7.9× / 1 of 18 |
| pooled area ratio p50, calibration → as-deployed | 2.33× → 6.06× | 2.37× → 6.38× |
| July + floor: windows / incidents / equal-incident coverage / effective incidents | 8 / 4 / 84.3 % / 2.41 | 13 / 6 / 100 % / 2.54 |
| pinned blend 0.5 median head (published → true) | 1.77× → 1.611× | 1.45× → 1.450× |

### 4.6 The full registry

Every documented number, once, with where it is cited. The doc:line references are at commit
`52d8b63`; the correction notes added in §6 shift later line numbers by a few lines. Generated by
`scratch_review/hindcast_stacked/registry.py` → `registry.md` / `registry.json`.

| # | number | documented | before (today's engine, pre-fix corpus) | after (repaired corpus) | cited at |
|---|---|---|---|---|---|
| 1 | Pinned corpus (Gifford/Dillon/Madre/Butler), default replay: scored pairs / new burnable cells <br>*hindcast_harness.py replay (blend 0)* | 14 / 60,062 | 14 / 60,062 | 15 / 65,078 | hindcast_first_pass.md:45; SPRINT2_PLAN.md:330,412; calibration_review_2026-07-31.md:98,143; MODEL_ASSESSMENT_HANDOFF.md:93-94 (14 / 60k); contracts.py:2310 (14-window) |
| 2 | Pinned, blend 0: p10 adequacy (necessary bound, = coverage possible) <br>*hindcast_harness.py replay* | 68.8% | 68.8% | 69.4% | hindcast_first_pass.md:46-47,92-93; SPRINT2_PLAN.md:413-414; ENGINE_REVIEW_2026-07-11.md:78; LIVE_OPS_PLAN.md:41; calibration_review:102,312,350; TRI_MODEL_REVIEW:49 |
| 3 | Pinned, blend 0: late miss <br>*hindcast_harness.py replay* | 31.2% | 31.2% | 30.6% | hindcast_first_pass.md:48,94; SPRINT2_PLAN.md:414; calibration_review:102 |
| 4 | Pinned, blend 0: p50 inside window <br>*hindcast_harness.py replay* | 64.7% | 64.7% | 64.2% | hindcast_first_pass.md:48; SPRINT2_PLAN.md:414 |
| 5 | Pinned, blend 0: median head ratio p50 (published upper-middle rule) <br>*hindcast_harness.py replay (07-10 report)* | 1.32x | 1.32x | 1.11x | hindcast_first_pass.md:50; calibration_review:102 |
| 6 | Pinned, blend 0: p10 adequacy with the widened band (wind 0.50 / dir 45) and its gain <br>*hindcast_harness.py replay (pairs_wide)* | 69.8% (+1.0 pp) | 69.8% (+1.0 pp) | 71.2% (+1.8 pp) | hindcast_first_pass.md:52,93; calibration_review:312,957; SPRINT2_PLAN.md:416 |
| 7 | Pinned, blend 0: late miss by fuel class (cells / late %) <br>*hindcast_harness.py replay* | GR 8,241/52.6 · GS 12,913/46.8 · SH 34,337/20.1 · TL 237/54.0 · TU 4,334/30.0 · NB 3,315 | GR 8,241/52.6 · GS 12,913/46.8 · SH 34,337/20.1 · TL 237/54.0 · TU 4,334/30.0 · NB 3,315 | GR 10,369/44.6 · GS 15,571/42.4 · SH 34,560/21.0 · TL 237/54.0 · TU 4,341/30.0 · NB 3,705 | hindcast_first_pass.md:62-69,95; calibration_review:180-189,204; SPRINT2_PLAN.md:420 |
| 8 | Pinned, blend 0: PIT histogram (rank 0 = engine late ... top = engine early) <br>*hindcast_harness.py replay* | [28706, 2050, 2145, 707, 1789, 2257, 22408] | [28706, 2050, 2145, 707, 1789, 2257, 22408] | [31695, 2728, 2642, 695, 2339, 2287, 22692] | hindcast_first_pass.md:51,95 (rank 0: 48 %, top: 37 %) |
| 9 | Pinned gust sweep, blend 0.0: coverage / late / median head (upper-middle) <br>*scratch_review/gust_sweep.py* | 68.8 / 31.2 / 1.32x | 68.8 / 31.2 / 1.32x | 69.4 / 30.6 / 1.11x | calibration_review:102 |
| 10 | Pinned gust sweep, blend 0.25: coverage / late / median head (upper-middle) <br>*scratch_review/gust_sweep.py* | 71.7 / 28.3 / 1.62x | 71.7 / 28.3 / 1.62x | 72.9 / 27.1 / 1.34x | calibration_review:103 |
| 11 | Pinned gust sweep, blend 0.5: coverage / late / median head (upper-middle) <br>*scratch_review/gust_sweep.py* | 73.5 / 26.5 / 1.77x | 73.5 / 26.5 / 1.77x | 74.7 / 25.3 / 1.45x | calibration_review:104; contracts.py:2310 (the blend-0.5 calibration) |
| 12 | Pinned gust sweep, blend 0.75: coverage / late / median head (upper-middle) <br>*scratch_review/gust_sweep.py* | 74.9 / 25.1 / 1.89x | 74.9 / 25.1 / 1.89x | 75.8 / 24.2 / 1.54x | calibration_review:105 |
| 13 | Pinned gust sweep, blend 1.0: coverage / late / median head (upper-middle) <br>*scratch_review/gust_sweep.py* | 76.3 / 23.7 / 2.03x | 76.3 / 23.7 / 2.03x | 77.3 / 22.7 / 1.60x | calibration_review:106; hindcast_oos_2026-07-28.md:40 (2.03x) |
| 14 | Calibration-set realism [blend0.5]: pairs / coverage / late / median head (upper-middle) <br>*scratch_review/oos_realism_cal.py* | 14 / 73.5 / 26.5 / 1.77x | 14 / 73.5 / 26.5 / 1.77x | 15 / 74.7 / 25.3 / 1.45x | hindcast_oos:108; calibration_review:104,145,269; MODEL_ASSESSMENT_HANDOFF:93; TRI_MODEL_REVIEW:42,49 |
| 15 | Calibration-set realism [grass_floor]: pairs / coverage / late / median head (upper-middle) <br>*scratch_review/oos_realism_cal.py* | 14 / 91.5 / 8.5 / 2.78x | 14 / 91.5 / 8.5 / 2.78x | 15 / 92.1 / 7.9 / 2.72x | hindcast_oos:109,127; calibration_review:146,157,269; MODEL_ASSESSMENT_HANDOFF:94 |
| 16 | Calibration-set realism [realism_pack]: pairs / coverage / late / median head (upper-middle) <br>*scratch_review/oos_realism_cal.py* | 14 / 98.8 / 1.2 / 4.16x | 14 / 98.8 / 1.2 / 4.16x | 15 / 98.9 / 1.1 / 3.98x | hindcast_oos:110,131; calibration_review:147,167,171,269 |
| 17 | July OOS strict pool (t0 >= 07-13, growth, weather span), blend 0.5: windows / cells / fires <br>*scratch_review/oos_pool.py* | 8 / 60,249 / 4 | 8 / 60,249 / 4 | 13 / 71,261 / 6 | hindcast_oos:19; calibration_review:108,143; MODEL_ASSESSMENT_HANDOFF:95; TRI_MODEL_REVIEW:44 |
| 18 | July OOS pool, blend 0.0: coverage / late / median head (upper-middle) <br>*scratch_review/oos_pool.py* | 43.2 / 56.8 / 0.99x | 43.2 / 56.8 / 0.99x | 72.9 / 27.1 / 0.99x | hindcast_oos:31; calibration_review:113 |
| 19 | July OOS pool, blend 0.25: coverage / late / median head (upper-middle) <br>*scratch_review/oos_pool.py* | 46.1 / 53.9 / 1.10x | 46.1 / 53.9 / 1.10x | 80.7 / 19.3 / 1.10x | hindcast_oos:32; calibration_review:114 |
| 20 | July OOS pool, blend 0.5: coverage / late / median head (upper-middle) <br>*scratch_review/oos_pool.py* | 48.6 / 51.4 / 1.26x | 48.6 / 51.4 / 1.26x | 85.8 / 14.2 / 1.26x | hindcast_oos:33; calibration_review:115; MODEL_ASSESSMENT_HANDOFF:95; calibration_review:145 |
| 21 | July OOS pool, blend 0.75: coverage / late / median head (upper-middle) <br>*scratch_review/oos_pool.py* | 50.4 / 49.6 / 1.40x | 50.4 / 49.6 / 1.40x | 89.0 / 11.0 / 1.40x | hindcast_oos:34; calibration_review:116 |
| 22 | July OOS pool, blend 1.0: coverage / late / median head (upper-middle) <br>*scratch_review/oos_pool.py* | 52.3 / 47.7 / 1.49x | 52.3 / 47.7 / 1.49x | 90.7 / 9.3 / 1.49x | hindcast_oos:35; calibration_review:117; calibration_review:122 (+3.7 pp vs 0.5) |
| 23 | July OOS, normal-regime subset (every pool window except Porcupine), blend 0.5: windows / cells / coverage <br>*scratch_review/oos_pool.py (per-pair)* | 7 / 26,361 / 89.1 | 7 / 26,361 / 89.1 | 12 / 65,918 / 89.1 | hindcast_oos:44; calibration_review:149; MODEL_ASSESSMENT_HANDOFF:97 |
| 24 | Porcupine Ridge 07-17 +12.9 h, blend 0.5: cells / observed head / p50 head / head ratio / observed new area / late <br>*replay_b0.5.json via oos_pool.py* | 33,888 cells / 22.5 km / 1.7 km / 0.08x / 73,951 ac / 82.8 % late | 33,888 cells / 22.5 km / 1.7 km / 0.08x / 73,951 ac / 82.8 % late | 5,343 cells / 3.7 km / 1.9 km / 0.52x / 11,139 ac / 54.7 % late | hindcast_oos:57-59; calibration_review:214-217; MODEL_ASSESSMENT_HANDOFF:100-101; README.md:400; rancho_santa_fe_plan.md:114,875; vail_aspen_response_program.md:906; contracts.py:2347; FIX_PROGRAM_2026-09-11:114 |
| 25 | Porcupine's share of the July OOS pool's cells (blend 0.5) <br>*scratch_review/oos_pool.py* | 56.2% | 56.2% | 7.5% | hindcast_oos:57; calibration_review:151,216 |
| 26 | Porcupine late miss under the widened band (wind 0.50 / dir 45), blend 0.5 <br>*replay_b0.5.json pairs_wide* | 81.7 % | 81.7 % | 52.0 % | hindcast_oos:61; calibration_review:216 |
| 27 | July OOS pool, blend 0.5: late miss by fuel class (cells / late %) <br>*per-fuel pooling of replay_b0.5.json* | GR 16,502/52.2 · GS 26,925/64.2 · SH 8,740/47.2 · TL 3,609/15.7 · TU 4,473/8.0 | GR 16,502/52.2 · GS 26,925/64.2 · SH 8,740/47.2 · TL 3,609/15.7 · TU 4,473/8.0 | GR 15,387/20.8 · GS 26,452/12.6 · SH 8,735/10.4 · TL 9,293/16.5 · TU 11,394/10.0 | hindcast_oos:66-72; calibration_review:191-199,204; rancho_santa_fe_plan.md:181,872; vail_aspen_response_program.md:263,861 |
| 28 | July OOS realism [shipped] (oos_realism.py GOOD set): pairs / coverage / late <br>*scratch_review/oos_realism.py* | 8 / 48.6 / 51.4 | 8 / 48.6 / 51.4 | 13 / 85.8 / 14.2 | hindcast_oos:91,108; calibration_review:145 |
| 29 | July OOS realism [grass_floor] (oos_realism.py GOOD set): pairs / coverage / late <br>*scratch_review/oos_realism.py* | 8 / 64.8 / 35.2 | 8 / 64.8 / 35.2 | 13 / 100.0 / 0.0 | hindcast_oos:92,127; calibration_review:146,167; MODEL_ASSESSMENT_HANDOFF:96; TRI_MODEL_REVIEW:40 (64.8 %) |
| 30 | July OOS realism [realism_pack] (oos_realism.py GOOD set): pairs / coverage / late <br>*scratch_review/oos_realism.py* | 8 / 65.5 / 34.5 | 8 / 65.5 / 34.5 | 13 / 100.0 / 0.0 | hindcast_oos:93; calibration_review:147,167 |
| 31 | Porcupine late miss: shipped / + floor / + floor + spotting <br>*scratch_review/oos_realism.py* | 82.8% / 62.6% / 61.4% | 82.8% / 62.6% / 61.4% | 54.7% / 0.0% / 0.0% | hindcast_oos:59,92-93; calibration_review:215 |
| 32 | Kaiser Canyon's worst pool window: coverage at blend 0.5 -> + floor <br>*scratch_review/oos_pool.py + oos_realism.py* | KaiserCanyon-WA 07-19 12:47: 69.1 -> 99.9 | KaiserCanyon-WA 07-19 12:47: 69.1 -> 99.9 | KaiserCanyon-WA 07-19 12:47: 69.1 -> 99.9 | hindcast_oos:95; calibration_review:151 |
| 33 | July OOS normal-regime subset: coverage + floor (every window except Porcupine) <br>*scratch_review/oos_realism.py* | 7 windows / 26,361 cells / 100.0 | 7 windows / 26,361 cells / 100.0 | 12 windows / 65,918 cells / 100.0 | hindcast_oos:96; calibration_review:150; MODEL_ASSESSMENT_HANDOFF:97 |
| 34 | July OOS: windows excluded from the strict pool (blend 0.5) and why <br>*scratch_review/oos_pool.py* | 19: Crosswhite-OR 07-16 16:25 +14.3h (no spatial growth (NON-MONOTONE pair: only 78% of the T0 interior is inside T1 )); Crosswhite-OR 07-24 09:04 +43.2h (no spatial growth (NON-MONOTONE pair: only 11% of the T0 interior is inside T1 )); AspenAcres-CO 06-29 15:19 +7.4h (pre-cutoff); AspenAcres-CO 06-29 22:43 +9.9h (pre-cutoff); AspenAcres-CO 06-30 08:40 +32.2h (pre-cutoff); AspenAcres-CO 07-01 16:53 +8.3h (pre-cutoff); Willow-CO 06-28 23:53 +17.5h (pre-cutoff); Willow-CO 06-29 17:22 +61.6h (pre-cutoff); Willow-CO 07-02 06:56 +20.8h (pre-cutoff); Willow-CO 07-03 03:45 +49.2h (pre-cutoff); Stookey-UT 07-11 17:25 +21.7h (pre-cutoff); Stookey-UT 07-12 15:09 +20.2h (pre-cutoff); Cottonwood-UT 06-23 02:15 +32.6h (pre-cutoff); Cottonwood-UT 06-24 10:51 +8.2h (pre-cutoff); Cottonwood-UT 06-24 19:05 +16.2h (pre-cutoff); KaiserCanyon-WA 07-16 20:44 +64.1h (no spatial growth (NON-MONOTONE pair: only 85% of the T0 interior is inside T1 )); Brewer-OR 07-20 04:22 +24.6h (no spatial growth (NON-MONOTONE pair: only 3% of the T0 interior is inside T1 ()); Brewer-OR 07-21 04:57 +45.5h (no spatial growth (NON-MONOTONE pair: only 47% of the T0 interior is inside T1 )); BISCAR 07-21 18:36 +8.7h (no spatial growth (NON-MONOTONE pair: only 0% of the T0 interior is inside T1 ()) | 19: Crosswhite-OR 07-16 16:25 +14.3h (no spatial growth (NON-MONOTONE pair: only 78% of the T0 interior is inside T1 )); Crosswhite-OR 07-24 09:04 +43.2h (no spatial growth (NON-MONOTONE pair: only 11% of the T0 interior is inside T1 )); AspenAcres-CO 06-29 15:19 +7.4h (pre-cutoff); AspenAcres-CO 06-29 22:43 +9.9h (pre-cutoff); AspenAcres-CO 06-30 08:40 +32.2h (pre-cutoff); AspenAcres-CO 07-01 16:53 +8.3h (pre-cutoff); Willow-CO 06-28 23:53 +17.5h (pre-cutoff); Willow-CO 06-29 17:22 +61.6h (pre-cutoff); Willow-CO 07-02 06:56 +20.8h (pre-cutoff); Willow-CO 07-03 03:45 +49.2h (pre-cutoff); Stookey-UT 07-11 17:25 +21.7h (pre-cutoff); Stookey-UT 07-12 15:09 +20.2h (pre-cutoff); Cottonwood-UT 06-23 02:15 +32.6h (pre-cutoff); Cottonwood-UT 06-24 10:51 +8.2h (pre-cutoff); Cottonwood-UT 06-24 19:05 +16.2h (pre-cutoff); KaiserCanyon-WA 07-16 20:44 +64.1h (no spatial growth (NON-MONOTONE pair: only 85% of the T0 interior is inside T1 )); Brewer-OR 07-20 04:22 +24.6h (no spatial growth (NON-MONOTONE pair: only 3% of the T0 interior is inside T1 ()); Brewer-OR 07-21 04:57 +45.5h (no spatial growth (NON-MONOTONE pair: only 47% of the T0 interior is inside T1 )); BISCAR 07-21 18:36 +8.7h (no spatial growth (NON-MONOTONE pair: only 0% of the T0 interior is inside T1 ()) | 14: Crosswhite-OR 07-16 16:25 +14.3h (no spatial growth (NON-MONOTONE pair: only 83% of the T0 interior is inside T1 )); AspenAcres-CO 06-29 15:19 +7.4h (pre-cutoff); AspenAcres-CO 06-29 22:43 +9.9h (pre-cutoff); AspenAcres-CO 06-30 08:40 +32.2h (pre-cutoff); AspenAcres-CO 07-01 16:53 +8.3h (pre-cutoff); Willow-CO 06-28 23:53 +17.5h (pre-cutoff); Willow-CO 06-29 17:22 +61.6h (pre-cutoff); Willow-CO 07-02 06:56 +20.8h (pre-cutoff); Willow-CO 07-03 03:45 +49.2h (pre-cutoff); Stookey-UT 07-11 17:25 +21.7h (pre-cutoff); Stookey-UT 07-12 15:09 +20.2h (pre-cutoff); Cottonwood-UT 06-23 02:15 +32.6h (pre-cutoff); Cottonwood-UT 06-24 10:51 +8.2h (pre-cutoff); Cottonwood-UT 06-24 19:05 +16.2h (pre-cutoff) | hindcast_oos:20-25 (Crosswhite 'no spatial growth', BISCAR 'perimeter did not grow', ELEPHANT dt) |
| 35 | Incidents with >= 1 scored growth window, pinned + July OOS (the 10-incident evidence gate) <br>*harness replays (blend 0.5)* | 11: AspenAcres-CO, Brewer-OR, Butler, Cottonwood-UT, Dillon, Gifford, KaiserCanyon-WA, Madre, PorcupineRidge-OR, Stookey-UT, Willow-CO | 11: AspenAcres-CO, Brewer-OR, Butler, Cottonwood-UT, Dillon, Gifford, KaiserCanyon-WA, Madre, PorcupineRidge-OR, Stookey-UT, Willow-CO | 13: AspenAcres-CO, BISCAR, Brewer-OR, Butler, Cottonwood-UT, Crosswhite-OR, Dillon, Gifford, KaiserCanyon-WA, Madre, PorcupineRidge-OR, Stookey-UT, Willow-CO | hindcast_oos:79-82; calibration_review:154 |
| 36 | Pinned + July OOS replay windows / new cells / incidents ('42 windows / ~177k cells / 11 incidents') <br>*harness replays (pinned blend 0 + OOS blend 0.5)* | 42 replayed (32 scored) / 177,047 new cells / 11 incidents with a scored window | 42 replayed (32 scored) / 177,047 new cells / 11 incidents with a scored window | 42 replayed (41 scored) / 206,533 new cells / 13 incidents with a scored window | calibration_review:37-38,354-355,907 |
| 37 | Extreme-regime signature: windows scored under the grass floor (cal + strict OOS) <br>*scratch_review/extreme_signature.py* | 22 | 22 | 28 | hindcast_oos:143; calibration_review:237,251,254; rancho_santa_fe_plan.md:172,206,975 |
| 38 | Windows the shipped regime condition arms (GR+GS >= 0.60 AND max eff wind >= 4.0 m/s): window (grass %, eff wind, floored late %) <br>*scratch_review/extreme_signature.py* | Madre 07-02 15:51 (68.9, 8.55, 0.0); Madre 07-03 09:38 (68.9, 4.28, 15.4); PorcupineRidge-OR 07-17 04:53 (88.2, 6.01, 62.6) | Madre 07-02 15:51 (68.9, 8.55, 0.0); Madre 07-03 09:38 (68.9, 4.28, 15.4); PorcupineRidge-OR 07-17 04:53 (88.2, 6.01, 62.6) | Madre 07-02 15:51 (68.9, 8.55, 0.0); Madre 07-03 09:38 (68.9, 4.28, 15.4); Madre 07-03 16:16 (68.9, 9.14, 0.0); BISCAR 07-21 18:36 (92.7, 7.76, 0.0); PorcupineRidge-OR 07-17 04:53 (88.2, 6.01, 0.0) | hindcast_oos:154-157; calibration_review:248-251; README.md:401; contracts.py:2350-2352 |
| 39 | Windows with floored late miss > 50 % <br>*scratch_review/extreme_signature.py* | PorcupineRidge-OR 07-17 04:53 (62.6) | PorcupineRidge-OR 07-17 04:53 (62.6) | none | calibration_review:251-255 ('exactly one: Porcupine', 'n = 1'); TRI_MODEL_REVIEW:124; FIX_PROGRAM_2026-09-03:214; MODEL_ASSESSMENT_HANDOFF:162; LIVE_WORLD_PLAN:1302; rancho_santa_fe_plan.md:206 |
| 40 | Max floored late miss among windows under 60 % grass-class ('~0 %') <br>*scratch_review/extreme_signature.py* | 25.8 % (of 19 windows) | 25.8 % (of 19 windows) | 25.8 % (of 23 windows) | hindcast_oos:149-150; calibration_review:245-246; README.md:401; contracts.py:2351 |
| 41 | August review-era table (t0 >= 08-01): pairs / cells / fires <br>*scratch_review/aug_hindcast_replay.py* | 18 / 135,081 / 7 | 18 / 135,081 / 7 | 18 / 129,630 / 7 | MODEL_ASSESSMENT_HANDOFF:98; TRI_MODEL_REVIEW:40 (7 fires); SCORECARD.md:34 (18 / 135k) |
| 42 | August review-era [calibration]: coverage / late / median head (upper-middle) <br>*scratch_review/aug_hindcast_replay.py* | 77.2 / 22.8 / 1.10x | 77.2 / 22.8 / 1.10x | 74.1 / 25.9 / 0.89x | TRI_MODEL_REVIEW:45 (77.2 -> 93.8 = +16.6 pp) |
| 43 | August review-era [as-deployed]: coverage / late / median head (upper-middle) <br>*scratch_review/aug_hindcast_replay.py* | 93.8 / 6.2 / 1.88x | 93.8 / 6.2 / 1.88x | 93.6 / 6.4 / 1.79x | MODEL_ASSESSMENT_HANDOFF:98; TRI_MODEL_REVIEW:40,42,45; SCORECARD.md:33 |
| 44 | August review-era worst pair (as-deployed) <br>*scratch_review/aug_hindcast_replay.py* | Bug-CA 10.2 km / 9.5 h at 0.69x (54.8 % late) | Bug-CA 10.2 km / 9.5 h at 0.69x (54.8 % late) | Bug-CA 10.2 km / 9.5 h at 0.69x (54.8 % late) | MODEL_ASSESSMENT_HANDOFF:102-103 |
| 45 | August review-era per-fuel late miss (as-deployed), % <br>*scratch_review/aug_hindcast_replay.py* | GR 3.7 / GS 5.3 / SH 13.5 / TL 3.0 / TU 0.2 | GR 3.7 / GS 5.3 / SH 13.5 / TL 3.0 / TU 0.2 | GR 3.7 / GS 5.4 / SH 14.7 / TL 3.0 / TU 0.2 | MODEL_ASSESSMENT_HANDOFF:104-105 |
| 46 | Null-model area efficiency, Aug as-deployed: area a physics-free isotropic buffer needs to match the engine's p10 coverage / the engine's own p10 area ('1.85x') <br>*scratch_review/rv2sci_a2_null.py (run_a2.py)* | 1.85x | 1.85x | 1.88x | TRI_MODEL_REVIEW:24-25,40 |
| 47 | p10 area budget (engine p10 new cells / new burnable cells), Aug as-deployed <br>*scratch_review/rv2sci_a2_null.py eng_area_x* | 5.79x | 5.79x | 5.94x | TRI_MODEL_REVIEW:40 (5.8x) |
| 48 | Aug as-deployed, dt 6-12 h: pairs / cells / p10 adequacy <br>*rvgrok_stats_audit.py window_duration_bins* | 5 / 27,089 / 69.8 | 5 / 27,089 / 69.8 | 5 / 26,921 / 70.1 | TRI_MODEL_REVIEW:40 (69.8 %); SCORECARD.md:33 ('one 9.5 h pair') |
| 49 | Bug-CA's share of all late-miss cells, Aug as-deployed <br>*rvclaude_aug_deep.py* | 91.1% | 91.1% | 92.4% | TRI_MODEL_REVIEW:40 (91 %); SCORECARD.md:33 |
| 50 | Effective incidents (1 / HHI of cell shares by incident), Aug as-deployed <br>*rv2stats_derive (HHI by incident)* | 3.24 | 3.24 | 3.07 | TRI_MODEL_REVIEW:40,44 (~3; 3.244); SCORECARD.md:34 (3-4) |
| 51 | PIT extreme-bin mass: Aug as-deployed / Aug calibration / July OOS blend 0.5 (pool) <br>*rv2sci_a7_disp.pool_pit* | 79.7% / 82.7% / 88.3% | 79.7% / 82.7% / 88.3% | 77.5% / 81.9% / 83.9% | TRI_MODEL_REVIEW:41 (76-95 %; 79.7 / 82.7 / 88.3; July = every scored July pair at blend 0.5) |
| 52 | Median head ratio, published upper-middle -> true median: Aug as-deployed; pinned blend 0.5 (cal_realism) <br>*rvclaude_aug_deep.py / rv2code_r8_r10_followup.py* | 1.88 -> 1.837; 1.77 -> 1.611 | 1.88 -> 1.837; 1.77 -> 1.611 | 1.79 -> 1.769; 1.45 -> 1.450 | TRI_MODEL_REVIEW:42 (1.88 -> 1.837; 1.77 -> 1.61) |
| 53 | Aug as-deployed: sd(log head_P50/obs) / under-dispersion factor / obs head inside [P10, P50] / median head_P10/head_P50 <br>*rvclaude_dispersion.py* | 0.710 / 8.0x / 1/18 / 1.093 | 0.710 / 8.0x / 1/18 / 1.093 | 0.698 / 7.9x / 1/18 / 1.093 | TRI_MODEL_REVIEW:43,45 (0.710 / 8x / 1/18 / 1.09), 114-115; README.md:378,423; FIX_PROGRAM_2026-09-03:58-59; FIRE_BELIEF.md:19-20,101,313; contracts.py:2210-2211; LIVE_WORLD_PLAN:1312 (the 0.71 prior) |
| 54 | Pooled area ratio p50 (modeled / observed new area): Aug calibration -> as-deployed <br>*rvclaude_final_table.py* | 2.33x -> 6.06x | 2.33x -> 6.06x | 2.37x -> 6.38x | TRI_MODEL_REVIEW:45 (2.3x -> 6.1x) |
| 55 | July OOS [+ floor]: windows / incidents / equal-incident coverage / effective incidents <br>*rv2stats_derive over oos_realism.json* | 8 / 4 / 84.3 / 2.41 | 8 / 4 / 84.3 / 2.41 | 13 / 6 / 100.0 / 2.54 | TRI_MODEL_REVIEW:44 (8 windows = 4 incidents, 84.3 %; 2.414) |
| 56 | J_regime_I071: scored pairs the extreme-regime detector arms (August; July) <br>*calmatrix2_run.sh -> calmatrix/{aug,oos}_J_regime_I071.json (pair notes)* | aug 13/27 (armed among scored: 8); oos 2/18 (armed among scored: 1) | aug 13/27 (armed among scored: 8); oos 2/18 (armed among scored: 1) | aug 13/34 (armed among scored: 12); oos 2/26 (armed among scored: 2) | FIX_PROGRAM_2026-09-11:102,106-108 (Aug 13/27, July 2/18); MODEL_ASSESSMENT_HANDOFF:66 |
| 57 | July matrix corpus: every scored pair with > 6 % late under D071 or I071 (cells / late D071 / late I071) <br>*calmatrix/oos_{D_gate_me071,I_gategrass05_me071}.json* | PorcupineRidge-OR 07-17 04:53 +12.9h 33,888 / 53.1 / 62.8; AspenAcres-CO 06-29 15:19 +7.4h 11,003 / 3.8 / 86.9; Cottonwood-UT 06-24 10:51 +8.2h 7,886 / 0.0 / 34.3; Willow-CO 07-02 06:56 +20.8h 1,231 / 0.0 / 62.6 | PorcupineRidge-OR 07-17 04:53 +12.9h 33,888 / 53.1 / 62.8; AspenAcres-CO 06-29 15:19 +7.4h 11,003 / 3.8 / 86.9; Cottonwood-UT 06-24 10:51 +8.2h 7,886 / 0.0 / 34.3; Willow-CO 07-02 06:56 +20.8h 1,231 / 0.0 / 62.6 | AspenAcres-CO 06-29 15:19 +7.4h 11,003 / 3.8 / 86.9; Cottonwood-UT 06-24 10:51 +8.2h 7,886 / 0.0 / 34.3 | FIX_PROGRAM_2026-09-11:114-121 (Porcupine 33,888 / 53.1 / 62.8; AspenAcres 11,003 / 3.8 / 86.9; Willow 1,231 / 0 / 62.6; Cottonwood 7,886 / 0 / 34.3; others 0 / <= 6) |
| 58 | Matrix August F_nofloor: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 78.6 · 21.4 · 4.65x · 1.28 · 82.4 (53/29) · 38.3 · 83.9 | 27/145,760/11 · 78.6 · 21.4 · 4.65x · 1.28 · 82.4 (53/29) · 38.3 · 83.9 | 34/190,223/12 · 75.2 · 24.8 · 4.91x · 1.23 · 83.5 (55/29) · 38.5 · 74.4 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:172-174 |
| 59 | Matrix August A_deployed: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 81.0 (12/69) · 70.7 · 96.8 | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 81.0 (12/69) · 70.7 · 96.8 | 34/190,223/12 · 95.0 · 5.0 · 18.51x · 1.05 · 82.6 (14/68) · 71.0 · 94.7 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:162,167-172,184,204-209; FIX_PROGRAM_2026-09-11:139-141 |
| 60 | Matrix August B_gate: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 80.9 (12/68) · 70.7 · 96.7 | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 80.9 (12/68) · 70.7 · 96.7 | 34/190,223/12 · 95.0 · 5.0 · 18.51x · 1.05 · 82.5 (14/68) · 71.0 · 94.7 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:167-169,185 |
| 61 | Matrix August C_gate_grass: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 93.6 · 6.4 · 10.71x · 1.08 · 78.9 (15/64) · 68.7 · 93.8 | 27/145,760/11 · 93.6 · 6.4 · 10.71x · 1.08 · 78.9 (15/64) · 68.7 · 93.8 | 34/190,223/12 · 91.6 · 8.4 · 11.46x · 1.06 · 81.7 (20/62) · 68.9 · 86.4 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:170-171,186,195-196 |
| 62 | Matrix August D_gate_me035: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 96.2 · 3.8 · 22.23x · 1.07 · 66.9 (10/57) · 80.5 · 97.8 | 27/145,760/11 · 96.2 · 3.8 · 22.23x · 1.07 · 66.9 (10/57) · 80.5 · 97.8 | 34/190,223/12 · 96.5 · 3.5 · 22.46x · 1.05 · 68.0 (10/58) · 80.8 · 95.7 | calibration_matrix_2026-09-03.md / -09-11.md (aug) |
| 63 | Matrix August D_gate_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 97.2 · 2.8 · 22.69x · 1.07 · 59.2 (8/51) · 85.4 · 98.3 | 27/145,760/11 · 97.2 · 2.8 · 22.69x · 1.07 · 59.2 (8/51) · 85.4 · 98.3 | 34/190,223/12 · 97.3 · 2.7 · 23.38x · 1.05 · 60.3 (9/51) · 85.7 · 96.1 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:174-176,191 |
| 64 | Matrix August D_gate_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 98.8 · 1.2 · 24.70x · 1.06 · 50.1 (6/44) · 93.8 · 99.2 | 27/145,760/11 · 98.8 · 1.2 · 24.70x · 1.06 · 50.1 (6/44) · 93.8 · 99.2 | 34/190,223/12 · 98.5 · 1.5 · 25.80x · 1.05 · 50.5 (7/44) · 94.0 · 96.9 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:174-176,190,200-209; FIX_PROGRAM_2026-09-11:100,134,145 |
| 65 | Matrix August E_gategrass_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 96.9 · 3.1 · 11.89x · 1.08 · 56.4 (10/47) · 84.1 · 97.3 | 27/145,760/11 · 96.9 · 3.1 · 11.89x · 1.08 · 56.4 (10/47) · 84.1 · 97.3 | 34/190,223/12 · 94.8 · 5.2 · 13.35x · 1.06 · 58.9 (13/46) · 84.4 · 91.0 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:187,195-196 |
| 66 | Matrix August E_gategrass_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 98.7 · 1.3 · 15.30x · 1.07 · 46.8 (7/40) · 93.3 · 98.8 | 27/145,760/11 · 98.7 · 1.3 · 15.30x · 1.07 · 46.8 (7/40) · 93.3 · 98.8 | 34/190,223/12 · 96.9 · 3.1 · 15.96x · 1.06 · 48.7 (10/38) · 93.5 · 93.8 | calibration_matrix_2026-09-03.md / -09-11.md (aug) |
| 67 | Matrix August G_gate_p50: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 80.9 (12/68) · 70.7 · 96.7 | 27/145,760/11 · 94.2 · 5.8 · 19.38x · 1.07 · 80.9 (12/68) · 70.7 · 96.7 | 34/190,223/12 · 95.0 · 5.0 · 18.51x · 1.05 · 82.5 (14/68) · 71.0 · 94.7 | calibration_matrix_2026-09-03.md / -09-11.md (aug) |
| 68 | Matrix August H_nofloor_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 88.1 · 11.9 · 6.23x · 1.25 · 65.0 (43/22) · 45.7 · 90.2 | 27/145,760/11 · 88.1 · 11.9 · 6.23x · 1.25 · 65.0 (43/22) · 45.7 · 90.2 | 34/190,223/12 · 86.2 · 13.8 · 6.92x · 1.21 · 65.4 (44/21) · 45.9 · 82.4 | calibration_matrix_2026-09-03.md / -09-11.md (aug) |
| 69 | Matrix August H_nofloor_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 90.5 · 9.5 · 8.23x · 1.23 · 52.6 (34/18) · 53.1 · 92.5 | 27/145,760/11 · 90.5 · 9.5 · 8.23x · 1.23 · 52.6 (34/18) · 53.1 · 92.5 | 34/190,223/12 · 89.7 · 10.3 · 9.09x · 1.19 · 53.0 (35/18) · 53.3 · 86.3 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:189 |
| 70 | Matrix August I_gategrass05_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 91.9 · 8.1 · 9.18x · 1.09 · 52.5 (21/32) · 58.8 · 93.7 | 27/145,760/11 · 91.9 · 8.1 · 9.18x · 1.09 · 52.5 (21/32) · 58.8 · 93.7 | 34/190,223/12 · 90.7 · 9.3 · 10.51x · 1.08 · 54.6 (24/30) · 59.1 · 87.2 | calibration_matrix_2026-09-03.md / -09-11.md (aug) |
| 71 | Matrix August I_gategrass05_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 94.1 · 5.9 · 10.98x · 1.09 · 39.8 (14/26) · 69.3 · 95.5 | 27/145,760/11 · 94.1 · 5.9 · 10.98x · 1.09 · 39.8 (14/26) · 69.3 · 95.5 | 34/190,223/12 · 93.2 · 6.8 · 12.08x · 1.07 · 42.1 (17/25) · 69.6 · 90.3 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-03:188,195-196,211-216; FIX_PROGRAM_2026-09-11:101; contracts.py:2336-2338 |
| 72 | Matrix August J_regime_I071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 95.9 · 4.1 · 14.19x · 1.07 · 47.5 (10/38) · 79.0 · 96.7 | 27/145,760/11 · 95.9 · 4.1 · 14.19x · 1.07 · 47.5 (10/38) · 79.0 · 96.7 | 34/190,223/12 · 94.5 · 5.5 · 14.37x · 1.05 · 49.3 (12/37) · 79.1 · 91.4 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-11:102,106-108 (regime pairs Aug 13/27, July 2/18); MODEL_ASSESSMENT_HANDOFF:66 |
| 73 | Matrix August L_regime_I_noband: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 91.1 · 8.9 · 6.96x · 1.07 · 79.7 (18/62) · 60.2 · 91.4 | 27/145,760/11 · 91.1 · 8.9 · 6.96x · 1.07 · 79.7 (18/62) · 60.2 · 91.4 | 34/190,223/12 · 89.4 · 10.6 · 7.57x · 1.05 · 82.2 (22/60) · 60.4 · 83.9 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-11:103 |
| 74 | Matrix August M_D071_piecewise: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 98.8 · 1.2 · 24.57x · 1.06 · 50.1 (6/44) · 93.5 · 99.2 | 27/145,760/11 · 98.8 · 1.2 · 24.57x · 1.06 · 50.1 (6/44) · 93.5 · 99.2 | 34/190,223/12 · 98.5 · 1.5 · 25.74x · 1.05 · 50.7 (7/44) · 93.7 · 96.9 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-11:136,147-148 |
| 75 | Matrix August N_D071_phi: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 97.7 · 2.3 · 23.73x · 1.06 · 50.5 (7/43) · 87.9 · 98.5 | 27/145,760/11 · 97.7 · 2.3 · 23.73x · 1.06 · 50.5 (7/43) · 87.9 · 98.5 | 34/190,223/12 · 97.6 · 2.4 · 25.03x · 1.05 · 50.5 (7/43) · 87.9 · 96.3 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-11:135,139-141; LIVE_WORLD_PLAN:830-831 (27 / 145,760 / 97.7 / 2.3, August) |
| 76 | Matrix August O_D071_both: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 27/145,760/11 · 97.6 · 2.4 · 23.62x · 1.06 · 50.3 (7/43) · 87.7 · 98.5 | 27/145,760/11 · 97.6 · 2.4 · 23.62x · 1.06 · 50.3 (7/43) · 87.7 · 98.5 | 34/190,223/12 · 97.6 · 2.4 · 24.98x · 1.05 · 50.6 (8/43) · 87.8 · 96.3 | calibration_matrix_2026-09-03.md / -09-11.md (aug); FIX_PROGRAM_2026-09-11:137 |
| 77 | Matrix July OOS F_nofloor: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 55.7 · 44.3 · 5.38x · 0.97 · 88.3 (56/32) · 48.6 · 66.2 | 18/109,040/7 · 55.7 · 44.3 · 5.38x · 0.97 · 88.3 (56/32) · 48.6 · 66.2 | 26/134,157/9 · 70.7 · 29.3 · 6.79x · 1.09 · 83.9 (49/35) · 52.2 · 76.7 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:172-174 |
| 78 | Matrix July OOS A_deployed: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 76.9 · 23.1 · 12.43x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 18/109,040/7 · 76.9 · 23.1 · 12.43x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 26/134,157/9 · 97.1 · 2.9 · 16.49x · 1.03 · 84.4 (9/75) · 89.7 · 98.2 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:162,167-172,184,204-209; FIX_PROGRAM_2026-09-11:139-141 |
| 79 | Matrix July OOS B_gate: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 76.9 · 23.1 · 12.21x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 18/109,040/7 · 76.9 · 23.1 · 12.21x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 26/134,157/9 · 97.1 · 2.9 · 16.49x · 1.03 · 84.3 (9/75) · 89.7 · 98.2 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:167-169,185 |
| 80 | Matrix July OOS C_gate_grass: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 68.2 · 31.8 · 9.48x · 0.95 · 90.9 (41/50) · 63.4 · 74.1 | 18/109,040/7 · 68.2 · 31.8 · 9.48x · 0.95 · 90.9 (41/50) · 63.4 · 74.1 | 26/134,157/9 · 84.5 · 15.5 · 9.98x · 1.03 · 85.7 (28/57) · 68.4 · 91.4 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:170-171,186,195-196 |
| 81 | Matrix July OOS D_gate_me035: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 79.7 · 20.3 · 12.83x · 1.00 · 85.4 (28/57) · 95.3 · 90.4 | 18/109,040/7 · 79.7 · 20.3 · 12.83x · 1.00 · 85.4 (28/57) · 95.3 · 90.4 | 26/134,157/9 · 98.8 · 1.2 · 17.54x · 1.04 · 75.4 (7/69) · 95.9 · 99.3 | calibration_matrix_2026-09-03.md / -09-11.md (oos) |
| 82 | Matrix July OOS D_gate_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 81.5 · 18.5 · 13.57x · 1.00 · 80.2 (26/54) · 97.6 · 91.4 | 18/109,040/7 · 81.5 · 18.5 · 13.57x · 1.00 · 80.2 (26/54) · 97.6 · 91.4 | 26/134,157/9 · 99.4 · 0.6 · 18.13x · 1.04 · 67.3 (5/63) · 98.0 · 99.6 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:174-176,191 |
| 83 | Matrix July OOS D_gate_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 83.1 · 16.9 · 14.19x · 0.99 · 71.6 (22/50) · 98.7 · 92.2 | 18/109,040/7 · 83.1 · 16.9 · 14.19x · 0.99 · 71.6 (22/50) · 98.7 · 92.2 | 26/134,157/9 · 99.7 · 0.3 · 19.06x · 1.04 · 56.4 (2/55) · 98.9 · 99.8 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:174-176,190,200-209; FIX_PROGRAM_2026-09-11:100,134,145 |
| 84 | Matrix July OOS E_gategrass_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 70.7 · 29.3 · 11.33x · 0.94 · 79.2 (37/43) · 65.6 · 75.3 | 18/109,040/7 · 70.7 · 29.3 · 11.33x · 0.94 · 79.2 (37/43) · 65.6 · 75.3 | 26/134,157/9 · 91.2 · 8.8 · 13.82x · 1.01 · 68.3 (21/48) · 70.3 · 94.9 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:187,195-196 |
| 85 | Matrix July OOS E_gategrass_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 73.7 · 26.3 · 12.15x · 0.95 · 72.0 (35/37) · 70.9 · 77.6 | 18/109,040/7 · 73.7 · 26.3 · 12.15x · 0.95 · 72.0 (35/37) · 70.9 · 77.6 | 26/134,157/9 · 92.6 · 7.4 · 15.35x · 1.01 · 56.9 (16/41) · 74.8 · 95.6 | calibration_matrix_2026-09-03.md / -09-11.md (oos) |
| 86 | Matrix July OOS G_gate_p50: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 76.9 · 23.1 · 12.21x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 18/109,040/7 · 76.9 · 23.1 · 12.21x · 1.00 · 91.9 (32/60) · 88.1 · 88.7 | 26/134,157/9 · 97.1 · 2.9 · 16.49x · 1.03 · 84.3 (9/75) · 89.7 · 98.2 | calibration_matrix_2026-09-03.md / -09-11.md (oos) |
| 87 | Matrix July OOS H_nofloor_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 60.5 · 39.5 · 7.33x · 0.97 · 75.0 (50/25) · 53.6 · 69.2 | 18/109,040/7 · 60.5 · 39.5 · 7.33x · 0.97 · 75.0 (50/25) · 53.6 · 69.2 | 26/134,157/9 · 80.3 · 19.7 · 10.44x · 1.10 · 63.5 (38/26) · 59.2 · 85.7 | calibration_matrix_2026-09-03.md / -09-11.md (oos) |
| 88 | Matrix July OOS H_nofloor_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 63.3 · 36.7 · 8.30x · 0.98 · 66.6 (46/21) · 57.9 · 71.4 | 18/109,040/7 · 63.3 · 36.7 · 8.30x · 0.98 · 66.6 (46/21) · 57.9 · 71.4 | 26/134,157/9 · 85.7 · 14.3 · 11.65x · 1.11 · 51.2 (30/21) · 63.6 · 90.2 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:189 |
| 89 | Matrix July OOS I_gategrass05_me050: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 66.5 · 33.5 · 8.80x · 0.95 · 74.9 (42/33) · 59.8 · 72.8 | 18/109,040/7 · 66.5 · 33.5 · 8.80x · 0.95 · 74.9 (42/33) · 59.8 · 72.8 | 26/134,157/9 · 88.0 · 12.0 · 11.77x · 1.07 · 61.8 (28/34) · 65.3 · 93.1 | calibration_matrix_2026-09-03.md / -09-11.md (oos) |
| 90 | Matrix July OOS I_gategrass05_me071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 68.0 · 32.0 · 10.40x · 0.94 · 66.5 (39/28) · 62.4 · 74.2 | 18/109,040/7 · 68.0 · 32.0 · 10.40x · 0.94 · 66.5 (39/28) · 62.4 · 74.2 | 26/134,157/9 · 90.4 · 9.6 · 13.39x · 1.02 · 50.0 (22/28) · 67.6 · 94.5 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-03:188,195-196,211-216; FIX_PROGRAM_2026-09-11:101; contracts.py:2336-2338 |
| 91 | Matrix July OOS J_regime_I071: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 71.1 · 28.9 · 10.40x · 0.93 · 67.0 (37/30) · 62.4 · 75.6 | 18/109,040/7 · 71.1 · 28.9 · 10.40x · 0.93 · 67.0 (37/30) · 62.4 · 75.6 | 26/134,157/9 · 90.4 · 9.6 · 14.86x · 1.02 · 50.8 (21/30) · 67.6 · 94.5 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-11:102,106-108 (regime pairs Aug 13/27, July 2/18); MODEL_ASSESSMENT_HANDOFF:66 |
| 92 | Matrix July OOS L_regime_I_noband: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 65.7 · 34.3 · 6.91x · 0.93 · 89.5 (46/44) · 56.0 · 72.2 | 18/109,040/7 · 65.7 · 34.3 · 6.91x · 0.93 · 89.5 (46/44) · 56.0 · 72.2 | 26/134,157/9 · 79.9 · 20.1 · 8.21x · 1.06 · 84.0 (37/47) · 62.0 · 88.6 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-11:103 |
| 93 | Matrix July OOS M_D071_piecewise: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 83.3 · 16.7 · 14.17x · 1.00 · 71.4 (22/49) · 98.8 · 92.2 | 18/109,040/7 · 83.3 · 16.7 · 14.17x · 1.00 · 71.4 (22/49) · 98.8 · 92.2 | 26/134,157/9 · 99.7 · 0.3 · 19.04x · 1.04 · 56.1 (2/54) · 98.9 · 99.8 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-11:136,147-148 |
| 94 | Matrix July OOS N_D071_phi: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 82.8 · 17.2 · 14.07x · 1.00 · 72.3 (23/49) · 97.1 · 91.9 | 18/109,040/7 · 82.8 · 17.2 · 14.07x · 1.00 · 72.3 (23/49) · 97.1 · 91.9 | 26/134,157/9 · 99.3 · 0.7 · 18.61x · 1.04 · 57.0 (3/54) · 97.5 · 99.5 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-11:135,139-141; LIVE_WORLD_PLAN:830-831 (27 / 145,760 / 97.7 / 2.3, August) |
| 95 | Matrix July OOS O_D071_both: pairs/cells/incidents · p10 adequacy · late · area p10 · skill · PIT extreme (early/late) · 6-12 h bin · equal-incident <br>*calmatrix_run.sh / calmatrix2_run.sh -> calmatrix_report.py* | 18/109,040/7 · 82.6 · 17.4 · 14.07x · 1.00 · 72.2 (23/49) · 96.9 · 91.8 | 18/109,040/7 · 82.6 · 17.4 · 14.07x · 1.00 · 72.2 (23/49) · 96.9 · 91.8 | 26/134,157/9 · 99.2 · 0.8 · 18.64x · 1.04 · 56.7 (3/54) · 97.3 · 99.5 | calibration_matrix_2026-09-03.md / -09-11.md (oos); FIX_PROGRAM_2026-09-11:137 |

## 5. What the corrections change in the documented conclusions

- **"Blow-ups are not a ROS-tuning problem" / "Porcupine is the gap" / "beyond every variant".**
  These rested on one artifact. The corrected Porcupine pair is a moderate under-forecast at
  blend 0.5 (0.52×, 54.7 % late) that the grass floor covers fully. The "two regimes" split of the
  July OOS pool no longer exists. Affected: `hindcast_oos_2026-07-28.md` §"two regimes" and the
  addendum; `calibration_review_2026-07-31.md` §1.6–1.7 and :219–223, :908–910;
  `FIX_PROGRAM_2026-09-11.md` :106–125; `MODEL_ASSESSMENT_HANDOFF.md` :100–102; `README.md`
  :400–401; `rancho_santa_fe_plan.md` :114, :177, :875–878; `vail_aspen_response_program.md`
  :906; `wgresponse/contracts.py` :2347–2352.
- **The extreme-regime detector's evidence (n = 1) is now n = 0.** "Fuel separates the blow-ups
  cleanly" and "weather-based detection is falsified" have no positive example left. Of the
  five windows the detector arms, the floor already covers four fully; the fifth is the Madre
  07-03 first-capture pair (15.4 %). See §7.
- **July OOS headline.** 48.6 % → 85.8 % at blend 0.5 and 64.8 % → 100 % with the floor. The
  July evidence for the grass floor gets stronger, not weaker.
- **Fuel ordering on July** ("timber solved; cured grass/shrub is the entire remaining gap").
  At blend 0.5 it flattens: GR 20.8, GS 12.6, SH 10.4, TL 16.5, TU 10.0 %. The pinned corpus still
  shows grass high (44.6 / 42.4 %), but TL (54.0 %, 237 cells) is highest.
- **"July skill ≈ 1.0 for every variant; coverage is area budget"** (`FIX_PROGRAM_2026-09-03`
  :196–199, `contracts.py` :2337–2338). Corrected July skill is 1.01–1.11: modest physics skill.
- **Exclusions** "Crosswhite: no spatial growth" and "BISCAR: the mapped perimeter did not grow"
  were artifacts; both are scored now. 16 of 20 NON-MONOTONE flags in the corpora were made by the
  bug. One Bald Mountain pair (08-01) remains genuinely non-monotone, so the tri-model's "guards
  work" (B6) still has an example, but most earlier flags were not garbage in the source.
- **A hard pair became visible.** Bald Mountain 07-27 +25.6 h: 36 % of its new cells are never
  reached under the deployed D071 (§4.1). It is the corrected corpora's largest never-reached
  concentration, and worth a look before the next calibration round.
- **Unchanged in substance:** the deployed D071 choice; the measured model error (0.710 → 0.698);
  the August headline (93.8 → 93.6 %) and its concentration caveats (effective incidents 3.24 →
  3.07); "widening the band is not the lever" (+1.0 → +1.8 pp); the gust-blend curve's shape.

## 6. Documents that cite the old numbers

The ~167 citations were inventoried doc by doc in 15 documents; the four primary calibration
documents were added to that list. Each document below received a dated correction note that
points here, directly under its title, with two exceptions: README's note sits above the Sprint 2
knob table that carries the citations, and the belief-vs-physics study got a status line in its
§8 item 1. No number in any of them was edited.

| document | citations | where |
|---|---|---|
| `docs/calibration/hindcast_first_pass.md` | primary (pinned first pass) | :27–52, :62–69, :92–96; the Madre rows :35–37 |
| `docs/calibration/hindcast_oos_2026-07-28.md` | primary (July OOS) | :8, :19–25, :31–35, :44–73, :79–110, :143–160 |
| `docs/calibration/calibration_matrix_2026-09-03.md` | primary (matrix) | every row |
| `docs/calibration/calibration_matrix_2026-09-11.md` | primary (matrix) | every row |
| `docs/calibration/SCORECARD.md` | 6 | :33–34 (the August headline correction, 9.5 h pair, effective incidents) |
| `docs/calibration_review_2026-07-31.md` | 47 | :37–38, :93–255 (§1.2–1.7), :269–271, :312–314, :350–355, :907–910, :957–958 |
| `docs/TRI_MODEL_REVIEW_2026-08-31.md` | 21 | :24–25, :27–28, :39–50, :87, :114–115, :124 |
| `docs/FIX_PROGRAM_2026-09-03.md` | 25 | :40–41, :58–59, :66–67, :155–216 |
| `docs/FIX_PROGRAM_2026-09-11.md` | 17 | :98–151 |
| `docs/MODEL_ASSESSMENT_HANDOFF.md` | 13 | :66, :93–105, :161–163, :175–176 |
| `docs/rancho_santa_fe_plan.md` | 9 | :114–115, :172–183, :203–207, :872–878, :975 |
| `docs/SPRINT2_PLAN.md` | 6 | :330, :412–425 |
| `README.md` | 5 | :378, :395–401, :423 |
| `docs/vail_aspen_response_program.md` | 3 | :262–264, :860–862, :906 |
| `docs/FIRE_BELIEF.md` | 3 | :19–20, :101, :313 (the 0.71 model error, now measured 0.698) |
| `docs/LIVE_WORLD_PLAN.md` | 2 | :830–831 (August N-variant guard A/B 97.7 / 2.3 % → 97.6 / 2.4 %), :1302 |
| `docs/ENGINE_REVIEW_2026-07-11.md` | 1 | :78–79 |
| `docs/LIVE_OPS_PLAN.md` | 1 | :41 |
| `docs/review/belief_vs_physics_2026-09-27.md` | the finding itself | §8 item 1 (status note: fixed) |
| **`wgresponse/contracts.py`** | **8, not edited** | :2210–2211, :2305–2311, :2321–2322, :2336–2338, :2347–2352 |

`wgresponse/contracts.py` is the law and was out of bounds for this ticket, so its docstrings still
quote the old figures:

- `:2347` "Porcupine Ridge 22.5 km/12.9 h = 13x under-forecast"; corrected 3.7 km, 0.52×.
- `:2350–2352` "Porcupine 88%, Madre grass run 69%; every non-grass-led window scored ~0% late";
  the grass fractions stand, but the blow-up is gone, and the max under 60 % grass is 25.8 %.
- `:2336–2338` "held back ONLY by the July blow-ups … skill ~1.0"; corrected July skill is
  1.01–1.11, and the July gap is Aspen Acres and Cottonwood.
- `:2210–2211` "sd(log) 0.71"; corrected 0.698.
- `:2310` "14-window hindcast"; corrected 15 windows.

An owner edit of those comments is the remaining step. It changes no behaviour.

Checked and found to contain no in-scope number: `docs/SIMULATOR_DESIGN.md`,
`docs/OPEN_QUESTIONS.md`, `docs/calibration/hindcast_live_summit.md` (the empty live corpus),
`docs/calibration/leadtime_4h_experiment_2026-09-11.md` and
`docs/calibration/vail_aspen_grid_2026-09-23.md` (simulator runs).

## 7. Owner decisions this raises (nothing was changed)

1. **The extreme-regime posture** (`--extreme-regime` on the live watcher; contracts thresholds
   0.60 / 4.0 m/s) now has no measured positive example. It errs conservative: the ladder
   evaluates on P10 when armed. Options: keep it as a precaution, retire it, or re-derive it when
   a real blow-up enters a corpus. The corpora contain none after the correction.
2. **The half-floor / grass-only variants** (the I family, "held back only by the July blow-ups")
   now trail D071 on July by 7–15 pp on non-blow-up pairs. The recommendation to re-cut the
   floor toward I has lost its stated reason.
3. **Re-select the corpora with the fixed picker** when they are next extended. The first
   publication's acreage changes the capture selection for 7 incident-corpora (§2).
4. **Owner edit of `contracts.py`'s hindcast comments** (§6).

## 8. Limits

- Capture times were kept, so the corrected corpora are the old pairs with correct geometry, not
  what a fresh fetch under the fixed picker would select (§2).
- Weather stays at the pre-fix centroid (0–7.5 km off).
- Pinned domains keep the old tiles; each still covers every ring with at least the 4 km pad.
- The Daily layer's semantics were inferred from its rows (source-record ids, edit dates, ring
  overlap), not from a published specification.
- Out-of-order rows are left in (§1).
- Numbers from the tri-model scripts were re-derived by re-running the scripts on mirrored inputs.
  Where a script needs the corpus itself (`rv2sci_a2_null.py`), it ran in a per-state sandbox.

## 9. Reproduce

```
py -3 -m pytest tests/test_hindcast_snapshot_rule.py tests/test_hindcast.py
py -3 tools/hindcast_harness.py repair --data-dir <corpus>        # network; already applied
py -3 scratch_review/hindcast_stacked/run_all.py --state both      # needs ../hindcast-before at
0bbd430 with the LFPS caches
py -3 scratch_review/hindcast_stacked/before_after.py
py -3 scratch_review/hindcast_stacked/build_mirrors.py
py -3 scratch_review/hindcast_stacked/run_review_scripts.py
py -3 scratch_review/hindcast_stacked/run_a2.py
py -3 scratch_review/hindcast_stacked/registry.py
py -3 scratch_review/hindcast_stacked/rule_evidence.py
py -3 scratch_review/hindcast_stacked/corrected_matrix.py
```
