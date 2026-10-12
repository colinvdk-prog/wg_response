"""wgresponse.roads — real-road graphs + routing for the interactive simulator.

Contract seam (contracts MODULE SEAMS, founder UX request 2026-07-10 — crews
must visibly travel on real roads):

    CLASS_SPEED_MPS              free-flow speed table by OSM highway class
    class RoadGraph              nodes {node_id: LatLon} + RoadEdge adjacency
    save_road_graph(g, path)     dep-free versioned cache write (atomic)
    load_road_graph(key) -> RoadGraph | None      (None when absent, never raises
                                                   for a missing cache file)
    route(g, a, b, cfg) -> RoadRoute(polyline, time_s, dist_m, on_roads)
    route_on_path(g, a, node_path, edge_path, cfg) -> RoadRoute
                                 the same assembly along a node path found by
                                 an external search (simui's SEM-10d road-
                                 network escape): origin spur + road path,
                                 ending ON the last node

DIVISION OF AUTHORITY (contracts): dispatch.plan keeps the calibrated tier-0
travel model (circuity x fire-traffic, wgresponse.travel); the SIMULATOR moves
crews along RoadRoute polylines and re-plans from actual positions each tick,
so model-vs-road divergence self-corrects. Road times here therefore apply
cfg.fire_traffic_factor but NOT cfg.circuity (the polyline already follows the
real geometry — circuity is the tier-0 stand-in for exactly that geometry).

ROUTING SEMANTICS (FOUNDER ROUND 3 law 2, ROADS ADHERENCE)
    route(g, a, b, cfg) builds a THREE-PHASE leg so the crew's position always
    lies ON the returned polyline and the ONLY straight parts are the two end
    spurs:  [origin -> SPUR -> nearest origin node] + [A* road path] +
            [nearest dest node -> SPUR -> dest].
      * both endpoints snap to their nearest graph NODE (grid-bucketed index,
        deterministic ties -> smaller node id) within cfg.road_spur_max_m; the
        two snap sub-legs a->node and node->b are the SPUR segments — straight
        lines charged at residential speed (SNAP_SPEED_MPS) x
        cfg.fire_traffic_factor, flagged spur=True in the route metadata (see
        SPUR METADATA). A spur is emitted ONLY when the endpoint does not
        already coincide with its node (an on-node endpoint has no spur);
      * node-to-node path = stdlib A* (heapq) on free-flow edge seconds with
        the admissible haversine/max-speed heuristic. cfg.fire_traffic_factor
        scales every edge uniformly, so the argmin path is identical whether
        or not the factor is inside the search; it is applied when summing
        the returned time_s (never baked into the cache);
      * the road middle follows per-edge saved geometry (OSM way curvature), so
        crews render on curves, not chords; it is flagged spur=False (real
        road) — the solid portion the UI draws;
      * on_roads=True iff the leg follows the network with BOTH end spurs no
        longer than cfg.road_spur_max_m (the crew genuinely lies on the
        returned polyline, spurs included);
      * WHOLE-LEG CHORD FALLBACK (on_roads=False, a single kind="chord"
        segment, NEVER silently pretends a road route) — legal ONLY when there
        is no graph / empty graph, EITHER endpoint is farther than
        cfg.road_spur_max_m from every node, or the snapped nodes are on
        disconnected components. It returns polyline [a, b] with tier-0
        travel.travel_time_s(a, b, cfg) and dist_m = haversine, carries a loud
        one-line `note` naming the off-network distance, AND logs a warning.
    Deterministic: same (graph, a, b, cfg) always returns the same RoadRoute;
    heap ties break on node id; no RNG anywhere.

SPUR METADATA (simui.py + simulator.html read this to draw the road portion
solid and the spur/chord dashed; the schema is a STABLE contract):
    RoadRoute.polyline  — the FULL ordered a..b points (BACK-COMPAT, unchanged;
        legacy callers that read only .polyline are unaffected).
    RoadRoute.segments  — an ordered, gap-free cover of `polyline` as maximal
        same-kind runs; each RouteSegment(points: tuple[LatLon], kind, spur):
            kind="road"  spur=False  real OSM geometry (draw solid)
            kind="spur"  spur=True   short straight end connector <= the cap
            kind="chord" spur=True   whole-leg straight fallback, on_roads=False
        Consecutive segments SHARE their boundary vertex, so
            polyline == list(segments[0].points)
                        + [p for s in segments[1:] for p in s.points[1:]]
        reconstructs the flat polyline EXACTLY. A normal leg is at most
        [spur?, road, spur?]; a fallback is exactly [chord].
    RoadRoute.spur_mask — a derived per-EDGE view: list[bool] of length
        len(polyline)-1; spur_mask[i] is True iff segment
        polyline[i]->polyline[i+1] is straight/off-network. (polyline,
        spur_mask) and segments reconstruct each other (group equal runs).
    RoadRoute.note      — the loud one-line chord-fallback reason (None on a
        clean road route).

CACHE FORMAT data/roads/<key>.wgr (dep-free text, mirrors terrain.py's
save/load_ascii_tile discipline: write-tmp-then-os.replace, stdlib only):
    line 1:  "WGR 1"                    (magic + format version)
    line 2+: one JSON object:
        {"format": "wgr-road-graph", "version": 1, "meta": {...provenance...},
         "classes": [highway_class, ...],
         "nodes":  [[lat, lon], ...],                    # node_id = index
         "edges":  [[u, v, class_idx, length_m, [glat, glon, ...]], ...]}
    Compactness: node ids are renumbered densely 0..N-1 (ascending original
    id) at save; coords rounded to 1e-5 deg (~1 m); length_m rounded 0.1 m;
    edge geometry lists only the INTERMEDIATE vertices (endpoints live in
    nodes), flattened lat,lon pairs oriented u->v, with u <= v canonical.
    speed_mps is deliberately NOT stored: it is re-derived from
    CLASS_SPEED_MPS at load, so a speed-table update reaches existing caches
    and cfg.fire_traffic_factor stays a route-time knob.

Units per contracts law: meters `_m`, seconds `_s`, m/s `_mps`, WGS84 degrees.
Stdlib only; no third-party deps anywhere.
"""
from __future__ import annotations

