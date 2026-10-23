"""SAFETY SEMANTICS 38 — A WAY OUT LEADS SOMEWHERE THAT LASTS (2026-10-22; the
Eagle River valley review's iteration 5; contracts egress_dest_durable_h;
docs/SIMULATOR_DESIGN.md "A way out leads somewhere that lasts").

The way-out predicate (29) and the planner's egress test (32) accepted a stop
whose lawful way out led to SOME zone, however short-lived. Iteration 4,
with the founder's calls applied, measured two drives deep into the band
from it: on a Beaver Creek fresh fire (bearing 250, 7 km, wind 235, high) a
crew's last homes near Edwards had one lawful way out — to Freedom Park,
3.4 h from the line and with no lawful exit left by the time it arrived —
and the sweep moved it at 7.75 h through band 337 min past the line; with
every crew driving 25 % slower, a Vail crew egressed to an East Vail zone
0.9 h from the line and fled it 46.6 min past it.

With the knob (H hours) a zone counts as a stop's way out only while the crew
reaches it before it is a DEAD END — the zone stays clear of the 10c line H
after the arrival, or a lawful leg from it to another zone, or 28a's road
search from it to a road refuge, that stays clear H is still open a step
after the arrival — or while the crew leaves a stop that has such a road
refuge itself. These tests pin the knob (default off, armed by the
simulator at 28a's 8 h); the planner's law on a hand-built world (a dead end
refused, a zone with a lawful onward exit kept, a stop's own road refuge
exempting it while the crew leaves by then and not after); both deep rows
both ways (the Beaver Creek fresh fire; Vail's late fire at 75 % speed); and
byte-identity off on three worlds where the law bites (goldens computed on
commit 8d0fbdf)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import sys

import pytest

from wgresponse import dispatch, geo, server, simui
from wgresponse.contracts import (UNREACHED, Raster, ResponseConfig, SafeZone,
                                  validate_config)

from tests.test_dispatch import NOW, cc, make_crew, make_ens, make_job, spec20
from tests.test_egress_margin import SPEED, _provider

#: the iteration-5 law off: the sessions of commit 8d0fbdf
ITER5_OFF = {"egress_dest_durable_h": 0.0}


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


def test_knob_is_off_by_default_and_the_simulator_arms_it():
    assert ResponseConfig().egress_dest_durable_h == 0.0
    s = simui.SimSession("boulder_co", 2, 1, seed="dd", roads_module=None)
    assert s._cfg.egress_dest_durable_h == simui.SIM_EGRESS_DEST_DURABLE_H
    assert simui.SIM_EGRESS_DEST_DURABLE_H == simui.SIM_EGRESS_DURABLE_MIN_H
    s = simui.SimSession("boulder_co", 2, 1, seed="dd", roads_module=None,
                         cfg_overrides=ITER5_OFF)
    assert s._cfg.egress_dest_durable_h == 0.0
    assert validate_config(ResponseConfig(egress_dest_durable_h=8.0)) == []
    assert any("egress_dest_durable_h" in p for p in validate_config(
        ResponseConfig(egress_dest_durable_h=-1.0)))


# ---------------------------------------------------------------------------
# the planner (dispatch's zone T*) on a hand-built world
# ---------------------------------------------------------------------------

def _world(onward_clear: bool):
    """The crew at (10, 2), its one job at (10, 4) (its own deadline far
    away), zone z1 at (10, 8) whose own cell the eroded P10 reaches 2 h
    after the crew would arrive there on the earliest plan (drive in, coat
    30 min, leave): lawful to reach, but 1.5 h from the 10c line on arrival.
    Zone z2 at (10, 16), never reached. onward_clear: the road from z1 on to
    z2 is clear; else a band the eroded P10 reaches 30 min BEFORE the crew
    would arrive at z1 lies across row 10 at columns 11-12 — on both the
    z1 -> z2 road and the stop -> z2 road, closed by the time it could be
    driven."""
    spec = spec20()
    data = [UNREACHED] * (spec.nrows * spec.ncols)
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 4), NOW + 20 * 3600.0,
                   NOW + 22 * 3600.0)
    z1 = SafeZone("z1", cc(spec, 10, 8))
    z2 = SafeZone("z2", cc(spec, 10, 16))
    depart = NOW + 2000.0 / SPEED + 30 * 60.0
    arrive_z1 = depart + 4000.0 / SPEED
    data[10 * spec.ncols + 8] = arrive_z1 + 2 * 3600.0
    if not onward_clear:
        for c in (11, 12):
            data[10 * spec.ncols + c] = arrive_z1 - 1800.0
    return crew, job, z1, z2, {"inc1": make_ens(Raster(spec, data))}


def _plan(cfg, crew, job, zones, ens):
    return dispatch.plan([crew], [job], ens, zones, cfg, NOW,
                         road_provider=_provider([]))


def test_the_planner_refuses_a_way_out_into_a_dead_end():
    """z1 alone: lawful to reach, 1.5 h from the line on arrival, no other
    zone to leave it for — served without the law, refused with it
    (no_safe_egress)."""
    crew, job, z1, _z2, ens = _world(True)
    off = _plan(ResponseConfig(), crew, job, [z1], ens)
    assert [s.client_id for s in off.plans["A"].stops] == ["c1"]
    on = _plan(ResponseConfig(egress_dest_durable_h=8.0), crew, job, [z1],
               ens)
    assert on.plans["A"].stops == []
    assert [u.reason for u in on.unserved] == ["no_safe_egress"]
    # a horizon the zone does meet (1 h of its 1.5 h): served again
    short = _plan(ResponseConfig(egress_dest_durable_h=1.0), crew, job, [z1],
                  ens)
    assert [s.client_id for s in short.plans["A"].stops] == ["c1"]


def test_a_short_lived_zone_with_a_lawful_onward_exit_is_a_way_out():
    """z1 short-lived but its road on to z2 (never reached) is clear: not a
    dead end — the stop is served and egresses to z1, the nearer zone, as
    without the law. With that road cut by a band (and the stop's own road to
    z2 with it) z1 is a dead end again and the stop is refused."""
    crew, job, z1, z2, ens = _world(True)
    on = _plan(ResponseConfig(egress_dest_durable_h=8.0), crew, job,
               [z1, z2], ens)
    assert [s.client_id for s in on.plans["A"].stops] == ["c1"]
    assert on.plans["A"].egress_zone_id == "z1"
    crew, job, z1, z2, ens = _world(False)
    off = _plan(ResponseConfig(), crew, job, [z1, z2], ens)
    assert [s.client_id for s in off.plans["A"].stops] == ["c1"]
    on = _plan(ResponseConfig(egress_dest_durable_h=8.0), crew, job,
               [z1, z2], ens)
    assert on.plans["A"].stops == []
    assert [u.reason for u in on.unserved] == ["no_safe_egress"]


def test_a_stop_with_its_own_durable_road_refuge_is_not_bound_by_the_zone():
    """z1 a dead end, but the stop itself has a lawful road to a road refuge
    that stays clear H (the simulator's stop_refuge, here a stub: its
    latest departure): the stop is served while the crew leaves it by then
    (28a re-points such an egress to that refuge), refused when it would
    leave later; the stub is asked at most once per plan point."""
    crew, job, z1, _z2, ens = _world(True)
    cfg = ResponseConfig(egress_dest_durable_h=8.0)
    asked = []

    def refuge(until):
        def f(loc):
            asked.append((round(loc.lat, 5), round(loc.lon, 5)))
            return until
        return f
    depart = NOW + 2000.0 / SPEED + 30 * 60.0
    on = dispatch.plan([crew], [job], ens, [z1], cfg, NOW,
                       road_provider=_provider([]),
                       stop_refuge=refuge(depart + 600.0))
    assert [s.client_id for s in on.plans["A"].stops] == ["c1"]
    assert asked and len(asked) == len(set(asked))
    late = dispatch.plan([crew], [job], ens, [z1], cfg, NOW,
                         road_provider=_provider([]),
                         stop_refuge=refuge(depart - 600.0))
    assert late.plans["A"].stops == []
    assert [u.reason for u in late.unserved] == ["no_safe_egress"]


# ---------------------------------------------------------------------------
# the simulator: the two deep rows iteration 4 measured, both ways
# ---------------------------------------------------------------------------

def _rm():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "scratch_review",
                                    "eagle_valley"))
    try:
        import run_matrix as rm
    finally:
        sys.path.pop(0)
    return rm


def _hold(loc, bearing, dist, wind, inten, cfg):
    """A fresh fire exactly as the review's holdout.py builds it."""
    rm = _rm()
    if not simui.tile_file(loc).exists():
        pytest.skip(f"LOUD SKIP: {loc} tile not cached")
    mgr = server.SimSessionManager()
    payload = {"scenario": rm.LOCS[loc]}
    if cfg:
        payload["cfg"] = dict(cfg)
    code, out = mgr.new_session(payload)
    assert code == 200, out
    s = mgr._entry(out["session_id"]).session
    s.reset()
    p = rm._start(s, bearing, dist)
    s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=wind)
    return s


