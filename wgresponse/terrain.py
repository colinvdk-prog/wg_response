"""wgresponse.terrain — terrain/fuel providers (contracts.TerrainProvider).

    SyntheticTerrain   deterministic tiles: uniform grass/chaparral fuel, mixed
                       patches, optional central NE-SW ridge (slope/aspect
                       derived consistently from the elevation surface)
    LfpsTerrain        live LANDFIRE Product Service (lfps.usgs.gov) job API:
                       LF2024_FBFM40 + LF2020_SlpD/Asp/Elev in one aligned
                       GeoTIFF; optional tifffile/numpy decode; disk cache
    load_ascii_tile /  dependency-free text cache format so a live tile is
    save_ascii_tile    fetched once and reused forever without tifffile/numpy

Units and grid law (contracts): grids row-major, row 0 = NORTH edge, col 0 =
west; cell_m meters; fuel raster holds FBFM40 integer codes as floats
(non-burnable 91-99); slope_deg degrees; aspect_deg = DOWNSLOPE-FACING compass
direction (0 = N, CW), -1.0 for flat cells (LANDFIRE convention); elev_m
meters. Degrees<->meters here use 111,320 m per degree latitude and
cos(lat)-scaled longitude (geo.py owns the canonical helpers).

Endpoint shapes per docs/research/weather_terrain_fuel.md §4.2 (live-verified
2026-07-06). Third-party deps (tifffile, numpy, imagecodecs — real LFPS tiles
are LZW-compressed, so tifffile alone cannot decode them; ops review OPS-2)
are OPTIONAL and probed for importability BEFORE an LFPS job is submitted;
when any is missing the LIVE path raises a clear RuntimeError naming all
three pips while cached/ascii/synthetic paths keep working (see
requirements.txt [live] extras). Mock generation uses contracts.seeded_rng —
never wall-clock.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
import zipfile
from typing import Callable, Optional

from .contracts import BBox, GridSpec, Raster, TerrainTile, seeded_rng

log = logging.getLogger(__name__)

HttpGet = Callable[[str, dict], "tuple[int, bytes]"]

M_PER_DEG_LAT = 111_320.0
FLAT_ASPECT = -1.0          # LANDFIRE Asp convention for flat cells


def _default_http_get(url: str, headers: Optional[dict] = None,
                      timeout_s: float = 120.0) -> "tuple[int, bytes]":
    """Stdlib urllib GET -> (HTTP status, body bytes)."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.getcode(), resp.read()


def _m_per_deg_lon(lat_deg: float) -> float:
    """Meters per degree of longitude at latitude lat_deg."""
    return M_PER_DEG_LAT * max(0.05, math.cos(math.radians(lat_deg)))


def _grid_for_bbox(bbox: BBox, cell_m: float) -> GridSpec:
    """GridSpec covering bbox at cell_m meters (row 0 = north edge)."""
    lat_c = (bbox.north + bbox.south) / 2.0
    nrows = max(1, int(round((bbox.north - bbox.south) * M_PER_DEG_LAT / cell_m)))
    ncols = max(1, int(round((bbox.east - bbox.west) * _m_per_deg_lon(lat_c) / cell_m)))
    return GridSpec(north_lat=bbox.north, west_lon=bbox.west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)


def _slope_aspect(elev: Raster) -> "tuple[Raster, Raster]":
    """Derive slope_deg + aspect_deg rasters from an elevation raster.

    Central differences (one-sided at edges) on the metric grid. Aspect is the
    compass direction of steepest DESCENT (downslope-facing), degrees 0-360,
    FLAT_ASPECT (-1.0) where the surface is flat."""
    spec = elev.spec
    cell = spec.cell_m
    slope = Raster(spec)
    aspect = Raster(spec, fill=FLAT_ASPECT)
    for r in range(spec.nrows):
        r_n, r_s = max(r - 1, 0), min(r + 1, spec.nrows - 1)   # row 0 = north
        for c in range(spec.ncols):
            c_w, c_e = max(c - 1, 0), min(c + 1, spec.ncols - 1)
            gx = ((elev.get(r, c_e) - elev.get(r, c_w)) / ((c_e - c_w) * cell)
                  if c_e > c_w else 0.0)                       # d(elev)/d(east)
            gy = ((elev.get(r_n, c) - elev.get(r_s, c)) / ((r_s - r_n) * cell)
                  if r_s > r_n else 0.0)                       # d(elev)/d(north)
            g = math.hypot(gx, gy)
            if g < 1e-9:
                continue
            slope.set(r, c, math.degrees(math.atan(g)))
            # steepest descent = -gradient; compass bearing = atan2(east, north)
            aspect.set(r, c, math.degrees(math.atan2(-gx, -gy)) % 360.0)
    return slope, aspect


