"""w7 probe: phi-vector vs speed-space combination magnitudes (no engine edits)."""
import math, sys
sys.path.insert(0, '.')
from wgresponse import rothermel
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

M2F = rothermel._MPS_TO_FTMIN

def probe(code, wind10, slope, rh=20.0, temp=30.0, herb=0.30, woody=0.60):
    fuel = FUEL_TABLE[code]
    m_dead = max(0.01, dead_fuel_moisture_frac(temp, rh))
    bed = rothermel.fuel_bed(fuel, m_dead, herb, woody)
    u = rothermel.midflame_wind_mps(wind10, fuel)
    phi_w = rothermel._phi_wind(bed, u * M2F)
    phi_s = rothermel._phi_slope(bed, slope)
    us = rothermel.effective_wind_from_bed(bed, 0.0, slope)   # slope-equiv wind m/s
    out = {}
    for name, ang in (("aligned", 0.0), ("opposed", 180.0), ("cross", 90.0)):
        # OFF: speed-space
        vx = u*math.sin(0.0) + us*math.sin(math.radians(ang))
        vy = u*math.cos(0.0) + us*math.cos(math.radians(ang))
        u_eff_off = math.hypot(vx, vy)
        brg_off = math.degrees(math.atan2(vx, vy)) % 360.0
        res_off = rothermel.surface_ros_from_bed(bed, u_eff_off, 0.0)
        # ON: phi-space
        px = phi_w*math.sin(0.0) + phi_s*math.sin(math.radians(ang))
        py = phi_w*math.cos(0.0) + phi_s*math.cos(math.radians(ang))
        phi_e = math.hypot(px, py)
        brg_on = math.degrees(math.atan2(px, py)) % 360.0
        r_on = bed.r0_ftmin*(1.0+phi_e)*rothermel._FTMIN_TO_MPS
        uw_on = rothermel._wind_from_phi(bed, phi_e)*rothermel._FTMIN_TO_MPS
        out[name] = (res_off.ros_mps, r_on, brg_off, brg_on,
                     res_off.effective_wind_mps, uw_on)
    return u, us, phi_w, phi_s, out

for code in (102, 145, 183):
    for wind10 in (3.0, 5.0, 10.0):
        for slope in (15.0, 25.0, 30.0):
            u, us, pw, ps, out = probe(code, wind10, slope)
            print(f"code={code} U10={wind10} slope={slope}: u_mid={u:.3f} u_slope_eq={us:.3f} phi_w={pw:.3f} phi_s={ps:.3f}")
            for name, (ro, rn, bo, bn, eo, en) in out.items():
                rel = (rn/ro - 1.0)*100.0 if ro > 0 else float('nan')
                print(f"    {name:8s} ros_off={ro:.5f} ros_on={rn:.5f} ON/OFF={rn/ro:.3f} ({rel:+.1f}%) brg_off={bo:.1f} brg_on={bn:.1f} ueff_off={eo:.2f} ueff_on={en:.2f}")
