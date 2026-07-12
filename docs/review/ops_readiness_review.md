# WG Response — Operational Readiness Review

**Date:** 2026-07-09 · **Reviewer lane:** operational readiness ("could we trust
this during a real fire tomorrow?") · **Repo state:** 453/453 tests green
(16 s), mocks + live read-path adapters, no deployment artifacts.

**Method:** code reading of the service seams (`threat.py`, `alerts.py`,
`server.py`, `cli.py`, `clients.py`, `notify.py`, `crews.py`, feeds/weather/
terrain adapters) + live READ-ONLY smoke tests against the real public
services (LFPS, NWS, WFIGS, FIRIS, CAL FIRE) + local provider-failure drills.
Scripts and raw outputs in `scratch_review/`. No AWS access, no writes to any
live system.

**Verdict: NOT deployable tomorrow — but closer than expected.** The full
live chain (feeds→NWS→LFPS→spread→alerts→dispatch) composes and ran a real
cycle against a real incident today (warm cycle 10.5 s; a live EXTREME alert
fired and dispatch correctly refused on `no_safe_egress`). The physics/
dispatch core is in far better shape than the service shell around it. Four gaps block a real
deployment: (1) a provider outage actively de-escalates alerts and sends
clients a false "All clear" within ~10 minutes; (2) nothing in the repo runs
`ThreatMonitor.cycle()` — there is no daemon, scheduler, or deploy artifact;
(3) all alert/crew/plan state is in-memory and dies with the process; (4) the
live terrain path fails on a real LFPS tile with the documented dependency
set (fixed only by an undocumented third dep). Everything else is a
hardening list.

---

## 1. Live smoke-test results

### 1.1 LFPS terrain (the untested seam) — FAIL first, PASS after an undocumented dep

`LfpsTerrain` end-to-end for a ~10×10 km Malibu AOI
(`-118.75,33.99 → -118.64,34.08`, 90 m grid) — `scratch_review/smoke_lfps.py`:

| Stage | Result |
|---|---|
| submit | 200 OK in 1.0–1.3 s, jobId returned |
| poll | job Succeeded in ~1–2 min (status calls themselves took 0.3–17 s each) |
| download | 0.6 s, 227,686-byte zip (multiband GeoTIFF + .tfw) |
| decode, run 1 | **FAILED** — `ValueError: <COMPRESSION.LZW: 5> requires the 'imagecodecs' package` |
| decode, run 2 (after `pip install imagecodecs`) | OK — 111×113 grid, 4 bands |
| total wall-clock | 200.1 s (failed run), **165.3 s** (successful run) |

**P0 finding (OPS-2):** real LFPS GeoTIFFs are **LZW-compressed**; `tifffile`
alone cannot decode them. The optional-dep boundary
(`terrain.py:410-422 _decode_geotiff`) imports only `numpy`+`tifffile`, so
its "clear RuntimeError" (`pip install tifffile numpy`) never fires — instead
a raw `ValueError` escapes from deep inside tifffile *after* the job has been
submitted, polled, and downloaded. Consequences: (a) the documented install
instruction is wrong for the live path; (b) the error type violates the
module's own contract (RuntimeError); (c) because the zip is not cached
before decode (`terrain.py:295-310`), every decode failure wastes a complete
LFPS job and a re-run resubmits from scratch.

**Georegistration (Output_Projection=4326 concern): VERIFIED.**
Fuel-code census over the tile: `98`(water)×6090, `122`(GS2)×1867,
`145`(SH5)×1089, `91`(urban)×1079, `142`(SH2)×947, `165`(TU5)×323 — a
credible Malibu coast mix. Known-coordinate checks:

- ocean point (34.000, −118.700): fuel **98** (water), elev 0 m, slope 0 — PASS
- inland Santa Monica Mtns (34.070, −118.700): fuel **165** (TU5), elev 386 m,
  slope 32° — PASS (burnable, mountainous)

The .tfw-based nearest-neighbor registration is not axis-flipped or offset at
90 m scale.

**`spread.compute_arrival` accepts the live tile:** yes — 12 h horizon, one
inland ignition, 9 m/s mock wind: 167/12,543 cells reached, 0.03 s compute,
no exceptions. (Whether 167 cells is *enough* spread is the physics lane's
question, not this one.)

### 1.2 NWS weather — PASS

`NwsWeather.forecast(Malibu, 96 h)` — `scratch_review/smoke_nws.py`:

- **2.8 s** wall-clock (points + gridpoint calls), source=nws
- 97 hourly samples, uninterrupted (0 non-hourly gaps), **span 96 h**
- gust/dir/RH/temp present on **every** sample; values sane (wind 0.5–3.6 m/s,
  gust ≤5.1, RH 66–99% — a marine-layer day)
- **72 h+ horizon decodes: YES** (samples reach +88 h relative to now)
- `red_flag_active`: **0.3 s → False** (works; no active RFW today)

One shrinkage note: the grid series starts at the first grid hour, which was
**8 h in the past**, and `forecast()` clips at `t0_grid + horizon_h`
(`weather.py:201-202`) — so a 96 h request yielded only 88 h of *future*
coverage. With `cfg.horizon_h = 96` and a stale grid the effective lookahead
can drop a few hours below the alert ladder's 72 h + margin. Minor today;
worth clamping to `now + horizon_h` eventually.

### 1.3 Feeds freshness — PASS with two real findings

One CA-wide sweep (`scratch_review/smoke_feeds.py`), 2026-07-09 ~13:00 PT:

| Feed | Wall-clock | Incidents | Perimeters | updated_at age (fresh / median / stalest) |
|---|---|---|---|---|
| WFIGS | 3.1 s | 106 | 7 | **0.31 h** / 42 h / 335 h |
| FIRIS | 1.4 s | 175 | 175 | 19 h / **1,176 h (49 days)** / 4,412 h (~6 months) |
| CAL FIRE | 0.4 s | 6 | 0 | 7.3 h / 10.8 h / 12.8 h |
| FIRMS | n/a | — | — | **constructor requires MAP_KEY** (see ticket) |

- **Fusion provenance survives:** 241 fused incidents, 4 with multi-source
  `source_ids` (`['firis','wfigs']` joined on IRWIN id) — e.g. VOLCANO,
  3-1 PIT. Field-level merge behaved (newest wins, acreage/containment
  filled).
- **P1 finding (OPS-6):** the FIRIS "current" ComboLayer retains months-old
  perimeters and `FirisFeed` marks everything `active=True`
  (`feeds.py:386`); WFIGS likewise (`feeds.py:282`). `fuse_incidents` has no
  staleness/containment gate and `ThreatMonitor` assesses every `active`
  incident. Net effect in production: **six-month-old perimeters near a
  client are threat-assessed every cycle** — wasted LFPS jobs and a live
  false-NOTICE risk from long-dead fires.
- **P1 finding (OPS-16, fusion identity misses):** the same physical fire
  shows up as **four separate incidents** in the fused output around the
  VOLCANO fire (Riverside Co.): WFIGS+FIRIS `VOLCANO` (IRWIN-joined, correct),
  CAL FIRE `Volcano Fire` (CAL FIRE exposes no IRWIN id and the name-join
  requires *identical* names — `"volcano fire" != "volcano"`), and two FIRIS
  mission-coded perimeter rows `CA-RRU-VOLCANO-N57B` / `CA-RRU-VOLCANO-N50X`.
  Each duplicate is assessed independently: 4 terrain domains, 4 spread
  ensembles, and — because alert pairs are keyed `(client_id, incident_id)` —
  **up to 4 parallel alert streams (and later, notification streams) per
  client for one fire.**
- **FIRMS:** `FirmsFeed(map_key)` is honest about needing a key; the CLI's
  bare `cls()` construction dies with `TypeError` (caught, printed to
  stderr), so `pull-feeds` silently runs without hotspot data. Signup is a
  5-minute form (see ticket OPS-13).

### 1.4 End-to-end live dry run — one real `ThreatMonitor.cycle`

Composed live WFIGS+FIRIS+CALFIRE (bbox-wrapped) + live NWS + live LFPS +
`MockClientRegistry` (3 fake clients at real La Cresta / Santa Rosa Plateau
coords) + `CrewTracker` (2 fake crews beaconed at Murrieta/Temecula) + the
real `dispatch` module, against the real **VOLCANO** incident
(33.531, −117.298, Riverside Co.) — `scratch_review/smoke_e2e.py`.

**It works — the full live chain composes and runs.** Results:

- **First attempt (±0.25° AOI): runaway.** The box held **20 fused "active"
  incidents, every one within 60 km of a client → 20 sequential LFPS jobs**;
  the cold cycle had not finished after 25 min (killed). 13 of the 20 were
  45 h–95 days stale (VISTA 83 days, SERENA 95 days). This is OPS-6 + OPS-10
  + OPS-16 compounding: with the default config, one cycle in a busy service
  area takes ~an hour against a 300 s poll period.
- **Tightened rerun (±0.08° AOI, the VOLCANO cluster only):**
  - **COLD cycle: 434.2 s** (4 LFPS jobs — one per duplicate incident — +
    NWS + 4 spread ensembles + alert eval + dispatch). No exceptions,
    `notes: 0`.
  - **WARM cycle: 10.5 s** (terrain ascii-cached; ~4 s feeds + ~3 s NWS +
    spread/alerts/dispatch). Steady-state is fine once tiles are cached.
  - 4 incidents = the **same physical VOLCANO fire four times** (see OPS-16):
    fused WFIGS+FIRIS `VOLCANO`, CAL FIRE `Volcano Fire`, FIRIS mission rows
    `firis:192`/`firis:195`. 12 assessments (3 clients × 4).
  - **Live alert fired:** `fake-lacresta` (1.7 km from the perimeter) went
    NONE→EXTREME_24H with p50=3.3 h / p10=3.2 h — **twice**, once each from
    the two *stale* FIRIS mission perimeters, while the fused fresh VOLCANO
    row assessed unreached (different ignition geometry). Two notifier sends
    for one fire = OPS-16's alert-spam mechanism observed live.
  - **Dispatch ran and correctly refused:** 2 jobs entered the planner
    (EXTREME pairs), plan returned 0 stops / 2 unserved with reason
    **`no_safe_egress`** — the hard safety rules held against a 3 h P10
    arrival with fresh crew beacons (Murrieta/Temecula). The refusal reason
    is only visible because the smoke script printed it; no operator surface
    shows unserved reasons today.

**Composition finding (OPS-5), independent of the run outcome:**
`ThreatMonitor._fetch_and_fuse` calls `feed.fetch_active()` **with no bbox**
(`threat.py:139`) and `ResponseConfig` has no AOI/service-area field — an
unwrapped production monitor pulls the **entire US** every cycle and assesses
every active incident in the country. The e2e script had to wrap each feed in
a `_BBoxFeed` adapter to be runnable. A service-area bbox must become config.

### 1.5 Local failure drills — the safety-critical result

`scratch_review/drill_failures.py`: escalate one client to **EXTREME_24H**
with healthy mocks, then kill one provider and run 4 more cycles
(300 s period). Result — **identical for all four kill switches** (feed→`[]`,
weather raises, terrain raises, registry raises):

```
after healthy phase:  states={('client-A','drill-fire-1'): 'extreme_24h'}
after 4 dead cycles:  states={('client-A','drill-fire-1'): 'cleared'}
EVENT emitted during outage: extreme_24h->cleared
  msg='All clear for Drill Client: no projected fire arrival within the 72h alert window...'
notifier sends total: 2   (the EXTREME alert, then the false All-clear)
```

And for the **dead-feed** case specifically: `notes: (NO NOTES)` on every
dead cycle — zero trace of degradation anywhere.

## 2. The silent-degradation answer (explicit)

**Worse than silent-stale: a provider outage actively de-escalates and
notifies clients "All clear" while the fire is still coming.**

Mechanism: `AlertEngine.evaluate` treats a tracked pair *absent from this
cycle's assessments* as a **clear evaluation** (`alerts.py:147-151`:
`a is None → clear_now = True`), advancing `clear_streak`; after
`clear_cycles = 2` consecutive absences the pair transitions
EXTREME_24H→CLEARED (`alerts.py:195-196`) and the notifier sends the
"All clear" message (`alerts.py:265-269`). Every provider failure funnels
into that same absence: weather/terrain/registry exceptions become
"assessment skipped" notes (`threat.py:110-113`) with **no assessment**, and
a feed transport failure returns `[]` **by documented design**
(`feeds.py:24` — "a dead feed must not kill the alert loop"), which
`_fetch_and_fuse` cannot distinguish from "no fires in America"
(`threat.py:128-155` — no note is recorded for an empty batch).

