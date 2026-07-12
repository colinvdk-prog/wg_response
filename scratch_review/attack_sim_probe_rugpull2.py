"""Probe 2: min (p10_now - planned_depart) over mid-deploy moments, synthetic
checkerboard (the suite's own geometry) + real tile, speed 1, slack 0."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import client_p10_map, pick_ignition
from scratch_review.attack_sim_probe_synth_sweep import controlled_dir
from wgresponse import geo
from wgresponse.simui import LOCATIONS, SimSession

FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0,
        "slack_min": 0.0, "egress_buffer_min": 10.0}


def scan(s, ticks):
    best = None
    n_deploy_moments = 0
    for k in range(ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        p10 = client_p10_map(st)
        for c in st["crews"]:
            if c["status"] != "deploying" or not c["itinerary"]:
                continue
            n_deploy_moments += 1
            cur = c["itinerary"][0]
            v = p10.get(cur["client_id"])
            if v is None:
                continue
            gap_min = (v - cur["planned_depart_s"]) / 60.0
            if best is None or gap_min < best[0]:
                best = (round(gap_min, 1), k + 1, c["crew_id"],
                        cur["client_id"])
    return best, n_deploy_moments, st["scoreboard"]["served"]


def main():
    base = tempfile.mkdtemp(prefix="atk_rug_")
    d = controlled_dir(base, hamlet_half=3)
    loc = LOCATIONS["boulder_co"]
    for dist in (6000.0, 8000.0):
        for seed in ("m1", "rug-a", "rug-b"):
            s = SimSession("boulder_co", 16, 1, seed=seed,
                           cfg_overrides=dict(FAST), tile_dir=d)
            ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, dist)
            s.ignite(ign.lat, ign.lon, intensity="high")
            best, n, served = scan(s, 60)
            print(f"synth dist={dist} seed={seed}: served={served} "
                  f"deploy_moments={n} min_gap={best}")
    # real tile, speed 1
    for seed in ("rug-1", "rug-2"):
        s = SimSession("santa_rosa_ca", 24, 1, seed=seed,
                       cfg_overrides={"slack_min": 0.0,
                                      "egress_buffer_min": 10.0})
        ign, _ = pick_ignition(s, "santa_rosa_ca", min_m=1500, max_m=3000,
                               cluster=True)
        s.ignite(ign.lat, ign.lon, intensity="extreme")
        best, n, served = scan(s, 60)
        print(f"real santa_rosa seed={seed}: served={served} "
              f"deploy_moments={n} min_gap={best}")


if __name__ == "__main__":
    main()
