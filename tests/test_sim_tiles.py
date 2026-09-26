"""SIM-1 — sanity pins for the committed simulator terrain tiles (data/tiles).

OFFLINE ONLY: no network anywhere. Each test loads ``data/tiles/<key>.wgt``
(written once by ``py -3 tools/fetch_sim_tiles.py`` — live LANDFIRE LFPS) and
SKIPS WITH A LOUD REASON when a tile is missing: a failed or never-run fetch
must be visible in the pytest output, never silent, because the simulator falls
back to synthetic terrain with a banner for exactly that location
(docs/SIMULATOR_DESIGN.md).

EXTENDED fire-zone tiles (``<key>_ext.wgt``, fetched with ``--extended``):
same five centers at a much larger AOI (~48x48 km @ 240 m primary, 36x36 km @
180 m fallback — data/tiles/provenance_ext.json records which size won) so
ignition can start far from the client core. Their _ext variants below mirror
the standard pins, plus the fire-zone feature's load-bearing assumption: the
CENTER ~18x18 km window (the standard tile's footprint, where clients are
placed) must still contain a real WUI band.

Bands are TOLERANT (LANDFIRE re-releases shift class fractions) — these pins
catch a wrong/empty/garbage tile, not minor data-version drift. Measured
2026-07-09 values live in data/tiles/provenance.json and the doc's
'## Tile provenance' appendix (extended: 2026-07-10, provenance_ext.json,
'## Extended fire-zone tiles (provenance)').

PER-LOCATION GEOMETRY (2026-09-25): the standard ~18 km @ 90 m pins apply to
STANDARD_KEYS — every key NOT in the tool's TILE_GEOMETRY, i.e. the twelve
original presets, byte-for-byte as before. The whole-valley Eagle -> Vail
corridor tile (eagle_vail_co, ~60 km @ 200 m) gets its OWN explicit pins at
the bottom of this file; it has no extended tile by design (NO_EXTENDED), so
the extended pins run over EXT_KEYS.
"""
from __future__ import annotations

import math
import os

import pytest

from tools.fetch_sim_tiles import (CELL_M, DEVELOPED_CODES, EXT_SIZES,
                                   EXT_SUFFIX, HALF_M, LOCATIONS, NO_EXTENDED,
                                   TILE_GEOMETRY, WUI_RADIUS_CELLS,
                                   center_window, geometry_ladder,
                                   saved_geometry, tile_path,
                                   wui_developed_cells)
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse.terrain import M_PER_DEG_LAT, _m_per_deg_lon, load_ascii_tile

KEYS = tuple(k for k, _, _, _ in LOCATIONS)
#: the 18 km @ 90 m law (every key without a per-location geometry)
STANDARD_KEYS = tuple(k for k in KEYS if k not in TILE_GEOMETRY)
#: keys that have an extended (<key>_ext.wgt) fire-zone product at all
EXT_KEYS = tuple(k for k in KEYS if k not in NO_EXTENDED)
CENTERS = {k: (lat, lon) for k, _, lat, lon in LOCATIONS}
EXT_CELL_CHOICES = tuple(cell_m for _half_m, cell_m in EXT_SIZES)

_cache: dict = {}


def _tile(key: str):
    """Load (and memoize) a committed tile; loud skip when missing."""
    path = tile_path(key)
    if not os.path.exists(path):
        pytest.skip(
            f"SIMULATOR TILE MISSING: {path} — the {key!r} LFPS fetch failed or "
            f"was never run. Recreate it with `py -3 tools/fetch_sim_tiles.py "
            f"--only {key}` (LIVE network, ~2-3 min). Until then the simulator "
            f"must show its LOUD missing-tile banner + synthetic fallback for "
            f"this location — this skip is that failure staying visible.")
    if key not in _cache:
        _cache[key] = load_ascii_tile(path)
    return _cache[key]


def _fuel_counts(tile) -> dict:
    counts: dict = {}
    for v in tile.fuel.data:
        code = int(v)
        counts[code] = counts.get(code, 0) + 1
    return counts


