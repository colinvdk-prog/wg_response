"""tests/test_live_crew_watch.py — THE LIVE CREW WATCH and A HOME BEING WORKED
IS NOT REASSIGNED (SAFETY SEMANTICS 45a-d; the founder's crew-program review,
scratch_review/crew_program_review_2026-11-11, step 3, lane C findings F1 and
F4).

  F1  a crew reporting DEPLOYING at c1 whose P10 lead fell to 24 min (law 30)
      got no withdrawal, no operator event and no note. With cfg.crew_watch
      the watch judges it every cycle: ONE event per crossing, a note every
      cycle, /api/state crew_watch.
  LAW its spot (10a), its home (2 / 7) and its way out (the planner's egress
      law, dispatch.egress_table) — synthetic rasters pin each margin, each
      level and the urgency order.
  FAIL CLOSED  silent; a coarse fix; no forecast fire; a spot no forecast
      covers; a failed fire nearby; an exception; a failing tracker; no safe
      zone — never clear.
  F4  a second crew sent to the home a deploying crew reports working: with
      cfg.crew_hold_worked the home is held (reason in_progress_by:<crew>).
  OFF both knobs off: the pinned pre-W1 goldens; the watch's shared rasters
      leave the plan unchanged when on."""
from __future__ import annotations

import dataclasses
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seeded_stack as ss  # noqa: E402
from wgresponse import crewwatch, dispatch, geo, safety  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    Crew,
    CrewBeacon,
    CrewStatus,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    ResponseConfig,
    SafeZone,
    ThreatEta,
    validate_config,
)
from wgresponse.crews import CrewTracker  # noqa: E402
from wgresponse.travel import travel_time_s  # noqa: E402

T0 = ss.T0
C1 = ss.NEAR_LOC


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _stack(tmp_path, registry_clients=None, **cfgkw):
    cfg = ss.small_cfg(**cfgkw)
    tracker = CrewTracker(cfg)
    clock = ss.SettableClock(T0)
    d, m, feed, eng, notif, store, wx, ter = ss.make_stack(
        tmp_path / "st", cfg=cfg, clock=clock, tracker=tracker,
        test_world=False, registry_clients=registry_clients)
    return SimpleNamespace(cfg=cfg, tracker=tracker, clock=clock, daemon=d,
                           monitor=m, feed=feed, store=store, weather=wx)


def _report(tracker, cid, loc, t_s, status=CrewStatus.AVAILABLE,
            client_id="", accuracy_m=30.0):
    assert tracker.ingest(CrewBeacon(crew_id=cid, loc=loc, t_s=t_s,
                                     status=status, client_id=client_id,
                                     accuracy_m=accuracy_m))


def _lane_c_f1(s, client_id=""):
    """Lane C's probe P6b: G1 planned for c1, then deploying AT c1 while the
    feed's fire jumps to 1.2 km upwind (wind 14 m/s); three cycles."""
    _report(s.tracker, "G1", LatLon(39.10, -121.05), T0 + 30)
    s.clock.t = T0 + 60
    first = s.daemon.run_cycle()
    _report(s.tracker, "G1", C1, T0 + 1800, CrewStatus.DEPLOYING, client_id)
    near = geo.destination(C1, 270.0, 1200.0)
    s.feed.incidents = [ss.feed_incident(origin=near,
                                         updated_at_s=T0 + 1700,
                                         acreage=800.0)]
    s.weather.wind_mps = 14.0
    out = []
    for k in range(3):
        s.clock.t = T0 + 1860 + k * 300
        _report(s.tracker, "G1", C1, s.clock.t - 10, CrewStatus.DEPLOYING,
                client_id)
        out.append(s.daemon.run_cycle())
    return first, out


def _crew_events(res):
    return [e for e in res.operator_events if e.kind.startswith("crew_")]


# synthetic rasters for the law tests: a 20 x 20 km tile, uniform arrival
SPEC = GridSpec(north_lat=39.1, west_lon=-121.12, cell_m=200.0, nrows=100,
                ncols=100)
CREW_AT = LatLon(39.0, -121.0)
ZONE = SafeZone(zone_id="ZN", loc=geo.destination(CREW_AT, 0.0, 1000.0))


