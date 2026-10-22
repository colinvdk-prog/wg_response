"""SAFETY SEMANTICS 36-37 AND THE FOUNDER'S TWO SWITCHES — the Eagle River
valley review's iteration 4, the pre-ship fixes (2026-10-21; docs/
SIMULATOR_DESIGN.md "Pre-ship fixes").

(36) A VOLUNTARY REFUGE IS ON THE MAP (contracts escape_refuge_on_map). The
road-refuge branches of the voluntary moves — the durable egress (28a), the
last-exit trigger (28b), the pocket exit (28d), the SEM-10e proactive
decamp — took the SEM-10d search's best node, and a node beyond the tile
edge has no fire field: the independent review's 24 fresh valley fires sent
crews off the map 5 -> 10 times (Beaver Creek's b340 fire: sim-g2 at 2.25 h,
re-pointed by 28a to a road refuge 2.1 km out, 0.1 km past the edge). With
the knob those searches return on-map nodes only.

(37) THE EGRESS SLACK GROWS WITH THE DRIVE (contracts route_margin_egress_
ramp). The flat 45-min road-egress margin asks for 15 min of slack at an
egress's first sample as at its last; on the public Eagle / Gypsum preset it
refused sim-c5 (P10 2.6 h out), whose roads out pass 36-37 min ahead of the
line within the first 2.4 min of the drive. With the ramp the slack over the
30-min road margin is frac x the time on the road, capped at the flat margin.

(34b / 34a') THE FOUNDER'S TWO OPEN CALLS, one line each:
simui.SIM_ARMS_REINFORCE_ENTRY_BY_NEED (arms reinforce_entry_by_need) and
simui.SIM_FAR_ZONES_OFF (cfg.sim_far_zones_off: far zones left out of the
catalog — "beaver_creek_co-z4" drops Cordillera west). Neither default is
changed here.

These tests pin the knobs (default off, 36 / 37 armed by the simulator), the
two switches both ways, the off-map mechanism and its fix on the fresh fire
that showed it, the ramp's T* against a brute-force reference, the Eagle /
Gypsum home, and byte-identity off on the worlds where the laws bite
(goldens computed on commit 1963df2)."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import sys

import pytest

from wgresponse import safety, server, simui
from wgresponse.contracts import (UNREACHED, ResponseConfig, seeded_rng,
                                  validate_config)

from tests.test_safety import _b1_case, _tstar_path

#: the iteration-4 laws off and the founder's 2026-10-22 calls undone: the
#: sessions of commit 1963df2
ITER4_OFF = {"escape_refuge_on_map": False, "route_margin_egress_ramp": 0.0,
             "reinforce_entry_by_need": False, "sim_far_zones_off": ""}
#: 37 as the founder's switch would arm it (the measured value)
RAMP_ON = {"route_margin_egress_ramp": simui.ROUTE_MARGIN_EGRESS_RAMP_MEASURED}


def _hash(st):
    st = dict(st)
    st.pop("cmd_log", None)
    return hashlib.sha256(json.dumps(st, sort_keys=True, allow_nan=False)
                          .encode()).hexdigest()[:16]


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
    """A fresh fire exactly as the review's holdout.py builds it (the valley
    harness's one-click roster, the start law of run_matrix._start)."""
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


def _preset(key, cfg):
    mgr = server.SimSessionManager()
    payload = {"scenario": key}
    if cfg:
        payload["cfg"] = dict(cfg)
    code, out = mgr.new_session(payload)
    assert code == 200, out
    return mgr._entry(out["session_id"]).session


# ---------------------------------------------------------------------------
# the knobs and the switches
# ---------------------------------------------------------------------------

