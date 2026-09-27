"""The three 2026-09-27 builds working together (contracts SAFETY SEMANTICS
14 + 15 + 16), pinned where the integrator joined them:

  * the per-fire spread belief's status (SEMANTICS 14) is computed BEFORE the
    planner and OR-combined into the per-fire off-domain horizon (SEMANTICS
    15): a belief that says RAPID puts a fire back on the ceiling even when
    its feed report says slow, and a belief that KNOWS a fire lets an
    unreported fire extrapolate at its own front;
  * CycleResult.fire_status is that same table;
  * the TEST world (SEMANTICS 16) drills from a fresh, non-persistent copy of
    the LIVE world's beliefs every cycle and never writes back.

Worlds: tests/offmap_stack.py (the Willow / 69-km shape) and
tests/seeded_stack.py (the two-world daemon)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import offmap_stack as om  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse.contracts import FireStatus  # noqa: E402

T0 = om.T0


def _cfg(**kw):
    return om.fail_closed_cfg(corridor_offraster_per_fire=True,
                              corridor_offraster_margin_min=60.0,
                              fire_belief_track=True, **kw)


def _run_with_belief_status(tmp_path, statuses, cycles=2):
    """Run the offmap world with belief tracking on and the store's statuses
    replaced by `statuses` (the belief's verdicts are the variable here)."""
    clock = ss.SettableClock(T0)
    daemon, monitor, _feed, _engine = om.make_stack(tmp_path / "state",
                                                    _cfg(), clock=clock)
    store = monitor.fire_beliefs
    assert store is not None
    store.statuses = lambda incidents, now_s, cfg: dict(statuses)
    res = None
    for k in range(cycles):
        clock.t = T0 + 60.0 + k * om.PERIOD_S
        res = daemon.run_cycle()
    return res


def _unserved(res):
    return [(u.client_id, u.reason) for u in res.plan.unserved]


def test_without_a_belief_verdict_the_feed_report_decides(tmp_path):
    res = _run_with_belief_status(tmp_path, {})
    assert res.offmap["inc-slow"].kind == "per_fire"
    assert res.offmap["inc-near"].kind == "unknown"
    assert _unserved(res) == []
    assert res.fire_status == {}


def test_a_belief_rapid_verdict_overrides_a_slow_feed_report(tmp_path):
    rapid = FireStatus(incident_id="inc-slow", known=True, rapid=True,
                       reasons=("belief: grew beyond noise in 12 h",))
    res = _run_with_belief_status(tmp_path, {"inc-slow": rapid})
    h = res.offmap["inc-slow"]
    assert h.kind == "rapid" and h.rapid and h.ros_mps == 1.7
    assert "belief: grew beyond noise in 12 h" in h.status_reasons
    # the Willow-shape job is refused again, exactly as under the flat law
    assert _unserved(res) == [("cB", "no_safe_corridor")]
    assert res.fire_status == {"inc-slow": rapid}


def test_a_belief_that_knows_an_unreported_fire_uses_its_own_front(tmp_path):
    known = FireStatus(incident_id="inc-near", known=True, rapid=False,
                       reasons=("belief: 3 reports, 0.2x physics",))
    res = _run_with_belief_status(tmp_path, {"inc-near": known})
    h = res.offmap["inc-near"]
    assert h.known and not h.rapid
    assert h.kind in ("per_fire", "floor", "ceiling_front")
    assert h.status_reasons[-1] == "belief: 3 reports, 0.2x physics"
    assert res.offmap["inc-slow"].kind == "per_fire"


def test_drills_start_from_a_fresh_non_persistent_copy_of_the_live_beliefs(
        tmp_path):
    clock = ss.SettableClock(T0)
    cfg = ss.small_cfg(fire_belief_track=True)
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", cfg=cfg,
                                        incidents=[ss.feed_incident()],
                                        clock=clock)
    tw = daemon.test_world
    tw.add_seed(ss.seed())
    clock.t = T0 + 60.0
    daemon.run_cycle()
    live_store = monitor.fire_beliefs
    first = tw.monitor.fire_beliefs
    assert live_store is not None and first is not None
    assert first is not live_store
    assert live_store.persistent is True and first.persistent is False
    assert json.dumps(first.to_json(), sort_keys=True, default=str) == \
        json.dumps(live_store.to_json(), sort_keys=True, default=str)
    clock.t = T0 + 60.0 + ss.PERIOD_S
    daemon.run_cycle()
    assert tw.monitor.fire_beliefs is not first            # re-copied
    assert tw.monitor.fire_beliefs is not monitor.fire_beliefs
    # the drill's seed is never learned by the live world
    assert "seeded:" not in json.dumps(monitor.fire_beliefs.to_json(),
                                       default=str)


def test_no_live_belief_leaves_the_drill_monitor_alone(tmp_path):
    clock = ss.SettableClock(T0)
    daemon, monitor, *_ = ss.make_stack(tmp_path / "s", cfg=ss.small_cfg(),
                                        incidents=[ss.feed_incident()],
                                        clock=clock)
    tw = daemon.test_world
    tw.add_seed(ss.seed())
    clock.t = T0 + 60.0
    daemon.run_cycle()
    assert monitor.fire_beliefs is None
    assert tw.monitor.fire_beliefs is None


def test_the_belief_applied_ensemble_prices_per_zone_and_guards_p10_there():
    """SAFETY SEMANTICS 14 x 17 (integrator 2026-09-27): a belief-applied
    fire with a weather field runs BOTH of applied_ensemble's ensembles per
    (zone, hour) — the belief's members and the physics run behind the P10
    guard — so local weather reaches belief-applied fires and P10 stays
    min(belief, physics) under the same local weather."""
    from test_weather_field import T0 as WT0, _B3_TILES, _zoned_field
    from wgresponse import fire_belief, geo, spread
    from wgresponse.contracts import ResponseConfig
    tile = _B3_TILES["mixed"]()
    ign = geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2, 4)
    field = _zoned_field(tile, "veer", ign)
    cfg = ResponseConfig(ensemble_n=5, horizon_h=96.0, model_error_sd_log=0.7)

    def half_speed(k, q, legacy):
        return [0.5] * 96

    got = fire_belief.applied_ensemble(
        tile, [ign], field.forecasts[0], WT0, cfg, "bz", None, None,
        half_speed, weather_field=field)
    want = spread.ensemble_arrival(
        tile, [ign], field.forecasts[0], WT0, cfg, incident_id="bz",
        member_hour_scale=half_speed, weather_field=field)
    phys = spread.ensemble_arrival(
        tile, [ign], field.forecasts[0], WT0, cfg, incident_id="bz",
        weather_field=field)
    assert got.p50.data == want.p50.data and got.p90.data == want.p90.data
    assert got.p10.data == [a if a <= b else b
                            for a, b in zip(want.p10.data, phys.p10.data)]
    one = fire_belief.applied_ensemble(
        tile, [ign], field.forecasts[0], WT0, cfg, "bz", None, None,
        half_speed)
    assert one.p50.data != got.p50.data          # the zones reach the belief
