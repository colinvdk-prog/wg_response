"""Why did arming escape_road_snap_m break the pinned Camp-Fire-scale
extended run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_
overrun_ever), and does the road-network escape that replaced it (contracts
escape_road_network, the simulator default since 2026-09-26) keep it clean?
Re-runs that exact scenario in three arms and prints every crew-overrun /
audit row: tick, crew, reason, mode, status, minutes inside the eroded-P10
band, minutes since the nominal front, plus served and relocations:

  rays     escape_road_network=False, escape_road_snap_m=0     (the old default)
  snap     escape_road_network=False, escape_road_snap_m=1500  (the 2026-09-25 A/B)
  network  the simulator defaults (road-network escape armed)

The per-decision dissection lives in scratch_review/escape_roads_diag.py.
Usage: py -3 scratch_review/snap_campfire_diag.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse import geo, simui  # noqa: E402

ARMS = (
    ("rays", {"escape_road_network": False, "escape_road_snap_m": 0.0}),
    ("snap", {"escape_road_network": False, "escape_road_snap_m": 1500.0}),
    ("network", {}),
)


def run(extra):
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    cfg = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
           "deploy_min": 15.0}
    cfg.update(extra)
    s = simui.SimSession(key, 20, 2, seed="alerted|paradise_ca",
                         cfg_overrides=cfg, fire_zone="extended")
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    rows = []
    served = None
    for t in range(120):
        st = s.tick()
        now = st["sim_now_s"]
        for o in st.get("crew_overruns", []):
            p10 = o.get("eroded_p10_s")
            nom = o.get("nominal_arrival_s")
            rows.append((t, o["crew_id"], o["reason"], o["mode"], o["status"],
                         None if p10 is None else round((now - p10) / 60.0, 1),
                         None if nom is None else round((now - nom) / 60.0, 1),
                         o["lat"], o["lon"]))
        if st["scoreboard"]["violations"] and not st.get("crew_overruns"):
            rows.append((t, "-", "departure-audit", "-", "-", None, None, None, None))
        served = st["scoreboard"].get("served")
    return rows, served, st["scoreboard"].get("relocations"), s._cfg


for name, extra in ARMS:
    rows, served, reloc, cfg = run(extra)
    print(f"\n=== {name} (escape_road_network={cfg.escape_road_network}, "
          f"escape_road_snap_m={cfg.escape_road_snap_m:g}, horizon cap "
          f"{cfg.escape_road_horizon_cap_h:g} h): {len(rows)} overrun/audit rows, "
          f"served {served}, relocations {reloc}")
    for r in rows[:20]:
        print("  tick %s crew %s reason %s mode %s status %s  min-past-P10 %s  "
              "min-since-front %s  at %s,%s" % r)