@pytest.mark.parametrize("key", STANDARD_KEYS)
class TestSimTiles:
    def test_spec_sane(self, key):
        """~18x18 km at 90 m, grid actually covering the pinned center."""
        tile = _tile(key)
        spec = tile.spec
        assert abs(spec.cell_m - CELL_M) <= 1.0, spec.cell_m       # 90 +- 1 m
        assert spec.nrows >= 150 and spec.ncols >= 150, (spec.nrows, spec.ncols)
        lat, lon = CENTERS[key]
        south = spec.north_lat - spec.nrows * spec.cell_m / M_PER_DEG_LAT
        east = spec.west_lon + spec.ncols * spec.cell_m / _m_per_deg_lon(lat)
        assert south < lat < spec.north_lat, (south, lat, spec.north_lat)
        assert spec.west_lon < lon < east, (spec.west_lon, lon, east)
        assert tile.source.startswith("lfps:"), tile.source        # real pull

    def test_fuel_histogram_burnable(self, key):
        """Real WUI terrain: a meaningful burnable fraction (tolerant >=5%)."""
        tile = _tile(key)
        counts = _fuel_counts(tile)
        n = sum(counts.values())
        burnable = sum(cnt for code, cnt in counts.items() if is_burnable(code))
        assert burnable / n >= 0.05, f"{key}: burnable {100 * burnable / n:.2f}%"

    def test_fuel_histogram_developed(self, key):
        """Non-burnable developed (FBFM40 91 urban / 93 agriculture) >=1% —
        the towns the simulator places clients in. Ticket SIM-1 allowed a
        park_city_ut exemption ('except possibly park_city'), but the fetched
        LF2024 data made it unnecessary — measured 2026-07-09: malibu 9.26%,
        paradise 10.13%, santa_rosa 27.64%, boulder 36.58%, park_city 10.61%
        — so the uniform tolerant floor applies (10x headroom everywhere)."""
        tile = _tile(key)
        counts = _fuel_counts(tile)
        n = sum(counts.values())
        developed = sum(counts.get(c, 0) for c in DEVELOPED_CODES)
        assert developed / n >= 0.01, (
            f"{key}: developed(91/93) {100 * developed / n:.2f}% < 1%")

    def test_fuel_codes_within_fbfm40_namespace(self, key):
        """Every fuel code is a known FBFM40/NB code (FUEL_TABLE is the
        complete Scott&Burgan namespace). LANDFIRE nodata (-9999) is tolerated
        in a BOUNDED fraction: it appears only where a bbox leaves the LANDFIRE
        mapping zone (measured 2026-07-09: malibu_ca's far-offshore Pacific
        fringe, 1181 cells = 2.95%; zero in the other four) and is fail-safe
        non-burnable per fuels.is_burnable. Any other alien code (garbage,
        wrong layer order) fails hard."""
        tile = _tile(key)
        counts = _fuel_counts(tile)
        alien = {code: cnt for code, cnt in counts.items()
                 if code not in FUEL_TABLE and code != -9999}
        assert not alien, f"{key}: codes outside FBFM40+NB namespace: {alien}"
        nodata_frac = counts.get(-9999, 0) / sum(counts.values())
        assert nodata_frac <= 0.35, (
            f"{key}: {100 * nodata_frac:.1f}% nodata — tile mostly outside "
            f"LANDFIRE coverage?")

    def test_terrain_bands_finite(self, key):
        """slope/aspect/elev must be finite everywhere (engine consumes raw)."""
        tile = _tile(key)
        for name, band in (("slope", tile.slope_deg), ("aspect", tile.aspect_deg),
                           ("elev", tile.elev_m)):
            bad = sum(1 for v in band.data if not math.isfinite(v))
            assert bad == 0, f"{key}: {bad} non-finite {name} cells"

    def test_wui_band_nonempty(self, key):
        """Placement precheck: >=50 developed cells within WUI_RADIUS_CELLS
        (Chebyshev, 3 cells = 270 m) of burnable fuel — the interface band the
        simulator seeds clients into must exist in the real data."""
        tile = _tile(key)
        n = wui_developed_cells(tile)
        assert n >= 50, (
            f"{key}: only {n} developed cells within {WUI_RADIUS_CELLS} cells "
            f"of burnable fuel — WUI band effectively empty")


# ---------------------------------------------------------------------------
# Extended fire-zone tiles (<key>_ext.wgt) — _ext variants of the pins above
# ---------------------------------------------------------------------------

