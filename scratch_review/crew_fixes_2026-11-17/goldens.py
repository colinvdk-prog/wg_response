"""Per-tick state hashes of the worlds the 2026-11-17 crew-program fixes bite
on — run on main e43c24e (before the rules) to make the knob-off goldens of
tests/test_crew_waiting.py, and on the branch with the six knobs off to check
them. NOT product code.

Usage: python goldens.py OUT.json [--ticks-scale 1.0]
(run from the tree under test: it imports the wgresponse beside it)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

FIXES = ("escape_wait_watch", "refuge_keeps_exit", "crew_overrun_worst_row",
         "replan_on_release", "outcome_lost_capacity",
         "unserved_reason_refresh")
PAGE = {"hold_h": 12, "hold_per_home": True, "proximity_first": True,
        "crews_3h": 0, "spotting": True, "grass_realism": True}


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)
    st.pop("report", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _off():
    from wgresponse.contracts import ResponseConfig
    if hasattr(ResponseConfig(), "escape_wait_watch"):
        return {k: False for k in FIXES}
    return {}


def build(world):
    from wgresponse import server, geo
    mgr = server.SimSessionManager()
    if world in ("vail24", "bc24", "pc_side"):
        loc, scen = {"vail24": ("vail_co", "vail_westerly"),
                     "bc24": ("beaver_creek_co", "beaver_creek_westerly"),
                     "pc_side": ("park_city_ut", "park_city_windstorm")}[world]
        payload = dict(PAGE, scenario=scen, fire_zone="standard")
        off = _off()
        if off:
            payload["cfg"] = off
        code, out = mgr.new_session(payload)
        assert code == 200, out
        s = mgr._entry(out["session_id"]).session
        f = s._fires["sim-f1"]
        origin, wind = f.origin, f.wind_dir_deg
        s.reset()
        if world == "pc_side":
            b = (wind + 90.0) % 360.0
            p = s.fine_fuel_start(b, 6000.0)
            if p is None:
                center = s._loc.center
                from wgresponse.fuels import is_burnable
                for db in (0, 10, -10, 20, -20, 30, -30):
                    for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
                        q = geo.destination(center, b + db, 6000.0 * shrink)
                        rc = geo.latlon_to_rc(s._tile.spec, q)
                        if rc is None:
                            continue
                        if is_burnable(int(round(s._tile.fuel.get(*rc)))):
                            p = q
                            break
                    if p is not None:
                        break
            s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=b)
        else:
            s.ignite(origin.lat, origin.lon, intensity="extreme",
                     wind_dir_deg=wind)
        return mgr, s
    raise ValueError(world)


TICKS = {"vail24": 96, "bc24": 96, "pc_side": 16}


def main(argv):
    out_path = argv[0]
    worlds = argv[1].split(",") if len(argv) > 1 else list(TICKS)
    res = {}
    for w in worlds:
        mgr, s = build(w)
        hs = []
        rows = []
        for _ in range(TICKS[w]):
            st = s.tick()
            hs.append(_hash(st))
            rows.extend((st["tick"], o["crew_id"], bool(o.get("transit")),
                         o.get("margin_min")) for o in st["crew_overruns"])
        chain = hashlib.sha256("".join(hs).encode()).hexdigest()[:16]
        res[w] = {"hashes": hs, "chain": chain, "rows": rows,
                  "served": st["scoreboard"]["served"]}
        print(w, chain, rows, st["scoreboard"]["served"], flush=True)
        with mgr._lock:
            mgr._sessions.clear()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
