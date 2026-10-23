# Eagle River valley, iterations 2-3 (b9e3b68, 1963df2): independent review (Sonnet)

(Saved by the integrator: the reviewer's environment refused the write. Probes and data:
`C:\Users\colin\wg_response\.claude\worktrees\eagle-valley-iter3\out\eagle_valley\probes\iter23_review\`.
No product code was touched.)

**Verdict.** Keep all three, but do not ship them as they are. The safety result is real, survives an
independent audit and generalizes to fires nobody tuned on. On 24 fresh fires transit rows fall 13 to 3 and
the deepest pass 345 to 22 min. At a 13:13 start rows fall 14 to 2 and the deepest 612 to 24 min. Before
shipping: arm `reinforce_entry_by_need`, verify or drop the one refuge that carries all of iteration 3's
gain, fix the page, and correct the write-up.

## 1. Claims

- Every headline number reproduces from the runs.jsonl files. Like for like holds:
  - The nominal fire is identical in 33/33 runs across iterations 0-3 (acres_by_h; 125 reached).
  - Opus's probe arms reproduce the integrator's matrices run for run.
  - With all 12 knobs off, the iteration-3 code replays iteration 1 tick for tick (6 runs).
  - The new test files pass (one strict xfail).
- Served is not protected. Of the 125 homes the fire reached, 105 / 110 / 111 / 111 were coated in time
  (iterations 0-3). Iteration 3's +31 are homes the fire never reaches in 12 h: +20 from far-side zones
  (all Beaver Creek) and +11 from ember ETAs. "Homes protected (of 1,320)" should read "coated".
- The posture arm is not like for like. It changes the truth fire in 32 of 33 runs (12-h acres +6 %, reached
  125 to 133). So 926 / 41 / 17 mixes forecast pessimism with a faster fire.

## 2. Audit, independent

I replayed all 33 iteration-3 runs with a 20 m sampler that wraps `_move_leg` and uses none of the engine's
audit code.
- It finds exactly the engine's two rows (bc|northwest|high / extreme, tick 2: -20.8 / -24.4 min, 0.9 s) and
  nothing else.
- No ensemble member's front is within the buffer at the pass (earliest 3.05 / 3.63 h later).
- With every crew driving 25 % slower than planned, still only those two rows.
- Lawful drives within 15 min of the line fall 67 to 29 crew-steps. All are ingress or chained post-work legs
  at the 30-min margin.

## 3. Fresh fires (the new evidence)

24 ignitions outside the matrix: 8 per tile, high, 07:13, 12 h, single seed, so read it as directional. Same
code, three arms: all 12 knobs off (= iteration 1) / armed / durable law off only.
- Rows: 13 in 7 fires (deepest -345 min, 2,786 s) / 3 in 2 (-22, 90 s) / 7 (-368).
- Served 634 / 647 / 647; reached 114; unprotected 40 / 42 / 42; crews off the map 5 / 10 / 5.
- Per tile (served; unprotected): Vail 260; 10 to 240; 15. Beaver Creek 218; 24 to 242; 23. Corridor 156; 6 to
  165; 4.
- **The durable law (28)** costs no home and removes every deep row. Its price is off-map crews: the road-refuge
  branch (`_durable_egress`, `_last_exit_choice`) accepts nodes beyond the tile edge. Cases: bc|b340 g2 at
  2.25 h, vail|b110 g1/g2 at 5 h, and at 13:13 bc|canonical|extreme g1/g3 with 4.35 h of life left. It needs an
  `_on_map` test.
- **Vail's loss is reinforcement staging.**
  - In vail|b70 the east entry is behind the fire, so sim-g3 stays inbound for 12 h (iteration 1 teleported it
    to Minturn).
  - In b250 restage parks it 12 km from the work.
  - Stage-by-need and restage off: 251 served, 11 unprotected.
  - The existing, unarmed `reinforce_entry_by_need` (34b): 256 / 10 (iteration 1: 260 / 10).
  - 34b on all 24 fires: 665 served, 37 unprotected, Beaver Creek unchanged. On the 33 matrix runs: 1,070 / 14,
    rows unchanged.
  - 34b read as "neutral" because no matrix fire starts between the east entry and the zones. It is a founder
    call, since it lets a 1-h crew enter from the west.
  - On Vail, guard and deadline have no effect, and turning the 45-min margin off is worse (232).

## 4. The questions asked

- **45-min margin.** In the matrix the refused homes (bc|canonical|high c10, c15, c37) have P10 ETAs of 22-39 h,
  so it refuses only where the fire does not come. Not in general: the public Eagle/Gypsum preset (6 h) loses
  sim-c5 to the margin alone (nominal arrival 3.9 h, P10 2.9 h; iteration 1 coats it at 0.35 h). Unprotected
  there goes 0 to 1.
- **Pocket exit: the right call.** bc|northwest g2 stands 1.4 km from the ignition. At the first field every
  way out is in the band: -21 / -24 min for 1 s at 0.5 h, against -262 / -328 min at 4.5 / 5.5 h by waiting.
  With the durable law off the fresh fires give -368 / -345 / -282 min. With it on, no pocket aged past the
  one-buffer cap (deepest -22 min; the one exit was -2.4 min for 12 s).
- **Far-side refuges: one zone carries everything.**
  - z4 alone gives bc canonical / pair / late high 39 / 39 / 38, against 34 / 34 / 34 with far zones off.
  - z5 alone does nothing; northwest high (+6) needs both; the corridor's z7 / z8 do nothing.
  - Fresh Beaver Creek fires: far zones off 221 served / 26 unprotected, z4 only 243 / 23, both 242 / 23.
  - z4 is 13 ha (16 cells) of LANDFIRE "developed" ground, 127 m from fuel, 49 % timber within 300 m. z1 / z3 /
    z5 are 666-918 ha.
  - 471 crew-ticks name it, and the page lists "Cordillera west, Club House Dr (developed, non-burnable)".
  - Ground-verify it, or drop it and report Beaver Creek as unchanged.
- **Ember ETAs.**
  - +11 homes, one in each of 11 runs; rows, off map, unprotected and relocations are identical, and crews drive
    44 km less.
  - All are developed-cell homes with ETAs of 9.7-62 h, coated 0.8-3.7 h in. One (vail|northwest|extreme
    sim-c22) is coated 58 h before its ember.
  - That is idle-crew work (crews idle ~60 %), harmless. The ETA is one nominal-member landing (p10 = p50 =
    p90), not a pessimistic bound.
- **13:13, same code, knobs off vs armed.** Rows 14 to 2, served 1,044 to 1,064, unprotected 16 to 13,
  withdrawals 2 to 0, off map 2 to 2 (different runs). Iteration 3's two are deliberate road-refuge exits.

## 5. Page

Rendered in a local server. Tick 2 reads "sim-g2 drove through the band at 14:29z, 21 min past the line";
tick 3 reads "0 now - 1 this run" in amber. Missed:
- The alert feed still says "sim-g2 caught in fire", stamped with the tick end (14:43Z), not the pass time.
- The scoreboard row label, the legend and the red-flashing marker still say "caught in fire".
- `scoreboard.overrun_ledger` is sent but never rendered.
- The test only greps source text.

## 6. Tests

Stripping the OFF pins from the nine legacy test files makes 26 tests fail: byte-identity goldens, control
worlds, labels, scenarios that no longer arm.
- Two failures are law-level and real: Glenwood extreme/generic "overrun is gone" (a row at tick 2, 17.9 min)
  and the rug-pull world (serves 0, was 4 / 1; one withdrawal).
- None hides a safety failure.
- The founder's Paradise crown-fire repro passes armed (28 s), so its pin is unnecessary.
- The strict xfail is a real defect (Paradise extended, sim-g1: 496 / 909 min at ticks 64 / 87). The public
  Paradise extended run will now show it.
- Nothing tests crews leaving the map under the new laws.

## 7. Ship?

First:
1. Arm 34b.
2. Ground-verify Cordillera, or drop it.
3. Add an `_on_map` test on durable refuges.
4. Fix the page strings, the feed time and the ledger rows, with a DOM test.
5. Correct the draft: coated vs protected, the posture caveat, the fresh-fire and 13:13 results, off-map, the
   Paradise and Eagle/Gypsum cases.
6. Un-pin the Paradise repro.

Then re-run the matrix, 13:13, the 24 fires and the 13 presets (all runnable from the probes directory). Keep
the 24-fire set as a second matrix.
