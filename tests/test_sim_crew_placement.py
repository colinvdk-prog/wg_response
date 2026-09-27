"""tests/test_sim_crew_placement.py — CREW PLACEMENT (founder 2026-09-26:
"allow the crews to be placed on the map"; docs/SIMULATOR_DESIGN.md
"Crew placement"; simui module docstring CREW PLACEMENT).

  ENGINE   SimSession.place_crew(crew_id, lat, lon) parks an IN-ZONE crew at
           the road node nearest the point (position + beacon; home_base and
           the egress anchor stay its safe zone), SETUP ONLY (refused once a
           fire is lit); reinforcements, unknown crews, off-tile points and
           points > 2 km from every road are refused; no road graph -> the
           raw point, loudly. reset_crews() undoes it. Both are in cmd_log,
           survive reset(), and a cmd_log replay reproduces the timeline.
  SAFETY   a crew placed in the fire's path is relocated by the SEM-10 sweep
           with 0 departure violations; a crew the first tick's front reaches
           is REPORTED by the 10c audit (never silent).
  SERVER   POST /api/sim/<id>/crew {crew_id, lat, lon} | {action:
           "reset_crews"} -> 200 / 400 / 404 / 409; ctl reset + reroll keep
           placements.
  PAGE     console/simulator.html Place-crews mode (source checks, the
           tests/test_simulator_ui.py style): a click in placement mode never
           ignites; drag handles; reset button; the post-ignition lock.

Logic tests run on a controlled synthetic tile (tmp dir) with a small REAL
wgresponse.roads.RoadGraph (a '+' of two roads through the location
center), so nothing depends on the concurrently-edited data/ caches; one
integration test uses the committed eagle_vail_co tile + road graph and
skips loudly when they are absent.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import threading
from pathlib import Path

import pytest

from wgresponse import geo, roads, server
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon, ResponseConfig
from wgresponse.crews import CrewTracker
from wgresponse.simui import (
    CREW_PLACE_LOCKED_REASON,
    CREW_PLACE_MAX_ROAD_M,
    DEFAULT_TILE_DIR,
    LOCATIONS,
    CrewPlacementError,
    CrewPlacementLocked,
    SimSession,
)
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0}
KEY = "boulder_co"
CENTER = LOCATIONS[KEY].center
WIND = LOCATIONS[KEY].hazard_wind_dir_deg          # 270: FROM the west
UPWIND = WIND                                        # bearing toward the fire
ROAD_HALF_M = 9_500.0                                # '+' arms, inside the tile
ROAD_STEP_M = 500.0

SIMULATOR_HTML = (Path(__file__).resolve().parent.parent / "console"
                  / "simulator.html")


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _tile_dir(tmp_path, key=KEY, size_km=20.0, cell_m=500.0, hamlet_half=2):
    """Controlled uniform-grass tile with a porous (checkerboard) developed
    hamlet at the center — the tests/test_simui.py geometry."""
    loc = LOCATIONS[key]
    bbox = geo.bbox_around([loc.center], pad_m=size_km * 500.0)
    tile = SyntheticTerrain(seed=f"simui-test|{key}", kind="grass").tile(
        bbox, cell_m)
    spec = tile.spec
    r0 = spec.nrows // 2 - hamlet_half
    c0 = spec.ncols // 2 - hamlet_half
    for r in range(r0, r0 + 2 * hamlet_half):
        for c in range(c0, c0 + 2 * hamlet_half):
            if (r + c) % 2 == 0:
                tile.fuel.set(r, c, 91.0)
    d = tmp_path / f"tiles_{key}"
    d.mkdir(exist_ok=True)
    save_ascii_tile(tile, str(d / f"{key}.wgt"))
    return d


def _plus_graph(center=CENTER, half_m=ROAD_HALF_M, step_m=ROAD_STEP_M,
                extra=()):
    """A REAL roads.RoadGraph: an E-W and a N-S road crossing at `center`
    (shared node 0), one node every step_m out to half_m. `extra` = an
    additional polyline of LatLon points (its own little road)."""
    nodes = {0: center}
    edges = []
    nid = 1

    def arm(bearing):
        nonlocal nid
        prev = 0
        for i in range(1, int(half_m // step_m) + 1):
            nodes[nid] = geo.destination(center, bearing, i * step_m)
            edges.append(roads.RoadEdge(
                u=prev, v=nid, highway_class="primary",
                length_m=geo.haversine_m(nodes[prev], nodes[nid])))
            prev = nid
            nid += 1

    for b in (90.0, 270.0, 0.0, 180.0):
        arm(b)
    prev = None
    for p in extra:
        nodes[nid] = p
        if prev is not None:
            edges.append(roads.RoadEdge(
                u=prev, v=nid, highway_class="residential",
                length_m=geo.haversine_m(nodes[prev], nodes[nid])))
        prev = nid
        nid += 1
    return roads.RoadGraph(nodes=nodes, edges=edges)


class _RoadsModule:
    """Module-shaped roads seam (contracts wgresponse.roads) serving ONE real
    RoadGraph and the real routing functions."""

    def __init__(self, graph):
        self.graph = graph

    def load_road_graph(self, key):
        return self.graph

    def route(self, g, a, b, cfg):
        return roads.route(g, a, b, cfg)

    def route_candidates(self, *args, **kwargs):
        return roads.route_candidates(*args, **kwargs)


def _session(tmp_path, n_clients=8, n_crews=2, seed="cp1", graph="plus",
             cfg=None, **kw):
    d = _tile_dir(tmp_path)
    mod = None
    if graph == "plus":
        mod = _RoadsModule(_plus_graph())
    elif graph is not None:
        mod = _RoadsModule(graph)
    return SimSession(KEY, n_clients, n_crews, seed=seed,
                      cfg_overrides=dict(FAST, **(cfg or {})), tile_dir=d,
                      roads_module=mod, **kw)


def _crew(st, cid):
    return next(c for c in st["crews"] if c["crew_id"] == cid)


def _on_ew_road(dist_west_m, north_m=0.0):
    """A point dist_west_m WEST of center on the E-W road, offset north."""
    p = geo.destination(CENTER, 270.0, dist_west_m)
    return geo.destination(p, 0.0, north_m) if north_m else p


def _hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def _replay(cmd_log, **seams):
    """Rebuild a session from its cmd_log alone (the determinism
    acceptance: same seed + same command script => identical timeline).
    Returns (session, [state per tick command])."""
    head = cmd_log[0]
    assert head["cmd"] == "configure"
    s = SimSession(head["location"], head["n_clients"], head["n_crews"],
                   seed=head["seed"], cfg_overrides=head["cfg_overrides"],
                   fire_zone=head["fire_zone"], n_crews_1h=head["n_crews_1h"],
                   n_crews_3h=head["n_crews_3h"], **seams)
    states = []
    for c in cmd_log[1:]:
        cmd = c["cmd"]
        if cmd == "tick":
            states.append(s.tick())
        elif cmd == "place_crew":
            s.place_crew(c["crew_id"], c["lat"], c["lon"])
        elif cmd == "reset_crews":
            s.reset_crews()
        elif cmd == "ignite":
            s.ignite(c["lat"], c["lon"], intensity=c["intensity"],
                     wind_dir_deg=c["wind_dir_deg"])
        elif cmd == "reset":
            s.reset()
        elif cmd == "set_wind":
            s.set_wind(c["dir_deg"])
        elif cmd == "set_buffer":
            s.set_buffer(c["egress_buffer_min"])
        elif cmd == "set_slack":
            s.set_slack(c["slack_min"])
        elif cmd == "set_deploy_min":
            s.set_deploy_min(c["deploy_min"])
        elif cmd == "set_speed":
            s.set_speed(c["mult"])
        elif cmd == "pause":
            s.pause()
        elif cmd == "resume":
            s.resume()
        else:
            raise AssertionError(f"unreplayable command {cmd!r}")
    return s, states


# ---------------------------------------------------------------------------
# ENGINE: placement before ignition
# ---------------------------------------------------------------------------

def test_place_before_ignition_moves_position_and_beacon(tmp_path):
    s = _session(tmp_path)
    zone1 = LOCATIONS[KEY].safe_zones[0]
    zone2 = LOCATIONS[KEY].safe_zones[1]
    click = _on_ew_road(5_000.0, north_m=300.0)     # 300 m off the E-W road
    node = _on_ew_road(5_000.0)                     # the nearest road node
    rec = s.place_crew("sim-g1", click.lat, click.lon)
    assert rec["crew_id"] == "sim-g1"
    assert rec["snapped"] is True
    assert rec["snap_m"] == pytest.approx(300.0, abs=1.0)
    assert (rec["lat"], rec["lon"]) == (round(node.lat, 6), round(node.lon, 6))
    assert (rec["requested_lat"], rec["requested_lon"]) == (
        round(click.lat, 6), round(click.lon, 6))
    cs = s._crew_sims["sim-g1"]
    assert geo.haversine_m(cs.pos, node) < 0.01           # sim position
    beacon = s._tracker.crew("sim-g1").last_beacon        # + beacon
    assert beacon.loc == cs.pos and beacon.t_s == s._now_s
    assert s._tracker.history("sim-g1") == [beacon]
    # escape targets never move: home_base + egress anchor stay the zone
    assert cs.crew.home_base == zone1.loc
    st = s.state()
    g1, g2 = _crew(st, "sim-g1"), _crew(st, "sim-g2")
    assert (g1["lat"], g1["lon"]) == (rec["lat"], rec["lon"])
    assert g1["anchor_zone_id"] == zone1.zone_id
    assert g1["status"] == "available"
    assert (g2["lat"], g2["lon"]) == (round(zone2.loc.lat, 6),
                                      round(zone2.loc.lon, 6))   # untouched
    cp = st["crew_placement"]
    assert cp["setup"] is True and cp["locked_reason"] is None
    assert cp["max_road_m"] == CREW_PLACE_MAX_ROAD_M == 2000.0
    assert cp["snaps_to_roads"] is True
    assert cp["placeable"] == ["sim-g1", "sim-g2"]
    assert cp["placed"] == [rec]
    assert st["cmd_log"][-1] == {"cmd": "place_crew", "crew_id": "sim-g1",
                                 "lat": click.lat, "lon": click.lon}
    json.dumps(st, allow_nan=False)                        # JSON-safe
    # re-placing moves it again (the newest placement wins)
    rec2 = s.place_crew("sim-g1", *_on_ew_road(2_000.0))
    assert rec2["snap_m"] == pytest.approx(0.0, abs=0.5)
    assert s.state()["crew_placement"]["placed"] == [rec2]


def test_placement_snaps_to_the_nearest_road_node(tmp_path):
    """The start is EXACTLY the graph node nearest the click — checked
    against a brute-force argmin over every node (ties -> smaller id)."""
    g = _plus_graph()
    s = _session(tmp_path, graph=g)
    for bearing, dist in ((30.0, 1_500.0), (200.0, 3_100.0), (95.0, 7_777.0),
                          (315.0, 1_300.0)):
        click = geo.destination(CENTER, bearing, dist)
        best = min(g.nodes, key=lambda n: (geo.haversine_m(click, g.nodes[n]), n))
        rec = s.place_crew("sim-g2", click.lat, click.lon)
        assert rec["road_node"] == best
        assert rec["snap_m"] == pytest.approx(
            geo.haversine_m(click, g.nodes[best]), abs=0.1)
        assert rec["snap_m"] <= CREW_PLACE_MAX_ROAD_M
        assert s._crew_sims["sim-g2"].pos == g.nodes[best]


def test_click_more_than_2km_from_any_road_is_refused(tmp_path):
    s = _session(tmp_path)
    before = s.state()
    far = geo.destination(CENTER, 45.0, 8_000.0)            # ~5.7 km off-road
    with pytest.raises(CrewPlacementError) as ei:
        s.place_crew("sim-g1", far.lat, far.lon)
    msg = str(ei.value)
    assert "no road within 2 km" in msg and "km away" in msg
    assert not isinstance(ei.value, CrewPlacementLocked)
    # refused = nothing changed, nothing logged
    assert s.state() == before
    # the 2 km line itself: 1.9 km off the E-W road snaps, 2.2 km is refused
    ok = _on_ew_road(6_000.0, north_m=1_900.0)
    assert s.place_crew("sim-g1", ok.lat, ok.lon)["snap_m"] == pytest.approx(
        1_900.0, abs=2.0)
    bad = geo.destination(_on_ew_road(6_250.0), 0.0, 2_200.0)
    with pytest.raises(CrewPlacementError, match="no road within 2 km"):
        s.place_crew("sim-g2", bad.lat, bad.lon)


def test_point_outside_the_tile_is_refused(tmp_path):
    s = _session(tmp_path)
    for lat, lon in ((0.0, 0.0), (CENTER.lat, CENTER.lon + 0.2),
                     (CENTER.lat + 0.2, CENTER.lon)):
        with pytest.raises(CrewPlacementError, match="outside the boulder_co tile"):
            s.place_crew("sim-g1", lat, lon)
    # a click INSIDE the tile whose nearest road node lies OUTSIDE it is
    # refused too (a start must be on the tile)
    outside_road = [geo.destination(CENTER, 90.0, d)
                    for d in (10_600.0, 11_000.0, 11_400.0)]
    g = _plus_graph(half_m=8_000.0, extra=outside_road)
    s2 = _session(tmp_path, graph=g)
    edge_click = geo.destination(CENTER, 90.0, 9_900.0)
    assert geo.latlon_to_rc(s2._tile.spec, edge_click) is not None
    with pytest.raises(CrewPlacementError, match="lies outside"):
        s2.place_crew("sim-g1", edge_click.lat, edge_click.lon)


@pytest.mark.parametrize("crew_id,lat,lon,frag", [
    ("sim-g9", None, None, "unknown crew"),
    (None, None, None, "unknown crew"),
    (7, None, None, "unknown crew"),
    ("sim-g1", float("nan"), None, "finite"),
    ("sim-g1", None, float("inf"), "finite"),
    ("sim-g1", "north", None, "numbers"),
])
def test_bad_crew_or_coordinates_refused(tmp_path, crew_id, lat, lon, frag):
    s = _session(tmp_path)
    p = _on_ew_road(3_000.0)
    with pytest.raises(CrewPlacementError, match=frag):
        s.place_crew(crew_id, p.lat if lat is None else lat,
                     p.lon if lon is None else lon)


def test_inbound_reinforcements_are_not_placeable(tmp_path):
    s = _session(tmp_path, n_crews=2, n_crews_1h=1, n_crews_3h=1)
    cp = s.state()["crew_placement"]
    assert cp["placeable"] == ["sim-g1", "sim-g2"]          # in-zone only
    p = _on_ew_road(3_000.0)
    for cid in ("sim-g3", "sim-g4"):
        with pytest.raises(CrewPlacementError, match="reinforcement"):
            s.place_crew(cid, p.lat, p.lon)
        assert _crew(s.state(), cid)["inbound"] is True
    s.place_crew("sim-g2", p.lat, p.lon)                    # in-zone still ok


def test_placement_refused_after_first_ignition(tmp_path):
    s = _session(tmp_path)
    s.place_crew("sim-g1", *_on_ew_road(4_000.0))
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    before_log = list(s.state()["cmd_log"])
    with pytest.raises(CrewPlacementLocked) as ei:
        s.place_crew("sim-g2", *_on_ew_road(3_000.0))
    assert CREW_PLACE_LOCKED_REASON in str(ei.value)
    assert isinstance(ei.value, CrewPlacementError)         # a ValueError too
    with pytest.raises(CrewPlacementLocked):
        s.reset_crews()
    st = s.state()
    assert st["cmd_log"] == before_log                      # nothing applied
    cp = st["crew_placement"]
    assert cp["setup"] is False
    assert cp["locked_reason"] == CREW_PLACE_LOCKED_REASON
    assert [p["crew_id"] for p in cp["placed"]] == ["sim-g1"]   # still recorded
    s.tick()
    with pytest.raises(CrewPlacementLocked):
        s.place_crew("sim-g1", *_on_ew_road(4_000.0))
    # reset() re-opens setup (and replays the placement)
    s.reset()
    assert s.state()["crew_placement"]["setup"] is True
    s.place_crew("sim-g2", *_on_ew_road(3_000.0))


def test_no_road_graph_uses_the_raw_point_loudly(tmp_path):
    s = _session(tmp_path, graph=None)                      # roads offline
    far = geo.destination(CENTER, 45.0, 8_000.0)            # no road anywhere
    rec = s.place_crew("sim-g1", far.lat, far.lon)
    assert rec["snapped"] is False and rec["snap_m"] is None
    assert rec["road_node"] is None
    assert "no road graph" in rec["note"]
    assert s._crew_sims["sim-g1"].pos == far                # the raw point
    st = s.state()
    assert st["crew_placement"]["snaps_to_roads"] is False
    assert any("CREW PLACEMENT WITHOUT ROADS" in n and "sim-g1" in n
               for n in st["notes"])
    # the off-tile rule still holds without roads
    with pytest.raises(CrewPlacementError, match="outside"):
        s.place_crew("sim-g1", 0.0, 0.0)


def test_crew_tracker_restage_bypasses_the_newer_rule_only_for_setup():
    t = CrewTracker(ResponseConfig())
    a = geo.destination(CENTER, 0.0, 1_000.0)
    b = geo.destination(CENTER, 90.0, 1_000.0)
    assert t.ingest(CrewBeacon("c1", CENTER, 100.0))
    assert not t.ingest(CrewBeacon("c1", a, 100.0))         # not strictly newer
    t.restage(CrewBeacon("c1", a, 100.0, status=CrewStatus.AVAILABLE))
    assert t.crew("c1").last_beacon.loc == a
    assert [x.loc for x in t.history("c1")] == [a]          # trail restarted
    assert not t.ingest(CrewBeacon("c1", b, 50.0))          # telemetry rule kept
    assert t.ingest(CrewBeacon("c1", b, 101.0))
    t.restage(CrewBeacon("new", b, 0.0))                    # auto-registers
    assert t.crew("new").last_beacon.loc == b


# ---------------------------------------------------------------------------
# ENGINE: reset / reset_crews / replay determinism
# ---------------------------------------------------------------------------

def test_reset_replays_the_same_staging(tmp_path):
    s = _session(tmp_path, n_crews=3)
    r1 = s.place_crew("sim-g1", *_on_ew_road(4_000.0, north_m=250.0))
    r3 = s.place_crew("sim-g3", *geo.destination(CENTER, 0.0, 3_300.0))
    s.tick()
    s.tick()                                    # fire-less ticks: nobody moves
    s.reset()
    st = s.state()
    assert st["tick"] == 0 and st["cmd_log"][-1] == {"cmd": "reset"}
    assert st["crew_placement"]["placed"] == [r1, r3]
    for rec in (r1, r3):
        c = _crew(st, rec["crew_id"])
        assert (c["lat"], c["lon"]) == (rec["lat"], rec["lon"])
        assert s._tracker.crew(rec["crew_id"]).last_beacon.loc == \
            s._crew_sims[rec["crew_id"]].pos
    zone2 = LOCATIONS[KEY].safe_zones[1]
    assert s._crew_sims["sim-g2"].pos == zone2.loc          # never placed
    # the reset scenario equals a fresh session given the same placements
    # (cmd_log legitimately differs: it records the history)
    fresh = _session(tmp_path, n_crews=3)
    fresh.place_crew("sim-g1", *_on_ew_road(4_000.0, north_m=250.0))
    fresh.place_crew("sim-g3", *geo.destination(CENTER, 0.0, 3_300.0))
    strip = lambda d: {k: v for k, v in d.items() if k != "cmd_log"}
    assert _hash(strip(fresh.state())) == _hash(strip(st))
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    for sess in (s, fresh):
        sess.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(4):
        assert _hash(strip(s.tick())) == _hash(strip(fresh.tick()))


def test_reset_crews_restores_the_safe_zones(tmp_path):
    s = _session(tmp_path)
    zones = LOCATIONS[KEY].safe_zones
    s.place_crew("sim-g1", *_on_ew_road(4_000.0))
    s.place_crew("sim-g2", *_on_ew_road(1_000.0))
    assert s.reset_crews() == ["sim-g1", "sim-g2"]
    st = s.state()
    for cid, z in (("sim-g1", zones[0]), ("sim-g2", zones[1])):
        assert s._crew_sims[cid].pos == z.loc
        assert s._tracker.crew(cid).last_beacon.loc == z.loc
    assert st["crew_placement"]["placed"] == []
    assert st["cmd_log"][-1] == {"cmd": "reset_crews"}
    s.reset()                                   # nothing left to replay
    assert all(s._crew_sims[c].pos == z.loc
               for c, z in (("sim-g1", zones[0]), ("sim-g2", zones[1])))
    assert s.reset_crews() == []                # a no-op, still logged
    assert s.state()["cmd_log"][-1] == {"cmd": "reset_crews"}


def test_place_then_reset_crews_is_a_no_op_on_the_timeline(tmp_path):
    """No placement in force = the default session, tick for tick (every key
    but cmd_log, which records the commands)."""
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    a = _session(tmp_path)
    b = _session(tmp_path)
    b.place_crew("sim-g1", *_on_ew_road(4_000.0))
    b.place_crew("sim-g2", *_on_ew_road(1_500.0))
    b.reset_crews()
    strip = lambda d: {k: v for k, v in d.items() if k != "cmd_log"}
    assert _hash(strip(a.state())) == _hash(strip(b.state()))
    for sess in (a, b):
        sess.ignite(ign.lat, ign.lon, intensity="high")
    for _ in range(6):
        assert _hash(strip(a.tick())) == _hash(strip(b.tick()))


def test_cmd_log_replay_reproduces_the_identical_timeline(tmp_path):
    """Determinism acceptance: the recorded command script (with placements,
    a re-placement, a fire-less tick, a reset_crews and a reset) replays to
    byte-identical states, tick for tick."""
    s = _session(tmp_path, n_crews=3, seed="replay")
    s.place_crew("sim-g1", *_on_ew_road(5_000.0, north_m=400.0))
    s.place_crew("sim-g2", *geo.destination(CENTER, 180.0, 2_600.0))
    s.tick()                                    # before any fire
    s.place_crew("sim-g1", *_on_ew_road(3_500.0))   # moved again
    s.reset_crews()
    s.place_crew("sim-g3", *geo.destination(CENTER, 0.0, 4_100.0))
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    live = [s.tick() for _ in range(5)]
    s.reset()
    s.ignite(ign.lat, ign.lon, intensity="high")
    live += [s.tick() for _ in range(3)]
    log = s.state()["cmd_log"]
    assert [c["cmd"] for c in log].count("place_crew") == 4
    d = _tile_dir(tmp_path)
    _s2, replayed = _replay(log, tile_dir=d,
                            roads_module=_RoadsModule(_plus_graph()))
    # replay also re-runs the fire-less tick before the fire
    replayed = replayed[1:]
    assert len(replayed) == len(live) == 8
    for k, (x, y) in enumerate(zip(live, replayed)):
        assert _hash(x) == _hash(y), f"replay diverged at live tick {k + 1}"


# ---------------------------------------------------------------------------
# SAFETY: placement never becomes a silent way into the fire
# ---------------------------------------------------------------------------

def _exposed(s, cid):
    """(burned, imminent) of crew `cid`'s cell at the session clock — the
    exact 10c audit law, recomputed independently of the audit rows."""
    nom, p10 = s._crew_exposure(s._crew_sims[cid].pos)
    now = s._now_s
    burned = (not math.isinf(nom)) and nom <= now
    imminent = (not math.isinf(p10)) and p10 < now + s._cfg.egress_buffer_min * 60.0
    return burned, imminent


def test_crew_placed_in_the_fire_path_is_relocated_lawfully(tmp_path):
    """A crew placed ON THE ROAD 3 km downwind of where the fire is then lit
    (HIGH, wind from the west) is moved out by the SEM-10 sweep ahead of the
    front: a relocation, 0 departure violations, and it never stands inside
    the pessimistic front at any audit (0 crew overruns)."""
    s = _session(tmp_path, seed="safety")
    start = _on_ew_road(5_000.0)
    s.place_crew("sim-g1", start.lat, start.lon)
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    relocated = False
    for _ in range(12):
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"]), st["audit"]
        assert st["crew_overruns"] == [], st["crew_overruns"]
        assert st["scoreboard"]["violations"] == 0
        g1 = _crew(st, "sim-g1")
        relocated |= bool(g1["relocating"])
        assert _exposed(s, "sim-g1") == (False, False)
    assert relocated, "the placed crew was never moved out of the fire's path"
    assert st["scoreboard"]["relocations"] >= 1
    assert geo.haversine_m(s._crew_sims["sim-g1"].pos, start) > 2_000.0
    # the crew kept its safe-zone anchor through all of it
    assert st["crew_placement"]["setup"] is False
    assert _crew(st, "sim-g1")["anchor_zone_id"] == \
        LOCATIONS[KEY].safe_zones[0].zone_id


def test_crew_placed_where_the_fire_starts_is_reported_never_silent(tmp_path):
    """The worst case — a fire lit ON a placed crew. The first tick's front
    reaches it before any relocation can drive it out, so the 10c audit
    MUST report it (every tick the crew's own cell is burned / imminent has
    its crew_overrun row and a nonzero violation count) while the sweep
    moves it away; departure violations stay 0.

    Pinned with old_black_refuge OFF (the strict law): 15 min after the
    ignition the grass under the crew has burned out (window 5-10 min), and
    under the founder's 2026-09-26 ruling old black is a refuge — the armed
    truth is test_armed_fire_lit_on_a_placed_crew_reported_then_rests_on_
    the_black below."""
    s = _session(tmp_path, seed="worst", cfg={"old_black_refuge": False})
    at_fire = _on_ew_road(8_000.0)
    s.place_crew("sim-g1", at_fire.lat, at_fire.lon)
    s.ignite(at_fire.lat, at_fire.lon, intensity="high")
    reported = 0
    for k in range(6):
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"])
        exposed = any(_exposed(s, "sim-g1"))
        rows = [o for o in st["crew_overruns"] if o["crew_id"] == "sim-g1"]
        assert bool(rows) == exposed, (k, rows, exposed)       # never silent
        if rows:
            reported += 1
            assert st["scoreboard"]["violations"] >= 1
            assert _crew(st, "sim-g1")["relocating"] is True
    assert reported >= 1, "a crew under the ignition must be reported"
    assert st["crew_overruns"] == []
    assert geo.haversine_m(s._crew_sims["sim-g1"].pos, at_fire) > 1_000.0


def test_armed_fire_lit_on_a_placed_crew_reported_then_rests_on_the_black(
        tmp_path):
    """The same worst case with the simulator's law (old black is a refuge,
    founder ruling 2026-09-26). The audit judges old black over the whole
    step, so the overrun is REPORTED at the first tick (the front crossed the
    crew during it) even though the grass has burned out by the audit —
    never silent. From then on the crew stands on old black: no row, no
    relocation (the black behind the front is the refuge), no departure
    violation."""
    s = _session(tmp_path, seed="worst")
    at_fire = _on_ew_road(8_000.0)
    s.place_crew("sim-g1", at_fire.lat, at_fire.lon)
    s.ignite(at_fire.lat, at_fire.lon, intensity="high")
    placed = s._crew_sims["sim-g1"].pos
    st = s.tick()
    rows = [o for o in st["crew_overruns"] if o["crew_id"] == "sim-g1"]
    assert [o["reason"] for o in rows] == ["burned"]
    assert st["scoreboard"]["violations"] >= 1
    nom, _p10 = s._crew_exposure(placed)
    assert s._now_s - nom >= s._cold_black_pass_s(placed)   # burned out now
    assert _crew(st, "sim-g1")["on_old_black"] is True
    for _ in range(5):
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"])
        assert [o for o in st["crew_overruns"]
                if o["crew_id"] == "sim-g1"] == []
        assert _crew(st, "sim-g1")["relocating"] is False
    assert s._crew_sims["sim-g1"].pos == placed


# ---------------------------------------------------------------------------
# SERVER: POST /api/sim/<id>/crew
# ---------------------------------------------------------------------------

API_KEY = "crew-placement-key"


class _Http:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            hdrs = {"X-Internal-API-Key": API_KEY}
            payload = raw
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
            if payload is not None:
                hdrs["Content-Type"] = "application/json"
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def new(self, **kw):
        body = {"location": KEY, "clients": 6, "crews": 2, "seed": "h1",
                "cfg": dict(FAST)}
        body.update(kw)
        status, out = self.call("POST", "/api/sim/new", body)
        assert status == 200, out
        return out["session_id"], out["state"]

    def crew(self, sid, body=None, raw=None):
        return self.call("POST", f"/api/sim/{sid}/crew", body=body, raw=raw)


@pytest.fixture
def http_sim(monkeypatch, tmp_path):
    monkeypatch.setenv("WGRESP_API_KEY", API_KEY)
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    graph = _plus_graph()
    # the server resolves the REAL roads module; serve our '+' graph for the
    # test location (and nothing for any other key)
    monkeypatch.setattr(roads, "load_road_graph",
                        lambda key, road_dir=None: graph if key == KEY else None)
    srv = server.serve(0, sim_tile_dir=_tile_dir(tmp_path),
                       sim_synth_size_km=6.0)
    thread = threading.Thread(target=lambda: srv.serve_forever(0.05),
                              daemon=True)
    thread.start()
    yield _Http(srv.server_address[1])
    srv.app.sim_manager.stop_all()
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def test_http_place_happy_path(http_sim):
    sid, st0 = http_sim.new()
    assert st0["crew_placement"]["setup"] is True
    assert st0["crew_placement"]["placed"] == []
    click = _on_ew_road(4_000.0, north_m=350.0)
    status, out = http_sim.crew(sid, {"crew_id": "sim-g1", "lat": click.lat,
                                      "lon": click.lon})
    assert status == 200, out
    assert out["ok"] is True and out["crew_id"] == "sim-g1"
    p = out["placement"]
    node = _on_ew_road(4_000.0)
    assert p["snapped"] is True and p["snap_m"] == pytest.approx(350.0, abs=1.0)
    assert (p["lat"], p["lon"]) == (round(node.lat, 6), round(node.lon, 6))
    st = out["state"]
    g1 = _crew(st, "sim-g1")
    assert (g1["lat"], g1["lon"]) == (p["lat"], p["lon"])
    assert st["crew_placement"]["placed"] == [p]
    assert st["cmd_log"][-1]["cmd"] == "place_crew"
    # the live state poll agrees
    status, polled = http_sim.call("GET", f"/api/sim/{sid}/state")
    assert status == 200 and _crew(polled, "sim-g1")["lat"] == p["lat"]


@pytest.mark.parametrize("body,frag", [
    ({"lat": 39.95, "lon": -105.2}, "crew_id must be a non-empty string"),
    ({"crew_id": "", "lat": 39.95, "lon": -105.2}, "crew_id must be"),
    ({"crew_id": 3, "lat": 39.95, "lon": -105.2}, "crew_id must be"),
    ({"crew_id": "sim-g1", "lon": -105.2}, "lat and lon are required"),
    ({"crew_id": "sim-g1", "lat": "north", "lon": -105.2}, "lat and lon"),
    ({"crew_id": "sim-g1", "lat": 39.95, "lon": -105.2, "zone": "z1"},
     "unknown crew keys"),
    ({"crew_id": "sim-g9", "lat": 39.955, "lon": -105.2}, "unknown crew"),
    ({"crew_id": "sim-g1", "lat": 0.0, "lon": 0.0}, "outside the boulder_co tile"),
    ({"action": "teleport"}, "action must be"),
    ({"action": "reset_crews", "crew_id": "sim-g1"}, "takes no other keys"),
])
def test_http_place_400s(http_sim, body, frag):
    sid, _ = http_sim.new()
    status, out = http_sim.crew(sid, body)
    assert status == 400, out
    assert frag in out["error"]


def test_http_place_400_far_from_road_non_finite_and_bad_bodies(http_sim):
    sid, _ = http_sim.new()
    far = geo.destination(CENTER, 45.0, 8_000.0)
    status, out = http_sim.crew(sid, {"crew_id": "sim-g1", "lat": far.lat,
                                      "lon": far.lon})
    assert status == 400 and "no road within 2 km" in out["error"]
    # NaN / Infinity literals (Python json emits + parses them)
    status, out = http_sim.crew(sid, raw=b'{"crew_id": "sim-g1", "lat": NaN, "lon": -105.2}')
    assert status == 400 and "finite" in out["error"]
    status, out = http_sim.crew(sid, raw=b'{"crew_id": "sim-g1", "lat": 39.9, "lon": Infinity}')
    assert status == 400 and "finite" in out["error"]
    status, out = http_sim.crew(sid, body=[1, 2])
    assert status == 400 and "JSON object" in out["error"]
    status, out = http_sim.crew(sid, raw=b"{not json")
    assert status == 400


def test_http_inbound_crew_is_400(http_sim):
    sid, st = http_sim.new(crews_1h=1)
    assert st["crew_placement"]["placeable"] == ["sim-g1", "sim-g2"]
    p = _on_ew_road(3_000.0)
    status, out = http_sim.crew(sid, {"crew_id": "sim-g3", "lat": p.lat,
                                      "lon": p.lon})
    assert status == 400 and "reinforcement" in out["error"]


def test_http_409_once_a_fire_is_lit(http_sim):
    sid, _ = http_sim.new()
    p = _on_ew_road(3_000.0)
    status, _ = http_sim.crew(sid, {"crew_id": "sim-g1", "lat": p.lat,
                                    "lon": p.lon})
    assert status == 200
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    status, _ = http_sim.call("POST", f"/api/sim/{sid}/ignite",
                              {"lat": ign.lat, "lon": ign.lon,
                               "intensity": "high"})
    assert status == 200
    status, out = http_sim.crew(sid, {"crew_id": "sim-g2", "lat": p.lat,
                                      "lon": p.lon})
    assert status == 409 and CREW_PLACE_LOCKED_REASON in out["error"]
    status, out = http_sim.crew(sid, {"action": "reset_crews"})
    assert status == 409 and CREW_PLACE_LOCKED_REASON in out["error"]
    status, st = http_sim.call("GET", f"/api/sim/{sid}/state")
    assert st["crew_placement"]["setup"] is False
    assert st["crew_placement"]["locked_reason"] == CREW_PLACE_LOCKED_REASON


def test_http_reset_crews_happy_path(http_sim):
    sid, st0 = http_sim.new()
    z1 = _crew(st0, "sim-g1")
    p = _on_ew_road(2_500.0)
    http_sim.crew(sid, {"crew_id": "sim-g1", "lat": p.lat, "lon": p.lon})
    status, out = http_sim.crew(sid, {"action": "reset_crews"})
    assert status == 200, out
    assert out["ok"] is True and out["action"] == "reset_crews"
    assert out["reset"] == ["sim-g1"]
    g1 = _crew(out["state"], "sim-g1")
    assert (g1["lat"], g1["lon"]) == (z1["lat"], z1["lon"])
    assert out["state"]["crew_placement"]["placed"] == []
    assert out["state"]["cmd_log"][-1] == {"cmd": "reset_crews"}


def test_http_ctl_reset_and_reroll_keep_placements(http_sim):
    sid, _ = http_sim.new()
    p = _on_ew_road(4_000.0, north_m=200.0)
    status, out = http_sim.crew(sid, {"crew_id": "sim-g2", "lat": p.lat,
                                      "lon": p.lon})
    placed = out["placement"]
    status, out = http_sim.call("POST", f"/api/sim/{sid}/ctl", {"action": "reset"})
    assert status == 200
    g2 = _crew(out["state"], "sim-g2")
    assert (g2["lat"], g2["lon"]) == (placed["lat"], placed["lon"])
    assert out["state"]["crew_placement"]["placed"] == [placed]
    status, out = http_sim.call("POST", f"/api/sim/{sid}/ctl",
                                {"action": "reroll_clients"})
    assert status == 200
    assert out["seed"] == "h1#r1"
    assert out["crew_placements_kept"] == ["sim-g2"]
    assert "crew_placements_dropped" not in out
    st = out["state"]
    g2 = _crew(st, "sim-g2")
    assert (g2["lat"], g2["lon"]) == (placed["lat"], placed["lon"])
    assert st["crew_placement"]["placed"] == [placed]
    # the re-rolled session's own script carries the staging (replayable)
    assert [c["cmd"] for c in st["cmd_log"]] == ["configure", "place_crew"]
    assert st["cmd_log"][0]["seed"] == "h1#r1"
    # a brand-new session starts with no placements
    sid2, st2 = http_sim.new()
    assert sid2 != sid and st2["crew_placement"]["placed"] == []


def test_http_reroll_without_placements_is_unchanged(http_sim):
    sid, _ = http_sim.new()
    status, out = http_sim.call("POST", f"/api/sim/{sid}/ctl",
                                {"action": "reroll_clients"})
    assert status == 200
    assert "crew_placements_kept" not in out
    assert "crew_placements_dropped" not in out


def test_http_unknown_session_404_and_get_405(http_sim):
    status, out = http_sim.crew("sim-99", {"crew_id": "sim-g1", "lat": 39.9,
                                           "lon": -105.2})
    assert status == 404
    sid, _ = http_sim.new()
    status, out = http_sim.call("GET", f"/api/sim/{sid}/crew")
    assert status == 405


# ---------------------------------------------------------------------------
# REAL DATA: the committed Eagle -> Vail corridor tile + road graph
# ---------------------------------------------------------------------------

def test_real_eagle_vail_placement_snaps_to_the_osm_graph():
    tile = Path(DEFAULT_TILE_DIR) / "eagle_vail_co.wgt"
    graph_file = roads.road_file("eagle_vail_co")
    if not tile.exists() or not graph_file.exists():
        pytest.skip(f"LOUD SKIP: eagle_vail_co tile/graph missing ({tile}, "
                    f"{graph_file}) — refetch with tools/fetch_sim_tiles.py "
                    f"/ tools/fetch_sim_roads.py --only eagle_vail_co")
    s = SimSession("eagle_vail_co", 6, 3, seed="cp-real",
                   cfg_overrides={"ensemble_n": 3})
    g = s._road_graph
    assert g is not None and s.state()["crew_placement"]["snaps_to_roads"]
    # 400 m north of the Edwards interchange anchor (I-70 exit 163)
    edwards = LOCATIONS["eagle_vail_co"].safe_zones[2].loc
    click = geo.destination(edwards, 0.0, 400.0)
    rec = s.place_crew("sim-g2", click.lat, click.lon)
    node_id, d = g.nearest_node(click, max_m=CREW_PLACE_MAX_ROAD_M)
    assert rec["snapped"] is True and rec["road_node"] == node_id
    assert rec["snap_m"] == pytest.approx(d, abs=0.1) and d <= 400.0
    assert s._crew_sims["sim-g2"].pos == g.nodes[node_id]
    # the Holy Cross Wilderness, far from every mapped road: refused
    wild = None
    for lat in (39.43, 39.45, 39.47):
        for lon in (-106.52, -106.50, -106.48, -106.46):
            p = LatLon(lat, lon)
            if g.nearest_node(p, max_m=CREW_PLACE_MAX_ROAD_M) is None:
                wild = p
                break
        if wild is not None:
            break
    assert wild is not None, "expected roadless wilderness south of Vail"
    with pytest.raises(CrewPlacementError, match="no road within 2 km"):
        s.place_crew("sim-g1", wild.lat, wild.lon)


# ---------------------------------------------------------------------------
# PAGE: console/simulator.html wiring (source checks)
# ---------------------------------------------------------------------------

def _html():
    return SIMULATOR_HTML.read_text(encoding="utf-8")


def _fn(html, name):
    """Source of `function name(...) {...}` / `async function name` — up to
    the next top-level function declaration."""
    m = re.search(r"\n(?:async )?function " + re.escape(name) + r"\(", html)
    assert m, name
    rest = html[m.end():]
    nxt = re.search(r"\n(?:async )?function \w+\(", rest)
    return html[m.start(): m.end() + (nxt.start() if nxt else len(rest))]


def test_page_place_crews_controls_live_in_the_crews_panel():
    html = _html()
    block = html[html.index('id="crewblock"'):html.index('<div class="ctl">', html.index('id="crewblock"'))]
    for marker in ('id="placecrews"', 'id="resetcrews"', 'id="placehint"',
                   'id="placelist"'):
        assert marker in block, marker
    assert ">Place crews<" in block and ">Reset to safe zones<" in block
    assert 'id="placemaphint"' in html
    # both start disabled until the first state says setup is open
    for bid in ("placecrews", "resetcrews"):
        tag = re.search(r'<button[^>]*id="' + bid + r'"[^>]*>', html).group(0)
        assert "disabled" in tag


def test_page_map_click_in_placement_mode_never_ignites():
    html = _html()
    m = re.search(r'map\.on\("click", e => \{(.*?)\n\}\);', html, flags=re.S)
    assert m, "map click handler"
    body = m.group(1)
    # placement is checked FIRST and returns before the ignite branch
    i_place = body.index("if (placing) { placeClickAt(")
    i_ret = body.index("return; }", i_place)
    i_ign = body.index("igniteAt(")
    assert i_place < i_ret < i_ign
    # belt and braces: igniteAt itself refuses while placing
    assert ("if (!sid || !armed || placing || mutationBlocked()) return;"
            in _fn(html, "igniteAt"))
    # entering placement mode disarms a primed intensity ...
    sp = _fn(html, "setPlacing")
    assert "armed = null" in sp and 'classList.remove("crosshair")' in sp
    # ... and arming an intensity leaves placement mode
    assert "if (armed && placing) setPlacing(false);" in html
    # distinct cursor + hint from the ignite crosshair / red hint
    assert "#map.placing" in html and "cursor: cell" in html


def test_page_places_through_the_crew_endpoint_click_and_drag():
    html = _html()
    pc = _fn(html, "placeCrew")
    assert "`/api/sim/${sid}/crew`" in pc
    assert "{ crew_id: cid, lat, lon: lng }" in pc
    assert "snapBack()" in pc                    # refused drag snaps back
    assert "mutationBlocked()" in pc and "placementOpen(lastState)" in pc
    click = _fn(html, "placeClickAt")
    assert "placeableIds(lastState)" in click
    assert "placeCrew(ids[placeNext % ids.length], lat, lng)" in click
    # the cursor cycles 1, 2, ... after the NEXT crew is placed (click or drag)
    assert ("if (ids.length && ids[placeNext] === cid) "
            "placeNext = (placeNext + 1) % ids.length;") in pc
    # drag a crew marker: dragend -> placeCrew; handles guarded for the shim
    assert 'R.marker.on("dragend"' in html and "placeCrew(cid, ll.lat, ll.lng)" in html
    assert "if (R.marker.on) {" in html
    sync = _fn(html, "syncPlacementUI")
    assert "R.marker.dragging.enable()" in sync and "R.marker.dragging.disable()" in sync
    assert "if (R.marker && R.marker.dragging)" in sync
    assert '(c.region || "in_zone") === "in_zone" && !c.inbound' in sync
    # reset to safe zones
    assert '{ action: "reset_crews" }' in html
    # a re-stage with no tick PLACES the marker (never a slide/tween), and a
    # marker the user is holding is never yanked by a render
    branch = html[html.index(
        "} else if (R.shown && (R.shown[0] !== c.lat || R.shown[1] !== c.lon)) {"):]
    branch = branch[:branch.index("\n    }\n")]
    assert "delete anim[c.crew_id];" in branch
    assert "R.marker.setLatLng([c.lat, c.lon])" in branch
    assert "startCrewTween" not in branch
    assert "if (R.dragging) {" in html


def test_page_locks_after_ignition_with_the_engine_reason():
    html = _html()
    assert f'const PLACE_LOCKED = "{CREW_PLACE_LOCKED_REASON}";' in html
    sync = _fn(html, "syncPlacementUI")
    assert "pb.disabled = !open;" in sync
    assert "cp.locked_reason || PLACE_LOCKED" in sync
    assert '$("placewrap").title' in sync          # hover works when disabled
    assert "Crews are now dispatched by the engine — reset the scenario to re-stage." in sync
    po = _fn(html, "placementOpen")
    assert "cp.setup" in po and "scrubbing" in po
    # scrubbing the past leaves placement mode
    assert "if (scrubbing && placing) setPlacing(false);" in html


def test_page_says_placements_cleared_on_rebuild_and_kept_on_reset():
    html = _html()
    ns = _fn(html, "newSession")
    assert "placedCount(lastState)" in ns and "crew placements cleared" in ns
    assert "crew_placements_kept" in html
    assert "scenario reset — crews re-staged where you placed them" in html
    # the copy-repro line carries the placements
    assert "placed=${placedStr}" in html


def test_page_crew_and_fire_markers_are_drawn_where_they_are():
    """Marker position fix found while building placement: .crew-icon /
    .fire-origin carried position:relative, overriding Leaflet's absolute
    marker icon, so crew N drew 10*(N-1) px south of its true position (a
    crew placed on a road node appeared beside it). The override must keep
    both icons absolutely positioned."""
    html = _html()
    style = html[html.index("<style>"):html.index("</style>")]
    rule = re.search(r"\.leaflet-marker-icon\.crew-icon, "
                     r"\.leaflet-marker-icon\.fire-origin \{ position: absolute; \}",
                     style)
    assert rule, "absolute-position override for crew / fire-origin markers"


def test_page_placement_layers_are_offline_shim_primitives():
    html = _html()
    sync = _fn(html, "syncPlacementUI")
    for api in ("L.polyline(", "L.circleMarker(", "placeLayer.clearLayers()"):
        assert api in sync, api
    assert "const placeLayer = L.layerGroup().addTo(map);" in html
    hosts = set(re.findall(r"https?://[A-Za-z0-9.-]+", html))
    assert hosts == {"https://unpkg.com", "https://tile.openstreetmap.org",
                     "http://www.w3.org"}