_ext_cache: dict = {}


def _tile_ext(key: str):
    """Load (and memoize) a committed EXTENDED tile; loud skip when missing."""
    path = tile_path(key, EXT_SUFFIX)
    if not os.path.exists(path):
        pytest.skip(
            f"EXTENDED SIMULATOR TILE MISSING: {path} — the {key!r} extended "
            f"(fire-zone) LFPS fetch failed or was never run. Recreate it with "
            f"`py -3 tools/fetch_sim_tiles.py --extended --only {key}` (LIVE "
            f"network, ~2-8 min at this AOI). Until then the far-ignition "
            f"fire-zone feature has no extended terrain for this location — "
            f"this skip is that gap staying visible.")
    if key not in _ext_cache:
        _ext_cache[key] = load_ascii_tile(path)
    return _ext_cache[key]


def test_ext_tiles_uniform_cell_size():
    """ALL committed _ext tiles share ONE cell size: the tool decides the AOI
    once (probe on the first location; a step-down applies to the whole
    product), so a mixed-size set means fetches from different policy runs —
    refetch with `py -3 tools/fetch_sim_tiles.py --extended --force`."""
    present = [k for k in KEYS if os.path.exists(tile_path(k, EXT_SUFFIX))]
    if not present:
        pytest.skip("no extended (_ext) tiles committed yet — run "
                    "`py -3 tools/fetch_sim_tiles.py --extended`")
    cells = {k: _tile_ext(k).spec.cell_m for k in present}
    assert len(set(cells.values())) == 1, f"mixed _ext cell sizes: {cells}"


@pytest.mark.parametrize("key", EXT_KEYS)
class TestSimTilesExtended:
    def test_ext_spec_sane(self, key):
        """~48x48 km @ 240 m (or the 36 km/180 m fallback — whichever size the
        fetch recorded in provenance_ext.json), >=150x150, covering the same
        pinned center as the standard tile, from a real LFPS pull."""
        tile = _tile_ext(key)
        spec = tile.spec
        assert any(abs(spec.cell_m - c) <= 1.0 for c in EXT_CELL_CHOICES), (
            f"{key}: cell_m {spec.cell_m} is neither of the extended sizes "
            f"{EXT_CELL_CHOICES} (+-1 m)")
        assert spec.nrows >= 150 and spec.ncols >= 150, (spec.nrows, spec.ncols)
        lat, lon = CENTERS[key]
        south = spec.north_lat - spec.nrows * spec.cell_m / M_PER_DEG_LAT
        east = spec.west_lon + spec.ncols * spec.cell_m / _m_per_deg_lon(lat)
        assert south < lat < spec.north_lat, (south, lat, spec.north_lat)
        assert spec.west_lon < lon < east, (spec.west_lon, lon, east)
        assert tile.source.startswith("lfps:"), tile.source      # real pull

    def test_ext_fuel_histogram_burnable(self, key):
        """Real terrain at fire-zone scale: still >=5% burnable (tolerant —
        malibu_ca's 48 km bbox is nearly half Pacific and still clears it)."""
        tile = _tile_ext(key)
        counts = _fuel_counts(tile)
        n = sum(counts.values())
        burnable = sum(cnt for code, cnt in counts.items() if is_burnable(code))
        assert burnable / n >= 0.05, f"{key}: burnable {100 * burnable / n:.2f}%"

    def test_ext_fuel_codes_within_fbfm40_namespace(self, key):
        """Same namespace pin as the standard tiles, with a LOOSER nodata
        bound (<=60% vs 35%): a 48 km AOI centered on Malibu reaches ~24 km
        into the Pacific, far past the LANDFIRE mapping zone, so a large
        -9999 fringe is expected there (fail-safe non-burnable per
        fuels.is_burnable). Any OTHER alien code still fails hard, and a
        mostly-nodata garbage tile still trips the bound."""
        tile = _tile_ext(key)
        counts = _fuel_counts(tile)
        alien = {code: cnt for code, cnt in counts.items()
                 if code not in FUEL_TABLE and code != -9999}
        assert not alien, f"{key}: codes outside FBFM40+NB namespace: {alien}"
        nodata_frac = counts.get(-9999, 0) / sum(counts.values())
        assert nodata_frac <= 0.60, (
            f"{key}: {100 * nodata_frac:.1f}% nodata — extended tile mostly "
            f"outside LANDFIRE coverage?")

    def test_ext_terrain_bands_finite(self, key):
        """slope/aspect/elev must be finite everywhere (engine consumes raw)."""
        tile = _tile_ext(key)
        for name, band in (("slope", tile.slope_deg), ("aspect", tile.aspect_deg),
                           ("elev", tile.elev_m)):
            bad = sum(1 for v in band.data if not math.isfinite(v))
            assert bad == 0, f"{key}: {bad} non-finite {name} cells"

    def test_ext_center_window_wui_core(self, key):
        """THE fire-zone assumption: the CENTER ~18x18 km window of the
        extended tile (the standard tile's footprint, where the simulator
        places clients) still contains a real WUI band — >=50 developed cells
        within WUI_RADIUS_CELLS (Chebyshev) of burnable fuel. Extended tiles
        exist to push IGNITION farther out, not to move the client core."""
        tile = _tile_ext(key)
        row_range, col_range = center_window(tile.spec, HALF_M)
        n = wui_developed_cells(tile, row_range, col_range)
        assert n >= 50, (
            f"{key}: only {n} developed cells within {WUI_RADIUS_CELLS} cells "
            f"of burnable fuel inside the center ~18x18 km window "
            f"(rows {row_range}, cols {col_range}) — the client-placement "
            f"core is missing from the extended tile")


