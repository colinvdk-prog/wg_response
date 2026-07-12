"""wgresponse.roads — real-road graphs + routing for the interactive simulator.

Contract seam (contracts MODULE SEAMS, founder UX request 2026-07-10 — crews
must visibly travel on real roads):

    CLASS_SPEED_MPS              free-flow speed table by OSM highway class
    class RoadGraph              nodes {node_id: LatLon} + RoadEdge adjacency
    save_road_graph(g, path)     dep-free versioned cache write (atomic)
    load_road_graph(key) -> RoadGraph | None      (None when absent, never raises
                                                   for a missing cache file)
    route(g, a, b, cfg) -> RoadRoute(polyline, time_s, dist_m, on_roads)

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
    _SNAP_CELL_DEG degrees) is built lazily on first use."""

    __slots__ = ("nodes", "edges", "meta", "_adj", "_snap_grid", "_vmax_mps")

    def __init__(self, nodes: dict, edges: list, meta: Optional[dict] = None):
        self.nodes = dict(nodes)
        self.edges = list(edges)
        self.meta = dict(meta or {})
        self._adj: dict = {}
        self._snap_grid: Optional[dict] = None
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
        buckets; deterministic ties -> smaller node id."""
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


def _astar(g: RoadGraph, start: int, goal: int):
    """A* over free-flow edge seconds. Returns (node_path, edge_index_path)
    or None when start and goal are disconnected. Heuristic: haversine to
    goal / fastest speed present (admissible + consistent). Deterministic:
    heap ties break on node id; strict-improvement relaxation."""
    if start == goal:
        return [start], []
    nodes = g.nodes
    goal_ll = nodes[goal]
    vmax = g._vmax_mps
    h0 = geo.haversine_m(nodes[start], goal_ll) / vmax
    g_score = {start: 0.0}
    came: dict = {}
    open_heap = [(h0, start)]
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
            e = g.edges[ei]
            ng = gc + e.length_m / e.speed_mps
            if ng < g_score.get(other, math.inf):
                g_score[other] = ng
                came[other] = (cur, ei)
                heapq.heappush(
                    open_heap,
                    (ng + geo.haversine_m(nodes[other], goal_ll) / vmax, other))
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
    snap_a = g.nearest_node(a, cap)
    snap_b = g.nearest_node(b, cap)
    if snap_a is None or snap_b is None:
        return _fallback(a, b, cfg,
                         _beyond_cap_reason(g, a, b, snap_a, snap_b, cap))
    found = _astar(g, snap_a[0], snap_b[0])
    if found is None:
        return _fallback(a, b, cfg,
                         f"snapped nodes {snap_a[0]} and {snap_b[0]} on "
                         f"disconnected road components")
    node_path, edge_path = found
    factor = cfg.fire_traffic_factor
    poly: list = [a]
    mask: list = []          # per-edge: True=straight spur, False=real road
    # PHASE 1 — origin spur a -> nearest origin node (straight, <= cap; skipped
    # when a already coincides with its node).
    _emit(poly, mask, g.nodes[node_path[0]], True)
    dist_m = snap_a[1]
    time_s = snap_a[1] * factor / SNAP_SPEED_MPS
    # PHASE 2 — A* road path (real per-edge OSM geometry, spur=False).
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
