"""EXP 5 — burned-area seeding from perimeter RING VERTICES.

(a) Does the model re-burn INSIDE the perimeter? (interior arrival should be
    ~t0/already-burned, not forecast-late)
(b) Client INSIDE the perimeter: sane ETA (IMPACTED-now) or +hours?
(c) Vertex sparsity: 4-corner ring vs densified ring — outside-client ETA.
(d) Containment: containment_pct is parsed by feeds but never reaches
    compute_arrival (no code path) — a 95%-contained fire forecasts like 0%.

Square perimeter 3x3 km on uniform GR2, wind 5 m/s FROM 270 (pushes east).
"""
from __future__ import annotations

import math

from _common import T0, cfg_default, flat_tile, fmt_h, hours

from wgresponse import geo, spread
from wgresponse.contracts import FireIncident, IncidentSource, LatLon
from wgresponse.weather import MockWeather

CELL = 90.0
NROWS, NCOLS = 100, 160                    # ~9 x 14.4 km
NORTH, WEST = 34.56, -119.0
cfg = cfg_default(ensemble_n=6, horizon_h=96.0)
tile = flat_tile(NORTH, WEST, CELL, NROWS, NCOLS, 102.0)
spec = tile.spec

center = geo.rc_to_latlon(spec, NROWS // 2, 45)      # perimeter center ~4 km in
HALF = 1500.0                                        # 3x3 km square


def square_ring(step_m: float) -> list:
    """Vertices of the 3x3 km square centered on `center`, spaced step_m."""
    nw = geo.destination(geo.destination(center, 0.0, HALF), 270.0, HALF)
    pts = []
    side = 2 * HALF
    n = max(1, int(side / step_m))
    for k in range(n):                     # N edge, W->E
        pts.append(geo.destination(nw, 90.0, k * side / n))
    ne = geo.destination(nw, 90.0, side)
    for k in range(n):                     # E edge, N->S
        pts.append(geo.destination(ne, 180.0, k * side / n))
    se = geo.destination(ne, 180.0, side)
    for k in range(n):                     # S edge, E->W
        pts.append(geo.destination(se, 270.0, k * side / n))
    sw = geo.destination(se, 270.0, side)
    for k in range(n):                     # W edge, S->N
        pts.append(geo.destination(sw, 0.0, k * side / n))
    return pts

clients = {
    "center (1.5 km inside)": center,
    "200 m inside E edge":    geo.destination(center, 90.0, HALF - 200.0),
    "outside, 3 km E of edge": geo.destination(center, 90.0, HALF + 3000.0),
    "outside, 8 km E of edge": geo.destination(center, 90.0, HALF + 8000.0),
}

wx = MockWeather("exp5", base_wind_mps=5.0, dir_deg=270.0, rh_pct=25.0,
                 temp_c=30.0, t0_s=T0)
wxf = wx.forecast(center, cfg.horizon_h + 2)

for label, ring in (("densified ring (150 m vertex spacing)", square_ring(150.0)),
                    ("4-corner ring (raw sparse polygon)", square_ring(3000.0))):
    inc = FireIncident(incident_id="rev5", name="rev5", source=IncidentSource.MOCK,
                       origin=center, updated_at_s=T0, perimeter=[ring],
                       containment_pct=95.0)
    ign = inc.ignition_points()
    ens = spread.ensemble_arrival(tile, ign, wxf, T0, cfg)
    print(f"\n{label}: {len(ign)} seed points, containment_pct={inc.containment_pct}")
    for name, loc in clients.items():
        eta = spread.eta_at(ens, loc)
        print(f"  {name:26s} p50 = {fmt_h(hours(eta.eta_p50_s, T0))} h   "
              f"p10 = {fmt_h(hours(eta.eta_p10_s, T0))} h")
    # interior re-burn stats: mean arrival of cells strictly inside the ring
    interior = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            p = geo.rc_to_latlon(spec, r, c)
            de = geo.haversine_m(center, LatLon(center.lat, p.lon))
            dn = geo.haversine_m(center, LatLon(p.lat, center.lon))
            if de < HALF - 200 and dn < HALF - 200:
                v = ens.members[0].get(r, c)
                if not math.isinf(v):
                    interior.append((v - T0) / 3600.0)
    if interior:
        interior.sort()
        print(f"  interior cells (should be ~0 h / already burned): "
              f"median arrival {interior[len(interior)//2]:.2f} h, "
              f"max {interior[-1]:.2f} h over {len(interior)} cells")

# (d) containment: no code path — compute_arrival takes only ignition points.
import inspect
src = inspect.getsource(spread.compute_arrival)
print("\ncontainment_pct mentioned in spread.compute_arrival:",
      "containment" in src)
src_t = inspect.getsource(__import__("wgresponse.threat", fromlist=["x"]))
print("containment_pct mentioned anywhere in threat.py:", "containment" in src_t)
