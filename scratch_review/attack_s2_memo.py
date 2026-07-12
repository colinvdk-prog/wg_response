"""ADVERSARIAL VERIFIER (Session 2) — attack #5: B3 ROS-memo byte-identity.

Claims under attack (Sprint 2 ticket B3; spread.py module docstring):
  5a. WGRESP_NO_ROS_MEMO=1 (unmemoized reference) vs default (memoized) is
      BYTE-IDENTICAL — verified on a FRESH random tile trio (not the ticket's
      tiles): LANDFIRE-like integer slopes/aspects, hostile continuous float
      slopes/aspects, and a mixed tile with an unknown burnable code +
      shifting/jittered weather + ensemble knobs (wind/moisture scales,
      dir_offset) + perimeter seeding, multiple ignition points.
  5b. The cache cap only causes recompute, never divergence: byte-identity
      holds with the cap forced tiny (monkeypatched _ROS_CACHE_MAX).
  5c. Bounded memory: repeated compute_arrival calls do not grow RSS
      unbounded and leave NO growing module-level containers in spread.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s2_memo.py -v
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread
from wgresponse.contracts import (GridSpec, LatLon, Raster, ResponseConfig,
                                  TerrainTile, seeded_rng)
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
BURNABLE_CODES = sorted(c for c in FUEL_TABLE if is_burnable(c))


def _fingerprint(raster):
    h = hashlib.sha256()
    for v in raster.data:
        h.update(struct.pack("<d", v))
    return h.hexdigest()


def _tile_random(k, nrows, ncols, cell_m, continuous, north=34.72,
                 west=-119.55, unknown_code=None):
    rng = seeded_rng("vf-s2-b3-tile", k)
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    fuel, slope, aspect = [], [], []
    for i in range(n):
        r = rng.random()
        if r < 0.08:
            fuel.append(float(rng.choice((91, 98, 99))))       # NB pockets
        elif unknown_code is not None and r < 0.16:
            fuel.append(float(unknown_code))                    # no-table row
        else:
            fuel.append(float(rng.choice(BURNABLE_CODES)))
        if continuous:
            slope.append(rng.uniform(0.0, 42.0))
            aspect.append(rng.uniform(0.0, 360.0))
        else:
            slope.append(float(rng.randrange(0, 42)))
            aspect.append(float(rng.randrange(0, 360)))
    return TerrainTile(spec=spec, fuel=Raster(spec, fuel),
                       slope_deg=Raster(spec, slope),
                       aspect_deg=Raster(spec, aspect),
                       elev_m=Raster(spec, [150.0] * n), source="attack-b3")


def _wx(loc, seed="attack-b3-wx", shifts=None, jitter=0.0):
    return MockWeather(seed, base_wind_mps=5.5, dir_deg=250.0, rh_pct=22.0,
                       temp_c=33.0, shifts=shifts, jitter_frac=jitter,
                       t0_s=T0).forecast(loc, 110)


def _run_pair(tile, ign, wx, cfg, **kw):
    """(memo_fingerprint, reference_fingerprint) for one compute_arrival."""
    old = os.environ.pop("WGRESP_NO_ROS_MEMO", None)
    try:
        memo = spread.compute_arrival(tile, ign, wx, T0, cfg, **kw)
        os.environ["WGRESP_NO_ROS_MEMO"] = "1"
        ref = spread.compute_arrival(tile, ign, wx, T0, cfg, **kw)
    finally:
        if old is None:
            os.environ.pop("WGRESP_NO_ROS_MEMO", None)
        else:
            os.environ["WGRESP_NO_ROS_MEMO"] = old
    return _fingerprint(memo), _fingerprint(ref)


def _ring(center, half_m):
    pts = []
    for brg in range(0, 360, 30):
        pts.append(geo.destination(center, float(brg),
                                   half_m * (1.0 + 0.2 * math.sin(brg))))
    return pts


# ---------------------------------------------------------------------------
# 5a — fresh random tile trio, hostile knob combinations
# ---------------------------------------------------------------------------

def test_b3_byte_identity_fresh_tile_trio():
    cases = []
    # T1: LANDFIRE-like integer quantization
    t1 = _tile_random(101, 48, 48, 120.0, continuous=False)
    i1 = [geo.rc_to_latlon(t1.spec, 24, 6)]
    cases.append(("T1-int", t1, i1, _wx(i1[0]), ResponseConfig(), {}))
    # T2: continuous slopes/aspects (worst case for key dedupe), 3 ignitions
    t2 = _tile_random(202, 40, 40, 150.0, continuous=True)
    i2 = [geo.rc_to_latlon(t2.spec, 20, 5), geo.rc_to_latlon(t2.spec, 5, 20),
          geo.rc_to_latlon(t2.spec, 35, 35)]
    wx2 = _wx(i2[0], shifts=[(6, 9.0, 310.0, 15.0), (18, 3.0, 200.0, 45.0)],
              jitter=0.15)
    cases.append(("T2-cont-shift", t2, i2, wx2, ResponseConfig(), {}))
    # T3: unknown burnable code + ensemble knobs + perimeter
    t3 = _tile_random(303, 44, 44, 130.0, continuous=False, unknown_code=150)
    center = geo.rc_to_latlon(t3.spec, 22, 10)
    i3 = [geo.rc_to_latlon(t3.spec, 30, 30)]
    kw3 = dict(moisture_scale=0.87, wind_scale=1.11, dir_offset_deg=-17.0,
               perimeter=[_ring(center, 800.0)],
               perimeter_at_s=T0 - 3 * 3600.0)
    cases.append(("T3-unknown-perim-knobs", t3, i3, _wx(i3[0]),
                  ResponseConfig(), kw3))
    # T3b: spotting ON over the same hostile tile
    cfg_spot = ResponseConfig()
    cfg_spot.spotting_enabled = True
    cases.append(("T3b-spotting", t3, i3, _wx(i3[0]), cfg_spot,
                  dict(dir_offset_deg=8.0)))

    bad = []
    for name, tile, ign, wx, cfg, kw in cases:
        fp_memo, fp_ref = _run_pair(tile, ign, wx, cfg, **kw)
        if fp_memo != fp_ref:
            bad.append(name)
    assert not bad, (
        f"B3 BYTE-IDENTITY BROKEN (memo vs WGRESP_NO_ROS_MEMO=1) on: {bad}")


def test_b3_byte_identity_with_cap_forced_tiny():
    """Past the cap the memo must recompute identical values, never diverge."""
    t = _tile_random(404, 36, 36, 140.0, continuous=True)
    ign = [geo.rc_to_latlon(t.spec, 18, 4)]
    wx = _wx(ign[0])
    cfg = ResponseConfig()
    old_cap = spread._ROS_CACHE_MAX
    try:
        spread._ROS_CACHE_MAX = 5
        fp_memo, fp_ref = _run_pair(t, ign, wx, cfg)
    finally:
        spread._ROS_CACHE_MAX = old_cap
    assert fp_memo == fp_ref, "cap-bound memo diverged from reference"
    # and the tiny-cap run matches the uncapped memo run too
    uncapped = spread.compute_arrival(t, ign, wx, T0, cfg)
    assert _fingerprint(uncapped) == fp_memo


# ---------------------------------------------------------------------------
# 5c — bounded memory across repeated invocations
# ---------------------------------------------------------------------------

class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _rss_bytes():
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = kernel32.K32GetProcessMemoryInfo
    fn.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PMC),
                   ctypes.wintypes.DWORD]
    fn.restype = ctypes.wintypes.BOOL
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    ok = fn(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    if not ok:
        raise OSError(f"GetProcessMemoryInfo failed "
                      f"(err={ctypes.get_last_error()})")
    return int(pmc.WorkingSetSize)


def _module_container_sizes(mod):
    out = {}
    for name, v in vars(mod).items():
        if isinstance(v, (dict, set, list)):
            out[name] = len(v)
    return out


def test_b3_repeated_calls_do_not_accrete_memory():
    t = _tile_random(505, 42, 42, 130.0, continuous=True)
    ign = [geo.rc_to_latlon(t.spec, 21, 5)]
    wx = _wx(ign[0])
    cfg = ResponseConfig()
    for _ in range(3):                                  # warmup / steady state
        spread.compute_arrival(t, ign, wx, T0, cfg)
    before_mod = _module_container_sizes(spread)
    rss0 = _rss_bytes()
    for _ in range(25):
        spread.compute_arrival(t, ign, wx, T0, cfg)
    rss1 = _rss_bytes()
    after_mod = _module_container_sizes(spread)
    assert after_mod == before_mod, (
        f"spread module-level containers grew across calls: "
        f"{before_mod} -> {after_mod} (cross-run cache accretion)")
    growth_mb = (rss1 - rss0) / 1e6
    # A leaked per-invocation ros_cache would add several MB per call
    # (25 calls x ~30k keys). Steady-state noise stays far below this bar.
    assert growth_mb < 40.0, (
        f"RSS grew {growth_mb:.1f} MB over 25 repeated compute_arrival calls "
        f"— per-invocation caches are not being released")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
