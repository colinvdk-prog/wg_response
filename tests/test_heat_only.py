"""tests/test_heat_only.py — SATELLITE HEAT ONLY (founder 2026-10-30: "heat
only" until the hotspot screening protocol is decided, with an operator
note for heat within 8 km of a home; contracts hotspot_heat_only,
hotspot_heat_note_m, SAFETY SEMANTICS 41; watch --hotspot-heat-only).

  OFF       a FIRMS cluster near a home is forecast (the legacy cycle).
  HEAT      on: the cluster is listed after the forecast fires and never
            forecast, assessed or snapshotted; a reported fire beside it is
            forecast as before and the detections on it still join it (39).
  ABSENCE   a pair a cluster raised before clears by the absence law —
            never blind, never a degradation.
  OPERATOR  heat within hotspot_heat_note_m of a home is listed every cycle
            and is one operator event while it stays in range (again after
            it leaves and returns); beyond it, or with 0, nothing is
            listed; a registry failure there is a note.
  SURFACE   the summary row says why there is no domain; the daemon's
            state key; the hotspot rule; the watch flag and startup note;
            the page; config validation."""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import offmap_stack as om  # noqa: E402
import seeded_stack as ss  # noqa: E402
from tests.test_hotspots import _cluster, _square  # noqa: E402
from tests.test_threat import (  # noqa: E402
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
from wgresponse import cli, geo, threat, worldview  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState,
    FireIncident,
    IncidentSource,
    ResponseConfig,
    validate_config,
)

PAGE = Path(__file__).resolve().parent.parent / "console" / "world.html"


def _near_cluster():
    """Two detections 500 / 700 m north of the origin: 1.6 km from c-near
    (1.5 km east of the origin), 100 km from c-far."""
    return _cluster([geo.destination(ORIGIN, 0.0, 500.0),
                     geo.destination(ORIGIN, 0.0, 700.0)])


def _monitor(cfg, feed, registry=None, alert_engine=None):
    return make_monitor(feeds=[feed], cfg=cfg, alert_engine=alert_engine,
                        registry=registry or StubRegistry([near_client(),
                                                           far_client()]))


def _heat_events(res):
    return [e for e in res.operator_events if e.kind == "satellite_heat"]


# ---------------------------------------------------------------------------
# OFF / HEAT
# ---------------------------------------------------------------------------

def test_off_a_cluster_near_a_home_is_forecast():
    cl = _near_cluster()
    r = _monitor(make_cfg(), StubFeed([cl])).cycle(NOW)
    assert cl.incident_id in r.ensembles
    assert [a for a in r.assessments if a.incident_id == cl.incident_id]
    assert not any("satellite heat" in n for n in r.notes)


def test_on_the_cluster_is_listed_as_heat_and_never_forecast():
    cl = _near_cluster()
    cfg = make_cfg(hotspot_heat_only=True)
    mon = _monitor(cfg, StubFeed([cl]), alert_engine=AlertEngine(cfg))
    r = mon.cycle(NOW)
    assert [i.incident_id for i in r.incidents] == [cl.incident_id]
    assert r.ensembles == {} and r.assessments == []
    assert mon._alert_engine.states() == {}
    assert mon.last_snapshot.incidents == ()      # the TEST world never sees it
    note = next(n for n in r.notes
                if n.startswith(f"incident {cl.incident_id}: "))
    assert "satellite heat only" in note and "spread skipped" in note
    assert any(n.startswith("satellite heat only: 1 FIRMS cluster(s)")
               and "1 within 8 km of a protected home" in n for n in r.notes)
    assert not r.degraded


def test_a_reported_fire_is_forecast_and_the_heat_on_it_joins_it():
    fire = dataclasses.replace(incident(), perimeter=_square(ORIGIN, 400.0))
    on = _cluster([geo.destination(ORIGIN, 0.0, 100.0)])
    away = _cluster([geo.destination(ORIGIN, 270.0, 6000.0)])
    cfg = make_cfg(fuse_hotspots=True, hotspot_heat_only=True)
    r = _monitor(cfg, StubFeed([fire, on, away])).cycle(NOW)
    assert [i.incident_id for i in r.incidents] == ["inc-1", away.incident_id]
    assert "inc-1" in r.ensembles and away.incident_id not in r.ensembles
    assert len(r.incidents[0].hotspots) == 1
    assert {a.incident_id for a in r.assessments} == {"inc-1"}


