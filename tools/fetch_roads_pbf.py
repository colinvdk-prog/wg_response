"""tools/fetch_roads_pbf.py — cut a simulator road graph from a local OSM PBF.

The Overpass fetcher (tools/fetch_sim_roads.py) does not scale to a national
world: every domain is a live query against a public shared service. This
tool cuts the same graph from a Geofabrik ``.osm.pbf`` extract already on
disk. Simplification is not reimplemented. Ways are handed to
``tools.fetch_sim_roads.build_graph`` in the element shape Overpass returns,
and the graph is written with ``roads.save_road_graph``.

Way selection matches that fetcher:

* highway tag fully matches ``HIGHWAY_REGEX`` (the same alternation);
* a way is kept when ANY of its nodes lies inside the bbox (Overpass bbox
  semantics: not a clip, and not "the way's bbox merely overlaps"). For a
  preset ``--key`` the box is ``bbox_for(LOCATIONS[key])`` formatted the way
  ``overpass_query`` sends it — each edge to 6 decimal degrees — so boundary
  nodes agree with the committed graphs;
* geometry is the way's full node list, including nodes outside the box.

Output is deterministic for a given extract aside from ``fetched_utc`` and
``fetch_seconds``. An existing output file is left in place unless
``--force`` is passed. A git-tracked ``data/roads/*.wgr`` is refused without
``--force``; ``--force`` is the deliberate overwrite.

DEPENDENCY (tested 2026-09-26, Windows, CPython 3.14.5):

    py -3 -m pip install osmium

That installed ``osmium-4.3.1-cp314-cp314-win_amd64.whl`` (pyosmium). The
engine, the cached ``.wgr`` files, and the rest of the test suite stay
stdlib-only; osmium is imported only while reading a PBF. pyrosm was
rejected: ``py -3 -m pip install pyrosm --only-binary=:all:`` cannot resolve
because ``cykhash`` publishes no cp314 wheel, and pyrosm returns a GeoDataFrame
rather than Overpass way elements. A hand-rolled protobuf reader was
unnecessary once this wheel imported.

The first pass over an extract writes ``<pbf>.wgridx`` beside the file
(under ``out/`` when the extract lives there): every matching way's node ids
and coordinates, plus a grid so later domains do not decompress the PBF
again. Target after that pass: one domain in under two minutes.

Usage (from the repo root):

    py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key vail_co
    py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key vail_co --out data/roads/vail_co.pbf.wgr
    py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --key vail_co --compare
    py -3 tools/fetch_roads_pbf.py --pbf out/osm/colorado-latest.osm.pbf --bbox -106.47,39.55,-106.25,39.72 --out out/roads/custom.wgr

``--bbox`` is W,S,E,N in degrees. ``--compare`` prints node/edge counts,
largest-component fraction, safe-zone snap distances, and a ``roads.route``
check against the committed Overpass graph for that key.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from wgresponse import geo  # noqa: E402
from wgresponse.contracts import BBox, LatLon, ResponseConfig, seeded_rng  # noqa: E402
from wgresponse.roads import (load_road_graph, route,  # noqa: E402
                              save_road_graph)
from wgresponse.simui import LOCATIONS  # noqa: E402
from tools.fetch_sim_roads import (HIGHWAY_REGEX, bbox_for,  # noqa: E402
                                   build_graph, overpass_query)

GENERATOR = "tools/fetch_roads_pbf.py"
# Install command recorded for operators and for the requirements.txt extra.
OSMIUM_INSTALL = "py -3 -m pip install osmium"
COLORADO_PBF_URL = (
    "https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf")
DEFAULT_COLORADO_PBF = Path(_REPO_ROOT) / "out" / "osm" / "colorado-latest.osm.pbf"


def missing_pbf_reason(path) -> "str | None":
    """Loud skip text when the extract is absent, else None.

    Names the Colorado file and its Geofabrik URL so a pytest skip says
    what to download and where it goes.
    """
    path = Path(path)
    if path.is_file():
        return None
    return (
        "OSM PBF MISSING: out/osm/colorado-latest.osm.pbf is not available "
        f"(looked at {path}). Download the Geofabrik Colorado extract from "
        f"{COLORADO_PBF_URL} into out/osm/ (that directory is git-ignored). "
        "Without it, PBF-cut graphs cannot be compared to the committed "
        "Overpass graphs for vail_co, eagle_co, and eagle_vail_co."
    )


def missing_osmium_reason() -> "str | None":
    """Loud skip text when the optional osmium extra is not installed."""
    try:
        import osmium  # noqa: F401
    except ImportError:
        return (
            "OSM PBF READER MISSING: osmium is not installed. Install the "
            f"optional extra with `{OSMIUM_INSTALL}` (pyosmium; tested wheel "
            "osmium-4.3.1-cp314-cp314-win_amd64). The engine and cached .wgr "
            "files stay stdlib-only; only this cutter imports osmium."
        )
    return None


# Equivalence tolerances vs a committed Overpass graph. The extract and the
# Overpass fetch are different days, so counts are not exact; the comparison
# still prints the measured delta rather than only pass/fail.
COUNT_TOL = 0.03            # node and edge counts, relative
LCC_TOL_PP = 1.0            # largest-component fraction, percentage points
SNAP_TOL_M = 50.0           # safe-zone snap distance, meters
ROUTE_TOL = 0.02            # roads.route distance, relative
SNAP_SEARCH_M = 20000.0     # how far nearest_node may look while measuring

# Grid cell for the on-disk way index. 0.05 deg is ~5.5 km; a preset bbox
# touches a few dozen cells instead of every highway way in the extract.
CELL_E7 = 500_000           # 0.05 * 1e7
INDEX_MAGIC = b"WGRIDX1\n"
INDEX_FORMAT = "wgridx-1"

_BBOX_IN_QUERY = re.compile(
    r"\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)\s*;")


def _highway_classes(pattern: str) -> frozenset:
    """Expand HIGHWAY_REGEX when it is a flat ``^(a|b|c)$`` alternation.

    The hot loop is a set lookup. If the fetcher's regex ever stops being
    that shape, fail at import rather than silently select a different set.
    """
    m = re.fullmatch(r"\^\((?P<body>[a-z0-9_|]+)\)\$", pattern)
    if not m:
        raise RuntimeError(
            f"HIGHWAY_REGEX is no longer a simple alternation: {pattern!r}")
    classes = frozenset(m.group("body").split("|"))
    compiled = re.compile(pattern)
    if not classes or any(compiled.fullmatch(c) is None for c in classes):
        raise RuntimeError("HIGHWAY_REGEX did not expand to itself")
    return classes


HIGHWAY_CLASSES = _highway_classes(HIGHWAY_REGEX)

_ITEMSIZE = {
    "q": array.array("q").itemsize,
    "Q": array.array("Q").itemsize,
    "i": array.array("i").itemsize,
    "I": array.array("I").itemsize,
    "H": array.array("H").itemsize,
}


class PbfDependencyError(RuntimeError):
    """osmium is not installed. The message names the install command."""


def _osmium_version() -> str:
    try:
        from importlib.metadata import version
        return version("osmium")
    except Exception:  # noqa: BLE001 — version is cosmetic
        return "unknown"


def import_osmium():
    """Import osmium or raise PbfDependencyError naming OSMIUM_INSTALL."""
    try:
        import osmium
    except ImportError as e:
        raise PbfDependencyError(
            "osmium is not installed. Cutting a road graph from an OSM PBF "
            "needs the optional reader extra (the engine and cached .wgr "
            f"files stay stdlib-only).\nInstall: {OSMIUM_INSTALL}\n"
            f"Import error: {e}") from e
    return osmium


def memory_mb() -> dict:
    """Process working set and commit, in MB. Empty dict off Windows.

    Peak working set is the high-water mark since process start (it does
    not reset between the index build and a later cut in the same process).
    ``argtypes`` is required: without it ctypes drops the call on Win64.
    """
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    pmc = _PMC()
    pmc.cb = ctypes.sizeof(pmc)
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    ok = fn(ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(pmc), pmc.cb)
    if not ok:
        return {}
    mb = 1024.0 * 1024.0
    return {
        "rss_mb": pmc.WorkingSetSize / mb,
        "peak_rss_mb": pmc.PeakWorkingSetSize / mb,
        "commit_mb": pmc.PagefileUsage / mb,
        "peak_commit_mb": pmc.PeakPagefileUsage / mb,
    }


def format_memory(mem: dict) -> str:
    if not mem:
        return "peak RSS n/a"
    return (f"peak RSS {mem['peak_rss_mb']:.0f} MB "
            f"(now {mem['rss_mb']:.0f} MB, peak commit {mem['peak_commit_mb']:.0f} MB)")


# ---------------------------------------------------------------------------
# BBox — preset keys use the exact box the Overpass query sent
# ---------------------------------------------------------------------------

def overpass_bbox_wsen(bbox: BBox) -> tuple:
    """(west, south, east, north) as ``overpass_query`` formats them.

    The live fetcher interpolates each edge with ``.6f`` before Overpass
    applies the filter. Re-parsing that query string keeps the PBF filter
    on the same four numbers the committed graph was built from, including
    halfway rounding.
    """
    q = overpass_query(bbox)
    m = _BBOX_IN_QUERY.search(q)
    if not m:
        raise RuntimeError("could not read the bbox out of overpass_query()")
    south, west, north, east = (float(v) for v in m.groups())
    return west, south, east, north


def preset_bbox(key: str) -> BBox:
    """The Overpass bbox for a simulator preset (6-decimal filter box)."""
    west, south, east, north = overpass_bbox_wsen(bbox_for(LOCATIONS[key]))
    return BBox(west=west, south=south, east=east, north=north)


def _bounds_e7(west: float, south: float, east: float, north: float) -> tuple:
    """Inclusive integer bounds at the OSM 1e-7 degree quantum."""
    return (int(round(west * 1e7)), int(round(south * 1e7)),
            int(round(east * 1e7)), int(round(north * 1e7)))


def pbf_timestamp(path) -> str:
    """Geofabrik/osmosis replication timestamp from the PBF header.

    ``osmosis_replication_timestamp`` is the extract's OSM data timestamp
    (what Geofabrik publishes). Empty headers yield ``"unknown"``.
    """
    osmium = import_osmium()
    reader = osmium.io.Reader(os.fspath(path))
    try:
        header = reader.header()
        for key in ("osmosis_replication_timestamp", "timestamp"):
            raw = (header.get(key) or "").strip()
            if raw:
                return _normalize_timestamp(raw)
    finally:
        reader.close()
    return "unknown"


def _normalize_timestamp(raw: str) -> str:
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    return raw


def source_label(path, timestamp: str) -> str:
    """Provenance ``source``: PBF file name plus its Geofabrik timestamp."""
    return f"{Path(path).name} (Geofabrik {timestamp})"


# ---------------------------------------------------------------------------
# Highway index (one pass over the extract, reused per domain)
# ---------------------------------------------------------------------------

def index_path_for(pbf_path) -> Path:
    """Cache path next to the extract: ``<file>.osm.pbf.wgridx``."""
    return Path(str(pbf_path) + ".wgridx")


def _payload_bytes(n_ways: int, n_refs: int, n_cells: int, n_grid: int) -> int:
    return (
        n_ways * _ITEMSIZE["q"]          # way ids
        + n_ways * _ITEMSIZE["H"]         # class index
        + (n_ways + 1) * _ITEMSIZE["Q"]   # node-ref offsets
        + n_refs * _ITEMSIZE["q"]         # node ids
        + n_refs * _ITEMSIZE["i"]         # lat e7
        + n_refs * _ITEMSIZE["i"]         # lon e7
        + n_cells * _ITEMSIZE["i"]        # cell ilat
        + n_cells * _ITEMSIZE["i"]        # cell ilon
        + n_cells * _ITEMSIZE["I"]        # cell start
        + n_cells * _ITEMSIZE["I"]        # cell count
        + n_grid * _ITEMSIZE["I"]         # way indices
    )


def _read_index_header(f) -> dict:
    magic = f.read(len(INDEX_MAGIC))
    if magic != INDEX_MAGIC:
        raise ValueError("not a wgridx file (bad magic)")
    raw_n = f.read(4)
    if len(raw_n) != 4:
        raise ValueError("truncated wgridx header")
    (n,) = struct.unpack("<I", raw_n)
    if n <= 0 or n > 20_000_000:
        raise ValueError(f"implausible wgridx header length {n}")
    blob = f.read(n)
    if len(blob) != n:
        raise ValueError("truncated wgridx json header")
    header = json.loads(blob.decode("utf-8"))
    if header.get("format") != INDEX_FORMAT:
        raise ValueError(
            f"wgridx format {header.get('format')!r} != {INDEX_FORMAT}")
    return header


def index_is_fresh(idx_path: Path, pbf_path: Path) -> bool:
    """True when the cache describes this PBF, regex, cell size, and endian."""
    if not idx_path.is_file():
        return False
    try:
        st = pbf_path.stat()
        with open(idx_path, "rb") as f:
            header = _read_index_header(f)
            pos = f.tell()
            f.seek(0, os.SEEK_END)
            size = f.tell()
    except (OSError, ValueError):
        return False
    payload = _payload_bytes(header.get("n_ways", -1), header.get("n_refs", -1),
                             header.get("n_cells", -1), header.get("n_grid", -1))
    return (
        header.get("pbf_size") == st.st_size
        and header.get("pbf_mtime_ns") == st.st_mtime_ns
        and header.get("highway_regex") == HIGHWAY_REGEX
        and header.get("cell_e7") == CELL_E7
        and header.get("endian") == sys.byteorder
        and header.get("itemsize") == _ITEMSIZE
        and header.get("payload_bytes") == payload
        and size - pos == payload
        and header.get("n_ways", 0) > 0
    )


class HighwayIndex:
    """Highway ways of one extract, addressed by a lat/lon grid.

    Coordinates are OSM 1e-7 degree integers (``location.y`` / ``location.x``).
    A query returns Overpass-shaped way dicts whose geometry is ``e7 / 1e7``.
    """

    def __init__(self):
        self.way_ids = array.array("q")
        self.class_idx = array.array("H")
        self.offsets = array.array("Q")
        self.node_ids = array.array("q")
        self.lat_e7 = array.array("i")
        self.lon_e7 = array.array("i")
        self.classes: list = []
        self.grid: dict = {}
        self.grid_ways = array.array("I")
        self.timestamp = "unknown"
        self.pbf_name = ""
        self.dropped_invalid = 0
        self.load_seconds = 0.0
        self.build_seconds = 0.0
        self.from_cache = False

    # -- build -----------------------------------------------------------

    @classmethod
    def build(cls, pbf_path, idx_path) -> "HighwayIndex":
        osmium = import_osmium()
        pbf_path = Path(pbf_path)
        idx_path = Path(idx_path)
        t0 = time.perf_counter()
        timestamp = pbf_timestamp(pbf_path)
        size_mb = pbf_path.stat().st_size / (1024.0 * 1024.0)
        ver = _osmium_version()
        print(f"indexing {pbf_path.name} ({size_mb:.0f} MB, Geofabrik "
              f"{timestamp}) with osmium {ver} ...", flush=True)
        print("  one pass: highway ways with node locations "
              "(flex_mem). Later domains reuse the index.", flush=True)

        collector = _WayCollector()
        collector.apply_file(os.fspath(pbf_path), locations=True, idx="flex_mem")
        idx = collector.finish()
        idx.timestamp = timestamp
        idx.pbf_name = pbf_path.name
        idx.build_seconds = time.perf_counter() - t0
        idx.from_cache = False
        idx.save(idx_path, pbf_path)
        mem = memory_mb()
        print(f"  index wrote {idx_path} "
              f"({idx_path.stat().st_size / (1024.0 * 1024.0):.0f} MB): "
              f"{len(idx.way_ids):,} ways, {len(idx.node_ids):,} node refs, "
              f"{len(idx.grid):,} cells, {idx.dropped_invalid:,} ways dropped "
              f"(missing node location), {idx.build_seconds:.1f} s, "
              f"{format_memory(mem)}", flush=True)
        return idx

    def save(self, idx_path: Path, pbf_path: Path) -> None:
        n_ways = len(self.way_ids)
        n_refs = len(self.node_ids)
        if len(self.offsets) != n_ways + 1:
            raise RuntimeError("way offset table does not match way count")
        items = sorted(self.grid.items())
        ilats = array.array("i")
        ilons = array.array("i")
        starts = array.array("I")
        counts = array.array("I")
        flat = array.array("I")
        # Query code sees the same (cell -> start, count) shape load() builds.
        # The collector's per-cell lists are dropped here.
        span_grid = {}
        cursor = 0
        for (ilat, ilon), wis in items:
            ilat = int(ilat)
            ilon = int(ilon)
            ilats.append(ilat)
            ilons.append(ilon)
            starts.append(cursor)
            counts.append(len(wis))
            span_grid[(ilat, ilon)] = (cursor, len(wis))
            flat.extend(wis)
            cursor += len(wis)
        self.grid = span_grid
        self.grid_ways = flat
        st = Path(pbf_path).stat()
        header = {
            "format": INDEX_FORMAT,
            "endian": sys.byteorder,
            "itemsize": _ITEMSIZE,
            "pbf_name": Path(pbf_path).name,
            "pbf_size": st.st_size,
            "pbf_mtime_ns": st.st_mtime_ns,
            "highway_regex": HIGHWAY_REGEX,
            "geofabrik_timestamp": self.timestamp,
            "cell_e7": CELL_E7,
            "classes": self.classes,
            "n_ways": n_ways,
            "n_refs": n_refs,
            "n_cells": len(items),
            "n_grid": len(flat),
            "dropped_invalid": self.dropped_invalid,
            "generator": GENERATOR,
            "payload_bytes": _payload_bytes(n_ways, n_refs, len(items), len(flat)),
        }
        blob = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        idx_path = Path(idx_path)
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(idx_path) + ".tmp")
        with open(tmp, "wb") as f:
            f.write(INDEX_MAGIC)
            f.write(struct.pack("<I", len(blob)))
            f.write(blob)
            for part in (self.way_ids, self.class_idx, self.offsets,
                         self.node_ids, self.lat_e7, self.lon_e7,
                         ilats, ilons, starts, counts, flat):
                part.tofile(f)
        os.replace(tmp, idx_path)

    @classmethod
    def load(cls, idx_path) -> "HighwayIndex":
        idx_path = Path(idx_path)
        idx = cls()
        with open(idx_path, "rb") as f:
            header = _read_index_header(f)
            payload = _payload_bytes(
                header["n_ways"], header["n_refs"],
                header["n_cells"], header["n_grid"])
            if header.get("payload_bytes") != payload:
                raise ValueError("wgridx payload_bytes does not match counts")
            if header.get("endian") != sys.byteorder:
                raise ValueError("wgridx was written on a different endianness")
            if header.get("itemsize") != _ITEMSIZE:
                raise ValueError("wgridx array item sizes do not match this Python")
            if header.get("highway_regex") != HIGHWAY_REGEX:
                raise ValueError("wgridx highway regex does not match this tool")
            if header.get("cell_e7") != CELL_E7:
                raise ValueError("wgridx cell size does not match this tool")
            n_ways = header["n_ways"]
            n_refs = header["n_refs"]
            n_cells = header["n_cells"]
            n_grid = header["n_grid"]
            idx.way_ids = _read_array(f, "q", n_ways)
            idx.class_idx = _read_array(f, "H", n_ways)
            idx.offsets = _read_array(f, "Q", n_ways + 1)
            idx.node_ids = _read_array(f, "q", n_refs)
            idx.lat_e7 = _read_array(f, "i", n_refs)
            idx.lon_e7 = _read_array(f, "i", n_refs)
            ilats = _read_array(f, "i", n_cells)
            ilons = _read_array(f, "i", n_cells)
            starts = _read_array(f, "I", n_cells)
            counts = _read_array(f, "I", n_cells)
            idx.grid_ways = _read_array(f, "I", n_grid)
            if f.read(1):
                raise ValueError("wgridx has trailing bytes")
        idx.classes = list(header["classes"])
        idx.timestamp = header.get("geofabrik_timestamp") or "unknown"
        idx.pbf_name = header.get("pbf_name") or ""
        idx.dropped_invalid = int(header.get("dropped_invalid") or 0)
        grid = {}
        for i in range(n_cells):
            grid[(int(ilats[i]), int(ilons[i]))] = (int(starts[i]), int(counts[i]))
        idx.grid = grid
        idx.from_cache = True
        return idx

    # -- query -----------------------------------------------------------

    def ways_for_bbox(self, west: float, south: float, east: float,
                      north: float) -> list:
        """Overpass-shaped ways with any node inside the inclusive box.

        Full geometry is returned (nodes outside the box stay). Way order is
        ascending OSM id.
        """
        west_e7, south_e7, east_e7, north_e7 = _bounds_e7(
            west, south, east, north)
        cell = CELL_E7
        ilat0, ilat1 = south_e7 // cell, north_e7 // cell
        ilon0, ilon1 = west_e7 // cell, east_e7 // cell
        cand = set()
        grid = self.grid
        gways = self.grid_ways
        for ilat in range(ilat0, ilat1 + 1):
            for ilon in range(ilon0, ilon1 + 1):
                span = grid.get((ilat, ilon))
                if span is None:
                    continue
                start, count = span
                cand.update(gways[start:start + count])
        way_ids = self.way_ids
        class_idx = self.class_idx
        offsets = self.offsets
        node_ids = self.node_ids
        lats = self.lat_e7
        lons = self.lon_e7
        classes = self.classes
        found = []
        for wi in sorted(cand):
            a = offsets[wi]
            b = offsets[wi + 1]
            hit = False
            for k in range(a, b):
                lat = lats[k]
                if lat < south_e7 or lat > north_e7:
                    continue
                lon = lons[k]
                if west_e7 <= lon <= east_e7:
                    hit = True
                    break
            if not hit:
                continue
            nids = []
            geom = []
            for k in range(a, b):
                nids.append(int(node_ids[k]))
                geom.append({"lat": lats[k] / 1e7, "lon": lons[k] / 1e7})
            found.append({
                "type": "way",
                "id": int(way_ids[wi]),
                "nodes": nids,
                "geometry": geom,
                "tags": {"highway": classes[class_idx[wi]]},
            })
        found.sort(key=lambda w: w["id"])
        return found


def _read_array(f, typecode: str, n: int):
    out = array.array(typecode)
    if n:
        out.fromfile(f, n)
    return out


class _WayCollector:
    """osmium handler: keep highway ways, bucketed by node cell.

    Created without subclassing at class-body import time so a missing
    osmium install still imports this module (tests, --help). ``apply_file``
    binds the real osmium base.
    """

    def apply_file(self, filename: str, locations: bool, idx: str) -> None:
        osmium = import_osmium()

        owner = self

        class _Handler(osmium.SimpleHandler):
            def way(self, w):
                owner._on_way(w)

        self._seen = 0
        self._kept = 0
        self.dropped_invalid = 0
        self._classes: list = []
        self._class_of: dict = {}
        self._way_ids = array.array("q")
        self._class_idx = array.array("H")
        self._offsets = array.array("Q", [0])
        self._node_ids = array.array("q")
        self._lat_e7 = array.array("i")
        self._lon_e7 = array.array("i")
        self._grid: dict = {}
        self._seen_ids: set = set()
        self._t0 = time.perf_counter()
        _Handler().apply_file(filename, locations=locations, idx=idx)

    def _on_way(self, w) -> None:
        self._seen += 1
        if self._seen % 500_000 == 0:
            dt = time.perf_counter() - self._t0
            mem = memory_mb()
            rss = f", RSS {mem['rss_mb']:.0f} MB" if mem else ""
            print(f"  scanned {self._seen:,} ways, kept {self._kept:,} "
                  f"({dt:.0f} s{rss})", flush=True)
        cls = w.tags.get("highway")
        if cls not in HIGHWAY_CLASSES:
            return
        wid = int(w.id)
        if wid in self._seen_ids:
            return
        ids = []
        lats = []
        lons = []
        for nd in w.nodes:
            loc = nd.location
            if not loc.valid():
                self.dropped_invalid += 1
                return
            ids.append(int(nd.ref))
            lats.append(int(loc.y))
            lons.append(int(loc.x))
        if len(ids) < 2:
            self.dropped_invalid += 1
            return
        self._seen_ids.add(wid)
        ci = self._class_of.get(cls)
        if ci is None:
            ci = len(self._classes)
            self._classes.append(cls)
            self._class_of[cls] = ci
        wi = self._kept
        self._way_ids.append(wid)
        self._class_idx.append(ci)
        self._node_ids.extend(ids)
        self._lat_e7.extend(lats)
        self._lon_e7.extend(lons)
        self._offsets.append(len(self._node_ids))
        cells = set()
        for lat, lon in zip(lats, lons):
            cells.add((lat // CELL_E7, lon // CELL_E7))
        grid = self._grid
        for cell in cells:
            bucket = grid.get(cell)
            if bucket is None:
                grid[cell] = [wi]
            else:
                bucket.append(wi)
        self._kept += 1

    def finish(self) -> HighwayIndex:
        idx = HighwayIndex()
        idx.way_ids = self._way_ids
        idx.class_idx = self._class_idx
        idx.offsets = self._offsets
        idx.node_ids = self._node_ids
        idx.lat_e7 = self._lat_e7
        idx.lon_e7 = self._lon_e7
        idx.classes = self._classes
        idx.grid = self._grid
        idx.dropped_invalid = self.dropped_invalid
        print(f"  scanned {self._seen:,} ways, kept {self._kept:,} highway "
              f"ways", flush=True)
        return idx


_INDEX_CACHE: dict = {}


def get_index(pbf_path, rebuild: bool = False) -> HighwayIndex:
    """Load or build the highway index for ``pbf_path``. Memoized per process."""
    pbf_path = Path(pbf_path)
    if not pbf_path.is_file():
        raise FileNotFoundError(
            f"OSM PBF not found: {pbf_path}. Download a Geofabrik extract "
            "covering the bbox (Colorado presets use "
            "https://download.geofabrik.de/north-america/us/colorado-latest.osm.pbf) "
            "into out/osm/.")
    st = pbf_path.stat()
    cache_key = (str(pbf_path.resolve()), st.st_size, st.st_mtime_ns)
    if not rebuild and cache_key in _INDEX_CACHE:
        idx = _INDEX_CACHE[cache_key]
        # Already resident: don't report the original build as this cut's cost.
        idx.from_cache = True
        idx.build_seconds = 0.0
        idx.load_seconds = 0.0
        return idx
    idx_path = index_path_for(pbf_path)
    if not rebuild and index_is_fresh(idx_path, pbf_path):
        t0 = time.perf_counter()
        print(f"index cache {idx_path.name} matches {pbf_path.name}; loading ...",
              flush=True)
        idx = HighwayIndex.load(idx_path)
        idx.load_seconds = time.perf_counter() - t0
        print(f"  loaded {len(idx.way_ids):,} ways in {idx.load_seconds:.1f} s, "
              f"{format_memory(memory_mb())}", flush=True)
    else:
        if idx_path.exists() and not rebuild:
            print(f"index cache {idx_path.name} is stale; rebuilding", flush=True)
        idx = HighwayIndex.build(pbf_path, idx_path)
    _INDEX_CACHE[cache_key] = idx
    return idx


# ---------------------------------------------------------------------------
# Cut
# ---------------------------------------------------------------------------

def cut_road_graph(pbf_path, bbox: BBox, *, key: str = "", name: str = "",
                   fetched_utc: str = None, index: HighwayIndex = None):
    """Build a RoadGraph for ``bbox`` from the extract.

    ``index`` may be a HighwayIndex already loaded by the caller (tests cut
    several keys without re-reading the cache). Returns (graph, timing)
    where timing has index_seconds, cut_seconds, from_cache, peak memory.
    """
    t_all = time.perf_counter()
    idx = index if index is not None else get_index(pbf_path)
    t_cut = time.perf_counter()
    ways = idx.ways_for_bbox(bbox.west, bbox.south, bbox.east, bbox.north)
    if not ways:
        raise RuntimeError(
            f"no highway ways with a node inside bbox "
            f"W {bbox.west:.6f} S {bbox.south:.6f} "
            f"E {bbox.east:.6f} N {bbox.north:.6f}")
    if fetched_utc is None:
        fetched_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # fetch_seconds is filled after simplification; build_graph copies meta.
    meta = {
        "key": key,
        "name": name,
        "bbox_wsen": [bbox.west, bbox.south, bbox.east, bbox.north],
        "source": source_label(pbf_path, idx.timestamp),
        "pbf_timestamp": idx.timestamp,
        "highway_regex": HIGHWAY_REGEX,
        "fetched_utc": fetched_utc,
        "generator": GENERATOR,
    }
    graph, stats = build_graph(ways, meta)
    cut_s = time.perf_counter() - t_cut
    index_s = idx.build_seconds or idx.load_seconds
    graph.meta["fetch_seconds"] = round(cut_s, 2)
    graph.meta["index_seconds"] = round(index_s, 2)
    timing = {
        "index_seconds": index_s,
        "index_from_cache": bool(idx.from_cache),
        "cut_seconds": cut_s,
        "total_seconds": time.perf_counter() - t_all,
        "ways": len(ways),
        "memory": memory_mb(),
        "stats": stats,
    }
    return graph, timing


def default_out_path(key: str) -> Path:
    """Where a preset graph is written when ``--out`` is omitted.

    Under ``out/`` (git-ignored), never ``data/roads/<key>.wgr``.
    """
    return Path(_REPO_ROOT) / "out" / "roads" / f"{key}.wgr"


def is_protected_committed_graph(path) -> bool:
    """True when ``path`` is a git-tracked file under ``data/roads``.

    Those are the Overpass graphs the simulator ships. A PBF cut must not
    replace one unless the operator passes ``--force``.
    """
    path = Path(path)
    try:
        resolved = path.resolve()
        root = Path(_REPO_ROOT).resolve()
        roads = (root / "data" / "roads").resolve()
    except OSError:
        return False
    if resolved.parent != roads or resolved.suffix.lower() != ".wgr":
        return False
    if not resolved.is_file():
        return False
    rel = resolved.relative_to(root).as_posix()
    try:
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            cwd=_REPO_ROOT, capture_output=True, text=True)
    except OSError:
        return True
    if r.returncode == 0:
        return True
    # git itself failed (not a "not tracked" answer) — protect the file.
    if r.returncode != 1:
        return True
    return False


# ---------------------------------------------------------------------------
# Comparison against a committed Overpass graph
# ---------------------------------------------------------------------------

def wui_pair(g, key: str):
    """Two road-adjacent points, same draws as ``tests/test_roads.py:_wui_pair``.

    Computed on the committed (Overpass) graph so both graphs are routed
    between the same coordinates.
    """
    lats = [p.lat for p in g.nodes.values()]
    lons = [p.lon for p in g.nodes.values()]
    lat_lo, lat_hi = min(lats), max(lats)
    lon_lo, lon_hi = min(lons), max(lons)
    lat_lo, lat_hi = (lat_lo + 0.2 * (lat_hi - lat_lo),
                      lat_hi - 0.2 * (lat_hi - lat_lo))
    lon_lo, lon_hi = (lon_lo + 0.2 * (lon_hi - lon_lo),
                      lon_hi - 0.2 * (lon_hi - lon_lo))
    rng = seeded_rng("roads-wui-pair", key)
    pts = []
    for _ in range(400):
        p = LatLon(lat_lo + rng.random() * (lat_hi - lat_lo),
                   lon_lo + rng.random() * (lon_hi - lon_lo))
        if g.nearest_node(p, max_m=1000.0) is None:
            continue
        if pts and geo.haversine_m(pts[0], p) < 3000.0:
            continue
        pts.append(p)
        if len(pts) == 2:
            return pts
    raise RuntimeError(
        f"{key}: could not draw two road-adjacent points >=3 km apart in "
        f"400 seeded draws")


def _snap(g, pt: LatLon):
    """(dist_m, node_id or None). dist is None when nothing is within range."""
    hit = g.nearest_node(pt, max_m=SNAP_SEARCH_M)
    if hit is None:
        return None, None
    nid, dist = hit
    return dist, nid


@dataclass
class Check:
    name: str
    overpass: str
    pbf: str
    delta: str
    ok: bool
    note: str = ""


@dataclass
class Comparison:
    key: str
    checks: list = field(default_factory=list)
    timing_note: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def compare_graphs(overpass, pbf, key: str, timing: dict = None) -> Comparison:
    """Quantify PBF vs the committed Overpass graph for one preset key.

    Gates (all printed, not just asserted): node and edge counts within
    ±3%, largest-component fraction within 1 percentage point, every safe
    zone's snap distance within ±50 m, and ``roads.route`` between the
    shared WUI pair with the same ``on_roads`` flag and distance within 2%.
    The location center is reported the same way and is part of the gate:
    it is a travel endpoint the simulator snaps, same tolerance.
    """
    loc = LOCATIONS[key]
    cmp = Comparison(key=key)
    n_ov, n_pbf = len(overpass.nodes), len(pbf.nodes)
    e_ov, e_pbf = len(overpass.edges), len(pbf.edges)
    cmp.checks.append(_count_check("nodes", n_ov, n_pbf))
    cmp.checks.append(_count_check("edges", e_ov, e_pbf))
    lcc_ov = float(overpass.meta.get("largest_component_node_pct"))
    lcc_pbf = float(pbf.meta.get("largest_component_node_pct"))
    d_lcc = lcc_pbf - lcc_ov
    cmp.checks.append(Check(
        name="largest_component_pct",
        overpass=f"{lcc_ov:.2f}",
        pbf=f"{lcc_pbf:.2f}",
        delta=f"{d_lcc:+.2f} pp",
        ok=abs(d_lcc) <= LCC_TOL_PP,
        note=f"tolerance ±{LCC_TOL_PP:.0f} percentage point",
    ))
    ways_ov = overpass.meta.get("ways_fetched")
    ways_pbf = pbf.meta.get("ways_fetched")
    if ways_ov is not None and ways_pbf is not None and ways_ov:
        rel = 100.0 * (ways_pbf - ways_ov) / ways_ov
        cmp.checks.append(Check(
            name="ways_fetched",
            overpass=f"{ways_ov}",
            pbf=f"{ways_pbf}",
            delta=f"{rel:+.2f}%",
            ok=True,
            note="informational (not gated; extract age moves this)",
        ))
    points = [("center", loc.center)]
    for z in loc.safe_zones:
        points.append((z.zone_id, z.loc))
    for label, pt in points:
        cmp.checks.append(_snap_check(overpass, pbf, label, pt))
    a, b = wui_pair(overpass, key)
    cfg = ResponseConfig()
    r_ov = route(overpass, a, b, cfg)
    r_pbf = route(pbf, a, b, cfg)
    same_flag = bool(r_ov.on_roads) == bool(r_pbf.on_roads)
    cmp.checks.append(Check(
        name="route_on_roads",
        overpass=str(bool(r_ov.on_roads)),
        pbf=str(bool(r_pbf.on_roads)),
        delta="match" if same_flag else "DIFFER",
        ok=same_flag,
        note=(f"WUI pair ({a.lat:.5f},{a.lon:.5f}) -> "
              f"({b.lat:.5f},{b.lon:.5f})"),
    ))
    if r_ov.dist_m == 0:
        dist_ok = r_pbf.dist_m == 0
        dist_delta = "0" if dist_ok else f"{r_pbf.dist_m:.1f} m vs 0"
    else:
        rel = (r_pbf.dist_m - r_ov.dist_m) / r_ov.dist_m
        dist_ok = abs(rel) <= ROUTE_TOL
        dist_delta = f"{100.0 * rel:+.2f}%"
    cmp.checks.append(Check(
        name="route_dist_m",
        overpass=f"{r_ov.dist_m:.1f}",
        pbf=f"{r_pbf.dist_m:.1f}",
        delta=dist_delta,
        ok=dist_ok,
        note=f"tolerance ±{100.0 * ROUTE_TOL:.0f}%",
    ))
    if timing:
        mem = timing.get("memory") or {}
        idx = "cached" if timing.get("index_from_cache") else "built"
        cmp.timing_note = (
            f"index {timing['index_seconds']:.2f} s ({idx}), "
            f"cut {timing['cut_seconds']:.2f} s, "
            f"total {timing['total_seconds']:.2f} s, {format_memory(mem)}"
        )
    return cmp


def _count_check(name: str, old: int, new: int) -> Check:
    if old == 0:
        ok = new == 0
        delta = "0" if ok else "n/a (overpass 0)"
    else:
        rel = (new - old) / old
        ok = abs(rel) <= COUNT_TOL
        delta = f"{100.0 * rel:+.2f}%"
    return Check(
        name=name,
        overpass=f"{old}",
        pbf=f"{new}",
        delta=delta,
        ok=ok,
        note=f"tolerance ±{100.0 * COUNT_TOL:.0f}%",
    )


def _snap_check(overpass, pbf, label: str, pt: LatLon) -> Check:
    d_ov, n_ov = _snap(overpass, pt)
    d_pbf, n_pbf = _snap(pbf, pt)
    if d_ov is None or d_pbf is None:
        return Check(
            name=f"snap {label}",
            overpass="NONE" if d_ov is None else f"{d_ov:.1f} m",
            pbf="NONE" if d_pbf is None else f"{d_pbf:.1f} m",
            delta="missing snap",
            ok=False,
            note=f"searched {SNAP_SEARCH_M:.0f} m",
        )
    gap = abs(d_pbf - d_ov)
    sep = geo.haversine_m(overpass.nodes[n_ov], pbf.nodes[n_pbf])
    return Check(
        name=f"snap {label}",
        overpass=f"{d_ov:.1f} m",
        pbf=f"{d_pbf:.1f} m",
        delta=f"|Δ| {gap:.1f} m",
        ok=gap <= SNAP_TOL_M,
        note=(f"tolerance ±{SNAP_TOL_M:.0f} m; "
              f"snap points {sep:.1f} m apart (informational)"),
    )


def format_comparison(cmp: Comparison) -> str:
    """Monospaced comparison table, one check per row, deltas included."""
    name_w = max(len(c.name) for c in cmp.checks)
    lines = [
        f"---- {cmp.key}  PBF vs committed Overpass graph  "
        f"{'OK' if cmp.ok else 'FAIL'} ----",
        f"{'check':<{name_w}}  {'overpass':>14}  {'pbf':>14}  "
        f"{'delta':>12}  result",
    ]
    for c in cmp.checks:
        flag = "OK" if c.ok else "FAIL"
        lines.append(
            f"{c.name:<{name_w}}  {c.overpass:>14}  {c.pbf:>14}  "
            f"{c.delta:>12}  {flag}"
            + (f"   {c.note}" if c.note else ""))
    if cmp.timing_note:
        lines.append(cmp.timing_note)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_request(args):
    """Return (bbox, key, name, out_path) or exit via SystemExit-like return.

    Validation is separate so tests can call ``main`` without a PBF when the
    request is rejected on flags alone.
    """
    if bool(args.key) == bool(args.bbox):
        raise ValueError("pass exactly one of --key or --bbox W,S,E,N")
    if args.compare and not args.key:
        raise ValueError("--compare needs --key (it diffs a committed preset graph)")
    if args.bbox and not args.out:
        raise ValueError("--bbox requires --out")
    if args.key:
        bbox = preset_bbox(args.key)
        name = LOCATIONS[args.key].name
        out = Path(args.out) if args.out else default_out_path(args.key)
        return bbox, args.key, name, out
    bbox = _parse_bbox(args.bbox)
    out = Path(args.out)
    return bbox, "", out.stem, out


def _parse_bbox(text: str) -> BBox:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be W,S,E,N (four comma-separated degrees)")
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as e:
        raise ValueError(
            "--bbox must be W,S,E,N (four comma-separated degrees)") from e
    if not (west < east and south < north):
        raise ValueError("--bbox requires west < east and south < north")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0
            and -90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise ValueError("--bbox degrees out of range")
    return BBox(west=west, south=south, east=east, north=north)


def _write_or_refuse(graph, out: Path, force: bool) -> str:
    """Save ``graph`` to ``out``. Return 'wrote' or 'skipped'.

    A git-tracked data/roads graph is never replaced unless ``force``.
    Any other existing file is left in place unless ``force`` (idempotent
    re-run). Raises FileExistsError when a protected graph is the target
    and force is not set — callers print it and exit 2.
    """
    out = Path(out)
    if out.exists() and not force:
        if is_protected_committed_graph(out):
            raise FileExistsError(
                f"refusing to overwrite committed road graph {out} "
                f"without --force")
        print(f"exists — leaving in place (idempotent; --force to rewrite): "
              f"{out}", flush=True)
        return "skipped"
    if out.exists() and force and is_protected_committed_graph(out):
        print(f"--force: replacing committed road graph {out}", flush=True)
    save_road_graph(graph, out)
    kb = out.stat().st_size / 1024.0
    print(f"saved {out} ({kb:,.0f} KB): nodes {len(graph.nodes):,} "
          f"edges {len(graph.edges):,} "
          f"LCC {graph.meta.get('largest_component_node_pct')}% "
          f"source {graph.meta.get('source')}", flush=True)
    return "wrote"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Cut a simulator .wgr road graph from a local OSM PBF "
                    "extract (same simplification as tools/fetch_sim_roads.py).")
    ap.add_argument("--pbf", required=True,
                    help="Geofabrik .osm.pbf extract covering the bbox")
    ap.add_argument("--key", choices=sorted(LOCATIONS),
                    help="simulator preset; bbox = fetch_sim_roads.bbox_for")
    ap.add_argument("--bbox", metavar="W,S,E,N",
                    help="explicit west,south,east,north degrees (requires --out)")
    ap.add_argument("--out",
                    help="output .wgr path (default for --key: out/roads/<key>.wgr). "
                         "A committed data/roads/<key>.wgr is not overwritten "
                         "unless --force")
    ap.add_argument("--compare", action="store_true",
                    help="print the equivalence table against the committed "
                         "Overpass graph for --key")
    ap.add_argument("--force", action="store_true",
                    help="rewrite --out even if it exists, including a "
                         "committed data/roads graph")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="ignore an existing <pbf>.wgridx and scan the extract again")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        code = e.code
        return code if isinstance(code, int) else 2
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    try:
        bbox, key, name, out = _resolve_request(args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Refuse / skip before touching the extract so a bad --out cannot spend
    # a multi-minute index build, and so a missing PBF is not required to
    # prove the committed-graph guard.
    if not args.compare:
        try:
            if out.exists() and not args.force:
                if is_protected_committed_graph(out):
                    print(f"error: refusing to overwrite committed road graph "
                          f"{out} without --force", file=sys.stderr)
                    return 2
                print(f"exists — leaving in place (idempotent; --force to "
                      f"rewrite): {out}")
                return 0
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    t_wall = time.perf_counter()
    pbf_path = Path(args.pbf)
    if not pbf_path.is_file():
        print(f"error: OSM PBF not found: {pbf_path}", file=sys.stderr)
        print("Download a Geofabrik extract covering the bbox. Colorado "
              "presets: https://download.geofabrik.de/north-america/us/"
              "colorado-latest.osm.pbf into out/osm/.", file=sys.stderr)
        return 1

    try:
        if args.rebuild_index:
            # Drop the memo so this process rescans.
            _INDEX_CACHE.clear()
            idx = get_index(pbf_path, rebuild=True)
        else:
            idx = get_index(pbf_path)
        graph, timing = cut_road_graph(
            pbf_path, bbox, key=key, name=name, index=idx)
    except PbfDependencyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — CLI reports the failure and exits
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    timing["total_seconds"] = time.perf_counter() - t_wall
    timing["memory"] = memory_mb()
    print(f"cut {key or out.stem}: ways {timing['ways']:,}  "
          f"nodes {timing['stats']['nodes']:,}  "
          f"edges {timing['stats']['edges']:,}  "
          f"LCC {timing['stats']['largest_component_node_pct']}%  "
          f"index {timing['index_seconds']:.2f} s "
          f"({'cached' if timing['index_from_cache'] else 'built'})  "
          f"cut {timing['cut_seconds']:.2f} s  "
          f"wall {timing['total_seconds']:.2f} s  "
          f"{format_memory(timing['memory'])}", flush=True)

    rc = 0
    if args.compare:
        committed = load_road_graph(key)
        if committed is None:
            print(f"error: committed graph data/roads/{key}.wgr is missing; "
                  f"nothing to compare", file=sys.stderr)
            return 1
        # Reload through the .wgr writer so 1e-5 deg / 0.1 m quantization
        # matches the committed file the simulator actually routes on.
        cmp_dir = Path(tempfile.mkdtemp(prefix="wgr_cmp_"))
        try:
            save_road_graph(graph, cmp_dir / f"{key}.wgr")
            reloaded = load_road_graph(key, road_dir=cmp_dir)
            if reloaded is None:
                print("error: comparison reload failed", file=sys.stderr)
                return 1
            report = compare_graphs(committed, reloaded, key, timing)
        finally:
            shutil.rmtree(cmp_dir, ignore_errors=True)
        print(format_comparison(report), flush=True)
        if not report.ok:
            rc = 1

    try:
        _write_or_refuse(graph, out, args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2 if rc == 0 else rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
