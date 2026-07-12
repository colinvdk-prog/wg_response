"""Tests for wgresponse.safety — deadlines, gel windows, corridor and egress checks."""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from wgresponse import safety, travel
from wgresponse.contracts import (
    UNREACHED,
    ClientProperty,
    CorridorVerdict,
    DispatchJob,
    GridSpec,
    LatLon,
    Raster,
    ResponseConfig,
    SafeZone,
    ThreatEta,
    WeatherForecast,
    WeatherSample,
    seeded_rng,
)

NOW = 1_800_000_000.0


# ---------------------------------------------------------------------------
# helpers (local fakes per cluster instructions; geo.py may not exist yet)
# ---------------------------------------------------------------------------

def grid(north=34.20, west=-117.20, cell_m=1000.0, n=20):
    return GridSpec(north_lat=north, west_lon=west, cell_m=cell_m, nrows=n, ncols=n)


def uniform_raster(spec, value):
    return Raster(spec, [value] * (spec.nrows * spec.ncols))


def cell_center(spec, r, c):
    return safety._rc_center(spec, r, c)


def make_job(eta_p10, eta_p50, client_id="c1"):
    return DispatchJob(
        client=ClientProperty(client_id=client_id, loc=LatLon(34.1, -117.1)),
        incident_id="inc1",
        eta=ThreatEta(eta_p10_s=eta_p10, eta_p50_s=eta_p50, eta_p90_s=eta_p50 + 3600.0),
    )


def wx_const(temp_c, rh_pct, wind_mps):
    return WeatherForecast(
        loc=LatLon(34.1, -117.1),
        samples=[WeatherSample(t_s=NOW, wind_speed_mps=wind_mps, wind_gust_mps=wind_mps + 2.0,
                               wind_dir_deg=0.0, rh_pct=rh_pct, temp_c=temp_c)],
    )


# ---------------------------------------------------------------------------
# latest departure / latest service start
# ---------------------------------------------------------------------------

def test_latest_departure_arithmetic():
    cfg = ResponseConfig()  # egress 30 min, ember 0
    eta = NOW + 6 * 3600.0
    assert safety.latest_departure_s(eta, cfg) == pytest.approx(eta - 1800.0)
    cfg2 = ResponseConfig(ember_buffer_min=15.0)
    assert safety.latest_departure_s(eta, cfg2) == pytest.approx(eta - 2700.0)


def test_latest_service_start_subtracts_deploy():
    cfg = ResponseConfig()
    eta = NOW + 6 * 3600.0
    assert safety.latest_service_start_s(eta, cfg) == pytest.approx(
        safety.latest_departure_s(eta, cfg) - cfg.deploy_min * 60.0)


def test_latest_departure_unreached_is_unreached():
    cfg = ResponseConfig()
    assert safety.latest_departure_s(UNREACHED, cfg) == UNREACHED
    assert safety.latest_service_start_s(UNREACHED, cfg) == UNREACHED


# ---------------------------------------------------------------------------
# derive_job_windows + gel env model
# ---------------------------------------------------------------------------

def test_derive_job_windows_defaults():
    cfg = ResponseConfig()
    job = make_job(NOW + 10 * 3600.0, NOW + 12 * 3600.0)
    safety.derive_job_windows(job, cfg)
    assert job.latest_depart_s == pytest.approx(NOW + 10 * 3600.0 - 1800.0)
    assert job.latest_service_start_s == pytest.approx(job.latest_depart_s - 1800.0)
    assert job.earliest_useful_start_s == pytest.approx(NOW + 12 * 3600.0 - cfg.gel_window_h * 3600.0)


def test_derive_job_windows_unreached_p50():
    cfg = ResponseConfig()
    job = make_job(UNREACHED, UNREACHED)
    safety.derive_job_windows(job, cfg)
    assert job.latest_depart_s == UNREACHED
    assert job.earliest_useful_start_s == UNREACHED


def test_env_model_reference_conditions_when_no_forecast():
    cfg = ResponseConfig(gel_env_model_enabled=True)
    job = make_job(NOW + 10 * 3600.0, NOW + 12 * 3600.0)
    safety.derive_job_windows(job, cfg)
    # reference-condition coating life is exactly 6.0 h
    assert job.earliest_useful_start_s == pytest.approx(NOW + 12 * 3600.0 - 6.0 * 3600.0)


