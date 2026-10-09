"""tests/test_city_weather.py — SIMULATOR CITY WEATHER (founder 2026-10-09,
verbatim: "change the weather conditions for each city specific";
contracts sim_weather_profile; docs/review/city_weather_2026-10-09.md).

  * weather.MockWeather's TIME SHAPE (DailyCycle / WindEvent) is additive:
    without them the series is the legacy constant script byte for byte;
    with them every hour is a pure function of its absolute time.
  * simui.CITY_WEATHER covers every simulator community and intensity and
    IS docs/review/city_weather/climatology.json (the station analysis of
    tools/city_weather_climatology.py), transcribed.
  * The simulator runs city weather by default; cfg sim_weather_profile=
    "generic" reproduces the legacy presets exactly (the pin the older
    simulator tests use); a location without a profile falls back.
  * The wind event's clock, the state()['weather'] block, the re-anchor
    weather history, and crew safety under city weather.
  * The page shows what each intensity means for the chosen community.

Logic tests run on SYNTHETIC tiles (tile_dir -> tmp) with roads pinned off,
like tests/test_simui.py. Deterministic (fixed sim epoch, seeded RNG).
"""
from __future__ import annotations

import dataclasses
import json
import math
import re
from pathlib import Path

import pytest

from wgresponse import geo, simui
from wgresponse.contracts import LatLon, ResponseConfig, seeded_rng, \
    validate_config
from wgresponse.weather import DailyCycle, MockWeather, WindEvent

ROOT = Path(__file__).resolve().parent.parent
CLIMO = ROOT / "docs" / "review" / "city_weather" / "climatology.json"
ANCHORS = ROOT / "docs" / "review" / "city_weather" / "anchors.json"
REVIEW = ROOT / "docs" / "review" / "city_weather_2026-10-09.md"
PAGE = ROOT / "console" / "simulator.html"

