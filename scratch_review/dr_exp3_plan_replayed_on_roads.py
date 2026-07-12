"""dr_exp3 — A TIER-0 PLAN DRIVEN ON THE REAL PARADISE ROAD NETWORK.

Setup: the real Paradise/Magalia terrain tile + the real OSM road graph.
A planar fire front sweeps in from the NE (Camp Fire geometry) at 0.45 m/s:
p10(cell) = T0 + SW-projected distance from the NE corner / 0.45;
p50 = p10 + 1.5 h, p90 = p10 + 3 h. 12 WUI clients (seeded placement),
3 crews staged at the location's safe zones.

Part A: dispatch.plan with stock config (tier-0 travel: circuity 1.35).
        Then REPLAY each planned route with roads.route(...) times — the same
        physics the interactive simulator uses to move crews — and measure,
        per stop: arrival slip, actual departure vs latest_depart (SAFETY
        SEMANTICS 2) and vs the 15-min slack floor (SEMANTICS 7).
Part B: same instance re-planned with circuity raised to the measured
        Paradise value (2.43, dr_exp2) — what an honest travel model changes.

The replay mirrors _SimCrew.advance: service_start = max(road arrival,
planned service_start); depart = service_start + deploy_min. The engine's
own defense (replan-from-actual-positions every 15 min) is deliberately NOT
simulated here — this isolates how much truth the plan itself carries.
"""
from __future__ import annotations

import math

from dr_common import T0, M_PER_DEG_LAT, mk_crew, mk_job

from wgresponse import dispatch, roads, spread, travel
from wgresponse.contracts import (EnsembleArrival, LatLon, Raster,
                                  ResponseConfig)
from wgresponse.geo import haversine_m, rc_to_latlon
from wgresponse.simui import LOCATIONS, load_tile, place_clients

KEY = "paradise_ca"
N_CLIENTS = 12


def planar_ne_ensemble(tile, v_front_mps):
    spec = tile.spec
    east_lon = spec.west_lon + spec.ncols * spec.cell_m / (
        M_PER_DEG_LAT * math.cos(math.radians(spec.north_lat)))
    p10 = Raster(spec)
    p50 = Raster(spec)
    p90 = Raster(spec)
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            ll = rc_to_latlon(spec, r, c)
            m_lon = M_PER_DEG_LAT * math.cos(math.radians(ll.lat))
            s = ((spec.north_lat - ll.lat) * M_PER_DEG_LAT
                 + (east_lon - ll.lon) * m_lon) / math.sqrt(2.0)
            a = T0 + max(0.0, s) / v_front_mps
            p10.set(r, c, a)
            p50.set(r, c, a + 1.5 * 3600.0)
            p90.set(r, c, a + 3.0 * 3600.0)
    return EnsembleArrival(spec=spec, members=[p50], p10=p10, p50=p50,
                           p90=p90, t0_s=T0, incident_id="INC-NE")


def build_instance(cfg, v_front_mps):
    tile, synthetic = load_tile(KEY)
    assert not synthetic
    ens = planar_ne_ensemble(tile, v_front_mps)
    clients = place_clients(tile, N_CLIENTS, seed="dr3")
    jobs = []
    for cl in clients:
        eta = spread.eta_at(ens, cl.loc)
        if math.isinf(eta.eta_p10_s):
            continue
        jobs.append(mk_job(cl.client_id, cl.loc, cl.value_usd, 1.0,
                           "INC-NE", eta.eta_p10_s, eta.eta_p50_s))
    loc = LOCATIONS[KEY]
    crews = [mk_crew(f"C{i+1}", z.loc) for i, z in enumerate(loc.safe_zones)]
    return jobs, crews, {"INC-NE": ens}, list(loc.safe_zones)


