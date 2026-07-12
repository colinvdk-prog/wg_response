"""EXP 6 — the ensemble perturbs wind SPEED and MOISTURE only; wind DIRECTION
is never perturbed (spread.ensemble_arrival). For clients off the head axis,
direction error dominates ETA error (research digest §6.4 prescribed +/-30 deg).

Uniform GR2, 6 m/s FROM 270 (head toward 90 deg). Clients 6 km from ignition
at 0 / 25 / 60 deg off the head axis. Compare the ensemble's own p10-p90 band
against the nominal-member ETA swing when the TRUE direction differs by
+/-20 deg (well inside NWS 24-72 h direction error).
"""
from __future__ import annotations

import math

from _common import T0, cfg_default, flat_tile, fmt_h, hours

from wgresponse import geo, spread
from wgresponse.weather import MockWeather

CELL = 90.0
NROWS, NCOLS = 200, 130                   # ~18 x 11.7 km
NORTH, WEST = 34.60, -119.0
cfg = cfg_default(ensemble_n=6, horizon_h=96.0)
tile = flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0)
spec = tile.spec

ign = geo.rc_to_latlon(spec, NROWS // 2, 8)
clients = {
    "on-axis (bearing 90)":  geo.destination(ign, 90.0, 6000.0),
    "25 deg off (bearing 115)": geo.destination(ign, 115.0, 6000.0),
    "60 deg off (bearing 150)": geo.destination(ign, 150.0, 6000.0),
}


def wx_at(dir_deg: float):
    return MockWeather("exp6", base_wind_mps=6.0, dir_deg=dir_deg, rh_pct=25.0,
                       temp_c=30.0, t0_s=T0).forecast(ign, cfg.horizon_h + 2)

# ensemble band at nominal direction
ens = spread.ensemble_arrival(tile, [ign], wx_at(270.0), T0, cfg)
# nominal member at rotated directions
noms = {}
for d in (250.0, 270.0, 290.0):
    noms[d] = spread.compute_arrival(tile, [ign], wx_at(d), T0, cfg)

print("client                        ens p10    p50    p90   band(h) | "
      "nominal ETA @dir 250/270/290 (h)     rotation swing(h)")
for name, loc in clients.items():
    eta = spread.eta_at(ens, loc)
    p10, p50, p90 = (hours(eta.eta_p10_s, T0), hours(eta.eta_p50_s, T0),
                     hours(eta.eta_p90_s, T0))
    band = p90 - p10 if not (math.isinf(p90) or math.isinf(p10)) else math.inf
    rot = []
    for d in (250.0, 270.0, 290.0):
        v = geo.raster_sample(noms[d], loc)
        rot.append(math.inf if v is None or math.isinf(v) else (v - T0) / 3600.0)
    finite = [v for v in rot if not math.isinf(v)]
    swing = (max(finite) - min(finite)) if len(finite) > 1 else math.inf
    print(f"{name:28s} {fmt_h(p10)} {fmt_h(p50)} {fmt_h(p90)} {fmt_h(band)} | "
          f"{fmt_h(rot[0])} {fmt_h(rot[1])} {fmt_h(rot[2])}   {fmt_h(swing)}")