FAST = {"ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 24.0}
INTENSITIES = ("low", "moderate", "high", "extreme")
T0 = simui.T0_S
MPH = simui.MPH_TO_MPS


def _session(tmp_path, key="boulder_co", seed="cw", cfg=None, n_clients=2,
             n_crews=1, size_km=6.0):
    over = dict(FAST)
    over.update(cfg or {})
    return simui.SimSession(key, n_clients, n_crews, seed=seed,
                            cfg_overrides=over, tile_dir=tmp_path,
                            synth_size_km=size_km, roads_module=None)


def _burnable_point(session):
    from wgresponse.fuels import is_burnable
    spec = session._tile.spec
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            if is_burnable(int(round(session._tile.fuel.get(r, c)))):
                return geo.rc_to_latlon(spec, r, c)
    raise AssertionError("no burnable cell")


# ---------------------------------------------------------------------------
# weather.MockWeather: the time shape is additive
# ---------------------------------------------------------------------------

def _legacy_series(seed, loc, horizon_h, base_wind_mps=6.0, dir_deg=45.0,
                   rh_pct=25.0, temp_c=30.0, gust_factor=1.4, shifts=None,
                   jitter_frac=0.0, t0_s=1_780_000_000.0):
    """The pre-2026-10-09 MockWeather.forecast loop, verbatim (the oracle
    the default path must still equal bit for bit)."""
    rng = seeded_rng(seed, "mockwx", round(loc.lat, 4), round(loc.lon, 4))
    wind, wdir, rh, temp = base_wind_mps, dir_deg, rh_pct, temp_c
    shifts = sorted(shifts or [], key=lambda s: s[0])
    out = []
    si = 0
    for h in range(max(1, int(horizon_h) + 1)):
        while si < len(shifts) and shifts[si][0] <= h:
            _, wind, wdir, rh = shifts[si]
            si += 1
        jw = 1.0 + rng.uniform(-jitter_frac, jitter_frac)
        jr = rng.uniform(-jitter_frac, jitter_frac) * 10.0
        w = max(0.0, wind * jw)
        out.append((t0_s + h * 3600.0, w, w * gust_factor,
                    float(wdir) % 360.0, min(100.0, max(1.0, rh + jr)),
                    float(temp)))
    return out


@pytest.mark.parametrize("kw", [
    {},
    {"base_wind_mps": 17.8816, "dir_deg": 225.0, "rh_pct": 6.0,
     "temp_c": 39.0, "gust_factor": 1.3, "t0_s": T0},
    {"jitter_frac": 0.2, "shifts": [(3, 9.0, 270.0, 11.0), (7, 4.0, 300.0,
                                                           40.0)]},
])
def test_mockweather_default_path_is_the_legacy_script(kw):
    loc = LatLon(40.72, -111.55)
    got = MockWeather(seed="x", **kw).forecast(loc, 30)
    assert [dataclasses.astuple(s) for s in got.samples] == \
        [tuple(r) for r in _legacy_series("x", loc, 30, **kw)]
    assert got.source == "mock"


def _cycle(**kw):
    base = dict(utc_offset_h=-7.0, wind_day_mps=8.0, wind_night_mps=1.0,
                rh_day_pct=12.0, rh_night_pct=70.0, temp_day_c=27.0,
                temp_night_c=8.0, gust_factor=1.5, peak_hour=14.0)
    base.update(kw)
    return DailyCycle(**base)


def _utc_at_local(day_s, local_hour, utc_off_h):
    return day_s + (local_hour - utc_off_h) * 3600.0


def test_daily_cycle_peaks_in_the_afternoon_and_recovers_overnight():
    c = _cycle()
    day = 1_790_000_000.0 - (1_790_000_000.0 % 86400.0)
    peak = _utc_at_local(day, 14.0, -7.0)
    trough = _utc_at_local(day, 6.0, -7.0)
    assert c.local_hour(peak) == pytest.approx(14.0)
    assert c.phase(peak) == pytest.approx(1.0)
    assert c.phase(trough) == pytest.approx(0.0, abs=1e-12)
    assert c.at(peak) == pytest.approx((8.0, 12.0, 27.0))
    assert c.at(trough) == pytest.approx((1.0, 70.0, 8.0))
    # monotone up through the morning, down through evening and night
    ups = [c.phase(_utc_at_local(day, h, -7.0)) for h in range(6, 15)]
    downs = [c.phase(_utc_at_local(day, h, -7.0)) for h in range(14, 30)]
    assert ups == sorted(ups)
    assert downs == sorted(downs, reverse=True)
    # the same local time every day: a pure function of time
    assert c.at(peak + 86400.0) == pytest.approx(c.at(peak))
    with pytest.raises(ValueError):
        _cycle(peak_hour=5.0)            # peak before trough
    with pytest.raises(ValueError):
        _cycle(peak_hour=24.5)


def test_wind_event_holds_then_eases_linearly():
    ev = WindEvent(start_s=T0, peak_h=5.0, ease_h=2.0, wind_mps=10.0,
                   rh_pct=8.0, temp_c=30.0, gust_factor=1.6)
    assert ev.weight(T0 - 1.0) == 0.0
    assert ev.weight(T0) == 1.0
    assert ev.weight(T0 + 4.99 * 3600.0) == 1.0
    assert ev.weight(T0 + 6.0 * 3600.0) == pytest.approx(0.5)
    assert ev.weight(T0 + 7.0 * 3600.0) == 0.0
    assert ev.end_s() == T0 + 7.0 * 3600.0
    with pytest.raises(ValueError):
        WindEvent(start_s=T0, peak_h=-1.0, ease_h=0.0, wind_mps=1.0,
                  rh_pct=1.0, temp_c=1.0)


def test_shaped_forecast_is_a_pure_function_of_absolute_time():
    c = _cycle()
    ev = WindEvent(start_s=T0 + 2 * 3600.0, peak_h=4.0, ease_h=2.0,
                   wind_mps=12.0, rh_pct=7.0, temp_c=29.0, gust_factor=1.6)
    loc = LatLon(39.65, -106.89)
    a = MockWeather(seed="s", t0_s=T0, diurnal=c, event=ev).forecast(loc, 48)
    b = MockWeather(seed="s", t0_s=T0 + 5 * 3600.0, diurnal=c,
                    event=ev).forecast(loc, 30)
    by_t = {s.t_s: s for s in a.samples}
    for s in b.samples:
        if s.t_s in by_t:
            assert dataclasses.astuple(s) == dataclasses.astuple(by_t[s.t_s])
    # the first N hours never depend on the horizon
    short = MockWeather(seed="s", t0_s=T0, diurnal=c,
                        event=ev).forecast(loc, 10)
    assert [dataclasses.astuple(s) for s in short.samples] == \
        [dataclasses.astuple(s) for s in a.samples[:11]]


def test_shaped_forecast_holds_the_event_then_returns_to_the_cycle():
    c = _cycle()
    ev = WindEvent(start_s=T0, peak_h=4.0, ease_h=2.0, wind_mps=12.0,
                   rh_pct=7.0, temp_c=29.0, gust_factor=1.6)
    f = MockWeather(seed="s", t0_s=T0, dir_deg=225.0, diurnal=c,
                    event=ev).forecast(LatLon(39.6, -106.9), 30)
    for s in f.samples[:4]:                                 # the hold
        assert (s.wind_speed_mps, s.rh_pct, s.temp_c) == \
            pytest.approx((12.0, 7.0, 29.0))
        assert s.wind_gust_mps == pytest.approx(12.0 * 1.6)
    for s in f.samples[6:]:                                 # the cycle
        w, rh, t = c.at(s.t_s)
        assert (s.wind_speed_mps, s.rh_pct, s.temp_c) == \
            pytest.approx((w, rh, t))
        assert s.wind_gust_mps == pytest.approx(w * 1.5)
        assert s.wind_dir_deg == 225.0
    mid = f.samples[5]                                      # half eased
    w, rh, t = c.at(mid.t_s)
    assert mid.wind_speed_mps == pytest.approx(w + (12.0 - w) * 0.5)


def test_shaped_forecast_shifts_steer_direction_only():
    c = _cycle()
    f = MockWeather(seed="s", t0_s=T0, dir_deg=225.0, diurnal=c,
                    shifts=[(3, 99.0, 300.0, 99.0)]).forecast(
                        LatLon(39.6, -106.9), 6)
    assert [s.wind_dir_deg for s in f.samples] == [225.0] * 3 + [300.0] * 4
    for s in f.samples:
        assert (s.wind_speed_mps, s.rh_pct) == \
            pytest.approx(c.at(s.t_s)[:2])


# ---------------------------------------------------------------------------
# simui.CITY_WEATHER: every community, every intensity, the analysis itself
# ---------------------------------------------------------------------------

def test_city_weather_covers_every_location_and_intensity():
    assert sorted(simui.CITY_WEATHER) == sorted(simui.LOCATIONS)
    for key, prof in simui.CITY_WEATHER.items():
        assert prof.key == key
        assert sorted(prof.days) == sorted(INTENSITIES)
        assert prof.utc_offset_h in (-8.0, -7.0)
        assert 10.0 <= prof.peak_hour <= 17.0
        assert prof.station and prof.anchor and prof.short
        for name in ("low", "moderate"):
            assert prof.days[name].event_h == 0.0        # cycle only
        for name in ("high", "extreme"):
            d = prof.days[name]
            assert 0.0 < d.event_h <= 24.0, (key, name)   # FINITE
            assert 0.0 <= d.ease_h <= 24.0, (key, name)


def test_city_weather_is_the_station_analysis():
    """simui transcribes docs/review/city_weather/climatology.json (written
    by tools/city_weather_climatology.py) — the table IS the analysis."""
    data = json.loads(CLIMO.read_text(encoding="utf-8"))["communities"]
    assert sorted(data) == sorted(simui.CITY_WEATHER)
    fields = (("wind_mph", "wind_mph"), ("gust_mph", "gust_mph"),
              ("rh_pct", "rh_pct"), ("temp_c", "temp_c"),
              ("night_wind_mph", "night_wind_mph"),
              ("night_rh_pct", "night_rh_pct"),
              ("night_temp_c", "night_temp_c"))
    for key, prof in simui.CITY_WEATHER.items():
        p = data[key]
        assert prof.elev_m == pytest.approx(p["home_elev_m"])
        assert prof.utc_offset_h == pytest.approx(p["utc_off_h"])
        assert prof.peak_hour == pytest.approx(p["peak_hour_lst"])
        assert prof.season == p["season"]
        assert prof.short == p["short"]
        for name in INTENSITIES:
            d, j = prof.days[name], p["days"][name]
            for attr, jk in fields:
                assert getattr(d, attr) == pytest.approx(j[jk], abs=0.051), \
                    (key, name, attr)
            if name in ("high", "extreme"):
                assert d.event_h == pytest.approx(j["hold_h"])
                assert d.ease_h == pytest.approx(j["ease_h"])


def test_city_weather_is_physically_sane_and_city_specific():
    generic = {(v["wind_mph"], v["rh_pct"], v["temp_c"])
               for v in simui.INTENSITY_WEATHER.values()}
    extremes = set()
    for key, prof in simui.CITY_WEATHER.items():
        d = prof.days
        for name in INTENSITIES:
            r = d[name]
            assert (r.wind_mph, r.rh_pct, r.temp_c) not in generic
            assert 0.0 < r.wind_mph <= r.gust_mph < 80.0, (key, name)
            assert 1.0 <= r.rh_pct < r.night_rh_pct <= 100.0, (key, name)
            assert r.night_wind_mph < 25.0, (key, name)
            # mountain homes (>= 1,500 m) are not 39 C
            if prof.elev_m >= 1500.0:
                assert r.temp_c <= 31.0, (key, name)
        # a harsher class is drier than a typical day, and extreme is the
        # windiest
        assert d["extreme"].rh_pct < d["low"].rh_pct, key
        assert d["high"].rh_pct < d["low"].rh_pct, key
        assert d["extreme"].wind_mph == max(r.wind_mph for r in d.values())
        extremes.add((d["extreme"].wind_mph, d["extreme"].rh_pct,
                      d["extreme"].temp_c))
    # communities sharing a station still differ (elevation moves T and RH)
    assert len(extremes) == len(simui.CITY_WEATHER)


def test_anchor_fire_days_were_harsh_days_at_their_stations():
    """The historic fire days read off the archive sit in the top of their
    stations' fire seasons (validates the ranking) — except the Parleys
    Canyon Fire, a hot light-wind afternoon, and the off-footprint stations
    (Santa Monica in the Woolsey wind, Broomfield in the Marshall wind)."""
    rows = json.loads(ANCHORS.read_text(encoding="utf-8"))
    by = {(r["fire"], r["station"]): r for r in rows}
    for fire, sid in (("Woolsey Fire", "CMA"), ("Camp Fire", "CIC"),
                      ("Tubbs Fire", "STS"), ("Witch Creek Fire", "CRQ"),
                      ("Marshall Fire", "BDU"), ("Lake Christine Fire", "ASE"),
                      ("Grizzly Creek Fire", "RIL")):
        assert by[(fire, sid)]["season_percentile"] >= 98.0, (fire, sid)
    assert by[("Parleys Canyon Fire", "HCR")]["season_percentile"] < 90.0


# ---------------------------------------------------------------------------
# SimSession wiring
# ---------------------------------------------------------------------------

def test_contract_knob_default_generic_and_validated():
    assert ResponseConfig().sim_weather_profile == "generic"
    assert validate_config(ResponseConfig()) == []
    bad = dataclasses.replace(ResponseConfig(), sim_weather_profile="local")
    assert any("sim_weather_profile" in p for p in validate_config(bad))
    assert simui.SIM_WEATHER_PROFILE == "city"


def test_simulator_defaults_to_city_weather(tmp_path):
    s = _session(tmp_path)
    assert s._cfg.sim_weather_profile == "city"
    assert s._city_wx is simui.CITY_WEATHER["boulder_co"]
    # the edge law stays the engine's frozen departure hour
    assert s._cfg.edge_weather_piecewise is False
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    vals = {(x.wind_speed_mps, x.rh_pct, x.temp_c) for x in s._wx.samples}
    assert len(vals) > 3                       # not one value held for days
    st = s.state()
    assert st["weather"]["profile"] == "city"
    assert st["cmd_log"][0]["cfg_overrides"] == {
        k: FAST[k] for k in sorted(FAST)}      # the default is not echoed


def test_generic_profile_is_the_legacy_presets_exactly(tmp_path):
    """The pin for the older simulator tests: cfg sim_weather_profile=
    "generic" builds exactly the legacy MockWeather script."""
    s = _session(tmp_path, cfg={"sim_weather_profile": "generic"})
    assert s._city_wx is None
    pt = _burnable_point(s)
    for intensity in INTENSITIES:
        s.ignite(pt.lat, pt.lon, intensity=intensity)
        spec = simui.INTENSITY_WEATHER[intensity]
        ref = MockWeather(seed=f"simui|{s._seed}|wx",
                          base_wind_mps=spec["wind_mph"] * MPH,
                          dir_deg=s._wind_dir_deg, rh_pct=spec["rh_pct"],
                          temp_c=spec["temp_c"],
                          gust_factor=simui.GUST_FACTOR,
                          t0_s=T0).forecast(s._loc.center, FAST["horizon_h"])
        assert [dataclasses.astuple(x) for x in s._wx.samples] == \
            [dataclasses.astuple(x) for x in ref.samples]
    assert s.state()["weather"]["profile"] == "generic"
    assert "held constant" in s.state()["weather"]["label"]


def test_location_without_a_profile_keeps_the_generic_table(tmp_path,
                                                             monkeypatch):
    cw = dict(simui.CITY_WEATHER)
    cw.pop("boulder_co")
    monkeypatch.setattr(simui, "CITY_WEATHER", cw)
    s = _session(tmp_path)
    assert s._cfg.sim_weather_profile == "city" and s._city_wx is None
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    assert {x.wind_speed_mps for x in s._wx.samples} == {40.0 * MPH}
    assert s.state()["weather"]["profile"] == "generic"


@pytest.mark.parametrize("key", ["park_city_ut", "malibu_ca", "eagle_co"])
def test_extreme_event_then_the_moderate_daily_cycle(tmp_path, key):
    s = _session(tmp_path, key=key)
    prof = simui.CITY_WEATHER[key]
    ext, mod = prof.days["extreme"], prof.days["moderate"]
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    by_t = {x.t_s: x for x in s._wx.samples}
    # the hold: the extreme row from the ignition
    for h in range(int(ext.event_h)):
        x = by_t[T0 + h * 3600.0]
        assert (x.wind_speed_mps / MPH, x.rh_pct, x.temp_c) == \
            pytest.approx((ext.wind_mph, ext.rh_pct, ext.temp_c))
    # after the event: exactly the community's moderate daily cycle — its
    # afternoon and its overnight recovery (hourly samples sit at :13:20
    # past the hour, so the day's extremes are read within the cosine)
    end = T0 + (ext.event_h + ext.ease_h) * 3600.0
    cyc = simui._city_cycle(prof, mod)
    after = [x for x in s._wx.samples if x.t_s >= end]
    for x in after:
        assert (x.wind_speed_mps, x.rh_pct, x.temp_c) == \
            pytest.approx(cyc.at(x.t_s), abs=1e-9)
    day = after[:24]
    assert max(x.wind_speed_mps for x in day) / MPH == \
        pytest.approx(mod.wind_mph, rel=0.05)
    assert min(x.wind_speed_mps for x in day) / MPH == \
        pytest.approx(mod.night_wind_mph, abs=0.6)
    assert min(x.rh_pct for x in day) == pytest.approx(mod.rh_pct, abs=1.5)
    assert max(x.rh_pct for x in day) == \
        pytest.approx(mod.night_rh_pct, abs=1.5)
    assert max(x.temp_c for x in day) == pytest.approx(mod.temp_c, abs=0.6)


def test_low_and_moderate_are_the_daily_cycle_alone(tmp_path):
    s = _session(tmp_path, key="aspen_co")
    prof = simui.CITY_WEATHER["aspen_co"]
    pt = _burnable_point(s)
    for name in ("low", "moderate"):
        s.ignite(pt.lat, pt.lon, intensity=name)
        d = prof.days[name]
        winds = [x.wind_speed_mps / MPH for x in s._wx.samples[:48]]
        rhs = [x.rh_pct for x in s._wx.samples[:48]]
        assert max(winds) == pytest.approx(d.wind_mph, abs=0.5)
        assert min(winds) == pytest.approx(d.night_wind_mph, abs=0.5)
        assert min(rhs) == pytest.approx(d.rh_pct, abs=1.5)
        assert max(rhs) == pytest.approx(d.night_rh_pct, abs=1.5)
        assert s.state()["weather"]["event"] is None


def test_event_clock_join_restart_and_change(tmp_path):
    s = _session(tmp_path, key="park_city_ut")
    ext = simui.CITY_WEATHER["park_city_ut"].days["extreme"]
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    assert s._wx_event_start_s == T0
    first = [dataclasses.astuple(x) for x in s._wx.samples]
    s.tick()                                   # 15 min later, still blowing
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    assert s._wx_event_start_s == T0           # joins the event
    assert [dataclasses.astuple(x)
            for x in s._wx.samples[:len(first)]] == first
    s.ignite(pt.lat, pt.lon, intensity="high")  # a change: a new event now
    assert s._wx_event_start_s == s._now_s
    # past the end of the event: the same intensity starts a new one
    s._now_s = s._wx_event_start_s + (
        (simui.CITY_WEATHER["park_city_ut"].days["high"].event_h
         + simui.CITY_WEATHER["park_city_ut"].days["high"].ease_h + 1.0)
        * 3600.0)
    s.ignite(pt.lat, pt.lon, intensity="high")
    assert s._wx_event_start_s == s._now_s
    ev = s.state()["weather"]["event"]
    assert ev["start_s"] == s._now_s
    assert ev["ends_s"] > ev["hold_until_s"] > ev["start_s"]
    assert ext.event_h > 0


def test_state_weather_block_says_what_each_intensity_means(tmp_path):
    s = _session(tmp_path, key="park_city_ut")
    w = s.state()["weather"]
    assert w["profile"] == "city" and w["community"] == "Park City"
    assert w["intensity"] is None and w["now"] is None and w["event"] is None
    assert list(w["presets"]) == list(INTENSITIES)
    ext = simui.CITY_WEATHER["park_city_ut"].days["extreme"]
    line = w["presets"]["extreme"]
    assert line.startswith("Park City · extreme: ")
    assert f"{simui._mph_round(ext.wind_mph)} mph gusting " \
           f"{simui._mph_round(ext.gust_mph)}" in line
    assert f"{simui._mph_round(ext.rh_pct)} % RH" in line
    assert f"eases after {ext.event_h:g} h" in line
    assert "afternoons" in w["presets"]["moderate"]
    assert "nights" in w["presets"]["low"]
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    w = s.state()["weather"]
    assert w["label"] == line and w["intensity"] == "extreme"
    assert w["now"]["wind_mph"] == pytest.approx(ext.wind_mph, abs=0.05)
    assert 0.0 <= w["now"]["local_hour"] < 24.0
    json.dumps(w, allow_nan=False)


def test_server_session_carries_city_weather_and_the_generic_pin(tmp_path):
    from wgresponse import server
    mgr = server.SimSessionManager(tile_dir=tmp_path, synth_size_km=6.0)
    code, out = mgr.new_session({"location": "vail_co", "clients": 2,
                                 "crews": 1, "cfg": dict(FAST)})
    assert code == 200, out
    w = out["state"]["weather"]
    assert w["profile"] == "city" and len(w["presets"]) == 4
    assert w["presets"]["extreme"].startswith("Vail · extreme: ")
    code, out = mgr.new_session({"location": "vail_co", "clients": 2,
                                 "crews": 1, "cfg": dict(
                                     FAST, sim_weather_profile="generic")})
    assert code == 200, out
    assert out["state"]["weather"]["profile"] == "generic"
    code, out = mgr.new_session({"location": "vail_co", "clients": 2,
                                 "crews": 1, "cfg": dict(
                                     FAST, sim_weather_profile="nope")})
    assert code == 400 and "sim_weather_profile" in out["error"]
    mgr.stop_all()


def test_forecast_extends_without_a_world_change(tmp_path):
    s = _session(tmp_path, key="eagle_co")
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="high")
    before = [dataclasses.astuple(x) for x in s._wx.samples]
    epoch = s._wx_epoch
    s._ensure_city_wx_covers(T0 + 400 * 3600.0)
    assert s._wx.samples[-1].t_s >= T0 + 400 * 3600.0
    assert [dataclasses.astuple(x)
            for x in s._wx.samples[:len(before)]] == before
    assert s._wx_epoch == epoch                # not a world change


