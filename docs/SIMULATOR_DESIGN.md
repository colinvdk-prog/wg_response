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
- **Fire** — click map to ignite (or drag existing); **intensity** preset mapping to weather.
  Since 2026-10-09 (founder: *"change the weather conditions for each city specific"*) each
  intensity means the chosen community's own conditions (`simui.CITY_WEATHER`: airport-station
  fire-season climatology checked against historic fire days, moved to the homes' elevation)
  on a daily cycle, and high / extreme are a finite wind event from the ignition that eases to
  the community's moderate day; the page lists what each intensity means there under the
  buttons. The legacy generic presets — Low (8 mph, RH 35%), Moderate (18 mph, RH 22%), High
  (28 mph, RH 12%), Extreme (40 mph gusting, RH 6%), held constant — remain one cfg key away
  (`sim_weather_profile: "generic"`); see "City weather (2026-10-09)" below. Plus the wind
  direction dial (default = location's canonical hazard wind:
  Santa Ana E-NE, Diablo NE, Marshall W, etc.). Multiple simultaneous fires allowed.
  Turning the dial while fires burn (`POST /api/sim/<id>/config {wind_dir_deg}` →
  `SimSession.set_wind`) re-anchors every fire at the next tick; the front keeps its real
  progress through the turn — see "Re-anchor keeps the front's progress (2026-09-26)".
- **THE SAFETY SLIDER — "minimum clear-by" (`egress_buffer_min`)**: 10–120 min, default 30.
  Applied live to `ResponseConfig.egress_buffer_min` (the SAFETY SEMANTICS 2 deadline). A
  second optional slider exposes `slack_min` (0–60). Changing either triggers an immediate
  re-plan so thresholds can be compared side-by-side.
- **Clock** — run/pause, speed 1×–600×, step one cycle, reset scenario. The 600× ceiling
  is enforced at BOTH seams (SimSession.set_speed and the server /api/sim config path), so
  no command script can request coarser multi-cycle jumps than the design allows.

## What the map shows (the functionality being demonstrated)