def _uniform(arrival_s):
    return [("inc-x", geo.make_raster(SPEC, fill=arrival_s))]


def _crew(status=CrewStatus.EN_ROUTE, t_s=T0, loc=CREW_AT, client_id="",
          accuracy_m=30.0, cid="G1"):
    b = CrewBeacon(crew_id=cid, loc=loc, t_s=t_s, status=status,
                   client_id=client_id, accuracy_m=accuracy_m)
    return Crew(crew_id=cid, name=cid, status=status, last_beacon=b)


def _way_min(cfg):
    """The chord law's minutes from the uniform arrival to the latest lawful
    departure on the crew's leg to ZONE: the route margin + the drive."""
    d = geo.haversine_m(CREW_AT, ZONE.loc)
    return cfg.route_margin_min + d / safety._corridor_speed_mps(cfg) / 60.0


def _judge(cfg, crew, arrival_min, now=T0, **kw):
    kw.setdefault("rasters", _uniform(now + arrival_min * 60.0))
    kw.setdefault("safe_zones", [ZONE])
    return crewwatch.judge_crew(crew, now, cfg, **kw)


# ---------------------------------------------------------------------------
# config + flag
# ---------------------------------------------------------------------------

def test_knobs_default_off_and_validated():
    cfg = ResponseConfig()
    assert cfg.crew_watch is False and cfg.crew_hold_worked is False
    assert cfg.crew_state_persist is False
    assert cfg.crew_work_radius_m == 150.0
    assert validate_config(cfg) == []
    for bad in (-1.0, 1000.5, float("nan"), float("inf")):
        probs = validate_config(dataclasses.replace(cfg,
                                                    crew_work_radius_m=bad))
        assert any("crew_work_radius_m" in p for p in probs), bad
    assert any("crew_watch must be a bool" in p for p in validate_config(
        dataclasses.replace(cfg, crew_watch=1)))


def test_watch_flags_arm_the_knobs_with_startup_notes():
    from wgresponse import cli
    args = cli._build_parser().parse_args(
        cli._normalize_argv(["watch", "--aoi", "-121,38,-120,39", "--crew-watch",
                             "--hold-worked-homes", "--persist-crews"]))
    cfg, notes = cli._crew_program_cfg(ResponseConfig(), args)
    assert cfg.crew_watch and cfg.crew_hold_worked and cfg.crew_state_persist
    assert len(notes) == 3
    assert all("SAFETY SEMANTICS 45" in n for n in notes)
    assert "never safe" in notes[0] and "in_progress_by" in notes[1]
    none = cli._build_parser().parse_args(cli._normalize_argv(["watch", "--aoi", "-121,38,-120,39"]))
    assert cli._crew_program_cfg(ResponseConfig(), none) == (
        ResponseConfig(), [])


# ---------------------------------------------------------------------------
# byte identity
# ---------------------------------------------------------------------------

def test_knobs_off_reproduce_the_pinned_pre_w1_goldens(tmp_path):
    """The SEMANTICS 44 / W1 goldens (tests/test_seeded_incidents.py) with
    every SEMANTICS 45 knob at its default."""
    from tests.test_seeded_incidents import (_GOLDEN_API, _GOLDEN_CYCLES,
                                             _GOLDEN_STATE)
    cycles, api, state = ss.baseline_run(tmp_path / "state")
    assert (cycles, api, state) == (_GOLDEN_CYCLES, _GOLDEN_API,
                                    _GOLDEN_STATE)


def test_knob_off_no_attribute_no_state_key_no_event(tmp_path):
    s = _stack(tmp_path)
    first, cycles = _lane_c_f1(s)
    for res in [first] + cycles:
        assert not hasattr(res, "crew_watch")
        assert _crew_events(res) == []
        assert not any("crew watch" in n for n in res.notes)
    st = s.daemon.state()
    assert "crew_watch" not in st and "crew_roster" not in st
    # F4 still reproduces with the hold off: the plan simply drops G1
    assert "G1" not in cycles[0].plan.plans


