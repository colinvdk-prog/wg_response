"""attack_r3_roads.py — FOUNDER ROUND 3 law 2 (ROADS ADHERENCE), the headline
defect. Two independent attacks:

A. BLACK-BOX SIM ADHERENCE (the founder's actual complaint "crews straight-line
   across the map"): drive 40 clients / 4 crews / HIGH fire on every real tile;
   for every crew sample that is en_route/egressing with route_on_roads=True,
   the crew's ACTUAL next position must lie within a few metres of the road
   polyline it drew last tick (it genuinely followed the drawn curve, did not
   cut the corner). Plus: route_segments reconstruct route_polyline exactly and
   every off-network spur edge is <= the spur cap. Reports the worst off-route
   distance per location and the pure-chord leg rate (must be ~0 at the sim cap).

B. WHITE-BOX route() SEAM on all 5 real graphs: segments flatten to polyline
   EXACTLY, spur edges <= cap, on_roads legs carry NO chord, and a chord is
   returned ONLY when an endpoint truly exceeds the cap or the nodes are
   disconnected.

Geometry is verified with an OWN ENU point-to-polyline distance (attack_r3_common),
never wgresponse.geo's own path math.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_r3_common import (                                  # noqa: E402
    REAL_TILES, make_session, arm_fire, tile_present, road_present, skip_loud,
    pt_polyline_dist_m, polyline_len_m, segments_reconstruct,
    spur_mask_consistent, enu,
)

# Reported per-location numbers (printed in the summary at the end).
OFFROUTE = {}          # key -> worst off-route metres (black-box adherence)
CHORDRATE = {}         # key -> % of sim legs that were pure chord
SEAM_CHORDRATE = {}    # key -> % of white-box sampled legs that were chord


def _edge_len_m(a, b):
    import math
    return math.hypot(*enu((a[0], a[1]), b[0], b[1]))


# ===========================================================================
# A. BLACK-BOX: crews genuinely ride the drawn road polyline
# ===========================================================================
# Deterministic fallback arming order. 40 clients / 4 crews / HIGH fire is the
# brief's headline scenario (first entry); the alternates only vary the seed
# (=> client placement) so a tile whose default placement lands its clients on
# developed/non-burnable cells (paradise's known limitation) still produces real
# on-road dispatch to test adherence against, rather than a vacuous pass.
_ARM_CONFIGS = (
    ("r3-roads", "high", False),
    ("r3-roads", "high", True),
    ("r3b", "high", False),
    ("r3c", "high", True),
    ("r3d", "extreme", False),
    ("r3e", "moderate", False),
)


def _run_adherence(key, seed, intensity, cluster, cap):
    """One full armed run; returns (worst_offroute, ctx, on_road_samples,
    leg_total, leg_chord, max_curvature, final_state) or None if it errored to
    arm."""
    from attack_sim_common import pick_ignition
    sess = make_session(key, 40, 4, seed=seed)
    assert sess._road_graph is not None, f"{key}: real road graph did not load"
    try:
        p, _t = pick_ignition(sess, key, cluster=cluster)
    except AssertionError:
        return None
    sess.ignite(p.lat, p.lon, intensity=intensity)
    sess.set_speed(0.2)                        # 180 s steps: catch crews mid-leg

    prev_route = {}       # crew_id -> (polyline, target_client) last tick
    prev_egress = {}
    worst = 0.0
    worst_ctx = ""
    on_road_samples = 0
    leg_total = 0
    leg_chord = 0
    max_curvature = 1.0                        # polyline_len / chord_len

    def check_state_leg(poly, on_roads, mask, segs):
        """STATE-payload structural invariants for one drawn leg."""
        nonlocal leg_total, leg_chord, max_curvature
        if not poly:
            return
        leg_total += 1
        # segments reconstruct the polyline EXACTLY
        assert segments_reconstruct(poly, segs or []), \
            f"{key}: route_segments do not reconstruct route_polyline"
        assert spur_mask_consistent(poly, mask, segs or []), \
            f"{key}: spur_mask inconsistent with segments"
        kinds = {s["kind"] for s in (segs or [])}
        if on_roads is True:
            # an on-road leg must never carry a chord segment...
            assert "chord" not in kinds, \
                f"{key}: route_on_roads=True but a chord segment is present"
            # ...and every off-network SPUR edge must be <= the cap
            for i, is_spur in enumerate(mask or []):
                if is_spur:
                    d = _edge_len_m(poly[i], poly[i + 1])
                    assert d <= cap + 1.0, \
                        f"{key}: spur edge {d:.0f} m exceeds cap {cap:.0f} m"
            clen = _edge_len_m(poly[0], poly[-1])
            if clen > 50.0:
                max_curvature = max(max_curvature, polyline_len_m(poly) / clen)
        else:
            leg_chord += 1

    for _ in range(110):
        st = sess.tick()
        for c in st["crews"]:
            cid = c["crew_id"]
            tgt = c["itinerary"][0]["client_id"] if c["itinerary"] else None
            pos = [c["lat"], c["lon"]]

            # ---- ingress adherence ----
            if c["status"] == "en_route" and c["route_polyline"]:
                check_state_leg(c["route_polyline"], c.get("route_on_roads"),
                                c.get("route_spur_mask"), c.get("route_segments"))
                # trivial-but-load-bearing: the drawn path starts at the crew
                assert c["route_polyline"][0] == pos, \
                    f"{key}: route_polyline[0] != crew position"
                if c.get("route_on_roads") is True:
                    on_road_samples += 1
                    if cid in prev_route and prev_route[cid][1] == tgt \
                            and tgt is not None:
                        d = pt_polyline_dist_m(pos, prev_route[cid][0])
                        if d > worst:
                            worst, worst_ctx = d, f"{cid} tgt={tgt}"
                    prev_route[cid] = (c["route_polyline"], tgt)
                else:
                    prev_route.pop(cid, None)
            else:
                prev_route.pop(cid, None)

            # ---- egress adherence (same law, escape leg) ----
            if c["status"] == "egressing" and c["egress_polyline"]:
                check_state_leg(c["egress_polyline"], c.get("egress_on_roads"),
                                c.get("egress_spur_mask"),
                                c.get("egress_segments"))
                if c.get("egress_on_roads") is True:
                    if cid in prev_egress:
                        d = pt_polyline_dist_m(pos, prev_egress[cid])
                        if d > worst:
                            worst, worst_ctx = d, f"{cid} egress"
                    prev_egress[cid] = c["egress_polyline"]
            else:
                prev_egress.pop(cid, None)

    return {"worst": worst, "ctx": worst_ctx, "on_road": on_road_samples,
            "leg_total": leg_total, "leg_chord": leg_chord,
            "max_curvature": max_curvature, "state": st}


@pytest.mark.parametrize("key", REAL_TILES)
def test_sim_crew_rides_road_polyline(key):
    if not tile_present(key):
        pytest.skip(skip_loud(key, "data/tiles/*.wgt"))
    if not road_present(key):
        pytest.skip(skip_loud(key, "data/roads/*.wgr"))
    # the sim default spur cap (SIM_ROAD_SPUR_MAX_M = 8000 m)
    cap = make_session(key, 2, 1, seed="probe")._cfg.road_spur_max_m

    res = None
    tried = []
    for seed, intensity, cluster in _ARM_CONFIGS:
        r = _run_adherence(key, seed, intensity, cluster, cap)
        tried.append(f"{seed}/{intensity}/cluster={cluster}")
        if r is not None and r["on_road"] > 0:
            res = r
            break
    if res is None:
        pytest.skip(
            f"SKIP-LOUDLY: {key} produced NO en_route-on-roads travel across "
            f"{len(tried)} armed scenarios ({', '.join(tried)}) — its clients "
            f"sit on developed/non-burnable cells so no crew is ever dispatched "
            f"(known surface-model limitation, NOT a roads defect; the route() "
            f"seam is still verified white-box on this tile's real graph)")

    OFFROUTE[key] = res["worst"]
    CHORDRATE[key] = (100.0 * res["leg_chord"] / res["leg_total"]
                      if res["leg_total"] else 0.0)
    st = res["state"]
    # THE HEADLINE ASSERTION: crews ride the drawn curve, not the chord.
    assert res["worst"] <= 3.0, \
        f"{key}: crew strayed {res['worst']:.1f} m off its drawn road " \
        f"polyline ({res['ctx']}) — straight-lining"
    # roads genuinely curve (so 'on the polyline' is a real constraint, not a
    # degenerate near-straight line a crew could cut and still pass)
    assert res["max_curvature"] > 1.02, \
        f"{key}: drawn polylines never curved (max ratio " \
        f"{res['max_curvature']:.3f}) — adherence check would be vacuous"
    # pure-chord legs must be ~0 at the sim cap
    assert CHORDRATE[key] <= 2.0, \
        f"{key}: {CHORDRATE[key]:.1f}% of sim legs were pure chord (>2%)"
    assert st["scoreboard"]["violations"] == 0, \
        f"{key}: {st['scoreboard']['violations']} safety violations"


# ===========================================================================
# B. WHITE-BOX: route() seam on the real graphs
# ===========================================================================
def _sample_points(sess):
    """Realistic sim leg endpoints: client locs + safe-zone locs."""
    pts = [c.loc for c in sess._clients]
    pts += [z.loc for z in sess._loc.safe_zones]
    return pts


@pytest.mark.parametrize("key", REAL_TILES)
def test_route_seam_structural(key):
    if not road_present(key):
        pytest.skip(skip_loud(key, "data/roads/*.wgr"))
    from wgresponse import roads
    g = roads.load_road_graph(key)
    assert g is not None, f"{key}: graph missing"
    sess = make_session(key, 40, 4, seed="r3-seam")
    cfg = sess._cfg
    cap = cfg.road_spur_max_m
    pts = _sample_points(sess)

    total = 0
    chord = 0
    for i in range(len(pts)):
        for j in range(len(pts)):
            if i == j:
                continue
            a, b = pts[i], pts[j]
            rr = roads.route(g, a, b, cfg)
            total += 1
            poly = [[p.lat, p.lon] for p in rr.polyline]
            # endpoints honored exactly
            assert poly[0] == [a.lat, a.lon] and poly[-1] == [b.lat, b.lon], \
                f"{key}: route endpoints not honored"
            # segments (as dicts) flatten to polyline EXACTLY
            segs = [{"points": [[p.lat, p.lon] for p in s.points],
                     "kind": s.kind} for s in rr.segments]
            assert segments_reconstruct(poly, segs), \
                f"{key}: RoadRoute.segments do not reconstruct polyline"
            # spur_mask (RoadRoute derived) aligns with segments
            assert spur_mask_consistent(
                poly, list(rr.spur_mask),
                [{"points": s["points"], "kind": s["kind"]} for s in segs]), \
                f"{key}: RoadRoute.spur_mask inconsistent"
            if rr.on_roads:
                assert all(s.kind != "chord" for s in rr.segments), \
                    f"{key}: on_roads route carries a chord segment"
                # every spur EDGE <= cap (the 'end spurs <= cap' contract)
                for e_i, is_spur in enumerate(rr.spur_mask):
                    if is_spur:
                        d = _edge_len_m(poly[e_i], poly[e_i + 1])
                        assert d <= cap + 1.0, \
                            f"{key}: spur edge {d:.0f} m > cap {cap:.0f} m"
            else:
                chord += 1
                # a chord is legal ONLY when an endpoint is beyond the cap OR
                # the snapped nodes are on disconnected components
                sa = g.nearest_node(a, cap)
                sb = g.nearest_node(b, cap)
                justified = (sa is None or sb is None)
                if not justified:
                    justified = roads._astar(g, sa[0], sb[0]) is None
                assert justified, \
                    f"{key}: chord fallback NOT justified (both ends snap " \
                    f"within cap and nodes are connected)"
                assert rr.note, f"{key}: chord fallback carried no loud note"
                kinds = [s.kind for s in rr.segments]
                assert kinds == ["chord"], \
                    f"{key}: fallback is not a single chord segment: {kinds}"

    SEAM_CHORDRATE[key] = (100.0 * chord / total) if total else 0.0
    # realistic client<->safezone legs are essentially always routable at 8 km
    assert SEAM_CHORDRATE[key] <= 2.0, \
        f"{key}: {SEAM_CHORDRATE[key]:.1f}% of realistic legs fell back to chord"


def test_chord_only_when_endpoint_beyond_cap():
    """Targeted: a point far off-network -> chord+note; two on-node points ->
    a real road route (never a chord)."""
    from wgresponse.contracts import LatLon
    from wgresponse import roads
    key = next((k for k in REAL_TILES if road_present(k)), None)
    if key is None:
        pytest.skip("no real road graph on disk")
    g = roads.load_road_graph(key)
    cfg = make_session(key, 2, 1, seed="r3-chord")._cfg
    cap = cfg.road_spur_max_m

    # two real network nodes -> must route on roads (no chord)
    node_ids = sorted(g.nodes)
    a = g.nodes[node_ids[0]]
    # pick a connected partner via A* neighbourhood
    partner = None
    for nid in node_ids[1:200]:
        if roads._astar(g, node_ids[0], nid) is not None:
            partner = g.nodes[nid]
            break
    assert partner is not None, "no connected node pair found"
    rr = roads.route(g, a, partner, cfg)
    assert rr.on_roads is True and all(s.kind != "chord" for s in rr.segments)

    # a point way beyond the cap from every node -> justified chord
    far = LatLon(a.lat + 5.0, a.lon + 5.0)      # ~500+ km away
    assert g.nearest_node(far, cap) is None
    rr2 = roads.route(g, a, far, cfg)
    assert rr2.on_roads is False
    assert [s.kind for s in rr2.segments] == ["chord"]
    assert rr2.note, "chord fallback must carry a loud note"


def test_zzz_roads_summary():
    """Print the per-location numbers the verifier brief asks for."""
    print("\n===== ROADS ADHERENCE per-location report =====")
    print(f"{'location':<15} {'worst_offroute_m':>18} "
          f"{'sim_chord_%':>12} {'seam_chord_%':>13}")
    for k in REAL_TILES:
        print(f"{k:<15} {OFFROUTE.get(k, float('nan')):>18.4f} "
              f"{CHORDRATE.get(k, float('nan')):>12.3f} "
              f"{SEAM_CHORDRATE.get(k, float('nan')):>13.3f}")
    print("================================================")
