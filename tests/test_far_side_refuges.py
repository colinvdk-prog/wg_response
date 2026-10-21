"""SAFETY SEMANTICS 34 — FAR-SIDE REFUGES, AND REINFORCEMENTS ENTER WHERE THE
WORK IS (2026-10-21; the Eagle River valley review's iteration 3; contracts
sim_far_side_zones, reinforce_entry_by_need; docs/SIMULATOR_DESIGN.md "Far-
side refuges, ember ETAs and afternoon starts").

The valley tiles' zones were I-70 interchanges on the fire runs' line:
Beaver Creek's three sit at or east of a west-northwest ignition and its
Edwards one stands on grass, the corridor tile's Edwards one on timber, and
the valley's north side had none. With (a) sim_far_side_zones each location's
far_zones — non-burnable ground chosen from the committed tile's fuel and road
graph, named for what it is — join its catalog after its own zones. With (b)
reinforce_entry_by_need (not armed: measured neutral) a reinforcement enters
by whichever road entry (east or west edge) is open and has the most waiting
homes on a lawful road, then the soonest.

These tests pin the knobs (default off, armed by the simulator), the catalog
on and off, each refuge's ground and access, byte-identity off on the worlds
where the laws bite (goldens computed on commit b9e3b68), the entry choice,
and what the refuges change on Beaver Creek (ratchets)."""
from __future__ import annotations

import hashlib
import json

import pytest

from wgresponse import fuels, geo, server, simui
from wgresponse.contracts import ResponseConfig

#: the iteration-3 knobs, all off: the sessions of commit b9e3b68
ITER3_OFF = {"sim_far_side_zones": False, "reinforce_entry_by_need": False,
             "ember_eta_landings": False}
FAR_ONLY = dict(ITER3_OFF, sim_far_side_zones=True)


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def test_knobs_are_off_by_default_and_the_simulator_arms_the_refuges():
    """34a is armed by the simulator; 34b is NOT (measured neutral on the
    valley harness: it acted in 5 of 33 runs for -1 home) — an explicit
    override arms it."""
    cfg = ResponseConfig()
    assert cfg.sim_far_side_zones is False
    assert cfg.reinforce_entry_by_need is False
    s = simui.SimSession("boulder_co", 2, 1, seed="fz", roads_module=None)
    assert s._cfg.sim_far_side_zones is True
    assert s._cfg.reinforce_entry_by_need is False
    s = simui.SimSession("boulder_co", 2, 1, seed="fz", roads_module=None,
                         cfg_overrides={"reinforce_entry_by_need": True})
    assert s._cfg.reinforce_entry_by_need is True
    s = simui.SimSession("boulder_co", 2, 1, seed="fz", roads_module=None,
                         cfg_overrides=ITER3_OFF)
    assert not (s._cfg.sim_far_side_zones or s._cfg.reinforce_entry_by_need)


@pytest.mark.parametrize("key,ids", [
    ("beaver_creek_co", ["beaver_creek_co-z4", "beaver_creek_co-z5"]),
    ("eagle_vail_co", ["eagle_vail_co-z7", "eagle_vail_co-z8"]),
    ("vail_co", [])])
def test_the_catalog_on_and_off(key, ids):
    loc = simui.LOCATIONS[key]
    assert [z.zone_id for z in loc.far_zones] == ids
    off = simui.SimSession(key, 4, 2, seed="fz", roads_module=None,
                           cfg_overrides=ITER3_OFF)
    on = simui.SimSession(key, 4, 2, seed="fz", roads_module=None)
    assert off._loc.safe_zones == loc.safe_zones
    assert on._loc.safe_zones == tuple(loc.safe_zones) + tuple(loc.far_zones)
    # the round-robin staging of the first zones is unchanged
    assert ({c: cs.crew.home_base for c, cs in off._crew_sims.items()}
            == {c: cs.crew.home_base for c, cs in on._crew_sims.items()})


@pytest.mark.parametrize("key", ["beaver_creek_co", "eagle_vail_co"])
def test_each_refuge_stands_on_non_burnable_ground_by_a_road(key):
    if not simui.tile_file(key).exists():
        pytest.skip(f"LOUD SKIP: {key} tile not cached")
    s = simui.SimSession(key, 2, 1, seed="fz")
    spec = s._tile.spec
    for z in s._loc.far_zones:
        r, c = geo.latlon_to_rc(spec, z.loc)
        code = int(round(s._tile.fuel.get(r, c)))
        assert not fuels.is_burnable(code), (z.zone_id, code)
        node = s._road_graph.nearest_node(z.loc, max_m=50.0)
        assert node is not None, z.zone_id
        # and on no interchange: the refuges are not the catalog's points
        assert all(geo.haversine_m(z.loc, o.loc) > 1000.0
                   for o in simui.LOCATIONS[key].safe_zones)


