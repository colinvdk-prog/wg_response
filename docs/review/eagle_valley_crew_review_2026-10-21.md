# Eagle River valley: crew management review and five improvement iterations (2026-10-21 to 23)

Founder, 2026-10-20: *"review the response crew management for the eagle river, avon,
vail minturn area only. run the simulation. analyze it using an opus and sonnet agents for
safety, propagation speed etc. recommend improvements and perform at least two new
iterations"*; then *"run iteration 1 as soon as it lands"*, *"then start iteration 2 straight
after"*, *"run iteration 3 after iteration 2 lands"*, *"continue"*. Founder decisions on 2026-10-22
(reinforcements may enter from either end of I-70; the Cordillera west refuge is dropped) and
2026-10-23 (arm the escape-margin ramp).

## Summary for the founder

**What was simulated.** The three simulator maps that cover the valley — Vail / East Vail /
Minturn, Beaver Creek / Avon / Edwards, and the whole Eagle → Vail corridor — with 40 homes
and 2 crews (+1 reinforcement at 1 h) each, as the public simulator runs them (Eagle airport
weather, crown fire, the spread-direction fix). 33 fires of 12 hours each: the standard
west-south-west start, a fire south of town (the Minturn / Beaver Creek side), a north-west
wind fire, two fires at once, and a second fire breaking out near town two hours in; at
moderate, high and extreme.

**What the two reviews found** (an Opus model on crew safety and management, a Sonnet model on
fire spread; a second Sonnet model checked iteration 1 independently).

- **Crews were passing through the danger band between checks.** The safety audit looked at
  crews only every 15 minutes. Between checks, crews drove through the pessimistic fire band
  12 times in 8 of the 33 fires — up to 7 hours past the line — and none of it was counted.
  None was actual fire contact; it was the safety margin crews must keep.
- **Crews parked where their way out was closing.** After work, a crew went to the nearest
  safety zone, not one that would stay safe; at Avon, Edwards and Dowd Junction crews sat
  until the last road out closed, then left through the band.
- **A planning bug sent crews back and forth.** A crew about to arrive at a home lost it in
  the next re-plan and was sent elsewhere, then back (39 reversals, 261 km wasted; both homes
  sometimes went uncoated).
- **North-west wind fires strand crews west of the fire**, because every safety zone on the
  Beaver Creek map is at or east of such a start.
- **Fire spread is plausible in speed** (the fastest hour a median 7.8 % of the wind, against
  the ~10 % rule of thumb for forest fires), **but its timing errors mostly run late**, which
  is the unsafe direction for crews: every fire starts at 07:13 on a single airport wind, and
  embers landing next to developed homes give those homes no warning.

**What changed** (all simulator-only; the live watcher is untouched):

| | Iteration 0 (as deployed) | Iteration 1 | Iteration 2 | Iteration 3 | Iteration 4 | Iteration 5 (final) |
|---|---|---|---|---|---|---|
| Homes coated (of 1,320) | 1,023 | 1,047 | 1,040 | 1,071 | 1,050 | 1,057 |
| Homes the fire reached that were coated in time (of 125) | 105 | 110 | 111 | 111 | 111 | 111 |
| Homes the fire reached uncoated | 20 | 15 | 14 | 14 | 14 | 14 |
| Crew passes through the danger band | 12 (unseen) | 10 | 2 | 2 | 2 | 3 |
| Deepest pass past the safety line | 434 min | 434 min | 24 min | 24 min | 24 min | 24 min |
| Time crews spent in the band | 41 min | 26 min | 2 s | 2 s | 2 s | 18 s |
| Crews driven off the map | 2 | 2 | 0 | 0 | 0 | 0 |
| Relocations | 37 | 31 | 14 | 15 | 15 | 18 |
| Kilometres driven | 6,027 | 5,624 | 5,700 | 5,741 | 5,749 | 5,569 |
| Median closest call | 0.82 h | 1.03 h | 1.36 h | 1.36 h | 1.69 h | 1.35 h |

