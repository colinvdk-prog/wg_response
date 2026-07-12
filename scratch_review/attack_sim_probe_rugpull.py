"""Probe: can we catch a crew MID-DEPLOY with (p10 - planned_depart) < 120 min?
Late-adoption config (notice_h=2) forces service just ahead of the front."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import client_p10_map, pick_ignition
from wgresponse.simui import SimSession

CFG = {"notice_h": 2.0, "extreme_h": 1.0, "extreme_p10_h": 0.5,
       "egress_buffer_min": 10.0, "slack_min": 0.0}


def main():
    for key in ("santa_rosa_ca", "boulder_co"):
        for seed in ("rug-1", "rug-2"):
            s = SimSession(key, 16, 1, seed=seed, cfg_overrides=dict(CFG))
            ign, _ = pick_ignition(s, key, min_m=800, max_m=2000, cluster=True)
            s.ignite(ign.lat, ign.lon, intensity="extreme")
            s.set_speed(2.0)
            best = None
            for k in range(40):
                st = s.tick()
                assert st["scoreboard"]["violations"] == 0
                p10 = client_p10_map(st)
                for c in st["crews"]:
                    if c["status"] != "deploying" or not c["itinerary"]:
                        continue
                    cur = c["itinerary"][0]
                    v = p10.get(cur["client_id"])
                    if v is None:
                        continue
                    gap_min = (v - cur["planned_depart_s"]) / 60.0
                    if best is None or gap_min < best[0]:
                        best = (gap_min, k + 1, cur["client_id"])
            print(f"{key} seed={seed}: served={st['scoreboard']['served']} "
                  f"min (p10 - planned_depart) while deploying = {best}")


if __name__ == "__main__":
    main()
