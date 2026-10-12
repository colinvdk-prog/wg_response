# wgresponse

Wildfire Guardian's operational response engine — the software side of the **GraphGel
Events** product. It watches live fire feeds, forecasts fire propagation, computes
threat ETAs for every protected client property, drives a 72h/24h alert ladder, and
dispatches GraphGel crews under hard safety rules (30-minute deploy, crews clear
>= 30 minutes before the pessimistic fire arrival, verified safe corridors in and out).

Pipeline in one line:

```
live fire feeds -> spread forecast (ensemble) -> per-client threat ETAs
                -> alert ladder (NOTICE_72H / WATCH / EXTREME_24H)
                -> crew dispatch (deadline-TOPTW) under P10 safety rules
```

This is **defensive emergency-response software** operated by an authorized
fire-protection company for its own contracted clients and crews.

The single source of truth for types, units, and safety semantics is
[`wgresponse/contracts.py`](wgresponse/contracts.py) — read its docstring first.
Design narrative: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Wiring into the
existing WG estate: [`docs/INTEGRATION.md`](docs/INTEGRATION.md). Everything still
unresolved: [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md).

## Quickstart

Python 3.10+ stdlib only for core logic (optional extras degrade gracefully).
Run everything from the repo root.

```bash
# 1. Tests (no network, no third-party deps required beyond pytest)
py -3 -m pip install pytest
py -3 -m pytest

# 2. Deterministic end-to-end simulation (synthetic fire + clients + crews)
py -3 -m wgresponse.cli simulate --scenario canyon_wind_event --hours 48 --seed demo

# 3. Ops console + beacon API on localhost (demo mode: mock feeds, open auth)
py -3 -m wgresponse.cli serve --demo
# then open http://127.0.0.1:8877/console

# 4. One-shot live feed pull (network; respects feed licensing below)
py -3 -m wgresponse.cli pull-feeds --bbox -121.0,32.5,-114.0,36.0

# 5. THE OPERATOR DAEMON (Sprint 2): poll live feeds over an AOI every 5 min,
#    alert + dispatch, persist state (restart re-fires nothing already sent),
#    serve the console + /healthz off the live loop. Ctrl-C stops cleanly.
py -3 -m wgresponse.cli watch --aoi "-121.0,32.5,-114.0,36.0" --state-dir state
# AOI can also be a zones JSON file carrying safe_zones for dispatch:
#   {"zones": [{"name": "sierra", "bbox": [-121,38.8,-120.7,39.2]}],
#    "safe_zones": [{"zone_id": "Z1", "lat": 39.1, "lon": -121.0}]}
# FIRMS hotspots join the stack when env FIRMS_MAP_KEY is set (else a loud
# skip note); terrain is live LANDFIRE when the decode extras are installed,
# else SyntheticTerrain with a LOUD rehearsal-grade warning. The LFPS job API
# requires an Email parameter on submit: watch sends env LFPS_EMAIL when set,
# else ops@thewildfireguardian.com.
#
# TWO WORLDS — A LIVE MAP AND A TEST MAP (2026-09-27; contracts SAFETY
# SEMANTICS 16; founder: "we need a test map and a live map so all
# simulations or drills do not impact real conditions"). The daemon runs two
# fully isolated worlds in one process:
#   LIVE  real conditions only — feed incidents, the real registry, real
#         crews (beacons), the real ladder, plan, notifier and state file. It
#         accepts NO seeded fire (POST /api/seed -> 409, pointing at the test
#         map), and nothing a drill does changes one byte of its cycles,
#         /api/state, /api/plan, /healthz, summary, notifications or state
#         files (pinned in tests/test_worlds_isolation.py).
#   TEST  drills and simulations — the live incidents and forecasts
#         READ-ONLY (the live cycle's own snapshot, never recomputed) ∪
#         SEEDED fires (W1, SAFETY SEMANTICS 12: the same engine path as a
#         feed incident), with its OWN ladder, OWN virtual crews and OWN
#         plan; everything it emits goes to the DRILL LOG (never a client);
#         it persists under <state-dir>/test_world/ (S3: <prefix>/test_world/).
#         IDLE (zero cost) until a drill starts — the first seed or a test
#         crew reset/placement clones the live crews + ladder + plan ("what
#         would we do right now"); each world then evolves alone.
# The TEST routes (authed like every write path — X-Internal-API-Key unless
# WGRESP_DEV_OPEN=1):
#   GET    /api/test/state           the test world's state (the /api/state
#                                    keys + world, banner, drill, seeds,
#                                    live_crews, drill_log)
#   POST   /api/test/seed            {"lat": 39.626, "lon": -106.936,
#                                     "intensity": "high", "wind_dir_deg": 240,
#                                     "note": "drill", "created_by": "colin"}
#                                    -> 201 {"incident_id": "seeded:<uuid>",
#                                    "seed", "drill"} (400 bad body / outside
#                                    the AOI, 409 at 12 active)
#   GET    /api/test/seed[/<id>]     seeds (active + recently retired): weather
#                                    mode, last-cycle domain, pair states
#   DELETE /api/test/seed/<id>       retire (?by=name): absent from the next
#                                    test cycle; its pairs take the absence law
#                                    (-> CLEARED after clear_cycles while every
#                                    live feed is healthy; frozen while any is
#                                    blind)
#   POST   /api/test/crews/reset     re-clone the virtual crews from the live
#                                    roster now ({"by"?})
#   POST   /api/test/crews/place     {"crew_id", "lat", "lon", "status"?,
#                                    "name"?} stage one virtual crew
#   POST   /api/test/reset           wipe the test world back to IDLE
#   GET    /api/world/summary?world=test   the test map's read model
#   GET    /api/seed[/<id>]          read-only alias of the test list (one
#                                    release); POST / DELETE /api/seed -> 409
# Weather is LIVE at the fire (the test world's own NWS instance) unless the
# seed overrides it: "intensity" (the simulator's low/moderate/high/extreme
# presets) or "weather" ({wind_speed_mps, rh_pct, temp_c, wind_gust_mps?})
# sets the conditions, "wind_dir_deg" fixes the direction; both = fully
# synthetic, no network. A seeded fire is a point incident spread from its
# point every cycle (no ground truth yet — W1.1 makes it grow); a seed /
# retire / crew action persists the TEST namespace at once and re-runs the
# drill at once (evaluating only the seeded fires' pairs — a live pair's
# ladder moves only with the live cycles); it never wakes or delays the live
# loop. Seeds auto-retire once their horizon passes with nothing threatened.
# THE PAGE: /world has a LIVE | TEST switch in the header (LIVE by default;
# /world?world=test opens the test map): the TEST map carries an amber banner
# "TEST MAP — drills and simulations. Nothing here affects real conditions,
# crews or clients.", an amber frame, the "Seed a fire" control (click the
# map -> intensity -> Seed; retire on every seeded fire), "Reset test crews
# to live", "Reset test map", the drill log, the virtual test crews in amber
# and the live crews ghosted read-only. /console stays LIVE-only; the
# simulator (/simulator) is a sandbox and carries a TEST chip.
# 5b. THE NATIONAL WORLD VIEWER (W6 thin, docs/LIVE_WORLD_PLAN.md): the same
#     daemon over the contiguous US with demo homes on every simulator
#     preset, and a zoomable map at /world (national markers -> regional
#     perimeters + P50 rings -> local plan legs + client cards).
py -3 -m wgresponse.cli watch --aoi data/aoi_conus.json --registry mock-presets \
    --dry-notify --gust-blend 0.5 --grass-floor 0.10 --floor-gate \
    --model-error 0.71 --extreme-regime --fail-closed --ember-radius 200 \
    --p50-bound --solver-evals 20000 --phi-vector --state-dir state
# then open http://127.0.0.1:8877/world (the key prompt appears only on a 401).
# --registry mock-presets = --preset-homes N (default 40) demo homes on EACH of
# the 13 simulator presets, placed by the simulator's own WUI placement (city-
# limits polygon, 300 m driveway rule), so live WFIGS fires near Vail / Aspen /
# Park City / Boulder / Malibu / Paradise / Santa Rosa / Rancho Santa Fe build
# spread domains and every other fire stays a marker; `--registry mock` (12
# homes around the AOI centre) is unchanged. GET /api/world/summary (authed,
# read-only) is the page's read model: counts, the ranked "incidents
# threatening clients" list, each incident's domain facts or the cycle's
# "why no domain?" line, display perimeters and P50 rings — built once per
# cycle (?since=<t_s> answers {"unchanged": true}).
# 5c. CONTAINED FIRES + CONFIRMED PERIMETERS (SAFETY SEMANTICS 13, founder
#     ruling 2026-09-27; both default OFF, add to either command above):
#       --drop-contained        a fire the feed reports contained (a source at
#                               >= 100 %, or a containment / control / out
#                               timestamp; no source below 100 %) and not
#                               growing leaves the map before any domain — its
#                               pairs take the absence law; /api/state gains
#                               removed_contained + removed_incidents and the
#                               /world header counts them. It comes back when
#                               a source reports < 100 % or it grows.
#       --confirmed-perimeters  a polygon whose fresher record reports the
#                               same size (within 2 %) is seeded at that report
#                               instead of being spread forward from its own
#                               age; a growing fire (record larger) keeps the
#                               legacy seeding. Measured in
#                               docs/LIVE_WORLD_PLAN.md (2026-09-27 section).
# 5d. THE OFF-DOMAIN HORIZON IS PER FIRE (SAFETY SEMANTICS 15, founder rule
#     2026-09-27; default OFF, add to either command above WITH --fail-closed):
#       --offmap-per-fire       arms corridor_offraster_per_fire and sets
#                               corridor_offraster_margin_min = 60: a fire
#                               whose fresh WFIGS behaviour report says it is
#                               not rapid extrapolates beyond its domain at its
#                               own P90 front speed (floor 0.28 m/s, ceiling
#                               1.7 m/s), rapid / unknown fires keep 1.7 m/s,
#                               and a bounded corridor sample needs a 60-min
#                               crossing margin. /api/state gains offmap_rule
#                               + an offmap block per incident; /world shows
#                               it. Inert without --fail-closed. Measured in
#                               docs/LIVE_WORLD_PLAN.md (ticket 1.5 section).
# 5d'. FEED SANITY (SAFETY SEMANTICS 18, 2026-09-27 audit; default OFF, add
#     to either command above; the belief side needs --fire-belief):
#       --feed-sanity           a perimeter or size the feed cannot have meant
#                               (future / pre-fire own time, a polygon under
#                               10 % of its record's size, a drop beyond
#                               noise, an area figure off its geometry) never
#                               teaches the belief; re-stamped polygons count
#                               once a day; a collapsed polygon seeds the
#                               fire's last plausible one (remembered 72 h,
#                               in-process). Measured in
#                               docs/review/feed_sanity_2026-09-27.md.
# 5e. SPREAD READS THE LOCAL WEATHER FORECAST (SAFETY SEMANTICS 17, founder
#     2026-09-27: "ensure that fire spread includes local weather forecast";
#     default OFF, add to either command above):
#       --weather-grid-km KM    besides the fire-centroid forecast (still the
#                               primary: extreme-regime posture, gel windows),
#                               every domain fetches the NWS forecast at the
#                               nodes of a global lat/lon lattice at KM
#                               spacing inside it (coarsened in sqrt-2 steps
#                               until within --weather-points-max N,
#                               default 16), and
#                               every cell spreads on its nearest point's
#                               forecast. A failed node falls back to the
#                               centroid (noted); a failed centroid is the
#                               usual provider failure. Seeded fires keep one
#                               point. NWS points mapping cached, gridpoints
#                               for 30 min. /api/state gains weather_grid + a
#                               weather block per incident; /world draws the
#                               points. Measured in docs/LIVE_WORLD_PLAN.md
#                               ("Spread reads the local weather forecast").

# 6. THE INTERACTIVE SIMULATOR (docs/SIMULATOR_DESIGN.md): thirteen real WUI
#    locations on cached LANDFIRE tiles, click-to-ignite fires, the REAL
#    engine every tick (ensemble spread -> alert ladder -> dispatch ->
#    per-tick SAFETY AUDIT), and the live safety-threshold slider (egress
#    buffer). Localhost demo instrument: auth is dev-open unless
#    WGRESP_API_KEY is set.
py -3 -m wgresponse.cli simui --open
# opens http://127.0.0.1:8878/simulator; self-test: `simui --smoke` exits 0
# iff the safety audit reports 0 violations after 3 live engine ticks.
```

