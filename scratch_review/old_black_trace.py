"""scratch_review/old_black_trace.py — tick-by-tick crew trace of one offroad-
probe session in two arms (the founder rulings of 2026-09-26: old black is a
refuge; a crew that leaves the map is out), to explain where they diverge.

Usage: py -3 scratch_review/old_black_trace.py KEY [--zone standard]
         [--from 30] [--to 40] [--ticks 40] [--intensity high] [--dist 7000]
         [--arm-a old_black_refuge=false,left_map_out=false,escape_prefer_on_map=false]
         [--arm-b ""]
Prints, for each tick in [from, to], per crew: mode / status / position /
egress target / relocate reason / on old black / its crew-overrun row, plus
the notes that tick added — arm A then arm B.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from scratch_review.roads_offroad_probe import (EXT_DIST_M, N_1H, N_3H,  # noqa: E402
                                                N_CLIENTS, N_CREWS, PAGE_CFG,
                                                SEED, _ignite)
from wgresponse import geo, simui  # noqa: E402


def _cfg(spec):
    out = {}
    for kv in [x for x in spec.split(",") if x]:
        k, v = kv.split("=", 1)
        out[k] = (v == "true") if v in ("true", "false") else float(v)
    return out


def run(key, zone, ticks, intensity, dist, extra, lo, hi, label,
        ext_dist=EXT_DIST_M):
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(key, N_CLIENTS, N_CREWS, seed=SEED,
                         cfg_overrides=dict(PAGE_CFG, **extra), fire_zone=zone,
                         n_crews_1h=N_1H, n_crews_3h=N_3H)
    _ignite(s, loc, zone, intensity, dist, ext_dist)
    print(f"\n===== {label}: {key} {zone} cfg {extra}")
    for k in range(1, ticks + 1):
        n_notes = len(s._notes)
        st = s.tick()
        if not (lo <= k <= hi):
            continue
        now = s._now_s
        rows = {o["crew_id"]: o for o in st["crew_overruns"]}
        print(f"-- tick {k} (T+{(now - s._t0_s) / 3600.0:.2f} h) served "
              f"{st['scoreboard']['served']} reloc {st['scoreboard']['relocations']}"
              f" left {st['scoreboard'].get('crews_left_map', 0)}")
        for c in st["crews"]:
            cs = s._crew_sims[c["crew_id"]]
            nom, p10 = s._crew_exposure(cs.pos)
            age = None if math.isinf(nom) else round((now - nom) / 60.0, 1)
            m = None if math.isinf(p10) else round((p10 - now) / 60.0, 1)
            tgt = cs.egress_loc
            tz = c.get("egress_zone_id") or ""
            dist_t = (None if tgt is None
                      else round(geo.haversine_m(cs.pos, tgt) / 1000.0, 2))
            row = rows.get(c["crew_id"])
            print(f"   {c['crew_id']:<7} {cs._mode:<7} {c['status']:<10} "
                  f"({cs.pos.lat:.5f},{cs.pos.lon:.5f}) burn_age_min={age} "
                  f"p10-now_min={m} tgt={tz}:{dist_t}km "
                  f"reloc={c.get('relocate_reason')} oldblack={c.get('on_old_black')} "
                  f"left={c.get('left_map')} "
                  f"row={(row['reason'], row['mode']) if row else None} "
                  f"itin={[x['client_id'] for x in c['itinerary']]}")
        for n in s._notes[n_notes:]:
            print(f"   NOTE {n[:240]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--zone", default="standard")
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=40)
    ap.add_argument("--ticks", type=int, default=40)
    ap.add_argument("--intensity", default="high")
    ap.add_argument("--dist", type=float, default=7000.0)
    ap.add_argument("--ext-dist", type=float, default=EXT_DIST_M)
    ap.add_argument("--arm-a", default="old_black_refuge=false,left_map_out=false,"
                                       "escape_prefer_on_map=false")
    ap.add_argument("--arm-b", default="")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    for label, spec in (("A", a.arm_a), ("B", a.arm_b)):
        run(a.key, a.zone, a.ticks, a.intensity, a.dist, _cfg(spec), a.lo, a.hi,
            label, ext_dist=a.ext_dist)


if __name__ == "__main__":
    main()
