"""tests/test_coating_window.py — the COATING WINDOW and PROXIMITY FIRST
(founder 2026-10-27, verbatim: "in the engine, I do not want homes coated
more than 12 hours in advance, as long as there is sufficient crew capacity
to coat every home. I want ranking based on proximity as a priority in case
weather conditions change"; contracts dispatch_hold_per_home /
dispatch_proximity_rank / dispatch_proximity_band_m; docs/SIMULATOR_DESIGN.md
"Coating window and proximity first (2026-10-27)").

  KNOBS    both default OFF (byte-identical); the band is validated.
  GATE     the hold's release is per home: only the homes the deferred plan
           loses are released (a capacity crunch: as many held homes as were
           lost, earliest pessimistic arrival first), re-probed, latched per
           home, at most DISPATCH_HOLD_ROUNDS rounds then everything, loudly;
           the hold's clock is the P10 arrival. Off: the global latch. Run
           against a stand-in planner that loses exactly what each case says.
  WINDOW   on a real one-click scenario served with the page's flags, no home
           is coated more than 12 h before its earliest likely arrival
           unless the gate released it, and every reached home is coated.
  PROXIMITY the fire's current extent is the P10 <= now cells; distance to
           it matches brute force; on a real plan call the fleet schedule
           serves exactly the homes the count-maximal plan serves and works
           each crew's homes band by band, nearest the fire first; off, the
           plan is untouched.
  SERVER   a param-less session keeps the legacy 24 h global hold and no
           proximity order; hold_h / hold_per_home / proximity_first arm
           them (validated); the page sends all three.
"""
from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_sim_crew_placement import CENTER, UPWIND, _session
from wgresponse import dispatch, geo, server, simui
from wgresponse.contracts import (
    ClientProperty,
    DispatchJob,
    EnsembleArrival,
    GridSpec,
    Raster,
    ResponseConfig,
    ThreatEta,
    validate_config,
)

PAGE = Path(__file__).resolve().parent.parent / "console" / "simulator.html"
H = 3600.0


# ---------------------------------------------------------------------------
# KNOBS
# ---------------------------------------------------------------------------

def test_knobs_are_off_by_default_and_the_band_is_validated():
    cfg = ResponseConfig()
    assert cfg.dispatch_hold_per_home is False
    assert cfg.dispatch_proximity_rank is False
    assert cfg.dispatch_proximity_band_m == 1000.0
    assert validate_config(cfg) == []
    bad = dataclasses.replace(cfg, dispatch_proximity_band_m=0.0)
    assert any("dispatch_proximity_band_m" in p for p in validate_config(bad))


# ---------------------------------------------------------------------------
# GATE (a stand-in planner)
# ---------------------------------------------------------------------------

def _job(cid, now, p10_h, p50_h):
    return DispatchJob(client=ClientProperty(client_id=cid, loc=CENTER),
                       incident_id="sim-f1",
                       eta=ThreatEta(now + p10_h * H, now + p50_h * H,
                                     now + (p50_h + 2.0) * H))


def _plan_of(served):
    return SimpleNamespace(
        plans={"sim-g1": SimpleNamespace(stops=[
            SimpleNamespace(client_id=c) for c in sorted(served)])},
        objective_breakdown={})


class _Planner:
    """dispatch.plan stand-in: serves every job except those `lose(deferred
    ids)` names; records each call's deferred ids and their (p10, p50)."""

    def __init__(self, lose):
        self.lose = lose
        self.calls = []

    def __call__(self, crew_args, jobs, *a, **kw):
        # a deferred probe job carries p50' = clock + (gel_window - hold) h
        # and p10' = p10 - buffer: a p50'-p10' gap no real job has here
        deferred = {j.client.client_id for j in jobs
                    if j.eta.eta_p50_s - j.eta.eta_p10_s > 50 * H}
        self.calls.append((deferred, {j.client.client_id: (
            j.eta.eta_p10_s, j.eta.eta_p50_s) for j in jobs}))
        return _plan_of({j.client.client_id for j in jobs}
                        - set(self.lose(deferred)))


def _gate(tmp_path, monkeypatch, lose, jobs_h, per_home=True):
    s = _session(tmp_path, seed="gate", cfg={
        "dispatch_hold_h": 12.0, "dispatch_hold_per_home": per_home})
    now = s._now_s
    jobs = [_job(c, now, p10, p50) for c, p10, p50 in jobs_h]
    planner = _Planner(lose)
    monkeypatch.setattr(simui.dispatch, "plan", planner)
    out = s._apply_coating_hold(jobs, [], {}, [], {}, now)
    return s, now, jobs, planner, out


#           id   p10 h  p50 h
JOBS = [("A", 13.0, 20.0), ("B", 30.0, 40.0), ("C", 40.0, 50.0),
        ("D", 2.0, 5.0)]


