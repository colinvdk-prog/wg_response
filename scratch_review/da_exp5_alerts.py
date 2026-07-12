"""EXP 5 — ALERT LADDER EDGE CASES (AlertEngine, direct drive).

(a) one client threatened by TWO incidents: are states independent, and what
    does the notification stream look like (contradictory notices)?
(b) ETA oscillating +/-1.5 h around the 24 h EXTREME threshold, and around the
    30 h de-escalation threshold: count transitions over 10 cycles.
(c) IMPACTED terminality: fire arrives, then the feed reports contained
    (pair absent) for 5 cycles.
(d) NOTICE_72H one-shot: emitted exactly once? plus the fast-fire path
    (NONE -> EXTREME directly, no NOTICE ever) and re-entry after CLEARED.

Run: py -3 scratch_review/da_exp5_alerts.py
"""
from __future__ import annotations

import math

from da_common import T0

from wgresponse.alerts import AlertEngine
from wgresponse.contracts import (AlertState, ClientProperty, LatLon, Notifier,
                                  ResponseConfig, ThreatAssessment, ThreatEta)

STEP = 900.0


class CaptureNotifier:
    def __init__(self):
        self.sent = []

    def send(self, event, client) -> bool:
        self.sent.append(event)
        return True


def ta(client, incident, now_s, p50_h, spread_h=6.0):
    p50 = now_s + p50_h * 3600.0
    return ThreatAssessment(client_id=client, incident_id=incident,
                            eta=ThreatEta(eta_p10_s=p50 - spread_h * 3600.0,
                                          eta_p50_s=p50,
                                          eta_p90_s=p50 + spread_h * 3600.0),
                            computed_at_s=now_s)


def case_a():
    print("=== (a) one client, two incidents ===")
    cfg = ResponseConfig()
    notif = CaptureNotifier()
    eng = AlertEngine(cfg, notifier=notif,
                      clients_by_id={"C1": ClientProperty("C1", LatLon(0, 0), name="Casa Uno")})
    # I1 closes fast (20h -> impact), I2 hovers at 60h then recedes
    script = [
        (0, [("I1", 20.0), ("I2", 60.0)]),
        (1, [("I1", 12.0), ("I2", 60.0)]),
        (2, [("I1", 4.0),  ("I2", 61.0)]),
        (3, [("I1", -0.5), ("I2", 62.0)]),   # I1 median arrival passed
        (4, [("I2", 80.0)]),                  # I1 contained/absent; I2 receding
        (5, [("I2", 85.0)]),
        (6, [("I2", 90.0)]),
    ]
    for k, pairs in script:
        now = T0 + k * STEP
        evs = eng.evaluate([ta("C1", iid, now, h) for iid, h in pairs], now)
        for e in evs:
            print(f"  cyc{k} [{e.incident_id}] {e.prev.value} -> {e.state.value}")
    states = {k: r.state.value for k, r in eng.states().items()}
    print(f"  final states: {states}")
    print(f"  notifications sent to client: {len(notif.sent)}")
    for e in notif.sent:
        print(f"    [{e.incident_id}] {e.state.value:>12}: {e.message[:95]}")


def count_transitions(cfg, p50_seq, spread_h, label, p10_seq=None):
    eng = AlertEngine(cfg)
    n_tr = 0
    log = []
    for k, p50_h in enumerate(p50_seq):
        now = T0 + k * STEP
        if p10_seq is not None:
            p50 = now + p50_h * 3600.0
            a = ThreatAssessment(client_id="C", incident_id="I",
                                 eta=ThreatEta(eta_p10_s=now + p10_seq[k] * 3600.0,
                                               eta_p50_s=p50, eta_p90_s=p50),
                                 computed_at_s=now)
        else:
            a = ta("C", "I", now, p50_h, spread_h)
        evs = eng.evaluate([a], now)
        n_tr += len(evs)
        for e in evs:
            log.append(f"cyc{k}({p50_h:+.1f}h): {e.prev.value}->{e.state.value}")
    st = eng.states()[("C", "I")].state.value
    print(f"  {label}: transitions={n_tr} final={st}")
    for l in log:
        print(f"     {l}")
    return n_tr


