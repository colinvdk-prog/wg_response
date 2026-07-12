"""DIAGNOSIS ONLY (Defect 1 — crews do not stay on real roads).

Two measurements, read-only, on the REAL cached tiles + road graphs:

  PART 1 (all 5 locations): distance from every placed client home and every
  safe zone to the NEAREST road-graph node. Points beyond roads.SNAP_MAX_M
  (2000 m) force a WHOLE-LEG straight chord (on_roads=False). Points within
  it still get a straight SNAP SPUR (a->nearest node) rendered as on_roads=True.

  PART 2 (boulder_co, malibu_ca): run the REAL SimSession (40 clients / 4
  crews / high fire / 35 ticks) with a recording wrapper around roads.route,
  then classify every movement leg the crews actually took:
    - whole-leg chord (on_roads=False, 2-pt [a,b]) vs road polyline, by kind
    - per on-road leg: straight snap-spur length (nearest-node dist of each
      endpoint) and its fraction of the leg
    - sampled followed-path positions >75 m from the nearest road vertex
"""
from __future__ import annotations

import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import roads, simui, geo
from wgresponse.contracts import ResponseConfig

TILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "tiles")


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1))))
    return xs[k]


def summarize(name, dists):
    over = sum(1 for d in dists if d > roads.SNAP_MAX_M)
    print(f"    {name:14s} n={len(dists):3d}  "
          f"min={min(dists):6.0f}  med={statistics.median(dists):6.0f}  "
          f"p90={pct(dists, 90):6.0f}  max={max(dists):6.0f}  "
          f">2000m(chord)={over:3d} ({100.0 * over / len(dists):4.1f}%)")


def part1():
    print("=" * 78)
    print("PART 1 — client/zone distance to nearest road-graph NODE (meters)")
    print("  (>2000 m = roads.SNAP_MAX_M => WHOLE-LEG straight chord fallback)")
    print("=" * 78)
    for key in simui.LOCATIONS:
        g = roads.load_road_graph(key)
        if g is None:
            print(f"  {key}: no road graph"); continue
        tile, synth = simui.load_tile(key, tile_dir=TILE_DIR)
        clients = simui.place_clients(tile, 40, seed=f"{key}|demo")
        cds = [g.nearest_node(c.loc, max_m=1e9)[1] for c in clients]
        zds = [g.nearest_node(z.loc, max_m=1e9)[1]
               for z in simui.LOCATIONS[key].safe_zones]
        print(f"\n  {key}  ({len(g.nodes)} nodes)")
        summarize("clients", cds)
        summarize("safe_zones", zds)


class _RecRoads:
    """Recording delegate around wgresponse.roads (the roads_module seam)."""
    def __init__(self):
        self.calls = []       # (a, b, on_roads, npoints, dist_m)
    def load_road_graph(self, key):
        return roads.load_road_graph(key)
    def route(self, g, a, b, cfg):
        rr = roads.route(g, a, b, cfg)
        self.calls.append((a, b, rr.on_roads, len(rr.polyline), rr.dist_m))
        return rr


def _road_vertices(g):
    """All road vertices (nodes + edge geometry pts) for nearest-road sampling."""
    pts = list(g.nodes.values())
    for e in g.edges:
        pts.extend(e.geometry)
    return pts


class _Grid:
    """Coarse grid index for nearest-vertex queries (~1.1 km buckets)."""
    CELL = 0.01
    def __init__(self, pts):
        self.grid = {}
        for p in pts:
            k = (math.floor(p.lat / self.CELL), math.floor(p.lon / self.CELL))
            self.grid.setdefault(k, []).append(p)
    def nearest_m(self, p):
        qr = math.floor(p.lat / self.CELL)
        qc = math.floor(p.lon / self.CELL)
        best = float("inf")
        for k in range(0, 4):
            cells = ([(qr, qc)] if k == 0 else
                     [(qr - k, c) for c in range(qc - k, qc + k + 1)]
                     + [(qr + k, c) for c in range(qc - k, qc + k + 1)]
                     + [(r, qc - k) for r in range(qr - k + 1, qr + k)]
                     + [(r, qc + k) for r in range(qr - k + 1, qr + k)])
            for cell in cells:
                for q in self.grid.get(cell, ()):
                    d = geo.haversine_m(p, q)
                    if d < best:
                        best = d
            if best < (k) * self.CELL * 111_000.0 * math.cos(math.radians(p.lat)):
                break
        return best


