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
   OLD BLACK IS A REFUGE (founder ruling 2026-09-26, verbatim: "old burned
   ground does not count as a violation"; cfg.old_black_refuge, default OFF,
   armed by the SIMULATOR — the live engine audits no crew position). A cell
   whose NOMINAL arrival is at least its fuel's cold-black window in the past
   (the SEM-10 transit window: fuels.cold_black_minutes per class under
   transit_cold_black_by_class, else the flat 45 min) is OLD BLACK — burned
   out, it cannot burn again. With the knob on, (c) counts no row for a crew
   standing on it, neither burned nor imminent (the eroded-P10 band that
   reaches an old-black cell from nearby fuel does not count); (a) never
   moves a crew off it; (b) and the SEM-10d/e escapes treat a zone or escape
   destination on it as a lawful refuge with an unbounded horizon; every
   route / exposure metric charges its samples nothing (judged at each
   sample's pass time). Burned ground still INSIDE its window (flaming, hot)
   is unchanged: counted, fled, impassable. The audit looks once per step,
   so it excuses black only if it was already old when the step began (or
   when the crew reached it, if later): a front that overran a crew between
   two audits is still reported once, even when its fuel burned out before
   the audit looked. The SEM-10e exposed-transit ZONE fallback keeps its
   strict nominal-crossing reject (relaxing it measured 3 extra imminent
   rows: the voluntary route over old black also crossed unburned band).
   The rule is PER CELL — doctrine's
   "good black" must also be large enough around the crew (safety-zone sizing
   ~4x flame length); the model does not size it (measured in
   docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map
   are out (2026-09-26)").
   A CREW THAT LEAVES THE MAP IS OUT (founder ruling 2026-09-26, verbatim:
   "crews once leave a map are no longer part of the simulation";
   cfg.left_map_out, default OFF, armed by the SIMULATOR). The map is the
   simulated terrain tile (its grid, or the rectangle the page draws for it)
   plus the surroundings of every catalog safe zone (within
   simui.ZONE_ON_MAP_M — five standard tiles have a zone just past their
   edge; crews stage and refuge there). A crew LEAVES when it is off the
   map with nothing that brings it back: stopped there, or on a final leg that
   ends off the map (in practice a road-network escape to a node beyond the
   tile edge). Transit through off-tile road toward an on-map destination is
   not leaving; an INBOUND reinforcement has not entered yet. From then on the
   crew is permanently out: never in dispatch.plan's crew set, no movement, no
   sweep, no audit row; its in-flight commitments are released (re-planned
   next tick, loud note). Reported: state crews[i].left_map / left_map_at_s,
   scoreboard.crews_left_map and a note. With cfg.escape_prefer_on_map the
   SEM-10d destination rule ranks an on-map refuge before an off-map one when
   the two are equally safe (same lawful / exposure / capped-horizon steps),
   and the VOLUNTARY SEM-10e road decamp prefers any on-map durable refuge
   (an off-tile node reads an unbounded horizon only because no fire field
   exists there).
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
12. SEEDED INCIDENTS ARE REAL TO THE ENGINE, NEVER TO A CLIENT (W1, 2026-09-26;
   docs/LIVE_WORLD_PLAN.md "W1 seeded incidents"). A user lights a fire at a
   point (SeededIgnition; the watch daemon's POST /api/seed) and the live
   watcher runs it through the SAME code as a feed incident:
   (a) IDENTITY: ids live in the SEEDED_ID_PREFIX namespace ('seeded:<uuid>',
       IncidentSource.SEEDED). Seeded incidents join the cycle AFTER feed
       fusion, and fuse_incidents never joins one with anything (IRWIN, name
       or distance): a drill can never rename, absorb or be absorbed by a
       real fire.
   (b) GEOMETRY: a point incident — no perimeter, acreage 0, containment
       unknown — whose observed geometry STAYS the ignition point. The watcher
       has no ground truth for live fires (the simulator's nominal-member
       "actual fire" does not exist here), so every cycle spreads it from the
       point at now_s exactly like a feed point-incident without a perimeter;
       its forecast is the ensemble. t_ignite_s records when it was lit (the
       (e) clock and discovered_at_s), never a spread t0.
   (c) FRESHNESS: materialized with updated_at_s = now_s every cycle (the seed
       store vouches for it), so the SEMANTICS 9 staleness gate keeps it until
       it is retired — there is no zombie gate on a fire the operator says
       burns.
   (d) SAME LAW AS LIVE FIRES: unioned BEFORE the client-range filter — a
       seeded fire with no protected client in range is shown (incidents,
       /api/state) but builds no domain (the cycle note says so); in range it
       gets a domain, ensemble, ETAs, the ladder, dispatch jobs, and its P10
       enters every corridor check like any fire's.
   (e) RETIREMENT = ABSENCE: DELETE /api/seed/<id> (operator), or the seed's
       horizon passing (t_ignite_s + cfg.horizon_h) with no pair of it in
       NOTICE/WATCH/EXTREME and its assessment healthy (the watcher), removes
       it from the incident set; its pairs then follow SEMANTICS 6 exactly as
       a vanished feed incident's: every feed healthy -> clear streak ->
       CLEARED after clear_cycles; any feed blind -> FROZEN. IMPACTED stays
       terminal.
   (f) WEATHER: default = the LIVE provider at the fire (NWS gridpoint, as for
       every incident). Optional per-incident override through the weather
       seam, for THAT incident only (wgresponse.seeded.seeded_forecast): the
       conditions from an intensity preset (simui.INTENSITY_WEATHER) or an
       explicit SeedWeather, the direction from wind_dir_deg; each part left
       unset stays live, both set = the simulator's MockWeather script (no
       network). The mode is recorded in the cycle notes and /api/state.
   (g) NEVER A CLIENT MESSAGE: the watch daemon routes every notification of a
       seeded incident to the log (seeded.SeedSafeNotifier; the send reports
       success, so it is ledgered and restart re-fires nothing) — a drill fire
       never reaches a real client channel, whatever notifier the daemon runs.
       The ladder and its AlertEvents are unchanged.
   (h) DURABLE: seeds (active + the recently retired) persist in the
       StateStore (an additive key, written only once a seed exists) and
       survive restart; restart re-fires nothing already sent (persist law).
   No seeds = byte-identical cycles, persisted state and /api/state (pinned in
   tests/test_seeded_incidents.py against goldens computed before W1).
   SINCE 16 (TWO WORLDS, 2026-09-27) a seed is lit ONLY in the TEST world
   (POST /api/test/seed; DELETE /api/test/seed/<id> retires it): (a)-(h) hold
   there unchanged, and the LIVE world never runs one.
13. CONTAINED FIRES LEAVE THE MAP; A PERIMETER BURNS FROM WHERE THE FEED LAST
   CONFIRMED IT (founder ruling 2026-09-27, verbatim: "a fire that is
   contained and no longer spreading is removed from the map";
   docs/LIVE_WORLD_PLAN.md "Contained fires leave the map ... (2026-09-27)").
   Measured on the W6 national run: SEMANTICS 9's gate reads the RECORD's
   age, not the polygon's, so a 100 %-contained fire's 148-h-old polygon was
   spread forward 148 h (11 homes 23 km away IMPACTED) and an 80 %-contained
   fire's 249-h-old polygon 249 h (87 homes EXTREME_24H).
   (a) REMOVAL (cfg.drop_contained_incidents, default OFF; watch
       --drop-contained). A feed incident is CONTAINED AND NO LONGER SPREADING
       when the feed declares it contained — some source reports
       containment_pct >= 100, or the record carries a containment / control
       / out timestamp (meta contained_at_s / controlled_at_s / out_at_s:
       WFIGS ContainmentDateTime / ControlDateTime / FireOutDateTime and the
       perimeter layer's attr_ twins, CAL FIRE ExtinguishedDate) — AND no
       source reports containment below 100 (a contradicting source keeps it
       on the map) AND it is not growing: per source, its acreage has not
       risen above that source's acreage when it was removed (the monitor's
       memory, in-process); a fire seen growing stays on the map until
       cfg.containment_no_growth_h pass without further growth (SEMANTICS 8's
       no-growth window). The incident leaves the set after the SEMANTICS 9
       gate and BEFORE assessment: no domain, no ETAs, no dispatch job, not
       in /api/state incidents nor the world summary. Seeded incidents are
       never removed.
   (b) ABSENCE LAW. Its client pairs follow SEMANTICS 6 exactly like a
       vanished feed incident's: clear streak toward CLEARED while every
       feed is healthy, frozen while any is blind; IMPACTED stays terminal.
   (c) NOTHING DISAPPEARS SILENTLY: the count and every removed id with its
       reason land in the cycle notes, CycleResult.removed_incidents,
       /api/state removed_contained / removed_incidents (present only with
       the knob on) and the world page header.
   (d) RETURN: the removal is re-decided every cycle from the feed. A removed
       fire that later reports containment < 100 (any source) or grows is
       back on the map that cycle, with a note naming why. A declaration that
       merely goes quiet (the fire left the WFIGS Current layer, which holds
       no declared-contained fire; a feed is blind) keeps it removed — only
       contradiction or growth returns a fire. The memory is in-process: a
       restart forgets it, and a quiet fire then returns until its record
       ages out (the pessimistic direction).
   (e) CONFIRMED PERIMETER (cfg.perimeter_confirmed_anchor, default OFF; watch
       --confirmed-perimeters). The F3 provenance rule stands — a polygon
       burns at its OWN time — EXCEPT when the feed has since confirmed it:
       the record's reported size (meta reported_acres, reported at
       reported_at_s) is fresher than the polygon, equals the polygon's own
       area (meta perimeter_acres) within cfg.perimeter_confirm_tolerance_frac
       either way, and no fused source carries a newer polygon (meta
       perimeter_newest_at_s). Then the polygon is the fire's extent as of
       that report and is seeded at reported_at_s (clamped to now_s; never
       earlier than its own time).
   (f) A GROWING FIRE KEEPS THE PESSIMISTIC LAW: a record reporting MORE acres
       than its polygon beyond the tolerance means the polygon lags a growing
       fire — the legacy seeding at the polygon's own time stands (a note
       says so); so do a smaller size beyond the tolerance, a newer polygon
       elsewhere, and a missing size or area. The anchor never touches the
       SEMANTICS 9 staleness gate (a record older than incident_stale_h is
       still excluded), hotspot seeds, ensembles, P10 or any crew-safety law.
   Both knobs OFF = byte-identical cycles, persisted state and /api/state
   (pinned in tests/test_contained_removal.py against goldens computed on
   commit a8bebec, before this rule existed, on a world where both bite).
14. THE PER-FIRE SPREAD-RATE BELIEF IS EVIDENCE, NEVER CLEARANCE (founder
   2026-09-27, verbatim: "as a fire ages and becomes contained, the rate of
   fire spread should be baynsian unless a new weather pattern emerges" and
   "simulations are always corrected by the real data. This becomes a
   baynesian feedback loop for each fire"; wgresponse.fire_belief,
   docs/FIRE_BELIEF.md). Two knobs, both default OFF:
   (a) cfg.fire_belief_track: per incident the watcher records the feed's
       growth OBSERVATIONS — a perimeter with a newer own time (its polygon
       area), a CHANGED reported acreage, and an acreage left unchanged for
       cfg.fire_belief_static_h while the record kept being modified (a
       zero-growth observation with 3x inflated error: a record can be
       modified without its size being re-measured) — no two of one kind
       closer than cfg.fire_belief_min_obs_spacing_h; the fire-centroid
       forecast's current hour into a bounded hourly weather log; and after
       each spread the NOMINAL member's growth curve. Consecutive
       observations of one kind form a growth WINDOW whose radial rate
       (equivalent-circle radius sqrt(A/pi) per hour: size-independent, so
       point and perimeter fires both learn) is compared with the model's
       over the same window. A 1-D grid posterior over theta = log(whole-fire
       head-ROS multiplier) starts from the prior N(0, prior_sd^2) (prior_sd
       = the engine's own measured model error) and weights each window
       0.5^(age / cfg.fire_belief_half_life_h). Tracking changes NO arrival,
       ETA, alert, notification or dispatch: it publishes
       CycleResult.fire_status (a FireStatus per active incident), the
       /api/state key 'fire_status', the world summary's per-incident
       'belief' row, and its OWN file <state-dir>/fire_beliefs.json
       (S3-mirrored beside the state file).
   (b) cfg.fire_belief_apply (requires track): in SAME-PATTERN forecast
       hours every ensemble member's head ROS follows the posterior (nominal
       = the posterior median; member k = the posterior quantile at its
       model-error stratum, the fast tail never narrower than
       cfg.fire_belief_fast_sd_floor in log space, no multiplier below
       cfg.fire_belief_mult_floor). THE P10 GUARD (cfg.fire_belief_apply_p10
       OFF, the default): P10 = min(belief P10, physics P10) — the belief
       moves the nominal, the P50 the alert ladder evaluates (alert timing
       changes) and P90, but P10 (crew safety, corridors) is never later
       than the legacy engine's. cfg.fire_belief_apply_p10 lets the belief
       move P10 too — THE SAFETY-LAW CHANGE: measured UNSAFE for contained
       fires (docs/FIRE_BELIEF.md §5: P10 too late on 38-43 % of newly
       burned cells vs 1.6-2.9 % physics — a slow fire's front is part-way
       across its next cells when a perimeter is drawn, and knowing its
       average rate does not shrink that position uncertainty). Both stay
       OFF until the founder arms them (watch --fire-belief-apply).
   The belief REFUSES to slow a fire — the PRIOR applies (full physics + the
   legacy model-error term, the exact legacy member call) — whenever:
     * NEW WEATHER PATTERN: from the first forecast hour whose Rothermel head
       ROS (the incident tile's dominant burnable fuel, flat ground) exceeds
       cfg.fire_belief_novel_severity_ratio x the P90 over the logged
       evidence hours, or whose wind >= cfg.fire_belief_novel_min_wind_mps
       turns more than cfg.fire_belief_novel_shift_deg from the evidence's
       speed-weighted mean direction, to the END of the horizon
       (FireStatus.novel_from_s); no logged weather inside the evidence
       windows => every hour is new;
     * SURPRISE: a new window grew past the belief's P90 predictive growth
       for that window — reset to the prior for cfg.fire_belief_reset_h
       (sticky; evidence older than that window is dropped): slow to trust,
       quick to distrust;
     * RAPID: a window inside cfg.fire_belief_rapid_lookback_h grew —
       counted at its lower bound beyond measurement noise, growth -
       cfg.fire_belief_rapid_noise_z x its sd — at >= cfg.fire_belief_rapid_
       acres_per_day (24-h equivalent) or at >= cfg.fire_belief_rapid_frac_
       per_day with >= cfg.fire_belief_rapid_min_acres, a young fire grew
       that fast since discovery, or a reset is active — member multipliers
       take the LARGER of prior and posterior at each quantile;
     * no usable evidence (state 'prior'), a SEEDED (drill) incident (never
       observed, never learned), or any belief failure (fail-safe: legacy).
   (c) THE v2 RULES (2026-09-28; docs/FIRE_BELIEF.md "v2"; default = the
       rules above, byte-identical; watch --fire-belief-v2 arms all three).
       Measured on 11 real 2026 fires, where the hourly new-pattern test
       fired on the ordinary afternoon wind turn (median 13 h into a
       forecast) and the 3 %-of-area noise hid big fires' growth:
       cfg.fire_belief_novelty = 'daily' compares forecast DAYS with
       evidence DAYS (local solar days; per day the peak severity and the
       driving wind over its hours at >= half its peak): a day is NEW if its
       peak exceeds cfg.fire_belief_novel_day_severity_ratio x the harshest
       evidence day, or its steady driving wind of >= cfg.fire_belief_novel_
       day_min_wind_mps comes more than cfg.fire_belief_novel_day_shift_deg
       from every evidence day's; the prior applies from that day's first
       qualifying hour to the end of the horizon.
       cfg.fire_belief_rapid_noise = 'calibrated' judges a PERIMETER
       window's growth beyond the measured re-mapping noise, sd = hypot(k x
       sqrt(A acres), sqrt(2) x cfg.fire_belief_area_abs_err_acres), k =
       cfg.fire_belief_rapid_perim_noise_k (reported windows keep the legacy
       sd); cfg.fire_belief_rapid_persist_windows = N >= 2 makes N
       consecutive perimeter windows at a rapid rate (raw) rapid. The rapid
       rule then flags more fires (the belief may only speed them up): the
       conservative direction. The P10 guard is untouched by every v2 knob.
   FireStatus is the interface the off-map road-crossing rule consumes.
   DRILLS NEVER CHANGE REAL CONDITIONS: a TEST world gets a deep copy
   (FireBeliefStore.copy(): persistent=False, never saved) through
   ThreatMonitor(..., fire_beliefs=copy) and no belief file. Point
   incidents are still SEEDED as a point at now_s (the belief learns their
   rate from acreage; it does not re-anchor their extent).
   All knobs OFF = byte-identical cycles, persisted state, /api/state and
   /api/world/summary, and no fire_beliefs.json written or read (pinned in
   tests/test_fire_belief.py against goldens computed on a8bebec).
