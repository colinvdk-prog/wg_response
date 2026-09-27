"""What the per-fire off-domain horizon can MISS (SAFETY SEMANTICS 15,
2026-09-27). NOT product code — measurement 3 of docs/LIVE_WORLD_PLAN.md
"The off-domain horizon is per fire (2026-09-27)".

A known-slow fire is extrapolated beyond its domain at the speed its own
forecast (raw P10) front crossed the domain edge. This harness asks how LATE
that bound is when the fire then runs faster outside the domain than it
crossed, with the engine itself as the truth (same engine, same physics
flags, the weather or fuel that actually happens) so only the rule is on
trial:

  fire     a point ignition on flat ground, a protected client 1 km downwind
           (west wind); the operational DOMAIN is the live law's
           bbox(fire + client) + 2 km pad.
  forecast the operational ensemble (the production physics flags, incl.
           model_error_sd_log 0.71) on that domain; the rule reads its raw
           P10 edge crossing and, with a fresh known-slow report,
           extrapolates at clamp(P90, floor 1.0 km/h, ceiling 6.1 km/h).
  truth    the NOMINAL engine run on a 72 x 24 km domain around it, under
           the weather / fuel that actually happens.
  scenarios
    S0 steady 4 m/s in grass (forecast = truth): the validation case
    S1 FORECAST wind event 4 -> 12 m/s at +1 h, just after the P10 front
       left the domain (the forecast knew; the rule reads the crossing)
    S2 UNFORECAST event 4 -> 12 m/s at +1 h (forecast stays 4 m/s)
    S3 UNFORECAST extreme event 4 -> 20 m/s at +1 h
    S4 FUEL beyond the domain: the domain is timber litter (TL3), the world
       outside it grass (GR2); steady 6 m/s, forecast = truth
    S5 = S2 without the ensemble model-error term (model_error_sd_log 0):
       how much of the protection is that term, not the rule
    S6 = S4 at a steady 2 m/s: a front slow enough for the floor to bind

Along the downwind line beyond the domain's east edge (0-60 km): the truth's
arrival T(d) and each bound B(d) (the rule, the rule without its floor, the
legacy 1.7 m/s). The law passes a crew at d when B(d) - pass >= its margin,
so the worst crew margin it allows there is margin - (B(d) - T(d)); a crew
the law let through CAN BE OVERRUN wherever B(d) - T(d) > margin (60 min for
the rule, 56 for the legacy chord law).

    py -3 scratch_review/offmap/miss_case.py

Results: scratch_review/offmap/miss_case.json.
"""
import dataclasses
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "scratch_review", "contained"))

from ab_national import PROD  # noqa: E402
from wgresponse import geo, offmap, safety, spread  # noqa: E402
from wgresponse.contracts import (BBox, LatLon, Raster, ResponseConfig,  # noqa: E402
                                  TerrainTile, WeatherForecast, WeatherSample)
from wgresponse.terrain import SyntheticTerrain  # noqa: E402

T0 = 1_790_500_000.0
H = 3600.0
KMH = 3.6
IGN = LatLon(39.0, -120.0)
CLIENT = geo.destination(IGN, 90.0, 1_000.0)
OUT = os.path.join(HERE, "miss_case.json")
KNOWN_SLOW = (True, False, ("reported 'Minimal | Creeping' (synthetic)",))
GR2, TL3 = 102.0, 183.0


def wx(speed_fn, hours=60):
    return WeatherForecast(loc=IGN, samples=[
        WeatherSample(t_s=T0 + (i - 1) * H, wind_speed_mps=speed_fn(i - 1),
                      wind_gust_mps=speed_fn(i - 1) * 1.3, wind_dir_deg=270.0,
                      rh_pct=15.0, temp_c=32.0)
        for i in range(hours)], source="synthetic")


def steady(v):
    return lambda h: v


def event(v0, v1, at_h):
    return lambda h: v0 if h < at_h else v1


def with_fuel(tile, code, inside: "BBox | None" = None):
    """tile with every cell (or every cell whose centre is inside `inside`)
    set to fuel `code`."""
    spec = tile.spec
    data = list(tile.fuel.data)
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if inside is not None and not inside.contains(
                    geo.rc_to_latlon(spec, r, c)):
                continue
            data[r * spec.ncols + c] = code
    return TerrainTile(spec=spec, fuel=Raster(spec, data),
                       slope_deg=tile.slope_deg, aspect_deg=tile.aspect_deg,
                       elev_m=tile.elev_m, source=tile.source + "+fuel")


