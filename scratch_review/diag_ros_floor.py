"""diag_ros_floor.py — PROTOTYPE the recommended sim-only calibration knob
(a Cruz-Alexander grass head-ROS FLOOR) via monkeypatch, and measure its
realized effect through the REAL engine spread path on the real tiles.

The floor prototyped here is:  for grass/grass-shrub cells,
    head_ros = max(rothermel_ros,  floor_frac * U10_cell)
with floor_frac = 0.10 for GR (Cruz & Alexander cured-grass rule) and 0.07 for
GS.  This is exactly the 3-line edit proposed for spread.cell_params, gated on a
cfg knob that defaults OFF (live product byte-identical) and that the SIMULATOR
turns on (same pattern as ember_threat_radius_m).

NOTHING in wgresponse/ is edited — the floor is injected by wrapping the public
rothermel seams, which spread.compute_arrival calls in the order
midflame_wind_mps(...) -> surface_ros_from_bed(...) per cell, so the wrapper can
recover the cell's U10 from the immediately-preceding midflame call.

Run: py -3 scratch_review/diag_ros_floor.py
"""
from __future__ import annotations

import math

from wgresponse import fuels, geo, rothermel, spread
from wgresponse.contracts import LatLon, ResponseConfig, RosResult
from wgresponse.simui import INTENSITY_WEATHER, MPH_TO_MPS, load_tile
from wgresponse.weather import MockWeather

MPS_TO_KMH = 3.6
T0 = 1_790_000_000.0
HERB, WOODY = 0.30, 0.60
HAZARD_DIR = {"paradise_ca": 45.0, "malibu_ca": 50.0, "boulder_co": 270.0}

# ---- the floor knob (prototype of ResponseConfig.grass_ros_floor_frac) --------
FLOOR_FRAC_GR = 0.0   # set to 0.10 to arm; GR grass
FLOOR_FRAC_GS = 0.0   # set to 0.07 to arm; grass-shrub
_STASH = {"u10": 0.0, "code": 0}

_orig_mid = rothermel.midflame_wind_mps
_orig_from_bed = rothermel.surface_ros_from_bed
_orig_ros_mps = rothermel.surface_ros_mps


def _floor_frac(code):
    if 101 <= code <= 109:
        return FLOOR_FRAC_GR
    if 121 <= code <= 124:
        return FLOOR_FRAC_GS
    return 0.0


def _apply(res, code):
    frac = _floor_frac(code)
    if frac <= 0.0 or _STASH["u10"] <= 0.0:
        return res
    floor = frac * _STASH["u10"]
    if res.ros_mps >= floor:
        return res
    return RosResult(ros_mps=floor, effective_wind_mps=res.effective_wind_mps,
                     flame_length_m=res.flame_length_m, intensity_kwm=res.intensity_kwm)


def _w_mid(wind10, fuel):
    _STASH["u10"] = wind10
    _STASH["code"] = fuel.code
    return _orig_mid(wind10, fuel)


def _w_from_bed(bed, mid, slope, *a, **k):
    return _apply(_orig_from_bed(bed, mid, slope, *a, **k), _STASH["code"])


def _w_ros_mps(fuel, *a, **k):
    _STASH["code"] = fuel.code
    return _apply(_orig_ros_mps(fuel, *a, **k), fuel.code)


def arm(gr=0.10, gs=0.07):
    global FLOOR_FRAC_GR, FLOOR_FRAC_GS
    FLOOR_FRAC_GR, FLOOR_FRAC_GS = gr, gs
    rothermel.midflame_wind_mps = _w_mid
    rothermel.surface_ros_from_bed = _w_from_bed
    rothermel.surface_ros_mps = _w_ros_mps
    spread.rothermel.midflame_wind_mps = _w_mid
    spread.rothermel.surface_ros_from_bed = _w_from_bed
    spread.rothermel.surface_ros_mps = _w_ros_mps