def test_reanchor_history_is_hourly_under_city_weather(tmp_path):
    s = _session(tmp_path, key="eagle_co", size_km=4.0)
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    for _ in range(9):                         # 2 h 15 min
        s.tick()
    f = s._fires["sim-f1"]
    field_wx = f._wx_field
    s.set_wind(s._wind_dir_deg + 30.0)          # a world change: re-anchor
    s.tick()
    hist = f._wx_hist
    spans = [t for t, _ in hist]
    assert spans[:3] == [T0, T0 + 3600.0, T0 + 7200.0]     # hourly
    for t, smp in hist[:-1]:
        assert dataclasses.astuple(smp) == \
            dataclasses.astuple(field_wx.at(t))
    assert hist[-1][0] == f.anchor_t0_s


def test_generic_reanchor_history_keeps_one_sample_per_field(tmp_path):
    s = _session(tmp_path, key="eagle_co", size_km=4.0,
                 cfg={"sim_weather_profile": "generic"})
    pt = _burnable_point(s)
    s.ignite(pt.lat, pt.lon, intensity="extreme")
    for _ in range(9):
        s.tick()
    s.set_wind(s._wind_dir_deg + 30.0)
    s.tick()
    f = s._fires["sim-f1"]
    assert [t for t, _ in f._wx_hist] == [T0, f.anchor_t0_s]
    assert f._wx_field is None


