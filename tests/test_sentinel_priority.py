"""tests/test_sentinel_priority.py — A FIRE THE SENTINEL FINDS NEAR HOMES IS
FORECAST AT ONCE (2026-11-19, founder: "build the sentinel priority pass";
contracts SAFETY SEMANTICS 62; cfg.sentinel_priority_pass; watch
--sentinel-priority).

  IDLE      armed with nothing queued, two cycles of the mixed latency
            world give the SAME events, operator events, notes,
            assessments and engine state as the knob off.
  SOONER    a WFIGS record queued while the cycle assesses E is fetched
            alone at the next checkpoint, forecast, and its homes notified
            before F's terrain call — a whole cycle before the knob-off run
            sends them; every knob-off send for it is made, no later.
  NEW AGAIN the next full cycle that carries the fire treats it as new: it
            goes first, its forecast is computed again (its terrain asked
            again) and its pairs are evaluated right after it; no pair
            steps twice in a cycle.
  SKIPS     a record the cycle already carries or tracks is never fetched;
            a failed fetch is a note (never a degraded cycle); a record that
            is the same fire as one of the cycle's under another name join,
            a stale record and a contained one are not assessed; the TEST
            world never runs a pass.
  FETCH     WfigsFeed.fetch_irwins asks both layers by IRWIN id (each
            stored spelling), builds incidents with the adapter's own
            parser, drops RX, never sends an unsafe id and leaves the
            adapter's fetch state untouched.
  HAND-OFF  the sentinel hands only WFIGS events to on_priority; the
            daemon queues them and wakes the loop; observe_priority marks
            the records.
  CLI       --sentinel-priority arms it; it needs the sentinel."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alert_path_stack as S  # noqa: E402
from tests.test_threat import NOW, StubFeed  # noqa: E402
from wgresponse import cli, feeds, geo, sentinel  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident,
    IncidentSource,
    OperatorEvent,
    ResponseConfig,
    validate_config,
)

IRW = "aaaa1111-2222-3333-4444-555566667777"
RAW = "{" + IRW.upper() + "}"


def late_fire(name="Late Fire", origin=S.N1, updated=NOW - 1800.0, **kw):
    return FireIncident(incident_id=IRW, name=name,
                        source=IncidentSource.WFIGS, origin=origin,
                        updated_at_s=updated, irwin_id=IRW,
                        source_ids={"wfigs": IRW}, **kw)


class IrwinFeed(StubFeed):
    """StubFeed + the WFIGS adapter's single-fire seam."""

    def __init__(self, incidents, late=None, ok=True):
        super().__init__(incidents)
        self.late = late
        self.ok = ok
        self.asked = []

    def fetch_irwins(self, ids, bbox=None):
        self.asked.append(list(ids))
        if not self.ok:
            return [], False
        want = {feeds._norm_irwin(i) for i in ids}
        return [f for f in ([self.late] if self.late else [])
                if f.irwin_id in want], True


class QueueAtTerrain(S.LogTerrain):
    """LogTerrain that queues the sentinel's hand-off while E's terrain is
    fetched (a record published while the cycle assesses E)."""

    def __init__(self, log, item=None):
        super().__init__(log)
        self.monitor = None
        self.item = item

    def tile(self, bbox, cell_m):
        if (self.item is not None and self.monitor is not None
                and bbox.contains(S.ORIGIN)):
            self.monitor.queue_priority(self.item)
            self.item = None
        return super().tile(bbox, cell_m)


def item(irwin=IRW, raw=RAW, name="Late Fire"):
    return {"irwin": irwin, "raw_irwin": raw, "t_s": NOW - 30.0,
            "client": "c-n1", "km": 1.5, "name": name}


def build(on, late=None, carry_late=False, queue=None, ok=True, **kw):
    """The mixed world (E, F + tracked pairs), no new fire in the feed;
    `late` is the WFIGS record the single-fire fetch returns."""
    cfg = S.ladder_cfg(alert_new_fires_early=True,
                       new_fire_sentinel_s=60.0 if on else 0.0,
                       sentinel_priority_pass=on, **kw)
    mon, eng, log = S.build(cfg, new=())
    incs = [S.fire("E", S.ORIGIN), S.fire("F", S.F0)]
    if carry_late:
        incs.append(late)
    feed = IrwinFeed(incs, late=late, ok=ok)
    mon._feeds = [feed]
    terrain = QueueAtTerrain(log, queue)
    terrain.monitor = mon
    mon._terrain = terrain
    return mon, eng, log, feed


