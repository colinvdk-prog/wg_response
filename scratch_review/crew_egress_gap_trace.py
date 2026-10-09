"""Per-tick crew trace of a one-click scenario (2026-10-09; docs/
SIMULATOR_DESIGN.md "No crew drives an unjudged leg"). NOT product code.

Attributes every SEM-10c crew-overrun row to its mechanism: each tick prints
every crew's mode, position, its cell's nominal and eroded-P10 arrival (hours
after T0), its itinerary and egress target, plus the tick's withdrawals,
overrun rows and crew-safety notes (incl. "egress re-routed (SEMANTICS 20b)").

Usage:
  py -3 scratch_review/crew_egress_gap_trace.py KEY INTENSITY PROFILE FLOOR
        HOURS [off]
  e.g.  ... malibu_santa_ana extreme city grass 1.5        (fixed: no row)
        ... malibu_santa_ana extreme city grass 1.5 off    (the T+0.75 h row)
        ... aspen_southwesterly high city all 4 off        (T+3.5 h, Z)
        ... eagle_gypsum_westerly extreme city all 4.25 off (T+4.0 h, V)

FLOOR: grass (ros_floor_grass_only, the simulator default) | all (the old
all-fuel floor). `off` pins both SAFETY SEMANTICS 20 knobs off (= commit
8973cd9's crew-safety law).
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import server, simui  # noqa: E402

T0 = simui.T0_S


def _h(t):
    if t is None or (isinstance(t, float) and math.isinf(t)):
        return "inf"
    return f"{(t - T0) / 3600.0:+.3f}h"


def trace(key, intensity, profile, floor, hours, off=False):
    cfg = {"sim_weather_profile": profile,
           "ros_floor_grass_only": floor == "grass"}
    if off:
        cfg.update(corridor_cell_exact=False, crew_egress_judged=False)
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    if intensity != f.intensity:
        lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
        s.reset()
        s.ignite(lat, lon, intensity=intensity, wind_dir_deg=wd)
    print("zones:", [(z.zone_id, round(z.loc.lat, 5), round(z.loc.lon, 5))
                     for z in s._loc.safe_zones])
    print("fire origin:", s._fires["sim-f1"].origin)
    seen_w, nn = 0, len(s._notes)
    for k in range(int(round(hours * 4))):
        st = s.tick()
        print(f"tick {k + 1} T{_h(s._now_s)} violations "
              f"{st['scoreboard']['violations']}")
        for cid in simui._idsort(s._crew_sims):
            cs = s._crew_sims[cid]
            nom, p10 = s._crew_exposure(cs.pos)
            print(f"  {cid} {cs._mode:6s} ({cs.pos.lat:.5f},{cs.pos.lon:.5f})"
                  f" nominal {_h(nom)} eroded-p10 {_h(p10)} stops "
                  f"{[x['client_id'] for x in cs.itinerary]} egress "
                  f"{cs.egress_zone_id or '-'}")
        for w in s._withdrawn[seen_w:]:
            print(f"  WITHDRAWN {w['crew_id']} at {w['client_id']} "
                  f"({w['cause']}) T{_h(w['at_s'])}")
        seen_w = len(s._withdrawn)
        for o in st.get("crew_overruns") or []:
            print(f"  OVERRUN {o['crew_id']} {o['reason']} {o['mode']} "
                  f"({o['lat']},{o['lon']}) eroded-p10 "
                  f"{_h(o['eroded_p10_s'])}")
        for note in s._notes[nn:]:
            if "SAFETY" in note or "crew" in note.lower():
                print(f"  NOTE {note[:240]}")
        nn = len(s._notes)


if __name__ == "__main__":
    a = sys.argv[1:]
    trace(a[0], a[1], a[2], a[3], float(a[4]),
          off=len(a) > 5 and a[5] == "off")