def test_only_the_home_the_deferral_costs_is_released(tmp_path, monkeypatch):
    s, now, jobs, planner, (active, probe, held) = _gate(
        tmp_path, monkeypatch, lambda d: {"A"} & d, JOBS)
    assert [j.client.client_id for j in active] == ["A", "D"]
    assert held == {"B", "C"} and probe is not None
    assert s._hold_released_ids == {"A"} and s._hold_released is False
    assert (s._unserved_reason["B"], s._unserved_reason["C"]) == ("held",) * 2
    assert [c[0] for c in planner.calls] == [set(), {"A", "B", "C"},
                                             {"B", "C"}]
    # the hold's clock is the P10: released at P10 - 12 h, buffer on P10
    p10b, p50b = planner.calls[2][1]["B"]
    assert p10b == pytest.approx(now + 30.0 * H - 6.0 * H)
    assert p50b == pytest.approx(now + 30.0 * H + (72.0 - 12.0) * H)
    assert any(n.startswith("COATING WINDOW — released 1 home(s) to coat "
                            "now (A)") for n in s._notes)
    st = s.state()
    assert st["hold"]["per_home"] is True
    assert st["hold"]["released_homes"] == ["A"]
    assert st["hold"]["held"] == 2 and st["hold"]["released"] is False
    # latched per home: the next cycle starts without A among the held
    planner.calls.clear()
    s._apply_coating_hold(jobs, [], {}, [], {}, now)
    assert planner.calls[1][0] == {"B", "C"}


def test_off_the_first_loss_releases_every_home(tmp_path, monkeypatch):
    s, _now, jobs, _p, (active, probe, held) = _gate(
        tmp_path, monkeypatch, lambda d: {"A"} & d, JOBS, per_home=False)
    assert active == jobs and probe is None and held == set()
    assert s._hold_released is True and s._hold_released_ids == set()
    assert "per_home" not in s.state()["hold"]


def test_a_capacity_crunch_releases_the_earliest_held_homes(tmp_path,
                                                            monkeypatch):
    """D is not held, yet the deferred plan loses it while two or more homes
    wait: the gate releases held homes earliest-P10 first until it does
    not."""
    s, _now, _jobs, planner, (active, _probe, held) = _gate(
        tmp_path, monkeypatch, lambda d: {"D"} if len(d) >= 2 else set(),
        JOBS)
    assert s._hold_released_ids == {"A", "B"} and held == {"C"}
    assert sorted(j.client.client_id for j in active) == ["A", "B", "D"]
    assert [c[0] for c in planner.calls] == [set(), {"A", "B", "C"},
                                             {"B", "C"}, {"C"}]


def test_after_the_last_round_every_held_home_is_released_loudly(
        tmp_path, monkeypatch):
    jobs_h = [(f"H{i}", 20.0 + i, 30.0 + i) for i in range(1, 7)]
    s, _now, jobs, _p, (active, probe, held) = _gate(
        tmp_path, monkeypatch, lambda d: {max(d)} if d else set(), jobs_h)
    assert held == set() and probe is None and active == jobs
    assert s._hold_released_ids == {f"H{i}" for i in range(1, 7)}
    assert s._hold_released is False                 # per home, not latched
    (note,) = [n for n in s._notes if n.startswith("COATING WINDOW —")]
    assert f"(after {simui.DISPATCH_HOLD_ROUNDS} rounds every remaining " \
           f"home was released)" in note


# ---------------------------------------------------------------------------
# PROXIMITY
# ---------------------------------------------------------------------------

def _square_fire(now=100.0):
    spec = GridSpec(north_lat=40.0, west_lon=-106.0, cell_m=100.0,
                    nrows=50, ncols=50)
    r = Raster(spec, fill=math.inf)
    for row in range(20, 25):
        for col in range(20, 25):
            r.set(row, col, 0.0)
    return EnsembleArrival(spec=spec, members=[r], p10=r, p50=r, p90=r,
                           t0_s=0.0), now


def test_proximity_is_the_distance_to_the_fire_now():
    ens, now = _square_fire()
    spec = ens.spec
    inside = geo.rc_to_latlon(spec, 22, 22)
    assert dispatch._proximity_m({"f": ens}, now, inside) == 0.0
    east = geo.rc_to_latlon(spec, 22, 30)            # 6 cells east of col 24
    assert dispatch._proximity_m({"f": ens}, now, east) == pytest.approx(
        600.0, abs=0.5)
    burned = [(row, col) for row in range(20, 25) for col in range(20, 25)]
    for row, col in [(0, 0), (49, 3), (10, 45), (31, 22), (24, 40)]:
        p = geo.rc_to_latlon(spec, row, col)
        brute = min(math.hypot(row - r2, col - c2) for r2, c2 in burned) * 100
        assert dispatch._proximity_m({"f": ens}, now, p) == pytest.approx(
            brute, abs=0.5)
    west = geo.LatLon(40.0 - 22.5 * 100 / geo.M_PER_DEG_LAT, -106.05)
    assert math.isfinite(dispatch._proximity_m({"f": ens}, now, west))
    # before the fire has reached any cell: no distance
    assert dispatch._proximity_m({"f": ens}, -1.0, east) == math.inf


