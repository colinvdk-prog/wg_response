"""The per-fire spread-rate belief (contracts SAFETY SEMANTICS 14;
wgresponse.fire_belief; docs/FIRE_BELIEF.md).

Byte-identity with every knob OFF — pinned against goldens computed on the
a8bebec engine BEFORE any fire-belief code existed (tests/fire_belief_stack
.py; scratch_review/fire_belief_golden.py) — and tracking changes no
arrival / ETA / alert / dispatch; the per-hour spread seam (exact memo,
legacy-identical when absent); weather at the fire drives spread (item 1);
observations (newer perimeters, changed / long-unchanged acreage, spacing,
drills never observed); the radial-rate likelihood (censoring, transient,
time decay, overlap); the posterior recovering a hidden multiplier; the
surprise reset; rapid / known; the new-weather-pattern rule; the apply path
(member multipliers, prior whenever it must, rapid = larger of prior and
posterior); persistence (own file, S3 mirror, drill copies never written);
the CLI wiring; the world summary / state / page surfaces; config bounds.

Deterministic; no network.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fire_belief_stack as fbs  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import cli, geo, spread  # noqa: E402
from wgresponse import fire_belief as fb  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    ClientProperty,
    FireIncident,
    FireStatus,
    GridSpec,
    IncidentSource,
    LatLon,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
    validate_config,
)
from wgresponse.threat import ThreatMonitor  # noqa: E402

H = 3600.0
T0 = ss.T0

#: PRE-BELIEF GOLDENS: tests/fire_belief_stack.belief_baseline_run() digests
#: computed with scratch_review/fire_belief_golden.py on the a8bebec engine
#: (the W6 merge), BEFORE any fire-belief code existed — 3 cycles of a
#: perimeter fire whose polygon and acreage change + a point fire whose
#: acreage changes, 3 clients, 1 crew, 1 safe zone, model error 0.71 + p50
#: bound, through the REAL monitor / ladder / planner / StateStore /
#: _WatchDaemon / worldview. Every fire-belief knob OFF must reproduce them.
_GOLDEN_CYCLES = [
    "491c5d4954ec0d398ee06987cb653234a7b529c45f087bcfacacbaf0aaa67d15",
    "09a3c26673aaf7e6d4fb31bef41a6ac0c4e8e0818101d4b1dcfbec505a949e50",
    "0a89c6d5d81f7702f2642f8c56cc7037a89ec5ae967575ded758854bae231e1c",
]
_GOLDEN_API = "6a72a7d7c47e93241329b4f92659c2a58b61ccb5d9a88ae6da648e43b0f27e25"
_GOLDEN_STATE = "1199a52b30ccd29923664d773eb94101ed223ac08b053f837283276d711bf09b"
_GOLDEN_WORLD = "b4626876bdd1dc063d3f1d626a52affe24fe71fdf216bb55dc696efa2475013d"


def bcfg(**kw) -> ResponseConfig:
    """A belief config on the default engine (knobs as given)."""
    base = dict(fire_belief_track=True)
    base.update(kw)
    return ResponseConfig(**base)


# ---------------------------------------------------------------------------
# 1. byte identity (the contracts law)
# ---------------------------------------------------------------------------

def test_all_knobs_off_reproduce_the_a8bebec_goldens(tmp_path):
    cycles, api, state, world = fbs.belief_baseline_run(tmp_path / "state")
    assert cycles == _GOLDEN_CYCLES          # every CycleResult decision
    assert api == _GOLDEN_API                # the /api/state payload
    assert state == _GOLDEN_STATE            # the persisted state bytes
    assert world == _GOLDEN_WORLD            # the /api/world/summary payload
    assert sorted(os.listdir(tmp_path / "state")) == ["wgresponse_state.json"]


def test_knobs_off_build_no_store_and_publish_nothing(tmp_path):
    daemon, monitor, _feed, _eng, _store = fbs.make_stack(tmp_path / "s")
    res = daemon.run_cycle()
    assert monitor.fire_beliefs is None
    assert res.fire_status == {}
    assert "fire_status" not in daemon.state()
    assert all("belief" not in r for r in daemon.world_summary()["incidents"])
    assert cli._fire_belief_file(fbs.belief_cfg(), tmp_path / "s") is None


def test_tracking_changes_no_arrival_eta_alert_or_dispatch(tmp_path):
    keep = []
    cycles, api, state, world = fbs.belief_baseline_run(
        tmp_path / "state", cfg=fbs.belief_cfg(fire_belief_track=True),
        keep=keep)
    # incidents, assessments (every ETA float), alert events, every ensemble
    # raster, notes, operator events and the dispatch plan: all identical
    assert cycles == _GOLDEN_CYCLES
    assert state == _GOLDEN_STATE            # the alert-state file untouched
    assert api != _GOLDEN_API and world != _GOLDEN_WORLD   # status published
    for res in keep:
        assert set(res.fire_status) == {"perim-1", "point-1"}
        for st in res.fire_status.values():
            assert isinstance(st, FireStatus)
    last = keep[-1].fire_status["perim-1"]
    assert last.known and last.state == "learned" and last.n_obs == 1
    assert last.mult_p10 < last.mult_p50 < last.mult_p90


def test_apply_keeps_physics_for_a_rapid_fire(tmp_path):
    """The pinned scenario's perimeter fire grows 259 ac in 3.2 h (1,956
    ac/24 h — RAPID) while its model runs faster still (learned 0.30x): the
    applied members take the LARGER of prior and posterior at each quantile,
    which is exactly physics — every byte of the golden cycles stands."""
    keep = []
    cycles, *_ = fbs.belief_baseline_run(
        tmp_path / "b", cfg=fbs.belief_cfg(fire_belief_track=True,
                                            fire_belief_apply=True),
        keep=keep)
    st = keep[2].fire_status["perim-1"]
    assert st.state == "learned" and st.rapid and st.mult_p50 < 0.5
    assert cycles == _GOLDEN_CYCLES


def _slow_perim(k):
    """perim_incident with a SLOW second polygon (32 ac in 3.2 h: not rapid)."""
    inc_k = fbs.perim_incident(k)
    if k == 2:
        inc_k = dataclasses.replace(
            inc_k, perimeter=[fbs.square_ring(fbs.PERIM_CENTER, 820.0)],
            acreage=700.0)
    return inc_k


def _run_slow(state_dir, cfg):
    daemon, monitor, feed, _e, _s = fbs.make_stack(state_dir, cfg=cfg)
    out = []
    for k in range(3):
        feed.incidents = [_slow_perim(k), fbs.point_incident(k)]
        daemon._clock.t = T0 + 60.0 + k * fbs.PERIOD_S
        out.append(daemon.run_cycle())
    return out


def test_apply_changes_nothing_until_evidence_then_only_that_fire(tmp_path):
    off = _run_slow(tmp_path / "a", fbs.belief_cfg(fire_belief_track=True))
    on = _run_slow(tmp_path / "b", fbs.belief_cfg(fire_belief_track=True,
                                                   fire_belief_apply=True))
    # no completed window yet: the prior = the exact legacy member calls
    assert [ss.cycle_digest(r) for r in on[:2]] == \
        [ss.cycle_digest(r) for r in off[:2]]
    st = on[2].fire_status["perim-1"]
    assert st.state == "learned" and not st.rapid and st.mult_p50 < 1.0
    fp = ss.raster_fingerprint
    # the point fire has no evidence: byte-identical ensemble
    assert [fp(m) for m in on[2].ensembles["point-1"].members] == \
        [fp(m) for m in off[2].ensembles["point-1"].members]
    e_off, e_on = off[2].ensembles["perim-1"], on[2].ensembles["perim-1"]
    # the learned-slow fire's nominal and ladder median move later ...
    assert all(b >= a for a, b in zip(e_off.members[0].data,
                                      e_on.members[0].data))
    assert any(b > a for a, b in zip(e_off.p50.data, e_on.p50.data))
    # ... but the P10 GUARD holds crew safety: never later than physics
    assert all(b <= a for a, b in zip(e_off.p10.data, e_on.p10.data))
    assert all(a <= b <= c for a, b, c in zip(e_on.p10.data, e_on.p50.data,
                                              e_on.p90.data))


def test_apply_p10_lets_the_belief_move_p10_later(tmp_path):
    off = _run_slow(tmp_path / "a", fbs.belief_cfg(fire_belief_track=True))
    on = _run_slow(tmp_path / "b", fbs.belief_cfg(
        fire_belief_track=True, fire_belief_apply=True,
        fire_belief_apply_p10=True))
    p_off = off[2].ensembles["perim-1"].p10.data
    p_on = on[2].ensembles["perim-1"].p10.data
    assert all(b >= a for a, b in zip(p_off, p_on))    # never earlier
    assert any(b > a for a, b in zip(p_off, p_on))     # later somewhere


# ---------------------------------------------------------------------------
# 2. the per-hour spread seam
# ---------------------------------------------------------------------------

def grass_tile(n=41, cell_m=100.0, fuel=102.0) -> TerrainTile:
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=cell_m,
                    nrows=n, ncols=n)
    return TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=fuel),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0))


def wx_series(loc, dirs=None, wind=8.0, hours=60, t0=T0):
    dirs = dirs or (lambda i: 270.0)
    return WeatherForecast(loc=loc, samples=[
        WeatherSample(t_s=t0 + i * H, wind_speed_mps=wind,
                      wind_gust_mps=wind * 1.3, wind_dir_deg=dirs(i),
                      rh_pct=20.0, temp_c=30.0) for i in range(hours)],
        source="test")


def _center(tile):
    return geo.rc_to_latlon(tile.spec, tile.spec.nrows // 2,
                            tile.spec.ncols // 2)


def test_hour_ros_scale_absent_or_all_ones_is_byte_identical():
    tile = grass_tile()
    ign = _center(tile)
    wx = wx_series(ign, dirs=lambda i: 270.0 + 10.0 * (i % 3))
    cfg = ResponseConfig(horizon_h=24.0)
    base = spread.compute_arrival(tile, [ign], wx, T0, cfg)
    none = spread.compute_arrival(tile, [ign], wx, T0, cfg, hour_ros_scale=None)
    ones = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  hour_ros_scale=[1.0] * 25)
    assert base.data == none.data == ones.data


def test_constant_hour_scale_equals_the_member_ros_scale():
    tile = grass_tile()
    ign = _center(tile)
    wx = wx_series(ign)
    cfg = ResponseConfig(horizon_h=24.0)
    a = spread.compute_arrival(tile, [ign], wx, T0, cfg, ros_scale=0.37)
    b = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                               hour_ros_scale=[0.37] * 25)
    assert a.data == b.data                  # one identical multiply


def test_hour_scale_memo_is_exact(monkeypatch):
    tile = grass_tile()
    ign = _center(tile)
    wx = wx_series(ign, dirs=lambda i: 250.0 + 7.0 * (i % 4))
    cfg = ResponseConfig(horizon_h=24.0, edge_weather_piecewise=True)
    scale = [1.0, 0.8, 0.5, 0.5, 2.0] * 5
    memo = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  hour_ros_scale=scale)
    monkeypatch.setenv("WGRESP_NO_ROS_MEMO", "1")
    ref = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                 hour_ros_scale=scale)
    assert memo.data == ref.data


def test_hour_scale_moves_only_hours_after_it_changes():
    tile = grass_tile(n=61)
    ign = _center(tile)
    wx = wx_series(ign)
    cfg = ResponseConfig(horizon_h=30.0)
    base = spread.compute_arrival(tile, [ign], wx, T0, cfg).data
    slow = spread.compute_arrival(tile, [ign], wx, T0, cfg,
                                  hour_ros_scale=[1.0] * 3 + [0.25] * 30).data
    for a, b in zip(base, slow):
        if a < T0 + 2.0 * H:
            assert a == b            # settled under the unchanged hours
        assert b >= a                # never earlier
    assert sum(1 for a, b in zip(base, slow) if b > a) > 10


def test_hour_scale_rejects_bad_values():
    tile = grass_tile(n=11)
    ign = _center(tile)
    for bad in ([], [0.0], [1.0, -1.0], [float("nan")]):
        with pytest.raises(ValueError):
            spread.compute_arrival(tile, [ign], wx_series(ign), T0,
                                   ResponseConfig(horizon_h=4.0),
                                   hour_ros_scale=bad)


def test_member_hour_scale_seam_is_legacy_when_it_returns_none_or_legacy():
    tile = grass_tile()
    ign = _center(tile)
    wx = wx_series(ign)
    cfg = ResponseConfig(horizon_h=24.0, ensemble_n=5, model_error_sd_log=0.71)
    base = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="x")
    seen = []

    def none_fn(k, q, legacy):
        seen.append((k, q, legacy))
        return None

    def legacy_fn(k, q, legacy):
        return [legacy] * 25

    a = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="x",
                                member_hour_scale=none_fn)
    b = spread.ensemble_arrival(tile, [ign], wx, T0, cfg, incident_id="x",
                                member_hour_scale=legacy_fn)
    for e in (a, b):
        assert [m.data for m in e.members] == [m.data for m in base.members]
        assert e.p10.data == base.p10.data and e.p50.data == base.p50.data
    # the seam hands each member its model-error stratum and legacy scale
    assert seen[0] == (0, 0.5, 1.0)
    from statistics import NormalDist
    for k, q, legacy in seen[1:]:
        assert 0.0 < q < 1.0
        assert legacy == pytest.approx(math.exp(0.71 * NormalDist().inv_cdf(q)))


# ---------------------------------------------------------------------------
# 3. item 1: the local forecast drives spread (pinned at the monitor)
# ---------------------------------------------------------------------------

class ShiftWeather:
    """West wind (blows east); from hour `shift_h` the wind comes from the
    SOUTH (blows north). shift_h=None = constant."""

    def __init__(self, shift_h=None):
        self.shift_h = shift_h

    def forecast(self, loc, horizon_h):
        return wx_series(loc, hours=int(horizon_h) + 2, dirs=lambda i: (
            180.0 if self.shift_h is not None and i >= self.shift_h
            else 270.0))


def test_mid_horizon_wind_shift_changes_downstream_arrivals():
    """Item 1 ("fire spread includes local weather forecast"): the monitor
    prices every edge with the fire-centroid forecast hour by hour, so a
    wind shift half-way through the horizon moves the arrivals it steers.
    (The spread-level pin is tests/test_spread.py::
    test_wind_switch_bends_fast_axis, a shift at +2 h.)"""
    origin = LatLon(39.0, -121.0)
    north = ClientProperty(client_id="n", loc=geo.destination(origin, 0.0, 7000.0))
    east = ClientProperty(client_id="e", loc=geo.destination(origin, 90.0, 4000.0))
    inc = FireIncident(incident_id="f", name="F", source=IncidentSource.MOCK,
                       origin=origin, updated_at_s=T0 - 60.0, active=True)
    cfg = ss.small_cfg(horizon_h=24.0)

    def run(shift):
        mon = ThreatMonitor(feeds=[ss.ScriptedFeed([inc])],
                            weather_provider=ShiftWeather(shift),
                            terrain_provider=ss.StubTerrain(),
                            registry=ss.StubRegistry([north, east]),
                            alert_engine=AlertEngine(cfg), cfg=cfg,
                            safe_zones=[])
        res = mon.cycle(T0)
        return {a.client_id: a.eta for a in res.assessments}

    const, shifted = run(None), run(12)
    # the shift at +12 h pushes the fire north: the northern client's
    # arrivals come earlier (it was a flank client under the west wind)
    assert shifted["n"].eta_p50_s < const["n"].eta_p50_s
    assert shifted["n"].eta_p10_s < const["n"].eta_p10_s


# ---------------------------------------------------------------------------
# 4. observations
# ---------------------------------------------------------------------------

def square(center, half_m):
    return fbs.square_ring(center, half_m)


def inc(iid="f1", perimeter=None, perimeter_at_s=None, acreage=None,
        updated_at_s=T0, meta=None, discovered=None, source=IncidentSource.MOCK):
    m = dict(meta or {})
    if perimeter_at_s is not None:
        m["perimeter_at_s"] = perimeter_at_s
    return FireIncident(incident_id=iid, name=iid, source=source,
                        origin=LatLon(39.0, -121.0), updated_at_s=updated_at_s,
                        discovered_at_s=discovered, acreage=acreage,
                        active=True, perimeter=perimeter, meta=m)


C = LatLon(39.0, -121.0)


def test_perimeter_observation_needs_a_newer_own_time():
    st = fb.FireBeliefStore()
    cfg = bcfg()
    st.observe([inc(perimeter=[square(C, 1000.0)], perimeter_at_s=T0)], T0, cfg)
    st.observe([inc(perimeter=[square(C, 1200.0)], perimeter_at_s=T0)],
               T0 + 4 * H, cfg)          # same own time: not a new polygon
    b = st.belief("f1")
    assert len(b.obs["perimeter"]) == 1 and not b.windows
    st.observe([inc(perimeter=[square(C, 1200.0)], perimeter_at_s=T0 + 6 * H)],
               T0 + 7 * H, cfg)
    assert len(b.obs["perimeter"]) == 2 and len(b.windows) == 1
    w = b.windows[0]
    assert w["kind"] == "perimeter" and w["tb"] - w["ta"] == 6 * H
    assert w["ab"] == pytest.approx(4 * 1200.0 ** 2, rel=0.01)
    assert w["vnom"] is None             # no curve was pinned: unmodeled


def test_min_spacing_skips_the_closer_observation():
    st = fb.FireBeliefStore()
    cfg = bcfg()
    for k, t in enumerate((0.0, 1.0, 2.0, 3.5)):
        st.observe([inc(perimeter=[square(C, 1000.0 + 100 * k)],
                        perimeter_at_s=T0 + t * H)], T0 + t * H, cfg)
    b = st.belief("f1")
    assert [o["t"] for o in b.obs["perimeter"]] == [T0, T0 + 3.5 * H]
    assert len(b.windows) == 1


def test_reported_acreage_changes_and_the_static_rule():
    st = fb.FireBeliefStore()
    cfg = bcfg()
    st.observe([inc(acreage=500.0, updated_at_s=T0)], T0, cfg)
    st.observe([inc(acreage=600.0, updated_at_s=T0 + 5 * H)], T0 + 5 * H, cfg)
    b = st.belief("f1")
    assert len(b.windows) == 1 and not b.windows[0]["static"]
    # unchanged while the record keeps being modified: nothing for 24 h
    for h in (10, 20, 28):
        st.observe([inc(acreage=600.0, updated_at_s=T0 + h * H)],
                   T0 + h * H, cfg)
    assert len(b.windows) == 1
    st.observe([inc(acreage=600.0, updated_at_s=T0 + 29.5 * H)],
               T0 + 29.5 * H, cfg)
    assert len(b.windows) == 2
    w = b.windows[1]
    assert w["static"] and w["ab"] == w["aa"] and w["tb"] == T0 + 29.5 * H
    # an unmodified record never becomes zero-growth evidence
    st2 = fb.FireBeliefStore()
    st2.observe([inc(acreage=600.0, updated_at_s=T0)], T0, cfg)
    for h in (10, 30, 60):
        st2.observe([inc(acreage=600.0, updated_at_s=T0)], T0 + h * H, cfg)
    assert not st2.belief("f1").windows


def test_static_observations_carry_inflated_error():
    cfg = bcfg()
    a = fb._area_sd(1000.0 * fb.ACRE_M2, cfg, False)
    b = fb._area_sd(1000.0 * fb.ACRE_M2, cfg, True)
    assert b == pytest.approx(fb.STATIC_ERR_INFLATION * a)


def test_meta_keys_take_precedence_when_present():
    st = fb.FireBeliefStore()
    cfg = bcfg()
    meta0 = {"reported_acres": 900.0, "reported_at_s": T0 - 2 * H,
             "perimeter_acres": 800.0}
    st.observe([inc(acreage=10.0, meta=meta0, perimeter=[square(C, 900.0)],
                    perimeter_at_s=T0 - 3 * H)], T0, cfg)
    meta1 = {"reported_acres": 1200.0, "reported_at_s": T0 + 6 * H,
             "perimeter_acres": 1100.0}
    st.observe([inc(acreage=10.0, meta=meta1, perimeter=[square(C, 950.0)],
                    perimeter_at_s=T0 + 5 * H)], T0 + 7 * H, cfg)
    b = st.belief("f1")
    rep = [w for w in b.windows if w["kind"] == "reported"][0]
    per = [w for w in b.windows if w["kind"] == "perimeter"][0]
    assert rep["aa"] == pytest.approx(900.0 * fb.ACRE_M2)
    assert rep["tb"] == T0 + 6 * H
    # the polygon's own acres on both ends win over the ring area
    assert per["aa"] == pytest.approx(800.0 * fb.ACRE_M2)
    assert per["ab"] == pytest.approx(1100.0 * fb.ACRE_M2)


def test_seeded_drills_are_never_observed():
    st = fb.FireBeliefStore()
    seed = inc(iid="seeded:abc", acreage=500.0, source=IncidentSource.SEEDED)
    st.observe([seed], T0, bcfg())
    assert st.belief("seeded:abc") is None and len(st) == 0
    s = st.statuses([seed], T0, bcfg())["seeded:abc"]
    assert not s.known and not s.rapid and s.state == "prior"
    assert "drill" in s.reasons[0]


def test_ring_area_is_even_odd_with_holes():
    outer = [geo.destination(C, b, 2000.0) for b in range(0, 360, 2)]
    hole = [geo.destination(C, b, 500.0) for b in range(0, 360, 2)]
    far = [geo.destination(geo.destination(C, 90.0, 9000.0), b, 1000.0)
           for b in range(0, 360, 2)]
    a = fb.rings_area_m2([outer, hole, far])
    want = math.pi * (2000.0 ** 2 - 500.0 ** 2 + 1000.0 ** 2)
    assert a == pytest.approx(want, rel=0.003)


# ---------------------------------------------------------------------------
# 5. likelihood + posterior
# ---------------------------------------------------------------------------

def curve(v_mph=100.0, r0=2000.0, hours=48, anchor=T0, point=False,
          sat=None, mult=None):
    """A hand-built growth curve whose equivalent radius grows v_mph m/h."""
    g = [math.pi * (r0 + v_mph * s) ** 2 for s in range(hours + 1)]
    return {"anchor": anchor, "t0": anchor, "g": g, "sat": sat,
            "point": point, "mult": mult}


def test_model_rate_uncensored_censored_transient_and_spent():
    c = curve(v_mph=100.0)
    v, cens, m = fb.model_rate(c, T0 + 2 * H, T0 + 14 * H)
    assert v == pytest.approx(100.0) and not cens and m == 1.0
    # past the horizon: a LOWER bound over the full window (censored)
    v, cens, _ = fb.model_rate(c, T0 + 40 * H, T0 + 56 * H)
    assert cens and v == pytest.approx(100.0 * 8 / 16)
    # the domain edge inside the window censors it too
    v, cens, _ = fb.model_rate(curve(sat=6 * H), T0, T0 + 12 * H)
    assert cens and v == pytest.approx(50.0)
    # a curve spent before the window starts models nothing
    assert fb.model_rate(curve(sat=H), T0 + 3 * H, T0 + 9 * H) is None
    # point starts skip the first 2 h
    pt = curve(v_mph=100.0, r0=0.0, point=True)
    pt["g"][1] = 0.0
    v, cens, _ = fb.model_rate(pt, T0, T0 + 10 * H)
    assert v == pytest.approx(100.0) and not cens
    # the nominal's own multiplier is averaged over the window
    v, cens, m = fb.model_rate(curve(mult=[0.5] * 6 + [1.0] * 50),
                               T0, T0 + 12 * H)
    assert m == pytest.approx(0.75)


def window(vobs, vnom=100.0, sdv=5.0, t_end=T0, dt_h=12.0, kind="perimeter",
           cens=False, mnom=1.0, r0=2000.0):
    aa = math.pi * r0 ** 2
    ab = math.pi * (r0 + vobs * dt_h) ** 2
    return {"kind": kind, "ta": t_end - dt_h * H, "tb": t_end, "aa": aa,
            "ab": ab, "static": False, "vobs": vobs, "sdv": sdv,
            "vnom": vnom, "cens": cens, "mnom": mnom, "pred50": None,
            "pred90": None, "surprise": False}


def post_of(wins, now=T0, cfg=None, **bel_kw):
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", now - 100 * H)
    b.windows = list(wins)
    for k, v in bel_kw.items():
        setattr(b, k, v)
    st._beliefs["x"] = b
    return st.posterior("x", now, cfg or bcfg())


@pytest.mark.parametrize("m_true", [0.1, 0.3, 1.0, 2.0])
def test_posterior_recovers_a_hidden_multiplier(m_true):
    # sdv 1 m/h: a ~3 km-radius fire's area error over a 12 h window is
    # ~3.5 m/h; at that noise a 0.1x fire reads 0.125x (the prior's pull)
    wins = [window(vobs=m_true * 100.0, sdv=1.0, t_end=T0 - k * 12 * H)
            for k in range(4)]
    post = post_of(wins)
    assert post.mult(0.5) == pytest.approx(m_true, rel=0.2)
    assert post.mult(0.1) < post.mult(0.5) < post.mult(0.9)


def test_one_window_learns_less_than_four():
    one = post_of([window(vobs=30.0)])
    four = post_of([window(vobs=30.0, t_end=T0 - k * 12 * H) for k in range(4)])
    spread1 = math.log(one.mult(0.9) / one.mult(0.1))
    spread4 = math.log(four.mult(0.9) / four.mult(0.1))
    assert spread4 < spread1 < 2 * 1.2816 * 0.71     # all narrower than prior


def test_censored_window_can_only_slow_the_belief():
    # model lower bound far below a fast observation: no information at all
    # (every multiplier's model could match it) -> the posterior IS the prior
    fast = post_of([window(vobs=500.0, vnom=50.0, cens=True)])
    assert fast.mult(0.5) == pytest.approx(1.0, abs=0.02)
    # zero growth against a big lower bound: as strong as an uncensored
    # window wherever the bound alone already outruns the observation
    one_c = post_of([window(vobs=0.0, vnom=200.0, cens=True)])
    one_u = post_of([window(vobs=0.0, vnom=200.0, cens=False)])
    assert one_c.mult(0.5) == pytest.approx(one_u.mult(0.5), rel=0.02)
    assert one_c.mult(0.5) < 0.7
    three = post_of([window(vobs=0.0, vnom=200.0, cens=True,
                            t_end=T0 - k * 12 * H) for k in range(3)])
    assert three.mult(0.5) < 0.2


def test_old_evidence_decays():
    fresh = post_of([window(vobs=20.0, t_end=T0)])
    stale = post_of([window(vobs=20.0, t_end=T0 - 10 * 72 * H)])
    assert fresh.mult(0.5) < 0.4
    assert stale is None or stale.mult(0.5) == pytest.approx(1.0, abs=0.05)


def test_overlapping_series_are_not_double_counted():
    cfg = bcfg()
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", T0 - 50 * H)
    b.windows = [window(vobs=20.0, kind="perimeter"),
                 window(vobs=20.0, kind="reported")]
    st._beliefs["x"] = b
    ev = st._evidence(b, T0, cfg)
    assert [wt for _w, wt in ev] == pytest.approx([0.5, 0.5])


def test_surprise_reset_is_sticky_drops_old_evidence_and_is_rapid():
    cfg = bcfg()
    st = fb.FireBeliefStore()
    # learn "slow" from three perimeters with pinned curves (model 100 m/h,
    # fire 20 m/h), then the fire runs at 150 m/h
    r = 2000.0
    t = T0
    for k, v in enumerate((None, 20.0, 20.0, 20.0, 150.0)):
        if v is not None:
            r += v * 12.0
        t_obs = T0 + k * 12 * H
        st.observe([inc(perimeter=[_circle(r)], perimeter_at_s=t_obs)],
                   t_obs + 60.0, cfg)
        b = st.belief("f1")
        b.obs["perimeter"][-1]["curve"] = curve(v_mph=100.0, r0=r,
                                                anchor=t_obs)
        t = t_obs + 60.0
        if k == 3:
            s_before = st.statuses([inc()], t, cfg)["f1"]
    assert s_before.state == "learned" and s_before.mult_p50 < 0.4
    s = st.statuses([inc()], t, cfg)["f1"]
    assert s.state == "reset" and s.rapid and s.mult_p50 is None
    assert any("surprise reset" in x for x in s.reasons)
    b = st.belief("f1")
    assert b.windows[-1]["surprise"] and b.floor_s == b.windows[-1]["ta"]
    # sticky for fire_belief_reset_h, then only the surprising window counts
    s24 = st.statuses([inc()], t + 23 * H, cfg)["f1"]
    assert s24.state == "reset"
    after = st.statuses([inc()], t + 25 * H, cfg)["f1"]
    assert after.state == "learned" and after.n_obs == 1
    assert after.mult_p50 > 1.0          # the fast window pulls it UP


def _circle(r_m):
    return [geo.destination(C, b, r_m) for b in range(0, 360, 3)]


def test_a_point_fire_learns_from_acreage_through_the_monitor():
    """A POINT incident (no perimeter) learns its rate from reported acreage
    alone: the curve from the point (first 2 h skipped) is pinned at the
    cycle that saw each size, the radial rates compare, and a later jump
    beyond the belief's P90 is a note + OperatorEvent('fire_belief')."""
    cfg = ss.small_cfg(horizon_h=24.0, fire_belief_track=True)
    origin = ss.ORIGIN

    def point(acres, t):
        return FireIncident(incident_id="pt", name="Pt",
                            source=IncidentSource.MOCK, origin=origin,
                            updated_at_s=t - 60.0, acreage=acres, active=True,
                            discovered_at_s=T0 - 30 * 24 * H)

    feed = ss.ScriptedFeed([])
    mon = ThreatMonitor(feeds=[feed], weather_provider=ss.StubWeather(wind_mps=4.0),
                        terrain_provider=ss.StubTerrain(),
                        registry=ss.StubRegistry(ss.clients()),
                        alert_engine=AlertEngine(cfg), cfg=cfg, safe_zones=[])
    # a slow fire: +5 ac per 4 h on ~500 ac, while 4 m/s grass runs far faster
    res = None
    for k, acres in enumerate((500.0, 505.0, 510.0, 515.0)):
        t = T0 + k * 4 * H
        feed.incidents = [point(acres, t)]
        res = mon.cycle(t)
    st = res.fire_status["pt"]
    assert st.state == "learned" and st.known and not st.rapid
    assert st.n_obs == 3 and st.mult_p50 < 0.5
    assert st.last_obs.kind == "reported"
    assert st.last_obs.predicted_growth_acres is not None
    # then it jumps: 515 -> 2,500 ac in 4 h
    t = T0 + 16 * H
    feed.incidents = [point(2500.0, t)]
    res = mon.cycle(t)
    st = res.fire_status["pt"]
    assert st.state == "reset" and st.rapid and st.mult_p50 is None
    ev = [e for e in res.operator_events if e.kind == "fire_belief"]
    assert len(ev) == 1 and ev[0].incident_id == "pt"
    assert any(n.startswith("fire belief pt: RESET to physics") for n in res.notes)
    assert not res.degraded                  # a belief event is not a failure


