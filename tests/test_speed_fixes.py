"""tests/test_speed_fixes.py — the live speed fixes (founder 2026-10-30,
"build and redeploy"): DOMAIN SNAP (contracts forecast_domain_snap_deg,
SAFETY SEMANTICS 42; watch --snap-domains), THE TERRAIN TURN (SAFETY
SEMANTICS 40: a learning forecast starts only while the cycle is not
assessing) and the dispatch planner with no eligible crew (no corridor
erosion; the same plan).

  SNAP      0 = the exact padded bbox; > 0 = snapped outward to the grid,
            holding the legacy domain and under one step larger a side; a
            small move of the fire keeps the snapped domain (one terrain
            tile) where the exact bbox moves; validation; the watch flag.
  TURN      the cycle holds the turn while it assesses (only with learning
            armed) and frees it after, even when interrupted; a learning
            forecast waits for it.
  DISPATCH  no eligible crew = no erosion and the plan of the eroded-input
            path; a crew on duty still erodes."""
from __future__ import annotations

import dataclasses
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from tests.test_dispatch import (  # noqa: E402
    INC,
    cc,
    make_crew,
    make_ens,
    make_job,
    spec20,
    uniform_raster,
)
from tests.test_dispatch import NOW as DNOW  # noqa: E402
from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, dispatch, geo, safety, threat  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    Crew,
    CrewStatus,
    ResponseConfig,
    SafeZone,
    validate_config,
)


# ---------------------------------------------------------------------------
# SNAP
# ---------------------------------------------------------------------------

def _domains(cfg, incs):
    """The terrain bbox each cycle asked for, one cycle per incident."""
    terr = ss.StubTerrain()
    feed = StubFeed([])
    mon = make_monitor(feeds=[feed], terrain=terr, cfg=cfg,
                       registry=StubRegistry([near_client()]))
    out = []
    for k, inc in enumerate(incs):
        feed._incidents = [inc]
        n = len(terr.calls)
        mon.cycle(NOW + 300.0 * k)
        out.append({b for b, _cell in terr.calls[n:]})
    return out


def _on_grid(v, step):
    return abs(v / step - round(v / step)) < 1e-6


def test_off_the_domain_is_the_exact_padded_bbox():
    (got,) = _domains(make_cfg(), [incident()])
    assert got == {geo.bbox_around([ORIGIN, near_client().loc],
                                   threat._DOMAIN_PAD_M)}


def test_on_the_domain_is_snapped_outward_and_holds_the_legacy_one():
    step = 0.05
    (got,) = _domains(make_cfg(forecast_domain_snap_deg=step), [incident()])
    (bb,) = got
    exact = geo.bbox_around([ORIGIN, near_client().loc], threat._DOMAIN_PAD_M)
    assert all(_on_grid(v, step)
               for v in (bb.west, bb.south, bb.east, bb.north))
    assert (bb.west <= exact.west and bb.south <= exact.south
            and bb.east >= exact.east and bb.north >= exact.north)
    assert (exact.west - bb.west < step and exact.south - bb.south < step
            and bb.east - exact.east < step and bb.north - exact.north < step)


def test_a_small_move_keeps_the_snapped_domain():
    moved = dataclasses.replace(incident(),
                                origin=geo.destination(ORIGIN, 0.0, 100.0))
    a, b = _domains(make_cfg(forecast_domain_snap_deg=0.05),
                    [incident(), moved])
    assert a == b                                   # one terrain tile
    a, b = _domains(make_cfg(), [incident(), moved])
    assert a != b                                   # the exact bbox moved


def test_the_learning_domain_shares_the_snap():
    bb = geo.bbox_around([ORIGIN], 10_000.0)
    assert threat._snap_bbox(bb, 0.0) is bb
    s = threat._snap_bbox(bb, 0.05)
    assert all(_on_grid(v, 0.05) for v in (s.west, s.south, s.east, s.north))
    mon = make_monitor(cfg=make_cfg(fire_belief_track=True,
                                    fire_belief_learn=True))
    assert mon._learn_domain([ORIGIN]) == s


