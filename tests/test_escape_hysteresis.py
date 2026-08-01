"""ESCAPE-TARGET HYSTERESIS (contracts escape_hysteresis_margin_min; founder
2026-07-30 churn fix): a fleeing crew keeps its adopted target unless the
proposal improves the routed destination margin beyond the threshold."""
from __future__ import annotations

import dataclasses

from wgresponse import simui
from wgresponse.contracts import LatLon


def _session(hyst_min: float):
    s = simui.SimSession("boulder_co", 2, 1, seed="hyst-test",
                         cfg_overrides={"escape_hysteresis_margin_min":
                                        hyst_min})
    return s


def _crew(s):
    return next(iter(s._crew_sims.values()))


def _script_metrics(s, table):
    """Monkeypatch _route_metrics: table maps rounded (lat,lon) of the
    TARGET to (burned, exposure, worst, t, dest_margin)."""
    def fake(pos, target, now, speed, lead_s):
        return table[(round(target.lat, 4), round(target.lon, 4))]
    s._route_metrics = fake


CUR = LatLon(40.0100, -105.2500)
NEW = LatLon(40.0200, -105.2600)


def _key(p):
    return (round(p.lat, 4), round(p.lon, 4))


def test_zero_threshold_always_switches():
    s = _session(0.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "escape")
    assert s._escape_switch_ok(cs, s._now_s, 900.0, NEW, "z9") is True


def test_small_improvement_keeps_current_target():
    s = _session(30.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "escape")
    _script_metrics(s, {_key(CUR): (False, 0, 0, 0, -600.0),
                        _key(NEW): (False, 0, 0, 0, 0.0)})   # +10 min only
    assert s._escape_switch_ok(cs, s._now_s, 900.0, NEW, "z9") is False


def test_large_improvement_switches():
    s = _session(30.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "escape")
    _script_metrics(s, {_key(CUR): (False, 0, 0, 0, -600.0),
                        _key(NEW): (False, 0, 0, 0, 2400.0)})  # +50 min
    assert s._escape_switch_ok(cs, s._now_s, 900.0, NEW, "z9") is True


def test_clean_route_beats_burned_route_regardless_of_margin():
    s = _session(30.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "escape")
    _script_metrics(s, {_key(CUR): (True, 5, 0, 0, 900.0),    # burned route
                        _key(NEW): (False, 0, 0, 0, 800.0)})  # clean, worse dm
    assert s._escape_switch_ok(cs, s._now_s, 900.0, NEW, "z9") is True


def test_never_trade_clean_for_burned():
    s = _session(30.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "escape")
    _script_metrics(s, {_key(CUR): (False, 0, 0, 0, -600.0),
                        _key(NEW): (True, 0, 0, 0, 9000.0)})  # burned, huge dm
    assert s._escape_switch_ok(cs, s._now_s, 900.0, NEW, "z9") is False


def test_same_target_id_is_a_switch_noop():
    s = _session(30.0)
    cs = _crew(s)
    cs.relocate_to(CUR, "boulder_co-z1")
    assert s._escape_switch_ok(cs, s._now_s, 900.0, CUR,
                               "boulder_co-z1") is True
