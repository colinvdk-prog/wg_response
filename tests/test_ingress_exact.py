"""SAFETY SEMANTICS 27 — THE PLANNER JUDGES THE ROAD THE CREW DRIVES, AND EVERY
INGRESS LEG IS JUDGED AS IT STARTS (2026-10-21; the Eagle River valley
review; contracts plan_route_exact / crew_ingress_judged; docs/
SIMULATOR_DESIGN.md "The audit sees the whole drive").

(a) The plan-on-roads provider cached routes on ~110 m position buckets, so
dispatch judged a crew's leg on the route from whichever point of its bucket
was routed first while the crew drove the route from its exact position —
measured in the valley: planned 10.7 km and +58.6 min clear from 17 m away,
the crew's own leg 9.6 km and 51 min past the 10c line. With
plan_route_exact the cache is keyed on the exact endpoints.

(b) SEMANTICS 23a judged an ingress leg as it starts only when the fields
changed since its stop was planned; a leg dispatch judged from another
point, at another time or on another road (the bucket; tier-0 chords when
plan-on-roads is off) was driven unjudged. With crew_ingress_judged every
ingress leg is judged by the 23a law as it starts — from rest, from a stop,
and a fresh leg begun en route after a re-plan.

These tests pin both knobs (default off, armed by the simulator), the
provider's exact routes, the judgement of current-generation legs on painted
fields, the any-angle rug-pull caught with SEMANTICS 23 off, and byte-
identity with both off on worlds where each bites (goldens computed on
commit 7bebc7a, before the rule)."""
from __future__ import annotations

import hashlib
import json

import pytest

from wgresponse import geo, server, simui
from wgresponse.contracts import LatLon, ResponseConfig

from tests.test_crew_egress_judged import _judged_session, _one_click, _pt
from tests.test_crew_work_judged import _crew_at, _paint, _stop
from tests.test_simui_policy import (_arm_mid_deploy, _inject_spot_fire,
                                     _session)

#: ... and the iteration-2 knobs (SAFETY SEMANTICS 28-33, 2026-10-21), armed
#: by the simulator since: this file's goldens and derivations predate
#: them too (their own goldens and armed behaviour: tests/test_crew_pocket.
#: py, test_egress_guard.py, test_stage_by_need.py, test_egress_margin.py)
ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False}
ITER1_OFF = {"crew_path_audit": False, "dispatch_keep_frozen": False,
             "plan_route_exact": False, "crew_ingress_judged": False,
             **ITER2_OFF}


def _only(knob):
    return dict(ITER1_OFF, **{knob: True})


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _rows(st):
    return [(o["crew_id"], o["reason"], o["mode"], bool(o.get("transit")))
            for o in st["crew_overruns"]]


def test_knobs_are_off_by_default_and_the_simulator_arms_them(tmp_path):
    cfg = ResponseConfig()
    assert cfg.plan_route_exact is False and cfg.crew_ingress_judged is False
    s = _judged_session(tmp_path)
    assert s._cfg.plan_route_exact and s._cfg.crew_ingress_judged
    s = _judged_session(tmp_path, plan_route_exact=False,
                        crew_ingress_judged=False)
    assert not s._cfg.plan_route_exact and not s._cfg.crew_ingress_judged