def test_snap_config_validation_and_the_watch_flag():
    cfg = ResponseConfig()
    assert cfg.forecast_domain_snap_deg == 0.0 and validate_config(cfg) == []
    for v in (-0.01, 1.5, float("nan")):
        probs = validate_config(dataclasses.replace(
            cfg, forecast_domain_snap_deg=v))
        assert any("forecast_domain_snap_deg" in p for p in probs), v
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--snap-domains"])
    got, notes = cli._heat_snap_cfg(cfg, args)
    assert got.forecast_domain_snap_deg == 0.05 and not got.hotspot_heat_only
    assert len(notes) == 1 and notes[0].startswith("DOMAIN SNAP")
    assert "SAFETY SEMANTICS 42" in notes[0]


# ---------------------------------------------------------------------------
# TURN
# ---------------------------------------------------------------------------

def _turn_seen(cfg):
    mon = make_monitor(feeds=[StubFeed([incident()])], cfg=cfg,
                       registry=StubRegistry([near_client()]))
    seen = []
    real = mon._assess_incident

    def spy(*a, **k):
        seen.append(mon._terrain_turn.held)
        return real(*a, **k)
    mon._assess_incident = spy
    mon.cycle(NOW)
    return mon, seen


def test_the_cycle_holds_the_turn_while_it_assesses():
    mon, seen = _turn_seen(make_cfg(fire_belief_track=True,
                                    fire_belief_learn=True))
    assert seen == [True] and not mon._terrain_turn.held
    mon, seen = _turn_seen(make_cfg())            # no learning: never held
    assert seen == [False] and not mon._terrain_turn.held


def test_the_turn_is_freed_when_the_cycle_is_interrupted():
    mon = make_monitor(feeds=[StubFeed([incident()])],
                       cfg=make_cfg(fire_belief_track=True,
                                    fire_belief_learn=True),
                       registry=StubRegistry([near_client()]))

    def interrupt(*a, **k):
        raise KeyboardInterrupt
    mon._assess_incident = interrupt
    with pytest.raises(KeyboardInterrupt):
        mon.cycle(NOW)
    assert not mon._terrain_turn.held


def test_a_learning_forecast_starts_only_when_the_turn_is_free():
    mon = make_monitor(cfg=make_cfg(fire_belief_track=True,
                                    fire_belief_learn=True))
    started = threading.Event()

    def inputs(*a, **k):
        started.set()
        raise RuntimeError("inputs reached")
    mon._forecast_inputs = inputs
    mon._terrain_turn.hold()
    fut = mon._learn_pool().submit(mon._learn_forecast, incident(), NOW,
                                   [ORIGIN], None, None, None, [ORIGIN],
                                   [ORIGIN])
    assert not started.wait(0.3)                  # the cycle is assessing
    mon._terrain_turn.release()
    assert started.wait(30)
    assert "inputs reached" in str(fut.exception(timeout=30))


# ---------------------------------------------------------------------------
# DISPATCH
# ---------------------------------------------------------------------------

def test_no_eligible_crew_means_no_erosion_and_the_same_plan(monkeypatch):
    cfg = ResponseConfig()
    spec = spec20()
    ens = {INC: make_ens(uniform_raster(spec, DNOW + 5 * 3600.0))}
    jobs = [make_job("c1", cc(spec, 10, 4), DNOW + 8 * 3600.0,
                     DNOW + 10 * 3600.0),
            make_job("c2", cc(spec, 12, 6), DNOW + 6 * 3600.0,
                     DNOW + 9 * 3600.0)]
    off = Crew(crew_id="A", status=CrewStatus.OFF_DUTY,
               home_base=cc(spec, 10, 2))
    zones = [SafeZone("z1", cc(spec, 10, 9))]
    # the reference: eroded rasters handed in, so no path erodes anything
    ref = dispatch.plan([off], jobs, ens, zones, cfg, DNOW,
                        eroded_rasters={INC: ens[INC].p10})
    calls = []
    real = safety._erode

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(safety, "_erode", counting)
    got = dispatch.plan([off], jobs, ens, zones, cfg, DNOW)
    assert calls == []
    assert got.plans == ref.plans == {}
    assert [(u.client_id, u.reason) for u in got.unserved] == \
        [(u.client_id, u.reason) for u in ref.unserved] == \
        [("c1", "capacity"), ("c2", "capacity")]

    def strip(b):
        return {k: v for k, v in b.items() if k != "solver_s"}
    assert strip(got.objective_breakdown) == strip(ref.objective_breakdown)
    dispatch.plan([make_crew("B", cc(spec, 10, 2))], jobs, ens, zones, cfg,
                  DNOW)
    assert calls                                   # a crew on duty erodes
