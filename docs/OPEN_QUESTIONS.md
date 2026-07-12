# wgresponse — Open Questions

Every unresolved item from the five research digests (`docs/research/`, 2026-07-06),
deduplicated and grouped. Each item carries an owner tag — **Colin** (business/policy
decision), **supplier** (external data required), **eng** (verify/benchmark/build) —
and the concrete thing that unblocks it. IDs are referenced from README /
ARCHITECTURE / INTEGRATION.

## Product

### P-1 — Gel effectiveness window: 6 h vs 72 h (BLOCKER-GRADE) — supplier
The dispatch placeholder assumes gel stays effective **72 h** after application
(`gel_window_h=72`, full strength 24 h). The wgdefense coating-life model
(`gel.py::coating_life_h`, re-derived engineering numbers) says a hydrogel coat lasts
**~6 h at 25 C / 50% RH / calm**, scaled down by heat/wind — a **12x conflict** in the
parameter that decides the entire dispatch posture: 72 h means calm daylight
deployment days ahead; 6 h means deployment inside the final hours, exactly when the
safety rules bite and crew capacity is scarcest. No measured GraphGel window,
full-strength period, or save-rate exists on disk (only E84 Class A + the ABC7 burn
demo). `gel_effectiveness=0.85` is likewise invented.
**Unblocks:** supplier/formulator data sheet or test report: effective life vs
temp/RH/wind, re-wet behavior, and any measured save-rate basis. Until then all three
knobs stay loudly flagged and `gel_env_model_enabled` offers the conservative model.

### P-2 — Survivability-lift numbers quoted to clients — eng
`graphgel_dispatch`'s per-home Δsurvivability still rides the legacy 60/30/10
scenario blend; the authoritative engine default is 25/75. Any client-facing "P(save)"
derived from those numbers is stale.
**Unblocks:** rerun lift calibration on the 25/75 engine before wgresponse surfaces
save-probability numbers in alerts or plans.

## Licensing

### L-1 — Watch Duty Enterprise terms — Colin
The only legitimate access path (unofficial app API is ToS-banned: automated +
commercial + redistribution). Dataset list, formats, polling terms, and pricing are
unpublished — Enterprise addon, sales conversation required. Also gates
ALERTCalifornia camera access (embedded in their Pro/Enterprise product) and the
evac-zone / detail-endpoint questions.
**Unblocks:** sales call via `https://watchduty.org/data-api`; signed license + API
docs. Steps in INTEGRATION.md section 6. Until then: WFIGS/FIRIS/CAL FIRE/FIRMS
backbone only, `WatchDutyEnterpriseFeed` stays a stub.

### L-2 — Open-Meteo commercial tier — Colin / eng
Free endpoint is non-commercial only; wgresponse is commercial. Paid-tier price is
ambiguous (pricing page parsed ~$300/mo for 1M calls; historically ~EUR 29/mo).
Self-hosting is a legal alternative (AGPLv3 server, CC-BY-4.0 data).
**Unblocks:** confirm price at open-meteo.com/en/pricing; subscribe (hostname
`customer-api.open-meteo.com` + key) or stand up self-hosted. Not optional — NWS has
multi-hour outage history, the fallback must be production-legal.

### L-3 — OSRM/routing for production (Tier-1) — eng
OSRM public demo server: <= 1 req/s, non-commercial, 10k-duration table cap —
development only. Production routing requires self-hosting (OSRM or Valhalla).
**Unblocks:** decision to move past the Tier-0 travel model; then self-host per
INTEGRATION.md section 7. Not blocking v1.

### L-4 — firelab/behave license — eng
The USFS BehavePlus C++ repo is a planned cross-check source for the FBFM40/Rothermel
table; the repo LICENSE was never read (likely public-domain USFS, unconfirmed).
**Unblocks:** read the LICENSE before vendoring any table/code derived from it.

## Technical

### T-1 — FBFM40 / Rothermel table cross-validation — eng
Table must be imported, not hand-typed: diff pyretechnics vs behave numerically, pin
as fingerprinted data (byte-identity discipline). Assertions owed: pyretechnics loads
are lb/ft^2 (GR2 1-h = 0.0046 — a summarizer once mislabeled tons/acre); spot-check
GR2/SH5/TL3 against RMRS-GTR-153; ELMFIRE `fuel_models.csv` contents unopened;
WAF (Albini-Baughman) formula and the 10-h/100-h EMC multipliers (1.28/1.43) are
from memory, not re-verified (low impact — 1-h dominates).
**Unblocks:** the import + diff + fingerprint test in `wgresponse.fuels` /
`wgresponse.rothermel`.

### T-2 — LFPS AOI limits and throttling — eng
Max AOI acreage per LFPS job and job-rate throttling unverified (test AOI was ~38k
acres, ~60–90 s). Matters for large multi-incident windows.
**Unblocks:** the user-guide PDF limits section, or `helpdesk@landfire.gov`; then
encode a tiling fallback in `wgresponse.terrain`.

### T-3 — Dispatch solver benchmark at N=20 / M=200 — eng
The 5 s wall-clock budget for regret-2 + ILS at 20 crews x 200 jobs is an estimate,
never measured. EDF-only fallback exists if blown.
**Unblocks:** synthetic benchmark harness (seeded scenario, scaling sweep) run
against `wgresponse.dispatch.plan`; tune `ils_iterations`/budget from data.

