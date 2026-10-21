"""SAFETY SEMANTICS 31 — ROAD EGRESS CARRIES TRAFFIC SLACK (2026-10-21; the
Eagle River valley review's iteration 2; contracts route_margin_egress_roads_
min; docs/SIMULATOR_DESIGN.md "A crew never waits in a pocket").

route_margin_roads_min equals egress_buffer_min, so a road egress the planner
judged lawful passes cells exactly at the 10c line: the first minute of delay
on the valley's one interstate is a violation. With the knob > 0 a stop's
EGRESS legs on roads need max(route_margin_roads_min, the knob) minutes
between every sample's eroded P10 and its pass time (dispatch's per-stop zone
test); ingress legs and chord legs keep their margins.

These tests pin the knob (default off, armed by the simulator), a stop whose
road egress clears 30 min but not 45 (feasible off, no_safe_egress on), an
INGRESS leg with the same slack staying feasible, chord legs untouched, and
byte-identity with it off."""
from __future__ import annotations

import pytest

from wgresponse import dispatch, geo, simui
from wgresponse.contracts import (UNREACHED, LatLon, Raster, ResponseConfig,
                                  SafeZone)

from tests.test_dispatch import (NOW, _fingerprint, cc, make_crew, make_ens,
                                 make_job, spec20)

#: the road speed of the straight fake route (m/s)
SPEED = 12.0


class _StraightRoute:
    """A road route straight from a to b at SPEED (on roads)."""

    def __init__(self, a, b):
        from wgresponse import geo
        self.polyline = [a, b]
        self.dist_m = max(1.0, geo.haversine_m(a, b))
        self.time_s = self.dist_m / SPEED
        self.on_roads = True


def _provider(calls):
    def provider(a, b, mode="ingress"):
        calls.append(mode)
        return _StraightRoute(a, b)
    return provider


def _world(slack_min: float, leg: str):
    """leg='egress': the crew at (10, 2), one job at (10, 4) (its own
    deadline far away), one zone at (10, 8); every cell of columns 5-8 (row
    10) — the EGRESS leg's ground — is reached by the P10 `slack_min`
    minutes after the crew would pass the zone on the earliest plan (drive
    in, coat 30 min, leave). leg='ingress': the crew at (10, 0), the job at
    (10, 5), the zone at (10, 9); columns 1-2 — the INGRESS leg's ground —
    reached `slack_min` minutes after the crew passes column 2 setting out
    now (the planner's 500 m erosion carries it one cell on, to column 3,
    passed `slack_min` - 1.4 min before it; the egress ground is clear)."""
    spec = spec20()
    data = [UNREACHED] * (spec.nrows * spec.ncols)
    if leg == "egress":
        crew = make_crew("A", cc(spec, 10, 2))
        job = make_job("c1", cc(spec, 10, 4), NOW + 20 * 3600.0,
                       NOW + 22 * 3600.0)
        zone = SafeZone("z1", cc(spec, 10, 8))
        depart = NOW + 2000.0 / SPEED + 30 * 60.0
        t_far = depart + 4000.0 / SPEED
        cols = range(5, 9)
    else:
        crew = make_crew("A", cc(spec, 10, 0))
        job = make_job("c1", cc(spec, 10, 5), NOW + 20 * 3600.0,
                       NOW + 22 * 3600.0)
        zone = SafeZone("z1", cc(spec, 10, 9))
        t_far = NOW + 2000.0 / SPEED
        cols = range(1, 3)
    for c in cols:
        data[10 * spec.ncols + c] = t_far + slack_min * 60.0
    ens = {"inc1": make_ens(Raster(spec, data))}
    return crew, job, zone, ens


def _plan(cfg, crew, job, zone, ens, calls=None):
    return dispatch.plan([crew], [job], ens, [zone], cfg, NOW,
                         road_provider=_provider(calls if calls is not None
                                                 else []))


