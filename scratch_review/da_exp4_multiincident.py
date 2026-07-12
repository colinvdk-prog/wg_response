"""EXP 4 — MULTI-INCIDENT / SHARED CREW POOL.

Part A (orchestration): ThreatMonitor with TWO simultaneous incidents 30 km
apart, disjoint client sets, ONE crew pool. Counts dispatcher.plan calls via a
proxy and checks every crew appears on a single, time-consistent route.

Part B (raster filtering): threat._maybe_dispatch forwards ONLY the ensembles
of incidents that produced jobs (`ens_used = {job.incident_id: ...}`).
Demonstrates at the dispatch seam that dropping a jobless incident's raster
flips a corridor verdict: same jobs, ens={A} vs ens={A,B} where B's fire sits
on the only route.

Part C (duplicate client): one client threatened by two incidents => two
DispatchJobs at the same location; shows the optimizer schedules BOTH stops
and counts the value twice.

Run: py -3 scratch_review/da_exp4_multiincident.py
"""
from __future__ import annotations

import math

from da_common import (T0, all_safe_ensemble, destination, mk_crew, mk_job,
                       uniform_arrival_ensemble)

from wgresponse import dispatch
from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (BBox, ClientProperty, FireIncident,
                                  IncidentSource, LatLon, ResponseConfig,
                                  SafeZone, WeatherForecast, WeatherSample)
from wgresponse.threat import ThreatMonitor

IGN_A = LatLon(34.2000, -117.6000)
IGN_B = destination(IGN_A, 90.0, 30_000.0)   # 30 km east


class StubFeed:
    def fetch_active(self, bbox=None):
        return [
            FireIncident(incident_id="INC-A", name="Alpha", source=IncidentSource.MOCK,
                         origin=IGN_A, updated_at_s=T0, active=True),
            FireIncident(incident_id="INC-B", name="Bravo", source=IncidentSource.MOCK,
                         origin=IGN_B, updated_at_s=T0, active=True),
        ]


class StubWeather:
    def forecast(self, loc, horizon_h):
        samples = [WeatherSample(t_s=T0 + h * 3600.0, wind_speed_mps=3.0,
                                 wind_gust_mps=4.0, wind_dir_deg=270.0,
                                 rh_pct=25.0, temp_c=30.0)
                   for h in range(int(horizon_h) + 1)]
        return WeatherForecast(loc=loc, samples=samples, source="stub")


class StubTerrain:
    """Uniform grass tile on whatever bbox/cell the monitor asks for."""
    def tile(self, bbox, cell_m):
        from wgresponse.contracts import GridSpec, Raster, TerrainTile
        m_lat = 111_320.0
        m_lon = m_lat * math.cos(math.radians((bbox.south + bbox.north) / 2.0))
        nrows = max(2, int((bbox.north - bbox.south) * m_lat / cell_m))
        ncols = max(2, int((bbox.east - bbox.west) * m_lon / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west,
                        cell_m=cell_m, nrows=nrows, ncols=ncols)
        n = nrows * ncols
        return TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                           slope_deg=Raster(spec, [0.0] * n),
                           aspect_deg=Raster(spec, [0.0] * n),
                           elev_m=Raster(spec, [300.0] * n), source="stub")


class StubRegistry:
    def __init__(self, clients):
        self._clients = clients

    def protected_clients(self, bbox=None):
        if bbox is None:
            return list(self._clients)
        return [c for c in self._clients if bbox.contains(c.loc)]


class StubTracker:
    def __init__(self, crews):
        self._crews = crews

    def crews(self):
        return list(self._crews)


class CountingDispatcher:
    def __init__(self):
        self.calls = []

    def plan(self, crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None):
        self.calls.append(dict(n_crews=len(crews), n_jobs=len(jobs),
                               incidents=sorted(ens),
                               job_incidents=sorted({j.incident_id for j in jobs})))
        return dispatch.plan(crews, jobs, ens, safe_zones, cfg, now_s,
                             incumbent=incumbent)


