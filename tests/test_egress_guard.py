"""SAFETY SEMANTICS 29 — A STOP IS WORKABLE ONLY IF ITS CREW CAN STILL LEAVE
(2026-10-21; the Eagle River valley review's iteration 2; contracts
egress_corridor_guard; docs/SIMULATOR_DESIGN.md "A crew never waits in a
pocket").

The workability law (23a / 27b) and the deploy guards tested a stop's
departure against the home's deadline and the stop's own cell at the
departure INSTANT — never the road out: a stop kept after a late fire was
worked until every egress from it was 5.6-86 min past the 10c line, and in
the rug-pull control a crew left a stop 12 s after the line reached the
stop's cell (the path audit judges the cell when the crew LEAVES it).

With the knob on a stop is workable only if, departing at its projected
departure, a leg from it to some catalog zone is lawful at every pass time —
the stop's own cell judged as the crew leaves it — at the egress line; the
workability cell bound counts the seconds the crew still needs to drive out
of the stop's cell; the ingress judgement releases a stop that fails, and a
deploying crew whose projected departure has no lawful way out withdraws
(cause egress_guard) at arrival and at every step.

These tests pin the knob (default off, armed by the simulator), the
judgement on painted fields, and the rug-pull control's 12 s with it armed
and off (the off arm = the iteration-1 session, pinned in tests/
test_crew_path_audit.py)."""
from __future__ import annotations

import math

import pytest

from wgresponse import geo, simui
from wgresponse.contracts import LatLon, ResponseConfig

from tests.test_crew_egress_judged import _judged_session, _pt
from tests.test_crew_work_judged import _crew_at, _paint, _stop
from tests.test_simui_policy import _arm_mid_deploy, _inject_spot_fire, _session

ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False,
             # ... and the iteration-3 knobs (SAFETY SEMANTICS 34-35)
             "sim_far_side_zones": False, "reinforce_entry_by_need": False,
             "ember_eta_landings": False,
             # ... and the iteration-4 laws (SAFETY SEMANTICS 36-37)
             "escape_refuge_on_map": False, "route_margin_egress_ramp": 0.0,
             # ... and the iteration-5 law (SAFETY SEMANTICS 38)
             "egress_dest_durable_h": 0.0}
GUARD_ONLY = dict(ITER2_OFF, egress_corridor_guard=True)


def test_knob_is_off_by_default_and_the_simulator_arms_it(tmp_path):
    assert ResponseConfig().egress_corridor_guard is False
    assert _judged_session(tmp_path)._cfg.egress_corridor_guard is True
    assert _judged_session(
        tmp_path, egress_corridor_guard=False)._cfg.egress_corridor_guard \
        is False


# ---------------------------------------------------------------------------
# the judgement on painted fields (straight legs, 250 m cells, zones 6 km E/W)
# ---------------------------------------------------------------------------

def _deploying_at_centre(s, deploy_s=600.0):
    centre = _pt(0, 0.0)
    t0 = s._now_s
    stop = dict(_stop("u-c", centre, s, deploy_s=deploy_s),
                actual_arrive_s=t0, actual_service_start_s=t0)
    cs = _crew_at(s, centre, [stop], mode="deploy", zone_id="zA")
    cs._deploy_end_s = t0 + deploy_s
    return cs, stop, centre


def test_the_stop_cell_is_judged_as_the_crew_leaves_it(tmp_path):
    """The stop's own 250 m cell is reached by the 10c line 5 s after the
    deploy ends: lawful at the departure INSTANT (the 23a / 27b bound), not
    by the time the crew has driven out of the cell. The guard's way out
    finds no lawful leg, the exit-aware workability bound is earlier than the
    departure, and the arrival check withdraws the crew (egress_guard)."""
    s = _judged_session(tmp_path, **GUARD_ONLY)
    buf = s._cfg.egress_buffer_min * 60.0
    cs, stop, centre = _deploying_at_centre(s)
    dep = cs._deploy_end_s
    _paint(s, p10_paint=[(centre, 200.0, dep + buf + 5.0)])
    zone, latest = s._egress_way_out(centre, dep, cs.speed)
    assert zone is None
    assert latest < dep                     # lawful only before it leaves
    # the legacy instant bound: lawful at the departure instant
    assert s._work_latest_depart_s(stop, centre, s._now_s) >= dep
    exit_s = s._cell_exit_s(centre, s._zone_by_id["zA"].loc, cs.speed)
    assert exit_s > 5.0
    assert s._work_latest_depart_s(stop, centre, s._now_s, exit_s) < dep
    assert s._egress_guard_at_arrival(cs, s._now_s) is True
    assert cs._mode == "egress"
    (w,) = cs.drain_withdrawals()
    assert w["cause"] == "egress_guard" and w["client_id"] == "u-c"


