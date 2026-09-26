"""wgresponse.contracts — THE LAW.

Every module in wgresponse codes against the types, protocols, units, and semantics
defined here. If a module needs a shape that isn't here, the shape gets added HERE
first. Follows the proven wgdefense convention: contracts is law; stdlib only.

============================================================================
UNITS & CONVENTIONS (non-negotiable)
============================================================================
- Time: absolute times are unix seconds, suffix `_s` (float). Durations meant for
  humans/config use hours `_h` or minutes `_min` (float). Never naive datetimes.
- Distance: meters `_m`, kilometers `_km`. Speed: meters/second `_mps`.
- Angles: compass degrees `_deg`, 0 = north, clockwise.
  WIND DIRECTION IS METEOROLOGICAL: `wind_dir_deg` = direction the wind blows FROM.
  A wind_dir_deg of 0 pushes fire spread toward 180 (southward head).
- Temperatures: Celsius `_c`. Humidity: percent 0-100 `_pct`.
- Money: USD `_usd`.
- Rothermel internals may use imperial (ft/min, lb/ft^2) INSIDE wgresponse.rothermel
  only; every public seam speaks SI.
- Grids are row-major, row 0 = NORTHERNMOST row (image convention), col 0 = west.
- All mocks/simulators are deterministic: RNG seeded via SHA-256 of explicit seed
  strings, never wall-clock.

============================================================================
SAFETY SEMANTICS (the load-bearing rules; do not reinterpret)
============================================================================
Let `eta_p10_s` be the PESSIMISTIC (earliest, 10th percentile) projected fire
arrival at a property from the ensemble, and `eta_p50_s` the median.

1. Alert ladder (per client x incident pair) evaluates on P50:
     NOTICE_72H  when eta_p50 within <= notice_h (72 h)
     EXTREME_24H when eta_p50 within <= extreme_h (24 h)  OR  eta_p10 <= extreme_p10_h (12 h)
   Downgrades require `clear_cycles` consecutive clear evaluations AND
   `clear_margin_h` of headroom (hysteresis — a flickering forecast must not flap states).
2. Crew safety evaluates on P10 (embers precede the modeled front):
     latest_depart_s        = eta_p10_s - egress_buffer_min*60      (30 min rule)
     latest_service_start_s = latest_depart_s - deploy_min*60       (30 min gel deploy)
3. Route safety: every travel leg (ingress AND egress-to-safe-zone) is checked
   against the ERODED P10 arrival grid (morphological min over corridor_erosion_m):
   at each sample point (every corridor_sample_m along the leg), the eroded arrival
   time must exceed the crew's pass time by route_margin_min.
4. A plan stop is feasible only if ALL of: service fits inside
   [max(now, eta_p50_s - gel_window effective seconds), latest_service_start_s],
   ingress corridor safe, egress corridor to a SafeZone safe.
5. GEL MODEL (GraphGel product data, founder 2026-07-11): GraphGel provides
   CLASS A protection at 1/8" DRY film — the DRIED coating is the protection, so
   effectiveness is FLAT across the operational window, not decaying like the
   wet-gel products (Barricade-style evaporative gels) the original placeholder
   modeled. gel_full_strength_h therefore equals gel_window_h (no decay ramp):
   applying early provides good protection; applying nearer the fire is at most
   marginally better (wet+dry), never required. STILL-UNVERIFIED supplier data:
   the outer window itself (weathering/rain wash-off horizon beyond 72 h),
   drying time to reach the rated dry film, coverage rate (volume per home at
   1/8" dry — gates the 15 min/home deploy assumption and inventory sizing),
   and gel_effectiveness=0.85 (Class A flame-spread rating is not a P(save);
   0.85 remains a placeholder). COVERAGE RATE IS QUOTED WET (founder
   2026-07-11): the dry film is much thinner than the wet application, so all
   volume/inventory math must use WET volume per home. THE COATING IS OPAQUE
   (same source): homeowners will not accept preventive/seasonal application —
   application happens ONLY under live threat (the alert-driven WATCH/EXTREME
   window, when occupants have evacuated and survival outweighs aesthetics).
   Seasonal pre-coating is NOT a product strategy; post-event coating removal/
   wash-down is part of the service model. wgdefense's environment-scaled
   coating-life model (~6 h wet-phase) models evaporative products and does NOT
   apply to the dried-film mechanism; gel_env_model_enabled stays default-OFF.
   Code must treat the window as config, never as a constant.
6. ABSENCE IS NOT CLEARANCE (Sprint 2, Council-1 OPS-1). A (client, incident) pair
   with no assessment this cycle FREEZES its alert state; it never advances toward
   CLEARED. Downgrades require ASSESSED evaluations exclusively. Provider failure
   (feed fetch error, weather/terrain/registry exception, empty-after-nonempty feed)
   marks the cycle DEGRADED: affected pairs increment AlertRecord.blind_cycles, and
   blind_cycles >= cfg.blind_alarm_cycles emits an OperatorEvent(kind='degraded') on
   CycleResult.operator_events — operator-facing, NEVER a client notification, and
   never auto-downgrades a client. A pair whose incident was ASSESSED and no longer
   threatens — or whose incident is absent while every fire feed reports a healthy
   fetch (last_fetch_ok convention below) — is assessed-clear and may progress the
   clear streak normally.
   REGISTRY COMPLETENESS (Council-1 verify, attack_partial_registry): a tracked
   pair whose CLIENT silently vanishes from the registry roster while its incident
   is still ACTIVE matches neither trustworthy-clear clause — it is BLIND (freeze +
   OperatorEvent kind='provider_error', detail.step='registry_roster'), because a
   partial scan / data-quality regression is indistinguishable from unenrollment at
   this layer. Only an explicit unenrollment signal (graphgel_enrolled=False on a
   RETURNED record) or incident end releases the pair toward CLEARED.
7. DISPATCH SLACK FLOOR (Sprint 2, Council-1 dispatch F2). Beyond deadline
   feasibility, every planned stop must carry at least cfg.slack_min minutes of
   slack (latest_depart - depart), absorbing travel-model optimism on mountain
   roads. A stop that fits the deadline but not the slack floor is infeasible.
8. CONTAINMENT GATES THE LADDER, NEVER THE CREW MATH (Sprint 2, Council-1 model
   F5). When cfg.containment_gate_enabled: an incident with containment_pct >=
   cfg.containment_watch_ceiling_pct AND no acreage growth for
   cfg.containment_no_growth_h caps that incident's ladder escalation at WATCH
   (growth resumption or containment drop lifts the cap immediately, and any pair
   already above WATCH is not downgraded by the cap — it merely stops NEW
   escalations). The cap NEVER modifies ensembles, P10, corridors, or any crew-
   safety deadline. Default OFF pending founder policy approval.
9. DATA HYGIENE (Sprint 2, Council-1 OPS-6). Incidents whose updated_at_s is
   older than cfg.incident_stale_h are excluded from alerting/dispatch (logged +
   note; FIRIS zombie perimeters measured at median 49 days still active=True).
   perimeter_at_s must be clamped to <= now_s before seeding (feed clock skew
   must not freeze interiors at a future time).
10. CONTINUOUS CREW SAFETY (2026-07-10, founder "crews G1/G2 caught in fire";
   docs/review/crews_caught_report.md). The safety audit governed only gel-job
   DEPARTURES — a crew's live/idle position was never checked, so idle crews
   (and crews resting at a safe zone the fire later overran) burned while
   scoreboard violations stayed 0. THREE binding rules extend (never weaken) the
   P10/corridor/slack law to ALL crew states:
   (a) CONTINUOUS MONITOR: every tick, EVERY crew (idle/AVAILABLE, EN_ROUTE,
       DEPLOYING, EGRESSING, and INBOUND-once-arrived) has its CURRENT cell's
       eroded-P10 arrival checked; unsafe iff arrival < now + egress_buffer_min.
       An unsafe deploying crew aborts per the withdrawal law; an unsafe idle/
       travelling/egressing crew RELOCATES immediately to a currently-safe zone.
   (b) DYNAMIC SAFE ZONES: a safe zone whose eroded-P10 arrival is within the
       egress buffer is NOT a valid egress/staging/anchor target; crews stage
       only at currently-safe zones and re-home as the fire advances. If NO zone
       is currently safe, all crews pull to the latest-arrival zone + a loud
       operator alarm (fail-closed, like no_safe_egress).
   (c) AUDIT COUNTS OVERRUN (the no-reship invariant): the audit ALSO flags a
       violation for any crew whose current cell is burned (nominal arrival <=
       now) or imminent (eroded-P10 < now + egress_buffer_min) — IN EVERY CREW
       MODE (idle, en route, deploying, egressing). scoreboard violations can
       therefore NEVER read 0 while a crew is in the burn. Strict superset of
       the old audit — byte-identical when no crew is exposed.
       2026-09-03 (tri-model review finding A8): the shipped audit exempted
       non-idle crews from the IMMINENT branch ("a fleeing crew is not
       caught"), so 106 crew-ticks in the 64-scenario sweep — up to 5.6 h past
       eroded-P10, all in egress mode — never entered the count, and the only
       guarding test pinned the zero-exposure case. The exemption is GONE. A
       lawful route never stands on an imminent cell at its pass time (the
       corridor margin equals the egress buffer), so anything counted here is
       a real exposure the sweep failed to prevent; rows carry `mode` so the
       scoreboard can attribute idle vs moving exposure, but both COUNT.
   Crew telemetry gains relocating/relocate_reason so the UI shows a crew
   repositioning ("zone threatened"). This is EXTENSION, not weakening: same
   eroded-P10 transform, same egress_buffer_min threshold.
11. EMBER SPOTTING MUST BE FUNCTIONAL (2026-07-10, founder). The Sardoy/ELMFIRE
   spotting hook exists but throws one ember at the MEAN distance (~200 m in
   grass) — measured negligible (never gets ahead of the front). To model real
   fire behaviour (Camp Fire), spotting samples the ember-distance DISTRIBUTION
   (lognormal tail, deterministic per cell/member via seeded_rng) so some brands
   land far downwind AHEAD of the front and ignite new fire there. Default OFF in
   ResponseConfig (live byte-identical; live enablement is a separate calibration
   decision); the SIMULATOR enables it (spotting_enabled=True) with a UI toggle.
   Spotting feeds the P10 ensemble (embers arrive early => more conservative =>
   SEMANTICS 10 protects crews near spot-ignition zones).

============================================================================
MODULE SEAMS (public functions each module MUST expose, exactly)
============================================================================
wgresponse.geo
    haversine_m(a: LatLon, b: LatLon) -> float
    destination(origin: LatLon, bearing_deg: float, dist_m: float) -> LatLon
    bearing_deg(a: LatLon, b: LatLon) -> float
    class GridSpec  (defined here in contracts)
    class Raster    (defined here in contracts)
    sample_polyline(a: LatLon, b: LatLon, step_m: float) -> list[LatLon]
    bbox_around(points: list[LatLon], pad_m: float) -> BBox
    erode_min(r: Raster, radius_m: float) -> Raster        # morphological min filter

wgresponse.fuels
    FUEL_TABLE: dict[int, FuelModel]         # FBFM40 codes -> params (pinned, provenance-commented)
    is_burnable(code: int) -> bool           # NB 91..99 are non-burnable
    dead_fuel_moisture_frac(temp_c: float, rh_pct: float) -> float   # Simard EMC * 1.03
    live_moisture_defaults(season: str) -> tuple[float, float]       # (herb_frac, woody_frac)

wgresponse.rothermel
    surface_ros_mps(fuel: FuelModel, m_dead_frac: float, m_live_herb_frac: float,
                    m_live_woody_frac: float, midflame_wind_mps: float,
                    slope_deg: float) -> RosResult
    midflame_wind_mps(wind10_mps: float, fuel: FuelModel) -> float   # WAF
    ellipse_lb(effective_wind_mps: float) -> float                   # Anderson 1983, capped 8
    directional_ros_mps(head_ros_mps: float, lb: float, theta_deg: float) -> float
        # theta = angle between max-spread direction and the direction of interest

wgresponse.spread
    compute_arrival(tile: TerrainTile, ignition: list[LatLon], wx: WeatherForecast,
                    t0_s: float, cfg: ResponseConfig, moisture_scale: float = 1.0,
                    wind_scale: float = 1.0, dir_offset_deg: float = 0.0,
                    perimeter: list | None = None, perimeter_at_s: float | None = None) -> Raster
        # arrival unix seconds per cell; UNREACHED = math.inf. Time-dependent
        # 16-neighbor Dijkstra; edge traversal speed uses weather at departure time.
        # Sprint 2: dir_offset_deg rotates the wind direction (ensemble knob).
        # Sprint 2 (Council-1 model F4): when perimeter polygons are given, every cell
        # INSIDE them is burned AT perimeter_at_s (the feed's updated_at_s, not now) —
        # the front then advances naturally from that earlier time; interior cells
        # never "re-burn" later; hotspots still seed even when a perimeter exists.
    ensemble_arrival(tile, ignition, wx, t0_s, cfg, incident_id: str = '') -> EnsembleArrival
        # Sprint 2 (Council-1 model F2+F6): members perturb wind SPEED, dead-fuel
        # MOISTURE, and wind DIRECTION (lead-scaled, up to cfg.dir_perturb_deg at max
        # lead), stratified draws, seeded on (incident_id, member_index) — NEVER on
        # wall-clock/t0 — so consecutive cycles are comparable (jitter < 5%).
    eta_at(ens: EnsembleArrival, loc: LatLon) -> ThreatEta

wgresponse.feeds
    class WfigsFeed / FirisFeed / CalFireFeed / FirmsFeed / WatchDutyEnterpriseFeed(stub) / MockFireFeed
    fuse_incidents(batches: list[list[FireIncident]]) -> list[FireIncident]   # join on IRWIN id
    # Health convention (Sprint 2): every adapter maintains `last_fetch_ok: bool`
    # after each fetch_active call — True iff HTTP + parse succeeded (an empty
    # result with last_fetch_ok=True means genuinely zero incidents; False means
    # BLIND, and consumers must apply SAFETY SEMANTICS 6, never treat as clear).

wgresponse.weather
    class NwsWeather / OpenMeteoWeather / MockWeather   (WeatherProvider protocol)

wgresponse.terrain
    class LfpsTerrain / SyntheticTerrain                (TerrainProvider protocol)
    load_ascii_tile(path) / save_ascii_tile(tile, path)  # dependency-free cache format

wgresponse.clients
    class DynamoClientRegistry / MockClientRegistry     (ClientRegistry protocol)

wgresponse.crews
    class CrewTracker   .ingest(beacon) .crews() .crew(id) .stale(now_s) -> list[str]

wgresponse.alerts
    class AlertEngine   .evaluate(pairs: list[ThreatAssessment], now_s) -> list[AlertEvent]
                        .states() -> dict[tuple[str,str], AlertRecord]

wgresponse.threat
    class ThreatMonitor .cycle(now_s) -> CycleResult    # full loop: feeds->wx->terrain->spread->etas->alerts

wgresponse.safety
    corridor_safe(a: LatLon, b: LatLon, depart_s: float, speed_mps: float,
                  eroded_p10: Raster, cfg: ResponseConfig) -> CorridorVerdict
    latest_departure_s(eta_p10_s: float, cfg: ResponseConfig) -> float
    egress_ok(stop_loc: LatLon, depart_s: float, safe_zones: list[SafeZone],
              eroded_p10: Raster, cfg: ResponseConfig) -> CorridorVerdict

wgresponse.dispatch
    plan(crews: list[Crew], jobs: list[DispatchJob], ens: dict[str, EnsembleArrival],
         safe_zones: list[SafeZone], cfg: ResponseConfig, now_s: float,
         incumbent: DispatchPlan | None = None) -> DispatchPlan

wgresponse.travel
    travel_time_s(a: LatLon, b: LatLon, cfg: ResponseConfig) -> float
        # haversine * circuity * fire_traffic_factor / class speed (upgrade path: Valhalla/OSRM)

wgresponse.notify
    class Notifier protocol; SesNotifier(stub wiring), SmsNotifier(stub), LogNotifier

wgresponse.sim
    build_scenario(name, seed) -> Scenario ; run(scenario, hours, cfg) -> SimReport

wgresponse.server
    stdlib http server: POST /api/beacon, GET /api/state, GET /api/plan, GET /console
    auth: X-Internal-API-Key (fail closed unless WGRESP_DEV_OPEN=1)

wgresponse.persist   (Sprint 2, Council-1 OPS-4)
    class StateStore(dir_path)  .save(alert_records, notified_ledger, incumbent_plan, meta)
                                .load() -> restored dicts (or None when absent/corrupt: loud note)
    # Alert/IMPACTED/notification state survives restart; restart re-fires NOTHING.
    # AlertEngine exposes .restore(records) and .notified_ledger() seams for this.

wgresponse.roads   (founder UX request 2026-07-10 — crews travel on real roads)
    class RoadGraph — nodes {id: LatLon}, edges (u, v, highway_class, length_m,
        speed_mps by class table with provenance); built from OSM (Overpass) once
        per simulator location, cached dep-free at data/roads/<key>.wgr.
    load_road_graph(key) -> RoadGraph | None ; save_road_graph(g, path)
    route(g, a: LatLon, b: LatLon, cfg) -> RoadRoute(polyline: list[LatLon],
        time_s, dist_m, on_roads: bool)
        # nearest-node snap both ends + stdlib A*; when no graph or disconnected,
        # falls back to the straight leg with tier-0 travel time and
        # on_roads=False — NEVER silently pretends a road route.
    # Division of authority: dispatch.plan keeps the calibrated tier-0 travel
    # model (circuity x fire-traffic); simulator CREWS MOVE along RoadRoute
    # polylines (ingress AND egress) and each tick re-plans from ACTUAL crew
    # positions, so model-vs-road divergence self-corrects cycle to cycle. The
    # per-tick SAFETY AUDIT judges ACTUAL times and remains authoritative.

wgresponse.simui   (founder request 2026-07-09; docs/SIMULATOR_DESIGN.md is the spec)
    class SimSession — interactive accelerated simulation over REAL engine paths
    (ensemble_arrival / AlertEngine / dispatch.plan / corridor+egress safety; the
    sim SAFETY AUDIT runs every tick and must stay at 0 violations).
    .configure(location_key, n_clients, n_crews, seed, cfg_overrides)  # WUI placement
    FOUNDER ROUND 3 (2026-07-10 pm) — five laws:
    1. TIME SCRUB / HISTORY: the session records the FULL state snapshot of every
       tick in a bounded ring (cfg.history_ticks, oldest evicted); .state_at(tick)
       returns the recorded snapshot VERBATIM (read-only replay — byte-identical
       to what was served live at that tick, so scrubbing back in time confirms
       the run). Server: GET /api/sim/<id>/state?tick=k (404 when evicted/future);
       state() gains history {first_tick, last_tick}. UI: a timeline slider scrubs
       history; scrubbed view is READ-ONLY (a LIVE button returns to now; commands
       while scrubbed are rejected 409, never silently applied to the past).
       reset() clears history.
    2. ROADS ADHERENCE: while a leg claims route_on_roads=true the crew's position
       lies ON its route polyline; EVERY movement leg (staging->stop, stop->stop,
       stop->egress, restage) consults the roads seam. Off-network endpoints join
       the graph via explicit short SPUR segments (spur is the only straight part,
       flagged spur=true in the polyline metadata); whole-leg chord fallback is
       legal ONLY when no graph is loaded or the endpoint is beyond
       cfg.road_spur_max_m from the network (loud note).
       ROADS LEGIBILITY (roads inspection 2026-09-25, founder "there are
       times that the response team does not use roads";
       docs/review/roads_inspection_2026-09-25.md): state.crews[i] gains
       ONE additive, DISPLAY-ONLY key `map_view` (never read by the engine;
       every other key unchanged): moved_path (the road polyline driven
       during the last tick, [] when the crew did not drive), jumped (null |
       {from, to, reason, why} — a non-driven position change, i.e. a
       reinforcement's arrival: the UI places the marker, never slides it),
       trail (runs of the driven road over the last 30 ticks, split at
       jumps), route_why / egress_why (a one-line reason per
       route_segments / egress_segments entry, null for road), queue (the
       road route through a DEPLOYING crew's queued stops, {segments, why}),
       egress_preview (the exact road leg a crew relocated THIS tick starts
       driving next tick, {segments, why}). The UI draws the marker along
       moved_path and the trail along the road, and names the mechanism
       behind every remaining straight spur / chord on hover. EXTENDED fire
       zone sessions load data/roads/<key>_ext.wgr (the whole extended
       tile; loud fallback to the standard graph when missing).
    3. FULL UTILIZATION: a crew that completes its itinerary or its egress returns
       to AVAILABLE (staged at its safe zone) and MUST be eligible for new work on
       the next plan. No crew idles while a feasible pending job exists; the
       planner exhausts eligible crews before leaving jobs unserved for capacity.
    4. TRIAGE ('protect as many homes as possible'): cfg.dispatch_objective =
       'homes' (default) | 'value'. 'homes' maximizes the COUNT of homes protected
       (value only as tiebreak) and is explicitly allowed to SKIP or ABANDON a
       planned-not-started job whose service would cost strictly more savable
       homes elsewhere — unserved reason 'triaged'. Objective-side abandonment
       NEVER weakens SAFETY SEMANTICS 2/3/7 (safety withdrawals keep their own
       law and vocabulary).
    5. GEL RING: every serviced client carries gel_applied=true + gelled_at_s in
       state.clients[]; the UI renders a BLUE outer ring on gelled homes (the
       job-done marker), persisting through later alert-state changes.
    FOUNDER ROUND 4 (2026-07-10, from the live sim-7 Malibu review — docs/review/
    {eta_gap,prioritization}_report.md):
    6. EMBER / ADJACENCY THREAT (model gap): the surface-spread model can never
       reach a home sitting ON a non-burnable (developed 91/93, water 98) cell, so
       such homes get no ETA / no alert / no dispatch even when fire surrounds them
       (~23% of sim clients; the founder's c34). spread.eta_at gains
       fallback_radius_m: when a home's own cell is UNREACHED, it inherits the
       earliest arrival among burnable cells within the radius (reuse geo.erode_min,
       the corridor-erosion transform — preserves p10<=p50<=p90). Default 0.0 =
       byte-identical (live product unchanged); the SIMULATOR sets
       cfg.ember_threat_radius_m (~200 m) so fire-adjacent developed homes DO alert
       and dispatch. Polarity: strictly MORE homes threatened = conservative.
    7. DISPATCH URGENCY (homes mode) — RETIRED FROM THE OBJECTIVE (2026-07-10). An
       urgency term was tried (score += w_urg*urgency(p50)) to protect sooner-to-burn
       homes first, but two adversarial sweeps proved it SERVES FEWER TOTAL homes
       under the realistic (physics-on) fire — the dynamic commitment loop makes
       crews chase imminent homes and lose more overall (measured net −129). So
       urgency is now OUT of the emitted homes objective: the count-maximal SET is
       chosen urgency-blind (homes tiebreak = value only), making served(count)
       BYTE-IDENTICAL for any dispatch_urgency_weight — urgency can never cost a
       protected home. The at-risk-first ORDERING survives as an unwired utility
       (dispatch._urgency_reorder, count-safe within a fixed set) but is NOT applied
       to the emitted plan. cfg.dispatch_urgency_weight is therefore INERT in the
       shipped planner (kept for the utility / experimentation). The SAFETY GATE
       (P10 deadlines, corridors, slack) and 'value' mode are byte-identical.
    FOUNDER ROUND 5 (2026-07-10): REGIONAL / MUTUAL-AID CREWS.
    9. Crews come from THREE regions: SimSession.configure gains n_crews_1h and
       n_crews_3h alongside n_crews (= the in-zone count). In-zone crews stage at
       local safe zones and respond immediately (region='in_zone', available now).
       Reinforcements are OUT OF REGION and MOBILIZE ON THE FIRST IGNITION (the
       incident is what summons mutual aid): each becomes AVAILABLE at
       first_ignition_s + delay, staged at a safe zone (region='reg_1h'/'reg_3h';
       delays = cfg.crew_reinforce_1h_min=60 / crew_reinforce_3h_min=180 minutes).
       Before arrival a reinforcement is status INBOUND with a live arrival
       countdown, EXCLUDED from dispatch (an INBOUND crew is NOT an idle crew — the
       full-utilization law 3 applies only to ARRIVED crews). Arrival is a pure
       function of (ignition time + delay), so it is deterministic and time-scrub
       replay is byte-identical. With no fire ignited, reinforcements stay in
       region on standby (never needed). state.crews[i] gains: region, inbound
       (bool), available_at_s (unix s | null until the first ignition sets the
       mobilization clock), arrival_eta_s (seconds until arrival | null). The UI
       shows INBOUND crews at the map edge toward their region with the countdown,
       and flips them to active at arrival; the itinerary lists their ETA.
    8. PER-CLIENT OUTCOME TAG: state.clients[i].outcome explains why each home is/
       isn't protected, so the map is never arbitrary:
         'saved'                  gelled, fire not yet arrived
         'protected_then_impacted' gelled AND fire arrived (the intended GraphGel
                                   outcome — protected, then faced the fire)
         'abandoned_unsavable'    threatened but no feasible corridor/deadline from
                                   ANY crew (correct triage of a too-close home)
         'triaged'                deprioritized so crews could save more homes
         'in_progress'/'pending'  a crew is or will be assigned
         'not_threatened'         no ETA (fire not projected to reach it)
       Surfaced in state.clients[] + a scoreboard breakdown; the UI colours/labels
       each so 'abandoned_unsavable' reads as a deliberate call, not a miss.
    FIRE ZONE (founder request 2026-07-10): sessions take fire_zone='standard'|
    'extended'. standard = the 18x18 km tile; extended = data/tiles/<key>_ext.wgt
    (~36-48 km, coarser cells) so ignition can sit tens of km from the homes and
    the full 72h NOTICE -> WATCH -> 24h EXTREME arc plays out. CLIENTS ARE ALWAYS
    PLACED IN THE CENTRAL ~18 km COMMUNITY WINDOW regardless of zone (the WUI
    placement mask is the central subgrid on extended tiles); ignition may be
    anywhere on the tile. Extended sessions default horizon_h to >=120 (a far
    fire must stay inside the forecast horizon). Missing _ext tile -> loud note
    + fall back to standard.
    CREW PLACEMENT (founder request 2026-09-26 "allow the crews to be placed on
    the map"): .place_crew(crew_id, lat, lon) / .reset_crews() — SETUP ONLY
    (before the first ignition; afterwards CrewPlacementLocked, HTTP 409 —
    crews then belong to dispatch + SAFETY SEMANTICS 10). Moves an IN-ZONE
    crew's sim position + beacon to the road-graph node nearest the point
    (simui.CREW_PLACE_MAX_ROAD_M = 2 km; farther, off-tile, unknown crew or an
    out-of-region reinforcement -> CrewPlacementError, HTTP 400; no graph ->
    the raw point, loud note). home_base and the egress anchor STAY the safe
    zone (escape targets are never arbitrary points). Recorded in cmd_log,
    replayed by reset() and the client re-roll; no placement = byte-identical
    sessions (state gains one additive key, crew_placement). Server:
    POST /api/sim/<id>/crew {crew_id, lat, lon} | {action: "reset_crews"}.
    .ignite(loc, intensity) / .set_wind(dir_deg) / .set_buffer(egress_buffer_min)
    .tick() -> state dict for the UI   (deterministic given seed + command script)
    Five real-location tiles cached dep-free under data/tiles/ (LFPS-sourced).
    STATE VISUAL CONTRACT (founder UX 2026-07-10; UI + backend build to this):
    "SHADE THE FIRE" (founder decision 2026-09-26, a DISPLAY change — no
    engine/physics effect): the shaded fire is the SIMULATED ACTUAL FIRE — the
    union over fires of cells whose NOMINAL-member arrival <= sim-now, the
    engine's ground truth (the mask the 10c audit and every re-anchor use) —
    and the bands are the ladder's median projection BOUNDED by it. (Until
    then state.burned was the ensemble P50 <= now, which under-drew a wind-
    driven fire ~20x: docs/SIMULATOR_DESIGN.md "Shading the fire".)
      state.burned      = list of rings (list[list[[lat,lon]]]) — cells with
                          NOMINAL arrival <= sim-now (the actual fire, union
                          over fires); GROWS tick over tick.
      state.burned_p50  = the previous state.burned, verbatim: each fire's P50
                          <= sim-now rings (the ladder median's own burned area).
      state.fire_bands  = {"h1": rings, "h5": rings, "h24": rings} — rings of
                          (actual burn UNION {p50 <= sim-now +1/+5/+24 h}): the
                          median projection bounded by the actual fire (render
                          red / orange / yellow), recomputed EVERY tick; nested.
      state.fire_view   = the exact per-cell picture the page shades: row-major
                          runs of classes none|burned|burning|h1|h5|h24 at FULL
                          tile resolution (burned+burning == the nominal mask;
                          burning = inside the fuel's cold-black window; band
                          cells never on the fire; p50 <= now but not yet
                          reached -> h1), the GridSpec to place them, chain-
                          coded cell-edge strokes (the fire's outline, each
                          band's FRONT beyond the fire) and areas_km2 (incl.
                          the P50's own burned area). simui._fire_visuals.
      crews[i].route_polyline / egress_polyline = list[[lat,lon]] road-following
                          paths (roads.route; on_roads flag per leg), which the
                          crew marker follows point-to-point between ticks.
    cfg.egress_buffer_min is the user-explorable safety threshold; changing it
    re-plans immediately. Served-count must be monotonically non-increasing in
    the buffer (property-tested).

wgresponse.cli
    argparse subcommands: simulate | forecast | plan | serve | pull-feeds | watch | simui
    # watch (Sprint 2, Council-1 OPS-3): the operator daemon — runs cycle() on
    # cfg.poll_period_s cadence over a REQUIRED --aoi bbox/zones file, serves the
    # console+state off the live loop, persists via wgresponse.persist, exposes
    # last-cycle age + degraded on /healthz.
============================================================================
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional, Protocol, Sequence

SCHEMA_VERSION = "0.1.0"
UNREACHED = math.inf  # arrival-raster value for cells the fire never reaches


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float

    def __iter__(self):
        yield self.lat
        yield self.lon


@dataclass(frozen=True)
class BBox:
    """west/south/east/north in degrees."""
    west: float
    south: float
    east: float
    north: float

    def contains(self, p: LatLon) -> bool:
        return self.south <= p.lat <= self.north and self.west <= p.lon <= self.east

    def center(self) -> LatLon:
        return LatLon((self.south + self.north) / 2.0, (self.west + self.east) / 2.0)


@dataclass(frozen=True)
class GridSpec:
    """Row-major lattice; row 0 is the NORTH edge, col 0 is the WEST edge.

    Cell (r, c) center latitude  = north_lat - (r + 0.5) * cell_m / M_PER_DEG_LAT
    Cell (r, c) center longitude = west_lon + (c + 0.5) * cell_m / m_per_deg_lon(lat)
    geo.py owns the exact index<->latlon math; every module uses geo's helpers.
    """
    north_lat: float
    west_lon: float
    cell_m: float
    nrows: int
    ncols: int


class Raster:
    """Flat row-major float grid bound to a GridSpec. Defined in contracts so every
    module shares one shape; geo.py provides constructors/utilities."""

    __slots__ = ("spec", "data", "__weakref__")   # weakref: per-raster memo tables (safety.py)

    def __init__(self, spec: GridSpec, data: Optional[list] = None, fill: float = 0.0):
        self.spec = spec
        n = spec.nrows * spec.ncols
        if data is None:
            self.data = [fill] * n
        else:
            if len(data) != n:
                raise ValueError(f"raster data length {len(data)} != {n}")
            self.data = data

    def get(self, r: int, c: int) -> float:
        # A negative index would silently wrap to the far edge of the row
        # above (tri-model review 2026-08-31, code finding): out-of-grid
        # cells are an error here, never a value.
        if r < 0 or c < 0 or r >= self.spec.nrows or c >= self.spec.ncols:
            raise IndexError(f"raster cell ({r}, {c}) outside "
                             f"{self.spec.nrows}x{self.spec.ncols} grid")
        return self.data[r * self.spec.ncols + c]

    def set(self, r: int, c: int, v: float) -> None:
        if r < 0 or c < 0 or r >= self.spec.nrows or c >= self.spec.ncols:
            raise IndexError(f"raster cell ({r}, {c}) outside "
                             f"{self.spec.nrows}x{self.spec.ncols} grid")
        self.data[r * self.spec.ncols + c] = v


# ---------------------------------------------------------------------------
# Fire incidents (fused view over all feeds)
# ---------------------------------------------------------------------------

class IncidentSource(str, Enum):
    WFIGS = "wfigs"
    FIRIS = "firis"
    CALFIRE = "calfire"
    FIRMS = "firms"
    WATCHDUTY = "watchduty"   # enterprise-licensed slot only; unofficial API is ToS-banned
    MOCK = "mock"


@dataclass
class FireIncident:
    incident_id: str                    # canonical: IRWIN id when known, else source-prefixed id
    name: str
    source: IncidentSource
    origin: LatLon                      # point of origin / representative point
    updated_at_s: float
    discovered_at_s: Optional[float] = None
    acreage: Optional[float] = None
    containment_pct: Optional[float] = None
    active: bool = True
    perimeter: Optional[list] = None    # list[list[LatLon]] rings, outer first; None if points-only
    hotspots: Optional[list] = None     # list[LatLon] (e.g. FIRMS detections), newest first
    irwin_id: Optional[str] = None
    source_ids: dict = field(default_factory=dict)   # source -> native id (fusion provenance)
    meta: dict = field(default_factory=dict)

    def ignition_points(self) -> list:
        """Points the spread model seeds from: perimeter ring vertices if present,
        else hotspots, else origin."""
        if self.perimeter:
            return [p for ring in self.perimeter for p in ring]
        if self.hotspots:
            return list(self.hotspots)
        return [self.origin]


class FireFeed(Protocol):
    def fetch_active(self, bbox: Optional[BBox] = None) -> "list[FireIncident]": ...


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WeatherSample:
    t_s: float
    wind_speed_mps: float      # 10-m sustained (rothermel applies WAF internally)
    wind_gust_mps: float
    wind_dir_deg: float        # meteorological: blowing FROM
    rh_pct: float
    temp_c: float


@dataclass
class WeatherForecast:
    loc: LatLon
    samples: list                # list[WeatherSample], hourly, ascending t_s
    source: str = "mock"

    def at(self, t_s: float) -> WeatherSample:
        """Nearest-hour lookup, clamped to the series ends. Series must be non-empty."""
        s = self.samples
        if not s:
            raise ValueError("empty forecast")
        if t_s <= s[0].t_s:
            return s[0]
        if t_s >= s[-1].t_s:
            return s[-1]
        # hourly cadence: index arithmetic, guard with local scan
        i = min(int((t_s - s[0].t_s) // 3600.0), len(s) - 1)
        best = min(range(max(0, i - 1), min(len(s), i + 2)), key=lambda k: abs(s[k].t_s - t_s))
        return s[best]


class WeatherProvider(Protocol):
    def forecast(self, loc: LatLon, horizon_h: float) -> WeatherForecast: ...


# ---------------------------------------------------------------------------
# Terrain & fuel
# ---------------------------------------------------------------------------

@dataclass
class FuelModel:
    """Rothermel parameter set for one FBFM40 model (Scott & Burgan RMRS-GTR-153).
    Loads in kg/m^2 (SI at the seam; rothermel.py converts internally as needed).
    sav_* in 1/m. depth_m in meters. mx_dead is a FRACTION (0-1)."""
    code: int
    name: str
    dynamic: bool                      # dynamic models transfer cured herb load to dead
    load_1h_kgm2: float
    load_10h_kgm2: float
    load_100h_kgm2: float
    load_live_herb_kgm2: float
    load_live_woody_kgm2: float
    sav_1h_1m: float
    sav_live_herb_1m: float
    sav_live_woody_1m: float
    depth_m: float
    mx_dead_frac: float
    heat_kjkg: float = 18608.0         # 8000 BTU/lb default


@dataclass
class TerrainTile:
    """Aligned rasters on one GridSpec. fuel holds FBFM40 integer codes as floats."""
    spec: GridSpec
    fuel: Raster
    slope_deg: Raster
    aspect_deg: Raster                 # downslope-facing compass direction
    elev_m: Raster
    source: str = "synthetic"


class TerrainProvider(Protocol):
    def tile(self, bbox: BBox, cell_m: float) -> TerrainTile: ...


# ---------------------------------------------------------------------------
# Spread outputs
# ---------------------------------------------------------------------------

@dataclass
class RosResult:
    ros_mps: float                     # head-fire surface rate of spread
    effective_wind_mps: float          # for L/B ellipse
    flame_length_m: float = 0.0
    intensity_kwm: float = 0.0


@dataclass
class EnsembleArrival:
    """Arrival rasters (unix s) for perturbed reruns + convenience quantile rasters.
    p10 = EARLIEST/pessimistic arrival (10th percentile of arrival time)."""
    spec: GridSpec
    members: list                      # list[Raster]
    p10: Raster
    p50: Raster
    p90: Raster
    t0_s: float
    incident_id: str = ""


@dataclass
class ThreatEta:
    """Per-location arrival estimate. Values are ABSOLUTE unix seconds; UNREACHED=inf."""
    eta_p10_s: float
    eta_p50_s: float
    eta_p90_s: float

    def hours_out(self, now_s: float) -> tuple:
        f = lambda t: UNREACHED if t == UNREACHED else max(0.0, (t - now_s) / 3600.0)
        return (f(self.eta_p10_s), f(self.eta_p50_s), f(self.eta_p90_s))


# ---------------------------------------------------------------------------
# Clients, crews, safe zones
# ---------------------------------------------------------------------------

@dataclass
class ClientProperty:
    client_id: str
    loc: LatLon
    address: str = ""
    name: str = ""
    value_usd: float = 1_000_000.0
    graphgel_enrolled: bool = True
    priority_weight: float = 1.0       # contract tier / strategic weight, >= 0
    contact_email: str = ""
    contact_phone: str = ""


class ClientRegistry(Protocol):
    def protected_clients(self, bbox: Optional[BBox] = None) -> "list[ClientProperty]": ...


class CrewStatus(str, Enum):
    AVAILABLE = "available"
    EN_ROUTE = "en_route"
    DEPLOYING = "deploying"
    EGRESSING = "egressing"
    OFF_DUTY = "off_duty"
    UNKNOWN = "unknown"
    INBOUND = "inbound"      # out-of-region reinforcement mobilizing; not yet arrived / not dispatch-eligible


@dataclass(frozen=True)
class CrewBeacon:
    crew_id: str
    loc: LatLon
    t_s: float
    status: CrewStatus = CrewStatus.AVAILABLE
    accuracy_m: float = 30.0
    speed_mps: float = 0.0


@dataclass
class Crew:
    crew_id: str
    name: str = ""
    status: CrewStatus = CrewStatus.UNKNOWN
    last_beacon: Optional[CrewBeacon] = None
    home_base: Optional[LatLon] = None
    members: int = 2

    def loc(self) -> Optional[LatLon]:
        return self.last_beacon.loc if self.last_beacon else self.home_base


@dataclass(frozen=True)
class SafeZone:
    zone_id: str
    loc: LatLon
    name: str = ""


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertState(str, Enum):
    NONE = "none"
    NOTICE_72H = "notice_72h"
    WATCH = "watch"
    EXTREME_24H = "extreme_24h"
    CLEARED = "cleared"
    IMPACTED = "impacted"


# Legal ladder transitions; AlertEngine must reject anything else.
ALERT_TRANSITIONS = {
    AlertState.NONE:        {AlertState.NOTICE_72H, AlertState.EXTREME_24H},
    AlertState.NOTICE_72H:  {AlertState.WATCH, AlertState.EXTREME_24H, AlertState.CLEARED},
    AlertState.WATCH:       {AlertState.EXTREME_24H, AlertState.CLEARED},
    AlertState.EXTREME_24H: {AlertState.WATCH, AlertState.CLEARED, AlertState.IMPACTED},
    AlertState.CLEARED:     {AlertState.NOTICE_72H, AlertState.EXTREME_24H},
    AlertState.IMPACTED:    set(),
}


@dataclass
class ThreatAssessment:
    client_id: str
    incident_id: str
    eta: ThreatEta
    computed_at_s: float


@dataclass
class AlertRecord:
    client_id: str
    incident_id: str
    state: AlertState = AlertState.NONE
    entered_at_s: float = 0.0
    last_eval_s: float = 0.0
    clear_streak: int = 0
    blind_cycles: int = 0             # consecutive cycles with NO assessment (SAFETY SEMANTICS 6)
    history: list = field(default_factory=list)   # list[(t_s, AlertState)]


@dataclass(frozen=True)
class OperatorEvent:
    """Operator-facing degradation/ops alarm (SAFETY SEMANTICS 6). Never sent to
    clients; surfaced on CycleResult.operator_events, /api/state, and the console."""
    t_s: float
    kind: str                          # 'degraded' | 'feed_blind' | 'provider_error' | 'recovered'
    message: str
    incident_id: str = ""
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AlertEvent:
    t_s: float
    client_id: str
    incident_id: str
    prev: AlertState
    state: AlertState
    eta_p50_h: float
    eta_p10_h: float
    message: str = ""


class Notifier(Protocol):
    def send(self, event: AlertEvent, client: ClientProperty) -> bool: ...


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@dataclass
class DispatchJob:
    """One protected property needing gel before a fire arrives."""
    client: ClientProperty
    incident_id: str
    eta: ThreatEta
    # Derived at plan time (safety.py owns the math; stored for explainability):
    latest_depart_s: float = 0.0
    latest_service_start_s: float = 0.0
    earliest_useful_start_s: float = 0.0     # gel-window opening (p50 - gel window)


@dataclass
class PlannedStop:
    client_id: str
    incident_id: str
    arrive_s: float
    service_start_s: float
    depart_s: float
    latest_depart_s: float
    margin_h: float                    # (latest_depart - depart) in hours; >= 0 iff feasible
    gel_fresh_frac: float = 1.0        # projected gel effectiveness remaining at fire arrival


@dataclass
class CrewPlan:
    crew_id: str
    stops: list = field(default_factory=list)          # list[PlannedStop]
    egress_zone_id: str = ""
    committed_until_s: float = 0.0                     # stops starting before this are frozen


@dataclass
class UnservedJob:
    client_id: str
    incident_id: str
    reason: str                        # "infeasible_deadline" | "no_safe_corridor" | "capacity" | ...


@dataclass
class DispatchPlan:
    computed_at_s: float
    plans: dict = field(default_factory=dict)          # crew_id -> CrewPlan
    unserved: list = field(default_factory=list)       # list[UnservedJob]
    objective: float = 0.0
    objective_breakdown: dict = field(default_factory=dict)
    churn_from_incumbent: float = 0.0


@dataclass
class CorridorVerdict:
    safe: bool
    reason: str = ""
    worst_margin_h: float = UNREACHED  # min(arrival - pass_time) along the leg, hours


# ---------------------------------------------------------------------------
# Orchestration outputs
# ---------------------------------------------------------------------------

@dataclass
class CycleResult:
    t_s: float
    incidents: list = field(default_factory=list)      # list[FireIncident]
    assessments: list = field(default_factory=list)    # list[ThreatAssessment]
    events: list = field(default_factory=list)         # list[AlertEvent]
    plan: Optional[DispatchPlan] = None
    ensembles: dict = field(default_factory=dict)      # incident_id -> EnsembleArrival
    notes: list = field(default_factory=list)
    operator_events: list = field(default_factory=list)  # list[OperatorEvent] (SAFETY SEMANTICS 6)
    degraded: bool = False             # True when any provider was blind/failed this cycle
    timings: dict = field(default_factory=dict)  # cycle phase wall-times (s): feeds/assess/alerts/dispatch + per-incident terrain/weather/spread (cadence telemetry, review item 6)


# ---------------------------------------------------------------------------
# Config (every tunable lives here; nothing hardcodes these)
# ---------------------------------------------------------------------------

@dataclass
class ResponseConfig:
    # --- alert ladder ---
    notice_h: float = 72.0             # first advance notice (3 days), on P50
    extreme_h: float = 24.0            # extreme alert, on P50
    extreme_p10_h: float = 12.0        # extreme also if pessimistic arrival within this
    clear_cycles: int = 2              # consecutive clear evals to downgrade
    clear_margin_h: float = 6.0        # headroom beyond threshold to count as clear
    # --- crew safety (user-specified operational rules) ---
    deploy_min: float = 30.0           # gel application time on site
    egress_buffer_min: float = 30.0    # crew clear of property before P10 arrival
    # CHORD corridor sample margin — CALIBRATED PAIR with circuity
    # (2026-08-02, scratch_review/circuity_calibration.py): 56 = 60 -
    # dt(L_rep), where dt(L) = L*(1.7-1.35)*fire_traffic_factor/
    # road_speed_mps = 3.9 min at the representative chord leg L_rep =
    # 8.5 km (pooled planner-leg median). Raising circuity 1.35->1.7 shifts
    # every chord pass/arrival time later; shaving the margin by the same
    # minutes keeps the END-OF-LEG total conservatism (t_model + margin) of
    # the old (1.35, 60) pair at L_rep (within 8 s). Shorter legs keep >=56
    # of the old 60 (their travel error was never the load-bearing part);
    # longer legs come out strictly MORE conservative (+2.8 min at the p90
    # 14.9 km leg) — exactly the canyon tail the old pair under-priced.
    # The pair moves together or not at all; see the circuity comment.
    route_margin_min: float = 56.0
    corridor_erosion_m: float = 500.0  # erosion radius on P10 grid for corridor checks
    # CORRIDOR HONESTY (founder-arithmetic trace 2026-07-29, commit c05d622;
    # founder "perform 1" + "2-3 safety routes chosen to be safe from fire,
    # not speed"): the legacy square erosion corner-reaches 1.53x the stated
    # radius and imported phantom arrivals onto roads the fire never touches;
    # dispatch evaluated exactly ONE fastest route per leg while a corridor-
    # clean alternative (1.3 min slower) existed. Both fixes default OFF =
    # byte-identical legacy.
    #
    # ⚠ MEASURED VERDICT (screen A/B, same day): erode_min is a MIN filter,
    # so the honest disc RELAXES the law — the square's unearned diagonal
    # slop was absorbing the P10-vs-truth quantile gap (member 0 beats P10 in
    # ~10% of cells by construction). Erosion-only: +12-18 belt homes, clean
    # at 4 crews, 2 crews caught in burn at 8 crews @ radius 500 — ship only
    # with a radius re-calibrated for 0 ground-truth violations. Route
    # candidates as built: REJECTED (-1..+4 homes, +3 caught crews; the
    # max-latest-depart egress rule optimizes when you must LEAVE, not how
    # exposed the DRIVE is). Keep route_alternatives=1 until the choice rule
    # judges in-transit exposure.
    corridor_erosion_euclidean: bool = False  # True: erode_min true-metric disc (calibrate radius first)
    route_alternatives: int = 1        # 1 = legacy fastest-only (KEEP: candidate rule rejected as built)
    # FUEL-TIERED EROSION (founder 2026-07-30: "in chaparral which will burn
    # slower 100m is likely sufficient" — the uniform radius does three jobs;
    # see spread.tiered_eroded_p10 + docs/transit_hazard_determination.md).
    # Each SOURCE cell's arrival projects by its own fuel class; per-tier
    # euclidean discs, min-combined. Default OFF = uniform legacy erosion,
    # byte-identical. Radii are the calibration sweep's to set; the caught-
    # crew event lived on TL ground, so the slow tier is validated against
    # exactly that scenario before any flip.
    corridor_erosion_tiered: bool = False
    corridor_erosion_fast_m: float = 550.0   # GR/GS + unclassified burnable (position error dominates)
    corridor_erosion_shrub_m: float = 200.0  # SH: flame-standoff-scale halo (~112 m needed at high wind)
    corridor_erosion_slow_m: float = 150.0   # TU/TL/SB: short flames, slow spread; snags are temporal
    corridor_sample_m: float = 300.0
    # CORRIDOR FAIL-CLOSED (tri-model review 2026-08-31, fix-program item 1):
    # the leg oracle SKIPPED samples off the spread raster and samples the
    # search never reached (UNREACHED) — "no bound" read as SAFE. Measured:
    # 4.0% false-clears over 720 realistic geometries, worst 7.85 h of lost
    # escape margin; egress legs 24-84% unsampled beyond the 2 km client-hull
    # domain; and domain truncation MANUFACTURES the UNREACHED value the law
    # trusted (2,024/2,024 cells behind a 2-cell non-burnable wall at the
    # domain edge). When corridor_fail_closed is ON an off-raster sample is
    # BOUNDED, not skipped: the fire can only leave the domain through a
    # boundary cell it actually reaches, so the earliest arrival at an
    # outside point p is >= min over reached boundary cells b of
    #     arrival(b) + dist(p, b) / corridor_offraster_ros_mps
    # (a plausible sustained MAXIMUM head ROS — the same 1.7 m/s, ~6 km/h
    # Tubbs-class run, the threat radius uses). The oracle evaluates that
    # bound over boundary RUNS (min arrival per run + distance to the run
    # segment): a rigorous LOWER bound on the true minimum, i.e. never later
    # than the truth. No reached boundary cell (fire contained in the domain
    # within the horizon) => the legacy skip. A leg with NO raster at all is
    # unsafe (no forecast = no verdict). corridor_unreached_bound applies the
    # same bound to IN-DOMAIN UNREACHED samples — the truncation case — for
    # the LIVE path, whose domain is a small window on a real world; the
    # simulator's tile IS its world (nothing arrives from outside it), so the
    # sim arms only corridor_fail_closed. Both default OFF = byte-identical
    # legacy; the watch daemon arms them via --fail-closed.
    corridor_fail_closed: bool = False
    corridor_unreached_bound: bool = False
    corridor_offraster_ros_mps: float = 1.7
    ember_buffer_min: float = 0.0      # extra safety on top of P10 (default-OFF; enable in wind events)
    # ESCAPE-TARGET HYSTERESIS (founder 2026-07-30 "fix escape target churn";
    # caught-crew dissection: a threatened crew was re-targeted to a fresh
    # escape ray nearly every tick for ~5.5 h — zigzag, not transit — before
    # one leg ended on the burn edge). When > 0: a fleeing crew keeps driving
    # to its adopted target unless the proposed target improves the ROUTED
    # destination margin by more than this many minutes (a clean-route
    # alternative to a burned-route current target always wins). 0 = off,
    # legacy re-pick-every-tick, byte-identical.
    escape_hysteresis_margin_min: float = 0.0
    # SEM-10e PROACTIVE PRE-POCKET ESCAPE (moderate-wind census attribution
    # 2026-08-02, paradise 8000/6000 m: the sweep's escape flag fires at
    # eroded-P10 within egress_buffer + one tick (~45 min), but the lawful
    # roads OUT of the Paradise SW pocket burned over 2-3 ticks BEFORE that
    # flag — the persisted arrival field knew both times from ignition, and
    # the crews spent the gap least-bad hopping until 4 of them sat idle in
    # the flaming front for 11 ticks). When > 0: an IDLE crew whose cell the
    # eroded-P10 front reaches within (egress_buffer + sweep lead + THIS many
    # minutes) decamps NOW — but ONLY along a route that is clean at arrival
    # to a zone that outlives the same horizon (never a least-bad hop, never
    # through a nominal-passed cell; when no such exit exists behavior is
    # unchanged and the imminent law stands). 0 = off, byte-identical.
    escape_proactive_lead_min: float = 0.0
    # SEM-10d AREA-ESCAPE ROAD SNAP (roads inspection 2026-09-25,
    # docs/review/roads_inspection_2026-09-25.md): the area-escape proposals
    # are points on 12 straight bearings (simui _escape_ray_targets), so a
    # crew fleeing along one drove its last stretch as a straight spur into
    # open ground — up to the spur cap (3.4 km on eagle_co, 11 km chords on
    # the extended tiles). When > 0 each proposal snaps to the road-graph
    # node nearest a safe ray sample within this many metres, walking back
    # from the ray end (the farthest safe point) toward the crew, keeping
    # the >= 2 km driveable minimum, and the flee becomes ROAD-BOUND: a
    # bearing with no road node in reach (open ground — the roadless Gore
    # Range NE of East Vail on extended vail_co) is proposed only when NO
    # bearing reaches a road. The proposals are then scored over the crew's
    # ACTUAL route exactly as before (burned routes excluded, audit exposure
    # minimized). 0 = off, byte-identical.
    escape_road_snap_m: float = 0.0
    # SEM-10e LAWFUL-ONLY (roads inspection 2026-09-25): when True the
    # PROACTIVE decamp takes only a fully lawful route (not burned, eroded-
    # P10 margin >= 0 at every pass time — nothing the 10c audit counts). The
    # exposed-transit fallback (not burned, no nominal crossing, lawful
    # destination) is skipped: since the 2026-09-03 audit change (A8) that
    # transit is counted as imminent crew-overrun rows, and a VOLUNTARY move
    # must not buy them. No lawful route -> no proactive move (the imminent
    # law still owns the emergency). False = legacy, byte-identical. The
    # simulator arms it for EXTENDED fire-zone sessions, whose extended road
    # graph opens exposed-transit routes the standard graph never offered
    # (the pinned Camp-Fire-scale run: one tick-23 imminent row -> 0).
    escape_proactive_lawful_only: bool = False
    # TRANSIT-HAZARD STAGE 1 (founder "perform stage 1" 2026-07-30;
    # docs/transit_hazard_determination.md): per-fuel-class cold-black
    # windows replace the flat 45-min carve-out in SEM-10 ROUTE SELECTION
    # (fuels.cold_black_minutes: grass 5-10 min, grass-shrub 10-20, shrub
    # 20-30, timber-understory 45, timber-litter 60, slash 90 — grass opens,
    # timber stricter). The 10c audit is never consulted here and never
    # relaxed. False = flat 45 min, byte-identical.
    transit_cold_black_by_class: bool = False
    # --- gel product parameters (UNVERIFIED — pending supplier data) ---
    gel_window_h: float = 72.0         # operational window after application (outer weathering/wash-off horizon still UNVERIFIED)
    gel_full_strength_h: float = 72.0  # == gel_window_h: GraphGel is Class A at 1/8" DRY — flat effectiveness, no decay ramp (product data 2026-07-11)
    gel_effectiveness: float = 0.85    # P(save) multiplier (PLACEHOLDER — Class A rating is not a survival probability)
    gel_env_model_enabled: bool = False  # wgdefense-style temp/RH/wind-scaled coating life
    # --- travel ---
    # MEASURED 2026-08-02 (scratch_review/circuity_calibration.py: 300
    # routed pairs x 6 tiles x 2 populations, roads.route dist_m /
    # haversine, spur cap 2000 m, PYTHONHASHSEED=0). Pooled MEDIANS: 1.63
    # (planner-representative WUI+safe-zone legs) / 1.76 (uniform on-tile
    # pairs) -> 1.7. The calibration review's "1.96-2.43" were MEANS
    # (pooled mean measured 1.95-2.14, Paradise 2.5-2.7) — the p90 3.0-3.3
    # tail stays route_margin_min's job, not the multiplier's. Per-map
    # medians span 1.42 (boulder_co) to 2.48 (paradise_ca uniform): one
    # global constant over-conserves flat tiles and still under-prices
    # canyon tiles — per-area circuity (DISPATCH_REVIEW item 1b) remains
    # the follow-up; per-map values live in circuity_calibration.json.
    # Effective chord corridor speed is now road_speed/(circuity*fire_
    # traffic) = 7.52 m/s (was 9.47). CALIBRATED PAIR with route_margin_min
    # (60 -> 56 in the same commit): the old 1.35 was optimistic and the
    # chord margin's extra width silently absorbed it; do not retune either
    # knob alone. Plan-on-roads legs use real geometry — unaffected.
    circuity: float = 1.7
    fire_traffic_factor: float = 1.4
    road_speed_mps: float = 17.9       # ~40 mph blended class speed
    # --- spread / forecast ---
    horizon_h: float = 96.0
    ensemble_n: int = 6                # perturbed members (+1 nominal)
    wind_perturb_frac: float = 0.25    # +/- scale at max lead; grows with lead time
    moisture_perturb_frac: float = 0.20
    spotting_enabled: bool = False     # Sardoy lognormal, default OFF
    grid_cell_m: float = 90.0
    grid_max_cells: int = 300_000      # coarsen above this
    live_herb_frac: float = 0.30       # cured fire-season default
    live_woody_frac: float = 0.60
    # EDGE-WEATHER PIECEWISE INTEGRATION (tri-model review, code finding
    # "frozen departure-bucket edge weather", late-biased: a 46-min constant
    # offset in a step case; timber flank edges spanning 25 h on one weather
    # hour). Legacy: an edge's traversal time is computed from the weather
    # of the hour the fire LEAVES the source cell, however long the traversal
    # runs. When ON, a traversal that crosses an hour boundary continues with
    # each subsequent hour's rate for the distance still unburned (piecewise
    # constant integration), so a rising wind arrives earlier and a dying
    # wind later — the honest direction both ways. Default OFF =
    # byte-identical; the hindcast A/B decides the live flip.
    edge_weather_piecewise: bool = False
    # PHI-VECTOR SLOPE/WIND COMBINATION (same review: the max-spread vector
    # combines midflame wind and the slope-equivalent wind in SPEED space,
    # whereas Rothermel/FARSITE combine the phi FACTORS as vectors; phi_w =
    # C*U^B is nonlinear, so the two agree only when one term is zero —
    # measured -11 to -33% too slow on OPPOSED slopes, the downslope Santa
    # Ana geometry, zero on flat ground). When ON the effective wind and the
    # max-spread bearing come from the phi-vector sum (rothermel.
    # effective_wind_from_bed's wind_slope_angle seam). Default OFF =
    # byte-identical; hindcast A/B decides the live flip.
    slope_phi_vector: bool = False
    # --- dispatch ---
    replan_period_s: float = 900.0
    commitment_horizon_min: float = 20.0
    churn_penalty_frac: float = 0.05
    adoption_threshold_frac: float = 0.03
    solver_budget_s: float = 5.0
    # DETERMINISTIC SOLVER BUDGET (tri-model review held item "solver
    # determinism"; measured +-1 belt home between identical runs because
    # solver_budget_s is a WALL CLOCK — a loaded machine cuts the ILS at a
    # different iteration). When > 0 the budget is a COUNT of budget checks
    # (each insertion scan / local-search pass / exact-DFS node consults the
    # budget once), so identical inputs always stop at the identical point
    # regardless of machine load; solver_budget_s then only bounds a runaway
    # (it still applies as a hard ceiling). 0 = legacy wall clock,
    # byte-identical. The simulator arms it; a value that matches ~5 s of
    # solver work on the dev box is derived in docs/FIX_PROGRAM_2026-09-11.md.
    solver_budget_evals: int = 0
    ils_iterations: int = 30
    # --- crew tracking ---
    beacon_stale_s: float = 600.0
    # --- feeds ---
    poll_period_s: float = 300.0
    user_agent: str = "wg-response/0.1 (ops@thewildfireguardian.com)"
    # --- Sprint 2 (Council-1 remediations) ---
    slack_min: float = 15.0            # planned-slack floor per stop (SAFETY SEMANTICS 7)
    blind_alarm_cycles: int = 1        # unassessed cycles before OperatorEvent('degraded')
    registry_ttl_s: float = 300.0      # client-registry cache TTL (kills per-incident re-scans)
    dir_perturb_deg: float = 30.0      # ensemble wind-direction perturbation at max lead (model F2)
    # ENSEMBLE MODEL-ERROR TERM (tri-model review 2026-08-31, finding A7):
    # the wind/moisture/direction perturbations are INPUT-uncertainty terms
    # (and the speed/moisture ones are damped to the horizon mean, i.e. the
    # realized wind spread is +/-wind_perturb_frac*0.5 at any member count),
    # so the band was 8x narrower than the measured head-ratio error
    # (sd(log) 0.71) and p10 was in effect the sample minimum, not a
    # percentile. When > 0, every perturbed member k also carries a
    # multiplicative head-ROS scale exp(sd * z_k), z_k the stratified
    # standard-normal quantile of a fourth Latin-hypercube dimension
    # (nominal member untouched; seeding law unchanged — the first three
    # dimensions' draws are byte-identical). Value = the hindcast PIT
    # calibration's (docs/calibration/); 0.0 = off, byte-identical.
    model_error_sd_log: float = 0.0
    # LADDER MEDIAN BOUND (same review, finding N4): with direction
    # perturbation the median member arrives at a head-axis client via its
    # flank, so the p50 the alert ladder evaluates sat ~4.2x the engine's own
    # nominal arrival. When ON, p50 = min(Hazen p50, nominal) per cell — the
    # median may never trail the best-estimate run (the exact rule p10
    # already obeys); p10 <= p50 <= p90 is preserved. Default OFF =
    # byte-identical ladder; the watch daemon arms it via --p50-bound.
    ensemble_p50_nominal_bound: bool = False
    dispatch_objective: str = "homes"  # 'homes' = max count (founder triage law) | 'value' = legacy $-weighted
    dispatch_urgency_weight: float = 0.30   # INERT in the shipped planner (urgency retired from the objective — SEMANTICS 7; kept for the _urgency_reorder utility)
    dispatch_value_weight: float = 0.05     # homes-mode value tiebreak (< urgency; count stays primary)
    # COAT-EARLY (2026-07-11 founder dispatch loop): emit-time gel-freshness
    # late-shift polish. Default True = shipped behavior (coat as late as safe for
    # freshest gel). False = coat as early as feasible, freeing crews sooner for
    # more homes — a sweep-proven robust +~1% homes-protected across 8 crew/client
    # ratios x 5 maps x winds x intensities, 0 safety violations, gel freshness at
    # arrival neutral (late-shift rarely binds under the p10 safety cap). Two other
    # levers (at-risk-first EMIT ordering; an urgency OBJECTIVE tiebreak) were
    # measured and REJECTED — both overfit one crew/client ratio and lost homes
    # out-of-sample (the round-4 urgency-in-the-objective lesson, re-confirmed).
    dispatch_late_shift: bool = True
    # PLAN-ON-ROADS (2026-07-12 founder build order, after the attribution
    # experiment proved ~73% of relocations + the residual overrun tail = the
    # CHORD-vs-ROAD mismatch): when True AND a road graph is available, the
    # dispatch planner's travel times AND corridor T* checks follow the ACTUAL
    # road polyline the crew will drive (roads.route), not the straight chord.
    # Default False = tier-0 chords, byte-identical to the pre-roads planner;
    # the simulator server turns it on per session (like spotting); the live
    # engine flips only after live road graphs + validation.
    dispatch_plan_on_roads: bool = False
    # Corridor margin for ROAD-sampled legs (plan-on-roads only). The chord
    # margin (route_margin_min — 60 before the 2026-08-02 pair calibration)
    # was calibrated to ALSO absorb the flat travel model's -21..-33%
    # mountain-road optimism (Council-1 F2); with the corridor sampled along
    # the ACTUAL road at the route's own speed that compensator double-
    # counts, so road legs use this leaner margin — equal to the egress
    # buffer — covering residual travel variability (traffic, closures)
    # only. Sweep-measured at 15-min crews: 874 vs 775 homes with margin 60,
    # per-tick violations 0 at both. Chord legs keep route_margin_min (now
    # 56, paired with measured circuity 1.7 — the optimism it absorbed is
    # priced in the multiplier instead).
    route_margin_roads_min: float = 30.0
    # FOUNDER ROUND 5 — COATING HOLD (2026-07-12): "if fire is more than 24
    # hours away, do not coat homes; place crews on alert and preposition as
    # required — AS LONG AS all homes can be coated based on the current fire
    # spread model and a buffer for changes in forecasted weather." The
    # coating is OPAQUE and owners accept it only under live threat, so
    # speculative early coats are waste — but deferral may NEVER cost a home:
    # each cycle a feasibility PROBE re-plans every job with held jobs pushed
    # to their release time (p50 − hold_h) AND their safety deadlines
    # tightened by the weather buffer; the hold stays only while the deferred
    # plan still serves EVERY home the coat-now plan would serve. The moment
    # the gate fails, the hold releases (latched — no stop-start churn) and
    # coating begins immediately. Held homes report unserved reason 'held'
    # (outcome 'pending'); idle crews preposition to the safe zone nearest
    # their deferred first stop (lawful routes only; SEM-10 still governs).
    # dispatch_hold_h = 0 disables (byte-identical legacy). Sim server ships
    # 24 h; the release boundary equals the EXTREME_24H alert rung.
    dispatch_hold_h: float = 0.0
    dispatch_hold_weather_buffer_h: float = 6.0
    # FOUNDER ROUND 6 (2026-07-12): within-route stop SEQUENCE. 'auto' (default,
    # byte-identical) = the count-max emit order. 'fire_first' = each crew's
    # non-frozen stops re-ordered CLOSEST-TO-FIRE-FIRST (earliest p50 arrival)
    # in 30-min deadline bands with nearest-neighbor travel chaining inside a
    # band (the founder's 'work backward from the fire, but minimize travel');
    # adopted ONLY when the whole re-ordered route stays feasible under the
    # unchanged safety chain, so it can never drop a served home per plan call.
    # 'urgency' = the raw deadline sort (no travel banding, _urgency_reorder).
    # Prior pure-urgency attempts lost homes DYNAMICALLY (−113/−4/−24 across
    # three sweeps, pre-roads/pre-hold); this knob exists to re-measure under
    # the current stack — the sweep verdict picks the shipped sim default.
    dispatch_sequence: str = "auto"
    # CREW SHIFTS (FOUNDER ROUND 6b, 2026-07-12): multi-day events (the 24h
    # coating hold + WATCH windows run 24-72h) need rest cycles. A crew may
    # work at most crew_shift_max_on_h continuous hours from its mobilization
    # (0 = shifts OFF, byte-identical); it then rests crew_shift_rest_h at its
    # current safe position (status OFF_DUTY — never dispatchable, still
    # SEM-10-protected and relocated if threatened) and returns AVAILABLE.
    # Rest NEVER interrupts a job in progress (the crew finishes, then rests).
    # First-cycle stagger: odd-indexed crews get a HALF first shift (A/B
    # rotation) so a simultaneous mobilization cannot put the whole fleet to
    # bed at the same hour.
    crew_shift_max_on_h: float = 0.0
    crew_shift_rest_h: float = 8.0
    # WIND GUST BLEND (2026-07-13, founder "calibrate the weather input to
    # increase accuracy"): effective 10-m wind = sustained + blend x
    # (gust - sustained). The hindcast localized part of the engine's late
    # bias to SMOOTH hourly wind inputs (max sustained 5.7 m/s across every
    # cached series while real fire runs ride gusts); gusts are already
    # parsed by every weather adapter (NWS windGust, Open-Meteo
    # wind_gusts_10m) and were previously DROPPED at the spread seam. 0.0 =
    # sustained only, byte-identical legacy. Calibrated against the 14-window
    # hindcast before any live use; the watch daemon sets it via
    # --gust-blend.
    wind_gust_blend: float = 0.0
    ember_threat_radius_m: float = 0.0      # eta_at adjacency fallback for developed-cell homes (0 = off/byte-identical; sim sets ~200)
    crew_reinforce_1h_min: float = 60.0     # out-of-region '1h away' crews arrive this many min after first ignition (SEMANTICS 9)
    crew_reinforce_3h_min: float = 180.0    # out-of-region '3h away' crews arrive this many min after first ignition (SEMANTICS 9)
    grass_ros_floor_frac: float = 0.0       # fuel-class head-ROS floor = frac*U10 (Cruz-Alexander; 0=off/byte-identical; sim 0.10, LIVE watcher armed 0.10 via --grass-floor since 2026-07-28)
    # ROS-FLOOR PHYSICS (tri-model review 2026-08-31, finding A3): the floor
    # as deployed overrides the MOISTURE OF EXTINCTION — fuel Rothermel says
    # is OUT (dead moisture at/above mx, ros 0) is walked at frac*U10 with
    # zero intensity and zero flame (39.4% of the cached August fire-hours
    # sat in that regime). When ON, the floor only ever RAISES a positive
    # Rothermel ROS (fire that can spread, spreads faster; fire that cannot
    # spread stays out) and the cell's Byram intensity scales with the raised
    # ROS (I = H*w*R is linear in R) so the spotting seam sees the same fire
    # the front does. Default OFF = the as-deployed floor, byte-identical;
    # the calibration A/B (docs/calibration/) decides the live flip.
    ros_floor_extinction_gate: bool = False
    # ROS-FLOOR CLASS SET (same review): the "grass" floor also lifts TU/TL
    # timber to 19-45x Rothermel. True restricts the floor to GR/GS (the
    # Cruz-Alexander evidence base); the TU/TL ratios become 0. Default OFF =
    # all four legacy classes, byte-identical.
    ros_floor_grass_only: bool = False
    # REGIME-CONDITIONAL FLOOR (calibration matrix 2026-09-03, the verdict's
    # own recommendation): the honest, tight band (half floor 0.05, gated,
    # GR/GS only, model-error 0.71 — PIT extreme mass 40%, near-symmetric)
    # was held back ONLY by the July blow-ups, where every variant has skill
    # ~1.0 and coverage is area budget. When > 0 and the incident's domain
    # trips the EXTREME SPREAD REGIME detector (threat._extreme_regime_now:
    # cured-grass-led fuelscape + wind), that incident spreads with THIS floor
    # fraction on EVERY floored class (grass-only lifted) — the blow-up
    # posture — while non-regime incidents keep the calibrated point. The
    # detector already gates the ladder's P10 evaluation; this lets it carry
    # the spread physics too. 0.0 = off, byte-identical.
    extreme_regime_floor_frac: float = 0.0
    # EXTREME SPREAD REGIME (docs/calibration/hindcast_oos_2026-07-28.md):
    # blow-up windows (Porcupine Ridge 22.5 km/12.9 h = 13x under-forecast)
    # are NOT separated by red-flag weather in reanalysis (they occurred at
    # modest wind/RH while well-forecast windows had stronger wind, drier
    # air) — the measured separator is a cured-grass FUELSCAPE (GR+GS >= ~2/3
    # of burnable domain: Porcupine 88%, Madre grass run 69%; every
    # non-grass-led window scored ~0% late under the grass floor) with any
    # meaningful wind. When an incident's domain matches, the alert ladder
    # evaluates that incident's stage thresholds on the PESSIMISTIC (p10)
    # arrival instead of the median, and every message carries an EXTREME
    # SPREAD REGIME banner. Crew-safety math already runs on p10 and is
    # untouched. Detector wind uses the fixed hindcast blend 0.5 (thresholds
    # were measured in that unit) independent of cfg.wind_gust_blend, so a
    # conservative spread model cannot weaken regime detection. Default OFF =
    # byte-identical ladder; the watch daemon arms it via --extreme-regime.
    extreme_regime_enabled: bool = False
    extreme_regime_grass_frac: float = 0.60  # GR+GS fraction of burnable domain cells
    extreme_regime_wind_mps: float = 4.0     # max effective wind over the lookahead
    extreme_regime_lookahead_h: float = 24.0
    history_ticks: int = 600           # simulator state-snapshot ring size (time-scrub replay)
    road_spur_max_m: float = 2000.0    # max off-network spur; beyond -> loud straight-leg fallback
    incident_stale_h: float = 168.0    # exclude incidents staler than this from alerting (SAFETY SEMANTICS 9)
    containment_gate_enabled: bool = False   # WATCH ceiling for contained fires (SAFETY SEMANTICS 8, default OFF)
    containment_watch_ceiling_pct: float = 90.0
    containment_no_growth_h: float = 48.0


# ---------------------------------------------------------------------------
# Deterministic RNG (wgdefense convention)
# ---------------------------------------------------------------------------

def seeded_rng(*seed_parts: object) -> random.Random:
    """Deterministic RNG from explicit seed strings. Never seed from wall-clock."""
    h = hashlib.sha256("|".join(str(p) for p in seed_parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


# ---------------------------------------------------------------------------
# Conformance helpers (used by tests)
# ---------------------------------------------------------------------------

def assert_valid_transition(prev: AlertState, new: AlertState) -> None:
    if new != prev and new not in ALERT_TRANSITIONS[prev]:
        raise ValueError(f"illegal alert transition {prev.value} -> {new.value}")


def validate_config(cfg: ResponseConfig) -> "list[str]":
    """Return a list of human-readable config problems (empty = valid).

    Tri-model review 2026-08-31 (docs/TRI_MODEL_REVIEW_2026-08-31.md, code
    finding 7 / scratch_review/rv2code_r7_quick.py): the prior 5-check version
    accepted 22/22 deliberately unsound configs (e.g. a NEGATIVE
    corridor_erosion_m silently DISABLES the corridor shield instead of
    eroding it). This function now bounds every knob it can from its own
    documented meaning: sign/range on every duration, radius, speed, count
    and fraction; [0,1]/[0,100]/[0,180]/[0,3] caps where the field is a
    fraction, percent, angle, or the calibrated model-error term; ordering
    constraints between paired knobs; enum-membership for the two dispatch
    strings; and bool/int type discipline via dataclasses introspection.
    Every check here passes on the class default AND must keep doing so —
    this validator must never reject ResponseConfig()."""
    import dataclasses as _dc
    problems = []

    # --- alert ladder ---
    if cfg.notice_h <= 0:
        problems.append("notice_h must be > 0")
    if cfg.extreme_h <= 0:
        problems.append("extreme_h must be > 0")
    if cfg.extreme_h >= cfg.notice_h:
        problems.append("extreme_h must be < notice_h")
    if cfg.extreme_p10_h <= 0:
        problems.append("extreme_p10_h must be > 0")
    # SAFETY SEMANTICS 1 is an OR of two independent rungs (p50 <= extreme_h,
    # p10 <= extreme_p10_h); no ordering between them is required — p10 <=
    # p50 always, so a p10 rung wider than extreme_h is still meaningful. A
    # rung beyond the forecast horizon, however, can never fire (arrivals
    # past the horizon are UNREACHED), which is what catches the review's
    # extreme_p10_h=999 case.
    if cfg.horizon_h > 0 and cfg.extreme_p10_h > cfg.horizon_h:
        problems.append("extreme_p10_h must be <= horizon_h (a P10 rung past "
                        "the forecast horizon can never fire)")
    if cfg.clear_cycles < 1:
        problems.append("clear_cycles must be >= 1 (0 defeats the clear-streak hysteresis)")
    if cfg.clear_margin_h < 0:
        problems.append("clear_margin_h must be >= 0")

    # --- crew safety / route margins (minutes). The cap is a MAGNITUDE guard
    # against unit errors (the review's egress_buffer_min=1e9), not a
    # physical bound: simulator drills and the withdrawal-law tests use
    # day-scale buffers on purpose ("brutal tightening" rug-pulls), so the
    # guard sits at one week. ---
    MAX_MARGIN_MIN = 10_080.0
    if cfg.deploy_min <= 0 or cfg.deploy_min > MAX_MARGIN_MIN:
        problems.append(f"deploy_min must be > 0 and <= {MAX_MARGIN_MIN:g} minutes")
    if cfg.egress_buffer_min < 0 or cfg.egress_buffer_min > MAX_MARGIN_MIN:
        problems.append(f"egress_buffer_min must be within [0, {MAX_MARGIN_MIN:g}] minutes")
    if cfg.route_margin_min < 0 or cfg.route_margin_min > MAX_MARGIN_MIN:
        problems.append(f"route_margin_min must be within [0, {MAX_MARGIN_MIN:g}] minutes")
    if cfg.route_margin_roads_min < 0 or cfg.route_margin_roads_min > MAX_MARGIN_MIN:
        problems.append(f"route_margin_roads_min must be within [0, {MAX_MARGIN_MIN:g}] minutes")
    if cfg.slack_min < 0 or cfg.slack_min > MAX_MARGIN_MIN:
        problems.append(f"slack_min must be within [0, {MAX_MARGIN_MIN:g}] minutes")
    if cfg.ember_buffer_min < 0 or cfg.ember_buffer_min > MAX_MARGIN_MIN:
        problems.append(f"ember_buffer_min must be within [0, {MAX_MARGIN_MIN:g}] minutes")
    if cfg.escape_hysteresis_margin_min < 0:
        problems.append("escape_hysteresis_margin_min must be >= 0")
    if cfg.escape_proactive_lead_min < 0:
        problems.append("escape_proactive_lead_min must be >= 0")
    if cfg.escape_road_snap_m < 0:
        problems.append("escape_road_snap_m must be >= 0")

    # --- corridor erosion / sampling ---
    if cfg.corridor_erosion_m < 0:
        problems.append("corridor_erosion_m must be >= 0 (negative DILATES the "
                        "corridor instead of eroding it)")
    if cfg.corridor_erosion_fast_m <= 0:
        problems.append("corridor_erosion_fast_m must be > 0")
    if cfg.corridor_erosion_shrub_m <= 0:
        problems.append("corridor_erosion_shrub_m must be > 0")
    if cfg.corridor_erosion_slow_m <= 0:
        problems.append("corridor_erosion_slow_m must be > 0")
    MAX_CORRIDOR_SAMPLE_M = 5000.0
    if cfg.corridor_sample_m <= 0 or cfg.corridor_sample_m > MAX_CORRIDOR_SAMPLE_M:
        problems.append(f"corridor_sample_m must be > 0 and <= {MAX_CORRIDOR_SAMPLE_M:g} m")
    if cfg.corridor_offraster_ros_mps <= 0:
        problems.append("corridor_offraster_ros_mps must be > 0")
    if cfg.route_alternatives < 1:
        problems.append("route_alternatives must be >= 1")

    # --- gel product parameters ---
    if cfg.gel_window_h <= 0:
        problems.append("gel_window_h must be > 0")
    if cfg.gel_full_strength_h <= 0:
        problems.append("gel_full_strength_h must be > 0")
    if cfg.gel_window_h > 0 and cfg.gel_full_strength_h > cfg.gel_window_h:
        problems.append("gel_full_strength_h must be <= gel_window_h")
    if not (0.0 <= cfg.gel_effectiveness <= 1.0):
        problems.append("gel_effectiveness must be within [0, 1]")

    # --- travel ---
    if cfg.circuity < 1.0:
        problems.append("circuity must be >= 1 (a routed path cannot be shorter than the chord)")
    if cfg.fire_traffic_factor < 1.0:
        problems.append("fire_traffic_factor must be >= 1")
    if cfg.road_speed_mps <= 0:
        problems.append("road_speed_mps must be > 0")

    # --- spread / forecast ---
    if cfg.horizon_h <= 0:
        problems.append("horizon_h must be > 0")
    if cfg.ensemble_n < 1:
        problems.append("ensemble_n must be >= 1")
    if not (0.0 <= cfg.wind_perturb_frac <= 1.0):
        problems.append("wind_perturb_frac must be within [0, 1]")
    if not (0.0 <= cfg.moisture_perturb_frac <= 1.0):
        problems.append("moisture_perturb_frac must be within [0, 1]")
    if cfg.grid_cell_m < 30.0:
        problems.append("grid_cell_m below LANDFIRE native 30 m is fabricated precision")
    if cfg.grid_max_cells < 1000:
        problems.append("grid_max_cells must be >= 1000")
    if not (0.0 <= cfg.live_herb_frac <= 1.0):
        problems.append("live_herb_frac must be within [0, 1]")
    if not (0.0 <= cfg.live_woody_frac <= 1.0):
        problems.append("live_woody_frac must be within [0, 1]")
    if not (0.0 <= cfg.dir_perturb_deg <= 180.0):
        problems.append("dir_perturb_deg must be within [0, 180] degrees")
    if not (0.0 <= cfg.model_error_sd_log <= 3.0):
        problems.append("model_error_sd_log must be within [0, 3]")

    # --- dispatch ---
    if cfg.replan_period_s <= 0:
        problems.append("replan_period_s must be > 0")
    if cfg.commitment_horizon_min < 0:
        problems.append("commitment_horizon_min must be >= 0")
    if not (0.0 <= cfg.churn_penalty_frac <= 1.0):
        problems.append("churn_penalty_frac must be within [0, 1]")
    if not (0.0 <= cfg.adoption_threshold_frac <= 1.0):
        problems.append("adoption_threshold_frac must be within [0, 1]")
    if cfg.solver_budget_s < 0:
        problems.append("solver_budget_s must be >= 0")
    if cfg.ils_iterations < 0:
        problems.append("ils_iterations must be >= 0")
    if cfg.dispatch_objective not in ("homes", "value"):
        problems.append("dispatch_objective must be 'homes' or 'value'")
    if cfg.dispatch_sequence not in ("auto", "fire_first", "urgency"):
        problems.append("dispatch_sequence must be 'auto', 'fire_first', or 'urgency'")
    if cfg.dispatch_urgency_weight < 0:
        problems.append("dispatch_urgency_weight must be >= 0")
    if cfg.dispatch_value_weight < 0:
        problems.append("dispatch_value_weight must be >= 0")
    if cfg.dispatch_hold_h < 0:
        problems.append("dispatch_hold_h must be >= 0")
    if cfg.dispatch_hold_weather_buffer_h < 0:
        problems.append("dispatch_hold_weather_buffer_h must be >= 0")

    # --- crew tracking / shifts ---
    if cfg.beacon_stale_s <= 0:
        problems.append("beacon_stale_s must be > 0")
    if cfg.crew_shift_max_on_h < 0:
        problems.append("crew_shift_max_on_h must be >= 0")
    if cfg.crew_shift_rest_h < 0:
        problems.append("crew_shift_rest_h must be >= 0")
    if cfg.crew_reinforce_1h_min < 0:
        problems.append("crew_reinforce_1h_min must be >= 0")
    if cfg.crew_reinforce_3h_min < 0:
        problems.append("crew_reinforce_3h_min must be >= 0")
    if (cfg.crew_reinforce_1h_min >= 0 and cfg.crew_reinforce_3h_min >= 0
            and cfg.crew_reinforce_1h_min > cfg.crew_reinforce_3h_min):
        problems.append("crew_reinforce_1h_min must be <= crew_reinforce_3h_min")

    # --- feeds / ops ---
    if cfg.poll_period_s <= 0:
        problems.append("poll_period_s must be > 0")
    if cfg.registry_ttl_s < 0:
        problems.append("registry_ttl_s must be >= 0")
    if cfg.blind_alarm_cycles < 0:
        problems.append("blind_alarm_cycles must be >= 0")
    if cfg.incident_stale_h <= 0:
        problems.append("incident_stale_h must be > 0")
    if cfg.history_ticks < 0:
        problems.append("history_ticks must be >= 0")
    if cfg.road_spur_max_m < 0:
        problems.append("road_spur_max_m must be >= 0")

    # --- ensemble / ladder bias knobs ---
    if not (0.0 <= cfg.wind_gust_blend <= 1.0):
        problems.append("wind_gust_blend must be within [0, 1]")
    if cfg.ember_threat_radius_m < 0:
        problems.append("ember_threat_radius_m must be >= 0")
    if not (0.0 <= cfg.grass_ros_floor_frac <= 1.0):
        problems.append("grass_ros_floor_frac must be within [0, 1]")

    # --- extreme spread regime ---
    if not (0.0 <= cfg.extreme_regime_grass_frac <= 1.0):
        problems.append("extreme_regime_grass_frac must be within [0, 1]")
    if cfg.extreme_regime_wind_mps < 0:
        problems.append("extreme_regime_wind_mps must be >= 0")
    if cfg.extreme_regime_lookahead_h <= 0:
        problems.append("extreme_regime_lookahead_h must be > 0")

    # --- containment gate ---
    if not (0.0 <= cfg.containment_watch_ceiling_pct <= 100.0):
        problems.append("containment_watch_ceiling_pct must be within [0, 100]")
    if cfg.containment_no_growth_h < 0:
        problems.append("containment_no_growth_h must be >= 0")

    # --- type discipline: bool-typed fields must hold bool, int-typed fields
    # must hold int (and not bool -- bool is an int subclass in Python) ---
    for fld in _dc.fields(cfg):
        v = getattr(cfg, fld.name)
        if fld.type == "bool":
            if not isinstance(v, bool):
                problems.append(f"{fld.name} must be a bool, got {type(v).__name__}")
        elif fld.type == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                problems.append(f"{fld.name} must be an int, got {type(v).__name__}")

    return problems