- **The shaded area is the simulated actual fire** — the engine's nominal member (the
  ground truth the crews are safety-audited against), cell for cell at full tile
  resolution, spot-fire islands included: dark red = burned, bright red = burning now
  (the front is still inside its fuel's cold-black window). Growth is the fire's own.
- **The h1 / h5 / h24 bands are the ladder's median (P50) forecast**, reached in < 1 h
  (red) / < 5 h (orange) / < 24 h (yellow), drawn from the fire's edge outward and never
  over a burned cell; where the median lags the fire, a band is simply absent there
  (see "Shading the fire" below). The eroded P10 corridor field toggle remains the
  actual safety surface.
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

### Shading the fire (2026-09-26)

Founder decision: *"shade the fire."* Until now the page filled the fire and drew the
bands from the pooled ensemble **P50**, while the fire the crews are audited against is
the **nominal** member. At high/extreme wind the fill under-drew the fire badly (the
extreme `park_city_windstorm` scenario at T+1h45: **0.6 km² drawn vs 12.4 km² burned**)
and jumped up to the true burn at every re-anchor. Display change only — no engine or
physics effect; contracts STATE VISUAL CONTRACT amended.

- **What is drawn** (`SimSession._fire_visuals`, `state.fire_view`): per cell, at full tile
  resolution, `burned | burning | h1 | h5 | h24`. burned + burning == the union over fires
  of nominal arrival <= sim-now — exactly `terrain.burned` and the 10c audit's mask.
  *burning now* = the cell's true nominal arrival is inside its fuel's cold-black window
  (`fuels.cold_black_minutes`: grass 5-10 min, grass-shrub 10-20, shrub 20-30,
  timber-understory 45, timber-litter 60, slash 90; flat 45 min when
  `transit_cold_black_by_class` is off) — the rule SEM-10 route selection already uses for
  "still hazardous to drive". True arrival times survive re-anchors (display
  bookkeeping — and, since the progress-preserving re-anchor below, the re-anchored field
  itself carries them; the two agree cell for cell), so a wind-dial turn never relights
  the old burn.
- **Bands** = the ladder's median at sim-now +1/+5/+24 h, **bounded by the actual fire**:
  never on a burned cell, filled from the fire's edge outward, with a border line (the
  band's *front*) only where the median reaches beyond the fire. **Overdue cells** — the
  median says burned, the nominal front has not arrived — are drawn in the **< 1 h band**
  (the forecast calls them burning; the truth has not reached them; the most urgent band
  is the conservative read). Where the median lags the fire the band is absent, and the
  legend says so.
- **Page**: one exact raster (canvas image overlay in its own pane under every vector
  layer, placed by the GridSpec law) plus the fire outline and band fronts as exact
  cell-edge lines (chain-coded in state); the layer switcher still hides the fire or any
  band; the ring / RLE paths remain as fallbacks for an older backend.
- **State compatibility**: `burned` keeps its ring shape but is now the actual fire;
  `burned_p50` carries the old fill verbatim; `fire_bands` keeps its nested-ring shape,
  now `(actual burn ∪ P50 <= now+h)`; `terrain.burned` is unchanged. Payload: `fire_view`
  7 KB (eagle_vail high, T+4h) to 28 KB (park_city extreme with spotting, T+1h45; whole
  state 90 KB vs 52 KB); one build per tick (cached across polls), 25-60 ms.
- **Frames** (`docs/img/fire_shading/`, `scratch_review/fire_shading_shots.py`): before /
  after of `park_city_windstorm` at T+1h45 and an eagle_vail_co HIGH fire at T+4h, plus
  cell-level close-ups (`*_detail_after.jpg`).

**Finding — the median is biased late against the simulated truth**
(`scratch_review/p50_vs_nominal_probe.py`; page physics: spotting + grass floor, 6 members):

| scenario | T | nominal km² | P50 km² | P10 km² | P50 / nominal |
|---|---|---|---|---|---|
| park_city_ut high | 1 h / 2 h / 4 h / 8 h | 2.7 / 11.9 / 40.5 / 64.1 | 0.09 / 2.3 / 12.0 / 30.9 | 11.0 / 37.7 / 85.7 / 140.6 | 0.03 / 0.19 / 0.30 / 0.48 |
| park_city_ut extreme | 1 h / **1h45** / 2 h / 4 h / 8 h | 3.2 / **12.4** / 17.1 / 38.3 / 62.9 | 0.11 / **0.57** / 0.97 / 7.1 / 24.3 | 15.2 / 47.3 / 56.0 / 100.6 / 131.6 | 0.03 / **0.05** / 0.06 / 0.19 / 0.39 |
| eagle_vail_co high | 1 h / 2 h / 4 h / 8 h | 1.1 / 2.2 / 8.9 / 25.4 | 0.16 / 0.60 / 2.6 / 8.8 | 2.8 / 7.7 / 29.4 / 118.2 | 0.15 / 0.27 / 0.29 / 0.35 |
| eagle_vail_co extreme | 1 h / 2 h / 4 h / 8 h | 1.9 / 5.6 / 12.6 / 79.2 | 0.36 / 1.3 / 3.6 / 8.6 | 4.8 / 15.2 / 74.9 / 300.6 | 0.19 / 0.23 / 0.29 / 0.11 |

- **Not a slow ensemble — a pooled percentile.** Each member alone burns about as much as
  the nominal (park extreme, T+1h45: 12.4 / 22.3 / 15.8 / 18.7 / 8.7 / 3.9 km² for members
  0-5, median member 14.1 km²); their per-cell median is 0.57 km².
- **Cause: the direction perturbation.** Members carry constant wind rotations of
  0 / −12 / +12 / 0 / −24 / +24° (`dir_perturb_deg` 30 at the mid-horizon ramp). At
  high/extreme wind the ellipse L/B saturates at 8 (e = 0.992), so a member turned 12°
  reaches the nominal head axis through its flank at (1−e)/(1−e·cos 12°) ≈ 0.26 of the
  head rate; the Hazen median of six (mean of the 3rd and 4th arrivals) *is* that flank
  arrival — measured 2.4-5.5× the nominal arrival 1-4 km down the park_city head axis
  (the tri-model review's N4 found ~4.2×). The pointwise median of six narrow ellipses
  pointing different ways is their common core near the ignition. Per-member ember
  spotting amplifies it (each member's spot fires land along its own downwind line).
  One knob at a time, park extreme, P50/nominal at T+1h45 / T+4h: baseline 0.05 / 0.19;
  no direction perturbation 1.21 / 1.01; direction perturbation only (wind and moisture
  perturbation off) 0.05 / 0.18; spotting off 0.25 / 0.39; neither 1.00 / 1.00;
  `model_error_sd_log` 0.71 0.08 / 0.23. Unreached (+inf) members are not a factor
  (0-2 cells).
- **Consequence.** The simulated truth is the unperturbed member, which along its own
  head axis is the ensemble's fastest member, so every P50-keyed rule runs late against
  the fire the page now shows: IMPACTED (and the `protected_then_impacted` outcome, the
  `fire_arrived` unserved reason) waits for P50 <= now — homes inside the shaded fire can
  still read EXTREME; NOTICE (P50 <= 72 h) and the P50 arm of EXTREME (P50 <= 24 h) come
  late, and so do job adoption (P50 <= notice_h) and the gel window (P50 − gel_window_h).
  The EXTREME rung itself still reaches homes ahead of the true front through its P10
  arm (P10 <= 12 h).
- **Crew safety is not affected.** Every deadline keys on P10, which is nominal-bounded
  (`spread.ensemble_arrival`): 0 cells later than the truth in all 20 scenario × time
  rows, covering 2-6× the actual burn — conservative.
- **Re-anchor sawtooth.** At every re-anchor the members restart from the nominal burned
  mask, so P50 snaps to the true burn and then lags again (park extreme: 0.11 → 3.86 km²
  against 3.95 at the anchor, 6.44 vs 9.43 km² 45 min later).
- **Recommendation (founder's call — a ladder change, not made here):** arm the existing
  default-OFF `ensemble_p50_nominal_bound` (P50 = min(Hazen P50, nominal)) in the
  simulator's session defaults. The live watcher already runs with it (`--p50-bound`,
  docs/MODEL_ASSESSMENT_HANDOFF.md §3), so the simulator currently shows a later ladder
  than production. Measured: P50/nominal 1.00-1.02 in all four probes; the bands would
  then start at the fire's head instead of being absent there.

Seen in passing here, fixed the same day (next section): a re-anchor seeded every consumed
cell AT the anchor time, so the front lost its partial progress into the next cells; after
a same-direction `set_wind` the simulated fire grew 10-24% less over the next 1-1.5 h than
the continuous run (IoU 0.76-0.93 at high/extreme with page physics; 0.87-0.91 with
spotting off, and re-throwing the seeds' embers changed nothing — the restart time, not the
embers).

### Re-anchor keeps the front's progress (2026-09-26)

**Defect.** The sim-world truth is the nominal member; a **re-anchor** re-solves a fire
from its current burn on a wind-dial change, a new ignition (the weather is domain-shared)
and the 0.8 × horizon rebase (`SimSession.tick()` step 2). Since the 2026-09-25 wind fix it
seeded every burned cell **at the anchor time** (quiet — no re-thrown embers), so every
front cell restarted its march into its unburned neighbours from zero: an edge the fire was
80% along started over. The crews are audited against this fire, so a re-anchor that slows
the truth made wind-change scenarios look safer than they were — and a re-anchor with no
physical change at all (the same wind re-set, a second fire's weather rebuild, the rebase)
slowed it too. Measured before the fix (`scratch_review/reanchor_progress_probe.py`, real
tiles, page physics — spotting + grass floor; switch at T = 1 h; area / growth-since-T /
IoU vs the reference at T + 0.5 / 1 / 1.5 / 3 h; every differing cell was LATE):

| scenario (a = same wind, c = second fire far away, d = rebase at T+5 h, horizon 6 h) | area | growth | IoU | mean late |
|---|---|---|---|---|
| park_city_ut high, a (spotting on / off) | 0.83 / 0.81 / 0.82 / 0.96 · 0.79 / 0.79 / 0.87 / 0.96 | 0.71 / 0.76 / 0.79 / 0.96 · 0.71 / 0.77 / 0.86 / 0.96 | = area | 10.2 · 6.7 min (max 60) |
| park_city_ut extreme, a (on / off) | 0.83 / 0.85 / 0.95 / 0.97 · 0.87 / 0.91 / 0.93 / 0.99 | 0.71 / 0.82 / 0.95 / 0.97 · 0.82 / 0.90 / 0.92 / 0.98 | = area | 8.9 · 3.4 min |
| eagle_vail_co high, a (on / off) | 0.88 / 0.78 / 0.87 / 0.90 · 0.94 / 0.78 / 0.93 / 0.94 | 0.67 / 0.57 / 0.80 / 0.88 · 0.86 / 0.67 / 0.91 / 0.94 | = area | 15.3 · 9.9 min |
| c (all three) | identical to a (spotting on) | | | |
| park_city_ut high, d | 0.98 / 0.98 / 0.99 / 0.98 | 0.68 / 0.87 / 0.91 / 0.94 | = area | 103 min |
| eagle_vail_co high, d | 0.92 / 0.91 / 0.89 / 0.86 | 0.42 / 0.62 / 0.64 / 0.74 | = area | 73 min |
| turn +45° vs the continuous-shift run (park high / park extreme / eagle high) | 0.96-1.00 | 0.86-1.00 | 0.92-1.00 | 2.8 / 3.0 / 5.2 min |
| turn +90° (same) | 0.97-1.00 | 0.95-1.00 | 0.97-1.00 | 1.7 / 1.5 / 1.5 min |

**Fix** (`simui._reanchor_history` + spread's RE-ANCHOR HISTORY arguments, all
default-None → byte-identical; the pinned spread fingerprints do not move): the re-anchor
hands the solve (1) the kept burn **with each cell's TRUE arrival time** — final, quiet, the
front re-seeded at its own times, every unburned cell clamped strictly after the anchor, so
nothing retro-burns and the burned mask at the anchor tick is identical; (2) the **weather
the fire burned under** (one sample per field — the domain weather is constant within a
field), so each front edge's progress before the anchor is priced at the rate it was made
at and only the remainder at the new wind; (3) the **embers still aloft** (thrown by a kept
cell, landing after the anchor — the nominal field logs every ember it throws); in the
continuous run they land, so they land here. After the fix every case above, turns
included, reproduces its reference **cell for cell**: 0 of 88-10,456 cells differ over the
3 h window, spotting on (IoU 1.00 / area 1.00 at every lead). Invariants pinned in
`tests/test_sim_reanchor_progress.py` (13 tests; 7 fail on the pre-fix re-anchor): a no-op
re-anchor (same wind, second ignition, rebase) is the continuous run; a 45°/90° turn — and
two turns an hour apart — is the continuous run with the same shifts; the growth still
turns; the burned mask never shrinks and never jumps; kept cells are history in every
member (p10 = p50 = p90 = the true time) and p10 stays nominal-bounded.

- **The reference for a turn** is ONE continuous nominal run from the ignition under the
  simulator's weather script with the wind shifting at the switch hour (MockWeather
  shifts), priced with piecewise edge weather (an edge crossing the shift keeps the
  progress it made and continues at the new rate — exactly "the new wind from T with each
  front cell's partial progress preserved"). Limits: an hourly weather script can only
  shift on a whole hour after the ignition, so the probe and tests switch at T = 1 h / 2 h
  (the simulator itself integrates a change at any tick); it is the nominal member only
  (the members restart from the truth at every re-anchor by design — the P50 sawtooth
  above is unchanged); edges are priced from their source cell (the engine's law, shared).
  The engine's default FROZEN departure-hour law (an edge keeps the wind it left under) is
  a different, LATE-biased yardstick: vs the piecewise run it reads IoU 0.42-0.96.
- **Why the weather history is needed.** The prescribed minimum — re-seed the front at its
  true times but price its old progress at the NEW wind — is exact for a no-op but, after a
  turn, runs EARLY (93-97% of differing cells early; mean 4-18 min over 3 h; the kept flank
  "finishes" edges it had barely started and is clamped to burn right after the anchor):
  area 1.07-1.16 / IoU 0.86-0.93 at +0.5 h on park_city, 1.52-1.73 / 0.58-0.66 on the
  200 m eagle_vail grid (1.01-1.15 / 0.86-0.99 by +3 h). Safe polarity, but larger than
  the pre-fix turn error; `kept_wx` removes it.
- **Correction (wind dial, 2026-09-25).** The earlier claim that the quiet anchor-time
  re-anchor "matches one continuous run with the same wind shift at IoU 0.97-1.00" (spread
  docstring; 0.95-1.00 in the commit) held only for turns, where restarting a flank that
  had made little progress costs little (measured again: 45° 0.92-1.00, 90° 0.97-1.00, every
  cell late). A re-anchor that changes nothing physical matched its continuous run at only
  IoU 0.78-0.95 over the first 1.5 h (growth 5-43% short; 0.86-0.99 by 3 h), the rebase
  losing 6-58% of the growth.
- **Side effects, by design.** The re-anchored field now holds each kept cell's true
  arrival instead of the anchor time, so SEM-10 route selection sees old burn as OLD after
  a re-anchor (its cold-black carve-out used to read the whole burn as freshly burned for
  up to its window — 5-90 min by fuel — after every wind change or second ignition); the 10c
  audit (nominal <= now = burned) and every P10 deadline are unchanged in kind; the
  fire-shading record equals the field. Cost: re-anchor ticks unchanged (2-3 s on the real
  tiles with page physics); the ember log is ≤ 0.5 MB per fire (packed array).
- **Coarse jumps (a SAFETY pin moved — left for the owner).** When one tick is longer than
  the field's remaining horizon (`SimSession.set_speed` past ~77× at the 96 h horizon;
  the page never does this — its speed is wall-clock cadence, every engine tick one 900 s
  cycle), the old field is UNREACHED beyond its horizon, so at the rebase the kept front's
  next edges are overdue: they now burn right after the anchor (the clamp — a one-ring
  catch-up of growth the horizon cut off), where the old rebase restarted them from zero.
  The fire grows more per coarse tick — `test_speed_600x_coarse_jumps_audit_still_clean`
  (FAST config, 24 h horizon, 150 h ticks): 666 vs 488 burned cells after the first coarse
  rebase, 1,555 vs 1,014 after six — and the 10c backstop now reports crews the nominal
  front reaches while parked inside one 150 h tick as **burned** (sim-g2 tick 3, sim-g1
  tick 6; crew-overrun rows per tick 0/1/1/0/2/2 vs 0/1/0/0/1/0, all while egressing;
  serviced-stop audit still clean). That test asserts imminent-only rows and now FAILS; it
  is a safety pin, so it was not re-pinned. One outcome pin was re-pinned:
  `test_live_tightening_reduces_served_vs_control`'s tightened twin serves 1 (was 0) — the
  spot-fire injection's wind swing re-anchors fire 1, which now keeps its progress (P10
  0.1-0.4 h earlier), so the tick-6 re-plan sends the idle crew to sim-c16, served lawfully
  (margin 15.3 h under the 120-min rule).

### Coarse jumps are sub-stepped; the median is tied to the fire (2026-09-26)

Two founder decisions, taken after the re-anchor fix above:

- **"Sub-step big jumps."** `SimSession.tick()` advances `replan_period_s × speed`. A
  tick longer than `SIM_MAX_SUBSTEP_S` (1 simulated hour) now runs as equal internal steps
  of at most an hour, each the full engine cycle (forecast, dispatch, movement, crew-safety
  sweep, audit). The caller still sees ONE tick: the clock advances by the whole jump, the
  tick counter and the time-scrub history by one, and the returned crew-overrun rows are
  the union over the internal steps, so nothing a step saw is hidden. Page ticks never take
  this path: the server's speed only changes wall-clock cadence, so every page tick is 15
  simulated minutes, and `set_speed` ≤ 4× at the 15-min period is byte-identical. One 2 h
  tick lands in exactly the same world as two 1 h ticks (fire, crews, served, audit;
  `tests/test_sim_coarse_substep.py`).
  Why: with the fire no longer freezing at each 150 h rebase, the 600× test caught a crew
  parked inside a single jump at tick 2; sub-stepped, no crew is caught by the moving front
  in the first 300 h. From ~400 h the whole synthetic tile, safe zone included, has burned,
  and the strict audit counts crews standing on black 2–23 days old (the crews shuttle
  between that black and the pessimistic band, ~300 relocations per 150 h). The founder's
  ruling ("split the test"): the original assertions hold over the first two jumps
  (`test_speed_600x_coarse_jumps_audit_still_clean`), and the burned-out stretch is pinned on
  its own terms — at every internal step, a crew on burned ground is only ever on black
  older than its fuel's cold-black window, never a live front
  (`test_burned_out_map_crews_only_on_old_black`). Whether old black should count as a
  violation at all was a separate safety-law decision — taken the same day (next
  section: it does not; that test now pins no rows at all).
- **"Match production."** The simulator now arms `ensemble_p50_nominal_bound` (the live
  watcher's `--p50-bound`): the ladder median is never later than the fire the page shades,
  so homes inside the burn read IMPACTED on time and job adoption / outcome tags stop
  lagging (park_city extreme at T+1h45: median burned area within 5 % of the 12.4 km² fire,
  every home on ground burned a tick ago IMPACTED —
  `test_default_median_never_lags_the_shaded_fire`). The shading tests that measure the old
  lag pin the bound off. Crew safety was already bound the same way (P10 ≤ nominal).

### Old black is a refuge; crews that leave the map are out (2026-09-26)

Founder rulings, verbatim: *"old burned ground does not count as a violation. crews once
leave a map are no longer part of the simulation"*. Three default-OFF knobs (contracts SAFETY
SEMANTICS 10, README config table), all armed in the simulator's sim-defaults block
(explicit `cfg_overrides` win): `old_black_refuge`, `left_map_out`, `escape_prefer_on_map`.
The live engine is untouched (below).

**Rule 1 — old black is a refuge.** A cell whose nominal (ground-truth) arrival is at least
its cold-black window in the past (`_cold_black_pass_s`: the transit rule's per-fuel windows,
grass 5–10 min … slash 90; flat 45 min when `transit_cold_black_by_class` is off) is OLD
BLACK — burned out, it cannot burn again. `SimSession._exposure_at(pos, t)` reads such a cell
as unreached, and every SEM-10 judgement goes through it:

- the **10c audit** counts no row for a crew on old black — neither *burned* nor *imminent*
  (the eroded-P10 band that reaches an old-black cell from nearby fuel does not count);
- the **sweep** never relocates a crew off old black, and SEM-10e never decamps it;
- a **zone** on old black is currently safe (10b) and a "forever" zone for the longevity /
  durable tests; an **escape destination** on it is lawful with an unbounded horizon;
- **route / exposure metrics** (`_metrics_of_points`) and the **road-network escape
  search** charge its samples nothing, each judged at its own pass time — the black behind
  the front becomes a lawful escape (extended stress basalt_carbondale_co, t51: the searched
  node 2.05 km out on 3.5-h-old black instead of the old 19.5-h node on unburned ground).

Black still inside its window is exactly as before: counted, fled, impassable. Two
deliberate refinements, both measured:

- **The audit judges old black over the whole step.** It looks once per step, so it excuses
  black only if it was already old when the step began, or when the crew got there if later
  (`_SimCrew.pos_since_s`). A front that overruns a crew between two audits is reported once
  even though its fuel burned out before the audit looked — without this a 5–10 min grass
  window inside a 15-min tick hid the overrun entirely (a fire lit on a placed crew:
  `test_armed_fire_lit_on_a_placed_crew_reported_then_rests_on_the_black`).
- **The SEM-10e zone fallback keeps its strict crossing reject.** It lets a *voluntary* move
  ride pessimistic-exposed transit and rejects any route over a front-passed cell "however
  cold". Relaxed to hot black only, it sent three basalt_carbondale_co crews (standard probe,
  t36) 16 km to zone z1 over old black *and* unburned band — 3 imminent-while-egressing rows
  where the strict rule decamps them 2.1 km by road with none. Kept strict.

**Rule 2 — a crew that leaves the map is out.** *The map* = the terrain tile (its grid, or
the rectangle the page draws for it — they differ by a sliver at the east edge because the
grid's longitude scale follows each row's latitude; a road node 9.5 m past the grid but
inside the drawn rectangle "left" three basalt_carbondale_co crews until the map included
the rectangle) plus `ZONE_ON_MAP_M` = 1.5 km (the road fetch's zone pad) around every
catalog zone. **Five**
standard-tile zones sit past the edge — the three known ones, malibu_ca z1 Zuma Beach
(1.24 km), santa_rosa_ca z3 Windsor (1.45 km), park_city_ut z3 Jeremy Ranch (28 m), and two
more: malibu_ca z3 US-101 / Las Virgenes (1.13 km) and paradise_ca z2 Neal Rd / Hwy 99
(1.00 km); no extended tile has one. All are within 1.45 km, so the operator's decision
(zones are on the map) holds for every preset.

- **Leave trigger** (`_leaving_map`, checked after each step's movement): the crew is off the
  map and nothing it still has to do brings it back — no itinerary stop and no egress target
  on the map (stopped / parked there, or on a final leg that ends there; in practice a
  road-network escape to a node past the tile edge). Transit through off-tile road toward an
  on-map home, zone or node is not leaving (PCH toward Zuma); an INBOUND reinforcement at its
  tile-edge entry has not entered; placement already refuses off-tile points.
- **Out for good**: never again in `dispatch.plan`'s crew set, no movement, no beacons, no
  shift or safety sweep, no audit row; its commitments are released (loud note naming them;
  re-planned next tick); status UNKNOWN (no longer tracked). Reported: `crews[i].left_map`
  / `left_map_at_s`, `scoreboard.crews_left_map`, a `CREW LEFT THE MAP` note; the page greys
  the marker at its last position with a "left the map" chip, greys its card, pins a feed
  line, adds a scoreboard row, legend rows and tooltip text (also for a crew on old black).
- **The tie-break** (`escape_prefer_on_map`). An off-tile road node has no fire field, so it
  reads an unbounded horizon: it ties with every long-safe on-map refuge at the 24 h cap and
  beats every shorter one. Decided on the numbers: (1) among lawful destinations on the same
  exposure and capped-horizon steps, an ON-map refuge ranks first (then zone before node,
  then drive time) — the 60-scenario bench's departures fell **23 → 6** with served **792 →
  793**, 10c rows unchanged (12), and aspen_co's three standard-probe departures disappeared
  with identical served / rows / relocations; (2) the VOLUNTARY SEM-10e road decamp (every
  candidate already lawful, exposure-free, durable) prefers any on-map refuge to any off-map
  one — the corridor measurement found that decamp leaving the map for nodes 0.8–1 km past
  the edge over durable on-map refuges 3.7–13 h out (departures 37 → 29 in local2_road, served
  679 either way). An emergency escape still takes a strictly safer off-map node.

**Numbers** (before = all three knobs off, byte-identical to HEAD; after = the simulator
defaults; 10c rows are crew-overrun rows summed over every tick; "old-black crew-ticks" are
the crew-ticks the strict audit would count on the same trajectory that the new audit does
not):

| set | served | departure rows | 10c rows (reason/mode) | relocations | crews left the map | old-black crew-ticks (inside an active front's band) |
|---|---|---|---|---|---|---|
| 600× coarse-jump pair, first 300 h | – | 0 → 0 | 0 → 0 | 1 → 1 | 0 → 0 | 0 |
| 600× burned-out stretch (300–900 h, per internal step) | – | 0 → 0 | 1,006 (972 burned on 19.5 min–23 d old black, 34 imminent) → **0** | 1,011 → **3** | 0 → 0 | 1,172 (0) |
| pinned Camp-Fire-scale extended run | 17 → 17 | 0 → 0 | 0 → 0 | 4 → 4 | 0 → 0 | 33 (0) |
| offroad probe, standard + extended, 24 sessions × 40 ticks | 669 → 669 | 0 → 0 | 8 → 8 (imminent/egress) | 100 → 100 | 0 → **5** (beaver_creek_co std) | 0 |
| extended stress, 6 × 80 ticks EXTREME | 164 → 164 | 0 → 0 | 1 → 1 (imminent/egress) | 31 → 31 | 0 → 0 | 29 (0) |
| 60-scenario bench, plan-on-roads | 791 → **793** | 0 → 0 | 12 → 12 (imminent/egress) | 80 → 83 | 0 → **6** | 0 |
| corridor local2_road (vail, beaver, glenwood; 54) | 683 → **679** | 0 → 0 | 11 → 11 (imminent/egress) | 131 → 128 | 0 → **29** | 729 (312) |
| corridor alerted2_road (9) | 263 → 263 | 0 → 0 | 1 → 1 | 30 → 29 | 0 → 0 | 516 (0) |

(Without the tie-break: probe 8 departures, bench 23 with served 792, corridor local2 39 with
served 681.) Departure-audit violations stay **0** everywhere. The pinned
`test_sem10d_extended_campfire_scale_no_overrun_ever` passes unchanged. Tick time unchanged
(eagle_vail_co std median 33.2 → 33.5 ms; boulder_co ext 21.5 → 19.3 ms;
`scratch_review/old_black_ticktime.py`).

- **Where crews still leave.** Every departure goes to a road node 0.06–2.0 km past the tile
  edge (corridor median 0.82 km). In the corridor, 21 of 29 are emergency escapes whose best
  on-map refuge was lawful but worse on the model's safety steps — a *finite* horizon
  (0.9–22.6 h) against the off-map node's "never", which is only the absence of a fire field
  past the edge, or more exposure on the way; 8 are voluntary decamps with no on-map durable
  refuge at all. (beaver_creek_co's standard-probe crews: on-map refuges of 13.0 / 7.8 / 2.6 h
  against a node 147 m past the edge.) Served: corridor local2 −4 (five scenarios
  −2, four +1/+2; `scratch_review/old_black_corridor_diag.py`), bench +2, probe and alerted
  unchanged. **Open (owner):** a horizon for off-tile nodes read from the nearest on-tile
  cell's P10 (or the corridor law's fail-closed boundary bound) would compare them honestly
  and keep most emergency escapes on the map — a model change, not made here.
- **Old black in the validation sets.** The standard probe, the extended stress set (bar one
  session) and the bench never put a crew on old black; the corridor does, because the road
  search now treats the black behind the front as a refuge (beaver_creek_co local2: 458
  crew-ticks, 308 inside an active front's band). No served loss follows from it (every
  corridor scenario with old-black crew-ticks served the same), and no 10c imminent row ever
  came from old black alone around an unburnable cell (0 "history-only" rows in every set).

**Doctrine nuance (quantified, not ruled on).** Good black must be large enough around the
crew (safety-zone sizing ~4× flame length); the model's rule is per 90–500 m cell. Of the
crew-ticks the new rule stops counting, the nearest actively burning cell (inside its
window) was:

| set | crew-ticks | inside an active front's eroded-P10 band | < 100 m | 100–300 m | 300 m–1 km | 1–3 km | ≥ 3 km / nothing burning |
|---|---|---|---|---|---|---|---|
| corridor local2_road | 729 | 312 (beaver_creek_co 308, vail_co 4) | 0 | 60 (min 113 m) | 463 | 206 | 0 |
| corridor alerted2_road | 516 | 0 | 0 | 0 | 9 | 263 | 244 |
| extended stress | 29 | 0 | 0 | 0 | 3 | 26 | 0 |
| Camp-Fire-scale run | 33 | 0 | 0 | 0 | 0 | 8 | 25 |
| burned-out map (600×) | 1,172 | 0 | 0 | 0 | 0 | 0 | 1,172 (4 at 14.6 km) |
| fire lit on a placed crew | 11 | 0 | 0 | 0 | 0 | 1 | 10 |

The black under the corridor local2 crews was 47–845 min old (windows 5–60 min; alerted:
236–3,221 min). 60 crew-ticks (all corridor local2) rest on black 113–300 m from flame —
beyond 4× flame length for grass or shrub (1–10 m flames), not for a timber crown run; the
per-cell rule cannot tell, and "inside an active front's band" (312) marks where doctrine
would ask.

**Byte-identity.** Knobs off = HEAD at every tick: 24/24 standard-probe sessions (984
states) and 6/6 extended-stress sessions (486 states), hashes with `map_view`, `cmd_log` and
the new additive keys stripped (`scratch_review/old_black_hashcmp.py`). A session where no
crew leaves is byte-identical with `left_map_out` on or off (tie-break pinned off): 22/24 +
6/6 (the two others are exactly the sessions where crews leave). The old-black refuge alone
changes nothing in the standard probe (24/24) and one extended session (the escape into the
black). New state keys (additive): `crews[i].left_map`, `left_map_at_s`, `on_old_black`,
`scoreboard.crews_left_map`.

**Live engine.** No crew-position audit or relocation exists outside the simulator:
`cli watch` only reports beacon positions; `threat.py` hands eligible crews to
`dispatch.plan`; `sim.py`'s audit covers serviced-stop departures only. The one related live
rule is the planner's corridor law (`safety.LegOracle`, samples include the leg's start):
a leg that starts or ends inside the eroded-P10 band is infeasible, so a crew beaconing from
old black gets no ingress assignment and an old-black zone is never an egress target. Left
unchanged — the ruling is recorded for the simulator's audit; the live side is a separate
decision. (In the simulator the same planner rule means a crew resting on old black is out
of dispatch until something moves it.)

**Tests.** New: `tests/test_sim_old_black.py`, `tests/test_sim_left_map.py`. Changed because
of the ruling (strict-law pins kept by pinning `old_black_refuge=False`, armed truth in the
new files): `test_simui.py::test_dynamic_safe_zone_validity_excludes_overrun_zone` and
`::test_no_safe_zone_fail_closed_alarm_and_safest_pick` (fires lit ON zones burn them out:
a burned-out zone is a refuge), `test_sim_crew_placement.py::test_crew_placed_where_the_fire_
starts_is_reported_never_silent` (armed: reported at the first tick, then rests on the black
— `test_armed_fire_lit_on_a_placed_crew_reported_then_rests_on_the_black`),
`test_escape_proactive.py::test_picker_fallback_rejects_nominal_crossing` and
`test_sim_escape_roads.py::test_proactive_counterpart_every_road_exposed_holds` (4-h-old
black is a refuge the lawful road decamp may cross); re-pinned to the new truth:
`test_sim_coarse_substep.py::test_burned_out_map_crews_only_on_old_black` (no rows at all,
relocations ≤ 5, measured 3); schema: `test_simui.py::test_map_view_is_additive_display_only`
(the three additive crew keys).

**Frames** (`docs/img/old_black_left_map/`, `scratch_review/old_black_shots.py`, a private
server): `a01_crew_resting_on_old_black` (+ `a01b` tooltip) — boulder_co, crew 1 placed on a
grass road, a HIGH fire lit 600 m upwind overruns it inside the first tick (reported), then
it rests in the black behind the front, not moved, violations 0; `a02_crews_left_the_map`
(+ `a02b` tooltip and greyed cards) — beaver_creek_co probe session, three crews greyed at a
node 147 m past the dashed tile edge, "crews left the map 3", pinned feed lines.

**Records.** `scratch_review/old_black/`: the probe arms in compact form (per-tick hashes,
summaries, measurements; `probe_head*` = the HEAD tree's run), the corridor arms
(`corridor_{off,on,on2,noprefer}/` — `on` = the plain tie-break, `on2` = shipped) with their
`violations.json`, `doctrine.json`, `campfire.json`, `corridor_leave_diag.json`;
`scratch_review/bench_oldblack_{off,on2,noprefer}_por.json`. Scripts:
`scratch_review/old_black_*.py` (metrics, A/B drivers, hash compare, state diff, traces,
diagnoses, frames); the probe, bench and corridor grid gained read-only measurement hooks
(`roads_offroad_probe.py` always; `BENCH_OLD_BLACK_METRICS=1`, `CORRIDOR_OLD_BLACK_METRICS=1`).

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

## City weather (2026-10-09)

Founder, 2026-10-09: *"change the weather conditions for each city specific."* Review of
record: `docs/review/city_weather_2026-10-09.md` (method, stations, every value, the
before/after of the thirteen one-click scenarios, sources).

- **What an intensity means.** `simui.CITY_WEATHER[key].days[intensity]` (`CityDay`):
  the afternoon wind, gust, RH and temperature and the pre-dawn trough of the station's
  fire-season days in that class (low = 40th-60th percentile day by the Fosberg index,
  moderate 75th-90th, high 93rd-98th, extreme top 1 %), moved to the homes' elevation;
  for high / extreme the hours the index stays near its peak (`event_h`) and the hours it
  takes to fall to half (`ease_h`). Santa Rosa's extreme is the Tubbs Fire night measured
  on the slopes (its airport sits outside the downslope wind). Generated by
  `tools/city_weather_climatology.py`; `tests/test_city_weather.py` holds the table to
  `docs/review/city_weather/climatology.json`.
- **Shape in time.** `weather.DailyCycle` (local standard time; afternoon peak at the
  moderate days' median peak hour, trough 06:00) and `weather.WindEvent` (hold, then a
  linear blend into the community's moderate cycle), both additive on `MockWeather`
  (without them the legacy script byte for byte). A high / extreme ignition starts an
  event unless one at the same intensity is still blowing (the new fire joins it); a change
  of intensity starts a new one. Direction stays the dial's. The forecast is a pure function
  of absolute time and is extended (no world change) when a field's horizon needs it.
- **Selection.** `cfg.sim_weather_profile` (contracts, default `"generic"`); the simulator
  arms `"city"` (`simui.SIM_WEATHER_PROFILE`), an explicit override wins, a location without
  a profile keeps the generic table. The edge law stays the frozen departure hour (the
  piecewise law moved the area at most 5.3 % for 7-12x the solve time). Seeded drills keep
  the generic table.
- **Re-anchor.** Under city weather the closing field's history becomes its hourly spans
  (`_field_wx_spans`), so a kept front's progress is priced at the weather it burned in; a
  no-op re-anchor stays identical to the continuous run for 3 h and within IoU 0.93-1.00 at
  6 h (generic: identical, unchanged).
- **State and page.** `state()["weather"]` (profile, community, station, `presets` = one line
  per intensity, `label`, `event` clock, `now`); the page renders it under the intensity
  buttons and lights the armed and the burning intensity.
- **Known limits / findings** (review doc): airports, not slopes (nights recover more than
  on thermal-belt slopes); one shape per class; the event starts with the fire (06:13-07:13
  local in the one-click scenarios); a crew-safety gap the new fire behaviour exposes
  (Malibu one-click: one overrun tick; reproduced with the old presets at high / moderate —
  fixed the same day: "No crew drives an unjudged leg (2026-10-09)" below);
  burned area falls as wind rises on the real tiles (a spread-review item).

## No crew drives an unjudged leg (2026-10-09)

The founder's standing law: crews must never be caught by fire (contracts SAFETY SEMANTICS 10,
*"crews G1/G2 caught in fire"*). City weather (above) surfaced SEM-10c crew-overrun rows the
law did not prevent (`docs/review/city_weather_2026-10-09.md` "Safety: one gap"). This is the
root cause and the fix: contracts **SAFETY SEMANTICS 20**, two default-OFF knobs
(`corridor_cell_exact`, `crew_egress_judged`; README config table), both armed in the
simulator's sim-defaults block (explicit `cfg_overrides` win; the live engine is unchanged).
Code: `geo.path_cell_runs`, `safety.LegOracle` (cell samples), `simui` `_sem10_points` /
`_cell_span_walk` / `_leg_lawful` / `_judge_egress_start` / `_rejudge_egress`. Tests:
`tests/test_crew_egress_judged.py` and
`tests/test_city_weather.py::test_young_fire_leg_no_overrun_under_city_weather` (the strict
xfail, now passing). Sweep: `scratch_review/crew_egress_gap_sweep.py`, its records
`crew_egress_gap_sweep_before.jsonl` (commit 8973cd9) and `crew_egress_gap_sweep_after.jsonl`.

**For the founder.** A crew could end up inside the danger band in four ways, all in how the
code checked the roads crews drive — none in the new weather. When a crew had to pull out of
a home early for safety, it headed for the safe zone planned for the *end* of its route — in
Malibu that is across the fire — and nobody had checked that road from where the crew
actually was. A crew driving to a safe zone kept going even when the danger band would reach
the zone soon after it got there. An early, voluntary move could take a road the system had
itself marked as exposed. And the road check looked at points 300 m apart, so a road that
clipped a danger spot between two points looked clear. The city weather changed which homes
became jobs and which roads crews drove, and that exposed these gaps (the old weather caught
Malibu crews the same way). Now every road a crew drives is checked square by square on the
map for the moment the crew will be on it; every time a crew starts to drive out — after a
pull-out or after its last home — the road from where it actually is gets checked first, and
if it is not safe the crew goes to a safe zone it can reach safely; while it drives, its road
and its destination are checked again every step; and an early move never takes an exposed
road. Across all thirteen demo scenarios at every intensity, both weather tables and both
fire-speed rules (208 runs of 6 hours): crews caught in the danger band 10 → 0;
homes protected 5,792 → 5,827.

**Measured before the fix.** The sweep: the 13 one-click scenarios (`server.SCENARIOS`, page
physics: spotting on, grass floor 0.10, plan on roads, 24 h hold) × low / moderate / high /
extreme (a non-preset intensity re-lit at the preset's own point and wind) × city / generic
weather × the grass-only floor (the simulator default) and the old all-fuel floor, 6 h each:
208 runs, **10 crew-overrun rows in 9 runs, every one "imminent" while egressing; 0 departure
violations**. Every row traced (`scratch_review/crew_egress_gap_trace.py`):

| Run | First row | Mechanism |
|---|---|---|
| Malibu extreme, city, grass-only | T+0.75 h sim-g1 | W |
| Malibu high, city, grass-only | T+1.25 h sim-g1 | W |
| Malibu high, city, all-fuel | T+1.75 h sim-g1 | W |
| Malibu high, generic, grass-only (2 rows) | T+1.00 h sim-g1, T+1.75 h sim-g3 | W, W |
| Malibu moderate, generic, grass-only | T+2.25 h sim-g3 | W |
| Aspen high, city, all-fuel | T+3.50 h sim-g3 | Z |
| Glenwood Springs extreme, generic, all-fuel | T+0.75 h sim-g1 | Z |
| Glenwood Springs extreme, city, all-fuel | T+1.50 h sim-g1 | Z |
| Eagle extreme, city, all-fuel | T+4.00 h sim-g3 | V |
| synthetic triage scatter (`tests/test_simui.py`), city moderate | T+1.00 h sim-g1 | S |

- **W — the withdrawal egress.** Dispatch (SEMANTICS 4) judges an egress from *every* stop to
  that stop's own nearest lawful zone at its planned departure, and emits *one* zone per crew
  plan: the last stop's (`dispatch.plan`: `egz` = the last stop's `egress_zone_id`). The
  road-egress guard withdraws a crew at an early stop (projected departure + egress time past
  the live latest-depart) and the simulator turned it onto that zone, on a road from THIS stop
  no law had judged. Malibu city extreme: sim-g1, eleven stops, last-stop zone z3 (US-101 / Las
  Virgenes, in the hills north of the fire), withdrawn at its second home on the coast at
  T+0.52 h; the road to z3 runs east along the coast, across the downwind side of a fire the
  Santa Ana pushes toward the sea; audited at T+0.75 h at a point the pessimistic band had
  reached at T+0.37 h (5.1 km from the ignition). The 10a sweep could not catch it: it judges the
  crew's own cell (one step of lead) at step ends, and a crew drives 10–15 km in a step.
- **Z — the zone after arrival.** Dispatch's egress law clears a zone at the arrival instant
  (eroded P10 ≥ arrival + 30 min); the crew then waits there up to a step for the next audit.
  The 10b sweep re-checked an egressing (or deploying) crew's zone by its own cell at **zero**
  lead, while relocation targets get one step. Aspen: zone z1's eroded P10 was T+3.83 h; at the
  T+3.25 h sweep 3.83 > 3.25 + 0.5, "safe"; the crew finished its last home, arrived, and the
  T+3.5 h audit found 3.83 < 4.0.
- **V — the voluntary decamp.** SEM-10e's exposed-transit fallback (a durable zone over road
  the law judges exposed but not yet burned) — skipped only on extended tiles, whose graph was
  thought to be the only one offering such routes — rode 915 s of exposed transit (worst margin
  −110 min) toward Eagle's z2 and was audited 1.1 km short of it.
- **S — the sampling gap.** The corridor law samples every 300 m, SEM-10 every ~275 m: on a
  90 m grid one cell in three. On the triage scatter's 500 m grid the ignition cell's eroded
  band (burning since T+0) is the cell and its four neighbours; the crew's chord between two
  homes clipped the corner of the west neighbour between samples reading T+17 h and T+10 h —
  the plan judged it 9 h clear — and the T+1.0 h audit found the crew in that cell, 467 m from
  the ignition cell.

**What "imminent" meant.** The 10c audit flags a crew whose cell's eroded P10 is earlier than
now + the 30-min egress buffer: the pessimistic (P10) front, widened by the erosion radius
(550 m grass, 200 m shrub, 150 m timber), is within half an hour of the crew. In every row
above the actual (nominal) fire had not reached the crew; the law counts the margin, and the
law is the founder's.

**Does the corridor law see the ignition cell's own arrival?** Yes: the eroded field carries it
(T+0 in that cell and in every cell within the erosion radius). What it missed was a cell its
samples stepped over. **Do forecast changes between cycles open a window?** No: a city
forecast's daily cycle is solved into each fire's arrival field once — the field is re-solved
only on a world change or the horizon rebase (verified: one field object across the 6-tick
triage run and the Malibu run, under a forecast that changes every hour). The cycle decides
which homes become jobs and when (the cool morning holds the young fire small while the
afternoon forecast brings homes into the notice window), so the plan sends crews down
different roads; the gaps were there under the old weather too (Malibu high / moderate).

**The fix.**
- **(a) `corridor_cell_exact`.** Every raster cell a leg passes through (`geo.path_cell_runs`:
  the leg followed linearly in lat/lon, as crews drive it) is also a sample, judged at the
  latest moment the crew is in it (where the leg leaves the cell) — in `safety.LegOracle`
  (dispatch's ingress and egress law) and in the SEM-10 judgement (`_route_metrics` and the
  road-network escape search share one per-span walk, so their numbers stay equal). The legacy
  samples stay; the crew's own starting cell keeps its carve-out while the walk has not left it.
- **(b) `crew_egress_judged`.** (i) When a crew turns onto an egress leg from a stop (a
  withdrawal, or leaving its last stop) that leg — the road it will drive, at the speed it
  drives it — must be lawful from where it is: nothing burned at its pass time, every sample's
  eroded P10 at least the buffer beyond its pass time, the zone clear one step beyond arrival;
  else the SEM-10d routed choice replaces it (note: "egress re-routed (SEMANTICS 20b)"). (ii)
  The sweep re-judges every egress in flight the same way; a lawful alternative always wins,
  between unlawful ones the escape hysteresis decides. (iii) A voluntary decamp never rides
  exposed transit, on any tile.

**After the fix** (same sweep; per run: homes served before → after, `[ovt b → a]` where a run
had overrun ticks; 0 departure violations in every run before and after):

| Scenario | Intensity | city / grass-only floor | city / all-fuel floor | generic / grass-only floor | generic / all-fuel floor |
|---|---|---|---|---|---|
| aspen_southwesterly | low | 0 → 0 | 36 → 36 | 0 → 0 | 36 → 36 |
| aspen_southwesterly | moderate | 0 → 0 | 36 → 36 | 25 → 25 | 23 → 27 |
| aspen_southwesterly | high | 11 → 11 | 33 → 33 [ovt 1 → 0] | 21 → 21 | 13 → 13 |
| aspen_southwesterly | extreme | 25 → 25 | 28 → 26 | 22 → 22 | 13 → 13 |
| basalt_carbondale_southwesterly | low | 31 → 31 | 36 → 36 | 32 → 32 | 36 → 36 |
| basalt_carbondale_southwesterly | moderate | 32 → 32 | 37 → 37 | 25 → 25 | 29 → 29 |
| basalt_carbondale_southwesterly | high | 30 → 30 | 28 → 28 | 13 → 13 | 13 → 13 |
| basalt_carbondale_southwesterly | extreme | 30 → 30 | 36 → 36 | 11 → 11 | 7 → 7 |
| beaver_creek_westerly | low | 15 → 15 | 37 → 37 | 24 → 24 | 38 → 37 |
| beaver_creek_westerly | moderate | 23 → 23 | 35 → 35 | 29 → 29 | 27 → 28 |
| beaver_creek_westerly | high | 26 → 26 | 26 → 24 | 21 → 21 | 19 → 20 |
| beaver_creek_westerly | extreme | 29 → 29 | 20 → 22 | 26 → 26 | 16 → 16 |
| boulder_chinook | low | 27 → 27 | 27 → 27 | 26 → 26 | 26 → 26 |
| boulder_chinook | moderate | 25 → 25 | 26 → 26 | 26 → 26 | 26 → 26 |
| boulder_chinook | high | 26 → 26 | 25 → 25 | 18 → 18 | 22 → 22 |
| boulder_chinook | extreme | 26 → 26 | 27 → 27 | 25 → 25 | 26 → 26 |
| eagle_gypsum_westerly | low | 30 → 30 | 30 → 30 | 24 → 24 | 24 → 24 |
| eagle_gypsum_westerly | moderate | 28 → 28 | 29 → 29 | 23 → 23 | 26 → 26 |
| eagle_gypsum_westerly | high | 23 → 23 | 26 → 26 | 20 → 20 | 27 → 27 |
| eagle_gypsum_westerly | extreme | 24 → 24 | 24 → 24 [ovt 1 → 0] | 21 → 21 | 17 → 17 |
| eagle_vail_corridor_westerly | low | 1 → 1 | 22 → 22 | 8 → 8 | 23 → 23 |
| eagle_vail_corridor_westerly | moderate | 7 → 7 | 21 → 21 | 9 → 9 | 21 → 21 |
| eagle_vail_corridor_westerly | high | 1 → 1 | 19 → 19 | 8 → 8 | 16 → 16 |
| eagle_vail_corridor_westerly | extreme | 8 → 8 | 17 → 17 | 12 → 12 | 14 → 14 |
| glenwood_springs_westerly | low | 26 → 26 | 39 → 39 | 32 → 32 | 39 → 39 |
| glenwood_springs_westerly | moderate | 33 → 33 | 34 → 34 | 23 → 23 | 22 → 22 |
| glenwood_springs_westerly | high | 32 → 32 | 21 → 21 | 11 → 11 | 14 → 13 |
| glenwood_springs_westerly | extreme | 23 → 23 | 20 → 16 [ovt 1 → 0] | 9 → 7 | 5 → 6 [ovt 1 → 0] |
| malibu_santa_ana | low | 0 → 0 | 0 → 0 | 30 → 28 | 32 → 32 |
| malibu_santa_ana | moderate | 31 → 34 | 35 → 35 | 21 → 23 [ovt 1 → 0] | 22 → 22 |
| malibu_santa_ana | high | 14 → 21 [ovt 1 → 0] | 15 → 20 [ovt 1 → 0] | 3 → 9 [ovt 2 → 0] | 9 → 9 |
| malibu_santa_ana | extreme | 10 → 17 [ovt 1 → 0] | 14 → 14 | 6 → 6 | 6 → 6 |
| paradise_ne_wind | low | 29 → 29 | 29 → 29 | 31 → 31 | 35 → 35 |
| paradise_ne_wind | moderate | 14 → 14 | 26 → 26 | 29 → 29 | 27 → 27 |
| paradise_ne_wind | high | 30 → 30 | 23 → 23 | 24 → 24 | 14 → 14 |
| paradise_ne_wind | extreme | 28 → 28 | 23 → 23 | 18 → 18 | 9 → 9 |
| park_city_windstorm | low | 34 → 34 | 36 → 36 | 36 → 36 | 33 → 33 |
| park_city_windstorm | moderate | 33 → 33 | 35 → 35 | 32 → 34 | 30 → 30 |
| park_city_windstorm | high | 32 → 32 | 22 → 28 | 14 → 14 | 20 → 20 |
| park_city_windstorm | extreme | 32 → 32 | 30 → 31 | 17 → 17 | 11 → 11 |
| rancho_santa_fe_santa_ana | low | 99 → 99 | 100 → 100 | 98 → 98 | 100 → 100 |
| rancho_santa_fe_santa_ana | moderate | 99 → 99 | 99 → 99 | 95 → 94 | 94 → 94 |
| rancho_santa_fe_santa_ana | high | 95 → 95 | 97 → 97 | 90 → 91 | 90 → 90 |
| rancho_santa_fe_santa_ana | extreme | 96 → 95 | 98 → 98 | 83 → 83 | 79 → 81 |
| santa_rosa_diablo | low | 25 → 25 | 27 → 27 | 25 → 25 | 26 → 26 |
| santa_rosa_diablo | moderate | 24 → 24 | 27 → 27 | 24 → 24 | 22 → 22 |
| santa_rosa_diablo | high | 24 → 24 | 22 → 22 | 13 → 13 | 15 → 15 |
| santa_rosa_diablo | extreme | 23 → 23 | 16 → 16 | 13 → 13 | 9 → 9 |
| vail_westerly | low | 0 → 0 | 40 → 40 | 0 → 0 | 39 → 39 |
| vail_westerly | moderate | 8 → 8 | 39 → 39 | 13 → 13 | 28 → 28 |
| vail_westerly | high | 11 → 11 | 29 → 29 | 12 → 12 | 21 → 21 |
| vail_westerly | extreme | 13 → 13 | 32 → 32 | 12 → 12 | 15 → 15 |

**Cost.** Homes served over the 208 runs 5,792 → 5,827 (+35; 16 runs up, 9 down, 183 unchanged; largest drops glenwood_springs_westerly extreme city all 20 → 16, aspen_southwesterly extreme city all 28 → 26, beaver_creek_westerly high city all 26 → 24; largest gains malibu_santa_ana extreme city grass 10 → 17, malibu_santa_ana high city grass 14 → 21, park_city_windstorm high city all 22 → 28). Safety withdrawals 26 → 25, relocations 129 → 113, crews that left the map 8 → 14 (Paradise ×5, Vail ×1: with no exposed-transit decamp on offer, the lawful SEM-10e road decamp found its durable refuge on a road point past the tile edge — out of the simulation by the founder's ruling; homes served in those four runs unchanged within the 6 h). Wall time of the sweep 9,268 s → 10,009 s (+8 %, 4-5 parallel workers, the machine also running other work — indicative only).

**Limits.** The laws read the forecast fields: a crew is safe from the audit only as far as the
eroded P10 is pessimistic. An in-flight *ingress* leg is judged when it is planned (now
cell-exact) and again only when its crew is re-planned; a world change mid-leg (wind dial, a
new ignition) is caught by the per-cell sweep, one step of lead at a time. An emergency
relocation (10a: the crew's own cell already threatened) still takes the least-exposure leg
when no lawful one exists, and a genuinely cornered crew is still reported. Dispatch still
emits one zone per crew plan and the road-egress guard still times the egress to it
(conservative: it may withdraw earlier than a nearer zone would need). The census and
60-scenario benches were not re-run.

## The slow first step (2026-10-12)

Founder, 2026-10-12: *"fix the slow first step."* On the live simulator (App Runner, image
world10 = main 76c4b41) the Park City one-click scenario took **106 s** for its first step;
later steps ~0 s. Probe and records: `scratch_review/first_step_perf_probe.py` (every
one-click scenario × low / moderate / high / extreme, page defaults plus the integrator's
"option 1" — `spot_fire_size` on, `ros_floor_grass_only` off — a non-preset intensity re-lit
at the preset's own point and wind; `--compare` = plan and state identity tick by tick,
`--outcomes` = a knob's measurement, `--table` = first-step seconds).

**For the founder.** The first step is where the crew planner meets every home at once. It
planned three times — two "coating hold" checks (coat now vs. wait until the fire is within
24 h) and then the real plan, which whenever the check says "coat now" (Park City, every
intensity) is *exactly* the first check computed again — and each plan drove every home to
every other home over the road map, walked every
one of those roads square by square against the fire's danger band, and then spent 30 rounds
shuffling the plan in search of one more protected home. On a busy or slower machine the
first plan hit the planner's 50-second emergency cut-off, which also made its answer depend
on machine load. Now the planner computes the same plans much faster: the three plans share
the road and danger-band work, the repeat of the first check is reused instead of recomputed,
the road search builds one map of fastest routes per starting point (and keeps the old search
for the rare near-tie), the danger-band walk reuses the shared start of routes that leave the
same home, and the planner's inner loop does a fraction of the work per candidate. Every one
of the 52 demo runs (13 scenarios × 4 intensities, 6 simulated hours) produces the same plans,
crew moves and outcomes as before, step for step. One rule changed, measured and switchable:
when the plan already protects every home it can reach, the 30 shuffling rounds — which can
only polish the order of the stops — are skipped (homes protected at 6 h 1,831 → 1,830, the
one being still in progress at the cut-off; no crew ever in the danger band, before or after).

**Where the time went** (main 76c4b41, this machine, Park City extreme first step, cProfile
and timers): fire field 6.1 s (spread — untouched here); probe NOW 28.4 s = road routing 7.9 s
(1,677 A\* searches from a cold route cache) + corridor oracles 2.8 s (1,677 cell-exact legs)
+ solver 16.5 s (475 k route evaluations re-walking whole routes, 25 M leg-key roundings, 51 M
`round` calls); probe HOLD 6.9 s; the real plan 17.7 s — the probe NOW plan computed again
(identical inputs, no incumbent). At low / moderate the first plan ran into the 10 × 5 s
wall-clock runaway guard of the deterministic budget (`budget_runaway` true — the plan then
depends on load); Rancho Santa Fe's three plans need 216 + 134 + 92 s to run their counted
budget (445 s), of which 30 ILS shakes cost ~12 s per plan with every one of its 100 homes
already served by the construction.

**What changed** (all plans byte-identical unless named):

1. *The route chain* (`dispatch._Ctx`): per-plan records of every job's deadline thresholds,
   gel window and objective terms (the same expressions, so the same floats); every plan
   point gets a dense id and the chain reads travel / ingress T\* / egress order from id
   matrices filled from the leg memos on first access (first touches unchanged);
   `route_contrib` runs one fused chain (`_run`) that resumes a candidate after the prefix it
   shares with an evaluated route (`states` / `contrib_from`); regret-2 keeps each (job, crew)
   best insertion until that crew's route changes; the local-search scans cache each
   neighbour's objective deltas per pair of current routes (`_ls_table`) and redo only the
   float test against the new threshold. Same scan order, same budget consults, same early
   exits.
2. *A shared leg memo* (`dispatch.LegMemo`, `plan(leg_memo=)`, one per simulator session):
   the road route, travel time and corridor T\* of every exact leg, bound to the cfg, the
   eroded rasters, the zones and the off-map table; reset with the session's bucketed route
   cache; not used by a plan whose own points collide on the leg key.
3. *The probe NOW plan is the plan* when the hold releases with no incumbent (same arguments,
   counted budget, route cache not cleared, no runaway) — `simui._tick_step` step 4.
4. *Road routing* (`roads`): from the 2nd search out of one start node, one exact Dijkstra
   tree per start node, and a goal's tree path is returned when a uniqueness margin proves the
   A\* search returns that very path (the proof is in the module comment "Shortest-path trees":
   A\* without reopening returns a path within the summed heuristic inconsistency of P, and
   every other simple path costs more than that); otherwise the search runs. Per-graph A\*
   tables (edge costs, node trig) and a heuristic memo per search; a `nearest_node` memo; a
   per-graph `route()` memo (a pure function of the graph, the exact endpoints and four cfg
   numbers), so a second session of a scenario — another intensity, another visitor — routes
   nothing.
5. *Corridor T\** (`safety.leg_tstar`): `LegOracle(...).latest_safe_depart_s` without the
   oracle's sample lists — the same resampling, haversine walk, cell lookup, fail-closed
   bounds and cell-exact sub-span walk and run merging — and with `walks=` the road legs over
   one raster share the walk of their common polyline prefix in a chunked trie (69-80 % of a
   leg's vertices repeat an earlier leg from the same start).
6. *A knob* — contracts `dispatch_ils_skip_all_served` ('homes' objective; default OFF =
   byte-identical; README config table; armed in the simulator's sim-defaults block): no ILS
   shakes when the best plan already serves every job in the pool.

**Plan identity.** The deterministic baseline is main 76c4b41 run with the runaway guard lifted
(`solver_budget_s` 10 000, so every plan runs its counted budget to the end — what the budget
law promises); the new code with the same cfg and the knob off. 13 scenarios × 4 intensities ×
24 ticks: **52 / 52 runs identical** — every adopted plan (stops, times, unserved reasons,
objective) and every tick's full `state()` hash. Also pinned: `tests/test_safety.py`
(`leg_tstar` == `LegOracle` bit for bit on seeded legs under every corridor law, with and
without shared walks), `tests/test_roads.py` (tree paths == the A\* search on three real graphs;
an exact tie falls back), `tests/test_dispatch.py` (the fused chain == the per-stop
`eval_route` sums; plans with a fresh, warm and cross-plan `LegMemo` == plans without one;
the knob), `tests/test_simui.py` (the hold-release tick adopts the probe NOW plan, == planning
afresh). Every road query of the 13 scenarios' first four ticks (27,622) returned the old A\*'s
path, and every first-step corridor leg (~67 k, with chord and off-domain variants) the
oracle's T\*.

**The knob, measured** (`--outcomes`, the same 52 runs × 24 ticks, guard lifted; knob off vs
the simulator default with it armed): homes protected at 6 h **1,831 → 1,830**; serviced-stop
departure violations **0 → 0**; crew-overrun ticks **0 → 0**; **51 of 52 runs identical tick
for tick** — when the ILS finds no improvement its plan is the one it started from, so
skipping it changes nothing. The one that differs, Eagle → Vail corridor extreme: its
first-tick probes skipped a tie-break polish (shorter drives), the crews' order shifted, and
its last home was still in progress (assigned, en route) at the 6 h cut-off instead of done at
5 h. What it buys: Rancho Santa Fe's first step ~40 → ~10 s with every other change in place
(every one of its plans serves the whole pool after construction — 30 shakes, 7-12 s per
plan, three plans; they found nothing in any traced plan), and the 40-home low / moderate
runs 2-7 s each. Where a home is left to find (Park City high and
extreme, Aspen extreme: +1 / +2 homes found by the ILS at iterations 0-14 in the traced
first steps) the ILS runs exactly as before.

**Later steps are not slower.** The 24-tick identity runs (same inputs, both versions under
6 parallel workers): the 1,196 later ticks sum 973 s → 186 s (knob off) / 119 s (simulator
defaults); the slowest later tick 96.5 s → 9.9 s / 1.5 s (re-plan ticks of the guard-lifted
baseline). A quiet tick stays ~0.05-0.25 s; the memos that outlive a plan (the session's leg
memo, the per-graph trees, route and snap memos) leave fewer objects for the garbage
collector than main's global path memo did (Rancho Santa Fe after the first step: +108 k
tracked objects, a full collection 92 ms, vs +160 k and 123 ms on main).

**First step, before → after** (this machine, every run a fresh process, nothing else running for the after column; *before* = main at production settings — the 50 s runaway guard live, as on the server — two runs at a time, `scratch_review/first_step_perf_timing_before.jsonl`; *after* = this change at the simulator defaults, one at a time, `first_step_perf_timing_after.jsonl`; *live est.* = after × 2, the ratio measured between the App Runner instance and this machine; *(guard)* = the runaway guard cut a plan short, so that plan depended on machine load — the new code never trips it):

| Scenario | Intensity | Before (s) | After (s) | After, live est. (s) | Later steps after (max s) |
|---|---|---|---|---|---|
| aspen_southwesterly | low | 119.4 (guard) | 5.8 | 12 | 0.05 |
| aspen_southwesterly | moderate | 119.6 (guard) | 6.0 | 12 | 0.05 |
| aspen_southwesterly | high | 88.4 | 8.3 | 17 | 0.04 |
| aspen_southwesterly | extreme | 64.0 | 7.5 | 15 | 0.05 |
| basalt_carbondale_southwesterly | low | 117.4 | 8.8 | 18 | 0.05 |
| basalt_carbondale_southwesterly | moderate | 108.9 | 8.4 | 17 | 0.07 |
| basalt_carbondale_southwesterly | high | 97.5 | 8.2 | 16 | 0.05 |
| basalt_carbondale_southwesterly | extreme | 83.8 | 7.9 | 16 | 0.05 |
| beaver_creek_westerly | low | 121.0 | 7.6 | 15 | 0.04 |
| beaver_creek_westerly | moderate | 97.6 | 7.3 | 15 | 0.05 |
| beaver_creek_westerly | high | 64.5 | 7.5 | 15 | 0.05 |
| beaver_creek_westerly | extreme | 56.5 | 7.1 | 14 | 0.07 |
| boulder_chinook | low | 34.3 | 4.0 | 8 | 0.04 |
| boulder_chinook | moderate | 32.9 | 4.2 | 8 | 0.04 |
| boulder_chinook | high | 29.9 | 4.2 | 8 | 0.03 |
| boulder_chinook | extreme | 29.9 | 4.2 | 8 | 0.05 |
| eagle_gypsum_westerly | low | 73.1 | 7.7 | 15 | 0.05 |
| eagle_gypsum_westerly | moderate | 69.2 | 7.5 | 15 | 0.06 |
| eagle_gypsum_westerly | high | 47.9 | 7.4 | 15 | 0.03 |
| eagle_gypsum_westerly | extreme | 62.3 | 7.5 | 15 | 0.05 |
| eagle_vail_corridor_westerly | low | 20.0 | 6.2 | 12 | 0.04 |
| eagle_vail_corridor_westerly | moderate | 22.9 | 6.1 | 12 | 0.04 |
| eagle_vail_corridor_westerly | high | 22.0 | 6.1 | 12 | 0.07 |
| eagle_vail_corridor_westerly | extreme | 22.3 | 6.0 | 12 | 0.05 |
| glenwood_springs_westerly | low | 121.9 (guard) | 6.8 | 14 | 0.03 |
| glenwood_springs_westerly | moderate | 115.8 (guard) | 6.6 | 13 | 0.03 |
| glenwood_springs_westerly | high | 51.9 | 7.3 | 15 | 0.04 |
| glenwood_springs_westerly | extreme | 41.3 | 6.9 | 14 | 0.06 |
| malibu_santa_ana | low | 0.2 | 0.2 | 0 | 0.01 |
| malibu_santa_ana | moderate | 87.4 | 5.2 | 10 | 0.06 |
| malibu_santa_ana | high | 42.0 | 6.4 | 13 | 0.06 |
| malibu_santa_ana | extreme | 40.3 | 6.0 | 12 | 0.05 |
| paradise_ne_wind | low | 109.1 | 10.1 | 20 | 0.05 |
| paradise_ne_wind | moderate | 119.3 | 12.1 | 24 | 0.09 |
| paradise_ne_wind | high | 81.5 | 9.3 | 19 | 0.11 |
| paradise_ne_wind | extreme | 67.7 | 8.9 | 18 | 0.09 |
| park_city_windstorm | low | 125.6 (guard) | 8.4 | 17 | 0.06 |
| park_city_windstorm | moderate | 116.0 | 7.4 | 15 | 0.07 |
| park_city_windstorm | high | 46.2 | 7.1 | 14 | 0.07 |
| park_city_windstorm | extreme | 45.6 | 7.3 | 15 | 0.06 |
| rancho_santa_fe_santa_ana | low | 121.7 (guard) | 8.4 | 17 | 0.14 |
| rancho_santa_fe_santa_ana | moderate | 144.9 (guard) | 8.6 | 17 | 0.06 |
| rancho_santa_fe_santa_ana | high | 153.8 (guard) | 8.3 | 17 | 0.06 |
| rancho_santa_fe_santa_ana | extreme | 153.9 (guard) | 8.6 | 17 | 0.17 |
| santa_rosa_diablo | low | 63.0 | 5.6 | 11 | 0.04 |
| santa_rosa_diablo | moderate | 58.8 | 5.5 | 11 | 0.09 |
| santa_rosa_diablo | high | 51.0 | 5.5 | 11 | 0.10 |
| santa_rosa_diablo | extreme | 39.1 | 6.0 | 12 | 0.05 |
| vail_westerly | low | 121.4 (guard) | 6.6 | 13 | 0.03 |
| vail_westerly | moderate | 107.3 | 6.3 | 13 | 0.04 |
| vail_westerly | high | 98.8 | 6.3 | 13 | 0.05 |
| vail_westerly | extreme | 97.4 | 6.2 | 12 | 0.04 |

Sum over the 52 runs **4,028 s → 359 s**; slowest **153.9 s → 12.1 s**; 50 of 52 first steps under 10 s (Paradise low 10.1 s, moderate 12.1 s: a 5 s fire field and two full ILS solves). Park City extreme, the founder's case: 45.6 s → 7.3 s here (106 s measured live → ~15 s estimated). The same code timed under another agent's parallel load (~70 % of the CPU busy) read 0-41 % slower (median 13 %), the fire-field-only runs included — machine load, not the planner.

**Limits.** The fire field (`spread.ensemble_arrival`, untouched here — the crown-fire model
was being built in `spread.py` in parallel) is 2-6 s of every first step. Where homes remain to
find (Paradise and Basalt at low / moderate) both coating-hold probes still run their full
ILS, ~2 s each. A freshly started server routes each scenario once (the per-graph route memo
then serves every later session of it — any intensity, any visitor). The live figures are
estimates (× 2, the ratio measured between the App Runner instance and this machine).
