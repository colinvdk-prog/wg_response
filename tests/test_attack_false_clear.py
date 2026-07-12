"""False-clear attack regressions — SAFETY SEMANTICS 6 hard edges + the
all-clear suppression gate (Sprint 2, Council-1 verifier findings).

Promoted from scratch_review/attack_false_clear.py (adversarial verification
of Sprint-2 Session-1 ticket A1). Extends the four ported drills in
tests/test_failsafe.py (origin: scratch_review/drill_failures.py) with the
MIXED / PARTIAL / FLAPPING outage geometries the straight drills never
exercised:

  * feed ok + weather dead              (assessment-step blindness, feeds healthy)
  * terrain dead ONLY for incident B    (selective blindness; A's ladder must live)
  * registry intermittent (odd cycles)  (blind/assessed churn; no decay to clear)
  * feed flapping [] ok / [] blind / incidents / [] ok  (hysteresis intact)
  * incident renamed mid-outage         (VERIFIER FINDING, fixed: the residual
    client-visible 'All clear' for the old incident id is now SUPPRESSED while
    the same client holds an active alert for the renamed incident — clients
    cannot distinguish incident ids, so an all-clear during id churn read as
    "the fire is gone" while it still burned)
  * two incidents, one client, one blind (blind pair must not ride the healthy
    pair's clear evaluation to CLEARED)
  * frozen EXTREME + fire genuinely gone during the outage (recovery must still
    CLEAR, with exact clear_cycles timing)
  * ZERO client notifications on any blind cycle, any state (incl. the
    NOTICE->WATCH auto-progression, which must also freeze)
  * blind freeze PAUSES (does not reset) the clear streak — characterization
    pinned for Council review (a reset-on-freeze variant would be strictly
    more conservative; flipping it is a policy decision, not a bug fix)

Deterministic; no network; contracts.seeded_rng mocks only.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from wgresponse.alerts import AlertEngine
from wgresponse.clients import MockClientRegistry
from wgresponse.contracts import (
    AlertState,
    ClientProperty,
    FireIncident,
    IncidentSource,
    LatLon,
    ResponseConfig,
    ThreatAssessment,
    ThreatEta,
)
from wgresponse.notify import LogNotifier
from wgresponse.terrain import SyntheticTerrain
from wgresponse.threat import ThreatMonitor
from wgresponse.weather import MockWeather

T0 = 1_780_000_000.0
PERIOD = 300.0

# Incident A geometry (identical to tests/test_failsafe.py — known to escalate
# to EXTREME within 4 cycles). Incident B: same layout ~250 km away so the two
# spread domains can never overlap (selective-kill isolation).
A_CENTER = LatLon(34.20, -118.50)
A_CLIENT = LatLon(34.235, -118.50)
B_CENTER = LatLon(36.20, -116.00)
B_CLIENT = LatLon(36.235, -116.00)
KEY_A = ("client-A", "fire-A")
KEY_B = ("client-B", "fire-B")


def _ring(center: LatLon, half: float = 0.005) -> list:
    return [LatLon(center.lat - half, center.lon - half),
            LatLon(center.lat - half, center.lon + half),
            LatLon(center.lat + half, center.lon + half),
            LatLon(center.lat + half, center.lon - half),
            LatLon(center.lat - half, center.lon - half)]


def _incident(iid: str, name: str, center: LatLon) -> FireIncident:
    return FireIncident(incident_id=iid, name=name, source=IncidentSource.MOCK,
                        origin=center, updated_at_s=T0 - 3600.0, active=True,
                        perimeter=[_ring(center)], irwin_id=iid,
                        source_ids={"mock": iid})


class SwitchableFeed:
    """'ok' healthy fetch; 'dead' [] + last_fetch_ok=False; 'gone' [] healthy.
    .incidents is mutable so the rename attack can swap ids mid-run.

    Healthy fetches re-stamp updated_at_s = now_s (live-feed refresh
    semantics: an active fire is re-observed every poll). Sprint-2 ticket A5
    anchors the perimeter front at the FEED timestamp, so a permanently
    frozen updated_at_s would model a fire burning UNOBSERVED for the whole
    drill — its front would (correctly) overrun the drill client mid-scenario
    and terminal IMPACTED would mask the ladder/outage semantics under test.
    The Stack sets now_s before each cycle."""

    def __init__(self, incidents):
        self.incidents = list(incidents)
        self.mode = "ok"
        self.last_fetch_ok = True
        self.now_s = T0

    def fetch_active(self, bbox=None):
        if self.mode == "dead":
            self.last_fetch_ok = False
            return []
        self.last_fetch_ok = True
        if self.mode == "gone":
            return []
        return [replace(inc, updated_at_s=self.now_s)
                for inc in self.incidents]


class KillableWeather:
    def __init__(self, inner):
        self._inner = inner
        self.dead = False

    def forecast(self, loc, horizon_h):
        if self.dead:
            raise RuntimeError("NWS 503 (simulated)")
        return self._inner.forecast(loc, horizon_h)


class SelectiveTerrain:
    """Raises ONLY for spread domains containing `dead_point` (incident-B-only
    terrain outage); every other tile request succeeds."""

    def __init__(self, inner, dead_point=None):
        self._inner = inner
        self.dead_point = dead_point

    def tile(self, bbox, cell_m):
        if self.dead_point is not None and bbox.contains(self.dead_point):
            raise RuntimeError("LFPS job failed for this AOI (simulated)")
        return self._inner.tile(bbox, cell_m)


class FlakyRegistry:
    def __init__(self, inner):
        self._inner = inner
        self.dead = False

    def protected_clients(self, bbox=None):
        if self.dead:
            raise RuntimeError("DynamoDB throttled (simulated)")
        return self._inner.protected_clients(bbox)


class Stack:
    """Two-incident, two-client drill stack with selectively killable providers."""

    def __init__(self, incidents=None, clients=None):
        self.cfg = ResponseConfig(horizon_h=48.0, poll_period_s=PERIOD,
                                  ensemble_n=2, grid_cell_m=150.0,
                                  grid_max_cells=20_000)
        incidents = incidents if incidents is not None else [
            _incident("fire-A", "ALPHA", A_CENTER),
            _incident("fire-B", "BRAVO", B_CENTER)]
        clients = clients if clients is not None else [
            ClientProperty(client_id="client-A", loc=A_CLIENT, name="Client A"),
            ClientProperty(client_id="client-B", loc=B_CLIENT, name="Client B")]
        self.feed = SwitchableFeed(incidents)
        # hot/dry/strong wind from the south -> pushes at both clients (north)
        self.weather = KillableWeather(MockWeather(
            "atk", base_wind_mps=12.0, dir_deg=180.0, rh_pct=10.0,
            temp_c=35.0, t0_s=T0))
        self.terrain = SelectiveTerrain(SyntheticTerrain("atk", kind="grass"))
        self.registry = FlakyRegistry(MockClientRegistry("atk", clients=clients))
        self.notifier = LogNotifier()
        self.engine = AlertEngine(self.cfg, notifier=self.notifier,
                                  clients_by_id={c.client_id: c for c in clients})
        self.mon = ThreatMonitor(feeds=[self.feed],
                                 weather_provider=self.weather,
                                 terrain_provider=self.terrain,
                                 registry=self.registry,
                                 alert_engine=self.engine, cfg=self.cfg,
                                 safe_zones=[])
        self.t = T0
        self.results = []

    def cycle(self):
        self.feed.now_s = self.t          # live-feed refresh (see SwitchableFeed)
        res = self.mon.cycle(self.t)
        self.t += PERIOD
        self.results.append(res)
        return res

    def escalate_to_extreme(self, keys):
        for i in range(4):
            self.cycle()
            st = self.engine.states()
            if all(k in st and st[k].state is AlertState.EXTREME_24H
                   for k in keys):
                return i + 1
        raise AssertionError(
            f"setup failed to escalate {keys}: "
            f"{ {k: v.state.value for k, v in self.engine.states().items()} }")


def _sends(stack):
    return len(stack.notifier.sent)


# ===========================================================================
# ATTACK 1a — feed ok + weather dead: every pair blind, feeds healthy
# ===========================================================================

def test_feed_ok_weather_dead_freezes_all_pairs_no_notifications():
    """Feeds report the fires fine but weather is down: EVERY assessment fails,
    feeds_healthy stays True — the freeze must ride blind_pairs alone.
    Ported from scratch_review/attack_false_clear.py (ATTACK 1a)."""
    stack = Stack()
    stack.escalate_to_extreme([KEY_A, KEY_B])
    base = _sends(stack)

    stack.weather.dead = True
    for _ in range(4):
        res = stack.cycle()
        assert res.degraded is True
        assert res.events == []
        assert res.assessments == []
        kinds = {e.kind for e in res.operator_events}
        assert "provider_error" in kinds
        assert "degraded" in kinds
    st = stack.engine.states()
    assert st[KEY_A].state is AlertState.EXTREME_24H
    assert st[KEY_B].state is AlertState.EXTREME_24H
    assert st[KEY_A].blind_cycles >= 3 and st[KEY_B].blind_cycles >= 3
    assert st[KEY_A].clear_streak == 0 and st[KEY_B].clear_streak == 0
    assert _sends(stack) == base, "client notified during a weather-blind cycle"

    # recovery: fire still burning -> EXTREME holds, recovered fires once/pair
    stack.weather.dead = False
    res = stack.cycle()
    assert res.degraded is False
    assert sorted(e.kind for e in res.operator_events) == ["recovered", "recovered"]
    assert _sends(stack) == base


# ===========================================================================
# ATTACK 1b — terrain dead ONLY for incident B; A's ladder must keep living
# ===========================================================================

def test_terrain_dead_only_for_b_freezes_b_while_a_ladder_lives():
    """Ported from scratch_review/attack_false_clear.py (ATTACK 1b)."""
    stack = Stack()
    stack.escalate_to_extreme([KEY_A, KEY_B])
    base = _sends(stack)

    stack.terrain.dead_point = B_CENTER
    for _ in range(3):
        res = stack.cycle()
        assert res.degraded is True
        # A keeps being assessed (still burning toward client-A -> no events)
        assert any(a.incident_id == "fire-A" for a in res.assessments)
        assert all(a.incident_id != "fire-B" for a in res.assessments)
    st = stack.engine.states()
    assert st[KEY_B].state is AlertState.EXTREME_24H
    assert st[KEY_B].blind_cycles == 3
    assert st[KEY_A].state is AlertState.EXTREME_24H   # genuinely still burning
    assert st[KEY_A].blind_cycles == 0
    assert _sends(stack) == base

    # Now fire A genuinely goes out (healthy absence) while B stays blind:
    # A must CLEAR through hysteresis; B must stay frozen. The blind pair must
    # NOT ride A's clear evaluations. (client-A's all-clear is NOT suppressed:
    # the suppression gate is per-CLIENT, and client-B's EXTREME belongs to a
    # different client.)
    stack.feed.incidents = [i for i in stack.feed.incidents
                            if i.incident_id != "fire-A"]
    stack.cycle()                                       # A clear streak 1
    res = stack.cycle()                                 # A clear streak 2
    cleared = [(e.client_id, e.incident_id) for e in res.events
               if e.state is AlertState.CLEARED]
    assert cleared == [KEY_A]
    st = stack.engine.states()
    assert st[KEY_A].state is AlertState.CLEARED
    assert st[KEY_B].state is AlertState.EXTREME_24H, (
        "blind pair cleared off the healthy pair's evaluation — FALSE CLEAR")
    assert st[KEY_B].blind_cycles == 5
    assert _sends(stack) == base + 1                    # exactly A's all-clear


# ===========================================================================
# ATTACK 1c — registry intermittent: fails odd cycles, heals even cycles
# ===========================================================================

def test_registry_intermittent_odd_cycles_never_walks_to_cleared():
    """Ported from scratch_review/attack_false_clear.py (ATTACK 1c)."""
    stack = Stack()
    stack.escalate_to_extreme([KEY_A, KEY_B])
    base = _sends(stack)

    for k in range(8):
        stack.registry.dead = (k % 2 == 0)              # odd wall cycles dead
        res = stack.cycle()
        st = stack.engine.states()
        assert st[KEY_A].state is AlertState.EXTREME_24H
        assert st[KEY_B].state is AlertState.EXTREME_24H
        if stack.registry.dead:
            assert res.degraded is True
            assert {e.kind for e in res.operator_events} >= {"provider_error",
                                                             "degraded"}
            assert st[KEY_A].blind_cycles == 1          # resets every heal
        else:
            assert res.degraded is False
            assert st[KEY_A].blind_cycles == 0
            assert all(e.kind == "recovered" for e in res.operator_events)
    assert _sends(stack) == base, "intermittent registry leaked a notification"
    assert stack.engine.states()[KEY_A].clear_streak == 0


# ===========================================================================
# ATTACK 1d — feed flapping: [] ok, [] blind, incidents, [] ok, [] ok
# ===========================================================================

def test_feed_flapping_empty_ok_empty_blind_incidents_then_gone():
    """Ported from scratch_review/attack_false_clear.py (ATTACK 1d)."""
    stack = Stack()
    stack.feed.mode = "gone"                            # [] healthy: nothing tracked
    res = stack.cycle()
    assert res.degraded is False and res.events == []
    stack.feed.mode = "dead"                            # [] blind: still nothing
    res = stack.cycle()
    assert res.degraded is True and res.events == []
    assert stack.engine.states() == {}

    stack.feed.mode = "ok"                              # incidents appear
    stack.escalate_to_extreme([KEY_A, KEY_B])
    base = _sends(stack)

    stack.feed.mode = "gone"                            # healthy absence #1
    res = stack.cycle()
    assert res.events == [], "CLEARED leaked after a SINGLE healthy-empty cycle"
    st = stack.engine.states()
    assert st[KEY_A].state is AlertState.EXTREME_24H
    assert _sends(stack) == base

    res = stack.cycle()                                 # healthy absence #2
    cleared = sorted((e.client_id, e.incident_id) for e in res.events
                     if e.state is AlertState.CLEARED)
    assert cleared == sorted([KEY_A, KEY_B])
    assert _sends(stack) == base + 2                    # exactly one per pair


def test_feed_flap_burning_blind_alternation_never_downgrades():
    """ok(fire)/dead alternation x8: EXTREME throughout, zero extra sends.
    Ported from scratch_review/attack_false_clear.py (ATTACK 1d)."""
    stack = Stack()
    stack.escalate_to_extreme([KEY_A, KEY_B])
    base = _sends(stack)
    for k in range(8):
        stack.feed.mode = "dead" if k % 2 == 0 else "ok"
        res = stack.cycle()
        st = stack.engine.states()
        assert st[KEY_A].state is AlertState.EXTREME_24H
        assert st[KEY_B].state is AlertState.EXTREME_24H
        assert not [e for e in res.events if e.state is AlertState.CLEARED]
    assert _sends(stack) == base


# ===========================================================================
# ATTACK 1e — incident renamed mid-outage (fusion id changes while blind)
# VERIFIER FINDING (major), now fixed by the all-clear suppression gate.
# ===========================================================================

def test_incident_renamed_mid_outage_new_id_escalates_before_old_clears():
    """Feed goes blind on fire-A; when it recovers the SAME fire carries a new
    canonical id (new IRWIN, new name). Ported from
    scratch_review/attack_false_clear.py (ATTACK 1e — the verifier finding:
    the old pair's CLEARED close-out used to SEND a client-visible 'All clear
    ... for incident fire-A' one cycle after the renamed fire's EXTREME; the
    client cannot distinguish incident ids, so that read as a false all-clear
    while the fire still burned).

    Pinned invariants:
      * the renamed fire escalates on the first recovered cycle — a rename
        mid-outage must never suppress the alert;
      * the old pair does not stay frozen forever (feeds healthy again): it
        closes out through CLEARED (per-incident bookkeeping, event emitted);
      * EXTREME(new) strictly precedes CLEARED(old) — at no wall-clock instant
        does the client sit with zero active alert while the fire burns;
      * THE FIX: the CLEARED(old) client notification is SUPPRESSED because
        the same client holds an active EXTREME for the renamed incident —
        the only send after recovery is EXTREME(fire-A2). A client with an
        active alert never reads 'All clear'."""
    stack = Stack(incidents=[_incident("fire-A", "ALPHA", A_CENTER)],
                  clients=[ClientProperty(client_id="client-A", loc=A_CLIENT,
                                          name="Client A")])
    stack.escalate_to_extreme([KEY_A])
    base = _sends(stack)

    stack.feed.mode = "dead"
    for _ in range(2):
        res = stack.cycle()
        assert res.events == []
    assert stack.engine.states()[KEY_A].state is AlertState.EXTREME_24H

    # recovery under a NEW id (rename mid-outage)
    stack.feed.incidents = [_incident("fire-A2", "ALPHA REBORN", A_CENTER)]
    stack.feed.mode = "ok"

    timeline = []            # (cycle_idx, client_id, incident_id, state)
    for k in range(4):
        res = stack.cycle()
        for e in res.events:
            timeline.append((k, e.client_id, e.incident_id, e.state))

    new_key = ("client-A", "fire-A2")
    st = stack.engine.states()
    assert st[new_key].state is AlertState.EXTREME_24H, (
        "renamed fire failed to escalate — a rename mid-outage suppressed the alert")
    assert st[KEY_A].state is AlertState.CLEARED, (
        "old pair failed to close out after recovery (frozen forever)")

    ext_new = [t for t in timeline
               if t[2] == "fire-A2" and t[3] is AlertState.EXTREME_24H]
    clr_old = [t for t in timeline
               if t[2] == "fire-A" and t[3] is AlertState.CLEARED]
    assert ext_new, "no EXTREME for the renamed incident"
    assert clr_old, "no CLEARED bookkeeping event for the old incident id"
    assert ext_new[0][0] < clr_old[0][0], (
        "FALSE ALL-CLEAR: old pair CLEARED before the renamed fire's "
        "EXTREME — client had zero active alert while the fire burned")
    # THE FIX: exactly one send after recovery — the new id's EXTREME. The
    # old id's all-clear was suppressed (client still under active alert).
    assert _sends(stack) == base + 1
    post = [(e.incident_id, e.state) for e, _ in stack.notifier.sent[base:]]
    assert post == [("fire-A2", AlertState.EXTREME_24H)]
    assert all(e.state is not AlertState.CLEARED
               for e, _ in stack.notifier.sent), (
        "a client-visible 'All clear' leaked while the same fire burned "
        "under a new incident id")


# ===========================================================================
# ATTACK 2 — notification leaks: blind freeze across EVERY ladder state
# (engine-level; includes the NOTICE_72H auto-progress-to-WATCH edge)
# ===========================================================================

def _ta(cid, iid, now_s, p10_h, p50_h):
    return ThreatAssessment(
        client_id=cid, incident_id=iid,
        eta=ThreatEta(eta_p10_s=now_s + p10_h * 3600.0,
                      eta_p50_s=now_s + p50_h * 3600.0,
                      eta_p90_s=now_s + p50_h * 3600.0 + 3600.0),
        computed_at_s=now_s)


def test_blind_freeze_holds_every_state_and_never_notifies():
    """Ported from scratch_review/attack_false_clear.py (ATTACK 2)."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    t = T0
    # three pairs: NOTICE (fresh — would auto-progress to WATCH), WATCH, EXTREME.
    # Every tracked pair is assessed on every setup cycle (an absent pair with
    # healthy feeds legitimately walks toward CLEARED — that would corrupt the
    # setup, as this attack itself verifies elsewhere).
    eng.evaluate([_ta("c2", "I", t, 60.0, 60.0),
                  _ta("c3", "I", t, 10.0, 20.0)], t)         # c2 NOTICE, c3 EXTREME
    t += 300
    eng.evaluate([_ta("c1", "I", t, 60.0, 60.0),             # c1 -> NOTICE_72H
                  _ta("c2", "I", t, 60.0, 60.0),             # c2 -> WATCH
                  _ta("c3", "I", t, 10.0, 20.0)], t)         # c3 holds EXTREME
    states0 = {k: r.state for k, r in eng.states().items()}
    assert states0 == {("c1", "I"): AlertState.NOTICE_72H,
                       ("c2", "I"): AlertState.WATCH,
                       ("c3", "I"): AlertState.EXTREME_24H}
    n0 = len(notifier.sent)

    keys = set(states0)
    for k in range(3):
        ev = eng.evaluate([], t + 300 * (k + 2), blind_pairs=keys,
                          feeds_healthy=False)
        assert ev == []
    states1 = {k: r.state for k, r in eng.states().items()}
    assert states1 == states0, "a blind cycle moved a ladder state"
    assert all(r.blind_cycles == 3 for r in eng.states().values())
    assert len(notifier.sent) == n0, "blind cycle sent a client notification"
    ops = eng.pop_operator_events()
    assert len([e for e in ops if e.kind == "degraded"]) == 9   # 3 pairs x 3


