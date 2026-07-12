# Routing + live-traffic options for crew drive-time (real roads)

**Date:** 2026-07-10 · **Requested by:** founder (research request) · **Scope:** web research only —
no product code, config, or the :8878 server was touched.
**Question:** *"Is there a plugin or online tool that can be imported to help determine crew and drive
time so it is on real roads with LIVE TRAFFIC data?"*

**Context this feeds:** `docs/DISPATCH_REVIEW.md` item 1 — the planner still prices every leg as a
straight line (`travel.travel_time_s` = haversine × circuity 1.35 × fire_traffic 1.4 ÷ 17.9 m/s),
optimistic on 76% of legs, mean 2.43× in Paradise; measured road circuity is 1.96–2.43, not 1.35.
Real OSM road graphs + A* already exist (`wgresponse/roads.py`) but the *planner* still uses tier-0,
and neither tier-0 nor `roads.py` has any traffic. This doc evaluates what to import to fix both.

---

## 0. TL;DR recommendation

**Two tiers, behind one `TravelProvider` seam, tier-0 stays the offline default.**

1. **Real roads NOW → self-host Valhalla** (Docker + a Geofabrik California extract). Gives real
   road-network matrices via one `/sources_to_targets` call, `$0` per-element cost, no cloud
   dependency, and — uniquely — **`exclude_polygons`** so the *live fire footprint* structurally
   removes closed roads from the matrix. This is the single highest-value, lowest-risk upgrade and it
   fixes the 2.43× circuity gap `DISPATCH_REVIEW` flagged. OSRM is the faster-matrix alternative but
   has no polygon exclusion; ORS's `avoid_polygons` **does not apply to its matrix** endpoint.
2. **Live traffic LATER (optional, online only) → TomTom Matrix Routing v2 or HERE Matrix v8**, not
   Google. Both carry live **and** historical/predictive traffic in the matrix; both are ~5–15× cheaper
   than Google at this workload because they bill *discounted transactions*, not raw elements. HERE also
   supports **polygon avoid areas** (fire footprint) in the cloud. Google Routes is the most accurate but
   the most expensive per element and **cannot avoid an arbitrary polygon** (tolls/highways only). Mapbox's
   live-traffic profile is capped at **25 coordinates**, which makes a 20×200 matrix impractical.
3. **Honest caveat (matters):** a live-traffic ETA models *normal* congestion, **not** fire-driven road
   closures or evacuation surge/contraflow. That still needs (a) fire-footprint exclusion
   (Valhalla `exclude_polygons` / HERE polygon avoid) and (b) a surge multiplier — which the engine's
   `fire_traffic_factor` (1.4) already gestures at and should be **kept even with live traffic on**.

**Python import path:** `routingpy` (one PyPI lib, consistent matrix interface across **Valhalla (local),
HERE, Google, Mapbox, OSRM, ORS, GraphHopper**). TomTom and Azure are *not* in routingpy → plain REST
(`requests`) or their own SDKs. This maps almost 1:1 onto the `TravelProvider` seam below.

---

## 1. The workload (used for every cost figure below)

- **1 matrix** = 20 crews (origins) × 200 homes (destinations) = **4,000 origin–destination pairs**.
- **Cadence** = re-matrixed every 15 min (matches `cfg.replan_period_s` = 900 s) = 4/hr = **96/day**.
- **Billed units differ by provider** — this is the whole cost story:
  - Google, Mapbox, HERE bill **per element** → **4,000 units/matrix**, **384,000/day**.
  - TomTom bills **discounted transactions**: when both origins and dests > 5, `max(o,d) × 5` =
    `200 × 5` = **1,000 txn/matrix**, **96,000/day**.
  - Azure bills **1 transaction per 4 cells** (round up): `4,000/4` = **1,000 txn/matrix**, **96,000/day**.
- **Reality check:** this only runs during an *active incident*, so real spend is **per-incident-day**,
  not a flat month. A continuous-30-day figure is the worst case and is shown only to bound it.

---

## 2. Comparison table (verified 2026 pricing unless flagged)

