"""EXP 6 — TRAVEL-TIME REALISM of the flat Tier-0 model.

Model: t = haversine * 1.35 (circuity) * 1.4 (fire traffic) / 17.9 m/s
     => effective straight-line speed 9.47 m/s = 34.1 km/h = 21.2 mph.

Five realistic WUI crew->property pairs (well-known fire-country roads),
compared against typical NORMAL-conditions drive times (reference values are
analyst estimates from route knowledge, not API measurements — flagged as
such; the model's built-in 1.4x is supposed to cover fire-condition
congestion ON TOP of the normal time, so 'model vs normal' understates any
optimism under real evacuation load).

Run: py -3 scratch_review/da_exp6_travel.py
"""
from __future__ import annotations

import math

from da_common import destination

from wgresponse.contracts import LatLon, ResponseConfig
from wgresponse.travel import travel_time_s

# (name, lat/lon a, lat/lon b, plausible NORMAL drive minutes (estimate),
#  road character)
PAIRS = [
    ("Paradise -> Magalia CA (Skyway)",
     LatLon(39.7596, -121.6219), LatLon(39.8122, -121.5783), 11.0,
     "2-lane WUI arterial, through town"),
    ("Boulder -> Nederland CO (Canyon Dr)",
     LatLon(40.0150, -105.2705), LatLon(39.9614, -105.5108), 35.0,
     "canyon 2-lane, winding"),
    ("Hemet -> Idyllwild CA (SR-74/243)",
     LatLon(33.7475, -116.9720), LatLon(33.7469, -116.7119), 45.0,
     "mountain switchbacks, 1500 m climb"),
    ("Santa Rosa -> Calistoga CA (Calistoga Rd)",
     LatLon(38.4405, -122.7141), LatLon(38.5788, -122.5797), 35.0,
     "winding secondary over Mark West grade"),
    ("Sylmar -> Acton CA (I-5/SR-14)",
     LatLon(34.3083, -118.4370), LatLon(34.4700, -118.1963), 25.0,
     "freeway corridor (2018 Creek/Sand fire ground)"),
]


def main():
    cfg = ResponseConfig()
    eff_kmh = cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor) * 3.6
    print(f"flat model effective straight-line speed: {eff_kmh:.1f} km/h "
          f"({eff_kmh/1.609:.1f} mph)\n")
    print(f"{'pair':<44} {'hav_km':>6} {'model_min':>9} {'ref_min*':>8} "
          f"{'errN%':>6} {'fire_ref':>8} {'errF%':>6}")
    errs_n, errs_f = [], []
    for name, a, b, ref_min, road in PAIRS:
        # haversine via travel module for consistency
        t_model = travel_time_s(a, b, cfg) / 60.0
        hav_km = t_model * 60.0 * (cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)) / 1000.0
        fire_ref = ref_min * 1.4          # nominal fire-condition reference
        err_n = 100.0 * (t_model - ref_min) / ref_min
        err_f = 100.0 * (t_model - fire_ref) / fire_ref
        errs_n.append(err_n)
        errs_f.append(err_f)
        print(f"{name:<44} {hav_km:>6.1f} {t_model:>9.1f} {ref_min:>8.0f} "
              f"{err_n:>+6.0f} {fire_ref:>8.0f} {err_f:>+6.0f}  [{road}]")
    print("\n* ref_min = analyst route-knowledge estimate, NORMAL conditions "
          "(no routing API available in review env).")
    print("  fire_ref = ref_min x 1.4 (the congestion allowance the model's own "
          "fire_traffic_factor claims to cover).")
    print(f"error vs normal:        {min(errs_n):+.0f}% .. {max(errs_n):+.0f}%")
    print(f"error vs fire-condition: {min(errs_f):+.0f}% .. {max(errs_f):+.0f}% "
          "(negative = model EARLY = crew later than planned)")
    print("\nmargin context: route_margin_min=60, egress_buffer_min=30; "
          "worst observed planned stop margin in EXP1 was 0.17 h = 10.2 min.")


if __name__ == "__main__":
    main()
