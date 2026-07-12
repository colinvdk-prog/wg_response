# wgresponse — Integration into the WG estate

Wiring plan from this standalone repo into the existing Wildfire Guardian production
estate (region us-west-2, stage `dev`). Source inventory:
`docs/research/reusable_assets.md` (2026-07-06 recon of the firerisk engine repo and
`wg_total_defense`). Paths below reference:

- **Repo A (engine):** `C:/Users/colin/projects/computer vpp demonstration/firerisk_v2-engine-hardening`
- **Repo B (wgdefense):** `C:/Users/colin/wg_total_defense`

## 1. Protected clients — `DynamoClientRegistry`

The "protected client list" already exists as data; wgresponse only reads it.

- **Table:** `wg-consumer-profiles-dev` (env `DDB_CONSUMER_PROFILES`; SAM
  `infra/template.yaml`, env-var block lines ~603–641). Golden record
  `ConsumerProfile` (Repo A `firerisk/persistence/models.py` ~line 546) carries
  everything `contracts.ClientProperty` needs: `primary_address`, `zip`, `lat`,
  `lon`, **`graphgel_enrolled: bool`** (line 589), `protection_level`,
  `current_survivability`, `hazard_zone`, consent/suppression block, contact fields.
- **Selection:** the segment rule already exists —
  `firerisk/consumer/segments.py:261`:
  `SegmentRule(field="graphgel_enrolled", op=EQ, value=True)`. The registry is one
  segment query away. Canonical upstream flag: `Homeowner.fr_graphgel_enrolled`
  (models.py:136), projected into the profile by
  `firerisk/consumer/profile.py::build_consumer_profile` (line 298).
- **Mapping:** `client_id` = profile `email` (PK); `loc` = `(lat, lon)`;
  `priority_weight` derived from `protection_level` / contract tier (mapping TBD,
  default 1.0); `value_usd` from the ZIP-prefix home-value estimator if no explicit
  value. Profiles with missing/unpinned lat/lon must be excluded loudly (the estate's
  geocode discipline is geocode-once-and-pin via `firerisk/api/geocode.py`,
  `FIRERISK_GEOCODER=aws_location`, precision-gated — do not re-geocode in
  wgresponse).
- **Access pattern:** read-only scan/query of the segment on a cache TTL (client list
  changes daily, not per cycle). Copy Repo A's repository factory verbatim
  (`firerisk/persistence/dynamodb.py`): Dynamo when `DDB_*` env vars are set, else
  in-memory — that is exactly the `DynamoClientRegistry` / `MockClientRegistry`
  split in contracts.

## 2. Crew identity & auth — tech-portal JWT + `wg-technicians-dev`

- **Roster:** `wg-technicians-dev` (`Technician`, PK `tech_id`, PD-managed, optional
  `pin_sha256`). WG Response crews extend this table; do NOT invent a new roster.
  `Crew.crew_id` = `tech_id` (or a crew grouping keyed to tech ids — grouping model
  is an open item, O-4).
- **Auth for the beacon/crew app:** the existing tech login IS the crew-app auth:
  `POST /api/portal/auth/login` (`firerisk/api/routes/portal.py::portal_login`),
  role `tech` via roster-validated `tech_id` + optional PIN (salted SHA-256), mints
  a 12 h HS256 JWT `{sub, scope:"portal", role}` signed with Secrets Manager
  `jwt/signing-key`; rate-limited 20 attempts / 15 min / IP; fail-closed.
  `POST /api/beacon` should accept that Bearer token; `X-Internal-API-Key` remains
  the staff/ops path (`wgresponse.server` already speaks it).
- **Beacon privacy:** phone GPS of employees is PII — retention window and consent
  language are an open policy item (O-3) before production ingest.

## 3. Notifications — SES via SQS worker, consent-gated; SMS undecided

- **Email path (exists, reuse):** producer render-validates, then **enqueues a job
  on `BackgroundJobsQueue` (SQS, env `BACKGROUND_QUEUE_SQS_URL`); the worker Lambda
  `wg-assess-worker-dev` (which holds the SES IAM) sends** (`ses:SendEmail` /
  `SendRawEmail`, template.yaml ~762/~877). Each email type behind its own
  default-OFF flag (pattern: `WG_EMAIL_*`). wgresponse's `SesNotifier` is a stub
  that formats `AlertEvent` -> queue message; the sending stays in the estate worker.