def test_env_model_shrinks_window_on_hot_windy_forecast():
    p50 = NOW + 12 * 3600.0
    base = safety.derive_job_windows(make_job(NOW + 10 * 3600.0, p50), ResponseConfig())
    cfg_env = ResponseConfig(gel_env_model_enabled=True)
    ref = safety.derive_job_windows(make_job(NOW + 10 * 3600.0, p50), cfg_env)
    hot = safety.derive_job_windows(make_job(NOW + 10 * 3600.0, p50), cfg_env,
                                    wx=wx_const(temp_c=35.0, rh_pct=30.0, wind_mps=5.0))
    # env model shrinks vs the 72 h placeholder; hot/windy shrinks further
    assert ref.earliest_useful_start_s > base.earliest_useful_start_s
    assert hot.earliest_useful_start_s > ref.earliest_useful_start_s
    # hot/windy life: 6*exp(-0.8)*(0.6^0.3)/1.25 h
    life = 6.0 * math.exp(-0.8) * (30.0 / 50.0) ** 0.3 / 1.25
    assert hot.earliest_useful_start_s == pytest.approx(p50 - life * 3600.0, rel=1e-9)


def test_gel_life_floor_and_monotonicity():
    assert safety.gel_life_h(45.0, 10.0, 20.0) == 1.0        # floored
    assert safety.gel_life_h(25.0, 50.0, 0.0) == pytest.approx(6.0)
    assert safety.gel_life_h(35.0, 50.0, 0.0) < safety.gel_life_h(25.0, 50.0, 0.0)
    assert safety.gel_life_h(25.0, 50.0, 10.0) < safety.gel_life_h(25.0, 50.0, 0.0)
    assert safety.gel_life_h(25.0, 20.0, 0.0) < safety.gel_life_h(25.0, 80.0, 0.0)


def test_gel_life_matches_review_fire_weather_numbers():
    """Council-1 dispatch F5's quantified fire-weather coating lives
    (docs/review/dispatch_alerts_review.md): 33 C/12 %RH/8 m/s -> ~1.47 h and
    38 C/8 %RH/15 m/s -> the 1.0 h floor — the '1.0-1.5 h under real fire
    weather vs the 6.0 h reference' regime that ticket C5 threads into
    dispatch.plan via forecasts."""
    assert safety.gel_life_h(25.0, 50.0, 0.0) == pytest.approx(6.0)
    hot = safety.gel_life_h(33.0, 12.0, 8.0)
    assert hot == pytest.approx(1.47, abs=0.01)
    assert 1.0 <= hot <= 1.5
    assert safety.gel_life_h(38.0, 8.0, 15.0) == 1.0


def test_derive_windows_reads_weather_at_service_time_not_forecast_start():
    """Sprint 2 C5 companion: derive_job_windows evaluates wx at the
    as-late-as-safe latest_service_start_s, NOT at the forecast's first
    sample. Cool at t0, hot/windy by the service hour -> the HOT coating life
    governs the window (a t0 lookup would return the benign 6.0 h life)."""
    cfg = ResponseConfig(gel_env_model_enabled=True)
    p10, p50 = NOW + 9 * 3600.0, NOW + 12 * 3600.0
    samples = []
    for h in range(13):
        hot = h >= 6           # heat arrives NOW+6h; service time is NOW+8h
        samples.append(WeatherSample(
            t_s=NOW + h * 3600.0,
            wind_speed_mps=8.0 if hot else 0.0,
            wind_gust_mps=10.0 if hot else 2.0,
            wind_dir_deg=0.0,
            rh_pct=12.0 if hot else 50.0,
            temp_c=33.0 if hot else 25.0))
    wx = WeatherForecast(loc=LatLon(34.1, -117.1), samples=samples)
    job = safety.derive_job_windows(make_job(p10, p50), cfg, wx=wx)
    # t_ref = latest_service_start = p10 - (egress 30 + deploy 30) min
    assert job.latest_service_start_s == pytest.approx(p10 - 3600.0)
    hot_life = safety.gel_life_h(33.0, 12.0, 8.0)
    assert job.earliest_useful_start_s == pytest.approx(
        p50 - hot_life * 3600.0, rel=1e-9)
    # a forecast-start lookup would have produced the 6.0 h reference window
    assert job.earliest_useful_start_s > p50 - 6.0 * 3600.0 + 1.0


