"""diag_ros_tile.py — realized head-fire ROS + acres/hour on the REAL sim tiles.

Runs the SAME nominal spread path the simulator demonstrates
(spread.compute_arrival, wind_scale=1/moisture_scale=1/dir_offset=0 = the
'ground-truth' member the fire actually follows) on the real cached LFPS
tiles at each intensity preset, and measures:
  - realized head-fire ROS (km/h)  [max radial speed over cells 2-7 km out]
  - km reached at 3 h / 6 h along the head axis
  - acres/hour over the first 90 min and first 3 h  (Camp anchor = 6,600 ac/h)

Also validates against a flat UNIFORM GR2 tile (isolates physics from the
fuel mosaic + terrain) so the mixed-tile slowdown can be attributed.

Product code untouched. Run: py -3 scratch_review/diag_ros_tile.py
"""
from __future__ import annotations

import math

from wgresponse import fuels, geo, spread
from wgresponse.contracts import GridSpec, LatLon, Raster, ResponseConfig, TerrainTile
from wgresponse.simui import INTENSITY_WEATHER, MPH_TO_MPS, load_tile
from wgresponse.weather import MockWeather

MPS_TO_KMH = 3.6
ACRE_M2 = 4046.8564224
T0 = 1_790_000_000.0
PRESETS = ["low", "moderate", "high", "extreme"]
HAZARD_DIR = {"paradise_ca": 45.0, "malibu_ca": 50.0, "boulder_co": 270.0}


def make_wx(preset, wind_dir_deg, horizon_h):
    spec = INTENSITY_WEATHER[preset]
    mock = MockWeather(seed="diag", base_wind_mps=spec["wind_mph"] * MPH_TO_MPS,
                       dir_deg=wind_dir_deg, rh_pct=spec["rh_pct"],
                       temp_c=spec["temp_c"], gust_factor=1.3, t0_s=T0)
    return mock.forecast(LatLon(0.0, 0.0), horizon_h)


def uniform_tile(code, nrows=200, ncols=200, cell_m=90.0,
                 center=LatLon(39.75, -121.6)):
    """Flat uniform tile of one fuel code (slope 0, aspect 0)."""
    north = center.lat + (nrows / 2.0) * cell_m / geo.M_PER_DEG_LAT
    west = center.lon - (ncols / 2.0) * cell_m / geo.m_per_deg_lon(center.lat)
    spec = GridSpec(north_lat=north, west_lon=west, cell_m=cell_m,
                    nrows=nrows, ncols=ncols)
    n = nrows * ncols
    return TerrainTile(spec=spec,
                       fuel=Raster(spec, [float(code)] * n),
                       slope_deg=Raster(spec, [0.0] * n),
                       aspect_deg=Raster(spec, [0.0] * n),
                       elev_m=Raster(spec, [500.0] * n),
                       source="uniform")


def _cell_latlon(spec, r, c):
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / geo.m_per_deg_lon(lat)
    return LatLon(lat, lon)


def center_burnable(tile, prefer_codes=None):
    """LatLon of a cell near the tile center. If prefer_codes is given, pick the
    nearest cell whose code is in that set (the dominant fast carrier); else the
    nearest burnable cell."""
    spec = tile.spec
    r0, c0 = spec.nrows // 2, spec.ncols // 2
    pref_hit = None
    burn_hit = None
    for rad in range(0, max(spec.nrows, spec.ncols)):
        for r in range(max(0, r0 - rad), min(spec.nrows, r0 + rad + 1)):
            for c in range(max(0, c0 - rad), min(spec.ncols, c0 + rad + 1)):
                code = int(round(tile.fuel.get(r, c)))
                if prefer_codes and code in prefer_codes and pref_hit is None:
                    pref_hit = (_cell_latlon(spec, r, c), code)
                if fuels.is_burnable(code) and burn_hit is None:
                    burn_hit = (_cell_latlon(spec, r, c), code)
        if pref_hit:
            return pref_hit
        if burn_hit and not prefer_codes:
            return burn_hit
    return burn_hit or (LatLon(spec.north_lat, spec.west_lon), None)


def fuel_class_mix(tile):
    """Fraction of burnable cells that are 'fast' (GR/GS) vs 'slow' (TL/TU/SH-litter)."""
    fast = slow = other = 0
    for v in tile.fuel.data:
        code = int(round(v))
        if not fuels.is_burnable(code):
            continue
        if 101 <= code <= 124:       # GR + GS
            fast += 1
        elif 181 <= code <= 189 or 161 <= code <= 165:   # TL + TU
            slow += 1
        else:
            other += 1               # SH, SB
    tot = fast + slow + other
    return fast, slow, other, tot


