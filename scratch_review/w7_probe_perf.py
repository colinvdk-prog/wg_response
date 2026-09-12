import math, sys, time
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, const_wx, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig
def cfg(**kw):
    kw.setdefault("ensemble_n",1); return ResponseConfig(**kw)
for code, wind, label in ((102.0, 8.0, "GR2 8m/s"), (183.0, 3.0, "TL3 3m/s")):
    tile = grass_tile(nrows=81, ncols=81, fuel_code=code); ign=center_ignition(tile)
    wx = const_wx(ign, wind_mps=wind, hours=300)
    for knobs in ({}, {"edge_weather_piecewise":True}, {"slope_phi_vector":True},
                  {"edge_weather_piecewise":True,"slope_phi_vector":True}):
        t=time.perf_counter()
        a=spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0,**knobs))
        dt=time.perf_counter()-t
        fin=sum(1 for v in a.data if math.isfinite(v))
        print(f"{label:10s} {str(knobs):62s} {dt*1000:8.1f} ms  finite={fin}")

print()
print("--- realistic: veering/varying forecast, default 96 h horizon ---")
from wgresponse.contracts import WeatherForecast, WeatherSample
def veer_wx(loc, hours=200):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=4.0+float(i%9),
                      wind_gust_mps=(4.0+float(i%9))*1.3,
                      wind_dir_deg=(250.0+10.0*(i%10))%360.0,
                      rh_pct=15.0+3.0*(i%7), temp_c=22.0+2.5*(i%5))
        for i in range(hours)])
for code, label in ((102.0,"GR2"), (183.0,"TL3")):
    tile = grass_tile(nrows=81, ncols=81, fuel_code=code); ign=center_ignition(tile)
    wx = veer_wx(ign)
    for knobs in ({}, {"edge_weather_piecewise":True}):
        t=time.perf_counter()
        a=spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=96.0,**knobs))
        dt=time.perf_counter()-t
        fin=sum(1 for v in a.data if math.isfinite(v))
        print(f"{label:5s} {str(knobs):40s} {dt*1000:8.1f} ms  finite={fin}")
