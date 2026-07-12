"""Silent PARTIAL registry regression — roster absence is not clearance.

Promoted from scratch_review/attack_partial_registry.py (Sprint 2, Council-1
adversarial verifier finding, major): a registry returning a syntactically-
healthy but INCOMPLETE roster (DynamoDB partial scan / pagination bug /
upstream geocode regression silently dropping lat/lon rows — see
wgresponse/clients.py _item_to_client) raised no exception, so the tracked
pair went absent while its incident stayed ACTIVE and every feed healthy —
and walked EXTREME -> CLEARED through normal hysteresis, false 'All clear'
notification included, in ~10 minutes (clear_cycles x 300 s), with ZERO
degradation signals.

THE FIX (wgresponse/threat.py, lawful under contracts SAFETY SEMANTICS 6):
the contracts default is "a pair with no assessment this cycle FREEZES", and
its two trustworthy-clear exceptions — (a) incident ASSESSED and no longer
threatening, (b) incident absent while every feed is healthy — cover a
missing-client pair in NEITHER case (nobody assessed that the fire no longer
threatens that client; the incident is still present). ThreatMonitor.cycle
therefore marks a tracked pair BLIND when its incident is still ACTIVE but
its client was not among the registry candidates for that incident: state
frozen, blind_cycles counting, OperatorEvent('provider_error', step
'registry_roster') + 'degraded', cycle degraded=True, zero client sends.
A client the registry DID return who merely sits beyond the plausible threat
radius remains a genuine assessed-clear absence (clear streak progresses) —
the fix must not over-freeze legitimate out-of-reach clearance.

Deterministic; no network.
"""
from __future__ import annotations

import pytest

from wgresponse import geo
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    ResponseConfig,
    ThreatAssessment,
    ThreatEta,
)

from test_attack_false_clear import (   # promoted sibling (same directory)
    A_CENTER,
    A_CLIENT,
    KEY_A,
    Stack,
    T0,
    _incident,
)


class PartialRegistry:
    """Wraps a healthy registry; silently drops one client_id (no exception)."""

    def __init__(self, inner):
        self._inner = inner
        self.drop = None

    def protected_clients(self, bbox=None):
        out = self._inner.protected_clients(bbox)
        if self.drop is not None:
            out = [c for c in out if c.client_id != self.drop]
        return out


def _partial_stack():
    stack = Stack(
        incidents=[_incident("fire-A", "ALPHA", A_CENTER)],
        clients=[ClientProperty(client_id="client-A", loc=A_CLIENT,
                                name="Client A")])
    stack.registry = PartialRegistry(stack.registry._inner)
    stack.mon._registry = stack.registry
    return stack


def test_silent_partial_registry_freezes_pair_and_alarms_operator():
    """THE regression (was: EXTREME -> CLEARED + false all-clear send with
    degraded=False and blind_cycles=0 — scratch_review/attack_partial_registry
    probe transcript). Now: frozen, alarmed, degraded, zero sends."""
    stack = _partial_stack()
    stack.escalate_to_extreme([KEY_A])
    base = len(stack.notifier.sent)

    stack.registry.drop = "client-A"          # silent partial scan begins
    for k in range(1, 4):
        res = stack.cycle()
        rec = stack.engine.states()[KEY_A]
        assert res.degraded is True, "silent partial roster read as healthy"
        assert rec.state is AlertState.EXTREME_24H, (
            "silent partial registry walked a threatened pair off EXTREME")
        assert rec.blind_cycles == k
        assert rec.clear_streak == 0
        assert res.events == []
        kinds = {e.kind for e in res.operator_events}
        assert "provider_error" in kinds       # monitor: roster-shrink alarm
        assert "degraded" in kinds             # engine: frozen-pair alarm
        roster_evs = [e for e in res.operator_events
                      if e.detail.get("step") == "registry_roster"]
        assert roster_evs and roster_evs[0].incident_id == "fire-A"
        assert roster_evs[0].detail["clients"] == ["client-A"]
        assert any("missing" in n and "roster" in n for n in res.notes)
    assert len(stack.notifier.sent) == base, (
        "client notified during a silent-partial-registry window "
        "(the old false all-clear)")


