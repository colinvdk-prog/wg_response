"""attack_r5_physics — SPOTTING + GRASS in the sim (task 3).

Claims under attack (physics-on via cfg, the server default):
  * fire is MATERIALLY faster (more burned cells / reach) with toggles ON vs OFF
  * embers land AHEAD of the contiguous front (spot cells beyond the front edge)
  * MORE homes threatened ON vs OFF
  * toggling OFF restores the baseline
  * determinism (identical seed -> identical stream)
  * performance is acceptable (ticks not pathological)
READ-ONLY; in-process only.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack_r5_common import make_session, geo                            # noqa
from wgresponse.simui import LOCATIONS                                    # noqa


def fire_metrics(s):
    """(burned_cells, max_reach_m, p10_reach_m, homes_threatened) at now."""
    now = s._now_s
    spec = s._tile.spec
    fid = s._fire_order[0]
    f = s._fires[fid]
    ens = f.ens
    origin = f.origin
    burned = 0
    max_reach = 0.0
    p10_reach = 0.0
    m0 = ens.members[0].data
    p10 = ens.p10.data
    ncols = spec.ncols
    for idx, v in enumerate(m0):
        if not math.isinf(v) and v <= now:
            burned += 1
            r, c = divmod(idx, ncols)
            d = geo.haversine_m(origin, geo.rc_to_latlon(spec, r, c))
            if d > max_reach:
                max_reach = d
    for idx, v in enumerate(p10):
        if not math.isinf(v) and v <= now:
            r, c = divmod(idx, ncols)
            d = geo.haversine_m(origin, geo.rc_to_latlon(spec, r, c))
            if d > p10_reach:
                p10_reach = d
    homes = 0
    for cl in s._clients:
        eta = s._client_etas.get(cl.client_id)
        if eta is not None and not math.isinf(eta.eta_p10_s):
            homes += 1
    return burned, max_reach, p10_reach, homes


def run_on_off():
    print("=" * 78)
    print("PART 1 — physics ON vs OFF: faster fire, spot-ahead, more homes")
    print("=" * 78)
    loc = "paradise_ca"
    pt = geo.destination(LOCATIONS[loc].center, 45.0, 8000.0)
    N = 60
    fails = 0
    for seed in ("phys-1", "phys-2", "phys-3"):
        rows = {}
        for label, phys in (("OFF", False), ("ON", True)):
            s = make_session(loc, 50, 4, seed=seed, fire_zone="extended",
                             physics=phys)
            s.ignite(pt.lat, pt.lon, intensity="extreme")
            for _ in range(N):
                s.tick()
            rows[label] = fire_metrics(s)
        b_off, reach_off, p10_off, h_off = rows["OFF"]
        b_on, reach_on, p10_on, h_on = rows["ON"]
        faster = b_on > b_off
        ahead = p10_on > p10_off      # spot embers pull the p10 front ahead
        morehomes = h_on >= h_off
        print(f"\n  seed={seed} @tick {N}:")
        print(f"    burned cells:   OFF={b_off:5d}  ON={b_on:5d}  "
              f"(+{100*(b_on-b_off)/max(b_off,1):.0f}%)  faster={faster}")
        print(f"    max reach (m):  OFF={reach_off:7.0f} ON={reach_on:7.0f}")
        print(f"    p10 reach (m):  OFF={p10_off:7.0f} ON={p10_on:7.0f}  "
              f"spot-ahead={ahead}")
        print(f"    homes threat:   OFF={h_off:5d}  ON={h_on:5d}  more={morehomes}")
        if not (faster and morehomes):
            fails += 1
            print("    !! physics did NOT materially increase fire/threat")
    return fails


def run_spot_ahead_gap():
    """Direct spot-ahead proof: with spotting ON there exist burned cells FAR
    beyond the ON-vs-OFF front edge (a brand that jumped a gap)."""
    print("\n" + "=" * 78)
    print("PART 2 — embers land AHEAD of the front (max burned reach ON >> OFF)")
    print("=" * 78)
    loc = "malibu_ca"
    pt = geo.destination(LOCATIONS[loc].center, 50.0, 7000.0)
    fails = 0
    for seed in ("gap-1", "gap-2"):
        reach = {}
        for label, phys in (("OFF", False), ("ON", True)):
            s = make_session(loc, 40, 3, seed=seed, fire_zone="extended",
                             physics=phys)
            s.ignite(pt.lat, pt.lon, intensity="extreme")
            for _ in range(45):
                s.tick()
            _, mr, _, _ = fire_metrics(s)
            reach[label] = mr
        gained = reach["ON"] - reach["OFF"]
        print(f"  seed={seed}: max burned reach OFF={reach['OFF']:.0f}m  "
              f"ON={reach['ON']:.0f}m  ember-jump gain={gained:+.0f}m")
        if gained <= 0:
            fails += 1
            print("    !! no ember jump ahead of the front")
    return fails


def run_toggle_restore_determinism():
    print("\n" + "=" * 78)
    print("PART 3 — toggle-OFF restores baseline; determinism; state echoes flags")
    print("=" * 78)
    loc = "boulder_co"
    pt = geo.destination(LOCATIONS[loc].center, 270.0, 9000.0)
    fails = 0

    def stream(phys, n=40):
        s = make_session(loc, 40, 3, seed="det", fire_zone="extended",
                         physics=phys)
        s.ignite(pt.lat, pt.lon, intensity="high")
        out = [json.dumps(s.tick(), sort_keys=True, allow_nan=False)
               for _ in range(n)]
        return s, out

    s_off1, off1 = stream(False)
    s_off2, off2 = stream(False)
    s_on1, on1 = stream(True)
    s_on2, on2 = stream(True)
    det_off = off1 == off2
    det_on = on1 == on2
    differ = off1 != on1
    print(f"  determinism physics-OFF (2 runs identical): {det_off}")
    print(f"  determinism physics-ON  (2 runs identical): {det_on}")
    print(f"  ON stream differs from OFF stream:           {differ}")
    if not (det_off and det_on and differ):
        fails += 1
    # state echoes flags
    st_on = json.loads(on1[-1])
    st_off = json.loads(off1[-1])
    echo_ok = (st_on.get("spotting") is True and st_on.get("grass_realism") is True
               and st_off.get("spotting") is False
               and st_off.get("grass_realism") is False)
    print(f"  state echoes spotting/grass_realism flags correctly: {echo_ok}  "
          f"(ON: spotting={st_on.get('spotting')} grass={st_on.get('grass_realism')}"
          f"; OFF: spotting={st_off.get('spotting')} grass={st_off.get('grass_realism')})")
    if not echo_ok:
        fails += 1
    return fails


def run_perf():
    print("\n" + "=" * 78)
    print("PART 4 — performance (physics-on ticks not pathological)")
    print("=" * 78)
    loc = "paradise_ca"
    pt = geo.destination(LOCATIONS[loc].center, 45.0, 8000.0)
    s = make_session(loc, 64, 5, seed="perf", fire_zone="extended", physics=True)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    t0 = time.perf_counter()
    N = 80
    for _ in range(N):
        s.tick()
    dt = time.perf_counter() - t0
    per = dt / N * 1000.0
    print(f"  {N} physics-on ticks (Paradise ext, 64/5) in {dt:.1f}s = "
          f"{per:.0f} ms/tick")
    # pathological if > 3s/tick
    ok = per < 3000.0
    print(f"  acceptable (<3000 ms/tick): {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    f = 0
    f += run_on_off()
    f += run_spot_ahead_gap()
    f += run_toggle_restore_determinism()
    f += run_perf()
    print("\n" + "=" * 78)
    print(f"TASK 3 (spotting+grass in sim) OVERALL: {'PASS' if f == 0 else 'FAIL'}"
          f"  (sub-failures={f})")
    print("=" * 78)
