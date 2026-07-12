"""DIAGNOSIS ONLY — capture EVERY real dispatch.plan() decision in the founder's
Malibu sim-7 and find WITHIN-PLAN urgency inversions + run the fix counterfactual.

Wraps wgresponse.dispatch.plan so we see the exact job pool, the served set, and
the unserved reasons the live engine produced each tick — ground truth, no proxy.

A WITHIN-PLAN inversion = one dispatch.plan() call that SERVED a home M while
leaving a home L unserved for capacity/triaged, where L's TRUE (p50) arrival is
EARLIER than M's (L more urgent) — the plan preferred the less-urgent home when
it could not take both. We report the same pair on p10 to expose compression.

Counterfactual: at each inversion call we re-run dispatch.plan on the identical
inputs with an urgency-aware _potential_score (earlier-p50 -> higher priority,
value only as a sub-tiebreak) and check whether the more-urgent home is protected
instead — validating the fix (task item d) without touching product code.

Read-only; drives SimSession in-process.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "tiles")
SAVABLE = {"capacity", "triaged", "dropped_by_optimizer"}
H = 3600.0
_real_plan = dispatch.plan


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


RECORDS = []


def recording_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                   forecasts=None):
    plan = _real_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                      forecasts=forecasts)
    jinfo = {j.client.client_id: dict(p10=j.eta.eta_p10_s, p50=j.eta.eta_p50_s,
                                      value=j.client.value_usd) for j in jobs}
    served = {st.client_id for cp in plan.plans.values() for st in cp.stops}
    unserved = {u.client_id: u.reason for u in plan.unserved}
    RECORDS.append(dict(now=now_s, jinfo=jinfo, served=served,
                        unserved=unserved,
                        # keep inputs for the counterfactual re-plan
                        crews=crews, jobs=jobs, ens=ens, safe_zones=safe_zones,
                        cfg=cfg, incumbent=incumbent, forecasts=forecasts))
    return plan


def run_capture(ign=(34.0796, -118.6803), ticks=260):
    RECORDS.clear()
    simui.dispatch.plan = recording_plan
    try:
        sess = simui.SimSession("malibu_ca", 48, 3, seed="sim-7", tile_dir=TILE)
        p = nearest_burnable(sess, *ign)
        sess.ignite(p.lat, p.lon, intensity="extreme")
        for _ in range(ticks):
            sess.tick()
    finally:
        simui.dispatch.plan = _real_plan
    return sess


def within_plan_inversions(margin_h=2.0):
    """Same-plan (served M vs capacity/triaged L) pairs with p50(L) < p50(M)."""
    events = []
    for rec in RECORDS:
        now = rec["now"]
        left = [(c, rec["jinfo"][c]) for c, r in rec["unserved"].items()
                if r in SAVABLE and c in rec["jinfo"]]
        srv = [(c, rec["jinfo"][c]) for c in rec["served"] if c in rec["jinfo"]]
        for L, jl in left:
            if jl["p50"] is None:
                continue
            for M, jm in srv:
                if jm["p50"] is None:
                    continue
                if jl["p50"] + margin_h * H < jm["p50"]:      # L more urgent
                    events.append(dict(now=now, L=L, M=M,
                                       Lp50=(jl["p50"] - now) / H,
                                       Mp50=(jm["p50"] - now) / H,
                                       Lp10=(jl["p10"] - now) / H if jl["p10"] else None,
                                       Mp10=(jm["p10"] - now) / H if jm["p10"] else None,
                                       Lval=jl["value"], Mval=jm["value"],
                                       reason=rec["unserved"][L]))
    return events


def counterfactual(rec, margin_h=2.0):
    """Re-run the SAME dispatch inputs with an urgency-aware _potential_score
    (primary = earlier p50 => bigger; value only a sub-tiebreak) and return the
    served set. Monkeypatches dispatch internals for THIS call only."""
    import wgresponse.dispatch as D
    now = rec["now"]
    # horizon for normalising urgency into a [0,1) lead fraction
    HORIZON = rec["cfg"].notice_h * H
    orig_ps, orig_sv = D._potential_score, D._served_value

    def urgency_ps(job, cfg):
        base = job.client.priority_weight
        if cfg.dispatch_objective != "homes":
            return orig_ps(job, cfg)
        p50 = job.eta.eta_p50_s
        lead = (p50 - now) if (p50 is not None and p50 == p50 and p50 < float("inf")) else HORIZON
        lead = max(0.0, min(HORIZON, lead))
        urg = 1.0 - lead / HORIZON            # 1 = imminent, 0 = far
        valfrac = min(job.client.value_usd / 1e7, 1.0)
        # count(1.0) dominates; urgency (<=1e-2) beats value (<=1e-4)
        return base * (1.0 + 1e-2 * urg + 1e-4 * valfrac)

    def urgency_sv(job, gel_frac, cfg):
        if cfg.dispatch_objective != "homes":
            return orig_sv(job, gel_frac, cfg)
        base = job.client.priority_weight
        p50 = job.eta.eta_p50_s
        lead = (p50 - now) if (p50 is not None and p50 == p50 and p50 < float("inf")) else HORIZON
        lead = max(0.0, min(HORIZON, lead))
        urg = 1.0 - lead / HORIZON
        valfrac = min(job.client.value_usd / 1e7, 1.0)
        return base * (1.0 + 1e-2 * urg + 1e-4 * valfrac * gel_frac)

    D._potential_score = urgency_ps
    D._served_value = urgency_sv
    try:
        plan = _real_plan(rec["crews"], rec["jobs"], rec["ens"],
                          rec["safe_zones"], rec["cfg"], now, rec["incumbent"],
                          forecasts=rec["forecasts"])
    finally:
        D._potential_score, D._served_value = orig_ps, orig_sv
    return {st.client_id for cp in plan.plans.values() for st in cp.stops}


def main():
    sess = run_capture()
    print(f"=== dispatch.plan() calls recorded: {len(RECORDS)} ===")
    ev = within_plan_inversions()
    # dedup by (L,M) to summarise
    by_pair = defaultdict(list)
    for e in ev:
        by_pair[(e["L"], e["M"])].append(e)
    lost_L = sorted({e["L"] for e in ev})
    print(f"\nWITHIN-PLAN inversions (served M less-urgent than left-savable L):")
    print(f"  total events: {len(ev)}   distinct L(left,more-urgent): {len(lost_L)}")
    print(f"  distinct (L,M) pairs: {len(by_pair)}")

    # value orientation of the inversions
    val_up = sum(1 for e in ev if e["Mval"] > e["Lval"])   # took the pricier
    val_dn = sum(1 for e in ev if e["Mval"] < e["Lval"])   # took the cheaper
    print(f"  of events, M(served) pricier than L: {val_up}; M cheaper: {val_dn}")

    print("\n  representative pairs (first occurrence each):")
    shown = 0
    for (L, M), es in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
        e = es[0]
        p10cmp = ("p10 L+%.1f M+%.1f" % (e["Lp10"], e["Mp10"])
                  if e["Lp10"] is not None and e["Mp10"] is not None else "p10 n/a")
        flip = "p10-INVERTED(L later on p10)" if (
            e["Lp10"] is not None and e["Mp10"] is not None
            and e["Lp10"] > e["Mp10"]) else ""
        print(f"    L={L}(${e['Lval']/1e6:.2f}M, p50+{e['Lp50']:.1f}h) LEFT[{e['reason']}] "
              f"<- M={M}(${e['Mval']/1e6:.2f}M, p50+{e['Mp50']:.1f}h) SERVED "
              f"| {len(es)} ticks | {p10cmp} {flip}")
        shown += 1
        if shown >= 12:
            break

    # counterfactual on the distinct inversion ticks: does urgency-aware save L?
    print("\n=== COUNTERFACTUAL: urgency-aware objective on the SAME inputs ===")
    inv_recs = []
    seen_now = set()
    for rec in RECORDS:
        now = rec["now"]
        left = [c for c, r in rec["unserved"].items() if r in SAVABLE
                and c in rec["jinfo"] and rec["jinfo"][c]["p50"] is not None]
        if not left:
            continue
        # does this call have a within-plan inversion?
        has = False
        for L in left:
            lp = rec["jinfo"][L]["p50"]
            for M in rec["served"]:
                if M in rec["jinfo"] and rec["jinfo"][M]["p50"] is not None \
                        and lp + 2 * H < rec["jinfo"][M]["p50"]:
                    has = True
                    break
            if has:
                break
        if has and now not in seen_now:
            inv_recs.append(rec)
            seen_now.add(now)

    fixed_saves = 0
    checked = 0
    detail = []
    for rec in inv_recs[:40]:
        base_served = rec["served"]
        cf_served = counterfactual(rec)
        now = rec["now"]
        left = [c for c, r in rec["unserved"].items() if r in SAVABLE
                and c in rec["jinfo"] and rec["jinfo"][c]["p50"] is not None]
        # more-urgent left homes that the fix now serves
        newly = [c for c in left if c in cf_served and c not in base_served]
        # equal served count?
        checked += 1
        if newly:
            fixed_saves += 1
            detail.append((now, len(base_served), len(cf_served), newly))
    print(f"  inversion calls checked: {checked}")
    print(f"  calls where urgency-aware protected a MORE-URGENT home homes-mode "
          f"left: {fixed_saves}")
    for (now, nb, nc, newly) in detail[:10]:
        print(f"    @+{(now-RECORDS[0]['now'])/H:5.1f}h  served {nb}->{nc}  "
              f"now-protected-urgent: {newly}")


if __name__ == "__main__":
    main()
