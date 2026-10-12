# Seven one-click fires moved off timber (2026-10-12)

Founder, 2026-10-12: *"go with option 1 and fix the slow first step but then continue to
option 2 and 3."* Option 1 = calibrated embers plus the timber speed rule restored in the
simulator until a crown-fire model exists. Option 2 = move the seven one-click fires that
started in timber (it was offered alongside the grass-only speed rule). Option 3 = the
crown-fire model. This note covers option 2 and how it behaves under both speed rules.

## Summary for the founder

- **What was wrong.** Seven of the thirteen demo fires started in timber: Park City, Vail,
  Beaver Creek, Glenwood Springs, Basalt / Carbondale, Aspen and the Eagle → Vail
  corridor. That was not a choice. The old rule lit the first burnable map cell on the
  upwind line, and in these valleys that cell was timber.
- **What changed.** These seven fires now start on the nearest patch of grass or brush to
  their old starting point. That is 300 m to 1,050 m away, within 5° of the same upwind
  bearing, in the same wind. Most wildland-urban fires start in grass or brush
  (roadsides, power lines, equipment), not under timber.
- **What did not change.** The other six (Boulder, Eagle / Gypsum, Malibu, Paradise,
  Rancho Santa Fe, Santa Rosa) already started in grass or brush. They keep their exact
  cells.
- **Effect today (timber speed rule on, option 1).** Six-hour burned areas move little
  (−30 % to +36 %). Park City's first hour is faster (48 → 192 acres) because a
  grass-shrub start gets going sooner than a timber start.
- **Effect under the grass-only rule** (where the simulator returns once the crown-fire
  model replaces the timber rule). The old timber starts barely burned: 2 to 346 acres
  at 6 h. The new starts burn 248 to 2,912 acres. The timber valleys (Vail, Aspen, the
  corridor) still reach only about 250 acres in 6 h on grass-only, because their fires
  soon run into timber. That gap is what the crown-fire model is for.
- **Recommendation.** Keep the new starts under either speed rule. Keep the timber rule
  until the crown-fire model is validated.

## The rule

`SimSession.fine_fuel_start(bearing, distance)` (`wgresponse/simui.py`), used by
`POST /api/sim/new {scenario}` for the scenarios marked `"ign_fine_fuel": True` in
`server.SCENARIOS`:

1. **Envelope.** The same as the old search: the scenario's bearing ±30° in 5° steps ×
   30–100 % of its distance in 5 % steps (195 points).
2. **Order.** Nearest first, by straight-line distance from the old canonical point (the
   preset bearing at the full preset distance). Ties go to the smaller bearing offset,
   then the farther point.
3. **Choice.** The first point on grass, grass-shrub or shrub (FBFM40 GR / GS / SH) where
   at least half the ground within 250 m is also grass, grass-shrub or shrub (a *patch*,
   `FINE_START_PATCH_FRAC` / `FINE_START_PATCH_RADIUS_M`). If there is no patch, the
   nearest single fine-fuel cell. If there is no fine fuel at all, the old
   first-burnable search, unchanged.
4. The wind stays the scenario's canonical hazard wind (blowing from the preset bearing).
   The search reads only the tile, so it is deterministic and costs no spread solve.

A payload `{"ign_fine_fuel": false}` replays the old first-burnable start (the
crew-safety regression tests use it to replay the overruns they were built on).

## Where each fire now starts (real tiles)

| Scenario | Old start | New start | Moved | Bearing offset × distance | Fine fuel within 250 m |
|---|---|---|---|---|---|
| park_city_windstorm | TU5 timber understory | GS1 grass-shrub | 1,050 m | 0° × 85 % | 55 % |
| vail_westerly | TL5 timber litter | SH4 shrub | 523 m | −5° × 100 % | 55 % |
| beaver_creek_westerly | TU1 timber understory | SH4 shrub | 1,021 m | +5° × 85 % | 76 % |
| glenwood_springs_westerly | TL8 timber litter | SH4 shrub | 900 m | 0° × 85 % | 76 % |
| basalt_carbondale_southwesterly | TU1 timber understory | GS3 grass-shrub | 300 m | 0° × 95 % | 79 % |
| aspen_southwesterly | TL1 timber litter | SH4 shrub | 523 m | −5° × 100 % | 31 % (no patch in the envelope) |
| eagle_vail_corridor_westerly | TU2 timber understory | SH4 shrub | 400 m | 0° × 95 % | 100 % |