15. THE OFF-DOMAIN HORIZON IS PER FIRE (founder rule 2026-09-27, verbatim:
   "the cap at extrapolation should include the rate of fire spread vs.
   drive time through the region. the pathway only becomes unaccessible
   when the crossing time between fire and road is less than 1 hour unless
   the fire has shown rapid rate of spread"; docs/LIVE_WORLD_PLAN.md ticket
   1.5, "The off-domain horizon is per fire (2026-09-27)";
   cfg.corridor_offraster_per_fire, default OFF; watch --offmap-per-fire).
   Measured stake: under --fail-closed the flat 1.7 m/s bound let the 80 %-
   contained Willow fire, reported "Minimal | Smoldering, Creeping", veto a
   seeded job ~69 km away that is served when Willow is absent. The rule
   changes ONLY the fail-closed bound of SEMANTICS 3 (corridor_fail_closed /
   corridor_unreached_bound): the arrival given to a corridor sample OFF a
   fire's forecast raster, or UNREACHED inside it, = min over that fire's
   reached boundary runs of t_run + distance / speed.
   (a) SPEED PER FIRE. corridor_offraster_ros_mps (1.7 m/s) is the CEILING.
       A fire extrapolates at the ceiling when it is RAPID, or when its
       behaviour is UNKNOWN (a new or unreported fire has not shown it is
       slow; seeded fires are always unknown). A KNOWN, not-rapid fire
       extrapolates at clamp(front, corridor_offraster_ros_floor_mps,
       ceiling): front = the P90 (nearest rank) over the fire's RAW (un-
       eroded) P10 raster's reached boundary cells of the outward front
       speed cell_m / (arrival(boundary cell) - arrival(inward neighbour)),
       counting only cells whose inward neighbour burned earlier — the
       forecast's own speed where the fire crosses its domain edge. No such
       cell (the raw front never crosses the edge) = the ceiling.
   (b) STATUS FROM THE FEED (wgresponse.offmap.status_for). WFIGS incident
       points carry FireBehaviorGeneral + FireBehaviorGeneral1-3 (meta
       fire_behavior, fire_behavior_descriptors, fire_behavior_at_s = the
       record's ModifiedOnDateTime). KNOWN = a report in the recognized
       IRWIN vocabulary no older than cfg.fire_behavior_max_age_h. RAPID = a
       known report whose general behaviour is Very Active or Extreme, or
       with any descriptor Running, Wind Driven Runs, Uphill Runs, Crowning
       or Long-range Spotting. Contradictions resolve toward rapid (a 100 %-
       contained fire still reported Extreme | Running is rapid; so is
       "Minimal" beside a Running descriptor). Other status sources OR-
       combine (offmap.merge_status): rapid if any says rapid, known if any
       knows.
   (c) THE CROSSING-TIME MARGIN. A sample whose arrival came from that bound
       is unsafe when bound - pass_time < max(the leg's own margin,
       cfg.corridor_offraster_margin_min) — the founder's hour
       (--offmap-per-fire sets 60). Samples inside the forecast keep their
       calibrated margins (route_margin_min / route_margin_roads_min). For
       rapid and unknown fires the speed is the legacy ceiling, so there the
       margin can only add conservatism.
   (d) NEVER SILENT. Per domain incident: one cycle note, CycleResult.offmap
       (contracts.OffDomainHorizon: speed, reason, margin, status), and with
       the knob armed an "offmap" block on its /api/state incident row and
       its /api/world/summary row. dispatch.plan takes the table as
       offmap={incident_id: OffDomainHorizon}; the simulator never passes it.
   (e) WHAT IT CANNOT SEE: the front speed is the forecast's speed at the
       edge crossing. A known-slow fire that runs faster beyond its domain —
       an unforecast wind event, a forecast wind shift after it crossed its
       edge, a behaviour report lagging the fire (the record's modified time
       is not the behaviour's own time) — is extrapolated LATE; the floor
       and the margin absorb only part of it (measured in the plan section).
       A fire that never reaches its edge within the horizon keeps the
       legacy law (no bound at all).
   (f) WITH THE SPREAD BELIEF (SEMANTICS 14, integrator 2026-09-27): when
       cfg.fire_belief_track is also on, the belief's FireStatus table is
       computed BEFORE the planner and OR-combined with the feed's report
       (offmap.merge_status): growth beyond measurement noise or an active
       surprise reset makes a fire RAPID (ceiling) even under a slow report,
       and a completed growth window makes an unreported fire KNOWN (its own
       front). So with both knobs armed, tracking alone can change which
       roads this rule opens; with this knob OFF it changes none (pinned in
       tests/test_belief_offmap_worlds.py).
   Knob OFF = byte-identical cycles, plans, persisted state, /api/state and
   /api/world/summary (pinned in tests/test_offmap_horizon.py against goldens
   computed on commit 6c748c5, before this rule existed, on a world where it
   bites).
16. TWO WORLDS: A LIVE MAP AND A TEST MAP (founder ruling 2026-09-27,
   verbatim: "we need a test map and a live map so all simulations or drills
   do not impact real conditions"; docs/LIVE_WORLD_PLAN.md "Test map and live
   map (2026-09-27)"; wgresponse.worlds). The watch daemon runs two fully
   isolated worlds in one process:
   (a) THE LIVE WORLD is real conditions only: feed incidents, the real client
       registry, the real crews (beacons), the real alert engine, the real
       dispatch plan, the real notifier and the live StateStore file + S3
       object. It accepts NO seeded fire: its ThreatMonitor is locked
       (ThreatMonitor.lock_live — create_seed / add_seed / restore_seeds raise
       seeded.LiveWorldRefused and no cycle ever unions a seed), and POST /
       DELETE /api/seed answer 409 with a pointer to /api/test/seed. Nothing
       the test world does — seeds, retirements, crew staging, test dispatch,
       resets, its own persistence — reads the live clock, wakes or delays the
       live loop, writes a live key or file, or changes one byte of any live
       CycleResult, /api/state, /api/plan, /healthz, live world summary, live
       notification or live persisted state (pinned byte-for-byte, with and
       without heavy drill activity, in tests/test_worlds_isolation.py). Seeds
       persisted by the pre-two-worlds build are MIGRATED at load, loudly: the
       seed rows, their alert pairs, ledgered drill sends and growth clocks
       move to the test namespace and the live incumbent plan loses its seeded
       stops — never run live.
   (b) THE TEST WORLD is drills and simulations. It sees the live world's
       incidents and forecasts READ-ONLY — the live cycle's own WorldSnapshot
       (ThreatMonitor.last_snapshot), shared by reference, never recomputed and
       never mutated — adds SEEDED incidents (SEMANTICS 12, computed in the
       test world only through the same ThreatMonitor path) and runs its OWN
       alert engine, its OWN crews and its OWN dispatch plan over live ∪
       seeded forecasts (the live fires' P10 still bounds every test
       corridor). Its weather provider is its own instance, a caching registry
       is its own instance, and its terrain reads the live tile cache
       read-only while fetching / caching everything else in its own
       namespace. Each live cycle triggers ONE test cycle at the live cycle's
       own time; a test action (seed, retire, crew staging) re-runs the drill
       at once at the test clock, but that re-run evaluates only the SEEDED
       fires' pairs (AlertEngine.evaluate only_incidents) — a live pair's
       ladder on the TEST map moves only with the live cycles, exactly as on
       the LIVE map (pinned); the planner re-plans on every run.
   (c) DRILLS: the test world is IDLE (no cycle, zero cost; the TEST map
       mirrors the live picture read-only) until a drill starts — the first
       seed, or a test-crew reset / placement. The drill start CLONES the live
       world while the live loop is idle: alert records + notified ledger +
       growth clocks, the incumbent plan, the regime postures and the crew
       roster at its current positions and statuses (a crew the live
       dispatcher would not use — stale beacon, UNKNOWN, OFF_DUTY — is cloned
       ineligible), so a drill answers "what would we do right now"; from then
       on each world evolves alone. POST /api/test/reset wipes the test world
       (its namespace included) back to IDLE.
   (d) TEST CREWS ARE VIRTUAL: they never beacon and never move (W1.4 is not
       built); POST /api/test/crews/reset re-clones the live roster, POST
       /api/test/crews/place stages one crew. A test plan never leaves the
       process (no real crew receives a test dispatch) and no test position
       ever enters the live CrewTracker.
   (e) EVERYTHING THE TEST WORLD EMITS IS DRILL-ONLY: its notifier
       (seeded.DrillOnlyNotifier) holds no client channel at all — every alert
       it raises, a seeded fire's or a LIVE incident's as seen from the test
       world, goes to the drill log and is ledgered (restart re-fires nothing).
   (f) OWN NAMESPACE, RESTART-SAFE: the test world persists to
       <state-dir>/test_world/ and s3://<prefix>/test_world/ (its state file
       and its terrain overlay) — never a live key — and a restart restores the
       drill (seeds, ladder, ledger, plan, virtual crews, drill log) without
       re-firing anything.
   (g) THE LIVE WORLD NEVER WAITS: the test world runs on its own thread,
       after each live cycle (or at once on a test action), yields to a
       running live cycle before it clones or cycles, and a test-world failure
       is logged — never raised into the live loop.
   No test activity = the pre-two-worlds live world, byte for byte (the W1
   goldens in tests/test_seeded_incidents.py still pin it).
17. SPREAD READS THE LOCAL WEATHER FORECAST ACROSS ITS DOMAIN (founder
   2026-09-27, verbatim: "ensure that fire spread includes local weather
   forecast"; docs/LIVE_WORLD_PLAN.md "Spread reads the local weather
   forecast (2026-09-27)"; cfg.weather_grid_km, default 0 = OFF; watch
   --weather-grid-km KM [--weather-points-max N]; wgresponse.wxfield). The
   legacy law prices every edge of a domain with ONE hourly forecast fetched
   at the fire centroid; national domains measure 50-75 km across, with
   ridges, valleys and coast inside one of them.
   (a) THE POINTS. The fire centroid stays the PRIMARY forecast (point 0).
       The others are the nodes of a GLOBAL lat/lon lattice that fall on the
       domain's grid (geo.latlon_to_rc): at level L the spacing is
       s = weather_grid_km x 2^(L//2), x sqrt 2 on odd levels
       (wxfield.level_spacing_km); row i sits at lat_i = i x s /
       geo.M_PER_DEG_LAT (i integer, counted from the equator) and holds
       nodes at lon = j x s / geo.m_per_deg_lon(lat_i) (j integer, counted
       from Greenwich). A node is a function of (s, i, j) only, so
       overlapping domains at one level ask the SAME points (and hit the
       same cache entries), and level L+2's nodes are a subset of level
       L's. L starts at 0 and grows until centroid + nodes <=
       cfg.weather_points_max (a sqrt 2 step halves the node count, so a
       domain keeps about half its point budget or more).
   (b) THE FETCH. Every point goes through the same WeatherProvider as the
       centroid (the watch daemon: NWS). A failed CENTROID is today's
       provider failure (SEMANTICS 6: the incident is BLIND — never a silent
       fallback). A failed SECONDARY point falls back to the centroid
       forecast for its zone, with a cycle note naming the point; the cycle
       is not degraded, because that zone prices exactly the legacy weather.
       With the knob on, NwsWeather keeps a process-level cache: the points
       -> forecastGridData mapping (static) and each gridpoint's decoded
       hourly series by URL for cfg.weather_cache_ttl_s (the centroid's
       included; a failed refresh raises, a stale series is never served).
   (c) THE ZONES. Every domain cell takes the forecast of its NEAREST point
       — a Voronoi partition in local metres (east-west measured at the
       cell's row latitude; ties to the lower point index), computed once
       per (grid, points) and cached. Zones are discrete, never blended per
       cell, so the ROS memo keeps sharing entries between cells of equal
       effective weather and stays exact (memo = WGRESP_NO_ROS_MEMO=1
       reference path, with zones on too).
   (d) THE SPREAD. An edge leaving a cell is priced with THAT cell's zone
       forecast at the departure hour (and at each later hour under
       edge_weather_piecewise): the per-hour effective weather becomes per
       (zone, hour). Ensemble perturbations (wind / moisture scale,
       direction offset, gust blend, model error) apply identically in
       every zone. A spotting ember flies on its SOURCE cell's zone wind
       (bearing, Sardoy distance and flight time). Seeded (drill) incidents
       keep their single-point weather (SEMANTICS 12f).
   (e) ONE FORECAST WHERE ONE IS NEEDED. Everything else that consumes a
       single forecast keeps the centroid's: the extreme-regime posture,
       dispatch's gel-window forecasts (plan(forecasts=...)), the per-fire
       off-domain horizon (which reads the P10 raster the zones produced)
       and any per-fire belief or novelty logic.
   (f) NEVER SILENT. One cycle note per domain incident (points asked,
       failed, lattice spacing used), CycleResult.weather_fields, and with
       the knob armed a "weather_grid" rule block on /api/state plus a
       "weather" block on every incident row and world-summary row: the
       points (lat, lon, role, source, ok, cells), each point's wind, gust,
       direction and RH at the next few hours, and how far the points
       disagree.
   (g) WHAT IT CANNOT SEE: winds finer than the forecast's own grid (NWS
       2.5 km: canyon channelling, slope and valley flows, a sea-breeze
       front inside one cell), anything between lattice nodes (nearest
       point, no interpolation — zone edges are discontinuities), the
       fire's own wind, and the forecast's own error (the ensemble still
       perturbs every zone alike).
   Knob OFF = byte-identical arrival rasters, cycles, plans, persisted
   state, /api/state and /api/world/summary (pinned in
   tests/test_weather_field.py against goldens computed on commit b5ce62c,
   before this rule existed, on a world whose forecast varies in space).
18. A PERIMETER OR SIZE THE FEED CANNOT HAVE MEANT NEVER TEACHES THE BELIEF
   AND NEVER SHRINKS THE SEED (2026-09-27 audit of the live feed path the
   SEMANTICS 14 belief learns from; docs/review/feed_sanity_2026-09-27.md;
   wgresponse.feedsanity; cfg.feed_sanity, default OFF; watch --feed-sanity).
   Measured on the recorded live layers, the deployed watcher's belief file
   and the WFIGS Daily history of 11 2026 fires (918 published edits): 12
   edits on 6 fires published a polygon under 10 % of its own record's size
   (0-8,183 ac while the record reported 10,524-156,699 ac) for minutes to
   ~2 days (the live layer right now holds a 29 ac polygon under a 200,292
   ac complex record); 306 of the 674 perimeter windows the belief would
   have formed were one polygon re-stamped under a newer time (the deployed
   watcher's first window was one); 23 of 560 live polygons carry an area
   figure more than 2x off their own geometry; reported sizes reset (Hagen
   15,798 -> 700 ac) and fused sources disagree (0.01 vs 73 ac).
   (a) THE POLYGON'S AREA IS ITS GEOMETRY'S. The feed's own area figure
       (meta perimeter_acres: WFIGS poly_GISAcres, FIRIS area_acres) stands
       for a polygon only while it agrees with the area of the rings the
       engine seeds (fire_belief.rings_area_m2) within a factor of
       cfg.feed_sanity_area_ratio; otherwise the rings' own area is used.
   (b) IMPLAUSIBLE TIME. An own time — a perimeter's meta perimeter_at_s
       (else its record's updated_at_s), a reported size's reported_at_s —
       later than now + cfg.feed_sanity_future_h, or earlier than its fire's
       discovery less 24 h (a year before now when discovery is unknown), is
       not a time the feed can have meant. The belief takes no observation
       from it (never clamped to now: a clamped future stamp was re-observed
       as a new zero-growth capture every fire_belief_min_obs_spacing_h).
       The seed takes the record's own time instead (the F3 fallback for a
       non-positive stamp; SEMANTICS 9's clamp still applies after it).
   (c) COLLAPSE. A polygon whose area is under cfg.feed_sanity_collapse_frac
       of its own record's reported size (meta reported_acres), when that
       size is at least cfg.feed_sanity_min_acres, is not the fire's
       perimeter: the belief takes no observation from it. The seed uses the
       fire's LAST PLAUSIBLE polygon instead — the monitor's in-process
       memory of the newest polygon that passed these checks, with that
       polygon's own time and meta, for up to cfg.feed_sanity_memory_h after
       it was last seen — and so does a polygon that DROPS below the
       remembered one by more than cfg.feed_sanity_drop_z x the sd of the
       difference under the belief's own area error (fire_belief_area_rel_err
       / fire_belief_area_abs_err_acres): within the memory window the seed
       is never a smaller fire than the feed showed. With nothing remembered
       (a restart, a first sighting) the polygon is seeded as received. Every
       cycle it bites is one note; a fire first found implausible is also an
       OperatorEvent('feed_sanity') (never a degradation).
   (d) RE-STAMP. A polygon whose area is within cfg.feed_sanity_restamp_frac
       of the last polygon the belief observed is the same capture re-issued
       under a newer time, not a new capture: it becomes a zero-growth
       observation only once cfg.fire_belief_static_h has passed since that
       observation, with the static 3x error — the reported series' own
       unchanged-size rule. The seed is unchanged (SEMANTICS 13e/f own the
       seed time of an old polygon).
   (e) A DROP IS NEVER NEGATIVE GROWTH. A size (either series) below the
       series' last observation by more than cfg.feed_sanity_drop_z x the sd
       of the difference is HELD — no observation — until the series comes
       back up (a glitch: never observed) or the lower size persists for
       cfg.fire_belief_static_h (a correction: the series restarts there,
       with no window across the drop). A held size does not move the young-
       fire rapid clause.
   (f) WHAT IT DOES NOT DO: it never touches the feed queries, fusion, the
       SEMANTICS 9 staleness gate, the SEMANTICS 13 removal, ensembles
       beyond the seed polygon and its time, P10 or any crew-safety law; the
       confirmed anchor (13e) sees a substituted polygon's own meta. It
       cannot fix fusion's identity problems (FIRIS rows carry no IRWIN id,
       so one fire can be several incidents; a fused size follows its
       freshest source), and its memory is in-process: a restart during a
       collapse seeds the collapsed polygon, with the alarm, until the feed
       restores a plausible one. Seeded (drill) incidents are never checked.
   Knob OFF = byte-identical cycles, persisted state, /api/state,
   /api/world/summary and fire_beliefs.json (pinned in
   tests/test_feed_sanity.py against goldens computed on commit 0bbd430,
   before this rule existed, on a world where every guard bites).
19. EMBERS FLY AS FAR AS THE FIRE THAT THROWS THEM (founder 2026-10-09,
   verbatim: "begin the calibration of the ember distance and size of fire
   calibration"; docs/review/ember_spotting_calibration_2026-10-09.md;
   scratch_review/ember_spotting_probe.py; cfg.spot_fire_size, default OFF,
   NOT armed by the simulator — the founder decides). SEMANTICS 11 made
   spotting functional by sampling the ember-distance distribution, but every
   burning cell throws from the FULL Sardoy lognormal from the first minute
   of a fire's life, and a cell an ember ignites throws its own ember the
   moment it burns. Measured on the one-click scenarios under the "extreme"
   preset: in the first minute of their lives, at about 2 ha, Boulder and
   Paradise threw embers that lit spot fires 1.0-1.6 km away, and Park City
   leap-frogged 3.2 km in ~7 minutes through a 6-hop spot-from-spot chain
   (12 of its 18 burned cells spot-ignited). Real fires do not: the source fire's active area is the
   strongest predictor of its maximum spotting distance and of its number of
   long-distance spot fires (Storey et al. 2020), and a spot fire needs
   minutes to ignite and grow (Alexander & Cruz 2014: 5-10 min). With the
   knob on (and spotting_enabled):
   (a) FIRE SIZE. A burning cell's fire is the connected burned area (8-
       neighbour) it joins when it ignites: every cell already burned then,
       kept history and perimeter-interior cells included. A spot fire is a
       new, small fire until it touches another.
   (b) REACH. The cell's ember — SEMANTICS 11's single draw with its seed,
       bearing and flight time unchanged — flies at most spread.spot_reach_m
       = Albini's (1983) maximum spotting distance from a wind-driven surface
       fire of the cell's Byram intensity in its 20-ft wind (the BehavePlus
       equations, flat ground, open downwind cover) x (fire ha /
       cfg.spot_fire_size_ref_ha) ** cfg.spot_fire_size_exp. A longer draw
       burns out in flight: no spot. So a small fire launches fewer and
       shorter embers and a fire larger than the reference reaches beyond
       the surface-fire maximum (its plume lofts brands higher).
   (c) DELAY. A spot fire starts cfg.spot_ignition_delay_min after its ember
       lands; the advancing front may overrun it first.
   (d) LAWS KEPT. No new random draw (the per-cell draw is the legacy one;
       seeding never wall-clock); the ROS memo stays exact (Albini's maximum
       is a function of the memo key, the size is applied at the teleport-
       edge site — memo = WGRESP_NO_ROS_MEMO=1 reference with the rule on);
       label-setting holds (a cell's fire size is a function of labels
       already final, ties settled by (time, index); every ember still lands
       after it is thrown); RE-ANCHOR HISTORY stays continuous (kept cells
       join their fires first, embers aloft carry their delayed landing — a
       no-op re-anchor is the continuous run cell for cell). P10 ordering is
       unchanged in kind: every member runs the same law on its own draws.
   (e) WHAT IT CANNOT DO. The engine has no torching or crown-fire model: a
       torching tree's 0.5-1.4 km spot from a young timber fire (Albini 1979)
       and the plume-driven 5-16 km spot fires of the largest fires (Camp
       2018, Dixie 2021) are not represented; an ember landing on any
       burnable cell still ignites it (no ignition probability); the size is
       the total connected burned area, a proxy for the ACTIVE area Storey
       measured (a fire that has gone quiet keeps its size); fire size cannot
       move a slow fuel: where the floor no longer lifts timber, a timber-
       litter ignition crawls whatever its embers do — measured on the seven
       timber-start one-click scenarios (Park City and six Colorado towns:
       6-395 acres in 6 h, about what they burn with spotting off).
   Knob OFF = byte-identical arrival rasters (pinned in
   tests/test_spot_fire_size.py against goldens computed on commit 05cf1d6,
   before this rule existed, on worlds where it bites: a high-wind corridor,
   a mixed-fuel tile with a perimeter, a re-anchor with embers aloft, an
   ensemble and the committed Park City tile under both floor class sets).
20. NO CREW DRIVES A LEG THE LAW HAS NOT JUDGED (2026-10-09; the crew-
   overrun gap the simulator's city weather exposed — docs/review/
   city_weather_2026-10-09.md "Safety: one gap"; docs/SIMULATOR_DESIGN.md
   "No crew drives an unjudged leg"; scratch_review/crew_egress_gap_
   sweep.py). The 10c audit judges a crew's EXACT position at every step;
   the laws that steer crews judged less than that, so a crew could be sent
   into the eroded-P10 band down a leg no law had judged lawful. Measured
   before the rule on the 13 one-click scenarios x 4 intensities x city /
   generic weather x both ROS-floor class sets (208 runs, 6 h each): 10
   crew-overrun rows in 9 runs, every one "imminent" while egressing, 0
   departure violations. The weather only changed WHICH legs got driven:
   the arrival fields the laws read do not change between cycles (a city
   forecast's daily cycle is solved into the field once; re-anchors happen
   only on a world change). Four mechanisms, none in the weather:
     W  a WITHDRAWAL egress (6 rows, all Malibu): the road-egress guard
        aborts a deploy at an early stop and the crew drives to its route's
        adopted zone — the LAST stop's — on a road from THIS stop no law
        had judged (east through the band, toward a zone across the fire);
     Z  a ZONE imminent at the first audit after arrival (3 rows: Aspen,
        Glenwood x2): dispatch's egress law clears the zone at the arrival
        instant only, and the 10b sweep re-checked an egress in flight by
        the zone's own cell at ZERO lead — the front reached it inside the
        buffer before the next audit;
     V  a VOLUNTARY decamp over exposed transit (1 row: Eagle): the SEM-10e
        exposed-transit fallback rode 915 s of pessimistic-exposed road to
        a lawful zone (the extended-only escape_proactive_lawful_only
        assumed the standard graph never offers such routes);
     S  a SAMPLING gap (the synthetic triage scatter): see (a).
   (a) CELL-EXACT CORRIDOR (cfg.corridor_cell_exact). SEMANTICS 3 samples a
       leg every corridor_sample_m (300 m), and the SEM-10 route judgement
       every ~275 m — on a 90 m grid one cell in three. A leg that clips a
       cell between two samples is never judged there; measured on the
       synthetic triage scatter (500 m cells): a chord passing 467 m from
       the ignition cell, inside its 550 m eroded band (eroded P10 = the
       ignition time), read 9 h clear at its samples, and the crew was
       audited inside the band. With the knob on, every raster cell a leg
       passes through (geo.path_cell_runs: the leg followed linearly in lat/
       lon, as the simulator's crews drive it) with a finite arrival is
       ALSO a sample, judged at the latest moment the crew is in it — the
       distance at which the leg leaves the cell. safety.LegOracle (the
       dispatch ingress / egress law; the live engine's too if armed) and
       the simulator's SEM-10 judgement (_route_metrics, the road-network
       escape search — both on one per-span sample walk, so their numbers
       stay equal — and every relocation / escape / decamp choice) read it;
       the crew's own starting cell keeps its SEM-10 carve-out while the
       walk has not left it (a crew standing in the burn must be able to
       drive out). The legacy samples all stay (never weaker); off-raster
       and unreached stretches keep their legacy (fail-closed: bounded)
       samples.
   (b) EVERY LEG A CREW STARTS IS JUDGED (cfg.crew_egress_judged; read only
       by the simulator — the live engine moves no crew). (i) The moment a
       crew turns onto an egress leg from a stop — a safety withdrawal (the
       road-egress guard, eta drift, a policy change) or leaving its last
       stop — that leg (the road it will drive, at the speed it drives it,
       from where it is at that moment) must be LAWFUL: nothing burned at
       its pass time, every sample's eroded P10 at least egress_buffer_min
       beyond its pass time (the 10c imminent line), and the destination's
       at least one step (the sweep's lead) beyond its arrival; else the
       SEM-10d routed choice (a lawful zone, else the road-network escape,
       else the least exposure) replaces the target, with a loud note
       (dispatch still judges each stop's own nearest lawful zone, SEMANTICS
       4, and still emits one zone per crew plan: W). (ii) The sweep re-
       judges every egress in flight the same way each step (legacy: the
       zone's own cell, zero lead: Z); a LAWFUL alternative always replaces
       an unlawful leg, and between two unlawful legs the escape hysteresis
       decides. Deploying crews keep the legacy 10b zone check (their egress
       is judged when it starts). (iii) A VOLUNTARY SEM-10e decamp never
       rides exposed transit, on any tile (the exposed-transit zone fallback
       is not offered — exactly escape_proactive_lawful_only, which the
       simulator arms for extended sessions: V); the lawful zone and lawful
       road-node decamps are unchanged, and with no lawful exit the imminent
       law still owns the emergency.
   WHAT IT CANNOT DO: the laws read the forecast fields; a crew is safe
   from the 10c audit only as far as the eroded P10 is pessimistic. An
   in-flight INGRESS leg is judged when it is planned (cell-exact) and re-
   judged only when its crew is re-planned; a world change mid-leg (wind
   dial, a new ignition) is caught by the per-cell sweep, one step of lead
   at a time. An EMERGENCY relocation (10a: the crew's own cell already
   threatened) still takes the least-exposure leg when no lawful one exists,
   and a crew genuinely cornered is still reported by the audit. Both knobs
   OFF = byte-identical (pinned in tests/test_crew_egress_judged.py against
   goldens computed on commit 8973cd9, before this rule existed, on worlds
   where it bites); the SIMULATOR arms both (explicit override wins).
21. THE FIRE MAY RUN IN ANY DIRECTION, NOT ONLY THE GRID'S (grid-orientation
   review 2026-10-09; docs/review/grid_orientation_2026-10-09.md;
   scratch_review/grid_orientation_probe.py; cfg.spread_any_angle, default
   OFF). The spread is a minimum-travel-time search over a 16-neighbour
   lattice: from each cell the fire may only step along 16 directions, so on
   a uniform tile it reaches exactly the 16-sided polygon inscribed in its
   own fire ellipse with corners on those directions (measured to within
   1 % of area). At high wind the length-to-breadth ratio climbs to its cap
   of 8 and the ellipse is a needle a few degrees wide: with the wind
   between two lattice directions the head ran at 22-28 % of its own rate,
   on a lattice axis the width was missed, and the burned area FELL as the
   wind rose (GR2, 6 h: 2,493 ac at 20 mph, 2,160 at 25; between directions
   2,110 -> 1,302; the engine's own ellipse 4,707 -> 5,772). Late at the
   head is the anti-safe polarity for every P10 deadline. With the knob on:
   (a) ANCHORS. Every labelled cell remembers the cell its straight run
       started from (a seed, a spot fire's landing cell, or the cell whose
       lattice edge labelled it). A burning cell offers each neighbour the
       edge it always did AND, when the edge leads away from its anchor,
       the anchor's straight run to that neighbour; the earlier wins and
       the neighbour inherits the winner's anchor (a tie keeps the longer
       run). Theta*-style any-angle paths over the existing lattice.
   (b) EVERY RUN IS A REAL PATH. A run's arrival is traced cell by cell
       along the straight line (spread._segment_arrival): each crossed cell
       prices its stretch at its OWN ellipse (directional ROS of its own
       head rate, length-to-breadth ratio and bearing; the edges' 1e-4 m/s
       floor), under the weather of the moment the fire enters it (a cell
       entered before a re-anchor's t0 under that kept_wx history span). A non-
       burnable cell, or one that cannot carry fire then, ends the run;
       through a cell corner it passes when either side cell is burnable
       (the diagonal gate rule). So no run reaches a cell earlier than a
       real path through the engine's own physics can; only earlier than
       the 16 directions could. The lattice edges are unchanged (source-
       only rate, gate rule): every legacy path is still offered at its
       legacy cost, so under constant weather no cell burns later than
       without the knob (without the SEMANTICS 19 size law). Runs restart
       past spread._ANY_ANGLE_MAX_CELLS (256 cells).
   (c) LAWS KEPT. Label-setting (a run never lands before the cell offering
       it burned), deterministic (no draw), the ROS memo exact (memo =
       WGRESP_NO_ROS_MEMO=1 reference with the knob on, weather zones and
       piecewise edges included). RE-ANCHOR HISTORY stays continuous:
       compute_arrival / ensemble_arrival(anchor_log={}) record each
       labelled cell's anchor and kept_anchor resumes each kept cell's run,
       so a no-op re-anchor is the continuous run cell for cell and a wind
       shift the continuous piecewise run; the simulator threads them
       (_SimFire._anchors).
   (d) MEASURED. Uniform tiles (GR2, GS2, SH5, TU5; 0-40 mph; the wind swept
       through a full lattice gap): the cells the engine's own ellipse covers
       (IoU 0.98-1.00; head within 3 %, area within 4 % at every angle, 1.6 %
       at L/B 8), area grows with wind; the downwind P10 that was hours late
       (30 mph: 4.3 h for 1.0 h at 3 km) is the ellipse's own time. Real
       tiles (the per-cell continuum reference: the same tile solved 5x5
       finer): the inversion on the committed tiles is mostly the elliptical
       physics itself (narrow fires against non-burnable ground; the
       reference loses area from 10 to 30 mph too); the knob removes 30-77 %
       of the legacy lateness at 20-40 mph; at low wind on a timber mosaic it
       can over-burn (the unchanged source-only lattice edges into slow cells,
       now reached sooner). Cost 2-3.4x per solve, 3x peak memory on a
       300k-cell domain. Numbers in the review.
   (e) WHAT IT DOES NOT DO. It does not change the ellipse (lb cap 8 kept:
       without it the analytic area itself falls with wind), the lattice
       edges' source-only rate or gate rule, spotting, or any crew law; a
       fire forced through a one-cell gap at a corner still bends at cell
       centres (the continuum slips past corners the lattice cannot).
   Knob OFF = byte-identical arrival rasters (pinned in
   tests/test_spread_any_angle.py against tests/test_spread.py's legacy
   fingerprints, with the new arguments passed).
22. A FOREST FIRE MAY CROWN (founder 2026-10-12, option 3 of the Park City
   speed review: "build a crown-fire model"; docs/review/crown_fire_model_
   2026-10-12.md; scratch_review/crown_fire_*.py; cfg.crown_fire, default
   OFF, NOT armed by the simulator or the live watcher — the founder
   decides). The engine modelled surface fire only: with the fuel-class
   floor on grass alone a timber ignition crawled at its litter's rate
   (Park City one-click: 50 acres in 6 h), while wind-driven conifer fires
   run as crown fires at roughly 10 % of the 10-m wind (Cruz & Alexander
   2019). With the knob on, on a tile carrying LANDFIRE canopy layers
   (TerrainTile.canopy; LF2024 CBH / CBD / CC / CH):
   (a) CROWN FUEL. A burnable cell holds crown fuel when CBD >= 0.02 kg/m^3
       (rothermel.CROWN_MIN_CBD_KGM3 — LANDFIRE codes broadleaf stands CBH
       10 m / CBD 0.01 "to prevent false simulation of crown fire") and
       CH > CBH. Only such cells change.
   (b) INITIATION AND TYPE (rothermel.crown_fire). The cell's surface fire
       (Rothermel after the fuel-class floor) crowns when its Byram
       intensity reaches Van Wagner's (1977) I_o = (0.010 CBH (460 + 25.9
       FMC))^1.5 kW/m; ACTIVE when the Cruz, Alexander & Wakimoto (2005)
       crown rate CROS_A = 11.02 U10^0.90 CBD^0.19 exp(-0.17 EFFM) m/min
       reaches Van Wagner's 3.0 / CBD m/min (CAC >= 1), else PASSIVE at
       CROS_A exp(-CAC). U10 the cell's 10-m wind (km/h, after gust blend
       and ensemble scaling), EFFM the fine dead fuel moisture as Cruz et
       al. define it (Rothermel 1983 tables: the engine's dead fuel
       moisture, +3 points under a canopy cover >= 50 %), FMC
       cfg.crown_foliar_moisture_pct (100 %) or, with
       cfg.crown_foliar_moisture_seasonal, the FBP System's seasonal model
       per cell (latitude, longitude, elevation) and UTC day.
   (c) THE CELL'S FIRE. Head ROS = max(surface, crown rate); Byram
       intensity = ROS (HPA + CFB CBD (CH - CBH) 18,608 kJ/kg) with CFB 1
       (active) or Van Wagner's (1993) passive crown fraction burned with
       FARSITE's coefficient; where the crown leads, the ellipse is
       Rothermel's (1991) crown ratio 1 + 0.125 U20 (mi/h) when broader.
       Then ros_scale / hour_ros_scale (ensemble, belief) and the spot
       tuple apply as to any cell: an ember flies on the crown intensity
       (SEMANTICS 11), and under SEMANTICS 19 a crowning cell's reach is at
       least the distance one torching tree of the stand height lofts a
       brand (rothermel.torching_spot_max_m, Albini 1979 as BehavePlus
       codes it) once its fire covers spread._TORCH_MIN_FIRE_HA — a lone
       spot fire relays no torching ember.
   (d) LAWS KEPT. Deterministic, never wall-clock (no new draw; seasonal FMC
       reads the forecast hour's date); the ROS memo exact (the canopy
       values and FMC ride in the crown memo key: memo = WGRESP_NO_ROS_MEMO=1
       reference with the knob on, zones / piecewise / any-angle / hour
       scale included); label-setting; RE-ANCHOR HISTORY continuous (a
       no-op re-anchor is the continuous run cell for cell, spotting and
       any-angle included). Under constant weather and with spotting off no
       cell burns later than without the knob; with spotting on, single
       cells can (each ember's draw re-scales with the crown intensity —
       as with any change that moves embers) while the burned area grows.
       Crew safety keys off P10 and arrivals move EARLIER: a crowning cell
       only spreads faster.
   (e) CREW-SAFETY CORRIDOR. With the knob on, spread.tiered_eroded_p10
       (cfg.corridor_erosion_tiered) puts every crown-fuel cell in the FAST
       tier (550 m) whatever its fuel class: a crowning timber cell spreads
       at grass-like rates with long flames and torching embers, so the
       slow tier's premise no longer holds. Only ever widens the halo.
   (f) WHAT IT CANNOT DO. No slope term in the crown rate (Cruz's model is
       for level terrain; the surface fire keeps its slope); no plume-
       dominated fires or long-range plume spotting; the surface fire under
       the canopy keeps the engine's unsheltered wind (FlamMap shelters it,
       which with Rothermel's 1991 crown model is the US systems' known
       under-prediction); LANDFIRE's canopy fuels were built for those US
       systems; the 10-m wind is the forecast's, not the in-stand wind.
       Measured (the review): uniform conifer tiles reproduce the Cruz rate
       exactly and run at 8-15 % of the 10-m wind at 18-40 mi/h where the
       canopy shades the litter (the 10 %-of-wind rule +-50 %); the Cruz
       model runs 1.3-5.5 times faster (geometric mean 3.3x) than the
       AVERAGES of the eight northern-Rockies runs Rothermel (1991) fitted
       his model to, 1.7x their observed maxima. With the grass-only floor
       and the calibrated embers the crowning one-click demos' fastest hours
       run at 9-21 % of the wind (7-16 % with spot_ignition_delay_min 10),
       crew audit 0 / 0; on the 11 real fires of the belief-vs-physics
       hindcast, grass-only floor + crown matches the deployed timber floor's
       pooled area ratio and P10 adequacy (3.50x vs 3.55x; 99.78 % both) with
       a worse P10 tail (worst step 82 % vs 94 %).
   Knob OFF = byte-identical arrival rasters, ON byte-identical on a tile
   without canopy or without crown fuel (pinned in tests/test_crown_fire.py
   against tests/test_spread.py's legacy fingerprints with a canopy
   attached, and against the surface solve under every physics knob).

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
    # SAFETY SEMANTICS 20a (2026-10-09, supplemental): path_cell_runs(spec,
    # path) -> (exit distances m, flat cell indices) — every grid cell a
    # polyline passes through, in driving order (the cell-exact corridor law)

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
    # SAFETY SEMANTICS 22 (2026-10-12), crown fire, all pure SI:
    crown_fire(surface_ros_mps, surface_intensity_kwm, hpa_kjm2, wind10_mps,
               effm_pct, cbh_m, cbd_kgm3, ch_m, fmc_pct) -> (ros_mps, intensity_kwm, cfb, kind)
        # kind CROWN_NONE / CROWN_PASSIVE / CROWN_ACTIVE; never lowers ROS or intensity
    crown_initiation_kwm(cbh_m, fmc_pct) -> float                    # Van Wagner 1977
    crown_active_threshold_mps(cbd_kgm3) -> float                    # 3.0 / CBD m/min
    cruz_active_crown_ros_mps(wind10_mps, cbd_kgm3, effm_pct) -> float  # Cruz et al. 2005
    crown_fraction_burned(ros_s, ros_init, ros_active) -> float      # Van Wagner 1993 / FARSITE
    crown_effm_pct(m_dead_frac, cc_pct) -> float                     # Rothermel 1983 shading
    crown_lb(wind10_mps) -> float                                    # Rothermel 1991, capped 8
    foliar_moisture_fbp_pct(lat, lon, elev_m, day_of_year) -> float  # FBP ST-X-3
    torching_spot_max_m(tree_height_m, u20_mps, cover_height_m) -> float  # Albini 1979

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
        # RE-ANCHOR HISTORY (simulator, 2026-09-26): optional kept / kept_wx /
        # embers_aloft / ember_log (also on ensemble_arrival; default None =
        # byte-identical) re-solve a burning fire from its burn with each cell's
        # TRUE arrival (final, quiet), the weather it burned under and the embers
        # still aloft — nothing arrives at or before t0_s; a no-op re-solve is the
        # continuous solve cell for cell. wgresponse.simui's re-anchor uses them.
        # SAFETY SEMANTICS 17 (2026-09-27): optional weather_field (a
        # contracts.WeatherField: one forecast per zone + the per-cell zone
        # index; also on ensemble_arrival; default None = byte-identical)
        # prices each edge with its source cell's zone forecast.
        # SAFETY SEMANTICS 19 (2026-10-09): cfg.spot_fire_size limits each
        # ember to spot_reach_m(intensity, u20, fire ha, cfg) and delays its
        # spot fire; optional spot_trace (a list; default None = nothing
        # recorded, byte-identical) receives one tuple per ember thrown
        # (cell, thrown s, Sardoy mean m, u20, drawn m, reach m, fire cells,
        # landing cell or -1, landing s) — the calibration probe's seam.
        # SAFETY SEMANTICS 21 (2026-10-09): cfg.spread_any_angle adds each
        # burning cell's anchored straight run (traced cell by cell);
        # optional kept_anchor ({kept cell: anchor}) / anchor_log (a dict
        # receiving {cell: anchor}) — also on ensemble_arrival (anchor_log
        # nominal only); default None = byte-identical, inert with the
        # knob off.
        # SAFETY SEMANTICS 22 (2026-10-12): cfg.crown_fire lets crown-fuel
        # cells of a canopy-carrying tile torch or crown; optional
        # crown_trace (a dict; default None = nothing recorded) receives
        # {cell: rothermel.CROWN_* kind} for every crown-fuel cell settled.
    spot_reach_m(intensity_kwm, u20_mps, fire_ha, cfg) -> float
        # SEMANTICS 19 reach (m): Albini's surface-fire maximum spotting
        # distance x (fire_ha / cfg.spot_fire_size_ref_ha) ** cfg.spot_fire_size_exp
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
    # SAFETY SEMANTICS 13 (2026-09-27) FireIncident.meta keys (additive; each
    # set only when the feed carries it): contained_at_s / controlled_at_s /
    # out_at_s (unix s; WFIGS ContainmentDateTime / ControlDateTime /
    # FireOutDateTime + attr_ twins, CAL FIRE ExtinguishedDate), reported_acres
    # + reported_at_s (the RECORD's reported size and its time: WFIGS
    # IncidentSize / attr_IncidentSize, CAL FIRE AcresBurned), perimeter_acres
    # (the POLYGON's own area: WFIGS poly_GISAcres, FIRIS area_acres). Fused
    # groups add containment_by_source / acres_by_source ({source: value} of
    # each source's freshest member) and perimeter_newest_at_s (the newest
    # polygon time over every member); perimeter_acres follows the perimeter
    # owner like perimeter_at_s.
    # SAFETY SEMANTICS 15 (2026-09-27) WFIGS incident-point meta (each set
    # only when the record carries a behaviour report): fire_behavior (str,
    # FireBehaviorGeneral), fire_behavior_descriptors (list[str],
    # FireBehaviorGeneral1-3, non-empty ones in field order),
    # fire_behavior_at_s (the record's ModifiedOnDateTime, unix s).

