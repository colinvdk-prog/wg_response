"""Tests for the LfpsTerrain S3 TILE-CACHE MIRROR — durable cache off /tmp.

On App Runner the terrain cache_dir lives on /tmp: every container restart
(~37 in August, tri-model review 2026-08-31) emptied it, so the first cycle
after a restart re-fetched every LANDFIRE tile through the LFPS job API
(~20 min measured). With ``s3_uri`` configured, a cache MISS first tries the
mirrored object under ``<prefix>/terrain/<cache filename>`` — the filename IS
the existing deterministic (layers, AOI[, cell_m]) hash, so the mirror acts
as a cache_dir shared across containers.

Guarantees exercised here:
  * a mirrored DECODED tile is served without submitting an LFPS job AND
    without the optional-dep probe (no decode is involved — ticket C3c);
  * a mirrored raw JOB ZIP is decoded without re-submitting;
  * every newly written cache file is uploaded, keyed so another container
    with an EMPTY cache_dir finds it;
  * every S3 failure (missing / broken / corrupt object, failed upload) is
    LOUD and falls back to the normal LFPS path — never fatal;
  * with s3_uri=None nothing changes.

Stdlib only: the GeoTIFF decode is faked (as in test_terrain.py) and the S3
client is the in-memory fake from test_persist_s3 — no AWS, no boto3, no
tifffile.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_persist_s3 import BUCKET, URI, FakeS3  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402
from wgresponse.terrain import (  # noqa: E402
    M_PER_DEG_LAT,
    S3_TERRAIN_PREFIX,
    LfpsTerrain,
    SyntheticTerrain,
    _m_per_deg_lon,
    load_ascii_tile,
    save_ascii_tile,
)


def bbox_meters(center_lat, center_lon, half_m):
    dlat = half_m / M_PER_DEG_LAT
    dlon = half_m / _m_per_deg_lon(center_lat)
    return BBox(west=center_lon - dlon, south=center_lat - dlat,
                east=center_lon + dlon, north=center_lat + dlat)


BB = bbox_meters(34.05, -118.70, 1500.0)      # 3 km x 3 km
CELL = 100.0
PREFIX = "wgresp/prod"                         # from test_persist_s3.URI


# ---------------------------------------------------------------------------
# stubs: decode, job zip, HTTP, probe
# ---------------------------------------------------------------------------

class _FakeBand:
    def __init__(self, rows):
        self._rows = rows
        self.shape = (len(rows), len(rows[0]))

    def __getitem__(self, rc):
        r, c = rc
        return self._rows[r][c]


class _FakeArr:
    def __init__(self, bands):
        self._bands = [_FakeBand(b) for b in bands]
        self.ndim = 3
        self.shape = (len(bands),) + self._bands[0].shape

    def __getitem__(self, b):
        return self._bands[b]


_SRC_N = 10
_BAND_VALUES = (102.0, 5.0, 180.0, 250.0)      # fuel, slope, aspect, elev


class FakeDecodeLfps(LfpsTerrain):
    """LfpsTerrain with only the GeoTIFF decode replaced (zip/tfw plumbing,
    cache seams and the S3 mirror all stay real)."""

    @staticmethod
    def _decode_geotiff(tif_bytes):
        return _FakeArr([[[v] * _SRC_N for _ in range(_SRC_N)]
                         for v in _BAND_VALUES])


def lfps_job_zip(bbox=BB, src_n=_SRC_N):
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


def job_http(zip_bytes):
    """submit -> status -> download stub; records every URL."""
    calls = []

    def http_get(url, headers):
        calls.append(url)
        if "/api/job/submit" in url:
            return 200, json.dumps({"jobId": "j123", "status": "Pending"}).encode()
        if "/api/job/status" in url:
            return 200, json.dumps(
                {"status": "Succeeded",
                 "outputFile": "https://lfps.usgs.gov/out/j123.zip"}).encode()
        if "/out/j123.zip" in url:
            return 200, zip_bytes
        raise AssertionError(f"unexpected URL: {url}")

    http_get.calls = calls
    return http_get


def forbidden_http(url, headers):
    raise AssertionError(f"LFPS network touched: {url}")


def no_probe():
    """The suite must pass on a stdlib-only machine (the REAL probe would
    correctly fail); used where a decode legitimately happens."""
    return None


def poisoned_probe():
    raise AssertionError("optional-dep probe ran on a cache hit (no decode "
                         "is involved — ticket C3c)")


def tile_bytes(seed=3, kind="ridge", tmp=None):
    """Exact save_ascii_tile bytes for a deterministic synthetic tile."""
    tile = SyntheticTerrain(seed, kind).tile(BB, CELL)
    path = str(tmp / "seed.wgtile")
    save_ascii_tile(tile, path)
    with open(path, "rb") as f:
        return tile, f.read()


def errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def text(records):
    return " | ".join(r.getMessage() for r in records)


# ---------------------------------------------------------------------------
# keys / configuration
# ---------------------------------------------------------------------------

class TestMirrorKeys:
    def test_keys_are_the_cache_filenames_under_prefix_terrain(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path), s3_uri=URI,
                         s3_client=FakeS3())
        tile_key = lt.s3_key(lt.cache_path(BB, CELL))
        zip_key = lt.s3_key(lt.zip_cache_path(BB))
        assert lt.s3_enabled is True
        assert tile_key == (f"{PREFIX}/{S3_TERRAIN_PREFIX}/"
                            + os.path.basename(lt.cache_path(BB, CELL)))
        assert tile_key.endswith(".wgtile") and zip_key.endswith(".zip")
        assert tile_key.startswith("wgresp/prod/terrain/lfps_")
        # cell_m keys the decoded tile, the AOI alone keys the zip
        assert lt.s3_key(lt.cache_path(BB, 30.0)) != tile_key
        assert zip_key != tile_key

    def test_mirror_off_by_default(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path))
        assert lt.s3_enabled is False
        assert lt.s3_key(lt.cache_path(BB, CELL)) is None
        assert lt.mirror_status()["enabled"] is False

    def test_mirror_without_cache_dir_warns_and_stays_off(self, caplog):
        with caplog.at_level(logging.WARNING, logger="wgresponse.terrain"):
            lt = LfpsTerrain(s3_uri=URI, s3_client=FakeS3())
        assert lt.s3_enabled is False
        assert any("stays OFF" in r.getMessage() for r in caplog.records)

    def test_bad_uri_raises_at_construction(self, tmp_path):
        with pytest.raises(ValueError):
            LfpsTerrain(cache_dir=str(tmp_path), s3_uri="my-bucket/tiles")
        # ...even when the mirror would be disabled anyway (no cache_dir):
        # a typo must never degrade silently into "no mirror".
        with pytest.raises(ValueError):
            LfpsTerrain(s3_uri="my-bucket/tiles")


# ---------------------------------------------------------------------------
# s3_uri=None: untouched
# ---------------------------------------------------------------------------

class TestMirrorOffUnchanged:
    def test_local_cache_hit_still_offline_and_dep_free(self, tmp_path):
        lt = LfpsTerrain(cache_dir=str(tmp_path), http_get=forbidden_http,
                         dep_probe=poisoned_probe)
        tile = SyntheticTerrain(3, "ridge").tile(BB, CELL)
        save_ascii_tile(tile, lt.cache_path(BB, CELL))
        got = lt.tile(BB, CELL)
        assert got.fuel.data == tile.fuel.data and got.spec == tile.spec

    def test_job_path_writes_only_local_caches(self, tmp_path):
        http = job_http(lfps_job_zip())
        lt = FakeDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe)
        tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert os.path.exists(lt.cache_path(BB, CELL))
        assert os.path.exists(lt.zip_cache_path(BB))


# ---------------------------------------------------------------------------
# THE HEADLINE: a cache miss is served from the mirror, no LFPS job
# ---------------------------------------------------------------------------

class TestMirrorPull:
    def test_mirrored_tile_skips_the_job_and_the_dep_probe(self, tmp_path):
        """A fresh container (EMPTY cache_dir) must serve the decoded tile
        from S3: no LFPS job, no network, and no optional-dep probe — pulling
        an ascii tile involves no GeoTIFF decode."""
        tile, body = tile_bytes(tmp=tmp_path)
        cache_dir = tmp_path / "fresh_container"
        probe_key = LfpsTerrain(cache_dir=str(cache_dir)).cache_path(BB, CELL)
        key = f"{PREFIX}/{S3_TERRAIN_PREFIX}/{os.path.basename(probe_key)}"
        fake = FakeS3({(BUCKET, key): body})

        lt = LfpsTerrain(cache_dir=str(cache_dir), http_get=forbidden_http,
                         dep_probe=poisoned_probe, s3_uri=URI, s3_client=fake)
        got = lt.tile(BB, CELL)

        assert got.fuel.data == tile.fuel.data
        assert got.elev_m.data == tile.elev_m.data
        assert got.spec == tile.spec
        assert fake.gets == [(BUCKET, key)]
        # ...and it landed in the local cache, so the NEXT call needs no S3
        assert os.path.exists(lt.cache_path(BB, CELL))
        again = LfpsTerrain(cache_dir=str(cache_dir), http_get=forbidden_http,
                            dep_probe=poisoned_probe)
        assert again.tile(BB, CELL).fuel.data == tile.fuel.data

    def test_mirrored_zip_is_decoded_without_resubmitting(self, tmp_path):
        """Only the raw job zip is mirrored (e.g. the decode died last time):
        decode it, never re-submit — an LFPS job costs 90-180 s."""
        zip_bytes = lfps_job_zip()
        cache_dir = tmp_path / "fresh"
        zip_name = os.path.basename(
            LfpsTerrain(cache_dir=str(cache_dir)).zip_cache_path(BB))
        key = f"{PREFIX}/{S3_TERRAIN_PREFIX}/{zip_name}"
        fake = FakeS3({(BUCKET, key): zip_bytes})

        lt = FakeDecodeLfps(cache_dir=str(cache_dir), http_get=forbidden_http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert set(tile.elev_m.data) == {250.0}
        with open(lt.zip_cache_path(BB), "rb") as f:
            assert f.read() == zip_bytes          # exact bytes landed locally
        # the decoded tile is now ALSO mirrored (next container skips decode)
        assert fake.body(f"{PREFIX}/{S3_TERRAIN_PREFIX}/"
                         + os.path.basename(lt.cache_path(BB, CELL)))

    def test_missing_object_is_an_info_note_then_the_job_runs(self, tmp_path,
                                                              caplog):
        fake = FakeS3()                                    # empty bucket
        http = job_http(lfps_job_zip())
        lt = FakeDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.INFO, logger="wgresponse.terrain"):
            tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert errors(caplog) == []                        # absence != failure
        assert any("no mirrored object" in r.getMessage() for r in caplog.records)
        assert len([u for u in http.calls if "job/submit" in u]) == 1


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------

class TestMirrorPush:
    def test_fresh_fetch_uploads_zip_and_tile_for_the_next_container(self,
                                                                     tmp_path):
        zip_bytes = lfps_job_zip()
        fake = FakeS3()
        http = job_http(zip_bytes)
        lt = FakeDecodeLfps(cache_dir=str(tmp_path / "c1"), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        tile = lt.tile(BB, CELL)

        tile_key = lt.s3_key(lt.cache_path(BB, CELL))
        zip_key = lt.s3_key(lt.zip_cache_path(BB))
        assert sorted(fake.keys()) == sorted([tile_key, zip_key])
        assert fake.body(zip_key) == zip_bytes             # raw job output
        # The decoded tile object is a valid ascii tile...
        mirrored = tmp_path / "mirrored.wgtile"
        mirrored.write_bytes(fake.body(tile_key))
        assert load_ascii_tile(str(mirrored)).fuel.data == tile.fuel.data
        # ...and a DIFFERENT container with an empty cache_dir serves it with
        # no network at all (the cache key is cache_dir-independent).
        c2 = LfpsTerrain(cache_dir=str(tmp_path / "c2"),
                         http_get=forbidden_http, dep_probe=poisoned_probe,
                         s3_uri=URI, s3_client=fake)
        assert c2.tile(BB, CELL).fuel.data == tile.fuel.data
        st = lt.mirror_status()
        assert st["enabled"] is True and st["last_upload_ok"] is True
        assert st["last_upload_key"] == tile_key

    def test_upload_failure_is_loud_but_the_tile_is_returned(self, tmp_path,
                                                             caplog):
        fake = FakeS3(fail_put=True)
        lt = FakeDecodeLfps(cache_dir=str(tmp_path), http_get=job_http(lfps_job_zip()),
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR):
            tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}              # never fatal
        assert os.path.exists(lt.cache_path(BB, CELL))     # local cache fine
        assert "FAILED" in text(errors(caplog))
        assert lt.mirror_status()["last_upload_ok"] is False


# ---------------------------------------------------------------------------
# failure semantics: loud, never fatal
# ---------------------------------------------------------------------------

class TestMirrorFailures:
    def test_broken_s3_falls_back_to_the_live_job(self, tmp_path, caplog):
        fake = FakeS3(fail_get=True)                       # AccessDenied
        http = job_http(lfps_job_zip())
        lt = FakeDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR, logger="wgresponse.terrain"):
            tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        msg = text(errors(caplog))
        assert "AccessDenied" in msg and "falling back to a live LFPS job" in msg
        assert len([u for u in http.calls if "job/submit" in u]) == 1

    def test_corrupt_mirrored_tile_is_discarded_then_the_job_runs(self,
                                                                  tmp_path,
                                                                  caplog):
        """A truncated/garbage mirrored tile must not poison every restart:
        discard it loudly and fetch for real (the fresh tile is re-uploaded,
        repairing the object)."""
        cache_dir = tmp_path / "c"
        probe = LfpsTerrain(cache_dir=str(cache_dir))
        key = (f"{PREFIX}/{S3_TERRAIN_PREFIX}/"
               + os.path.basename(probe.cache_path(BB, CELL)))
        fake = FakeS3({(BUCKET, key): b"not an ascii tile at all\n"})
        http = job_http(lfps_job_zip())
        lt = FakeDecodeLfps(cache_dir=str(cache_dir), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI, s3_client=fake)
        with caplog.at_level(logging.ERROR, logger="wgresponse.terrain"):
            tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert "unusable" in text(errors(caplog))
        assert len([u for u in http.calls if "job/submit" in u]) == 1
        # the object was repaired by the re-upload
        mirrored = tmp_path / "repaired.wgtile"
        mirrored.write_bytes(fake.body(key))
        assert load_ascii_tile(str(mirrored)).fuel.data == tile.fuel.data

    def test_missing_boto3_is_loud_and_non_fatal(self, tmp_path, monkeypatch,
                                                 caplog):
        """boto3 is an OPTIONAL dependency: with a mirror configured and no
        client injected its absence is a clear RuntimeError INSIDE the mirror
        — logged, and the LFPS path still delivers the tile."""
        monkeypatch.setitem(sys.modules, "boto3", None)
        http = job_http(lfps_job_zip())
        lt = FakeDecodeLfps(cache_dir=str(tmp_path), http_get=http,
                            sleep_fn=lambda s: None, dep_probe=no_probe,
                            s3_uri=URI)
        with caplog.at_level(logging.ERROR, logger="wgresponse.terrain"):
            tile = lt.tile(BB, CELL)
        assert set(tile.fuel.data) == {102.0}
        assert "pip install boto3" in text(errors(caplog))
