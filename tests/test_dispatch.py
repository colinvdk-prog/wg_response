"""Tests for wgresponse.dispatch — deadline-TOPTW planner under hard safety rules.

geo.py is built by a sibling cluster; these tests use local fake rasters and
cell-center placement (per-cluster instructions), so they run with or without
geo.py present.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from wgresponse import dispatch, safety, travel
from wgresponse.contracts import (
    UNREACHED,
    ClientProperty,
    Crew,
    CrewPlan,
    CrewStatus,
    DispatchJob,
    DispatchPlan,
    EnsembleArrival,
    GridSpec,
    LatLon,
    PlannedStop,
    Raster,
    ResponseConfig,
    SafeZone,
    ThreatEta,
    WeatherForecast,
    WeatherSample,
    seeded_rng,
)

NOW = 1_800_000_000.0
INC = "inc1"


# ---------------------------------------------------------------------------
# scenario helpers
# ---------------------------------------------------------------------------

def spec20():
    return GridSpec(north_lat=34.20, west_lon=-117.20, cell_m=1000.0, nrows=20, ncols=20)


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def uniform_raster(spec, value):
    return Raster(spec, [value] * (spec.nrows * spec.ncols))


def make_ens(raster):
    return EnsembleArrival(spec=raster.spec, members=[], p10=raster, p50=raster,
                           p90=raster, t0_s=NOW, incident_id=INC)


def make_crew(crew_id, loc):
    return Crew(crew_id=crew_id, name=crew_id, status=CrewStatus.AVAILABLE, home_base=loc)


def make_job(client_id, loc, p10_s, p50_s, value=1_000_000.0, weight=1.0):
    return DispatchJob(
        client=ClientProperty(client_id=client_id, loc=loc, value_usd=value,
                              priority_weight=weight),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s, eta_p90_s=p50_s + 3600.0),
    )


def stops_by_client(plan):
    out = {}
    for cid, cp in plan.plans.items():
        for st in cp.stops:
            out[st.client_id] = (cid, st)
    return out


# ---------------------------------------------------------------------------
# 1. single crew / single job: full arithmetic chain
# ---------------------------------------------------------------------------

def test_single_crew_single_job_end_to_end():
    # value mode: this asserts the exact USD objective arithmetic (byte-identity
    # anchor for the legacy planner); the homes-mode chain is pinned separately
    # in test_homes_single_job_counts_one.
    cfg = ResponseConfig(dispatch_objective="value")
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew_loc, client_loc, zone_loc = cc(spec, 10, 2), cc(spec, 10, 7), cc(spec, 10, 9)
    crew = make_crew("A", crew_loc)
    job = make_job("c1", client_loc, NOW + 5 * 3600.0, NOW + 6 * 3600.0)
    zones = [SafeZone("z1", zone_loc)]

    plan = dispatch.plan([crew], [job], ens, zones, cfg, NOW)

    assert plan.unserved == []
    cp = plan.plans["A"]
    assert len(cp.stops) == 1
    st = cp.stops[0]
    tt = travel.travel_time_s(crew_loc, client_loc, cfg)
    assert st.arrive_s == pytest.approx(NOW + tt, rel=1e-9)
    # gel window opened long ago (p50 - 72 h << now): service starts on arrival
    assert st.service_start_s == pytest.approx(st.arrive_s, rel=1e-9)
    assert st.depart_s == pytest.approx(st.service_start_s + cfg.deploy_min * 60.0, rel=1e-9)
    assert st.latest_depart_s == pytest.approx(NOW + 5 * 3600.0 - cfg.egress_buffer_min * 60.0)
    assert st.depart_s <= st.latest_depart_s
    assert st.margin_h == pytest.approx((st.latest_depart_s - st.depart_s) / 3600.0, rel=1e-9)
    assert st.margin_h >= 0.0
    assert st.gel_fresh_frac == 1.0
    assert cp.egress_zone_id == "z1"
    assert cp.committed_until_s == pytest.approx(NOW + cfg.commitment_horizon_min * 60.0)
    # objective = value*eff - travel cost (no churn without an incumbent)
    bd = plan.objective_breakdown
    assert bd["served"] == 1
    assert plan.objective == pytest.approx(bd["value_usd"] - bd["travel_cost_usd"], rel=1e-9)
    assert bd["value_usd"] == pytest.approx(1_000_000.0 * cfg.gel_effectiveness)


# ---------------------------------------------------------------------------
# 2-4. unserved reasons
# ---------------------------------------------------------------------------

def test_deadline_already_passed_is_infeasible_deadline():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 7), NOW + 1200.0, NOW + 2400.0)  # latest_depart < now
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.plans["A"].stops == []
    assert len(plan.unserved) == 1
    assert plan.unserved[0].reason == "infeasible_deadline"
    assert plan.unserved[0].client_id == "c1"


def test_corridor_blocked_is_no_safe_corridor():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, NOW + 300.0))}   # fire everywhere in 5 min
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 7), NOW + 6 * 3600.0, NOW + 8 * 3600.0)
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.plans["A"].stops == []
    assert [u.reason for u in plan.unserved] == ["no_safe_corridor"]


def test_egress_blocked_is_no_safe_egress():
    cfg = ResponseConfig()
    spec = spec20()
    early = NOW + 300.0
    data = [early if c >= 12 else UNREACHED
            for r in range(spec.nrows) for c in range(spec.ncols)]
    ens = {INC: make_ens(Raster(spec, data))}
    crew = make_crew("A", cc(spec, 10, 2))                     # safe west half
    job = make_job("c1", cc(spec, 10, 4), NOW + 8 * 3600.0, NOW + 10 * 3600.0)
    zone_in_fire = SafeZone("z1", cc(spec, 10, 16))            # only zone, inside fire path
    plan = dispatch.plan([crew], [job], ens, [zone_in_fire], cfg, NOW)
    assert plan.plans["A"].stops == []
    assert [u.reason for u in plan.unserved] == ["no_safe_egress"]


def test_no_eligible_crews_is_capacity():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    job = make_job("c1", cc(spec, 10, 4), NOW + 8 * 3600.0, NOW + 10 * 3600.0)
    off = Crew(crew_id="A", status=CrewStatus.OFF_DUTY, home_base=cc(spec, 10, 2))
    plan = dispatch.plan([off], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.plans == {}
    assert [u.reason for u in plan.unserved] == ["capacity"]


# ---------------------------------------------------------------------------
# 5. two crews / four jobs: all served + THE INVARIANT on every stop
# ---------------------------------------------------------------------------

def _four_job_scenario():
    cfg = ResponseConfig()
    spec = spec20()
    raster = uniform_raster(spec, NOW + 12 * 3600.0)           # finite, late arrival
    ens = {INC: make_ens(raster)}
    crews = [make_crew("A", cc(spec, 10, 2)), make_crew("B", cc(spec, 14, 2))]
    p10 = {"a1": NOW + 5 * 3600.0, "a2": NOW + 6 * 3600.0,
           "b1": NOW + 5.5 * 3600.0, "b2": NOW + 7 * 3600.0}
    jobs = [
        make_job("a1", cc(spec, 10, 4), p10["a1"], p10["a1"] + 7200.0),
        make_job("a2", cc(spec, 10, 6), p10["a2"], p10["a2"] + 7200.0),
        make_job("b1", cc(spec, 14, 4), p10["b1"], p10["b1"] + 7200.0),
        make_job("b2", cc(spec, 14, 6), p10["b2"], p10["b2"] + 7200.0),
    ]
    zones = [SafeZone("z1", cc(spec, 12, 1))]
    return cfg, spec, raster, ens, crews, jobs, zones, p10


def test_two_crews_four_jobs_all_served_with_safety_invariant():
    cfg, spec, raster, ens, crews, jobs, zones, p10 = _four_job_scenario()
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)

    assert plan.unserved == []
    served = stops_by_client(plan)
    assert set(served) == {"a1", "a2", "b1", "b2"}
    eroded = safety._erode(raster, cfg.corridor_erosion_m)
    client_loc = {j.client.client_id: j.client.loc for j in jobs}
    for cid, st in served.values():
        # crew clear >= 30 min (egress buffer) before pessimistic arrival
        assert st.depart_s <= p10[st.client_id] - cfg.egress_buffer_min * 60.0 + 1e-6
        assert st.margin_h >= 0.0
        assert st.latest_depart_s == pytest.approx(p10[st.client_id] - 1800.0)
        # THE INVARIANT: departing at depart_s, the egress corridor to a safe
        # zone clears the eroded P10 arrival with the route margin
        v = safety.egress_ok(client_loc[st.client_id], st.depart_s, zones, eroded, cfg)
        assert v.safe
        assert v.worst_margin_h >= cfg.route_margin_min / 60.0
        assert st.gel_fresh_frac == 1.0
    # timelines are internally consistent per crew
    for cid, cp in plan.plans.items():
        crew = [c for c in crews if c.crew_id == cid][0]
        loc, t = crew.loc(), NOW
        for st in cp.stops:
            tt = travel.travel_time_s(loc, client_loc[st.client_id], cfg)
            assert st.arrive_s == pytest.approx(t + tt, rel=1e-9)
            assert st.service_start_s >= st.arrive_s - 1e-6
            assert st.depart_s == pytest.approx(st.service_start_s + 1800.0, rel=1e-9)
            loc, t = client_loc[st.client_id], st.depart_s


# ---------------------------------------------------------------------------
# 6. regret-2 saves the tighter-deadline job when capacity forces a choice
# ---------------------------------------------------------------------------

def test_regret2_prefers_tighter_deadline_under_capacity():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew_loc = cc(spec, 10, 2)
    tight_loc, loose_loc = cc(spec, 10, 7), cc(spec, 9, 7)
    crew = make_crew("A", crew_loc)
    tt_tight = travel.travel_time_s(crew_loc, tight_loc, cfg)
    tt_loose = travel.travel_time_s(crew_loc, loose_loc, cfg)
    deploy_s = cfg.deploy_min * 60.0
    egress_s = cfg.egress_buffer_min * 60.0
    # both jobs only fit as a FIRST stop (equal value): 20/25 min slack —
    # above the Sprint 2 slack_min=15 floor (SAFETY SEMANTICS 7) so the test
    # still isolates the regret-2 capacity choice (second-stop fits still
    # fail on the HARD deadline, exactly as in the original 5/10 min version)
    tight_latest_depart = NOW + tt_tight + deploy_s + 1200.0
    loose_latest_depart = NOW + tt_loose + deploy_s + 1500.0
    tight = make_job("tight", tight_loc, tight_latest_depart + egress_s,
                     tight_latest_depart + egress_s + 7200.0)
    loose = make_job("loose", loose_loc, loose_latest_depart + egress_s,
                     loose_latest_depart + egress_s + 7200.0)
    zones = [SafeZone("z1", cc(spec, 10, 9))]

    plan = dispatch.plan([crew], [loose, tight], ens, zones, cfg, NOW)

    served = stops_by_client(plan)
    assert set(served) == {"tight"}
    assert [(u.client_id, u.reason) for u in plan.unserved] == [("loose", "capacity")]
    assert served["tight"][1].margin_h >= 0.0


# ---------------------------------------------------------------------------
# 7. frozen (committed) stop is never reassigned on replan
# ---------------------------------------------------------------------------

def _frozen_scenario(cfg):
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    client_loc = cc(spec, 10, 12)
    crew_a = make_crew("A", cc(spec, 10, 2))      # ~10 km away
    crew_b = make_crew("B", cc(spec, 10, 11))     # ~1 km away
    job = make_job("c1", client_loc, NOW + 4 * 3600.0, NOW + 6 * 3600.0)
    zones = [SafeZone("z1", cc(spec, 8, 12))]
    return spec, ens, [crew_a, crew_b], [job], zones


def _incumbent_on_a(service_start_s):
    stop = PlannedStop(client_id="c1", incident_id=INC, arrive_s=service_start_s,
                       service_start_s=service_start_s,
                       depart_s=service_start_s + 1800.0,
                       latest_depart_s=NOW + 4 * 3600.0 - 1800.0, margin_h=2.0)
    return DispatchPlan(computed_at_s=NOW - 900.0,
                        plans={"A": CrewPlan(crew_id="A", stops=[stop])})


def test_frozen_stop_not_reassigned():
    # churn/adoption disabled so ONLY the freeze can hold the assignment
    cfg = ResponseConfig(churn_penalty_frac=0.0, adoption_threshold_frac=0.0)
    spec, ens, crews, jobs, zones = _frozen_scenario(cfg)

    # sanity: without an incumbent the closer crew B wins the job
    free = dispatch.plan(crews, [jobs[0]], ens, zones, cfg, NOW)
    assert set(st.client_id for st in free.plans["B"].stops) == {"c1"}

    # incumbent has A committed (service_start within commitment horizon)
    spec, ens, crews, jobs, zones = _frozen_scenario(cfg)
    incumbent = _incumbent_on_a(service_start_s=NOW + 600.0)   # < now + 20 min
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW, incumbent=incumbent)
    assert [st.client_id for st in plan.plans["A"].stops] == ["c1"]
    assert plan.plans["B"].stops == []
    assert plan.unserved == []


# ---------------------------------------------------------------------------
# 8. churn: a near-tie alternative does NOT displace the incumbent assignment
# ---------------------------------------------------------------------------

def _churn_scenario():
    cfg = ResponseConfig()   # churn 5%, adoption 3%
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    client_loc = cc(spec, 10, 10)
    crew_a = make_crew("A", cc(spec, 10, 5))      # ~5 km
    crew_b = make_crew("B", cc(spec, 10, 14))     # ~4 km (slightly closer)
    job = make_job("c1", client_loc, NOW + 4 * 3600.0, NOW + 6 * 3600.0)
    zones = [SafeZone("z1", cc(spec, 8, 10))]
    return cfg, ens, [crew_a, crew_b], [job], zones


def test_churn_near_tie_keeps_incumbent_crew():
    cfg, ens, crews, jobs, zones = _churn_scenario()
    # sanity: from scratch, B (closer) would win
    free = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
    assert set(st.client_id for st in free.plans["B"].stops) == {"c1"}

    cfg, ens, crews, jobs, zones = _churn_scenario()
    incumbent = _incumbent_on_a(service_start_s=NOW + 5000.0)  # NOT frozen
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW, incumbent=incumbent)
    assert [st.client_id for st in plan.plans["A"].stops] == ["c1"]
    assert plan.plans["B"].stops == []
    assert plan.churn_from_incumbent == 0.0


# ---------------------------------------------------------------------------
# 9-10. gel window semantics
# ---------------------------------------------------------------------------

def test_service_never_starts_before_gel_window_opens():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    # fire is far out: window opens at p50 - 72 h = now + 8 h
    p10, p50 = NOW + 76 * 3600.0, NOW + 80 * 3600.0
    job = make_job("c1", cc(spec, 10, 7), p10, p50)
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)

    st = plan.plans["A"].stops[0]
    earliest = p50 - cfg.gel_window_h * 3600.0
    assert st.service_start_s >= earliest - 1e-6
    # GraphGel flat-effectiveness model (product data 2026-07-11): the DRIED
    # coating is the protection (full_strength == window, no decay ramp), so
    # the late-shift polish is a structural no-op — service starts the moment
    # the window opens, departs after deploy, at full effectiveness.
    assert st.service_start_s == pytest.approx(earliest, rel=1e-9)
    assert st.depart_s == pytest.approx(earliest + cfg.deploy_min * 60.0, rel=1e-9)
    assert st.gel_fresh_frac == 1.0
    assert st.depart_s <= st.latest_depart_s
    assert st.margin_h >= 0.0


def test_env_model_flag_shrinks_gel_window_in_dispatch():
    spec = spec20()
    crew_loc, client_loc = cc(spec, 10, 2), cc(spec, 10, 7)
    p10, p50 = NOW + 9 * 3600.0, NOW + 10 * 3600.0

    def run(cfg):
        ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
        crew = make_crew("A", crew_loc)
        job = make_job("c1", client_loc, p10, p50)
        return dispatch.plan([crew], [job], ens,
                             [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)

    st_off = run(ResponseConfig()).plans["A"].stops[0]
    st_on = run(ResponseConfig(gel_env_model_enabled=True)).plans["A"].stops[0]
    # default 72 h window opened long ago: service on arrival
    tt = travel.travel_time_s(crew_loc, client_loc, ResponseConfig())
    assert st_off.service_start_s == pytest.approx(NOW + tt, rel=1e-9)
    # env model (reference conditions, no forecast): 6 h coating life =>
    # window opens at p50 - 6 h = now + 4 h; service waits for it
    assert st_on.service_start_s == pytest.approx(p50 - 6.0 * 3600.0, rel=1e-9)
    assert st_on.service_start_s > st_off.service_start_s
    assert st_on.gel_fresh_frac == 1.0
    assert st_on.margin_h >= 0.0


# ---------------------------------------------------------------------------
# robustness: stale incumbents, unreachable fires, multi-incident corridors
# ---------------------------------------------------------------------------

def test_reassignment_from_vanished_crew_is_not_churn():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 7), NOW + 5 * 3600.0, NOW + 6 * 3600.0)
    ghost_stop = PlannedStop(client_id="c1", incident_id=INC, arrive_s=NOW,
                             service_start_s=NOW + 5000.0, depart_s=NOW + 6800.0,
                             latest_depart_s=NOW + 5 * 3600.0 - 1800.0, margin_h=2.0)
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"GONE": CrewPlan(crew_id="GONE", stops=[ghost_stop])})
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))],
                         cfg, NOW, incumbent=incumbent)
    assert [st.client_id for st in plan.plans["A"].stops] == ["c1"]
    assert plan.churn_from_incumbent == 0.0   # forced move, not churn


def test_unreached_p50_is_infeasible_deadline():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 7), UNREACHED, UNREACHED)
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.plans["A"].stops == []
    assert [u.reason for u in plan.unserved] == ["infeasible_deadline"]


def test_leg_checked_against_every_incident_raster():
    cfg = ResponseConfig()
    spec = spec20()
    ens = {
        "inc1": make_ens(uniform_raster(spec, UNREACHED)),
        "inc2": EnsembleArrival(spec=spec, members=[],
                                p10=uniform_raster(spec, NOW + 300.0),
                                p50=uniform_raster(spec, NOW + 300.0),
                                p90=uniform_raster(spec, NOW + 300.0),
                                t0_s=NOW, incident_id="inc2"),
    }
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 7), NOW + 5 * 3600.0, NOW + 6 * 3600.0)  # inc1 job
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    # the second fire blocks the corridor even though the job belongs to inc1
    assert plan.plans["A"].stops == []
    assert [u.reason for u in plan.unserved] == ["no_safe_corridor"]


# ---------------------------------------------------------------------------
# 11. determinism
# ---------------------------------------------------------------------------

def _fingerprint(plan):
    crews = []
    for cid in sorted(plan.plans):
        cp = plan.plans[cid]
        crews.append((cid, cp.egress_zone_id,
                      tuple((s.client_id, s.incident_id, s.arrive_s, s.service_start_s,
                             s.depart_s, s.margin_h, s.gel_fresh_frac) for s in cp.stops)))
    unserved = tuple(sorted((u.client_id, u.incident_id, u.reason) for u in plan.unserved))
    return (tuple(crews), unserved, plan.objective, plan.churn_from_incumbent)


def test_plan_is_deterministic():
    cfg1, _s1, _r1, ens1, crews1, jobs1, zones1, _ = _four_job_scenario()
    p1 = dispatch.plan(crews1, jobs1, ens1, zones1, cfg1, NOW)
    cfg2, _s2, _r2, ens2, crews2, jobs2, zones2, _ = _four_job_scenario()
    p2 = dispatch.plan(crews2, jobs2, ens2, zones2, cfg2, NOW)
    assert _fingerprint(p1) == _fingerprint(p2)


# ---------------------------------------------------------------------------
# 12. Sprint 2 A6 — planned-slack floor (SAFETY SEMANTICS 7, Council-1
#     dispatch F2, docs/review/dispatch_alerts_review.md)
# ---------------------------------------------------------------------------

def _thin_slack_scenario(slack_s):
    """Single crew / single job where the only possible stop departs exactly
    slack_s seconds before latest_depart (gel window opened long ago, so
    service starts on arrival and depart = NOW + travel + deploy)."""
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew_loc, client_loc = cc(spec, 10, 2), cc(spec, 10, 7)
    crew = make_crew("A", crew_loc)
    cfg = ResponseConfig()
    tt = travel.travel_time_s(crew_loc, client_loc, cfg)
    depart = NOW + tt + cfg.deploy_min * 60.0
    latest_depart = depart + slack_s
    p10 = latest_depart + cfg.egress_buffer_min * 60.0
    job = make_job("c1", client_loc, p10, p10 + 3600.0)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    return crew, job, ens, zones


def test_slack_floor_rejects_12min_slack_at_default_15():
    """A stop that FITS the deadline but carries only 12 min of slack is
    infeasible at the default slack_min=15 (SAFETY SEMANTICS 7); unserved
    reason is 'infeasible_slack', not 'infeasible_deadline'.

    Council-1 dispatch F2: EXP1 observed planned stops with margin_h=0.17 h
    (10.2 min) while the travel model is -21..-33% optimistic on mountain
    roads (scratch_review/da_exp6_travel.py)."""
    crew, job, ens, zones = _thin_slack_scenario(12 * 60.0)
    cfg = ResponseConfig()
    assert cfg.slack_min == 15.0                # contract default
    plan = dispatch.plan([crew], [job], ens, zones, cfg, NOW)
    assert plan.plans["A"].stops == []
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("c1", "infeasible_slack")]


def test_slack_floor_accepts_12min_slack_at_10():
    """The identical 12-min-slack stop is feasible when cfg.slack_min=10 —
    the floor is config, and margin_h keeps its raw-slack meaning."""
    crew, job, ens, zones = _thin_slack_scenario(12 * 60.0)
    cfg = ResponseConfig(slack_min=10.0)
    plan = dispatch.plan([crew], [job], ens, zones, cfg, NOW)
    assert plan.unserved == []
    st = plan.plans["A"].stops[0]
    assert st.margin_h == pytest.approx(12.0 / 60.0, rel=1e-6)
    assert st.margin_h * 60.0 >= cfg.slack_min


def test_slack_floor_holds_on_every_emitted_stop():
    """Multi-stop plan: every emitted stop carries >= slack_min of raw slack
    (margin_h * 60 >= cfg.slack_min) — the floor survives construction, local
    search, ILS and the late-shift polish."""
    cfg, spec, raster, ens, crews, jobs, zones, p10 = _four_job_scenario()
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
    assert plan.unserved == []
    stops = [st for cp in plan.plans.values() for st in cp.stops]
    assert stops
    for st in stops:
        assert st.margin_h * 60.0 >= cfg.slack_min - 1e-6


def test_late_shift_never_spends_the_slack_floor():
    """The late-gel polish wants depart = eta_p50 - gel_full_strength (which
    here would leave ZERO slack); the slack floor must cap it at
    latest_depart - slack_min*60.

    GraphGel's flat model (full_strength == window) makes the polish a no-op
    under product defaults, so this pins the MECHANISM under an explicit
    decaying-product config (gel_full_strength_h=24 < window 72) — the code
    path stays config-driven for wet-gel products."""
    cfg = ResponseConfig(gel_full_strength_h=24.0)
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    # wide p10/p50 spread: desired late-gel depart (p50 - 24 h = NOW + 2 h)
    # sits past latest_depart - slack (NOW + 1.5 h - 15 min)
    p10, p50 = NOW + 2 * 3600.0, NOW + 26 * 3600.0
    job = make_job("c1", cc(spec, 10, 7), p10, p50)
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.unserved == []
    st = plan.plans["A"].stops[0]
    latest = p10 - cfg.egress_buffer_min * 60.0
    assert st.latest_depart_s == pytest.approx(latest)
    assert st.depart_s == pytest.approx(latest - cfg.slack_min * 60.0, rel=1e-9)
    assert st.margin_h == pytest.approx(cfg.slack_min / 60.0, rel=1e-6)


# ---------------------------------------------------------------------------
# 13. Sprint 2 A7 — client-level job dedupe across incidents (Council-1
#     dispatch F4, docs/review/dispatch_alerts_review.md)
# ---------------------------------------------------------------------------

def _dup_client_jobs(spec, loc):
    """One client threatened by two incidents (INC-A worse: earlier p10/p50)."""
    def mk(inc, p10_s, p50_s):
        return DispatchJob(
            client=ClientProperty(client_id="c1", loc=loc, value_usd=2_000_000.0),
            incident_id=inc,
            eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s, eta_p90_s=p50_s + 3600.0))
    ja = mk("inc-a", NOW + 5 * 3600.0, NOW + 6 * 3600.0)
    jb = mk("inc-b", NOW + 7 * 3600.0, NOW + 8 * 3600.0)
    ens = {"inc-a": make_ens(uniform_raster(spec, UNREACHED)),
           "inc-b": make_ens(uniform_raster(spec, UNREACHED))}
    return ja, jb, ens


def test_client_deduped_across_incidents_one_stop_value_once():
    """One client under TWO incidents plans exactly ONE stop, counts the value
    ONCE, and uses the worst-case (earliest-p10) deadline.

    Port of scratch_review/da_exp4_multiincident.py part C (Council-1
    dispatch F4: the same house was gelled twice back-to-back — 60 min of
    crew time — and value_usd reported $3.4M for a $2.0M x 0.85 = $1.7M
    property, distorting adoption/churn/drop decisions downstream)."""
    cfg = ResponseConfig(dispatch_objective="value")   # asserts USD value_usd
    spec = spec20()
    loc = cc(spec, 10, 7)
    crew = make_crew("A", cc(spec, 10, 2))
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    ja, jb, ens = _dup_client_jobs(spec, loc)
    plan = dispatch.plan([crew], [ja, jb], ens, zones, cfg, NOW)

    stops = [st for cp in plan.plans.values() for st in cp.stops]
    assert len(stops) == 1                       # one house, one gel application
    st = stops[0]
    assert st.client_id == "c1"
    assert st.incident_id == "inc-a"             # worst case (earliest p10) survives
    assert st.latest_depart_s == pytest.approx(
        NOW + 5 * 3600.0 - cfg.egress_buffer_min * 60.0)
    bd = plan.objective_breakdown
    assert bd["served"] == 1
    # value counted ONCE: $2.0M x 0.85, not $3.4M
    assert bd["value_usd"] == pytest.approx(2_000_000.0 * cfg.gel_effectiveness)
    assert bd["dedup_dropped_jobs"] == 1
    # the collapsed duplicate is the SAME physical stop, not an unserved job
    assert plan.unserved == []


def test_client_dedupe_tie_breaks_on_earliest_p50():
    """Equal eta_p10: the job with the earlier eta_p50 survives (worst case on
    the tie-break too) — even when its incident_id sorts LAST."""
    cfg = ResponseConfig()
    spec = spec20()
    loc = cc(spec, 10, 7)
    crew = make_crew("A", cc(spec, 10, 2))
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    p10 = NOW + 6 * 3600.0

    def mk(inc, p50_s):
        return DispatchJob(
            client=ClientProperty(client_id="c1", loc=loc, value_usd=2_000_000.0),
            incident_id=inc,
            eta=ThreatEta(eta_p10_s=p10, eta_p50_s=p50_s, eta_p90_s=p50_s + 3600.0))

    j_keep = mk("inc-z", NOW + 7 * 3600.0)       # earlier p50, later-sorting id
    j_drop = mk("inc-a", NOW + 9 * 3600.0)
    ens = {"inc-a": make_ens(uniform_raster(spec, UNREACHED)),
           "inc-z": make_ens(uniform_raster(spec, UNREACHED))}
    plan = dispatch.plan([crew], [j_drop, j_keep], ens, zones, cfg, NOW)
    stops = [st for cp in plan.plans.values() for st in cp.stops]
    assert [st.incident_id for st in stops] == ["inc-z"]


def test_dedupe_plan_deterministic_regardless_of_input_order():
    """Same duplicate-client inputs in either order -> byte-identical plan
    (dedupe keys are stable; contracts.seeded_rng drives the shakes)."""
    cfg1 = ResponseConfig()
    spec = spec20()
    loc = cc(spec, 10, 7)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    ja1, jb1, ens1 = _dup_client_jobs(spec, loc)
    p1 = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [ja1, jb1], ens1,
                       zones, cfg1, NOW)
    cfg2 = ResponseConfig()
    ja2, jb2, ens2 = _dup_client_jobs(spec, loc)
    p2 = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [jb2, ja2], ens2,
                       zones, cfg2, NOW)
    assert _fingerprint(p1) == _fingerprint(p2)


# ---------------------------------------------------------------------------
# 14. Sprint 2 B1 — plan-level oracle consistency: everything the solver's
#     O(1) T* fast path admits must re-verify SAFE through the public
#     corridor_safe / egress_ok seams (Council-1 perf D1).
# ---------------------------------------------------------------------------

def test_emitted_plan_reverifies_through_public_corridor_seams():
    """Every emitted leg (ingress from the previous location at its actual
    leg-departure time) and every stop's egress re-verify SAFE through the
    public safety seams — the B1 per-leg T* fast path must never admit a stop
    the sampled verdict rejects."""
    cfg, spec, raster, ens, crews, jobs, zones, p10 = _four_job_scenario()
    plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
    assert plan.unserved == []
    eroded = safety._erode(raster, cfg.corridor_erosion_m)
    speed = safety._corridor_speed_mps(cfg)
    client_loc = {j.client.client_id: j.client.loc for j in jobs}
    n_stops = 0
    for cid, cp in plan.plans.items():
        crew = [c for c in crews if c.crew_id == cid][0]
        loc, t = crew.loc(), NOW
        for st in cp.stops:
            v = safety.corridor_safe(loc, client_loc[st.client_id], t,
                                     speed, eroded, cfg)
            assert v.safe, v.reason
            ve = safety.egress_ok(client_loc[st.client_id], st.depart_s,
                                  zones, eroded, cfg)
            assert ve.safe, ve.reason
            loc, t = client_loc[st.client_id], st.depart_s
            n_stops += 1
    assert n_stops == 4


# ---------------------------------------------------------------------------
# 15. Sprint 2 B2 — multi-start construction + never-worse-than-greedy
#     guardrail (Council-1 perf D2 + dispatch F3; docs/review/perf_review.md
#     section 3.3, docs/review/dispatch_alerts_review.md F3).
# ---------------------------------------------------------------------------

_EARTH_R_M = 6_371_000.0


def _dest(origin, bearing_deg, dist_m):
    """Spherical forward solution (local copy — same formula as
    scratch_review/da_common.destination; tests stay geo.py-independent)."""
    br = math.radians(bearing_deg)
    la1, lo1 = math.radians(origin.lat), math.radians(origin.lon)
    ang = dist_m / _EARTH_R_M
    la2 = math.asin(math.sin(la1) * math.cos(ang)
                    + math.cos(la1) * math.sin(ang) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(ang) * math.cos(la1),
                           math.cos(ang) - math.sin(la1) * math.sin(la2))
    return LatLon(math.degrees(la2), math.degrees(lo2))


def _far_unreached_ens():
    """Ensemble raster 100+ km from any test geometry, all UNREACHED — every
    corridor/egress check trivially safe (same trick as
    scratch_review/da_common.all_safe_ensemble)."""
    spec = GridSpec(north_lat=44.0, west_lon=-100.0, cell_m=1000.0, nrows=2, ncols=2)
    return {INC: make_ens(Raster(spec, [UNREACHED] * 4))}


def test_guardrail_value_floor_serves_job_regret2_skips():
    """A far-away tiny-value job is J-NEGATIVE to insert (score*frac <
    lambda_travel * extra drive-hours), so regret-2 legitimately skips it —
    but the greedy-EDF guardrail serves everything feasible, and ticket B2
    guarantees the emitted plan never serves less VALUE than raw greedy-EDF.
    The tiny job must appear in the final plan via the value floor."""
    # value mode: 'homes' would serve the tiny job outright (count-first, no
    # value-floor guardrail needed) -- covered by test_homes_serves_tiny_job.
    cfg = ResponseConfig(dispatch_objective="value")
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    big = make_job("big", cc(spec, 10, 4), NOW + 8 * 3600.0, NOW + 10 * 3600.0,
                   value=2_000_000.0)
    tiny = make_job("tiny", cc(spec, 10, 17), NOW + 10 * 3600.0, NOW + 12 * 3600.0,
                    value=1_000.0)
    zones = [SafeZone("z1", cc(spec, 10, 9))]

    plan = dispatch.plan([crew], [big, tiny], ens, zones, cfg, NOW)

    served = stops_by_client(plan)
    assert set(served) == {"big", "tiny"}
    assert plan.unserved == []
    bd = plan.objective_breakdown
    assert bd["candidate"] == "greedy_edf"
    assert bd["guardrail"] == "value"
    assert bd["value_usd"] == pytest.approx(
        (2_000_000.0 + 1_000.0) * cfg.gel_effectiveness)


def test_guardrail_adoption_respects_frozen_commitment():
    """When the guardrail adopts the greedy-EDF candidate, frozen (committed)
    incumbent stops are still present, first, and on their incumbent crew —
    every construction seed builds on the frozen-committed skeleton."""
    # value mode: relies on the greedy-EDF value guardrail adopting the tiny job
    # (regret-2 skips it as J-negative); the frozen-commitment invariant it
    # checks is mode-independent.
    cfg = ResponseConfig(dispatch_objective="value",
                         churn_penalty_frac=0.0, adoption_threshold_frac=0.0)
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    big = make_job("big", cc(spec, 10, 4), NOW + 8 * 3600.0, NOW + 10 * 3600.0,
                   value=2_000_000.0)
    tiny = make_job("tiny", cc(spec, 10, 17), NOW + 10 * 3600.0, NOW + 12 * 3600.0,
                    value=1_000.0)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    committed = PlannedStop(client_id="big", incident_id=INC, arrive_s=NOW + 300.0,
                            service_start_s=NOW + 600.0, depart_s=NOW + 2400.0,
                            latest_depart_s=NOW + 8 * 3600.0 - 1800.0, margin_h=5.0)
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"A": CrewPlan(crew_id="A", stops=[committed])})

    plan = dispatch.plan([crew], [big, tiny], ens, zones, cfg, NOW,
                         incumbent=incumbent)

    assert plan.objective_breakdown["candidate"] == "greedy_edf"
    assert [st.client_id for st in plan.plans["A"].stops] == ["big", "tiny"]
    assert plan.unserved == []


def _tight_instance(i):
    """Seeded 3-crew x 10-job tight-deadline instance (the Council-1 EXP1
    regime — scratch_review/da_exp1_optgap.py — at slightly larger scale) with
    trivially safe corridors: isolates routing/deadline/value behavior."""
    rng = seeded_rng("b2-guardrail", i)
    center = LatLon(34.2, -117.6)
    crews = [make_crew(f"G{k + 1}", _dest(center, rng.uniform(0, 360),
                                          rng.uniform(3000, 12000)))
             for k in range(3)]
    jobs = []
    for j in range(10):
        loc = _dest(center, rng.uniform(0, 360), rng.uniform(2000, 14000))
        p10 = NOW + rng.uniform(1.2, 5.0) * 3600.0
        p50 = p10 + rng.uniform(0.3, 1.5) * 3600.0
        jobs.append(make_job(f"J{j + 1:02d}", loc, p10, p50,
                             value=rng.uniform(600_000.0, 3_500_000.0),
                             weight=2.0 if rng.random() < 0.25 else 1.0))
    zones = [SafeZone("Z1", _dest(center, 270.0, 25_000.0)),
             SafeZone("Z2", _dest(center, 180.0, 25_000.0))]
    return crews, jobs, _far_unreached_ens(), zones


def _edf_baseline(crews, jobs, cfg):
    """The review's greedy EDF baseline (append-only, earliest feasible depart,
    ties by crew id) under TODAY's feasibility chain — deadline + slack floor
    via dispatch._depart_fail. Corridors in these instances are trivially
    safe. Jobs must have derived windows. Returns (served, value_usd)."""
    state = {c.crew_id: (c.loc(), NOW) for c in crews}
    served, value = 0, 0.0
    for job in sorted(jobs, key=lambda j: (j.latest_service_start_s,
                                           (j.client.client_id, j.incident_id))):
        best = None
        for cid in sorted(state):
            loc, t = state[cid]
            tt = travel.travel_time_s(loc, job.client.loc, cfg)
            arrive = t + tt
            service = arrive if job.earliest_useful_start_s <= arrive \
                else job.earliest_useful_start_s
            depart = service + cfg.deploy_min * 60.0
            if dispatch._depart_fail(depart, job.latest_depart_s, cfg) is not None:
                continue
            if best is None or (depart, cid) < best:
                best = (depart, cid)
        if best is not None:
            depart, cid = best
            state[cid] = (job.client.loc, depart)
            served += 1
            value += (job.client.priority_weight * job.client.value_usd
                      * dispatch._gel_fresh_frac(job, depart, cfg)
                      * cfg.gel_effectiveness)
    return served, value


def test_never_worse_than_greedy_on_12_seeded_tight_instances():
    """Ticket B2 guarantee: emitted served value >= the greedy-EDF baseline on
    EVERY instance, by construction (Council-1 perf finding 2: the 5 s solver
    shipped 9x less value than its own 0.45 s greedy fallback at 20/200)."""
    for i in range(12):
        cfg = ResponseConfig(dispatch_objective="value")   # USD value floor
        cfg.solver_budget_s = 3.0
        crews, jobs, ens, zones = _tight_instance(i)
        plan = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
        crews2, jobs2, _e, _z = _tight_instance(i)
        for j in jobs2:
            safety.derive_job_windows(j, cfg)
        _g_served, g_value = _edf_baseline(crews2, jobs2, cfg)
        assert plan.objective_breakdown["value_usd"] >= g_value - 1e-6, \
            f"instance {i}: plan value < greedy value"


def test_multistart_plan_is_deterministic():
    """Multi-start seeds, guardrail comparisons and ILS keep the determinism
    contract: identical inputs -> identical plans (stable tie-breaks;
    contracts.seeded_rng drives the shakes)."""
    cfg1 = ResponseConfig()
    cfg1.solver_budget_s = 3.0
    crews1, jobs1, ens1, zones1 = _tight_instance(3)
    p1 = dispatch.plan(crews1, jobs1, ens1, zones1, cfg1, NOW)
    cfg2 = ResponseConfig()
    cfg2.solver_budget_s = 3.0
    crews2, jobs2, ens2, zones2 = _tight_instance(3)
    p2 = dispatch.plan(crews2, jobs2, ens2, zones2, cfg2, NOW)
    assert _fingerprint(p1) == _fingerprint(p2)


# ---------------------------------------------------------------------------
# 16. Sprint 2 B2 acceptance — optimality-gap harness, ported from
#     scratch_review/da_exp1_optgap.py (Council-1 dispatch F3). The brute
#     force honors TODAY's feasibility semantics (slack floor included), so
#     the optimum is the true optimum of the problem plan() actually solves.
#     Gate: mean gap <= 1%, ZERO served-count misses (the review's 2/24
#     structural misses — instances 11 and 14, one unprotected home each —
#     must be gone; the greedy-EDF seed covers them).
# ---------------------------------------------------------------------------

def _optgap_instance(i):
    """VERBATIM port of scratch_review/da_exp1_optgap.build_instance (same
    seed stream 'da_exp1', same draw order): 2 crews x 5-7 jobs, tight
    deadlines, corridors trivially safe, gel frac pinned at 1.0 by design."""
    rng = seeded_rng("da_exp1", i)
    center = LatLon(34.2000, -117.6000)
    n_jobs = 5 + (i % 3)
    crews = []
    for k in range(2):
        loc = _dest(center, rng.uniform(0, 360), rng.uniform(4000, 14000))
        crews.append(make_crew(f"G{k + 1}", loc))
    jobs = []
    for j in range(n_jobs):
        loc = _dest(center, rng.uniform(0, 360), rng.uniform(2000, 12000))
        p10 = NOW + rng.uniform(1.0, 3.2) * 3600.0
        p50 = p10 + rng.uniform(0.3, 1.5) * 3600.0
        jobs.append(make_job(f"J{j + 1:02d}", loc, p10, p50,
                             value=rng.uniform(800_000.0, 3_500_000.0),
                             weight=2.0 if rng.random() < 0.25 else 1.0))
    zones = [SafeZone("Z1", _dest(center, 270.0, 20_000.0)),
             SafeZone("Z2", _dest(center, 180.0, 20_000.0))]
    return crews, jobs, zones


def _optgap_prune(crews, jobs, cfg):
    """Replicates plan() step-1 pruning + lambda_travel derivation exactly
    (port of da_exp1_optgap.prune_and_lambda)."""
    jobmap = {}
    for job in sorted(jobs, key=lambda j: (j.client.client_id, j.incident_id)):
        key = (job.client.client_id, job.incident_id)
        if key in jobmap:
            continue
        safety.derive_job_windows(job, cfg)
        min_tt = min(travel.travel_time_s(c.loc(), job.client.loc, cfg)
                     for c in crews)
        if (job.earliest_useful_start_s == UNREACHED
                or job.earliest_useful_start_s > job.latest_service_start_s + 1e-6
                or NOW + min_tt > job.latest_service_start_s + 1e-6):
            continue
        jobmap[key] = job
    scores = [j.client.priority_weight * j.client.value_usd * cfg.gel_effectiveness
              for j in jobmap.values()]
    mean_score = (sum(scores) / len(scores)) if scores else 1.0
    return jobmap, 0.01 * mean_score


def _crew_best_by_set(crew, jobmap, cfg):
    """DFS over ALL ordered feasible routes for one crew under the CURRENT
    feasibility chain (deadline + slack floor via dispatch._depart_fail); per
    served frozenset keep the MIN total drive_h (value per set is constant:
    gel frac == 1 by instance design)."""
    keys = sorted(jobmap)
    best = {frozenset(): 0.0}

    def dfs(loc, t, served, drive_h):
        for key in keys:
            if key in served:
                continue
            job = jobmap[key]
            tt = travel.travel_time_s(loc, job.client.loc, cfg)
            arrive = t + tt
            service = arrive if job.earliest_useful_start_s <= arrive \
                else job.earliest_useful_start_s
            depart = service + cfg.deploy_min * 60.0
            if dispatch._depart_fail(depart, job.latest_depart_s, cfg) is not None:
                continue
            dt_h = (job.eta.eta_p50_s - depart) / 3600.0
            assert dt_h <= cfg.gel_full_strength_h + 1e-9, "gel-frac-1 design violated"
            ns = served | {key}
            nd = drive_h + tt / 3600.0
            fs = frozenset(ns)
            if fs not in best or nd < best[fs]:
                best[fs] = nd
            dfs(job.client.loc, depart, ns, nd)

    dfs(crew.loc(), NOW, set(), 0.0)
    return best


def _brute_force_opt(crews, jobmap, lam, cfg):
    """True optimum over all (subset, ordered route) splits across 2 crews."""
    score = {k: j.client.priority_weight * j.client.value_usd * cfg.gel_effectiveness
             for k, j in jobmap.items()}
    b1 = _crew_best_by_set(crews[0], jobmap, cfg)
    b2 = _crew_best_by_set(crews[1], jobmap, cfg)
    best_j, best_served = 0.0, 0
    for s1, d1 in b1.items():
        for s2, d2 in b2.items():
            if s1 & s2:
                continue
            val = sum(score[k] for k in s1) + sum(score[k] for k in s2)
            j = val - lam * (d1 + d2)
            if j > best_j + 1e-9 or (abs(j - best_j) <= 1e-9
                                     and len(s1) + len(s2) > best_served):
                best_j, best_served = j, len(s1) + len(s2)
    return best_j, best_served


def test_optimality_gap_24_instances_mean_le_1pct_no_serve_miss():
    # value mode: the brute force honors the USD objective; homes mode is
    # re-baselined (count-optimal + reported value) in
    # test_homes_optimality_24_instances_no_count_miss below.
    gaps = []
    misses = []
    for i in range(24):
        cfg = ResponseConfig(dispatch_objective="value")
        cfg.solver_budget_s = 3.0
        crews, jobs, _zones = _optgap_instance(i)
        jobmap, lam = _optgap_prune(crews, jobs, cfg)
        opt_j, opt_served = _brute_force_opt(crews, jobmap, lam, cfg)
        # rebuild fresh jobs for plan() (derive_job_windows mutates)
        crews2, jobs2, zones2 = _optgap_instance(i)
        plan = dispatch.plan(crews2, jobs2, _far_unreached_ens(), zones2, cfg, NOW)
        # sanity: the brute force really is an upper bound on the identical objective
        assert plan.objective <= opt_j + max(1e-6, 1e-9 * abs(opt_j))
        for cp in plan.plans.values():
            for st in cp.stops:
                assert abs(st.gel_fresh_frac - 1.0) < 1e-9, "gel frac drifted from design"
        served = plan.objective_breakdown["served"]
        if opt_j > 0:
            gaps.append(100.0 * (opt_j - plan.objective) / opt_j)
        if served < opt_served:
            misses.append(i)
    assert misses == [], f"served-count misses vs optimum on instances {misses}"
    mean_gap = sum(gaps) / len(gaps)
    assert mean_gap <= 1.0, f"mean optimality gap {mean_gap:.3f}% > 1%"


# ---------------------------------------------------------------------------
# 17. Sprint 2 C5 — weather-aware gel windows at plan time (Council-1
#     dispatch F5, docs/review/dispatch_alerts_review.md: plan() derived
#     windows WEATHERLESS, so the env model always used its 6.0 h reference
#     while real fire weather gives 1.0-1.5 h; gel could be expired at fire
#     arrival while the plan reported gel_fresh_frac=1.0).
# ---------------------------------------------------------------------------

def _wx_forecast(temp_c, rh_pct, wind_mps, t0=NOW, hours=24):
    """Constant-conditions hourly forecast (contracts.WeatherForecast)."""
    return WeatherForecast(
        loc=LatLon(34.1, -117.2),
        samples=[WeatherSample(t_s=t0 + h * 3600.0, wind_speed_mps=wind_mps,
                               wind_gust_mps=wind_mps + 2.0, wind_dir_deg=0.0,
                               rh_pct=rh_pct, temp_c=temp_c)
                 for h in range(hours)])


# The review's canonical fire-weather point: 33 C / 12 %RH / 8 m/s -> ~1.47 h
# coating life (asserted against safety.gel_life_h inside each test).
_HOT = (33.0, 12.0, 8.0)


def test_forecast_shrinks_gel_window_and_shifts_service_later():
    """Ticket C5 acceptance: with gel_env_model_enabled and a hot/windy
    incident forecast threaded through plan(..., forecasts=...), the
    effective window shrinks 72 h -> 6 h (reference) -> 1.0-1.5 h (fire
    weather) and service_start shifts correspondingly later — while the stop
    stays lawful (slack floor held, full gel strength at arrival)."""
    spec = spec20()
    crew_loc, client_loc = cc(spec, 10, 2), cc(spec, 10, 7)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    p10, p50 = NOW + 9 * 3600.0, NOW + 9.1 * 3600.0   # sharp forecast (0.1 h)

    def run(cfg, forecasts=None):
        ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
        job = make_job("c1", client_loc, p10, p50)
        plan = dispatch.plan([make_crew("A", crew_loc)], [job], ens, zones,
                             cfg, NOW, forecasts=forecasts)
        return plan, job

    plan_off, job_off = run(ResponseConfig())
    plan_ref, job_ref = run(ResponseConfig(gel_env_model_enabled=True))
    plan_hot, job_hot = run(ResponseConfig(gel_env_model_enabled=True),
                            forecasts={INC: _wx_forecast(*_HOT)})
    for p in (plan_off, plan_ref, plan_hot):
        assert p.unserved == []
    st_off = plan_off.plans["A"].stops[0]
    st_ref = plan_ref.plans["A"].stops[0]
    st_hot = plan_hot.plans["A"].stops[0]

    life_h = safety.gel_life_h(*_HOT)
    assert 1.0 <= life_h <= 1.5              # the review's fire-weather regime
    # effective windows recovered from the derived jobs (p50 - earliest_useful)
    assert (p50 - job_off.earliest_useful_start_s) / 3600.0 == pytest.approx(72.0)
    assert (p50 - job_ref.earliest_useful_start_s) / 3600.0 == pytest.approx(6.0)
    assert (p50 - job_hot.earliest_useful_start_s) / 3600.0 == pytest.approx(
        life_h, rel=1e-9)

    # service shifts later: on-arrival (72 h window opened long ago) ->
    # p50 - 6 h (reference) -> p50 - coating life (fire weather)
    cfg = ResponseConfig()
    tt = travel.travel_time_s(crew_loc, client_loc, cfg)
    assert st_off.service_start_s == pytest.approx(NOW + tt, rel=1e-9)
    assert st_ref.service_start_s == pytest.approx(p50 - 6.0 * 3600.0, rel=1e-9)
    assert st_hot.service_start_s == pytest.approx(p50 - life_h * 3600.0, rel=1e-9)
    assert st_hot.service_start_s > st_ref.service_start_s > st_off.service_start_s
    # lawful stop: slack floor held, gel at full strength at median arrival
    assert st_hot.depart_s <= st_hot.latest_depart_s - cfg.slack_min * 60.0 + 1e-6
    assert st_hot.margin_h * 60.0 >= cfg.slack_min - 1e-6
    assert st_hot.gel_fresh_frac == 1.0


def test_wide_spread_under_fire_weather_is_gel_window_closed():
    """A 3 h p50-p10 forecast spread exceeds the ~1.47 h fire-weather life
    minus the egress+deploy buffers -> structurally unservable, reported with
    the DISTINCT reason 'gel_window_closed' (not 'infeasible_deadline'). The
    same job is servable at the 6 h no-forecast reference (the closure is
    weather-caused — exactly the F5 plumbing gap) and under the flag-OFF
    72 h placeholder."""
    spec = spec20()
    crew_loc, client_loc = cc(spec, 10, 2), cc(spec, 10, 7)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    p10, p50 = NOW + 9 * 3600.0, NOW + 12 * 3600.0    # 3 h spread

    def run(cfg, forecasts=None):
        ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
        return dispatch.plan([make_crew("A", crew_loc)],
                             [make_job("c1", client_loc, p10, p50)],
                             ens, zones, cfg, NOW, forecasts=forecasts)

    plan_hot = run(ResponseConfig(gel_env_model_enabled=True),
                   forecasts={INC: _wx_forecast(*_HOT)})
    assert plan_hot.plans["A"].stops == []
    assert [(u.client_id, u.reason) for u in plan_hot.unserved] == \
        [("c1", "gel_window_closed")]
    # sanity: the spread really does exceed life - (egress+deploy) buffers
    life_h = safety.gel_life_h(*_HOT)
    assert (p50 - p10) / 3600.0 > life_h - 1.0
    # no forecast -> 6 h reference window -> servable (weather caused it)
    assert run(ResponseConfig(gel_env_model_enabled=True)).unserved == []
    # flag OFF -> 72 h placeholder -> servable (historical behavior)
    assert run(ResponseConfig(),
               forecasts={INC: _wx_forecast(*_HOT)}).unserved == []


def test_reference_window_closure_also_reports_gel_window_closed():
    """Even without a forecast, an env-model window closure (spread 7 h > the
    6 h reference minus buffers — the review's W06-W08 'window-caused
    unserved' cohort) reports 'gel_window_closed'."""
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    job = make_job("c1", cc(spec, 10, 7), NOW + 9 * 3600.0, NOW + 16 * 3600.0)
    plan = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [job], ens,
                         [SafeZone("z1", cc(spec, 10, 9))],
                         ResponseConfig(gel_env_model_enabled=True), NOW)
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("c1", "gel_window_closed")]


