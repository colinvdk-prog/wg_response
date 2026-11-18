"""tests/test_first_forecast_speed.py — FIRST FORECAST SPEED (2026-11-18,
the fire-to-alert latency review, build lane 2; contracts SAFETY SEMANTICS
53-56).

  REGIONS   (53, cfg.terrain_regions) the service regions cover every
            domain a fire near the homes can have; a cut is LfpsTerrain's
            own nearest-pixel resampling (equal to _decode_rasters on the
            same zip, and to a direct job on a globally aligned synthetic
            LFPS); a domain inside ready pieces makes 0 LFPS calls; one
            straddling the region's edge, or needing a failed / missing
            piece, takes today's path; a domain with a cached tile keeps it;
            the fetcher waits for the terrain turn before each job, retries
            a failure after retry_s, takes mirrored pieces without a job;
            the cycle holds the turn with the knob; the watch wiring.
  MEMORY    (cfg.terrain_memory_tiles) an LRU keyed on path, mtime and size;
            same values; a shared tile's fingerprint is unchanged by
            forecasts; a cycle digest is identical with the memory on/off.
  KNOB OFF  every knob at its default = the legacy cycle digest.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import math
import os
import random
import sys
import threading
import time
import urllib.parse
import zipfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

np = pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")

from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
    StubAlertEngine,
    StubFeed,
    StubRegistry,
    StubWeather,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, geo, spread, terrain as T, threat  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    BBox,
    ClientProperty,
    LatLon,
    ResponseConfig,
    validate_config,
)

# ---------------------------------------------------------------------------
# a synthetic LFPS: every job's raster sits on ONE global pixel lattice
# ---------------------------------------------------------------------------

PX = 0.002                       # synthetic native pixel (degrees)
LAT0, LON0 = 50.0, -130.0        # the lattice's north-west corner
FUELS = (102.0, 122.0, 145.0, 165.0, 183.0, 98.0)


def _band_values(r, c, canopy):
    """Raw band values at global pixel (r, c) (int16 arrays)."""
    if canopy:
        forest = ((r // 3 + c // 5) % 3) != 0
        f = forest.astype(np.int16)
        return [((r + c) % 50 + 5) * f, ((r * 7 + c) % 30) * f,
                ((r + 3 * c) % 100) * f, ((2 * r + c) % 400 + 20) * f]
    fuel_idx = ((r // 4) * 31 + (c // 3) * 17) % len(FUELS)
    fuel = np.array(FUELS, dtype=np.int16)[fuel_idx]
    return [fuel, (r * 7 + c * 3) % 45, (r + 2 * c) % 360,
            1000 + (r % 97) + (c % 89)]


def make_job_zip(aoi, canopy=False):
    """A real (uncompressed) 4-band int16 GeoTIFF over aoi on the global
    lattice + its .tfw, zipped the LFPS way."""
    r0 = math.floor((LAT0 - aoi.north) / PX)
    r1 = math.ceil((LAT0 - aoi.south) / PX)
    c0 = math.floor((aoi.west - LON0) / PX)
    c1 = math.ceil((aoi.east - LON0) / PX)
    rr, cc = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing="ij")
    arr = np.stack([np.asarray(b, dtype=np.int16)
                    for b in _band_values(rr, cc, canopy)])
    tif = io.BytesIO()
    tifffile.imwrite(tif, arr, photometric="minisblack",
                     planarconfig="separate")
    tfw = "\n".join(repr(v) for v in (
        PX, 0.0, 0.0, -PX, LON0 + (c0 + 0.5) * PX, LAT0 - (r0 + 0.5) * PX))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("j1.tif", tif.getvalue())
        zf.writestr("j1.tfw", tfw)
    return buf.getvalue()


class FakeLfps:
    """HTTP stub of the LFPS job API: submit -> status -> download; the
    zip is made for the submitted AOI and layer list. Records submits."""

    def __init__(self, canopy_layers=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS,
                 fail_aoi=None):
        self.submits = []
        self._jobs = {}
        self._canopy_layers = canopy_layers
        self.fail_aoi = fail_aoi

    def __call__(self, url, headers):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "/api/job/submit" in url:
            w, s, e, n = (float(v) for v in q["Area_of_Interest"].split())
            aoi = BBox(west=w, south=s, east=e, north=n)
            jid = f"j{len(self.submits)}"
            self.submits.append((q["Layer_List"], aoi))
            self._jobs[jid] = (aoi, q["Layer_List"] == self._canopy_layers)
            return 200, json.dumps({"jobId": jid}).encode()
        if "/api/job/status" in url:
            jid = q["JobId"]
            aoi, _c = self._jobs[jid]
            if self.fail_aoi is not None and self.fail_aoi(aoi):
                return 200, json.dumps({"status": "Failed", "messages": [
                    "Output geotif with no raster statistics"]}).encode()
            return 200, json.dumps({"status": "Succeeded",
                                    "outputFile": f"https://x/{jid}.zip"}
                                   ).encode()
        if url.startswith("https://x/"):
            jid = url.rsplit("/", 1)[1][:-4]
            aoi, canopy = self._jobs[jid]
            return 200, make_job_zip(aoi, canopy)
        raise AssertionError(f"unexpected URL {url}")


def lfps_for(tmp_path, http, canopy=True, **kw):
    return T.LfpsTerrain(
        cache_dir=str(tmp_path / "cache"), http_get=http,
        canopy_layer_list=(T.LfpsTerrain.DEFAULT_CANOPY_LAYERS
                           if canopy else None),
        dep_probe=lambda: None, sleep_fn=lambda s: None, **kw)


def bands(tile):
    out = [tile.fuel.data, tile.slope_deg.data, tile.aspect_deg.data,
           tile.elev_m.data]
    if tile.canopy is not None:
        c = tile.canopy
        out += [c.cbh_m.data, c.cbd_kgm3.data, c.cc_pct.data, c.ch_m.data]
    return out


def fingerprint(tile):
    h = hashlib.sha256(repr(tile.spec).encode())
    for b in bands(tile):
        h.update(repr(b).encode())
    return h.hexdigest()


# homes: two groups far apart (Sierra foothills; 250 km east)
HOMES_A = [LatLon(39.0 + 0.01 * k, -121.0 + 0.013 * k) for k in range(6)]
HOMES_B = [LatLon(39.3, -118.0), LatLon(39.32, -117.95)]
RANGE_M, PAD_M, SNAP = 60_000.0, 2000.0, 0.05


def domain_for(fire, homes, cell_m=600.0):
    """The cycle's own domain law (threat._assess_incident): the fire and
    the homes within the client range, padded, snapped."""
    near = [h for h in homes if geo.haversine_m(h, fire) <= RANGE_M]
    bb = geo.bbox_around([fire] + near, PAD_M)
    return threat._snap_bbox(bb, SNAP), near


# ---------------------------------------------------------------------------
# REGIONS: the plan
# ---------------------------------------------------------------------------

def test_region_pieces_cover_every_domain_of_a_fire_near_the_homes():
    homes = HOMES_A + HOMES_B
    d = 0.5
    plan = {p.key for p in T.region_pieces(homes, RANGE_M, PAD_M, SNAP, d)}
    rng = random.Random(53)
    checked = 0
    for _ in range(400):
        h = rng.choice(homes)
        fire = geo.destination(h, rng.uniform(0, 360),
                               rng.uniform(0, RANGE_M * 0.999))
        dom, near = domain_for(fire, homes)
        assert near
        for cell in (150.0, 900.0):
            spec = T._grid_for_bbox(dom, cell)
            m_lon = T._m_per_deg_lon((dom.north + dom.south) / 2.0)
            for r in (0, spec.nrows - 1):
                lat = spec.north_lat - (r + 0.5) * cell / T.M_PER_DEG_LAT
                for c in (0, spec.ncols - 1):
                    lon = spec.west_lon + (c + 0.5) * cell / m_lon
                    assert (math.floor(lat / d), math.floor(lon / d)) in plan
            checked += 1
    assert checked == 800


def test_region_pieces_group_far_homes_apart_and_order_by_distance():
    pieces = T.region_pieces(HOMES_A + HOMES_B, RANGE_M, PAD_M, SNAP, 0.5)
    keys = [p.key for p in pieces]
    assert len(keys) == len(set(keys))
    dists = [p.dist_m for p in pieces]
    assert dists == sorted(dists)
    # two groups (250 km apart, beyond 2 x the range): two disjoint boxes,
    # nothing in the gap between them
    gap = [p for p in pieces if -119.6 < p.core.center().lon < -119.4]
    assert gap == []
    # a priority puts its pieces first, whatever their distance
    pr = T.region_pieces(HOMES_A + HOMES_B, RANGE_M, PAD_M, SNAP, 0.5,
                         priority=lambda bb: 0 if bb.west > -119.0 else 1)
    assert all(p.core.west > -119.0 for p in pr[:5])


# ---------------------------------------------------------------------------
# REGIONS: the cut
# ---------------------------------------------------------------------------

def _ready_regional(tmp_path, homes=HOMES_A, canopy=True, d=0.5, **kw):
    http = FakeLfps()
    lfps = lfps_for(tmp_path, http, canopy=canopy)
    reg = T.RegionalTerrain(lfps, piece_deg=d, **kw)
    reg.plan_from_points(homes, RANGE_M, PAD_M, SNAP)
    got = reg.fetch_pending()
    assert got["failed"] == 0
    return reg, lfps, http


def test_cut_is_the_lfps_resampling_of_the_piece(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path)
    # a domain inside ONE piece: the cut equals _decode_rasters on that
    # piece's own zip, every band, exactly
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    cell = 300.0
    pieces = reg._needed(dom, cell)[0]
    assert len(pieces) == 1
    cut = reg.cut(dom, cell)
    zt = open(lfps.job_zip_path(pieces[0].aoi), "rb").read()
    zc = open(lfps.job_zip_path(pieces[0].aoi, canopy=True), "rb").read()
    spec, ref = lfps._decode_rasters(zt, dom, cell, lfps.layer_list)
    _s, can = lfps._decode_canopy_zip(zc, dom, cell)
    assert cut.spec == spec
    assert bands(cut) == [r.data for r in ref] + [
        can.cbh_m.data, can.cbd_kgm3.data, can.cc_pct.data, can.ch_m.data]
    assert cut.source.startswith("lfps-region:")
    assert cut.canopy.source.startswith("lfps-region:")


def test_multi_piece_cut_matches_a_direct_job_on_an_aligned_lfps(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path)
    fire = geo.destination(HOMES_A[0], 300.0, 30_000.0)
    dom, _near = domain_for(fire, HOMES_A)
    cell = 450.0
    assert len(reg._needed(dom, cell)[0]) >= 2      # a real mosaic
    cut = reg.cut(dom, cell)
    # the direct path on a separate cache: its own job for the exact
    # domain (the synthetic LFPS keeps one global pixel lattice, so the
    # same pixels exist in both — the cut must find each of them)
    direct = lfps_for(tmp_path / "direct", FakeLfps()).tile(dom, cell)
    a, b = bands(cut), bands(direct)
    n = len(a[0])
    for k in range(8):
        same = sum(1 for x, y in zip(a[k], b[k]) if x == y)
        assert same >= n - max(2, n // 1000), (k, same, n)


def test_domain_inside_region_makes_zero_lfps_calls(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path)
    n0 = len(http.submits)
    assert n0 == 2 * len(reg.pieces())               # terrain + canopy each
    fire = geo.destination(HOMES_A[2], 45.0, 20_000.0)
    dom, _near = domain_for(fire, HOMES_A)
    t = reg.tile(dom, 300.0)
    assert len(http.submits) == n0                   # 0 LFPS calls
    assert t.source.startswith("lfps-region:")
    assert reg.stats["cuts"] == 1
    # held: the next ask is the same object, still 0 calls
    assert reg.tile(dom, 300.0) is t
    assert reg.stats["held_hits"] == 1
    assert len(http.submits) == n0


def test_domain_straddling_the_region_edge_falls_through(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path)
    keys = {p.key for p in reg.pieces()}
    east = max(j for _i, j in keys)
    # a box reaching one piece past the region's east edge
    edge = (east + 1) * reg.piece_deg
    dom = BBox(west=edge - 0.2, south=39.05, east=edge + 0.2, north=39.3)
    n0 = len(http.submits)
    t = reg.tile(dom, 300.0)
    assert not reg.covers(dom, 300.0)
    assert t.source.startswith("lfps:")             # today's path
    assert len(http.submits) == n0 + 2              # its own two jobs
    assert reg.stats["fallthrough"] == 1


def test_a_domain_with_a_cached_tile_keeps_it(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path)
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    before = lfps.tile(dom, 300.0)                   # today's path, cached
    n0 = len(http.submits)
    t = reg.tile(dom, 300.0)
    assert t.source.startswith("lfps:")
    assert bands(t) == bands(before)
    assert len(http.submits) == n0
    assert reg.stats["cached_direct"] == 1 and reg.stats["cuts"] == 0


def test_terrain_only_provider_cuts_without_canopy(tmp_path):
    reg, lfps, http = _ready_regional(tmp_path, canopy=False)
    assert len(http.submits) == len(reg.pieces())
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    t = reg.tile(dom, 300.0)
    assert t.canopy is None and t.source.startswith("lfps-region:")


def test_failed_piece_falls_through_and_retries_after_retry_s(tmp_path):
    clock = [1000.0]
    bad = lambda aoi: aoi.west < -121.0 < aoi.east   # noqa: E731
    http = FakeLfps(fail_aoi=bad)
    lfps = lfps_for(tmp_path, http)
    reg = T.RegionalTerrain(lfps, piece_deg=0.5, retry_s=3600.0,
                            clock=lambda: clock[0])
    reg.plan_from_points(HOMES_A, RANGE_M, PAD_M, SNAP)
    got = reg.fetch_pending()
    assert got["failed"] > 0
    assert reg.status()["failed"]
    http.fail_aoi = None
    n0 = len(http.submits)
    # a domain needing a failed piece: today's path
    dom = BBox(west=-121.2, south=39.05, east=-120.8, north=39.3)
    t = reg.tile(dom, 300.0)
    assert t.source.startswith("lfps:")
    # before retry_s nothing is resubmitted; after it, the piece is fetched
    n1 = len(http.submits)
    assert reg.fetch_pending()["waiting_retry"] == got["failed"]
    assert len(http.submits) == n1
    clock[0] += 3601.0
    assert reg.fetch_pending()["fetched"] == got["failed"]
    assert len(http.submits) == n1 + got["failed"]
    assert n1 == n0 + 2


def test_fetcher_waits_for_the_terrain_turn_before_each_job(tmp_path):
    http = FakeLfps()
    lfps = lfps_for(tmp_path, http)
    reg = T.RegionalTerrain(lfps, piece_deg=0.5)
    reg.plan_from_points(HOMES_A[:1], RANGE_M, PAD_M, SNAP)
    turn = threat._TerrainTurn()
    reg.bind_turn(turn.wait)
    turn.hold()                                      # the cycle assesses
    done = threading.Event()
    th = threading.Thread(target=lambda: (reg.fetch_pending(), done.set()))
    th.start()
    time.sleep(0.3)
    assert http.submits == [] and not done.is_set()
    turn.release()
    assert done.wait(30.0)
    assert len(http.submits) == 2 * len(reg.pieces())


def test_mirrored_pieces_are_taken_without_a_job(tmp_path):
    class FakeS3:
        def __init__(self):
            self.objs = {}

        def put_object(self, Bucket, Key, Body):
            self.objs[Key] = bytes(Body)

        def get_object(self, Bucket, Key):
            if Key not in self.objs:
                e = Exception("missing")
                e.response = {"Error": {"Code": "NoSuchKey"}}
                raise e
            return {"Body": io.BytesIO(self.objs[Key])}

        def head_object(self, Bucket, Key):
            if Key not in self.objs:
                e = Exception("missing")
                e.response = {"Error": {"Code": "404"}}
                raise e
            return {}

    s3 = FakeS3()
    http = FakeLfps()
    first = lfps_for(tmp_path / "c1", http, s3_uri="s3://b/p", s3_client=s3)
    r1 = T.RegionalTerrain(first, piece_deg=0.5)
    r1.plan_from_points(HOMES_A, RANGE_M, PAD_M, SNAP)
    r1.fetch_pending()
    n0 = len(http.submits)
    # a fresh container: empty cache, the same mirror
    second = lfps_for(tmp_path / "c2", http, s3_uri="s3://b/p", s3_client=s3)
    r2 = T.RegionalTerrain(second, piece_deg=0.5)
    r2.plan_from_points(HOMES_A, RANGE_M, PAD_M, SNAP)
    got = r2.fetch_pending()
    assert len(http.submits) == n0 and got["fetched"] == 0
    assert set(r2._state.values()) == {"mirrored"}
    assert not os.listdir(tmp_path / "c2" / "cache") if os.path.isdir(
        tmp_path / "c2" / "cache") else True        # nothing pulled yet
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    t = r2.tile(dom, 300.0)
    assert t.source.startswith("lfps-region:") and len(http.submits) == n0


def test_cut_errors_fall_through(tmp_path, monkeypatch):
    reg, lfps, http = _ready_regional(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("decode exploded")

    monkeypatch.setattr(T, "_read_job_zip", boom)
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    t = reg.tile(dom, 300.0)
    assert t.source.startswith("lfps:")
    assert reg.stats["cut_errors"] == 1


# ---------------------------------------------------------------------------
# REGIONS: the cycle and the watch wiring
# ---------------------------------------------------------------------------

class TurnProbe:
    """A terrain provider that records whether the turn was held."""

    def __init__(self, inner):
        self.inner = inner
        self.held = []
        self.mon = None

    def tile(self, bbox, cell_m):
        self.held.append(self.mon.terrain_turn.held)
        return self.inner.tile(bbox, cell_m)


@pytest.mark.parametrize("armed", [False, True])
def test_cycle_holds_the_terrain_turn_with_regions_armed(armed):
    from tests.seeded_stack import StubTerrain
    probe = TurnProbe(StubTerrain())
    mon = make_monitor(terrain=probe, cfg=make_cfg(terrain_regions=armed))
    probe.mon = mon
    mon.cycle(NOW)
    assert probe.held == [armed]
    assert not mon.terrain_turn.held               # freed after


def _watch_args(**kw):
    base = dict(synthetic_terrain=False, live_terrain=False,
                crown_fire=True, terrain_fallback=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_watch_wiring(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "probe_live_decode_deps", lambda *a: None)
    off, _ = cli._watch_terrain(_watch_args(), str(tmp_path))
    assert isinstance(off, T.BundledFallbackTerrain)
    assert isinstance(off._primary, T.LfpsTerrain)
    assert off._primary.memory is None
    assert T.find_regional(off) is None
    cfg = dataclasses.replace(ResponseConfig(), terrain_regions=True,
                              terrain_memory_tiles=3)
    on, notes = cli._watch_terrain(_watch_args(), str(tmp_path), cfg=cfg)
    reg = T.find_regional(on)
    assert isinstance(reg, T.RegionalTerrain)
    assert isinstance(reg.lfps, T.LfpsTerrain)
    assert reg.lfps.memory is not None and reg._memory is reg.lfps.memory
    # the flags
    p = cli._build_parser()
    a = p.parse_args(cli._normalize_argv(
        "watch --aoi x --terrain-regions --terrain-region-piece-deg 0.25 "
        "--terrain-memory-tiles 4 --first-forecast-priority "
        "--weather-retry-first-forecast --learn-terrain-yield".split()))
    c, notes = cli._first_forecast_cfg(ResponseConfig(), a)
    assert (c.terrain_regions, c.terrain_region_piece_deg,
            c.terrain_memory_tiles, c.first_forecast_priority,
            c.weather_retry_first_forecast, c.learn_terrain_yield) == (
        True, 0.25, 4, True, True, True)
    assert len(notes) == 5 and validate_config(c) == []
    a0 = p.parse_args(cli._normalize_argv(["watch", "--aoi", "x"]))
    c0, notes0 = cli._first_forecast_cfg(ResponseConfig(), a0)
    assert c0 == ResponseConfig() and notes0 == []


def test_start_terrain_regions_binds_the_turn_and_plans(tmp_path):
    http = FakeLfps()
    lfps = lfps_for(tmp_path, http)
    reg = T.RegionalTerrain(lfps, piece_deg=0.5, recheck_s=3600.0)
    fb = T.BundledFallbackTerrain(reg, str(tmp_path / "no_tiles"))
    homes = [ClientProperty(client_id=f"h{k}", loc=p)
             for k, p in enumerate(HOMES_A)]
    mon = make_monitor(terrain=fb, registry=StubRegistry(homes),
                       cfg=make_cfg(terrain_regions=True))
    mon.terrain_turn.hold()
    notes = cli._start_terrain_regions(fb, mon, StubRegistry(homes), None,
                                       make_cfg(terrain_regions=True))
    try:
        assert notes and mon.regional_terrain is reg
        deadline = time.time() + 10
        while not reg.pieces() and time.time() < deadline:
            time.sleep(0.05)
        assert reg.pieces()
        time.sleep(0.2)
        assert http.submits == []                    # the turn is held
        mon.terrain_turn.release()
        deadline = time.time() + 30
        while (reg.status()["terrain_ready"] < len(reg.pieces())
               and time.time() < deadline):
            time.sleep(0.05)
        assert reg.status()["terrain_ready"] == len(reg.pieces())
    finally:
        reg.stop()
        mon.terrain_turn.release()


def test_validation_bounds():
    assert validate_config(ResponseConfig(terrain_region_piece_deg=0.05))
    assert validate_config(ResponseConfig(terrain_region_piece_deg=1.5))
    assert validate_config(ResponseConfig(terrain_memory_tiles=17))
    assert validate_config(ResponseConfig(terrain_memory_tiles=-1))
    assert validate_config(ResponseConfig(weather_retry_wait_s=31.0))
    assert validate_config(ResponseConfig(terrain_regions=1))
    assert validate_config(ResponseConfig()) == []


# ---------------------------------------------------------------------------
# MEMORY
# ---------------------------------------------------------------------------

def test_tile_memory_lru_and_rewrite(tmp_path):
    calls = []

    def loader(p):
        calls.append(p)
        return open(p).read()

    mem = T.TileMemory(2)
    paths = []
    for k in range(3):
        p = tmp_path / f"t{k}"
        p.write_text(f"v{k}")
        paths.append(str(p))
    assert mem.load(paths[0], loader) == "v0"
    assert mem.load(paths[0], loader) == "v0"
    assert len(calls) == 1
    mem.load(paths[1], loader)
    mem.load(paths[2], loader)                       # evicts t0
    mem.load(paths[0], loader)
    assert len(calls) == 4
    # a rewritten file (new size / mtime) is read again
    with open(paths[0], "w") as f:
        f.write("v0-new!")
    assert mem.load(paths[0], loader) == "v0-new!"
    assert len(calls) == 5
    assert mem.status()["tiles"] == 2


def test_memory_serves_the_same_values_without_reparsing(tmp_path,
                                                          monkeypatch):
    http = FakeLfps()
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    plain = lfps_for(tmp_path, http)
    t0 = plain.tile(dom, 300.0)                      # fetched + cached
    mem = T.TileMemory(4)
    lt = lfps_for(tmp_path, http, memory=mem)
    reads = []
    real_t, real_c = T.load_ascii_tile, T.load_ascii_canopy
    monkeypatch.setattr(T, "load_ascii_tile",
                        lambda p: (reads.append(p), real_t(p))[1])
    monkeypatch.setattr(T, "load_ascii_canopy",
                        lambda p: (reads.append(p), real_c(p))[1])
    a = lt.tile(dom, 300.0)
    b = lt.tile(dom, 300.0)
    assert len(reads) == 2                           # tile + canopy, once
    assert bands(a) == bands(b) == bands(t0)
    assert a.fuel is b.fuel                          # shared, read-only
    assert mem.status()["hits"] == 2


def test_a_shared_tile_is_never_written_by_forecasts(tmp_path):
    http = FakeLfps()
    mem = T.TileMemory(4)
    lt = lfps_for(tmp_path, http, memory=mem)
    dom = BBox(west=-121.05, south=38.95, east=-120.85, north=39.1)
    lt.tile(dom, 300.0)                              # fetched, then cached
    tile = lt.tile(dom, 300.0)                       # the memory's copy
    fp = fingerprint(tile)
    cfg = make_cfg(ensemble_n=3, crown_fire=True, spread_any_angle=True,
                   corridor_erosion_tiered=True, extreme_regime_enabled=True)
    from tests.test_threat import StubWeather as W
    wx = W().forecast(ORIGIN, cfg.horizon_h)
    ens = spread.ensemble_arrival(tile, [ORIGIN], wx, NOW, cfg,
                                  incident_id="fp")
    spread.tiered_eroded_p10(ens.p10, tile.fuel, cfg, canopy=tile.canopy)
    threat.regime_spread_cfg(tile, wx, NOW, cfg)
    again = lt.tile(dom, 300.0)
    assert again.fuel is tile.fuel
    assert fingerprint(again) == fp == fingerprint(tile)


# ---------------------------------------------------------------------------
# KNOB OFF / CYCLE DIGESTS
# ---------------------------------------------------------------------------

def _cycle_digest(cfg, terrain, cycles=2):
    mon = make_monitor(terrain=terrain, cfg=cfg)
    out = []
    for k in range(cycles):
        res = mon.cycle(NOW + 600.0 * k)
        rows = sorted((a.client_id, a.incident_id, a.eta.eta_p10_s,
                       a.eta.eta_p50_s, a.eta.eta_p90_s)
                      for a in res.assessments)
        ens = {iid: (e.p10.data, e.p50.data, e.p90.data)
               for iid, e in res.ensembles.items()}
        out.append((rows, repr(ens), [str(e) for e in res.events],
                    res.notes))
    return hashlib.sha256(repr(out).encode()).hexdigest()


#: the legacy cycle digests computed on the BASE commit 9d11793 (before any
#: of these knobs existed) by scratch_review/fire_alert_latency_review_
#: 2026-11-18/L2_first_forecast_speed/golden_digest.py: a stub terrain
#: without and with forecast tiers, and the synthetic-LFPS LfpsTerrain
#: (a cold fetch, then its cache), 3 cycles each
BASE_GOLDEN = {
    "stub": "a19ddb0a95b1b454726eb934f93c9cb452ab75a5a9630057e619ef74af441794",
    "stub_tiers":
        "78c0c6e5e28cf4299430d6d9fd2bce951e9b4d943e4742d1b82409341db4cba7",
    "lfps": "8675ef0138ae4322e31302c92f5616d8b3fa785e511e8df65412a32c2dabebbc",
}


def test_knobs_off_cycle_matches_the_base_commit(tmp_path):
    from tests.seeded_stack import StubTerrain
    assert _cycle_digest(make_cfg(), StubTerrain(), cycles=3) == \
        BASE_GOLDEN["stub"]
    assert _cycle_digest(make_cfg(forecast_tiers=True), StubTerrain(),
                         cycles=3) == BASE_GOLDEN["stub_tiers"]
    assert _cycle_digest(make_cfg(), lfps_for(tmp_path, FakeLfps()),
                         cycles=3) == BASE_GOLDEN["lfps"]


def test_knobs_off_cycle_is_byte_identical(tmp_path):
    from tests.seeded_stack import StubTerrain
    legacy = _cycle_digest(make_cfg(), StubTerrain())
    explicit = _cycle_digest(make_cfg(
        terrain_regions=False, terrain_region_piece_deg=0.5,
        terrain_memory_tiles=0, first_forecast_priority=False,
        weather_retry_first_forecast=False, weather_retry_wait_s=5.0,
        learn_terrain_yield=False), StubTerrain())
    assert legacy == explicit


def test_memory_on_cycle_digest_equals_off(tmp_path):
    http = FakeLfps()
    cfg = make_cfg()                 # every cycle asks for the tile again
    off = lfps_for(tmp_path, http)
    d_off = _cycle_digest(cfg, off, cycles=3)
    on = lfps_for(tmp_path, http, memory=T.TileMemory(2))
    d_on = _cycle_digest(cfg, on, cycles=3)
    assert d_on == d_off
    assert on.memory.status()["hits"] >= 1


# ---------------------------------------------------------------------------
# PRIORITY (SAFETY SEMANTICS 54): the two-lane member dispatcher
# ---------------------------------------------------------------------------

def _job(order, name, gate=None, fail=False):
    if gate is not None:
        assert gate.wait(30.0)
    if fail:
        raise ValueError(f"{name} failed")
    order.append(name)
    return name


@pytest.fixture
def one_slot():
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1)
    d = spread._MemberDispatch(pool, 1)
    yield d
    d.close("test over")
    pool.shutdown(wait=True)


def test_foreground_members_go_ahead_of_queued_background(one_slot):
    order, gate = [], threading.Event()
    futs = [one_slot.submit(_job, (order, "bg0", gate), False)]
    time.sleep(0.1)                                  # bg0 holds the slot
    futs += [one_slot.submit(_job, (order, n), False) for n in ("bg1", "bg2")]
    futs += [one_slot.submit(_job, (order, n), True) for n in ("fg1", "fg2")]
    gate.set()
    assert [f.result(30) for f in futs] == ["bg0", "bg1", "bg2", "fg1",
                                            "fg2"]
    assert order == ["bg0", "fg1", "fg2", "bg1", "bg2"]
    assert list(one_slot.dispatched) == ["bg", "fg", "fg", "bg", "bg"]


def test_a_cancelled_foreground_member_is_skipped(one_slot):
    order, gate = [], threading.Event()
    f0 = one_slot.submit(_job, (order, "bg0", gate), False)
    time.sleep(0.1)
    fg = one_slot.submit(_job, (order, "fg"), True)
    bg = one_slot.submit(_job, (order, "bg1"), False)
    assert fg.cancel()
    gate.set()
    assert f0.result(30) == "bg0" and bg.result(30) == "bg1"
    assert order == ["bg0", "bg1"] and fg.cancelled()


def test_a_failed_foreground_member_frees_its_slot(one_slot):
    order, gate = [], threading.Event()
    f0 = one_slot.submit(_job, (order, "bg0", gate), False)
    time.sleep(0.1)
    fg = one_slot.submit(_job, (order, "fg", None, True), True)
    bgs = [one_slot.submit(_job, (order, f"bg{k}"), False) for k in (1, 2)]
    gate.set()
    with pytest.raises(ValueError):
        fg.result(30)
    assert [b.result(30) for b in bgs] == ["bg1", "bg2"]
    assert f0.result(30) == "bg0"


def test_closing_fails_every_queued_member_at_once(one_slot):
    order, gate = [], threading.Event()
    f0 = one_slot.submit(_job, (order, "bg0", gate), False)
    time.sleep(0.1)
    queued = [one_slot.submit(_job, (order, "x"), fg) for fg in (True, False)]
    t = time.time()
    one_slot.close("pool dropped")
    for q in queued:
        with pytest.raises(spread._DispatchClosed):
            q.result(5)
    assert time.time() - t < 5
    gate.set()
    assert f0.result(30) == "bg0"                    # in flight: finishes


def test_priority_members_are_the_in_process_members():
    from tests.test_spread import T0, center_ignition, const_wx, grass_tile
    tile = grass_tile(nrows=31, ncols=31)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=9.0, wind_dir_deg=281.25, hours=30)
    cfg = ResponseConfig(ensemble_n=5, horizon_h=24.0)
    here = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                   incident_id="prio")
    on = dataclasses.replace(cfg, ensemble_workers=2,
                             first_forecast_priority=True)
    try:
        with spread.foreground_members():
            fg = spread.ensemble_arrival(tile, [ign], wx, T0, on,
                                         incident_id="prio")
        bg = spread.ensemble_arrival(tile, [ign], wx, T0, on,
                                     incident_id="prio")
        for other in (fg, bg):
            assert [m.data for m in other.members] == [
                m.data for m in here.members]
            assert other.p10.data == here.p10.data
            assert other.p50.data == here.p50.data
        assert spread._DISPATCH is not None
        assert list(spread._DISPATCH.dispatched) == ["fg"] * 4 + ["bg"] * 4
    finally:
        spread._drop_member_pool()
    assert spread._DISPATCH is None


def test_knob_off_never_builds_the_dispatcher():
    from tests.test_spread import T0, center_ignition, const_wx, grass_tile
    spread._drop_member_pool()
    tile = grass_tile(nrows=21, ncols=21)
    ign = center_ignition(tile)
    wx = const_wx(ign, hours=30)
    cfg = ResponseConfig(ensemble_n=4, horizon_h=24.0, ensemble_workers=2)
    try:
        with spread.foreground_members():
            spread.ensemble_arrival(tile, [ign], wx, T0, cfg)
        assert spread._DISPATCH is None
    finally:
        spread._drop_member_pool()


def _lane_recorder(monkeypatch):
    seen = []
    real = spread.ensemble_arrival

    def rec(*a, **k):
        seen.append((threading.current_thread().name,
                     bool(getattr(spread._LANE, "fg", False))))
        return real(*a, **k)

    monkeypatch.setattr(spread, "ensemble_arrival", rec)
    return seen


@pytest.mark.parametrize("armed", [False, True])
def test_the_first_forecast_runs_in_the_foreground_lane(monkeypatch, armed):
    seen = _lane_recorder(monkeypatch)
    cfg = make_cfg(forecast_tiers=True, first_forecast_priority=armed)
    mon = make_monitor(cfg=cfg)
    mon.cycle(NOW)                                   # the first forecast
    mon.cycle(NOW + 3600.0)                          # a background refresh
    kept = mon._kept["inc-1"]
    if kept.pending is not None:
        kept.pending.result(60)
    assert len(seen) == 2
    (t1, fg1), (t2, fg2) = seen
    assert fg1 is armed and not t1.startswith("wg-forecast-refresh")
    assert fg2 is False and t2.startswith("wg-forecast-refresh")


# ---------------------------------------------------------------------------
# WEATHER RETRY (SAFETY SEMANTICS 55)
# ---------------------------------------------------------------------------

class FlakyWeather(StubWeather):
    """Fails the first `fails` calls with `exc`, then answers."""

    def __init__(self, exc, fails=1):
        super().__init__()
        self.exc = exc
        self.fails = fails
        self.tries = 0

    def forecast(self, loc, horizon_h):
        self.tries += 1
        if self.tries <= self.fails:
            raise self.exc
        return super().forecast(loc, horizon_h)


def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError(
        "https://api.weather.gov/gridpoints/STO/1,2?secret=k", code,
        "Service Unavailable", {}, None)


def _timeout():
    import socket
    import urllib.error
    return urllib.error.URLError(socket.timeout("timed out"))


def _weather_mon(weather, **kw):
    mon = make_monitor(weather=weather, cfg=make_cfg(**kw))
    slept = []
    mon._retry_sleep = slept.append
    return mon, slept


@pytest.mark.parametrize("exc,kind", [(_http_error(503), "HTTP 503"),
                                      (_timeout(), "timeout")])
@pytest.mark.parametrize("tiers", [False, True])
def test_a_first_forecast_retries_a_5xx_or_timeout_once(exc, kind, tiers,
                                                         caplog):
    wx = FlakyWeather(exc)
    mon, slept = _weather_mon(wx, weather_retry_first_forecast=True,
                              forecast_tiers=tiers)
    with caplog.at_level("WARNING", logger="wgresponse.threat"):
        res = mon.cycle(NOW)
    assert res.assessments and wx.tries == 2 and slept == [5.0]
    assert any("SAFETY SEMANTICS 55" in n and kind in n for n in res.notes)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secret" not in text and "/gridpoints" not in text
    if kind.startswith("HTTP"):
        assert "api.weather.gov" in text


@pytest.mark.parametrize("tiers", [False, True])
def test_knob_off_a_first_forecast_failure_costs_the_cycle(tiers):
    wx = FlakyWeather(_http_error(503))
    mon, slept = _weather_mon(wx, forecast_tiers=tiers)
    res = mon.cycle(NOW)
    assert not res.assessments and wx.tries == 1 and slept == []
    assert any(e.kind == "provider_error" for e in res.operator_events)


def test_a_4xx_and_a_second_failure_are_not_retried_again():
    wx = FlakyWeather(_http_error(404))
    mon, slept = _weather_mon(wx, weather_retry_first_forecast=True)
    assert not mon.cycle(NOW).assessments and wx.tries == 1 and slept == []
    wx2 = FlakyWeather(_http_error(502), fails=5)
    mon2, slept2 = _weather_mon(wx2, weather_retry_first_forecast=True)
    res = mon2.cycle(NOW)
    assert not res.assessments and wx2.tries == 2 and slept2 == [5.0]
    assert any("failed too" in n for n in res.notes)


@pytest.mark.parametrize("tiers", [False, True])
def test_a_fire_with_a_forecast_is_never_retried(tiers):
    wx = FlakyWeather(_http_error(503), fails=0)
    mon, slept = _weather_mon(wx, weather_retry_first_forecast=True,
                              forecast_tiers=tiers)
    assert mon.cycle(NOW).assessments                 # forecast made
    wx.fails, wx.tries = 1, 0
    # tiers: past the growing tier's limit (a refresh, not a first forecast)
    res = mon.cycle(NOW + 3600.0)
    assert wx.tries == 1 and slept == []
    if not tiers:
        assert not res.assessments


# ---------------------------------------------------------------------------
# LEARNING YIELDS (SAFETY SEMANTICS 56)
# ---------------------------------------------------------------------------

class HoldingLfps(FakeLfps):
    """The first job submitted holds the terrain turn (a cycle starts
    assessing right after the learning forecast's terrain job)."""

    def __init__(self, turn):
        super().__init__()
        self.turn = turn

    def __call__(self, url, headers):
        if "/api/job/submit" in url and not self.submits:
            self.turn.hold()
        return super().__call__(url, headers)


@pytest.mark.parametrize("armed", [False, True])
def test_a_learning_forecast_yields_between_its_two_jobs(tmp_path, armed):
    mon = make_monitor(cfg=make_cfg(learn_terrain_yield=armed,
                                    grid_cell_m=600.0))
    http = HoldingLfps(mon.terrain_turn)
    mon._terrain = lfps_for(tmp_path, http)
    inc = incident()
    done = threading.Event()

    def run():
        mon._learn_forecast(inc, NOW, [inc.origin], None, None, None,
                            [inc.origin], [inc.origin])
        done.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(1.0)
    if armed:
        assert len(http.submits) == 1 and not done.is_set()
        mon.terrain_turn.release()
    assert done.wait(60.0)
    mon.terrain_turn.release()
    assert len(http.submits) == 2


def test_regional_pieces_leave_the_disk_once_mirrored(tmp_path):
    """SAFETY SEMANTICS 53, THE DISK: the container has 3 GB (App Runner,
    image included) — a piece the mirror holds is not kept locally after
    its fetch, a cut pulls only what it needs and drops it after reading,
    and the cut equals the one a disk-heavy cache would give."""
    class FakeS3:
        def __init__(self):
            self.objs = {}

        def put_object(self, Bucket, Key, Body):
            self.objs[Key] = bytes(Body)

        def get_object(self, Bucket, Key):
            if Key not in self.objs:
                e = Exception("missing")
                e.response = {"Error": {"Code": "NoSuchKey"}}
                raise e
            return {"Body": io.BytesIO(self.objs[Key])}

        def head_object(self, Bucket, Key):
            if Key not in self.objs:
                e = Exception("missing")
                e.response = {"Error": {"Code": "404"}}
                raise e
            return {}

    def zips(root):
        return [os.path.join(d, f) for d, _s, fs in os.walk(root)
                for f in fs if f.endswith(".zip")]

    s3 = FakeS3()
    http = FakeLfps()
    lfps = lfps_for(tmp_path / "c1", http, s3_uri="s3://b/p", s3_client=s3)
    reg = T.RegionalTerrain(lfps, piece_deg=0.5)
    reg.plan_from_points(HOMES_A, RANGE_M, PAD_M, SNAP)
    got = reg.fetch_pending()
    assert got["fetched"] == 2 * len(reg.pieces())
    assert set(reg._state.values()) == {"mirrored"}
    assert zips(tmp_path / "c1") == []                # nothing kept locally
    dom = BBox(west=-120.95, south=39.05, east=-120.6, north=39.4)
    t = reg.tile(dom, 300.0)
    assert t.source.startswith("lfps-region:")
    assert zips(tmp_path / "c1") == []                # pulled, read, dropped
    # the same cut as a cache that keeps every piece on disk
    heavy, _l, _h = _ready_regional(tmp_path / "heavy")
    assert bands(t) == bands(heavy.tile(dom, 300.0))
    # no mirror: the zips stay (nothing can bring them back)
    assert zips(tmp_path / "heavy")