class SyntheticTerrain:
    """Deterministic synthetic terrain tiles for tests/simulation.

    kind:
      'grass'      uniform GR2 (102), flat
      'chaparral'  uniform SH5 (145), flat
      'mixed'      blocky seeded patches of GR2/GS2/SH5/TL3, flat
      'ridge'      mixed patches + a central NE-SW ridge (Gaussian cross
                   section, height ridge_height_m); slope/aspect follow the
                   elevation surface, aspect facing downslope.
    Same (seed, kind, bbox, cell_m) always yields byte-identical tiles
    (contracts.seeded_rng keyed per fuel patch). Units: meters/degrees per
    module docstring."""

    KINDS = ("grass", "chaparral", "mixed", "ridge")
    _UNIFORM_FUEL = {"grass": 102.0, "chaparral": 145.0}
    _MIX_CODES = (102.0, 122.0, 145.0, 183.0)   # GR2, GS2, SH5, TL3

    def __init__(self, seed: object, kind: str = "grass",
                 base_elev_m: float = 100.0, ridge_height_m: float = 400.0,
                 patch_cells: int = 4):
        if kind not in self.KINDS:
            raise ValueError(f"kind must be one of {self.KINDS}, got {kind!r}")
        self._seed = seed
        self._kind = kind
        self._base_elev_m = base_elev_m
        self._ridge_height_m = ridge_height_m
        self._patch_cells = max(1, patch_cells)

    def tile(self, bbox: BBox, cell_m: float) -> TerrainTile:
        """Deterministic TerrainTile covering bbox at cell_m meters."""
        spec = _grid_for_bbox(bbox, cell_m)
        fuel = self._fuel_raster(spec)
        elev = self._elev_raster(spec)
        slope, aspect = _slope_aspect(elev)
        return TerrainTile(spec=spec, fuel=fuel, slope_deg=slope,
                           aspect_deg=aspect, elev_m=elev,
                           source=f"synthetic:{self._kind}:{self._seed}")

    def _fuel_raster(self, spec: GridSpec) -> Raster:
        fuel = Raster(spec)
        uniform = self._UNIFORM_FUEL.get(self._kind)
        if uniform is not None:
            fuel.data = [uniform] * (spec.nrows * spec.ncols)
            return fuel
        # mixed / ridge: blocky patches, rng keyed per patch (order-independent)
        cache: dict = {}
        for r in range(spec.nrows):
            br = r // self._patch_cells
            for c in range(spec.ncols):
                bc = c // self._patch_cells
                code = cache.get((br, bc))
                if code is None:
                    rng = seeded_rng(self._seed, "fuelpatch", br, bc)
                    code = rng.choice(self._MIX_CODES)
                    cache[(br, bc)] = code
                fuel.set(r, c, code)
        return fuel

    def _elev_raster(self, spec: GridSpec) -> Raster:
        elev = Raster(spec, fill=self._base_elev_m)
        if self._kind != "ridge":
            return elev
        w_m = spec.ncols * spec.cell_m
        h_m = spec.nrows * spec.cell_m
        cx, cy = w_m / 2.0, h_m / 2.0
        ux = uy = math.sqrt(0.5)                     # ridge axis: NE (east, north)
        sigma = max(2.0 * spec.cell_m, min(w_m, h_m) / 6.0)
        for r in range(spec.nrows):
            y = (spec.nrows - r - 0.5) * spec.cell_m  # meters north of south edge
            for c in range(spec.ncols):
                x = (c + 0.5) * spec.cell_m           # meters east of west edge
                d = abs((x - cx) * uy - (y - cy) * ux)  # perp distance to axis
                elev.set(r, c, self._base_elev_m
                         + self._ridge_height_m * math.exp(-(d / sigma) ** 2))
        return elev


