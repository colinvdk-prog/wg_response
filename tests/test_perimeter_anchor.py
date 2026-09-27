"""A perimeter burns from where the feed last CONFIRMED it (contracts SAFETY
SEMANTICS 13e/f; cfg.perimeter_confirmed_anchor / watch --confirmed-
perimeters; 2026-09-27).

Measured on the W6 national run: SEMANTICS 9's staleness gate reads the
RECORD's age, so an 80 %-contained fire whose 249-h-old polygon a 19-h-old
record re-confirmed at the same size was spread forward 249 h (87 homes
EXTREME_24H). Pinned here: a fresh record reporting the polygon's size seeds
the polygon at the record's report time (never earlier than its own time,
never in the future); a record reporting MORE acres beyond the tolerance (a
growing fire whose polygon lags) keeps the legacy, pessimistic seeding — and
so do a smaller size beyond the tolerance, a newer polygon in another fused
source, a missing size or area, and a record no fresher than its polygon;
every decision on a domain incident is one note; a genuinely stale record
still hits the staleness gate; the knob OFF is the legacy seeding; and on
the RECORDED Willow payloads (2026-09-27) the anchor seeds at the 19.7-h-old
record instead of the 259.6-h-old polygon and the modeled burn stops
running days ahead.

Deterministic; no network; spread.ensemble_arrival spied for the exact
perimeter_at_s (the pattern of tests/test_threat.py).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contained_stack as cs  # noqa: E402
import seeded_stack as ss  # noqa: E402
from wgresponse import feeds, geo, spread  # noqa: E402
from wgresponse.contracts import ClientProperty, LatLon  # noqa: E402

T0 = cs.T0
H = cs.H
NOW = T0 + 60.0
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                       "contained_2026_09_27.json")


@pytest.fixture
def spy(monkeypatch):
    """{incident_id: perimeter_at_s} handed to spread.ensemble_arrival."""
    seen = {}
    orig = spread.ensemble_arrival

    def wrapper(tile, ignition, wx, t0_s, cfg, incident_id="", perimeter=None,
                perimeter_at_s=None, **kw):
        seen[incident_id] = perimeter_at_s
        return orig(tile, ignition, wx, t0_s, cfg, incident_id=incident_id,
                    perimeter=perimeter, perimeter_at_s=perimeter_at_s, **kw)

    monkeypatch.setattr(spread, "ensemble_arrival", wrapper)
    return seen


def _cycle(tmp_path, incidents, anchor=True, now=NOW, **cfg_kw):
    cfg = ss.small_cfg(perimeter_confirmed_anchor=anchor, **cfg_kw)
    clock = ss.SettableClock(now)
    daemon, *_ = ss.make_stack(tmp_path / "state", cfg=cfg,
                               incidents=incidents, clock=clock)
    return daemon.run_cycle()


def _notes(res, iid="inc-anchor"):
    return [n for n in res.notes if n.startswith(f"incident {iid}: polygon")]


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------

def test_fresh_record_same_size_anchors_at_the_record_time(tmp_path, spy):
    res = _cycle(tmp_path, [cs.anchor_incident()])      # +1 %, 30 min old
    assert spy["inc-anchor"] == T0 - 1800.0             # the record, not T0-100h
    (note,) = _notes(res)
    assert "polygon 100.0 h old, record 0.5 h old" in note
    assert "(+1.0%, within 2%)" in note and "CONFIRMED as of the record" in note
    # the polygon interior burned at the anchor, the front ran 0.5 h from it
    ens = res.ensembles["inc-anchor"]
    inside = geo.latlon_to_rc(ens.spec, cs.ANCHOR_CENTER)
    assert ens.p50.get(*inside) == T0 - 1800.0


@pytest.mark.parametrize("rel, anchored", [
    (0.0, True), (0.019, True), (-0.019, True),
    (0.021, False), (0.10, False), (-0.05, False)])
def test_same_size_is_within_the_tolerance_either_way(tmp_path, spy, rel,
                                                       anchored):
    poly = cs.ring_acres(cs.ANCHOR_RING)
    res = _cycle(tmp_path, [cs.anchor_incident(reported_acres=poly * (1 + rel))])
    assert (spy["inc-anchor"] == T0 - 1800.0) is anchored
    if not anchored:
        assert spy["inc-anchor"] == T0 - 100 * H          # legacy: own time


def test_a_growing_fire_keeps_the_legacy_pessimistic_seeding(tmp_path, spy):
    """THE OPPOSITE RISK: the record reports more acres than the polygon —
    the polygon lags a growing fire. Anchoring it at the record time would
    claim the old outline is the fire's extent NOW; the legacy seeding at
    the polygon's own time stands, and the note says why."""
    poly = cs.ring_acres(cs.ANCHOR_RING)
    res = _cycle(tmp_path, [cs.anchor_incident(reported_acres=poly * 1.5)])
    assert spy["inc-anchor"] == T0 - 100 * H
    (note,) = _notes(res)
    assert "+50.0% over the polygon's" in note
    assert "lags a GROWING fire" in note and "legacy, pessimistic" in note


