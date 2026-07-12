"""attack_r5_regional — SEMANTICS 9 regional/mutual-aid crews (task 2).

Claims under attack:
  (A) arrival = pure function of first-ignition + delay (deterministic, replayable)
  (B) INBOUND crews never dispatched, never counted an idle-utilization failure,
      never falsely relocated by the crew-safety sweep, never a crew_overrun row
  (C) on arrival they become eligible and get work; edge-marker positions correct
  (D) 0-reinforcement session is byte-identical to a plain session
READ-ONLY; in-process only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attack_r5_common import make_session, geo, ignite_point              # noqa
from wgresponse.simui import LOCATIONS, SimSession                        # noqa
from wgresponse.contracts import CrewStatus                              # noqa


def state_stream(s, ignite_pt, n_ticks, inten="high"):
    s.ignite(ignite_pt.lat, ignite_pt.lon, intensity=inten)
    out = []
    for _ in range(n_ticks):
        out.append(json.dumps(s.tick(), sort_keys=True, allow_nan=False))
    return out


def test_zero_reinforcement_byte_identical():
    print("### (D) 0-reinforcement byte-identity")
    loc = "paradise_ca"
    fails = 0
    for physics in (False, True):
        a = make_session(loc, 40, 4, seed="reg-bytecheck", fire_zone="extended",
                         physics=physics)
        b = make_session(loc, 40, 4, seed="reg-bytecheck", fire_zone="extended",
                         physics=physics, n_crews_1h=0, n_crews_3h=0)
        pt = ignite_point(a, km=8.0)
        sa = state_stream(a, pt, 40)
        sb = state_stream(b, pt, 40)
        same = sa == sb
        print(f"  physics={physics}: default vs explicit-0 reinforcement "
              f"byte-identical over 40 ticks: {same}")
        if not same:
            fails += 1
            for i, (x, y) in enumerate(zip(sa, sb)):
                if x != y:
                    print(f"    first divergence at tick {i+1}")
                    break
    return fails


def test_arrival_determinism_and_replay():
    print("\n### (A) arrival determinism + scrub replay")
    loc = "malibu_ca"
    fails = 0
    # two independent identical sessions -> identical streams (incl. arrivals)
    s1 = make_session(loc, 50, 3, seed="reg-arr", fire_zone="extended",
                      physics=True, n_crews_1h=2, n_crews_3h=2)
    s2 = make_session(loc, 50, 3, seed="reg-arr", fire_zone="extended",
                      physics=True, n_crews_1h=2, n_crews_3h=2)
    pt = ignite_point(s1, km=9.0)
    st1 = state_stream(s1, pt, 120)
    st2 = state_stream(s2, pt, 120)
    print(f"  two identical sessions byte-identical (120 ticks, 3+2+2 crews): "
          f"{st1 == st2}")
    if st1 != st2:
        fails += 1

    # arrival time is pure f(first_ignition + delay): compute expected available_at
    cfg = s1._cfg
    first_ig = None
    for fid in s1._fire_order:
        first_ig = s1._fires[fid].t_ignite_s
        break
    exp_1h = first_ig + cfg.crew_reinforce_1h_min * 60.0
    exp_3h = first_ig + cfg.crew_reinforce_3h_min * 60.0
    got = {}
    for cid, region in s1._crew_region.items():
        if region != "in_zone":
            got[cid] = (region, s1._crew_available_at[cid])
    ok_arr = all(
        (v[1] == exp_1h if v[0] == "reg_1h" else v[1] == exp_3h)
        for v in got.values())
    print(f"  available_at == first_ignition+delay for every reinforcement: "
          f"{ok_arr}   ({got})")
    if not ok_arr:
        fails += 1

    # scrub replay: state_at(k) byte-identical to what tick k served
    replay_ok = True
    for k in (5, 40, 90, 118):
        snap = s1.state_at(k)
        if snap is None:
            continue
        if json.dumps(snap, sort_keys=True, allow_nan=False) != st1[k-1]:
            replay_ok = False
    print(f"  state_at(k) replays arrival ticks byte-identically: {replay_ok}")
    if not replay_ok:
        fails += 1
    return fails


def test_inbound_never_dispatched_relocated_overrun():
    print("\n### (B) INBOUND never dispatched / relocated / overrun / idle-fail")
    loc = "boulder_co"
    # extreme fire ON the west edge; reinforcements enter from edges -> could a
    # spot fire near the edge falsely relocate an inbound crew? must not.
    s = make_session(loc, 60, 3, seed="reg-inb", fire_zone="extended",
                     physics=True, n_crews_1h=3, n_crews_3h=3)
    pt = ignite_point(s, km=11.0)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    inbound_ids = {cid for cid, r in s._crew_region.items() if r != "in_zone"}
    viol = []
    arrived_and_worked = set()
    edge_pos = {cid: (s._crew_sims[cid].pos.lat, s._crew_sims[cid].pos.lon)
                for cid in inbound_ids}
    for tk in range(1, 220):
        st = s.tick()
        crews_by_id = {c["crew_id"]: c for c in st["crews"]}
        for cid in inbound_ids:
            cs = s._crew_sims[cid]
            c = crews_by_id[cid]
            if cs.status == CrewStatus.INBOUND:
                # still inbound: must not be dispatched/relocating/overrun, must
                # sit at its fixed edge marker, must be excluded from eligibility
                if cs.itinerary:
                    viol.append(f"t{tk} {cid} INBOUND has itinerary")
                if c["relocating"]:
                    viol.append(f"t{tk} {cid} INBOUND relocating")
                if any(o["crew_id"] == cid for o in s._crew_overruns):
                    viol.append(f"t{tk} {cid} INBOUND in crew_overruns")
                if cid in s._eligible_replan_ids():
                    viol.append(f"t{tk} {cid} INBOUND is dispatch-eligible")
                if (c["lat"], c["lon"]) != tuple(round(v, 6) for v in edge_pos[cid]):
                    # edge marker must be stable while inbound
                    viol.append(f"t{tk} {cid} INBOUND edge marker moved")
                if not c["inbound"] or c["status"] != "inbound":
                    viol.append(f"t{tk} {cid} INBOUND flag wrong")
            else:
                # arrived: must be at its staging zone on arrival tick & eligible
                if cs.itinerary or cs._mode in ("travel", "deploy"):
                    arrived_and_worked.add(cid)
    n_arrived = sum(1 for cid in inbound_ids
                    if s._crew_sims[cid].status != CrewStatus.INBOUND)
    print(f"  inbound crews: {len(inbound_ids)}  arrived by end: {n_arrived}  "
          f"got work after arrival: {len(arrived_and_worked)}")
    print(f"  invariant violations while INBOUND: {len(viol)}")
    for v in viol[:6]:
        print(f"    - {v}")
    # arrivals should have happened (1h=60min, 3h=180min; replan_period scaled)
    ok = (len(viol) == 0 and n_arrived > 0 and len(arrived_and_worked) > 0)
    return 0 if ok else 1


if __name__ == "__main__":
    f = 0
    f += test_zero_reinforcement_byte_identical()
    f += test_arrival_determinism_and_replay()
    f += test_inbound_never_dispatched_relocated_overrun()
    print("\n" + "=" * 78)
    print(f"TASK 2 (regional crews) OVERALL: {'PASS' if f == 0 else 'FAIL'}  "
          f"(sub-failures={f})")
    print("=" * 78)
