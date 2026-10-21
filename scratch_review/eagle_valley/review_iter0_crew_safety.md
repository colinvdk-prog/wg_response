# Eagle River valley — crew safety and crew management (iteration 0, Opus lens)

Method: read-only probes in `out/eagle_valley/probes/crew_safety/`, product code
untouched. `probe.py` replays a matrix run tick for tick (all 33 baseline runs
reproduce exactly: served 1,023, reached / unprotected 125 / 20) and adds a
**continuous path audit**: each crew's driven path between tick ends, sampled every
~45 m at its pass time, judged by the 10c law (burned, or eroded P10 < pass + 30 min;
old black excused). Also per-tick plans, pocket status (`pocket_probe.py`), departure
margins against every fire, single-access depth (`deadend2.py`). `patches.py` holds
the fix prototypes (outputs `all/` = baseline, `varA`, `varB8`, `varC`, `varABC`,
`varF`, `varAF`, `varD`, `varDR`; table by `compare2.py`); `frozen_check.py`,
`leg_check.py`, `egress_check.py`, `overreach.py` reproduce the F3, F4, R4, F5 evidence.

## Bottom line

The 0 / 0 record is a tick-end record. Between tick ends crews drove through the
eroded-P10 band in 12 episodes in 8 of the 33 runs (1,029 path samples, worst 433 min
past the 10c line, never inside the nominal fire); the audit counted none. The
"closest margin 0.5 h" (harness margin = eroded P10 − now, so 0.5 h *is* the 10c line)
is not coating near the front: 13 of the 14 closest margins are crews the sweep was
ordering out that tick, 8 of them parked 1–6.5 h on the same spot — 7 at I-70 zones
(Edwards ×3, Avon ×2, Dowd Junction ×2) whose escape routes had already closed, each
then fleeing through the band. Homes are lost to a dispatch-loop bug (frozen stops
dropped → en-route ping-pong) and to geometry (crews stranded behind a fire that
severs I-70).

## Findings

**F1. Transit exposure is invisible.** `_run_audit` judges crews only at tick ends.
Worst: bc canonical extreme g2 (9.5 h, 6.4 min in band, −433 min), vail canonical
extreme g3 (11.25 h, 6.1 min, −400), bc NW high g2 (4.25 h, 3.7 min, −262). 11 of the
17 emergency (`zone_threatened`) relocations rode through the band; all 20 proactive
SEM-10e moves were clean.

**F2. Crews park in pockets — an LCES gap.** Post-work egress takes the *nearest*
lawful zone (`dispatch._Ctx._ezones_k`; 20b asks one step of lead); zone life and
exits are never weighed, and later moves key on the crew's own cell, never on whether
an escape route still exists. bc canonical high: g1/g3 egress to Avon with 1.2 h of
life; the last lawful exit (Eagle-Vail, 19 h) closes 15 min later; at 6.0 h they flee
through the band. vail canonical extreme: g3 picks Dowd Junction (6.7 h) over a lawful
Minturn (17 h); Dowd is a pocket within 45 min; 6 h idle, forced out at 11.25 h. bc NW
high: g2 stages at Edwards 1.4 km from the ignition, pocketed from tick 1, idle 4 h,
then the gauntlet.

**F3. Frozen stops are dropped — en-route ping-pong.** `_tick_step` step 4 keeps an
eligible crew's frozen stops (service start < now + 20 min) out of the job pool;
`dispatch.plan` step 2 keeps only incumbent stops that are in the pool
(`keys = [... if key in jobmap]`). Whenever a crew idles (replan every tick), an
en-route crew about to arrive loses its head stop. bc NW high t33: g3 17 m from
sim-c8, sent 4.7 km to c33, sent back next tick; both homes ended unprotected.
eagle_vail canonical moderate: g1 shuttled c33↔c3 for 3 h (~100 km) while g2/g3
idled at Eagle. 39 swaps, 261 km in all.

**F4. The planner judges one road, the crew drives another.** `_plan_road_provider`
caches routes on ~110 m buckets; crews drive `_leg_route` from their exact position.
bc NW high t33: planned route (from 17 m away) 10.7 km, worst +58.6 min; driven route
9.6 km, −51 min, 113 s in band; SEM-23a skips it (fields unchanged).

**F5. Idle time and stranding.** 2,789 idle crew-ticks (60 %): two thirds with nothing
left to do, one third (907) while threatened homes were blocked (`no_safe_corridor`,
`no_safe_egress`). The 24 h coating hold released at tick 1 in all 33 runs — not a
factor. bc NW high: both in-zone crews stranded west of the fire ~10 h (g2 coated 0
homes, g1 4) beside 7 homes at `no_safe_egress` — every zone sits at or east of the
ignition. 421 idle crew-ticks were parked off-zone (escape points; mid-road, where
`revoke_unstarted` leaves a dropped en-route crew). Roads stay shut because the law
reads the ignition-anchored P10: its "already arrived" area is 1.7× the nominal burn
at 4 h, 2.5× at 12 h (bc NW high), never re-forecast.

