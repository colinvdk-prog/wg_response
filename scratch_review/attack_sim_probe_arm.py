"""Probe: arming for B2 (revocable itinerary) and retro test (served>=1)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import pick_ignition
from wgresponse.simui import SimSession

# B2: crews=1 queue, speed 1
for seed in ("rug-2", "atk-replan", "atk-b2"):
    s = SimSession("santa_rosa_ca", 24, 1, seed=seed)
    ign, _ = pick_ignition(s, "santa_rosa_ca", min_m=1500, max_m=3000,
                           cluster=True)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    armed_at = None
    kinds = []
    for k in range(30):
        st = s.tick()
        for c in st["crews"]:
            if c["status"] == "en_route" and c["itinerary"]:
                kinds.append((k + 1, "en_route", len(c["itinerary"])))
            elif c["status"] == "deploying" and len(c["itinerary"]) >= 2:
                kinds.append((k + 1, "deploy+queue", len(c["itinerary"])))
        if kinds and armed_at is None:
            armed_at = k + 1
    print(f"B2 seed={seed}: armed_at={armed_at} first5={kinds[:5]} "
          f"served={st['scoreboard']['served']}")

# retro: sweep-session params buf 10
s = SimSession("santa_rosa_ca", 60, 1, seed="atk-slider",
               cfg_overrides={"egress_buffer_min": 10.0})
ign, _ = pick_ignition(s, "santa_rosa_ca", min_m=1500, max_m=3000, cluster=True)
s.ignite(ign.lat, ign.lon, intensity="extreme")
s.set_speed(2.0)
first_serve = None
for k in range(36):
    st = s.tick()
    if st["scoreboard"]["served"] >= 1 and first_serve is None:
        first_serve = k + 1
print(f"retro: first_serve_tick={first_serve} "
      f"served_final={st['scoreboard']['served']}")
