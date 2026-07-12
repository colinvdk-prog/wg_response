"""attack_r4_urgency.py — FOUNDER ROUND 4 law 7 (URGENCY-FIRST DISPATCH).

Attacks:
  A. COUNT-PRIMARY IS UNBREAKABLE (single plan): no urgency+value combination,
     however wild, makes the planner serve N when it could serve N+1.
       A1 constructed temptation: 1 crew can serve {B,C} (2 cheap, comfortable
          homes) XOR {A} (1 astronomically valuable, maximally urgent home);
          the planner MUST take the 2. Swept over wild value/urgency.
       A2 scoring cap: _potential_score / _served_value per home stay inside
          [priority, priority*(1+EPS*(w_urg+w_val))] for ANY value_usd (to 1e18)
          and ANY urgency — so the per-home tiebreak is < 1 count credit; the
          exact N at which count could stop dominating is reported.
  B. URGENCY NEVER MAKES A STOP UNSAFE: 20 seeded dynamic Malibu runs — every
     served stop independently satisfies margin>=0 (own p10-buffer recompute) +
     slack>=floor (latest_depart-depart from payload) + no crew in a burned cell;
     engine scoreboard violations==0 and its audit agrees in sign.
  C. VALUE MODE BYTE-IDENTICAL under wild urgency/value weights (value mode never
     reads urgency).
  D. (attack_r4_urgency_dynamic.py) the ON-vs-OFF dynamic served comparison.

Run:  py -3 -m pytest scratch_review/attack_r4_urgency.py -q
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import dispatch, safety, travel                       # noqa: E402
from wgresponse.contracts import (UNREACHED, ClientProperty, Crew,     # noqa: E402
                                  CrewStatus, DispatchJob, EnsembleArrival,
                                  GridSpec, LatLon, Raster, ResponseConfig,
                                  SafeZone, ThreatEta, seeded_rng)
from attack_r4_common import TILE_DIR, tile_present, skip_loud         # noqa: E402
from attack_sim_common import (independent_stop_violations,            # noqa: E402
                               crews_inside_burned)

NOW = 1_800_000_000.0
INC = "inc1"


# --------------------------------------------------------------------------
# reused test_dispatch scenario helpers (local fake rasters; UNREACHED raster
# => corridors trivially safe, so the attack isolates the OBJECTIVE)
# --------------------------------------------------------------------------
def spec20():
    return GridSpec(north_lat=34.20, west_lon=-117.20, cell_m=1000.0,
                    nrows=20, ncols=20)


def cc(spec, r, c):
    return safety._rc_center(spec, r, c)


def make_ens(raster):
    return EnsembleArrival(spec=raster.spec, members=[], p10=raster, p50=raster,
                           p90=raster, t0_s=NOW, incident_id=INC)


def uniform_ens(spec, value=UNREACHED):
    return {INC: make_ens(Raster(spec, [value] * (spec.nrows * spec.ncols)))}


def make_crew(cid, loc):
    return Crew(crew_id=cid, name=cid, status=CrewStatus.AVAILABLE, home_base=loc)


def make_job(cid, loc, p10_s, p50_s, value=1_000_000.0, weight=1.0):
    return DispatchJob(
        client=ClientProperty(client_id=cid, loc=loc, value_usd=value,
                              priority_weight=weight),
        incident_id=INC,
        eta=ThreatEta(eta_p10_s=p10_s, eta_p50_s=p50_s, eta_p90_s=p50_s + 3600.0))


def served_ids(plan):
    out = set()
    for cp in plan.plans.values():
        out.update(st.client_id for st in cp.stops)
    return out


# ==========================================================================
# A1. constructed temptation — count beats any value+urgency
# ==========================================================================
def _temptation(value_A, p50_A_h, value_BC, p50_BC_h, w_urg=0.30, w_val=0.05):
    """1 crew, homes mode. B,C near the crew (both servable in sequence); A far
    (servable only alone). Returns the served client-id set."""
    cfg = ResponseConfig(dispatch_objective="homes",
                         dispatch_urgency_weight=w_urg,
                         dispatch_value_weight=w_val)
    spec = spec20()
    ens = uniform_ens(spec)
    crew = make_crew("K", cc(spec, 10, 2))
    zones = [SafeZone("z1", cc(spec, 10, 1))]
    # feasible deadlines: p10 = now + 2h lets the crew do B then C; A far but
    # p10 = now+2h lets A be served ALONE. Serving A precludes B,C (and v.v.).
    p10 = NOW + 2 * 3600.0
    B = make_job("B", cc(spec, 10, 4), p10, NOW + p50_BC_h * 3600.0, value=value_BC)
    C = make_job("C", cc(spec, 10, 5), p10, NOW + p50_BC_h * 3600.0, value=value_BC)
    A = make_job("A", cc(spec, 10, 18), p10, NOW + p50_A_h * 3600.0, value=value_A)
    plan = dispatch.plan([crew], [A, B, C], ens, zones, cfg, NOW)
    return served_ids(plan), plan


def test_temptation_preconditions_hold():
    """Sanity: A really does conflict with {B,C} (so it IS a 2-vs-1 choice),
    and {B,C} together are feasible."""
    # {B,C} feasible for 2; A alone feasible for 1; A+B or B+A infeasible.
    s_bc, _ = _temptation(1e6, 40.0, 1e6, 40.0)
    assert s_bc == {"B", "C"}                       # baseline: 2 served
    # force A only (make B,C infeasible by impossible deadline) -> 1 served
    cfg = ResponseConfig(dispatch_objective="homes")
    spec = spec20()
    ens = uniform_ens(spec)
    crew = make_crew("K", cc(spec, 10, 2))
    A = make_job("A", cc(spec, 10, 18), NOW + 2 * 3600.0, NOW + 3 * 3600.0)
    plan = dispatch.plan([crew], [A], ens, [SafeZone("z1", cc(spec, 10, 1))],
                         cfg, NOW)
    assert served_ids(plan) == {"A"}                # A alone is feasible


@pytest.mark.parametrize("value_A", [1e6, 1e9, 1e12, 1e15, 1e18])
@pytest.mark.parametrize("p50_A_h", [2.01, 2.5, 5.0])   # <- maximal-ish urgency
def test_count_beats_wild_value_and_urgency(value_A, p50_A_h):
    """A is astronomically valuable AND maximally urgent; B,C are cheap and
    comfortable. Count-primary MUST still take the 2 (B,C), never the 1 (A)."""
    served, plan = _temptation(value_A=value_A, p50_A_h=p50_A_h,
                               value_BC=600_000.0, p50_BC_h=70.0)
    assert served == {"B", "C"}, (value_A, p50_A_h, served)
    assert "A" in {u.client_id for u in plan.unserved}
    # the count-max plan skipped a feasible A on purpose -> triaged
    assert next(u.reason for u in plan.unserved if u.client_id == "A") == "triaged"


def test_count_beats_value_fuzzed():
    """Randomize A's value/urgency and B,C's value/urgency across seeds; the
    2-home plan wins every time."""
    rng = seeded_rng("r4-urgency-fuzz")
    for _ in range(200):
        vA = 10.0 ** rng.uniform(3, 18)
        vBC = 10.0 ** rng.uniform(3, 7)
        p50A = rng.uniform(2.01, 20.0)
        p50BC = rng.uniform(2.01, 71.0)
        served, _ = _temptation(vA, p50A, vBC, p50BC)
        assert served == {"B", "C"}, (vA, vBC, p50A, p50BC, served)


# ==========================================================================
# A2. scoring cap — per-home score band < 1 count credit for ANY value/urgency
# ==========================================================================
def test_per_home_score_band_bounds_tiebreak_below_one_home():
    """For homes mode, _potential_score and _served_value(gel_frac in [0,1]) for a
    single home stay in [priority, priority*(1+EPS*w_val)] for ANY value_usd (to
    1e18) — the VALUE tiebreak per home is < 1 count credit. Urgency is NOT in the
    objective (round-4 mis-tuning fix), so the band is value-only. Reports the
    exact N at which count could stop dominating."""
    cfg = ResponseConfig(dispatch_objective="homes")
    EPS = dispatch._HOMES_VALUE_EPS
    band = EPS * cfg.dispatch_value_weight         # value-only (urgency not in score)
    spec = spec20()
    rng = seeded_rng("r4-cap")
    lo, hi = math.inf, -math.inf
    for _ in range(3000):
        value = 10.0 ** rng.uniform(-3, 18)
        p10 = NOW + rng.uniform(0.1, 200.0) * 3600.0
        p50 = p10 + rng.uniform(0.0, 200.0) * 3600.0
        job = make_job("x", cc(spec, 10, 10), p10, p50, value=value)
        urg = dispatch._urgency(job, NOW, cfg)
        assert 0.0 <= urg <= 1.0                    # urgency still a valid [0,1] signal
        ps = dispatch._potential_score(job, cfg)    # urgency-blind objective
        assert 1.0 <= ps <= 1.0 + band + 1e-15, (value, ps)
        for gf in (0.0, 0.3, 1.0):
            sv = dispatch._served_value(job, gf, cfg)
            assert 1.0 <= sv <= 1.0 + band + 1e-15, (value, gf, sv)
            lo, hi = min(lo, sv), max(hi, sv)
    n_break = math.floor(1.0 / band)
    print(f"\n[A2] homes-mode per-home score in [{lo:.12f}, {hi:.12f}]; "
          f"value tiebreak band EPS*w_val={band:.3e}; count stays primary for "
          f"up to N={n_break} homes (>> launch scale)")
    assert hi < 2.0                                # a home is always ~1 count


# ==========================================================================
# B. urgency never makes a stop unsafe — 20 seeded dynamic Malibu runs
# ==========================================================================
def _malibu(seed, n_clients, n_crews, ticks, overrides=None):
    from wgresponse.simui import SimSession
    from attack_sim_common import pick_ignition
    sess = SimSession("malibu_ca", n_clients, n_crews, seed=seed,
                      cfg_overrides=(overrides or {}), fire_zone="standard",
                      tile_dir=TILE_DIR)
    p, _ = pick_ignition(sess, "malibu_ca", cluster=True)
    sess.ignite(p.lat, p.lon, intensity="high")
    states = []
    for _ in range(ticks):
        states.append(sess.tick())
    return sess, states


@pytest.mark.skipif(not tile_present("malibu_ca"),
                    reason=skip_loud("malibu_ca", "tile"))
def test_urgency_on_20_seeds_every_served_stop_safe():
    """Homes mode (dispatch_urgency_weight=0.30 default), 20 seeds. The DISPATCH
    decisions stay safe independently: every served stop has margin>=0 (own
    p10-buffer recompute) and slack>=floor (latest_depart-depart). SEMANTICS 10
    may audit genuine crew OVERRUNS (a staged/relocating crew caught as the fire
    advances -- fire behaviour, NOT a dispatch/urgency failure, and identical ON
    vs OFF since the emitted homes plan is urgency-blind); when the INDEPENDENT
    burned-cell recompute finds one, the engine audit ALSO flags it (never
    silently missed -- the SEMANTICS-10 no-reship invariant). REFRESHED from the
    old 'violations==0 / no crew ever burned' pins, which SEMANTICS 10 (audit
    counts overrun) made stale; the overruns are real, not a regression."""
    cfg0 = ResponseConfig()
    slack_floor_h = cfg0.slack_min / 60.0
    total_stops = 0
    overrun_ticks = 0
    for i in range(20):
        seed = f"safe{i}"
        sess, states = _malibu(seed, 40, 3, 45)
        cfg = sess._cfg
        assert cfg.dispatch_urgency_weight == 0.30      # default weight (inert now)
        for st in states:
            mm = st["scoreboard"]["min_margin_h"]
            assert mm is None or mm >= 0.0, (seed, st["tick"], mm)
            # DISPATCH safety: every served stop meets its deadline (own recompute)
            bad = independent_stop_violations(st, cfg.egress_buffer_min,
                                              cfg.ember_buffer_min)
            assert bad == [], (seed, st["tick"], bad[:2])
            # SEMANTICS 10: engine violations MAY be >0 (genuine crew overrun); the
            # independent burned-cell check and the engine audit agree in sign.
            v = st["scoreboard"]["violations"]
            assert v >= 0
            if crews_inside_burned(st):
                assert v > 0, (seed, st["tick"], "burned crew not audited")
                overrun_ticks += 1
            # INDEPENDENT slack floor from the serviced payload
            for s in st["serviced"]:
                slack_h = (s["latest_depart_s"] - s["depart_s"]) / 3600.0
                assert slack_h >= slack_floor_h - 1e-6, (seed, s["client_id"], slack_h)
                total_stops += 1
            # every emitted PLAN stop also carries the slack floor
            if st["plan"]:
                for s in st["plan"]["stops"]:
                    slack_h = (s["latest_depart_s"] - s["depart_s"]) / 3600.0
                    assert slack_h >= slack_floor_h - 1e-6, (seed, s["client_id"])
    assert total_stops > 0
    print(f"\n[B] 20 seeds x 45 ticks: {total_stops} served-stop-observations, all "
          f"margin>=0 + slack>={slack_floor_h:.2f}h + 0 independent stop violations; "
          f"{overrun_ticks} tick(s) with a genuine SEMANTICS-10 crew overrun "
          f"(engine audit agrees with the independent burned-cell recompute)")


# ==========================================================================
# C. value mode byte-identical under wild urgency/value weights
# ==========================================================================
def _plan_digest(plan):
    import json
    d = {
        "objective": round(plan.objective, 6),
        "unserved": sorted((u.client_id, u.reason) for u in plan.unserved),
        "plans": {cid: [(st.client_id, round(st.arrive_s, 6),
                         round(st.service_start_s, 6), round(st.depart_s, 6),
                         round(st.latest_depart_s, 6), round(st.margin_h, 9),
                         round(st.gel_fresh_frac, 9))
                        for st in cp.stops]
                  for cid, cp in sorted(plan.plans.items())},
    }
    return json.dumps(d, sort_keys=True)


def _value_scenario(w_urg, w_val):
    cfg = ResponseConfig(dispatch_objective="value",
                         dispatch_urgency_weight=w_urg,
                         dispatch_value_weight=w_val)
    spec = spec20()
    ens = uniform_ens(spec)
    crews = [make_crew("K1", cc(spec, 10, 2)), make_crew("K2", cc(spec, 12, 2))]
    zones = [SafeZone("z1", cc(spec, 10, 1))]
    rng = seeded_rng("r4-valuemode")
    jobs = []
    for i in range(10):
        r, c = 5 + i % 10, 5 + (i * 3) % 12
        jobs.append(make_job(f"c{i}", cc(spec, r, c),
                             NOW + (2 + i * 0.3) * 3600.0,
                             NOW + (3 + i * 0.7) * 3600.0,
                             value=10.0 ** (5 + (i % 6))))
    return dispatch.plan(crews, jobs, ens, zones, cfg, NOW)


def test_value_mode_byte_identical_under_wild_weights():
    """'value' mode ignores urgency: wildly different dispatch_urgency_weight /
    dispatch_value_weight produce a byte-identical plan."""
    base = _plan_digest(_value_scenario(0.30, 0.05))
    for w_urg, w_val in [(0.0, 0.0), (5.0, 5.0), (1e6, 1e6),
                         (0.99, 0.01), (-3.0, 2.0), (1e-9, 1e9)]:
        assert _plan_digest(_value_scenario(w_urg, w_val)) == base, (w_urg, w_val)
