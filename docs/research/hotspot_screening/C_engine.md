# Hotspot screening: how it plugs into WG Response safely

Angle C: engine integration, safety semantics, validation. Written 2026-10-29. Read-only study: nothing in `C:\Users\colin\wg_response` was modified (all experiments ran on a `git archive HEAD` copy under the scratchpad).

Evidence tags used below: **[C]** read in code (file:line), **[M]** measured by the scripts listed in Appendix A, **[X]** external page fetched today, **[I]** inference (stated so you can challenge it). Line numbers are **6203e98** (the HEAD I read) unless marked *(wt)*, which means the lead's in-flight work (`attach_hotspots`, `FirmsFeed.live`, SAFETY SEMANTICS 39, `docs/review/firms_live_2026-10-29.md`, `tests/test_hotspots.py`). It was uncommitted when I read it and was committed as **facac24** at 13:28 while this report was being written; *(wt)* line numbers are that commit's, and the 6203e98 numbers for `threat.py`, `feeds.py` and `contracts.py` have shifted by the lead's additions after the insertion points.

---

## 0. Bottom line

1. **Today a standalone FIRMS cluster is a full fire.** One 375 m pixel on grass, 1.0 km upwind of a home (the home downwind), takes the home NONE to EXTREME_24H in the first 5-minute cycle (p10 0.95 h) and the client text reads "Crew dispatch underway - ensure property access now" **[M A1]**. Ten minutes after the cluster leaves the feed (window roll-off, id change, attachment) the same pair is CLEARED and an "All clear" is sent **[M B1]**. The deployed watcher is dry-notify shadow ops **[C Dockerfile:1-5]**, so nothing has reached a client yet; this is the moment to fix it.
2. **The engine's only built-in "screen" is the burnability of the single seed cell** (`spread.py:1786-1787`). It is brittle and weak: one 90 m non-burnable cell among grass gives *no forecast at all* for a home 1 km downwind **[M G]**, and on the real Chico valley floor (LANDFIRE LF2024, bundled Paradise wide tile) it screens only about a fifth of random single-pixel seeds; 65 % of them become fires of 100+ acres in 6 h (median 5,209 acres in 24 h) **[M H]**. The fuel layer calls most valley floor "grass" (61 % GR/GS, 8.2 % NB3 agriculture) **[M]**. Rice-straw heat is therefore modelled as a fast grass fire.
3. **Where to screen:** a monitor-side step in `ThreatMonitor.cycle`, after `_attach_hotspots` *(wt threat.py:677-681)* and before the assess loop (`threat.py:708`). Never in the adapter and never before fusion: the repo's own convention is that feeds return records and the gate is monitor-side (`threat.py:1202-1217`), and `CycleResult.removed_incidents` already shows the pattern for "nothing disappears silently" (SEM 13c).
4. **Three tiers.** HEAT (map only), POTENTIAL (assessed on a cheap profile, *client-invisible by default*, never dispatched, still an obstacle in the crew corridor law), CONFIRMED (today's behaviour). Promotion is immediate; demotion needs affirmative benign evidence and is slow; CONFIRMED latches.
5. **Do not gate client messages by routing the notifier** (the `SeedSafeNotifier` pattern). Routing records the send as delivered (and even if it reports "not delivered" nothing retries it in-process), so on promotion the client is never told **[M I(1), I(2)]**. Use a **shadow AlertEngine** for POTENTIAL (the client hears exactly one normal transition at promotion **[M I(4)]**) or, if a client-visible rung is wanted, the existing SEM 8 cap with its lift-on-promotion **[M E, E2, I(3)]**.
6. **Absence is not clearance, applied to screening:** a screen may only *demote* on positive benign evidence; missing/unavailable screening data never demotes (SEM 8: "no evidence, no gate"); nothing within R_trip (about 8 km, derived from the engine's own constants) is ever HEAT; a screen-out is a listed, expiring decision, and it never creates, advances or clears a pair. Demotion of a pair-holding incident freezes it (blind), it does not walk it to CLEARED.
7. **Rule ranking:** adopt now - static-heat mask, persistence (second look), all-low-confidence single-look demotion beyond R_trip, tripwire, operator Confirm/Dismiss. Adopt as *labels that lower the ceiling*, not drops - cropland/ag, prescribed burns. Features only - FRP, cluster size, day/night, fuel/WUI. Phase 2 - GOES (the only thing that makes distance-based deferral safe).
8. **Four engine hazards to fix regardless of screening:** (a) hotspots seed at `now_s` whatever their age, which is wrong in both directions (late for a real fire, and honest seeding would drive a 1 km home to terminal IMPACTED **[M D]**), and P10 crew deadlines for any FIRMS-origin fire would be optimistic by the age of the last look; (b) seeds are one grid cell, not a 375-800 m pixel footprint **[M G]**; (c) cluster-to-fire id handover duplicates client messages **[M F]**; (d) `attach_hotspots` *(wt)* can launder confirmation (a 0.01-acre reported point takes every detection within 3 km).
9. **Validation:** replay the FIRMS standard-processing archive (2018-2025) against WFIGS discovery/creation times with the production clustering and attach code, per-rule ablation, temporal holdout, client-weighted false alarms, zero tolerated "danger misses" (rule-of-three bound), plus forward shadow logging because URT/RT latency and retraction cannot be recovered from the archive.

---

## 1. Method and caveats

- Read: `feeds.py` (FirmsFeed 702-820, fuse_incidents 916-990, `_merge_group` 1231), `threat.py` (cycle 637-920, `_fetch_and_fuse` 1126, gates 1202-1346, `_assess_incident` 1366-1526, freshness tiers 1656-1834, `_maybe_dispatch` 2076-2183), `alerts.py` (whole), `contracts.py` SAFETY SEMANTICS 1-38 plus knobs, `spread.py` seeding, `offmap.py`, `notify.py`, `seeded.py`, `persist.py`, `server.py` routes, `cli.py` watch daemon, `docs/LIVE_WORLD_PLAN.md`, `docs/LIVE_OPS_PLAN.md`, `docs/FIRE_BELIEF.md`, `docs/review/live_map_freshness_2026-10-28.md`, and the in-flight diff (snapshot: `scratchpad/hotspot_screen/inflight.diff`).
- Experiments: real `ThreatMonitor`, real `AlertEngine`, real `FirmsFeed` (HEAD), default `ResponseConfig` (ensemble_n 6, horizon 96 h), stub weather (constant west wind, RH 20 %, 30 C), stub uniform terrain or the real bundled Paradise tile. The production flags (grass floor, gust blend, model error, any-angle, crown fire, extreme-regime posture) make forecasts earlier and more conservative, so the measured "EXTREME in one cycle" is a lower bound on how fast it fires live.
- External facts were fetched today from the FIRMS area-API page and the FIRMS FAQ **[X]**; GOES product facts come from search summaries only (lower confidence). No FIRMS data was downloaded and no key was used; the lead's 2026-10-29 counts (4,763 detections, 1,312 clusters, 27 detections on 5 of 217 reported fires, 26 clusters within 60 km of the 520 demo homes, 27 % id churn in 3 h before stable ids) are quoted as given.
- Not measured: the replay itself, the corridor-veto effect of POTENTIAL rasters on real jobs, operator workload.

---

## 2. How a standalone FIRMS cluster flows through the engine today

### 2.1 Stage by stage

| # | Stage | What happens | Where |
|---|---|---|---|
| 1 | Parse | Only latitude, longitude, acq date/time and `frp` are read. `confidence`, `daynight`, `satellite`, `bright_ti4/ti5`, `scan`, `track` are **discarded**. | `feeds.py:766-783` |
| 2 | Cluster | Greedy 2 km centroid clusters; id `firms:<lat>,<lon>` of the centroid; name "FIRMS cluster lat,lon"; meta `n_detections`, `max_frp`, `satellite_source`. *(wt: live mode adds `hotspot_t_s`, `satellites`, kept ids, 3 satellites, 24 h window.)* | `feeds.py:785-820`, `:806-816` |
| 3 | Health | Any feed with `last_fetch_ok=False` makes the cycle `feeds_healthy=False`; every pair without an assessment then **freezes** (blind), not only FIRMS ones. *(wt: any one of three satellite requests failing sets it.)* | `threat.py:1169-1179`; `alerts.py:276-278` |
| 4 | Fuse | FIRMS never joins by IRWIN id or name; canonical-id rank 3. *(wt: `attach_hotspots` moves detections on a reported fire into it; the remainder stays a FIRMS incident.)* | `feeds.py:906-990`; *(wt)* `threat.py:677-681` |
| 5 | Gates | SEM 9 stale gate reads the newest detection time (24 h window, so never bites). SEM 13 contained-removal never applies (no containment signal). | `threat.py:1202-1231`, `:1233-1346` |
| 6 | Assess | No perimeter, so seeds = every detection **at `now_s`** whatever its age; client search is a 60 km radius around all detections; no client = one note per incident; else a full domain (LANDFIRE tile + NWS forecast + ensemble). | `threat.py:1463-1471`, `:1348-1352`, `:1473-1490`, `:1492-1526`; `spread.py:988-1001` |
| 7 | Forecast tier (if armed) | No containment reported = "growing" (60 min); a pair at WATCH or above = "threat" (15 min; NOTICE_72H does not count). New detections change the seeds, so geometry-changed refreshes queue on **one** FIFO worker. | `threat.py:1656-1694`, `:241-243`, `:1696-1702`, `:1791-1809` |
| 8 | Ladder | NONE can jump straight to EXTREME_24H (p50 <= 24 h or p10 <= 12 h); NOTICE auto-progresses to WATCH; EXTREME reaches IMPACTED (terminal) when the median ETA passes; EXTREME or WATCH clear after 2 clean cycles (10 min). | `alerts.py:538-649`, `:562-563`, `:632-633`; `contracts.py:3008` |
| 9 | Message | Names the incident by its feed name (so "FIRMS cluster 39.800,-121.600 (firms:39.800...)") and, at EXTREME, promises crew dispatch. A CLEARED message is suppressed while the client holds another active pair. | `alerts.py:692-759`, `:787-796` |
| 10 | Dispatch | Pairs in NOTICE_72H, WATCH or EXTREME_24H become jobs; gel window 72 h and hold 0, so a stop can be planned as soon as the job exists. **Every** assessed ensemble (jobless fires too) enters the corridor law; FIRMS clusters have no behaviour report, so under SEM 15 they extrapolate at the 1.7 m/s ceiling with a 60 min margin. | `threat.py:234-236`, `:2106-2120`, `:2130-2150`; `contracts.py:3521`, `:3886`; `dispatch.py:2431-2434`; `offmap.py:115-123` |
| 11 | Map / state | Every cluster is an incident row (about 200 B) and every no-client cluster a per-cycle note (about 85 B): 1,312 clusters add about 363 KB to `/api/state` (W6 baseline 276 KB). | `threat.py:1482-1490`; `cli.py:1468-1478` **[M estimate]** |
| 12 | Persist | Pair records are never pruned; each new cluster id near homes adds a pair forever. | `alerts.py:178`, `:266-270`; `persist.py:512-555` |

### 2.2 Measured behaviour (HEAD engine, scripts in Appendix A)

| Case | Result |
|---|---|
| **A1** one detection, GR2 grass, 6 m/s, home 1.0 km downwind | cycle 0: p10 0.95 h, p50 1.36 h, **NONE to EXTREME_24H**. Client text: "EXTREME alert for Test Home - FIRMS cluster 39.800,-121.600 (firms:39.800...): projected fire arrival ~1h (median), 1h (pessimistic). Crew dispatch underway - ensure property access now." |
| A2 same on NB3 agriculture seed cell | p10 = inf, state NONE (the implicit fuel screen) |
| A3 timber litter, 2 m/s | p10 = inf within 96 h, state NONE |
| A4 home 1 km **upwind** | p10 24.9 h, p50 27.7 h: **NOTICE_72H** "Advance notice - crews being scheduled" from one pixel |
| **B1** EXTREME pair, cluster absent for healthy cycles | +5 min still EXTREME; **+10 min CLEARED**, "All clear ... no projected fire arrival within the 72h alert window for FIRMS cluster ..." |
| **D** seed at the detection time instead of now | age 6 h: homes at 1 and 3 km already burned (ETA 0); age 20 h: 8 km too. Ladder: NONE to EXTREME ("AT the property NOW - do not approach; crews withdrawing") then **IMPACTED (terminal)** next cycle |
| **E** SEM 8 cap on a pair | extreme p50 gives NOTICE, WATCH, WATCH, WATCH (never EXTREME); a p10-only trigger with p50 100 h holds **NONE**; lifting the cap emits WATCH to EXTREME at once |
| **F** cluster absorbed into a reported fire at the same place | two EXTREME messages for one physical fire (cluster-labelled, then named); the cluster pair's all-clear is suppressed; cluster pair ends CLEARED at +15 min |
| **G** non-burnable block of 1, 3 or 5 cells (90 m) at the seed, grass elsewhere | **no forecast** (p10 inf) for a home 1 km downwind; all-grass control p10 1.07 h |
| **H** 40 random single-pixel seeds per band on the real Paradise wide tile (240 m, 6 m/s west wind) | valley floor (lon < -121.70): 22 % no spread, 65 % >= 100 acres at 6 h, median 285 ac at 6 h and 5,209 ac at 24 h; seeds on grass never fail to spread (median 484 ac at 6 h). Valley fuel mix: GR/GS 61.0 %, timber 17.8 %, NB1 urban 10.4 %, NB3 ag 8.2 %. Foothills: 58 % >= 100 ac. Mountains: 30 % |
| **I** notifier routing vs shadow engine vs cap | routing (delivered or not): 0 client messages after promotion; cap: NOTICE and WATCH were sent before promotion, EXTREME at promotion; shadow engine: 0 before, one EXTREME at promotion |

### 2.3 Screens the engine already has (accidentally) and what it lacks

Has: seed-cell burnability (hard, single cell, G); the 60 km client radius (`threat.py:230-231`); "no clients = no domain" (1482); the SEM 9 age gate; the extreme-regime posture (which *amplifies* grass domains, exactly the valley-floor case).
Lacks: any use of confidence, FRP trend, persistence, day/night, land cover, static sources, prescribed-burn knowledge, or any notion of evidence level.

### 2.4 What a false POTENTIAL costs the engine (measured or cited)

1. A client message with crew-dispatch wording within 5 minutes (A1). Messages are not recallable.
2. Crew dispatch eligibility from NOTICE onward (`threat.py:234-236`). SEM 5: coating is opaque and applied only under live threat; a false job means a coated home and a removal visit.
3. A terminal IMPACTED if seeds were ever made honest (D).
4. Compute: a domain per cluster within 60 km: LANDFIRE tile 155-435 s cold each, sequential (`docs/LIVE_WORLD_PLAN.md:580`), 746 MB peak working set at 18 domains (`:640-645`).
5. Corridor coupling: every assessed ensemble is an obstacle for every real crew leg and, with `--offmap-per-fire`, an *unknown-behaviour* fire extrapolates at the ceiling (`dispatch.py:2431-2434`, `offmap.py:115-123`). Not measured here; the replay must count jobs vetoed by POTENTIAL rasters.
6. Single refresh worker: a queue of POTENTIAL refreshes can delay the 15-minute refresh of a real threat fire (`threat.py:1696-1702`).
7. Degraded banner: a 0.01-acre report (VILAS) failed LANDFIRE every cycle and flagged the whole map degraded (`docs/review/live_map_freshness_2026-10-28.md`). Each extra domain is another way to fail.
8. Payload and notes inflation (363 KB) and unbounded pair records.
9. Operator attention: 26 clusters within 60 km of 520 homes today.

---

## 3. Every place a screening decision could act

| DP | Where | Acts as (your examples) | Verdict |
|---|---|---|---|
| 0 | `FirmsFeed._parse_csv/_cluster` (`feeds.py:766-820`) | drop before fusion | **No for decisions.** Do add the missing columns here (confidence, daynight, satellite, bright_ti4, scan, track), aligned with `hotspots` like *(wt)* `hotspot_t_s`. Dropping in the adapter hides data from attach, audit and the map and breaks the monitor-side-gate convention. |
| 1 | `_fetch_and_fuse` (`threat.py:1126-1200`) | drop before fusion | No (same reasons; also the lead's attach needs every detection). |
| 2 | `attach_hotspots` *(wt feeds.py)* | identity, not evidence | Keep identity only; **stamp provenance** (distance to host, host credibility, max distance) so the screen can refuse laundering (S 4.1, I-4). |
| 3 | New step between `_attach_hotspots` *(wt threat.py:677-681)* and the assess loop (`threat.py:708`) | keep on map, skip assessment | **Primary.** Partition into assess-set and heat-only list, stamp `meta["screen"]`, hold the per-cluster memory, emit aggregated notes/events. |
| 4 | `_assess_incident` entry (`threat.py:1366-1490`) | skip / cheap profile | Tripwire distance, forecast profile choice (cheap for POTENTIAL), footprint seeding, per-cycle assessment budget. |
| 5 | `_forecast_tier` and `_refresh_pool` (`threat.py:1656-1702`) | cadence | Add a POTENTIAL tier and its own worker/priority so it cannot starve threat-tier refreshes. |
| 6 | `_register_forecast` (`threat.py:1640-1652`) to `_evaluate_alerts` (`:846`) | assess but cap | **Route POTENTIAL assessments to a shadow AlertEngine** (client-invisible); promotion = start sending them to the live engine. |
| 7 | `incident_meta` (`threat.py:804-812`) to `_update_incident_meta`/`_step(capped=)` (`alerts.py:429-499`, `:294-296`, `:596-647`) | cap the ladder | Optional client-visible WATCH ceiling (`screen_potential_alert="watch"`). Template already tested (E, E2). |
| 8 | `_notify` (`alerts.py:771-814`), notifier wrappers (`seeded.py:646-662`) | suppress sends | **Avoid as the gate** (ledger hazard, I). Fine for an operator copy. |
| 9 | `_maybe_dispatch` job loop (`threat.py:2106-2120`) | assess but never dispatch | Skip pairs of non-CONFIRMED incidents (note). Keep the ensembles in `dict(ensembles)` (`:2139`). |
| 10 | Blind-pair derivation (`threat.py:757-796`) | defer / hold | Deferred (budget) or demoted pair-holding incidents go into `blind_pairs`: frozen, operator-alarmed, never cleared. |
| 11 | `CycleResult`, `/api/state`, `worldview.hotspot_rule` *(wt; it already carries `"screening": None`)*, `world.html` | display | New `CycleResult.screened` list (like `removed_incidents`), reason counts, near-client list, mask ages. |
| 12 | `server.py:1195-1220`, `:1324` and `persist.py:512-555` | human vetting | Authed Confirm/Dismiss routes; memory persisted (additive key or own file, as `seeded_incidents` / `fire_beliefs.json`). |

"Require a second pass or a second source to escalate" is not a place but a state machine across cycles; it needs the DP-3 memory (cluster id to looks, labels, tier, latches).

---

## 4. Criteria

Each: integration point, benefit, risk (with failure scenarios), safety-semantics impact, recommendation. R_trip and the E-levels are defined in S 6.

### C1. VIIRS confidence (l / n / h)
- **Integration:** carry `confidence` per detection from `_parse_csv` (DP-0), cluster features n_low/n_nom/n_high, rule at DP-3.
- **Benefit:** the FIRMS FAQ **[X]** describes low as mostly daytime pixels near Sun glint with a temperature anomaly under 15 K, nominal as glint-free with a stronger anomaly, and high as saturated pixels. A search summary of the Earth Engine catalog adds that low-confidence night pixels were already removed from the NRT feed (lower confidence source). Cheapest rule, no new data, and each demoted cluster saves a domain.
- **Risk:** the first pixel of a small real fire can be low or nominal. Scenario: 0.3 ha grass start, 12 m/s wind, 2 km from a home, one low-confidence daytime pixel; the 1.7 m/s plausible-max bound (`threat.py:230`) covers 2 km in 20 min while the next look is 50 min to 12 h away. The FAQ itself cautions that the field needs an empirically chosen threshold.
- **Safety semantics:** 6 (screened is not cleared: listed, expiring); SEM 8 pattern (missing column = no demotion). Nothing in 1-4, 7, 10.
- **Recommendation:** **Adopt, demote-only:** clusters whose *every* detection is low and that have one look, beyond R_trip, go to HEAT with a 6 h expiry and return on any second detection. `high` (saturated) is a promotion contributor. Shadow-log first. Expect a small yield (all-low clusters are probably a minor share of a day's 1,300 clusters; the replay will say): adopt it because it is safety-neutral, not for volume.

### C2. FRP
- **Integration:** `frp` already parsed (`feeds.py:780`), `max_frp` in meta (`:816`); add per-look series.
- **Benefit:** the only continuous intensity signal; rising FRP across looks is growth evidence, and the fire belief has no growth series for FIRMS (`docs/FIRE_BELIEF.md:80-81`). High FRP promotes; FRP orders the assessment budget.
- **Risk:** a floor drops real early fires (value unknown until the replay); FRP is biased by view angle, smoke and cloud edges; a 100-acre stubble burn can read tens of MW, so high FRP does not separate ag from wildfire.
- **Safety semantics:** 14 ("evidence, never clearance"): a later growth series for the belief is consistent. None otherwise.
- **Recommendation:** **Promotion and priority only.** No FRP floor in phase 1; revisit if the replay shows a floor removes a large share of false alarms at zero lost true positives.

### C3. Cluster size
- **Integration:** `n_detections` (`feeds.py:816`) counts detections over all looks and satellites, not footprint; compute contiguous pixels per look.
- **Benefit:** >= 3 contiguous pixels in one look (roughly 40 ha) is very likely significant; growth between looks is a growth signal.
- **Risk:** the 2 km greedy clustering merges unrelated heat in a farm district and inflates n; one fire seen by 3 satellites counts as 3; true fires start as one pixel, so a size floor costs a look.
- **Safety semantics:** none new.
- **Recommendation:** **Promotion contributor on per-look contiguous pixels. No demotion by size.**

### C4. Multi-pass / multi-satellite persistence
- **Integration:** per-detection time and satellite *(wt meta `hotspot_t_s`, `satellites`)*, grouped into looks (same satellite, passes within about 6 min); memory per cluster id across cycles; keep 3 days of raw detections for persistence (`day_range` allows 5, `feeds.py:729`) while seeds use only the last 24 h.
- **Benefit:** the best discriminator inside the feed itself; also the natural promotion path (E1/E2).
- **Risk:** (1) looks are **not time-diverse**: per the FAQ **[X]** NOAA-20/21 pass about 50 min ahead of S-NPP (about 12:40 vs 13:30 local, same at night; the page is inconsistent about NOAA-21's slot) and mid-latitudes get three to four looks a day combined, versus the lead's "about 6": a second satellite confirms about an hour of persistence, not a 6 h stubble burn; independent persistence needs the next day/night group (about 12 h). Measure the real look-gap histogram from the 24 h data already fetched. (2) **RT/URT rows are provisional**: the area-API page says RT and URT detections are dropped when the matching NRT detections are processed, or after 6 h **[X]**, so a look can move or disappear. (3) No detection is ambiguous (no pass, cloud, smoke, sub-threshold). (4) Swath geometry leaves some places with one look per half-day.
- **Safety semantics:** 6 (quiet is not out: hold, S 6.3 D-b); 13d analog (re-detection returns it at once); 39 (stable ids are a precondition).
- **Recommendation:** **Adopt as the primary promotion evidence and basis of the E-levels. Never as a basis for clearing.** Count a look as confirmed only after NRT replacement, but never retract a promotion because a provisional row vanished.

### C5. Static industrial heat mask
- **Integration:** versioned data file (like `data/tiles`, one README row) of 375 m cells built offline from the SP archive; lookup at DP-3; mask age published in `hotspot_rule`.
- **Benefit:** highest-precision rule: same-pixel heat on >= N distinct days a year is not a new fire. The FAQ **[X]** says the NRT VIIRS/MODIS feeds do not attribute thermal-anomaly types, so the engine must build its own mask; the FAQ also names gas flares and bright factory roofs as false-alarm sources.
- **Risk:** a real fire inside or next to a plant yard, landfill or refinery masked. Mitigations: radius <= one pixel; FRP above 1.5x the cell's historical maximum unmasks; cluster growth beyond the pixel unmasks; exclude cells inside any wildfire perimeter of the last N years (a slash-pile or smouldering burn scar must not become "static"). A stale mask only costs false alarms.
- **Safety semantics:** 6, 8 (no mask file = no masking), 13d analog (unmask on growth).
- **Recommendation:** **Adopt.** Lowest risk, build first.

### C6. Cropland / ag-burn mask and season
- **Integration:** land-cover lookup per detection. The engine already fetches LANDFIRE layers through LFPS (`terrain.py`, canopy-layer precedent), so LANDFIRE EVT is the cheapest path; USDA CDL or NLCD class 82 are alternatives. Label `suspect:ag` at DP-3. Month window per state is a prior, not a gate.
- **Benefit:** the dominant false-alarm class near Paradise/Chico (rice-straw season, 26 clusters). The fuel layer does *not* screen it **[M H]**: the valley floor is mostly "grass", so one ag pixel becomes a 285-acre-in-6-h grass fire.
- **Risk:** (1) field-edge grass fires are real: require distance to non-ag burnable fuel > W (500 m) for the label; (2) pasture/hay/fallow are grass and must not be masked, only cultivated crops; (3) season as a hard gate fails in anomalous years; (4) ag burns do escape: the label must expire (6-12 h) and unmask on growth or detections outside the field; (5) land-cover vintage and resolution.
- **Safety semantics:** 6, 8 (service down = no label), 13d (return on growth).
- **Recommendation:** **Adopt as a label that lowers the ceiling** (POTENTIAL-suspect: operator list, no page, no dispatch). Outside R_trip it may demote to HEAT with expiry. Inside R_trip it stays POTENTIAL: a valley home 2 km from a burn is exactly the exposed client.

### C7. Burn permits / the RX layer
- **Integration:** `WfigsFeed` **drops** prescribed burns (`feeds.py:339`, `:470`, `include_rx=False`); parse them into a non-incident side list (point, planned acres, ignition/discovery time) handed to the screen. State permit systems are mostly not APIs.
- **Benefit:** explains autumn prescribed heat (named in the lead's FIRMS doc), gives the operator a name and acreage, and gives an escape guard (planned acres).
- **Risk:** RX escape is the highest-consequence case. The label must lapse and unmask when cluster extent exceeds 1.5x planned, detections fall beyond unit radius + 1 km, the window [ignition - 6 h, planned duration + 24 h] ends, or wind/red-flag conditions apply. WFIGS RX coverage is incomplete (absence is not "not RX", presence is not "safe"); placeholder sizes/times are common.
- **Safety semantics:** 6, 8, 13 (a declaration is evidence; contradiction wins), 24 (over-alerting preferred to silence).
- **Recommendation:** **Adopt as a label only**, after RX records are retained. Defer non-API permit data.

### C8. Day/night
- **Integration:** `daynight` column; feature at DP-3.
- **Benefit:** night detections have less background clutter and are more likely sustained; a day-to-night re-detection at one location is strong persistence; ag burns cluster in afternoon permitted windows; the low-confidence class is daytime.
- **Risk:** as a gate it is wrong (fires burn at night; night sensitivity yields more tiny detections).
- **Safety semantics:** none.
- **Recommendation:** **Feature only.**

### C9. Distance to clients
- **Integration:** DP-4 (the registry call per incident already exists, `threat.py:1475`) and DP-3 for the tripwire.
- **Benefit:** R_trip is the valve that makes every demoting rule safe; distance also drives the cost profile (cheap forecast, assessment order).
- **Risk:** **distance cannot defer with VIIRS-only evidence** **[I]**: the engine's own plausible-max ROS 1.7 m/s crosses 60 km in 9.8 h, while the next look can be 12 h away, so no unlabelled single-look cluster inside the 60 km radius can be left HEAT by distance alone. Only a fast second look (GOES, 5 min) makes deferral beyond about 9-14 km safe. A client enrolled tomorrow, or a partial registry roster, changes the answer; the screen re-evaluates every cycle and a partial roster must not demote (SEM 6 registry completeness, `threat.py:757-796`).
- **Safety semantics:** 6, 9 (the 60 km radius is the engine's own bound).
- **Recommendation:** **Adopt as tripwire and as a cost/priority key. Do not use as a stand-alone drop until GOES.**

### C10. Fuel / WUI context (LANDFIRE)
- **Integration:** the domain forecast already applies fuel; a *pre*-assessment fuel test needs a national lookup (W2 national COGs, `docs/LIVE_WORLD_PLAN.md:588-594`), not per-cluster LFPS jobs (155-435 s).
- **Benefit:** physical, no new rule: no fuel, no spread; WUI adjacency raises priority.
- **Risk:** the existing gate is a hard single-cell test **[M G]**: a road, driveway, pond or field pixel centre gives *no forecast* even beside grass. A VIIRS pixel is 375 m at nadir and larger off-nadir (the `scan`/`track` columns give it), geolocation is imprecise, and LF2024 may be stale. On the valley floor it screens little **[M H]**.
- **Safety semantics:** 6 (today a no-ETA result is `p10 = inf`, state NONE, with no note: silence masquerading as clearance); 35/ember radius (`ember_threat_radius_m = 0` live, `contracts.py:3957`).
- **Recommendation:** **No pre-assessment fuel screen. Fix the seed instead**: seed every burnable cell inside the pixel footprint plus a geolocation allowance (or snap to the nearest burnable cell), and add a note when a cluster still has no burnable seed. This is a prerequisite for any FIRMS-origin fire, screened or not. WUI/fuel only as promotion or priority features.

### C11. GOES confirmation
- **Integration:** a sidecar writes a small JSON of recent GOES ABI fire pixels (time, lat, lon, mask category, power); the screen reads it at DP-3. The engine core stays stdlib-only (optional heavy imports are lazy, e.g. `tifffile` in `terrain.py`). The FIRMS area API has no GOES source **[X]**.
- **Benefit:** 5-minute CONUS cadence turns one VIIRS look into a time series within 10-15 minutes, and it sees fires before a VIIRS pass. It is the only thing that makes distance deferral safe (R_defer about 9 km with a 15 min look + 15 min ack + 60 min crew lead). Promote-only, so fail-safe.
- **Risk:** 2 km pixels and poor sensitivity to small cool fires (no corroboration for them; they simply stay POTENTIAL); false alarms on reflective surfaces; NOAA advises focusing on the processed and saturated categories (codes 10, 11, 30, 31) **[X, search summary]**; GOES-19 became operational 2025-04-07 **[X, search summary]**; a new data dependency.
- **Safety semantics:** none new (promote-only); 6 (missing GOES = no promotion).
- **Recommendation:** **Phase 2**, in shadow first, via sidecar.

### C12. Human vetting (operator Confirm / Dismiss)
- **Integration:** authed routes next to `/api/seed` (`server.py:1195-1220`, `:1324`; key check `:1079`); decision record persisted (additive key as `seeded_incidents`, `persist.py:512-555`); UI on `world.html`. `docs/LIVE_OPS_PLAN.md:65-72` already plans operator approve/hold per incident.
- **Benefit:** the only way to resolve an urgent near-client ambiguity in minutes (cameras, 911/CAD, calling the client), and it is accountable.
- **Risk:** staffing at night; alert fatigue; a wrong Dismiss silences a real fire; a single shared API key gives no operator identity; Dismiss must not read as clearance of pairs.
- **Safety semantics:** 6 and 13d: a Dismiss is a positive declaration by an accountable human, with TTL, reason code and return-on-growth, valid only on POTENTIAL (never on CONFIRMED). `LIVE_OPS_PLAN.md:112`: an operator may hold, never extend a deadline: a human may lower the ceiling, never loosen a crew law.
- **Recommendation:** **Adopt** for urgent near-client POTENTIAL. Confirm = latch with `confirmed_by`; Dismiss = TTL (12 h default) + return rules.

### C13 (added). Fire-history proximity (escape / rekindle prior)
- **Integration:** `self._contained` (`threat.py:514`) and the lead's `removed` list know fires removed as contained and their perimeters.
- **Benefit:** a detection just *outside* a removed fire's perimeter is growth evidence that acreage will not show; E+1 and exempt from benign labels.
- **Risk:** residual heat near a contained point-only fire (no perimeter footprint) becomes a standalone cluster.
- **Safety semantics:** extends 13d ("growth returns a fire") to hotspot growth: needs a founder amendment.
- **Recommendation:** **Adopt as a promotion prior**; propose the 13d extension.

### C14 (added). Weather and lightning
- Red-flag/wind and recent lightning raise urgency and E-level (the forecast fetch already exists). Low priority; features only.

### 4.1 Cross-cutting engine interactions (apply to every criterion)

- **I-1 Seed time.** FIRMS hotspots seed at `now_s` (`threat.py:1463-1465`, `spread.py:988-1001`). For a FIRMS-origin fire the whole fire is that seed, so the front is *late* by the age of the last look, and SEM 2 crew deadlines (P10) would be optimistic by the same amount **[I]**. Honest seeding (acquisition time) is correct for a real fire but drives nearby homes to IMPACTED for a false one **[M D]**. Therefore: POTENTIAL may keep `now_s` seeds (triage only, no deadlines), but **no FIRMS-origin fire may be dispatched until it is seeded at acquisition time** (footprint polygons through the `perimeter`/`perimeter_at_s` seam, the F3 provenance rule), and POTENTIAL must have a ladder ceiling below IMPACTED.
- **I-2 Footprint seeding** (C10) must land with, or before, screening.
- **I-3 Id handover.** Cluster id to reported-fire id duplicates the client message **[M F]**; promotion/attachment of a client-visible pair needs a pair re-key (the SEM 24 spirit). With a client-invisible POTENTIAL there is nothing to re-key.
- **I-4 Attachment can launder confirmation.** *(wt)* a point-only host gets a footprint of at least 2 km + 1 km regardless of size, so a 0.01-acre report (VILAS-type) takes every detection within 3 km as "its" hotspots and they inherit CONFIRMED. Split "identity footprint" from "confirmation footprint" (inside the perimeter, or within max(1 km, 3 x equivalent radius) of a credible size) and record the max attach distance in `meta["hotspots_attached"]`.
- **I-5 Corridor law.** POTENTIAL ensembles stay in `dict(ensembles)` (crew safety is monotone in obstacles). This is an invariant, not a knob. Cost to measure in the replay.
- **I-6 Refresh starvation.** Give POTENTIAL its own worker (or priority) and a cheap profile (24 h horizon, 240 m cells, 2 members), refreshing in-cycle on promotion.
- **I-7 Provisional rows and blind sources.** Three satellite requests per cycle (wt) means any transient failure flags the cycle degraded and freezes unassessed pairs (safe, but it also prevents legitimate clears elsewhere). Track per-source health; count `feed_blind` only after N consecutive misses, while still never treating a missing satellite as clear.

---

## 5. Fail-closed and "absence is not clearance"

### 5.1 Rule classes
- **Demoting rules** (low-confidence, static, ag, RX, quiet, Dismiss) need *affirmative* benign evidence. Missing, stale or erroring data = the rule does not apply (SEM 8: "Unknown containment or unknown acreage NEVER caps").
- **Promoting rules** (persistence, GOES, FRP, size, high confidence, history, Confirm) raise a tier. A missing promoting source means no promotion; the tier stays where it is.
- So an outage of *either* class never lowers a tier. The failure it can cause is the opposite one: a flood of POTENTIALs. That is handled by the budget valve (5.3), not by dropping.

### 5.2 Source-down matrix

| If this is down or wrong | Effect | Safe default |
|---|---|---|
| One of three satellite requests | `last_fetch_ok=False` *(wt)*; others' detections kept; ids carried | Cycle degraded, absent pairs freeze (`alerts.py:276`); quiet period does not run for that source |
| All FIRMS | no new detections | All FIRMS-derived state frozen, banner; never demote from absence |
| Columns renamed/missing (confidence, daynight) | feature absent | No demotion by that feature |
| Static mask missing/corrupt/old | no static demotion | Unscreened; operator event with mask age |
| Land-cover service/layer down | no ag label | Unscreened |
| RX fetch fails | no RX label | `WfigsFeed.last_fetch_ok` already covers both layers (`feeds.py:382-411`) |
| GOES sidecar stale | no promotion | Stays put |
| Registry partial roster | R_trip cannot be evaluated for missing clients | No demotion for incidents in range of unreturned clients; SEM 6 blind pairs (`threat.py:757-796`) |
| Screening code raises | `OperatorEvent('screen_error')` + note | That cycle runs unscreened (all POTENTIAL as today), as `_attach_hotspots` does on failure *(wt)* |
| Memory file lost/corrupt at restart | latches and dismissals forgotten | Everything re-derived from the 24 h window as new POTENTIAL/HEAT (pessimistic for dismissals); lost CONFIRMED latches are the unsafe direction, so persist atomically like `StateStore` and raise an operator event when a latch cannot be restored |

### 5.3 Budget valve
A per-cycle assessment time budget (`screen_assess_budget_s`, 0 = off) with priority order: tripwire first, then earliest shadow p10, then client value, then FRP. A cluster deferred by budget is **blind**, not clear: its pair (if any) joins `blind_pairs`, an operator event names it, and it is first next cycle. Without this, "screening data down" turns the 1,312-cluster firehose into a DoS on the cycle (it already took 40 min once, `docs/review/live_map_freshness_2026-10-28.md`).

### 5.4 What "absence is not clearance" means for a screened-out hotspot near clients
1. A screen-out is a **decision with a TTL**, not a deletion. It is re-evaluated every cycle against new data.
2. It needs **positive** benign evidence, never the lack of alarming evidence.
3. **Nothing within R_trip of a client is ever HEAT.** Benign labels only lower its ceiling.
4. It is **listed** (`CycleResult.screened`, `/api/state`, one aggregated note, near-client entries as operator items): "NOTHING DISAPPEARS SILENTLY" (SEM 13c).
5. A screen **never creates, advances or clears a pair.** If a pair exists and its incident is demoted, deferred or lost, the pair is **blind** (frozen, operator-alarmed), not on the SEM 6 healthy-feed clear path.
6. A FIRMS cluster that goes quiet is **not out**. B1 shows a 10-minute path from EXTREME to "All clear" on mere absence. CONFIRMED FIRMS-origin fires latch (S 6.3) and leave only through `screen_confirmed_quiet_h`, a sustained quiet period, then the ordinary absence law.
7. The memory must be **persisted**. SEM 13 accepted "a restart forgets" because forgetting there is pessimistic; here forgetting a latch is the anti-safe direction (the pair clears in 10 min).
8. Restart, id change, attachment, window roll-off and URT retraction are all "absence"; none may clear a client-visible pair without the quiet-period rule.

---

## 6. Proposed protocol (SAFETY SEMANTICS 40, proposed)

### 6.1 Tiers

| | HEAT | POTENTIAL | CONFIRMED |
|---|---|---|---|
| Who | FIRMS detections not attached, not promoted | FIRMS clusters passing the T1 test | Reported fires (unchanged); FIRMS detections attached with credible footprint; operator-confirmed; (optional) satellite self-confirmed |
| Map | aggregated heat layer, not incidents | marker with reasons and labels | full |
| Forecast | none | cheap profile (24 h, 240 m, 2 members) refreshed on geometry change or 60 min, own worker; full profile in-cycle on promotion | full |
| Shadow ladder | none | yes (separate AlertEngine, `LogNotifier`/operator queue) | not needed |
| Client-visible ladder | none | **none** by default; optional SEM 8-style WATCH ceiling | full |
| Operator | tripwire items only | `potential_fire` events; **page** if shadow p10 <= `screen_urgent_p10_h` and not benign-labelled; benign-labelled = list only | existing |
| Dispatch jobs | none | **none** | yes (FIRMS-origin only with acquisition-time seeds, I-1) |
| Corridor obstacle | none | **yes** (invariant) | yes |
| Memory / audit | counters, reasons | pair-less memory, shadow pairs | existing + latch |

### 6.2 Evidence and urgency
- **E-levels.** E0: one single-look detection. E1: any one of high (saturated) confidence; >= 3 contiguous pixels in one look; FRP >= F1; a second look by a different satellite within 2 h; day-night re-detection; one GOES scan; prior (C13). E2: two independent E1 signals; or >= 2 looks spanning >= `screen_look_span_min` (default 180) with non-decreasing size/FRP; or two consecutive GOES scans (processed/saturated); or inside a reported fire's confirmation footprint. E3: Confirm or an agency record.
- **Urgency.** `t_fast = d / v_max` with d the distance from the nearest detection to the nearest client and `v_max = 1.7 m/s` (`threat.py:230`, the engine's own plausible bound).
- **R_trip** = v_max x (`screen_trip_ack_min` + `egress_buffer_min` + `deploy_min`) = 1.7 x 3600 x (15 + 30 + 30)/60 = **7.65 km** (`contracts.py:3011-3012`), floor 3 km. Inside R_trip the earliest plausible arrival is inside operator-ack plus crew lead time, so there is no time to wait for a second look.
- **Required evidence for POTENTIAL:** inside R_trip: E0 suffices (tripwire, never HEAT). Outside R_trip: an unlabelled cluster is POTENTIAL; a benign-labelled one needs E1, else HEAT with expiry. Distance deferral (HEAT for unlabelled E0 beyond R_defer = v_max x (look latency + ack + 60 min) about 9 km with GOES, about 14 km with a guaranteed 1 h second satellite, and *no* radius under 60 km with unguaranteed VIIRS revisit) is **enabled only in phase 2 with GOES**.

### 6.3 Promotion and demotion

**Promotions (immediate):**
- P-a attach with credible footprint = CONFIRMED by inheritance, cluster id retired with a note;
- P-b tripwire (detection within R_trip) = HEAT to POTENTIAL; a new client enrolled or a cluster growing into range counts;
- P-c required evidence reached = POTENTIAL;
- P-d (optional, `screen_self_confirm`) POTENTIAL to CONFIRMED when E2 and growth (size/FRP up) and not benign-labelled and shadow p10 <= 12 h; **default off** until the replay shows precision;
- P-e operator Confirm = CONFIRMED (latched, audited);
- P-f urgent and un-acked (`screen_urgent_escalate`, default off): lift to the WATCH ceiling with "verification pending" wording; still no dispatch.

**Demotions (slow, positive evidence only):**
- D-a benign label with expiry, outside R_trip, no pair above NONE: POTENTIAL to HEAT, `screen_demote_cycles` consecutive cycles (default 6);
- D-b quiet: no new detection for `screen_quiet_h` (12 h, so at least one day group and one night group of satellite passes), not inside the tripwire with a shadow pair above NONE (a quiet cluster is "not seen", never "out");
- D-c Dismiss: POTENTIAL to HEAT-dismissed for `screen_dismiss_ttl_h`;
- **Never:** CONFIRMED is demoted by screening. Reported fires leave only through SEM 9 and 13. FIRMS-origin CONFIRMED latches for `screen_confirmed_quiet_h` (36) of quiet (kept as a point fire seeded at its last detection, pairs blind), then the ordinary absence law applies.
- **A wrong Confirm** (or a wrong self-confirm) is undone only by that quiet-period rule or by a two-person Stand-down (two distinct operator ids, reason code, audited); it is never silent and a client-visible pair it created follows the blind-then-clear path.

**Returns (immediate, pessimistic):** a benign-labelled or dismissed cluster returns to POTENTIAL on a detection beyond its footprint (> 500 m), n or FRP >= `screen_return_growth_factor` x (2) the labelled values, persistence past the label TTL, a detection inside any client's R_trip, or a failed data source behind the label.

### 6.4 Ceilings and communications
- Default: POTENTIAL is client-invisible. A client-visible rung is a policy knob (`screen_potential_alert = "watch"`), implemented with the existing cap, never by routing the notifier.
- Messages for FIRMS-origin fires must not print the raw id: name them "unconfirmed satellite heat detection N km <bearing> of your home" (`alerts.py:692-702` uses the incident name from `incident_meta`).
- No client-visible IMPACTED below CONFIRMED. (A shadow pair may read IMPACTED internally; that is an operator signal that the forecast already puts the fire at the property.)

### 6.5 Compute and state
Cheap profile for POTENTIAL; budget valve (5.3); own refresh worker; one `screen` note per cycle with counts per rule; pair-less memory persisted; shadow pairs are ephemeral and pruned by TTL.

### 6.6 Knobs (repo convention: `contracts.py`, default OFF, byte-identical when off, one README row each, `watch --screen-hotspots` plus sub-flags)

| Knob | Default | Meaning |
|---|---|---|
| `screen_hotspots` | False | Master. Requires `fuse_hotspots`. Off = byte-identical cycles, `/api/state`, persisted state |
| `screen_potential_alert` | "operator" | "operator" (client-invisible) / "watch" (SEM 8-style cap, lifts on promotion) / "full" (no ceiling) |
| `screen_potential_dispatch` | False | POTENTIAL pairs produce jobs |
| `screen_trip_ack_min` | 15 | Operator acknowledgement window in R_trip |
| `screen_trip_floor_m` | 3000 | Lower bound of R_trip |
| `screen_potential_horizon_h` / `_cell_m` / `_members` | 24 / 240 / 2 | Cheap profile |
| `screen_assess_budget_s` | 0 | 0 = off; else seconds of POTENTIAL domains per cycle |
| `screen_min_looks`, `screen_look_span_min` | 2, 180 | Persistence evidence |
| `screen_lowconf_heat`, `screen_lowconf_expiry_h` | True, 6 | C1 demotion |
| `screen_frp_promote_mw`, `screen_size_promote_px` | 0, 0 | 0 = off; replay sets the values (starting guess: 3 contiguous pixels) |
| `screen_static_mask`, `_radius_m`, `_frp_factor` | "", 400, 1.5 | C5 |
| `screen_ag_layer`, `_months`, `_wildland_m`, `_expiry_h` | "", "", 500, 12 | C6 |
| `screen_rx`, `screen_rx_escape_factor` | False, 1.5 | C7 (needs RX retained) |
| `screen_demote_cycles`, `screen_quiet_h`, `screen_confirmed_quiet_h` | 6, 12, 36 | D-a/b and latch |
| `screen_dismiss_ttl_h`, `screen_return_growth_factor` | 12, 2.0 | C12 |
| `screen_urgent_p10_h`, `screen_urgent_escalate` | 3.0, False | Page threshold; auto-lift |
| `screen_self_confirm` | False | P-d |
| *(not a knob)* | invariant | POTENTIAL ensembles stay in the corridor law; no client-visible IMPACTED below CONFIRMED; a screen never touches SEM 1-4, 7, 10 |

`validate_config` bounds each (finite, ordering: `trip_floor_m <= R_trip`, `quiet_h <= confirmed_quiet_h`, `demote_cycles >= 1`). Code lands as a pure-function module `wgresponse/hotspots.py` (like `offmap.py`, `wxfield.py`: `screen()`, `rule_summary()`, `screen_row()`, notes helpers; no wall clock), with thin hooks in `threat.py` and an optional shadow `AlertEngine` handed to `ThreatMonitor` (`alerts.py` itself unchanged for the default "operator" ceiling; only the optional "watch" ceiling reuses its existing cap hook).

### 6.7 Draft SAFETY SEMANTICS 40
"40. A SATELLITE HEAT IS EVIDENCE, NEVER A FIRE UNTIL THE EVIDENCE SAYS SO, AND NEVER CLEARANCE WHEN IT GOES QUIET. (a) Tiers HEAT / POTENTIAL / CONFIRMED; reported fires are CONFIRMED; screening never changes a reported fire. (b) A screen demotes only on affirmative benign evidence; unknown or unavailable screening data never demotes (SEM 8); nothing within R_trip of a client is HEAT. (c) POTENTIAL is assessed, never dispatched, never client-visible below the owner's ceiling, never IMPACTED; its ensemble stays in the corridor law (SEM 3, 15). (d) Promotion is immediate; demotion slow; CONFIRMED latches; FIRMS-origin fires leave only after a sustained quiet period. (e) A screen never creates, advances or clears a pair; a demoted, deferred or lost incident's pairs are blind (SEM 6). (f) Every screened-out hotspot, label, expiry, Dismiss and Confirm is listed with reason and operator (SEM 13c). (g) A human may lower a ceiling, never loosen SEM 1-4, 7, 10. Off = byte-identical (tests/test_hotspot_screen.py)."

### 6.8 Tests to pin
Off = byte-identical vs a golden; never-demote-on-missing-data for each rule (property test); tripwire (no detection within R_trip is HEAT under any rule combination); POTENTIAL never reaches client-visible EXTREME/IMPACTED; promotion emits exactly one normal transition (E2/I-4 style); demotion/deferral freezes pairs; restart keeps latches; Dismiss returns on growth; `dispatch.plan(ens=...)` contains POTENTIAL ids, jobs do not; budget deferral = blind + event; determinism.

### 6.9 Rollout
- **Phase 0 (no behaviour change, start now):** carry all FIRMS columns; **log every poll's raw rows with first-seen time** (URT/RT latency and retraction cannot be recovered later); compute screening labels in shadow and show them.
- **Phase 1:** footprint seeding first (it changes forecasts: more FIRMS pixels on road/field cells will now spread, so land it before, and measure it alone), then the HEAT/POTENTIAL partition with a client-invisible POTENTIAL, labels C1/C4/C5/C13, tripwire, budget, own worker.
- **Phase 2:** ag and RX labels, urgent pages, Confirm/Dismiss, GOES sidecar in shadow.
- **Phase 3:** any client-visible or dispatch policy for FIRMS-origin fires, only after the replay thresholds, four weeks of shadow data and an owner decision, with acquisition-time seeding in place.

### 6.10 Worked examples (today's cases)

1. **The single-detection cluster 1.0 km from a demo home.** Inside R_trip, so never HEAT. If its seed cell is grass-mapped (the valley floor is 61 % grass), the shadow ladder says EXTREME within one cycle (as A1), the client sees nothing, the operator is paged because shadow p10 (about 1 h) is under `screen_urgent_p10_h` and the ag label cannot apply when non-ag burnable fuel lies within 500 m. If it is a pure cropland pixel with no wildland within 500 m, it is POTENTIAL-suspect: on the operator list, no page, no dispatch, re-examined every cycle, and any second detection outside the field or a doubling of size or FRP returns it to a full POTENTIAL.
2. **The other valley clusters.** Beyond R_trip and cropland-labelled: HEAT with a 12 h expiry and a counter on the map. Within R_trip: POTENTIAL-suspect. Unlabelled grass or timber: POTENTIAL on the cheap profile, client-invisible, listed. None reaches a client or a crew; all are still counted in the screened list.
3. **A real FIRMS-first fire at night with no agency record.** Tripwire or evidence puts it at POTENTIAL on the first look; the shadow ladder goes EXTREME; the operator is paged; a second satellite look about an hour later makes E1, a third look or growth makes E2; the operator Confirms (or the agency record arrives and `attach_hotspots` promotes it with a credible footprint). Only then does the live engine see it: one normal NONE to EXTREME_24H transition and message, a full-profile forecast seeded at acquisition time, and dispatch eligibility.
4. **A cluster that goes quiet.** POTENTIAL: 12 h of quiet then HEAT, with no client message ever having existed. A satellite-confirmed fire: latched for 36 h, pairs blind rather than cleared, then the ordinary absence law (which today would have cleared it in 10 minutes, B1).

---

## 7. Validation plan

### 7.1 Questions
1. **Lead time**: for fires that matter, how much earlier would the protocol raise a client-visible or operator alert than the engine without FIRMS?
2. **False-alarm load** per rule and tier, weighted by client exposure.
3. **Danger misses**: threatening fires the protocol is later on than unscreened FIRMS.
4. **Cost**: domains per cycle, corridor vetoes, operator pages.

### 7.2 Data
| Need | Source | Note |
|---|---|---|
| Detections | FIRMS standard processing, VIIRS S-NPP (from about 2012), NOAA-20 (about 2018), NOAA-21 (about 2023; check `data_availability`), CONUS plus a border margin. The area API supports `DATE` and `DAY_RANGE <= 5` on `*_SP` sources **[X]** (about 73 calls per satellite-year); the 5,000 transactions / 10 min limit is not binding | has `confidence`, `daynight`, `frp`, `scan/track`, and (per search summary, unverified) `type`; NRT has no `type` **[X]** |
| Ground truth | WFIGS incident locations since about 2020 (`FireDiscoveryDateTime`, `CreatedOnDateTime`, `IncidentTypeCategory` WF/RX/CX, size, containment), WFIGS Daily perimeters (time series, already used by `tools/hindcast_lib.py`), CAL FIRE and FIRIS times for CA, FPA-FOD for small-fire discovery times | Audit time quality (rounded to the hour, 00:00 defaults). "Known to the engine" = max(`CreatedOnDateTime`, first feed appearance) plus publish lag, not discovery time |
| Benign truth | WFIGS RX records; CDL/NLCD cropland; static facilities (EPA FRS, WRI power plants, VIIRS Nightfire flares); SP `type = 2` | for labelling unmatched clusters |
| Clients | WG roster (anonymised) and a WUI-synthetic fallback (building footprints within SILVIS WUI) | false alarms are counted only where a client could be hit |
| GOES | ABI-L2-FDCC (CONUS, 5 min) from NOAA open data, 2023-2025 seasons | for the phase-2 ablation |
| Forward log | the live watcher's raw FIRMS polls with first-seen time and the screening decisions | the only way to measure NRT/RT/URT latency, retraction and id stability |

### 7.3 Pipeline (deterministic, cached, read-only fetches, like `tools/hindcast_harness.py`)
1. Release each detection at `t_acq + L` (L = 0.5 h baseline; sensitivity 0, 1, 3 h) into a 5-minute simulated cycle with a rolling 24 h window.
2. Run the **production** `_cluster_detections` and `attach_hotspots` (so association equals production) against the perimeters/points that existed at that time.
3. Label clusters: matched to a WFIGS event (spatial = the attach footprint test; temporal = first detection in [discovery - 12 h, containment]); matched to RX; unmatched. Unmatched are sub-labelled by land cover, static mask, lifetime/size proxies; audit a random sample of 300 by hand to calibrate those proxies. Unmatched wildland clusters that persist and grow are "uncertain", never counted as false alarms.
4. Run each rule and stack in simulation, producing per-cycle tier trajectories.
5. Engine-consequence subsample: run the real `ThreatMonitor.cycle` for sampled region-days in arms A (no FIRMS), B (FIRMS attach, unscreened), C (protocol) to count client-visible alerts, jobs, corridor vetoes, domains, cycle time. A cheap surrogate (earliest arrival from distance and the plausible-max ROS) drives the full-season scan; validate the surrogate on about 100 clusters against the full engine.

### 7.4 Metrics
- **Lead time** = T_known(WFIGS baseline) - T_first_alert(protocol), distributions by size class, region, day/night, season, and for the *threatening* subset (final footprint within R_c = 5 km of a client, or baseline p10 <= 24 h). Also lead vs FIRMS-unscreened.
- **Danger miss** = threatening event where protocol's first client-visible or operator alert is later than unscreened FIRMS by more than 15 minutes, or never. Each one is read by hand.
- **False alarms** per client-year: client-visible alerts and dispatch jobs on non-threatening clusters; operator pages per day nationally and per region.
- **Compute**: domains and cycle seconds; **corridor vetoes**: jobs lost to POTENTIAL rasters.

### 7.5 Thresholds that would justify adoption (pre-register before the holdout; owner sets the budgets)
- **Safety (hard):** zero danger misses on development and holdout, with n >= 150 threatening events (rule of three: 0 of 150 bounds the true miss rate below 2 % at 95 %); and no ≥100-acre event delayed more than 15 min versus unscreened.
- **Value:** median lead over the WFIGS-created baseline >= 30 min with the 25th percentile >= 0. If FIRMS gives about zero lead on threatening fires, the finding is that FIRMS is not worth its risk and that is a valid result.
- **False alarms:** at least 80 % fewer client-visible false alerts than unscreened FIRMS at equal or better lead; absolute budgets (suggested starting values for the owner: <= 0.05 client-visible false alerts per client-year, <= 3 operator pages per day nationally).
- **Per-rule parsimony:** a rule stays only if it removes >= 15 % of the remaining false-alarm volume at zero safety cost on both sets; labels that cost no true positive are adopted first (static, low-confidence, persistence).
- **Stability:** no region above 3x the mean false-alarm rate; temporal holdout by year (develop 2019-2022, test 2023-2025); block bootstrap by fire-season week.
- **Shadow gate:** four weeks of forward shadow logging, no unexplained difference between replay and live tier decisions, and `docs/LIVE_OPS_PLAN.md:47` style "zero false all-clear" spot audit on cluster pairs.

### 7.6 Pitfalls to design around
Look-ahead leakage (SP refinements, final perimeters used at decision time); survivorship (WFIGS has only reported fires; unreported real fires look like false alarms); WFIGS coverage varying by agency and year; SP versus NRT differences (SP can drop provisional NRT detections, so replay on SP overstates precision); ag-burn regulation drift (use temporal holdout); class imbalance; association tolerance (VIIRS geolocation plus perimeter age).

---

## 8. Decisions for the owner

1. Is POTENTIAL client-invisible by default? (Recommended: yes; shadow-ops makes this free to test.)
2. May FIRMS-origin fires without an agency record or human Confirm ever be dispatched? (Recommended: not until acquisition-time seeding exists and the replay passes; then only via P-d.)
3. Operator coverage: who is paged, the ack window, and is auto-lift (P-f) wanted?
4. Approve acquisition-time footprint seeding for FIRMS-origin fires (a `spread` seam change).
5. R_trip: 7.65 km derived (15 min ack); pick the ack.
6. Ag and RX as ceiling-lowering labels (recommended) or as drops.
7. Retain WFIGS RX records in the feed (changes `feeds.py:470`).
8. GOES sidecar investment.
9. Client wording for any unconfirmed message.
10. The false-alarm and miss budgets in 7.5.
11. Amend SEM 13d so hotspots just outside a removed fire's perimeter return it to the map.

---

## Appendix A. Experiments (all read-only, in `scratchpad/hotspot_screen/`, run against `repo_head/`)

| Script | Shows |
|---|---|
| `exp_a_cluster_flow.py` | A1-A4, B1: ladder, client text, 10-minute clear on absence |
| `exp_b_ladder_cap_and_seedtime.py` | D, D2 (seed time and IMPACTED), E, E2 (SEM 8 cap as ceiling template, lift on promotion) |
| `exp_c_handover.py` | F: cluster to reported fire, duplicate messages, suppressed all-clear |
| `exp_d_seed_cell.py` | G: non-burnable seed cell among grass gives no forecast |
| `exp_e_valley_seeds.py` | H: random seeds on the real Paradise wide tile |
| `exp_f_routing_hazard.py` | I: notifier routing vs cap vs shadow engine |
| `inflight.diff` | snapshot of the lead's uncommitted diff used for the *(wt)* references |

## Appendix B. External sources
- FIRMS area API: https://firms.modaps.eosdis.nasa.gov/api/area/ (sources list, `DAY_RANGE` 1-5, optional `DATE`, 5,000 transactions / 10 min, RT/URT removal rule, no GOES source).
- FIRMS FAQ: https://www.earthdata.nasa.gov/data/tools/firms/faq (confidence semantics, no `type` in NRT, NRT within 3 h, RT 30-60 min, URT 1-5 min, overpass times, false-alarm sources). The page text is long and only its first half was read.
- GOES-R FDC mask categories and codes 10/11/30/31, GOES-19 operational 2025-04-07: Google Earth Engine catalog entries surfaced by search (summary only, not the NOAA user guide).
