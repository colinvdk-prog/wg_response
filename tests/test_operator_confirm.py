"""tests/test_operator_confirm.py — AN OPERATOR CONFIRMS OR DISMISSES WHAT
ONLY A SATELLITE SEES (2026-11-19, founder: "build operator Confirm and
Dismiss next"; contracts SAFETY SEMANTICS 64; cfg.operator_confirm;
wgresponse.decisions).

  CONFIRM   heat becomes a fire under a stable id — forecast, assessed,
            alerted; new clusters near its footprint (a new cluster id, a
            second cluster) are the same fire; a reported fire cannot be
            "confirmed".
  LAPSE     24 h with no detection lapses it (operator event).
  STAND     a stand-down needs two DIFFERENT operators.
  HANDOVER  an agency fire that takes the detections takes the alerts:
            records, ledger and owed re-fires move; the higher level
            stands; nothing re-sent; no all-clear; the agency fire is
            assessed as new that cycle.
  DISMISS   quiets the page 12 h; expires; growth undoes it and pages; a
            confirmed fire cannot be dismissed; a Confirm supersedes it.
  DURABLE   decisions persist and match by footprint after a restart.
  SURFACE   the daemon's actions, the authed routes, the config rule and
            the flag."""
from __future__ import annotations

import argparse
import dataclasses
import http.client
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_hotspots import _cluster, _square  # noqa: E402
from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    far_client,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, decisions, geo, paging, server  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertRecord,
    AlertState,
    ClientProperty,
    LatLon,
    ResponseConfig,
    validate_config,
)


def near_cluster(dy=500.0, cid=None, t0=NOW - 600.0):
    return _cluster([geo.destination(ORIGIN, 0.0, dy),
                     geo.destination(ORIGIN, 0.0, dy + 200.0)],
                    t0=t0, cid=cid)


class MutableFeed(StubFeed):
    def set(self, incs):
        self._incidents = list(incs)


def build(tmp_path, feed, cfg=None, **kw):
    cfg = cfg or make_cfg(hotspot_heat_only=True, fuse_hotspots=True,
                          operator_confirm=True, **kw)
    eng = AlertEngine(cfg)
    mon = make_monitor(feeds=[feed], cfg=cfg, alert_engine=eng,
                       registry=StubRegistry([near_client(), far_client()]))
    clock = SimpleNamespace(t=NOW)
    dec = decisions.OperatorDecisions(state_dir=str(tmp_path / "state"),
                                      clock=lambda: clock.t)
    mon.operator_decisions = dec
    return mon, eng, dec, clock


# -- confirm -------------------------------------------------------------------

def test_confirmed_heat_becomes_a_fire_under_a_stable_id(tmp_path):
    cl = near_cluster()
    feed = MutableFeed([cl])
    mon, eng, dec, clock = build(tmp_path, feed)
    r1 = mon.cycle(NOW)
    assert r1.assessments == [] and r1.ensembles == {}      # heat
    code, rec = dec.confirm(cl, by="Alex")
    assert code == 200 and rec["confirm_id"].startswith("confirmed:")
    cid = rec["confirm_id"]
    r2 = mon.cycle(NOW + 120.0)
    assert cid in r2.ensembles
    inc = next(i for i in r2.incidents if i.incident_id == cid)
    assert inc.source_ids["operator"] == cid
    assert inc.meta["operator_confirmed"]["by"] == "Alex"
    assert {a.client_id for a in r2.assessments if a.incident_id == cid}
    assert any(e.incident_id == cid for e in r2.events)
    # the cluster's id changes (a new track) and a second cluster appears
    # near the footprint: still ONE fire under the same id
    moved = near_cluster(dy=900.0, cid="firms:moved")
    second = _cluster([geo.destination(ORIGIN, 10.0, 1500.0)],
                      cid="firms:second")
    feed.set([moved, second])
    r3 = mon.cycle(NOW + 240.0)
    fires = [i for i in r3.incidents if i.incident_id == cid]
    assert len(fires) == 1 and len(fires[0].hotspots) == 3
    assert not any(i.incident_id in ("firms:moved", "firms:second")
                   for i in r3.incidents)
    assert "firms:moved" in dec.status()["confirmed"][0]["cluster_ids"]
    # two clusters the feed did not fuse: apply() merges them into one
    a = _cluster([geo.destination(ORIGIN, 0.0, 600.0)], cid="firms:a")
    b = _cluster([geo.destination(ORIGIN, 90.0, 1200.0)], cid="firms:b")
    far = _cluster([geo.destination(ORIGIN, 180.0, 40_000.0)],
                   cid="firms:far")
    out = dec.apply([a, b, far], NOW + 300.0, [], [])
    assert [i.incident_id for i in out] == ["firms:far", cid]
    assert len(out[1].hotspots) == 2
    assert set(dec.status()["confirmed"][0]["cluster_ids"]) >= {
        "firms:a", "firms:b"}