def disarm():
    global FLOOR_FRAC_GR, FLOOR_FRAC_GS
    FLOOR_FRAC_GR, FLOOR_FRAC_GS = 0.0, 0.0
    rothermel.midflame_wind_mps = _orig_mid
    rothermel.surface_ros_from_bed = _orig_from_bed
    rothermel.surface_ros_mps = _orig_ros_mps
    spread.rothermel.midflame_wind_mps = _orig_mid
    spread.rothermel.surface_ros_from_bed = _orig_from_bed
    spread.rothermel.surface_ros_mps = _orig_ros_mps


def make_wx(preset, wdir, horizon_h):
    s = INTENSITY_WEATHER[preset]
    return MockWeather(seed="floor", base_wind_mps=s["wind_mph"] * MPH_TO_MPS,
                       dir_deg=wdir, rh_pct=s["rh_pct"], temp_c=s["temp_c"],
                       gust_factor=1.3, t0_s=T0).forecast(LatLon(0, 0), horizon_h)


def cell_ll(spec, r, c):
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / geo.m_per_deg_lon(lat)
    return LatLon(lat, lon)


def head_ros(arr, origin):
    spec = arr.spec
    s = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            t = arr.get(r, c)
            if not math.isfinite(t) or t <= T0:
                continue
            ll = cell_ll(spec, r, c)
            dy = (ll.lat - origin.lat) * geo.M_PER_DEG_LAT
            dx = (ll.lon - origin.lon) * geo.m_per_deg_lon(origin.lat)
            d = math.hypot(dx, dy)
            if 1000.0 <= d <= 6000.0:
                s.append(d / (t - T0))
    return (max(s) * MPS_TO_KMH) if s else 0.0


def probe(key, prefer, n=8):
    tile, _ = load_tile(key)
    spec = tile.spec
    wdir = HAZARD_DIR[key]
    cands = []
    step = max(1, spec.nrows // 30)
    for r in range(0, spec.nrows, step):
        for c in range(0, spec.ncols, step):
            if int(round(tile.fuel.get(r, c))) in prefer:
                cands.append((r, c))
    if len(cands) > n:
        cands = cands[::max(1, len(cands) // n)][:n]
    out = {}
    for preset in ["high", "extreme"]:
        wx = make_wx(preset, wdir, 8.0)
        cfg = ResponseConfig(horizon_h=8.0, ensemble_n=1,
                             live_herb_frac=HERB, live_woody_frac=WOODY)
        rates = sorted(head_ros(spread.compute_arrival(tile, [cell_ll(spec, r, c)],
                       wx, T0, cfg), cell_ll(spec, r, c)) for (r, c) in cands)
        out[preset] = (rates[len(rates) // 2], max(rates))
    return out


print("=" * 78)
print("PROTOTYPE: Cruz-Alexander grass FLOOR (GR 0.10*U10, GS 0.07*U10)")
print("realized median / max head ROS km/h over 8 grass ignitions per tile")
print("=" * 78)

for key, prefer in [("paradise_ca", {102, 104, 107}),
                    ("boulder_co", {102}),
                    ("malibu_ca", {122, 145})]:
    disarm()
    off = probe(key, prefer)
    arm(0.10, 0.07)
    on = probe(key, prefer)
    disarm()
    print(f"\n{key}")
    for preset in ["high", "extreme"]:
        w = INTENSITY_WEATHER[preset]["wind_mph"] * MPH_TO_MPS
        anchor = 0.10 * w * MPS_TO_KMH
        omed, omax = off[preset]
        nmed, nmax = on[preset]
        print(f"   {preset:>7}: OFF med {omed:4.2f}/max {omax:4.2f}  ->  "
              f"ON med {nmed:4.2f}/max {nmax:4.2f}   "
              f"(anchor {anchor:4.2f}; ON med/anchor {nmed/anchor:0.2f}x)")

print("\nNOTE: the floor lifts grass cells to the empirical rule, but the realized")
print("Paradise head is still bounded by the timber-litter mosaic the head must")
print("cross (52% TL/TU) — a separate percolation limit, not fixed by a grass knob.")
print("\nDONE.")
