"""diag_simui_roads_snap.py — DEFECT 1 static geometry census (diagnosis only).

Question: when does roads.route() give up and return a straight chord?
Law (wgresponse/roads.py route()): fallback iff (a) no graph, (b) either
endpoint farther than SNAP_MAX_M=2000 m from every node, or (c) A* finds no
path (disconnected).  The fetcher keeps only the largest connected component
(tools/fetch_sim_roads.py), so (c) should be structurally impossible — verify.

Measures, per real location:
  * connected components of the cached graph (verify single-component claim);
  * nearest-node distance distribution for 40 demo-placed clients (seed
    'demo', the gate-run placement) and for the safe zones;
  * counts beyond 2000 m (whole-leg chord trigger) and beyond 500/1000 m
    (long straight snap spur, still flagged on_roads=True);
  * route() smoke: staging zone -> each client, on_roads + spur share of
    polyline length (spur = the two straight snap sub-legs).
"""
import os
import sys
import math
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, roads
from wgresponse.contracts import ResponseConfig
from wgresponse.simui import LOCATIONS, load_tile, place_clients

CFG = ResponseConfig()


def components(g):
    seen = set()
    comps = []
    for nid in g.nodes:
        if nid in seen:
            continue
        q = deque([nid])
        seen.add(nid)
        n = 0
        while q:
            u = q.popleft()
            n += 1
            for v, _ei in g.neighbors(u):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        comps.append(n)
    comps.sort(reverse=True)
    return comps


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[i]


def main():
    print(f"SNAP_MAX_M = {roads.SNAP_MAX_M}")
    for key in sorted(LOCATIONS):
        g = roads.load_road_graph(key)
        if g is None:
            print(f"\n== {key}: NO ROAD GRAPH CACHED ==")
            continue
        tile, synth = load_tile(key)
        assert not synth, f"{key}: real tile missing"
        notes = []
        clients = place_clients(tile, 40, seed=f"{key}|demo", notes=notes)
        comps = components(g)
        print(f"\n== {key}: {len(g.nodes)} nodes, {len(g.edges)} edges, "
              f"components={len(comps)} (sizes {comps[:4]}{'...' if len(comps) > 4 else ''}) ==")

        cd = []
        for c in clients:
            nn = g.nearest_node(c.loc, 1e9)
            cd.append(nn[1] if nn else float("inf"))
        zd = []
        for z in LOCATIONS[key].safe_zones:
            nn = g.nearest_node(z.loc, 1e9)
            zd.append(nn[1] if nn else float("inf"))
        print(f"  clients->node m: med {pct(cd,50):7.1f}  p90 {pct(cd,90):7.1f} "
              f" max {max(cd):7.1f} | >500m {sum(d>500 for d in cd):2d}"
              f" >1000m {sum(d>1000 for d in cd):2d}"
              f" >2000m(CHORD) {sum(d>2000 for d in cd):2d} of {len(cd)}")
        print(f"  zones ->node m: {['%.0f' % d for d in zd]}")

        # route() smoke: staging zone (round-robin like SimSession) -> client
        zones = LOCATIONS[key].safe_zones
        chord = 0
        spur_fracs = []
        for i, c in enumerate(clients):
            z = zones[i % len(zones)]
            rr = roads.route(g, z.loc, c.loc, CFG)
            if not rr.on_roads:
                chord += 1
                continue
            sa = g.nearest_node(z.loc, roads.SNAP_MAX_M)
            sb = g.nearest_node(c.loc, roads.SNAP_MAX_M)
            spur = (sa[1] if sa else 0.0) + (sb[1] if sb else 0.0)
            spur_fracs.append(spur / max(rr.dist_m, 1e-9))
        print(f"  route(zone->client): {chord}/{len(clients)} whole-leg CHORD"
              f" fallbacks; spur share of dist: med {pct(spur_fracs,50)*100:5.1f}%"
              f" p90 {pct(spur_fracs,90)*100:5.1f}% max "
              f"{(max(spur_fracs)*100 if spur_fracs else float('nan')):5.1f}%")

        # client->client legs (stop->stop): sample all consecutive pairs
        cc_chord = 0
        cc_n = 0
        for a, b in zip(clients, clients[1:]):
            rr = roads.route(g, a.loc, b.loc, CFG)
            cc_n += 1
            if not rr.on_roads:
                cc_chord += 1
        print(f"  route(client->client): {cc_chord}/{cc_n} chord fallbacks")


if __name__ == "__main__":
    main()
