"""tests/test_new_fire_sentinel.py — A NEW FIRE IS SEEN WHEN IT IS
PUBLISHED, IN SHADOW (2026-11-18, the fire -> alert latency review, lane A
fix 7; BUILD lane 3; contracts SAFETY SEMANTICS 60; cfg.new_fire_sentinel_s
/ new_fire_sentinel_km; watch --new-fire-sentinel; wgresponse.sentinel).

  BASELINE  a source's first answered poll is its baseline: nothing in it
            is new, no event.
  NEW       a record that appears later gets first_seen_s; a non-RX one
            within the radius of a home is ONE new_fire_near_homes event at
            once (to the daemon's operator log and the sentinel's own list);
            far, RX or already-seen records raise none; WFIGS by IRWIN id,
            FIRIS by OBJECTID (a generalized polygon), CAL FIRE by UniqueId.
  FAILURE   a source that fails is counted, never raised; the other sources
            carry on; its baseline waits for its first answer.
  CYCLE     observe_cycle marks the records a cycle carried (cycle_seen_s,
            cycle_done_s, the lead).
  SHADOW    a live stack with the sentinel polling — new fires near the
            homes, a broken sentinel — gives the SAME cycle digests and the
            SAME state file as one without (injected clock); never a
            degraded cycle; its block and events appear in /api/state.
  THREAD    start() polls on a daemon thread; stop() joins it.
  CLI       --new-fire-sentinel S [--new-fire-sentinel-km KM] arms it."""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import threading
import time
import urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import cli, feeds, geo, sentinel  # noqa: E402
from wgresponse.contracts import (FireIncident, IncidentSource,  # noqa: E402
                                  LatLon, ResponseConfig)

NEAR = geo.destination(ss.NEAR_LOC, 270.0, 6000.0)       # 6 km from c1
FAR = geo.destination(ss.NEAR_LOC, 90.0, 40000.0)        # 40 km away


class AgencyServer:
    """The three agencies, as the sentinel asks them."""

    def __init__(self):
        self.points = []      # (irwin, name, type, LatLon)
        self.firis = []       # (oid, name, LatLon)
        self.calfire = []     # (uid, name, LatLon)
        self.fail = set()
        self.asked = []

    def add_point(self, irwin, name, loc, typ="WF"):
        self.points.append(("{%s}" % irwin.upper(), name, typ, loc))

    def __call__(self, url, headers=None):
        self.asked.append(url)
        base, _, qs = url.partition("?")
        if base == feeds.CalFireFeed.LIST_URL.split("?")[0]:
            if "calfire" in self.fail:
                return 503, b""
            return 200, json.dumps([
                {"UniqueId": u, "Name": n, "Latitude": p.lat,
                 "Longitude": p.lon, "Updated": "2026-11-18T10:00:00Z",
                 "Started": "2026-11-18T09:50:00Z", "AcresBurned": 5}
                for u, n, p in self.calfire]).encode()
        q = dict(urllib.parse.parse_qsl(qs))
        assert "_t" in q                       # always cache-busted
        if base == feeds.WfigsFeed.POINTS_URL:
            if "wfigs" in self.fail:
                return 503, b""
            rows = self.points
            if q["where"].startswith("IrwinID IN"):
                want = set(q["where"][len("IrwinID IN ("):-1]
                           .replace("'", "").split(","))
                rows = [r for r in rows if r[0] in want]
                return 200, json.dumps({"features": [
                    {"attributes": {"IrwinID": i, "IncidentName": n,
                                    "IncidentTypeCategory": t,
                                    "CreatedOnDateTime_dt": 1_795_000_000_000,
                                    "ModifiedOnDateTime_dt": 1_795_000_000_000,
                                    "FireDiscoveryDateTime": 1_794_999_000_000,
                                    "IncidentSize": 3},
                     "geometry": {"x": p.lon, "y": p.lat}}
                    for i, n, t, p in rows]}).encode()
            return 200, json.dumps({"features": [
                {"attributes": {"IrwinID": i}} for i, _, _, _ in rows]}).encode()
        if base == feeds.FirisFeed.LAYER_URL:
            if "firis" in self.fail:
                return 503, b""
            if q.get("returnIdsOnly") == "true":
                return 200, json.dumps({"objectIdFieldName": "OBJECTID",
                                        "objectIds": [o for o, _, _ in
                                                      self.firis]}).encode()
            assert q["maxAllowableOffset"] == "0.001"
            want = {int(x) for x in q["objectIds"].split(",")}
            d = 0.005
            return 200, json.dumps({"features": [
                {"attributes": {"OBJECTID": o, "mission": n,
                                "incident_name": None,
                                "CreationDate": 1_795_000_000_000,
                                "type": "Heat Perimeter"},
                 "geometry": {"rings": [[[p.lon - d, p.lat - d],
                                         [p.lon + d, p.lat - d],
                                         [p.lon + d, p.lat + d],
                                         [p.lon - d, p.lat + d],
                                         [p.lon - d, p.lat - d]]]}}
                for o, n, p in self.firis if o in want]}).encode()
        raise AssertionError(url)


