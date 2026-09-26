"""Why does arming escape_road_snap_m break the pinned Camp-Fire-scale
extended run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_
overrun_ever)? Re-runs that exact scenario with the snap OFF (0.0) and ON
(1500 m) and prints every crew-overrun / audit row: tick, crew, reason,
mode, status, minutes inside the eroded-P10 band, minutes since the nominal
front, plus each arm's escape / relocation notes around the event.
Usage: py -3 scratch_review/snap_campfire_diag.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse import geo, simui  # noqa: E402


def run(snap_m):
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(
        key, 20, 2, seed="alerted|paradise_ca",
        cfg_overrides={"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
                       "deploy_min": 15.0, "escape_road_snap_m": snap_m},
        fire_zone="extended")
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    rows = []
    notes_seen = []
    served = None
    for t in range(120):
        st = s.tick()
        now = st["sim_now_s"]
        for n in st.get("notes", []):
            if ("escape" in n or "relocat" in n) and n not in notes_seen:
                notes_seen.append(n)
                if 60 <= t <= 75:
                    print(f"  [snap {snap_m:.0f}] t{t} note: {n[:220]}")
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
    return rows, served, st["scoreboard"].get("relocations")


for snap in (0.0, 1500.0):
    rows, served, reloc = run(snap)
    print(f"\n=== escape_road_snap_m={snap:.0f}: {len(rows)} overrun/audit rows, "
          f"served {served}, relocations {reloc}")
    for r in rows[:20]:
        print("  tick %s crew %s reason %s mode %s status %s  min-past-P10 %s  "
              "min-since-front %s  at %s,%s" % r)
