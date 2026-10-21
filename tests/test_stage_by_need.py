"""SAFETY SEMANTICS 30 — CREWS STAGE WHERE THE WORK IS (2026-10-21; the Eagle
River valley review's iteration 2; contracts reinforce_stage_by_need,
idle_restage; docs/SIMULATOR_DESIGN.md "A crew never waits in a pocket").

A reinforcement was placed, on arrival, at its round-robin zone — zone 3:
Minturn on vail_co, a single-access refuge 3 km up US-24 where the valley's
reinforcement idled 32-40 of 44 ticks in the northwest and south runs — with
no judged way in; and an idle crew the plan gave nothing stayed where it
stopped (421 idle crew-ticks parked away from any zone in the baseline).

With (a) reinforce_stage_by_need a reinforcement arrives at its region's ROAD
ENTRY (reg_1h from the east edge: I-70 in every valley preset) when that spot
is clear and a lawful leg leads from it to a safe zone, else it holds and
says so; it is dispatchable from there and, with no work, stages toward the
open work. With (b) idle_restage an idle crew drives on a lawful leg to the
durable safe zone nearest the homes still waiting for a crew, when that is
at least 2 km nearer them than where it stands.

These tests pin the knobs (default off, armed by the simulator), byte-
identity with them off on the Vail worlds where they bite (goldens computed
on commit 0109ad3), the entry, the hold, the staging and the restaging."""
from __future__ import annotations

import hashlib
import json

import pytest

from wgresponse import geo, server, simui
from wgresponse.contracts import LatLon, ResponseConfig

ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False}
STAGE_ONLY = dict(ITER2_OFF, reinforce_stage_by_need=True, idle_restage=True)
RESTAGE_ONLY = dict(ITER2_OFF, idle_restage=True)

#: sha256(state minus cmd_log)[:16] per tick, computed on 0109ad3 (before
#: SEMANTICS 28-33): the one-click Vail westerly scenario as served, 8 ticks
GOLDEN_VAIL_WESTERLY = [
    "caada984925c0fb5", "1324fa1254c2b619", "6d1bde484c3b82ba",
    "60957958cac297f9", "7081a582e30944cc", "005c66b9553e2753",
    "c672b9d206256d40", "a5e74f87d5b8bc20"]
#: ... and the valley harness's Vail northwest fire at extreme (the one-click
#: roster, the fire 5 km NW of the centre on its nearest grass, wind 300),
#: 24 ticks
GOLDEN_VAIL_NW_EXTREME = [
    "2336d331d35065f8", "4ba9cd75a8ca7502", "c83df3c7b362cf04",
    "6a2ab2ace39ce804", "5eda744afd45b8a5", "dcf19905ff9328d3",
    "7abac6d3404f639e", "5092e177ea660297", "fe223d977c298621",
    "9dbff65483567cf3", "21a41f7e898eb557", "3abb7c2e2195b562",
    "2c684abbb3dba125", "7b3072a67057a260", "86a40161b8ad199d",
    "cae71469cdd21303", "5b04948346933695", "d8d90cfa2dbf6399",
    "0d2569f5a8f8a0bd", "4898bcb3c892eb63", "d3da5eb5838f16d8",
    "af61ca8ab3a79aa6", "a58654b8a4755d3c", "257c89d84389750f"]
MINTURN = simui.LOCATIONS["vail_co"].safe_zones[2]


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _vail(cfg, northwest=False):
    """The one-click Vail westerly scenario as the page serves it; with
    northwest=True re-lit as the valley harness's northwest fire at extreme
    (5 km NW of the centre on its nearest grass, wind from 300)."""
    if not simui.tile_file("vail_co").exists():
        pytest.skip("LOUD SKIP: vail_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": "vail_westerly", "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    if northwest:
        s.reset()
        p = s.fine_fuel_start(300.0, 5000.0)
        s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=300.0)
    return s


def _run(s, ticks):
    hashes, states, notes = [], [], []
    seen = len(s._notes)
    for _ in range(ticks):
        st = s.tick()
        hashes.append(_hash(st))
        states.append(st)
        notes.append(list(s._notes[seen:]))
        seen = len(s._notes)
    return hashes, states, notes


def _arrived_at(s, cid="sim-g3"):
    """Where the reinforcement stood when it arrived: its first beacon after
    INBOUND (the tick's step 1 records it where step 0 placed it)."""
    for t in s._tracks[cid]:
        if t["status"] != "inbound":
            return LatLon(t["lat"], t["lon"])
    return None


def test_knobs_are_off_by_default_and_the_simulator_arms_them():
    cfg = ResponseConfig()
    assert cfg.reinforce_stage_by_need is False and cfg.idle_restage is False
    s = simui.SimSession("boulder_co", 2, 1, seed="st", roads_module=None)
    assert s._cfg.reinforce_stage_by_need is True
    assert s._cfg.idle_restage is True
    s = simui.SimSession("boulder_co", 2, 1, seed="st", roads_module=None,
                         cfg_overrides=ITER2_OFF)
    assert s._cfg.reinforce_stage_by_need is False
    assert s._cfg.idle_restage is False