def test_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().route_margin_egress_roads_min == 0.0
    s = simui.SimSession("boulder_co", 2, 1, seed="em", roads_module=None)
    assert s._cfg.route_margin_egress_roads_min == simui.SIM_ROUTE_MARGIN_EGRESS_ROADS_MIN == 45.0
    s = simui.SimSession("boulder_co", 2, 1, seed="em", roads_module=None,
                         cfg_overrides={"route_margin_egress_roads_min": 0.0})
    assert s._cfg.route_margin_egress_roads_min == 0.0


def test_validation_bounds():
    from wgresponse.contracts import validate_config
    assert validate_config(ResponseConfig(route_margin_egress_roads_min=45.0)) == []
    assert any("route_margin_egress_roads_min" in p for p in validate_config(
        ResponseConfig(route_margin_egress_roads_min=-1.0)))


@pytest.mark.parametrize("slack_min", [40.0])
def test_a_road_egress_with_less_than_the_slack_is_refused(slack_min):
    """The egress leg's far end is reached by the eroded P10 40 min after the
    crew passes it: lawful at the road margin (30), not at 45. Off: the stop
    is served; armed: unserved, no_safe_egress — its zone legs were judged in
    'egress' mode."""
    crew, job, zone, ens = _world(slack_min, "egress")
    calls = []
    off = _plan(ResponseConfig(), crew, job, zone, ens, calls)
    assert [s.client_id for s in off.plans["A"].stops] == ["c1"]
    assert "egress" not in calls
    calls = []
    on = _plan(ResponseConfig(route_margin_egress_roads_min=45.0), crew, job,
               zone, ens, calls)
    assert on.plans["A"].stops == []
    assert [u.reason for u in on.unserved] == ["no_safe_egress"]
    assert "egress" in calls


def test_a_wider_slack_keeps_the_stop():
    crew, job, zone, ens = _world(50.0, "egress")
    on = _plan(ResponseConfig(route_margin_egress_roads_min=45.0), crew, job,
               zone, ens)
    assert [s.client_id for s in on.plans["A"].stops] == ["c1"]


def test_an_ingress_leg_keeps_the_road_margin():
    """The same ~40-min slack on the INGRESS leg: feasible both ways (the
    knob is an egress margin)."""
    crew, job, zone, ens = _world(40.0, "ingress")
    for em in (0.0, 45.0):
        plan = _plan(ResponseConfig(route_margin_egress_roads_min=em), crew,
                     job, zone, ens)
        assert [s.client_id for s in plan.plans["A"].stops] == ["c1"], em


def test_chord_legs_are_untouched():
    """No road provider: chord legs keep route_margin_min whatever the knob
    — the plans are identical."""
    crew, job, zone, ens = _world(40.0, "egress")
    plans = [_fingerprint(dispatch.plan(
        [crew], [job], ens, [zone],
        ResponseConfig(route_margin_egress_roads_min=em), NOW))
        for em in (0.0, 45.0)]
    assert plans[0] == plans[1]


def test_knob_off_is_byte_identical_on_road_plans():
    """0 = the legacy egress T* (computed in 'ingress' mode): the plan's
    fingerprint equals a plan built with the knob absent from the config's
    effect — every value of the slack world, off."""
    for slack in (20.0, 40.0, 60.0):
        crew, job, zone, ens = _world(slack, "egress")
        a = _fingerprint(_plan(ResponseConfig(), crew, job, zone, ens))
        b = _fingerprint(_plan(ResponseConfig(
            route_margin_egress_roads_min=0.0), crew, job, zone, ens))
        assert a == b


# ---------------------------------------------------------------------------
# SAFETY SEMANTICS 32 — A STOP'S EGRESS FITS ITS DEADLINE (contracts
# plan_egress_time_deadline): the planner's egress test is the simulator
# guard's — the zone must also be REACHED by the home's latest departure
# ---------------------------------------------------------------------------

#: the slow road of the deadline world (m/s): 5 km of egress take 20.8 min
SLOW = 4.0


class _SlowRoute(_StraightRoute):
    def __init__(self, a, b):
        super().__init__(a, b)
        self.time_s = self.dist_m / SLOW


