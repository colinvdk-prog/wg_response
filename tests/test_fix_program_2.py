"""Fix program 2026-09-11 (docs/FIX_PROGRAM_2026-09-11.md): the regime-
conditional floor (contracts extreme_regime_floor_frac +
threat.regime_spread_cfg + the hindcast replay hook), cycle-phase timings
(CycleResult.timings / state()["last_cycle_phases"]) and the deterministic
solver budget (contracts solver_budget_evals, dispatch._Budget)."""
from __future__ import annotations

import dataclasses

import pytest

from wgresponse import dispatch, geo, threat
from wgresponse.contracts import (
    GridSpec,
    ResponseConfig,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)

T0 = 1_750_000_000.0
NORTH, WEST = 39.5, -121.5


def _tile(fuel_code=102.0, n=21):
    spec = GridSpec(north_lat=NORTH, west_lon=WEST, cell_m=100.0, nrows=n, ncols=n)
    return TerrainTile(spec=spec, fuel=geo.make_raster(spec, fill=float(fuel_code)),
                       slope_deg=geo.make_raster(spec, fill=0.0),
                       aspect_deg=geo.make_raster(spec, fill=0.0),
                       elev_m=geo.make_raster(spec, fill=100.0), source="test")


def _wx(loc, wind_mps=10.0):
    samples = [WeatherSample(t_s=T0 + i * 3600.0, wind_speed_mps=wind_mps,
                             wind_gust_mps=wind_mps * 1.3, wind_dir_deg=270.0,
                             rh_pct=20.0, temp_c=30.0) for i in range(48)]
    return WeatherForecast(loc=loc, samples=samples, source="test")


# --------------------------------------------------------------- regime floor

def test_regime_cfg_is_identity_when_posture_off():
    tile = _tile()
    wx = _wx(geo.rc_to_latlon(tile.spec, 10, 10))
    cfg = ResponseConfig(extreme_regime_floor_frac=0.10)     # knob but no posture
    out, flag = threat.regime_spread_cfg(tile, wx, T0, cfg)
    assert out is cfg and flag is False


def test_regime_cfg_lifts_floor_on_regime_incident_only():
    grass = _tile(102.0)                        # GR2 sea: regime fires
    timber = _tile(183.0)                       # TL3: no regime
    wx = _wx(geo.rc_to_latlon(grass.spec, 10, 10))
    cfg = ResponseConfig(extreme_regime_enabled=True, grass_ros_floor_frac=0.05,
                         ros_floor_grass_only=True, ros_floor_extinction_gate=True,
                         extreme_regime_floor_frac=0.10)
    out, flag = threat.regime_spread_cfg(grass, wx, T0, cfg)
    assert flag is True
    assert out.grass_ros_floor_frac == 0.10 and out.ros_floor_grass_only is False
    assert out.ros_floor_extinction_gate is True          # untouched
    assert cfg.grass_ros_floor_frac == 0.05               # caller's config intact
    out2, flag2 = threat.regime_spread_cfg(timber, wx, T0, cfg)
    assert flag2 is False and out2 is cfg


def test_regime_cfg_knob_zero_keeps_calibrated_band_even_in_regime():
    grass = _tile(102.0)
    wx = _wx(geo.rc_to_latlon(grass.spec, 10, 10))
    cfg = ResponseConfig(extreme_regime_enabled=True, grass_ros_floor_frac=0.05,
                         ros_floor_grass_only=True)
    out, flag = threat.regime_spread_cfg(grass, wx, T0, cfg)
    assert flag is True and out is cfg


def test_harness_replay_records_regime_note(tmp_path):
    from tools import hindcast_lib as H
    tile = _tile(102.0)
    wx = _wx(geo.rc_to_latlon(tile.spec, 10, 10))
    c = geo.rc_to_latlon(tile.spec, 10, 10)
    ring = [geo.destination(c, b, 250.0) for b in (0, 90, 180, 270)]
    ring1 = [geo.destination(c, b, 900.0) for b in (0, 90, 180, 270)]
    s0 = H.PerimeterSnapshot(t_s=T0, rings=[ring], acres=None) \
        if "acres" in H.PerimeterSnapshot.__dataclass_fields__ \
        else H.PerimeterSnapshot(t_s=T0, rings=[ring])
    s1 = dataclasses.replace(s0, t_s=T0 + 6 * 3600.0, rings=[ring1])
    cfg = ResponseConfig(ensemble_n=2, extreme_regime_enabled=True,
                         grass_ros_floor_frac=0.05, ros_floor_grass_only=True,
                         extreme_regime_floor_frac=0.10)
    m = H.replay_pair(tile, wx, s0, s1, cfg, "inc-r", name="r")
    assert any("blow-up floor 0.1 applied" in n for n in m.notes)
    off = H.replay_pair(tile, wx, s0, s1,
                        dataclasses.replace(cfg, extreme_regime_floor_frac=0.0),
                        "inc-r", name="r")
    assert any("physics unchanged" in n for n in off.notes)
    assert not any("applied" in n for n in off.notes)


