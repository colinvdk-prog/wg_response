import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, const_wx, center_ignition, T0
from wgresponse import rothermel, spread
from wgresponse.contracts import ResponseConfig
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

def cfg(**kw):
    kw.setdefault("ensemble_n", 1); return ResponseConfig(**kw)
def mins(a,r,c):
    v=a.get(r,c); return math.inf if math.isinf(v) else (v-T0)/60.0

print("=== ALIGNED, mild slopes / strong wind (GR2) ===")
for slope, wind in ((3.0,12.0),(5.0,12.0),(5.0,14.0),(8.0,12.0),(5.0,10.0),(10.0,14.0)):
    tile = grass_tile(slope_deg=slope, aspect_deg=270.0); ign=center_ignition(tile)
    wx = const_wx(ign, wind_mps=wind, wind_dir_deg=270.0, hours=300)
    off = spread.compute_arrival(tile,[ign],wx,T0,cfg())
    on  = spread.compute_arrival(tile,[ign],wx,T0,cfg(slope_phi_vector=True))
    for c in (30,35):
        a,b = mins(off,20,c), mins(on,20,c)
        print(f"  slope={slope:4.1f} wind={wind:4.1f} c={c}: off={a:7.2f} on={b:7.2f} on/off={b/a:.4f} ({(b/a-1)*100:+.2f}%)")

print()
print("=== CROSS bearing, analytic (GR2, m_dead from rh=20/temp=30) ===")
fuel = FUEL_TABLE[102]
m_dead = max(0.01, dead_fuel_moisture_frac(30.0, 20.0))
bed = rothermel.fuel_bed(fuel, m_dead, 0.30, 0.60)
for slope, wind in ((30.0,3.0),(25.0,3.0),(30.0,2.0)):
    u = rothermel.midflame_wind_mps(wind, fuel)
    us = rothermel.effective_wind_from_bed(bed, 0.0, slope)
    pw = rothermel._phi_wind(bed, u*rothermel._MPS_TO_FTMIN)
    ps = rothermel._phi_slope(bed, slope)
    # wind toward 090 (east), upslope 000 (north)
    brg_off = math.degrees(math.atan2(u, us)) % 360.0
    brg_on  = math.degrees(math.atan2(pw, ps)) % 360.0
    print(f"  slope={slope} wind={wind}: u_mid={u:.3f} u_slope={us:.3f} phi_w={pw:.2f} phi_s={ps:.2f}"
          f"  brg_off={brg_off:.2f} brg_on={brg_on:.2f} (upslope=0)  rotation toward upslope = {brg_off-brg_on:+.2f} deg")
