"""Regression tests for the ember-buffer safety findings in wgresponse.sim.

Two confirmed 'safety'-dimension findings:

 1. sim._latest_departure_s (the safety-module-absent fallback) dropped the
    ember_buffer_min term, so in a wind event (ember_buffer_min > 0) it computed
    a departure deadline 15 min LATER than the canonical safety.latest_departure_s
    — stripping exactly the ember margin the buffer exists to add.

 2. sim.replan_console built jobs via that ember-omitting fallback AND ran the
    fallback plan with an empty ensemble (no corridor checks). Its plans must (a)
    use the canonical ember-inclusive deadline and (b) be clearly flagged as
    corridor-unvalidated so a caller cannot mistake the preview for a
    safety-validated operational plan.

All hermetic: no network, no third-party deps, no wall-clock. ember_buffer_min
is the switch that separates the buggy branch from the correct one.
"""
import math

from wgresponse import sim
from wgresponse import safety as safety_mod
from wgresponse.contracts import ResponseConfig


# egress_buffer_min=30, ember_buffer_min=15 mirrors a wind event: the finding's
# canonical deadline is arrival-45min, the buggy fallback yielded arrival-30min.
CFG_WIND = ResponseConfig(egress_buffer_min=30.0, ember_buffer_min=15.0)
ARRIVAL_S = 1_770_400_000.0


# ---------------------------------------------------------------------------
# Finding 1: fallback deadline must match the canonical safety module exactly
# ---------------------------------------------------------------------------

def test_fallback_latest_departure_includes_ember_buffer():
    """sim._latest_departure_s must subtract (egress + ember)*60, identical to
    safety.latest_departure_s. With a 15-min ember buffer that is arrival-45min."""
    got = sim._latest_departure_s(ARRIVAL_S, CFG_WIND)
    canonical = safety_mod.latest_departure_s(ARRIVAL_S, CFG_WIND)
    assert got == canonical, "sim fallback diverges from canonical safety deadline"
    expected = ARRIVAL_S - (30.0 + 15.0) * 60.0
    assert got == expected
    # the buggy value (ember omitted) is 15 min later — must NOT be produced
    buggy = ARRIVAL_S - 30.0 * 60.0
    assert got != buggy
    assert got == buggy - 15.0 * 60.0  # exactly the ember margin, earlier


def test_fallback_latest_departure_matches_canonical_across_configs():
    for eb, mb in ((30.0, 0.0), (30.0, 15.0), (45.0, 30.0), (0.0, 20.0)):
        cfg = ResponseConfig(egress_buffer_min=eb, ember_buffer_min=mb)
        assert sim._latest_departure_s(ARRIVAL_S, cfg) == \
            safety_mod.latest_departure_s(ARRIVAL_S, cfg)


def test_fallback_latest_departure_infinite_eta():
    """UNREACHED arrival yields no deadline (inf), matching the canonical fn."""
    assert math.isinf(sim._latest_departure_s(math.inf, CFG_WIND))


# ---------------------------------------------------------------------------
# Finding 2: replan_console uses the ember-inclusive deadline AND flags its
# plans as corridor-unvalidated
# ---------------------------------------------------------------------------

def _console_with_one_client():
    """Minimal console dict: one crew, one threatened client, one safe zone.
    p50 far enough out that the deadline is not the binding constraint, so a
    stop is actually planned and we can inspect its deadline."""
    now = 1_770_300_000.0
    p10 = now + 40.0 * 3600.0   # pessimistic arrival 40 h out
    p50 = now + 50.0 * 3600.0
    return {
        "now_s": now,
        "t0_s": now,
        "incidents": [{"incident_id": "INC-1"}],
        "clients": [{
            "client_id": "C01", "name": "alpha",
            "lat": 34.10, "lon": -118.30,
            "eta_p10_s": p10, "eta_p50_s": p50, "eta_p90_s": p50 + 3600.0,
        }],
        "crews": [{
            "crew_id": "CR1", "name": "crew1",
            "track": [{"t_s": now, "lat": 34.11, "lon": -118.31, "status": "available"}],
        }],
        "safe_zones": [{"zone_id": "Z1", "lat": 34.05, "lon": -118.25, "name": "staging"}],
    }, now


def test_replan_console_uses_ember_inclusive_deadline():
    console, now = _console_with_one_client()
    plan = sim.replan_console(console, cfg=CFG_WIND, now_s=now)
    p10 = console["clients"][0]["eta_p10_s"]
    canonical_latest = safety_mod.latest_departure_s(p10, CFG_WIND)
    assert plan.plans, "expected the console client to be planned"
    for cp in plan.plans.values():
        for stop in cp.stops:
            # deadline stored on the stop must be the canonical ember-inclusive one
            assert stop.latest_depart_s == canonical_latest
            # and the buggy ember-omitting deadline must not appear
            assert stop.latest_depart_s != p10 - CFG_WIND.egress_buffer_min * 60.0


def test_replan_console_flags_plan_corridor_unvalidated():
    """The preview plan skips corridor checks (empty ensemble). It must be
    clearly flagged so a caller cannot mistake it for a safety-validated plan."""
    console, now = _console_with_one_client()
    plan = sim.replan_console(console, cfg=CFG_WIND, now_s=now)
    assert plan.objective_breakdown.get("corridor_validated") is False
    note = plan.objective_breakdown.get("safety_note", "")
    assert "corridor" in note.lower()


def test_replan_console_no_clients_still_flagged():
    """Even with nothing to plan, the flag must be present and honest."""
    console = {"now_s": 100.0, "t0_s": 100.0, "incidents": [{"incident_id": "X"}],
               "clients": [], "crews": [], "safe_zones": []}
    plan = sim.replan_console(console, cfg=CFG_WIND, now_s=100.0)
    assert plan.objective_breakdown.get("corridor_validated") is False
