# Eagle River valley, iteration 1 (0109ad3): independent review (Sonnet)

Probes and data: `out/eagle_valley/probes/iter1_review/` (product code untouched).

**Verdict.** Keep it. The audit is exact and the service gain is real (+24 homes), but the behaviour knobs cleaned no run of transit exposure, keep-frozen exposes a latent loss path, and the gain is keep-frozen's alone. Four small fixes first (section 4).

## 1. Claims checked

- **Numbers reproduce.** Served 1,023 to 1,047 (up in 11 runs, down in 1), unprotected 20 to 15, abandoned_unsavable 79 to 58, relocations 37 to 31, km 6,027 to 5,624, closest-margin median 0.82 to 1.03 h. The 13 one-click presets over 3 h: served 284 to 291, no overrun row in either arm.
- **Like for like.** The audit-only arm is tick-for-tick identical to baseline (crews, served, acres: 33/33), so "0 to 8" is the audit gaining sight; the behavioural effect is audit-only to iter1. The harness "violations" sums each run's peak per tick, i.e. runs with a row (8 in both arms). Rows: 12 to 10; distinct episodes 8 to 8, in the same 8 runs; exposure seconds 2,484 to 1,554; rows over 60 min past the line 10 to 6. Gone: bc|NW|high g3 ingress, both vail|pair 11.25 h rows (-327 min), bc|canonical|high g1. New: bc|canonical|high g2 t26 (-28.9 min), vail|pair g3 t20 (-0.08 min). The four deepest (bc|canonical|extreme -434, vail|canonical|extreme -400, bc|NW|high -262, bc|NW|extreme -328 min) are untouched.
- **The path audit judges what it says.** I replayed all 66 arm-runs with an independent sampler (`probe_full2.py`: wraps `_move_leg`, 20 m time-stamped samples, no `path_track`, no `path_cell_runs`): the same 12 and 10 rows, crews and ticks, margins within 0.05 min, nothing missed, nothing extra.
- **The rows are law exposure, not fire contact.** All 10 are "imminent" egress, none burned. The nominal front reaches the pass point 4.1 h later (2 rows), 7.5 h later (1) or never in 12 h (7). Against the 7-member ensemble, 8 of 10 have no member within the buffer at the pass, and 7 of those are cells no member burns (the 500 m erosion halo). Only bc|canonical|high g3 t25 (and its pair twin) has a member front 2.4 min ahead of the crew. The simulator's P10 is 0.90x nominal (live: 0.46x), so rows are a floor.
- **What carries the gain.** `crew_ingress_judged` refused 0 stops in 66 valley runs, and iter1 with `plan_route_exact` off is tick-for-tick identical (33/33). The gain is keep-frozen: en-route head swaps 130 to 3, idle crew-ticks with feasible work waiting 170 to 24. 27b is a live net, not dead code: 106 refusals when crews drive 25% slower than planned; in chord-planning mode it cuts beaver_creek_westerly rows 11 to 1 for -3 homes.

## 2. Regressions and side effects

- **vail|south|extreme, the new withdrawal.** At t6 sim-g1 heads for sim-c14 with a 17.7 min planned margin against a 22.1 min road egress. The arrival guard withdraws it (4.6 min deficit), the home is lost (unprotected 0 to 1) and its 16-stop queue is dumped (served -2 at 2 h and 4 h, level by 6 h). Iteration 0 had the same doomed head stop (4.43 min) and escaped only because the ping-pong reassigned it. park_city_windstorm repeats it (t5, sim-c32, 1.8 min): 2 doomed stops in about 46 runs, both guard disagreements with no field change. Cause: dispatch needs only `slack_min` (15 min) plus a zone-T* egress test; the guard demands `deploy_end + road egress <= home P10 - buffer`; 27b's `_ingress_unworkable` has no egress term.
- **eagle_vail|canonical|high:** served 28 to 26, km +16%, relocations 0 to 1. g3's proactive escape (t19) and g2's work park both crews at East Vail (z6), idle 5.5 to 10 h, while two homes sit `no_safe_corridor` (fire never reached them).
- **The new near-miss rows come from the extra service.** bc|canonical|high g2 serves an extra home at 5.25 h, parks at Edwards with 0.9 h of life and flees at 6.5 h with the P10 front 66 s behind; vail|pair g3 keeps working until its cell has 0.55 h of life.
- **Small.** km up in 10 runs; closest tick-end margin down in 5 (all still 0.75 h or more); idle share 60.5% to 61.1%; idle-while-blocked 909 to 928; crews off the map 2 to 2. No crew is stranded by ingress refusals.

