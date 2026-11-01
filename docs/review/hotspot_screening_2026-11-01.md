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

## Still open

- **The rota (O-7):** names, the fire-season dates and the phone channel (O-1).
- **GOES:** the build effort, step 3.
- **The replay and its budgets:** needed only if client messages or automatic dispatch come back.
