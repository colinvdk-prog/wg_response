"""EXP 1 — FUEL_TABLE subset gap: burnable FBFM40 codes without a table row are
treated as NON-SPREADING walls by spread.compute_arrival (LATE-biased).

(a) Enumerate present vs missing burnable codes.
(b) Band experiment: GR2 corridor, 270 m band of a MISSING burnable code
    (GR3=103) between fire and client -> client ETA; controls: band=GR2 (no
    band), band=GR1 (present slow fuel ~ what a complete table would give),
    band=NB1 urban 91 (true non-burnable, same engine behavior as missing).
"""
from __future__ import annotations

import math

from _common import T0, cfg_default, east_of, flat_tile, fmt_h, hours, set_col_band

from wgresponse import geo, spread
from wgresponse.contracts import LatLon
from wgresponse.fuels import FUEL_TABLE, is_burnable
from wgresponse.weather import MockWeather

# ---- (a) enumerate ----------------------------------------------------------
FBFM40_ALL = {
    101: "GR1", 102: "GR2", 103: "GR3", 104: "GR4", 105: "GR5", 106: "GR6",
    107: "GR7", 108: "GR8", 109: "GR9",
    121: "GS1", 122: "GS2", 123: "GS3", 124: "GS4",
    141: "SH1", 142: "SH2", 143: "SH3", 144: "SH4", 145: "SH5", 146: "SH6",
    147: "SH7", 148: "SH8", 149: "SH9",
    161: "TU1", 162: "TU2", 163: "TU3", 164: "TU4", 165: "TU5",
    181: "TL1", 182: "TL2", 183: "TL3", 184: "TL4", 185: "TL5", 186: "TL6",
    187: "TL7", 188: "TL8", 189: "TL9",
    201: "SB1", 202: "SB2", 203: "SB3", 204: "SB4",
}
present = sorted(c for c in FBFM40_ALL if c in FUEL_TABLE)
missing = sorted(c for c in FBFM40_ALL if c not in FUEL_TABLE)
print("burnable FBFM40 codes:", len(FBFM40_ALL))
print(f"present in FUEL_TABLE ({len(present)}):",
      ", ".join(f"{c}/{FBFM40_ALL[c]}" for c in present))
print(f"MISSING ({len(missing)}):",
      ", ".join(f"{c}/{FBFM40_ALL[c]}" for c in missing))
for c in missing:
    assert is_burnable(c), c   # all classified burnable yet have no params

# ---- (b) band experiment ----------------------------------------------------
# 20 km x 4 km strip at 90 m; fire seeded 1 km from the west edge; client 15 km
# downwind (east). Wind 5 m/s FROM 270 (pushes east). Weather constant.
CELL = 90.0
NROWS, NCOLS = 45, 245                     # ~4 x 22 km
NORTH, WEST = 34.52, -119.0
wx = MockWeather("exp1", base_wind_mps=5.0, dir_deg=270.0, rh_pct=25.0,
                 temp_c=30.0, t0_s=T0)
cfg = cfg_default(ensemble_n=1, horizon_h=96.0)

spec_probe = flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0).spec
row_mid = NROWS // 2
ign = geo.rc_to_latlon(spec_probe, row_mid, 11)          # ~1 km in
client = east_of(ign, 15.0)

BAND_LO, BAND_HI = 85, 88                                # 3 cells = 270 m band, ~6.7 km in

cases = [
    ("no band (uniform GR2)",           None),
    ("band = GR3 103 (burnable, MISSING row)", 103.0),
    ("band = NB1  91 (true non-burnable)",     91.0),
    ("band = GR1 101 (present, sparse grass)", 101.0),
]
print("\nband experiment: fire -> [270 m band @6.7 km] -> client @15 km, "
      "GR2, 5 m/s tailwind")
forecast = wx.forecast(ign, cfg.horizon_h + 2)
for label, code in cases:
    tile = flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0)
    if code is not None:
        set_col_band(tile, BAND_LO, BAND_HI, code)
    arr = spread.compute_arrival(tile, [ign], forecast, T0, cfg)
    eta = geo.raster_sample(arr, client)
    eta = math.inf if eta is None else eta
    # how much of the grid ever burns
    burned = sum(1 for v in arr.data if not math.isinf(v))
    print(f"  {label:44s} client ETA = {fmt_h(hours(eta, T0))} h   "
          f"(cells reached: {burned}/{len(arr.data)})")
