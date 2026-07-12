"""ATTACK PROBE — silent PARTIAL registry response (no exception).

FINDING STATUS: FIXED (Sprint-2 fixer session) and PROMOTED to
tests/test_attack_partial_registry.py — that file is the regression of
record; this probe just pins the same behavior from the original attack
harness.

Original finding (major): a registry that returns a syntactically-healthy
but INCOMPLETE client list (DynamoDB partial scan / pagination bug / filter
regression — live trigger: clients.py _item_to_client silently drops rows
with unparseable lat/lon) made the tracked pair absent while its incident
was assessed and every feed healthy — and the pair walked EXTREME -> CLEARED
through normal hysteresis, false 'All clear' send included, with ZERO
degradation signals.

The fix did NOT need a contracts amendment: contracts SAFETY SEMANTICS 6
defaults every unassessed pair to FREEZE, and neither trustworthy-clear
clause ((a) incident assessed and no longer threatening, (b) incident absent
with healthy feeds) covers a client silently missing from the roster while
its incident is still ACTIVE. ThreatMonitor.cycle now marks such pairs BLIND
(frozen + OperatorEvent 'provider_error'/step 'registry_roster' + degraded
cycle, zero client sends). Clients the registry DOES return who merely sit
beyond the threat radius still clear normally.

Run:  py -3 -m pytest -q scratch_review/attack_partial_registry.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

import attack_false_clear as afc
from wgresponse.contracts import AlertState, ClientProperty


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


def test_silent_partial_registry_freezes_pair_fixed_behavior():
    stack = afc.Stack(
        incidents=[afc._incident("fire-A", "ALPHA", afc.A_CENTER)],
        clients=[ClientProperty(client_id="client-A", loc=afc.A_CLIENT,
                                name="Client A")])
    stack.registry = PartialRegistry(stack.registry._inner)
    stack.mon._registry = stack.registry
    stack.escalate_to_extreme([afc.KEY_A])
    base = len(stack.notifier.sent)

    stack.registry.drop = "client-A"          # silent partial scan begins
    states = []
    for _ in range(3):
        res = stack.cycle()
        rec = stack.engine.states()[afc.KEY_A]
        states.append((res.degraded, rec.state, rec.blind_cycles))

    # FIXED behavior: every partial-roster cycle is DEGRADED, the pair is
    # BLIND (frozen EXTREME, blind_cycles counting), and no client
    # notification goes out. See tests/test_attack_partial_registry.py for
    # the full regression (operator events, recovery, radius boundary).
    assert all(degraded for degraded, _, _ in states)
    assert [blind for _, _, blind in states] == [1, 2, 3]
    assert all(state is AlertState.EXTREME_24H for _, state, _ in states), (
        "silent partial registry walked a threatened pair off EXTREME")
    assert len(stack.notifier.sent) == base   # zero sends — no false all-clear


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
