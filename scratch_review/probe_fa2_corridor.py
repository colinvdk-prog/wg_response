"""probe_fa2_corridor — WHERE does the founder-arithmetic corridor check fail?

Reproduces probe_founder_arith.py's certain/deploy-15 case in-process (same
seed, same construction), captures the EXACT arguments dispatch.plan() sees at
tick 1 by wrapping wgresponse.dispatch.plan at runtime (no engine file is
modified), then instruments every ingress and egress leg the way
dispatch._Ctx.leg_latest_depart does: safety.LegOracle with the road polyline,
margin_min=cfg.route_margin_roads_min, against the eroded P10 raster.

Reports per leg: route length, every failing sample (index, fraction along,
pass time, eroded P10 arrival, shortfall minutes), the un-eroded P10 and
ground-truth arrival at that same point (erosion bluntness), T* vs now, and
what margin / erosion radius would have cleared it.

Also runs a corridor-CONSTRAINED earliest-arrival Dijkstra over the whole road
graph (depot->home, home->each safe zone) to answer whether ANY road approach
was feasible that dispatch's single A* route did not consider.

Writes scratch_review/probe_fa2_corridor.json.
"""
import json
import math
import os
import sys
import heapq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, geo, safety, simui  # noqa: E402
from wgresponse.contracts import UNREACHED  # noqa: E402
from scratch_review.probe_founder_arith import (  # noqa: E402
    MAP, SEED, ignite, pick_homes, variant_cfg,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "probe_fa2_corridor.json")

CAPTURED = {}


def _install_capture():
    orig = dispatch.plan

    def wrapper(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                forecasts=None, road_provider=None):
        if "args" not in CAPTURED:
            CAPTURED["args"] = dict(crews=crews, jobs=jobs, ens=ens,
                                    safe_zones=safe_zones, cfg=cfg,
                                    now_s=now_s, road_provider=road_provider)
        return orig(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                    forecasts=forecasts, road_provider=road_provider)

    dispatch.plan = wrapper
    return orig


def build_session(deploy=15.0, mode="certain", crew_locs=None, extra=None):
    """Exact probe_founder_arith construction (+ optional cfg/staging tweaks)."""
    cfg = variant_cfg(mode, deploy)
    if extra:
        cfg.update(extra)
    homes, truth_h, span = pick_homes(cfg)
    kw = {}
    if crew_locs is not None:
        kw["crew_locs"] = crew_locs
    s = simui.SimSession(MAP, 4, 1, seed=SEED, cfg_overrides=dict(cfg),
                         client_locs=homes, **kw)
    return s, homes, truth_h, span, cfg


# --------------------------------------------------------------------------
# leg instrumentation — mirrors dispatch._Ctx._road_route / leg_latest_depart
# --------------------------------------------------------------------------

def _road_route(provider, a, b):
    if provider is None:
        return None
    rr = provider(a, b)
    if (rr is None or not getattr(rr, "on_roads", False)
            or not getattr(rr, "time_s", 0.0) or rr.time_s <= 0.0
            or not getattr(rr, "dist_m", 0.0) or rr.dist_m <= 0.0):
        return None
    return rr


