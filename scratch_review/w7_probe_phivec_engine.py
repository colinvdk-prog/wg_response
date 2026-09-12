import math, sys
sys.path.insert(0, '.')
from tests.test_spread import grass_tile, const_wx, center_ignition, T0
from wgresponse import spread
from wgresponse.contracts import ResponseConfig

def cfg(**kw):
    kw.setdefault("ensemble_n", 1); return ResponseConfig(**kw)

def mins(arr, r, c):
    v = arr.get(r, c)
    return math.inf if math.isinf(v) else (v - T0)/60.0

def run(slope, aspect, wind, code=102.0):
    tile = grass_tile(slope_deg=slope, aspect_deg=aspect, fuel_code=code)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=wind, wind_dir_deg=270.0, hours=300)
    off = spread.compute_arrival(tile, [ign], wx, T0, cfg(horizon_h=240.0))
    on  = spread.compute_arrival(tile, [ign], wx, T0, cfg(horizon_h=240.0, slope_phi_vector=True))
    return off, on

print("=== FLAT: ON vs OFF byte identity ===")
for w in (0.0, 5.0, 12.0):
    off, on = run(0.0, 0.0, w)
    print(f"  wind={w}: identical={off.data == on.data}")
off, on = run(0.0, 137.0, 7.0)   # aspect irrelevant on flat
print(f"  flat, aspect=137, wind=7: identical={off.data == on.data}")

print("=== ALIGNED (wind toward 090, upslope 090 => aspect 270) ===")
for slope, wind in ((15.0,10.0),(25.0,10.0),(30.0,5.0),(25.0,3.0),(10.0,12.0),(15.0,12.0)):
    off, on = run(slope, 270.0, wind)
    c = 35
    a, b = mins(off,20,c), mins(on,20,c)
    print(f"  slope={slope:4.1f} wind={wind:4.1f}  east(20,{c}) off={a:8.2f} on={b:8.2f}  on/off={b/a:.4f}  ({(b/a-1)*100:+.1f}%)")

print("=== OPPOSED (wind toward 090, upslope 270 => aspect 090) ===")
for slope, wind in ((25.0,5.0),(25.0,3.0),(25.0,10.0),(30.0,5.0),(20.0,5.0)):
    off, on = run(slope, 90.0, wind)
    for c in (25, 30, 35):
        a, b = mins(off,20,c), mins(on,20,c)
        r = (a/b) if (math.isfinite(a) and math.isfinite(b) and b>0) else float('nan')
        print(f"  slope={slope:4.1f} wind={wind:4.1f}  east(20,{c}) off={a:9.2f} on={b:9.2f}  speedup off/on={r:.3f}")

print("=== CROSS (wind toward 090, upslope 000 => aspect 180) ===")
for slope, wind in ((30.0,3.0),(30.0,2.0),(25.0,3.0),(35.0,3.0),(30.0,5.0)):
    off, on = run(slope, 180.0, wind)
    e_off, e_on = mins(off,20,32), mins(on,20,32)
    n_off, n_on = mins(off,8,20), mins(on,8,20)
    print(f"  slope={slope:4.1f} wind={wind:4.1f}  east off={e_off:8.2f} on={e_on:8.2f} | north off={n_off:8.2f} on={n_on:8.2f} | ratio N/E off={n_off/e_off:.4f} on={n_on/e_on:.4f}")
