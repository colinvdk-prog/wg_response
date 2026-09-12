"""Independent arithmetic check of the piecewise integrator, raster-only:
r0 and r1 are recovered from two CONSTANT-weather runs, then the two-rate
arrival is predicted by hand and compared with the knob's answer."""
import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, const_wx, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig, WeatherForecast, WeatherSample

def cfg(**kw):
    kw.setdefault("ensemble_n",1); return ResponseConfig(**kw)

tile = grass_tile(); ign = center_ignition(tile)
D = 100.0          # rook edge, 100 m cells
W0, W1 = 3.0, 7.0
c0 = spread.compute_arrival(tile,[ign],const_wx(ign,wind_mps=W0,hours=300),T0,cfg(horizon_h=240.0))
c1 = spread.compute_arrival(tile,[ign],const_wx(ign,wind_mps=W1,hours=300),T0,cfg(horizon_h=240.0))
t0_w = c0.get(20,19) - T0
t1_w = c1.get(20,19) - T0
r0, r1 = D/t0_w, D/t1_w
print(f"west neighbour: const {W0} m/s -> {t0_w:.4f}s (r0={r0:.6f} m/s); const {W1} m/s -> {t1_w:.4f}s (r1={r1:.6f})")
assert t0_w > 3600.0, "edge must span the first hour boundary"
wx = WeatherForecast(loc=ign, source="test", samples=[
    WeatherSample(t_s=T0+i*3600.0, wind_speed_mps=(W0 if i<1 else W1),
                  wind_gust_mps=(W0 if i<1 else W1)*1.3, wind_dir_deg=270.0,
                  rh_pct=20.0, temp_c=30.0) for i in range(300)])
off = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0))
on  = spread.compute_arrival(tile,[ign],wx,T0,cfg(horizon_h=240.0, edge_weather_piecewise=True))
pred = 3600.0 + (D - r0*3600.0)/r1
print(f"OFF west = {off.get(20,19)-T0:.4f}s (== frozen {t0_w:.4f})")
print(f"ON  west = {on.get(20,19)-T0:.4f}s   predicted {pred:.4f}s   err={on.get(20,19)-T0-pred:+.3e}s")
