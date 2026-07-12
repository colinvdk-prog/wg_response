# WG Response — Live Fire-Incident Data Feeds (Research Digest)

**Date:** 2026-07-06 · **Researcher:** recon subagent · **Status:** all endpoint claims below were live-verified today unless flagged ⚠️ UNVERIFIED.

Purpose: feed inventory for the WG Response coordination engine (72h advance alert → ongoing watch → 24h EXTREME → GraphGel dispatch with 30-min crew-clear safety rule). The alerting pipeline needs three signal classes: **(a) incident metadata** (new fire, name, location, containment), **(b) perimeter polygons** (for arrival-time projection), **(c) hotspot growth signal** (satellite detections between perimeter updates).

---

## 1. Watch Duty (client-requested PRIMARY source)

Operator: **Sherwood Forestry Service, Inc.**, a 501(c)(3) nonprofit. Nationwide coverage since **Dec 1, 2025** (expanded from 22 states); flood incidents added June 2026. Human reporters (largely ex-fire/dispatch volunteers) monitor scanner traffic, dispatch, aircraft — updates post within minutes of radio traffic, i.e. typically **faster than any official feed** for new-start intel.

### 1.1 Official access tiers (verified 2026-07-06)

| Tier | Price | What you get |
|---|---|---|
| Free app | $0 | incidents, perimeters, evac zones, push alerts |
| Membership | from ~$25/yr | supporter tier |
| **Watch Duty Pro** | **$79 first yr / $99 after** | firefighter/responder tier: real-time firefighting-aircraft tracker, custom GIS layers, weather insights, satellite detections |
| **Enterprise ("Professional Solutions")** | contact sales | org-wide platform: human-verified GIS intelligence, custom GIS layer imports, infrastructure overlays, **ArcGIS Online/Enterprise integration**, centralized admin, SOC 2 Type II. **API is listed as an Enterprise *Addon*** on the pricing page (`https://watchduty.org/data-api`). |

- There is **no self-serve public API**. The API product exists only as an Enterprise addon; datasets exposed, formats, and pricing are **not published** — ⚠️ UNVERIFIED, requires a sales conversation.
- Enterprise page (`https://www.watchduty.org/solutions/overview`) explicitly targets **utilities, emergency management, telecom/critical infrastructure, insurance**. A commercial fire-protection company fits their stated customer profile ("trusted by frontline responders... at a fraction of the cost of traditional commercial platforms") — licensed commercial access looks obtainable, but terms must be negotiated. **Action: contact Watch Duty sales for Enterprise + API addon; this is the only legitimate path for WG Response.**

### 1.2 Unofficial app API (verified live, NO auth required — but see 1.3)

The mobile/web app talks to `https://api.watchduty.org`. Probed today:

**`GET https://api.watchduty.org/api/v1/geo_events/?is_relevant=true&limit=5`** → HTTP 200, JSON **array** of geo-event objects (~90+ active records returned; the `limit` param did not appear to be honored on this call — ⚠️ param semantics unverified). Object shape:

```jsonc
{
  "id": 12345,                    // int
  "is_active": true,
  "name": "…Fire",
  "description": null,
  "lat": 34.1, "lng": -118.2,     // floats
  "address": null,
  "geo_event_type": "wildfire",   // "wildfire" | "location"
  "date_created": "…ISO8601…",
  "date_modified": "…ISO8601…",
  "notification_type": "normal",  // "silent" | "normal"
  "incident_id": null,
  "external_id": null, "external_source": null,
  "reporter_managed": true,
  "child_geo_events": [], "parent_geo_events": [],
  "data": {
    "geo_event_type": "wildfire",
    "is_prescribed": false, "prescribed_date_start": null,
    "is_fps": null,
    "containment": 0,             // 0–100 or null
    "acreage": 152.0,             // float or null
    "links": [{"link_type": "…", "label": "…", "value": "…"}],
    "evacuation_orders": null,    // strings or null
    "evacuation_warnings": null,
    "evacuation_advisories": null,
    "evacuation_shelter_in_place": null,
    "evacuation_notes": null,
    "has_custom_evacuation_orders": false,  // + warnings/advisories variants
    "is_complex_parent": false,
    "temporarily_display_evac_zones": false,
    "bot_locked_fields": null
  }
}
```

