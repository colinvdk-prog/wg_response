"""Fire belief v2 rules (2026-09-28; contracts SAFETY SEMANTICS 14 "v2";
docs/FIRE_BELIEF.md "v2"; wgresponse/fire_belief.py "V2 RULES").

Three new knobs, each defaulting to the legacy rule (byte-identical; the
a8bebec goldens in tests/test_fire_belief.py pin the defaults, and the
goldens are re-checked here with the v2 knobs SET but tracking off, and with
tracking on):
  * cfg.fire_belief_novelty = 'daily': forecast DAYS against evidence DAYS
    (peak severity + driving wind) -- the afternoon wind turn is no longer a
    new weather pattern; a new driving-wind sector, a harsher day, or a
    driving wind where the evidence had none still are;
  * cfg.fire_belief_rapid_noise = 'calibrated': a PERIMETER window's growth
    is judged beyond the measured re-mapping noise (~sqrt of the area), so a
    big fire's real growth is visible; reported windows keep the legacy sd;
  * cfg.fire_belief_rapid_persist_windows = 2: two consecutive perimeter
    windows at a rapid rate (raw) make a fire rapid.
Plus the watch flag --fire-belief-v2 and the config bounds.
"""
from __future__ import annotations

import dataclasses
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fire_belief_stack as fbs  # noqa: E402
from test_fire_belief import (  # noqa: E402
    _GOLDEN_API, _GOLDEN_CYCLES, _GOLDEN_STATE, _GOLDEN_WORLD)
from wgresponse import cli  # noqa: E402
from wgresponse import fire_belief as fb  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
    validate_config,
)
from wgresponse import geo  # noqa: E402

H = 3600.0
C = LatLon(39.0, -121.0)                 # local solar time = UTC - 8.07 h
OFF_S = C.lon / 15.0 * H
# 2026-07-20 14:00 UTC = 05:56 local solar: the forecast starts before dawn
NOW = 1784556000.0
V2 = dict(fire_belief_novelty="daily", fire_belief_rapid_noise="calibrated",
          fire_belief_rapid_persist_windows=2)


def cfg_of(**kw) -> ResponseConfig:
    base = dict(fire_belief_track=True, horizon_h=96.0)
    base.update(kw)
    return ResponseConfig(**base)


def local_hour(t: float) -> float:
    return ((t + OFF_S) / H) % 24.0


def local_day(t: float) -> int:
    return int(math.floor((t + OFF_S) / 86400.0))


def afternoon(t: float) -> bool:
    return 11.0 <= local_hour(t) < 19.0


def diurnal(t: float, day_wind=(6.0, 270.0), night_wind=(2.5, 90.0),
            day_rh=20.0, day_temp=30.0):
    """(wind m/s, dir, rh, temp): afternoon upslope/synoptic wind from the
    west, drier and hotter; night/morning a light downslope wind from the
    east, moist and cool -- the ordinary diurnal cycle."""
    if afternoon(t):
        return day_wind[0], day_wind[1], day_rh, day_temp
    return night_wind[0], night_wind[1], 60.0, 12.0


def learned(now=NOW, rows_fn=diurnal, hours=72, window_end=None):
    """A store with one learned growth window over the last `hours` and the
    weather log of those hours (rows_fn(t) -> (ws, dir, rh, temp))."""
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", now - 200 * H)
    tb = now if window_end is None else window_end
    b.windows = [{"kind": "perimeter", "ta": tb - hours * H, "tb": tb,
                  "aa": 1e7, "ab": 1.1e7, "static": False, "vobs": 30.0,
                  "sdv": 5.0, "vnom": 100.0, "cens": False, "mnom": 1.0,
                  "pred50": None, "pred90": None, "surprise": False}]
    b.fuel_code = 102
    t0 = math.floor(now / H) * H
    b.wx_log = []
    for i in range(hours, 0, -1):
        t = t0 - i * H
        ws, wd, rh, tc = rows_fn(t)
        b.wx_log.append([t, ws, wd, rh, tc])
    st._beliefs["x"] = b
    return st, b


def forecast(fn, now=NOW, hours=100):
    t0 = math.floor(now / H) * H
    samples = []
    for i in range(hours):
        ws, wd, rh, tc = fn(t0 + i * H)
        samples.append(WeatherSample(t0 + i * H, ws, ws, wd, rh, tc))
    return WeatherForecast(loc=C, samples=samples)


def novelty(st, b, wx, cfg, now=NOW):
    return st._novelty(b, st._evidence(b, now, cfg), wx, now, cfg)


def first_hour(pred, now=NOW, hours=97) -> int:
    return next(h for h in range(hours) if pred(now + h * H))


# ---------------------------------------------------------------------------
# 1. the daily new-weather test
# ---------------------------------------------------------------------------

