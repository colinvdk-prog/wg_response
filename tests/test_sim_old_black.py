"""tests/test_sim_old_black.py — OLD BLACK IS A REFUGE (founder ruling
2026-09-26, verbatim: "old burned ground does not count as a violation";
contracts SAFETY SEMANTICS 10 "OLD BLACK IS A REFUGE", knob old_black_refuge —
default OFF, armed by the simulator; docs/SIMULATOR_DESIGN.md "Old black is a
refuge; crews that leave the map are out (2026-09-26)").

OLD BLACK = a cell the nominal front passed at least its cold-black window
(simui._cold_black_pass_s — the per-fuel transit window) ago. Scripted worlds
(the real boulder_co tile, _crew_exposure replaced on the instance — the
convention of tests/test_audit_exposure.py and tests/test_escape_proactive.py)
and the hand-built road network of tests/test_sim_escape_roads.py pin:
  * the 10c audit never counts a crew on old black, neither burned nor
    imminent; black still inside its window is counted exactly as before —
    also when the front passed the crew BETWEEN two audits (the step rule);
  * the sweep never relocates (or proactively decamps) a crew off old black;
    hot black is still fled;
  * a zone or escape destination on old black is a lawful refuge; route /
    exposure metrics charge its samples nothing; the road-escape search
    treats the black behind the front as a lawful, unbounded-horizon refuge;
  * the knob OFF reproduces the strict legacy law.
"""
from __future__ import annotations

import math

import pytest

from tests.test_sim_escape_roads import (
    LEAD, SPEED, _fire, _node_of, _pt, _session as _net_session)
from wgresponse import geo, simui
from wgresponse.contracts import LatLon, ResponseConfig

INF = math.inf


def _session(**cfg):
    return simui.SimSession("boulder_co", 2, 1, seed="old-black",
                            cfg_overrides=cfg)


def _crew(s):
    return next(iter(s._crew_sims.values()))


def _expose(s, nom=INF, p10=INF):
    """Every position reads (nominal, eroded-p10) = (nom, p10)."""
    s._crew_exposure = lambda pos: (nom, p10)


def _zones(s):
    return sorted(s._loc.safe_zones, key=lambda z: z.zone_id)


def _script_by_zone(s, zone_vals, elsewhere=(INF, INF)):
    """Each catalog zone's cell reads zone_vals[zone_id] = (nom, p10); every
    other point reads `elsewhere` (the crew stages at z1)."""
    zones = {(round(z.loc.lat, 6), round(z.loc.lon, 6)): z.zone_id
             for z in s._loc.safe_zones}

    def fake(pos):
        zid = zones.get((round(pos.lat, 6), round(pos.lon, 6)))
        if zid is not None and zid in zone_vals:
            return zone_vals[zid]
        return elsewhere
    s._crew_exposure = fake


# ---------------------------------------------------------------------------
# the knob
# ---------------------------------------------------------------------------

def test_knob_default_off_and_simulator_arms_it():
    assert ResponseConfig().old_black_refuge is False
    assert _session()._cfg.old_black_refuge is True               # sim default
    assert _session(old_black_refuge=False)._cfg.old_black_refuge is False


def test_knob_off_reads_exactly_the_legacy_exposure():
    """Knob OFF: _exposure_at is _crew_exposure verbatim (old black reads as
    the burned, imminent cell it always was) and no crew is ever flagged
    on_old_black."""
    s = _session(old_black_refuge=False)
    now = s._now_s
    _expose(s, nom=now - 5 * 3600.0, p10=now - 6 * 3600.0)
    p = _crew(s).pos
    assert s._exposure_at(p, now) == (now - 5 * 3600.0, now - 6 * 3600.0)
    assert s._on_old_black(p, now) is False
    assert s._cell_unsafe(p, now) == (True, True, True)
    on = _session()
    _expose(on, nom=now - 5 * 3600.0, p10=now - 6 * 3600.0)
    assert on._exposure_at(p, now) == (INF, INF)
    assert on._on_old_black(p, now) is True
    assert on._cell_unsafe(p, now) == (False, False, False)


