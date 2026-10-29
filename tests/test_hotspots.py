"""tests/test_hotspots.py — the FIRMS live mode and HOTSPOT ATTACHMENT
(founder 2026-10-29, "get the FIRMS key added and redeploy"; contracts
SAFETY SEMANTICS 39; cfg.fuse_hotspots; watch --fuse-hotspots).

  LIVE MODE  the NOAA-20 and NOAA-21 VIIRS (never S-NPP: its data ends
             2026-11-02), DAY_RANGE 2 kept to the last 24 h by
             the clock (DAY_RANGE 1 is TODAY in UTC only), each hotspot's
             time in meta, a failed source is a blind fetch that keeps the
             others' detections, cluster ids kept across fetches; legacy
             construction unchanged.
  CLUSTERS   the grid-indexed greedy clustering is the plain scan's result
             exactly (random data, every fallback).
  ATTACH     a detection inside or near a reported fire's footprint joins
             it (perimeter + margin, point radius by size, a removed
             contained fire's interior only); the nearest footprint wins;
             the rest of a cluster stays a FIRMS fire under its id; seeded,
             FIRMS and inactive records are never footprints.
  MONITOR    off = the legacy cycle; on = joins + one note; a failure keeps
             every cluster a fire.
  SURFACE    the watch flag, the startup note, the summary rows, the page,
             config validation."""
from __future__ import annotations

import dataclasses
import math
import random
from pathlib import Path

import pytest

from tests.test_threat import (
    NOW,
    ORIGIN,
    StubFeed,
    StubRegistry,
    far_client,
    incident,
    make_cfg,
    make_monitor,
    near_client,
)
from wgresponse import feeds, geo, worldview
from wgresponse.contracts import (
    BBox,
    FireIncident,
    IncidentSource,
    LatLon,
    RemovedIncident,
    ResponseConfig,
    validate_config,
)

PAGE = Path(__file__).resolve().parent.parent / "console" / "world.html"
CONUS = BBox(west=-125, south=24, east=-66, north=50)
HDR = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
       "satellite,instrument,confidence,version,bright_ti5,frp,daynight\n")


def _row(p: LatLon, t_s: float, frp: float = 5.0) -> str:
    import datetime as dt
    d = dt.datetime.fromtimestamp(t_s, tz=dt.timezone.utc)
    return (f"{p.lat:.5f},{p.lon:.5f},330.0,0.4,0.4,{d:%Y-%m-%d},"
            f"{d:%H%M},N20,VIIRS,n,2.0NRT,290.0,{frp},D\n")


class Http:
    """http_get stub: per-source CSV bodies (or an HTTP status)."""

    def __init__(self, bodies: dict):
        self.bodies = bodies
        self.urls: list = []

    def __call__(self, url, headers):
        self.urls.append(url)
        src = url.split("/")[7]
        body = self.bodies.get(src, HDR)
        if isinstance(body, int):
            return body, b""
        return 200, body.encode()


def _live(bodies, now=NOW):
    http = Http(bodies)
    return feeds.FirmsFeed.live("k" * 32, http_get=http,
                                clock=lambda: now), http


def _csv(*dets):
    return HDR + "".join(_row(p, t) for p, t in dets)


# ---------------------------------------------------------------------------
# LIVE MODE
# ---------------------------------------------------------------------------

def test_legacy_construction_is_unchanged():
    http = Http({"VIIRS_NOAA20_NRT": _csv((ORIGIN, NOW - 600.0))})
    f = feeds.FirmsFeed(map_key="k", http_get=http)
    assert not f.live_mode
    out = f.fetch_active(CONUS)
    assert http.urls == [f"{f.BASE_URL}/k/VIIRS_NOAA20_NRT/-125,24,-66,50/1"]
    assert len(out) == 1 and "hotspot_t_s" not in out[0].meta
    assert out[0].meta["satellite_source"] == "VIIRS_NOAA20_NRT"


