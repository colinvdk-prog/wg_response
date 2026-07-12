"""Tests for wgresponse.terrain — synthetic tiles, ascii cache, LFPS plumbing.

LFPS live-path tests inject ``dep_probe`` and (where a decode is reached) a
fake ``_decode_geotiff`` so the suite stays stdlib-only: the real probe/decode
need the optional tifffile/numpy/imagecodecs [live] extras (ticket C3c)."""
import io
import json
import math
import os
import zipfile

import pytest

from wgresponse.contracts import BBox, GridSpec, Raster, TerrainTile
from wgresponse.terrain import (FLAT_ASPECT, M_PER_DEG_LAT, LfpsTerrain,
                                SyntheticTerrain, _m_per_deg_lon, _slope_aspect,
                                load_ascii_tile, probe_live_decode_deps,
                                save_ascii_tile)


def _no_probe():
    """Injected dep_probe for plumbing tests: the suite must pass on a
    stdlib-only machine, where the REAL probe would (correctly) fail first."""
    return None


def bbox_meters(center_lat, center_lon, half_m):
    """Square bbox of +/- half_m meters around a center point."""
    dlat = half_m / M_PER_DEG_LAT
    dlon = half_m / _m_per_deg_lon(center_lat)
    return BBox(west=center_lon - dlon, south=center_lat - dlat,
                east=center_lon + dlon, north=center_lat + dlat)


BB = bbox_meters(34.05, -118.70, 1500.0)     # 3 km x 3 km
CELL = 100.0


class TestSyntheticTerrain:
    def test_grass_uniform_flat(self):
        tile = SyntheticTerrain(1, "grass").tile(BB, CELL)
        assert tile.spec.north_lat == BB.north
        assert tile.spec.west_lon == BB.west
        assert tile.spec.cell_m == CELL
        assert set(tile.fuel.data) == {102.0}                 # GR2
        assert set(tile.slope_deg.data) == {0.0}
        assert set(tile.aspect_deg.data) == {FLAT_ASPECT}
        assert set(tile.elev_m.data) == {100.0}

    def test_chaparral_uniform(self):
        tile = SyntheticTerrain(1, "chaparral").tile(BB, CELL)
        assert set(tile.fuel.data) == {145.0}                 # SH5

    def test_grid_sizing_matches_bbox(self):
        tile = SyntheticTerrain(1, "grass").tile(BB, CELL)
        expect_rows = int(round((BB.north - BB.south) * M_PER_DEG_LAT / CELL))
        assert tile.spec.nrows == expect_rows
        assert tile.spec.nrows >= 28                          # ~3 km / 100 m
        assert tile.spec.ncols >= 28

    def test_mixed_has_multiple_fuels_from_known_set(self):
        tile = SyntheticTerrain(7, "mixed").tile(BB, CELL)
        codes = set(tile.fuel.data)
        assert codes <= {102.0, 122.0, 145.0, 183.0}
        assert len(codes) >= 2

    def test_mixed_deterministic(self):
        a = SyntheticTerrain(7, "mixed").tile(BB, CELL)
        b = SyntheticTerrain(7, "mixed").tile(BB, CELL)
        assert a.fuel.data == b.fuel.data
        c = SyntheticTerrain(8, "mixed").tile(BB, CELL)
        assert a.fuel.data != c.fuel.data

    def test_bad_kind_rejected(self):
        with pytest.raises(ValueError):
            SyntheticTerrain(1, "volcano")

    def test_ridge_elevation_peaks_at_center(self):
        tile = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        spec = tile.spec
        cr, cc = spec.nrows // 2, spec.ncols // 2
        center = tile.elev_m.get(cr, cc)
        corner = tile.elev_m.get(0, spec.ncols - 1)   # NE corner: ON the ridge axis
        nw_corner = tile.elev_m.get(0, 0)             # NW corner: far from axis
        assert center > nw_corner + 100.0
        assert center > 400.0                         # base 100 + ridge ~400
        # NE-SW orientation: the axis-aligned corner sits higher than the
        # perpendicular corner
        assert corner > nw_corner

    def test_ridge_flanks_slope_and_aspect_downslope(self):
        tile = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        spec = tile.spec
        cr, cc = spec.nrows // 2, spec.ncols // 2
        # NW of the NE-SW axis: rows up AND cols down moves perpendicular NW
        r_nw, c_nw = cr - 5, cc - 5
        slope_nw = tile.slope_deg.get(r_nw, c_nw)
        aspect_nw = tile.aspect_deg.get(r_nw, c_nw)
        assert slope_nw > 1.0                          # flank is steep
        assert 285.0 <= aspect_nw <= 345.0             # faces ~NW (downslope)
        # SE flank mirrors
        r_se, c_se = cr + 5, cc + 5
        aspect_se = tile.aspect_deg.get(r_se, c_se)
        assert 105.0 <= aspect_se <= 165.0             # faces ~SE
        # crest is flatter than the flank
        assert tile.slope_deg.get(cr, cc) < slope_nw

    def test_ridge_deterministic(self):
        a = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        b = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        assert a.elev_m.data == b.elev_m.data
        assert a.fuel.data == b.fuel.data
        assert a.slope_deg.data == b.slope_deg.data
        assert a.aspect_deg.data == b.aspect_deg.data


