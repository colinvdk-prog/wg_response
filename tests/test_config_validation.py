"""Coverage for wgresponse.contracts.validate_config.

Tri-model review 2026-08-31 (docs/TRI_MODEL_REVIEW_2026-08-31.md; code finding 7,
scratch_review/rv2code_r7_quick.py / rv2code_VERDICTS.md) found that
validate_config accepted 22/22 deliberately unsound ResponseConfig values --
e.g. a NEGATIVE corridor_erosion_m silently DISABLES the corridor shield
(erode_min with radius<=0 does no erosion) instead of being rejected.

This file:
  - pins the shipped default (and a simulator-style config) as clean, so a
    future validate_config edit can never accidentally reject a real config;
  - ports every one of the review's 22 unsound cases as a must-reject case,
    each asserting the offending field's name appears in a returned problem
    string (scratch_review/rv2code_r7_quick.py:33-56 is the source list);
  - adds targeted coverage for the categories the review script didn't happen
    to probe: ordering/consistency constraints, enum-membership for the two
    dispatch strings, and bool/int type discipline (dataclasses-driven, so it
    exercises every bool- and int-typed field in ResponseConfig, not just a
    couple of examples).

Pure stdlib + pytest; no network; no fixtures needed beyond ResponseConfig.
"""
from __future__ import annotations

import dataclasses

import pytest

from wgresponse.contracts import ResponseConfig, validate_config


# ---------------------------------------------------------------------------
# Baseline: these must ALWAYS stay clean. If a future edit to validate_config
# breaks either of these, the edit is wrong, not the test.
# ---------------------------------------------------------------------------

def test_default_config_is_valid():
    """The shipped default must never be rejected."""
    assert validate_config(ResponseConfig()) == []


def test_validate_config_does_not_mutate_its_argument():
    cfg = ResponseConfig()
    before = dataclasses.asdict(cfg)
    validate_config(cfg)
    assert dataclasses.asdict(cfg) == before


def test_validate_config_returns_a_list_of_strings():
    problems = validate_config(ResponseConfig())
    assert isinstance(problems, list)
    bad = ResponseConfig(horizon_h=-1.0)
    problems = validate_config(bad)
    assert isinstance(problems, list) and problems
    assert all(isinstance(p, str) for p in problems)


def test_simulator_style_config_is_valid():
    """Shaped like what the simulator server actually arms in practice (SEM-10e
    proactive escape, transit-hazard stage 1, ember-threat radius, road spur
    cap, fail-closed corridors, grass ROS floor, spotting, gust blend, the
    ensemble model-error term, the P50 nominal bound, and the extinction-gated
    ROS floor). These are real ON knobs, not defaults -- validate_config must
    treat them as sound.
    """
    cfg = ResponseConfig(
        corridor_erosion_tiered=True,
        escape_hysteresis_margin_min=20,
        transit_cold_black_by_class=True,
        escape_proactive_lead_min=120,
        ember_threat_radius_m=200,
        road_spur_max_m=8000,
        corridor_fail_closed=True,
        grass_ros_floor_frac=0.10,
        spotting_enabled=True,
        wind_gust_blend=0.5,
        model_error_sd_log=0.7,
        ensemble_p50_nominal_bound=True,
        ros_floor_extinction_gate=True,
    )
    assert validate_config(cfg) == []


# ---------------------------------------------------------------------------
# Ported from the tri-model review's validate_config probe
# (scratch_review/rv2code_r7_quick.py:33-56 -- the exact 22 cases that were
# "ACCEPTED" before this fix, per rv2code_VERDICTS.md finding 7: "22/22
# unsound configs accepted"). Every one must now be REJECTED.
# ---------------------------------------------------------------------------