# ---------------------------------------------------------------------------
# PER-LOCATION GEOMETRY — the Eagle -> Vail whole-valley corridor tile
# (founder 2026-09-25: "expand the area for Vail so that the entire Eagle to
# Vail corridor can be modeled at once"). Explicit, clearly-scoped pins; the
# 18 km @ 90 m law above is untouched for STANDARD_KEYS.
# ---------------------------------------------------------------------------

CORRIDOR_KEY = "eagle_vail_co"
#: the I-70 anchors and valley towns the ONE tile must hold, (lat, lon)
CORRIDOR_POINTS = {
    "Gypsum / I-70 exit 140": (39.6465, -106.9425),
    "Eagle base / I-70 exit 147": (39.6605, -106.8285),
    "Wolcott": (39.7033, -106.6795),
    "Edwards / I-70 exit 163": (39.6495, -106.5985),
    "Beaver Creek village": (39.6042, -106.5165),
    "Avon / I-70 exit 167": (39.6365, -106.5230),
    "Minturn": (39.5865, -106.4305),
    "Dowd Junction / I-70 exit 171": (39.6150, -106.4370),
    "Vail Village": (39.6403, -106.3742),
    "East Vail / I-70 exit 180": (39.6305, -106.2870),
}


def _tile_extent(tile, lat_c: float):
    """(west, south, east, north) of a tile by the FETCH law (terrain.py
    constants, the tool's bbox_for) — the rectangle LFPS was asked for."""
    spec = tile.spec
    south = spec.north_lat - spec.nrows * spec.cell_m / M_PER_DEG_LAT
    east = spec.west_lon + spec.ncols * spec.cell_m / _m_per_deg_lon(lat_c)
    return spec.west_lon, south, east, spec.north_lat


def test_corridor_is_the_only_custom_geometry_and_has_no_extended():
    """The per-location seam is scoped: exactly one key leaves the 18 km law,
    its ladder is the documented 60 -> 56 -> 52 km AOI step-down at 200 m,
    and it is exactly the key with no extended product."""
    assert CORRIDOR_KEY in KEYS
    assert set(TILE_GEOMETRY) == {CORRIDOR_KEY}
    assert NO_EXTENDED == frozenset({CORRIDOR_KEY})
    assert geometry_ladder(CORRIDOR_KEY) == (
        (30000.0, 200.0), (28000.0, 200.0), (26000.0, 200.0))
    for key in STANDARD_KEYS:
        assert geometry_ladder(key) == ((HALF_M, CELL_M),), key
    assert len(STANDARD_KEYS) == 12