| Provider | Live traffic? | Matrix support | ~Cost at the workload | Commercial / caching terms | Python import |
|---|---|---|---|---|---|
| **Self-host Valhalla** | **No** (historical "predicted" CSV + optional live `traffic.tar` **if you supply data**) | **Yes** — `/sources_to_targets`, unlimited size, **`exclude_polygons`** for fire footprint | **~$0** per matrix; server only (~$50–300/mo EC2) | You own it. OSM data = **ODbL** (attribute OSM; share-alike only if you *distribute* the DB — internal dispatch is fine) | **`routingpy`** (`Valhalla` w/ local `base_url`), or raw REST |
| **Self-host OSRM** | **No** (CSV speed updates only, not live) | **Yes** — `/table`, fastest matrices, caps ~10k durations/req | **~$0**; server only | **BSD-2-Clause** engine + ODbL data; fully commercial | `routingpy` (`OSRM`), or raw REST |
| **Self-host ORS** | **No** | Yes — many-to-many; **`avoid_polygons` NOT honored by matrix** (directions only) | **~$0**; server only | Apache-2.0 engine + ODbL data | `routingpy` (`ORS`) / `openrouteservice-py` |
| **Google Routes** (Compute Route Matrix **Pro**) | **Yes** — `TRAFFIC_AWARE` / `TRAFFIC_AWARE_OPTIMAL`, live + predictive | Yes — **max 625 elem/req**, **100 elem/req** in `TRAFFIC_AWARE_OPTIMAL`; 3,000 EPM rate cap | **~$3.3k/incident-day → ~$24k/mo** continuous (Pro **$10/1k** →$8→$6→$3→$0.75 tiered) | Strictest: **no permanent caching** of results (limited temp caching only); attribution/display rules; no building competing services | `routingpy` (`Google`) or `googlemaps` SDK |
| **Mapbox Matrix** | **Yes** — `driving-traffic` profile (live) | Yes but **`driving-traffic` capped at 25 coords** → live matrices max ~625 elem; heavy tiling for 20×200 | **~$0.77k/incident-day → ~$23k/mo** ($2/1k after 100k free/mo) | Commercial OK; temporary caching allowed | `routingpy` (`MapboxOSRM`) or `requests` |
| **HERE Matrix v8** | **Yes** — real-time **and** historical, in Base Plan | **Yes** — built for large fleet matrices; **`avoid[areas]` supports polygons** (≤250) for fire footprint | **Quote-based**; illustratively **~$270/incident-day → ~$8k/mo** (~$0.5–0.8/1k routing) | Fleet-friendly; caching of results within your app generally permitted for contract term | `routingpy` (`HereMaps`) or `requests` |
| **TomTom Matrix v2** | **Yes** — live **and** historical (`departAt=now`) | **Yes** — discounted txn `max(o,d)×5`; **`avoidAreas` = rectangles only** | **~$47/incident-day → ~$1.5k/mo** (~$0.50/1k txn; **2,500 free/day**) — **cheapest with live traffic** | Commercial OK; caching within your app for service term | **Not in routingpy** → `requests` / TomTom SDK |
| **Azure Maps** (Route Matrix, **Gen2**) | **Yes** | Yes — 1 txn per 4 cells | **~$430/incident-day → ~$13k/mo** ($4.50/1k Gen2) | Commercial under Azure agreement; **⚠ moving target** | `azure-maps-route` SDK / `requests` |

**Notes / caveats on the numbers**
- Google/Mapbox/Azure per-1k rates are **verified 2026** (Google official SKU list; Azure Gen2 $4.50/1k).
- **HERE and TomTom exact per-1k are quote/tier-based and not cleanly public** — the dollar figures for
  them are *illustrative order-of-magnitude* from their published models + a third-party matrix-cost
  comparison, and should be confirmed with a sales quote. TomTom's per-1k also **changes on 1 Jul 2026** —
  re-verify. HERE raised prices **~6% on 1 Apr 2026**.
- **Azure ⚠:** the **Gen1 pricing tier retires 15 Sep 2026** (forced migration to Gen2, reported as a
  ~9× increase for Gen1 customers) and **Route v1 is deprecated** (retires 31 Mar 2028; migrate to Route
  `2025-01-01`). Two migrations in flight = avoid for a new build unless already in the Azure ecosystem.

---

## 3. Per-provider detail

### Self-host — Valhalla (recommended real-roads tier)
- **Matrix:** `POST localhost:8002/sources_to_targets` returns the full O×D time/distance table in one call.
- **Fire footprint:** **`exclude_polygons`** (nested JSON rings) — *all roads intersecting the polygons
  are removed from routing*, per request, with no graph rebuild. This is exactly the "roads closed by the
  fire" case and is the strongest fire feature of any option here. `roads.py` already anticipates this in
  its upgrade note. Belt-and-braces with the existing grid-corridor safety check.