@pytest.mark.parametrize("key,intensity", [("boulder_co", "extreme"),
                                           ("paradise_ca", "extreme"),
                                           ("malibu_ca", "high")])
def test_crew_safety_holds_under_city_weather(tmp_path, key, intensity):
    """Never weaken a safety law: test_simui's crew-safety scenario (the
    controlled tile, 10 homes, 2 crews, fire 5 km upwind, 40 ticks at 2x)
    under city weather — no idle crew ever stands in the burn, the departure
    audit and the crew-overrun backstop stay 0 EVERY tick, every serviced
    stop's margin is non-negative, and work was really done."""
    from tests.test_simui import (_controlled_tile_dir, _idle_crews_in_burn,
                                  _upwind_ignition)
    d = _controlled_tile_dir(tmp_path, key, hamlet_half=3)
    s = simui.SimSession(key, 10, 2, seed="cs1", cfg_overrides=dict(FAST),
                         tile_dir=d, roads_module=None)
    assert s._city_wx is simui.CITY_WEATHER[key]
    ign = _upwind_ignition(key, 5_000.0)
    s.ignite(ign.lat, ign.lon, intensity=intensity)
    s.set_speed(2.0)
    st = None
    for _ in range(40):
        st = s.tick()
        assert not _idle_crews_in_burn(s, s._now_s), st["tick"]
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]
    assert all(a["margin_h"] >= 0.0 for a in st["audit"]
               if math.isfinite(a["margin_h"]))
    assert st["scoreboard"]["served"] >= 1, "vacuous: nothing serviced"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN CREW-SAFETY GAP exposed by city weather (docs/review/"
    "city_weather_2026-10-09.md, 'Safety: one gap'): under the daily cycle "
    "the young fire is small and slow in the morning while the forecast's "
    "afternoon makes homes jobs; the single crew's egress passes ~470 m from "
    "the burning ignition cell, inside the 550 m eroded-P10 band, and the "
    "SEM-10c audit reports it (constant weather at either end of the cycle: "
    "no overrun). The crew-safety code reads only the arrival fields. When "
    "the gap is fixed this XPASSes: drop the xfail."))
