"""DIAGNOSIS ONLY — the honest verdict: were SAVABLE urgent homes lost while
crews serviced LESS-urgent (comfortable-margin) homes?  (task item 1 + 3)

Drives the founder's Malibu sim-7 (48 clients, 3 crews, EXTREME, central) with
rich per-tick capture, then classifies every home that ended UNGELLED:

  * SAVABLE-BUT-LOST  = the engine's OWN feasibility verdict said it was
    servable-for-some-crew (unserved reason capacity/triaged/dropped_by_optimizer)
    at >=1 tick, yet it ended ungelled.
  * CORRECT-TRIAGE (unsavable) = every tick it was threatened its reason was a
    genuine infeasibility (infeasible_deadline/slack, no_safe_corridor/egress,
    fire_arrived) — no crew could ever have reached it (the c6 case).

For each SAVABLE-BUT-LOST home L, at each tick it was savable-and-unassigned, we
ask what the crews were committed to: a TRUE INVERSION is a tick where some crew
is servicing/en-route to a home M whose TRUE (p50 / ensemble-median) arrival is
LATER than L's — i.e. the crew took the less-urgent home. We report the same
comparison on p10 to expose the compression artefact.

Read-only; drives SimSession in-process.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "tiles")
SAVABLE_REASONS = {"capacity", "triaged", "dropped_by_optimizer"}
HANDLED_REASONS = SAVABLE_REASONS | {"in_progress"}
H = 3600.0


def nearest_burnable(sess, lat, lon):
    spec = sess._tile.spec
    rc0 = geo.latlon_to_rc(spec, geo.LatLon(lat, lon))
    r0, c0 = rc0
    for rad in range(0, 40):
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                if max(abs(dr), abs(dc)) != rad:
                    continue
                r, c = r0 + dr, c0 + dc
                if 0 <= r < spec.nrows and 0 <= c < spec.ncols and \
                        is_burnable(int(round(sess._tile.fuel.get(r, c)))):
                    return geo.rc_to_latlon(spec, r, c)
    return None


def capture(ign=(34.0796, -118.6803), ticks=260):
    sess = simui.SimSession("malibu_ca", 48, 3, seed="sim-7", tile_dir=TILE)
    p = nearest_burnable(sess, *ign)
    sess.ignite(p.lat, p.lon, intensity="extreme")
    value = {c.client_id: c.value_usd for c in sess._clients}
    snaps = []
    for t in range(ticks):
        st = sess.tick()
        now = st["sim_now_s"]
        cl = {c["client_id"]: dict(p10=c["eta_p10_s"], p50=c["eta_p50_s"],
                                   gel=c["gel_applied"], served=c["serviced"],
                                   assigned=c["assigned"], state=c["state"])
              for c in st["clients"]}
        reason = {u["client_id"]: u["reason"] for u in st["unserved"]}
        crews = {}
        for cr in st["crews"]:
            crews[cr["crew_id"]] = dict(
                status=cr["status"],
                itin=[s["client_id"] for s in cr["itinerary"]])
        snaps.append(dict(t=t, now=now, cl=cl, reason=reason, crews=crews))
    return sess, snaps, value


def p50_of(snap, cid):
    v = snap["cl"].get(cid, {}).get("p50")
    return v


def p10_of(snap, cid):
    v = snap["cl"].get(cid, {}).get("p10")
    return v


def classify(sess, snaps, value):
    clients = list(value)
    # final outcome
    final = snaps[-1]
    gelled_ever = set()
    impacted_at = {}
    threatened_ever = set()
    reason_hist = defaultdict(set)     # cid -> set of reasons seen
    savable_ticks = defaultdict(list)  # cid -> [tick idx where savable+unassigned]
    handled_ticks = defaultdict(list)
    for s in snaps:
        now = s["now"]
        for cid, c in s["cl"].items():
            if c["gel"]:
                gelled_ever.add(cid)
            if c["p50"] is not None:
                threatened_ever.add(cid)
                if c["p50"] <= now and cid not in impacted_at:
                    impacted_at[cid] = s["t"]
        for cid, r in s["reason"].items():
            reason_hist[cid].add(r)
            if r in SAVABLE_REASONS:
                savable_ticks[cid].append(s["t"])
            if r in HANDLED_REASONS:
                handled_ticks[cid].append(s["t"])

    served_final = {c for c, cc in final["cl"].items() if cc["gel"]}
    impacted = set(impacted_at)
    # ungelled + threatened homes = the ones the founder cares about
    lost = sorted(c for c in threatened_ever
                  if c not in gelled_ever)
    outcome = {}
    for c in clients:
        if c not in threatened_ever:
            outcome[c] = "not-threatened"
        elif c in gelled_ever and c in impacted:
            outcome[c] = "protected-then-impacted"
        elif c in gelled_ever:
            outcome[c] = "saved"
        elif c in impacted:
            outcome[c] = "burned-ungelled"
        else:
            outcome[c] = "threatened-ungelled(comfortable)"
    return dict(outcome=outcome, gelled=gelled_ever, impacted_at=impacted_at,
                threatened=threatened_ever, reason_hist=reason_hist,
                savable_ticks=savable_ticks, handled_ticks=handled_ticks,
                lost=lost, served_final=served_final)


def analyze_inversions(snaps, value, cl, margin_h=2.0):
    """For each SAVABLE-BUT-LOST home, count ticks where a crew was committed to
    a strictly-less-urgent (later p50) home while the lost home sat savable."""
    lost_savable = [c for c in cl["lost"]
                    if any(r in SAVABLE_REASONS for r in cl["reason_hist"][c])]
    lost_unsavable = [c for c in cl["lost"]
                      if not any(r in HANDLED_REASONS
                                 for r in cl["reason_hist"][c])]
    # homes that were only ever 'in_progress' then lost (assigned, dropped)
    lost_assigned_only = [c for c in cl["lost"]
                          if c not in lost_savable and c not in lost_unsavable]

    inversion_events = []          # (lost, tick, crew, M, dp50_h, dp10_h)
    lost_with_inversion = set()
    for L in lost_savable:
        for ti in cl["savable_ticks"][L]:
            s = snaps[ti]
            pL50 = p50_of(s, L)
            pL10 = p10_of(s, L)
            if pL50 is None:
                continue
            for crew, cr in s["crews"].items():
                for M in cr["itin"]:
                    if M == L:
                        continue
                    pM50 = p50_of(s, M)
                    pM10 = p10_of(s, M)
                    if pM50 is None:
                        continue
                    # M less urgent than L by TRUE (p50) arrival?
                    if pM50 > pL50 + margin_h * H:
                        dp10 = None if (pM10 is None or pL10 is None) \
                            else (pM10 - pL10) / H
                        inversion_events.append(
                            (L, ti, crew, M, (pM50 - pL50) / H, dp10))
                        lost_with_inversion.add(L)
    return dict(lost_savable=lost_savable, lost_unsavable=lost_unsavable,
                lost_assigned_only=lost_assigned_only,
                events=inversion_events,
                lost_with_inversion=sorted(lost_with_inversion))


def main():
    ign = (34.0796, -118.6803)
    sess, snaps, value = capture(ign=ign, ticks=260)
    cl = classify(sess, snaps, value)
    inv = analyze_inversions(snaps, value, cl)

    from collections import Counter
    oc = Counter(cl["outcome"].values())
    print(f"=== Malibu sim-7 (seed=sim-7, 48/3 extreme, ign@{ign}) ===")
    print(f"ticks={len(snaps)}  sim_now +{(snaps[-1]['now']-snaps[0]['now']+900)/3600:.1f}h")
    print("\nOUTCOME CENSUS:")
    for k in ("saved", "protected-then-impacted", "burned-ungelled",
              "threatened-ungelled(comfortable)", "not-threatened"):
        print(f"  {k:34s}: {oc.get(k,0)}")
    print(f"  (gelled total = {len(cl['gelled'])}, impacted total = {len(cl['impacted_at'])})")

    print("\n--- SAVABLE-BUT-LOST vs CORRECT-TRIAGE (task item 1) ---")
    print(f"  lost & SAVABLE (feasible-for-some-crew >=1 tick): "
          f"{len(inv['lost_savable'])}  {inv['lost_savable']}")
    print(f"  lost & assigned-only (in_progress then dropped) : "
          f"{len(inv['lost_assigned_only'])}  {inv['lost_assigned_only']}")
    print(f"  lost & UNSAVABLE (correct triage, c6-like)      : "
          f"{len(inv['lost_unsavable'])}  {inv['lost_unsavable']}")

    print(f"\n  TRUE INVERSION events (crew on later-p50 home while a savable "
          f"earlier-p50 home sat): {len(inv['events'])}")
    print(f"  distinct lost homes with >=1 inversion: "
          f"{len(inv['lost_with_inversion'])}  {inv['lost_with_inversion']}")

    # show a few representative events
    print("\n  representative inversion events (lost L @tick: crew on M):")
    seen = set()
    for (L, ti, crew, M, dp50, dp10) in inv["events"]:
        if L in seen:
            continue
        seen.add(L)
        s = snaps[ti]
        pL50 = (p50_of(s, L) - s["now"]) / H
        pM50 = (p50_of(s, M) - s["now"]) / H
        pL10 = p10_of(s, L)
        pM10 = p10_of(s, M)
        pL10h = "inf" if pL10 is None else f"{(pL10-s['now'])/H:.1f}"
        pM10h = "inf" if pM10 is None else f"{(pM10-s['now'])/H:.1f}"
        print(f"    {L}(${value[L]/1e6:.2f}M) savable @t{ti}: {crew} committed to "
              f"{M}(${value[M]/1e6:.2f}M)")
        print(f"        L: p50 +{pL50:.1f}h p10 +{pL10h}h   "
              f"M: p50 +{pM50:.1f}h p10 +{pM10h}h   "
              f"(M later by {dp50:.1f}h on p50"
              + (f", {dp10:+.1f}h on p10)" if dp10 is not None else ")"))

    # p10-compression census (task item 3)
    print("\n--- P10 COMPRESSION (task item 3) ---")
    per_tick = []
    for s in snaps:
        now = s["now"]
        finite = [(cid, c["p10"], c["p50"]) for cid, c in s["cl"].items()
                  if c["p10"] is not None and c["p50"] is not None
                  and not c["gel"]]
        if not finite:
            continue
        near0 = [c for c in finite if (c[1] - now) / H <= 2.0]
        near0_farmed = [c for c in near0 if (c[2] - now) / H >= 10.0]
        per_tick.append((s["t"], len(finite), len(near0), len(near0_farmed)))
    if per_tick:
        # pick the busiest tick
        busiest = max(per_tick, key=lambda r: r[1])
        print(f"  busiest tick t{busiest[0]}: {busiest[1]} live-threatened ungelled homes; "
              f"{busiest[2]} have p10<=+2h; of those {busiest[3]} have p50>=+10h "
              f"(p10 says 'imminent', p50 says hours away)")
        avg_near = sum(r[2] for r in per_tick) / len(per_tick)
        avg_comp = sum(r[3] for r in per_tick) / len(per_tick)
        print(f"  avg across active ticks: {avg_near:.1f} homes p10<=+2h, "
              f"{avg_comp:.1f} of them p50>=+10h (compression artefacts)")
    return sess, snaps, value, cl, inv


if __name__ == "__main__":
    main()
