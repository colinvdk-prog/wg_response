# WG Response — Weather + Terrain + Fuel Data Research Digest

**Date:** 2026-07-06 · **Task:** data sourcing for the fire-propagation model (wind/RH forecasts ≥72 h, slope/aspect grids ~30 m over tens-of-km operational windows, FBFM40 fuel → Rothermel parameters).
**Verification key:** ✅ = live-verified today with a real HTTP call (test point: Malibu, CA `34.0359,-118.6892`). ⚠️ = documented but NOT live-verified — treat as needing confirmation.

---

## 1. NWS API (api.weather.gov) — primary weather source

No API key. **A `User-Agent` header is REQUIRED** — recommended form `User-Agent: (app-domain, contact-email)`; NWS says it will eventually be replaced by an API key. Rate limit is **unpublished** ("allows a generous amount for typical use"); on exceeding it you get an error that clears "typically within 5 seconds" → retry with backoff. Honor `Cache-Control`/`Last-Modified`; do NOT cache-bust. All observed calls today succeeded with UA `(wg-response-recon, colinvdk@gmail.com)`.

### 1.1 points → gridpoints flow ✅

**Step 1 — resolve lat/lon to a WFO grid cell** (NDFD grid is 2.5 km):

```
GET https://api.weather.gov/points/{lat},{lon}
GET https://api.weather.gov/points/34.0359,-118.6892      ← verified
```

Response `properties` (verified values for Malibu):

| field | value |
|---|---|
| `gridId` / `cwa` | `LOX` |
| `gridX`, `gridY` | `139`, `47` |
| `forecast` | `https://api.weather.gov/gridpoints/LOX/139,47/forecast` |
| `forecastHourly` | `https://api.weather.gov/gridpoints/LOX/139,47/forecast/hourly` |
| `forecastGridData` | `https://api.weather.gov/gridpoints/LOX/139,47` |
| `forecastZone` | `https://api.weather.gov/zones/forecast/CAZ362` |
| **`fireWeatherZone`** | `https://api.weather.gov/zones/fire/CAZ362` ← key field for Red Flag alerting |
| `county` | `https://api.weather.gov/zones/county/CAC037` |

Cache the points result per client property at onboarding — the grid mapping is static.

### 1.2 Hourly forecast (human-friendly) ✅ — horizon = 156 h (6.5 days)

```
GET https://api.weather.gov/gridpoints/LOX/139,47/forecast/hourly
```

Verified today: **156 hourly periods**, first `2026-07-06T07:00-07:00` → last `2026-07-12T18:00-07:00`. Comfortably ≥ the 72 h alert requirement. Period shape (verified):

```json
{ "number": 1, "startTime": "2026-07-06T07:00:00-07:00", "endTime": "...08:00...",
  "temperature": 64, "temperatureUnit": "F",
  "relativeHumidity": {"unitCode": "wmoUnit:percent", "value": 97},
  "dewpoint": {"unitCode": "wmoUnit:degC", "value": 16.11},
  "windSpeed": "0 mph", "windDirection": "",
  "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 0},
  "shortForecast": "Patchy Fog" }
```

Parsing caveats: `windSpeed` is a **string** (`"0 mph"`, sometimes a range `"10 to 15 mph"`); `windDirection` is a compass string and **empty when calm**; **no gusts** in this endpoint. Fine for UI display, not ideal for the model → use 1.3.

### 1.3 Raw gridpoint data (model-grade) ✅ — horizon ≈ 191 h (~8 days)

```
GET https://api.weather.gov/gridpoints/{gridId}/{gridX},{gridY}
GET https://api.weather.gov/gridpoints/LOX/139,47                ← verified
```

Verified `validTimes: 2026-07-06T02:00:00+00:00/P7DT23H` (~7.96 days). Each variable is a **run-length-encoded time series**: `values[] = {"validTime": "2026-07-06T02:00:00+00:00/PT3H", "value": 18.52}` — the ISO-8601 duration (`PT1H`, `PT3H`, `PT6H`…) is how long the value holds. **You must expand these to an hourly array yourself** (durations get coarser in days 4–8).

Verified variables and units:

| property | uom | notes (verified counts today) |
|---|---|---|
| `windSpeed` | `wmoUnit:km_h-1` | 48 RLE segments over ~8 d |
| `windGust` | `wmoUnit:km_h-1` | 47 segments — **gusts only live here** |
| `windDirection` | `wmoUnit:degree_(angle)` | 62 segments, numeric degrees |
| `relativeHumidity` | `wmoUnit:percent` | 170 segments (≈hourly) |
| `temperature`, `dewpoint` | degC | |

