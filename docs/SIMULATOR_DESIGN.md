# WG Response — Interactive Simulator UI (design of record)

**Requested 2026-07-09 (founder):** a simulator UI to *watch the model's functionality*:
five real locations; user sets client count (auto-placed in WUI areas), response-team count,
fire location + intensity; engine demonstrates propagation + crew deployment with crews
leaving every location at a user-selected minimum time before fire arrival — the slider is
the tool for exploring safety options and thresholds.

## Product shape

`py -3 -m wgresponse.cli simui` → browser page (Leaflet, same offline-shim discipline as the
ops console) at `/simulator`. Runs the REAL engine — `ensemble_arrival`, alert ladder,
`dispatch.plan`, corridor/egress safety — in an accelerated background loop; nothing is
canned. The UI is a demonstration instrument, not a toy renderer.

## The five real locations (real terrain, real WUI)

Chosen for known WUI exposure + business relevance (PD Mountain West = CO/UT footprint):

| # | Location | Center (lat, lon) | Why |
|---|---|---|---|
| 1 | Malibu / Santa Monica Mtns, CA | 34.055, -118.72 | Chaparral canyon WUI, Santa Ana alignment |
| 2 | Paradise / Magalia, CA | 39.775, -121.60 | Camp Fire geography (timber-litter + foothill towns) |
| 3 | Santa Rosa NE / Mark West, CA | 38.50, -122.65 | Tubbs corridor (wind-driven grass/oak) |
| 4 | Boulder / Superior, CO | 39.955, -105.16 | Marshall Fire grass-WUI; PD Mountain West |
| 5 | Park City / Summit Park, UT | 40.72, -111.55 | Gambel-oak/aspen mountain WUI; PD Mountain West |

Terrain: one-time LFPS pull per location (~18×18 km @ 90 m), cached into `data/tiles/*.wgt`
(the dep-free ASCII tile format) and committed — the simulator then runs **offline and
dep-free**. If a tile is missing at runtime: loud banner + synthetic fallback (never silent).

## User controls (left panel)

- **Location** — the five presets (map recenters; tile loads).
- **Clients (N)** — slider/input 1–200. Placement: seeded-random over **WUI cells** derived
  from the real fuel grid: non-burnable developed cells (FBFM40 91/93) within 3 cells of
  burnable fuel, plus burnable cells within 1 cell of developed — the actual
  interface/intermix band. Re-roll button (new seed). Client values drawn from a seeded
  lognormal ($600k–$5M) so dispatch triage is visible.
- **Response teams (M)** — 1–12. Staged at real safe-zone anchor points (town centers /
  highway nodes per location); safe zones marked on the map.
- **Crew placement** (founder 2026-09-26: *"allow the crews to be placed on the map"*) —
  a **setup-only** control in the Response crews panel, before the first ignition.
  *Place crews* turns the map click into a placement — it never lights a fire (arming an
  intensity leaves the mode, entering it disarms the intensity; its own cursor and teal
  hint): each click puts the next in-zone crew (1, 2, … cycling; click a crew or its row to
  choose) on the **road-graph node nearest the click**, and in-zone crew markers become
  draggable; *Reset to safe zones* undoes every placement. **Refusals** (the engine's
  message, shown verbatim; `POST /api/sim/<id>/crew` → 400): a click more than **2 km from
  every road node** (the message carries the measured distance — a truck starts on a
  road; the whole-valley `eagle_vail_co` tile has wilderness where this happens), a point
  off the tile (or whose nearest road node is), an unknown crew, and an out-of-region
  **reinforcement** (it arrives at its staging zone). With no road graph loaded the raw
  point is used and said loudly (placement note + `state.notes`). A placement moves the
  crew's **position + beacon only**: `home_base` and the egress anchor stay its round-robin
  safe zone — an escape target is never an arbitrary clicked point — and the SEM-10 sweep
  governs a placed crew exactly like any parked crew. Once a fire is lit, placement is
  refused (409, *"crews are now dispatched by the engine — reset the scenario to
  re-stage"*) and the page disables the mode with that tooltip. **Determinism:**
  `place_crew` / `reset_crews` land in `cmd_log`; placements survive **Reset** and the
  client **re-roll** (the same staging replays; the re-rolled session's own `cmd_log`
  re-issues them), a replay of the command script reproduces the timeline byte for byte,
  and a session with no placement is byte-identical to before (the one additive state key,
  `crew_placement`, stripped: per-tick hashes of eagle_vail_co / boulder_co / malibu_ca
  scripted runs matched the pre-feature engine exactly). Changing location, crew count,
  fire zone or a physics toggle builds a new session with no placements, and the page
  says so. **Safety, measured.** On the controlled grass test tile
  (tests/test_sim_crew_placement.py) a crew placed on the road 3 km downwind of where a
  HIGH fire is then lit is relocated by the SEM-10 sweep at the first tick: 0 departure
  violations, 0 crew overruns, the crew > 2 km clear; a fire lit ON a placed crew is
  reported by the 10c audit on every tick the crew's own cell is burned or imminent
  (asserted row for row) while the sweep drives it out. On the real eagle_vail_co timber
  through the page's defaults (spotting on, HIGH, wind from 240, ignition WSW of
  Edwards; scratch_review/crew_placement_path_probe.py), a crew placed
  ≤ ~2 km downwind is already inside the pessimistic front at the first audit — the
  backstop REPORTS it for that one tick (red "crew overrun" banner, 1 — UNSAFE) while
  the sweep turns it out, and it is clear with violations 0 from tick 2 (departure
  violations 0 throughout); placed ≥ ~3.5 km out, dispatch takes it out of the path with
  0 overruns. Placement cannot hide danger: it only chooses where an idle crew waits,
  and the audit judges it exactly like every other crew. (The tick-1 report is the tick
  order at work — the sweep runs after each tick's movement, so no crew anywhere can
  leave a cell the first tick's front reaches before that tick's audit; a pre-movement
  sweep on the ignition tick would change every session's timeline and is left as a
  decision.) Frames, eagle_vail_co, 40 homes, 4 in-zone + 1 +1 h crew, seed
  `placement-demo` (`docs/img/crew_placement/`): `01_refused_off_road` (three crews put
  on roads by map clicks, a click on roadless ski terrain refused), `02_setup_all_placed`
  (the fourth crew dragged; hover text), `03_dispatched_from_placed_starts` (tick 2:
  dispatched from the placed starts, placement locked), `04_fire_path_tick1_reported` and
  `05_fire_path_tick3_relocated` (a crew placed ~2 km downwind: reported, then out).
  **Marker position fix (found here):** `.crew-icon` / `.fire-origin` carried
  `position: relative`, overriding Leaflet's absolutely positioned marker icon, so each
  icon also took normal-flow space in the marker pane — crew N was drawn 10·(N−1) px
  south of its true position and a fire origin lit after five crews ~50 px south of the
  click (on the page since the initial commit). Both are absolutely positioned again
  (measured: icon centres now match `latLngToContainerPoint` to the pixel).