def test_live_mode_asks_both_noaa_satellites_for_two_utc_days():
    f, http = _live({})
    f.fetch_active(CONUS)
    assert [u.split("/")[7] for u in http.urls] == list(
        feeds.FirmsFeed.LIVE_SOURCES)
    assert all(u.endswith("/-125,24,-66,50/2") for u in http.urls)
    assert feeds.FirmsFeed.LIVE_SOURCES == ("VIIRS_NOAA20_NRT",
                                            "VIIRS_NOAA21_NRT")
    assert f.describe() == {"sources": list(feeds.FirmsFeed.LIVE_SOURCES),
                            "window_h": 24.0, "day_range": 2,
                            "cluster_km": 2.0, "stable_ids": True}
    with pytest.raises(ValueError):
        feeds.FirmsFeed("k", window_h=0.0)


def test_the_window_keeps_the_last_24_hours_and_each_hotspot_time():
    near = geo.destination(ORIGIN, 90.0, 400.0)
    f, _ = _live({
        "VIIRS_NOAA20_NRT": _csv((ORIGIN, NOW - 3600.0),
                                  (near, NOW - 25 * 3600.0)),
        "VIIRS_NOAA21_NRT": _csv((near, NOW - 23 * 3600.0)),
    })
    out = f.fetch_active(CONUS)
    assert f.last_fetch_ok is True
    assert len(out) == 1
    inc = out[0]
    assert len(inc.hotspots) == 2                  # the 25 h one aged out
    t = inc.meta["hotspot_t_s"]
    assert t == sorted(t, reverse=True) and len(t) == 2
    assert inc.updated_at_s == pytest.approx(NOW - 3600.0, abs=60.0)
    assert inc.meta["satellites"] == ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
    assert inc.name == "FIRMS cluster " + inc.incident_id[len("firms:"):]


def test_a_failed_source_is_blind_but_the_others_are_kept():
    f, _ = _live({"VIIRS_NOAA21_NRT": 500,
                  "VIIRS_NOAA20_NRT": _csv((ORIGIN, NOW - 600.0))})
    out = f.fetch_active(CONUS)
    assert f.last_fetch_ok is False
    assert len(out) == 1


def test_a_cluster_keeps_its_id_as_passes_land_and_detections_age_out():
    a = ORIGIN
    b = geo.destination(ORIGIN, 90.0, 900.0)
    c = geo.destination(ORIGIN, 90.0, 1500.0)
    far = geo.destination(ORIGIN, 0.0, 30_000.0)
    f, http = _live({"VIIRS_NOAA20_NRT": _csv((a, NOW - 20 * 3600.0),
                                              (b, NOW - 10 * 3600.0))})
    first = f.fetch_active(CONUS)
    assert len(first) == 1
    cid = first[0].incident_id
    # a new pass lands east, the oldest detection ages out: the centroid
    # moves (a plain centroid id would change) but the id is kept
    http.bodies = {"VIIRS_NOAA20_NRT": _csv((b, NOW - 10 * 3600.0),
                                            (c, NOW - 600.0),
                                            (far, NOW - 300.0))}
    f._clock = lambda: NOW + 5 * 3600.0
    second = {i.incident_id: i for i in f.fetch_active(CONUS)}
    assert cid in second and len(second[cid].hotspots) == 2
    assert feeds._centroid_id(second[cid].origin.lat,
                              second[cid].origin.lon) != cid
    others = [i for i in second if i != cid]
    assert others == [feeds._centroid_id(far.lat, far.lon)]
    # every old detection gone, heat within 2 km of the old point: kept id
    d = geo.destination(c, 90.0, 300.0)
    http.bodies = {"VIIRS_NOAA20_NRT": _csv((d, NOW + 6 * 3600.0))}
    f._clock = lambda: NOW + 7 * 3600.0
    third = [i.incident_id for i in f.fetch_active(CONUS)]
    assert third == [cid]


def test_a_blind_fetch_does_not_forget_the_ids():
    f, http = _live({"VIIRS_NOAA20_NRT": _csv((ORIGIN, NOW - 600.0))})
    cid = f.fetch_active(CONUS)[0].incident_id
    http.bodies = {s: 500 for s in feeds.FirmsFeed.LIVE_SOURCES}
    assert f.fetch_active(CONUS) == [] and f.last_fetch_ok is False
    moved = geo.destination(ORIGIN, 90.0, 1200.0)
    http.bodies = {"VIIRS_NOAA20_NRT": _csv((moved, NOW))}
    assert [i.incident_id for i in f.fetch_active(CONUS)] == [cid]


