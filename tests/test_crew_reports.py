"""tests/test_crew_reports.py — A CREW REPORT IS KEYED, SANE AND ANSWERED; A
CLIENT IS TOLD ONLY WHAT THE CREW PLAN SUPPORTS (SAFETY SEMANTICS 44, the
founder's crew-program review 2026-11-11, step 1 of its recommendations).

  KEYED     POST /api/beacon needs X-Beacon-Key (WGRESP_BEACON_KEY) or the
            ops key even under WGRESP_DEV_OPEN=1; no key configured = 401;
            the rest of the open demo stays open.
  SANE      a NaN / Infinity / millisecond t_s, a t_s more than 300 s ahead
            of the server clock and a non-finite accuracy are refused (400);
            an old report is still a valid report; the tracker refuses a
            non-finite t_s on its own.
  ANSWERED  a report the tracker drops as not newer says accepted: false.
  WORDING   cfg.alert_crew_wording "plan": NOTICE / WATCH / EXTREME /
            IMPACTED say only what the crews on duty and the current plan
            support; "fixed" keeps the original sentences byte for byte; the
            monitor tells the engine its crews and plan before evaluating;
            the watch flag and config validation."""
from __future__ import annotations

import dataclasses
import http.client
import json
import math
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_threat import (  # noqa: E402
    NOW,
    StubFeed,
    StubRegistry,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import cli, server  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertRecord,
    AlertState,
    CrewBeacon,
    CrewStatus,
    LatLon,
    ResponseConfig,
    validate_config,
)
from wgresponse.crews import CrewTracker  # noqa: E402

BEACON_KEY = "beacon-test-key"
OPS_KEY = "ops-test-key"


class _Srv:
    def __init__(self, srv, tracker):
        self.srv, self.tracker = srv, tracker

    def post(self, body, headers=None):
        host, port = self.srv.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            h = {"Content-Type": "application/json"}
            h.update(headers or {})
            conn.request("POST", "/api/beacon", body=raw, headers=h)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else None)
        finally:
            conn.close()

    def get(self, path):
        host, port = self.srv.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()


@pytest.fixture
def open_demo(monkeypatch):
    """The public deployments' posture: WGRESP_DEV_OPEN=1, no ops key."""
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    monkeypatch.delenv("WGRESP_API_KEY", raising=False)
    monkeypatch.delenv("WGRESP_BEACON_KEY", raising=False)
    tracker = CrewTracker(ResponseConfig())
    srv = server.serve(0, state_provider=lambda: {"crews": []},
                       beacon_sink=tracker)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield _Srv(srv, tracker)
    srv.shutdown()
    t.join(timeout=5)
    srv.server_close()


def _report(crew="G1", t_s=None, **kw):
    body = {"crew_id": crew, "lat": 39.0, "lon": -121.0,
            "t_s": time.time() - 30.0 if t_s is None else t_s}
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# KEYED
# ---------------------------------------------------------------------------

def test_an_open_demo_refuses_an_unkeyed_report(open_demo):
    status, data = open_demo.post(_report())
    assert status == 401 and "X-Beacon-Key" in data["error"]
    assert open_demo.tracker.crews() == []
    # the rest of the open demo stays open
    assert open_demo.get("/api/state")[0] == 200


def test_the_beacon_key_or_the_ops_key_opens_it(open_demo, monkeypatch):
    monkeypatch.setenv("WGRESP_BEACON_KEY", BEACON_KEY)
    assert open_demo.post(_report(), {"X-Beacon-Key": "wrong"})[0] == 401
    status, data = open_demo.post(_report(), {"X-Beacon-Key": BEACON_KEY})
    assert status == 200 and data["accepted"] is True
    assert [c.crew_id for c in open_demo.tracker.crews()] == ["G1"]
    # the ops key is not the device key, but ops tooling may report
    assert open_demo.post(_report("G2"), {"X-Internal-API-Key": OPS_KEY}
                          )[0] == 401
    monkeypatch.setenv("WGRESP_API_KEY", OPS_KEY)
    assert open_demo.post(_report("G2"), {"X-Internal-API-Key": OPS_KEY}
                          )[0] == 200