def test_a_reported_fire_cannot_be_confirmed_or_dismissed(tmp_path):
    dec = decisions.OperatorDecisions()
    fire = incident()
    assert dec.confirm(fire, "Alex")[0] == 409
    assert dec.dismiss(fire, "Alex")[0] == 409


def test_24_hours_quiet_lapses_a_confirmation(tmp_path):
    cl = near_cluster()
    feed = MutableFeed([cl])
    mon, eng, dec, clock = build(tmp_path, feed)
    cid = dec.confirm(cl, "Alex")[1]["confirm_id"]
    mon.cycle(NOW)
    feed.set([])
    r = mon.cycle(NOW + 23 * 3600.0)
    assert not [e for e in r.operator_events
                if e.kind == "confirmed_fire_lapsed"]
    r = mon.cycle(NOW + 25 * 3600.0)
    assert [e.kind for e in r.operator_events
            if e.kind == "confirmed_fire_lapsed"] == ["confirmed_fire_lapsed"]
    assert dec.status()["confirmed"][0]["status"] == "lapsed"
    feed.set([near_cluster(t0=NOW + 26 * 3600.0)])
    r = mon.cycle(NOW + 26 * 3600.0)
    assert cid not in r.ensembles                   # heat again


def test_a_stand_down_needs_two_different_operators(tmp_path):
    cl = near_cluster()
    feed = MutableFeed([cl])
    mon, eng, dec, clock = build(tmp_path, feed)
    cid = dec.confirm(cl, "Alex")[1]["confirm_id"]
    assert dec.stand_down(cid, "Alex")[1]["status"] == "stand_down_pending"
    assert mon.cycle(NOW).ensembles.get(cid) is not None    # still a fire
    assert dec.stand_down(cid, " alex ")[0] == 409          # same person
    assert dec.stand_down(cid, "")[0] == 400
    assert dec.stand_down("confirmed:nope", "Blair")[0] == 404
    assert dec.stand_down(cid, "Blair")[1]["status"] == "stood_down"
    assert cid not in mon.cycle(NOW + 120.0).ensembles      # heat again
    assert dec.stand_down(cid, "Casey")[0] == 409


# -- handover --------------------------------------------------------------------

class Ledger:
    def __init__(self):
        self.sent = []

    def send(self, event, client):
        self.sent.append((event.client_id, event.incident_id,
                          event.state.value))
        return True


def test_an_agency_fire_takes_the_alerts_over(tmp_path):
    cl = near_cluster()
    feed = MutableFeed([cl])
    cfg = make_cfg(hotspot_heat_only=True, fuse_hotspots=True,
                   operator_confirm=True, alert_new_fires_early=True)
    note = Ledger()
    eng = AlertEngine(cfg, notifier=note,
                      clients_by_id={c.client_id: c
                                     for c in (near_client(), far_client())})
    mon = make_monitor(feeds=[feed], cfg=cfg, alert_engine=eng,
                       registry=StubRegistry([near_client(), far_client()]))
    dec = decisions.OperatorDecisions(state_dir=str(tmp_path / "s"))
    mon.operator_decisions = dec
    cid = dec.confirm(cl, "Alex")[1]["confirm_id"]
    mon.cycle(NOW)
    before = {k: r.state for k, r in eng.states().items() if k[1] == cid}
    assert before and any(s is not AlertState.NONE for s in before.values())
    sent_before = list(note.sent)
    # the agency report arrives: its perimeter covers the detections
    fire = dataclasses.replace(incident(), perimeter=_square(ORIGIN, 1500.0))
    feed.set([fire, near_cluster(t0=NOW + 100.0)])
    r = mon.cycle(NOW + 120.0)
    kinds = [e.kind for e in r.operator_events]
    assert "confirmed_fire_handed_over" in kinds and "alerts_carried" in kinds
    after = {k: rec.state for k, rec in eng.states().items()}
    for (client, _), st in before.items():
        assert (client, cid) not in after
        assert decisions_rank(after[(client, "inc-1")]) >= decisions_rank(st)
    # nothing re-sent for a level the client already had; no all-clear
    new_sends = note.sent[len(sent_before):]
    had = {(c, s.value) for (c, _), s in before.items()}
    assert not [s for s in new_sends if (s[0], s[2]) in had]
    assert not [e for e in r.events
                if e.incident_id == cid and e.state is AlertState.CLEARED]
    ledger = eng.notified_ledger()
    assert not [k for k in ledger if k[1] == cid]
    assert all((c, "inc-1", st) in ledger for c, st in had
               if (c, cid, st) in {(s[0], s[1], s[2]) for s in sent_before})
    # the agency fire was assessed as new (first in the early plan)
    assert r.timings["early_alerts"]["new_fires"][:1] == ["inc-1"]
    assert dec.status()["confirmed"][0]["status"] == "handed_over"


