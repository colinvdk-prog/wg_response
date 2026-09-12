import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig, WeatherForecast, WeatherSample

def cfg(**kw):
    kw.setdefault("ensemble_n", 1); return ResponseConfig(**kw)
def mins(a,r,c):
    v=a.get(r,c); return math.inf if math.isinf(v) else (v-T0)/60.0

def soak_wx(loc, dead_hours, wind=3.0, hours=200):
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=wind, wind_gust_mps=wind*1.3,
                      wind_dir_deg=270.0,
                      rh_pct=100.0 if i in dead_hours else 20.0,
                      temp_c=5.0 if i in dead_hours else 30.0)
        for i in range(hours)])

tile = grass_tile(fuel_code=102.0); ign = center_ignition(tile)
dead = {1,2,3}
wx = soak_wx(ign, dead)
off = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0))
on  = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0, edge_weather_piecewise=True))
print("first ring around the seed (row 20 / col 20):")
for (r,c,label) in ((20,21,'E'),(20,19,'W'),(19,20,'N'),(21,20,'S'),
                    (19,21,'NE'),(21,19,'SW'),(18,21,'knight NNE'),(20,18,'W2'),(20,22,'E2')):
    print(f"  {label:11s} ({r},{c})  off={mins(off,r,c):9.2f}  on={mins(on,r,c):9.2f}")
# dry control (no dead hours) for reference
wx2 = soak_wx(ign, set())
ctl = spread.compute_arrival(tile,[ign],wx2,T0,cfg(horizon_h=240.0))
print("dry control W (20,19) =", f"{mins(ctl,20,19):.2f}", " E (20,21) =", f"{mins(ctl,20,21):.2f}")
