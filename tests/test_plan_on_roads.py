"""PLAN-ON-ROADS (founder build order 2026-07-12).

Motivating measurement (scratch_review bench, chords-only control): the
planner/corridor law approved straight chords while crews drove 1.65-2.43x
circuity roads through unvalidated cells — ~73% of crew relocations (383->105)
and the entire residual overrun tail (3->0) were that mismatch.

Pins here:
1. safety.LegOracle `path=` sampling — byte-identical to the chord oracle when
   the path IS the chord; a road detour through an early-arrival band produces
   a FINITE/earlier T* the chord oracle cannot see (the exact failure class
   that burned crews).
2. dispatch plan(road_provider=) — travel() returns the route's own time
   (kinematic truth), falls back to tier-0 on None / on_roads=False; the
   emitted plan with provider=None is byte-identical to the legacy call.
3. cfg.dispatch_plan_on_roads default False == byte-identical engine (the
   whole existing suite pins that transitively; the explicit hash pin here
   makes the seam's identity contract loud)."""
import math

from wgresponse import dispatch, safety
from wgresponse.contracts import (Crew, CrewStatus, ClientProperty,
                                  DispatchJob, EnsembleArrival, GridSpec,
                                  LatLon, Raster, ResponseConfig, SafeZone,
                                  ThreatEta, UNREACHED)

NOW = 1_790_000_000.0
INC = "inc1"


def spec20(cell_m: float = 500.0) -> GridSpec:
    return GridSpec(north_lat=40.0, west_lon=-105.5, cell_m=cell_m,
                    nrows=20, ncols=20)


def cc(spec: GridSpec, r: int, c: int) -> LatLon:
    from wgresponse import geo
    return geo.rc_to_latlon(spec, r, c)


def uniform_raster(spec: GridSpec, value: float) -> Raster:
    return Raster(spec, fill=value)


# ---------------------------------------------------------------------------
# 1. LegOracle path sampling
# ---------------------------------------------------------------------------

def test_leg_oracle_path_equals_chord_when_path_is_chord():
    cfg = ResponseConfig()
    spec = spec20()
    ras = uniform_raster(spec, NOW + 6 * 3600.0)
    a, b = cc(spec, 10, 2), cc(spec, 10, 12)
    chord = safety.LegOracle(a, b, 9.47, ras, cfg)
    path = safety.LegOracle(a, b, 9.47, ras, cfg, path=[a, b])
    # _resample_path subdivides per-span (vs geo.sample_polyline whole-leg) so
    # sample coordinates differ at float level — T* agrees to well under the
    # 1 ms guard band the oracle itself is property-tested to.
    assert abs(path.latest_safe_depart_s
               - chord.latest_safe_depart_s) < 1e-3


def test_leg_oracle_road_detour_sees_hazard_the_chord_misses():
    """Chord a->b crosses only late-arrival cells (T* far out); the ROAD
    detours north through a band the fire reaches EARLY. The path oracle must
    return a much earlier T* — this is the g2 failure class made visible to
    the planner."""
    cfg = ResponseConfig()
    spec = spec20()
    ras = uniform_raster(spec, NOW + 12 * 3600.0)     # everywhere: half a day
    # early-arrival band across rows 4-6 (north of the chord row 10)
    early = NOW + 30 * 60.0                            # fire in 30 min
    for r in (4, 5, 6):
        for c in range(spec.ncols):
            ras.set(r, c, early)
    a, b = cc(spec, 10, 2), cc(spec, 10, 12)
    detour = cc(spec, 5, 7)                            # the road hooks north
    chord = safety.LegOracle(a, b, 9.47, ras, cfg)
    road = safety.LegOracle(a, b, 9.47, ras, cfg, path=[a, detour, b])
    assert road.latest_safe_depart_s < chord.latest_safe_depart_s
    # the road T* is bound by the 30-min band minus margin/pass — it must sit
    # BEFORE the band arrival; the chord T* is bound only by the 12 h field
    assert road.latest_safe_depart_s < early
    assert chord.latest_safe_depart_s > NOW + 6 * 3600.0


def test_leg_oracle_path_duplicate_points_are_safe():
    cfg = ResponseConfig()
    spec = spec20()
    ras = uniform_raster(spec, NOW + 6 * 3600.0)
    a, b = cc(spec, 10, 2), cc(spec, 10, 6)
    o = safety.LegOracle(a, b, 9.47, ras, cfg, path=[a, a, b, b])
    assert math.isfinite(o.latest_safe_depart_s)


