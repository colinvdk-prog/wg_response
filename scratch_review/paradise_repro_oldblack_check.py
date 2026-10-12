"""Is every 'idle in the burn' tick of the crown-fire Paradise repro a crew on
OLD BLACK (founder ruling 2026-09-26, contracts old_black_refuge), or does an
idle crew ever stand on HOT black / an imminent cell by the product's own law?
NOT product code. Usage: py -3 scratch_review/paradise_repro_oldblack_check.py
"""
from __future__ import annotations

import collections
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tests.test_simui import _crew_fire_exposure  # noqa: E402
from wgresponse import geo  # noqa: E402
from wgresponse.fuels import FUEL_TABLE  # noqa: E402
from wgresponse.simui import SimSession  # noqa: E402

s = SimSession("paradise_ca", 64, 5, seed="caught-1", fire_zone="extended",
               cfg_overrides={"sim_weather_profile": "generic"})
assert s._cfg.crown_fire and s._cfg.old_black_refuge
s.ignite(39.83, -121.52, intensity="extreme")
buf = s._cfg.egress_buffer_min * 60.0
raw_ticks = law_ticks = hidden_law = 0
ages = []
fuels = collections.Counter()
for k in range(180):
    st = s.tick()
    now = s._now_s
    raw = law = False
    for cid, cs in s._crew_sims.items():
        if cs.status.value != "available" or cs.itinerary:
            continue
        nom, p10 = _crew_fire_exposure(s, cs)
        if (not math.isinf(nom) and nom <= now) or (not math.isinf(p10) and p10 < now + buf):
            raw = True
            unsafe, burned, imminent = s._cell_unsafe(cs.pos, now)
            if unsafe:
                law = True
                print(f"t {(k + 1) * 0.25:.2f} h {cid} UNSAFE BY LAW burned={burned} imminent={imminent}", flush=True)
            else:
                window = s._cold_black_pass_s(cs.pos)
                ages.append(round((now - nom) / 60.0 - window / 60.0, 1))
                rc = geo.latlon_to_rc(s._tile.spec, cs.pos)
                code = int(round(s._tile.fuel.get(*rc)))
                fm = FUEL_TABLE.get(code)
                fuels[fm.name.split()[0] if fm else code] += 1
                assert s._on_old_black(cs.pos, now), (cid, k)
    raw_ticks += raw
    law_ticks += law
    if law and st["scoreboard"]["violations"] == 0:
        hidden_law += 1
print("ticks with an idle crew on burned / imminent ground (test helper):", raw_ticks)
print("... of which a crew is UNSAFE by the product's law (hot black / imminent):", law_ticks,
      "| with violations 0:", hidden_law)
print("idle-on-old-black crew-ticks:", len(ages), "| minutes past the cold-black window: min",
      min(ages) if ages else None, "median", sorted(ages)[len(ages) // 2] if ages else None)
print("their fuels:", dict(fuels))
print("final: violations", st["scoreboard"]["violations"], "relocations", st["scoreboard"]["relocations"],
      "served", st["scoreboard"]["served"], "left map", st["scoreboard"].get("crews_left_map"))
