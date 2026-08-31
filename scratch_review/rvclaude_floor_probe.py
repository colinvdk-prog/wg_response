import sys, dataclasses, inspect
sys.path.insert(0, r'C:\Users\colin\wg_response')
from wgresponse import rothermel, fuels, spread
from wgresponse.contracts import ResponseConfig
cfg_on = dataclasses.replace(ResponseConfig(), grass_ros_floor_frac=0.10)
print("midflame_wind_mps sig:", inspect.signature(rothermel.midflame_wind_mps))
U10 = 10.0
NAMES = {102:'GR2 low-load dry grass',122:'GS2 mod-load grass-shrub',165:'TU5 very-high-load timber-understory',183:'TL3 moderate-load conifer litter',104:'GR4 mod-load dry grass'}
for code in (102,104,122,165,183):
    fm = fuels.fuel_model(code) if hasattr(fuels,'fuel_model') else None
    if fm is None:
        fm = spread._fuel_for_code(code)
    print(f"\n=== FBFM {code}  {NAMES.get(code,'')}  |  U10={U10} m/s ({U10*2.237:.0f} mph) ===")
    print(f"  mx_dead (moisture of extinction) = {getattr(fm,'mx_dead_frac',getattr(fm,'mx_dead',None))}")
    mid = rothermel.midflame_wind_mps(U10, fm) if len(inspect.signature(rothermel.midflame_wind_mps).parameters)==2 else rothermel.midflame_wind_mps(U10)
    ff = spread._ros_floor_frac(code, cfg_on); floor = ff*U10
    print(f"  midflame wind = {mid:.3f} m/s ;  floor ratio={ff:.3f} -> floor ROS={floor:.4f} m/s = {floor*3.6:.3f} km/h")
    print(f"  {'m_dead%':>8} {'rothROS m/s':>12} {'rothROS km/h':>13} {'floored km/h':>13} {'x-inflation':>12} {'I kW/m':>10} {'flameL m':>9}")
    for m in (0.03,0.06,0.10,0.15,0.20,0.25,0.30,0.40,0.60):
        res = rothermel.surface_ros_mps(fm, m, 0.5, 0.9, mid, 0.0)
        fl = max(res.ros_mps, floor)
        inf = (fl/res.ros_mps) if res.ros_mps>1e-12 else float('inf')
        L = 0.0775*(res.intensity_kwm**0.46) if res.intensity_kwm>0 else 0.0
        print(f"  {m*100:8.0f} {res.ros_mps:12.6f} {res.ros_mps*3.6:13.5f} {fl*3.6:13.5f} {inf:12.1f} {res.intensity_kwm:10.2f} {L:9.3f}")
