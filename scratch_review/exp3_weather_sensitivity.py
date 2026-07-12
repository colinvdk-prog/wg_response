"""EXP 3 — single-point weather: ROS/ETA sensitivity to plausible cross-domain
weather differences (threat.py:199 uses ONE forecast at domain.center()).

Domain can span up to ~120 km (clients within _MAX_THREAT_RADIUS_M=60 km of the
ignition are all folded into one bbox), so the forecast point can sit tens of
km from the fire. Quantify what a coastal-vs-inland difference does to ROS
(ETA scales ~1/ROS on uniform fuel).
"""
from __future__ import annotations

from _common import cfg_default

from wgresponse import rothermel
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

cfg = cfg_default()
cases = [
    # (label, wind10 m/s, rh %, temp C)
    ("marine layer (center)", 3.0, 55.0, 22.0),
    ("inland canyon (fire)",  6.0, 25.0, 32.0),
    ("Santa Ana core",        12.0, 10.0, 35.0),
]
for code, name in ((102, "GR2 grass"), (145, "SH5 chaparral")):
    fuel = FUEL_TABLE[code]
    print(f"\n{name}:")
    base = None
    for label, w10, rh, tc in cases:
        m_dead = dead_fuel_moisture_frac(tc, rh)
        u_mid = rothermel.midflame_wind_mps(w10, fuel)
        res = rothermel.surface_ros_mps(fuel, m_dead, cfg.live_herb_frac,
                                        cfg.live_woody_frac, u_mid, 0.0)
        if base is None:
            base = res.ros_mps
        print(f"  {label:22s} wind10={w10:4.1f} m/s RH={rh:4.0f}% -> "
              f"dead FM={m_dead*100:4.1f}%  ROS={res.ros_mps*3.6:6.3f} km/h  "
              f"(x{res.ros_mps/base:5.2f} vs first; ETA x{base/res.ros_mps:5.2f})")