def _matrix_run(run_id, cfg):
    """A valley-harness run exactly as run_matrix builds it (its late fire
    lit before the ninth tick, as the harness does): (session, a function
    that ticks it once)."""
    rm = _rm()
    loc, ign, inten = run_id.split("|")
    if not simui.tile_file(loc).exists():
        pytest.skip(f"LOUD SKIP: {loc} tile not cached")
    _mgr, s = rm.build(loc, ign, inten, cfg)
    done = [0]

    def tick():
        if ign == "late" and done[0] == 8:
            p = rm._start(s, rm.LATE_SECOND[0], rm.LATE_SECOND[1])
            s.ignite(p.lat, p.lon, intensity=inten,
                     wind_dir_deg=s._fires["sim-f1"].wind_dir_deg)
        done[0] += 1
        return s.tick()
    return s, tick


def _sim_world(name, cfg):
    """(session, tick, ticks) of a golden world: hold:LOC:B:D:W:INT:N (a
    fresh fire) or rm:RUN_ID:N (a valley-harness run)."""
    kind, rest = name.split(":", 1)
    if kind == "hold":
        loc, b, d, w, inten, n = rest.split(":")
        s = _hold(loc, float(b), float(d), float(w), inten, cfg)
        return s, s.tick, int(n)
    run_id, n = rest.rsplit(":", 1)
    s, tick = _matrix_run(run_id, cfg)
    return s, tick, int(n)


