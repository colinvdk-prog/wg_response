# WG Response — Live World Plan: one national map, live and seeded fires, crews that respond anywhere

2026-09-26. Founder direction: *"the model needs to move towards a more dynamic
situation where the screen is not the world, but is a zoomed in view of the
world. ultimately the entire US needs to be on one map with our crews and teams
to respond to wildfires."* This plan turns the tile-bound simulator and the
AOI-bound shadow watcher into one national world with a viewer that zooms
anywhere, monitors current fires, and lets a user seed a fire at any location.
Companion plans: `LIVE_OPS_PLAN.md` (feeds → shadow ops → clients → dispatch),
`SIMULATOR_DESIGN.md` (the view built so far), `vail_aspen_response_program.md`
(the first corridor program, whose measured findings shape the crew model here).

## 0. The principle

**The world is the state; the screen is a viewport.** Three layers, kept apart:

| Layer | Owns | Today |
|---|---|---|
| **World** | one authoritative national state: every live incident from the feeds, every seeded incident a user lights, every enrolled client, every crew and team with its base and beacon; persisted and served by an API | split across the watcher (`wgresponse.cli watch`: feeds, registry, persistence, per-incident domains) and the simulator (`simui.SimSession`: crews, dispatch, safety, the rich view) |
| **Domains** | the physics box around one incident: terrain, spread ensemble, client ETAs, dispatch, crew safety. A domain exists only where a fire meets clients or where a user seeds one; live and seeded fires run the same code | the watcher builds a domain per live incident with protected clients in range (`threat._assess_incident`); the simulator builds one per session on a pre-fetched tile |
| **View** | a map that zooms anywhere; what it draws depends on zoom (national markers → regional perimeters and rings → the full local detail) | the simulator page (one tile per session) and the ops console (one AOI) |

The engine's existing scaling law is what makes a national world tractable:
**a domain is built only where a fire comes within threat range of a protected
client** (or where a user seeds one). WFIGS listed 325 active US incidents on
2026-09-26 (163 with perimeters, the largest 374,000 acres); the number of
domains is driven by fires near clients, not by the national fire count.

## 1. What exists, and the gap

Verified on disk (see the companion docs for the record):

- **Feeds** are national already: WFIGS perimeters + incident points, NASA
  FIRMS hotspots, NWS gridpoint weather. FIRIS and CAL FIRE are California-only
  and simply contribute nothing elsewhere.
- **Per-incident domains** (`threat.py`): geometry + nearby clients + 2 km pad,
  cell size coarsened to stay under 300,000 cells, terrain from LANDFIRE LFPS on
  demand with a local + S3-mirrored cache (a fresh tile costs a 5–8 min LFPS job;
  cached tiles load in seconds). Cycle telemetry: the SoCal shadow AOI runs
  8.5–9.4 min steady on the 1-vCPU service, inside the 15-min freshness rule.
- **The simulator** has the complete local view and the complete crew model:
  crews on real roads with every straight stretch explained, user placement
  snapped to roads, the shaded actual fire with median bands from its edge,
  road-network escapes, old black as a refuge, crews that leave the map out of
  the simulation, sub-stepped time, wind steering of running fires, 13 presets
  including one 60-km valley tile.
- **Roads** are per-tile OSM graphs fetched from Overpass (not viable
  nationally); the routing upgrade path is already named in `travel.py`: a
  self-hosted Valhalla / OSRM on Geofabrik extracts.
- **Clients**: the read-only DynamoDB registry adapter exists
  (`DynamoClientRegistry`, per-bbox queries). **Crews**: the roster and GPS
  beacon endpoint are designed in `LIVE_OPS_PLAN.md` Phase 2, not yet live.
- **Persistence**: `persist.StateStore` with the S3 mirror; App Runner
  services; CodeBuild image pipeline.

**The gap.** Two engines share the physics but not the world. The watcher has
no crew kinematics, no safety sweep and a thin console; the simulator has no
feeds, no registry and one tile per session. Merging them into one world
model is the long pole. Everything else is data plumbing and a viewer.

## 2. Workstreams

Numbered in build order. Each has tickets and an exit gate; each keeps the
engine's laws (contracts.py: default-OFF knobs, byte-identical legacy; the
crew-safety invariants; "absence is not clearance").

### W1 — One world model (the merge)

