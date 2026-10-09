"""tests/test_sim_left_map.py — A CREW THAT LEAVES THE MAP IS OUT (founder
ruling 2026-09-26, verbatim: "crews once leave a map are no longer part of the
simulation"; contracts SAFETY SEMANTICS 10 "A CREW THAT LEAVES THE MAP IS OUT",
knobs left_map_out + escape_prefer_on_map — default OFF, armed by the
simulator; docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave
the map are out (2026-09-26)").

THE MAP = the simulated terrain tile + simui.ZONE_ON_MAP_M around every catalog
safe zone (five standard tiles have a zone just past their edge). A crew
LEAVES when it is off the map with nothing that brings it back — stopped
there, or on a final leg that ends off it; transit through off-tile road
toward the map is not leaving; INBOUND reinforcements have not entered yet.
Once out: no dispatch, no movement, no sweep, no audit; commitments released;
reported in state / scoreboard / notes. The SEM-10d destination rule ranks an
on-map refuge before an equally safe off-map one.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from tests.test_sim_escape_roads import (
    KEY, LEAD, SPEED, _GraphRoads, _fire, _graph, _node_of, _pt)
from wgresponse import geo, simui
from wgresponse.contracts import CrewStatus, LatLon, ResponseConfig, SafeZone
from wgresponse.simui import (DEFAULT_TILE_DIR, LOCATIONS, ZONE_ON_MAP_M,
                              SimSession)
from wgresponse.terrain import SyntheticTerrain, save_ascii_tile

INF = math.inf
SIMULATOR_HTML = (Path(__file__).resolve().parent.parent / "console"
                  / "simulator.html")

#: zones that sit just past their STANDARD tile's edge (measured 2026-09-26;
#: docs/SIMULATOR_DESIGN.md "Old black is a refuge; crews that leave the map
#: are out") — the operator knew three; the data adds malibu z3 and paradise z2
OFF_TILE_ZONES = {"malibu_ca-z1", "malibu_ca-z3", "paradise_ca-z2",
                  "santa_rosa_ca-z3", "park_city_ut-z3"}


# ---------------------------------------------------------------------------
# a SMALL tile under the hand-built road network of test_sim_escape_roads:
# centred 1.5 km south of the network's node 0, half-width 3.5 km — so the
# north end (node 9, 3 km N) and the far ends (nodes 3, 6, 7) are OFF it
# ---------------------------------------------------------------------------

def _small_tile_dir(tmp_path):
    loc = LOCATIONS[KEY]
    c = geo.destination(loc.center, 180.0, 1500.0)
    bbox = geo.bbox_around([c], pad_m=3500.0)
    tile = SyntheticTerrain(seed="left-map", kind="grass").tile(bbox, 250.0)
    d = tmp_path / "tiles_small"
    d.mkdir(parents=True, exist_ok=True)
    save_ascii_tile(tile, str(d / f"{KEY}.wgt"))
    return d


def _net_session(tmp_path, **cfg):
    g = _graph()
    base = {"ensemble_n": 3, "grid_cell_m": 250.0, "horizon_h": 24.0,
            "transit_cold_black_by_class": False}
    s = SimSession(KEY, 2, 1, seed="left-map", tile_dir=_small_tile_dir(tmp_path),
                   roads_module=_GraphRoads(g), cfg_overrides=dict(base, **cfg))
    # catalog zones 3.5 km SW and SE of node 0 — on this tile, far from the
    # north end of the network (_fire paints them inside the band)
    zones = (SafeZone("lm-z1", _pt(225, 3.5), "south-west zone"),
             SafeZone("lm-z2", _pt(135, 3.5), "south-east zone"))
    s._loc = dataclasses.replace(s._loc, safe_zones=zones)
    s._zone_by_id = {z.zone_id: z for z in zones}
    return s, g


def _complete(f):
    """Give _fire()'s painted ensemble the p50/p90 a full tick reads (the
    homes stay unthreatened: p50 = p90 = the nominal) and pin it as the
    current field (no re-solve)."""
    ens = f.ens
    ens.p50 = ens.members[0]
    ens.p90 = ens.members[0]
    return f


def _hold_field(s):
    for f in s._fires.values():
        f.wx_epoch = s._wx_epoch
        f.anchor_t0_s = s._now_s


def _crew(s):
    cid = next(iter(s._crew_sims))
    return cid, s._crew_sims[cid]


def _strip(st):
    st = json.loads(json.dumps(st, sort_keys=True, allow_nan=False))
    for c in st.get("crews", []):
        for k in ("left_map", "left_map_at_s", "on_old_black"):
            c.pop(k, None)
    st.get("scoreboard", {}).pop("crews_left_map", None)
    st.pop("cmd_log", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# the knobs
# ---------------------------------------------------------------------------

def test_knobs_default_off_and_simulator_arms_them(tmp_path):
    assert ResponseConfig().left_map_out is False
    assert ResponseConfig().escape_prefer_on_map is False
    s, _g = _net_session(tmp_path)
    assert s._cfg.left_map_out is True and s._cfg.escape_prefer_on_map is True
    off, _g2 = _net_session(tmp_path, left_map_out=False,
                            escape_prefer_on_map=False)
    assert off._cfg.left_map_out is False
    assert off._cfg.escape_prefer_on_map is False
    assert ZONE_ON_MAP_M == 1500.0


# ---------------------------------------------------------------------------
# the map: zones past the tile edge are part of it
# ---------------------------------------------------------------------------

def _map_of(key, zone):
    """A stand-in carrying just what SimSession._on_map / _off_tile_m read
    (the loaded tile + the location) — no client placement, no roads."""
    import types
    tile, _syn, eff = simui.load_zone_tile(key, fire_zone=zone)
    m = types.SimpleNamespace(_tile=tile, _loc=LOCATIONS[key])
    m._tile_bbox = lambda: SimSession._tile_bbox(m)
    m.on_map = lambda p: SimSession._on_map(m, p)
    m.off_tile_m = lambda p: SimSession._off_tile_m(m, p)
    return m, eff


def test_zones_off_the_tile_are_on_the_map():
    """Every catalog zone of every preset is on the map on its standard and
    its extended tile; exactly five standard-tile zones sit past the edge
    (at most 1.45 km — inside ZONE_ON_MAP_M), none on an extended tile."""
    off = set()
    for key, loc in sorted(LOCATIONS.items()):
        for zone in ("standard", "extended"):
            if not simui.tile_file(key, fire_zone=zone).exists():
                continue
            m, eff = _map_of(key, zone)
            if eff != zone:
                continue
            for z in loc.safe_zones:
                assert m.on_map(z.loc), (key, zone, z.zone_id)
                if geo.latlon_to_rc(m._tile.spec, z.loc) is None:
                    assert zone == "standard", (key, z.zone_id)
                    assert 0.0 < m.off_tile_m(z.loc) < ZONE_ON_MAP_M
                    off.add(z.zone_id)
    if not all(simui.tile_file(k).exists() for k in
               ("malibu_ca", "paradise_ca", "santa_rosa_ca", "park_city_ut")):
        pytest.skip("LOUD SKIP: real standard tiles not cached")
    assert off == OFF_TILE_ZONES


def test_crew_staged_at_an_off_tile_zone_never_leaves():
    """malibu_ca: crew 1 stages at Zuma Beach, 1.24 km past the tile edge —
    reaching (or staging at) its own zone must never remove it."""
    if not simui.tile_file("malibu_ca").exists():
        pytest.skip("LOUD SKIP: malibu_ca tile not cached")
    s = SimSession("malibu_ca", 2, 3, seed="zuma", roads_module=None)
    zuma = LOCATIONS["malibu_ca"].safe_zones[0].loc
    cs = s._crew_sims["sim-g1"]
    assert cs.pos == zuma and geo.latlon_to_rc(s._tile.spec, zuma) is None
    assert not s._leaving_map(cs)
    s._check_left_map(s._now_s)
    assert s._left_map == {}


# ---------------------------------------------------------------------------
# transit vs leaving
# ---------------------------------------------------------------------------

def test_transit_through_off_tile_road_is_not_leaving():
    """Off the map (3 km past malibu's west edge, 1.8 km beyond Zuma) with a
    leg that returns — to Zuma, to a point on the tile, or to a home — is
    transit. Stopped there, or on a final leg that ends off the map: left."""
    if not simui.tile_file("malibu_ca").exists():
        pytest.skip("LOUD SKIP: malibu_ca tile not cached")
    s = SimSession("malibu_ca", 2, 1, seed="pch", roads_module=None)
    cs = s._crew_sims["sim-g1"]
    west = s._tile_bbox()[0]
    zuma = LOCATIONS["malibu_ca"].safe_zones[0].loc
    out = LatLon(zuma.lat, west - 3000.0 / geo.m_per_deg_lon(zuma.lat))
    assert not s._on_map(out) and geo.haversine_m(out, zuma) > ZONE_ON_MAP_M
    cs.pos = out
    for tgt in (zuma, s._loc.center):
        cs._mode, cs.egress_loc = "egress", tgt
        assert not s._leaving_map(cs), tgt
    home = s._clients[0].loc
    cs._mode, cs.egress_loc = "travel", zuma
    cs.itinerary = [{"client_id": s._clients[0].client_id,
                     "incident_id": "f", "loc": home}]
    assert not s._leaving_map(cs)
    cs.itinerary = []
    cs._mode, cs.egress_loc = "egress", LatLon(out.lat, out.lon - 0.02)
    assert s._leaving_map(cs)                    # final leg ends off the map
    cs._mode, cs.egress_loc = "idle", None
    assert s._leaving_map(cs)                    # stopped off the map
    cs.pos = zuma
    assert not s._leaving_map(cs)                # at its zone: on the map


def test_inbound_reinforcements_are_untouched():
    """A reinforcement parked at its tile-edge entry (INBOUND) has not
    entered yet — never 'left'; on arrival at its staging zone (malibu's
    fourth crew stages at Zuma, off the tile) it is on the map."""
    if not simui.tile_file("malibu_ca").exists():
        pytest.skip("LOUD SKIP: malibu_ca tile not cached")
    s = SimSession("malibu_ca", 2, 3, seed="inbound", n_crews_1h=1,
                   roads_module=None)
    cs = s._crew_sims["sim-g4"]
    assert cs.status == CrewStatus.INBOUND
    edge = cs.pos                                    # the east-edge entry
    assert not s._leaving_map(cs)
    # even parked well past the edge (the transit happens off the map) an
    # INBOUND crew has not entered: never 'left'
    cs.pos = LatLon(edge.lat, edge.lon + 0.03)
    assert not s._on_map(cs.pos) and not s._leaving_map(cs)
    s._check_left_map(s._now_s)
    assert s._left_map == {}
    # arrival: step 0 of a tick parks it at its staging zone (Zuma)
    s._crew_available_at["sim-g4"] = s._now_s
    cs.pos = cs.crew.home_base
    cs.status = CrewStatus.AVAILABLE
    assert cs.pos == LOCATIONS["malibu_ca"].safe_zones[0].loc
    assert not s._leaving_map(cs)


# ---------------------------------------------------------------------------
# the SEM-10d on-map tie-break
# ---------------------------------------------------------------------------

def test_on_map_tie_break_orders_equally_safe_destinations(tmp_path):
    s, _g = _net_session(tmp_path)
    k_on = s._esc_net_key(0.0, 900.0, INF, LEAD, 1, 5, off_map=False)
    k_off = s._esc_net_key(0.0, 400.0, INF, LEAD, 1, 9, off_map=True)
    assert k_on < k_off                  # equally safe: the map first
    short = s._esc_net_key(0.0, 900.0, 3 * 3600.0, LEAD, 1, 5, off_map=False)
    assert k_off < short                 # a strictly safer off-map node wins
    exposed_off = s._esc_net_key(400.0, 400.0, INF, LEAD, 1, 9, off_map=True)
    assert k_on < exposed_off            # exposure first, as ever
    zone = s._esc_net_key(0.0, 2000.0, INF, LEAD, 0, "z", off_map=False)
    assert zone < k_on                   # zone before node among on-map ties
    # knob off: the legacy key (the off-map flag is ignored)
    legacy, _g2 = _net_session(tmp_path, escape_prefer_on_map=False)
    a = legacy._esc_net_key(0.0, 400.0, INF, LEAD, 1, 9, off_map=True)
    b = legacy._esc_net_key(0.0, 900.0, INF, LEAD, 1, 5, off_map=False)
    assert a < b and len(a) == 6


def test_search_prefers_an_equally_safe_on_map_node(tmp_path):
    """No fire: every node is lawful with an unbounded horizon. The fastest
    node >= 2 km out is node 9 (3 km N, straight road) — past this tile's
    north edge. Legacy (tie-break off): node 9. Armed: an on-map node."""
    legacy, g = _net_session(tmp_path, escape_prefer_on_map=False)
    a = legacy._road_escape_search(g.nodes[0], legacy._now_s, SPEED, LEAD)
    assert _node_of(g, a["loc"]) == 9 and not legacy._on_map(a["loc"])
    armed, g2 = _net_session(tmp_path)
    b = armed._road_escape_search(g2.nodes[0], armed._now_s, SPEED, LEAD)
    assert armed._on_map(b["loc"]) and _node_of(g2, b["loc"]) in (2, 5)
    assert b["exposure_s"] == 0.0 and math.isinf(b["dm_s"])


def test_voluntary_decamp_prefers_any_durable_on_map_refuge(tmp_path):
    """The on-map nodes 3 km out are reached by the P10 front in 5 h — durable
    past the proactive bar — while node 9 (off the map) reads "never" only
    because no fire field exists past the edge. An EMERGENCY search takes the
    strictly longer horizon (node 9, the crew would leave); the VOLUNTARY
    lawful-only decamp takes the durable on-map refuge (the crew stays in
    play). Legacy (tie-break off): node 9 both ways."""
    def paint(s, g):
        now = s._now_s
        _complete(_fire(s, paint_p10=[(g.nodes[n], 300.0, now + 5 * 3600.0)
                                      for n in (1, 2, 4, 5, 8)]))
        return now

    for prefer, want_emergency, want_voluntary in ((True, {9}, {2, 5}),
                                                   (False, {9}, {9})):
        s, g = _net_session(tmp_path, escape_prefer_on_map=prefer)
        now = paint(s, g)
        bar = now + 2.75 * 3600.0
        em = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
        vol = s._road_escape_search(g.nodes[0], now, SPEED, LEAD,
                                    lawful_only=True, min_p10_s=bar)
        assert _node_of(g, em["loc"]) in want_emergency, prefer
        assert _node_of(g, vol["loc"]) in want_voluntary, prefer
        assert vol["exposure_s"] == 0.0 and vol["dm_s"] >= 0.0


# ---------------------------------------------------------------------------
# a crew that flees beyond the edge is out
# ---------------------------------------------------------------------------

def _flee_off_map(tmp_path, **cfg):
    """The crew at node 0 inside the band; every on-map node reached by the
    front in 3 h, node 9 (off the map) never: the strictly safer refuge is
    off the map, so even the tie-break sends the crew there. Returns the
    session after one full tick (sweep -> drive -> left)."""
    s, g = _net_session(tmp_path, **cfg)
    now = s._now_s
    short = now + 3 * 3600.0
    on_map = [n for n in g.nodes if s._on_map(g.nodes[n]) and n != 0]
    _complete(_fire(s, paint_p10=[(g.nodes[0], 300.0, now + 60.0)]
                    + [(g.nodes[n], 300.0, short) for n in on_map]))
    _hold_field(s)
    cid, cs = _crew(s)
    cs.pos = g.nodes[0]
    cs._mode = "idle"
    s._crew_safety_sweep(lead_s=LEAD)
    assert cs.egress_zone_id == "escape"
    assert not s._on_map(cs.egress_loc)
    return s, g, cid, cs


def test_a_crew_fleeing_beyond_the_edge_is_out(tmp_path):
    s, g, cid, cs = _flee_off_map(tmp_path)
    st = s.tick()                               # drives past the edge: out
    row = next(c for c in st["crews"] if c["crew_id"] == cid)
    assert row["left_map"] is True
    assert row["left_map_at_s"] == st["sim_now_s"]
    assert row["status"] == CrewStatus.UNKNOWN.value
    assert st["scoreboard"]["crews_left_map"] == 1
    assert any(n.startswith(f"CREW LEFT THE MAP: {cid}") for n in st["notes"])
    rec = s._left_map[cid]
    assert rec["edge_m"] > 0.0 and rec["zone_m"] > ZONE_ON_MAP_M
    pos = cs.pos
    assert not s._on_map(pos)
    # out of the simulation: no dispatch, no movement, no sweep, no audit
    assert cid not in s._eligible_replan_ids()
    assert cs.itinerary == [] and cs.egress_loc is None and cs._leg is None
    track_n = len(s._tracks[cid])
    s._crew_exposure = lambda p: (s._now_s - 60.0, s._now_s - 60.0)   # hot
    s._crew_safety_sweep(lead_s=LEAD)
    assert cs.egress_loc is None and cid not in s._relocating
    s._run_audit()
    assert [o for o in s._crew_overruns if o["crew_id"] == cid] == []
    del s._crew_exposure
    for _ in range(2):
        st = s.tick()
        assert cs.pos == pos and cs.status == CrewStatus.UNKNOWN
        assert len(s._tracks[cid]) == track_n            # no more beacons
        assert all(o["crew_id"] != cid for o in st["crew_overruns"])
        assert st["scoreboard"]["crews_left_map"] == 1
    # the note is said once
    assert sum(1 for n in st["notes"] if n.startswith("CREW LEFT THE MAP")) == 1


def test_leaving_releases_every_commitment(tmp_path):
    """Defensive path (homes are always on the tile, so a crew with a stop
    is in transit): a crew off the map whose every remaining destination is
    off it leaves with its stops released to the job pool, loudly."""
    s, g = _net_session(tmp_path)
    cid, cs = _crew(s)
    far = g.nodes[9]
    cs.pos = LatLon(far.lat + 0.01, far.lon)
    cs._mode = "travel"
    cs.itinerary = [{"client_id": "sim-c1", "incident_id": "F1", "loc": far}]
    cs.egress_loc = far
    cs.egress_zone_id = "escape"
    s._assigned.add("sim-c1")
    s._stop_rule["sim-c1"] = (30.0, 0.0)
    s._relocating[cid] = "zone_threatened"
    s._check_left_map(s._now_s)
    assert cid in s._left_map and s._left_map[cid]["dropped"] == ["sim-c1"]
    assert "sim-c1" not in s._assigned and "sim-c1" not in s._stop_rule
    assert cid not in s._relocating
    assert any("released 1 stop(s) (sim-c1)" in n for n in s._notes)


def test_knob_off_a_crew_off_the_map_stays_in_play(tmp_path):
    s, g, cid, cs = _flee_off_map(tmp_path, left_map_out=False,
                                  escape_prefer_on_map=False)
    st = s.tick()
    row = next(c for c in st["crews"] if c["crew_id"] == cid)
    assert row["left_map"] is False and st["scoreboard"]["crews_left_map"] == 0
    assert s._left_map == {} and not s._on_map(cs.pos)
    assert cs.status != CrewStatus.UNKNOWN


def test_leaving_is_deterministic(tmp_path):
    """Two identical sessions leave at the same tick, from the same spot,
    with identical states throughout."""
    runs = []
    for i in range(2):
        s, g, cid, cs = _flee_off_map(tmp_path / f"r{i}")
        runs.append([s.tick() for _ in range(3)])
    for a, b in zip(*runs):
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert runs[0][-1]["scoreboard"]["crews_left_map"] == 1


def test_no_crew_leaves_sessions_are_byte_identical():
    """A real session where no crew leaves: left_map_out on vs off reads the
    same state at every tick once the additive keys are stripped (the
    escape tie-break pinned off in both, so only the leave rule differs)."""
    if not simui.tile_file("boulder_co").exists():
        pytest.skip("LOUD SKIP: boulder_co tile not cached")
    loc = LOCATIONS["boulder_co"]
    hashes = []
    for lmo in (True, False):
        s = SimSession("boulder_co", 12, 3, seed="lmo-ident",
                       cfg_overrides={"left_map_out": lmo,
                                      "escape_prefer_on_map": False,
                                      "spotting_enabled": True,
                                      "grass_ros_floor_frac": 0.10})
        ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 7000.0)
        s.ignite(ign.lat, ign.lon, intensity="high")
        seq = []
        for _ in range(10):
            st = s.tick()
            assert st["scoreboard"]["crews_left_map"] == 0
            seq.append(_strip(st))
        hashes.append(seq)
    assert hashes[0] == hashes[1]


def _replay(cmd_log):
    """Rebuild a session from its cmd_log (configure / ignite / tick /
    set_speed) and return every tick's state."""
    c0 = cmd_log[0]
    assert c0["cmd"] == "configure"
    s = SimSession(c0["location"], c0["n_clients"], c0["n_crews"], c0["seed"],
                   cfg_overrides=c0["cfg_overrides"], fire_zone=c0["fire_zone"],
                   n_crews_1h=c0["n_crews_1h"], n_crews_3h=c0["n_crews_3h"])
    out = []
    for c in cmd_log[1:]:
        if c["cmd"] == "ignite":
            s.ignite(c["lat"], c["lon"], intensity=c["intensity"],
                     wind_dir_deg=c["wind_dir_deg"])
        elif c["cmd"] == "set_speed":
            s.set_speed(c["mult"])
        elif c["cmd"] == "tick":
            out.append(json.dumps(s.tick(), sort_keys=True))
        else:
            raise AssertionError(f"unexpected command {c}")
    return out


def test_replay_of_the_command_log_is_deterministic():
    """A real session replayed from its cmd_log (configure + ignite + ticks)
    reproduces every tick byte for byte, the rulings' fields included."""
    if not simui.tile_file("malibu_ca").exists():
        pytest.skip("LOUD SKIP: malibu_ca tile not cached")
    loc = LOCATIONS["malibu_ca"]
    s = SimSession("malibu_ca", 12, 3, seed="replay",
                   cfg_overrides={"spotting_enabled": True})
    for d_m in (7000.0, 6000.0, 5000.0, 4000.0):      # first burnable cell
        ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, d_m)
        try:
            s.ignite(ign.lat, ign.lon, intensity="extreme")
            break
        except ValueError:
            continue
    live = [json.dumps(s.tick(), sort_keys=True) for _ in range(8)]
    assert _replay(s.state()["cmd_log"]) == live


