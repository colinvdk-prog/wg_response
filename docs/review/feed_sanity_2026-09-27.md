# Live feed sanity: bad perimeter data vs the fire belief and the seed (2026-09-27)

The belief-vs-physics study (`docs/review/belief_vs_physics_2026-09-27.md`, §8 item 2) noticed that the
NIFC WFIGS Daily perimeters carried back-dated polygons, bogus years (2002, 2020) on 2026 fires and
0–3 ac polygons on a ~155,000 ac fire, and asked whether the live path had the same problems. The
watcher does not read the Daily layer. It reads WFIGS Current perimeters and incident points, FIRIS and
CAL FIRE (`wgresponse/feeds.py`). The per-fire belief (SAFETY SEMANTICS 14) now learns from those feeds.
Its FireStatus `known` and `rapid` flags feed the off-domain road rule (SEMANTICS 15f). On the deployed
watcher (image world5) that rule is armed: `--fail-closed --offmap-per-fire --fire-belief`.

This audit does three things:

1. Measures the four hazards in the live layers, read-only.
2. Traces what `feeds.py`, the monitor and `fire_belief.py` do with each hazard.
3. Adds a guard behind a default-OFF knob: `cfg.feed_sanity`, SAFETY SEMANTICS 18, `watch --feed-sanity`.

Nothing is pushed or deployed.

## 1. For the founder, in plain words

**Two of the four hazards are in the data the watcher reads today. A third is in the recent history of
the same feed. The fourth is a flaw in our own code. I also found a fifth hazard along the way.**

| hazard | in the live data? | does it corrupt the belief? | does it mis-anchor the seed? |
|---|---|---|---|
| **Re-stamped polygons** (one polygon re-issued under a newer time) | **Yes, and common.** 60 of 111 current WFIGS polygons were last edited more than 3 days after they were drawn. The deployed watcher's first and only window so far is one. | **Yes.** Each re-stamp becomes a full-weight "zero growth" window. The fire turns `known` and learns slow. Under 15f, known-and-not-rapid swaps the 1.7 m/s road ceiling for the fire's own speed. | No |
| **Collapsed polygons** (a tiny polygon on a large fire) | **Yes, in the recent history.** 12 of 918 published edits on 6 of 11 fires carried a polygon under 10 % of the record's own size. The polygons were 0–8,183 ac while the records said 10,524–156,699 ac. Each lasted minutes to about 2 days. Each carried a *newer* edit time, which is the field the adapter reads. One such record is live right now, but it is old enough to be gated. | **Yes.** The belief reads a −99.99 % "shrink", then a jump of 100,000+ ac, which triggers a surprise reset and `rapid`. | **Yes, dangerously.** The seed *is* the tiny polygon, so the forecast front starts behind the real one. In the pinned test world the P10 comes 1.3 h late. |
| **Reported sizes that drop** | **Yes.** In the history the record's size reset (Hagen 15,798 → 700 ac; Border 2 12,918 → 7,296). Right now two sources disagree on one fire: JUNIPER is 0.01 ac in WFIGS and 73 ac in CAL FIRE. The fused size follows whichever record is fresher. | **Yes.** The belief reads a negative window, then a jump (`rapid`). | No (reported sizes never seed) |
| **Own time years off or in the future** | **No** in any time field the adapters read, live or in the history. The 2002/2020 stamps were in `poly_PolygonDateTime`, which no adapter requests. | **A code flaw.** A future stamp is clamped to "now", so the same polygon is re-observed as a new zero-growth capture every 3 h. The fire turns `known` and slow. | **A code flaw.** An ancient stamp burns the whole domain before now. Every client in it goes EXTREME, then terminal IMPACTED, unless the confirmed anchor (13e) happens to rescue it. |
| *Found along the way:* **the area figure is not the geometry** | **Yes.** 23 of 560 live polygons carry an area figure more than 2× off their own rings. WFIGS "Auto-generated for InFORM" polygons keep their first size (Bunker: 1 ac on 2,850 ac of rings). FIRIS has rows like LOST: 12,820 ac on 1.6 ac. | **Yes.** The belief prefers the figure, so a bogus figure next to a right one is a fake collapse or jump. | No (the rings seed) |

