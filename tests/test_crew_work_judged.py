"""SAFETY SEMANTICS 23 — A WORLD CHANGE RE-JUDGES COMMITTED WORK (2026-10-14;
contracts crew_work_judged; docs/SIMULATOR_DESIGN.md "A world change
re-judges committed work").

The integrator's finding (2026-10-14): with spread_any_angle armed, the
scripted rug-pull of tests/test_simui_policy.py recorded a SEM-10c crew
overrun the 16-direction engine did not. Diagnosis: the crew was NOT
cornered and the SEM-10d area escape that fired next was the least-exposure
way out. Twenty-six minutes after a second fire was planted 1.5 km upwind,
the crew finished its stop and drove its QUEUED ingress leg — planned before
that fire existed — 494 m past the new fire's ignition point, through its
eroded band; from the stop every alternative was lawful (two other queued
stops, all three zones). The 16-direction engine has the same gap on another
branch: at nearby geometries of the same choreography crews kept DEPLOYING
where the new band arrived before the deploy ended. Dispatch judges a crew's
ingress legs and stops once, against the fields of the tick that planned
them, and nothing re-read them after a world change.

These tests pin the knob (default off, armed by the simulator), byte-
identity with it off on worlds where it bites (goldens computed on commit
3dd341a, before the rule), the finding and its 16-direction twin with it on,
the leg-start and before-moving judgements on painted fields, the never-
silent report of a crew the world change lands on, and the armed twins of
the two rug-pull choreographies that pin it off."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from wgresponse import geo, simui, spread
from wgresponse.contracts import LatLon, Raster, ResponseConfig, UNREACHED
from wgresponse.simui import LOCATIONS, SimSession, _SimFire

from tests.test_crew_egress_judged import _judged_session, _pt
from tests.test_simui_policy import (FAST_TIGHT, KEY, _arm_mid_deploy,
                                     _checkerboard_dir, _inject_spot_fire,
                                     _session)

INF = math.inf
ANY = {"spread_any_angle": True}
OFF = {"crew_work_judged": False}


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# the knob
# ---------------------------------------------------------------------------

def test_knob_is_off_by_default_and_the_simulator_arms_it(tmp_path):
    assert ResponseConfig().crew_work_judged is False
    assert _session(tmp_path)._cfg.crew_work_judged is True
    assert _session(tmp_path, cfg=OFF)._cfg.crew_work_judged is False


# ---------------------------------------------------------------------------
# knob OFF = byte-identical (goldens computed on commit 3dd341a, before
# SEMANTICS 23, on the two worlds below, where it bites)
# ---------------------------------------------------------------------------

#: (any-angle, spot-fire distance m, which deploy arms it, ticks after the
#: injection) -> (sha256(state minus cmd_log)[:16] per tick, crew-overrun
#: rows per tick), computed on 3dd341a
GOLDENS = {
    "any_angle_rugpull": (
        True, 1250.0, 1, 6,
        ["e31e601e02643ccb", "e1ece546f163cc47", "8578b8457f3407ce",
         "ca60ed31e783e755", "76477f740dc0580a", "7ac1835f5d0b2d80",
         "ceff8c11546c02b6", "c62a660436de0cd2"],
        [0, 0, 0, 1, 0, 0, 0, 0]),
    "sixteen_direction_second_deploy": (
        False, 1500.0, 2, 6,
        ["0dee44013e96bd85", "45e37930642c8934", "a70fcd9b821f85b0",
         "c30c1d5dccedfe2a", "8b461e29cd299eea", "4a6ac378d8a8a133",
         "3c3a7d18257ecfbc", "6d99f63d6752fb5d", "79aaa63d9a51996a",
         "ac46929d48c581d7"],
        [0, 0, 0, 0, 1, 0, 1, 1, 1, 0]),
}


def _golden_world(tmp_path, any_angle, spot_d, nth, ticks, cfg):
    """The rug-pull world of tests/test_simui_policy.py (value objective,
    FAST_TIGHT, 16 clients / 1 crew, high fire 4.6 km upwind, straight legs):
    tick until the crew deploys at its nth distinct client, plant the phase-2
    spot fire spot_d upwind of it, tick `ticks` more. Per-tick hashes and
    crew-overrun row counts, and the session."""
    # (the spread law pinned either way: the goldens are geometry)
    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg=dict(cfg, spread_any_angle=any_angle))
    hashes, rows, seen = [], [], []
    while len(seen) < nth:
        st = s.tick()
        hashes.append(_hash(st))
        rows.append(len(st["crew_overruns"]))
        c = st["crews"][0]
        if c["status"] == "deploying" and c["itinerary"]:
            if c["itinerary"][0]["client_id"] not in seen:
                seen.append(c["itinerary"][0]["client_id"])
    _inject_spot_fire(s, st, st["crews"][0]["itinerary"][0], dist_m=spot_d)
    for _ in range(ticks):
        st = s.tick()
        hashes.append(_hash(st))
        rows.append(len(st["crew_overruns"]))
    return hashes, rows, s


@pytest.mark.parametrize("world", sorted(GOLDENS))
def test_knob_off_is_byte_identical_and_still_overruns(tmp_path, world):
    """Knob off: both worlds replay the pre-rule sessions byte for byte,
    their overrun rows included (so the armed arm below bites)."""
    any_angle, spot_d, nth, ticks, golden, golden_rows = GOLDENS[world]
    hashes, rows, _s = _golden_world(tmp_path, any_angle, spot_d, nth,
                                     ticks, OFF)
    assert hashes == golden
    assert rows == golden_rows


@pytest.mark.parametrize("world", sorted(GOLDENS))
def test_knob_on_the_same_worlds_have_no_overrun(tmp_path, world):
    """Armed (the simulator default): no crew-overrun row on any tick, and
    the sessions agree with the pre-rule ones up to the world change."""
    any_angle, spot_d, nth, ticks, golden, _rows = GOLDENS[world]
    hashes, rows, s = _golden_world(tmp_path, any_angle, spot_d, nth, ticks,
                                    {})
    assert s._cfg.crew_work_judged
    assert rows == [0] * len(rows)
    first = len(golden) - ticks              # the first tick after the change
    assert hashes[:first] == golden[:first]


# ---------------------------------------------------------------------------
# the finding: any-angle rug-pull (the queued ingress leg)
# ---------------------------------------------------------------------------

def test_any_angle_rugpull_queued_ingress_leg_is_judged(tmp_path):
    """The integrator's scenario under the simulator's law. At tick 3 the
    crew still deploys at sim-c4 (the spot fire's field just took effect);
    from that stop, at its deploy end, the queued leg to sim-c16 runs
    through the planted fire's band while the next queued stop and every
    zone are lawful — the crew was not cornered. Armed, it releases sim-c16
    the moment it would start that leg and drives on to sim-c11; no
    crew-overrun row and no departure violation through tick 60."""
    s = _session(tmp_path, ign_dist_m=4_600.0, cfg=ANY)
    st, crew, cur, _gap = _arm_mid_deploy(s)
    assert (st["tick"], cur["client_id"]) == (2, "sim-c4")
    spot = _inject_spot_fire(s, st, cur)
    st = s.tick()
    cs = s._crew_sims[crew["crew_id"]]
    assert cs._mode == "deploy"
    assert [x["client_id"] for x in cs.itinerary[:3]] == [
        "sim-c4", "sim-c16", "sim-c11"]
    t_dep = cs._deploy_end_s
    c16, c11 = cs.itinerary[1], cs.itinerary[2]
    why = s._ingress_unworkable(cs, c16, t_dep)
    assert why is not None and "band" in why
    assert s._ingress_unworkable(cs, c11, t_dep) is None
    lead = s._judge_lead_s()
    for z in s._loc.safe_zones:
        b, _e, worst, _t, dm = s._metrics_of_points(
            s._sem10_points([cs.pos, z.loc], cs.speed), t_dep, 0.0)
        assert not b and worst >= 0.0 and dm >= lead, z.zone_id
    # the leg passes within the planted fire's 550 m band of its origin
    origin = s._fires[spot].origin
    closest = min(geo.haversine_m(q, origin)
                  for q, _dt in s._sem10_points([cs.pos, c16["loc"]],
                                                cs.speed))
    assert closest < 550.0
    st = s.tick()                                        # tick 4
    assert st["crew_overruns"] == []
    assert any("released sim-c16 (SEMANTICS 23)" in n for n in st["notes"])
    assert [x["client_id"] for x in cs.itinerary][:1] == ["sim-c11"]
    while st["tick"] < 60:
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, (st["crew_overruns"],
                                                     st["audit"])
    assert st["scoreboard"]["withdrawn"] == 0


def test_any_angle_rugpull_knob_off_overruns_on_the_ingress_leg(tmp_path):
    """The finding reproduced (knob off): at tick 4 the crew stands on
    unburned fuel inside the planted fire's eroded band — eroded P10 = that
    fire's ignition time (T0 + 30 min) — after driving its queued leg."""
    s = _session(tmp_path, ign_dist_m=4_600.0, cfg=dict(ANY, **OFF))
    st, crew, cur, _gap = _arm_mid_deploy(s)
    spot = _inject_spot_fire(s, st, cur)
    s.tick()
    st = s.tick()
    (row,) = st["crew_overruns"]
    assert row["reason"] == "imminent" and row["nominal_arrival_s"] is None
    assert row["eroded_p10_s"] == s._fires[spot].t_ignite_s
    assert st["scoreboard"]["violations"] == 1


def test_withdrawal_law_holds_under_any_angle(tmp_path):
    """The slider withdrawal under any-angle and the simulator's law, self-
    calibrated like the homes-mode demo (the any-angle field leaves a
    ~13 h own-incident gap at tick 3, far outside the 120-min slider): a
    buffer 30 min above the gap withdraws the in-flight stop with cause
    policy_change and a 30-min deficit, the crew egresses on a judged leg
    with no crew-overrun row until it is next at work, and the victim is
    never served. (Later fresh plans under a ~13 h buffer meet dispatch's
    corridor margin, not the buffer — a pre-existing dispatch / audit split
    the contracts entry names; not this law's.)"""
    s = _session(tmp_path, ign_dist_m=4_600.0, cfg=ANY)
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    assert st["scoreboard"]["violations"] == 0
    cm = next(c for c in st["crews"] if c["crew_id"] == crew["crew_id"])
    assert cm["status"] == "deploying" and cm["itinerary"][0][
        "client_id"] == victim
    own = spread.eta_at(s._fires[cur["incident_id"]].ens,
                        s._client_by_id[victim].loc,
                        fallback_radius_m=s._cfg.ember_threat_radius_m)
    gap_min = (own.eta_p10_s - cm["eta_s"]) / 60.0
    assert gap_min > 120.0
    s.set_buffer(gap_min + 30.0)
    now = s.state()
    (w,) = now["withdrawn"]
    assert (w["client_id"], w["cause"]) == (victim, "policy_change")
    assert w["deficit_min"] == pytest.approx(30.0, abs=2.0)
    cs = s._crew_sims[crew["crew_id"]]
    assert cs._mode == "egress"
    while cs._mode != "deploy" and now["tick"] < 60:
        now = s.tick()
        assert now["scoreboard"]["violations"] == 0, now["crew_overruns"]
    while now["tick"] < 60:
        now = s.tick()
        assert victim not in {x["client_id"] for x in now["serviced"]}


# ---------------------------------------------------------------------------
# the 16-direction twin: the deploy branch
# ---------------------------------------------------------------------------

def test_sixteen_direction_deploy_outrun_by_the_world_change_withdraws(
        tmp_path):
    """The same choreography at the second deploy, spot fire 1.5 km (16
    directions): knob off, the crew keeps deploying at sim-c15 while the new
    band arrives (rows at ticks 5, 7, 8, 9 — GOLDENS). Armed, before it
    moves in the step the spot fire's field takes effect, it withdraws
    (cause work_judged: it would leave 8.5 min past its latest lawful
    departure, every fire and its own cell counted)."""
    _h, rows, s = _golden_world(tmp_path, False, 1500.0, 2, 6, {})
    assert rows == [0] * len(rows)
    (w,) = s.state()["withdrawn"]
    assert (w["client_id"], w["cause"]) == ("sim-c15", "work_judged")
    assert w["deficit_min"] == pytest.approx(8.5, abs=0.5)
    assert w["at_s"] == s.state()["t0_s"] + 4 * 900.0      # tick 5's start
    assert any("SAFETY WITHDRAWAL (work_judged)" in n
               for n in s.state()["notes"])


# ---------------------------------------------------------------------------
# the armed twins of the two pinned rug-pull choreographies
# ---------------------------------------------------------------------------

def test_rugpull_twins_armed_control_keeps_no_stale_unlawful_work(tmp_path):
    """tests/test_simui_policy.py::test_live_tightening_reduces_served_vs_
    control under the simulator's law: every law assertion holds (the
    tightened twin withdraws and serves strictly fewer; violations 0 on
    every tick of both). The control twin serves 4, not 9: it releases
    sim-c15 (it would leave 14.8 min past its rule against the planted
    fire) and, at sim-c1, the queued legs to four homes the wind swing no
    longer threatens (each crosses the planted fire's band), then leaves.
    The 16-direction spread is pinned (the choreography's geometry)."""
    def run(tighten, cfg=None):
        s = _session(tmp_path, ign_dist_m=4_600.0,
                     cfg=dict(cfg or {}, spread_any_angle=False))
        st, crew, cur, _gap = _arm_mid_deploy(s)
        _inject_spot_fire(s, st, cur)
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["audit"]
        if tighten:
            s.set_buffer(120.0)
        while st["tick"] < 60:
            st = s.tick()
            assert st["scoreboard"]["violations"] == 0, (st["audit"],
                                                         st["crew_overruns"])
        return st, s

    st_a, s_a = run(False)
    st_b, _s_b = run(True)
    assert st_a["scoreboard"]["withdrawn"] == 0
    assert st_b["scoreboard"]["withdrawn"] >= 1
    assert st_b["scoreboard"]["served"] < st_a["scoreboard"]["served"]
    # derivation run 2026-10-14 (loud re-pin markers, not law)
    assert [x["client_id"] for x in st_a["serviced"]] == [
        "sim-c12", "sim-c9", "sim-c1", "sim-c16"]
    assert st_b["scoreboard"]["served"] == 1
    released = [n for n in st_a["notes"] if "(SEMANTICS 23)" in n]
    assert any("released sim-c15" in n and "worked and left lawfully" in n
               for n in released)
    eta = {c["client_id"]: c["eta_p10_s"] for c in st_a["clients"]}
    for c in ("sim-c14", "sim-c11", "sim-c4", "sim-c10"):
        assert any(f"released {c}" in n and "band" in n for n in released), c
        assert eta[c] is None            # no longer threatened at all
    # what the pinned (knob-off) control did instead: sim-c15 served past
    # its rule against the planted fire (the departure audit reads only
    # the stop's own fire, so it counted nothing)
    st_o, s_o = run(False, cfg=OFF)
    c15 = next(x for x in st_o["serviced"] if x["client_id"] == "sim-c15")
    rollup = min(spread.eta_at(f.ens, s_o._client_by_id["sim-c15"].loc,
                               fallback_radius_m=s_o._cfg.ember_threat_radius_m
                               ).eta_p10_s for f in s_o._fires.values())
    assert c15["depart_s"] > rollup                  # left after the P10
    assert st_o["scoreboard"]["served"] == 9


def test_homes_mode_rugpull_armed_no_row(tmp_path):
    """tests/test_simui_policy.py::test_homes_mode_withdrawal_still_fires
    under the simulator's law: the victim's cell was clear when the spot
    fire's field took effect (the 10c row the pinned test tolerates came 12
    min later), so the crew withdraws before it moves (cause work_judged) —
    no crew-overrun row at tick 3 or after, the victim never served. The
    16-direction spread is pinned (the choreography's geometry)."""
    d = _checkerboard_dir(tmp_path)
    s = SimSession(KEY, 16, 1, seed="m1",
                   cfg_overrides=dict(FAST_TIGHT,
                                      corridor_erosion_tiered=False,
                                      escape_hysteresis_margin_min=0.0,
                                      transit_cold_black_by_class=False,
                                      spread_any_angle=False),
                   tile_dir=d, roads_module=None)
    loc = LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 4_600.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    st, crew, cur, _gap = _arm_mid_deploy(s)
    victim = cur["client_id"]
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    assert st["crew_overruns"] == []
    (w,) = st["withdrawn"]
    assert (w["client_id"], w["cause"]) == (victim, "work_judged")
    assert w["at_s"] == st["sim_now_s"] - 900.0           # before it moved
    while st["tick"] < 60:
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]
        assert victim not in {x["client_id"] for x in st["serviced"]}


