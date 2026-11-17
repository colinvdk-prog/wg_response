"""tests/test_crew_roster.py — PER-CREW IDENTITY (the roster) and CREW STATE
SURVIVES A RESTART (SAFETY SEMANTICS 45f / 45g; the crew-program review's lane
C findings F3 — one shared key, no per-crew identity — and F9 — a restart
empties the CrewTracker).

  ROSTER   env WGRESP_CREW_ROSTER names a JSON roster {crew_id, name,
           members, key_sha256}: a report for a crew not on it is refused, a
           report must carry ITS crew's key (sha256, constant time), the
           shared beacon key and the ops key report for no crew; the
           roster's crews are listed before their first report; a broken
           roster refuses everything (503) and stops `watch` at startup;
           no key is echoed or logged. Unset = SEMANTICS 44, unchanged.
  PERSIST  cfg.crew_state_persist: the tracker's last report per crew rides
           in the state file (crew_reports) and comes back on restart with
           its own time — staleness applies; restore never raises."""
from __future__ import annotations

import http.client
import json
import logging
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import cli, server  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    CrewBeacon,
    CrewStatus,
    LatLon,
    ResponseConfig,
)
from wgresponse.crews import (  # noqa: E402
    CrewRoster,
    CrewTracker,
    RosterError,
    key_sha256,
    parse_roster,
)
from wgresponse.persist import StateStore  # noqa: E402

T0 = ss.T0
KEY_G1 = "g1-own-secret"
KEY_G2 = "g2-own-secret"
SHARED = "shared-beacon-key"
OPS = "ops-key"


def _roster_rows(**extra):
    rows = [{"crew_id": "G1", "name": "Engine 1", "members": 3,
             "key_sha256": key_sha256(KEY_G1)},
            {"crew_id": "G2", "key_sha256": key_sha256(KEY_G2).upper()}]
    rows += [dict(r) for r in extra.get("more", [])]
    return rows


def _write(path, obj):
    path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj,
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# the roster file
# ---------------------------------------------------------------------------

def test_parse_roster_accepts_a_list_or_a_crews_object():
    a = parse_roster(json.dumps(_roster_rows()))
    b = parse_roster(json.dumps({"crews": _roster_rows()}))
    assert a == b and sorted(a) == ["G1", "G2"]
    assert a["G1"].name == "Engine 1" and a["G1"].members == 3
    assert a["G2"].name == "G2" and a["G2"].members == 2
    assert a["G2"].key_sha256 == key_sha256(KEY_G2)      # lower-cased


@pytest.mark.parametrize("bad, why", [
    ("not json", "not JSON"),
    ('{"crew": []}', "must be a list"),
    ('[1]', "not an object"),
    ('[{"key_sha256": "%s"}]' % ("a" * 64), "crew_id"),
    ('[{"crew_id": "G1", "key_sha256": "abc"}]', "key_sha256"),
    ('[{"crew_id": "G1", "key_sha256": "%s"}]' % ("z" * 64), "key_sha256"),
    ('[{"crew_id": "G1", "members": 0, "key_sha256": "%s"}]' % ("a" * 64),
     "members"),
    ('[{"crew_id": "G1", "members": true, "key_sha256": "%s"}]' % ("a" * 64),
     "members"),
    ('[{"crew_id": "G1", "key_sha256": "%s"}, {"crew_id": "G1", '
     '"key_sha256": "%s"}]' % ("a" * 64, "b" * 64), "listed twice"),
])
def test_parse_roster_refuses_a_malformed_roster(bad, why):
    with pytest.raises(RosterError) as ei:
        parse_roster(bad)
    assert why in str(ei.value)
    assert "a" * 64 not in str(ei.value)            # never a hash