Duplicates: today no WFIGS incident carries two current polygons. If one ever does, the adapter keeps
the last in feature order, and the collapse guard covers the damage. FIRIS rows carry no IRWIN id, so
one fire can appear as several incidents (Dome and Timber each appear three times today). That is a
fusion identity problem. This guard does not fix it.

**What `--feed-sanity` does.** A perimeter or size the feed cannot have meant never teaches the belief
and never shrinks the seed. On the belief side:

- An own time in the future or before the fire is ignored. It is never clamped to "now".
- A polygon under 10 % of its record's size is ignored.
- A re-stamped polygon counts at most once a day, as the existing "static" zero-growth observation with
  3× error.
- A drop beyond measurement noise is held. If the size comes back, it was a glitch and never counts. If
  it persists a day, it was a correction: the series restarts there, with no negative window across it.
- A polygon's area comes from its geometry when the feed's figure is more than 2× off.

On the seed side:

- An implausible polygon time falls back to the record's own time.
- A collapsed polygon is replaced by the fire's last plausible one, and so is one that drops beyond
  noise. That polygon is remembered for 72 h, in-process.
- Every finding is a note. The first finding on a fire is also an operator event.

**Measured on the 11 fires' published history** (918 edits, replayed through the real belief store;
§5):

- Perimeter windows: 674 → 481.
- Full-weight zero-growth windows: **306 → 0**. Re-stamps now become 101 once-a-day static
  observations.
- Collapses: **6 → 0**. Jumps over 10×: **10 → 4**; the 4 left are genuine early growth.
- Reported resets and drops: **3 → 0**.
- Steps flagged `rapid`: **244 → 218**.
- Seed: the guard replaced **13 of 918** polygons: the 12 collapses and one back-dated re-publication.
  Every one was justified, and no polygon was seeded blind.

On the pinned hazard world (§4) the unguarded engine:

- sends every client near an ancient-stamped fire to terminal IMPACTED;
- runs the P10 1.3 h late on a collapsed seed;
- raises a fake surprise reset and three fake `rapid` flags;
- turns a future-stamped fire into "known slow", which opens its roads.

The guarded engine does none of these.

**Recommendation.** Arm `--feed-sanity` with the next watcher deploy, alongside `--fire-belief`.
Tracking and the road rule are live, and the re-stamp flaw is already writing `known` windows into the
deployed belief file. With the knob OFF the engine is byte-identical to main (pinned; §6). Keep
`--fire-belief-apply` on hold as the belief study recommends. Fake windows would make apply worse, not
better.

## 2. What was measured, exactly

All network access was public, keyless, read-only GETs through the adapters' own URLs. Everything
downstream ran on the recordings.

| input | what | where |
|---|---|---|
| live recording 1 | `WfigsFeed` + `FirisFeed` + `CalFireFeed` over the deployed CONUS AOI, 2026-09-28 04:21 UTC, plus attribute-only side queries (every field) on both WFIGS layers | `out/feed_sanity/rec_20260928T042143Z/` |
| live recordings 2 and 3 | the same, 04:55 and 05:07 UTC (34 and 46 min after the first) | `out/feed_sanity/rec_20260928T045508Z_b/`, `rec_20260928T050729Z_c/` |
| deployed belief file | the watcher's `fire_beliefs.json` from its S3 mirror, saved 04:05 UTC; tracking began about 03:19 | `out/feed_sanity/watcher_s3/` |
| published history | the belief study's WFIGS Daily rows for its 11 fires: 918 edits in publication (OBJECTID) order | `out/bvp_data/rows/` (bvp worktree) |

- Recording 1 holds WFIGS 291 incidents (374 points, 126 perimeters), FIRIS 443 rows and CAL FIRE 7.
  Fused, that is 535 incidents. Recordings 2 and 3 differ only in WFIGS 292 / 291 and CAL FIRE 6.
- The Daily layer's rows behave like one row per edit of a Current-layer polygon. For example, Willow
  has 12 rows on 07-20, each with its own `poly_DateCurrent`. So each row is read here as a state the
  Current layer held; that is an inference from the pattern, not documented behaviour. A row carries no
  publication time: §5 clocks each one at max(polygon edit, record edit), the earliest the watcher
  could have read it.