def test_pure_deadline_miss_stays_infeasible_deadline_under_env_model():
    """A job whose latest_service_start is already past is a crew-timing
    miss, NOT a window closure: reason stays 'infeasible_deadline' even with
    the env model on and a hot forecast supplied."""
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    job = make_job("c1", cc(spec, 10, 7), NOW + 1200.0, NOW + 2400.0)
    plan = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [job], ens,
                         [SafeZone("z1", cc(spec, 10, 9))],
                         ResponseConfig(gel_env_model_enabled=True), NOW,
                         forecasts={INC: _wx_forecast(*_HOT)})
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("c1", "infeasible_deadline")]


def test_unreached_p50_stays_infeasible_deadline_under_env_model():
    """eta_p50=UNREACHED (fire not forecast to arrive) keeps its historical
    'infeasible_deadline', never 'gel_window_closed' (there is no window to
    close — the opening itself is UNREACHED)."""
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    job = make_job("c1", cc(spec, 10, 7), UNREACHED, UNREACHED)
    plan = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [job], ens,
                         [SafeZone("z1", cc(spec, 10, 9))],
                         ResponseConfig(gel_env_model_enabled=True), NOW,
                         forecasts={INC: _wx_forecast(*_HOT)})
    assert [u.reason for u in plan.unserved] == ["infeasible_deadline"]