(The 33 fires at 07:13. Iteration 3 still had the Cordillera west refuge, which the founder dropped
in iteration 4; iteration 4's lower coating count is that decision. Iteration 5's third pass is a
6-minute corridor pass the escape-margin ramp brings; its lower median closest call is the ramp's
shorter margin on short drives.)

The passes left are crews leaving a pocket the north-west fire forms around them in its first half
hour at Beaver Creek — 21-24 minutes past the safety line for about a second, three hours before any
forecast fire reaches the spot (waiting, as before, cost 262-328 minutes) — and one 6-minute corridor
pass. "Coated" counts every home a crew gelled; of the 125 homes the fire actually reached in 12 hours,
105 were coated in time before this work and 111 after it.

**It holds beyond the 33 fires it was tuned on.** The final code against iteration 4, on five test sets:

| Set | Homes coated | Reached homes uncoated | Deepest pass past the line | Passes > 30 min | Crews off the map |
|---|---|---|---|---|---|
| 33 fires, 07:13 | 1,050 → 1,057 | 14 → 14 | 24 → 24 min | 0 → 0 | 0 → 0 |
| 33 fires, 13:13 | 1,034 → 1,046 | 20 → 15 | 24 → 24 min | 0 → 0 | 0 → 0 |
| 24 fresh fires (chosen afterwards by the independent reviewer) | 647 → 649 | 39 → 39 | 337 → 22 min | 1 → 0 | 8 → 4 |
| 13 one-click demos (6 h) | 473 → 469 | 7 → 7 | none | 0 | 0 → 0 |
| Every crew driving 25 % slower than planned | 998 → 1,005 | — | 46.6 → 24.8 min | 1 → 0 | 2 → 0 |

Against the code as deployed, the fresh fires fell from 13 passes (deepest 345 min) to 2 (22 min), and
the 13:13 fires from 14 (deepest 612 min) to 3 (24 min).

**Founder decisions.** 2026-10-22: the 1-hour reinforcement crew may enter from either end of I-70,
whichever reaches the most threatened homes on a lawful road; the Cordillera west refuge is dropped
(13 hectares of subdivision streets with half timber within 300 m is not a credible safety zone
without a ground check). 2026-10-23: the escape-margin ramp is armed (30 minutes plus a third of the
drive, up to 45, instead of a flat 45).

**Recommendation.** Ship iterations 1-5 to the public simulator: every crew-safety measure is better
or equal on all five test sets, no pass goes more than 25 minutes past the safety line anywhere, and
no crew is driven off the map in the 33 fires. Known costs: Park City's windstorm demo leaves one
reached home uncoated (Eagle / Gypsum's demo gains one); the extended Paradise map still has one
dead-end refuge defect (below). The live watcher is not changed by any of this.

## Method

`scratch_review/eagle_valley/run_matrix.py` builds each fire through the server exactly as the
page does (the one-click roster of each map), runs 48 steps of 15 minutes, and records the
safety record (departure violations, crew-overrun rows — with iteration 1, the transit rows of
the path audit — withdrawals, relocations, crews off the map, each crew's closest margin to the
pessimistic front), service (homes coated, outcomes, homes the nominal fire reached unprotected,
protection lead), crew time and kilometres, and the fire (acres per hour, head speed, crowned
area). `scratch_review/eagle_valley/summarize.py` compares iterations. Every iteration ran the
same 33 fires on its own commit; the result files are `scratch_review/eagle_valley/*_runs.jsonl`.
Iteration 0's numbers are quoted under the iteration-1 path audit (the "audit-only" arm: the
iteration-0 behaviour, tick for tick, with the new audit) — the original audit reported 0 passes
because it could not see them.

The reviews: `review_iter0_crew_safety.md` (Opus), `review_iter0_propagation.md` (Sonnet),
`review_iter1_sonnet.md` (Sonnet, independent check of iteration 1), all in
`scratch_review/eagle_valley/`.

## Iteration 1 — the audit sees the whole drive (SAFETY SEMANTICS 25-27; commit 0109ad3)