**`GET https://api.watchduty.org/api/v1/reports/?geo_event_id={id}&is_moderated=true&is_active=true&limit=3`** → HTTP 200, DRF-style paginated envelope `{count, next, previous, results:[…]}` (reporter posts per incident — the human-intel stream).

**`GET https://api.watchduty.org/api/v1/evac_zones/?limit=2`** → HTTP **400** (endpoint exists but requires other params — likely zone ids or lat/lng/bbox; ⚠️ param shape unverified). Evac-zone geometry in the app comes largely from Genasys Protect (Zonehaven) zones — ⚠️ sourcing unverified.

⚠️ UNVERIFIED (likely but not probed): `GET /api/v1/geo_events/{id}/` detail endpoint; perimeter geometry delivery (app appears to use a separate geospatial tile server, mentioned in their Heroku/New Relic case studies).

### 1.3 Legal / ToS posture on unofficial use — **DO NOT BUILD ON THIS**

Watch Duty ToS (`https://www.watchduty.org/legal/eula`, verified today) expressly prohibits, quoting:

- "Use any robot, spider, or other automatic device, process, or means to access the Platform for any purpose"
- "[You] must not access or use for any commercial purposes any part of the Platform or any services or materials available"
- "You must not reproduce, distribute, modify, create derivative works of, publicly display… republish, download, store, or transmit"
- reverse-engineering clause: "decompile, reverse engineer, disassemble, attempt to derive the source code of…"

Polling `api.watchduty.org` from WG Response servers would be automated + commercial + redistributive — a triple ToS breach against a sympathetic nonprofit whose goodwill WG may want (and whose data quality WG depends on). **Verdict: unofficial API is useful only as a prototyping reference for data shapes; production use requires the Enterprise API license.**

**Latency/cadence:** human-verified updates typically within minutes of dispatch/radio traffic; no fixed cadence (event-driven). During Jan-2025 LA fires, 8M+ users in one week.

---

## 2. NIFC / WFIGS (national authoritative — perimeters + incident points)

Host org: `services3.arcgis.com/T4QMspbfLg3qTGWY` (NIFC ArcGIS Online). Public, no key, standard ArcGIS REST query API (`/query?where=1%3D1&outFields=*&f=geojson`, pagination via `resultOffset`, `maxRecordCount=2000`). Discovery portal: `https://data-nifc.opendata.arcgis.com/`.

### 2.1 Current perimeter polygons (verified)

```
https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0
```
- Layer "Perimeters", `esriGeometryPolygon`, maxRecordCount 2000.
- Key fields (verified): `poly_IncidentName`, `attr_IncidentName`, `poly_IRWINID`, `attr_IrwinID`, `poly_GISAcres`, `attr_CalculatedAcres`, `attr_FinalAcres`, `attr_PercentContained`, `attr_ContainmentDateTime`, `attr_FireDiscoveryDateTime`, `attr_ModifiedOnDateTime_dt`, `poly_DateCurrent`.
- `attr_*` = IRWIN incident attributes; `poly_*` = GIS perimeter attributes. Join key to points = IRWIN ID.

### 2.2 Current incident points (verified)

```
https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0
```
- Layer "Incidents", `esriGeometryPoint`, maxRecordCount 2000.
- Verified fields: `IncidentName`, `IrwinID`, `IncidentSize`, `PercentContained`, `FireDiscoveryDateTime`, `ModifiedOnDateTime_dt`, `POOState`, `IncidentTypeCategory` (WF=wildfire, RX=prescribed).

**Cadence:** NIFC states the layers **refresh every 5 minutes**, with perimeter-source changes visible **within ~15 minutes**. Real-world perimeter *content* freshness varies: NIRSC IR-flight perimeters are typically once-nightly; FIRIS/agency uploads can be intra-day. Points appear near-immediately after an IRWIN record is created (usually within minutes-to-an-hour of dispatch).