# ---------------------------------------------------------------------------
# SANE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_t, why", [
    (b"NaN", "finite"),
    (b"Infinity", "finite"),
    (b"-Infinity", "finite"),
])
def test_a_non_finite_time_is_refused(open_demo, monkeypatch, raw_t, why):
    monkeypatch.setenv("WGRESP_BEACON_KEY", BEACON_KEY)
    body = (b'{"crew_id": "G1", "lat": 39.0, "lon": -121.0, "t_s": '
            + raw_t + b"}")
    status, data = open_demo.post(body, {"X-Beacon-Key": BEACON_KEY})
    assert status == 400 and why in data["error"]
    assert open_demo.tracker.crews() == []


def test_milliseconds_and_a_fast_clock_are_refused(open_demo, monkeypatch):
    monkeypatch.setenv("WGRESP_BEACON_KEY", BEACON_KEY)
    h = {"X-Beacon-Key": BEACON_KEY}
    status, data = open_demo.post(_report(t_s=time.time() * 1000.0), h)
    assert status == 400 and "milliseconds" in data["error"]
    status, data = open_demo.post(_report(t_s=time.time() + 6 * 3600.0), h)
    assert status == 400 and "ahead of the server clock" in data["error"]
    # a phone a minute fast is normal
    status, data = open_demo.post(_report(t_s=time.time() + 60.0), h)
    assert status == 200 and data["accepted"] is True


def test_parse_beacon_rules():
    ok = server.parse_beacon(_report(t_s=5.0))            # old is still valid
    assert ok.t_s == 5.0
    for bad in ({"accuracy_m": float("nan")}, {"speed_mps": float("inf")}):
        with pytest.raises(ValueError, match="finite"):
            server.parse_beacon(_report(**bad))
    with pytest.raises(ValueError, match="ahead"):
        server.parse_beacon(_report(t_s=1000.0 + 301.0), now_s=1000.0)
    assert server.parse_beacon(_report(t_s=1000.0 + 299.0),
                               now_s=1000.0).t_s == 1299.0
    # no clock given: the future check is the caller's
    assert server.parse_beacon(_report(t_s=4e9)).t_s == 4e9


def test_the_tracker_refuses_a_non_finite_time_on_its_own():
    tr = CrewTracker(ResponseConfig())
    for t in (math.nan, math.inf):
        assert tr.ingest(CrewBeacon(crew_id="G1", loc=LatLon(39.0, -121.0),
                                    t_s=t)) is False
    assert tr.crews() == []
    assert tr.ingest(CrewBeacon(crew_id="G1", loc=LatLon(39.0, -121.0),
                                t_s=100.0)) is True
    assert [c.crew_id for c in tr.eligible(150.0)] == ["G1"]


# ---------------------------------------------------------------------------
# ANSWERED
# ---------------------------------------------------------------------------

def test_a_dropped_report_says_so(open_demo, monkeypatch):
    monkeypatch.setenv("WGRESP_BEACON_KEY", BEACON_KEY)
    h = {"X-Beacon-Key": BEACON_KEY}
    t = time.time() - 60.0
    assert open_demo.post(_report(t_s=t), h)[1]["accepted"] is True
    status, data = open_demo.post(_report(t_s=t - 10.0, lat=39.5), h)
    assert status == 200 and data["accepted"] is False
    assert "not newer" in data["ignored"]
    assert open_demo.tracker.crew("G1").last_beacon.loc.lat == 39.0


# ---------------------------------------------------------------------------
# WORDING
# ---------------------------------------------------------------------------

def _msg(engine, state, p50_h=5.0, p10_h=3.0, client="c1"):
    rec = AlertRecord(client_id=client, incident_id="inc-1")
    return engine._message(rec, state, p10_h, p50_h)