def test_the_watch_hands_the_planner_the_same_rasters(tmp_path):
    """crew_watch erodes the fires once and passes them to dispatch.plan as
    eroded_rasters: every plan equals the knob-off plan."""
    plans = {}
    for on in (False, True):
        s = _stack(tmp_path / str(on), crew_watch=on)
        _report(s.tracker, "G1", LatLon(39.10, -121.05), T0 + 30,
                CrewStatus.EN_ROUTE)
        _report(s.tracker, "G2", LatLon(39.12, -121.06), T0 + 30)
        seq = []
        for k in range(3):
            s.clock.t = T0 + 60 + k * 300
            res = s.daemon.run_cycle()
            seq.append(ss._canon(res.plan))
            _report(s.tracker, "G1", LatLon(39.10 - 0.01 * k, -121.05),
                    s.clock.t + 1, CrewStatus.EN_ROUTE)
        plans[on] = seq
    assert plans[True] == plans[False]
    assert '"G1"' in plans[True][0]          # the en-route crew was planned


# ---------------------------------------------------------------------------
# F1 — the deploying crew past its line
# ---------------------------------------------------------------------------

def test_lane_c_f1_one_event_per_crossing_a_note_every_cycle(tmp_path):
    s = _stack(tmp_path, crew_watch=True)
    first, cycles = _lane_c_f1(s)
    assert first.crew_watch["crews"] == []          # G1 available: unwatched
    ev = _crew_events(cycles[0])
    assert [e.kind for e in ev] == ["crew_no_way_out"]
    det = ev[0].detail
    assert det["crew_id"] == "G1" and det["status"] == "deploying"
    assert det["client_id"] == "c1" and det["margin_min"] < 0.0
    assert det["best_zone_id"] == "Z1" and det["way_out"] is None
    assert ev[0].incident_id == "inc-1"
    assert "NO LAWFUL WAY OUT" in ev[0].message
    for res in cycles[1:]:
        assert _crew_events(res) == []              # same crossing: no event
    for res in cycles:
        assert sum("crew watch (SAFETY SEMANTICS 45): crew G1" in n
                   for n in res.notes) == 1
        (row,) = res.crew_watch["crews"]
        assert row["level"] == "no_way_out"
        # every finding, whatever the level: the home deadline passed
        # (P10 lead 24.2 min < 30), the spot past its line, no lawful leg
        assert row["home"]["client_id"] == "c1"
        assert row["home"]["source"] == "nearby"    # no client_id sent
        assert -6.5 < row["home"]["margin_min"] < -5.0
        assert row["spot"]["margin_min"] < 0.0
        assert row["way_out"]["lawful"] is False
        assert any("latest lawful departure passed" in r
                   for r in row["reasons"])
    st = s.daemon.state()
    (row,) = st["crew_watch"]["crews"]
    assert row["crew_id"] == "G1" and row["level"] == "no_way_out"
    assert st["crew_watch"]["rule"]["egress_buffer_min"] == 30.0
    assert any(e["kind"] == "crew_no_way_out" for e in
               [ss.jsonable(o) for o in st["operator_log"]])
    # operator-facing only: the cycle is not degraded by it
    assert not cycles[0].degraded


def test_a_reported_client_id_names_the_home(tmp_path):
    s = _stack(tmp_path, crew_watch=True)
    _first, cycles = _lane_c_f1(s, client_id="c1")
    (row,) = cycles[0].crew_watch["crews"]
    assert row["client_id"] == "c1"
    assert row["home"]["source"] == "report"
    assert row["homes"] == ["c1"]


def test_a_crew_in_the_clear_is_listed_clear_without_an_event(tmp_path):
    """Inside the fire's domain, up the flank the forecast never reaches in
    its horizon: covered, clear, no event, no note."""
    s = _stack(tmp_path, crew_watch=True)
    _report(s.tracker, "G1", LatLon(39.09, -121.015), T0 + 30,
            CrewStatus.EGRESSING)
    s.clock.t = T0 + 60
    res = s.daemon.run_cycle()
    (row,) = res.crew_watch["crews"]
    assert row["level"] == "clear" and row["unjudged"] == []
    assert row["spot"]["covered"] is True
    assert row["way_out"]["lawful"] and row["way_out"]["zone_id"] == "Z1"
    assert row["margin_min"] is None             # nothing bounds it
    assert _crew_events(res) == []
    assert not any("crew watch" in n for n in res.notes)