def _c5_pin_scenario():
    """Flag-OFF pin scenario with served AND unserved variety: the four-job
    core plus a past-deadline job ('late') and an empty-window job ('wide',
    p50-p10 = 78 h > the 72 h placeholder window — the one flag-OFF case the
    C5 reason logic could have disturbed)."""
    cfg, spec, raster, ens, crews, jobs, zones, _p10 = _four_job_scenario()
    jobs.append(make_job("late", cc(spec, 10, 8), NOW + 1200.0, NOW + 2400.0))
    jobs.append(make_job("wide", cc(spec, 12, 8), NOW + 2 * 3600.0,
                         NOW + 80 * 3600.0))
    return cfg, ens, crews, jobs, zones


def test_flag_off_plans_byte_identical_with_and_without_forecasts():
    """Ticket C5 pin: with gel_env_model_enabled=False (the shipped default)
    the forecast map is never read — plans are byte-identical whether the
    kwarg is omitted, None, or a full hot/windy map, INCLUDING the unserved
    reasons ('wide', the flag-OFF empty-window case, stays
    'infeasible_deadline')."""
    cfg1, ens1, crews1, jobs1, zones1 = _c5_pin_scenario()
    p_omit = dispatch.plan(crews1, jobs1, ens1, zones1, cfg1, NOW)
    cfg2, ens2, crews2, jobs2, zones2 = _c5_pin_scenario()
    p_none = dispatch.plan(crews2, jobs2, ens2, zones2, cfg2, NOW,
                           forecasts=None)
    cfg3, ens3, crews3, jobs3, zones3 = _c5_pin_scenario()
    p_hot = dispatch.plan(crews3, jobs3, ens3, zones3, cfg3, NOW,
                          forecasts={INC: _wx_forecast(*_HOT)})
    assert _fingerprint(p_omit) == _fingerprint(p_none) == _fingerprint(p_hot)
    reasons = dict((u.client_id, u.reason) for u in p_omit.unserved)
    assert reasons == {"late": "infeasible_deadline",
                       "wide": "infeasible_deadline"}
    assert p_omit.objective_breakdown["served"] == 4