## 3. Test pins: none hides an iteration-1 regression

- All 20 tests in `test_crew_path_audit.py` pass with the three behaviour knobs forced off (`iter0_plugin.py`): every pinned exposure (slider withdrawals, rug-pull, old-black halo, fire lit on a crew) predates iteration 1. The 36 new tests and 124 tests of six modified files pass at HEAD.
- The Camp-Fire run (`campfire_ab.py`) is identical in both arms: transit rows at ticks 65, 82, 88, 89 (-496, -326, -909, -217 min). The armed twin stops at tick 65, so three of four are pinned nowhere under the armed defaults, though 120 ticks run in 19 s.
- The twins pin exact defect values (-495.88 min). Make the law-level assertions `xfail(strict)` and keep the numbers as `<=` ratchets.

## 4. Fix in iteration 1

1. **The page.** `console/simulator.html` shows every `crew_overruns` row as "CAUGHT IN FIRE" and ignores `transit`; the scoreboard counts only this tick's rows, so a transit row reads UNSAFE for 15 min, then "0 - SAFE". Add a cumulative `transit_overruns_total` and a "drove through the band at hh:mm" label.
2. **Guard parity.** The planner margin must cover the stop's road-egress minutes; re-judge keep-frozen heads every tick with the guard's predicate.
3. **Twin ratchet** (section 3), and **harness metrics:** report episodes, exposure seconds and a drive-margin histogram (57 lawful drive crew-steps pass within 15 min of the line; 17/33 runs within 10 min).

## 5. Iteration 2: what the evidence says

`exit_lead.py` bisects each idle crew's latest lawful departure to every zone with the engine's own leg law.

- **Pockets form at assignment, not later.** Windows to leave: bc|canonical|extreme g2 none after its last stop (Eagle-Vail has 8 h of life but no lawful route); bc|NW g2 none from tick 1 (staged 1.4 km from the ignition); vail|canonical|extreme g3 Minturn open 5.9 min at 4.5 h; bc|canonical|high g2 0.2 min of egress lead at stop end; only vail|pair g3 had room (Minturn open 77-92 min at 4.0-4.25 h). Durable-zone choice helps at most 2 of 8 episodes, leave-while-open 1. **Build the egress-corridor guard and margin first**, as one predicate used at plan, leg start, every tick for frozen heads, arrival and in-flight deploys. Size the margin from data (cost bound: `route_margin_roads_min` 45): a 25% slower drive turns 10 rows into 16 (11 of the 12 shallow ones within 7 min of the line) and cuts served 5%.
- **No egress law saves bc|NW g2.** Fix it by staging, or exclude it from acceptance. Staging by need must cover in-zone crews too (58 homes abandoned; 928 idle-while-blocked crew-ticks, 33% of idle).
- **Reinforcement.** g3 coats 269 of 1,047 homes (idle 35+ of 48 ticks in 7 runs). `_run_audit`, `_path_overruns` and the sweep skip INBOUND crews, so an entry leg driven while INBOUND is an unaudited drive; flip its status at the map edge.
- **Live-watcher posture: do not arm by default.** On iter1: served 949 (-98), unprotected 34 (from 15), relocations 75 (from 31), crews off the map 20 (from 2), rows 13. The audit looks quieter only because off-map crews are skipped; judge on unprotected and off-map counts.
- **Acceptance.** No row deeper than 30 min; report episodes, exposure seconds and member-credible rows; off-map and unprotected no worse.
