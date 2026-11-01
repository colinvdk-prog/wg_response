# Hotspot screening: the founder's decisions (2026-11-01)

The founder, 2026-11-01, chose the recommended option on each of the four open screening
decisions. Nothing here has changed the engine yet: satellite heat only (SAFETY SEMANTICS 41,
watch `--hotspot-heat-only`) stays live until the protocol below is built.

The evidence is the three reports written for the founder on 2026-10-29, copied unchanged into
`docs/research/hotspot_screening/`:

- A: the VIIRS algorithm, agency practice and 49 sources, with joins of the 2024 archive against
  reported fires;
- B: candidate rules measured on the 25–29 Oct FIRMS data against the live fires and the 520 demo
  homes;
- C: where each rule would act in the engine, and the validation plan.

Their scripts and data were scratch files and were not kept.

## The decisions

| # | Question | Decision |
|---|---|---|
| 1 | What does a hotspot only the satellite sees do? | **Potential fire, operator only.** Three tiers: heat, potential fire, confirmed fire. Clients hear nothing until an agency reports the fire or an operator confirms it. |
| 2 | Can a satellite-only fire send crews? | **Only after a Confirm**, or once an agency reports it. Automatic dispatch is reconsidered only if a replay of past seasons shows it misses no dangerous fire. |
| 3 | Registered prescribed burns | **Keep them as labels.** The WFIGS RX records stop being dropped (`feeds.py`, `include_rx`), but they only label heat; they never become fires. |
| 4 | Who answers a page, and how fast? | **On call 24/7 in fire season, 15-minute acknowledgement**, with a second person paged if there is no answer. This sets the tripwire at 8 km. |

No operational agency confirms a satellite hotspot automatically (report A §4): NOAA's analysts,
CAL FIRE's watchstanders and Watch Duty's reporters all put a person between the pixel and the
response. Decisions 1 and 2 follow that practice.

## The protocol these decisions make

**Heat.** Heat appears on the map with no forecast, no alert and no effect on crew routes. Two
kinds qualify:

- a site hot on most nights (refinery, gas flare, power plant, landfill), from NASA's
  standard-processing archive plus the watcher's own five-day memory. On the October data this
  was 11 % of clusters, and it lost none of the 10 known-fire clusters or 86 prescribed-burn
  clusters (B §0.8);
- a pixel more than 1 km offshore (about 1 %).

A heat site becomes a potential fire again when its heat spreads beyond the site, burns hotter
than its history, or comes within the tripwire of a protected home.

**Potential fire.** This is every other cluster that no reported fire takes.

- **Forecast:** a cheap one (24 h, 240 m, 2 members; C §6.1) on its own worker, with a budget per
  cycle. Satellite heat must not slow the reported fires again: world18's cycles ran 26–113 min
  while 14 clusters were forecast.
- **Crew routes:** its forecast counts in the corridor check. Today heat only leaves satellite heat
  out of crew routing entirely. That costs nothing while no crews are live.
- **Ladder:** a shadow ladder the operator sees and clients do not. It never makes a dispatch job.
  If it reads IMPACTED, that tells the operator the forecast already puts the fire at the home;
  it is never a client state.
- **Seeding:** it starts at the satellite's pass time and covers the pixel's footprint. Today every
  detection, including those on reported fires, seeds one grid cell at the cycle time, whatever
  its age.
- **Going quiet:** a potential fire not seen again for 12 h (one day and one night window) goes back
  to heat. The exception is one inside the tripwire with a home on its shadow ladder: quiet means
  "not seen", never "out".
- **Agency report:** when an agency report takes the cluster, its shadow pairs retire. No second
  alert starts under the new id.

A second look from the other satellite about 50 minutes later raises a potential fire's priority,
as do three or more pixels, growth, and later a GOES detection. None of them is required: 53 % of
new hotspots are never seen again, and a large fire's first look is often a single pixel (12 %),
weak (43 % under 10 MW) or at night (49 %) (A §0.3, B §3.3).

**Confirmed fire.** A reported fire (unchanged), or a potential fire an operator Confirms, gets the
full ladder and dispatch. A Confirm sticks. A wrong Confirm is undone only by a sustained quiet
period or by a stand-down that two operators sign, never silently.

**Missing data never demotes.** If the heat-site memory, the RX records or the land-cover layer is
unavailable, its label does not apply and the cluster stays a potential fire.

### The tripwire and the page