# ---------------------------------------------------------------------------
# the judgements on painted fields
# ---------------------------------------------------------------------------

def _paint(s, p10_paint=(), nom_paint=()):
    """One fire whose nominal / P10 / eroded-P10 fields are painted (paint =
    [(centre, radius m, unix s)], the rest UNREACHED); p50 = p90 = p10 so
    the client ETA reads the same field the corridor judges."""
    spec = s._tile.spec
    n = spec.nrows * spec.ncols
    nom = Raster(spec, [INF] * n)
    p10 = Raster(spec, [INF] * n)
    for ras, paints in ((nom, nom_paint), (p10, p10_paint)):
        for centre, radius, val in paints:
            for r in range(spec.nrows):
                for c in range(spec.ncols):
                    if geo.haversine_m(centre, geo.rc_to_latlon(spec, r, c)) \
                            <= radius:
                        i = r * spec.ncols + c
                        ras.data[i] = min(ras.data[i], val)
    ens = type("Ens", (), {})()
    ens.members = [nom]
    ens.p10 = ens.p50 = ens.p90 = p10
    f = _SimFire(incident_id="F1", origin=_pt(90, 20.0), t_ignite_s=s._now_s,
                 intensity="high", wind_dir_deg=270.0, ens=ens)
    f._eroded_src = ens
    f._eroded_p10 = p10
    s._fires = {"F1": f}
    s._fire_order = ["F1"]
    return f


