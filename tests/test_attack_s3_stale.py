"""Session-3 staleness-gate + perimeter-provenance regressions (verifier
finding F3 + brief #4, SAFETY SEMANTICS 9).

Promoted from scratch_review/attack_s3_stale.py after the fixer pass.

Staleness gate (SS9): the fused updated_at_s (freshest member) decides the
168 h gate — a zombie perimeter fused with fresh point evidence stays
alerted (never silence an active fire), while an unfused zombie is gated and
a fresh FIRMS cluster keeps the client covered.

FINDING F3 (major, LATE polarity, FIXED here-in): the fused record carries
ONE updated_at_s (the freshest member's) and _assess_incident stamped the
perimeter interior at min(fused updated_at_s, now) — an old member's polygon
fused with a fresh perimeter-less point record was rasterized as freshly
burned, running the modeled front up to (fresh - perimeter age) hours LATE
(measured 47.8 h below). FIX: the feeds layer carries the perimeter OWNER's
own currency time as meta["perimeter_at_s"] (WFIGS poly_DateCurrent in
_from_perimeter; the perimeter member's own stamp through WfigsFeed._build
and _merge_group) and threat._assess_incident stamps min(provenance, now),
falling back to updated_at_s for single-timestamp records (byte-identical
there — pinned in tests/test_threat.py). Alerts can only get EARLIER (safe
polarity); non-positive/garbage stamps fall back rather than burning the
domain at epoch 0.

Deterministic; no network; stub stack from tests/attack_s3_stack.py.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attack_s3_stack import (  # noqa: E402
    CLIENT_LOC, H, ORIGIN, T0, ScriptedFeed, StubRegistry, StubTerrain,
    StubWeather, client, incident, km_east, small_cfg,
)
from wgresponse import geo, spread  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import AlertState, IncidentSource, LatLon  # noqa: E402
from wgresponse.feeds import WfigsFeed, fuse_incidents  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

NOW = T0


def square_ring(center, half_m=500.0):
    n = geo.destination(center, 0.0, half_m)
    s = geo.destination(center, 180.0, half_m)
    e = geo.destination(center, 90.0, half_m)
    w = geo.destination(center, 270.0, half_m)
    return [LatLon(s.lat, w.lon), LatLon(s.lat, e.lon),
            LatLon(n.lat, e.lon), LatLon(n.lat, w.lon),
            LatLon(s.lat, w.lon)]


def make_monitor(feed, engine=None, cfg=None):
    cfg = cfg or small_cfg()
    engine = engine or AlertEngine(cfg)
    mon = ThreatMonitor(feeds=[feed], weather_provider=StubWeather(),
                        terrain_provider=StubTerrain(),
                        registry=StubRegistry([client()]),
                        alert_engine=engine, cfg=cfg, safe_zones=[])
    return mon, engine


def spy_ensemble(monkeypatch, captured):
    orig = spread.ensemble_arrival

    def spy(tile, ignition, wx, t0_s, cfg, incident_id="", perimeter=None,
            perimeter_at_s=None):
        captured["perimeter_at_s"] = perimeter_at_s
        captured["has_perimeter"] = perimeter is not None
        return orig(tile, ignition, wx, t0_s, cfg, incident_id=incident_id,
                    perimeter=perimeter, perimeter_at_s=perimeter_at_s)

    monkeypatch.setattr(spread, "ensemble_arrival", spy)


def stale_perimeter_member(age_h=200.0):
    return incident(iid="firis:9", name="Volcano", source=IncidentSource.FIRIS,
                    irwin_id="IRW-V", updated_at_s=NOW - age_h * H,
                    perimeter=[square_ring(ORIGIN)], acreage=900.0)


def fresh_point_member(age_h=0.25):
    return incident(iid="irw-v", name="Volcano", source=IncidentSource.WFIGS,
                    irwin_id="IRW-V", updated_at_s=NOW - age_h * H,
                    origin=km_east(ORIGIN, 0.4), acreage=950.0)


# ---------------------------------------------------------------------------
# fresh member FUSES with the stale perimeter record -> not gated
# ---------------------------------------------------------------------------

def test_fused_record_carries_fresh_updated_at_perimeter_and_provenance():
    (fused,) = fuse_incidents([[stale_perimeter_member()],
                               [fresh_point_member()]])
    assert fused.incident_id == "irw-v"
    assert fused.updated_at_s == NOW - 0.25 * H       # freshest member wins
    assert fused.perimeter is not None                # stale member's polygon
    assert fused.acreage == 950.0
    # F3: the perimeter OWNER's own time rides along for the spread stamp.
    assert fused.meta["perimeter_at_s"] == NOW - 200.0 * H


def test_stale_perimeter_plus_fresh_fused_member_is_not_gated_end_to_end():
    """200 h-old perimeter member + 15 min-old point member of the SAME fire
    (shared IRWIN): the fused record is fresh -> assessed, alerted,
    perimeter used — the gate must not kill the actually-active fire."""
    feed = ScriptedFeed([stale_perimeter_member(), fresh_point_member()])
    mon, engine = make_monitor(feed)
    res = mon.cycle(NOW)
    assert [i.incident_id for i in res.incidents] == ["irw-v"]
    assert not any("stale" in n for n in res.notes)
    assert {(a.client_id, a.incident_id) for a in res.assessments} == \
        {("c1", "irw-v")}
    assert engine.states()[("c1", "irw-v")].state in (
        AlertState.NOTICE_72H, AlertState.WATCH, AlertState.EXTREME_24H,
        AlertState.IMPACTED)
    assert res.degraded is False


# ---------------------------------------------------------------------------
# realistic FIRMS shape: no fusion — gate drops the zombie perimeter, the
# fresh cluster keeps the fire alerted
# ---------------------------------------------------------------------------

def firms_cluster_member(age_h=1.0):
    pts = [km_east(ORIGIN, 0.3), km_east(ORIGIN, 0.6),
           geo.destination(ORIGIN, 45.0, 400.0)]
    return incident(iid="firms:39.000,-120.995",
                    name="FIRMS cluster 39.000,-120.995",
                    source=IncidentSource.FIRMS, irwin_id=None,
                    origin=km_east(ORIGIN, 0.4),
                    updated_at_s=NOW - age_h * H, hotspots=pts,
                    acreage=None, containment_pct=None)


def test_zombie_perimeter_gated_but_fresh_firms_cluster_keeps_fire_alive():
    """The stale 'Volcano' perimeter (200 h) and a fresh FIRMS cluster do NOT
    fuse (synthetic cluster name, no IRWIN): the perimeter record is excluded
    by the gate, but the client is still assessed + alerted through the
    cluster incident — an active fire with fresh satellite evidence is never
    silenced by the staleness gate."""
    stale = stale_perimeter_member(age_h=200.0)
    stale.irwin_id = None                      # nothing for FIRMS to join on
    cluster = firms_cluster_member()
    feed = ScriptedFeed([stale, cluster])
    mon, engine = make_monitor(feed)
    res = mon.cycle(NOW)

    fused_ids = {i.incident_id for i in res.incidents}
    assert fused_ids == {"firms:39.000,-120.995"}      # zombie gated
    (note,) = [n for n in res.notes if "stale" in n]
    assert "firis:9" in note and "200 h" in note
    assert res.degraded is False                       # hygiene, not outage

    pairs = {(a.client_id, a.incident_id) for a in res.assessments}
    assert pairs == {("c1", "firms:39.000,-120.995")}
    state = engine.states()[("c1", "firms:39.000,-120.995")].state
    assert state in (AlertState.NOTICE_72H, AlertState.WATCH,
                     AlertState.EXTREME_24H)
    (a,) = res.assessments
    assert math.isfinite(a.eta.eta_p50_s)


def test_gate_alone_would_have_silenced_the_fire_without_the_cluster():
    """Control for the test above: with ONLY the zombie perimeter on the
    feed, the cycle assesses nothing (what the FIRMS layer protects against —
    and why FIRMS-keyless deployments lean entirely on perimeter-feed
    freshness)."""
    stale = stale_perimeter_member(age_h=200.0)
    stale.irwin_id = None
    mon, _ = make_monitor(ScriptedFeed([stale]))
    res = mon.cycle(NOW)
    assert res.incidents == [] and res.assessments == []


# ---------------------------------------------------------------------------
# boundary exactly at incident_stale_h — including through fusion
# ---------------------------------------------------------------------------

def test_boundary_exact_age_kept_one_second_over_excluded_through_fusion():
    cfg = small_cfg()
    max_age_s = cfg.incident_stale_h * 3600.0

    def run(fresher_age_s):
        old = stale_perimeter_member(age_h=300.0)          # very stale member
        newer = fresh_point_member()
        newer.updated_at_s = NOW - fresher_age_s           # decides the gate
        mon, _ = make_monitor(ScriptedFeed([old, newer]), cfg=cfg)
        return mon.cycle(NOW)

    exact = run(max_age_s)                     # fused age exactly 168 h
    assert [i.incident_id for i in exact.incidents] == ["irw-v"]
    assert len(exact.assessments) == 1
    over = run(max_age_s + 1.0)                # one second older
    assert over.incidents == [] and over.assessments == []
    assert any("excluded 1 stale" in n for n in over.notes)


def test_future_stamped_incident_negative_age_is_kept():
    inc = incident(updated_at_s=NOW + 2 * H)   # feed clock 2 h ahead
    mon, _ = make_monitor(ScriptedFeed([inc]))
    res = mon.cycle(NOW)
    assert [i.incident_id for i in res.incidents] == ["inc-1"]
    assert not any("stale" in n for n in res.notes)


# ---------------------------------------------------------------------------
# FINDING F3 (FIXED) — fused perimeter stamped at ITS OWN time
# ---------------------------------------------------------------------------

def test_f3_fused_old_perimeter_stamped_at_its_own_time(monkeypatch):
    """F3 regression pin: the fused record (old FIRIS perimeter + fresh WFIGS
    point) keeps the FRESH updated_at_s for the staleness gate, but the
    perimeter interior is stamped at the PERIMETER MEMBER's own 48 h-old
    time — the modeled front keeps its 48 h of advance."""
    captured = {}
    spy_ensemble(monkeypatch, captured)
    feed = ScriptedFeed([stale_perimeter_member(age_h=48.0),
                         fresh_point_member(age_h=0.25)])
    mon, _ = make_monitor(feed)
    mon.cycle(NOW)
    assert captured["has_perimeter"] is True
    assert captured["perimeter_at_s"] == NOW - 48.0 * H   # its own age
    assert captured["perimeter_at_s"] != NOW - 0.25 * H   # never fused-fresh


def test_f3_single_source_record_still_stamped_at_updated_at(monkeypatch):
    """No-meta fallback: a single-timestamp record's perimeter stamp stays
    exactly min(updated_at_s, now) — byte-identical to the pre-fix path."""
    captured = {}
    spy_ensemble(monkeypatch, captured)
    mon, _ = make_monitor(ScriptedFeed([stale_perimeter_member(age_h=2.0)]))
    mon.cycle(NOW)
    assert captured["perimeter_at_s"] == NOW - 2.0 * H


def test_f3_garbage_provenance_falls_back_to_updated_at(monkeypatch):
    """Hostile/broken meta stamps (0, negative, bool, string, inf) must never
    reach spread: fall back to the record's updated_at_s — a bogus epoch-0
    provenance would otherwise burn the whole domain decades ago."""
    for garbage in (0, -5.0, True, "garbage", math.inf):
        captured = {}
        spy_ensemble(monkeypatch, captured)
        inc = stale_perimeter_member(age_h=2.0)
        inc.meta["perimeter_at_s"] = garbage
        mon, _ = make_monitor(ScriptedFeed([inc]))
        mon.cycle(NOW)
        assert captured["perimeter_at_s"] == NOW - 2.0 * H, repr(garbage)


def test_f3_future_provenance_still_clock_clamped(monkeypatch):
    """SS9 clock clamp survives the provenance change: a perimeter stamp from
    a feed clock running ahead of ours is clamped to now_s."""
    captured = {}
    spy_ensemble(monkeypatch, captured)
    inc = stale_perimeter_member(age_h=2.0)
    inc.meta["perimeter_at_s"] = NOW + 2 * H   # polygon 'from the future'
    mon, _ = make_monitor(ScriptedFeed([inc]))
    mon.cycle(NOW)
    assert captured["perimeter_at_s"] == NOW


def test_f3_measured_lateness_of_fresh_stamped_old_perimeter():
    """Quantifies what the F3 fix buys, on the harness tile: same polygon,
    stamped at its own 48 h-old time (what production NOW does) vs at the
    fused-fresh time (the pre-fix defect). Downwind client 4.3 km east:
    honest-stamp p50 has already PASSED (hours_out 0) while fresh-stamp p50
    is hours out — the defect ran up to (fresh - old) ~48 h LATE."""
    cfg = small_cfg()
    domain = geo.bbox_around([ORIGIN, CLIENT_LOC], 3000.0)
    tile = StubTerrain().tile(domain, cfg.grid_cell_m)
    wx = StubWeather().forecast(ORIGIN, cfg.horizon_h)
    ring = square_ring(ORIGIN)

    def eta_with_stamp(stamp_s):
        ens = spread.ensemble_arrival(tile, [], wx, NOW, cfg,
                                      incident_id="irw-v", perimeter=[ring],
                                      perimeter_at_s=stamp_s)
        return spread.eta_at(ens, CLIENT_LOC)

    fresh = eta_with_stamp(NOW - 0.25 * H)     # the pre-fix defect stamp
    honest = eta_with_stamp(NOW - 48.0 * H)    # the perimeter's own age

    fresh_h = fresh.hours_out(NOW)[1]
    honest_h = honest.hours_out(NOW)[1]
    assert honest_h == 0.0, "48 h-old front should already be past the client"
    assert fresh_h > 2.0, "fresh-stamped front is hours away"
    lateness_h = (fresh.eta_p50_s - honest.eta_p50_s) / H
    assert lateness_h > 40.0                   # ~the 48 h stamp gap


# ---------------------------------------------------------------------------
# F3 single-source WFIGS shape: _build carries the polygon's own currency
# ---------------------------------------------------------------------------

def _wfigs_http_stub(points, perims):
    def http_get(url, headers):
        if "Incident_Locations" in url:
            import json as _json
            return 200, _json.dumps(points).encode("utf-8")
        import json as _json
        return 200, _json.dumps(perims).encode("utf-8")
    return http_get


def test_f3_wfigs_point_perimeter_join_keeps_polygon_provenance():
    """WfigsFeed._build: a minutes-fresh point record joined with a
    nightly-IR perimeter keeps updated_at_s = max(point, perimeter) — the
    staleness-gate freshness — while meta['perimeter_at_s'] carries
    poly_DateCurrent, the polygon's own currency time."""
    point_ms = int((NOW - 0.25 * H) * 1000.0)          # 15 min-fresh point
    poly_ms = int((NOW - 30.0 * H) * 1000.0)           # 30 h-old IR polygon
    points = {"features": [
        {"attributes": {"OBJECTID": 1, "IncidentName": "Canyon",
                        "IrwinID": "{ABC-123}", "IncidentSize": 512.0,
                        "ModifiedOnDateTime_dt": point_ms,
                        "IncidentTypeCategory": "WF"},
         "geometry": {"x": -118.60, "y": 34.10}},
    ]}
    perims = {"features": [
        {"attributes": {"OBJECTID": 11, "poly_IncidentName": "Canyon",
                        "poly_IRWINID": "{abc-123}",
                        "attr_ModifiedOnDateTime_dt": point_ms,
                        "poly_DateCurrent": poly_ms},
         "geometry": {"rings": [[[-118.61, 34.09], [-118.59, 34.09],
                                 [-118.59, 34.11], [-118.61, 34.11],
                                 [-118.61, 34.09]]]}},
    ]}
    feed = WfigsFeed(http_get=_wfigs_http_stub(points, perims))
    (inc,) = feed.fetch_active()
    assert inc.incident_id == "abc-123"
    assert inc.updated_at_s == pytest.approx(NOW - 0.25 * H)   # fresh record
    assert inc.perimeter is not None
    assert inc.meta["perimeter_at_s"] == pytest.approx(NOW - 30.0 * H)