def test_the_diurnal_wind_turn_is_new_hourly_but_not_daily():
    """Evidence and forecast share one diurnal cycle: the hourly rule calls
    the first afternoon hour new (the day and night winds average to no
    steady direction); the daily rule sees the same pattern all horizon."""
    st, b = learned()
    wx = forecast(diurnal)
    h, why = novelty(st, b, wx, cfg_of())
    assert h == first_hour(afternoon) and "no steady direction" in why
    h, why = novelty(st, b, wx, cfg_of(fire_belief_novelty="daily"))
    assert h == fb.horizon_hours(cfg_of()) + 1 and why == ""


def test_a_new_driving_wind_sector_is_new_from_its_first_hour():
    """The second forecast afternoon's wind comes from the north-east (45
    deg, 135 deg from every evidence day's driving wind): new from its first
    qualifying hour, not before."""
    day2 = local_day(NOW) + 1

    def fn(t):
        if local_day(t) == day2 and afternoon(t):
            return 7.0, 45.0, 20.0, 30.0
        return diurnal(t)
    st, b = learned()
    h, why = novelty(st, b, forecast(fn), cfg_of(fire_belief_novelty="daily"))
    assert h == first_hour(lambda t: local_day(t) == day2 and afternoon(t))
    assert "driving wind" in why and "from 45 deg" in why


def test_a_harsher_day_is_new_from_its_first_harsher_hour():
    day3 = local_day(NOW) + 2

    def fn(t):
        if local_day(t) == day3 and afternoon(t):
            return 12.0, 270.0, 8.0, 36.0
        return diurnal(t)
    st, b = learned()
    h, why = novelty(st, b, forecast(fn), cfg_of(fire_belief_novelty="daily"))
    assert h == first_hour(lambda t: local_day(t) == day3 and afternoon(t))
    assert "severity" in why and "harshest evidence day" in why


def test_calm_or_night_only_changes_are_not_a_new_pattern():
    day2 = local_day(NOW) + 1
    st, b = learned()
    cfg = cfg_of(fire_belief_novelty="daily")

    def light(t):             # a new direction below the minimum wind
        if local_day(t) == day2 and afternoon(t):
            return 3.0, 45.0, 20.0, 30.0
        return diurnal(t)
    assert novelty(st, b, forecast(light), cfg)[0] == 97

    def nights(t):            # the night wind turns; afternoons unchanged
        if not afternoon(t):
            return 3.5, 0.0, 60.0, 12.0
        return diurnal(t)
    assert novelty(st, b, forecast(nights), cfg)[0] == 97


def test_a_driving_wind_where_the_evidence_had_none_is_new():
    """Evidence afternoons were as windy but UNSTEADY (the wind swapped
    between north and south every hour: no driving-wind sector at all): the
    first forecast afternoon with a steady 6 m/s wind is a new pattern --
    and a calm evidence record makes that afternoon a harsher day."""
    def gusty(t):
        if afternoon(t):
            return 6.0, (0.0 if int(t // H) % 2 else 180.0), 20.0, 30.0
        return diurnal(t)
    st, b = learned(rows_fn=gusty)
    cfg = cfg_of(fire_belief_novelty="daily")
    h, why = novelty(st, b, forecast(diurnal), cfg)
    assert h == first_hour(afternoon)
    assert "where no evidence day had one" in why
    st, b = learned(rows_fn=lambda t: diurnal(t, day_wind=(2.0, 270.0)))
    h, why = novelty(st, b, forecast(diurnal), cfg)
    assert h == first_hour(afternoon) and "harshest evidence day" in why


def test_today_s_logged_afternoon_counts_toward_today():
    """Evening forecast: the belief's window ended before today's afternoon,
    which came (logged) from a new sector -> today is already a new
    pattern: physics from now (hour 0)."""
    now = NOW + 15 * H                      # 20:56 local solar
    today = local_day(now)

    def logged(t):
        if local_day(t) == today and afternoon(t):
            return 7.0, 45.0, 20.0, 30.0
        return diurnal(t)
    st, b = learned(now=now, rows_fn=logged, window_end=now - 14 * H,
                    hours=72)
    cfg = cfg_of(fire_belief_novelty="daily")
    h, why = novelty(st, b, forecast(diurnal, now=now), cfg, now=now)
    assert h == 0 and "driving wind" in why


def test_daily_keeps_the_hourly_rules_no_evidence_answers():
    cfg = cfg_of(fire_belief_novelty="daily")
    st, b = learned()
    b.wx_log = []
    h, why = novelty(st, b, forecast(diurnal), cfg)
    assert h == 0 and "no logged weather" in why
    st, b = learned()
    assert st._novelty(b, [], forecast(diurnal), NOW, cfg)[0] == 0


def _tile(n=11, fuel=102.0):
    spec = GridSpec(north_lat=39.5, west_lon=-121.5, cell_m=100.0,
                    nrows=n, ncols=n)
    return TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=fuel),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0))