@pytest.fixture(scope="module")
def plan_call(tmp_path_factory):
    """The first tick's real dispatch.plan call of a HIGH fire 8 km upwind
    of the crew-placement test town (8 homes, 3 crews)."""
    s = _session(tmp_path_factory.mktemp("prox"), n_clients=8, n_crews=3,
                 seed="prox")
    ign = geo.destination(CENTER, UPWIND, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    calls = []
    orig = simui.dispatch.plan

    def rec(*a, **kw):
        calls.append((a, kw))
        return orig(*a, **kw)
    simui.dispatch.plan = rec
    try:
        s.tick()
    finally:
        simui.dispatch.plan = orig
    assert calls
    return calls[-1]


def _replan(call, **knobs):
    a, kw = call
    cfg = dataclasses.replace(a[4], **knobs)
    return dispatch.plan(*a[:4], cfg, *a[5:], **kw)


def _served(p):
    return {(s.client_id, s.incident_id) for cp in p.plans.values()
            for s in cp.stops}


def test_proximity_first_serves_the_same_homes_nearest_first(plan_call):
    base = _replan(plan_call, dispatch_proximity_rank=False)
    prox = _replan(plan_call, dispatch_proximity_rank=True)
    assert "proximity" not in base.objective_breakdown
    tag = prox.objective_breakdown["proximity"]
    assert tag in ("fleet", "route")
    assert _served(prox) == _served(base)            # count-safe per call
    ens, now = plan_call[0][2], plan_call[0][5]
    jobs = {j.client.client_id: j for j in plan_call[0][1]}
    band = ResponseConfig().dispatch_proximity_band_m
    for cp in prox.plans.values():
        d = [dispatch._proximity_m(ens, now, jobs[st.client_id].client.loc)
             for st in cp.stops]
        bands = [math.floor(x / band) for x in d]
        if tag == "fleet":
            assert bands == sorted(bands), (cp.crew_id, d)
        assert all(st.margin_h >= ResponseConfig().slack_min / 60.0 - 1e-9
                   for st in cp.stops)


def test_proximity_off_leaves_the_plan_untouched(plan_call):
    a, kw = plan_call
    first = dispatch.plan(*a, **kw)
    again = _replan(plan_call, dispatch_proximity_rank=False)
    assert [(c, [(s.client_id, s.depart_s) for s in p.stops])
            for c, p in sorted(first.plans.items())] == [
        (c, [(s.client_id, s.depart_s) for s in p.stops])
        for c, p in sorted(again.plans.items())]


# ---------------------------------------------------------------------------
# SERVER + PAGE
# ---------------------------------------------------------------------------

def _cfg_of(mgr, payload):
    code, out = mgr.new_session(payload)
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    return s._cfg, s.state()["cmd_log"][0]["cfg_overrides"]


def test_the_server_keeps_the_legacy_hold_unless_asked():
    mgr = server.SimSessionManager()
    base = {"location": "boulder_co", "clients": 4, "crews": 1}
    cfg, _o = _cfg_of(mgr, base)
    assert (cfg.dispatch_hold_h, cfg.dispatch_hold_per_home,
            cfg.dispatch_proximity_rank) == (24.0, False, False)
    cfg, over = _cfg_of(mgr, dict(base, hold_h=12, hold_per_home=True,
                                  proximity_first=True))
    assert (cfg.dispatch_hold_h, cfg.dispatch_hold_per_home,
            cfg.dispatch_proximity_rank) == (12.0, True, True)
    assert (over["dispatch_hold_h"], over["dispatch_hold_per_home"],
            over["dispatch_proximity_rank"]) == (12.0, True, True)
    cfg, _o = _cfg_of(mgr, dict(base, hold_24h=False, hold_h=12))
    assert cfg.dispatch_hold_h == 0.0
    for bad in ({"hold_per_home": "yes"}, {"proximity_first": 1},
                {"hold_h": 0}, {"hold_h": 100}, {"hold_h": True},
                {"hold_h": "12"}):
        code, out = mgr.new_session(dict(base, **bad))
        assert code == 400, (bad, out)


def test_the_page_asks_for_the_window_and_proximity():
    src = PAGE.read_text(encoding="utf-8")
    assert "hold_h: 12, hold_per_home: true, proximity_first: true" in src


# ---------------------------------------------------------------------------
# WINDOW (a real scenario, as the page builds it)
# ---------------------------------------------------------------------------

def test_no_home_is_coated_more_than_12h_ahead_unless_released():
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": "vail_westerly", "hold_h": 12,
                                 "hold_per_home": True,
                                 "proximity_first": True})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    first = {}
    st = None
    for _ in range(24):
        st = s.tick()
        for cl in st["clients"]:
            if cl["gel_applied"] and cl["client_id"] not in first:
                first[cl["client_id"]] = (cl["gelled_at_s"], cl["eta_p10_s"])
    released = set(st["hold"]["released_homes"])
    assert first, "the scenario must coat homes"
    for cid, (g, p10) in first.items():
        if cid not in released:
            assert p10 - g <= 12.0 * H + 1.0, (cid, (p10 - g) / H)
    rep = st["report"]
    assert rep["reached_uncoated"] == 0
    assert st["scoreboard"]["violations"] == 0
    assert st["scoreboard"]["crew_overruns_total"] == 0