## 3. The hazards, one by one

### 3.1 Re-stamped polygons (live)

**Data.**

- `feeds.WfigsFeed` stamps a polygon's own time (`meta perimeter_at_s`) from `poly_DateCurrent`. That
  field is the GIS record's last edit.
- The depiction time is `poly_PolygonDateTime`. No adapter requests it.
- Over the 111 current polygons that carry both fields, the edit came a median **89.8 h** after the
  depiction. The 90th percentile is 808 h, and the maximum is 1,384.7 h (Willow: drawn Jul 21, last
  edited Sep 16). 60 of the 111 gaps exceed 72 h.
- In the history, 295 perimeter windows kept an unchanged `poly_PolygonDateTime`. 216 of them changed
  area by less than 0.1 %.
- The deployed watcher's only completed window: Little Giant's 172,891.23 ac polygon at 18:28, then the
  same 172,891.23 ac at 03:23. That is zero growth over 8.9 h. It is unmodeled, but it already makes the
  fire `known`.

**Code.** `fire_belief._observe_perimeter` takes any polygon with a newer own time as a new capture. It
records it every `fire_belief_min_obs_spacing_h` (3 h), and each one becomes a full-weight window. The
reported series already has a guard for this (an unchanged size becomes a static observation only after
`fire_belief_static_h`, with 3× error). The perimeter series had none.

**Effect.**

- **(a) Corrupts the belief.** Windows that say "zero growth" pull the multiplier toward the floor, and
  the first one makes the fire `known`. With 15f armed, known and not rapid replaces the ceiling with
  the fire's own P90 front speed.
- Seed: none. SEMANTICS 13e/f own the seed time of an old polygon.

**Guard (18d).** A polygon within `feed_sanity_restamp_frac` (0.1 %) of the last observed polygon's
area is the same capture. It becomes a static zero-growth observation only once `fire_belief_static_h`
(24 h) has passed since that observation, exactly the reported series' rule.

### 3.2 Collapsed polygons (history; one record live now)

**Data.** 12 published edits carried a polygon under 10 % of the size their own record reported. All
were WFIGS, `Wildfire Daily Fire Perimeter`, and most had no `poly_PolygonDateTime`:

| fire | edit (≈ seen) | polygon | record says | until a plausible polygon again |
|---|---|---|---|---|
| Grasshopper | 08-19 05:40 | 43.2 ac | 91,555 ac | 4.9 h |
| Grasshopper | 08-28 01:03 | 8,182.6 ac | 93,841 ac | 1.8 h |
| Hagen | 08-30 01:30 | 0.0 ac | 60,861 ac | 1.0 h |
| Hagen | 08-31 01:24 | 0.0 ac | 60,975 ac | 1.1 h |
| Little Giant | 07-30 20:04 | 0.0 ac | 25,407 ac | 4.0 h |
| Sinlahekin | 08-18 01:38 | 2.4 ac | 153,843 ac | 48.0 h |
| Sinlahekin | 08-24 00:32 | 2.9 ac | 155,209 ac | 23.8 h |
| Sinlahekin | 08-25 19:09 | 0.0 ac | 155,227 ac | 5.4 h |
| Sinlahekin | 08-27 03:05 | 0.6 ac | 156,699 ac | 13.9 h |
| Sinlahekin | 08-27 16:57 | 0.9 ac | 156,699 ac | 0.1 h |
| Sand Creek | 09-01 10:45 | 66.6 ac | 34,083 ac | 12.4 h |
| Bobcat Lakes | 08-29 11:25 | 0.0 ac | 10,524 ac | 0.3 h |

- In publication order, Sinlahekin's record reads 152,743 ac, then 2.4, then 154,179, then 2.9, then
  155,160, then 0.0, then 155,406. Each version carries a *newer* `poly_DateCurrent`, so the adapter
  and the belief's newer-time rule accept every one.
