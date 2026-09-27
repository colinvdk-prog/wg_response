# Vail → Aspen Response Program — the corridor served from the Eagle base

**GraphGel Events, PD Mountain Resorts territory: the I-70 Eagle Valley and the
SH-82 Roaring Fork Valley, Colorado** (`vail_co`, `beaver_creek_co`, `eagle_co`,
`glenwood_springs_co`, `basalt_carbondale_co`, `aspen_co`).
Founder deliverable, 2026-09-23 ("build the response program from Vail to
Aspen"). Every number below is traced to a committed artifact or to a run
reproduced today; nothing is projected, estimated, or carried over from
marketing material.

## Label key — read this before quoting any number

| Tag | Means |
|---|---|
| **[CORR]** | Measured **on the six corridor tiles** (real LANDFIRE LF2024 fuel / LF2020 terrain, real OSM roads, fetched today) by `scratch_review/corridor_grid.py`, or on the corridor road graph by `scratch_review/corridor_roads.py`. |
| **[4H]** | The 4-hour experiment of 2026-09-11 (`docs/calibration/leadtime_4h_experiment_2026-09-11.md`) on the five-map suite, applied as an analog. |
| **[5MAP]** | Measured on the five-map suite (Malibu / Paradise / Santa Rosa / Boulder / Park City) and applied as an analog. |
| **[ASSUMED]** | Operational doctrine or literature value. Not measured in this repo. |
| **[PENDING]** | A required input that does not exist yet — almost always supplier data. |

Two boundaries govern every figure and cannot be argued away:

1. **These are COATING RATES, not survival rates.** The outcome metric is "did
   GraphGel get onto this home in time, with the crew safely clear."
   `gel_effectiveness` is a **[PENDING]** placeholder that never enters these
   numbers. Coating rate × real effectiveness = survivability, and the second
   factor does not exist yet.
2. **Zero real GraphGel deployments have ever been scored.** No outcome number
   here is a claim about the world stronger than "measured in simulation on
   real LANDFIRE fuel and real OSM roads, under the live watcher's physics."

Every scenario in this program runs the **live watcher's physics** — the
model-error band 0.71, the extinction gate, the φ-vector slope/wind term, the
gust blend, the grass floor — i.e. the deadlines crews really face
(`docs/FIX_PROGRAM_2026-09-11.md`). The old narrow band is not quoted anywhere
in this document.

---

## 1. Executive summary

**What was built.** Six community presets on real LANDFIRE tiles and real
OSM roads — Vail, Beaver Creek / Avon / Edwards, Eagle / Gypsum, Glenwood
Springs, Basalt / El Jebel / Carbondale, Aspen / Snowmass Village — a
corridor road graph with a measured drive-time matrix from the Eagle base,
18 planning-grade staging anchors, a watcher AOI file, and a measured grid:
**18 scenarios × 6 communities × 40 homes across 10 crew / basing /
placement arms — 972 reactive scenario runs, 38,880 home-instances — under
the live watcher's physics, plus 63 sixty-hour alerted-regime runs across 4
arms.** All six communities are in the public simulator's location list.

**The corridor's shape decides the program.** Vail and Aspen are 151
minutes apart under fire traffic; the Eagle base is 8–59 minutes from the
I-70 valley and 74–107 minutes from the Roaring Fork; the only paved link
between the valleys is Glenwood Canyon, which the Grizzly Creek Fire closed
for two weeks in August 2020 (§2). **[CORR]**

**What the grid measured [CORR], road-placed homes, ember-belt coating rate:**

| Question | Answer |
|---|---|
| Two local crews, reactive, pooled | **33.6%** of the ember belt (Eagle 50% … Glenwood 24%) |
| Four local crews | **47.4%** — **+13 points in every community**, Vail to Aspen |
| Two local + two Eagle crews | +4 to +7 inside 45 minutes (Eagle, Beaver Creek, Vail); **≤ +1 beyond** (Glenwood, Basalt, Aspen) |
| One local + three Eagle crews ("fleet at the base") | never better than two local crews outside Eagle; −6 to −10 beyond the canyon |
| Four hours of notice (3–5 h band) | 25% with two crews, 37% with four; zero below two hours everywhere |
| Alerted (detected 15 km out), two local crews | **80%** pooled (73–92%) |
| Alerted, one local + three base crews | **87%** pooled (84–90%), **from Eagle at 122 min or from Basalt at 55 min alike** |
| A Roaring Fork base at Basalt / El Jebel | does for Aspen what Eagle does for Vail (+5.7 reactive at 55 min; 90% alerted); nothing for Glenwood from either base |
| Crew-safety departure violations | **0** in all 1,035 scenarios; every 10c backstop row inspected and classified (§3.1) |

**The program (§4).** Local crews in every enrolled community — two at
launch, four where reactive coverage is sold, because that is the only lever
that moves the reactive number; **two bases, one per valley** (Eagle, and a
new one at Basalt / El Jebel), each the alerted-regime fleet for its own
valley and reactive reinforcement inside 45 minutes, so that nothing in the
program depends on the canyon; Glenwood Springs served by its own crews;
gel staged in both valleys. Five of the eighteen planning-grade anchors sit
downwind of the hazard axis and must be re-sited or demoted to alerted-
regime assembly points before any dispatch (§4.3).

**The honest boundary.** These are coating rates under the model's gel
assumptions, on 15 simulated hours with detection at ignition. Gel
effectiveness that converts a coat into a save is **[PENDING]**; the engine
is a surface-fire model on timber ground where crown runs are the
community-killers (§6.6); the extreme-spread posture never arms here, so the
honest band is the whole margin (§4.4); and the road-placement figures
depend on a placement rule, not a client list. The safety record is measured,
not assumed: **0 departure-audit violations**, and every backstop row is a
crew on an escape leg inside the pessimistic band or on cold black after
the front — including crews parked on Glenwood's No Name interchange after
the hazard-axis fire ran over it, which is why that zone is on the
re-siting list.

---

### Three things this program corrects in the corridor's premises

| Premise as one might brief it | What the measurement says |
|---|---|
| "One Eagle base with a fleet covers Vail to Aspen" | Reactively it covers ~45 minutes of road — Eagle, Beaver Creek, Vail — and nothing beyond; the Roaring Fork needs its own crews and its own base (§3.3, §3.6). |
| "More crews saturate, buy detection instead" (the five-map and Rancho Santa Fe finding) | True at 5–8 homes per crew. At 20 homes per crew on mountain roads the third and fourth local crew are worth +13 points everywhere (§3.2). Buy both. |
| "The pessimistic posture will protect us in a wind event" | It cannot arm here — 40% grass at most against a 60% gate (§2.5). The honest band is the margin, and the alerts will read early. |

---

## 2. The corridor

### 2.1 Geography: two valleys, one hinge, one base

The corridor is ~150 km of road between two dead ends. From the east: **Vail**
(I-70, Gore Creek, 2,500 m), **Beaver Creek / Avon / Edwards**, **Eagle /
Gypsum** (the PD Mountain Resorts HQ and the Eagle County airport — the
corridor's forward base), then I-70 through **Glenwood Canyon** to **Glenwood
Springs**, where SH-82 turns south-east up the Roaring Fork through
**Carbondale / El Jebel / Basalt** to **Aspen / Snowmass Village**. Vail and
Aspen are 64 km apart in a straight line and 151 minutes apart by road under
fire traffic **[CORR]** — and the only paved connection between the two
valleys is the canyon. East of Aspen, SH-82 over Independence Pass (3,687 m)
is closed from roughly November to May; it is on no shortest path in this
program either way.

Six community tiles carry the program, each ~18 × 18 km at 90 m (standard) and
~48 × 48 km at 240 m (extended, for far-detection scenarios), on the same tile
and road laws as the five original simulator locations
(`docs/SIMULATOR_DESIGN.md`, "Vail → Aspen corridor presets"). No city-limits
polygon is used: in these valleys the mountain WUI band *is* the towns.

A seventh, whole-valley preset — `eagle_vail_co`, **Eagle → Vail corridor**
(founder 2026-09-25) — puts the entire I-70 Eagle Valley from Gypsum to East
Vail on one ~60 × 60 km tile at 200 m with the six I-70 interchange anchors
(the Eagle base first), so the Eagle-base question plays in a single
session: a fire near Eagle, crews rolling from exit 147, homes from Edwards to
Vail. It complements the three per-community Eagle Valley presets the grid in
§3 was measured on (their tiles, zones and results are unchanged); its homes
seed only within 300 m of a road (the §3.7 rule), and it has no extended tile —
the standard tile already spans ~30 km each way (`docs/SIMULATOR_DESIGN.md`,
"Eagle → Vail corridor preset").

### 2.2 Drive times from the Eagle base — measured on OSM

Corridor road graph: OSM major roads (motorway through tertiary, plus links)
over the corridor bbox, 993 nodes / 1,442 edges, largest component 99.6%,
routed with the engine's own `roads.route` (OSM class speeds: I-70 ≈ 65 mph,
SH-82 ≈ 49 mph; `fire_traffic_factor = 1.4` for the fire column). **[CORR]**
`data/roads/corridor_vail_aspen.wgr`, `scratch_review/corridor_drive_times.json`.

| From the Eagle base (I-70 exit 147) to | km | free-flow | **fire traffic** |
|---|---|---|---|
| Eagle / Gypsum (the base's own community) | 6.9 | 6 min | **8 min** |
| Beaver Creek / Avon / Edwards | 31.3 | 20 min | **29 min** |
| Glenwood Springs | 50.7 | 30 min | **42 min** |
| Vail / East Vail / Minturn | 50.9 | 32 min | **44 min** |
| Basalt / El Jebel / Carbondale | 80.1 | 53 min | **74 min** |
| Aspen / Snowmass Village | 112.0 | 77 min | **107 min** |

Community to community, fire traffic, minutes **[CORR]**:

| from \ to | Vail | Beaver Creek | Eagle | Glenwood | Basalt | Aspen |
|---|---|---|---|---|---|---|
| Vail | — | 26 | 52 | 86 | 118 | 151 |
| Beaver Creek | 26 | — | 36 | 70 | 102 | 135 |
| Eagle | 52 | 36 | — | 40 | 73 | 106 |
| Glenwood Springs | 86 | 70 | 40 | — | 34 | 67 |
| Basalt | 118 | 102 | 73 | 34 | — | 40 |
| Aspen | 151 | 135 | 106 | 67 | 40 | — |

Read the first table against the crew-safety law: a crew is dispatched only
if it can reach the home, coat it, and be off the property **30 minutes
before the pessimistic (P10) arrival**. A crew that is 44 minutes away when
the fire is detected has spent 44 minutes of every home's deadline before it
turns a wheel on the tile; a crew 107 minutes away has spent most of a
four-hour window. §3 measures exactly what that costs.

What these numbers do **not** contain **[ASSUMED]**: grades, chain laws,
construction, and evacuation traffic beyond the flat 1.4× — and on the Aspen
side SH-82 is the *only* paved road out, so an Aspen evacuation and a crew
ingress share one carriageway. Treat the Roaring Fork figures as floors.

### 2.3 Severance: Glenwood Canyon

I-70 through Glenwood Canyon is the corridor's single point of failure. The
Grizzly Creek Fire closed it for two weeks in August 2020 — during fire
season, *because of* a fire; rockfall closures are routine. With the canyon
edges removed from the graph **[CORR]**:

| Eagle base to | canyon open | canyon closed | what the closed route is |
|---|---|---|---|
| Vail, Beaver Creek, Eagle | 44 / 29 / 8 min | unchanged | I-70 east of the canyon is untouched |
| Glenwood Springs | 42 min | 93 min | unpaved seasonal back road (Crooked Creek Pass via Sylvan Lake / Cottonwood Pass), charged at paved tertiary speed — **optimistic, summer only** |
| Basalt / El Jebel / Carbondale | 74 min | 80 min | same back roads |
| Aspen / Snowmass | 107 min | 112 min | same back roads |

The router finds those bypasses because OSM tags the county roads `tertiary`;
it does not know they are gravel, single-lane, and closed in winter. The
honest reading is: **when the canyon closes, the Roaring Fork is on its own
for anything reactive, and in the shoulder season it is unreachable from
Eagle.** A single Eagle base cannot underwrite the Roaring Fork. §4.1 builds
the program on that fact.

### 2.4 Hazard winds and the analog fires

The western-slope fire wind is the dry-cold-front / prefrontal south-west to
west-south-west flow, channelled along the east-west Eagle Valley
(`hazard_wind_dir_deg = 240`) and crossing the north-west-trending Roaring
Fork (`225`). The named events the presets are pinned to **[ASSUMED]**, as
geography and wind, not as measured runs:

| Community | Analog event | What it teaches the program |
|---|---|---|
| Glenwood Springs | Storm King (July 1994, 14 firefighters killed, W/NW frontal wind shift); Coal Seam (June 2002, 29 homes lost in West Glenwood under strong W-SW wind); Grizzly Creek (Aug 2020, I-70 closed two weeks) | The hinge burns, and when it burns the corridor is cut. |
| Basalt / El Jebel | Lake Christine (July 2018, 3 homes lost at El Jebel, erratic SW wind, night-time run) | Roaring Fork WUI runs happen at night under gusty SW flow — detection lead is the product. |
| Eagle | Sylvan (June 2021, south of Eagle, no homes lost) | The base's own backyard is Gambel-oak / pinyon-juniper WUI. |
| Vail, Beaver Creek, Aspen | No modern town-threatening run on record; hazard wind by valley climatology | Absence of history is not absence of exposure — these are timber-and-oak interfaces with one road out. |

The grid rotates the hazard axis ±30° and ignites 6 and 8 km upwind at three
wind regimes (moderate 18 mph, high 28 mph, extreme 40 mph — the intensity
presets *are* wind speeds), so every community is scored against the whole
approach arc, not one bearing.

### 2.5 Fuel picture per community

Recomputed today from the six standard tiles (200 × 200 @ 90 m,
LF2024 FBFM40; `scratch_review/corridor_fuel.py`). Class shares are of
*burnable* cells; GR+GS is the fraction the extreme-spread-regime detector
gates on. **[CORR]**

| Community | Burnable | Developed | GR | GS | SH | TU | TL | **GR+GS** (gate 0.60) | WUI dev cells (r≤3) | Elev m | Slope max | Top codes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Vail / East Vail / Minturn | 89.1% | 4.0% | 4.5% | 2.3% | 4.6% | 59.6% | 29.0% | **6.8%** | 1,608 | 2,324–3,986 | 64° | TU5 27.5, TU1 22.4, TL5 11.1 |
| Beaver Creek / Avon / Edwards | 92.2% | 6.6% | 5.2% | 7.4% | 10.5% | 59.6% | 17.3% | **12.6%** | 2,649 | 2,124–3,704 | 56° | TU1 27.6, TU5 23.3, TL3 6.8 |
| Eagle / Gypsum (base) | 90.8% | 7.5% | 6.7% | 33.5% | 15.9% | 10.0% | 33.9% | **40.1%** | 2,985 | 1,893–3,003 | 47° | TL3 27.0, GS2 17.5, SH2 10.6 |
| Glenwood Springs | 93.9% | 4.8% | 6.6% | 11.7% | 25.3% | 34.1% | 22.3% | **18.3%** | 1,909 | 1,719–3,192 | 72° | TU1 21.8, SH4 21.3, TL3 11.9 |
| Basalt / El Jebel / Carbondale | 87.3% | 11.6% | 12.3% | 21.8% | 26.7% | 12.5% | 26.8% | **34.0%** | 4,619 | 1,854–3,216 | 46° | TL3 20.6, SH4 19.5, GS2 13.6 |
| Aspen / Snowmass Village | 90.3% | 6.5% | 5.3% | 6.6% | 17.3% | 53.8% | 17.1% | **11.9%** | 2,604 | 2,186–4,023 | 61° | TU1 22.2, TU5 21.6, SH4 12.7 |

Transit cold-black windows (how long a burned cell stays a route hazard after
the front passes; `fuels.cold_black_minutes`, share of burnable ground):

| Community | ≤10 min (grass) | 20–30 min (shrub) | 45 min (TU) | 60 min (TL) |
|---|---|---|---|---|
| Vail | 6.8% | 4.6% | 59.6% | 29.0% |
| Beaver Creek | 12.6% | 10.5% | 59.6% | 17.3% |
| Eagle / Gypsum | 40.1% | 15.9% | 10.0% | 33.9% |
| Glenwood Springs | 18.3% | 25.3% | 34.1% | 22.3% |
| Basalt / Carbondale | 34.0% | 26.7% | 12.5% | 26.8% |
| Aspen / Snowmass | 11.9% | 17.3% | 53.8% | 17.1% |

What the table says:

- **This is timber country, not grass country.** Vail, Beaver Creek and
  Aspen are 54–60% timber-understory (TU1/TU5) with 17–29% timber litter and
  only 7–13% grass. The grassiest ground on the corridor is the base's own:
  Eagle / Gypsum is 40% GR+GS (sagebrush-grass GS2 over pinyon-juniper litter
  TL3), Basalt / Carbondale 34%.
- **The extreme-spread-regime posture never arms here.** The detector gates
  on GR+GS ≥ 0.60 of the burnable domain; the highest corridor tile is 0.40.
  Every rung of the ladder therefore evaluates on the median with the honest
  band (§4.4) — there is no second, pessimistic posture waiting behind a
  wind forecast, as there is at Rancho Santa Fe (0.80, effectively
  always-on). The margin on this corridor is the band itself.
- **The model's measured bias is favorable on this fuel, with a stated
  exception.** Out of sample the engine forecasts the timber classes best
  (late-miss TU 8% / TL 16% against GS 64% / GR 52%). The exception is that
  it is a surface-fire model: crown runs and long-range spotting through
  spruce-fir, lodgepole and Gambel oak (SH4, 13–25% of the Roaring Fork and
  Glenwood tiles — the fuel Lake Christine ran through) are not in it (§6.6).
- **Cold-black is long here.** At Vail 89% of burnable ground stays a
  45–60-minute transit hazard after the front passes; at Eagle 40% reopens
  within ten minutes. A crew cut off behind the front in the upper Eagle
  Valley waits an hour for drivable black, not minutes — the opposite of the
  grass-shrub geography at Rancho Santa Fe.
- **Steep.** Maximum slopes 46–72°, elevations 1,700–4,000 m. The φ-vector
  slope/wind term armed live on 2026-09-11 (the review had measured a 1.9×
  slow bias on opposed slopes without it) is doing real work on these tiles.
- **Small towns in big fuel.** Developed ground is 4–12% of each tile
  (Rancho Santa Fe 47.8%, Boulder 36.6%); the WUI band runs 1,600–4,600
  cells, Basalt / El Jebel / Carbondale the largest and the most town-like.
  Developed ground is not a fuel break at this scale: the fire crosses
  continuous fuel to the town edge and the homes sit in it.
- Water ≤ 1%, LANDFIRE nodata 0% — the tiles are clean.

---

## 3. What the grid measured

**Design.** Six communities × the Rancho Santa Fe 18-scenario matrix
(intensity moderate / high / extreme × ignition 6 / 8 km upwind × wind
offset 0 / +30 / −30 off the hazard axis) × 40 homes × 15 simulated hours,
under the live watcher's physics, sim defaults otherwise (calibrated corridor
stack, stage-1 cold-black, 200 m ember-threat radius, deterministic 20,000-
consult solver budget). **[CORR]** `scratch_review/corridor_grid.py`;
tables `docs/calibration/vail_aspen_grid_2026-09-23.md`; per-home rows in
`scratch_review/corridor/`.

Two placements were run. The simulator's default seeds clients across the
whole LANDFIRE WUI band, and on resort tiles that band includes 'developed'
cells on the ski mountains — lift stations, on-mountain restaurants — with
no road; at Vail **25% of the placed homes were more than 2 km from any
road** (`scratch_review/corridor_roadless.py`), none elsewhere but Basalt
(0.4%). A real client has a driveway, so **the program's headline placement
draws the same 40 homes from WUI cells within 300 m of a road** (`_road`
arms); the whole-band placement is reported as the sensitivity (§3.7).
Since 2026-09-25 the six corridor presets in the simulator seed their homes
the same way by default (`SimLocation.place_near_road_m = 300`, roads
inspection `docs/review/roads_inspection_2026-09-25.md`), so a public
session plays the headline placement; the grid harness pins its whole-band
arms explicitly (`place_near_road_m=0.0`).

Arms, all with the same seeds so comparisons are paired:

| arm | crews | where the crews start |
|---|---|---|
| `local2` | 2 | the community's own safe zones, available at detection |
| `local4` | 4 | same |
| `mixed2p2` | 2 + 2 | 2 local; 2 from the Eagle base, arriving after the measured all-season drive under fire traffic + 15 min mobilization **[ASSUMED]** (Eagle 23 · Beaver Creek 44 · Glenwood 57 · Vail 59 · Basalt 89 · Aspen 122 min) |
| `base1p3` | 1 + 3 | 1 local; 3 from the Eagle base (the session requires ≥ 1 local crew) |
| `alerted2`, `alerted1p3` | 2 / 1 + 3 | the ALERTED regime: extended tile, fire detected ~15 km out on the hazard bearing, 60 h (§3.5) |
| `…_rf` | 2 + 2 / 1 + 3 | corridor crews from a Basalt / El Jebel base instead (§3.6) |

Three denominators, never mixed: **belt** = coated among homes within 1 km
of actual burn or reached by the front; **of threatened** = coated among
homes the forecast gave a live arrival; **lead bands** = coated among homes
the ground-truth fire reached, by detection-to-arrival lead (3–5 h is the
"four hours of notice" band of the 4-hour experiment). Because the solver is
deterministic, a paired difference between arms is exact for these seeds;
its *generalization* is not, and pooled differences under ~2 pp (per
community ~5 pp, with 14–43 reached homes in the 3–5 h band) should not be
read as findings.

### 3.1 Safety first — what the audit recorded

The crew-safety law is not negotiable, so every arm was inspected before any
coating number was read (`scratch_review/corridor_viol.py` re-runs every
violating scenario and classifies each audit row):

| arm (road placement) | violating scenarios / 108 | **departure-audit rows** | 10c imminent-while-egressing crew-ticks | 10c burned rows (egress / idle) |
|---|---|---|---|---|
| local2 | 40 | **0** | 108 | 1 / 1 |
| local4 | 47 | **0** | 262 | 6 / 3 |
| mixed2p2 | 63 | **0** | 264 | 3 / 1 |
| base1p3 | 62 | **0** | 276 (+2 imminent-idle) | 6 / 2 |
| mixed2p2 from a Basalt base (3 communities / 54) | 29 | **0** | 146 | 1 / 1 |
| base1p3 from a Basalt base (3 communities / 54) | 32 | **0** | 150 | 2 / 2 |
| *(whole-band placement)* | 43 · 50 · 64 · 68 | **0 · 0 · 0 · 0** | 132 · 275 · 299 · 296 | 4/2 · 6/2 · 6/2 · 4/1 |
| *(alerted arms, §3.5)* | 5–11 of 9–18 | **0 · 0 · 0 · 0** | 30–54 (+32–64 idle) | 276–550 / 0 |

**Zero departure-audit violations in all 1,035 scenarios (972 reactive, 63
alerted; `scratch_review/corridor/violations.json`)** — no serviced stop ever
departed after its latest-depart, the failure the law exists to prevent. Everything the scoreboard counts is the honest 10c backstop
shipped on 2026-09-11, and it divides into two classes:

- **Imminent-while-egressing** (the bulk): a crew on its escape leg
  crossing a cell whose *pessimistic* P10 arrival is inside the 30-minute
  buffer — the same class every base-arm violation in the 4-hour experiment
  belonged to **[4H]**. Under the honest 0.71 band the pessimistic surface
  is wide and often early (worst row: 838 minutes past a P10 that the
  actual fire never made good on); these are exposures the band *says*
  exist, on ground the fire did not in fact reach.
- **Burned rows** (2–9 per arm): a crew standing on a cell the nominal
  front has already passed. Every one was inspected. All but one sit on
  **cold black 102–662 minutes old**; the exception is a single 9-minute row
  on Eagle grass (GS2, cold-black window 10 min). The idle rows are all the
  same event: in the high-intensity 8-km hazard-axis Glenwood run the fire
  crosses the **No Name interchange (zone z2) 3.5 h after ignition**, and
  the crews staged there sit on the zone's black for the next two hours —
  the survivable place, counted honestly. The egress rows at Eagle are crews
  on escape legs through 3–8-hour-old black within 1.4–3 km of the exit 147
  node. Under the founder's ruling of 2026-09-26 (*"old burned ground does
  not count as a violation"*; docs/SIMULATOR_DESIGN.md "Old black is a
  refuge; crews that leave the map are out (2026-09-26)") every one of these
  burned rows on cold black 102–662 minutes old would no longer count — only
  the 9-minute Eagle GS2 row, still inside its 10-minute window, would — and
  the table above stays the historical record under the strict audit.

**What that means for the program.** Two of the planning-grade zones —
Glenwood's No Name (bearing 053°) and Eagle's exit 147 (077°) — sit
*downwind* of the 240° hazard axis, exactly where a hazard-axis fire goes.
The engine handled it (fail-closed zone choice plus SEM-10 relocation; no
crew was ever in fresh fire), but the ground truth is that a downwind
interchange is not a refuge. Field verification (§6.3) must add an upwind
or cross-wind anchor for every community, and the Eagle base node itself
should not be the egress anchor for Eagle's own hazard-axis fires. By the
same rule (within 45° of the down-axis bearing 060°), Vail's East Vail exit
(095°), Avon (068°) and Eagle-Vail (093°) are downwind anchors too; the
Roaring Fork nodes all sit cross-wind of the 225° axis (§4.3).

### 3.2 Local crews: two versus four

**[CORR]**, road placement, belt = coated / homes within 1 km of burn:

| Community | 2 local crews — belt | of threatened | 4 local crews — belt | of threatened | Δ belt |
|---|---|---|---|---|---|
| Vail / East Vail / Minturn | 34.9% | 41.9% | **47.7%** | 53.7% | +12.8 |
| Beaver Creek / Avon / Edwards | 31.6% | 38.9% | **45.2%** | 52.5% | +13.6 |
| Eagle / Gypsum | 49.6% | 57.6% | **62.8%** | 72.3% | +13.2 |
| Glenwood Springs | 23.9% | 30.9% | **36.9%** | 46.2% | +13.0 |
| Basalt / El Jebel / Carbondale | 28.9% | 52.2% | **47.0%** | 64.7% | +18.1 |
| Aspen / Snowmass Village | 29.8% | 42.1% | **43.3%** | 55.9% | +13.5 |
| **Pooled (108 scenarios each)** | **33.6%** | 43.7% | **47.4%** | 57.3% | **+13.8** |

Two things the table says that the five-map catalog did not:

1. **The third and fourth crew are worth +13 points in every community.**
   The five-map crew ladder saturated (10 → 24 crews bought nothing) and the
   Rancho Santa Fe grid was flat from 8 to 12 crews; those were fleets of
   5–8 homes per crew. At 20 homes per crew, on tiles where every leg is a
   mountain road and cold-black lasts an hour, response is *crew-bound*, and
   the second pair of crews pays almost identically from Vail to Aspen.
2. **Eagle / Gypsum is the easy ground, Glenwood Springs the hard one.**
   The base's own community, the grassiest and flattest tile (§2.5),
   coats 50–63%; the canyon hinge, 94% burnable with slopes to 72°, coats
   24–37%. The order is the fuel order: open sagebrush-grass first, then
   the Roaring Fork mix, then the timber-understory valleys, then the canyon.

### 3.3 Corridor crews from the Eagle base: what the drive costs

**[CORR]**, road placement, belt; Δ against two local crews:

| Community | Eagle crews arrive after | 2 local + 2 Eagle | Δ | 1 local + 3 Eagle | Δ |
|---|---|---|---|---|---|
| Eagle / Gypsum | 23 min | **56.5%** | +6.9 | **56.5%** | +6.9 |
| Beaver Creek / Avon / Edwards | 44 min | 35.7% | +4.1 | 30.0% | −1.6 |
| Glenwood Springs | 57 min | 24.6% | +0.7 | 17.9% | −6.0 |
| Vail / East Vail / Minturn | 59 min | 38.9% | +4.0 | 35.4% | +0.5 |
| Basalt / El Jebel / Carbondale | 89 min | 30.1% | +1.2 | 20.9% | −8.0 |
| Aspen / Snowmass Village | 122 min | 30.5% | +0.7 | 19.6% | −10.2 |
| **Pooled** | | **36.8%** | **+3.2** | **31.0%** | **−2.6** |

The measurement is unambiguous:

- **The reactive reach of a base is about 45 minutes.** Two Eagle crews
  added to two local crews are worth +4 to +7 points at Eagle, Beaver Creek
  and Vail, and +1 or less beyond that — Glenwood (57 min, and the fastest
  fires on the corridor) gets nothing, Basalt and Aspen get nothing. The
  homes the fire reaches are decided before a crew has driven an hour.
- **The base-fleet posture never beats two local crews outside the base's
  own community.** One local crew plus three from Eagle is a wash at Vail
  and Beaver Creek and loses 6–10 points at Glenwood, Basalt and Aspen.
  Crews at the base are not crews in the community.
- **Crews compound only when they are already there.** Two more *local*
  crews (+13, §3.2) are worth three to four times two Eagle crews at the
  same communities.

### 3.4 Lead-time bands — four hours of notice on this ground

Coated / reached by detection-to-arrival lead, pooled over the six
communities, road placement **[CORR]**:

| arm | 0–1 h | 1–2 h | 2–3 h | **3–5 h** | 5–6 h | 6–8 h | 8–12 h | 12–16 h |
|---|---|---|---|---|---|---|---|---|
| 2 local | 0% | 6% | 21% | **25% (37/150)** | 25% | 18% | 26% | 40% |
| 4 local | 0% | 15% | 32% | **37% (55/150)** | 42% | 37% | 49% | 56% |
| 2 local + 2 Eagle | 0% | 6% | 21% | **25% (38/150)** | 25% | 26% | 31% | 41% |
| 1 local + 3 Eagle | 0% | 10% | 12% | **21% (32/150)** | 22% | 25% | 27% | 34% |

Read against the 4-hour experiment **[4H]**, which measured the live band
at 27% in the 3–5 h band with **eight** crews on the five-map suite: the
corridor with **two** crews is at 25%, with four at 37%. Below two hours of
lead the coating rate is zero here as everywhere; the curve with two crews
is flat at ~25% from 3 h to 12 h of lead — a crew-bound plateau, not a
lead-time one — and only the four-crew arm climbs with lead (32 → 56%).
On this corridor, lead time buys coverage only when there are crews on the
ground to spend it.

### 3.5 The alerted regime — where the corridor fleet earns its keep

Extended tiles (48 × 48 km), fire detected ~15 km out on the hazard bearing
(offsets 0 / +30 / −30, high intensity), 60 simulated hours, 2 local crews vs
1 local + 3 corridor crews; road placement. **[CORR]** Belt = coated / homes
within 1 km of burn; "of threatened" in brackets.

| Community | 2 local crews | 1 local + 3 from Eagle (arrive after) | Δ |
|---|---|---|---|
| Vail / East Vail / Minturn | 72.5% (79/118) | **86.2%** (96/118) — 59 min | +13.7 |
| Beaver Creek / Avon / Edwards | 76.9% (90/116) | **88.0%** (105/116) — 44 min | +11.1 |
| Eagle / Gypsum | **92.1%** (105/110) | 89.5% (102/110) — 23 min | −2.6 |
| Glenwood Springs | 77.0% (79/114) | **84.0%** (87/114) — 57 min | +7.0 |
| Basalt / El Jebel / Carbondale | 80.2% (82/111) | **83.2%** (85/111) — 89 min | +3.0 |
| Aspen / Snowmass Village | 78.8% (91/118) | **90.3%** (104/118) — 122 min | +11.5 |
| **Pooled (18 runs each)** | **79.7%** (526/687 of threatened = 76.6%) | **87.0%** (579/687 = 84.3%) | **+7.3** |

This is the other half of the basing answer. Detected 15 km out — the
WATCH window, hours of lead — **two local crews coat 73–92% of the ember
belt, and the corridor fleet lifts that to 84–90% in every community but
the base's own, including Aspen at 122 minutes.** With lead, the drive is
irrelevant: the crews are prepositioned under the 24-hour hold long before
the deadlines bite, which is exactly what the hold is for (§4.5). Compare
the reactive §3.3, where the same three Eagle crews were worth nothing at
Aspen: **the base fleet is an alerted-regime asset, and the program should
say so in those words.**

The first ladder event fires with the pessimistic (P10) arrival 0.7–4.0 h
out in every run — the honest band is wide, and at 15 km it says "could be
here in an hour"; the median and the ground-truth arrivals are many hours
later. Homes the fire reached inside 3–5 h of detection are rare in these
runs (0–4 per community), so the four-hour band is not the story here; the
8–16 h and 16 h+ bands are.

**Safety in the alerted runs.** Zero departure-audit violations again. The
10c rows are dominated by *burned* crew-ticks 2–12 hours after the front
passed, 15–20 km from any zone: crews that have egressed clear off the
community's road graph onto the far side of the fire zone and sit on cold
black for the rest of a 60-hour run (Glenwood: 139 of 240 ticks). That is
the survivable place and the audit counts it; it is also a reminder that the
road graph covers the community box, not the whole 48 km tile — long
escapes beyond it move on straight legs (the run log's `straight-leg
fallback` lines).

### 3.6 A Roaring Fork base — Basalt / El Jebel instead of Eagle

The same corridor-crew arms with the crews starting at Basalt / El Jebel
(Glenwood 49 min, Aspen 55 min, its own community 23 min), on the three
Roaring Fork-side communities; road placement **[CORR]**:

| Community | 2 local | + 2 from **Eagle** (arrive) | + 2 from **Basalt** (arrive) | 1 local + 3 from Basalt |
|---|---|---|---|---|
| Glenwood Springs | 23.9% | 24.6% (57 min) | 24.6% (49 min) | 17.9% |
| Basalt / El Jebel / Carbondale | 28.9% | 30.1% (89 min) | **34.5%** (23 min) | 29.7% |
| Aspen / Snowmass Village | 29.8% | 30.5% (122 min) | **35.5%** (55 min) | 27.2% |
| *alerted, 1 local + 3 crews* | *2 local: 77.0 / 80.2 / 78.8%* | *84.0 / 83.2 / 90.3%* | *84.0 / 85.1 / 90.3%* | |

A Roaring Fork base does for Aspen what Eagle does for Vail: **+5.7 points
reactively at 55 minutes** (Vail from Eagle at 59 min: +4.0), +5.6 at
Basalt itself, and the same 84–90% in the alerted regime. Glenwood Springs
gets nothing from either base — 49 or 57 minutes, the canyon's fires are
faster than any drive — and the base-fleet posture (one local crew) loses
at Glenwood and Aspen exactly as it did from Eagle. **The Roaring Fork is
worth a base for the same reasons the Eagle Valley is, and Glenwood Springs
needs its own crews regardless of where either base sits.**

### 3.7 Placement sensitivity — whole WUI band vs road-proximity

**[CORR]**, belt, 2 / 4 local crews:

| Community | whole band, 2 | road, 2 | whole band, 4 | road, 4 |
|---|---|---|---|---|
| Vail / East Vail / Minturn | 25.5% | 34.9% | 33.7% | 47.7% |
| Beaver Creek / Avon / Edwards | 29.1% | 31.6% | 44.3% | 45.2% |
| Eagle / Gypsum | 49.3% | 49.6% | 61.3% | 62.8% |
| Glenwood Springs | 22.5% | 23.9% | 35.5% | 36.9% |
| Basalt / El Jebel / Carbondale | 26.7% | 28.9% | 47.0% | 47.0% |
| Aspen / Snowmass Village | 23.3% | 29.8% | 39.2% | 43.3% |
| **Pooled** | 29.4% | **33.6%** | 42.7% | **47.4%** |

The two resort tiles move (Vail +9 to +14, Aspen +4 to +7); the other four
are within 2.5 points. Every conclusion in §3.2–3.4 holds under both
placements; the road placement is the one a real roster resembles.

---

## 4. Recommended program

### 4.1 Basing: local crews everywhere, two bases, nothing that depends on the canyon

The grid separates the two things a "base" can be, and they are not the
same thing:

- **A base is not reactive coverage.** Beyond ~45 minutes' drive, crews at
  a base add nothing to the homes a fire reaches (§3.3, §3.6); inside it
  they add 4–7 points. The only lever that moves the reactive number
  everywhere is crews *in* the community: +13 points for the third and
  fourth local crew, from Vail to Aspen (§3.2).
- **A base is the alerted-regime fleet.** With hours of lead, three base
  crews lift every community except the base's own by 7–14 points to
  84–90% of the ember belt, at 122 minutes as readily as at 44 (§3.5).

Therefore:

1. **Local crews in every enrolled community** — two at minimum, four
   where reactive coverage is the promise (the resort communities and
   Glenwood Springs, whose fires outrun every drive). Two local crews coat
   ~34% of the ember belt pooled; four coat ~47%; nothing else changes
   that number.
2. **Two bases, one per valley.** The Eagle base (PD Mountain Resorts HQ)
   serves the I-70 valley — Eagle, Beaver Creek / Edwards, Vail — as
   alerted-regime fleet and as reactive reinforcement inside 45 minutes. A
   **Roaring Fork base at Basalt / El Jebel** does the same for Basalt /
   Carbondale and Aspen / Snowmass (§3.6: +5.7 at Aspen reactively, 90% in
   the alerted regime). Each base covers its own valley in full; when
   Glenwood Canyon closes (§2.3) nothing in the program has to cross it.
3. **Glenwood Springs is served by its own crews and by both bases only in
   the alerted regime.** Neither base reaches it reactively; it is the
   hinge and the hardest ground on the corridor.
4. **Gel and rigs are staged in both valleys** (the Rancho Santa Fe split-
   inventory rule, now for a severable corridor): a Glenwood Canyon closure
   must not strand the Roaring Fork's gel at Eagle.

### 4.2 Crew count and composition

Measured ladder, road placement, 40 homes per community **[CORR]**:

| Posture | Reactive belt, pooled | Reactive 3–5 h band | Alerted belt, pooled |
|---|---|---|---|
| 2 local crews per community | 33.6% | 24.7% | 79.7% |
| 2 local + 2 base crews (inside 45 min) | 36.8% | 25.3% | — |
| **4 local crews per community** | **47.4%** | **36.7%** | — |
| 1 local + 3 base crews (with lead) | 31.0% reactive | 21.3% | **87.0%** |

- **Launch posture: two local crews per community plus a three-crew fleet
  at each base** — the alerted regime is the product (the HOA playbook's
  doctrine, now measured on this corridor at 84–90%), and the base fleet is
  what makes it a *corridor* product rather than six separate ones.
- **Growth posture: four local crews** wherever the reactive promise is
  sold. On this ground it is the only thing that buys reactive coverage,
  and it buys +13 points everywhere.
- **Composition unchanged:** 3-person crews (two applicators + a prep-
  runner), 15 min/home **[ASSUMED]** — every number here was measured at
  deploy 15, not the 10-minute program target; see §6.9. The documented
  density law (≤ 5 homes per crew for reactive odds ≥ 57%) is not met at 40
  homes / 4 crews and the governing documents should say the communities are
  enrolled in the alerted-regime-or-evacuate posture.
- **Arithmetic check on the alerted coat:** 40 homes × 15 min ÷ 5 crews
  (2 local + 3 base) = 2 h of application per crew; with mountain travel,
  a full community coat is a 3–5 h operation inside an EXTREME_24H window —
  consistent with the 84–90% the runs measure.

### 4.3 Staging anchors — 18 planning-grade nodes, five of them downwind

`simui.LOCATIONS` zones (bearing and distance from the community center;
drive from the Eagle base under fire traffic **[CORR]**; "downwind" = within
45° of the hazard axis's down-fire bearing, 060° in the Eagle Valley, 045°
in the Roaring Fork):

| Community | Zone | Bearing / km | From Eagle | Note |
|---|---|---|---|---|
| Vail | I-70 exit 180 / East Vail | 095° / 6.3 | 52 min | **downwind** |
| Vail | I-70 exit 171 / Dowd Junction | 251° / 7.0 | 38 min | upwind |
| Vail | Minturn / US-24 | 228° / 8.1 | 36 min | upwind |
| Beaver Creek | I-70 exit 167 / Avon | 068° / 3.4 | 27 min | **downwind** |
| Beaver Creek | I-70 exit 163 / Edwards | 310° / 4.3 | 22 min | cross-wind |
| Beaver Creek | I-70 exit 169 / Eagle-Vail | 093° / 7.6 | 31 min | **downwind** |
| Eagle | I-70 exit 147 / Eagle (base) | 077° / 5.4 | 0 min | **downwind** — not an egress anchor for Eagle's own hazard-axis fires |
| Eagle | I-70 exit 140 / Gypsum, airport | 265° / 4.5 | 11 min | upwind |
| Eagle | Brush Creek Rd / Eagle Ranch | 121° / 6.1 | 9 min | cross-wind |
| Glenwood | I-70 exit 114 / West Glenwood | 300° / 3.3 | 44 min | cross-wind |
| Glenwood | I-70 exit 119 / No Name | 053° / 3.9 | 39 min | **downwind — overrun at 3.5 h in the grid (§3.1)** |
| Glenwood | SH-82 / Cattle Creek | 156° / 9.2 | 53 min | cross-wind |
| Basalt | SH-82 / SH-133 Carbondale | 283° / 7.8 | 63 min | cross-wind |
| Basalt | SH-82 / Basalt Ave | 109° / 8.5 | 81 min | cross-wind |
| Basalt | SH-82 / Willits Ln | 106° / 4.7 | 76 min | cross-wind |
| Aspen | SH-82 / Brush Creek intercept lot | 344° / 4.5 | 100 min | cross-wind |
| Aspen | Aspen Main St / Mill St | 116° / 4.9 | 110 min | cross-wind |
| Aspen | Snowmass Village Town Park | 273° / 5.8 | 109 min | upwind |

Every community keeps at least one upwind or cross-wind anchor, which is
why the engine's zone choice held (0 departure violations); the field
verification (§6.3) should treat the five downwind nodes as *assembly*
points for the alerted regime, not as reactive egress anchors, and add an
upwind alternative where a community has only one.

### 4.4 Alert ladder mapped to the corridor

The shipped ladder (`alerts.py`, `ResponseConfig`):

| Rung | Trigger | On this corridor |
|---|---|---|
| **NOTICE_72H** | P50 ≤ 72 h (one-time; auto-progresses to WATCH) | the base fleet's prepositioning order |
| **WATCH** | ongoing threat inside the notice window | hold + preposition (§4.5) |
| **EXTREME_24H** | P50 ≤ 24 h **or** P10 ≤ 12 h | hold releases; full-community coat begins |
| **IMPACTED** | median arrival has passed | crews clear; post-impact assessment |
| **CLEARED** | 2 consecutive clear cycles with 6 h margin | |

**The extreme-spread-regime column does not exist here.** The detector
gates on GR+GS ≥ 0.60 of the burnable domain; the corridor's highest tile is
0.40 (§2.5), so every rung evaluates on the median with the honest 0.71
band — there is no pessimistic posture waiting behind a wind forecast. Crew
safety keys off P10 regardless, and that is the whole margin. In the
15-km-detection runs the first ladder event carried a P10 arrival 0.7–4.0 h
out (§3.5): the band is wide, alerts will read urgent early, and the
onboarding packet should say so once, properly.

### 4.5 The coating hold and prepositioning across a 107-minute corridor

`dispatch_hold_h = 24`: while a home's fire is more than 24 h out, crews do
not coat — they go on alert and preposition at the safe zone nearest their
first stop — as long as a per-cycle probe (deferred starts, deadlines
tightened by a 6 h weather buffer) confirms deferral still covers every home
the coat-now plan would; gate failure releases the hold, latched. On a
corridor where the base is two hours from Aspen, the hold is the mechanism
that turns three Eagle crews from nothing (reactive, §3.3) into +11.5
points (alerted, §3.5): the NOTICE_72H that fires two days out is the
dispatch order that puts them at the Brush Creek intercept lot with a day to
spare. The measured five-map hold benefit (+3.5 pp alerted, zero
violations) **[5MAP]** and the corridor's own alerted numbers are the same
finding from two directions.

---

## 5. The corridor protocol

### 5.1 Onboarding — what changes in the mountains

- **Season, not year-round.** Fire season here is June–October; the program
  stands up with the season. Crews, gel inventory, and the staging catalog are
  verified before the season, not on the first red-flag day.
- **Per-parcel data the engine needs** (unchanged from `HOA_40_PLAYBOOK.md`):
  verified structure lat/lon (a 90 m raster carries ±45 m, the largest error
  term in the model), gate codes and driveway clearance for the rig on a
  steep mountain drive, turn-around, spigots, **propane tanks** (near-universal
  off the gas grid), solar/battery cutoffs, animals, contact ladder with an
  evacuation-needs flag, and a **coating consent stating the coating is
  opaque, is applied only under live threat, and comes with a post-event
  wash-down**.
- **Defensible space is the single per-home lever.** The model treats a
  cleared parcel as un-burnable by surface fire — ember-only exposure. Annual
  pre-season inspection; pass required for the "protected" tier.
- **Enroll for dispersion, and say why.** The five-map and Rancho Santa Fe
  work both found that tight clusters synchronise deadlines and turn coverage
  into a lottery (`docs/rancho_santa_fe_plan.md` §1); the 4-hour experiment
  measured clustering as the single worst arm (−19 pp at four hours) **[4H]**.
  Run `tools/onboard.py` neighbourhood by neighbourhood on the community's
  own tile before accepting concentrated enrollment.
- **Two ground-verified safe zones per community, on opposite sides.** The
  engine is fail-closed on zones: no verified zone, no schedule. The 18
  anchors in §4.3 are planning-grade until someone drives them.

### 5.2 Coating-hold policy — what the client is told

> *While the fire is projected more than 24 hours away, we do not coat. Crews
> go on alert and move to staging. The system re-checks every cycle that
> waiting still lets us reach every home on the list — with a six-hour
> allowance for the weather turning worse. The moment that check fails,
> coating begins immediately and does not stop.*

On this corridor the hold is also the **prepositioning order for the base
fleet**: an Eagle crew ordered to Aspen at NOTICE_72H is at the Brush Creek
intercept lot two hours later, inside the community's own drive-time circle
for the rest of the event. Not coating is not inaction; the coating is opaque
and is not seasonal; re-coat only if rain has hit it or application-to-arrival
would exceed the 72 h window (**[PENDING]** — the 72 h figure conflicts 12×
with a ~6 h evaporative-gel model; if the supplier comes back short, this
policy inverts).

### 5.3 Communication ladder

The engine emits these; every message names its incident:

| Rung | Client-facing text (shipped wording) |
|---|---|
| **NOTICE_72H** | *"Projected fire arrival at [property]: ~X h (median), Y h (pessimistic) — [incident]. Advance notice — crews being scheduled."* |
| **WATCH** | *"Monitoring [property] for [incident]: projected fire arrival ~X h (median), Y h (pessimistic). Under continuous watch; crews scheduled as needed."* |
| **EXTREME_24H** | *"EXTREME alert for [property] — [incident]: projected fire arrival … Crew dispatch underway — ensure property access now."* |
| **IMPACTED** | *"Projected fire arrival time has passed at [property]. Crews are clear; post-impact assessment to follow."* |
| **CLEARED** | *"All clear for [property]: no projected fire arrival within the 72 h alert window."* Suppressed while the client holds any other active incident. |

### 5.4 Crew-safety laws the client sees

Coded laws (SEMANTICS 2/3/7/10), not operator-overridable downward. An
operator may HOLD a dispatch; never extend a deadline.

1. **The 30-minute rule.** Crews are off the property ≥ 30 minutes before the
   *earliest credible* (P10) arrival, not the median. Every stop carries a
   latest-depart time: leave by it, finished or not.
2. **15-minute slack floor.** A stop with under 15 minutes of planned slack is
   never dispatched. If reality eats the slack, call it in; the next cycle
   replans. Do not improvise on a mountain road.
3. **Corridors both ways.** Route in *and* route to the egress zone are
   checked against the eroded pessimistic fire surface every cycle.
   "Withdraw mid-coat" means withdraw mid-coat.
4. **Relocation orders (SEM-10).** An idle or staging position that becomes
   unsafe generates a relocate order. Comply immediately, acknowledge.
5. **ACK discipline.** Un-ACKed dispatches escalate at 10 minutes and the crew
   is dropped from the plan as unavailable.
6. **Beacon on at shift start.** A stale beacon bars dispatch by design.

### 5.5 What residents should expect

1. **NOTICE / WATCH:** confirm gate codes and access, clear the driveway,
   verify defensible space, charge phones, move vehicles nose-out, open gates,
   review the evacuation route. Eagle Valley: I-70 in either direction away
   from the fire. Roaring Fork: **SH-82 down-valley is the paved way out;
   Independence Pass is summer-only and single-lane in places.**
2. **You will probably see crews staging before you see them coating.** That
   is the hold working — and on this corridor it may be an Eagle crew
   arriving two hours after the first notice, exactly as planned.
3. **EXTREME_24H: evacuate when told. Do not wait to watch the coating.**
   Leave gates open. The crew's departure deadline is not negotiable.
4. **A no-notice ignition close by is the one case the service cannot
   cover.** Below about two hours between detection and arrival the coating
   rate is zero on every ground ever measured (`docs/rancho_santa_fe_plan.md`
   §3.4, `[4H]`); the product in that band is the alert and the evacuation.
5. **Expect losses, and expect them explained.** The engine deliberately skips
   homes only when the fire has beaten every safe round trip; the post-event
   report states which homes were coated, when, and what road blockages crews
   observed.
6. **A saved home comes back opaque.** Post-event wash-down is part of the
   service.

---

## 6. Open items

Ranked by what blocks the most.

### 6.1 Gel supplier data — the only blocker-grade item **[PENDING]**

Everything downstream is ungradeable without it: plan freshness math, the
24-hour coating hold, crew sizing, inventory, and any P(save) quoted to a
homeowner or an insurer. Needed: effective life vs temperature / RH / wind
(the shipped 72 h window vs the ~6 h evaporative-gel model — a 12× conflict
in the parameter that decides the entire dispatch posture); wet coverage rate
(gallons per home at 1/8" dry film — gates the minutes-per-home figure and
all inventory sizing); drying time to rated film **at altitude, in single-
digit summer humidity and high UV**; rain wash-off horizon; any measured
save-rate basis. Closes with a formulator data sheet or test report.

### 6.2 The Roaring Fork base — a cost decision, no longer a coverage question

Whether the Roaring Fork gets its own base is the program's structural
decision (§2.3, §4.1), and the coverage side of it is now measured: from
Eagle the corridor crews are worth nothing reactively at Basalt or Aspen
(§3.3); from Basalt / El Jebel they are worth +5.6 / +5.7 (§3.6); in the
alerted regime either base delivers 84–90% (§3.5). What remains is what a
second base costs against ~6 points of reactive coverage in two
communities plus canyon-closure independence — the founder's call. Basalt /
El Jebel is the geometric answer inside the valley: 34 minutes to Glenwood,
40 to Aspen, 23 to its own community, on the one road that connects all
three.

### 6.3 Field verification of the 18 staging anchors

Every zone is a planning-grade highway interchange, SH-82 junction or
park-and-ride, placed 3–13 km from its community center — plausible on the
map, driven by nobody. The engine is fail-closed on zones, so this is an
onboarding gate, not a nicety. Verify per zone: rig access and turn-around,
water, comms coverage (several Roaring Fork nodes sit in canyon shadow),
defensibility of the node itself, whether an I-70 interchange is usable as a
staging area during an evacuation (CDOT / State Patrol permission), and
whether it stays reachable when the valley's one road is carrying the
evacuation. Two verified zones on opposite sides per community, minimum.

### 6.4 Glenwood Canyon closure input

The road model knows fire-blocked roads (the eroded-P10 corridor law) and
nothing else. An administrative I-70 closure — CDOT closes the canyon for
rockfall, crashes and fires — is invisible to the planner, which would keep
routing Eagle crews through it. The travel seam's documented upgrade path
(Valhalla `exclude_polygons`, `wgresponse/travel.py`) is where a live
closure feed (cotrip.org) belongs. Until it exists, the operator holds the
Eagle-to-Roaring-Fork dispatches by hand when the canyon is closed, and the
program is written so that the Roaring Fork never *depends* on them (§4.1).

### 6.5 Detection

Every lead-time figure in §3 assumes detection at ignition. Real feed latency
comes straight off every deadline, and the Lake Christine run into El Jebel
happened after dark. Camera coverage of the two valleys (the utility-deployed
AI camera stations in the region, ALERTWildfire-class or equivalent —
coverage to be verified) plus a Watch Duty enterprise feed are the levers;
the 4-hour experiment measured an hour of earlier warning as worth roughly as
much as perfect physics **[4H]**.

### 6.6 Crown fire is not modeled

The engine is a Rothermel *surface*-fire model. The corridor is timber
country — the Vail tile is 89% burnable and 89% of that is timber understory
/ timber litter (§2.5) — and the runs that destroy mountain communities
(Lake Christine's night run, Grizzly Creek in the canyon) include crown
runs and long-range spotting that no surface ROS reproduces. The model's
measured bias is *favorable* here in one sense — out-of-sample it forecasts
timber classes best (TU 8% / TL 16% late-miss against GS 64% / GR 52%,
`docs/calibration/`) and the φ-vector slope term is armed live — but a crown
run under a dry cold front is a **[ASSUMED]** risk the ensemble band absorbs
only partly. The pessimistic P10, not the median, is what crew safety keys
off; that is the margin, and it is not infinite.

### 6.7 The corridor watcher is not deployed

`data/aoi_vail_aspen.json` and the live watcher's flags are ready
(`python -m wgresponse.cli watch --aoi data/aoi_vail_aspen.json` with the
production flag set from `docs/FIX_PROGRAM_2026-09-11.md`). Running it means
either a second App Runner service (~$25–50 / month) or re-pointing the
SoCal shadow watcher at the corridor — a founder decision, not made here.
Feed coverage is national (WFIGS, FIRMS, NWS gridpoints); the California-only
feeds (FIRIS, CAL FIRE) simply contribute nothing in Colorado. LANDFIRE LFPS
covers the corridor (that is where today's tiles came from).

### 6.8 Smoke, visibility, and the limits of any route verdict

Not modeled anywhere. `docs/transit_hazard_determination.md` says it plainly:
smoke is historically the actual killer on evacuation roads, so a PASSABLE
verdict is thermal only — and these are canyon valleys that trap smoke. No
PASSABLE verdict may ever be presented to a crew as a route-safety guarantee.
Road width, grade and lane count are not modeled; speed is a pure function
of OSM highway class (which is also why the canyon-closed back-road times in
§2.3 are optimistic).

### 6.9 Deploy time and the density law

Every figure in §3 was measured at `deploy_min = 15` (the playbook's
3-person crew, two applicators + a prep-runner) — **[ASSUMED]** capability.
Field-drill it in a water-only EXTREME exercise before the season; if it
measures 18 or 20 on a steep mountain drive, re-run the grid with the
measured number before quoting §3 to anyone. The documented siting law is
≤ 5 homes per committed crew for reactive odds ≥ 57% **[5MAP]**; the 40-home
communities in this grid run at 10–20 homes per local crew, which is the
alerted-regime-or-evacuate posture, and the governing documents should say so.

### 6.10 Standing model limits that ride on every number above

- 90 m raster: a parcel carries ±45 m of positional error — bigger than any
  physics uncertainty. A home on a cell LANDFIRE calls developed receives no
  arrival time except through the 200 m ember-threat radius the simulator
  ships (the live engine ships 0.0 — the Rancho Santa Fe finding applies).
- Blow-ups are not forecastable (Porcupine Ridge 13× under-forecast); the
  answer is posture, and on this fuel the extreme-spread-regime posture does
  not arm (§2.5) — the honest band is the whole margin.
- The forecast band is honest but wide: the 4-hour experiment measured the
  live band costing 16 points at four hours against the old narrow band, and
  perfect information worth +18 **[4H]**. Feeding perimeter and hotspot
  updates into every cycle is the only large lever outside detection.
- The solver budget is deterministic (20,000 consults) since fix program 2,
  so A/Bs here are reproducible run to run; placement noise between
  different seeds is not, and the per-community arms share seeds.

---

## Appendix — artifacts

| Artifact | What |
|---|---|
| `wgresponse/simui.py` `LOCATIONS` | the six corridor presets (centers, hazard winds, 18 planning-grade safe zones) |
| `tools/fetch_sim_tiles.py` | tile registry (kept in sync; `tests/test_corridor_vail_aspen.py`) |
| `data/tiles/<key>.wgt`, `<key>_ext.wgt`, `provenance*.json` | the LANDFIRE tiles fetched today |
| `data/roads/<key>.wgr`, `data/roads/corridor_vail_aspen.wgr` | per-tile OSM road graphs and the corridor major-road graph |
| `scratch_review/corridor_roads.py` → `corridor_drive_times.json` | the drive-time matrix, canyon-closed and all-season variants |
| `scratch_review/corridor_grid.py` → `scratch_review/corridor/*.json` | the grid: every arm × community, per-home rows |
| `docs/calibration/vail_aspen_grid_2026-09-23.md` | the pooled and per-community tables |
| `data/aoi_vail_aspen.json` | the corridor AOI + safe zones for the shadow watcher |
| `console/simulator.html` | the six presets in the public simulator's location list |