import heapq
import json
import logging
import math
import os
import threading
from array import array
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wgresponse import geo, travel
from wgresponse.contracts import LatLon, ResponseConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Speed table (module constants)
# ---------------------------------------------------------------------------

#: Free-flow road speed by OSM ``highway`` class, m/s.
#: PROVENANCE: OSM-routing-convention defaults — the typical free-flow speeds
#: community routing engines assume for ways with no maxspeed tag (cf. the
#: OSRM car profile / Valhalla automobile costing defaults and the class
#: ladder in docs/research/dispatch_optimization.md section 4.1): motorway
#: 29 m/s (~65 mph), trunk/primary 22 (~49 mph), secondary 18 (~40 mph),
#: tertiary 15 (~34 mph), residential/unclassified 11 (~25 mph), service/
#: track 7 (~16 mph). ``*_link`` ramps are charged ONE TIER BELOW their
#: parent class (routing engines cost ramps well under the mainline).
#: These are FREE-FLOW speeds: cfg.fire_traffic_factor is applied at ROUTE
#: time by route(), never baked into cached .wgr files — mirroring
#: wgresponse.travel's tier-0 convention.
CLASS_SPEED_MPS = {
    "motorway": 29.0,
    "motorway_link": 22.0,
    "trunk": 22.0,
    "trunk_link": 18.0,
    "primary": 22.0,
    "primary_link": 18.0,
    "secondary": 18.0,
    "secondary_link": 15.0,
    "tertiary": 15.0,
    "tertiary_link": 11.0,
    "unclassified": 11.0,
    "residential": 11.0,
    "service": 7.0,
    "track": 7.0,
}

#: Fallback for classes not in the table (hand-built graphs / future fetch
#: filters): residential-equivalent, conservative.
DEFAULT_SPEED_MPS = 11.0

#: Snap sub-legs (endpoint <-> nearest node) are straight lines charged at
#: residential speed — the last-hundred-meters driveway approximation.
SNAP_SPEED_MPS = 11.0

#: Default off-network spur ceiling AND RoadGraph.nearest_node's default cap.
#: route() uses cfg.road_spur_max_m (contracts, default 2000.0 == this value)
#: as the LIVE knob; an endpoint farther than the cap from every graph node
#: cannot honestly claim a road route -> whole-leg chord fallback
#: (on_roads=False). Kept as a module constant for the nearest_node default and
#: back-compat imports.
SNAP_MAX_M = 2000.0

#: Fastest class in the table; A* heuristic divisor (admissible: no edge is
#: traversed faster than this unless the graph itself carries a higher
#: explicit speed, in which case RoadGraph._vmax_mps takes over).
MAX_TABLE_SPEED_MPS = max(CLASS_SPEED_MPS.values())

#: Snap-index bucket size, degrees (~1.1 km of latitude).
_SNAP_CELL_DEG = 0.01

_FORMAT_MAGIC = "WGR"
_FORMAT_VERSION = 1

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROAD_DIR = _REPO_ROOT / "data" / "roads"


#: RoadGraph.nearest_node memo bound (entries per graph; cleared whole on
#: overflow — simple, deterministic: the memo only ever returns what the
#: search itself returns).
_SNAP_MEMO_MAX = 100_000
#: route() memo bound (results per graph; cleared whole on overflow). A
#: road route is a pure function of the graph, its exact endpoints and four
#: cfg numbers, so every session on one graph — another visitor, another
#: intensity of the same one-click scenario, a reset — reuses the first
#: one's routes (first-step perf 2026-10-12). The result objects are shared
#: read-only, exactly as one session already shares them across its caches.
_ROUTE_MEMO_MAX = 20_000
_MISS = object()


def class_speed_mps(highway_class: str) -> float:
    """Free-flow speed for an OSM highway class (DEFAULT_SPEED_MPS when the
    class is not in CLASS_SPEED_MPS)."""
    return CLASS_SPEED_MPS.get(highway_class, DEFAULT_SPEED_MPS)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoadEdge:
    """One undirected road segment between graph nodes u and v.

    geometry = INTERMEDIATE vertices only (u->v order, endpoints excluded) so
    polylines follow the real curvature. speed_mps is free-flow; pass 0.0 (the
    default) to derive it from CLASS_SPEED_MPS — the normal path everywhere."""
    u: int
    v: int
    highway_class: str
    length_m: float
    speed_mps: float = 0.0
    geometry: tuple = ()

    def __post_init__(self):
        if self.length_m < 0.0:
            raise ValueError(f"edge ({self.u},{self.v}) length_m < 0")
        if self.speed_mps < 0.0:
            raise ValueError(f"edge ({self.u},{self.v}) speed_mps < 0")
        if self.speed_mps == 0.0:
            object.__setattr__(self, "speed_mps",
                               class_speed_mps(self.highway_class))
        if not isinstance(self.geometry, tuple):
            object.__setattr__(self, "geometry", tuple(self.geometry))


@dataclass(frozen=True)
class RouteSegment:
    """One maximal same-kind run of a RoadRoute.polyline (module docstring SPUR
    METADATA). `points` is a tuple[LatLon] of >= 1 vertex; consecutive segments
    of a route share their boundary vertex.
        kind="road"  spur=False  real OSM way geometry (UI draws solid)
        kind="spur"  spur=True   short straight end connector <= road_spur_max_m
        kind="chord" spur=True   whole-leg straight fallback (on_roads=False)
    spur == (kind != "road") — the single 'is this a straight, off-network part'
    flag the UI keys the dashed style off."""
    points: tuple
    kind: str
    spur: bool

    def __post_init__(self):
        if not isinstance(self.points, tuple):
            object.__setattr__(self, "points", tuple(self.points))