**F6. Valley geometry.** Zones are I-70 interchanges on the run line; Minturn sits
3.0 km up single-access road, East Vail 2.6, Dowd 2.1; 4 of 15 SEM-10d refuges were
3.2–4.7 km up single-access roads. vail canonical extreme: two crews sat 6 h in
Minturn — safe, but out of the fight once Dowd Junction closed.

**F7. Coating margins: lawful, no slack on the halo side.** 1,023 departures, 0
violations; 25 left < 30 min beyond the 10c line, 6 < 15 min (closest 5.3 min). The
SEM-7 slack floor covers only the raw home deadline; on road legs
`route_margin_roads_min` (30) *is* the 10c line — an I-70 evacuation queue is a
violation.

**F8. Two fires; reinforcement.** SEM-23 released 1 stop, 0 withdrawals. eagle_vail
late: 7 coated homes' worst fire became the late one; the serviced-stop audit reads
only each stop's own fire (all ≥ +2.9 h). The 1 h reinforcement is teleported to
round-robin zone 3 (Minturn on vail_co), unjudged; idle 32–40 of 44 ticks in vail NW
and eagle_vail south.

## Prototype measurements (same 33 runs, 12 h)

A keep frozen stops · B durable egress at the 20b judgement (H = 8 h) · C exact-endpoint
plan routes · A+B+C. F (4th Beaver Creek zone, US-6 at Lake Creek Rd), D (old-black
sources dropped from the halo) and R (re-forecast every 2 h) on subsets.

| metric (33 runs) | baseline | A | B | C | A+B+C |
|---|---|---|---|---|---|
| band samples / episodes (path audit) | 1,029 / 12 | 548 / 10 | 274 / 8 | 1,000 / 11 | **245 / 7** |
| tick-end 10c rows · departure violations | 0 · 0 | 0 · 0 | 0 · 0 | 0 · 0 | 0 · 0 |
| closest margin h, min / median | 0.50 / 0.82 | 0.50 / 1.03 | 0.51 / 0.88 | 0.50 / 0.82 | 0.51 / 1.03 |
| homes served | 1,023 | 1,047 | 1,023 | 1,025 | **1,046** |
| reached / unprotected | 125 / 20 | 125 / 15 | 125 / 20 | 125 / 20 | **125 / 15** |
| idle share · idle while work blocked | 60 % · 907 | 61 % · 853 | 61 % · 884 | 60 % · 908 | 61 % · 939 |
| km driven | 6,471 | 6,069 | 6,205 | 6,464 | **5,884** |
| en-route head swaps (km) | 39 (261) | 0 | 41 (252) | 33 (239) | 0 |
| relocations / withdrawals / left map | 37 / 0 / 2 | 31 / 1 / 2 | 18 / 0 / 2 | 37 / 0 / 2 | 14 / 1 / 2 |

Subsets. **F** (6 Beaver Creek runs, 184 served): F 182, A 200, A+F 198; with A+F bc NW
high 22 → 36 (idle while blocked 94 → 31), but bc canonical high and pair −4 each,
and bc late high −3 with one tick-end 10c row (−2.2 min) — see R4. **D** (bc NW high):
no change (22 served, 80 band samples, up to 4,374 old-black cells masked). **D+R**:
17 served, 55 band samples, unprotected 3 (= baseline). The A / A+B+C withdrawal is a
road-egress abort at arrival (vail south extreme, unprotected 0 → 1 there).

## Recommendations (ranked by value; SC = safety-critical)

Each knob is a new `ResponseConfig` field, default OFF (byte-identical), armed by the
simulator in `SimSession._init_state`.

**R1 (SC) Audit the whole drive — `crew_path_audit`.** *Problem:* F1. *Change:*
`_SimCrew._move_leg` records (t, pos) per vertex (bookkeeping, like `driven`);
`_run_audit` resamples each span at ≤ half a cell and applies the 10c test at each pass
time (old black as today), one row per crew per step (`transit`, worst margin, seconds);
the serviced-stop loop takes the minimum P10 over all fires (as `_work_latest_depart_s`
does). *Effect:* no behaviour change; the baseline would read 12 episodes in 8 runs,
not 0 — the yardstick for R3–R5. *Risk:* rows rise, honestly. *Measure:* `probe.py`
(`interp_track`, `judge`) is a reference implementation.

**R2 Keep frozen stops — `dispatch_keep_frozen`.** *Problem:* F3. *Change:*
`_tick_step` step 4 marks `protected` only for non-eligible (deploying / egressing)
crews; `dispatch.plan` step 2 matches incumbent stops by `client_id` (re-keyed to the
surviving worst-case job), so the existing freeze holds them — also when a second fire
becomes a client's worst case. *Effect (A):* +24 homes, unprotected 20 → 15, swaps
39 → 0, km −6 %, band samples −47 %, no new rows. *Risk:* low — frozen stops still pass
dispatch's feasibility repair; one arrival withdrawal; eagle_vail canonical high −2
(homes with P10 ~56 h, never reached). *Measure:* swaps, served, unprotected.

