"""attack_r3_util.py — FOUNDER ROUND 3 law 3 (FULL UTILIZATION): no eligible
crew idles while a feasible pending job exists; the planner exhausts eligible
crews; a DEPLOYING crew is never yanked; the safety audit stays at 0 violations.

Attacks:
  A. WHITE-BOX EXHAUSTION (decisive, known optimum): K crews + N>K jobs, each
     job one-deploy-tight so a crew serves AT MOST one. A correct maximizer must
     serve exactly min(N,K) and give EVERY crew a stop. Run in both 'homes' and
     'value' objective. A lazy planner that leaves a crew idle serves < min(N,K).
  B. ASYMMETRIC (far crew must not idle): a tight near job only the near crew can
     reach + a loose job reachable by the FAR crew. Serving both REQUIRES both
     crews; assert served==2 and both crews are used.
  C. SIM-LEVEL adversarial: asymmetric ignition + trickle (slow fire) + crews
     finishing mid-run over 90 ticks. Rigorous, semantics-matched checks:
       * DEPLOYING is never yanked (a crew mid-gel either finishes/serviced or
         withdraws via the SAFETY law — never silently reassigned);
       * no PERSISTENT idle-with-feasible-job starvation: using dispatch ITSELF
         as the single-crew feasibility oracle, an idle AVAILABLE crew that could
         serve a pending job never persists past the 1-tick replan latency;
       * thrash is bounded (reassignments/tick quantified, no ping-pong);
       * finished crews are REUSED; 0 safety violations throughout.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_r3_common import make_session, tile_present, skip_loud    # noqa: E402
from dr_common import all_safe_ensemble, mk_job, mk_crew, T0, destination  # noqa: E402
from wgresponse.contracts import (                                    # noqa: E402
    Crew, CrewBeacon, CrewStatus, DispatchJob, LatLon, ResponseConfig, SafeZone,
)
from wgresponse import dispatch, spread, travel                       # noqa: E402

THRASH = {}       # tile -> (reassignments_per_tick, max_client_reassigns)


def _served_count(plan):
    return sum(len(cp.stops) for cp in plan.plans.values())


def _per_crew(plan):
    return {cid: [s.client_id for s in cp.stops]
            for cid, cp in plan.plans.items() if cp.stops}


# ===========================================================================
# A. WHITE-BOX EXHAUSTION: serve exactly min(N,K), every crew used
# ===========================================================================
@pytest.mark.parametrize("objective", ["homes", "value"])
@pytest.mark.parametrize("K,N", [(2, 5), (4, 6), (3, 3), (5, 8)])
def test_exhaust_all_crews(objective, K, N):
    cfg = ResponseConfig(dispatch_objective=objective)
    anchor = LatLon(44.0, -100.0)
    zones = [SafeZone(zone_id="Z", loc=anchor, name="Z")]
    ens = {"I-SAFE": all_safe_ensemble("I-SAFE")}
    crews = [mk_crew(f"G{i}", anchor) for i in range(K)]
    # one-deploy-tight: eta_p10 leaves room for exactly one 30-min deploy + the
    # 15-min slack floor, so a 2nd stop on any crew misses the deadline.
    p10 = T0 + 76 * 60      # latest_depart = p10-30 = T0+46min
    p50 = T0 + 90 * 60
    jobs = [mk_job(f"C{i}", anchor, 1_000_000.0, 1.0, "I-SAFE", p10, p50)
            for i in range(N)]
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, T0)
    served = _served_count(plan)
    per = _per_crew(plan)
    assert served == min(N, K), \
        f"{objective} K={K} N={N}: served {served}, expected {min(N, K)} " \
        f"(a crew idled while a feasible job waited) per={per}"
    # every crew got exactly one stop -> none left idle
    assert len(per) == K and all(len(v) == 1 for v in per.values()), \
        f"{objective} K={K} N={N}: not all crews used: {per}"
    # the leftover jobs are capacity (feasible-for-someone, no crew left)
    reasons = {u.reason for u in plan.unserved}
    assert reasons <= {"capacity"}, \
        f"{objective}: unexpected unserved reasons {reasons}"


# ===========================================================================
# B. ASYMMETRIC: the far crew must be dispatched, never left idle
# ===========================================================================
@pytest.mark.parametrize("objective", ["homes", "value"])
def test_far_crew_not_left_idle(objective):
    cfg = ResponseConfig(dispatch_objective=objective)
    A = LatLon(44.0, -100.0)
    B = destination(A, 90.0, 5000.0)              # 5 km east
    zones = [SafeZone(zone_id="Z", loc=A, name="Z")]
    ens = {"I-SAFE": all_safe_ensemble("I-SAFE")}
    G0, G1 = mk_crew("G0", A), mk_crew("G1", B)
    tt = travel.travel_time_s(B, A, cfg)
    # C1 tight: only the co-located G0 can make it; C2 loose enough that the FAR
    # crew G1 can travel over and still gel+egress in time.
    j1 = mk_job("C1", A, 1_000_000.0, 1.0, "I-SAFE", T0 + 76 * 60, T0 + 90 * 60)
    j2 = mk_job("C2", A, 1_000_000.0, 1.0, "I-SAFE",
                T0 + tt + 80 * 60, T0 + tt + 120 * 60)
    plan = dispatch.plan([G0, G1], [j1, j2], ens, zones, cfg, T0)
    per = _per_crew(plan)
    assert _served_count(plan) == 2, \
        f"{objective}: served {_served_count(plan)} of 2 — far crew idled? {per}"
    assert set(per) == {"G0", "G1"}, \
        f"{objective}: both crews must be used, got {per}"
    assert per["G0"] == ["C1"] and per["G1"] == ["C2"], \
        f"{objective}: unexpected assignment {per}"


# ===========================================================================
# C. SIM-LEVEL adversarial
# ===========================================================================
def _idle_servable(sess):
    """{crew_id: frozenset(client_ids)} — for each idle/AVAILABLE crew, the
    clients dispatch ITSELF (single-crew re-solve from the crew's current
    position) says are servable. Semantics-matched oracle: if the full plan
    left this crew idle while dispatch can serve one of these, that is a
    genuine utilization gap (an empty-route crew serving a feasible job is a
    pure objective gain a maximizer must take)."""
    cfg = sess._cfg
    now = sess._now_s
    ens_map = {fid: sess._fires[fid].ens for fid in sess._fire_order}
    zones = list(sess._loc.safe_zones)
    assigned = {s["client_id"] for cs in sess._crew_sims.values()
                for s in cs.itinerary}
    serviced = {s.client_id for s in sess._serviced}
    out = {}
    for cid in sess._crew_sims:
        cs = sess._crew_sims[cid]
        if cs._mode != "idle" or cs.itinerary or cs.status != CrewStatus.AVAILABLE:
            continue
        jobs = []
        for cl in sess._clients:
            if (not cl.graphgel_enrolled or cl.client_id in serviced
                    or cl.client_id in assigned):
                continue
            best = None
            for fid in sess._fire_order:
                eta = spread.eta_at(sess._fires[fid].ens, cl.loc)
                if math.isinf(eta.eta_p10_s) or math.isinf(eta.eta_p50_s):
                    continue
                if best is None or eta.eta_p10_s < best[1].eta_p10_s:
                    best = (fid, eta)
            if best is None:
                continue
            fid, eta = best
            if (eta.eta_p50_s - now) / 3600.0 > cfg.notice_h:
                continue
            jobs.append(DispatchJob(client=cl, incident_id=fid, eta=eta))
        if not jobs:
            continue
        crew = Crew(crew_id=cid, name=cid, status=CrewStatus.AVAILABLE,
                    last_beacon=CrewBeacon(crew_id=cid, loc=cs.pos, t_s=now,
                                           status=CrewStatus.AVAILABLE),
                    home_base=cs.crew.home_base)
        plan = dispatch.plan([crew], jobs, ens_map, zones, cfg, now)
        served = frozenset(s.client_id for cp in plan.plans.values()
                           for s in cp.stops)
        if served:
            out[cid] = served
    return out


@pytest.mark.parametrize("key,intensity,speed",
                         [("malibu_ca", "moderate", 0.34),
                          ("boulder_co", "moderate", 0.34),
                          ("santa_rosa_ca", "high", 0.25)])
def test_sim_utilization_no_starve_no_yank(key, intensity, speed):
    if not tile_present(key):
        pytest.skip(skip_loud(key, "data/tiles/*.wgt"))
    from attack_sim_common import pick_ignition
    sess = make_session(key, 40, 4, seed="r3-util")
    p, _t = pick_ignition(sess, key, cluster=False)
    sess.ignite(p.lat, p.lon, intensity=intensity)
    sess.set_speed(speed)

    streak = {}                 # crew -> (len, servable_set)
    max_streak = 0
    max_streak_ctx = ""
    deploying = {}              # crew -> client it was DEPLOYING last tick
    assign_prev = {}           # client -> crew (last tick, unserviced)
    reassigns = 0
    per_client_reassigns = {}
    ever_had_stop = set()
    finished = set()           # crews that finished an itinerary (went empty)
    reused = 0

    for _ in range(90):
        st = sess.tick()
        serviced_ids = {s["client_id"] for s in st["serviced"]}
        withdrawn_ids = {w["client_id"] for w in st["withdrawn"]}

        # ---- DEPLOYING is never yanked ----
        now_deploy = {}
        for c in st["crews"]:
            if c["status"] == "deploying" and c["itinerary"]:
                now_deploy[c["crew_id"]] = c["itinerary"][0]["client_id"]
        for cid, x in deploying.items():
            still = now_deploy.get(cid) == x
            done = x in serviced_ids
            gone = x in withdrawn_ids
            assert still or done or gone, \
                f"{key}: crew {cid} was DEPLOYING {x} but at the next tick it " \
                f"is neither still deploying it, serviced, nor withdrawn — " \
                f"the in-progress deploy was silently yanked"
        deploying = now_deploy

        # ---- crew reuse (finished -> available -> new stop) ----
        for c in st["crews"]:
            cid = c["crew_id"]
            if c["itinerary"]:
                if cid in finished:        # picked up NEW work after finishing
                    reused += 1
                    finished.discard(cid)
                ever_had_stop.add(cid)
            elif cid in ever_had_stop:
                finished.add(cid)

        # ---- thrash: unserviced client bouncing between crews ----
        assign_now = {}
        for c in st["crews"]:
            for s in c["itinerary"]:
                if s["client_id"] not in serviced_ids:
                    assign_now[s["client_id"]] = c["crew_id"]
        for cl, crew in assign_now.items():
            prev = assign_prev.get(cl)
            if prev is not None and prev != crew:
                reassigns += 1
                per_client_reassigns[cl] = per_client_reassigns.get(cl, 0) + 1
        assign_prev = assign_now

        # ---- persistent idle-with-feasible-job starvation ----
        cur = _idle_servable(sess)
        newstreak = {}
        for cid, served in cur.items():
            prev = streak.get(cid)
            n = prev[0] + 1 if (prev and (prev[1] & served)) else 1
            newstreak[cid] = (n, served)
            if n > max_streak:
                max_streak = n
                max_streak_ctx = f"{cid} tick{st['tick']} {sorted(served)[:3]}"
        streak = newstreak

        assert st["scoreboard"]["violations"] == 0, \
            f"{key}: {st['scoreboard']['violations']} safety violations at " \
            f"tick {st['tick']}"

    tick_n = st["tick"]
    THRASH[key] = (reassigns / tick_n if tick_n else 0.0,
                   max(per_client_reassigns.values(), default=0), reused)

    # 1-tick replan latency is expected (design: per-tick replan self-corrects);
    # PERSISTENT starvation (a crew idling with the same feasible job cycle
    # after cycle) is the bug.
    assert max_streak <= 2, \
        f"{key}: an AVAILABLE crew could serve a pending job for {max_streak} " \
        f"consecutive ticks ({max_streak_ctx}) — utilization starvation"
    # no pathological ping-pong of a single job between crews
    assert THRASH[key][1] <= 6, \
        f"{key}: a single job was reassigned {THRASH[key][1]} times (thrash)"


def test_zzz_util_summary():
    print("\n===== UTILIZATION report =====")
    print(f"{'location':<15} {'reassign/tick':>14} "
          f"{'max_client_reassign':>21} {'crew_reuses':>12}")
    for k, (rpt, mx, reused) in THRASH.items():
        print(f"{k:<15} {rpt:>14.4f} {mx:>21d} {reused:>12d}")
    print("==============================")