# ---------------------------------------------------------------------------
# the 10c audit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["idle", "egress", "travel", "deploy"])
def test_old_black_never_counted_in_any_mode(mode):
    """Black older than its window under the crew: no row — neither burned
    nor imminent (the eroded-P10 band that reaches it counts for nothing).
    The strict audit (knob off) counts the same crew-tick as burned."""
    for knob, want in ((True, []), (False, ["burned"])):
        s = _session(old_black_refuge=knob)
        cs = _crew(s)
        cs._mode = mode
        now = s._now_s
        win = s._cold_black_pass_s(cs.pos)
        _expose(s, nom=now - win - 60.0, p10=now - win - 3600.0)
        s._run_audit()
        assert [o["reason"] for o in s._crew_overruns] == want, (knob, mode)


def test_hot_black_still_counted_and_the_window_is_the_boundary():
    """Inside the cold-black window the cell is still burning: counted as
    burned exactly as before. At the window boundary it turns old."""
    s = _session()
    cs = _crew(s)
    now = s._now_s
    win = s._cold_black_pass_s(cs.pos)
    for age, want in ((60.0, ["burned"]), (win - 1.0, ["burned"]),
                      (win, []), (win + 3600.0, [])):
        _expose(s, nom=now - age, p10=now - age - 600.0)
        s._run_audit()
        assert [o["reason"] for o in s._crew_overruns] == want, (age, win)


def test_imminent_band_on_old_black_is_not_counted_unburned_band_is():
    """The eroded-P10 band reaches an old-black cell from nearby fuel: not a
    violation. An UNBURNED cell inside the band is imminent exactly as
    before (the ruling is about burned-out ground only)."""
    s = _session()
    now = s._now_s
    buffer_s = s._cfg.egress_buffer_min * 60.0
    _expose(s, nom=now - 3 * 3600.0, p10=now + 0.5 * buffer_s)
    s._run_audit()
    assert s._crew_overruns == []
    _expose(s, nom=INF, p10=now + 0.5 * buffer_s)
    s._run_audit()
    assert [o["reason"] for o in s._crew_overruns] == ["imminent"]


def test_front_passing_between_audits_is_reported_once(tmp_path):
    """The audit looks once per step: a crew standing on a cell the front
    crossed DURING the step is counted as burned even though its fuel's
    window has run out by the audit (a 5-10 min grass window inside a
    15-min tick must never hide an overrun); black that was already old
    when the step began — or when the crew got there — is the refuge."""
    s = _session()
    cs = _crew(s)
    now = s._now_s
    win = s._cold_black_pass_s(cs.pos)
    step = win + 600.0                       # the window ends inside the step
    t0 = now - step                          # the step's start
    _expose(s, nom=t0 + 60.0, p10=t0)        # crossed 1 min into the step
    cs.pos_since_s = -INF                    # the crew stood there all along
    s._run_audit(since_s=t0)
    assert [o["reason"] for o in s._crew_overruns] == ["burned"]
    s._run_audit()                           # the instant alone: old black
    assert s._crew_overruns == []
    # the crew DROVE onto that black after it had gone old: the refuge
    cs.pos_since_s = t0 + 60.0 + win
    s._run_audit(since_s=t0)
    assert s._crew_overruns == []
    # black already old when the step began: the refuge
    _expose(s, nom=t0 - win - 1.0, p10=t0 - win - 600.0)
    cs.pos_since_s = -INF
    s._run_audit(since_s=t0)
    assert s._crew_overruns == []


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

def test_sweep_never_relocates_a_crew_off_old_black():
    """A crew resting on old black stays put — no relocation, no proactive
    decamp (the eroded P10 there is in the past, which used to trigger
    both). Knob off: the same crew is flagged and moved."""
    for knob, moved in ((True, False), (False, True)):
        s = _session(old_black_refuge=knob)
        cs = _crew(s)
        now = s._now_s
        z1, _z2, _z3 = _zones(s)
        # the crew's zone cell burned 3 h ago; everything else is safe
        _script_by_zone(s, {z1.zone_id: (now - 3 * 3600.0, now - 3 * 3600.0)})
        s._crew_safety_sweep(lead_s=900.0)
        assert (cs._mode == "egress") is moved, knob
        assert (s._n_relocations > 0) is moved, knob
        assert (cs.crew.crew_id in s._relocating) is moved, knob