- The live layer holds one such record now. HAY CREEK COMPLEX carries a 29 ac polygon, which is a child
  fire ("Esau Canyon"), under the complex's 200,292 ac aggregate size. It is 100 % contained and its
  record is 13 days old, so the SEMANTICS 9 gate excludes it.
- The genuine early-growth jumps are never under 10 % of their record's size: Aspen Acres 362 → 22,827,
  Plaskett 40 → 2,774 and Little Giant 20 → 245. In those cases the polygon is *larger* than the
  record's lagging size.

**Code.** `fire_belief._observe_perimeter` accepts any area above 0. `threat._assess_incident` seeds
whatever polygon the fused record carries. Nothing compares the polygon with its own record's size.

**Effect.**

- **(a) Corrupts the belief.** The belief reads a −99.99 % window (slow, `known`), then a +150,000 ac
  jump. That jump is a surprise reset and `rapid` for a day or two.
- **(b) Mis-anchors the seed, in the unsafe direction.** The spread starts from a 2 ac polygon where
  150,000 ac burn. Every arrival near the real front is hours late. P10 is the crew-safety law, and the
  corridors under fail-closed trust the late raster.

**Guard (18c).**

- Belief: a polygon under `feed_sanity_collapse_frac` (10 %) of its record's reported size is not
  observed, when that size is at least `feed_sanity_min_acres` (100 ac).
- Seed: the monitor seeds the fire's **last plausible polygon** instead: its rings, its own time and
  its meta, remembered for `feed_sanity_memory_h` (72 h; the longest measured spell was about 48 h).
- A polygon that drops below the remembered one by more than `feed_sanity_drop_z` (3) sd of the
  difference is replaced the same way. That catches a partial polygon, or a back-dated one, when no
  record size is present.
- Within the memory window the seed is never a smaller fire than the feed showed.
- With nothing remembered (a restart, or a first sighting), the polygon is seeded as received, with the
  alarm.

### 3.3 Reported sizes that drop

**Data.**

- 28 IncidentSize decreases in the history. Most are GIS re-measurements under 1 %. The largest genuine
  ones are Little Giant −6.4 % and Border 2 −5.8%; under the belief's own area-error model (3 % relative
  plus 10 ac) that is at most 1.6 sd.
- Three were resets: Hagen 5,685 → 700 and 15,798 → 700 (the record snapping back to its first size),
  and Border 2 12,918 → 7,296. Those are 12 sd or more.
- Live, fusion disagreement:
  - JUNIPER fuses two WFIGS records (0.1 and 0.01 ac) with CAL FIRE's 73 ac. The fused
    `reported_acres` is whichever member is freshest, 0.01 ac right now.
  - Mineral: CAL FIRE says 19 ac and the FIRIS polygon is 2.03 ac.

**Code.** `_observe_reported` takes any change as a new observation. A drop becomes a negative-growth
window.

**Effect.**

- **(a) Corrupts the belief.** A drop is read as a shrink (slow). The recovery reads as a jump, which
  is `rapid` and possibly a reset.
- Seed: none. The confirmed anchor (13e) reads a smaller record as "sizes disagree", which is the
  legacy, pessimistic seeding.

**Guard (18e).** A size below the series' last observation by more than 3 sd of the difference is
**held**:

- It is not observed, and the young-fire rapid clause keeps the pre-drop size.
- If the size comes back, it was a glitch and is never counted.
- If it persists `fire_belief_static_h`, it was a correction. The series restarts there, with no window
  across the drop.

The same rule holds a perimeter that drops beyond noise without collapsing.

### 3.4 Own times years off or in the future

**Data.**

- Every time field the adapters read is clean in both recordings: 0 in the future, 0 over a year old, 0
  before discovery. The fields are WFIGS `poly_DateCurrent` (124 carry it, 2 are missing),
  `attr_ModifiedOnDateTime_dt` (126), point `ModifiedOnDateTime_dt` (374), FIRIS `poly_DateCurrent`
  (434, 9 missing) and CAL FIRE `Updated` (7).
- In the history the bogus years were in `poly_PolygonDateTime` only. Plaskett had 9 rows stamped
  2002-09-05 17:28 and Sinlahekin 1 row stamped 2020-07-31. Their `poly_DateCurrent` was sane.
