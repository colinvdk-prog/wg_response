"""wgresponse.dispatch — deadline-TOPTW crew dispatch.

Public seam (contracts.MODULE SEAMS):
    plan(crews, jobs, ens, safe_zones, cfg, now_s, incumbent=None) -> DispatchPlan

Algorithm (docs/research/dispatch_optimization.md):
  0. Jobs are collapsed to ONE per client_id before planning (Sprint 2 A7,
     Council-1 dispatch F4): a property is a single gel target no matter how
     many incidents threaten it. The worst-case job survives (earliest
     eta_p10, ties by earliest eta_p50, then incident_id); the union of the
     other incident ids is logged. Corridor checks still run against EVERY
     incident's raster, so safety is unaffected.
  1. safety.derive_job_windows for every job — with the job's OWN incident
     forecast when the caller supplies plan(..., forecasts={incident_id:
     WeatherForecast}) (Sprint 2 C5, Council-1 dispatch F5: windows were
     derived weatherless, so the env gel model always used its 6.0 h
     reference while real fire weather gives 1.0-1.5 h). Jobs already
     infeasible (now > latest_service_start - min travel from any eligible
     crew, or an empty [earliest_useful, latest_service_start] window) are
     dropped as UnservedJob('infeasible_deadline') — except that an EMPTY
     window under cfg.gel_env_model_enabled is reported with the distinct
     structural reason 'gel_window_closed' (the forecast spread p50-p10
     exceeds the coating life minus the egress+deploy buffers, so NO crew
     count or positioning can ever serve the job).
  2. Warm start: repair the incumbent — keep stops still feasible; stops whose
     INCUMBENT service_start_s < now + commitment_horizon_min*60 are FROZEN
     (never moved/dropped unless now-infeasible; safety trumps commitment).
  3. Multi-start construction (Sprint 2 B2, Council-1 perf D2 + dispatch F3):
     three seeds — the repaired incumbent filled by regret-2 (the Sprint-1
     construction), a greedy earliest-deadline-first append seed, and regret-2
     from the frozen-committed skeleton — each polished by local search; the
     best (by objective, warm winning ties) feeds the ILS. Regret-2 insertion
     (best minus second-best insertion delta across DISTINCT crews;
     single-option jobs get infinite regret; ties -> larger priority*value
     score, then earlier deadline, then client id). Every candidate insertion
     re-evaluates the whole route: arrive = prev_depart + travel;
     service_start = max(arrive, earliest_useful_start);
     depart = service_start + deploy_min*60 <=
     latest_depart - slack_min*60 (the SAFETY SEMANTICS 7 planned-slack
     floor: a stop that fits the deadline but carries less than cfg.slack_min
     minutes of slack is INFEASIBLE — absorbs the measured -21..-33%
     mountain-road travel optimism, Council-1 dispatch F2). All timing
     feasibility funnels through the single _depart_fail chokepoint, used by
     construction, local search, ILS shakes and incumbent repair alike.
     Ingress corridor safe against every incident's ERODED P10
     raster, and a safe egress corridor to a SafeZone at that stop's depart —
     egress is re-checked for EVERY stop, and all downstream stops must stay
     feasible after the shift. Corridor/egress feasibility is O(1) per check
     via per-leg closed-form latest-safe-departure oracles (Sprint 2 B1,
     safety.LegOracle): T* = min over rasters and samples of
     (arrival - margin - d/speed), precomputed once per unique leg per plan()
     call — byte-identical semantics to corridor_safe, which stays the
     public verdict authority.
  4. Objective J = sum(priority_weight * value_usd * gel_fresh_frac *
     cfg.gel_effectiveness) - lambda_travel * drive_h - churn penalty.
     lambda_travel = 1% of the mean potential stop score per drive-hour and the
     churn penalty = cfg.churn_penalty_frac * mean potential stop score per
     stop reassigned to a different crew than the incumbent's (x2 when it was
     the incumbent crew's first stop), per spec section 8.2.
  5. Local search (relocate, inter-crew swap, intra-route 2-opt,
     drop-and-refill), then ILS: cfg.ils_iterations shakes (remove a seeded-RNG
     random ~20% of non-frozen stops, reinsert by regret-2), strict-improvement
     acceptance, all under the cfg.solver_budget_s wall clock (time.monotonic —
     a budget, not randomness). The ILS result displaces the warm
     (incumbent-shaped) plan only if it beats it by cfg.adoption_threshold_frac.
     NEVER-WORSE-THAN-GREEDY GUARDRAIL (Sprint 2 B2): the final plan is argmax
     objective over {that result, the raw greedy-EDF seed}, and additionally
     must serve at least the greedy-EDF's collected value — so the emitted
     plan's served value >= the greedy baseline on every instance BY
     CONSTRUCTION (Council-1 measured the budget-starved solver at 9x WORSE
     value than its own greedy fallback at 20 crews / 200 jobs).
     EXACT MICRO-SOLVER (Sprint 2 B2, Council-1 dispatch F3): pools with <= 8
     non-frozen jobs and <= 4 crews — the launch-scale regime, and exactly the
     brute-forceable class where the review demonstrated 2/24 structural
     misses — are solved to the TRUE optimum (per-crew ordered-route DFS
     under the full feasibility chain + subset DP across crews; ties prefer
     more served stops); the ILS is skipped when the exact pass completes.
  6. A final "late-shift" pass delays non-frozen services toward
     eta_p50 - gel_full_strength (never past latest_depart - slack_min*60 or
     any other safety deadline, with ingress/egress corridors re-verified at
     the shifted times) so gel is applied as late as safely possible; every
     stop records margin_h (RAW slack, (latest_depart - depart)/3600 — the
     slack floor guarantees margin_h >= slack_min/60 on every emitted stop)
     and gel_fresh_frac; unserved jobs carry reasons (the UnservedJob.reason
     vocabulary from contracts, extended here with 'infeasible_slack' and
     'gel_window_closed'): infeasible_deadline | infeasible_slack |
     gel_window_closed | no_safe_corridor | no_safe_egress | capacity |
     dropped_by_optimizer.

Determinism: identical inputs (without budget pressure) produce identical
plans — contracts.seeded_rng drives the shakes and every sort key is stable.
"""
from __future__ import annotations

import logging
import math
import time

from wgresponse import safety
from wgresponse.contracts import (
    UNREACHED,
    Crew,
    CrewPlan,
    CrewStatus,
    DispatchJob,
    DispatchPlan,
    PlannedStop,
    ResponseConfig,
    UnservedJob,
    seeded_rng,
)
from wgresponse.travel import travel_time_s

_EPS = 1e-6
_BIG_REGRET = 1e30

# 'homes'-mode (founder TRIAGE law, contracts FOUNDER ROUND 3 #4) scoring: one
# protected home is worth ~1 priority-weighted "count" point; value_usd is only a
# strict lexicographic tiebreak scaled into [0, _HOMES_VALUE_EPS x w_val], so no
# value can ever outweigh a single served home (EPS x w_val x #homes stays << 1 at
# launch scale). The count-first EPS-lexicographic structure is fixed.
#
# URGENCY-FIRST (FOUNDER ROUND 4 law 7) is DELIBERATELY NOT in this objective, and
# does NOT change the emitted plan. The round-4 build put w_urg x urgency into the
# score; under the now-default physics-on fire (fast grass + spotting) that
# urgency term steered the budget-limited ILS to protect FEWER total homes (two
# adversarial sweeps measured a large NET served-count LOSS vs pure count-max, up
# to -113 on one class). Per the round-4 mis-tuning fix (option A / STRICT
# can't-cost-a-home): the count-maximal SET is chosen URGENCY-BLIND -- so the
# emitted homes plan is byte-identical to dispatch_urgency_weight=0 pure count-max
# for EVERY w_urg, and urgency can NEVER cost a protected home (the founder's hard
# requirement; pure count-max maximizes homes protected, the founder's actual
# goal). A within-count-maximal-plan at-risk-first ORDERING utility exists
# (_urgency_reorder) and is count-safe per-plan, but is NOT applied to the emitted
# plan: any emit-order change feeds back through the sim's commitment loop and was
# measured to cost homes dynamically, so it cannot be guaranteed count-safe from
# dispatch alone. dispatch_urgency_weight is therefore inert in the emitted plan
# (like it always was in 'value' mode); a feedback-free at-risk-first opt-in is
# deferred to a future integration point.
_HOMES_VALUE_EPS = 1.0e-3     # tiebreak band width (<< 1 count credit per home)
_HOMES_VALUE_CAP = 1.0e7      # USD normalization for the min(value/CAP, 1) tiebreak

# Statuses that bar a crew from dispatch regardless of its last-known location.
# OFF_DUTY: crew is not working. UNKNOWN: the crew's GPS beacon is stale (or was
# never received) — CrewTracker.stale() sets this — so loc() would return a
# STALE last-known position; routing on it would violate the crew-safety
# corridor math, which assumes the crew's location is current (contracts §3).
_INELIGIBLE_STATUSES = frozenset({CrewStatus.OFF_DUTY, CrewStatus.UNKNOWN})


_LOG = logging.getLogger("wgresponse.dispatch")


def _imp_eps(j: float) -> float:
    """Minimum objective improvement treated as real (absolute + relative)."""
    return 1e-6 + 1e-9 * abs(j)


