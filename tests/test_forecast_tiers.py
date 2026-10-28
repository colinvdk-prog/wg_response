"""tests/test_forecast_tiers.py — FORECAST FRESHNESS TIERS and the TERRAIN
FALLBACK (founder 2026-10-28, verbatim: "for the live map, have a graduated
level of stale. for a fire under control, stale is 6 hours, for a fire that
is growing stale is 1 hour, for a fire that has at least medium risk that
has clients in its path, stale is 15 minutes"; contracts forecast_tiers;
watch --forecast-tiers / --terrain-fallback).

  KNOBS     off by default (every cycle re-forecasts every incident, as
            before); limits validated.
  KEEP      a kept forecast is served while its inputs are unchanged and it
            is younger than its tier's limit less the margin; the protected
            homes in range changing re-forecasts in the cycle; the fire's
            geometry changing or its age reaching the limit refreshes it on
            the background worker while the previous one is served; a
            refresh whose inputs fail is a provider error and the kept
            forecast stays served; an incident gone from the active set
            drops its forecast.
  TIERS     a client pair at WATCH or above -> threat (15 min); every
            source >= 90 % contained and no growth since the forecast ->
            controlled (6 h); otherwise growing (1 h).
  SURFACE   CycleResult.freshness, the world summary rows, healthz's
            fires_stale; the page names stale fires and condenses provider
            errors.
  FALLBACK  a failing live terrain provider is replaced by the finest
            bundled tile covering the bbox (noted per fire); none covering
            = the failure stands.

Monitor tests run on tests/test_threat.py's stubs (uniform grass, a steady
west wind, a client 1.5 km downwind)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tests.test_threat import (
    NOW,
    ORIGIN,
    RaisingTerrain,
    StubAlertEngine,
    StubFeed,
    StubRegistry,
    StubTerrain,
    far_client,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import geo, server, terrain, worldview
from wgresponse.contracts import (
    AlertState,
    BBox,
    ClientProperty,
    CycleResult,
    ResponseConfig,
    validate_config,
)
from wgresponse.terrain import BundledFallbackTerrain, SyntheticTerrain

PAGE = Path(__file__).resolve().parent.parent / "console" / "world.html"
MIN = 60.0


def _tiers(**kw):
    return make_cfg(forecast_tiers=True, **kw)


def _mon(cfg=None, feeds=None, terrain_p=None, engine=None, clients=None):
    return make_monitor(
        feeds=feeds, terrain=terrain_p or StubTerrain(),
        alert_engine=engine,
        registry=StubRegistry(clients or [near_client(), far_client()]),
        cfg=cfg or _tiers())


def _settle(mon, iid="inc-1"):
    """Let the background refresh in flight finish (the next cycle
    collects it)."""
    k = mon._kept[iid]
    if k.pending is not None:
        k.pending.result(timeout=120)


def _eta(res):
    (a,) = res.assessments
    return a.eta


# ---------------------------------------------------------------------------
# KNOBS
# ---------------------------------------------------------------------------

def test_knobs_are_off_by_default_and_validated():
    cfg = ResponseConfig()
    assert cfg.forecast_tiers is False
    assert (cfg.forecast_stale_threat_min, cfg.forecast_stale_growing_min,
            cfg.forecast_stale_controlled_min) == (15.0, 60.0, 360.0)
    assert cfg.forecast_controlled_containment_pct == 90.0
    assert cfg.forecast_refresh_margin_min == 5.0
    assert validate_config(cfg) == []
    for bad in ({"forecast_stale_threat_min": 0.0},
                {"forecast_stale_controlled_min": -1.0},
                {"forecast_refresh_margin_min": -1.0},
                {"forecast_controlled_containment_pct": 101.0}):
        assert validate_config(dataclasses.replace(cfg, **bad)), bad


def test_off_every_cycle_reforecasts_and_reports_no_freshness():
    t = StubTerrain()
    mon = _mon(cfg=make_cfg(), terrain_p=t)
    r1 = mon.cycle(NOW)
    r2 = mon.cycle(NOW + 5 * MIN)
    assert len(t.calls) == 2
    assert r1.freshness == {} and r2.freshness == {}
    assert r1.ensembles["inc-1"] is not r2.ensembles["inc-1"]


# ---------------------------------------------------------------------------
# KEEP
# ---------------------------------------------------------------------------

def test_a_kept_forecast_is_served_until_its_limit_then_refreshed():
    t = StubTerrain()
    mon = _mon(terrain_p=t)
    r1 = mon.cycle(NOW)
    f = r1.freshness["inc-1"]
    assert (f["tier"], f["limit_s"], f["age_s"], f["refreshing"],
            f["stale"]) == ("growing", 3600.0, 0.0, False, False)
    assert f["why"] == "containment not reported"
    assert len(t.calls) == 1
    # 5 and 50 minutes on: the same forecast, no terrain / spread call
    for dt in (5, 50):
        r = mon.cycle(NOW + dt * MIN)
        assert len(t.calls) == 1
        assert r.ensembles["inc-1"] is r1.ensembles["inc-1"]
        assert r.freshness["inc-1"]["age_s"] == dt * MIN
        assert _eta(r) == _eta(r1)
        assert r.timings["incidents"]["inc-1"]["forecast"] == "kept"
    # 55 min = the 60-min limit less the 5-min margin: refresh on the
    # background worker; the old forecast is still the one served
    r = mon.cycle(NOW + 55 * MIN)
    assert len(t.calls) == 2
    assert r.ensembles["inc-1"] is r1.ensembles["inc-1"]
    assert r.freshness["inc-1"]["refreshing"] is True
    assert r.timings["incidents"]["inc-1"]["forecast"].startswith(
        "refreshing (55 min old (limit 60 min))")
    _settle(mon)
    r = mon.cycle(NOW + 60 * MIN)
    assert r.ensembles["inc-1"] is not r1.ensembles["inc-1"]
    f = r.freshness["inc-1"]
    assert f["computed_at_s"] == NOW + 55 * MIN
    assert (f["age_s"], f["refreshing"], f["stale"]) == (5 * MIN, False, False)


def test_new_homes_in_range_reforecast_in_the_cycle():
    reg_clients = [near_client(), far_client()]
    reg = StubRegistry(reg_clients)
    t = StubTerrain()
    mon = make_monitor(terrain=t, registry=reg, cfg=_tiers())
    r1 = mon.cycle(NOW)
    reg_clients.append(ClientProperty(
        client_id="c-new", loc=geo.destination(ORIGIN, 90.0, 3000.0)))
    r2 = mon.cycle(NOW + 5 * MIN)
    assert len(t.calls) == 2                      # computed now, not later
    assert r2.ensembles["inc-1"] is not r1.ensembles["inc-1"]
    assert {a.client_id for a in r2.assessments} == {"c-near", "c-new"}
    assert r2.freshness["inc-1"]["age_s"] == 0.0
    assert r2.timings["incidents"]["inc-1"]["forecast"] == "computed"


def test_new_fire_geometry_refreshes_in_the_background():
    inc = incident()
    feed = StubFeed([inc])
    t = StubTerrain()
    mon = _mon(feeds=[feed], terrain_p=t)
    r1 = mon.cycle(NOW)
    moved = dataclasses.replace(
        inc, hotspots=[geo.destination(ORIGIN, 90.0, 400.0)])
    feed._incidents = [moved]
    r2 = mon.cycle(NOW + 5 * MIN)
    assert len(t.calls) == 2
    assert r2.ensembles["inc-1"] is r1.ensembles["inc-1"]      # served
    assert r2.timings["incidents"]["inc-1"]["forecast"].startswith(
        "refreshing (the fire's geometry changed")
    _settle(mon)
    r3 = mon.cycle(NOW + 10 * MIN)
    assert r3.ensembles["inc-1"] is not r1.ensembles["inc-1"]
    assert r3.freshness["inc-1"]["computed_at_s"] == NOW + 5 * MIN


def test_a_refresh_that_cannot_get_its_inputs_keeps_the_forecast_served():
    class FlakyTerrain(StubTerrain):
        fail = False

        def tile(self, bbox, cell_m):
            if self.fail:
                raise RuntimeError("LFPS job failed")
            return super().tile(bbox, cell_m)

    t = FlakyTerrain()
    mon = _mon(terrain_p=t)
    r1 = mon.cycle(NOW)
    t.fail = True
    r2 = mon.cycle(NOW + 58 * MIN)
    assert r2.ensembles["inc-1"] is r1.ensembles["inc-1"]
    assert [a.client_id for a in r2.assessments] == ["c-near"]
    assert r2.degraded is True
    (ev,) = [e for e in r2.operator_events if e.kind == "provider_error"]
    assert ev.incident_id == "inc-1" and ev.detail == {"step": "terrain",
                                                      "kept": True}
    assert "previous forecast stays served" in ev.message
    r3 = mon.cycle(NOW + 62 * MIN)
    assert r3.freshness["inc-1"]["stale"] is True      # past its 60 min


def test_an_incident_gone_drops_its_kept_forecast():
    feed = StubFeed([incident()])
    mon = _mon(feeds=[feed])
    mon.cycle(NOW)
    assert "inc-1" in mon._kept
    feed._incidents = []
    mon.cycle(NOW + 5 * MIN)
    assert mon._kept == {}


# ---------------------------------------------------------------------------
# TIERS
# ---------------------------------------------------------------------------

def test_clients_at_watch_or_above_put_the_fire_in_the_threat_tier():
    for st, tier in ((AlertState.NOTICE_72H, "growing"),
                     (AlertState.WATCH, "threat"),
                     (AlertState.EXTREME_24H, "threat"),
                     (AlertState.IMPACTED, "threat")):
        engine = StubAlertEngine(states={("c-near", "inc-1"): st})
        r = _mon(engine=engine).cycle(NOW)
        f = r.freshness["inc-1"]
        assert f["tier"] == tier, st
        if tier == "threat":
            assert f["limit_s"] == 15 * MIN
            assert f["why"] == "1 client(s) at watch or above"


def test_a_contained_fire_not_growing_is_controlled_until_it_grows():
    inc = dataclasses.replace(incident(), containment_pct=95.0,
                              acreage=500.0)
    feed = StubFeed([inc])
    mon = _mon(feeds=[feed])
    f = mon.cycle(NOW).freshness["inc-1"]
    assert (f["tier"], f["limit_s"]) == ("controlled", 360 * MIN)
    assert f["why"] == "95% contained, not growing"
    feed._incidents = [dataclasses.replace(inc, acreage=650.0)]
    f = mon.cycle(NOW + 5 * MIN).freshness["inc-1"]
    assert (f["tier"], f["limit_s"]) == ("growing", 60 * MIN)
    assert "grew" in f["why"]
    feed._incidents = [dataclasses.replace(inc, containment_pct=60.0)]
    f = mon.cycle(NOW + 10 * MIN).freshness["inc-1"]
    assert (f["tier"], f["why"]) == ("growing", "60% contained")


def test_a_controlled_fire_keeps_its_forecast_for_hours():
    inc = dataclasses.replace(incident(), containment_pct=92.0,
                              acreage=7359.0)
    t = StubTerrain()
    mon = _mon(feeds=[StubFeed([inc])], terrain_p=t)
    r1 = mon.cycle(NOW)
    r = mon.cycle(NOW + 5 * 60 * MIN)                 # 5 h on
    assert len(t.calls) == 1
    assert r.ensembles["inc-1"] is r1.ensembles["inc-1"]
    assert r.freshness["inc-1"]["stale"] is False
    mon.cycle(NOW + 355 * MIN)                        # 6 h less 5 min
    assert len(t.calls) == 2


# ---------------------------------------------------------------------------
# SURFACE
# ---------------------------------------------------------------------------

def test_the_summary_and_healthz_carry_each_fires_freshness():
    mon = _mon()
    res = mon.cycle(NOW)
    state = {"forecast_tiers": worldview.forecast_tiers_rule(mon._cfg)}
    later = dataclasses.replace(res, t_s=NOW + 70 * MIN)
    summ = worldview.build_summary(state, later, geometry=False)
    (row,) = summ["incidents"]
    assert row["forecast"]["tier"] == "growing"
    assert row["forecast"]["age_min"] == 70.0
    assert row["forecast"]["limit_min"] == 60.0
    assert row["forecast"]["stale"] is True
    assert summ["forecast_tiers"]["controlled_min"] == 360.0
    # not armed: no forecast key at all
    assert "forecast" not in worldview.build_summary({}, res,
                                                     geometry=False)[
        "incidents"][0]
    app = server._App(state_provider=lambda: {
        "degraded": False, "stale_fires": [{"incident_id": "inc-1"}]})
    assert server._Handler._health_payload(
        type("H", (), {"app": app})())["fires_stale"] == 1


def test_the_page_names_stale_fires_and_condenses_provider_errors():
    src = PAGE.read_text(encoding="utf-8")
    assert "d.stale_fires" in src
    assert "past " in src and "limit:" in src
    assert "unavailable for " in src             # provider errors condensed
    assert "bar.title = detail.join" in src      # the services' words on hover
    assert "function freshChip" in src and "freshChip(r)" in src


# ---------------------------------------------------------------------------
# FALLBACK
# ---------------------------------------------------------------------------

def _bundled_dir(tmp_path, bbox):
    d = tmp_path / "tiles"
    d.mkdir()
    tile = SyntheticTerrain(seed="fallback").tile(bbox, 200.0)
    terrain.save_ascii_tile(tile, str(d / "area_ext.wgt"))
    return d


def test_a_failing_live_terrain_falls_back_to_a_covering_bundled_tile(
        tmp_path):
    big = geo.bbox_around([ORIGIN], 15_000.0)
    d = _bundled_dir(tmp_path, big)
    fb = BundledFallbackTerrain(RaisingTerrain(), d)
    small = geo.bbox_around([ORIGIN], 4_000.0)
    t = fb.tile(small, 150.0)
    assert t.spec.cell_m == 200.0
    (name, err), = fb.pop_fallbacks()
    assert name == "area_ext.wgt" and err == "landfire down"
    assert fb.pop_fallbacks() == []
    # nothing covers a bbox beyond the bundled tile: the failure stands
    far = geo.bbox_around([geo.destination(ORIGIN, 0.0, 60_000.0)], 4_000.0)
    with pytest.raises(RuntimeError, match="landfire down"):
        fb.tile(far, 150.0)
    # a healthy live provider is never bypassed
    ok = BundledFallbackTerrain(StubTerrain(), d)
    assert ok.tile(small, 150.0).source == "stub"
    assert ok.pop_fallbacks() == []


def test_the_monitor_spreads_on_the_fallback_and_says_so(tmp_path):
    d = _bundled_dir(tmp_path, geo.bbox_around([ORIGIN], 40_000.0))
    mon = make_monitor(terrain=BundledFallbackTerrain(RaisingTerrain(), d),
                       cfg=make_cfg())
    res = mon.cycle(NOW)
    assert res.degraded is False
    assert [a.client_id for a in res.assessments] == ["c-near"]
    assert any("terrain from the bundled LANDFIRE tile area_ext.wgt" in n
               for n in res.notes)
    assert res.timings["incidents"]["inc-1"]["terrain_fallback"] == \
        "area_ext.wgt"


def test_the_watch_flags_arm_the_tiers_and_the_fallback(monkeypatch,
                                                        tmp_path):
    from wgresponse import cli
    monkeypatch.setattr(terrain, "probe_live_decode_deps", lambda: None)
    parser = cli._build_parser()
    args = parser.parse_args(["watch", "--aoi", "data/aoi_conus.json",
                              "--forecast-tiers", "--terrain-fallback"])
    assert args.forecast_tiers is True and args.terrain_fallback is True
    provider, notes = cli._watch_terrain(args, str(tmp_path))
    assert isinstance(provider, BundledFallbackTerrain)
    assert any(n.startswith("TERRAIN FALLBACK") for n in notes)
    args = parser.parse_args(["watch", "--aoi", "data/aoi_conus.json"])
    provider, _notes = cli._watch_terrain(args, str(tmp_path))
    assert not isinstance(provider, BundledFallbackTerrain)
