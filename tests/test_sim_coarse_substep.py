"""COARSE JUMPS ARE SUB-STEPPED (founder 2026-09-26, "sub-step big jumps";
simui.SIM_MAX_SUBSTEP_S): a tick longer than one simulated hour runs as equal
internal steps of at most an hour — the full engine cycle each, so the
crew-safety sweep and the audit act at least hourly — surfaced to the caller
as ONE tick, with the crew-overrun rows reported as their union over the
jump. Page ticks (15 min) take the single-step path unchanged."""
from __future__ import annotations

import math

from tests.test_simui_policy import _session
from wgresponse import simui


def _count_steps(s):
    steps = []
    orig = s._tick_step

    def wrapped(step, *, emit=True, count=True):
        steps.append((step, emit, count))
        return orig(step, emit=emit, count=count)

    s._tick_step = wrapped
    return steps


def test_step_sizes_and_single_tick_surface(tmp_path):
    s = _session(tmp_path, n_clients=6, n_crews=2)
    period = s._cfg.replan_period_s
    steps = _count_steps(s)
    for mult, n_expected in ((1.0, 1), (4.0, 1), (8.0, 2), (600.0, 150)):
        s.set_speed(mult)
        steps.clear()
        t0, n0, h0 = s._now_s, s._tick_n, len(s._history)
        st = s.tick()
        assert len(steps) == n_expected, (mult, len(steps))
        assert all(step <= simui.SIM_MAX_SUBSTEP_S + 1e-6 for step, _e, _c in steps)
        assert math.isclose(sum(step for step, _e, _c in steps), period * mult)
        assert math.isclose(s._now_s - t0, period * mult)          # full jump
        assert s._tick_n == n0 + 1                                  # ONE tick
        assert len(s._history) == min(h0 + 1, s._history.maxlen or h0 + 1)
        assert st["sim_now_s"] == s._now_s
        if n_expected == 1:
            assert steps == [(period * mult, True, True)]          # legacy path
        else:
            assert [e for _s, e, _c in steps] == [False] * n_expected
            assert [c for _s, _e, c in steps] == [True] + [False] * (n_expected - 1)


def test_coarse_jump_equals_the_same_fine_steps(tmp_path):
    """One 2-hour tick (two internal 1-hour steps) lands in exactly the same
    world as two 1-hour ticks: fire, crews, served, audit."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _session(tmp_path / "a", n_clients=8, n_crews=2)
    b = _session(tmp_path / "b", n_clients=8, n_crews=2)
    a.tick()
    b.tick()
    a.set_speed(8.0)
    b.set_speed(4.0)
    for _ in range(3):
        sa = a.tick()
        b.tick()
        sb = b.tick()
    assert a._now_s == b._now_s
    assert sa["terrain"]["burned"] == sb["terrain"]["burned"]
    pa = {c["crew_id"]: (c["lat"], c["lon"], c["status"]) for c in sa["crews"]}
    pb = {c["crew_id"]: (c["lat"], c["lon"], c["status"]) for c in sb["crews"]}
    assert pa == pb
    assert sa["scoreboard"]["served"] == sb["scoreboard"]["served"]
    assert sa["scoreboard"]["violations"] == sb["scoreboard"]["violations"]


def test_overrun_rows_are_the_union_over_the_jump(tmp_path):
    """Nothing an internal step saw is hidden: rows raised at ANY internal
    step are reported on the returned tick (deduplicated by crew / reason /
    mode) and counted in the scoreboard's violations."""
    s = _session(tmp_path, n_clients=4, n_crews=2)
    s.tick()
    real_audit = s._run_audit
    calls = {"n": 0}

    def audit_with_injected_rows():
        real_audit()
        calls["n"] += 1
        if calls["n"] == 2:     # only the SECOND internal step sees these
            s._crew_overruns = list(s._crew_overruns) + [
                {"crew_id": "sim-g1", "kind": "crew_overrun", "reason": "imminent",
                 "status": "egressing", "mode": "egress", "lat": 0.0, "lon": 0.0,
                 "nominal_arrival_s": None, "eroded_p10_s": None}]
        if calls["n"] == 3:
            s._crew_overruns = list(s._crew_overruns) + [
                {"crew_id": "sim-g2", "kind": "crew_overrun", "reason": "imminent",
                 "status": "egressing", "mode": "egress", "lat": 0.0, "lon": 0.0,
                 "nominal_arrival_s": None, "eroded_p10_s": None}]

    s._run_audit = audit_with_injected_rows
    s.set_speed(16.0)                       # 4 h -> 4 internal steps
    st = s.tick()
    ids = {(o["crew_id"], o["reason"]) for o in st["crew_overruns"]}
    assert ("sim-g1", "imminent") in ids and ("sim-g2", "imminent") in ids
    assert st["scoreboard"]["violations"] >= 2


def test_burned_out_map_crews_only_on_old_black(tmp_path):
    """The burned-out stretch of the 600x coarse-jump scenario (founder
    2026-09-26, "split the test"): 900 h of 150 h jumps burn out the whole
    synthetic tile, safe zone included. Checked at EVERY internal step: no
    stop ever departs past its rule, every crew-overrun row is reported in
    the scoreboard, and a crew on burned ground is only ever on OLD black —
    burned longer ago than its fuel's cold-black window, never a live front.
    (The strict audit still counts that ground as "burned"; whether old
    black should count is a separate safety-law decision.)"""
    from wgresponse.contracts import LatLon
    s = _session(tmp_path, n_clients=8, n_crews=2)
    st = s.tick()
    assert st["scoreboard"]["violations"] == 0
    s.set_speed(600.0)
    orig = s._tick_step
    seen = {"burned": 0, "steps": 0}

    def checked(step, *, emit=True, count=True):
        out = orig(step, emit=emit, count=count)
        seen["steps"] += 1
        now = s._now_s
        for o in s._crew_overruns:
            if o["reason"] == "burned":
                seen["burned"] += 1
                age_s = now - o["nominal_arrival_s"]
                window_s = s._cold_black_pass_s(LatLon(o["lat"], o["lon"]))
                assert age_s > window_s, (o, age_s, window_s)
            else:
                assert o["reason"] == "imminent" and o["mode"] != "idle", o
        return out

    s._tick_step = checked
    for _ in range(6):
        st = s.tick()
        for a in st["audit"]:
            assert a["violation"] is False, a
        assert st["scoreboard"]["violations"] == len(st["crew_overruns"])
    assert seen["steps"] == 6 * 150
    assert seen["burned"] > 0, "the tile no longer burns out — rebuild the pin"
    assert st["sim_now_s"] - st["t0_s"] >= 6 * 600.0 * 900.0