def test_knobs_are_off_by_default_and_the_simulator_arms_36_not_37():
    """36 is armed by the simulator; 37 is built and measured but NOT armed
    (simui.SIM_ROUTE_MARGIN_EGRESS_RAMP = 0.0, the founder's one-line call:
    it keeps the Eagle / Gypsum home and costs homes on the fresh fires) —
    an explicit override arms it."""
    cfg = ResponseConfig()
    assert cfg.escape_refuge_on_map is False
    assert cfg.route_margin_egress_ramp == 0.0
    assert cfg.sim_far_zones_off == ""
    s = simui.SimSession("boulder_co", 2, 1, seed="pf", roads_module=None)
    assert s._cfg.escape_refuge_on_map is True
    assert s._cfg.route_margin_egress_ramp == simui.SIM_ROUTE_MARGIN_EGRESS_RAMP
    assert simui.SIM_ROUTE_MARGIN_EGRESS_RAMP == 0.0
    s = simui.SimSession("boulder_co", 2, 1, seed="pf", roads_module=None,
                         cfg_overrides=RAMP_ON)
    assert s._cfg.route_margin_egress_ramp == 1.0 / 3.0
    s = simui.SimSession("boulder_co", 2, 1, seed="pf", roads_module=None,
                         cfg_overrides=ITER4_OFF)
    assert s._cfg.escape_refuge_on_map is False
    assert s._cfg.route_margin_egress_ramp == 0.0
    bad = ResponseConfig(route_margin_egress_ramp=1.5)
    assert any("route_margin_egress_ramp" in p for p in validate_config(bad))


def test_the_founder_switches_carry_his_2026_10_22_calls():
    """Decided by the founder 2026-10-22: 34b armed ("Either end of I-70"),
    Cordillera west dropped ("Drop it" — not a credible safety zone without
    a ground check); Beaver Creek keeps Freedom Park / Battle Mountain HS."""
    assert simui.SIM_ARMS_REINFORCE_ENTRY_BY_NEED is True
    assert simui.SIM_FAR_ZONES_OFF == "beaver_creek_co-z4"
    s = simui.SimSession("beaver_creek_co", 4, 2, seed="pf",
                         roads_module=None)
    assert s._cfg.reinforce_entry_by_need is True
    assert s._cfg.sim_far_zones_off == "beaver_creek_co-z4"
    ids = [z.zone_id for z in s._loc.safe_zones]
    assert "beaver_creek_co-z4" not in ids
    assert ids[-1] == "beaver_creek_co-z5"
    # the refuge stays in the catalog's record, restorable explicitly
    assert "beaver_creek_co-z4" in [
        z.zone_id for z in simui.LOCATIONS["beaver_creek_co"].far_zones]


def test_one_line_arms_reinforce_entry_by_need(monkeypatch):
    monkeypatch.setattr(simui, "SIM_ARMS_REINFORCE_ENTRY_BY_NEED", False)
    s = simui.SimSession("vail_co", 4, 2, seed="pf", roads_module=None)
    assert s._cfg.reinforce_entry_by_need is False
    monkeypatch.setattr(simui, "SIM_ARMS_REINFORCE_ENTRY_BY_NEED", True)
    s = simui.SimSession("vail_co", 4, 2, seed="pf", roads_module=None)
    assert s._cfg.reinforce_entry_by_need is True
    # an explicit override still wins
    s = simui.SimSession("vail_co", 4, 2, seed="pf", roads_module=None,
                         cfg_overrides={"reinforce_entry_by_need": False})
    assert s._cfg.reinforce_entry_by_need is False


def test_one_line_drops_cordillera_west(monkeypatch):
    loc = simui.LOCATIONS["beaver_creek_co"]
    own = [z.zone_id for z in loc.safe_zones]
    s = simui.SimSession("beaver_creek_co", 4, 2, seed="pf",
                         roads_module=None)
    assert [z.zone_id for z in s._loc.safe_zones] == own + [
        "beaver_creek_co-z5"]
    # the other way of the call: "" keeps it
    monkeypatch.setattr(simui, "SIM_FAR_ZONES_OFF", "")
    s = simui.SimSession("beaver_creek_co", 4, 2, seed="pf",
                         roads_module=None)
    assert [z.zone_id for z in s._loc.safe_zones] == own + [
        "beaver_creek_co-z4", "beaver_creek_co-z5"]
    monkeypatch.setattr(simui, "SIM_FAR_ZONES_OFF", "beaver_creek_co-z4")
    # the same value on another location leaves its own far zones alone
    s = simui.SimSession("eagle_vail_co", 4, 2, seed="pf", roads_module=None)
    assert [z.zone_id for z in s._loc.safe_zones][-2:] == [
        "eagle_vail_co-z7", "eagle_vail_co-z8"]