def run(mon, eng, log, cycles, after=None):
    snaps, results = [], []
    for k in range(cycles):
        res = mon.cycle(NOW + 300.0 * k)
        results.append(res)
        snaps.append(S.snapshot(res, eng, log))
        if after is not None:
            after(k)
    return snaps, results


KEYS = ("events", "ops", "notes", "assessments", "degraded", "states",
        "ledger", "growth", "deesc", "pending")


def test_armed_and_idle_changes_nothing():
    off = run(*build(False)[:3], cycles=2)[0]
    on, res = run(*build(True)[:3], cycles=2)
    for k, (a, b) in enumerate(zip(off, on)):
        for key in KEYS:
            assert a[key] == b[key], (k, key)
        assert a["sends"] == b["sends"], k
    assert res[0].timings["priority_pass"] == []


def _late_run(on, cycles=2, **kw):
    """The late fire is published while cycle 1 assesses E; every feed
    carries it from cycle 2 on. Returns the log's length after each cycle
    too (the cycle boundaries)."""
    late = late_fire()
    mon, eng, log, feed = build(on, late=late, queue=item() if on else None,
                                **kw)
    marks = []

    def after(k):
        marks.append(len(log))
        if k == 0:
            feed._incidents.append(late)
    snaps, results = run(mon, eng, log, cycles, after=after)
    return snaps, results, log, feed, marks


def test_the_late_fire_is_notified_a_cycle_sooner_and_never_later():
    off, res_off, log_off, _, _ = _late_run(False)
    on, res_on, log_on, feed, _ = _late_run(True)
    # knob off: nothing about the late fire in cycle 1; its sends in cycle 2
    assert not [s for s in off[0]["sends"] if s[2] == IRW]
    sends_off = [s for s in off[1]["sends"] if s[2] == IRW]
    assert sends_off
    # knob on: fetched alone, once, by its stored spelling
    assert feed.asked == [[RAW]]
    rows = res_on[0].timings["priority_pass"]
    assert [r["outcome"] for r in rows] == ["assessed"]
    assert rows[0]["incident_id"] == IRW
    assert IRW in {i.incident_id for i in res_on[0].incidents}
    sends_on_1 = [s for s in on[0]["sends"] if s[2] == IRW]
    assert sends_on_1
    # ... notified before F's terrain call (the next incident)
    i_send = log_on.index(sends_on_1[0])
    i_f = next(i for i, r in enumerate(log_on)
               if r[0] == "terrain" and abs(r[1] - S.F0.lat) < 0.05)
    assert i_send < i_f
    # every knob-off send for it was made by the end of the same cycle
    made = {(s[1], s[2], s[3]) for s in on[0]["sends"] + on[1]["sends"]}
    assert {(s[1], s[2], s[3]) for s in sends_off} <= made
    # no pair stepped twice in cycle 1
    keys = [(e.client_id, e.incident_id) for e in res_on[0].events
            if e.incident_id == IRW]
    assert len(keys) == len(set(keys))
    # the cycle is not degraded by the pass
    assert res_on[0].degraded == res_off[0].degraded


def test_the_next_full_cycle_treats_it_as_new_again():
    """With forecast tiers (live), the pass's forecast is NOT kept into the
    next full cycle: the fire goes first there, its forecast is computed
    again in the cycle (its terrain asked first), its pairs evaluated right
    after it; from the cycle after, it is an ordinary fire (kept)."""
    on, res, log, _, marks = _late_run(True, cycles=3, forecast_tiers=True)
    assert res[0].timings["incidents"][IRW]["forecast"] == "computed"
    assert res[1].timings["early_alerts"]["new_fires"][:1] == [IRW]
    assert res[1].timings["incidents"][IRW]["forecast"] == "computed"
    c2 = [r for r in log[marks[0]:marks[1]] if r[0] == "terrain"]
    assert abs(c2[0][1] - S.N1.lat) < 0.2              # asked again, first
    assert res[1].timings["priority_pass"] == []
    for r in res[:2]:
        keys = [(e.client_id, e.incident_id) for e in r.events]
        assert len(keys) == len(set(keys))
    assert res[2].timings["incidents"][IRW]["forecast"] == "kept"
    assert IRW not in res[2].timings["early_alerts"]["new_fires"]