Fire-weather bonus fields verified present on the same response: **`twentyFootWindSpeed` / `twentyFootWindDirection`** (the fire-behavior standard wind), `transportWindSpeed/Direction`, `mixingHeight`, `hainesIndex`, `redFlagThreatIndex` (in `hazards` group), `heatRisk`. Use 20-ft wind directly for Rothermel midflame-wind derivation instead of converting 10 m winds.

Convert km/h→mph/(m/s) in-app (unit conversion is on us; `?units=us` affects the `/forecast` endpoints, not the raw grid).

### 1.4 Red Flag / fire-weather alerts ✅

- By event, CONUS-wide: `GET https://api.weather.gov/alerts/active?event=Red%20Flag%20Warning` — verified today, 2 active RFWs returned. Also poll `event=Fire%20Weather%20Watch` and `event=Extreme%20Fire%20Danger`.
- By fire-weather zone (per client property): `GET https://api.weather.gov/alerts/active?zone=CAZ362` — verified (valid empty FeatureCollection). The zone id comes from `fireWeatherZone` in the points response.
- By point: `GET https://api.weather.gov/alerts/active?point=34.0359,-118.6892` — verified.
- By state: `.../alerts/active?area=CA`.
- Zone metadata: `GET https://api.weather.gov/zones/fire/CAZ362` → verified `{id: CAZ362, name: "Malibu Coast", type: "fire", state: "CA"}`.

Alert feature `properties` (verified shape): `event`, `severity` ("Severe"), `urgency` ("Expected"), `certainty` ("Likely"), `onset`, `ends`, `areaDesc`, `geocode.UGC[]` (zone list). ⚠️ Alert `geometry` is often `null` — map alerts to properties via UGC zone codes, not polygons.

**Recommended pattern:** at onboarding store each property's `(gridId, gridX, gridY, fireWeatherZone)`; poll `alerts/active?zone=` on the distinct zone set (deduped — many properties share a zone), every 2–5 min.

---

## 2. Open-Meteo — no-key fallback ✅ (licensing flag ⚠️)

Verified exact call:

```
GET https://api.open-meteo.com/v1/forecast?latitude=34.0359&longitude=-118.6892
    &hourly=relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m
    &forecast_days=7&wind_speed_unit=mph&timezone=America%2FLos_Angeles
```

Verified response: parallel arrays under `hourly` keyed by `time[]` (ISO8601, local tz), **168 hourly values (7 days)**; `hourly_units` echoed (`mp/h`, `%`, `°`); site `elevation` (7.0 m) included. `forecast_days` accepts up to **16**. Other useful params: `&models=best_match|gfs_seamless|ecmwf_ifs025` (HRRR blended into best_match for CONUS short range), `&past_days=N`, `&vapour_pressure_deficit` as an extra hourly variable (fire-relevant). No key, no User-Agent requirement, CORS-open.

