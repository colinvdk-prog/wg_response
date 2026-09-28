"""Fuel mix of each fire's harness domain and of its final burned area
(LANDFIRE FBFM40 classes; context for why physics runs fast here: the
deployed ROS floor, grass_ros_floor_frac 0.10 x U10 without
ros_floor_grass_only, lifts GR, GS, TU and TL cells alike)."""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _REPO)
sys.path.insert(0, _HERE)
import bvp_run as R  # noqa: E402
from tools import hindcast_lib as H  # noqa: E402

out = {}
for key in json.load(open(os.path.join(_HERE, "fires.json"))):
    fire = R.Fire(key)
    if not fire.has_tile():
        continue
    tile = fire.tile
    codes = [int(round(v)) for v in tile.fuel.data]
    last = H.polygon_interior_mask(tile.spec, fire.rings[-1])

    def mix(idx):
        c = {}
        n = 0
        for i in idx:
            cl = H.fuel_class(codes[i])
            c[cl] = c.get(cl, 0) + 1
            n += 1
        burn = n - c.get("NB", 0)
        return {k: round(v / burn, 3) for k, v in sorted(c.items())
                if k != "NB"} | {"NB_share_of_all": round(c.get("NB", 0) / n, 3)}
    out[key] = {"domain": mix(range(len(codes))), "final_burn": mix(last)}
    fb = out[key]["final_burn"]
    floored = sum(fb.get(k, 0) for k in ("GR", "GS", "TU", "TL"))
    print(f"{key:12s} final burn: " + " ".join(f"{k} {v:.2f}" for k, v in fb.items())
          + f" | floored classes {floored:.2f}")
json.dump(out, open(os.path.join(_HERE, "fuel_mix.json"), "w"), indent=1,
          sort_keys=True)
