"""Lane L2: is a REGIONAL CUT (SAFETY SEMANTICS 53) systematically more or
less pessimistic than the domain's own LFPS job? For each real pair of
region_agreement.py, several burnable ignitions inside domain B, the live
config's nominal member under red-flag weather on (a) B's own tile and
(b) B's grid cut from job A: the cells reached within 12 h / 24 h / 72 h.
A bias would show as the cut reaching less (or more) in most runs.
Writes results/region_bias_check.json."""
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


def main():
    import psutil
    psutil.Process().cpu_affinity([0, 1])
    from wgresponse import fuels, geo, spread, terrain as T
    from wgresponse.contracts import BBox, LatLon
    from wgresponse.weather import MockWeather
    import lzw_shim
    lzw_shim.install()
    kw, _ = live_cfg.capture()
    cfg = dataclasses.replace(kw["cfg"], ensemble_n=1)
    lfps = T.LfpsTerrain(cache_dir=R.MIRROR, dep_probe=lambda: None,
                         canopy_layer_list=T.LfpsTerrain.DEFAULT_CANOPY_LAYERS)
    rows = []
    for pr in json.load(open(os.path.join(HERE, "pairs.json"))):
        A, B = pr["A"], pr["B"]
        abox = BBox(west=A["west"], south=A["south"], east=A["east"],
                    north=A["north"])
        bbox = BBox(west=B["west"], south=B["south"], east=B["east"],
                    north=B["north"])
        direct = lfps.tile(bbox, B["cell_m"])
        cut = RA.OnePiece(lfps, abox).cut(bbox, B["cell_m"])
        ctr = bbox.center()
        fires = []
        for brg in range(0, 360, 45):
            for dist in (2000.0, 6000.0):
                f = geo.destination(ctr, brg, dist)
                rc = geo.latlon_to_rc(direct.spec, f)
                if rc is None:
                    continue
                if (fuels.is_burnable(int(direct.fuel.get(*rc)))
                        and fuels.is_burnable(int(cut.fuel.get(*rc)))):
                    fires.append(f)
                    break
        for f in fires[:6]:
            now = time.time()
            wx = MockWeather("harness-redflag", base_wind_mps=12.5,
                             dir_deg=270.0, rh_pct=12.0, temp_c=35.0,
                             gust_factor=1.4, t0_s=now).forecast(
                f, cfg.horizon_h)
            got = {}
            for name, tile in (("direct", direct), ("cut", cut)):
                e = spread.ensemble_arrival(tile, [f], wx, now, cfg,
                                            incident_id="bias")
                got[name] = {h: sum(1 for v in e.p50.data
                                    if v <= now + h * 3600.0)
                             for h in (12, 24, 72)}
            rows.append({"B": [B["west"], B["south"], B["east"],
                               B["north"]], "fire": [f.lat, f.lon], **got})
            print(rows[-1], flush=True)
    summ = {}
    for h in (12, 24, 72):
        d = [r["cut"][h] - r["direct"][h] for r in rows]
        tot_d = sum(r["direct"][h] for r in rows)
        summ[f"{h}h"] = {"runs": len(d), "cut_less": sum(1 for x in d if x < 0),
                         "cut_more": sum(1 for x in d if x > 0),
                         "cells_direct": tot_d,
                         "cells_cut": sum(r["cut"][h] for r in rows),
                         "ratio": round(sum(r["cut"][h] for r in rows)
                                        / max(1, tot_d), 4)}
    out = {"runs": rows, "summary": summ}
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "region_bias_check.json"),
              "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(summ, indent=1), flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