- **Consent gate (mandatory):** call
  `firerisk/consumer/consent.py::is_mailable(profile, purpose)` with a **service**
  purpose. Fire-arrival alerts are operational/service messages: allowed under
  marketing unsubscribe, still blocked by BLOCK-ALL suppressions (hard_bounce,
  complaint, manual, ccpa_delete). Never bypass this.
- **Caveats:** SES production-mode access is still an open estate item (sandbox =
  verified recipients only); sender identity default in `ses.py`
  (`no-reply@dev.wildfireguardian.io`) is stale — the real domain is
  `thewildfireguardian.com`.
- **SMS: does not exist anywhere.** `ses.py` explicitly defers it ("Phase 2"); no
  SNS/Pinpoint/Twilio code in either repo. EXTREME_24H alerts arguably require SMS.
  Provider choice (SNS vs Pinpoint vs Twilio) is an open decision (O-1); whatever is
  chosen slots into the same enqueue -> worker pattern behind `SmsNotifier`.

## 4. Deploy options

Two viable shapes; **recommendation: (b) now, (a) later.**

- **(a) Converged:** wgresponse rides inside the existing `wg-firerisk` Lambda image
  — one more router/handler in the image, one more `AWS::Serverless::Function` block
  in `infra/template.yaml` pointing at the same `EcrImageUri` with a different
  `ImageConfig` command (the estate is already one-image-many-Lambdas). Build via
  **AWS CodeBuild** (`buildspec.yml`, classic builder `DOCKER_BUILDKIT=0` — Lambda
  rejects buildx OCI-index images; local Docker->ECR push is broken) -> ECR
  `296920174396.dkr.ecr.us-west-2.amazonaws.com/wg-firerisk-dev:<tag>` -> `sam
  deploy`. The fire-watch polling loop becomes an EventBridge-scheduled Lambda in
  the same template.
- **(b) Separate SAM stack sharing tables:** wgresponse ships as its own small stack
  (its own image or zip Lambda + EventBridge schedule + API), with read IAM on
  `wg-consumer-profiles-dev` / `wg-technicians-dev`, write on its own new tables
  (beacons, alert records, plans), and enqueue rights on `BackgroundJobsQueue`.
- **Why (b) first:** blast-radius. It is launch week and the firerisk repo/image is
  mid-flux (tag churn `v4.53.0-l41-...`); an operational-safety system should not
  share a deploy artifact with a repo taking daily unrelated changes, and wgresponse
  iterates fastest with its own pipeline. **(a) is the convergence target** once both
  stabilize — one image, one auth surface, one ops console. The contracts-first
  seam makes the move mechanical.
- Live API base for the estate (portal HTMLs, deployment_manifest 2026-06-15):
  `https://cobz3ddao0.execute-api.us-west-2.amazonaws.com/dev`.

## 5. Relationship to existing code — supersedes / ports

| Existing asset | Relationship |
|---|---|
| `graphgel_dispatch/` (Repo A) — MMKP ILP optimizer (PuLP/CBC), single-HOA, no travel matrix, no live crews, `POST /api/dispatch/optimize` | **Superseded by `wgresponse.dispatch`** for event response: wgresponse adds routing (sequential stops), live crew GPS, arrival-time deadlines, corridor safety. `graphgel_dispatch` remains valid for its original scope — pre-event, intra-HOA surface prioritization under a gallon/time budget. Port back candidates: nothing structural; keep its surface-priority lift curve (`SURFACE_PRIORITY`, eaves-first) and `GAL_PER_M2_FULL=0.84` as per-stop servicing detail when WG Response plans within a property. Its lift numbers ride the legacy 60/30/10 blend (authoritative is 25/75) — recalibrate before quoting Δsurvivability. |
| `firerisk/fire/propagation.py` (Repo A) — Finney-MTT arrival grids + `propagation_jit.py` | **Same algorithm family; NOT superseded.** It is the consumer-scoring engine: numpy-based, byte-identity-pinned, scenario-driven (synthetic `WeatherScenario`, no live feeds). `wgresponse.spread` is the operational twin: stdlib, live-weather-driven, time-dependent, ensemble. Keep both; cross-validate arrival fields on a shared synthetic scenario (factor-of-agreement regression). Port INTO wgresponse: the burned-perimeter fuel de-rate (`landfire.py::recent_fire_year` + `reconcile_disturbed_fuel`) and, later, spotting/crown modules. Port BACK to firerisk: nothing until wgresponse's Rothermel table passes the two-source cross-check. |
| wgdefense `gel.py` (Repo B) — coat window opens 60 min and hard-stops 20 min before arrival, `coating_life_h` (~6 h @ 25 C), dosing | Source of the `gel_env_model_enabled` model and the structural template for the deploy scheduler. Its 6 h coating life vs the 72 h dispatch placeholder is open question P-1. Coefficients are re-derived engineering numbers, not lab-validated. |
| wgdefense `preposition.py` (Repo B) | Shape donor for the alert ladder (stage thresholds, degraded-mode confidence penalties). No runtime dependency. |
| `firerisk/fire/crew_response.py` (Repo A) — CalFire-DINS-calibrated crew timing (`response_time_s=1800`, full benefit at ~30 min margin) | Calibration cross-check for wgresponse's 30/30 budgets; not imported. |
| `graphgel_dispatch/frontend/index.html` (Repo A) — Leaflet 1.9.4 + OSM tiles dashboard | Seed for `GET /console` (add perimeters, isochrones, crew dots, alert feed). Only real map UI in the estate. |
| `GraphGelEvent` table (`wg-graphgel-events-dev`) | Natural persistence target for completed deployment logs (`sqft_applied`, `photo_ids`, `dispatched_at`) — wgresponse writes one record per completed stop. |

