# City weather for the simulator (2026-10-09)

Founder, 2026-10-09: *"change the weather conditions for each city specific."*

## For the founder

**What changed.** Until today one table served all thirteen communities:
"extreme" was a 40 mph wind gusting to 52 mph, 6 % humidity and 39 °C (102 °F)
in Malibu, Park City and Aspen alike, held day and night for as long as the run
lasted. Now each community's low, moderate, high and extreme mean that
community's own weather, measured at its nearest airport weather station over
twenty fire seasons and moved to the elevation of its homes. The weather also
has a day now: afternoons are the windiest, driest and hottest hours, and nights
are calmer, cooler and more humid. High and extreme are a wind event that starts
with the fire, holds for the number of hours such days really last there, then
eases back to an ordinary dry day.

**What high and extreme mean now** (afternoon wind, gust, humidity and
temperature; the event's hold and easing in hours):

| Community | High | Extreme | Wind event (hold + easing) |
|---|---|---|---|
| Malibu | 23 mph gusting 33, 10 % RH, 23 °C (73 °F) | 29 mph gusting 42, 9 % RH, 19 °C (66 °F) | high 8 h + 2 h; extreme 10 h + 4 h |
| Paradise | 19 mph gusting 27, 19 % RH, 25 °C (78 °F) | 24 mph gusting 33, 17 % RH, 24 °C (74 °F) | high 6 h + 2 h; extreme 7 h + 1 h |
| Santa Rosa | 13 mph gusting 22, 28 % RH, 27 °C (80 °F) | 26 mph gusting 61, 7 % RH, 33 °C (91 °F), the Tubbs Fire night | high 5 h + 1 h; extreme 4 h + 12 h |
| Rancho Santa Fe | 9 mph gusting 13, 25 % RH, 22 °C (72 °F) | 14 mph gusting 22, 8 % RH, 25 °C (76 °F) | high 4 h + 1 h; extreme 6 h + 1 h |
| Boulder | 21 mph gusting 31, 20 % RH, 14 °C (57 °F) | 32 mph gusting 48, 22 % RH, 12 °C (54 °F) | high 5 h + 1 h; extreme 5 h + 1 h |
| Park City | 18 mph gusting 26, 16 % RH, 25 °C (77 °F) | 21 mph gusting 31, 12 % RH, 25 °C (78 °F) | high 5 h + 1 h; extreme 5 h + 1 h |
| Vail | 22 mph gusting 31, 15 % RH, 24 °C (75 °F) | 26 mph gusting 37, 12 % RH, 24 °C (74 °F) | high 5 h + 1 h; extreme 7 h + 1 h |
| Beaver Creek | 22 mph gusting 31, 14 % RH, 25 °C (77 °F) | 26 mph gusting 37, 12 % RH, 25 °C (77 °F) | high 5 h + 1 h; extreme 7 h + 1 h |
| Eagle | 22 mph gusting 31, 14 % RH, 27 °C (81 °F) | 26 mph gusting 37, 11 % RH, 27 °C (81 °F) | high 5 h + 1 h; extreme 7 h + 1 h |
| Glenwood Springs | 20 mph gusting 31, 11 % RH, 30 °C (85 °F) | 23 mph gusting 36, 8 % RH, 29 °C (84 °F) | high 5 h + 1 h; extreme 5 h + 1 h |
| Basalt | 14 mph gusting 25, 14 % RH, 28 °C (82 °F) | 18 mph gusting 30, 12 % RH, 25 °C (77 °F) | high 5 h + 1 h; extreme 5 h + 1 h |
| Aspen | 14 mph gusting 25, 16 % RH, 25 °C (76 °F) | 18 mph gusting 30, 13 % RH, 22 °C (72 °F) | high 5 h + 1 h; extreme 5 h + 1 h |
| Eagle-Vail corridor | 22 mph gusting 31, 14 % RH, 26 °C (78 °F) | 26 mph gusting 37, 11 % RH, 25 °C (78 °F) | high 5 h + 1 h; extreme 7 h + 1 h |

"High" is about one fire-season day in twenty at that community; "extreme" is
the worst one day in a hundred. After the event the community's "moderate" day
takes over: in Park City that is 14 mph and 16 % humidity in the afternoon and a
calm, 79 % humid night.

**Where the numbers come from.** Hourly observations from the airport weather
stations (Iowa Environmental Mesonet archive, 2005–2024), one station per
community, ranked day by day with the Fosberg fire-weather index (wind and how
dry the dead fuel is). Each class is the median of its days. Mountain homes sit
above or below their airport, so temperature and humidity are moved to the
homes' elevation with the change measured between the Eagle and Aspen airports.
Each community was checked against its historic fire day: the Woolsey, Camp,
Tubbs, Witch Creek, Marshall, Lake Christine and Grizzly Creek fire days all sit
in the top 2 % of their station's season. Santa Rosa is the one exception: its
airport is out on the plain, so extreme there is the Tubbs Fire night as measured
on the slopes (26 mph gusting 61, 7 % humidity).

**What changed in the demos** (each one-click scenario, page physics, the
grass-only speed floor on in both runs):

| Scenario | Acres at 1 h / 3 h / 6 h, before → after | First home reached, before → after |
|---|---|---|
| Malibu (extreme) | 2,248 / 4,750 / 7,444 → 1,365 / 3,467 / 5,640 | 2.5 h → 4.25 h |
| Paradise (extreme) | 336 / 999 / 1,839 → 82 / 408 / 821 | 0.5 h → 2.75 h |
| Santa Rosa (extreme) | 272 / 1,405 / 3,457 → 56 / 454 / 1,533 | 1.25 h → 4 h |
| Rancho Santa Fe (extreme) | 200 / 630 / 1,461 → 330 / 1,225 / 3,014 | 2.25 h → 2.25 h |
| Boulder (extreme) | 456 / 1,267 / 3,108 → 154 / 943 / 1,895 | 13.75 h → 9.75 h |
| Park City (extreme) | 6 / 843 / 7,124 → 4 / 12 / 1,517 | 3.25 h → 9.5 h |
| Vail (high) | 2 / 34 / 184 → 2 / 10 / 70 | 8 h → none in 24 h |
| Beaver Creek (high) | 2 / 14 / 150 → 2 / 4 / 32 | 11 h → none in 24 h |
| Eagle (high) | 12 / 120 / 683 → 16 / 440 / 1,015 | 4.25 h → 3 h |
| Glenwood Springs (high) | 382 / 805 / 1,841 → 8 / 274 / 1,049 | none → none in 24 h |
| Basalt (high) | 130 / 741 / 3,789 → 4 / 4 / 1,205 | 4.75 h → 9.5 h |
| Aspen (high) | 4 / 8 / 40 → 2 / 2 / 2 | 15 h → none in 24 h |
| Eagle-Vail corridor (high) | 158 / 227 / 593 → 99 / 217 / 425 | 14.75 h → none in 24 h |

Most demos are slower in their first hours; Park City drops from 7,124 to 1,517
acres at 6 h. Six burn more over the whole day than before (Boulder, Park City,
Basalt, Santa Rosa, Rancho Santa Fe, Eagle), and Eagle and Rancho Santa Fe are
faster from the start. That is not the weather being harsher: on these maps the
simulator burns more area in a lighter wind (see "Area does not grow with wind"
below).

**Three things to know before relying on it.**

1. **Safety: one gap.** The Malibu one-click scenario now shows one crew caught
   in the danger band (one tick, while driving out after a safety withdrawal);
   before it showed none. The same thing happens in Malibu today with the old
   weather at "high" or "moderate", so the weak spot is in how crews leave a
   home near a young fire, not in the weather. The other twelve one-click
   scenarios stay at zero. With today's speed rule (before the grass-only
   change lands) the one is Aspen instead of Malibu. It needs a fix in the
   crew-safety code before the city weather is shown to a client.
   **Fixed the same day** (`docs/SIMULATOR_DESIGN.md` "No crew drives an
   unjudged leg (2026-10-09)", contracts SAFETY SEMANTICS 20): the weak spots
   were in how the crew-safety code judged the roads crews drive, not in the
   weather.
2. **More wind burns less on these maps.** Measured on the Eagle, Rancho Santa
   Fe and Park City maps: with the same humidity, the burned area after 6 hours
   falls as the wind rises from 10 to about 30 mph (Eagle: 1,889 acres at
   10 mph, 172 at 28 mph, spotting off). Real fires do the opposite. This is a
   fire-spread issue for its own review; the city weather does not cause it.
3. **The first step is slower.** Creating a scenario and taking its first step
   took 6 s with the old Park City extreme and takes about 55 s now. A slower
   fire puts every home within reach of the response plan, and the planner then
   works much harder. The page already paid the same price for the old
   "moderate" (55 s) and "low" (101 s). Later steps are fast again.

**On the page.** Under the intensity buttons the simulator now lists what each
intensity means for the chosen community, for example "Park City · extreme:
21 mph gusting 31, 12 % RH, 25 C from ignition; eases after 5 h to a moderate
day". Once a fire burns, a line shows the weather at that moment and how long
the wind event has left.

## Method

Code: `tools/city_weather_climatology.py` (`fetch`, `analyze`, `anchors`,
`lapse`, `elevations`); output `docs/review/city_weather/climatology.json` and
`anchors.json`; `simui.CITY_WEATHER` transcribes the JSON and
`tests/test_city_weather.py` holds the two together.

1. **Data.** Iowa Environmental Mesonet ASOS/AWOS archive, keyless, routine and
   special METARs 2005–2024 (IEM files many sites' hourly reports as specials:
   Broomfield, July 2015, has 43 routine rows a week but 269 with specials). One
   report per hour, the one nearest the top of the hour. Wind is the two-minute
   10 m sustained wind of the report, gust the five-second peak. QC drops
   impossible RH or temperature, sustained winds over 70 mph without a gust
   behind them (Broomfield 147 kt on 2012-01-10, Eagle 109 kt on 2013-06-17) and
   isolated spikes (over 40 mph and more than 2.5 times the larger neighbour
   plus 10 mph).
2. **Index.** Each hour gets the Fosberg Fire Weather Index (FFWI; Fosberg 1978),
   computed from the Simard equilibrium moisture content, the same formula the
   engine uses for dead fuel (`fuels.dead_fuel_moisture_frac`, without its 1.03
   multiplier). It is not capped at 100, because the days are ranked by it. A
   centred 3-hour mean smooths the two-minute wind sample.
3. **Days.** Each fire-season day (local standard time, at least 18 valid hours)
   is scored by its highest smoothed index. Its peak period is the run of hours
   around that peak where the smoothed index stays at or above 70 % of it,
   followed across midnight. The afternoon conditions are the mean wind, gust,
   RH and temperature over the peak period: they happened together, unlike
   percentiles taken separately. The easing is the hours from the end of the
   peak period until the index is half the peak. The night is the pre-dawn
   trough (mean wind 02–06, highest RH and lowest temperature 00–08).
4. **Classes.** Low is the 40th–60th percentile day, moderate the 75th–90th,
   high the 93rd–98th and extreme the top 1 %. Each class is the median of its
   days: afternoon, night, hold (the peak period) and easing.
5. **Elevation.** Homes' elevation is the median ground elevation of 200 homes
   the simulator places on the committed tile. Temperature and dew point move
   with the lapse rates measured between Eagle (1,993 m) and Aspen (2,382 m) on
   the same hours of 2005–2024: afternoons 7.1 °C/km and 2.8 °C/km, pre-dawn
   2.3 °C/km and 2.9 °C/km. RH is recomputed (Magnus). Wind is not adjusted.
6. **Record envelope.** Where a community's own historic fire day, measured
   inside the community by a fire-weather station, was harsher than its
   airport's top 1 %, extreme is that day. This applies to Santa Rosa only: the
   Santa Rosa RAWS read 26 mph sustained, 61 mph gust, 7 % RH and 32.8 °C at
   04:00 PDT on 9 October 2017 (FFWI 78, its 99th percentile), against an airport
   top 1 % of 19 mph and 18 % RH. Its hold and easing are the Tubbs day as the
   airport measured it.

**Time shape in the simulator** (`weather.DailyCycle`, `weather.WindEvent`,
`simui.city_mock_weather`). Low and moderate are a 24-hour cycle on local
standard time: a half cosine up from the 06:00 trough to the afternoon peak (the
moderate days' median peak hour, 13:00–15:00), a half cosine down through the
evening and night. High and extreme hold the class's afternoon values from the
ignition for the hold hours, then blend linearly over the easing hours into the
community's moderate cycle. A new event begins at a high or extreme ignition
unless one at the same intensity is still blowing, in which case the new fire
joins it; changing the intensity starts the new intensity's event. The wind
direction stays the wind dial's. The simulation clock starts at 14:13 UTC, which
is 06:13 local standard time in California and 07:13 in Colorado and Utah, so
the one-click fires start with their wind event in the early morning. Real
Colorado and Utah events peak in the afternoon. Every hour is a pure function of
absolute time, so the forecast is deterministic and can be extended at any
moment without changing the hours already built.

## Stations

| Community | Station | Station m | Homes m | Season | Fire-season days | Peak hour (local standard) |
|---|---|---|---|---|---|---|
| Malibu | KCMA Camarillo (AWOS), 39 km WNW, where the Santa Ana flow leaves the Santa Clara and Conejo gaps; cross-checks KSMO, KVNY | 23 | 246 | Sep–Dec | 2,050 | 13 |
| Paradise | KCIC Chico Municipal, 22 km W on the valley floor; cross-check KOVE | 73 | 572 | Jun–Nov | 2,346 (2011–2024: too few hourly reports before mid-2011) | 15 |
| Santa Rosa | KSTS Sonoma County Airport, 14 km W | 31 | 138 | Jun–Nov | 3,630 | 15 |
| Rancho Santa Fe | KCRQ Carlsbad / Palomar, 14 km NNW on the same coastal mesa; cross-check KRNM Ramona | 100 | 62 | Sep–Dec | 2,351 | 13 |
| Boulder | KBDU Boulder Municipal (AWOS), 11 km NNW, under the foothills' downslope winds, 2010–2024; cross-check KBJC | 1,612 | 1,637 | all year | 5,157 | 15 |
| Park City | Heber Valley (K36U to 2017, KHCR after), 28 km SSE, 2010–2024; cross-check KSLC | 1,718 | 2,042 | Jun–Oct | 2,101 | 14 |
| Vail | KEGE Eagle County Regional, 48 km W; cross-check KASE | 1,993 | 2,496 | Jun–Sep | 2,211 | 14 |
| Beaver Creek | KEGE, 31 km W; cross-check KASE | 1,993 | 2,312 | Jun–Sep | 2,211 | 14 |
| Eagle | KEGE, 3 km WSW; cross-check KRIL | 1,993 | 2,010 | Jun–Sep | 2,211 | 14 |
| Glenwood Springs | KRIL Rifle / Garfield County, 35 km W down the Colorado River (Glenwood has no station in the archive); cross-check KEGE | 1,691 | 1,814 | Jun–Sep | 2,394 | 15 |
| Basalt | KASE Aspen / Sardy Field, 29 km SE up the same valley, moved down 384 m; cross-checks KEGE, KRIL | 2,382 | 1,998 | Jun–Sep | 2,418 | 14 |
| Aspen | KASE, in the community; cross-check KEGE | 2,382 | 2,428 | Jun–Sep | 2,418 | 14 |
| Eagle-Vail corridor | KEGE, 26 km W, at the corridor's west end | 1,993 | 2,256 | Jun–Sep | 2,211 | 14 |

Boulder's season is the whole year because its grass fires are cold-season
downslope-wind events as often as summer ones (Marshall was 30 December): its
top 1 % days fall in every month from October to May. Broomfield (KBJC, 6 km
from the tile centre) is the cross-check rather than the source because it sat
outside the Marshall windstorm's surface footprint: 15 mph that day against
Boulder Municipal's 40 mph.

## Every community, every intensity

Afternoon (or the event's held) values and the pre-dawn trough. mph, %, °C.

| Community | Intensity | Afternoon wind / gust | RH | °C | Night wind | Night RH | Night °C | Shape |
|---|---|---|---|---|---|---|---|---|
| Malibu | low | 9.4 / 13.2 | 57.1 | 21.3 | 2.0 | 84.1 | 12.3 | daily cycle |
|  | moderate | 15.0 / 26.5 | 27.3 | 19.5 | 3.9 | 72.4 | 10.1 | daily cycle |
|  | high | 22.7 / 33.4 | 10.2 | 23.0 | 12.4 | 40.0 | 11.7 | 8 h hold + 2 h ease |
|  | extreme | 29.4 / 42.1 | 8.6 | 18.8 | 21.6 | 13.1 | 13.9 | 10 h hold + 4 h ease |
| Paradise | low | 10.4 / 13.8 | 29.6 | 28.3 | 2.1 | 60.7 | 16.9 | daily cycle |
|  | moderate | 14.6 / 19.5 | 26.3 | 27.2 | 2.7 | 57.1 | 16.9 | daily cycle |
|  | high | 18.9 / 27.0 | 19.3 | 25.3 | 3.6 | 48.1 | 15.9 | 6 h hold + 2 h ease |
|  | extreme | 24.4 / 33.1 | 17.0 | 23.6 | 10.2 | 41.9 | 12.9 | 7 h hold + 1 h ease |
| Santa Rosa | low | 10.4 / 14.9 | 47.4 | 23.5 | 1.4 | 95.7 | 10.9 | daily cycle |
|  | moderate | 11.5 / 16.5 | 35.9 | 27.3 | 0.7 | 92.5 | 10.4 | daily cycle |
|  | high | 13.4 / 21.9 | 28.2 | 26.6 | 1.6 | 88.5 | 9.8 | 5 h hold + 1 h ease |
|  | extreme | 26.0 / 61.1 | 7.0 | 32.8 | 3.5 | 85.7 | 9.8 | 4 h hold + 12 h ease (Tubbs) |
| Rancho Santa Fe | low | 7.7 / 11.3 | 60.4 | 21.3 | 1.8 | 83.9 | 15.1 | daily cycle |
|  | moderate | 8.5 / 12.5 | 45.0 | 21.1 | 2.5 | 72.8 | 14.5 | daily cycle |
|  | high | 8.8 / 12.9 | 24.6 | 22.2 | 3.2 | 55.3 | 13.4 | 4 h hold + 1 h ease |
|  | extreme | 13.7 / 22.4 | 8.0 | 24.7 | 2.9 | 50.8 | 12.7 | 6 h hold + 1 h ease |
| Boulder | low | 7.3 / 10.6 | 27.1 | 21.3 | 2.3 | 66.4 | 9.9 | daily cycle |
|  | moderate | 13.8 / 23.8 | 22.0 | 17.1 | 3.5 | 58.8 | 5.9 | daily cycle |
|  | high | 20.7 / 31.1 | 19.5 | 14.0 | 4.8 | 55.0 | 2.9 | 5 h hold + 1 h ease |
|  | extreme | 31.9 / 48.1 | 21.9 | 12.2 | 7.2 | 44.3 | 5.4 | 5 h hold + 1 h ease |
| Park City | low | 10.4 / 18.4 | 19.8 | 24.4 | 0.7 | 82.2 | 5.3 | daily cycle |
|  | moderate | 14.1 / 21.3 | 15.8 | 26.0 | 0.7 | 79.4 | 6.3 | daily cycle |
|  | high | 17.8 / 26.2 | 16.0 | 25.0 | 0.9 | 74.0 | 6.3 | 5 h hold + 1 h ease |
|  | extreme | 21.3 / 31.0 | 12.4 | 25.4 | 0.9 | 66.7 | 10.3 | 5 h hold + 1 h ease |
| Vail | low | 12.9 / 21.9 | 24.6 | 23.4 | 2.1 | 69.8 | 7.8 | daily cycle |
|  | moderate | 17.8 / 26.5 | 17.7 | 24.0 | 2.3 | 63.9 | 6.8 | daily cycle |
|  | high | 21.9 / 31.1 | 15.0 | 23.8 | 2.1 | 57.7 | 6.3 | 5 h hold + 1 h ease |
|  | extreme | 26.0 / 37.0 | 12.0 | 23.6 | 2.8 | 48.7 | 7.7 | 7 h hold + 1 h ease |
| Beaver Creek | low | 12.9 / 21.9 | 23.6 | 24.7 | 2.1 | 70.4 | 8.3 | daily cycle |
|  | moderate | 17.8 / 26.5 | 17.0 | 25.3 | 2.3 | 64.5 | 7.3 | daily cycle |
|  | high | 21.9 / 31.1 | 14.4 | 25.1 | 2.1 | 58.3 | 6.8 | 5 h hold + 1 h ease |
|  | extreme | 26.0 / 37.0 | 11.5 | 24.9 | 2.8 | 49.2 | 8.2 | 7 h hold + 1 h ease |
| Eagle | low | 12.9 / 21.9 | 22.1 | 26.9 | 2.1 | 71.4 | 9.0 | daily cycle |
|  | moderate | 17.8 / 26.5 | 16.0 | 27.5 | 2.3 | 65.5 | 8.0 | daily cycle |
|  | high | 21.9 / 31.1 | 13.5 | 27.3 | 2.1 | 59.2 | 7.5 | 5 h hold + 1 h ease |
|  | extreme | 26.0 / 37.0 | 10.8 | 27.1 | 2.8 | 50.1 | 8.9 | 7 h hold + 1 h ease |
| Glenwood Springs | low | 12.3 / 23.0 | 20.1 | 28.2 | 2.3 | 63.7 | 11.9 | daily cycle |
|  | moderate | 16.7 / 26.8 | 13.4 | 29.5 | 2.3 | 55.1 | 11.4 | daily cycle |
|  | high | 19.6 / 30.6 | 11.1 | 29.5 | 2.3 | 49.6 | 10.8 | 5 h hold + 1 h ease |
|  | extreme | 23.3 / 36.2 | 8.2 | 28.9 | 2.5 | 43.3 | 10.8 | 5 h hold + 1 h ease |
| Basalt | low | 9.9 / 19.9 | 20.7 | 26.2 | 5.5 | 69.6 | 9.2 | daily cycle |
|  | moderate | 12.0 / 22.2 | 16.1 | 27.0 | 5.8 | 62.8 | 8.7 | daily cycle |
|  | high | 13.8 / 25.3 | 14.2 | 27.6 | 6.4 | 57.3 | 9.2 | 5 h hold + 1 h ease |
|  | extreme | 18.0 / 29.9 | 12.2 | 25.1 | 6.4 | 47.5 | 8.1 | 5 h hold + 1 h ease |
| Aspen | low | 9.9 / 19.9 | 22.8 | 23.2 | 5.5 | 68.1 | 8.2 | daily cycle |
|  | moderate | 12.0 / 22.2 | 17.7 | 24.0 | 5.8 | 61.5 | 7.7 | daily cycle |
|  | high | 13.8 / 25.3 | 15.6 | 24.6 | 6.4 | 56.0 | 8.2 | 5 h hold + 1 h ease |
|  | extreme | 18.0 / 29.9 | 13.3 | 22.1 | 6.4 | 46.3 | 7.1 | 5 h hold + 1 h ease |
| Eagle-Vail corridor | low | 12.9 / 21.9 | 23.3 | 25.1 | 2.1 | 70.6 | 8.4 | daily cycle |
|  | moderate | 17.8 / 26.5 | 16.8 | 25.7 | 2.3 | 64.7 | 7.4 | daily cycle |
|  | high | 21.9 / 31.1 | 14.3 | 25.5 | 2.1 | 58.5 | 6.9 | 5 h hold + 1 h ease |
|  | extreme | 26.0 / 37.0 | 11.4 | 25.3 | 2.8 | 49.4 | 8.3 | 7 h hold + 1 h ease |

The night columns of high and extreme describe those days at the station; the
simulator does not use them, because after the event it returns to the
moderate cycle.

## Historic fire days

Read straight off the archive (`tools/city_weather_climatology.py anchors`):
the day's peak period at the station, with its hourly maximum wind and minimum
RH, and where the day ranks among that community's fire-season days.

| Fire | Station | Day | Rank in the season | Peak-period wind (max hour), mph | Gust | RH % (min) | °C | Hold + ease h |
|---|---|---|---|---|---|---|---|---|
| Woolsey | KCMA | 2018-11-09 | 99.6th pct | 26.8 (32.2) | 39.5 | 3.0 (2.0) | 25.9 | 7 + 2 |
| Woolsey | KVNY | 2018-11-09 | 99.7th | 21.1 (25.3) | 29.8 | 5.3 (3.4) | 22.8 | 25 + 3 |
| Woolsey | KSMO | 2018-11-08 | 89.9th | 6.9 (8.1) | – | 6.7 (6.4) | 23.3 | 3 + 0 |
| Camp | KCIC | 2018-11-08 | 98.9th | 21.6 (25.3) | – | 7.0 (6.1) | 20.5 | 4 + 1 |
| Camp | KOVE | 2018-11-08 | 96.5th | 12.9 (17.3) | 24.4 | 16.6 (14.2) | 19.3 | 5 + 7 |
| Tubbs | KSTS | 2017-10-08 | 99.8th | 18.7 (20.7) | 27.0 | 5.5 (3.7) | 29.2 | 4 + 12 |
| Witch Creek | KCRQ | 2007-10-22 | 99.9th | 14.2 (18.4) | 25.2 | 3.9 (3.6) | 28.8 | 8 + 7 |
| Witch Creek | KRNM | 2007-10-21 | 100th | 26.8 (34.5) | 40.9 | 7.2 (5.8) | 21.6 | 18 + 29 |
| Marshall | KBDU | 2021-12-30 | 100th | 40.3 (46.0) | 66.3 | 21.8 (19.1) | 6.8 | 5 + 0 |
| Marshall | KBJC | 2021-12-30 | 78.4th | 14.6 (19.6) | 28.4 | 18.2 (15.4) | 7.4 | 7 + 6 |
| Parleys Canyon | Heber | 2021-08-15 | 63.1st | 11.5 (12.7) | 17.3 | 13.5 (12.8) | 32.0 | 3 + 1 |
| Parleys Canyon | KSLC | 2021-08-14 | 58.2nd | 11.3 (11.5) | 17.3 | 14.5 (12.7) | 37.0 | 5 + 1 |
| Lake Christine | KASE | 2018-07-03 | 98.7th | 16.9 (33.4) | 32.8 | 17.9 (10.8) | 25.4 | 3 + 1 |
| Grizzly Creek | KRIL | 2020-08-11 | 98.9th | 22.8 (25.3) | 34.3 | 6.5 (6.0) | 34.1 | 5 + 1 |
| Grizzly Creek | KEGE | 2020-08-11 | 81.7th | 16.4 (24.2) | 25.6 | 10.1 (8.9) | 30.0 | 4 + 2 |
| Sylvan | KEGE | 2021-06-20 | 96.8th | 20.7 (28.8) | 30.1 | 12.8 (8.3) | 30.6 | 8 + 2 |

Published fire-day conditions, for comparison:

- **Camp Fire (Paradise).** Jarbo Gap RAWS: 25–30 mph sustained, gusts 45–50,
  strongest between 02:00 and 06:00 on 8 November 2018; RH about 23 % at
  ignition, falling to about 10 % by the afternoon; daytime gusts 25–35 mph with
  12–20 mph sustained (CAL FIRE Green Sheet). The communities lie in a thermal
  belt with poor overnight humidity recovery. Paradise's extreme row (24 mph
  gusting 33, 17 %) sits inside this.
- **Tubbs Fire (Santa Rosa).** Santa Rosa RAWS at 11:00 UTC on 9 October 2017:
  11.6 m/s (26 mph), gust 27.3 m/s (61 mph), RH 7 %, 32.8 °C, FFWI 78; Hawkeye
  RAWS (on the ridge) 21.5 m/s sustained, 35.3 m/s gust (79 mph), RH 12 %, FFWI
  128 (its record); at least four consecutive hours above the 99th percentile,
  at night (Nauslar et al. 2018).
- **Woolsey Fire (Malibu).** Sustained winds rose from about 10 to 30 mph with
  60 mph gusts on the morning of 8 November 2018; on the 9th much of the region
  gusted 50–69 mph (Athenium wind analysis); single-digit humidity.
- **Witch Creek Fire (Rancho Santa Fe).** Santa Ana gusts of 26 m/s at Julian,
  local reports far higher in the San Pasqual Valley, RH near 5 % (Witch Fire
  summary). Carlsbad, the RSF station, read 14 mph and 4 % that day; Ramona,
  inland, 27 mph.
- **Marshall Fire (Boulder).** Gusts to 115 mph at Arvada, 110 mph at Rocky
  Flats, 81–85 mph in south Boulder, 68 mph in Louisville; strongest around
  noon, easing in the late afternoon (NWS Boulder).
- **Parleys Canyon Fire (Park City).** A hot afternoon start beside I-80 on
  14 August 2021, run uphill by the wind; a Red Flag Warning for 40–50 mph winds
  came three days later. Heber and Salt Lake City read 11–12 mph that afternoon.
  Park City's extreme row is Heber's top 1 % (mostly June wind days), not this
  fire.
- **Lake Christine Fire (Basalt).** Ignited 3 July 2018; the 4 July run came
  with outflow gusts to 50 mph and single-digit RH (news reports). Aspen's
  airport recorded its strongest smoothed peak of that season on 3 July, with a
  33 mph hour at 16:00.
- **Grizzly Creek Fire (Glenwood Springs).** 10 August 2020, about 13:30: 93 °F,
  single-digit RH, 5–16 mph winds gusting 20–30 (CPR News); Rifle peaked the
  next afternoon at 23 mph, 7 %, 34 °C. **South Canyon (1994):** a cold front
  raised 10–15 mph winds to about 30 mph with 45 mph gusts around 16:00
  (USDA/USDI investigation).

## The thirteen one-click scenarios, before and after

`scratch_review/city_weather_scenarios.py PROFILE 24`: each scenario exactly as
the page builds it (`POST /api/sim/new {scenario}`: spotting and the grass speed
floor on, plan on roads, 24 h coating hold) with `ros_floor_grass_only=True` set
explicitly in both runs, 15-minute ticks for 24 h. Before = the base commit
(05cf1d6) with no profile key; after = city weather. Safety columns follow
`scratch_review/bench_dispatch.py`: the per-tick maximum of
`scoreboard.violations`, ticks with a crew-overrun row, safety withdrawals and
relocations. The generic profile on the new code reproduced the base commit's
run exactly in all 13 scenarios: every acreage, home, safety count and the 30 h
weather series.

| Scenario | Intensity | Acres 1 h | 3 h | 6 h | 24 h | First home reached | Violations / overrun ticks / withdrawn / relocations |
|---|---|---|---|---|---|---|---|
| aspen_southwesterly | high | 4 → 2 | 8 → 2 | 40 → 2 | 7,234 → 18 | 15 h → none | 0/0/0/1 → 0/0/0/1 |
| basalt_carbondale_southwesterly | high | 130 → 4 | 741 → 4 | 3,789 → 1,205 | 16,008 → 26,244 | 4.75 h → 9.5 h | 0/0/0/2 → 0/0/0/1 |
| beaver_creek_westerly | high | 2 → 2 | 14 → 4 | 150 → 32 | 7,039 → 3,433 | 11 h → none | 0/0/0/3 → 0/0/0/1 |
| boulder_chinook | extreme | 456 → 154 | 1,267 → 943 | 3,108 → 1,895 | 12,348 → 23,995 | 13.75 h → 9.75 h | 0/0/0/0 → 0/0/0/0 |
| eagle_gypsum_westerly | high | 12 → 16 | 120 → 440 | 683 → 1,015 | 5,851 → 13,194 | 4.25 h → 3 h | 0/0/0/5 → 0/0/0/7 |
| eagle_vail_corridor_westerly | high | 158 → 99 | 227 → 217 | 593 → 425 | 7,215 → 1,848 | 14.75 h → none | 0/0/0/3 → 0/0/0/0 |
| glenwood_springs_westerly | high | 382 → 8 | 805 → 274 | 1,841 → 1,049 | 12,308 → 11,079 | none → none | 0/0/0/0 → 0/0/0/1 |
| malibu_santa_ana | extreme | 2,248 → 1,365 | 4,750 → 3,467 | 7,444 → 5,640 | 16,925 → 16,661 | 2.5 h → 4.25 h | 0/0/1/1 → **1/1**/1/3 |
| paradise_ne_wind | extreme | 336 → 82 | 999 → 408 | 1,839 → 821 | 18,847 → 5,710 | 0.5 h → 2.75 h | 0/0/0/3 → 0/0/0/1 |
| park_city_windstorm | extreme | 6 → 4 | 843 → 12 | 7,124 → 1,517 | 21,843 → 26,366 | 3.25 h → 9.5 h | 0/0/0/2 → 0/0/0/0 |
| rancho_santa_fe_santa_ana | extreme | 200 → 330 | 630 → 1,225 | 1,461 → 3,014 | 7,336 → 14,215 | 2.25 h → 2.25 h | 0/0/0/13 → 0/0/1/1 |
| santa_rosa_diablo | extreme | 272 → 56 | 1,405 → 454 | 3,457 → 1,533 | 9,679 → 12,894 | 1.25 h → 4 h | 0/0/0/0 → 0/0/1/1 |
| vail_westerly | high | 2 → 2 | 34 → 10 | 184 → 70 | 6,059 → 534 | 8 h → none | 0/0/0/0 → 0/0/0/0 |

"First home reached" reads the burned cell under each home, as in
`scenario_real_tiles_check.py`; homes on built-up cells reached only through the
ember-radius rule are not counted.

### Safety: one gap

**Malibu.** At T+0.52 h crew sim-g1, arriving at its first home, was withdrawn
by the road-egress guard (projected departure 4.5 min past the live
latest-depart). At the T+0.75 h audit it was egressing inside the eroded-P10
band, at a point the pessimistic front had come within the erosion radius of at
T+0.37 h. The audit reported it: one crew-overrun row, violations 1 for one
tick. **The old weather does the same in Malibu** at "high" (two overrun ticks,
first at T+1.0 h) and "moderate" (one, at T+2.25 h), each time a crew caught in
the band while egressing (`scratch_review/city_weather_overrun_probe.py
malibu`). Old "extreme" happened not to trigger it.

**Synthetic tests.** Three scripted test scenarios
(`test_homes_mode_triage_reason_surfaces`,
`test_outcome_triaged_matches_reason`,
`test_kept_stop_flagged_and_audited_under_new_rule`) show one overrun row under
city weather. In the triage scenario the overrun needs the daily cycle: constant
weather at the cycle's afternoon values or at its night values, and the old low,
moderate and high, show none (`scratch_review/city_weather_overrun_probe.py
triage`). The mechanism: in the cool, calm morning the young fire is a few
cells, while the forecast's afternoon brings the homes inside the 72 h notice;
the crew finishes a home and its straight-leg egress passes 467 m from the
ignition cell (burning since T+0), inside the 550 m grass erosion band, so the
10c audit reports it. The crew-safety code reads only the arrival fields, never
the weather, and the fields are consistent where checked (P10 before the
nominal arrival). These three tests pin the legacy presets they were derived
under; `test_known_gap_young_fire_egress_overrun_under_city_weather` recorded the
gap as a strict xfail (fixed 2026-10-09: now
`test_young_fire_leg_no_overrun_under_city_weather`, below). The crew-safety
scenario of `test_simui.py` (controlled tile, 2 crews, 40 ticks) stays clean
under city weather at every intensity in Boulder, Paradise, Park City and
Malibu.

**With today's speed floor.** The measurements above use the grass-only floor
(`ros_floor_grass_only=True`), which the simulator is about to make its
default. With the floor the page uses today (all four fuel classes),
`scratch_review/city_weather_overrun_probe.py floor` (12 h, old presets vs city
weather): every scenario stays at 0 violations and 0 overrun ticks, Malibu
included, except **Aspen: 0 → 1 overrun tick** (sim-g3, imminent while
egressing, T+3.5 h). The same pattern, in a different place.

**Decision needed.** The acceptance rule was "overruns and violations stay 0
where they are 0 today". Malibu (grass-only floor) and Aspen (today's floor)
break it. Two options: fix the egress gap (a crew should not be routed to, or
out past, a point inside the eroded band), or keep city weather off until
then. The default is one constant: `simui.SIM_WEATHER_PROFILE` (`"generic"`
restores the old presets for every location).

**Update 2026-10-09: fixed.** The egress gap is closed in the crew-safety code
(contracts SAFETY SEMANTICS 20; `docs/SIMULATOR_DESIGN.md` "No crew drives an
unjudged leg (2026-10-09)"). Root causes, all in how the code judged the roads
crews drive: a crew withdrawn at an early home drove to the safe zone planned
for the END of its route on a road nobody had checked from that home (Malibu);
a crew driving to a zone kept going when the zone would be threatened before
the next check (Aspen, and Glenwood in the wider sweep); a voluntary move rode
road the law had judged exposed (Eagle); and the corridor check sampled every
300 m, stepping over the corner of the ignition cell's band (the triage test).
The sweep of all thirteen one-click scenarios at every intensity, both weather
tables and both speed floors (208 runs, 6 h) is at 0 overrun ticks and 0
departure violations. The strict xfail is now a passing test
(`tests/test_city_weather.py::test_young_fire_leg_no_overrun_under_city_weather`).

## Engine notes

**Edge law.** With a forecast that changes hour to hour, the engine's default
edge law prices a whole cell-to-cell traversal at the weather of the hour it
starts (a known late bias, `edge_weather_piecewise`). Measured under city
weather on three one-click maps (`scratch_review/city_weather_engine_probes.py
edge`; nominal fire, 1–48 h): switching to the piecewise law changed the burned
area by at most 5.3 % at any lead (Park City 46,334 vs 45,874 acres at 48 h;
Malibu 38,524 vs 38,508; Eagle 38,630 vs 39,559) and cost 7–12 times the first
field's solve (Park City 3.5 s vs 25.4 s; Malibu 2.6 s vs 18.9 s; Eagle 3.6 s
vs 41.6 s). The simulator keeps the default law.

**Re-anchor under a changing forecast.** A re-anchor (wind dial, a second
ignition, the horizon rebase) now carries the weather the fire burned in hour by
hour (`simui._field_wx_spans`), not one sample per field. Measured with a no-op
wind re-set at T+2 h 15 min (`scratch_review/city_weather_reanchor_probe.py`):
identical to the continuous run at +0.5, +1 and +3 h in Park City, Malibu and
Eagle; at +6 h Park City reads area 1.005, IoU 0.927, mean shift −2.1 min, Eagle
0.992 / 0.992 / +1.1 min, Malibu identical. Under the generic presets every lead
is identical, as before. The residual comes from the engine's hour buckets
restarting at the anchor.

**Area does not grow with wind on these maps.**
`scratch_review/city_weather_engine_probes.py sweep`: `ros_floor_grass_only`,
constant weather (13 % RH, 30 °C), nominal fire from each scenario's ignition,
acres at 6 h:

| Wind (mph) | 10 | 15 | 20 | 22 | 25 | 28 | 32 | 40 |
|---|---|---|---|---|---|---|---|---|
| Eagle, spotting off | 1,889 | 1,505 | 985 | 741 | 384 | 172 | 132 | 342 |
| Eagle, spotting on | 2,008 | 1,639 | 1,119 | 995 | 737 | 683 | 572 | 877 |
| Rancho Santa Fe, spotting off | 2,010 | 1,641 | 1,069 | 929 | 757 | 596 | 580 | 727 |
| Park City, spotting off | 362 | 120 | 50 | 50 | 32 | 22 | 28 | 36 |

The engine applies no wind limit (Andrews, Cruz & Rothermel 2013), so the head
rate keeps rising with wind. A likely cause is the ellipse: its length-to-breadth
ratio reaches its cap of 8 at moderate midflame winds, and on a 16-direction
grid a narrow ellipse leaves little rate on any edge that is not aligned with
the head, while terrain and fuel breaks stop the head. This needs its own
spread review. It also explains the scenarios that got faster with the lower
city winds.

**Time to the first step.** Park City through the server
(`scratch_review/city_weather_engine_probes.py tick`, first tick only): old
extreme 6.4 s (fire field 3.1 s, dispatch 3.1 s), city extreme 54 s (field
3.9 s, dispatch 49.8 s in three planner calls), old moderate 55 s, old low
101 s. The fire field is as fast as before. The planner does more work when a
slower fire leaves every home serviceable. The 24 h scenario runs took 4–162 s
(old: 4–84 s).

## Options, files, tests

- `ResponseConfig.sim_weather_profile` (contracts, default `"generic"`,
  validated; README row): read only by the simulator. `simui.SIM_WEATHER_PROFILE
  = "city"` is the simulator default; an explicit `cfg_overrides` value (page or
  API `cfg`) wins.
- `weather.DailyCycle`, `weather.WindEvent`: `MockWeather(diurnal=, event=)`,
  additive; without them the series is the legacy script byte for byte
  (pinned against a verbatim copy of the old loop).
- `simui.CITY_WEATHER`, `CityWeather`, `CityDay`, `city_mock_weather`,
  `weather_label`; `state()["weather"]` (profile, community, station, presets,
  label, event clock, conditions now); the page's legend under the intensity
  buttons.
- `seeded.py` (drills on the TEST map, anywhere in the country) keeps the generic
  table for every seed, including seeds inside a simulator community: a seed's
  forecast is rebuilt from the watcher's cycle clock, and the TEST map already
  offers live NWS weather for realism.
- Tests pinned to the legacy presets (never re-pinned numbers):
  `test_simui.py` (`FAST`, `MON_CFG`, `PHYS_CFG` and five inline configs),
  `test_simui_policy.py`, `test_sim_crew_placement.py`, `test_sim_fire_shading.py`,
  `test_server.py` (`SIM_FAST`, which `test_wind_direction.py` also uses), the
  Glenwood roads probe in `test_sim_left_map.py` and `test_sim_escape_roads.py`;
  `test_sim_reanchor_progress.py` and `test_wind_direction.py` take `FAST` from
  `test_simui.py`.
- Full suite (serial, 7 min): 2,330 passed, 13 skipped, 1 xfailed (the gap
  above), 3 failed — the three CLI tests that pass `--aoi -121,38,-120,39`,
  which this machine's Python 3.13.13 argparse reads as an option (a
  standalone `argparse` parse of that value fails the same way); unrelated to
  this change.
- New: `tests/test_city_weather.py` (33 tests); `tools/city_weather_climatology.py`;
  `docs/review/city_weather/climatology.json`, `anchors.json`;
  `scratch_review/city_weather_scenarios.py` (+ `_legacy.json`, the base-commit
  run and the "before" column; `_generic.json`; `_city.json`),
  `scratch_review/city_weather_reanchor_probe.py` (+ `.json`),
  `scratch_review/city_weather_overrun_probe.py`,
  `scratch_review/city_weather_engine_probes.py`.

## Limits

- **Airports, not slopes.** The stations sit on valley floors and plains. At
  night they decouple: calm and humid. Slope and ridge homes in a thermal belt
  stay warmer, drier and breezier, so the simulated fire lies down more at night
  than a fire on the slopes above Paradise or Park City would. Airport winds
  also under-read downslope events on the ridges (the Tubbs night: 19 mph at the
  airport, 48 mph at Hawkeye).
- **One station per community, up to 48 km away** (Vail from Eagle). Vail and
  Beaver Creek take Eagle's wind; Aspen's airport, at Vail's elevation, reads
  lighter winds (extreme 18 against 26 mph), so Vail's may be overstated.
- **One shape per class.** Every high or extreme fire gets the median hold and
  easing; real events vary (Malibu's extreme hold: 8 to 13 h interquartile).
- **The event starts with the fire**, at 06:13–07:13 local time for the one-click
  scenarios, so Colorado and Utah demos see an afternoon's wind in the morning.
- **Direction is the dial's.** No sea-breeze or drainage reversal after an event.
- **Gusts are display only**; the spread uses the sustained wind
  (`wind_gust_blend` 0).
- **Two-minute airport winds**, not the 20-ft ten-minute RAWS wind fire-weather
  forecasts use; the Santa Rosa extreme row keeps the RAWS wind as published.

## Sources

- Iowa Environmental Mesonet, ASOS/AWOS archive download service:
  https://mesonet.agron.iastate.edu/request/download.phtml (network metadata:
  https://mesonet.agron.iastate.edu/geojson/network/CO_ASOS.geojson and the UT and
  CA equivalents).
- NOAA NCEI Integrated Surface Database station history (station search):
  https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv
- Fosberg, M. A. (1978), Weather in wildland fire management: the fire weather
  index; Goodrick, S. L. (2002), Modification of the Fosberg fire weather index
  to include drought, Int. J. Wildland Fire 11.
- Simard, A. J. (1968), The moisture content of forest fuels (equilibrium
  moisture content).
- Andrews, Cruz & Rothermel (2013), Examination of the wind speed limit function
  in the Rothermel surface fire spread model, Int. J. Wildland Fire 22.
- CAL FIRE, Camp Incident Green Sheet 18-CA-BTU-016737:
  https://www.firefighterclosecalls.com/wp-content/uploads/2018/12/18-CA-BTU-016737-Camp-Green-Sheet.pdf
- Nauslar, N. J., Abatzoglou, J. T. & Marsh, P. T. (2018), The 2017 North Bay
  and Southern California Fires: A Case Study, Fire 1(1):18:
  https://www.spc.noaa.gov/publications/nauslar/2017cali.pdf
- Mass, C. & Ovens, D. (2019), The Northern California Wildfires of 8–9 October
  2017: The Role of a Major Downslope Wind Event, BAMS 100(2):
  https://journals.ametsoc.org/view/journals/bams/100/2/bams-d-18-0037.1.xml
- Woolsey Fire: Athenium Analytics wind analysis,
  https://www.athenium.com/2018/11/16/wind-data-california-woolsey-camp-wildfires/;
  LA County After Action Review, https://file.lacounty.gov/SDSInter/bos/supdocs/144968.pdf
- Witch Fire: https://en.wikipedia.org/wiki/Witch_Fire; City of San Diego,
  https://www.sandiego.gov/fire/about/major-fires-incidents/2007-witch-creek-guejito-fires
- NWS Boulder, Marshall Fire high winds of 30 December 2021:
  https://www.weather.gov/bou/HighWinds12_30_2021
- Parleys Canyon Fire: KUER,
  https://www.kuer.org/news/2021-08-14/parleys-canyon-fire-burns-nearly-3-000-acres-and-forces-8-000-homes-to-be-evacuated;
  Deseret News, https://www.deseret.com/utah/2021/8/17/22629309/parleys-canyon-fire-crews-fear-blaze-could-spread-very-rapidly-in-storm-wildfire-evacuations/
- Lake Christine Fire: https://en.wikipedia.org/wiki/Lake_Christine_Fire;
  Westword, https://www.westword.com/news/lake-christine-fire-update-july-2018-blaze-worsens-10564210
- Grizzly Creek Fire: CPR News,
  https://www.cpr.org/2020/08/12/grizzly-creek-fire-update-august-12/
- South Canyon Fire Investigation (USDA Forest Service / USDI BLM, 1994):
  https://npshistory.com/publications/fire/south-canyon-fire-investigation.pdf
