"""tests/test_dest_durable.py's Paradise crown-fire repro (extended Paradise,
64 homes, 5 crews, extreme, 180 ticks) with the simulator's defaults plus
overrides: rows, idle-in-burn, notes. NOT product code.
Usage: python diag_crown.py '{"escape_wait_watch": false}'
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def main(argv):
    from wgresponse import simui
    from tests.test_simui import LEGACY_WX, _idle_crews_in_burn
    extra = json.loads(argv[0]) if argv else {}
    cfg = dict(LEGACY_WX, **extra)
    s = simui.SimSession("paradise_ca", 64, 5, seed="caught-1",
                         fire_zone="extended", cfg_overrides=cfg)
    s.ignite(39.83, -121.52, intensity="extreme")
    seen = len(s._notes)
    st = None
    for _ in range(180):
        st = s.tick()
        idle = _idle_crews_in_burn(s, s._now_s)
        for n in s._notes[seen:]:
            if ("SEMANTICS 28" in n or "SEMANTICS 46" in n or "reposition" in n
                    or "SEMANTICS 36" in n or "LEFT THE MAP" in n
                    or "SEMANTICS 30" in n or "SEMANTICS 29" in n):
                print(st["tick"], n[:230], flush=True)
        seen = len(s._notes)
        if st["crew_overruns"] or idle:
            print("ROW", st["tick"], idle,
                  [(o["crew_id"], o.get("reason"), o.get("mode"), o.get("transit"),
                    o.get("margin_min"), o.get("exposed_s")) for o in st["crew_overruns"]],
                  flush=True)
    print("served", st["scoreboard"]["served"], "viol", st["scoreboard"]["violations"],
          "left", st["scoreboard"]["crews_left_map"])


if __name__ == "__main__":
    main(sys.argv[1:])