def leg_probe(a, b, depart_s, provider, eroded, raw_p10, truth, cfg, t0,
              corr_speed):
    """Full per-sample instrumentation of ONE leg exactly as dispatch checks it."""
    rr = _road_route(provider, a, b)
    path = rr.polyline if rr is not None else None
    spd = (rr.dist_m / rr.time_s) if rr is not None else corr_speed
    mm = cfg.route_margin_roads_min if rr is not None else cfg.route_margin_min
    orc = safety.LegOracle(a, b, spd, eroded, cfg, path=path, margin_min=mm)
    v = orc.verdict(depart_s)
    margin_s = orc.margin_s
    total_m = rr.dist_m if rr is not None else geo.haversine_m(a, b)

    fails = []
    for (i, p, val, dt) in orc.samples:
        t_pass = depart_s + dt
        slack_s = val - t_pass
        if slack_s < margin_s:
            cum_m = dt * spd
            raw = safety._raster_value_at(raw_p10, p)
            tru = safety._raster_value_at(truth, p) if truth is not None else None
            fails.append(dict(
                sample=i, n_pts=orc.n_pts,
                frac_along=round(cum_m / max(total_m, 1.0), 3),
                cum_m=round(cum_m),
                lat=round(p.lat, 5), lon=round(p.lon, 5),
                pass_h=round((t_pass - t0) / 3600.0, 3),
                eroded_p10_h=round((val - t0) / 3600.0, 3),
                raw_p10_h=(None if raw is None or math.isinf(raw)
                           else round((raw - t0) / 3600.0, 3)),
                truth_h=(None if tru is None or math.isinf(tru)
                         else round((tru - t0) / 3600.0, 3)),
                erosion_gap_min=(None if raw is None or math.isinf(raw)
                                 else round((raw - val) / 60.0, 1)),
                shortfall_min=round((margin_s - slack_s) / 60.0, 1),
            ))
    # worst sample (binding constraint)
    binding = None
    if orc.samples:
        b_i, b_p, b_v, b_dt = min(orc.samples,
                                  key=lambda s: s[2] - s[3] - margin_s)
        cum_m = b_dt * spd
        raw = safety._raster_value_at(raw_p10, b_p)
        binding = dict(sample=b_i, frac_along=round(cum_m / max(total_m, 1.0), 3),
                       cum_m=round(cum_m),
                       eroded_p10_h=round((b_v - t0) / 3600.0, 3),
                       raw_p10_h=(None if raw is None or math.isinf(raw)
                                  else round((raw - t0) / 3600.0, 3)),
                       tstar_h=round((b_v - margin_s - b_dt - t0) / 3600.0, 3))
    # what margin would clear it at this departure?  need slack >= margin
    min_slack_min = None
    if orc.samples:
        min_slack_min = round(min((val - (depart_s + dt))
                                  for (_i, _p, val, dt) in orc.samples) / 60.0, 1)
    return dict(
        on_roads=rr is not None,
        route_m=round(total_m), route_min=(round(rr.time_s / 60.0, 1)
                                           if rr is not None else None),
        speed_mps=round(spd, 2), margin_min=mm,
        n_pts=orc.n_pts, n_constrained=len(orc.samples), n_fail=len(fails),
        safe=v.safe, worst_margin_h=(None if math.isinf(v.worst_margin_h)
                                     else round(v.worst_margin_h, 3)),
        min_slack_min=min_slack_min,
        tstar_h=(None if math.isinf(orc.latest_safe_depart_s)
                 else round((orc.latest_safe_depart_s - t0) / 3600.0, 3)),
        tstar_minus_depart_min=(None if math.isinf(orc.latest_safe_depart_s)
                                else round((orc.latest_safe_depart_s - depart_s) / 60.0, 1)),
        depart_h=round((depart_s - t0) / 3600.0, 3),
        binding=binding,
        fails_first=fails[:6], fails_last=fails[-3:] if len(fails) > 6 else [],
    )


# --------------------------------------------------------------------------
# corridor-constrained earliest-arrival search over the FULL road graph
# --------------------------------------------------------------------------

def _edge_points(g, e, u):
    """Ordered LatLon vertices of edge e traversed from node u."""
    geom = e.geometry if e.u == u else tuple(reversed(e.geometry))
    other = e.v if e.u == u else e.u
    return [g.nodes[u]] + [p for p in geom] + [g.nodes[other]], other