def test_a_crew_off_every_domain_is_unjudged_in_the_cycle(tmp_path):
    """West of the fire's domain (no fail-closed law armed): no forecast
    covers it — unjudged with one event, never clear."""
    s = _stack(tmp_path, crew_watch=True)
    _report(s.tracker, "G1", LatLon(39.15, -121.05), T0 + 30,
            CrewStatus.EGRESSING)
    s.clock.t = T0 + 60
    res = s.daemon.run_cycle()
    (row,) = res.crew_watch["crews"]
    assert row["level"] == "unjudged"
    assert row["spot"]["covered"] is False
    assert [e.kind for e in _crew_events(res)] == ["crew_unjudged"]


def test_available_off_duty_and_unreported_crews_are_not_watched():
    cfg = ResponseConfig(crew_watch=True)
    crews = [_crew(CrewStatus.AVAILABLE, cid="A"),
             _crew(CrewStatus.OFF_DUTY, cid="B"),
             _crew(CrewStatus.INBOUND, cid="C"),
             Crew(crew_id="D"),
             _crew(CrewStatus.DEPLOYING, cid="E"),
             _crew(CrewStatus.EGRESSING, cid="F"),
             _crew(CrewStatus.EN_ROUTE, cid="G"),
             _crew(CrewStatus.DEPLOYING, cid="H",
                   t_s=T0 - crewwatch.SILENT_WATCH_S - 1.0)]
    assert [c.crew_id for c in crewwatch.watched(crews, T0)] == ["E", "F",
                                                                 "G"]
    assert cfg.crew_watch


# ---------------------------------------------------------------------------
# LAW — margins and levels on synthetic rasters
# ---------------------------------------------------------------------------

def test_levels_follow_the_tightest_margin():
    cfg = ResponseConfig(crew_watch=True)
    way = _way_min(cfg)
    clear = _judge(cfg, _crew(), way + 60.0)
    assert clear["level"] == "clear"
    assert clear["way_out"]["margin_min"] == pytest.approx(60.0, abs=0.05)
    assert clear["spot"]["margin_min"] == pytest.approx(
        way + 60.0 - cfg.egress_buffer_min, abs=0.05)
    risk = _judge(cfg, _crew(), way + 10.0)
    assert risk["level"] == "at_risk" and risk["binding"] == "way_out"
    assert risk["margin_min"] == pytest.approx(10.0, abs=0.05)
    assert any("slack floor" in r for r in risk["reasons"])
    gone = _judge(cfg, _crew(), way - 5.0)
    assert gone["level"] == "no_way_out"
    assert gone["way_out"]["lawful"] is False
    assert gone["way_out"]["best_zone_id"] == "ZN"
    assert gone["way_out"]["margin_min"] == pytest.approx(-5.0, abs=0.05)
    # spot past its line too: still the most urgent level
    deep = _judge(cfg, _crew(), cfg.egress_buffer_min - 3.0)
    assert deep["level"] == "no_way_out"
    assert deep["spot"]["margin_min"] == pytest.approx(-3.0, abs=0.05)


def test_a_working_crews_home_deadline_is_the_law_of_semantics_2():
    cfg = ResponseConfig(crew_watch=True, ember_buffer_min=10.0)
    home = ClientProperty(client_id="h1", loc=CREW_AT, name="h1")
    etas = {("h1", "inc-x"): ThreatEta(T0 + 35 * 60.0, T0 + 3600.0,
                                       T0 + 7200.0)}
    row = _judge(cfg, _crew(CrewStatus.DEPLOYING, client_id="h1"),
                 _way_min(cfg) + 120.0, etas=etas, clients={"h1": home})
    # P10 35 min out less egress 30 and ember 10 = 5 min ago
    assert row["home"]["margin_min"] == pytest.approx(-5.0, abs=1e-6)
    assert row["home"]["incident_id"] == "inc-x"
    assert row["level"] == "must_leave" and row["binding"] == "home"
    assert row["way_out"]["lawful"] is True        # the road out is open
    # several homes close by: the earliest deadline governs
    near = ClientProperty(client_id="h2",
                          loc=geo.destination(CREW_AT, 90.0, 60.0))
    etas[("h2", "inc-x")] = ThreatEta(T0 + 30 * 60.0, T0 + 3600.0,
                                      T0 + 7200.0)
    row = _judge(cfg, _crew(CrewStatus.DEPLOYING, client_id="h1"),
                 _way_min(cfg) + 120.0, etas=etas,
                 clients={"h1": home, "h2": near})
    assert row["homes"] == ["h1", "h2"]
    assert row["home"]["client_id"] == "h2"
    assert row["home"]["margin_min"] == pytest.approx(-10.0, abs=1e-6)
    # an en-route crew is not judged against a home
    row = _judge(cfg, _crew(CrewStatus.EN_ROUTE, client_id="h1"),
                 _way_min(cfg) + 120.0, etas=etas, clients={"h1": home})
    assert row["home"] is None and row["level"] == "clear"


