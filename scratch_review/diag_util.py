"""DIAGNOSIS ONLY (Defect 2 — crews not consistently utilized; all should
work until the threat passes).

Runs the REAL SimSession and measures, per tick and per crew:
  * busy vs idle (AVAILABLE) while the threat is active
  * serviced-stop distribution across crews (concentration)
  * idle-crew-ticks while >=1 job sits UNSERVED for 'capacity'
  * whether a crew that finishes + egresses ever picks up NEW work

Two scenarios (task): <loc> 40 clients / 4 crews, and 80 clients / 8 crews.
Read-only; drives SimSession in-process (no server, no disk writes).
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import simui
from wgresponse.fuels import is_burnable
import wgresponse.geo as geo

TILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "tiles")
BUSY = {"en_route", "deploying", "egressing"}   # CrewStatus.value strings


def _ignition(loc, tile):
    for dist in (6000, 5000, 7000, 4000, 8000):
        for dd in (0, 20, -20, 40, -40):
            a = math.radians(loc.hazard_wind_dir_deg + dd)
            dlat = (dist * math.cos(a)) / 111_000.0
            dlon = (dist * math.sin(a)) / (111_000.0 * math.cos(math.radians(loc.center.lat)))
            p = geo.LatLon(loc.center.lat + dlat, loc.center.lon + dlon)
            rc = geo.latlon_to_rc(tile.spec, p)
            if rc and is_burnable(int(round(tile.fuel.get(*rc)))):
                return p
    raise RuntimeError("no burnable ignition found")


def run(key, n_clients, n_crews, ticks=60, intensity="high"):
    print("\n" + "=" * 78)
    print(f"SCENARIO {key}  {n_clients} clients / {n_crews} crews / {intensity} "
          f"/ {ticks} ticks (900 s each)")
    print("=" * 78)
    loc = simui.LOCATIONS[key]
    sess = simui.SimSession(key, n_clients, n_crews, seed="demo", tile_dir=TILE_DIR)
    p = _ignition(loc, sess._tile)
    sess.ignite(p.lat, p.lon, intensity=intensity)

    busy_ticks = defaultdict(int)      # crew_id -> ticks busy
    threat_ticks = 0                   # ticks with >=1 active job (pending or in flight)
    idle_while_capacity = 0            # crew-ticks idle while a 'capacity' job waits
    idle_while_any_unserved = 0        # crew-ticks idle while ANY job unserved (not serviced)
    first_avail_after_work = {}        # crew -> tick it first returned AVAILABLE having worked
    picked_up_after_idle = set()       # crews that got NEW stop after being idle-post-work
    ever_busy = set()
    prev_itin = defaultdict(int)

    for t in range(ticks):
        st = sess.tick()
        crews = st["crews"]
        sb = st["scoreboard"]
        unserved = st["unserved"]
        serviced_ids = {s["client_id"] for s in st["serviced"]}
        # active threat = any client jobbed (unserved list non-empty) or any crew busy
        cap_jobs = [u for u in unserved if u["reason"] == "capacity"]
        pending_any = [u for u in unserved if u["reason"] not in ("fire_arrived",)]
        any_busy = any(c["status"] in BUSY for c in crews)
        threat_on = bool(unserved) or any_busy or sb["served"] > 0
        if threat_on:
            threat_ticks += 1
        for c in crews:
            cid = c["crew_id"]
            itin = len(c["itinerary"])
            if c["status"] in BUSY:
                busy_ticks[cid] += 1
                ever_busy.add(cid)
            else:
                # idle this tick
                if cap_jobs:
                    idle_while_capacity += 1
                if pending_any:
                    idle_while_any_unserved += 1
                # returned to AVAILABLE having previously worked?
                if cid in ever_busy and cid not in first_avail_after_work:
                    first_avail_after_work[cid] = t
            # did a crew that had gone idle-after-work pick up a NEW stop?
            if (cid in first_avail_after_work and t > first_avail_after_work[cid]
                    and itin > 0):
                picked_up_after_idle.add(cid)
            prev_itin[cid] = itin

    # per-crew serviced count
    st = sess.state()
    per_crew_serviced = defaultdict(int)
    for s in st["serviced"]:
        per_crew_serviced[s["crew_id"]] += 1
    all_crew_ids = [c["crew_id"] for c in st["crews"]]
    counts = [per_crew_serviced[c] for c in all_crew_ids]
    zero_crews = [c for c in all_crew_ids if per_crew_serviced[c] == 0]

    print(f"  threat-active ticks: {threat_ticks}/{ticks}")
    print(f"  scoreboard: served={st['scoreboard']['served']} "
          f"unserved={sum(1 for u in st['unserved'])} "
          f"withdrawn={st['scoreboard']['withdrawn']} "
          f"violations={st['scoreboard']['violations']}")
    print(f"  unserved by reason: {st['scoreboard']['unserved_by_reason']}")
    print(f"\n  serviced stops per crew: "
          f"{ {c: per_crew_serviced[c] for c in all_crew_ids} }")
    print(f"    total={sum(counts)}  max={max(counts)}  min={min(counts)}  "
          f"crews with 0 stops: {len(zero_crews)}/{len(all_crew_ids)}")
    print(f"  busy-tick fraction per crew (of threat window):")
    for c in all_crew_ids:
        frac = busy_ticks[c] / max(1, threat_ticks)
        print(f"    {c}: busy {busy_ticks[c]:3d}/{threat_ticks} = {frac*100:5.1f}%")
    print(f"\n  idle-crew-ticks while a 'capacity' job waits: {idle_while_capacity}")
    print(f"  idle-crew-ticks while ANY job unserved:        {idle_while_any_unserved}")
    print(f"  crews that returned AVAILABLE after working:   "
          f"{sorted(first_avail_after_work)}")
    print(f"  ...of those, crews that later got NEW work:    "
          f"{sorted(picked_up_after_idle)}")


if __name__ == "__main__":
    run("malibu_ca", 40, 4)
    run("malibu_ca", 80, 8)
    run("boulder_co", 40, 4)
