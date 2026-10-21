"""SAFETY SEMANTICS 26 — A COMMITTED NEAR-TERM STOP STAYS COMMITTED
(2026-10-21; the Eagle River valley review; contracts dispatch_keep_frozen; docs/
SIMULATOR_DESIGN.md "The audit sees the whole drive").

dispatch.plan's warm start repairs the incumbent with only the stops it
finds in the job pool, keyed (client_id, incident_id), and the simulator
kept an eligible crew's FROZEN stops (service start inside
commitment_horizon_min) out of its pool — so the commitment freeze never
engaged for them: whenever any crew idled (a re-plan every such tick) an
en-route crew about to arrive lost its head stop. Measured on the valley
harness: 39 en-route head swaps, 261 km driven for them; a crew 17 m from a
driveway sent 4.7 km away and back, both homes unprotected.

With the knob on, the simulator's pool keeps an eligible crew's frozen
stops, and plan() matches each incumbent stop to its client's surviving job
by client_id (a second fire may have changed the worst-case incident), so
the freeze holds it — still dropped when it is no longer feasible.

These tests pin the knob (default off, armed by the simulator), the planner's
match by client, the ping-pong it ends on the valley's Eagle-Vail canonical
fire, and byte-identity with it off there (goldens computed on commit
7bebc7a, before the rule)."""
from __future__ import annotations

import hashlib
import json

import pytest

from wgresponse import dispatch, server, simui
from wgresponse.contracts import (CrewPlan, DispatchPlan, PlannedStop,
                                  ResponseConfig, SafeZone)

from tests.test_dispatch import (NOW, _dup_client_jobs, _fingerprint, cc,
                                 make_crew, spec20)

#: ... and the iteration-2 knobs (SAFETY SEMANTICS 28-33, 2026-10-21), armed
#: by the simulator since: this file's goldens and derivations predate
#: them too (their own goldens and armed behaviour: tests/test_crew_pocket.
#: py, test_egress_guard.py, test_stage_by_need.py, test_egress_margin.py)
ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False,
             # ... and the iteration-3 knobs (SAFETY SEMANTICS 34-35)
             "sim_far_side_zones": False, "reinforce_entry_by_need": False,
             "ember_eta_landings": False}
ITER1_OFF = {"crew_path_audit": False, "dispatch_keep_frozen": False,
             "plan_route_exact": False, "crew_ingress_judged": False,
             **ITER2_OFF}
FROZEN_ONLY = dict(ITER1_OFF, dispatch_keep_frozen=True)


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def test_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().dispatch_keep_frozen is False
    s = simui.SimSession("boulder_co", 2, 1, seed="kf", roads_module=None)
    assert s._cfg.dispatch_keep_frozen is True
    s = simui.SimSession("boulder_co", 2, 1, seed="kf", roads_module=None,
                         cfg_overrides={"dispatch_keep_frozen": False})
    assert s._cfg.dispatch_keep_frozen is False


# ---------------------------------------------------------------------------
# the planner: the incumbent matched by client (SEMANTICS 26b)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("knob", [True, False])
def test_a_frozen_stop_whose_worst_case_fire_changed_stays_frozen(knob):
    """Crew A holds c1, frozen (service in 10 min), planned when inc-b was
    its only fire; inc-a now reaches c1 first, so the pool's surviving job
    for c1 is (c1, inc-a). Churn and adoption are off, so only the freeze
    can hold the stop (the closer crew B wins it free — tests/test_dispatch.
    py::test_frozen_stop_not_reassigned). Armed: the incumbent stop is
    matched by client — A keeps c1, now under inc-a and its tighter
    deadline. Off: the (c1, inc-b) stop is not in the pool; dropped, B
    takes c1."""
    cfg = ResponseConfig(churn_penalty_frac=0.0, adoption_threshold_frac=0.0,
                         dispatch_keep_frozen=knob)
    spec = spec20()
    loc = cc(spec, 10, 12)
    crews = [make_crew("A", cc(spec, 10, 2)), make_crew("B", cc(spec, 10, 11))]
    zones = [SafeZone("z1", cc(spec, 8, 12))]
    ja, jb, ens = _dup_client_jobs(spec, loc)
    stop = PlannedStop(client_id="c1", incident_id="inc-b",
                       arrive_s=NOW + 600.0, service_start_s=NOW + 600.0,
                       depart_s=NOW + 2_400.0,
                       latest_depart_s=NOW + 7 * 3_600.0 - 1_800.0,
                       margin_h=2.0)
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"A": CrewPlan(crew_id="A", stops=[stop])})
    plan = dispatch.plan(crews, [ja, jb], ens, zones, cfg, NOW,
                         incumbent=incumbent)
    held = {cid: [(st.client_id, st.incident_id) for st in cp.stops]
            for cid, cp in plan.plans.items()}
    if knob:
        assert held == {"A": [("c1", "inc-a")], "B": []}
        (st,) = plan.plans["A"].stops
        assert st.latest_depart_s == pytest.approx(
            ja.eta.eta_p10_s - cfg.egress_buffer_min * 60.0)
    else:
        assert held == {"A": [], "B": [("c1", "inc-a")]}
    assert plan.unserved == []


