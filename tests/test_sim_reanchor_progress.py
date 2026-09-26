"""tests/test_sim_reanchor_progress.py — a re-anchor must keep the fire's
progress.

The simulator's ground truth is the NOMINAL member; a RE-ANCHOR re-solves a
fire from its current burn on a wind-dial change, a new ignition (shared
weather) and the 0.8 x horizon rebase (simui tick() step 2). Until
2026-09-26 it seeded every burned cell AT the anchor time, so each front
cell lost its partial progress into its unburned neighbours: after a
SAME-direction wind "turn" — no physical change — the fire grew 2-58% less
over the next 0.5-3 h than the continuous run, every cell late
(scratch_review/reanchor_progress_probe.py, real tiles, page physics). The
crews are audited against this fire, so a slowed truth made wind-change
scenarios look safer than they were.

Fixed by spread's RE-ANCHOR HISTORY (kept / kept_wx / embers_aloft /
ember_log, all default None = byte-identical) and simui._reanchor_history:
the kept burn enters the solve with each cell's TRUE arrival (final, quiet,
the front re-seeded at its own times, every unburned cell clamped strictly
after the anchor), the weather each front edge's progress was made under,
and the embers still aloft. Pinned here:
  * the new arguments at their defaults leave the pinned spread fingerprints
    untouched (tests/test_spread.py A-D);
  * a no-op re-anchor (same wind / second ignition / horizon rebase)
    reproduces the continuous run CELL FOR CELL, spot fires and embers in
    flight included;
  * a wind turn reproduces ONE continuous run with the same wind shift
    (piecewise edge weather) cell for cell, one change or two — and still
    turns the growth;
  * without the weather history the kept front's old progress is priced at
    the NEW wind (the plain approximation): never retro-burns, but early;
  * the burned mask never shrinks and never jumps at a re-anchor, kept
    cells are history in every member (p10 = p50 = p90 = the true time,
    p10 <= nominal everywhere), and the fire-shading true-arrival record
    agrees with the field.

Offline + fast: tests/test_spread.py's inline grass tiles for the spread
seam, tests/test_simui.py's controlled uniform-grass tiles (roads pinned off)
for the simulator.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

from wgresponse import geo, spread
from wgresponse.contracts import (LatLon, ResponseConfig, WeatherForecast,
                                  WeatherSample)
from wgresponse.simui import (GUST_FACTOR, INTENSITY_WEATHER, LOCATIONS,
                              MPH_TO_MPS, T0_S, SimSession)
from wgresponse.weather import MockWeather

from tests.test_simui import FAST, _controlled_tile_dir
from tests.test_spread import (_LEGACY_FINGERPRINTS, T0, _fingerprint,
                               _spot_setup, center_ignition, const_wx,
                               grass_tile)

HOUR = 3600.0
LOC = "boulder_co"
#: the page's fire physics (server /api/sim/new defaults)
PAGE = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _burned(values, t):
    return {i for i, v in enumerate(values) if v <= t}


def _diff_below(a, b, t_hi):
    """Cells either field reaches by t_hi whose arrivals differ at all."""
    return [i for i, (x, y) in enumerate(zip(a, b))
            if (x <= t_hi or y <= t_hi) and x != y]


def _history(field, t_a, log=()):
    """(kept, embers_aloft) of `field` at t_a — what simui._reanchor_history
    hands spread: every cell burned by t_a with its own arrival, and every
    logged ember thrown by t_a that lands after it on an unburned cell."""
    kept = {i: v for i, v in enumerate(field) if v <= t_a}
    aloft = sorted({(i, land) for i, thrown, land in log
                    if thrown <= t_a < land and i not in kept})
    return kept, aloft


def _shift_wx(loc, t0, hours, w_from, w_to, shift_h, wind_mps=12.0):
    """Hourly forecast from t0, wind FROM w_from, veering to w_to at
    shift_h (the spread tests' constant-weather convention otherwise)."""
    return WeatherForecast(loc=loc, source="test", samples=[
        WeatherSample(t_s=t0 + i * HOUR, wind_speed_mps=wind_mps,
                      wind_gust_mps=wind_mps * 1.3,
                      wind_dir_deg=w_from if i < shift_h else w_to,
                      rh_pct=15.0, temp_c=32.0) for i in range(hours)])


def _session(tmp_path, *, cell_m=250.0, hamlet_half=0, n_clients=1,
             n_crews=1, **cfg):
    """Page-physics session on a controlled flat uniform-grass tile."""
    d = tmp_path / f"tiles_{int(cell_m)}_{hamlet_half}"
    d.mkdir(exist_ok=True)
    d = _controlled_tile_dir(d, LOC, size_km=20.0, cell_m=cell_m,
                             hamlet_half=hamlet_half)
    c = dict(FAST)
    c.update(PAGE)
    c.update(cfg)
    return SimSession(LOC, n_clients, n_crews, seed="progress",
                      cfg_overrides=c, tile_dir=d, roads_module=None)


def _ignite(s, *, bearing=270.0, dist_m=5000.0, intensity="high",
            wind=270.0):
    spec = s._tile.spec
    p = geo.destination(LOCATIONS[LOC].center, bearing, dist_m)
    p = geo.rc_to_latlon(spec, *geo.latlon_to_rc(spec, p))
    return s.ignite(p.lat, p.lon, intensity=intensity, wind_dir_deg=wind)


def _shift_reference(s, f, intensity, w0, shifts):
    """ONE continuous nominal run from the ignition under the simulator's
    own weather script with the wind shifting at whole sim hours
    (MockWeather shifts), priced with PIECEWISE edge weather: an edge
    crossing a shift keeps the progress it made and continues at the new
    rate. shifts = [(sim hour, wind FROM), ...]."""
    p = INTENSITY_WEATHER[intensity]
    u = p["wind_mph"] * MPH_TO_MPS
    wx = MockWeather(seed=f"simui|{s._seed}|wx", base_wind_mps=u, dir_deg=w0,
                     rh_pct=p["rh_pct"], temp_c=p["temp_c"],
                     gust_factor=GUST_FACTOR, t0_s=T0_S,
                     shifts=[(h, u, w, p["rh_pct"]) for h, w in shifts]
                     ).forecast(s._loc.center, s._cfg.horizon_h)
    cfg = dataclasses.replace(s._cfg, edge_weather_piecewise=True)
    return spread.compute_arrival(s._tile, [f.origin], wx, f.t_ignite_s, cfg,
                                  incident_id=f.incident_id, member_k=0).data


def _rle_cells(rle):
    out = set()
    i = 0
    val = rle["first_value"]
    for run in rle["runs"]:
        if val:
            out.update(range(i, i + run))
        i += run
        val = 1 - val
    return out


# ---------------------------------------------------------------------------
# 1. the spread seam: defaults byte-identical, history exact
# ---------------------------------------------------------------------------

def test_history_arguments_default_to_the_pinned_fingerprints():
    """kept / kept_wx / embers_aloft / ember_log at their defaults — passed
    explicitly, or ember_log recording — leave the pinned legacy rasters
    (tests/test_spread.py A-D, spotting on in C) bit for bit, and the
    ensemble too."""
    none = dict(kept=None, kept_wx=None, embers_aloft=None, ember_log=None)
    cfg = ResponseConfig(ensemble_n=4)
    tile = grass_tile()
    ign = center_ignition(tile)
    a = spread.compute_arrival(tile, [ign], const_wx(ign), T0, cfg, **none)
    assert _fingerprint(a) == _LEGACY_FINGERPRINTS["A_flat_wind"]
    tile_b = grass_tile(slope_deg=30.0, aspect_deg=270.0)
    ign_b = center_ignition(tile_b)
    b = spread.compute_arrival(tile_b, [ign_b], const_wx(ign_b, wind_mps=0.0),
                               T0, cfg, **none)
    assert _fingerprint(b) == _LEGACY_FINGERPRINTS["B_upslope_nowind"]
    tile_c, ign_c, wx_c = _spot_setup()
    log = []
    c = spread.compute_arrival(tile_c, [ign_c], wx_c, T0,
                               ResponseConfig(ensemble_n=1,
                                              spotting_enabled=True),
                               ember_log=log)
    assert _fingerprint(c) == _LEGACY_FINGERPRINTS["C_spotting"]
    assert log, "the spotting pin throws embers — the log must see them"
    samples = [WeatherSample(t_s=T0 + i * HOUR, wind_speed_mps=10.0,
                             wind_gust_mps=13.0,
                             wind_dir_deg=270.0 if i < 2 else 180.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(100)]
    wx_d = WeatherForecast(loc=ign, samples=samples, source="test")
    d = spread.compute_arrival(tile, [ign], wx_d, T0, cfg, **none)
    assert _fingerprint(d) == _LEGACY_FINGERPRINTS["D_wind_switch"]
    e1 = spread.ensemble_arrival(tile_c, [ign_c], wx_c, T0,
                                 ResponseConfig(ensemble_n=3,
                                                spotting_enabled=True),
                                 incident_id="x")
    e2 = spread.ensemble_arrival(tile_c, [ign_c], wx_c, T0,
                                 ResponseConfig(ensemble_n=3,
                                                spotting_enabled=True),
                                 incident_id="x", ember_log=[], **{
                                     k: v for k, v in none.items()
                                     if k != "ember_log"})
    for ra, rb in zip(e1.members + [e1.p10, e1.p50, e1.p90],
                      e2.members + [e2.p10, e2.p50, e2.p90]):
        assert ra.data == rb.data


def test_kept_history_reproduces_the_continuous_solve_exactly():
    """Re-solve a spotting fire from its burn at 12 anchors 5 min apart,
    under the SAME weather: every re-solve is the continuous solve bit for
    bit — spot fires and the embers in flight at the anchor included (at
    least one anchor has embers aloft, and they matter)."""
    tile = grass_tile(nrows=81, ncols=81, cell_m=100.0)
    ign = geo.rc_to_latlon(tile.spec, 40, 10)
    wx = const_wx(ign, wind_mps=16.0, rh_pct=10.0, temp_c=36.0)
    cfg = ResponseConfig(ensemble_n=1, spotting_enabled=True, horizon_h=12.0)
    log = []
    cont = spread.compute_arrival(tile, [ign], wx, T0, cfg, incident_id="f",
                                  ember_log=log).data
    t_hi = T0 + cfg.horizon_h * HOUR
    aloft_seen = 0
    for k in range(1, 13):
        t_a = T0 + k * 300.0
        kept, aloft = _history(cont, t_a, log)
        got = spread.compute_arrival(
            tile, [], wx, t_a, cfg, incident_id="f", seed_spotting=False,
            kept=kept, kept_wx=[(T0, wx.at(T0))], embers_aloft=aloft).data
        assert _diff_below(got, cont, t_hi) == [], k
        assert _burned(got, t_a) == set(kept), k
        if aloft:
            aloft_seen += 1
            lost = spread.compute_arrival(
                tile, [], wx, t_a, cfg, incident_id="f", seed_spotting=False,
                kept=kept, kept_wx=[(T0, wx.at(T0))]).data
            assert _diff_below(lost, cont, t_hi), \
                "an ember in flight must land in the re-solve"
    assert aloft_seen >= 1, "no anchor had an ember aloft — test is vacuous"


@pytest.mark.parametrize("spotting", [False, True])
def test_kept_weather_history_reproduces_a_mid_run_wind_shift(spotting):
    """Wind FROM 270 veering to FROM 180 at T0 + 1 h. Re-solving at the shift
    from the 270-run's burn, with its weather history (kept_wx) and the new
    wind, IS the one continuous run under the shifted forecast with
    piecewise edge weather — cell for cell. Without the history the kept
    front's old progress is priced at the new wind: nothing retro-burns
    (every unburned cell arrives after the anchor) but the fire runs early."""
    tile = grass_tile(nrows=81, ncols=81, cell_m=100.0)
    ign = center_ignition(tile)
    cfg = ResponseConfig(ensemble_n=1, spotting_enabled=spotting,
                         horizon_h=12.0)
    t_a = T0 + HOUR
    wx_old = _shift_wx(ign, T0, 30, 270.0, 270.0, 99)
    wx_new = _shift_wx(ign, T0, 30, 180.0, 180.0, 99)
    ref = spread.compute_arrival(
        tile, [ign], _shift_wx(ign, T0, 30, 270.0, 180.0, 1), T0,
        dataclasses.replace(cfg, edge_weather_piecewise=True),
        incident_id="f").data
    log = []
    before = spread.compute_arrival(tile, [ign], wx_old, T0, cfg,
                                    incident_id="f", ember_log=log).data
    kept, aloft = _history(before, t_a, log)
    assert kept == {i: v for i, v in enumerate(ref) if v <= t_a}
    got = spread.compute_arrival(
        tile, [], wx_new, t_a, cfg, incident_id="f", seed_spotting=False,
        kept=kept, kept_wx=[(T0, wx_old.at(T0))], embers_aloft=aloft).data
    t_hi = T0 + cfg.horizon_h * HOUR
    assert _diff_below(got, ref, t_hi) == []
    # the plain approximation: the new wind applied to the old progress
    approx = spread.compute_arrival(
        tile, [], wx_new, t_a, cfg, incident_id="f", seed_spotting=False,
        kept=kept, embers_aloft=aloft).data
    assert _burned(approx, t_a) == set(kept)
    assert all(v > t_a for i, v in enumerate(approx) if i not in kept)
    d = [(x - y) for x, y in zip(approx, ref)
         if math.isfinite(x) and math.isfinite(y) and y <= t_a + 3 * HOUR
         and y > t_a]
    assert d and sum(d) / len(d) < 0.0, "the approximation runs early"


def test_history_memo_and_reference_paths_agree(monkeypatch):
    """The ROS memo stays byte-identical to the WGRESP_NO_ROS_MEMO reference
    path on the history spans too (the memo-vs-reference law)."""
    tile = grass_tile(nrows=61, ncols=61, cell_m=100.0, slope_deg=12.0,
                      aspect_deg=200.0)
    ign = center_ignition(tile)
    cfg = ResponseConfig(ensemble_n=1, spotting_enabled=True, horizon_h=8.0)
    wx_old = _shift_wx(ign, T0, 20, 250.0, 250.0, 99)
    before = spread.compute_arrival(tile, [ign], wx_old, T0, cfg,
                                    incident_id="f").data
    t_a = T0 + 1.25 * HOUR
    kept, _ = _history(before, t_a)
    args = (tile, [], _shift_wx(ign, T0, 20, 340.0, 340.0, 99), t_a, cfg)
    kw = dict(incident_id="f", seed_spotting=False, kept=kept,
              kept_wx=[(T0, wx_old.at(T0))])
    memo = spread.compute_arrival(*args, **kw).data
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(*args, **kw).data
    assert memo == ref


def test_ensemble_history_is_final_in_every_member():
    """ensemble_arrival forwards the history to every member: the kept burn
    is the truth in every member and quantile (its true times), nothing
    arrives at or before the anchor anywhere else, and p10 stays
    nominal-bounded."""
    tile = grass_tile(nrows=61, ncols=61, cell_m=100.0)
    ign = center_ignition(tile)
    cfg = ResponseConfig(ensemble_n=4, spotting_enabled=True, horizon_h=8.0)
    wx_old = _shift_wx(ign, T0, 20, 270.0, 270.0, 99)
    log = []
    before = spread.compute_arrival(tile, [ign], wx_old, T0, cfg,
                                    incident_id="f", ember_log=log).data
    t_a = T0 + HOUR
    kept, aloft = _history(before, t_a, log)
    log2 = []
    ens = spread.ensemble_arrival(
        tile, [], _shift_wx(ign, T0, 20, 200.0, 200.0, 99), t_a, cfg,
        incident_id="f", seed_spotting=False, kept=kept,
        kept_wx=[(T0, wx_old.at(T0))], embers_aloft=aloft, ember_log=log2)
    assert len(ens.members) == 4
    for r in ens.members + [ens.p10, ens.p50, ens.p90]:
        for i, v in enumerate(r.data):
            if i in kept:
                assert v == kept[i]
            else:
                assert v > t_a
    nom = ens.members[0].data
    assert all(p <= n for p, n in zip(ens.p10.data, nom))
    assert log2 and all(thrown > t_a for _i, thrown, _land in log2), \
        "kept cells are quiet: only post-anchor burning throws embers"


# ---------------------------------------------------------------------------
# 2. the simulator: no-op re-anchors are the continuous run
# ---------------------------------------------------------------------------

def test_same_wind_reanchor_reproduces_the_continuous_run(tmp_path):
    """The founder-visible defect: set_wind(the SAME direction) — a world
    change with no physical change — used to slow the fire (every cell
    late). It now leaves the nominal truth untouched cell for cell, with
    spot fires and an ember in flight at the anchor."""
    s = _session(tmp_path)
    fid = _ignite(s)
    for _ in range(2):
        s.tick()
    f = s._fires[fid]
    t_sw = s._now_s
    old = f.ens.members[0].data
    kept, aloft = s._reanchor_history(f, t_sw)
    assert aloft, "scenario must have an ember aloft at the re-anchor"
    s.set_wind(f.wind_dir_deg)
    s.tick()
    assert f.anchor_t0_s == t_sw, "the no-op set_wind must re-anchor"
    new = f.ens.members[0].data
    t_hi = f.t_ignite_s + s._cfg.horizon_h * HOUR
    assert _diff_below(new, old, t_hi) == []
    # the kept burn is the truth in the new field, cell for cell
    assert {i: v for i, v in enumerate(new) if v <= t_sw} == kept


def test_second_ignition_does_not_slow_the_first_fire(tmp_path):
    """Lighting a second fire rebuilds the SHARED weather (same intensity,
    same wind: nothing physical changes for fire 1) and re-anchors fire 1:
    its growth is exactly what it would have been without the second fire."""
    s = _session(tmp_path)
    fid = _ignite(s, bearing=270.0)
    for _ in range(4):
        s.tick()
    f = s._fires[fid]
    t_sw = s._now_s
    old = f.ens.members[0].data
    fid2 = _ignite(s, bearing=90.0, wind=None)
    s.tick()
    assert f.anchor_t0_s == t_sw and s._fires[fid2].ens is not None
    new = f.ens.members[0].data
    assert _diff_below(new, old, f.t_ignite_s + s._cfg.horizon_h * HOUR) == []
    for lead in (0.5, 1.0, 1.5, 3.0):
        t = t_sw + lead * HOUR
        assert len(_burned(new, t)) == len(_burned(old, t)) > 0


def test_horizon_rebase_is_transparent(tmp_path):
    """The 0.8 x horizon rebase re-anchors with no weather change: the fire
    continues exactly as ONE long-horizon solve says (it used to lose every
    front cell's progress at each rebase)."""
    s = _session(tmp_path, horizon_h=4.0, extreme_p10_h=4.0)
    fid = _ignite(s)
    s.tick()
    f = s._fires[fid]
    first = f.ens
    while f.ens is first:
        t_rb = s._now_s
        s.tick()
    assert f.anchor_t0_s == t_rb and t_rb - f.t_ignite_s > 0.8 * 4.0 * HOUR
    long_cfg = dataclasses.replace(s._cfg, horizon_h=24.0)
    ref = spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                                 long_cfg, incident_id=fid, member_k=0).data
    new = f.ens.members[0].data
    assert _diff_below(new, ref, t_rb + 4.0 * HOUR) == []
    assert len(_burned(new, t_rb + HOUR)) > len(_burned(new, t_rb))


# ---------------------------------------------------------------------------
# 3. the simulator: a wind turn continues the front's real progress
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("turn", [45.0, 90.0])
def test_turn_reproduces_the_continuous_shift_run(tmp_path, turn):
    """Turn the dial at T+1 h: the re-anchored fire IS the continuous run
    with the same wind shift at that hour (each edge in progress keeps what
    it burned under the old wind and finishes at the new rate) — cell for
    cell, spotting on — the burn at the switch is untouched, and the growth
    after it runs toward the NEW downwind bearing."""
    s = _session(tmp_path)
    fid = _ignite(s)
    f = s._fires[fid]
    for _ in range(4):
        s.tick()
    t_sw = s._now_s
    assert t_sw == T0_S + HOUR == f.t_ignite_s + HOUR
    old = f.ens.members[0].data
    w_new = (270.0 + turn) % 360.0
    s.set_wind(w_new)
    s.tick()
    new = f.ens.members[0].data
    assert _burned(new, t_sw) == _burned(old, t_sw)
    ref = _shift_reference(s, f, "high", 270.0, [(1, w_new)])
    assert _diff_below(new, ref, f.t_ignite_s + s._cfg.horizon_h * HOUR) == []
    # growth direction: the centroid of the cells burned in the 1.5 h after
    # the switch sits on the new downwind side of the burn at the switch
    spec = s._tile.spec
    nc = spec.ncols

    def centroid(cells):
        xs = [geo.rc_to_latlon(spec, i // nc, i % nc) for i in cells]
        return (sum(p.lat for p in xs) / len(xs),
                sum(p.lon for p in xs) / len(xs))

    a = centroid(_burned(new, t_sw))
    b = centroid(_burned(new, t_sw + 1.5 * HOUR) - _burned(new, t_sw))
    brg = geo.bearing_deg(LatLon(*a), LatLon(*b))
    assert abs((brg - (w_new + 180.0) + 180.0) % 360.0 - 180.0) <= 30.0, brg


def test_two_turns_keep_each_epochs_progress(tmp_path):
    """Two dial turns an hour apart: the fire keeps, for every edge still in
    progress, what it burned under EACH earlier wind (the fire's weather
    history) — the continuous run with both shifts, cell for cell."""
    s = _session(tmp_path)
    fid = _ignite(s)
    f = s._fires[fid]
    for _ in range(4):
        s.tick()
    s.set_wind(0.0)
    for _ in range(4):
        s.tick()
    s.set_wind(315.0)
    s.tick()
    assert f.anchor_t0_s == T0_S + 2 * HOUR
    assert [round((t - T0_S) / HOUR, 6) for t, _s in f._wx_hist] == [0, 1, 2]
    ref = _shift_reference(s, f, "high", 270.0, [(1, 0.0), (2, 315.0)])
    new = f.ens.members[0].data
    assert _diff_below(new, ref, f.t_ignite_s + s._cfg.horizon_h * HOUR) == []


# ---------------------------------------------------------------------------
# 4. invariants across a scripted run with every re-anchor cause
# ---------------------------------------------------------------------------

def test_burned_mask_never_shrinks_or_jumps_across_reanchors(tmp_path):
    """Turn, same-wind reset, a second ignition, another turn: tick over tick
    the burned mask (state.terrain.burned) only grows; at every re-anchor the
    mask AT the anchor is exactly the old field's; kept cells are history in
    every member and quantile (their true times — the fire-shading record
    agrees), p10 never trails the nominal, and no serviced stop's audit row
    is a violation."""
    s = _session(tmp_path, cell_m=500.0, hamlet_half=2, n_clients=6,
                 n_crews=2)
    fid = _ignite(s, dist_m=6000.0)
    script = {3: ("wind", 0.0), 6: ("wind", 0.0), 8: ("ignite", None),
              11: ("wind", 300.0)}
    prev = set()
    n_reanchor = 0
    for k in range(1, 15):
        cmd = script.get(k)
        if cmd and cmd[0] == "wind":
            s.set_wind(cmd[1])
        elif cmd:
            _ignite(s, bearing=0.0, dist_m=6000.0, wind=None)
        before = {i: s._fires[i].ens for i in s._fire_order
                  if s._fires[i].ens is not None}
        now = s._now_s
        st = s.tick()
        cells = _rle_cells(st["terrain"]["burned"])
        assert prev <= cells, f"tick {k}: the burned mask shrank"
        prev = cells
        assert all(a["violation"] is False for a in st["audit"]), st["audit"]
        for iid, old in before.items():
            f = s._fires[iid]
            if f.ens is old:
                continue
            n_reanchor += 1
            assert f.anchor_t0_s == now
            kept = {i: v for i, v in enumerate(old.members[0].data)
                    if v <= now}
            for r in f.ens.members + [f.ens.p10, f.ens.p50, f.ens.p90]:
                got = {i: v for i, v in enumerate(r.data) if v <= now}
                assert got == kept, (k, iid, "mask/times moved at the anchor")
            assert all(p <= n for p, n in zip(f.ens.p10.data,
                                              f.ens.members[0].data))
            rec = f._fv_burn_t or {}
            assert all(f.ens.members[0].data[i] == t for i, t in rec.items())
    assert n_reanchor >= 4, "every scripted world change must re-anchor"
    assert fid in s._fires