def test_real_session_where_crews_leave_replays_from_its_command_log():
    """beaver_creek_co, the roads-offroad probe's session (40 homes, 6 in-zone
    crews + 1 h + 3 h reinforcements, the page's defaults, HIGH 7 km up the
    hazard bearing): three crews flee by road to a node 147 m past the east
    edge and leave the map at ticks 8-9 (docs/SIMULATOR_DESIGN.md frames
    a02). Replaying its cmd_log reproduces every tick byte for byte."""
    key = "beaver_creek_co"
    if not simui.tile_file(key).exists() or \
            not (DEFAULT_TILE_DIR.parent / "roads" / f"{key}.wgr").exists():
        pytest.skip("LOUD SKIP: beaver_creek_co tile/road graph not cached")
    loc = LOCATIONS[key]
    s = SimSession(key, 40, 6, seed="roads-probe", n_crews_1h=1, n_crews_3h=1,
                   cfg_overrides={"spotting_enabled": True,
                                  "grass_ros_floor_frac": 0.10,
                                  # PHYSICS PIN (2026-10-09): this scenario was built under the simulator's
                                  # timber-floored ROS (before the grass-only default); pin that physics so
                                  # the situation under test still forms. The law under test is speed-blind.
                                  "ros_floor_grass_only": False,
                                  "dispatch_late_shift": True,
                                  "dispatch_plan_on_roads": True,
                                  "dispatch_hold_h": 24.0, "deploy_min": 15.0,
                                  "dispatch_sequence": "auto"})
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 7000.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    live = [json.dumps(s.tick(), sort_keys=True) for _ in range(10)]
    st = json.loads(live[-1])
    left = sorted(c["crew_id"] for c in st["crews"] if c["left_map"])
    assert left == ["sim-g3", "sim-g6", "sim-g7"], left
    assert st["scoreboard"]["crews_left_map"] == 3
    for cid in left:
        assert s._left_map[cid]["edge_m"] > 0.0
        assert s._left_map[cid]["zone_m"] > ZONE_ON_MAP_M
    assert _replay(s.state()["cmd_log"]) == live


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def _html():
    return SIMULATOR_HTML.read_text(encoding="utf-8")