@dataclass(frozen=True)
class RoadRoute:
    """route() result (FOUNDER ROUND 3 law 2, ROADS ADHERENCE).

    polyline  — a->b inclusive (list[LatLon]), the FULL ordered path; time_s
                and dist_m cover the WHOLE polyline including the end spurs.
    on_roads  — True iff the leg follows the network with both end spurs no
                longer than cfg.road_spur_max_m (crew lies ON the polyline);
                False marks the whole-leg straight-leg tier-0 fallback (a single
                kind="chord" segment — never a pretended road route).
    segments  — ordered, gap-free cover of `polyline` as maximal same-kind
                RouteSegment runs (module docstring SPUR METADATA): a normal leg
                is [spur?, road, spur?]; a fallback is [chord]. simui.py +
                simulator.html read this to draw road solid, spur/chord dashed.
    note      — loud one-line reason on the chord fallback (names the
                off-network distance); None on a clean road route.
    Back-compat: callers reading only .polyline / .time_s / .dist_m / .on_roads
    are unaffected; segments/note are additive (segments defaults to [].)"""
    polyline: list
    time_s: float
    dist_m: float
    on_roads: bool
    segments: list = field(default_factory=list)
    note: Optional[str] = None

    @property
    def spur_mask(self) -> list:
        """Derived per-EDGE view aligned to `polyline`: list[bool] of length
        len(polyline)-1; spur_mask[i] is True iff segment
        polyline[i]->polyline[i+1] is a straight off-network part (spur or
        chord), False iff real road geometry. Derived from `segments` (each
        segment of k points contributes k-1 identical edge flags)."""
        mask: list = []
        for seg in self.segments:
            mask.extend([seg.spur] * (len(seg.points) - 1))
        return mask


class RoadGraph:
    """Undirected road graph: nodes {node_id: LatLon} + RoadEdge list.

    Adjacency is built at construction (node -> [(other_id, edge_index)],
    sorted for determinism). Self-loop edges (u == v) are retained in
    .edges for provenance but excluded from adjacency — they can never
    improve a route. The nearest-node snap index (grid buckets of
    _SNAP_CELL_DEG degrees) is built lazily on first use, and so are the A*
    tables (_astar_tables) and the snap memo (nearest_node) — pure functions
    of the graph, which is never mutated after construction."""

    # __weakref__: lets the simulator share one loaded graph across its
    # concurrent sessions (simui._load_road_graph_shared, a
    # WeakValueDictionary) without keeping it alive after the last session.
    __slots__ = ("nodes", "edges", "meta", "_adj", "_snap_grid", "_vmax_mps",
                 "_astar_tab", "_snap_memo", "_spt", "_route_memo",
                 "__weakref__")

    def __init__(self, nodes: dict, edges: list, meta: Optional[dict] = None):
        self.nodes = dict(nodes)
        self.edges = list(edges)
        self.meta = dict(meta or {})
        self._adj: dict = {}
        self._snap_grid: Optional[dict] = None
        self._astar_tab = None
        self._snap_memo: dict = {}
        self._spt = None                # shortest-path trees (_spt_state)
        self._route_memo: dict = {}     # route() results (exact endpoints)
        vmax = MAX_TABLE_SPEED_MPS
        for i, e in enumerate(self.edges):
            if e.u not in self.nodes or e.v not in self.nodes:
                raise ValueError(f"edge {i} ({e.u},{e.v}) references unknown node")
            if e.speed_mps <= 0.0:
                raise ValueError(f"edge {i} ({e.u},{e.v}) speed_mps must be > 0")
            vmax = max(vmax, e.speed_mps)
            if e.u == e.v:
                continue  # self-loop: kept in .edges, useless for routing
            self._adj.setdefault(e.u, []).append((e.v, i))
            self._adj.setdefault(e.v, []).append((e.u, i))
        for lst in self._adj.values():
            lst.sort()
        self._vmax_mps = vmax

    # -- introspection ---------------------------------------------------

    def neighbors(self, node_id: int) -> list:
        """[(other_node_id, edge_index), ...] sorted; [] for unknown/isolated."""
        return self._adj.get(node_id, [])

    # -- nearest-node snap -------------------------------------------------

    def _grid(self) -> dict:
        if self._snap_grid is None:
            grid: dict = {}
            for nid in sorted(self.nodes):
                p = self.nodes[nid]
                key = (math.floor(p.lat / _SNAP_CELL_DEG),
                       math.floor(p.lon / _SNAP_CELL_DEG))
                grid.setdefault(key, []).append(nid)
            self._snap_grid = grid
        return self._snap_grid

    def nearest_node(self, p: LatLon, max_m: float = SNAP_MAX_M):
        """(node_id, dist_m) of the node nearest to p, or None when every node
        is farther than max_m. O(1)-ish: expanding square rings of grid
        buckets; deterministic ties -> smaller node id. Memoised per exact
        (p, max_m) — a pure function of the never-mutated graph (first-step
        perf 2026-10-12: every road route snaps both ends, and the planner
        routes each client to every other one); bounded, cleared whole on
        overflow."""
        k = (p.lat, p.lon, max_m)
        hit = self._snap_memo.get(k, _MISS)
        if hit is not _MISS:
            return hit
        out = self._nearest_node(p, max_m)
        if len(self._snap_memo) >= _SNAP_MEMO_MAX:
            self._snap_memo.clear()
        self._snap_memo[k] = out
        return out

    def _nearest_node(self, p: LatLon, max_m: float):
        if not self.nodes:
            return None
        grid = self._grid()
        qr = math.floor(p.lat / _SNAP_CELL_DEG)
        qc = math.floor(p.lon / _SNAP_CELL_DEG)
        # Conservative meters spanned by one bucket along its SHORT axis:
        # a candidate in a ring-k bucket is at least (k-1)*cell_min_m away.
        cell_min_m = _SNAP_CELL_DEG * geo.M_PER_DEG_LAT * max(
            0.1, math.cos(math.radians(p.lat)))
        best: Optional[tuple] = None          # (dist_m, node_id)
        k = 0
        while True:
            ring_floor_m = (k - 1) * cell_min_m
            if ring_floor_m > max_m:
                break
            if best is not None and ring_floor_m > best[0]:
                break
            if k == 0:
                cells = [(qr, qc)]
            else:
                cells = []
                for c in range(qc - k, qc + k + 1):
                    cells.append((qr - k, c))
                    cells.append((qr + k, c))
                for r in range(qr - k + 1, qr + k):
                    cells.append((r, qc - k))
                    cells.append((r, qc + k))
            for cell in cells:
                for nid in grid.get(cell, ()):
                    d = geo.haversine_m(p, self.nodes[nid])
                    cand = (d, nid)
                    if best is None or cand < best:
                        best = cand
            k += 1
        if best is None or best[0] > max_m:
            return None
        return best[1], best[0]


