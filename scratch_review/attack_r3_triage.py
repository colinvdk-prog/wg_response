"""attack_r3_triage.py — FOUNDER ROUND 3 law 4 (TRIAGE, 'protect as many homes
as possible'): cfg.dispatch_objective 'homes' (default) maximizes the COUNT of
homes protected and may ABANDON a planned-not-started job ('triaged'), but this
is OBJECTIVE-ONLY and NEVER weakens SAFETY SEMANTICS 2/3/7.

Attacks:
  1. COUNT BEATS DOLLARS AT ANY GAP: sweep the expensive home's value up to
     $1e15. 'homes' must ALWAYS protect the two cheap homes (count 2) and triage
     the expensive one; 'value' always protects the single expensive one. No
     value gap can outweigh one served home.
  2. TRIAGED REASON: the skipped home is 'triaged' in homes mode (feasible on its
     own, count-costing) and 'capacity' (never 'triaged') in value mode; every
     'triaged' client is provably solo-servable (triage never masks a safety
     failure).
  3. SAFETY IS OBJECTIVE-INDEPENDENT: a home whose corridor crosses a fire is
     no_safe_corridor in BOTH modes; a served home's margin/slack and an
     INDEPENDENT corridor+egress re-verification are byte-identical across modes.
  4. NO TRIAGE SERVES AN UNSAFE STOP: across every homes-mode scenario, each
     served stop has margin_h >= 0 AND slack >= floor.
  5. VALUE MODE UNCHANGED: 'triaged' NEVER appears in value mode over a battery
     of scenarios, and the value objective equals the legacy $-formula.
  6. SIM-LEVEL: the SAME ignition/seed in homes vs value both stay at 0 audit
     violations with every served stop safe; value never triages.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_r3_common import make_session, tile_present, skip_loud       # noqa: E402
from wgresponse import dispatch, safety, travel                          # noqa: E402
from wgresponse.contracts import (                                       # noqa: E402
    UNREACHED, ClientProperty, Crew, CrewBeacon, CrewStatus, DispatchJob,
    EnsembleArrival, GridSpec, Raster, ResponseConfig, SafeZone, ThreatEta,
)

NOW = 1_790_000_000.0
INC = "inc1"
SPEC = GridSpec(north_lat=34.40, west_lon=-117.80, cell_m=1000.0,
                nrows=40, ncols=60)


def cc(r, c):
    return safety._rc_center(SPEC, r, c)


def mk_crew(cid, loc):
    return Crew(crew_id=cid, name=cid, status=CrewStatus.AVAILABLE,
                last_beacon=CrewBeacon(crew_id=cid, loc=loc, t_s=NOW,
                                       status=CrewStatus.AVAILABLE),
                home_base=loc)


def mk_job(cid, loc, p10, p50, val, weight=1.0):
    return DispatchJob(
        client=ClientProperty(client_id=cid, loc=loc, value_usd=val,
                              priority_weight=weight),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10, eta_p50_s=p50, eta_p90_s=p50 + 3600.0))


def _ens(p10):
    return {INC: EnsembleArrival(spec=SPEC, members=[], p10=p10, p50=p10,
                                 p90=p10, t0_s=NOW, incident_id=INC)}


def _safe_ens():
    return _ens(Raster(SPEC, [UNREACHED] * (SPEC.nrows * SPEC.ncols)))


def _served(plan):
    return {s.client_id for cp in plan.plans.values() for s in cp.stops}


def _stops(plan):
    return [s for cp in plan.plans.values() for s in cp.stops]


def _dilemma(cfg, a_value):
    """1-crew triage dilemma (port of the diag_triage arithmetic): expensive
    home A near the crew XOR two cheap homes B,C far the OPPOSITE way. Only the
    objective binds (corridors trivially safe)."""
    crew_loc = cc(20, 30)
    A_loc, B_loc, C_loc = cc(20, 6), cc(20, 54), cc(21, 54)
    tt = lambda a, b: travel.travel_time_s(a, b, cfg)
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
    # exclusivity proven by construction
    assert NOW + t_cA + deploy_s + tt(A_loc, B_loc) + deploy_s > B_ld
    assert dC <= C_ld - slack + 1e-6
    A = mk_job("A_hi", A_loc, A_p10, A_p10 + 2 * 3600.0, a_value)
    B = mk_job("B_lo", B_loc, B_ld + egr, B_ld + egr + 2 * 3600.0, 700_000.0)
    C = mk_job("C_lo", C_loc, C_ld + egr, C_ld + egr + 2 * 3600.0, 700_000.0)
    return mk_crew("G1", crew_loc), [A, B, C], _safe_ens(), \
        [SafeZone("Z1", cc(20, 39))]


# ===========================================================================
# 1. Count beats dollars at ANY value gap
# ===========================================================================
@pytest.mark.parametrize("a_value", [2e6, 1e8, 1e12, 1e15])
def test_homes_count_beats_any_dollar_gap(a_value):
    crew, jobs, ens, zones = _dilemma(ResponseConfig(dispatch_objective="homes"),
                                      a_value)
    ph = dispatch.plan([crew], jobs, ens, zones,
                       ResponseConfig(dispatch_objective="homes"), NOW)
    assert _served(ph) == {"B_lo", "C_lo"}, \
        f"homes with A=${a_value:g} served {_served(ph)}, expected the 2 cheap " \
        f"homes — a value gap outweighed a home (COUNT law broken)"
    crewv, jobsv, ensv, zonesv = _dilemma(
        ResponseConfig(dispatch_objective="value"), a_value)
    pv = dispatch.plan([crewv], jobsv, ensv, zonesv,
                       ResponseConfig(dispatch_objective="value"), NOW)
    assert _served(pv) == {"A_hi"}, \
        f"value with A=${a_value:g} served {_served(pv)}, expected the 1 " \
        f"expensive home"


# ===========================================================================
# 2. Triaged reason + triage never masks a safety failure
# ===========================================================================
def test_triaged_reason_and_solo_servable():
    crew, jobs, ens, zones = _dilemma(ResponseConfig(dispatch_objective="homes"),
                                      5e6)
    cfg = ResponseConfig(dispatch_objective="homes")
    ph = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
    reasons = {u.client_id: u.reason for u in ph.unserved}
    assert reasons == {"A_hi": "triaged"}, reasons
    # a 'triaged' client MUST be feasible on its own (else it is a safety miss
    # mislabelled): re-solve the lone crew + that lone job -> it is servable.
    for u in ph.unserved:
        if u.reason == "triaged":
            job = next(j for j in jobs if j.client.client_id == u.client_id)
            solo = dispatch.plan([mk_crew("G1", cc(20, 30))], [job], ens, zones,
                                 cfg, NOW)
            assert u.client_id in _served(solo), \
                f"{u.client_id} was 'triaged' but is NOT solo-servable — " \
                f"triage is masking a safety/deadline failure"

    # value mode: same instance -> B,C are 'capacity', NEVER 'triaged'
    crewv, jobsv, ensv, zonesv = _dilemma(
        ResponseConfig(dispatch_objective="value"), 5e6)
    pv = dispatch.plan([crewv], jobsv, ensv, zonesv,
                       ResponseConfig(dispatch_objective="value"), NOW)
    rv = {u.client_id: u.reason for u in pv.unserved}
    assert rv == {"B_lo": "capacity", "C_lo": "capacity"}, rv
    assert "triaged" not in set(rv.values())


# ===========================================================================
# 3. Safety verdicts are objective-independent (fire-disk corridor block)
# ===========================================================================
def _fire_disk_scenario():
    """Crew at (20,30); home A (20,10) has a clear corridor; home D (20,52) sits
    behind a disk of NOW-arrival fire cells so its corridor is unsafe."""
    data = [UNREACHED] * (SPEC.nrows * SPEC.ncols)
    for r in range(SPEC.nrows):
        for c in range(SPEC.ncols):
            if (r - 20) ** 2 + (c - 40) ** 2 <= 16:
                data[r * SPEC.ncols + c] = NOW + 60.0
    ens = _ens(Raster(SPEC, data))
    crew = mk_crew("G1", cc(20, 30))
    zones = [SafeZone("Z1", cc(20, 30))]
    A = mk_job("A", cc(20, 10), NOW + 6 * 3600, NOW + 8 * 3600, 1e6)
    D = mk_job("D", cc(20, 52), NOW + 6 * 3600, NOW + 8 * 3600, 9e6)
    return crew, [A, D], ens, zones


def test_safety_reason_objective_independent():
    crew, jobs, ens, zones = _fire_disk_scenario()
    out = {}
    for obj in ("homes", "value"):
        cfg = ResponseConfig(dispatch_objective=obj)
        plan = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
        reasons = {u.client_id: u.reason for u in plan.unserved}
        margins = {s.client_id: round(s.margin_h, 6) for s in _stops(plan)}
        out[obj] = (_served(plan), reasons, margins)
    # the fire-blocked home is no_safe_corridor in BOTH modes; the served home's
    # margin is byte-identical across modes (safety math ignores the objective)
    assert out["homes"][1] == out["value"][1] == {"D": "no_safe_corridor"}, out
    assert out["homes"][0] == out["value"][0] == {"A"}, out
    assert out["homes"][2] == out["value"][2], \
        f"served-stop margins differ by objective: {out['homes'][2]} vs " \
        f"{out['value'][2]}"

    # INDEPENDENT re-verification (safety seams): A's corridor+egress are safe,
    # D's ingress corridor is genuinely UNSAFE (justifying no_safe_corridor).
    cfg = ResponseConfig()
    eroded = safety._erode(ens[INC].p10, cfg.corridor_erosion_m)
    speed = safety._corridor_speed_mps(cfg)
    A_loc, D_loc = cc(20, 10), cc(20, 52)
    assert safety.corridor_safe(cc(20, 30), A_loc, NOW, speed, eroded, cfg).safe
    assert safety.egress_ok(A_loc, NOW + 3600.0, zones, eroded, cfg).safe
    assert not safety.corridor_safe(cc(20, 30), D_loc, NOW, speed, eroded, cfg).safe


# ===========================================================================
# 4. No triage ever serves an unsafe stop
# ===========================================================================
@pytest.mark.parametrize("a_value", [2e6, 1e9, 1e15])
def test_homes_served_stops_all_safe(a_value):
    cfg = ResponseConfig(dispatch_objective="homes")
    crew, jobs, ens, zones = _dilemma(cfg, a_value)
    ph = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
    for s in _stops(ph):
        assert s.margin_h >= 0.0, \
            f"served stop {s.client_id} has NEGATIVE margin {s.margin_h:.4f}h " \
            f"— triage produced an unsafe (past-deadline) stop"
        assert s.margin_h * 60.0 >= cfg.slack_min - 1e-6, \
            f"served stop {s.client_id} slack {s.margin_h*60:.2f}min < floor " \
            f"{cfg.slack_min}min (SAFETY SEMANTICS 7 violated by triage)"


# ===========================================================================
# 5. Value mode never triages; value objective is the legacy $-formula
# ===========================================================================
def test_value_mode_never_triages_and_objective_is_legacy():
    cfg = ResponseConfig(dispatch_objective="value")
    # a battery of dilemmas at many value gaps + the fire-disk scenario
    for a_value in (1e6, 5e6, 1e9, 1e14):
        crew, jobs, ens, zones = _dilemma(cfg, a_value)
        pv = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
        rv = {u.reason for u in pv.unserved}
        assert "triaged" not in rv, \
            f"value mode emitted 'triaged' at A=${a_value:g} (homes-only reason)"
        # legacy objective: served value = sum priority*value*gel_effectiveness
        served_jobs = [j for j in jobs if j.client.client_id in _served(pv)]
        legacy = sum(j.client.priority_weight * j.client.value_usd
                     * cfg.gel_effectiveness for j in served_jobs)
        assert pv.objective_breakdown["value_usd"] == pytest.approx(legacy), \
            f"value objective {pv.objective_breakdown['value_usd']} != legacy " \
            f"{legacy}"
    crew, jobs, ens, zones = _fire_disk_scenario()
    pv = dispatch.plan([crew], jobs, ens, zones, cfg, NOW)
    assert "triaged" not in {u.reason for u in pv.unserved}


# ===========================================================================
# 6. SIM-LEVEL: same ignition, both modes safe, value never triages
# ===========================================================================
@pytest.mark.parametrize("key", ["malibu_ca", "boulder_co"])
def test_sim_homes_vs_value_both_safe(key):
    if not tile_present(key):
        pytest.skip(skip_loud(key, "data/tiles/*.wgt"))
    from attack_sim_common import pick_ignition
    # a fixed ignition for both modes (same seed => same placement)
    twin = make_session(key, 40, 4, seed="r3-triage")
    p, _t = pick_ignition(twin, key, cluster=False)

    counts = {}
    for obj in ("homes", "value"):
        sess = make_session(key, 40, 4, seed="r3-triage",
                            cfg={"dispatch_objective": obj})
        sess.ignite(p.lat, p.lon, intensity="high")
        for _ in range(40):
            st = sess.tick()
        # both modes: 0 audit violations, every served stop non-negative margin
        assert st["scoreboard"]["violations"] == 0, \
            f"{key}/{obj}: {st['scoreboard']['violations']} violations"
        for s in st["serviced"]:
            assert s["margin_h"] >= 0.0, \
                f"{key}/{obj}: served {s['client_id']} margin {s['margin_h']}<0"
        reasons = {u["reason"] for u in st["unserved"]}
        if obj == "value":
            assert "triaged" not in reasons, \
                f"{key}: value mode emitted 'triaged' in the sim"
        counts[obj] = st["scoreboard"]["served"]
    print(f"\n{key}: served homes={counts['homes']} value={counts['value']}")