def test_forecast_looked_up_per_incident():
    """forecasts is keyed by incident_id: a job under the hot incident gets
    the ~1.47 h fire-weather window while a job under an incident MISSING
    from the map derives at the 6 h no-forecast reference — in the same
    plan() call."""
    cfg = ResponseConfig(gel_env_model_enabled=True)
    spec = spec20()
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    p10, p50 = NOW + 9 * 3600.0, NOW + 9.1 * 3600.0

    def mk(client_id, loc, inc):
        return DispatchJob(
            client=ClientProperty(client_id=client_id, loc=loc),
            incident_id=inc,
            eta=ThreatEta(eta_p10_s=p10, eta_p50_s=p50, eta_p90_s=p50 + 3600.0))

    ja = mk("ca", cc(spec, 10, 7), "inc-a")
    jb = mk("cb", cc(spec, 12, 7), "inc-b")
    ens = {"inc-a": make_ens(uniform_raster(spec, UNREACHED)),
           "inc-b": make_ens(uniform_raster(spec, UNREACHED))}
    plan = dispatch.plan([make_crew("A", cc(spec, 10, 2))], [ja, jb], ens,
                         zones, cfg, NOW,
                         forecasts={"inc-a": _wx_forecast(*_HOT)})

    assert plan.unserved == []
    life_h = safety.gel_life_h(*_HOT)
    assert (p50 - ja.earliest_useful_start_s) / 3600.0 == pytest.approx(
        life_h, rel=1e-9)
    assert (p50 - jb.earliest_useful_start_s) / 3600.0 == pytest.approx(6.0)
    served = stops_by_client(plan)
    assert served["ca"][1].service_start_s == pytest.approx(
        p50 - life_h * 3600.0, rel=1e-9)
    assert served["cb"][1].service_start_s == pytest.approx(
        p50 - 6.0 * 3600.0, rel=1e-9)
    # both stops still honor the slack floor on the shared route
    for _cid, st in served.values():
        assert st.margin_h * 60.0 >= cfg.slack_min - 1e-6