# ---------------------------------------------------------------------------
# corridor_safe
# ---------------------------------------------------------------------------

def test_corridor_safe_on_unreached_raster():
    cfg = ResponseConfig()
    spec = grid()
    ras = uniform_raster(spec, UNREACHED)
    a, b = cell_center(spec, 10, 2), cell_center(spec, 10, 8)
    v = safety.corridor_safe(a, b, NOW, 10.0, ras, cfg)
    assert v.safe
    assert v.reason == ""
    assert v.worst_margin_h == UNREACHED


def test_corridor_blocked_by_early_arrival():
    cfg = ResponseConfig()
    spec = grid()
    ras = uniform_raster(spec, NOW + 600.0)   # fire (eroded) everywhere in 10 min
    a, b = cell_center(spec, 10, 2), cell_center(spec, 10, 8)
    v = safety.corridor_safe(a, b, NOW, 10.0, ras, cfg)
    assert not v.safe
    assert "corridor unsafe" in v.reason
    # worst margin is min(arrival - pass) and must be under the 60-min margin
    assert v.worst_margin_h < cfg.route_margin_min / 60.0


def test_corridor_margin_arithmetic():
    cfg = ResponseConfig()
    spec = grid()
    a, b = cell_center(spec, 10, 2), cell_center(spec, 10, 5)
    speed = 10.0
    dist = safety._haversine_m(a, b)
    arrival = NOW + dist / speed + cfg.route_margin_min * 60.0 + 3600.0
    ras = uniform_raster(spec, arrival)
    v = safety.corridor_safe(a, b, NOW, speed, ras, cfg)
    assert v.safe
    # worst margin (arrival - pass_time) occurs at the last sample: margin + 1 h
    assert v.worst_margin_h == pytest.approx(cfg.route_margin_min / 60.0 + 1.0, rel=1e-3)


def test_corridor_unsafe_when_margin_short():
    cfg = ResponseConfig()
    spec = grid()
    a, b = cell_center(spec, 10, 2), cell_center(spec, 10, 5)
    speed = 10.0
    dist = safety._haversine_m(a, b)
    # arrival only 30 min after the last pass: under the 60-min route margin
    ras = uniform_raster(spec, NOW + dist / speed + 1800.0)
    v = safety.corridor_safe(a, b, NOW, speed, ras, cfg)
    assert not v.safe


def test_corridor_outside_raster_is_safe():
    cfg = ResponseConfig()
    spec = grid(north=40.0, west=-100.0)      # far away from the leg
    ras = uniform_raster(spec, NOW)           # would be lethal if sampled
    a, b = LatLon(34.10, -117.10), LatLon(34.10, -117.05)
    v = safety.corridor_safe(a, b, NOW, 10.0, ras, cfg)
    assert v.safe
    assert v.worst_margin_h == UNREACHED


def test_corridor_none_raster_is_safe():
    cfg = ResponseConfig()
    v = safety.corridor_safe(LatLon(34.1, -117.1), LatLon(34.1, -117.0), NOW, 10.0, None, cfg)
    assert v.safe


def test_corridor_bad_speed_raises():
    cfg = ResponseConfig()
    spec = grid()
    with pytest.raises(ValueError):
        safety.corridor_safe(cell_center(spec, 1, 1), cell_center(spec, 1, 2),
                             NOW, 0.0, uniform_raster(spec, UNREACHED), cfg)


# ---------------------------------------------------------------------------
# egress_ok / egress_choice
# ---------------------------------------------------------------------------

def _split_raster(spec, early_col, early_t):
    """Columns >= early_col get arrival early_t; the rest UNREACHED."""
    data = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            data.append(early_t if c >= early_col else UNREACHED)
    return Raster(spec, data)


def test_egress_uses_nearest_safe_zone():
    cfg = ResponseConfig()
    spec = grid()
    ras = uniform_raster(spec, UNREACHED)
    stop = cell_center(spec, 10, 5)
    near = SafeZone("z_near", cell_center(spec, 10, 7))
    far = SafeZone("z_far", cell_center(spec, 10, 15))
    verdict, zone = safety.egress_choice(stop, NOW, [far, near], ras, cfg)
    assert verdict.safe
    assert zone == "z_near"
    assert safety.egress_ok(stop, NOW, [far, near], ras, cfg).safe


