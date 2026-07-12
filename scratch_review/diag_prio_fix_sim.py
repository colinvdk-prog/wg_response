"""DIAGNOSIS ONLY (task item d validation) — run the founder's Malibu sim-7 twice,
STOCK vs an urgency-aware objective, and compare the protection SET + gel ORDER.

The urgency-aware objective (proposed fix) keeps homes-mode count-max and the P10
safety gate UNCHANGED, but replaces the value-only tiebreak with:
    score = priority * (1 + w_urg*urgency(p50) + w_val*valuefrac)
where urgency(p50) = 1 - clamp((p50-now)/notice_h, 0, 1)  (sooner p50 => higher),
w_urg (1e-2) dominates w_val (1e-4).  So among equal COUNT the planner prefers the
SOONER-to-burn savable home, and (via _served_value's freshness-carrying tiebreak)
DEFERS comfortable far homes instead of gelling them 40-70 h early.

Safety deadlines/corridors still key off p10 (latest_service_start, LegOracle T*,
_depart_fail) — untouched. This ONLY reweights the objective's value/urgency
tiebreak.  Monkeypatch (no product-code change).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wgresponse.dispatch as D  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "tiles")
H = 3600.0
_now = {"v": 0.0}
_orig_ps = D._potential_score
_orig_sv = D._served_value
_real_plan = D.plan


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


def _urg(job, cfg):
    p50 = job.eta.eta_p50_s
    HN = cfg.notice_h * H
    if p50 is None or p50 != p50 or p50 == float("inf"):
        lead = HN
    else:
        lead = max(0.0, min(HN, p50 - _now["v"]))
    return 1.0 - lead / HN


def urg_ps(job, cfg):
    if cfg.dispatch_objective != "homes":
        return _orig_ps(job, cfg)
    vf = min(job.client.value_usd / 1e7, 1.0)
    return job.client.priority_weight * (1.0 + 1e-2 * _urg(job, cfg) + 1e-4 * vf)


def urg_sv(job, gel_frac, cfg):
    if cfg.dispatch_objective != "homes":
        return _orig_sv(job, gel_frac, cfg)
    vf = min(job.client.value_usd / 1e7, 1.0)
    return job.client.priority_weight * (
        1.0 + 1e-2 * _urg(job, cfg) + 1e-4 * vf * gel_frac)


def now_tracking_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                      forecasts=None):
    _now["v"] = now_s
    return _real_plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                      forecasts=forecasts)


def run(urgency_aware, ticks=260):
    if urgency_aware:
        D._potential_score = urg_ps
        D._served_value = urg_sv
    simui.dispatch.plan = now_tracking_plan
    try:
        sess = simui.SimSession("malibu_ca", 48, 3, seed="sim-7", tile_dir=TILE)
        p = nearest_burnable(sess, 34.0796, -118.6803)
        sess.ignite(p.lat, p.lon, intensity="extreme")
        value = {c.client_id: c.value_usd for c in sess._clients}
        gel_at = {}
        gel_lead = {}
        snaps = []
        for t in range(ticks):
            st = sess.tick()
            now = st["sim_now_s"]
            snaps.append({c["client_id"]: (c["eta_p10_s"], c["eta_p50_s"])
                          for c in st["clients"]})
            for c in st["clients"]:
                if c["gel_applied"] and c["client_id"] not in gel_at:
                    gel_at[c["client_id"]] = t
        st = sess.state()
        for s in st["serviced"]:
            cid = s["client_id"]
            # p50 lead at gel completion (nearest snap)
            ti = min(gel_at.get(cid, 0), len(snaps) - 1)
            p50 = snaps[ti].get(cid, (None, None))[1]
            now = sess._t0_s + ti * 900.0
            gel_lead[cid] = None if p50 is None else (p50 - now) / H
        served = {s["client_id"] for s in st["serviced"]}
        # impacted
        impacted = set()
        for t, snap in enumerate(snaps):
            now = sess._t0_s + t * 900.0
            for cid, (p10, p50) in snap.items():
                if p50 is not None and p50 <= now:
                    impacted.add(cid)
    finally:
        D._potential_score = _orig_ps
        D._served_value = _orig_sv
        simui.dispatch.plan = _real_plan
    return dict(served=served, value=value, gel_at=gel_at, gel_lead=gel_lead,
                impacted=impacted, sb=st["scoreboard"])


def main():
    stock = run(False)
    fix = run(True)
    print("=== STOCK (value-tiebreak) vs URGENCY-AWARE — Malibu sim-7 48/3 extreme ===\n")
    for tag, r in (("STOCK", stock), ("URGENCY", fix)):
        comfy = sum(1 for c in r["served"]
                    if r["gel_lead"].get(c) is not None and r["gel_lead"][c] > 24)
        prot_imp = len(r["served"] & r["impacted"])
        print(f"{tag:8s}: served={len(r['served'])}  "
              f"gelled-comfortable(lead>24h)={comfy}  "
              f"protected-then-impacted={prot_imp}  "
              f"scoreboard_unserved={r['sb']['unserved_by_reason']}")
    print(f"\nserved-set delta: only-stock={sorted(stock['served']-fix['served'])}  "
          f"only-urgency={sorted(fix['served']-stock['served'])}")

    # gel ORDER comparison: rank of each served home by gel tick, is the fix
    # protecting sooner-to-burn homes earlier?
    print("\nGEL-ORDER (first 10 gelled each), with p50 lead at gel:")
    for tag, r in (("STOCK", stock), ("URGENCY", fix)):
        order = sorted(r["served"], key=lambda c: r["gel_at"].get(c, 999))
        s = "  ".join(f"{c.split('-')[1]}(+{r['gel_lead'].get(c) or 0:.0f}h)"
                      for c in order[:10])
        print(f"  {tag:8s}: {s}")

    # average p50-lead-at-gel weighted by order: lower early-lead = more urgent-first
    for tag, r in (("STOCK", stock), ("URGENCY", fix)):
        order = sorted(r["served"], key=lambda c: r["gel_at"].get(c, 999))
        leads = [r["gel_lead"].get(c) for c in order if r["gel_lead"].get(c) is not None]
        first5 = leads[:5]
        print(f"  {tag:8s}: mean p50-lead of FIRST-5 gelled = "
              f"{sum(first5)/len(first5):.1f}h (lower = more urgent-first)")


if __name__ == "__main__":
    main()
