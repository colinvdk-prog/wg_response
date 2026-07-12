"""Integrator full-loop test: REAL modules end to end, no stubs.

MockFireFeed (one wind-driven fire whose front advances 300 m downwind per
feed update) + MockWeather (northerly wind ramping 1.2 -> 2.2 m/s) +
SyntheticTerrain('grass') + MockClientRegistry (8 clients on the downwind
ray, 0.7-7.0 km) + CrewTracker (2 crews) + AlertEngine(LogNotifier) +
ThreatMonitor dispatching through wgresponse.dispatch.

Three cycles at 15-minute steps. Asserts: the alert ladder emits >=1
NOTICE_72H and >=1 EXTREME_24H, every planned stop has margin_h >= 0 and
departs by safety.latest_departure_s(eta_p10), per-client ETAs shrink as the
front advances, and cycle() never raises (no degradation notes either).

All inputs are seeded/deterministic (contracts.seeded_rng); grid stays
within 120x120 cells and the whole run takes a few seconds. Units: unix
seconds, meters, m/s per contracts.
"""
from __future__ import annotations

import math

import pytest

from wgresponse import dispatch, geo, safety
from wgresponse.alerts import AlertEngine
from wgresponse.clients import MockClientRegistry
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    CrewBeacon,
    CycleResult,
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    SafeZone,
    assert_valid_transition,
)
from wgresponse.crews import CrewTracker
from wgresponse.feeds import MockFireFeed
from wgresponse.notify import LogNotifier
from wgresponse.terrain import SyntheticTerrain
from wgresponse.threat import ThreatMonitor
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0            # mid-2026, matches the mock modules' default epoch
STEP_S = 900.0                  # 15-minute cycle cadence
N_CYCLES = 3
ORIGIN = LatLon(34.30, -118.50)             # ignition; wind FROM 0 deg -> heads south
CLIENT_DISTS_M = [700, 1100, 1600, 2200, 2900, 3800, 5000, 7000]
INCIDENT_ID = "mock-ray-fire"
FRONT_ADVANCE_M = 300.0         # per-cycle feed update: front moves this far downwind


@pytest.fixture(scope="module")
def loop_run():
    """Run the full 3-cycle loop once; individual tests assert on the record."""
    cfg = ResponseConfig(grid_cell_m=100.0)

    clients = [
        ClientProperty(client_id=f"c-{i + 1:02d}",
                       loc=geo.destination(ORIGIN, 180.0, d),
                       name=f"Ray client {i + 1}")
        for i, d in enumerate(CLIENT_DISTS_M)
    ]
    registry = MockClientRegistry(seed="itest", clients=clients)

    incident = FireIncident(
        incident_id=INCIDENT_ID, name="Ray Fire", source=IncidentSource.MOCK,
        origin=ORIGIN, updated_at_s=T0, active=True)
    feed = MockFireFeed(seed="itest", incidents=[incident])

    weather = MockWeather(
        seed="itest", base_wind_mps=1.2, dir_deg=0.0, rh_pct=25.0, temp_c=30.0,
        shifts=[(1, 1.8, 0.0, 24.0), (2, 2.2, 0.0, 22.0)], t0_s=T0)
    terrain = SyntheticTerrain(seed="itest", kind="grass")

    safe_zones = [
        SafeZone("sz-south", geo.destination(ORIGIN, 166.0, 7300.0), "SE staging"),
        SafeZone("sz-north", geo.destination(ORIGIN, 0.0, 1500.0), "upwind lot"),
    ]
    notifier = LogNotifier()
    engine = AlertEngine(cfg, notifier=notifier,
                         clients_by_id={c.client_id: c for c in clients})
    tracker = CrewTracker(cfg)
    crew_locs = {
        "crew-1": geo.destination(ORIGIN, 162.0, 5200.0),
        "crew-2": geo.destination(ORIGIN, 168.0, 6800.0),
    }

    monitor = ThreatMonitor([feed], weather, terrain, registry, engine, cfg,
                            safe_zones, crew_tracker=tracker, dispatcher=dispatch)

    results = []
    for i in range(N_CYCLES):
        now = T0 + i * STEP_S
        # Feed update between cycles: the front has advanced downwind (the
        # served FireIncident object is what MockFireFeed returns each fetch).
        incident.hotspots = [geo.destination(ORIGIN, 180.0, FRONT_ADVANCE_M * i)]
        incident.updated_at_s = now
        # Crews beacon every cycle (beacon_stale_s=600 < 900 s cadence).
        for cid, loc in crew_locs.items():
            assert tracker.ingest(CrewBeacon(crew_id=cid, loc=loc, t_s=now))
        results.append(monitor.cycle(now))    # must never raise

    return {"cfg": cfg, "results": results, "clients": clients,
            "notifier": notifier, "engine": engine}


def _all_events(results):
    return [e for r in results for e in r.events]


def _all_stops(results):
    """(now_s, crew_id, PlannedStop) triples across every cycle's plan."""
    out = []
    for r in results:
        if r.plan is None:
            continue
        for crew_id, cp in r.plan.plans.items():
            for stop in cp.stops:
                out.append((r.t_s, crew_id, stop))
    return out


