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
"""
from __future__ import annotations

import math
import os

import pytest

from tools.fetch_sim_tiles import (CELL_M, DEVELOPED_CODES, EXT_SIZES,
                                   EXT_SUFFIX, HALF_M, LOCATIONS,
                                   WUI_RADIUS_CELLS, center_window, tile_path,
                                   wui_developed_cells)
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse.terrain import M_PER_DEG_LAT, _m_per_deg_lon, load_ascii_tile

KEYS = tuple(k for k, _, _, _ in LOCATIONS)
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


@pytest.mark.parametrize("key", KEYS)
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


@pytest.mark.parametrize("key", KEYS)
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