# ---------------------------------------------------------------------------
# Dependency-free ascii tile cache format
# ---------------------------------------------------------------------------

_BANDS = ("FUEL", "SLOPE_DEG", "ASPECT_DEG", "ELEV_M")


def save_ascii_tile(tile: TerrainTile, path: str) -> None:
    """Write a TerrainTile to the dependency-free text format.

    Line 1 header: ``nrows ncols cell_m north_lat west_lon`` (floats via repr
    for exact round-trip); line 2: ``source <str>``; then the 4 band blocks
    (FUEL, SLOPE_DEG, ASPECT_DEG, ELEV_M), each a name line + nrows rows of
    ncols space-separated repr floats. Units per contracts.TerrainTile."""
    spec = tile.spec
    lines = [f"{spec.nrows} {spec.ncols} {spec.cell_m!r} "
             f"{spec.north_lat!r} {spec.west_lon!r}",
             f"source {tile.source}"]
    for name, band in zip(_BANDS, (tile.fuel, tile.slope_deg,
                                   tile.aspect_deg, tile.elev_m)):
        lines.append(name)
        for r in range(spec.nrows):
            row = band.data[r * spec.ncols:(r + 1) * spec.ncols]
            lines.append(" ".join(repr(v) for v in row))
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def load_ascii_tile(path: str) -> TerrainTile:
    """Read a TerrainTile written by save_ascii_tile (exact float round-trip)."""
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    if not lines:
        raise ValueError(f"empty ascii tile: {path}")
    head = lines[0].split()
    if len(head) != 5:
        raise ValueError(f"bad ascii tile header in {path}: {lines[0]!r}")
    nrows, ncols = int(head[0]), int(head[1])
    spec = GridSpec(north_lat=float(head[3]), west_lon=float(head[4]),
                    cell_m=float(head[2]), nrows=nrows, ncols=ncols)
    i = 1
    source = "ascii"
    if i < len(lines) and lines[i].startswith("source "):
        source = lines[i][len("source "):]
        i += 1
    bands: dict = {}
    while i < len(lines):
        name = lines[i].strip()
        if not name:
            i += 1
            continue
        if name not in _BANDS:
            raise ValueError(f"unknown band {name!r} in {path}")
        i += 1
        data: list = []
        for _r in range(nrows):
            row = [float(tok) for tok in lines[i].split()]
            if len(row) != ncols:
                raise ValueError(f"band {name} row {_r} has {len(row)} cols, "
                                 f"expected {ncols} in {path}")
            data.extend(row)
            i += 1
        bands[name] = Raster(spec, data=data)
    missing = [b for b in _BANDS if b not in bands]
    if missing:
        raise ValueError(f"ascii tile {path} missing bands: {missing}")
    return TerrainTile(spec=spec, fuel=bands["FUEL"], slope_deg=bands["SLOPE_DEG"],
                       aspect_deg=bands["ASPECT_DEG"], elev_m=bands["ELEV_M"],
                       source=source)


# ---------------------------------------------------------------------------
# Live LANDFIRE Product Service
# ---------------------------------------------------------------------------

# Optional deps of the LIVE GeoTIFF decode path, in pip-install order. ALL
# three are required: real LFPS GeoTIFFs are LZW-compressed and tifffile
# raises `ValueError: <COMPRESSION.LZW: 5> requires the 'imagecodecs'
# package` without the codec (measured live — ops review OPS-2).
_LIVE_DECODE_DEPS = ("tifffile", "numpy", "imagecodecs")
_LIVE_DECODE_PIP = "pip install tifffile numpy imagecodecs"
_LIVE_DECODE_HINT = (
    "LfpsTerrain live GeoTIFF decode requires optional deps ({missing}): "
    f"{_LIVE_DECODE_PIP} — see requirements.txt '[live] extras'. Cached "
    "tiles keep working without them (load_ascii_tile/save_ascii_tile)."
)