def test_only_satellite_only_incidents_are_heat():
    cl = _near_cluster()
    assert threat._satellite_only(cl)
    assert not threat._satellite_only(dataclasses.replace(
        cl, source_ids={"firms": cl.incident_id, "wfigs": "w-1"}))
    reported = dataclasses.replace(incident(), source=IncidentSource.WFIGS,
                                   source_ids={"wfigs": "w-1",
                                               "firms": "firms:1"})
    assert not threat._satellite_only(reported)
    assert not threat._satellite_only(dataclasses.replace(
        cl, incident_id="seeded:x"))


# ---------------------------------------------------------------------------
# ABSENCE
# ---------------------------------------------------------------------------

def test_a_pair_raised_before_clears_by_the_absence_law():
    cl = _near_cluster()
    engine = AlertEngine(make_cfg())
    before = _monitor(make_cfg(), StubFeed([cl]), alert_engine=engine)
    before.cycle(NOW)
    pairs = [k for k in engine.states() if k[1] == cl.incident_id]
    assert pairs and all(engine.states()[k].state is not AlertState.NONE
                         for k in pairs)
    # a restart with the rule armed, the alert states restored
    after = _monitor(make_cfg(hotspot_heat_only=True), StubFeed([cl]),
                     alert_engine=engine)
    for k in range(3):
        r = after.cycle(NOW + 300.0 * (k + 1))
        assert not r.degraded
        assert not [e for e in r.operator_events
                    if e.kind in ("provider_error", "degraded")]
    assert all(engine.states()[k].state is AlertState.CLEARED for k in pairs)
    assert all(engine.states()[k].blind_cycles == 0 for k in pairs)


# ---------------------------------------------------------------------------
# OPERATOR
# ---------------------------------------------------------------------------

def test_heat_near_a_home_is_listed_each_cycle_and_announced_once():
    cl = _near_cluster()
    feed = StubFeed([cl])
    mon = _monitor(make_cfg(hotspot_heat_only=True), feed)
    ev = _heat_events(mon.cycle(NOW))
    assert len(ev) == 1 and ev[0].incident_id == cl.incident_id
    assert "km from c-near" in ev[0].message
    assert "unscreened, no forecast or alerts" in ev[0].message
    st = mon.heat_status()
    assert st["note_m"] == 8000.0
    (row,) = st["near"]
    assert row["nearest_client_id"] == "c-near" and row["homes_in_range"] == 1
    assert 1500 < row["nearest_m"] < 1700 and row["n_detections"] == 2
    assert row["satellites"] == ["VIIRS_NOAA20_NRT"]
    assert _heat_events(mon.cycle(NOW + 300.0)) == []     # once while in range
    assert len(mon.heat_status()["near"]) == 1            # still listed
    feed._incidents = []
    mon.cycle(NOW + 600.0)
    assert mon.heat_status()["near"] == []
    feed._incidents = [cl]
    assert len(_heat_events(mon.cycle(NOW + 900.0))) == 1  # back in range


def test_heat_beyond_the_radius_or_with_radius_zero_is_not_listed():
    far = _cluster([geo.destination(ORIGIN, 270.0, 20_000.0)])
    mon = _monitor(make_cfg(hotspot_heat_only=True), StubFeed([far]))
    r = mon.cycle(NOW)
    assert [i.incident_id for i in r.incidents] == [far.incident_id]
    assert mon.heat_status()["near"] == [] and _heat_events(r) == []
    assert any("0 within 8 km" in n for n in r.notes)
    mon = _monitor(make_cfg(hotspot_heat_only=True, hotspot_heat_note_m=0.0),
                   StubFeed([_near_cluster()]))
    r = mon.cycle(NOW)
    assert mon.heat_status() == {"note_m": 0.0, "near": []}
    assert _heat_events(r) == [] and r.ensembles == {}


class _BrokenRegistry(StubRegistry):
    def protected_clients(self, bbox=None):
        raise RuntimeError("registry down")