def _depart_fail(depart_s: float, latest_depart_s: float, cfg: ResponseConfig):
    """THE stop-timing feasibility chokepoint (SAFETY SEMANTICS 7).

    Returns None when depart_s both meets the hard deadline AND carries at
    least cfg.slack_min minutes of slack before latest_depart_s; 'deadline'
    when the hard deadline itself is missed; 'slack' when the stop fits the
    deadline but not the slack floor (Council-1 dispatch F2: the flat travel
    model is -21..-33% optimistic on mountain roads, so deadline-only
    feasibility left stops with as little as 10.2 min of slack).

    Every feasibility path — regret-2 construction, local search, ILS shakes
    and incumbent repair (all via _Ctx.eval_route) plus diagnose() — funnels
    through this check; _late_shift applies the same floor as a cap
    (latest_depart - slack_min*60), so a slack-starved stop can never survive
    into an emitted plan. NOTE: extends the contracts.UnservedJob.reason
    vocabulary with 'infeasible_slack' (comment-level extension; contracts
    itself is unchanged).
    """
    if depart_s > latest_depart_s + _EPS:
        return "deadline"
    if depart_s > latest_depart_s - cfg.slack_min * 60.0 + _EPS:
        return "slack"
    return None


def _job_key(job: DispatchJob):
    return (job.client.client_id, job.incident_id)


def _value_score(job: DispatchJob, cfg: ResponseConfig) -> float:
    """Legacy USD-weighted potential value: priority x value x gel_effectiveness.
    The dollar component both modes share (drives lam_travel/churn/value-tiebreak
    scaling); identical to the historical _potential_score."""
    return job.client.priority_weight * job.client.value_usd * cfg.gel_effectiveness


def _homes_value_frac(job: DispatchJob) -> float:
    """value_usd normalized to [0, 1] for the 'homes'-mode lexicographic value
    tiebreak: min(value_usd / _HOMES_VALUE_CAP, 1)."""
    if _HOMES_VALUE_CAP <= 0.0:
        return 0.0
    v = job.client.value_usd / _HOMES_VALUE_CAP
    return v if v < 1.0 else 1.0


def _urgency(job: DispatchJob, now_s: float, cfg: ResponseConfig) -> float:
    """URGENCY of a job on its HONEST MEDIAN arrival (eta_p50), in [0, 1]: a
    bounded, monotonically DECREASING function of hours-to-p50 -- 1.0 for a fire
    arriving now (or already past the median), decaying linearly to 0.0 at the
    notice horizon cfg.notice_h. Sooner-to-burn => LARGER: the _urgency_reorder
    utility services the sooner-to-burn savable home first WITHIN an already-
    count-maximal plan (FOUNDER ROUND 4 law 7, re-scoped by the round-4 mis-tuning
    fix -- NOT an objective term, and NOT applied to the emitted plan; see the
    module header and _potential_score).

    Keyed off p50 (the median) by design, NOT the compressed p10 the SAFETY gate
    uses: urgency only RANKS which savable home to service first, while the p10
    deadline/corridor/slack gate (SAFETY SEMANTICS 2/3/7) independently decides
    feasibility and is untouched. A non-arriving job (UNREACHED p50) has no
    median threat and no urgency (0.0)."""
    eta50 = job.eta.eta_p50_s
    if not math.isfinite(eta50):
        return 0.0
    horizon = cfg.notice_h * 3600.0
    if horizon <= 0.0:
        return 0.0
    frac = (eta50 - now_s) / horizon
    if frac <= 0.0:
        return 1.0
    if frac >= 1.0:
        return 0.0
    return 1.0 - frac


def _homes_value_tiebreak(job: DispatchJob, cfg: ResponseConfig) -> float:
    """The 'homes'-mode OBJECTIVE tiebreak, in [0, w_val], BEFORE the
    _HOMES_VALUE_EPS band scaling and any freshness weighting:
    w_val x min(value/CAP, 1), w_val = cfg.dispatch_value_weight.

    URGENCY IS DELIBERATELY ABSENT here (the round-4 mis-tuning fix): the count-
    maximal SET is chosen urgency-BLIND, so this objective is value-only --
    exactly the pre-round-4 (dispatch_urgency_weight=0) tiebreak, which the
    count-max SET now always reproduces regardless of w_urg. Urgency instead
    orders servicing WITHIN the count-maximal plan (_urgency_reorder), where it
    can never cost a protected home."""
    return cfg.dispatch_value_weight * _homes_value_frac(job)


def _potential_score(job: DispatchJob, cfg: ResponseConfig) -> float:
    """Max collectible stop score at full gel freshness, in cfg.dispatch_objective
    units. THE single scoring chokepoint — objective (route_contrib), greedy-EDF,
    the exact micro-solver, regret ties, drop-refill ordering, mean_score, the
    guardrail and diagnose all read it, so switching modes here switches the whole
    planner coherently:

    'value' (legacy): priority_weight x value_usd x gel_effectiveness.
    'homes' (founder TRIAGE law): count-first -> priority_weight x (1 + EPS x
        w_val x min(value/CAP,1)). Every protected home is worth ~1 priority-
        weighted point; value is only a tiebreak of at most EPS x w_val << 1, so
        the COUNT of homes protected is the primary maximand and no value can
        outweigh one served home. URGENCY is NOT in the objective (so it can
        never cost a home under the budget-limited solver); the emitted homes plan
        is thus byte-identical for every w_urg (the _urgency_reorder utility is a
        within-fixed-set tiebreak, not applied to the emitted plan). lam_travel /
        churn / the anti-concentration term are derived from the VALUE COMPONENT
        only (see _Ctx), so they too stay strictly below one home.
    """
    if cfg.dispatch_objective == "homes":
        return job.client.priority_weight * (
            1.0 + _HOMES_VALUE_EPS * _homes_value_tiebreak(job, cfg))
    return job.client.priority_weight * job.client.value_usd * cfg.gel_effectiveness


def _served_value(job: DispatchJob, gel_frac: float, cfg: ResponseConfig) -> float:
    """Objective value CONTRIBUTED by actually serving `job` at projected gel
    freshness gel_frac -- the quantity route_contrib / the exact solver / emit
    accumulate:

    'value' (legacy): value_score x gel_frac  (freshness-weighted dollars).
    'homes' (founder TRIAGE law): priority x (1 + EPS x w_val x
        min(value/CAP,1) x gel_frac). The COUNT credit (priority x 1) is FIXED
        per protected home -- it is NOT scaled by freshness, so a served home
        always counts as one home regardless of gel_frac; only the tiny value
        tiebreak carries the freshness (keeping _served_value(gel_frac=1) ==
        _potential_score exactly). (Scaling the count by gel_frac let a crew
        inflate its 'count' by stacking stops to delay departs into fresher gel
        -- which would fight the anti-concentration term; keeping the count fixed
        removes that.) URGENCY is absent (objective-blind); it orders WITHIN the
        count-maximal plan and never changes the served set."""
    if cfg.dispatch_objective == "homes":
        return job.client.priority_weight * (
            1.0 + _HOMES_VALUE_EPS * _homes_value_tiebreak(job, cfg) * gel_frac)
    return _value_score(job, cfg) * gel_frac


def _eff_gel_window_h(job: DispatchJob, cfg: ResponseConfig):
    """(window_h, full_strength_h) actually in force for this job — recovered
    from the derived earliest_useful_start_s so the env-model-shrunk window
    (safety.derive_job_windows) is honored."""
    eta50 = job.eta.eta_p50_s
    if math.isfinite(eta50) and math.isfinite(job.earliest_useful_start_s):
        window_h = max(0.0, (eta50 - job.earliest_useful_start_s) / 3600.0)
    else:
        window_h = cfg.gel_window_h
    return window_h, min(cfg.gel_full_strength_h, window_h)


def _gel_fresh_frac(job: DispatchJob, depart_s: float, cfg: ResponseConfig) -> float:
    """Projected gel effectiveness fraction remaining at (median) fire arrival:
    1.0 when eta_p50 - depart <= gel_full_strength_h, linear decay to 0.0 at
    the effective gel window. 0.0 when the fire never arrives at P50."""
    eta50 = job.eta.eta_p50_s
    if not math.isfinite(eta50):
        return 0.0
    window_h, full_h = _eff_gel_window_h(job, cfg)
    dt_h = (eta50 - depart_s) / 3600.0
    if dt_h <= full_h:
        return 1.0
    if dt_h >= window_h:
        return 0.0
    return (window_h - dt_h) / (window_h - full_h)


class _Stop:
    """Internal timed stop (all absolute times unix seconds)."""
    __slots__ = ("key", "arrive_s", "service_start_s", "depart_s", "margin_h",
                 "gel_frac", "egress_zone_id", "travel_s")

    def __init__(self, key, arrive_s, service_start_s, depart_s, margin_h,
                 gel_frac, egress_zone_id, travel_s):
        self.key = key
        self.arrive_s = arrive_s
        self.service_start_s = service_start_s
        self.depart_s = depart_s
        self.margin_h = margin_h
        self.gel_frac = gel_frac
        self.egress_zone_id = egress_zone_id
        self.travel_s = travel_s


class _State:
    """Assignment state: crew_id -> ordered job-key list + frozen prefix len."""
    __slots__ = ("assign", "frozen")

    def __init__(self, assign, frozen):
        self.assign = assign
        self.frozen = frozen

    def copy(self) -> "_State":
        return _State({c: list(v) for c, v in self.assign.items()}, dict(self.frozen))

    def assigned(self):
        out = set()
        for keys in self.assign.values():
            out.update(keys)
        return out


class _Contrib:
    __slots__ = ("value", "drive_h", "churn_w", "j")

    def __init__(self, value, drive_h, churn_w, j):
        self.value = value
        self.drive_h = drive_h
        self.churn_w = churn_w
        self.j = j