# ---------------------------------------------------------------------------
# 6. rapid + known
# ---------------------------------------------------------------------------

def test_rapid_thresholds_and_lookback():
    cfg = bcfg()
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", T0 - 100 * H)
    st._beliefs["x"] = b
    ac = fb.ACRE_M2

    def w(a0, a1, tb, dt_h=12.0):
        return {"kind": "reported", "ta": tb - dt_h * H, "tb": tb,
                "aa": a0 * ac, "ab": a1 * ac, "vobs": 0.0, "sdv": 1.0,
                "vnom": None, "static": False}

    b.windows = [w(1000, 1400, T0)]          # 800 ac/24 h but 80%/24 h
    assert st._rapid(b, T0, cfg)[0]
    b.windows = [w(20000, 20400, T0)]        # 800 ac/24 h, 4%/24 h
    assert not st._rapid(b, T0, cfg)[0]
    b.windows = [w(20000, 22500, T0)]        # 2,500 ac in 12 h: beyond noise
    assert st._rapid(b, T0, cfg)[0]
    b.windows = [w(300, 380, T0)]            # 53%/24 h but only 80 ac
    assert not st._rapid(b, T0, cfg)[0]
    b.windows = [w(20000, 22500, T0 - 49 * H)]   # outside the lookback
    assert not st._rapid(b, T0, cfg)[0]
    b.windows = []
    b.discovered_s, b.latest_acres = T0 - 6 * H, 400.0   # 1600 ac/24 h
    assert st._rapid(b, T0, cfg)[0]
    b.discovered_s = T0 - 60 * H                          # not young
    assert not st._rapid(b, T0, cfg)[0]