def test_known_gap_young_fire_egress_overrun_under_city_weather(tmp_path):
    from tests.test_simui import (_nudge_to_burnable, _triage_scatter_dir,
                                  _upwind_ignition)
    d = _triage_scatter_dir(tmp_path, seed="C")
    s = simui.SimSession("boulder_co", 50, 1, seed="C", cfg_overrides={
        "ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 120.0},
        tile_dir=d, roads_module=None)
    assert s._city_wx is not None
    ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 5_000.0))
    s.ignite(ign.lat, ign.lon, intensity="moderate")
    for _ in range(6):
        st = s.tick()
        assert st["scoreboard"]["violations"] == 0, st["crew_overruns"]


# ---------------------------------------------------------------------------
# The page and the docs
# ---------------------------------------------------------------------------

def test_page_shows_what_each_intensity_means_here():
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="wxlegend"' in html
    assert "function renderWeather(st)" in html
    assert "renderWeather(st);" in html            # every render
    assert "w.presets" in html and "w.event" in html and "w.now" in html
    # arming an intensity lights its row at once
    assert re.search(r'classList\.toggle\("active", armed === j\);[\s\S]{0,'
                     r'300}renderWeather\(lastState\)', html)
    seg = html[html.index("function renderWeather"):
               html.index("function relH(")]
    assert "http" not in seg                       # no external host