**R3 (SC) Never park in a pocket — `egress_durable_min_h` (8) +
`escape_last_exit_lead_min` (30).** *Problem:* F2. *Change:* (a) `_ezones_k` /
`egress()`, `_judge_egress_start` (20b), the `_adopt` fallback and `_preposition_crews`
take the nearest lawful zone whose eroded-P10 life at arrival is ≥ H, else the SEM-10d
lawful road refuge (`_road_escape_search(..., lawful_only=True)`), else the longest-
lived; (b) a last-exit trigger in `_crew_safety_sweep`: an idle crew whose cell's life
is < H leaves when the latest lawful departure (LegOracle T*, 10c line) of its best leg
to a durable refuge falls within lead + step — move while the escape route is open,
not when the band arrives. *Effect (B = part a at 20b only):* band samples −73 %,
emergency relocations 37 → 18, closest-margin median 0.82 → 0.88 h, served unchanged.
6 of the 7 residual A+B+C episodes are crew g2 parked at Edwards (17–21 ticks in three
runs, from its staging start in both NW runs; 3 ticks after returning as the only
lawful zone in three) — what part (b) and a staging check target. *Risk:* longer drives
from the work; refuges are not sized safety zones. *Measure:* band episodes, pocketed
idle crew-ticks (`pocket_probe.py`), served.

**R4 (SC) Judge every leg as driven — `plan_route_exact` + `crew_ingress_judged` +
`egress_corridor_guard`.** *Problem:* F4, plus a SEMANTICS 23 gap prototype F exposed:
in bc late high g3's stop c33 — planned at 1.0 h, re-judged and kept after the late
fire — was worked until 3.37 h, when every egress from it was 5.6–86 min past the 10c
line; 20b could only take the least-exposure escape and the 3.5 h audit counted it.
`_ingress_unworkable` has no egress term, and `_road_depart_check` / `_recheck_deploy`
test a deploy's departure against the home deadline only. *Change:* exact endpoints for
crew-origin legs in `_plan_road_provider` (or pin the planned polyline, like
`_escape_pins`); `_ingress_unworkable` at every ingress start; "departure ≤ T* of the
best lawful egress leg, every fire" in `_ingress_unworkable`, `_rejudge_work` and the
deploy guard — re-point or withdraw while a lawful leg remains. *Effect (C):* the
ingress episode disappears, +2 homes (bc NW high), 32 runs unchanged. *Risk:* more A*
on crew legs; a few released stops. *Measure:* ingress band rows; departures without
a lawful egress (target 0).

**R5 Far-side anchors — zone data + `dispatch_road_refuges`.** *Problem:* F5, F6.
*Change:* a zone on both sides of plausible fires on every valley tile (prototype:
Beaver Creek z4); flag single-access zones (Minturn); optionally let dispatch egress to
SEM-10d-validated durable road refuges, and rank two-way-core refuges first in
`_esc_net_key`. *Effect:* A+F unstrands bc NW high (22 → 36) but costs 4 homes each in
bc canonical high / pair — land after R2 and R4. *Risk:* planning-grade locations; more
replanning. *Measure:* `no_safe_egress` home-ticks, idle while blocked, served.

**R6 Stage by need — `reinforce_stage_by_need` + `idle_restage`.** *Problem:* F5, F8.
*Change:* `_tick_step` step 0 brings the reinforcement in from its I-70 entry (Vail
Pass east, Dotsero west) over a judged leg to the durable lawful zone nearest the
unserved threatened homes, not round-robin `zones[(k − 1) % n]`; crews dropped by
`revoke_unstarted` or idle off-zone restage likewise (`_preposition_crews` logic, always
on). *Effect:* not prototyped; targets 421 off-zone idle crew-ticks and a
reinforcement idle 32–40 of 44 ticks. *Risk:* more driving. *Measure:* reinforcement
first gel, off-zone idle, served.

**R7 (SC) Traffic slack on road egress — `route_margin_egress_roads_min` (45).**
*Problem:* F7. *Change:* egress legs' T* (`_ezones_k` computes them in "ingress" mode
with `route_margin_roads_min`, via `_tstar_k`) and the 20b `_leg_lawful` line use the
egress buffer + 15 min; ingress unchanged. *Effect:* not prototyped; the 25 departures
within 30 min of the line move earlier or drop; `--cfg {"route_margin_roads_min": 45}`
bounds the cost (both directions). *Risk:* fewer homes. *Measure:* departure margins,
served.

**Not now:** the halo relaxation (D) changed nothing; re-forecasting every 2 h (D+R)
cost 5 homes in bc NW high. I-70 stays shut because the P10 is pessimistic, not because
of old black — a founder call, not an iteration.

**Suggested iterations.** 1: R1 + R2 + R4 (exact routes, ingress judged) — each
byte-identical when off, measurable now. 2: R3 + R4's egress guard + R5 on Beaver
Creek, measured with R1's path audit.