# ---------------------------------------------------------------------------
# (a) the reinforcement's way in
# ---------------------------------------------------------------------------

def test_off_is_byte_identical_and_the_reinforcement_lands_at_minturn():
    s = _vail(ITER2_OFF)
    hashes, states, _notes = _run(s, len(GOLDEN_VAIL_WESTERLY))
    assert hashes == GOLDEN_VAIL_WESTERLY
    at = _arrived_at(s)
    assert geo.haversine_m(at, MINTURN.loc) < 1.0


def test_the_reinforcement_enters_at_the_east_interstate_entry():
    """Armed: the 1 h crew comes in from the EAST edge on I-70 — the on-map
    motorway node farthest east — not at Minturn; identical to the pre-rule
    session until it arrives (tick 5)."""
    s = _vail(STAGE_ONLY)
    entry = s._reinforce_entry("sim-g3")
    g = s._road_graph
    mw = {n for e in g.edges if e.highway_class == "motorway"
          for n in (e.u, e.v)}
    east = max((g.nodes[n].lon for n in mw if s._on_map(g.nodes[n])))
    assert entry.lon == east
    assert entry.lon > simui.LOCATIONS["vail_co"].safe_zones[0].loc.lon
    hashes, states, _notes = _run(s, len(GOLDEN_VAIL_WESTERLY))
    assert hashes[:4] == GOLDEN_VAIL_WESTERLY[:4]
    assert hashes[4] != GOLDEN_VAIL_WESTERLY[4]
    assert geo.haversine_m(_arrived_at(s), entry) < 1.0
    g3 = next(c for c in states[4]["crews"] if c["crew_id"] == "sim-g3")
    assert g3["status"] != "inbound"


def test_a_closed_entry_holds_the_reinforcement_and_says_so(monkeypatch):
    """With its entry closed (the band on it, or no lawful road on to a safe
    zone) the reinforcement stays INBOUND past its arrival time — loudly,
    once — and enters at the first step the entry opens."""
    s = _vail(STAGE_ONLY)
    closed = {"on": True}
    real = s._entry_closed

    def gate(*a, **kw):
        return "the fire's band is at the entry" if closed["on"] else real(
            *a, **kw)

    monkeypatch.setattr(s, "_entry_closed", gate)
    _h, states, notes = _run(s, 6)
    g3 = [next(c for c in st["crews"] if c["crew_id"] == "sim-g3")
          for st in states]
    assert [c["status"] for c in g3[3:6]] == ["inbound"] * 3
    held = [n for ns in notes for n in ns
            if "reinforcement sim-g3 held at the valley entry" in n]
    assert len(held) == 1 and "SEMANTICS 30a" in held[0]
    closed["on"] = False
    st = s.tick()
    g3 = next(c for c in st["crews"] if c["crew_id"] == "sim-g3")
    assert g3["status"] != "inbound"


# ---------------------------------------------------------------------------
# (a)+(b) staging and restaging on the northwest fire
# ---------------------------------------------------------------------------

def test_northwest_off_is_byte_identical():
    s = _vail(ITER2_OFF, northwest=True)
    hashes, _states, notes = _run(s, len(GOLDEN_VAIL_NW_EXTREME))
    assert hashes == GOLDEN_VAIL_NW_EXTREME
    assert not any("SEMANTICS 30" in n for ns in notes for n in ns)


def test_an_idle_reinforcement_restages_from_minturn_toward_the_work():
    """idle_restage alone: the reinforcement lands at Minturn (legacy) at
    tick 5, gets no work, and Dowd Junction (vail_co-z2) is 2.2 km nearer the
    homes still waiting for a crew on a lawful leg, durable 8 h: it restages
    there at once and stands there, idle and dispatchable, by the tick's end;
    at tick 16 sim-g2 does the same. The session is the pre-rule one until
    tick 5."""
    s = _vail(RESTAGE_ONLY, northwest=True)
    hashes, states, notes = _run(s, 16)
    assert hashes[:4] == GOLDEN_VAIL_NW_EXTREME[:4]
    t5 = [n for n in notes[4] if "(SEMANTICS 30)" in n]
    assert len(t5) == 1 and "crew sim-g3 restaging to vail_co-z2" in t5[0]
    g3 = next(c for c in states[4]["crews"] if c["crew_id"] == "sim-g3")
    z2 = simui.LOCATIONS["vail_co"].safe_zones[1].loc
    assert geo.haversine_m(LatLon(g3["lat"], g3["lon"]), z2) < 50.0
    assert g3["status"] == "available"
    assert any("crew sim-g2 restaging to vail_co-z2" in n for n in notes[15])


def test_a_reinforcement_staged_by_need_from_the_east_entry():
    """Both: the reinforcement enters at the east I-70 entry at tick 5 with
    no work, and stages to the nearest durable zone it reaches lawfully
    toward the open homes — East Vail (vail_co-z1)."""
    s = _vail(STAGE_ONLY, northwest=True)
    _hashes, states, notes = _run(s, 5)
    t5 = [n for n in notes[4] if "(SEMANTICS 30)" in n]
    assert len(t5) == 1 and "crew sim-g3 staging to vail_co-z1" in t5[0]
