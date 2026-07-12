# WG Response — Reusable Asset Inventory (Recon Digest)

Date: 2026-07-06. Read-only inventory of two local repos for the new **WG Response**
operational wildfire-response coordination engine.

- **Repo A (engine):** `C:/Users/colin/projects/computer vpp demonstration/firerisk_v2-engine-hardening`
- **Repo B (wgdefense):** `C:/Users/colin/wg_total_defense`

Headline: **far more is reusable than expected.** Repo A already contains a working
GraphGel crew-dispatch optimizer with a Leaflet map UI (`graphgel_dispatch/`), a
Finney-MTT fire-propagation engine that produces per-cell **arrival-time grids**
(exactly the "projected fire arrival" primitive WG Response needs), the full
protected-client DynamoDB layer, geocoding, terrain/fuel ingest, SES notification
plumbing with consent gates, staff auth, and a proven CodeBuild→ECR→SAM deploy
pipeline. Repo B contributes the gel-timing physics (T-60/T-20 hard-stop window,
coating-life model) and the contracts-is-law engineering conventions.

**What does NOT exist anywhere (must be built new):** live fire-perimeter ingest
(no FIRMS/NIFC/IRWIN/GOES code found), crew phone-GPS tracking, SMS alerting, and
any 72h/24h alert state machine keyed to client properties.

---

## 1. Client / consumer property data (Repo A)

### DynamoDB layer
- `firerisk/persistence/dynamodb.py` — the ONLY module that talks to DynamoDB.
  `Repository` ABC + `DynamoRepository` (prod) + `InMemoryRepository` (tests/dev).
  Factory picks Dynamo when all `DDB_*` env vars are set (SAM injects them),
  else in-memory — copy this factory pattern verbatim for WG Response.
- `firerisk/persistence/models.py` — pydantic models for every table.
  Key records:
  - `Homeowner` (PK `email`) — line ~126: `email, name, phone,
    fr_graphgel_enrolled: bool, fr_intervention_count, fr_last_assessed`.
  - `Assessment` (PK `assessment_id`, SK `version`) — line ~181: carries
    **`address: str`, `lat: float`, `lon: float`**, `scores`, `pmap_s3_key`
    (serialized PropertyMap in S3), `engine_version`, GSI1 email→assessments.
  - `ConsumerProfile` (PK `email`, GSI1 `LifecycleGSI`) — line ~546: the golden
    record. Flat fields incl. **`primary_address`, `zip`, `lat`, `lon`,
    `graphgel_enrolled: bool` (line 589), `protection_level`,
    `current_survivability`, `hazard_zone`, consent/suppression block**.
  - `GraphGelEvent` (PK `event_id`) — line ~326: per-deployment record
    (`assessment_id, declared_event_id, batch_id, sqft_applied, photo_ids,
    contractor_id, verified_by, dispatched_at`). This is the natural
    persistence target for WG Response deployment logs.
  - `Technician` (PK `tech_id`) — the crew roster; PD-managed, powers Field
    Tech App login (`pin_sha256` optional PIN). WG Response crew identity
    should extend this table rather than invent a new one.
- Profile builder: `firerisk/consumer/profile.py :: build_consumer_profile`
  (pure) and `refresh_consumer_profile` — rebuilds the golden record from
  canonical tables; `graphgel_enrolled = homeowner.fr_graphgel_enrolled OR
  existing profile flag` (profile.py line 298).

### GraphGel purchase/subscription marker
- **`Homeowner.fr_graphgel_enrolled`** (canonical, models.py:136) projected to
  **`ConsumerProfile.graphgel_enrolled`** (models.py:589). A prebuilt segment
  rule exists: `firerisk/consumer/segments.py:261`
  (`SegmentRule(field="graphgel_enrolled", op=EQ, value=True)`) — i.e., the
  "protected client list" for WG Response is one segment query away.
  `protection_level` values come from `firerisk/scoring/tier_progression.py ::
  ProtectionLevel` (unmitigated / ember_ready / structure_hardened /
  property_resilient).

### Physical tables (SAM: `infra/template.yaml`)
`TableName: !Sub "wg-<name>-${StageName}"`, stage = `dev` today:
`wg-homeowners-dev`, `wg-assessments-dev`, `wg-consumer-profiles-dev` (via
`DDB_CONSUMER_PROFILES`), `wg-graphgel-events-dev`, `wg-technicians-dev`,
`wg-contractors-dev`, `wg-users-dev`, `wg-hardening-jobs-dev`, plus ERP/CRM
tables (segments, campaigns, mailing-list-members, consumer-activities,
metrics-events/rollups, rate-limit, score-codes). Env-var names at
template.yaml lines 603–641.

