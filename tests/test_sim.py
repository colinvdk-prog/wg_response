"""Tests for wgresponse.sim — deterministic, no network, no third-party deps.

Sim runs use use_siblings=False (internal fallbacks) or explicit stub stacks
so these tests stay hermetic while sibling modules land concurrently.
Grids are tiny and cycle counts small; full-fidelity runs happen in the Demo
phase, not pytest.
"""
import json
import math
from pathlib import Path

import pytest

from wgresponse import sim
from wgresponse.contracts import (
    UNREACHED,
    AlertState,
    EnsembleArrival,
    Raster,
    ResponseConfig,
    assert_valid_transition,
)

CFG_FAST = ResponseConfig(ensemble_n=2)


# ---------------------------------------------------------------------------
# Scenario construction
# ---------------------------------------------------------------------------

def test_scenario_registry_counts():
    canyon = sim.build_scenario("canyon_wind_event", seed="s1")
    assert len(canyon.clients) == 12
    assert len(canyon.crews) == 3
    assert canyon.safe_zones, "canyon scenario must ship safe zones"
    creeper = sim.build_scenario("slow_creeper")
    assert len(creeper.clients) == 6
    assert len(creeper.crews) == 2


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        sim.build_scenario("does_not_exist")


def test_build_scenario_deterministic_by_seed():
    a = sim.build_scenario("canyon_wind_event", seed="s1")
    b = sim.build_scenario("canyon_wind_event", seed="s1")
    c = sim.build_scenario("canyon_wind_event", seed="s2")
    coords = lambda sc: [(cl.loc.lat, cl.loc.lon) for cl in sc.clients]
    assert coords(a) == coords(b)
    assert coords(a) != coords(c)
    assert a.t0_s == b.t0_s == c.t0_s  # t0 fixed per scenario, not per seed


def test_wind_event_script_shape():
    sc = sim.build_scenario("canyon_wind_event", seed="x")
    samples = sc.weather_script
    assert samples[0].wind_speed_mps == pytest.approx(2.0)   # calm start
    assert samples[14].wind_speed_mps == pytest.approx(15.0)  # inside event (12-20 h)
    assert samples[14].wind_dir_deg == pytest.approx(270.0)   # FROM west -> pushes east
    assert samples[30].wind_speed_mps < 15.0                  # event over
    ts = [s.t_s for s in samples]
    assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# Fallback spread engine sanity
# ---------------------------------------------------------------------------

def test_fallback_ensemble_quantiles_ordered_and_downwind_faster():
    sc = sim.build_scenario("tiny", seed="q")
    stack = sim.default_stack(sc, CFG_FAST, use_siblings=False)
    tile = stack.terrain.tile(sc.bbox, sc.spec.cell_m)
    wx = stack.weather.forecast(sc.ignition, CFG_FAST.horizon_h)
    ens = stack.ensemble_fn(tile, [sc.ignition], wx, sc.t0_s, CFG_FAST)
    for cl in sc.clients:
        eta = stack.eta_fn(ens, cl.loc)
        assert eta.eta_p10_s <= eta.eta_p50_s <= eta.eta_p90_s
        assert eta.eta_p10_s >= sc.t0_s
    # wind FROM 270 pushes east: downwind (east) point burns before upwind (west)
    east = sim._destination(sc.ignition, 90.0, 1_000.0)
    west = sim._destination(sc.ignition, 270.0, 1_000.0)
    eta_e = stack.eta_fn(ens, east)
    eta_w = stack.eta_fn(ens, west)
    assert eta_e.eta_p50_s < eta_w.eta_p50_s


# ---------------------------------------------------------------------------
# Determinism: same seed twice -> byte-identical report + console JSON
# ---------------------------------------------------------------------------

def test_run_deterministic_json(tmp_path):
    r1 = sim.run(sim.build_scenario("tiny", seed="det"), hours=2, cfg=CFG_FAST,
                 out_dir=tmp_path / "a", use_siblings=False)
    r2 = sim.run(sim.build_scenario("tiny", seed="det"), hours=2, cfg=CFG_FAST,
                 out_dir=tmp_path / "b", use_siblings=False)
    assert Path(r1.report_path).read_bytes() == Path(r2.report_path).read_bytes()
    assert Path(r1.console_path).read_bytes() == Path(r2.console_path).read_bytes()
    assert r1.cycles_run == r2.cycles_run > 0


# ---------------------------------------------------------------------------
# canyon_wind_event with stubbed-fast spread: alerts fire, safety holds
# ---------------------------------------------------------------------------

def _stub_ensemble(tile, ignition, wx, t0_s, cfg):
    """Analytic arrival stub: fast eastward sector (0.12 m/s), slow elsewhere.
    p10/p90 are +/-20% time-scalings of the p50 surface. No RNG, no Dijkstra."""
    spec = tile.spec
    ign = ignition[0]

    def surface(scale):
        data = []
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                center = sim._cell_center(spec, r, c)
                d = sim._haversine_m(ign, center)
                brg = sim._bearing_deg(ign, center)
                off_axis = abs((brg - 90.0 + 180.0) % 360.0 - 180.0)
                speed = 0.12 if off_axis <= 60.0 else 0.02
                data.append(t0_s + scale * d / speed)
        return Raster(spec, data)

    p50 = surface(1.0)
    p10 = surface(0.8)
    p90 = surface(1.25)
    return EnsembleArrival(spec=spec, members=[p10, p50, p90],
                           p10=p10, p50=p50, p90=p90, t0_s=t0_s)


