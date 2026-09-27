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
