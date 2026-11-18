"""tests/test_latency_small_fixes.py — SAFETY SEMANTICS 52 (2026-11-18, the
fire -> alert latency review, build lane 1 items 5-7).

  (a) MISSING TIME  stale_gate_missing_time (amending 9; lane B's B7): a
                    record without an update time is aged by its discovery
                    time, else kept as current — never excluded for the
                    missing time alone; off it is excluded every cycle.
  (b) STRICT NAME   fuse_name_join_strict (amending 24; lane B's B6 / H3):
                    the name join refuses a different IRWIN id or a
                    discovery gap over 24 h, so an old contained namesake no
                    longer takes a new fire off the map with it.
  (c) URGENT FIRST  notify_urgent_first (lane C's C10): the sends go impact /
                    extreme first, then by p10; the returned events, the
                    ledger and the all-clear gate are unchanged."""
from __future__ import annotations

import dataclasses
import math
from types import SimpleNamespace

import pytest

from tests.test_alerts import assess
from tests.test_threat import (
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, feeds, geo
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    AlertRecord,
    AlertState,
    ClientProperty,
    FireIncident,
    IncidentSource,
    ResponseConfig,
    validate_config,
)


def _wfigs(iid, origin=ORIGIN, name=None, **kw):
    kw.setdefault("updated_at_s", NOW - 600.0)
    return FireIncident(incident_id=iid, name=name or f"Fire {iid}",
                        source=IncidentSource.WFIGS, origin=origin,
                        irwin_id=kw.pop("irwin", iid),
                        source_ids={"wfigs": iid}, **kw)


def _cycle(cfg, incs, homes=None):
    eng = AlertEngine(cfg)
    mon = make_monitor(feeds=[StubFeed(incs)], cfg=cfg, alert_engine=eng,
                       registry=StubRegistry(homes or [near_client()]))
    return mon.cycle(NOW)


# ---------------------------------------------------------------------------
# (a) missing time
# ---------------------------------------------------------------------------

def test_a_off_a_record_without_a_time_is_excluded_every_cycle():
    r = _cycle(make_cfg(), [_wfigs("m", updated_at_s=0.0,
                                   discovered_at_s=NOW - 3600.0)])
    assert r.incidents == [] and r.assessments == []
    assert any("SAFETY SEMANTICS 9" in n and "m (age 488" in n
               for n in r.notes)


def test_a_on_aged_by_its_discovery_time():
    r = _cycle(make_cfg(stale_gate_missing_time=True),
               [_wfigs("m", updated_at_s=0.0, discovered_at_s=NOW - 3600.0)])
    (inc,) = r.incidents
    assert inc.updated_at_s == NOW - 3600.0
    assert inc.meta["updated_at_missing"] == "discovered"
    assert [a.client_id for a in r.assessments] == ["c-near"]
    assert any("52a" in n and "aged by their discovery time: m" in n
               for n in r.notes)


def test_a_on_no_time_at_all_is_kept_as_current_never_at_epoch_zero():
    ring = [geo.destination(ORIGIN, b, 300.0) for b in (0, 90, 180, 270)]
    inc = _wfigs("p", updated_at_s=0.0, perimeter=[ring + ring[:1]],
                 meta={"perimeter_at_s": 0.0})
    r = _cycle(make_cfg(stale_gate_missing_time=True), [inc])
    (got,) = r.incidents
    assert got.updated_at_s == NOW
    assert got.meta["updated_at_missing"] == "none"
    (a,) = r.assessments
    assert a.eta.eta_p10_s >= NOW - 1.0      # not burned since 1970
    assert any("kept as current" in n and ": p" in n for n in r.notes)


def test_a_on_a_discovery_older_than_the_gate_is_still_stale():
    r = _cycle(make_cfg(stale_gate_missing_time=True),
               [_wfigs("old", updated_at_s=0.0,
                       discovered_at_s=NOW - 10 * 86400.0)])
    assert r.incidents == []
    assert any("SAFETY SEMANTICS 9" in n and "old (age 240 h)" in n
               for n in r.notes)


def test_a_on_a_nan_time_is_missing_too_and_never_fewer_fires():
    incs = [_wfigs("nan", updated_at_s=float("nan")),
            _wfigs("zero", updated_at_s=0.0,
                   origin=geo.destination(ORIGIN, 0.0, 300_000.0)),
            _wfigs("ok")]
    off = _cycle(make_cfg(), incs)
    on = _cycle(make_cfg(stale_gate_missing_time=True), incs)
    assert [i.incident_id for i in off.incidents] == ["ok"]
    assert {i.incident_id for i in on.incidents} == {"nan", "zero", "ok"}
    assert {i.incident_id for i in off.incidents} <= \
        {i.incident_id for i in on.incidents}
    assert all(math.isfinite(i.updated_at_s) for i in on.incidents)


# ---------------------------------------------------------------------------
# (b) strict name join
# ---------------------------------------------------------------------------

def _h3():
    """Lane B's H3: an old CONTAINED 'Junction Fire' and a new one of
    another IRWIN id 2 km north, both in the WFIGS layer."""
    old = _wfigs("IRW-OLD", name="Junction Fire", containment_pct=100.0,
                 updated_at_s=NOW - 40 * 3600.0,
                 discovered_at_s=NOW - 30 * 86400.0,
                 meta={"contained_at_s": NOW - 60 * 3600.0})
    new = _wfigs("IRW-NEW", origin=geo.destination(ORIGIN, 0.0, 2000.0),
                 name="Junction Fire", discovered_at_s=NOW - 3600.0)
    home = ClientProperty("c-new", geo.destination(new.origin, 90.0, 1500.0))
    return old, new, home