def test_the_roster_verifies_each_crews_own_key(tmp_path):
    r = CrewRoster(_write(tmp_path / "r.json", _roster_rows()))
    assert r.verify("G1", KEY_G1) and r.verify("G2", KEY_G2)
    assert not r.verify("G1", KEY_G2)                # another crew's key
    assert not r.verify("G9", KEY_G1)                # not rostered
    assert not r.verify("G1", "")
    assert r.status() == {"crews": 2, "usable": True, "error": None}


def test_the_roster_is_re_read_and_a_broken_file_refuses_all(tmp_path):
    p = _write(tmp_path / "r.json", _roster_rows())
    r = CrewRoster(p)
    assert [e.crew_id for e in r.entries()] == ["G1", "G2"]
    _write(p, _roster_rows(more=[{"crew_id": "G3",
                                  "key_sha256": key_sha256("k3")}]))
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert [e.crew_id for e in r.entries()] == ["G1", "G2", "G3"]
    _write(p, "{broken")
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 2 * 10**9))
    with pytest.raises(RosterError):
        r.verify("G1", KEY_G1)                       # the old roster is gone
    assert r.status()["usable"] is False
    p.unlink()
    with pytest.raises(RosterError):
        r.get("G1")


# ---------------------------------------------------------------------------
# the tracker with a roster
# ---------------------------------------------------------------------------

def test_a_rostered_tracker_lists_its_crews_before_any_report(tmp_path):
    r = CrewRoster(_write(tmp_path / "r.json", _roster_rows()))
    tr = CrewTracker(ResponseConfig(), roster=r)
    crews = tr.crews()
    assert [(c.crew_id, c.name, c.members, c.status) for c in crews] == [
        ("G1", "Engine 1", 3, CrewStatus.UNKNOWN),
        ("G2", "G2", 2, CrewStatus.UNKNOWN)]
    assert all(c.loc() is None for c in crews)
    assert tr.stale(T0) == [] and tr.eligible(T0) == []
    # only a rostered crew reports
    assert not tr.ingest(CrewBeacon("X", LatLon(39.0, -121.0), T0))
    assert tr.crew("X") is None
    assert tr.ingest(CrewBeacon("G1", LatLon(39.0, -121.0), T0))
    assert [c.crew_id for c in tr.eligible(T0 + 1)] == ["G1"]
    # an unusable roster refuses every report
    _write(r.path, "[{]")
    os.utime(r.path, ns=(time.time_ns(), time.time_ns() + 10**9))
    assert not tr.ingest(CrewBeacon("G1", LatLon(39.0, -121.0), T0 + 5))


def test_no_roster_is_the_legacy_tracker():
    tr = CrewTracker(ResponseConfig())
    assert tr.roster is None and tr.crews() == []
    assert tr.ingest(CrewBeacon("anyone", LatLon(39.0, -121.0), T0))
    assert [c.crew_id for c in tr.crews()] == ["anyone"]


# ---------------------------------------------------------------------------
# the endpoint with a roster
# ---------------------------------------------------------------------------

class _Srv:
    def __init__(self, srv, tracker):
        self.srv, self.tracker = srv, tracker

    def post(self, body, headers=None):
        host, port = self.srv.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            h = {"Content-Type": "application/json"}
            h.update(headers or {})
            conn.request("POST", "/api/beacon", body=json.dumps(body).encode(),
                         headers=h)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None), raw
        finally:
            conn.close()


@pytest.fixture
def rostered(tmp_path, monkeypatch):
    path = _write(tmp_path / "roster.json", _roster_rows())
    monkeypatch.setenv("WGRESP_CREW_ROSTER", str(path))
    monkeypatch.setenv("WGRESP_BEACON_KEY", SHARED)
    monkeypatch.setenv("WGRESP_API_KEY", OPS)
    monkeypatch.setenv("WGRESP_DEV_OPEN", "1")
    roster = CrewRoster(path)
    tracker = CrewTracker(ResponseConfig(), roster=roster)
    srv = server.serve(0, state_provider=lambda: {}, beacon_sink=tracker)
    srv.app.crew_roster = roster
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield _Srv(srv, tracker)
    srv.shutdown()
    t.join(timeout=5)
    srv.server_close()