- **Fire** — click map to ignite (or drag existing); **intensity** preset mapping to weather:
  Low (8 mph, RH 35%), Moderate (18 mph, RH 22%), High (28 mph, RH 12%), Extreme
  (40 mph gusting, RH 6%) + wind direction dial (default = location's canonical hazard wind:
  Santa Ana E-NE, Diablo NE, Marshall W, etc.). Multiple simultaneous fires allowed.
- **THE SAFETY SLIDER — "minimum clear-by" (`egress_buffer_min`)**: 10–120 min, default 30.
  Applied live to `ResponseConfig.egress_buffer_min` (the SAFETY SEMANTICS 2 deadline). A
  second optional slider exposes `slack_min` (0–60). Changing either triggers an immediate
  re-plan so thresholds can be compared side-by-side.
- **Clock** — run/pause, speed 1×–600×, step one cycle, reset scenario. The 600× ceiling
  is enforced at BOTH seams (SimSession.set_speed and the server /api/sim config path), so
  no command script can request coarser multi-cycle jumps than the design allows.

## What the map shows (the functionality being demonstrated)

- Fire perimeter growth (burned mask) + P50 isochrone rings (6/12/24/48/72 h) and the eroded
  P10 corridor field toggle — the actual safety surface.
- Clients colored by live alert state (NONE→NOTICE→WATCH→EXTREME→IMPACTED/CLEARED ladder,
  real AlertEngine with hysteresis).
- Crews moving along planned routes (real `dispatch.plan` output, commitment horizon honored),
  stop cards showing arrive/service/depart vs `latest_depart`, and the **margin** at each stop.
- Egress legs to safe zones drawn when crews withdraw; corridor-blocked routes shown red with
  the unserved reason.
- Right panel: alert feed (real AlertEvents), per-crew itinerary with margins, and a **safety
  scoreboard**: served / unserved (by reason) / withdrawn / min margin observed / any violation
  (must stay 0) — recomputed per slider change. What the slider demonstrably moves is pinned
  in "Slider observability" below: start-of-run buffer sweeps move the unserved REASONS and
  the departure deadlines (not the served count — the corridor law dominates there), while a
  LIVE mid-run tightening moves the headline served count itself by withdrawing in-flight
  work — the threshold-exploration the request asks for, told honestly.

## Slider observability — what the buffer demonstrably changes (measured 2026-07-10)

Verifier sweeps (scratch_review/attack_sim_slider.py + attack_sim_probe_*.py) established
that the START-OF-RUN served count is buffer-insensitive in every constructible scenario: a
10-point sweep 10→120 min on the real santa_rosa_ca tile (60 clients / 1 crew / extreme,
36 ticks) and probes across n∈{16..200}, 1–2 crews, near/far ignitions, plus the suite's own
synthetic checkerboard, all leave the count flat. That is engine physics, not a defect:

- **The corridor law dominates the low range.** Every serviced stop must clear its egress
  corridor against the ERODED P10 (500 m erosion, route_margin_min 60): at the stop itself
  that already forces departure ≥ ~60 min + the erosion gap before local P10 arrival, so
  buffer values ≲ 60–90 min can never become the binding constraint.
- **Early adoption gives hours of margin.** Jobs are adopted as soon as P50 ≤ notice_h (72 h)
  and serviced immediately — measured minimum audited service margins are tens of hours on
  the real tiles (19.2–24.2 h on the sweep scenario itself; 26.5–60.2 h across the verifier's
  wider probe matrix); a late-notice probe (notice_h = 2) serves 0 outright because the
  corridor blocks late dispatch. The 10–120 min slider range almost never intersects the
  feasibility frontier of a from-scratch plan.

What the slider DOES visibly demonstrate (all pinned in tests):

1. **Unserved-reason escalation** — the marginal client's reason moves from
   no_safe_corridor / infeasible_slack at low buffers to infeasible_deadline at high ones:
   the operative safety constraint changes name on the scoreboard
   (tests/test_simui.py::test_buffer_binds_unserved_reasons).
2. **Deadline tightening** — every re-planned stop's latest_depart moves earlier
   minute-for-minute with the slider, visible on the stop cards within one tick
   (tests/test_simui.py::test_buffer_change_triggers_replan_next_tick).