wgresponse.weather
    class NwsWeather / OpenMeteoWeather / MockWeather   (WeatherProvider protocol)
    # SAFETY SEMANTICS 17: NwsWeather(cfg) with cfg.weather_grid_km > 0 keeps a
    # process-level NwsForecastCache (points -> forecastGridData, static; each
    # gridpoint's decoded series for cfg.weather_cache_ttl_s); off = no cache.

wgresponse.terrain
    class LfpsTerrain / SyntheticTerrain                (TerrainProvider protocol)
    load_ascii_tile(path) / save_ascii_tile(tile, path)  # dependency-free cache format
    # SAFETY SEMANTICS 22 (2026-10-12): TerrainTile.canopy (CanopyLayers) —
    # LfpsTerrain(canopy_layer_list=LfpsTerrain.DEFAULT_CANOPY_LAYERS) also
    # fetches the LANDFIRE canopy (.canopy(bbox, cell_m), its own cache);
    # the ascii format carries four optional canopy bands (a file without
    # them loads with canopy=None); attach_canopy(tile, canopy) and
    # save_ascii_canopy / load_ascii_canopy (the canopy cache file).

wgresponse.clients
    class DynamoClientRegistry / MockClientRegistry     (ClientRegistry protocol)

wgresponse.crews
    class CrewTracker   .ingest(beacon) .crews() .crew(id) .stale(now_s) -> list[str]

