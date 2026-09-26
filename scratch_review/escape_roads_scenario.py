"""scratch_review/escape_roads_scenario.py — dissect one bench_dispatch.py
scenario with the road-network escape off vs on (contracts
escape_road_network): every relocation (tick, crew, reason, target, straight
distance, route metrics of the choice), every 10c row, and served per tick.

Usage: py -3 scratch_review/escape_roads_scenario.py "paradise_ca|+0|8000|high" [--set k=v]
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scratch_review.bench_dispatch import BASE, HORIZON_TICKS, N_CLIENTS, N_CREWS  # noqa: E402
from wgresponse import geo, simui  # noqa: E402


def run(seed: str, extra: dict, ticks: int):
    m, off, dist, inten = seed.split("|")
    loc = simui.LOCATIONS[m]
    wd = (loc.hazard_wind_dir_deg + float(off)) % 360.0
    cfg = dict(BASE)
    cfg.update(extra)
    s = simui.SimSession(m, N_CLIENTS, N_CREWS, seed=seed, cfg_overrides=cfg,
                         fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, float(dist) * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=wd)
            break
        except ValueError:
            continue
    events = []
    orig = simui._SimCrew.relocate_to
    tick = {"n": 0}

    def reloc(self, zone_loc, zone_zone_id, *a, **k):
        events.append((tick["n"], self.crew.crew_id, zone_zone_id,
                       round(geo.haversine_m(self.pos, zone_loc)), self._mode,
                       (round(self.pos.lat, 5), round(self.pos.lon, 5))))
        return orig(self, zone_loc, zone_zone_id, *a, **k)

    simui._SimCrew.relocate_to = reloc
    served = []
    rows = []
    try:
        for t in range(1, ticks + 1):
            tick["n"] = t
            st = s.tick()
            served.append(st["scoreboard"]["served"])
            for o in st.get("crew_overruns") or []:
                p10 = o.get("eroded_p10_s")
                rows.append((t, o["crew_id"], o["reason"], o["mode"],
                             None if p10 is None else round((st["sim_now_s"] - p10) / 60.0, 1)))
    finally:
        simui._SimCrew.relocate_to = orig
    return s, events, served, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed")
    ap.add_argument("--ticks", type=int, default=HORIZON_TICKS)
    a = ap.parse_args()
    for name, extra in (("off", {"escape_road_network": False}), ("on", {})):
        s, events, served, rows = run(a.seed, extra, a.ticks)
        print(f"\n=== {name}: served {served[-1]}, relocations "
              f"{s._n_relocations}, 10c rows {len(rows)}")
        print("  served by tick:", " ".join(str(x) for x in served))
        notes = [n for n in s._notes if "proactive escape" in n or "repositioning" in n]
        for e in events:
            print("  reloc t%d %s -> %s (%d m) from mode %s at %s" % e)
        for r in rows:
            print("  ROW t%d %s %s/%s past-P10 %s min" % r)


if __name__ == "__main__":
    main()
