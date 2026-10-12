"""Crown-fire study, controlled cases on uniform tiles (2026-10-12; NOT
product code; docs/review/crown_fire_model_2026-10-12.md sections 3.1-3.2).

  r91    Rothermel (1991) Table 1: eight wind-driven crown fire runs in the
         northern Rocky Mountains (fuel moistures, 20-ft wind, slope,
         observed average and maximum spread rates). For each run:
           - R10, the Rothermel surface spread of fuel model 10 with a 0.4
             wind reduction (the engine's rothermel module, all dead classes
             at the 1-h moisture), against Rothermel's own R10 column — a
             check of the surface model;
           - Rothermel 1991's crown rate 3.34 x R10 (the US systems' model);
           - Cruz et al. (2005) CROS at CBD 0.08 / 0.12 / 0.20 kg/m3 (the
             runs' CBD is not published; 0.08-0.20 spans northern Rockies
             mixed conifer), EFFM = the run's 1-h moisture, U10 = 1.15 x U20;
           - the ENGINE: a uniform flat tile (TU5 surface fuel, CBH 1.5 m,
             CBD 0.12, CH 20 m, CC 40 %), the run's wind and 1-h moisture
             (moisture_scale), crown_fire on, any-angle on, head speed from
             the arrival field between 2 and 14 km downwind.
  wind   the 10 %-of-wind benchmark (Cruz & Alexander 2019: ~10 % of the
         10-m wind, +-50 %, for forest and shrub fires in dry fuel and high
         wind): head speed on uniform conifer tiles, four timber fuels x
         three LANDFIRE-typical canopies x the simulator's four intensity
         presets (8 / 18 / 28 / 40 mph), crown fire off and on.

Usage: py -3 scratch_review/crown_fire_uniform.py r91|wind|all
Output: scratch_review/crown_fire_uniform.json (merged by mode).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import geo, rothermel, spread  # noqa: E402
from wgresponse.contracts import (CanopyLayers, FuelModel, GridSpec,  # noqa: E402
                                  ResponseConfig, TerrainTile,
                                  WeatherForecast, WeatherSample)
from wgresponse.fuels import (FT2FT3_TO_1M, FT_TO_M, LB_FT2_TO_KGM2,  # noqa: E402
                              dead_fuel_moisture_frac)

OUT = os.path.join(HERE, "crown_fire_uniform.json")
T0 = 1_752_000_000.0          # 2025-07-08 (a July day: FMC constant 100 %)
MPH = 2.2369362920544025
T_AC_TO_LB_FT2 = 0.0459137    # tons/acre -> lb/ft^2 (fuels.py docstring)

#: Anderson (1982) fuel model 10, timber (litter and understory)
FM10 = FuelModel(
    code=165, name="FM10 timber litter and understory", dynamic=False,
    load_1h_kgm2=3.01 * T_AC_TO_LB_FT2 * LB_FT2_TO_KGM2,
    load_10h_kgm2=2.00 * T_AC_TO_LB_FT2 * LB_FT2_TO_KGM2,
    load_100h_kgm2=5.01 * T_AC_TO_LB_FT2 * LB_FT2_TO_KGM2,
    load_live_herb_kgm2=0.0,
    load_live_woody_kgm2=2.00 * T_AC_TO_LB_FT2 * LB_FT2_TO_KGM2,
    sav_1h_1m=2000.0 * FT2FT3_TO_1M, sav_live_herb_1m=0.0,
    sav_live_woody_1m=1500.0 * FT2FT3_TO_1M, depth_m=1.0 * FT_TO_M,
    mx_dead_frac=0.25)

#: Rothermel (1991) Table 1a / 1b (fire, period, 1-h %, live %, 20-ft wind
#: mi/h (range: low, high), slope %, observed average R mi/h, max R mi/h,
#: Rothermel's R10 mi/h)
R91 = [
    ("Sundance", "2-7 pm", 7, 60, (30, 30), 0, 1.40, 2.5, 0.45),
    ("Sundance", "7-9 pm", 8, 60, (45, 45), 10, 3.0, 6.0, 0.78),
    ("Red Bench", "4:30-8:30 pm", 5, 70, (12, 12), 0, 0.52, 0.64, 0.20),
    ("Lily Lake", "2-5 pm", 5, 100, (25, 25), 20, 0.92, None, 0.29),
    ("Sandpoint", "2:30-5:30 pm", 4, 75, (12, 20), 0, 1.04, 1.76, 0.26),
    ("Pattee Canyon", "4:25-5:15 pm", 4, 100, (25, 35), 15, 1.56, None, 0.49),
    ("Mink Creek", "1-5 pm", 5, 100, (10, 20), 0, 0.55, None, 0.21),
    ("Black Tiger", "1-3:30 pm", 3, 90, (11, 11), 20, 0.51, 0.89, 0.12),
]


def r10_mph(m1_pct, live_pct, u20_mph, slope_pct):
    """Rothermel 1972/Albini surface spread of FM10 (mi/h) with Rothermel
    1991's wind reduction 0.4 on the 20-ft wind, every dead class at the
    1-h moisture (the engine's convention), slope collinear."""
    slope_deg = math.degrees(math.atan(slope_pct / 100.0))
    res = rothermel.surface_ros_mps(FM10, m1_pct / 100.0, 0.30,
                                    live_pct / 100.0,
                                    0.4 * u20_mph / MPH, slope_deg)
    return res.ros_mps * MPH


def tile(fuel_code, cbh, cbd, cc, ch, nrows=41, ncols=260, cell=60.0):
    spec = GridSpec(north_lat=45.0, west_lon=-114.0, cell_m=cell,
                    nrows=nrows, ncols=ncols)

    def mk(v):
        return geo.make_raster(spec, fill=float(v))

    canopy = CanopyLayers(mk(cbh), mk(cbd), mk(cc), mk(ch), source="test")
    return TerrainTile(spec, mk(fuel_code), mk(0.0), mk(-1.0), mk(1500.0),
                       "uniform", canopy=canopy)


def weather(loc, wind_mph, rh, temp_c, hours=40):
    w = wind_mph / MPH
    return WeatherForecast(loc=loc, source="uniform", samples=[
        WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=w,
                      wind_gust_mps=w * 1.3, wind_dir_deg=270.0, rh_pct=rh,
                      temp_c=temp_c) for i in range(hours)])