wgresponse.alerts
    class AlertEngine   .evaluate(pairs: list[ThreatAssessment], now_s) -> list[AlertEvent]
                        .states() -> dict[tuple[str,str], AlertRecord]
        # TWO WORLDS (16b): evaluate(..., only_incidents=ids) evaluates just
        # those incidents' pairs (every other tracked pair untouched); None =
        # every pair, byte-identical. The TEST world's action re-runs use it.

wgresponse.threat
    class ThreatMonitor .cycle(now_s) -> CycleResult    # full loop: feeds->wx->terrain->spread->etas->alerts
        # W1 SEEDED INCIDENTS (SAFETY SEMANTICS 12): .seeded (the store),
        # .create_seed(...) / .add_seed(ign) / .retire_seed(id, now_s, ...) /
        # .list_seeds() / .restore_seeds(rows); consulted every cycle.
        # SAFETY SEMANTICS 13: contained_signals(inc) -> (signals,
        # contradictions); CycleResult.removed_incidents carries this cycle's
        # RemovedIncident rows ([] unless cfg.drop_contained_incidents).
        # SAFETY SEMANTICS 15: CycleResult.offmap = {incident_id:
        # OffDomainHorizon} for every domain incident ({} unless
        # cfg.corridor_offraster_per_fire), forwarded to dispatch.plan(offmap=).

wgresponse.offmap   (2026-09-27; SAFETY SEMANTICS 15 — the per-fire off-domain horizon)
    status_for(inc, now_s, cfg) -> (known, rapid, reasons)   # the feed's behaviour status
    merge_status(*statuses) -> (known, rapid, reasons)       # OR-combine (tuples or .known/.rapid/.reasons)
    front_speed_stats(p10) -> (p90_mps | None, max_mps | None, n)
    horizon_for(incident_id, p10, status, cfg) -> OffDomainHorizon
    horizons(incidents, ensembles, now_s, cfg, status=None) -> dict[str, OffDomainHorizon]
        # TWO WORLDS (SAFETY SEMANTICS 16): .lock_live() makes this the LIVE
        # world's monitor (seeds refused: seeded.LiveWorldRefused; none ever
        # unioned); .last_snapshot -> WorldSnapshot | None, the read-only
        # record of the last completed cycle the TEST world builds on.

wgresponse.wxfield   (2026-09-27; SAFETY SEMANTICS 17 — local weather across a domain)
    level_spacing_km(grid_km, level) -> float  # x sqrt 2 per level, x2 per two
    lattice_points(spec, cfg) -> (nodes: list[LatLon], spacing_km, level)
    zone_map(spec, points) -> bytes           # nearest point per cell (cached)
    build_field(provider, primary_wx, primary_loc, spec, cfg, horizon_h) -> WeatherField
    field_notes(incident_id, field, cfg) -> list[str]
    field_row(field, now_s) -> dict | None    # the /api/state + world-summary block
    rule_summary(cfg) -> dict