def test_rapid_is_judged_beyond_measurement_noise():
    """3 % of 20,000 ac is +-600 ac per report: +600 ac in 12 h (1,200
    ac/24 h raw) is inside the noise of two reports — not rapid at the
    default z, rapid on the raw rule (z = 0)."""
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", T0 - 100 * H)
    st._beliefs["x"] = b
    ac = fb.ACRE_M2
    b.windows = [{"kind": "reported", "ta": T0 - 12 * H, "tb": T0,
                  "aa": 20000 * ac, "ab": 20600 * ac, "vobs": 0.0,
                  "sdv": 1.0, "vnom": None, "static": False}]
    assert not st._rapid(b, T0, bcfg())[0]
    rapid, why = st._rapid(b, T0, bcfg(fire_belief_rapid_noise_z=0.0))
    assert rapid and "1,200 ac/24 h" in why[0]


def test_known_needs_a_completed_window():
    st = fb.FireBeliefStore()
    cfg = bcfg()
    st.observe([inc(acreage=100.0, updated_at_s=T0)], T0, cfg)
    assert not st.statuses([inc()], T0, cfg)["f1"].known
    st.observe([inc(acreage=110.0, updated_at_s=T0 + 4 * H)], T0 + 4 * H, cfg)
    s = st.statuses([inc()], T0 + 4 * H, cfg)["f1"]
    assert s.known and s.state == "prior"    # unmodeled: nothing to learn
    assert s.last_obs.growth_acres == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 7. the new-weather-pattern rule