def _cfg(**kw):
    base = dict(new_fire_sentinel_s=60.0)
    base.update(kw)
    return ss.small_cfg(**base)


def _sentinel(srv, clock=None, events=None, cfg=None):
    clock = clock or ss.SettableClock()
    return sentinel.NewFireSentinel(
        cfg or _cfg(), clients_fn=ss.clients, aoi=ss.AOI, http_get=srv,
        clock=clock, on_event=(events.append if events is not None
                               else None)), clock


def test_baseline_then_one_event_per_new_fire_near_a_home():
    srv = AgencyServer()
    srv.add_point("aaaa0000-0000-0000-0000-000000000001", "OLD", NEAR)
    events: list = []
    s, clock = _sentinel(srv, events=events)
    assert s.poll_once() == [] and events == []        # the baseline
    st = s.status()
    assert all(v["baseline"] for v in st["sources"].values())
    assert st["records"] == [] and st["polls"] == 1
    clock.t += 60.0
    srv.add_point("bbbb0000-0000-0000-0000-000000000002", "NEW NEAR", NEAR)
    srv.add_point("cccc0000-0000-0000-0000-000000000003", "NEW FAR", FAR)
    srv.add_point("dddd0000-0000-0000-0000-000000000004", "BURN", NEAR,
                  typ="RX")
    srv.firis.append((77, "CA-XXX-NEAR-N1", NEAR))
    srv.calfire.append(("u-9", "Calfire Near", NEAR))
    out = s.poll_once()
    assert [e.kind for e in out] == ["new_fire_near_homes"] * 3
    assert events == out
    names = sorted(e.detail["name"] for e in out)
    assert names == ["CA-XXX-NEAR-N1", "Calfire Near", "NEW NEAR"]
    ev = next(e for e in out if e.detail["source"] == "wfigs")
    assert ev.incident_id == "bbbb0000-0000-0000-0000-000000000002"
    assert ev.detail["client"] == "c1" and 5.0 < ev.detail["km"] < 7.0
    assert ev.t_s == clock.t and ev.detail["first_seen_s"] == clock.t
    recs = {r["name"]: r for r in s.status()["records"]}
    assert set(recs) == {"NEW NEAR", "NEW FAR", "BURN", "CA-XXX-NEAR-N1",
                         "Calfire Near"}
    assert recs["NEW FAR"]["event"] is False and recs["BURN"]["event"] is False
    assert recs["NEW NEAR"]["created_to_seen_s"] == clock.t - 1_795_000_000.0
    clock.t += 60.0
    assert s.poll_once() == []                         # never twice
    assert len(s.status()["events"]) == 3


def test_a_failing_source_is_counted_never_raised():
    srv = AgencyServer()
    srv.fail = {"wfigs"}
    s, clock = _sentinel(srv)
    s.poll_once()
    st = s.status()["sources"]
    assert st["wfigs"]["errors"] == 1 and st["wfigs"]["baseline"] is False
    assert st["wfigs"]["last"]["ok"] is False and "error" in \
        st["wfigs"]["last"]
    assert st["firis"]["baseline"] and st["calfire"]["baseline"]
    srv.fail = set()
    srv.add_point("eeee0000-0000-0000-0000-000000000005", "THERE", NEAR)
    clock.t += 60.0
    assert s.poll_once() == []                         # its baseline now
    assert s.status()["sources"]["wfigs"]["baseline"]


def test_observe_cycle_marks_the_lead():
    srv = AgencyServer()
    s, clock = _sentinel(srv)
    s.poll_once()
    irwin = "ffff0000-0000-0000-0000-000000000006"
    srv.add_point(irwin, "LEAD", NEAR)
    srv.firis.append((5, "CA-FIRIS-N2", NEAR))
    clock.t += 60.0
    s.poll_once()
    seen = clock.t
    inc = FireIncident(incident_id=irwin, name="LEAD",
                       source=IncidentSource.WFIGS, origin=NEAR,
                       updated_at_s=seen, irwin_id=irwin,
                       source_ids={"wfigs": irwin, "firis": "5"})
    res = type("R", (), {"incidents": [inc], "removed_incidents": []})()
    assert s.observe_cycle(res, seen + 400.0, seen + 700.0) == 2
    st = s.status()
    assert st["lead"]["n"] == 2 and st["lead"]["mean_s"] == 700.0
    assert s.observe_cycle(res, seen + 999.0, seen + 1999.0) == 0
    assert s.observe_cycle(object(), 0.0, 0.0) == 0     # never raises