| # | Ticket | Notes |
|---|---|---|
| 1.1 | `IncidentDomain`: one object hosting terrain, ensemble, ETAs, dispatch, crew kinematics, sweep and audit for one incident, built from either a feed incident or a seeded ignition | lift from `SimSession` (crews/safety) and `threat._assess_incident` (feeds/domain); one code path |
| 1.2 | Seeded incidents: a synthetic `IncidentSource.SEEDED` with point ignition, intensity → weather (the simulator's presets) or live NWS weather at the point | the simulator's `ignite` becomes "seed into the world" |
| 1.3 | World cycle: feeds → incident set (live ∪ seeded) → domains (build / re-anchor / retire) → per-domain cycle → persist; per-domain cycle budget; retire a domain when the incident is out and no seeded fire remains | keep the watcher's staleness gates, zombie-perimeter guards, restart re-fires-nothing |
| 1.4 | Crews are world objects, not session objects: a crew belongs to a team, has a base, a status, a beacon; a domain sees the crews whose reach covers it | replaces the session's round-robin staging; `place_crew` becomes "stage team at" |
| 1.5 | The world's map vs a domain: "left the map" becomes "left the domain"; ground past a domain edge gets a **conservative** fire horizon derived from the nearest in-domain cells (absence is not clearance), closing the open item from the escape work | measured: off-edge "never burns" pulled 29 crews off the corridor map |

**Exit gate:** a seeded fire on the Vail corridor and a live WFIGS fire with
clients nearby run side by side in one process; 0 departure-audit violations;
every 10c row reported; per-tick state hashes of the seeded run byte-identical
to the simulator's for the same commands.

### W2 — Terrain anywhere, in seconds

| # | Ticket | Notes |
|---|---|---|
| 2.1 | National LANDFIRE layers (LF2024 FBFM40, LF2020 slope/aspect/elevation) as cloud-optimized GeoTIFFs on S3, read by window into the existing `TerrainTile` format | replaces the per-domain LFPS job; LFPS stays as the fallback and the refresh path |
| 2.2 | Pre-warmed fast path: the 13 preset tiles and every enrolled community's domain cached as today | instant where clients are |
| 2.3 | Provenance per domain (layer version, window, cell size) in the state, as the tile tools record it | the hindcast scorecard needs it |

**Exit gate:** any point in CONUS yields a 300,000-cell domain in under 10 s
from the COGs; the fingerprints of the 13 presets are unchanged when built from
the COGs at the same cell size (or the difference is recorded and explained).

### W3 — Roads anywhere

| # | Ticket | Notes |
|---|---|---|
| 3.1 | Self-hosted routing service (Valhalla on Geofabrik US extracts) behind `travel.py`'s seam: `/route`, `/sources_to_targets` for dispatch matrices; closures via `exclude_polygons` (the Glenwood Canyon input the corridor program asked for) | ~$100–200 / month; the public OSRM demo is development-only |
| 3.2 | Per-domain road graphs cut from the local OSM extract (osmium / pyrosm) into the `.wgr` format on demand, cached in S3, using the two-tier law the extended graphs already use (all classes in the core, major roads outside) | retires Overpass; keeps crews on real roads in every domain |
| 3.3 | Edge snapping and OSM `track` roads (open items from the roads inspection) | spurs −30…−60 %; mountain access roads |

**Exit gate:** the corridor drive-time matrix reproduced from the routing
service within the class-speed model's tolerance; a domain anywhere in CONUS
gets a road graph in under 30 s.

### W4 — Crews and teams

| # | Ticket | Notes |
|---|---|---|
| 4.1 | Team = crews + base + shift; bases carry measured drive-time reach (the Eagle base matrix generalized: a base's reactive reach is ~45 min, its alerted-regime reach is the whole corridor) | `vail_aspen_response_program.md` §3.3, §3.5 |
| 4.2 | Roster from `wg-technicians`; GPS beacons through the tech-portal endpoint (LIVE_OPS_PLAN 2.2); stale beacon bars dispatch as today | consent / retention policy before enabling |
| 4.3 | The regional / mutual-aid tiers (1 h / 3 h) become real drive times from real bases | the SEMANTICS 9 seam already takes minutes |
| 4.4 | "Stage this team here" on the map (placement generalized to teams and bases) | setup-only, road-snapped, as built |

**Exit gate:** a real crew beaconing from a real base appears in the world,
is dispatched into a seeded domain on the corridor, and every safety law
holds end to end.

### W5 — Clients nationwide

| # | Ticket | Notes |
|---|---|---|
| 5.1 | The DynamoDB registry as the world's client source; per-domain bbox queries; TTL cache | adapter exists |
| 5.2 | Enrollment tooling (`tools/onboard.py`) on any location via W2/W3 | grades homes anywhere, not on preset tiles |
| 5.3 | The ember-threat radius decision (developed cells get no arrival without it; the live default is 0) taken for the world | the Rancho Santa Fe finding |

### W6 — The viewer

| # | Ticket | Notes |
|---|---|---|
| 6.1 | World map (MapLibre or Leaflet) with three zoom tiers: **national** (incident markers scaled by acreage and source, teams and bases, enrolled-client clusters, a ranked "incidents threatening clients" list), **regional** (perimeters, forecast rings, ETAs, alert states), **local** (the full detail built this week) | `/api/world/summary`, `/api/world/region?bbox`, `/api/incident/<id>/view` |
| 6.2 | Seeding: click anywhere → seed a fire → the world builds a domain and the view shows it building; live and seeded incidents look the same, labelled by source | the simulator's ignite flow, generalized |
| 6.3 | Time-scrub across the world (the session history generalized per domain) | LAW 1 time-scrub exists |
| 6.4 | Crew and team panel: placement, status, beacons, escapes, "left the domain" | as built, per domain |

**Exit gate:** zoom from the US to a Vail street in one page; the national tier
shows every WFIGS incident with clients in range; seeding on the corridor shows
the fire in under 15 s (pre-warmed) and under a minute anywhere (W2/W3).

#### W6 thin viewer (2026-09-26)

§3 step 1, built: the existing watcher with its AOI set to CONUS and a zoomable
map over its state. No physics changed and no engine knob added; the watcher,
ThreatMonitor and request handler are extended only by new routes, a registry
choice and one loud-failure fix in the feed pager.

| Piece | What it is |
|---|---|
| `data/aoi_conus.json` | bbox W −125, S 24, E −66, N 50 (Alaska and Hawaii are outside it) + the union of every simulator preset's safe zones (42, all UNVERIFIED, as the corridor AOI) |
| `--registry mock-presets` | `clients.preset_client_registry`: `--preset-homes N` (default 40) demo homes on each of the 13 presets = 520, placed by the simulator's own `place_clients` (RSF city-limits polygon, the 300 m driveway rule on the seven corridor tiles, seed law `f"{key}|{seed}"` — a SimSession of the same preset, N and seed places the same cells, pinned in `tests/test_registry_presets.py`). Same `protected_clients(bbox)` seam; `--registry mock` is byte-identical (its roster fingerprint is pinned from 09f94b5) |
| `/world` (`console/world.html`) | one full-screen Leaflet map, three tiers by zoom. **National ≤ 6**: every incident as a marker scaled by acreage and coloured by source (WFIGS / FIRIS / CAL FIRE / FIRMS / SEEDED), a white ring + client-count badge when it built a spread domain, enrolled-client clusters, crews and safe zones small, and the ranked list "incidents threatening clients" (domain incidents first by earliest client P10, then the rest by acreage; a click flies to it). **Regional 7–11**: feed perimeters (else the point), P50 rings at the cycle time + 0/6/12/24/48/72 h, a "N homes · P10 +x h" label per domain, clients in the console's ladder colours, crews with status. **Local ≥ 12**: plan legs (crew → stops → egress zone, straight between plan points), egress zones ringed, client cards (state, P10/P50, crew stop and margin or the unserved reason); the shaded fire is W1's merge, and a visible "local detail arrives with the world merge (W1)" note sits where it will be drawn. Header: cycle clock + the console's age / staleness chip, AOI, counts, tier, source legend; hovering a marker says "why no domain?" in the cycle's own words. The offline SVG shim gained zoom (the same web-mercator law), so the tiers work air-gapped (`?nomap=1`) |
| `GET /api/world/summary` | `wgresponse.worldview`, authed and read-only: the four things `/api/state` does not carry — domains (clients, earliest client P10/P50, grid, phase times), the per-incident note, display geometry (perimeters simplified for display, P50 rings traced by the simulator's own tracer), counts + the ranked list. Built once per cycle; `?since=<t_s>` answers `{"unchanged": true}` so the 15 s poll stays light |
| Feed checks at CONUS scale | the ArcGIS pager's page cap is now LOUD (a fetch that runs out of pages while the server still reports more keeps its features but reports `last_fetch_ok=False`: absence is not clearance); FIRMS over a national AOI is one legal area request (the FIRMS docs' maximum boundary is the whole world) and a loud startup note on what does scale |

**The national run** (2026-09-26 21:32–22:34 PDT, this machine, real feeds,
live LANDFIRE via LFPS, the production physics flags, `--period 60`):

| | |
|---|---|
| National fetch | WFIGS incident points: 414 US-wide, **378 in the CONUS envelope** (283 wildfire, 92 prescribed — dropped by design —, 3 complex); perimeters 163 → **129**; FIRIS 439 (California-only); CAL FIRE 10. Every count equal to the layer's `returnCountOnly`, each in ONE page (maxRecordCount 2,000, no `exceededTransferLimit`): **nothing dropped**. 290 WFIGS + 439 FIRIS + 10 CAL FIRE → 537 fused → 280 stale-gated → **257 incidents on the map** (256 from cycle 2) |
| Domains | **18 of 257**: 14 LA County incidents (OTIS, DARK, ALAMO, LUCAS, ten LAC-numbered) whose 60 km radius reaches the Malibu demo homes, HIGHLAND and the 100%-contained Chileno Fire near Santa Rosa, DORADO near Rancho Santa Fe, and the 7,389-acre Willow fire reaching **204** demo homes across six Colorado presets. Grids 45,067–300,544 cells (Willow at the 300,000-cell cap, 129.5 m cells). The other 239 are markers, each with the cycle's "no protected clients within 60 km; spread skipped" |
| Cycle 1 (cold) | **56.8 min**: feeds 29.0 s; assess 56.3 min, of which **55.7 min is 18 fresh LFPS tile jobs** (155–435 s each, mean 186 s, sequential) — spread 19.2 s and weather 13.4 s in total; alerts 0.04 s; dispatch 1.2 s. 100 alert events, 4 extreme-regime entries (HIGHLAND, LAC-345537, Chileno, DORADO), not degraded |
| Cycles 2–5 (warm) | **39–49 s** each (the tile cache hits: terrain 1.2 s for all 18 domains): feeds 14–23 s, assess ~24 s (spread ~19 s — Willow 10.5 s, Chileno 4.3 s), dispatch ~1.1 s; well inside the 5-minute period and the 15-minute freshness rule while most of these fires do not spread |
| Memory (working set / private) | 231 / 955 MB with the roster loaded; 402 / 1,127 MB after the cold cycle; **peak 746 / 1,469 MB** at the start of cycle 2 (the last result's 18 ensembles are still held while the next 18 are built); 570–600 / ~1,300 MB steady. Tile cache 144 MB on disk (18 tiles + 18 job zips) |
| Viewer payloads | `/api/state` 276 KB; `/api/world/summary` 373 KB built in 0.31 s once per cycle (0.03 s cached), 63 bytes when `?since=` finds the cycle unchanged |
| Alerts it produced | 520 demo homes: 418 none, **91 EXTREME_24H, 11 IMPACTED** — all from the two contained fires in finding 6 |

**What strains at CONUS scale** (findings; only the pager was changed):

1. **Terrain per incident is the cold-cycle cost.** Every domain fetches its
   own LFPS tile, sequentially, at 155–435 s per job. The terrain cache
   keys on the exact domain bbox, so the 14 LA County incidents whose domains
   each wrap 26–40 of the same 40 Malibu demo homes cost 14 jobs and 14
   near-duplicate tiles. W2's national COGs remove this; an interim is snapping domain boxes
   to a shared grid so neighbouring incidents share a cached tile.
2. **Feed volume at full geometry.** One CONUS cycle downloads ~52 MB: WFIGS
   perimeters 29.3 MB for 129 polygons (4,513 rings; 126,102 vertices even
   after the adapter's 200-vertex subsample), FIRIS 23.0 MB for 439 polygons,
   incident points 0.12 MB — ~15 GB a day at the 5-minute period. ArcGIS
   `maxAllowableOffset` would shrink it, but it changes the rings spread
   seeds from, so it is a default-OFF engine knob for a later ticket, not a
   viewer change. (The viewer draws a display copy: the 117 fresh perimeter
   incidents' 4,509 rings / 119,757 vertices become 262 rings / 9,662
   vertices, ~210 KB, in 0.1 s.)
3. **The staleness gate carries the national picture.** 537 fused incidents,
   280 excluded as older than 7 days (236 of them FIRIS "current" polygons;
   median age 1,829 h, oldest 6,316 h), leaving 257. The exclusion note lists
   every id with its age — an ~8 KB warning line per cycle in the log and in
   `/api/state` notes.
4. **The pager's page cap was a silent drop** (fixed: loud, data kept, fetch
   marked unhealthy). Today every national count matched `returnCountOnly`
   in one page (points 378 = 283 WF + 92 RX + 3 CX, the RX dropped by
   design; perimeters 129; FIRIS 439). Paging without `orderByFields` is not
   stable per the ArcGIS docs — moot at one page, a note for the day a layer
   passes 2,000 features.
5. **FIRMS** (not armed on this machine — no `FIRMS_MAP_KEY`): CONUS is one
   legal area request (the docs' maximum boundary is the whole world; a
   large request may count as several of the key's 5,000 transactions per
   10 minutes). Downstream is quadratic: a synthetic 6,000-detection CONUS
   day clusters into 5,170 FIRMS incidents in 8.5 s and fuses in 3.2 s per
   cycle, and a FIRMS cluster never fuses with the WFIGS fire it sits on
   (synthetic name, no IRWIN id), so one fire near clients can build several
   domains. Spatial fusion (a hotspot inside or near a perimeter / incident
   point) is the prerequisite for national FIRMS.
6. **Days-old polygons of contained fires free-run into alerts** (engine
   policy; not changed here — an owner decision). The perimeter-provenance
   rule (a polygon burns at its OWN timestamp, never the fused record's) has
   no ceiling: Chileno Fire (341 ac, **100% contained**, record 4.7 h old)
   carries a FIRIS polygon 148 h old, and the engine spread it forward 148 h
   under the current forecast — the modeled burn at the cycle time fills
   most of its ~40 × 25 km domain and **11 Santa Rosa demo homes 23 km away
   read IMPACTED** (P50 arrival 24–39 h in the past); Willow (80% contained,
   polygon 249 h old) put **87 Colorado demo homes in EXTREME_24H**. The
   7-day staleness gate reads the record's time, not the polygon's, so it
   passes both; the containment WATCH ceiling (`containment_gate_enabled`,
   default OFF, not in the production flag set) would cap Chileno but not
   Willow (80% < 90%). Options: cap a polygon's free-run at a few hours past
   the newest observation, treat a contained fire's polygon as the fire, or
   arm the containment gate. `docs/img/world_viewer/w6_regional.jpg` and
   `w6_regional_ranked1.jpg` show both; the SoCal shadow service runs the
   same rule.
7. **Memory holds every domain twice at the cycle boundary.** The last
   CycleResult keeps all 18 ensembles (members + p10/p50/p90 as Python float
   lists, ~10 MB per raster at 300,000 cells) while the next cycle builds
   its own: a 746 MB working-set peak with mostly non-spreading fires. A busy
   season on the 1-vCPU watch service is a W7 sizing item (the worker fleet,
   or not retaining the members).
8. **One home, many domains.** The Malibu demo homes sit inside up to 14 LA
   County domains at once (842 client assessments for 520 homes); every domain
   re-spreads the same ground and re-assesses the same homes. Correct for
   the ladder (pairs key on client + incident) but it multiplies compute and
   alert pairs — a W1 `IncidentDomain` shared by nearby incidents would not.
9. **No crews in the national run** (the watcher has none until beacons
   arrive), so every dispatch job was unserved (91) and the local tier's
   plan legs were empty; they render from `/api/state`'s plan when crews
   beacon (pinned at source level in `tests/test_world_viewer.py`).

Start command (the production flags of `FIX_PROGRAM_2026-09-11.md`, dry-notify,
demo roster; `WGRESP_DEV_OPEN=1` only on a local box — a deployment keeps
`WGRESP_API_KEY` and the page prompts for it on the first 401):

```
py -3 -m wgresponse.cli watch --aoi data/aoi_conus.json --registry mock-presets \
    --dry-notify --gust-blend 0.5 --grass-floor 0.10 --floor-gate \
    --model-error 0.71 --extreme-regime --fail-closed --ember-radius 200 \
    --p50-bound --solver-evals 20000 --phi-vector --state-dir state --port 8877
# then open http://127.0.0.1:8877/world
```

The national run used `--period 60` to see several cycles quickly; the
production cadence is the default 300 s. Screenshots of that run
(`scratch_review/world_viewer_shots.py`, headless Chrome over the live
daemon, cycle 5) in `docs/img/world_viewer/`: `w6_national.jpg` (all 256
incidents, 18 domain badges, client clusters, the ranked list),
`w6_regional.jpg` (Willow and the Vail → Aspen corridor homes, the modeled
burn of finding 6), `w6_regional_ranked1.jpg` (Chileno Fire over Santa
Rosa), `w6_regional_malibu.jpg` (the 14 LA County domains),
`w6_local.jpg` (OTIS above Malibu: homes, client cards, the W1 note),
`w6_why_no_domain.jpg` (the hover on a 373,927-acre complex with no homes
in range) and `w6_national_offline.jpg` (the air-gapped shim).

**Open (W6 proper):** `/api/world/region?bbox` and `/api/incident/<id>/view`
(the thin viewer draws every incident in every tier — fine at 256, not at
thousands of FIRMS clusters); teams / bases on the national tier (W4);
seeding (6.2, the other half of W1's first piece); time-scrub (6.3).

### W7 — Scale, cost, operations

| # | Ticket | Notes |
|---|---|---|
| 7.1 | Worker fleet: domains run in parallel across a container fleet (ECS / Fargate; the corridor grid ran 12 in parallel on the dev box) instead of the 1-vCPU service; per-domain 15-min freshness | cost scales with fires near clients, near zero today |
| 7.2 | Shared caches: terrain windows, road graphs, routing matrices in S3; one RoadGraph per file per process (as built) | |
| 7.3 | Alarms: feed staleness, domain cycle overrun, violations > 0, LFPS / COG failures; on-call runbook | LIVE_OPS_PLAN 1.3 |
| 7.4 | Security: the world API behind staff auth; the public simulator becomes a read-only demo of seeded domains | |

### W8 — Validation and safety at scale (runs throughout)

| # | Ticket | Notes |
|---|---|---|
| 8.1 | Nightly hindcast scorecard across every live incident with clients in range (`tools/hindcast_*`) instead of hand-picked corpora; the two-sided decision rule stands | `docs/calibration/SCORECARD.md` |
| 8.2 | Safety invariants as world-level tests: 0 departure violations, every 10c row reported, old-black forgiveness only for black already old at step start, conservative off-domain horizon, no crew silently lost | |
| 8.3 | Good-black sizing: doctrine wants a burned area around the crew, the model judges one cell; measure and decide before crews rest near flame by rule | corridor: 60 crew-ticks 113–300 m from flame |
| 8.4 | The median bound and the honest band stay the operating physics; the extreme-spread posture remains data-limited (n = 1) | `FIX_PROGRAM_2026-09-11.md` |

## 3. Sequencing and what to build first

1. **Now (days):** a thin W6 over the existing watcher — set its AOI to CONUS
   and put a zoomable map over `/api/state`. It costs nothing new and shows the
   national picture immediately, because the watcher already skips fires with
   no clients nearby.
2. **Next (weeks, the long pole):** W1, the engine merge, with seeding (1.2)
   first so the simulator's users see no regression.
3. **Then:** W2 and W3 together — they are what make seeding work anywhere.
4. **Then:** W4 and W5 as the business enrolls real clients and crews.
5. **Throughout:** W7 as domains multiply; W8 from day one.

## 4. Honest boundaries

- Seeding anywhere is instant only once the national terrain layer (W2)
  exists; until then a fresh domain takes minutes.
- Roads outside pre-fetched areas are straight lines until W3.
- The off-domain "never burns" reading and per-cell old black both need their
  conservative fixes (1.5, 8.3) before domains multiply.
- The national tier will look empty until real clients are in the registry:
  the world computes only where fires meet clients, by design.
- Every coating number remains a coating rate, not a survival rate, until gel
  effectiveness data exists (`OPEN_QUESTIONS.md`).
