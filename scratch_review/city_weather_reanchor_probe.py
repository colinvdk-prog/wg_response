"""City weather: does a re-anchor that changes nothing physical still
reproduce the continuous run? (2026-10-09; docs/review/
city_weather_2026-10-09.md). NOT product code.

Under the generic presets (constant weather) a no-op re-anchor — the same
wind re-set — reproduces the continuous run cell for cell
(tests/test_sim_reanchor_progress.py). Under city weather the forecast
changes hour to hour; the re-anchor history now carries the closed field's
HOURLY weather (simui._field_wx_spans), but spread's hour buckets restart at
the anchor, so a re-anchor between two whole hours prices the next partial
hour from a different sample. This probe measures the residual: for each
scenario (real tile, page physics, grass-only floor) it runs the fire
continuously and with a no-op set_wind at T = 2 h 15 min, then compares the
nominal burned masks at +0.5 / +1 / +3 / +6 h after the switch.

Usage: py -3 scratch_review/city_weather_reanchor_probe.py [key,...]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, server, simui  # noqa: E402

SWITCH_TICKS = 9          # 2 h 15 min
LEADS_H = (0.5, 1.0, 3.0, 6.0)


def build(key: str, profile: str):
    sc = server.SCENARIOS[key]
    cfg = {"ros_floor_grass_only": True, "spotting_enabled": True,
           "grass_ros_floor_frac": 0.10, "sim_weather_profile": profile}
    s = simui.SimSession(sc["location"], 1, 1, seed=f"scenario-{key}",
                         cfg_overrides=cfg, roads_module=None)
    center = simui.LOCATIONS[sc["location"]].center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, sc["ign_bearing"] + db,
                                sc["ign_dist_m"] * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity=sc["intensity"],
                         wind_dir_deg=sc["ign_bearing"])
                return s
            except ValueError:
                continue
    raise SystemExit("no ignition")


def run(key: str, profile: str) -> dict:
    ref = build(key, profile)
    alt = build(key, profile)
    for _ in range(SWITCH_TICKS):
        ref.tick()
        alt.tick()
    alt.set_wind(alt._wind_dir_deg)           # a no-op world change
    t_sw = alt._now_s
    rows = []
    done = 0.0
    for lead in LEADS_H:
        n = int(round((lead - done) * 4))
        for _ in range(n):
            ref.tick()
            alt.tick()
        done = lead
        now = ref._now_s
        a = ref._fires["sim-f1"].ens.members[0].data
        b = alt._fires["sim-f1"].ens.members[0].data
        ba = {i for i, v in enumerate(a) if v <= now}
        bb = {i for i, v in enumerate(b) if v <= now}
        inter = len(ba & bb)
        union = len(ba | bb) or 1
        diff = [b[i] - a[i] for i in (ba | bb)
                if a[i] < 1e18 and b[i] < 1e18]
        rows.append({"lead_h": lead, "cells_ref": len(ba),
                     "cells_reanchored": len(bb),
                     "area_ratio": round(len(bb) / max(1, len(ba)), 4),
                     "iou": round(inter / union, 4),
                     "mean_shift_min": round(sum(diff) / len(diff) / 60.0, 2)
                     if diff else 0.0})
    return {"key": key, "profile": profile, "switch_s": t_sw, "rows": rows}


if __name__ == "__main__":
    keys = (sys.argv[1].split(",") if len(sys.argv) > 1 else
            ["park_city_windstorm", "malibu_santa_ana",
             "eagle_gypsum_westerly"])
    out = []
    for key in keys:
        for profile in ("generic", "city"):
            r = run(key, profile)
            out.append(r)
            print(f"{key:28s} {profile:7s} " + " | ".join(
                f"+{x['lead_h']}h area {x['area_ratio']:.3f} IoU "
                f"{x['iou']:.3f} shift {x['mean_shift_min']:+.1f} min"
                for x in r["rows"]), flush=True)
            with open(os.path.join(HERE, "city_weather_reanchor_probe.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=1)