def head_kmh(t, wx, cfg, moisture_scale=1.0, from_km=2.0, to_km=14.0):
    """(head km/h, crown kinds {kind: cells}, wall s): the fire runs east
    along the centre row from column 5; the speed is the slope of arrival
    vs distance between from_km and to_km (or the farthest cell reached)."""
    spec = t.spec
    r0 = spec.nrows // 2
    ign = geo.rc_to_latlon(spec, r0, 5)
    trace = {}
    tic = time.time()
    a = spread.compute_arrival(t, [ign], wx, T0, cfg,
                               moisture_scale=moisture_scale,
                               crown_trace=trace).data
    wall = time.time() - tic
    row = [(c, a[r0 * spec.ncols + c]) for c in range(5, spec.ncols)]
    pts = [((c - 5) * spec.cell_m / 1000.0, v) for c, v in row
           if math.isfinite(v)]
    sel = [(d, v) for d, v in pts if from_km <= d <= to_km]
    if len(sel) < 3:
        sel = [(d, v) for d, v in pts if d >= 0.3]
    kinds = {}
    for k in trace.values():
        kinds[k] = kinds.get(k, 0) + 1
    if len(sel) < 3 or sel[-1][1] <= sel[0][1]:
        return None, kinds, wall
    (d1, v1), (d2, v2) = sel[0], sel[-1]
    return (d2 - d1) / ((v2 - v1) / 3600.0), kinds, wall


def scale_for(m_pct, rh=20.0, temp=30.0):
    """moisture_scale putting the engine's dead fuel moisture at m_pct."""
    return (m_pct / 100.0) / dead_fuel_moisture_frac(temp, rh)