## 6. Watch Duty Enterprise onboarding (the only legitimate path)

The unofficial app API is ToS-banned for automated/commercial use (triple breach:
automated + commercial + redistribution). `WatchDutyEnterpriseFeed` stays a stub
until this completes:

1. Contact Watch Duty sales (Professional Solutions / "Data API" page:
   `https://watchduty.org/data-api`) — the API is an **Enterprise addon**; datasets,
   formats, and pricing are unpublished. WG (commercial fire-protection company)
   fits their stated customer profile (utilities, EM, insurance, critical infra).
2. Negotiate: API addon scope (incidents, perimeters, evac zones, camera embeds?),
   polling/redistribution terms, SLA. Their stack is SOC 2 Type II; expect a
   security questionnaire.
3. On signature: obtain credentials + schema docs, implement
   `WatchDutyEnterpriseFeed.fetch_active` against the real endpoints, map their ids
   to IRWIN ids in `fuse_incidents` (their geo_events carry `external_id` /
   `incident_id`; mapping semantics unverified — confirm during integration).
4. Until then the automated backbone is WFIGS (points + perimeters) + FIRIS
   ComboLayer (CA) + CAL FIRE IncidentApi (CA metadata) + FIRMS (growth) — all
   public, all keyless except FIRMS.

Owner: Colin (business/sales conversation). Tracked as L-1 in OPEN_QUESTIONS.md.

## 7. Key / account acquisition checklist

| Item | How | Cost | Blocking |
|---|---|---|---|
| FIRMS MAP_KEY | "Get MAP Key" form at `https://firms.modaps.eosdis.nasa.gov/api/area/` — email signup, instant | free | `FirmsFeed` live path |
| LFPS | no key, no auth (`https://lfps.usgs.gov/api/job/submit`) — but confirm max AOI acreage + job throttling with `helpdesk@landfire.gov` before requesting >~1 deg boxes | free | large-window terrain tiles (T-2) |
| NWS | no key; **`User-Agent` header required** — `ResponseConfig.user_agent` (`wg-response/0.1 (ops@thewildfireguardian.com)`) | free | all NWS calls |
| Open-Meteo commercial | subscribe at open-meteo.com/en/pricing -> `customer-api.open-meteo.com` + `apikey` (price ambiguous: page parsed ~$300/mo, historically ~EUR 29/mo — confirm) | paid | weather fallback in production (L-2) |
| Watch Duty Enterprise | section 6 | negotiated | premium intel layer (L-1) |
| OSRM/Valhalla self-host | Tier-1 routing upgrade only; demo OSRM server is non-commercial, dev-only | infra cost | not blocking v1 (Tier-0 travel model ships first) |

## 8. Environment variables (wgresponse-side, proposed)

| Var | Purpose |
|---|---|
| `WGRESP_DEV_OPEN` | `1` = disable auth locally (contracts server seam; mirrors `WG_DEV_OPEN`) |
| `INTERNAL_API_KEY` | staff/ops API key (same convention as estate) |
| `DDB_CONSUMER_PROFILES`, `DDB_TECHNICIANS` | table names; presence flips registries from mock to Dynamo (factory pattern) |
| `BACKGROUND_QUEUE_SQS_URL` | notification enqueue target |
| `FIRMS_MAP_KEY` | FIRMS area API |
| `WGRESP_OPEN_METEO_KEY` | commercial Open-Meteo (absent = NWS only) |
