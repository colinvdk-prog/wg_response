"""Crown-fire study: the 10-m sustained wind each one-click scenario's city
weather blows over the first 6 h after ignition (min / mean / max, mi/h), the
denominator of the share-of-the-wind columns in docs/review/crown_fire_model_
2026-10-12.md (NOT product code; the simulator runs no gust blend, so the
engine spreads on this sustained wind).

Usage: py -3 scratch_review/crown_fire_city_wind.py
Output: scratch_review/crown_fire_city_wind.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import ember_spotting_probe as E  # noqa: E402

SCENS = ["park_city_windstorm", "vail_westerly", "beaver_creek_westerly",
         "glenwood_springs_westerly", "basalt_carbondale_southwesterly",
         "aspen_southwesterly", "eagle_vail_corridor_westerly",
         "eagle_gypsum_westerly", "boulder_chinook",
         "rancho_santa_fe_santa_ana", "malibu_santa_ana",
         "paradise_ne_wind", "santa_rosa_diablo"]


def main():
    out = {}
    for sc in SCENS:
        s, fid = E.build(sc, {"spot_fire_size": True})
        t0 = s._fires[fid].t_ignite_s
        w = [x.wind_speed_mps * 2.2369 for x in s._wx.samples
             if t0 - 1800.0 <= x.t_s <= t0 + 6 * 3600.0]
        out[sc] = {"min": round(min(w), 1), "mean": round(sum(w) / len(w), 1),
                   "max": round(max(w), 1), "n": len(w)}
        print(sc, out[sc], flush=True)
    with open(os.path.join(HERE, "crown_fire_city_wind.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
