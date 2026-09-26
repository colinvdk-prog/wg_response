"""tools/fetch_sim_tiles.py — SIM-1: one-time LFPS fetch of the five simulator tiles.

Pulls REAL LANDFIRE terrain/fuel (LF2024_FBFM40 + LF2020_SlpD/Asp/Elev via the
keyless LFPS job API — wgresponse.terrain.LfpsTerrain, the shipped adapter) for
the five locations pinned in docs/SIMULATOR_DESIGN.md — ~18x18 km at 90 m each —
and saves them as dependency-free ascii tiles ``data/tiles/<key>.wgt``
(terrain.save_ascii_tile), after which the simulator and tests/test_sim_tiles.py
run fully OFFLINE and dep-free.

Usage (from the repo root; LIVE network, each LFPS job runs 90-180 s server-side,
expect ~15 min for all five):

    py -3 tools/fetch_sim_tiles.py                  # fetch whatever is missing
    py -3 tools/fetch_sim_tiles.py --force          # refetch (fresh LFPS jobs)
    py -3 tools/fetch_sim_tiles.py --only malibu_ca # subset (repeatable)
    py -3 tools/fetch_sim_tiles.py --extended       # fire-zone tiles (*_ext.wgt)

EXTENDED (fire-zone) tiles: same five centers, PRIMARY ~48x48 km @ 240 m —
still ~200x200 cells, so decode/summarize/simulate cost matches the standard
tiles — saved as ``data/tiles/<key>_ext.wgt`` with their own provenance file
``data/tiles/provenance_ext.json``. They exist so the simulator can accept
ignition points far (~24 km) from the client core instead of the standard
tile's ~9 km cap. The LFPS job API's maximum AOI is UNDOCUMENTED, so the first
requested location (malibu_ca on a full run) acts as the AOI-size probe: if
the 48 km job fails twice, the tool STEPS DOWN to the fallback ~36x36 km @
180 m and uses that for ALL five locations — the product stays uniform — and
records which size won in provenance_ext.json (``aoi_km``/``stepped_down``).
Once any _ext tile exists its size is inferred and reused for the rest
(uniformity survives interrupted runs); --force re-probes from the primary.

PER-LOCATION GEOMETRY (founder 2026-09-25, "expand the area for Vail so that
the entire Eagle to Vail corridor can be modeled at once"): a key listed in
TILE_GEOMETRY fetches its STANDARD tile at its own (half_m, cell_m) instead of
the 18 km @ 90 m law — eagle_vail_co is the whole I-70 Eagle Valley, Gypsum to
East Vail, ~60x60 km @ 200 m (300x300). Its entry is a LADDER of rungs in
probe order: the LFPS max AOI is undocumented (48 km is the largest proven),
so a rung that fails EXT_PROBE_ATTEMPTS times steps down to the next (56, then
52 km at the same cell size) and provenance.json records which rung won
(``aoi_km`` / ``stepped_down`` / ``aoi_ladder_km``). Every key NOT listed keeps
HALF_M / CELL_M exactly: its tile, bbox and provenance record never change.
Keys in NO_EXTENDED have no ``_ext`` fire-zone product by design (their
standard tile already reaches tens of km); ``--extended`` skips them loudly.

Behavior:
  * idempotent — a location whose data/tiles/<key>.wgt already exists is
    skipped (re-summarized from disk) unless --force;
  * --force deletes the tile AND that location's LFPS zip/tile cache entries in
    out/lfps_sim_cache so a genuinely fresh job is submitted;
  * sequential, with a progress line per LFPS HTTP round-trip (submit / status
    poll / download);
  * each location gets 1 + RETRIES attempts; a location that still fails is
    reported LOUDLY, recorded in data/tiles/provenance.json, and the run
    continues (final exit code 1) — the simulator must then show its loud
    missing-tile banner + synthetic fallback for that location, never silence;
  * LFPS requires an Email param on submit (HTTP 400 without one — see the
    wgresponse/cli.py note): env LFPS_EMAIL else ops@thewildfireguardian.com;
  * finishes by writing data/tiles/provenance.json and printing the
    '## Tile provenance' markdown block for docs/SIMULATOR_DESIGN.md.

The live GeoTIFF decode deps (tifffile+numpy+imagecodecs) are probed by
LfpsTerrain BEFORE any job is submitted (OPS-2); the committed ascii tiles this
tool produces need none of them. No RNG anywhere — the tool is a cache filler;
determinism of the simulator comes from the committed tiles being static files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from wgresponse.contracts import BBox  # noqa: E402
from wgresponse.fuels import FUEL_TABLE, is_burnable  # noqa: E402
from wgresponse.terrain import (M_PER_DEG_LAT, LfpsTerrain,  # noqa: E402
                                _default_http_get, _m_per_deg_lon,
                                load_ascii_tile, save_ascii_tile)

#: The SIMULATOR_DESIGN.md locations: (key, display name, lat, lon).
#: KEEP IN SYNC with wgresponse.simui.LOCATIONS (the roads fetcher imports
#: that registry directly; this tuple is standalone so the tile tool stays
#: importable without the sim package's heavier deps).
LOCATIONS = (
    ("malibu_ca",     "Malibu / Santa Monica Mtns, CA", 34.055, -118.72),
    ("paradise_ca",   "Paradise / Magalia, CA",         39.775, -121.60),
    ("santa_rosa_ca", "Santa Rosa NE / Mark West, CA",  38.50,  -122.65),
    ("boulder_co",    "Boulder / Superior, CO",         39.955, -105.16),
    ("park_city_ut",  "Park City / Summit Park, UT",    40.72,  -111.55),
    # Witch Creek corridor estate WUI (founder 2026-07-31)
    ("rancho_santa_fe_ca", "Rancho Santa Fe / Covenant, CA", 33.020, -117.200),
    # VAIL -> ASPEN CORRIDOR (founder 2026-09-23; six community tiles served
    # from the Eagle base; docs/vail_aspen_response_program.md)
    ("vail_co",              "Vail / East Vail / Minturn, CO",        39.635, -106.360),
    ("beaver_creek_co",      "Beaver Creek / Avon / Edwards, CO",    39.625, -106.560),
    ("eagle_co",             "Eagle / Gypsum, CO (corridor base)",   39.650, -106.890),
    ("glenwood_springs_co",  "Glenwood Springs, CO",                 39.545, -107.320),
    ("basalt_carbondale_co", "Basalt / El Jebel / Carbondale, CO",   39.395, -107.125),
    ("aspen_co",             "Aspen / Snowmass Village, CO",         39.210, -106.870),
    # EAGLE -> VAIL CORRIDOR (founder 2026-09-25): the whole I-70 Eagle Valley
    # on ONE tile (Gypsum / airport to East Vail) — per-location geometry in
    # TILE_GEOMETRY below; no extended product (NO_EXTENDED).
    ("eagle_vail_co",        "Eagle → Vail corridor, CO",            39.635, -106.620),
)
HALF_M = 9000.0        # bbox half-width in meters -> ~18 x 18 km per location
CELL_M = 90.0          # simulator grid step (LANDFIRE native 30 m, NN-resampled)

#: PER-LOCATION STANDARD-TILE GEOMETRY (module docstring): key -> ladder of
#: (half_m, cell_m) rungs in probe order. Keys absent here use the one-rung
#: standard law ((HALF_M, CELL_M),) — byte-identical tiles, bboxes and
#: provenance. KEEP IN SYNC with wgresponse.simui.LOCATIONS[key].tile_half_m /
#: .tile_cell_m (the rung that WON; tests/test_sim_tiles.py pins the committed
#: tile against both). eagle_vail_co: W edge -106.970 (Gypsum exit 140 at
#: -106.9425 sits 2.4 km inside) .. E edge -106.270 (East Vail exit 180 at
#: -106.2870, 1.5 km inside); 200 m chosen by measured tick cost
#: (docs/SIMULATOR_DESIGN.md "Eagle → Vail corridor preset").
TILE_GEOMETRY = {
    "eagle_vail_co": ((30000.0, 200.0), (28000.0, 200.0), (26000.0, 200.0)),
}
#: Keys with NO extended (<key>_ext.wgt) fire-zone tile BY DESIGN — the
#: standard tile already spans the far-ignition distances the extended
#: product exists for. --extended skips them loudly; the simulator's
#: missing-_ext fallback note says so (simui.SimLocation.extended_zone).
NO_EXTENDED = frozenset({"eagle_vail_co"})
RETRIES = 2            # extra attempts after the first failure (ticket SIM-1)
RETRY_SLEEP_S = 20.0
TILE_DIR = os.path.join(_REPO_ROOT, "data", "tiles")
CACHE_DIR = os.path.join(_REPO_ROOT, "out", "lfps_sim_cache")
PROVENANCE_PATH = os.path.join(TILE_DIR, "provenance.json")
DEVELOPED_CODES = (91, 93)   # NB1 urban/developed + NB3 agriculture (WUI anchors)
WUI_RADIUS_CELLS = 3         # same band the simulator's client placement uses

#: Extended fire-zone tiles (--extended): (half_m, cell_m) candidates in probe
#: order. Both keep the grid at ~200x200 cells (2*half_m / cell_m = 200), i.e.
#: the same decode/summarize/runtime cost as the standard tiles. The LFPS max
#: AOI is undocumented -> primary 48x48 km is probed on the first requested
#: location; two failures there step the WHOLE product down to 36x36 km.
EXT_SIZES = ((24000.0, 240.0), (18000.0, 180.0))
EXT_SUFFIX = "_ext"
EXT_PROBE_ATTEMPTS = 2       # probe failures before stepping down (per ticket)
EXT_TIMEOUT_S = 1200.0       # larger AOI -> longer LFPS jobs (2-8 min typical)
PROVENANCE_EXT_PATH = os.path.join(TILE_DIR, "provenance_ext.json")


def tile_path(key: str, suffix: str = "") -> str:
    return os.path.join(TILE_DIR, f"{key}{suffix}.wgt")


def geometry_ladder(key: str) -> tuple:
    """(half_m, cell_m) rungs of key's STANDARD tile in probe order: its
    TILE_GEOMETRY ladder, else the one-rung standard law (HALF_M, CELL_M)."""
    return TILE_GEOMETRY.get(key, ((HALF_M, CELL_M),))


def saved_geometry(path: str):
    """(half_m, cell_m) of a saved ascii tile read from its HEADER line only
    (``nrows ncols cell_m north_lat west_lon`` — no band parse): half_m =
    nrows * cell_m / 2. None when the file is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.readline().split()
        nrows, cell = int(head[0]), float(head[2])
    except (OSError, ValueError, IndexError):
        return None
    return nrows * cell / 2.0, cell


