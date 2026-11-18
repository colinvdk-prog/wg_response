"""Find SEMANTICS 29 en-route releases in a world and whether a plan ran in
the same tick (SEMANTICS 47b). NOT product code.
Usage: python diag_release.py WORLD HOURS '{"replan_on_release": false}'
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def main(argv):
    import check_display
    from wgresponse import dispatch
    world = argv[0]
    hours = float(argv[1])
    cfg = json.loads(argv[2]) if len(argv) > 2 else {}
    calls = [0]
    orig = dispatch.plan

    def wrapped(*a, **kw):
        calls[0] += 1
        return orig(*a, **kw)
    dispatch.plan = wrapped
    mgr, s = check_display.build(world, cfg)
    seen = len(s._notes)
    for _ in range(int(round(hours * 4))):
        c0 = calls[0]
        st = s.tick()
        notes = s._notes[seen:]
        seen = len(s._notes)
        rel = [n for n in notes if "en route (SEMANTICS 29)" in n]
        if rel:
            print(st["tick"], "plans", calls[0] - c0, rel)
            for n in rel:
                c = n.split(" released ")[1].split(" ")[0]
                u = [x for x in st["unserved"] if x["client_id"] == c]
                print("   ", c, "assigned" if c in s._assigned else "unassigned",
                      u, "outcome", [x["outcome"] for x in st["clients"] if x["client_id"] == c])
    print("served", st["scoreboard"]["served"])


if __name__ == "__main__":
    main(sys.argv[1:])