def test_sweep_still_flees_hot_black():
    s = _session()
    cs = _crew(s)
    now = s._now_s
    z1, _z2, _z3 = _zones(s)
    _script_by_zone(s, {z1.zone_id: (now - 60.0, now - 600.0)})    # burning
    s._crew_safety_sweep(lead_s=900.0)
    assert cs._mode == "egress"
    assert s._relocating.get(cs.crew.crew_id) == "zone_overrun"


def test_state_flags_the_crew_on_old_black():
    s = _session()
    now = s._now_s
    z1, _z2, _z3 = _zones(s)
    _script_by_zone(s, {z1.zone_id: (now - 3 * 3600.0, now - 3 * 3600.0)})
    row = s.state()["crews"][0]
    assert row["on_old_black"] is True
    assert row["left_map"] is False and row["left_map_at_s"] is None
    off = _session(old_black_refuge=False)
    _script_by_zone(off, {z1.zone_id: (now - 3 * 3600.0, now - 3 * 3600.0)})
    assert off.state()["crews"][0]["on_old_black"] is False


# ---------------------------------------------------------------------------
# zones and escape destinations
# ---------------------------------------------------------------------------

def test_zone_on_old_black_is_a_lawful_refuge():
    """Every zone but z2 inside the UNBURNED eroded-P10 band; z2's cell is
    old black: z2 is currently safe (no fail-closed), it is the lawful
    reachable pick and a 'forever' zone for the proactive durable test.
    Knob off: z2 is overrun too and the zone set fails closed."""
    s = _session()
    now = s._now_s
    z1, z2, z3 = _zones(s)
    vals = {z1.zone_id: (INF, now + 600.0),
            z2.zone_id: (now - 4 * 3600.0, now - 4 * 3600.0),
            z3.zone_id: (INF, now + 600.0)}
    _script_by_zone(s, vals)
    assert s._zone_currently_safe(z2, now, lead_s=900.0)
    assert not s._zone_currently_safe(z1, now, lead_s=900.0)
    safe, fail_closed = s._currently_safe_zones(now, lead_s=900.0)
    assert [z.zone_id for z in safe] == [z2.zone_id] and not fail_closed
    cs = _crew(s)
    # from a safe spot (the tile centre), the road to z2 is fully lawful and
    # ends on the refuge: margin +inf at arrival
    pick = s._reachable_zone(s._loc.center, safe, now, cs.speed, 900.0)
    assert pick is not None and pick.zone_id == z2.zone_id
    b, e, w, _t, dm = s._route_metrics(s._loc.center, z2.loc, now, cs.speed,
                                       900.0)
    assert not b and e == 0.0 and w >= 0.0 and math.isinf(dm)
    assert s._exposure_at(z2.loc, now) == (INF, INF)          # 'forever'
    off = _session(old_black_refuge=False)
    _script_by_zone(off, vals)
    assert not off._zone_currently_safe(z2, now, lead_s=900.0)
    assert off._currently_safe_zones(now, lead_s=900.0)[1] is True


