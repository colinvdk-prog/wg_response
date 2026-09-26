"""scratch_review/crew_placement_path_probe.py — how far into the fire's
path can a crew be PLACED on the real eagle_vail_co tile and still get out
with zero crew overruns? (CREW PLACEMENT, docs/SIMULATOR_DESIGN.md.)

Through the SERVER path (SimSessionManager: the page's defaults — spotting,
grass floor, plan-on-roads, 24 h coating hold), 40 homes, 4 in-zone crews,
seed placement-demo. Per run: crew sim-g4 is placed (POST /crew) on the road
node nearest a point D km DOWNWIND (bearing 060) of the ignition, then a HIGH
fire is lit WSW of Edwards with the hazard wind (FROM 240) and 8 ticks run.
Prints the first relocation, every crew-overrun row, the departure
violations and how far the crew ended from its placed start.

Measured 2026-09-26: D <= ~2 km -> a one-tick crew-overrun report at tick 1
(reason 'imminent', the sweep already turning it out), 0 departure
violations, the crew 16-17 km away by tick 8; D >= ~3.5 km -> no overrun,
dispatch takes the crew out of the path (0 relocations needed).

    py -3 scratch_review/crew_placement_path_probe.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo                          # noqa: E402
from wgresponse.contracts import LatLon             # noqa: E402
from wgresponse.server import SimSessionManager    # noqa: E402

IGN = LatLon(39.62701, -106.64906)                  # TU1 timber WSW of Edwards


def main() -> int:
    for d_m in (1000, 2000, 3000, 4000, 5000, 6000):
        mgr = SimSessionManager()
        code, out = mgr.new_session({"location": "eagle_vail_co",
                                     "clients": 40, "crews": 4,
                                     "seed": "placement-demo"})
        assert code == 200, out
        sid = out["session_id"]
        q = geo.destination(IGN, 60.0, d_m)
        code, res = mgr.crew(sid, {"crew_id": "sim-g4", "lat": q.lat,
                                   "lon": q.lon})
        if code != 200:
            print(f"D={d_m} m: placement refused ({res['error']})")
            continue
        p = res["placement"]
        start = LatLon(p["lat"], p["lon"])
        code, res = mgr.ignite(sid, {"lat": IGN.lat, "lon": IGN.lon,
                                     "intensity": "high", "wind_dir_deg": 240})
        assert code == 200, res
        entry = mgr._entry(sid)
        overruns, first_reloc, dep_viol = [], None, 0
        for _ in range(8):
            with entry.lock:
                st = entry.session.tick()
            dep_viol += sum(1 for a in st["audit"] if a["violation"])
            overruns += [(st["tick"], o["crew_id"], o["reason"], o["mode"])
                         for o in st["crew_overruns"]]
            g4 = next(c for c in st["crews"] if c["crew_id"] == "sim-g4")
            if first_reloc is None and g4["relocating"]:
                first_reloc = (st["tick"], g4["relocate_reason"])
        moved = geo.haversine_m(entry.session._crew_sims["sim-g4"].pos, start)
        print(f"D={d_m} m: placed {geo.haversine_m(IGN, start):,.0f} m from "
              f"the ignition (snap {p['snap_m']} m) | first relocation "
              f"{first_reloc} | crew overruns {overruns} | departure "
              f"violations {dep_viol} | ended {moved:,.0f} m from its start | "
              f"relocations {st['scoreboard']['relocations']}")
        mgr.stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
