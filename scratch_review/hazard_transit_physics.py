"""hazard_transit_physics — READ-ONLY derivation for the TRANSIT-HAZARD MODEL SPEC.

Computes, per FBFM40 class group and per simulator intensity preset, using the
repo's OWN physics (wgresponse.fuels.FUEL_TABLE + wgresponse.rothermel, the
identical call path spread.py uses at cell_params):

  * head ROS, Byram fireline intensity I, Byram flame length L
  * Anderson (1969) flame-front residence time t_r = 384 / sigma_char  (min)
  * flaming-zone DEPTH  D_f = R * t_r  (m)   <- "flame density" / optical depth
  * flame emissivity    eps = 1 - exp(-kappa * D_f)
  * emissive power      E = eps * sigma_SB * T_f^4
  * radiant flux at separation d:  q(d) = 0.5 * H/sqrt(H^2+d^2) * E
    (2-D infinite-front view factor to a ground-level receiver facing the flame)
  * standing safe separation (Butler & Cohen 1998, 7 kW/m^2)
  * transit safe separation from a THERMAL DOSE budget, dose = q^(4/3) * t

Nothing here is imported by wgresponse/*. Pure analysis.  py -3 this file.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wgresponse import rothermel
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac, live_moisture_defaults

# --- constants -------------------------------------------------------------
SIGMA_SB = 5.670374419e-8          # Stefan-Boltzmann, W/m^2/K^4
T_FLAME_K = 1200.0                 # Butler & Cohen 1998 wildland flame temp
KAPPA = 0.5                        # flame absorption coeff, 1/m (0.3-0.8 lit.)
E_BLACK_KWM2 = SIGMA_SB * T_FLAME_K ** 4 / 1000.0     # 117.6 kW/m^2

MPH_TO_MPS = 0.44704
INTENSITY_WEATHER = {              # verbatim simui.INTENSITY_WEATHER
    "low":      {"wind_mph": 8.0,  "rh_pct": 35.0, "temp_c": 28.0},
    "moderate": {"wind_mph": 18.0, "rh_pct": 22.0, "temp_c": 31.0},
    "high":     {"wind_mph": 28.0, "rh_pct": 12.0, "temp_c": 35.0},
    "extreme":  {"wind_mph": 40.0, "rh_pct": 6.0,  "temp_c": 39.0},
}

CLASS_GROUPS = [
    ("GR", range(101, 110)),
    ("GS", range(121, 125)),
    ("SH", range(141, 150)),
    ("TU", range(161, 166)),
    ("TL", range(181, 190)),
    ("SB", range(201, 205)),
]

# Anderson 1969 residence time applied PER SIZE CLASS: t = 384/sigma (min)
SAV_BY_TIMELAG = {"1h": 2000.0, "10h": 109.0, "100h": 30.0, "1000h": 8.0}


def view_factor(h_m: float, d_m: float) -> float:
    """2-D view factor, ground-level receiver facing an infinitely long vertical
    flame band of height h at horizontal distance d:  F = 0.5*h/sqrt(h^2+d^2).
    (Differential-element-to-2D-surface: F = 0.5*(sin th2 - sin th1).)"""
    if h_m <= 0.0:
        return 0.0
    return 0.5 * h_m / math.hypot(h_m, d_m)


def flux_kwm2(h_m: float, d_m: float, emis: float) -> float:
    return view_factor(h_m, d_m) * emis * E_BLACK_KWM2


def sep_for_flux(h_m: float, emis: float, q_target: float) -> float:
    """Separation d (m) at which flux falls to q_target. Closed form:
    0.5*h/sqrt(h^2+d^2)*E = q  ->  d = h*sqrt((0.5E/q)^2 - 1)."""
    if h_m <= 0.0 or emis <= 0.0:
        return 0.0
    k = 0.5 * emis * E_BLACK_KWM2 / q_target
    if k <= 1.0:
        return 0.0                      # even touching the flame is below target
    return h_m * math.sqrt(k * k - 1.0)


def sep_for_dose(h_m: float, emis: float, dose_budget: float, t_s: float) -> float:
    """Separation (m) keeping thermal dose q^(4/3)*t at/below dose_budget."""
    if t_s <= 0.0:
        return 0.0
    q_allow = (dose_budget / t_s) ** 0.75
    return sep_for_flux(h_m, emis, q_allow)


def run_fuel(code: int, preset: str, slope_deg: float = 0.0):
    fuel = FUEL_TABLE[code]
    wx = INTENSITY_WEATHER[preset]
    u10 = wx["wind_mph"] * MPH_TO_MPS
    m_dead = dead_fuel_moisture_frac(wx["temp_c"], wx["rh_pct"])
    herb, woody = live_moisture_defaults("summer")
    bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
    if bed is None:
        return None
    u_mid = rothermel.midflame_wind_mps(u10, fuel)
    u_slope = rothermel.effective_wind_from_bed(bed, 0.0, slope_deg)
    u_eff = math.hypot(u_mid, u_slope) if slope_deg else u_mid
    res = rothermel.surface_ros_from_bed(bed, u_eff, 0.0)
    t_r_min = 384.0 / bed.sigma_char
    depth_m = res.ros_mps * t_r_min * 60.0
    emis = 1.0 - math.exp(-KAPPA * depth_m)
    return {
        "code": code, "name": fuel.name, "preset": preset,
        "u10": u10, "u_mid": u_mid, "m_dead": m_dead,
        "sigma_char": bed.sigma_char,
        "ros_mps": res.ros_mps, "ros_kmh": res.ros_mps * 3.6,
        "I_kwm": res.intensity_kwm, "L_m": res.flame_length_m,
        "t_r_min": t_r_min, "depth_m": depth_m, "emis": emis,
        "E_kwm2": emis * E_BLACK_KWM2,
    }


def size_class_windows(code: int):
    """Per-timelag Anderson residence times for the classes this model carries."""
    f = FUEL_TABLE[code]
    out = []
    if f.load_1h_kgm2 > 0 or f.load_live_herb_kgm2 > 0:
        out.append(("1h", 384.0 / SAV_BY_TIMELAG["1h"]))
    if f.load_10h_kgm2 > 0:
        out.append(("10h", 384.0 / SAV_BY_TIMELAG["10h"]))
    if f.load_100h_kgm2 > 0:
        out.append(("100h", 384.0 / SAV_BY_TIMELAG["100h"]))
    return out


def main():
    print("=" * 100)
    print("A. BLACK-BODY / DOSE CONSTANTS")
    print("=" * 100)
    print(f"  sigma*T^4 at {T_FLAME_K:.0f} K  = {E_BLACK_KWM2:.1f} kW/m^2 (eps=1 ceiling)")
    print(f"  kappa (flame absorption)      = {KAPPA} 1/m")
    for x in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0):
        q = flux_kwm2(1.0, x, 1.0)
        print(f"   d/H={x:4.1f}  q(eps=1) = {q:6.2f} kW/m^2")
    print("  -> 7 kW/m^2 (Butler&Cohen 1998) at d/H =",
          f"{sep_for_flux(1.0, 1.0, 7.0):.2f} (infinite front, eps=1)")

    print()
    print("=" * 100)
    print("B. PER-SIZE-CLASS ANDERSON RESIDENCE TIMES  t = 384/sigma (min)")
    print("=" * 100)
    for k, s in SAV_BY_TIMELAG.items():
        print(f"   {k:>6}  sigma={s:7.1f} ft^-1   t_r = {384.0/s:7.2f} min")

    print()
    print("=" * 100)
    print("C. PER-FUEL-MODEL FIRE BEHAVIOUR  (slope 0, sim INTENSITY_WEATHER, summer live)")
    print("=" * 100)
    hdr = (f"{'code':>4} {'model':<34} {'U10':>5} {'Umid':>5} {'md%':>5} "
           f"{'ROS':>7} {'I':>9} {'L':>6} {'t_r':>6} {'D_f':>7} {'eps':>5} "
           f"{'E':>6} {'d7':>7} {'4L':>6}")
    for preset in ("low", "moderate", "high", "extreme"):
        print(f"\n--- preset {preset} "
              f"(U10={INTENSITY_WEATHER[preset]['wind_mph']*MPH_TO_MPS:.2f} m/s) ---")
        print(hdr)
        print(f"{'':>4} {'':<34} {'m/s':>5} {'m/s':>5} {'':>5} "
              f"{'km/h':>7} {'kW/m':>9} {'m':>6} {'min':>6} {'m':>7} {'':>5} "
              f"{'kW/m2':>6} {'m':>7} {'m':>6}")
        for gname, codes in CLASS_GROUPS:
            for code in codes:
                if code not in FUEL_TABLE:
                    continue
                r = run_fuel(code, preset)
                if r is None:
                    continue
                d7 = sep_for_flux(r["L_m"], r["emis"], 7.0)
                print(f"{code:>4} {r['name'][:34]:<34} {r['u10']:5.2f} "
                      f"{r['u_mid']:5.2f} {r['m_dead']*100:5.1f} "
                      f"{r['ros_kmh']:7.3f} {r['I_kwm']:9.1f} {r['L_m']:6.2f} "
                      f"{r['t_r_min']:6.3f} {r['depth_m']:7.2f} {r['emis']:5.3f} "
                      f"{r['E_kwm2']:6.1f} {d7:7.1f} {4*r['L_m']:6.1f}")

    print()
    print("=" * 100)
    print("D. CLASS-GROUP ROLL-UP  (median / max over models in the group)")
    print("=" * 100)
    for preset in ("moderate", "high", "extreme"):
        print(f"\n--- preset {preset} ---")
        print(f"{'grp':>4} {'n':>3} {'I med':>8} {'I max':>9} {'L med':>6} {'L max':>6} "
              f"{'t_r med':>8} {'D_f med':>8} {'eps med':>8} {'d7 med':>7} {'d7 max':>7}")
        for gname, codes in CLASS_GROUPS:
            rows = [run_fuel(c, preset) for c in codes if c in FUEL_TABLE]
            rows = [r for r in rows if r]
            if not rows:
                continue
            def med(key):
                v = sorted(x[key] for x in rows)
                n = len(v)
                return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])
            d7s = sorted(sep_for_flux(r["L_m"], r["emis"], 7.0) for r in rows)
            n = len(d7s)
            d7med = d7s[n // 2] if n % 2 else 0.5 * (d7s[n // 2 - 1] + d7s[n // 2])
            print(f"{gname:>4} {len(rows):>3} {med('I_kwm'):8.0f} "
                  f"{max(r['I_kwm'] for r in rows):9.0f} {med('L_m'):6.2f} "
                  f"{max(r['L_m'] for r in rows):6.2f} {med('t_r_min'):8.3f} "
                  f"{med('depth_m'):8.2f} {med('emis'):8.3f} {d7med:7.1f} "
                  f"{max(d7s):7.1f}")

    print()
    print("=" * 100)
    print("E. TRANSIT DOSE — separation needed for a vehicle passing an ACTIVE front")
    print("=" * 100)
    print("dose = q^(4/3) * t   [(kW/m^2)^4/3 s].  HSE/Hymes anchors:")
    print("   pain ~92 TDU | 2nd-degree blister ~290 TDU | 1% fatality ~1000 TDU")
    print("Budget used below: 200 TDU (bare skin at the window, below blister).")
    for v_kmh in (30.0, 40.0, 50.0):
        v = v_kmh / 3.6
        print(f"\n  speed {v_kmh:.0f} km/h ({v:.2f} m/s)")
        for run_m in (90.0, 180.0, 270.0):
            t = run_m / v
            print(f"    exposed run {run_m:5.0f} m -> t = {t:5.1f} s")
            for L in (1.0, 2.0, 3.0, 5.0, 8.0):
                for emis in (0.5, 1.0):
                    d = sep_for_dose(L, emis, 200.0, t)
                    q_at_10 = flux_kwm2(L, 10.0, emis)
                    print(f"      L={L:4.1f} m eps={emis:3.1f}: d_req={d:6.1f} m "
                          f"({d/L:5.2f} x L) | q@10m={q_at_10:6.2f} kW/m^2 "
                          f"dose@10m={q_at_10**(4/3)*t:8.1f} TDU")

    print()
    print("=" * 100)
    print("F. SIZE-CLASS BURNOUT WINDOWS PER MODEL (which timelag classes are loaded)")
    print("=" * 100)
    for gname, codes in CLASS_GROUPS:
        for code in codes:
            if code not in FUEL_TABLE:
                continue
            f = FUEL_TABLE[code]
            w = size_class_windows(code)
            longest = max((t for _k, t in w), default=0.0)
            heavy = f.load_100h_kgm2
            print(f"  {code:>4} {f.name[:38]:<38} classes={','.join(k for k,_ in w):<12} "
                  f"t_max={longest:6.2f} min  w100h={heavy:5.3f} kg/m2 "
                  f"w10h={f.load_10h_kgm2:5.3f}")

    print()
    print("=" * 100)
    print("G. ROAD GEOMETRY — clear separation from roadside fuel edge to lane centre")
    print("=" * 100)
    roads = {
        "track/service":      (4.0, 0.0),
        "residential":        (8.0, 0.5),
        "tertiary/unclass":   (9.0, 1.0),
        "secondary":          (11.0, 1.5),
        "primary/trunk":      (13.0, 2.5),
        "motorway":           (24.0, 5.0),
    }
    print(f"{'class':<20} {'paved m':>8} {'shoulder':>9} {'half-width':>11} "
          f"{'d_lane (m)':>11} {'d_far lane':>11}")
    for k, (paved, shoulder) in roads.items():
        half = paved / 2.0
        d_near = half / 2.0 + shoulder + 0.0          # near-lane centre to fuel edge
        d_near = shoulder + paved / 4.0
        d_far = shoulder + 3.0 * paved / 4.0
        print(f"{k:<20} {paved:8.1f} {shoulder:9.1f} {half:11.1f} "
              f"{d_near:11.2f} {d_far:11.2f}")

    print()
    print("=" * 100)
    print("H. PASSABILITY MATRIX INPUTS: q and dose at the FAR-LANE offset, per class")
    print("=" * 100)
    print("Vehicle at far lane centre, one 90-m cell of active front alongside,")
    print("crew speed = class free-flow / fire_traffic_factor 1.4.")
    speeds = {"track/service": 7.0 / 1.4, "residential": 11.0 / 1.4,
              "tertiary/unclass": 15.0 / 1.4, "secondary": 18.0 / 1.4,
              "primary/trunk": 22.0 / 1.4, "motorway": 29.0 / 1.4}
    for preset in ("moderate", "high", "extreme"):
        print(f"\n--- preset {preset} ---")
        print(f"{'grp':>4} {'L p50':>6} {'L p90':>6} {'eps':>5} | " +
              " | ".join(f"{k.split('/')[0][:8]:>16}" for k in roads))
        for gname, codes in CLASS_GROUPS:
            rows = [run_fuel(c, preset) for c in codes if c in FUEL_TABLE]
            rows = [r for r in rows if r]
            if not rows:
                continue
            Ls = sorted(r["L_m"] for r in rows)
            es = sorted(r["emis"] for r in rows)
            n = len(Ls)
            Lmed = Ls[n // 2] if n % 2 else 0.5 * (Ls[n // 2 - 1] + Ls[n // 2])
            L90 = Ls[min(n - 1, int(math.ceil(0.9 * n)) - 1)]
            emed = es[n // 2] if n % 2 else 0.5 * (es[n // 2 - 1] + es[n // 2])
            cells = []
            for k, (paved, shoulder) in roads.items():
                d_far = shoulder + 3.0 * paved / 4.0
                q = flux_kwm2(L90, d_far, emed)
                t = 90.0 / speeds[k]
                cells.append(f"{q:6.1f}kW {q**(4/3)*t:7.0f}TDU")
            print(f"{gname:>4} {Lmed:6.2f} {L90:6.2f} {emed:5.3f} | " +
                  " | ".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