class _Ctx:
    """Per-plan() evaluation context: caches for travel, per-leg corridor
    T* oracles, per-(stop, zone) egress T*, and route timelines. All caches are
    keyed on exact values, so results are deterministic within a run.

    Sprint 2 B1 (Council-1 perf D1): corridor feasibility is precomputed ONCE
    per unique leg per plan() invocation as the closed-form latest safe
    departure T* = min over rasters of safety.LegOracle.latest_safe_depart_s;
    every construction / local-search / ILS feasibility check on a leg is then
    the O(1) comparison depart_s <= T* — dispatch departure times vary on every
    candidate insertion, which is exactly what the old (a, b, depart_s)-keyed
    verdict cache could never absorb (measured 94% of plan() wall time)."""

    def __init__(self, crews, jobmap, rasters, safe_zones, cfg, now_s,
                 inc_assign, inc_first, road_provider=None):
        self.crews = crews
        self.crew_ids = [c.crew_id for c in crews]
        self.crew_by_id = {c.crew_id: c for c in crews}
        self.jobmap = jobmap
        self.active_keys = set(jobmap)
        self.rasters = rasters                      # list[(incident_id, eroded Raster)]
        self.safe_zones = safe_zones
        self.cfg = cfg
        self.now_s = now_s
        self.inc_assign = inc_assign                # job key -> incumbent crew_id
        self.inc_first = inc_first                  # keys that were an incumbent route's first stop
        # PLAN-ON-ROADS (2026-07-12): optional duck-typed provider
        # (a, b) -> RoadRoute-like with .time_s/.dist_m/.polyline/.on_roads,
        # or None. When it yields a real road route, travel() uses ITS time
        # (exactly what the sim kinematics drive) and leg_latest_depart()
        # samples the corridor along ITS polyline at the route's implied
        # speed. None / on_roads=False legs fall back to tier-0 chords —
        # which is also exactly what the sim drives for off-network legs, so
        # prediction and kinematics agree leg-by-leg in both regimes.
        self.road_provider = road_provider
        self._rp_cache = {}
        self.corr_speed = safety._corridor_speed_mps(cfg)
        self.homes_mode = (cfg.dispatch_objective == "homes")
        # FOUNDER ROUND 4 law 7 (homes mode): per-job URGENCY on the honest
        # median arrival (eta_p50), cached ONCE — now_s and the jobmap are fixed
        # for this plan() call, so urgency is constant per job and deterministic.
        # The OBJECTIVE is urgency-blind (round-4 mis-tuning fix); this cache is
        # read only by the _urgency_reorder utility via self.urg(key), which is
        # NOT applied to the emitted plan. Empty in 'value' mode. Neither the
        # objective, the emitted plan, nor 'value' mode reads urgency, so 'value'
        # plans stay byte-identical and the emitted homes plan is byte-identical
        # for every w_urg.
        self.urgency = ({k: _urgency(j, now_s, cfg) for k, j in jobmap.items()}
                        if self.homes_mode else {})
        scores = [_potential_score(j, cfg) for j in jobmap.values()]
        self.mean_score = (sum(scores) / len(scores)) if scores else 1.0
        if self.homes_mode:
            # 'homes' (founder TRIAGE + FULL-UTILIZATION laws): the primary
            # maximand is the COUNT of protected homes (score ~ priority each);
            # value is only an <= EPS tiebreak. Derive lam_travel / churn_unit /
            # the anti-concentration unit from the VALUE COMPONENT (the eps-scaled
            # value part of the score), NOT the count credit -- if they scaled off
            # the count credit they would balloon and over-penalise travel/churn
            # (field report Defect 3). Kept this small they stay strictly below
            # one served home and can never drop a home or reorder homes away from
            # the count-maximal set.
            comp = [_HOMES_VALUE_EPS * j.client.priority_weight * _homes_value_frac(j)
                    for j in jobmap.values()]
            unit = (sum(comp) / len(comp)) if comp else 0.0
            if unit <= 0.0:
                unit = _HOMES_VALUE_EPS          # keep tiebreaks finite-positive
            self.lam_travel = 0.01 * unit
            self.churn_unit = cfg.churn_penalty_frac * unit
            # anti-concentration unit (Defect 2): fixed at _HOMES_VALUE_EPS so a
            # crew's convex stop-count penalty robustly dominates BOTH the travel
            # term (~0.01*unit) AND the freshness-scaled value tiebreak (<= EPS
            # per home), yet a single home's count credit (1.0) still dwarfs the
            # marginal spread penalty for any launch-scale route; the count
            # guardrail backstops the pathological tail.
            self.spread_unit = _HOMES_VALUE_EPS
        else:
            self.lam_travel = 0.01 * self.mean_score  # value units per drive-hour (spec 2)
            self.churn_unit = cfg.churn_penalty_frac * self.mean_score
            self.spread_unit = 0.0               # no load-spread term in legacy value mode
        self._tt = {}
        self._tstar = {}                            # leg key -> combined T* (B1)
        self._ezones = {}                           # loc key -> [(zone_id, T*)] ordered
        self._route = {}
        self._contrib = {}

    # -- primitives ---------------------------------------------------------

    def urg(self, key) -> float:
        """Cached per-job urgency on eta_p50 (FOUNDER ROUND 4 law 7), read only by
        the _urgency_reorder utility; 0.0 for an unknown key and always 0.0 in
        'value' mode (empty cache). NOT read by the objective/search or the
        emitted plan."""
        return self.urgency.get(key, 0.0)

    @staticmethod
    def _lkey(p):
        return (round(p.lat, 7), round(p.lon, 7))

    def _road_route(self, a, b):
        """Memoized road_provider lookup for the a->b leg; None when no
        provider, no route, or the route is the off-network chord fallback
        (on_roads=False -> tier-0 is already the right model)."""
        if self.road_provider is None:
            return None
        k = (self._lkey(a), self._lkey(b))
        if k in self._rp_cache:
            return self._rp_cache[k]
        rr = self.road_provider(a, b)
        if (rr is None or not getattr(rr, "on_roads", False)
                or not getattr(rr, "time_s", 0.0)
                or rr.time_s <= 0.0 or not getattr(rr, "dist_m", 0.0)
                or rr.dist_m <= 0.0):
            rr = None
        self._rp_cache[k] = rr
        return rr

    def travel(self, a, b) -> float:
        k = (self._lkey(a), self._lkey(b))
        v = self._tt.get(k)
        if v is None:
            rr = self._road_route(a, b)
            # PLAN-ON-ROADS: the route's own time IS the kinematic truth (sim
            # crews drive the polyline at rr.dist_m/rr.time_s); tier-0 chord
            # otherwise (which is what off-network crews drive).
            v = rr.time_s if rr is not None else travel_time_s(a, b, self.cfg)
            self._tt[k] = v
        return v

    def leg_latest_depart(self, a, b) -> float:
        """Combined closed-form latest safe departure for the a->b leg: min
        over EVERY incident's eroded P10 raster of the per-raster
        safety.LegOracle T* (Sprint 2 B1). Sampled once per unique leg per
        plan() invocation; UNREACHED (+inf) when nothing constrains the leg.
        The leg is corridor-safe at depart_s iff depart_s <= T*.

        PLAN-ON-ROADS: when the road provider yields a real route, the oracle
        samples along ITS polyline at the route's implied speed — the corridor
        law sees the exact ground the crew will drive (the chord-vs-road
        mismatch caused ~73% of relocations + the residual overrun tail)."""
        k = (self._lkey(a), self._lkey(b))
        v = self._tstar.get(k)
        if v is None:
            rr = self._road_route(a, b)
            path = rr.polyline if rr is not None else None
            spd = (rr.dist_m / rr.time_s) if rr is not None else self.corr_speed
            # road legs use the leaner road margin (the chord margin's extra
            # width compensated flat-travel-model error; see contracts)
            mm = (self.cfg.route_margin_roads_min if rr is not None else None)
            v = UNREACHED
            for _iid, ras in self.rasters:
                t = safety.LegOracle(a, b, spd, ras, self.cfg, path=path,
                                     margin_min=mm).latest_safe_depart_s
                if t < v:
                    v = t
            self._tstar[k] = v
        return v

    def corridor_ok(self, a, b, depart_s) -> bool:
        """Leg check against every incident's eroded P10 raster: O(1) T*
        comparison (same semantics as corridor_safe over all rasters)."""
        return depart_s <= self.leg_latest_depart(a, b)

    def egress(self, loc, depart_s):
        """Nearest safely-reachable SafeZone at depart_s, checked against every
        incident raster. Returns (safe, zone_id). Same zone ordering and
        first-safe-wins semantics as safety.egress_choice (ascending
        (travel_time, zone_id)); Sprint 2 B1 precomputes the per-(stop, zone)
        T* once, so each call is O(#zones) comparisons."""
        k = self._lkey(loc)
        zl = self._ezones.get(k)
        if zl is None:
            zl = [(z.zone_id, self.leg_latest_depart(loc, z.loc))
                  for z in sorted(self.safe_zones,
                                  key=lambda z: (self.travel(loc, z.loc), z.zone_id))]
            self._ezones[k] = zl
        for zid, tstar in zl:
            if depart_s <= tstar:
                return (True, zid)
        return (False, "")

    # -- route evaluation ----------------------------------------------------

    def eval_route(self, crew_id, keys):
        """Timeline for crew serving keys in order, or (None, (kind, key)) on
        the first hard-constraint failure. kind in {deadline, slack, corridor,
        egress}."""
        ck = (crew_id, keys)
        hit = self._route.get(ck)
        if hit is not None:
            return hit
        crew = self.crew_by_id[crew_id]
        loc = crew.loc()
        t = self.now_s
        stops = []
        fail = None
        for key in keys:
            job = self.jobmap[key]
            tt = self.travel(loc, job.client.loc)
            arrive = t + tt
            service = arrive if job.earliest_useful_start_s <= arrive else job.earliest_useful_start_s
            depart = service + self.cfg.deploy_min * 60.0
            fk = _depart_fail(depart, job.latest_depart_s, self.cfg)
            if fk is not None:
                fail = (fk, key)
                break
            if not self.corridor_ok(loc, job.client.loc, t):
                fail = ("corridor", key)
                break
            eok, zid = self.egress(job.client.loc, depart)
            if not eok:
                fail = ("egress", key)
                break
            stops.append(_Stop(key, arrive, service, depart,
                               (job.latest_depart_s - depart) / 3600.0,
                               _gel_fresh_frac(job, depart, self.cfg), zid, tt))
            loc = job.client.loc
            t = depart
        res = (stops, None) if fail is None else (None, fail)
        self._route[ck] = res
        return res

    def route_contrib(self, crew_id, keys):
        """Objective contribution of one route, or None if infeasible."""
        ck = (crew_id, keys)
        if ck in self._contrib:
            return self._contrib[ck]
        stops, _fail = self.eval_route(crew_id, keys)
        if stops is None:
            self._contrib[ck] = None
            return None
        value = 0.0
        drive_h = 0.0
        churn_w = 0.0
        for st in stops:
            job = self.jobmap[st.key]
            value += _served_value(job, st.gel_frac, self.cfg)
            drive_h += st.travel_s / 3600.0
            inc = self.inc_assign.get(st.key)
            # churn only for voluntary reassignment: incumbent crew still eligible
            if inc is not None and inc != crew_id and inc in self.crew_by_id:
                churn_w += 2.0 if st.key in self.inc_first else 1.0
        j = value - self.lam_travel * drive_h - self.churn_unit * churn_w
        if self.spread_unit:
            # anti-concentration (founder FULL-UTILIZATION law): a mild CONVEX
            # per-crew stop-count penalty (n*(n-1)/2) so, at equal protected
            # count, feasible jobs distribute across ALL eligible crews instead
            # of the cheapest one hoarding them (field report Defect 2). Scaled
            # (spread_unit, 'homes' mode only) far below one home, and the never-
            # worse-than-greedy COUNT guardrail backstops any over-penalty, so it
            # can only ever break ties among equal-count plans, never drop a home.
            n = len(stops)
            j -= self.spread_unit * (n * (n - 1) * 0.5)
        c = _Contrib(value, drive_h, churn_w, j)
        self._contrib[ck] = c
        return c

    def plan_j(self, state: _State) -> float:
        total = 0.0
        for cid in self.crew_ids:
            c = self.route_contrib(cid, tuple(state.assign[cid]))
            if c is None:
                return -math.inf
            total += c.j
        return total

    # -- unserved-reason diagnosis -------------------------------------------

    def diagnose(self, key, final: "_State | None" = None) -> str:
        """Reason a job went unserved: solo (empty-route) feasibility per crew.
        feasible-for-someone => capacity (dropped_by_optimizer when the
        incumbent had it); else the furthest-along failure wins
        (egress > corridor > slack > deadline).

        'homes' mode (founder TRIAGE law, contracts FOUNDER ROUND 3 #4) adds the
        distinct reason 'triaged': a job that IS feasible-for-someone but whose
        service would cost strictly MORE savable homes elsewhere -- the count-
        maximal plan deliberately skipped it. This is an OBJECTIVE-side abandon
        and leaves the SAFETY withdrawal law (withdrawn_unsafe/road_egress) and
        its vocabulary untouched. Reason vocabulary (contracts.UnservedJob plus
        comment-level extensions 'infeasible_slack', 'gel_window_closed' and now
        'triaged'): infeasible_deadline | infeasible_slack | gel_window_closed |
        no_safe_corridor | no_safe_egress | capacity | dropped_by_optimizer |
        triaged."""
        job = self.jobmap[key]
        outcomes = set()
        for cid in self.crew_ids:
            loc = self.crew_by_id[cid].loc()
            tt = self.travel(loc, job.client.loc)
            arrive = self.now_s + tt
            service = max(arrive, job.earliest_useful_start_s)
            depart = service + self.cfg.deploy_min * 60.0
            fk = _depart_fail(depart, job.latest_depart_s, self.cfg)
            if fk is not None:
                outcomes.add(fk)
                continue
            if not self.corridor_ok(loc, job.client.loc, self.now_s):
                outcomes.add("corridor")
                continue
            if not self.egress(job.client.loc, depart)[0]:
                outcomes.add("egress")
                continue
            outcomes.add("feasible")
        if "feasible" in outcomes:
            if self.homes_mode and final is not None and self._is_triaged(final, key):
                return "triaged"
            return "dropped_by_optimizer" if key in self.inc_assign else "capacity"
        if "egress" in outcomes:
            return "no_safe_egress"
        if "corridor" in outcomes:
            return "no_safe_corridor"
        if "slack" in outcomes:
            return "infeasible_slack"
        return "infeasible_deadline"

    def _is_triaged(self, final: _State, key) -> bool:
        """True when serving `key` would cost NET homes: the most homes a
        deterministic greedy-EDF probe can still protect WHILE forced to serve
        key is strictly fewer than the emitted plan protects. That is exactly
        the founder's triage case -- the job is feasible on its own but the
        count-maximal plan skipped it to save more homes elsewhere (an equal-
        count swap stays 'capacity')."""
        forced = self._max_served_forcing(key)
        return forced is not None and forced < _state_served(final)

    def _max_served_forcing(self, key):
        """Greedy-EDF lower bound on the homes servable when `key` MUST be
        served: seat key on its earliest-feasible-depart crew, then EDF-fill the
        rest of the active pool. Deterministic; O(pool x crews). Returns the
        served count, or None if key is feasible for no crew (caller only calls
        it when key is feasible-for-someone)."""
        st = _State({cid: [] for cid in self.crew_ids},
                    {cid: 0 for cid in self.crew_ids})
        best = None
        for cid in self.crew_ids:
            ext = _extend(self, self.crew_by_id[cid].loc(), self.now_s, key)
            if ext is not None and (best is None or (ext[2], cid) < best):
                best = (ext[2], cid)
        if best is None:
            return None
        st.assign[best[1]].append(key)
        _edf_insert(self, st, self.active_keys - {key}, time.monotonic() + 1.0)
        return _state_served(st)


