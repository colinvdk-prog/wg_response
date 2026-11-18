"""tests/test_keep_reported_point.py — A REPORTED POINT STAYS A SEED
(2026-11-18, the fire -> alert latency review's lane B finding B2; contracts
fuse_hotspots_keep_point, SAFETY SEMANTICS 50 amending 39; watch
--keep-reported-point).

  B2        lane B's geometry: night heat 2 km crosswind of a point-only
            agency record, a home 1.5 km downwind — p10 15.2 h with the
            live law (the heat replaced the point), 0.45 h with the point
            kept.
  NEVER     ETA(on) <= ETA(off) for every client of every world here
  LATER     (heat around the point at eight bearings and three ranges,
            homes at four bearings).
  SCOPE     a FIRMS-only cluster, a perimeter fire, a point without heat and
            a seeded fire seed exactly as before; the weather point stays
            the mean of the ignition points.
  LEARN     a fire with no home in range queues its learning forecast (40)
            with the point among the seeds.
  KEY       the kept-forecast fire_key holds the point and is stable from
            cycle to cycle (no refresh churn).
  SURFACE   the watch flag; config validation."""
from __future__ import annotations

import dataclasses
import math
from types import SimpleNamespace

import pytest

from tests.test_threat import (
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    StubWeather,
    make_cfg,
    make_monitor,
)
from wgresponse import cli, geo, threat
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    validate_config,
)


def _agency(origin=ORIGIN, **kw):
    kw.setdefault("source", IncidentSource.WFIGS)
    return FireIncident(incident_id="wf-1", name="Point Fire",
                        origin=origin, updated_at_s=NOW - 300.0,
                        irwin_id="wf-1", source_ids={"wfigs": "wf-1"}, **kw)


def _cluster(pts, cid="firms:c1"):
    return FireIncident(
        incident_id=cid, name="FIRMS cluster", source=IncidentSource.FIRMS,
        origin=LatLon(sum(p.lat for p in pts) / len(pts),
                      sum(p.lon for p in pts) / len(pts)),
        updated_at_s=NOW - 4 * 3600.0, hotspots=list(pts),
        source_ids={"firms": cid},
        meta={"n_detections": len(pts),
              "hotspot_t_s": [NOW - 4 * 3600.0] * len(pts)})


def _cfg(keep, **kw):
    return make_cfg(fuse_hotspots=True, fuse_hotspots_keep_point=keep, **kw)


def _etas(keep, incs, homes, **kw):
    weather = StubWeather()
    cfg = _cfg(keep, **kw)
    mon = make_monitor(feeds=[StubFeed(incs)], cfg=cfg,
                       registry=StubRegistry(homes), weather=weather,
                       alert_engine=AlertEngine(cfg))
    r = mon.cycle(NOW)
    return ({(a.client_id, a.incident_id): (a.eta.eta_p10_s, a.eta.eta_p50_s)
             for a in r.assessments}, weather, r, mon)


def _homes(dists=(1500.0, 4000.0)):
    out = []
    for brg in (0.0, 90.0, 180.0, 270.0):
        for d in dists:
            out.append(ClientProperty(f"h-{int(brg)}-{int(d)}",
                                      geo.destination(ORIGIN, brg, d)))
    return out


def test_lane_b_geometry_the_point_burns_again():
    north = geo.destination(ORIGIN, 0.0, 2000.0)
    heat = _cluster([north, geo.destination(north, 90.0, 375.0)])
    home = ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 1500.0))
    off, _, r_off, _ = _etas(False, [_agency(), heat], [home])
    on, _, r_on, _ = _etas(True, [_agency(), heat], [home])
    key = ("home-1", "wf-1")
    p10_off = (off[key][0] - NOW) / 3600.0
    p10_on = (on[key][0] - NOW) / 3600.0
    # live flags: 15.2 h -> 0.45 h (scratch_review/.../origin_kept.py)
    assert p10_off > 10.0 and p10_on < 2.0
    # the heat joined the record in both (SEMANTICS 39 unchanged)
    for r in (r_off, r_on):
        inc = next(i for i in r.incidents if i.incident_id == "wf-1")
        assert len(inc.hotspots) == 2


@pytest.mark.parametrize("rng", [800.0, 1800.0, 2800.0])
def test_never_later_for_any_client(rng):
    homes = _homes()
    for brg in range(0, 360, 45):
        heat = _cluster([geo.destination(ORIGIN, float(brg), rng)])
        off, _, _, _ = _etas(False, [_agency(), heat], homes)
        on, _, _, _ = _etas(True, [_agency(), heat], homes)
        assert set(off) <= set(on)              # never a home fewer
        for key, (p10, p50) in off.items():
            assert on[key][0] <= p10, (brg, rng, key)
            assert on[key][1] <= p50, (brg, rng, key)


