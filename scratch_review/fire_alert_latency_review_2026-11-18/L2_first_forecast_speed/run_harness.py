"""Lane L2: Lane C's new-fire harness (C_alerting/harness_new_fire.py, on
the LIVE watcher's exact config via live_cfg.capture, pinned to 2 CPUs,
real NWS + red-flag weather, the real 520-home registry, AlertEngine,
dispatch) run against THIS worktree's code, with the build lane's knobs.

Scenarios
  co_cold_before   a new fire at the Beaver Creek centroid (a 300k-cell
                   western-Colorado domain NOT in the terrain mirror):
                   today's stack — the harness guard refuses the LFPS job,
                   no bundled tile covers Colorado, so no forecast here
                   (live: two LFPS jobs, 155-462 s each, first).
  co_cold_after    the same fire with REGIONAL TERRAIN (SAFETY SEMANTICS
                   53): the domain is cut from a real regional LFPS job
                   zip (the mirror's Willow-scale job, -107.4..-106.25 x
                   39.15..39.9, 0.86 deg^2 — the stand-in for the lattice
                   pieces), re-encoded uncompressed (this machine has no
                   imagecodecs; live decodes LZW in C). 0 LFPS calls.
  contention2      Lane C's: a Willow-scale refresh is queued in the member
                   pool when a new Paradise fire's first forecast starts;
                   add --priority for first_forecast_priority (54).
Usage: python run_harness.py <scenario> [--priority] [--tag name]
Writes results/<scenario>[_<tag>].json beside this script.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
REVIEW = r"C:\Users\colin\wg_response\scratch_review\fire_alert_latency_review_2026-11-18"
SCRATCH = os.path.join(os.environ["TEMP"], "claude", "78600f61c23682e1",
                       "301cc633-83bf-4a52-9972-a841bce65068", "scratchpad")
os.environ.setdefault("WGC_TCACHE", os.path.join(SCRATCH, "tcache"))
MIRROR = os.path.join(SCRATCH, "mirror_pull")
REGION_CACHE = os.path.join(SCRATCH, "region_cache")

sys.path.insert(0, os.path.join(REVIEW, "C_alerting"))
import harness_new_fire as H  # noqa: E402  (inserts the main checkout)
import live_cfg  # noqa: E402

sys.path.insert(0, WORKTREE)                 # THIS worktree's wgresponse
sys.path.insert(0, HERE)
live_cfg.REPO = WORKTREE
for m in [m for m in list(sys.modules) if m.startswith("wgresponse")]:
    del sys.modules[m]
import wgresponse  # noqa: E402
assert os.path.dirname(wgresponse.__file__).startswith(WORKTREE), \
    wgresponse.__file__

A_AOI = (-107.4, 39.15, -106.25, 39.9)


def _reencode_region():
    """A's two real job zips, decoded (pure-Python LZW shim) and written
    back uncompressed under their canonical cache names in REGION_CACHE."""
    from wgresponse import terrain as T
    from wgresponse.contracts import BBox
    import lzw_shim
    import tifffile
    lzw_shim.install()
    aoi = BBox(*A_AOI)
    lt = T.LfpsTerrain(cache_dir=REGION_CACHE,
                       canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    src = T.LfpsTerrain(cache_dir=MIRROR,
                        canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    os.makedirs(REGION_CACHE, exist_ok=True)
    for canopy in (False, True):
        dst = lt.job_zip_path(aoi, canopy)
        if os.path.exists(dst):
            continue
        body = open(src.job_zip_path(aoi, canopy), "rb").read()
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            tif = [n for n in zf.namelist() if n.lower().endswith(".tif")][0]
            tfw = [n for n in zf.namelist() if n.lower().endswith(".tfw")][0]
            arr = tifffile.imread(io.BytesIO(zf.read(tif)))
            tfw_b = zf.read(tfw)
        out = io.BytesIO()
        tifffile.imwrite(out, arr, photometric="minisblack",
                         planarconfig="separate")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("r.tif", out.getvalue())
            zf.writestr("r.tfw", tfw_b)
        with open(dst, "wb") as f:
            f.write(buf.getvalue())
    return lt


class _Piece:
    def __init__(self, aoi):
        self.i = self.j = 0
        self.key = ("A", 0)
        self.aoi = aoi


def regional_over(guard, lfps_region):
    """A RegionalTerrain whose only ready piece is A's real job (the
    production cut code; a domain outside A falls through)."""
    from wgresponse import terrain as T
    from wgresponse.contracts import BBox
    aoi = BBox(*A_AOI)

    class OnePiece(T.RegionalTerrain):
        def _needed(self, bbox, cell_m):
            _p, spec, lats, lons, _r, _c = super()._needed(bbox, cell_m)
            inside = (bbox.west >= aoi.west and bbox.east <= aoi.east
                      and bbox.south >= aoi.south
                      and bbox.north <= aoi.north)
            if not inside:
                return None, spec, lats, lons, _r, _c
            return ([_Piece(aoi)], spec, lats, lons,
                    {0: list(range(spec.nrows))},
                    {0: list(range(spec.ncols))})

    return OnePiece(lfps_region, primary=guard)


def main():
    import psutil
    import logging
    scenario = sys.argv[1]
    priority = "--priority" in sys.argv
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else ""
    psutil.Process().cpu_affinity(H.AFFINITY)
    logging.basicConfig(level=logging.WARNING)
    t0_ref = [time.monotonic()]
    log_h = H.TimedLog(t0_ref)
    for name in ("wgresponse.alerts", "wgresponse.notify",
                 "wgresponse.terrain", "wgresponse.threat"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(log_h)
    from wgresponse import contracts, terrain as T
    from wgresponse.contracts import LatLon
    extra = {}
    if priority:
        extra["first_forecast_priority"] = True
    if scenario == "co_cold_after":
        extra["terrain_regions"] = True
    env = H.build(extra)
    roster = env["roster"]

    def centroid(prefix):
        hs = [c for c in roster if c.client_id.startswith(prefix + "-c")]
        return LatLon(sum(h.loc.lat for h in hs) / len(hs),
                      sum(h.loc.lon for h in hs) / len(hs))

    now = time.time()
    results = {"scenario": scenario, "priority": priority,
               "affinity": psutil.Process().cpu_affinity(),
               "cfg_extra": extra, "cycles": []}
    if scenario in ("co_cold_before", "co_cold_after"):
        if scenario == "co_cold_after":
            lr = _reencode_region()
            reg = regional_over(env["guard"], lr)
            env["mon"]._terrain = T.BundledFallbackTerrain(
                reg, os.path.join(WORKTREE, "data", "tiles"))
            env["terrain"] = env["mon"]._terrain
        # 3 km SW (upwind) of the Beaver Creek homes' centroid: the centroid
        # itself is a developed (FBFM40 91, non-burnable) cell
        from wgresponse import geo
        loc = geo.destination(centroid("beaver_creek_co"), 225.0, 3000.0)
        results["fire"] = [loc.lat, loc.lon]
        fire = H.new_fire(contracts, loc, now, "Harness cold Colorado fire")
        env["feed"].incidents = [fire]
        r1, res = H.run_cycle(env, now, log_h, t0_ref, "cycle1_new_fire")
        r1["terrain_s"] = {k: v.get("terrain_s") for k, v in
                           res.timings.get("incidents", {}).items()}
        r1["spread_s"] = {k: v.get("spread_s") for k, v in
                          res.timings.get("incidents", {}).items()}
        r1["cells"] = {k: v.get("cells") for k, v in
                       res.timings.get("incidents", {}).items()}
        if scenario == "co_cold_after":
            r1["regional_stats"] = dict(reg.stats)
        results["cycles"].append(r1)
    elif scenario == "contention2":
        big = H.new_fire(contracts, centroid("eagle_vail_co"), now,
                         "Harness Willow-scale")
        env["feed"].incidents = [big]
        ra, _ = H.run_cycle(env, now, log_h, t0_ref,
                            "A_big_fire_first_forecast")
        results["cycles"].append(ra)
        rb1, _ = H.run_cycle(env, now + 1200.0, log_h, t0_ref,
                             "B1_big_refresh_submitted")
        results["cycles"].append(rb1)
        time.sleep(1.0)
        small = H.new_fire(contracts, centroid("paradise_ca"), now + 1201.0,
                           "Harness new Paradise fire")
        env["feed"].incidents = [small, big]
        t = time.monotonic()
        rb2, _ = H.run_cycle(env, now + 1201.0, log_h, t0_ref,
                             "B2_new_fire_while_refresh_runs")
        results["cycles"].append(rb2)
        kept = env["mon"]._kept.get(big.incident_id)
        pend = kept.pending if kept is not None else None
        results["refresh_still_running_after_B2"] = bool(
            pend is not None and not pend.done())
        tt = time.monotonic()
        if pend is not None:
            pend.result()
        results["bg_refresh_tail_after_B2_s"] = round(time.monotonic() - tt,
                                                      2)
        results["bg_refresh_done_after_B2_start_s"] = round(
            time.monotonic() - t, 2)
        from wgresponse import spread
        d = spread._DISPATCH
        results["dispatched"] = list(d.dispatched) if d is not None else None
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    fn = os.path.join(HERE, "results", scenario + (f"_{tag}" if tag else "")
                      + ".json")
    with open(fn, "w") as f:
        json.dump(results, f, indent=1, default=str)
    print(json.dumps(results, indent=1, default=str)[:5000])
    from wgresponse import spread
    if spread._MEMBER_POOL is not None:
        spread._MEMBER_POOL.shutdown(wait=True, cancel_futures=True)
    os._exit(0)


if __name__ == "__main__":
    main()