# ---------------------------------------------------------------------------
# Construction: regret-2 insertion
# ---------------------------------------------------------------------------

def _best_insertions(ctx: _Ctx, state: _State, key):
    """Per-crew best (delta_j, crew_id, position) for inserting key; sorted by
    descending delta then crew_id/position for determinism."""
    out = []
    for cid in ctx.crew_ids:
        keys = state.assign[cid]
        old = ctx.route_contrib(cid, tuple(keys))
        if old is None:
            continue
        best = None
        for pos in range(state.frozen[cid], len(keys) + 1):
            nk = tuple(keys[:pos] + [key] + keys[pos:])
            new = ctx.route_contrib(cid, nk)
            if new is None:
                continue
            dj = new.j - old.j
            if best is None or dj > best[0] + _EPS:
                best = (dj, pos)
        if best is not None:
            out.append((best[0], cid, best[1]))
    out.sort(key=lambda t: (-t[0], t[1], t[2]))
    return out


def _regret2_insert(ctx: _Ctx, state: _State, pool, deadline: float) -> None:
    """Insert pool jobs by regret-2 (spec section 6.2) until nothing feasible
    and value-positive remains. Mutates state; leftover jobs stay unrouted."""
    pool = set(pool)
    while pool and time.monotonic() < deadline:
        cands = []
        for key in sorted(pool):
            if time.monotonic() >= deadline:     # in-scan budget check (perf D4)
                break
            job = ctx.jobmap[key]
            options = _best_insertions(ctx, state, key)
            options = [o for o in options if o[0] > _EPS]   # only value-positive inserts
            if not options:
                continue
            best = options[0]
            second = None
            for o in options[1:]:
                if o[1] != best[1]:                          # regret across DISTINCT crews
                    second = o
                    break
            regret = _BIG_REGRET if second is None else best[0] - second[0]
            cands.append((regret, _potential_score(job, ctx.cfg),
                          job.latest_service_start_s, key, best))
        if not cands:
            break
        # max regret; ties -> larger score, earlier deadline, stable key
        cands.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
        _r, _sc, _dl, key, (dj, cid, pos) = cands[0]
        state.assign[cid].insert(pos, key)
        pool.discard(key)


def _insert_best(ctx: _Ctx, state: _State, key) -> bool:
    """Insert key at its single best feasible position anywhere (used by
    drop-and-refill). Returns False if nowhere feasible."""
    options = _best_insertions(ctx, state, key)
    if not options:
        return False
    dj, cid, pos = options[0]
    state.assign[cid].insert(pos, key)
    return True