At defaults (`poll_period_s=300`, `clear_cycles=2`) the false All-clear fires
**~10 minutes into any outage**. The `notes` that do exist are per-cycle,
in-memory, and rendered nowhere (no live console wiring, no alarm), so an
operator has no signal that the ladder went blind before it lied.

The engine currently cannot represent the distinction that matters:
*"assessed and positively clear"* vs *"not assessed this cycle."* Fixing that
distinction is the single highest-value change in this review (ticket OPS-1).

## 3. Service gaps (code-reading)

### 3.1 Persistence — everything dies with the process
- `AlertEngine._records` + `_deesc_streak` (alerts.py:79-83),
  `CrewTracker._crews`/`_history` (crews.py:30-31), `ThreatMonitor._incumbent`
  (threat.py:85), `LogNotifier.sent` — all in-memory only. The only disk state
  in the whole system is the LFPS ascii tile cache and sim output JSONs.
- Restart consequences: every pair re-enters NONE → **duplicate NOTICE/WATCH/
  EXTREME notifications re-fire** for every active threat; IMPACTED terminality
  is forgotten; de-escalation hysteresis resets; the dispatch incumbent (churn
  damping) is lost → plan churn on restart; crew positions blank until next
  beacon.
- Needs pinning: alert records + streaks (per pair), notification send log
  (dedupe/idempotency), latest plan + incumbent, crew last-beacons (short TTL),
  cycle heartbeat. A single small DynamoDB table (or a local JSON snapshot per
  cycle for the interim) covers all of it — the records are dataclasses
  already serialized by `sim.jsonable`.