REVIEW_UNSOUND_CASES = [
    pytest.param("horizon_h", dict(horizon_h=0.0),
                 id="r01_horizon_h_zero"),
    pytest.param("horizon_h", dict(horizon_h=-5.0),
                 id="r02_horizon_h_negative"),
    pytest.param("wind_gust_blend", dict(wind_gust_blend=5.0),
                 id="r03_wind_gust_blend_500pct"),
    pytest.param("wind_gust_blend", dict(wind_gust_blend=-3.0),
                 id="r04_wind_gust_blend_negative"),
    pytest.param("circuity", dict(circuity=0.1),
                 id="r05_circuity_shorter_than_chord"),
    pytest.param("circuity", dict(circuity=0.0),
                 id="r06_circuity_zero_div_by_zero"),
    pytest.param("road_speed_mps", dict(road_speed_mps=0.0),
                 id="r07_road_speed_mps_zero"),
    pytest.param("route_margin_min", dict(route_margin_min=-60.0),
                 id="r08_route_margin_min_negative"),
    pytest.param("corridor_erosion_m", dict(corridor_erosion_m=-500.0),
                 id="r09_corridor_erosion_m_negative_dilates"),
    pytest.param("corridor_sample_m", dict(corridor_sample_m=100000.0),
                 id="r10_corridor_sample_m_one_sample_per_leg"),
    pytest.param("gel_effectiveness", dict(gel_effectiveness=5.0),
                 id="r11_gel_effectiveness_500pct"),
    pytest.param("gel_effectiveness", dict(gel_effectiveness=-1.0),
                 id="r12_gel_effectiveness_negative"),
    pytest.param("wind_perturb_frac", dict(wind_perturb_frac=10.0),
                 id="r13_wind_perturb_frac_1000pct"),
    pytest.param("wind_perturb_frac", dict(wind_perturb_frac=-0.25),
                 id="r14_wind_perturb_frac_inverted"),
    pytest.param("dir_perturb_deg", dict(dir_perturb_deg=400.0),
                 id="r15_dir_perturb_deg_400"),
    pytest.param("grass_ros_floor_frac", dict(grass_ros_floor_frac=3.0),
                 id="r16_grass_ros_floor_frac_3x_wind"),
    pytest.param("live_herb_frac", dict(live_herb_frac=-1.0),
                 id="r17_live_herb_frac_negative"),
    pytest.param("grid_max_cells", dict(grid_max_cells=1),
                 id="r18_grid_max_cells_one_cell_domain"),
    pytest.param("slack_min", dict(slack_min=-100.0),
                 id="r19_slack_min_negative"),
    pytest.param("egress_buffer_min", dict(egress_buffer_min=1e9),
                 id="r20_egress_buffer_min_1e9"),
    pytest.param("fire_traffic_factor", dict(fire_traffic_factor=0.0),
                 id="r21_fire_traffic_factor_zero"),
    pytest.param("extreme_p10_h", dict(extreme_p10_h=999.0),
                 id="r22_extreme_p10_h_exceeds_extreme_and_notice"),
]


def test_all_22_review_cases_are_ported():
    """Guards the port itself against an accidental drop -- the review's
    finding was specifically 22/22, not "some"."""
    assert len(REVIEW_UNSOUND_CASES) == 22


@pytest.mark.parametrize("field_name,override", REVIEW_UNSOUND_CASES)
def test_review_unsound_case_now_rejected(field_name, override):
    cfg = ResponseConfig(**override)
    problems = validate_config(cfg)
    assert problems, f"{override} should be rejected but validated clean"
    assert any(field_name in p for p in problems), (
        f"expected a problem naming {field_name!r}, got {problems}")


# ---------------------------------------------------------------------------
# Task-listed knobs the review script didn't happen to probe: newer knobs and
# the "safety margins ... >= 0" set, checked directly against contracts.py's
# documented meaning for each field.
# ---------------------------------------------------------------------------