def test_a_clear_stop_keeps_its_crew(tmp_path):
    """The same stop with its cell clear for an hour after the deploy: a way
    out exists (the nearer zone), nothing is withdrawn."""
    s = _judged_session(tmp_path, **GUARD_ONLY)
    buf = s._cfg.egress_buffer_min * 60.0
    cs, _stop_, centre = _deploying_at_centre(s)
    _paint(s, p10_paint=[(centre, 200.0, cs._deploy_end_s + buf + 3600.0)])
    zone, latest = s._egress_way_out(centre, cs._deploy_end_s, cs.speed)
    assert zone is not None and zone.zone_id in ("zA", "zB")
    assert latest >= cs._deploy_end_s
    assert s._egress_guard_at_arrival(cs, s._now_s) is False
    assert cs._mode == "deploy"


def _egress_blocked_world(s, after_s):
    """A stop 1.5 km north of the centre; two bands on the roads OUT of it
    (1 km east and 1 km west of the stop, 300 m wide) that the 10c line
    reaches `after_s` seconds after the crew could finish the stop — the
    road IN (from the centre, due south) is clear."""
    stop_loc = _pt(0, 1.5)
    t0 = s._now_s
    buf = s._cfg.egress_buffer_min * 60.0
    east = geo.destination(stop_loc, 90.0, 1000.0)
    west = geo.destination(stop_loc, 270.0, 1000.0)
    # arrive ~2 min, coat 15 min: departure ~t0 + 17 min; the bands' line
    t_line = t0 + 17 * 60.0 + after_s
    _paint(s, p10_paint=[(east, 300.0, t_line + buf),
                         (west, 300.0, t_line + buf)])
    return stop_loc


@pytest.mark.parametrize("guard", [False, True])
def test_a_stop_with_no_way_out_after_the_work_is_released(tmp_path, guard):
    """A crew at rest at the centre sets out for the stop; both roads out of
    it cross a band whose 10c line arrives a minute after the crew could
    leave (the road in and the stop itself clear). Guard off: workable (the
    legacy law never looks at the road out). Guard on: unworkable — the 27b
    judgement releases it as the crew sets out."""
    cfg = dict(ITER2_OFF, egress_corridor_guard=guard)
    s = _judged_session(tmp_path, **cfg)
    stop_loc = _egress_blocked_world(s, after_s=60.0)
    stop = _stop("u-n", stop_loc, s, deploy_s=900.0)
    cs = _crew_at(s, _pt(0, 0.0), [stop], mode="idle", zone_id="zA")
    why = s._ingress_unworkable(cs, stop, s._now_s)
    if not guard:
        assert why is None
        return
    assert why is not None and "SEMANTICS 29" in why
    n0 = len(s._notes)
    s._judge_ingress_start(cs, s._now_s)
    assert cs.itinerary == []
    assert any("released u-n" in n and "SEMANTICS 29" in n
               for n in s._notes[n0:])


def test_a_deploying_crew_whose_way_out_closes_withdraws(tmp_path):
    """Deploying at the stop, its projected departure past the moment the
    roads out close: the step guard's way out is None and the withdrawal law
    applies (abort unfinished, egress now, loud record, cause egress_guard);
    the client is freed 'withdrawn_unsafe'."""
    s = _judged_session(tmp_path, **GUARD_ONLY)
    stop_loc = _egress_blocked_world(s, after_s=-300.0)
    t0 = s._now_s
    stop = dict(_stop("u-n", stop_loc, s, deploy_s=900.0),
                actual_arrive_s=t0, actual_service_start_s=t0)
    cs = _crew_at(s, stop_loc, [stop], mode="deploy", zone_id="zA")
    cs._deploy_end_s = t0 + 17 * 60.0
    zone, latest = s._egress_way_out(cs.pos, cs._deploy_end_s, cs.speed)
    assert zone is None and latest < cs._deploy_end_s
    s._egress_guard_withdraw("sim-g1", cs, stop, latest, t0)
    assert cs._mode == "egress" and cs.itinerary == []
    rec = s._withdrawn[-1]
    assert rec["cause"] == "egress_guard" and rec["client_id"] == "u-n"
    assert rec["deficit_min"] > 0.0
    assert s._unserved_reason["u-n"] == "withdrawn_unsafe"