def _console_safe() -> None:
    """Never let a display name the console cannot encode (the corridor's
    'Eagle → Vail corridor, CO' on a Windows cp1252 pipe) kill a long LFPS
    run: unencodable characters print as backslash escapes instead."""
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def bbox_for(lat: float, lon: float, half_m: float = HALF_M) -> BBox:
    """~(2*half_m) x (2*half_m) bbox centered on (lat, lon); default ~18x18 km.

    Uses terrain.py's own grid-law constants (M_PER_DEG_LAT / _m_per_deg_lon)
    so the resulting GridSpec comes out exactly 2*half_m/cell_m cells per side
    (200 x 200 for every (half_m, cell_m) pair this tool uses)."""
    dlat = half_m / M_PER_DEG_LAT
    dlon = half_m / _m_per_deg_lon(lat)
    return BBox(west=lon - dlon, south=lat - dlat, east=lon + dlon, north=lat + dlat)


def wui_developed_cells(tile, row_range=None, col_range=None) -> int:
    """Developed cells (FBFM40 91/93) with >=1 burnable cell within
    WUI_RADIUS_CELLS (Chebyshev) — the interface-band precheck of SIM-1.

    Optional half-open ``row_range``/``col_range`` restrict which DEVELOPED
    cells are counted (the burnable neighbor may sit anywhere in the full
    grid): the extended-tile checks use the CENTER ~18x18 km window to pin
    that the client-placement core still exists inside a fire-zone tile."""
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    r_lo, r_hi = row_range if row_range is not None else (0, nr)
    c_lo, c_hi = col_range if col_range is not None else (0, nc)
    fuel = tile.fuel.data
    burn = [is_burnable(int(v)) for v in fuel]
    count = 0
    for r in range(r_lo, r_hi):
        base = r * nc
        for c in range(c_lo, c_hi):
            if int(fuel[base + c]) not in DEVELOPED_CODES:
                continue
            for rr in range(max(0, r - WUI_RADIUS_CELLS),
                            min(nr, r + WUI_RADIUS_CELLS + 1)):
                row = rr * nc
                lo = max(0, c - WUI_RADIUS_CELLS)
                hi = min(nc, c + WUI_RADIUS_CELLS + 1)
                if any(burn[row + lo:row + hi]):
                    count += 1
                    break
    return count