# ---------------------------------------------------------------------------
# CLUSTERS — the grid is the plain scan exactly
# ---------------------------------------------------------------------------

def _plain(detections, cluster_m):
    clusters = []
    for det in detections:
        p = det[1]
        for cl in clusters:
            if feeds._haversine_m(p, LatLon(cl["lat"], cl["lon"])) <= cluster_m:
                cl["pts"].append(det)
                n = len(cl["pts"])
                cl["lat"] += (p.lat - cl["lat"]) / n
                cl["lon"] += (p.lon - cl["lon"]) / n
                break
        else:
            clusters.append({"pts": [det], "lat": p.lat, "lon": p.lon})
    return clusters


@pytest.mark.parametrize("lat0,lon0,span", [
    (39.0, -121.0, 0.6),      # a dense fire region
    (34.0, -100.0, 20.0),     # scattered, CONUS-wide
    (86.0, 10.0, 2.0),        # beyond 85 degrees: the plain scan
    (10.0, 179.5, 1.0),       # at the antimeridian: the plain scan
])
@pytest.mark.parametrize("cluster_m", [375.0, 2000.0, 5000.0])
def test_grid_clustering_is_the_plain_scan(lat0, lon0, span, cluster_m):
    rng = random.Random(f"{lat0}{lon0}{cluster_m}")
    dets = []
    for k in range(600):
        hub = (lat0 + rng.uniform(-span, span) * 0.2,
               lon0 + rng.uniform(-span, span) * 0.2)
        lat = max(-89.9, min(89.9, hub[0] + rng.gauss(0, 0.02)))
        lon = hub[1] + rng.gauss(0, 0.02)
        lon = ((lon + 180.0) % 360.0) - 180.0
        dets.append((NOW - k, LatLon(lat, lon), None))
    got = feeds._cluster_detections(dets, cluster_m)
    want = _plain(dets, cluster_m)
    assert [(c["lat"], c["lon"], [d[0] for d in c["pts"]]) for c in got] == \
        [(c["lat"], c["lon"], [d[0] for d in c["pts"]]) for c in want]


# ---------------------------------------------------------------------------
# ATTACH
# ---------------------------------------------------------------------------

def _square(center: LatLon, half_m: float) -> list:
    pts = [geo.destination(geo.destination(center, 0.0, dy), 90.0, dx)
           for dx, dy in ((-half_m, -half_m), (half_m, -half_m),
                          (half_m, half_m), (-half_m, half_m))]
    return [pts]


def _reported(iid="wf-1", perimeter=None, acreage=None, origin=ORIGIN,
              source=IncidentSource.WFIGS, active=True):
    return FireIncident(incident_id=iid, name=f"Fire {iid}", source=source,
                        origin=origin, updated_at_s=NOW - 3600.0,
                        acreage=acreage, active=active, perimeter=perimeter,
                        source_ids={source.value: iid})


def _cluster(points, t0=NOW - 600.0, cid=None):
    ts = [t0 - 60.0 * k for k in range(len(points))]
    lat = sum(p.lat for p in points) / len(points)
    lon = sum(p.lon for p in points) / len(points)
    cid = cid or feeds._centroid_id(lat, lon)
    return FireIncident(incident_id=cid, name="FIRMS cluster", origin=LatLon(lat, lon),
                        source=IncidentSource.FIRMS, updated_at_s=ts[0],
                        hotspots=list(points), source_ids={"firms": cid},
                        meta={"n_detections": len(points), "max_frp": 3.0,
                              "hotspot_t_s": ts,
                              "satellites": ["VIIRS_NOAA20_NRT"]})


def _cfg(**kw):
    return dataclasses.replace(ResponseConfig(), fuse_hotspots=True, **kw)


