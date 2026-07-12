"""Probe 3: execute the rug-pull at the armed moment; capture exact numbers."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import client_p10_map
from scratch_review.attack_sim_probe_synth_sweep import controlled_dir
from wgresponse import geo
from wgresponse.simui import LOCATIONS, SimSession

FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
        "slack_min": 0.0, "egress_buffer_min": 10.0}


def main():
    base = tempfile.mkdtemp(prefix="atk_rug3_")
    d = controlled_dir(base, hamlet_half=3)
    loc = LOCATIONS["boulder_co"]
    s = SimSession("boulder_co", 16, 1, seed="m1", cfg_overrides=dict(FAST),
                   tile_dir=d)
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 8000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    pulled = None
    for k in range(60):
        st = s.tick()
        p10 = client_p10_map(st)
        if pulled is None:
            for c in st["crews"]:
                if c["status"] != "deploying" or not c["itinerary"]:
                    continue
                cur = c["itinerary"][0]
                v = p10.get(cur["client_id"])
                if v is None:
                    continue
                gap_min = (v - cur["planned_depart_s"]) / 60.0
                if gap_min <= 100.0:
                    s.set_buffer(120.0)
                    st2 = s.state()
                    crew2 = next(x for x in st2["crews"]
                                 if x["crew_id"] == c["crew_id"])
                    kept = bool(crew2["itinerary"] and
                                crew2["itinerary"][0]["client_id"]
                                == cur["client_id"])
                    pulled = {"tick": k + 1, "crew": c["crew_id"],
                              "client": cur["client_id"],
                              "gap_min": round(gap_min, 1), "kept": kept,
                              "planned_depart_s": cur["planned_depart_s"],
                              "p10_at_pull": v}
                    print("PULLED:", pulled)
                    break
        else:
            for sv in st["serviced"]:
                if sv["client_id"] == pulled["client"]:
                    v = p10.get(sv["client_id"])
                    aud = [a for a in st["audit"]
                           if a["client_id"] == sv["client_id"]]
                    if v is not None:
                        deficit = (sv["depart_s"] - (v - 120 * 60.0)) / 60.0
                        print(f"tick {st['tick']}: COMPLETED depart="
                              f"{sv['depart_s']} p10_now={v} "
                              f"deficit_vs_new_buffer={deficit:.1f}min "
                              f"engine_violations={st['scoreboard']['violations']} "
                              f"audit_flag={aud[0]['violation'] if aud else None} "
                              f"audit_margin_h={aud[0]['margin_h'] if aud else None}")
            if any(sv["client_id"] == pulled["client"]
                   for sv in st["serviced"]) and st["tick"] >= pulled["tick"] + 6:
                break
    if pulled is None:
        print("NO OPPORTUNITY FOUND")


if __name__ == "__main__":
    main()
