"""BUILD lane 1, item 2 (fuse_hotspots_keep_point, SAFETY SEMANTICS 50):
lane B's B2 geometry on the build branch. A point-only agency record (no
size), two night refinery-type pixels 2 km NORTH (crosswind) inside its
attach footprint, a home 1.5 km EAST (downwind); stub west wind 8 m/s,
uniform GR2, the live verification flags. ETA p10/p50 with the knob off
(the live law: the heat replaces the point) and on (point + heat).

Run from the build worktree root:  python <this file>"""
import dataclasses
import os
import sys

WT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "tests"))

from test_threat import (NOW, ORIGIN, StubFeed, StubRegistry,  # noqa: E402
                         make_monitor)
from wgresponse import geo  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (ClientProperty, FireIncident,  # noqa: E402
                                  IncidentSource, LatLon, ResponseConfig,
                                  validate_config)


def live_cfg(**kw):
    c = ResponseConfig(
        ensemble_n=2, horizon_h=24.0, grid_cell_m=150.0, grid_max_cells=20000,
        wind_gust_blend=0.5, grass_ros_floor_frac=0.10,
        ros_floor_extinction_gate=True, extreme_regime_enabled=True,
        corridor_fail_closed=True, corridor_unreached_bound=True,
        drop_contained_incidents=True, perimeter_confirmed_anchor=True,
        fire_belief_track=True, fire_belief_learn=True, feed_sanity=True,
        forecast_tiers=True, fuse_hotspots=True, hotspot_heat_only=True,
        forecast_domain_snap_deg=0.05, screen_labels=True)
    c = dataclasses.replace(c, **kw)
    assert not validate_config(c)
    return c


def heat_cluster(pts, t):
    return FireIncident(
        "firms:x", "FIRMS cluster", IncidentSource.FIRMS,
        LatLon(sum(p.lat for p in pts) / len(pts),
               sum(p.lon for p in pts) / len(pts)), t, hotspots=list(pts),
        source_ids={"firms": "firms:x"},
        meta={"n_detections": len(pts), "hotspot_t_s": [t] * len(pts),
              "max_frp": 3.0})


def run(geometry, keep):
    home, pts = geometry
    cfg = live_cfg(fuse_hotspots_keep_point=keep)
    fire = FireIncident("lac-new", "LAC-500002", IncidentSource.WFIGS, ORIGIN,
                        NOW - 300.0, irwin_id="lac-new",
                        source_ids={"wfigs": "lac-new"})
    feed = StubFeed([fire, heat_cluster(pts, NOW - 4 * 3600.0)])
    mon = make_monitor(feeds=[feed], cfg=cfg, registry=StubRegistry([home]),
                       alert_engine=AlertEngine(cfg))
    r = mon.cycle(NOW)
    a = [x for x in r.assessments if x.incident_id == "lac-new"]
    return ([(round((x.eta.eta_p10_s - NOW) / 3600.0, 2),
              round((x.eta.eta_p50_s - NOW) / 3600.0, 2)) for x in a],
            [e.state.value for e in r.events])


GEOMETRIES = {
    "B2 (heat 2 km crosswind north, home 1.5 km downwind)": (
        ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 1500.0)),
        [geo.destination(ORIGIN, 0.0, 2000.0),
         geo.destination(geo.destination(ORIGIN, 0.0, 2000.0), 90.0, 375.0)]),
    "heat 1 km north-west (upwind), home 3 km downwind": (
        ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 3000.0)),
        [geo.destination(ORIGIN, 315.0, 1000.0)]),
    "heat 1.5 km downwind, home 4 km downwind (heat ahead of the point)": (
        ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 4000.0)),
        [geo.destination(ORIGIN, 90.0, 1500.0)]),
}

if __name__ == "__main__":
    for name, g in GEOMETRIES.items():
        off = run(g, False)
        on = run(g, True)
        print(f"{name}\n  off (live law):   p10/p50 h {off[0]}  events "
              f"{off[1]}\n  on  (point kept): p10/p50 h {on[0]}  events "
              f"{on[1]}")
