"""Signed forecast differences of each weather point vs the fire centroid
(SAFETY SEMANTICS 17, 2026-09-27). NOT product code — the "why" behind
measurement B in docs/LIVE_WORLD_PLAN.md "Spread reads the local weather
forecast (2026-09-27)".

Re-reads the points a measurement JSON recorded (A.per_point_vs_centroid:
lat/lon at 1e-4 deg, i.e. the exact NWS points URLs) through the same NWS
disk cache, and reports per point the MEAN SIGNED difference from the
centroid over hours [0, H] after the snapshot: wind, gust, RH, temperature,
the resulting Simard dead-fuel moisture, and the mean direction difference.

    py -3 scratch_review/wxfield/zone_signed.py JSON INCIDENT_NAME [H]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "scratch_review", "contained"))
sys.path.insert(0, HERE)

from national_wxfield import NWS_CACHE, Clock, DiskNws  # noqa: E402
from wgresponse import weather  # noqa: E402
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.fuels import dead_fuel_moisture_frac  # noqa: E402


def main():
    path, name = sys.argv[1], sys.argv[2]
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else 72
    d = json.load(open(path, encoding="utf-8"))
    if "A" in d and isinstance(d["A"], dict) and "per_point_vs_centroid" in d["A"]:
        a = d["A"]
    else:
        (a,) = [v for v in d["A"].values() if v["name"] == name]
    now = float(d["now_s"])
    w = weather.NwsWeather(http_get=DiskNws(NWS_CACHE),
                           cfg=ResponseConfig(weather_grid_km=10.0),
                           cache=weather.NwsForecastCache(clock=Clock()))
    pts = a["per_point_vs_centroid"]
    fcs = [w.forecast(LatLon(p["lat"], p["lon"]), 96.0) for p in pts]
    c = fcs[0]
    print(f"{name}: mean signed difference vs the centroid over 0-{hours} h")
    print("zone  km  brg  cells   dwind  dgust   dRH   dT    dFM(pct-pts)  |ddir|")
    for p, fc in zip(pts, fcs):
        n = hours + 1
        sw = sg = sr = st = sm = sd = 0.0
        for h in range(n):
            t = now + h * 3600.0
            s, b = fc.at(t), c.at(t)
            sw += s.wind_speed_mps - b.wind_speed_mps
            sg += s.wind_gust_mps - b.wind_gust_mps
            sr += s.rh_pct - b.rh_pct
            st += s.temp_c - b.temp_c
            sm += 100.0 * (dead_fuel_moisture_frac(s.temp_c, s.rh_pct)
                           - dead_fuel_moisture_frac(b.temp_c, b.rh_pct))
            dd = abs((s.wind_dir_deg - b.wind_dir_deg) % 360.0)
            sd += min(dd, 360.0 - dd)
        print(f"{p['zone']:4d} {p['km_from_centroid']:5.1f} {p['bearing']:4d} "
              f"{p['cells']:6d}  {sw / n:+6.2f} {sg / n:+6.2f} {sr / n:+6.1f} "
              f"{st / n:+5.1f}  {sm / n:+6.2f}       {sd / n:6.1f}")


if __name__ == "__main__":
    main()