# ---------------------------------------------------------------------------

def _learned_store(now, wx_rows, cfg):
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", now - 100 * H)
    b.windows = [window(vobs=30.0, t_end=now - 1 * H, dt_h=24.0)]
    b.fuel_code = 102
    b.wx_log = wx_rows
    st._beliefs["x"] = b
    return st, b


def _rows(now, n=24, ws=4.0, wdir=270.0, rh=30.0, tc=25.0):
    t0 = math.floor(now / H) * H
    return [[t0 - (n - i) * H, ws, wdir, rh, tc] for i in range(n)]


def _wx(now, fn, hours=100):
    t0 = math.floor(now / H) * H
    return WeatherForecast(loc=C, samples=[
        WeatherSample(t0 + i * H, *fn(i)) for i in range(hours)])


def test_same_weather_stays_learned_through_the_horizon():
    cfg = bcfg(horizon_h=48.0)
    st, b = _learned_store(T0, _rows(T0), cfg)
    wx = _wx(T0, lambda i: (4.0, 5.0, 270.0, 30.0, 25.0))
    h, why = st._novelty(b, st._evidence(b, T0, cfg), wx, T0, cfg)
    assert h == fb.horizon_hours(cfg) + 1 and why == ""


def test_forecast_wind_shift_is_a_new_pattern_from_that_hour():
    cfg = bcfg(horizon_h=48.0)
    st, b = _learned_store(T0, _rows(T0), cfg)
    wx = _wx(T0, lambda i: (4.0, 5.0, 270.0 if i < 10 else 180.0, 30.0, 25.0))
    h, why = st._novelty(b, st._evidence(b, T0, cfg), wx, T0, cfg)
    assert h == 10 and "turns 90 deg" in why
    # a turn at a wind below the minimum is not a pattern change
    wx = _wx(T0, lambda i: (4.0 if i < 10 else 2.0, 3.0,
                            270.0 if i < 10 else 180.0, 30.0, 25.0))
    assert st._novelty(b, st._evidence(b, T0, cfg), wx, T0, cfg)[0] == 49