def test_canyon_wind_event_alerts_and_zero_violations(tmp_path):
    sc = sim.build_scenario("canyon_wind_event", seed="test")
    stack = sim.default_stack(sc, CFG_FAST, use_siblings=False)
    stack.ensemble_fn = _stub_ensemble
    report = sim.run(sc, hours=8, cfg=CFG_FAST, stack=stack, out_dir=tmp_path)

    states = [ev.state for ev in report.events]
    assert AlertState.NOTICE_72H in states, "expected >=1 NOTICE event"
    assert AlertState.EXTREME_24H in states, "expected >=1 EXTREME event"
    # every emitted transition must be ladder-legal
    for ev in report.events:
        assert_valid_transition(ev.prev, ev.state)
    # hard safety: no serviced stop departs after (p10 - egress buffer)
    assert report.violations == []
    assert len(report.serviced) >= 1, "crews should complete at least one stop"
    assert report.min_margin_h is not None and report.min_margin_h > 0.0
    for stop in report.serviced:
        assert stop.depart_s <= stop.latest_depart_s
        assert stop.depart_s - stop.service_start_s == pytest.approx(CFG_FAST.deploy_min * 60.0)
    # unserved entries all carry a reason
    assert all(u["reason"] for u in report.unserved)


def test_slow_creeper_runs_clean(tmp_path):
    report = sim.run(sim.build_scenario("slow_creeper", seed="sc"), hours=3,
                     cfg=CFG_FAST, out_dir=tmp_path, use_siblings=False)
    assert report.violations == []
    assert report.cycles_run == int(3 * 3600 / CFG_FAST.replan_period_s)


# ---------------------------------------------------------------------------
# Report + console JSON shapes
# ---------------------------------------------------------------------------

def test_report_json_shape_and_no_infinity(tmp_path):
    report = sim.run(sim.build_scenario("tiny", seed="shape"), hours=2,
                     cfg=CFG_FAST, out_dir=tmp_path, use_siblings=False)
    text = Path(report.report_path).read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    data = json.loads(text)
    for key in ("scenario", "seed", "t0_s", "cycles_run", "events", "serviced",
                "unserved", "violations", "audit", "final_states",
                "stack_provenance", "schema_version"):
        assert key in data, f"report missing {key}"
    assert data["violations"] == []
    # internal fallbacks were forced
    assert all(v == "sim-internal" for v in data["stack_provenance"].values())
    # events serialized with enum values as strings
    for ev in data["events"]:
        assert isinstance(ev["state"], str)


def test_console_json_shape(tmp_path):
    report = sim.run(sim.build_scenario("tiny", seed="console"), hours=2,
                     cfg=CFG_FAST, out_dir=tmp_path, use_siblings=False)
    text = Path(report.console_path).read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    data = json.loads(text)
    for key in ("bbox", "ignition", "isochrones", "clients", "crews", "routes",
                "safe_zones", "events", "t0_s", "now_s", "cycle_period_s"):
        assert key in data, f"console JSON missing {key}"
    assert data["bbox"]["west"] < data["bbox"]["east"]
    assert data["bbox"]["south"] < data["bbox"]["north"]
    for cl in data["clients"]:
        assert {"client_id", "lat", "lon", "state"} <= set(cl)
        assert isinstance(cl["state"], str)
    for cr in data["crews"]:
        assert cr["track"], "each crew must have a beacon track"
        assert {"t_s", "lat", "lon", "status"} <= set(cr["track"][0])
    for iso in data["isochrones"]:
        assert iso["h"] in (24.0, 48.0, 72.0)
        assert len(iso["points"]) >= 4


def test_audit_margins_match_p10_rule(tmp_path):
    """Audit margin must equal (p10 - egress_buffer - depart) per contracts."""
    sc = sim.build_scenario("canyon_wind_event", seed="audit")
    stack = sim.default_stack(sc, CFG_FAST, use_siblings=False)
    stack.ensemble_fn = _stub_ensemble
    report = sim.run(sc, hours=8, cfg=CFG_FAST, stack=stack, out_dir=tmp_path)
    assert report.audit, "expected audited stops"
    for a in report.audit:
        if math.isinf(a.eta_p10_s):
            continue
        expected = (a.eta_p10_s - CFG_FAST.egress_buffer_min * 60.0 - a.depart_s) / 3600.0
        assert a.margin_h == pytest.approx(expected)
        assert a.violation == (a.margin_h < 0.0)


# ---------------------------------------------------------------------------
# jsonable helper
# ---------------------------------------------------------------------------

def test_jsonable_handles_enums_inf_and_tuple_keys():
    obj = {
        ("C01", "inc-1"): AlertState.EXTREME_24H,
        "eta": UNREACHED,
        "nested": [AlertState.NONE, float("nan"), 1.5],
    }
    out = sim.jsonable(obj)
    assert out["C01|inc-1"] == "extreme_24h"
    assert out["eta"] is None
    assert out["nested"] == ["none", None, 1.5]
    json.dumps(out)  # round-trips as strict JSON
