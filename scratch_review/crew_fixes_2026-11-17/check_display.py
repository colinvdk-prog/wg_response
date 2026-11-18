"""Display-only proof for the 2026-11-17 fixes (outcome_lost_capacity,
unserved_reason_refresh): the same world with the two display knobs on and
off — every tick's state minus the display fields hashes the same, every
dispatch.plan call returns the same plan. Also reports what the display says.
NOT product code. Usage: python check_display.py WORLD [HOURS]
WORLD: malibu_left | glenwood_left | parkcity_right | bc24
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

ROT = {"canon": 0.0, "left": -40.0, "right": 40.0}


def build(world, cfg):
    from wgresponse import server, geo
    from wgresponse.fuels import is_burnable
    key, side = {"malibu_left": ("malibu_santa_ana", "left"),
                 "glenwood_left": ("glenwood_springs_westerly", "left"),
                 "parkcity_right": ("park_city_windstorm", "right"),
                 "bc24": ("beaver_creek_westerly", "canon")}[world]
    mgr = server.SimSessionManager()
    pre = server.SCENARIOS[key]
    payload = {"scenario": key, "spotting": True, "grass_realism": True,
               "hold_h": 12, "hold_per_home": True, "proximity_first": True,
               "cfg": dict(cfg)}
    code, out = mgr.new_session(payload)
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    rot = ROT[side]
    if rot != 0.0:
        s.reset()
        b = (pre["ign_bearing"] + rot) % 360.0
        p = None
        if pre.get("ign_fine_fuel", False):
            p = s.fine_fuel_start(b, pre["ign_dist_m"])
        if p is None:
            center = s._loc.center
            for db in (0, 10, -10, 20, -20, 30, -30):
                for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
                    q = geo.destination(center, b + db, pre["ign_dist_m"] * shrink)
                    rc = geo.latlon_to_rc(s._tile.spec, q)
                    if rc is None:
                        continue
                    if is_burnable(int(round(s._tile.fuel.get(*rc)))):
                        p = q
                        break
                if p is not None:
                    break
        s.ignite(p.lat, p.lon, intensity=pre["intensity"], wind_dir_deg=b)
    return mgr, s


def strip(st):
    st = dict(st)
    for k in ("cmd_log", "report", "unserved"):
        st.pop(k, None)
    sb = dict(st["scoreboard"])
    sb.pop("outcomes", None)
    sb.pop("unserved_by_reason", None)
    st["scoreboard"] = sb
    st["clients"] = [{k: v for k, v in c.items() if k != "outcome"}
                     for c in st["clients"]]
    return st


def h(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, allow_nan=False,
                                     default=str).encode()).hexdigest()[:16]


def run(world, cfg, ticks):
    from wgresponse import dispatch
    plans = []
    orig = dispatch.plan

    def wrapped(*a, **kw):
        p = orig(*a, **kw)
        plans.append(h({cid: [(s.client_id, round(s.arrive_s, 3))
                              for s in cp.stops]
                        for cid, cp in sorted(p.plans.items())}))
        return p
    dispatch.plan = wrapped
    try:
        mgr, s = build(world, cfg)
        hs, last = [], None
        reasons = {}
        for _ in range(ticks):
            st = s.tick()
            hs.append(h(strip(st)))
            for u in st["unserved"]:
                reasons.setdefault(u["reason"], 0)
                reasons[u["reason"]] += 1
            last = st
    finally:
        dispatch.plan = orig
    return hs, plans, last, reasons


def main(argv):
    world = argv[0]
    hours = float(argv[1]) if len(argv) > 1 else 16.0
    ticks = int(round(hours * 4))
    on = {}
    off = {"outcome_lost_capacity": False, "unserved_reason_refresh": False}
    a = run(world, on, ticks)
    b = run(world, off, ticks)
    print(world, "ticks", ticks, "states equal (display stripped):", a[0] == b[0],
          "plans equal:", a[1] == b[1], len(a[1]), "plan calls")
    print("  on  outcomes", a[2]["scoreboard"]["outcomes"])
    print("  off outcomes", b[2]["scoreboard"]["outcomes"])
    rep = a[2]["report"]
    print("  report reached", rep["reached"], "uncoated", rep["reached_uncoated"],
          "capacity", rep.get("reached_uncoated_capacity"),
          "unsavable", rep.get("reached_uncoated_unsavable"))
    print("  unserved reason-ticks on ", dict(sorted(a[3].items())))
    print("  unserved reason-ticks off", dict(sorted(b[3].items())))


if __name__ == "__main__":
    main(sys.argv[1:])
