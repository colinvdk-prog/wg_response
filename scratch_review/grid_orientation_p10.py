"""Grid orientation and the LIVE quantities (2026-10-09; NOT product code):
the ensemble P10 / P50 arrival at homes straight downwind of a point fire on
a flat uniform GR2 tile, legacy vs cfg.spread_any_angle, against the
engine's own analytic arrival (nominal member's ellipse). Default ensemble
(ensemble_n 6: nominal + 5 perturbed members; wind speed, moisture and
direction perturbations; dir_perturb_deg default), the live 96-h horizon
(env P10_HORIZON_H), 100 m cells.

Usage: py -3 scratch_review/grid_orientation_p10.py
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from grid_orientation_common import (MPH, T0, ResponseConfig, const_wx,  # noqa: E402
                                     engine_params, flat_tile, gauge_s)
from wgresponse import geo, spread  # noqa: E402

HORIZON_H = float(os.environ.get("P10_HORIZON_H", "96"))   # live default
# (the members' direction offsets scale with min(1, horizon / 96 h): a short
# horizon would understate the ensemble's direction spread)
base = ResponseConfig(spotting_enabled=False, horizon_h=HORIZON_H)
fix = dataclasses.replace(base, spread_any_angle=True)
n = 281
r0, c0 = 140, 20
rows = []
print(f"dir_perturb_deg {base.dir_perturb_deg}, wind_perturb_frac "
      f"{base.wind_perturb_frac}, ensemble_n {base.ensemble_n}")
for mph in (15, 20, 30):
    for wdir in (270.0, 281.25, 290.0):
        tile = flat_tile(n, n, 100.0, 102)
        ign = geo.rc_to_latlon(tile.spec, r0, c0)
        wx = const_wx(ign, mph * MPH, wdir)
        head, lb, brg = engine_params(102, mph * MPH, wdir, base)
        ens = {name: spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                             incident_id="p10-probe")
               for name, cfg in (("legacy", base), ("fix", fix))}
        for d_km in (3.0, 6.0, 10.0):
            x = d_km * 1000.0 * math.sin(math.radians(brg))
            y = d_km * 1000.0 * math.cos(math.radians(brg))
            rr = r0 - round(y / 100.0)
            cc = c0 + round(x / 100.0)
            if not (0 <= rr < n and 0 <= cc < n):
                continue
            i = rr * n + cc
            ana_h = gauge_s((cc - c0) * 100.0, (r0 - rr) * 100.0, head, lb,
                            brg) / 3600.0
            row = {"mph": mph, "wdir": wdir, "d_km": d_km, "lb": lb,
                   "analytic_h": ana_h}
            for name, e in ens.items():
                row[name] = {q: (getattr(e, q).data[i] - T0) / 3600.0
                             for q in ("p10", "p50", "p90")}
                row[name]["nominal"] = (e.members[0].data[i] - T0) / 3600.0
            rows.append(row)
            L, F = row["legacy"], row["fix"]
            print(f"{mph:2d} mph from {wdir:6.2f} (lb {lb:.2f}) home {d_km:4.1f} km "
                  f"downwind | analytic {ana_h:5.2f} h | legacy P10 {L['p10']:5.2f} "
                  f"P50 {L['p50']:5.2f} nominal {L['nominal']:5.2f} | fix P10 "
                  f"{F['p10']:5.2f} P50 {F['p50']:5.2f} nominal {F['nominal']:5.2f}",
                  flush=True)
with open(os.path.join(HERE, f"grid_orientation_p10_h{HORIZON_H:g}.json"),
          "w") as fh:
    json.dump(rows, fh, indent=1)
