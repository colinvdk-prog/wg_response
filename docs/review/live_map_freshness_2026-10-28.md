# Live map: freshness tiers and terrain fallback (2026-10-28)

The founder, 2026-10-28: *"review so the full map is live"*, then *"for the live map, have a
graduated level of stale. for a fire under control, stale is 6 hours, for a fire that is growing
stale is 1 hour, for a fire that has at least medium risk that has clients in its path, stale is 15
minutes"* and *"go ahead with both and redeploy the live map"*.

## What the review found (watcher world13, CONUS)

- The map covered the whole continental US: 207 fires, plus 26 contained removed and 299 stale
  excluded.
- One cycle took 40 min against a 5-min poll period, so the map always read STALE (more than 15 min).
  35 of those minutes were spent re-forecasting two fires: Cooley (CA, 95 % contained, record 49 h
  old) at 18 min and Willow (CO, 92 %, record 127 h old) at 17 min. Every other domain fire took
  under 30 s.
- The map read DEGRADED because LANDFIRE's LFPS answered *"Failure determining region from outline
  map service"* for VILAS (a new 0.01-acre report north of Chico, CA) on every cycle, so the fire was
  skipped. The banner printed LFPS's raw job messages across a third of the map.

## What changed

- **Forecast freshness tiers** (contracts `forecast_tiers` and its five limits; watch
  `--forecast-tiers`). A fire's forecast is kept between cycles and refreshed on these triggers:
  - its tier's limit, less 5 min: threat 15 min (a client at watch or above), growing 60 min, under
    control 6 h (every source at least 90 % contained, no growth since the forecast);
  - its geometry changing (perimeter, perimeter time, seeds);
  - the homes in range changing. This one refreshes inside the cycle, because the domain must cover
    them.

  Refreshes of a kept forecast run on one background worker, and the previous forecast is served
  until the new one lands, so a slow fire never holds the map. A refresh that cannot get its inputs
  is a provider error, and the kept forecast stays served. Each fire reports its forecast's age, tier,
  limit, refreshing and stale (`/api/state` incidents[].forecast and `stale_fires`, the world summary,
  and `/healthz` `fires_stale`).
- **Terrain fallback** (watch `--terrain-fallback`). When LFPS fails for a domain, the finest
  bundled LANDFIRE tile covering it is used (data/tiles, the same LF2024 fuel and canopy layers, at
  the tile's grid), noted per fire. With no covering tile the failure stands. VILAS's domain lies
  inside the bundled Paradise wide tile (240 m).
- **Banner.** One line: each failing service with the fires it hit (the services' own words on
  hover), the fires past their own limit, and a stalled loop. Each fire card shows "forecast N old
  (limit M)", red once past it.

Point-seeded fires (no perimeter) re-anchor at the cycle time, so a kept forecast of one is anchored
earlier: its arrivals are earlier, never later. A kept forecast's horizon ends up to its tier's limit
earlier than a fresh one's.

**Tests:** `tests/test_forecast_tiers.py`. Covered: off is the old behaviour; kept / refreshed by
limit; new homes in the cycle; new geometry in the background; a failed refresh keeps the forecast;
pruning; all three tiers and growth; the summary, healthz and page; the fallback and its fail-closed
edge; the monitor spreading on the fallback; the watch flags.