def _stop(cid, loc, s, service_in_s=0.0, deploy_s=900.0, gen=None):
    st = {"client_id": cid, "incident_id": "F1", "loc": loc,
          "service_start_s": s._now_s + service_in_s, "deploy_s": deploy_s,
          "latest_depart_s": s._now_s + 86_400.0, "gel_fresh_frac": 1.0,
          "eta_p50_s": s._now_s + 86_400.0, "planned_arrive_s": s._now_s,
          "planned_depart_s": s._now_s, "planned_margin_h": 24.0}
    if gen is not None:
        st["fields_gen"] = gen
    return st


def _crew_at(s, loc, stops, mode="idle", zone_id="zB"):
    cs = next(iter(s._crew_sims.values()))
    z = s._zone_by_id[zone_id]
    cs.pos = loc
    cs.itinerary = list(stops)
    cs.egress_loc, cs.egress_zone_id = z.loc, z.zone_id
    cs._mode = mode
    cs.status = {"idle": simui.CrewStatus.AVAILABLE,
                 "travel": simui.CrewStatus.EN_ROUTE,
                 "deploy": simui.CrewStatus.DEPLOYING}[mode]
    cs._leg = None
    return cs


@pytest.mark.parametrize("arm", ["stale", "current", "off"])
def test_an_ingress_leg_is_judged_when_it_starts(tmp_path, arm):
    """A crew at rest at the centre sets out for its queued stop 3 km east;
    the planted band lies across that road (already past its 10c line). A
    stop planned against an older field generation is judged as the leg
    starts: released (loud note), and the next queued stop (3 km west, road
    clear) is judged and kept. A stop stamped with the CURRENT generation is
    dispatch's — never re-judged; knob off — legacy, unjudged."""
    s = _judged_session(tmp_path, **({"crew_work_judged": False}
                                     if arm == "off" else {}))
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s - 60.0)])
    s._field_gen = 2
    gen = 1 if arm == "stale" else 2
    east = _stop("u-east", _pt(90, 3.0), s, gen=gen)
    west = _stop("u-west", _pt(270, 3.0), s, gen=gen)
    cs = _crew_at(s, _pt(0, 0.0), [east, west])
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert cs._mode == "travel"
    if arm == "stale":
        assert [x["client_id"] for x in cs.itinerary] == ["u-west"]
        assert cs.itinerary[0]["fields_gen"] == 2          # judged, kept
        assert any("released u-east (SEMANTICS 23)" in n and "band" in n
                   for n in s._notes)
        assert cs._leg["target"] == west["loc"]
    else:
        assert [x["client_id"] for x in cs.itinerary] == ["u-east", "u-west"]
        assert cs._leg["target"] == east["loc"]
        assert not any("SEMANTICS 23" in n for n in s._notes)


