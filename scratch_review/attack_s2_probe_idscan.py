"""Probe: scan incident-ids for an LHS pairing where the NOMINAL member is the
strictly-earliest arrival along some bearing (=> Hazen p10 > nominal there).

Analytic screen on uniform flat GR2: along a ray at bearing theta from the
ignition, arrival ~ dist / directional_ros(theta). Member k beats nominal on
the ray iff ros_k(theta) > ros_0(theta). If for some theta EVERY member is
slower, nominal is the strict min and p10 = nom + 0.1*(s1 - nom) > nom.

Uses the engine's own rothermel + fuels functions, so the screen is faithful.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import rothermel, spread
from wgresponse.fuels import FUEL_TABLE, dead_fuel_moisture_frac

WIND10 = 6.0
WDIR = 270.0          # blows FROM 270 -> head toward 90
TEMP, RH = 30.0, 25.0
FUEL = FUEL_TABLE[102]
CFG_WPF, CFG_MPF, CFG_DIR = 0.25, 0.20, 30.0   # ResponseConfig defaults
DIR_LEAD = 1.0                                  # horizon 96 h -> saturated


def member_ros(theta_deg, wind_scale, moist_scale, dir_off):
    """Directional ROS along compass bearing theta for a perturbed member."""
    w10 = max(0.0, WIND10 * wind_scale)
    m_dead = max(0.01, dead_fuel_moisture_frac(TEMP, RH) * moist_scale)
    u_mid = rothermel.midflame_wind_mps(w10, FUEL)
    res = rothermel.surface_ros_mps(FUEL, m_dead, 0.30, 0.60, u_mid, 0.0)
    lb = rothermel.ellipse_lb(res.effective_wind_mps)
    head_brg = (WDIR + dir_off + 180.0) % 360.0
    return rothermel.directional_ros_mps(res.ros_mps, lb, theta_deg - head_brg)


def scan(n_ids=4000):
    hits = []
    thetas = [k * 2.0 for k in range(180)]           # 2-deg bearing sweep
    nom = {t: member_ros(t, 1.0, 1.0, 0.0) for t in thetas}
    for i in range(n_ids):
        iid = f"att-{i}"
        draws = spread._stratified_member_draws(iid, 5)
        worst_ratio, worst_theta = math.inf, None
        for t in thetas:
            if nom[t] <= 1e-9:
                continue
            best = max(
                member_ros(t, 1.0 + uw * CFG_WPF * 0.5,
                           1.0 + um * CFG_MPF * 0.5,
                           ud * CFG_DIR * DIR_LEAD) / nom[t]
                for (uw, um, ud) in draws)
            if best < worst_ratio:
                worst_ratio, worst_theta = best, t
        if worst_ratio < 1.0:
            hits.append((worst_ratio, worst_theta, iid,
                         spread._stratified_member_draws(iid, 5)))
    hits.sort()
    print(f"scanned {n_ids} ids; {len(hits)} with nominal strictly earliest "
          f"along some bearing")
    for h in hits[:10]:
        print(f"  {h[2]}: best member/nominal ratio {h[0]:.4f} at "
              f"theta={h[1]:.0f} deg; draws={h[3]}")
    return hits


if __name__ == "__main__":
    scan()