### 3.2 Scheduling — nothing runs the loop
- `cli serve` never calls `cycle()` — `--demo` replays a canned sim
  (`cli.py:180-243`); without `--demo` the server serves an empty state dict.
  `ThreatMonitor` is referenced only by `bench/bench_cycle.py` and tests.
- No `watch`/daemon subcommand, no EventBridge schedule, no SAM template, no
  Dockerfile, no requirements.txt, no CI (`Glob` over the repo: zero
  deployment artifacts).
- The INTEGRATION.md deploy shape (separate small SAM stack, option b) is a
  plan on paper only.

### 3.3 Observability — logging exists, nothing else
- Module loggers with good messages exist throughout (feeds warn with
  exc_info, alerts log transitions). But: no metrics, no cycle heartbeat, no
  per-feed freshness gauge, no dead-feed alarm, and `CycleResult.notes` are
  transient in-memory strings.
- `/healthz` (server.py:142-144) is pure liveness — it returns `{ok:true}`
  even if no cycle has run for hours (the server doesn't know the monitor
  exists). There is no "last successful cycle age" surface anywhere.
- The ops console is demo-only: nothing wires live `CycleResult`s into
  `state_provider`.
- Minimum bar for on-call: persisted per-cycle summary (t, incidents, assessed,
  events, notes, per-feed batch size+freshness, wall-clock), `/healthz`
  reflecting last-cycle age, and one alarm on "no successful cycle in N min"
  + one on "feed empty/stale for M cycles".

