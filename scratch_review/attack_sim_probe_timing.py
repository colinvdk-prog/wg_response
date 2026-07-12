"""attack_sim_probe_timing.py — verifier probe: how expensive is one real-tile
tick with the AS-SHIPPED default config? Sizes the safety/perf attack budget.
Run: py -3 scratch_review/attack_sim_probe_timing.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo
from wgresponse.fuels import is_burnable
from wgresponse.simui import LOCATIONS, SimSession


def burnable_ignition(session, key, from_loc, max_km=4.0):
    tile = session._tile
    hazard = LOCATIONS[key].hazard_wind_dir_deg

    def fuel_at(p):
        rc = geo.latlon_to_rc(tile.spec, p)
        return None if rc is None else int(round(tile.fuel.get(*rc)))

    for d in range(1000, int(max_km * 1000) + 1, 250):
        p = geo.destination(from_loc, hazard, d)
        f = fuel_at(p)
        if f is not None and is_burnable(f):
            return p
    raise AssertionError("no burnable ignition found")


def main():
    key = "boulder_co"
    t0 = time.perf_counter()
    s = SimSession(key, 40, 4, seed="probe")   # default cfg: ensemble_n=6
    t1 = time.perf_counter()
    print(f"configure ({key}, real tile, defaults): {t1 - t0:.2f}s")
    target = next(c for c in s._clients
                  if is_burnable(int(round(s._tile.fuel.get(
                      *geo.latlon_to_rc(s._tile.spec, c.loc))))))
    ign = burnable_ignition(s, key, target.loc)
    s.ignite(ign.lat, ign.lon, intensity="extreme")
    for k in range(22):
        t2 = time.perf_counter()
        st = s.tick()
        t3 = time.perf_counter()
        burned = sum(st["terrain"]["burned"]["runs"][1::2])
        alerted = sum(1 for c in st["clients"] if c["state"] != "none")
        print(f"tick {k + 1}: {t3 - t2:.2f}s burned_cells={burned} "
              f"alerted={alerted} served={st['scoreboard']['served']} "
              f"violations={st['scoreboard']['violations']} "
              f"unserved={st['scoreboard']['unserved_by_reason']}")


if __name__ == "__main__":
    main()
