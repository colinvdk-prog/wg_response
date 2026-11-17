# Live crew groundwork, in shadow (2026-11-17, SAFETY SEMANTICS 45)

Step 3 of the founder-approved crew-program review
(`scratch_review/crew_program_review_2026-11-11/ASSESSMENT.md`), the part that
needs no founder decision. Built on main `ba1f21a` (SEMANTICS 44). Every
behaviour change is behind a default-OFF knob or flag; with all of them off
the pinned goldens reproduce byte for byte. Everything is operator-facing:
nothing reaches a crew or a client, and no crew is moved.

## What was built

| # | Review finding | What it does | Arms it |
|---|---|---|---|
| 1 | F1 (S1): no ongoing safety for a working crew | `wgresponse.crewwatch`: every cycle, each crew whose last report says en route, deploying or egressing (or one gone silent in those states, for 12 h) is judged against every fire's eroded P10, on the dispatcher's own rasters (eroded once, handed to the planner, so plans are unchanged). It checks three things: the crew's **spot** against the 10a line, a working crew's **home** against its latest lawful departure (SEMANTICS 2), and its **way out** (a lawful egress leg to a safe zone now, by the planner's own law: `dispatch.egress_table`). Levels: clear < at risk < unjudged < must leave < no lawful way out. One operator event per crew per crossing, a note per crew above clear, and `/api/state crew_watch`. Fail closed: a silent crew, a coarse fix, no forecast, a spot no forecast covers, a failed fire nearby or an exception all give **unjudged**, never clear. | `cfg.crew_watch`, `watch --crew-watch` |
| 2 | F4: a second crew sent to a worked home | A crew report may carry an optional `client_id`. A home that a crew's fresh deploying report says it is working (its `client_id`, else the one enrolled home within `crew_work_radius_m`, 150 m plus the fix's accuracy) leaves the job list as `in_progress_by:<crew>`, with a note. When two or more homes are that close, nothing is held and a note asks for `client_id`. | `cfg.crew_hold_worked`, `watch --hold-worked-homes` |
| 3 | F5: October laws never armed in `watch` | Arms the simulator's values for 20a `corridor_cell_exact`, 26 `dispatch_keep_frozen`, 32 `plan_egress_time_deadline` and 38 `egress_dest_durable_h` = 8 h. **37 is left out**: it ramps 31's road egress margin and reads only road legs, and was measured to change no chord plan. **35 is left out**: under the founder's err-pessimistic rule. | `watch --crew-laws` |
| 4 | F3: one shared key, no per-crew identity | `WGRESP_CREW_ROSTER` names a JSON roster of `{crew_id, name, members, key_sha256}`. A report must name a rostered crew and carry that crew's own `X-Beacon-Key` (sha256, compared in constant time). The shared key and the ops key report for no crew. A roster that cannot be read stops `watch` at startup and refuses every report (503) while it stays broken. Roster crews are listed from the start with status unknown. No key is logged or echoed. | env `WGRESP_CREW_ROSTER` |
| 5 | F9: restart empties the crew tracker | The tracker's last report per crew is saved in the state file (additive key `crew_reports`, mirrored with it) and restored with its own time, so staleness still applies. Restore never raises. | `cfg.crew_state_persist`, `watch --persist-crews` |
| 6 | F7: console itinerary pane always empty live | `console/index.html` builds its routes from `d.plan` when `d.routes` is absent. It shows each crew's stops and margins, its egress zone, held homes under their crew, and the crew watch rows above them. | page change (no state change) |

## Lane C's probes, before and after

Before is the review's own output at `e43c24e`
(`scratch_review/crew_program_review_2026-11-11/C_liveops/*.out`); after is
`scratch_review/live_crew_groundwork/after_*.py` on this branch. Since
SEMANTICS 44 every report needs a key, so the after-scripts send one.

**P6b, the deploying overrun** (`after_deploying_overrun.py`):

```
before  t=+31min c1 P10 lead 24.2 min | plan crews [] | unserved [('c1', 'capacity')] | operator_events [] | notes mentioning G1 []
after   t=+31min c1 P10 lead 24.2 min | plan crews [] | unserved [('c1', 'in_progress_by:G1')] | operator_events [('crew_no_way_out', 'G1')]
        note: crew watch (SAFETY SEMANTICS 45): crew G1 (deploying at c1) NO LAWFUL WAY OUT: its spot is 17.9 min past
              the line (eroded P10 of inc-1 less the 30-min egress buffer); home c1's latest lawful departure passed
              5.8 min ago ...; no leg to a safe zone is lawful now: the last one, to Z1, closed 53.3 min ago — leave by
              the least exposed way, Z1
        t=+36, +41 min: the same row and note, no second event (same crossing)
```

**P4, ghost crew and spoofed "off duty"** (`after_probe_beacons.py`, roster armed, open demo):

```
before  ghost -> 200 ok; spoof G1 off_duty -> 200 ok; after ghost + spoof: {'X': [('c1', 7.0)]}
after   ghost X -> 401; spoof G1 off_duty with G2's key -> 401; tracker: [('G1', 'available'), ('G2', 'unknown')]
```

**P5, a second crew sent to the worked home** (no `client_id`; the hold infers c1 within 150 m):

```
before  cycle B (G1 reports deploying AT c1): {'G2': [('c1', 77.3)]} unserved=[]
after   cycle B (G1 reports deploying AT c1): {'G2': []} unserved=[('c1', 'in_progress_by:G1')]
```

