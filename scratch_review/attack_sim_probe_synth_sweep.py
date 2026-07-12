"""Probe: does ANY scenario make served drop with buffer? (their synthetic
checkerboard geometry, dense sweep)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo
from wgresponse.simui import LOCATIONS, SimSession
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0}


def controlled_dir(base, key="boulder_co", size_km=20.0, cell_m=500.0,
                   hamlet_half=3):
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = os.path.join(base, "tiles")
    os.makedirs(d, exist_ok=True)
    save_ascii_tile(tile, os.path.join(d, f"{key}.wgt"))
    return d


def main():
    base = tempfile.mkdtemp(prefix="atk_sweep_")
    d = controlled_dir(base)
    loc = LOCATIONS["boulder_co"]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 8000.0)
    for n_cl, crews, ticks in ((16, 1, 12), (16, 1, 24)):
        row = {}
        for buf in (10.0, 20.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0):
            cfg = dict(FAST)
            cfg["egress_buffer_min"] = buf
            s = SimSession("boulder_co", n_cl, crews, seed="m1",
                           cfg_overrides=cfg, tile_dir=d)
            s.ignite(ign.lat, ign.lon, intensity="high")
            s.set_speed(2.0)
            for _ in range(ticks):
                st = s.tick()
                assert st["scoreboard"]["violations"] == 0
            row[buf] = st["scoreboard"]["served"]
        print(f"n={n_cl} crews={crews} ticks={ticks}: {row}")


if __name__ == "__main__":
    main()