# ---------------------------------------------------------------------------
# (a) the planner's routes are the crew's own (plan_route_exact)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("knob", [True, False])
def test_the_planner_routes_the_crew_from_its_exact_position(knob):
    """Malibu, plan on roads: the crew stands at its staging zone; a point
    40 m north lies in the same ~110 m bucket. Routed first from that point,
    the legacy cache serves the crew the SAME route — from the other point,
    26 m shorter — for every home; armed, the crew's route is exactly the
    one its leg resolves (_leg_route: the same roads.route call)."""
    if not simui.tile_file("malibu_ca").exists():
        pytest.skip("LOUD SKIP: malibu_ca tile not cached")
    s = simui.SimSession("malibu_ca", 4, 1, seed="exact",
                         cfg_overrides={"dispatch_plan_on_roads": True,
                                        "plan_route_exact": knob})
    if s._road_graph is None:
        pytest.skip("LOUD SKIP: malibu_ca road graph not cached")
    p = next(iter(s._crew_sims.values())).pos
    q = LatLon(p.lat + 40.0 / geo.M_PER_DEG_LAT, p.lon)
    assert (round(q.lat, 3), round(q.lon, 3)) == (round(p.lat, 3),
                                                  round(p.lon, 3))
    for cl in s._clients:
        provider = s._plan_road_provider()
        r_q = provider(q, cl.loc)
        r_p = provider(p, cl.loc)
        own = s._leg_route(p, cl.loc)
        if knob:
            assert r_p is not r_q
            assert (r_p.dist_m, r_p.time_s, list(r_p.polyline)) == (
                own.dist_m, own.time_s, list(own.polyline))
        else:
            assert r_p is r_q                            # the bucket's
            assert r_p.polyline[0] == q and own.polyline[0] == p
            assert own.dist_m - r_p.dist_m == pytest.approx(25.8, abs=1.0)


#: sha256(state minus cmd_log)[:16] per tick, computed on 7bebc7a: the
#: Malibu one-click (tests/test_crew_egress_judged.py::_one_click — city
#: weather, grass-only floor, legacy embers, surface forest, 16 directions)
GOLDEN_MALIBU_EXTREME = [
    "9ef10b49b9e37515", "00776b4a801c205d", "3ac0d9fff424578f",
    "45090ac59a088409", "823ee4f5e12025b2", "19145b4a9a58ca5d",
    "9494c9cf3f488f93", "da0fa10711445967", "ed4f6aafa120c486",
    "30c4ba94fac90cc6", "0e4ba1732ce8e684", "06c708d33a40e9cc"]


def test_plan_route_exact_off_is_byte_identical_and_on_bites_at_tick_11():
    """Malibu one-click: every 2026-10-21 knob off replays the pre-rule
    session byte for byte. plan_route_exact alone: the same through tick 10;
    at tick 11 every route the planner asks for from sim-g1's position is
    another point's in the legacy session (~68 m off the route the crew
    resolves from where it stands), and exactly its own route armed — the
    plan's times change from there."""
    sessions = {}
    for name, cfg in (("off", ITER1_OFF),
                      ("exact", _only("plan_route_exact"))):
        s = _one_click("malibu_santa_ana", "extreme", "city", True, **cfg)
        hashes = []
        for _ in range(10):
            hashes.append(_hash(s.tick()))
        calls = []
        make = s._plan_road_provider

        def spying(make=make, calls=calls):
            prov = make()
            if prov is None:
                return None

            def p(a, b, mode="ingress"):
                rr = prov(a, b, mode=mode)
                calls.append((a, b, rr))
                return rr
            return p

        s._plan_road_provider = spying
        hashes.append(_hash(s.tick()))
        del s._plan_road_provider
        hashes.append(_hash(s.tick()))
        sessions[name] = (s, hashes, calls)
    s_off, h_off, calls_off = sessions["off"]
    s_ex, h_ex, calls_ex = sessions["exact"]
    assert h_off == GOLDEN_MALIBU_EXTREME
    assert h_ex[:10] == GOLDEN_MALIBU_EXTREME[:10]
    assert h_ex[10] != GOLDEN_MALIBU_EXTREME[10]
    assert calls_off and calls_ex
    off_by = [abs(rr.dist_m - s_off._leg_route(a, b).dist_m)
              for a, b, rr in calls_off]
    assert min(off_by) > 60.0                        # every one: elsewhere
    for a, b, rr in calls_ex:
        own = s_ex._leg_route(a, b)
        assert (rr.dist_m, list(rr.polyline)) == (own.dist_m,
                                                  list(own.polyline))


# ---------------------------------------------------------------------------
# (b) every ingress leg is judged as it starts (crew_ingress_judged)
# ---------------------------------------------------------------------------

