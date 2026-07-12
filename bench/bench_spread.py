"""bench_spread — spread-engine performance benchmark (wgresponse.spread).

Measures, on deterministic SyntheticTerrain 'ridge' tiles (mixed GR2/GS2/SH5/
TL3 fuel patches + a central ridge so slope/aspect are exercised) with a
constant-8 m/s westerly MockWeather forecast:

  1. compute_arrival single-member wall-clock at 128/256/512 (and 768 when the
     512-projected time stays under --max768-s), with settled-cells/s and
     edge-relaxations/s throughput.
  2. ensemble_arrival at 256x256 with cfg.ensemble_n=6 (nominal + 5 perturbed)
     including the pure-Python per-cell quantile pass.
  3. B3 ROS-memo before/after (Sprint 2 ticket B3, Council-1 perf S1+S2):
     wall-clock with the memo ON vs the WGRESP_NO_ROS_MEMO=1 reference path on
     west-line FULL-BURN tiles at --memo-sizes (default 256,512) plus the
     6-member 256x256 ensemble, each on the stock CONTINUOUS-slope ridge AND a
     1-deg QUANTIZED copy (integer slope/aspect — what LANDFIRE SlpD/Asp
     actually deliver). Reports full directional-ROS evaluations, fuel-bed
     builds and the memo hit rate (counted in separate uninstrumented-timing
     runs). Replaces the pre-B3 accounting section that measured the old
     per-(cell,hour) cache at a 0.0% hit rate (perf_review.md §1.7).
  4. cProfile of the 256x256 single member (top cumulative functions).
  5. Peak RSS of the 512x512 6-member ensemble, isolated in a child process
     (--mem-child mode; add --tracemalloc for a traced-python-alloc peak too).
  6. The operational-budget table: members/incidents per replan_period_s.

Deterministic: fixed seeds; wall-clock is only ever *measured*.
Usage:  py -3 bench/bench_spread.py [--sizes 128,256,512] [--skip-768]
        [--skip-mem] [--skip-profile] [--skip-memo] [--memo-sizes 256,512]
        [--max768-s 300]
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import os
import pstats
import subprocess
import sys
import time
from contextlib import contextmanager

from _benchlib import REPO_ROOT, env_banner, fmt_s, peak_rss_bytes, table, timeit

from wgresponse import geo, rothermel, spread
from wgresponse.contracts import BBox, Raster, ResponseConfig, TerrainTile, UNREACHED
from wgresponse.terrain import SyntheticTerrain, M_PER_DEG_LAT, _m_per_deg_lon
from wgresponse.weather import MockWeather

SEED = "bench-spread-v1"
T0_S = 1_780_000_000.0            # matches MockWeather default t0
NORTH_LAT = 34.5
WEST_LON = -118.5
CELL_M = 90.0                     # cfg.grid_cell_m default
N_EDGE_OFFSETS = 16


def make_cfg(**over) -> ResponseConfig:
    cfg = ResponseConfig()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def make_tile(n: int):
    """~n x n TerrainTile at CELL_M meters (ridge kind, deterministic)."""
    south = NORTH_LAT - n * CELL_M / M_PER_DEG_LAT
    lat_c = (NORTH_LAT + south) / 2.0
    east = WEST_LON + n * CELL_M / _m_per_deg_lon(lat_c)
    bbox = BBox(west=WEST_LON, south=south, east=east, north=NORTH_LAT)
    terr = SyntheticTerrain(seed=SEED, kind="ridge", patch_cells=4)
    return terr.tile(bbox, CELL_M)


def make_wx(jitter_frac: float = 0.0):
    # 8 m/s sustained from the west (pushes fire east), dry: RH 25 %, 30 C.
    # jitter_frac > 0 adds seeded per-hour wind/RH wobble (deterministic) —
    # the B3 memo bench uses 0.15 for its honest hour-varying-weather rows.
    return MockWeather(seed=SEED, base_wind_mps=8.0, dir_deg=270.0,
                       rh_pct=25.0, temp_c=30.0, jitter_frac=jitter_frac,
                       t0_s=T0_S)


def center_ignition(tile):
    spec = tile.spec
    return [geo.rc_to_latlon(spec, spec.nrows // 2, spec.ncols // 2)]


def west_line_ignition(tile):
    """Ignite the whole west edge: with the westerly wind the entire grid
    settles, giving the full-coverage (worst-case) per-member cost."""
    spec = tile.spec
    return [geo.rc_to_latlon(spec, r, 0) for r in range(spec.nrows)]


IGNITIONS = {"center": center_ignition, "west-line": west_line_ignition}


def settled_stats(arr):
    """(settled_count, max_arrival_h) over a finished arrival raster."""
    settled = 0
    tmax = 0.0
    for v in arr.data:
        if v != UNREACHED:
            settled += 1
            if v > tmax:
                tmax = v
    return settled, (tmax - T0_S) / 3600.0 if settled else 0.0


# ---------------------------------------------------------------------------
# 1. single-member scaling
# ---------------------------------------------------------------------------

def bench_single(sizes, cfg, max768_s: float, skip768: bool,
                 ignition: str = "center"):
    ign_fn = IGNITIONS[ignition]
    print(f"\n== 1. compute_arrival single member (SyntheticTerrain ridge, "
          f"8 m/s W wind, horizon 96 h, {ignition} ignition) ==")
    t = table(["grid", "cells", "settled", "wall_s", "settled_cells/s",
               "edge_relax/s", "max_arr_h"],
              [9, 9, 9, 8, 16, 13, 10])
    t.print_header()
    results = {}
    wx_provider = make_wx()
    for n in sizes:
        tile = make_tile(n)
        wx = wx_provider.forecast(geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                                                   tile.spec.ncols // 2),
                                  cfg.horizon_h)
        ign = ign_fn(tile)
        arr, dt = timeit(spread.compute_arrival, tile, ign, wx, T0_S, cfg)
        settled, max_h = settled_stats(arr)
        cells = tile.spec.nrows * tile.spec.ncols
        results[n] = {"cells": cells, "settled": settled, "wall_s": dt}
        t.print_row([f"{tile.spec.nrows}x{tile.spec.ncols}", cells, settled,
                     fmt_s(dt), f"{settled / dt:,.0f}",
                     f"{settled * N_EDGE_OFFSETS / dt:,.0f}", f"{max_h:.1f}"])
    if not skip768 and 512 in results:
        proj = results[512]["wall_s"] * (768 * 768) / (512 * 512) * 1.3
        if proj <= max768_s:
            tile = make_tile(768)
            wx = wx_provider.forecast(
                geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                                 tile.spec.ncols // 2), cfg.horizon_h)
            arr, dt = timeit(spread.compute_arrival, tile,
                             ign_fn(tile), wx, T0_S, cfg)
            settled, max_h = settled_stats(arr)
            cells = tile.spec.nrows * tile.spec.ncols
            results[768] = {"cells": cells, "settled": settled, "wall_s": dt}
            t.print_row([f"{tile.spec.nrows}x{tile.spec.ncols}", cells, settled,
                         fmt_s(dt), f"{settled / dt:,.0f}",
                         f"{settled * N_EDGE_OFFSETS / dt:,.0f}", f"{max_h:.1f}"])
        else:
            print(f"(768x768 skipped: projected {proj:.0f} s > {max768_s:.0f} s cap)")
    return results


# ---------------------------------------------------------------------------
# 2. ensemble
# ---------------------------------------------------------------------------

def bench_ensemble(n: int, cfg):
    print(f"\n== 2. ensemble_arrival {n}x{n}, ensemble_n={cfg.ensemble_n} "
          f"(={cfg.ensemble_n - 1} perturbed + nominal) ==")
    tile = make_tile(n)
    wx = make_wx().forecast(geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                                             tile.spec.ncols // 2), cfg.horizon_h)
    ign = center_ignition(tile)
    ens, dt = timeit(spread.ensemble_arrival, tile, ign, wx, T0_S, cfg)
    # isolate the quantile pass by re-running it on the members
    cols = [m.data for m in ens.members]
    ncells = tile.spec.nrows * tile.spec.ncols
    tq0 = time.perf_counter()
    for i in range(ncells):
        vals = sorted(col[i] for col in cols)
        spread._quantile(vals, 0.10)
        spread._quantile(vals, 0.50)
        spread._quantile(vals, 0.90)
    tq = time.perf_counter() - tq0
    print(f"members={len(ens.members)}  wall={dt:.1f} s  "
          f"(~{dt / len(ens.members):.1f} s/member incl. quantiles)  "
          f"quantile pass alone ~{tq:.1f} s")
    return {"n": n, "members": len(ens.members), "wall_s": dt, "quantile_s": tq}


# ---------------------------------------------------------------------------
# 3. B3 ROS memo before/after (before = WGRESP_NO_ROS_MEMO=1 reference path)
# ---------------------------------------------------------------------------

def quantize_tile(tile: TerrainTile) -> TerrainTile:
    """Copy of `tile` with slope/aspect rounded to integer degrees — what
    LANDFIRE SlpD/Asp deliver on production LFPS tiles, and the realistic
    memo-sharing case (perf_review.md §1.7: 83.2% duplicate ROS evals)."""
    spec = tile.spec
    return TerrainTile(
        spec=spec,
        fuel=Raster(spec, list(tile.fuel.data)),
        slope_deg=Raster(spec, [float(round(v)) for v in tile.slope_deg.data]),
        aspect_deg=Raster(spec, [float(round(v)) for v in tile.aspect_deg.data]),
        elev_m=Raster(spec, list(tile.elev_m.data)),
        source=tile.source + "-quantized",
    )


@contextmanager
def ros_counters():
    """Count fuel-bed builds + full directional-ROS evaluations via runtime
    wrappers on rothermel._bed / surface_ros_from_bed (works for BOTH paths:
    the classic wrappers route through the same module globals). Restored in
    finally; product files untouched."""
    counts = {"bed": 0, "full": 0}
    orig_bed = rothermel._bed
    orig_full = rothermel.surface_ros_from_bed

    def w_bed(*a):
        counts["bed"] += 1
        return orig_bed(*a)

    def w_full(*a):
        counts["full"] += 1
        return orig_full(*a)

    rothermel._bed = w_bed
    rothermel.surface_ros_from_bed = w_full
    try:
        yield counts
    finally:
        rothermel._bed = orig_bed
        rothermel.surface_ros_from_bed = orig_full


def _timeit_no_memo(fn, *args):
    """(result, seconds) with the B3 memo disabled (reference path)."""
    os.environ["WGRESP_NO_ROS_MEMO"] = "1"
    try:
        return timeit(fn, *args)
    finally:
        os.environ.pop("WGRESP_NO_ROS_MEMO", None)


def _identical_output(a, b) -> bool:
    """Exact float equality of arrival payloads (Raster or EnsembleArrival)."""
    if hasattr(a, "members"):
        return (len(a.members) == len(b.members)
                and all(x.data == y.data for x, y in zip(a.members, b.members))
                and a.p10.data == b.p10.data and a.p50.data == b.p50.data
                and a.p90.data == b.p90.data)
    return a.data == b.data


def _memo_case(label, fn, args, settled_of):
    """One before/after row: reference timing, memo timing (both
    uninstrumented), a separate counted memo run for evals/hit rate, plus the
    B3 byte-identity check (memo output == reference output, exact floats)."""
    ref, before_s = _timeit_no_memo(fn, *args)
    out, after_s = timeit(fn, *args)
    with ros_counters() as counts:
        fn(*args)
    settled = settled_of(out)
    hit_rate = 1.0 - counts["full"] / max(1, settled)
    return {"label": label, "settled": settled, "before_s": before_s,
            "after_s": after_s, "speedup": before_s / after_s,
            "full": counts["full"], "bed": counts["bed"],
            "hit_rate": hit_rate, "byte_id": _identical_output(ref, out)}


def bench_memo(sizes, cfg):
    print(f"\n== 3. B3 ROS memo before/after (before = WGRESP_NO_ROS_MEMO=1 "
          f"reference; west-line FULL-BURN; ens = center, n={cfg.ensemble_n}) ==")
    if os.environ.get("WGRESP_NO_ROS_MEMO"):
        print("   (note: WGRESP_NO_ROS_MEMO was set in the environment; "
              "cleared for the 'after' runs)")
        os.environ.pop("WGRESP_NO_ROS_MEMO", None)
    t = table(["case", "settled", "before_s", "after_s", "speedup",
               "ros_evals", "bed_builds", "hit_rate", "byte_id"],
              [26, 9, 8, 8, 7, 10, 10, 8, 8])
    t.print_header()
    providers = (("steady-wx", make_wx()), ("vary-wx", make_wx(0.15)))
    results = []

    def emit(row):
        results.append(row)
        t.print_row([row["label"], f"{row['settled']:,}",
                     fmt_s(row["before_s"]), fmt_s(row["after_s"]),
                     f"{row['speedup']:.2f}x", f"{row['full']:,}",
                     f"{row['bed']:,}", f"{row['hit_rate']:.1%}",
                     "OK" if row["byte_id"] else "MISMATCH"])

    for n in sizes:
        base = make_tile(n)
        loc = geo.rc_to_latlon(base.spec, base.spec.nrows // 2,
                               base.spec.ncols // 2)
        for wx_name, provider in providers:
            wx = provider.forecast(loc, cfg.horizon_h)
            for variant, tile in (("cont", base),
                                  ("quant", quantize_tile(base))):
                ign = west_line_ignition(tile)
                emit(_memo_case(
                    f"{n}x{n} {variant} {wx_name}",
                    spread.compute_arrival, (tile, ign, wx, T0_S, cfg),
                    lambda arr: settled_stats(arr)[0]))

    # 6-member ensemble (center ignition, matches §2); counts aggregate over
    # all members, settled = sum of per-member settled cells.
    ens_n = 256
    base = make_tile(ens_n)
    loc = geo.rc_to_latlon(base.spec, base.spec.nrows // 2,
                           base.spec.ncols // 2)
    for wx_name, provider in providers:
        wx = provider.forecast(loc, cfg.horizon_h)
        for variant, tile in (("cont", base), ("quant", quantize_tile(base))):
            ign = center_ignition(tile)
            emit(_memo_case(
                f"ens6 {ens_n} {variant} {wx_name}",
                spread.ensemble_arrival, (tile, ign, wx, T0_S, cfg),
                lambda ens: sum(settled_stats(m)[0] for m in ens.members)))

    print("(hit_rate = 1 - ros_evals/settled, from a separate counted run; "
          "timings are uninstrumented. 'quant' = integer-degree slope/aspect "
          "copy = production LANDFIRE quantization; 'vary-wx' = seeded "
          "per-hour wind/RH wobble, the honest fragmented-weather case. "
          "byte_id = memo output exactly equals the reference output.)")
    if not all(r["byte_id"] for r in results):
        bad = [r["label"] for r in results if not r["byte_id"]]
        raise AssertionError(
            f"B3 BYTE-IDENTITY VIOLATION on: {bad} — memo raster diverged "
            f"from the WGRESP_NO_ROS_MEMO=1 reference path")
    return results


# ---------------------------------------------------------------------------
# 4. cProfile
# ---------------------------------------------------------------------------

def bench_profile(n: int, cfg, top: int = 12):
    print(f"\n== 4. cProfile compute_arrival @ {n}x{n} (top {top} cumulative) ==")
    tile = make_tile(n)
    wx = make_wx().forecast(geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                                             tile.spec.ncols // 2), cfg.horizon_h)
    ign = center_ignition(tile)
    pr = cProfile.Profile()
    pr.enable()
    spread.compute_arrival(tile, ign, wx, T0_S, cfg)
    pr.disable()
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf).sort_stats("cumulative")
    stats.print_stats(top)
    print(buf.getvalue())


# ---------------------------------------------------------------------------
# 5. memory (child-process isolation)
# ---------------------------------------------------------------------------

def mem_child(n: int, cfg, use_tracemalloc: bool):
    tile = make_tile(n)
    wx = make_wx().forecast(geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                                             tile.spec.ncols // 2), cfg.horizon_h)
    ign = center_ignition(tile)
    peak0, cur0 = peak_rss_bytes()
    traced_peak = None
    if use_tracemalloc:
        import tracemalloc
        tracemalloc.start()
    t0 = time.perf_counter()
    ens = spread.ensemble_arrival(tile, ign, wx, T0_S, cfg)
    dt = time.perf_counter() - t0
    if use_tracemalloc:
        import tracemalloc
        _, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    peak1, cur1 = peak_rss_bytes()
    print(json.dumps({
        "n": n, "members": len(ens.members), "wall_s": dt,
        "rss_before_mb": cur0 / 1e6, "rss_after_mb": cur1 / 1e6,
        "peak_rss_mb": peak1 / 1e6,
        "traced_peak_mb": (traced_peak / 1e6) if traced_peak else None,
    }))


def bench_memory(n: int, use_tracemalloc: bool):
    print(f"\n== 5. peak RSS: {n}x{n} ensemble (ensemble_n=6) in a child process ==")
    cmd = [sys.executable, str(REPO_ROOT / "bench" / "bench_spread.py"),
           "--mem-child", str(n)]
    if use_tracemalloc:
        cmd.append("--tracemalloc")
    out = subprocess.run(cmd, capture_output=True, text=True,
                         cwd=str(REPO_ROOT), timeout=1800)
    line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "{}"
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        print("child failed:", out.stdout, out.stderr)
        return None
    print(f"members={d['members']}  wall={d['wall_s']:.1f} s  "
          f"RSS before={d['rss_before_mb']:.0f} MB  after={d['rss_after_mb']:.0f} MB  "
          f"PEAK={d['peak_rss_mb']:.0f} MB"
          + (f"  traced-python-peak={d['traced_peak_mb']:.0f} MB"
             if d.get("traced_peak_mb") else ""))
    return d


# ---------------------------------------------------------------------------
# 6. operational budget
# ---------------------------------------------------------------------------

def budget_table(single_results, ens_result, cfg):
    print("\n== 6. operational budget (replan_period_s = "
          f"{cfg.replan_period_s:.0f} s, ensemble_n = {cfg.ensemble_n}) ==")
    t = table(["grid", "s/member", "s/incident(6mem)", "max concurrent incidents/cycle"],
              [9, 10, 17, 31])
    t.print_header()
    for n, r in sorted(single_results.items()):
        per_member = r["wall_s"]
        per_incident = per_member * cfg.ensemble_n
        if ens_result and ens_result["n"] == n:
            per_incident = ens_result["wall_s"]      # measured, incl. quantiles
        t.print_row([f"{n}x{n}", fmt_s(per_member), fmt_s(per_incident),
                     f"{int(cfg.replan_period_s // per_incident)}"])
    print("(spread only - dispatch/alert/feed overhead measured in bench_cycle.py)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="128,256,512")
    ap.add_argument("--ignition", choices=sorted(IGNITIONS), default="center")
    ap.add_argument("--skip-768", action="store_true")
    ap.add_argument("--max768-s", type=float, default=300.0)
    ap.add_argument("--skip-mem", action="store_true")
    ap.add_argument("--skip-profile", action="store_true")
    ap.add_argument("--skip-memo", action="store_true",
                    help="skip the B3 memo before/after section")
    ap.add_argument("--memo-sizes", default="256,512",
                    help="grid sizes for the B3 full-burn before/after rows")
    ap.add_argument("--mem-child", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--tracemalloc", action="store_true")
    args = ap.parse_args()

    cfg = make_cfg()      # stock ResponseConfig: horizon 96 h, ensemble_n 6
    if args.mem_child:
        mem_child(args.mem_child, cfg, args.tracemalloc)
        return

    print("bench_spread |", env_banner())
    print(f"cfg: horizon_h={cfg.horizon_h} ensemble_n={cfg.ensemble_n} "
          f"cell_m={CELL_M} terrain=ridge(mixed fuels) wind=8 m/s W seed={SEED!r}")
    sizes = [int(s) for s in args.sizes.split(",")]
    singles = bench_single(sizes, cfg, args.max768_s, args.skip_768,
                           args.ignition)
    ens = bench_ensemble(256, cfg)
    if not args.skip_memo:
        bench_memo([int(s) for s in args.memo_sizes.split(",")], cfg)
    if not args.skip_profile:
        bench_profile(256, cfg)
    if not args.skip_mem:
        bench_memory(512, args.tracemalloc)
    budget_table(singles, ens, cfg)


if __name__ == "__main__":
    main()