**Licensing flag (important for WG):** the free endpoint is **non-commercial only** (10,000 calls/day, 300k/mo). Commercial use requires an API subscription → hostname changes to `https://customer-api.open-meteo.com/v1/forecast?...&apikey=KEY`. Pricing page fetched today reported "API Standard, 1M calls/month" (page rendered ≈$300/mo equivalent — ⚠️ number not confidently parsed, historically this tier was ~€29/mo; confirm at https://open-meteo.com/en/pricing before contracting). Self-hosting is allowed (AGPLv3 server; underlying data CC-BY-4.0, commercial use permitted with attribution). WG Response is commercial software → budget for the paid tier or self-host; do NOT ship on the free endpoint.

---

## 3. DEM / slope / aspect

### 3.1 Recommendation up front

For the propagation model over an operational window (tens of km): **get slope+aspect+elevation from the same LANDFIRE LFPS job that delivers fuel** (§4.2) — one request returns a single multiband 30 m GeoTIFF with fuel and topo **pre-aligned on the same grid**, which eliminates the resample/registration step entirely. Verified end-to-end today. Use the options below for point lookups, redundancy, or higher resolution.

### 3.2 USGS 3DEP EPQS — point elevations ✅

```
GET https://epqs.nationalmap.gov/v1/json?x=-118.6892&y=34.0359&units=Meters&wkid=4326&includeDate=false
→ {"value":"5.756093025","resolution":1, ...}    ← verified (1 m source resolution)
```

Best-available source at the point (1 m where lidar exists). Single-point only, ~100–300 ms/call — fine for spot checks (e.g., a property's elevation at onboarding), useless for grids.

### 3.3 USGS 3DEP dynamic ImageServer — server-side slope/aspect grids ✅

`https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer` — verified: F32 single band, **maxImageWidth/Height 8000×8000**, WMS+WCS enabled, and built-in raster functions including **`Slope Degrees`**, **`Aspect Degrees`**, `Slope Map`, `Aspect Map`, `Hillshade Gray`. Verified export (returns an href to a real GeoTIFF):

```
GET .../3DEPElevation/ImageServer/exportImage
    ?bbox=-118.75,34.02,-118.60,34.12&bboxSR=4326&size=500,333&imageSR=4326
    &format=tiff&pixelType=F32
    &renderingRule={"rasterFunction":"Slope Degrees"}&f=json
→ {"href":"https://elevation.nationalmap.gov/arcgis/rest/directories/elevation_output/.../_ags_....tif", ...}
```

So a 30 km window at 30 m is a 1000×1000 request — one call each for slope and aspect, no local DEM math needed. ⚠️ Caveat: slope is computed on the service's *visualization* mosaic; for auditable numbers prefer computing from 3.4 or using LANDFIRE SlpD (which is what fire tools use anyway).

### 3.4 Static 3DEP GeoTIFFs on public S3 — no key ✅

1°×1° seamless tiles, HTTP-range-readable (COG-style access works with rasterio/GDAL `/vsicurl/`):

```
10 m: https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n35w119/USGS_13_n35w119.tif   ← HEAD 200 verified
30 m: https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/n35w119/USGS_1_n35w119.tif     ← HEAD 200 verified
```

Tile naming: `n{ceil(lat)}w{ceil(|lon|)}` (tile covers 1° south/east of the name). Compute slope/aspect locally with `gdaldem slope/aspect`, `richdem`, or `xarray-spatial` (Horn's method — identical to what LANDFIRE uses). This is the best **offline/cached** path: pre-download tiles for all client regions (~50–400 MB per 1° tile at 10 m).

### 3.5 Other options

- **py3dep 0.19.0** (PyPI, verified exists): `py3dep.get_dem(geom, resolution)` — convenience wrapper over the 3.3 service; pulls heavy deps (rioxarray). Fine for prototyping, unnecessary in production.
- **AWS Terrain Tiles (terrarium)** ✅: `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` — HEAD 200 verified (z12 Malibu tile). Elevation = `(R*256 + G + B/256) − 32768` m. No key, free, fast web-mercator tiles; mixed sources (3DEP/SRTM); good for map UI hillshade, second choice for modeling.
- **Open-Elevation** ✅ works (`https://api.open-elevation.com/api/v1/lookup?locations=34.0359,-118.6892` → 8.0 m) but is a third-party box with no SLA and coarse SRTM data — do not build on it.
- **OpenTopography SRTM/COP30 API** ⚠️: requires a free API key, global rasters via one GET — only relevant if WG ever operates outside CONUS.

---

## 4. LANDFIRE fuel (FBFM40) — non-GEE paths ✅

Context: the internal firerisk repo already samples FBFM40 via Google Earth Engine and separately has a **live-verified `getSamples` recipe** (see memory `firerisk_vegetation_improvement.md` "REUSABLE RECIPES"). Everything below is GEE-free and verified today for a standalone WG Response service. Native data: **30 m**, CONUS Albers (`wkid 5070`), thematic uint codes.

### 4.1 Direct ImageServer (points + small grids, synchronous) ✅

Catalog: `https://lfps.usgs.gov/arcgis/rest/services?f=json` → folders `Landfire_LF2016 … Landfire_LF2025`, `Landfire_LF2025_Seasonal_Fuels_2026`, `Landfire_Topo`, plus `LandfireProductService (GPServer)`.

- **Per-point/per-transect sampling** (verified): 
  ```
  GET https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2024/LF2024_FBFM40_CONUS/ImageServer/getSamples
      ?geometry={"xmin":-118.70,"ymin":34.03,"xmax":-118.68,"ymax":34.05,"spatialReference":{"wkid":4326}}
      &geometryType=esriGeometryEnvelope&sampleCount=8&f=json
  ```
  Returns clean raw FBFM40 codes with 30 m resolution flag — verified values today: `91` (NB1 urban), `102` (GR2), `122` (GS2), `183` (TL3). `identify` also works per-pixel. **`computeHistograms` returns binned/stretched values — unusable** (known from internal work, still true).
- **Grid export** (verified): `exportImage?bbox=-118.75,34.02,-118.60,34.12&bboxSR=4326&size=556,371&imageSR=4326&format=tiff&f=json` → href to a GeoTIFF of raw codes. Server reports pixelType S16, pixelSize 30, maxImageWidth/Height **100000×100000** — a whole operational window in one synchronous call is fine.

### 4.2 LANDFIRE Product Service (LFPS) job API — RECOMMENDED for windows ✅

**The old GPServer submit path is DEAD** (`.../LandfireProductService/GPServer/.../submitJob` now serves an HTML SPA — verified today). The current API (verified end-to-end today, no key, no auth):

```
1) Submit:
GET https://lfps.usgs.gov/api/job/submit
    ?Layer_List=LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev
    &Area_of_Interest=-118.75 34.02 -118.60 34.12        ← "W S E N", space-separated, WGS84
    &Email=colinvdk@gmail.com                             ← optional
→ {"jobId":"c7bffbb9-...","status":"Pending","job":{"aoiSize":37962,"geoArea":"SW",...}}   (aoiSize ≈ acres)

2) Poll:
GET https://lfps.usgs.gov/api/job/status?JobId=c7bffbb9-b3e1-4d7e-a2c7-02b217c87119
→ status: Pending → Executing → Succeeded, messages[], and on success:
   outputFile: https://lfps.usgs.gov/arcgis/rest/directories/arcgisjobs/landfireproductservice_gpserver/<job>/scratch/<job>.zip

3) Download the zip.
```

Verified result for the 13.9 km × 11.1 km Malibu test AOI: job Succeeded in **~60–90 s**; zip (735 KB) contains one **4-band GeoTIFF** + `.tfw` + `.aux.xml` with band descriptions `LF2024_FBFM40_CONUS`, `LF2020_SlpD_CONUS`, `LF2020_Asp_CONUS`, `LF2020_Elev_CONUS` — i.e., **fuel + slope(deg) + aspect + elevation, 30 m, one aligned raster** (local Albers projection by default; `Output_Projection` param available, `Resample_Resolution` ≥30 optional). Wrong layer names fail fast with `ERROR: Invalid products` in the status messages (~1.3 s), so validate names against 4.3.

Other job params (from the GPServer task schema, still authoritative): `Output_Projection`, `Resample_Resolution` (int >30), `Edit_Rule` (JSON — can post-edit layers by rule), `Edit_Mask`. Docs: `https://lfps.usgs.gov/LFProductsServiceUserGuide.pdf`. ⚠️ Max AOI size per job not verified (test was ~38 k acres; the user guide states limits — check before requesting >~1° boxes).

### 4.3 Product name discovery ✅

```
GET https://lfps.usgs.gov/api/products      → {"products":[...]}  (136 products, verified)
```

Load-bearing layer names (verified in the list): fuel `LF2024_FBFM40` (also `LF2025_FBFM40`, `LF2023_...`, `LF2022_...`, `LF2016_...`), **2026 seasonal fuels `LF2025_FBFM40_SP26` / `_SU26` / `_ES26`** (spring/summer/early-season 2026 — most current operational fuel; use these for live response), Anderson `LF2024_FBFM13`, canopy `LF2024_CC` / `CH` / `CBD` / `CBH` (needed if the model adds crown-fire), topo `LF2020_Elev` / `LF2020_SlpD` (slope degrees) / `LF2020_SlpP` / `LF2020_Asp`.

### 4.4 FBFM40 code namespace (for decoding rasters/samples)

Non-burnable **91–99**: 91 NB1 urban, 92 NB2 snow/ice, 93 NB3 agriculture, 98 NB8 water, 99 NB9 barren. Burnable: **GR1–9 = 101–109** (grass), **GS1–4 = 121–124** (grass-shrub), **SH1–9 = 141–149** (shrub/chaparral), **TU1–5 = 161–165** (timber-understory), **TL1–9 = 181–189** (timber litter), **SB1–4 = 201–204** (slash/blowdown). Burnable mask: `~((code>=91)&(code<=99))` — note all codes are ≥91 (internal repo previously had a `<91` bug; don't repeat it).

### 4.5 FBFM40 → Rothermel parameters

Authoritative table: **Scott & Burgan 2005, RMRS-GTR-153** — per-model: fuel load by class (1-h/10-h/100-h dead + live herbaceous + live woody, tons/ac), SAV ratios (1-h dead: model-specific; 10-h: 109 ft⁻¹; 100-h: 30 ft⁻¹), fuel-bed depth (ft), dead-fuel moisture of extinction (%), heat content (8,000 Btu/lb standard), and the **dynamic** flag (GR/GS/SH + TU1/TU3: live-herb load transfers to dead as herb cures — must be implemented, this is what makes cured grass explosive). PDF: `https://www.fs.usda.gov/rm/pubs_series/rmrs/gtr/rmrs_gtr153.pdf`; quick-reference: `https://gacc.nifc.gov/oncc/docs/40-Standard%20Fire%20Behavior%20Fuel%20Models.pdf`.

Machine-readable / library options (avoid hand-typing 40×~10 numbers):
- **`pyretechnics` 2026.5.15 (PyPI, verified)** — SIG's fire-behavior library: ships the S&B fuel-model table plus Rothermel surface spread, crown fire, and elliptical spread over rasters. **Strong candidate to BE the propagation core**, not just the parameter table — evaluate first.
- **`pyrothermel` 0.1.4 (PyPI, verified)** — Python bindings to the USFS Behave C++ engine (Rothermel + extensions), fuel models included.
- **`firestarter-io/fuelmodels`** (GitHub, ⚠️ not fetched today) — JSON/TS constants for Anderson 13 + S&B 40; handy if the engine is TypeScript.
- R `firebehavioR` (`SFM_metric` table) if metric units are wanted for cross-checking.

### 4.6 Currency caveat (operational)

LF2024 fuels describe the ~2024 landscape (LF2025 seasonal = 2026 conditions). Fuel inside **any perimeter that burned after the vintage** must be de-rated (regrowth window → GR1-like). The internal firerisk repo already has this exact logic (`landfire.py::recent_fire_year` + `reconcile_disturbed_fuel`, CAL FIRE perimeters `YEAR_ > vintage`) — port it. Bulk alternative to LFPS for self-hosting: full CONUS mosaics from `https://landfire.gov` Data downloads (multi-GB; unnecessary given LFPS works). ⚠️ Old GeoServer WCS (`edcintl.cr.usgs.gov`) not tested — assume deprecated.

---

## 5. Suggested architecture for the propagation model's data layer

1. **Onboard a client property** → one `points/{lat,lon}` call (cache `gridId,gridX,gridY,fireWeatherZone`), one EPQS spot elevation (optional).
2. **On fire detection near clients** → define the operational window (e.g., 30–50 km box covering fire + threatened properties) → **one LFPS job** (`LF2025_FBFM40_SU26;LF2020_SlpD;LF2020_Asp;LF2020_Elev` + `LF2024_CC;LF2024_CBD;LF2024_CBH` if crown fire modeled) → aligned 30 m stack in ~1–2 min; cache per window; apply burned-perimeter de-rate.
3. **Weather grid** → for each distinct NWS 2.5 km cell touching the window (dedupe via `points`), pull `forecastGridData`, RLE-expand `windSpeed/windGust/windDirection/relativeHumidity/twentyFootWindSpeed` to hourly UTC arrays out ~191 h; refresh hourly (respect `Last-Modified`). Fallback per-cell to Open-Meteo (`customer-api` once licensed) on NWS failure.
4. **Alert triggers** → poll `alerts/active?zone={fireWeatherZone}` (deduped zone set) + CONUS `event=Red Flag Warning` sweep every 2–5 min; RFW in a client's zone escalates watch posture independent of propagation ETA.
5. All four upstream sources are keyless today; the only licensing landmine is Open-Meteo commercial use (§2).

## 6. Open items / not verified

- Open-Meteo paid-tier price + call quota — page parse ambiguous ($300/mo? historic €29/mo); confirm before relying on the fallback in production.
- LFPS max AOI acreage per job and any job-rate throttling (helpdesk: `helpdesk@landfire.gov`; user guide PDF).
- NWS hard rate-limit number (unpublished by design) — engineer for 429-retry + caching regardless; NWS has known multi-hour outage history, so the Open-Meteo fallback is not optional.
- `firestarter-io/fuelmodels` contents not inspected today; RMRS-GTR-153 table values not spot-checked against any library — do a 3-model spot check (GR2, SH5, TL3) when the table lands in code.
- 3DEP `Slope Degrees` renderingRule output was not numerically compared against LANDFIRE `LF2020_SlpD` — if both are used anywhere, reconcile once.