# ---------------------------------------------------------------------------
# Cache file I/O
# ---------------------------------------------------------------------------

def road_file(key: str, road_dir=None) -> Path:
    """Path of the cached road graph for a location key: data/roads/<key>.wgr
    under the repo root unless road_dir overrides (tests point this at tmp
    dirs so logic tests never depend on the fetched real graphs)."""
    base = Path(road_dir) if road_dir is not None else DEFAULT_ROAD_DIR
    return base / f"{key}.wgr"


def save_road_graph(g: RoadGraph, path) -> None:
    """Write g to the versioned dep-free .wgr format (atomic tmp+replace).

    Node ids are renumbered densely 0..N-1 in ascending original-id order
    (a graph whose ids are already dense-ascending round-trips id-identically);
    coords quantized to 1e-5 deg, lengths to 0.1 m; edges canonicalized
    u <= v (geometry reversed to match) and sorted for byte-stable output."""
    path = str(path)
    ids = sorted(g.nodes)
    id_map = {old: new for new, old in enumerate(ids)}
    nodes_out = [[round(g.nodes[old].lat, 5), round(g.nodes[old].lon, 5)]
                 for old in ids]
    classes = sorted({e.highway_class for e in g.edges})
    class_idx = {c: i for i, c in enumerate(classes)}
    edges_out = []
    for e in g.edges:
        u, v = id_map[e.u], id_map[e.v]
        geom = e.geometry
        if u > v:
            u, v = v, u
            geom = tuple(reversed(geom))
        flat = []
        for pt in geom:
            flat.append(round(pt.lat, 5))
            flat.append(round(pt.lon, 5))
        edges_out.append([u, v, class_idx[e.highway_class],
                          round(e.length_m, 1), flat])
    edges_out.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    body = {
        "format": "wgr-road-graph",
        "version": _FORMAT_VERSION,
        "meta": g.meta,
        "classes": classes,
        "nodes": nodes_out,
        "edges": edges_out,
    }
    text = (f"{_FORMAT_MAGIC} {_FORMAT_VERSION}\n"
            + json.dumps(body, sort_keys=True, separators=(",", ":"))
            + "\n")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def load_road_graph(key: str, road_dir=None) -> Optional[RoadGraph]:
    """RoadGraph for a location key, or None when the cache file is ABSENT
    (missing is a supported state — the simulator falls back to straight
    legs; never raises for missing). A PRESENT-but-corrupt file raises
    ValueError loudly: silent garbage must not masquerade as roads."""
    path = road_file(key, road_dir)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    nl = text.find("\n")
    header = text[:nl if nl >= 0 else len(text)].split()
    if len(header) != 2 or header[0] != _FORMAT_MAGIC:
        raise ValueError(f"bad road-graph header in {path}: not a .wgr file")
    if header[1] != str(_FORMAT_VERSION):
        raise ValueError(f"unsupported .wgr version {header[1]!r} in {path} "
                         f"(this build reads version {_FORMAT_VERSION})")
    try:
        body = json.loads(text[nl + 1:])
    except ValueError as e:
        raise ValueError(f"corrupt road-graph JSON body in {path}: {e}") from e
    if body.get("version") != _FORMAT_VERSION:
        raise ValueError(f"header/body version mismatch in {path}")
    try:
        classes = body["classes"]
        nodes = {i: LatLon(lat, lon)
                 for i, (lat, lon) in enumerate(body["nodes"])}
        edges = []
        for u, v, ci, length_m, flat in body["edges"]:
            geom = tuple(LatLon(flat[i], flat[i + 1])
                         for i in range(0, len(flat), 2))
            edges.append(RoadEdge(u=u, v=v, highway_class=classes[ci],
                                  length_m=length_m, geometry=geom))
    except (KeyError, TypeError, IndexError) as e:
        raise ValueError(f"corrupt road-graph structure in {path}: "
                         f"{type(e).__name__}: {e}") from e
    return RoadGraph(nodes=nodes, edges=edges, meta=body.get("meta") or {})


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _fallback(a: LatLon, b: LatLon, cfg: ResponseConfig,
              reason: str) -> RoadRoute:
    """Whole-leg straight-leg tier-0 fallback (contracts law 2: NEVER silently
    pretend a road route). Emits a single kind="chord" segment, carries the
    loud `reason` as .note, AND logs a warning naming the chord distance so the
    off-network leg stays visible. time = travel.travel_time_s (haversine x
    circuity x fire factor / blended speed); dist = honest haversine of [a, b]."""
    dist_m = geo.haversine_m(a, b)
    logger.warning("roads.route straight-leg fallback (on_roads=False): %s "
                   "[chord %.0f m]", reason, dist_m)
    seg = RouteSegment(points=(a, b), kind="chord", spur=True)
    return RoadRoute(polyline=[a, b],
                     time_s=travel.travel_time_s(a, b, cfg),
                     dist_m=dist_m, on_roads=False,
                     segments=[seg], note=reason)


def _astar_tables(g: RoadGraph):
    """(cost adjacency, per-node trig) for _astar, built once per graph:
    node -> ((other, edge_index, length_m / speed_mps), ...) in neighbors()
    order, and node -> (radians(lat), cos(radians(lat)), lon) — exactly the
    values _astar and geo.haversine_m compute inline, so the search is
    bit-identical (first-step perf 2026-10-12)."""
    tab = g._astar_tab
    if tab is None:
        edges = g.edges
        adjc = {}
        for nid, lst in g._adj.items():
            adjc[nid] = tuple((other, ei,
                               edges[ei].length_m / edges[ei].speed_mps)
                              for other, ei in lst)
        trig = {}
        for nid, p in g.nodes.items():
            phi = math.radians(p.lat)
            trig[nid] = (phi, math.cos(phi), p.lon)
        tab = g._astar_tab = (adjc, trig)
    return tab