def _inc():
    return FireIncident(incident_id="x", name="x", source=IncidentSource.MOCK,
                        origin=C, updated_at_s=NOW, perimeter=None)


def test_before_spread_applies_through_the_diurnal_cycle_only_when_daily():
    """Applied: the hourly rule hands the members back to physics at the
    first afternoon; the daily rule keeps the learned multiplier all
    horizon (and the P10 guard, applied_ensemble, is untouched either way)."""
    wx = forecast(diurnal)
    tile = _tile()
    st, b = learned()
    fn_h = st.before_spread(_inc(), tile, wx, NOW,
                            cfg_of(fire_belief_apply=True))
    st, b = learned()
    fn_d = st.before_spread(_inc(), tile, wx, NOW,
                            cfg_of(fire_belief_apply=True,
                                   fire_belief_novelty="daily"))
    nom_h, nom_d = fn_h(0, 0.5, 1.0), fn_d(0, 0.5, 1.0)
    cut = first_hour(afternoon)
    assert nom_h[0] < 1.0 and nom_h[cut:] == [1.0] * (len(nom_h) - cut)
    assert nom_d == [nom_d[0]] * len(nom_d) and nom_d[0] < 1.0


# ---------------------------------------------------------------------------
# 2. the rapid rule: calibrated noise + persistence
# ---------------------------------------------------------------------------

AC = fb.ACRE_M2


def _win(a0, a1, tb, dt_h=12.0, kind="perimeter"):
    return {"kind": kind, "ta": tb - dt_h * H, "tb": tb, "aa": a0 * AC,
            "ab": a1 * AC, "vobs": 0.0, "sdv": 1.0, "vnom": None,
            "static": False}


def _rapid(windows, cfg, now=NOW):
    st = fb.FireBeliefStore()
    b = fb.IncidentBelief("x", now - 400 * H)
    b.windows = list(windows)
    st._beliefs["x"] = b
    return st._rapid(b, now, cfg)


def test_calibrated_noise_sees_a_big_fire_growing():
    """Little Giant, Aug 6 18:08 -> Aug 7 22:33: 77,509 -> 82,758 ac in
    28.4 h. 3 % of 80,000 ac is +-2,400 ac per end: legacy reads the 5,249
    ac as noise; the calibrated edge noise (~sqrt A) does not."""
    w = [_win(77509, 82758, NOW, dt_h=28.4)]
    assert not _rapid(w, cfg_of())[0]
    rapid, why = _rapid(w, cfg_of(fire_belief_rapid_noise="calibrated"))
    assert rapid and "beyond noise" in why[0]
    sd = fb.perimeter_growth_sd_acres(77509 * AC, 82758 * AC, cfg_of())
    assert sd == pytest.approx(math.hypot(math.sqrt(82758.0),
                                          math.sqrt(2.0) * 10.0))


def test_calibrated_noise_still_discounts_noise_level_growth():
    """What the discount cured stays cured: +600 ac in 12 h on a 20,000-ac
    fire (1,200 ac/24 h raw) is still not rapid; +1,000 ac is."""
    cal = cfg_of(fire_belief_rapid_noise="calibrated")
    assert not _rapid([_win(20000, 20600, NOW)], cal)[0]
    assert not _rapid([_win(20000, 20600, NOW)], cfg_of())[0]
    assert _rapid([_win(20000, 21000, NOW)], cal)[0]
    assert not _rapid([_win(20000, 21000, NOW)], cfg_of())[0]
    # a big contained fire re-mapped by a few hundred acres is never rapid
    assert not _rapid([_win(166000, 166400, NOW)], cal)[0]


def test_reported_windows_keep_the_legacy_noise():
    cal = cfg_of(fire_belief_rapid_noise="calibrated")
    assert not _rapid([_win(20000, 21000, NOW, kind="reported")], cal)[0]


def test_persistence_two_consecutive_raw_rapid_windows():
    """Two consecutive perimeter windows at 1,200 ac/24 h raw, each inside
    the legacy single-window noise: rapid with persistence 2, not without."""
    p2 = cfg_of(fire_belief_rapid_persist_windows=2)
    two = [_win(20000, 20600, NOW - 12 * H), _win(20600, 21200, NOW)]
    assert not _rapid(two, cfg_of())[0]
    rapid, why = _rapid(two, p2)
    assert rapid and "2 consecutive windows" in why[-1]
    assert not _rapid(two[1:], p2)[0]                   # one window
    gap = [_win(20000, 20600, NOW - 13 * H), _win(20600, 21200, NOW)]
    assert not _rapid(gap, p2)[0]                       # not consecutive
    slow = [_win(20000, 20300, NOW - 12 * H), _win(20300, 20900, NOW)]
    assert not _rapid(slow, p2)[0]                      # one below the rate
    old = [_win(20000, 20600, NOW - 61 * H), _win(20600, 21200, NOW - 49 * H)]
    assert not _rapid(old, p2)[0]                       # outside the lookback
    rep = [_win(20000, 20600, NOW - 12 * H, kind="reported"),
           _win(20600, 21200, NOW, kind="reported")]
    assert not _rapid(rep, p2)[0]                       # perimeter only
    # +125 ac per 12 h on a ~600-ac fire: 40-50 %/day raw, but under 100 ac
    # beyond the legacy noise in either window alone
    small = [_win(500, 625, NOW - 12 * H), _win(625, 750, NOW)]
    assert not _rapid(small, cfg_of())[0]
    assert _rapid(small, p2)[0]                         # the 25 %/day rule


