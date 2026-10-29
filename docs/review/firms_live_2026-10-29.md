# NASA FIRMS on the live map: live mode and hotspot attachment (2026-10-29)

The founder, 2026-10-29: *"get the FIRMS key added and redeploy"*. The key is in the watcher's
environment (`FIRMS_MAP_KEY`, App Runner); the watcher runs `--fuse-hotspots`.

## What a live CONUS pull showed (2026-10-29 18:45 UTC)

- The key works: one CONUS area request per satellite answers in about 1 s.
- Two UTC days from the three VIIRS satellites: 7,287 detections (S-NPP 2,540, NOAA-20 2,523,
  NOAA-21 2,224); the last 24 h: **4,763 detections in 1,312 clusters** (2 km).
- **27 of the 4,763 detections lie on reported fires**: 5 of the 217 fresh reported fires (Skid
  Road, Kelsey, MUSE, South Tank Trail, Bee Haven). Nationally, late-October FIRMS is mostly
  agricultural, prescribed and industrial heat.
- 26 clusters lie within 60 km of the 520 demo homes (the client-search radius). Most are in the
  Sacramento Valley near Paradise and Chico, in the rice-straw burning season; one single-detection
  cluster is 1.0 km from a demo home. Each is a fire the engine forecasts and alerts on.
- With the clusters' ids taken from their centroids, 27 % of the ids changed within 3 h (a pass
  lands, an old detection ages out): a fire's alert pairs and kept forecast would restart.

## What the adapter got wrong, and the live mode

| Finding | Live mode (`FirmsFeed.live`, armed by `--fuse-hotspots`) |
|---|---|
| `DAY_RANGE 1` is **today in UTC** only (API docs: "from TODAY to TODAY - (DAY_RANGE-1)"): the layer emptied at 00:00 UTC (17:00 PDT) every day. | DAY_RANGE 2, kept to the last 24 h by the clock. |
| One satellite (NOAA-20): 2 passes a day. | S-NPP, NOAA-20 and NOAA-21 (URT+NRT): about 6. A failed satellite is a blind fetch (`last_fetch_ok=False`) that keeps the others' detections. |
| Ids from centroids (27 % changed in 3 h). | A cluster keeps the id of the previous fetch's cluster it shares the most detections with, else of an unclaimed previous cluster within 2 km; a fetch with a blind satellite forgets no id. |
| Clustering scanned every cluster for every detection (2.8 s on two days of CONUS; tens of seconds at the synthetic 6,000-detection day). | A grid over the centroids: the same clusters exactly (pinned against the plain scan on random data, every fallback), 0.02 s. |

## Hotspot attachment (contracts SAFETY SEMANTICS 39, `fuse_hotspots`)

`docs/LIVE_WORLD_PLAN.md` W6 named spatial fusion "the prerequisite for national FIRMS": a FIRMS
cluster has no IRWIN id and a synthetic name, so it never joined the fire it sat on, and one fire
near clients built a domain per 2 km cluster. After the staleness gate and the contained removal,
each detection now joins the reported fire whose footprint holds it:

- inside a kept fire's perimeter, or within `fuse_hotspots_perimeter_m` (1 km) of it;
- within min(10 km, max(2 km, 2 × the acreage's equivalent-circle radius)) + 1 km of a point-only
  fire's point (a reported point is often the origin, at the fire's edge);
- strictly inside the perimeter of a fire removed as contained: its own interior heat leaves the
  map with it, and heat outside its perimeter stays on the map as a FIRMS fire.

The nearest footprint wins. A joined detection becomes one of the fire's hotspots, which already
seeded the spread at the cycle time, and `firms` joins its sources. The rest of each cluster stays
a FIRMS fire under its own id. One cycle note counts the joins; `/api/state` and the world summary
carry `hotspot_rule` and each fire's satellite heat (`hotspots`), shown on its card.

On the 2026-10-29 data: 27 detections join 5 fires, and 5 clusters are wholly absorbed (0.08 s).

## Not done here

- **Screening.** Every cluster not on a reported fire is still a fire the engine forecasts and
  alerts on. A hotspot screening protocol (which heat counts as a potential fire) is an open owner
  decision: the candidate criteria, with their risks and benefits, went to the founder on
  2026-10-29.
- **The fire belief.** It learns only from perimeters and reported sizes. Hotspots as a third
  growth series would need calibrating against perimeters first.

**Tests:** `tests/test_hotspots.py` (35).