### 3.4 Notifications — stubs, and the failure mode is drop-and-log
- `SesNotifier`/`SmsNotifier` raise `RuntimeError` by design (notify.py:88,105
  — correctly loud). But `AlertEngine._notify` catches **all** exceptions and
  logs (`alerts.py:277-295`) — the state transition is already recorded, so a
  failed send is **at-most-once with no retry, no queue, no dead-letter, no
  send log**. Wiring SES today would make alert delivery best-effort-once.
- Consent gate (`is_mailable`, INTEGRATION.md §3) not implemented; the
  registry maps every enrolled profile regardless of `suppressed`/
  `suppression_reason` (ccpa_delete included) — must land before any real
  send.
- SES sandbox + stale sender identity + no SMS provider are known estate items
  (O-1/O-2).

### 3.5 Crew beacon auth
- `X-Internal-API-Key` single shared key, fail-closed, constant-time compare
  (server.py:124-131) — fine for staff/ops. The INTEGRATION.md §2 crew path
  (tech-portal 12 h JWT from `portal_login`, roster-validated) is not
  implemented — no JWT verification code exists in the repo, and there's no
  per-crew identity check (any key holder can beacon as any `crew_id`).
- No rate limit on POST /api/beacon; body cap (1 MB) and out-of-order-beacon
  rejection exist. Binds 127.0.0.1 by default (good); TLS is assumed to be
  someone else's problem (fine behind API Gateway/ALB, undocumented).