wgresponse.seeded   (W1, 2026-09-26; SAFETY SEMANTICS 12)
    class SeededIncidentStore  .create(...) -> SeededIgnition  .add(ign)
        .retire(id, now_s, reason, by) .active() .entries() .ignition(id)
        .rows() / .restore(rows)   # thread-safe; MAX_ACTIVE cap, bounded retired history
    seeded_forecast(ign, live_provider, loc, horizon_h, now_s) -> WeatherForecast
    parse_seed_request(payload, aoi) -> dict   # ValueError = HTTP 400
    class SeedSafeNotifier(real, drill)        # seeded incidents -> the log, never a client
    class DrillOnlyNotifier(drill)             # SEMANTICS 16e: EVERY send -> the drill log (no channel held)
    class LiveWorldRefused(SeedConflict)       # SEMANTICS 16a: a seed offered to the live world (HTTP 409)

wgresponse.worlds   (TWO WORLDS, 2026-09-27; SAFETY SEMANTICS 16)
    class TestWorld  the TEST map: .attach_live(view) .on_live_cycle(snapshot)
        .run_cycle(trigger) .restore() .adopt_legacy(...) .persist_now()
        .state() .world_summary(since) .seed_list/get/create/retire
        .crews_reset(payload) .crews_place(payload) .reset(payload)
        # every API method -> (http_status, json_payload); sync=True runs
        # inline (tests), else one worker thread that yields to the live loop
    class VirtualCrewTracker   # test crews: clone_live / place / rows; CrewTracker seam
    class TestTerrainOverlay(live, own)   # live tile cache read-only; own namespace writes
    split_legacy_seeds(loaded) -> (live_loaded, LegacySeeds | None)   # the migration partition
    child_s3_uri(uri, "test_world") -> uri | None

wgresponse.fire_belief   (SAFETY SEMANTICS 14, 2026-09-27; docs/FIRE_BELIEF.md)
    class FireBeliefStore(persistent=True)
        .observe(incidents, now_s, cfg) -> list[dict]   # growth obs, windows, resets
        .before_spread(inc, tile, wxf, now_s, cfg) -> member_hour_scale | None
        .after_spread(inc, ens, now_s, cfg, anchor_s, point)   # growth curve
        .statuses(incidents, now_s, cfg) -> {incident_id: FireStatus}
        .to_json() / from_json(d) / copy()   # copy(): persistent=False (drills)
    class BeliefFile(state_dir, s3_uri=None)  .save(store, now_s) .load_into(store)
    status_row(status: FireStatus) -> dict      # the world summary 'belief' row
    # spread seams it drives: compute_arrival(hour_ros_scale=[...]) and
    # ensemble_arrival(member_hour_scale=callable); both None = byte-identical.
    # ThreatMonitor(..., fire_beliefs=store) injects a store (a TEST world's
    # copy); .fire_beliefs exposes it; cli watch --fire-belief[-apply] and
    # --fire-belief-v2 (the v2 rules, SEMANTICS 14 (c)).

wgresponse.feedsanity   (SAFETY SEMANTICS 18, 2026-09-27; docs/review/feed_sanity_2026-09-27.md)
    time_problem(t_s, discovered_s, now_s, cfg) -> str | None   # why an own time is implausible
    feed_area_stands(feed_acres, geom_acres, cfg) -> bool       # the feed's area figure vs the geometry
    collapse_problem(area_acres, reported_acres, cfg) -> str | None
    is_drop(new_m2, ref_m2, cfg) -> bool       # beyond feed_sanity_drop_z x sd of the difference
    is_restamp(new_m2, ref_m2, cfg) -> bool    # within feed_sanity_restamp_frac
    class PerimeterMemory  .get(incident_id, now_s, cfg) .remember(incident_id, RememberedPerimeter)
        .prune(now_s, cfg)   # the monitor's in-process last-plausible-polygon memory
    # consumers: fire_belief.FireBeliefStore.observe (the belief side) and
    # threat.ThreatMonitor._assess_incident (the seed side), both only with
    # cfg.feed_sanity; OperatorEvent kind 'feed_sanity'; cli watch --feed-sanity.

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
        # SAFETY SEMANTICS 15: optional offmap={incident_id: OffDomainHorizon
        # | (ros_mps, margin_min, reason)} — that incident's fail-closed bound
        # extrapolates at ros_mps with margin max(leg margin, margin_min);
        # None / a missing incident = the legacy flat law, byte-identical.
        # FIRST-STEP PERF (2026-10-12; docs/SIMULATOR_DESIGN.md "The slow
        # first step"): optional leg_memo=dispatch.LegMemo() shared across
        # calls (each exact leg's road route, travel time and corridor T*,
        # bound to the cfg / eroded rasters / zones it was computed under) —
        # PERFORMANCE ONLY, plans are byte-identical with or without it.
        # cfg.dispatch_ils_skip_all_served ('homes' objective; default False
        # = byte-identical): no ILS when the best plan already serves every
        # job in the pool — its shakes search for MORE protected homes and
        # could only re-polish tie-break terms (freshness, drive, spread).

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
    W1: /api/seed routes (GET/POST/DELETE) to the injected app.seed_api (the
    watch daemon); 404 when no seed API is wired.
    TWO WORLDS (SAFETY SEMANTICS 16): /api/test/state, /api/test/seed[/<id>]
    (GET/POST/DELETE), POST /api/test/crews/reset, /api/test/crews/place,
    /api/test/reset route to the injected app.test_api (404 when unwired);
    GET /api/world/summary?world=test the test map's read model (default
    live, unchanged). /api/seed keeps GET as a read-only alias of the TEST
    list (one release); its POST / DELETE answer 409 (the live world refuses).

wgresponse.persist   (Sprint 2, Council-1 OPS-4)
    class StateStore(dir_path)  .save(alert_records, notified_ledger, incumbent_plan, meta)
                                .load() -> restored dicts (or None when absent/corrupt: loud note)
    # Alert/IMPACTED/notification state survives restart; restart re-fires NOTHING.
    # AlertEngine exposes .restore(records) and .notified_ledger() seams for this.
    # W1: save(..., seeded_incidents=rows) writes an ADDITIVE key only when
    # given (no seeds = byte-identical file); load()["seeded_incidents"] ([]
    # when absent) feeds ThreatMonitor.restore_seeds (SAFETY SEMANTICS 12h).
    # TWO WORLDS (16f): save(..., virtual_crews=rows, drill_log=rows) —
    # ADDITIVE keys only the TEST world writes (in its own namespace:
    # <state-dir>/test_world/); load() returns them ([] when absent). Since
    # 13 the LIVE world never writes seeded_incidents; a live file that
    # carries it (pre-two-worlds build) is migrated to the test world.

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
    # W1 (SAFETY SEMANTICS 12): POST /api/seed {lat, lon, intensity?,
    # wind_dir_deg?, weather?, note?, created_by?} -> 201 {incident_id};
    # GET /api/seed[/<id>]; DELETE /api/seed/<id> (retire) — authed like
    # every write path; seeds persist and survive restart.
    # SAFETY SEMANTICS 13: --drop-contained arms drop_contained_incidents
    # (/api/state gains removed_contained + removed_incidents),
    # --confirmed-perimeters arms perimeter_confirmed_anchor.
    # SAFETY SEMANTICS 15: --offmap-per-fire arms corridor_offraster_per_fire
    # with corridor_offraster_margin_min = 60 (the founder's hour); /api/state
    # incidents and the world summary rows gain an "offmap" block.
    # TWO WORLDS (SAFETY SEMANTICS 16): the same body on POST /api/test/seed
    # (the TEST world); /api/seed POST/DELETE -> 409 (the LIVE world refuses),
    # GET /api/seed[/<id>] a read-only alias of the test list for one release;
    # GET /api/test/state, POST /api/test/crews/reset | /api/test/crews/place
    # {crew_id, lat, lon, status?, name?} | /api/test/reset;
    # /api/world/summary?world=live|test (default live).
    # SAFETY SEMANTICS 17: --weather-grid-km KM arms weather_grid_km
    # (--weather-points-max N sets weather_points_max); /api/state gains
    # weather_grid + a "weather" block per incident; so do the summary rows.
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
    SEEDED = "seeded"         # W1: a fire a user lit in the live world (SAFETY SEMANTICS 12) — never a feed


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
# Seeded incidents (W1 — docs/LIVE_WORLD_PLAN.md ticket 1.2; SAFETY SEMANTICS 12)
# ---------------------------------------------------------------------------

#: Every seeded incident id lives in this namespace ('seeded:<uuid>'). No feed
#: adapter mints it (IRWIN GUIDs, wfigs:/firis:/calfire:/firms: ids, mock-*),
#: so a seeded fire can never collide with — or fuse into — a feed incident.
SEEDED_ID_PREFIX = "seeded:"

#: The simulator's intensity presets, by name. wgresponse.simui.
#: INTENSITY_WEATHER owns the numbers (sustained mph, RH, temperature); the
#: names are pinned equal to its keys by tests/test_seeded_incidents.py.
SEED_INTENSITIES = ("low", "moderate", "high", "extreme")

_SEED_ID_MAX = 80
_SEED_WHO_MAX = 64
_SEED_NOTE_MAX = 280


def is_seeded_id(incident_id: object) -> bool:
    """True for an id in the seeded namespace (SEEDED_ID_PREFIX + a tail)."""
    return (isinstance(incident_id, str)
            and incident_id.startswith(SEEDED_ID_PREFIX)
            and len(incident_id) > len(SEEDED_ID_PREFIX))


def _finite_number(v: object) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


@dataclass(frozen=True)
class SeedWeather:
    """EXPLICIT constant fire weather for a seeded ignition — the MockWeather-
    style sample: every forecast hour carries these values (SI at the seam:
    10-m sustained + gust m/s, RH percent, Celsius). No direction here:
    SeededIgnition.wind_dir_deg fixes it, else the live provider's hourly
    direction applies (SAFETY SEMANTICS 12f)."""
    wind_speed_mps: float
    wind_gust_mps: float
    rh_pct: float
    temp_c: float

    def problems(self) -> "list[str]":
        out = [f"weather.{n} must be a finite number"
               for n in ("wind_speed_mps", "wind_gust_mps", "rh_pct", "temp_c")
               if not _finite_number(getattr(self, n))]
        if out:
            return out
        if not 0.0 <= self.wind_speed_mps <= 60.0:
            out.append("weather.wind_speed_mps must be within [0, 60] m/s")
        if not self.wind_speed_mps <= self.wind_gust_mps <= 90.0:
            out.append("weather.wind_gust_mps must be within "
                       "[wind_speed_mps, 90] m/s")
        if not 1.0 <= self.rh_pct <= 100.0:
            out.append("weather.rh_pct must be within [1, 100] %")
        if not -40.0 <= self.temp_c <= 60.0:
            out.append("weather.temp_c must be within [-40, 60] C")
        return out


@dataclass(frozen=True)
class SeededIgnition:
    """One fire a user lit at a point (W1 ticket 1.2; SAFETY SEMANTICS 12 —
    since 16, in the TEST world only). The watch daemon's POST
    /api/test/seed creates one (wgresponse.seeded mints the id); the TEST
    world's ThreatMonitor's seeded store holds it
    and every cycle materializes it with to_incident(now_s) next to the feed
    results — from then on it is an ordinary incident to the engine.

    Weather (12f): `intensity` (a SEED_INTENSITIES simulator preset) OR
    `weather` (an explicit SeedWeather) replaces the CONDITIONS — sustained
    wind, gust, RH, temperature — in every forecast hour; `wind_dir_deg`
    (meteorological FROM, [0, 360)) fixes the DIRECTION in every hour. Each
    part left unset comes from the LIVE provider at the fire (NWS gridpoint,
    as for every incident); with both set the forecast is fully synthetic —
    the simulator's MockWeather script, no network (weather_mode()).
    t_ignite_s is when it was lit (unix s): the auto-retire clock (12e) and
    the incident's discovered_at_s — never a spread t0 (12b)."""
    incident_id: str
    loc: LatLon
    t_ignite_s: float
    intensity: Optional[str] = None
    weather: Optional[SeedWeather] = None
    wind_dir_deg: Optional[float] = None
    created_by: str = ""
    note: str = ""

    def problems(self) -> "list[str]":
        """Human-readable validation problems (empty = valid)."""
        out = []
        if not is_seeded_id(self.incident_id):
            out.append(f"incident_id must start with {SEEDED_ID_PREFIX!r} "
                       f"(the seeded namespace)")
        elif (len(self.incident_id) > _SEED_ID_MAX
              or any(ch.isspace() or ch in "/?#%" for ch in self.incident_id)):
            out.append(f"incident_id must be <= {_SEED_ID_MAX} chars with no "
                       f"whitespace or '/?#%'")
        if not (isinstance(self.loc, LatLon) and _finite_number(self.loc.lat)
                and _finite_number(self.loc.lon)):
            out.append("loc must be a LatLon with finite lat/lon")
        elif not (-90.0 <= self.loc.lat <= 90.0
                  and -180.0 <= self.loc.lon <= 180.0):
            out.append("loc out of range (lat ±90, lon ±180)")
        if not (_finite_number(self.t_ignite_s) and self.t_ignite_s > 0):
            out.append("t_ignite_s must be a finite unix time > 0")
        if self.intensity is not None and self.intensity not in SEED_INTENSITIES:
            out.append(f"intensity must be one of {list(SEED_INTENSITIES)} "
                       f"(or omitted for live conditions)")
        if self.weather is not None:
            if not isinstance(self.weather, SeedWeather):
                out.append("weather must be a SeedWeather")
            else:
                out.extend(self.weather.problems())
            if self.intensity is not None:
                out.append("intensity and weather are exclusive (each sets "
                           "the conditions)")
        if self.wind_dir_deg is not None and not (
                _finite_number(self.wind_dir_deg)
                and 0.0 <= self.wind_dir_deg < 360.0):
            out.append("wind_dir_deg must be a finite bearing in [0, 360)")
        if not isinstance(self.created_by, str) or len(self.created_by) > _SEED_WHO_MAX:
            out.append(f"created_by must be a string <= {_SEED_WHO_MAX} chars")
        if not isinstance(self.note, str) or len(self.note) > _SEED_NOTE_MAX:
            out.append(f"note must be a string <= {_SEED_NOTE_MAX} chars")
        return out

    def weather_mode(self) -> str:
        """Which weather drives this fire (SAFETY SEMANTICS 12f):
        'live' | 'live+fixed-dir' (live conditions, direction forced) |
        'preset' / 'explicit' (fully synthetic, no network) |
        'preset+live-dir' / 'explicit+live-dir' (synthetic conditions, the
        live provider's hourly direction)."""
        cond = ("preset" if self.intensity is not None else
                "explicit" if self.weather is not None else "live")
        fixed = self.wind_dir_deg is not None
        if cond == "live":
            return "live+fixed-dir" if fixed else "live"
        return cond if fixed else cond + "+live-dir"

    def display_name(self) -> str:
        """Incident name: loud 'SEEDED' + the id's first 8 chars (+ note)."""
        name = f"SEEDED fire {self.incident_id[len(SEEDED_ID_PREFIX):][:8]}"
        if self.note:
            name += " — " + self.note[:60]
        return name

    def summary(self) -> dict:
        """JSON-able description (incident meta, /api/state, the API)."""
        w = self.weather
        return {
            "incident_id": self.incident_id,
            "lat": self.loc.lat, "lon": self.loc.lon,
            "t_ignite_s": self.t_ignite_s,
            "intensity": self.intensity,
            "weather": (None if w is None else
                        {"wind_speed_mps": w.wind_speed_mps,
                         "wind_gust_mps": w.wind_gust_mps,
                         "rh_pct": w.rh_pct, "temp_c": w.temp_c}),
            "wind_dir_deg": self.wind_dir_deg,
            "weather_mode": self.weather_mode(),
            "created_by": self.created_by,
            "note": self.note,
        }

    def to_incident(self, now_s: float) -> FireIncident:
        """The cycle's FireIncident for this seed (SAFETY SEMANTICS 12b/c):
        a POINT incident — origin = the ignition, no perimeter, no hotspots,
        acreage 0, containment unknown — whose observed geometry stays the
        point (the watcher has no ground truth for live fires; its forecast is
        the ensemble, spread from the point at now_s like any feed
        point-incident). updated_at_s = now_s: the seed store vouches for it
        every cycle, so the staleness gate keeps it until it is retired.
        No IRWIN id; source_ids carries the seeded provenance only."""
        return FireIncident(
            incident_id=self.incident_id,
            name=self.display_name(),
            source=IncidentSource.SEEDED,
            origin=self.loc,
            updated_at_s=float(now_s),
            discovered_at_s=float(self.t_ignite_s),
            acreage=0.0,
            containment_pct=None,
            active=True,
            perimeter=None,
            hotspots=None,
            irwin_id=None,
            source_ids={"seeded": self.incident_id},
            meta={"seed": self.summary()},
        )


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