3. **Live withdrawals move the headline number** — tightening MID-RUN re-checks in-flight
   work: a crew mid-deploy whose projected departure violates the new rule withdraws on the
   spot (gel unfinished, immediate egress, loud `withdrawn` record). With the honest
   anchored arrival fields (2026-07-10) a from-scratch plan never parks a deploy near the
   120-min slider ceiling — measured p10 gaps at adoption are 400–900+ min under the
   corridor law — so the demo tightens the WORLD mid-deploy instead of scripting a doomed
   stop: on the pinned checkerboard (16 clients / 1 crew / high fire 4.6 km upwind, seed
   m1) the first deploy arms at tick 2; a SECOND IGNITION 1250 m dead-upwind of the
   deploying client, with the domain wind swung onto it (ignite's documented shared-weather
   semantics — every fire's field re-anchors next tick), collapses that client's fresh p10
   gap to 43.3 min worst-case (77.6 min on the stop's own fire): inside the 10-min rule,
   outside 120. Raising 10→120 min then withdraws the in-flight stop (deficit 42.4 min);
   the control twin — identical commands minus the tightening — legally completes it under
   the adoption-time rule and serves 3 by tick 60 with withdrawn=0, while the tightened run
   serves 0 with withdrawn=1; violations 0 in both
   (tests/test_simui_policy.py::test_live_tightening_reduces_served_vs_control).

The founder demo script therefore is: run at a low buffer, let a crew start a roof, tighten
the world (drop a spot-fire ignition upwind of the property being serviced), then RAISE the
slider — watch the crew abort, egress, and the scoreboard trade served for withdrawn, with
the audit green the whole time.

## Live policy changes vs in-flight work (the safety law)

Moving either slider (buffer or slack) revokes every not-yet-started stop (next tick
re-plans under the new rule) and re-checks any IN-PROGRESS deploy — never grandfathered
(2026-07-09 verifier blocking finding, resolved toward safety):

- the stop's audit rule only ever TIGHTENS (max of adoption-time and new rule; a live
  loosening never relaxes work already in flight);
- a projected departure violating the effective rule ⇒ the crew WITHDRAWS immediately: the
  deploy aborts unfinished (no ServicedStop), the crew turns straight onto its planned
  egress leg, and the event lands in state()['withdrawn'], the scoreboard `withdrawn`
  count, unserved reason `withdrawn_unsafe`, and a session note;
- a KEPT tightened stop is flagged `policy_superseded` on the itinerary and re-checked
  EVERY tick against the newest P10 (cause `eta_drift`), so a forecast update can never let
  it complete past the new rule;
- COMPLETED stops keep their adoption-time rule — history is not retroactively re-labeled —
  and every audit row carries rule_buffer_min / rule_ember_min so the grandfather is
  visible, never silent.

## Engine integration (no physics forks)

New module `wgresponse/simui.py`: `SimSession` holds (location tile, clients, crews, fires,
cfg overrides, sim clock); each accelerated tick runs the same cycle path as `sim.run`
(ensemble → ThreatAssessments → AlertEngine.evaluate → dispatch.plan → kinematic crew
movement → SAFETY AUDIT identical to `sim.py`'s). Server: `/api/sim/*` endpoints
(new-session / configure / ignite / tick-state stream via poll) mounted in `server.py`;
`cli simui` = `serve` + auto-open instructions. All seeded → any run is reproducible from
its seed line (shown in the UI for bug reports).

## Acceptance

1. All five locations load real cached tiles (fuel histogram sanity per location pinned in
   tests); WUI placement puts ≥95% of clients within the interface band (test).
2. With defaults (30-min buffer): every serviced stop departs ≥ buffer before the local P10
   arrival — the sim SAFETY AUDIT (violations == 0) runs every tick and renders on the
   scoreboard.
3. Raising the buffer strictly never increases served count (monotonic safety trade-off,
   property test across seeds); the scoreboard reflects re-plans within one tick; the
   slider's bind is demonstrated NON-vacuously — unserved-reason escalation to
   infeasible_deadline, latest-depart tightening, and the live mid-deploy withdrawal
   served-count demo ("Slider observability" above; tests/test_simui_policy.py).
4. Deterministic: same seed + same click script ⇒ identical timeline (test via scripted
   session).
5. Suite stays green; simulator adds its own tests (placement, session API, audit wiring,
   determinism).

## Tile provenance

**Fetched 2026-07-10T05:12Z (2026-07-09 PDT)** by `py -3 tools/fetch_sim_tiles.py` — live
LANDFIRE **LFPS** job API (`lfps.usgs.gov`, keyless GET; `Email` = env `LFPS_EMAIL` else
`ops@thewildfireguardian.com`), layers **`LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev`**
(`Output_Projection=4326`), 30 m native → nearest-neighbor 90 m by the shipped
`wgresponse.terrain.LfpsTerrain` adapter, saved dep-free via `terrain.save_ascii_tile`.
**All five jobs succeeded** (first attempt, 155–447 s each); no failures to record, so no
location relies on the missing-tile fallback today. Every tile: 200×200 @ 90 m (~18×18 km),
bands FUEL/SLOPE_DEG/ASPECT_DEG/ELEV_M. Machine-readable copy: `data/tiles/provenance.json`;
offline sanity pins: `tests/test_sim_tiles.py`; raw job zips cached in `out/lfps_sim_cache/`
(a decode failure never re-burns a completed LFPS job). Refetch: `--force`; one location:
`--only <key>`.

| key | bbox W, S, E, N (deg) | bytes | burnable | dev 91/93 | water 98 | top fuels (% of cells) | WUI dev cells (r≤3) | elev m | slope max |
|---|---|---|---|---|---|---|---|---|---|
| malibu_ca | -118.817584, 33.974152, -118.622416, 34.135848 | 849,857 | 57.1% | 9.3% | 30.5% | SH2 16.8, GS2 14.3, SH5 10.1 | 3,686 | 0*–853 | 56° |
| paradise_ca | -121.705194, 39.694152, -121.494806, 39.855848 | 891,227 | 88.6% | 10.1% | 0.7% | GR2 17.9, GS2 12.5, TL4 9.9 | 4,052 | 101–1,235 | 55° |
| santa_rosa_ca | -122.753306, 38.419152, -122.546694, 38.580848 | 865,922 | 72.0% | 27.6% | 0.3% | GR2 12.4, TU2 9.2, TL5 8.3 | 9,870 | 32–829 | 48° |
| boulder_co | -105.265470, 39.874152, -105.054530, 40.035848 | 874,533 | 60.6% | 36.6% | 2.3% | GR2 43.6, GR1 4.8, GS2 3.1 | 14,143 | 1,547–1,974 | 26° |
| park_city_ut | -111.656673, 40.639152, -111.443327, 40.800848 | 924,812 | 88.8% | 10.6% | 0.1% | SH7 21.4, GS2 19.1, TU5 10.1 | 4,239 | 1,844–3,034 | 49° |

\* malibu_ca only: 1,181 cells (2.95%) of far-offshore Pacific fringe are LANDFIRE nodata
(−9999 in FUEL and ELEV_M) — the bbox leaves the LANDFIRE mapping zone there. Fail-safe by
construction: `fuels.is_burnable` treats −9999 as non-burnable, and `tests/test_sim_tiles.py`
bounds the nodata fraction (≤35%) while rejecting any other out-of-namespace code. The other
four tiles contain FBFM40+NB codes only. Histograms are LF2024-plausible per location
(Malibu chaparral SH/GS, Paradise foothill timber-litter/grass, Tubbs-corridor grass/oak +
Santa Rosa urban edge, Marshall grass + suburbs, Park City Gambel-oak SH7/aspen-conifer);
ticket SIM-1's optional park_city developed-fraction exemption proved unnecessary (10.6%
measured, 10× the 1% floor). If a tile is deleted, its tests SKIP with a loud refetch
instruction and the UI must show the missing-tile banner + synthetic fallback (never silent).

## Simulator (SIM-1..3) results

**Gate run 2026-07-10** (Windows 11, Python 3.14.5, pytest 9.1.1; everything from the
repo root). SIM-1 = tiles + WUI placement, SIM-2 = SimSession policy engine, SIM-3 =
server/CLI/UI. Founder entry point: `py -3 -m wgresponse.cli simui --open`.

### Suite

`py -3 -m pytest` → **913 passed, 0 failed, 0 skipped** in ~31 s (Sprint-2 baseline 822
+ 91 simulator tests). All Sprint-2 pins (byte-identity, outage drills, attack suites)
stayed green; `filterwarnings = error::RuntimeWarning` in force.

### Tile provenance recap

Every demo below ran on the REAL committed LFPS tiles (`data/tiles/<key>.wgt`, source
`lfps:LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev`, 200×200 @ 90 m, fetched
2026-07-10 — provenance table above): `state.terrain.synthetic == false` on all five
locations, so **no synthetic fallback was exercised anywhere in this gate** (the
fallback path stays covered by tests/test_simui.py on controlled tmp tile dirs).

### Five-location scripted demo

Per location: 40 clients (seeded WUI placement, seed `demo`), 4 crews, one **high** fire
(28 mph / RH 12%) ignited **6 km upwind** of the location center along the canonical
hazard wind (deterministic burnable-cell scan; boulder_co needed a 0.5 km lateral nudge
off non-burnable suburb cells), buffer 30 min (shipped default), **30 ticks**. One tick
= one 900 s replan cycle — the server's run path holds the engine step at 1 cycle/tick
and its default **300×** only compresses wall time (3 s/tick), so this scripted run is
the same run the founder watches at 300×; 30 ticks = **7.5 sim hours**.

| location | served | unserved by reason | min margin (h) | violations | first NOTICE | first EXTREME | wall, 30 ticks |
|---|---|---|---|---|---|---|---|
| malibu_ca | **13** | in_progress ×1 | 9.12 | **0** | tick 1 | tick 1 | 20.7 s |
| paradise_ca | **1** | — | 35.25 | **0** | tick 1 | — | 14.9 s |
| santa_rosa_ca | **0** | in_progress ×2 | n/a (none complete yet) | **0** | tick 1 | — | 12.5 s |
| boulder_co | **2** | — | 16.83 | **0** | tick 1 | tick 1 | 9.6 s |
| park_city_ut | **3** | — | 17.68 | **0** | tick 1 | — | 13.0 s |

Ignition points (all `intensity=high`, wind = canonical hazard wind): malibu
34.08967,-118.67009 (SH5 145); paradise 39.81314,-121.55033 (TU5 165); santa_rosa
38.53814,-122.60122 (TL8 188); boulder 39.95048,-105.23039 (GR1 101); park_city
40.68183,-111.60031 (GS2 122). "—" = did not occur within the 30-tick window.

Reading it honestly: NOTICE fires at tick 1 everywhere because the ladder evaluates the
FORECAST — the first post-ignition evaluation already puts nearby clients inside P50 ≤
72 h. EXTREME at tick 1 only where that first forecast is inside P50 ≤ 24 h / P10 ≤
12 h (fast chaparral/grass: malibu, boulder); the slower timber/oak tiles (paradise,
santa_rosa, park_city) produced **no** EXTREME within the 7.5 h slice. Served counts are
the 7.5-hour completion slice, not a campaign total (santa_rosa's only 2 threatened
clients became jobs late, as the growing perimeter pulled their P50 inside 72 h; both
were mid-flight at tick 30). Alert-event volumes: 20/2/4/4/6. Mean engine cost
0.32–0.69 s/tick at default config — comfortably under the 3 s/tick the default 300×
playback needs, i.e. the UI keeps real-time at 300× on this hardware.

### Malibu buffer trade-off (the threshold-explorer story)

Identical scenario, `egress_buffer_min` set at session start to 30 / 60 / 90:

| buffer (min) | served | withdrawn | min audited margin (h) | unserved by reason | violations |
|---|---|---|---|---|---|
| 30 | 13 | 0 | 9.12 | in_progress ×1 | 0 |
| 60 | 13 | 0 | 8.62 | in_progress ×1 | 0 |
| 90 | 13 | 0 | 8.12 | in_progress ×1 | 0 |

Exactly the pinned "Slider observability" physics: a START-of-run buffer change does not
move the served count (corridor law + early adoption dominate; margins are hours) — what
it demonstrably moves is every deadline and the audited margin, **minute-for-minute**
(−0.5 h per +30 min: 9.12 → 8.62 → 8.12), with the audit green throughout. The demo that
moves the headline served number is the LIVE mid-deploy tightening (control 3 vs tightened
0 served, withdrawn=1, after a second-ignition p10 collapse on the pinned checkerboard;
tests/test_simui_policy.py): tighten the world while a crew is on a roof, then raise the
slider.

### Smoke + console-JSON contract (SIM-3)

- `py -3 -m wgresponse.cli simui --smoke` → **exit 0** (boulder_co real tile, ignite, 3
  engine ticks over the live HTTP API, scoreboard printed, violations 0; DEV-OPEN
  posture announced loudly; all five tiles reported cached).
- Server booted once via `py -3 -m wgresponse.cli simui --port 8878`; the Malibu demo
  scenario created over HTTP (`POST /api/sim/new` → ignite → `ctl run`, default 300×,
  tick_interval 3 s); `GET /api/sim/<id>/state` polled at the UI's 1 s cadence across
  ticks 1–3 plus one `?safety=1` poll. **Every field console/simulator.html reads was
  present on every poll**: top-level (session_id, running, seed, speed_x, wind_dir_deg,
  location, location_name, sim_now_s, tick), scoreboard (served, unserved_by_reason,
  min_margin_h, violations, buffer_min, slack_min, withdrawn), terrain (+ bbox + rle01
  burned mask), fires, isochrones (5 rings), clients, safe_zones, crews (+ track points
  + 13 itinerary stop cards incl. policy_superseded), alert_feed (13→19 events),
  serviced (1 completed stop by tick 3), and safety_surface (5 eroded-P10 rings) — all
  strict JSON (no bare NaN/Infinity), violations 0 on every poll, clean pause after.

### Known limitations (as measured)

1. **Developed-cell clients never alert or dispatch.** 19/15/22/31/13 of the 40 placed
   clients (malibu/paradise/santa_rosa/boulder/park_city) sit on FBFM40 91/93 cells; the
   surface model treats NB fuel as a break (ember and structure-to-structure spread are
   not modeled). The per-session note in `state.notes` says so loudly; the WUI band
   deliberately includes the developed side of the interface.
2. **Slice, not campaign:** 30 ticks = 7.5 h; served counts rise with longer runs
   (santa_rosa 0 served / 2 in flight at tick 30 is the honest slice reading).
3. **EXTREME at tick 1 is correct, not a bug** — the ladder evaluates the forecast, so a
   client whose first P50/P10 already sits inside 24 h/12 h alerts immediately; there is
   no artificial ramp.
4. **Start-of-run buffer sweeps leave served flat** (re-measured above); the honest
   slider demos are unserved-reason escalation, deadline tightening, and the live
   mid-run withdrawal — all pinned in tests.
5. **Determinism caveat** (inherited from dispatch.plan): bytes guaranteed while the
   solver finishes inside `cfg.solver_budget_s` — always true at demo scale.
6. `min_margin_h` stays null until the first stop completes (it is the audited
   serviced-stop margin, not a plan projection).
7. Wall-clock figures are this machine's; per-tick cost grows with burned-perimeter size
   (malibu mean 0.69 s/tick vs boulder_co 0.32 s/tick over the 30-tick window).

Repro: a SimSession given (seed, command script) is byte-reproducible (design rule 3);
the demos above are seed `demo` with the exact parameters listed, driven through the
public SimSession seam (configure → ignite → tick×30).

## Fire zone

**Requested 2026-07-10 (founder):** *"allow the fire zone to be increased so ignition can be
further from the clients homes"* — so the full 72 h NOTICE → WATCH → 24 h EXTREME arc plays
out instead of a fire that is already on the doorstep at ignition. Law of record:
`wgresponse/contracts.py`, FIRE ZONE block under the `wgresponse.simui` seam.

- **Two zones per session** — `fire_zone='standard'` (default; the ~18×18 km tile
  `data/tiles/<key>.wgt`) or `'extended'` (the wide-area `data/tiles/<key>_ext.wgt`,
  ~48×48 km @ 240 m as fetched 2026-07-10 by `py -3 tools/fetch_sim_tiles.py --extended`;
  provenance: `data/tiles/provenance_ext.json`). Constructor/`configure()` parameter,
  serialized into the repro `cmd_log` (`configure.fire_zone`), accepted by
  `POST /api/sim/new` (unknown value → 400) and selectable in the UI next to the location
  dropdown (changing it re-creates the session, same UX as a location change; a small
  teal chip marks an active extended zone; the copy-repro line carries `fire_zone=`).
- **Clients always live in the community** — WUI placement on extended tiles is restricted
  to the CENTRAL window covering the standard tile's footprint
  (`simui.STANDARD_ZONE_HALF_M` = 9 km around the location center, computed from
  coordinates via `central_window_bbox`, never hardcoded grid indices — max client
  distance from center ≈ 12.7 km at the window corner). Ignition may be anywhere on the
  loaded tile; safe zones and crew staging are unchanged (lat/lon anchors).
- **Horizon floor** — extended sessions default `cfg.horizon_h` to
  `max(horizon_h, 120.0)` so a fire ignited tens of km out stays inside the forecast;
  an explicit `horizon_h` in `cfg_overrides` ALWAYS wins (documented precedence on
  `SimSession.configure`).
- **Missing `_ext` tile** — loud `state.notes` entry + fallback to the standard tile;
  `state.fire_zone` always reflects what ACTUALLY loaded (the UI toasts the note and
  re-syncs the selector). There is deliberately no synthetic extended fallback.
- **Roads** — until 2026-09-25 an extended session routed on the STANDARD ~18 km
  graph, so a crew fleeing past the core (the SEM-10d area escape) drove a whole-leg
  straight chord — up to 11 km across the Gore Range on extended vail_co (roads
  inspection, `docs/review/roads_inspection_2026-09-25.md`). Extended sessions now
  load `data/roads/<key>_ext.wgr` over the whole extended tile
  (`py -3 tools/fetch_sim_roads.py --extended`; `data/roads/provenance_ext.json`;
  two-tier — every class in the standard bbox, motorway..tertiary + unclassified
  outside — for the urban `boulder_co`, `malibu_ca`, `santa_rosa_ca`, full elsewhere)
  and fall back LOUDLY to the standard graph when it is missing; standard sessions
  never load it. A leg endpoint outside graph coverage still falls back per the
  `wgresponse.roads` seam: straight tier-0 leg, `on_roads=False`, never silent.
- **Map** — the UI fits bounds to `state.terrain.bbox` per session, so an extended
  session automatically frames the wide tile.

Tests (`tests/test_simui.py`, `tests/test_server.py`): controlled-tile window placement
(far-hamlet WUI cells excluded, all clients ≤ 13 km), loud missing-`_ext` fallback,
horizon default + override precedence, ~20 km ignition rejected-on-standard /
accepted-on-extended, byte-identical replay of an extended session, per-location real
`_ext` tile integration (loud per-tile skip when a tile is absent; bbox ~2–3× the
standard tile, bands present, violations 0), and the `/api/sim/new` fire_zone
happy + 400 paths (re-roll preserves the zone).

Ring quality note (same UI pass, 2026-07-10): `simui._threshold_rings` now orders every
burned/band ring by TRUE BOUNDARY FOLLOWING (clockwise Moore neighbor-trace per
8-connected boundary component, start at the westmost-then-northmost cell) instead of
the old centroid-angle sort, which starburst-zigzagged on concave fronts (the h5 band
finding). Shape contract unchanged (closed `[lat, lon]` rings, ≤ 240 pts). Pinned by a
synthetic crescent test: no self-crossing segments, consecutive points within 2 cell
diagonals. Idle crews now emit `route_on_roads`/`egress_on_roads` = `null` when no
polyline exists — `false` is reserved for a real straight-line leg (the UI's honesty
chip trigger).

## Vail → Aspen corridor presets (2026-09-23)

Founder request 2026-09-23: *"build the response program from Vail to Aspen"*
(program of record: `docs/vail_aspen_response_program.md`). Six community
tiles along the PD Mountain Resorts corridor — the I-70 Eagle Valley and the
SH-82 Roaring Fork Valley, served from the Eagle, CO forward base — were added
to `simui.LOCATIONS`, `tools/fetch_sim_tiles.py` (kept in sync;
`tests/test_corridor_vail_aspen.py`) and the simulator's location list. Same
tile law as the five originals (standard ~18×18 km @ 90 m, extended ~48×48 km
@ 240 m), same OSM road law (`tools/fetch_sim_roads.py`), no city-limits
polygon (the mountain WUI band *is* the towns). Since the roads inspection
(2026-09-25) the six seed homes only on WUI cells within 300 m of a road node
(`SimLocation.place_near_road_m = 300`, the program's headline `_road`
placement): on the whole band a quarter of Vail's homes were ski-mountain
'developed' cells with no road, and crews drove straight spurs of up to 6.4 km
up the mountain to them.

| key | community | center (lat, lon) | hazard wind FROM | analog events (geography + wind, [ASSUMED]) | safe zones (PLANNING-GRADE, not field-verified) |
|---|---|---|---|---|---|
| `vail_co` | Vail / East Vail / Minturn | 39.635, −106.360 | 240° WSW | prefrontal SW-WSW channelled along Gore Creek; no modern town-threatening run on record | I-70 exit 180 East Vail · I-70 exit 171 Dowd Junction · Minturn US-24 |
| `beaver_creek_co` | Beaver Creek / Avon / Edwards | 39.625, −106.560 | 240° | Sylvan 2021 (south of Eagle) as the valley's recent WSW-driven event | I-70 exit 167 Avon · I-70 exit 163 Edwards · I-70 exit 169 Eagle-Vail |
| `eagle_co` | Eagle / Gypsum (corridor base) | 39.650, −106.890 | 240° | Sylvan Fire 2021 | I-70 exit 147 Eagle (base) · I-70 exit 140 Gypsum / airport · Brush Creek Rd / Eagle Ranch |
| `glenwood_springs_co` | Glenwood Springs | 39.545, −107.320 | 240° | Storm King 1994, Coal Seam 2002 (29 homes, West Glenwood), Grizzly Creek 2020 (I-70 closed two weeks) | I-70 exit 114 West Glenwood · I-70 exit 119 No Name · SH-82 / Cattle Creek |
| `basalt_carbondale_co` | Basalt / El Jebel / Carbondale | 39.395, −107.125 | 225° SW | Lake Christine 2018 (El Jebel, 3 homes) | SH-82 / SH-133 Carbondale · SH-82 / Basalt Ave · SH-82 / Willits Ln |
| `aspen_co` | Aspen / Snowmass Village | 39.210, −106.870 | 225° | SW synoptic flow across the Roaring Fork; no modern town-threatening run on record | SH-82 / Brush Creek intercept lot · Aspen Main St / Mill St · Snowmass Village Town Park |

Hazard-wind rationale: the western-slope fire wind is the dry-cold-front /
prefrontal SW–WSW flow behind every analog above; the E–W Eagle Valley tiles
use 240 (channelled along the valley), the NW–SE Roaring Fork tiles 225. The
scenario suites rotate the axis ±30/±40°, so each community is scored across
the whole approach arc. Safe zones are highway interchanges, SH-82 junctions
and park-and-rides 3–13 km from each center (the registry-shape pin);
`HOA_40_PLAYBOOK.md` ground-verification is a hard gate before any dispatch.

Corridor drive-time graph: `data/roads/corridor_vail_aspen.wgr` — OSM major
roads (motorway..tertiary + links) over the corridor bbox
(W −107.45, S 39.05, E −106.20, N 39.80), 993 nodes / 1,442 edges, LCC 99.6%,
built by `scratch_review/corridor_roads.py`; the drive-time matrix (free-flow,
fire-traffic, Glenwood-Canyon-closed and all-season variants) lives in
`scratch_review/corridor_drive_times.json`. Watcher AOI for the corridor:
`data/aoi_vail_aspen.json` (one bbox + the 18 zones, all labelled UNVERIFIED).

### Corridor tile provenance

**Standard tiles fetched 2026-09-24 03:51–04:30Z (2026-09-23 PDT evening)** by
`py -3 tools/fetch_sim_tiles.py --only <key> …` — six LFPS jobs, 369–415 s
each, all first attempt; layers `LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev`
@ 90 m, 200×200; machine-readable record `data/tiles/provenance.json`; pins
`tests/test_sim_tiles.py` (all green).

| key | bytes | burnable | dev 91/93 | water | WUI dev cells (r≤3) | elev m | slope max | top fuels (% of cells) |
|---|---|---|---|---|---|---|---|---|
| vail_co | 937,576 | 89.1% | 4.0% | 0.2% | 1,608 | 2,324–3,986 | 64° | TU5 27.5, TU1 22.4, TL5 11.1 |
| beaver_creek_co | 934,823 | 92.2% | 6.6% | 0.3% | 2,649 | 2,124–3,704 | 56° | TU1 27.6, TU5 23.3, TL3 6.8 |
| eagle_co | 924,018 | 90.8% | 7.5% | 0.4% | 2,985 | 1,893–3,003 | 47° | TL3 27.0, GS2 17.5, SH2 10.6 |
| glenwood_springs_co | 936,163 | 93.9% | 4.8% | 0.5% | 1,909 | 1,719–3,192 | 72° | TU1 21.8, SH4 21.3, TL3 11.9 |
| basalt_carbondale_co | 919,815 | 87.3% | 11.6% | 0.9% | 4,619 | 1,854–3,216 | 46° | TL3 20.6, SH4 19.5, GS2 13.6 |
| aspen_co | 931,516 | 90.3% | 6.5% | 0.3% | 2,604 | 2,186–4,023 | 61° | TU1 22.2, TU5 21.6, SH4 12.7 |

Timber country: four of the six tiles are 50–60% timber-understory; GR+GS
peaks at 40% of burnable (Eagle / Gypsum), below the 0.60 extreme-spread-regime
gate everywhere, so that posture never arms on the corridor
(`docs/vail_aspen_response_program.md` §2.5). No nodata anywhere.

**Extended fire-zone tiles fetched 2026-09-24 04:30–05:12Z** by
`py -3 tools/fetch_sim_tiles.py --extended --only <key> …` — six LFPS jobs,
387–475 s each, all first attempt; the existing product size was reused
(**48×48 km @ 240 m, no step-down**; `data/tiles/provenance_ext.json`).
"WUI dev cells" counts the whole 48 km tile; "center 18 km" counts the
client-placement core (the standard footprint), which must stay populated.

| key | bytes | grid | burnable | dev 91/93 | water | WUI dev cells (r≤3) | center 18 km WUI cells | elev m | slope max | top fuels |
|---|---|---|---|---|---|---|---|---|---|---|
| vail_co_ext | 931,305 | 200×200 @ 240 m | 84.9% | 2.2% | 0.5% | 867 | 227 | 2,086–4,207 | 65° | TU5 25.3, TU1 19.0, TL5 10.6 |
| beaver_creek_co_ext | 932,138 | 200×200 @ 240 m | 89.7% | 2.2% | 0.3% | 891 | 381 | 1,987–4,230 | 64° | TU5 24.6, TU1 20.9, TL3 7.3 |
| eagle_co_ext | 929,784 | 200×200 @ 240 m | 96.2% | 2.0% | 0.4% | 790 | 418 | 1,863–3,828 | 67° | TU1 15.3, TL3 14.0, SH2 12.5 |
| glenwood_springs_co_ext | 929,812 | 200×200 @ 240 m | 94.9% | 3.9% | 0.4% | 1,539 | 274 | 1,668–3,463 | 71° | TU1 18.9, TL3 18.5, SH4 14.7 |
| basalt_carbondale_co_ext | 930,740 | 200×200 @ 240 m | 94.3% | 3.9% | 0.4% | 1,570 | 658 | 1,726–3,927 | 72° | TU1 21.2, SH4 15.7, TU5 13.6 |
| aspen_co_ext | 932,624 | 200×200 @ 240 m | 84.7% | 1.9% | 0.5% | 744 | 358 | 1,916–4,282 | 66° | TU5 25.7, TU1 16.8, NB9 12.7 |

(aspen_co_ext's 12.7% NB9 is bare rock / snow above treeline — the Elk
Mountains; non-burnable by construction, no nodata.) All twelve corridor
tiles pass `tests/test_sim_tiles.py`, including the extended uniform-cell-size
pin.

### Eagle → Vail corridor preset (2026-09-25)

Founder request 2026-09-25: *"expand the area for Vail so that the entire
Eagle to Vail corridor can be modeled at once."* New preset `eagle_vail_co`
(**Eagle → Vail corridor, CO**): ONE standard tile over the whole I-70 Eagle
Valley, Gypsum / Eagle County airport to East Vail, so a fire near Eagle,
crews at the exit-147 base and homes in Edwards, Avon, Beaver Creek and Vail
share one session. `vail_co`, `beaver_creek_co` and `eagle_co` (and every grid
result pinned to them) are untouched.

- **Geometry** — center 39.635, −106.620, half-width 30 km: bbox W −106.969935,
  S 39.365507, E −106.270065, N 39.904493 (~60 × 60 km; Gypsum exit 140 sits
  2.4 km inside the west edge, East Vail exit 180 1.5 km inside the east edge),
  **300 × 300 @ 200 m** (90,000 cells, 2.25× a standard tile). Per-location
  geometry is a registry field — `SimLocation.tile_half_m` / `tile_cell_m`
  (defaults 9 km / 90 m) mirrored by `TILE_GEOMETRY` in
  `tools/fetch_sim_tiles.py`, an AOI ladder 60 → 56 → 52 km at 200 m because
  the LFPS maximum AOI is undocumented. Every other key keeps the 18 km @ 90 m
  law, and its tile, road bbox and provenance stay byte-identical (the 12
  presets' placement, notes and 3-tick state hashes were diffed before/after).
- **Why 200 m (measured 2026-09-25, this machine, other jobs running).**
  SimSession tick wall time, 40 clients, 8 crews, one HIGH fire WSW of the
  Eagle base, 24 ticks, `set_wind(260)` before tick 8, real LANDFIRE decodes of
  the same LFPS job zip, roads on, 6 runs per row:

  | grid | ignition tick | wind-change tick | other ticks, median / max |
  |---|---|---|---|
  | **300 × 300 @ 200 m (chosen)** | 0.50–0.71 s | 0.45–0.60 s | 0.01 / 0.02 s |
  | 400 × 400 @ 150 m | 0.79–1.09 s | 0.73–1.04 s | 0.02 / 0.06 s |
  | 200 × 200 @ 300 m (pre-fetch proxy, roads off) | 0.23 s | 0.23 s | 0.005 / 0.01 s |
  | reference: `eagle_co` 200 × 200 @ 90 m | 0.41–0.59 s | 0.56–0.86 s | 0.01 / 0.03 s |

  EXTREME intensity: 200 m 0.94–1.10 s / 0.80–1.03 s; 150 m 1.52–2.92 s /
  1.79–2.29 s; `eagle_co` 0.98–1.14 s / 0.82–1.06 s. Through the public
  page's session defaults (`POST /api/sim/new`: spotting + grass floor,
  plan-on-roads, 24 h coating hold) every world-change tick is heavier —
  ember-spotting ensembles plus road-routed dispatch:

  | grid (page defaults) | ignition tick | wind-change tick | other ticks median |
  |---|---|---|---|
  | **200 m (chosen)** | 4.1–5.2 s | 6.0–6.1 s | 0.06 s |
  | 150 m | 8.9–9.4 s | 12.0–14.8 s | 0.13 s |
  | 300 m | 2.3–2.6 s | 6.4–7.7 s | 0.03 s |
  | reference: `eagle_co` 90 m | 6.8–9.6 s | 2.2–2.6 s | 0.02 s |

  150 m doubles every grid-bound cost; 300 m trims only the ignition tick (the
  wind-change tick is road-routed dispatch on the 6.8k-node graph either way)
  while coarsening the WUI band and the 300 m road rule to one 300 m cell.
  **Honest caveat:** with the page defaults a world change (ignite, wind dial)
  costs 4–6 s at 200 m — inside the envelope today's 90 m `eagle_co` already
  shows at ignition (7–10 s), but above the ~2–3 s poll-comfort target;
  ordinary ticks stay ~60 ms. A 200-client / 12-crew / two-extreme-fire
  stress peaks at 8.3 s (the dispatch local search — `eagle_co` 10.3 s,
  `beaver_creek_co` 4.9 s on the same stress). Coarse-cell physics note: at
  200 m the tiered corridor erosion's slow tier (150 m, euclidean floor)
  rounds to 0 cells and the shrub tier (200 m) to 1 — the regime the 240 m
  extended tiles already run in.
- **Tile provenance** — fetched 2026-09-26T00:57Z (2026-09-25 PDT) by
  `py -3 tools/fetch_sim_tiles.py --only eagle_vail_co`: one LFPS job, 438 s,
  first attempt on the 60 km rung — **no step-down** (`aoi_km` 60,
  `stepped_down` false in `data/tiles/provenance.json`); 16.2 MB job zip cached
  in `out/lfps_sim_cache` (re-decoding it at 200 m reproduces the committed
  tile byte for byte). Pins: `tests/test_sim_tiles.py` (corridor section).

  | key | bytes | grid | burnable | dev 91/93 | water | WUI dev cells (r≤3) | elev m | slope max | top fuels (% of cells) |
  |---|---|---|---|---|---|---|---|---|---|
  | eagle_vail_co | 2,095,361 | 300×300 @ 200 m | 91.1% | 2.2% | 0.5% | 1,959 | 1,905–4,234 | 63° | TU5 22.2, TU1 18.9, TL3 9.7 |

- **Roads** — the road bbox law now takes the location's own tile half-width
  (`SimLocation.tile_half_m`; 9 km for every other key, their recorded bboxes
  reproduced exactly): 30 km ∪ the 1.5 km zone pads → W −106.970328,
  S 39.365204, E −106.269477, N 39.904796. `data/roads/eagle_vail_co.wgr`,
  fetched 2026-09-26 by `py -3 tools/fetch_sim_roads.py --only eagle_vail_co`
  (one Overpass request; two HTTP 504s, success on the tool's third attempt):
  5,339 ways → **6,836 nodes / 8,462 edges**, LCC 93.2% (500 island nodes in
  48 islands dropped), 1,747 KB; loads in 0.06–0.10 s (budget 2 s); all six
  zones snap within 270 m; Eagle base → East Vail exit 180 is a 56.1 km I-70
  route (47.7 min at the 1.4× fire-traffic factor), A* 7–8 ms.
- **Safe zones** (PLANNING-GRADE, the same coordinates the per-community
  presets carry): I-70 exit 147 Eagle (base — listed first, so in-zone crew 1
  stages there; crews round-robin west to east), exit 140 Gypsum / airport,
  exit 163 Edwards, exit 167 Avon, exit 171 Dowd Junction, exit 180 East Vail.
  Hazard wind 240 (the Eagle Valley's WSW).
- **Placement** — `SimLocation.place_near_road_m = 300`: the seeded WUI draw is
  restricted to band cells within 300 m of a road node (program §3.7's
  driveway rule as a registry field; 0 = off, byte-identical, for every other
  preset). Of the tile's 6,527 WUI band cells 3,699 (56.7%) qualify; 1,053
  (16.1%) sit > 2 km from any road (ski-mountain 'developed' cells). No road
  graph loaded → whole-band draw plus a loud note.
- **Fire zone** — no extended tile by design (`extended_zone=False`; the tile
  tool's `NO_EXTENDED` makes `--extended` skip the key): the standard tile
  already reaches ~30 km from center. A session asking for `extended` loads
  the standard tile with a loud "NO extended tile by design" note and no
  horizon floor; the page toasts it and re-syncs the selector.
- **UI** — Location list + `SIM_LOCATIONS` entry; the map fits
  `state.terrain.bbox` and the RLE/ring layers read `nrows`/`ncols`, so the
  60 km tile frames and renders with no 18 km assumption (the zone
  selector's "Standard — 18 km community" label is the one generic string).

## Roads legibility (roads inspection 2026-09-25)

Founder: *"there are times that the response team does not use roads."* Record:
`docs/review/roads_inspection_2026-09-25.md` (screenshots, probe tables, file:line
references); probe `scratch_review/roads_offroad_probe.py`.

- **What was wrong.** The engine drove the road polylines all along (zero whole-leg
  chords in 1,000+ movement legs on the 24 standard/extended probe sessions). The
  straight lines the founder watched were the PAGE: the marker tween projected the
  previous position onto the NEXT tick's remaining route and slid in a straight line
  between 15-min tick positions (40-60% of moving crew-ticks cut > 250 m off the road,
  up to 7 km), the breadcrumb joined one point per tick straight, a DEPLOYING crew's
  queued stops were one straight red "no road route" chord (up to 38 km), a crew the
  safety sweep relocated had no leg for one tick (a synthesized straight chord), and a
  reinforcement's off-map arrival was slid up to 31 km across the map from the tile
  edge.
- **`crews[i].map_view`** (display-only, additive; contracts ROADS LEGIBILITY):
  `moved_path` (the road polyline the crew drove this tick), `jumped` (a non-driven
  position change — a reinforcement's arrival), `trail` (the driven road over the
  last 30 ticks, split at jumps; the last hour at 20 m, older ticks at <= 80 m, the
  oldest dropped before anything straightens), `queue` (a deploying crew's queued
  stops on the road route), `egress_preview` (the exact road leg a just-relocated crew
  drives next tick), `route_why` / `egress_why` (a one-line reason per spur/chord). The
  page tweens the marker along `moved_path`, draws the trail from `trail`, places
  (never slides) a jump, and names the mechanism of every remaining straight stretch
  on hover: a home or a zone anchor N m from the nearest road junction, an SEM-10d
  open-ground escape point, a chord with its roads-seam reason. Legend rows: driven
  trail, planned next stops, "hover for why".
- **Placement.** The six Vail -> Aspen community presets are road-placed
  (`place_near_road_m = 300`, the program's headline placement); the five originals
  and Rancho Santa Fe keep the whole band (byte-identical).
- **Extended fire zone.** `data/roads/<key>_ext.wgr` over the whole extended tile (see
  Fire zone, Roads). The new routes beyond the core let the SEM-10e proactive escape's
  exposed-transit fallback ride pessimistic exposure that the 10c audit counts (the
  pinned Camp-Fire-scale extended run recorded one imminent row at tick 23), so extended
  sessions arm `escape_proactive_lawful_only` (contracts; default OFF, explicit override
  wins): a voluntary decamp takes only a fully lawful route. Standard sessions keep the
  legacy rule, byte-identical.
- **Engine knob, default OFF.** `escape_road_snap_m` (contracts SEM-10d AREA-ESCAPE ROAD
  SNAP) makes the area escape road-bound; not armed in the simulator (it would change
  existing presets' sessions) — the A/B is in the review record.
- **Spur cap.** `SIM_ROAD_SPUR_MAX_M` stays 8 km: with road-placed homes every spur
  that remains longer than the engine's 2 km belongs to a zone anchor, a non-corridor
  roadless home or an open-ground escape point, and a lower cap would turn each of
  those legs into a whole-leg straight chord (worse). Byte-identity: the probe's
  per-tick state hashes (map_view stripped) are identical before/after for every
  standard session of the five originals, Rancho Santa Fe and eagle_vail_co.