def test_forecast_severity_jump_is_a_new_pattern():
    cfg = bcfg(horizon_h=48.0)
    st, b = _learned_store(T0, _rows(T0), cfg)
    wx = _wx(T0, lambda i: (4.0 if i < 20 else 11.0, 12.0, 270.0,
                            30.0 if i < 20 else 10.0, 25.0))
    h, why = st._novelty(b, st._evidence(b, T0, cfg), wx, T0, cfg)
    assert h == 20 and "severity" in why


def test_no_evidence_weather_means_physics_from_hour_zero():
    cfg = bcfg(horizon_h=48.0)
    st, b = _learned_store(T0, [], cfg)
    wx = _wx(T0, lambda i: (4.0, 5.0, 270.0, 30.0, 25.0))
    h, why = st._novelty(b, st._evidence(b, T0, cfg), wx, T0, cfg)
    assert h == 0 and "no logged weather" in why


# ---------------------------------------------------------------------------
# 8. applying the belief (the safety-law change)
# ---------------------------------------------------------------------------

def _post(median, sd):
    logp = [-0.5 * ((t - math.log(median)) / sd) ** 2 for t in fb.THETA]
    return fb.Posterior(logp)


def test_member_multipliers_quantiles_floor_rapid_and_new_hours():
    cfg = bcfg(horizon_h=10.0)
    post = _post(0.2, 0.05)
    fn = fb.member_scale_fn(post, h_new=4, H=10, cfg=cfg, rapid=False)
    nom = fn(0, 0.5, 1.0)
    assert len(nom) == 11
    assert nom[0] == pytest.approx(0.2, rel=0.03) and nom[4:] == [1.0] * 7
    fast = fn(5, 0.9, 2.48)
    # the posterior sd (0.05) is below the fast-tail floor (0.35)
    assert fast[0] == pytest.approx(0.2 * math.exp(0.35 * 1.2816), rel=0.03)
    assert fast[4:] == [2.48] * 7            # new pattern: the legacy member
    slow = fn(1, 0.1, 0.40)
    assert slow[0] == pytest.approx(0.2 * math.exp(-0.05 * 1.2816), rel=0.03)
    # never below the multiplier floor
    tiny = fb.member_scale_fn(_post(0.012, 0.05), 11, 10, cfg, False)
    assert min(tiny(1, 0.05, 0.3)) == pytest.approx(0.05)
    # rapid: the LARGER of prior and posterior at each quantile
    rap = fb.member_scale_fn(post, 11, 10, cfg, rapid=True)
    from statistics import NormalDist
    assert rap(0, 0.5, 1.0)[0] == pytest.approx(1.0)
    assert rap(5, 0.9, 2.48)[0] == pytest.approx(
        math.exp(0.71 * NormalDist().inv_cdf(0.9)), rel=1e-9)