def test_route_metrics_charge_nothing_on_old_black():
    """_metrics_of_points: samples on old black at their pass time — no
    exposure, never burned, margins +inf; a sample still burning at its
    pass time is burned; an unburned sample in the band is exposure."""
    s = _session()
    now = s._now_s
    a, b, c = LatLon(40.0, -105.2), LatLon(40.001, -105.2), LatLon(40.002, -105.2)
    vals = {}

    def fake(pos):
        return vals[(round(pos.lat, 6), round(pos.lon, 6))]
    s._crew_exposure = fake
    pts = [(a, 0.0), (b, 60.0), (c, 120.0)]
    old = (now - 4 * 3600.0, now - 5 * 3600.0)
    for p in (a, b, c):
        vals[(round(p.lat, 6), round(p.lon, 6))] = old
    burned, expo, worst, total, dm = s._metrics_of_points(pts, now, 900.0)
    assert (burned, expo, worst, total, dm) == (False, 0.0, INF, 120.0, INF)
    # knob off: the same old black is exposure all the way
    s._cfg.old_black_refuge = False
    burned, expo, worst, _t, dm = s._metrics_of_points(pts, now, 900.0)
    assert not burned and expo == 120.0 and worst < 0.0 and dm < 0.0
    s._cfg.old_black_refuge = True
    vals[(round(b.lat, 6), round(b.lon, 6))] = (now + 30.0, now - 600.0)
    burned, *_ = s._metrics_of_points(pts, now, 900.0)          # hot at pass
    assert burned is True
    vals[(round(b.lat, 6), round(b.lon, 6))] = (INF, now + 60.0)
    burned, expo, *_ = s._metrics_of_points(pts, now, 900.0)    # unburned band
    assert not burned and expo == 60.0


def test_road_escape_into_the_black_behind_the_front(tmp_path):
    """The hand-built network: the crew at node 0 is inside the band; the
    east branch runs through UNBURNED band (exposure), the west branch
    through black the front left 5 h ago. Armed, the west is a zero-exposure
    road to a lawful, unbounded-horizon refuge on the black; knob off, the
    same road is exposure (and the refuge a spot already inside the band)."""
    s, g = _net_session(tmp_path)
    now = s._now_s
    west = [(_pt(270, km), 450.0, now - 5 * 3600.0) for km in (1.5, 3.0, 4.5, 6.0)]
    _fire(s, paint_nom=list(west),
          paint_p10=list(west) + [(_pt(90, 1.5), 500.0, now + 60.0),
                                  (_pt(90, 3.0), 500.0, now + 60.0),
                                  (g.nodes[0], 400.0, now + 60.0),
                                  (_pt(0, 1.5), 450.0, now + 60.0),
                                  (_pt(0, 3.0), 450.0, now + 60.0)])
    found = s._road_escape_search(g.nodes[0], now, SPEED, LEAD)
    assert found is not None
    assert _node_of(g, found["loc"]) in (5, 6, 7)
    # the only exposure is leaving the crew's own band (400 m ~ 53 s); the
    # black itself costs nothing and the refuge on it never burns again
    own_band_s = 400.0 / SPEED
    assert 0.0 < found["exposure_s"] <= own_band_s
    assert math.isinf(found["dm_s"])
    b, e, _w, _t, dm = s._route_metrics(g.nodes[0], found["loc"], now,
                                        SPEED, LEAD)
    assert not b and e == found["exposure_s"] and math.isinf(dm)
    # knob off: the black is band like any other — the west road costs its
    # length in exposure and every node on it is unlawful, so the strict
    # search never picks the black
    s0, g0 = _net_session(tmp_path, old_black_refuge=False)
    _fire(s0, paint_nom=list(west),
          paint_p10=list(west) + [(_pt(90, 1.5), 500.0, now + 60.0),
                                  (_pt(90, 3.0), 500.0, now + 60.0),
                                  (g0.nodes[0], 400.0, now + 60.0),
                                  (_pt(0, 1.5), 450.0, now + 60.0),
                                  (_pt(0, 3.0), 450.0, now + 60.0)])
    b0, e0, _w0, _t0, dm0 = s0._route_metrics(g0.nodes[0], g0.nodes[5], now,
                                              SPEED, LEAD)
    assert not b0 and e0 > own_band_s and dm0 < 0.0
    f0 = s0._road_escape_search(g0.nodes[0], now, SPEED, LEAD)
    assert f0 is not None and _node_of(g0, f0["loc"]) not in (4, 5, 6, 7)