def test_a_stop_whose_cell_the_band_reaches_before_departure_is_released(
        tmp_path):
    """The road is clear and the stop's cell clear at arrival, but its
    eroded P10 comes within egress_buffer_min of the crew's departure
    (arrival + the 15-min deploy): the stop cannot be worked and left
    lawfully — released; with nothing left the crew leaves from here, its
    egress judged (20b)."""
    s = _judged_session(tmp_path)
    buf = s._cfg.egress_buffer_min * 60.0
    dest = _pt(0, 2.0)
    arrive = s._now_s + geo.haversine_m(_pt(0, 0.0), dest) / 7.52
    # (200 m: the stop's own 250 m cell centre always lies inside it)
    _paint(s, p10_paint=[(dest, 200.0, arrive + buf + 300.0)])
    s._field_gen = 2
    cs = _crew_at(s, _pt(0, 0.0), [_stop("u-north", dest, s, gen=1)])
    assert cs.speed == pytest.approx(7.52, abs=0.01)
    stop = cs.itinerary[0]
    why = s._ingress_unworkable(cs, stop, s._now_s)
    assert why is not None and "worked and left lawfully" in why
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert cs.itinerary == [] and cs._mode == "egress"
    assert any("nothing workable left" in n for n in s._notes)


def test_a_deploy_the_world_change_outran_withdraws_before_moving(tmp_path):
    """A deploying crew on a stale stop whose cell the new band reaches 20
    min before its deploy ends + buffer: _rejudge_work withdraws it (cause
    work_judged, the deficit the 10c line sets). Stamped current: kept."""
    for gen, withdrawn in ((1, True), (2, False)):
        s = _judged_session(tmp_path)
        buf = s._cfg.egress_buffer_min * 60.0
        centre = _pt(0, 0.0)
        end = s._now_s + 1_800.0
        _paint(s, p10_paint=[(centre, 200.0, end + buf - 1_200.0)])
        s._field_gen = 2
        cs = _crew_at(s, centre, [_stop("u-c", centre, s, gen=gen)],
                      mode="deploy")
        cs._deploy_end_s = end
        assert not s._caught_at_instant(centre, s._now_s)
        s._rejudge_work(s._now_s, instant=True)
        if withdrawn:
            (w,) = s._withdrawn
            assert w["cause"] == "work_judged" and w["client_id"] == "u-c"
            assert w["deficit_min"] == pytest.approx(20.0, abs=1e-6)
            assert cs._mode == "egress" and cs.itinerary == []
        else:
            assert s._withdrawn == [] and cs._mode == "deploy"
        assert s._instant_overruns == {}