def _slow_provider(a, b, mode="ingress"):
    return _SlowRoute(a, b)


def _deadline_world(margin_min: float):
    """The crew at (10, 2), one job at (10, 4), one zone at (10, 9), every
    road at SLOW (no fire raster bounds any leg: every T* is +inf). The
    job's latest departure lies `margin_min` minutes after the crew could
    finish it (drive in 2 km, coat deploy_min); its road egress takes 20.8
    min (5 km at SLOW, more than the tier-0 model's)."""
    from wgresponse import safety
    from wgresponse.travel import travel_time_s
    cfg = ResponseConfig()
    spec = spec20()
    crew = make_crew("A", cc(spec, 10, 2))
    zone = SafeZone("z1", cc(spec, 10, 9))
    job_loc = cc(spec, 10, 4)
    drive_in = max(1.0, geo.haversine_m(crew.home_base, job_loc)) / SLOW
    depart = NOW + drive_in + cfg.deploy_min * 60.0
    latest = depart + margin_min * 60.0
    p10 = latest + (cfg.egress_buffer_min + cfg.ember_buffer_min) * 60.0
    job = make_job("c1", job_loc, p10, p10 + 3600.0)
    data = [UNREACHED] * (spec.nrows * spec.ncols)
    ens = {"inc1": make_ens(Raster(spec, data))}
    egress = max(travel_time_s(job_loc, zone.loc, cfg),
                 _SlowRoute(job_loc, zone.loc).time_s)
    assert 20.0 * 60.0 < egress < 21.5 * 60.0
    assert safety.latest_departure_s(p10, cfg) == pytest.approx(latest)
    return crew, job, zone, ens


def _plan_slow(cfg, crew, job, zone, ens):
    return dispatch.plan([crew], [job], ens, [zone], cfg, NOW,
                         road_provider=_slow_provider)


def test_deadline_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().plan_egress_time_deadline is False
    s = simui.SimSession("boulder_co", 2, 1, seed="em", roads_module=None)
    assert s._cfg.plan_egress_time_deadline is True
    s = simui.SimSession("boulder_co", 2, 1, seed="em", roads_module=None,
                         cfg_overrides={"plan_egress_time_deadline": False})
    assert s._cfg.plan_egress_time_deadline is False


def test_a_stop_whose_egress_misses_its_deadline_is_not_planned():
    """18 min of margin clears the 15-min slack floor (planned off) but not
    the 20.8-min road egress (armed: unserved, no_safe_egress) — the stop
    the simulator's arrival guard would withdraw (Vail south extreme t6)."""
    crew, job, zone, ens = _deadline_world(18.0)
    off = _plan_slow(ResponseConfig(), crew, job, zone, ens)
    assert [s.client_id for s in off.plans["A"].stops] == ["c1"]
    on = _plan_slow(ResponseConfig(plan_egress_time_deadline=True), crew,
                    job, zone, ens)
    assert on.plans["A"].stops == []
    assert [u.reason for u in on.unserved] == ["no_safe_egress"]


def test_a_stop_with_time_to_reach_its_zone_is_planned_identically():
    """25 min of margin covers the 20.8-min egress: the armed plan is the
    unarmed one, value for value."""
    crew, job, zone, ens = _deadline_world(25.0)
    plans = [_fingerprint(_plan_slow(
        ResponseConfig(plan_egress_time_deadline=on), crew, job, zone, ens))
        for on in (False, True)]
    assert plans[0] == plans[1]
    assert plans[0][0][0][2][0][0] == "c1"


def test_deadline_knob_off_is_byte_identical():
    """Off = the legacy test (d <= T* alone): every margin of the deadline
    world plans identically with the knob absent and explicitly False."""
    for m in (10.0, 18.0, 25.0, 60.0):
        crew, job, zone, ens = _deadline_world(m)
        a = _fingerprint(_plan_slow(ResponseConfig(), crew, job, zone, ens))
        b = _fingerprint(_plan_slow(ResponseConfig(
            plan_egress_time_deadline=False), crew, job, zone, ens))
        assert a == b