def test_partial_registry_heals_recovered_once_then_genuine_clear():
    """Roster heals -> exactly one 'recovered', ladder resumes EXTREME (fire
    still burning); fire then genuinely ends -> normal hysteresis CLEARED
    with exactly one (unsuppressed) all-clear send."""
    stack = _partial_stack()
    stack.escalate_to_extreme([KEY_A])
    base = len(stack.notifier.sent)

    stack.registry.drop = "client-A"
    for _ in range(3):
        stack.cycle()
    stack.registry.drop = None                 # roster heals
    res = stack.cycle()
    assert res.degraded is False
    assert [e.kind for e in res.operator_events] == ["recovered"]
    rec = stack.engine.states()[KEY_A]
    assert rec.state is AlertState.EXTREME_24H
    assert rec.blind_cycles == 0
    assert len(stack.notifier.sent) == base

    stack.feed.mode = "gone"                   # fire genuinely out
    stack.cycle()                              # clear streak 1 of 2
    res = stack.cycle()                        # streak 2 -> CLEARED
    assert [e.state for e in res.events] == [AlertState.CLEARED]
    assert len(stack.notifier.sent) == base + 1


def test_client_vouched_by_registry_but_out_of_reach_still_clears():
    """The over-freeze guard: a client the registry DOES return, sitting
    beyond the plausible threat radius (in the search bbox corner but > the
    60 km radius cap), is a genuine assessed-clear absence — the pair must
    keep walking to CLEARED through normal hysteresis, not freeze."""
    # 75 km NE of the fire: inside the (radius-padded, axis-aligned) search
    # bbox corner, outside the 60 km capped threat radius.
    far_loc = geo.destination(A_CENTER, 45.0, 75_000.0)
    stack = Stack(
        incidents=[_incident("fire-A", "ALPHA", A_CENTER)],
        clients=[ClientProperty(client_id="client-far", loc=far_loc,
                                name="Far Client")])
    key = ("client-far", "fire-A")
    # Pre-seed the pair to EXTREME at the engine seam (a client can hold an
    # alert from earlier geometry; the monitor then stops assessing it once
    # it sits beyond the current threat radius).
    now = T0 - 600.0
    stack.engine.evaluate([ThreatAssessment(
        client_id="client-far", incident_id="fire-A",
        eta=ThreatEta(eta_p10_s=now + 8 * 3600.0, eta_p50_s=now + 20 * 3600.0,
                      eta_p90_s=now + 30 * 3600.0),
        computed_at_s=now)], now)
    assert stack.engine.states()[key].state is AlertState.EXTREME_24H
    base = len(stack.notifier.sent)

    res1 = stack.cycle()                       # registry vouches; out of reach
    assert res1.degraded is False
    assert any("no protected clients" in n for n in res1.notes)
    rec = stack.engine.states()[key]
    assert rec.state is AlertState.EXTREME_24H     # hysteresis: 1 of 2
    assert rec.blind_cycles == 0, "over-freeze: vouched client marked blind"
    res2 = stack.cycle()
    assert [e.state for e in res2.events] == [AlertState.CLEARED]
    assert stack.engine.states()[key].state is AlertState.CLEARED
    assert len(stack.notifier.sent) == base + 1    # genuine all-clear sends


def test_client_outside_search_window_freezes_conservatively():
    """A tracked pair whose client the registry did NOT return for the
    incident's search window (here: 100 km due east, outside the radius-padded
    bbox) cannot be vouched clear — the registry never reported on it, and
    ignition geometry can shrink through data loss (perimeter -> point), so
    'far from CURRENT geometry' must not read as clearance. Contracts default:
    unassessed pairs FREEZE. Pinned: frozen + operator-alarmed, never CLEARED,
    zero sends — releases only when the incident itself ends."""
    out_loc = geo.destination(A_CENTER, 90.0, 100_000.0)
    stack = Stack(
        incidents=[_incident("fire-A", "ALPHA", A_CENTER)],
        clients=[ClientProperty(client_id="client-out", loc=out_loc,
                                name="Out Client")])
    key = ("client-out", "fire-A")
    now = T0 - 600.0
    stack.engine.evaluate([ThreatAssessment(
        client_id="client-out", incident_id="fire-A",
        eta=ThreatEta(eta_p10_s=now + 8 * 3600.0, eta_p50_s=now + 20 * 3600.0,
                      eta_p90_s=now + 30 * 3600.0),
        computed_at_s=now)], now)
    base = len(stack.notifier.sent)

    for k in range(1, 3):
        res = stack.cycle()
        rec = stack.engine.states()[key]
        assert res.degraded is True
        assert rec.state is AlertState.EXTREME_24H
        assert rec.blind_cycles == k
        assert any(e.detail.get("step") == "registry_roster"
                   for e in res.operator_events)
    assert len(stack.notifier.sent) == base

    stack.feed.mode = "gone"                   # incident ends: pair releases
    stack.cycle()
    res = stack.cycle()
    assert [e.state for e in res.events] == [AlertState.CLEARED]
    assert len(stack.notifier.sent) == base + 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