# ---------------------------------------------------------------------------
# Shortest-path trees: the A* result without the A* search (perf 2026-10-12)
# ---------------------------------------------------------------------------
#
# SHORTEST-PATH TREES (first-step perf 2026-10-12; docs/SIMULATOR_DESIGN.md
# "The slow first step"). The planner routes every client to every other
# one, so most searches start from a node many searches started from before.
# From the _SPT_AFTER-th search out of one start node, that node gets ONE
# exact Dijkstra tree over the graph, and _astar returns a goal's tree path
# instead of searching — but ONLY when a uniqueness margin PROVES that A*
# would return that very path; otherwise A* runs exactly as before.
#
# The proof. Let P be the tree path s -> t (a shortest path: Dijkstra's
# float path sums are minimal, float addition being monotone). A* here is a
# graph search without reopening whose heuristic h = haversine-to-goal /
# vmax is, on a handful of edges per graph, INCONSISTENT by up to a few
# hundredths of a second (node coordinates are stored to 1e-5 deg, lengths
# to 0.1 m; measured on every committed graph — so it is not strictly
# admissible either, which the argument never needs): delta(u, v) =
# max(0, h(u) - c(u, v) - h(v)) <= dhat(u, v) =
# max(0, haversine(u, v) / vmax - c(u, v)) by the triangle inequality. By
# induction over the order nodes are closed, every node v_j of P is closed
# with g <= g*(v_j) + sum of delta over P's first j edges (when v_j pops,
# the first not-yet-closed node v_m of P sits in the heap with a near-
# optimal g and f(v_j) <= f(v_m), and h(v_m) - h(v_j) is at most P's cost
# from v_m to v_j plus the delta on the way), so the path A* returns costs
# at most C* + tau_P, tau_P = the sum of delta over P (computed from A*'s
# own heuristic toward this goal, on the edges where dhat > 0 — dhat bounds
# delta, so the other edges contribute nothing). The margin: for every
# node v of P and every other edge (u', e') into v whose u' is not later on
# P, dist(u') + c(e') - dist(v) > tau_P + _SPT_EPS_S. Any other simple path
# to t leaves P's suffix through such an edge, so it costs more than C* +
# tau_P — hence A*'s path IS P, node for node and edge for edge (a parallel
# edge is an alternative too). Ties and near-ties fail the margin and fall
# back to A*. Checked on every road query of the 13 one-click scenarios'
# first steps (tests/test_roads.py pins many-targets-per-source queries on
# real graphs against the search).
_SPT_AFTER = 2
#: per-graph tree memory bound (bytes; least recently used tree evicted)
_SPT_MAX_BYTES = 48 << 20
#: certificate slack (seconds) on top of tau_P: orders of magnitude above the
#: float rounding of second-scale path sums, far below any real alternative
_SPT_EPS_S = 1e-6
_UNREACHABLE = object()


class _SptState:
    """One graph's tree cache (built lazily; guarded by `lock`)."""
    __slots__ = ("lock", "idx", "ids", "adj", "dhat", "count", "trees",
                 "tree_bytes")

    def __init__(self, g: "RoadGraph"):
        self.lock = threading.Lock()
        adjc, _trig = _astar_tables(g)
        self.ids = sorted(g.nodes)
        self.idx = {nid: i for i, nid in enumerate(self.ids)}
        idx = self.idx
        self.adj = [tuple((idx[o], ei, c) for o, ei, c in adjc.get(nid, ()))
                    for nid in self.ids]
        vmax = g._vmax_mps
        dhat = array("d", [0.0]) * len(g.edges)
        for ei, e in enumerate(g.edges):
            if e.u == e.v:
                continue
            over = (geo.haversine_m(g.nodes[e.u], g.nodes[e.v]) / vmax
                    - e.length_m / e.speed_mps)
            if over > 0.0:
                dhat[ei] = over
        self.dhat = dhat
        self.count: dict = {}
        self.trees: OrderedDict = OrderedDict()
        self.tree_bytes = 16 * len(self.ids)


def _spt_state(g: "RoadGraph") -> "_SptState":
    st = g._spt
    if st is None:
        st = g._spt = _SptState(g)
    return st


def _spt_build(st: "_SptState", src: int):
    """Exact Dijkstra from dense index src over the whole graph: (dist,
    pred node, pred edge) arrays — float path sums, strict improvement."""
    n = len(st.ids)
    dist = array("d", [math.inf]) * n
    pn = array("i", [-1]) * n
    pe = array("i", [-1]) * n
    done = bytearray(n)
    adj = st.adj
    heappush, heappop = heapq.heappush, heapq.heappop
    dist[src] = 0.0
    heap = [(0.0, src)]
    while heap:
        d, u = heappop(heap)
        if done[u]:
            continue
        done[u] = 1
        for v, ei, c in adj[u]:
            if done[v]:
                continue
            nd = d + c
            if nd < dist[v]:
                dist[v] = nd
                pn[v] = u
                pe[v] = ei
                heappush(heap, (nd, v))
    return dist, pn, pe


def _spt_path(g: "RoadGraph", st: "_SptState", tree, s: int, t: int):
    """A*'s (node_path, edge_path) s -> t (dense indices in, node ids out)
    when the tree's margin proves it (module comment above), _UNREACHABLE
    when t is not reachable from s (A* returns None then too), else None:
    run A*."""
    dist, pn, pe = tree
    if dist[t] == math.inf:
        return _UNREACHABLE
    nodes = [t]
    edges = []
    v = t
    while v != s:
        edges.append(pe[v])
        v = pn[v]
        nodes.append(v)
    nodes.reverse()
    edges.reverse()
    dhat = st.dhat
    ids = st.ids
    tau = _SPT_EPS_S
    hgoal = None
    for k, ei in enumerate(edges):
        if dhat[ei] > 0.0:
            # delta = max(0, h(u) - c(u, v) - h(v)) along P's direction, with
            # _astar_search's own heuristic toward this goal
            if hgoal is None:
                hgoal = _HeurToGoal(g, ids[t])
            e = g.edges[ei]
            over = (hgoal(ids[nodes[k]]) - e.length_m / e.speed_mps
                    - hgoal(ids[nodes[k + 1]]))
            if over > 0.0:
                tau += over
    adj = st.adj
    later = set()
    for i in range(len(nodes) - 1, 0, -1):
        v = nodes[i]
        pu = nodes[i - 1]
        pei = edges[i - 1]
        dv = dist[v]
        for u2, e2, c2 in adj[v]:
            if e2 == pei and u2 == pu:
                continue
            if u2 in later:
                continue
            if (dist[u2] + c2) - dv <= tau:
                return None                     # a (near-)tie: run A*
        later.add(v)
    return [ids[i] for i in nodes], edges