def test_an_unchanged_key_is_matched_as_before():
    """The match by client only re-keys a stop whose exact key left the pool:
    with one fire the armed planner returns the legacy plan exactly."""
    spec = spec20()
    loc = cc(spec, 10, 12)
    crews = [make_crew("A", cc(spec, 10, 2)), make_crew("B", cc(spec, 10, 11))]
    zones = [SafeZone("z1", cc(spec, 8, 12))]
    ja, _jb, ens = _dup_client_jobs(spec, loc)
    stop = PlannedStop(client_id="c1", incident_id="inc-a",
                       arrive_s=NOW + 600.0, service_start_s=NOW + 600.0,
                       depart_s=NOW + 2_400.0,
                       latest_depart_s=NOW + 5 * 3_600.0 - 1_800.0,
                       margin_h=2.0)
    incumbent = DispatchPlan(computed_at_s=NOW - 900.0,
                             plans={"A": CrewPlan(crew_id="A", stops=[stop])})
    plans = []
    for knob in (False, True):
        cfg = ResponseConfig(churn_penalty_frac=0.0,
                             adoption_threshold_frac=0.0,
                             dispatch_keep_frozen=knob)
        plans.append(_fingerprint(dispatch.plan(
            crews, [ja], {"inc-a": ens["inc-a"]}, zones, cfg, NOW,
            incumbent=incumbent)))
    assert plans[0] == plans[1]


# ---------------------------------------------------------------------------
# the ping-pong on the valley's Eagle-Vail canonical fire (SEMANTICS 26a+b)
# ---------------------------------------------------------------------------

#: sha256(state minus cmd_log)[:16] per tick, computed on 7bebc7a (before
#: SEMANTICS 26): the Eagle River valley harness's Eagle-Vail canonical
#: moderate fire (scratch_review/eagle_valley/run_matrix.py)
GOLDEN_EV_CANONICAL_MODERATE = [
    "bee6cef04725e331", "3e39e18a565883c2", "5b127a4f099101b8",
    "63549e7d6d59ff89", "8d4da1e824e594a7", "369bccdb19f60e06"]


def _valley_canonical(cfg, key="eagle_vail_corridor_westerly",
                      intensity="moderate"):
    """The valley harness's canonical run: the one-click roster (40 homes,
    2 in-zone crews + 1 h reinforcement, the page's defaults), its own fire
    re-lit at its origin and wind at `intensity`."""
    if not simui.tile_file("eagle_vail_co").exists():
        pytest.skip("LOUD SKIP: eagle_vail_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    origin, wind = f.origin, f.wind_dir_deg
    s.reset()
    s.ignite(origin.lat, origin.lon, intensity=intensity, wind_dir_deg=wind)
    return s


def _heads(st):
    now = st["sim_now_s"]
    return {c["crew_id"]: (c["status"], c["itinerary"][0]["client_id"],
                           round((c["itinerary"][0]["planned_arrive_s"]
                                  - now) / 60.0, 1))
            for c in st["crews"] if c["itinerary"]}


def test_the_ping_pong_ends_and_the_knob_off_is_byte_identical(monkeypatch):
    """Tick 4: sim-g1 is 0.8 min from sim-c10 and sim-g2 8.3 min from
    sim-c22, both frozen. Tick 5: the 1 h reinforcement arrives idle and
    dispatch re-plans. Off (byte-identical to the pre-rule session): both
    frozen stops are left out of the job pool, the incumbent repair drops
    them, and both crews are sent elsewhere (sim-c37, sim-c23) — 4 homes by
    tick 6. Armed: both stops are in the pool and stay frozen on their
    crews; both deploy there — 5 homes by tick 6."""
    pools = {}
    real_plan = dispatch.plan

    def spy(crews, jobs, *a, **kw):
        pools.setdefault(tag[0], []).append({j.client.client_id for j in jobs})
        return real_plan(crews, jobs, *a, **kw)

    monkeypatch.setattr(simui.dispatch, "plan", spy)
    runs = {}
    for name, cfg in (("off", ITER1_OFF), ("armed", FROZEN_ONLY)):
        tag = [name]
        s = _valley_canonical(cfg)
        hashes, states = [], []
        for _ in range(len(GOLDEN_EV_CANONICAL_MODERATE)):
            if len(states) == 4:
                pools[name] = []                 # the tick-5 plan's pool
            st = s.tick()
            hashes.append(_hash(st))
            states.append(st)
        runs[name] = (hashes, states)
    off_h, off_st = runs["off"]
    on_h, on_st = runs["armed"]
    assert off_h == GOLDEN_EV_CANONICAL_MODERATE
    assert on_h[:4] == GOLDEN_EV_CANONICAL_MODERATE[:4]
    assert on_h[4] != GOLDEN_EV_CANONICAL_MODERATE[4]
    assert _heads(off_st[3]) == {"sim-g1": ("en_route", "sim-c10", 0.8),
                                 "sim-g2": ("en_route", "sim-c22", 8.3)}
    assert _heads(on_st[3]) == _heads(off_st[3])
    # the tick-5 re-plan's job pool (the first plan of the step)
    assert not {"sim-c10", "sim-c22"} & pools["off"][0]
    assert {"sim-c10", "sim-c22"} <= pools["armed"][0]
    h_off, h_on = _heads(off_st[4]), _heads(on_st[4])
    assert (h_off["sim-g1"][1], h_off["sim-g2"][1]) == ("sim-c37", "sim-c23")
    assert (h_on["sim-g1"][:2], h_on["sim-g2"][:2]) == (
        ("deploying", "sim-c10"), ("deploying", "sim-c22"))
    assert (off_st[5]["scoreboard"]["served"],
            on_st[5]["scoreboard"]["served"]) == (4, 5)