def test_the_per_zone_switch_through_cfg_and_a_typo_is_loud():
    s = simui.SimSession("beaver_creek_co", 4, 2, seed="pf",
                         roads_module=None,
                         cfg_overrides={"sim_far_zones_off":
                                        "beaver_creek_co-z4, beaver_creek_co-z5"})
    assert [z.zone_id for z in s._loc.safe_zones] == [
        z.zone_id for z in simui.LOCATIONS["beaver_creek_co"].safe_zones]
    with pytest.raises(ValueError, match="names no far zone"):
        simui.SimSession("beaver_creek_co", 4, 2, seed="pf",
                         roads_module=None,
                         cfg_overrides={"sim_far_zones_off":
                                        "beaver_creek_co-z9"})
    # through the page's server path too (a 400, not a crash)
    code, out = server.SimSessionManager().new_session(
        {"scenario": "beaver_creek_westerly",
         "cfg": {"sim_far_zones_off": "cordillera"}})
    assert code == 400 and "names no far zone" in out["error"]


# ---------------------------------------------------------------------------
# (36) a voluntary refuge is on the map
# ---------------------------------------------------------------------------

def _spy_searches(s):
    """Wrap s._road_escape_search: every VOLUNTARY (lawful_only) search's
    result, with the on-map search from the very same state beside it."""
    orig = s._road_escape_search
    calls = []

    def spy(pos, now, speed_mps, lead_s, lawful_only=False,
            min_p10_s=-math.inf, on_map_only=False):
        r = orig(pos, now, speed_mps, lead_s, lawful_only, min_p10_s,
                 on_map_only)
        if lawful_only:
            r_on = (r if on_map_only else
                    orig(pos, now, speed_mps, lead_s, lawful_only, min_p10_s,
                         True))
            calls.append({"now": now, "on_map_only": on_map_only,
                          "loc": None if r is None else r["loc"],
                          "loc_on": None if r_on is None else r_on["loc"]})
        return r
    s._road_escape_search = spy
    return calls


def test_off_the_voluntary_laws_send_a_crew_off_the_map():
    """The fresh fire that showed it (Beaver Creek, bearing 340, 5 km, wind
    340, high; the iteration-3 laws — 36-37 off): 28a re-points sim-g2 at
    2.25 h to a road refuge beyond the tile edge and the crew leaves the map;
    the on-map search from the same state finds no such refuge off the
    map."""
    s = _hold("beaver_creek_co", 340.0, 5000.0, 340.0, "high", ITER4_OFF)
    calls = _spy_searches(s)
    left = None
    for _ in range(10):
        st = s.tick()
        out = [c for c in st["crews"] if c.get("left_map")]
        if out:
            left = out
            break
    assert left is not None and [c["crew_id"] for c in left] == ["sim-g2"]
    off = [c for c in calls if c["loc"] is not None
           and not s._on_map(c["loc"])]
    assert off, "control: a voluntary search returned an off-map refuge"
    for c in off:
        assert c["loc_on"] is None or s._on_map(c["loc_on"])
    assert any(n.startswith("crew sim-g2 egress re-pointed to escape "
                            "(SEMANTICS 28)") for n in s._notes)