# ---------------------------------------------------------------------------
# the rug-pull control's 12 s (tests/test_crew_path_audit.py pins the
# iteration-1 session with this knob off)
# ---------------------------------------------------------------------------

def _rugpull_control(tmp_path, cfg):
    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg=dict({"spread_any_angle": False}, **cfg))
    st, _crew, cur, _gap = _arm_mid_deploy(s)
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    rows = []
    while st["tick"] < 60:
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"])
        rows.extend((st["tick"], o) for o in st["crew_overruns"])
    return st, s, rows


def test_rugpull_control_no_departure_inside_the_line_with_the_guard(
        tmp_path):
    """Off (the iteration-1 session): at tick 8 the crew leaves sim-c9 12 s
    after the 10c line reached the stop's cell — the path audit's one row.
    Armed: the stop is judged with the seconds the crew needs to leave its
    cell, so the crew never stands there past the line — no row in 60
    ticks, and no departure violation either way."""
    st_off, _s, rows_off = _rugpull_control(tmp_path, ITER2_OFF)
    assert [(t, o["crew_id"], o.get("transit")) for t, o in rows_off] == [
        (8, "sim-g1", True)]
    st_on, s_on, rows_on = _rugpull_control(tmp_path, GUARD_ONLY)
    assert rows_on == []
    assert st_on["scoreboard"]["violations"] == 0


#: sha256(state minus cmd_log)[:16] per tick after the spot fire, computed on
#: 0109ad3: the rug-pull control (tests/test_crew_path_audit.py's twin), ticks
#: up to 20
GOLDEN_RUGPULL_CONTROL = [
    "1bda33f27d79fad2", "ca0fc6ded8cc62de", "8e538fcec181492c",
    "f06aba7637c6d300", "844fbf9539dc732c", "9655b530086068b7",
    "fe63ebfa7ab222db", "796f88fe1b48d2bd", "d44ec22e719acccc",
    "b7b59dd2839fa1e8", "0ec67ed1d2f42304", "3584ab539bcdb08f",
    "fe7475edda52427a", "7a0b3c3215e87077", "a14b827cf3c5fba1",
    "ef97f40138644b34", "cc4d8300c9135177"]


def test_rugpull_control_knob_off_is_byte_identical(tmp_path):
    import hashlib
    import json

    def _h(st):
        st = dict(st)
        st.pop("cmd_log", None)
        st.pop("report", None)   # run report: display only (2026-10-27)
        return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                              .encode()).hexdigest()[:16]

    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg=dict({"spread_any_angle": False}, **ITER2_OFF))
    st, _crew, cur, _gap = _arm_mid_deploy(s)
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    hashes = []
    while st["tick"] < 20:
        st = s.tick()
        hashes.append(_h(st))
    assert hashes == GOLDEN_RUGPULL_CONTROL


# ---------------------------------------------------------------------------
# the 16-direction withdrawal (tests/test_crew_work_judged.py pins the
# iteration-1 arm with this knob off: deficit 8.5 min)
# ---------------------------------------------------------------------------

def test_the_sixteen_direction_withdrawal_counts_the_cell_exit(tmp_path):
    """The work-judged world's 16-direction deploy (spot fire 1.5 km at the
    second client): the same withdrawal at the same instant (tick 5's start,
    sim-c15, cause work_judged), but its deficit counts the seconds the crew
    still needs to drive out of the stop's cell — 9.3 min, not 8.5 — and no
    overrun row either way."""
    from tests.test_crew_work_judged import _golden_world
    for cfg, deficit in ((ITER2_OFF, 8.5), (GUARD_ONLY, 9.3)):
        _h, rows, s = _golden_world(tmp_path, False, 1500.0, 2, 6, cfg)
        assert rows == [0] * len(rows)
        (w,) = s.state()["withdrawn"]
        assert (w["client_id"], w["cause"]) == ("sim-c15", "work_judged")
        assert w["deficit_min"] == pytest.approx(deficit, abs=0.1)
        assert w["at_s"] == s.state()["t0_s"] + 4 * 900.0


# ---------------------------------------------------------------------------
# THE ONE PREDICATE (2026-10-21, the independent review's fold-in): the way
# out must also reach a zone by the stop's latest departure — the road-egress
# guard's law, to a zone the crew can lawfully reach — judged as the crew
# sets out, every step en route, at arrival and every step of the work
# ---------------------------------------------------------------------------