def corridor_dijkstra(g, src_ll, dst_lls, depart_s, eroded_list, cfg, speed_mps,
                      margin_min, sample_m=300.0):
    """Earliest-arrival search where an edge may only be traversed if EVERY
    sample along it clears the corridor law (eroded P10 > pass + margin).
    Arriving earlier is never worse, so plain Dijkstra on arrival time is
    exact. Returns {dst_index: (arrival_s, n_edges)} for reachable targets."""
    margin_s = margin_min * 60.0
    src = g.nearest_node(src_ll, cfg.road_spur_max_m)
    if src is None:
        return {}, None
    tgt = {}
    for i, d in enumerate(dst_lls):
        sn = g.nearest_node(d, cfg.road_spur_max_m)
        if sn is not None:
            tgt.setdefault(sn[0], []).append(i)
    # start time includes the origin spur
    t_src = depart_s + src[1] * cfg.fire_traffic_factor / 13.4
    best = {src[0]: t_src}
    prev = {}
    heap = [(t_src, src[0])]
    out = {}
    seen = set()
    while heap:
        t, n = heapq.heappop(heap)
        if n in seen:
            continue
        seen.add(n)
        if n in tgt:
            for i in tgt[n]:
                if i not in out:
                    hops = 0
                    x = n
                    while x in prev:
                        x = prev[x]
                        hops += 1
                    out[i] = (t, hops)
        for other, ei in g.neighbors(n):
            if other in seen:
                continue
            e = g.edges[ei]
            pts, _o = _edge_points(g, e, n)
            pts = safety._resample_path(pts, sample_m)
            # walk the edge, checking every sample
            cum = 0.0
            prev_p = None
            ok = True
            for p in pts:
                if prev_p is not None:
                    cum += geo.haversine_m(prev_p, p)
                prev_p = p
                tp = t + cum * cfg.fire_traffic_factor / e.speed_mps
                for ras in eroded_list:
                    val = safety._raster_value_at(ras, p)
                    if val is None or math.isinf(val):
                        continue
                    if (val - tp) < margin_s:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            nt = t + e.length_m * cfg.fire_traffic_factor / e.speed_mps
            if nt < best.get(other, math.inf):
                best[other] = nt
                prev[other] = n
                heapq.heappush(heap, (nt, other))
    return out, len(seen)


