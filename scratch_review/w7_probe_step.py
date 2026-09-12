import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig, WeatherForecast, WeatherSample

def cfg(**kw):
    kw.setdefault("ensemble_n", 1); return ResponseConfig(**kw)

def step_wx(loc, lo, hi, step_hour=2, hours=200):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=(lo if i < step_hour else hi),
                      wind_gust_mps=(lo if i < step_hour else hi)*1.3,
                      wind_dir_deg=270.0, rh_pct=20.0, temp_c=30.0)
        for i in range(hours)])

def mins(arr, r, c):
    v = arr.get(r, c)
    return math.inf if math.isinf(v) else (v - T0)/60.0

tile = grass_tile(); ign = center_ignition(tile)
for base, after, label in ((4.0, 12.0, "UP 4->12"), (4.0, 1.5, "DOWN 4->1.5"),
                           (4.0, 2.0, "DOWN 4->2"), (3.0, 9.0, "UP 3->9"),
                           (3.0, 1.0, "DOWN 3->1")):
    wx = step_wx(ign, base, after)
    off = spread.compute_arrival(tile, [ign], wx, T0, cfg())
    on  = spread.compute_arrival(tile, [ign], wx, T0, cfg(edge_weather_piecewise=True))
    row = []
    for c in (24, 28, 32, 36, 40):
        row.append(f"c{c}: {mins(off,20,c):8.2f}->{mins(on,20,c):8.2f} ({mins(on,20,c)-mins(off,20,c):+7.2f})")
    print(f"{label:12s} " + " | ".join(row))