def test_a_current_stop_is_judged_as_the_crew_sets_out(tmp_path):
    """tests/test_crew_work_judged.py::test_an_ingress_leg_is_judged_when_
    it_starts[current], armed: the stops carry the CURRENT generation
    (dispatch just planned them); the road east runs through a band already
    past its 10c line. 23 alone never re-judges them; with 27b the leg is
    judged as the crew sets out — u-east released (loud note), u-west (road
    clear) judged and kept, the crew drives west."""
    s = _judged_session(tmp_path)
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s - 60.0)])
    s._field_gen = 2
    east = _stop("u-east", _pt(90, 3.0), s, gen=2)
    west = _stop("u-west", _pt(270, 3.0), s, gen=2)
    cs = _crew_at(s, _pt(0, 0.0), [east, west])
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert cs._mode == "travel"
    assert [x["client_id"] for x in cs.itinerary] == ["u-west"]
    assert cs._leg["target"] == west["loc"]
    (note,) = [n for n in s._notes if "released u-east" in n]
    assert "(SEMANTICS 27b)" in note and "band" in note
    assert s._n_ingress_refused == 1 and s._n_work_released == 0


def test_a_fresh_leg_begun_en_route_is_judged(tmp_path):
    """A crew already en route whose leg a re-plan reset (adopt: _leg None)
    begins a FRESH leg from where it is — judged as it starts. The road east
    crosses the band: the stop is released and, with nothing left, the crew
    leaves from here as after its last stop (20b judges that egress)."""
    s = _judged_session(tmp_path)
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s - 60.0)])
    s._field_gen = 2
    cs = _crew_at(s, _pt(0, 0.0), [_stop("u-east", _pt(90, 3.0), s, gen=2)],
                  mode="travel")
    assert cs._leg is None
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert cs.itinerary == [] and cs._mode == "egress"
    assert any("released u-east (SEMANTICS 27b)" in n
               and "nothing workable left" in n for n in s._notes)


def test_a_fresh_leg_off_drives_as_resolved(tmp_path):
    """Knob off (SEMANTICS 23 on, the stop current): the fresh leg en route
    is driven unjudged — legacy."""
    s = _judged_session(tmp_path, crew_ingress_judged=False)
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s - 60.0)])
    s._field_gen = 2
    cs = _crew_at(s, _pt(0, 0.0), [_stop("u-east", _pt(90, 3.0), s, gen=2)],
                  mode="travel")
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert [x["client_id"] for x in cs.itinerary] == ["u-east"]
    assert cs._leg["target"] == _pt(90, 3.0)
    assert not any("SEMANTICS 27b" in n for n in s._notes)


def test_a_repeated_refusal_is_said_once_per_crew_client_generation(
        tmp_path):
    """A planner that re-offers the refused stop (its law and the crew's
    road disagree) gets it refused again: counted every time, said once per
    crew, client and field generation — and again after the fields change."""
    s = _judged_session(tmp_path)
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s - 60.0)])
    cs = _crew_at(s, _pt(0, 0.0), [])
    for gen in (2, 2, 3):
        s._field_gen = gen
        cs.itinerary = [_stop("u-east", _pt(90, 3.0), s, gen=gen)]
        s._judge_ingress_start(cs, s._now_s)
        assert cs.itinerary == []
    assert s._n_ingress_refused == 3
    assert sum("released u-east (SEMANTICS 27b)" in n for n in s._notes) == 2


def test_any_angle_rugpull_with_23_off_is_caught_by_27b(tmp_path):
    """tests/test_crew_work_judged.py::test_any_angle_rugpull_knob_off_
    overruns_on_the_ingress_leg, armed with 27b (SEMANTICS 23 still off):
    the queued leg to sim-c16 — planned before the planted fire existed — is
    judged as the crew sets out at tick 4 (40.3 min past the 10c line at its
    worst) and released; no crew-overrun row and no violation through tick
    60, the path audit included."""
    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg={"spread_any_angle": True, "crew_work_judged": False})
    st, crew, cur, _gap = _arm_mid_deploy(s)
    _inject_spot_fire(s, st, cur)
    s.tick()
    st = s.tick()
    assert st["crew_overruns"] == []
    (note,) = [n for n in st["notes"] if "released sim-c16" in n]
    assert "(SEMANTICS 27b)" in note and "worst -40.3 min" in note
    cs = s._crew_sims[crew["crew_id"]]
    assert [x["client_id"] for x in cs.itinerary][:1] == ["sim-c11"]
    while st["tick"] < 60:
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]