class TestSlopeAspectMath:
    def _spec(self, n=5):
        return GridSpec(north_lat=34.0, west_lon=-118.0, cell_m=100.0,
                        nrows=n, ncols=n)

    def test_plane_rising_east_faces_west(self):
        spec = self._spec()
        elev = Raster(spec)
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                elev.set(r, c, 10.0 * c)              # +0.1 m/m eastward
        slope, aspect = _slope_aspect(elev)
        s = math.degrees(math.atan(0.1))
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                assert slope.get(r, c) == pytest.approx(s, abs=1e-9)
                assert aspect.get(r, c) == pytest.approx(270.0)   # downslope = west

    def test_plane_rising_south_faces_north(self):
        spec = self._spec()
        elev = Raster(spec)
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                elev.set(r, c, 10.0 * r)              # row 0 = north edge = lowest
        _slope, aspect = _slope_aspect(elev)
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                assert aspect.get(r, c) == pytest.approx(0.0)     # downslope = north

    def test_flat_marks_flat_aspect(self):
        spec = self._spec()
        elev = Raster(spec, fill=50.0)
        slope, aspect = _slope_aspect(elev)
        assert set(slope.data) == {0.0}
        assert set(aspect.data) == {FLAT_ASPECT}


class TestAsciiTileRoundTrip:
    def test_round_trip_exact(self, tmp_path):
        tile = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        path = str(tmp_path / "tile.wgtile")
        save_ascii_tile(tile, path)
        loaded = load_ascii_tile(path)
        assert loaded.spec == tile.spec
        assert loaded.source == tile.source
        assert loaded.fuel.data == tile.fuel.data              # exact floats
        assert loaded.slope_deg.data == tile.slope_deg.data
        assert loaded.aspect_deg.data == tile.aspect_deg.data
        assert loaded.elev_m.data == tile.elev_m.data

    def test_round_trip_preserves_awkward_floats(self, tmp_path):
        spec = GridSpec(north_lat=34.123456789, west_lon=-118.987654321,
                        cell_m=90.0, nrows=2, ncols=2)
        vals = [0.1, 1e-17, 123456.789012345, -0.30000000000000004]
        tile = TerrainTile(spec=spec, fuel=Raster(spec, data=list(vals)),
                           slope_deg=Raster(spec, data=list(vals)),
                           aspect_deg=Raster(spec, data=list(vals)),
                           elev_m=Raster(spec, data=list(vals)), source="t")
        path = str(tmp_path / "awkward.wgtile")
        save_ascii_tile(tile, path)
        loaded = load_ascii_tile(path)
        assert loaded.fuel.data == vals
        assert loaded.spec.north_lat == 34.123456789

    def test_missing_band_rejected(self, tmp_path):
        path = tmp_path / "bad.wgtile"
        path.write_text("1 1 100.0 34.0 -118.0\nFUEL\n102.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing bands"):
            load_ascii_tile(str(path))

    def test_bad_header_rejected(self, tmp_path):
        path = tmp_path / "bad2.wgtile"
        path.write_text("not a header\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_ascii_tile(str(path))


class TestLfpsTerrain:
    def test_cache_hit_never_touches_network_or_probe(self, tmp_path):
        """An ascii-cache hit is fully offline AND dep-free: neither the
        network nor the optional-dep probe may run (ticket C3c)."""
        def forbidden(url, headers):
            raise AssertionError(f"network touched: {url}")
        def poisoned_probe():
            raise AssertionError("dep probe ran on a cache hit")
        lt = LfpsTerrain(cache_dir=str(tmp_path), http_get=forbidden,
                         dep_probe=poisoned_probe)
        tile = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        save_ascii_tile(tile, lt.cache_path(BB, CELL))
        got = lt.tile(BB, CELL)
        assert got.fuel.data == tile.fuel.data
        assert got.spec == tile.spec

    def test_cache_path_deterministic_and_distinct(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path))
        assert lt.cache_path(BB, 100.0) == lt.cache_path(BB, 100.0)
        assert lt.cache_path(BB, 100.0) != lt.cache_path(BB, 30.0)
        other = bbox_meters(35.0, -119.0, 1500.0)
        assert lt.cache_path(BB, 100.0) != lt.cache_path(other, 100.0)
        assert LfpsTerrain().cache_path(BB, 100.0) is None

    def test_submit_http_error_raises(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path),
                         http_get=lambda url, headers: (500, b""),
                         sleep_fn=lambda s: None, dep_probe=_no_probe)
        with pytest.raises(RuntimeError, match="submit failed"):
            lt.tile(BB, CELL)

    def test_job_failure_raises_with_messages(self):
        def http_get(url, headers):
            if "/api/job/submit" in url:
                return 200, json.dumps({"jobId": "j1", "status": "Pending"}).encode()
            if "/api/job/status" in url:
                return 200, json.dumps({"status": "Failed", "messages": [
                    {"type": "esriJobMessageTypeError",
                     "description": "ERROR: Invalid products"}]}).encode()
            raise AssertionError(url)
        lt = LfpsTerrain(http_get=http_get, sleep_fn=lambda s: None,
                         dep_probe=_no_probe)
        with pytest.raises(RuntimeError, match="Invalid products"):
            lt.tile(BB, CELL)

    def test_job_timeout_raises(self):
        def http_get(url, headers):
            if "/api/job/submit" in url:
                return 200, json.dumps({"jobId": "j2", "status": "Pending"}).encode()
            return 200, json.dumps({"status": "Executing"}).encode()
        lt = LfpsTerrain(http_get=http_get, poll_interval_s=5.0, timeout_s=20.0,
                         sleep_fn=lambda s: None, dep_probe=_no_probe)
        with pytest.raises(RuntimeError, match="timed out"):
            lt.tile(BB, CELL)

    def test_submit_url_encodes_layers_and_aoi(self):
        seen = {}
        def http_get(url, headers):
            seen["url"] = url
            return 500, b""
        lt = LfpsTerrain(http_get=http_get, dep_probe=_no_probe)
        with pytest.raises(RuntimeError):
            lt.tile(BB, CELL)
        url = seen["url"]
        assert "lfps.usgs.gov/api/job/submit" in url
        assert "LF2024_FBFM40%3BLF2020_SlpD%3BLF2020_Asp%3BLF2020_Elev" in url
        assert "Area_of_Interest=" in url

    def test_layer_list_must_have_four_layers(self):
        with pytest.raises(ValueError):
            LfpsTerrain(layer_list="LF2024_FBFM40;LF2020_Elev")