### T-4 — Ensemble quantile calibration — eng
`wind_perturb_frac=0.25` / `moisture_perturb_frac=0.20` and the lead-time growth
curve are design guesses; the digests' `eta_uncertainty_by_lead` (0.15@6h -> 0.35@48h)
is a placeholder. P10 is load-bearing for crew safety — it should be *verified*
pessimistic.
**Unblocks:** hindcast verification: run the regression anchors (Camp, Tubbs, East
Troublesome — note East Troublesome 24-h totals conflict 87k–140k ac, anchor on the
~6,000 ac/h rate) with archived weather; check observed arrivals fall at/inside the
modeled P10 the intended fraction of the time.

### T-5 — Valhalla image pin — eng
Preferred Tier-1 router (`exclude_polygons` = native fire-avoiding routes), but the
exact official Docker image name/tag was not verified (historically
`gis-ops/docker-valhalla`, since moved under the valhalla org).
**Unblocks:** confirm at github.com/valhalla/valhalla + valhalla.github.io before
pinning anything in infra.

### T-6 — Feed details to probe during implementation — eng
Deduped small-print items, none blocking design: FIRIS ComboLayer field schema +
per-source refresh cadence (query layer 0 `?f=pjson`); CAL FIRE IncidentApi update
SLA (duty-officer cadence, assume hours); FIRMS exact URT latency figure + current
CSV column list; Watch Duty geo_event `external_id`/`incident_id` -> IRWIN mapping
semantics (post-L-1); WFIGS perimeter-content freshness by source.
**Unblocks:** live probes while building `wgresponse.feeds`; record findings as
adapter docstrings.

### T-7 — Travel-model calibration — eng
`circuity=1.35` (literature 1.3±0.1; canyon terrain 1.5–2.0) and
`fire_traffic_factor=1.4` (pure heuristic, no source) are uncalibrated.
**Unblocks:** one-time offline script sampling ~200 OSRM routes per service area to
fit local circuity; after-action GPS traces for the traffic factor.

### T-8 — Slope-source reconciliation — eng
3DEP ImageServer `Slope Degrees` (visualization mosaic) vs LANDFIRE `LF2020_SlpD`
were never numerically compared. wgresponse uses LFPS SlpD; only relevant if any
component ever mixes in 3DEP slope.
**Unblocks:** one-off comparison on a shared AOI if/when 3DEP slope is introduced.

### T-9 — Estate facts assumed, not verified live — eng
Physical DynamoDB table names inferred from `template.yaml` `!Sub` patterns
(stage=dev); live API base taken from portal HTML (manifest dated 2026-06-15, not
hit).
**Unblocks:** one read-only AWS session confirming table names + endpoint before
wiring `DynamoClientRegistry`.

## Operational

### O-1 — SMS provider — Colin
No SMS anywhere in the estate (explicitly deferred "Phase 2" in `ses.py`);
EXTREME_24H alerts realistically need it. Candidates: SNS, Pinpoint, Twilio — all
slot into the existing enqueue->worker pattern behind `SmsNotifier`. 10DLC/A2P
registration lead time argues for deciding early.
**Unblocks:** provider decision + account/registration; then implement `SmsNotifier`
against the queue.

### O-2 — SES production access + sender identity — eng / Colin
SES may still be sandboxed (verified recipients only); the code default sender
(`no-reply@dev.wildfireguardian.io`) is stale — real domain is
`thewildfireguardian.com` (GoDaddy DNS, no user access recorded).
**Unblocks:** SES prod-access request + DKIM on `thewildfireguardian.com`; until
then alert emails only reach verified addresses.

### O-3 — Crew-location retention & consent policy — Colin
Phone-GPS beacons are employee PII. No policy exists for retention window, who can
see live positions, or consent language in crew onboarding.
**Unblocks:** a written policy (suggested default: live positions ops-only, raw
beacons purged after incident close + N days) + consent text in the tech-app login
flow; then `wgresponse.crews` persistence can be finished.

### O-4 — Crew model & shifts — Colin / ops
Is a "crew" one technician (`tech_id`) or a grouped unit with a vehicle? What is
`shift_end`, max staging wait, callout procedure when the strategic tier flags a
staffing gap at 72 h? The dispatch math needs these as inputs, currently defaulted.
**Unblocks:** ops decision; encode as config + roster fields on
`wg-technicians-dev`.

### O-5 — Safe-zone catalog — Colin / ops
Egress checks (contracts Safety Rule 3/4) require a `SafeZone` list per service
area — staging areas, town centers, verified refuge. No source exists: nobody has
picked whether these are hand-curated per client region, derived from county
evacuation-center data, or both; nor who maintains them as incidents evolve.
**Unblocks:** initial hand-curated list per active service area (Colin/ops) + an
update procedure; engine treats it as config data.

### O-6 — Client priority & value mapping — Colin
`ClientProperty.priority_weight` (contract tier / strategic weight) and `value_usd`
need a policy mapping from profile fields (`protection_level`, home-value estimate).
This directly orders who gets served when not everyone can be.
**Unblocks:** a written mapping table (business decision, not engineering); default
until then: weight 1.0 for all enrolled clients, ZIP-prefix value estimate.