def main():
    base = dataclasses.replace(ResponseConfig(), **PROD,
                               corridor_offraster_per_fire=True,
                               corridor_offraster_margin_min=60.0,
                               horizon_h=48.0)
    terr = SyntheticTerrain("offmap-miss", kind="grass")
    dom = geo.bbox_around([IGN, CLIENT], 2_000.0)
    grass_dom = terr.tile(dom, 100.0)
    big = geo.bbox_around([geo.destination(IGN, 270.0, 6_000.0),
                           geo.destination(IGN, 90.0, 66_000.0),
                           geo.destination(IGN, 0.0, 12_000.0),
                           geo.destination(IGN, 180.0, 12_000.0)], 0.0)
    grass_big = terr.tile(big, 100.0)
    timber_dom = with_fuel(grass_dom, TL3)
    timber_in_grass = with_fuel(grass_big, TL3, inside=dom)
    no_merr = dataclasses.replace(base, model_error_sd_log=0.0)
    scenarios = [
        ("S0 steady 4 m/s, grass (forecast = truth)",
         steady(4.0), steady(4.0), grass_dom, grass_big, base),
        ("S1 FORECAST event 4 -> 12 m/s at +1 h",
         event(4.0, 12.0, 1.0), event(4.0, 12.0, 1.0), grass_dom, grass_big,
         base),
        ("S2 UNFORECAST event 4 -> 12 m/s at +1 h",
         steady(4.0), event(4.0, 12.0, 1.0), grass_dom, grass_big, base),
        ("S3 UNFORECAST extreme 4 -> 20 m/s at +1 h",
         steady(4.0), event(4.0, 20.0, 1.0), grass_dom, grass_big, base),
        ("S4 timber domain, grass beyond it, steady 6 m/s",
         steady(6.0), steady(6.0), timber_dom, timber_in_grass, base),
        ("S5 = S2 without the model-error term",
         steady(4.0), event(4.0, 12.0, 1.0), grass_dom, grass_big, no_merr),
        ("S6 timber domain, grass beyond it, steady 2 m/s (the floor's case)",
         steady(2.0), steady(2.0), timber_dom, timber_in_grass, base),
    ]
    east_lon = dom.east
    out = {"domain_km": [round((dom.east - dom.west) * geo.m_per_deg_lon(
        IGN.lat) / 1000, 1), round((dom.north - dom.south)
                                   * geo.M_PER_DEG_LAT / 1000, 1)],
           "scenarios": {}}
    for label, fwx, twx, tile, big_tile, cfg in scenarios:
        ens = spread.ensemble_arrival(tile, [IGN], wx(fwx), T0, cfg,
                                      incident_id="offmap-miss")
        h = offmap.horizon_for("offmap-miss", ens.p10, KNOWN_SLOW, cfg)
        raw = h.front_p90_mps
        er = safety._erode(ens.p10, cfg.corridor_erosion_m, shape="chebyshev")
        runs = safety._boundary_runs(er)
        if not runs:
            print(f"\n[{label}] the forecast never reaches its edge: no bound")
            out["scenarios"][label] = {"summary": {"kind": h.kind}}
            continue
        t_b = min(r[0] for r in runs)
        cell = ens.spec.cell_m
        truth = spread.compute_arrival(big_tile, [IGN], wx(twx), T0, cfg,
                                       incident_id="offmap-miss")
        rows = []
        for k in range(0, 241):
            d = k * 250.0
            p = LatLon(IGN.lat, east_lon + d / geo.m_per_deg_lon(IGN.lat))
            T = geo.raster_sample(truth, p)
            if T is None or not math.isfinite(T):
                continue
            b_rule = safety._bound_arrival_s(p, runs, h.ros_mps, cell)
            b_raw = safety._bound_arrival_s(p, runs, raw, cell)
            b_leg = safety._bound_arrival_s(p, runs, 1.7, cell)
            rows.append({"d_km": d / 1000.0, "truth_h": (T - T0) / H,
                         "late_rule_min": (b_rule - T) / 60.0,
                         "late_nofloor_min": (b_raw - T) / 60.0,
                         "late_legacy_min": (b_leg - T) / 60.0})
        at = {r["d_km"]: r for r in rows}

        def first(key, limit):
            return next((r["d_km"] for r in rows if r[key] > limit), None)

        def speed(d0, d1):
            if d0 in at and d1 in at and at[d1]["truth_h"] > at[d0]["truth_h"]:
                return round((d1 - d0) / (at[d1]["truth_h"]
                                          - at[d0]["truth_h"]), 2)
            return None
        summ = {
            "forecast_edge_crossed_h": round((t_b - T0) / H, 2),
            "truth_at_edge_h": round(rows[0]["truth_h"], 2),
            "kind": h.kind, "speed_kmh": round(h.ros_mps * KMH, 2),
            "front_p90_kmh": round(raw * KMH, 2),
            "front_max_kmh": round(h.front_max_mps * KMH, 2),
            "front_cells": h.front_n,
            "truth_kmh_0_10km": speed(0.0, 10.0),
            "truth_kmh_10_30km": speed(10.0, 30.0),
            "truth_kmh_30_60km": speed(30.0, 60.0),
            "late_rule_min_at_km": {str(d): round(at[d]["late_rule_min"], 1)
                                    for d in (5.0, 10.0, 20.0, 40.0, 60.0)
                                    if d in at},
            "max_late_rule_min": round(max(r["late_rule_min"] for r in rows), 1),
            "max_late_nofloor_min": round(max(r["late_nofloor_min"]
                                              for r in rows), 1),
            "max_late_legacy_min": round(max(r["late_legacy_min"]
                                             for r in rows), 1),
            "rule_late_from_km": first("late_rule_min", 0.0),
            "rule_overrun_from_km": first("late_rule_min", 60.0),
            "nofloor_overrun_from_km": first("late_nofloor_min", 60.0),
            "legacy_late_from_km": first("late_legacy_min", 0.0),
            "legacy_overrun_from_km": first("late_legacy_min", 56.0),
        }
        out["scenarios"][label] = {"summary": summ, "line": [
            {k: round(v, 2) for k, v in r.items()} for r in rows[::8]]}
        print(f"\n[{label}]")
        for k, v in summ.items():
            print(f"  {k}: {v}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