**P6, the wind rises to 18 m/s while G1 works c1:**

```
before  t=+50..80min c1 P10 lead=59.1 min | op events: [] | crew notes: []
after   t=+50min ... watch G1: no_way_out (spot 20.69, home 29.14, way out -9.45) | op events: ['crew_no_way_out']
        t=+60..80min: same level, no new event
```

At a 59-minute P10 lead, the home's deadline still has 29 minutes and the
spot 21 minutes. But no chord to a safe zone clears the planner's 56-minute
chord margin, so by the planner's own law the crew should already have left.
The watch reports exactly that.

**The trace** (`after_trace_live_crew.py`, everything armed):

```
before  /api/state crews: [G1 only after its first beacon] | 'routes' key: False | restart: tracker crews []
after   /api/state before any report: crews [('G1', 'Engine 1', 'unknown'), ('G2', 'Engine 2', 'unknown')]
        G1 with G2's key / ghost X / the shared key -> 401 each
        cycle 3 (deploying at c1, wind 18): unserved [('c1', 'in_progress_by:G1')]; watch G1 no_way_out; event crew_no_way_out
        cycle 4 (G1 silent): the hold lapses (c1 capacity); watch keeps G1 at no_way_out, now also unjudged (silent)
        restart: tracker crews [('G1', 'deploying', 'c1'), ('G2', None, None)]; next cycle G1 unjudged (silent), one event
```

The console renders the plan as an itinerary. This is checked in a headless
browser by `tests/test_console_itinerary.py`.

## Item 3: what the October laws change on the test stacks

Measured by `tests/test_live_crew_laws.py` through the real monitor and planner:

- **Every plan stays lawful**, checked stop by stop on three worlds (the seeded stack, the off-domain stack under the fail-closed law, and a dense world of eight homes downwind of one fire with three crews and three zones). The checks are the ingress chord (cell-exact), the slack floor, and a lawful way out reached by the home's latest departure.
- **The seeded and off-domain stacks plan byte for byte the same.** Their stops leave hours before their deadlines, no chord clips a cell between samples, and no second fire re-keys a committed stop.
- **On the dense world, 38 moves a way out off a dead end.** Zone Z3 sits among the homes and its 10c line is inside 8 h. Without the laws G1's route ends at Z3; with them it ends at Z1. The same 7 homes are served.
- **37 changes nothing on chords:** the dense world's plans are identical with it armed (it and 31 read road legs only).

The live effect of 20a, 26 and 32 needs geometry these stacks lack. 32 needs a
departure within about 45 minutes of its deadline with a long egress. 20a needs
a chord that clips a burning cell between samples. 26 needs a second fire that
changes a committed stop's key. Each is pinned against `dispatch.plan` in its
own October test file.

## Tests

New: `tests/test_live_crew_watch.py` (30), `tests/test_live_crew_laws.py` (7),
`tests/test_crew_roster.py` (26) and `tests/test_console_itinerary.py` (3,
rendered in headless Chromium). The pinned pre-W1 goldens (cycles, API, state
file) reproduce with every knob off. Full suite on `f5f8b0a` (Python 3.13, one
process): 2,980 passed, 15 skipped (the existing environment-gated skips), 1
xfail, 0 failed.

## What remains for real crews

**Needs a founder decision:**

1. **Crew messaging** (SMS provider, `docs/OPEN_QUESTIONS.md` O-1). This covers itinerary, leave-now and withdraw messages, with acknowledgement. Today the watch's "must leave" and "no lawful way out" reach only the operator.
2. **Operator approve / hold** of a plan before anything goes to a crew: who approves, and what happens on no answer.
3. **Shifts and fatigue** policy (hours on, rest, hand-over).
4. **Gel inventory** per crew (supply levels as a planning constraint).
5. **Road routing for the live planner.** This is a large project: an OSM / Overture road graph per AOI, since Overpass has been dead since 12 Oct. Until then the live laws judge straight chords.
6. **Arming in production:** whether and when to run `--crew-watch --hold-worked-homes --crew-laws --persist-crews` on the live watcher (shadow first), and issuing crew keys and the roster to crews.
7. **The watch's ladder on chords.** The 56-minute chord margin closes a crew's way out before its spot crosses the 30-minute line, so "no lawful way out" (the most urgent level, as the review asked) usually comes first. The founder may want that reported as "past the planner's last lawful departure" rather than as the top alarm.
8. **`crew_work_radius_m`** (150 m): accept the inferred home for phones that send no `client_id`, or require `client_id`.
9. **A "job done / aborted" report** (part of the acknowledgement protocol), so that a home a crew finished is not offered again. There is no coating record on the live path.

**Decision-free, not done here:**

- Idle (available) crews are not watched. SEMANTICS 10a says every crew, but the review scoped the watch to crews in the field.
- The test map (`/api/test/state`) does not show the crew watch.
- The watch's per-crew level memory is in-process, so a restart re-raises one event per crew.
- A crew-app spec for the `client_id` field.
- Cost. When crews are in the field the watch erodes every fire's P10 once a
  cycle, and the planner reuses that erosion. If no crew is dispatch-eligible
  (all deploying or egressing), that erosion is new work. On the national AOI
  it took 393 s for 27 domains on 2026-10-30. Every fire is kept on purpose: a
  far fire's fail-closed bound can still decide a leg (the Willow case). Run
  the watch on a regional AOI.