Aspen's upwind sector is 70 % timber understory and 15 % timber litter. The nearest patch
that is at least half fine fuel lies 40° off the wind line at half the distance, outside
the envelope. Aspen therefore takes the nearest shrub cell instead.

## Burned acres, first 6 hours (real tiles, the page's session)

Built through `SimSessionManager.new_session` exactly as the page builds it: city weather,
calibrated embers, 15-minute steps. Each fire was run from its old start and its new start,
under the timber speed rule (option 1, the simulator default) and the grass-only rule. The
last column is the first demo home reached by the fire (nominal truth); "–" means none
within 6 h. Script: `scratch_review/timber_start_resite_probe.py`; raw rows:
`scratch_review/timber_start_resite_probe.jsonl`.

| Scenario | Start | Speed rule | 1 h | 3 h | 6 h | First home |
|---|---|---|---|---|---|---|
| park_city_windstorm | old (TU5) | timber | 48 | 1,984 | 6,593 | – |
| | new (GS1) | timber | 192 | 2,904 | 6,945 | – |
| | old | grass-only | 4 | 10 | 50 | – |
| | new | grass-only | 36 | 416 | 2,912 | – |
| vail_westerly | old (TL5) | timber | 38 | 320 | 863 | – |
| | new (SH4) | timber | 86 | 546 | 1,171 | – |
| | old | grass-only | 2 | 8 | 34 | – |
| | new | grass-only | 22 | 88 | 252 | – |
| beaver_creek_westerly | old (TU1) | timber | 90 | 1,053 | 3,687 | 5.25 h |
| | new (SH4) | timber | 42 | 618 | 2,566 | 5.75 h |
| | old | grass-only | 2 | 4 | 14 | – |
| | new | grass-only | 18 | 118 | 526 | – |
| glenwood_springs_westerly | old (TL8) | timber | 68 | 953 | 2,504 | – |
| | new (SH4) | timber | 58 | 905 | 2,032 | – |
| | old | grass-only | 6 | 10 | 198 | – |
| | new | grass-only | 48 | 254 | 1,041 | – |
| basalt_carbondale_southwesterly | old (TU1) | timber | 126 | 955 | 3,927 | – |
| | new (GS3) | timber | 170 | 1,021 | 3,955 | – |
| | old | grass-only | 2 | 4 | 346 | – |
| | new | grass-only | 160 | 420 | 1,371 | – |
| aspen_southwesterly | old (TL1) | timber | 70 | 937 | 4,099 | 6.00 h |
| | new (SH4) | timber | 74 | 1,061 | 4,173 | 5.75 h |
| | old | grass-only | 2 | 2 | 2 | – |
| | new | grass-only | 30 | 84 | 248 | – |
| eagle_vail_corridor_westerly | old (TU2) | timber | 59 | 593 | 2,550 | – |
| | new (SH4) | timber | 59 | 573 | 2,698 | – |
| | old | grass-only | 10 | 109 | 247 | – |
| | new | grass-only | 49 | 109 | 277 | – |

The corridor changes least because its old start (on a 200 m tile) already sat inside
a shrub patch: 80 % of the ground within 250 m was fine fuel.

## Tests

- `tests/test_scenario_fine_fuel_start.py`: the seven are exactly the marked scenarios.
  On a painted synthetic tile, a grass patch beats a nearer lone grass cell, a lone cell
  is taken when there is no patch, and an envelope without fine fuel returns None (the
  server then falls back). A bad `ign_fine_fuel` is a 400. On the real tiles, every one
  of the thirteen fires starts on grass, grass-shrub or shrub; the seven moved less than
  1.5 km from a timber old start, in their canonical wind; the six kept their exact cell.
  The payload replays the old start.
- `tests/test_crew_egress_judged.py`: the replayed overruns pin the old start (and the
  legacy ember draw) they were reproduced under.

## Limits

- Fuel comes from LANDFIRE FBFM40 at 90 m (200 m on the corridor tile). SH4 (low-load
  timber-shrub) counts as shrub. A real patch may be smaller or larger than the 90 m cells
  show.
- The search stays inside the old envelope, so a scenario cannot drift toward a different
  approach to its town; Aspen's start is shrub, but most of its surroundings are timber.
- Under city weather, most of these fires reach no demo home within 6 h under either speed
  rule. They start 5.1–8 km from the town centre and the wind event eases. That is the city-weather finding
  of 2026-10-09 (`docs/review/city_weather_2026-10-09.md`), not something this change
  introduced.