def test_before_spread_returns_none_whenever_the_prior_applies():
    cfg = bcfg(fire_belief_apply=True, horizon_h=24.0)
    tile = grass_tile(n=11)
    now = T0
    wx = _wx(now, lambda i: (4.0, 5.0, 270.0, 30.0, 25.0))
    f = inc(iid="x")
    st, b = _learned_store(now, _rows(now), cfg)
    assert callable(st.before_spread(f, tile, wx, now, cfg))
    # track only: never a seam
    assert st.before_spread(f, tile, wx, now, dataclasses.replace(
        cfg, fire_belief_apply=False)) is None
    # a reset is physics
    b.reset_until_s = now + H
    assert st.before_spread(f, tile, wx, now, cfg) is None
    b.reset_until_s = None
    # hour 0 already new is physics everywhere
    wx_new = _wx(now, lambda i: (11.0, 12.0, 90.0, 10.0, 25.0))
    assert st.before_spread(f, tile, wx_new, now, cfg) is None
    # no evidence is physics
    b.windows = []
    assert st.before_spread(f, tile, wx, now, cfg) is None


# ---------------------------------------------------------------------------
# 9. persistence + drills
# ---------------------------------------------------------------------------

def _tracked_store(tmp_path):
    keep = []
    daemon, monitor, feed, _eng, _store = fbs.make_stack(
        tmp_path / "s", cfg=fbs.belief_cfg(fire_belief_track=True))
    for k in range(3):
        feed.incidents = [fbs.perim_incident(k), fbs.point_incident(k)]
        daemon._clock.t = T0 + 60.0 + k * fbs.PERIOD_S
        keep.append(daemon.run_cycle())
    return daemon, monitor, keep


