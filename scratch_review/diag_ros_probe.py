"""diag_ros_probe.py — (a) multi-ignition robustness on real tiles;
(b) WAF/midflame sensitivity to pin the root cause of the grass slow bias.

Product code untouched. Run: py -3 scratch_review/diag_ros_probe.py
"""
from __future__ import annotations

import math

from wgresponse import fuels, geo, rothermel, spread
from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.simui import INTENSITY_WEATHER, MPH_TO_MPS, load_tile
from wgresponse.weather import MockWeather

MPS_TO_KMH = 3.6
T0 = 1_790_000_000.0
HERB, WOODY = 0.30, 0.60
HAZARD_DIR = {"paradise_ca": 45.0, "malibu_ca": 50.0, "boulder_co": 270.0}


def make_wx(preset, wdir, horizon_h):
    s = INTENSITY_WEATHER[preset]
    return MockWeather(seed="probe", base_wind_mps=s["wind_mph"] * MPH_TO_MPS,
                       dir_deg=wdir, rh_pct=s["rh_pct"], temp_c=s["temp_c"],
                       gust_factor=1.3, t0_s=T0).forecast(LatLon(0, 0), horizon_h)


def cell_ll(spec, r, c):
    lat = spec.north_lat - (r + 0.5) * spec.cell_m / geo.M_PER_DEG_LAT
    lon = spec.west_lon + (c + 0.5) * spec.cell_m / geo.m_per_deg_lon(lat)
    return LatLon(lat, lon)


def head_ros_from_arrival(arr, origin, wdir):
    spec = arr.spec
    samples = []
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
                samples.append(d / (t - T0))
    return (max(samples) * MPS_TO_KMH) if samples else 0.0


def probe_tile(key, prefer, n_ignite=8):
    tile, _ = load_tile(key)
    spec = tile.spec
    wdir = HAZARD_DIR[key]
    # gather candidate grass ignition cells across the tile (not just center)
    cands = []
    step = max(1, spec.nrows // 30)
    for r in range(0, spec.nrows, step):
        for c in range(0, spec.ncols, step):
            if int(round(tile.fuel.get(r, c))) in prefer:
                cands.append((r, c))
    # spread candidates out: take every k-th
    if len(cands) > n_ignite:
        k = len(cands) // n_ignite
        cands = cands[::k][:n_ignite]
    print(f"\n{key}: probing {len(cands)} grass ignition points (fuel {sorted(prefer)})")
    for preset in ["high", "extreme"]:
        wx = make_wx(preset, wdir, 8.0)
        cfg = ResponseConfig(horizon_h=8.0, ensemble_n=1,
                             live_herb_frac=HERB, live_woody_frac=WOODY)
        rates = []
        for (r, c) in cands:
            origin = cell_ll(spec, r, c)
            arr = spread.compute_arrival(tile, [origin], wx, T0, cfg)
            rates.append(head_ros_from_arrival(arr, origin, wdir))
        rates.sort()
        med = rates[len(rates) // 2] if rates else 0.0
        mx = max(rates) if rates else 0.0
        w = INTENSITY_WEATHER[preset]["wind_mph"] * MPH_TO_MPS
        anchor = 0.10 * w * MPS_TO_KMH
        print(f"   {preset:>7}: realized head ROS km/h  median {med:4.2f}  max {mx:4.2f}"
              f"   (grass 10%-rule anchor {anchor:4.2f};  median/anchor "
              f"{med/anchor:0.2f}x, max/anchor {mx/anchor:0.2f}x)")


print("=" * 78)
print("A. MULTI-IGNITION ROBUSTNESS on real tiles (is the throttle a fluke?)")
print("=" * 78)
probe_tile("paradise_ca", {102, 104, 107})   # GR2/GR4/GR7
probe_tile("boulder_co", {102})              # GR2
probe_tile("malibu_ca", {145, 122})          # SH5/GS2


print("\n" + "=" * 78)
print("B. ROOT CAUSE — WAF/midflame sensitivity for GR2 (grass)")
print("=" * 78)
gr2 = fuels.FUEL_TABLE[102]
print("For each preset: what ROS does GR2 give as we feed different midflame winds?")
print("  faithful = current chain (WAF applied);  20ft = U10/1.15 (NO WAF);")
print("  U10 = feed the raw 10-m wind as midflame (WAF=1).  Anchor = 10%-rule.\n")
print(f"{'preset':>9} | {'U10':>5} | {'faithful mid':>12} | {'ros_faith':>9} | "
      f"{'ros_20ft':>8} | {'ros_U10':>8} | {'10%anchor':>9}")
for preset in ["low", "moderate", "high", "extreme"]:
    s = INTENSITY_WEATHER[preset]
    w = s["wind_mph"] * MPH_TO_MPS
    m_dead = max(0.01, fuels.dead_fuel_moisture_frac(s["temp_c"], s["rh_pct"]))
    u_faith = rothermel.midflame_wind_mps(w, gr2)
    r_faith = rothermel.surface_ros_mps(gr2, m_dead, HERB, WOODY, u_faith, 0.0).ros_mps
    r_20ft = rothermel.surface_ros_mps(gr2, m_dead, HERB, WOODY, w / 1.15, 0.0).ros_mps
    r_u10 = rothermel.surface_ros_mps(gr2, m_dead, HERB, WOODY, w, 0.0).ros_mps
    anchor = 0.10 * w
    print(f"{preset:>9} | {w:>5.1f} | {u_faith:>6.2f}={u_faith/w*100:>3.0f}%U10 | "
          f"{r_faith*MPS_TO_KMH:>8.2f}  | {r_20ft*MPS_TO_KMH:>7.2f}  | "
          f"{r_u10*MPS_TO_KMH:>7.2f}  | {anchor*MPS_TO_KMH:>8.2f}")

print("\n  ros_* in km/h.  If 'ros_20ft' or 'ros_U10' overshoot the anchor badly,")
print("  raising the WAF is NOT the right knob (it would over-speed at extreme).")

# What single ROS multiplier on the faithful chain hits the 10%-rule per preset?
print("\n  ROS multiplier that would make faithful GR2 == 10%-rule anchor:")
for preset in ["low", "moderate", "high", "extreme"]:
    s = INTENSITY_WEATHER[preset]
    w = s["wind_mph"] * MPH_TO_MPS
    m_dead = max(0.01, fuels.dead_fuel_moisture_frac(s["temp_c"], s["rh_pct"]))
    u_faith = rothermel.midflame_wind_mps(w, gr2)
    r_faith = rothermel.surface_ros_mps(gr2, m_dead, HERB, WOODY, u_faith, 0.0).ros_mps
    anchor = 0.10 * w
    print(f"   {preset:>9}: x{anchor/r_faith:0.2f}")

print("\nDONE.")