- **Traffic:** two kinds. **Historical / "predicted"** via a CSV of encoded weekly speeds per edge
  (`edge_id,freeflow_speed,constrained_speed,historical_speeds`); **live** via a memory-mapped `traffic.tar`
  you update — but **you must supply the traffic data yourself** (no feed ships with it). So Valhalla alone
  is *real roads, no live traffic* unless paired with a feed.
- **Ops:** Docker image + a Geofabrik `california-latest.osm.pbf` extract; runs on the same box → keeps the
  offline/dep-free discipline (network call is to `localhost`).

### Self-host — OSRM / ORS
- **OSRM** `/table` gives the fastest matrices, **BSD-2-Clause**, custom speeds via Lua profile + CSV — but
  **no live traffic** and **no polygon exclusion**. The public demo server is **dev-only** (already noted in
  `travel.py`); production must self-host.
- **ORS** (GraphHopper-based) has a matrix, but `avoid_polygons` is a *directions*-only param and
  **does not affect the matrix** endpoint (community-confirmed) — so it's a poor fit for the fire case.

### Google Routes API (Compute Route Matrix)
- **Verified SKUs:** Essentials **$5/1k** elem (10k free/mo) — *no traffic*; **Pro $10/1k** →$8→$6→$3→$0.75
  tiered (5k free/mo) — Pro is what `TRAFFIC_AWARE` / `TRAFFIC_AWARE_OPTIMAL` bills as.
- **Limits:** 625 elem/request (100 with `TRAFFIC_AWARE_OPTIMAL`), 3,000 elements-per-minute cap → a
  4,000-pair matrix is 7 requests (40 in optimal mode) and ~1.3 min under the rate cap; fine at 15-min cadence.
- **Fire:** **no arbitrary-polygon avoidance** — only avoid tolls/highways/ferries. Weakest for closures.
- **ToS risk:** strict no-permanent-caching + display/attribution rules; most expensive per element here.

### Mapbox Matrix
- **$2/1k** after 100k free/mo; live traffic via `driving-traffic`. The killer limit: **`driving-traffic`
  caps at 25 coordinates total** (10 by default), so a live-traffic 20×200 matrix must be tiled into dozens
  of ≤625-element sub-matrices — awkward and rate-limited. Fine for small crews, poor at 200 homes.

### HERE Matrix Routing v8
- Purpose-built for **fleet/logistics** many-to-many; strongest cloud fit after cost. **Real-time +
  historical** traffic in the Base Plan. **`avoid[areas]` supports polygons** (≤250) → can exclude the fire
  footprint in the cloud (only cloud provider that does this well). Pricing is **quote/volume-tiered**
  (free allowance a few thousand/mo; +6% since 1 Apr 2026) — get a quote before relying on the ~$8k/mo est.

### TomTom Matrix Routing v2
- **Best value with live traffic.** Live **and** historical traffic (`departAt`), and the **discounted
  transaction** rule (`max(o,d)×5`) means a 20×200 matrix costs **1,000 txn, not 4,000** — ~$47/incident-day
  at ~$0.50/1k with **2,500 free/day**. `avoidAreas` is **rectangles only** (bbox around the fire, not a true
  polygon). Not in routingpy → REST. Re-verify the per-1k after the **1 Jul 2026** price change.