DERIVED_RANGE_CASES = [
    pytest.param("notice_h", dict(notice_h=0.0), id="notice_h_zero"),
    pytest.param("deploy_min", dict(deploy_min=0.0), id="deploy_min_zero"),
    pytest.param("route_margin_roads_min", dict(route_margin_roads_min=-1.0),
                 id="route_margin_roads_min_negative"),
    pytest.param("ember_buffer_min", dict(ember_buffer_min=-1.0),
                 id="ember_buffer_min_negative"),
    pytest.param("corridor_offraster_ros_mps", dict(corridor_offraster_ros_mps=0.0),
                 id="corridor_offraster_ros_mps_zero"),
    pytest.param("route_alternatives", dict(route_alternatives=0),
                 id="route_alternatives_zero"),
    pytest.param("escape_hysteresis_margin_min", dict(escape_hysteresis_margin_min=-5.0),
                 id="escape_hysteresis_margin_min_negative"),
    pytest.param("escape_proactive_lead_min", dict(escape_proactive_lead_min=-5.0),
                 id="escape_proactive_lead_min_negative"),
    pytest.param("escape_road_snap_m", dict(escape_road_snap_m=-1.0),
                 id="escape_road_snap_m_negative"),
    pytest.param("escape_road_horizon_cap_h", dict(escape_road_horizon_cap_h=0.0),
                 id="escape_road_horizon_cap_h_zero"),
    pytest.param("ember_threat_radius_m", dict(ember_threat_radius_m=-1.0),
                 id="ember_threat_radius_m_negative"),
    pytest.param("road_spur_max_m", dict(road_spur_max_m=-1.0),
                 id="road_spur_max_m_negative"),
    pytest.param("history_ticks", dict(history_ticks=-1),
                 id="history_ticks_negative"),
    pytest.param("poll_period_s", dict(poll_period_s=0.0), id="poll_period_s_zero"),
    pytest.param("replan_period_s", dict(replan_period_s=0.0), id="replan_period_s_zero"),
    pytest.param("solver_budget_s", dict(solver_budget_s=-1.0), id="solver_budget_s_negative"),
    pytest.param("ils_iterations", dict(ils_iterations=-1), id="ils_iterations_negative"),
    pytest.param("beacon_stale_s", dict(beacon_stale_s=0.0), id="beacon_stale_s_zero"),
    pytest.param("registry_ttl_s", dict(registry_ttl_s=-1.0), id="registry_ttl_s_negative"),
    pytest.param("incident_stale_h", dict(incident_stale_h=0.0), id="incident_stale_h_zero"),
    pytest.param("crew_shift_max_on_h", dict(crew_shift_max_on_h=-1.0),
                 id="crew_shift_max_on_h_negative"),
    pytest.param("crew_shift_rest_h", dict(crew_shift_rest_h=-1.0),
                 id="crew_shift_rest_h_negative"),
    pytest.param("model_error_sd_log", dict(model_error_sd_log=5.0),
                 id="model_error_sd_log_over_3"),
    pytest.param("model_error_sd_log", dict(model_error_sd_log=-0.1),
                 id="model_error_sd_log_negative"),
]


@pytest.mark.parametrize("field_name,override", DERIVED_RANGE_CASES)
def test_derived_range_case_rejected(field_name, override):
    cfg = ResponseConfig(**override)
    problems = validate_config(cfg)
    assert problems, f"{override} should be rejected but validated clean"
    assert any(field_name in p for p in problems), (
        f"expected a problem naming {field_name!r}, got {problems}")


# ---------------------------------------------------------------------------
# Ordering / consistency constraints between paired knobs.
# ---------------------------------------------------------------------------

def test_gel_full_strength_h_must_not_exceed_gel_window_h():
    cfg = ResponseConfig(gel_window_h=48.0, gel_full_strength_h=72.0)
    problems = validate_config(cfg)
    assert any("gel_full_strength_h" in p for p in problems)


def test_crew_reinforce_1h_must_not_exceed_3h():
    cfg = ResponseConfig(crew_reinforce_1h_min=200.0, crew_reinforce_3h_min=100.0)
    problems = validate_config(cfg)
    assert any("crew_reinforce_1h_min" in p for p in problems)