LEGACY = {
    AlertState.NOTICE_72H: "Advance notice — crews being scheduled.",
    AlertState.WATCH: "Under continuous watch; crews scheduled as needed.",
    AlertState.EXTREME_24H: "Crew dispatch underway — ensure property access "
                            "now.",
    AlertState.IMPACTED: "Crews are clear; post-impact assessment to follow.",
}


def test_fixed_wording_is_the_original_text():
    eng = AlertEngine(ResponseConfig())
    eng.set_crew_context(0, ())          # told nothing changes under "fixed"
    for state, sentence in LEGACY.items():
        assert sentence in _msg(eng, state)
    assert _msg(eng, AlertState.EXTREME_24H, p50_h=0.0).endswith(
        "do not approach; crews withdrawing.")


def test_plan_wording_with_no_crew_on_duty():
    eng = AlertEngine(ResponseConfig(alert_crew_wording="plan"))
    texts = {s: _msg(eng, s) for s in LEGACY}            # never told
    assert "no response crew is on duty yet" in texts[AlertState.NOTICE_72H]
    assert "no response crew is on duty yet" in texts[AlertState.WATCH]
    assert "No response crew is on duty — follow official evacuation " \
           "orders." in texts[AlertState.EXTREME_24H]
    assert texts[AlertState.IMPACTED].endswith(
        "Post-impact assessment to follow.")
    assert "Crews are clear" not in texts[AlertState.IMPACTED]
    assert "crews withdrawing" not in _msg(eng, AlertState.EXTREME_24H,
                                           p50_h=0.0)
    for t in texts.values():
        assert "Crew dispatch underway" not in t


def test_plan_wording_with_crews_on_duty_and_a_plan():
    eng = AlertEngine(ResponseConfig(alert_crew_wording="plan"))
    eng.set_crew_context(2, {"c1"})
    assert "A response crew is scheduled for this property" in \
        _msg(eng, AlertState.EXTREME_24H, client="c1")
    assert "Crews are being assigned; none is confirmed for this property" \
        in _msg(eng, AlertState.EXTREME_24H, client="c2")
    assert LEGACY[AlertState.NOTICE_72H] in _msg(eng, AlertState.NOTICE_72H,
                                                 client="c2")
    assert LEGACY[AlertState.IMPACTED] in _msg(eng, AlertState.IMPACTED,
                                               client="c1")
    assert "Crews are clear" not in _msg(eng, AlertState.IMPACTED,
                                         client="c2")
    assert _msg(eng, AlertState.EXTREME_24H, p50_h=0.0, client="c1").endswith(
        "crews withdrawing.")


def _cycle(cfg):
    mon = make_monitor(feeds=[StubFeed([incident()])], cfg=cfg,
                       registry=StubRegistry([near_client()]),
                       alert_engine=AlertEngine(cfg))
    return mon.cycle(NOW)


def test_the_monitor_tells_the_engine_before_it_speaks():
    fixed = _cycle(make_cfg())
    plan = _cycle(make_cfg(alert_crew_wording="plan"))
    assert fixed.events and len(fixed.events) == len(plan.events)
    assert [(e.client_id, e.state) for e in fixed.events] == \
        [(e.client_id, e.state) for e in plan.events]
    ext = [e for e in plan.events if e.state is AlertState.EXTREME_24H]
    assert ext, "the stub fire must reach EXTREME for this test"
    for e in ext:
        assert "No response crew is on duty" in e.message
    for e in fixed.events:
        if e.state is AlertState.EXTREME_24H:
            assert "Crew dispatch underway" in e.message


def test_the_watch_flag_and_validation():
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-121,38,-120,39", "--crew-text-from-plan"]))
    assert args.crew_text_from_plan is True
    cfg = ResponseConfig()
    assert cfg.alert_crew_wording == "fixed" and validate_config(cfg) == []
    probs = validate_config(dataclasses.replace(cfg,
                                                alert_crew_wording="loud"))
    assert any("alert_crew_wording" in p for p in probs)