def decisions_rank(state):
    return AlertEngine._LEVEL.get(state.value, 0)


def test_carry_pairs_keeps_the_higher_level_and_moves_the_ledger():
    eng = AlertEngine(ResponseConfig())
    A = AlertState
    eng.restore({("c1", "old"): AlertRecord("c1", "old", A.EXTREME_24H,
                                            entered_at_s=1.0),
                 ("c2", "old"): AlertRecord("c2", "old", A.NOTICE_72H,
                                            entered_at_s=1.0),
                 ("c2", "new"): AlertRecord("c2", "new", A.WATCH,
                                            entered_at_s=2.0),
                 ("c3", "old"): AlertRecord("c3", "old", A.WATCH,
                                            entered_at_s=1.0)},
                notified_ledger={("c1", "old", "extreme_24h"): 5.0,
                                 ("c2", "old", "notice_72h"): 6.0,
                                 ("c2", "new", "watch"): 7.0})
    out = eng.carry_pairs("old", "new", 100.0)
    assert sorted(out) == [("c1", "carried", "extreme_24h"),
                           ("c2", "kept", "watch"),
                           ("c3", "carried", "watch")]
    st = eng.states()
    assert not [k for k in st if k[1] == "old"]
    assert st[("c1", "new")].state is A.EXTREME_24H
    assert st[("c2", "new")].state is A.WATCH
    led = eng.notified_ledger()
    assert led == {("c1", "new", "extreme_24h"): 5.0,
                   ("c2", "new", "notice_72h"): 6.0,
                   ("c2", "new", "watch"): 7.0}
    assert ("c3", "new") in eng._resend_pending    # WATCH never sent: owed
    assert [e.kind for e in eng.pop_operator_events()] == ["alerts_carried"]
    assert eng.carry_pairs("old", "new", 101.0) == []


# -- dismiss -----------------------------------------------------------------------

def heat_event(cl):
    from wgresponse.contracts import OperatorEvent
    return OperatorEvent(t_s=NOW, kind="satellite_heat", message="m",
                         incident_id=cl.incident_id,
                         detail={"nearest_m": 1600.0,
                                 "nearest_client": "near",
                                 "nearest_client_id": "c-near"})


def test_dismiss_quiets_the_page_expires_and_growth_undoes_it(tmp_path):
    cl = near_cluster()
    feed = MutableFeed([cl])
    mon, eng, dec, clock = build(tmp_path, feed)
    pager = paging.Pager(ResponseConfig(operator_paging=True),
                         state_dir=str(tmp_path / "p"),
                         clock=lambda: clock.t, key=b"k" * 32)
    pager.decisions = dec
    assert pager.observe_operator_event(heat_event(cl))      # paged
    code, d = dec.dismiss(cl, "Alex")
    assert code == 200 and d["expires_s"] == NOW + 12 * 3600.0
    assert pager.observe_operator_event(heat_event(cl)) is None  # quiet
    mon.cycle(NOW)
    assert dec.dismissed_for(cl.incident_id)
    # growth: a detection 1 km outside the footprint, same cluster id
    grown = _cluster(list(cl.hotspots) + [geo.destination(ORIGIN, 0.0,
                                                          2000.0)],
                     cid=cl.incident_id)
    feed.set([grown])
    r = mon.cycle(NOW + 120.0)
    grew = [e for e in r.operator_events if e.kind == "dismissed_heat_grew"]
    assert len(grew) == 1 and "outside" in grew[0].detail["why"]
    assert dec.dismissed_for(cl.incident_id) is None
    assert pager.observe_operator_event(grew[0])              # pages anew
    # a fresh dismissal expires after 12 h
    dec.dismiss(grown, "Alex")
    clock.t = NOW + 13 * 3600.0
    mon.cycle(NOW + 13 * 3600.0)
    assert dec.dismissed_for(cl.incident_id) is None


