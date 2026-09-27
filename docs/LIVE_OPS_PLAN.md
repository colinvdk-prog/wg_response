# WG Response — Live Data, Live Monitoring, Dispatch-to-Crews Plan

2026-07-11. Goal: take the verified engine (1,149 tests, 0 violations) from
simulator to a live operations loop: real fires in → monitored forecasts →
real crews dispatched, under the same safety laws. Phased so every phase is
independently shippable and fail-safe; each has an explicit exit gate.

## What already exists (verified, on disk)

- Feeds: WFIGS perimeters/points, FIRIS, CAL FIRE, NASA FIRMS (needs MAP_KEY),
  NWS gridpoint forecasts (keyless, UA required) — fused on IRWIN id, staleness
  gates (SEMANTICS 9), zombie-perimeter and clock-clamp guards.
- `watch` daemon (`--aoi` required): full cycle feeds→spread→ETAs→ladder→plan;
  warm cycle ≤10.3s on a real fire (Volcano Fire soak, 0 dup streams, restart
  re-fired nothing).
- `persist.StateStore` (load NEVER raises; re-notify ward for applied-but-unsent
  alerts). SES sender behind `WGRESP_NOTIFY_SES` (fail-closed, sandbox).
- Ops console + public simulator (App Runner, CodeBuild `wg-simulator-image`).
- Estate reuse: `wg-consumer-profiles` (graphgel_enrolled clients),
  `wg-technicians` roster + tech-portal JWT, SES→SQS enqueue pattern,
  20-user staff auth (firerisk l26), DynamoDB single-table patterns.

## Phase 0 — Foundations (immediately, ~1 day)

| # | Ticket | Notes |
|---|---|---|
| 0.1 | `git init` wg_response, initial commit, private remote, CI running pytest | P0 risk from review; nothing else ships before this |
| 0.2 | 24h live soak (Sprint 2 exit gate, command in SPRINT2_PLAN.md) | entry evidence for Phase 1 |
| 0.3 | FIRMS MAP_KEY + NWS User-Agent in Secrets Manager | keyless feeds already fine |

## Phase 1 — Live shadow ops (week 1): see everything, send nothing

Run the watch daemon continuously against a real AOI; alerts and plans are
computed and PERSISTED but not delivered. Humans watch dashboards.

| # | Ticket | Notes |
|---|---|---|
| 1.1 | Deploy `watch` as an always-on worker (App Runner service or ECS Fargate, same pattern as the simulator; it is a loop, not a Lambda) | state → DynamoDB (new `wg-response-state` table) instead of local disk |
| 1.2 | Monitoring dashboard: extend ops console to read StateStore — active incidents, per-client ladder state, ETAs, computed (undelivered) dispatch plans | host behind the existing staff-auth pattern; NOT public |
| 1.3 | CloudWatch alarms: feed staleness > threshold, cycle overrun, ladder freeze events, violation counter > 0 (page immediately — it has never fired) | the "absence is not clearance" law gets an operational mirror |
| 1.4 | Nightly hindcast regression + weekly report (coverage %, late-miss, per-fuel bias) | tracks the 68.8%→90% calibration effort on real incidents |
| 1.5 | LFPS tile pipeline for the live AOI(s) (`fetch_sim_tiles.py` productized, tile provenance pinned) | per-community onboarding artifact |
| 1.6 | Arm `--drop-contained --confirmed-perimeters` on the shadow service before the 7-day gate (contracts SAFETY SEMANTICS 13, built 2026-09-27, default OFF): contained fires leave the map and re-confirmed polygons stop free-running into alerts — every alert of the W6 national run came from exactly those two cases | owner decision; measured in `LIVE_WORLD_PLAN.md` (2026-09-27 section); the off-raster corridor veto stays open |

**Exit gate:** 7 consecutive days: zero missed cycles > 15 min, zero false
"all clear" (spot-audited), hindcast report generated, alarms tested by fault
injection.

## Phase 2 — Live clients & crews (week 2): real registry, real GPS