Example poll (California active wildfires, GeoJSON):
```
…/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState%3D'US-CA'&outFields=*&f=geojson
```

## 3. IRWIN (upstream integrator — restricted)

- IRWIN itself is **not publicly accessible**: access to IRWIN Observer / the GeoPlatform "IRWIN Data" service requires a GeoPlatform/Login.gov account and is "intended for use by IRWIN connected systems and NWCG affiliated agencies" (wildfire.gov; IRWIN Data Services Guide, Feb 2024 PDF on wildfireweb-prod-media-bucket.s3.us-gov-west-1.amazonaws.com).
- Official guidance for everyone else: consume IRWIN data **via the NIFC Open Data site / WFIGS layers (§2)** — which is exactly the same incident attribute set (`attr_*` fields). **Verdict: do not pursue IRWIN direct; WFIGS is the public IRWIN.**

## 4. CAL FIRE incidents API (California incident metadata)

Both verified live today, no auth:

```
https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false        → JSON array
https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false → GeoJSON FeatureCollection (Point)
```

Record fields (verified, both endpoints share them): `Name`, `Final`, `Updated`, `Started`, `AdminUnit`, `AdminUnitUrl`, `County`, `Location`, `AcresBurned`, `PercentContained`, `ControlStatement`, `AgencyNames`, `Longitude`, `Latitude`, `Type`, `UniqueId` (GUID), `Url`, `ExtinguishedDate`, `ExtinguishedDateOnly`, `StartedDateOnly`, `IsActive`, `CalFireIncident`, `NotificationDesired`. (`inactive=true` returns historical.)

**Cadence:** manually curated by CAL FIRE duty officers; major incidents update several times/day, small ones can lag hours. ⚠️ exact SLA unverified. Use as CA-specific metadata cross-check (acres/containment narrative), not as the fast trigger.

## 5. NASA FIRMS (satellite hotspots — the growth signal)

Docs: `https://firms.modaps.eosdis.nasa.gov/api/area/` (verified). Free **MAP_KEY** via the "Get MAP Key" form on that page (email signup, instant).

**URL templates (verified, quoted from docs):**
```
https://firms.modaps.eosdis.nasa.gov/api/area/csv/[MAP_KEY]/[SOURCE]/[AREA_COORDINATES]/[DAY_RANGE]
https://firms.modaps.eosdis.nasa.gov/api/area/csv/[MAP_KEY]/[SOURCE]/[AREA_COORDINATES]/[DAY_RANGE]/[DATE]
```
- `AREA_COORDINATES` = `west,south,east,north` bbox (or `world`); `DAY_RANGE` = 1–5; `DATE` = `YYYY-MM-DD`.
- **Sources:** `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT`, `LANDSAT_NRT` (US/Canada only), plus `*_SP` standard-processing variants.
- **Rate limit:** 5000 transactions / 10-minute interval per MAP_KEY.
- **Output:** CSV (primary). Typical VIIRS columns: `latitude, longitude, bright_ti4, scan, track, acq_date, acq_time, satellite, instrument, confidence, version, bright_ti5, frp, daynight` — ⚠️ column list from FIRMS attribute docs, not re-verified today. WMS/WFS services also exist with a MAP_KEY if GeoJSON is preferred.
- Example (Southern California, last 24h, NOAA-20 VIIRS):
  `…/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/-121.0,32.5,-114.0,36.0/1`

**Latency & resolution (verified via NASA Earthdata):** NRT = within **~3 hours** of observation globally; **US/Canada get real-time (RT) and Ultra Real-Time (URT)** feeds (direct-broadcast; URT measured in minutes — ⚠️ exact URT minute figure unverified). Resolution: **VIIRS 375 m**, **MODIS 1 km**, Landsat 30 m (US/Canada). Revisit: each VIIRS satellite ~2×/day (×3 satellites ≈ 4–6 usable passes/day mid-latitudes); MODIS 2 satellites. **Gap: no geostationary (GOES) feed in the FIRMS area API — passes are hours apart at night; do not treat FIRMS as continuous.**