def test_armed_an_off_map_refuge_is_only_the_last_exit():
    """The same fire with the simulator's defaults (36 armed; since
    2026-10-22 entry by need armed and Cordillera west dropped — so no
    on-map refuge west of this fire stays clear 8 h): no voluntary law picks
    an off-map refuge early — the durable egress (28a) and the proactive
    decamp (10e) only on-map nodes — and every crew stays on the map past
    the tick the iteration-3 law took sim-g2 off (9): each is told (8-9)
    that its only refuge that stays clear 8 h lies off the map, decamps to
    an on-map road point, and leaves the map at 11 by its LAST lawful way
    there (28b: that way closes within 30 min plus one step). No crew drives
    through the band."""
    s = _hold("beaver_creek_co", 340.0, 5000.0, 340.0, "high", {})
    assert s._cfg.escape_refuge_on_map is True
    picked = []
    orig_retarget, orig_pro = s._durable_retarget, s._proactive_escape_zone

    def retarget(cs, loc, zone_id, why):
        picked.append(("28a", loc))
        return orig_retarget(cs, loc, zone_id, why)

    def pro(pos, now, speed_mps, lead_s, pro_s):
        z = orig_pro(pos, now, speed_mps, lead_s, pro_s)
        if z is not None:
            picked.append(("10e", z.loc))
        return z
    s._durable_retarget, s._proactive_escape_zone = retarget, pro
    orig_off = s._off_map_last_exit
    exits = []

    def off(cid, cs, now, lead_s, dur_s, le_s):
        r = orig_off(cid, cs, now, lead_s, dur_s, le_s)
        if r[0] is not None:
            close = s._off_map_exit[cid][2]
            exits.append((s._tick_n, cid, close - now, le_s + lead_s))
        return r
    s._off_map_last_exit = off
    left_at = None
    rows = 0
    for _ in range(14):
        st = s.tick()
        rows += len(st["crew_overruns"])
        g2 = next(c for c in st["crews"] if c["crew_id"] == "sim-g2")
        if left_at is None and g2.get("left_map"):
            left_at = st["tick"]
    assert picked and all(s._on_map(loc) for _law, loc in picked)
    assert "sim-g2" in {cid for _t, cid, _o, _b in exits}
    for _tick, _cid, open_s, bound_s in exits:
        assert open_s <= bound_s
    assert left_at is not None and left_at > 9
    assert any(n.startswith("crew sim-g2 stays on the map (SEMANTICS 36)")
               for n in s._notes)
    assert any(n.startswith("crew sim-g2 leaves by its last exit (SEMANTICS "
                            "28b)") and "leads off the map" in n
               for n in s._notes)
    assert rows == 0


# ---------------------------------------------------------------------------
# (37) the egress slack grows with the drive
# ---------------------------------------------------------------------------

def _ramp_ref(o, end_s, base_s, slack_s, frac):
    """Brute-force T* over the oracle's own samples with the ramped margin —
    the flat margin at the leg's end (end_s: its full length in seconds)."""
    if o.unverifiable:
        return -math.inf
    t = UNREACHED
    for _i, _p, arr, dt in o.samples:
        extra = (slack_s if dt >= end_s - 1e-6
                 else min(slack_s, frac * dt))
        v = arr - (base_s + extra) - dt
        if v < t:
            t = v
    return t


def _leg_len_m(a, b, path, sample_m):
    pts = (safety._resample_path(path, sample_m) if path is not None
           else safety._sample_polyline(a, b, sample_m))
    return sum(safety._haversine_m(p, q) for p, q in zip(pts, pts[1:]))


def test_ramp_tstar_matches_a_brute_force_reference_on_seeded_legs():
    n_bound = n_path = n_between = 0
    for k in range(200):
        a, b, _depart, speed, ras, cfg = _b1_case(k, tag="ramp-prop")
        rng = seeded_rng("ramp-variant", k)
        cfg = dataclasses.replace(
            cfg, corridor_cell_exact=rng.random() < 0.6,
            corridor_sample_m=rng.choice([90.0, 300.0, 750.0]))
        path = _tstar_path(rng, ras.spec, a, b) if rng.random() < 0.7 else None
        base = rng.choice([10.0, 30.0])
        margin = base + rng.choice([5.0, 15.0, 40.0])
        frac = rng.choice([0.1, 1.0 / 3.0, 1.0])
        got = safety.leg_tstar(a, b, speed, ras, cfg, path=path,
                               margin_min=margin, margin_ramp=(base, frac))
        o = safety.LegOracle(a, b, speed, ras, cfg, path=path,
                             margin_min=margin)
        end_s = _leg_len_m(a, b, path, cfg.corridor_sample_m) / speed
        want = _ramp_ref(o, end_s, base * 60.0, (margin - base) * 60.0, frac)
        if math.isinf(want):
            assert got == want, k
        else:
            assert got == pytest.approx(want, abs=1e-6), (k, got, want)
        lo = safety.leg_tstar(a, b, speed, ras, cfg, path=path,
                              margin_min=margin)
        hi = safety.leg_tstar(a, b, speed, ras, cfg, path=path,
                              margin_min=base)
        assert lo <= got + 1e-6 and got <= hi + 1e-6
        n_path += path is not None
        n_between += (not math.isinf(got)) and lo < got < hi
        n_bound += math.isinf(got)
    assert n_path >= 100 and n_between >= 40, (n_path, n_between)
    # off (None / frac 0 / margin <= base): the flat margin, bit for bit
    a, b, _d, speed, ras, cfg = _b1_case(7, tag="ramp-prop")
    flat = safety.leg_tstar(a, b, speed, ras, cfg, margin_min=45.0)
    for ramp in (None, (30.0, 0.0), (45.0, 0.5), (50.0, 0.5)):
        assert safety.leg_tstar(a, b, speed, ras, cfg, margin_min=45.0,
                                margin_ramp=ramp) == flat