def test_a_crew_the_world_change_lands_on_is_pulled_out_and_reported(
        tmp_path):
    """NEVER SILENT: the new band is over the deploying crew at the very
    instant the fields change (a scripted, unavoidable overrun). It is
    pulled out at once — and the step's audit reports the instant, once,
    even after the crew drove clear (world_change=True)."""
    s = _judged_session(tmp_path)
    centre = _pt(0, 0.0)
    _paint(s, p10_paint=[(centre, 300.0, s._now_s)])
    s._field_gen = 2
    cs = _crew_at(s, centre, [_stop("u-c", centre, s, gen=1)],
                  mode="deploy")
    cs._deploy_end_s = s._now_s + 1_800.0
    assert s._caught_at_instant(centre, s._now_s)
    s._rejudge_work(s._now_s, instant=True)
    (w,) = s._withdrawn
    assert w["cause"] == "work_judged"
    assert set(s._instant_overruns) == {cs.crew.crew_id}
    cs.advance(s._now_s, s._now_s + 900.0, [])
    s._now_s += 900.0
    assert not any(s._cell_unsafe(cs.pos, s._now_s)[1:])     # drove clear
    s._run_audit()
    (row,) = s._crew_overruns
    assert row["world_change"] is True and row["mode"] == "deploy"
    assert row["reason"] == "imminent"
    assert (row["lat"], row["lon"]) == (round(centre.lat, 6),
                                        round(centre.lon, 6))
    s._run_audit()                                   # reported once
    assert s._crew_overruns == []