def _densify(polyline, step_m=25.0):
    out = []
    for i in range(len(polyline) - 1):
        a, b = polyline[i], polyline[i + 1]
        d = geo.haversine_m(a, b)
        n = max(1, int(math.ceil(d / step_m)))
        for j in range(n):
            f = j / n
            out.append(geo.LatLon(a.lat + (b.lat - a.lat) * f,
                                  a.lon + (b.lon - a.lon) * f))
    if polyline:
        out.append(polyline[-1])
    return out


def part2(key, n_clients=40, n_crews=4, ticks=35, intensity="high"):
    print("\n" + "=" * 78)
    print(f"PART 2 — leg analysis on {key} ({n_clients} clients / {n_crews} "
          f"crews / {intensity} / {ticks} ticks)")
    print("=" * 78)
    g = roads.load_road_graph(key)
    grid = _Grid(_road_vertices(g))
    rec = _RecRoads()
    loc = simui.LOCATIONS[key]
    sess = simui.SimSession(key, n_clients, n_crews, seed="demo",
                            tile_dir=TILE_DIR, roads_module=rec)
    # ignite 6 km upwind of center along the canonical hazard wind (as the demo)
    import wgresponse.geo as _geo
    ang = math.radians(loc.hazard_wind_dir_deg)
    # move FROM center toward the wind source (upwind) ~6 km; find a burnable cell
    from wgresponse.fuels import is_burnable
    fired = None
    for dist in (6000, 5000, 7000, 4000, 8000):
        for dd in (0, 20, -20, 40, -40):
            a = math.radians(loc.hazard_wind_dir_deg + dd)
            dlat = (dist * math.cos(a)) / 111_000.0
            dlon = (dist * math.sin(a)) / (111_000.0 * math.cos(math.radians(loc.center.lat)))
            p = _geo.LatLon(loc.center.lat + dlat, loc.center.lon + dlon)
            rc = _geo.latlon_to_rc(sess._tile.spec, p)
            if rc and is_burnable(int(round(sess._tile.fuel.get(*rc)))):
                fired = p; break
        if fired:
            break
    fid = sess.ignite(fired.lat, fired.lon, intensity=intensity)
    # keep speed 1.0 (900 s/tick) — the cadence the founder watches; set_speed
    # (300) would advance 75 sim-hours/tick and teleport crews through legs.
    for _ in range(ticks):
        sess.tick()

    # classify recorded legs. _SimCrew calls route for ingress + egress legs
    # (and _refresh_egress_plan). We separate by inspecting on_roads/points.
    calls = rec.calls
    chord = [c for c in calls if not c[2]]
    road = [c for c in calls if c[2]]
    print(f"\n  roads.route calls: {len(calls)}  "
          f"on_roads=True: {len(road)}  chord(on_roads=False): {len(chord)} "
          f"({100.0 * len(chord) / max(1, len(calls)):.1f}%)")

    # spur analysis on the on-road legs (straight nearest-node snap gaps)
    spur_in = []
    spur_out = []
    offroad_frac = []
    for (a, b, on, npts, dist_m) in road:
        sa = g.nearest_node(a, max_m=1e9)[1]
        sb = g.nearest_node(b, max_m=1e9)[1]
        spur_in.append(sa)
        spur_out.append(sb)
        if dist_m > 0:
            offroad_frac.append((sa + sb) / dist_m)
    if road:
        print(f"  on-road leg SNAP SPURS (straight, mislabeled on_roads=True):")
        summarize("spur a->node", spur_in)
        summarize("spur node->b", spur_out)
        print(f"    off-road straight fraction of on-road legs: "
              f"med={statistics.median(offroad_frac)*100:.1f}%  "
              f"p90={pct(offroad_frac,90)*100:.1f}%  max={max(offroad_frac)*100:.1f}%")

    # sampled followed-path positions vs nearest road vertex
    all_samp = []
    over75 = 0
    for (a, b, on, npts, dist_m) in calls:
        rr = roads.route(g, a, b, ResponseConfig())  # recompute polyline
        for p in _densify(rr.polyline):
            d = grid.nearest_m(p)
            all_samp.append(d)
            if d > 75.0:
                over75 += 1
    if all_samp:
        print(f"\n  followed-path samples: {len(all_samp)}  "
              f">75 m off nearest road vertex: {over75} "
              f"({100.0 * over75 / len(all_samp):.1f}%)  "
              f"med={statistics.median(all_samp):.0f} m  max={max(all_samp):.0f} m")

    # scoreboard sanity (the run really happened)
    st = sess.state()
    sb = st["scoreboard"]
    onroads_flags = [(c["route_on_roads"], c["egress_on_roads"]) for c in st["crews"]]
    print(f"\n  run sanity: served={sb['served']} violations={sb['violations']} "
          f"withdrawn={sb['withdrawn']}  crew on_roads flags now={onroads_flags}")
    # any straight legs from egress plans specifically?
    return calls


