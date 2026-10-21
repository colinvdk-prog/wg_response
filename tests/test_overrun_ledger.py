"""SAFETY SEMANTICS 33 — THE PAGE SAYS WHAT THE AUDIT SAW (2026-10-21; the
Eagle River valley review's iteration 2, folding in the independent review of
iteration 1; contracts crew_overrun_ledger; docs/SIMULATOR_DESIGN.md "A crew
never waits in a pocket").

state()'s crew_overruns rows are the emitted tick's only, and the page showed
every row as "CAUGHT IN FIRE": a transit row — a crew that drove through the
pessimistic front's band between two tick ends (SEMANTICS 25) — read as a
crew standing in the fire for one tick, then "0 - SAFE". With the knob the
scoreboard also carries the run's totals (crew_overruns_total,
transit_overruns_total) and its latest rows (overrun_ledger), and the page
labels a transit row "drove through the band at hh:mm".

These tests pin the knob (default off, armed by the simulator), the ledger on
the rug-pull control's transit row (tests/test_egress_guard.py pins that
session byte for byte with the knob off), and the page's reading of it."""
from __future__ import annotations

from pathlib import Path

from wgresponse import simui
from wgresponse.contracts import ResponseConfig

from tests.test_simui_policy import _arm_mid_deploy, _inject_spot_fire, _session

ITER2_OFF = {"egress_durable_min_h": 0.0, "escape_last_exit_lead_min": 0.0,
             "egress_corridor_guard": False,
             "reinforce_stage_by_need": False, "idle_restage": False,
             "route_margin_egress_roads_min": 0.0,
             "escape_pocket_exit": False, "plan_egress_time_deadline": False,
             "crew_overrun_ledger": False}
LEDGER_ONLY = dict(ITER2_OFF, crew_overrun_ledger=True)
KEYS = ("crew_overruns_total", "transit_overruns_total", "overrun_ledger")


def test_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().crew_overrun_ledger is False
    s = simui.SimSession("boulder_co", 2, 1, seed="ld", roads_module=None)
    assert s._cfg.crew_overrun_ledger is True
    assert all(k in s.state()["scoreboard"] for k in KEYS)
    s = simui.SimSession("boulder_co", 2, 1, seed="ld", roads_module=None,
                         cfg_overrides={"crew_overrun_ledger": False})
    assert s._cfg.crew_overrun_ledger is False
    assert not any(k in s.state()["scoreboard"] for k in KEYS)


def _rugpull(tmp_path, cfg, until=12):
    """The rug-pull control (iteration 1's session with the iteration-2 laws
    off): at tick 8 the path audit reports sim-g1's departure from sim-c9,
    12 s after the 10c line reached the stop's cell — a transit row."""
    s = _session(tmp_path, ign_dist_m=4_600.0,
                 cfg=dict({"spread_any_angle": False}, **cfg))
    st, _crew, cur, _gap = _arm_mid_deploy(s)
    _inject_spot_fire(s, st, cur)
    st = s.tick()
    states = []
    while st["tick"] < until:
        st = s.tick()
        states.append(st)
    return states


def test_the_ledger_keeps_a_transit_row_after_its_tick(tmp_path):
    states = _rugpull(tmp_path, LEDGER_ONLY)
    by_tick = {st["tick"]: st for st in states}
    t8 = by_tick[8]
    (row,) = t8["crew_overruns"]
    assert row["transit"] is True and row["crew_id"] == "sim-g1"
    sb = t8["scoreboard"]
    assert (sb["crew_overruns_total"], sb["transit_overruns_total"]) == (1, 1)
    (led,) = sb["overrun_ledger"]
    assert (led["tick"], led["crew_id"], led["transit"]) == (8, "sim-g1", True)
    assert led["margin_min"] == round(row["margin_min"], 2)
    assert led["at_s"] == row["at_s"]
    for k in range(9, 12):
        st = by_tick[k]
        assert st["crew_overruns"] == []
        assert st["scoreboard"]["violations"] == 0
        assert st["scoreboard"]["crew_overruns_total"] == 1
        assert st["scoreboard"]["overrun_ledger"] == [led]
    before = [st for st in states if st["tick"] < 8]
    assert all(st["scoreboard"]["crew_overruns_total"] == 0 for st in before)


def test_off_the_scoreboard_carries_no_ledger(tmp_path):
    states = _rugpull(tmp_path, ITER2_OFF, until=9)
    assert any(st["crew_overruns"] for st in states)
    assert not any(k in st["scoreboard"] for st in states for k in KEYS)


def test_the_page_labels_a_transit_row_and_keeps_the_run_total():
    html = (Path(__file__).resolve().parent.parent / "console"
            / "simulator.html").read_text(encoding="utf-8")
    # one label for a row: a transit row is a drive through the band
    assert "function overrunLabel(o)" in html
    assert "DROVE THROUGH THE BAND at ${hhmm}${past}" in html
    assert "CAUGHT IN FIRE (${(o && o.reason) || \"\"})" in html
    # the crew tooltip, its badge and the banner all read it
    assert html.count("overrunLabel(overrunIds[c.crew_id])") >= 2
    assert "transitRows.forEach(o => parts.push(" in html
    # the scoreboard: never the green SAFE after a row this run
    assert "const runRows = sb.crew_overruns_total" in html
    assert "`0 now — ${runRows} this run`" in html
    assert '$("sb_violations").classList.toggle("past", pastRows)' in html
    assert "#sb_violations.past" in html
    assert "drove through the band)" in html
    # the iteration-2 moves are named for what they are (SEMANTICS 28 / 30)
    assert 'const RELOC_LABEL = { restage: "staging toward the work"' in html
    assert 'pocket_exit: "leaving its pocket"' in html
    assert html.count("${RELOC_LABEL[c.relocate_reason] || (") == 2
