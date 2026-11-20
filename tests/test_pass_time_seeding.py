"""tests/test_pass_time_seeding.py — A SATELLITE DETECTION BURNS FROM ITS PASS
TIME, OVER ITS PIXEL (2026-11-19, founder: "build footprint seeding at the
pass time next"; the 2026-11-01 screening build order, step 1; contracts
SAFETY SEMANTICS 65; cfg.hotspot_pass_time_seeding, hotspot_footprint_m;
watch --pass-time-seeding).

  SPREAD    a timed footprint seed burns from its pass time (clamped to
            now); every cell inside the pixel is seeded; without timed seeds
            the solve is unchanged.
  PIXEL     a detection whose centre cell is non-burnable still spreads
            from the burnable cells of its pixel (report C, experiment G).
  EARLIER   a cluster seen hours ago reaches a home earlier by about its
            age; ages 0-12 h, near and far: never later than the knob off.
  CARRIED   the FIRMS adapter keeps each pixel (scan x track) and pass time
            (off: no key); attachment carries them to the reported fire; a
            Confirm's merge keeps them; a hotspot without a time stays a
            seed at now.
  SURFACE   the flag and its note; config validation; knob off = the
            legacy call."""
from __future__ import annotations

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_hotspots import _cluster, _reported, _square  # noqa: E402
from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    StubTerrain,
    StubWeather,
    make_cfg,
    make_monitor,
)
from wgresponse import cli, decisions, feeds, geo, spread, threat  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    BBox,
    ClientProperty,
    LatLon,
    ResponseConfig,
    validate_config,
)


def _home(m_east):
    return ClientProperty("h", geo.destination(ORIGIN, 90.0, m_east),
                          name="Home")


def _aged_cluster(age_h, n=1):
    pts = [geo.destination(ORIGIN, 0.0, 60.0 * k) for k in range(n)]
    cl = _cluster(pts, t0=NOW - age_h * 3600.0)
    return cl


def _eta(cfg, inc, home):
    mon = make_monitor(feeds=[StubFeed([inc])], cfg=cfg,
                       registry=StubRegistry([home]))
    res = mon.cycle(NOW)
    a = [x for x in res.assessments if x.client_id == home.client_id]
    return (a[0].eta.eta_p10_s, a[0].eta.eta_p50_s) if a else (None, None)


# -- spread --------------------------------------------------------------------

def _tile(cells=60, cell_m=100.0, fuel=102.0):
    half = cells * cell_m / 2.0
    bb = geo.bbox_around([ORIGIN], half)
    return StubTerrain(fuel).tile(bb, cell_m)


def test_a_timed_seed_burns_from_its_pass_time_and_none_is_unchanged():
    cfg = ResponseConfig(horizon_h=12.0)
    tile = _tile()
    wx = StubWeather().forecast(ORIGIN, 24.0)
    far = geo.destination(ORIGIN, 90.0, 2000.0)
    base = spread.compute_arrival(tile, [ORIGIN], wx, NOW, cfg)
    same = spread.compute_arrival(tile, [ORIGIN], wx, NOW, cfg,
                                  timed_seeds=None)
    assert base.data == same.data                      # off: unchanged
    ring = threat._pass_time_seeds(
        _aged_cluster(0.0), [ORIGIN], NOW, cfg)[1][0][0]
    t6 = spread.compute_arrival(tile, [], wx, NOW, cfg,
                                timed_seeds=[(ring, NOW - 6 * 3600.0,
                                              ORIGIN)])
    rc = geo.latlon_to_rc(tile.spec, far)
    i = rc[0] * tile.spec.ncols + rc[1]
    assert t6.data[i] < base.data[i] - 3 * 3600.0       # hours earlier
    later = spread.compute_arrival(tile, [], wx, NOW, cfg,
                                   timed_seeds=[(ring, NOW + 3600.0,
                                                 ORIGIN)])
    assert later.data[i] <= base.data[i] + 1.0          # clamped to now


def test_the_pixel_spreads_when_its_centre_cell_cannot():
    cfg = ResponseConfig(horizon_h=6.0)
    tile = _tile()
    rc = geo.latlon_to_rc(tile.spec, ORIGIN)
    tile.fuel.data[rc[0] * tile.spec.ncols + rc[1]] = 98.0     # NB water
    wx = StubWeather().forecast(ORIGIN, 24.0)
    far = geo.destination(ORIGIN, 90.0, 1500.0)
    fi = geo.latlon_to_rc(tile.spec, far)
    j = fi[0] * tile.spec.ncols + fi[1]
    one = spread.compute_arrival(tile, [ORIGIN], wx, NOW, cfg)
    assert one.data[j] == float("inf")                  # C experiment G
    ring = threat._pass_time_seeds(
        _aged_cluster(0.0), [ORIGIN], NOW, cfg)[1][0][0]
    pix = spread.compute_arrival(tile, [], wx, NOW, cfg,
                                 timed_seeds=[(ring, NOW, ORIGIN)])
    assert pix.data[j] < float("inf")


# -- the monitor ---------------------------------------------------------------

def test_a_cluster_seen_hours_ago_arrives_earlier_by_about_its_age():
    home = _home(4000.0)
    off = make_cfg()
    on = make_cfg(hotspot_pass_time_seeding=True)
    cl = _aged_cluster(3.0)
    p10_off, p50_off = _eta(off, cl, home)
    p10_on, p50_on = _eta(on, cl, home)
    assert p10_off is not None and p10_on is not None
    shift_h = (p50_off - p50_on) / 3600.0
    assert 2.0 <= shift_h <= 3.6, shift_h               # ~ the 3 h age
    assert p10_on <= p10_off