# ---------------------------------------------------------------------------
# Loop mechanics
# ---------------------------------------------------------------------------

def test_cycles_complete_clean(loop_run):
    """cycle() never raises and no step degraded (notes stay empty)."""
    results = loop_run["results"]
    assert len(results) == N_CYCLES
    for i, r in enumerate(results):
        assert isinstance(r, CycleResult)
        assert r.t_s == T0 + i * STEP_S
        assert r.notes == [], f"cycle {i + 1} degraded: {r.notes}"
        assert [inc.incident_id for inc in r.incidents] == [INCIDENT_ID]
        assert INCIDENT_ID in r.ensembles


def test_every_client_assessed_each_cycle(loop_run):
    for r in loop_run["results"]:
        assessed = {a.client_id for a in r.assessments}
        assert assessed == {c.client_id for c in loop_run["clients"]}
        for a in r.assessments:
            assert a.incident_id == INCIDENT_ID
            assert a.computed_at_s == r.t_s
            # ETA ordering law: p10 (earliest/pessimistic) <= p50 <= p90
            assert a.eta.eta_p10_s <= a.eta.eta_p50_s <= a.eta.eta_p90_s
            assert a.eta.eta_p50_s != math.inf     # everyone in reach in-horizon


def test_grid_stays_small(loop_run):
    for r in loop_run["results"]:
        spec = r.ensembles[INCIDENT_ID].spec
        assert spec.nrows <= 120 and spec.ncols <= 120


# ---------------------------------------------------------------------------
# Alert ladder
# ---------------------------------------------------------------------------

def test_notice_and_extreme_events_emitted(loop_run):
    events = _all_events(loop_run["results"])
    states = [e.state for e in events]
    assert AlertState.NOTICE_72H in states
    assert AlertState.EXTREME_24H in states


def test_all_event_transitions_legal_and_notified(loop_run):
    events = _all_events(loop_run["results"])
    for e in events:
        assert_valid_transition(e.prev, e.state)   # raises on an illegal hop
    assert len(loop_run["notifier"].sent) == len(events)


def test_engine_states_consistent_with_events(loop_run):
    records = loop_run["engine"].states()
    for e in _all_events(loop_run["results"]):
        assert (e.client_id, e.incident_id) in records


# ---------------------------------------------------------------------------
# Dispatch safety invariants (the load-bearing rules)
# ---------------------------------------------------------------------------

def test_plan_produced_every_cycle_with_stops(loop_run):
    for r in loop_run["results"]:
        assert r.plan is not None
        assert sum(len(cp.stops) for cp in r.plan.plans.values()) >= 1


def test_every_stop_margin_nonnegative(loop_run):
    stops = _all_stops(loop_run["results"])
    assert stops, "no planned stops to verify"
    for now_s, crew_id, stop in stops:
        assert stop.margin_h >= 0.0, (
            f"crew {crew_id} stop {stop.client_id}: negative margin {stop.margin_h}")


def test_every_stop_departs_by_latest_departure(loop_run):
    cfg = loop_run["cfg"]
    for r in loop_run["results"]:
        etas = {(a.client_id, a.incident_id): a.eta for a in r.assessments}
        if r.plan is None:
            continue
        for cp in r.plan.plans.values():
            for stop in cp.stops:
                assert stop.depart_s <= stop.latest_depart_s + 1e-6
                # stop deadline must equal the safety seam's 30-min rule on
                # the pessimistic ETA assessed THIS cycle
                eta = etas[(stop.client_id, stop.incident_id)]
                expected = safety.latest_departure_s(eta.eta_p10_s, cfg)
                assert abs(stop.latest_depart_s - expected) < 1.0


def test_stop_timeline_and_service_duration(loop_run):
    cfg = loop_run["cfg"]
    for now_s, crew_id, stop in _all_stops(loop_run["results"]):
        assert stop.arrive_s >= now_s - 1e-6
        assert stop.service_start_s >= stop.arrive_s - 1e-6
        # full deploy_min gel application fits before departure
        assert stop.depart_s >= stop.service_start_s + cfg.deploy_min * 60.0 - 1e-6
        assert 0.0 < stop.gel_fresh_frac <= 1.0


def test_crews_with_stops_have_egress_zone(loop_run):
    zone_ids = {"sz-south", "sz-north"}
    for r in loop_run["results"]:
        for cp in r.plan.plans.values():
            if cp.stops:
                assert cp.egress_zone_id in zone_ids


# ---------------------------------------------------------------------------
# Shrinking-ETA fire
# ---------------------------------------------------------------------------

def test_etas_shrink_as_front_advances(loop_run):
    """With the front advancing 300 m/cycle, every client's median
    hours-to-impact must strictly decrease cycle over cycle."""
    results = loop_run["results"]
    per_client = {c.client_id: [] for c in loop_run["clients"]}
    for r in results:
        for a in r.assessments:
            per_client[a.client_id].append(a.eta.hours_out(r.t_s)[1])   # p50
    for cid, series in per_client.items():
        assert len(series) == N_CYCLES
        for earlier, later in zip(series, series[1:]):
            assert later < earlier, f"{cid}: ETA did not shrink ({series})"