#: sha256(state minus cmd_log)[:16] per tick, computed on 7bebc7a: two one-
#: click scenarios with tier-0 chord planning (plan_on_roads false — the
#: page's toggle) while crews drive the road graph
GOLDEN_BOULDER_CHORDS = ["18998196456fa6a4", "afbc1a7a3cc0f6ee",
                         "798af5edb9fbae01"]
GOLDEN_PARADISE_CHORDS = ["398f394951d443b2", "7ddcac27b951e129",
                          "490097446e5b39fa"]


def _one_click_chords(key, **cfg):
    """tests/test_crew_egress_judged.py::_one_click at the preset intensity,
    planning tier-0 chords (the page's plan_on_roads toggle off)."""
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "ign_fine_fuel": False,
                                 "plan_on_roads": False, "cfg": dict(
        {"sim_weather_profile": "city", "ros_floor_grass_only": True,
         "spot_fire_size": False, "crown_fire": False,
         "spread_any_angle": False}, **cfg)})
    assert code == 200, out
    return mgr._entry(out["session_id"]).session


def _run(s, ticks):
    out = []
    for _ in range(ticks):
        st = s.tick()
        out.append((_hash(st), st))
    return out


def test_ingress_judged_off_is_byte_identical_and_on_refuses_the_road_leg():
    """Boulder one-click, chord planning: the planner judged a straight
    chord the crew does not drive. Every 2026-10-21 knob off: the pre-rule
    session byte for byte. The path audit alone: the same session, and at
    tick 1 it reports sim-g1 driving its ROAD leg through the band (the leg
    dispatch judged as a chord). With 27b too, the leg is judged as the
    crew sets out and refused (a loud note at tick 1): no row."""
    off = _run(_one_click_chords("boulder_chinook", **ITER1_OFF), 3)
    assert [h for h, _st in off] == GOLDEN_BOULDER_CHORDS
    path = _run(_one_click_chords("boulder_chinook",
                                  **_only("crew_path_audit")), 3)
    assert _rows(path[0][1]) == [("sim-g1", "imminent", "travel", True)]
    assert [h for h, _st in path][1:] == GOLDEN_BOULDER_CHORDS[1:]
    both = _run(_one_click_chords("boulder_chinook", **dict(
        _only("crew_path_audit"), crew_ingress_judged=True)), 3)
    assert both[0][0] != GOLDEN_BOULDER_CHORDS[0]
    assert all(_rows(st) == [] for _h, st in both)
    assert any("(SEMANTICS 27b)" in n for n in both[0][1]["notes"])


def test_ingress_judged_avoids_the_paradise_chord_overrun():
    """Paradise one-click, chord planning: the pre-rule session (byte for
    byte, every 2026-10-21 knob off) has a crew overrun at the tick-3
    audit — sim-g2 egressing inside the band after the road legs its chord
    plan sent it down. 27b alone judges those legs as they start: different
    from tick 1, and no row through tick 3."""
    off = _run(_one_click_chords("paradise_ne_wind", **ITER1_OFF), 3)
    assert [h for h, _st in off] == GOLDEN_PARADISE_CHORDS
    assert _rows(off[2][1]) == [("sim-g2", "imminent", "egress", False)]
    on = _run(_one_click_chords("paradise_ne_wind",
                                **_only("crew_ingress_judged")), 3)
    assert on[0][0] != GOLDEN_PARADISE_CHORDS[0]
    assert all(_rows(st) == [] for _h, st in on)
