"""The extended Camp-Fire-scale Paradise run of tests/test_crew_pocket.py
(campfire_armed), with given overrides: rows and the crew-safety notes.
NOT product code. Usage: python diag_campfire.py '{"escape_wait_watch": false}'
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def main(argv):
    from wgresponse import geo, simui
    extra = json.loads(argv[0]) if argv else {}
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    cfg = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
           "deploy_min": 15.0}
    cfg.update(extra)
    s = simui.SimSession(key, 20, 2, seed="alerted|paradise_ca",
                         cfg_overrides=cfg, fire_zone="extended")
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    seen = len(s._notes)
    rows = []
    for _ in range(120):
        st = s.tick()
        for o in st["crew_overruns"]:
            rows.append((st["tick"], o["crew_id"], bool(o.get("transit")),
                         o.get("margin_min"), o.get("mode"), o.get("reason")))
        for n in s._notes[seen:]:
            if ("SEMANTICS 28" in n or "SEMANTICS 46" in n or "reposition" in n
                    or "SEMANTICS 36" in n or "LEFT THE MAP" in n
                    or "SEMANTICS 30" in n):
                print(st["tick"], n[:260], flush=True)
        seen = len(s._notes)
    print("rows", rows)
    print("served", st["scoreboard"]["served"], "left", st["scoreboard"]["crews_left_map"],
          "reloc", st["scoreboard"]["relocations"])


if __name__ == "__main__":
    main(sys.argv[1:])