def _body(crew="G1", **kw):
    b = {"crew_id": crew, "lat": 39.0, "lon": -121.0,
         "t_s": time.time() - 30.0}
    b.update(kw)
    return b


def test_only_a_crews_own_key_reports_for_it(rostered, caplog):
    caplog.set_level(logging.DEBUG)
    ok = rostered.post(_body(), {"X-Beacon-Key": KEY_G1})
    assert ok[0] == 200 and ok[1]["accepted"] is True
    for crew, headers in (
            ("G1", {"X-Beacon-Key": KEY_G2}),            # another crew's key
            ("G1", {"X-Beacon-Key": SHARED}),            # the shared key
            ("G1", {"X-Internal-API-Key": OPS}),         # the ops key
            ("G1", {}),                                  # none
            ("X", {"X-Beacon-Key": KEY_G1}),             # not rostered
    ):
        status, data, raw = rostered.post(_body(crew), headers)
        assert status == 401, (crew, headers)
        assert "rostered crew_id" in data["error"]
        for secret in (KEY_G1, KEY_G2, SHARED, OPS):
            assert secret.encode() not in raw
    assert rostered.tracker.crew("X") is None
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (KEY_G1, KEY_G2, SHARED, OPS, key_sha256(KEY_G1)):
        assert secret not in logged
    # the crew's own key, its client_id carried through (SEMANTICS 45d)
    st, data, _raw = rostered.post(_body("G2", client_id=" c1 ",
                                         status="deploying"),
                                   {"X-Beacon-Key": KEY_G2})
    assert st == 200 and data["accepted"] is True
    assert rostered.tracker.crew("G2").last_beacon.client_id == "c1"


def test_a_broken_roster_refuses_every_report(rostered):
    _write(rostered.srv.app.crew_roster.path, "nope")
    os.utime(rostered.srv.app.crew_roster.path,
             ns=(time.time_ns(), time.time_ns() + 10**9))
    status, data, _raw = rostered.post(_body(), {"X-Beacon-Key": KEY_G1})
    assert status == 503 and "roster unusable" in data["error"]


def test_the_env_roster_applies_without_an_attached_one(tmp_path,
                                                        monkeypatch):
    path = _write(tmp_path / "roster.json", _roster_rows())
    monkeypatch.setenv("WGRESP_CREW_ROSTER", str(path))
    monkeypatch.setenv("WGRESP_BEACON_KEY", SHARED)
    tracker = CrewTracker(ResponseConfig())
    srv = server.serve(0, state_provider=lambda: {}, beacon_sink=tracker)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        s = _Srv(srv, tracker)
        assert s.post(_body(), {"X-Beacon-Key": SHARED})[0] == 401
        assert s.post(_body(), {"X-Beacon-Key": KEY_G1})[0] == 200
    finally:
        srv.shutdown()
        t.join(timeout=5)
        srv.server_close()


def test_bad_client_ids_are_refused():
    base = {"crew_id": "G1", "lat": 39.0, "lon": -121.0, "t_s": T0}
    assert server.parse_beacon(dict(base)).client_id == ""
    assert server.parse_beacon(dict(base, client_id=None)).client_id == ""
    for bad in (7, ["c1"], "x" * 129):
        with pytest.raises(ValueError):
            server.parse_beacon(dict(base, client_id=bad))


# ---------------------------------------------------------------------------
# the watch daemon with a roster
# ---------------------------------------------------------------------------