def test_proactive_zone_fallback_keeps_the_strict_crossing_reject():
    """SEM-10e's exposed-transit ZONE fallback rejects any route over a
    nominal-passed cell 'however cold' (tests/test_escape_proactive.py::
    test_picker_fallback_rejects_nominal_crossing). KEPT STRICT under the
    old-black ruling, measured: relaxed to hot black only, it let three
    basalt_carbondale_co crews (standard probe, t36) ride 16 km over old
    black AND the unburned band to z1 — 3 imminent-while-egressing rows
    where the strict rule decamped them 2.1 km by road with none. Old black
    and hot black are both rejected here."""
    for elsewhere_age, want in ((4 * 3600.0, None), (60.0, None)):
        s = simui.SimSession("boulder_co", 2, 1, seed="pro-test",
                             cfg_overrides={})
        cs = _crew(s)
        now = s._now_s
        z1, z2, z3 = _zones(s)
        zones = {(round(z.loc.lat, 6), round(z.loc.lon, 6)): z.zone_id
                 for z in s._loc.safe_zones}

        def fake_expo(pos, z1=z1, z2=z2, z3=z3, zones=zones, now=now,
                      age=elsewhere_age):
            zid = zones.get((round(pos.lat, 6), round(pos.lon, 6)))
            if zid == z2.zone_id:
                return INF, INF
            if zid in (z1.zone_id, z3.zone_id):
                return INF, now + 600.0
            return now - age, now - age
        s._crew_exposure = fake_expo
        table = {(round(z1.loc.lat, 4), round(z1.loc.lon, 4)): (False, 0.0, 0.0, 0.0, -100.0),
                 (round(z2.loc.lat, 4), round(z2.loc.lon, 4)): (False, 0.0, -900.0, 1800.0, INF),
                 (round(z3.loc.lat, 4), round(z3.loc.lon, 4)): (False, 0.0, -900.0, 60.0, -100.0)}
        s._route_metrics = (lambda pos, target, now_, speed, lead, t=table:
                            t[(round(target.lat, 4), round(target.lon, 4))])
        pick = s._proactive_exposed_zone(
            cs.pos, now, cs.speed, 900.0,
            [(z2, INF)])
        got = None if pick is None else ("z2" if pick.zone_id == z2.zone_id else pick.zone_id)
        assert got == want, elsewhere_age


def test_armed_fail_closed_when_every_zone_is_in_the_unburned_band():
    """The ruling is about burned-out ground only: with every zone inside the
    UNBURNED eroded-P10 band the zone set still fails closed to the single
    latest-arrival zone (tests/test_simui.py::test_no_safe_zone_fail_closed_
    alarm_and_safest_pick pins the strict law with fires lit ON the zones)."""
    s = _session()
    now = s._now_s
    zs = _zones(s)
    _script_by_zone(s, {z.zone_id: (INF, now + 600.0 + 60.0 * i)
                        for i, z in enumerate(zs)})
    safe, fail_closed = s._currently_safe_zones(now, lead_s=900.0)
    assert fail_closed and [z.zone_id for z in safe] == [zs[-1].zone_id]


def test_armed_zone_excluded_while_hot_then_a_refuge_once_old_black(tmp_path):
    """A fire lit ON zone z1 (the controlled tile of tests/test_simui.py):
    while its cell is still burning (inside its cold-black window) z1 is
    excluded from the currently-safe set; once burned out it is a lawful
    refuge again — the strict-law pin is tests/test_simui.py::test_dynamic_
    safe_zone_validity_excludes_overrun_zone (knob off)."""
    from tests.test_simui import _crewsafe_session
    s = _crewsafe_session(tmp_path, seed="dz", n_clients=8, n_crews=2)
    z_hit = s._loc.safe_zones[0]
    win = s._cold_black_pass_s(z_hit.loc)
    s.ignite(z_hit.loc.lat, z_hit.loc.lon, intensity="extreme")
    s.set_speed(0.2)                               # 3-min ticks
    s.tick()
    nom, _p10 = s._crew_exposure(z_hit.loc)
    assert nom <= s._now_s < nom + win             # hot
    assert not s._zone_currently_safe(z_hit, s._now_s)
    assert z_hit not in s._currently_safe_zones(s._now_s)[0]
    for _ in range(60):
        if s._now_s - nom >= win:
            break
        s.tick()
    assert s._now_s - nom >= win                   # burned out
    assert s._zone_currently_safe(z_hit, s._now_s)
    assert z_hit in s._currently_safe_zones(s._now_s)[0]


