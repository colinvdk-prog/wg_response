"""Crown-fire study: dissect a crew-overrun row (2026-10-12; NOT product code).

Re-runs one one-click scenario x variant through the server path and, at
every tick with a crew-overrun row (SAFETY SEMANTICS 10c), prints the row
(crew, reason, mode, status, position, nominal arrival and eroded P10 vs
now) and that crew's track: its position, mode, status and target over the
previous ticks, plus the tick's notes naming it.

Usage: py -3 scratch_review/crown_fire_overrun_probe.py SCENARIO VARIANT [ticks]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_scenarios as C  # noqa: E402
import ember_spotting_probe as E  # noqa: E402


def main(argv):
    scen, variant = argv[0], argv[1]
    ticks = int(argv[2]) if len(argv) > 2 else 24
    s, fid = E.build(scen, C.VARIANTS[variant])
    f = s._fires[fid]
    t0 = f.t_ignite_s
    track = {}
    for k in range(ticks):
        st = s.tick()
        now = st.get("t_s") or st.get("now_s")
        for c in st.get("crews", []):
            cid = c.get("crew_id") or c.get("id")
            track.setdefault(cid, []).append(
                {"tick": k + 1, "t_h": round((k + 1) * 0.25, 2),
                 "lat": c.get("lat"), "lon": c.get("lon"),
                 "status": c.get("status"), "mode": c.get("mode"),
                 "target": c.get("egress_zone_id") or c.get("target")})
        rows = st.get("crew_overruns") or []
        if rows:
            print(f"== tick {k + 1} (t+{(k + 1) * 0.25:.2f} h) now {now}")
            for r in rows:
                rr = dict(r)
                for key in ("nominal_arrival_s", "eroded_p10_s"):
                    if rr.get(key) is not None and now:
                        rr[key + "_minus_now_min"] = round(
                            (rr[key] - now) / 60.0, 1)
                print(json.dumps(rr))
                for p in track.get(r["crew_id"], [])[-6:]:
                    print("   ", json.dumps(p))
            for n in st.get("notes", [])[-12:]:
                if any(r["crew_id"] in str(n) for r in rows):
                    print("   note:", str(n)[:300])
    print("t0", t0)


if __name__ == "__main__":
    main(sys.argv[1:])
