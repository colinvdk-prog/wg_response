"""City weather and the crew-safety audit: where do crew overruns come from?
(2026-10-09; docs/review/city_weather_2026-10-09.md "Safety: one gap"). NOT
product code.

  malibu    the Malibu one-click scenario exactly as the page builds it
            (grass-only floor set explicitly), 6 h, under the old presets
            at high / moderate / extreme and under city weather: the
            per-tick maximum of scoreboard.violations, the ticks with a
            SEM-10c crew-overrun row (and the first one), withdrawals and
            relocations.
  triage    test_simui's scatter scenario (seed C, 50 homes, 1 crew,
            moderate fire 5 km upwind, 12 ticks) under the old presets
            (low / moderate / high), city weather (the daily cycle), and
            CONSTANT weather at the city cycle's afternoon values and at its
            night values — does the overrun need the time-varying weather?

  floor     every one-click scenario with TODAY's page floor (all four
            fuel classes: no ros_floor_grass_only key — the setting until
            the simulator's grass-only default lands), old presets vs city
            weather, 12 h: violations, overrun ticks, withdrawals,
            relocations, acres.

Usage: py -3 scratch_review/city_weather_overrun_probe.py
       [malibu|triage|floor|both]   (both = malibu + triage)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import server, simui  # noqa: E402
from wgresponse.weather import MockWeather  # noqa: E402


def malibu(hours: float = 6.0) -> None:
    key = "malibu_santa_ana"
    for prof, inten in (("generic", "extreme"), ("generic", "high"),
                        ("generic", "moderate"), ("city", "extreme")):
        mgr = server.SimSessionManager()
        code, out = mgr.new_session({"scenario": key, "cfg": {
            "ros_floor_grass_only": True, "sim_weather_profile": prof}})
        s = mgr._entry(out["session_id"]).session
        f = s._fires["sim-f1"]
        if inten != f.intensity:
            lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
            s.reset()
            s.ignite(lat, lon, intensity=inten, wind_dir_deg=wd)
        ov_ticks, first, viol = 0, None, 0
        for k in range(int(hours * 4)):
            st = s.tick()
            viol = max(viol, st["scoreboard"]["violations"])
            if st.get("crew_overruns"):
                ov_ticks += 1
                if first is None:
                    r = st["crew_overruns"][0]
                    first = (f"T+{(k + 1) * 0.25:.2f}h {r['crew_id']} "
                             f"{r['reason']} {r['mode']}")
        sb = st["scoreboard"]
        print(f"{key} {prof}:{inten}: max violations {viol}, overrun ticks "
              f"{ov_ticks} (first {first}), withdrawn {sb['withdrawn']}, "
              f"relocations {sb['relocations']}", flush=True)
        with mgr._lock:
            mgr._sessions.clear()


def triage() -> None:
    from tests.test_simui import (_nudge_to_burnable, _triage_scatter_dir,
                                  _upwind_ignition)
    m = simui.CITY_WEATHER["boulder_co"].days["moderate"]
    runs = [("old low", "generic", "low", None),
            ("old moderate", "generic", "moderate", None),
            ("old high", "generic", "high", None),
            ("city moderate (daily cycle)", "city", "moderate", None),
            ("constant: city afternoon", "generic", "moderate",
             (m.wind_mph, m.rh_pct, m.temp_c)),
            ("constant: city night", "generic", "moderate",
             (m.night_wind_mph, m.night_rh_pct, m.night_temp_c))]
    for label, prof, inten, const in runs:
        d = _triage_scatter_dir(Path(tempfile.mkdtemp()), seed="C")
        s = simui.SimSession("boulder_co", 50, 1, seed="C", cfg_overrides={
            "ensemble_n": 3, "grid_cell_m": 500.0, "horizon_h": 120.0,
            "sim_weather_profile": prof}, tile_dir=d, roads_module=None)
        ign = _nudge_to_burnable(s, _upwind_ignition("boulder_co", 5_000.0))
        s.ignite(ign.lat, ign.lon, intensity=inten)
        if const is not None:          # the same field, constant weather
            w, rh, t = const
            s._wx = MockWeather(
                seed="c", base_wind_mps=w * simui.MPH_TO_MPS,
                dir_deg=s._wind_dir_deg, rh_pct=rh, temp_c=t,
                t0_s=simui.T0_S).forecast(s._loc.center, 120)
        worst, first = 0, None
        for k in range(12):
            st = s.tick()
            v = st["scoreboard"]["violations"]
            if v and first is None:
                r = (st.get("crew_overruns") or [{}])[0]
                first = (f"T+{(k + 1) * 0.25:.2f}h {r.get('reason')} "
                         f"{r.get('mode')}")
            worst = max(worst, v)
        print(f"triage {label:30s} max violations {worst} first {first}",
              flush=True)


def floor(hours: float = 12.0) -> None:
    for key in sorted(server.SCENARIOS):
        for prof in ("generic", "city"):
            mgr = server.SimSessionManager()
            code, out = mgr.new_session({"scenario": key, "cfg": {
                "sim_weather_profile": prof}})
            s = mgr._entry(out["session_id"]).session
            worst, ov, first = 0, 0, None
            for k in range(int(hours * 4)):
                st = s.tick()
                worst = max(worst, st["scoreboard"]["violations"])
                if st.get("crew_overruns"):
                    ov += 1
                    if first is None:
                        r = st["crew_overruns"][0]
                        first = (f"T+{(k + 1) * 0.25:.2f}h {r['crew_id']} "
                                 f"{r['reason']} {r['mode']}")
            sb = st["scoreboard"]
            print(f"{key:32s} {prof:7s} max violations {worst} overrun ticks "
                  f"{ov} (first {first}) | withdrawn {sb['withdrawn']} "
                  f"relocations {sb['relocations']} | acres "
                  f"{st['fire_view']['areas_km2']['fire'] * 247.105:.0f}",
                  flush=True)
            with mgr._lock:
                mgr._sessions.clear()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("malibu", "both"):
        malibu()
    if which in ("triage", "both"):
        triage()
    if which == "floor":
        floor()
