"""THE CREW-PROGRAM REVIEW FIXES (2026-11-17; the founder-approved crew-program
review of 2026-11-11, step 2 — scratch_review/crew_program_review_2026-11-11/
ASSESSMENT.md; docs/SIMULATOR_DESIGN.md "Crew program review fixes
(2026-11-17)"; contracts SAFETY SEMANTICS 46-47).

  46a escape_wait_watch — A WAITING CREW LEAVES BEFORE ITS REFUGE IS A DEAD
      END: the last-exit trigger (28b) watched only crews within the durable
      horizon of their own line; on 24-h fires crews waited at a refuge
      whose own cell stayed clear for hours while its ways out closed, and
      the sweep later forced them out through old band (Vail 24 h: 339.9 min
      past the line, two crews off the map). An idle crew whose spot's
      keep-until (SEMANTICS 38) falls within the last-exit window leaves now.
  46b refuge_keeps_exit — A REFUGE KEEPS A WAY OUT: a Beaver Creek crew
      waited 12 h at a road refuge with no lawful exit from its arrival
      (682.5 min past the line). The stop's own road refuge (38 b, which the
      planner counts and 28a re-points the egress to), 28a's choice and
      restaging (30b) take a refuge that keeps a lawful way out until the
      crew would have to leave it.
  47a crew_overrun_worst_row — THE AUDIT KEEPS THE DEEPER ROW.
  47b replan_on_release — a stop released en route re-plans at once.
  display only: outcome_lost_capacity (a lost home some crew could once
      have served reads "lost — capacity", not "unsavable") and
      unserved_reason_refresh (released_en_route / fire_arrived).

Pinned: the knobs (off by default, armed by the simulator), byte-identity
with them off on the worlds where they bite (goldens computed on commit
e43c24e, before the rules: scratch_review/crew_fixes_2026-11-17/goldens.py),
each mechanism armed, and that the display knobs change no plan."""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from wgresponse import dispatch, geo, server, simui
from wgresponse.contracts import ResponseConfig, validate_config

from tests.test_crew_egress_judged import _judged_session, _pt
from tests.test_crew_work_judged import _crew_at, _paint

FIXES = ("escape_wait_watch", "refuge_keeps_exit", "crew_overrun_worst_row",
         "replan_on_release", "outcome_lost_capacity",
         "unserved_reason_refresh")
