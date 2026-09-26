"""scratch_review/fire_shading_shots.py — before/after frames for the
"shade the fire" display change (founder decision 2026-09-26).

Wraps scratch_review/roads_inspection_shots.py (headless Chrome over the
DevTools protocol, the REAL page and the REAL /api/sim session API) with its
own BASE / OUT_DIR / debugging port / profile directory, so it can run next
to other agents' servers and Chrome sessions without colliding.

Frames (docs/img/fire_shading/<name>_<tag>.jpg, 1600x1000 JPEG):
  park_city_windstorm_T1h45  the one-click EXTREME park_city_windstorm
                             scenario stepped 7 ticks (T+1h45) — the frame
                             where the old fill showed ~0.6 km2 against
                             ~12 km2 actually burned;
  eagle_vail_high_T4h        a HIGH fire 8 km WSW of the Eagle -> Vail tile
                             centre (wind FROM 240, the valley's hazard wind)
                             stepped 16 ticks (T+4h).

The map view and the caption numbers come from an in-process replica of the
page's session (same seed, same page physics: spotting + grass floor 0.10,
same ignition): the fire field depends only on tile, weather seed, physics
cfg and ignition, so the replica's nominal / P50 areas are the server's.

Usage (a server from the tree under test must be listening on --base):
    py -3 -m wgresponse.cli simui --host 127.0.0.1 --port 8893   (separately)
    py -3 scratch_review/fire_shading_shots.py --tag before
    py -3 scratch_review/fire_shading_shots.py --tag after
    py -3 scratch_review/fire_shading_shots.py --views   (print views only)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)
sys.path.insert(0, _HERE)

import roads_inspection_shots as ris  # noqa: E402
from wgresponse import geo, simui  # noqa: E402

HOUR = 3600.0
PAGE_CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}

SHOTS = [
    {"name": "park_city_windstorm_T1h45", "key": "park_city_ut",
     "zone": "standard", "tick": 7, "scenario": "park_city_windstorm",
     "replica": {"seed": "scenario-park_city_windstorm", "clients": 40,
                 "crews": 2, "crews_1h": 1, "intensity": "extreme",
                 "bearing": 225.0, "dist_m": 7000.0}},
    {"name": "eagle_vail_high_T4h", "key": "eagle_vail_co", "zone": "standard",
     "tick": 16,
     "replica": {"seed": "fire-shading-eagle", "clients": 40, "crews": 6,
                 "crews_1h": 0, "intensity": "high", "bearing": 240.0,
                 "ignition": [39.598999, -106.700863]}},
    # close-ups (cell-level check of the raster, outline and fronts)
    {"name": "park_city_windstorm_T1h45_detail", "key": "park_city_ut",
     "zone": "standard", "tick": 7, "scenario": "park_city_windstorm",
     "detail": True, "zoom_delta": 1,
     "replica": {"seed": "scenario-park_city_windstorm", "clients": 40,
                 "crews": 2, "crews_1h": 1, "intensity": "extreme",
                 "bearing": 225.0, "dist_m": 7000.0}},
    {"name": "eagle_vail_high_T4h_detail", "key": "eagle_vail_co",
     "zone": "standard", "tick": 16, "detail": True, "zoom_delta": 1,
     "replica": {"seed": "fire-shading-eagle", "clients": 40, "crews": 6,
                 "crews_1h": 0, "intensity": "high", "bearing": 240.0,
                 "ignition": [39.598999, -106.700863]}},
]


def replica(spec: dict):
    """(session, fid) replicating the page session's fire (roads off: the
    fire field does not depend on them)."""
    r = spec["replica"]
    s = simui.SimSession(spec["key"], r["clients"], r["crews"], seed=r["seed"],
                         cfg_overrides=dict(PAGE_CFG),
                         n_crews_1h=r["crews_1h"], roads_module=None)
    if "ignition" in r:
        lat, lon = r["ignition"]
        return s, s.ignite(lat, lon, intensity=r["intensity"],
                           wind_dir_deg=r["bearing"])
    center = simui.LOCATIONS[spec["key"]].center
    for db in (0, 10, -10, 20, -20, 30, -30):          # server preset loop
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, r["bearing"] + db, r["dist_m"] * shrink)
            try:
                return s, s.ignite(p.lat, p.lon, intensity=r["intensity"],
                                   wind_dir_deg=r["bearing"])
            except ValueError:
                continue
    raise RuntimeError(f"{spec['name']}: no burnable ignition cell")


def view_and_numbers(spec: dict) -> dict:
    s, fid = replica(spec)
    s.tick()
    f = s._fires[fid]
    ens = f.ens
    spec_g = s._tile.spec
    t = f.t_ignite_s + spec["tick"] * 900.0
    cell_km2 = (spec_g.cell_m / 1000.0) ** 2

    def pts(raster, thr):
        out = []
        for i, v in enumerate(raster.data):
            if v <= thr:
                p = geo.rc_to_latlon(spec_g, i // spec_g.ncols, i % spec_g.ncols)
                out.append([p.lat, p.lon])
        return out
    nom = pts(ens.members[0], t)
    p50 = pts(ens.p50, t)
    p50h5 = pts(ens.p50, t + 5 * HOUR)
    view = ris.view_for(nom + p50h5)
    if spec.get("detail"):
        # a close-up on the head half of the actual fire (cell-level check)
        heads = sorted(nom, key=lambda p: -p[0])[: max(1, len(nom) // 2)]
        lat, lon, zoom = ris.view_for(heads)
        view = [lat, lon, zoom + int(spec.get("zoom_delta", 0))]
    return {"view": view, "ignition": [round(f.origin.lat, 6),
                                       round(f.origin.lon, 6)],
            "nominal_km2": round(len(nom) * cell_km2, 2),
            "p50_km2": round(len(p50) * cell_km2, 2),
            "t_h": spec["tick"] * 0.25}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="after", help="before | after (file suffix)")
    ap.add_argument("--base", default="http://127.0.0.1:8893")
    ap.add_argument("--port", type=int, default=9447, help="Chrome debug port")
    ap.add_argument("--views", action="store_true", help="print views, no shots")
    ap.add_argument("--only", action="append")
    args = ap.parse_args(argv)
    ris.BASE = args.base
    ris.PORT = args.port
    ris.OUT_DIR = os.path.join(_REPO, "docs", "img", "fire_shading")
    os.makedirs(ris.OUT_DIR, exist_ok=True)
    plans = []
    for spec in SHOTS:
        if args.only and spec["name"] not in args.only:
            continue
        info = view_and_numbers(spec)
        print(f"[{spec['name']}] {json.dumps(info)}", flush=True)
        plans.append((spec, info))
    if args.views:
        return 0
    prof = os.path.join(os.environ.get("TEMP", "."), "wg_fire_shading_cdp_profile")
    proc, cdp = ris.launch(prof)
    try:
        for spec, info in plans:
            shot = {
                "name": f"{spec['name']}_{args.tag}", "key": spec["key"],
                "zone": spec["zone"], "tick": spec["tick"], "view": info["view"],
                "caption": (f"{args.tag.upper()} · T+{info['t_h']:g} h · "
                            f"simulated fire (nominal member) "
                            f"{info['nominal_km2']:.1f} km² · pooled ensemble "
                            f"P50 <= now {info['p50_km2']:.1f} km²"),
            }
            runs = {}
            if spec.get("scenario"):
                shot["scenario"] = spec["scenario"]
            else:
                r = spec["replica"]
                runs[(spec["key"], spec["zone"])] = {
                    "_params": {"clients": r["clients"], "crews": r["crews"],
                                "crews_1h": r["crews_1h"], "crews_3h": 0,
                                "seed": r["seed"], "intensity": r["intensity"]},
                    "_wind": r["bearing"],
                    "ignition": {"lat": info["ignition"][0],
                                 "lon": info["ignition"][1]},
                    "moves": [],
                }
            print(f"[{shot['name']}] tick {shot['tick']} view {shot['view']}",
                  flush=True)
            # close-ups collapse the legend window (the page persists that
            # per origin in localStorage, read at load)
            cdp.call("Page.navigate", url=f"{ris.BASE}/simulator")
            time.sleep(1.0)
            ris.wait_ready(cdp)
            cdp.js("localStorage.setItem('wg_legend_collapsed', "
                   f"{json.dumps('1' if spec.get('detail') else '0')}); true")
            ris.run_shot(cdp, shot, runs)
    finally:
        try:
            cdp.call("Browser.close")
        except Exception:
            pass
        time.sleep(0.5)
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