def test_a_crew_far_from_the_home_it_reports_is_said():
    cfg = ResponseConfig(crew_watch=True)
    far = ClientProperty(client_id="h9",
                         loc=geo.destination(CREW_AT, 180.0, 3000.0))
    row = _judge(cfg, _crew(CrewStatus.DEPLOYING, client_id="h9"),
                 _way_min(cfg) + 120.0, clients={"h9": far})
    assert any("reports working h9 but is 3.00 km from it" in r
               for r in row["reasons"])


def test_the_off_domain_bound_judges_a_spot_off_every_raster():
    """Fail-closed law armed: a spot beyond the domain is judged by the
    corridor law's boundary bound (and the spot is not 'covered')."""
    cfg = ResponseConfig(crew_watch=True, corridor_fail_closed=True,
                         corridor_unreached_bound=True)
    outside = geo.destination(CREW_AT, 0.0, 15000.0)        # north of SPEC
    row = _judge(cfg, _crew(loc=outside), 120.0,
                 safe_zones=[SafeZone("ZF", geo.destination(outside, 0.0,
                                                            500.0))])
    assert row["spot"]["covered"] is False
    assert row["spot"]["margin_min"] is not None
    assert not any("no forecast covers" in u for u in row["unjudged"])


# ---------------------------------------------------------------------------
# FAIL CLOSED
# ---------------------------------------------------------------------------

def test_a_silent_crew_is_unjudged_never_clear():
    cfg = ResponseConfig(crew_watch=True)
    silent = _crew(CrewStatus.DEPLOYING, t_s=T0 - cfg.beacon_stale_s - 60.0)
    row = _judge(cfg, silent, _way_min(cfg) + 120.0)
    assert row["level"] == "unjudged" and row["fresh"] is False
    assert any(u.startswith("silent 11 min") for u in row["unjudged"])
    # ... and a silent crew whose last spot is past the line is told so
    row = _judge(cfg, silent, 10.0)
    assert row["level"] == "no_way_out"
    assert row["unjudged"]


def test_a_coarse_fix_is_unjudged():
    cfg = ResponseConfig(crew_watch=True)
    row = _judge(cfg, _crew(accuracy_m=5000.0), _way_min(cfg) + 120.0)
    assert row["level"] == "unjudged"
    assert any("5000 m" in u for u in row["unjudged"])


def test_no_forecast_fire_and_an_uncovered_spot_are_unjudged():
    cfg = ResponseConfig(crew_watch=True)
    row = _judge(cfg, _crew(), 0.0, rasters=[], has_forecast=False)
    assert row["level"] == "unjudged"
    assert any("no forecast fire this cycle" in u for u in row["unjudged"])
    outside = geo.destination(CREW_AT, 0.0, 15000.0)
    row = _judge(cfg, _crew(loc=outside), 120.0,
                 safe_zones=[SafeZone("ZF", geo.destination(outside, 0.0,
                                                            500.0))])
    assert row["level"] == "unjudged"
    assert any("no forecast covers its spot" in u for u in row["unjudged"])


def test_a_failed_fire_nearby_makes_the_crew_unjudged():
    cfg = ResponseConfig(crew_watch=True)
    inc = FireIncident(incident_id="inc-dead", name="x",
                       source=IncidentSource.MOCK,
                       origin=geo.destination(CREW_AT, 90.0, 5000.0),
                       updated_at_s=T0, active=True)
    row = _judge(cfg, _crew(), _way_min(cfg) + 120.0, failed=[inc],
                 threat_radius_m=60000.0)
    assert row["level"] == "unjudged"
    assert any("inc-dead" in u for u in row["unjudged"])
    row = _judge(cfg, _crew(), _way_min(cfg) + 120.0, failed=[inc],
                 threat_radius_m=1000.0)
    assert row["level"] == "clear"


