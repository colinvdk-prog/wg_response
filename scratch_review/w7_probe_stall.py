import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig, WeatherForecast, WeatherSample
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

for code in (102, 183):
    fm = FUEL_TABLE[code]
    print(f"code={code} mx_dead={fm.mx_dead_frac}")
    for rh in (20, 60, 80, 90, 95, 100):
        print(f"   rh={rh}: m_dead={dead_fuel_moisture_frac(15.0, rh):.4f}")

def cfg(**kw):
    kw.setdefault("ensemble_n", 1); return ResponseConfig(**kw)
def mins(a,r,c):
    v=a.get(r,c); return math.inf if math.isinf(v) else (v-T0)/60.0

# hour 1..3 soaked (RH 100, cool) => ros 0; hours 0 and 4+ dry
def soak_wx(loc, dead_hours, wind=3.0, hours=200):
    s=[]
    for i in range(hours):
        wet = i in dead_hours
        s.append(WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=wind,
                               wind_gust_mps=wind*1.3, wind_dir_deg=270.0,
                               rh_pct=100.0 if wet else 20.0,
                               temp_c=5.0 if wet else 30.0))
    return WeatherForecast(loc=loc, samples=s, source="test")

tile = grass_tile(fuel_code=102.0); ign = center_ignition(tile)
for dead in ((1,2,3), (1,), (1,2)):
    wx = soak_wx(ign, set(dead))
    off = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0))
    on  = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0, edge_weather_piecewise=True))
    print(f"dead hours {dead}: east c=22 off={mins(off,20,22):8.2f} on={mins(on,20,22):8.2f} "
          f"| c=25 off={mins(off,20,25):8.2f} on={mins(on,20,25):8.2f} "
          f"| c=30 off={mins(off,20,30):8.2f} on={mins(on,20,30):8.2f}")