def center_window(spec, half_m: float):
    """Half-open (row_range, col_range) of the CENTER ~(2*half_m)-wide window
    of a grid — the standard-tile footprint inside an extended tile."""
    half_cells = max(1, int(round(half_m / spec.cell_m)))
    r0 = max(0, spec.nrows // 2 - half_cells)
    r1 = min(spec.nrows, spec.nrows // 2 + half_cells)
    c0 = max(0, spec.ncols // 2 - half_cells)
    c1 = min(spec.ncols, spec.ncols // 2 + half_cells)
    return (r0, r1), (c0, c1)


def summarize(tile, path: str, center_wui_half_m: float = None) -> dict:
    """Histogram + sanity summary of a saved tile (goes into provenance).

    ``center_wui_half_m`` (extended tiles: HALF_M) additionally counts the
    WUI developed cells inside the CENTER window of that half-width — the
    client-placement core the fire-zone feature assumes is still present."""
    spec = tile.spec
    n = spec.nrows * spec.ncols
    counts: dict = {}
    for v in tile.fuel.data:
        code = int(v)
        counts[code] = counts.get(code, 0) + 1
    burnable = sum(cnt for code, cnt in counts.items() if is_burnable(code))
    developed = sum(counts.get(c, 0) for c in DEVELOPED_CODES)
    outside = {str(code): cnt for code, cnt in sorted(counts.items())
               if code not in FUEL_TABLE}

    def _nm(code: int) -> str:
        fm = FUEL_TABLE.get(code)
        return fm.name.split()[0] if fm else "OUTSIDE-NAMESPACE"

    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    out = {
        "file": os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/"),
        "bytes": os.path.getsize(path),
        "nrows": spec.nrows, "ncols": spec.ncols, "cell_m": spec.cell_m,
        "burnable_pct": round(100.0 * burnable / n, 2),
        "developed_91_93_pct": round(100.0 * developed / n, 2),
        "water_98_pct": round(100.0 * counts.get(98, 0) / n, 2),
        "nodata_pct": round(100.0 * counts.get(-9999, 0) / n, 2),
        "codes_outside_namespace": outside,
        "top_codes": [f"{code} {_nm(code)} {100.0 * cnt / n:.1f}%"
                      for code, cnt in top],
        "wui_developed_cells_r3": wui_developed_cells(tile),
        "elev_min_m": min(tile.elev_m.data),
        "elev_max_m": max(tile.elev_m.data),
        "slope_max_deg": max(tile.slope_deg.data),
    }
    if center_wui_half_m is not None:
        rr, cc = center_window(spec, center_wui_half_m)
        out["wui_developed_cells_center_18x18km_r3"] = (
            wui_developed_cells(tile, rr, cc))
    return out


def _progress_http_get(key: str, t0: float):
    """LfpsTerrain http_get wrapper: one progress line per HTTP round-trip."""
    def get(url, headers=None, timeout_s=180.0):
        kind = ("submit" if "/submit" in url
                else "status-poll" if "/status" in url else "zip-download")
        print(f"  [{key}] +{time.monotonic() - t0:6.1f}s LFPS {kind}", flush=True)
        return _default_http_get(url, headers, timeout_s=timeout_s)
    return get


def fetch_one(key: str, name: str, lat: float, lon: float, force: bool, *,
              half_m: float = HALF_M, cell_m: float = CELL_M, suffix: str = "",
              attempts: int = RETRIES + 1, timeout_s: float = 900.0):
    """Fetch/refresh one location (standard tile by default; the --extended
    path passes half_m/cell_m/suffix='_ext' and its own attempt budget).
    Returns (status, payload): status 'cached'|'fetched' with a summary dict,
    or 'FAILED' with the error."""
    tag = f"{key}{suffix}"
    path = tile_path(key, suffix)
    bbox = bbox_for(lat, lon, half_m)
    aoi_km = 2.0 * half_m / 1000.0
    # Extended tiles also record the CENTER ~18x18 km WUI core (the standard
    # footprint the fire-zone feature assumes stays populated).
    wui_half = HALF_M if suffix else None
    print(f"[{tag}] {name}  center ({lat}, {lon})")
    print(f"  bbox W {bbox.west:.5f}  S {bbox.south:.5f}  E {bbox.east:.5f}  "
          f"N {bbox.north:.5f}  (~{aoi_km:.0f}x{aoi_km:.0f} km) @ {cell_m:.0f} m")
    if os.path.exists(path) and not force:
        print(f"  exists — skipping (use --force to refetch): {path}")
        return "cached", summarize(load_ascii_tile(path), path,
                                   center_wui_half_m=wui_half)
    email = os.environ.get("LFPS_EMAIL") or "ops@thewildfireguardian.com"
    t0 = time.monotonic()
    lfps = LfpsTerrain(cache_dir=CACHE_DIR, http_get=_progress_http_get(tag, t0),
                       email=email, poll_interval_s=10.0, timeout_s=timeout_s)
    if force:
        for stale in (path, lfps.cache_path(bbox, cell_m), lfps.zip_cache_path(bbox)):
            if stale and os.path.exists(stale):
                os.remove(stale)
                print(f"  --force: removed {stale}")
    last_err = "never attempted"
    for attempt in range(1, attempts + 1):
        try:
            tile = lfps.tile(bbox, cell_m)
            os.makedirs(TILE_DIR, exist_ok=True)
            save_ascii_tile(tile, path)
            summary = summarize(tile, path, center_wui_half_m=wui_half)
            summary["fetch_seconds"] = round(time.monotonic() - t0, 1)
            print(f"  saved {path} ({summary['bytes']:,} bytes) "
                  f"in {time.monotonic() - t0:.0f} s")
            return "fetched", summary
        except Exception as e:  # noqa: BLE001 — one location must not kill the run
            last_err = f"{type(e).__name__}: {e}"
            print(f"  !! [{tag}] attempt {attempt}/{attempts} FAILED: {last_err}",
                  flush=True)
            if attempt < attempts:
                print(f"  retrying in {RETRY_SLEEP_S:.0f} s ...")
                time.sleep(RETRY_SLEEP_S)
    print(f"  ** [{tag}] FAILED after {attempts} attempts — tile NOT written.")
    print("  ** The simulator must show its LOUD missing-tile banner (synthetic "
          "fallback) for this location; tests/test_sim_tiles.py will skip it "
          "with a visible reason.")
    return "FAILED", last_err


def write_provenance(results: dict, path: str = PROVENANCE_PATH,
                     meta: dict = None) -> dict:
    """Merge this run's results into the provenance json at ``path`` (so
    --only runs never erase other locations' records). ``meta`` (the extended
    run's aoi_km/cell_m/stepped_down record) is applied AFTER the on-disk doc
    so this run's size decision always wins. Returns the merged document."""
    doc = {"layers": LfpsTerrain.DEFAULT_LAYERS, "cell_m": CELL_M,
           "bbox_half_m": HALF_M, "locations": {}}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc.update(json.load(f))
        except (ValueError, OSError):
            pass  # rewrite from scratch on a corrupt file
    if meta:
        doc.update(meta)
    doc.setdefault("locations", {})
    doc["locations"].update(results)
    os.makedirs(TILE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return doc


def print_markdown(doc: dict, section: str = "Tile provenance",
                   exclude=()) -> None:
    """Print the '## <section>' block for docs/SIMULATOR_DESIGN.md (keys in
    ``exclude`` — no product by design — get a one-line 'by design' row)."""
    print(f"\n---- markdown for docs/SIMULATOR_DESIGN.md '## {section}' ----")
    aoi = f" (AOI ~{doc['aoi_km']}x{doc['aoi_km']} km)" if "aoi_km" in doc else ""
    print(f"Layers: {doc['layers']} @ {doc['cell_m']:.0f} m{aoi}")
    for key, _name, _lat, _lon in LOCATIONS:
        if key in exclude:
            print(f"- {key}: no tile in this product BY DESIGN")
            continue
        rec = doc["locations"].get(key)
        if not rec:
            print(f"- {key}: NO RECORD (never fetched)")
            continue
        if rec["status"] == "FAILED":
            print(f"- {key}: FAILED — {rec['error']}")
            continue
        s = rec["summary"]
        extra = ""
        if "wui_developed_cells_center_18x18km_r3" in s:
            extra = (f" center18k_wui {s['wui_developed_cells_center_18x18km_r3']}"
                     f" nodata {s.get('nodata_pct', 0.0)}%")
        if "aoi_km" in rec:            # per-location geometry (TILE_GEOMETRY)
            extra += (f" aoi ~{rec['aoi_km']} km"
                      + (" (STEPPED DOWN)" if rec.get("stepped_down") else ""))
        print(f"- {key}: {s['file']} {s['bytes']:,} B "
              f"{s['nrows']}x{s['ncols']}@{s['cell_m']:.0f}m "
              f"burnable {s['burnable_pct']}% dev(91/93) {s['developed_91_93_pct']}% "
              f"water {s['water_98_pct']}% wui_dev_cells {s['wui_developed_cells_r3']}"
              f"{extra} top {s['top_codes'][:3]}")


def _prior_locations(path: str) -> dict:
    """The 'locations' dict of an existing provenance file ({} when absent
    or corrupt) — used to preserve original fetch timestamps on cached runs."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("locations", {})
        except (ValueError, OSError):
            pass
    return {}


def _location_record(status, payload, name: str, lat: float, lon: float,
                     half_m: float, cell_m: float, now: str,
                     prior_rec: dict) -> dict:
    """One provenance record from a fetch_one result."""
    bbox = bbox_for(lat, lon, half_m)
    rec = {"status": status, "name": name,
           "center_lat": lat, "center_lon": lon,
           "bbox_wsen": [round(bbox.west, 6), round(bbox.south, 6),
                         round(bbox.east, 6), round(bbox.north, 6)],
           "layers": LfpsTerrain.DEFAULT_LAYERS, "cell_m": cell_m,
           "bbox_half_m": half_m, "checked_utc": now}
    if status == "FAILED":
        rec["error"] = payload
    else:
        rec["summary"] = payload
        if status == "fetched":
            rec["fetched_utc"] = now
        else:  # cached: keep the original fetch timestamp on record
            prev = (prior_rec or {}).get("fetched_utc")
            if prev:
                rec["fetched_utc"] = prev
    return rec


def _fetch_ladder(key: str, name: str, lat: float, lon: float, ladder: tuple,
                  force: bool, now: str, prior_rec: dict) -> dict:
    """STANDARD tile of a TILE_GEOMETRY key (module docstring PER-LOCATION
    GEOMETRY): probe the ladder top-down — every rung but the last gets
    EXT_PROBE_ATTEMPTS attempts, the last the normal 1 + RETRIES — and stop
    at the first success. An already-saved tile is re-summarized at the rung
    it was SAVED at (read from its header), so an idempotent re-run never
    re-probes and never mislabels a stepped-down tile. Returns the
    provenance record: the winning (half_m, cell_m) plus aoi_km /
    stepped_down / aoi_ladder_km / aoi_failed_km (the rungs above the
    winner, which by the ladder law failed)."""
    ladder_km = [round(2.0 * h / 1000.0) for h, _c in ladder]
    path = tile_path(key)
    won = None
    if os.path.exists(path) and not force:
        geom = saved_geometry(path)
        won = next((i for i, (h, c) in enumerate(ladder)
                    if geom is not None and abs(geom[1] - c) <= 1.0
                    and abs(geom[0] - h) <= c), None)
        if won is None:
            print(f"!! [{key}] the saved tile's geometry {geom} matches NO "
                  f"rung of {ladder} — the simulator registry and the tests "
                  f"will disagree with it; refetch with --force --only {key}")
        half_m, cell_m = ladder[won] if won is not None else (geom or ladder[0])
        status, payload = fetch_one(key, name, lat, lon, False,
                                    half_m=half_m, cell_m=cell_m)
    else:
        for i, (half_m, cell_m) in enumerate(ladder):
            last = i == len(ladder) - 1
            if i:
                print(f"\n** [{key}] {ladder_km[i - 1]} km AOI failed "
                      f"{EXT_PROBE_ATTEMPTS}x — STEPPING DOWN to "
                      f"{ladder_km[i]}x{ladder_km[i]} km @ {cell_m:.0f} m "
                      f"(the LFPS max AOI is undocumented)\n")
            status, payload = fetch_one(
                key, name, lat, lon, force, half_m=half_m, cell_m=cell_m,
                attempts=(RETRIES + 1) if last else EXT_PROBE_ATTEMPTS,
                timeout_s=EXT_TIMEOUT_S)
            if status != "FAILED":
                won = i
                break
    rec = _location_record(status, payload, name, lat, lon, half_m, cell_m,
                           now, prior_rec)
    rec["aoi_km"] = round(2.0 * half_m / 1000.0)
    rec["aoi_ladder_km"] = ladder_km
    rec["stepped_down"] = won is not None and won > 0
    if won is not None:
        rec["aoi_failed_km"] = ladder_km[:won]
    elif status == "FAILED":
        rec["aoi_failed_km"] = ladder_km          # every rung failed
    else:
        rec["geometry_off_ladder"] = True         # loud print above
    return rec


def _existing_ext_size():
    """(half_m, cell_m) implied by already-present *_ext.wgt tiles, else None.

    Keeps interrupted --extended runs uniform: once ANY extended tile exists,
    its size is the product's size (first tile in LOCATIONS order wins; a
    mixed-size product gets a LOUD warning here and is failed outright by
    tests/test_sim_tiles.py's uniformity pin — fix with --extended --force)."""
    found = []
    for key, _name, _lat, _lon in LOCATIONS:
        path = tile_path(key, EXT_SUFFIX)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                cell = float(f.readline().split()[2])
        except (OSError, ValueError, IndexError):
            continue  # unreadable tile: let the fetch/tests deal with it
        for half_m, cell_m in EXT_SIZES:
            if abs(cell - cell_m) <= 1.0:
                found.append((key, (half_m, cell_m)))
                break
    if not found:
        return None
    if len({size for _k, size in found}) > 1:
        print(f"!! WARNING: existing _ext tiles disagree on cell size: "
              f"{[(k, s[1]) for k, s in found]} — proceeding with "
              f"{found[0][0]}'s; refetch with --extended --force for a "
              f"uniform product.")
    return found[0][1]


def _main_extended(args, now: str) -> int:
    """--extended run: probe the AOI size on the first requested location
    (malibu_ca on a full run), step down ONCE for the whole product if the
    primary size fails EXT_PROBE_ATTEMPTS times, then fetch the rest at the
    winning size with the normal retry ladder (loud continue-on-failure).
    NO_EXTENDED keys are skipped loudly; a run left with nothing to fetch
    returns 0 BEFORE touching the network or provenance_ext.json."""
    requested = [loc for loc in LOCATIONS if not args.only or loc[0] in args.only]
    for loc in requested:
        if loc[0] in NO_EXTENDED:
            print(f"[extended] {loc[0]}: NO extended tile BY DESIGN — its "
                  f"standard tile already spans the far-ignition distances "
                  f"(NO_EXTENDED); skipped")
    requested = [loc for loc in requested if loc[0] not in NO_EXTENDED]
    if not requested:
        print("[extended] nothing to fetch.")
        return 0
    prior = _prior_locations(PROVENANCE_EXT_PATH)
    size = None if args.force else _existing_ext_size()
    stepped_down = False
    results: dict = {}

    def _run(loc, half_m, cell_m, attempts):
        key, name, lat, lon = loc
        status, payload = fetch_one(
            key, name, lat, lon, args.force, half_m=half_m, cell_m=cell_m,
            suffix=EXT_SUFFIX, attempts=attempts, timeout_s=EXT_TIMEOUT_S)
        results[key] = _location_record(status, payload, name, lat, lon,
                                        half_m, cell_m, now, prior.get(key))
        return status

    if size is None and requested:
        probe = requested[0]  # malibu_ca on a full run (LOCATIONS order)
        half_m, cell_m = EXT_SIZES[0]
        print(f"[extended] AOI-size probe on {probe[0]!r}: "
              f"{2 * half_m / 1000:.0f}x{2 * half_m / 1000:.0f} km @ "
              f"{cell_m:.0f} m — the LFPS max AOI is undocumented; "
              f"{EXT_PROBE_ATTEMPTS} failures here step the WHOLE product "
              f"down to {2 * EXT_SIZES[1][0] / 1000:.0f} km.")
        if _run(probe, half_m, cell_m, EXT_PROBE_ATTEMPTS) == "FAILED":
            stepped_down = True
            half_m, cell_m = EXT_SIZES[1]
            print(f"\n** [extended] {2 * EXT_SIZES[0][0] / 1000:.0f} km AOI "
                  f"failed {EXT_PROBE_ATTEMPTS}x — STEPPING DOWN to "
                  f"{2 * half_m / 1000:.0f}x{2 * half_m / 1000:.0f} km @ "
                  f"{cell_m:.0f} m for ALL locations (uniform product).\n")
            _run(probe, half_m, cell_m, RETRIES + 1)
        size = (half_m, cell_m)
        requested = requested[1:]
    if size is None:
        size = EXT_SIZES[0]  # unreachable via argparse (--only has choices)
    half_m, cell_m = size
    print(f"[extended] AOI size in force: {2 * half_m / 1000:.0f}x"
          f"{2 * half_m / 1000:.0f} km @ {cell_m:.0f} m"
          + (" (STEPPED DOWN)" if stepped_down else ""))
    for loc in requested:
        _run(loc, half_m, cell_m, RETRIES + 1)

    meta = {"cell_m": cell_m, "bbox_half_m": half_m,
            "aoi_km": round(2.0 * half_m / 1000.0),
            "stepped_down": stepped_down,
            "note": "extended fire-zone tiles (<key>_ext.wgt): same five "
                    "centers, larger AOI so simulator ignition can start far "
                    "from the client core; grids stay ~200x200 (same runtime "
                    "cost as the standard tiles)"}
    doc = write_provenance(results, PROVENANCE_EXT_PATH, meta)
    print(f"\nprovenance written: {PROVENANCE_EXT_PATH}")
    print_markdown(doc, section="Extended fire-zone tiles (provenance)",
                   exclude=NO_EXTENDED)
    failed = [k for k, r in results.items() if r["status"] == "FAILED"]
    if failed:
        print(f"\nEXIT 1 — locations FAILED: {', '.join(failed)} (rerun: py -3 "
              f"tools/fetch_sim_tiles.py --extended --only {failed[0]})")
        return 1
    print("\nall requested extended tiles present.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch the five SIMULATOR_DESIGN.md terrain tiles from "
                    "LANDFIRE LFPS into data/tiles/*.wgt (live network).")
    ap.add_argument("--force", action="store_true",
                    help="refetch even if the tile exists (fresh LFPS job; "
                    "clears the location's zip/tile cache)")
    ap.add_argument("--only", action="append", metavar="KEY",
                    choices=[k for k, _, _, _ in LOCATIONS],
                    help="restrict to one location key (repeatable)")
    ap.add_argument("--extended", action="store_true",
                    help="fetch the EXTENDED fire-zone tiles (<key>_ext.wgt, "
                    "~48x48 km @ 240 m, fallback 36x36 km @ 180 m) instead of "
                    "the standard ~18x18 km tiles")
    args = ap.parse_args(argv)
    _console_safe()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.extended:
        return _main_extended(args, now)

    prior = _prior_locations(PROVENANCE_PATH)
    results: dict = {}
    for key, name, lat, lon in LOCATIONS:
        if args.only and key not in args.only:
            continue
        if key in TILE_GEOMETRY:        # per-location geometry (AOI ladder)
            results[key] = _fetch_ladder(key, name, lat, lon,
                                         geometry_ladder(key), args.force,
                                         now, prior.get(key))
            continue
        status, payload = fetch_one(key, name, lat, lon, args.force)
        results[key] = _location_record(status, payload, name, lat, lon,
                                        HALF_M, CELL_M, now, prior.get(key))

    doc = write_provenance(results)
    print(f"\nprovenance written: {PROVENANCE_PATH}")
    print_markdown(doc)
    failed = [k for k, r in results.items() if r["status"] == "FAILED"]
    if failed:
        print(f"\nEXIT 1 — locations FAILED: {', '.join(failed)} "
              f"(rerun: py -3 tools/fetch_sim_tiles.py --only {failed[0]})")
        return 1
    print("\nall requested tiles present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