def test_doubled_detections_undo_a_dismissal():
    d = {"points": [ORIGIN], "n_detections": 2, "max_frp": 4.0}
    cl = _cluster([ORIGIN, geo.destination(ORIGIN, 0.0, 100.0),
                   geo.destination(ORIGIN, 0.0, 200.0),
                   geo.destination(ORIGIN, 0.0, 300.0)])
    assert "detections 2 -> 4" == decisions.OperatorDecisions._growth(
        d, cl, [ORIGIN])
    hot = dataclasses.replace(cl, meta=dict(cl.meta, n_detections=2,
                                            max_frp=9.0))
    assert "FRP" in decisions.OperatorDecisions._growth(d, hot, [ORIGIN])


def test_confirmed_fire_cannot_be_dismissed_and_confirm_supersedes(tmp_path):
    dec = decisions.OperatorDecisions()
    cl = near_cluster()
    dec.dismiss(cl, "Alex")
    code, rec = dec.confirm(cl, "Blair")
    assert code == 200
    assert dec.status()["dismissed"][0]["status"] == "superseded"
    assert dec.dismiss(cl, "Casey")[0] == 409
    assert dec.confirm(cl, "Casey")[1]["confirm_id"] == rec["confirm_id"]


# -- durable -----------------------------------------------------------------------

def test_decisions_survive_a_restart_and_match_by_footprint(tmp_path):
    cl = near_cluster()
    d1 = decisions.OperatorDecisions(state_dir=str(tmp_path / "s"))
    cid = d1.confirm(cl, "Alex")[1]["confirm_id"]
    d1.dismiss(_cluster([geo.destination(ORIGIN, 180.0, 30_000.0)]), "Alex")
    d2 = decisions.OperatorDecisions(state_dir=str(tmp_path / "s"))
    assert d2.restore() == 2
    # after the restart the FIRMS tracks start over: a new id, same place
    feed = MutableFeed([near_cluster(cid="firms:after-restart")])
    mon, eng, _, _ = build(tmp_path / "m", feed)
    mon.operator_decisions = d2
    assert cid in mon.cycle(NOW).ensembles
    audit = list((tmp_path / "s" / "oncall").glob("decisions-audit-*.jsonl"))
    rows = [json.loads(x) for f in audit
            for x in f.read_text(encoding="utf-8").splitlines()]
    assert [r["what"] for r in rows][:2] == ["confirmed", "dismissed"]


# -- surface -----------------------------------------------------------------------

def test_the_daemon_action_finds_the_heat_and_answers_its_page():
    cl = near_cluster()
    dec = decisions.OperatorDecisions()
    pager = paging.Pager(ResponseConfig(operator_paging=True),
                         key=b"k" * 32)
    pager.decisions = dec
    pid = pager.observe_operator_event(heat_event(cl))
    d = cli._WatchDaemon.__new__(cli._WatchDaemon)
    d.last_result = SimpleNamespace(incidents=[cl])
    d._monitor = SimpleNamespace(operator_decisions=None)
    d._pager = pager
    d._wake = threading.Event()
    d.attach_decisions(dec)
    assert d._monitor.operator_decisions is dec and pager.decisions is dec
    assert d.operator_action("confirm", {"incident_id": cl.incident_id})[0] \
        == 400                                       # who are you?
    assert d.operator_action("confirm", {"incident_id": "nope",
                                         "by": "Alex"})[0] == 404
    code, out = d.operator_action("confirm", {"incident_id": cl.incident_id,
                                              "by": "Alex"})
    assert code == 200 and d._wake.is_set()
    page = next(p for p in pager.status()["pages"] if p["page_id"] == pid)
    assert page["status"] == "acked" and page["acked_via"] == "confirm"
    assert d.operator_action("stand-down", {"confirm_id": out["confirm_id"],
                                            "by": "Alex"})[0] == 200
    d2 = cli._WatchDaemon.__new__(cli._WatchDaemon)
    assert d2.operator_action("confirm", {"by": "x"})[0] == 404   # off