def test_growth_guard_is_the_more_pessimistic_forecast(tmp_path, spy):
    """Same fire, same polygon: the growing record's (legacy) seeding has
    burned more ground by now and reaches more of the domain in the
    horizon than the anchored seeding would — the guard errs pessimistic."""
    poly = cs.ring_acres(cs.ANCHOR_RING)
    grow = _cycle(tmp_path / "g", [cs.anchor_incident(reported_acres=poly * 1.5)])
    same = _cycle(tmp_path / "s", [cs.anchor_incident()])
    g, s = grow.ensembles["inc-anchor"].p10, same.ensembles["inc-anchor"].p10
    assert sum(1 for v in g.data if v <= NOW) > sum(1 for v in s.data if v <= NOW)
    assert (sum(1 for v in g.data if v != float("inf"))
            >= sum(1 for v in s.data if v != float("inf")))


def test_a_smaller_size_beyond_tolerance_is_legacy(tmp_path, spy):
    poly = cs.ring_acres(cs.ANCHOR_RING)
    res = _cycle(tmp_path, [cs.anchor_incident(reported_acres=poly * 0.5)])
    assert spy["inc-anchor"] == T0 - 100 * H
    assert "the sizes disagree" in _notes(res)[0]


def test_a_newer_polygon_elsewhere_blocks_the_anchor(tmp_path, spy):
    """The fused group carries the freshest RECORD's polygon; when another
    source holds a NEWER polygon, the old one cannot be 'confirmed'."""
    inc = cs.anchor_incident(meta={"perimeter_newest_at_s": T0 - 10 * H})
    res = _cycle(tmp_path, [inc])
    assert spy["inc-anchor"] == T0 - 100 * H
    assert "a NEWER polygon (10.0 h old) exists in another source" in _notes(res)[0]


@pytest.mark.parametrize("drop", ["reported_acres", "perimeter_acres"])
def test_no_size_to_compare_is_legacy(tmp_path, spy, drop):
    inc = cs.anchor_incident()
    del inc.meta[drop]
    res = _cycle(tmp_path, [inc])
    assert spy["inc-anchor"] == T0 - 100 * H
    assert "no size to compare" in _notes(res)[0]


def test_a_record_no_fresher_than_its_polygon_has_nothing_to_confirm(
        tmp_path, spy):
    inc = cs.anchor_incident(meta={"reported_at_s": T0 - 100 * H})
    res = _cycle(tmp_path, [inc])
    assert spy["inc-anchor"] == T0 - 100 * H
    assert _notes(res) == []                             # silent: legacy law


def test_a_future_report_is_clamped_to_now(tmp_path, spy):
    inc = cs.anchor_incident(updated_at_s=NOW + 2 * H)   # feed clock ahead
    _cycle(tmp_path, [inc])
    assert spy["inc-anchor"] == NOW


