# Ember spotting calibration: how far embers fly, and how that depends on fire size (2026-10-09)

Founder, 2026-10-09: *"begin the calibration of the ember distance and size of fire calibration."*

Follows `docs/review/park_city_speed_2026-10-09.md`. Measured with
`scratch_review/ember_spotting_probe.py` (results:
`scratch_review/ember_spotting_probe.json`, all-scenario catalogue
`scratch_review/ember_spotting_catalog.json`). The prototype is contracts
SAFETY SEMANTICS 19 (`cfg.spot_fire_size`, default OFF, not armed in the
simulator). Every measurement below uses `ros_floor_grass_only=True` (the
simulator's new floor default) unless it says "as deployed" or "timber floor".

## For the founder

**What is wrong today.** Every burning 90 m patch of ground throws one ember.
Its flight distance is drawn from a statistical spread that suits a large,
steady fire, and that spread has a long tail. Even a weak litter fire under
the 40 mph "extreme" wind throws 1 ember in 100 beyond about 0.87 km, more
than twice the farthest such a fire can loft a burning brand (about 0.37 km).
Worse, a patch lit by an ember throws its own ember the moment it starts
burning, so the fire can hop from ember to ember at the speed of the wind.
Measured on the one-click scenarios:

* Boulder: 48 seconds after ignition, with 6 acres burning, the fire threw an
  ember that lit a spot fire 1.6 km away less than two minutes later.
* Paradise: 12 seconds after ignition, with 4 acres burning, it threw one that
  lit a spot fire 1.0 km away a minute later.
* Park City: once the fire started throwing embers, 18 burning patches covered
  3.2 km in about 7 minutes. 12 of the 18 were lit by embers, through a chain
  of 6 ember hops.
* Across the three scenarios, 148 spot fires were lit by embers from a single
  patch burning on its own (128 of those patches were themselves earlier spot
  fires), some up to 1.5 km away.

**What the evidence says.**

* Most spot fires start close to the fire. Across 4,219 spot fires in 251
  Australian wildfires, the median was 0.1 km and the mean 0.4 km. In a
  southern European survey (245 extinguished fires and 48 monitored
  wildfires), the mean was 0.23 km and only 8 % of fires spotted beyond
  0.5 km.
* Long-range spotting needs a large, intense fire. The size of the actively
  burning fire is the strongest single predictor of how far it spots. In the
  worst Australian forests it is about 4 km at about 1,000 ha and about 8 km
  at about 5,000 ha; in sparse vegetation it is 0.4-0.9 km. North American
  crown fires commonly spot up to about 2 km; about 5 km is documented, and
  9-19 km only from extreme fires with deep smoke plumes such as Dixie (2021)
  and Sundance (1967). The Camp Fire's spotting, mostly 1-5 km, was driven by
  its deep plume.
* Physics caps what a surface fire can loft. The standard US model (Albini
  1983, used in BehavePlus) gives a maximum spotting distance at 35 mph of
  about 0.4 km for a weak litter fire, 1.3 km for a grass fire and 2.8 km for
  the most intense chaparral.
* Spot fires take time. It is up to 5-10 minutes from an ember landing to a
  spreading fire, and most spot fires ahead of a crown fire are overrun and
  absorbed before they spread on their own.
* For scale: wildfires in forest and shrub under strong wind advance at about
  10 % of the wind speed, including their spotting. That is about 6.4 km/h at
  40 mph.

**What the prototype does.** One switch (`spot_fire_size`) adds three rules:

1. **An ember cannot outfly its fire.** Each ember (the same random draw as
   today) may fly at most Albini's maximum for the intensity and wind of the
   patch that threw it, scaled by the size of the fire that patch belongs to.
   At the recommended settings a fire of one patch reaches 8 % of Albini's
   distance, 10 ha 23 %, 100 ha 62 %, 300 ha 100 % and 1,000 ha 168 %. An
   ember drawn farther burns out in the air.
2. **A spot fire is a new, small fire.** Until it grows or touches another
   fire, its own embers are short.
3. **A landed ember needs 5 minutes** to become a spreading fire, and the
   main fire can overrun it first.

**What it changes in the demos** (6 h simulator runs with the crew-safety
audit, which stayed at 0 violations and 0 crew overruns in every one of the
53 runs; the Malibu row comes from the fire-only catalogue, §5.4):

| Scenario | Today: first spot beyond 1 km | Prototype: first spot beyond 1 km | Acres at 6 h, today → prototype | First home reached, today → prototype |
|---|---|---|---|---|
| Boulder (grass) | 1 min, 6-acre fire | 160 min, 540-acre fire | 3,108 → 2,012 | 4.8 h → 9.1 h |
| Paradise (shrub / timber) | 20 min, 60-acre fire | 98 min, 252-acre fire | 1,839 → 1,571 | 0.4 h → 2.6 h |
| Malibu (chaparral) | 3 min, 22-acre fire | 21 min, 122-acre fire | 7,444 → 6,645 | — |
| Park City (timber start), grass-only floor | 108 min, 56-acre fire | none in 6 h | 7,124 → 50 | 2.0 h → 9.4 h |
| Park City, timber floor kept | 10 min, 10-acre fire (as deployed) | 86 min, 228-acre fire | 13,030 → 11,029 | 0.4 h → 2.4 h |

Large fires still spot 1.4-3 km, as real ones do. What disappears are
kilometre jumps from fires of a few acres and ember chains running at wind
speed.

**What it still cannot do.**

* The engine has no crown-fire or torching model. A single torching tree can
  throw an ember 0.5-1.4 km from a young timber fire; the prototype cannot.
  Plume-driven spotting of 5-16 km (Camp, Dixie) is also out of reach.
* With the floor on grass only, the engine's timber fires creep. In 7 of the
  13 one-click scenarios (Park City, Vail, Beaver Creek, Glenwood Springs,
  Basalt/Carbondale, Aspen and Eagle-Vail, all ignited in timber) the fire was
  being carried largely by the faulty spotting. With the prototype they burn
  6-395 acres in 6 hours, about what they burn with spotting off (6-366
  acres). Eagle/Gypsum, a grass start hemmed in by developed ground, also
  falls from 683 to 172 acres. That is a spread-model gap, not a spotting one.
* Every ember that lands on burnable fuel still ignites it, and fire size
  means all the area burned so far, not just the part still flaming.

**Recommendation.** Arm `spot_fire_size` in the simulator with its defaults
(300 ha, exponent 0.43, 5 minutes). It removes a measured artefact, is backed
by every source above, and leaves crew safety unchanged. Decide the timber
floor at the same time, because the two interact:

* **Keep the timber floor in the simulator until a crown-fire model exists.**
  With the prototype, Park City's grown fire then advances at 7.1 km/h, close
  to the 10 %-of-wind benchmark (6.4 km/h), after a realistic first hour (64
  acres instead of 801). The six Colorado timber-start towns burn 586-2,817
  acres in 6 hours.
* **Or keep the floor on grass only and re-site the 7 timber-start
  ignitions** into fuel that carries fire (oak and brush, grass). Otherwise
  those demos will show fires that barely move.

The right long-term fix is a crown-fire and torching model. It is the missing
physics behind both fast timber runs and real timber spotting. The live
engine runs with spotting off, so nothing changes there.

## 1. Calibration targets (literature and fire records)

### 1.1 How far: the longest ember is not where spot fires usually start

| What | Typical (where spot fires are likely to establish) | Frequent maximum | Extreme (documented) | Source |
|---|---|---|---|---|
| All spot fires, SE Australia (4,219 spot fires, 251 wildfires, line scans 2002-2018) | median 0.1 km, mean 0.4 km from the source fire | per-observation maximum: mean 0.9 km, 95th percentile 3.9 km | 13.9 km (only 11 source fires had a maximum > 5 km) | Storey et al. 2020a |
| Long-distance (> 500 m) spot fires per observation, SE Australia | mean 2.3 | 95th percentile 10.2 | 55 | Storey et al. 2020b |
| Southern Europe: 245 extinguished fires + 48 monitored wildfires (SALTUS) | mean 228 m; 56 % of fires spot > 10 m, 32 % > 100 m | 8 % of fires spot > 500 m | 2.4 km | Colin et al. 2002, via Koo et al. 2010 |
| 10 large fires, Oregon and Washington (33 days of observation) | spot fires 0.4 km ahead "common" | | 3.2 km ahead "in a few cases" | USDA 1952, via Alexander & Cruz 2014 |
| Wind-driven crown fires, North American conifer | | up to ~2 km "commonly observed" | ~5 km documented; 6-10 km in the 1910 and 1934 Northern Rockies seasons; 16-19 km, Sundance Fire 1967 | Alexander & Cruz 2014 |
| Northern Rockies 2017 (IR hot spots, per fire-day maximum) | | | 2.7 km | Page et al. 2019 |
| Oakland Hills 1991 | | spot fires > 1 km from the main front by noon | | Koo et al. 2010 |
| Camp Fire 2018 (plume-coupled long-range spotting) | most spot ignitions 1-5 km ahead | "numerous" > 5 km | up to 6.3 km into Paradise (NIST); 8-10 km from plume cores (radar); 11 km travelled and/or spotted in < 1.5 h | Lareau 2026; NIST |
| Dixie Fire 2021 (plume tops 10-12 km, ~15 m/s cross wind) | | | 9, 12 and 16 km; ~18 min ember transit | Lareau 2025 |
| Australian eucalypt (contrast only: fibrous bark, deep plumes) | | | 33 km, Kilmore East 2009; "anecdotal 30 km" | Cruz et al. 2012; Koo et al. 2010 |

Models give the LONGEST distance a burning brand can reach, not where spot
fires start ("the maximum spot fire distance was found at the burnout limit",
Koo et al. 2010; whether a landed brand starts a fire "must still be assessed
based on its ignition probability", Alexander & Cruz 2014):

| Maximum potential spotting distance (model, flat ground) | Values | Source |
|---|---|---|
| Wind-driven surface fire (Albini 1983; the BehavePlus equations, reproduced in `spread._albini_surface_max_spot_m`) | at a 35 mph 20-ft wind: 0.37 km at 100 kW/m, 0.76 km at 1 MW/m, 1.6 km at 10 MW/m, 2.8 km at 60 MW/m; at 24 mph: 0.29 / 0.60 / 1.25 / 2.23 km (table §3) | Albini 1983; Morris 1987; firelab/behave |
| Grassland, BehavePlus | 0.8 km at a 32 km/h wind, 1.4 km at 129 km/h | Donovan et al. 2023 |
| Torching trees (Albini 1979) | intermediate range, "perhaps 1.5 to 3.0 km" | Alexander & Cruz 2014 |
| Active crown fire (Albini et al. 2012) | ~4-5.5 km at 30-40 mph 20-ft wind | Alexander & Cruz 2014, fig. 3 |

### 1.2 What scales spotting with the size of the fire

* **Area.** Source-fire area was the most important predictor of maximum
  spotting distance and of the number of spot fires > 500 m (338 line-scan
  observations; gamma GLM on log area). Under strong winds in dense forest on
  steep slopes, the predicted maximum is ~4 km at ~1,000 ha and ~8 km at
  ~5,000 ha (distance ∝ area^0.43); in sparse vegetation 0.4 km and 0.9 km
  (∝ area^0.50). Mechanism given: a large, intense fire releases more total
  power, builds stronger convective plumes and generates more brands, including
  large ones. Smaller fires do spot far occasionally: "six source fires of
  50-100 ha produced at least three spot fires > 500 m". Storey et al. 2020b.
* **Growth × wind.** Maximum spot distance correlated with the interaction of
  fire growth and wind speed (2017 Northern Rockies). Page et al. 2019.
* **Scale.** "The distance of spotting becomes as great as a few kilometres
  when the scale of a fire gets large." Koo et al. 2010.
* **Plume.** The Camp and Dixie long-range spot fires fell out of the
  downwind side of deep, bent-over convective plumes of large fires. Lareau
  2025, 2026.
* **Intensity.** The onset of significant spotting from surface fires is
  generally put at 1,500-2,000 kW/m; crown fires (≳ 4,000 kW/m) "inevitably
  spawn spot fires". Alexander 2001. ELMFIRE's worked example uses a critical
  spotting intensity of 2,000 kW/m.
* **Age.** Every fire from a point ignition accelerates: about 10 minutes to
  steady spread in grass, 30 minutes or more in forest. Alexander 2001, after
  McAlpine & Wakimoto 1991. A young fire's head is narrower and weaker than
  the steady fire the per-cell model assumes.

### 1.3 Timing

* Ignition delay (brand lands → ignition → spreading spot fire) "can be as
  much as 5.0 to 10 minutes"; with it, spots must land 500-700 m ahead of a
  3 km/h crown fire to speed it up at all ("leap frog"). "In most
  high-intensity wildfires that involve crowning, spot fires originating ahead
  of the advancing flame front are typically overrun and thus incorporated
  into the larger fire perimeter before they are able to develop and spread
  independently." Alexander & Cruz 2014.
* Operational simulators: ELMFIRE uses a 100 s cell ignition delay plus a 30 s
  local ignition time; FARSITE has a "spot delay" input and spots only from
  torching trees.

### 1.4 Whole-fire speed (the outcome spotting feeds)

* Forward spread of wildfires in forests and shrublands ≈ 10 % of the 10-m
  open wind speed, mean relative error < 50 % in dry fuel and high wind (not
  grasslands). About 6.4 km/h at the 40 mph "extreme" preset and 4.5 km/h at
  the 28 mph "high" preset. Cruz & Alexander 2019.
* Camp Fire 2018: more than 11 km to Paradise in less than 1.5 h under winds
  up to 22 m/s (≈ 7-8 km/h). NIST.
* Marshall Fire 2021: 6,026 acres in total, under sustained winds above 45 mph
  for about 8 hours and gusts above 100 mph. Parleys Canyon Fire 2021 (Summit
  Park): 539-619 acres after its first day, suppressed.

### 1.5 The targets used here

| | Target | Basis |
|---|---|---|
| C1 | A fire under 10 ha lands no spot fire beyond ~0.5 km | Storey's area law extrapolated (≈ 0.55 km at 10 ha in the worst eucalypt forest); SALTUS: only 8 % of fires ever spot > 0.5 km; Albini's surface-fire maxima |
| C2 | A fire of 10-100 ha lands no spot fire beyond ~1.5 km | Storey's dense-forest law at 100 ha (an Australian upper bound) |
| C3 | Spot fires of 1-3 km do occur once the fire is a few hundred ha or more; none beyond ~5 km without a plume model | Alexander & Cruz; Page et al.; Storey's P95 3.9 km |
| C4 | No ember chains: an isolated spot fire of one patch throws nothing beyond a cell or two | Alexander & Cruz (overrun before they spread); ignition delay |
| C5 | Typical lead of spot fires ahead of the fire ~0.1-0.4 km | Storey (median 0.1, mean 0.4 km); SALTUS (mean 0.23 km) |
| C6 | Grown forest/shrub fire's fastest 1-h head advance ≈ 10 % of the 10-m wind (6.4 km/h at 40 mph, ±50 %) | Cruz & Alexander 2019; Camp Fire |
| C7 | The simulator's crew-safety audit stays at 0 departure violations and 0 crew-overrun rows | house law |

## 2. Method

* Scenarios: the server's own one-click sessions (`SimSessionManager.new_session`
  — the page's defaults: spotting ON, ROS floor 0.10, plan-on-roads, the 24 h
  coating hold, 15-min coat time) on the committed real tiles (200 × 200 cells
  of 90 m): `park_city_windstorm` (timber start), `boulder_chinook` (grass,
  Marshall-style), `paradise_ne_wind` (shrub / timber, Camp-style), all
  "extreme" (40 mph sustained ×1.3 gusts, 6 % RH, 39 °C, held constant). Plus
  `vail_westerly` ("high", 28 mph) and a nominal-only catalogue of all 13
  scenarios.
* Spotting statistics: the nominal member (the simulator's actual fire)
  re-solved with `spread.compute_arrival(spot_trace=[...])`, which records
  every ember thrown. The re-solve is checked bit-equal to the session's
  member 0 in every run. A **spot ignition** is an ember whose landing time is
  its landing cell's final arrival. Its **lead** is the distance from the
  landing cell to the nearest cell already burned when the ember was thrown.
  **Fire size** at a throw is the whole fire's burned area then; the
  **thrower's own fire** is the 8-connected burned area its cell joined when
  it ignited. That is the rule's fire size; it is replayed independently over
  the final field and matched the engine's count in every armed run (0
  mismatches).
* Demo: 24 ticks (6 h) with the crew-safety audit (departure violations; crew
  overrun rows summed over ticks). "First home reached" = the earliest time
  the actual fire burns a client's own cell or, for a home on a non-burnable
  cell, a burnable cell in the simulator's 200 m ember-radius window. It is
  therefore earlier than the own-cell figure of the Park City speed review.
* Variants: `legacy` (SEMANTICS 11 as today), `spotting_off`, `law_rA_dD[_eE]`
  (SEMANTICS 19 armed: reference size A ha, delay D min, exponent E, default
  0.43), each on the grass-only floor; `as_deployed` and the `dep_*` variants
  keep the floor on all four fuel classes ("timber floor").

## 3. The engine today (measured)

The legacy per-cell draw against the physical maximum at the 40 mph "extreme"
preset (20-ft wind 15.5 m/s):

| Byram intensity | Legacy mean | median | P99 | P99.9 | Albini surface-fire maximum |
|---|---|---|---|---|---|
| 100 kW/m (timber litter) | 136 m | 81 m | 868 m | 1,895 m | 375 m |
| 1 MW/m | 271 m | 196 m | 1,282 m | 2,377 m | 760 m |
| 6 MW/m (grass at this wind) | 464 m | 374 m | 1,723 m | 2,844 m | 1,352 m |
| 60 MW/m (chaparral) | 926 m | 822 m | 2,562 m | 3,722 m | 2,840 m |

Albini's surface-fire maximum by the simulator's wind presets (m, open flat
ground):

| Preset (10-m wind) | 100 kW/m | 300 | 1,000 | 2,000 | 5,000 | 10,000 | 30,000 | 60,000 |
|---|---|---|---|---|---|---|---|---|
| low (8 mph) | 123 | 174 | 256 | 321 | 431 | 538 | 766 | 955 |
| moderate (18 mph) | 214 | 302 | 443 | 554 | 744 | 931 | 1,325 | 1,655 |
| high (28 mph) | 291 | 408 | 597 | 746 | 1,002 | 1,254 | 1,786 | 2,231 |
| extreme (40 mph) | 375 | 521 | 760 | 949 | 1,275 | 1,595 | 2,272 | 2,840 |

The legacy variance law (variance = 250 m × mean, ELMFIRE's worked example)
widens the tail as the mean shrinks. That is why weak fires throw the
relatively longest embers: at 100 kW/m the P99 is 2.3 times Albini's maximum.

Spotting by the nominal fire, first 6 h (legacy; Park City also as deployed):

| Scenario | First spot > 0.5 km | First spot > 1 km | Longest spot from a fire < 10 ha | 10-100 ha | Longest spot (6 h) | Spot ignitions | of which thrown by a one-cell fire | Spot-from-spot chain depth |
|---|---|---|---|---|---|---|---|---|
| Park City (as deployed) | 10 min (4 ha) | 10 min (4 ha) | 1.04 km | 1.80 km | 2.72 km | 2,314 | 106 (up to 1.97 km) | 19 |
| Park City (grass-only floor) | 98 min (5 ha) | 108 min (23 ha) | 0.61 km | 2.52 km | 2.63 km | 1,241 | 88 (up to 1.53 km) | 16 |
| Boulder | 1 min (2 ha) | 1 min (2 ha) | 1.64 km | 1.42 km | 2.09 km | 478 | 38 (up to 1.20 km) | 9 |
| Paradise | 0 min (2 ha) | 20 min (24 ha) | 0.97 km | 1.29 km | 2.17 km | 322 | 22 (up to 1.12 km) | 8 |

(The fire size in parentheses is the whole fire's burned area when the ember
was thrown; times are when it was thrown.) Of the 148 spot ignitions thrown
by a one-cell fire on the grass-only floor, 128 were thrown by an earlier
spot fire, 3 by the ignition cell and 17 by single cells reached across a
knight move. Half of all legacy spot ignitions (51 % Park City, 46 % Boulder,
48 % Paradise) were thrown by cells that were themselves spot-ignited; the
direct spot-from-spot chains reached 8-16 hops (19 as deployed). The typical
lead was one cell (p50 0.09 km), longest 1.25-1.40 km. Park City's 1 h 45
snapshot (grass-only floor): 18 burned cells, 12 spot-ignited (8 of them by
other spot fires), a 6-hop chain, and the head 3.82 km out. The head was
still 0.64 km out 7 minutes earlier, when the first ember of the chain was
thrown.

Legacy against the targets: C1 fails in all three scenarios (0.6-1.6 km from
fires under 10 ha); C2 fails in Park City (2.5 km from a fire under 100 ha);
C3 holds for distance but the long spots come from young fires; C4 fails
(22-88 spot fires thrown by one-cell fires, mostly earlier spot fires, up to
1.5 km); C5
holds; C6 is too fast in Park City (10.6 km/h fastest hour on the grass-only
floor, 11.0 as deployed) and on target in Paradise (6.1 km/h).

## 4. The prototype (contracts SAFETY SEMANTICS 19)

`cfg.spot_fire_size` (bool, default False) with three parameters read only
when it is on. Their defaults are the recommended values:

| Knob | Default | Meaning |
|---|---|---|
| `spot_fire_size` | False | master switch (requires `spotting_enabled`) |
| `spot_fire_size_ref_ha` | 300.0 | fire size (ha) at which an ember's reach equals Albini's surface-fire maximum |
| `spot_fire_size_exp` | 0.43 | reach ∝ fire area ^ this (Storey et al. 2020b) |
| `spot_ignition_delay_min` | 5.0 | minutes from an ember landing to its spot fire spreading |

For each burning cell, in `spread.compute_arrival`:

* **Fire size.** The cell's fire is the 8-connected burned area it joins when
  it ignites. A union-find over cells already burned is maintained in
  Dijkstra's settle order; kept history and perimeter-interior cells join
  first. A spot fire is a new fire until it touches another.
* **Reach.** `spot_reach_m(I, u20, fire_ha, cfg)` = `_albini_surface_max_spot_m(I, u20)`
  × (fire_ha / `spot_fire_size_ref_ha`) ^ `spot_fire_size_exp`. Albini is
  evaluated with BehavePlus's equations, with I = the cell's Byram intensity
  (as the spotting seam already uses it), u20 = the 20-ft wind and open
  downwind cover (no canopy layer on the tiles, so the critical cover height
  governs: the most generous maximum). The legacy per-cell draw (same seed,
  same bearing, flight time = distance / u20) lands only if it is within the
  reach; otherwise it burns out. The reach at the 40 mph preset, by the
  thrower's fire size:

  | Fire size | 1 cell (0.81 ha) | 2 ha | 5 ha | 10 ha | 30 ha | 100 ha | 300 ha | 1,000 ha | 3,000 ha |
  |---|---|---|---|---|---|---|---|---|---|
  | factor (A/300)^0.43 | 0.08 | 0.12 | 0.17 | 0.23 | 0.37 | 0.62 | 1.00 | 1.68 | 2.69 |
  | grass-like cell, 6 MW/m | 106 m | 157 m | 233 m | 313 m | 502 m | 843 m | 1,352 m | 2,269 m | 3,639 m |
  | chaparral-like cell, 60 MW/m | 223 m | 329 m | 488 m | 658 m | 1,055 m | 1,771 m | 2,840 m | 4,766 m | 7,643 m |

  From ~1,000 ha (chaparral-like cells) or ~3,000 ha (grass-like cells) the
  reach exceeds the legacy draws' own tail (P99.9 ≈ 2.8-3.7 km), so for the
  largest fires the draw, not the reach, sets the longest spot.
* **Delay.** A landed ember's spot fire starts `spot_ignition_delay_min` later.

Laws kept (all pinned in `tests/test_spot_fire_size.py`, 26 tests):

* OFF is byte-identical. The fingerprints of 7 worlds where the rule would
  bite (high-wind corridor, re-anchor with embers aloft, mixed-fuel tile,
  stale perimeter, 4-member ensemble, the committed Park City tile under both
  floor class sets) were computed on 05cf1d6 before the rule existed and stay
  bit-equal. The three parameters are inert while the switch is off.
* No new random draw, member-seeded, never wall-clock: a t0 shift shifts every
  arrival exactly.
* ROS memo exact: memo = `WGRESP_NO_ROS_MEMO=1` reference, with rotation and a
  perimeter. Albini's maximum is a function of the memo key and rides in the
  cached spot tuple.
* Label-setting holds; a no-op re-anchor (kept + kept_wx + embers aloft)
  reproduces the continuous run cell for cell with the rule armed.
* Every ember obeys the law (fire size = an independent replay; reach =
  `spot_reach_m`; burns out iff beyond reach; landing time = throw + flight
  + delay).
* On the committed Boulder tile: no spot beyond 0.6 km while the fire is under
  10 ha, nor beyond 1 km under 50 ha, while large fires still spot beyond
  1 km. Legacy: 1.6 km from 2.4 ha.

## 5. Prototype against today

### 5.1 Spotting (nominal fire, first 6 h)

| Scenario | Variant | First spot > 0.5 km | First spot > 1 km | First spot > 2 km | Longest from a fire < 10 ha | 10-100 ha | Longest (6 h) | Lead p50 / max | Spot ignitions | thrown by a one-cell fire |
|---|---|---|---|---|---|---|---|---|---|---|
| Park City | legacy | 98 min (5 ha) | 108 min (23 ha) | 130 min (85 ha) | 0.61 km | 2.52 km | 2.63 km | 0.09 / 1.40 km | 1,241 | 88 (≤ 1.53 km) |
| Park City | **law_r300_d5** | none | none | none | – | – | – | – | 0 | 0 |
| Park City | dep_law_r300_d5 (timber floor) | 69 min (45 ha) | 86 min (92 ha) | 107 min (198 ha) | – | 1.17 km | 3.00 km | 0.09 / 2.04 km | 1,115 | 0 |
| Boulder | legacy | 1 min (2 ha) | 1 min (2 ha) | 235 min (658 ha) | 1.64 km | 1.42 km | 2.09 km | 0.09 / 1.35 km | 478 | 38 (≤ 1.20 km) |
| Boulder | law_r100_d0 | 27 min (6 ha) | 55 min (32 ha) | 254 min (662 ha) | 0.52 km | 1.06 km | 2.09 km | 0.09 / 0.81 km | 397 | 5 (≤ 0.16 km) |
| Boulder | **law_r300_d5** | 28 min (6 ha) | 160 min (219 ha) | none | 0.52 km | 0.61 km | 1.44 km | 0.09 / 0.73 km | 104 | 0 |
| Boulder | law_r1000_d5 | 152 min (138 ha) | 224 min (359 ha) | none | – | 0.37 km | 1.34 km | 0.09 / 0.73 km | 74 | 0 |
| Paradise | legacy | 0 min (2 ha) | 20 min (24 ha) | 261 min (532 ha) | 0.97 km | 1.29 km | 2.17 km | 0.09 / 1.25 km | 322 | 22 (≤ 1.12 km) |
| Paradise | law_r100_d0 | 40 min (20 ha) | 62 min (75 ha) | 127 min (271 ha) | 0.20 km | 1.56 km | 2.17 km | 0.09 / 1.25 km | 276 | 1 (0.20 km) |
| Paradise | **law_r300_d5** | 58 min (30 ha) | 98 min (102 ha) | 168 min (261 ha) | – | 0.84 km | 2.11 km | 0.09 / 1.27 km | 214 | 0 |
| Paradise | law_r1000_d5 | 87 min (61 ha) | 118 min (119 ha) | none | – | 0.96 km | 1.54 km | 0.09 / 0.77 km | 172 | 0 |

The one sub-10-ha spot beyond 0.5 km under the recommended setting (Boulder,
0.52 km at 28 min from a 6 ha fire) came from a 59 MW/m cell, the most
intense fire on that map.

### 5.2 The demo (6 h of ticks)

| Scenario | Variant | Acres 1 h | 3 h | 6 h | Head km 0.5 h | 1 h | 3 h | 6 h | Fastest 1-h head gain | First home reached | Departure violations | Crew-overrun rows | Homes served |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Park City | as_deployed | 801 | 6,779 | 13,030 | 8.27 | 10.06 | 19.61 | 19.68 | 11.0 km/h | 0.40 h | 0 | 0 | 11 |
| Park City | legacy | 6 | 843 | 7,124 | 0.14 | 0.26 | 15.98 | 19.68 | 10.6 km/h | 1.98 h | 0 | 0 | 17 |
| Park City | spotting_off | 4 | 16 | 50 | 0.14 | 0.14 | 0.51 | 0.82 | 0.25 km/h | 16.5 h | 0 | 0 | 14 |
| Park City | **law_r300_d5** | 4 | 16 | 50 | 0.14 | 0.14 | 0.51 | 0.82 | 0.25 km/h | 9.4 h | 0 | 0 | 25 |
| Park City | dep_spotting_off | 62 | 1,443 | 3,677 | 0.64 | 1.41 | 5.55 | 8.18 | 2.7 km/h | 7.4 h | 0 | 0 | 20 |
| Park City | dep_law_r300_d5 | 64 | 2,410 | 11,029 | 0.64 | 1.41 | 9.93 | 19.55 | 7.1 km/h | 2.4 h | 0 | 0 | 26 |
| Boulder | legacy | 456 | 1,267 | 3,108 | 4.94 | 5.57 | 6.84 | 15.47 | 8.4 km/h | 4.84 h | 0 | 0 | 25 |
| Boulder | spotting_off | 26 | 460 | 1,719 | 0.37 | 0.49 | 4.94 | 6.43 | 2.3 km/h | 15.7 h | 0 | 0 | 9 |
| Boulder | **law_r300_d5** | 34 | 725 | 2,012 | 0.37 | 1.00 | 5.03 | 7.37 | 3.9 km/h | 9.1 h | 0 | 0 | 25 |
| Boulder | dep_law_r300_d5 | 86 | 1,025 | 2,270 | 0.37 | 2.69 | 5.04 | 8.36 | 4.6 km/h | 8.5 h | 0 | 0 | 26 |
| Paradise | as_deployed | 566 | 1,407 | 4,848 | 6.36 | 6.36 | 7.77 | 19.67 | 11.3 km/h | 0.22 h | 0 | 0 | 9 |
| Paradise | legacy | 336 | 999 | 1,839 | 5.73 | 6.17 | 7.58 | 8.23 | 6.1 km/h | 0.37 h | 0 | 0 | 18 |
| Paradise | spotting_off | 64 | 362 | 941 | 0.70 | 1.08 | 3.24 | 5.22 | 1.2 km/h | 5.75 h | 0 | 0 | 23 |
| Paradise | **law_r300_d5** | 82 | 733 | 1,571 | 0.70 | 1.72 | 5.54 | 6.00 | 2.9 km/h | 2.62 h | 0 | 0 | 28 |
| Paradise | dep_law_r300_d5 | 186 | 1,175 | 2,282 | 1.46 | 2.48 | 6.36 | 8.30 | 4.1 km/h | 1.52 h | 0 | 0 | 23 |
| Vail ("high") | legacy | 2 | 34 | 184 | — | 0.04 | — | 5.71 | — | 6.5 h | 0 | 0 | — |
| Vail ("high") | spotting_off / **law_r300_d5** | 2 | 8 | 32 | — | 0.04 | — | 1.22 | — | 89 / 73 h | 0 | 0 | — |

Every variant of every scenario kept the crew-safety audit at 0 departure
violations and 0 crew-overrun rows: 53 six-hour runs in
`ember_spotting_probe.json`, including all the parameter variants of §5.3.
Homes served is unchanged or higher under the prototype, because the slower
start leaves the crews more time.

### 5.3 Sensitivity of the recommended setting

| Parameter (others at 300 ha / 0.43 / 5 min) | Boulder acres at 6 h | Boulder first spot > 1 km | Paradise acres at 6 h | Paradise first spot > 1 km | Paradise head at 1 h |
|---|---|---|---|---|---|
| reference 100 ha | 2,252 | 89 min (111 ha) | 1,619 | 71 min (62 ha) | 1.91 km |
| **reference 300 ha** | **2,012** | **160 min (219 ha)** | **1,571** | **98 min (102 ha)** | **1.72 km** |
| reference 500 ha | 1,841 | 164 min (222 ha) | 1,511 | 98 min (86 ha) | 1.46 km |
| reference 1,000 ha | 1,797 | 224 min (359 ha) | 1,437 | 118 min (119 ha) | 1.08 km |
| exponent 0.3 | 2,290 | 90 min (122 ha) | 1,587 | 69 min (63 ha) | 2.29 km |
| exponent 0.6 | 1,847 | 164 min (222 ha) | 1,511 | 98 min (85 ha) | 1.08 km |
| delay 0 min | 2,134 | 143 min (179 ha) | 1,641 | 71 min (66 ha) | 2.23 km |
| delay 10 min | 1,970 | 165 min (220 ha) | 1,505 | 98 min (93 ha) | 1.34 km |

Across the whole plausible range, the 6-h area moves by −11 % / +14 %
(Boulder) and −9 % / +5 % (Paradise). The first-hour behaviour, the first
long spot and the first home reached are the sensitive outputs, as single
early events.

### 5.4 All thirteen one-click scenarios (nominal fire only, acres at 6 h)

| Scenario (preset) | as deployed | legacy (grass-only floor) | spotting off | **law_r300_d5** | timber floor, spotting off | timber floor + law |
|---|---|---|---|---|---|---|
| Park City (extreme) | 13,030 | 7,124 | 50 | 50 | 3,677 | 11,029 |
| Boulder (extreme) | 3,140 | 3,108 | 1,719 | 2,012 | 1,994 | 2,270 |
| Rancho Santa Fe (extreme) | 1,903 | 1,461 | 735 | 1,009 | 753 | 1,017 |
| Malibu (extreme) | 7,632 | 7,444 | 4,153 | 6,645 | 4,558 | 6,873 |
| Paradise (extreme) | 4,848 | 1,839 | 941 | 1,571 | 1,795 | 2,282 |
| Santa Rosa (extreme) | 4,594 | 3,457 | 562 | 885 | 1,721 | 3,839 |
| Vail (high) | 7,065 | 184 | 32 | 32 | 490 | 586 |
| Beaver Creek (high) | 4,898 | 150 | 12 | 12 | 2,382 | 2,776 |
| Eagle / Gypsum (high) | 683 | 683 | 172 | 172 | 172 | 172 |
| Glenwood Springs (high) | 13,168 | 1,841 | 228 | 298 | 1,731 | 1,839 |
| Basalt / Carbondale (high) | 6,815 | 3,789 | 310 | 394 | 2,232 | 2,496 |
| Aspen (high) | 10,274 | 40 | 6 | 6 | 2,442 | 2,814 |
| Eagle-Vail corridor (high) | 3,796 | 593 | 366 | 395 | 1,967 | 2,817 |

## 6. How close each variant gets to the targets

| Target | as deployed | legacy (grass-only) | **law_r300_d5 (grass-only)** | law_r300_d5 + timber floor |
|---|---|---|---|---|
| C1 no spot > 0.5 km from a fire < 10 ha | fails (1.0-1.6 km) | fails (0.6-1.6 km) | at the limit (one 0.52 km spot, from a 59 MW/m cell) | at the limit (same) |
| C2 no spot > 1.5 km from a fire of 10-100 ha | fails (Park City 1.8 km) | fails (Park City 2.5 km) | holds (≤ 0.84 km) | at the limit (≤ 1.56 km, Paradise) |
| C3 1-3 km spots from fires of a few hundred ha | holds, but also from tiny fires | holds, but also from tiny fires | holds in Boulder / Paradise (1.4-2.1 km from 100-220 ha on); none in Park City (no fire to throw them) | holds (Park City 3.0 km max) |
| C4 no isolated-spot-fire chains | fails | fails (22-88, ≤ 1.5 km) | holds (0) | holds (0) |
| C5 typical lead 0.1-0.4 km | holds (0.09) | holds | holds | holds |
| C6 grown forest/shrub fire ≈ 6.4 km/h (±50 %) | too fast (11.0-11.3) | Park City too fast (10.6); Paradise on target (6.1) | too slow (Park City 0.25, Paradise 2.9) | Park City on target (7.1); Paradise within the band (4.1) |
| C7 crew-safety audit at 0 | holds | holds | holds | holds |

The prototype meets the spotting targets (C1-C5) at the recommended setting.
Across the settings tried, a 10-100 ha fire's longest spot ranges from 0.33
to 1.86 km. It stays within C2 for reference sizes of 300 ha and above (at
the limit, 1.56 km, in Paradise with a 10-minute delay or the timber floor)
and passes it at the most permissive settings (reference 100 ha: up to
1.86 km; exponent 0.3: 1.64 km). That is the main reason to prefer 300 ha
over 100 ha. The one sub-10-ha spot beyond 0.5 km (0.52 km in Boulder)
appears at every reference size up to 300 ha. The recommended 300 ha, 0.43,
5 min sits in the middle of the plausible range.
The remaining miss, C6, is a spread-model property: in timber and timber-shrub
the surface model without crown fire is too slow once the faulty spotting no
longer carries it. With the timber floor kept, the developed-fire speed falls
within the benchmark band in both forest scenarios.

## 7. Recommended parameters and their uncertainty

| Knob | Recommended | Plausible range | Evidence for the range | Measured effect across the range |
|---|---|---|---|---|
| `spot_fire_size_ref_ha` | 300 ha | 100-1,000 ha | Storey's area law (eucalypt upper bound: ~1 km at ~40 ha, 4 km at 1,000 ha); North American crown-fire maxima 2-5 km from large fires; the Camp Fire's mostly 1-5 km | 6-h area −11 % / +12 %; first spot > 1 km 1.2-3.7 h after ignition |
| `spot_fire_size_exp` | 0.43 | 0.3-0.6 | Storey: 0.43 dense forest, 0.50 sparse vegetation | 6-h area −8 % / +14 % |
| `spot_ignition_delay_min` | 5 min | 0-10 min | ELMFIRE 100 s + 30 s; Alexander & Cruz up to 5-10 min; FARSITE default 0 | 6-h area −4 % / +6 %; head at 1 h −22 % / +30 % (Paradise) |

The largest uncertainty is not in these three numbers but in what they sit on:
Australian spotting statistics standing in for US fuels, a surface-only spread
model in timber, and fire size counted as total burned area rather than the
actively flaming area Storey measured.

## 8. Limits

* No torching or crown-fire model. This misses torching-tree spots of
  0.5-1.4 km from young timber fires (Albini 1979; BehavePlus), active crown
  fire spread (the 10 % rule) and plume-driven spotting of 5-16 km (Camp 2018,
  Dixie 2021; Lareau 2025, 2026).
* Albini's model assumes flat ground and open downwind cover. Ridge-to-valley
  geometry, which can add distance, is not applied.
* One ember per burning cell, and every ember that lands on burnable fuel
  ignites it. There is no ignition probability (fuel moisture, ember size),
  and no generation per unit of heat release (ELMFIRE's per-MW law).
* Fire size is the total connected burned area. A fire that has gone quiet
  keeps its size, and a spot fire that merges inherits the whole fire's size
  at once.
* The calibration anchors on statistics, not hindcasts of individual US fires'
  spot-fire maps. The next step is a hindcast against mapped spot fires (the
  2017 Northern Rockies IR data of Page et al. 2019; NIST's Camp Fire spot
  ignitions; Storey's line-scan maxima by area).
* The demos run a constant weather preset for 6 h. A relentless 40 mph wind
  inflates every 6-h area relative to real events (the Park City speed
  review's third finding).

## Sources

* Albini, F.A. 1979. *Spot fire distance from burning trees — a predictive model.* USDA FS GTR INT-56.
* Albini, F.A. 1983. *Potential spotting distance from wind-driven surface fires.* USDA FS Res. Pap. INT-309. [FRAMES](https://www.frames.gov/catalog/8155)
* Albini, F.A., Alexander, M.E., Cruz, M.G. 2012. A mathematical model for predicting the maximum potential spotting distance from a crown fire. *IJWF* 21: 609-627. [FRAMES](https://www.frames.gov/catalog/13108)
* Alexander, M.E. 2001. *Fire behaviour as a factor in forest and rural fire suppression.* NZ Fire Service Commission Research Report 21. [PDF](https://www.fireandemergency.nz/assets/Documents/Research-and-reports/Report-21-Fire-Behaviour-as-a-factor-in-Forest-and-Rural-Fire-Supression.PDF)
* Alexander, M.E., Cruz, M.G. 2014. Energy release rates, flame dimensions, and spotting characteristics of crown fires. *Fire Management Today* 73(4): 24-27. [PDF](https://www.frames.gov/documents/catalog/alexander_and_cruz_2014b.pdf)
* BehavePlus surface-fire spotting code: firelab/behave `spot.cpp`. [GitHub](https://github.com/firelab/behave)
* Colin, P.Y. et al. 2002. SALTUS program (spot fires in southern Europe), as summarised in Koo et al. 2010.
* Cruz, M.G., Alexander, M.E. 2019. The 10 % wind speed rule of thumb for estimating a wildfire's forward rate of spread in forests and shrublands. *Annals of Forest Science* 76: 44. [Article](https://annforsci.biomedcentral.com/articles/10.1007/s13595-019-0829-8)
* Cruz, M.G. et al. 2012. Anatomy of a catastrophic wildfire: the Black Saturday Kilmore East fire in Victoria, Australia. *Forest Ecology and Management* 284: 269-285. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0378112712001223)
* Donovan, V.M., Fogarty, D.T., Twidwell, D. 2023. Spot-fire distance increases disproportionately for wildfires compared to prescribed fires as grasslands transition to *Juniperus* woodlands. *PLoS One* 18(4): e0283816. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10089345/)
* ELMFIRE user guide and input reference (spotting: lognormal mean a·I^b·U^c, normalised variance, critical spotting intensity, cell ignition delay). [elmfire.io](https://elmfire.io/user_guide.html), [inputs](https://elmfire.io/input_reference.html)
* FARSITE / FlamMap help: spotting technical documentation and model settings (spot delay, ember spot probability). [Spotting](https://owfflammaphelp62.firenet.gov/LegacyFARSITE/Tech_Spotting.htm), [Settings](https://owfflammaphelp62.firenet.gov/FARSITE/FARSITE_Model_Settings_Tab.htm)
* Koo, E., Pagni, P.J., Weise, D.R., Woycheese, J.P. 2010. Firebrands and spotting ignition in large-scale fires. *IJWF* 19: 818-843. [Treesearch](https://research.fs.usda.gov/treesearch/38384)
* Lareau, N. 2025. Plume dynamics drive extreme long-range spotting during California's Dixie Fire. *JGR Atmospheres*. [doi:10.1029/2024JD043167](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2024JD043167)
* Lareau, N. 2026. Plume-coupled long-range spotting drove the explosive spread of the 2018 Camp Fire. *JGR Atmospheres*. [doi:10.1029/2025JD045798](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2025JD045798)
* McAlpine, R.S., Wakimoto, R.H. 1991. The acceleration of fire from point source to equilibrium spread. *Forest Science* 37: 1314-1337.
* Morris, G.A. 1987. *A simple method for computing spotting distances from wind-driven surface fires.* USDA FS Res. Note INT-374. [PDF](https://www.frames.gov/documents/behaveplus/publications/Morris_1987_INT-RN-374.pdf)
* NIST. A case study of the Camp Fire: fire progression timeline (TN 2135) and TN 2252. [TN 2252](https://nvlpubs.nist.gov/nistpubs/TechnicalNotes/NIST.TN.2252.pdf), [IAFC presentation](https://www.nist.gov/system/files/documents/2021/05/11/Camp%20Fire%20Progression%20Timeline%20IAFC%2020210429%20pdf.pdf)
* Page, W.G., Wagenbrenner, N.S., Butler, B.W., Blunck, D.L. 2019. An analysis of spotting distances during the 2017 fire season in the Northern Rockies, USA. *Can. J. For. Res.* 49: 317-325. [Treesearch](https://research.fs.usda.gov/treesearch/57899)
* Sardoy, N., Consalvi, J.L., Kaiss, A., Fernandez-Pello, A.C., Porterie, B. 2008. Numerical study of ground-level distribution of firebrands generated by line fires. *Combustion and Flame* 154: 478-488. [doi:10.1016/j.combustflame.2008.05.006](https://doi.org/10.1016/j.combustflame.2008.05.006)
* Storey, M.A., Price, O.F., Bradstock, R.A., Sharples, J.J. 2020a. Analysis of variation in distance, number, and distribution of spotting in southeast Australian wildfires. *Fire* 3(2): 10. [doi:10.3390/fire3020010](https://doi.org/10.3390/fire3020010)
* Storey, M.A., Price, O.F., Sharples, J.J., Bradstock, R.A. 2020b. Drivers of long-distance spotting during wildfires in south-eastern Australia. *IJWF* 29: 459-472. [doi:10.1071/WF19124](https://doi.org/10.1071/WF19124)
* Marshall Fire and Parleys Canyon Fire figures: as cited in `docs/review/park_city_speed_2026-10-09.md`.
