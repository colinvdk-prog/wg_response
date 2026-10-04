# Real clients and crews on every map — review and pathway (2026-10-03)

Founder, 2026-10-03: *"perform a complete review and establish a pathway forward
to add individual clients and the locations of our crews to both the overall map
and individual maps or training demos."*

This document is the review of record and the plan. It extends
`docs/LIVE_WORLD_PLAN.md` workstreams W4 (crews and teams), W5 (clients) and W6
(the viewer), and `docs/LIVE_OPS_PLAN.md` Phase 2. Every fact below was checked
on main `40e8a83` and against the live services on 2026-10-03.

## 1. In plain words

**The maps are ready; the data, identity and privacy are not.** The national map
already draws client clusters, clients coloured by alert state, client cards,
crews with status, safe zones, and on the test map the live crews as read-only
ghosts beside the virtual drill crews. Today those layers show 520 synthetic
demo homes and no crews, because:

1. **Both live services are open to anyone.** The watcher and the simulator run
   with `WGRESP_DEV_OPEN=1`, so every data endpoint answers without a key. Real
   client names, coordinates and email addresses cannot go on them as they are.
2. **No real crew can report a position.** The GPS beacon endpoint exists, but
   there is no roster (the `wg-technicians-dev` table is empty), no per-crew
   login, no phone app or tracker, and no consent policy.
3. **Real clients are one switch away, and that switch is unsafe today.** The
   adapter for the consumer database exists (`--registry dynamo`), but turning
   it on would publish each client's email as their map ID.
4. **There are no per-client or per-crew views**, and the per-incident view is
   the national map zoomed in.
5. **Training demos can't load a real community.** The simulator can place homes
   and crews at exact coordinates, but only the onboarding command-line tool
   uses that; the simulator page can't.

**The pathway, in order:** lock the doors (Phase A), put real clients on the live
map (B), put real crews on it (C), add per-incident, per-client and per-crew
views (D), then let trainers load a real community and its real crews into a
drill (E). Scale work (F) follows growth. Drills on the test map pick up real
clients and crews automatically once B and C are live, because the test map
already reads the live roster read-only.

## 2. Review: what exists today

| Area | Clients | Crews |
|---|---|---|
| Data contract | `ClientProperty`: id, location, address, name, value, priority weight, email, phone | `Crew` (id, name, status, last beacon, home base, members) and `CrewBeacon` (position, time, status, accuracy, speed) |
| Real source | `DynamoClientRegistry` over `wg-consumer-profiles-dev` (about 95 profiles; enrolled = `graphgel_enrolled` with lat/lon; read-only; one scan per 5-min TTL; **client id = email**) | none. `wg-technicians-dev` exists and holds 0 rows. `POST /api/beacon` takes beacons with the one shared API key |
| Demo source | `--registry mock-presets`: 40 synthetic homes on each of 13 simulator presets (520, live on the national watcher now) | simulator crews staged at preset safe zones; test-map virtual crews cloned from the live roster |
| Live watcher | registry chosen by `--registry`; the Dynamo table name and region are hard-coded defaults | `CrewTracker`: auto-registers any crew id on its first beacon, rejects out-of-order beacons, marks a crew UNKNOWN when its beacon goes stale (never dispatchable), keeps a 50-beacon trail. **Not persisted**: a restart empties it |
| National map, country zoom | enrolled-home clusters, sized by count, coloured by the worst alert state | crews and safe zones as small markers |
| National map, regional zoom | every client as a dot coloured by alert state; tooltip = name, client id, alert state, P10 and P50 arrival | crews labelled with id and status; tooltip = name, status, last beacon time |
| National map, local zoom | client cards, plan legs, egress zones | as regional, plus plan legs |
| Test map | the live roster read-only; seeded fires; drill-only notifier | live crews drawn as ghosts; virtual crews cloned at drill start, placeable with `/api/test/crews/place`, persisted under `test_world/` |
| Simulator | synthetic WUI homes per preset; `SimSession.configure(client_locs=...)` places homes at exact points, **used only by `tools/onboard.py`** | crews on real roads, user placement snapped to roads, regional reinforcements; `crew_locs=...` parks crews at exact points, **not exposed by `/api/sim/new`** |
| Ops console | one AOI | one AOI |
| Access | one shared `X-Internal-API-Key`; **both App Runner services run `WGRESP_DEV_OPEN=1`**, so `/api/state`, `/api/plan`, `/api/world/summary` and `/api/test/*` are public | same |
| Privacy | `/api/state` client rows carry id, name, lat, lon; with real clients the id is an email. The state file (S3 mirror) and logs carry client ids | beacon trail in memory only; no consent or retention policy (LIVE_OPS 2.3 open) |