def test_blind_pause_does_not_reset_clear_streak_but_needs_assessed_evals():
    """Documented pause semantics: streaks survive a freeze untouched; CLEARED
    still requires clear_cycles ASSESSED-clear evaluations in total. Ported
    from scratch_review/attack_false_clear.py (verifier finding, minor):
    clear evaluations on either side of an outage SUM toward CLEARED — the
    two clear evaluations are consecutive in ASSESSED time but not wall time.
    Contract-compliant ('clear/de-escalation streaks untouched', alerts.py
    _freeze_blind); pinned so a silent semantics change fails loudly. A
    reset-on-freeze variant would be strictly more conservative — flipping
    this is a Council policy decision, not a bug fix."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    t = T0
    eng.evaluate([_ta("c", "I", t, 10.0, 20.0)], t)            # EXTREME
    far = lambda ts: _ta("c", "I", ts, 400.0, 500.0)
    ev = eng.evaluate([far(t + 300)], t + 300)                 # assessed clear #1
    assert not [e for e in ev if e.state is AlertState.CLEARED]
    ev = eng.evaluate([], t + 600, blind_pairs={("c", "I")},
                      feeds_healthy=False)                     # freeze (pause)
    assert ev == []
    assert eng.states()[("c", "I")].state is AlertState.EXTREME_24H
    ev = eng.evaluate([far(t + 900)], t + 900)                 # assessed clear #2
    assert [e.state for e in ev
            if e.state is AlertState.CLEARED] == [AlertState.CLEARED]


# ===========================================================================
# ATTACK 2b — the all-clear suppression gate itself (engine-level unit pins
# for the ATTACK-1e fix; a client with an active alert never reads 'All clear')
# ===========================================================================

def test_all_clear_suppressed_while_same_client_other_incident_active():
    """Two incidents threaten ONE client; the first clears while the second
    stays EXTREME. The CLEARED transition/event must happen (bookkeeping),
    but the client-facing all-clear is suppressed until the LAST active pair
    clears — that final all-clear does send."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    t = T0
    eng.evaluate([_ta("c", "F1", t, 10.0, 20.0),
                  _ta("c", "F2", t, 10.0, 20.0)], t)           # both EXTREME
    assert len(notifier.sent) == 2
    far = lambda iid, ts: _ta("c", iid, ts, 400.0, 500.0)
    hot = lambda iid, ts: _ta("c", iid, ts, 10.0, 20.0)

    t += 300
    eng.evaluate([far("F1", t), hot("F2", t)], t)              # F1 clear #1
    t += 300
    ev = eng.evaluate([far("F1", t), hot("F2", t)], t)         # F1 clear #2
    assert [e.state for e in ev] == [AlertState.CLEARED]       # event emitted
    assert eng.states()[("c", "F1")].state is AlertState.CLEARED
    assert len(notifier.sent) == 2, (
        "all-clear sent while the same client held an EXTREME for F2")

    t += 300
    eng.evaluate([far("F1", t), far("F2", t)], t)              # F2 clear #1
    t += 300
    ev = eng.evaluate([far("F1", t), far("F2", t)], t)         # F2 clear #2
    assert [e.state for e in ev] == [AlertState.CLEARED]
    assert len(notifier.sent) == 3                             # final all-clear sends
    sent_event, _ = notifier.sent[-1]
    assert (sent_event.incident_id, sent_event.state) == ("F2", AlertState.CLEARED)