The tripwire is how far the engine's fastest plausible fire (`corridor_offraster_ros_mps`,
1.7 m/s, about 6 km/h) travels in the time it takes three things to happen:

- the operator acknowledges the page (15 min);
- a crew deploys (`deploy_min`, 30 min);
- the crew egresses (`egress_buffer_min`, 30 min).

That is 1.7 m/s × 75 min = 7.65 km, so the tripwire is 8 km, which is also `hotspot_heat_note_m`
today. Inside it there is no time to wait for a second satellite look. Even an instant
acknowledgement gives 6.1 km, so a 3 km radius (A, C19) is too short.

- Nothing within 8 km of a protected home is heat.
- The operator is paged for any potential fire within 8 km of a home, and for any potential fire
  whose forecast reaches a home within 3 h (p10).
- Some potential fires are listed but not paged until they escape: one on a registered prescribed
  burn, and one on a known heat site that came inside the tripwire. "Escape" means spreading more
  than 500 m past the burn or site, or doubling in size or heat.
- A farm-burn label (cropland, daytime, no night repeat, small, in season) lowers a potential
  fire's priority beyond 8 km and never applies inside it. More than half the near-home clusters
  on 29 Oct were on cropland (B §0.3).
- A Dismiss expires after 12 h and is undone by growth.

**Load.** On the late-October data about one cluster a day came within 10 km of the 520 demo homes
(B §7b.6). The live map on 2026-10-31 had 312 heat clusters and none within 30 km of a demo home.
The load swings with the day and the season, so the shadow phase measures it before anyone is
paged.

## Build order

Each step is default-off and byte-identical when off, with its own safety note and tests.

0. **Shadow labels, no behaviour change.**
   - Log every raw FIRMS poll with its first-seen time. NRT delivery delays and later-withdrawn
     detections cannot be recovered afterwards.
   - Keep the WFIGS RX records as labels and build the heat-site memory.
   - Show each cluster's labels on the map.
1. **Footprint seeding,** at the pass time and over the pixel's footprint. Measure it on its own
   first, because it changes forecasts. Seeded honestly, a 6-hour-old detection already reaches
   homes 1–3 km away (C §2.2, experiment D).
2. **The potential-fire tier:**
   - the cheap forecast on its own worker, the budget, the crew-route obstacle, the shadow ladder
     and the operator list;
   - **the attach split.** Once attachment confirms a fire, it must not confirm by accident.
     Today a point-only report gets a footprint of at least 2 km + 1 km whatever its size, so a
     0.01-acre report takes every detection within 3 km as its own (C §4.1 I-4). The footprint
     that names a fire and the one that confirms it become separate tests.
3. **The page.**
   - The phone channel (O-1) and the rota (O-7).
   - Confirm and Dismiss on the map, with an audit trail.
   - GOES fire detections in shadow. They are free and arrive every 5 minutes. They caught the
     2024 Park Fire 3.6 min after its recorded discovery, 12.5 h before the first VIIRS pixel,
     but see about 19 % of VIIRS hotspots and none at night (A §0.6).

A client message or automatic dispatch for a satellite-only fire needs new evidence and a new
decision. The evidence is the season replay in report C §7: zero danger misses on at least 150
threatening fires, with false-alarm and page budgets the founder sets.

## Step 0, built (2026-11-01)

The founder said to start step 0 the same day. It is SAFETY SEMANTICS 43 (`wgresponse/screening.py`,
`tests/test_screen_shadow.py`), armed by watch `--screen-labels` and `--firms-log`. It changes no
forecast, alert, crew or heat-only listing. The one change to the fire list is the prescribed-burn
polygon below.

- **Labels.** Each satellite-only cluster gets three checks:
  - *known heat site*: report B's industrial signature over the last five UTC dates. The FIRMS
    adapter asks for five days per source at start and daily, and merges every poll in between.
  - *registered prescribed burn*: from the WFIGS RX records the adapter now keeps.
  - *looks and growth*: how many passes saw it, and whether it grew between them.

  A check that cannot be made reads unknown, never "not a fire". The labels appear on the heat
  rows, in `/api/state` `screen` and on the map (a "heat labels" chip and a line on each cluster).
- **Raw log.** Every FIRMS row is recorded at the poll that first returned it, then again if it
  changes or is withdrawn while still inside the asked window. Day files are mirrored gzipped to
  the state's S3 prefix, and a restart replays the last two days.