def test_store_json_round_trip_and_copy_never_persists(tmp_path):
    _d, monitor, _k = _tracked_store(tmp_path)
    live = monitor.fire_beliefs
    d = live.to_json()
    back = fb.FireBeliefStore.from_json(json.loads(json.dumps(d)))
    assert back.to_json() == d and back.persistent
    cp = live.copy()
    assert not cp.persistent and cp.to_json() == d
    assert not fb.BeliefFile(tmp_path / "x").save(cp, now_s=T0)
    assert not (tmp_path / "x" / fb.BELIEF_FILENAME).exists()


def test_daemon_saves_and_restores_its_own_belief_file(tmp_path):
    cfg = fbs.belief_cfg(fire_belief_track=True)
    bf = cli._fire_belief_file(cfg, tmp_path / "s")
    daemon, monitor, feed, engine, store = fbs.make_stack(tmp_path / "s",
                                                          cfg=cfg)
    daemon._belief_file = bf
    for k in range(3):
        feed.incidents = [fbs.perim_incident(k), fbs.point_incident(k)]
        daemon._clock.t = T0 + 60.0 + k * fbs.PERIOD_S
        daemon.run_cycle()
    assert sorted(os.listdir(tmp_path / "s")) == [
        fb.BELIEF_FILENAME, "wgresponse_state.json"]
    saved = json.loads((tmp_path / "s" / fb.BELIEF_FILENAME).read_text("utf-8"))
    assert saved["schema"] == fb.SCHEMA and "perim-1" in saved["incidents"]
    # a restarted daemon restores the beliefs before its first cycle
    d2, m2, *_ = fbs.make_stack(tmp_path / "s", cfg=cfg)
    d2._belief_file = cli._fire_belief_file(cfg, tmp_path / "s")
    d2.restore()
    assert m2.fire_beliefs.to_json() == monitor.fire_beliefs.to_json()


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            err = Exception("missing")
            err.response = {"Error": {"Code": "NoSuchKey"}}
            raise err
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def test_belief_file_mirrors_to_s3_and_restores_from_it(tmp_path):
    _d, monitor, _k = _tracked_store(tmp_path)
    s3 = FakeS3()
    bf = fb.BeliefFile(tmp_path / "a", s3_uri="s3://bkt/wg/state",
                       s3_client=s3)
    assert bf.save(monitor.fire_beliefs, now_s=T0)
    assert ("bkt", "wg/state/fire_beliefs.json") in s3.objects
    fresh = fb.FireBeliefStore()
    assert fb.BeliefFile(tmp_path / "empty", s3_uri="s3://bkt/wg/state",
                         s3_client=s3).load_into(fresh)
    assert fresh.to_json() == monitor.fire_beliefs.to_json()