# ---------------------------------------------------------------------------
# 18. FOUNDER ROUND 3 (contracts) — cfg.dispatch_objective 'homes' (default,
#     count-first TRIAGE law) vs 'value' (legacy $-weighted). Homes-first
#     objective + anti-concentration (FULL UTILIZATION law) + triage
#     abandonment ('triaged'). SAFETY SEMANTICS 2/3/7 are objective-independent
#     and byte-compared across modes.
# ---------------------------------------------------------------------------

def _big_spec():
    return GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                    nrows=40, ncols=60)


def _dilemma(cfg):
    """Deterministic 1-crew triage dilemma (port of scratch_review/
    diag_triage.py): a high-value home A ($5M) feasible-now near the crew XOR
    two low-value homes B,C ($0.7M) far the OPPOSITE way. The travel arithmetic
    makes {A} and {B,C} the only maximal feasible sets, so 'value' protects the
    single expensive home (1) and 'homes' protects the two cheaper ones (2).
    Corridors trivially safe (uniform UNREACHED), so only the objective binds.
    Returns (crew, jobs, ens, zones, geom) with geom = the incumbent-arm
    timings for the preemption test."""
    spec = _big_spec()
    crew_loc = cc(spec, 20, 30)
    A_loc, B_loc, C_loc = cc(spec, 20, 6), cc(spec, 20, 54), cc(spec, 21, 54)

    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)

    deploy_s = cfg.deploy_min * 60.0
    egr = cfg.egress_buffer_min * 60.0
    slack = cfg.slack_min * 60.0
    t_cA, t_cB, t_BC = tt(crew_loc, A_loc), tt(crew_loc, B_loc), tt(B_loc, C_loc)
    A_ld = NOW + t_cA + deploy_s + slack + 120.0
    A_p10 = A_ld + egr
    dB = NOW + t_cB + deploy_s
    B_ld = dB + slack + 120.0
    dC = dB + t_BC + deploy_s
    C_ld = dC + slack + 120.0
    # exclusivity, proven by construction (mirrors diag_triage asserts)
    assert NOW + t_cA + deploy_s + tt(A_loc, B_loc) + deploy_s > B_ld  # A-first blows B
    assert dC <= C_ld - slack + 1e-6                                   # direct B->C fits
    A = make_job("A_hi", A_loc, A_p10, A_p10 + 2 * 3600.0, value=5_000_000.0)
    B = make_job("B_lo", B_loc, B_ld + egr, B_ld + egr + 2 * 3600.0, value=700_000.0)
    C = make_job("C_lo", C_loc, C_ld + egr, C_ld + egr + 2 * 3600.0, value=700_000.0)
    crew = make_crew("G1", crew_loc)
    zones = [SafeZone("Z1", cc(spec, 20, 39))]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crew, [A, B, C], ens, zones, (t_cB, dB, B_ld, t_BC, deploy_s, dC, C_ld)


def test_homes_vs_value_dilemma_count_beats_dollars():
    """THE triage dilemma: {A=$5M} XOR {B,C=$0.7M}. 'value' protects the single
    $5M home (1); 'homes' protects the two $0.7M homes (2). Same instance, only
    cfg.dispatch_objective differs — the founder law made real."""
    crew_v, jobs_v, ens_v, zones_v, _ = _dilemma(ResponseConfig(dispatch_objective="value"))
    pv = dispatch.plan([crew_v], jobs_v, ens_v, zones_v,
                       ResponseConfig(dispatch_objective="value"), NOW)
    crew_h, jobs_h, ens_h, zones_h, _ = _dilemma(ResponseConfig(dispatch_objective="homes"))
    ph = dispatch.plan([crew_h], jobs_h, ens_h, zones_h,
                       ResponseConfig(dispatch_objective="homes"), NOW)
    assert set(stops_by_client(pv)) == {"A_hi"}
    assert pv.objective_breakdown["served"] == 1
    assert set(stops_by_client(ph)) == {"B_lo", "C_lo"}
    assert ph.objective_breakdown["served"] == 2


def test_homes_triaged_reason_for_skipped_home():
    """The skipped high-value home is reported 'triaged' in 'homes' mode
    (feasible on its own, but serving it would cost a NET home) — distinct from
    capacity / dropped_by_optimizer / the safety reasons. In 'value' mode the
    skipped B,C are a plain 'capacity' miss (never 'triaged')."""
    crew, jobs, ens, zones, _ = _dilemma(ResponseConfig(dispatch_objective="homes"))
    ph = dispatch.plan([crew], jobs, ens, zones,
                       ResponseConfig(dispatch_objective="homes"), NOW)
    assert {u.client_id: u.reason for u in ph.unserved} == {"A_hi": "triaged"}

    crewv, jobsv, ensv, zonesv, _ = _dilemma(ResponseConfig(dispatch_objective="value"))
    pv = dispatch.plan([crewv], jobsv, ensv, zonesv,
                       ResponseConfig(dispatch_objective="value"), NOW)
    rv = {u.client_id: u.reason for u in pv.unserved}
    assert rv == {"B_lo": "capacity", "C_lo": "capacity"}
    assert "triaged" not in set(rv.values())


