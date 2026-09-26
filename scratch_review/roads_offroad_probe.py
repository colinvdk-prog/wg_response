"""scratch_review/roads_offroad_probe.py — where do simulator crews leave the roads?

Founder observation (2026-09-25): "there are times that the response team
does not use roads". This probe runs HEADLESS SimSessions over every preset
(standard zone, plus extended for every key with a cached _ext tile) with the
public page's session defaults (server.SimSessionManager.new_session: ember
spotting + grass floor, plan-on-roads, 24 h coating hold, 15 min deploy),
HIGH intensity, 40 clients, 6 in-zone crews + one 1 h and one 3 h
reinforcement, 40 ticks, and attributes every straight line a viewer can see
to a named mechanism:

ENGINE (what the crew actually drives, from the roads seam):
  * legs by kind — every movement leg _SimCrew._begin_leg resolves, split into
    its RouteSegments: road (OSM geometry), spur (straight end connector),
    chord (whole-leg straight fallback, with its roads.route reason);
  * spur length distribution (median, p90, max; > 500 m, > 2 km);
  * egress legs whose endpoint lies outside the loaded road graph's bbox;
  * relocation legs (SEM-10 zone_threatened / zone_overrun / proactive) and
    the SEM-10d "escape" targets (straight ray points, not road nodes);
  * reinforcement entry: an INBOUND crew's arrival moves it from the tile-edge
    staging marker to its safe zone with NO leg (a jump).
RENDERING (what console/simulator.html draws from state):
  * marker tween — the page's startCrewTween/subPathAlong ported verbatim
    (projects the previous marker position onto the NEW tick's remaining
    route/egress polyline); `tween cut` = the farthest the tweened marker gets
    from the path the crew actually drove that tick;
  * breadcrumb trail — the page draws state.crews[i].track (one position per
    tick) as a polyline; `trail cut` = the farthest the straight track segment
    gets from the driven path;
  * queued-stop chord — a DEPLOYING crew with >= 2 itinerary stops has no
    route polyline, so the page draws [crew, stops...] as the red dashed
    "straight-line (no road route)" chord;
  * synthesized egress — an active crew with no egress polyline gets a
    straight [from, zone] chord.
When the state carries the post-fix fields (crews[i].moved_path /
trail), the page's NEW logic is emulated instead (tween along moved_path,
trail = the driven runs).

Instrumentation is READ-ONLY: thin wrappers around _SimCrew._begin_leg /
_refresh_egress_plan / _move_leg / advance and each crew's route_fn record
what the originals return and never change arguments or results — the
per-tick state sha256 recorded here is the byte-identity baseline.

Usage (repo root):
    py -3 scratch_review/roads_offroad_probe.py --tag before --jobs 3
    py -3 scratch_review/roads_offroad_probe.py --tag after --only vail_co
Writes scratch_review/roads_offroad_probe_<tag>.json and prints the tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from multiprocessing import Pool

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from wgresponse import geo, simui  # noqa: E402
from wgresponse.contracts import LatLon  # noqa: E402

#: the public page's /api/sim/new defaults (server.new_session mapping)
PAGE_CFG = {
    "spotting_enabled": True,
    "grass_ros_floor_frac": 0.10,
    "dispatch_late_shift": True,        # coat_early False (page default)
    "dispatch_plan_on_roads": True,
    "dispatch_hold_h": 24.0,
    "deploy_min": 15.0,
    "dispatch_sequence": "auto",
}
N_CLIENTS = 40
N_CREWS = 6
N_1H = 1
N_3H = 1
TICKS = 40
INTENSITY = "high"
STD_DIST_M = 7000.0      # the one-click scenarios' ~6.5-7 km upwind ignition
EXT_DIST_M = 18000.0     # extended: far ignition, outside the 18 km core
SEED = "roads-probe"


# ---------------------------------------------------------------------------
# geometry helpers (local equirectangular metres)
# ---------------------------------------------------------------------------

def _xy(p, lat0, lon0):
    k = math.cos(math.radians(lat0))
    return ((p[1] - lon0) * geo.M_PER_DEG_LAT * k, (p[0] - lat0) * geo.M_PER_DEG_LAT)


def _pt(p):
    return (p.lat, p.lon) if isinstance(p, LatLon) else (float(p[0]), float(p[1]))


def _seg_dist(P, A, B):
    vx, vy = B[0] - A[0], B[1] - A[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((P[0] - A[0]) * vx + (P[1] - A[1]) * vy) / L2))
    qx, qy = A[0] + t * vx, A[1] + t * vy
    return math.hypot(P[0] - qx, P[1] - qy)


def _poly_len_m(pts) -> float:
    return sum(geo.haversine_m(LatLon(*_pt(pts[i])), LatLon(*_pt(pts[i + 1])))
               for i in range(len(pts) - 1))


def max_cut_m(shown, driven, step_m: float = 25.0) -> float:
    """Farthest any point of the `shown` polyline (sampled every step_m) lies
    from the `driven` polyline — how far a drawn line cuts off the real path."""
    shown = [_pt(p) for p in shown]
    driven = [_pt(p) for p in driven]
    if len(shown) < 2 or len(driven) < 1:
        return 0.0
    lat0, lon0 = driven[0]
    S = [_xy(p, lat0, lon0) for p in shown]
    D = [_xy(p, lat0, lon0) for p in driven]
    if len(D) == 1:
        D = D * 2
    worst = 0.0
    for i in range(len(S) - 1):
        a, b = S[i], S[i + 1]
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) // step_m))
        for k in range(n + 1):
            f = k / n
            P = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            d = min(_seg_dist(P, D[j], D[j + 1]) for j in range(len(D) - 1))
            if d > worst:
                worst = d
    return worst


# ---------------------------------------------------------------------------
# console/simulator.html tween logic, ported verbatim (planar degrees frame)
# ---------------------------------------------------------------------------

def _toXY(p, lat0):
    k = math.cos(lat0 * math.pi / 180) or 1
    return (p[1] * k, p[0])


def _d2(a, b):
    dx, dy = a[0] - b[0], a[1] - b[1]
    return dx * dx + dy * dy


def _project(path, p):
    if not path or len(path) < 2:
        return None
    lat0 = p[0]
    P = _toXY(p, lat0)
    best = None
    for i in range(len(path) - 1):
        A, B = _toXY(path[i], lat0), _toXY(path[i + 1], lat0)
        vx, vy = B[0] - A[0], B[1] - A[1]
        L2 = vx * vx + vy * vy
        t = ((P[0] - A[0]) * vx + (P[1] - A[1]) * vy) / L2 if L2 > 0 else 0
        t = max(0, min(1, t))
        q = (A[0] + t * vx, A[1] + t * vy)
        dd = _d2(P, q)
        if best is None or dd < best["d2"]:
            best = {"seg": i, "t": t, "d2": dd,
                    "pos": (path[i][0] + t * (path[i + 1][0] - path[i][0]),
                            path[i][1] + t * (path[i + 1][1] - path[i][1]))}
    return best


def _sub_path_along(path, frm, to):
    a, b = _project(path, frm), _project(path, to)
    if not a or not b:
        return [frm, to]
    pts = [frm, a["pos"]]
    if a["seg"] < b["seg"] or (a["seg"] == b["seg"] and a["t"] <= b["t"]):
        for i in range(a["seg"] + 1, b["seg"] + 1):
            pts.append(path[i])
    else:
        for i in range(a["seg"], b["seg"], -1):
            pts.append(path[i])
    pts.append(b["pos"])
    pts.append(to)
    out = []
    for p in pts:
        if not out or _d2(_toXY(out[-1], p[0]), _toXY(p, p[0])) > 1e-14:
            out.append(p)
    return out if len(out) >= 2 else [frm, to]


def legacy_tween(frm, to, paths):
    """startCrewTween as shipped before 2026-09-25: None = no tween (snap or
    no move), else the point list the marker slides along."""
    moved = _d2(_toXY(frm, frm[0]), _toXY(to, frm[0]))
    if moved < 1e-14 or moved > 0.09:
        return None
    pts, best = None, math.inf
    for path in paths:
        if not path or len(path) < 2:
            continue
        a, b = _project(path, frm), _project(path, to)
        if not a or not b:
            continue
        err = a["d2"] + b["d2"]
        if err < best:
            best, pts = err, _sub_path_along(path, frm, to)
    return pts or [frm, to]


# ---------------------------------------------------------------------------
# read-only instrumentation
# ---------------------------------------------------------------------------

class _Rec:
    def __init__(self):
        self.session = None
        self.tick = 0
        self.legs = []
        self.plans = []
        self.last_rr = {}
        self.paths = {}
        self.start_pos = {}
        self.cid_of = {}


REC = _Rec()
_ORIG = {}


def _install():
    if _ORIG:
        return
    C = simui._SimCrew
    _ORIG["begin"] = C._begin_leg
    _ORIG["refresh"] = C._refresh_egress_plan
    _ORIG["move"] = C._move_leg
    _ORIG["advance"] = C.advance

    def begin(self, kind, target):
        origin = self.pos
        REC.last_rr.pop(id(self), None)
        out = _ORIG["begin"](self, kind, target)
        got = REC.last_rr.pop(id(self), None)
        _record_leg(self, kind, origin, target, got[2] if got else None)
        return out

    def refresh(self):
        REC.last_rr.pop(id(self), None)
        out = _ORIG["refresh"](self)
        got = REC.last_rr.pop(id(self), None)
        if got is not None and self._egress_plan is not None:
            _record_plan(self, got[0], got[1], got[2])
        return out

    def move(self, t, t_end_s):
        leg = self._leg
        before = list(leg["pts"]) if leg else []
        p0 = self.pos
        res = _ORIG["move"](self, t, t_end_s)
        after_n = len(leg["pts"]) if leg else 0
        consumed = before[:len(before) - after_n]
        path = REC.paths.setdefault(id(self), [p0])
        for q in [p0] + consumed + [self.pos]:
            if path[-1] != q:
                path.append(q)
        return res

    def advance(self, t0, t1, out):
        REC.paths[id(self)] = [self.pos]
        REC.start_pos[id(self)] = self.pos
        return _ORIG["advance"](self, t0, t1, out)

    C._begin_leg = begin
    C._refresh_egress_plan = refresh
    C._move_leg = move
    C.advance = advance


def _wrap_route_fn(cs):
    fn = cs._route_fn
    if fn is None:
        return

    def wrapped(a, b):
        rr = fn(a, b)
        REC.last_rr[id(cs)] = (a, b, rr)
        return rr
    cs._route_fn = wrapped


def _in_bbox(p, bb):
    return bb is not None and bb[0] <= p.lat <= bb[2] and bb[1] <= p.lon <= bb[3]


def _classify_note(note):
    if not note:
        return None
    n = note.lower()
    if "no road graph" in n:
        return "no_graph"
    if "disconnected" in n:
        return "disconnected"
    if "off-network" in n:
        return "endpoint_too_far"
    return "other"


def _segs_summary(rr):
    out = []
    if rr is None:
        return [{"kind": "chord", "len_m": None, "end": "whole"}]
    segs = list(getattr(rr, "segments", None) or [])
    for i, seg in enumerate(segs):
        pts = list(seg.points)
        L = _poly_len_m(pts) if len(pts) > 1 else 0.0
        end = "whole" if seg.kind == "chord" else (
            "origin" if i == 0 and seg.kind == "spur" else
            "dest" if seg.kind == "spur" else "")
        out.append({"kind": seg.kind, "len_m": round(L, 1), "end": end,
                    "npts": len(pts)})
    return out


def _point_type(p):
    """What sits at a leg endpoint: a client home, a catalog safe zone, the
    tile-edge staging marker, or somewhere along a road (mid-leg)."""
    s = REC.session
    for c in s._clients:
        if geo.haversine_m(p, c.loc) < 1.0:
            return "home"
    for z in s._loc.safe_zones:
        if geo.haversine_m(p, z.loc) < 1.0:
            return "zone:" + z.zone_id
    return "other"


def _leg_context(cs, kind):
    s = REC.session
    cid = cs.crew.crew_id
    reloc = s._relocating.get(cid)
    ctx = {"reloc": reloc}
    if kind == "egress":
        ctx["target_kind"] = ("escape" if cs.egress_zone_id == "escape"
                              else "zone")
        ctx["zone_id"] = cs.egress_zone_id
        ctx["prepos"] = bool(reloc is None and cs.egress_zone_id
                             and s._preposition_target.get(cid) == cs.egress_zone_id)
    else:
        ctx["target_kind"] = "client"
        ctx["prepos"] = False
    return ctx


def _record_leg(cs, kind, origin, target, rr):
    bb = REC.bbox
    rec = {"tick": REC.tick, "crew": cs.crew.crew_id, "kind": kind,
           "origin": [round(origin.lat, 6), round(origin.lon, 6)],
           "target": [round(target.lat, 6), round(target.lon, 6)],
           "on_roads": bool(getattr(rr, "on_roads", False)) if rr else False,
           "note": getattr(rr, "note", None) if rr else "no route_fn (roads off)",
           "reason": _classify_note(getattr(rr, "note", None)) if rr else "no_graph",
           "segs": _segs_summary(rr),
           "origin_in_graph_bbox": _in_bbox(origin, bb),
           "target_in_graph_bbox": _in_bbox(target, bb),
           "origin_type": _point_type(origin), "target_type": _point_type(target),
           "dist_m": round(float(getattr(rr, "dist_m", 0.0) or 0.0), 1) if rr else None}
    rec.update(_leg_context(cs, kind))
    g = REC.session._road_graph if REC.session is not None else None
    rec["target_on_node"] = bool(g is not None and hasattr(g, "nearest_node")
                                 and g.nearest_node(target, max_m=0.5) is not None)
    REC.legs.append(rec)


def _on_node(_run, leg) -> bool:
    """An escape leg ending ON a road-graph node (the road-network escape's
    destinations are nodes; the ray escape's are open-ground points)."""
    return bool(leg.get("target_on_node"))


def _record_plan(cs, a, b, rr):
    REC.plans.append({"tick": REC.tick, "crew": cs.crew.crew_id,
                      "on_roads": bool(getattr(rr, "on_roads", False)),
                      "reason": _classify_note(getattr(rr, "note", None)),
                      "segs": _segs_summary(rr),
                      "target_in_graph_bbox": _in_bbox(b, REC.bbox)})


# ---------------------------------------------------------------------------
# one session
# ---------------------------------------------------------------------------

def _ignite(s, loc, zone, intensity=INTENSITY, std_dist=STD_DIST_M,
            ext_dist=EXT_DIST_M):
    bearing = loc.hazard_wind_dir_deg
    dist = ext_dist if zone == "extended" else std_dist
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(loc.center, bearing + db, dist * shrink)
            try:
                fid = s.ignite(p.lat, p.lon, intensity=intensity,
                               wind_dir_deg=bearing)
                return {"fid": fid, "lat": round(p.lat, 6), "lon": round(p.lon, 6),
                        "bearing": bearing + db, "dist_m": dist * shrink}
            except ValueError:
                continue
    return None


def _state_hash(st, strip=(), top_strip=()):
    st = json.loads(json.dumps(st, sort_keys=True, allow_nan=False))
    for c in st.get("crews", []):
        for k in strip:
            c.pop(k, None)
    for k in top_strip:
        st.pop(k, None)
    return hashlib.sha256(json.dumps(st, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


#: ENGINE-identity hash (escape roads 2026-09-26): also strips the state's
#: cmd_log — the echo of the configure() call, whose cfg_overrides differ
#: when a knob is pinned explicitly (a knob-OFF arm vs a tree without the
#: knob), while every engine output is compared.
ENGINE_TOP_STRIP = ("cmd_log",)


#: crew-level state keys added by the 2026-09-25 roads-legibility fix; the
#: byte-identity hash strips them so before/after compare the engine output.
NEW_CREW_KEYS = ("map_view",)


def run_one(job):
    key, zone, ticks = job[:3]
    intensity, std_dist, ext_dist = (job[3:6] if len(job) >= 6
                                     else (INTENSITY, STD_DIST_M, EXT_DIST_M))
    extra_cfg = dict(job[6]) if len(job) >= 7 else {}
    _install()
    t_start = time.time()
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(key, N_CLIENTS, N_CREWS, seed=SEED,
                         cfg_overrides=dict(PAGE_CFG, **extra_cfg), fire_zone=zone,
                         n_crews_1h=N_1H, n_crews_3h=N_3H)
    REC.__init__()
    REC.session = s
    g = s._road_graph
    if g is not None and g.nodes:
        lats = [p.lat for p in g.nodes.values()]
        lons = [p.lon for p in g.nodes.values()]
        REC.bbox = (min(lats), min(lons), max(lats), max(lons))
    else:
        REC.bbox = None
    for cs in s._crew_sims.values():
        _wrap_route_fn(cs)
    homes = []
    for c in s._clients:
        d = g.nearest_node(c.loc, max_m=float("inf")) if g is not None else None
        homes.append(round(d[1], 1) if d else None)
    ign = _ignite(s, loc, zone, intensity, std_dist, ext_dist)
    st = s.state()
    hashes = [_state_hash(st, NEW_CREW_KEYS)]
    engine_hashes = [_state_hash(st, NEW_CREW_KEYS, ENGINE_TOP_STRIP)]
    prev = st
    moves = []
    jumps = []
    queue_chords = []
    egress_synth = []
    prev_drive = {}             # crew -> its previous tick's driven path
    state_kb = []
    # per-TICK safety record (escape roads 2026-09-26): the final scoreboard
    # only shows the last tick's 10c rows, so count every tick's
    ov_rows = 0
    ov_ticks = 0
    ov_kinds = {}
    dep_viol_max = 0
    for k in range(1, ticks + 1):
        REC.tick = k
        pre_pos = {cid: cs.pos for cid, cs in s._crew_sims.items()}
        st = s.tick()
        hashes.append(_state_hash(st, NEW_CREW_KEYS))
        engine_hashes.append(_state_hash(st, NEW_CREW_KEYS, ENGINE_TOP_STRIP))
        state_kb.append(round(len(json.dumps(st)) / 1024.0, 1))
        rows_k = st.get("crew_overruns") or []
        if rows_k:
            ov_ticks += 1
        ov_rows += len(rows_k)
        for o in rows_k:
            kk = f"{o['reason']}|{o['mode']}"
            ov_kinds[kk] = ov_kinds.get(kk, 0) + 1
        sb_k = st["scoreboard"]
        dep_viol_max = max(dep_viol_max, sb_k.get("violations", 0)
                           - sb_k.get("crew_overruns", 0))
        pc = {c["crew_id"]: c for c in prev["crews"]}
        for c in st["crews"]:
            cid = c["crew_id"]
            cs = s._crew_sims[cid]
            mv = c.get("map_view")            # post-fix page logic when present
            old = pc.get(cid)
            frm = (old["lat"], old["lon"]) if old else (c["lat"], c["lon"])
            to = (c["lat"], c["lon"])
            paths = [[tuple(p) for p in (c.get("route_polyline") or [])],
                     [tuple(p) for p in (c.get("egress_polyline") or [])]]
            start = REC.start_pos.get(id(cs))
            jumped_now = (start is not None
                          and geo.haversine_m(pre_pos[cid], start) > 1.0)
            if jumped_now:
                # what the page draws for the jump: the legacy tween slides the
                # marker from its old spot toward the new one (straight across
                # the map unless > 0.09 deg^2); map_view.jumped -> a snap
                if mv is not None:
                    slide = 0.0
                    ui = "snap (map_view.jumped)" if mv.get("jumped") else "NOT FLAGGED"
                else:
                    tw = legacy_tween(frm, to, paths)
                    slide = (geo.haversine_m(pre_pos[cid], start) if tw else 0.0)
                    ui = "straight slide" if tw else "snap (> 0.09 deg^2)"
                jumps.append({"tick": k, "crew": cid,
                              "from": [pre_pos[cid].lat, pre_pos[cid].lon],
                              "to": [start.lat, start.lon],
                              "dist_m": round(geo.haversine_m(pre_pos[cid], start), 1),
                              "region": c.get("region"),
                              "ui_slide_m": round(slide, 1), "ui": ui})
            path = REC.paths.get(id(cs)) or []
            drove = len(path) >= 2 and _poly_len_m(path) > 5.0
            if jumped_now:
                prev_drive.pop(cid, None)
            if drove:
                drv = [_pt(p) for p in path]
                chord_cut = max_cut_m([drv[0], drv[-1]], drv)
                if mv is not None:
                    mp = mv.get("moved_path") or []
                    tw = mp if len(mp) >= 2 else None
                    tween_mode = "moved_path"
                else:
                    tw = legacy_tween(frm, to, paths)
                    tween_mode = "legacy"
                tween_cut = max_cut_m(tw, drv) if tw else 0.0
                if mv is not None:
                    runs = mv.get("trail") or []
                    last = runs[-1] if runs else []
                    if len(last) >= 2:
                        # this tick's stretch of the trail: walk back from the
                        # run's end (= the crew now) by the driven length
                        want = _poly_len_m(drv)
                        tail = [last[-1]]
                        acc = 0.0
                        for i in range(len(last) - 1, 0, -1):
                            a_, b_ = last[i], last[i - 1]
                            seg = geo.haversine_m(LatLon(*a_), LatLon(*b_))
                            if acc + seg >= want:
                                f = (want - acc) / seg if seg > 0 else 0.0
                                tail.append([a_[0] + (b_[0] - a_[0]) * f,
                                             a_[1] + (b_[1] - a_[1]) * f])
                                break
                            acc += seg
                            tail.append(b_)
                        # the simplified trail is a little SHORTER than the
                        # drive, so the tail can reach back into the previous
                        # tick's road: judge it against the crew's previous
                        # drive + this one (the roads it actually drove)
                        prev_drv = prev_drive.get(cid) or []
                        ref = (prev_drv + drv[1:] if prev_drv and
                               _pt(prev_drv[-1]) == _pt(drv[0]) else drv)
                        trail_cut = max_cut_m(tail[::-1], ref)
                    else:
                        trail_cut = 0.0
                    trail_mode = "trail"
                else:
                    trail_cut = chord_cut
                    trail_mode = "track"
                mrec = {"tick": k, "crew": cid, "status": c["status"],
                        "driven_m": round(_poly_len_m(drv), 1),
                        "chord_m": round(geo.haversine_m(LatLon(*drv[0]), LatLon(*drv[-1])), 1),
                        "tween_cut_m": round(tween_cut, 1),
                        "trail_cut_m": round(trail_cut, 1),
                        "tween_mode": tween_mode, "trail_mode": trail_mode,
                        "relocating": c.get("relocating"),
                        "egress_zone_id": c.get("egress_zone_id"),
                        "from": [round(frm[0], 6), round(frm[1], 6)],
                        "to": [round(to[0], 6), round(to[1], 6)]}
                if tween_cut > 250.0 or trail_cut > 250.0 or chord_cut > 1000.0:
                    stride = max(1, len(drv) // 150)
                    keep = drv[::stride]
                    if keep[-1] != drv[-1]:
                        keep.append(drv[-1])
                    mrec["path"] = [[round(p[0], 6), round(p[1], 6)] for p in keep]
                    if tw:
                        mrec["tween_pts"] = [[round(p[0], 6), round(p[1], 6)] for p in tw]
                moves.append(mrec)
                prev_drive[cid] = drv
            stops = c.get("itinerary") or []
            if (c["status"] == "deploying" and len(stops) >= 2
                    and not (c.get("route_segments") or c.get("route_polyline"))):
                pts = [(c["lat"], c["lon"])] + [(x["lat"], x["lon"]) for x in stops]
                q = (mv or {}).get("queue")
                qkinds = [sg.get("kind") for sg in (q or {}).get("segments") or []]
                queue_chords.append({"tick": k, "crew": cid, "n_stops": len(stops),
                                     "chord_m": round(_poly_len_m(pts), 1),
                                     "drawn": ("road_queue" if q else "queue_chord"),
                                     "queue_kinds": sorted(set(qkinds))})
            active = (c["status"] in ("en_route", "deploying", "egressing")
                      or len(stops) > 0)
            if active and not c.get("egress_polyline") and not c.get("egress_segments"):
                egress_synth.append({"tick": k, "crew": cid, "status": c["status"],
                                     "mode": cs._mode, "n_stops": len(stops),
                                     "egress_zone_id": c.get("egress_zone_id"),
                                     "egress_loc_set": cs.egress_loc is not None,
                                     "plan_none": cs._egress_plan is None,
                                     "preview": bool((mv or {}).get("egress_preview"))})
        prev = st
    out = {
        "key": key, "zone_req": zone, "zone": st["fire_zone"], "ignition": ign,
        "roads_on": st["roads_on"], "graph_nodes": len(g.nodes) if g is not None else 0,
        "graph_bbox": REC.bbox, "graph_meta_key": (g.meta.get("key") if g is not None else None),
        "cap_m": s._cfg.road_spur_max_m,
        "homes_nearest_node_m": homes,
        "legs": REC.legs, "plans": REC.plans, "moves": moves, "jumps": jumps,
        "queue_chords": queue_chords, "egress_synth": egress_synth,
        "relocations": st["scoreboard"]["relocations"],
        "violations": st["scoreboard"]["violations"],
        "served": st["scoreboard"]["served"],
        "overrun_rows": ov_rows, "overrun_ticks": ov_ticks,
        "overrun_kinds": ov_kinds, "departure_viol_max": dep_viol_max,
        "notes": [n for n in st["notes"] if "road" in n.lower() or "ROAD" in n
                  or "FIRE ZONE" in n or "escape" in n.lower()],
        "hashes": hashes, "engine_hashes": engine_hashes,
        "state_kb_max": max(state_kb) if state_kb else 0,
        "state_kb_median": statistics.median(state_kb) if state_kb else 0,
        "wall_s": round(time.time() - t_start, 1),
    }
    return out


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def _q(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


def summarize(r):
    legs = r["legs"]
    kinds = {"road": 0.0, "spur": 0.0, "chord": 0.0}
    n_chord = n_spur_leg = n_road_only = 0
    reasons = {}
    spurs = []
    for L in legs:
        ks = [s["kind"] for s in L["segs"]]
        if "chord" in ks:
            n_chord += 1
            reasons[L["reason"] or "?"] = reasons.get(L["reason"] or "?", 0) + 1
        elif "spur" in ks:
            n_spur_leg += 1
        else:
            n_road_only += 1
        for s in L["segs"]:
            if s["len_m"] is not None:
                kinds[s["kind"]] += s["len_m"]
            if s["kind"] == "spur" and s["len_m"] is not None:
                spurs.append(s["len_m"])
    egress_out = sum(1 for L in legs if L["kind"] == "egress"
                     and not (L["target_in_graph_bbox"] and L["origin_in_graph_bbox"]))
    ingress_out = sum(1 for L in legs if L["kind"] == "ingress"
                      and not (L["target_in_graph_bbox"] and L["origin_in_graph_bbox"]))
    reloc = [L for L in legs if L["reloc"]]
    escape = [L for L in legs if L.get("target_kind") == "escape"]
    esc_straight = sum(s["len_m"] or 0 for L in escape for s in L["segs"] if s["kind"] != "road")
    prepos = [L for L in legs if L.get("prepos")]
    moves = r["moves"]
    homes = [h for h in r["homes_nearest_node_m"] if h is not None]
    return {
        "key": r["key"], "zone": r["zone"], "zone_req": r["zone_req"],
        "legs": len(legs), "road_only": n_road_only, "with_spur": n_spur_leg,
        "chord": n_chord, "chord_reasons": reasons,
        "km_road": round(kinds["road"] / 1000, 1), "km_spur": round(kinds["spur"] / 1000, 1),
        "km_chord": round(kinds["chord"] / 1000, 1),
        "spur_n": len(spurs), "spur_med": _q(spurs, 0.5), "spur_p90": _q(spurs, 0.9),
        "spur_max": max(spurs) if spurs else None,
        "spur_gt500": sum(1 for x in spurs if x > 500), "spur_gt2k": sum(1 for x in spurs if x > 2000),
        "egress_out_bbox": egress_out, "ingress_out_bbox": ingress_out,
        "reloc_legs": len(reloc), "escape_legs": len(escape),
        "escape_straight_km": round(esc_straight / 1000, 1),
        "prepos_legs": len(prepos),
        "jumps": len(r["jumps"]), "jump_max_km": round(max((j["dist_m"] for j in r["jumps"]), default=0) / 1000, 1),
        "moving_ticks": len(moves),
        "tween_cut_gt250": sum(1 for m in moves if m["tween_cut_m"] > 250),
        "tween_cut_gt1k": sum(1 for m in moves if m["tween_cut_m"] > 1000),
        "tween_cut_max_km": round(max((m["tween_cut_m"] for m in moves), default=0) / 1000, 2),
        "trail_cut_gt250": sum(1 for m in moves if m["trail_cut_m"] > 250),
        "trail_cut_max_km": round(max((m["trail_cut_m"] for m in moves), default=0) / 1000, 2),
        "queue_chords": sum(1 for q in r["queue_chords"] if q.get("drawn") != "road_queue"),
        "queue_chord_max_km": round(max((q["chord_m"] for q in r["queue_chords"]
                                         if q.get("drawn") != "road_queue"), default=0) / 1000, 1),
        "queue_on_roads": sum(1 for q in r["queue_chords"] if q.get("drawn") == "road_queue"),
        "egress_synth": sum(1 for e in r["egress_synth"] if not e.get("preview")),
        "egress_preview": sum(1 for e in r["egress_synth"] if e.get("preview")),
        "jump_slides": sum(1 for j in r["jumps"] if j.get("ui_slide_m", 0) > 0),
        "jump_slide_max_km": round(max((j.get("ui_slide_m", 0) for j in r["jumps"]), default=0) / 1000, 1),
        "homes_gt300": sum(1 for h in homes if h > 300), "homes_gt2k": sum(1 for h in homes if h > 2000),
        "home_max_km": round(max(homes, default=0) / 1000, 2),
        "relocations": r["relocations"], "violations": r["violations"], "served": r["served"],
        "overrun_rows": r.get("overrun_rows"), "overrun_ticks": r.get("overrun_ticks"),
        "overrun_kinds": r.get("overrun_kinds"),
        "departure_viol_max": r.get("departure_viol_max"),
        "escape_on_node": sum(1 for L in escape if _on_node(r, L)),
        "escape_chord_km": round(sum(s["len_m"] or 0 for L in escape for s in L["segs"]
                                     if s["kind"] == "chord") / 1000, 1),
        "cap_m": r["cap_m"], "graph": r["graph_meta_key"], "graph_nodes": r["graph_nodes"],
        "state_kb_max": r["state_kb_max"], "wall_s": r["wall_s"],
    }


def print_tables(summ):
    hdr1 = (f"{'preset':<22}{'zone':<5}{'legs':>5}{'road':>5}{'+spur':>6}{'chord':>6}"
            f"{'kmRd':>7}{'kmSp':>6}{'kmCh':>6}{'spMed':>6}{'spP90':>6}{'spMax':>7}"
            f"{'>500':>5}{'>2k':>4}{'egOut':>6}{'reloc':>6}{'esc':>4}{'escKm':>6}"
            f"{'jump':>5}")
    print("\nENGINE — movement legs (roads seam)")
    print(hdr1)
    for s in summ:
        z = "ext" if s["zone"] == "extended" else "std"
        f = lambda v: "-" if v is None else f"{v:.0f}"
        print(f"{s['key']:<22}{z:<5}{s['legs']:>5}{s['road_only']:>5}{s['with_spur']:>6}"
              f"{s['chord']:>6}{s['km_road']:>7}{s['km_spur']:>6}{s['km_chord']:>6}"
              f"{f(s['spur_med']):>6}{f(s['spur_p90']):>6}{f(s['spur_max']):>7}"
              f"{s['spur_gt500']:>5}{s['spur_gt2k']:>4}{s['egress_out_bbox']:>6}"
              f"{s['reloc_legs']:>6}{s['escape_legs']:>4}{s['escape_straight_km']:>6}"
              f"{s['jumps']:>5}")
    print("\nchord reasons: " + "; ".join(
        f"{s['key']}/{s['zone'][:3]} {s['chord_reasons']}" for s in summ if s["chord_reasons"]))
    hdr2 = (f"{'preset':<22}{'zone':<5}{'moves':>6}{'tw>250':>7}{'tw>1k':>6}{'twMax':>7}"
            f"{'tr>250':>7}{'trMax':>7}{'qChord':>7}{'qMax':>6}{'egSyn':>6}"
            f"{'h>300':>6}{'h>2k':>5}{'hMax':>6}{'viol':>5}{'srv':>4}{'kb':>6}{'wall':>6}")
    print("\nRENDERING — what the page draws + placement")
    print(hdr2)
    for s in summ:
        z = "ext" if s["zone"] == "extended" else "std"
        print(f"{s['key']:<22}{z:<5}{s['moving_ticks']:>6}{s['tween_cut_gt250']:>7}"
              f"{s['tween_cut_gt1k']:>6}{s['tween_cut_max_km']:>7}{s['trail_cut_gt250']:>7}"
              f"{s['trail_cut_max_km']:>7}{s['queue_chords']:>7}{s['queue_chord_max_km']:>6}"
              f"{s['egress_synth']:>6}{s['homes_gt300']:>6}{s['homes_gt2k']:>5}"
              f"{s['home_max_km']:>6}{s['violations']:>5}{s['served']:>4}"
              f"{s['state_kb_max']:>6}{s['wall_s']:>6}")
    print("\nESCAPES + SAFETY — every tick (10c rows = crew-overrun rows summed "
          "over ticks; depV = max departure-audit rows in any tick)")
    print(f"{'preset':<22}{'zone':<5}{'esc':>4}{'onNode':>7}{'escKm':>6}{'chKm':>6}"
          f"{'reloc':>6}{'10c':>5}{'ovT':>5}{'depV':>5}{'srv':>5}  kinds")
    for s in summ:
        z = "ext" if s["zone"] == "extended" else "std"
        print(f"{s['key']:<22}{z:<5}{s['escape_legs']:>4}{s.get('escape_on_node', 0):>7}"
              f"{s['escape_straight_km']:>6}{s.get('escape_chord_km', 0):>6}"
              f"{s['relocations']:>6}{s.get('overrun_rows', 0):>5}"
              f"{s.get('overrun_ticks', 0):>5}{s.get('departure_viol_max', 0):>5}"
              f"{s['served']:>5}  {s.get('overrun_kinds') or ''}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="before")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", action="append")
    ap.add_argument("--zones", default="standard,extended")
    ap.add_argument("--ticks", type=int, default=TICKS)
    ap.add_argument("--intensity", default=INTENSITY)
    ap.add_argument("--std-dist-m", type=float, default=STD_DIST_M)
    ap.add_argument("--ext-dist-m", type=float, default=EXT_DIST_M)
    ap.add_argument("--cfg", action="append", default=[], metavar="KEY=VALUE",
                    help="extra ResponseConfig override on top of the page "
                         "defaults (A/B of a default-OFF knob)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    zones = args.zones.split(",")
    extra = {}
    for kv in args.cfg:
        k_, v_ = kv.split("=", 1)
        extra[k_] = (v_ == "true" if v_ in ("true", "false")
                     else float(v_) if "." in v_ else int(v_))
    jobs = []
    for key in sorted(simui.LOCATIONS):
        if args.only and key not in args.only:
            continue
        if "standard" in zones:
            jobs.append((key, "standard", args.ticks, args.intensity,
                         args.std_dist_m, args.ext_dist_m, extra))
        if "extended" in zones and simui.tile_file(key, fire_zone="extended").exists():
            jobs.append((key, "extended", args.ticks, args.intensity,
                         args.std_dist_m, args.ext_dist_m, extra))
    t0 = time.time()
    if args.jobs > 1:
        with Pool(args.jobs) as pool:
            results = pool.map(run_one, jobs, chunksize=1)
    else:
        results = [run_one(j) for j in jobs]
    summ = [summarize(r) for r in results]
    path = os.path.join(_REPO, "scratch_review", f"roads_offroad_probe_{args.tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tag": args.tag, "params": {
            "clients": N_CLIENTS, "crews": N_CREWS, "crews_1h": N_1H, "crews_3h": N_3H,
            "ticks": args.ticks, "intensity": args.intensity, "seed": SEED,
            "std_dist_m": args.std_dist_m, "ext_dist_m": args.ext_dist_m,
            "page_cfg": PAGE_CFG, "extra_cfg": extra},
            "summary": summ, "runs": results}, f, indent=1)
    print_tables(summ)
    print(f"\nwrote {path}  ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