- **Growth threshold.** New heat must be more than 1 km from every earlier look, not 500 m. A VIIRS
  pixel is 375 m at nadir and about 800 m at the swath edge. On the live day most 0.5–0.6 km
  "moves" were one pixel shifting between the two satellites.

**Dry run on the live data, 2026-11-01 00:20 UTC** (local only, nothing written to S3):

| | Count |
|---|---|
| Satellite-only clusters | 313 |
| Known heat sites | 53 (17 %; 104 of 2,767 five-day sites qualify) |
| On a registered prescribed burn | 4 (174 of 381 WFIGS RX records usable: open, recent, or a perimeter) |
| Grew between looks | 12 |
| One look so far | 197 (63 %) |

The four burns are real: 130 South RX (Georgia), and RX D7 Second Skinny plus two landing-pile
burns (Montana). The commonest reasons a cluster is not a known site are a daytime detection (107)
and heat on only one or two of the last five dates (137). The first poll logged 754 rows for the
two satellites. Just after 00:00 UTC, FIRMS's two-day answer holds only the day that just ended.

**Found while building:** the WFIGS adapter drops prescribed-burn *points* but never filtered the
*perimeter* layer by type. So a prescribed burn with a polygon enters as a fire: 5 RX polygons
nationally on 2026-11-01, one not stale-gated (RX BUFFALO WOMAN, Oklahoma, 40 acres, no homes near).
Decision 3 says burns are labels, never fires. The founder closed the gap the same day: with
`--screen-labels` a burn polygon is a label only. That is the one change step 0 makes to the fire
list. A polygon whose id belongs to a record of another type still joins it, so a burn that escaped
and was re-typed as a wildfire keeps its polygon.

## world20 live (2026-11-01, 01:07 UTC on)

Main cc28265, watch `--screen-labels --firms-log` on top of world19b's flags.

| Cycle | Minutes | Notes |
|---|---|---|
| 1 | 12.7 | restart: all 10 forecasts computed (Willow's spread 282 s; one new terrain tile 200 s); not degraded |
| 3 | 18.6 | **degraded**: WFIGS and FIRMS NOAA-20 blind; Willow recomputed (homes in range 204 → 187), spread 745 s |

- **Labels, cycle 1:** 312 clusters — 53 known heat sites, 4 registered burns, 12 grew, 197 one
  look, none unchecked. The five-day history loaded for both satellites (8,335 detections, 104 known
  sites).
- **Cycle 3:** WFIGS was blind, so every burn label read unknown, as designed. One satellite was
  blind, so 211 clusters were shown.
- **RX BUFFALO WOMAN** left the fire list.
- **The raw log** is at `watch-conus/firms_log/firms_2026-11-01.jsonl.gz` (754 rows, poll records,
  and every upload succeeded).
- **Step 0's own cost**, measured locally on the same data: 0.07 s to label 311 clusters. The log and
  the memory add nothing measurable to a poll.

**Why cycle 3 went blind.** The raw log's blind event for NOAA-20 reads "<urlopen error [Errno -3]
Temporary failure in name resolution>": the App Runner instance briefly lost DNS, and WFIGS failed in
the same cycle. That is almost certainly the 2026-10-31 blind cycle too. Each blind cycle freezes
every pair of the blind feed (13,222 operator events this time). The next cycle rebuilds forecasts,
which accounts for most of the slow cycles seen since 2026-10-31.

The fix, same day (founder: "add the DNS retry fix"): `wgresponse/net.py`. The feeds', weather's and
terrain's default GET tries once more after 3 s in two cases:
- the request never left: the name did not resolve, or the connection was refused;
- feeds and weather only: the connection was reset or cut off mid-answer. Terrain is excluded
  because the LFPS job submission is a GET that creates a job, so repeating it could queue a
  duplicate.

An HTTP answer or a timeout is never retried, and a second failure is the same blind fetch as
before. Willow's 745 s spread is a separate question (CPU shared with 8 in-flight learning
forecasts is the suspect) and is not addressed here.

## Still open

- **The rota (O-7):** names, the fire-season dates and the phone channel (O-1).
- **Growth in the page step (proposed 2026-11-01):**
  - a follow-up page when a potential fire grows between looks;
  - the time the next satellite look is due, on every page. VIIRS sees a place in two windows a
    day, about 1–2 pm and 1–2 am local, so "no growth" means nothing until it has looked again.

  Inside 8 km the first page never waits for growth.
- **GOES:** the build effort, step 3.
- **The replay and its budgets:** needed only if client messages or automatic dispatch come back.
