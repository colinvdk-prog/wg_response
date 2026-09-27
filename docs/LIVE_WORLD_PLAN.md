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

#### W1 seeded incidents (2026-09-26)

Ticket 1.2 is built, and the first slice of 1.3 (live ∪ seeded in one
cycle): **a user lights a fire at a point and the live watcher runs it
through the same code as a feed incident.** The law is contracts SAFETY
SEMANTICS 12; the module is `wgresponse/seeded.py`.

| Question | Answer as built |
|---|---|
| Identity | `SeededIgnition` (contracts): id `seeded:<uuid>` (a name-based uuid5 over the store sequence, ignition time and point, so a replayed command script re-mints the same ids — the ensemble is seeded on the incident id), `IncidentSource.SEEDED`, lat/lon, `t_ignite_s`, intensity preset **or** explicit `SeedWeather`, optional `wind_dir_deg`, `created_by`, `note`. |
| Fusion | Seeds are unioned **after** `fuse_incidents`, and `fuse_incidents` itself never joins a seeded incident with anything (IRWIN, name, distance) — pinned with a seeded record carrying a feed fire's IRWIN id, name and point. |
| Geometry | A point incident: no perimeter, acreage 0, containment unknown. **Its observed geometry stays the ignition point**; every cycle spreads it from the point at `now_s`, exactly like a feed point-incident without a perimeter (pinned: its ensemble equals a direct `ensemble_arrival` of the point at now). The watcher has no ground truth for live fires, so the simulator's "actual fire" (the nominal member's burn) does not exist here — a seeded fire therefore never grows, and its ETAs stay where the first cycle put them for as long as the weather holds. |
| Guards | Materialized with `updated_at_s = now` every cycle (the seed store vouches for it): the SEMANTICS 9 staleness gate keeps it until it is retired. |
| Client range | Unioned **before** the staleness gate and the client-range filter: a seed with no protected client within the threat radius is listed (`/api/state`, the note says so) and builds no domain — the live law. |
| Weather | Default: the LIVE provider at the fire (NWS gridpoint), as for every incident. Override, for that incident only, through the weather seam (`seeded.seeded_forecast`): `intensity` (the simulator's `INTENSITY_WEATHER` presets) or `weather` sets the conditions (sustained, gust, RH, temperature) in every hour, `wind_dir_deg` fixes the direction; each part left unset stays live; both set = the simulator's own `MockWeather` script, no network. The mode (`live`, `live+fixed-dir`, `preset`, `preset+live-dir`, `explicit`, `explicit+live-dir`) is in the cycle notes and `/api/state`. |
| Retirement | `DELETE /api/seed/<id>` (operator), or automatically once `t_ignite_s + horizon_h` has passed with no pair of it in NOTICE / WATCH / EXTREME and a healthy assessment (`OperatorEvent` kind `seed_retired`). A retired seed simply stops appearing, so its pairs take SEMANTICS 6's absence path **exactly** as a vanished feed incident's (pinned side by side: the same states and events cycle for cycle — CLEARED after `clear_cycles` while every feed is healthy, frozen while any is blind). |
| Clients | A seeded fire's notifications go to the DRILL LOG only (`SeedSafeNotifier`, wired by `watch`): never a client channel, whatever notifier the daemon runs. The ladder and its events are unchanged and the drill send is ledgered, so a restart re-fires nothing. |
| API | `POST /api/seed` {lat, lon, intensity?, wind_dir_deg?, weather?, note?, created_by?} → 201 `{incident_id}` (400: bad body / outside the AOI; 409: 12 active); `GET /api/seed[/<id>]` (weather mode, last-cycle domain and phase times, pair states); `DELETE /api/seed/<id>` (404 unknown, 409 already retired). Authed like every write path. A seed / retire persists at once and wakes the loop (the next cycle runs now, not after the period). `/api/state` lists seeded incidents with `source: "seeded"` and gains a `seeds` key once any seed exists; the console draws them with a violet ring. |
| Persistence | Seeds (active + the last 50 retired) ride in the StateStore under an additive `seeded_incidents` key, written only once a seed exists; restart restores them before the first cycle. |
| Byte-identity | With no seed the cycles, the persisted state file and `/api/state` are byte-identical to goldens computed on commit 09f94b5 **before** any of this code existed (`tests/test_seeded_incidents.py`). |

**The drill (2026-09-26, dev box, one watch daemon per phase).** `watch --aoi
data/aoi_vail_aspen.json --registry mock --dry-notify` with the production
physics flags (`--gust-blend 0.5 --grass-floor 0.10 --floor-gate
--model-error 0.71 --extreme-regime --fail-closed --ember-radius 200
--p50-bound --solver-evals 20000 --phi-vector`), live LANDFIRE via LFPS, live
WFIGS / FIRIS / CAL FIRE feeds, an API key, crews beaconing from the Eagle,
Gypsum and Avon zones. The live WFIGS **Willow** fire (39.234, −106.440;
7,389 ac, 80 % contained) was burning inside the AOI with 9 mock clients in
range, so every phase ran **a live WFIGS fire and a seeded fire side by side
in one process** — the W1 exit gate's shape. Every cycle was healthy
(`degraded: false`).

| Cycle | Wall | Where the time went |
|---|---|---|
| Willow, fresh domain | 439.7 s | LFPS terrain 424.6 s · NWS 1.3 s · spread 11.3 s (300,987 cells, 9 clients) · feeds 2.3 s · alerts 0.0002 s · dispatch 0.13 s |
| + seed, fresh domain (phase 1) | 477.2 s | seed: LFPS terrain 462.3 s · weather 0.0002 s (synthetic preset) · spread 1.05 s (300,286 cells at 108.8 m, 9 clients); Willow cached: terrain 0.10 s · NWS 0.24 s · spread 10.8 s |
| + seed, fresh domain (phase 2) | 387.9 s | seed: LFPS 372.6 s · spread 1.70 s (300,492 cells at 114.3 m, 12 clients); dispatch 0.24 s |
| both cached | 13–17 s | seed spread 1.0–1.7 s, Willow spread ~11 s, feeds ~2–3 s |

- **Phase 1 — the literal drill:** HIGH, wind FROM 240, 10 km WSW of the
  Eagle base (4–8 km out is FBFM40 91 in both cached Eagle tiles). `POST` →
  201 (persisted at once, loop woken); a point outside the AOI → 400. The
  seed missed the next cycle by 4 s (cycle 1 had overrun the 300 s period, so
  cycle 2 began at once, before the POST) and joined the one after — a fresh
  LFPS domain with 9 clients in range. Its 9 pairs stayed NONE: the run up the
  Eagle valley stalls in urban / agriculture / barren (the nominal member
  reaches 5,167 cells in 96 h) and every mock client sits S/SW of Eagle,
  upwind or crosswind. **Hard kill** (TerminateProcess, no final persist) →
  restart: the seed row, 18 alert records and 6 ledgered sends came back;
  `restored: true`; the post-restart cycle (14.3 s) re-fired **0** events.
  `DELETE` → 200 (again → 409); absent from the next cycle; listed retired.
- **Phase 2 — a seed that threatens:** HIGH, wind FROM 240, 8 km upwind of
  mock-client-000. Ladder: client-000 NONE → EXTREME_24H (p50 5.14 h), client-007
  NONE → EXTREME_24H on the P10 rung (p10 9.61 h, p50 51.8 h); two DRILL-log
  sends, no client channel. Both homes are also EXTREME for Willow with p10 =
  0 h, so the planner's one-job-per-home dedup kept Willow's (infeasible) job
  and folded the seeded ones away (`dedup_dropped_jobs: 2`). Hard kill →
  restart: seed restored active, **0** events and **0** drill-log lines
  re-fired. `DELETE` → next cycle absent with both pairs still EXTREME (clear
  streak 1; "pair … not assessed this cycle; job skipped", as for a vanished
  feed fire) → the cycle after, both EXTREME → CLEARED (the all-clear sends
  suppressed: both homes still hold Willow alerts — the existing law).
- **Phases 3–5 — dispatch:** HIGH seeds upwind of homes Willow does not
  threaten (client-006 wind 240; client-001 wind 90 and wind 135; phases 4–5
  reuse the cached 12-client domain — terrain 0.09 s). Ladder: EXTREME
  (p50 6.05 h), EXTREME (p50 7.53 h, p10 5.56 h), NOTICE_72H → WATCH (p50
  34.0 h, p10 20.8 h). Every plan listed the seeded job as
  **`no_safe_corridor`**; each retirement walked the pair to CLEARED on the
  second cycle. **Attribution** (phase 5's exact seed, same id, replayed
  through the real monitor + planner + cached tiles): **alone, it is served**
  — crew E2 (Gypsum) → client-001, arrive +1.61 h, margin 18.2 h; **with the
  live Willow ensemble present, the same job is `no_safe_corridor`.**
  Client-001 is ~69 km from Willow, outside Willow's domain: the fail-closed
  corridor law bounds every off-raster sample by Willow's reached-boundary
  arrival + distance at 1.7 m/s, so a large live fire vetoes routes tens of
  km beyond its own domain. Conservative by design — and in a multi-fire
  world it is the dominant dispatch constraint (ticket 1.5's question).

`POST` / `GET` / `DELETE` and every 400 / 404 / 405 / 409 / 401 path are also
pinned over real HTTP in `tests/test_seed_api.py`.

What remains of W1:

- **1.1 `IncidentDomain`** — one object hosting terrain, ensemble, ETAs,
  dispatch, crew kinematics, sweep and audit per incident. Today the watcher
  still rebuilds every domain from scratch each cycle, and a seeded fire is a
  point re-lit at `now`; with 1.1 a seeded domain can carry the simulator's
  ground truth (the nominal burn, re-anchored from its own history) and the
  exit gate's "per-tick state hashes byte-identical to the simulator's" becomes
  testable. The seeded weather already uses the simulator's own
  `MockWeather` script and preset table, so that comparison starts from the
  same weather.
- **1.3 (rest)** — domain lifecycle (build / re-anchor / retire as objects,
  not per-cycle rebuilds) and a per-domain cycle budget: a seed with clients
  in range costs a full ~300,000-cell ensemble every cycle.
- **1.4 crews as world objects** — the watcher plans with beacon crews only;
  seeded domains see no simulator crews.
- **1.5 left-the-domain / conservative off-domain horizon** — now with a
  measured stake: the fail-closed off-raster bound (1.7 m/s from every reached
  boundary cell) let the live Willow fire veto a seeded job ~69 km away that
  the planner serves with an 18.2 h margin when Willow is absent. A
  per-domain horizon that knows where each fire can actually go is what keeps
  one large fire from freezing dispatch across a region.
- **Policy before real crews (W4):** a seeded fire is real to the engine —
  its P10 enters every corridor check and its jobs enter the plan — so on a
  daemon with real crews a drill would re-route them. The drill-safe notifier
  covers clients only; drills on a live crew roster need a drill window, a
  drill flag on crews, or a separate drill world.

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

#### W3.2 road graphs from OSM extracts (2026-09-26)

`tools/fetch_roads_pbf.py` cuts a domain `.wgr` from a local Geofabrik
`.osm.pbf` and passes the ways, unchanged, through
`tools.fetch_sim_roads.build_graph`. Same highway regex, same simplification,
`roads.save_road_graph` on the way out. A preset `--key` uses
`bbox_for(LOCATIONS[key])` formatted the way the Overpass query sends it
(each edge to 6 decimals): a way is kept when any node lies inside that
inclusive box, and the way's full geometry is kept, not clipped.

**Dependency.** osmium 4.3.1 (pyosmium). On this Windows / CPython 3.14.5
machine, `py -3 -m pip install osmium` installed
`osmium-4.3.1-cp314-cp314-win_amd64.whl`. pyrosm does not install here:
`py -3 -m pip install pyrosm --only-binary=:all:` fails because `cykhash`
has no cp314 wheel, and pyrosm would return a GeoDataFrame rather than the
Overpass way elements `build_graph` already consumes. A hand-rolled protobuf
reader was unnecessary once the osmium wheel imported. The extra is
recorded in `requirements.txt`; cached `.wgr` files stay stdlib-only.

The first pass writes `out/osm/<extract>.osm.pbf.wgridx` (highway ways and
coordinates for the whole extract, 126 MB for Colorado: 752,075 ways,
7,211,411 node refs). Later domains reuse it. Colorado extract:
`colorado-latest.osm.pbf`, 365 MB, MD5 `8e574e1c76c0b6af264df52e1da95a34`,
Geofabrik timestamp `2026-09-26T20:22:51Z`. The committed Overpass graphs
were fetched 2026-09-24 (vail, eagle) and 2026-09-26T01:01Z (eagle_vail),
so counts are not required to be identical; these are the measured deltas.
Snap distances are the gap from the preset point to its nearest graph node.
Every safe zone and the location center agreed exactly (snap points 0 m
apart). `roads.route` uses the same seeded WUI pair as `tests/test_roads.py`.

| key | nodes (ov → pbf) | edges (ov → pbf) | LCC % (ov → pbf) | safe-zone snap \|Δ\| | route on_roads | route dist |
|---|---|---|---|---|---|---|
| vail_co | 1,170 → 1,170 (+0.00%) | 1,425 → 1,425 (+0.00%) | 98.32 → 98.32 (+0.00 pp) | 0.0 m (z1 31.0, z2 55.9, z3 17.1) | True / True | 7,137.4 m (+0.00%) |
| eagle_co | 2,124 → 2,140 (+0.75%) | 2,774 → 2,797 (+0.83%) | 99.07 → 99.07 (+0.00 pp) | 0.0 m (z1 18.2, z2 20.5, z3 889.4) | True / True | 5,458.9 m (+0.00%) |
| eagle_vail_co | 6,836 → 6,854 (+0.26%) | 8,462 → 8,486 (+0.28%) | 93.18 → 93.20 (+0.02 pp) | 0.0 m (z1 18.2, z2 20.5, z3 270.0, z4 4.8, z5 55.9, z6 31.0) | True / True | 27,275.1 m (+0.00%) |

Wall time and peak working set, separate processes, after the memory
counters were read correctly (`GetProcessMemoryInfo` needs ctypes
`argtypes` on Win64):

| run | wall | peak RSS | peak commit |
|---|---|---|---|
| vail_co cold (index build + cut) | 26.4 s | 1,290 MB | 1,591 MB |
| vail_co warm (index reused) | 0.12 s | 182 MB | 181 MB |
| eagle_co warm | 0.14 s | 182 MB | 181 MB |
| eagle_vail_co warm | 0.32 s | 224 MB | 223 MB |

The cold peak is the osmium node-location cache for the whole state
extract (~1.1 GB RSS while ways are scanned, then released; the process
ends near 280 MB). A domain after that pass is a fraction of a second,
under the 2-minute target. `corridor_vail_aspen` is out of scope: it was
built with a major-roads regex, not `HIGHWAY_REGEX`.

Commands used:

```
py -3 -m pip install osmium
py -3 -m pip install pyrosm --only-binary=:all:
curl.exe -L -o out/osm/colorado-latest.osm.pbf https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf
py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key vail_co --out out/roads/vail_co.wgr --compare
py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key eagle_co --out out/roads/eagle_co.wgr --compare
py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key eagle_vail_co --out out/roads/eagle_vail_co.wgr --compare
py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key vail_co --out out/roads/vail_co.wgr --compare --force --rebuild-index
```

`--bbox W,S,E,N --out path` is the direct alternative to `--key`. An
existing output is left in place unless `--force`; a git-tracked
`data/roads/*.wgr` is refused without it.

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

#### Contained fires leave the map; perimeters are anchored where the feed confirmed them (2026-09-27)

Founder ruling, verbatim: *"a fire that is contained and no longer spreading
is removed from the map."* The problem is finding 6 above: a polygon burns at
its own timestamp and is spread forward to now with no ceiling, while the
7-day staleness gate reads the record's age, not the polygon's. Both fixes
are contracts SAFETY SEMANTICS 13, default-OFF knobs, armed by watch flags;
with both off the cycles, `/api/state` and the persisted file are
byte-identical to goldens computed on commit a8bebec on a world where both
bite (`tests/test_contained_removal.py`).

| Question | Answer as built |
|---|---|
| The removal definition (`drop_contained_incidents`, `--drop-contained`) | A feed incident is CONTAINED AND NO LONGER SPREADING when (1) the feed declares it contained: some source reports `containment_pct` >= 100, or the record carries a containment / control / out timestamp; (2) no source reports containment below 100 (fusion keeps every source's value in `meta["containment_by_source"]`; one source below 100 keeps the fire on the map); (3) it is not growing: no source's acreage (`meta["acres_by_source"]`) has risen above that source's value when the fire was first removed. Seeded fires are never removed. |
| Feed fields behind it | WFIGS incident locations: `PercentContained`, `ContainmentDateTime`, `ControlDateTime`, `FireOutDateTime`, `IncidentSize`; WFIGS perimeters: `attr_PercentContained`, `attr_ContainmentDateTime`, `attr_ControlDateTime`, `attr_FireOutDateTime`, `attr_IncidentSize`, `poly_GISAcres`; CAL FIRE: `PercentContained`, `ExtinguishedDate` (its "out"), `AcresBurned`; FIRIS: `area_acres` only (it carries no containment and no incident size). The timestamps ride in `meta` as `contained_at_s` / `controlled_at_s` / `out_at_s`, present only when the feed sends them. Both WFIGS layer schemas were checked live. |
| What removal does | The fire leaves the incident set after the staleness gate and before assessment: no domain, no ETAs, no dispatch job. It is absent from `/api/state` incidents and the world summary. |
| Its pairs | They take the SEMANTICS 6 absence law exactly as a vanished feed incident's do: toward CLEARED after `clear_cycles` while every feed is healthy, frozen while any is blind. IMPACTED stays terminal. This is pinned cycle for cycle against a fire that simply leaves the feed. |
| Nothing disappears silently | One cycle note counts the removed fires and lists each id with its reason. They are also in `CycleResult.removed_incidents`, in `/api/state` as `removed_contained` and `removed_incidents` (present only with the knob armed), in the world summary's `counts.removed_contained`, and in a header chip on `/world` ("contained, removed N", whose hover lists the fires). |
| Return to the map | The decision is re-made every cycle. A source reporting < 100 % brings the fire back that cycle, with a note. So does growth, and then the fire stays on the map until `containment_no_growth_h` (48 h, SEMANTICS 8's no-growth window) passes without further growth. A declaration that merely goes quiet (the fire leaves the WFIGS Current layer, or a feed is blind) keeps the fire removed: only contradiction or growth returns it. |
| The anchor (`perimeter_confirmed_anchor`, `--confirmed-perimeters`) | A polygon whose record reports the same size later than the polygon's own time is seeded at that report time (clamped to now), not at its own age. "Same size" means the record's `reported_acres` is within `perimeter_confirm_tolerance_frac` (2 %) either way of the polygon's own `perimeter_acres`. The anchor needs no newer polygon in any fused source (`perimeter_newest_at_s`). Fusion keeps the freshest record's polygon, which is not always the freshest polygon. |
| The opposite risk | A record reporting more acres than its polygon beyond the tolerance means the polygon lags a growing fire. The legacy, pessimistic seeding at the polygon's own time stands, and the note says "the polygon lags a GROWING fire". The same legacy seeding applies to a smaller size beyond the tolerance, a newer polygon elsewhere, and a missing size or area. Every decision on a domain incident is one `incident <id>: polygon N h old, record M h old — …` note. |
| Staleness | Unchanged. The gate still reads the record's own time: a confirmed polygon whose record then ages past 7 days is excluded (pinned on the recorded Willow payloads). |

**The feeds, checked live (2026-09-27 15:55Z).** The WFIGS *Current* item
description defines the layer as incidents that have "not been declared
contained, controlled, nor out". Accordingly, 0 of the 285 CONUS wildfire
points carried any of those timestamps, while 71 reported
`PercentContained = 100`. The percentage is the working signal; the
timestamps are defense in depth for records caught mid-transition and for
other layers. After fusion and the 7-day gate, **39** fires on the map are at
100 % and no other source contradicts any of them. All 7 CAL FIRE records had
an empty `ExtinguishedDate`. FIRIS `displayStatus` (81 Active / 358 Inactive)
is not used. Record size against polygon area, over 146 joined WFIGS
perimeters: 107 within 1 %, 112 within 2 %, 116 within 5 %; 67 records are
larger than their polygon, 16 of them by more than 5 %. Of the 252 fused
incidents on the map, 108 carry a polygon and 86 of those have a size report
newer than the polygon (76 by a day or more). The anchor takes **67**, removing
29,372 h of free-running spread in total (~440 h per polygon). **10** are held
on the legacy seeding as growing fires, 8 as smaller beyond 2 %, and 1
because another source holds a newer polygon.

**Before / after on ONE recorded snapshot.** The recorded national payloads
were replayed through the real adapters, monitor, ladder and planner, with the
production flags, the 520-home `mock-presets` roster, NWS forecasts cached so
both variants see the same weather, and the W6 tile cache. Each variant ran 3
cycles 5 minutes apart. Recipe, committed in `scratch_review/contained/`:
`feedsnap.py record` writes the payloads into the gitignored `out/contained/`;
then `ab_national.py [--options]`, `replay_69km.py [--options]`,
`snapshot_stats.py`, `largest_fires.py`, `hindcast_guard.py` and
`run_watch.py` (the live run). The results are in `ab_results.json`,
`replay_69km_options.json` and `live_cycles_run1.jsonl`.

| | A: production flags | B: + `--drop-contained --confirmed-perimeters` |
|---|---|---|
| Incidents on the map | 252 | **213** |
| Removed as contained | — | **39** (all "wfigs reports 100% contained") |
| Stale-gated | 282 | 282 |
| Domains | 18 | 16 (Chileno and HIGHLAND removed) |
| 520 demo homes by worst state | **120 EXTREME_24H · 6 IMPACTED** · 394 none | **520 none** |
| Alert events (3 cycles) | 132 | 0 |
| Chileno (341 ac, 100 %, FIRIS polygon 158 h old, record 15 h) | domain with 40 homes; today's forecast left its polygon unspread (1.4 km²), so no alerts this time | removed |
| Willow (7,389 ac, 80 %, polygon 259.7 h old, record 19.9 h, +1.8 %) | polygon spread from its own time: P50 burn at now **1,209.7 km²** (the polygon is 29.4 km²); 120 EXTREME · 6 IMPACTED · 78 none | anchored at the record: P50 burn at now **110.2 km²**; 204 none (earliest client P10 +59.7 h in the live run) |
| Warm cycle | 20.0–21.8 s (assess 17–19 s, dispatch 1.2 s) | 13.5–13.9 s (assess 11.7–12.1 s, nothing to dispatch) |

Every alert of the legacy national picture came from these two cases, as in
finding 6. Yesterday's W6 run gave 91 EXTREME + 11 IMPACTED; today's forecast
kept Chileno quiet and Willow produced them all.

**The live national run** (2026-09-27 16:25–16:30Z, this machine, real
feeds, the production flags plus `--drop-contained --confirmed-perimeters`,
`--period 60`, 6 cycles, tiles cached): each cycle took **32–42 s** (feeds
18.7–28.5 s, assess 13.1–14.3 s, of which terrain 1.0 s and spread
8.9–9.4 s over 16 domains; alerts ≤ 0.01 s; dispatch 0). The map held 212
incidents, **38 removed as contained**, 281 stale-gated and 16 domains. All
520 homes stayed at none with 0 alert events, and no cycle was degraded.
Chileno was removed and Willow anchored (record 20.3 h, polygon 260.1 h)
every cycle. Working set 405–453 MB (peak 664 MB), private 1.2 GB. The
header chip reads "contained, removed 38":
`docs/img/world_viewer/contained_national.jpg`, and
`contained_regional_willow.jpg` beside finding 6's `w6_regional.jpg`, which
shows the same fire before.

**The corridor veto** (`safety._bound_arrival_s`, not changed here). Under
`--fail-closed`, an off-raster corridor sample passed at now is refused
within R = (now + 56 min − t_b) × 1.7 m/s of the domain edge the eroded P10
first reaches at t_b.

| Willow | legacy | both knobs |
|---|---|---|
| National domain (204 homes, 300,544 cells) | edge reached 253.7 h ago, **R 1,574 km** | 13.8 h ago, **R 91 km** |
| W1 drill domain (Vail → Aspen AOI) | 258.6 h ago, **R 1,605 km** | 18.8 h ago, **R 122 km** |
| Gypsum crew → client-001 chord leg, latest safe departure | now − 249.2 h | now − 9.4 h |
| The phase-5 seeded job 69 km away (the drill's own seed, replayed) | `no_safe_corridor` | **still `no_safe_corridor`** |

**The 69-km refusal does not disappear.** The anchored front reaches its own
domain edge 0.9 h after the confirming report on the drill domain (6.1 h on
the national one). The domain pad beyond the fire on its client-free sides is
only 2 km. From that edge the bound extrapolates at 1.7 m/s for the rest of
the time until now: 18.8 h on the drill domain, 13.8 h on the national one. The other 15
national domains never reach their edge (R 0; DARK 0.1 km). The largest live fires
build no domain today (no demo home within 60 km), so they veto nothing. If
one did, its R would be at most (seeding age + 56 min) × 6.2 km/h:

| Fire | Legacy R | New R | Why |
|---|---|---|---|
| Crosswhite (342,923 ac, 99 %) | 1,304 km | 1,304 km | not confirmed: its record is 3.4 % *smaller* than its polygon |
| Little Giant | 312 km | 107 km | anchored |
| Sinlahekin | 1,056 km | 98 km | anchored |
| Widemouth 2 | 6,067 km | 115 km | anchored |
| Aspen Acres | 7,139 km | 118 km | anchored |
| Bench | 7,337 km | 863 km | anchored, but its record is 139 h old |
| Kaiser Canyon (100 %) | 5,031 km | — | removed, no domain |

The refusal lifts only when the modeled free run before now shrinks to a few
hours (options c and d below). That, or capping the off-raster
extrapolation, is the pending decision.

**Containment scaling (report only, nothing built).** Today the only
containment rule is SEMANTICS 8's WATCH ceiling: `containment_gate_enabled`,
default OFF and not in the production flag set; ceiling 90 %; 48 h of
alert-engine-tracked no growth. It caps new ladder escalations and never
touches ETAs, P10, corridors or crew math. The spread physics ignores
containment: an 80 %-contained fire is forecast as a free fire from its
whole perimeter. Willow's record has re-confirmed a polygon unchanged for 259
h (its `poly_PolygonDateTime` is ~68 days old), yet even anchored it is
modeled at 3.7× its reported area by now. The options, on the same snapshot
(national domain; the 69-km column is the drill replay):

| Option | Willow P50 burn at now | Willow homes alerted | Willow veto R | 69-km job |
|---|---|---|---|---|
| legacy | 1,209.7 km² | 120 EXTREME · 6 IMPACTED | 1,574 km | refused |
| (a) built: anchor | 110.2 km² | 0 | 91 km | refused |
| (b) SEMANTICS 8 ceiling at 90 % (default), 48 h tracked | 1,209.7 km² | unchanged (80 < 90) | 1,574 km | refused |
| (b′) the same at 80 % | 1,209.7 km² | 48 WATCH · 1 NOTICE (the ladder only) | 1,574 km | refused |
| (c) the free run since the confirming report × (1 − containment) — 20 % of 19.9 h | 43.2 km² | 0 | 0 km | **served**, margin 18.2 h |
| (d) a confirmed contained polygon IS the fire at now | 29.3 km² | 0 | 0 km | **served**, margin 18.2 h |

(c) and (d) would be default-OFF knobs on top of the anchor. The evidence to
arm one is a hindcast on containment-reporting fires: does reported
containment predict no growth over 24–72 h? Either one also trades away
pessimism that the fail-closed corridor law currently leans on, so it is the
founder's call.

**Hindcast guard.** `tools/hindcast_harness.py` seeds each corpus pair's
first polygon at its own capture time straight into
`spread.ensemble_arrival`. It never passes through `ThreatMonitor`, and the
pairs carry no separate record time or size, so neither knob can apply.
Measured on the August corpus with the deployed variant (gust 0.5,
floor 0.10 gated, model error 0.71, extreme regime, p50 bound, phi vector),
knobs off against on: 27 pairs / 145,760 cells, p10 adequacy 97.7 %, late
miss 2.3 %, and every scored pair byte-identical.

**Open:**

- The removal memory (first-removal baselines, the regrowth latch, the
  sticky "last reported contained") is in-process. A restart forgets it, and
  a fire whose declaration has gone quiet then returns until its record ages
  out, which errs in the pessimistic direction. Persisting it through the
  StateStore should wait for the TEST/LIVE world split.
- A fire declared contained leaves WFIGS Current by that layer's definition.
  Its leftover FIRIS polygon (no containment, no record size) then runs on the
  legacy law until its own 7-day gate.
- Fusion keeps the freshest record's polygon, not the freshest polygon (1
  fire today). The anchor refuses such a group, but the legacy seeding still
  uses the older polygon.
- The 2 % tolerance is a judgement call: Willow reads +1.8 %. A one-sided
  rule (smaller always confirms) would anchor Crosswhite-type records.
- Pairs already IMPACTED by earlier legacy cycles stay IMPACTED.
- The off-raster veto is the dominant remaining dispatch constraint near
  large fires.

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