def _extend(ctx: _Ctx, loc, t, key):
    """O(1) feasibility + timing for serving job `key` next after being at
    `loc`, free from time `t` — the SAME per-stop chain _Ctx.eval_route
    applies (deadline + slack floor via _depart_fail, ingress corridor T*,
    egress T*; identical check order). Returns (arrive, service, depart) or
    None on any hard-constraint failure. Shared by the greedy-EDF
    construction and the exact micro-solver; anything they admit is
    re-verified by eval_route at adoption/emission time (an infeasible route
    evaluates to J = -inf and can never be emitted)."""
    job = ctx.jobmap[key]
    tt = ctx.travel(loc, job.client.loc)
    arrive = t + tt
    service = arrive if job.earliest_useful_start_s <= arrive \
        else job.earliest_useful_start_s
    depart = service + ctx.cfg.deploy_min * 60.0
    if _depart_fail(depart, job.latest_depart_s, ctx.cfg) is not None:
        return None
    if not ctx.corridor_ok(loc, job.client.loc, t):
        return None
    if not ctx.egress(job.client.loc, depart)[0]:
        return None
    return arrive, service, depart


def _edf_insert(ctx: _Ctx, state: _State, pool, deadline: float) -> None:
    """Greedy earliest-deadline-first construction (Sprint 2 B2 multi-start
    seed + never-worse-than-greedy guardrail candidate; Council-1 perf D2 and
    dispatch F3 — docs/review/perf_review.md section 3.3, docs/review/
    dispatch_alerts_review.md F3: plain EDF beat the budget-starved solver 9x
    on collected value at 20/200 and found the full-serve plan on both
    structural-miss instances).

    Jobs are appended in ascending (latest_service_start_s, key) order, each
    to the crew whose route end serves it with the EARLIEST feasible depart
    (ties: crew_id). Appending never disturbs the already-checked prefix, so
    each candidate is one O(1) _extend check — the full per-stop feasibility
    chain (deadline + slack floor per SAFETY SEMANTICS 7, ingress corridors
    against every incident raster, egress to a SafeZone) — making the seed a
    lawful plan by construction. Deterministic; mutates state; infeasible
    jobs stay unrouted. Unlike regret-2 there is NO value-positivity filter:
    the greedy baseline serves everything feasible, and the guardrail's value
    floor depends on that."""
    ends = {}
    for cid in ctx.crew_ids:
        keys = tuple(state.assign[cid])
        if keys:
            stops, _fail = ctx.eval_route(cid, keys)
            if stops is None:              # defensive; seeded skeletons are lawful
                return
            ends[cid] = (ctx.jobmap[keys[-1]].client.loc, stops[-1].depart_s)
        else:
            ends[cid] = (ctx.crew_by_id[cid].loc(), ctx.now_s)
    for key in sorted(pool, key=lambda k: (ctx.jobmap[k].latest_service_start_s, k)):
        if time.monotonic() >= deadline:
            break
        best = None
        for cid in ctx.crew_ids:
            loc, t = ends[cid]
            ext = _extend(ctx, loc, t, key)
            if ext is None:
                continue
            cand = (ext[2], cid)
            if best is None or cand < best:
                best = cand
        if best is not None:
            depart, cid = best
            state.assign[cid].append(key)
            ends[cid] = (ctx.jobmap[key].client.loc, depart)


def _state_value(ctx: _Ctx, state: _State) -> float:
    """Collected stop value (sum of score*gel_frac over routed stops) of a
    state — the guardrail's mission metric, gross of travel/churn costs."""
    total = 0.0
    for cid in ctx.crew_ids:
        c = ctx.route_contrib(cid, tuple(state.assign[cid]))
        if c is None:
            return -math.inf
        total += c.value
    return total


# ---------------------------------------------------------------------------
# Local search: relocate / swap / 2-opt / drop-and-refill (first-improvement)
# ---------------------------------------------------------------------------

def _try_relocate(ctx, state, cur_j):
    for cid1 in ctx.crew_ids:
        keys1 = state.assign[cid1]
        for i in range(state.frozen[cid1], len(keys1)):
            moved = keys1[i]
            base1 = keys1[:i] + keys1[i + 1:]
            for cid2 in ctx.crew_ids:
                target = base1 if cid2 == cid1 else state.assign[cid2]
                for pos in range(state.frozen[cid2], len(target) + 1):
                    if cid2 == cid1 and pos == i:
                        continue
                    if cid2 == cid1:
                        changed = {cid1: target[:pos] + [moved] + target[pos:]}
                    else:
                        changed = {cid1: base1,
                                   cid2: target[:pos] + [moved] + target[pos:]}
                    nj = _delta_j(ctx, state, cur_j, changed)
                    if nj is not None and nj > cur_j + _imp_eps(cur_j):
                        for c, ks in changed.items():
                            state.assign[c] = list(ks)
                        return nj
    return None


def _try_swap(ctx, state, cur_j):
    n = len(ctx.crew_ids)
    for a in range(n):
        for b in range(a + 1, n):
            cid1, cid2 = ctx.crew_ids[a], ctx.crew_ids[b]
            k1, k2 = state.assign[cid1], state.assign[cid2]
            for i in range(state.frozen[cid1], len(k1)):
                for j in range(state.frozen[cid2], len(k2)):
                    n1 = k1[:i] + [k2[j]] + k1[i + 1:]
                    n2 = k2[:j] + [k1[i]] + k2[j + 1:]
                    nj = _delta_j(ctx, state, cur_j, {cid1: n1, cid2: n2})
                    if nj is not None and nj > cur_j + _imp_eps(cur_j):
                        state.assign[cid1] = n1
                        state.assign[cid2] = n2
                        return nj
    return None


def _try_2opt(ctx, state, cur_j):
    for cid in ctx.crew_ids:
        keys = state.assign[cid]
        fz = state.frozen[cid]
        for i in range(fz, len(keys) - 1):
            for j in range(i + 1, len(keys)):
                nk = keys[:i] + list(reversed(keys[i:j + 1])) + keys[j + 1:]
                nj = _delta_j(ctx, state, cur_j, {cid: nk})
                if nj is not None and nj > cur_j + _imp_eps(cur_j):
                    state.assign[cid] = nk
                    return nj
    return None


def _try_drop_refill(ctx, state, cur_j):
    unrouted = sorted(ctx.active_keys - state.assigned(),
                      key=lambda k: (-_potential_score(ctx.jobmap[k], ctx.cfg), k))
    if not unrouted:
        return None
    routed = []
    for cid in ctx.crew_ids:
        for k in state.assign[cid][state.frozen[cid]:]:
            routed.append((_potential_score(ctx.jobmap[k], ctx.cfg), k, cid))
    routed.sort()
    for u in unrouted:
        su = _potential_score(ctx.jobmap[u], ctx.cfg)
        for sk, k, cid in routed:
            if sk >= su:
                break
            trial = state.copy()
            trial.assign[cid].remove(k)
            if not _insert_best(ctx, trial, u):
                continue
            _insert_best(ctx, trial, k)          # may fail; k then stays unrouted
            nj = ctx.plan_j(trial)
            if nj > cur_j + _imp_eps(cur_j):
                state.assign = trial.assign
                state.frozen = trial.frozen
                return nj
    return None


def _delta_j(ctx, state, cur_j, changed):
    """New plan J when routes in `changed` (crew_id -> new key list) replace the
    current ones; None if any changed route is infeasible."""
    nj = cur_j
    for cid, nk in changed.items():
        old = ctx.route_contrib(cid, tuple(state.assign[cid]))
        new = ctx.route_contrib(cid, tuple(nk))
        if new is None or old is None:
            return None
        nj += new.j - old.j
    return nj


def _local_search(ctx: _Ctx, state: _State, deadline: float) -> None:
    cur_j = ctx.plan_j(state)
    if cur_j == -math.inf:
        return
    while time.monotonic() < deadline:
        for move in (_try_relocate, _try_swap, _try_2opt, _try_drop_refill):
            nj = move(ctx, state, cur_j)
            if nj is not None:
                cur_j = nj
                break
        else:
            return


# ---------------------------------------------------------------------------
# ILS shake
# ---------------------------------------------------------------------------

def _shake(ctx: _Ctx, state: _State, iteration: int, extra: int = 0):
    """Remove a seeded-RNG random ~20% (+ `extra`, the VNS escalation) of
    non-frozen stops; returns the removed keys. Deterministic for identical
    (now_s, iteration, extra).

    Sprint 2 B2 (Council-1 dispatch F3 root cause): a 1-stop shake followed by
    deterministic regret-2 reinsertion rebuilds the same plan shape, so
    structural optima needing a simultaneous multi-route restructure were
    unreachable. The ILS loop escalates `extra` while iterations fail to
    improve (variable-neighborhood style), so a stalled search perturbs
    progressively larger fractions of the plan — always leaving at least one
    stop as an anchor when possible."""
    flat = [(cid, k) for cid in ctx.crew_ids
            for k in state.assign[cid][state.frozen[cid]:]]
    if not flat:
        return []
    rng = seeded_rng("wgresponse.dispatch.shake", repr(ctx.now_s), iteration)
    n = max(1, int(round(0.2 * len(flat)))) + max(0, extra)
    n = min(n, max(1, len(flat) - 1))     # keep an anchor stop when possible
    removed = []
    for cid, k in rng.sample(flat, min(n, len(flat))):
        state.assign[cid].remove(k)
        removed.append(k)
    return removed


# ---------------------------------------------------------------------------
# Exact micro-solver for brute-forceable instances (Sprint 2 B2)
# ---------------------------------------------------------------------------

_EXACT_MAX_JOBS = 8       # free-job cap: DFS explores sum_k P(n,k) ordered routes
_EXACT_MAX_CREWS = 4      # subset-DP combine is 3^n per crew step


class _ExactAbort(Exception):
    """Raised inside the exact DFS when the solver budget expires."""


def _state_served(state: _State) -> int:
    return sum(len(v) for v in state.assign.values())


