"""Option 2 probe: compare two candidate orders for the fine-fuel start of the
seven timber-start scenarios (NOT product code)."""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from wgresponse import geo, server, simui  # noqa: E402
from ignition_fuel_probe import cls, frac_fine  # noqa: E402

SEVEN = ["aspen_southwesterly", "basalt_carbondale_southwesterly", "beaver_creek_westerly",
         "eagle_vail_corridor_westerly", "glenwood_springs_westerly", "park_city_windstorm",
         "vail_westerly"]
DBS = [0] + [s * d for d in range(5, 31, 5) for s in (1, -1)]
SHRINKS = [round(1.0 - 0.05 * i, 2) for i in range(15)]


def pick(tile, center, sc, order, thr):
    spec = tile.spec
    cands = []
    for db in DBS:
        for s in SHRINKS:
            p = geo.destination(center, sc["ign_bearing"] + db, sc["ign_dist_m"] * s)
            rc = geo.latlon_to_rc(spec, p)
            if rc is None:
                continue
            code = int(round(tile.fuel.get(*rc)))
            # displacement from the canonical point (law of cosines on the plane)
            a = math.radians(db)
            disp = sc["ign_dist_m"] * math.sqrt(1 + s * s - 2 * s * math.cos(a))
            cands.append((db, s, disp, code, rc, p))
    if order == "displacement":
        cands.sort(key=lambda t: (round(t[2], 3), abs(t[0]), -t[1], t[0]))
    for level_thr in (thr, 0.0):
        for db, s, disp, code, rc, p in cands:
            if cls(code) in ("GR", "GS", "SH"):
                f = frac_fine(tile, rc, 250)[0]
                if f >= level_thr:
                    return db, s, disp, cls(code), code, f, level_thr
    return None


for key in SEVEN:
    sc = server.SCENARIOS[key]
    tile, _ = simui.load_tile(sc["location"])
    center = simui.LOCATIONS[sc["location"]].center
    for order in ("bearing", "displacement"):
        for thr in (0.5, 0.3):
            r = pick(tile, center, sc, order, thr)
            if r is None:
                print(f"{key:34s} {order:12s} thr {thr}: NONE")
                continue
            db, s, disp, c, code, f, lev = r
            print(f"{key:34s} {order:12s} thr {thr}: db {db:+3d} x{s:.2f} moved {disp:5.0f} m "
                  f"{c}({code}) fine250 {f:.2f} level {'patch' if lev > 0 else 'cell'}")
