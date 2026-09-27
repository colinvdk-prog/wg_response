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
# SEEDED INCIDENTS (W1, docs/LIVE_WORLD_PLAN.md; contracts SAFETY SEMANTICS
# 12): light a fire at a point and the watcher runs it exactly like a feed
# incident — listed in /api/state (source "seeded"), a domain + ensemble +
# ETAs + alert ladder + dispatch when protected clients are in range (none
# in range = listed, no domain: the live law), persisted across restarts.
# Authed like every write path (X-Internal-API-Key unless WGRESP_DEV_OPEN=1):
#   POST   /api/seed        {"lat": 39.626, "lon": -106.936,
#                            "intensity": "high", "wind_dir_deg": 240,
#                            "note": "drill", "created_by": "colin"}
#                            -> 201 {"incident_id": "seeded:<uuid>", "seed"}
#                            (400 bad body / outside the AOI, 409 at 12 active)
#   GET    /api/seed[/<id>] seeds (active + recently retired): weather mode,
#                            last-cycle domain, pair states
#   DELETE /api/seed/<id>   retire (?by=name): absent from the next cycle; its
#                            pairs take the absence law like a vanished feed
#                            incident (-> CLEARED after clear_cycles while every
#                            feed is healthy; frozen while any is blind)
# Weather is LIVE at the fire (NWS) unless the seed overrides it: "intensity"
# (the simulator's low/moderate/high/extreme presets) or "weather"
# ({wind_speed_mps, rh_pct, temp_c, wind_gust_mps?}) sets the conditions,
# "wind_dir_deg" fixes the direction; both = fully synthetic, no network. A
# seeded fire is a point incident spread from its point every cycle (the
# watcher has no ground truth for live fires); a seed/retire persists at once
# and wakes the loop. Seeded fires notify the DRILL LOG only — never a client
# channel — and auto-retire once their horizon passes with nothing threatened.
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
| `wgresponse.threat` | `ThreatMonitor.cycle()` — the full orchestration loop (feeds ∪ seeded incidents → domains → ladder → dispatch). |
| `wgresponse.seeded` | W1 seeded incidents (SAFETY SEMANTICS 12): the thread-safe `SeededIncidentStore` (create / retire / list / persistence rows, 12-active cap), `seeded_forecast` (the per-incident weather override through the weather seam), `parse_seed_request` (the `POST /api/seed` body), `SeedSafeNotifier` (a seeded fire's notifications go to the drill log, never a client). |
| `wgresponse.safety` | P10 departure deadlines, eroded-grid corridor checks, egress-to-safe-zone verdicts. |
| `wgresponse.dispatch` | Deadline-TOPTW planner: regret-2 insertion + ILS, commitment/churn stability. |
| `wgresponse.travel` | Tier-0 travel time (haversine x circuity / class speed x fire-traffic factor). |
| `wgresponse.notify` | Notifier protocol; SES/SMS stubs, log notifier. |
| `wgresponse.sim` | Seeded synthetic scenarios + full-loop sim harness (the acceptance test and the demo). |
| `wgresponse.server` | Stdlib HTTP: `POST /api/beacon`, `GET /api/state`, `GET /api/plan`, `GET /console`; `/healthz` carries last-cycle age + degraded + cycle count; `/simulator` + `/api/sim/*` session API (LRU-capped, per-session ticker; `POST /api/sim/<id>/crew` = setup-phase crew placement, road-snapped, 409 once a fire is lit); `/api/seed` GET/POST/DELETE routed to the watch daemon (404 elsewhere). `X-Internal-API-Key`, fail-closed. |
| `wgresponse.simui` | `SimSession`: the interactive simulator over the REAL engine — thirteen cached LFPS WUI tiles (five originals, Rancho Santa Fe, the six Vail→Aspen corridor communities, the whole-valley Eagle→Vail corridor on one ~60 km @ 200 m tile), seeded WUI client placement, setup-phase crew placement on the road network (`place_crew` / `reset_crews`: nearest road node within 2 km, escape targets stay the safe zones, replayed by reset), perimeter-seeded ensembles, live safety-slider re-plans (in-flight deploys re-checked, never grandfathered), per-tick safety audit. |
| `wgresponse.worldview` | The W6 thin world viewer's read model behind `GET /api/world/summary`: per-incident domain facts (clients, earliest client P10/P50), the cycle's "why no domain?" note, display-simplified perimeters, P50 rings traced from each domain's ensemble, counts and the ranked "incidents threatening clients" list — built once per cycle, read-only (page: `console/world.html` at `/world`). |
| `wgresponse.persist` | `StateStore`: atomic-write (fsync'd) versioned JSON persistence for alert records, the notified ledger, the incumbent plan, and watch metadata — restart re-fires nothing already sent (an applied-but-never-sent active alert IS re-fired: re-notify-ward); ANY corrupt state — including non-UTF-8 bytes and type-corrupt fields — loads fresh with a loud note, never a crash. |
| `wgresponse.cli` | `simulate \| forecast \| plan \| serve \| pull-feeds \| watch \| simui` (watch = the live operator daemon: AOI-bounded feeds, per-cycle persistence, console + healthz off the live loop, the `/api/seed` seeding API; simui = the interactive simulator UI). |

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
| `escape_proactive_lawful_only` | False | SEM-10e LAWFUL-ONLY (roads inspection 2026-09-25): the proactive decamp takes only a fully lawful route (not burned, eroded-P10 margin ≥ 0 at every pass time); the exposed-transit fallback — audit-counted as imminent crew-overrun rows since the 2026-09-03 audit change — is skipped, so a voluntary move never buys 10c rows. False = legacy, byte-identical. The SIMULATOR arms it for EXTENDED fire-zone sessions only (their extended road graph opens exposed routes beyond the 18 km core; explicit override wins). |
| `escape_road_snap_m` | 0.0 | SEM-10d AREA-ESCAPE ROAD SNAP (roads inspection 2026-09-25): area-escape proposals snap to the road node nearest a safe ray sample within this many metres and the flee becomes road-bound (a bearing with no road in reach is proposed only when none reaches a road) — no straight spur into open ground. 0 = off, byte-identical; NOT armed by the simulator: the 8-session A/B was clean (straight escape km −75…−99 %, 0 violations, docs/review/roads_inspection_2026-09-25.md §5) but armed as a default it produced two imminent-while-egressing rows in the pinned Camp-Fire-scale extended run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_overrun_ever; ticks 69 and 106, 410 and 1,158 min inside the eroded-P10 band, nominal front ≥ 48 h away; relocations 3 → 7, served unchanged; scratch_review/snap_campfire_diag.py) — the road-bound flee routes through pessimistic exposure the open-ground escape avoided. Superseded in the simulator by `escape_road_network` (the snap now applies only in that knob's no-passable-road fallback). |
| `escape_road_network` | False | SEM-10d ROAD-NETWORK ESCAPE (founder "rework the crews fleeing", 2026-09-26; docs/review/escape_roads_2026-09-26.md): a crew that must flee with no catalog zone lawfully reachable (zone-first rule unchanged) SEARCHES THE ROAD GRAPH — a time-dependent Dijkstra at the crew's escape speed where a sample the nominal front crosses inside its cold-black window makes an edge impassable and samples inside the eroded-P10 imminent band cost exposure seconds — and drives, roads end to end, to the road node ≥ 2 km out that is lawful at arrival, then has the least exposure (5-minute steps; zero exposure always first), then the longest safety horizon (eroded-P10 arrival minus arrival, capped by `escape_road_horizon_cap_h`), then the shortest drive; the catalog zones compete under the same rule and win ties on those safety steps, and the crew drives exactly the searched path (the sweep re-judges its remaining leg). When neither SEM-10e zone rule yields a move, the proactive decamp also takes a fully lawful road path to a durable road node (a crew parked at a road point after an area escape otherwise waited for the imminent law). No passable road escape (cornered / off the network) → the legacy ray logic with a loud note. Diagnosis: the ray and snap escapes both ranked lawful destinations by (exposure, drive time) only — the pinned Camp-Fire-scale run was clean by a 4 s drive-time tie between an infinite-horizon ray end and a 26.6 h one, and the snap parked crews at 10–27 h nodes the band later overtook. False = off, byte-identical; the SIMULATOR arms it (explicit override wins). |
| `escape_road_horizon_cap_h` | 24.0 | SEM-10d ROAD-NETWORK ESCAPE: the safety horizon at which a destination counts as fully safe — beyond it the nearer node wins, so crews are not sent to the far edge. Short caps park crews where the growing band overtakes them (pinned Camp-Fire-scale run: 6 h → 1 imminent row, 9 relocations; 12 h → 0 rows, 7 relocations; 24 h → 0, 4; 48 h → 0, 3). Read only when `escape_road_network` is on. |
| `transit_cold_black_by_class` | False | TRANSIT-HAZARD STAGE 1: per-fuel-class cold-black windows for SEM-10 ROUTE SELECTION (`fuels.cold_black_minutes`: grass 5–10 min, grass-shrub 10–20, shrub 20–30, timber-understory 45, timber-litter 60, slash/unknown 90 — grass opens, timber stricter; the flat legacy 45 min is numerically the residence time of a 1000-h log). The 10c audit consults it only under `old_black_refuge` (the window is what makes black OLD); otherwise never. False = flat 45 min, byte-identical. |
| `old_black_refuge` | False | OLD BLACK IS A REFUGE (founder ruling 2026-09-26, verbatim: *"old burned ground does not count as a violation"*; contracts SAFETY SEMANTICS 10; docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map are out (2026-09-26)"): a cell whose nominal arrival is at least its cold-black window in the past (the transit window above) is OLD BLACK — burned out, it cannot burn again. The 10c audit counts no row for a crew on it (neither burned nor imminent — the eroded-P10 band reaching it from nearby fuel does not count), the sweep never relocates or proactively decamps a crew off it, a zone or escape destination on it is a lawful refuge with an unbounded horizon, and route / exposure metrics and the road-escape search charge its samples nothing. Black still inside its window (hot) is unchanged: counted, fled, impassable. The audit judges old black over the whole step (from the step start, or from when the crew got there if later), so a front that overran a crew between two audits is still reported once. The SEM-10e zone fallback keeps its strict crossing reject (measured: relaxing it sent 3 crews on a 16 km route over old black and unburned band — 3 imminent rows). Per cell: doctrine's "good black" sizing (~4× flame length) is not modelled. False = the strict audit, byte-identical; the SIMULATOR arms it (explicit override wins). The live engine audits no crew position. |
| `left_map_out` | False | A CREW THAT LEAVES THE MAP IS OUT (founder ruling 2026-09-26, verbatim: *"crews once leave a map are no longer part of the simulation"*): the map is the terrain tile plus `simui.ZONE_ON_MAP_M` (1.5 km) around every catalog zone (five standard tiles have a zone just past their edge). A crew off the map with nothing that brings it back — stopped there, or on a final leg that ends there (in practice a road-network escape to a node beyond the tile edge) — is removed for good: never again in dispatch, no movement, no sweep, no audit; its commitments are released; state `crews[i].left_map` / `left_map_at_s`, `scoreboard.crews_left_map` and a note report it. Transit through off-tile road toward an on-map destination is not leaving; INBOUND reinforcements have not entered. False = legacy (a crew off the tile stays in play), byte-identical; the SIMULATOR arms it. |
| `escape_prefer_on_map` | False | SEM-10d ON-MAP TIE-BREAK (read only with `left_map_out`): among lawful escape destinations on the same exposure and capped-horizon steps, one ON the map ranks before one OFF it (then zone before node, then drive time). An off-tile road node has no fire field, so it reads an unbounded horizon and ties with every long-safe on-map refuge; reaching it takes the crew out of the simulation. In an emergency escape the safety steps still come first — a strictly safer off-map node still wins; in the VOLUNTARY SEM-10e road decamp (every candidate already lawful, exposure-free and durable) any on-map refuge ranks before every off-map one. Measured (docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map are out"): the plain tie-break cut crews leaving on the 60-scenario plan-on-roads bench 23 → 6 with served 792 → 793; in the corridor subset (local2_road) voluntary decamps still left for nodes 0.8–1 km past the tile edge over durable on-map refuges 3.7–13 h out, and the voluntary rule cut its departures 37 → 29 (served 679 either way). False = the legacy order, byte-identical; the SIMULATOR arms it. |
| `corridor_sample_m` | 300.0 | Sample spacing along travel legs. |
| `corridor_fail_closed` | False | CORRIDOR FAIL-CLOSED (tri-model review 2026-08-31, fix-program item 1): the leg oracle used to SKIP samples off the spread raster — "no bound" read as safe (4.0% false-clears over 720 geometries, worst 7.85 h of lost escape margin; egress legs 24–84% unsampled beyond the 2 km domain). True: an off-raster sample is BOUNDED — the fire leaves the domain only through a boundary cell it actually reaches, so arrival at an outside point is ≥ min over reached boundary runs of (run's earliest arrival + distance / `corridor_offraster_ros_mps`), a rigorous never-late lower bound; a leg with no raster at all is unsafe. No reached boundary cell = legacy skip. Default OFF = byte-identical; the simulator arms it, the watch daemon via `--fail-closed`. |
| `corridor_unreached_bound` | False | The same boundary bound applied to IN-DOMAIN UNREACHED samples — the truncation case (a non-burnable wall at the domain edge manufactured 2,024/2,024 "unreached" cells the law trusted). For the LIVE path, whose spread domain is a small window on a real world; the simulator's tile is its whole world, so the sim leaves this OFF. Default OFF = byte-identical. |
| `corridor_offraster_ros_mps` | 1.7 | Plausible sustained MAXIMUM head ROS (m/s, ~6 km/h Tubbs-class run — the threat radius's constant) used by the fail-closed corridor bounds above. Larger = more conservative bound. |
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
| `grid_cell_m` | 90.0 | Working cell size (never below LANDFIRE-native 30 m). |
| `grid_max_cells` | 300000 | Coarsen the grid above this cell count. |
| `live_herb_frac` | 0.30 | Cured fire-season live herbaceous moisture default. |
| `live_woody_frac` | 0.60 | Live woody moisture default. |
| `edge_weather_piecewise` | False | EDGE-WEATHER PIECEWISE INTEGRATION (review code finding, late-biased frozen departure-hour weather): True continues a traversal that crosses an hour boundary with each subsequent hour's rate for the distance still unburned. Default OFF = byte-identical; hindcast A/B decides the live flip. |
| `slope_phi_vector` | False | PHI-VECTOR SLOPE/WIND COMBINATION (review code finding: speed-space vector combination runs −11 to −33% slow on opposed slopes, the downslope Santa Ana geometry): True combines the Rothermel phi factors as vectors (the ELMFIRE/FARSITE convention) for both the effective wind and the max-spread bearing. Default OFF = byte-identical; hindcast A/B decides the live flip. |

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