def test_a_detection_inside_a_perimeter_joins_that_fire():
    fire = _reported(perimeter=_square(ORIGIN, 1000.0))
    cl = _cluster([geo.destination(ORIGIN, 45.0, 200.0),
                   geo.destination(ORIGIN, 200.0, 300.0)])
    kept, cont, st = feeds.attach_hotspots([fire, cl], [], _cfg())
    assert [i.incident_id for i in kept] == ["wf-1"]       # cluster absorbed
    got = kept[0]
    assert got.hotspots == cl.hotspots
    assert got.source_ids == {"wfigs": "wf-1", "firms": cl.incident_id}
    assert got.meta["hotspots_attached"] == {
        "n": 2, "newest_s": NOW - 600.0, "clusters": [cl.incident_id]}
    assert st["attached"] == 2 and st["fires"] == ["wf-1"]
    assert st["clusters_absorbed"] == [cl.incident_id] and cont == []


def test_the_margin_joins_and_beyond_it_the_cluster_stays_a_fire():
    fire = _reported(perimeter=_square(ORIGIN, 1000.0))
    near = geo.destination(ORIGIN, 90.0, 1000.0 + 800.0)    # 800 m outside
    out = geo.destination(ORIGIN, 90.0, 1000.0 + 1600.0)    # 1.6 km outside
    cl = _cluster([near, out])                              # near is newest
    kept, _, st = feeds.attach_hotspots([fire, cl], [], _cfg())
    by = {i.incident_id: i for i in kept}
    assert by["wf-1"].hotspots == [near]
    rest = by[cl.incident_id]                               # same id
    assert rest.hotspots == [out]
    assert rest.origin == out
    assert rest.updated_at_s == NOW - 660.0                 # newest remaining
    assert rest.meta["hotspot_t_s"] == [NOW - 660.0]
    assert rest.meta["n_detections"] == 1
    assert rest.meta["attached_elsewhere"] == 1
    assert st["clusters_split"] == [cl.incident_id]
    kept, _, _ = feeds.attach_hotspots(
        [fire, cl], [], _cfg(fuse_hotspots_perimeter_m=500.0))
    assert len(kept) == 2 and kept[0].hotspots is None      # 800 m > 500 m


@pytest.mark.parametrize("acres,inside_m,outside_m", [
    (None, 2900.0, 3200.0),          # floor 2 km + 1 km
    (10_000.0, 8100.0, 8300.0),      # 2 x 3,589 m + 1 km = 8,178 m
    (1_000_000.0, 10_900.0, 11_100.0),   # capped 10 km + 1 km
])
def test_a_point_fire_takes_the_heat_within_its_size(acres, inside_m,
                                                     outside_m):
    fire = _reported(acreage=acres)
    for d, joins in ((inside_m, True), (outside_m, False)):
        cl = _cluster([geo.destination(ORIGIN, 30.0, d)])
        kept, _, st = feeds.attach_hotspots([fire, cl], [], _cfg())
        assert (st["attached"] == 1) is joins, (acres, d)


def test_a_contained_fire_takes_only_its_interior_heat():
    gone = _reported(perimeter=_square(ORIGIN, 1000.0))
    inside = geo.destination(ORIGIN, 0.0, 500.0)
    edge = geo.destination(ORIGIN, 90.0, 1300.0)           # 300 m outside
    cl = _cluster([inside, edge])
    kept, cont, st = feeds.attach_hotspots([cl], [gone], _cfg())
    assert cont[0].hotspots == [inside]
    assert st["inside_contained"] == 1
    assert [i.hotspots for i in kept] == [[edge]]          # never hidden


def test_the_nearest_footprint_wins_and_ties_go_by_id():
    a = _reported("wf-a", perimeter=_square(ORIGIN, 1000.0))
    east = geo.destination(ORIGIN, 90.0, 2500.0)
    b = _reported("wf-b", perimeter=_square(east, 1000.0))
    p_in_a = geo.destination(ORIGIN, 90.0, 900.0)       # inside a, 600 m from b
    p_near_a = geo.destination(ORIGIN, 90.0, 1150.0)    # 150 m from a, 350 from b
    p_near_b = geo.destination(ORIGIN, 90.0, 1400.0)    # 400 m from a, 100 from b
    kept, _, _ = feeds.attach_hotspots(
        [b, a, _cluster([p_in_a]), _cluster([p_near_a], t0=NOW - 7200.0),
         _cluster([p_near_b], t0=NOW - 9000.0)], [], _cfg())
    by = {i.incident_id: i for i in kept}
    assert by["wf-a"].hotspots == [p_in_a, p_near_a]     # newest first
    assert by["wf-b"].hotspots == [p_near_b]
    # the same footprint twice: the lower incident id takes the heat
    twin = _reported("wf-0", perimeter=_square(ORIGIN, 1000.0))
    kept, _, _ = feeds.attach_hotspots([a, twin, _cluster([p_in_a])], [],
                                       _cfg())
    by = {i.incident_id: i for i in kept}
    assert by["wf-0"].hotspots == [p_in_a] and by["wf-a"].hotspots is None


