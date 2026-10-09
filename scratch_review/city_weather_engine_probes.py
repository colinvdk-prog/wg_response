"""City weather: three engine measurements behind docs/review/
city_weather_2026-10-09.md "Engine notes" (2026-10-09). NOT product code.

  edge     city weather, the engine's frozen departure-hour edge law vs
           cfg.edge_weather_piecewise: nominal acres at 1-48 h from each
           one-click ignition (direct SimSession, page physics, grass-only
           floor, 1 home / 1 crew, roads off) and the first field's solve time
  sweep    constant weather (13 % RH, 30 C), wind 10-40 mph: nominal acres at
           1 / 3 / 6 / 12 h from the scenario's ignition, spotting off and on
           — does burned area grow with wind on these maps?
  tick     the first step's time through the server (the page's path):
           fire field vs dispatch planner, old presets vs city weather

Usage: py -3 scratch_review/city_weather_engine_probes.py edge|sweep|tick
       [scenario,...] [intensity]
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from wgresponse import dispatch, geo, server, simui, spread  # noqa: E402
from wgresponse.contracts import ResponseConfig  # noqa: E402
from wgresponse.weather import MockWeather  # noqa: E402


def _ignite_like_server(s, sc):
    center = simui.LOCATIONS[sc["location"]].center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            p = geo.destination(center, sc["ign_bearing"] + db,
                                sc["ign_dist_m"] * shrink)
            try:
                return s.ignite(p.lat, p.lon, intensity=sc["intensity"],
                                wind_dir_deg=sc["ign_bearing"])
            except ValueError:
                continue
    raise SystemExit("no ignition")


def edge(scen: str) -> None:
    sc = server.SCENARIOS[scen]
    for pw in (False, True):
        cfg = {"ros_floor_grass_only": True, "spotting_enabled": True,
               "grass_ros_floor_frac": 0.10, "sim_weather_profile": "city",
               "edge_weather_piecewise": pw}
        s = simui.SimSession(sc["location"], 1, 1, seed=f"scenario-{scen}",
                             cfg_overrides=cfg, roads_module=None)
        _ignite_like_server(s, sc)
        t0 = time.time()
        s.tick()
        t_first = time.time() - t0
        f = s._fires["sim-f1"]
        nom = f.ens.members[0].data
        cell_ac = (s._tile.spec.cell_m ** 2) / 4046.86
        row = [f"{h}h {sum(1 for v in nom if v <= f.t_ignite_s + h * 3600.0) * cell_ac:7.0f}"
               for h in (1, 3, 6, 12, 18, 24, 30, 36, 48)]
        print(f"{scen} piecewise={pw} first field {t_first:.1f}s | "
              + " | ".join(row), flush=True)


def sweep(scen: str) -> None:
    sc = server.SCENARIOS[scen]
    loc = simui.LOCATIONS[sc["location"]]
    mgr = server.SimSessionManager()
    code, out = mgr.new_session({"scenario": scen, "cfg": {
        "ros_floor_grass_only": True, "sim_weather_profile": "generic"}})
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    cfg = s._cfg
    cell_ac = s._tile.spec.cell_m ** 2 / 4046.86
    for spot in (False, True):
        for mph in (10, 15, 20, 22, 25, 28, 32, 40):
            wx = MockWeather(seed="x", base_wind_mps=mph * simui.MPH_TO_MPS,
                             dir_deg=f.wind_dir_deg, rh_pct=13.0, temp_c=30.0,
                             t0_s=simui.T0_S).forecast(loc.center,
                                                       cfg.horizon_h)
            c2 = ResponseConfig(**{k: getattr(cfg, k)
                                   for k in cfg.__dataclass_fields__})
            c2.spotting_enabled = spot
            a = spread.compute_arrival(s._tile, [f.origin], wx,
                                       f.t_ignite_s, c2,
                                       incident_id=f.incident_id)
            row = [f"{h}h {sum(1 for v in a.data if v <= f.t_ignite_s + h * 3600) * cell_ac:7.0f}"
                   for h in (1, 3, 6, 12)]
            print(f"{scen} spotting={spot!s:5s} {mph:3d} mph: "
                  + " | ".join(row), flush=True)


def tick(scen: str, intensity: str = None) -> None:
    timers = {"ens": 0.0, "plan": 0.0, "n_plan": 0}
    _ens, _plan = spread.ensemble_arrival, dispatch.plan

    def ens(*a, **k):
        t = time.time()
        r = _ens(*a, **k)
        timers["ens"] += time.time() - t
        return r

    def plan(*a, **k):
        t = time.time()
        r = _plan(*a, **k)
        timers["plan"] += time.time() - t
        timers["n_plan"] += 1
        return r

    spread.ensemble_arrival, dispatch.plan = ens, plan
    try:
        for prof in ("generic", "city"):
            mgr = server.SimSessionManager()
            code, out = mgr.new_session({"scenario": scen, "cfg": {
                "ros_floor_grass_only": True, "sim_weather_profile": prof}})
            s = mgr._entry(out["session_id"]).session
            f = s._fires["sim-f1"]
            inten = intensity or f.intensity
            if inten != f.intensity:
                lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
                s.reset()
                s.ignite(lat, lon, intensity=inten, wind_dir_deg=wd)
            timers.update(ens=0.0, plan=0.0, n_plan=0)
            t1 = time.time()
            s.tick()
            print(f"{scen} {prof}:{inten}: first tick {time.time() - t1:.1f}s"
                  f" (fire field {timers['ens']:.1f}s, dispatch "
                  f"{timers['plan']:.1f}s in {timers['n_plan']} planner "
                  f"calls)", flush=True)
    finally:
        spread.ensemble_arrival, dispatch.plan = _ens, _plan


if __name__ == "__main__":
    what = sys.argv[1]
    scens = (sys.argv[2].split(",") if len(sys.argv) > 2 else
             ["park_city_windstorm", "malibu_santa_ana",
              "eagle_gypsum_westerly"])
    for sc_key in scens:
        if what == "edge":
            edge(sc_key)
        elif what == "sweep":
            sweep(sc_key)
        else:
            tick(sc_key, sys.argv[3] if len(sys.argv) > 3 else None)