def test_a_record_the_cycle_carries_or_tracks_is_never_fetched():
    late = late_fire()
    mon, eng, log, feed = build(True, late=late, carry_late=True,
                                queue=item())
    res = mon.cycle(NOW)
    assert feed.asked == []
    assert [r["outcome"] for r in res.timings["priority_pass"]] == \
        ["in_cycle"]
    # a tracked id (G: a pair, no record this cycle)
    mon2, _, _, feed2 = build(True, late=late,
                              queue=item(irwin="g", raw="G"))
    res2 = mon2.cycle(NOW)
    assert feed2.asked == []
    assert [r["outcome"] for r in res2.timings["priority_pass"]] == \
        ["in_cycle"]


def test_a_failed_fetch_is_a_note_never_a_degraded_cycle():
    off = build(False)
    res_off = off[0].cycle(NOW)
    mon, eng, log, feed = build(True, late=late_fire(), queue=item(),
                                ok=False)
    res = mon.cycle(NOW)
    assert [r["outcome"] for r in res.timings["priority_pass"]] == \
        ["fetch_failed"]
    assert res.degraded == res_off.degraded
    assert any("priority pass" in n and "failed" in n for n in res.notes)
    assert IRW not in {i.incident_id for i in res.incidents}


@pytest.mark.parametrize("late,outcome,extra", [
    (late_fire(name="Fire E", origin=geo.destination(S.ORIGIN, 0.0, 2000.0)),
     "joins_active", {}),
    (late_fire(updated=NOW - 200 * 3600.0), "stale", {}),
    (late_fire(containment_pct=100.0), "contained",
     {"drop_contained_incidents": True}),
])
def test_same_fire_stale_and_contained_records_are_not_assessed(
        late, outcome, extra):
    mon, eng, log, feed = build(True, late=late, queue=item(), **extra)
    res = mon.cycle(NOW)
    assert [r["outcome"] for r in res.timings["priority_pass"]] == [outcome]
    assert IRW not in {a.incident_id for a in res.assessments}
    assert not [e for e in res.events if e.incident_id == IRW]


def test_the_test_world_never_runs_a_pass():
    mon, eng, log, feed = build(True, late=late_fire(), queue=item())
    mon.early_alerts_ok = False            # what the TEST world's monitor is
    res = mon.cycle(NOW)
    assert feed.asked == [] and "priority_pass" not in res.timings
    assert len(mon._priority_inbox) == 1   # left for the live monitor


def test_queue_is_thread_safe_and_dedupes():
    mon = build(True)[0]
    mon.queue_priority(item())
    mon.queue_priority(item(irwin=IRW.upper()))
    mon.queue_priority({"irwin": ""})
    assert [q["irwin"] for q in mon._priority_inbox] == [IRW]


# -- the WFIGS single-fire fetch --------------------------------------------

class WfigsServer:
    def __init__(self):
        self.asked = []

    def __call__(self, url, headers=None):
        base, _, qs = url.partition("?")
        q = dict(urllib.parse.parse_qsl(qs))
        self.asked.append((base, q))
        if base == feeds.WfigsFeed.POINTS_URL:
            return 200, json.dumps({"features": [
                {"attributes": {"IrwinID": RAW, "IncidentName": "Late",
                                "IncidentTypeCategory": "WF",
                                "ModifiedOnDateTime_dt": int(NOW * 1000),
                                "FireDiscoveryDateTime": int(NOW * 1000),
                                "IncidentSize": 4.0, "OBJECTID": 9},
                 "geometry": {"x": S.N1.lon, "y": S.N1.lat}},
                {"attributes": {"IrwinID": "{BBBB0000-0000-0000-0000-"
                                           "000000000002}",
                                "IncidentName": "A burn",
                                "IncidentTypeCategory": "RX",
                                "ModifiedOnDateTime_dt": int(NOW * 1000)},
                 "geometry": {"x": S.N2.lon, "y": S.N2.lat}}]}).encode()
        if base == feeds.WfigsFeed.PERIMETERS_URL:
            return 200, json.dumps({"features": []}).encode()
        raise AssertionError(url)