### 3.6 Config
- Total env surface today: `WGRESP_API_KEY`, `WGRESP_DEV_OPEN` (server.py,
  cli.py). None of the INTEGRATION.md §8 vars exist in code (`FIRMS_MAP_KEY`,
  `DDB_CONSUMER_PROFILES`, `BACKGROUND_QUEUE_SQS_URL`, `WGRESP_OPEN_METEO_KEY`).
- `ResponseConfig` is a dataclass with no env/file loader and no AOI field; no
  subcommand accepts a config file. A deployed Lambda has no way to be
  configured without code changes.

### 3.7 Client registry vs the real table (read-only; no live query performed)
- Field check against the actual `ConsumerProfile` model (firerisk repo
  `persistence/models.py:546-616`): `email` PK ✓, `graphgel_enrolled` ✓,
  `lat`/`lon` ✓, `primary_address` ✓, `name`/`phone` ✓ — **but
  `clients.py:156-157` reads `home_value` / `estimated_home_value`, and the
  real field is `home_value_usd`** (`models.py:571`; `home_value_estimated`
  is a bool flag, not a value). Every live client would silently get the
  $1M default `value_usd` — which feeds dispatch job scoring, i.e. triage
  order. One-line fix; add `priority_weight` derivation while there (O-6).
- Access pattern: full-table `scan` per call with **no cache/TTL**
  (clients.py:171-188), and `ThreatMonitor` calls
  `registry.protected_clients(bbox)` **once per active incident per cycle**
  (threat.py:187). At 241 fused CA incidents and 5-min cycles that is ~70k
  full scans/day of a 10k-row table. INTEGRATION.md §1 specified a cached
  segment read; not implemented.
- Suppression/consent fields are ignored at mapping time (see 3.4).
- Live-schema smoke (T-9) still owed but now one bug is already known
  without it.

## 4. Findings register

Severity: **P0** = blocks a real deployment · **P1** = must fix before
volume/first real client alert · **P2** = hardening. Effort: S <1 d, M 1–3 d,
L >3 d.