def test_an_en_route_leg_in_flight_is_rejudged_before_moving(tmp_path):
    """A crew already on its ingress leg east when the band lands across it
    (and clear of its own cell): the stale stop is released before the crew
    moves, and it turns to its next stop."""
    s = _judged_session(tmp_path)
    _paint(s, p10_paint=[(_pt(90, 1.5), 400.0, s._now_s + 600.0)])
    s._field_gen = 2
    east = _stop("u-east", _pt(90, 3.0), s, gen=1)
    west = _stop("u-west", _pt(270, 3.0), s, gen=1)
    cs = _crew_at(s, _pt(0, 0.0), [east, west], mode="travel")
    cs._begin_leg("ingress", east["loc"])
    s._rejudge_work(s._now_s, instant=True)
    assert [x["client_id"] for x in cs.itinerary] == ["u-west"]
    assert cs._mode == "travel" and cs._leg is None
    cs.advance(s._now_s, s._now_s + 60.0, [])
    assert cs._leg["target"] == west["loc"]


def test_world_change_pass_moves_only_the_avoidable(tmp_path):
    """The emergency pass before any crew moves (idle / egressing crews):
    an idle crew the new band reaches within the step relocates now; one
    the change landed ON keeps the step's existing handling (the audit
    reports it); one merely inside the voluntary SEM-10e horizon is left to
    the end-of-step sweep."""
    lead = 900.0
    cases = (("within_step", 600.0, True), ("landed_on", -60.0, False),
             ("proactive_only", 5_400.0, False))
    for name, dt, moved in cases:
        s = _judged_session(tmp_path)
        buf = s._cfg.egress_buffer_min * 60.0
        centre = _pt(0, 0.0)
        _paint(s, p10_paint=[(centre, 300.0, s._now_s + buf + dt)])
        cs = _crew_at(s, centre, [])
        cs.egress_loc, cs.egress_zone_id = None, ""
        s._crew_safety_sweep(lead_s=lead, emergency_only=True)
        assert (cs._mode == "egress") is moved, name
        assert (s._n_relocations == 1) is moved, name


def test_unchanged_fields_are_byte_identical_with_the_knob_on(tmp_path):
    """No world change after the first ignition: armed and off replay each
    other byte for byte (every stop is stamped with the generation dispatch
    planned it against, so nothing is ever re-judged)."""
    def run(cfg):
        s = _session(tmp_path, cfg=cfg)
        return [_hash(s.tick()) for _ in range(16)]
    assert run({}) == run(OFF)
