"""wgresponse.alerts — the alert ladder state machine (SAFETY SEMANTICS rule 1).

Per (client_id, incident_id) pair, evaluates ThreatEta against the ladder:

    NOTICE_72H  when eta_p50 within <= cfg.notice_h   (72 h default)
    EXTREME_24H when eta_p50 within <= cfg.extreme_h  (24 h default)
                OR   eta_p10 within <= cfg.extreme_p10_h (12 h default)

NOTICE_72H is the one-time "3 days out" advance notice: it fires once and
auto-progresses to WATCH (the ongoing-monitoring state) on the next evaluation
cycle. Escalation to EXTREME_24H is immediate whenever thresholds are met
(NONE may jump straight to EXTREME for fast-moving fires). Downgrades are
hysteretic: CLEARED requires cfg.clear_cycles consecutive clear evaluations,
each with cfg.clear_margin_h of headroom beyond cfg.notice_h; EXTREME_24H ->
WATCH likewise requires cfg.clear_cycles consecutive evaluations free of both
extreme triggers with the same margin. Once the median ETA has passed while
EXTREME, the pair becomes IMPACTED (terminal).

ABSENCE IS NOT CLEARANCE (SAFETY SEMANTICS 6, Council-1 OPS-1). A tracked
pair with no assessment this cycle progresses its clear streak ONLY when the
caller vouches that the absence is trustworthy: the pair is not in
``blind_pairs`` AND ``feeds_healthy`` is True. Otherwise the pair is BLIND:
its state FREEZES (no transition, clear/de-escalation streaks untouched, no
client notification), AlertRecord.blind_cycles increments, and once
blind_cycles >= cfg.blind_alarm_cycles an OperatorEvent(kind='degraded') is
queued each cycle on the operator-event seam (pop_operator_events()) —
operator-facing only, never a client notification. When a blind pair is seen
again (assessed, or absent with healthy feeds) an OperatorEvent('recovered')
is queued once and blind_cycles resets.

All transitions are validated against contracts.ALERT_TRANSITIONS via
contracts.assert_valid_transition; every transition emits exactly one
AlertEvent and (when a notifier is configured) at most one notify attempt.
Notifier failures are logged, never raised.

A CLIENT WITH AN ACTIVE ALERT NEVER READS "ALL CLEAR" (Sprint 2, Council-1
verifier finding: an incident renamed mid-outage left a client-visible
'All clear' for the old incident id while the same fire burned under the new
id — clients cannot distinguish incident ids). Notifications are therefore
sent only AFTER every transition of the cycle has been applied, and a
CLEARED notification is SUPPRESSED (logged, never sent) whenever the same
client still holds any other pair in an active threat state (NOTICE_72H /
WATCH / EXTREME_24H). The CLEARED transition itself still happens and its
AlertEvent is still emitted/recorded — the per-incident bookkeeping and the
operator event stream are untouched; only the client-facing send is gated.
When the client's LAST active pair clears, that all-clear does send. This is
strictly conservative (suppressing a downgrade message can never hide a
threat). Sprint-2 D3 closes the comms side: every message now NAMES its
incident (name + shortened id, learned from the ``incident_meta`` evaluate
kwarg), so two overlapping fires produce distinguishable client texts, and a
median ETA that has already PASSED at transition time reads as imminent
impact ('fire projected AT the property NOW — do not approach; crews
withdrawing'), never the stale 'ensure property access' escalation wording.

CONTAINMENT GATES THE LADDER, NEVER THE CREW MATH (Sprint 2 D2, contracts
SAFETY SEMANTICS 8, default OFF). When cfg.containment_gate_enabled, an
incident whose reported containment_pct >= cfg.containment_watch_ceiling_pct
AND whose acreage has not grown for cfg.containment_no_growth_h caps that
incident's NEW ladder escalations at WATCH: NONE/CLEARED escalate at most to
NOTICE_72H, NOTICE_72H auto-progresses to WATCH, WATCH holds. Pairs already
above WATCH are untouched (never downgraded by the cap; EXTREME hysteresis /
IMPACTED terminality unchanged), and the cap lifts IMMEDIATELY on observed
acreage growth or a containment drop below the ceiling. Unknown containment
or unknown acreage NEVER caps (fail-safe: no evidence, no gate). The engine
tracks last-growth per incident off the acreage history in ``incident_meta``
(first observation initializes the no-growth clock at that evaluation, so
the gate cannot engage before containment_no_growth_h of TRACKED history);
the clock is persistence-backed via incident_growth()/restore() so a daemon
restart cannot erase it. The cap gates ONLY the alert ladder — ensembles,
P10, corridors, and every crew-safety deadline are computed elsewhere and
never read it.

PERSISTENCE SEAMS (Sprint 2 C2, contracts persist seam). The engine exposes
restore(records, notified_ledger=..., incident_growth=...) and
notified_ledger() so wgresponse.persist.StateStore can round-trip alert
state across a daemon restart: restored records resume the ladder exactly
where it stood (IMPACTED stays terminal, blind_cycles/history survive), the
notified ledger records what was ACTUALLY sent — keyed (client_id,
incident_id, state_value), appended only when the notifier reports success —
and a restart therefore re-fires NOTHING that was successfully sent for an
unchanged world while a NEW transition still notifies. The EXTREME->WATCH
de-escalation streak is engine-internal and deliberately NOT persisted:
after restore it restarts at 0, which can only DELAY a downgrade
(conservative).

RESTORED-UNSENT RECONCILE (Session-3 verifier F4, silence-ward fix). A
Ctrl-C landing inside a notifier send (KeyboardInterrupt is a BaseException:
_notify's `except Exception` deliberately lets it propagate) aborts
evaluate() AFTER the cycle's transitions were applied but BEFORE the send
was ledgered; the daemon's clean-shutdown final persist then durably records
an applied-but-never-notified state, and without this reconcile the
restarted engine would see no transition and swallow the client alert
PERMANENTLY. restore(..., notified_ledger=...) therefore flags every
restored pair whose CURRENT state is an active threat state
(NOTICE_72H/WATCH/EXTREME_24H) with no ledgered send at/after
entered_at_s, and the next evaluate() re-fires exactly that state's
notification (fresh ETAs when the pair was assessed that cycle, 'n/a'
otherwise), retrying each cycle until the notifier reports success. This
errs re-notify-ward (a duplicate beats silence — the persist-module law);
consistent record+ledger restores flag nothing, so the restart drill still
re-fires nothing. CLEARED is never re-fired (a lost downgrade message can
never hide a threat) and IMPACTED stays terminal-silent (pinned).

Units: absolute times are unix seconds (`_s`); ladder thresholds and event
ETA fields are hours (`_h`).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, Iterable, List, Optional, Tuple

from .contracts import (
    AlertEvent,
    AlertRecord,
    AlertState,
    ClientProperty,
    LatLon,
    Notifier,
    OperatorEvent,
    ResponseConfig,
    ThreatAssessment,
    assert_valid_transition,
)

logger = logging.getLogger(__name__)

_PairKey = Tuple[str, str]  # (client_id, incident_id)
_LedgerKey = Tuple[str, str, str]  # (client_id, incident_id, state_value)

# States that mean "this client currently has an active fire alert". A CLEARED
# notification for one incident is suppressed while the same client holds ANY
# other pair in one of these states (see module docstring). IMPACTED is
# deliberately excluded: it is terminal and would otherwise mute every future
# all-clear for that client forever.
_ACTIVE_THREAT_STATES = frozenset({
    AlertState.NOTICE_72H, AlertState.WATCH, AlertState.EXTREME_24H,
})


def _fmt_h(hours: float) -> str:
    """Format an hours-out value for human messages ('54h'; inf -> 'n/a')."""
    if math.isinf(hours):
        return "n/a"
    return f"{hours:.0f}h"


def _short_id(incident_id: str) -> str:
    """Shorten an incident id for human messages (ids can be 40-char IRWIN
    GUIDs). Deterministic: <= 12 chars pass through, longer ids keep their
    first 12 chars + an ellipsis — enough to distinguish concurrent fires."""
    s = str(incident_id)
    return s if len(s) <= 12 else s[:12] + "…"


class AlertEngine:
    """Alert ladder engine. cfg thresholds are hours; all timestamps unix seconds.

    Args:
        cfg: ResponseConfig (notice_h / extreme_h / extreme_p10_h /
             clear_cycles / clear_margin_h are the knobs used here).
        notifier: optional contracts.Notifier; called once per emitted
             AlertEvent. Exceptions from send() are logged and swallowed.
        clients_by_id: optional {client_id: ClientProperty} used for human
             names in messages and passed to the notifier. Unknown ids get a
             minimal placeholder ClientProperty so notification still happens.
    """

    def __init__(
        self,
        cfg: ResponseConfig,
        notifier: Optional[Notifier] = None,
        clients_by_id: Optional[Dict[str, ClientProperty]] = None,
    ) -> None:
        self._cfg = cfg
        self._notifier = notifier
        self._clients: Dict[str, ClientProperty] = dict(clients_by_id or {})
        self._records: Dict[_PairKey, AlertRecord] = {}
        # EXTREME_24H -> WATCH de-escalation streak (consecutive evals free of
        # both extreme triggers with clear_margin_h headroom). Kept engine-side
        # because AlertRecord (contracts) only carries the full-clear streak.
        self._deesc_streak: Dict[_PairKey, int] = {}
        # Operator-facing degradation events queued since the last drain
        # (SAFETY SEMANTICS 6). Drained via pop_operator_events(); the engine
        # never imports threat — the orchestrator pulls from this seam.
        self._op_events: List[OperatorEvent] = []
        # What was ACTUALLY sent (notifier reported success), keyed
        # (client_id, incident_id, state_value) -> event t_s. Persisted by
        # the watch daemon (contracts persist seam); see notified_ledger().
        self._notified: Dict[_LedgerKey, float] = {}
        # Restored pairs whose CURRENT active state has no ledgered send for
        # this occupancy (Session-3 F4): evaluate() re-fires them until the
        # notifier succeeds or the pair leaves the state. Derived purely from
        # records+ledger at restore time, so it is idempotent across
        # restarts (never persisted itself).
        self._resend_pending: set = set()
        # Per-incident acreage history for the containment gate (SAFETY
        # SEMANTICS 8): incident_id -> {"acreage": float|None,
        # "last_growth_s": unix s}. Persisted via incident_growth()/restore().
        self._growth: Dict[str, dict] = {}
        # Incident display names learned from incident_meta (D3 comms):
        # every client message names its incident, not just the pair id.
        self._incident_names: Dict[str, str] = {}

    # ------------------------------------------------------------------ API

    def evaluate(
        self,
        assessments: List[ThreatAssessment],
        now_s: float,
        blind_pairs: Optional[Iterable[_PairKey]] = None,
        feeds_healthy: bool = True,
        incident_meta: Optional[Dict[str, dict]] = None,
    ) -> List[AlertEvent]:
        """Run one evaluation cycle at unix time now_s (seconds).

        Every tracked pair is evaluated: pairs present in `assessments` against
        their ThreatEta hours-out; pairs absent while the absence is
        trustworthy (not in ``blind_pairs`` and ``feeds_healthy``) count as
        clear evaluations toward the downgrade streak (fire genuinely gone
        must still CLEAR). Pairs absent because a provider was blind this
        cycle (listed in ``blind_pairs``, or any fire feed unhealthy) FREEZE:
        no transition, streaks untouched, blind_cycles increments, and the
        degradation is queued on the operator seam (SAFETY SEMANTICS 6).

        ``incident_meta`` (Sprint 2 D2/D3, additive): optional
        {incident_id: {"name", "containment_pct", "acreage", ...}} snapshot
        of this cycle's active incidents. It feeds (a) incident naming in
        every client message and (b) the SAFETY SEMANTICS 8 containment gate
        — acreage history updates the per-incident no-growth clock every
        call, and when cfg.containment_gate_enabled an incident at/above
        cfg.containment_watch_ceiling_pct with no growth for
        cfg.containment_no_growth_h has its NEW escalations capped at WATCH
        (pairs already above WATCH untouched; the cap lifts immediately on
        growth or containment drop; unknown containment/acreage never caps).
        The cap never touches crew-safety math — nothing here does.

        The 2-argument form (defaults: no blind pairs, healthy feeds, no
        meta) keeps the pre-Sprint-2 behavior for legacy callers.
        Returns the AlertEvents emitted this cycle (at most one per pair).
        """
        capped_incidents = self._update_incident_meta(incident_meta, now_s)
        latest = self._dedupe(assessments)
        blind_keys = {tuple(k) for k in (blind_pairs or ())}
        for key, a in latest.items():
            if key not in self._records:
                self._records[key] = AlertRecord(
                    client_id=key[0], incident_id=key[1], entered_at_s=now_s
                )
                self._deesc_streak[key] = 0
        events: List[AlertEvent] = []
        for key, rec in self._records.items():
            a = latest.get(key)
            if a is None and (key in blind_keys or not feeds_healthy):
                self._freeze_blind(key, rec, now_s)
                continue
            if rec.blind_cycles > 0:
                # Pair is visible again (assessed, or trustworthy absence):
                # emit exactly one recovery event, then evaluate normally.
                self._op_events.append(OperatorEvent(
                    t_s=now_s, kind="recovered",
                    message=(f"pair ({key[0]}, {key[1]}) assessed again after "
                             f"{rec.blind_cycles} blind cycle(s); ladder resumed "
                             f"from {rec.state.value}"),
                    incident_id=key[1],
                    detail={"client_id": key[0],
                            "blind_cycles": rec.blind_cycles,
                            "state": rec.state.value},
                ))
                rec.blind_cycles = 0
            new_state, p10_h, p50_h = self._step(
                key, rec, a, now_s, capped=key[1] in capped_incidents)
            rec.last_eval_s = now_s
            if new_state is not None and new_state != rec.state:
                events.append(self._transition(rec, new_state, now_s, p10_h, p50_h))
        # Notify only after EVERY transition of this cycle has been applied,
        # so the CLEARED suppression gate sees the cycle's final states (an
        # EXTREME escalating later in the same cycle must still mute a
        # same-client all-clear sent "earlier" in iteration order).
        for event in events:
            self._notify(event)
        # Session-3 F4: re-fire restored applied-but-never-sent active states
        # (see module docstring, RESTORED-UNSENT RECONCILE). After the normal
        # notify loop so a pair that legitimately transitioned this cycle is
        # already ledgered and drops out of the pending set.
        self._flush_restored_unnotified(latest, now_s)
        return events

    def states(self) -> Dict[_PairKey, AlertRecord]:
        """Current records keyed by (client_id, incident_id).

        Values are the live AlertRecord dataclasses (serializable via
        dataclasses.asdict; timestamps unix seconds).
        """
        return dict(self._records)

    def pop_operator_events(self) -> List[OperatorEvent]:
        """Drain queued operator-facing degradation/recovery events.

        The orchestrator (threat.ThreatMonitor) calls this once per cycle and
        surfaces the events on CycleResult.operator_events. These are NEVER
        client notifications (SAFETY SEMANTICS 6)."""
        out = self._op_events
        self._op_events = []
        return out

    def notified_ledger(self) -> Dict[_LedgerKey, float]:
        """What was ACTUALLY sent (contracts persist seam).

        {(client_id, incident_id, state_value): event t_s} — an entry exists
        only when the notifier reported a successful delivery (send() True);
        failed/raised/suppressed sends are never ledgered, so a later cycle
        may retry them. Returns a copy; times unix seconds."""
        return dict(self._notified)

    def incident_growth(self) -> Dict[str, dict]:
        """Per-incident acreage/no-growth clock for the containment gate
        (SAFETY SEMANTICS 8): {incident_id: {"acreage": float|None,
        "last_growth_s": unix s}}. Persisted by the watch daemon so a restart
        cannot reset the cfg.containment_no_growth_h clock. Returns a copy."""
        return {iid: dict(g) for iid, g in self._growth.items()}

    def restore(
        self,
        records: Dict[_PairKey, AlertRecord],
        notified_ledger: Optional[Dict[_LedgerKey, float]] = None,
        incident_growth: Optional[Dict[str, dict]] = None,
    ) -> None:
        """Replace engine state with persisted state (contracts persist seam).

        ``records`` is {(client_id, incident_id): AlertRecord} exactly as
        states() returned it (persist.StateStore round-trips the shapes).
        Restored pairs resume the ladder where they stood: IMPACTED stays
        terminal, blind_cycles/clear_streak/history survive, and the next
        evaluate() of an unchanged world re-fires NOTHING that the restored
        ledger says was already sent (no transition => no event => no
        notification). The EXTREME->WATCH de-escalation streak restarts at 0
        (not persisted; can only DELAY a downgrade — conservative). Entries
        that are not AlertRecord-shaped are skipped LOUDLY, never raised — a
        partially-corrupt restore must not kill the daemon (the skipped pair
        re-tracks fresh, erring re-notify-ward).

        RESTORED-UNSENT RECONCILE (Session-3 F4): when ``notified_ledger`` is
        given, every restored pair whose current state is an active threat
        state (NOTICE_72H/WATCH/EXTREME_24H) with no ledgered send at/after
        its entered_at_s is flagged for re-notification on the next
        evaluate() — the shutdown-during-send window otherwise swallows that
        client alert permanently (see module docstring). A ledger lost to
        corruption therefore re-fires the active states it covered: one
        duplicate message, never silence.
        """
        new_records: Dict[_PairKey, AlertRecord] = {}
        for key, rec in (records or {}).items():
            if not isinstance(rec, AlertRecord):
                logger.error(
                    "restore: skipping non-AlertRecord state for %r (%s); "
                    "that pair re-tracks fresh", key, type(rec).__name__)
                continue
            new_records[(rec.client_id, rec.incident_id)] = rec
        self._records = new_records
        self._deesc_streak = {key: 0 for key in new_records}
        self._resend_pending = set()
        if notified_ledger is not None:
            self._notified = {
                (str(k[0]), str(k[1]), str(k[2])): float(t)
                for k, t in notified_ledger.items()
            }
            for key, rec in new_records.items():
                if rec.state not in _ACTIVE_THREAT_STATES:
                    continue          # CLEARED/IMPACTED/NONE never re-fire
                led_t = self._notified.get((rec.client_id, rec.incident_id,
                                            rec.state.value))
                if led_t is None or led_t < rec.entered_at_s:
                    self._resend_pending.add(key)
            if self._resend_pending:
                logger.warning(
                    "restore: %d restored pair(s) hold an ACTIVE alert state "
                    "with no ledgered send for that occupancy — the previous "
                    "process was likely interrupted mid-notification; the "
                    "next evaluate() re-fires them (re-notify-ward): %s",
                    len(self._resend_pending),
                    ", ".join(f"({k[0]}, {k[1]})"
                              for k in sorted(self._resend_pending)))
        if incident_growth is not None:
            restored: Dict[str, dict] = {}
            for iid, g in incident_growth.items():
                try:
                    acr = g.get("acreage")
                    restored[str(iid)] = {
                        "acreage": float(acr) if acr is not None else None,
                        "last_growth_s": float(g["last_growth_s"]),
                    }
                except Exception:
                    logger.error("restore: skipping malformed growth entry "
                                 "for incident %r", iid)
            self._growth = restored
        logger.info(
            "alert engine restored: %d pair(s), %d ledgered send(s), "
            "%d growth-tracked incident(s)",
            len(self._records), len(self._notified), len(self._growth))

    # ------------------------------------------------------------- internals

    def _update_incident_meta(self, incident_meta: Optional[Dict[str, dict]],
                              now_s: float) -> set:
        """Ingest one cycle's incident snapshot; return the capped incident set.

        ALWAYS (even with the gate off) records incident display names (D3
        comms) and advances the per-incident acreage/no-growth clock, so
        enabling cfg.containment_gate_enabled later starts from real history.
        Growth semantics (SAFETY SEMANTICS 8): the FIRST observation of an
        incident (or its first non-None acreage after unknowns) initializes
        last_growth_s = now_s — the gate cannot engage before
        cfg.containment_no_growth_h of tracked history; any later acreage
        INCREASE re-stamps last_growth_s (an acreage correction downward then
        back up reads as growth — ambiguity lifts the cap, the fail-safe
        direction). Returns the incident ids whose NEW escalations are capped
        at WATCH this cycle: gate enabled AND reported containment >=
        cfg.containment_watch_ceiling_pct AND known acreage with no growth
        for cfg.containment_no_growth_h. Unknown containment or unknown
        acreage never caps."""
        if not incident_meta:
            return set()
        cfg = self._cfg
        capped: set = set()
        for iid, meta in incident_meta.items():
            if not isinstance(meta, dict):
                continue
            name = meta.get("name")
            if name:
                self._incident_names[str(iid)] = str(name)
            raw = meta.get("acreage")
            acreage = float(raw) if isinstance(raw, (int, float)) else None
            g = self._growth.get(iid)
            if g is None:
                g = {"acreage": acreage, "last_growth_s": float(now_s)}
                self._growth[iid] = g
            else:
                prev = g.get("acreage")
                if acreage is not None and prev is None:
                    g["last_growth_s"] = float(now_s)   # first real reading
                elif (acreage is not None and prev is not None
                        and acreage > prev + 1e-9):
                    # ANY up-tick beyond float noise is growth — contracts
                    # SS8 says "no acreage growth", literally. KNOWN
                    # CONSEQUENCE (Session-3 verifier F2, fail-safe
                    # polarity): fused acreage follows the freshest member,
                    # so two sources disagreeing by ~0.4% flip the value
                    # every cycle and the cap never engages on such fires
                    # (alerts escalate normally — safe). A relative growth
                    # epsilon is a Sprint-4 POLICY item: it needs a contracts
                    # SS8 amendment + a config knob, not a code-side guess.
                    # Pinned in tests/test_attack_s3_containment.py.
                    g["last_growth_s"] = float(now_s)   # observed growth
                if acreage is not None:
                    g["acreage"] = acreage
            if not cfg.containment_gate_enabled:
                continue
            containment = meta.get("containment_pct")
            if not isinstance(containment, (int, float)):
                continue                    # unknown containment never gates
            if float(containment) < cfg.containment_watch_ceiling_pct:
                continue
            if g.get("acreage") is None:
                continue                    # no acreage evidence, no gate
            if now_s - g["last_growth_s"] >= cfg.containment_no_growth_h * 3600.0:
                capped.add(iid)
                logger.info(
                    "containment gate: incident %s capped at WATCH "
                    "(containment %.0f%% >= %.0f%%, no growth for %.1f h)",
                    iid, float(containment),
                    cfg.containment_watch_ceiling_pct,
                    (now_s - g["last_growth_s"]) / 3600.0)
        return capped

    def _freeze_blind(self, key: _PairKey, rec: AlertRecord, now_s: float) -> None:
        """One BLIND cycle for a pair: freeze state, alarm the operator.

        SAFETY SEMANTICS 6 — absence is not clearance. The pair's alert state,
        clear_streak and de-escalation streak are untouched (it can neither
        advance toward CLEARED nor de-escalate); blind_cycles increments and,
        at/beyond cfg.blind_alarm_cycles, an OperatorEvent('degraded') is
        queued every cycle the pair stays blind. No AlertEvent is emitted and
        the notifier is never called."""
        rec.blind_cycles += 1
        rec.last_eval_s = now_s
        logger.warning(
            "pair (%s, %s) BLIND for %d cycle(s); alert state frozen at %s",
            key[0], key[1], rec.blind_cycles, rec.state.value,
        )
        if rec.blind_cycles >= self._cfg.blind_alarm_cycles:
            self._op_events.append(OperatorEvent(
                t_s=now_s, kind="degraded",
                message=(f"pair ({key[0]}, {key[1]}) unassessed for "
                         f"{rec.blind_cycles} cycle(s); alert state FROZEN at "
                         f"{rec.state.value} — absence is not clearance"),
                incident_id=key[1],
                detail={"client_id": key[0], "blind_cycles": rec.blind_cycles,
                        "state": rec.state.value},
            ))

    @staticmethod
    def _dedupe(assessments: Iterable[ThreatAssessment]) -> Dict[_PairKey, ThreatAssessment]:
        """Keep the freshest assessment (max computed_at_s) per pair."""
        latest: Dict[_PairKey, ThreatAssessment] = {}
        for a in assessments:
            key = (a.client_id, a.incident_id)
            cur = latest.get(key)
            if cur is None or a.computed_at_s >= cur.computed_at_s:
                latest[key] = a
        return latest

    def _step(
        self,
        key: _PairKey,
        rec: AlertRecord,
        a: Optional[ThreatAssessment],
        now_s: float,
        capped: bool = False,
    ) -> Tuple[Optional[AlertState], float, float]:
        """Update streaks and decide the next state (or None to hold).

        Returns (new_state_or_None, p10_h, p50_h) with hours-out relative to
        now_s (math.inf when the pair is absent / unreached).

        ``capped`` (SAFETY SEMANTICS 8): the pair's incident is containment-
        gated this cycle — NEW escalations stop at WATCH (an extreme trigger
        escalates NONE/CLEARED at most to NOTICE_72H, rides NOTICE's auto-
        progress to WATCH, and holds WATCH); everything at or above
        EXTREME_24H is evaluated exactly as uncapped (never downgraded by the
        cap; the normal hysteresis owns downgrades). Streak bookkeeping is
        IDENTICAL capped or not — extreme_now still zeroes the clear streak,
        so a lifted cap resumes from honest streaks.
        """
        cfg = self._cfg
        if rec.state is AlertState.IMPACTED:  # terminal
            return None, math.inf, math.inf

        if a is None:
            p10_h = p50_h = math.inf
            extreme_now = notice_now = impacted_now = False
            clear_now = True
            deesc_ok = True
        else:
            p10_h, p50_h, _ = a.eta.hours_out(now_s)
            extreme_now = p50_h <= cfg.extreme_h or p10_h <= cfg.extreme_p10_h
            notice_now = p50_h <= cfg.notice_h
            # Clear evaluation: margin beyond the notice threshold AND no
            # active extreme trigger (a near p10 must never count as clear).
            clear_now = (not extreme_now) and p50_h > cfg.notice_h + cfg.clear_margin_h
            # Median arrival has passed (hours_out clamps at 0).
            impacted_now = math.isfinite(a.eta.eta_p50_s) and a.eta.eta_p50_s <= now_s
            deesc_ok = (
                p50_h > cfg.extreme_h + cfg.clear_margin_h
                and p10_h > cfg.extreme_p10_h + cfg.clear_margin_h
            )

        rec.clear_streak = rec.clear_streak + 1 if clear_now else 0
        self._deesc_streak[key] = self._deesc_streak.get(key, 0) + 1 if deesc_ok else 0

        state = rec.state
        if state is AlertState.NONE:
            if extreme_now:
                if not capped:
                    return AlertState.EXTREME_24H, p10_h, p50_h
                # WATCH ceiling: enter the ladder at NOTICE when inside the
                # notice window (NONE->WATCH is an illegal transition);
                # a p10-only trigger with p50 beyond notice_h holds NONE.
                if notice_now:
                    return AlertState.NOTICE_72H, p10_h, p50_h
                return None, p10_h, p50_h
            if notice_now:
                return AlertState.NOTICE_72H, p10_h, p50_h
            return None, p10_h, p50_h

        if state is AlertState.NOTICE_72H:
            if extreme_now and not capped:
                return AlertState.EXTREME_24H, p10_h, p50_h
            if rec.clear_streak >= cfg.clear_cycles:
                return AlertState.CLEARED, p10_h, p50_h
            # One-time notice auto-progresses to ongoing monitoring (a capped
            # extreme trigger rides this same path to the WATCH ceiling).
            return AlertState.WATCH, p10_h, p50_h

        if state is AlertState.WATCH:
            if extreme_now and not capped:
                return AlertState.EXTREME_24H, p10_h, p50_h
            if rec.clear_streak >= cfg.clear_cycles:
                return AlertState.CLEARED, p10_h, p50_h
            return None, p10_h, p50_h

        if state is AlertState.EXTREME_24H:
            # Above the WATCH ceiling: the cap never touches this state
            # (never-downgrade); impact/clear/de-escalation are the normal
            # hysteresis paths regardless of the gate.
            if impacted_now:
                return AlertState.IMPACTED, p10_h, p50_h
            if rec.clear_streak >= cfg.clear_cycles:
                return AlertState.CLEARED, p10_h, p50_h
            if not extreme_now and self._deesc_streak[key] >= cfg.clear_cycles:
                return AlertState.WATCH, p10_h, p50_h
            return None, p10_h, p50_h

        if state is AlertState.CLEARED:
            if extreme_now:
                if not capped:
                    return AlertState.EXTREME_24H, p10_h, p50_h
                if notice_now:
                    return AlertState.NOTICE_72H, p10_h, p50_h
                return None, p10_h, p50_h
            if notice_now:
                return AlertState.NOTICE_72H, p10_h, p50_h
            return None, p10_h, p50_h

        return None, p10_h, p50_h  # defensive; all states handled above

    def _transition(
        self,
        rec: AlertRecord,
        new_state: AlertState,
        now_s: float,
        p10_h: float,
        p50_h: float,
    ) -> AlertEvent:
        """Apply a validated transition at now_s (unix s); emit one event.

        Notification is NOT sent here: evaluate() notifies after the whole
        cycle's transitions are applied (the CLEARED suppression gate must see
        final states — see module docstring)."""
        assert_valid_transition(rec.state, new_state)
        prev = rec.state
        rec.state = new_state
        rec.entered_at_s = now_s
        rec.history.append((now_s, new_state))
        event = AlertEvent(
            t_s=now_s,
            client_id=rec.client_id,
            incident_id=rec.incident_id,
            prev=prev,
            state=new_state,
            eta_p50_h=p50_h,
            eta_p10_h=p10_h,
            message=self._message(rec, new_state, p10_h, p50_h),
        )
        logger.info(
            "alert %s->%s client=%s incident=%s p50=%s p10=%s",
            prev.value, new_state.value, rec.client_id, rec.incident_id,
            _fmt_h(p50_h), _fmt_h(p10_h),
        )
        return event

    def _client_name(self, client_id: str) -> str:
        client = self._clients.get(client_id)
        return client.name if client and client.name else client_id

    def _incident_label(self, incident_id: str) -> str:
        """Human incident label for messages (Sprint 2 D3): the incident NAME
        (learned from incident_meta) plus a shortened id — two overlapping
        fires must be distinguishable in client texts (the Session-1 rename
        hazard: clients cannot tell raw incident ids apart). Falls back to
        'incident <short-id>' when no name has been seen."""
        name = self._incident_names.get(incident_id)
        short = _short_id(incident_id)
        if name:
            return f"{name} ({short})"
        return f"incident {short}"

    def _message(self, rec: AlertRecord, state: AlertState, p10_h: float, p50_h: float) -> str:
        """Human alert text; ETAs are hours from evaluation time.

        EVERY message names its incident via _incident_label (D3). The
        EXTREME wording is ETA-aware: a median arrival that has already
        PASSED at transition time (p50_h clamped to 0 — the forecast jumped
        past the property between cycles while the pair was not yet IMPACTED)
        reads as imminent impact ('AT the property NOW — do not approach;
        crews withdrawing'), never the stale 'ensure property access now'
        escalation text."""
        name = self._client_name(rec.client_id)
        fire = self._incident_label(rec.incident_id)
        etas = f"~{_fmt_h(p50_h)} (median), {_fmt_h(p10_h)} (pessimistic)"
        if state is AlertState.NOTICE_72H:
            return (
                f"Projected fire arrival at {name}: {etas} — {fire}. "
                "Advance notice — crews being scheduled."
            )
        if state is AlertState.WATCH:
            return (
                f"Monitoring {name} for {fire}: projected fire arrival {etas}. "
                "Under continuous watch; crews scheduled as needed."
            )
        if state is AlertState.EXTREME_24H:
            if p50_h <= 0.0:
                return (
                    f"EXTREME alert for {name} — {fire}: fire projected AT "
                    "the property NOW — do not approach; crews withdrawing."
                )
            return (
                f"EXTREME alert for {name} — {fire}: projected fire arrival "
                f"{etas}. Crew dispatch underway — ensure property access now."
            )
        if state is AlertState.CLEARED:
            return (
                f"All clear for {name}: no projected fire arrival within the "
                f"{self._cfg.notice_h:.0f}h alert window for {fire}."
            )
        if state is AlertState.IMPACTED:
            return (
                f"Projected fire arrival time has passed at {name} ({fire}). "
                "Crews are clear; post-impact assessment to follow."
            )
        return f"Alert state {state.value} for {name} — {fire}."

    def _other_active_incidents(self, client_id: str, incident_id: str) -> "List[str]":
        """Incident ids (other than incident_id) for which client_id currently
        holds a pair in an active threat state (NOTICE_72H/WATCH/EXTREME_24H)."""
        return sorted(
            key[1]
            for key, rec in self._records.items()
            if key[0] == client_id and key[1] != incident_id
            and rec.state in _ACTIVE_THREAT_STATES
        )

    def _notify(self, event: AlertEvent) -> None:
        """Send one notification per event; failures logged, never raised.

        CLEARED sends are gated: while the same client holds any OTHER pair in
        an active threat state, the client-facing all-clear is suppressed
        (clients cannot distinguish incident ids — an 'All clear' during id
        churn while the same fire burns under a new id is a false all-clear;
        see module docstring). The AlertEvent itself was already emitted and
        recorded; suppression affects the notifier call only.

        Successful sends (notifier returned True) are appended to the
        notified ledger keyed (client_id, incident_id, state_value) — the
        persist seam's record of what was ACTUALLY sent. Failed, raised, and
        suppressed sends are never ledgered (a later cycle may retry)."""
        if self._notifier is None:
            return
        if event.state is AlertState.CLEARED:
            others = self._other_active_incidents(event.client_id, event.incident_id)
            if others:
                logger.warning(
                    "SUPPRESSED client all-clear for %s/%s: client still has "
                    "active alert(s) for incident(s) %s — a client with an "
                    "active alert never reads 'All clear'",
                    event.client_id, event.incident_id, ", ".join(others),
                )
                return
        client = self._clients.get(event.client_id) or ClientProperty(
            client_id=event.client_id, loc=LatLon(0.0, 0.0), name=event.client_id
        )
        try:
            ok = self._notifier.send(event, client)
            if ok:
                self._notified[(event.client_id, event.incident_id,
                                event.state.value)] = event.t_s
            else:
                logger.warning(
                    "notifier returned False for %s/%s (%s)",
                    event.client_id, event.incident_id, event.state.value,
                )
        except Exception:
            logger.exception(
                "notifier failed for %s/%s (%s); alert state already recorded",
                event.client_id, event.incident_id, event.state.value,
            )

    def _flush_restored_unnotified(self, latest: Dict[_PairKey, ThreatAssessment],
                                   now_s: float) -> None:
        """Re-fire restored applied-but-never-sent active states (Session-3
        F4; module docstring RESTORED-UNSENT RECONCILE).

        For every pending pair still holding an active threat state whose
        current occupancy has no ledgered send (ledger t_s >= entered_at_s):
        build the state's message with THIS cycle's ETAs when the pair was
        assessed (math.inf -> 'n/a' otherwise, e.g. blind/absent — the
        already-applied transition still owes its client a message) and call
        the notifier. Success (ledgered) or leaving the active state drops
        the pair from the pending set; failure keeps it for retry next cycle.
        These re-sends are NOT ladder transitions: no AlertEvent is appended
        to evaluate()'s return, history/records are untouched, and the
        CLEARED-suppression gate is irrelevant (only active states re-fire).
        """
        if not self._resend_pending:
            return
        if self._notifier is None:
            self._resend_pending.clear()      # nothing can ever send
            return
        for key in sorted(self._resend_pending):
            rec = self._records.get(key)
            if rec is None or rec.state not in _ACTIVE_THREAT_STATES:
                self._resend_pending.discard(key)
                continue
            led_key = (rec.client_id, rec.incident_id, rec.state.value)
            led_t = self._notified.get(led_key)
            if led_t is not None and led_t >= rec.entered_at_s:
                # Sent for this occupancy (possibly by this very cycle's
                # normal transition notify) — nothing owed.
                self._resend_pending.discard(key)
                continue
            a = latest.get(key)
            if a is not None:
                p10_h, p50_h, _ = a.eta.hours_out(now_s)
            else:
                p10_h = p50_h = math.inf
            event = AlertEvent(
                t_s=now_s,
                client_id=rec.client_id,
                incident_id=rec.incident_id,
                prev=rec.state,               # not a transition: prev == state
                state=rec.state,
                eta_p50_h=p50_h,
                eta_p10_h=p10_h,
                message=self._message(rec, rec.state, p10_h, p50_h),
            )
            logger.warning(
                "re-firing unsent %s notification for (%s, %s): the state "
                "was applied before the last shutdown but never successfully "
                "sent (no ledger entry for this occupancy) — re-notify-ward",
                rec.state.value, rec.client_id, rec.incident_id)
            before = self._notified.get(led_key)
            self._notify(event)
            after = self._notified.get(led_key)
            if after is not None and after != before:
                # Ledger entry appeared/changed => THIS send succeeded. An
                # identity check, not a timestamp compare: a clock stepping
                # backwards across the restart could stamp the re-send
                # earlier than the pre-restart entered_at_s, which must not
                # read as still-unsent (worst case under such skew is one
                # duplicate per restart — re-notify-ward, never silence).
                self._resend_pending.discard(key)
            # else: send failed/raised — stays pending, retried next cycle.