### Geocoding
- `firerisk/api/geocode.py :: geocode(address, *, timeout_s=3.5) -> GeocodeResult`
  (line 393). `GeocodeResult` = `{address, lat, lon, display_name, resolved_from}`.
- Primary resolver switch: env **`FIRERISK_GEOCODER=aws_location`** →
  `_query_aws_location` (line 300) calls **Amazon Location Service
  `geo-places:Geocode`** (`boto3.client("geo-places")`,
  `client.geocode(QueryText=..., MaxResults=1, IntendedUse="Storage")`,
  region default `us-west-2`). Precision-gated: coarse PlaceTypes
  (Locality/PostalCode/District/...) FAIL LOUD (`geocoded=False`) rather than
  silently returning a city centroid; results are cached/pinned (geocode once,
  reuse forever — determinism discipline). Legacy chain = Census
  (`geocoding.geo.census.gov/geocoder/locations/onelineaddress`) → Nominatim
  (`nominatim.openstreetmap.org/search`). Also parses inline `"lat, lon"`
  strings offline. Called from `firerisk/api/app.py:981` inside the
  `POST /api/property/address` pipeline.

---

## 2. Terrain / fuel / vegetation ingestion (Repo A — all in `firerisk/api/`)

All modules share one transport style: stdlib `urllib` + guarded TIFF read, no
API key, best-effort (return `None` → caller falls back), attribution strings,
and default-OFF `FIRERISK_*` env flags. Results are S3-pinned for determinism
via `firerisk/api/input_pins.py` (`npz_get`/`npz_put`).

- **DEM / slope:** `firerisk/api/dem.py :: fetch_dem(lat, lon, half_m, ny, nx,
  timeout_s=30)` → `(elev ndarray float32, extent)` from USGS 3DEP ImageServer
  `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage`
  (~10 m, 1/3 arc-second). First successful fetch per (lat,lon,half,grid) is
  pinned. Slope/aspect derived downstream in the pmap build.
- **LANDFIRE fuel type:** `firerisk/api/landfire.py` — LF2024 FBFM40 fuel-model
  raster from
  `https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2024/LF2024_FBFM40_CONUS/ImageServer/exportImage`
  (raw uint8 FBFM40 codes; `fbfm40_to_sb40` maps to the engine's Scott & Burgan
  subset in `firerisk/data/sb40.py`). Gate: `FIRERISK_LANDFIRE_FUEL`, URL
  override `LANDFIRE_IMAGESERVER_URL`. Fusion strategy: LANDFIRE owns fuel
  TYPE, aerial path owns boundaries/non-burnable detail.
- **LANDFIRE point sampling recipe:** `tests/validation/fuel_confusion.py` —
  reusable `getSamples` recipe against
  `https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2024/LF2024_FBFM40_CONUS/ImageServer/getSamples`
  with a `coarse(code)` FBFM40→class binning helper (NB 91–99, GRASS 101–109,
  GRASS-SHRUB 121–124, SHRUB-CHAP 141–149, TIMBER 161–169).
- **NAIP 4-band (RGB+NIR):** `firerisk/api/naip.py` — USDA NAIP 0.6 m imagery
  for true-NDVI cured-fuel discrimination; gate `FIRERISK_NAIP_NIR`, override
  `NAIP_IMAGESERVER_URL`; consumed by
  `firerisk/api/veg_from_aerial.py :: classify_aerial_nir_to_fuel`.
- **Lidar canopy:** `firerisk/api/canopy_lidar.py` — CBD/CBH/CH from USGS 3DEP
  lidar (AWS Open Data `usgs-lidar` EPT; needs optional pdal/laspy) with a
  4-rung fallback ladder (EPT → CHM raster → Meta/WRI 1 m canopy height →
  USFS TreeMap); per-metric confidence blending + disturbance reconciliation.
  Gate `FIRERISK_CANOPY_LIDAR`, default OFF.
- **PropertyMap contract:** `firerisk/propertymap/schema.py :: PropertyMap` —
  the unified raster+structures data model (shared (Y,X) grid, affine
  transform, `Structure` footprint dataclass); save/load `.npz` + JSON sidecar.
  Everything above feeds this.
