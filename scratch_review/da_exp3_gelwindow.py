"""EXP 3 — GEL-WINDOW SENSITIVITY (the unverified supplier number).

Fixed scenario: 2 crews, 8 jobs with median arrivals 5-40 h out and ensemble
spreads (p50-p10) of 1-8 h. Sweep gel_window_h in {6, 24, 72} plus the
env-scaled coating-life model (gel_env_model_enabled=True, which caps the
window at gel_life_h(...) -- and note: dispatch.plan calls
safety.derive_job_windows WITHOUT a weather forecast, so the env model always
evaluates at reference conditions => 6.0 h regardless of actual fire weather).

Reported per setting: jobs served, unserved (with reasons), service-start lead
vs median fire arrival (does the optimizer deploy LATER when the window is
short?), gel freshness at arrival, window-caused unserved vs the 72 h run.

Run: py -3 scratch_review/da_exp3_gelwindow.py
"""
from __future__ import annotations

import math

from da_common import T0, all_safe_ensemble, destination, mk_crew, mk_job

from wgresponse import dispatch, safety
from wgresponse.contracts import LatLon, ResponseConfig, SafeZone, seeded_rng

CENTER = LatLon(34.2000, -117.6000)


def build():
    rng = seeded_rng("da_exp3", "layout")
    crews = [mk_crew("G1", destination(CENTER, 200.0, 5000.0)),
             mk_crew("G2", destination(CENTER, 160.0, 7000.0))]
    jobs = []
    p50_hs = [5.0, 8.0, 12.0, 16.0, 20.0, 26.0, 32.0, 40.0]
    spreads = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    for j, (p50h, sp) in enumerate(zip(p50_hs, spreads)):
        loc = destination(CENTER, rng.uniform(0, 360), rng.uniform(2000, 10000))
        p50 = T0 + p50h * 3600.0
        p10 = p50 - sp * 3600.0
        jobs.append(mk_job(f"W{j+1:02d}", loc, rng.uniform(900_000, 3_000_000),
                           1.0, "I1", p10, p50))
    zones = [SafeZone("Z1", destination(CENTER, 270.0, 25_000.0), "west"),
             SafeZone("Z2", destination(CENTER, 0.0, 25_000.0), "north")]
    return crews, jobs, zones


def run(window_h, env_model):
    cfg = ResponseConfig()
    cfg.gel_window_h = window_h
    cfg.gel_env_model_enabled = env_model
    cfg.solver_budget_s = 3.0
    crews, jobs, zones = build()
    plan = dispatch.plan(crews, jobs, {"I1": all_safe_ensemble("I1")},
                         zones, cfg, T0)
    stops = [(cid, st) for cid, cp in plan.plans.items() for st in cp.stops]
    leads = []
    fracs = []
    detail = {}
    job_by = {j.client.client_id: j for j in jobs}
    for cid, st in stops:
        eta50 = job_by[st.client_id].eta.eta_p50_s
        lead_h = (eta50 - st.depart_s) / 3600.0
        leads.append(lead_h)
        fracs.append(st.gel_fresh_frac)
        detail[st.client_id] = (lead_h, st.gel_fresh_frac, st.margin_h)
    unserved = {u.client_id: u.reason for u in plan.unserved}
    return dict(window=window_h, env=env_model, served=len(stops),
                unserved=unserved, leads=leads, fracs=fracs, detail=detail,
                value=plan.objective_breakdown["value_usd"])


def main():
    runs = [run(72.0, False), run(24.0, False), run(6.0, False), run(72.0, True)]
    base = runs[0]
    print(f"{'setting':>18} {'served':>6} {'value($M)':>10} {'lead_h mean/max':>16} "
          f"{'gel_frac mean/min':>18} {'window-caused unserved':>24}")
    for r in runs:
        tag = f"env-model(ref 6h)" if r["env"] else f"window={r['window']:.0f}h"
        wl = (f"{sum(r['leads'])/len(r['leads']):.1f}/{max(r['leads']):.1f}"
              if r["leads"] else "-")
        gf = (f"{sum(r['fracs'])/len(r['fracs']):.2f}/{min(r['fracs']):.2f}"
              if r["fracs"] else "-")
        caused = sorted(set(r["unserved"]) - set(base["unserved"]))
        print(f"{tag:>18} {r['served']:>6} {r['value']/1e6:>10.2f} {wl:>16} "
              f"{gf:>18} {str(caused):>24}")
    print("\nper-job detail (lead_h = median-arrival minus gel-complete, h):")
    hdr = "  job   p50_h  spread_h " + "".join(
        f"{('env' if r['env'] else str(int(r['window']))+'h'):>16}" for r in runs)
    print(hdr)
    crews, jobs, zones = build()
    for j in jobs:
        cid = j.client.client_id
        p50h = (j.eta.eta_p50_s - T0) / 3600.0
        sp = (j.eta.eta_p50_s - j.eta.eta_p10_s) / 3600.0
        cells = []
        for r in runs:
            if cid in r["detail"]:
                lead, frac, marg = r["detail"][cid]
                cells.append(f"ld{lead:5.1f} gf{frac:4.2f}")
            else:
                cells.append(f"UNSRV:{r['unserved'].get(cid,'?')[:9]}")
        print(f"  {cid}  {p50h:5.1f}    {sp:4.1f}   " + "  ".join(f"{c:>14}" for c in cells))

    # show what the env model WOULD give under real fire weather (never used by plan)
    print("\ngel_life_h under actual fire weather vs the reference dispatch uses:")
    for (t, rh, w) in [(25.0, 50.0, 0.0), (33.0, 12.0, 8.0), (38.0, 8.0, 15.0)]:
        print(f"  temp={t:.0f}C rh={rh:.0f}% wind={w:.0f}m/s -> "
              f"gel_life_h={safety.gel_life_h(t, rh, w):.2f}")


if __name__ == "__main__":
    main()