def test_homes_preemption_keeps_two_committed_homes_over_one():
    """Objective-side preemption (contracts FOUNDER ROUND 3 #4): with an
    incumbent already committed to B+C (2 homes, NON-frozen so only the
    objective — not the freeze — can hold them) and the high-value A newly
    feasible, 'homes' does NOT drop the 2 committed homes for the 1 (the
    diagnosis's dropped_by_optimizer case) — A is 'triaged', churn stays 0."""
    cfg = ResponseConfig(dispatch_objective="homes")
    crew, jobs, ens, zones, geom = _dilemma(cfg)
    t_cB, dB, B_ld, t_BC, deploy_s, dC, C_ld = geom
    assert NOW + t_cB > NOW + cfg.commitment_horizon_min * 60.0   # B is NON-frozen
    inc_stops = [
        PlannedStop(client_id="B_lo", incident_id=INC, arrive_s=NOW + t_cB,
                    service_start_s=NOW + t_cB, depart_s=dB,
                    latest_depart_s=B_ld, margin_h=(B_ld - dB) / 3600.0),
        PlannedStop(client_id="C_lo", incident_id=INC, arrive_s=dB + t_BC,
                    service_start_s=dB + t_BC, depart_s=dC,
                    latest_depart_s=C_ld, margin_h=(C_ld - dC) / 3600.0),
    ]
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"G1": CrewPlan(crew_id="G1", stops=inc_stops)})
    plan = dispatch.plan([crew], jobs, ens, zones, cfg, NOW, incumbent=incumbent)
    assert set(stops_by_client(plan)) == {"B_lo", "C_lo"}          # 2 homes kept
    assert {u.client_id: u.reason for u in plan.unserved} == {"A_hi": "triaged"}
    assert plan.churn_from_incumbent == 0.0


def _symmetric_cluster(cfg, n_crews, k):
    """n_crews EQUIDISTANT around a center; n_crews*k co-located, equal-value,
    all-feasible jobs at that center. The count-optimal plan is 'serve them
    all'; the anti-concentration term alone decides HOW they distribute."""
    spec = GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                    nrows=60, ncols=60)
    center = cc(spec, 30, 30)
    crews = [make_crew("g%d" % i, _dest(center, i * 360.0 / n_crews, 10_000.0))
             for i in range(n_crews)]
    p10 = NOW + 40 * 3600.0
    jobs = [make_job("j%d" % i, center, p10, p10 + 3600.0, value=1_000_000.0)
            for i in range(n_crews * k)]
    zones = [SafeZone("Z", _dest(center, 0.0, 40_000.0))]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crews, jobs, ens, zones


def test_homes_anti_concentration_spreads_load():
    """FULL-UTILIZATION law (contracts #3): 3 crews, 6 co-located equal-value
    feasible jobs -> 'homes' gives each crew EXACTLY k=2 stops (no crew idle,
    none hoarding), and WITHOUT protecting fewer homes; 'value' minimizes
    travel and piles them onto one crew (the measured Defect-2 behavior)."""
    n_crews, k = 3, 2
    cfg = ResponseConfig(dispatch_objective="homes")
    cfg.solver_budget_s = 30.0        # let the exact micro-solver finish (determinism)
    crews, jobs, ens, zones = _symmetric_cluster(cfg, n_crews, k)
    ph = dispatch.plan(crews, jobs, ens, zones, cfg, NOW)
    per_h = sorted(len(cp.stops) for cp in ph.plans.values())
    assert ph.objective_breakdown["served"] == n_crews * k        # every home served
    assert per_h == [k] * n_crews                                 # each crew ~k, none idle

    cfgv = ResponseConfig(dispatch_objective="value")
    cfgv.solver_budget_s = 30.0
    crews2, jobs2, ens2, zones2 = _symmetric_cluster(cfgv, n_crews, k)
    pv = dispatch.plan(crews2, jobs2, ens2, zones2, cfgv, NOW)
    per_v = sorted(len(cp.stops) for cp in pv.plans.values())
    assert max(per_v) > k                                         # value concentrates
    # spread NEVER cost a home: homes protects at least as many as value
    assert ph.objective_breakdown["served"] >= pv.objective_breakdown["served"]


def test_homes_guardrail_never_serves_fewer_than_greedy_count():
    """The never-worse-than-greedy guardrail guards HOME COUNT in 'homes' mode:
    on every one of the 24 seeded optgap instances the emitted plan protects at
    least as many homes as the raw greedy-EDF seed (greedy-EDF is count-natural
    -- it appends every feasible job)."""
    for i in range(24):
        cfg = ResponseConfig(dispatch_objective="homes")
        cfg.solver_budget_s = 3.0
        crews, jobs, _z = _optgap_instance(i)
        plan = dispatch.plan(crews, jobs, _far_unreached_ens(), _optgap_instance(i)[2],
                             cfg, NOW)
        crews2, jobs2, _z2 = _optgap_instance(i)
        for j in jobs2:
            safety.derive_job_windows(j, cfg)
        g_served, _gv = _edf_baseline(crews2, jobs2, cfg)
        assert plan.objective_breakdown["served"] >= g_served, \
            f"instance {i}: served {plan.objective_breakdown['served']} < greedy {g_served}"


def _brute_force_max_count(crews, jobmap, cfg):
    """Max protectable home COUNT over all disjoint (crew route) splits — the
    'homes'-mode optimum on the 2-crew instances (reuses the value harness's
    per-crew feasible-set DFS under the identical feasibility chain)."""
    b1 = _crew_best_by_set(crews[0], jobmap, cfg)
    b2 = _crew_best_by_set(crews[1], jobmap, cfg)
    best = 0
    for s1 in b1:
        for s2 in b2:
            if s1 & s2:
                continue
            best = max(best, len(s1) + len(s2))
    return best


def test_homes_optimality_24_instances_no_count_miss():
    """Re-baseline of the 24-instance optimality/served harness in 'homes'
    mode (the count-first analogue of test_optimality_gap_24_instances...): the
    planner protects the COUNT-optimal number of homes on every brute-forceable
    instance — ZERO count misses. (Per-instance homes-served and value are
    reported in the sprint notes via scratch_review.)"""
    misses = []
    for i in range(24):
        cfg = ResponseConfig(dispatch_objective="homes")
        cfg.solver_budget_s = 3.0
        crews, jobs, _z = _optgap_instance(i)
        jobmap, _lam = _optgap_prune(crews, jobs, cfg)
        opt_count = _brute_force_max_count(crews, jobmap, cfg)
        crews2, jobs2, zones2 = _optgap_instance(i)
        plan = dispatch.plan(crews2, jobs2, _far_unreached_ens(), zones2, cfg, NOW)
        got = plan.objective_breakdown["served"]
        if got < opt_count:
            misses.append((i, got, opt_count))
    assert misses == [], f"homes-mode count misses vs optimum: {misses}"


def test_value_mode_byte_identical_regression():
    """'value' mode is the untouched legacy planner: identical inputs ->
    byte-identical plans (the arithmetic pins above assert the exact USD
    objective/value_usd; this pins full-plan stability), and it stays DISTINCT
    from 'homes' on the triage dilemma, so the flag is provably live and the
    two modes do not collapse into one."""
    cfg1 = ResponseConfig(dispatch_objective="value")
    cfg1.solver_budget_s = 3.0
    crews1, jobs1, ens1, zones1 = _tight_instance(5)
    a = dispatch.plan(crews1, jobs1, ens1, zones1, cfg1, NOW)
    cfg2 = ResponseConfig(dispatch_objective="value")
    cfg2.solver_budget_s = 3.0
    crews2, jobs2, ens2, zones2 = _tight_instance(5)
    b = dispatch.plan(crews2, jobs2, ens2, zones2, cfg2, NOW)
    assert _fingerprint(a) == _fingerprint(b)
    # the flag is live: 'homes' diverges from 'value' on the dilemma
    cv, jv, ev, zv, _ = _dilemma(ResponseConfig(dispatch_objective="value"))
    ch, jh, eh, zh, _ = _dilemma(ResponseConfig(dispatch_objective="homes"))
    pv = dispatch.plan([cv], jv, ev, zv, ResponseConfig(dispatch_objective="value"), NOW)
    ph = dispatch.plan([ch], jh, eh, zh, ResponseConfig(dispatch_objective="homes"), NOW)
    assert set(stops_by_client(pv)) != set(stops_by_client(ph))


def test_homes_mode_deterministic():
    """'homes' mode keeps the determinism contract: identical inputs ->
    identical plans (stable tie-breaks; contracts.seeded_rng drives shakes)."""
    cfg1 = ResponseConfig(dispatch_objective="homes")
    cfg1.solver_budget_s = 3.0
    crews1, jobs1, ens1, zones1 = _tight_instance(3)
    p1 = dispatch.plan(crews1, jobs1, ens1, zones1, cfg1, NOW)
    cfg2 = ResponseConfig(dispatch_objective="homes")
    cfg2.solver_budget_s = 3.0
    crews2, jobs2, ens2, zones2 = _tight_instance(3)
    p2 = dispatch.plan(crews2, jobs2, ens2, zones2, cfg2, NOW)
    assert _fingerprint(p1) == _fingerprint(p2)