- **Caveat (memory-verified):** the default RGB aerial classifier cannot see
  chaparral (0% recall); LANDFIRE fusion is the fix but is default-OFF.
  WG Response fuel maps should turn `FIRERISK_LANDFIRE_FUEL=on` (it's an
  operational product, not the byte-pinned consumer score).

### Fire propagation & weather (bonus, directly on-mission)
- `firerisk/fire/propagation.py` — **Finney (2002) Minimum-Travel-Time spread
  on a raster grid**: per-cell head ROS + Anderson length/breadth ellipse,
  Dijkstra → `PropagationResult.arrival_time_s (Y,X)` (inf = unburned). This
  IS the "projected arrival time at client property" primitive; JIT variant in
  `propagation_jit.py` (`WG_DISABLE_JIT=1` to disable).
- Supporting physics: `rothermel.py` (ROS), `wind_slope.py`, `spotting.py` /
  `spotting_stochastic.py` (ember transport), `crown.py`, `moisture.py`,
  `firebreak_model.py`, all under `firerisk/fire/`.
- `firerisk/fire/crew_response.py :: CrewResponseConfig` — calibrated crew
  timing constants: `response_time_s=1800` (30 min alert→on-scene; 900 s for
  premium tier), setup 5–15 min, full benefit at ~30 min margin before
  arrival, `efficacy_max=0.50` (0.70 premium). CalFire DINS-calibrated. Gate
  `FIRERISK_CREW_RESPONSE`.
- `firerisk/weather/scenario.py :: WeatherScenario` — frozen dataclass:
  `temperature_c, relative_humidity (0-1), wind_speed_20ft_mps,
  wind_direction_deg (FROM, 0=N CW), wind_cone_half_angle_deg, gust factor
  (1.05 calm … 1.80 peak Santa Ana), ignition_type: adjacent|front|
  ember_storm|front_near_miss`. NOTE: scenarios are synthetic/RAWS-percentile
  driven — **no live weather-feed ingest (NWS/RAWS API) exists**; WG Response
  must add one.

---

## 3. Notification infrastructure (Repo A)

- `firerisk/integrations/ses.py` — AWS SES transactional sender + tiny HS256
  JWT impl (`_sign_jwt` / `verify_jwt`, key from Secrets Manager
  `jwt/signing-key`, field `key`). Sender: env `SES_SENDER` (default
  `no-reply@dev.wildfireguardian.io` — stale; live identity per launch notes is
  owner gmail until `no-reply@thewildfireguardian.com` DKIM lands). Sends via
  `ses:SendEmail`; magic-link (15 min TTL), assessment-ready, quote-approved.
- `firerisk/integrations/consumer_emails.py` — branded HTML+text consumer
  emails with PDF attachment via `ses:send_raw_email` (MIME multipart).
  Pattern to copy: producer render-validates then **enqueues a job on the SQS
  background queue; the worker Lambda (which holds SES IAM) sends**. Each
  email type behind its own default-OFF flag (`WG_EMAIL_SCORE_DELIVERY`,
  `WG_EMAIL_QUOTE_ACK`). CAN-SPAM footer + signed one-click unsubscribe
  (token pattern in `firerisk/consumer/campaigns.py :: marketing_footer`).
- **Consent gate:** `firerisk/consumer/consent.py :: is_mailable(profile,
  purpose)` — BLOCK-ALL suppressions (hard_bounce, complaint, manual,
  ccpa_delete) vs MARKETING-ONLY (unsubscribe). **Fire-arrival alerts are
  operational/service messages** → allowed under marketing suppression but
  still blocked by BLOCK-ALL; WG Response should call `is_mailable` with a
  service purpose.
- Infra: `infra/template.yaml` — `BackgroundJobsQueue` +
  `HubSpotWritesQueue` (SQS), worker Lambda `wg-assess-worker-${StageName}`
  SQS-triggered, `ses:SendEmail`+`ses:SendRawEmail` IAM on both API and worker
  roles (template lines ~762, ~877). Env `BACKGROUND_QUEUE_SQS_URL`.
- **SMS: none.** `ses.py` docstring explicitly defers "Crew dispatch
  notifications — phone/SMS (Phase 2)". No SNS/Twilio/Pinpoint code found
  anywhere. WG Response EXTREME (24h) alerts need a new SMS channel
  (SNS or Pinpoint would slot into the same enqueue→worker pattern).
- SES prod-mode access is still an open item (sandbox = verified recipients
  only) per the staff-auth ship notes. Not verified live.

---

## 4. wgdefense conventions worth copying (Repo B)