def measure(arr, origin, cell_m, wind_dir_deg):
    """From an arrival raster: head ROS (km/h), km@3h, km@6h, and
    burned-area time series (for acres/hour). Head direction = wind blows TO."""
    spec = arr.spec
    head_brg = (wind_dir_deg + 180.0) % 360.0
    hb = math.radians(head_brg)
    hx, hy = math.sin(hb), math.cos(hb)   # unit vector toward head (compass)
    # collect (elapsed_s, dist_m, head_proj_m) for reached cells
    reached = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            t = arr.get(r, c)
            if not math.isfinite(t):
                continue
            elapsed = t - T0
            if elapsed <= 0:
                continue
            lat = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
            lon = spec.west_lon + (c + 0.5) * spec.cell_m / geo.m_per_deg_lon(lat)
            # local ENU-ish offset in meters from origin
            dy = (lat - origin.lat) * geo.M_PER_DEG_LAT
            dx = (lon - origin.lon) * geo.m_per_deg_lon(origin.lat)
            dist = math.hypot(dx, dy)
            proj = dx * hx + dy * hy      # meters along head axis
            reached.append((elapsed, dist, proj))
    # head ROS = max radial speed over cells 1.5-6 km out (constant-weather => flat).
    # Fallback for slow mosaics that never reach 1.5 km: max radial speed of the
    # farthest-reached quartile of cells (still the head axis).
    ros_samples = [d / e for (e, d, p) in reached if 1500.0 <= d <= 6000.0]
    if ros_samples:
        head_ros_kmh = max(ros_samples) * MPS_TO_KMH
    elif reached:
        far = sorted(reached, key=lambda x: x[1])[-max(1, len(reached) // 20):]
        head_ros_kmh = max(d / e for (e, d, p) in far) * MPS_TO_KMH
    else:
        head_ros_kmh = float("nan")
    # km reached along head axis by 3h / 6h
    def head_km(hours):
        cutoff = hours * 3600.0
        projs = [p for (e, d, p) in reached if e <= cutoff and p > 0]
        return (max(projs) / 1000.0) if projs else 0.0
    # acres/hour over first 90 min / 3h
    def acres_per_h(hours):
        cutoff = hours * 3600.0
        cells = sum(1 for (e, d, p) in reached if e <= cutoff)
        area_ac = cells * cell_m * cell_m / ACRE_M2
        return area_ac / hours
    return (head_ros_kmh, head_km(3.0), head_km(6.0),
            acres_per_h(1.5), acres_per_h(3.0), len(reached))


def run_tile(name, tile, prefer_codes=None, horizon_h=8.0, wdir=None):
    origin, ig_code = center_burnable(tile, prefer_codes=prefer_codes)
    if wdir is None:
        wdir = HAZARD_DIR.get(name.split()[0], 45.0)
    cfg = ResponseConfig(horizon_h=horizon_h, ensemble_n=1,
                         live_herb_frac=0.30, live_woody_frac=0.60)
    mix = ""
    if not name.startswith("UNIFORM"):
        fast, slow, other, tot = fuel_class_mix(tile)
        mix = (f"\n   burnable-fuel mix: GR/GS(fast) {100*fast/tot:.0f}%  "
               f"TL/TU(slow) {100*slow/tot:.0f}%  SH/SB {100*other/tot:.0f}%")
    print(f"\n### {name}  (ignition fuel FBFM40 {ig_code}, wind FROM {wdir:.0f} deg, "
          f"tile {tile.spec.nrows}x{tile.spec.ncols}@{tile.spec.cell_m:.0f}m){mix}")
    print(f"{'preset':>9} | {'U10 m/s':>7} | {'headROS':>8} | {'km@3h':>6} | "
          f"{'km@6h':>6} | {'ac/h 90m':>9} | {'ac/h 3h':>8}")
    for preset in PRESETS:
        w = INTENSITY_WEATHER[preset]["wind_mph"] * MPH_TO_MPS
        wx = make_wx(preset, wdir, horizon_h)
        arr = spread.compute_arrival(tile, [origin], wx, T0, cfg)
        hros, km3, km6, ac15, ac3, nreached = measure(
            arr, origin, tile.spec.cell_m, wdir)
        print(f"{preset:>9} | {w:>7.2f} | {hros:>7.2f}  | {km3:>6.2f} | "
              f"{km6:>6.2f} | {ac15:>9.0f} | {ac3:>8.0f}")


print("=" * 84)
print("REALIZED HEAD-FIRE SPREAD  (nominal member = the fire the simulator draws)")
print("=" * 84)
print("headROS km/h = max radial speed 2-7 km out (constant weather => time-invariant)")
print("Camp Fire anchor: ~6,600 ac/h over the first 90 min (Paradise, Nov 2018)")

# 1) flat uniform GR2 (isolates physics)
run_tile("UNIFORM-GR2 (flat, isolates physics)", uniform_tile(102))

# 2) real tiles — ignite on the dominant grass carrier (GR2) so the fire has a
#    fast fuel to run in; the mixed mosaic then throttles it (the founder's case).
IGNITE_PREF = {
    "paradise_ca": {102, 104, 122},   # GR2/GR4/GS2 grass carriers
    "malibu_ca": {122, 121, 145},     # GS2/GS1/SH5
    "boulder_co": {102, 101},         # GR2/GR1
}
for key in ["paradise_ca", "malibu_ca", "boulder_co"]:
    tile, _synth = load_tile(key)
    run_tile(key, tile, prefer_codes=IGNITE_PREF[key], horizon_h=8.0)

print("\nDONE.")
