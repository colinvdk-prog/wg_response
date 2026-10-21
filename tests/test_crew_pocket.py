"""SAFETY SEMANTICS 28 — A CREW NEVER WAITS IN A POCKET (2026-10-21; the Eagle
River valley review's iteration 2; contracts egress_durable_min_h,
escape_last_exit_lead_min; docs/SIMULATOR_DESIGN.md "A crew never waits in a
pocket").

A crew leaving its last stop kept the plan's zone — the NEAREST lawful one —
and every later move keyed on its own cell, never on whether its escape route
still existed: on the valley harness crews egressed to an I-70 zone with 1.2 h
of life whose last lawful exit closed 15 min after they arrived, or sat 4-6 h
in a zone no lawful road left, then fled through the band (the iteration-1
path audit's transit rows). With the knobs:
  (a) an egress target must stay clear of the 10c line egress_durable_min_h
      after arrival — else the nearest zone with a lawful leg that does, else
      a lawful road refuge that does, else the lawful zone outliving it by an
      hour;
  (b) a parked crew whose spot the 10c line reaches within the horizon leaves
      while its last lawful way to such a refuge is still open (the last-exit
      trigger);
  (c) a crew a re-plan strips of every stop where the line arrives within the
      horizon leaves at once instead of stopping mid-road.

These tests pin the knobs (default off, armed by the simulator), byte-identity
with them off on the valley worlds where they bite (goldens computed on commit
0109ad3, before the rule), and each mechanism's armed behaviour there."""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from wgresponse import geo, server, simui
from wgresponse.contracts import ResponseConfig, validate_config

#: the 2026-10-21 iteration-2 knobs (SAFETY SEMANTICS 28-33), all off: the
#: sessions these files' goldens were computed on (commit 0109ad3)
ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False,
             # ... and the iteration-3 knobs (SAFETY SEMANTICS 34-35)
             "sim_far_side_zones": False, "reinforce_entry_by_need": False,
             "ember_eta_landings": False}
#: SEMANTICS 28 alone, at the simulator's values
POCKET_ONLY = dict(ITER2_OFF, egress_durable_min_h=8.0,
                   escape_last_exit_lead_min=30.0)


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _valley(key, cfg, intensity=None, tile="beaver_creek_co"):
    """A one-click valley scenario as the page builds it (the 40-home roster,
    2 in-zone crews + the 1 h reinforcement), re-lit at its own origin and
    wind at `intensity` when given — the valley harness's canonical run
    (scratch_review/eagle_valley/run_matrix.py)."""
    if not simui.tile_file(tile).exists():
        pytest.skip(f"LOUD SKIP: {tile} tile not cached")
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    if intensity is not None:
        f = s._fires["sim-f1"]
        origin, wind = f.origin, f.wind_dir_deg
        s.reset()
        s.ignite(origin.lat, origin.lon, intensity=intensity,
                 wind_dir_deg=wind)
    return s


def _run(s, ticks):
    hashes, states, notes = [], [], []
    seen = len(s._notes)
    for _ in range(ticks):
        st = s.tick()
        hashes.append(_hash(st))
        states.append(st)
        notes.append(list(s._notes[seen:]))
        seen = len(s._notes)
    return hashes, states, notes


# ---------------------------------------------------------------------------
# the knobs
# ---------------------------------------------------------------------------

def test_knobs_are_off_by_default_and_the_simulator_arms_them():
    cfg = ResponseConfig()
    assert cfg.egress_durable_min_h == 0.0
    assert cfg.escape_last_exit_lead_min == 0.0
    s = simui.SimSession("boulder_co", 2, 1, seed="pk", roads_module=None)
    assert s._cfg.egress_durable_min_h == simui.SIM_EGRESS_DURABLE_MIN_H == 8.0
    assert (s._cfg.escape_last_exit_lead_min
            == simui.SIM_ESCAPE_LAST_EXIT_LEAD_MIN == 30.0)
    s = simui.SimSession("boulder_co", 2, 1, seed="pk", roads_module=None,
                         cfg_overrides=ITER2_OFF)
    assert s._cfg.egress_durable_min_h == 0.0
    assert s._cfg.escape_last_exit_lead_min == 0.0


def test_validation_bounds():
    assert validate_config(ResponseConfig(egress_durable_min_h=8.0,
                                          escape_last_exit_lead_min=30.0)) == []
    assert any("egress_durable_min_h" in p for p in validate_config(
        ResponseConfig(egress_durable_min_h=-1.0)))
    assert any("escape_last_exit_lead_min" in p for p in validate_config(
        ResponseConfig(escape_last_exit_lead_min=-5.0)))