def unconstrained_dijkstra(g, src_ll, dst_lls, cfg):
    """Same search with NO corridor constraint (pure reachability/time)."""
    src = g.nearest_node(src_ll, cfg.road_spur_max_m)
    if src is None:
        return {}
    tgt = {}
    for i, d in enumerate(dst_lls):
        sn = g.nearest_node(d, cfg.road_spur_max_m)
        if sn is not None:
            tgt.setdefault(sn[0], []).append(i)
    best = {src[0]: 0.0}
    heap = [(0.0, src[0])]
    out = {}
    seen = set()
    while heap:
        t, n = heapq.heappop(heap)
        if n in seen:
            continue
        seen.add(n)
        if n in tgt:
            for i in tgt[n]:
                out.setdefault(i, t)
        for other, ei in g.neighbors(n):
            if other in seen:
                continue
            e = g.edges[ei]
            nt = t + e.length_m * cfg.fire_traffic_factor / e.speed_mps
            if nt < best.get(other, math.inf):
                best[other] = nt
                heapq.heappush(heap, (nt, other))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def q1_q2(report):
    orig = _install_capture()
    try:
        s, homes, truth_h, span, cfg_d = build_session(deploy=15.0)
        t0 = s._now_s
        ignite(s)
        CAPTURED.clear()
        st = s.tick()
    finally:
        dispatch.plan = orig

    args = CAPTURED["args"]
    cfg = args["cfg"]
    now = args["now_s"]
    provider = args["road_provider"]
    ens = args["ens"]
    zones = args["safe_zones"]
    corr_speed = safety._corridor_speed_mps(cfg)

    fire = next(iter(s._fires.values()))
    truth = (fire.ens.members[0] if getattr(fire.ens, "members", None)
             else fire.ens.p50)

    iid = sorted(ens)[0]
    raw_p10 = ens[iid].p10
    eroded = safety._erode(raw_p10, cfg.corridor_erosion_m)
    eroded_list = [eroded]

    crew = args["crews"][0]
    depot = crew.loc()

    report["config"] = dict(
        corridor_erosion_m=cfg.corridor_erosion_m,
        corridor_sample_m=cfg.corridor_sample_m,
        route_margin_min=cfg.route_margin_min,
        route_margin_roads_min=cfg.route_margin_roads_min,
        egress_buffer_min=cfg.egress_buffer_min,
        ember_buffer_min=cfg.ember_buffer_min,
        deploy_min=cfg.deploy_min,
        road_spur_max_m=cfg.road_spur_max_m,
        fire_traffic_factor=cfg.fire_traffic_factor,
        circuity=cfg.circuity, road_speed_mps=cfg.road_speed_mps,
        raster_cell_m=raw_p10.spec.cell_m,
        erosion_cells=int(math.ceil(cfg.corridor_erosion_m / raw_p10.spec.cell_m)),
        now_minus_t0_min=round((now - t0) / 60.0, 1),
        crew_depot=[round(depot.lat, 5), round(depot.lon, 5)],
        crew_anchor_zone=s._crew_anchor[crew.crew_id],
        homes=[[round(h.lat, 5), round(h.lon, 5)] for h in homes],
        truth_arrival_h=truth_h, homes_span_m=round(span),
        n_jobs=len(args["jobs"]),
        zones=[dict(zone_id=z.zone_id, name=getattr(z, "name", ""),
                    lat=round(z.loc.lat, 5), lon=round(z.loc.lon, 5),
                    km_from_home1=round(geo.haversine_m(homes[0], z.loc) / 1000.0, 2))
               for z in zones],
        all_map_zones=[dict(zone_id=z.zone_id,
                            km_from_home1=round(geo.haversine_m(homes[0], z.loc) / 1000.0, 2))
                       for z in s._loc.safe_zones],
        zones_filtered_out=[z.zone_id for z in s._loc.safe_zones
                            if z.zone_id not in {q.zone_id for q in zones}],
    )

    # ---- Q1 ingress + egress legs -----------------------------------------
    ing = {}
    egr = {}
    for job in sorted(args["jobs"], key=lambda j: j.client.client_id):
        cid = job.client.client_id
        h = job.client.loc
        ing[cid] = leg_probe(depot, h, now, provider, eroded, raw_p10, truth,
                             cfg, t0, corr_speed)
        # departure the dispatcher would use for the egress check
        rr = _road_route(provider, depot, h)
        tt = rr.time_s if rr is not None else None
        if tt is None:
            from wgresponse.travel import travel_time_s
            tt = travel_time_s(depot, h, cfg)
        arrive = now + tt
        service = max(arrive, job.earliest_useful_start_s)
        depart = service + cfg.deploy_min * 60.0
        egr[cid] = dict(
            travel_min=round(tt / 60.0, 1),
            arrive_h=round((arrive - t0) / 3600.0, 3),
            depart_h=round((depart - t0) / 3600.0, 3),
            latest_depart_h=round((job.latest_depart_s - t0) / 3600.0, 3),
            eta_p10_h=round((job.eta.eta_p10_s - t0) / 3600.0, 3),
            deadline_slack_min=round((job.latest_depart_s - depart) / 60.0, 1),
            zones={z.zone_id: leg_probe(h, z.loc, depart, provider, eroded,
                                        raw_p10, truth, cfg, t0, corr_speed)
                   for z in s._loc.safe_zones},
        )
    report["ingress"] = ing
    report["egress"] = egr

    # ---- erosion bluntness: eroded vs raw along the WHOLE ingress route ----
    rr0 = _road_route(provider, depot, sorted(args["jobs"], key=lambda j: j.client.client_id)[0].client.loc)
    if rr0 is not None:
        pts = safety._resample_path(rr0.polyline, cfg.corridor_sample_m)
        prof = []
        cum = 0.0
        prev = None
        for i, p in enumerate(pts):
            if prev is not None:
                cum += geo.haversine_m(prev, p)
            prev = p
            e = safety._raster_value_at(eroded, p)
            r = safety._raster_value_at(raw_p10, p)
            t = safety._raster_value_at(truth, p)
            f = lambda x: (None if x is None or math.isinf(x)
                           else round((x - t0) / 3600.0, 2))
            prof.append([i, round(cum), f(e), f(r), f(t)])
        report["ingress_profile_c1"] = dict(
            columns=["sample", "cum_m", "eroded_p10_h", "raw_p10_h", "truth_h"],
            rows=prof)

    # ---- Q2 alternative approaches on the full road graph ------------------
    g = s._road_graph
    homes_ll = [j.client.loc for j in sorted(args["jobs"],
                                             key=lambda j: j.client.client_id)]
    unc = unconstrained_dijkstra(g, depot, homes_ll, cfg)
    con, nseen = corridor_dijkstra(g, depot, homes_ll, now, eroded_list, cfg,
                                   corr_speed, cfg.route_margin_roads_min)
    con15, _ = corridor_dijkstra(g, depot, homes_ll, now, eroded_list, cfg,
                                 corr_speed, 15.0)
    con0, _ = corridor_dijkstra(g, depot, homes_ll, now, eroded_list, cfg,
                                corr_speed, 0.0)
    eroded250 = safety._erode(raw_p10, 250.0)
    con_e250, _ = corridor_dijkstra(g, depot, homes_ll, now, [eroded250], cfg,
                                    corr_speed, cfg.route_margin_roads_min)
    con_e0, _ = corridor_dijkstra(g, depot, homes_ll, now, [raw_p10], cfg,
                                  corr_speed, cfg.route_margin_roads_min)
    report["graph_search_ingress"] = dict(
        nodes=len(g.nodes), edges=len(g.edges),
        note=("earliest-arrival Dijkstra over EVERY road (dispatch only ever "
              "evaluates the single A* fastest route)"),
        unconstrained_min={str(i): round(v / 60.0, 1) for i, v in unc.items()},
        constrained_margin30={str(i): round((v[0] - now) / 60.0, 1)
                              for i, v in con.items()},
        constrained_margin15={str(i): round((v[0] - now) / 60.0, 1)
                              for i, v in con15.items()},
        constrained_margin0={str(i): round((v[0] - now) / 60.0, 1)
                             for i, v in con0.items()},
        constrained_erosion250_margin30={str(i): round((v[0] - now) / 60.0, 1)
                                         for i, v in con_e250.items()},
        constrained_erosion0_margin30={str(i): round((v[0] - now) / 60.0, 1)
                                       for i, v in con_e0.items()},
        nodes_reached_constrained=nseen,
    )

    # egress graph search from home 0 at its planned departure
    j0 = sorted(args["jobs"], key=lambda j: j.client.client_id)[0]
    rr = _road_route(provider, depot, j0.client.loc)
    dep0 = now + (rr.time_s if rr else 0.0) + cfg.deploy_min * 60.0
    zlls = [z.loc for z in s._loc.safe_zones]
    zc, _ = corridor_dijkstra(g, j0.client.loc, zlls, dep0, eroded_list, cfg,
                              corr_speed, cfg.route_margin_roads_min)
    zc0, _ = corridor_dijkstra(g, j0.client.loc, zlls, dep0, [raw_p10], cfg,
                               corr_speed, cfg.route_margin_roads_min)
    report["graph_search_egress_home1"] = dict(
        depart_h=round((dep0 - t0) / 3600.0, 3),
        zone_ids=[z.zone_id for z in s._loc.safe_zones],
        constrained_arrival_min={str(i): round((v[0] - dep0) / 60.0, 1)
                                 for i, v in zc.items()},
        constrained_erosion0_arrival_min={str(i): round((v[0] - dep0) / 60.0, 1)
                                          for i, v in zc0.items()},
    )
    return st