def test_harness_flags_map_to_config():
    import argparse
    from tools import hindcast_harness as HH
    ns = argparse.Namespace(gust_blend=0.5, grass_floor=0.05, floor_gate=True,
                            grass_only=True, model_error=0.71, p50_bound=False,
                            spotting=False, extreme_regime=True, regime_floor=0.10,
                            edge_piecewise=True, phi_vector=False)
    kw = HH._variant_config_kwargs(ns)
    cfg = dataclasses.replace(ResponseConfig(), **kw)
    assert cfg.extreme_regime_enabled and cfg.extreme_regime_floor_frac == 0.10
    assert cfg.edge_weather_piecewise is True and cfg.slope_phi_vector is False


# ------------------------------------------------------------ cycle timings

def test_cycle_result_carries_phase_timings(tmp_path):
    from tests.test_cli import _daemon
    daemon, _feed, _engine, _notifier, _store = _daemon(tmp_path)
    res = daemon.run_cycle()
    t = res.timings
    for key in ("feeds_s", "assess_s", "alerts_s", "dispatch_s"):
        assert key in t and t[key] >= 0.0
    assert "incidents" in t
    for row in t["incidents"].values():
        assert row["spread_s"] >= 0.0 and row["cells"] > 0
    assert daemon.state()["last_cycle_phases"]["feeds_s"] == t["feeds_s"]


def _ignite_burnable(s, dist0_m=6000.0, intensity="high"):
    """Ignite on the first BURNABLE cell along the hazard-wind bearing from
    dist0_m outward (real tiles put non-burnable developed cells upwind of
    some centers)."""
    from wgresponse import geo
    loc = s._loc
    for d in range(int(dist0_m), 15001, 500):
        for db in (0.0, 10.0, -10.0, 20.0, -20.0):
            p = geo.destination(loc.center, loc.hazard_wind_dir_deg + db, float(d))
            try:
                s.ignite(p.lat, p.lon, intensity=intensity)
                return p
            except ValueError:
                continue
    raise RuntimeError("no burnable ignition cell found")


# ------------------------------------------------- the production start command

PRODUCTION_WATCH_ARGS = (
    "watch --aoi data/aoi_socal_shadow.json --state-dir /tmp/wgstate "
    "--registry mock --dry-notify --gust-blend 0.5 --grass-floor 0.10 "
    "--floor-gate --model-error 0.71 --extreme-regime --fail-closed "
    "--ember-radius 200 --p50-bound --solver-evals 20000 --phi-vector "
    "--host 0.0.0.0 --port 8080").split()


def test_watch_parser_accepts_the_production_start_command():
    """Every flag the live App Runner StartCommand carries must parse — a
    flag missing from the watch parser makes the daemon exit 2 before it
    listens, App Runner's health check fails and the deploy rolls back
    (2026-09-11: --phi-vector existed on the hindcast harness but not on
    watch). Keep this list identical to docs/MODEL_ASSESSMENT_HANDOFF.md §3."""
    from wgresponse.cli import _build_parser
    args = _build_parser().parse_args(PRODUCTION_WATCH_ARGS)
    assert args.cmd == "watch" and args.phi_vector is True
    assert args.solver_evals == 20000 and args.model_error == 0.71
    assert args.floor_gate and args.fail_closed and args.p50_bound
    # the knobs that are built but deliberately NOT armed live
    assert args.edge_piecewise is False and args.regime_floor == 0.0
    # ... incl. SAFETY SEMANTICS 13 (2026-09-27): built, owner arms them
    assert args.drop_contained is False and args.confirmed_perimeters is False
    # ... and SAFETY SEMANTICS 15 (2026-09-27): built, owner arms it
    assert args.offmap_per_fire is False
    # every optional watch flag is parseable together
    extra = PRODUCTION_WATCH_ARGS + ["--edge-piecewise", "--regime-floor", "0.10",
                                     "--grass-only", "--drop-contained",
                                     "--confirmed-perimeters",
                                     "--offmap-per-fire"]
    parsed = _build_parser().parse_args(extra)
    assert parsed.edge_piecewise is True
    assert parsed.drop_contained is True and parsed.confirmed_perimeters is True
    assert parsed.offmap_per_fire is True


# ------------------------------------------------------- solver determinism

def test_budget_object_modes():
    b = dispatch._Budget(1e18, 0)
    assert b.counted is False and b.expired() is False and b.spent == 1
    c = dispatch._Budget(1e18, 3)
    assert [c.expired() for _ in range(5)] == [False, False, False, True, True]
    assert c.spent == 5 and c.runaway is False


def test_counted_budget_is_reported_and_identical_run_to_run(tmp_path):
    from tests.test_dispatch import _fingerprint  # noqa: F401  (import guard)
    from wgresponse.simui import SimSession
    plans = []
    for _ in range(2):
        s = SimSession("boulder_co", 12, 2, seed="det",
                       cfg_overrides={"solver_budget_evals": 5000,
                                      "ensemble_n": 3, "grid_cell_m": 500.0,
                                      "horizon_h": 24.0})
        _ignite_burnable(s)
        st = None
        for _i in range(3):
            st = s.tick()
        plans.append(st["plan"])
    assert plans[0] == plans[1]


def test_tiny_counted_budget_still_yields_a_lawful_plan():
    from wgresponse.simui import SimSession
    s = SimSession("boulder_co", 12, 2, seed="tiny",
                   cfg_overrides={"solver_budget_evals": 3, "ensemble_n": 3,
                                  "grid_cell_m": 500.0, "horizon_h": 24.0})
    _ignite_burnable(s)
    st = s.tick()
    assert st["scoreboard"]["violations"] == len(st["crew_overruns"])