# ---------------------------------------------------------------------------
# byte-identity off — goldens computed on commit b9e3b68 (the valley
# harness's runs, out/eagle_valley/probes/iter3/golden_calc.py)
# ---------------------------------------------------------------------------

GOLDENS = {
    "beaver_creek_co|canonical|high": [
        "a7c5200bb4d1579d", "ff9205c94d73c28f", "33ed99017fee54e1",
        "e1f4e3c8c8864aaf", "ee6267fb8bdb3433", "b5581a4470a1df9a",
        "be2e9eef097d2473", "736e12eda9387b54", "109e5d384e3bcedc",
        "8745a460b3c35a50", "6a06300dfe053d0e", "b8ba419f763c0ef2"],
    "beaver_creek_co|northwest|high": [
        "4d8a86e0fd3d12f2", "0d1896fbed70c78c", "3bb172a8ad5751fa",
        "340293ec022f17ab", "dc493c0e3d8de102", "4868d95c5bd2d2cc",
        "850b3cc63ad75830", "24d76cd69d3cc392"],
    "eagle_vail_co|canonical|high": [
        "7e756c578ad8272d", "c044185fb39fa23d", "d25e81c5912fedbe",
        "05b040d6b1e15273", "d4f0cf9d15e5660c", "a1e6a6bd27b9863b",
        "99d9cfc33a8e8c85", "498fd9541ec81b0f", "6737545c46a6f26a",
        "3ee1613b56d130c5", "c4c84c65e3d7020a", "b0f228b0a86a1e2f"],
    "vail_co|canonical|extreme": [
        "d05c234767c7e378", "9cf9c588583bfee9", "08667f1baf49c168",
        "19f70add8eae7818", "45018a3166224d5b", "d5a5e0387a04d38e",
        "33401a79f4cd9959", "d6da7acbf6019fcf"],
}


def _harness(run_id, cfg):
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "scratch_review",
                                    "eagle_valley"))
    try:
        import run_matrix as rm
    finally:
        sys.path.pop(0)
    loc, ign, inten = run_id.split("|")
    if not simui.tile_file(loc).exists():
        pytest.skip(f"LOUD SKIP: {loc} tile not cached")
    _mgr, s = rm.build(loc, ign, inten, cfg)
    return s


@pytest.mark.parametrize("run_id", sorted(GOLDENS))
def test_off_is_byte_identical(run_id):
    s = _harness(run_id, ITER3_OFF)
    hashes = [_hash(s.tick()) for _ in range(len(GOLDENS[run_id]))]
    assert hashes == GOLDENS[run_id]


# ---------------------------------------------------------------------------
# (b) the entry choice
# ---------------------------------------------------------------------------

def test_a_reinforcement_enters_on_the_side_of_the_work(monkeypatch):
    """The corridor tile's canonical fire, its first field: with the homes
    waiting for a crew at Eagle (west) the 1 h crew — its region's side is
    east — enters by the west road entry (both entries reach them lawfully;
    the west one in 15 min), and says so; with them at East Vail it keeps its
    own side, unsaid."""
    from wgresponse.contracts import LatLon
    s = _harness("eagle_vail_co|canonical|high", {})
    s.tick()
    east, west = s._side_entry(True), s._side_entry(False)
    assert east.lon > west.lon
    eagle = [LatLon(39.66617, -106.80053), LatLon(39.66258, -106.81456)]
    monkeypatch.setattr(s, "_open_work", lambda now: list(eagle))
    e, note = s._entry_by_need("sim-g3", s._now_s, 900.0, 15.0)
    assert e is west
    assert note.startswith("reinforcement sim-g3 enters from the west "
                           "(SEMANTICS 34b): 2 of the 2 homes")
    east_vail = [simui.LOCATIONS["eagle_vail_co"].safe_zones[5].loc]
    monkeypatch.setattr(s, "_open_work", lambda now: list(east_vail))
    e, note = s._entry_by_need("sim-g3", s._now_s, 900.0, 15.0)
    assert e is east and note is None