def test_armed_proactive_road_decamp_rides_old_black_not_the_band():
    """The road-network counterpart of tests/test_sim_escape_roads.py::test_
    proactive_counterpart_every_road_exposed_holds (pinned knob-off): every
    point off the zones inside the UNBURNED band — still no lawful road path,
    no voluntary move; every point off the zones 4-h-old black — a refuge,
    so the lawful-only decamp takes a road node over it."""
    from tests.test_sim_escape_roads import _script, _scripted_session
    if not (simui.DEFAULT_TILE_DIR.parent / "roads" / "boulder_co.wgr").exists():
        pytest.skip("LOUD SKIP: boulder_co road graph not cached")
    s = _scripted_session()
    cs = _crew(s)
    now = s._now_s
    _script(s, {z.zone_id: now + 1800.0 for z in s._loc.safe_zones},
            elsewhere=(INF, now - 4 * 3600.0))
    assert s._proactive_escape_zone(cs.pos, now, cs.speed, 900.0,
                                    7200.0) is None
    s2 = _scripted_session()
    cs2 = _crew(s2)
    _script(s2, {z.zone_id: now + 1800.0 for z in s2._loc.safe_zones},
            elsewhere=(now - 4 * 3600.0, now - 4 * 3600.0))
    pick = s2._proactive_escape_zone(cs2.pos, now, cs2.speed, 900.0, 7200.0)
    assert pick is not None and pick.zone_id == "escape"
    b, e, _w, _t, dm = s2._route_metrics(cs2.pos, pick.loc, now, cs2.speed,
                                         900.0)
    assert not b and e == 0.0 and math.isinf(dm)


def test_knob_off_route_metrics_equal_the_legacy_body_over_old_black(tmp_path):
    """Knob OFF over painted old black: _route_metrics equals the verbatim
    pre-change body (tests/test_sim_escape_roads.py::_legacy_route_metrics)."""
    from tests.test_sim_escape_roads import _legacy_route_metrics
    s, g = _net_session(tmp_path, old_black_refuge=False)
    now = s._now_s
    west = [(_pt(270, km), 450.0, now - 5 * 3600.0) for km in (1.5, 3.0, 4.5)]
    _fire(s, paint_nom=west, paint_p10=west)
    for tgt in (g.nodes[5], g.nodes[7], g.nodes[3], _pt(45, 5.0)):
        assert (s._route_metrics(g.nodes[0], tgt, now, SPEED, LEAD)
                == _legacy_route_metrics(s, g.nodes[0], tgt, now, SPEED, LEAD))


# ---------------------------------------------------------------------------
# the burned-out map (the founder's 600x coarse-jump scenario)
# ---------------------------------------------------------------------------

def test_knob_off_keeps_the_strict_audit_on_the_burned_out_map(tmp_path):
    """Control for tests/test_sim_coarse_substep.py::test_burned_out_map_
    crews_only_on_old_black (which pins the armed truth: no rows): with the
    knob OFF the same scenario still reports crews on days-old black as
    'burned' — the strict legacy law, unchanged."""
    from tests.test_simui_policy import FAST_TIGHT, _checkerboard_dir, KEY
    d = _checkerboard_dir(tmp_path)
    s = simui.SimSession(KEY, 8, 2, seed="m1",
                         cfg_overrides={**FAST_TIGHT,
                                        "dispatch_objective": "value",
                                        "old_black_refuge": False},
                         tile_dir=d, roads_module=None)
    loc = simui.LOCATIONS[KEY]
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 8_000.0)
    s.ignite(ign.lat, ign.lon, intensity="high")
    s.tick()
    s.set_speed(600.0)
    burned_rows = 0
    for _ in range(3):
        st = s.tick()
        burned_rows += sum(1 for o in st["crew_overruns"]
                           if o["reason"] == "burned")
    assert burned_rows > 0