@pytest.mark.parametrize("age_h", [0.0, 0.5, 2.0, 6.0, 12.0])
@pytest.mark.parametrize("east_m", [1500.0, 6000.0])
def test_never_later_than_the_knob_off(age_h, east_m):
    home = _home(east_m)
    cl = _aged_cluster(age_h, n=3)
    off = _eta(make_cfg(), cl, home)
    on = _eta(make_cfg(hotspot_pass_time_seeding=True), cl, home)
    if off[0] is None:
        return
    assert on[0] is not None
    assert on[0] <= off[0] + 1.0 and on[1] <= off[1] + 1.0


def test_a_hotspot_without_a_time_stays_a_seed_at_now():
    fire = dataclasses.replace(_reported(), hotspots=[ORIGIN])
    left, timed = threat._pass_time_seeds(fire, [ORIGIN], NOW,
                                          ResponseConfig())
    assert left == [ORIGIN] and timed == []
    cl = _aged_cluster(1.0)
    left, timed = threat._pass_time_seeds(cl, list(cl.hotspots), NOW,
                                          ResponseConfig())
    assert left == [] and len(timed) == 1
    ring, t, c = timed[0]
    assert t == pytest.approx(NOW - 3600.0) and c == cl.hotspots[0]
    # the default 375 m pixel: the ring spans 375 m east-west
    assert geo.haversine_m(ring[0], ring[1]) == pytest.approx(375.0, rel=0.02)


def test_scan_and_track_size_the_footprint():
    cl = _aged_cluster(1.0)
    p = cl.hotspots[0]
    key = f"{p.lat:.5f},{p.lon:.5f}"
    cl = dataclasses.replace(cl, meta=dict(cl.meta, seed_px={
        key: [NOW - 7200.0, 600.0, 450.0]}))
    _, timed = threat._pass_time_seeds(cl, list(cl.hotspots), NOW,
                                       ResponseConfig())
    ring, t, _ = timed[0]
    assert t == NOW - 7200.0                            # seed_px wins
    assert geo.haversine_m(ring[0], ring[1]) == pytest.approx(600.0, rel=0.02)
    assert geo.haversine_m(ring[1], ring[2]) == pytest.approx(450.0, rel=0.02)


# -- carried -------------------------------------------------------------------

CSV = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
       "satellite,confidence,version,bright_ti5,frp,daynight\n"
       "39.00000,-120.00000,330,0.52,0.41,2026-11-19,0130,N20,n,2,290,4.1,N\n"
       "39.00300,-120.00000,331,0.39,0.36,2026-11-19,0310,N21,n,2,291,5.0,N\n")


class _Get:
    def __call__(self, url, headers=None):
        return 200, CSV.encode()


def test_the_adapter_keeps_pixels_and_times_only_when_asked():
    clock = lambda: 1_795_062_000.0                       # noqa: E731
    on = feeds.FirmsFeed.live("KEY", http_get=_Get(), clock=clock,
                              footprints=True)
    off = feeds.FirmsFeed.live("KEY", http_get=_Get(), clock=clock)
    inc_on = on.fetch_active(BBox(-121.0, 38.0, -119.0, 40.0))
    inc_off = off.fetch_active(BBox(-121.0, 38.0, -119.0, 40.0))
    assert inc_on and inc_off
    assert "seed_px" not in (inc_off[0].meta or {})
    sp = inc_on[0].meta["seed_px"]
    rec = sp["39.00000,-120.00000"]
    assert rec[1] == pytest.approx(520.0) and rec[2] == pytest.approx(410.0)
    assert {k: v for k, v in inc_on[0].meta.items() if k != "seed_px"} == \
        inc_off[0].meta


def test_attachment_and_confirm_carry_the_pass_times():
    fire = dataclasses.replace(_reported(), perimeter=_square(ORIGIN, 400.0))
    p = geo.destination(ORIGIN, 0.0, 600.0)
    cl = _cluster([p], t0=NOW - 5400.0)
    key = f"{p.lat:.5f},{p.lon:.5f}"
    cl = dataclasses.replace(cl, meta=dict(cl.meta, seed_px={
        key: [NOW - 5400.0, 500.0, 400.0]}))
    on = dataclasses.replace(make_cfg(fuse_hotspots=True),
                             hotspot_pass_time_seeding=True)
    kept, _, _ = feeds.attach_hotspots([fire, cl], [], on)
    got = next(i for i in kept if i.incident_id == fire.incident_id)
    assert got.meta["seed_px"][key] == [NOW - 5400.0, 500.0, 400.0]
    kept_off, _, _ = feeds.attach_hotspots([fire, cl], [],
                                           make_cfg(fuse_hotspots=True))
    got_off = next(i for i in kept_off if i.incident_id == fire.incident_id)
    assert "seed_px" not in got_off.meta
    # a Confirm's merged fire keeps every pass time
    dec = decisions.OperatorDecisions()
    dec.confirm(cl, "Alex")
    out = dec.apply([cl], NOW, [], [])
    assert out[0].meta["seed_px"][key][0] == NOW - 5400.0


# -- surface ---------------------------------------------------------------------

def test_flag_note_and_validation():
    import argparse
    cfg, notes = cli._feeds_cfg(ResponseConfig(),
                                argparse.Namespace(pass_time_seeding=True))
    assert cfg.hotspot_pass_time_seeding
    assert any("SAFETY SEMANTICS 65" in n for n in notes)
    for bad in (50.0, 5000.0, float("nan")):
        assert any("hotspot_footprint_m" in x for x in validate_config(
            ResponseConfig(hotspot_footprint_m=bad)))
    assert not any("hotspot_footprint_m" in x for x in validate_config(
        ResponseConfig()))