def test_fetch_irwins_asks_both_layers_by_id_and_parses_like_fetch_active():
    srv = WfigsServer()
    feed = feeds.WfigsFeed(http_get=srv, keep_rx=True)
    feed.last_fetch_ok = "untouched"
    feed.rx_records = ["untouched"]
    before = list(feed._requests)
    got, ok = feed.fetch_irwins([RAW, "bbbb0000-0000-0000-0000-000000000002"])
    assert ok and [g.incident_id for g in got] == [IRW]       # RX dropped
    assert got[0].irwin_id == IRW and got[0].origin.lat == pytest.approx(
        S.N1.lat)
    where_pts = srv.asked[0][1]["where"]
    assert where_pts.startswith("IrwinID IN (")
    for v in (IRW, IRW.upper(), "{" + IRW + "}", RAW):
        assert f"'{v}'" in where_pts
    assert "attr_IrwinID IN" in srv.asked[1][1]["where"]
    assert "poly_IRWINID IN" in srv.asked[1][1]["where"]
    assert feed.last_fetch_ok == "untouched"
    assert feed.rx_records == ["untouched"]
    assert feed._requests == before


def test_fetch_irwins_never_sends_an_unsafe_id_and_never_raises():
    srv = WfigsServer()
    feed = feeds.WfigsFeed(http_get=srv)
    assert feed.fetch_irwins(["x') OR (1=1"]) == ([], True)
    assert srv.asked == []

    def broken(url, headers=None):
        raise OSError("network down")
    got, ok = feeds.WfigsFeed(http_get=broken).fetch_irwins([RAW])
    assert got == [] and ok is False


# -- the sentinel's hand-off and the daemon -----------------------------------

def _ev(source, rid):
    return OperatorEvent(t_s=NOW, kind=sentinel.EVENT_KIND, message="m",
                         incident_id=rid,
                         detail={"source": source, "id": rid, "name": "N",
                                 "client": "c1", "km": 2.0})


def test_the_sentinel_hands_off_only_wfigs_events():
    got = []
    cfg = ResponseConfig(new_fire_sentinel_s=60.0)
    s = sentinel.NewFireSentinel(cfg, clients_fn=lambda: [],
                                 on_priority=got.append)
    s._records["wfigs:" + IRW] = {"key": "wfigs:" + IRW, "first_seen_s": NOW,
                                  "_raw_irwin": RAW}
    s._hand_off([_ev("wfigs", IRW), _ev("firis", "firis:7"),
                 _ev("calfire", "calfire:u")])
    assert [g["irwin"] for g in got] == [IRW]
    assert got[0]["raw_irwin"] == RAW
    assert s._records["wfigs:" + IRW]["priority"] == "queued"
    s.observe_priority([{"irwin": IRW, "outcome": "assessed",
                         "done_s": NOW + 42.0}])
    row = s._records["wfigs:" + IRW]
    assert row["priority"] == "assessed" and row["priority_lead_s"] == 42.0


def test_without_the_hand_off_nothing_is_handed():
    cfg = ResponseConfig(new_fire_sentinel_s=60.0)
    s = sentinel.NewFireSentinel(cfg, clients_fn=lambda: [])
    assert s._on_priority is None


def test_the_daemon_queues_and_wakes():
    class Mon:
        def __init__(self):
            self.q = []

        def queue_priority(self, it):
            self.q.append(it)
    d = cli._WatchDaemon.__new__(cli._WatchDaemon)
    d._monitor = Mon()
    import threading
    d._wake = threading.Event()
    d.priority_from_sentinel(item())
    assert d._monitor.q and d._wake.is_set()


def test_flag_arms_it_and_it_needs_the_sentinel():
    base = ResponseConfig()
    cfg, notes = cli._feeds_cfg(base, argparse.Namespace(
        new_fire_sentinel=60.0, sentinel_priority=True))
    assert cfg.sentinel_priority_pass and cfg.new_fire_sentinel_s == 60.0
    assert any("SAFETY SEMANTICS 62" in n for n in notes)
    assert not any("sentinel_priority_pass" in p
                   for p in validate_config(cfg))
    bad = ResponseConfig(sentinel_priority_pass=True)
    assert any("sentinel_priority_pass" in p for p in validate_config(bad))
    cfg0, notes0 = cli._feeds_cfg(base, argparse.Namespace())
    assert cfg0 == base and not any("62" in n for n in notes0)