def test_homes_single_job_counts_one():
    """'homes' single-job end-to-end: one protected home, served value ~ the
    count credit (priority x (1 + eps x ...) ~ 1), with the SAME feasible
    timing/safety chain as value mode (the objective changes WHICH homes, never
    the safety math)."""
    cfg = ResponseConfig(dispatch_objective="homes")
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew_loc, client_loc = cc(spec, 10, 2), cc(spec, 10, 7)
    crew = make_crew("A", crew_loc)
    job = make_job("c1", client_loc, NOW + 5 * 3600.0, NOW + 6 * 3600.0)
    plan = dispatch.plan([crew], [job], ens, [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW)
    assert plan.unserved == []
    assert plan.objective_breakdown["served"] == 1
    st = plan.plans["A"].stops[0]
    tt = travel.travel_time_s(crew_loc, client_loc, cfg)
    assert st.arrive_s == pytest.approx(NOW + tt, rel=1e-9)     # safety/timing unchanged
    assert st.margin_h >= 0.0
    assert st.gel_fresh_frac == 1.0
    # count credit ~ 1.0 (priority=1), value only a tiny tiebreak (<= EPS)
    assert 1.0 <= plan.objective_breakdown["value_usd"] <= 1.001


def test_homes_serves_tiny_job_count_first():
    """Count-first: the far tiny-value job that 'value' skips as J-negative
    (test_guardrail_value_floor_serves_job_regret2_skips) is SERVED outright in
    'homes' mode — one more protected home dominates the travel cost."""
    cfg = ResponseConfig(dispatch_objective="homes")
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    crew = make_crew("A", cc(spec, 10, 2))
    big = make_job("big", cc(spec, 10, 4), NOW + 8 * 3600.0, NOW + 10 * 3600.0, value=2_000_000.0)
    tiny = make_job("tiny", cc(spec, 10, 17), NOW + 10 * 3600.0, NOW + 12 * 3600.0, value=1_000.0)
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    plan = dispatch.plan([crew], [big, tiny], ens, zones, cfg, NOW)
    assert set(stops_by_client(plan)) == {"big", "tiny"}
    assert plan.objective_breakdown["served"] == 2


def test_safety_verdicts_byte_identical_across_modes():
    """SAFETY SEMANTICS 2/3/7 are OBJECTIVE-INDEPENDENT: on a scenario mixing a
    reachable job and a corridor-blocked job, the safety-blocked unserved
    reason is byte-identical across 'homes' and 'value', and every emitted stop
    in BOTH modes re-verifies SAFE through the public corridor_safe / egress_ok
    seams against EVERY incident raster. The triage/utilization change is
    objective-side only; it never weakens the corridor/slack/egress chain."""
    def run(objective):
        cfg = ResponseConfig(dispatch_objective=objective)
        spec = spec20()
        early = NOW + 300.0
        blk = [early if c >= 10 else UNREACHED
               for _r in range(spec.nrows) for c in range(spec.ncols)]
        ens = {"inc1": make_ens(uniform_raster(spec, UNREACHED)),
               "inc2": EnsembleArrival(spec=spec, members=[],
                                       p10=Raster(spec, blk), p50=Raster(spec, blk),
                                       p90=Raster(spec, blk), t0_s=NOW,
                                       incident_id="inc2")}
        crew = make_crew("A", cc(spec, 10, 2))
        j_ok = make_job("ok", cc(spec, 10, 5), NOW + 6 * 3600.0, NOW + 7 * 3600.0)
        j_blk = make_job("blk", cc(spec, 10, 16), NOW + 6 * 3600.0, NOW + 7 * 3600.0)
        zones = [SafeZone("z1", cc(spec, 10, 3))]
        plan = dispatch.plan([crew], [j_ok, j_blk], ens, zones, cfg, NOW)
        eroded = {iid: safety._erode(ens[iid].p10, cfg.corridor_erosion_m) for iid in ens}
        speed = safety._corridor_speed_mps(cfg)
        cloc = {"ok": j_ok.client.loc, "blk": j_blk.client.loc}
        for _cid, cp in plan.plans.items():
            loc, t = crew.loc(), NOW
            for st in cp.stops:
                for iid in sorted(ens):
                    assert safety.corridor_safe(loc, cloc[st.client_id], t, speed,
                                                eroded[iid], cfg).safe
                    assert safety.egress_ok(cloc[st.client_id], st.depart_s, zones,
                                            eroded[iid], cfg).safe
                loc, t = cloc[st.client_id], st.depart_s
        return {u.client_id: u.reason for u in plan.unserved}

    rh, rv = run("homes"), run("value")
    assert rh.get("blk") == "no_safe_corridor"
    assert rv.get("blk") == "no_safe_corridor"
    assert rh["blk"] == rv["blk"]              # identical safety verdict, both modes


# ---------------------------------------------------------------------------
# 19. FOUNDER ROUND 4 law 7 — URGENCY-FIRST dispatch, RE-SCOPED by the round-4
#     mis-tuning fix (docs/review/prioritization_report.md). The round-4 build
#     put w_urg x urgency(p50) into the homes OBJECTIVE; under the now-default
#     physics-on fire (fast grass + spotting) that term steered the budget-limited
#     ILS to protect FEWER total homes vs pure count-max (two adversarial sweeps
#     measured a large NET served-count loss, up to -113 on one class).
#
#     FIX (option A / STRICT can't-cost-a-home): urgency is REMOVED from the
#     objective, so the count-maximal SET is chosen URGENCY-BLIND and the emitted
#     homes plan is BYTE-IDENTICAL for every dispatch_urgency_weight (== pure
#     count-max). Urgency therefore can NEVER cost a protected home. The homes
#     objective tiebreak is value-only: score = priority x (1 + EPS x w_val x
#     valuefrac); COUNT stays primary; the SAFETY gate and 'value' mode are
#     UNTOUCHED. A count-safe-per-plan within-fixed-set at-risk-first ordering
#     UTILITY (_urgency_reorder) is retained + tested but is deliberately NOT
#     applied to the emitted plan (any emit-order change feeds back through the
#     sim commitment loop and was measured to cost homes dynamically), so
#     dispatch_urgency_weight is inert in the emitted plan.
# ---------------------------------------------------------------------------

def _ctx_for(cfg, crews, jobs, ens, zones, now_s=NOW):
    """Build a dispatch._Ctx exactly as plan() does (derive windows, erode P10
    rasters), for direct unit tests of the _urgency_reorder utility."""
    eligible = sorted((c for c in crews if c.loc() is not None
                       and c.status not in dispatch._INELIGIBLE_STATUSES),
                      key=lambda c: c.crew_id)
    rasters = [(iid, safety._erode(ens[iid].p10, cfg.corridor_erosion_m))
               for iid in sorted(ens)]
    jobmap = {}
    for job in jobs:
        safety.derive_job_windows(job, cfg)
        jobmap[dispatch._job_key(job)] = job
    return dispatch._Ctx(eligible, jobmap, rasters, zones, cfg, now_s, {}, set())


def _excl_urgency_instance(cfg):
    """1 crew, 2 homes in GENUINE mutual exclusion (serving one blows the
    other's p10 deadline — trivially-safe corridors, so only deadlines +
    objective bind). U is URGENT (median arrival p50=+3 h) and CHEAPER ($1.0M);
    C is COMFORTABLE (p50=+30 h) and PRICIER ($1.4M). Both feasible ALONE; the
    two single-home plans tie on count (1), so the objective alone picks the
    winner — the report's TEST2. Port of scratch_review/diag_prio_urgency.py."""
    spec = _big_spec()
    crew_loc = cc(spec, 20, 30)
    U_loc, C_loc = cc(spec, 20, 6), cc(spec, 20, 54)   # far opposite ways
    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)
    deploy_s, egr, slack = cfg.deploy_min * 60.0, cfg.egress_buffer_min * 60.0, cfg.slack_min * 60.0
    t_cU, t_cC = tt(crew_loc, U_loc), tt(crew_loc, C_loc)
    U_ld = NOW + t_cU + deploy_s + slack + 120.0       # just feasible if served first
    C_ld = NOW + t_cC + deploy_s + slack + 120.0
    # exclusivity by construction (serving one then driving to the other misses)
    assert NOW + t_cU + deploy_s + tt(U_loc, C_loc) + deploy_s > C_ld - slack
    assert NOW + t_cC + deploy_s + tt(C_loc, U_loc) + deploy_s > U_ld - slack
    U = make_job("U", U_loc, U_ld + egr, NOW + 3 * 3600.0, value=1_000_000.0)
    C = make_job("C", C_loc, C_ld + egr, NOW + 30 * 3600.0, value=1_400_000.0)
    crew = make_crew("G1", crew_loc)
    zones = [SafeZone("Z1", cc(spec, 20, 39))]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crew, [U, C], ens, zones


def test_homes_urgency_first_emit_is_urgency_blind():
    """Round-4 mis-tuning fix: under GENUINE 1-crew/2-home mutual exclusion the
    EMITTED homes plan is urgency-BLIND -- it protects the pricier home (C, the
    value-tiebreak winner), IDENTICAL to 'value' mode, and does so for EVERY
    dispatch_urgency_weight. Urgency no longer flips the exclusive count-1 choice
    (that objective term is what cost homes under the physics-on fire); it
    survives only as the within-fixed-set _urgency_reorder utility, which cannot
    change WHICH home a 1-crew mutual exclusion serves."""
    def homes(w_urg):
        cfg = ResponseConfig(dispatch_objective="homes", dispatch_urgency_weight=w_urg)
        crew, jobs, ens, zones = _excl_urgency_instance(cfg)
        return dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
    p_on, p_off = homes(0.30), homes(0.0)
    assert set(stops_by_client(p_on)) == {"C"}               # value-tiebreak winner
    assert {u.client_id: u.reason for u in p_on.unserved} == {"U": "capacity"}
    assert _fingerprint(p_on) == _fingerprint(p_off)         # byte-identical vs w_urg=0

    cfg_v = ResponseConfig(dispatch_objective="value")
    crew2, jobs2, ens2, zones2 = _excl_urgency_instance(cfg_v)
    pv = dispatch.plan([crew2], jobs2, ens2, zones2, cfg_v, NOW)
    assert set(stops_by_client(pv)) == {"C"}                 # 'value' also takes pricier
    assert set(stops_by_client(p_on)) == set(stops_by_client(pv))   # same choice now


def test_homes_count_primary_value_band_below_one_home():
    """COUNT stays the primary maximand: the whole OBJECTIVE tiebreak lives in the
    EPS-lexicographic band [0, EPS x w_val] (VALUE ONLY -- urgency is not in the
    objective), so even 1000 max-value homes accumulate strictly less than one
    count credit. No value can outweigh one additional served home."""
    cfg = ResponseConfig()
    band = dispatch._HOMES_VALUE_EPS * cfg.dispatch_value_weight
    assert 1000 * band < 1.0
    # a max-value ($99M+) home still scores in [1, 1 + band] -- one count credit
    j = make_job("m", LatLon(34, -117), NOW, NOW, value=99_000_000.0)
    s = dispatch._potential_score(j, cfg)
    assert 1.0 < s < 1.0 + band + 1e-12               # exactly one count credit + band


def _count_primary_instance(cfg):
    """1 crew: {P1,P2} (two MINIMALLY attractive homes: cheapest $1, comfortable
    p50=+71 h) XOR {S} (one MAXIMALLY attractive home: $50M, urgent p50=+2 h).
    Serving S blows P1's deadline; P1->P2 fits directly. The urgency+value
    tiebreak maximally favours S, yet count-primacy must still protect the two."""
    spec = _big_spec()
    crew_loc = cc(spec, 20, 30)
    S_loc, P1_loc, P2_loc = cc(spec, 20, 33), cc(spec, 20, 6), cc(spec, 21, 6)
    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)
    deploy_s, egr, slack = cfg.deploy_min * 60.0, cfg.egress_buffer_min * 60.0, cfg.slack_min * 60.0
    t_cS, t_cP, t_P1P2 = tt(crew_loc, S_loc), tt(crew_loc, P1_loc), tt(P1_loc, P2_loc)
    S_ld = NOW + t_cS + deploy_s + slack + 120.0
    dP = NOW + t_cP + deploy_s
    P1_ld = dP + slack + 120.0
    dP2 = dP + t_P1P2 + deploy_s
    P2_ld = dP2 + slack + 120.0
    assert NOW + t_cS + deploy_s + tt(S_loc, P1_loc) + deploy_s > P1_ld   # S-first blows P1
    assert dP2 <= P2_ld - slack + 1e-6                                    # P1->P2 fits
    S = make_job("S", S_loc, S_ld + egr, NOW + 2 * 3600.0, value=50_000_000.0)
    P1 = make_job("P1", P1_loc, P1_ld + egr, NOW + 71 * 3600.0, value=1.0)
    P2 = make_job("P2", P2_loc, P2_ld + egr, NOW + 71 * 3600.0, value=1.0)
    crew = make_crew("G1", crew_loc)
    zones = [SafeZone("Z1", cc(spec, 20, 39))]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crew, [S, P1, P2], ens, zones


def test_homes_count_primary_two_cheap_beat_one_urgent_costly():
    """N+1 beats N regardless of value: two MINIMALLY attractive homes (cheapest,
    most comfortable) are protected over one MAXIMALLY attractive home ($50M,
    imminent) — the extra count dominates the biggest possible value tiebreak.
    The skipped big home is 'triaged' (serving it would cost a NET home)."""
    cfg = ResponseConfig(dispatch_objective="homes")
    crew, jobs, ens, zones = _count_primary_instance(cfg)
    plan = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
    assert set(stops_by_client(plan)) == {"P1", "P2"}         # count (2) > 1
    assert plan.objective_breakdown["served"] == 2
    assert {u.client_id: u.reason for u in plan.unserved} == {"S": "triaged"}


def test_urgency_function_monotonic_and_unreached_zero():
    """_urgency(p50) is a bounded, DECREASING function of hours-to-p50 (sooner ->
    larger), 1.0 for a fire arriving now, and 0.0 for a non-arriving (UNREACHED
    p50) job. This is the ranking signal the within-fixed-set _urgency_reorder
    utility uses; it is NOT read by the objective (round-4 mis-tuning fix)."""
    cfg = ResponseConfig(dispatch_objective="homes")
    loc = LatLon(34, -117)
    soon = make_job("s", loc, NOW + 3 * 3600.0, NOW + 3 * 3600.0)
    late = make_job("l", loc, NOW + 30 * 3600.0, NOW + 30 * 3600.0)
    assert dispatch._urgency(soon, NOW, cfg) > dispatch._urgency(late, NOW, cfg)
    assert 0.0 <= dispatch._urgency(late, NOW, cfg) <= 1.0
    now_arr = make_job("n", loc, NOW, NOW, value=1.0)
    assert dispatch._urgency(now_arr, NOW, cfg) == 1.0        # arriving now => max
    unreached = make_job("u", loc, NOW + 3 * 3600.0, UNREACHED, value=1_000_000.0)
    assert dispatch._urgency(unreached, NOW, cfg) == 0.0      # no median threat


