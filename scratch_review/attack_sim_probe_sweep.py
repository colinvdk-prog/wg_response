"""Probe: find a (tile, range, speed) combo where the buffer sweep BINDS —
several clients served at low buffer, fewer at high buffer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_sim_common import burned_count, pick_ignition
from wgresponse.simui import SimSession


def run(key, buf, seed, min_m, max_m, speed, ticks, n_clients=24, n_crews=2):
    s = SimSession(key, n_clients, n_crews, seed=seed,
                   cfg_overrides={"egress_buffer_min": buf})
    ign, target = pick_ignition(s, key, min_m=min_m, max_m=max_m, cluster=True)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    s.set_speed(speed)
    for _ in range(ticks):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
    return (st["scoreboard"]["served"], st["scoreboard"]["unserved_by_reason"],
            burned_count(st), sum(1 for c in st["clients"] if c["eta_p10_s"]))


for key, n_cl, crews, rng, speed, ticks in (
        ("boulder_co", 200, 1, (500, 1500), 2.0, 40),
        ("santa_rosa_ca", 200, 1, (500, 1500), 2.0, 40)):
    for buf in (10.0, 30.0, 60.0, 90.0, 120.0):
        served, unserved, burned, finite = run(
            key, buf, "sweep-probe", rng[0], rng[1], speed, ticks,
            n_clients=n_cl, n_crews=crews)
        print(f"{key} n={n_cl} crews={crews} rng={rng} speed={speed} "
              f"buf={buf:5.0f}: served={served} unserved={unserved} "
              f"burned={burned} finite={finite}")