def test_watch_roster_helper(tmp_path, monkeypatch):
    monkeypatch.delenv("WGRESP_CREW_ROSTER", raising=False)
    assert cli._watch_roster() == (None, [])
    monkeypatch.setenv("WGRESP_CREW_ROSTER",
                       str(_write(tmp_path / "r.json", _roster_rows())))
    roster, notes = cli._watch_roster()
    assert len(roster.entries()) == 2
    assert "CREW ROSTER: 2 crew(s)" in notes[0]
    assert "SAFETY SEMANTICS 45f" in notes[0]
    monkeypatch.setenv("WGRESP_CREW_ROSTER", str(tmp_path / "missing.json"))
    with pytest.raises(RosterError):
        cli._watch_roster()                 # watch refuses to start (2)


def test_the_state_lists_roster_crews_before_their_first_report(tmp_path):
    roster = CrewRoster(_write(tmp_path / "r.json", _roster_rows()))
    cfg = ss.small_cfg()
    tracker = CrewTracker(cfg, roster=roster)
    daemon, *_ = ss.make_stack(tmp_path / "st", cfg=cfg, tracker=tracker,
                               test_world=False)
    st = daemon.state()
    assert [(c["crew_id"], c["name"], c["status"], c["lat"])
            for c in st["crews"]] == [("G1", "Engine 1", "unknown", None),
                                      ("G2", "G2", "unknown", None)]
    assert st["crew_roster"] == {"crews": 2, "usable": True, "error": None}
    daemon.run_cycle()                       # no crew reported: no crash
    assert "crews with stale GPS" not in " ".join(daemon.last_result.notes)


# ---------------------------------------------------------------------------
# PERSIST — crew state survives a restart
# ---------------------------------------------------------------------------

def test_rows_round_trip_with_their_own_time():
    cfg = ResponseConfig()
    a = CrewTracker(cfg)
    a.ingest(CrewBeacon("G1", LatLon(39.0, -121.0), T0,
                        CrewStatus.DEPLOYING, accuracy_m=12.0,
                        speed_mps=0.5, client_id="c1"))
    a.ingest(CrewBeacon("G2", LatLon(39.1, -121.1), T0 - 900.0))
    rows = a.rows()
    assert [r["crew_id"] for r in rows] == ["G1", "G2"]
    a.stale(T0 + 3600.0)                     # flips status, not the report
    assert a.rows()[0]["status"] == "deploying"
    b = CrewTracker(cfg)
    assert b.restore_rows(json.loads(json.dumps(rows))) == 2
    g1 = b.crew("G1")
    assert g1.last_beacon == a.crew("G1").last_beacon
    assert g1.status is CrewStatus.DEPLOYING
    assert b.history("G1") == [g1.last_beacon]
    # its own time: stale at once when old
    assert b.stale(T0 + cfg.beacon_stale_s + 1.0) == ["G1", "G2"]
    # a newer report is never rewound
    b.ingest(CrewBeacon("G2", LatLon(38.9, -121.2), T0 + 10.0))
    assert b.restore_rows(rows) == 0
    assert b.crew("G2").last_beacon.t_s == T0 + 10.0


def test_restore_never_raises_and_skips_what_it_cannot_use(tmp_path, caplog):
    roster = CrewRoster(_write(tmp_path / "r.json", _roster_rows()))
    tr = CrewTracker(ResponseConfig(), roster=roster)
    good = {"crew_id": "G1", "lat": 39.0, "lon": -121.0, "t_s": T0,
            "status": "en_route"}
    rows = [good, {"crew_id": "G2"}, {"crew_id": "G2", "lat": 99.0,
                                      "lon": 0.0, "t_s": T0},
            {"crew_id": "G2", "lat": 39.0, "lon": -121.0,
             "t_s": float("nan")},
            {"crew_id": "G2", "lat": 39.0, "lon": -121.0, "t_s": T0,
             "status": "flying"},
            {"crew_id": "X", "lat": 39.0, "lon": -121.0, "t_s": T0},
            "garbage", None]
    assert tr.restore_rows(rows) == 1
    assert tr.crew("X") is None
    assert tr.crew("G1").last_beacon.status is CrewStatus.EN_ROUTE
    assert tr.restore_rows(7) == 0
    assert tr.restore_rows(None) == 0