# ---------------------------------------------------------------------------
# (a) durable egress — Beaver Creek, the canonical fire at high
# ---------------------------------------------------------------------------

#: sha256(state minus cmd_log)[:16] per tick, computed on 0109ad3 (before
#: SEMANTICS 28-33): the valley harness's beaver_creek_co canonical run at
#: high (the one-click Beaver Creek westerly fire), 26 ticks
GOLDEN_BC_CANONICAL_HIGH = [
    "2c7e1a2b1c85a791", "8c9daf498f1e173a", "39ee8c9824e2e3f8",
    "23cdd8900d593074", "3376bf49080ee37c", "68ce782409f6c285",
    "0e4067fc79ea3c18", "b24025e590ab3784", "2271267fc4e66223",
    "45248fbdcb8165a3", "35efa2822a8c0ae0", "947b72a4ec1e9f71",
    "18112c94a478a610", "4085dde2b777b848", "b1d249ad0ee58f82",
    "8acf029199dd17c0", "181b392bf634e294", "88d55d26877734f5",
    "e808572bca402d93", "f6a1d6b973f8557f", "5ea8869e838af9d0",
    "c8e050765ff2a839", "8c069b7f6b19525d", "910ad1f24346d911",
    "8d81130832d68c86", "52c3228bab941a10"]


def test_durable_egress_off_is_byte_identical_and_runs_the_gauntlet():
    """Off: the iteration-1 session byte for byte — sim-g3 egresses to Avon
    (beaver_creek_co-z1) and sim-g2 to Edwards (beaver_creek_co-z2) as their
    work ends; both zones' last lawful exits close while they wait, and at
    ticks 25 and 26 the sweep sends them out through the band (the path
    audit's transit rows: 75 and 29 min past the 10c line)."""
    s = _valley("beaver_creek_westerly", ITER2_OFF, "high")
    hashes, states, _notes = _run(s, len(GOLDEN_BC_CANONICAL_HIGH))
    assert hashes == GOLDEN_BC_CANONICAL_HIGH
    rows = [(i + 1, o["crew_id"], o.get("transit"), o["mode"])
            for i, st in enumerate(states) for o in st["crew_overruns"]]
    assert rows == [(25, "sim-g3", True, "egress"),
                    (26, "sim-g2", True, "egress")]


def test_durable_egress_points_the_crews_at_a_lasting_zone():
    """Armed (28 alone): as sim-g3 leaves its last stop (tick 14) Avon would
    stay clear only 2.7 h after it arrived — its egress is re-pointed to
    Eagle-Vail (beaver_creek_co-z3), clear far past 8 h on a lawful leg; at
    tick 22 Edwards would stay clear 0.7 h for sim-g2 and no zone lasts 8 h
    on a lawful leg — a road refuge 2 km out does. No crew drives through
    the band on any tick."""
    s = _valley("beaver_creek_westerly", POCKET_ONLY, "high")
    hashes, states, notes = _run(s, len(GOLDEN_BC_CANONICAL_HIGH))
    flat = [n for ns in notes for n in ns]
    assert any("sim-g3 egress re-pointed to beaver_creek_co-z3 (SEMANTICS 28)"
               in n for n in notes[13])
    assert any("sim-g2 egress re-pointed to escape (SEMANTICS 28)" in n
               and "road refuge" in n for n in notes[21])
    assert sum("(SEMANTICS 28)" in n for n in flat) == 2
    assert all(not st["crew_overruns"] for st in states)
    first = next(i for i, ns in enumerate(notes)
                 if any("(SEMANTICS 28)" in n for n in ns))
    # identical to the pre-rule session until the first re-pointed egress
    assert hashes[:first] == GOLDEN_BC_CANONICAL_HIGH[:first]
    assert hashes[first] != GOLDEN_BC_CANONICAL_HIGH[first]


# ---------------------------------------------------------------------------
# (b) the last-exit trigger — Vail, the canonical fire at moderate
# ---------------------------------------------------------------------------