def probe_live_decode_deps(import_module: Optional[Callable] = None) -> None:
    """Raise RuntimeError naming ALL live-decode pips if any dep is missing.

    Called by LfpsTerrain.tile() BEFORE submitting an LFPS job: a submit +
    poll + download burns 90–180 s of wall clock and an LFPS server job, and
    the ops review (OPS-2) measured exactly that being wasted when the decode
    then died on the LZW codec. Probing importability first fails in
    milliseconds with the complete install instruction instead.

    ``import_module`` is injectable for tests (defaults to
    importlib.import_module); the probe imports each dep by name and treats
    ImportError as missing. No-op when all three import."""
    imp = import_module or importlib.import_module
    missing = []
    for mod in _LIVE_DECODE_DEPS:
        try:
            imp(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise RuntimeError(
            _LIVE_DECODE_HINT.format(missing="missing: " + ", ".join(missing)))


class LfpsTerrain:
    """Live LANDFIRE tiles via the LFPS job API (lfps.usgs.gov, keyless).

    Flow (verified 2026-07-06): GET /api/job/submit?Layer_List=LF2024_FBFM40;
    LF2020_SlpD;LF2020_Asp;LF2020_Elev&Area_of_Interest=<W S E N (WGS84)>
    [&Email=] -> {jobId, status}; poll GET /api/job/status?JobId= until
    Succeeded; download the outputFile zip (one multiband GeoTIFF + .tfw).
    Output_Projection=4326 is requested so the world file is in degrees.

    OPTIONAL-DEP BOUNDARY (Sprint 2 ticket C3c, ops review OPS-2): decoding
    the GeoTIFF needs tifffile + numpy + imagecodecs (real LFPS tiles are
    LZW-compressed — tifffile alone raises deep inside the codec layer).
    Importability is PROBED before a job is submitted; when any dep is
    missing the live path raises RuntimeError naming all three pips in
    milliseconds instead of wasting a 90–180 s LFPS job. Cached tiles (ascii
    format above) keep working dep-free.

    ZIP CACHE (same ticket): the downloaded job zip is written to cache_dir
    (keyed on layers+AOI hash — cell_m independent, the zip is raw job
    output) BEFORE decode, so a decode failure never forces a job
    re-submission: the next tile() call re-decodes the cached zip offline.
    Decoded tiles are then resampled nearest-neighbor to the requested
    cell_m grid and cached as ascii so each operational window is fetched
    once. slope deg, aspect deg (LANDFIRE: -1 flat), elev m, fuel FBFM40
    codes; cell_m meters.

    poll_interval_s/timeout_s in seconds; sleep_fn and dep_probe injectable
    for tests (dep_probe defaults to probe_live_decode_deps)."""

    SUBMIT_URL = "https://lfps.usgs.gov/api/job/submit"
    STATUS_URL = "https://lfps.usgs.gov/api/job/status"
    DEFAULT_LAYERS = "LF2024_FBFM40;LF2020_SlpD;LF2020_Asp;LF2020_Elev"

    def __init__(self, cache_dir: Optional[str] = None,
                 http_get: Optional[HttpGet] = None,
                 layer_list: str = DEFAULT_LAYERS,
                 email: Optional[str] = None,
                 poll_interval_s: float = 5.0,
                 timeout_s: float = 600.0,
                 sleep_fn: Callable[[float], None] = time.sleep,
                 dep_probe: Optional[Callable[[], None]] = None):
        if len(layer_list.split(";")) != 4:
            raise ValueError("layer_list must name exactly 4 layers "
                             "(fuel;slope;aspect;elev) to fill a TerrainTile")
        self._cache_dir = cache_dir
        self._http_get = http_get or _default_http_get
        self._layer_list = layer_list
        self._email = email
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._sleep = sleep_fn
        self._dep_probe = dep_probe or probe_live_decode_deps

    # -- public seam ---------------------------------------------------------

    def tile(self, bbox: BBox, cell_m: float) -> TerrainTile:
        """TerrainTile for bbox at cell_m meters; disk-cached. Raises
        RuntimeError on job failure/timeout or missing optional deps.

        Order of operations (ticket C3c): ascii-cache hit returns dep-free
        and network-free; otherwise the decode deps are probed FIRST (fail
        fast, before any network); then a cached job zip (if present) is
        re-decoded without re-submitting; only then is a fresh LFPS job
        submitted, and its zip is written to the cache BEFORE decode."""
        cache_path = self.cache_path(bbox, cell_m)
        if cache_path and os.path.exists(cache_path):
            return load_ascii_tile(cache_path)
        # A decode is unavoidable from here: probe the optional deps BEFORE
        # spending an LFPS job (or even touching the network) — OPS-2.
        self._dep_probe()
        zip_path = self.zip_cache_path(bbox)
        if zip_path and os.path.exists(zip_path):
            log.info("LFPS zip cache hit for %s; re-decoding without "
                     "re-submitting a job", zip_path)
            with open(zip_path, "rb") as f:
                body = f.read()
        else:
            job_id = self._submit(bbox)
            zip_url = self._poll(job_id)
            status, body = self._http_get(zip_url, {})
            if status != 200:
                raise RuntimeError(f"LFPS zip download failed: HTTP {status}")
            if zip_path:
                # Persist the raw download BEFORE decode: a decode failure
                # must never waste the completed job (OPS-2).
                os.makedirs(os.path.dirname(zip_path), exist_ok=True)
                tmp = f"{zip_path}.tmp"
                with open(tmp, "wb") as f:
                    f.write(body)
                os.replace(tmp, zip_path)
        tile = self._decode_zip(body, bbox, cell_m)
        if cache_path:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            save_ascii_tile(tile, cache_path)
        return tile

    def cache_path(self, bbox: BBox, cell_m: float) -> Optional[str]:
        """Deterministic cache file path for (layers, bbox, cell_m); None if
        no cache_dir configured."""
        if not self._cache_dir:
            return None
        key = (f"{self._layer_list}|{bbox.west:.6f},{bbox.south:.6f},"
               f"{bbox.east:.6f},{bbox.north:.6f}|{cell_m:.1f}")
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self._cache_dir, f"lfps_{h}.wgtile")

    def zip_cache_path(self, bbox: BBox) -> Optional[str]:
        """Deterministic cache path for the RAW downloaded job zip; None if
        no cache_dir configured. Keyed on (layers, AOI) only — the zip is the
        job's output and does not depend on the resample cell_m — so ANY
        later tile() for the same AOI re-decodes it instead of re-submitting
        (ticket C3c: cache the download before decode)."""
        if not self._cache_dir:
            return None
        key = (f"{self._layer_list}|{bbox.west:.6f},{bbox.south:.6f},"
               f"{bbox.east:.6f},{bbox.north:.6f}")
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self._cache_dir, f"lfps_{h}.zip")

    # -- job API --------------------------------------------------------------

    def _submit(self, bbox: BBox) -> str:
        params = {"Layer_List": self._layer_list,
                  "Area_of_Interest": f"{bbox.west} {bbox.south} {bbox.east} {bbox.north}",
                  "Output_Projection": "4326"}
        if self._email:
            params["Email"] = self._email
        url = self.SUBMIT_URL + "?" + urllib.parse.urlencode(
            params, quote_via=urllib.parse.quote)
        status, body = self._http_get(url, {"Accept": "application/json"})
        if status != 200:
            raise RuntimeError(f"LFPS submit failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8"))
        job_id = payload.get("jobId")
        if not job_id:
            raise RuntimeError(f"LFPS submit returned no jobId: {payload}")
        return str(job_id)

    def _poll(self, job_id: str) -> str:
        elapsed = 0.0
        while True:
            url = self.STATUS_URL + "?" + urllib.parse.urlencode({"JobId": job_id})
            status, body = self._http_get(url, {"Accept": "application/json"})
            if status != 200:
                raise RuntimeError(f"LFPS status failed: HTTP {status}")
            payload = json.loads(body.decode("utf-8"))
            state = str(payload.get("status") or "").lower()
            if state == "succeeded":
                out = payload.get("outputFile")
                if not out:
                    raise RuntimeError(f"LFPS job {job_id} succeeded without outputFile")
                return str(out)
            if state in ("failed", "cancelled", "timedout"):
                msgs = payload.get("messages") or []
                raise RuntimeError(f"LFPS job {job_id} {state}: {msgs}")
            if elapsed >= self._timeout_s:
                raise RuntimeError(f"LFPS job {job_id} timed out after "
                                   f"{self._timeout_s:.0f} s (last status {state!r})")
            self._sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s

    # -- decode ----------------------------------------------------------------

    def _decode_zip(self, zip_bytes: bytes, bbox: BBox, cell_m: float) -> TerrainTile:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            tif_names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            tfw_names = [n for n in zf.namelist() if n.lower().endswith(".tfw")]
            if not tif_names:
                raise RuntimeError("LFPS zip contained no GeoTIFF")
            if not tfw_names:
                raise RuntimeError("LFPS zip contained no .tfw world file "
                                   "(needed for dependency-light georeferencing)")
            arr = self._decode_geotiff(zf.read(tif_names[0]))
            tfw = [float(tok) for tok in
                   zf.read(tfw_names[0]).decode("ascii").split()][:6]
        if len(tfw) != 6:
            raise RuntimeError("LFPS .tfw world file malformed")
        # tfw: A=x-scale, D, B, E=y-scale (negative), C=x of UL pixel center,
        #      F=y of UL pixel center. Output_Projection=4326 -> degrees.
        xa, _, _, ye, x_ul, y_ul = tfw
        n_bands = len(self._layer_list.split(";"))
        if getattr(arr, "ndim", 0) != 3:
            raise RuntimeError(f"LFPS GeoTIFF: expected {n_bands}-band raster, "
                               f"got shape {getattr(arr, 'shape', None)}")
        if arr.shape[0] == n_bands:
            bands = [arr[b] for b in range(n_bands)]
        elif arr.shape[-1] == n_bands:
            bands = [arr[:, :, b] for b in range(n_bands)]
        else:
            raise RuntimeError(f"LFPS GeoTIFF shape {arr.shape} does not match "
                               f"{n_bands} requested layers")
        src_rows, src_cols = bands[0].shape
        spec = _grid_for_bbox(bbox, cell_m)
        out = [Raster(spec) for _ in range(n_bands)]
        m_lon = _m_per_deg_lon((bbox.north + bbox.south) / 2.0)
        for r in range(spec.nrows):
            lat = spec.north_lat - (r + 0.5) * cell_m / M_PER_DEG_LAT
            sr = min(max(int(round((lat - y_ul) / ye)), 0), src_rows - 1)
            for c in range(spec.ncols):
                lon = spec.west_lon + (c + 0.5) * cell_m / m_lon
                sc = min(max(int(round((lon - x_ul) / xa)), 0), src_cols - 1)
                for b in range(n_bands):
                    out[b].set(r, c, float(bands[b][sr, sc]))
        return TerrainTile(spec=spec, fuel=out[0], slope_deg=out[1],
                           aspect_deg=out[2], elev_m=out[3],
                           source=f"lfps:{self._layer_list}")

    @staticmethod
    def _decode_geotiff(tif_bytes: bytes):
        """Decode GeoTIFF bytes -> numpy array. Optional-dep boundary backstop:
        tile() probes the deps before any network work (the primary gate);
        this keeps the documented RuntimeError contract even when called
        directly, and converts codec errors from deep inside tifffile (e.g.
        "<COMPRESSION.LZW: 5> requires the 'imagecodecs' package" — a
        ValueError, measured live per ops review OPS-2) into the same
        RuntimeError naming every pip."""
        try:
            import numpy  # noqa: F401  (tifffile needs it; fail together)
            import tifffile
        except ImportError as e:
            raise RuntimeError(_LIVE_DECODE_HINT.format(
                missing=f"missing: {getattr(e, 'name', None) or 'tifffile/numpy'}"
            )) from e
        try:
            return tifffile.imread(io.BytesIO(tif_bytes))
        except ValueError as e:
            # tifffile signals missing codecs (and malformed TIFFs) as
            # ValueError subclasses; the module contract is RuntimeError.
            raise RuntimeError(
                f"LFPS GeoTIFF decode failed ({e}) — the live decode path "
                f"needs {_LIVE_DECODE_PIP}") from e