def test_page_greys_out_a_crew_that_left_and_marks_its_last_position():
    html = _html()
    # the marker: a greyed class + a "left the map" chip at its last position
    assert 'el.classList.toggle("leftmap", !!c.left_map)' in html
    assert ".crew-icon.leftmap" in html and ".crew-icon.leftmap .cleft" in html
    assert '<span class="cleft">&#8677; left the map</span>' in html
    # the crew list greys the card, badges it and says it is out
    assert 'c.left_map ? " leftmap"' in html
    assert ".crew-block.leftmap" in html
    assert "out of the simulation since" in html
    # the tooltip
    assert "LEFT THE MAP at ${fmtClock(c.left_map_at_s)}" in html
    # the scoreboard row, sourced from scoreboard.crews_left_map
    assert 'id="sb_leftmap_row"' in html and 'id="sb_leftmap"' in html
    assert "sb.crews_left_map" in html
    assert '$("sb_leftmap_row").classList.toggle("hot", nLeft > 0)' in html
    # the alert feed pins a line per crew that left
    assert 'class="evt leftmap"' in html
    assert "cos + wds + lefts +" in html
    # the legend
    body = html[html.index('class="lg-body"'):]
    assert "crew left the map — out of the simulation" in body


def test_page_marks_a_crew_resting_on_old_black_as_a_refuge():
    html = _html()
    assert 'el.classList.toggle("oldblack", !!c.on_old_black' in html
    assert ".crew-icon.oldblack" in html
    assert 'class="chip oldblack"' in html
    assert "on old black — burned out, it cannot burn again" in html
    body = html[html.index('class="lg-body"'):]
    assert "crew on old black — burned out, a refuge (not a violation)" in body
    assert "burned — the simulated fire (old black: a crew refuge)" in body


def test_page_additions_add_no_external_host():
    html = _html()
    hosts = set(re.findall(r"https?://[A-Za-z0-9.-]+", html))
    assert hosts == {"https://unpkg.com", "https://tile.openstreetmap.org",
                     "http://www.w3.org"}
