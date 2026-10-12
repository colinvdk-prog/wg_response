"""The founder Paradise crew-safety repro (tests/test_simui.py::
test_crew_safety_paradise_founder_repro_when_tile_present: extended tile, 64
clients, 5 crews, extreme at 39.83,-121.52, generic weather, 180 ticks) under
the crown-fire simulator defaults and controls: which knob leaves crews idle
in the burn, when, where, and what the safety machinery did. NOT product code.
Usage: py -3 scratch_review/paradise_repro_crown_probe.py VARIANT [OUT.json]
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests.test_simui import _crew_fire_exposure, _idle_crews_in_burn  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.simui import SimSession  # noqa: E402

VARIANTS = {
    "defaults": {},
    "no_crown": {"crown_fire": False},
    "crown_legacy_embers": {"spot_fire_size": False},
    "option1": {"crown_fire": False, "ros_floor_grass_only": False,
                "spot_ignition_delay_min": 5.0},
}
name = sys.argv[1]
cfg = dict({"sim_weather_profile": "generic"}, **VARIANTS[name])
t0 = time.monotonic()
s = SimSession("paradise_ca", 64, 5, seed="caught-1", fire_zone="extended",
               cfg_overrides=cfg)
s.ignite(39.83, -121.52, intensity="extreme")
events = []
idle_ticks = hidden = 0
st = None
for k in range(180):
    st = s.tick()
    now = s._now_s
    idle = _idle_crews_in_burn(s, now)
    if idle:
        idle_ticks += 1
        if st["scoreboard"]["violations"] == 0:
            hidden += 1
        if len(events) < 40:
            rows = []
            for cid in idle:
                cs = s._crew_sims[cid]
                nom, p10 = _crew_fire_exposure(s, cs)
                rows.append({"crew": cid, "lat": round(cs.pos.lat, 5), "lon": round(cs.pos.lon, 5),
                             "nom_h": None if math.isinf(nom) else round((nom - now) / 3600.0, 2),
                             "p10_h": None if math.isinf(p10) else round((p10 - now) / 3600.0, 2),
                             "zone": getattr(cs, "zone_id", None),
                             "status": cs.status.value})
            events.append({"tick": k + 1, "t_h": round((k + 1) * 0.25, 2),
                           "violations": st["scoreboard"]["violations"],
                           "crews": rows,
                           "notes": [n for n in (st.get("notes") or [])][-4:]})
res = {"variant": name, "cfg": cfg, "idle_in_burn_ticks": idle_ticks, "hidden_ticks": hidden,
       "relocations": st["scoreboard"]["relocations"], "violations_final": st["scoreboard"]["violations"],
       "served": st["scoreboard"]["served"],
       "fire_acres": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
       "first_events": events, "secs": round(time.monotonic() - t0, 1)}
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, f"paradise_repro_crown_{name}.json")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(res, fh, indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k != "first_events"}), flush=True)