### Azure Maps
- Route Matrix bills **1 txn / 4 cells** (like TomTom's discount), Gen2 **$4.50/1k**, traffic supported.
  Avoid = rectangles. **Skip for a new build** due to the Gen1→Gen2 (15 Sep 2026) + Route v1→v2025
  double migration noted above.

### Other worth noting
- **GraphHopper** (self-host or cloud, in routingpy), **NextBillion.ai** (commercial, large matrices),
  **TravelTime** (flat-fee unlimited elements — good if element counts explode). None beat the
  Valhalla-for-roads + TomTom/HERE-for-traffic split for this use case.

---

## 4. The fire / evacuation angle (why a plain ETA is wrong)

A normal-traffic ETA is *optimistic* the moment the fire starts closing roads. Two things the engine needs
that no "live traffic" feed gives on its own:

1. **Exclude the fire footprint from routing.** Ranked by capability:
   **Valhalla `exclude_polygons`** (true polygons, works in the matrix) > **HERE `avoid[areas]`** (polygons,
   cloud) > **TomTom / Azure `avoidAreas`** (rectangles only) > **Google** (none) ≈ **ORS matrix** (ignored).
2. **An evacuation-surge multiplier.** Live traffic shows commuters, not a mass egress under contraflow.
   The engine's `fire_traffic_factor` (1.4) is the right knob for this and **should stay on even when a live
   feed is wired in** — live traffic and the surge factor are additive, not redundant.

So the fire realism win comes primarily from **Valhalla `exclude_polygons` fed the live fire polygon**, not
from any traffic API. Live traffic is a second-order refinement for the non-burning parts of the network.

---

## 5. Recommendation

- **Ship first (real roads, offline, free):** self-host **Valhalla**, wire the planner's
  `travel_matrix_s` to `/sources_to_targets`, and pass the current fire polygon as `exclude_polygons`.
  Closes `DISPATCH_REVIEW` item 1 (2.43× circuity) *and* the fire-closure gap in one move, with no
  per-element cost and no cloud dependency.
- **Add when online + budget justifies (live traffic):** **TomTom Matrix v2** for best cost/live-traffic, or
  **HERE Matrix v8** if you also want cloud polygon-avoid and fleet-grade SLAs. **Not Google** for the
  matrix workload (5–15× the cost, no polygon avoid); **not Mapbox** for live traffic at 200 homes
  (25-coord cap); **not Azure** for a new build (migration churn).
- **Keep tier-0 as the default and the fallback.** It stays the dep-free offline baseline; the providers
  above are opt-in behind config and must degrade to tier-0 on any error/timeout.

---

## 6. wgresponse integration sketch

Add a `TravelProvider` seam *behind* the existing `travel.travel_time_s` / `travel_matrix_s` contract, so
`dispatch.plan` and `roads.py` are unchanged and tier-0 remains the default.

```python
# wgresponse/travel_provider.py  (new; travel.py keeps its current seam + becomes Tier0Provider)
from typing import Protocol, Optional, Sequence

class TravelProvider(Protocol):
    name: str
    def matrix_s(self, origins, destinations, cfg, *,
                 depart_at: Optional[float] = None,          # epoch s; None -> now/free-flow
                 exclude_polygons: Optional[Sequence] = None  # fire footprint rings
                 ) -> list[list[float]]: ...
    def time_s(self, a, b, cfg, *, depart_at=None, exclude_polygons=None) -> float: ...

class Tier0Provider:            # wraps today's haversine model — the DEFAULT, offline, dep-free
    name = "tier0"
    def matrix_s(self, o, d, cfg, **_): return travel.travel_matrix_s(o, d, cfg)
    ...

class ValhallaProvider:         # localhost:8002 /sources_to_targets; passes exclude_polygons
    name = "valhalla"           #   import via routingpy (Valhalla, base_url=local) or requests
    ...

class TomTomProvider / HereProvider:   # live traffic (departAt); REST (routingpy for HERE)
    ...

def get_provider(cfg) -> TravelProvider:
    return _REGISTRY.get(cfg.travel_provider, Tier0Provider())
```

**Config (contracts.ResponseConfig), all defaulting to today's behavior:**
- `travel_provider: str = "tier0"`  → `"valhalla" | "tomtom" | "here" | "google" | "mapbox"`.
- `travel_endpoint_url: str = ""`    → e.g. `http://localhost:8002` for Valhalla.
- `travel_use_live_traffic: bool = False`.
- **API keys come from env / Secrets Manager, never `cfg` and never committed** (mirror the engine's
  existing secret discipline). `circuity`, `fire_traffic_factor`, `road_speed_mps` stay as the tier-0
  knobs *and* as the offline fallback; `fire_traffic_factor` remains applied even under live traffic.

**Offline fallback (preserves the dep-free discipline):** every non-tier-0 provider wraps its call in
try/except-with-timeout and **falls back to `Tier0Provider` on any error, timeout, or missing key**,
logging loudly — the dispatch loop never fails because a cloud call did. Network is reached *only* when a
provider is explicitly configured and a key is present.

**Cost/robustness lever (already hinted in `travel.py`):** cache the crew×property matrix and refresh a
crew's row only when it moves > 1 km from where its row was computed. This both cuts per-element billing at
the 15-min cadence and shrinks the rate-limit/latency exposure. With Valhalla self-host it also means most
ticks never leave the box.

---

## 7. Key risks

- **Per-element cost at re-matrix cadence.** Google/Mapbox bill raw elements → $23–24k/mo continuous worst
  case. TomTom/Azure/Valhalla avoid this (discounted txn / self-host). The matrix-row cache is the mitigation.
- **Cloud dependency breaks the current offline/dep-free discipline.** Hence tier-0 default + hard fallback,
  and Valhalla-on-localhost as the "real roads without leaving the box" middle ground.
- **Secrets handling & rate limits.** Keys in Secrets Manager only; respect Google 3,000 EPM, Mapbox
  25-coord, TomTom/HERE quotas; back off to tier-0 on 429.
- **ToS/caching.** Google forbids permanent caching of route results — don't build a stored travel-time DB
  from Google. Self-host (ODbL attribution) has none of this.
- **Pricing is a moving target in 2026.** TomTom (1 Jul 2026), HERE (+6% 1 Apr 2026), Azure Gen1→Gen2
  (15 Sep 2026). Re-quote before committing. HERE/TomTom exact per-1k here are illustrative pending a quote.
- **Live traffic ≠ fire behavior.** It won't see closures/contraflow; keep `exclude_polygons` + the surge
  factor. This is the load-bearing caveat for the whole feature.

---

## 8. Sources

- Google Routes API pricing (SKU list, verified 2026): https://developers.google.com/maps/billing-and-pricing/pricing
- Google Routes usage & billing (element caps, traffic-aware SKU): https://developers.google.com/maps/documentation/routes/usage-and-billing
- Google Compute Route Matrix: https://developers.google.com/maps/documentation/routes/compute_route_matrix
- Mapbox Matrix API (limits, driving-traffic): https://docs.mapbox.com/api/navigation/matrix/ · pricing https://www.mapbox.com/pricing
- Mapbox real-time traffic via Matrix (25-coord note): https://blog.afi.io/blog/real-time-traffic-with-the-mapbox-api/
- HERE Matrix Routing v8 intro: https://www.here.com/docs/bundle/matrix-routing-api-developer-guide/page/README.html
- HERE pricing / free tier / 6% increase: https://placematic.com/here-technologies-api-pricing/ · https://www.here.com/get-started/pricing
- HERE polygon avoid areas: https://www.here.com/learn/blog/routing-supports-polygons-for-avoid-areas
- TomTom Matrix v2 discounted transaction billing: https://developer.tomtom.com/matrix-routing-v2-api/documentation/discounted-transaction-billing
- TomTom Matrix v2 (live/historical, departAt): https://developer.tomtom.com/matrix-routing-v2-api/documentation/synchronous-matrix · pricing https://developer.tomtom.com/pricing
- Azure Maps pricing / Gen2 $4.50 / transaction counting: https://azure.microsoft.com/en-us/pricing/details/azure-maps/ · https://learn.microsoft.com/en-us/azure/azure-maps/understanding-azure-maps-transactions
- Azure Maps Gen1 retirement (15 Sep 2026): https://www.globenewswire.com/news-release/2026/06/22/3315226/0/en/azure-maps-gen1-pricing-customers-to-be-migrated-to-gen2-pricing-in-september-2026-a-9x-cost-increase.html
- Valhalla Matrix API: https://valhalla.github.io/valhalla/api/matrix/api-reference/ · traffic https://valhalla.github.io/valhalla/mjolnir/historical_traffic/ · traffic PoC https://github.com/alinmindroc/valhalla_traffic_poc
- OSRM traffic (no live; CSV speeds), BSD license: https://github.com/Project-OSRM/osrm-backend/wiki/Traffic
- Self-hosted engines compared (2026): https://www.pistack.xyz/posts/2026-04-25-graphhopper-vs-osrm-vs-valhalla-self-hosted-routing-engines-guide-2026/
- ORS avoid_polygons not honored by Matrix: https://ask.openrouteservice.org/t/avoid-polygons-not-working-with-matrix-api-for-multiple-locations/7579
- routingpy (unified Python client): https://pypi.org/project/routingpy/ · https://github.com/nilsnolde/routingpy
- Distance-matrix provider cost comparison: https://traveltime.com/blog/distance-matrix-api-pricing-comparison