# ---------------------------------------------------------------------------
# Optional-dep boundary (Sprint 2 ticket C3c, ops review OPS-2): the live
# decode needs tifffile+numpy+imagecodecs (real LFPS tiles are LZW-compressed)
# and the probe must fail BEFORE a 90-180 s LFPS job is submitted.
# ---------------------------------------------------------------------------

def _import_missing(*missing):
    """Fake importlib.import_module: named modules raise ImportError."""
    def imp(name):
        if name in missing:
            raise ImportError(f"No module named {name!r}", name=name)
        return object()
    return imp


class TestLiveDecodeDepBoundary:
    PIPS = "pip install tifffile numpy imagecodecs"

    def test_probe_names_all_pips_and_the_missing_dep(self):
        """The documented failure names ALL THREE pips — a user who installs
        only 'tifffile numpy' (the old, wrong instruction) then dies on the
        LZW codec mid-decode was the measured OPS-2 failure."""
        with pytest.raises(RuntimeError) as ei:
            probe_live_decode_deps(_import_missing("imagecodecs"))
        msg = str(ei.value)
        assert self.PIPS in msg
        assert "missing: imagecodecs" in msg

    def test_probe_lists_every_missing_dep(self):
        with pytest.raises(RuntimeError) as ei:
            probe_live_decode_deps(
                _import_missing("tifffile", "numpy", "imagecodecs"))
        assert "missing: tifffile, numpy, imagecodecs" in str(ei.value)

    def test_probe_noop_when_all_deps_import(self):
        probe_live_decode_deps(lambda name: object())   # must not raise

    def test_default_probe_is_the_module_probe(self):
        assert LfpsTerrain()._dep_probe is probe_live_decode_deps

    def test_tile_probes_deps_before_any_network(self, tmp_path):
        """Probe failure must precede submit: no HTTP call may ever happen
        when the decode deps are missing (do not spend the LFPS job)."""
        def forbidden(url, headers):
            raise AssertionError(f"network touched before dep probe: {url}")
        def failing_probe():
            probe_live_decode_deps(_import_missing("imagecodecs"))
        lt = LfpsTerrain(cache_dir=str(tmp_path), http_get=forbidden,
                         dep_probe=failing_probe)
        with pytest.raises(RuntimeError, match="imagecodecs"):
            lt.tile(BB, CELL)

    def test_decode_geotiff_failure_is_runtimeerror_naming_pips(self):
        """The decode boundary keeps the documented RuntimeError contract on
        EVERY machine: without the deps the import guard raises it; with them
        tifffile's ValueError (garbage/missing-codec) is wrapped into it."""
        with pytest.raises(RuntimeError, match="pip install tifffile numpy "
                                               "imagecodecs"):
            LfpsTerrain._decode_geotiff(b"not a tiff at all")