def test_the_routes_are_authed_and_404_when_off(monkeypatch):
    monkeypatch.setenv("WGRESP_API_KEY", "k-1")
    monkeypatch.delenv("WGRESP_DEV_OPEN", raising=False)
    calls = []

    class Api:
        def operator_action(self, action, payload):
            calls.append((action, payload))
            return 200, {"ok": True}
    s = server.serve(0, state_provider=lambda: {})
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    port = s.server_address[1]

    def post(path, key=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Content-Type": "application/json"}
        if key:
            h["X-Internal-API-Key"] = key
        c.request("POST", path, body=json.dumps({"incident_id": "x",
                                                 "by": "A"}), headers=h)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    try:
        assert post("/api/operator/confirm", "k-1") == 404     # no provider
        s.app.operator_api = Api()
        assert post("/api/operator/confirm") == 401
        assert post("/api/operator/confirm", "k-1") == 200
        assert post("/api/operator/dismiss", "k-1") == 200
        assert post("/api/operator/stand-down", "k-1") == 200
        assert post("/api/operator/delete-everything", "k-1") == 404
        assert [c[0] for c in calls] == ["confirm", "dismiss", "stand-down"]
    finally:
        s.shutdown()
        th.join(timeout=5)
        s.server_close()


def test_config_rule_and_flag():
    assert any("operator_confirm" in p for p in validate_config(
        ResponseConfig(operator_confirm=True)))
    ok = ResponseConfig(operator_confirm=True, hotspot_heat_only=True)
    assert not any("operator_confirm" in p for p in validate_config(ok))
    cfg, notes = cli._feeds_cfg(ResponseConfig(),
                                argparse.Namespace(operator_confirm=True))
    assert cfg.operator_confirm
    assert any("SAFETY SEMANTICS 64" in n for n in notes)
    cfg0, notes0 = cli._feeds_cfg(ResponseConfig(), argparse.Namespace())
    assert not cfg0.operator_confirm


def test_dev_open_never_opens_an_operator_write(monkeypatch):
    """2026-11-19, found on the live watcher (its read API runs with
    WGRESP_DEV_OPEN=1): Confirm / Dismiss / stand-down and the console's
    page acknowledgement need the real key; with no key set they are
    refused even when the read API is open."""
    calls = []

    class Api:
        def operator_action(self, action, payload):
            calls.append(action)
            return 200, {"ok": True}

    class Pg:
        def ack(self, page_id, by, via):
            calls.append("ack")
            return {"status": "acked", "acked_by": by, "acked_s": 1.0}

        def status(self):
            return {"open": 0, "pages": []}
    s = server.serve(0, state_provider=lambda: {})
    s.app.operator_api = Api()
    s.app.pager = Pg()
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    port = s.server_address[1]

    def post(path, key=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Content-Type": "application/json"}
        if key:
            h["X-Internal-API-Key"] = key
        c.request("POST", path, body=json.dumps({"incident_id": "x",
                                                 "page_id": "p",
                                                 "by": "A"}), headers=h)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status
    try:
        monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
        monkeypatch.delenv("WGRESP_API_KEY", raising=False)
        for path in ("/api/operator/confirm", "/api/operator/dismiss",
                     "/api/operator/stand-down", "/api/pages/ack"):
            assert post(path) == 401, path              # open reads, closed writes
        monkeypatch.setenv("WGRESP_API_KEY", "k-9")
        assert post("/api/operator/confirm") == 401
        assert post("/api/operator/confirm", "wrong") == 401
        assert post("/api/operator/confirm", "k-9") == 200
        assert post("/api/pages/ack", "k-9") == 200
        assert calls == ["confirm", "ack"]
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/pages")                  # a READ stays open
        assert c.getresponse().status == 200
        c.close()
    finally:
        s.shutdown()
        th.join(timeout=5)
        s.server_close()