## 6. FIRIS / ALERTCalifornia (California rapid perimeters + cameras)

- **FIRIS** (Cal OES): two IR-equipped fixed-wing aircraft (Sacramento/McClellan + Los Alamitos/Chino), 7 days/wk; produces intra-day tactical perimeters, fed into Intterra "Next-Gen SCOUT" (agency-only) and WIFIRE.
- **Public access path (verified today):**
  ```
  https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer
  ```
  Layer 0 = **"FIRIS WFIGS ComboLayer"** (polygon). Owner `ITS.GIS.CALFIRE`. Combines CAL FIRE Intel remote-sensing perimeters + FIRIS aircraft perimeters + WFIGS 2026 perimeters — this is what CAL FIRE's own public incident map draws. Supports GeoJSON export. This is the **fastest public perimeter source in CA** (aircraft perimeters often hours ahead of the nightly national IR product). ⚠️ per-source refresh cadence inside the combo layer unverified; field schema not enumerated (query `?f=pjson` on layer 0 during implementation).
- **ALERTCalifornia** (UCSD): 1,000+ PTZ cameras, public viewing at alertcalifornia.org; **no public API**; AI detections go to fire agencies. Watch Duty Enterprise/Pro embeds camera feeds. ⚠️ direct programmatic access not available/unverified — treat as human-in-the-loop verification aid, or get cameras via the Watch Duty Enterprise license.

---

## 7. Latency comparison & recommended stack

| Feed | Signal class | Auth | Refresh/latency | Coverage |
|---|---|---|---|---|
| Watch Duty (Enterprise API) | incident metadata, evac, perimeters, aircraft, cameras | license (contact sales) | minutes (human-verified, event-driven) | US nationwide |
| WFIGS Incident Locations Current | incident points (IRWIN) | none | layer refresh 5 min; new incidents ≈ minutes–1h | US |
| WFIGS Perimeters Current | perimeter polygons | none | layer refresh 5 min; content often nightly (IR flights) | US |
| CA FIRIS ComboLayer | perimeter polygons (tactical) | none | intra-day during CA incidents | CA |
| CAL FIRE IncidentApi | incident metadata | none | duty-officer cadence (hours) | CA |
| NASA FIRMS area API | hotspot points (growth) | free MAP_KEY | US RT/URT ≈ minutes–1h; NRT ≤3h; 4–6 VIIRS passes/day | global |
| IRWIN direct | — | agency-only | — | use WFIGS instead |

**Recommended near-real-time picture for the 72h/24h alert engine:**
1. **Primary intel:** Watch Duty Enterprise API (license) — earliest human-verified new-start + evac signal. Until licensed, use WFIGS points as trigger.
2. **Perimeter of record:** WFIGS Perimeters Current, unioned with FIRIS ComboLayer in CA (take freshest by IRWIN ID / spatial match) — poll every 5 min.
3. **Growth signal between perimeters:** FIRMS `VIIRS_SNPP_NRT` + `VIIRS_NOAA20_NRT` + `VIIRS_NOAA21_NRT` bbox polls every 15 min per client-region; convex-hull/alpha-shape hotspot clusters vs last perimeter → effective spread vector feeding the propagation forecaster.
4. **Cross-check/enrichment:** CAL FIRE IncidentApi (CA narrative, containment), ALERTCalifornia cameras via Watch Duty for human confirmation before EXTREME (24h) alerts and crew dispatch.
5. **Dedup key:** IRWIN ID everywhere it exists (`IrwinID` / `poly_IRWINID`); Watch Duty geo_events carry `external_id`/`incident_id` for mapping — ⚠️ verify mapping semantics during integration.

**Open items / could not verify:** Watch Duty Enterprise API dataset list, formats, price, and license terms (sales call required); Watch Duty evac_zones/detail endpoint params; FIRMS exact URT latency figure and current CSV column list; CAL FIRE update SLA; FIRIS per-source refresh cadence; ALERTCalifornia programmatic access.