- **`src/wgdefense/contracts.py` — "contracts are law".** One stdlib-only file
  (dataclasses/enum/typing/json, no third-party imports EVER) holding every
  shared type, enum, Protocol interface, and MQTT topic map. Rules encoded in
  the docstring: units in field names (`gpm, psi, m, deg, s, gal, kw_m2`),
  compass degrees 0=N CW, unix-seconds floats, modules exchange ONLY contract
  types. `to_json()` gives canonical sorted-key JSON for hashing/signing.
  Directly relevant types: `SystemState` (DORMANT→WATCH→PREPOSITION→GEL_COAT→
  DEFEND→…), `ThreatEstimate` (bearing/range/eta_min/confidence),
  `DefenseEventRecord` (hash-chained evidence record — reuse for dispatch
  audit), `Topics` (`wg/{site}/...`, `wg/mesh/{hood}/{type}`).
- **Module layout:** flat `src/wgdefense/*.py`, one PCT concern per module,
  `comms/` subpackage for bus/API/dashboard; core is stdlib-only, extras via
  `pip install .[full]` (fastapi/uvicorn/paho-mqtt/matplotlib) —
  `pyproject.toml` `[project.optional-dependencies]`.
- **Test conventions:** `tests/test_<module>.py` mirroring modules, 277/277
  passing, pure-deterministic: mock drivers implement the contract Protocols;
  RNG is SHA-256-keyed on `(seed, scenario, int(t))` (see
  `preposition.MockRiskFeed._rng`) — never process-salted `hash()` or global
  random. `_protocol_conformance()` type-checker hooks assert mocks satisfy
  Protocols.
- **CLI pattern:** `src/wgdefense/cli.py` — argparse subcommands
  (`simulate|plan|serve|report`), each delegating to a small `_cmd_*` handler;
  optional deps degrade with a clear install message; console script in
  pyproject (`wgdefense = "wgdefense.cli:main"`).

### `preposition.py` (PCT-04) — readiness/alert-laddering to reuse
- `ReadinessPlanner.assess(t, feeds, weather) -> {score, eta_min, bearing_deg,
  stage, actions, mode, confidence_penalty, red_flag}` — fuses risk-feed event
  dicts (`{"kind": "red_flag"|"fire_detected"|"forecast", "eta_min",
  "bearing_deg", "confidence", "source"}`) into a 0–1 readiness score.
- Constants: `RED_FLAG_SCORE=0.25`; ETA anchors (60 min→0.4, 30→0.6, 10→0.9,
  interpolated in 1/eta); stages standby/watch(≥0.20)/preposition(≥0.35 or
  ETA≤60)/active(ETA≤5); staging actions `top_off_tank` / `warm_pump`(ETA≤30)
  / `prewet_jit`(ETA≤45); degraded-mode ladder
  hybrid→satellite→local→weather_only at −0.2 confidence per step.
- These are per-site minute-scale thresholds; WG Response's 72h/24h ladder is
  a longer-horizon analog of exactly this `stage()` structure — copy the shape,
  re-anchor at (72 h → WATCH, 24 h → EXTREME).

### `gel.py` (PCT-07) — gel deploy timing & effectiveness (YES, it models both)
- **Timing window:** coating opens **T-60 min** before predicted arrival
  (`COAT_START_LEAD_MIN=60.0`) and **HARD-STOPS at T-20**
  (`COAT_HARD_STOP_MIN=20.0`); `SMOLDER_MARGIN_MIN=20.0` (coat must survive to
  ~T+20). `coat_plan(site, surfaces, eta_min, ...) -> GelPlan` enforces both
  bounds and truncates to priority order on time/budget shortfall — this is a
  ready-made template for the WG Response "30-min deploy, clear 30 min before
  arrival" scheduler (constants differ; structure identical).
- **Dosing:** `DEFAULT_DOSE_L_PER_M2 = 3.0` L mixed gel per m² (~3 mm wet
  coat); mix ratio 1:6 concentrate:water → concentrate = 1/7 of mixed volume;
  `concentrate_needed_gal(area_m2, mix_ratio, dose)`; example: 250 m² roof →
  750 L mixed → 28.3 gal concentrate. `L_PER_GAL=3.785411784`.