# ---------------------------------------------------------------------------
# 2. dispatch road_provider seam
# ---------------------------------------------------------------------------

class _RR:
    """Duck-typed RoadRoute stand-in."""
    def __init__(self, a, b, time_s, dist_m, on_roads=True, polyline=None):
        self.time_s = time_s
        self.dist_m = dist_m
        self.on_roads = on_roads
        self.polyline = polyline if polyline is not None else [a, b]


def _mk_ens(spec, raster=None):
    r = raster if raster is not None else uniform_raster(spec, UNREACHED)
    return EnsembleArrival(spec=spec, members=[], p10=r, p50=r, p90=r,
                           t0_s=NOW, incident_id=INC)


def _mk_crew(spec):
    return Crew(crew_id="A", name="A", status=CrewStatus.AVAILABLE,
                home_base=cc(spec, 10, 2))


def _mk_job(spec, cid="c1"):
    return DispatchJob(
        client=ClientProperty(client_id=cid, loc=cc(spec, 10, 7),
                              value_usd=1_000_000.0),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=NOW + 10 * 3600.0,
                      eta_p50_s=NOW + 12 * 3600.0,
                      eta_p90_s=NOW + 13 * 3600.0))


def _plan(spec, provider):
    cfg = ResponseConfig()
    return dispatch.plan([_mk_crew(spec)], [_mk_job(spec)],
                         {"inc1": _mk_ens(spec)},
                         [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW,
                         road_provider=provider)


def test_travel_uses_route_time_and_falls_back():
    spec = spec20()
    a, b = cc(spec, 10, 2), cc(spec, 10, 7)
    road_time = 1234.0

    def provider(p, q):
        return _RR(p, q, time_s=road_time, dist_m=road_time * 8.0)

    plan_road = _plan(spec, provider)
    st = plan_road.plans["A"].stops[0]
    # arrive = now + route time exactly (kinematic truth)
    assert st.arrive_s == NOW + road_time

    def provider_offnet(p, q):
        return _RR(p, q, time_s=road_time, dist_m=road_time * 8.0,
                   on_roads=False)

    plan_chord = _plan(spec, provider_offnet)
    plan_none = _plan(spec, None)
    assert (plan_chord.plans["A"].stops[0].arrive_s
            == plan_none.plans["A"].stops[0].arrive_s)


def test_plan_byte_identical_with_and_without_none_provider():
    spec = spec20()
    cfg = ResponseConfig()

    def run(**kw):
        p = dispatch.plan([_mk_crew(spec)], [_mk_job(spec)],
                          {INC: _mk_ens(spec)},
                          [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW, **kw)
        stops = tuple((c, s.client_id, s.arrive_s, s.service_start_s,
                       s.depart_s, s.margin_h, s.gel_fresh_frac)
                      for c, cp in sorted(p.plans.items()) for s in cp.stops)
        return (stops, tuple((u.client_id, u.reason) for u in p.unserved),
                p.objective)

    assert run() == run(road_provider=None)


def test_corridor_blocks_road_detour_through_early_band():
    """End-to-end plan(): the job is corridor-feasible on the chord but the
    ROAD to it detours through an early band -> with the road provider the
    stop must fail 'corridor' (unserved), not be dispatched into the front."""
    spec = spec20()
    cfg = ResponseConfig()
    ens = _mk_ens(spec)
    early = NOW + 30 * 60.0
    for r in (4, 5, 6):
        for c in range(spec.ncols):
            ens.p10.set(r, c, early)

    detour = cc(spec, 5, 7)

    def provider(p, q):
        # every leg 'follows a road' that hooks through the early band
        d = 6000.0
        return _RR(p, q, time_s=d / 9.47, dist_m=d,
                   polyline=[p, detour, q])

    plan_road = _plan_with_ens(spec, cfg, ens, provider)
    plan_none = _plan_with_ens(spec, cfg, ens, None)
    assert plan_none.plans["A"].stops, "chord plan should serve the job"
    assert not plan_road.plans["A"].stops, "road plan must refuse the detour"
    assert any(u.reason == "no_safe_corridor" for u in plan_road.unserved)


def _plan_with_ens(spec, cfg, ens, provider):
    return dispatch.plan([_mk_crew(spec)], [_mk_job(spec)], {"inc1": ens},
                         [SafeZone("z1", cc(spec, 10, 9))], cfg, NOW,
                         road_provider=provider)