def test_the_last_exit_trigger_leaves_dowd_junction_while_minturn_is_open():
    """Vail canonical, moderate, 43 ticks. Armed (28 alone): by tick 43 the
    three crews park at Dowd Junction (vail_co-z2), whose cell the 10c line
    reaches in ~5 h; the last lawful leg to a zone that stays clear 8 h
    (Minturn) closes within 30 min + a step — all three leave for it at tick
    43. With the trigger off (28a alone) they stay parked there."""
    trig = None
    for name, cfg in (("on", POCKET_ONLY),
                      ("off", dict(POCKET_ONLY,
                                   escape_last_exit_lead_min=0.0))):
        s = _valley("vail_westerly", cfg, "moderate", tile="vail_co")
        _h, states, notes = _run(s, 43)
        last = notes[42]
        moves = [n for n in last if "leaves by its last exit (SEMANTICS 28b)"
                 in n]
        crews = {c["crew_id"]: c for c in states[42]["crews"]}
        if name == "on":
            trig = moves
            assert len(moves) == 3
            assert all("relocating to vail_co-z3" in n for n in moves)
            assert all(crews[c]["relocate_reason"] == "last_exit"
                       for c in ("sim-g1", "sim-g2", "sim-g3"))
        else:
            assert moves == []
            z2 = simui.LOCATIONS["vail_co"].safe_zones[1].loc
            for c in ("sim-g1", "sim-g2", "sim-g3"):
                assert crews[c]["status"] == "available"
                assert geo.haversine_m(
                    geo.LatLon(crews[c]["lat"], crews[c]["lon"]), z2) < 50.0
    assert trig


# ---------------------------------------------------------------------------
# a unit check of the exit window on the trigger's own arithmetic
# ---------------------------------------------------------------------------

def test_an_open_exit_far_from_closing_keeps_the_crew():
    """Vail canonical, moderate, armed, tick 42: the crews at Dowd Junction
    still have a durable exit open longer than the window — _last_exit_choice
    says stay; one step later it says go (tick 43 above)."""
    s = _valley("vail_westerly", POCKET_ONLY, "moderate", tile="vail_co")
    _run(s, 42)
    cs = s._crew_sims["sim-g1"]
    now = s._now_s
    lead = s._judge_lead_s()
    z, why = s._last_exit_choice("sim-g1", cs, now, lead, 8 * 3600.0,
                                 30 * 60.0)
    assert z is None and why is None
    _nom, p10 = s._exposure_at(cs.pos, now)
    assert p10 - now - 1800.0 < 8 * 3600.0          # on the clock



# ---------------------------------------------------------------------------
# (c) a crew a re-plan strips mid-road — Eagle-Vail, the south fire, extreme
# ---------------------------------------------------------------------------

#: sha256(state minus cmd_log)[:16] per tick, computed on 0109ad3: the valley
#: harness's eagle_vail_co south fire at extreme (5 km SSW of the centre on
#: its nearest grass, wind 200), 10 ticks
GOLDEN_EV_SOUTH_EXTREME = [
    "c25cf9271b576cc0", "5bb907c8b4c27596", "2761e42b64f13336",
    "ca2c8ad8517284f9", "efa700b0f9003d27", "fff9948478771fc7",
    "36f72fb440002469", "d2794db28afb1ec8", "21bcedaacad4b12a",
    "ff35dbb80e252c9b"]


def _ev_south(cfg):
    s = _valley("eagle_vail_corridor_westerly", cfg, tile="eagle_vail_co")
    s.reset()
    p = s.fine_fuel_start(200.0, 5000.0)
    s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=200.0)
    return s


def test_a_crew_stripped_mid_road_leaves_at_once():
    """Off (byte-identical to the pre-rule session): at tick 9 a re-plan
    strips sim-g2 of its stops 0.3 h before the 10c line reaches the road it
    stands on; it stops there a whole step, and the sweep sends it out at
    tick 10, 20.9 min past the line. Armed (28): it leaves at once, at tick
    9, by the egress law (no lawful durable refuge is left, so the 20b least-
    exposure escape) — its drive out is 15 min shallower in the band."""
    s = _ev_south(ITER2_OFF)
    hashes, states, _notes = _run(s, 10)
    assert hashes == GOLDEN_EV_SOUTH_EXTREME
    rows = [(i + 1, o["crew_id"], round(o["margin_min"], 1))
            for i, st in enumerate(states) for o in st["crew_overruns"]]
    assert rows == [(10, "sim-g2", -20.9)]
    s = _ev_south(POCKET_ONLY)
    hashes, states, notes = _run(s, 10)
    assert hashes[:8] == GOLDEN_EV_SOUTH_EXTREME[:8]
    assert any("crew sim-g2 leaves where the re-plan left it (SEMANTICS 28c)"
               in n for n in notes[8])
    rows = [(i + 1, o["crew_id"], round(o["margin_min"], 1))
            for i, st in enumerate(states) for o in st["crew_overruns"]]
    assert rows == [(9, "sim-g2", -5.8)]