class _HeurToGoal:
    """_astar_search's heuristic toward one goal node, the same float."""
    __slots__ = ("trig", "phi2", "cos2", "lon2", "two_r", "vmax")

    def __init__(self, g: "RoadGraph", goal: int):
        _adjc, trig = _astar_tables(g)
        self.trig = trig
        self.phi2, self.cos2, self.lon2 = trig[goal]
        self.two_r = 2.0 * geo.EARTH_RADIUS_M
        self.vmax = g._vmax_mps

    def __call__(self, node: int) -> float:
        phi1, cos1, lon1 = self.trig[node]
        dphi = self.phi2 - phi1
        dlam = math.radians(self.lon2 - lon1)
        hh = (math.sin(dphi / 2.0) ** 2
              + cos1 * self.cos2 * math.sin(dlam / 2.0) ** 2)
        return self.two_r * math.asin(min(1.0, math.sqrt(hh))) / self.vmax


def _astar(g: RoadGraph, start: int, goal: int):
    """A* over free-flow edge seconds. Returns (node_path, edge_index_path)
    or None when start and goal are disconnected. Heuristic: haversine to
    goal / fastest speed present (admissible + consistent). Deterministic:
    heap ties break on node id; strict-improvement relaxation.

    From the _SPT_AFTER-th search out of one start node the result comes
    from that node's shortest-path tree whenever the tree's uniqueness
    margin proves it is A*'s (module comment "Shortest-path trees"), else
    from the search itself (_astar_search) — the same paths either way."""
    if start == goal:
        return [start], []
    st = _spt_state(g)
    tree = None
    with st.lock:
        tree = st.trees.get(start)
        if tree is not None:
            st.trees.move_to_end(start)
        else:
            n = st.count.get(start, 0) + 1
            st.count[start] = n
            if n >= _SPT_AFTER:
                tree = _spt_build(st, st.idx[start])
                st.trees[start] = tree
                while (len(st.trees) > 1
                       and len(st.trees) * st.tree_bytes > _SPT_MAX_BYTES):
                    st.trees.popitem(last=False)
    if tree is not None:
        r = _spt_path(g, st, tree, st.idx[start], st.idx[goal])
        if r is _UNREACHABLE:
            return None
        if r is not None:
            return r
    return _astar_search(g, start, goal)


def _astar_search(g: RoadGraph, start: int, goal: int):
    """The A* search itself (see _astar). Edge costs and the per-node trig
    of the heuristic come from _astar_tables, and each node's heuristic is
    computed once per search — geo.haversine_m's expression on the same
    values, so every f-value, every heap order and the returned path are
    the original search's."""
    if start == goal:
        return [start], []
    adjc, trig = _astar_tables(g)
    nodes = g.nodes
    goal_ll = nodes[goal]
    vmax = g._vmax_mps
    h0 = geo.haversine_m(nodes[start], goal_ll) / vmax
    phi2, cos2, lon2 = trig[goal]
    two_r = 2.0 * geo.EARTH_RADIUS_M
    sin, asin, sqrt, radians = math.sin, math.asin, math.sqrt, math.radians
    heappush, heappop = heapq.heappush, heapq.heappop
    inf = math.inf
    g_score = {start: 0.0}
    came: dict = {}
    hmemo: dict = {}
    open_heap = [(h0, start)]
    closed = set()
    while open_heap:
        _f, cur = heappop(open_heap)
        if cur == goal:
            node_path = [cur]
            edge_path = []
            while cur != start:
                cur, ei = came[cur]
                node_path.append(cur)
                edge_path.append(ei)
            node_path.reverse()
            edge_path.reverse()
            return node_path, edge_path
        if cur in closed:
            continue
        closed.add(cur)
        gc = g_score[cur]
        for other, ei, cost in adjc.get(cur, ()):
            if other in closed:
                continue
            ng = gc + cost
            if ng < g_score.get(other, inf):
                g_score[other] = ng
                came[other] = (cur, ei)
                h = hmemo.get(other)
                if h is None:
                    # geo.haversine_m(nodes[other], goal_ll) / vmax
                    phi1, cos1, lon1 = trig[other]
                    dphi = phi2 - phi1
                    dlam = radians(lon2 - lon1)
                    hh = (sin(dphi / 2.0) ** 2
                          + cos1 * cos2 * sin(dlam / 2.0) ** 2)
                    h = two_r * asin(min(1.0, sqrt(hh))) / vmax
                    hmemo[other] = h
                heappush(open_heap, (ng + h, other))
    return None


def _emit(poly: list, mask: list, pt: LatLon, is_spur: bool) -> None:
    """Append pt to the flat polyline unless it duplicates the last vertex
    (snap points and geometry often coincide with node coords; polylines must
    not stutter). When a NEW vertex is added, record the kind of the segment
    LEADING INTO it in `mask` (True=straight spur/chord, False=real road), so
    mask stays aligned to polyline EDGES: len(mask) == len(poly) - 1."""
    if poly and poly[-1] == pt:
        return
    poly.append(pt)
    if len(poly) > 1:
        mask.append(is_spur)


def _segments_from_mask(poly: list, mask: list) -> list:
    """Group the flat polyline into maximal same-kind RouteSegment runs using
    the per-edge `mask` (True=spur, False=road) — the on-network routed path
    only (the chord fallback builds its own segment). Consecutive segments share
    their boundary vertex, so flattening reproduces `poly` exactly."""
    if len(poly) < 2:
        # Degenerate zero-length route (a == b, both on one node): a single
        # 1-vertex road touch (no edge, hence no spur).
        return ([RouteSegment(points=tuple(poly), kind="road", spur=False)]
                if poly else [])
    segs = []
    i, n = 0, len(mask)
    while i < n:
        j = i
        while j < n and mask[j] == mask[i]:
            j += 1
        is_spur = mask[i]
        segs.append(RouteSegment(points=tuple(poly[i:j + 1]),
                                 kind="spur" if is_spur else "road",
                                 spur=is_spur))
        i = j
    return segs