- `crew_path_audit` (25): the crew-safety audit judges every crew's driven path between steps
  and every departure against all fires (transit rows flagged and counted).
- `dispatch_keep_frozen` (26): a crew's near-term stop is kept through re-plans — the
  back-and-forth bug (head swaps 130 → 3).
- `plan_route_exact` (27a) and `crew_ingress_judged` (27b): the planner judges the road the crew
  drives; every drive in is judged as it starts. (Neither fired in these valley runs; they are a
  net for slower drives and chord planning.)

Result: +24 homes, unprotected 20 → 15, passes 12 → 10, band time −37 %, km −7 %. The independent
Sonnet check confirmed the audit with its own sampler (same rows, margins within 0.05 min) and
found one regression — a home lost to a planner / arrival-guard disagreement (Vail south extreme)
— fixed in iteration 2.

## Iteration 2 — a crew never waits in a pocket (SAFETY SEMANTICS 28-33; commit b9e3b68)

- `egress_durable_min_h` (8 h) and `escape_last_exit_lead_min` (30): crews egress to a zone that
  stays safe, and leave while the last lawful way out is still open; `escape_pocket_exit`: a crew
  with no lawful way out leaves at once by the least-exposed way while the band is shallow.
- `egress_corridor_guard` (29): one rule everywhere — a home is only worked if the crew can still
  leave it lawfully afterwards (judged at setting out, en route, on arrival and every step of
  work); `plan_egress_time_deadline` (32) makes the planner use the same rule.
- `reinforce_stage_by_need` / `idle_restage` (30): the reinforcement enters at the map's road
  entry and is audited from there; idle crews restage toward the waiting homes.
- `route_margin_egress_roads_min` (31): road escapes keep 15 minutes beyond the safety line (sized
  by re-running everything with crews driving 25 % slower than planned).
- `crew_overrun_ledger` (33): the page shows a pass as "DROVE THROUGH THE BAND at hh:mm" and keeps
  the run's total, instead of "CAUGHT IN FIRE" for one step and then "0 — SAFE".