def test_knob_off_is_the_legacy_seeding(tmp_path, spy):
    res = _cycle(tmp_path, [cs.anchor_incident()], anchor=False)
    assert spy["inc-anchor"] == T0 - 100 * H
    assert _notes(res) == []


def test_a_genuinely_stale_incident_still_hits_the_staleness_gate(tmp_path, spy):
    """The anchor never reaches past SEMANTICS 9: a record older than
    incident_stale_h is excluded before any domain, anchor or not."""
    inc = cs.anchor_incident(updated_at_s=T0 - 200 * H,
                             perimeter_at_s=T0 - 400 * H)
    res = _cycle(tmp_path, [inc])
    assert res.incidents == [] and res.ensembles == {} and spy == {}
    assert any(n.startswith("excluded 1 stale incident(s)") and "inc-anchor"
               in n for n in res.notes)


def test_only_domain_incidents_carry_the_note(tmp_path, spy):
    far = cs.anchor_incident(ring=cs.square_ring(LatLon(45.0, -110.0), 500.0))
    res = _cycle(tmp_path, [far])
    assert res.ensembles == {} and _notes(res) == []


def test_fusion_carries_the_polygon_area_and_the_newest_polygon():
    """perimeter_acres follows the perimeter OWNER (like perimeter_at_s);
    perimeter_newest_at_s is the newest polygon over every member."""
    from wgresponse.contracts import FireIncident, IncidentSource
    ring = cs.square_ring(LatLon(38.2, -122.8), 600.0)
    wf = FireIncident(incident_id="abc", name="Chileno", source=IncidentSource.WFIGS,
                      origin=LatLon(38.2, -122.8), updated_at_s=T0 - 3600.0,
                      acreage=341.0, containment_pct=80.0, irwin_id="abc",
                      perimeter=[ring], source_ids={"wfigs": "abc"},
                      meta={"perimeter_at_s": T0 - 200 * H,
                            "perimeter_acres": 330.0,
                            "reported_acres": 341.0,
                            "reported_at_s": T0 - 3600.0})
    fi = FireIncident(incident_id="firis:9", name="CHILENO",
                      source=IncidentSource.FIRIS, origin=LatLon(38.201, -122.8),
                      updated_at_s=T0 - 20 * H, acreage=360.0, perimeter=[ring],
                      source_ids={"firis": "9"}, meta={"perimeter_acres": 360.0})
    (fused,) = feeds.fuse_incidents([[wf], [fi]])
    assert fused.meta["perimeter_at_s"] == T0 - 200 * H      # WFIGS owns it
    assert fused.meta["perimeter_acres"] == 330.0            # ... and its area
    assert fused.meta["perimeter_newest_at_s"] == T0 - 20 * H   # FIRIS newer
    assert fused.meta["reported_acres"] == 341.0
    # FIRIS alone: its acres are the polygon's own, never a record size
    fir = feeds.FirisFeed()._from_feature({
        "attributes": {"OBJECTID": 1, "incident_name": "X", "area_acres": 12.5,
                       "poly_DateCurrent": T0 * 1000.0},
        "geometry": {"rings": [[[-122.8, 38.2], [-122.79, 38.2],
                                [-122.79, 38.21], [-122.8, 38.2]]]}})
    assert fir.meta == {"perimeter_acres": 12.5}


# ---------------------------------------------------------------------------
# recorded data: the Willow fire, 2026-09-27
# ---------------------------------------------------------------------------

def _willow():
    with open(FIXTURE, encoding="utf-8") as f:
        fx = json.load(f)
    wl = fx["willow"]
    bodies = {"Incident_Locations": {"features": [wl["point"]]},
              "Interagency_Perimeters": {"features": [wl["perimeter"]]}}

    def http(url, headers=None):
        for frag, body in bodies.items():
            if frag in url:
                return 200, json.dumps(body).encode("utf-8")
        raise AssertionError(url)
    (inc,) = feeds.WfigsFeed(http_get=http).fetch_active()
    return inc, fx["recorded_at_s"]