@dataclass(frozen=True)
class WeatherPoint:
    """One forecast point of a WeatherField (SAFETY SEMANTICS 17). role:
    'primary' (the fire centroid, zone 0) | 'lattice' (a global-lattice
    node). ok: False when this point's own fetch failed and its zone prices
    the primary forecast instead (error = the provider's message). source:
    the forecast's source tag ('nws', ...; the primary's when ok is False).
    cells: domain cells in its zone."""
    lat: float
    lon: float
    role: str
    ok: bool = True
    source: str = ""
    error: str = ""
    cells: int = 0


@dataclass
class WeatherField:
    """LOCAL WEATHER across one spread domain (SAFETY SEMANTICS 17;
    cfg.weather_grid_km). forecasts[z] is zone z's hourly forecast (zone 0 =
    the primary, the fire-centroid forecast; a failed secondary point carries
    the primary's forecast); zone_of[i] is the zone of flat cell i (row-major
    on `spec`, len nrows*ncols; bytes, so <= 256 zones); points[z] describes
    zone z's point. spacing_km / level: the lattice spacing used (after
    coarsening) and its level (spacing = wxfield.level_spacing_km(
    cfg.weather_grid_km, level)).
    spread.compute_arrival(weather_field=...) prices every edge with its
    source cell's zone forecast."""
    spec: GridSpec
    forecasts: list                    # list[WeatherForecast], index = zone
    zone_of: bytes
    points: list                       # list[WeatherPoint], index = zone
    spacing_km: float = 0.0
    level: int = 0


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
class CanopyLayers:
    """LANDFIRE forest canopy layers on a TerrainTile's grid (SAFETY SEMANTICS
    22, the crown-fire model). PHYSICAL units at the seam (terrain.py converts
    LANDFIRE's integer encodings: CBH and CH metres x 10, CBD kg/m^3 x 100, CC
    percent). A cell with no forest canopy (LANDFIRE non-forest, nodata) holds
    0.0 in every layer; spread.py lets a cell crown only where cbd_kgm3 > 0
    and ch_m > cbh_m."""
    cbh_m: Raster                      # canopy base height, m
    cbd_kgm3: Raster                   # canopy bulk density, kg/m^3
    cc_pct: Raster                     # canopy cover, percent 0-100
    ch_m: Raster                       # canopy (stand) height, m
    source: str = ""                   # provenance, e.g. "lfps:LF2024_CBH;..."


@dataclass
class TerrainTile:
    """Aligned rasters on one GridSpec. fuel holds FBFM40 integer codes as floats.

    canopy (SAFETY SEMANTICS 22, 2026-10-12): the optional LANDFIRE canopy
    layers on the same grid. None (every tile built before the crown-fire
    model, and every tile whose provider fetched no canopy) = no crown fire
    anywhere: the spread is exactly the surface model, whatever the config."""
    spec: GridSpec
    fuel: Raster
    slope_deg: Raster
    aspect_deg: Raster                 # downslope-facing compass direction
    elev_m: Raster
    source: str = "synthetic"
    canopy: Optional[CanopyLayers] = None


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


@dataclass(frozen=True)
class OffDomainHorizon:
    """One fire's OFF-DOMAIN HORIZON for the fail-closed corridor bound
    (SAFETY SEMANTICS 15; cfg.corridor_offraster_per_fire). Built by
    wgresponse.offmap per domain incident each cycle, carried on
    CycleResult.offmap and handed to dispatch.plan(offmap=...): a corridor
    sample off this fire's raster (or UNREACHED inside it) gets the arrival
    min over its reached boundary runs of t_run + distance / ros_mps and is
    unsafe when that arrival - pass_time < max(the leg's margin, margin_min).

    kind: 'rapid' | 'unknown' (both at the ceiling) | 'per_fire' (the P90
    front speed, inside [floor, ceiling]) | 'floor' (front slower than the
    floor) | 'ceiling_front' (front faster than the ceiling) | 'no_front'
    (known slow, but the raw front never crosses the edge: the ceiling) |
    'no_bound' (the forecast stays well inside its domain: nothing is
    extrapolated, legacy). reason: the human line ('ceiling: rapid', 'per-
    fire: P90 front speed 1.4 km/h', ...). known / rapid / status_reasons:
    the behaviour status that chose the kind. front_p90_mps / front_max_mps /
    front_n: the raw-P10 boundary front statistics (None / 0 when no cell
    crosses the edge)."""
    incident_id: str
    ros_mps: float
    margin_min: float
    reason: str
    kind: str
    known: bool = False
    rapid: bool = False
    status_reasons: tuple = ()
    front_p90_mps: Optional[float] = None
    front_max_mps: Optional[float] = None
    front_n: int = 0


# ---------------------------------------------------------------------------
# Orchestration outputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RemovedIncident:
    """One feed incident removed from a cycle's incident set as CONTAINED AND
    NO LONGER SPREADING (SAFETY SEMANTICS 13a; cfg.drop_contained_incidents).
    `incident` is the fused record exactly as the feeds returned it (never
    assessed); `signals` the feed declarations that removed it (e.g. 'wfigs
    reports 100% contained', 'declared out 3.0 h ago'); `reason` their
    human join; `removed_since_s` when the monitor first removed it (unix s;
    in-process memory, reset by a restart)."""
    incident: FireIncident
    reason: str
    signals: tuple = ()
    removed_since_s: float = 0.0


#: FireStatus.state values (SAFETY SEMANTICS 14): 'prior' = no usable
#: evidence (full physics), 'learned' = the posterior from growth windows,
#: 'reset' = a surprise reset is active (full physics, sticky).
FIRE_BELIEF_STATES = ("prior", "learned", "reset")


@dataclass(frozen=True)
class FireObservation:
    """The latest completed growth window of one fire (SAFETY SEMANTICS 14),
    for display: its end time (unix s), the fire's size then (acres), the
    series it came from ('perimeter' | 'reported'), the observed growth over
    the window (acres) and the belief's median predicted growth for that
    window made BEFORE it was seen (acres; None when the window could not
    be modeled — no growth curve was pinned to its start)."""
    t_s: float
    acres: float
    kind: str
    growth_acres: Optional[float] = None
    predicted_growth_acres: Optional[float] = None


@dataclass(frozen=True)
class FireStatus:
    """Per-fire spread-rate belief status (SAFETY SEMANTICS 14) — the
    interface the off-map road-crossing rule consumes. Published on
    CycleResult.fire_status for every active incident only with
    cfg.fire_belief_track.

    known        at least one completed growth observation (window) exists
    rapid        the fire is growing fast (thresholds in ResponseConfig
                 fire_belief_rapid_*) or a surprise reset is active
    reasons      human-readable why (rapid / reset / state / weather)
    mult_p10/p50/p90  posterior quantiles of the whole-fire head-ROS
                 multiplier vs the engine's physics; None = the prior applies
                 (state 'prior' or 'reset')
    n_obs        growth windows in the posterior
    novel_from_s unix s of the first NEW-weather-pattern forecast hour this
                 cycle (the prior applies from then on); None = no new pattern
                 within the horizon, or no forecast consulted this cycle (the
                 incident built no domain — reasons say which)
    state        one of FIRE_BELIEF_STATES
    last_obs     the latest completed window (FireObservation) or None"""
    incident_id: str
    known: bool
    rapid: bool
    reasons: tuple = ()
    mult_p10: Optional[float] = None
    mult_p50: Optional[float] = None
    mult_p90: Optional[float] = None
    n_obs: int = 0
    novel_from_s: Optional[float] = None
    state: str = "prior"
    last_obs: Optional[FireObservation] = None


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
    removed_incidents: list = field(default_factory=list)  # list[RemovedIncident]: contained fires removed this cycle (SAFETY SEMANTICS 13; [] unless cfg.drop_contained_incidents)
    offmap: dict = field(default_factory=dict)  # incident_id -> OffDomainHorizon per domain incident (SAFETY SEMANTICS 15; {} unless cfg.corridor_offraster_per_fire)
    fire_status: dict = field(default_factory=dict)  # incident_id -> FireStatus (SAFETY SEMANTICS 14; populated only with cfg.fire_belief_track)
    weather_fields: dict = field(default_factory=dict)  # incident_id -> WeatherField per domain incident that spread on one (SAFETY SEMANTICS 17; {} unless cfg.weather_grid_km > 0)


