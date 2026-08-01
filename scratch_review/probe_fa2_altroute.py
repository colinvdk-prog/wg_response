"""probe_fa2_altroute — was a corridor-CLEAN road route to the homes available
that dispatch never evaluated?

dispatch._Ctx._road_route calls roads.route() once per (a,b); roads.route runs a
single fastest-time A* and returns ONE polyline. leg_latest_depart then builds
ONE safety.LegOracle on it. If that single route crosses a blocked cell the job
is 'no_safe_corridor' — even if another road reaches the same house.

This probe searches the whole road graph for the earliest-arrival route whose
every 300 m sample clears the corridor law, rebuilds it in roads.route's own
[spur -> edges -> spur] form, and then re-checks it with dispatch's OWN oracle
(uniform route-average speed, margin_min=cfg.route_margin_roads_min, same
eroded raster). Writes scratch_review/probe_fa2_altroute.json.
"""
import json
import heapq
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, geo, roads, safety, simui  # noqa: E402
from scratch_review.probe_founder_arith import (  # noqa: E402
    MAP, SEED, ignite, pick_homes, variant_cfg,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "probe_fa2_altroute.json")
CAPTURED = {}


def _capture():
    orig = dispatch.plan

    def wrapper(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None,
                forecasts=None, road_provider=None):
        CAPTURED.setdefault("args", dict(
            crews=crews, jobs=jobs, ens=ens, safe_zones=safe_zones, cfg=cfg,
            now_s=now_s, road_provider=road_provider))
        return orig(crews, jobs, ens, safe_zones, cfg, now_s, incumbent,
                    forecasts=forecasts, road_provider=road_provider)
    dispatch.plan = wrapper
    return orig


def edge_pts(g, e, u):
    geom = e.geometry if e.u == u else tuple(reversed(e.geometry))
    other = e.v if e.u == u else e.u
    return [g.nodes[u]] + list(geom) + [g.nodes[other]], other


def safe_dijkstra(g, src_ll, dst_ll, depart_s, eroded, cfg, margin_min,
                  sample_m=300.0):
    """Earliest corridor-CLEAN arrival at dst's snap node; returns
    (node_path, edge_path, arrival_s) or None."""
    margin_s = margin_min * 60.0
    sa = g.nearest_node(src_ll, cfg.road_spur_max_m)
    sb = g.nearest_node(dst_ll, cfg.road_spur_max_m)
    if sa is None or sb is None:
        return None
    t0 = depart_s + sa[1] * cfg.fire_traffic_factor / roads.SNAP_SPEED_MPS
    best = {sa[0]: t0}
    came = {}
    heap = [(t0, sa[0])]
    seen = set()
    while heap:
        t, n = heapq.heappop(heap)
        if n in seen:
            continue
        seen.add(n)
        if n == sb[0]:
            npath, epath = [n], []
            while n in came:
                n, ei = came[n]
                npath.append(n)
                epath.append(ei)
            npath.reverse()
            epath.reverse()
            return npath, epath, t
        for other, ei in g.neighbors(n):
            if other in seen:
                continue
            e = g.edges[ei]
            pts, _ = edge_pts(g, e, n)
            pts = safety._resample_path(pts, sample_m)
            cum, prev, ok = 0.0, None, True
            for p in pts:
                if prev is not None:
                    cum += geo.haversine_m(prev, p)
                prev = p
                tp = t + cum * cfg.fire_traffic_factor / e.speed_mps
                v = safety._raster_value_at(eroded, p)
                if v is not None and not math.isinf(v) and (v - tp) < margin_s:
                    ok = False
                    break
            if not ok:
                continue
            nt = t + e.length_m * cfg.fire_traffic_factor / e.speed_mps
            if nt < best.get(other, math.inf):
                best[other] = nt
                came[other] = (n, ei)
                heapq.heappush(heap, (nt, other))
    return None