- **Effectiveness duration:** `coating_life_h(temp_c, rh_pct, wind_mps,
  life_at_25c_h=6.0)` = `life25 * exp(-0.08*(T-25)) * (RH/50)^0.3 /
  (1+0.05*wind)`, floored at 1.0 h. Baseline **6 h at 25 °C/50 %RH/calm**.
  `reapply_needed(applied_t, now, eta_min, weather)` schedules re-application
  if the coat expires before arrival+smolder margin. Critical for WG Response:
  gel applied at the 24h EXTREME mark may NOT last to fire arrival in hot/windy
  conditions — the coating-life model must gate deployment timing.
- `GelState` ledger (capacity/draw/refill, low-stock alert at 20%) +
  `MockGelDriver` for tests.

---

## 5. Staff auth + deploy pipeline (Repo A)

### Auth patterns (three tiers, all reusable as-is)
1. **`X-Internal-API-Key` header == env `INTERNAL_API_KEY`** → wg_admin.
   Fail-closed when unset (503) unless `WG_DEV_OPEN=1` (local/tests only).
   Gate examples: `firerisk/api/routes/wg_v1.py:204+` (invite_issue admin
   mode), `firerisk/api/routes/portal.py :: _portal_actor`.
2. **Portal JWT:** `POST /api/portal/auth/login`
   (`firerisk/api/routes/portal.py :: portal_login`) — role `wg_admin`/
   `pd_admin` via shared portal password (falls back to INTERNAL_API_KEY), or
   role `tech` via roster-validated `tech_id` + optional PIN
   (salted SHA-256). Mints 12 h HS256 JWT `{sub, scope:"portal", role}` signed
   with Secrets Manager `jwt/signing-key`; sent as `Authorization: Bearer`.
   Rate-limited 20 attempts/15 min/IP. **The `tech` role login is exactly the
   WG Response crew-app auth.**
3. **Per-user staff accounts:** `firerisk/auth/users.py` — PBKDF2-HMAC-SHA256
   (Django-style encoding), roles `admin/staff/readonly` mapped onto legacy
   gates via `ROLE_EFFECTIVE`; rows in `wg-users-dev`; routes in
   `firerisk/api/routes/auth_users.py`; seeding via `scripts/seed_admin_user.py`.
   Consumer-side session JWT dependency: `firerisk/api/auth.py ::
   get_current_homeowner` (cookie `wg_session` or Bearer).

### Deploy pipeline — how a new Lambda service slots in
Everything is **one Docker image, many Lambdas**: `buildspec.yml` (AWS
CodeBuild, used because local Docker→ECR push is broken) builds with the
classic builder (`DOCKER_BUILDKIT=0`, Lambda rejects buildx OCI-index images)
and pushes to ECR `296920174396.dkr.ecr.us-west-2.amazonaws.com/wg-firerisk-dev:<tag>`
(current tag `v4.53.0-l41-pdf-store`). `infra/template.yaml` (SAM) then defines
image-based Lambdas — `wg-api-${StageName}` (FastAPI via handler override),
`wg-assess-worker-${StageName}` (SQS-triggered), `wg-hubspot-writer-${StageName}` —
all pointing at the same `EcrImageUri` with different `ImageConfig` commands,
plus all tables/queues/IAM. A new **WG Response service = one more
AWSServerlessFunction block in template.yaml reusing the same image** (add a
FastAPI router or a new handler module), plus its DDB policies; deploy =
CodeBuild build+push, then `sam deploy` from `infra/samconfig.toml`.
Live API base: `https://cobz3ddao0.execute-api.us-west-2.amazonaws.com/dev`
(hardcoded in `frontend/v2/public/portals/*.html`). Alternatively, an
EventBridge-scheduled Lambda (for the fire-watch polling loop) follows the
same pattern. Region us-west-2 throughout.

---

## 6. Existing map UI + the dispatch optimizer (Repo A, `graphgel_dispatch/`)

### Leaflet map — the only real map UI in either repo
- `graphgel_dispatch/frontend/index.html` — single-file dashboard using
  **Leaflet 1.9.4 from unpkg CDN** + OpenStreetMap tiles
  (`https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`): `initMap()` at line
  ~473, per-home markers colored by assignment, plan-comparison Chart canvas.
  Served by the engine app via `GET` route registered in
  `firerisk/api/dispatch_routes.py` (HTMLResponse). This is the seed of the
  WG Response ops map (add fire perimeter, arrival-time isochrones, crew GPS
  dots). The main Next.js frontend (`frontend/v2/`) has **no** map library.
- `wgdefense` `comms/dashboard.html` is zone/tank/threat status only — no map.