def test_the_one_predicate_counts_the_egress_drive(tmp_path, monkeypatch):
    """Deploying at the centre, both zones 6 km out on lawful legs: with the
    stop's latest departure 5 min after the deploy ends the crew cannot
    reach either zone in time — cause road_egress, its latest the deadline
    less the shorter drive; with a minute to spare beyond the drive the stop
    keeps its crew. The arrival check withdraws with that cause and carries
    the drive's minutes."""
    s = _judged_session(tmp_path, **GUARD_ONLY)
    cs, stop, centre = _deploying_at_centre(s)
    dep = cs._deploy_end_s
    ways, _latest = s._egress_ways(centre, dep, cs.speed)
    drive = min(max(d, t0) for _z, d, t0 in ways)
    assert len(ways) == 2 and drive > 600.0
    monkeypatch.setattr(s, "_client_latest_s", lambda st: dep + 300.0)
    cause, latest, egress_s = s._stop_exit_verdict(stop, centre, dep,
                                                   cs.speed)
    assert cause == "road_egress"
    assert egress_s == pytest.approx(drive)
    assert latest == pytest.approx(dep + 300.0 - drive)
    monkeypatch.setattr(s, "_client_latest_s",
                        lambda st: dep + drive + 60.0)
    assert s._stop_exit_verdict(stop, centre, dep, cs.speed) == (None, None,
                                                                 0.0)
    monkeypatch.setattr(s, "_client_latest_s", lambda st: dep + 300.0)
    assert s._egress_guard_at_arrival(cs, s._now_s) is True
    (w,) = cs.drain_withdrawals()
    assert w["cause"] == "road_egress"
    assert w["road_eff_egress_min"] == pytest.approx(drive / 60.0)


def test_a_head_stop_is_judged_again_every_step_en_route(tmp_path,
                                                         monkeypatch):
    """En route to a stop that was workable when the crew set out: the step
    judgement keeps it while the way out holds and RELEASES it — before the
    crew gets there, loudly, once — when the stop's latest departure no
    longer leaves time to reach a zone; the crew, left with nothing, leaves
    as after its last stop."""
    s = _judged_session(tmp_path, **GUARD_ONLY)
    stop_loc = _pt(0, 1.5)
    stop = _stop("u-n", stop_loc, s, deploy_s=900.0)
    cs = _crew_at(s, _pt(0, 0.0), [stop], mode="travel", zone_id="zA")
    now = s._now_s
    s._judge_head_en_route("sim-g1", cs, now)
    assert cs.itinerary == [stop]
    monkeypatch.setattr(s, "_client_latest_s", lambda st: now + 600.0)
    n0 = len(s._notes)
    s._judge_head_en_route("sim-g1", cs, now)
    assert cs.itinerary == [] and cs._mode == "egress"
    said = [n for n in s._notes[n0:] if "released u-n en route" in n]
    assert len(said) == 1 and "SEMANTICS 29" in said[0]
    assert s._n_en_route_released == 1


def test_the_one_predicate_supersedes_the_route_level_road_guard(tmp_path):
    """The legacy road-egress guard's zone is the plan's ROUTE-level one
    (the last stop's): armed, a crew arriving at its stop is judged by the
    one predicate (the session's hook) and the route-level check is not run;
    off, the route-level check runs as ever and there is no hook."""
    for cfg, hooked in ((GUARD_ONLY, True), (ITER2_OFF, False)):
        s = _judged_session(tmp_path, **cfg)
        stop = _stop("u-c", _pt(0, 0.3), s, deploy_s=600.0)
        cs = _crew_at(s, _pt(0, 0.0), [stop], mode="travel", zone_id="zA")
        calls, hooks = [], []
        cs._road_depart_check = lambda t: calls.append(t) or False
        real = cs._egress_guard
        assert (real is not None) is hooked
        if hooked:
            cs._egress_guard = lambda c, t: hooks.append(t) or real(c, t)
        cs.advance(s._now_s, s._now_s + 300.0, [])
        assert cs._mode == "deploy"
        assert (len(hooks), len(calls)) == ((1, 0) if hooked else (0, 1))


# ---------------------------------------------------------------------------
# guard parity — the independent review's regression of iteration 1 (the
# valley harness's Vail south fire at extreme)
# ---------------------------------------------------------------------------

#: sha256(state minus cmd_log)[:16] per tick, computed on 0109ad3: the valley
#: harness's vail_co south fire at extreme (5 km SSW of the centre on its
#: nearest grass, wind 200), 8 ticks
GOLDEN_VAIL_SOUTH_EXTREME = [
    "384a9854fa4a94e8", "832aa70b852e7cf0", "955bdaec4cfd063f",
    "5c1c744cb406f767", "5953bf59efdd0227", "c6ced23736d603e2",
    "e4aefa0226c9d272", "3a5cceb40812c7e1"]
