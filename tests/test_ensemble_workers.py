"""ENSEMBLE WORKERS (2026-10-14; contracts ensemble_workers): the perturbed
members of spread.ensemble_arrival may run in worker processes. The founder
put crown fire and the spread-direction fix on the live watcher, which cost
1.3-2.3x and 2.7-3.4x the spread; the watcher solves its fires one at a time,
so its members run in parallel instead. The law: the ensemble is IDENTICAL to
the in-process run, cell for cell; only the wall time changes.
"""
from __future__ import annotations

import dataclasses

import pytest

from wgresponse import spread
from wgresponse.contracts import ResponseConfig, validate_config

from tests.test_spread import T0, center_ignition, const_wx, grass_tile


def _same(a, b):
    assert len(a.members) == len(b.members)
    for ma, mb in zip(a.members, b.members):
        assert ma.data == mb.data
    assert a.p10.data == b.p10.data
    assert a.p50.data == b.p50.data
    assert a.p90.data == b.p90.data


def test_the_default_is_in_process_and_the_knob_is_validated():
    assert ResponseConfig().ensemble_workers == 1
    assert validate_config(ResponseConfig(ensemble_workers=4)) == []
    for bad in (0, 65, True, 2.5):
        assert any("ensemble_workers" in p for p in
                   validate_config(ResponseConfig(ensemble_workers=bad)))


@pytest.mark.parametrize("extra", [
    {},
    {"spotting_enabled": True, "model_error_sd_log": 0.71},
    {"spotting_enabled": True, "spot_fire_size": True,
     "spread_any_angle": True},
])
def test_worker_members_are_the_in_process_members(extra):
    tile = grass_tile(nrows=31, ncols=31)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=9.0, wind_dir_deg=281.25, hours=30)
    cfg = ResponseConfig(ensemble_n=5, horizon_h=24.0, **extra)
    here = spread.ensemble_arrival(tile, [ign], wx, T0, cfg,
                                   incident_id="workers")
    there = spread.ensemble_arrival(
        tile, [ign], wx, T0, dataclasses.replace(cfg, ensemble_workers=2),
        incident_id="workers")
    _same(here, there)


def test_the_nominal_keeps_its_logs_in_the_caller():
    """Only the nominal writes ember_log / anchor_log; it runs in the caller,
    so the logs a re-anchor carries are the in-process ones."""
    tile = grass_tile(nrows=31, ncols=31)
    ign = center_ignition(tile)
    wx = const_wx(ign, wind_mps=9.0, hours=30)
    cfg = ResponseConfig(ensemble_n=4, horizon_h=24.0, spotting_enabled=True,
                         spread_any_angle=True)
    logs = []
    for workers in (1, 2):
        ember_log, anchor_log = [], {}
        spread.ensemble_arrival(
            tile, [ign], wx, T0,
            dataclasses.replace(cfg, ensemble_workers=workers),
            incident_id="logs", ember_log=ember_log, anchor_log=anchor_log)
        logs.append((ember_log, anchor_log))
    assert logs[0] == logs[1]


def test_a_broken_pool_falls_back_to_the_same_members(monkeypatch):
    tile = grass_tile(nrows=25, ncols=25)
    ign = center_ignition(tile)
    wx = const_wx(ign, hours=30)
    cfg = ResponseConfig(ensemble_n=4, horizon_h=24.0)
    here = spread.ensemble_arrival(tile, [ign], wx, T0, cfg)

    def boom(n):
        raise RuntimeError("no processes here")

    monkeypatch.setattr(spread, "_member_pool", boom)
    there = spread.ensemble_arrival(
        tile, [ign], wx, T0, dataclasses.replace(cfg, ensemble_workers=3))
    _same(here, there)


def test_watch_ensemble_workers_flag_parses():
    from wgresponse import cli
    aoi = "--aoi=-107,39,-106,40"
    args = cli._build_parser().parse_args(["watch", aoi,
                                           "--ensemble-workers", "3"])
    assert args.ensemble_workers == 3
    assert cli._build_parser().parse_args(["watch", aoi]).ensemble_workers == 1