B250 = "hold:beaver_creek_co:250:7000:235:high:33"
Z5 = "beaver_creek_co-z5"


@pytest.fixture(scope="module")
def b250_off():
    """Iteration 4's defaults (the law off), 33 ticks (past the 7.75 h of
    its row): per-tick hashes, the rows, and sim-g2's arrival at Freedom
    Park (z5) with z5's keep-until then (the law's own function, read with
    a copy of the config and the cache put back: the session never sees
    the probe — the goldens below pin that)."""
    s, tick, n = _sim_world(B250, ITER5_OFF)
    hashes, rows = [], []
    arrived = keep = None
    st = None
    for _ in range(n):
        st = tick()
        hashes.append(_hash(st))
        rows += st["crew_overruns"]
        cs = s._crew_sims["sim-g2"]
        if (arrived is None and cs.status.value == "available"
                and cs.egress_zone_id == Z5):
            arrived = s._now_s
            cfg, memo = s._cfg, (s._dest_keep, s._dest_keep_key)
            s._cfg = dataclasses.replace(cfg, egress_dest_durable_h=8.0)
            try:
                z5 = next(z for z in s._loc.safe_zones if z.zone_id == Z5)
                keep = s._dest_keep_until(z5, cs.speed)
            finally:
                s._cfg = cfg
                s._dest_keep, s._dest_keep_key = memo
    return st, hashes, rows, arrived, keep


@pytest.fixture(scope="module")
def b250_armed():
    s, tick, n = _sim_world(B250, {})
    assert s._cfg.egress_dest_durable_h == 8.0
    hashes, rows = [], []
    st = None
    for _ in range(n):
        st = tick()
        hashes.append(_hash(st))
        rows += st["crew_overruns"]
    return st, hashes, rows


def test_off_the_crew_is_trapped_at_freedom_park(b250_off):
    """Iteration 4's defaults (the law off): sim-g2 works its last homes
    around Edwards with one lawful way out — Freedom Park (z5) — and arrives
    there while z5 is a dead end (3.4 h from the line, no lawful way on to
    a refuge that lasts: its keep-until is already past); at 7.75 h the
    sweep moves it through band 337 min past the line."""
    _st, _h, rows, arrived, keep = b250_off
    assert arrived is not None and keep is not None and keep < arrived
    deep = [r for r in rows if r.get("transit") and r["crew_id"] == "sim-g2"
            and r["margin_min"] < -300.0]
    assert deep, rows


def test_armed_no_crew_drives_into_the_band_and_no_home_is_lost(
        b250_off, b250_armed):
    """The same fire with the simulator's defaults (the law armed): no crew
    drives through the band in the 33 ticks, and the law costs this fire
    nothing — the crews finish elsewhere (ratchet: served at least as many
    as with the law off; measured 36 = 36 at 12 h)."""
    st_off = b250_off[0]
    st, hashes, rows = b250_armed
    assert rows == []
    assert hashes != b250_off[1]                 # the law bites here
    assert st["scoreboard"]["served"] >= st_off["scoreboard"]["served"]