def test_seeded_firms_and_inactive_records_are_never_footprints():
    seeded = _reported("seeded:x", perimeter=_square(ORIGIN, 1000.0),
                       source=IncidentSource.SEEDED)
    other_firms = _cluster([ORIGIN], cid="firms:other")
    out_fire = _reported("wf-out", perimeter=_square(ORIGIN, 1000.0),
                         active=False)
    cl = _cluster([geo.destination(ORIGIN, 0.0, 100.0)])
    kept, _, st = feeds.attach_hotspots(
        [seeded, other_firms, out_fire, cl], [], _cfg())
    assert st["attached"] == 0
    assert kept == [seeded, other_firms, out_fire, cl]


def test_no_reported_fire_or_no_firms_changes_nothing():
    fire = _reported(perimeter=_square(ORIGIN, 1000.0))
    cl = _cluster([geo.destination(ORIGIN, 0.0, 30_000.0)])
    for kept_in in ([fire], [cl], [fire, cl]):
        kept, cont, st = feeds.attach_hotspots(kept_in, [], _cfg())
        assert kept == kept_in and st["attached"] == 0


# ---------------------------------------------------------------------------
# MONITOR
# ---------------------------------------------------------------------------

def _scene():
    fire = dataclasses.replace(incident(), perimeter=_square(ORIGIN, 400.0))
    cl = _cluster([geo.destination(ORIGIN, 0.0, 100.0),
                   geo.destination(ORIGIN, 180.0, 100.0)], t0=NOW - 900.0)
    return fire, cl


def _monitor(cfg, feed):
    return make_monitor(feeds=[feed], cfg=cfg,
                        registry=StubRegistry([near_client(), far_client()]))


def test_knob_off_keeps_every_cluster_its_own_fire():
    fire, cl = _scene()
    r = _monitor(make_cfg(), StubFeed([fire, cl])).cycle(NOW)
    ids = sorted(i.incident_id for i in r.incidents)
    assert ids == sorted(["inc-1", cl.incident_id])
    assert not any(n.startswith("hotspots:") for n in r.notes)
    assert next(i for i in r.incidents if i.incident_id == "inc-1").hotspots \
        is None


def test_knob_on_joins_the_fire_and_notes_it():
    fire, cl = _scene()
    r = _monitor(make_cfg(fuse_hotspots=True), StubFeed([fire, cl])).cycle(NOW)
    assert [i.incident_id for i in r.incidents] == ["inc-1"]
    inc = r.incidents[0]
    assert len(inc.hotspots) == 2 and "firms" in inc.source_ids
    note = next(n for n in r.notes if n.startswith("hotspots:"))
    assert "2 of 2 FIRMS detection(s) joined 1 reported fire(s) (inc-1)" in note
    assert "SAFETY SEMANTICS 39" in note
    assert "inc-1" in r.ensembles                  # still forecast


def test_a_removed_contained_fire_takes_its_interior_heat(monkeypatch):
    fire, cl = _scene()
    fire = dataclasses.replace(fire, containment_pct=100.0, acreage=80.0)
    edge = geo.destination(ORIGIN, 90.0, 900.0)              # 500 m outside
    cl = dataclasses.replace(
        cl, hotspots=list(cl.hotspots) + [edge],
        meta=dict(cl.meta, hotspot_t_s=cl.meta["hotspot_t_s"] + [NOW - 2000.0]))
    cfg = make_cfg(fuse_hotspots=True, drop_contained_incidents=True)
    r = _monitor(cfg, StubFeed([fire, cl])).cycle(NOW)
    assert [x.incident.incident_id for x in r.removed_incidents] == ["inc-1"]
    assert len(r.removed_incidents[0].incident.hotspots) == 2
    left = [i for i in r.incidents if i.source is IncidentSource.FIRMS]
    assert [i.hotspots for i in left] == [[edge]]
    assert isinstance(r.removed_incidents[0], RemovedIncident)


