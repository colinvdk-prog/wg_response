"""Lane L2: what a REGIONAL CUT (SAFETY SEMANTICS 53) does to ETAs — the
same fire, the same weather, the same grid, the live ensemble config, on
(a) the domain's own LFPS tile (direct) and (b) the domain cut from a
neighbouring LFPS job (the Colorado pair of region_agreement.py: domain B
-107.15..-106.25 x 39.15..39.8 at 136.4 m, cut from job A
-107.4..-106.25 x 39.15..39.9). Every protected home on the grid within
the client range: p10 / p50 hours and the ladder bucket (<= 24 h, <= 72 h,
later / unreached). Writes results/region_eta_compare.json."""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = [sys.argv[0], "none"]
import run_harness as R  # noqa: E402  (this worktree's wgresponse)
import region_agreement as RA  # noqa: E402
import live_cfg  # noqa: E402


def bucket(h):
    return "<=24h" if h <= 24 else ("<=72h" if h <= 72 else "later")


def main():
    import psutil
    psutil.Process().cpu_affinity([0, 1])
    from wgresponse import geo, spread, terrain as T
    from wgresponse.contracts import BBox, LatLon
    from wgresponse.weather import MockWeather
    kw, _ = live_cfg.capture()
    cfg = kw["cfg"]
    pairs = json.load(open(os.path.join(HERE, "pairs.json")))
    A, B = pairs[0]["A"], pairs[0]["B"]
    abox = BBox(west=A["west"], south=A["south"], east=A["east"],
                north=A["north"])
    bbox = BBox(west=B["west"], south=B["south"], east=B["east"],
                north=B["north"])
    lfps = T.LfpsTerrain(cache_dir=R.MIRROR, dep_probe=lambda: None,
                         canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    import lzw_shim
    lzw_shim.install()
    direct = lfps.tile(bbox, B["cell_m"])
    cut = RA.OnePiece(lfps, abox).cut(bbox, B["cell_m"])
    from wgresponse import fuels
    roster = list(kw["registry"].protected_clients())
    fires = [LatLon(39.61618924809201, -106.58692478626823)]
    for pid in ("eagle_vail_co", "vail_co", "eagle_co", "beaver_creek_co"):
        hs = [c for c in roster if c.client_id.startswith(pid + "-c")]
        ctr = LatLon(sum(h.loc.lat for h in hs) / len(hs),
                     sum(h.loc.lon for h in hs) / len(hs))
        for brg, dist in ((225.0, 3000.0), (180.0, 4000.0), (270.0, 5000.0)):
            f = geo.destination(ctr, brg, dist)
            rc = geo.latlon_to_rc(direct.spec, f)
            if rc is None or not fuels.is_burnable(
                    int(direct.fuel.get(*rc))):
                continue
            if all(geo.haversine_m(f, g) > 2000.0 for g in fires):
                fires.append(f)
            break
    summary = {"fires": [], "ladder_bucket_moves": {"same": 0, "earlier": 0,
                                                    "later": 0},
               "reached_direct": 0, "reached_cut": 0}
    for fire in fires:
        res = one_fire(fire, direct, cut, cfg, roster, bucket)
        summary["fires"].append(res)
        for k in ("same", "earlier", "later"):
            summary["ladder_bucket_moves"][k] += res["ladder_bucket_moves"][k]
        summary["reached_direct"] += res["reached_direct"]
        summary["reached_cut"] += res["reached_cut"]
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "region_eta_compare.json"),
              "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({**{k: v for k, v in summary.items() if k != "fires"},
                      "per_fire": [{k: v for k, v in r.items()
                                    if k != "rows"}
                                   for r in summary["fires"]]},
                     indent=1), flush=True)
    if spread._MEMBER_POOL is not None:
        spread._MEMBER_POOL.shutdown(wait=True, cancel_futures=True)
    os._exit(0)


def one_fire(fire, direct, cut, cfg, roster, bucket):
    from wgresponse import geo, spread
    from wgresponse.weather import MockWeather
    now = time.time()
    wx = MockWeather("harness-redflag", base_wind_mps=12.5, dir_deg=270.0,
                     rh_pct=12.0, temp_c=35.0, gust_factor=1.4,
                     t0_s=now).forecast(fire, cfg.horizon_h)
    homes = [c for c in roster
             if geo.latlon_to_rc(direct.spec, c.loc) is not None
             and geo.haversine_m(c.loc, fire) <= 60_000.0]
    out = {"fire": [fire.lat, fire.lon], "homes": len(homes),
           "ensemble_n": cfg.ensemble_n, "rows": []}
    ens = {}
    for name, tile in (("direct", direct), ("cut", cut)):
        t = time.time()
        ens[name] = spread.ensemble_arrival(tile, [fire], wx, now, cfg,
                                            incident_id="eta-compare")
        out[f"{name}_s"] = round(time.time() - t, 1)
    d10, d50 = [], []
    moved = {"same": 0, "earlier": 0, "later": 0}
    for c in homes:
        e = {k: spread.eta_at(v, c.loc).hours_out(now) for k, v in ens.items()}
        (p10a, p50a, _), (p10b, p50b, _) = e["direct"], e["cut"]
        row = {"client": c.client_id, "direct_p10_h": round(p10a, 2),
               "direct_p50_h": round(p50a, 2), "cut_p10_h": round(p10b, 2),
               "cut_p50_h": round(p50b, 2),
               "direct_bucket": bucket(p50a), "cut_bucket": bucket(p50b)}
        out["rows"].append(row)
        if p50a < 1e9 and p50b < 1e9:
            d50.append(p50b - p50a)
        if p10a < 1e9 and p10b < 1e9:
            d10.append(p10b - p10a)
        if bucket(p50a) == bucket(p50b):
            moved["same"] += 1
        elif p50b < p50a:
            moved["earlier"] += 1
        else:
            moved["later"] += 1

    def stats(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return {"n": len(xs), "median_h": round(xs[len(xs) // 2], 2),
                "mean_abs_h": round(sum(abs(x) for x in xs) / len(xs), 2),
                "min_h": round(xs[0], 2), "max_h": round(xs[-1], 2)}

    out["p50_cut_minus_direct"] = stats(d50)
    out["p10_cut_minus_direct"] = stats(d10)
    out["ladder_bucket_moves"] = moved
    out["reached_direct"] = sum(1 for r in out["rows"]
                                if r["direct_p50_h"] < 1e9)
    out["reached_cut"] = sum(1 for r in out["rows"] if r["cut_p50_h"] < 1e9)
    return out


if __name__ == "__main__":
    main()