class TestCorridorTile:
    """data/tiles/eagle_vail_co.wgt — loud skip when missing (same law as
    every committed tile)."""

    def test_spec_is_a_ladder_rung_not_the_90m_law(self):
        tile = _tile(CORRIDOR_KEY)
        spec = tile.spec
        rungs = [(h, c) for h, c in geometry_ladder(CORRIDOR_KEY)
                 if abs(spec.cell_m - c) <= 1.0
                 and abs(spec.nrows - 2.0 * h / c) <= 1
                 and abs(spec.ncols - 2.0 * h / c) <= 1]
        assert rungs, (f"{CORRIDOR_KEY}: {spec.nrows}x{spec.ncols} @ "
                       f"{spec.cell_m} m is no rung of its ladder")
        assert saved_geometry(tile_path(CORRIDOR_KEY)) == (
            spec.nrows * spec.cell_m / 2.0, spec.cell_m)
        # the primary rung WON on 2026-09-25 (no step-down): 300x300 @ 200 m
        assert (spec.nrows, spec.ncols) == (300, 300)
        assert abs(spec.cell_m - 200.0) <= 1.0
        assert tile.source.startswith("lfps:"), tile.source      # real pull

    def test_covers_gypsum_to_east_vail_with_margin(self):
        """Every I-70 anchor and valley town from Gypsum to East Vail lies
        >= 1 km inside every edge of the ONE tile, and maps to a cell under
        the simulator's own index law (geo.latlon_to_rc)."""
        from wgresponse import geo
        from wgresponse.contracts import LatLon
        tile = _tile(CORRIDOR_KEY)
        lat_c, _lon_c = CENTERS[CORRIDOR_KEY]
        west, south, east, north = _tile_extent(tile, lat_c)
        assert west <= -106.965 and east >= -106.275, (west, east)
        m_lon = _m_per_deg_lon(lat_c)
        for name, (lat, lon) in CORRIDOR_POINTS.items():
            margins_km = ((lon - west) * m_lon / 1000.0,
                          (east - lon) * m_lon / 1000.0,
                          (lat - south) * M_PER_DEG_LAT / 1000.0,
                          (north - lat) * M_PER_DEG_LAT / 1000.0)
            assert min(margins_km) >= 1.0, (name, margins_km)
            assert geo.latlon_to_rc(tile.spec, LatLon(lat, lon)) is not None, name

    def test_fuel_and_terrain_real_mountain_valley(self):
        """Tolerant bands (LANDFIRE drift, not exact values): a burnable
        mountain tile with real valley towns, FBFM40+NB codes only and NO
        nodata (the whole AOI sits inside the LANDFIRE mapping zone), finite
        terrain bands, Eagle Valley elevations."""
        tile = _tile(CORRIDOR_KEY)
        counts = _fuel_counts(tile)
        n = sum(counts.values())
        burnable = sum(c for code, c in counts.items() if is_burnable(code))
        developed = sum(counts.get(c, 0) for c in DEVELOPED_CODES)
        assert burnable / n >= 0.50, f"burnable {100 * burnable / n:.1f}%"
        assert developed / n >= 0.01, f"developed {100 * developed / n:.2f}%"
        alien = {code: c for code, c in counts.items() if code not in FUEL_TABLE}
        assert not alien, f"codes outside FBFM40+NB (incl. nodata): {alien}"
        for name, band in (("slope", tile.slope_deg), ("aspect", tile.aspect_deg),
                           ("elev", tile.elev_m)):
            bad = sum(1 for v in band.data if not math.isfinite(v))
            assert bad == 0, f"{bad} non-finite {name} cells"
        # the valley floor (Gypsum ~1,900 m) to the Gore / Sawatch summits
        assert 1_700.0 <= min(tile.elev_m.data) <= 2_100.0
        assert 3_600.0 <= max(tile.elev_m.data) <= 4_400.0

    def test_wui_band_spans_the_valley(self):
        """The interface band the clients seed into exists along the WHOLE
        valley: >= 200 WUI developed cells overall, and some in both the
        western (Gypsum / Eagle) and eastern (Avon / Vail) thirds."""
        tile = _tile(CORRIDOR_KEY)
        spec = tile.spec
        assert wui_developed_cells(tile) >= 200
        third = spec.ncols // 3
        west_n = wui_developed_cells(tile, None, (0, third))
        east_n = wui_developed_cells(tile, None, (spec.ncols - third, spec.ncols))
        assert west_n >= 30 and east_n >= 30, (west_n, east_n)
