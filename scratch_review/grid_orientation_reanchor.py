"""Grid-orientation study: does the simulator's no-op re-anchor still
reproduce the continuous run with cfg.spread_any_angle? (2026-10-09; NOT
product code.) Runs scratch_review/city_weather_reanchor_probe.run (real
tiles, page physics, a no-op set_wind at T = 2 h 15 min, nominal burned
masks compared at +0.5 / +1 / +3 / +6 h) with the knob off and on, under the
generic (constant) and the city (hourly) weather. Writes
scratch_review/grid_orientation_reanchor.json (never the probe's own file).

Usage: py -3 scratch_review/grid_orientation_reanchor.py [key,...]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import city_weather_reanchor_probe as CW  # noqa: E402
from wgresponse import simui  # noqa: E402

_init = simui.SimSession.__init__
ARM = {"on": False}


def _patched(self, *a, **k):
    if ARM["on"]:
        k["cfg_overrides"] = dict(k.get("cfg_overrides") or {},
                                  spread_any_angle=True)
    return _init(self, *a, **k)


simui.SimSession.__init__ = _patched

keys = (sys.argv[1].split(",") if len(sys.argv) > 1 else
        ["park_city_windstorm", "malibu_santa_ana", "eagle_gypsum_westerly"])
out = []
for key in keys:
    for profile in ("generic", "city"):
        for on in (False, True):
            ARM["on"] = on
            r = CW.run(key, profile)
            r["any_angle"] = on
            out.append(r)
            print(f"{key:24s} {profile:7s} any_angle={on!s:5s} " + " | ".join(
                f"+{x['lead_h']}h area {x['area_ratio']:.3f} IoU {x['iou']:.3f}"
                f" shift {x['mean_shift_min']:+.1f} min" for x in r["rows"]),
                flush=True)
            with open(os.path.join(HERE, "grid_orientation_reanchor.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(out, fh, indent=1)