def test_the_weather_point_stays_the_mean_of_the_ignition_points():
    heat = _cluster([geo.destination(ORIGIN, 0.0, 2000.0)])
    home = ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 1500.0))
    _, w_off, _, _ = _etas(False, [_agency(), heat], [home])
    _, w_on, _, _ = _etas(True, [_agency(), heat], [home])
    assert [c[0] for c in w_on.calls] == [c[0] for c in w_off.calls]


def _same(incs, homes, **kw):
    off, w_off, _, _ = _etas(False, incs, homes, **kw)
    on, w_on, _, _ = _etas(True, incs, homes, **kw)
    assert off == on and off
    assert [c[0] for c in w_on.calls] == [c[0] for c in w_off.calls]


def test_a_firms_only_cluster_is_unchanged():
    # its origin is the mean of its own detections: never a seed
    heat = _cluster([geo.destination(ORIGIN, 0.0, 600.0),
                     geo.destination(ORIGIN, 0.0, 1400.0)])
    _same([heat], _homes())


def test_a_perimeter_fire_and_a_point_without_heat_are_unchanged():
    ring = [geo.destination(ORIGIN, b, 400.0) for b in (0, 90, 180, 270)]
    per = _agency(perimeter=[ring + ring[:1]],
                  hotspots=[geo.destination(ORIGIN, 0.0, 300.0)])
    _same([per], _homes())
    _same([_agency()], _homes())


def test_the_seed_law_by_source():
    mon = make_monitor(cfg=_cfg(True))
    pts = [geo.destination(ORIGIN, 0.0, 2000.0)]
    ign, _, _, _, seeds, geom = mon._seed_geometry(
        _agency(hotspots=pts), NOW)
    assert ign == pts and seeds == [ORIGIN] + pts and geom == seeds
    for inc in (_cluster(pts),
                _agency(hotspots=pts, source=IncidentSource.SEEDED),
                dataclasses.replace(_agency(hotspots=pts),
                                    incident_id="seeded:abc")):
        ign, _, _, _, seeds, geom = mon._seed_geometry(inc, NOW)
        assert seeds == ign == geom == inc.ignition_points()
    off = make_monitor(cfg=_cfg(False))
    assert off._seed_geometry(_agency(hotspots=pts), NOW)[4] == pts


def test_a_fire_without_homes_learns_with_the_point_among_its_seeds():
    """SAFETY SEMANTICS 40: the learning forecast seeds what a domain
    forecast would have (the no-domain row carries the seed geometry)."""
    pts = [geo.destination(ORIGIN, 0.0, 2000.0)]
    far = ClientProperty("far", geo.destination(ORIGIN, 90.0, 200_000.0))
    mon = make_monitor(cfg=_cfg(True), registry=StubRegistry([far]))
    rows: list = []
    mon._assess_incident(_agency(hotspots=pts), NOW, [], {}, {}, {}, [],
                         no_domain=rows)
    (inc, ign, perimeter, _, _, seeds, geom) = rows[0]
    assert perimeter is None and ign == pts
    assert seeds == [ORIGIN] + pts and geom == [ORIGIN] + pts


def test_the_kept_forecast_key_holds_the_point_and_stays_put():
    north = geo.destination(ORIGIN, 0.0, 2000.0)
    home = ClientProperty("home-1", geo.destination(ORIGIN, 90.0, 1500.0))
    inc = _agency(hotspots=[north])
    cfg = _cfg(True, forecast_tiers=True)
    mon = make_monitor(feeds=[StubFeed([inc])], cfg=cfg,
                       registry=StubRegistry([home]),
                       alert_engine=AlertEngine(cfg))
    r1 = mon.cycle(NOW)
    assert r1.timings["incidents"]["wf-1"]["forecast"] == "computed"
    key = mon._kept["wf-1"].fire_key
    assert key == threat._fire_key(None, None, [ORIGIN, north])
    r2 = mon.cycle(NOW + 300.0)
    assert r2.timings["incidents"]["wf-1"]["forecast"] == "kept"
    assert mon._kept["wf-1"].fire_key == key


def test_default_off_validated_and_armed_by_the_watch_flag():
    assert ResponseConfig().fuse_hotspots_keep_point is False
    assert validate_config(ResponseConfig(fuse_hotspots_keep_point=True)) \
        == []
    assert any("fuse_hotspots_keep_point" in p for p in validate_config(
        ResponseConfig(fuse_hotspots_keep_point=1)))
    args = cli._build_parser().parse_args(cli._normalize_argv(
        ["watch", "--aoi", "x.json", "--keep-reported-point"]))
    cfg, notes = cli._latency_cfg(ResponseConfig(), args)
    assert cfg.fuse_hotspots_keep_point is True
    assert any("SAFETY SEMANTICS 50" in n for n in notes)
    cfg, _ = cli._latency_cfg(ResponseConfig(), SimpleNamespace())
    assert cfg.fuse_hotspots_keep_point is False
    assert math.isfinite(NOW)
