"""diag_ros_presets.py — SIMULATOR fire-spread-rate review (read-only).

Quantifies the CURRENT simulator demonstrated spread at each intensity preset
(low/mod/high/extreme -> the sim's MockWeather wind/RH) and compares head-fire
ROS to published anchors (Cruz-Alexander 10% grass rule, Tubbs, Camp,
chaparral/timber ranges). Also decomposes the 10-m -> midflame WAF loss.

Product code untouched. Run: py -3 scratch_review/diag_ros_presets.py
"""
from __future__ import annotations

import math

from wgresponse import fuels, rothermel
from wgresponse.simui import INTENSITY_WEATHER, MPH_TO_MPS

MPS_TO_KMH = 3.6
HERB = 0.30    # cfg.live_herb_frac default (cured fire season)
WOODY = 0.60   # cfg.live_woody_frac default

# Preset 10-m sustained winds (m/s) as the sim builds them.
PRESETS = ["low", "moderate", "high", "extreme"]


def preset_wind_mps(name):
    return INTENSITY_WEATHER[name]["wind_mph"] * MPH_TO_MPS


def point_ros(fuel, wind10_mps, temp_c, rh_pct, slope_deg=0.0):
    """The exact single-cell chain spread.compute_arrival uses on flat ground."""
    m_dead = max(0.01, fuels.dead_fuel_moisture_frac(temp_c, rh_pct))
    u_mid = rothermel.midflame_wind_mps(wind10_mps, fuel)
    res = rothermel.surface_ros_mps(fuel, m_dead, HERB, WOODY, u_mid, slope_deg)
    return res, u_mid, m_dead


def waf_of(fuel):
    depth_ft = fuel.depth_m / 0.3048
    if depth_ft <= 0:
        return 0.0
    return 1.83 / math.log((20.0 + 0.36 * depth_ft) / (0.13 * depth_ft))


# Dominant fuels per tile (from provenance top_codes).
TILE_FUELS = {
    "paradise_ca (GR2/GS2/TL timber-litter WUI)":
        [102, 122, 184, 183, 165],   # GR2, GS2, TL4, TL3, TU5
    "malibu_ca (chaparral)":
        [142, 145, 122, 121],        # SH2, SH5, GS2, GS1
    "boulder_co (grass)":
        [102, 101, 122],             # GR2, GR1, GS2
}


def anchor_ros_kmh(fuel_code, wind10_mps):
    """Published-anchor head ROS (km/h) for this fuel class at this 10-m wind.
    Returns (label, observed_kmh) or (None, None) if no clean anchor."""
    # grass: Cruz & Alexander 10% rule (head ROS ~= 0.10 * U10)
    if 101 <= fuel_code <= 109:
        return ("10%-rule", 0.10 * wind10_mps * MPS_TO_KMH)
    # grass-shrub: between grass and shrub; use ~7% of U10 as a soft anchor
    if 121 <= fuel_code <= 124:
        return ("~7%-rule(GS)", 0.07 * wind10_mps * MPS_TO_KMH)
    return (None, None)


def banner(s):
    print("\n" + "=" * 78)
    print(s)
    print("=" * 78)


banner("1. PRESET WEATHER (as the sim's MockWeather builds it)")
print(f"{'preset':>9} | {'wind mph':>8} | {'wind m/s':>8} | {'10% km/h':>9} | "
      f"{'RH %':>5} | {'temp C':>6}")
for name in PRESETS:
    w = preset_wind_mps(name)
    spec = INTENSITY_WEATHER[name]
    print(f"{name:>9} | {spec['wind_mph']:>8.0f} | {w:>8.2f} | "
          f"{0.10*w*MPS_TO_KMH:>9.2f} | {spec['rh_pct']:>5.0f} | {spec['temp_c']:>6.0f}")
print("\n  (10% km/h = Cruz-Alexander grass head-ROS anchor = 10% of the 10-m wind)")