# ---------------------------------------------------------------------------
# LFPS zip cache (ticket C3c): the downloaded job zip is persisted BEFORE
# decode, so a decode failure re-decodes from disk instead of re-submitting.
# Decode is faked (pure-python band arrays) to keep the suite stdlib-only.
# ---------------------------------------------------------------------------

class _FakeBand:
    def __init__(self, rows):
        self._rows = rows
        self.shape = (len(rows), len(rows[0]))

    def __getitem__(self, rc):
        r, c = rc
        return self._rows[r][c]


class _FakeArr:
    """Minimal ndarray stand-in for _decode_zip: .ndim/.shape/[band][r, c]."""

    def __init__(self, bands):
        self._bands = [_FakeBand(b) for b in bands]
        self.ndim = 3
        self.shape = (len(bands),) + self._bands[0].shape

    def __getitem__(self, b):
        return self._bands[b]


_SRC_N = 10
_BAND_VALUES = (102.0, 5.0, 180.0, 250.0)      # fuel, slope, aspect, elev


class _FakeDecodeLfps(LfpsTerrain):
    """Live-path LfpsTerrain with the GeoTIFF decode replaced by constant
    4-band pure-python arrays (the zip/tfw plumbing stays real)."""

    @staticmethod
    def _decode_geotiff(tif_bytes):
        return _FakeArr([[[v] * _SRC_N for _ in range(_SRC_N)]
                         for v in _BAND_VALUES])


class _ExplodingDecodeLfps(LfpsTerrain):
    @staticmethod
    def _decode_geotiff(tif_bytes):
        raise RuntimeError("decode exploded (simulated codec failure)")


def _lfps_job_zip(bbox, src_n=_SRC_N):
    """A structurally-valid LFPS output zip: one .tif (content irrelevant —
    the decoder is faked) + a real .tfw whose pixel grid covers bbox."""
    xa = (bbox.east - bbox.west) / src_n
    ye = -(bbox.north - bbox.south) / src_n
    tfw = "\n".join(repr(v) for v in
                    (xa, 0.0, 0.0, ye,
                     bbox.west + xa / 2.0, bbox.north + ye / 2.0))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("j123.tif", b"fake-tif-decoder-is-injected")
        zf.writestr("j123.tfw", tfw)
    return buf.getvalue()


def _job_http(zip_bytes):
    """HTTP stub for the full submit -> status -> download flow; records URLs."""
    calls = []

    def http_get(url, headers):
        calls.append(url)
        if "/api/job/submit" in url:
            return 200, json.dumps({"jobId": "j123",
                                    "status": "Pending"}).encode()
        if "/api/job/status" in url:
            return 200, json.dumps(
                {"status": "Succeeded",
                 "outputFile": "https://lfps.usgs.gov/out/j123.zip"}).encode()
        if "/out/j123.zip" in url:
            return 200, zip_bytes
        raise AssertionError(f"unexpected URL: {url}")

    http_get.calls = calls
    return http_get