def _exact_small_solve(ctx: _Ctx, base: _State, deadline: float):
    """TRUE optimum for brute-forceable pools (Council-1 dispatch F3: the
    review's 2/24 structural misses were instances where the optimum needs a
    simultaneous multi-route restructure no local neighborhood reaches; at
    launch scale — a few crews, a handful of threatened clients — the honest
    fix is to solve exactly).

    Applies only when the non-frozen job pool has <= _EXACT_MAX_JOBS keys and
    there are <= _EXACT_MAX_CREWS eligible crews; returns None otherwise (or
    on budget expiry — the heuristic result then stands, same budget-pressure
    caveat as the ILS). Per crew, a DFS enumerates every ordered feasible
    extension of the frozen-committed prefix under the FULL feasibility chain
    (_depart_fail deadline + slack floor, B1 corridor T*, egress T*), keeping
    the best objective contribution per served subset — identical arithmetic
    to _Ctx.route_contrib (value*gel_frac, lambda_travel, churn). A subset-DP
    across crews then maximizes total J (ties -> more served, then stable
    order). Deterministic when it completes."""
    if not ctx.crew_ids or len(ctx.crew_ids) > _EXACT_MAX_CREWS:
        return None
    universe = sorted(ctx.active_keys - base.assigned())
    n = len(universe)
    if n == 0 or n > _EXACT_MAX_JOBS:
        return None
    cfg = ctx.cfg
    lam = ctx.lam_travel
    cunit = ctx.churn_unit
    spread_unit = ctx.spread_unit
    counter = [0]

    def crew_table(cid):
        """mask -> (contrib_j, route_tuple) over feasible ordered extensions."""
        prefix = tuple(base.assign[cid])
        pc = ctx.route_contrib(cid, prefix)
        if pc is None:                    # defensive; repaired prefixes are feasible
            return None
        if prefix:
            stops, _f = ctx.eval_route(cid, prefix)
            loc0, t0 = ctx.jobmap[prefix[-1]].client.loc, stops[-1].depart_s
        else:
            loc0, t0 = ctx.crew_by_id[cid].loc(), ctx.now_s
        table = {0: (pc.j, prefix)}

        def dfs(mask, route, loc, t, value, drive_h, churn_w):
            counter[0] += 1
            if counter[0] % 256 == 0 and time.monotonic() >= deadline:
                raise _ExactAbort()
            for i, key in enumerate(universe):
                if mask & (1 << i):
                    continue
                ext = _extend(ctx, loc, t, key)
                if ext is None:
                    continue
                depart = ext[2]
                job = ctx.jobmap[key]
                nv = value + _served_value(job, _gel_fresh_frac(job, depart, cfg),
                                           cfg)
                nd = drive_h + ctx.travel(loc, job.client.loc) / 3600.0
                inc = ctx.inc_assign.get(key)
                nw = churn_w
                if inc is not None and inc != cid and inc in ctx.crew_by_id:
                    nw += 2.0 if key in ctx.inc_first else 1.0
                nm = mask | (1 << i)
                nr = route + (key,)
                j = nv - lam * nd - cunit * nw
                if spread_unit:                 # anti-concentration; matches route_contrib
                    m = len(nr)
                    j -= spread_unit * (m * (m - 1) * 0.5)
                cur = table.get(nm)
                if cur is None or j > cur[0] + 1e-9:
                    table[nm] = (j, nr)
                dfs(nm, nr, job.client.loc, depart, nv, nd, nw)

        dfs(0, prefix, loc0, t0, pc.value, pc.drive_h, pc.churn_w)
        return table

    try:
        tables = []
        for cid in ctx.crew_ids:
            tbl = crew_table(cid)
            if tbl is None:
                return None
            tables.append(tbl)
    except _ExactAbort:
        return None

    # subset-DP across crews: mask -> (total_j, [route per processed crew])
    dp = {0: (0.0, [])}
    for tbl in tables:
        ndp = {}
        for m0 in sorted(dp):
            j0, routes = dp[m0]
            for s in sorted(tbl):
                if m0 & s:
                    continue
                js, route = tbl[s]
                m1 = m0 | s
                j1 = j0 + js
                cur = ndp.get(m1)
                if cur is None or j1 > cur[0] + 1e-9:
                    ndp[m1] = (j1, routes + [route])
        dp = ndp

    best_mask = None
    best_j = -math.inf
    best_served = -1
    for m in sorted(dp):
        j, _routes = dp[m]
        served = bin(m).count("1")
        if j > best_j + 1e-9 or (abs(j - best_j) <= 1e-9 and served > best_served):
            best_mask, best_j, best_served = m, j, served
    if best_mask is None:
        return None
    routes = dp[best_mask][1]
    return _State({cid: list(routes[i]) for i, cid in enumerate(ctx.crew_ids)},
                  dict(base.frozen))


# ---------------------------------------------------------------------------
# Final-plan polish: apply gel as late as safely possible
# ---------------------------------------------------------------------------

def _late_shift(ctx: _Ctx, crew_id: str, keys, stops, frozen_n: int):
    """Backward pass delaying non-frozen services toward full gel freshness
    (depart* = eta_p50 - gel_full_strength), capped by
    latest_depart - slack_min*60 (the SAFETY SEMANTICS 7 slack floor — the
    polish must never spend the safety slack the feasibility chain enforced)
    and by the successor's chosen service start; every shifted depart
    re-verifies the stop's egress and its outbound ingress corridor. Frozen
    stops keep their planned times. Returns a rebuilt, verified stop list
    (never less safe: caps guarantee margin_h >= slack_min/60)."""
    if not stops:
        return stops
    cfg = ctx.cfg
    n = len(stops)
    chosen_depart = [st.depart_s for st in stops]
    for i in range(n - 1, -1, -1):
        if i < frozen_n:
            continue
        job = ctx.jobmap[keys[i]]
        eta50 = job.eta.eta_p50_s
        if not math.isfinite(eta50):
            continue
        _window_h, full_h = _eff_gel_window_h(job, cfg)
        desired = eta50 - full_h * 3600.0
        cap = job.latest_depart_s - cfg.slack_min * 60.0   # slack floor (SEMANTICS 7)
        if i + 1 < n:
            leg = ctx.travel(job.client.loc, ctx.jobmap[keys[i + 1]].client.loc)
            cap = min(cap, (chosen_depart[i + 1] - cfg.deploy_min * 60.0) - leg)
        new_depart = min(max(stops[i].depart_s, desired), cap)
        if new_depart <= stops[i].depart_s + _EPS:
            continue
        if not ctx.egress(job.client.loc, new_depart)[0]:
            continue
        if i + 1 < n and not ctx.corridor_ok(job.client.loc,
                                             ctx.jobmap[keys[i + 1]].client.loc,
                                             new_depart):
            continue
        chosen_depart[i] = new_depart
    # forward rebuild with the chosen departs
    out = []
    crew = ctx.crew_by_id[crew_id]
    loc = crew.loc()
    t = ctx.now_s
    for i, key in enumerate(keys):
        job = ctx.jobmap[key]
        tt = stops[i].travel_s
        arrive = t + tt
        depart = chosen_depart[i]
        service = depart - cfg.deploy_min * 60.0
        if service < arrive - _EPS:            # defensive; caps should prevent this
            service = max(arrive, job.earliest_useful_start_s)
            depart = service + cfg.deploy_min * 60.0
        _eok, zid = ctx.egress(job.client.loc, depart)
        out.append(_Stop(key, arrive, service, depart,
                         (job.latest_depart_s - depart) / 3600.0,
                         _gel_fresh_frac(job, depart, cfg),
                         zid or stops[i].egress_zone_id, tt))
        loc = job.client.loc
        t = depart
    return out


# ---------------------------------------------------------------------------
# Within-count-maximal-plan urgency ordering (FOUNDER ROUND 4 law 7 utility)
# ---------------------------------------------------------------------------

def _urgency_reorder(ctx: _Ctx, crew_id: str, keys, frozen_n: int):
    """Order a crew's NON-frozen stops to service the sooner-to-burn (higher-
    urgency, on the honest median p50) home first, WITHOUT ever changing the
    served SET (FOUNDER ROUND 4 law 7, re-scoped by the round-4 mis-tuning fix
    as the option-A STRICT can't-cost-a-home tiebreak).

    *** NOT wired into the emitted plan (plan() emits the urgency-BLIND order
    verbatim). *** This is a count-safe-PER-PLAN utility: the count-maximal
    assignment is already fixed and was chosen URGENCY-BLIND (the objective in
    _potential_score/_served_value has no urgency term), so the served set/count
    is byte-identical to dispatch_urgency_weight=0 pure count-max. This function
    only PERMUTES one crew's own non-frozen suffix and adopts the permutation
    ONLY when the whole reordered route stays feasible under the exact same
    safety chain (eval_route: deadline + slack floor, ingress corridor T*, egress
    T* — every original stop still served). So per plan() call it can never drop
    a home, move a home onto another crew, or change how many homes are served.

    It stays OUT of the emitted plan because it is NOT count-safe DYNAMICALLY: the
    emit order becomes the crew's itinerary -> next tick's frozen commitment
    skeleton -> the greedy count floor, and that feedback was measured to cost
    homes on some seeds. A feedback-free at-risk-first opt-in is deferred. Kept
    (and unit-tested) so the within-fixed-set at-risk-first ordering concept is
    verified and available.

    No-op in 'value' mode and at w_urg == 0. The sort key (descending
    w_urg*urgency + w_val*valuefrac, ties by stable job key) and eval_route are
    pure functions of the fixed plan inputs, so the reorder is deterministic."""
    cfg = ctx.cfg
    if not ctx.homes_mode or cfg.dispatch_urgency_weight == 0.0:
        return keys
    suffix = keys[frozen_n:]
    if len(suffix) < 2:
        return keys
    ordered = sorted(
        suffix,
        key=lambda k: (
            -(cfg.dispatch_urgency_weight * ctx.urg(k)
              + cfg.dispatch_value_weight * _homes_value_frac(ctx.jobmap[k])), k))
    if ordered == list(suffix):
        return keys                              # already urgency-ordered
    cand = list(keys[:frozen_n]) + ordered
    stops, _fail = ctx.eval_route(crew_id, tuple(cand))
    if stops is not None:                        # fully feasible => reorder is free
        return cand
    return keys                                  # not free => keep the blind order