def test_b_h3_off_the_new_fire_leaves_with_the_old_one():
    old, new, home = _h3()
    r = _cycle(make_cfg(drop_contained_incidents=True), [old, new], [home])
    assert r.incidents == [] and r.assessments == []
    assert len(r.removed_incidents) == 1


def test_b_h3_on_the_new_fire_stays_and_alerts():
    old, new, home = _h3()
    r = _cycle(make_cfg(drop_contained_incidents=True,
                        fuse_name_join_strict=True), [old, new], [home])
    assert [i.incident_id for i in r.incidents] == ["IRW-NEW"]
    assert [i.incident.incident_id for i in r.removed_incidents] == [
        "IRW-OLD"]
    assert [(a.client_id, a.incident_id) for a in r.assessments] == [
        ("c-new", "IRW-NEW")]
    assert [e.state for e in r.events] == [AlertState.EXTREME_24H]


def _fuse(cfg, *incs):
    return sorted(i.incident_id for i in feeds.fuse_incidents(
        [list(incs)], cfg=cfg, now_s=NOW))


@pytest.mark.parametrize("case,split", [
    ("different_irwin", True),
    ("discovered_25h_apart", True),
    ("discovered_23h_apart", False),
    ("one_without_irwin_or_time", False),
])
def test_b_the_refusal_rule(case, split):
    a = _wfigs("A", name="Twin Fire", discovered_at_s=NOW - 3600.0)
    b = _wfigs("B", origin=geo.destination(ORIGIN, 0.0, 1000.0),
               name="Twin Fire", irwin=None, discovered_at_s=None)
    if case == "different_irwin":
        b = dataclasses.replace(b, irwin_id="B")
    elif case == "discovered_25h_apart":
        b = dataclasses.replace(b, discovered_at_s=NOW - 26 * 3600.0)
    elif case == "discovered_23h_apart":
        b = dataclasses.replace(b, discovered_at_s=NOW - 24 * 3600.0)
    on = _fuse(make_cfg(fuse_name_join_strict=True), a, b)
    off = _fuse(make_cfg(), a, b)
    assert len(off) == 1                       # the legacy join
    assert len(on) == (2 if split else 1)


def test_b_irwin_joins_are_untouched():
    a = _wfigs("A", name="Twin Fire", discovered_at_s=NOW - 3600.0)
    b = _wfigs("B", origin=geo.destination(ORIGIN, 0.0, 30_000.0),
               name="Other", irwin="A", discovered_at_s=NOW - 90 * 3600.0)
    assert len(_fuse(make_cfg(fuse_name_join_strict=True), a, b)) == 1


# ---------------------------------------------------------------------------
# (c) urgent first
# ---------------------------------------------------------------------------

class _Log:
    def __init__(self):
        self.sent = []

    def send(self, event, client):
        self.sent.append((event.client_id, event.state))
        return True


def _evaluate(on):
    cfg = ResponseConfig(notify_urgent_first=on)
    log = _Log()
    eng = AlertEngine(cfg, notifier=log)
    t0 = NOW - 7200.0
    eng.restore({("cl", "G"): AlertRecord("cl", "G", AlertState.WATCH, t0,
                                          clear_streak=1,
                                          history=[(t0, AlertState.WATCH)])},
                notified_ledger={("cl", "G", "watch"): t0})
    evs = eng.evaluate([assess("n-far", "X", NOW, 60.0),
                        assess("n-near", "X", NOW, 50.0),
                        assess("x-late", "X", NOW, 20.0, p10_h=11.0),
                        assess("x-soon", "X", NOW, 5.0, p10_h=2.0)], NOW)
    return evs, log.sent, eng


def test_c_the_most_urgent_client_hears_first():
    ev_off, sent_off, eng_off = _evaluate(False)
    ev_on, sent_on, eng_on = _evaluate(True)
    assert [s[0] for s in sent_off] == ["cl", "n-far", "n-near", "x-late",
                                        "x-soon"]
    assert [s[0] for s in sent_on] == ["x-soon", "x-late", "n-near", "n-far",
                                       "cl"]
    assert ev_on == ev_off                       # the engine's order
    assert eng_on.notified_ledger() == eng_off.notified_ledger()
    assert eng_on.states() == eng_off.states()


# ---------------------------------------------------------------------------
# surface
# ---------------------------------------------------------------------------

def test_defaults_bounds_and_watch_flags():
    c = ResponseConfig()
    assert (c.stale_gate_missing_time, c.fuse_name_join_strict,
            c.notify_urgent_first) == (False, False, False)
    assert c.fuse_name_join_max_gap_h == 24.0
    assert validate_config(ResponseConfig(stale_gate_missing_time=True,
                                          fuse_name_join_strict=True,
                                          notify_urgent_first=True)) == []
    for bad in (0.0, -1.0, float("nan"), 169.0):
        assert any("fuse_name_join_max_gap_h" in p for p in validate_config(
            ResponseConfig(fuse_name_join_max_gap_h=bad)))
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "x.json", "--stale-missing-time",
         "--strict-name-join", "--notify-urgent-first"]))
    cfg, notes = cli._latency_cfg(ResponseConfig(), args)
    assert (cfg.stale_gate_missing_time and cfg.fuse_name_join_strict
            and cfg.notify_urgent_first)
    for tag in ("52a", "52b", "52c"):
        assert any(tag in n for n in notes)
    cfg, notes = cli._latency_cfg(ResponseConfig(), SimpleNamespace())
    assert cfg == ResponseConfig() and notes == []
