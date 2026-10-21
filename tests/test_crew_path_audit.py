"""SAFETY SEMANTICS 25 — THE AUDIT SEES THE WHOLE DRIVE (2026-10-21; the
Eagle River valley review, crew-safety lens; contracts crew_path_audit;
docs/SIMULATOR_DESIGN.md "The audit sees the whole drive").

The 10c audit judged each crew at its position at the END of each step.
Between two audits a crew drives up to a step of road and stands at stops it
leaves before the audit looks: on the valley harness (33 runs, 12 h) the
review's path probe found crews crossing the eroded-P10 band in 12 episodes
in 8 runs (1,029 samples, the worst 433 min past the 10c line) while the
scoreboard read 0. And the serviced-stop audit read only the stop's own fire.

With the knob on, the audit also judges every crew's driven path since the
last audit — every field cell it passed through at the moment it left the
cell, every spot it stood at at the moment it left — by the tick-end law
(old black honoured), one row per crew per step (transit: true for a crew
only the path audit saw), and each departure against every fire.

These tests pin the knob (default off, armed by the simulator), byte-
identity with it off — and with it on everywhere but the report — on a
world where it bites (goldens computed on commit 7bebc7a, before the rule),
the judgement on painted fields, every fire at the door, and the ARMED
TWINS of the existing tests that pin it off because their law is the tick-
end audit's: each pins exactly what the path audit sees there."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from wgresponse import geo, server, simui, spread
from wgresponse.contracts import LatLon, Raster, ResponseConfig
from wgresponse.sim import ServicedStop
from wgresponse.simui import LOCATIONS, SimSession, _SimFire

from tests.test_crew_egress_judged import _judged_session, _pt
from tests.test_crew_work_judged import _crew_at, _paint, _stop
from tests.test_simui_policy import (FAST_TIGHT, KEY, _arm_mid_deploy,
                                     _checkerboard_dir, _inject_spot_fire,
                                     _session)

INF = math.inf
#: the 2026-10-21 knobs (SAFETY SEMANTICS 25-27) all off: the pre-rule session
#: ... and the iteration-2 knobs (SAFETY SEMANTICS 28-33, 2026-10-21), armed
#: by the simulator since: this file's goldens and derivations predate
#: them too (their own goldens and armed behaviour: tests/test_crew_pocket.
#: py, test_egress_guard.py, test_stage_by_need.py, test_egress_margin.py)
ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False}
ITER1_OFF = {"crew_path_audit": False, "dispatch_keep_frozen": False,
             "plan_route_exact": False, "crew_ingress_judged": False,
             **ITER2_OFF}
#: the path audit alone (the session the pre-rule one is, plus the report)
PATH_ONLY = dict(ITER1_OFF, crew_path_audit=True)


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _audited_step(s, seconds=900.0):
    """One step of crew movement and audit exactly as tick() runs them
    (steps 5 and 6c): each crew's path recorded while it advances, the clock
    moved, the audit judging the step's paths — without dispatch or the
    sweep, so a painted world stays as painted."""
    t0 = s._now_s
    for cs in s._crew_sims.values():
        cs.path_track = [(t0, cs.pos, cs._mode, cs.status.value)]
        cs.advance(t0, t0 + seconds, s._serviced)
        cs.path_track.append((t0 + seconds, cs.pos, cs._mode,
                              cs.status.value))
    s._now_s = t0 + seconds
    s._audit_since_s = t0
    s._audit_paths = True
    try:
        s._run_audit()
    finally:
        s._audit_paths = False
        s._audit_since_s = None
    return s._crew_overruns


def _on_leg(s, target, cid="u-east"):
    """The crew at the centre, already driving its ingress leg to `target`
    (judged when it began; a live leg is not judged again)."""
    cs = _crew_at(s, _pt(0, 0.0), [_stop(cid, target, s)], mode="travel")
    cs._begin_leg("ingress", target)
    return cs


# ---------------------------------------------------------------------------
# the knob
# ---------------------------------------------------------------------------

def test_knob_is_off_by_default_and_the_simulator_arms_it(tmp_path):
    assert ResponseConfig().crew_path_audit is False
    assert _judged_session(tmp_path)._cfg.crew_path_audit is True
    assert _judged_session(
        tmp_path, crew_path_audit=False)._cfg.crew_path_audit is False


# ---------------------------------------------------------------------------
# the judgement on painted fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("armed", [True, False])
def test_a_drive_through_the_band_is_reported(tmp_path, armed):
    """The crew drives 3 km east through a band the 10c line crossed 2 min
    into the step (1.2-1.8 km out) and works its stop beyond it: clear at
    the tick end. Armed: ONE row — transit, imminent, the mode / status it
    drove in, inside the painted disc, while it was there, ~31 min past the
    line, the seconds it spent in the band — counted as a violation. Off:
    the tick-end audit, no row."""
    s = _judged_session(tmp_path, crew_path_audit=armed)
    t0 = s._now_s
    band = _pt(90, 1.5)
    _paint(s, p10_paint=[(band, 300.0, t0 + 120.0)])
    cs = _on_leg(s, _pt(90, 3.0))
    rows = _audited_step(s)
    assert cs._mode == "deploy"                       # at its stop, clear
    assert not any(s._cell_unsafe(cs.pos, s._now_s)[1:])
    if not armed:
        assert rows == []
        return
    (row,) = rows
    assert row["transit"] is True and row["reason"] == "imminent"
    assert (row["mode"], row["status"]) == ("travel", "en_route")
    assert geo.haversine_m(LatLon(row["lat"], row["lon"]), band) <= 300.0
    assert t0 + 120.0 < row["at_s"] < t0 + 3_000.0 / cs.speed
    buf = s._cfg.egress_buffer_min * 60.0
    assert row["margin_min"] == pytest.approx(        # (2 decimals)
        (t0 + 120.0 - row["at_s"] - buf) / 60.0, abs=0.006)
    assert row["margin_min"] < -30.0
    # ~600 m of band at 7.52 m/s, cell by cell
    assert 40.0 < row["exposed_s"] < 140.0
    assert row["eroded_p10_s"] == t0 + 120.0
    assert row["nominal_arrival_s"] is None
    assert len(s._violations) + len(s._crew_overruns) == 1


def test_a_drive_across_the_front_is_burned(tmp_path):
    """The nominal front crosses the road 100 s into the step; the crew
    passes a minute or two later, inside the grass's cold-black window: hot
    black — burned."""
    s = _judged_session(tmp_path)
    t0 = s._now_s
    band = _pt(90, 1.5)
    _paint(s, p10_paint=[(band, 300.0, t0 + 100.0)],
           nom_paint=[(band, 300.0, t0 + 100.0)])
    _on_leg(s, _pt(90, 3.0))
    (row,) = _audited_step(s)
    assert row["transit"] is True and row["reason"] == "burned"
    assert row["nominal_arrival_s"] == t0 + 100.0


@pytest.mark.parametrize("refuge", [True, False])
def test_old_black_on_the_way_is_honoured_as_the_audit_honours_it(
        tmp_path, refuge):
    """Ground the front crossed 2 h before the step is old black (grass:
    5-10 min): with old_black_refuge (the simulator's law) the drive across
    it is no row, exactly as a crew standing there is none; with the strict
    law it is burned."""
    s = _judged_session(tmp_path, old_black_refuge=refuge)
    t0 = s._now_s
    band = _pt(90, 1.5)
    _paint(s, p10_paint=[(band, 300.0, t0 - 7_200.0)],
           nom_paint=[(band, 300.0, t0 - 7_200.0)])
    _on_leg(s, _pt(90, 3.0))
    rows = _audited_step(s)
    if refuge:
        assert rows == []
    else:
        (row,) = rows
        assert row["transit"] is True and row["reason"] == "burned"


def test_a_stop_the_line_reaches_before_the_crew_leaves_is_reported(
        tmp_path):
    """The crew works its stop at the centre until 10 min into the step;
    the 10c line reaches the stop's cell 2 min before that, and the crew is
    2 km west, clear, at the tick end. The path audit reports the stop's
    cell, exposed from the moment the line reached it until the crew drove
    out of it (at least the 2 min it still worked there)."""
    s = _judged_session(tmp_path)
    t0 = s._now_s
    buf = s._cfg.egress_buffer_min * 60.0
    centre = _pt(0, 0.0)
    # (200 m: the stop's own 250 m cell centre always lies inside it)
    _paint(s, p10_paint=[(centre, 200.0, t0 + 480.0 + buf)])
    stop = dict(_stop("u-c", centre, s, deploy_s=600.0),
                actual_arrive_s=t0, actual_service_start_s=t0)
    cs = _crew_at(s, centre, [stop], mode="deploy")
    cs._deploy_end_s = t0 + 600.0
    rows = _audited_step(s)
    assert cs._mode == "egress"
    assert geo.haversine_m(cs.pos, centre) > 1_500.0
    (row,) = rows
    assert row["transit"] is True and row["reason"] == "imminent"
    assert geo.haversine_m(LatLon(row["lat"], row["lon"]), centre) <= 200.0
    assert row["at_s"] >= t0 + 600.0                  # as it left the cell
    assert row["exposed_s"] >= 120.0
    assert row["margin_min"] <= -2.0


def test_one_row_per_crew_per_step(tmp_path):
    """A crew exposed on the way AND at the tick end keeps the tick-end
    audit's row (no transit flag) — still one row. The iteration-2 knobs are
    pinned off (2026-10-21): the SEMANTICS 29 guard withdraws the crew as it
    arrives at a stop inside the band (no way out of it) — this test's law is
    the one-row rule (tests/test_egress_guard.py: the guard's own)."""
    s = _judged_session(tmp_path, **ITER2_OFF)
    t0 = s._now_s
    _paint(s, p10_paint=[(_pt(90, 1.5), 300.0, t0 + 120.0),
                         (_pt(90, 3.0), 300.0, t0 + 120.0)])
    _on_leg(s, _pt(90, 3.0))
    (row,) = _audited_step(s)
    assert "transit" not in row and row["reason"] == "imminent"
    assert row["mode"] == "deploy"


def test_a_direct_audit_call_judges_the_instant(tmp_path):
    """Outside a tick (a slider's refresh, a probe) the audit judges the
    instant, as ever: the step's path is judged only by tick()'s audit."""
    s = _judged_session(tmp_path)
    t0 = s._now_s
    _paint(s, p10_paint=[(_pt(90, 1.5), 300.0, t0 + 120.0)])
    _on_leg(s, _pt(90, 3.0))
    assert len(_audited_step(s)) == 1
    s._run_audit()
    assert s._crew_overruns == []


# ---------------------------------------------------------------------------
# every fire at the door (SEMANTICS 25c)
# ---------------------------------------------------------------------------

def _two_fires(s, home, own_s, other_s):
    """Two painted fires over `home`: F1 (the stop's own) arrives at own_s,
    F2 at other_s (P10 = P50 = P90 = nominal; the rest unreached)."""
    spec = s._tile.spec
    n = spec.nrows * spec.ncols
    fires = {}
    for fid, val in (("F1", own_s), ("F2", other_s)):
        ras = Raster(spec, [INF] * n)
        for r in range(spec.nrows):
            for c in range(spec.ncols):
                q = geo.rc_to_latlon(spec, r, c)
                if geo.haversine_m(home, q) <= 400.0:
                    ras.data[r * spec.ncols + c] = val
        ens = type("Ens", (), {})()
        ens.members = [ras]
        ens.p10 = ens.p50 = ens.p90 = ras
        f = _SimFire(incident_id=fid, origin=_pt(90, 20.0),
                     t_ignite_s=s._now_s, intensity="high",
                     wind_dir_deg=270.0, ens=ens)
        f._eroded_src = ens
        f._eroded_p10 = ras
        fires[fid] = f
    s._fires = fires
    s._fire_order = ["F1", "F2"]


@pytest.mark.parametrize("armed", [True, False])
def test_a_departure_is_judged_against_every_fire(tmp_path, armed):
    """A home coated for its own fire F1 (10 h out) and left now; a second
    fire F2 reaches it in 20 min — inside the stop's 30-min rule. Armed: a
    departure violation, its row naming F2. Off: the stop's own fire only —
    the departure reads 9.5 h clear."""
    s = _judged_session(tmp_path, crew_path_audit=armed)
    t0 = s._now_s
    cl = s._clients[0]
    _two_fires(s, cl.loc, t0 + 10 * 3_600.0, t0 + 1_200.0)
    s._serviced.append(ServicedStop(
        crew_id="sim-g1", client_id=cl.client_id, incident_id="F1",
        arrive_s=t0 - 900.0, service_start_s=t0 - 900.0, depart_s=t0,
        latest_depart_s=t0 + 9 * 3_600.0, margin_h=9.0, gel_fresh_frac=1.0))
    s._run_audit()
    (a,) = s._audit
    row = s.state()["audit"][0]
    if armed:
        assert a.violation and a.eta_p10_s == t0 + 1_200.0
        assert a.margin_h == pytest.approx(-10.0 / 60.0)
        assert row["worst_incident_id"] == "F2"
    else:
        assert not a.violation and a.eta_p10_s == t0 + 10 * 3_600.0
        assert "worst_incident_id" not in row


# ---------------------------------------------------------------------------
# knob OFF = byte-identical; knob ON = the same session plus the report
# (goldens computed on commit 7bebc7a, before SEMANTICS 25, on a world where
# it bites: the Eagle River valley harness's Eagle-Vail south-start extreme
# fire, scratch_review/eagle_valley/run_matrix.py)
# ---------------------------------------------------------------------------

#: sha256(state minus cmd_log)[:16] per tick, computed on 7bebc7a
GOLDEN_EV_SOUTH_EXTREME = [
    "c25cf9271b576cc0", "5bb907c8b4c27596", "2761e42b64f13336",
    "ca2c8ad8517284f9", "dda0e84d9b574db3", "14c51e83c494b97d",
    "27da56fff649c5d4", "b116359b6b6e385f", "846fdb4a2de9e14e",
    "20884cf7869e52d8"]


def _valley_south(cfg, key="eagle_vail_corridor_westerly",
                  intensity="extreme"):
    """The valley harness's south start: the one-click roster (40 homes, 2
    in-zone crews + 1 h reinforcement, the page's defaults), its fire
    replaced by one 5 km south of the centre (the nearest grass / brush
    patch), wind from 200 deg."""
    if not simui.tile_file("eagle_vail_co").exists():
        pytest.skip("LOUD SKIP: eagle_vail_co tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    s.reset()
    p = s.fine_fuel_start(200.0, 5_000.0)
    assert p is not None, "the south start's grass patch moved — re-pin"
    s.ignite(p.lat, p.lon, intensity=intensity, wind_dir_deg=200.0)
    return s


def test_knob_off_is_byte_identical_and_on_changes_only_the_report():
    """Every 2026-10-21 knob off: the pre-rule session, byte for byte.
    The path audit alone on: the SAME session — every tick byte-identical
    but tick 10, where it reports sim-g2's egress 20.9 min past the 10c line
    for 16 s (the review's probe found this episode), the one violation of
    the run; served unchanged."""
    s_off = _valley_south(ITER1_OFF)
    s_on = _valley_south(PATH_ONLY)
    hashes_off, hashes_on = [], []
    for k in range(len(GOLDEN_EV_SOUTH_EXTREME)):
        st_off = s_off.tick()
        st_on = s_on.tick()
        hashes_off.append(_hash(st_off))
        hashes_on.append(_hash(st_on))
        if k < 9:
            assert st_on["crew_overruns"] == []
            assert st_on["scoreboard"]["violations"] == 0
    assert hashes_off == GOLDEN_EV_SOUTH_EXTREME
    assert hashes_on[:9] == GOLDEN_EV_SOUTH_EXTREME[:9]
    assert hashes_on[9] != GOLDEN_EV_SOUTH_EXTREME[9]
    assert st_off["crew_overruns"] == []
    (row,) = st_on["crew_overruns"]
    assert (row["crew_id"], row["reason"], row["mode"], row["transit"]) == (
        "sim-g2", "imminent", "egress", True)
    assert row["margin_min"] == pytest.approx(-20.85, abs=0.05)
    assert row["exposed_s"] == pytest.approx(16.1, abs=0.5)
    assert st_on["scoreboard"]["violations"] == 1
    assert st_on["scoreboard"]["crew_overruns"] == 1
    assert st_on["scoreboard"]["served"] == st_off["scoreboard"]["served"]
    # the report is the only difference: drop the row and the counts, and
    # the two states are the same
    a, b = dict(st_off), dict(st_on)
    for st in (a, b):
        st.pop("cmd_log")
        st["crew_overruns"] = []
        st["scoreboard"] = dict(st["scoreboard"], violations=0,
                                crew_overruns=0)
        st.pop("history", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# the ARMED TWINS of the existing tests that pin the path audit off (their
# law is the tick-end audit's): exactly what the path audit sees there
# ---------------------------------------------------------------------------

def _rows(st):
    return [(o["crew_id"], o["reason"], o["mode"], bool(o.get("transit")))
            for o in st["crew_overruns"]]


def test_withdrawal_egress_under_a_raised_line_is_reported(tmp_path):
    """tests/test_crew_work_judged.py::test_withdrawal_law_holds_under_any_
    angle, armed. The slider raises the buffer to ~13.5 h (30 min above the
    any-angle field's own-incident gap) and withdraws the in-flight stop;
    20b finds no road from the stop on the audit's side of that line and
    the crew takes the least-exposure escape. One row until it is next at
    work: its tick-4 drive, 19.2 min inside the raised line — 13 h before
    the eroded P10 itself, far outside the demo's original 10-min line."""
    s = _session(tmp_path, ign_dist_m=4_600.0, cfg={"spread_any_angle": True})
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    cm = next(c for c in st["crews"] if c["crew_id"] == crew["crew_id"])
    own = spread.eta_at(s._fires[cur["incident_id"]].ens,
                        s._client_by_id[victim].loc,
                        fallback_radius_m=s._cfg.ember_threat_radius_m)
    s.set_buffer((own.eta_p10_s - cm["eta_s"]) / 60.0 + 30.0)
    now = s.state()
    assert any("egress re-routed (SEMANTICS 20b)" in n for n in now["notes"])
    cs = s._crew_sims[crew["crew_id"]]
    seen = []
    while cs._mode != "deploy" and now["tick"] < 60:
        now = s.tick()
        assert all(a["violation"] is False for a in now["audit"])
        if now["crew_overruns"]:
            seen.append((now["tick"], now["crew_overruns"]))
    ((tick, (row,)),) = seen
    assert tick == 4
    assert _rows({"crew_overruns": [row]}) == [
        ("sim-g1", "imminent", "egress", True)]
    assert row["margin_min"] == pytest.approx(-19.2, abs=0.1)
    clear_min = (row["eroded_p10_s"] - row["at_s"]) / 60.0
    assert clear_min > 12 * 60.0          # 13 h before the eroded P10


def test_rugpull_twins_path_audit_reports_what_the_tick_end_audit_missed(
        tmp_path):
    """tests/test_crew_work_judged.py::test_rugpull_twins_armed_control_
    keeps_no_stale_unlawful_work, armed (same served 4 / 1, same withdrawal).
    Control: one row in 60 ticks — at tick 8 the crew leaves sim-c9, its
    departure lawful at that INSTANT (the 23a / 27b workability law tests
    the stop's cell then), and drives out of the stop's own 500 m cell 12 s
    after the 10c line reached it. Tightened: one row — the withdrawal
    egress at tick 4, 89.6 min inside the raised 120-min line (30 min
    before the eroded P10, outside the original 10-min line). No tick-end
    row and no departure violation in either."""
    def run(tighten):
        # the iteration-2 knobs pinned off (2026-10-21): the SEMANTICS 29
        # guard judges sim-c9's cell as the crew leaves it and ends the 12 s
        # (its armed twin: tests/test_egress_guard.py::test_rugpull_control_
        # no_departure_inside_the_line_with_the_guard)
        s = _session(tmp_path, ign_dist_m=4_600.0,
                     cfg=dict({"spread_any_angle": False}, **ITER2_OFF))
        st, crew, cur, _gap = _arm_mid_deploy(s)
        _inject_spot_fire(s, st, cur)
        st = s.tick()
        if tighten:
            s.set_buffer(120.0)
        seen = []
        while st["tick"] < 60:
            st = s.tick()
            assert all(a["violation"] is False for a in st["audit"])
            if st["crew_overruns"]:
                seen.append((st["tick"], st["crew_overruns"]))
        return st, s, seen

    st_a, s_a, seen_a = run(False)
    st_b, _s_b, seen_b = run(True)
    assert (st_a["scoreboard"]["served"], st_b["scoreboard"]["served"]) == (
        4, 1)
    ((tick, (row,)),) = seen_a
    assert tick == 8
    assert _rows({"crew_overruns": [row]}) == [
        ("sim-g1", "imminent", "travel", True)]
    c9 = s_a._client_by_id["sim-c9"].loc
    spec = s_a._tile.spec
    assert geo.latlon_to_rc(spec, LatLon(row["lat"], row["lon"])) == \
        geo.latlon_to_rc(spec, c9)                   # the stop's own cell
    assert row["margin_min"] == pytest.approx(-0.21, abs=0.02)
    ((tick, (row,)),) = seen_b
    assert tick == 4
    assert _rows({"crew_overruns": [row]}) == [
        ("sim-g1", "imminent", "egress", True)]
    assert row["margin_min"] == pytest.approx(-89.55, abs=0.1)
    assert (row["eroded_p10_s"] - row["at_s"]) / 60.0 > 30.0


def test_eta_drift_withdrawal_egress_under_a_raised_line_is_reported(
        tmp_path):
    """tests/test_simui_policy.py::test_eta_drift_withdraws_superseded_stop,
    armed: the same eta_drift withdrawal; its egress (under the 18 h line
    the slider set) is the one row, at the withdrawal tick — 85 min inside
    that line, 16.6 h before the eroded P10."""
    s = _session(tmp_path)
    st, crew, cur, gap_min = _arm_mid_deploy(s, min_tick=8,
                                             gap_max_min=math.inf,
                                             gap_min_min=200.0)
    victim = cur["client_id"]
    s.set_buffer(gap_min - 1.0)
    now = s.state()
    cl = next(c for c in now["clients"] if c["client_id"] == victim)
    brg = geo.bearing_deg(s._fires["sim-f1"].origin,
                          LatLon(cl["lat"], cl["lon"]))
    s.set_wind((brg + 180.0) % 360.0)
    for _ in range(6):
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"])
        if [w for w in st["withdrawn"] if w["client_id"] == victim]:
            break
    (w,) = st["withdrawn"]
    assert w["cause"] == "eta_drift"
    (row,) = st["crew_overruns"]
    assert _rows(st) == [("sim-g1", "imminent", "egress", True)]
    assert row["margin_min"] == pytest.approx(-84.86, abs=0.1)
    assert (row["eroded_p10_s"] - row["at_s"]) / 3_600.0 > 16.0


def test_homes_mode_withdrawal_egress_is_inside_even_the_original_line(
        tmp_path):
    """tests/test_simui_policy.py::test_homes_mode_withdrawal_still_fires
    (SEMANTICS 23 pinned off, as there), armed. The slider raises the buffer
    to ~94 min and withdraws sim-c1; 20b finds no road on the audit's side
    of THAT line and takes the least-exposure escape — which passes 5.0 min
    before the eroded P10: inside the demo's ORIGINAL 10-min line too. The
    one row of the run (tick 4). A finding, not a re-pin: the escape choice
    under a slider-raised buffer is the review's iteration-2 egress work."""
    d = _checkerboard_dir(tmp_path)
    s = SimSession(KEY, 16, 1, seed="m1",
                   cfg_overrides=dict(FAST_TIGHT,
                                      corridor_erosion_tiered=False,
                                      escape_hysteresis_margin_min=0.0,
                                      transit_cold_black_by_class=False,
                                      crew_work_judged=False,
                                      # the iteration-2 knobs (2026-10-21)
                                      # pinned off: the finding is the pre-
                                      # rule escape choice (SEMANTICS 28's
                                      # durable egress picks another target)
                                      **ITER2_OFF),
                   tile_dir=d, roads_module=None)
    loc = LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 4_600.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]
    _inject_spot_fire(s, st, cur)
    st2 = s.tick()
    crew_mid = next(c for c in st2["crews"] if c["crew_id"] == crew["crew_id"])
    own = spread.eta_at(s._fires[cur["incident_id"]].ens,
                        s._client_by_id[victim].loc).eta_p10_s
    s.set_buffer((own - crew_mid["eta_s"]) / 60.0 + 30.0)
    now = s.state()
    (w,) = now["withdrawn"]
    assert w["cause"] == "policy_change"
    seen = []
    for _ in range(60 - now["tick"]):
        st = s.tick()
        assert all(a["violation"] is False for a in st["audit"])
        if st["crew_overruns"]:
            seen.append((st["tick"], st["crew_overruns"]))
    ((tick, (row,)),) = seen
    assert tick == 4
    assert _rows({"crew_overruns": [row]}) == [
        ("sim-g1", "imminent", "egress", True)]
    clear_min = (row["eroded_p10_s"] - row["at_s"]) / 60.0
    assert clear_min == pytest.approx(5.0, abs=0.1)
    assert clear_min < FAST_TIGHT["egress_buffer_min"]


def test_every_fire_at_the_door_counts_the_pinned_controls_late_departure(
        tmp_path):
    """tests/test_simui_policy.py::test_live_tightening_reduces_served_vs_
    control's control twin (SEMANTICS 23 and 27b pinned off, as there),
    armed: it leaves sim-c15 4.8 min AFTER the planted fire's P10 reached
    the home — 14.8 min past its 10-min rule. The stop's own-fire audit
    counted nothing; every fire at the door counts it, on every tick from
    that departure, naming the planted fire. The rest of the control is
    unchanged (served 9)."""
    # the iteration-2 knobs pinned off as well (2026-10-21): the control is
    # the pre-rule twin (the SEMANTICS 29 guard changes which stops it works)
    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg=dict({"crew_work_judged": False,
                           "crew_ingress_judged": False}, **ITER2_OFF))
    st, crew, cur, _gap = _arm_mid_deploy(s)
    spot = _inject_spot_fire(s, st, cur)
    st = s.tick()
    first = None
    while st["tick"] < 60:
        st = s.tick()
        bad = [a for a in st["audit"] if a["violation"]]
        if bad and first is None:
            first = st["tick"]
        for a in bad:
            assert a["client_id"] == "sim-c15"
            assert a["worst_incident_id"] == spot
            assert a["margin_h"] * 60.0 == pytest.approx(-14.8, abs=0.1)
    assert first is not None
    assert st["scoreboard"]["served"] == 9
    c15 = next(x for x in st["serviced"] if x["client_id"] == "sim-c15")
    p10 = spread.eta_at(s._fires[spot].ens, s._client_by_id["sim-c15"].loc,
                        fallback_radius_m=s._cfg.ember_threat_radius_m
                        ).eta_p10_s
    assert (c15["depart_s"] - p10) / 60.0 == pytest.approx(4.8, abs=0.1)


def test_burned_out_map_path_audit_sees_the_old_black_halo(tmp_path):
    """tests/test_sim_coarse_substep.py::test_burned_out_map_crews_only_on_
    old_black, armed (same three relocations, no departure violation). In
    900 internal steps one is reported: at step 315 both crews egress across
    one grass cell the nominal front has not reached, whose eroded P10 is 19
    h in the past — the band the burned-out ground beside it projects (old
    black is a refuge per cell; its halo is not) — for 20 s each."""
    from tests.test_sim_coarse_substep import _session as coarse_session
    s = coarse_session(tmp_path, n_clients=8, n_crews=2)
    assert s._cfg.crew_path_audit
    st = s.tick()
    assert st["scoreboard"]["violations"] == 0
    s.set_speed(600.0)
    orig = s._tick_step
    seen = {"steps": 0, "rows": []}

    def checked(step, *, emit=True, count=True):
        out = orig(step, emit=emit, count=count)
        seen["steps"] += 1
        if s._crew_overruns:
            seen["rows"].append((seen["steps"], list(s._crew_overruns),
                                 s._now_s))
        return out

    s._tick_step = checked
    for _ in range(6):
        st = s.tick()
        for a in st["audit"]:
            assert a["violation"] is False, a
    assert seen["steps"] == 6 * 150
    assert st["scoreboard"]["relocations"] <= 5
    ((step, rows, now),) = seen["rows"]
    assert step == 315
    assert [(o["crew_id"], o["reason"], o["mode"], o["transit"])
            for o in rows] == [("sim-g1", "imminent", "egress", True),
                               ("sim-g2", "imminent", "egress", True)]
    for o in rows:
        assert o["nominal_arrival_s"] > o["at_s"]          # unburned
        assert (now - o["eroded_p10_s"]) / 3_600.0 > 18.0  # 19 h past
        assert o["exposed_s"] == pytest.approx(19.7, abs=0.5)


def test_the_drive_out_of_a_fire_lit_on_a_crew_is_reported(tmp_path):
    """tests/test_sim_crew_placement.py::test_crew_placed_where_the_fire_
    starts_is_reported_never_silent, armed (strict law: old_black_refuge
    off, as there). Tick 1 reports the crew in the fire (the tick-end row);
    tick 2 reports its drive out through the burning ground (transit,
    burned) — clear at that audit, never silent on the way out."""
    from tests.test_sim_crew_placement import (_exposed, _on_ew_road,
                                               _session as place_session)
    s = place_session(tmp_path, seed="worst",
                      cfg={"old_black_refuge": False})
    at_fire = _on_ew_road(8_000.0)
    s.place_crew("sim-g1", at_fire.lat, at_fire.lon)
    s.ignite(at_fire.lat, at_fire.lon, intensity="high")
    st = s.tick()
    assert any(_exposed(s, "sim-g1"))
    assert _rows(st) == [("sim-g1", "burned", "egress", False)]
    st = s.tick()
    assert not any(_exposed(s, "sim-g1"))
    (row,) = st["crew_overruns"]
    assert _rows(st) == [("sim-g1", "burned", "egress", True)]
    assert row["exposed_s"] > 0.0
    for _ in range(4):
        st = s.tick()
        assert st["crew_overruns"] == []
        assert all(a["violation"] is False for a in st["audit"])


def test_extended_campfire_scale_egress_transits_are_reported():
    """tests/test_simui.py::test_sem10d_extended_campfire_scale_no_overrun_
    ever, armed: through 64 ticks (32 h) the tick-end audit and the
    departure audit stay clean, and at tick 65 the path audit reports
    sim-g1's egress through the band — 8 h past the 10c line for 13.6 min
    of the step — the first of the four such drives the 120-tick run makes
    (ticks 65, 82, 88, 89). The yardstick for the review's iteration-2
    egress laws."""
    from wgresponse import geo as _geo
    key = "paradise_ca"
    if not simui.tile_file(key, fire_zone="extended").exists():
        pytest.skip("LOUD SKIP: paradise_ca extended tile not cached")
    loc = simui.LOCATIONS[key]
    # the iteration-2 knobs pinned off (2026-10-21): this is iteration 1's
    # yardstick for them — their armed twin, through all four drives:
    # tests/test_crew_pocket.py::test_extended_campfire_scale_armed_twin
    s = simui.SimSession(
        key, 20, 2, seed="alerted|paradise_ca",
        cfg_overrides=dict({"spotting_enabled": True,
                            "grass_ros_floor_frac": 0.10,
                            "deploy_min": 15.0}, **ITER2_OFF),
        fire_zone="extended")
    ign = _geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    for t in range(64):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, (t, st["crew_overruns"])
    st = s.tick()
    (row,) = st["crew_overruns"]
    assert _rows(st) == [("sim-g1", "imminent", "egress", True)]
    assert row["margin_min"] == pytest.approx(-495.88, abs=0.5)
    assert row["exposed_s"] == pytest.approx(817.6, abs=5.0)
    assert all(a["violation"] is False for a in st["audit"])