def run_r91():
    rows = []
    base = ResponseConfig(ensemble_n=1, horizon_h=36.0, crown_fire=True,
                          spread_any_angle=True)
    for (fire, period, m1, live, (ulo, uhi), slope, r_obs, r_max,
         r10_pub) in R91:
        u20 = uhi                       # Rothermel: the upper end of a range
        r10 = r10_mph(m1, live, u20, slope)
        u10_kmh = 1.15 * u20 / MPH * 3.6
        cruz = {}
        for cbd in (0.05, 0.08, 0.12, 0.20):
            ra = rothermel.cruz_active_crown_ros_mps(1.15 * u20 / MPH, cbd, m1)
            cac = ra / rothermel.crown_active_threshold_mps(cbd)
            r = ra if cac >= 1.0 else ra * math.exp(-cac)
            cruz[str(cbd)] = {"mph": round(r * MPH, 2), "cac": round(cac, 2)}
        t = tile(165, 1.5, 0.12, 40.0, 20.0)
        r0 = t.spec.nrows // 2
        wx = weather(geo.rc_to_latlon(t.spec, r0, 5), 1.15 * u20, 20.0, 30.0)
        cfg = ResponseConfig(**{**base.__dict__, "live_woody_frac": live / 100.0})
        eng, kinds, wall = head_kmh(t, wx, cfg, scale_for(m1))
        eng_off, _k, _w = head_kmh(
            t, wx, ResponseConfig(**{**cfg.__dict__, "crown_fire": False}),
            scale_for(m1), from_km=0.3, to_km=3.0)
        row = {"fire": fire, "period": period, "m1h_pct": m1,
               "live_pct": live, "u20_mph": [ulo, uhi], "slope_pct": slope,
               "u10_kmh": round(u10_kmh, 1),
               "observed_mph": r_obs, "observed_max_mph": r_max,
               "r10_published_mph": r10_pub, "r10_engine_mph": round(r10, 3),
               "rothermel1991_mph": round(3.34 * r10, 2),
               "cruz2005_mph": cruz,
               "engine_crown_mph": round(eng / 1.609344, 2) if eng else None,
               "engine_crown_kinds": kinds,
               "engine_surface_mph": round(eng_off / 1.609344, 3) if eng_off else None,
               "solve_s": round(wall, 2)}
        rows.append(row)
        print(f"{fire:14s} {period:13s} obs {r_obs:4.2f} (max {r_max}) | R10 "
              f"pub {r10_pub:.2f} eng {r10:.2f} | R91 {3.34 * r10:4.2f} | "
              f"Cruz 0.05/0.08/0.12/0.20 {cruz['0.05']['mph']}/"
              f"{cruz['0.08']['mph']}/{cruz['0.12']['mph']}/{cruz['0.2']['mph']}"
              f" | engine {row['engine_crown_mph']} mph {kinds} | surface "
              f"{row['engine_surface_mph']}", flush=True)
    return rows


FUELS = {"TU1": 161, "TU5": 165, "TL3": 183, "TL8": 188}
CANOPIES = {
    "dense": (0.5, 0.16, 65.0, 20.0),
    "moderate": (1.0, 0.10, 55.0, 18.0),
    "open": (2.0, 0.06, 35.0, 15.0),
}
PRESETS = {"low": (8.0, 35.0, 28.0), "moderate": (18.0, 22.0, 31.0),
           "high": (28.0, 12.0, 35.0), "extreme": (40.0, 6.0, 39.0)}


def run_wind():
    rows = []
    for fname, code in FUELS.items():
        for cname, (cbh, cbd, cc, ch) in CANOPIES.items():
            t = tile(code, cbh, cbd, cc, ch)
            r0 = t.spec.nrows // 2
            for pname, (mph, rh, temp) in PRESETS.items():
                wx = weather(geo.rc_to_latlon(t.spec, r0, 5), mph, rh, temp)
                cfg_on = ResponseConfig(ensemble_n=1, horizon_h=36.0,
                                        crown_fire=True,
                                        spread_any_angle=True)
                cfg_off = ResponseConfig(ensemble_n=1, horizon_h=36.0,
                                         spread_any_angle=True)
                on, kinds, _w = head_kmh(t, wx, cfg_on)
                off, _k, _w2 = head_kmh(t, wx, cfg_off, from_km=0.3,
                                        to_km=3.0)
                u10_kmh = mph * 1.609344
                row = {"fuel": fname, "canopy": cname, "cbh_m": cbh,
                       "cbd_kgm3": cbd, "cc_pct": cc, "ch_m": ch,
                       "preset": pname, "wind_mph": mph, "rh_pct": rh,
                       "temp_c": temp,
                       "head_kmh_surface": round(off, 3) if off else None,
                       "head_kmh_crown": round(on, 2) if on else None,
                       "pct_of_u10_surface": round(100 * off / u10_kmh, 2) if off else None,
                       "pct_of_u10_crown": round(100 * on / u10_kmh, 1) if on else None,
                       "kinds": kinds}
                rows.append(row)
                print(f"{fname} {cname:8s} {pname:8s} {mph:4.0f} mph | surface "
                      f"{row['head_kmh_surface']} km/h ({row['pct_of_u10_surface']}%) | "
                      f"crown {row['head_kmh_crown']} km/h ({row['pct_of_u10_crown']}%) "
                      f"{kinds}", flush=True)
    return rows


def main(mode):
    try:
        with open(OUT, encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        store = {}
    if mode in ("r91", "all"):
        store["r91"] = run_r91()
    if mode in ("wind", "all"):
        store["wind"] = run_wind()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