def _forbidden_http(url, headers):
    raise AssertionError(f"network touched: {url}")


class TestLfpsZipCache:
    def test_zip_cache_path_keyed_on_aoi_not_cellm(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path))
        assert lt.zip_cache_path(BB) == lt.zip_cache_path(BB)
        other = bbox_meters(35.0, -119.0, 1500.0)
        assert lt.zip_cache_path(BB) != lt.zip_cache_path(other)
        assert lt.zip_cache_path(BB).endswith(".zip")
        # the RAW zip is the job's output: independent of the resample cell_m
        # (cache_path, by contrast, is cell_m-keyed)
        assert lt.cache_path(BB, 100.0) != lt.cache_path(BB, 30.0)
        assert LfpsTerrain().zip_cache_path(BB) is None

    def test_happy_path_decodes_and_writes_both_caches(self, tmp_path):
        zip_bytes = _lfps_job_zip(BB)
        http = _job_http(zip_bytes)
        lt = _FakeDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                             sleep_fn=lambda s: None, dep_probe=_no_probe)
        tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert set(tile.slope_deg.data) == {5.0}
        assert set(tile.aspect_deg.data) == {180.0}
        assert set(tile.elev_m.data) == {250.0}
        assert tile.spec.north_lat == BB.north and tile.spec.cell_m == CELL
        assert os.path.exists(lt.zip_cache_path(BB))
        assert os.path.exists(lt.cache_path(BB, CELL))
        assert len([u for u in http.calls if "job/submit" in u]) == 1

    def test_zip_persisted_before_decode_then_redecoded_without_resubmit(
            self, tmp_path):
        """THE OPS-2 regression: decode dies AFTER the job completed. The
        downloaded zip must already be on disk (exact bytes), and the next
        tile() call must re-decode it with ZERO network — never re-submit."""
        zip_bytes = _lfps_job_zip(BB)
        http = _job_http(zip_bytes)
        broken = _ExplodingDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                                      sleep_fn=lambda s: None,
                                      dep_probe=_no_probe)
        with pytest.raises(RuntimeError, match="decode exploded"):
            broken.tile(BB, CELL)
        zip_path = broken.zip_cache_path(BB)
        assert zip_path and os.path.exists(zip_path)
        with open(zip_path, "rb") as f:
            assert f.read() == zip_bytes               # exact download persisted
        assert len([u for u in http.calls if "job/submit" in u]) == 1
        # deps fixed (decoder works now), same cache_dir: fully offline retry
        fixed = _FakeDecodeLfps(cache_dir=str(tmp_path),
                                http_get=_forbidden_http,
                                sleep_fn=lambda s: None, dep_probe=_no_probe)
        tile = fixed.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        # and the ascii cache now exists -> third call needs no probe either
        poisoned = LfpsTerrain(cache_dir=str(tmp_path),
                               http_get=_forbidden_http,
                               dep_probe=lambda: (_ for _ in ()).throw(
                                   AssertionError("probe ran on cache hit")))
        assert poisoned.tile(BB, CELL).fuel.data == tile.fuel.data

    def test_cached_zip_reused_for_a_different_cell_size(self, tmp_path):
        """cell_m is a DECODE-time resample: a new cell size must re-use the
        cached zip (ascii cache misses; zip cache hits; no network)."""
        zip_bytes = _lfps_job_zip(BB)
        lt = _FakeDecodeLfps(cache_dir=str(tmp_path),
                             http_get=_job_http(zip_bytes),
                             sleep_fn=lambda s: None, dep_probe=_no_probe)
        t100 = lt.tile(BB, CELL)                       # fetches + caches zip
        lt2 = _FakeDecodeLfps(cache_dir=str(tmp_path),
                              http_get=_forbidden_http,
                              sleep_fn=lambda s: None, dep_probe=_no_probe)
        t150 = lt2.tile(BB, 150.0)                     # zip hit, no job
        assert t150.spec.cell_m == 150.0
        assert t150.spec.nrows < t100.spec.nrows
        assert set(t150.fuel.data) == {102.0}

    def test_no_cache_dir_still_decodes_in_memory(self):
        zip_bytes = _lfps_job_zip(BB)
        lt = _FakeDecodeLfps(http_get=_job_http(zip_bytes),
                             sleep_fn=lambda s: None, dep_probe=_no_probe)
        tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert lt.zip_cache_path(BB) is None