def test_egress_falls_back_to_farther_safe_zone():
    cfg = ResponseConfig()
    spec = grid()
    ras = _split_raster(spec, early_col=12, early_t=NOW + 60.0)
    stop = cell_center(spec, 10, 10)                              # in the safe west half
    near_blocked = SafeZone("z_east", cell_center(spec, 10, 13))  # 3 cells away, in fire path
    far_safe = SafeZone("z_west", cell_center(spec, 10, 1))       # 9 cells away, safe
    verdict, zone = safety.egress_choice(stop, NOW, [near_blocked, far_safe], ras, cfg)
    assert verdict.safe
    assert zone == "z_west"


def test_egress_no_zones_fails_closed():
    cfg = ResponseConfig()
    spec = grid()
    v = safety.egress_ok(cell_center(spec, 10, 5), NOW, [], uniform_raster(spec, UNREACHED), cfg)
    assert not v.safe
    assert "no_safe_egress" in v.reason


def test_egress_all_zones_blocked():
    cfg = ResponseConfig()
    spec = grid()
    ras = uniform_raster(spec, NOW + 60.0)    # everything burns in 1 min
    stop = cell_center(spec, 10, 5)
    zones = [SafeZone("z1", cell_center(spec, 10, 8)), SafeZone("z2", cell_center(spec, 2, 2))]
    v = safety.egress_ok(stop, NOW, zones, ras, cfg)
    assert not v.safe
    assert "no_safe_egress" in v.reason


# ---------------------------------------------------------------------------
# erosion fallback sanity (used by dispatch when geo.py is absent)
# ---------------------------------------------------------------------------

def test_erode_min_local_pulls_earliest_neighbor():
    spec = grid(n=5)
    ras = uniform_raster(spec, 1000.0)
    ras.set(2, 2, 100.0)
    out = safety._erode_min_local(ras, 1000.0)   # 1-cell square window
    assert out.get(2, 2) == 100.0
    assert out.get(1, 2) == 100.0 and out.get(3, 3) == 100.0
    assert out.get(0, 0) == 1000.0               # outside the window
    # original untouched
    assert ras.get(1, 2) == 1000.0


def test_erode_min_zero_radius_is_identity_copy():
    spec = grid(n=4)
    ras = uniform_raster(spec, 5.0)
    ras.set(1, 1, 2.0)
    out = safety._erode_min_local(ras, 0.0)
    assert out.data == ras.data
    assert out.data is not ras.data


# ---------------------------------------------------------------------------
# Sprint 2 B1 — LegOracle closed-form corridor precompute (Council-1 perf D1,
# docs/review/perf_review.md sections 3-4: corridor geometry recomputation was
# 94% of dispatch.plan; the verdict is monotone in depart_s, so each leg
# reduces to T* = min_i(arrival_i - margin - d_i/speed)).
# ---------------------------------------------------------------------------

def _corridor_safe_sprint1_reference(a, b, depart_s, speed_mps, eroded_p10, cfg):
    """VERBATIM port of the Sprint-1 corridor_safe loop — the old-vs-new
    byte-identity reference demanded by ticket B1. Do NOT modernize: the
    property tests below assert the LegOracle-backed corridor_safe returns
    exactly what this loop returns (same geometry helpers, same arithmetic)."""
    if speed_mps <= 0.0:
        raise ValueError("speed_mps must be > 0")
    if eroded_p10 is None:
        return CorridorVerdict(True, "", UNREACHED)
    pts = safety._sample_polyline(a, b, cfg.corridor_sample_m)
    margin_s = cfg.route_margin_min * 60.0
    worst_h = UNREACHED
    safe = True
    reason = ""
    cum_m = 0.0
    prev = None
    for i, p in enumerate(pts):
        if prev is not None:
            cum_m += safety._haversine_m(prev, p)
        prev = p
        v = safety._raster_value_at(eroded_p10, p)
        if v is None or math.isinf(v):     # outside raster or UNREACHED: safe
            continue
        t_pass = depart_s + cum_m / speed_mps
        m_h = (v - t_pass) / 3600.0
        if m_h < worst_h:
            worst_h = m_h
        if safe and (v - t_pass) < margin_s:
            safe = False
            reason = (f"corridor unsafe at sample {i + 1}/{len(pts)} "
                      f"({p.lat:.5f},{p.lon:.5f}): eroded p10 arrival {v:.0f}s < "
                      f"pass {t_pass:.0f}s + margin {margin_s:.0f}s")
    return CorridorVerdict(safe, reason, worst_h)