# ---------------------------------------------------------------------------
# 3. byte identity, flag, bounds
# ---------------------------------------------------------------------------

def test_v2_knobs_are_inert_without_tracking(tmp_path):
    cycles, api, state, world = fbs.belief_baseline_run(
        tmp_path / "s", cfg=fbs.belief_cfg(**V2))
    assert cycles == _GOLDEN_CYCLES and api == _GOLDEN_API
    assert state == _GOLDEN_STATE and world == _GOLDEN_WORLD


def test_v2_tracking_changes_no_arrival_eta_alert_or_dispatch(tmp_path):
    keep = []
    cycles, _api, state, _w = fbs.belief_baseline_run(
        tmp_path / "s", cfg=fbs.belief_cfg(fire_belief_track=True, **V2),
        keep=keep)
    assert cycles == _GOLDEN_CYCLES and state == _GOLDEN_STATE
    assert keep[-1].fire_status["perim-1"].state == "learned"


def test_v2_apply_keeps_physics_for_the_pinned_rapid_fire(tmp_path):
    keep = []
    cycles, *_ = fbs.belief_baseline_run(
        tmp_path / "s", cfg=fbs.belief_cfg(fire_belief_track=True,
                                            fire_belief_apply=True, **V2),
        keep=keep)
    assert keep[2].fire_status["perim-1"].rapid
    assert cycles == _GOLDEN_CYCLES


def test_cli_v2_flag_arms_the_three_rules_and_implies_track():
    p = cli._build_parser()
    a = p.parse_args(["watch", "--aoi", "-121,38,-120,39", "--fire-belief-v2"])
    cfg = cli._fire_belief_cfg(ResponseConfig(), a)
    assert cfg.fire_belief_track and not cfg.fire_belief_apply
    assert (cfg.fire_belief_novelty, cfg.fire_belief_rapid_noise,
            cfg.fire_belief_rapid_persist_windows) == ("daily", "calibrated", 2)
    notes = cli._fire_belief_notes(a)
    assert notes[0].startswith("fire belief TRACKING")
    assert notes[1].startswith("fire belief v2 RULES")
    b = p.parse_args(["watch", "--aoi", "-121,38,-120,39",
                      "--fire-belief-apply", "--fire-belief-v2"])
    cfg = cli._fire_belief_cfg(ResponseConfig(), b)
    assert cfg.fire_belief_apply and cfg.fire_belief_novelty == "daily"
    assert "P10 GUARDED" in cli._fire_belief_notes(b)[0]
    assert validate_config(cfg) == []
    n = p.parse_args(["watch", "--aoi", "-121,38,-120,39", "--fire-belief"])
    cfg = cli._fire_belief_cfg(ResponseConfig(), n)
    assert cfg == dataclasses.replace(ResponseConfig(), fire_belief_track=True)
    assert len(cli._fire_belief_notes(n)) == 1


def test_v2_defaults_are_the_legacy_rules():
    c = ResponseConfig()
    assert (c.fire_belief_novelty, c.fire_belief_rapid_noise,
            c.fire_belief_rapid_persist_windows) == ("hourly", "area_frac", 0)
    assert validate_config(cfg_of(**V2)) == []


@pytest.mark.parametrize("field_name,bad", [
    ("fire_belief_novelty", "weekly"),
    ("fire_belief_novel_day_severity_ratio", 0.9),
    ("fire_belief_novel_day_shift_deg", -1.0),
    ("fire_belief_novel_day_shift_deg", 181.0),
    ("fire_belief_novel_day_min_wind_mps", -1.0),
    ("fire_belief_rapid_noise", "raw"),
    ("fire_belief_rapid_perim_noise_k", 0.0),
    ("fire_belief_rapid_perim_noise_k", 101.0),
    ("fire_belief_rapid_persist_windows", 1),
    ("fire_belief_rapid_persist_windows", -1),
])
def test_v2_config_bounds(field_name, bad):
    probs = validate_config(cfg_of(**{field_name: bad}))
    assert any(field_name in p for p in probs), probs
