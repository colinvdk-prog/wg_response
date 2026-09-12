"""w7 probe: measure the two knobs' effect sizes through compute_arrival."""
import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, const_wx, center_ignition, T0
from wgresponse import geo, spread
from wgresponse.contracts import (GridSpec, LatLon, ResponseConfig, TerrainTile,
                                  WeatherForecast, WeatherSample)

def cfg(**kw):
    kw.setdefault("ensemble_n", 1)
    return ResponseConfig(**kw)

def step_wx(loc, lo, hi, step_hour=2, hours=120):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=(lo if i < step_hour else hi),
                      wind_gust_mps=(lo if i < step_hour else hi)*1.3,
                      wind_dir_deg=270.0, rh_pct=20.0, temp_c=30.0)
        for i in range(hours)])

def mins(arr, r, c):
    v = arr.get(r, c)
    return math.inf if math.isinf(v) else (v - T0)/60.0

print("=== PIECEWISE: step UP (4 -> 12 m/s at T0+2h), GR2 flat 41x41 @100m ===")
tile = grass_tile(); ign = center_ignition(tile)
wx = step_wx(ign, 4.0, 12.0)
off = spread.compute_arrival(tile, [ign], wx, T0, cfg())
on  = spread.compute_arrival(tile, [ign], wx, T0, cfg(edge_weather_piecewise=True))
for c in (25, 30, 35, 40):
    print(f"  (20,{c}) east  off={mins(off,20,c):9.2f}  on={mins(on,20,c):9.2f}  d={mins(on,20,c)-mins(off,20,c):+8.2f} min")
for r in (15, 10, 5, 0):
    print(f"  ({r},20) north off={mins(off,r,20):9.2f}  on={mins(on,r,20):9.2f}  d={mins(on,r,20)-mins(off,r,20):+8.2f} min")

print("=== PIECEWISE: step DOWN (12 -> 4 m/s at T0+2h) ===")
wx = step_wx(ign, 12.0, 4.0)
off = spread.compute_arrival(tile, [ign], wx, T0, cfg())
on  = spread.compute_arrival(tile, [ign], wx, T0, cfg(edge_weather_piecewise=True))
for c in (25, 30, 35, 40):
    print(f"  (20,{c}) east  off={mins(off,20,c):9.2f}  on={mins(on,20,c):9.2f}  d={mins(on,20,c)-mins(off,20,c):+8.2f} min")
for r in (15, 10, 5, 0):
    print(f"  ({r},20) north off={mins(off,r,20):9.2f}  on={mins(on,r,20):9.2f}  d={mins(on,r,20)-mins(off,r,20):+8.2f} min")

print("=== PIECEWISE: constant weather byte-identity ===")
wxc = const_wx(ign, wind_mps=6.0)
a = spread.compute_arrival(tile, [ign], wxc, T0, cfg())
b = spread.compute_arrival(tile, [ign], wxc, T0, cfg(edge_weather_piecewise=True))
print("  identical:", a.data == b.data, " mismatches:", sum(1 for x,y in zip(a.data,b.data) if x!=y))
# slow fuel (TL3 183) low wind => long edges spanning many hours
tl = grass_tile(fuel_code=183.0); itl = center_ignition(tl)
wtl = const_wx(itl, wind_mps=3.0, hours=300)
a = spread.compute_arrival(tl, [itl], wtl, T0, cfg(horizon_h=240.0))
b = spread.compute_arrival(tl, [itl], wtl, T0, cfg(horizon_h=240.0, edge_weather_piecewise=True))
fin = sum(1 for v in a.data if math.isfinite(v))
print(f"  TL3 const (finite={fin}): identical: {a.data == b.data}  mismatches: {sum(1 for x,y in zip(a.data,b.data) if x!=y)}")
print(f"  TL3 one edge spans hours: reach east (20,21) = {mins(a,20,21):.1f} min")