FIXES_OFF = {k: False for k in FIXES}
#: the page's payload (the lane-A harness, scratch_review/crew_program_
#: review_2026-11-11/A_safety/matrix.py)
PAGE = {"hold_h": 12, "hold_per_home": True, "proximity_first": True,
        "crews_3h": 0, "spotting": True, "grass_realism": True}


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)            # echoes the override keys themselves
    st.pop("report", None)             # run report: display only
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def _chain(hashes):
    return hashlib.sha256("".join(hashes).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# the knobs
# ---------------------------------------------------------------------------

def test_knobs_are_off_by_default_and_the_simulator_arms_them():
    cfg = ResponseConfig()
    assert all(getattr(cfg, k) is False for k in FIXES)
    assert simui.SIM_REVIEW_FIXES_2026_11_17 == FIXES
    s = simui.SimSession("boulder_co", 2, 1, seed="cw", roads_module=None)
    assert all(getattr(s._cfg, k) is True for k in FIXES)
    s = simui.SimSession("boulder_co", 2, 1, seed="cw", roads_module=None,
                         cfg_overrides=FIXES_OFF)
    assert all(getattr(s._cfg, k) is False for k in FIXES)
    # one explicit override wins alone
    s = simui.SimSession("boulder_co", 2, 1, seed="cw", roads_module=None,
                         cfg_overrides={"refuge_keeps_exit": False})
    assert s._cfg.refuge_keeps_exit is False and s._cfg.escape_wait_watch
    assert validate_config(ResponseConfig(**{k: True for k in FIXES})) == []
    assert any("escape_wait_watch" in p for p in validate_config(
        ResponseConfig(escape_wait_watch=1)))
    # the display split rides only with its knob
    on = simui.SimSession("boulder_co", 2, 1, seed="cw", roads_module=None)
    off = simui.SimSession("boulder_co", 2, 1, seed="cw", roads_module=None,
                           cfg_overrides=FIXES_OFF)
    assert tuple(on.state()["scoreboard"]["outcomes"]) == \
        simui.OUTCOME_TAGS_SPLIT
    assert tuple(off.state()["scoreboard"]["outcomes"]) == simui.OUTCOME_TAGS


# ---------------------------------------------------------------------------
# 46a / 46b on painted fields (straight legs, 250 m cells; zones 6 km E / W)
# ---------------------------------------------------------------------------

def _waiting_world(tmp_path, own_h, band_h, **cfg):
    """A crew idle at zA (6 km east of the centre): zA's own cell reaches
    the 10c line own_h hours from now; a band 500 m round the centre — on
    the only road to zB — reaches it band_h hours after the crew would pass
    it (None: no band); zB is never reached."""
    s = _judged_session(tmp_path, **cfg)
    buf = s._cfg.egress_buffer_min * 60.0
    za = s._zone_by_id["zA"]
    cs = _crew_at(s, za.loc, [], mode="idle", zone_id="zA")
    cs.egress_loc = cs.egress_zone_id = None          # at rest there
    now = s._now_s
    paint = [(za.loc, 300.0, now + buf + own_h * 3600.0)]
    if band_h is not None:
        pass_s = geo.haversine_m(za.loc, _pt(0, 0.0)) / cs.speed
        paint.append((_pt(0, 0.0), 500.0,
                      now + pass_s + buf + band_h * 3600.0))
    _paint(s, p10_paint=paint)
    return s, cs, za


def test_a_refuge_keeps_a_way_out_past_the_waiting_window(tmp_path):
    """SEMANTICS 46b on zA, reached now (the window: the 30-min last-exit
    lead plus a 15-min step): never reached — it lasts; 20 h of life with
    the road to zB open for good — it keeps a way out; the road closing in
    2 h — still one past the window; in 40 min — none a waiting crew could
    take (its exit term 25 min out), whatever zA's own life (20 h or 8.5 h).
    Off: every refuge keeps one. The REFUGE_EXIT_UNTIL_LEAVE arm (measured,
    not shipped) also refuses 20 h of life with the road open 2 h."""
    cases = ((None, None, True), (20.0, None, True), (20.0, 2.0, True),
             (20.0, 40 / 60.0, False), (8.5, 2.0, True),
             (8.5, 40 / 60.0, False))
    for own_h, band_h, want in cases:
        if own_h is None:
            s = _judged_session(tmp_path)
            za = s._zone_by_id["zA"]
            _paint(s)
            spd = next(iter(s._crew_sims.values())).speed
        else:
            s, cs, za = _waiting_world(tmp_path, own_h, band_h)
            spd = cs.speed
        assert s._refuge_keeps_exit(za.loc, s._now_s, spd,
                                    zone_id="zA") is want, (own_h, band_h)
    s, cs, za = _waiting_world(tmp_path, 20.0, 40 / 60.0,
                               refuge_keeps_exit=False)
    assert s._refuge_keeps_exit(za.loc, s._now_s, cs.speed, zone_id="zA")
    assert simui.REFUGE_EXIT_UNTIL_LEAVE is False
    s, cs, za = _waiting_world(tmp_path, 20.0, 2.0)
    try:
        simui.REFUGE_EXIT_UNTIL_LEAVE = True
        assert not s._refuge_keeps_exit(za.loc, s._now_s, cs.speed,
                                        zone_id="zA")
    finally:
        simui.REFUGE_EXIT_UNTIL_LEAVE = False


@pytest.mark.parametrize("armed", [True, False])
def test_a_waiting_crew_leaves_before_its_zone_is_a_dead_end(tmp_path,
                                                             armed):
    """SEMANTICS 46a: the crew idles at zA, whose own cell stays clear 8.25
    h past the 10c line — beyond the 8-h horizon 28b watches — while the
    only road out (to zB) closes 20 min from now: zA is a dead end for an
    arrival later than its keep-until, a quarter of an hour away. Armed, it
    leaves now for zB (loud note, relocate reason 'wait_watch'); off, 28b
    leaves it where it is."""
    s, cs, za = _waiting_world(tmp_path, 8.25 + 0.5 / 60.0, 20 / 60.0,
                               escape_wait_watch=armed)
    lead = s._judge_lead_s()
    ku = s._dest_keep_until(za, cs.speed)
    assert 0.0 < ku - s._now_s <= (s._cfg.escape_last_exit_lead_min * 60.0
                                   + lead)
    n0 = len(s._notes)
    s._crew_safety_sweep(lead_s=lead)
    said = [n for n in s._notes[n0:] if "SEMANTICS 46a" in n]
    if armed:
        assert len(said) == 1 and "relocating to zB" in said[0]
        assert cs.egress_zone_id == "zB" and cs._mode == "egress"
        assert s._relocating[cs.crew.crew_id] == "wait_watch"
    else:
        assert said == [] and cs._mode == "idle"
        assert geo.haversine_m(cs.pos, za.loc) < 1.0


# ---------------------------------------------------------------------------
# 47a: the audit keeps the deeper row (unit)
# ---------------------------------------------------------------------------

def test_the_worst_row_per_crew_is_kept():
    s = simui.SimSession("boulder_co", 2, 2, seed="cw", roads_module=None)
    a, b = sorted(s._crew_sims)

    def row(cid, reason, m, transit=False, exposed=None):
        r = {"crew_id": cid, "reason": reason, "margin_min": m}
        if transit:
            r.update(transit=True, exposed_s=exposed)
        return r
    tick_end = [row(a, "imminent", -10.0), row(b, "burned", -5.0)]
    path = [row(a, "imminent", -145.0, True, 607.3),
            row(b, "imminent", -300.0, True, 20.0)]
    out = s._merge_worst_rows(tick_end, path, path=True)
    # a: the deeper transit row wins, and says the crew is still in the band
    assert out[0]["margin_min"] == -145.0 and out[0]["transit"]
    assert out[0]["caught_at_end"] is True
    # b: burned before imminent — the tick-end row stays, with the step's
    # exposed seconds from its path
    assert out[1]["reason"] == "burned" and not out[1].get("transit")
    assert out[1]["exposed_s"] == 20.0
    # a tie keeps the row already there
    out = s._merge_worst_rows([row(a, "imminent", -145.0)],
                              [row(a, "imminent", -145.0, True, 607.3)],
                              path=True)
    assert not out[0].get("transit") and out[0]["exposed_s"] == 607.3


# ---------------------------------------------------------------------------
# the worlds the review measured (lane A's harness, page settings)
# ---------------------------------------------------------------------------

#: per-tick state hashes chained (sha256 of the concatenation)[:16], computed
#: on e43c24e (before the rules) by scratch_review/crew_fixes_2026-11-17/
#: goldens.py: Vail and Beaver Creek, the canonical fire at extreme, 96 ticks
#: (24 h); Park City, the side fire at extreme, 16 ticks
GOLDEN_CHAIN = {"vail24": "bbb0161681c5bda8", "bc24": "1042e40a236ff8e6",
                "pc_side": "954debb3cd29fdcc"}
TICKS = {"vail24": 96, "bc24": 96, "pc_side": 16}


def _world(world, cfg):
    loc, scen = {"vail24": ("vail_co", "vail_westerly"),
                 "bc24": ("beaver_creek_co", "beaver_creek_westerly"),
                 "pc_side": ("park_city_ut", "park_city_windstorm")}[world]
    if not simui.tile_file(loc).exists():
        pytest.skip(f"LOUD SKIP: {loc} tile not cached")
    mgr = server.SimSessionManager()
    payload = dict(PAGE, scenario=scen, fire_zone="standard")
    if cfg:
        payload["cfg"] = dict(cfg)
    code, out = mgr.new_session(payload)
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    origin, wind = f.origin, f.wind_dir_deg
    s.reset()
    if world == "pc_side":
        b = (wind + 90.0) % 360.0
        p = s.fine_fuel_start(b, 6000.0)
        assert p is not None
        s.ignite(p.lat, p.lon, intensity="extreme", wind_dir_deg=b)
    else:
        s.ignite(origin.lat, origin.lon, intensity="extreme",
                 wind_dir_deg=wind)
    return s


def _run(world, cfg):
    s = _world(world, cfg)
    hashes, rows, notes = [], [], []
    seen = len(s._notes)
    st = None
    for _ in range(TICKS[world]):
        st = s.tick()
        hashes.append(_hash(st))
        rows.extend(dict(o, tick=st["tick"]) for o in st["crew_overruns"])
        notes.extend((st["tick"], n) for n in s._notes[seen:])
        seen = len(s._notes)
    return {"s": s, "st": st, "hashes": hashes, "rows": rows, "notes": notes}


@pytest.fixture(scope="module")
def vail_off():
    return _run("vail24", FIXES_OFF)


@pytest.fixture(scope="module")
def vail_on():
    return _run("vail24", {})


@pytest.fixture(scope="module")
def bc_off():
    return _run("bc24", FIXES_OFF)


@pytest.fixture(scope="module")
def bc_on():
    return _run("bc24", {})


def _brief(rows):
    return [(r["tick"], r["crew_id"], bool(r.get("transit")),
             r.get("margin_min")) for r in rows]


def test_vail_24h_off_is_byte_identical(vail_off):
    """Off: e43c24e's session byte for byte — two crews idle at Minturn
    (vail_co-z3) from T+5.5 h while its roads out close, and at T+22.25 h
    the sweep sends them out through a band 339.9 min past the line."""
    assert _chain(vail_off["hashes"]) == GOLDEN_CHAIN["vail24"]
    assert _brief(vail_off["rows"]) == [(89, "sim-g2", True, -339.91),
                                        (89, "sim-g3", True, -339.91)]


def test_vail_24h_crews_leave_minturn_before_it_is_a_dead_end(vail_on):
    """Armed: at T+16.25 h Minturn's keep-until falls within the window and
    sim-g2 and sim-g3 leave it for a lawful road refuge (46a) — no row, no
    crew off the map, every home still served (ratchets)."""
    st = vail_on["st"]
    assert vail_on["rows"] == []
    assert st["scoreboard"]["crews_left_map"] == 0
    assert st["scoreboard"]["served"] >= 40
    moved = [(t, n.split()[1]) for t, n in vail_on["notes"]
             if "(SEMANTICS 46a)" in n and "vail_co-z3" in n]
    assert (65, "sim-g2") in moved and (65, "sim-g3") in moved


def test_beaver_creek_24h_off_is_byte_identical(bc_off):
    """Off: e43c24e's session byte for byte — sim-g1 sent at T+5.75 h to a
    road refuge with 12.7 h of life and no lawful exit from its arrival,
    moved at T+18 h through a band 682.5 min past the line."""
    assert _chain(bc_off["hashes"]) == GOLDEN_CHAIN["bc24"]
    assert _brief(bc_off["rows"]) == [(73, "sim-g1", True, -682.53)]


def test_beaver_creek_24h_no_crew_is_sent_into_the_pocket(bc_on):
    """Armed: the planner no longer counts a dead-end road refuge as a
    stop's way out (46b in SEMANTICS 38 b) — the last homes in the Eagle-
    Vail pocket are left to the fire (34 -> 31 served, the measured cost)
    and 28a sends the egressing crews to refuges that keep a way out. No
    row; one crew leaves the map by its last lawful way (36) at T+11.5 h
    (ratchets)."""
    st = bc_on["st"]
    assert bc_on["rows"] == []
    assert st["scoreboard"]["served"] >= 31
    assert st["scoreboard"]["crews_left_map"] <= 1
    assert any("keeps a way out" in n and "(SEMANTICS 46b)" in n
               for _t, n in bc_on["notes"])


def test_park_city_side_row_carries_its_depth():
    """Park City, the side fire at extreme, tick 13: sim-g1 arrives on
    timber-litter black 58 min old (its window is 60). Off (e43c24e byte for
    byte): one tick-end row without a depth — the path audit's row, 145 min
    past the line with 607 s in the band, was dropped. Armed: the same one
    row per crew, with its depth and the step's seconds in the band."""
    off = _run("pc_side", FIXES_OFF)
    assert _chain(off["hashes"]) == GOLDEN_CHAIN["pc_side"]
    ((r,),) = [[r for r in off["rows"] if r["tick"] == 13]]
    assert r["crew_id"] == "sim-g1" and "margin_min" not in r
    on = _run("pc_side", {})
    rows = [r for r in on["rows"] if r["tick"] == 13]
    assert len(rows) == 1
    r = rows[0]
    assert r["crew_id"] == "sim-g1" and r["reason"] == "burned"
    assert r["margin_min"] == pytest.approx(-145.0, abs=0.1)
    assert r["exposed_s"] == pytest.approx(607.3, abs=0.1)
    led = on["st"]["scoreboard"]["overrun_ledger"]
    assert any(e["tick"] == 13 and e["margin_min"] is not None for e in led)


# ---------------------------------------------------------------------------
# 47b and the display-only knobs
# ---------------------------------------------------------------------------

def test_a_release_en_route_reopens_the_plan_and_says_why(tmp_path,
                                                          monkeypatch):
    """A head stop the 29 predicate releases en route: armed, it joins the
    re-plan trigger (47b) and the page's reason reads released_en_route
    until a plan labels it again; off, neither."""
    from tests.test_crew_work_judged import _stop
    for armed in (True, False):
        s = _judged_session(tmp_path, replan_on_release=armed,
                            unserved_reason_refresh=armed)
        _paint(s)
        stop = _stop("u-n", _pt(0, 1.5), s, deploy_s=900.0)
        s._unserved_reason["u-n"] = "held"
        cs = _crew_at(s, _pt(0, 0.0), [stop], mode="travel", zone_id="zA")
        now = s._now_s
        monkeypatch.setattr(s, "_client_latest_s", lambda st: now + 600.0)
        s._judge_head_en_route(cs.crew.crew_id, cs, now)
        assert cs.itinerary == []
        assert (s._release_opened == {"u-n"}) is armed
        assert (s._reason_shown.get("u-n") == "released_en_route") is armed
        s._jobbed["u-n"] = "F1"
        (u,) = [x for x in s.state()["unserved"] if x["client_id"] == "u-n"]
        assert u["reason"] == ("released_en_route" if armed else "held")


def _rotated_start(s, pre, bearing):
    """The review's lane-B start (its harness's _rot_start): the scenario's
    fine-fuel start on the rotated bearing when it asks for one, else the
    first burnable cell outward."""
    from wgresponse.fuels import is_burnable
    if pre.get("ign_fine_fuel", False):
        p = s.fine_fuel_start(bearing, pre["ign_dist_m"])
        if p is not None:
            return p.lat, p.lon
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            q = geo.destination(s._loc.center, bearing + db,
                                pre["ign_dist_m"] * shrink)
            rc = geo.latlon_to_rc(s._tile.spec, q)
            if rc is not None and is_burnable(
                    int(round(s._tile.fuel.get(*rc)))):
                return q.lat, q.lon
    raise AssertionError("no burnable start")


def _strip_display(st):
    st = dict(st)
    for k in ("cmd_log", "report", "unserved"):
        st.pop(k, None)
    sb = dict(st["scoreboard"])
    sb.pop("outcomes", None)
    sb.pop("unserved_by_reason", None)
    st["scoreboard"] = sb
    st["clients"] = [{k: v for k, v in c.items() if k != "outcome"}
                     for c in st["clients"]]
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def test_the_display_knobs_change_no_plan(monkeypatch):
    """Park City's windstorm, the fire 40 degrees right of its one-click
    start (the review's lane B), 16 h: with the two display knobs on and off
    every dispatch.plan call returns the same plan and every tick's state
    minus the outcome tags and unserved reasons is the same. On, the nine
    homes the fire reached uncoated were each savable by some crew at some
    plan (lost_capacity, all in the latch); off, all nine read
    'abandoned_unsavable'; burned homes read fire_arrived, never capacity."""
    key = "park_city_windstorm"
    if not simui.tile_file("park_city_ut").exists():
        pytest.skip("LOUD SKIP: park_city_ut tile not cached")
    pre = server.SCENARIOS[key]

    def run(cfg):
        plans = []
        orig = dispatch.plan

        def wrapped(*a, **kw):
            p = orig(*a, **kw)
            plans.append(sorted((cid, tuple(x.client_id for x in cp.stops))
                                for cid, cp in p.plans.items()))
            return p
        monkeypatch.setattr(dispatch, "plan", wrapped)
        mgr = server.SimSessionManager()
        code, out = mgr.new_session(dict(
            scenario=key, spotting=True, grass_realism=True, hold_h=12,
            hold_per_home=True, proximity_first=True, cfg=cfg))
        assert code == 200, out
        s = mgr._entry(out["session_id"]).session
        s.reset()
        b = (pre["ign_bearing"] + 40.0) % 360.0
        s.ignite(*_rotated_start(s, pre, b), intensity=pre["intensity"],
                 wind_dir_deg=b)
        hs, st = [], None
        for _ in range(64):
            st = s.tick()
            hs.append(_strip_display(st))
        monkeypatch.setattr(dispatch, "plan", orig)
        return s, st, hs, plans

    s_on, on, h_on, p_on = run({})
    _s_off, off, h_off, p_off = run({"outcome_lost_capacity": False,
                                     "unserved_reason_refresh": False})
    assert h_on == h_off and p_on == p_off and p_on
    oc_on, oc_off = on["scoreboard"]["outcomes"], off["scoreboard"]["outcomes"]
    assert oc_on["lost_capacity"] == 9 and oc_on["abandoned_unsavable"] == 0
    assert oc_off["abandoned_unsavable"] == 9 and "lost_capacity" not in oc_off
    lost = {c["client_id"] for c in on["clients"]
            if c["outcome"] == "lost_capacity"}
    assert lost <= s_on._ever_feasible
    rep = on["report"]
    assert (rep["reached_uncoated_capacity"]
            + rep["reached_uncoated_unsavable"]) == rep["reached_uncoated"]
    assert all(r.get("lost") in ("capacity", "unsavable")
               for r in rep["homes_reached"] if not r["coated_in_time"])
    burned = {c["client_id"] for c in on["clients"]
              if c["eta_p50_s"] is not None
              and c["eta_p50_s"] <= on["sim_now_s"] and not c["gel_applied"]}
    reasons = {u["client_id"]: u["reason"] for u in on["unserved"]}
    assert burned and all(reasons[c] == "fire_arrived" for c in burned
                          if c in reasons)


def test_the_page_reads_the_new_fields():
    """console/simulator.html: the lost-home split (a scoreboard row shown
    only when the server sends 'lost_capacity', an amber dashed ring, the
    tooltip label, the run report's reason per home and its counts), the
    waiting rule's relocate label, and a transit row that won over a crew
    still in the band read as caught (47a)."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "console"
            / "simulator.html").read_text(encoding="utf-8")
    assert 'id="oc_lost_row"' in html and 'id="oc_lost"' in html
    assert ('Object.prototype.hasOwnProperty.call(oc, "lost_capacity")'
            in html)
    assert 'c.outcome === "lost_capacity"' in html
    assert "border:2px dashed #ffb74d" in html
    assert re.search(r"\blost_capacity:", html)
    assert 'h.lost === "capacity"' in html
    assert "r.reached_uncoated_capacity" in html
    assert 'wait_watch: "leaving before its refuge is a dead end"' in html
    assert ("const isTransitRow = o => !!(o && o.transit && "
            "!o.caught_at_end);") in html
    assert "if (o && o.transit && o.caught_at_end) {" in html
