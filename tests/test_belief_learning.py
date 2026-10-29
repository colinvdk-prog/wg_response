"""tests/test_belief_learning.py — LEARNING FORECASTS (founder 2026-10-29,
"perform fix 1"; contracts fire_belief_learn, SAFETY SEMANTICS 40; watch
--fire-belief-learn).

The fire belief (SEMANTICS 14) learns a fire's spread rate by comparing
each reported growth with the forecast the engine made at the window's
start — and the engine forecast only fires with a protected client within
60 km (13 of 197 live fires on 2026-10-29; 2 had learned). With the knob,
a fire WITHOUT a client domain gets a learning forecast whenever the belief
holds an observation no curve is pinned to: the nominal member only, over
the fire's own geometry padded fire_belief_learn_pad_m and snapped outward
to fire_belief_learn_snap_deg (the terrain cache key), on one background
worker; the next cycle pins it — before that cycle's observations, and
only to the observation it was made for.

  OFF        nothing runs for a fire without clients (the legacy belief).
  LEARNS     a point fire 150 km from every client learns from acreage.
  SCOPE      domain fires, FIRMS and seeded fires are never queued.
  STALE      a forecast that lands after a newer observation is dropped
             and the newer one gets its own.
  FAILURE    a failed learning forecast is a note, never a provider error.
  SHAPE      nominal only; the snapped, padded domain.
  THREADS    a background thread's terrain fallbacks are its own.
  SURFACE    validation, the watch flag, the status."""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import cli, geo, terrain  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    BBox,
    FireIncident,
    IncidentSource,
    validate_config,
)
from wgresponse.threat import ThreatMonitor  # noqa: E402

H = ss.H
T0 = ss.T0
FAR = ss.FAR_SEED_LOC          # ~150 km east of both clients: no domain
TILES = Path(__file__).resolve().parent.parent / "data" / "tiles"


def point(acres, t, iid="pt", loc=FAR, source=IncidentSource.MOCK):
    return FireIncident(incident_id=iid, name="Pt", source=source,
                        origin=loc, updated_at_s=t - 60.0, acreage=acres,
                        active=True, discovered_at_s=T0 - 30 * 24 * H)


def _cfg(**kw):
    return ss.small_cfg(horizon_h=24.0, fire_belief_track=True, **kw)


def _mon(cfg, feed, terr=None):
    return ThreatMonitor(feeds=[feed],
                         weather_provider=ss.StubWeather(wind_mps=4.0),
                         terrain_provider=terr or ss.StubTerrain(),
                         registry=ss.StubRegistry(ss.clients()),
                         alert_engine=AlertEngine(cfg), cfg=cfg, safe_zones=[])


def _settle(mon):
    """Wait for the learning forecasts in flight (a failed one included:
    the next cycle collects it)."""
    for job in list(mon._learning.values()):
        job.future.exception(timeout=120)


def _run(cfg, sizes=(500.0, 505.0, 510.0, 515.0), loc=FAR, terr=None,
         source=IncidentSource.MOCK):
    feed = ss.ScriptedFeed([])
    terr = terr or ss.StubTerrain()
    mon = _mon(cfg, feed, terr)
    res = None
    for k, acres in enumerate(sizes):
        t = T0 + k * 4 * H
        feed.incidents = [point(acres, t, loc=loc, source=source)]
        res = mon.cycle(t)
        _settle(mon)
    return mon, res, terr


def test_off_a_fire_without_clients_never_learns():
    mon, res, terr = _run(_cfg())
    st = res.fire_status["pt"]
    assert st.state == "prior" and st.n_obs == 0
    assert terr.calls == [] and mon._learning == {}
    assert not any("learning" in n for n in res.notes)


def test_a_point_fire_without_clients_learns_from_acreage():
    mon, res, terr = _run(_cfg(fire_belief_learn=True))
    st = res.fire_status["pt"]
    assert st.state == "learned" and st.n_obs == 3
    assert st.mult_p50 < 0.5 and not st.rapid    # slow vs 4 m/s grass
    assert "pt" not in res.ensembles             # learning only: no domain
    assert not [a for a in res.assessments if a.incident_id == "pt"]
    assert any(n.startswith("fire belief learning:") and "kept 1" in n
               for n in res.notes)
    assert res.timings["learning"]["kept"] == 1
    assert not res.degraded


def test_the_domain_is_the_fire_padded_and_snapped_and_nominal_only():
    seen = []
    orig = ThreatMonitor._forecast_spread

    def spy(inc, b, seeds, perimeter, now_s):
        seen.append(b.spread_cfg.ensemble_n)
        return orig(inc, b, seeds, perimeter, now_s)
    try:
        ThreatMonitor._forecast_spread = staticmethod(spy)
        _mon_, _res, terr = _run(_cfg(fire_belief_learn=True), sizes=(500.0,))
    finally:
        ThreatMonitor._forecast_spread = staticmethod(orig)
    assert seen == [1]
    (bbox, cell_m), = terr.calls
    snap = 0.05
    for v in (bbox.west, bbox.south, bbox.east, bbox.north):
        assert abs(v / snap - round(v / snap)) < 1e-6
    inner = geo.bbox_around([FAR], 10_000.0)
    assert (bbox.west <= inner.west and bbox.south <= inner.south
            and bbox.east >= inner.east and bbox.north >= inner.north)
    assert bbox.east - bbox.west < inner.east - inner.west + 2 * snap + 1e-9