- With the history clocked correctly, the guard's time check fires on none of the 918 edits.

**Code.**

- `fire_belief._observe_perimeter` clamps a future stamp to now. The next cycle's clamp is newer again,
  so the same polygon is re-observed as a new zero-growth capture every 3 h, for as long as the stamp
  stays in the future.
- The seed clamps a future stamp too, which is harmless.
- An ancient stamp (older than the fire) passes the SEMANTICS 9 gate, which reads the *record's* time.
  The spread then burns the domain from that date.

**Effect.**

- Future stamp: **(a)** a fake `known` fire that turns slow.
- Ancient stamp: **(b)** the whole domain has burned before now. Every client in it goes EXTREME, then
  IMPACTED, which is terminal. The confirmed anchor rescues it only when the record's size matches the
  polygon.

**Guard (18b).**

- An own time later than now + `feed_sanity_future_h` (1 h) is never observed. So is one earlier than
  the fire's discovery less 24 h, or a year before now when discovery is unknown.
- The seed takes the record's own time (the F3 fallback) instead.
- A stamp inside the tolerance keeps the SEMANTICS 9 clamp. It is observed once and never repeated.

### 3.5 Found along the way: the area figure is not the geometry

**Data.** Of 560 live polygons, 519 have an area figure within 5 % of their own rings. Of the rest:

- WFIGS: 10 within 2× and **4 more than 2× off**.
- FIRIS: 8 within 2× and **19 more than 2× off**.

The WFIGS four are all `Auto-generated for InFORM`:

| fire | figure | rings | record says |
|---|---|---|---|
| Bunker | 1 ac | 2,850 ac | 2,849 ac |
| Star Lake | 1,129 ac | 16,249 ac | 16,412 ac |
| Main Trail | 200 ac | 842 ac | 841 ac |
| EGGER | 1 ac | 0.14 ac | 1 ac |

FIRIS examples: LOST 12,820 ac on 1.6 ac; SPRINGS 0.01 ac on 4,164 ac. Separately, the adapter's
200-vertex stride subsampling alone moves some rings' area by around 20 %. CHICKEN COOP, a single ring,
reads 72.6 ac subsampled against 91.1 ac in full and 91.2 ac from GIS.

**Code.** `_complete` uses the feed's figures whenever both ends of a window carry one. The confirmed
anchor compares the figure with the record. For Bunker that means "polygon lags a growing fire", which
is the pessimistic direction.

**Effect.** **(a) Corrupts the belief.** A bogus figure beside a right one is a fake collapse or jump.

**Guard (18a).** The feed's figure stands only within `feed_sanity_area_ratio` (2×) of the rings'
area. Otherwise the rings' area is used.

## 4. The pinned hazard world (tests/feed_sanity_stack.py)

Five feed fires, one hazard each, run through the real monitor, ladder, planner, StateStore, daemon and
world summary. The posture is the deployed watcher's: tracking, fail-closed plus the per-fire horizon,
the confirmed anchor, model error 0.71 and the p50 bound. It runs five cycles, four hours apart.

| fire | unguarded (the goldens, main 0bbd430) | `feed_sanity=True` |
|---|---|---|
| ancient-1 (stamped 3 years before discovery; the record reports more than the polygon) | c1–c3 P10 at −26,165 h. All three EXTREME, then **IMPACTED** (terminal) | Seeded at the record's time. No alert. One `feed_sanity` operator event |
| collapse-1 (6,178 ac, then 3.6 ac for one cycle under a newer time, then 6,427 ac, then re-stamped twice) | c1 P10 **3.47 h vs 2.18 h** in the collapse cycle. Windows 6,178 → 4 → 6,427. Surprise RESET plus `rapid`. Two zero-growth re-stamp windows | c1 P10 2.18 h throughout (last plausible polygon). One window, 6,178 → 6,427 over 8 h. No reset, not rapid. Re-stamps not observed (under 24 h). One operator event |
| future-1 (stamped 400 days ahead) | Four zero-growth windows (the clamp). `known`, not rapid. Road horizon **per-fire 0.37 m/s** | No window. Unknown, so the **1.7 m/s ceiling** |
| reset-1 (reported 5,000 → 500 → 5,100) | Windows 5,000 → 500 → 5,100. `rapid` | 500 held. Window 5,000 → 5,100. Not rapid |
| figure-1 (988 ac of rings with a 1 ac figure, then re-mapped at 1,028 ac) | Window 1 → 1,028 ac. `rapid` | The geometry is used: 988 → 1,028 ac. Not rapid |