def test_value_and_homes_modes_byte_identical_under_wild_urgency_weight():
    """Neither mode reads dispatch_urgency_weight into the emitted plan: wildly
    different weights leave the plan BYTE-IDENTICAL. 'value' always ignored it;
    'homes' now ignores it too (round-4 mis-tuning fix), so urgency can never
    change -- and thus never cost -- a home. ('homes' still reads the VALUE
    weight, so only w_urg is swept here.)"""
    def fp(objective, uw):
        cfg = ResponseConfig(dispatch_objective=objective)
        cfg.solver_budget_s = 3.0
        cfg.dispatch_urgency_weight = uw
        crews, jobs, ens, zones = _tight_instance(5)
        return _fingerprint(dispatch.plan(crews, jobs, ens, zones, cfg, NOW))
    for objective in ("value", "homes"):
        base = fp(objective, 0.30)
        for uw in (0.0, 0.60, 5.0, 1e6, -3.0, 1e-9):
            assert fp(objective, uw) == base, (objective, uw)


def test_homes_urgency_weight_never_serves_fewer_than_pure_count():
    """THE anti-'-129' pin (option A / STRICT can't-cost-a-home): the emitted
    homes plan is BYTE-IDENTICAL for every dispatch_urgency_weight, so
    served(any w_urg) == served(w_urg=0 pure count-max) EXACTLY on every instance
    -- urgency can never serve FEWER homes than pure count-max. (The round-4
    build's urgency OBJECTIVE term did exactly that under the physics-on fire: two
    adversarial sweeps measured a NET served-count loss up to -113.) These are
    exact-micro-solver instances, so the pin is deterministic (budget-free)."""
    for i in range(24):
        crews, jobs, zones = _optgap_instance(i)
        base = dispatch.plan(crews, jobs, _far_unreached_ens(), zones,
                             ResponseConfig(dispatch_objective="homes",
                                            dispatch_urgency_weight=0.0), NOW)
        for w_urg in (0.05, 0.30, 2.0):
            crews2, jobs2, zones2 = _optgap_instance(i)
            p = dispatch.plan(crews2, jobs2, _far_unreached_ens(), zones2,
                              ResponseConfig(dispatch_objective="homes",
                                             dispatch_urgency_weight=w_urg), NOW)
            assert (p.objective_breakdown["served"]
                    >= base.objective_breakdown["served"])       # never fewer
            assert _fingerprint(p) == _fingerprint(base), (i, w_urg)  # byte-identical


# Pre-urgency 'homes'-mode served count on each of the 24 seeded optgap
# instances (captured before the FOUNDER ROUND 4 law-7 change = the value-tiebreak
# count-max). The round-4 mis-tuning fix makes the emitted set urgency-BLIND, so
# the count-max set is exactly this pre-urgency (pure count-max) set again -- the
# harness must never protect FEWER homes.
_HOMES_SERVED_24_PRE_URGENCY = [4, 5, 4, 4, 4, 6, 3, 5, 4, 3, 5, 5, 4, 5, 6, 4,
                                6, 5, 3, 5, 4, 4, 5, 4]


def test_homes_24_instances_never_fewer_than_pure_count_baseline():
    """The 24-instance homes harness at the default weight protects AT LEAST as
    many homes as the pre-urgency (pure count-max) baseline on every instance --
    a regression guard that the urgency-blind refactor never dropped a home."""
    for i in range(24):
        cfg = ResponseConfig(dispatch_objective="homes")
        cfg.solver_budget_s = 3.0
        crews, jobs, _z = _optgap_instance(i)
        plan = dispatch.plan(crews, jobs, _far_unreached_ens(),
                             _optgap_instance(i)[2], cfg, NOW)
        got = plan.objective_breakdown["served"]
        assert got >= _HOMES_SERVED_24_PRE_URGENCY[i], \
            f"instance {i}: dropped a home ({got} < {_HOMES_SERVED_24_PRE_URGENCY[i]})"


def test_homes_urgency_is_objective_only_safety_verdicts_unchanged():
    """The urgency term is OBJECTIVE-only: on the mixed reachable / corridor-
    blocked scenario the emitted safety timings + unserved reasons are
    BYTE-IDENTICAL to 'value' mode (which is byte-identical to the pre-urgency
    planner — pinned by test_single_crew_single_job_end_to_end and
    test_value_mode_byte_identical_regression). So the corridor/egress/slack
    verdicts are unchanged before/after the urgency term; only the objective
    scalar (which home the count-max objective prefers) may move."""
    def run(objective):
        cfg = ResponseConfig(dispatch_objective=objective)
        spec = spec20()
        early = NOW + 300.0
        blk = [early if c >= 10 else UNREACHED
               for _r in range(spec.nrows) for c in range(spec.ncols)]
        ens = {"inc1": make_ens(uniform_raster(spec, UNREACHED)),
               "inc2": EnsembleArrival(spec=spec, members=[],
                                       p10=Raster(spec, blk), p50=Raster(spec, blk),
                                       p90=Raster(spec, blk), t0_s=NOW,
                                       incident_id="inc2")}
        crew = make_crew("A", cc(spec, 10, 2))
        j_ok = make_job("ok", cc(spec, 10, 5), NOW + 6 * 3600.0, NOW + 7 * 3600.0)
        j_blk = make_job("blk", cc(spec, 10, 16), NOW + 6 * 3600.0, NOW + 7 * 3600.0)
        zones = [SafeZone("z1", cc(spec, 10, 3))]
        return dispatch.plan([crew], [j_ok, j_blk], ens, zones, cfg, NOW)

    ph, pv = run("homes"), run("value")
    # routes (with all safety timings) + unserved reasons, excluding the
    # objective scalar which legitimately differs between the two objectives
    assert _fingerprint(ph)[:2] == _fingerprint(pv)[:2]
    assert {u.client_id: u.reason for u in ph.unserved} == {"blk": "no_safe_corridor"}


def test_homes_mode_deterministic_on_exclusion_instance():
    """Determinism holds in homes mode: identical inputs -> identical plans on the
    mutual-exclusion instance (all sort keys stable, objective urgency-blind)."""
    cfg1 = ResponseConfig(dispatch_objective="homes")
    crew1, jobs1, ens1, zones1 = _excl_urgency_instance(cfg1)
    p1 = dispatch.plan([crew1], jobs1, ens1, zones1, cfg1, NOW)
    cfg2 = ResponseConfig(dispatch_objective="homes")
    crew2, jobs2, ens2, zones2 = _excl_urgency_instance(cfg2)
    p2 = dispatch.plan([crew2], jobs2, ens2, zones2, cfg2, NOW)
    assert _fingerprint(p1) == _fingerprint(p2)


# --- _urgency_reorder utility: within-fixed-set at-risk-first ordering ------
# The count-safe-PER-PLAN within-fixed-set at-risk-first ordering the round-4 fix
# left urgency as. NOT applied to the emitted plan (see dispatch module header),
# but retained + tested so the "reorder which home a crew does first, never change
# the served set" property is verified and available.

def _reorder_both_feasible(cfg):
    """1 crew, 2 homes BOTH servable in EITHER order (loose deadlines, trivial
    corridors): U urgent (p50=+3 h), C comfortable (p50=+30 h)."""
    spec = _big_spec()
    crew_loc = cc(spec, 20, 30)
    U_loc, C_loc = cc(spec, 20, 28), cc(spec, 20, 32)       # both near, opposite sides
    U = make_job("U", U_loc, NOW + 3 * 3600.0, NOW + 3 * 3600.0, value=1_000_000.0)
    C = make_job("C", C_loc, NOW + 25 * 3600.0, NOW + 30 * 3600.0, value=1_400_000.0)
    crew = make_crew("G1", crew_loc)
    zones = [SafeZone("Z1", crew_loc)]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crew, U, C, ens, zones


def _reorder_urgency_infeasible(cfg):
    """1 crew, 2 homes servable ONLY in the order [C, U]: C (comfortable) has a
    tight deadline met only if served first; U (urgent) is far. The urgency-first
    order [U, C] is INFEASIBLE, so _urgency_reorder must keep [C, U]."""
    spec = _big_spec()
    crew_loc = cc(spec, 20, 30)
    C_loc, U_loc = cc(spec, 20, 32), cc(spec, 20, 6)        # C near (east), U far (west)
    def tt(a, b):
        return travel.travel_time_s(a, b, cfg)
    deploy, egr, slack = cfg.deploy_min * 60.0, cfg.egress_buffer_min * 60.0, cfg.slack_min * 60.0
    t_cC, t_cU = tt(crew_loc, C_loc), tt(crew_loc, U_loc)
    t_CU, t_UC = tt(C_loc, U_loc), tt(U_loc, C_loc)
    C_ld = NOW + t_cC + deploy + slack + 120.0             # C tight: only if FIRST
    dU_after_C = NOW + t_cC + deploy + t_CU + deploy
    U_ld = dU_after_C + slack + 600.0                       # U generous
    assert dU_after_C <= U_ld - slack + 1e-6               # [C,U] feasible
    assert NOW + t_cU + deploy + t_UC + deploy > C_ld - slack   # [U,C] blows C
    U = make_job("U", U_loc, U_ld + egr, NOW + 3 * 3600.0, value=1_000_000.0)
    C = make_job("C", C_loc, C_ld + egr, NOW + 30 * 3600.0, value=1_400_000.0)
    crew = make_crew("G1", crew_loc)
    zones = [SafeZone("Z1", crew_loc)]
    ens = {INC: make_ens(uniform_raster(spec, UNREACHED))}
    return crew, U, C, ens, zones


def test_urgency_reorder_orders_urgent_first_within_fixed_set():
    """The 1-crew/2-home at-risk-first ordering, WHERE IT COSTS NO HOME: given a
    fixed count-maximal route serving BOTH homes, _urgency_reorder services the
    sooner-to-burn home (U, p50=+3 h) before the comfortable one (C, p50=+30 h)
    and drops NEITHER (the served set is invariant). Inert at w_urg == 0."""
    cfg = ResponseConfig(dispatch_objective="homes")           # w_urg default 0.30
    crew, U, C, ens, zones = _reorder_both_feasible(cfg)
    ctx = _ctx_for(cfg, [crew], [U, C], ens, zones)
    uk, ck = dispatch._job_key(U), dispatch._job_key(C)
    out = dispatch._urgency_reorder(ctx, "G1", [ck, uk], 0)
    assert out == [uk, ck]                                      # urgent first
    assert set(out) == {uk, ck}                                 # both kept (no drop)
    assert dispatch._urgency_reorder(ctx, "G1", [uk, ck], 0) == [uk, ck]  # stable
    # w_urg == 0 => inert (blind order kept verbatim)
    cfg0 = ResponseConfig(dispatch_objective="homes", dispatch_urgency_weight=0.0)
    ctx0 = _ctx_for(cfg0, [crew], [U, C], ens, zones)
    assert dispatch._urgency_reorder(ctx0, "G1", [ck, uk], 0) == [ck, uk]


def test_urgency_reorder_never_drops_a_home_when_order_infeasible():
    """_urgency_reorder can NEVER cost a home: when the urgency-first order is
    infeasible (would blow a deadline) it KEEPS the blind order and serves both.
    The strict can't-cost-a-home property, per plan() call."""
    cfg = ResponseConfig(dispatch_objective="homes")
    crew, U, C, ens, zones = _reorder_urgency_infeasible(cfg)
    ctx = _ctx_for(cfg, [crew], [U, C], ens, zones)
    uk, ck = dispatch._job_key(U), dispatch._job_key(C)
    assert ctx.eval_route("G1", (ck, uk))[0] is not None        # blind order feasible
    assert ctx.eval_route("G1", (uk, ck))[0] is None            # urgency order not
    out = dispatch._urgency_reorder(ctx, "G1", [ck, uk], 0)
    assert out == [ck, uk]                                      # kept feasible blind order
    assert set(out) == {uk, ck}                                 # both homes still served


def test_urgency_reorder_respects_frozen_prefix_and_value_mode_noop():
    """_urgency_reorder never touches the FROZEN (committed) prefix, and is a
    no-op in 'value' mode."""
    cfg = ResponseConfig(dispatch_objective="homes")
    crew, U, C, ens, zones = _reorder_both_feasible(cfg)
    ctx = _ctx_for(cfg, [crew], [U, C], ens, zones)
    uk, ck = dispatch._job_key(U), dispatch._job_key(C)
    # frozen_n=1 pins C first (committed); the 1-long non-frozen suffix can't move
    assert dispatch._urgency_reorder(ctx, "G1", [ck, uk], 1) == [ck, uk]
    # value mode: reorder is inert regardless of weight
    cfgv = ResponseConfig(dispatch_objective="value")
    ctxv = _ctx_for(cfgv, [crew], [U, C], ens, zones)
    assert dispatch._urgency_reorder(ctxv, "G1", [ck, uk], 0) == [ck, uk]