def _slow_begin(monkeypatch, factor):
    """The review's traffic test (probe_dense_slow.py): every crew drives
    `factor` x the speed the planner and the judges assumed."""
    orig = simui._SimCrew._begin_leg

    def slow(self, kind, target):
        orig(self, kind, target)
        if self._leg is not None:
            self._leg["speed"] = self._leg["speed"] * factor
    monkeypatch.setattr(simui._SimCrew, "_begin_leg", slow)


@pytest.mark.parametrize("armed", [False, True])
def test_the_slower_drive_no_longer_flees_east_vail(monkeypatch, armed):
    """Vail's late fire with every crew driving 25 % slower, 28 ticks (past
    iteration 4's row at tick 26): with the law off sim-g3 egresses to an
    East Vail zone 0.9 h from the line and flees it through the band more
    than 40 min past the line (measured 46.6); armed, no crew drives
    deeper than 30 min past it (measured: no row)."""
    _slow_begin(monkeypatch, 0.75)
    _s, tick = _matrix_run("vail_co|late|high", {} if armed else ITER5_OFF)
    rows = []
    for _ in range(28):
        rows += tick()["crew_overruns"]
    deepest = min((r["margin_min"] for r in rows if r.get("transit")
                   and r.get("margin_min") is not None), default=0.0)
    if armed:
        assert deepest >= -30.0, rows
    else:
        assert deepest < -40.0, rows


# ---------------------------------------------------------------------------
# the armed twins of three legacy tests that now pin the law off: each
# exercises a mechanism (36's last exit off the map, old black as a refuge,
# the road-network area escape) in a situation the law keeps crews out of
# ---------------------------------------------------------------------------

def test_armed_the_b340_crews_stay_on_the_map():
    """tests/test_preship_fixes.py's off-map last-exit fire (Beaver Creek,
    bearing 340, 5 km, wind 340, high) with the simulator's defaults: no
    crew works the homes whose way out ends in the dead end 36's last exit
    leaves — none leaves the map and none drives through the band in its 14
    ticks (with the law off all three crews leave the map at 2.75 h; the
    fresh-fire set, 12 h: 3 -> 0 crews off the map, 12 -> 11 reached homes
    uncoated)."""
    s = _hold("beaver_creek_co", 340.0, 5000.0, 340.0, "high", {})
    assert s._cfg.egress_dest_durable_h == 8.0
    rows = []
    for _ in range(14):
        st = s.tick()
        rows += st["crew_overruns"]
        assert not s._left_map, st["tick"]
    assert rows == []


def test_armed_the_paradise_crown_fire_repro():
    """tests/test_simui.py's founder repro under the crown fire (extended
    Paradise, 64 homes, 5 crews, 180 ticks) with the simulator's defaults:
    no crew is ever idle on burned ground — hot or old black — none drives
    through the band, no violation, none off the map (measured: 25 homes;
    with the law off, 26 homes, the crown fire drives crews onto old black
    for 51 idle crew-ticks — the legacy test's control — and sim-g3 through
    the band at tick 130, 1,346 min past the line)."""
    from tests.test_simui import LEGACY_WX, _idle_crews_in_burn
    if not (simui.DEFAULT_TILE_DIR / "paradise_ca_ext.wgt").exists():
        pytest.skip("LOUD SKIP: no data/tiles/paradise_ca_ext.wgt")
    s = simui.SimSession("paradise_ca", 64, 5, seed="caught-1",
                         fire_zone="extended", cfg_overrides=dict(LEGACY_WX))
    assert s._cfg.egress_dest_durable_h == 8.0
    s.ignite(39.83, -121.52, intensity="extreme")
    st = None
    for _ in range(180):
        st = s.tick()
        assert _idle_crews_in_burn(s, s._now_s) == [], st["tick"]
        assert st["crew_overruns"] == [], st["tick"]
    assert st["scoreboard"]["violations"] == 0
    assert not s._left_map
    assert st["scoreboard"]["served"] >= 25          # ratchet (measured 25)