Related live findings that real clients depend on:

- **The ember radius is inert in the live watcher.** It runs `--ember-radius
  200`, but the fix that makes it reach the live arrival times is still on branch
  `fix/watch-ember-radius-live` (6092c63), unmerged. Homes on developed cells get
  no arrival time without it (the Rancho Santa Fe finding, W5.3).
- **The national map polls the full `/api/state` every 15 s.** That is fine at
  hundreds of homes and will not be at tens of thousands (Phase F).

## 3. Gaps

| # | Gap | Why it matters |
|---|---|---|
| G1 | No login, no roles; client emails used as public ids; PII in the S3 state mirror and logs | blocks every real-data step |
| G2 | Client source settings hard-coded; no count of enrolled clients with usable coordinates; no display consent flag | can't trust what the map shows or omits |
| G3 | No crew roster, per-device credentials, telemetry client, bases, teams or shifts; live crews lost on restart | no real crew can appear |
| G4 | No per-incident, per-client or per-crew view; no search | "individual maps" don't exist |
| G5 | Simulator page can't load real homes or crews; no anonymised demo mode | training can't use the real community |
| G6 | Full-state polling and full-table scans | fine now, not at scale |

## 4. Pathway

Every engine change keeps the house laws: default-OFF knobs, byte-identical
legacy, the crew-safety invariants untouched, nothing delivered until the
notification decision (LIVE_OPS Phase 3).

### Phase A — Access and privacy (first; blocks everything else)

| # | Ticket | Notes |
|---|---|---|
| A1 | Close `WGRESP_DEV_OPEN` on the watcher; staff login in front of `/world`, `/console`, `/api/state`, `/api/test/*` | reuse the firerisk staff accounts (l26, PBKDF2) through a signed session token, or a managed identity provider; founder decision |
| A2 | Roles: **ops** (names, addresses, crews), **viewer** (clusters, counts, fires; no names), **crew** (own position and assignments), **demo** (synthetic data only) | enforced server-side on every payload, not in the page |
| A3 | Opaque client ids: `client_ref = HMAC(secret, email)` everywhere outside the notifier; emails and phones stay server-side | removes PII from the S3 state, logs and map payloads |
| A4 | The public simulator and any sales demo stay synthetic | never real clients on an open page |

**Exit gate:** an unauthenticated request gets no client or crew data; a viewer
sees clusters without names; logs and the S3 state contain no email.

### Phase B — Real clients on the live map

| # | Ticket | Notes |
|---|---|---|
| B1 | `--clients-table` and `--clients-region` flags; read-only IAM for the watcher's instance role | today's defaults are hard-coded |
| B2 | Roster health on the map header: enrolled, located, skipped for missing coordinates, stale | count-only, no PII; fixes go upstream (geocode in firerisk) |
| B3 | Live map = real clients only; demo homes move to the test map and the simulator | otherwise demo homes and customers mix on one map |
| B4 | Merge the ember-radius fix (branch `fix/watch-ember-radius-live`) | developed-cell homes need it before alerts are relied on |
| B5 | Client card at local zoom (ops role): address, alert state and history, P10/P50 arrival, assigned crew and stop, firerisk home profile | the data exists across the two engines |

**Exit gate:** real enrolled clients show on the live map for the ops role only;
a real fire near one builds a domain; still dry-run.

### Phase C — Real crews on the live map