| ID | Sev | Finding (evidence) | Fix shape | Effort |
|---|---|---|---|---|
| OPS-1 | **P0** | Provider outage → false CLEARED + "All clear" notification in ~10 min; dead feed leaves NO note (drills §1.5; alerts.py:147-151,195; feeds.py:24; threat.py:128-155) | Ladder must distinguish "positively clear" from "unassessed": absence freezes state (with max-hold TTL + operator alarm) instead of advancing clear_streak; a feed that returns [] when it previously returned >0 incidents in the AOI is a degradation note; CLEARED requires N *assessed* clear evals. Add drill tests. | M |
| OPS-2 | **P0** | Live LFPS decode fails without `imagecodecs` (LZW); wrong error type (ValueError) + misleading message; decode failure wastes the whole job (smoke §1.1; terrain.py:410-422, 295-310) | Add imagecodecs to the optional-dep boundary + docs; catch codec errors → RuntimeError naming all three deps; cache the downloaded zip before decode; add requirements.txt (`tifffile numpy imagecodecs boto3`) | S |
| OPS-3 | **P0** | Nothing runs the loop: no daemon/scheduler; `serve` is demo-replay; ThreatMonitor referenced only by bench/tests (§3.2; cli.py:180-243) | `cli watch --aoi ... --period 300` daemon subcommand wiring ThreatMonitor→server state_provider (one process, both roles), as the pre-Lambda shape; EventBridge+SAM later per INTEGRATION §4b | M |
| OPS-4 | **P0** | All alert/crew/plan state in-memory; restart re-fires every active alert and forgets IMPACTED (§3.1) | Persist alert records + send log + incumbent (DynamoDB table or interim JSON snapshot); load on boot; idempotent notify keyed (pair, state, entered_at) | M |
| OPS-5 | **P1** | No AOI config: monitor fetches feeds with no bbox → whole-US assessment (threat.py:139; e2e needed a wrapper) | `ResponseConfig.aoi: BBox` (or service-area list) plumbed into fetch_active + registry calls | S |
| OPS-6 | **P1** | Stale incidents live forever: FIRIS median perimeter age 49 days, all `active=True`; no freshness/containment gate → false-NOTICE risk + wasted LFPS jobs (§1.3) | Staleness gate in cycle(): skip (with note) incidents with updated_at older than cfg.max_incident_age_h (default ~48–72 h) or containment ≥100%; keep override flag | S |
| OPS-7 | **P1** | Registry maps wrong value field (`home_value` vs real `home_value_usd`) → all clients $1M default, distorting dispatch triage; full scan per incident per cycle, no TTL cache; suppression ignored (§3.7) | Fix field map; cache roster with ~15-min TTL (one scan/cycle max); carry suppressed/consent into ClientProperty for the notify gate | S |
| OPS-8 | **P1** | No heartbeat/metrics/alarm; /healthz is liveness-only; notes transient; console not wired to live cycles (§3.3) | Persist per-cycle summary; /healthz returns last-cycle age + per-feed freshness (503 if stale); alarm on cycle-stall and feed-dead; wire live state_provider | M |
| OPS-9 | **P1** | Alert delivery is at-most-once drop-and-log once a real notifier lands; no consent gate (§3.4; alerts.py:277-295) | SES enqueue via BackgroundJobsQueue per INTEGRATION §3 behind default-OFF flag; send log + retry/DLQ; is_mailable service-purpose gate; keep stubs loud until then | M |
| OPS-10 | **P1** | LFPS cold tile = **2.8 min**, jobs run sequentially, and the domain bbox is derived from perimeter+clients so every perimeter update is a cache miss → a moving fire re-runs a ~3-min job every cycle. Measured blast radius: a 0.5°×0.5° AOI held **20 assessable incidents → the cold cycle did not finish in 25 min** (killed; poll period is 300 s). AOI/rate limits unverified (T-2) | Snap spread domains to a fixed tile grid (fetch-once per region); prefetch service-area tiles off-cycle; concurrent job submission; confirm LFPS limits with helpdesk@landfire.gov | M |
| OPS-11 | P2 | Crew beacon auth: shared key, no per-crew identity, no rate limit; tech-portal JWT path unbuilt (§3.5; INTEGRATION §2) | Accept portal JWT Bearer on /api/beacon (verify HS256 vs jwt/signing-key), crew_id must match token sub; rate-limit per key/IP | M |
| OPS-12 | P2 | Config/env story: 2 env vars total; no ResponseConfig loader; INTEGRATION §8 vars unimplemented (§3.6) | `ResponseConfig.from_env()` + document; wire FIRMS_MAP_KEY, DDB names, queue URL | S |
| OPS-13 | P2 | FIRMS unusable in CLI (needs MAP_KEY); no signup done (§1.3) | 5-min signup at firms.modaps.eosdis.nasa.gov/api/area/ → env FIRMS_MAP_KEY; CLI constructs FirmsFeed only when set (with stderr note otherwise) | S |
| OPS-14 | P2 | NWS horizon clips from grid-start (8 h in the past observed) not from now → effective lookahead shrinkage (§1.2; weather.py:201-202) | Clip at now+horizon; drop past samples (keep current-hour) | S |
| OPS-15 | P2 | No deployment artifacts at all: requirements.txt, Dockerfile/SAM, CI (Glob: zero hits) | Minimal: requirements.txt + SAM stack (option b) + GitHub Actions pytest gate | M |
| OPS-16 | **P1** | Fusion identity misses: CAL FIRE rows carry no IRWIN and the name-join needs identical names ("Volcano Fire" vs "VOLCANO" fails); FIRIS mission-coded rows (CA-RRU-VOLCANO-N57B/N50X) never join either → one real fire = 4 fused incidents = 4× terrain jobs + 4 alert/notification streams per client (§1.4; feeds.py:634-641) | Normalize names before join (strip " fire" suffix, strip `CA-XXX-…-NNNX` mission wrappers); add an origin-distance-only join tier (<2 km) with a note; regression-test on the VOLCANO snapshot | M |