def test_the_store_carries_crew_reports_only_when_handed(tmp_path):
    s = StateStore(tmp_path)
    s.save({}, {}, None, {})
    doc = json.loads(s.path.read_text("utf-8"))
    assert "crew_reports" not in doc
    assert s.load()["crew_reports"] == []
    rows = [{"crew_id": "G1", "lat": 39.0, "lon": -121.0, "t_s": T0}]
    s.save({}, {}, None, {}, crew_reports=rows)
    assert s.load()["crew_reports"] == rows
    doc = json.loads(s.path.read_text("utf-8"))
    doc["crew_reports"] = 5                  # type-corrupt: loud, never fatal
    s.path.write_text(json.dumps(doc), "utf-8")
    assert s.load()["crew_reports"] == []


def _restart_drill(tmp_path, persist):
    cfg = ss.small_cfg(crew_state_persist=persist)
    clock = ss.SettableClock(T0)
    tr = CrewTracker(cfg)
    d, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock, tracker=tr,
                          test_world=False)
    tr.ingest(CrewBeacon("G1", ss.NEAR_LOC, T0 + 30, CrewStatus.DEPLOYING,
                         client_id="c1"))
    tr.ingest(CrewBeacon("G2", LatLon(39.12, -121.06), T0 + 30))
    clock.t = T0 + 60
    d.run_cycle()
    tr2 = CrewTracker(cfg)
    d2, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock, tracker=tr2,
                           test_world=False)
    assert d2.restore() is True
    return d, tr, d2, tr2


def test_a_restart_restores_the_crews_with_the_knob(tmp_path):
    _d, tr, d2, tr2 = _restart_drill(tmp_path, True)
    assert [c.crew_id for c in tr2.crews()] == ["G1", "G2"]
    assert tr2.crew("G1").last_beacon == tr.crew("G1").last_beacon
    assert [r["crew_id"] for r in d2.state()["crews"]] == ["G1", "G2"]
    # staleness applies to the restored reports: the next cycle 11 min on
    d2._clock.t = T0 + 30 + 660
    res = d2.run_cycle()
    assert any("stale GPS excluded from dispatch: G1, G2" in n
               for n in res.notes)


def test_without_the_knob_a_restart_empties_the_tracker(tmp_path):
    _d, _tr, _d2, tr2 = _restart_drill(tmp_path, False)
    assert tr2.crews() == []                 # lane C F9, as before
    doc = json.loads((tmp_path / "wgresponse_state.json").read_text("utf-8"))
    assert "crew_reports" not in doc


def test_a_restored_silent_field_crew_is_unjudged_by_the_watch(tmp_path):
    """Persist + watch: a crew that was deploying when the daemon stopped and
    has not reported since is listed unjudged (silent), never dropped."""
    cfg = ss.small_cfg(crew_state_persist=True, crew_watch=True)
    clock = ss.SettableClock(T0)
    tr = CrewTracker(cfg)
    d, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock, tracker=tr,
                          test_world=False)
    tr.ingest(CrewBeacon("G1", LatLon(39.09, -121.015), T0 + 30,
                         CrewStatus.DEPLOYING))
    clock.t = T0 + 60
    d.run_cycle()
    tr2 = CrewTracker(cfg)
    d2, *_ = ss.make_stack(tmp_path, cfg=cfg, clock=clock, tracker=tr2,
                           test_world=False)
    d2.restore()
    clock.t = T0 + 30 + cfg.beacon_stale_s + 120
    res = d2.run_cycle()
    (row,) = res.crew_watch["crews"]
    assert row["crew_id"] == "G1" and row["level"] == "unjudged"
    assert row["fresh"] is False
    assert [e.kind for e in res.operator_events
            if e.kind.startswith("crew_")] == ["crew_unjudged"]