### GraphGel Dispatch Optimizer (huge head start — this is proto-WG-Response)
- Package: `graphgel_dispatch/graphgel_dispatch/`; CLI `wg-dispatch`
  (`cli.py:main`); REST mounted into the engine app:
  - `POST /api/dispatch/build_homes` `{property_ids, scenario_mode:
    integrated|front_only|ember_only}` → HOA spec JSON
  - `POST /api/dispatch/optimize` `{homes|property_ids, crews:int=3,
    rate_gpm:20, prep_time_min:5–10, time_budget_min:90, gallon_budget?,
    strategy: all|max_safety_in_time|max_per_gallon|unconstrained,
    solver: auto|ilp|greedy}` → DispatchPlan JSON
  - `GET /api/dispatch/health`
- `model.py` — `Home`, `Crew`, `FireScenario` (**`detected_at`,
  `estimated_arrival`, `.minutes_available` = detection→arrival budget**),
  `Assignment`, `CrewSchedule`, `DispatchPlan`; `SURFACE_PRIORITY` 7-surface
  concave lift curve (eaves 22% lift @5% area → other_walls).
- `cost.py` — **`GAL_PER_M2_FULL=0.84`** (1/8" wet film → FSI Class A),
  20 gpm application rate, windward-only option
  (`WINDWARD_AREA_FACTOR=0.50`, `WINDWARD_LIFT_FACTOR=0.70`).
- `strategies/max_safety_in_time.py :: optimize_max_safety_in_time` — MMKP
  binary ILP (PuLP+CBC, <1 s for 50 homes×5 crews) with marginal-efficiency
  greedy fallback; per-crew time budget T = the safety window. **WG Response's
  "crews clear ≥30 min before arrival" is just T = eta_min − 30 − travel.**
  Missing pieces vs WG Response: no travel matrix (assumes tight HOA
  clustering), no live crew positions, single-HOA scope.
- `lift_calculator.py :: compute_home_lift` — runs the firerisk engine twice
  (baseline vs full-GraphGel) per home for Δsurvivability. CAVEAT: still on the
  legacy 60/30/10 scenario blend, not the authoritative 25/75
  (`W2_DIRECT=0.25/W2_EMBER=0.75` in `firerisk/jobs/handlers/assess.py`).
- `ingest.py` / `realdata.py` — build `Home[]` from addresses via
  `POST /api/property/address` (geocode→parcel→footprint→PropertyMap) or from
  an OSM boundary polygon.

---

## Gaps WG Response must build new (verified absent)

1. **Live fire data ingest** — no NASA FIRMS/VIIRS, NIFC perimeters, IRWIN, or
   GOES hotspot code in either repo (grep over `*.py` found only incidental
   word matches). New module needed; the `preposition.RiskFeed` protocol +
   event-dict schema is the right interface to implement it against.
2. **Live weather feed** — `WeatherScenario` is synthetic/percentile-based;
   no NWS/Open-Meteo/RAWS API client exists.
3. **Crew phone GPS tracking** — nothing anywhere (no location-report
   endpoint, no tracking table). `Technician` table + tech portal JWT are the
   substrate to attach it to.
4. **SMS/push alerting** — explicitly deferred "Phase 2" in `ses.py`; only
   SES email exists.
5. **Long-horizon (72h) alert state machine** — `preposition.py` ladders
   minutes, `gel.py` ladders T-60..T-20; nothing spans days.
6. **Travel-time routing between properties** — dispatch optimizer assumes
   intra-HOA travel is absorbed in prep time.

## Unverified / flagged items

- Physical table names inferred from `template.yaml` `!Sub` patterns +
  memory ("stage=dev"); not confirmed against the live AWS account from here.
- Live API base URL `https://cobz3ddao0.execute-api.us-west-2.amazonaws.com/dev`
  taken from portal HTML; `infra/deployment_manifest.json` confirms wg-api-dev
  bindings as of 2026-06-15 but I did not hit the endpoint.
- SES prod-mode status and current verified sender identity not verified
  (docstrings disagree: `dev.wildfireguardian.io` default vs
  `thewildfireguardian.com` launch notes; memory says wildfireguardian.io
  never existed — treat the env default as stale).
- gel.py coating-life coefficients are RE-DERIVED engineering numbers (the
  as-filed PCT-07 hydraulics were internally inconsistent, per module
  docstring) — fine for scheduling, not a lab-validated effectiveness claim.
- `graphgel_dispatch` optimizer's Δsurv numbers ride the legacy 60/30/10 blend
  (explicitly not migrated to the authoritative 25/75) — recalibrate before
  quoting survivability lift to clients.