def _egress_world(slack_min, cols):
    """tests/test_egress_margin.py's egress world with the band on `cols` of
    row 10: the crew at (10, 2), the job at (10, 4), the zone at (10, 8);
    each band cell reached by the eroded P10 `slack_min` minutes after the
    crew passes the zone on the earliest plan (drive in, coat, leave)."""
    from tests.test_dispatch import NOW, cc, make_crew, make_ens, make_job,         spec20
    from tests.test_egress_margin import SPEED
    from wgresponse.contracts import Raster, SafeZone
    spec = spec20()
    data = [UNREACHED] * (spec.nrows * spec.ncols)
    crew = make_crew("A", cc(spec, 10, 2))
    job = make_job("c1", cc(spec, 10, 4), NOW + 20 * 3600.0,
                   NOW + 22 * 3600.0)
    zone = SafeZone("z1", cc(spec, 10, 8))
    t_far = NOW + 2000.0 / SPEED + 30 * 60.0 + 4000.0 / SPEED
    for c in cols:
        data[10 * spec.ncols + c] = t_far + slack_min * 60.0
    return crew, job, zone, {"inc1": make_ens(Raster(spec, data))}


def test_the_ramp_serves_a_short_egress_the_flat_margin_refuses():
    """The egress world's band on the leg's first cells (columns 5-6, 1-3
    min into the drive), reached 40 min after the crew passes the zone: the
    flat 45 refuses the stop (no_safe_egress), the ramp serves it (those
    samples need 30 + tau / 3); at 30 min the ramp refuses it too (a
    margin, not a waiver). With the band through the zone's own cell
    (columns 5-8) the leg's END keeps the flat 45: refused at 40 with the
    ramp as without — the zone is where the crew waits after it arrives."""
    from tests.test_egress_margin import _plan
    flat = ResponseConfig(route_margin_egress_roads_min=45.0)
    ramp = ResponseConfig(route_margin_egress_roads_min=45.0,
                          route_margin_egress_ramp=1.0 / 3.0)
    w = _egress_world(40.0, range(5, 7))
    off = _plan(flat, *w)
    assert off.plans["A"].stops == []
    assert [u.reason for u in off.unserved] == ["no_safe_egress"]
    on = _plan(ramp, *w)
    assert [s.client_id for s in on.plans["A"].stops] == ["c1"]
    tight = _plan(ramp, *_egress_world(30.0, range(5, 7)))
    assert [u.reason for u in tight.unserved] == ["no_safe_egress"]
    zone_in_band = _plan(ramp, *_egress_world(40.0, range(5, 9)))
    assert [u.reason for u in zone_in_band.unserved] == ["no_safe_egress"]


def test_the_ramp_is_a_rescue_not_a_new_preference():
    """Two zones: the nearer (10, 8; 4 km from the stop) behind the band of
    columns 5-6 at 40 min — lawful only under the ramped margin, and first
    in the planner's nearest-first order — and the farther (11, 0; 4.1 km)
    on clear ground, lawful at the flat 45. The planner keeps the flat
    margin's choice, the farther zone, with the ramp armed as without it;
    the ramped margin serves only a stop no zone serves at the flat one (the
    nearer zone alone: served through it)."""
    from tests.test_dispatch import cc, spec20
    from tests.test_egress_margin import NOW, _plan, _provider
    from wgresponse import dispatch
    from wgresponse.contracts import SafeZone
    crew, job, near, ens = _egress_world(40.0, range(5, 7))
    far = SafeZone("z2", cc(spec20(), 11, 0))
    flat = ResponseConfig(route_margin_egress_roads_min=45.0)
    ramp = ResponseConfig(route_margin_egress_roads_min=45.0,
                          route_margin_egress_ramp=1.0 / 3.0)
    for cfg in (flat, ramp):
        plan = dispatch.plan([crew], [job], ens, [near, far], cfg, NOW,
                             road_provider=_provider([]))
        assert [s.client_id for s in plan.plans["A"].stops] == ["c1"]
        assert plan.plans["A"].egress_zone_id == "z2", (
            cfg.route_margin_egress_ramp)
    on = _plan(ramp, crew, job, near, ens)
    assert [s.client_id for s in on.plans["A"].stops] == ["c1"]
    assert on.plans["A"].egress_zone_id == "z1"
    assert _plan(flat, crew, job, near, ens).plans["A"].stops == []


