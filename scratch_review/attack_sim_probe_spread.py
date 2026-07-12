"""attack_sim_probe_spread.py — verifier diagnostic: does the real engine
spread fire on the real cached tiles at all? Bypasses SimSession: direct
spread.ensemble_arrival on data/tiles/boulder_co.wgt from a deep-grass cell.
Run from repo root: py -3 scratch_review/attack_sim_probe_spread.py
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, spread
from wgresponse.contracts import ResponseConfig
from wgresponse.fuels import is_burnable
from wgresponse.simui import (INTENSITY_WEATHER, LOCATIONS, MPH_TO_MPS,
                              GUST_FACTOR, T0_S, load_tile)
from wgresponse.weather import MockWeather


def grass_core_cell(tile, code_want=None, win=3):
    """(r, c) whose full (2*win+1)^2 window is burnable — deep-fuel cell."""
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    codes = [int(round(v)) for v in tile.fuel.data]
    burn = [is_burnable(v) for v in codes]
    for r in range(win, nr - win):
        for c in range(win, nc - win):
            if code_want is not None and codes[r * nc + c] != code_want:
                continue
            ok = True
            for rr in range(r - win, r + win + 1):
                base = rr * nc
                for cc in range(c - win, c + win + 1):
                    if not burn[base + cc]:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return r, c
    return None


def main():
    key = "boulder_co"
    tile, synth = load_tile(key)
    print(f"tile source={tile.source} synth={synth} "
          f"spec={tile.spec.nrows}x{tile.spec.ncols}@{tile.spec.cell_m}m")
    rc = grass_core_cell(tile, code_want=102)   # GR2 heartland
    print("deep GR2 cell:", rc)
    p = geo.rc_to_latlon(tile.spec, *rc)
    cfg = ResponseConfig()
    wxspec = INTENSITY_WEATHER["extreme"]
    mock = MockWeather(seed="probe|wx",
                       base_wind_mps=wxspec["wind_mph"] * MPH_TO_MPS,
                       dir_deg=270.0, rh_pct=wxspec["rh_pct"],
                       temp_c=wxspec["temp_c"], gust_factor=GUST_FACTOR,
                       t0_s=T0_S)
    wx = mock.forecast(LOCATIONS[key].center, cfg.horizon_h)
    print("wx sample0:", wx.samples[0])
    t0 = time.perf_counter()
    ens = spread.ensemble_arrival(tile, [p], wx, T0_S, cfg, incident_id="probe")
    t1 = time.perf_counter()
    nom = ens.members[0].data
    for lead_h in (0.25, 1, 2, 4, 6, 12, 24):
        thr = T0_S + lead_h * 3600.0
        n = sum(1 for v in nom if v <= thr)
        print(f"nominal burned cells at +{lead_h}h: {n}")
    finite = sum(1 for v in nom if not math.isinf(v))
    print(f"cells ever reached (nominal, 96h horizon): {finite} "
          f"of {len(nom)}; ensemble build {t1 - t0:.2f}s")


if __name__ == "__main__":
    main()