# ---------------------------------------------------------------------------
# plan() — the contract seam
# ---------------------------------------------------------------------------

def plan(crews: list, jobs: list, ens: dict, safe_zones: list,
         cfg: ResponseConfig, now_s: float,
         incumbent: "DispatchPlan | None" = None,
         forecasts: "dict | None" = None,
         road_provider=None) -> DispatchPlan:
    """Build a DispatchPlan for `crews` over `jobs` at unix time now_s.

    ens: incident_id -> EnsembleArrival; each incident's P10 raster is eroded
    by cfg.corridor_erosion_m and EVERY travel leg (ingress and egress) is
    checked against every incident's eroded raster. safe_zones: candidate
    egress destinations (no zones => nothing is schedulable, fail closed).
    incumbent: previous DispatchPlan for warm-start/commitment/churn handling.
    Crews without a known location, OFF_DUTY, or UNKNOWN (stale GPS beacon —
    last-known position is out of date) are ineligible. Deterministic for
    identical inputs absent solver-budget pressure.

    forecasts (Sprint 2 C5, Council-1 dispatch F5): OPTIONAL incident_id ->
    contracts.WeatherForecast map. Each job's gel window is then derived with
    ITS incident's forecast — safety.derive_job_windows(job, cfg, wx=...) —
    so under cfg.gel_env_model_enabled the coating life reflects the actual
    fire weather at the job's as-late-as-safe service time (hot/windy fire
    weather: 1.0-1.5 h) instead of the 25 C / 50 %RH / 0-wind reference
    6.0 h. Additive and default None (weatherless derivation — the prior
    behavior); incidents missing from the map derive weatherless too. With
    gel_env_model_enabled=False the forecast is never read and plans are
    byte-identical with or without it (pinned in tests/test_dispatch.py). A
    job whose service window is EMPTY under the env model is unserved with
    the distinct structural reason 'gel_window_closed' (see step 1 above;
    comment-vocabulary extension like 'infeasible_slack' — contracts
    unchanged).

    Sprint 2 (Council-1 dispatch F2+F4): jobs are collapsed to ONE per
    client_id before planning (worst-case eta_p10 survives — one house is one
    gel application, never two stops / double-counted value), and every stop
    must carry >= cfg.slack_min minutes of planned slack (SAFETY SEMANTICS 7;
    unserved reason 'infeasible_slack' when only the floor fails).

    Sprint 2 B1+B2 (Council-1 perf D1+D2, dispatch F3): corridor/egress
    feasibility inside the solver runs on per-leg closed-form latest-safe-
    departure oracles (safety.LegOracle, built once per unique leg per call),
    construction is multi-start ({repaired incumbent + regret-2, greedy-EDF,
    frozen-skeleton regret-2} -> LS -> ILS from the best), and the emitted
    plan is guarded to never be worse than the raw greedy-EDF seed in either
    objective or collected value (objective_breakdown['seed'/'guardrail']).

    road_provider (PLAN-ON-ROADS, 2026-07-12): optional (a, b) -> RoadRoute-
    like (.time_s/.dist_m/.polyline/.on_roads) or None. When given, travel
    times use the route's own time and corridor T* oracles sample along the
    route's polyline — the planner sees the ground crews actually drive
    (chord-vs-road mismatch measured at ~73% of relocations + the residual
    overrun tail). None (default) = tier-0 chords, byte-identical.
    """
    t0 = time.monotonic()
    deadline = t0 + max(0.05, cfg.solver_budget_s)

    eligible = sorted((c for c in crews
                       if c.loc() is not None
                       and c.status not in _INELIGIBLE_STATUSES),
                      key=lambda c: c.crew_id)

    rasters = [(iid, safety._erode(ens[iid].p10, cfg.corridor_erosion_m))
               for iid in sorted(ens)]

    # -- step 0: ONE job per client (Sprint 2 A7, Council-1 dispatch F4) ------
    # A property is a single gel target no matter how many incidents threaten
    # it. Keep the worst-case job per client_id: earliest eta_p10, ties broken
    # by earliest eta_p50, then incident_id (stable/deterministic). The
    # surviving stop therefore honors the tightest safety deadline; corridor
    # checks still run against EVERY incident's eroded raster, so dropping the
    # duplicate jobs never weakens a safety check. The union of the other
    # incident ids is logged (ClientProperty has no free-form note field) and
    # the drop count lands in objective_breakdown['dedup_dropped_jobs'].
    surviving = {}                                       # client_id -> DispatchJob
    client_incidents = {}                                # client_id -> set(incident_id)
    for job in sorted(jobs, key=lambda j: (j.client.client_id, j.eta.eta_p10_s,
                                           j.eta.eta_p50_s, j.incident_id)):
        cid = job.client.client_id
        client_incidents.setdefault(cid, set()).add(job.incident_id)
        if cid not in surviving:
            surviving[cid] = job
    dedup_dropped = len(jobs) - len(surviving)
    for cid in sorted(surviving):
        others = sorted(client_incidents[cid] - {surviving[cid].incident_id})
        if others:
            _LOG.info("dedupe: client %s planned once under %s "
                      "(also threatened by: %s)",
                      cid, surviving[cid].incident_id, ", ".join(others))

    # -- step 1: windows + prune already-infeasible jobs ----------------------
    unserved_pre = []
    jobmap = {}
    for job in sorted(surviving.values(),
                      key=lambda j: (j.client.client_id, j.incident_id)):
        key = _job_key(job)
        # Sprint 2 C5 (Council-1 dispatch F5): derive the gel window with THIS
        # incident's forecast so the env coating-life model sees real fire
        # weather (1.0-1.5 h hot/windy) instead of its 6.0 h reference. wx is
        # only read inside derive_job_windows when cfg.gel_env_model_enabled,
        # so flag-OFF plans stay byte-identical whether or not forecasts are
        # passed (pinned in tests/test_dispatch.py).
        wx = forecasts.get(job.incident_id) if forecasts else None
        safety.derive_job_windows(job, cfg, wx=wx)
        if not eligible:
            unserved_pre.append(UnservedJob(key[0], key[1], "capacity"))
            continue
        min_tt = min(travel_time_s(c.loc(), job.client.loc, cfg) for c in eligible)
        # empty/degenerate service window (incl. eta_p50=UNREACHED => window
        # [inf, inf], the fire is not forecast to arrive) or deadline already
        # out of reach for every crew: lost to dispatch (spec section 3)
        if (job.earliest_useful_start_s == UNREACHED
                or job.earliest_useful_start_s > job.latest_service_start_s + _EPS
                or now_s + min_tt > job.latest_service_start_s + _EPS):
            reason = "infeasible_deadline"
            # Sprint 2 C5: an EMPTY window with a FINITE opening under the
            # env gel model is STRUCTURAL, not a crew-timing miss — the
            # p50-p10 forecast spread exceeds the coating life minus the
            # egress+deploy buffers (Council-1 F5: at a 6 h life, spread
            # > ~5 h is unservable no matter how many crews exist), and a
            # weather-shrunk life makes it the dominant unserved mode. Report
            # it distinctly ('gel_window_closed' — comment vocabulary, not
            # contracts). Gated on the flag: with gel_env_model_enabled=False
            # the historical 'infeasible_deadline' is kept byte-identically.
            if (cfg.gel_env_model_enabled
                    and job.earliest_useful_start_s != UNREACHED
                    and job.earliest_useful_start_s
                    > job.latest_service_start_s + _EPS):
                reason = "gel_window_closed"
            unserved_pre.append(UnservedJob(key[0], key[1], reason))
            continue
        jobmap[key] = job

    inc_assign = {}
    inc_first = set()
    frozen_keys = set()
    if incumbent is not None:
        for cid in sorted(incumbent.plans):
            cp = incumbent.plans[cid]
            for idx, st in enumerate(cp.stops):
                key = (st.client_id, st.incident_id)
                inc_assign[key] = cid
                if idx == 0:
                    inc_first.add(key)
                if st.service_start_s < now_s + cfg.commitment_horizon_min * 60.0:
                    frozen_keys.add(key)

    ctx = _Ctx(eligible, jobmap, rasters, safe_zones, cfg, now_s, inc_assign,
               inc_first, road_provider=road_provider)

    # -- step 2: warm start (repair incumbent; freeze committed stops) --------
    state = _State({cid: [] for cid in ctx.crew_ids}, {cid: 0 for cid in ctx.crew_ids})
    if incumbent is not None:
        for cid in ctx.crew_ids:
            cp = incumbent.plans.get(cid)
            if cp is None:
                continue
            keys = [(st.client_id, st.incident_id) for st in cp.stops
                    if (st.client_id, st.incident_id) in jobmap]
            # drop now-infeasible stops one at a time (safety trumps commitment)
            while keys:
                stops, fail = ctx.eval_route(cid, tuple(keys))
                if stops is not None:
                    break
                keys.remove(fail[1])
            state.assign[cid] = keys
            fz = 0
            for k in keys:
                if k in frozen_keys:
                    fz += 1
                else:
                    break
            state.frozen[cid] = fz

    # -- steps 3+5: multi-start construction (Sprint 2 B2), LS, ILS -----------
    # Guardrail candidate FIRST: greedy-EDF from the frozen-committed skeleton,
    # built before any budget can be spent elsewhere so the never-worse-than-
    # greedy floor always exists. Frozen (committed) stops appear in EVERY
    # candidate, so commitment semantics are unchanged.
    base = _State({cid: state.assign[cid][:state.frozen[cid]]
                   for cid in ctx.crew_ids}, dict(state.frozen))
    greedy = base.copy()
    _edf_insert(ctx, greedy, ctx.active_keys - greedy.assigned(), deadline)
    greedy_j = ctx.plan_j(greedy)

    # Seeds: {repaired incumbent + regret-2 (the Sprint-1 construction),
    # greedy-EDF, regret-2 from the frozen skeleton}; duplicates skipped.
    s_warm = state
    _regret2_insert(ctx, s_warm, ctx.active_keys - s_warm.assigned(), deadline)
    seeds = [("warm", s_warm)]
    if greedy.assign != s_warm.assign:
        seeds.append(("edf", greedy.copy()))
    if incumbent is not None:
        s_cold = base.copy()
        _regret2_insert(ctx, s_cold, ctx.active_keys - s_cold.assigned(), deadline)
        if all(s_cold.assign != s.assign for _t, s in seeds):
            seeds.append(("cold", s_cold))
    for _t, s in seeds:
        _local_search(ctx, s, deadline)
    warm_j = ctx.plan_j(s_warm)          # incumbent-shaped reference (adoption)

    seed_tag, best = seeds[0]
    best_j = ctx.plan_j(best)
    for t, s in seeds[1:]:               # deterministic argmax; warm wins ties
        sj = ctx.plan_j(s)
        if sj > best_j + _imp_eps(best_j):
            seed_tag, best, best_j = t, s, sj
    best = best.copy()

    # Exact micro-solver first (B2): brute-forceable pools (<= 8 free jobs,
    # <= 4 crews — the launch-scale regime) get the TRUE optimum over the
    # identical feasibility chain and objective; the ILS cannot beat it, so
    # it is skipped when the exact pass completes. Ties adopt the exact
    # result only when it serves MORE stops (mission tie-break).
    exact = _exact_small_solve(ctx, base, deadline)
    if exact is not None:
        ej = ctx.plan_j(exact)
        if ej > best_j + _imp_eps(best_j) or (
                ej >= best_j - _imp_eps(best_j)
                and _state_served(exact) > _state_served(best)):
            best, best_j, seed_tag = exact, ej, "exact"

    stall = 0                            # VNS escalation counter (B2, F3)
    for it in range(cfg.ils_iterations if exact is None else 0):
        if time.monotonic() >= deadline:
            break
        cand = best.copy()
        removed = _shake(ctx, cand, it, extra=stall // 3)
        # Ruin & recreate with a ONE-ITERATION tabu on the removed keys:
        # without it, regret-2 deterministically re-inserts exactly the stops
        # the shake removed and rebuilds the same plan shape (Council-1
        # dispatch F3 root cause), so an optimum that requires PERMANENTLY
        # dropping a greedy-attractive stop — e.g. two tight-deadline homes
        # instead of one earlier, higher-value one — stays unreachable. The
        # tabu'd keys get their re-entry chance in the second refill below
        # (and in later iterations), so nothing feasible is ever left out of
        # an improving candidate.
        _regret2_insert(ctx, cand,
                        ctx.active_keys - cand.assigned() - set(removed), deadline)
        _local_search(ctx, cand, deadline)
        pool2 = ctx.active_keys - cand.assigned()
        if pool2:
            _regret2_insert(ctx, cand, pool2, deadline)
            _local_search(ctx, cand, deadline)
        cj = ctx.plan_j(cand)
        if cj > best_j + _imp_eps(best_j):
            best, best_j = cand, cj
            stall = 0
        else:
            stall += 1

    # adoption threshold: the ILS plan must beat the incumbent-shaped (warm)
    # plan by adoption_threshold_frac, else keep the warm plan (spec 8.2)
    if incumbent is not None and (best_j - warm_j) < cfg.adoption_threshold_frac * max(abs(warm_j), ctx.mean_score):
        final, tag, final_j = s_warm, "warm", warm_j
    else:
        final, tag, final_j = best, "ils", best_j

    # never-worse-than-greedy guardrail (Sprint 2 B2, Council-1 perf D2 +
    # dispatch F3): the emitted plan is argmax objective over {polished result,
    # raw greedy-EDF}; additionally the emitted plan must never serve less
    # collected VALUE than raw greedy-EDF (objective and value can diverge by
    # the small travel/churn terms — the mission guarantee is on protected
    # value, so the floor is enforced on both).
    guardrail = ""
    if ctx.homes_mode:
        # 'homes' (founder TRIAGE law): the mission metric is the COUNT of homes
        # protected. greedy-EDF is count-natural (it appends EVERY feasible job,
        # no value filter), so the emitted plan must never protect FEWER homes
        # than greedy-EDF; among equal count it must not be objectively worse.
        # This is what keeps the anti-concentration term a pure tie-break: a
        # spread plan that ever cost a home is overridden back to greedy here.
        if _state_served(greedy) > _state_served(final):
            final, tag, final_j, guardrail = greedy, "greedy_edf", greedy_j, "count"
        elif greedy_j > final_j + _imp_eps(final_j):
            final, tag, final_j, guardrail = greedy, "greedy_edf", greedy_j, "objective"
    else:
        if greedy_j > final_j + _imp_eps(final_j):
            final, tag, final_j, guardrail = greedy, "greedy_edf", greedy_j, "objective"
        elif _state_value(ctx, greedy) > _state_value(ctx, final) + _EPS:
            final, tag, final_j, guardrail = greedy, "greedy_edf", greedy_j, "value"

    # -- step 6: emit (with late-shift freshness polish) -----------------------
    plans = {}
    served = set()
    total_value = 0.0
    total_drive_h = 0.0
    churn_w = 0.0
    for cid in ctx.crew_ids:
        # STRICT can't-cost-a-home (round-4 mis-tuning fix): the emitted plan is
        # the urgency-BLIND count-maximal plan verbatim. Urgency is NOT applied
        # to the emitted order -- any emit-order change would feed back through
        # the sim's commitment loop (this order becomes the crew itinerary ->
        # next tick's frozen skeleton -> the greedy count floor) and was measured
        # to deterministically cost homes on some seeds. _urgency_reorder is
        # count-safe PER CALL but NOT dynamically, so it stays out of the emitted
        # plan; see its docstring and the module header.
        keys = final.assign[cid]
        stops, _fail = ctx.eval_route(cid, tuple(keys))
        # COAT-EARLY lever (dispatch_late_shift, default on = shipped): the gel-
        # freshness backward polish. Off => coat as early as feasible, freeing
        # crews sooner for more homes (founder dispatch loop 2026-07-11).
        if cfg.dispatch_late_shift:
            stops = _late_shift(ctx, cid, keys, stops or [], final.frozen[cid])
        planned = []
        egz = ""
        for st in stops:
            job = ctx.jobmap[st.key]
            planned.append(PlannedStop(
                client_id=st.key[0], incident_id=st.key[1],
                arrive_s=st.arrive_s, service_start_s=st.service_start_s,
                depart_s=st.depart_s, latest_depart_s=job.latest_depart_s,
                margin_h=st.margin_h, gel_fresh_frac=st.gel_frac))
            served.add(st.key)
            total_value += _served_value(job, st.gel_frac, cfg)
            total_drive_h += st.travel_s / 3600.0
            inc = inc_assign.get(st.key)
            if inc is not None and inc != cid and inc in ctx.crew_by_id:
                churn_w += 2.0 if st.key in inc_first else 1.0
            egz = st.egress_zone_id
        plans[cid] = CrewPlan(crew_id=cid, stops=planned, egress_zone_id=egz,
                              committed_until_s=now_s + cfg.commitment_horizon_min * 60.0)

    unserved = list(unserved_pre)
    for key in sorted(ctx.active_keys - served):
        unserved.append(UnservedJob(key[0], key[1], ctx.diagnose(key, final)))

    travel_cost = ctx.lam_travel * total_drive_h
    churn_penalty = ctx.churn_unit * churn_w
    objective = total_value - travel_cost - churn_penalty
    breakdown = {
        "value_usd": total_value,
        "travel_cost_usd": travel_cost,
        "churn_penalty_usd": churn_penalty,
        "drive_h": total_drive_h,
        "served": len(served),
        "unserved": len(unserved),
        "churn_weight": churn_w,
        "mean_stop_score_usd": ctx.mean_score,
        "lambda_travel_usd_per_h": ctx.lam_travel,
        "candidate": tag,
        "seed": seed_tag,                 # winning construction seed (B2)
        "guardrail": guardrail,           # ''|'objective'|'value' (B2 floor)
        "solver_s": time.monotonic() - t0,
        "dedup_dropped_jobs": dedup_dropped,
    }
    return DispatchPlan(computed_at_s=now_s, plans=plans, unserved=unserved,
                        objective=objective, objective_breakdown=breakdown,
                        churn_from_incumbent=churn_w)