def test_review_doc_and_tool_name_the_stations():
    tool = (ROOT / "tools" / "city_weather_climatology.py").read_text(
        encoding="utf-8")
    for sid in ("CMA", "CIC", "STS", "CRQ", "BDU", "36U", "HCR", "EGE",
                "RIL", "ASE"):
        assert f'"{sid}"' in tool, sid
    doc = REVIEW.read_text(encoding="utf-8")
    for needle in ("Iowa Environmental Mesonet", "KHCR", "KEGE", "KASE",
                   "Marshall", "Camp Fire", "Tubbs", "Woolsey",
                   "Witch Creek", "Parleys", "Lake Christine",
                   "Grizzly Creek", "sim_weather_profile"):
        assert needle in doc, needle


def test_tool_emc_is_the_engine_emc():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cwc", ROOT / "tools" / "city_weather_climatology.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from wgresponse.fuels import dead_fuel_moisture_frac
    for t, rh in ((5.0, 80.0), (25.0, 15.0), (35.0, 6.0), (12.0, 40.0)):
        assert max(0.01, 1.03 * mod.emc_pct(t, rh) / 100.0) == \
            pytest.approx(dead_fuel_moisture_frac(t, rh))
    # FFWI: calm and saturated is ~0, windier is higher, drier is higher
    assert mod.ffwi(0.0, 10.0, 100.0) < 1.0
    assert mod.ffwi(30.0, 25.0, 10.0) > mod.ffwi(15.0, 25.0, 10.0)
    assert mod.ffwi(20.0, 25.0, 8.0) > mod.ffwi(20.0, 25.0, 30.0)
    # elevation: 500 m up is cooler and (same air) more humid
    t2, rh2 = mod.to_elevation(25.0, 15.0, 500.0, "day")
    assert t2 == pytest.approx(25.0 - 7.1 * 0.5) and rh2 > 15.0