def test_no_safe_zone_is_no_way_out():
    cfg = ResponseConfig(crew_watch=True)
    row = _judge(cfg, _crew(), _way_min(cfg) + 120.0, safe_zones=[])
    assert row["level"] == "no_way_out"
    assert any("no safe zone is configured" in r for r in row["reasons"])


def test_an_exception_lists_the_crew_unjudged(monkeypatch):
    cfg = ResponseConfig(crew_watch=True)

    def boom(*a, **k):
        raise RuntimeError("table exploded")
    monkeypatch.setattr(dispatch, "egress_table", boom)
    (row,) = crewwatch.judge([_crew()], T0, cfg, rasters=_uniform(T0 + 1e6),
                             safe_zones=[ZONE])
    assert row["level"] == "unjudged"
    assert "table exploded" in row["unjudged"][0]


def test_a_failing_watch_or_tracker_fails_closed_in_the_cycle(tmp_path,
                                                              monkeypatch):
    s = _stack(tmp_path, crew_watch=True)
    _report(s.tracker, "G1", LatLon(39.15, -121.05), T0 + 30,
            CrewStatus.EN_ROUTE)
    monkeypatch.setattr(crewwatch, "judge",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("judge exploded")))
    s.clock.t = T0 + 60
    res = s.daemon.run_cycle()
    (row,) = res.crew_watch["crews"]
    assert row["level"] == "unjudged"
    assert res.crew_watch["error"] == "judge exploded"
    assert [e.kind for e in _crew_events(res)] == ["crew_unjudged"]
    monkeypatch.undo()

    class Broken:
        def crews(self):
            raise RuntimeError("tracker down")
    s.monitor._crew_tracker = Broken()
    s.clock.t += 300
    res = s.monitor.cycle(s.clock.t)
    assert res.crew_watch["error"] == "tracker down"
    ev = [e for e in res.operator_events if e.kind == "crew_unjudged"]
    assert ev and "no crew is judged this cycle" in ev[0].message


# ---------------------------------------------------------------------------
# EVENTS — one per crossing
# ---------------------------------------------------------------------------

def test_one_event_per_crossing_and_again_after_a_fall():
    rows = lambda lv: [{"crew_id": "G1", "level": lv, "status": "en_route",
                        "lat": 39.0, "lon": -121.0, "reasons": [],
                        "unjudged": [], "margin_min": 1.0}]
    seq = ["clear", "at_risk", "at_risk", "no_way_out", "must_leave",
           "no_way_out", "clear", "unjudged"]
    prev, kinds = {}, []
    for lv in seq:
        ev, notes, prev = crewwatch.events_and_notes(rows(lv), prev, T0)
        kinds.append([e.kind for e in ev])
        assert len(notes) == (0 if lv == "clear" else 1)
    assert kinds == [[], ["crew_at_risk"], [], ["crew_no_way_out"], [],
                     ["crew_no_way_out"], [], ["crew_unjudged"]]


def test_a_crew_leaving_the_field_is_noted(tmp_path):
    s = _stack(tmp_path, crew_watch=True)
    _report(s.tracker, "G1", LatLon(39.15, -121.05), T0 + 30,
            CrewStatus.EN_ROUTE)
    s.clock.t = T0 + 60
    s.daemon.run_cycle()
    _report(s.tracker, "G1", LatLon(39.15, -121.05), T0 + 100)
    s.clock.t = T0 + 360
    res = s.daemon.run_cycle()
    assert res.crew_watch["crews"] == []
    assert any("G1 left the watch" in n for n in res.notes)


# ---------------------------------------------------------------------------
# F4 — the worked home is held
# ---------------------------------------------------------------------------

def _two_crews_then_g1_deploys(s, client_id="c1", loc=C1, accuracy_m=30.0):
    _report(s.tracker, "G1", LatLon(39.10, -121.05), T0 + 30)
    _report(s.tracker, "G2", LatLon(39.12, -121.06), T0 + 30)
    s.clock.t = T0 + 60
    first = s.daemon.run_cycle()
    _report(s.tracker, "G1", loc, T0 + 2400, CrewStatus.DEPLOYING,
            client_id, accuracy_m=accuracy_m)
    _report(s.tracker, "G2", LatLon(39.12, -121.06), T0 + 2400)
    s.clock.t = T0 + 2460
    return first, s.daemon.run_cycle()