`degraded` is identical in both runs, so a feed-sanity finding is never a degradation.

## 5. The guard on the published history (11 fires, 918 edits)

`scratch_review/feed_sanity/fs_measure.py` part B does the replay:

- Each edit becomes a FireIncident, exactly as `WfigsFeed` builds one: `perimeter_at_s` =
  `poly_DateCurrent`, `perimeter_acres` = `poly_GISAcres`, and reported = IncidentSize at the record's
  modification time. The polygon is a circle of the edit's GIS area, because the rows carry no geometry.
- The real `FireBeliefStore` observes each edit at max(polygon edit, record edit) + 10 min.
- There is no spread, so windows are unmodeled. This counts window shapes and the `known`/`rapid`
  flags, not the posterior.
- The store's 64-window cap is lifted to count every window formed.

| perimeter series | live rule | guarded |
|---|---|---|
| windows formed | 674 | 481 |
| zero growth, full weight (re-stamps) | **306** | **0** |
| zero growth, static (3× error, once a day) | 0 | 101 |
| collapse (< 10 %) | **6** | **0** |
| jump (> 10×) | 10 | 4 (genuine early growth) |
| growth or re-measurement | 352 | 376 |

| reported series | live rule | guarded |
|---|---|---|
| windows formed | 407 | 403 |
| collapse / drop beyond noise | 1 / 2 | 0 / 0 |
| jump (> 10×) | 4 | 2 |
| zero growth, full weight (genuine tiny re-measurements, e.g. 96,031 → 96,121 ac) | 79 | 79 |

- Steps flagged `rapid`: 244 → 218. The steps removed come from collapse recoveries and resets:
  Sinlahekin 40 → 28, Grasshopper 53 → 46, Sand Creek 13 → 7 and Bobcat Lakes 10 → 9. No other fire's
  count moved.
- The guard issued 12 collapse notes, 3 holds and 1 restart. Two holds were Hagen's resets to 700 ac
  (from 5,685 and from 15,798); both sizes came back, so neither ever counted. The third was Border 2's
  12,918 → 7,296 ac. It persisted 26 h, and the series restarted there (the fire later grew to 23,857
  ac).