def build_route(g, a, b, npath, epath, cfg):
    """Rebuild a roads.RoadRoute-shaped polyline/time/dist for a node path,
    exactly as roads.route() does (origin spur + edge geometry + dest spur)."""
    sa = g.nearest_node(a, cfg.road_spur_max_m)
    sb = g.nearest_node(b, cfg.road_spur_max_m)
    f = cfg.fire_traffic_factor
    poly = [a]
    def emit(p):
        if not poly or poly[-1] != p:
            poly.append(p)
    emit(g.nodes[npath[0]])
    dist = sa[1]
    time = sa[1] * f / roads.SNAP_SPEED_MPS
    cur = npath[0]
    for nxt, ei in zip(npath[1:], epath):
        e = g.edges[ei]
        geom = e.geometry if e.u == cur else tuple(reversed(e.geometry))
        for pt in geom:
            emit(pt)
        emit(g.nodes[nxt])
        dist += e.length_m
        time += e.length_m * f / e.speed_mps
        cur = nxt
    emit(b)
    dist += sb[1]
    time += sb[1] * f / roads.SNAP_SPEED_MPS
    return poly, dist, time


def oracle_report(a, b, poly, dist, time, depart_s, eroded, cfg, t0, margin):
    spd = dist / time
    o = safety.LegOracle(a, b, spd, eroded, cfg, path=poly, margin_min=margin)
    v = o.verdict(depart_s)
    slack = [(val - (depart_s + dt)) / 60.0 for (_i, _p, val, dt) in o.samples]
    return dict(dist_m=round(dist), time_min=round(time / 60.0, 1),
                speed_mps=round(spd, 2), margin_min=margin,
                n_pts=o.n_pts, n_constrained=len(o.samples),
                safe=v.safe,
                min_slack_min=(round(min(slack), 1) if slack else None),
                tstar_minus_depart_min=(
                    None if math.isinf(o.latest_safe_depart_s)
                    else round((o.latest_safe_depart_s - depart_s) / 60.0, 1)),
                max_lat=round(max(p.lat for p in poly), 5),
                min_lat=round(min(p.lat for p in poly), 5),
                first_reason=v.reason[:150])


def main():
    orig = _capture()
    try:
        cfgd = variant_cfg("certain", 15.0)
        homes, truth_h, span = pick_homes(cfgd)
        s = simui.SimSession(MAP, 4, 1, seed=SEED, cfg_overrides=dict(cfgd),
                             client_locs=homes)
        t0 = s._now_s
        ignite(s)
        s.tick()
    finally:
        dispatch.plan = orig

    a = CAPTURED["args"]
    cfg, now, prov = a["cfg"], a["now_s"], a["road_provider"]
    iid = sorted(a["ens"])[0]
    eroded = safety._erode(a["ens"][iid].p10, cfg.corridor_erosion_m)
    g = s._road_graph
    depot = a["crews"][0].loc()
    jobs = sorted(a["jobs"], key=lambda j: j.client.client_id)
    margin = cfg.route_margin_roads_min

    rep = {"note": ("dispatch evaluates ONLY roads.route()'s single fastest-"
                    "time A* polyline per (a,b); 'alt' below is the earliest "
                    "corridor-clean route over the same graph, re-judged by "
                    "dispatch's OWN LegOracle."),
           "depart_h": round((now - t0) / 3600.0, 3), "legs": {}}

    for j in jobs:
        cid, h = j.client.client_id, j.client.loc
        rr = prov(depot, h)
        cur = oracle_report(depot, h, rr.polyline, rr.dist_m, rr.time_s, now,
                            eroded, cfg, t0, margin)
        found = safe_dijkstra(g, depot, h, now, eroded, cfg, margin)
        alt = None
        if found is not None:
            npath, epath, _arr = found
            poly, dist, time = build_route(g, depot, h, npath, epath, cfg)
            alt = oracle_report(depot, h, poly, dist, time, now, eroded, cfg,
                                t0, margin)
            alt["n_edges"] = len(epath)
        rep["legs"][cid] = dict(dispatch_route=cur, corridor_clean_alt=alt,
                                detour_m=(None if alt is None else
                                          alt["dist_m"] - cur["dist_m"]),
                                detour_min=(None if alt is None else
                                            round(alt["time_min"] - cur["time_min"], 1)))
        print(f"{cid}: dispatch route {cur['dist_m']}m/{cur['time_min']}min "
              f"safe={cur['safe']} minslack={cur['min_slack_min']}  ||  "
              f"alt=" + ("NONE" if alt is None else
                         f"{alt['dist_m']}m/{alt['time_min']}min safe={alt['safe']} "
                         f"minslack={alt['min_slack_min']} "
                         f"(+{rep['legs'][cid]['detour_m']}m/"
                         f"+{rep['legs'][cid]['detour_min']}min)"))

    json.dump(rep, open(OUT, "w"), indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