def test_same_cycle_escalation_mutes_same_client_all_clear():
    """CLEARED(old) and EXTREME(new) landing in the SAME evaluate() call:
    record-iteration order must not matter. Notifications go out only after
    every transition of the cycle is applied, so the suppression gate sees
    the cycle's FINAL states."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    t = T0
    eng.evaluate([_ta("c", "OLD", t, 10.0, 20.0)], t)          # OLD EXTREME (send)
    t += 300
    eng.evaluate([], t)                                        # trustworthy absence #1
    t += 300
    # Absence #2 clears OLD in the very cycle NEW first appears and escalates.
    ev = eng.evaluate([_ta("c", "NEW", t, 10.0, 20.0)], t)
    states = {e.incident_id: e.state for e in ev}
    assert states == {"OLD": AlertState.CLEARED,
                      "NEW": AlertState.EXTREME_24H}
    sent = [(e.incident_id, e.state) for e, _ in notifier.sent]
    assert ("NEW", AlertState.EXTREME_24H) in sent
    assert ("OLD", AlertState.CLEARED) not in sent, (
        "same-cycle iteration order leaked an all-clear past the gate")


def test_all_clear_for_other_client_is_never_suppressed():
    """The gate is per-CLIENT: c1 clearing while c2 burns must still send
    c1's all-clear (suppressing it would hide a genuine all-clear)."""
    cfg = ResponseConfig()
    notifier = LogNotifier()
    eng = AlertEngine(cfg, notifier=notifier)
    t = T0
    eng.evaluate([_ta("c1", "F1", t, 10.0, 20.0),
                  _ta("c2", "F2", t, 10.0, 20.0)], t)
    hot_c2 = lambda ts: _ta("c2", "F2", ts, 10.0, 20.0)
    t += 300
    eng.evaluate([hot_c2(t)], t)                               # c1 absent-healthy #1
    t += 300
    ev = eng.evaluate([hot_c2(t)], t)                          # c1 absent-healthy #2
    assert [(e.client_id, e.state) for e in ev] == [
        ("c1", AlertState.CLEARED)]
    assert [(e.client_id, e.state) for e, _ in notifier.sent[-1:]] == [
        ("c1", AlertState.CLEARED)]