def test_the_ramp_keeps_the_eagle_gypsum_home():
    """The public Eagle / Gypsum preset (iteration 1 coats sim-c5 at 0.35 h;
    nominal arrival 3.9 h, P10 2.6 h at tick 1): its roads out pass 36-37
    min ahead of the 10c line within the first 2.4 min of the drive. The
    flat 45-min margin refuses it (no_safe_egress); the ramp coats it at
    tick 2."""
    if not simui.tile_file("eagle_co").exists():
        pytest.skip("LOUD SKIP: eagle_co tile not cached")
    s = _preset("eagle_gypsum_westerly", RAMP_ON)
    gelled = None
    for k in range(4):
        st = s.tick()
        cl = next(c for c in st["clients"] if c["client_id"] == "sim-c5")
        if gelled is None and cl.get("gelled_at_s"):
            gelled = k + 1
    assert gelled == 2
    # the simulator's default: the flat margin (37 not armed)
    s = _preset("eagle_gypsum_westerly", {})
    for _ in range(4):
        st = s.tick()
    cl = next(c for c in st["clients"] if c["client_id"] == "sim-c5")
    assert not cl.get("gelled_at_s")
    un = {u["client_id"]: u.get("reason") for u in st.get("unserved") or []}
    assert un.get("sim-c5") == "no_safe_egress"


# ---------------------------------------------------------------------------
# byte-identity off — goldens computed on commit 1963df2 (out/eagle_valley/
# probes/iter4/golden_calc.py against a pristine 1963df2 worktree)
# ---------------------------------------------------------------------------

GOLDENS = {
    "hold:beaver_creek_co:340:5000:340:high:12": [
        "4a95e0d6cbca7616", "6b4295ae1ef508d6", "cc15b08083d36b62",
        "aa60715168798f20", "3cfb1c3dd157c0cf", "f65231a9a3ccdedb",
        "0da0c7e4b28e5978", "794d3164bd221b51", "6e37c9ce63e6beb5",
        "ec25ec6774e5fbc5", "8ca3dfe4658f02bc", "068a3a79bcdd06b8"],
    "hold:vail_co:70:5000:70:high:26": [
        "f20068832025e4f2", "a0c38fcbb1b5a37b", "573aa2583b3c58cb",
        "94cd6fe3dcb30a6b", "4a545477bca27852", "a91f23c078333c60",
        "699cd0b41ddd578c", "3cea0086150a434c", "c0fbed238a415377",
        "73ef669fc21f0062", "9738de47652b185a", "51a4c6f3381ee8f0",
        "f3c3b734e3a2345b", "8a584f59c9914d73", "bd5b9d050df1fb2b",
        "285e4fb5ec7eb26e", "d01cdfc269c4cb5e", "2392eff01dbd73a1",
        "d6e8a1559d82410c", "0ae572c3caf5919d", "f4a65cfe444d25bd",
        "152b7969b3b07618", "f5f0e6a16378ce79", "dad7071e3331f52b",
        "a53f9df5f6e4bc7b", "d6c2d8628f9022ab"],
    "preset:eagle_gypsum_westerly:4": [
        "756861aff9a1f8d4", "0d7dd201b7007367", "3c19216da9cf12a6",
        "496844fceef7def2"],
}


@pytest.mark.parametrize("world", sorted(GOLDENS))
def test_off_is_byte_identical(world):
    kind, rest = world.split(":", 1)
    if kind == "hold":
        loc, b, d, w, inten, n = rest.split(":")
        s = _hold(loc, float(b), float(d), float(w), inten, ITER4_OFF)
    else:
        key, n = rest.split(":")
        s = _preset(key, ITER4_OFF)
    got = [_hash(s.tick()) for _ in range(int(n))]
    assert got == GOLDENS[world]