def case_b():
    print("\n=== (b) hysteresis under ETA oscillation ===")
    cfg = ResponseConfig()
    seq24 = [24.0 + (1.5 if k % 2 else -1.5) for k in range(10)]   # 22.5/25.5 alternating
    count_transitions(cfg, seq24, 4.0, "p50 oscillating 24h +/- 1.5h (10 cycles)")
    seq30 = [22.0] + [30.0 + (1.5 if k % 2 else -1.5) for k in range(10)]
    count_transitions(cfg, seq30, 4.0,
                      "enter EXTREME @22h then p50 oscillating 30h +/- 1.5h")
    seq_deesc = [22.0] + [33.0] * 10   # steady, above extreme+margin, below notice
    count_transitions(cfg, seq_deesc, 4.0,
                      "enter EXTREME @22h then steady 33h (de-escalation path)")
    # p10 oscillating around the 12h extreme_p10 trigger while p50 stays 30h
    p50s = [30.0] * 10
    p10s = [12.0 + (1.5 if k % 2 else -1.5) for k in range(10)]
    count_transitions(cfg, p50s, None, "p10 oscillating 12h +/- 1.5h (p50=30h)",
                      p10_seq=p10s)


def case_c():
    print("\n=== (c) IMPACTED terminality vs 'contained' feed ===")
    cfg = ResponseConfig()
    eng = AlertEngine(cfg)
    script = [20.0, 10.0, 2.0, -0.5]           # drive to impact
    for k, h in enumerate(script):
        now = T0 + k * STEP
        eng.evaluate([ta("C", "I", now, h)], now)
    st = eng.states()[("C", "I")].state
    print(f"  after arrival: {st.value}")
    n_after = 0
    for k in range(4, 9):                       # fire contained: pair absent
        now = T0 + k * STEP
        evs = eng.evaluate([], now)
        n_after += len(evs)
    st2 = eng.states()[("C", "I")].state
    print(f"  after 5 absent (contained) cycles: {st2.value}, extra events={n_after} "
          f"(terminal holds: {st2 is AlertState.IMPACTED and n_after == 0})")


def case_d():
    print("\n=== (d) NOTICE_72H one-shot + auto-progression ===")
    cfg = ResponseConfig()
    notif = CaptureNotifier()
    eng = AlertEngine(cfg, notifier=notif)
    for k in range(10):
        now = T0 + k * STEP
        eng.evaluate([ta("C", "I", now, 60.0)], now)      # steady 60h out
    notices = [e for e in notif.sent if e.state is AlertState.NOTICE_72H]
    watches = [e for e in notif.sent if e.state is AlertState.WATCH]
    print(f"  steady 60h x10 cycles: NOTICE events={len(notices)} "
          f"(cycle of emit: t+{(notices[0].t_s - T0)/60.0:.0f}min), "
          f"WATCH events={len(watches)} "
          f"(auto-progress at t+{(watches[0].t_s - T0)/60.0:.0f}min), total events={len(notif.sent)}")
    # fast fire: NONE -> EXTREME directly; NOTICE (the '3 days out' promise) never sent
    notif2 = CaptureNotifier()
    eng2 = AlertEngine(cfg, notifier=notif2)
    for k, h in enumerate([20.0, 15.0]):
        eng2.evaluate([ta("C", "I", T0 + k * STEP, h)], T0 + k * STEP)
    kinds = [e.state.value for e in notif2.sent]
    print(f"  fast fire (first seen at 20h): events={kinds} "
          f"(NOTICE skipped: {'notice_72h' not in kinds})")
    # re-entry: NOTICE -> WATCH -> CLEARED -> NOTICE again (second '3 days out')
    notif3 = CaptureNotifier()
    eng3 = AlertEngine(cfg, notifier=notif3)
    seq = [60.0, 60.0, 90.0, 90.0, 90.0, 60.0, 60.0]
    for k, h in enumerate(seq):
        eng3.evaluate([ta("C", "I", T0 + k * STEP, h)], T0 + k * STEP)
    kinds = [e.state.value for e in notif3.sent]
    print(f"  recede-then-return: events={kinds}")


if __name__ == "__main__":
    case_a()
    case_b()
    case_c()
    case_d()