# ===========================================================================
# ATTACK 3 — freeze must not block a GENUINE clear after recovery
# ===========================================================================

def test_fire_gone_during_outage_clears_with_exact_hysteresis_timing():
    """5 blind cycles frozen EXTREME; feed recovers reporting the fire GONE
    (healthy empty). CLEARED must arrive on exactly the clear_cycles-th
    post-recovery cycle — neither early (hysteresis broken) nor never
    (freeze leaked into a permanent EXTREME). Ported from
    scratch_review/attack_false_clear.py (ATTACK 3)."""
    stack = Stack(incidents=[_incident("fire-A", "ALPHA", A_CENTER)],
                  clients=[ClientProperty(client_id="client-A", loc=A_CLIENT,
                                          name="Client A")])
    stack.escalate_to_extreme([KEY_A])
    base = _sends(stack)

    stack.feed.mode = "dead"
    for _ in range(5):
        stack.cycle()
    rec = stack.engine.states()[KEY_A]
    assert rec.state is AlertState.EXTREME_24H and rec.blind_cycles == 5

    stack.feed.mode = "gone"                       # fire vanished during outage
    clear_cycles = stack.cfg.clear_cycles
    for k in range(1, clear_cycles + 1):
        res = stack.cycle()
        cleared = [e for e in res.events if e.state is AlertState.CLEARED]
        if k < clear_cycles:
            assert not cleared, f"CLEARED after only {k} healthy cycle(s)"
            if k == 1:
                assert any(e.kind == "recovered" for e in res.operator_events)
        else:
            assert [e.state for e in cleared] == [AlertState.CLEARED], (
                "freeze blocked a genuine clear after recovery")
    assert stack.engine.states()[KEY_A].state is AlertState.CLEARED
    assert _sends(stack) == base + 1               # exactly one all-clear