def test_armed_glenwood_needs_no_area_escape():
    """tests/test_sim_escape_roads.py's Glenwood scenario (40 homes, 6
    in-zone crews and two reinforcements, HIGH 7 km upwind, its physics and
    dispatch pins) with the simulator's defaults: in its 8 ticks no crew
    needs an area escape (with the law off three crews flee on road escapes
    at tick 2), none relocates and none drives through the band — the law
    skips the homes whose crews would have had to flee (measured: 7 homes
    in the 8 ticks, 11 with the law off)."""
    key = "glenwood_springs_co"
    if not (simui.DEFAULT_TILE_DIR / f"{key}.wgt").exists() or not (
            simui.DEFAULT_TILE_DIR.parent / "roads" / f"{key}.wgr").exists():
        pytest.skip("LOUD SKIP: glenwood_springs_co tile/road graph not "
                    "cached")
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(
        key, 40, 6, seed="roads-probe", n_crews_1h=1, n_crews_3h=1,
        cfg_overrides={"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
                       "ros_floor_grass_only": False,
                       "sim_weather_profile": "generic",
                       "spot_fire_size": False, "dispatch_late_shift": True,
                       "dispatch_plan_on_roads": True, "dispatch_hold_h": 24.0,
                       "deploy_min": 15.0, "dispatch_sequence": "auto"})
    assert s._cfg.egress_dest_durable_h == 8.0
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 7000.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    st = None
    for _ in range(8):
        st = s.tick()
        assert st["crew_overruns"] == []
        assert not [cid for cid, cs in s._crew_sims.items()
                    if cs.egress_zone_id == "escape"], st["tick"]
    assert st["scoreboard"]["relocations"] == 0


# ---------------------------------------------------------------------------
# byte-identity off — goldens computed on commit 8d0fbdf (out/eagle_valley/
# probes/iter5/golden_calc.py against the eagle-valley-iter4 worktree) on
# worlds where the law bites: the Beaver Creek fire above (its armed twin
# differs), Vail's south fire (the law changes its plan) and Vail's
# b70 fresh fire (the law ends its west-side work earlier)
# ---------------------------------------------------------------------------

GOLDENS = {
    B250: [
        "2d06bb38eb69ffaf", "8c6ec09653a80f12", "e839db4b8c965d3e",
        "ddeb05e8e8e4f78b", "210d27bd1de0a567", "71e6de5b96587589",
        "0043cd9efbaad4d2", "72e4b20ff80f61cd", "19c70df294301041",
        "8246cd6575a8a086", "c18376716ccfc106", "c2472a737f954bac",
        "605db686e77c1836", "1b26b9f37c7b5396", "4e41e19f57505888",
        "ecc8139454e29a56", "4ad0b68546da3930", "3a0c3a99fd03abac",
        "5837488ba9acfab5", "66047be2e926ed10", "e852808b2b681b77",
        "6b72d095e8f5df20", "3af97bff0b6229bd", "b9d259147e41a7d2",
        "75c110c501dd3cab", "b57aa05092fb2796", "79978f7d364a4ee8",
        "e75214109523e205", "5912bd34ff1414cd", "b5d3273aaa2bd3ac",
        "5e96876b3b2eccc9", "45912fe54640255b", "6c8635ec122b78c1"],
    "rm:vail_co|south|high:16": [
        "578627d76191c093", "57e6b6cb7b014546", "bf466081f94e2690",
        "9edb70a7d4244cde", "183ce8403f62eb12", "7e191d8aed6e16b8",
        "bde1eb3dea383fa1", "c372b7baa2d01b70", "e2dddf78e291c2a6",
        "5d529853dfae9eec", "cdfed190124d7625", "37619fd82304e94b",
        "673336790601253a", "12031053fe59a14e", "e722f6570651d0cb",
        "8c8372bad9af522c"],
    "hold:vail_co:70:5000:70:high:24": [
        "f20068832025e4f2", "a0c38fcbb1b5a37b", "573aa2583b3c58cb",
        "94cd6fe3dcb30a6b", "826092744b714de9", "4883475b0194a982",
        "26b9b7ea131d7023", "b868036817cee6a0", "552b937078f267ee",
        "7d51d364239529ee", "256bed21d1503d60", "3a910e7a9c846772",
        "f30a9520deccd637", "7ca05c232c9e298a", "d5a01abca606a882",
        "23bc4ffefd772c31", "6f1d063968575bfe", "81c29358d3595f04",
        "ed71cf86662b32a5", "c8254a2a83a7c819", "5719f71249f7cd8f",
        "6c6fa0e753119d3d", "fc0203756826cdb2", "d26f287efa243c67"],
}


def test_off_is_byte_identical_on_the_beaver_creek_fire(b250_off):
    assert b250_off[1] == GOLDENS[B250]


@pytest.mark.parametrize("world", sorted(w for w in GOLDENS if w != B250))
def test_off_is_byte_identical_and_the_law_bites(world):
    _s, tick, n = _sim_world(world, ITER5_OFF)
    got = [_hash(tick()) for _ in range(n)]
    assert got == GOLDENS[world]
    _s, tick, n = _sim_world(world, {})
    armed = [_hash(tick()) for _ in range(n)]
    assert armed != got