def test_domain_fires_firms_and_seeded_fires_are_never_queued():
    cfg = _cfg(fire_belief_learn=True)
    mon, res, _ = _run(cfg, loc=ss.ORIGIN)          # clients in range
    assert mon._learning == {} and "pt" in res.ensembles
    assert res.fire_status["pt"].state == "learned"  # its own domain
    mon, res, terr = _run(cfg, source=IncidentSource.FIRMS)
    assert mon._learning == {} and terr.calls == []
    feed = ss.ScriptedFeed([])
    mon = _mon(cfg, feed)
    seeded = dataclasses.replace(point(500.0, T0), incident_id="seeded:x",
                                 source=IncidentSource.SEEDED)
    assert not mon._learn_candidate(seeded, {})


class GatedTerrain(ss.StubTerrain):
    """Blocks every tile() until released (a learning forecast in flight)."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()

    def tile(self, bbox, cell_m):
        assert self.gate.wait(60)
        return super().tile(bbox, cell_m)


def test_a_forecast_landing_after_a_newer_observation_is_dropped():
    cfg = _cfg(fire_belief_learn=True)
    feed = ss.ScriptedFeed([])
    terr = GatedTerrain()
    mon = _mon(cfg, feed, terr)
    feed.incidents = [point(500.0, T0)]
    mon.cycle(T0)                                   # job for obs 1, blocked
    feed.incidents = [point(505.0, T0 + 4 * H)]
    mon.cycle(T0 + 4 * H)                           # obs 2 while in flight
    terr.gate.set()
    _settle(mon)
    res = mon.cycle(T0 + 4 * H + 300.0)             # lands: obs 1 is old
    assert any("dropped 1" in n for n in res.notes
               if n.startswith("fire belief learning:"))
    _settle(mon)                                    # obs 2's own forecast
    feed.incidents = [point(510.0, T0 + 8 * H)]
    res = mon.cycle(T0 + 8 * H)
    st = res.fire_status["pt"]
    assert st.state == "learned" and st.n_obs == 1  # 2 -> 3 only


class RaisingTerrain:
    def tile(self, bbox, cell_m):
        raise RuntimeError("LFPS down")


def test_a_failed_learning_forecast_is_a_note_not_a_provider_error():
    mon, res, _ = _run(_cfg(fire_belief_learn=True), sizes=(500.0, 505.0),
                       terr=RaisingTerrain())
    assert res.fire_status["pt"].state == "prior"
    note = next(n for n in res.notes if n.startswith("fire belief learning:"))
    assert "failed 1" in note and "LFPS down" in note
    assert not [e for e in res.operator_events if e.kind == "provider_error"]
    assert not res.degraded


def test_a_background_threads_terrain_fallbacks_are_its_own():
    class Down:
        def tile(self, bbox, cell_m):
            raise RuntimeError("LFPS outline failure")
    t = terrain.BundledFallbackTerrain(Down(), TILES)
    names = [n for n in sorted(os.listdir(TILES)) if n.endswith(".wgt")]
    assert names, "bundled tiles missing"
    cell_m, name, path, nrows, ncols, north, west = t._tiles()[0]
    inner = BBox(west=west + 0.01, south=north - 0.02, east=west + 0.02,
                 north=north - 0.01)
    got = []
    th = threading.Thread(target=lambda: (t.tile(inner, cell_m),
                                          got.append(t.pop_fallbacks())))
    th.start()
    th.join(60)
    assert len(got) == 1 and len(got[0]) == 1        # the thread's own
    assert t.pop_fallbacks() == []                   # never the cycle's


def test_config_defaults_and_validation():
    cfg = ss.small_cfg()
    assert cfg.fire_belief_learn is False
    assert (cfg.fire_belief_learn_pad_m, cfg.fire_belief_learn_snap_deg) == \
        (10_000.0, 0.05)
    assert validate_config(cfg) == []
    for kw, frag in ((dict(fire_belief_learn_pad_m=0.0), "learn_pad_m"),
                     (dict(fire_belief_learn_pad_m=200_000.0), "learn_pad_m"),
                     (dict(fire_belief_learn_snap_deg=-0.1), "learn_snap_deg"),
                     (dict(fire_belief_learn_snap_deg=2.0), "learn_snap_deg")):
        probs = validate_config(dataclasses.replace(cfg, **kw))
        assert any(frag in p for p in probs), kw


def test_the_watch_flag_arms_learning_and_tracking():
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--fire-belief-learn"])
    assert args.fire_belief_learn is True
    cfg = cli._fire_belief_cfg(ss.small_cfg(), args)
    assert cfg.fire_belief_track and cfg.fire_belief_learn
    assert not cfg.fire_belief_apply
    notes = cli._fire_belief_notes(args)
    assert any(n.startswith("LEARNING FORECASTS") for n in notes)


def test_learning_status_counts_what_is_in_flight():
    cfg = _cfg(fire_belief_learn=True)
    feed = ss.ScriptedFeed([point(500.0, T0)])
    terr = GatedTerrain()
    mon = _mon(cfg, feed, terr)
    mon.cycle(T0)
    st = mon.learning_status()
    assert st == {"pad_m": 10_000.0, "snap_deg": 0.05, "in_flight": 1}
    terr.gate.set()
    _settle(mon)
    assert ThreatMonitor(feeds=[], weather_provider=ss.StubWeather(),
                         terrain_provider=ss.StubTerrain(),
                         registry=ss.StubRegistry([]),
                         alert_engine=AlertEngine(_cfg()), cfg=_cfg(),
                         safe_zones=[]).learning_status() is None
