"""Probe: why does the slider scenario serve nothing on boulder_co?"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import burned_count, pick_ignition
from wgresponse.simui import SimSession


def main():
    s = SimSession("boulder_co", 24, 2, seed="atk-slider",
                   cfg_overrides={"egress_buffer_min": 10.0})
    ign, target = pick_ignition(s, "boulder_co")
    print("ignition:", ign, "target:", target.client_id, target.loc)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(2.0)
    for k in range(24):
        st = s.tick()
        n_alert = sum(1 for c in st["clients"] if c["state"] != "none")
        finite = [c for c in st["clients"] if c["eta_p10_s"] is not None]
        print(f"t{k+1}: burned={burned_count(st)} alerted={n_alert} "
              f"finite_eta={len(finite)} served={st['scoreboard']['served']} "
              f"unserved={st['scoreboard']['unserved_by_reason']} "
              f"crews={[ (c['crew_id'], c['status'], len(c['itinerary'])) for c in st['crews']]}")
        if k == 5 and finite:
            import json
            print("  finite clients:", [(c['client_id'],
                                         round((c['eta_p50_s'] - st['sim_now_s'])/3600, 1) if c['eta_p50_s'] else None)
                                        for c in finite])
            print("  plan:", json.dumps(st["plan"], indent=1)[:600])


if __name__ == "__main__":
    main()