def test_containment_watch_ceiling_pct_bounds():
    over = validate_config(ResponseConfig(containment_watch_ceiling_pct=150.0))
    assert any("containment_watch_ceiling_pct" in p for p in over)
    under = validate_config(ResponseConfig(containment_watch_ceiling_pct=-1.0))
    assert any("containment_watch_ceiling_pct" in p for p in under)


# ---------------------------------------------------------------------------
# Enum-like strings: dispatch_objective in {'homes','value'} (contracts.py
# FOUNDER ROUND 3 point 4) and dispatch_sequence in {'auto','fire_first',
# 'urgency'} (contracts.py FOUNDER ROUND 6; the accepted set is enforced
# independently at the API boundary in server.py:390).
# ---------------------------------------------------------------------------

def test_dispatch_objective_rejects_unknown_value():
    problems = validate_config(ResponseConfig(dispatch_objective="acreage"))
    assert any("dispatch_objective" in p for p in problems)


@pytest.mark.parametrize("value", ["homes", "value"])
def test_dispatch_objective_accepts_documented_values(value):
    problems = validate_config(ResponseConfig(dispatch_objective=value))
    assert not any("dispatch_objective" in p for p in problems)


def test_dispatch_sequence_rejects_unknown_value():
    problems = validate_config(ResponseConfig(dispatch_sequence="fastest_first"))
    assert any("dispatch_sequence" in p for p in problems)


@pytest.mark.parametrize("value", ["auto", "fire_first", "urgency"])
def test_dispatch_sequence_accepts_documented_values(value):
    problems = validate_config(ResponseConfig(dispatch_sequence=value))
    assert not any("dispatch_sequence" in p for p in problems)


# ---------------------------------------------------------------------------
# Bool/int type discipline (task category: "bool-typed fields must be bools
# and int-typed fields ints"). Driven off dataclasses.fields() + the
# annotations, so this exercises every bool- and int-typed field in
# ResponseConfig, not just a couple of hand-picked examples.
# ---------------------------------------------------------------------------

_ALL_FIELDS = dataclasses.fields(ResponseConfig)
BOOL_FIELD_NAMES = [f.name for f in _ALL_FIELDS if f.type == "bool"]
INT_FIELD_NAMES = [f.name for f in _ALL_FIELDS if f.type == "int"]


def test_bool_and_int_field_sets_are_nonempty():
    """Sanity check on the introspection itself -- if this ever comes back
    empty, the type-discipline tests below would silently stop testing
    anything (dataclasses field .type is a string here because contracts.py
    uses `from __future__ import annotations`)."""
    assert len(BOOL_FIELD_NAMES) >= 10
    assert len(INT_FIELD_NAMES) >= 5


@pytest.mark.parametrize("field_name", BOOL_FIELD_NAMES)
def test_bool_field_rejects_non_bool_value(field_name):
    # 1 (a plain int) is truthy and numerically equal to True, but it is not
    # a bool instance -- exactly the kind of value a JSON/CLI config loader
    # can hand back for what should be a flag.
    cfg = ResponseConfig(**{field_name: 1})
    problems = validate_config(cfg)
    assert any(field_name in p for p in problems), (
        f"{field_name}=1 (int, not bool) should be rejected, got {problems}")


@pytest.mark.parametrize("field_name", INT_FIELD_NAMES)
def test_int_field_rejects_float_value(field_name):
    cfg = ResponseConfig(**{field_name: 2.5})
    problems = validate_config(cfg)
    assert any(field_name in p for p in problems), (
        f"{field_name}=2.5 (float, not int) should be rejected, got {problems}")


@pytest.mark.parametrize("field_name", INT_FIELD_NAMES)
def test_int_field_rejects_bool_value(field_name):
    # bool is an int subclass in Python; True/False must still be rejected
    # for a field that is documented as a count, not a flag.
    cfg = ResponseConfig(**{field_name: True})
    problems = validate_config(cfg)
    assert any(field_name in p for p in problems), (
        f"{field_name}=True (bool, not int) should be rejected, got {problems}")
