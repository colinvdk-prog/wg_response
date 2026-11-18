# Crew-program review fixes — measurements (2026-11-17)

Before = main e43c24e (a `git archive` snapshot); after = this branch at commit 5b156b3 as it ships
(46a, 47a, 47b and the two display knobs armed; 46b built, not armed); the `arm46b_*` runs are
commit 5063f74 with 46b armed too. Two worker processes per set.

Harnesses (this folder):

- `chain_before.sh` / `chain_after.sh` — the sets below, in order.
- `matrix.py` — lane A's harness (copied from `crew_program_review_2026-11-11/A_safety/`; ROOT is
  this tree; `--set main | valley24 | more24`; `--cfg JSON`). Page payload: hold_h 12,
  hold_per_home, proximity_first, crews_3h 0, spotting, grass realism.
- `scratch_review/eagle_valley/run_matrix.py` — the October valley harness (33 fires), run with
  `--cfg '{"dispatch_hold_h":12.0,"dispatch_hold_per_home":true,"dispatch_proximity_rank":true}'`
  (the page's coating window and proximity first, as the 2026-10-27 review ran it), 12 h at 07:13
  and at 13:13 (`--start-hour 13.22`), and 24 h.
- `summarize_fix.py` — the tables. `goldens.py` — the knob-off goldens of
  `tests/test_crew_waiting.py`. `diag_bc.py` (Beaver Creek's refuge candidates at the 28a
  decision), `diag_campfire.py` (the extended Paradise run), `check_display.py` (display-only
  proof), `diag_release.py`, `run_arm.py` (the 46b arms).
- `runs/` — every `runs.jsonl` (per-tick logs under `ticks/` are not committed).

## What ships (the simulator's arming)

| Knob | SEMANTICS | Armed by the simulator |
|---|---|---|
| `escape_wait_watch` | 46a — a waiting crew leaves before its refuge is a dead end | yes |
| `refuge_keeps_exit` | 46b — a refuge keeps a way out | **no** (`simui.SIM_ARMS_REFUGE_KEEPS_EXIT = False`) |
| `crew_overrun_worst_row` | 47a — the audit keeps the deeper row | yes |
| `replan_on_release` | 47b — a stop released en route re-plans at once | yes |
| `outcome_lost_capacity` | display only | yes |
| `unserved_reason_refresh` | display only | yes |

All six default OFF in `ResponseConfig` (byte-identical): the knob-off goldens of
`tests/test_crew_waiting.py` (Vail 24 h, Beaver Creek 24 h, Park City side extreme; computed on
e43c24e by `goldens.py`, `runs/goldens_main.json`) reproduce on the branch with the knobs off
(`runs/goldens_branch_off.json`), and every legacy golden test passes with the six knobs added to
its knob-off dict.

## Summary (before = e43c24e, after = the branch as it ships)

| Set | Homes coated | Reached homes uncoated | Rows | Deepest row (min past the line) | Rows > 30 min | Crews off the map | Relocations |
|---|---|---|---|---|---|---|---|
| Valley, 33 fires, 12 h, 07:13 | 907 → 907 | 13 → 13 | 3 → 3 | 24.4 → 24.4 | 0 → 0 | 0 → 0 | 14 → 15 |
| Valley, 33 fires, 12 h, 13:13 | 815 → 815 | 14 → 14 | 2 → 2 | 24.4 → 24.4 | 0 → 0 | 0 → 0 | 5 → 6 |
| Valley, 33 fires, 24 h, 07:13 | 977 → 977 | 27 → 27 | 17 → 4 | 823.4 → 682.5 | 14 → 1 | 17 → 4 | 39 → 49 |
| Lane A's 32 non-valley fires | 1,066 → 1,066 | — | 2 → 2 | (no depth) → 145.0 | 0 → 2 | 4 → 4 | 17 → 18 |
| 24-h fires, six more maps (8 runs) | 247 → 247 | — | 4 → 1 | 305.4 → 0.9 | 3 → 0 | 7 → 4 | 13 → 16 |
| Lane A's 24-h valley pair | 74 → 74 | — | 3 → 1 | 682.5 → 682.5 | 3 → 1 | 2 → 0 | 4 → 4 |
| Extended Paradise (test_crew_pocket) | 20 → 20 | — | 2 → 0 | 909.1 → — | 2 → 0 | 0 → 0 | — |
| Paradise crown-fire repro (test_dest_durable) | 24 → 24 | — | 0 → 0 | — | 0 → 0 | 0 → 0 | — |

The lane-A "rows > 30 min" 0 → 2 are Park City's side fire at 12 h and 24 h: the same two rows as
before, which carried no depth (47a now gives them theirs: 145.0 min, 607 s in the band). No new
departure violation, withdrawal or row anywhere. The four 24-h valley rows left: Beaver Creek's
canonical extreme fire (682.5 min — the known defect), its northwest high and extreme fires at
T+0.5 h (20.9 / 24.4 min: the pocket exit of SEMANTICS 28d, as in October) and Eagle-Vail's
canonical high fire (0.5 min). The four crews off the map left are Beaver Creek's (canonical high,
pair, late ×2), unchanged.