def test_an_attachment_failure_keeps_every_cluster_a_fire(monkeypatch):
    fire, cl = _scene()

    def boom(*a, **k):
        raise RuntimeError("bad geometry")
    monkeypatch.setattr(feeds, "attach_hotspots", boom)
    r = _monitor(make_cfg(fuse_hotspots=True), StubFeed([fire, cl])).cycle(NOW)
    assert sorted(i.incident_id for i in r.incidents) == sorted(
        ["inc-1", cl.incident_id])
    assert any("hotspot attachment failed (bad geometry)" in n for n in r.notes)


# ---------------------------------------------------------------------------
# SURFACE
# ---------------------------------------------------------------------------

def test_the_watch_flag_builds_the_live_firms_feed():
    from wgresponse import cli
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--fuse-hotspots"])
    assert args.fuse_hotspots is True
    stack, notes = cli._live_feed_stack("k" * 32, live=True)
    firms = [f for f in stack if isinstance(f, feeds.FirmsFeed)]
    assert len(firms) == 1 and firms[0].live_mode and notes == []
    stack, _ = cli._live_feed_stack("k" * 32)
    assert not [f for f in stack if isinstance(f, feeds.FirmsFeed)][0].live_mode
    note = cli._firms_scale_notes(
        [feeds.FirmsFeed.live("k")],
        BBox(west=-125, south=24, east=-66, north=50))
    assert len(note) == 1 and "live mode" in note[0]
    assert "SAFETY SEMANTICS 39" in note[0]


def test_summary_rows_carry_satellite_heat_only_when_armed():
    fire = _reported(perimeter=_square(ORIGIN, 1000.0))
    cl = _cluster([geo.destination(ORIGIN, 0.0, 9000.0)])
    kept, _, _ = feeds.attach_hotspots(
        [fire, _cluster([ORIGIN]), cl], [], _cfg())
    result = type("R", (), {"t_s": NOW, "incidents": kept, "notes": [],
                            "ensembles": {}, "assessments": []})()
    rule = worldview.hotspot_rule(_cfg(), feeds.FirmsFeed.live("k"))
    assert rule["firms"]["window_h"] == 24.0 and rule["screening"] is None
    out = worldview.build_summary({"hotspot_rule": rule}, result,
                                  geometry=False)
    rows = {r["incident_id"]: r for r in out["incidents"]}
    assert rows["wf-1"]["hotspots"]["kind"] == "attached"
    assert rows["wf-1"]["hotspots"]["n"] == 1
    assert rows[cl.incident_id]["hotspots"] == {
        "n": 1, "kind": "cluster", "newest_s": NOW - 600.0,
        "newest_age_h": round(600.0 / 3600.0, 2),
        "satellites": ["VIIRS_NOAA20_NRT"], "max_frp": 3.0}
    assert out["hotspot_rule"] == rule
    plain = worldview.build_summary({}, result, geometry=False)
    assert "hotspot_rule" not in plain
    assert all("hotspots" not in r for r in plain["incidents"])


def test_the_page_shows_satellite_heat():
    html = PAGE.read_text(encoding="utf-8")
    assert "function hotspotLine(r)" in html
    assert "satellite heat</i> (NASA FIRMS)" in html
    assert "const hl = hotspotLine(r);" in html


def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert cfg.fuse_hotspots is False
    assert (cfg.fuse_hotspots_perimeter_m, cfg.fuse_hotspots_point_min_m,
            cfg.fuse_hotspots_point_max_m) == (1000.0, 2000.0, 10000.0)
    assert validate_config(cfg) == []
    for kw, frag in ((dict(fuse_hotspots_perimeter_m=-1.0), "perimeter_m"),
                     (dict(fuse_hotspots_perimeter_m=math.inf), "perimeter_m"),
                     (dict(fuse_hotspots_point_min_m=0.0), "point_min_m"),
                     (dict(fuse_hotspots_point_max_m=1500.0), "point_max_m")):
        probs = validate_config(dataclasses.replace(cfg, **kw))
        assert any(frag in p for p in probs), kw