PARITY = dict(ITER2_OFF, egress_corridor_guard=True,
              plan_egress_time_deadline=True)


def _vail_south(cfg):
    import hashlib
    import json
    from wgresponse import server
    if not simui.tile_file("vail_co").exists():
        pytest.skip("LOUD SKIP: vail_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": "vail_westerly", "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    s.reset()
    p = s.fine_fuel_start(200.0, 5000.0)
    s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=200.0)

    def run(n):
        hashes, states = [], []
        for _ in range(n):
            st = s.tick()
            d = dict(st)
            d.pop("cmd_log", None)
            d.pop("report", None)   # run report: display only (2026-10-27)
            hashes.append(hashlib.sha256(json.dumps(
                d, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16])
            states.append(st)
        return hashes, states
    return run


def test_guard_parity_the_stop_withdrawn_on_arrival_is_worked():
    """Off (byte-identical to commit 0109ad3): dispatch sends sim-g1 to
    sim-c14 with 17.7 min of planned margin; the road-egress guard times
    its egress to the plan's ROUTE-level zone (the last stop's, 22.1 min
    away) and withdraws it on arrival at tick 6, 4.6 min late — the home
    and the crew's queue dropped. Armed (29's one predicate, 32's planner
    term): the egress is timed to a zone the crew can lawfully reach from
    sim-c14 — the stop is worked (tick 7), nothing is withdrawn."""
    hashes, states = _vail_south(ITER2_OFF)(8)
    assert hashes == GOLDEN_VAIL_SOUTH_EXTREME
    (w,) = states[-1]["withdrawn"]
    assert (w["crew_id"], w["client_id"], w["cause"]) == (
        "sim-g1", "sim-c14", "road_egress")
    assert w["road_eff_egress_min"] == pytest.approx(22.1, abs=0.05)
    assert w["deficit_min"] == pytest.approx(4.58, abs=0.05)
    assert not any(x["client_id"] == "sim-c14" for x in states[-1]["serviced"])
    _h, states = _vail_south(PARITY)(8)
    assert all(st["withdrawn"] == [] for st in states)
    first = min(i + 1 for i, st in enumerate(states)
                for x in st["serviced"] if x["client_id"] == "sim-c14")
    assert first == 7


def test_the_slow_road_world_never_sets_out_for_a_stop_it_could_not_leave(
        tmp_path):
    """tests/test_simui.py's road-egress world (every road to a safe zone
    takes 40 h; pinned there with these laws off, where the crew deploys
    and the route-level guard withdraws it): armed, no stop is ever worked
    or withdrawn — the planner's egress term (32) and the one predicate
    (29) refuse a stop no crew could leave in time — and nothing is
    violated."""
    from tests.test_simui import _FakeRoads, _road_session
    zones = [z.loc for z in simui.LOCATIONS["boulder_co"].safe_zones]
    fake = _FakeRoads(speed_mps=15.0, slow_to=zones, slow_time_s=40 * 3600.0)
    s = _road_session(tmp_path, fake)
    assert s._cfg.egress_corridor_guard and s._cfg.plan_egress_time_deadline
    for _ in range(8):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0
        assert st["withdrawn"] == []
        assert st["serviced"] == []
        assert all(c["status"] != "deploying" for c in st["crews"])


def test_the_slider_world_s_crew_leaves_at_the_world_change(tmp_path):
    """tests/test_simui_policy.py's slider world (pinned there with these
    laws off: the crew still deploys at sim-c12 after the phase-2 spot fire,
    and only the buffer slider withdraws it): armed, the one predicate pulls
    the crew out at the world change itself — the spot fire leaves no zone
    it may lawfully reach by the stop's latest departure (19.0 min of road
    egress, 2.2 min late) — before the slider ever moves; its egress is the
    durable zone (28a); nothing is violated."""
    from tests.test_simui_policy import TICK_END_AUDIT
    s = _session(tmp_path, ign_dist_m=4_600.0, cfg=TICK_END_AUDIT)
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]
    _inject_spot_fire(s, st, cur)
    st2 = s.tick()
    c = next(x for x in st2["crews"] if x["crew_id"] == crew["crew_id"])
    assert c["status"] == "egressing" and not c["itinerary"]
    (w,) = st2["withdrawn"]
    assert (w["client_id"], w["cause"]) == (victim, "road_egress")
    assert w["road_eff_egress_min"] == pytest.approx(19.0, abs=0.1)
    assert w["deficit_min"] == pytest.approx(2.2, abs=0.1)
    assert st2["scoreboard"]["violations"] == 0
    for _ in range(6):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]