def part_a():
    print("=== PART A: two incidents, one crew pool, ThreatMonitor orchestration ===")
    clients = []
    for i in range(3):   # 3 clients ~2.5-4 km downwind (east) of each ignition
        clients.append(ClientProperty(client_id=f"A{i+1}", name=f"A{i+1}",
                                      loc=destination(IGN_A, 90.0, 2500.0 + 800.0 * i),
                                      value_usd=1_500_000.0))
        clients.append(ClientProperty(client_id=f"B{i+1}", name=f"B{i+1}",
                                      loc=destination(IGN_B, 90.0, 2500.0 + 800.0 * i),
                                      value_usd=1_500_000.0))
    crews = [mk_crew("G1", destination(IGN_A, 180.0, 8000.0), T0),
             mk_crew("G2", destination(IGN_B, 180.0, 8000.0), T0)]
    zones = [SafeZone("Z1", destination(IGN_A, 270.0, 10_000.0), "west of A"),
             SafeZone("Z2", destination(IGN_B, 135.0, 12_000.0), "SE of B")]
    cfg = ResponseConfig()
    cfg.grid_cell_m = 500.0
    cfg.grid_max_cells = 20_000
    cfg.ensemble_n = 2
    cfg.horizon_h = 48.0
    cfg.solver_budget_s = 3.0
    dispatcher = CountingDispatcher()
    monitor = ThreatMonitor(feeds=[StubFeed()], weather_provider=StubWeather(),
                            terrain_provider=StubTerrain(),
                            registry=StubRegistry(clients),
                            alert_engine=AlertEngine(cfg), cfg=cfg,
                            safe_zones=zones, crew_tracker=StubTracker(crews),
                            dispatcher=dispatcher)
    res = monitor.cycle(T0)
    print(f"notes: {res.notes}")
    print(f"assessments: {len(res.assessments)} "
          f"({sorted(set((a.client_id, a.incident_id) for a in res.assessments))[:6]}...)")
    print(f"events: {[(e.client_id, e.incident_id, e.state.value) for e in res.events]}")
    print(f"dispatcher.plan calls this cycle: {len(dispatcher.calls)}")
    for c in dispatcher.calls:
        print(f"  call: crews={c['n_crews']} jobs={c['n_jobs']} "
              f"ens_incidents={c['incidents']} job_incidents={c['job_incidents']}")
    plan = res.plan
    if plan is None:
        print("NO PLAN PRODUCED")
        return
    # crew double-booking check: each crew one route, stops time-ordered
    for cid, cp in sorted(plan.plans.items()):
        seq = [(s.client_id, s.incident_id, round((s.arrive_s - T0) / 3600.0, 2),
                round((s.depart_s - T0) / 3600.0, 2)) for s in cp.stops]
        ok = all(cp.stops[i].depart_s <= cp.stops[i + 1].arrive_s + 1e-6
                 for i in range(len(cp.stops) - 1))
        print(f"  {cid}: {seq}  time-consistent={ok}")
    print(f"  unserved: {[(u.client_id, u.incident_id, u.reason) for u in plan.unserved]}")
    served_inc = sorted({s.incident_id for cp in plan.plans.values() for s in cp.stops})
    print(f"  incidents served by the shared pool: {served_inc}")


def part_b():
    print("\n=== PART B: jobless incident's raster dropped from corridor checks ===")
    crew_loc = destination(IGN_A, 270.0, 12_000.0)          # west of A
    client_loc = destination(IGN_A, 90.0, 3_000.0)          # east of A
    # incident B's fire footprint sits mid-route, arrival = now (already burning)
    mid = destination(IGN_A, 270.0, 5_000.0)
    ens_a = all_safe_ensemble("INC-A")                       # A far from route
    ens_b = uniform_arrival_ensemble(mid, half_km=2.0, cell_m=250.0,
                                     arrival_s=T0, incident_id="INC-B")
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    crews = [mk_crew("G1", crew_loc, T0)]
    zones = [SafeZone("Z1", destination(IGN_A, 135.0, 9_000.0), "SE")]
    job = mk_job("C1", client_loc, 2_000_000.0, 1.0, "INC-A",
                 T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)
    plan_filtered = dispatch.plan(crews, [job], {"INC-A": ens_a}, zones, cfg, T0)
    job2 = mk_job("C1", client_loc, 2_000_000.0, 1.0, "INC-A",
                  T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)
    plan_full = dispatch.plan(crews, [job2], {"INC-A": ens_a, "INC-B": ens_b},
                              zones, cfg, T0)
    def describe(p, tag):
        stops = [(s.client_id, round((s.arrive_s - T0) / 60.0, 1))
                 for cp in p.plans.values() for s in cp.stops]
        print(f"  {tag}: served={len(stops)} {stops} "
              f"unserved={[(u.client_id, u.reason) for u in p.unserved]}")
    describe(plan_filtered, "ens={A}      (threat.py ens_used behavior)")
    describe(plan_full,     "ens={A,B}    (all assessed incidents)")


def part_c():
    print("\n=== PART C: one client threatened by two incidents ===")
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    loc = destination(IGN_A, 90.0, 3_000.0)
    crews = [mk_crew("G1", destination(IGN_A, 180.0, 6_000.0), T0)]
    zones = [SafeZone("Z1", destination(IGN_A, 270.0, 10_000.0), "west")]
    j1 = mk_job("C1", loc, 2_000_000.0, 1.0, "INC-A",
                T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)
    j2 = mk_job("C1", loc, 2_000_000.0, 1.0, "INC-B",
                T0 + 7.0 * 3600.0, T0 + 8.0 * 3600.0)
    ens = {"INC-A": all_safe_ensemble("INC-A"), "INC-B": all_safe_ensemble("INC-B")}
    plan = dispatch.plan(crews, [j1, j2], ens, zones, cfg, T0)
    for cid, cp in plan.plans.items():
        for s in cp.stops:
            print(f"  {cid}: gels {s.client_id} for {s.incident_id} "
                  f"service+{(s.service_start_s - T0)/3600.0:.2f}h "
                  f"depart+{(s.depart_s - T0)/3600.0:.2f}h")
    bd = plan.objective_breakdown
    print(f"  stops={bd['served']} value_counted=${bd['value_usd']:,.0f} "
          f"(property worth $2.0M x 0.85 = $1.7M once)")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