| # | Ticket | Notes |
|---|---|---|
| 2.1 | Client registry adapter: `wg-consumer-profiles` where `graphgel_enrolled` → ClientProperty (home_value_usd fix already landed; TTL cache exists) | one-way read; consumer DB stays SoR |
| 2.2 | Crew roster from `wg-technicians`; crew phones POST GPS beacons to a small authenticated endpoint (tech-portal JWT) → CrewBeacon ingest (seam exists) | staleness→UNKNOWN already bars dispatch of stale crews |
| 2.3 | Consent + retention policy for crew GPS (retain raw ≤72h post-incident, aggregate after) | legal review before enabling |
| 2.4 | Safe-zone catalog per community (from HOA onboarding, verified on the ground — NOT auto-derived) | fail-closed law already refuses to schedule without zones |

**Exit gate:** live registry + ≥1 real crew beaconing through a full shadow
cycle; plans reference real crews; still nothing delivered.

## Phase 3 — Notifications & dispatch-to-crews (weeks 3–4): close the loop

Human-in-the-loop first: an operator approves each computed plan before send.

| # | Ticket | Notes |
|---|---|---|
| 3.1 | SES production access + verified domain (thewildfireguardian.com already verified in the estate) — client alert emails (NOTICE_72H/WATCH/EXTREME_24H templates exist, incident-named) | |
| 3.2 | SMS decision: **Twilio** recommended over SNS (two-way ack is the requirement; SNS is send-only). Crew dispatch message = itinerary (stops in order, per-stop latest-depart, egress zone, map link) + ACK reply | re-notify ward already exists for unacked/unsent |
| 3.3 | Ack tracking: no crew ACK in 10 min → escalate to operator + auto-replan without that crew (status→UNKNOWN path already bars them) | |
| 3.4 | Operator console: view computed plan → approve/hold per incident; approval stamps the plan for delivery; every withdrawal/relocate (SEM-10) also pushes SMS to the affected crew | the sim's withdrawal observability, delivered |
| 3.5 | Client-facing status page per incident (read-only, tokened link) | reuses alert feed rendering |

**Exit gate:** full drill on a synthetic incident: alert emails delivered,
crew SMS delivered + ACKed, mid-drill buffer tightening produces a withdrawal
SMS ≤1 tick later. Zero un-ACKed dispatches proceed.

## Phase 4 — Hardening & scale (month 2)

- Watch Duty enterprise license (coverage/price/redistribution) — replaces the
  loud-fail stub; Open-Meteo commercial tier as NWS fallback.
- **Gel supplier data** → recalibrate `gel_window_h`/`gel_full_strength_h`/
  `gel_effectiveness`; re-run the coat-early decision for the LIVE default;
  re-run every application-rate table (all currently placeholder-window).
- Hindcast to ≥90% coverage (grass calibration decision + spotting default for
  live), then flip live ROS floors per that verdict — not before.
- Auto-approve tier: after N clean human-approved incidents, low-severity plans
  send without operator hold (EXTREME never auto-approves initially).
- Multi-AOI: one worker per region, shared tables; on-call rotation + runbook.

## Architecture (target)

```
feeds (WFIGS/FIRIS/CALFIRE/FIRMS/NWS)
        │  pull, staleness-gated
        ▼
  watch worker (App Runner/Fargate, always-on)
        │  cycle ≤15 min: fuse → spread ensemble → ETAs → ladder → TOPTW plan
        ▼
  DynamoDB wg-response-state  ◄── crew GPS POSTs (JWT)
        │                          ▲
        ├─► ops dashboard (staff auth, approve/hold)
        ├─► SQS notify queue ─► SES (clients) / Twilio (crews, two-way ACK)
        └─► CloudWatch alarms (staleness, overrun, violations>0)
```

Principles carried from the engine: fail-closed everywhere (no zones → no
schedule; stale feed → freeze + operator event, never "all clear"); every
delivered artifact traces to a persisted plan; the safety laws (P10 deadlines,
eroded corridors, 30-min egress, SEM-10 relocation) are not operator-overridable
downward — an operator can HOLD a dispatch, never extend a deadline.