Console screenshot: *(placeholder — capture from `cli serve --demo` once the console
lands)*

![ops console placeholder](docs/img/console_placeholder.png)

### Live extras

Core is **stdlib only** — everything above runs on a bare Python install (pytest is
the sole dev dependency). Exactly two live paths need optional third-party packages,
listed in [`requirements.txt`](requirements.txt):

```bash
py -3 -m pip install -r requirements.txt   # tifffile + numpy + imagecodecs (+ boto3)
```

- **`LfpsTerrain` fresh-GeoTIFF decode** needs `tifffile numpy imagecodecs` — real
  LFPS tiles are LZW-compressed, so `tifffile` alone cannot decode them. The adapter
  probes importability **before** submitting an LFPS job (a job costs 90–180 s; a
  missing codec must fail in milliseconds, not after the download) and raises a
  `RuntimeError` naming the exact pips. The downloaded job zip is cached to disk
  **before** decode, so a decode failure never wastes the job — the next call
  re-decodes the cached zip without re-submitting. Cached/ascii tiles stay dep-free.
- **`DynamoClientRegistry` live scans** need `boto3` (lazily imported; tests inject
  `scan_fn`).

## Module map

| Module | Owns |
|---|---|
| `wgresponse.contracts` | THE LAW: types, units, safety semantics, config. Never import anything from a sibling module here. |
| `wgresponse.geo` | Haversine/bearing/destination, `GridSpec`/`Raster` helpers, polyline sampling, bbox, morphological min (`erode_min`). |
| `wgresponse.fuels` | FBFM40 fuel-model table (Scott & Burgan RMRS-GTR-153), burnability, Simard dead-fuel moisture, live-moisture season defaults. |
| `wgresponse.rothermel` | Rothermel/Albini surface ROS, wind adjustment factor, Anderson (1983) ellipse L/B, directional ROS. Imperial units internal only. |
| `wgresponse.spread` | Time-dependent 16-neighbor MTT Dijkstra arrival grids, ensemble perturbation, per-location `ThreatEta` lookup. |
| `wgresponse.feeds` | WFIGS / FIRIS / CAL FIRE / FIRMS adapters + Watch Duty Enterprise stub + deterministic mock; IRWIN-id fusion. |
| `wgresponse.weather` | NWS gridpoint + Open-Meteo adapters + mock (`WeatherProvider` protocol). |
| `wgresponse.terrain` | LANDFIRE LFPS job-API tiles + synthetic terrain; dependency-free ASCII tile cache. |
| `wgresponse.clients` | `DynamoClientRegistry` (wg-consumer-profiles) + mock registry + `preset_client_registry` (the watch daemon's `--registry mock-presets`: N demo homes on every simulator preset via the simulator's own WUI placement). |
| `wgresponse.crews` | Phone-GPS beacon ingest, crew state, staleness. |
| `wgresponse.alerts` | Alert ladder state machine with hysteresis (`ALERT_TRANSITIONS` enforced). |
| `wgresponse.threat` | `ThreatMonitor.cycle()` — the full orchestration loop (feeds ∪ seeded incidents → domains → ladder → dispatch); `lock_live()` makes it the LIVE world's monitor (seeds refused) and every cycle records a read-only `last_snapshot` (`WorldSnapshot`) the TEST world builds on. |
| `wgresponse.seeded` | W1 seeded incidents (SAFETY SEMANTICS 12): the thread-safe `SeededIncidentStore` (create / retire / list / persistence rows, 12-active cap), `seeded_forecast` (the per-incident weather override through the weather seam), `parse_seed_request` (the `POST /api/test/seed` body), `SeedSafeNotifier` (a seeded fire's notifications go to the drill log, never a client), `DrillOnlyNotifier` (the TEST world's notifier: no client channel at all), `LiveWorldRefused`. |
| `wgresponse.offmap` | The per-fire off-domain horizon (SAFETY SEMANTICS 15, `corridor_offraster_per_fire`): the feed's behaviour status (`status_for` → known / rapid), the OR-combine seam for other status sources (`merge_status`), the raw-P10 boundary front speed (`front_speed_stats`: P90 + max) and each domain fire's `OffDomainHorizon` (speed, reason, crossing margin) for `dispatch.plan(offmap=...)`. |
| `wgresponse.worlds` | TWO WORLDS (SAFETY SEMANTICS 16): `TestWorld` — the test map: the live cycle's snapshot read-only ∪ seeded fires through the same `ThreatMonitor.cycle()` (live domains shared, never recomputed), its own ladder (`DrillOnlyNotifier`), `VirtualCrewTracker` crews, plan and `test_world/` state namespace; IDLE until a drill starts; one worker thread that yields to the live loop; `TestTerrainOverlay`; `split_legacy_seeds` (the migration of a pre-two-worlds live file). |
| `wgresponse.wxfield` | Local weather across a spread domain (SAFETY SEMANTICS 17, `weather_grid_km`): the global lat/lon lattice nodes inside a domain (`lattice_points`, coarsened in √2 steps to fit `weather_points_max`), the nearest-point zone of every cell (`zone_map`, cached), the `WeatherField` (`build_field`: the centroid forecast + each node's, a failed node falling back to the centroid) that `spread.ensemble_arrival(weather_field=...)` prices per (zone, hour), and the `/api/state` + world-summary `weather` block (`field_row`). |
| `wgresponse.safety` | P10 departure deadlines, eroded-grid corridor checks, egress-to-safe-zone verdicts. |
| `wgresponse.dispatch` | Deadline-TOPTW planner: regret-2 insertion + ILS, commitment/churn stability. |
| `wgresponse.travel` | Tier-0 travel time (haversine x circuity / class speed x fire-traffic factor). |
| `wgresponse.notify` | Notifier protocol; SES/SMS stubs, log notifier. |
| `wgresponse.sim` | Seeded synthetic scenarios + full-loop sim harness (the acceptance test and the demo). |
| `wgresponse.server` | Stdlib HTTP: `POST /api/beacon`, `GET /api/state`, `GET /api/plan`, `GET /console`; `/healthz` carries last-cycle age + degraded + cycle count; `/simulator` + `/api/sim/*` session API (LRU-capped, per-session ticker; `POST /api/sim/<id>/crew` = setup-phase crew placement, road-snapped, 409 once a fire is lit); the TEST map's `/api/test/*` routes and `/api/world/summary?world=test` routed to the watch daemon (404 elsewhere); `/api/seed` = GET alias of the test list, POST/DELETE 409 (the live world refuses). `X-Internal-API-Key`, fail-closed. |
| `wgresponse.simui` | `SimSession`: the interactive simulator over the REAL engine — thirteen cached LFPS WUI tiles (five originals, Rancho Santa Fe, the six Vail→Aspen corridor communities, the whole-valley Eagle→Vail corridor on one ~60 km @ 200 m tile), seeded WUI client placement, setup-phase crew placement on the road network (`place_crew` / `reset_crews`: nearest road node within 2 km, escape targets stay the safe zones, replayed by reset), perimeter-seeded ensembles, live safety-slider re-plans (in-flight deploys re-checked, never grandfathered), per-tick safety audit. |
| `wgresponse.worldview` | The W6 thin world viewer's read model behind `GET /api/world/summary`: per-incident domain facts (clients, earliest client P10/P50), the cycle's "why no domain?" note, display-simplified perimeters, P50 rings traced from each domain's ensemble, counts and the ranked "incidents threatening clients" list — built once per cycle, read-only (page: `console/world.html` at `/world`). |
| `wgresponse.persist` | `StateStore`: atomic-write (fsync'd) versioned JSON persistence for alert records, the notified ledger, the incumbent plan, and watch metadata — restart re-fires nothing already sent (an applied-but-never-sent active alert IS re-fired: re-notify-ward); ANY corrupt state — including non-UTF-8 bytes and type-corrupt fields — loads fresh with a loud note, never a crash. The TEST world (SAFETY SEMANTICS 16) keeps its own file under `<state-dir>/test_world/` (+ `<prefix>/test_world/state.json` on S3) with the additive `seeded_incidents` / `virtual_crews` / `drill_log` keys; the LIVE file never carries them (a pre-two-worlds live file that does is migrated to the test world at load). |
| `wgresponse.cli` | `simulate \| forecast \| plan \| serve \| pull-feeds \| watch \| simui` (watch = the live operator daemon: AOI-bounded feeds, per-cycle persistence, console + healthz off the live loop, plus the isolated TEST world and its `/api/test/*` seeding API; simui = the interactive simulator UI). |

## Config knobs (`contracts.ResponseConfig`)

Every tunable lives in `ResponseConfig`; nothing hardcodes these. Defaults below are
the shipped values.

### Alert ladder

| Knob | Default | Meaning |
|---|---|---|
| `notice_h` | 72.0 | First advance notice when P50 arrival within this many hours. |
| `extreme_h` | 24.0 | EXTREME alert when P50 arrival within this. |
| `extreme_p10_h` | 12.0 | EXTREME also when the pessimistic P10 arrival is within this. |
| `clear_cycles` | 2 | Consecutive clear evaluations required to downgrade. |
| `clear_margin_h` | 6.0 | Headroom beyond the threshold needed to count as "clear". |

### Crew safety (operational hard rules)

| Knob | Default | Meaning |
|---|---|---|
| `deploy_min` | 30.0 | Gel application time on site (minutes). |
| `egress_buffer_min` | 30.0 | Crew must be clear of the property this many minutes before **P10** arrival. |
| `route_margin_min` | 56.0 | Margin required at every corridor sample point (minutes) on CHORD legs. Calibrated PAIR with `circuity` (2026-08-02): (1.35, 60) -> (1.7, 56) preserves the old end-of-leg conservatism at the representative 8.5 km chord leg; the two knobs move together. |
| `corridor_erosion_m` | 500.0 | Erosion radius applied to the P10 grid before corridor checks. |
| `corridor_erosion_euclidean` | False | CORRIDOR HONESTY: True = true-metric disc erosion. The legacy square corner-reaches 1.53× the stated radius and imports phantom arrivals onto clean roads (founder-arithmetic trace 2026-07-29) — but erode_min is a MIN filter, so the honest window is also a relaxation (the diagonal slop was absorbing the P10-vs-truth quantile gap: 2 crews caught at 8 crews with disc-500). **Calibrated clean operating point: disc + `corridor_erosion_m=550`** — 0 violations/overruns at 4 AND 8 crews, +4/+10 belt homes vs legacy (R600 confirms with less gain). Default off = legacy square. |
| `route_alternatives` | 1 | ⚠ EXPERIMENTAL — REJECTED AS BUILT (screen A/B 2026-07-29): candidate routing (fastest / widest-clock / avoid-hot) with safety-first egress ordering measured −1 to +4 homes while putting 3 extra crews in burned cells (max-latest-depart zone choice sends crews on longer drives across a moving front; latest-safe-DEPART is not drive exposure). Keep 1 (legacy fastest-only) until the choice rule is reworked to judge in-transit exposure. |
| `corridor_erosion_tiered` | False | FUEL-TIERED EROSION (founder 2026-07-30): each SOURCE cell's P10 arrival projects over a radius set by its own fuel class (per-tier euclidean discs, min-combined) — grass keeps a wide position-error halo, chaparral gets a flame-standoff-scale halo, timber a small one. The uniform radius was doing three jobs at once. Off = uniform legacy erosion, byte-identical. Consumed via `spread.tiered_eroded_p10`; dispatch stays fuel-blind through the `eroded_rasters` plan seam. |
| `corridor_erosion_fast_m` | 550.0 | Tiered erosion: FAST tier radius — GR/GS grass classes plus any unclassified burnable (fail-safe into the widest tier). Position error dominates: wind-driven grass moves hundreds of meters per tick. |
| `corridor_erosion_shrub_m` | 200.0 | Tiered erosion: SH shrub/chaparral tier radius — slow spread (small position error) but the longest surface flame lengths (~112 m standing standoff at high wind), so the halo is flame-standoff-scale. |
| `corridor_erosion_slow_m` | 150.0 | Tiered erosion: TU/TL/SB timber+slash tier radius — slow spread, short flames; the residual hazards there (snags) are temporal, owned by the cold-black windows, not spatial. |
| `escape_hysteresis_margin_min` | 0.0 | ESCAPE-TARGET HYSTERESIS (founder churn fix): once fleeing, a crew keeps its adopted escape target unless the proposed one improves the routed destination margin by more than this many minutes (clean-route always beats burned-route). The caught-crew dissection showed per-tick re-picking zigzagged a crew between near-equal escape rays for ~5.5 h. 0 = legacy always-switch, byte-identical. The SIMULATOR defaults to 20 (calibrated stack flip 2026-07-30; explicit override wins). |
| `escape_proactive_lead_min` | 0.0 | SEM-10e PROACTIVE PRE-POCKET ESCAPE (moderate-wind census attribution 2026-08-02): an IDLE crew whose cell the eroded-P10 front reaches within (egress buffer + sweep lead + this many minutes) decamps NOW — but only along a route that is clean at arrival (not burned, never crossing a nominal-passed cell) to a zone that outlives the same horizon (never a least-bad hop; no qualifying exit → unchanged behavior). The paradise 8000 m trace measured the imminent flag firing 2-3 ticks AFTER the last lawful road out of the SW pocket burned over — 4 crews then idled 11 audit ticks in the flaming front. 0 = off, byte-identical. The SIMULATOR defaults to 120 (explicit override wins). |
| `escape_proactive_lawful_only` | False | SEM-10e LAWFUL-ONLY (roads inspection 2026-09-25): the proactive decamp takes only a fully lawful route (not burned, eroded-P10 margin ≥ 0 at every pass time); the exposed-transit fallback — audit-counted as imminent crew-overrun rows since the 2026-09-03 audit change — is skipped, so a voluntary move never buys 10c rows. False = legacy, byte-identical. The SIMULATOR arms it for EXTENDED fire-zone sessions only (their extended road graph opens exposed routes beyond the 18 km core; explicit override wins); since 2026-10-09 `crew_egress_judged` (SAFETY SEMANTICS 20b-iii) applies the same rule on every tile (the standard Eagle graph offered such a route: an extreme city-weather decamp rode 915 s of exposed transit and was audited in the band). |
| `escape_road_snap_m` | 0.0 | SEM-10d AREA-ESCAPE ROAD SNAP (roads inspection 2026-09-25): area-escape proposals snap to the road node nearest a safe ray sample within this many metres and the flee becomes road-bound (a bearing with no road in reach is proposed only when none reaches a road) — no straight spur into open ground. 0 = off, byte-identical; NOT armed by the simulator: the 8-session A/B was clean (straight escape km −75…−99 %, 0 violations, docs/review/roads_inspection_2026-09-25.md §5) but armed as a default it produced two imminent-while-egressing rows in the pinned Camp-Fire-scale extended run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_overrun_ever; ticks 69 and 106, 410 and 1,158 min inside the eroded-P10 band, nominal front ≥ 48 h away; relocations 3 → 7, served unchanged; scratch_review/snap_campfire_diag.py) — the road-bound flee routes through pessimistic exposure the open-ground escape avoided. Superseded in the simulator by `escape_road_network` (the snap now applies only in that knob's no-passable-road fallback). |
| `escape_road_network` | False | SEM-10d ROAD-NETWORK ESCAPE (founder "rework the crews fleeing", 2026-09-26; docs/review/escape_roads_2026-09-26.md): a crew that must flee with no catalog zone lawfully reachable (zone-first rule unchanged) SEARCHES THE ROAD GRAPH — a time-dependent Dijkstra at the crew's escape speed where a sample the nominal front crosses inside its cold-black window makes an edge impassable and samples inside the eroded-P10 imminent band cost exposure seconds — and drives, roads end to end, to the road node ≥ 2 km out that is lawful at arrival, then has the least exposure (5-minute steps; zero exposure always first), then the longest safety horizon (eroded-P10 arrival minus arrival, capped by `escape_road_horizon_cap_h`), then the shortest drive; the catalog zones compete under the same rule and win ties on those safety steps, and the crew drives exactly the searched path (the sweep re-judges its remaining leg). When neither SEM-10e zone rule yields a move, the proactive decamp also takes a fully lawful road path to a durable road node (a crew parked at a road point after an area escape otherwise waited for the imminent law). No passable road escape (cornered / off the network) → the legacy ray logic with a loud note. Diagnosis: the ray and snap escapes both ranked lawful destinations by (exposure, drive time) only — the pinned Camp-Fire-scale run was clean by a 4 s drive-time tie between an infinite-horizon ray end and a 26.6 h one, and the snap parked crews at 10–27 h nodes the band later overtook. False = off, byte-identical; the SIMULATOR arms it (explicit override wins). |
| `escape_road_horizon_cap_h` | 24.0 | SEM-10d ROAD-NETWORK ESCAPE: the safety horizon at which a destination counts as fully safe — beyond it the nearer node wins, so crews are not sent to the far edge. Short caps park crews where the growing band overtakes them (pinned Camp-Fire-scale run: 6 h → 1 imminent row, 9 relocations; 12 h → 0 rows, 7 relocations; 24 h → 0, 4; 48 h → 0, 3). Read only when `escape_road_network` is on. |
| `transit_cold_black_by_class` | False | TRANSIT-HAZARD STAGE 1: per-fuel-class cold-black windows for SEM-10 ROUTE SELECTION (`fuels.cold_black_minutes`: grass 5–10 min, grass-shrub 10–20, shrub 20–30, timber-understory 45, timber-litter 60, slash/unknown 90 — grass opens, timber stricter; the flat legacy 45 min is numerically the residence time of a 1000-h log). The 10c audit consults it only under `old_black_refuge` (the window is what makes black OLD); otherwise never. False = flat 45 min, byte-identical. |
| `old_black_refuge` | False | OLD BLACK IS A REFUGE (founder ruling 2026-09-26, verbatim: *"old burned ground does not count as a violation"*; contracts SAFETY SEMANTICS 10; docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map are out (2026-09-26)"): a cell whose nominal arrival is at least its cold-black window in the past (the transit window above) is OLD BLACK — burned out, it cannot burn again. The 10c audit counts no row for a crew on it (neither burned nor imminent — the eroded-P10 band reaching it from nearby fuel does not count), the sweep never relocates or proactively decamps a crew off it, a zone or escape destination on it is a lawful refuge with an unbounded horizon, and route / exposure metrics and the road-escape search charge its samples nothing. Black still inside its window (hot) is unchanged: counted, fled, impassable. The audit judges old black over the whole step (from the step start, or from when the crew got there if later), so a front that overran a crew between two audits is still reported once. The SEM-10e zone fallback keeps its strict crossing reject (measured: relaxing it sent 3 crews on a 16 km route over old black and unburned band — 3 imminent rows). Per cell: doctrine's "good black" sizing (~4× flame length) is not modelled. False = the strict audit, byte-identical; the SIMULATOR arms it (explicit override wins). The live engine audits no crew position. |
| `left_map_out` | False | A CREW THAT LEAVES THE MAP IS OUT (founder ruling 2026-09-26, verbatim: *"crews once leave a map are no longer part of the simulation"*): the map is the terrain tile plus `simui.ZONE_ON_MAP_M` (1.5 km) around every catalog zone (five standard tiles have a zone just past their edge). A crew off the map with nothing that brings it back — stopped there, or on a final leg that ends there (in practice a road-network escape to a node beyond the tile edge) — is removed for good: never again in dispatch, no movement, no sweep, no audit; its commitments are released; state `crews[i].left_map` / `left_map_at_s`, `scoreboard.crews_left_map` and a note report it. Transit through off-tile road toward an on-map destination is not leaving; INBOUND reinforcements have not entered. False = legacy (a crew off the tile stays in play), byte-identical; the SIMULATOR arms it. |
| `escape_prefer_on_map` | False | SEM-10d ON-MAP TIE-BREAK (read only with `left_map_out`): among lawful escape destinations on the same exposure and capped-horizon steps, one ON the map ranks before one OFF it (then zone before node, then drive time). An off-tile road node has no fire field, so it reads an unbounded horizon and ties with every long-safe on-map refuge; reaching it takes the crew out of the simulation. In an emergency escape the safety steps still come first — a strictly safer off-map node still wins; in the VOLUNTARY SEM-10e road decamp (every candidate already lawful, exposure-free and durable) any on-map refuge ranks before every off-map one. Measured (docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map are out"): the plain tie-break cut crews leaving on the 60-scenario plan-on-roads bench 23 → 6 with served 792 → 793; in the corridor subset (local2_road) voluntary decamps still left for nodes 0.8–1 km past the tile edge over durable on-map refuges 3.7–13 h out, and the voluntary rule cut its departures 37 → 29 (served 679 either way). False = the legacy order, byte-identical; the SIMULATOR arms it. |
| `crew_egress_judged` | False | EVERY LEG A CREW STARTS IS JUDGED (2026-10-09; contracts SAFETY SEMANTICS 20b; read only by the simulator — the live engine moves no crew). Measured before the rule on the one-click sweep (13 scenarios × 4 intensities × city/generic weather × both floor class sets, 6 h): 10 crew-overrun rows, all "imminent" while egressing — 6 from a crew withdrawn at an early stop (road-egress guard) driving to its route's adopted zone, the LAST stop's, on a road from THIS stop no law had judged (dispatch validates each stop's own nearest lawful zone and emits only the last one; all Malibu); 3 from an egress whose zone the pessimistic front reached inside the buffer before the next audit (the 10b sweep checked the zone's own cell at zero lead; Aspen, Glenwood); 1 from a voluntary SEM-10e decamp over exposed transit (Eagle). True = (i) the moment a crew turns onto an egress leg from a stop (a withdrawal, or leaving its last stop) that leg — the road it will drive, at the speed it drives it, from where it is — must be lawful: nothing burned at its pass time, every sample's eroded P10 at least `egress_buffer_min` beyond its pass time, the zone clear one step (the sweep's lead) beyond arrival; else the SEM-10d routed choice replaces it (loud note); (ii) the sweep re-judges every egress in flight the same way (a lawful alternative always wins; between unlawful ones the escape hysteresis decides); (iii) a voluntary decamp never rides exposed transit, on any tile (`escape_proactive_lawful_only`'s rule). Default OFF = byte-identical; the SIMULATOR arms it. |
| `corridor_sample_m` | 300.0 | Sample spacing along travel legs. |
| `corridor_cell_exact` | False | CELL-EXACT CORRIDOR (2026-10-09; contracts SAFETY SEMANTICS 20a; docs/SIMULATOR_DESIGN.md "No crew drives an unjudged leg"): the corridor law samples a leg every `corridor_sample_m` — on a 90 m grid one cell in three — so a leg that clips a cell between two samples is never judged there (measured: a chord passing 467 m from a cell burning since the ignition, inside its 550 m eroded band, read 9 h clear and the crew was audited inside the band). True = EVERY raster cell a leg passes through (`geo.path_cell_runs`, the leg followed linearly in lat/lon as the simulator's crews drive it) with a finite eroded arrival is also a sample, judged at the latest moment the crew is in it (where the leg leaves the cell) — in `safety.LegOracle` (dispatch's ingress and egress law) and in the simulator's SEM-10 route judgement (relocation, escape, decamp and road-network escape choices; the crew's own starting cell keeps its carve-out). The legacy samples all stay (never weaker); off-raster and unreached stretches keep their legacy, possibly bounded, samples. Default OFF = byte-identical; the SIMULATOR arms it; the live watcher does not. |
| `corridor_fail_closed` | False | CORRIDOR FAIL-CLOSED (tri-model review 2026-08-31, fix-program item 1): the leg oracle used to SKIP samples off the spread raster — "no bound" read as safe (4.0% false-clears over 720 geometries, worst 7.85 h of lost escape margin; egress legs 24–84% unsampled beyond the 2 km domain). True: an off-raster sample is BOUNDED — the fire leaves the domain only through a boundary cell it actually reaches, so arrival at an outside point is ≥ min over reached boundary runs of (run's earliest arrival + distance / `corridor_offraster_ros_mps`), a rigorous never-late lower bound; a leg with no raster at all is unsafe. No reached boundary cell = legacy skip. Default OFF = byte-identical; the simulator arms it, the watch daemon via `--fail-closed`. |
| `corridor_unreached_bound` | False | The same boundary bound applied to IN-DOMAIN UNREACHED samples — the truncation case (a non-burnable wall at the domain edge manufactured 2,024/2,024 "unreached" cells the law trusted). For the LIVE path, whose spread domain is a small window on a real world; the simulator's tile is its whole world, so the sim leaves this OFF. Default OFF = byte-identical. |
| `corridor_offraster_ros_mps` | 1.7 | Plausible sustained MAXIMUM head ROS (m/s, ~6 km/h Tubbs-class run — the threat radius's constant) used by the fail-closed corridor bounds above. Larger = more conservative bound. Under `corridor_offraster_per_fire` it is the CEILING (rapid and unknown fires use it). |
| `corridor_offraster_per_fire` | False | THE OFF-DOMAIN HORIZON IS PER FIRE (founder rule 2026-09-27, verbatim: *"the cap at extrapolation should include the rate of fire spread vs. drive time through the region. the pathway only becomes unaccessible when the crossing time between fire and road is less than 1 hour unless the fire has shown rapid rate of spread"*; contracts SAFETY SEMANTICS 15; `wgresponse.offmap`). True = each domain fire's fail-closed bound extrapolates at its own forecast front speed where it crosses its domain edge — the P90 over its raw P10's boundary cells of cell / (arrival − inward neighbour's earlier arrival), clamped to [`corridor_offraster_ros_floor_mps`, `corridor_offraster_ros_mps`] — when a fresh WFIGS behaviour report (`FireBehaviorGeneral` + 1–3) says it is KNOWN and not RAPID (Very Active / Extreme, or Running / Wind Driven Runs / Uphill Runs / Crowning / Long-range Spotting; contradictions resolve toward rapid). Rapid and unknown fires (no report, stale, unrecognized terms, every seeded fire) keep the ceiling. Loosens the law for known-slow fires (measured: the 69 km Willow veto; what it can miss is in docs/LIVE_WORLD_PLAN.md). Inert without `corridor_fail_closed` / `corridor_unreached_bound`. `/api/state` incidents + the world summary gain an `offmap` block. Default OFF = byte-identical; watch `--offmap-per-fire` (also sets the 60-min margin below). |
| `corridor_offraster_ros_floor_mps` | 0.28 | SEMANTICS 15 floor (m/s, ~1 km/h): a known-slow fire never extrapolates slower than this, whatever its forecast front says. Read only with `corridor_offraster_per_fire`. |
| `corridor_offraster_margin_min` | 0.0 | SEMANTICS 15 crossing-time margin (minutes): a corridor sample whose arrival came from the extrapolated bound (off-raster or unreached) is unsafe when bound − pass time < max(the leg's own margin, this); samples inside the forecast keep their calibrated margins. 0 = the leg's own margin (legacy); `--offmap-per-fire` sets 60 (the founder's hour). Read only with `corridor_offraster_per_fire`. |
| `fire_behavior_max_age_h` | 48.0 | SEMANTICS 15: a WFIGS behaviour report older than this (by its record's `ModifiedOnDateTime` — the layer has no per-field time) is STALE, so the fire's behaviour is unknown and the ceiling applies. Read only with `corridor_offraster_per_fire`. |
| `ember_buffer_min` | 0.0 | Extra buffer on top of P10; default OFF, enable in wind events. |

### Gel product parameters — **ALL UNVERIFIED, PENDING SUPPLIER DATA**

> **WARNING:** these three numbers are PLACEHOLDERS. No measured GraphGel
> effectiveness-window or save-rate data exists on disk; the only documented product
> evidence is the E84 Class A rating and the ABC7 burn demo. Worse, the wgdefense
> coating-life model says a hydrogel coat lasts **~6 h at 25 C** (temp/RH/wind scaled),
> while the dispatch placeholder assumes **72 h**. That is a 12x conflict in a
> load-bearing parameter. Do not trust any plan freshness math until the supplier
> confirms real numbers. See `docs/OPEN_QUESTIONS.md` item P-1.

| Knob | Default | Meaning |
|---|---|---|
| `gel_window_h` | 72.0 | Operational window after application. GraphGel is Class A at 1/8" dry film (product data 2026-07-11); the outer weathering/rain wash-off horizon beyond 72 h is **UNVERIFIED**. |
| `gel_full_strength_h` | 72.0 | Equals `gel_window_h`: GraphGel's DRIED coating is the protection, so effectiveness is FLAT across the window (no decay ramp) and early application provides good protection. Outer horizon **UNVERIFIED** (weathering/wash-off). |
| `gel_effectiveness` | 0.85 | **UNVERIFIED** P(save) multiplier (a Class A flame-spread rating is not a survival probability). |
| `gel_env_model_enabled` | False | Switch to the wgdefense temp/RH/wind-scaled coating-life model (~6 h @ 25 C base). |

### Travel

| Knob | Default | Meaning |
|---|---|---|
| `circuity` | 1.7 | Road-network/geodesic distance ratio — MEASURED on all six sim tiles (`scratch_review/circuity_calibration.py`, 2026-08-02): pooled median 1.63 planner-legs / 1.76 uniform pairs; per-map medians 1.42 (boulder_co) – 2.48 (paradise_ca). Moves as a PAIR with `route_margin_min`; per-area values are the follow-up (canyon tiles run ~2.1–2.5). |
| `fire_traffic_factor` | 1.4 | Congestion multiplier near active fire (heuristic, uncalibrated). |
| `road_speed_mps` | 17.9 | Blended class speed (~40 mph). |

### Spread / forecast

| Knob | Default | Meaning |
|---|---|---|
| `horizon_h` | 96.0 | Forecast horizon. |
| `ensemble_n` | 6 | Perturbed ensemble members (+1 nominal). |
| `wind_perturb_frac` | 0.25 | +/- wind-speed scale at max lead; grows with lead time. |
| `moisture_perturb_frac` | 0.20 | +/- moisture scale. |
| `spotting_enabled` | False | Sardoy lognormal ember transport; default OFF. |
| `spot_fire_size` | False | EMBERS FLY AS FAR AS THE FIRE THAT THROWS THEM (founder 2026-10-09, *"begin the calibration of the ember distance and size of fire calibration"*; contracts SAFETY SEMANTICS 19; `docs/review/ember_spotting_calibration_2026-10-09.md`). Measured: the legacy spotting lets a fire of a few acres leap-frog kilometres in minutes — every burning cell throws from the full Sardoy lognormal from the first minute and a spot-ignited cell throws again the moment it burns (Park City one-click: a 6-hop spot-from-spot chain covered 3.2 km in ~7 min; Boulder and Paradise threw embers that lit spot fires 1.0–1.6 km away in the fire's first minute, at about 2 ha). True (with `spotting_enabled`): an ember (the unchanged draw, same seed) flies at most Albini's (1983) surface-fire maximum spotting distance for its cell's Byram intensity and 20-ft wind (BehavePlus equations, open flat ground), times (size of the connected burned area the cell joins / `spot_fire_size_ref_ha`) ^ `spot_fire_size_exp` — a spot fire is a new, small fire until it joins another — and a longer draw burns out in flight; a spot fire starts `spot_ignition_delay_min` after its ember lands. Deterministic seeding and the ROS memo stay exact; `compute_arrival(spot_trace=[...])` records every ember for calibration. Default OFF = byte-identical (goldens in `tests/test_spot_fire_size.py`, computed before the rule). NOT armed by the simulator: founder decision after the measurements. |
| `spot_fire_size_ref_ha` | 300.0 | SEMANTICS 19: the fire size (ha) at which an ember's reach equals Albini's surface-fire maximum; smaller fires reach less, larger more (at 0.43: a single 90 m cell 0.08×, 10 ha 0.23×, 100 ha 0.62×, 1,000 ha 1.68×). The calibration's recommended value; measured range and trade-off in the review. Within (0, 1e6]. Read only with `spot_fire_size`. |
| `spot_fire_size_exp` | 0.43 | SEMANTICS 19: reach ∝ fire area ^ this (Storey et al. 2020: source-fire area is the strongest predictor of maximum spotting distance — ~4 km at ~1,000 ha, ~8 km at ~5,000 ha in SE Australian forest => 0.43). Within (0, 2]. Read only with `spot_fire_size`. |
| `spot_ignition_delay_min` | 5.0 | SEMANTICS 19: minutes from an ember landing to its spot fire spreading (Alexander & Cruz 2014: up to 5–10 min; ELMFIRE's cell ignition delay is 100 s). Within [0, 120]. Read only with `spot_fire_size`. |
| `grid_cell_m` | 90.0 | Working cell size (never below LANDFIRE-native 30 m). |
| `grid_max_cells` | 300000 | Coarsen the grid above this cell count. |
| `live_herb_frac` | 0.30 | Cured fire-season live herbaceous moisture default. |
| `live_woody_frac` | 0.60 | Live woody moisture default. |
| `edge_weather_piecewise` | False | EDGE-WEATHER PIECEWISE INTEGRATION (review code finding, late-biased frozen departure-hour weather): True continues a traversal that crosses an hour boundary with each subsequent hour's rate for the distance still unburned. Default OFF = byte-identical; hindcast A/B decides the live flip. |
| `slope_phi_vector` | False | PHI-VECTOR SLOPE/WIND COMBINATION (review code finding: speed-space vector combination runs −11 to −33% slow on opposed slopes, the downslope Santa Ana geometry): True combines the Rothermel phi factors as vectors (the ELMFIRE/FARSITE convention) for both the effective wind and the max-spread bearing. Default OFF = byte-identical; hindcast A/B decides the live flip. |
| `spread_any_angle` | False | ANY-ANGLE SPREAD (grid-orientation review 2026-10-09; contracts SAFETY SEMANTICS 21; `docs/review/grid_orientation_2026-10-09.md`). The 16-neighbour lattice moves along 16 directions only, so on a uniform tile the fire is the 16-sided polygon inscribed in its own ellipse: at the length-to-breadth cap of 8 the head ran at 22–28 % of its own rate with the wind between two lattice directions, the area was 16–45 % of the ellipse, and it FELL as the wind rose (GR2, 6 h: 2,110 ac at 20 mph, 1,302 at 25). True: every burning cell also offers its neighbours the straight run from its anchor (Theta*-style), traced cell by cell at each crossed cell's own ellipse and weather — a real path, never faster than the engine's own physics; the lattice edges are unchanged, so under constant weather no cell burns later. Uniform tiles: the cells of the analytic ellipse at any wind angle (IoU 0.98–1.00; head within 3 %, area within 4 %); the downwind P10 that ran hours late at 30 mph is the ellipse's own time. Real tiles: closer to the per-cell continuum at high wind; can over-burn at low wind in timber mosaics. Cost 2–3.4× per solve, 3× peak memory. `compute_arrival` / `ensemble_arrival(anchor_log=, kept_anchor=)` keep re-anchors exact (the simulator threads them). Default OFF = byte-identical; `watch --any-angle` arms it live. |
| `crown_fire` | False | A FOREST FIRE MAY CROWN (founder 2026-10-12, option 3 of the Park City speed review, *"build a crown-fire model"*; contracts SAFETY SEMANTICS 22; `docs/review/crown_fire_model_2026-10-12.md`). The engine modelled surface fire only, so with the fuel-class floor on grass alone a timber ignition crawled (Park City one-click: 50 acres in 6 h) while wind-driven conifer fires run as crown fires at ~10 % of the 10-m wind. True, on a tile carrying LANDFIRE canopy layers (`TerrainTile.canopy`: LF2024 CBH / CBD / CC / CH — the committed simulator tiles carry them; `LfpsTerrain(canopy_layer_list=...)` fetches them live): a forest cell (CBD ≥ 0.02 kg/m³, CH > CBH; LANDFIRE's hardwood code CBH 10 m / CBD 0.01 never crowns) crowns when its surface Byram intensity reaches Van Wagner's (1977) threshold; active when the Cruz, Alexander & Wakimoto (2005) crown rate (11.02 U10^0.90 CBD^0.19 exp(−0.17 EFFM) m/min) reaches 3.0 / CBD m/min, else passive (× exp(−CAC)); the cell spreads at max(surface, crown), with the crown fire's intensity and, where the crown leads, Rothermel's (1991) broader crown ellipse; its ember flies on the crown intensity and, under SEMANTICS 19, at least as far as a torching tree lofts one (Albini 1979) once its fire covers 2 ha. With `corridor_erosion_tiered`, crown-fuel cells join the fast erosion tier. Only ever raises a cell's spread; deterministic, memo-exact, re-anchor continuous; `compute_arrival(crown_trace={})` records each forest cell's fire type. A tile without canopy spreads exactly as with the knob off. Default OFF = byte-identical; NOT armed by the simulator or the live watcher (`watch --crown-fire` arms it live and fetches the canopy). |
| `crown_foliar_moisture_pct` | 100.0 | SEMANTICS 22: the live foliage moisture (%) in Van Wagner's initiation threshold I_o = (0.010 CBH (460 + 25.9 FMC))^1.5 kW/m; 100 % is the US systems' default (Scott & Reinhardt 2001; FlamMap / FARSITE). Within [50, 300]. Read only with `crown_fire`. |
| `crown_foliar_moisture_seasonal` | False | SEMANTICS 22: True = the FMC follows the season instead — the Canadian FBP System's foliar moisture model (Forestry Canada Fire Danger Group 1992, ST-X-3) from each cell's latitude, longitude and elevation and the forecast hour's UTC day: 85 % at the spring dip, 120 % from 50 days after it. Read only with `crown_fire`. |
| `weather_grid_km` | 0.0 | SPREAD READS THE LOCAL WEATHER FORECAST (founder 2026-09-27, *"ensure that fire spread includes local weather forecast"*; contracts SAFETY SEMANTICS 17; `wgresponse.wxfield`). Legacy: ONE hourly forecast at the fire centroid prices every edge of a domain 50–75 km across. > 0 (km, ≥ 1): the centroid stays the primary forecast and the nodes of a GLOBAL lat/lon lattice at this spacing (row i at lat i·s/M_PER_DEG_LAT, node j at lon j·s/m_per_deg_lon(lat_i)) that fall inside the domain are fetched through the same provider, coarsened in √2 steps (every second step exactly ×2, so those levels nest) until centroid + nodes ≤ `weather_points_max`; every cell spreads on its nearest point's forecast (Voronoi zones in local metres; per (zone, hour) weather; ensemble perturbations alike in every zone; embers fly on their source zone's wind). A failed node's zone falls back to the centroid forecast (cycle note); a failed centroid is the legacy provider failure. The centroid forecast stays the one forecast for the extreme-regime posture and the gel windows; seeded fires keep one point. `/api/state` gains `weather_grid` + a `weather` block per incident (points, their next hours, how far they disagree); so do the world-summary rows, and `/world` draws the points. Default 0 = byte-identical; watch `--weather-grid-km KM`. |
| `weather_points_max` | 16 | SEMANTICS 17 cap on forecast points per domain, the centroid included (1–255; each point costs up to two NWS requests on a cold cache). Read only with `weather_grid_km` > 0; watch `--weather-points-max N`. |
| `weather_cache_ttl_s` | 1800.0 | SEMANTICS 17: how long `NwsWeather` reuses a gridpoint's decoded forecast (s, 0–21600) in its process-level cache; the points → gridpoint mapping is static and kept. A failed refresh raises like an uncached fetch (an expired forecast is never served). Read only with `weather_grid_km` > 0. |

### Dispatch

| Knob | Default | Meaning |
|---|---|---|
| `replan_period_s` | 900.0 | Scheduled re-plan cadence (15 min, matches forecast refresh). |
| `commitment_horizon_min` | 20.0 | Stops reachable within this are frozen across re-plans. |
| `churn_penalty_frac` | 0.05 | Objective penalty per reassigned stop (fraction of mean stop score). |
| `adoption_threshold_frac` | 0.03 | New plan must beat the repaired incumbent by this fraction to be adopted. |
| `solver_budget_s` | 5.0 | Wall-clock budget per plan cycle. |
| `solver_budget_evals` | 0 | DETERMINISTIC SOLVER BUDGET (review held item): > 0 makes the dispatch solver's budget a COUNT of budget checks instead of a wall clock, so identical inputs stop at the identical point regardless of machine load (the measured ±1-home nondeterminism); `solver_budget_s` stays as a hard ceiling. 0 = legacy wall clock, byte-identical; the simulator arms it. |
| `ils_iterations` | 30 | Iterated-local-search restarts. |

### Crew tracking / feeds

| Knob | Default | Meaning |
|---|---|---|
| `beacon_stale_s` | 600.0 | Crew GPS older than this = stale (crew treated unavailable-pending). |
| `poll_period_s` | 300.0 | Feed poll cadence. |
| `user_agent` | `wg-response/0.1 (ops@thewildfireguardian.com)` | Sent on all HTTP; **required** by NWS. |

### Sprint 2 (Council-1 remediations)

> **Correction note (2026-09-27): stacked perimeter rows.** Hindcast numbers in this document were computed on corpora whose snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every documented replay was re-run. Corrected values, and every number here they replace, are in [docs/calibration/stacked_rings_fix_2026-09-27.md](docs/calibration/stacked_rings_fix_2026-09-27.md) §4.3–4.5. **Affected here:** the knob notes at :378, :395–401 and :423 below. Porcupine's 13× under-forecast becomes 0.52×, and no blow-up is left in the regime signature. The measured sd(log) of 0.71 becomes 0.698. The text below is left as written.

| Knob | Default | Meaning |
|---|---|---|
| `slack_min` | 15.0 | Planned-slack floor per dispatch stop, on top of deadline feasibility (SAFETY SEMANTICS 7 — absorbs mountain-road travel-model optimism). |
| `blind_alarm_cycles` | 1 | Consecutive unassessed cycles before an `OperatorEvent('degraded')` fires (SAFETY SEMANTICS 6 — absence is not clearance). |
| `registry_ttl_s` | 300.0 | Client-registry cache TTL; prevents per-incident full-table re-scans. |
| `dir_perturb_deg` | 30.0 | Ensemble wind-direction perturbation at max lead time, degrees (makes P10 pessimistic for off-axis clients — Council-1 model F2). |
| `model_error_sd_log` | 0.0 | ENSEMBLE MODEL-ERROR TERM (tri-model review 2026-08-31, A7): the wind/moisture/direction perturbations are input-uncertainty terms damped to the horizon mean (realized wind spread ±`wind_perturb_frac`×0.5 at any member count), so the band ran 8× narrower than the measured head-ratio error and P10 was in effect the sample minimum. When > 0 each perturbed member also scales its head ROS by exp(sd·z_k) — z_k the stratified standard-normal quantile of a fourth Latin-hypercube dimension drawn after the three legacy ones (their strata stay byte-identical). Value comes from the hindcast PIT calibration; 0 = off, byte-identical. |
| `ensemble_p50_nominal_bound` | False | LADDER MEDIAN BOUND (same review, N4): direction perturbation makes the median member reach a head-axis client via its flank, so the ladder's p50 sat ~4.2× the engine's own nominal arrival. True: p50 = min(Hazen p50, nominal) per cell — the rule p10 already obeys; p10 ≤ p50 ≤ p90 preserved. Default OFF = byte-identical ladder; the watch daemon arms it via `--p50-bound`, and the SIMULATOR arms it too since 2026-09-26 (founder: "match production"; its pooled median had run 2–30× behind the fire it shades); an explicit override wins. |
| `dispatch_objective` | `homes` | `homes` = maximize COUNT of homes protected (value as tiebreak; may skip/abandon a job that costs more savable homes — reason `triaged`). `value` = legacy $-weighted objective. Never weakens crew-safety law. |
| `dispatch_urgency_weight` | 0.30 | Homes-mode: weight of the urgency (sooner-to-burn, on p50) tiebreak — protect at-risk homes before comfortable-margin ones. Count stays primary; safety gate untouched. |
| `dispatch_value_weight` | 0.05 | Homes-mode: weight of the value tiebreak (below urgency). |
| `dispatch_late_shift` | True | Emit-time gel-freshness polish: delay each coat toward the freshest-gel-at-arrival time (still inside the p10 safety slack). `True` = shipped. `False` = coat as early as feasible, freeing crews sooner — sweep-proven ~+1% more homes protected across crew/client ratios, 0 safety violations, gel-freshness-at-arrival neutral (2026-07-11 founder dispatch loop). Under the GraphGel flat-effectiveness model the polish is a structural no-op either way. |
| `dispatch_plan_on_roads` | False | Plan on ROADS: dispatch travel times and corridor T* checks follow the actual road polyline the crew will drive (when a road graph is loaded), not the straight chord. `False` = tier-0 chords (byte-identical legacy). The simulator turns it on per session; measured motivation: chord-vs-road mismatch caused ~73% of crew relocations and the residual overrun tail. |
| `route_margin_roads_min` | 30.0 | Corridor margin (minutes) for ROAD-sampled legs under plan-on-roads. The chord margin (`route_margin_min`, 60 pre-calibration) also compensated the flat travel model's mountain-road optimism (Council-1 F2); honest road geometry removes that error, so road legs carry the egress-buffer-sized margin only (sweep: 874 vs 775 homes at margin 60, per-tick violations 0 at both). Chord legs keep `route_margin_min` (56 since the 2026-08-02 pair calibration). |
| `dispatch_hold_h` | 0.0 | FOUNDER ROUND 5 COATING HOLD: while a home's fire is more than this many hours away (P50), do NOT coat it — crews go on alert and preposition — as long as a per-cycle feasibility probe (deferred start + weather buffer on deadlines) still covers every home the coat-now plan would. Gate failure releases the hold (latched). 0 = off (byte-identical). The simulator ships 24. |
| `dispatch_sequence` | `auto` | Within-route stop order. `auto` = the count-max emit order (default). `fire_first` = closest-to-fire-first in 30-min deadline bands with nearest-neighbor travel chaining (FOUNDER ROUND 6); `urgency` = raw deadline sort. Both count-safe per plan call; measured 0-12 homes BELOW `auto` across ratios (4th urgency-ordering measurement), so `auto` stays the shipped default. |
| `crew_shift_max_on_h` | 0.0 | CREW SHIFTS fatigue law: max continuous on-duty hours before a crew must rest (never mid-job; A/B first-shift stagger so the fleet never beds down at once). 0 = off (byte-identical). The sim server offers 12h-on/8h-rest via `crew_shifts: true`. |
| `wind_gust_blend` | 0.0 | Effective 10-m wind = sustained + blend x (gust - sustained). 0 = sustained only (byte-identical legacy — gusts were parsed by every adapter but dropped at the spread seam). Calibrated against the hindcast; the watch daemon sets it via `--gust-blend`. |
| `crew_shift_rest_h` | 8.0 | CREW SHIFTS: rest duration (hours) before a rested crew returns AVAILABLE. Resting crews are never dispatchable but stay under the SEM-10 safety sweep (they flee if threatened, resume resting after). |
| `dispatch_hold_weather_buffer_h` | 6.0 | Weather-change buffer for the coating-hold feasibility probe: held homes' safety deadlines are tightened by this many hours in the probe, so the hold survives only if full coverage would still fit after a forecast worsening of this size. |
| `ember_threat_radius_m` | 0.0 | Adjacency/ember threat fallback: a home on a non-burnable (developed) cell inherits the earliest fire arrival within this radius so it can alert + dispatch (fixes "home surrounded by fire, no alert"). 0 = off (live default); the simulator sets ~200 m. Conservative (more homes threatened). |
| `crew_reinforce_1h_min` | 60.0 | Out-of-region "1h away" reinforcement crews arrive this many minutes after the first ignition (mutual-aid mobilization; SEMANTICS 9). |
| `crew_reinforce_3h_min` | 180.0 | Out-of-region "3h away" reinforcement crews arrive this many minutes after the first ignition. |
| `grass_ros_floor_frac` | 0.0 | Fuel-class head-fire ROS floor as a fraction of 10-m wind (Cruz–Alexander anchor for grass; GS/TU/TL at reduced ratios). 0 = off (engine-default byte-identical); the simulator sets 0.10 and the LIVE watch daemon has run 0.10 via `--grass-floor` since 2026-07-28. The tri-model review (docs/TRI_MODEL_REVIEW_2026-08-31.md) found the as-deployed floor overrides the moisture of extinction and buys coverage by area inflation — see the two physics knobs below; the old "0% early-miss" validation language is retired (tautological by construction). |
| `ros_floor_extinction_gate` | False | ROS-FLOOR PHYSICS (review finding A3): True = the floor only ever raises a POSITIVE Rothermel ROS — fuel at/above its moisture of extinction stays out instead of walking at frac×U10 with zero intensity (39.4% of cached August fire-hours) — and Byram intensity scales with the raised ROS so spotting sees the same fire the front does. Default OFF = the as-deployed floor, byte-identical; the calibration A/B decides the live flip. |
| `ros_floor_grass_only` | False | ROS-FLOOR CLASS SET (same review): True restricts the floor to GR/GS (the Cruz–Alexander evidence base) — the "grass" floor was lifting TU/TL timber 19–45× Rothermel. Default OFF = all four legacy classes, byte-identical. |
| `extreme_regime_floor_frac` | 0.0 | REGIME-CONDITIONAL FLOOR (calibration matrix 2026-09-03 verdict): when > 0 and the incident's domain trips the extreme-spread-regime detector (cured-grass fuelscape + wind), that incident spreads with THIS floor fraction on every floored class (grass-only lifted) — the blow-up posture — while non-regime incidents keep the calibrated honest band. Lets the honest, tight band (half floor, gated, grass-only, model-error 0.71) run outside blow-ups without giving up the July late-miss. 0 = off, byte-identical. |
| `spotting_enabled` | False | Ember spotting — burning cells throw firebrands downwind that ignite ahead of the front (samples the distance distribution). Off by default (live engine); the simulator turns it on. |
| `extreme_regime_enabled` | False | EXTREME SPREAD REGIME posture (docs/calibration/hindcast_oos_2026-07-28.md): incidents whose burnable domain is cured-grass-led with wind evaluate the alert ladder on the PESSIMISTIC (p10) arrival and carry a banner — the measured blow-ups (Porcupine Ridge 13x under-forecast) are fuel-separated, not red-flag-weather-separated. Off = byte-identical ladder; the watch daemon arms it via `--extreme-regime`. |
| `extreme_regime_grass_frac` | 0.60 | Regime fuel gate: GR+GS fraction of burnable domain cells that arms the posture (Porcupine 88%, Madre grass run 69%; every non-grass-led window scored ~0% late under the grass floor). |
| `extreme_regime_wind_mps` | 4.0 | Regime wind assist: max effective wind (fixed hindcast blend 0.5 — sustained + 0.5 x gust excess) over the lookahead that must be met; keeps a dead-calm grass sea from arming the posture. |
| `extreme_regime_lookahead_h` | 24.0 | Hours ahead the regime detector scans the fire-centroid forecast for the wind clause. |
| `history_ticks` | 600 | Simulator time-scrub replay: state snapshots kept per session (ring buffer; oldest evicted). |
| `road_spur_max_m` | 2000.0 | Max straight "spur" joining an off-network point to the road graph; beyond this a leg falls back to a loud straight line. |
| `incident_stale_h` | 168.0 | Incidents with `updated_at_s` older than this are excluded from alerting/dispatch with a note (SAFETY SEMANTICS 9 — kills zombie perimeters). |
| `containment_gate_enabled` | False | WATCH ceiling for highly-contained, non-growing fires (SAFETY SEMANTICS 8). **Default OFF pending founder policy approval**; never touches crew-safety math. |
| `containment_watch_ceiling_pct` | 90.0 | Containment at/above this (with no growth) engages the WATCH ceiling when the gate is enabled. |
| `containment_no_growth_h` | 48.0 | Hours without acreage growth required before the WATCH ceiling engages. Also the SAFETY SEMANTICS 13 regrowth window: a removed contained fire seen growing stays on the map until this many hours pass without growth. |
| `drop_contained_incidents` | False | CONTAINED FIRES LEAVE THE MAP (founder ruling 2026-09-27, "a fire that is contained and no longer spreading is removed from the map"; SAFETY SEMANTICS 13a–d). True = a feed incident the feed declares contained (a source at `containment_pct` >= 100, or a containment / control / out timestamp: WFIGS `ContainmentDateTime` / `ControlDateTime` / `FireOutDateTime` + `attr_` twins, CAL FIRE `ExtinguishedDate`) with no source below 100 and no acreage growth since removal leaves the incident set after the staleness gate — no domain, no ETAs, no dispatch; its pairs take the absence law like a vanished feed incident; the count + ids go to the cycle notes, `CycleResult.removed_incidents`, `/api/state` (`removed_contained`, `removed_incidents`) and the `/world` header. It returns the cycle a source reports < 100 or it grows. Default OFF = byte-identical; watch `--drop-contained`. |
| `perimeter_confirmed_anchor` | False | CONFIRMED PERIMETERS (SAFETY SEMANTICS 13e–f): True = a polygon whose record reports the SAME size (`reported_acres` vs the polygon's own `perimeter_acres`, within the tolerance below) later than the polygon's own time, with no newer polygon in any fused source, is seeded at that report instead of being spread forward from its own age (measured: an 80 %-contained fire's 259.6-h-old polygon re-confirmed 19.7 h ago). A record reporting MORE acres beyond the tolerance — a growing fire the polygon lags — keeps the legacy, pessimistic seeding; the staleness gate still reads the record's time. Default OFF = byte-identical; watch `--confirmed-perimeters`. |
| `perimeter_confirm_tolerance_frac` | 0.02 | "Same size" for the anchor: \|reported − polygon\| <= this × polygon acres. Live WFIGS 2026-09-27: record size within 1 % of its polygon for 107 of 146 perimeters, 2 % for 112, 5 % for 116 (Willow reads +1.8 %). Every percent admitted is growth a lagging polygon may hide — keep it small. |

### Fire belief (SAFETY SEMANTICS 14 — `docs/FIRE_BELIEF.md`)

The per-fire spread-rate belief (founder 2026-09-27: "the rate of fire spread should be bayesian unless a new weather pattern emerges" and "a bayesian feedback loop for each fire"). Every knob OFF = byte-identical engine; the watch daemon arms it with `--fire-belief` (track) / `--fire-belief-apply` (apply, implies track).

| Knob | Default | Meaning |
|---|---|---|
| `fire_belief_track` | False | Learn a per-fire whole-fire head-ROS multiplier from the feeds' growth observations (newer perimeters, changed / long-unchanged reported acreage) against the engine's own nominal growth curve; publish `CycleResult.fire_status`, `/api/state` `fire_status`, the world summary's `belief` rows and `<state-dir>/fire_beliefs.json` (S3-mirrored). Changes NO arrival, ETA, alert or dispatch. |
| `fire_belief_apply` | False | Requires track. The posterior drives the ensemble's head-ROS multiplier in SAME-weather-pattern forecast hours (nominal = posterior median, members at their model-error stratum's posterior quantile): the footprint, the ladder's P50 (**alert timing changes**) and P90 move; P10 stays GUARDED (never later than physics) unless `fire_belief_apply_p10`. Measured before/after in `docs/FIRE_BELIEF.md`. Physics (the prior) returns for new weather, surprise resets, rapid fires (larger of prior and posterior) and no evidence. |
| `fire_belief_apply_p10` | False | Requires apply. OFF (default) = the **P10 guard**: P10 = min(belief P10, physics P10) — the belief drives the nominal, P50 (the alert ladder) and P90 but never makes P10 (crew safety, corridors) later than today's engine (a second, legacy ensemble runs for belief-applied fires only). ON = the belief moves P10 too — **measured unsafe for contained fires** (P10 too late on 38–43 % of newly burned cells vs 1.6–2.9 % physics; `docs/FIRE_BELIEF.md` §5). |
| `fire_belief_prior_sd_log` | 0.0 | Prior sd of log(multiplier). 0 = `model_error_sd_log` when > 0, else 0.71 (the hindcast-measured model error). |
| `fire_belief_half_life_h` | 72.0 | Evidence half-life: a growth window's log-likelihood weighs 0.5^(age / half-life). |
| `fire_belief_min_obs_spacing_h` | 3.0 | Two observations of one series closer than this are not both kept (the next spans both). |
| `fire_belief_area_rel_err` | 0.03 | Area measurement error, relative part (sd). |
| `fire_belief_area_abs_err_acres` | 10.0 | Area measurement error, absolute part (sd, acres). Long-unchanged ("static") sizes carry 3× both. |
| `fire_belief_noise_nu` | 4.0 | Student-t degrees of freedom of the growth-rate noise (heavy tails: one re-stated acreage cannot swing a fire). |
| `fire_belief_discrepancy_frac` | 0.30 | Model-discrepancy sd as a fraction of the predicted radial growth rate. |
| `fire_belief_fast_sd_floor` | 0.35 | Apply: the fast side of the members' multipliers is never narrower than this log sd around the posterior median. |
| `fire_belief_mult_floor` | 0.05 | Apply: no member multiplier below this. |
| `fire_belief_novel_severity_ratio` | 1.5 | New weather pattern: a forecast hour whose Rothermel severity (dominant fuel, flat) exceeds this × the evidence hours' P90; physics applies from the first such hour to the end of the horizon. |
| `fire_belief_novel_shift_deg` | 60.0 | New weather pattern: the wind turns more than this from the evidence's speed-weighted mean direction ... |
| `fire_belief_novel_min_wind_mps` | 4.0 | ... at an effective wind of at least this (m/s). |
| `fire_belief_reset_h` | 24.0 | Surprise reset: a window that beats the belief's P90 predicted growth resets the fire to physics for this long (sticky) and drops older evidence. |
| `fire_belief_static_h` | 24.0 | An unchanged reported size counts as zero growth only after the record kept being modified this long since the size last changed. |
| `fire_belief_rapid_acres_per_day` | 1000.0 | Rapid: a window grew at least this many acres per 24 h (equivalent) ... |
| `fire_belief_rapid_frac_per_day` | 0.25 | ... or at least this fraction of its starting area per 24 h ... |
| `fire_belief_rapid_min_acres` | 100.0 | ... with at least this many acres grown. |
| `fire_belief_rapid_lookback_h` | 48.0 | Rapid: windows ending within this lookback count (and fires discovered within it are judged on growth since discovery). |
| `fire_belief_rapid_noise_z` | 1.645 | Rapid is judged on growth BEYOND measurement noise: growth − z × its sd under the belief's own area-error model (one-sided 95 %). 3 % of a 20,000-acre fire is ±600 ac per report, so raw 12-h windows of a fire truly growing ~600 ac/day read 1,400–2,500 ac/day and would flag every big fire rapid. 0 = the raw observed growth. |

**v2 rules (2026-09-28, `docs/FIRE_BELIEF.md` "v2").** Measured on 11 real 2026 fires (`docs/review/belief_vs_physics_2026-09-27.md`). The hourly new-weather test fired on the ordinary afternoon wind turn, and the 3 %-of-area noise hid a big fire's growth from the rapid rule. Every default below is the legacy rule, byte-identical. The watch flag `--fire-belief-v2` (implies `--fire-belief`) arms `fire_belief_novelty=daily`, `fire_belief_rapid_noise=calibrated` and `fire_belief_rapid_persist_windows=2`.

| Knob | Default | Meaning |
|---|---|---|
| `fire_belief_novelty` | hourly | New-weather test. `hourly` = the legacy test: forecast hours against the evidence hours' P90 severity and one mean direction. `daily` = forecast DAYS against evidence DAYS in the fire's local solar time. Each day has a peak severity and a driving wind: the speed-weighted mean over its hours at ≥ 0.5 × its peak. The afternoon wind turn is therefore not a new pattern. The prior applies from the first qualifying hour of the first new day. |
| `fire_belief_novel_day_severity_ratio` | 1.5 | Daily: a forecast day is new if its peak severity exceeds this × the harshest evidence day (the weighted P90 of evidence-day peaks). Chosen by leave-one-fire-out. |
| `fire_belief_novel_day_shift_deg` | 60.0 | Daily: ... or if its steady driving wind comes more than this from EVERY evidence day's driving wind. This only applies on a day that can drive the fire (peak ≥ 0.5 × the reference). Evidence days that were calm, unsteady or mild give no sector; with no sector at all, any such forecast day is new. |
| `fire_belief_novel_day_min_wind_mps` | 4.0 | Daily: ... at a driving wind of at least this (m/s). The evidence could not tell 3–5 m/s apart, so this keeps the hourly rule's value. |
| `fire_belief_rapid_noise` | area_frac | Rapid-rule noise. `area_frac` = legacy: each end's sd is hypot(`fire_belief_area_rel_err` × A, `fire_belief_area_abs_err_acres`). `calibrated` = a PERIMETER window's growth noise is the measured re-mapping noise, hypot(k × √A, √2 × abs err) acres, with A the larger end. Consecutive perimeters share most of their boundary, so the noise follows the edge (~√A), not the area. Reported-acreage windows keep the legacy sd. |
| `fire_belief_rapid_perim_noise_k` | 1.0 | Calibrated: k in acres per √acre. The measured 95th percentile of pure-noise (negative) perimeter changes is 1.6 √A acres, so z (1.645) × k = 1.645 √A covers it. For comparison, legacy is ~12 √A at 80,000 ac. |
| `fire_belief_rapid_persist_windows` | 0 | Rapid persistence: N ≥ 2 consecutive perimeter windows, each growing at a rapid rate RAW (no noise discount) and the latest ending inside the lookback, make the fire rapid. Consecutive windows share an end, so noise alone rarely lifts both. 0 = off. 1 is rejected: a single raw window is the rule the noise discount exists to prevent. |

### Feed sanity (SAFETY SEMANTICS 18 — `docs/review/feed_sanity_2026-09-27.md`)

The 2026-09-27 audit of the live feed path the belief learns from (`wgresponse.feedsanity`). Measured on the recorded live layers, the deployed watcher's belief file and the WFIGS Daily history of 11 fires: 12 of 918 published edits on 6 fires carried a polygon under 10 % of the record's own size (0–8,183 ac while reporting 10,524–156,699 ac, for minutes to ~2 days; one record does right now: a 29 ac polygon under a 200,292 ac complex); 45 % of the perimeter windows the belief would form are one polygon re-stamped under a newer time (the deployed watcher's first window was one); 23 of 560 live polygons carry an area figure more than 2× off their geometry; reported sizes reset (15,798 → 700 ac) and fused sources disagree (0.01 vs 73 ac). Every knob OFF = byte-identical engine; the watch daemon arms it with `--feed-sanity`.

| Knob | Default | Meaning |
|---|---|---|
| `feed_sanity` | False | A perimeter or size the feed cannot have meant never teaches the belief and never shrinks the seed. Belief side (with `fire_belief_track`): no observation from an own time in the future or before the fire (never clamped to now), nor from a polygon under `feed_sanity_collapse_frac` of its own record's reported size; a polygon's area is its geometry's when the feed's figure disagrees; a re-stamped polygon counts once per `fire_belief_static_h` as a static zero-growth observation; a drop beyond noise is HELD until it comes back or persists `fire_belief_static_h` (then the series restarts there — never a negative-growth window); notes say what was skipped. Seed side (every feed incident with a perimeter): an implausible polygon time falls back to the record's; a collapsed polygon, or one dropped below the remembered one beyond noise, is replaced by the fire's last plausible polygon (in-process memory, `feed_sanity_memory_h`) for geometry, domain and seed; with none remembered it is seeded as received; a note per cycle and an `OperatorEvent('feed_sanity')` on first finding. Default OFF = byte-identical; watch `--feed-sanity`. |
| `feed_sanity_future_h` | 1.0 | An own time (a perimeter's, a reported size's) later than now + this many hours is implausible (a feed clock may run a little ahead; the SEMANTICS 9 clamp covers that). Earlier than the fire's discovery less 24 h (a year before now without a discovery time) is implausible too. |
| `feed_sanity_area_ratio` | 2.0 | The feed's own area figure (`perimeter_acres`: WFIGS `poly_GISAcres`, FIRIS `area_acres`) stands for a polygon only within this factor of its geometry's area; else the geometry's. Live 2026-09-28: 519 of 560 polygons agree within 5 %; 'Auto-generated for InFORM' polygons keep their first size (Bunker 1 ac on 2,850 ac of rings); the adapter's 200-vertex subsampling alone can move a ring's area ~20 %. |
| `feed_sanity_collapse_frac` | 0.1 | A polygon whose area is under this fraction of its own record's reported size is not the fire's perimeter. Caught all 12 collapsed edits in the 11-fire history (0–8,183 ac vs 10,524–156,699 ac reported) and none of the genuine early-growth jumps (the polygon is then larger than the record). |
| `feed_sanity_min_acres` | 100.0 | The collapse check applies only when the record's reported size is at least this (small fires' first sizes are rough). |
| `feed_sanity_restamp_frac` | 0.001 | A polygon within this fraction of the last observed polygon's area is the same capture re-issued: 216 of the 295 history windows with an unchanged `poly_PolygonDateTime` changed by less than 0.1 %. |
| `feed_sanity_drop_z` | 3.0 | A size (either series; and the seed vs its memory) this many sd of the difference below the last one is a drop, not a re-measurement, under the belief's own area error (`fire_belief_area_rel_err` / `_abs_err_acres`). History: genuine IncidentSize drops ≤ 6.4 % (≤ 1.6 sd); resets −43 % to −96 % (≥ 12 sd). |
| `feed_sanity_memory_h` | 72.0 | How long the seed side remembers a fire's last plausible polygon after last seeing it (the longest measured collapse lasted ~48 h). In-process: a restart forgets it. |

### Simulator city weather (2026-10-09)

Founder, 2026-10-09: *"change the weather conditions for each city specific."* The interactive simulator builds its own synthetic weather; until now one table served every location (extreme = 40 mph, 6 % RH, 39 °C held for days). `docs/review/city_weather_2026-10-09.md` has the method, stations, sources and the before/after of the thirteen one-click scenarios; `tools/city_weather_climatology.py` reproduces the numbers. Read only by `wgresponse.simui`; the live engine never reads it.

| Knob | Default | Meaning |
|---|---|---|
| `sim_weather_profile` | generic | `generic` = the legacy intensity table (`simui.INTENSITY_WEATHER`), the same at every location and constant for the whole run — byte-identical. `city` = each community's own conditions per intensity (`simui.CITY_WEATHER`: airport-station fire-season climatology 2005–2024 ranked by the Fosberg index, checked against historic fire days, temperature and RH moved to the homes' elevation) with a daily cycle on local time (afternoon wind and low RH, overnight recovery) and, for high / extreme, a finite wind event from the ignition that holds, eases and returns to the community's moderate daily cycle. A location without a profile keeps the generic table. The SIMULATOR arms `city` (explicit override wins); seeded drills (`wgresponse.seeded`) keep the generic table. |

## Feed & data licensing posture

Read this before pointing any adapter at a live endpoint.

| Source | Status | Rule |
|---|---|---|
| **Watch Duty** | Enterprise license **pending** (sales conversation required; API is an Enterprise addon, no self-serve tier). | The unofficial `api.watchduty.org` app API is **ToS-banned** for automated/commercial use — `WatchDutyEnterpriseFeed` stays a stub until a signed license exists. Never poll the app API from WG servers. |
| **WFIGS** (NIFC ArcGIS) | Public, no key. | The automated backbone for incident points + perimeter polygons. Layers refresh every 5 min; perimeter *content* is often nightly (IR flights). |
| **FIRIS ComboLayer** (CAL FIRE/Cal OES) | Public, no key. | Fastest public perimeters in CA (intra-day aircraft IR); union with WFIGS by IRWIN id, take freshest. |
| **CAL FIRE IncidentApi** | Public, no key. | CA metadata cross-check (acres/containment); duty-officer cadence, hours of lag — never the fast trigger. |
| **NASA FIRMS** | Free MAP_KEY (instant email signup). | Hotspot growth signal between perimeter updates. 5000 transactions / 10 min per key. VIIRS 375 m, 4–6 passes/day — **not continuous**; no GOES in the area API. |
| **NWS** (api.weather.gov) | Public, no key. | **A `User-Agent` header is REQUIRED** (`ResponseConfig.user_agent`). Rate limit unpublished; honor `Cache-Control`, retry with backoff. Known multi-hour outage history — the fallback is not optional. |
| **Open-Meteo** | Free tier is **non-commercial only**. | wgresponse is commercial software: production use requires the paid API (`customer-api.open-meteo.com` + key) or self-hosting (AGPLv3 server, CC-BY-4.0 data). Do NOT ship on the free endpoint. |
| **LANDFIRE LFPS** | Public, no key. | Fuel + slope + aspect + elevation as one aligned 30 m GeoTIFF per job (~60–90 s). Max AOI per job unverified — see open questions. |

## Determinism & testing conventions

- All mocks/synthetic generators seed RNG via `contracts.seeded_rng(...)` (SHA-256 of
  explicit seed strings). Never wall-clock, never unseeded `random`.
- Live adapters take an injectable `http_get` callable; tests never touch the network.
- Third-party deps (e.g. `tifffile`/`numpy` for live GeoTIFF ingest) import inside
  `try` and raise a clear `RuntimeError` only when the live path is invoked without
  them. Core logic is stdlib-only.
- Regression anchors for the spread model (Camp/Tubbs/East Troublesome growth rates)
  are documented in `docs/ARCHITECTURE.md`.

## Verified demo results

Real numbers from a full verification pass on 2026-07-06 (Python 3.14.5, pytest 9.1.1,
Windows 11). Every figure below is reproducible from the repo root with the exact
commands shown — the sim is deterministic (seeded RNG, no wall-clock), the feed pull is
a live read-only GET sweep.

### Test suite

```
py -3 -m pytest
```

**453 passed** in ~16 s. Zero failures, zero errors, zero skips, zero warnings
(`filterwarnings = error::RuntimeWarning` is in effect, so any RuntimeWarning would have
failed the run).

### Flagship scenario — `canyon_wind_event`, 96 h

```
py -3 -m wgresponse.cli simulate --scenario canyon_wind_event --hours 96
```

*Scenario:* 12 h calm, then 8 h of 15 m/s wind from the west pushing the fire east
through a canyon community of 12 protected homes; 3 GraphGel crews, 3 safe zones.

| Metric | Value |
|---|---|
| Cycles run | **384** × 900 s (96 h simulated) |
| Alert events | **28** |
| First `NOTICE_72H` | sim hour **0.00** (C10 / C12 — the two farthest homes; P50 ≈ 34–35 h out at t0) |
| First `EXTREME_24H` | sim hour **0.00** (C01 — closest home, already inside the 24 h window at t0, P50 = 8.18 h) |
| First `IMPACTED` | sim hour **8.25** (C01, fire arrives) |
| Properties serviced | **12 / 12** (gel applied before arrival) |
| Unserved | **0** |
| **SAFETY AUDIT** | **0 violations** (every serviced crew departed before its P10 latest-departure deadline) |
| Min crew margin observed | **4.59 h** of headroom on the tightest stop |
| Wall-clock runtime | **~2.2 s** |
| Exit code | 0 |

All 12 homes are eventually reached by the fire (this is a fast, wind-driven
conflagration), but the response engine got GraphGel onto all 12 with crews safely clear
in every case — the safety audit against the final P10 arrival grids is clean, which is
the release gate. Re-running is bit-for-bit identical (deterministic).

Output written to `out/sim_canyon_wind_event.json` (full report) and
`out/sim_canyon_wind_event_console.json` (map console payload). The console JSON was
structurally validated against every field `console/index.html`'s `?file=` renderer
reads — 12 clients, 3 crews, 28 events, 1 planned route, 3 isochrones, 247-point
modeled perimeter, 3 safe zones; `kind="wgresponse-sim-console"`, `schema_version="0.1.0"`;
no bare `NaN`/`Infinity` tokens that would break browser `JSON.parse()`.

### Live feed pull (real public feeds, read-only)

```
py -3 -m wgresponse.cli pull-feeds --bbox "-120,33,-116,36"
```

Live GET sweep of the keyless public feeds over a Southern California bounding box:

| Feed | Incidents parsed |
|---|---|
| WFIGS (NIFC ArcGIS) | **207** |
| FIRIS ComboLayer (CAL FIRE / Cal OES) | **112** |
| CAL FIRE IncidentApi | **3** |
| Raw total (pre-fusion) | **322** |
| **Fused total** (IRWIN-id join, freshest wins) | **291** |

Fusion collapsed **31** duplicate records across feeds (1 fused incident carried
cross-feed multi-source provenance). Largest active perimeters returned included
SANTA ROSA (18,379 ac, FIRIS), SANTA ROSA ISLAND (16,938 ac), and LOST (12,820 ac).
FIRMS was skipped gracefully — at the time of this run the `pull-feeds` CLI's keyless
construction died with `[FirmsFeed] fetch failed: ... missing 1 required positional
argument: 'map_key'` on stderr and the sweep continued (non-fatal, exit 0). *(Since
Sprint-2 ticket C3d, `FirmsFeed()` constructs without a key and a keyless fetch
returns `[]` as a BLIND fetch — `last_fetch_ok=False` — with one loud log naming the
free MAP_KEY signup at `firms.modaps.eosdis.nasa.gov/api/area/`.)* Fetch wall-clock was
~7–14 s depending on feed latency. This confirms the live adapters parse and fuse real
WFIGS/FIRIS/CAL FIRE data end to end.