Result: passes 10 → 2 (both Beaver Creek north-west pocket exits, 21 and 24 minutes past the line
for one second each; no ensemble member's fire reaches those points for 3 more hours), deepest
434 → 24 min, crews off the map 2 → 0, relocations 31 → 14, unprotected 15 → 14; 7 fewer homes
coated (Beaver Creek coatings refused by the 45-minute escape margin; the fire never reached them).

**The live watcher's wider safety margin is not for the simulator.** On top of iteration 2 the live
P10 posture (model error, gust blend, slope vector, floor gate) coats 926 homes instead of 1,040,
leaves 41 reached uncoated instead of 14, drives 17 crews off the map and passes 185 minutes past
the line. Caveat (independent review): it is not like for like — the posture also changes the
simulated fire itself (12-h area +6 %, homes reached 125 → 133), so the numbers mix wider forecasts
with a faster fire. It stays off in the simulator.

## Iteration 3 — refuges on the far side, ember warnings, afternoon fires (SAFETY SEMANTICS 34-35; commit 1963df2)

- `sim_far_side_zones` (34a): zones on the far side of the likely fire runs, picked from each map's
  non-burnable ground and road graph and named from OpenStreetMap — Freedom Park / Battle Mountain HS
  (Edwards) on the Beaver Creek and corridor maps, Wolcott (US-6 / SH-131) on the corridor. (Cordillera
  west was added and then dropped by founder decision, above.) Vail gets none: its core is reached by
  every Vail fire within 4-10 h and Red Cliff and Vail Pass are off the map.
- `reinforce_entry_by_need` (34b): the reinforcement enters by whichever I-70 end reaches the most
  threatened homes on a lawful road — armed by founder decision 2026-10-22 (iteration 4).
- `ember_eta_landings` (35): a home on a developed cell gets a fire-arrival time from the first ember
  landing within 200 m when that comes before the front (the spread itself is unchanged). It added 11
  coatings, all homes the fire never reached in 12 h; the time is one nominal-member landing, not a
  pessimistic bound.
- Afternoon starts: `start_local_hour` on the session and `POST /api/sim/new` (absent = 07:13,
  unchanged); the harness's `--start-hour`.

Result (33 runs, 07:13): coated 1,040 → 1,071 (up in 12 runs, down in none), uncoated 14, passes 2 (24
min), crews off the map 0, idle crew-time while homes waited behind a blocked road 869 → 616 ticks. At
13:13: coated 1,064, uncoated 13, passes 2 (24 min), 2 crews off the map (Beaver Creek extreme, escaping
east by a road refuge past the map edge — fixed in iteration 4). Independent review:
`scratch_review/eagle_valley/review_iter23_sonnet.md`.

## Iteration 4 — pre-ship fixes and the founder's calls (SAFETY SEMANTICS 36-37; commit 8d0fbdf)

- `escape_refuge_on_map` (36): a refuge off the map is the last exit, never an early one (the
  road-refuge rule had sent crews off the edge: 5 → 10 on the fresh fires; now 4).
- The page: a pass reads "drove through the band at hh:mm, N min past the line" in the feed (stamped
  with its pass time), the banner, the crew card and the tooltip; the run's ledger is shown; the
  marker glows orange instead of the red "caught in fire" flash; a test renders the real page in a
  headless browser and reads it.
- The founder's crown-fire Paradise repro un-pinned (it passes with every law on).
- `route_margin_egress_ramp` (37): the escape margin grows with the drive — built, measured, left off
  (it then cost homes elsewhere); armed in iteration 5.
- The founder's calls applied: reinforcement entry by need armed; Cordillera west dropped by a
  per-zone switch.

The calls exposed one deep pass each — 337 minutes on a fresh Beaver Creek fire (dropping
Cordillera) and 46.6 minutes in the slower-drive test (reinforcement entry) — from one remaining
flaw: a home counted as workable if its way out led to any zone, however short-lived.

## Iteration 5 — a way out leads somewhere that lasts (SAFETY SEMANTICS 38; commits 03dd325, 960c9d6)

- `egress_dest_durable_h` (38, 8 h): a home is workable only if the crew's way out leads to a zone
  or on-map road refuge that stays safe long enough to be left again lawfully, judged with the same
  rule at planning, at setting out, every step of a kept job, on arrival and every step of work.
  Both deep passes are gone; the Vail live-posture passes (185 min) and a 1,346-minute pass in the
  founder's Paradise crown repro go with them.
- 960c9d6 arms the escape-margin ramp (founder decision 2026-10-23): with the new law it keeps every
  acceptance criterion and coats more homes (13:13: 20 → 15 reached homes uncoated).

Suite on 960c9d6: 2,705 passed, 14 skipped, 1 expected failure (the Paradise dead end below).

## Open items

- **A dead-end refuge on the extended Paradise map** (an expected-failure test): a crew waits at a
  refuge with 24 hours of life whose roads out close while it waits; it later drives out 496 / 909
  minutes past the line. Needs a waiting-crew law (watch the exits of the refuge, refuse restaging
  past its keep-until).
- **Homes beyond the fire** still cannot be reached; far-side refuges help only where they make a
  home's way out lawful. Vail has none on its map (Red Cliff and Vail Pass are off it).
- **Ember warnings** use one forecast member's landing, not a pessimistic bound.
- **The live watcher is unchanged** by this review; its safety margin (P10 posture) was measured here
  only as a simulator arm (and is not like for like: it also speeds the simulated fire).
- **Refuges are map choices**, named from OpenStreetMap; no site has been checked on the ground.
- **Merging and deploying:** all five iterations are merged on branch review/eagle-valley-2026-10-20.
  Main also carries an uncommitted feed-fusion change (SAFETY SEMANTICS 24) from 2026-10-15, which has
  to be committed or set aside before this branch merges into main and the simulator is rebuilt.