def _beyond_cap_reason(g: RoadGraph, a: LatLon, b: LatLon,
                       snap_a, snap_b, cap: float) -> str:
    """One-line reason naming the endpoint distance(s) that exceeded the spur
    cap. The true nearest-node distance is recomputed UNBOUNDED for the message
    (nearest_node returned None precisely because it was beyond the cap)."""
    parts = []
    if snap_a is None:
        d = g.nearest_node(a, max_m=float("inf"))
        parts.append(f"origin {d[1]:.0f} m" if d else "origin (no node)")
    if snap_b is None:
        d = g.nearest_node(b, max_m=float("inf"))
        parts.append(f"dest {d[1]:.0f} m" if d else "dest (no node)")
    return (f"{' and '.join(parts)} off-network > "
            f"road_spur_max_m={cap:.0f} m")


def _astar_excluding(g: RoadGraph, start: int, goal: int, blocked) -> object:
    """A* identical to _astar but skipping BLOCKED intermediate nodes.

    start/goal are always allowed (a threatened destination's own snap node is
    legitimately hot — the corridor law, not the generator, owns the verdict).
    Returns (node_path, edge_path) or None."""
    if start == goal:
        return [start], []
    nodes = g.nodes
    goal_ll = nodes[goal]
    vmax = g._vmax_mps
    g_score = {start: 0.0}
    came: dict = {}
    open_heap = [(geo.haversine_m(nodes[start], goal_ll) / vmax, start)]
    closed = set()
    while open_heap:
        _f, cur = heapq.heappop(open_heap)
        if cur == goal:
            node_path = [cur]
            edge_path = []
            while cur != start:
                cur, ei = came[cur]
                node_path.append(cur)
                edge_path.append(ei)
            node_path.reverse()
            edge_path.reverse()
            return node_path, edge_path
        if cur in closed:
            continue
        closed.add(cur)
        gc = g_score[cur]
        for other, ei in g.neighbors(cur):
            if other in closed:
                continue
            if other != goal and other in blocked:
                continue
            e = g.edges[ei]
            ng = gc + e.length_m / e.speed_mps
            if ng < g_score.get(other, math.inf):
                g_score[other] = ng
                came[other] = (cur, ei)
                heapq.heappush(
                    open_heap,
                    (ng + geo.haversine_m(nodes[other], goal_ll) / vmax, other))
    return None


def _dijkstra_widest(g: RoadGraph, start: int, goal: int, clock) -> object:
    """WIDEST-CLOCK route: maximize the MINIMUM fire-arrival clock over the
    path's nodes (founder 2026-07-29: safety routes are chosen to be safe
    from fire, not for speed); ties prefer lower travel time, then node id.
    `clock` is {node_id: arrival_seconds_or_inf}. Returns (node_path,
    edge_path) or None. Deterministic bottleneck Dijkstra."""
    if start == goal:
        return [start], []
    best_b = {start: clock.get(start, math.inf)}
    best_t = {start: 0.0}
    came: dict = {}
    # max-heap on bottleneck -> negate; then time, then node id.
    open_heap = [(-best_b[start], 0.0, start)]
    done = set()
    while open_heap:
        nb, nt, cur = heapq.heappop(open_heap)
        nb = -nb
        if cur in done:
            continue
        done.add(cur)
        if cur == goal:
            node_path = [cur]
            edge_path = []
            while cur != start:
                cur, ei = came[cur]
                node_path.append(cur)
                edge_path.append(ei)
            node_path.reverse()
            edge_path.reverse()
            return node_path, edge_path
        for other, ei in g.neighbors(cur):
            if other in done:
                continue
            e = g.edges[ei]
            ob = min(nb, clock.get(other, math.inf))
            ot = nt + e.length_m / e.speed_mps
            cb, ct = best_b.get(other, -math.inf), best_t.get(other, math.inf)
            if ob > cb or (ob == cb and ot < ct):
                best_b[other], best_t[other] = ob, ot
                came[other] = (cur, ei)
                heapq.heappush(open_heap, (-ob, ot, other))
    return None


def route_candidates(g: Optional[RoadGraph], a: LatLon, b: LatLon,
                     cfg: ResponseConfig, node_clock_fn) -> list:
    """Up to cfg.route_alternatives distinct route candidates for the a -> b
    leg (contracts CORRIDOR HONESTY block): [fastest, avoid-hot fastest,
    widest-clock]. The corridor LAW is not consulted here — candidates are
    OPTIONS the unchanged law then judges; callers adopt per their leg rule
    (ingress: fastest passing; egress: safest passing).

    node_clock_fn(LatLon) -> earliest fire-arrival SECONDS at that point
    (math.inf when unreached) — normally a lookup into the same eroded P10
    grid the corridor law samples. Chord fallbacks return [fastest] only
    (no graph to search). Deduped by node path; order is [fastest, ...]."""
    fastest = route(g, a, b, cfg)
    k = int(getattr(cfg, "route_alternatives", 1) or 1)
    if k <= 1 or g is None or not fastest.on_roads or node_clock_fn is None:
        return [fastest]
    cap = cfg.road_spur_max_m
    snap_a = g.nearest_node(a, cap)
    snap_b = g.nearest_node(b, cap)
    if snap_a is None or snap_b is None:
        return [fastest]
    start, goal = snap_a[0], snap_b[0]
    clock: dict = {}
    for nid, p in g.nodes.items():
        try:
            clock[nid] = float(node_clock_fn(p))
        except Exception:
            clock[nid] = math.inf
    out = [fastest]
    seen = set()
    base = _astar(g, start, goal)
    if base is not None:
        seen.add(tuple(base[0]))
    # widest-clock candidate (the founder's safety route)
    widest = _dijkstra_widest(g, start, goal, clock)
    if widest is not None and tuple(widest[0]) not in seen:
        seen.add(tuple(widest[0]))
        out.append(_assemble(g, a, b, snap_a, snap_b, widest, cfg))
    # avoid-hot fastest: exclude nodes hotter than the fastest route's worst
    # point + 30 min — "at least half an hour more corridor clock than the
    # route we would have driven"
    if k >= 3 and base is not None:
        bottleneck = min((clock.get(n, math.inf) for n in base[0]),
                         default=math.inf)
        if math.isfinite(bottleneck):
            cutoff = bottleneck + 1800.0
            blocked = {nid for nid, t in clock.items() if t < cutoff}
            alt = _astar_excluding(g, start, goal, blocked)
            if alt is not None and tuple(alt[0]) not in seen:
                out.append(_assemble(g, a, b, snap_a, snap_b, alt, cfg))
    return out