@dataclass(frozen=True)
class WorldSnapshot:
    """TWO WORLDS (SAFETY SEMANTICS 16): the READ-ONLY record of one completed
    LIVE cycle that the TEST world builds on — ThreatMonitor.last_snapshot.

    Everything the live cycle decided before its alert ladder ran, per
    incident: the post-gate incident set, every ThreatAssessment, the
    ensembles (the immutable arrival rasters, SHARED by reference — the test
    world never recomputes a live domain), the in-domain clients, the ETAs,
    the fire-centroid forecasts, the tiered eroded rasters (when that knob is
    on), the extreme-regime flags computed this cycle, the registry roster
    each incident's search returned (SEMANTICS 6 registry completeness), the
    incidents whose assessment FAILED, the feed health and the cycle's notes
    and feed-blind operator events. Containers are fresh shallow copies; the
    objects inside are the live cycle's own and are never mutated by a
    reader. Times unix seconds."""
    t_s: float
    incidents: tuple = ()                 # FireIncident, the live cycle's order
    assessments: tuple = ()               # ThreatAssessment
    ensembles: dict = field(default_factory=dict)    # incident_id -> EnsembleArrival
    clients: dict = field(default_factory=dict)      # client_id -> ClientProperty (in a domain)
    etas: dict = field(default_factory=dict)         # (client_id, incident_id) -> ThreatEta
    forecasts: dict = field(default_factory=dict)    # incident_id -> WeatherForecast
    eroded: dict = field(default_factory=dict)       # incident_id -> Raster (tiered erosion)
    extreme_flags: dict = field(default_factory=dict)  # incident_id -> bool (computed this cycle)
    roster_seen: dict = field(default_factory=dict)  # incident_id -> frozenset(client_id)
    failed_incidents: frozenset = frozenset()
    feeds_healthy: bool = True
    notes: tuple = ()
    feed_events: tuple = ()               # the cycle's 'feed_blind' OperatorEvents


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
    # OFF-DOMAIN HORIZON PER FIRE (founder rule 2026-09-27, verbatim: "the
    # cap at extrapolation should include the rate of fire spread vs. drive
    # time through the region. the pathway only becomes unaccessible when
    # the crossing time between fire and road is less than 1 hour unless the
    # fire has shown rapid rate of spread"; SAFETY SEMANTICS 15). Measured
    # stake: the flat 1.7 m/s bound above let the 80 %-contained Willow fire
    # (WFIGS "Minimal | Smoldering, Creeping, Smoldering") veto a seeded job
    # ~69 km away. When True, each fire's bound extrapolates at ITS OWN
    # forecast front speed where it crosses its domain edge (P90 over the
    # raw P10's boundary cells, clamped to [corridor_offraster_ros_floor_mps,
    # corridor_offraster_ros_mps]) — only when a fresh feed behaviour report
    # says the fire is known and not rapid; rapid and unknown fires keep the
    # 1.7 m/s ceiling (unknown = rapid: a fire that has not shown it is slow
    # is not trusted to be). This LOOSENS the law for known-slow fires (an
    # unforecast run beyond the domain is extrapolated late — measured in
    # docs/LIVE_WORLD_PLAN.md). Changes only the fail-closed bound, so it is
    # inert without corridor_fail_closed / corridor_unreached_bound. False =
    # legacy, byte-identical; watch --offmap-per-fire (+ the 60-min margin).
    corridor_offraster_per_fire: bool = False
    # ... its FLOOR (m/s, ~1 km/h): a known-slow fire never extrapolates
    # slower than this, whatever its forecast front says. Read only with
    # corridor_offraster_per_fire.
    corridor_offraster_ros_floor_mps: float = 0.28
    # ... the founder's CROSSING-TIME margin (minutes): a corridor sample
    # whose arrival came from the extrapolated bound (off-raster or
    # unreached) is unsafe when bound - pass_time < max(the leg's own margin,
    # this); samples inside the forecast keep their calibrated margins.
    # 0 = the leg's own margin (legacy); --offmap-per-fire sets 60. Read only
    # with corridor_offraster_per_fire.
    corridor_offraster_margin_min: float = 0.0
    # ... a WFIGS fire-behaviour report (FireBehaviorGeneral / 1-3) older
    # than this many hours — by its record's ModifiedOnDateTime, the only
    # time the feed gives — is STALE: behaviour unknown, the ceiling applies.
    # Read only with corridor_offraster_per_fire.
    fire_behavior_max_age_h: float = 48.0
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
    # SEM-10d ROAD-NETWORK ESCAPE (founder "rework the crews fleeing",
    # 2026-09-26; docs/review/escape_roads_2026-09-26.md). The ray escape and
    # its road snap both score their proposals by (transit exposure, drive
    # time) among lawful destinations and never look at how long the
    # destination stays safe: on the pinned Camp-Fire-scale extended run the
    # raw rays won by a 4 s drive-time tie (an infinite-horizon ray end over
    # a 26.6 h one) and the snap walked back to nodes with 10-27 h horizons,
    # so the crews were overtaken, re-fled from worse spots and caught (two
    # imminent-while-egressing rows). When True and a road graph is loaded,
    # a crew that must flee with no catalog zone lawfully reachable (the
    # zone-first rule is unchanged) SEARCHES THE ROAD GRAPH from its
    # position: a time-dependent Dijkstra at the crew's escape speed where an
    # edge is impassable if any sample is BURNED at its pass time (inside the
    # cold-black window) and costs EXPOSURE seconds for samples inside the
    # eroded-P10 imminent band. Candidate destinations are road NODES
    # >= 2 km out, chosen lexicographically by (exposure in 5-minute steps —
    # zero exposure always first — then the node's SAFETY HORIZON: eroded-
    # P10 arrival minus arrival, capped so a long-safe spot beats a marginal
    # one without sending crews to the far edge — then drive time); a lawful
    # destination (horizon >= egress buffer + lead) always beats an unlawful
    # one. The catalog zones compete under the same key and win ties on the
    # safety steps (dispatch stages crews at zones). The crew then drives
    # EXACTLY the searched path (roads end to end). No passable road escape
    # (cornered, or off the network) -> the legacy ray logic, with a loud
    # note. False = off, byte-identical. The SIMULATOR arms it (explicit
    # override wins). When neither SEM-10e zone rule yields a move, the
    # proactive decamp also takes a fully lawful road path to a durable
    # road node — a crew parked at a road point after an area escape
    # otherwise waited for the imminent law and re-fled through the band.
    escape_road_network: bool = False
    # ... its horizon cap (hours): a destination whose safety horizon reaches
    # this counts as fully safe (beyond it the nearer node wins). Measured on
    # the pinned Camp-Fire-scale run (escape_roads_2026-09-26.md): 6 h -> 1
    # imminent row, 9 relocations; 12 h -> 0 rows, 7 relocations; 24 h -> 0,
    # 4; 48 h -> 0, 3 — short caps park crews where the growing band
    # overtakes them and each re-flee starts from a worse pocket. Read only
    # when escape_road_network is on.
    escape_road_horizon_cap_h: float = 24.0
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
    # Since 2026-10-09 crew_egress_judged (SAFETY SEMANTICS 20b-iii) applies
    # the same rule on EVERY tile: the standard Eagle graph offered one (an
    # extreme city-weather decamp rode 915 s of exposed transit).
    escape_proactive_lawful_only: bool = False
    # TRANSIT-HAZARD STAGE 1 (founder "perform stage 1" 2026-07-30;
    # docs/transit_hazard_determination.md): per-fuel-class cold-black
    # windows replace the flat 45-min carve-out in SEM-10 ROUTE SELECTION
    # (fuels.cold_black_minutes: grass 5-10 min, grass-shrub 10-20, shrub
    # 20-30, timber-understory 45, timber-litter 60, slash 90 — grass opens,
    # timber stricter). The 10c audit consults these windows only under
    # old_black_refuge (below: the window is what makes black OLD); with
    # that knob off it never does. False = flat 45 min, byte-identical.
    transit_cold_black_by_class: bool = False
    # OLD BLACK IS A REFUGE (founder ruling 2026-09-26: "old burned ground
    # does not count as a violation"; SAFETY SEMANTICS 10, OLD BLACK). When
    # True, a cell whose nominal arrival is at least its cold-black window
    # (the transit window above) in the past is a REFUGE in the simulator's
    # crew-safety law: the 10c audit counts no burned / imminent row for a
    # crew standing on it, the sweep never relocates a crew off it, a zone or
    # escape destination on it is lawful with an unbounded horizon, and route
    # / exposure metrics charge its samples nothing. Hot black (inside its
    # window) is unchanged — counted, fled, impassable. The live engine has
    # no crew-position audit, so only the simulator reads it. False = the
    # strict audit (any front-passed cell counts), byte-identical. The
    # SIMULATOR arms it (explicit override wins).
    old_black_refuge: bool = False
    # A CREW THAT LEAVES THE MAP IS OUT (founder ruling 2026-09-26: "crews
    # once leave a map are no longer part of the simulation"; SAFETY
    # SEMANTICS 10, LEFT THE MAP). When True, a crew off the simulated
    # terrain tile and away from every catalog zone (simui.ZONE_ON_MAP_M)
    # with nothing that brings it back — stopped there, or on a final leg
    # that ends there — is removed from the simulation for good: no
    # dispatch, no movement, no sweep, no audit rows; its in-flight
    # commitments are released and it is reported (state crews[i].left_map /
    # left_map_at_s, scoreboard.crews_left_map, a note). Zones just past the
    # tile edge are part of the map; transit through off-tile road toward
    # the map is not leaving. False = legacy (a crew off the tile stays in
    # play, unaudited there), byte-identical. The SIMULATOR arms it.
    left_map_out: bool = False
    # ... and its SEM-10d TIE-BREAK: among equally safe escape destinations
    # (same lawful / exposure-step / capped-horizon step) one ON the map
    # ranks before one OFF it (then catalog zone before road node, then
    # drive time, as before). An off-tile road node has an unbounded fire
    # horizon, so it ties with every long-safe on-map refuge; leaving would
    # take the crew out of the simulation. In an emergency escape a strictly
    # safer off-map node still wins; in the VOLUNTARY SEM-10e road decamp
    # (candidates all lawful, exposure-free, durable) every on-map refuge
    # ranks before every off-map one. Read only with left_map_out. False =
    # the legacy order, byte-identical. The SIMULATOR arms it.
    escape_prefer_on_map: bool = False
    # NO CREW DRIVES A LEG THE LAW HAS NOT JUDGED (2026-10-09, the crew-
    # overrun gap the simulator's city weather exposed; SAFETY SEMANTICS 20;
    # docs/SIMULATOR_DESIGN.md "No crew drives an unjudged leg"). Two knobs.
    # CELL-EXACT CORRIDOR (20a): the corridor law samples a leg every
    # corridor_sample_m (300 m) — on a 90 m grid one cell in three, and a
    # leg that clips a cell between two samples is never judged there
    # (measured: a chord 467 m from a cell burning since the ignition,
    # inside its 550 m eroded band, read 9 h clear). When True every raster
    # cell a leg passes through is ALSO a sample, judged at the latest
    # moment the crew is in it — in safety.LegOracle (dispatch's ingress /
    # egress law; the live engine's too if armed) and in the simulator's
    # SEM-10 route judgement (relocation / escape / decamp choices, the
    # road-network escape search). The legacy samples all stay. False =
    # byte-identical. The SIMULATOR arms it; the live watcher does not (the
    # founder decides).
    corridor_cell_exact: bool = False
    # EVERY LEG A CREW STARTS IS JUDGED (20b, read only by the simulator —
    # the live engine moves no crew). Measured before the rule (one-click
    # sweep, 208 runs): 6 overrun rows from a crew withdrawn at an early
    # stop driving to its route's adopted zone — the LAST stop's, whose road
    # from THIS stop no law had judged (dispatch validates each stop's own
    # nearest lawful zone and emits only the last one) — 3 from an egress
    # whose zone the pessimistic front reached inside the buffer before the
    # next audit (the 10b sweep checked the zone's own cell at zero lead),
    # 1 from a voluntary SEM-10e decamp over exposed transit. When True: the
    # moment a crew turns onto an egress leg from a stop (a withdrawal, or
    # leaving its last stop) that leg is judged from where the crew is
    # (every sample's eroded P10 >= pass + egress buffer, nothing burned,
    # the zone clear one step beyond arrival) and the SEM-10d routed choice
    # replaces it when it fails; the sweep re-judges every egress in flight
    # the same way; a voluntary decamp never rides exposed transit (the
    # escape_proactive_lawful_only rule, on every tile). False = byte-
    # identical. The SIMULATOR arms it.
    crew_egress_judged: bool = False
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
    # EMBERS FLY AS FAR AS THE FIRE THAT THROWS THEM (founder 2026-10-09:
    # "begin the calibration of the ember distance and size of fire
    # calibration"; SAFETY SEMANTICS 19; docs/review/ember_spotting_
    # calibration_2026-10-09.md). Measured: the legacy spotting (above) lets a
    # fire of a few acres leap-frog kilometres in minutes — every burning cell
    # throws from the full Sardoy lognormal from the first minute, and a spot-
    # ignited cell throws again the moment it burns (Park City one-click: a
    # 6-hop spot-from-spot chain covered 3.2 km in ~7 min). When True (with
    # spotting_enabled) an ember flies at most Albini's surface-fire maximum
    # spotting distance for its cell's intensity and 20-ft wind, scaled by
    # (size of the connected fire the cell joined / spot_fire_size_ref_ha) **
    # spot_fire_size_exp — a spot fire is a new, small fire until it joins
    # another; a longer draw burns out in flight — and a spot fire starts
    # spot_ignition_delay_min after its ember lands. False = the legacy
    # spotting, byte-identical. NOT armed by the simulator (founder decision);
    # the three parameters below are read only when it is on and default to
    # the calibration's recommended values.
    spot_fire_size: bool = False
    # ... the fire size (ha) at which an ember's reach equals Albini's
    # surface-fire maximum (smaller fires reach less, larger more).
    spot_fire_size_ref_ha: float = 300.0
    # ... the size exponent: reach ~ fire area ** exp (Storey et al. 2020,
    # source-fire area vs maximum spotting distance: ~4 km at ~1000 ha, ~8 km
    # at ~5000 ha => 0.43).
    spot_fire_size_exp: float = 0.43
    # ... minutes from an ember landing to its spot fire spreading (Alexander
    # & Cruz 2014: up to 5-10 min; ELMFIRE cell ignition delay 100 s).
    spot_ignition_delay_min: float = 5.0
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
    # ANY-ANGLE SPREAD (grid-orientation review 2026-10-09, the wind-area
    # inversion probe; SAFETY SEMANTICS 21; docs/review/grid_orientation_
    # 2026-10-09.md). Legacy: the 16-neighbour lattice moves along 16
    # directions only, so on a uniform tile the fire is the 16-sided polygon
    # inscribed in its own ellipse — at the length-to-breadth cap of 8 the
    # head ran at 22-28 % of its rate whenever the wind lay between two
    # lattice directions and the area was 16-45 % of the ellipse, FALLING as
    # the wind rose (GR2 6 h: 2,110 ac at 20 mph, 1,302 at 25). When ON,
    # every burning cell also offers its neighbours the straight run from
    # its anchor, traced cell by cell at each crossed cell's own ellipse
    # (spread._segment_arrival): uniform tiles get the analytic ellipse at
    # any wind angle; real tiles move toward the per-cell continuum. Lattice
    # edges unchanged; never later than legacy under constant weather.
    # Default OFF = byte-identical; the simulator A/B and the hindcast decide
    # the flips (watch --any-angle arms it live).
    spread_any_angle: bool = False
    # CROWN FIRE (founder 2026-10-12, option 3 of the Park City speed review:
    # "build a crown-fire model"; SAFETY SEMANTICS 22; docs/review/crown_
    # fire_model_2026-10-12.md). The engine models surface fire only, so a
    # timber fire crawls at its litter's rate (Park City with the floor on
    # grass only: 50 acres in 6 h) while real wind-driven conifer fires run
    # as crown fires at ~10 % of the 10-m wind. When True, on a tile that
    # carries LANDFIRE canopy layers (TerrainTile.canopy), each forest cell's
    # surface fire may CROWN: Van Wagner (1977) initiation (the cell's Byram
    # intensity >= (0.010 CBH (460 + 25.9 FMC))^1.5 kW/m), active vs passive
    # by his critical spread rate 3.0 / CBD m/min, the crown rate of spread
    # of Cruz, Alexander & Wakimoto (2005) (active 11.02 U10^0.90 CBD^0.19
    # exp(-0.17 EFFM) m/min, passive x exp(-CAC)), the crown fire's Byram
    # intensity (canopy fuel CBD x (CH - CBH) at the crown fraction burned)
    # and Rothermel's (1991) crown-fire ellipse; a crowning cell's ember flies
    # on the crown intensity and, under SEMANTICS 19, at least as far as a
    # torching tree lofts one (Albini 1979). It only ever RAISES a cell's
    # spread. A tile without canopy layers spreads exactly as with the knob
    # off. Default OFF = byte-identical; NOT armed by the simulator or the
    # live watcher (the founder decides; watch --crown-fire arms it live).
    crown_fire: bool = False
    # ... the foliar moisture content (%) of the live canopy, the FMC of Van
    # Wagner's initiation threshold: 100 % is the US systems' default (Scott
    # & Reinhardt 2001; FlamMap / FARSITE). Within [50, 300]. Read only with
    # crown_fire.
    crown_foliar_moisture_pct: float = 100.0
    # ... True = the FMC follows the season instead: the Canadian FBP
    # System's foliar moisture model (Forestry Canada Fire Danger Group 1992,
    # ST-X-3 eqs. 1-8) from each cell's latitude, longitude and elevation and
    # the day of year of the weather hour — the spring dip to 85 % around
    # the date of minimum FMC, 120 % from 50 days after it. Read only with
    # crown_fire.
    crown_foliar_moisture_seasonal: bool = False
    # LOCAL WEATHER ACROSS THE DOMAIN (founder 2026-09-27: "ensure that fire
    # spread includes local weather forecast"; SAFETY SEMANTICS 17;
    # wgresponse.wxfield). Legacy: ONE forecast at the fire centroid prices
    # every edge of a 50-75 km domain. When > 0, the centroid stays the
    # primary forecast and the nodes of a GLOBAL lat/lon lattice at this
    # spacing (km) that fall inside the domain are fetched too (coarsened in
    # sqrt-2 steps until centroid + nodes <= weather_points_max); every cell
    # takes its nearest point's forecast (Voronoi zones) and spread prices
    # edges per (zone, hour). A failed secondary point falls back to the
    # centroid for its zone (noted); a failed centroid is the legacy
    # provider failure.
    # 0 = legacy single point, byte-identical; watch --weather-grid-km.
    weather_grid_km: float = 0.0
    # ... the cap on forecast points per domain (centroid included; each is
    # up to two NWS requests on a cold cache). Read only when armed.
    weather_points_max: int = 16
    # ... how long NwsWeather reuses a gridpoint's decoded forecast (s; the
    # points -> gridpoint mapping is static and kept). Read only when armed.
    weather_cache_ttl_s: float = 1800.0
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
    # ILS ONLY WHERE A HOME IS LEFT TO FIND (first-step perf 2026-10-12;
    # docs/SIMULATOR_DESIGN.md "The slow first step"; 'homes' objective
    # only). The ILS is the planner's search for MORE protected homes; when
    # the best construction already protects every job in the pool there is
    # none left to find, and its ils_iterations shakes can only re-polish
    # the tie-break terms (gel freshness, drive time, churn, the anti-
    # concentration spread) — measured at ~12 s per plan on the Rancho Santa
    # Fe 100-estate / 10-crew one-click fleet, three plans a tick, no home
    # gained. True = skip the ILS when every job in the pool is served (the
    # exact micro-solver, the guardrails and the adoption law are unchanged).
    # Default False = byte-identical; the SIMULATOR arms it.
    dispatch_ils_skip_all_served: bool = False
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
    # (also the SEMANTICS 13a regrowth window: a removed contained fire seen
    # growing stays on the map until this many hours pass without growth)
    containment_no_growth_h: float = 48.0
    # CONTAINED FIRES LEAVE THE MAP (founder ruling 2026-09-27: "a fire that
    # is contained and no longer spreading is removed from the map"; SAFETY
    # SEMANTICS 13a-d). When True, a feed incident the feed declares contained
    # (a source at containment_pct >= 100, or a containment / control / out
    # timestamp in its meta), with no source below 100 and no acreage growth
    # since it was removed, leaves the cycle's incident set after the
    # staleness gate: no domain, no ETAs, no dispatch; its pairs take the
    # SEMANTICS 6 absence law; the count + ids are reported (notes,
    # CycleResult.removed_incidents, /api/state, the world header). It returns
    # the cycle it reports < 100 or grows. False = legacy, byte-identical; the
    # watch daemon arms it via --drop-contained.
    drop_contained_incidents: bool = False
    # CONFIRMED PERIMETER ANCHOR (SAFETY SEMANTICS 13e-f; measured: an 80 %-
    # contained fire's 249-h-old polygon, re-confirmed by a 19-h-old record
    # of the same size, was spread forward 249 h into 87 EXTREME homes). When
    # True, a polygon whose record reports the SAME size later than the
    # polygon's own time (|reported - polygon| <= tolerance x polygon acres)
    # and no newer polygon exists in any fused source is seeded at the
    # record's report time instead of its own time. A record reporting MORE
    # acres beyond the tolerance (a growing fire the polygon lags) keeps the
    # legacy, pessimistic seeding. False = legacy, byte-identical; the watch
    # daemon arms it via --confirmed-perimeters.
    perimeter_confirmed_anchor: bool = False
    # ... its "same size" tolerance, a fraction of the polygon's own acres
    # (either way). Live WFIGS 2026-09-27: record size vs polygon area within
    # 1 % for 107 of 146 perimeters, 2 % for 112, 5 % for 116 (the 80 %-
    # contained Willow reads +1.8 %). Every percent admitted is a growing
    # fire's unmapped growth the anchor may under-state — keep it small.
    perimeter_confirm_tolerance_frac: float = 0.02
    # FIRE BELIEF (SAFETY SEMANTICS 14; founder 2026-09-27 "the rate of fire
    # spread should be bayesian unless a new weather pattern emerges" + "a
    # bayesian feedback loop for each fire"; wgresponse.fire_belief,
    # docs/FIRE_BELIEF.md). track = observe + learn + publish status, NO
    # physics change; apply (requires track) = the posterior drives the
    # ensemble's head-ROS multiplier in same-weather-pattern hours (footprint,
    # P50 — alert timing — and P90 move; P10 guarded unless apply_p10, the
    # P10 safety-law change), measured in the doc. All OFF = byte-identical.
    # The rest tune the belief (defaults documented there).
    fire_belief_track: bool = False
    fire_belief_apply: bool = False
    # apply's P10 GUARD (measured 2026-09-27, docs/FIRE_BELIEF.md §5): OFF =
    # P10 = min(belief P10, physics P10) — the belief drives nominal, P50 and
    # P90 but can never make P10 later than today's engine. ON = the belief
    # moves P10 too (members at their posterior quantiles, fast-tail floor):
    # measured UNSAFE for contained fires (P10 too late on 38-43 % of newly
    # burned cells vs 1.6-2.9 % physics). Requires apply.
    fire_belief_apply_p10: bool = False
    fire_belief_prior_sd_log: float = 0.0     # prior sd of log multiplier; 0 = model_error_sd_log if > 0 else 0.71
    fire_belief_half_life_h: float = 72.0     # evidence half-life (window weight 0.5^(age/half-life))
    fire_belief_min_obs_spacing_h: float = 3.0   # min gap between two observations of one kind
    fire_belief_area_rel_err: float = 0.03    # area measurement error, relative part (sd)
    fire_belief_area_abs_err_acres: float = 10.0  # area measurement error, absolute part (sd, acres)
    fire_belief_noise_nu: float = 4.0         # Student-t degrees of freedom of the growth-rate noise
    fire_belief_discrepancy_frac: float = 0.30  # model discrepancy sd as a fraction of predicted growth rate
    fire_belief_fast_sd_floor: float = 0.35   # apply: fast-tail spread never narrower than this (log sd)
    fire_belief_mult_floor: float = 0.05      # apply: no member multiplier below this
    fire_belief_novel_severity_ratio: float = 1.5  # new pattern: severity > ratio x evidence P90
    fire_belief_novel_shift_deg: float = 60.0      # new pattern: wind turns more than this ...
    fire_belief_novel_min_wind_mps: float = 4.0    # ... at an effective wind at least this
    fire_belief_reset_h: float = 24.0         # surprise reset duration (sticky, prior applies)
    fire_belief_static_h: float = 24.0        # unchanged acreage counts as zero growth after this
    fire_belief_rapid_acres_per_day: float = 1000.0   # rapid: growth >= this per 24 h (equivalent)
    fire_belief_rapid_frac_per_day: float = 0.25      # rapid: or >= this fraction of area per 24 h ...
    fire_belief_rapid_min_acres: float = 100.0        # ... with at least this many acres grown
    fire_belief_rapid_lookback_h: float = 48.0        # rapid: windows ending within this lookback
    fire_belief_rapid_noise_z: float = 1.645          # rapid: growth counted at its lower bound grown - z*sd (0 = raw)
    # FIRE BELIEF v2 (2026-09-28; SAFETY SEMANTICS 14 "v2"; docs/FIRE_BELIEF.md
    # "v2"; watch --fire-belief-v2 arms the three rules; every default below
    # is the legacy rule, byte-identical). Measured on 11 real 2026 fires
    # (docs/review/belief_vs_physics_2026-09-27.md): the hourly new-weather
    # test fired on the ordinary afternoon wind turn, and the 3 %-of-area
    # noise hid a big fire's growth from the rapid rule.
    # New-weather test: 'hourly' = legacy (forecast hours against the
    # evidence hours' P90 severity and ONE mean direction); 'daily' = forecast
    # DAYS against evidence DAYS (peak severity + driving wind; diurnal-aware).
    # The daily thresholds were chosen by leave-one-fire-out on the 11 fires
    # (docs/FIRE_BELIEF.md "v2"): the hourly rule's own values, per day.
    fire_belief_novelty: str = "hourly"
    fire_belief_novel_day_severity_ratio: float = 1.5  # daily: a day's peak severity > this x the harshest evidence day
    fire_belief_novel_day_shift_deg: float = 60.0      # daily: a driving wind more than this from EVERY evidence day's ...
    fire_belief_novel_day_min_wind_mps: float = 4.0    # ... at a driving wind of at least this (m/s)
    # Rapid noise: 'area_frac' = legacy (each end's sd = hypot(area_rel_err
    # x A, area_abs_err)); 'calibrated' = a PERIMETER window's growth noise
    # is the measured re-mapping noise hypot(k x sqrt(A acres), sqrt(2) x
    # area_abs_err) acres (reported-acreage windows keep the legacy sd).
    fire_belief_rapid_noise: str = "area_frac"
    fire_belief_rapid_perim_noise_k: float = 1.0      # calibrated: k (acres per sqrt(acre)); measured, see docs
    # Rapid persistence: N >= 2 consecutive perimeter windows each growing at
    # a rapid rate RAW make the fire rapid (0 = off, legacy).
    fire_belief_rapid_persist_windows: int = 0
    # FEED SANITY (SAFETY SEMANTICS 18; the 2026-09-27 audit of the live feed
    # path the belief learns from, docs/review/feed_sanity_2026-09-27.md;
    # wgresponse.feedsanity). True = the belief takes no observation from a
    # perimeter or size the feed cannot have meant (an own time in the future
    # or before the fire, a polygon collapsed under its own record's size, a
    # drop beyond measurement noise until it persists), counts a re-stamped
    # polygon once per fire_belief_static_h, and reads a polygon's area from
    # its geometry when the feed's figure disagrees; the seed never uses a
    # smaller polygon than the fire's last plausible one (in-process memory)
    # and replaces an implausible polygon time with the record's. False =
    # legacy, byte-identical; the watch daemon arms it via --feed-sanity.
    feed_sanity: bool = False
    feed_sanity_future_h: float = 1.0        # an own time later than now + this is implausible
    feed_sanity_area_ratio: float = 2.0      # the feed's area figure stands only within this factor of the geometry's
    feed_sanity_collapse_frac: float = 0.10  # a polygon under this fraction of its record's reported size is not the perimeter ...
    feed_sanity_min_acres: float = 100.0     # ... when that reported size is at least this
    feed_sanity_restamp_frac: float = 0.001  # a polygon within this fraction of the last observed one's area is a re-stamp
    feed_sanity_drop_z: float = 3.0          # a size more than z x sd(difference) below the last one is held
    feed_sanity_memory_h: float = 72.0       # the seed remembers a fire's last plausible polygon this long
    # SIMULATOR CITY WEATHER (founder 2026-10-09, verbatim: "change the
    # weather conditions for each city specific"; docs/review/
    # city_weather_2026-10-09.md). Read ONLY by wgresponse.simui (the
    # interactive simulator builds its own synthetic weather; the live
    # engine takes its weather from NWS / Open-Meteo and never reads this).
    # "generic" = the legacy intensity table (simui.INTENSITY_WEATHER: one
    # sustained wind / RH / temperature per intensity, the same at every
    # location, held constant for the whole run) — byte-identical. "city" =
    # each simulator community's own conditions per intensity
    # (simui.CITY_WEATHER: station climatology + historic fire days, adjusted
    # to the homes' elevation) with a daily cycle on local time and, for
    # high / extreme, a FINITE wind event from the ignition that eases back
    # to the community's moderate daily cycle; a location without a profile
    # keeps the generic table. The SIMULATOR arms "city" (explicit override
    # wins). Seeded drills (wgresponse.seeded) keep the generic table.
    sim_weather_profile: str = "generic"


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
    if not cfg.escape_road_horizon_cap_h > 0:
        problems.append("escape_road_horizon_cap_h must be > 0")

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
    # --- per-fire off-domain horizon (SAFETY SEMANTICS 15) ---
    if not cfg.corridor_offraster_ros_floor_mps > 0:
        problems.append("corridor_offraster_ros_floor_mps must be > 0 (a "
                        "zero floor extrapolates a fire that never arrives)")
    elif (cfg.corridor_offraster_per_fire
          and cfg.corridor_offraster_ros_floor_mps > cfg.corridor_offraster_ros_mps):
        problems.append("corridor_offraster_ros_floor_mps must be <= "
                        "corridor_offraster_ros_mps (the floor above the "
                        "ceiling would extrapolate a known-slow fire faster "
                        "than a rapid one)")
    if (cfg.corridor_offraster_margin_min < 0
            or cfg.corridor_offraster_margin_min > MAX_MARGIN_MIN):
        problems.append(f"corridor_offraster_margin_min must be within "
                        f"[0, {MAX_MARGIN_MIN:g}] minutes")
    if not cfg.fire_behavior_max_age_h > 0:
        problems.append("fire_behavior_max_age_h must be > 0")
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
    # --- ember reach by fire size (SAFETY SEMANTICS 19) ---
    if not (math.isfinite(cfg.spot_fire_size_ref_ha)
            and 0.0 < cfg.spot_fire_size_ref_ha <= 1_000_000.0):
        problems.append("spot_fire_size_ref_ha must be a finite size within "
                        "(0, 1e6] ha")
    if not (0.0 < cfg.spot_fire_size_exp <= 2.0):
        problems.append("spot_fire_size_exp must be within (0, 2] (0 would "
                        "make the reach independent of fire size)")
    if not (0.0 <= cfg.spot_ignition_delay_min <= 120.0):
        problems.append("spot_ignition_delay_min must be within [0, 120] "
                        "minutes")
    # --- crown fire (SAFETY SEMANTICS 22) ---
    if not (math.isfinite(cfg.crown_foliar_moisture_pct)
            and 50.0 <= cfg.crown_foliar_moisture_pct <= 300.0):
        problems.append("crown_foliar_moisture_pct must be within [50, 300] "
                        "percent (live conifer foliage)")
    # --- local weather across the domain (SAFETY SEMANTICS 17) ---
    if not (cfg.weather_grid_km == 0.0
            or (math.isfinite(cfg.weather_grid_km)
                and cfg.weather_grid_km >= 1.0)):
        problems.append("weather_grid_km must be 0 (off) or a finite spacing "
                        ">= 1 km (a sub-km lattice is fabricated precision "
                        "for any forecast grid; NWS cells are 2.5 km)")
    if not (1 <= cfg.weather_points_max <= 255):
        problems.append("weather_points_max must be within [1, 255] (zones "
                        "are stored one byte per cell)")
    if not (0.0 <= cfg.weather_cache_ttl_s <= 21_600.0):
        problems.append("weather_cache_ttl_s must be within [0, 21600] s (a "
                        "cached forecast older than 6 h is stale weather)")

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
    # --- confirmed perimeter anchor (SAFETY SEMANTICS 13e) ---
    if not (0.0 <= cfg.perimeter_confirm_tolerance_frac < 1.0):
        problems.append("perimeter_confirm_tolerance_frac must be within [0, 1) "
                        "(1 would call any size the polygon's own)")

    # --- fire belief (SAFETY SEMANTICS 14) ---
    if cfg.fire_belief_apply and not cfg.fire_belief_track:
        problems.append("fire_belief_apply requires fire_belief_track (the "
                        "posterior it applies is learned by the tracker)")
    if cfg.fire_belief_apply_p10 and not cfg.fire_belief_apply:
        problems.append("fire_belief_apply_p10 requires fire_belief_apply")
    if not (0.0 <= cfg.fire_belief_prior_sd_log <= 3.0):
        problems.append("fire_belief_prior_sd_log must be within [0, 3] "
                        "(0 = the model-error term, else 0.71)")
    if not cfg.fire_belief_half_life_h > 0:
        problems.append("fire_belief_half_life_h must be > 0")
    if cfg.fire_belief_min_obs_spacing_h < 0:
        problems.append("fire_belief_min_obs_spacing_h must be >= 0")
    if not (0.0 <= cfg.fire_belief_area_rel_err <= 1.0):
        problems.append("fire_belief_area_rel_err must be within [0, 1]")
    if not cfg.fire_belief_area_abs_err_acres > 0:
        problems.append("fire_belief_area_abs_err_acres must be > 0 (a "
                        "zero-error area makes the likelihood singular)")
    if not (1.0 <= cfg.fire_belief_noise_nu <= 1000.0):
        problems.append("fire_belief_noise_nu must be within [1, 1000]")
    if not (0.0 <= cfg.fire_belief_discrepancy_frac <= 5.0):
        problems.append("fire_belief_discrepancy_frac must be within [0, 5]")
    if not (0.0 <= cfg.fire_belief_fast_sd_floor <= 3.0):
        problems.append("fire_belief_fast_sd_floor must be within [0, 3]")
    if not (0.0 < cfg.fire_belief_mult_floor <= 1.0):
        problems.append("fire_belief_mult_floor must be within (0, 1]")
    if not cfg.fire_belief_novel_severity_ratio >= 1.0:
        problems.append("fire_belief_novel_severity_ratio must be >= 1 (a "
                        "ratio below 1 calls the evidence weather itself new)")
    if not (0.0 <= cfg.fire_belief_novel_shift_deg <= 180.0):
        problems.append("fire_belief_novel_shift_deg must be within [0, 180]")
    if cfg.fire_belief_novel_min_wind_mps < 0:
        problems.append("fire_belief_novel_min_wind_mps must be >= 0")
    if cfg.fire_belief_reset_h < 0:
        problems.append("fire_belief_reset_h must be >= 0")
    if not cfg.fire_belief_static_h > 0:
        problems.append("fire_belief_static_h must be > 0")
    if not cfg.fire_belief_rapid_acres_per_day > 0:
        problems.append("fire_belief_rapid_acres_per_day must be > 0")
    if not cfg.fire_belief_rapid_frac_per_day > 0:
        problems.append("fire_belief_rapid_frac_per_day must be > 0")
    if cfg.fire_belief_rapid_min_acres < 0:
        problems.append("fire_belief_rapid_min_acres must be >= 0")
    if not cfg.fire_belief_rapid_lookback_h > 0:
        problems.append("fire_belief_rapid_lookback_h must be > 0")
    if not (0.0 <= cfg.fire_belief_rapid_noise_z <= 5.0):
        problems.append("fire_belief_rapid_noise_z must be within [0, 5]")
    # --- fire belief v2 (2026-09-28) ---
    if cfg.fire_belief_novelty not in ("hourly", "daily"):
        problems.append("fire_belief_novelty must be 'hourly' or 'daily'")
    if not cfg.fire_belief_novel_day_severity_ratio >= 1.0:
        problems.append("fire_belief_novel_day_severity_ratio must be >= 1 (a "
                        "ratio below 1 calls the evidence days themselves new)")
    if not (0.0 <= cfg.fire_belief_novel_day_shift_deg <= 180.0):
        problems.append("fire_belief_novel_day_shift_deg must be within "
                        "[0, 180]")
    if cfg.fire_belief_novel_day_min_wind_mps < 0:
        problems.append("fire_belief_novel_day_min_wind_mps must be >= 0")
    if cfg.fire_belief_rapid_noise not in ("area_frac", "calibrated"):
        problems.append("fire_belief_rapid_noise must be 'area_frac' or "
                        "'calibrated'")
    if not (0.0 < cfg.fire_belief_rapid_perim_noise_k <= 100.0):
        problems.append("fire_belief_rapid_perim_noise_k must be within "
                        "(0, 100]")
    if isinstance(cfg.fire_belief_rapid_persist_windows, int) and not \
            isinstance(cfg.fire_belief_rapid_persist_windows, bool) and (
                cfg.fire_belief_rapid_persist_windows < 0
                or cfg.fire_belief_rapid_persist_windows == 1):
        problems.append("fire_belief_rapid_persist_windows must be 0 (off) "
                        "or >= 2 (one raw window is the rule the noise "
                        "discount exists to prevent)")

    # --- feed sanity (SAFETY SEMANTICS 18) ---
    if not (math.isfinite(cfg.feed_sanity_future_h)
            and cfg.feed_sanity_future_h >= 0):
        problems.append("feed_sanity_future_h must be a finite value >= 0")
    if not (math.isfinite(cfg.feed_sanity_area_ratio)
            and cfg.feed_sanity_area_ratio > 1.0):
        problems.append("feed_sanity_area_ratio must be a finite factor > 1 "
                        "(1 would reject every area figure)")
    if not (0.0 < cfg.feed_sanity_collapse_frac < 1.0):
        problems.append("feed_sanity_collapse_frac must be within (0, 1)")
    if not (math.isfinite(cfg.feed_sanity_min_acres)
            and cfg.feed_sanity_min_acres >= 0):
        problems.append("feed_sanity_min_acres must be a finite value >= 0")
    if not (0.0 <= cfg.feed_sanity_restamp_frac < 0.1):
        problems.append("feed_sanity_restamp_frac must be within [0, 0.1) "
                        "(a wider band would call real growth a re-stamp)")
    if not (math.isfinite(cfg.feed_sanity_drop_z)
            and cfg.feed_sanity_drop_z > 0):
        problems.append("feed_sanity_drop_z must be a finite value > 0")
    if not (math.isfinite(cfg.feed_sanity_memory_h)
            and cfg.feed_sanity_memory_h > 0):
        problems.append("feed_sanity_memory_h must be a finite value > 0")
    if cfg.sim_weather_profile not in ("generic", "city"):
        problems.append("sim_weather_profile must be 'generic' or 'city'")

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