# ---------------------------------------------------------------- SHADOW ---

def _run(tmp_path, name, with_sentinel, broken=False):
    clock = ss.SettableClock(ss.T0)
    daemon, monitor, *_ = ss.make_stack(tmp_path / name, clock=clock,
                                        test_world=False)
    srv = AgencyServer()
    s = None
    if with_sentinel:
        if broken:
            srv.fail = {"wfigs", "firis", "calfire"}
        s = sentinel.NewFireSentinel(_cfg(), clients_fn=ss.clients,
                                     aoi=ss.AOI, http_get=srv, clock=clock,
                                     on_event=daemon.add_operator_event)
        daemon.attach_sentinel(s)
        s.poll_once()                                  # baseline
    digests = []
    for k in range(3):
        clock.t = ss.T0 + 60.0 + k * ss.PERIOD_S
        if s is not None:
            srv.add_point(f"{k:04d}0000-0000-0000-0000-00000000000{k}",
                          f"NEW {k}", NEAR)
            s.poll_once()                              # between cycles
        res = daemon.run_cycle()
        digests.append(ss.cycle_digest(res))
        assert not res.degraded
    return digests, ss.state_digest(daemon._store), daemon, s


@pytest.mark.parametrize("broken", [False, True])
def test_the_cycle_reads_nothing_from_the_sentinel(tmp_path, broken):
    plain, plain_state, d0, _ = _run(tmp_path, "plain", False)
    shadow, shadow_state, d1, s = _run(tmp_path, "shadow", True, broken)
    assert shadow == plain and shadow_state == plain_state
    st = d1.state()
    assert "sentinel" in st and "sentinel" not in d0.state()
    if broken:
        assert st["sentinel"]["records"] == []
        assert all(v["errors"] == 4
                   for v in st["sentinel"]["sources"].values())
    else:
        kinds = [getattr(e, "kind", None) for e in st["operator_log"]]
        assert kinds.count("new_fire_near_homes") == 3
        assert len(st["sentinel"]["events"]) == 3
        # the daemon's operator log otherwise holds the cycles' events only
        assert [k for k in kinds if k != "new_fire_near_homes"] == [
            getattr(e, "kind", None) for e in d0.state()["operator_log"]]


def test_operator_log_is_safe_across_threads(tmp_path):
    clock = ss.SettableClock(ss.T0)
    daemon, *_ = ss.make_stack(tmp_path, clock=clock, test_world=False)
    from wgresponse.contracts import OperatorEvent

    def spam(tag):
        for i in range(300):
            daemon.add_operator_event(OperatorEvent(
                t_s=ss.T0, kind="x", message=f"{tag}{i}"))
    ts = [threading.Thread(target=spam, args=(t,)) for t in "abc"]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    log = daemon.state()["operator_log"]
    assert len(log) == daemon.OP_LOG_KEEP


# ---------------------------------------------------------------- THREAD ---

def test_start_polls_on_a_daemon_thread_and_stop_joins():
    srv = AgencyServer()
    cfg = dataclasses.replace(_cfg(), new_fire_sentinel_s=0.05)
    s = sentinel.NewFireSentinel(cfg, clients_fn=ss.clients, aoi=ss.AOI,
                                 http_get=srv)
    s.start()
    deadline = time.monotonic() + 5.0
    while s.status()["polls"] < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    s.stop()
    assert s.status()["polls"] >= 3
    assert not any(t.name == "new-fire-sentinel" for t in
                   threading.enumerate())


# ------------------------------------------------------------------- CLI ---

def test_flags_arm_the_sentinel():
    p = cli._build_parser()
    args = p.parse_args(cli._normalize_argv(
        ["watch", "--aoi", "-125,24,-66,50", "--new-fire-sentinel", "60",
         "--new-fire-sentinel-km", "10"]))
    cfg, notes = cli._feeds_cfg(ResponseConfig(), args)
    assert cfg.new_fire_sentinel_s == 60.0 and cfg.new_fire_sentinel_km == 10.0
    assert notes[0].startswith("NEW-FIRE SENTINEL (shadow)")
    assert cli._feed_transport(cfg) == {}               # no feed change
    rule = sentinel.rule_summary(cfg)
    assert rule["radius_km"] == 10.0 and rule["shadow"] is True


def test_default_radius_is_tripwire_plus_lag():
    # 8 km tripwire + 1.7 m/s x 40 min = 12.08 km -> 12
    assert ResponseConfig().new_fire_sentinel_km == 12.0
    assert abs(8.0 + 1.7 * 40 * 60 / 1000.0 - 12.0) < 0.1