# ---------------------------------------------------------------------------
# (d) the pocket exit — Beaver Creek, the northwest fire at high
# ---------------------------------------------------------------------------

#: SEMANTICS 28d alone (with 28a/b, which it extends)
POCKET_EXIT = dict(POCKET_ONLY, escape_pocket_exit=True)
#: sha256(state minus cmd_log)[:16] per tick, computed on 0109ad3: the valley
#: harness's beaver_creek_co northwest fire at high (5 km NW of the centre on
#: its nearest grass, wind 300), 6 ticks
GOLDEN_BC_NORTHWEST_HIGH = [
    "007b52debe667814", "925052f3b30de68d", "37255561772ae501",
    "cf6f53937fc237a4", "29bfe495bfe480a2", "f1996e1f4137405d"]


def _bc_northwest(cfg):
    s = _valley("beaver_creek_westerly", cfg)
    s.reset()
    p = s.fine_fuel_start(300.0, 5000.0)
    s.ignite(p.lat, p.lon, intensity="high", wind_dir_deg=300.0)
    return s


def _transit(states, cid):
    return [(i + 1, round(o["margin_min"], 2))
            for i, st in enumerate(states) for o in st["crew_overruns"]
            if o["crew_id"] == cid and o.get("transit")]


def test_pocket_exit_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().escape_pocket_exit is False
    s = simui.SimSession("boulder_co", 2, 1, seed="pk", roads_module=None)
    assert s._cfg.escape_pocket_exit is True
    s = simui.SimSession("boulder_co", 2, 1, seed="pk", roads_module=None,
                         cfg_overrides=ITER2_OFF)
    assert s._cfg.escape_pocket_exit is False


def test_pocket_exit_off_is_byte_identical():
    s = _bc_northwest(ITER2_OFF)
    hashes, _states, notes = _run(s, len(GOLDEN_BC_NORTHWEST_HIGH))
    assert hashes == GOLDEN_BC_NORTHWEST_HIGH
    assert not any("SEMANTICS 28d" in n for ns in notes for n in ns)


def test_a_pocketed_crew_leaves_at_once_not_four_hours_later():
    """The first field pockets sim-g2 at its staging zone, 1.4 km from the
    ignition: every leg to a zone that stays clear 8 h runs inside the band
    (21 min past the 10c line), only Edwards (1 min away, 4 h of life) is
    lawful, and no road refuge is. Without the pocket exit (28a/b only) it
    waits there until the sweep moves it at 4.25 h, through the same band
    262 min past the line. With it, it leaves at once, ~21 min past the
    line, at the end of the first tick (its drive out the tick-2 row) — a
    row, said as one, never deeper than the acceptance's 30 min (the depth a
    ratchet: it may only get shallower)."""
    s = _bc_northwest(POCKET_ONLY)
    _hashes, states, notes = _run(s, 19)
    assert not any("SEMANTICS 28d" in n for ns in notes for n in ns)
    ((tick, margin),) = _transit(states, "sim-g2")
    assert tick >= 18 and margin < -200.0          # the control: 4 h later
    s = _bc_northwest(POCKET_EXIT)
    _hashes, states, notes = _run(s, 19)
    # the end-of-step sweep of the first tick (the first field) says it ...
    said = [n for n in notes[0] if "SEMANTICS 28d" in n]
    assert len(said) == 1
    assert said[0].startswith("crew sim-g2 leaves its pocket now")
    g2 = next(c for c in states[0]["crews"] if c["crew_id"] == "sim-g2")
    assert g2["relocate_reason"] == "pocket_exit"
    # ... and tick 2's path audit reports the drive out, once
    rows = _transit(states, "sim-g2")
    assert len(rows) == 1 and rows[0][0] == 2
    assert rows[0][1] >= -21.5                     # ratchet (measured -20.85)
    # no other crew is pocketed: each has a lawful durable exit
    assert all(n.startswith("crew sim-g2") for ns in notes for n in ns
               if "SEMANTICS 28d" in n)