| # | Ticket | Notes |
|---|---|---|
| C1 | Roster in `wg-technicians`: id, name, team, base, phone, active; the watcher loads it and rejects beacons from unknown ids (default-OFF knob) | today any id auto-registers |
| C2 | Telemetry client | founder decision: (a) "On shift" in the tech portal, a web page posting position every 60-120 s while open (fast to build; iPhones pause it in the background); (b) a native app (reliable background GPS, weeks of build); (c) vehicle GPS trackers posting to the beacon endpoint by webhook (most reliable, per-device cost). Recommended: (a) for the pilot, (c) for trucks at scale |
| C3 | Per-device credentials: short-lived tokens from the tech portal; rate limits; implausible jumps rejected | replaces the shared key for beacons |
| C4 | Consent and retention: positions only while on shift; raw kept 72 h after an incident, then aggregated; crews see their own, ops sees all | LIVE_OPS 2.3; legal review |
| C5 | Teams and bases (W4.1): base markers and drive-time reach rings; team = crews + base + shift | the Eagle base matrix generalised |
| C6 | Persist the roster's last positions so a restart doesn't empty the map; trail on click | the tracker already keeps a 50-beacon trail |

**Exit gate:** a real crew on shift appears within one ping, turns UNKNOWN when
its pings stop, shows as a ghost on the test map, and appears in a dry-run plan.

### Phase D — Individual maps

| # | Ticket | Notes |
|---|---|---|
| D1 | Per-incident view `/world?incident=<id>` backed by `/api/incident/<id>/view` | W6.1: domain, forecast rings, clients in range with arrival times, crews nearby or assigned, plan legs |
| D2 | Per-client view `/world?client=<ref>` (ops) | the home, the fires threatening it, alert history, assigned crew |
| D3 | Per-crew view `/world?crew=<id>` (ops, and crew for self) | trail, assignments, route, safety windows |
| D4 | Search by client, crew, team or incident | ops role |

**Exit gate:** each view opens from a link, respects roles, loads in under 2 s.

### Phase E — Training demos with real clients and crews

| # | Ticket | Notes |
|---|---|---|
| E1 | Drills on the test map | no build: once B and C are live, the test map already shows real clients read-only and clones real crews as virtual drill crews; only role-gating is needed |
| E2 | "Load community" in the simulator (ops role): pass a community's enrolled homes and its team's crews and bases to `/api/sim/new` through the existing `client_locs` / `crew_locs` seams | homes labelled by `client_ref`, not names; any preset tile now, anywhere after W2/W3 |
| E3 | Anonymised demo mode for sales and public use: positions jittered within their WUI cell, synthetic names, real counts | keeps the public simulator safe |
| E4 | Training record: per-crew drill log and an after-action replay | the drill log and time-scrub exist |

**Exit gate:** a trainer loads one real community with today's crew roster into a
simulator session and runs a drill; nothing leaves the test world.

### Phase F — Scale (as enrollment grows)

Geo-indexed client queries instead of full scans; bounding-box map APIs instead
of full-state polling; a tiled client layer for tens of thousands of homes.

## 5. Decisions for the founder

1. **Login and roles:** reuse the firerisk staff accounts or adopt a managed
   identity provider; who gets ops, viewer and crew access.
2. **Client source of record:** confirm `wg-consumer-profiles-dev` (the live
   consumer funnel's table) is the live map's source, and whether enrolled
   clients need a display-consent flag.
3. **Crew telemetry:** web page, native app or vehicle trackers; and the consent
   and retention policy (legal review).
4. **Demo homes:** move them off the live map when real clients arrive
   (recommended).
5. **Demo anonymisation rules** for sales and training material.

## 6. Order and size

| Phase | Depends on | Rough size |
|---|---|---|
| A — access and privacy | founder decision 1 | 2-3 days |
| B — real clients | A; decision 2 | 2-3 days, plus upstream geocoding |
| C — real crews | A; decision 3 | 1-2 weeks with the web-page client; longer for an app |
| D — individual maps | B, C | about 1 week |
| E — training with real data | B, C | 3-5 days |
| F — scale | growth | later |