_B1_T0 = 1_780_000_000.0


def _b1_case(k, tag="b1-corridor-prop"):
    """Seeded random (leg, raster, departure, speed) case: random grid placement
    and cell size, mixed UNREACHED/finite arrivals (some already passed), leg
    endpoints that may fall outside the raster, random departure and speed."""
    rng = seeded_rng(tag, k)
    spec = GridSpec(north_lat=34.0 + rng.random() * 6.0,
                    west_lon=-122.0 + rng.random() * 5.0,
                    cell_m=rng.uniform(150.0, 1200.0),
                    nrows=rng.randrange(8, 28), ncols=rng.randrange(8, 28))
    p_inf = rng.uniform(0.1, 0.7)
    data = [UNREACHED if rng.random() < p_inf
            else _B1_T0 + rng.uniform(-2.0, 30.0) * 3600.0
            for _ in range(spec.nrows * spec.ncols)]
    ras = Raster(spec, data)

    def pt():
        rr = rng.uniform(-4.0, spec.nrows + 3.0)     # sometimes outside the grid
        cc = rng.uniform(-4.0, spec.ncols + 3.0)
        return safety._rc_center(spec, rr, cc)

    a, b = pt(), pt()
    depart_s = _B1_T0 + rng.uniform(0.0, 12.0) * 3600.0
    speed = rng.uniform(3.0, 25.0)
    return a, b, depart_s, speed, ras, ResponseConfig()


def test_b1_corridor_byte_identical_on_500_seeded_legs():
    """Ticket B1 hard gate: property-test old-vs-new on 500 seeded random
    legs/rasters/departure times — exact equality on `safe`, worst_margin_h
    within 1e-9 (bitwise in practice: LegOracle.verdict replays the identical
    float operations), and identical reason strings on the first failing
    sample. Applies to both the public corridor_safe seam and a reused
    LegOracle."""
    n_safe = n_unsafe = n_unbounded = 0
    for k in range(500):
        a, b, depart_s, speed, ras, cfg = _b1_case(k)
        ref = _corridor_safe_sprint1_reference(a, b, depart_s, speed, ras, cfg)
        new = safety.corridor_safe(a, b, depart_s, speed, ras, cfg)
        assert new.safe == ref.safe
        assert new.reason == ref.reason
        assert new.worst_margin_h == pytest.approx(ref.worst_margin_h, abs=1e-9)

        oracle = safety.LegOracle(a, b, speed, ras, cfg)
        v = oracle.verdict(depart_s)
        assert v.safe == ref.safe
        assert v.reason == ref.reason
        assert v.worst_margin_h == pytest.approx(ref.worst_margin_h, abs=1e-9)
        if ref.safe:
            n_safe += 1
        else:
            n_unsafe += 1
        if oracle.latest_safe_depart_s == UNREACHED:
            n_unbounded += 1
    # the seeded corpus must genuinely exercise all three regimes
    assert n_safe >= 100 and n_unsafe >= 100 and n_unbounded >= 20


def test_b1_tstar_threshold_matches_loop_verdict_on_500_legs():
    """T* semantics (contracts spread seam note / ticket B1): the leg is safe
    iff depart_s <= T*, where UNREACHED samples impose no bound. The closed
    form agrees with the sampled loop everywhere except within float-rounding
    of the threshold itself (~1e-7 s at unix scale) — asserted with a 1 ms
    guard band, plus explicit probes 1 s either side of T*."""
    checked = probed = 0
    for k in range(500):
        a, b, depart_s, speed, ras, cfg = _b1_case(k)
        oracle = safety.LegOracle(a, b, speed, ras, cfg)
        tstar = oracle.latest_safe_depart_s
        ref = _corridor_safe_sprint1_reference(a, b, depart_s, speed, ras, cfg)
        if tstar == UNREACHED:
            assert ref.safe            # nothing constrains the leg
            continue
        if abs(depart_s - tstar) > 1e-3:
            assert (depart_s <= tstar) == ref.safe
            checked += 1
        if probed < 60:                # keep the geometry re-runs bounded
            assert _corridor_safe_sprint1_reference(
                a, b, tstar - 1.0, speed, ras, cfg).safe
            assert not _corridor_safe_sprint1_reference(
                a, b, tstar + 1.0, speed, ras, cfg).safe
            probed += 1
    assert checked >= 300 and probed == 60


