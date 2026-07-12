"""EXP 2 — ETA stability across 15-min forecast cycles for a STATIC fire.

Members re-seed on round(t0_s) (spread.ensemble_arrival seeds with
("wgresponse.spread.ensemble", geom, round(t0_s), k)), so every 900 s cycle
draws brand-new wind/moisture scales. For a static fire under STATIONARY
weather, hours-to-impact should be constant cycle-to-cycle; any movement is
pure sampling jitter.

Scenario A: constant weather  -> isolates RNG re-seed jitter (ensemble_n=6).
Scenario B: one scripted wind shift at absolute hour 12 -> adds the
            start-relative hour-bucket jitter (also run nominal-only).
Clients at 8 / 26 / 55 km downwind (ETAs bracketing the 12/24/72 h ladder).
"""
from __future__ import annotations

import math

from _common import T0, cfg_default, east_of, flat_tile, fmt_h, hours

from wgresponse import geo, spread
from wgresponse.weather import MockWeather

CELL = 150.0
NROWS, NCOLS = 41, 420                    # ~6 x 63 km strip
NORTH, WEST = 34.53, -119.0
cfg = cfg_default(horizon_h=96.0)         # ensemble_n=6 default

tile = flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0)   # uniform GR2
spec = tile.spec
row_mid = NROWS // 2
ign = geo.rc_to_latlon(spec, row_mid, 6)
clients = {"C1@4km": east_of(ign, 4.0),
           "C2@8km": east_of(ign, 8.0),
           "C3@20km": east_of(ign, 20.0)}

CYCLES = 8


def run(wx, label, ensemble_n):
    c = cfg_default(horizon_h=96.0, ensemble_n=ensemble_n)
    print(f"\n=== {label} (ensemble_n={ensemble_n}) ===")
    hdr = "cycle  t0+min  " + "".join(f"| {n}: p50     p10    " for n in clients)
    print(hdr)
    series = {n: {"p50": [], "p10": []} for n in clients}
    for k in range(CYCLES):
        t0 = T0 + k * 900.0
        wxf = wx.forecast(ign, c.horizon_h + 2)
        ens = spread.ensemble_arrival(tile, [ign], wxf, t0, c)
        row = f"{k:3d}   {k*15:5d}  "
        for name, loc in clients.items():
            eta = spread.eta_at(ens, loc)
            p50 = hours(eta.eta_p50_s, t0)
            p10 = hours(eta.eta_p10_s, t0)
            series[name]["p50"].append(p50)
            series[name]["p10"].append(p10)
            row += f"| {fmt_h(p50)} {fmt_h(p10)}  "
        print(row)
    print("worst swing (max-min) minutes:")
    for name, d in series.items():
        for q in ("p50", "p10"):
            vals = [v for v in d[q] if not math.isinf(v)]
            if not vals:
                print(f"  {name:8s} {q}: (all unreached)")
                continue
            mean = sum(vals) / len(vals)
            swing = (max(vals) - min(vals)) * 60.0
            rel = (max(vals) - min(vals)) / mean * 100.0 if mean else 0.0
            print(f"  {name:8s} {q}: {swing:7.1f} min  ({rel:4.1f}% of mean {mean:6.2f} h)")
    return series


# A: stationary weather
wxA = MockWeather("exp2", base_wind_mps=3.0, dir_deg=270.0, rh_pct=25.0,
                  temp_c=30.0, t0_s=T0)
run(wxA, "A: constant 3 m/s tailwind", ensemble_n=6)

# B: wind shift 3 -> 5 m/s at absolute hour 12 (bucket boundaries move with t0)
wxB = MockWeather("exp2", base_wind_mps=3.0, dir_deg=270.0, rh_pct=25.0,
                  temp_c=30.0, t0_s=T0, shifts=[(12, 5.0, 270.0, 25.0)])
run(wxB, "B: shift to 5 m/s at abs hour 12 — nominal only (bucket jitter)", ensemble_n=1)
run(wxB, "B: shift to 5 m/s at abs hour 12 — full ensemble", ensemble_n=6)
