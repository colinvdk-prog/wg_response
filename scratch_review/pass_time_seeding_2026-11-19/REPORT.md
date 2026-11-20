# Pass-time footprint seeding, measured on its own (2026-11-19)

Founder's build order (2026-11-01), step 1: seed each satellite detection over its pixel at the pass time, and "measure it on its own first, because it changes forecasts". This is that measurement. The knob is `hotspot_pass_time_seeding` (SAFETY SEMANTICS 65), default off.

## Method

- `measure.py` (this folder). Real LANDFIRE terrain: the bundled `paradise_ca`, `malibu_ca`, `boulder_co` and `park_city_ut` tiles (extended versions).
- 6 random burnable spots per site; a 2-detection cluster at each spot with the detections 0, 1, 3, 6 or 12 h old.
- Homes 1, 3 and 8 km downwind (east). Constant west wind 6 m/s, RH 20 %, 30 °C (report C's experiment weather).
- The production physics knobs (gust blend, grass floor, model error, any-angle, extreme regime), 6 members, 72 h.
- Each case: one real `ThreatMonitor` + `AlertEngine` cycle with the knob off and on. Nothing else differs. 120 cases, 360 home rows. Raw rows in `results.jsonl`, the table in `summary.json`.

## Results (24 homes per row)

| Detection age / home distance | p50 earlier, median (max) | p10 later | Alert level raised | EXTREME or more, off / on | Arrival already passed, off / on |
|---|---|---|---|---|---|
| 0 h / 1 km | 0.13 h (5.9) | 0 | 0 | 19 / 19 | 0 / 0 |
| 0 h / 3 km | 0.12 h (1.8) | 0 | 0 | 22 / 22 | 0 / 0 |
| 0 h / 8 km | 0.03 h (2.5) | 0 | 0 | 20 / 20 | 0 / 0 |
| 1 h / 1 km | 1.1 h (6.9) | 0 | 0 | 19 / 19 | 0 / **12** |
| 1 h / 3 km | 1.1 h (2.8) | 0 | 0 | 22 / 22 | 0 / 0 |
| 1 h / 8 km | 1.0 h (3.5) | 0 | 0 | 20 / 20 | 0 / 0 |
| 3 h / 1 km | 3.1 h (8.9) | 0 | 0 | 19 / 19 | 0 / **16** |
| 3 h / 3 km | 3.1 h (4.8) | 0 | 0 | 22 / 22 | 0 / **9** |
| 3 h / 8 km | 3.0 h (5.5) | 0 | 0 | 20 / 20 | 0 / 0 |
| 6 h / 1 km | 6.1 h (11.9) | 0 | 0 | 19 / 19 | 0 / **18** |
| 6 h / 3 km | 6.1 h (7.8) | 0 | 0 | 22 / 22 | 0 / **17** |
| 6 h / 8 km | 6.0 h (8.5) | 0 | 0 | 20 / 20 | 0 / **3** |
| 12 h / 1 km | 12.1 h (17.9) | 0 | 0 | 19 / 19 | 0 / **19** |
| 12 h / 3 km | 12.1 h (13.8) | 0 | 0 | 22 / 22 | 0 / **21** |
| 12 h / 8 km | 12.0 h (14.5) | 0 | 0 | 20 / 20 | 0 / **14** |

"Arrival already passed" = the median arrival is at or before the cycle time. The ladder then goes EXTREME → IMPACTED at the next cycle ("AT the property NOW — do not approach; crews withdrawing").

## What it shows

1. **It does what it says.** Arrival times move earlier by almost exactly the detection's age (median age + 0.1 h). The extra up to ~6 h (the max column) is the pixel footprint: a detection whose centre cell was slow or non-burnable now spreads from the rest of its pixel.
2. **Never later.** In 360 home rows, no pessimistic (p10) arrival got later.
3. **The first alert level did not change** in this weather. Every home already at EXTREME with the knob off stayed there; none was raised. With a 6 m/s wind the pessimistic arrival was already inside 12 h.
4. **The cost the founder asked to see: IMPACTED from age alone.**
   - **1 h old:** half the homes 1 km away read the fire as already arrived.
   - **6 h old:** about three-quarters of homes at 1–3 km.
   - **12 h old:** most homes out to 8 km.

   None of these are IMPACTED with the knob off. This is honest only if the fire really ran unchecked at the modelled rate since the satellite saw it. For a fire that was slower, being suppressed, or not a fire at all, it is a false IMPACTED, and IMPACTED is terminal.
5. **Crew deadlines.** The p10 moves earlier by the same amount, so latest-depart times (SAFETY SEMANTICS 2) get earlier by about the detection's age. This is the safety-side gain report C asked for: today crew deadlines on a satellite-seeded fire are optimistic by the age of the last look.

## Limits

- Constant wind and no suppression: a worst-case reading, as the engine's forecasts are meant to be (pessimistic on the worst-1 % day).
- No real-fire comparison here. Report C §7's season replay (archived VIIRS detections against later perimeters) is the study that says how often the honest seed is right. It needs the FIRMS standard-processing archive and is not done.
- One cycle each. IMPACTED itself appears a cycle later; "arrival already passed" is the measure used.