def test_corrupt_or_missing_belief_file_is_loud_and_starts_from_the_prior(
        tmp_path, caplog):
    fresh = fb.FireBeliefStore()
    assert not fb.BeliefFile(tmp_path).load_into(fresh)       # absent
    (tmp_path / fb.BELIEF_FILENAME).write_text("{not json", "utf-8")
    with caplog.at_level(logging.ERROR):
        assert not fb.BeliefFile(tmp_path).load_into(fresh)
    assert "restore FAILED" in caplog.text and len(fresh) == 0


def test_a_drill_world_copy_never_changes_the_live_belief(tmp_path):
    _d, live_monitor, _k = _tracked_store(tmp_path)
    live = live_monitor.fire_beliefs
    before = json.dumps(live.to_json(), sort_keys=True)
    cfg = fbs.belief_cfg(fire_belief_track=True)
    test_world = ThreatMonitor(
        feeds=[ss.ScriptedFeed([fbs.perim_incident(2)])],
        weather_provider=ss.StubWeather(), terrain_provider=ss.StubTerrain(),
        registry=ss.StubRegistry(fbs.clients()), alert_engine=AlertEngine(cfg),
        cfg=cfg, safe_zones=[], fire_beliefs=live.copy())
    big = dataclasses.replace(fbs.perim_incident(2), acreage=9000.0,
                              updated_at_s=T0 + 30 * H)
    test_world._feeds[0].incidents = [big]
    test_world.cycle(T0 + 30 * H)
    assert json.dumps(live.to_json(), sort_keys=True) == before
    assert test_world.fire_beliefs.to_json() != live.to_json()
    assert not test_world.fire_beliefs.persistent


# ---------------------------------------------------------------------------
# 10. CLI + surfaces
# ---------------------------------------------------------------------------

def test_cli_flags_apply_implies_track():
    p = cli._build_parser()
    a = p.parse_args(["watch", "--aoi", "-121,38,-120,39", "--fire-belief-apply"])
    cfg = cli._fire_belief_cfg(ResponseConfig(), a)
    assert cfg.fire_belief_track and cfg.fire_belief_apply
    assert "P10 GUARDED" in cli._fire_belief_notes(a)[0]
    t = p.parse_args(["watch", "--aoi", "-121,38,-120,39", "--fire-belief"])
    cfg = cli._fire_belief_cfg(ResponseConfig(), t)
    assert cfg.fire_belief_track and not cfg.fire_belief_apply
    n = p.parse_args(["watch", "--aoi", "-121,38,-120,39"])
    assert cli._fire_belief_cfg(ResponseConfig(), n) == ResponseConfig()
    assert cli._fire_belief_notes(n) == []


def test_state_and_world_summary_carry_the_belief_while_tracking(tmp_path):
    daemon, _m, keep = _tracked_store(tmp_path)
    st = daemon.state()
    assert set(st["fire_status"]) == {"perim-1", "point-1"}
    row = st["fire_status"]["perim-1"]
    assert row["state"] == "learned" and row["known"] and row["n_obs"] == 1
    assert row["last_obs"]["kind"] == "perimeter"
    summ = daemon.world_summary()
    rows = {r["incident_id"]: r for r in summ["incidents"]}
    assert rows["perim-1"]["belief"] == row
    json.dumps(summ)                              # JSON-able


def test_world_page_draws_the_belief_sentence():
    src = (fbs.ss.__file__ and open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "console", "world.html"), encoding="utf-8").read())
    assert "function beliefText(b)" in src
    assert "beliefText(r.belief)" in src
    assert "Spread rate learned from" in src and "then new pattern" in src


# ---------------------------------------------------------------------------
# 11. config bounds + numerics
# ---------------------------------------------------------------------------

def test_default_config_valid_and_apply_needs_track():
    assert validate_config(ResponseConfig()) == []
    assert validate_config(bcfg(fire_belief_apply=True)) == []
    assert validate_config(bcfg(fire_belief_apply=True,
                                fire_belief_apply_p10=True)) == []
    probs = validate_config(ResponseConfig(fire_belief_apply=True))
    assert any("fire_belief_apply requires fire_belief_track" in p
               for p in probs)
    probs = validate_config(bcfg(fire_belief_apply_p10=True))
    assert any("fire_belief_apply_p10 requires fire_belief_apply" in p
               for p in probs)


@pytest.mark.parametrize("field_name,bad", [
    ("fire_belief_prior_sd_log", -0.1), ("fire_belief_half_life_h", 0.0),
    ("fire_belief_min_obs_spacing_h", -1.0), ("fire_belief_area_rel_err", 1.5),
    ("fire_belief_area_abs_err_acres", 0.0), ("fire_belief_noise_nu", 0.5),
    ("fire_belief_discrepancy_frac", -0.1), ("fire_belief_fast_sd_floor", 4.0),
    ("fire_belief_mult_floor", 0.0), ("fire_belief_novel_severity_ratio", 0.9),
    ("fire_belief_novel_shift_deg", 181.0),
    ("fire_belief_novel_min_wind_mps", -1.0), ("fire_belief_reset_h", -1.0),
    ("fire_belief_static_h", 0.0), ("fire_belief_rapid_acres_per_day", 0.0),
    ("fire_belief_rapid_frac_per_day", 0.0),
    ("fire_belief_rapid_min_acres", -1.0),
    ("fire_belief_rapid_lookback_h", 0.0),
    ("fire_belief_rapid_noise_z", -0.5), ("fire_belief_rapid_noise_z", 6.0),
])
def test_config_bounds(field_name, bad):
    probs = validate_config(bcfg(**{field_name: bad}))
    assert any(field_name in p for p in probs), probs


def test_student_t_helpers():
    for nu in (1.0, 2.0, 3.0, 4.0, 7.5):
        for z in (-8.0, -1.3, 0.0, 0.4, 2.1, 30.0):
            p = 0.5 * fb.betainc(nu / 2.0, 0.5, nu / (nu + z * z))
            want = 1.0 - p if z > 0 else p
            assert fb.t_cdf(z, nu) == pytest.approx(want, abs=1e-12)
    # the density integrates to one
    tot = sum(math.exp(fb.t_logpdf(z / 50.0, 4.0)) / 50.0
              for z in range(-50000, 50001))
    assert tot == pytest.approx(1.0, abs=1e-6)
    assert fb.t_cdf(1.5332, 4.0) == pytest.approx(0.90, abs=1e-4)