def test_a_registry_failure_in_the_heat_check_is_a_note():
    r = _monitor(make_cfg(hotspot_heat_only=True), StubFeed([_near_cluster()]),
                 registry=_BrokenRegistry([])).cycle(NOW)
    assert any("heat near homes not checked (registry: registry down)" in n
               for n in r.notes)
    assert not [e for e in r.operator_events if e.kind == "provider_error"]
    assert not r.degraded


# ---------------------------------------------------------------------------
# SURFACE
# ---------------------------------------------------------------------------

def test_the_summary_row_says_why_there_is_no_domain():
    cl = _near_cluster()
    r = _monitor(make_cfg(hotspot_heat_only=True), StubFeed([cl])).cycle(NOW)
    out = worldview.build_summary({}, r, geometry=False)
    row = next(x for x in out["incidents"] if x["incident_id"] == cl.incident_id)
    assert row["domain"] is None
    assert row["why_no_domain"].startswith("satellite heat only")


def test_the_daemon_lists_heat_near_homes_and_the_rule(tmp_path):
    near_b = FireIncident(
        incident_id="firms:39.000,-120.000", name="FIRMS cluster",
        source=IncidentSource.FIRMS, origin=geo.destination(om.CB_LOC, 0.0,
                                                            3000.0),
        updated_at_s=ss.T0 - 900.0, active=True,
        hotspots=[geo.destination(om.CB_LOC, 0.0, 3000.0)],
        source_ids={"firms": "firms:39.000,-120.000"},
        meta={"hotspot_t_s": [ss.T0 - 900.0], "satellites": ["N21"],
              "max_frp": 7.0})
    cfg = ss.small_cfg(fuse_hotspots=True, hotspot_heat_only=True)
    daemon, monitor, _feed, _engine = om.make_stack(
        str(tmp_path), cfg, incidents=[om.slow_incident(), near_b])
    daemon.run_cycle()
    st = daemon.state()
    (row,) = st["heat_near_homes"]["near"]
    assert row["nearest_client"] == "Casa B" and 2900 < row["nearest_m"] < 3100
    assert st["hotspot_rule"]["screening"]["rule"] == "heat_only"
    assert any(r["incident_id"] == near_b.incident_id for r in st["incidents"])
    off, *_ = om.make_stack(str(tmp_path / "off"), ss.small_cfg(),
                            incidents=[om.slow_incident()])
    off.run_cycle()
    assert "heat_near_homes" not in off.state()


def test_the_rule_names_the_interim_screening():
    rule = worldview.hotspot_rule(make_cfg(fuse_hotspots=True,
                                           hotspot_heat_only=True))
    assert rule["screening"]["rule"] == "heat_only"
    assert rule["screening"]["note_m"] == 8000.0
    assert worldview.hotspot_rule(make_cfg(fuse_hotspots=True))["screening"] \
        is None


def test_the_watch_flag_arms_the_rule_with_its_note():
    args = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json", "--hotspot-heat-only"])
    assert args.hotspot_heat_only is True
    cfg, notes = cli._heat_snap_cfg(ResponseConfig(), args)
    assert cfg.hotspot_heat_only and cfg.forecast_domain_snap_deg == 0.0
    assert len(notes) == 1 and notes[0].startswith("SATELLITE HEAT ONLY")
    assert "SAFETY SEMANTICS 41" in notes[0]
    plain = cli._build_parser().parse_args(
        ["watch", "--aoi", "data/aoi_conus.json"])
    assert cli._heat_snap_cfg(ResponseConfig(), plain) == (ResponseConfig(),
                                                           [])


def test_the_page_shows_heat_near_homes():
    html = PAGE.read_text(encoding="utf-8")
    assert "function heatNearChip(d)" in html
    assert "offmapChip(c) + heatNearChip(d)" in html
    assert "SAFETY SEMANTICS 41" in html


def test_config_defaults_and_validation():
    cfg = ResponseConfig()
    assert cfg.hotspot_heat_only is False and cfg.hotspot_heat_note_m == 8000.0
    assert validate_config(cfg) == []
    for v in (-1.0, 60_001.0, float("inf"), float("nan")):
        probs = validate_config(dataclasses.replace(cfg, hotspot_heat_note_m=v))
        assert any("hotspot_heat_note_m" in p for p in probs), v