# ---------------------------------------------------------------------------
# the extended Camp-Fire-scale run, armed — the independent review asked the
# armed twin to cover all four drives (tests/test_crew_path_audit.py pins
# iteration 1's yardstick with these laws off: transit rows at ticks 65, 82,
# 88, 89); law-level assertions on a known defect are xfail(strict), exact
# numbers ratchets (they may only improve)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def campfire_armed():
    key = "paradise_ca"
    if not simui.tile_file(key, fire_zone="extended").exists():
        pytest.skip("LOUD SKIP: paradise_ca extended tile not cached")
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(
        key, 20, 2, seed="alerted|paradise_ca",
        cfg_overrides={"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
                       "deploy_min": 15.0},
        fire_zone="extended")
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    rows, notes = [], []
    seen = len(s._notes)
    st = None
    for _ in range(120):
        st = s.tick()
        rows.extend((st["tick"], o["crew_id"], bool(o.get("transit")),
                     o.get("margin_min")) for o in st["crew_overruns"])
        notes.extend((st["tick"], n) for n in s._notes[seen:])
        seen = len(s._notes)
    return st, rows, notes


def test_extended_campfire_scale_armed_twin(campfire_armed):
    """Armed, 120 ticks (60 h): sim-g2 leaves by its last exit at tick 30
    (28b, a road refuge 7 km out) and its pocket at tick 70 (28d) — its two
    egress drives through the band (ticks 82 and 89, 326 and 217 min past
    the line) are gone; sim-g1's two stay (ticks 65 and 88), the defect the
    next test names. Ratchets: no more rows, none deeper, none new."""
    st, rows, notes = campfire_armed
    assert all(transit for _t, _c, transit, _m in rows)
    assert len(rows) <= 2
    assert {c for _t, c, _tr, _m in rows} <= {"sim-g1"}
    assert min(m for _t, _c, _tr, m in rows) >= -909.1 - 0.5
    assert any(t == 30 and n.startswith("crew sim-g2 leaves by its last exit")
               for t, n in notes)
    assert any(t == 70 and n.startswith("crew sim-g2 leaves its pocket now")
               for t, n in notes)
    assert st["scoreboard"]["served"] >= 20
    assert st["scoreboard"]["crew_overruns_total"] == len(rows)
    assert st["scoreboard"]["transit_overruns_total"] == len(rows)


@pytest.mark.xfail(strict=True, reason=(
    "known defect (2026-10-21): sim-g1 waits at paradise_ca-z1, a refuge with "
    "24 h of life whose only exit (z2) stops being durable at tick 29 and "
    "lawful at tick 43 — the last-exit trigger watches only crews within "
    "egress_durable_min_h of their own line, and once it does every way out "
    "is older than the pocket exit's one-buffer cap; the sweep then moves it "
    "at ticks 64 and 87, 496 and 909 min past the line (a wider watch was "
    "measured worse: restaging (30b) returns crews to the dead end)"))
def test_extended_campfire_scale_no_row_deeper_than_30_min(campfire_armed):
    _st, rows, _notes = campfire_armed
    assert all(m >= -30.0 for _t, _c, _tr, m in rows)


def test_glenwood_pocket_exit_trades_a_deep_late_row_for_a_shallow_early_one():
    """tests/test_crew_egress_judged.py's Glenwood one-click at extreme
    (generic weather; pinned there with the pocket exit off — its window is
    3 ticks): sim-g2 idles at a zone with 1.3 h of life whose only lawful
    way out leads to a zone with the same life. Without the pocket exit it
    waits, and the sweep moves it at tick 7 through the band 92.9 min past
    the line; with it, it leaves at the first field's step — the row at tick
    2, 17.9 min past the line — and none follows (ratchets)."""
    from tests.test_crew_egress_judged import _one_click
    if not simui.tile_file("glenwood_springs_co").exists():
        pytest.skip("LOUD SKIP: glenwood_springs_co tile not cached")

    def run(**cfg):
        s = _one_click("glenwood_springs_westerly", "extreme", "generic",
                       False, **cfg)
        rows, notes = [], []
        seen = len(s._notes)
        for _ in range(8):
            st = s.tick()
            rows.extend((st["tick"], o["crew_id"], o.get("mode"),
                         o.get("margin_min")) for o in st["crew_overruns"])
            notes.extend((st["tick"], n) for n in s._notes[seen:])
            seen = len(s._notes)
        return rows, notes

    rows, _notes = run(escape_pocket_exit=False)
    ((tick, crew, mode, margin),) = rows
    assert (tick, crew, mode) == (7, "sim-g2", "egress")
    assert margin < -90.0                          # the control: deep, late
    rows, notes = run()
    ((tick, crew, mode, margin),) = rows
    assert (tick, crew, mode) == (2, "sim-g2", "egress")
    assert margin >= -18.5                         # ratchet (measured -17.9)
    assert any(t == 1 and n.startswith("crew sim-g2 leaves its pocket now")
               for t, n in notes)
