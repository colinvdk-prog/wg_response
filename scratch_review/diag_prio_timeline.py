"""DIAGNOSIS ONLY — the decisive timeline: is the fleet losing SAVABLE homes
because it is busy on COMFORTABLE (large-lead) homes, or because the lost homes
are genuinely infeasible (corridor/deadline)?  Combines the recorded dispatch
decisions with per-tick crew state, aligned by tick.

Answers:
  Q1 UTILIZATION — how often is the whole fleet busy; idle-crew-ticks while a
     FEASIBLE (in-pool, non-infeasible) home waits.
  Q2 GEL-ORDER  — of homes actually gelled, how many had a LARGE p50 lead
     (comfortable, >gel_full_strength) at gel time; and were more-urgent homes
     feasible-but-unserved at that same tick.
  Q3 COMMITMENT-LOCK — lost homes that were FEASIBLE (served/attempted in some
     plan) at a tick when every crew was committed to a strictly-less-urgent
     (later-p50) home.
  Q4 WHY-LOST   — final reason census for every ungelled threatened home.

Read-only; drives SimSession in-process.
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "tiles")
INFEASIBLE = {"no_safe_corridor", "no_safe_egress", "infeasible_deadline",
              "infeasible_slack", "gel_window_closed", "fire_arrived"}
BUSY = {"en_route", "deploying", "egressing"}
H = 3600.0
_real_plan = dispatch.plan
PLAN_BY_TICK = {}


def nearest_burnable(sess, lat, lon):
    spec = sess._tile.spec
    r0, c0 = geo.latlon_to_rc(spec, geo.LatLon(lat, lon))
    for rad in range(40):
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                if max(abs(dr), abs(dc)) != rad:
                    continue
                r, c = r0 + dr, c0 + dc
                if 0 <= r < spec.nrows and 0 <= c < spec.ncols and \
                        is_burnable(int(round(sess._tile.fuel.get(r, c)))):
                    return geo.rc_to_latlon(spec, r, c)


def run(ign=(34.0796, -118.6803), ticks=260):
    PLAN_BY_TICK.clear()
    tick_ref = {"t": -1}

    def rec_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                 forecasts=None):
        plan = _real_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                          forecasts=forecasts)
        PLAN_BY_TICK[tick_ref["t"] + 1] = dict(
            jinfo={j.client.client_id: (j.eta.eta_p10_s, j.eta.eta_p50_s,
                                        j.client.value_usd) for j in jobs},
            served={st.client_id for cp in plan.plans.values()
                    for st in cp.stops},
            unserved={u.client_id: u.reason for u in plan.unserved})
        return plan

    simui.dispatch.plan = rec_plan
    snaps = []
    try:
        sess = simui.SimSession("malibu_ca", 48, 3, seed="sim-7", tile_dir=TILE)
        p = nearest_burnable(sess, *ign)
        sess.ignite(p.lat, p.lon, intensity="extreme")
        value = {c.client_id: c.value_usd for c in sess._clients}
        for t in range(ticks):
            tick_ref["t"] = t
            st = sess.tick()
            now = st["sim_now_s"]
            cl = {c["client_id"]: (c["eta_p10_s"], c["eta_p50_s"], c["gel_applied"])
                  for c in st["clients"]}
            crews = {cr["crew_id"]: (cr["status"],
                                     [s["client_id"] for s in cr["itinerary"]])
                     for cr in st["crews"]}
            snaps.append(dict(t=t, now=now, cl=cl, crews=crews,
                              serviced=[s["client_id"] for s in st["serviced"]]))
    finally:
        simui.dispatch.plan = _real_plan
    return sess, snaps, value


def lead_h(snap, cid):
    p50 = snap["cl"].get(cid, (None, None, None))[1]
    return None if p50 is None else (p50 - snap["now"]) / H


def main():
    sess, snaps, value = run()
    st = sess.state()
    gel_at_tick = {}                       # cid -> first tick gel_applied
    for s in snaps:
        for cid, (_p10, _p50, gel) in s["cl"].items():
            if gel and cid not in gel_at_tick:
                gel_at_tick[cid] = s["t"]
    threatened = {cid for s in snaps for cid, v in s["cl"].items()
                  if v[1] is not None}
    gelled = set(gel_at_tick)
    impacted = {}
    for s in snaps:
        for cid, (_p10, p50, _g) in s["cl"].items():
            if p50 is not None and p50 <= s["now"] and cid not in impacted:
                impacted[cid] = s["t"]

    # ---- Q1 utilization -------------------------------------------------
    all_busy = 0
    idle_while_feasible_waits = 0
    active_ticks = 0
    for s in snaps:
        pool = PLAN_BY_TICK.get(s["t"])
        threatened_now = any(v[1] is not None and not v[2]
                             for v in s["cl"].values())
        if not threatened_now:
            continue
        active_ticks += 1
        statuses = [v[0] for v in s["crews"].values()]
        nbusy = sum(1 for x in statuses if x in BUSY)
        if nbusy == len(statuses):
            all_busy += 1
        # feasible-and-waiting: a home the last plan could serve for someone
        # (served or non-infeasible unserved) but is not gelled/committed
        if pool:
            feasible_waiting = [c for c, r in pool["unserved"].items()
                                if r not in INFEASIBLE]
            n_idle = sum(1 for x in statuses if x not in BUSY)
            if n_idle > 0 and feasible_waiting:
                idle_while_feasible_waits += 1
    print(f"=== Malibu sim-7 (48/3 extreme) — {len(snaps)} ticks, "
          f"+{(snaps[-1]['now']-snaps[0]['now']+900)/H:.1f}h ===")
    print(f"\nQ1 UTILIZATION:")
    print(f"  active ticks (>=1 threatened ungelled): {active_ticks}")
    print(f"  ticks with ALL crews busy: {all_busy} "
          f"({100*all_busy/max(1,active_ticks):.0f}% of active)")
    print(f"  idle-crew ticks while a FEASIBLE home waits: {idle_while_feasible_waits}")

    # ---- Q2 gel-order: comfortable homes gelled -------------------------
    full_h = sess._cfg.gel_full_strength_h
    comfy_gelled = []       # gelled with lead > full_h (had slack to defer)
    for cid, gt in gel_at_tick.items():
        s = snaps[gt]
        lh = lead_h(s, cid)
        if lh is not None and lh > full_h:
            comfy_gelled.append((cid, gt, lh))
    print(f"\nQ2 GEL-ORDER (gel_full_strength_h={full_h:g}h):")
    print(f"  homes gelled with p50 lead > {full_h:g}h (comfortable, deferrable): "
          f"{len(comfy_gelled)} / {len(gelled)} gelled")
    for cid, gt, lh in sorted(comfy_gelled, key=lambda x: -x[2])[:8]:
        # at that gel tick, was a MORE-urgent home feasible-but-not-gelled?
        s = snaps[gt]
        pool = PLAN_BY_TICK.get(gt, {})
        urg_waiting = []
        for c2, r in pool.get("unserved", {}).items():
            l2 = lead_h(s, c2)
            if r not in INFEASIBLE and l2 is not None and l2 < lh - 6:
                urg_waiting.append((c2, round(l2, 1)))
        print(f"    {cid} (${value[cid]/1e6:.2f}M) gelled @t{gt} at lead +{lh:.1f}h"
              + (f"  <-- more-urgent feasible waiting: {urg_waiting}" if urg_waiting else ""))

    # ---- Q3 commitment-lock: lost home feasible while fleet on comfy -----
    lost = sorted(c for c in threatened if c not in gelled)
    print(f"\nQ3 COMMITMENT-LOCK (lost homes feasible while every crew on a "
          f"later-p50 home):")
    lock_events = 0
    locked_homes = set()
    for L in lost:
        for s in snaps:
            pool = PLAN_BY_TICK.get(s["t"])
            if not pool or L not in pool["jinfo"]:
                continue
            r = pool["unserved"].get(L, "served" if L in pool["served"] else "?")
            if r in INFEASIBLE or L in pool["served"]:
                continue                    # infeasible or actually taken
            lL = lead_h(s, L)
            if lL is None:
                continue
            # every crew committed to something strictly less urgent than L?
            crew_leads = []
            all_busy_less_urgent = True
            any_busy = False
            for cr, (statx, itin) in s["crews"].items():
                if statx not in BUSY or not itin:
                    all_busy_less_urgent = False
                    continue
                any_busy = True
                head = itin[0]
                lh = lead_h(s, head)
                crew_leads.append((cr, head, lh))
                if lh is None or lh <= lL + 6:      # crew on an equal/more-urgent home
                    all_busy_less_urgent = False
            if any_busy and all_busy_less_urgent:
                lock_events += 1
                locked_homes.add(L)
    print(f"  events: {lock_events}   distinct lost homes locked out by comfy "
          f"commitments: {len(locked_homes)}  {sorted(locked_homes)}")

    # ---- Q4 why-lost reason census --------------------------------------
    final_reason = {}
    for L in lost:
        # last non-infeasible or the terminal reason
        terminal = None
        feasible_ever = False
        for s in snaps:
            pool = PLAN_BY_TICK.get(s["t"])
            if not pool or L not in pool["jinfo"]:
                continue
            if L in pool["served"]:
                terminal = "assigned_then_lost"
                feasible_ever = True
            else:
                r = pool["unserved"].get(L)
                if r and r not in INFEASIBLE:
                    feasible_ever = True
                if r:
                    terminal = r
        final_reason[L] = (terminal or "?", feasible_ever)
    print(f"\nQ4 WHY-LOST ({len(lost)} ungelled threatened homes):")
    cnt = Counter(v[0] for v in final_reason.values())
    for r, n in cnt.most_common():
        print(f"    {r:22s}: {n}")
    feas_lost = [L for L, (r, f) in final_reason.items() if f]
    print(f"  lost homes that were FEASIBLE-for-someone at some tick: "
          f"{len(feas_lost)}  {feas_lost}")
    print(f"  lost homes NEVER feasible (correct triage / c6-like): "
          f"{len(lost)-len(feas_lost)}  {[L for L in lost if L not in feas_lost]}")


if __name__ == "__main__":
    main()