## The 46b arm (all six knobs armed), the same sets — not shipped

`runs/tables_arm46b.md`: 07:13 906 homes, 15 reached uncoated, 1 crew off the map (Beaver Creek's
canonical extreme fire 34 → 31, 1 → 4 — the Eagle-Vail pocket's last homes); 13:13 820, 14; lane A
1,065, rows 2, off the map 4 → 3; the six maps 247, rows 1, off the map 3; the pair 71 homes, no
row, one crew off the map (Beaver Creek: 34 → 31, its row gone). The valley 24-h set was stopped
after one run. It was not armed because of `tests/test_dest_durable.py`'s Paradise crown-fire repro
(`diag_crown.py`): 28a sent sim-g3 at T+8.5 h to a refuge that kept a way out, 46a moved it on at
T+18 h to one that did not, and at T+27 h the sweep drove it through a band 1,019 min past the line
(46b off: no row in 180 ticks; served 28 with 46b, 24 without).

Earlier 46b shapes measured and dropped: in every voluntary refuge law (28b and 46a too) it walked
the extended Paradise run's sim-g2 from refuge to refuge into a dead end, 1,473 min past the line
(`diag_campfire.py`); requiring the way out to stay open until the crew would have to leave
(`simui.REFUGE_EXIT_UNTIL_LEAVE`, the `armA_*` runs: 19 of the 33 valley fires and the pair) cost
Beaver Creek's canonical high fire 6 of 37 homes in 12 h and changed no row; the shipped arrival
window kept those 37 (`armB_*`).

## Beaver Creek 24 h: why the waiting rule cannot reach it

`diag_bc.py` at sim-g1's 28a decision (T+5.68 h, the crew leaving its last stop): every zone's and
every reachable road refuge's exit term (SEMANTICS 38 (ii)/(iii)) had closed 0.9-4.8 h earlier —
23 candidate road refuges, none with a lawful way on. The pocket formed while the crew worked; only
the planner (38 b's stop refuge) can avoid it, which is what 46b does.

## Display only (`check_display.py`)

Malibu left and Park City right (lane B's worlds), 16 h, the two display knobs on vs off: every
`dispatch.plan` call returned the same plan (93 / 164 calls), and every tick's state minus the
outcome tags and unserved reasons hashed the same. Park City: 9 lost homes all `lost_capacity`;
run report 6 reached-uncoated, 6 lost to capacity; 150 home-ticks of `capacity` /
`infeasible_deadline` now `fire_arrived`. Malibu: 1 lost home, `abandoned_unsavable`.

## Tests

- Full suite on the final code (commit 5b156b3 code, `runs/pytest_full_3.txt`): 2,915 passed, 15
  skipped, 1 xfailed (the Beaver Creek known defect, strict), 4 failed — all four in
  `tests/test_simui.py`, from the armed display knobs (the outcome vocabulary gained
  `lost_capacity`; a burned home's reason reads `fire_arrived`): fixed in the tests (vocabulary
  `OUTCOME_TAGS_SPLIT`; the reason refresh pinned off where the planner's own reason is the
  subject) and re-run (`runs/pytest_rerun.txt`).
- New: `tests/test_crew_waiting.py` (knobs; 46b's test on painted fields; 46a on a painted world;
  47a's merge; 47b and the reason refresh; the Vail, Beaver Creek and Park City worlds against the
  e43c24e goldens and armed; the display-only proof; the page). `tests/test_crew_pocket.py`: the
  extended Paradise xfail is now a passing armed test, with the iteration-5 twin pinning the old
  rows.

## Per-run tables

### the valley's 33 fires, 12 h, 07:13 start — before -> after (33 runs)

| | before | after |
|---|---|---|
| runs | 33 | 33 |
| coated | 907 | 907 |
| reached | 125 | 125 |
| in_time | 112 | 112 |
| uncoated | 13 | 13 |
| rows | 3 | 3 |
| transit | 3 | 3 |
| deepest | -24.4 | -24.4 |
| over30 | 0 | 0 |
| band_s | 30.2 | 30.2 |
| off_map | 0 | 0 |
| reloc | 14 | 15 |
| km | 6103.3 | 6107.3 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| median_closest_h | 1.36 | 1.36 |

| map | coated before -> after | reached uncoated | rows | off map |
|---|---|---|---|---|
| beaver_creek_co | 368 -> 368 | 8 -> 8 | 2 -> 2 | 0 -> 0 |
| eagle_vail_co | 151 -> 151 | 1 -> 1 | 1 -> 1 | 0 -> 0 |
| vail_co | 388 -> 388 | 4 -> 4 | 0 -> 0 | 0 -> 0 |

| run | coated | uncoated | rows (deepest) | off map | reloc |
|---|---|---|---|---|---|
| beaver_creek_co|northwest|extreme | 28 -> 28 | 2 -> 2 | 1 (-24.42) -> 1 (-24.42) | 0 -> 0 | 3 -> 4 |

### the valley's 33 fires, 12 h, 13:13 start — before -> after (33 runs)

| | before | after |
|---|---|---|
| runs | 33 | 33 |
| coated | 815 | 815 |
| reached | 104 | 104 |
| in_time | 90 | 90 |
| uncoated | 14 | 14 |
| rows | 2 | 2 |
| transit | 2 | 2 |
| deepest | -24.4 | -24.4 |
| over30 | 0 | 0 |
| band_s | 2.0 | 2.0 |
| off_map | 0 | 0 |
| reloc | 5 | 6 |
| km | 5572.3 | 5578.7 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| median_closest_h | 1.36 | 1.36 |

| map | coated before -> after | reached uncoated | rows | off map |
|---|---|---|---|---|
| beaver_creek_co | 335 -> 335 | 10 -> 10 | 2 -> 2 | 0 -> 0 |
| eagle_vail_co | 137 -> 137 | 1 -> 1 | 0 -> 0 | 0 -> 0 |
| vail_co | 343 -> 343 | 3 -> 3 | 0 -> 0 | 0 -> 0 |

| run | coated | uncoated | rows (deepest) | off map | reloc |
|---|---|---|---|---|---|
| beaver_creek_co|south|extreme | 34 -> 34 | 0 -> 0 | 0 (None) -> 0 (None) | 0 -> 0 | 0 -> 1 |

### the valley's 33 fires, 24 h, 07:13 start — before -> after (33 runs)

| | before | after |
|---|---|---|
| runs | 33 | 33 |
| coated | 977 | 977 |
| reached | 244 | 244 |
| in_time | 217 | 217 |
| uncoated | 27 | 27 |
| rows | 17 | 4 |
| transit | 17 | 4 |
| deepest | -823.4 | -682.5 |
| over30 | 14 | 1 |
| band_s | 1824.2 | 288.9 |
| off_map | 17 | 4 |
| reloc | 39 | 49 |
| km | 7190.3 | 7207.0 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| median_closest_h | 1.36 | 1.36 |

| map | coated before -> after | reached uncoated | rows | off map |
|---|---|---|---|---|
| beaver_creek_co | 393 -> 393 | 16 -> 16 | 5 -> 3 | 4 -> 4 |
| eagle_vail_co | 176 -> 176 | 2 -> 2 | 1 -> 1 | 0 -> 0 |
| vail_co | 408 -> 408 | 9 -> 9 | 11 -> 0 | 13 -> 0 |

| run | coated | uncoated | rows (deepest) | off map | reloc |
|---|---|---|---|---|---|
| beaver_creek_co|northwest|extreme | 28 -> 28 | 5 -> 5 | 3 (-823.43) -> 1 (-24.42) | 0 -> 0 | 5 -> 5 |
| beaver_creek_co|northwest|moderate | 38 -> 38 | 0 -> 0 | 0 (None) -> 0 (None) | 0 -> 0 | 0 -> 5 |
| beaver_creek_co|south|moderate | 40 -> 40 | 0 -> 0 | 0 (None) -> 0 (None) | 0 -> 0 | 0 -> 1 |
| vail_co|canonical|extreme | 40 -> 40 | 0 -> 0 | 2 (-339.91) -> 0 (None) | 2 -> 0 | 2 -> 2 |
| vail_co|canonical|high | 37 -> 37 | 1 -> 1 | 3 (-305.41) -> 0 (None) | 3 -> 0 | 3 -> 3 |
| vail_co|late|high | 38 -> 38 | 1 -> 1 | 3 (-299.35) -> 0 (None) | 3 -> 0 | 3 -> 3 |
| vail_co|northwest|moderate | 37 -> 37 | 2 -> 2 | 0 (None) -> 0 (None) | 2 -> 0 | 2 -> 2 |
| vail_co|pair|high | 38 -> 38 | 0 -> 0 | 3 (-305.41) -> 0 (None) | 3 -> 0 | 3 -> 3 |
| vail_co|south|high | 40 -> 40 | 0 -> 0 | 0 (None) -> 0 (None) | 0 -> 0 | 1 -> 3 |
| vail_co|south|moderate | 40 -> 40 | 0 -> 0 | 0 (None) -> 0 (None) | 0 -> 0 | 0 -> 2 |

### lane A's 32 non-valley fires — before -> after (32 runs)

| | before | after |
|---|---|---|
| runs | 32 | 32 |
| coated | 1066 | 1066 |
| rows | 2 | 2 |
| transit | 0 | 0 |
| deepest | None | -145.0 |
| over30 | 0 | 2 |
| band_s | 0.0 | 1214.6 |
| off_map | 4 | 4 |
| reloc | 17 | 18 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| no_depth | 2 | 0 |

| map | coated before -> after | rows | off map | reloc |
|---|---|---|---|---|
| aspen_co | 101 -> 101 | 0 -> 0 | 1 -> 1 | 6 -> 6 |
| boulder_co | 56 -> 56 | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| glenwood_springs_co | 78 -> 78 | 0 -> 0 | 2 -> 2 | 4 -> 4 |
| malibu_ca | 151 -> 151 | 0 -> 0 | 1 -> 1 | 0 -> 0 |
| paradise_ca | 170 -> 170 | 0 -> 0 | 0 -> 0 | 1 -> 1 |
| park_city_ut | 178 -> 178 | 2 -> 2 | 0 -> 0 | 6 -> 7 |
| rancho_santa_fe_ca | 240 -> 240 | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| santa_rosa_ca | 92 -> 92 | 0 -> 0 | 0 -> 0 | 0 -> 0 |

| run | coated | rows (deepest) | off map | reloc | 46a fired |
|---|---|---|---|---|---|
| park_city_ut|std|side|extreme|24h | 36 -> 36 | 1 (None) -> 1 (-145.01) | 0 -> 0 | 1 -> 2 | |

### 24-h fires on six more maps — before -> after (8 runs)

| | before | after |
|---|---|---|
| runs | 8 | 8 |
| coated | 247 | 247 |
| rows | 4 | 1 |
| transit | 4 | 1 |
| deepest | -305.4 | -0.9 |
| over30 | 3 | 0 |
| band_s | 288.4 | 2.5 |
| off_map | 7 | 4 |
| reloc | 13 | 16 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| no_depth | 0 | 0 |

| map | coated before -> after | rows | off map | reloc |
|---|---|---|---|---|
| aspen_co | 40 -> 40 | 0 -> 0 | 0 -> 0 | 2 -> 2 |
| beaver_creek_co | 52 -> 52 | 1 -> 1 | 1 -> 1 | 3 -> 3 |
| boulder_co | 22 -> 22 | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| glenwood_springs_co | 32 -> 32 | 0 -> 0 | 2 -> 2 | 3 -> 3 |
| paradise_ca | 30 -> 30 | 0 -> 0 | 0 -> 0 | 1 -> 1 |
| vail_co | 71 -> 71 | 3 -> 0 | 4 -> 1 | 4 -> 7 |

| run | coated | rows (deepest) | off map | reloc | 46a fired |
|---|---|---|---|---|---|
| vail_co|std|canon|high|24h | 37 -> 37 | 3 (-305.41) -> 0 (None) | 3 -> 0 | 3 -> 3 | |
| vail_co|std|side|extreme|24h | 34 -> 34 | 0 (None) -> 0 (None) | 1 -> 1 | 1 -> 4 | |

### lane A's 24-h valley pair — before -> after (2 runs)

| | before | after |
|---|---|---|
| runs | 2 | 2 |
| coated | 74 | 74 |
| rows | 3 | 1 |
| transit | 3 | 1 |
| deepest | -682.5 | -682.5 |
| over30 | 3 | 1 |
| band_s | 419.7 | 258.7 |
| off_map | 2 | 0 |
| reloc | 4 | 4 |
| wd | 0 | 0 |
| dv | 0 | 0 |
| no_depth | 0 | 0 |

| map | coated before -> after | rows | off map | reloc |
|---|---|---|---|---|
| beaver_creek_co | 34 -> 34 | 1 -> 1 | 0 -> 0 | 2 -> 2 |
| vail_co | 40 -> 40 | 2 -> 0 | 2 -> 0 | 2 -> 2 |

| run | coated | rows (deepest) | off map | reloc | 46a fired |
|---|---|---|---|---|---|
| vail_co|std|canon|extreme|24h | 40 -> 40 | 2 (-339.91) -> 0 (None) | 2 -> 0 | 2 -> 2 | |