def test_leg_oracle_none_raster_and_bad_speed():
    cfg = ResponseConfig()
    o = safety.LegOracle(LatLon(34.1, -117.1), LatLon(34.1, -117.0), 10.0, None, cfg)
    assert o.latest_safe_depart_s == UNREACHED
    v = o.verdict(NOW)
    assert v.safe and v.reason == "" and v.worst_margin_h == UNREACHED
    with pytest.raises(ValueError):
        safety.LegOracle(LatLon(34.1, -117.1), LatLon(34.1, -117.0), 0.0, None, cfg)


def test_leg_oracle_tstar_closed_form_on_uniform_raster():
    """Uniform finite arrival: the farthest sample binds, so
    T* = arrival - route_margin - leg_time and worst_margin_h(depart) =
    (arrival - leg_time - depart)/3600 — checkable by hand."""
    cfg = ResponseConfig()
    spec = grid()
    a, b = cell_center(spec, 10, 2), cell_center(spec, 10, 8)
    speed = 10.0
    arrival = NOW + 8 * 3600.0
    o = safety.LegOracle(a, b, speed, uniform_raster(spec, arrival), cfg)
    leg_s = safety._haversine_m(a, b) / speed
    assert o.latest_safe_depart_s == pytest.approx(
        arrival - cfg.route_margin_min * 60.0 - leg_s, abs=1e-3)
    v = o.verdict(NOW)
    assert v.safe
    assert v.worst_margin_h == pytest.approx((arrival - leg_s - NOW) / 3600.0, abs=1e-6)


def test_leg_oracle_rebuild_is_deterministic():
    a, b, depart_s, speed, ras, cfg = _b1_case(7)
    o1 = safety.LegOracle(a, b, speed, ras, cfg)
    o2 = safety.LegOracle(a, b, speed, ras, cfg)
    assert o1.latest_safe_depart_s == o2.latest_safe_depart_s
    assert o1.n_pts == o2.n_pts
    assert o1.samples == o2.samples
    v1, v2 = o1.verdict(depart_s), o2.verdict(depart_s)
    assert (v1.safe, v1.reason, v1.worst_margin_h) == (v2.safe, v2.reason, v2.worst_margin_h)


def test_b1_egress_per_zone_tstar_matches_egress_choice():
    """Ticket B1 egress clause: per-(stop, zone) T* precompute picks exactly
    the zone egress_choice picks (nearest-first by travel time, ties by
    zone_id, first safe wins) and agrees on the safe/unsafe verdict —
    egress_choice semantics unchanged."""
    agree = 0
    for k in range(60):
        a, _b, depart_s, _speed, ras, cfg = _b1_case(k, tag="b1-egress-prop")
        rng = seeded_rng("b1-egress-zones", k)
        zones = [SafeZone(f"z{z}", safety._rc_center(
                     ras.spec, rng.uniform(-1.0, ras.spec.nrows),
                     rng.uniform(-1.0, ras.spec.ncols)))
                 for z in range(3)]
        speed = safety._corridor_speed_mps(cfg)
        tstars = {z.zone_id: safety.LegOracle(a, z.loc, speed, ras, cfg).latest_safe_depart_s
                  for z in zones}
        if any(abs(depart_s - t) < 1e-3 for t in tstars.values()):
            continue                        # guard band at a float threshold
        ordered = sorted(zones, key=lambda z: (travel.travel_time_s(a, z.loc, cfg),
                                               z.zone_id))
        expect = next((z.zone_id for z in ordered if depart_s <= tstars[z.zone_id]), "")
        verdict, zid = safety.egress_choice(a, depart_s, zones, ras, cfg)
        assert zid == expect
        assert verdict.safe == (expect != "")
        agree += 1
    assert agree >= 50