def route(g: Optional[RoadGraph], a: LatLon, b: LatLon,
          cfg: ResponseConfig) -> RoadRoute:
    """Road route a -> b (contracts seam, FOUNDER ROUND 3 law 2 ROADS
    ADHERENCE). See the module docstring ROUTING SEMANTICS + SPUR METADATA for
    the full contract: a three-phase [spur -> A* road path -> spur] leg with the
    two end spurs the ONLY straight parts, per-segment spur metadata on the
    result, and cfg.fire_traffic_factor applied at route time. Whole-leg chord
    fallback (on_roads=False, single kind="chord" segment, loud note + warning)
    ONLY when there is no graph, an endpoint is farther than cfg.road_spur_max_m
    from every node, or the snapped nodes are disconnected."""
    cap = cfg.road_spur_max_m
    if g is None or not g.nodes:
        return _fallback(a, b, cfg, "no road graph loaded")
    # every input of the result: the exact endpoints, the spur cap, the fire
    # traffic factor (route time) and the tier-0 fallback's speed model
    mk = (a.lat, a.lon, b.lat, b.lon, cap, cfg.fire_traffic_factor,
          cfg.circuity, cfg.road_speed_mps)
    memo = g._route_memo
    hit = memo.get(mk)
    if hit is not None:
        return hit
    snap_a = g.nearest_node(a, cap)
    snap_b = g.nearest_node(b, cap)
    if snap_a is None or snap_b is None:
        rr = _fallback(a, b, cfg,
                       _beyond_cap_reason(g, a, b, snap_a, snap_b, cap))
    else:
        found = _astar(g, snap_a[0], snap_b[0])
        if found is None:
            rr = _fallback(a, b, cfg,
                           f"snapped nodes {snap_a[0]} and {snap_b[0]} on "
                           f"disconnected road components")
        else:
            rr = _assemble(g, a, b, snap_a, snap_b, found, cfg)
    if len(memo) >= _ROUTE_MEMO_MAX:
        memo.clear()
    memo[mk] = rr
    return rr


def route_on_path(g: RoadGraph, a: LatLon, node_path, edge_path,
                  cfg: ResponseConfig) -> RoadRoute:
    """RoadRoute for a node path found by an EXTERNAL search (the simulator's
    SEM-10d road-network escape, contracts escape_road_network): origin spur
    a -> node_path[0] (skipped when a sits on that node), then the road path
    along exactly `edge_path` (edge indices, one per hop, so parallel edges
    are unambiguous), ending EXACTLY on node_path[-1] — the destination is a
    road node, so there is no destination spur. Same geometry/time model as
    route() (_assemble): per-edge OSM geometry, cfg.fire_traffic_factor
    applied at route time, spur charged at SNAP_SPEED_MPS. The caller owns
    the path's validity (consecutive nodes joined by the named edges)."""
    node_path = list(node_path)
    edge_path = list(edge_path)
    if not node_path or len(edge_path) != len(node_path) - 1:
        raise ValueError("route_on_path: need len(edge_path) == len(node_path) - 1")
    for (u, v), ei in zip(zip(node_path, node_path[1:]), edge_path):
        e = g.edges[ei]
        if {e.u, e.v} != {u, v}:
            raise ValueError(f"route_on_path: edge {ei} does not join {u} and {v}")
    first = node_path[0]
    snap_a = (first, geo.haversine_m(a, g.nodes[first]))
    last = node_path[-1]
    return _assemble(g, a, g.nodes[last], snap_a, (last, 0.0),
                     (node_path, edge_path), cfg)


def _assemble(g: RoadGraph, a: LatLon, b: LatLon, snap_a, snap_b,
              found, cfg: ResponseConfig) -> RoadRoute:
    """[spur -> road path -> spur] RoadRoute assembly shared by route() and
    route_candidates() — one geometry/time model regardless of which search
    produced the node path."""
    node_path, edge_path = found
    factor = cfg.fire_traffic_factor
    poly: list = [a]
    mask: list = []          # per-edge: True=straight spur, False=real road
    # PHASE 1 — origin spur a -> nearest origin node (straight, <= cap; skipped
    # when a already coincides with its node).
    _emit(poly, mask, g.nodes[node_path[0]], True)
    dist_m = snap_a[1]
    time_s = snap_a[1] * factor / SNAP_SPEED_MPS
    # PHASE 2 — road path (real per-edge OSM geometry, spur=False).
    cur = node_path[0]
    for nxt, ei in zip(node_path[1:], edge_path):
        e = g.edges[ei]
        geom = e.geometry if e.u == cur else tuple(reversed(e.geometry))
        for pt in geom:
            _emit(poly, mask, pt, False)
        _emit(poly, mask, g.nodes[nxt], False)
        dist_m += e.length_m
        time_s += e.length_m * factor / e.speed_mps
        cur = nxt
    # PHASE 3 — dest spur nearest dest node -> b (straight, <= cap; skipped when
    # b already coincides with its node).
    _emit(poly, mask, b, True)
    dist_m += snap_b[1]
    time_s += snap_b[1] * factor / SNAP_SPEED_MPS
    return RoadRoute(polyline=poly, time_s=time_s, dist_m=dist_m,
                     on_roads=True, segments=_segments_from_mask(poly, mask),
                     note=None)