def part3():
    """Deterministic leg census across ALL 5 locations, independent of dispatch
    physics: for each placed client, classify the ingress leg (staging zone ->
    home) and the egress leg (home -> nearest zone). A leg is a WHOLE-LEG chord
    iff either endpoint is >2000 m from the network; otherwise it carries a
    straight snap SPUR = each endpoint's nearest-node distance."""
    print("\n" + "=" * 78)
    print("PART 3 — deterministic ingress/egress leg census (all 5 locations)")
    print("  ingress = round-robin staging zone -> each of 40 client homes")
    print("  egress  = each home -> nearest safe zone")
    print("=" * 78)
    cfg = ResponseConfig()
    for key in simui.LOCATIONS:
        g = roads.load_road_graph(key)
        tile, _ = simui.load_tile(key, tile_dir=TILE_DIR)
        clients = simui.place_clients(tile, 40, seed=f"{key}|demo")
        zones = list(simui.LOCATIONS[key].safe_zones)
        legs = []
        for i, c in enumerate(clients):
            z_stage = zones[i % len(zones)]
            z_egr = min(zones, key=lambda z: geo.haversine_m(c.loc, z.loc))
            legs.append(("ingress", z_stage.loc, c.loc))
            legs.append(("egress", c.loc, z_egr.loc))
        chords = 0
        term_spurs = []       # the home/zone-side snap spur (the visible approach)
        for kind, a, b in legs:
            rr = roads.route(g, a, b, cfg)
            if not rr.on_roads:
                chords += 1
            # home-side spur for ingress is b; for egress is a (the home)
            home = b if kind == "ingress" else a
            term_spurs.append(g.nearest_node(home, max_m=1e9)[1])
        vis150 = sum(1 for s in term_spurs if s > 150.0)
        vis300 = sum(1 for s in term_spurs if s > 300.0)
        print(f"\n  {key}: {len(legs)} legs  chords(on_roads=False)={chords} "
              f"({100.0*chords/len(legs):.1f}%)")
        print(f"    home-approach straight spur: med={statistics.median(term_spurs):.0f} m "
              f"p90={pct(term_spurs,90):.0f} m max={max(term_spurs):.0f} m  "
              f">150m={vis150} >300m={vis300}")


if __name__ == "__main__":
    part1()
    part3()
    part2("boulder_co")
    part2("malibu_ca")