def replay_on_roads(plan, jobs, crews, zones, cfg, g):
    """Drive each planned route at road times; return per-stop records."""
    jl = {j.client.client_id: j.client.loc for j in jobs}
    cl = {c.crew_id: c.loc() for c in crews}
    zl = {z.zone_id: z.loc for z in zones}
    out = []
    for cid in sorted(plan.plans):
        cp = plan.plans[cid]
        t, loc = T0, cl[cid]
        for st in cp.stops:
            tgt = jl[st.client_id]
            rr = roads.route(g, loc, tgt, cfg)
            arrive = t + rr.time_s
            service = max(arrive, st.service_start_s)
            depart = service + cfg.deploy_min * 60.0
            egress_t0 = travel.travel_time_s(tgt, zl[cp.egress_zone_id], cfg) \
                if cp.egress_zone_id in zl else 0.0
            rr_e = roads.route(g, tgt, zl[cp.egress_zone_id], cfg) \
                if cp.egress_zone_id in zl else None
            out.append({
                "crew": cid, "client": st.client_id,
                "slip_min": (arrive - st.arrive_s) / 60.0,
                "planned_margin_min": st.margin_h * 60.0,
                "actual_margin_min": (st.latest_depart_s - depart) / 60.0,
                "deadline_breach": depart > st.latest_depart_s,
                "floor_breach": (st.latest_depart_s - depart) / 60.0
                                < cfg.slack_min,
                "on_roads": rr.on_roads,
                "egress_slip_min": ((rr_e.time_s - egress_t0) / 60.0
                                    if rr_e else 0.0),
            })
            t, loc = depart, tgt
    return out


def report(tag, plan, rows):
    b = plan.objective_breakdown
    print(f"\n=== {tag} ===")
    print(f"  planned: served={b['served']} value=${b['value_usd']/1e6:.2f}M "
          f"drive={b['drive_h']:.2f}h  unserved={b['unserved']} "
          f"candidate={b['candidate']}/{b['seed']}")
    if not rows:
        print("  (no stops to replay)")
        return
    slips = [r["slip_min"] for r in rows]
    print(f"  replay on roads ({sum(r['on_roads'] for r in rows)}/{len(rows)}"
          f" legs on-network):")
    print(f"    arrival slip min/mean/max: {min(slips):.1f} / "
          f"{sum(slips)/len(slips):.1f} / {max(slips):.1f} min")
    hard = [r for r in rows if r["deadline_breach"]]
    soft = [r for r in rows if r["floor_breach"] and not r["deadline_breach"]]
    print(f"    stops breaching latest_depart (SAFETY SEMANTICS 2): "
          f"{len(hard)}/{len(rows)}"
          + (f"  -> {[(r['crew'], r['client'], round(r['actual_margin_min'],1)) for r in hard]}"
             if hard else ""))
    print(f"    stops landing under the 15-min slack floor (SEM 7): "
          f"{len(soft)}/{len(rows)}"
          + (f"  -> {[(r['crew'], r['client'], round(r['actual_margin_min'],1)) for r in soft]}"
             if soft else ""))
    for r in rows:
        print(f"    {r['crew']}->{r['client']}: slip {r['slip_min']:+.1f} min, "
              f"margin planned {r['planned_margin_min']:.0f} -> actual "
              f"{r['actual_margin_min']:.0f} min, egress slip "
              f"{r['egress_slip_min']:+.1f} min")


def main():
    g = roads.load_road_graph(KEY)
    assert g is not None

    for v_front, label in ((0.45, "slow front"), (1.30, "PRESSURE front")):
        cfg = ResponseConfig()
        jobs, crews, ens, zones = build_instance(cfg, v_front)
        print(f"\n################ {label}: {v_front} m/s from NE — "
              f"{len(jobs)} finite-eta jobs, {len(crews)} crews, p10 leads "
              f"{min((j.eta.eta_p10_s-T0)/3600 for j in jobs):.1f}-"
              f"{max((j.eta.eta_p10_s-T0)/3600 for j in jobs):.1f} h")

        plan_a = dispatch.plan(crews, jobs, ens, zones, cfg, T0)
        rows_a = replay_on_roads(plan_a, jobs, crews, zones, cfg, g)
        report("A: stock tier-0 plan (circuity 1.35) driven on real roads",
               plan_a, rows_a)

        cfg_b = ResponseConfig(circuity=2.43)
        plan_b = dispatch.plan(crews, jobs, ens, zones, cfg_b, T0)
        rows_b = replay_on_roads(plan_b, jobs, crews, zones, cfg_b, g)
        report("B: re-planned with measured Paradise circuity 2.43",
               plan_b, rows_b)


if __name__ == "__main__":
    main()