- **Seed side** (part C, the monitor's rule): 13 of 918 edits were replaced by the last plausible
  polygon.
  - 12 were collapses. The remembered polygon was 0.4–40 h old.
  - 1 was a drop. Aspen Acres OBJECTID 56969 re-published the 07-02 polygon (55,392 ac, own time 03:13)
    *after* the 07-03 polygon (66,896 ac, 11:25). That is a back-dated re-publication, and keeping the
    newer polygon is right.
  - None was seeded as received.

## 6. Byte identity, tests, cost

- `cfg.feed_sanity` OFF reproduces, on the pinned hazard world, every digest computed on **main 0bbd430
  before this rule existed**: 5 cycles, `/api/state`, the state file, the world summary and the belief
  file (`tests/test_feed_sanity.py::test_knob_off_reproduces_the_0bbd430_goldens`). The goldens came
  from `scratch_review/feed_sanity/fs_golden.py` run against the main checkout's engine. The script
  prints the engine path it imported.
- The belief's legacy observers are the unmodified code path. With the knob off the sanity observers are
  never called (pinned).
- `tests/test_feed_sanity.py` has 37 tests covering:
  - the hazard world both ways;
  - the pure checks;
  - each belief guard, one hazard at a time;
  - the seed side: substitution, the no-memory alarm, expiry, a drop without a record size, the time
    fallback for ancient and future stamps, and the knob off;
  - the CLI flag;
  - config bounds.
- No existing test was re-pinned. Full suite on the branch: 2,271 results, 2,259 passed, 12 skipped, 0
  failed, pytest exit 0. All 12 skips are environmental: this fresh worktree lacks the git-ignored
  Colorado OSM PBF and one extended simulator tile.
- Cost: the seed side computes each perimeter's ring area once per cycle. On the national snapshot
  that is 0.45 s for all 373 fused perimeter incidents, before the staleness gate removes most of them.
  The worst single fire, Hagen (403 rings), takes 17 ms.

## 7. Limits, and what it does not do

1. **The memory is in-process.** A restart during a collapse seeds the collapsed polygon, with the
   operator event, until the feed restores a plausible one. The belief side does not share this gap:
   its reference is the record's own size and its own persisted series.
2. **A re-verified unchanged polygon looks like a re-stamp.** A genuine "no change since yesterday"
   re-map with an identical area counts once a day with 3× error. That is the reported series' own
   treatment of an unchanged size.
3. **Complexes.** A complex record (CX) whose polygon is one child fire, like HAY CREEK COMPLEX, always
   trips the collapse check. The belief learns no perimeter series from it, and the seed keeps it with
   an alarm. The children are separate incidents with their own polygons.
4. **`poly_PolygonDateTime` is not used.** It is the true depiction time, but no adapter requests it,
   and adding it would change the feed query even with the knob off. The history also shows it stale
   for weeks: Sinlahekin kept 08-10 00:00 on polygons edited through 08-28. It also carries typos (2002,
   2020). Requesting it as a cross-check on `poly_DateCurrent` is a possible next step, not done here.
5. **Fusion is untouched.**
   - FIRIS rows carry no IRWIN id. One fire can be several incidents (Dome and Timber: WFIGS plus two
     FIRIS mission groups each), each with its own belief.
   - A fused size follows its freshest source. The drop hold limits a two-source flip (JUNIPER) to at
     most one growth window each way.
   - Both are fusion identity problems for their own ticket.
6. **What was measured is partly live and partly history.**
   - The three live recordings spanned 46 min of a US evening, and no polygon's time moved across
     them. The live re-stamp evidence is the lag field (§3.1) and the deployed belief file (re-read at
     04:44 UTC: still that one Little Giant window).
   - The collapse evidence is the Daily history of the same records. It is an append-per-edit copy of
     the Current layer, clocked approximately.
   - The history replay has no spread, so posterior effects are shown only on the synthetic hazard
     world.

## 8. Files and how to reproduce

| file | what it is |
|---|---|
| `wgresponse/feedsanity.py` | the pure checks + the seed side's `PerimeterMemory` |
| `wgresponse/fire_belief.py` | `_observe_perimeter_sane` / `_observe_reported_sane` (called only under the knob) |
| `wgresponse/threat.py` | `_sane_seed_incident` (top of `_assess_incident`), the memory, the `feed_sanity` operator events |
| `wgresponse/contracts.py` | SAFETY SEMANTICS 18, the 8 `feed_sanity*` knobs + bounds, the module seam |
| `wgresponse/cli.py` | `watch --feed-sanity` (`_feed_sanity_cfg`, `_feed_sanity_notes`) |
| `tests/feed_sanity_stack.py`, `tests/test_feed_sanity.py` | the hazard world + goldens, 37 tests |
| `scratch_review/feed_sanity/fs_record.py` | read-only recorder of the live layers (the adapters' own URLs + two side queries) |
| `scratch_review/feed_sanity/fs_measure.py` | parts A–D above; writes `out/feed_sanity/measure.json` |
| `scratch_review/feed_sanity/fs_golden.py` | the byte-identity goldens on a given engine tree |

From the worktree root (`py -3` is the engine's Python):

```
py -3 scratch_review/feed_sanity/fs_record.py            # a new read-only recording
py -3 scratch_review/feed_sanity/fs_measure.py           # every number above, from recordings
py -3 scratch_review/feed_sanity/fs_golden.py <engine-root> 2
py -3 -m pytest tests/test_feed_sanity.py
```

`fs_measure.py` reads the belief study's Daily rows from `$FS_BVP_ROWS`, then `out/bvp_data/rows`, then
that study's worktree. `scratch_review/belief_vs_physics/bvp_fetch.py fetch` re-creates them.