### Blocks a real deployment
OPS-1, OPS-2, OPS-3, OPS-4 — plus one working notification channel (OPS-9)
if "deployment" means clients actually receive alerts, and OPS-5/OPS-6/OPS-16
before leaving it unattended on live feeds (the e2e run showed unfiltered
live data produces hour-long cycles and duplicate EXTREME alerts today).

### Nice-to-have (deploy without, schedule soon)
OPS-10 (until multiple simultaneous fast fires), OPS-11–OPS-15, live-schema
registry smoke (T-9), Open-Meteo commercial fallback (L-2), solver benchmark
(T-3).

## 5. Top 5 sprint candidates (ranked)

1. **OPS-1 — Fail-safe alert ladder + degradation alarm (P0, M).** Absence of
   assessment must freeze, not clear; empty-feed-after-nonempty is a noted
   degradation; CLEARED only from assessed-clear evals; alarm note when any
   pair is held frozen. This is the one that prevents lying to a client
   during an outage. Drill tests from `scratch_review/drill_failures.py`
   become regression tests.
2. **OPS-3 — `cli watch` daemon + live console wiring (P0, M).** A loop that
   calls `cycle()` every `poll_period_s` with an AOI (OPS-5 rides along, S),
   feeds `state_provider` from the latest CycleResult, and exposes last-cycle
   age on /healthz (the first half of OPS-8). Turns the library into a
   service.
3. **OPS-4 — Persist alert/notify/incumbent state (P0, M).** Restart-safe
   ladder + idempotent notifications; DynamoDB table (own stack, per
   INTEGRATION §4b) or interim JSON snapshot — either kills the
   re-alert-everyone-on-restart failure.
4. **OPS-2 + OPS-6 + OPS-16 + OPS-7 — live-data hygiene bundle (P0/P1,
   S+S+M+S).** imagecodecs dep boundary + zip cache + requirements.txt;
   incident staleness/containment gate (kills the 49-day FIRIS zombies and
   the 20-incident hour-long cycle); fusion name-normalization + distance
   join (kills the observed duplicate EXTREME alerts — one fire, four alert
   streams); registry `home_value_usd` fix + roster TTL cache. These are what
   made the live e2e ugly.
5. **OPS-9 — SES alert channel behind a default-OFF flag (P1, M).** Enqueue to
   BackgroundJobsQueue + consent gate + send log with retry — the last mile
   that makes an alert an alert. (Stays OFF until SES prod access, O-2.)

## 6. Artifacts

- `scratch_review/smoke_lfps.py` — LFPS end-to-end + georegistration + spread
  acceptance (re-runs offline via `scratch_review/lfps_cache/`)
- `scratch_review/smoke_nws.py` — NWS forecast + red-flag
- `scratch_review/smoke_feeds.py` — feeds freshness/fusion sweep (+
  `feed_candidates.json`)
- `scratch_review/smoke_e2e.py` — one live ThreatMonitor.cycle (VOLCANO)
- `scratch_review/drill_failures.py` — the four provider-kill drills
- Local deps installed for the live path: `numpy 2.5.1`, `tifffile 2026.6.1`,
  `imagecodecs 2026.6.26` (user-level pip, Python 3.14.5)