def test_recorded_willow_payloads_carry_the_confirmation():
    inc, now = _willow()
    assert inc.name == "Willow" and inc.containment_pct == 80.0
    assert round((now - inc.meta["perimeter_at_s"]) / H, 1) == 259.6
    assert round((now - inc.meta["reported_at_s"]) / H, 1) == 19.7
    assert inc.meta["reported_acres"] == 7389.0          # IncidentSize
    assert round(inc.meta["perimeter_acres"], 1) == 7258.6   # poly_GISAcres
    assert inc.acreage == 7389.0 and inc.updated_at_s == inc.meta["reported_at_s"]


def _willow_run(tmp_path, anchor, inc=None, now=None):
    base, fixture_now = _willow()
    inc = inc or base
    now = now or fixture_now
    # a demo home 5 km east of the fire's point record (downwind of the stub
    # west wind), on a slow timber-litter fuel so a day of spread stays local
    home = geo.destination(inc.origin, 90.0, 5000.0)
    cfg = ss.small_cfg(perimeter_confirmed_anchor=anchor, horizon_h=48.0,
                       grid_max_cells=40_000)
    clock = ss.SettableClock(now)
    daemon, _m, _f, engine, *_ = ss.make_stack(
        tmp_path / "state", cfg=cfg, feed=ss.ScriptedFeed([inc]), clock=clock,
        terrain=ss.StubTerrain(fuel_code=183.0),
        weather=ss.StubWeather(wind_mps=4.0),
        registry_clients=[ClientProperty(client_id="h1", loc=home)])
    res = daemon.run_cycle()
    return res, now, engine


def test_recorded_willow_no_longer_spreads_the_polygon_forward_for_days(
        tmp_path, spy):
    legacy, now, _ = _willow_run(tmp_path / "legacy", anchor=False)
    iid = legacy.incidents[0].incident_id
    assert round((now - spy[iid]) / H, 1) == 259.6       # the polygon's own age
    anchored, _, _ = _willow_run(tmp_path / "anchor", anchor=True)
    assert round((now - spy[iid]) / H, 1) == 19.7        # the confirming record
    note = next(n for n in anchored.notes if n.startswith(f"incident {iid}:"))
    assert "(+1.8%, within 2%)" in note and "CONFIRMED" in note

    def burned(res):
        e = res.ensembles[iid]
        return sum(1 for v in e.p50.data if v <= now)
    # 240 fewer hours of free-running spread: a fraction of the modeled burn
    assert burned(anchored) < 0.5 * burned(legacy)


def test_recorded_willow_growing_record_keeps_the_legacy_seeding(tmp_path, spy):
    """The same recorded fire with its record reporting 10 % MORE acres than
    the polygon: a growing fire — seeded at the polygon's own 259.6 h."""
    inc, now = _willow()
    inc.meta["reported_acres"] = inc.meta["perimeter_acres"] * 1.10
    res, _, _ = _willow_run(tmp_path, anchor=True, inc=inc, now=now)
    assert round((now - spy[inc.incident_id]) / H, 1) == 259.6
    assert any("lags a GROWING fire" in n for n in res.notes)


def test_recorded_willow_is_staleness_gated_when_its_record_ages_out(
        tmp_path, spy):
    """Seven days after the record, the anchor has nothing to keep alive: the
    staleness gate (record time) excludes the fire before any domain."""
    inc, now = _willow()
    later = inc.meta["reported_at_s"] + 169 * H
    res, _, _ = _willow_run(tmp_path, anchor=True, inc=inc, now=later)
    assert res.incidents == [] and spy == {}
    assert any(n.startswith("excluded 1 stale incident(s)") for n in res.notes)