banner("2. WAF (wind-adjustment-factor) LOSS: 10-m wind -> midflame wind")
print("  midflame = (U10 / 1.15) * WAF ;  WAF = 1.83/ln[(20+0.36d)/(0.13d)], d=depth ft")
print(f"\n{'fuel':>26} | {'depth ft':>8} | {'WAF':>5} | {'midflame frac':>13}")
seen = set()
for label, codes in TILE_FUELS.items():
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        f = fuels.FUEL_TABLE[c]
        depth_ft = f.depth_m / 0.3048
        waf = waf_of(f)
        # fraction of the 10-m wind that survives to midflame
        frac = waf / 1.15
        print(f"{f.name[:26]:>26} | {depth_ft:>8.2f} | {waf:>5.3f} | "
              f"{frac*100:>11.1f} %")


banner("3. POINT HEAD-ROS per fuel x preset  (flat ground, slope=0)")
print("  head ROS km/h (ratio vs anchor where one exists); '%U10' = ROS as % of 10-m wind\n")
for label, codes in TILE_FUELS.items():
    print(f"--- {label} ---")
    hdr = f"{'fuel':>28} |"
    for name in PRESETS:
        hdr += f" {name:>16} |"
    print(hdr)
    for c in codes:
        f = fuels.FUEL_TABLE[c]
        row = f"{f.name[:28]:>28} |"
        for name in PRESETS:
            spec = INTENSITY_WEATHER[name]
            w = preset_wind_mps(name)
            res, u_mid, m_dead = point_ros(f, w, spec["temp_c"], spec["rh_pct"])
            ros_kmh = res.ros_mps * MPS_TO_KMH
            pctU10 = (res.ros_mps / w * 100.0) if w > 0 else 0.0
            alabel, obs = anchor_ros_kmh(c, w)
            if obs:
                cell = f"{ros_kmh:5.2f}({ros_kmh/obs:0.2f}x)"
            else:
                cell = f"{ros_kmh:5.2f}[{pctU10:4.1f}%U10]"
            row += f" {cell:>16} |"
        print(row)
    print()

print("  ratios (Nx) are sim/anchor for grass & grass-shrub; <1 = sim SLOWER than the anchor.")
print("  [%U10] for shrub/timber where a single % rule doesn't cleanly apply.")


banner("4. KEY ANCHOR CHECKS")
# Tubbs: GR2 grass under ~Diablo wind. Review used 12 m/s/10%RH ~ 'high' preset.
# Compare GR2 at 'high' and 'extreme' to Tubbs 6.4 km/h sustained.
gr2 = fuels.FUEL_TABLE[102]
for name in ["high", "extreme"]:
    spec = INTENSITY_WEATHER[name]
    w = preset_wind_mps(name)
    res, u_mid, m_dead = point_ros(gr2, w, spec["temp_c"], spec["rh_pct"])
    ros_kmh = res.ros_mps * MPS_TO_KMH
    print(f"  Tubbs anchor 6.4 km/h (grass) vs GR2 @ {name:>7} "
          f"({w:4.1f} m/s): {ros_kmh:4.2f} km/h = {ros_kmh/6.4:0.2f}x  "
          f"[midflame {u_mid:4.2f} m/s = {u_mid/w*100:0.0f}% of U10, m_dead {m_dead*100:0.1f}%]")

# chaparral SH5 typical wind-driven Santa Ana head ROS ~ 3-5 km/h.
sh5 = fuels.FUEL_TABLE[145]
for name in ["high", "extreme"]:
    spec = INTENSITY_WEATHER[name]
    w = preset_wind_mps(name)
    res, u_mid, m_dead = point_ros(sh5, w, spec["temp_c"], spec["rh_pct"])
    ros_kmh = res.ros_mps * MPS_TO_KMH
    print(f"  Chaparral wind-driven ~3-5 km/h vs SH5 @ {name:>7} "
          f"({w:4.1f} m/s): {ros_kmh:4.2f} km/h  [midflame {u_mid:4.2f} m/s]")

print("\nDONE.")