def run_variant(label, deploy=15.0, extra=None, crew_locs=None, ticks=20):
    s, homes, truth_h, span, _cfg = build_session(deploy=deploy,
                                                  crew_locs=crew_locs,
                                                  extra=extra)
    t0 = s._now_s
    ignite(s)
    st = s.tick()
    t1 = [dict(client=u.get("client_id"), reason=u.get("reason"))
          for u in (st.get("unserved") or [])]
    for _ in range(ticks - 1):
        st = s.tick()
    coated = [r["client_id"] for r in st["clients"] if r.get("gel_applied")]
    return dict(label=label, deploy=deploy, extra=extra or {},
                crew_staged=bool(crew_locs),
                coated=len(coated), coated_ids=coated,
                violations=st["scoreboard"].get("violations", 0),
                overruns=st["scoreboard"].get("crew_overruns", 0),
                unserved_tick1=t1,
                unserved_final=[dict(client=u.get("client_id"),
                                     reason=u.get("reason"))
                                for u in (st.get("unserved") or [])],
                outcomes={r["client_id"]: r["outcome"] for r in st["clients"]})


def main():
    report = {}
    print("== Q1/Q2: instrumenting tick-1 certain/deploy-15 ==")
    q1_q2(report)
    cfgd = report["config"]
    print(f"  erosion={cfgd['corridor_erosion_m']}m over cell_m="
          f"{cfgd['raster_cell_m']} -> k={cfgd['erosion_cells']} cells; "
          f"margin_roads={cfgd['route_margin_roads_min']}min")
    for cid, d in sorted(report["ingress"].items()):
        b = d["binding"] or {}
        print(f"  INGRESS {cid}: {d['route_m']}m/{d['route_min']}min on_roads={d['on_roads']} "
              f"fails {d['n_fail']}/{d['n_constrained']} constrained of {d['n_pts']} pts; "
              f"T*-depart={d['tstar_minus_depart_min']}min; binding frac={b.get('frac_along')} "
              f"eroded={b.get('eroded_p10_h')}h raw={b.get('raw_p10_h')}h")
    for cid, d in sorted(report["egress"].items()):
        vs = {z: (v["safe"], v["tstar_minus_depart_min"]) for z, v in d["zones"].items()}
        print(f"  EGRESS  {cid}: depart={d['depart_h']}h "
              f"deadline_slack={d['deadline_slack_min']}min zones={vs}")
    print(f"  GRAPH ingress: {report['graph_search_ingress']}")
    print(f"  GRAPH egress:  {report['graph_search_egress_home1']}")

    print("== Q3: sensitivity ==")
    variants = [
        ("baseline_certain_d15", dict(deploy=15.0)),
        ("margin_roads_15", dict(deploy=15.0, extra={"route_margin_roads_min": 15.0})),
        ("margin_roads_0", dict(deploy=15.0, extra={"route_margin_roads_min": 0.0})),
        ("erosion_250", dict(deploy=15.0, extra={"corridor_erosion_m": 250.0})),
        ("erosion_0", dict(deploy=15.0, extra={"corridor_erosion_m": 0.0})),
        ("erosion_0_margin_15", dict(deploy=15.0,
                                     extra={"corridor_erosion_m": 0.0,
                                            "route_margin_roads_min": 15.0})),
    ]
    rows = []
    for label, kw in variants:
        r = run_variant(label, **kw)
        rows.append(r)
        print(f"  {label:<24} coated={r['coated']}/4 viol={r['violations']} "
              f"t1={[u['reason'] for u in r['unserved_tick1']]}")
    # crew staged AT home 1 (kills the ingress leg)
    s0, homes0, _th, _sp, _c = build_session(deploy=15.0)
    for label, kw in [("staged_at_home1", dict(deploy=15.0,
                                               crew_locs=[homes0[0]])),
                      ("staged_at_home1_erosion_0",
                       dict(deploy=15.0, crew_locs=[homes0[0]],
                            extra={"corridor_erosion_m": 0.0}))]:
        r = run_variant(label, **kw)
        rows.append(r)
        print(f"  {label:<24} coated={r['coated']}/4 viol={r['violations']} "
              f"t1={[u['reason'] for u in r['unserved_tick1']]}")
    report["sensitivity"] = rows

    json.dump(report, open(OUT, "w"), indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