def test_lane_c_f4_reproduced_with_the_hold_off(tmp_path):
    s = _stack(tmp_path)
    first, res = _two_crews_then_g1_deploys(s)
    assert [st.client_id for st in first.plan.plans["G1"].stops] == ["c1"]
    assert [st.client_id for st in res.plan.plans["G2"].stops] == ["c1"]


def test_the_worked_home_is_held_for_its_crew(tmp_path):
    s = _stack(tmp_path, crew_hold_worked=True)
    _first, res = _two_crews_then_g1_deploys(s)
    assert all(st.client_id != "c1" for cp in res.plan.plans.values()
               for st in cp.stops)
    held = [(u.client_id, u.reason) for u in res.plan.unserved]
    assert ("c1", "in_progress_by:G1") in held
    assert any("c1 held for crew G1" in n for n in res.notes)
    # the hold is the incumbent the next cycle reads (wording context)
    assert s.monitor.incumbent is res.plan


def test_a_home_inferred_within_the_radius_is_held(tmp_path):
    s = _stack(tmp_path, crew_hold_worked=True)
    _first, res = _two_crews_then_g1_deploys(
        s, client_id="", loc=geo.destination(C1, 45.0, 80.0))
    assert ("c1", "in_progress_by:G1") in [(u.client_id, u.reason)
                                           for u in res.plan.unserved]


def test_an_ambiguous_spot_holds_nothing_and_says_so(tmp_path):
    twin = ClientProperty(client_id="c1b",
                          loc=geo.destination(C1, 0.0, 60.0),
                          name="Casa Uno B", graphgel_enrolled=True)
    s = _stack(tmp_path, crew_hold_worked=True,
               registry_clients=ss.clients() + [twin])
    _first, res = _two_crews_then_g1_deploys(s, client_id="")
    assert not any(u.reason.startswith("in_progress_by:")
                   for u in res.plan.unserved)
    assert any("ambiguous, no home held" in n and "c1, c1b" in n
               for n in res.notes)


def test_a_stale_or_not_deploying_report_holds_nothing(tmp_path):
    s = _stack(tmp_path, crew_hold_worked=True)
    _report(s.tracker, "G1", LatLon(39.10, -121.05), T0 + 30)
    _report(s.tracker, "G2", LatLon(39.12, -121.06), T0 + 30)
    s.clock.t = T0 + 60
    s.daemon.run_cycle()
    _report(s.tracker, "G1", C1, T0 + 100, CrewStatus.DEPLOYING, "c1")
    _report(s.tracker, "G2", LatLon(39.12, -121.06), T0 + 2400)
    s.clock.t = T0 + 2460                     # G1's report: 39 min old
    res = s.daemon.run_cycle()
    assert not any(u.reason.startswith("in_progress_by:")
                   for u in res.plan.unserved)


def test_the_wording_context_counts_a_held_home_as_served(tmp_path):
    s = _stack(tmp_path, crew_hold_worked=True, alert_crew_wording="plan")
    _two_crews_then_g1_deploys(s)
    told = {}
    s.monitor._alert_engine.set_crew_context = (
        lambda on_duty, planned: told.update(on_duty=on_duty,
                                             planned=set(planned)))
    s.monitor._set_crew_context(s.clock.t)
    assert "c1" in told["planned"]


def test_the_console_routes_list_a_held_home(tmp_path):
    """The state the console reads carries the hold (rendered in
    tests/test_console_itinerary.py)."""
    s = _stack(tmp_path, crew_hold_worked=True, crew_watch=True)
    _two_crews_then_g1_deploys(s)
    st = ss.jsonable(s.daemon.state())
    assert {"client_id": "c1", "incident_id": "inc-1",
            "reason": "in_progress_by:G1"} in st["plan"]["unserved"]
    (row,) = st["crew_watch"]["crews"]
    assert row["crew_id"] == "G1" and row["home"]["client_id"] == "c1"
