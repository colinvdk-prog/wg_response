"""diag_simui_workload.py — DEFECT 2: per-crew utilization + dispatch concentration.

Measures on real-tile scenarios (malibu_ca 40/4, malibu_ca 80/8, boulder_co
40/4 for the founder-scenario reference; high fire at the pinned gate
ignitions, 60 ticks = 15 sim hours):

  * per-crew: stops served, busy ticks (EN_ROUTE/DEPLOYING/EGRESSING at tick
    boundary), adoptions (dispatch handed it work), max queue length;
  * threat-active window: first tick with >= 1 dispatchable job .. last tick
    with >= 1 dispatchable job or busy crew;
  * IDLE-WHILE-WORK-EXISTS ticks, split:
      - idle_pending:  crew idle() while >= 1 dispatchable job is UNASSIGNED
        (plan() could have taken it but didn't / wasn't able);
      - idle_others_queued: crew idle() while no unassigned job exists but
        some OTHER crew holds >= 2 not-yet-started stops (rebalance gap —
        assigned clients are excluded from the job pool by tick() step 4);
  * dispatch.plan call census: how many jobs/idle-crews each call saw, and
    which crews the winning plan used (concentration evidence);
  * job trickle: new jobbed clients per tick.

Diagnosis only — read-only pokes of session internals + spy wrappers.
"""
import os
import sys
import math
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, spread
from wgresponse.simui import SimSession, _SimCrew

TICKS = 60


def dispatchable_jobs(sess):
    """Replicate tick() step 4's job filter (read-only): clients that would
    be offered to dispatch.plan THIS tick if a crew were idle."""
    cfg = sess._cfg
    now = sess._now_s
    serviced = {s.client_id for s in sess._serviced}
    out = set()
    for fid in sess._fire_order:
        ens = sess._fires[fid].ens
        if ens is None:
            continue
        for cl in sess._clients:
            if cl.client_id in sess._assigned or cl.client_id in serviced:
                continue
            eta = spread.eta_at(ens, cl.loc)
            if math.isinf(eta.eta_p50_s) or math.isinf(eta.eta_p10_s):
                continue
            if (eta.eta_p50_s - now) / 3600.0 > cfg.notice_h:
                continue
            if eta.eta_p50_s <= now:
                continue                      # fire_arrived
            out.add(cl.client_id)
    return out


def run(key, ig, n_clients, n_crews, label):
    print(f"\n========== {label}: {key} {n_clients} clients / {n_crews} crews "
          f"/ high / {TICKS} ticks ==========")
    plan_calls = []
    orig_plan = dispatch.plan

    def spy_plan(crews, jobs, ens, zones, cfg, now_s, incumbent=None, forecasts=None):
        p = orig_plan(crews, jobs, ens, zones, cfg, now_s, incumbent, forecasts=forecasts)
        used = {cid: len(cp.stops) for cid, cp in p.plans.items() if cp.stops}
        plan_calls.append({
            "n_jobs": len(jobs), "n_crews": len(crews),
            "assigned": used, "incumbent": incumbent is not None,
            "unserved": {u.reason: 1 for u in p.unserved},
        })
        return p

    adoptions = defaultdict(list)
    orig_adopt = _SimCrew.adopt

    def spy_adopt(self, stops, egress_loc, egress_zone_id):
        adoptions[self.crew.crew_id].append(len(stops))
        orig_adopt(self, stops, egress_loc, egress_zone_id)

    dispatch.plan = spy_plan
    _SimCrew.adopt = spy_adopt
    try:
        sess = SimSession(key, n_clients, n_crews, seed="demo")
        sess.ignite(ig[0], ig[1], "high")
        busy = defaultdict(int)
        idle_pending = defaultdict(int)
        idle_others_q = defaultdict(int)
        maxq = defaultdict(int)
        jobbed_seen = set()
        trickle = []
        active_ticks = []
        for t in range(1, TICKS + 1):
            st = sess.tick()
            pend = dispatchable_jobs(sess)
            new_jobbed = set(sess._jobbed) - jobbed_seen
            jobbed_seen |= set(sess._jobbed)
            trickle.append(len(new_jobbed))
            crews_busy = 0
            for cid, cs in sess._crew_sims.items():
                q = len(cs.itinerary)
                maxq[cid] = max(maxq[cid], q)
                if not cs.idle():
                    busy[cid] += 1
                    crews_busy += 1
            for cid, cs in sess._crew_sims.items():
                if cs.idle():
                    if pend:
                        idle_pending[cid] += 1
                    elif any(len(o.itinerary) >= 2 for k, o in
                             sess._crew_sims.items() if k != cid):
                        idle_others_q[cid] += 1
            if pend or crews_busy:
                active_ticks.append(t)
        served = defaultdict(int)
        for s in sess._serviced:
            served[s.crew_id] += 1
        t_lo = active_ticks[0] if active_ticks else 0
        t_hi = active_ticks[-1] if active_ticks else 0
        window = max(1, t_hi - t_lo + 1)
        print(f"threat-active window: ticks {t_lo}..{t_hi} ({window} ticks); "
              f"jobbed clients total {len(jobbed_seen)}; "
              f"served total {sum(served.values())}; withdrawn {len(sess._withdrawn)}")
        print(f"job trickle (new jobs per tick, first 20): {trickle[:20]}")
        print(f"{'crew':8s} {'served':>6s} {'busy':>5s} {'util%':>6s} "
              f"{'idle_pend':>9s} {'idle_reb':>8s} {'adoptions':>9s} {'maxq':>4s}")
        for cid in sorted(sess._crew_sims, key=lambda s: (len(s), s)):
            print(f"{cid:8s} {served[cid]:6d} {busy[cid]:5d} "
                  f"{100.0*busy[cid]/window:6.1f} {idle_pending[cid]:9d} "
                  f"{idle_others_q[cid]:8d} {len(adoptions[cid]):9d} {maxq[cid]:4d}")
        sd = [served[cid] for cid in sess._crew_sims]
        print(f"stops/crew spread: min {min(sd)} max {max(sd)}; "
              f"adoption sizes {dict((k, v) for k, v in sorted(adoptions.items()))}")
        nz = [c for c in plan_calls if c["n_jobs"]]
        print(f"plan() calls with jobs: {len(nz)}; "
              f"jobs-per-call max {max((c['n_jobs'] for c in nz), default=0)}; "
              f"crews-per-call max {max((c['n_crews'] for c in nz), default=0)}; "
              f"incumbent ever passed: {any(c['incumbent'] for c in plan_calls)}")
        multi = [c for c in nz if c["n_jobs"] >= 2 and c["n_crews"] >= 2]
        conc = [c for c in multi if len(c["assigned"]) == 1]
        print(f"multi-job multi-crew calls: {len(multi)}; of those, ALL stops "
              f"to ONE crew: {len(conc)}")
        for c in nz[:8]:
            print(f"   call: jobs={c['n_jobs']} idle_crews={c['n_crews']} "
                  f"-> assigned {c['assigned']} unserved={c['unserved']}")
        # final unserved picture
        ub = st["scoreboard"]["unserved_by_reason"]
        print(f"final unserved_by_reason: {ub}")
    finally:
        dispatch.plan = orig_plan
        _SimCrew.adopt = orig_adopt


if __name__ == "__main__":
    run("malibu_ca", (34.08967, -118.67009), 40, 4, "SCENARIO A")
    run("malibu_ca", (34.08967, -118.67009), 80, 8, "SCENARIO B")
    run("boulder_co", (39.95048, -105.23039), 40, 4, "SCENARIO C (founder ref)")
