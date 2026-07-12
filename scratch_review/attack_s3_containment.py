"""ADVERSARIAL VERIFIER (Session 3) — attack #2: containment gate (D2,
SAFETY SEMANTICS 8).

Claims under attack (verifier brief #2):
  2a. Growth oscillation right at the threshold: with acreage +/-0.4%/cycle
      noise, does 'no growth for 48 h' ever engage, or does noise hold the
      cap off forever? (Measured: it holds it OFF forever — see FINDING F2.)
  2b. Gate ON must not change ANY DispatchPlan or corridor verdict — full
      monitor + REAL dispatcher, byte-compare on a seeded scenario where the
      cap demonstrably engages and the ladder diverges.
  2c. Pairs at EXTREME before the cap engages STAY EXTREME (cap never
      downgrades); IMPACTED stays reachable while capped; normal hysteresis
      still owns downgrades; the cap lifts immediately on growth.
  2d. Boundaries: containment exactly at the ceiling caps; no-growth window
      exactly at containment_no_growth_h caps; just under either does not.

FINDINGS documented here:
  F2  (minor, fail-safe polarity) There is NO growth epsilon: any acreage
      up-tick > 1e-9 ACRES re-stamps the no-growth clock. Multi-source
      fusion picks acreage from whichever member is freshest, so two sources
      that disagree by 0.4% flip the fused acreage every cycle and the
      watch-ceiling can never engage on such fires (measured 0 capped
      cycles over 120 h). Polarity is safe — the cap stays OFF and alerts
      escalate normally — but the founder-approved alert-reduction feature
      silently cannot do its job on multi-source fires, and no epsilon knob
      exists in contracts to tune it.
  OBS-1 (documented consequence, ladder-gated dispatch by design): a capped
      incident whose ONLY trigger is p10 (p50 beyond notice_h) holds NONE,
      so it produces NO DispatchJob while the uncapped ladder (EXTREME)
      would have. Deadlines/corridors of every job that DOES exist are
      untouched (2b), but the job SET is downstream of the ladder.

Run from repo root:
    py -3 -m pytest scratch_review/attack_s3_containment.py -v
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scratch_review.attack_s3_common import (  # noqa: E402
    H, T0, FakeClock, StubTracker, SAFE_ZONE_N, fresh_crew, incident,
    make_daemon, small_cfg,
)
from wgresponse import dispatch as dispatch_mod  # noqa: E402
from wgresponse import threat  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertState, IncidentSource, ResponseConfig, ThreatAssessment, ThreatEta,
)
from wgresponse.feeds import fuse_incidents  # noqa: E402
from wgresponse.sim import jsonable  # noqa: E402


def hot(cid, now_s, iid="inc-1", p10_h=6.0, p50_h=10.0):
    eta = ThreatEta(eta_p10_s=now_s + p10_h * H, eta_p50_s=now_s + p50_h * H,
                    eta_p90_s=now_s + p50_h * 1.5 * H)
    return ThreatAssessment(client_id=cid, incident_id=iid, eta=eta,
                            computed_at_s=now_s)


def gate_cfg(**kw) -> ResponseConfig:
    cfg = ResponseConfig(containment_gate_enabled=True)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def meta(acreage, containment=95.0, iid="inc-1"):
    return {iid: {"name": "Contained Fire", "containment_pct": containment,
                  "acreage": acreage}}


def first_state_per_cycle(acreages, containment=95.0, cycles=None,
                          period_h=1.0):
    """Feed an acreage series; per cycle escalate a FRESH pair and record its
    first-cycle state — NOTICE_72H means the cap was ENGAGED that cycle,
    EXTREME_24H means it was OFF."""
    cfg = gate_cfg()
    engine = AlertEngine(cfg)
    out = []
    n = cycles if cycles is not None else len(acreages)
    for k in range(n):
        now = T0 + k * period_h * H
        acr = acreages[k] if not callable(acreages) else acreages(k)
        events = engine.evaluate([hot(f"c{k}", now)], now,
                                 incident_meta=meta(acr, containment))
        (ev,) = [e for e in events if e.client_id == f"c{k}"]
        out.append(ev.state)
    return out


# ---------------------------------------------------------------------------
# 2a. growth-noise oscillation vs the no-growth clock
# ---------------------------------------------------------------------------

def test_control_constant_acreage_caps_after_exactly_no_growth_h():
    states = first_state_per_cycle(lambda k: 1000.0, cycles=60)
    # First observation initializes the clock at T0; the cap engages at the
    # first cycle with now - T0 >= 48 h -> cycle 48 exactly (>= boundary).
    first_capped = next(i for i, s in enumerate(states)
                        if s is AlertState.NOTICE_72H)
    assert first_capped == 48
    assert all(s is AlertState.EXTREME_24H for s in states[:48])
    assert all(s is AlertState.NOTICE_72H for s in states[48:])


def test_FINDING_f2_pm_0p4pct_oscillation_holds_cap_off_forever():
    """FINDING F2 (minor, fail-safe): 1000 <-> 1004 acre flapping (+0.4%)
    — e.g. two fused sources disagreeing, freshest-wins alternating — re-
    stamps last_growth_s on every up-tick, so a 95%-contained fire is NEVER
    capped across 120 h (0 capped cycles). Safe direction (alerts escalate
    normally), but the gate cannot engage on real multi-source noise and
    there is no epsilon knob."""
    states = first_state_per_cycle(
        lambda k: 1000.0 if k % 2 == 0 else 1004.0, cycles=120)
    capped = sum(s is AlertState.NOTICE_72H for s in states)
    assert capped == 0                          # pins the defect
    assert all(s is AlertState.EXTREME_24H for s in states)


def test_FINDING_f2_strictly_rising_0p4pct_noise_never_caps():
    states = first_state_per_cycle(lambda k: 1000.0 * (1.004 ** k), cycles=120)
    assert sum(s is AlertState.NOTICE_72H for s in states) == 0


def test_growth_epsilon_is_one_nano_acre():
    """The only 'epsilon' is 1e-9 acres (~4 mm^2): an up-tick of 2e-9 acres
    per cycle re-stamps the clock; 5e-10 does not. Documents how sharp the
    knife edge is."""
    below = first_state_per_cycle(lambda k: 1000.0 + k * 5e-10, cycles=60)
    above = first_state_per_cycle(lambda k: 1000.0 + k * 2e-9, cycles=60)
    assert below[48:] == [AlertState.NOTICE_72H] * 12    # caps (sub-epsilon)
    assert all(s is AlertState.EXTREME_24H for s in above)  # never caps


def test_fused_acreage_flips_with_member_freshness():
    """The feeds-side half of F2: _merge_group takes acreage from the
    freshest member, so two sources disagreeing by 0.4% flip the fused
    acreage as their relative freshness alternates cycle to cycle."""
    def pair(newer_first):
        a = incident(iid="w-1", name="Volcano", source=IncidentSource.WFIGS,
                     irwin_id="IRW-1", acreage=1000.0,
                     updated_at_s=T0 - (600.0 if newer_first else 7200.0))
        b = incident(iid="f-1", name="Volcano", source=IncidentSource.FIRIS,
                     irwin_id="IRW-1", acreage=1004.0,
                     updated_at_s=T0 - (7200.0 if newer_first else 600.0))
        return fuse_incidents([[a], [b]])

    (fused_a,) = pair(newer_first=True)
    (fused_b,) = pair(newer_first=False)
    assert fused_a.incident_id == fused_b.incident_id == "irw-1"
    assert fused_a.acreage == 1000.0
    assert fused_b.acreage == 1004.0            # 1000 -> 1004 reads as growth


# ---------------------------------------------------------------------------
# 2d. boundaries
# ---------------------------------------------------------------------------

def test_containment_exactly_at_ceiling_caps_just_under_does_not():
    at = first_state_per_cycle(lambda k: 1000.0, containment=90.0, cycles=50)
    under = first_state_per_cycle(lambda k: 1000.0, containment=89.999,
                                  cycles=50)
    assert at[48] is AlertState.NOTICE_72H          # 90.0 >= 90.0 caps
    assert under[48] is AlertState.EXTREME_24H      # 89.999 never caps
    assert at[47] is AlertState.EXTREME_24H         # 47 h quiet: not yet


# ---------------------------------------------------------------------------
# 2c. never-downgrade / IMPACTED / hysteresis / immediate lift
# ---------------------------------------------------------------------------

def test_extreme_before_cap_stays_extreme_then_normal_hysteresis_owns_it():
    cfg = gate_cfg(clear_cycles=2)
    engine = AlertEngine(cfg)
    # t0: first sight of the incident (clock initializes) — c1 escalates
    # EXTREME uncapped (gate cannot engage before 48 h of tracked history).
    ev = engine.evaluate([hot("c1", T0)], T0, incident_meta=meta(800.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H

    # 49 h later, no growth: the cap is engaged (a fresh pair proves it) but
    # c1 — already above WATCH — is untouched through repeated hot cycles.
    t = T0 + 49 * H
    engine.evaluate([hot("c1", t), hot("probe", t)], t,
                    incident_meta=meta(800.0))
    assert engine.states()[("probe", "inc-1")].state is AlertState.NOTICE_72H
    for k in range(1, 4):
        engine.evaluate([hot("c1", t + k * H)], t + k * H,
                        incident_meta=meta(800.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H

    # Fire recedes (not extreme, not clear): NORMAL de-escalation hysteresis
    # still owns the downgrade to WATCH while capped...
    for k in range(2):
        tt = t + (4 + k) * H
        engine.evaluate([hot("c1", tt, p10_h=30.0, p50_h=40.0)], tt,
                        incident_meta=meta(800.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.WATCH

    # ...and while capped, WATCH holds even when the fire goes hot again.
    tt = t + 7 * H
    engine.evaluate([hot("c1", tt)], tt, incident_meta=meta(800.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.WATCH

    # Growth resumes -> the cap lifts THIS cycle: WATCH -> EXTREME again.
    tt = t + 8 * H
    engine.evaluate([hot("c1", tt)], tt, incident_meta=meta(900.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H


def test_impacted_still_reachable_while_capped():
    cfg = gate_cfg()
    engine = AlertEngine(cfg)
    engine.evaluate([hot("c1", T0)], T0, incident_meta=meta(800.0))
    assert engine.states()[("c1", "inc-1")].state is AlertState.EXTREME_24H
    t = T0 + 49 * H
    # Median arrival has PASSED while the incident is capped: the pair must
    # still ride EXTREME -> IMPACTED (the cap never blocks the terminal).
    eta = ThreatEta(eta_p10_s=t - 2 * H, eta_p50_s=t - H, eta_p90_s=t + H)
    engine.evaluate(
        [ThreatAssessment(client_id="c1", incident_id="inc-1", eta=eta,
                          computed_at_s=t),
         hot("probe", t)], t, incident_meta=meta(800.0))
    assert engine.states()[("probe", "inc-1")].state is AlertState.NOTICE_72H
    assert engine.states()[("c1", "inc-1")].state is AlertState.IMPACTED


def test_OBS1_p10_only_trigger_capped_pair_produces_no_dispatch_job():
    """OBS-1: capped + p10-only trigger (p50 beyond notice_h) holds NONE —
    and NONE is not a dispatch state, so the job set differs from the
    uncapped ladder (which would be EXTREME -> job). Ladder-gated dispatch
    is the designed architecture; documenting the operational consequence:
    a >=90%-contained fire with a scary p10 tail gets NO pre-gel while
    capped."""
    def run(gate_on):
        cfg = gate_cfg(containment_gate_enabled=gate_on)
        engine = AlertEngine(cfg)
        engine.restore({}, incident_growth={
            "inc-1": {"acreage": 800.0, "last_growth_s": T0 - 100 * H}})
        eta = ThreatEta(eta_p10_s=T0 + 8 * H, eta_p50_s=math.inf,
                        eta_p90_s=math.inf)
        engine.evaluate(
            [ThreatAssessment(client_id="c1", incident_id="inc-1", eta=eta,
                              computed_at_s=T0)], T0,
            incident_meta=meta(800.0))
        return engine.states()[("c1", "inc-1")].state

    assert run(gate_on=False) is AlertState.EXTREME_24H
    assert run(gate_on=True) is AlertState.NONE
    assert AlertState.NONE not in threat._DISPATCH_STATES
    assert AlertState.EXTREME_24H in threat._DISPATCH_STATES


# ---------------------------------------------------------------------------
# 2b. gate ON vs OFF: byte-identical dispatch through the REAL planner
# ---------------------------------------------------------------------------

def _plan_bytes(plan):
    """Canonical plan JSON minus objective_breakdown['solver_s'] — the one
    deliberately wall-clock field in a plan (time.monotonic solve timing,
    diagnostic only). It differs even between two identical gate-OFF runs;
    test_gate_off_rerun_is_deterministic_modulo_solver_s proves everything
    ELSE is deterministic, so the ON-vs-OFF compare stays byte-honest."""
    d = jsonable(plan)
    if isinstance(d, dict):
        (d.get("objective_breakdown") or {}).pop("solver_s", None)
    return json.dumps(d, sort_keys=True)


def _run_gated_scenario(tmp_path, gate_on):
    """Two full monitor cycles with the real dispatch module + a real crew +
    safe zone; the incident is 95% contained with 100 h of persisted
    no-growth history so the cap engages from cycle 1 when the gate is ON."""
    cfg = small_cfg(containment_gate_enabled=gate_on)
    contained = incident(acreage=800.0, containment_pct=95.0)
    tracker = StubTracker([fresh_crew(T0)])
    daemon, _, engine, _, _ = make_daemon(
        tmp_path / ("on" if gate_on else "off"), cfg=cfg,
        dispatcher=dispatch_mod, incidents=[contained],
        clock=FakeClock(T0), tracker=tracker, safe_zones=[SAFE_ZONE_N])
    engine.restore({}, incident_growth={
        "inc-1": {"acreage": 800.0, "last_growth_s": T0 - 100 * H}})
    out = []
    for _ in range(2):
        res = daemon.run_cycle()
        out.append({
            "state": engine.states()[("c1", "inc-1")].state,
            "plan": _plan_bytes(res.plan),
            "ens": [tuple(m.data) for m in res.ensembles["inc-1"].members],
            "p10": tuple(res.ensembles["inc-1"].p10.data),
            "assessed": [(a.client_id, a.incident_id, a.eta.eta_p10_s,
                          a.eta.eta_p50_s, a.eta.eta_p90_s)
                         for a in res.assessments],
        })
    return out


def test_gate_off_rerun_is_deterministic_modulo_solver_s(tmp_path):
    """Self-control: two identical gate-OFF runs produce byte-identical
    plans once solver_s is excluded — the ON-vs-OFF compare below therefore
    detects ANY semantic divergence."""
    a = _run_gated_scenario(tmp_path / "a", gate_on=False)
    b = _run_gated_scenario(tmp_path / "b", gate_on=False)
    for k in range(2):
        assert a[k]["plan"] == b[k]["plan"]
        assert a[k]["ens"] == b[k]["ens"]


def test_gate_on_never_changes_dispatch_plan_or_safety_inputs(tmp_path):
    on = _run_gated_scenario(tmp_path, gate_on=True)
    off = _run_gated_scenario(tmp_path, gate_on=False)

    # The ladder DID diverge — the cap really engaged (non-vacuous compare):
    assert off[0]["state"] is AlertState.EXTREME_24H
    assert on[0]["state"] in (AlertState.NOTICE_72H, AlertState.WATCH)
    assert off[1]["state"] is AlertState.EXTREME_24H
    assert on[1]["state"] is AlertState.WATCH

    for k in range(2):
        # DispatchPlan byte-identical (stops, times, margins, corridor-based
        # unserved reasons, objective, churn — the whole plan JSON):
        assert on[k]["plan"] == off[k]["plan"], f"plan diverged on cycle {k}"
        # Crew-safety inputs byte-identical: every ensemble member raster and
        # the P10 grid every corridor check reads:
        assert on[k]["ens"] == off[k]["ens"]
        assert on[k]["p10"] == off[k]["p10"]
        assert on[k]["assessed"] == off[k]["assessed"]
        # And the plan is non-trivial (a real job existed both ways).
        plan = json.loads(on[k]["plan"])
        n_stops = sum(len(cp["stops"]) for cp in plan["plans"].values())
        assert n_stops + len(plan["unserved"]) >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
