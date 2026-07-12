"""ATTACK SUITE 4 — second-fire corridor (Sprint-2 A2, Council-1 dispatch F1).

Rebuilds the review's head-to-head (scratch_review/da_exp4_multiincident.py
part B: a jobless incident's raster dropped from `ens` let the planner route a
crew straight through fire B) and attacks the fixed chain from both ends:

  * dispatch seam: fire B mid-route with ZERO jobs -> stop blocked,
    unserved reason no_safe_corridor (and the control without B's raster
    still serves, proving the raster is load-bearing);
  * third fire C with jobs only on crew-2's side: crew-2 serves its side,
    crew-1's through-B job stays blocked — extra rasters/jobs must not
    launder the corridor verdict;
  * threat seam (the actual A2 fix, threat.py `_maybe_dispatch`): a spy
    dispatcher on a full ThreatMonitor cycle proves EVERY assessed ensemble
    is forwarded even when only one incident produced jobs, and the emitted
    plan blocks the through-fire route end to end.

Run:  py -3 -m pytest -q scratch_review/attack_corridor_multifire.py
Deterministic; no network. Product code untouched (read-only attack).
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from da_common import (T0, all_safe_ensemble, destination, mk_crew, mk_job,
                       uniform_arrival_ensemble)

from wgresponse import dispatch
from wgresponse.contracts import (
    ClientProperty,
    FireIncident,
    GridSpec,
    IncidentSource,
    LatLon,
    Raster,
    ResponseConfig,
    SafeZone,
    TerrainTile,
    WeatherForecast,
    WeatherSample,
)
from wgresponse.threat import ThreatMonitor

IGN_A = LatLon(34.2000, -117.6000)


def _cfg(**kw) -> ResponseConfig:
    cfg = ResponseConfig()
    cfg.solver_budget_s = 3.0
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# ===========================================================================
# 4a — the review head-to-head: fire B mid-route, ZERO jobs for B
# ===========================================================================

def test_fire_b_mid_route_zero_jobs_blocks_with_no_safe_corridor():
    """Port of scratch_review/da_exp4_multiincident.py part B. The Council-1
    evidence: ens={A} served C1 straight through B's footprint; the fix must
    make ens={A,B} refuse with reason no_safe_corridor."""
    crew_loc = destination(IGN_A, 270.0, 12_000.0)       # 12 km west
    client_loc = destination(IGN_A, 90.0, 3_000.0)       # 3 km east
    mid = destination(IGN_A, 270.0, 5_000.0)             # B burning mid-route NOW
    ens_a = all_safe_ensemble("INC-A")
    ens_b = uniform_arrival_ensemble(mid, half_km=2.0, cell_m=250.0,
                                     arrival_s=T0, incident_id="INC-B")
    cfg = _cfg()
    crews = [mk_crew("G1", crew_loc, T0)]
    zones = [SafeZone("Z1", destination(IGN_A, 135.0, 9_000.0), "SE")]

    # control: without B's raster the planner happily drives through the fire
    job = mk_job("C1", client_loc, 2_000_000.0, 1.0, "INC-A",
                 T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)
    plan_blind = dispatch.plan(crews, [job], {"INC-A": ens_a}, zones, cfg, T0)
    served_blind = [s.client_id for cp in plan_blind.plans.values()
                    for s in cp.stops]
    assert served_blind == ["C1"], (
        "control broke: the through-fire route should look feasible when B's "
        "raster is withheld (that is exactly the pre-fix bug)")

    # attack: B's raster present (jobless incident) -> stop must be refused
    job2 = mk_job("C1", client_loc, 2_000_000.0, 1.0, "INC-A",
                  T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)
    plan_full = dispatch.plan(crews, [job2], {"INC-A": ens_a, "INC-B": ens_b},
                              zones, cfg, T0)
    served_full = [s.client_id for cp in plan_full.plans.values()
                   for s in cp.stops]
    assert served_full == [], "crew routed THROUGH fire B (jobless incident)"
    assert [(u.client_id, u.reason) for u in plan_full.unserved] == \
        [("C1", "no_safe_corridor")]


# ===========================================================================
# 4b — third fire, jobs only on crew-2's side
# ===========================================================================

def test_third_fire_with_jobs_only_for_crew2_side():
    """Fires A (job C1, crew-1 side), B (jobless, mid crew-1 route), C (job C2,
    crew-2 side, footprint off-route). Crew-2 must serve C2; C1 must stay
    unserved no_safe_corridor (crew-1 blocked by B; crew-2 out of deadline
    range) — extra fires and jobs must not launder the verdict."""
    crew1_loc = destination(IGN_A, 270.0, 12_000.0)
    client1 = destination(IGN_A, 90.0, 3_000.0)
    mid = destination(IGN_A, 270.0, 5_000.0)

    south = destination(IGN_A, 180.0, 30_000.0)          # crew-2 world, 30 km S
    crew2_loc = destination(south, 270.0, 4_000.0)
    client2 = destination(south, 90.0, 3_000.0)
    fire_c_foot = destination(south, 90.0, 12_000.0)     # off crew-2's routes

    ens = {
        "INC-A": all_safe_ensemble("INC-A"),
        "INC-B": uniform_arrival_ensemble(mid, half_km=2.0, cell_m=250.0,
                                          arrival_s=T0, incident_id="INC-B"),
        "INC-C": uniform_arrival_ensemble(fire_c_foot, half_km=1.5, cell_m=250.0,
                                          arrival_s=T0, incident_id="INC-C"),
    }
    cfg = _cfg()
    crews = [mk_crew("G1", crew1_loc, T0), mk_crew("G2", crew2_loc, T0)]
    zones = [SafeZone("Z1", destination(IGN_A, 135.0, 9_000.0), "SE of A"),
             SafeZone("Z2", destination(south, 135.0, 9_000.0), "SE of south")]

    # C1 deadline tuned so crew-1 is timing-comfortable but corridor-blocked,
    # while crew-2 (30+ km away) fits the deadline only sans the slack floor:
    # latest_depart = p10 - 30 min = T0+90 min; G1 departs T0+56 min (34 min
    # slack, ok -> corridor check fires and fails on B); G2 departs T0+84 min
    # (6 min slack -> 'slack'). diagnose priority corridor > slack must
    # surface no_safe_corridor.
    p10_c1 = T0 + 120.0 * 60.0
    job_c1 = mk_job("C1", client1, 2_000_000.0, 1.0, "INC-A",
                    p10_c1, p10_c1 + 3600.0)
    job_c2 = mk_job("C2", client2, 1_500_000.0, 1.0, "INC-C",
                    T0 + 5.0 * 3600.0, T0 + 6.0 * 3600.0)

    plan = dispatch.plan(crews, [job_c1, job_c2], ens, zones, cfg, T0)
    stops = {cid: [s.client_id for s in cp.stops]
             for cid, cp in plan.plans.items()}
    assert stops.get("G2") == ["C2"], f"crew-2 failed to serve its side: {stops}"
    assert stops.get("G1", []) == [], (
        f"crew-1 was routed through jobless fire B: {stops}")
    unserved = {(u.client_id): u.reason for u in plan.unserved}
    assert unserved == {"C1": "no_safe_corridor"}, unserved


# ===========================================================================
# 4c — the threat seam itself: ThreatMonitor forwards ALL assessed ensembles
# ===========================================================================

class _StubFeed:
    last_fetch_ok = True

    def __init__(self, incidents):
        self._incidents = incidents

    def fetch_active(self, bbox=None):
        return list(self._incidents)


class _StubWeather:
    """Constant wind FROM the north (pushes fire A south toward the client)."""

    def forecast(self, loc, horizon_h):
        samples = [WeatherSample(t_s=T0 + h * 3600.0, wind_speed_mps=5.0,
                                 wind_gust_mps=7.0, wind_dir_deg=0.0,
                                 rh_pct=25.0, temp_c=30.0)
                   for h in range(int(horizon_h) + 1)]
        return WeatherForecast(loc=loc, samples=samples, source="stub")


class _GrassTerrain:
    """Uniform flat GR2 tile for whatever bbox/cell the monitor asks."""

    def tile(self, bbox, cell_m):
        m_lat = 111_320.0
        m_lon = m_lat * math.cos(math.radians((bbox.south + bbox.north) / 2.0))
        nrows = max(2, int((bbox.north - bbox.south) * m_lat / cell_m))
        ncols = max(2, int((bbox.east - bbox.west) * m_lon / cell_m))
        spec = GridSpec(north_lat=bbox.north, west_lon=bbox.west,
                        cell_m=cell_m, nrows=nrows, ncols=ncols)
        n = nrows * ncols
        return TerrainTile(spec=spec, fuel=Raster(spec, [102.0] * n),
                           slope_deg=Raster(spec, [0.0] * n),
                           aspect_deg=Raster(spec, [-1.0] * n),
                           elev_m=Raster(spec, [300.0] * n), source="stub")


class _StubRegistry:
    def __init__(self, clients):
        self._clients = clients

    def protected_clients(self, bbox=None):
        if bbox is None:
            return list(self._clients)
        return [c for c in self._clients if bbox.contains(c.loc)]


class _StubTracker:
    def __init__(self, crews):
        self._crews = crews

    def crews(self):
        return list(self._crews)


class _OnePairExtremeEngine:
    """Alert-engine stub: exactly one pair (C1, INC-A) pinned EXTREME so the
    monitor builds a job ONLY for INC-A; INC-B stays assessed-but-jobless."""

    def __init__(self, key):
        self._key = key

    def evaluate(self, assessments, now_s, blind_pairs=None, feeds_healthy=True):
        return []

    def states(self):
        from wgresponse.contracts import AlertRecord, AlertState
        return {self._key: AlertRecord(client_id=self._key[0],
                                       incident_id=self._key[1],
                                       state=AlertState.EXTREME_24H)}


class _SpyDispatcher:
    def __init__(self):
        self.calls = []

    def plan(self, crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None):
        self.calls.append({
            "ens_ids": sorted(ens),
            "job_ids": sorted({j.incident_id for j in jobs}),
            "n_crews": len(crews),
        })
        return dispatch.plan(crews, jobs, ens, safe_zones, cfg, now_s,
                             incumbent=incumbent)


def test_monitor_forwards_jobless_ensembles_and_blocks_through_fire_route():
    """Full ThreatMonitor cycle: fire A (north, blowing at C1) produces the
    only job; fire B is a burning ring sitting mid-route between the crew and
    C1 with NO pair in a dispatch state. The A2 fix (threat.py: pass full
    `ensembles`, never `ens_used`) must (a) hand the dispatcher BOTH rasters
    and (b) leave (C1, INC-A) unserved with no_safe_corridor."""
    client1 = LatLon(34.2000, -117.6000)
    fire_a = destination(client1, 0.0, 6_000.0)          # 6 km north of C1
    ring_c = destination(client1, 270.0, 5_000.0)        # 5 km west, ON the route
    ring = [destination(ring_c, b, 800.0) for b in range(0, 360, 24)]
    ring.append(ring[0])

    inc_a = FireIncident(incident_id="INC-A", name="Alpha",
                         source=IncidentSource.MOCK, origin=fire_a,
                         updated_at_s=T0 - 1800.0, active=True)
    inc_b = FireIncident(incident_id="INC-B", name="Bravo",
                         source=IncidentSource.MOCK, origin=ring_c,
                         updated_at_s=T0 - 900.0, active=True,
                         perimeter=[ring])

    crew = mk_crew("G1", destination(client1, 270.0, 10_000.0), T0)
    zones = [SafeZone("Z1", destination(client1, 135.0, 8_000.0), "SE")]
    client = ClientProperty(client_id="C1", loc=client1, name="C1",
                            value_usd=2_000_000.0)

    cfg = _cfg(grid_cell_m=150.0, grid_max_cells=40_000, ensemble_n=2,
               horizon_h=48.0)
    spy = _SpyDispatcher()
    mon = ThreatMonitor(feeds=[_StubFeed([inc_a, inc_b])],
                        weather_provider=_StubWeather(),
                        terrain_provider=_GrassTerrain(),
                        registry=_StubRegistry([client]),
                        alert_engine=_OnePairExtremeEngine(("C1", "INC-A")),
                        cfg=cfg, safe_zones=zones,
                        crew_tracker=_StubTracker([crew]),
                        dispatcher=spy)
    res = mon.cycle(T0)

    assessed = sorted({a.incident_id for a in res.assessments})
    assert assessed == ["INC-A", "INC-B"], res.notes
    assert len(spy.calls) == 1, res.notes
    call = spy.calls[0]
    assert call["job_ids"] == ["INC-A"], "setup broke: B produced a job"
    assert call["ens_ids"] == ["INC-A", "INC-B"], (
        "A2 REGRESSION: the jobless incident's ensemble was filtered out of "
        "the dispatcher call — corridor checks are blind to fire B again")

    plan = res.plan
    assert plan is not None
    served = [s.client_id for cp in plan.plans.values() for s in cp.stops]
    assert served == [], "crew routed through fire B's burning ring"
    assert [(u.client_id, u.reason) for u in plan.unserved] == \
        [("C1", "no_safe_corridor")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
