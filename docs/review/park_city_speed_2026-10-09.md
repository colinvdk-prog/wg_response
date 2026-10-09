# Park City one-click scenario: is the fire speed right? (2026-10-09)

Founder, 2026-10-09: *"I just ran a simulation in park city and the fire seemed
to spread far too quickly. confirm this was correct."*

**Answer: no.** The simulator computes what it is configured to compute, but
the simulated Park City fire spreads several times faster than a real fire
there would. Two simulator rules cause most of it; the "extreme" weather preset
adds to it.

Evidence: `scratch_review/park_city_speed_probe.py` replays
`server.SCENARIOS["park_city_windstorm"]` on the committed Park City tile
(90 m cells) with the page's default physics, the server's seed and ignition
search, and measures the simulated actual fire (the nominal member the page
shades) every 15 minutes for 6 hours. Results:
`scratch_review/park_city_speed_probe.json`.

## What the probe measured

| Variant | Acres at 1 h 45 | Acres at 6 h | First demo home reached |
|---|---|---|---|
| As deployed (what the founder saw) | 3,054 | 13,030 | 1.5 h |
| Minimum-speed rule on grass and grass-shrub only (`ros_floor_grass_only`) | 36 | 7,124 | 3.25 h |
| Minimum-speed rule off ("Realistic grass speed" unticked) | 18 | 6,005 | 3.5 h |
| Ember spotting off ("Ember spotting" unticked) | 302 | 3,677 | none within 6 h |
| "High" weather instead of "extreme" | 2,250 | 13,504 | 2.0 h |

The "first home" column reads the burned cell under each home; homes on
built-up cells are reached through the ember-radius rule, which this column
does not count.

## Why it is so fast

1. **The minimum-speed rule also speeds up timber.** The page's "Realistic grass
   speed" sets a head spread floor of 10 % of the wind speed for grass, and
   reduced shares for grass-shrub, timber understory and timber litter. This
   fire starts in timber understory and litter, where the physics model alone
   spreads it slowly. With the floor on timber the fire covers 3,054 acres by
   1 h 45; with the floor on grass only, 36 acres. The earlier fire-speed review
   measured the floor lifting timber 19 to 45 times above the physics model with
   no evidence base (`contracts.ros_floor_grass_only`), and the real-fire
   hindcast found that limiting it to grass alone cuts the engine's area
   over-prediction from 3.5 to 2.3 times, while the pessimistic P10's coverage
   of newly burned ground falls from 99.8 % to 97.1 %
   (`docs/review/belief_vs_physics_2026-09-27.md`).
2. **Ember spotting throws spot fires far ahead of a young fire.** Fifteen
   minutes after ignition, with 34 acres burned, a spot fire is already 3.8 km
   downwind; at 30 minutes, 8.3 km; by 1 h 30 the burn reaches the far edge of
   the map, 19.7 km away. With spotting off, the continuous front advances
   about 1.5 to 2.6 km per hour. Long-range spotting of several kilometres is
   real in extreme crown fires, but not from a fire of a few dozen acres in its
   first quarter hour.
3. **The weather preset is generic and relentless.** "Extreme" is a 40 mph
   sustained wind gusting to 52 mph, 6 % humidity and 102 °F, at every location,
   held day and night for the whole run with no recovery. That is hotter, drier
   and longer-lasting than Park City's realistic worst case. It is not the main
   driver here: the "high" preset (28 mph, 12 %, 95 °F) gives a similar 6-hour
   area, because the two rules above dominate.

## How it compares with real fires

- **Marshall Fire, Boulder County, December 30, 2021:** 6,026 acres in total,
  driven by sustained winds above 45 mph for about 8 hours and gusts above
  100 mph through cured grass; most structures were lost over about 6 hours.
  The simulated Park City fire burns 13,030 acres in 6 hours under a 40 mph
  wind, more than twice Marshall's total under much weaker wind.
- **Parleys Canyon Fire, Summit Park, August 14, 2021:** the closest real
  analog. It was first estimated at about 3,000 acres, then mapped at 539 to 619
  acres after its first full day, with heavy suppression and no homes lost.

## Options (founder decision; nothing changed yet)

1. **Limit the minimum-speed rule to grass in the simulator**
   (`ros_floor_grass_only`, an existing knob). Measured on real fires; the
   biggest single realism gain. The live engine keeps its separate A/B.
2. **Make spotting scale with the fire.** Ember distance and launch rate should
   grow with fire size and intensity, so a young fire cannot spot kilometres
   ahead. Needs its own calibration against documented spotting distances.
3. **Location-aware weather.** Per-community worst-case conditions with a daily
   cycle and a finite wind event, instead of one preset held for days.
4. **Meanwhile:** the page's "Realistic grass speed" and "Ember spotting"
   toggles let a viewer compare; unticking spotting is the closest to a real
   continuous front today.

Sources: [Marshall Fire (Wikipedia)](https://en.wikipedia.org/wiki/Marshall_Fire);
[NWS Boulder, Dec 30 2021 high winds](https://www.weather.gov/bou/HighWinds12_30_2021);
[Parleys Canyon Fire forces thousands to evacuate (Salt Lake Tribune)](https://www.sltrib.com/news/2021/08/14/parleys-canyon-fire-shuts/);
[Parley's Canyon Fire mapped to 619 acres (TownLift)](https://townlift.com/2021/08/parleys-canyon-fire-down-to-619-acres/);
[Parleys Canyon Fire 10 percent contained at 539 acres (Fox 13)](https://www.fox13now.com/news/local-news/wildfire-burning-in-parleys-canyon).
