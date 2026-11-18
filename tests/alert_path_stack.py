"""tests/alert_path_stack.py — shared worlds for the fire -> alert latency
build (2026-11-18, build lane 1; contracts SAFETY SEMANTICS 49-52).

A MIXED world: an EXISTING fire E with a tracked EXTREME pair (assessed, and
recovering from one blind cycle), a tracked pair whose client the registry
no longer returns (BLIND by the roster rule), a fire F whose terrain fails
(its tracked pair BLIND), a vanished fire G whose WATCH pair walks its clear
streak to CLEARED, and up to three NEW fires (N1, N2, N3) each with homes
downwind — plus optional feed blindness and a restored-unsent ledger. Every
call to the terrain provider and every notifier send lands in one shared
log, so a test can read what happened in which order."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
    BlindFeed,
    StubFeed,
    StubRegistry,
    StubTerrain,
    StubWeather,
    make_cfg,
)
from wgresponse import geo  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (  # noqa: E402
    AlertRecord,
    AlertState,
    ClientProperty,
    FireIncident,
    IncidentSource,
    SafeZone,
)
from wgresponse.threat import ThreatMonitor  # noqa: E402

N1 = geo.destination(ORIGIN, 0.0, 150_000.0)
N2 = geo.destination(ORIGIN, 270.0, 150_000.0)
N3 = geo.destination(ORIGIN, 90.0, 150_000.0)
F0 = geo.destination(ORIGIN, 180.0, 150_000.0)


def east(p, m):
    return geo.destination(p, 90.0, m)


def fire(iid, origin, name=None, source=IncidentSource.MOCK, **kw):
    return FireIncident(incident_id=iid, name=name or f"Fire {iid}",
                        source=source, origin=origin,
                        updated_at_s=NOW - 1800.0, **kw)


def clients():
    """Every client the registry returns (c-e2 is NOT: it went missing)."""
    return [ClientProperty("c-e1", east(ORIGIN, 1500.0), name="E one"),
            ClientProperty("c-f", east(F0, 1500.0), name="F one"),
            ClientProperty("c-g", geo.destination(ORIGIN, 45.0, 400_000.0),
                           name="G one"),
            ClientProperty("c-n1", east(N1, 1500.0), name="N1 near"),
            ClientProperty("c-n1b", east(N1, 9000.0), name="N1 far"),
            ClientProperty("c-n2", east(N2, 6000.0), name="N2 one"),
            ClientProperty("c-n3", east(N3, 1500.0), name="N3 one")]


class LogNotifier:
    def __init__(self, log):
        self.log = log

    def send(self, event, client):
        self.log.append(("notify", event.client_id, event.incident_id,
                         event.state.value, event.message))
        return True


class LogTerrain(StubTerrain):
    """StubTerrain that logs each call and fails for F's domain."""

    def __init__(self, log, fail_at=F0):
        super().__init__()
        self.log = log
        self.fail_at = fail_at

    def tile(self, bbox, cell_m):
        self.log.append(("terrain", round(bbox.center().lat, 3),
                         round(bbox.center().lon, 3)))
        if self.fail_at is not None and bbox.contains(self.fail_at):
            raise RuntimeError("landfire down for F")
        return super().tile(bbox, cell_m)


def ladder_cfg(**kw):
    """A short ladder so a 24 h horizon reaches every rung: EXTREME within
    2 h (p10 1 h), NOTICE within 20 h."""
    kw.setdefault("notice_h", 20.0)
    kw.setdefault("extreme_h", 2.0)
    kw.setdefault("extreme_p10_h", 1.0)
    kw.setdefault("clear_margin_h", 1.0)
    return make_cfg(**kw)


def tracked_records(t0=NOW - 3600.0):
    """The pairs tracked before the cycle (restore() shapes)."""
    return {
        ("c-e1", "E"): AlertRecord("c-e1", "E", AlertState.EXTREME_24H,
                                   entered_at_s=t0, blind_cycles=1,
                                   history=[(t0, AlertState.EXTREME_24H)]),
        ("c-e2", "E"): AlertRecord("c-e2", "E", AlertState.WATCH,
                                   entered_at_s=t0,
                                   history=[(t0, AlertState.WATCH)]),
        ("c-f", "F"): AlertRecord("c-f", "F", AlertState.NOTICE_72H,
                                  entered_at_s=t0,
                                  history=[(t0, AlertState.NOTICE_72H)]),
        ("c-g", "G"): AlertRecord("c-g", "G", AlertState.WATCH,
                                  entered_at_s=t0, clear_streak=1,
                                  history=[(t0, AlertState.WATCH)]),
        # N1's nearest home also watched a fire that is gone: its all-clear
        # must be suppressed while N1 escalates it (SEMANTICS 6 comms gate)
        ("c-n1", "G"): AlertRecord("c-n1", "G", AlertState.WATCH,
                                   entered_at_s=t0, clear_streak=1,
                                   history=[(t0, AlertState.WATCH)]),
    }


def ledger_for(records, unsent=()):
    """A notified ledger covering every active record except `unsent`."""
    out = {}
    for key, rec in records.items():
        if key in unsent:
            continue
        out[(key[0], key[1], rec.state.value)] = rec.entered_at_s
    return out


def build(cfg, new=("N1",), feed_blind=False, unsent=(), engine_cls=None):
    """(monitor, engine, log) for the mixed world."""
    log: list = []
    incs = [fire("E", ORIGIN), fire("F", F0)]
    where = {"N1": N1, "N2": N2, "N3": N3}
    incs += [fire(n, where[n]) for n in new]
    feeds = [StubFeed(incs)]
    if feed_blind:
        feeds.append(BlindFeed())
    cl = clients()
    engine = (engine_cls or AlertEngine)(
        cfg, notifier=LogNotifier(log),
        clients_by_id={c.client_id: c for c in cl})
    recs = tracked_records()
    if hasattr(engine, "restore"):
        engine.restore(copy.deepcopy(recs),
                       notified_ledger=ledger_for(recs, unsent))
    mon = ThreatMonitor(
        feeds=feeds, weather_provider=StubWeather(),
        terrain_provider=LogTerrain(log), registry=StubRegistry(cl),
        alert_engine=engine, cfg=cfg,
        safe_zones=[SafeZone("z1", geo.destination(ORIGIN, 180.0, 20_000.0))])
    return mon, engine, log


def op_rows(res):
    return [(e.kind, e.message, e.incident_id, repr(sorted(
        (e.detail or {}).items()))) for e in res.operator_events]


def snapshot(res, engine, log):
    """Everything a run may be compared on (deep copies)."""
    return {
        "events": list(res.events),
        "ops": op_rows(res),
        "notes": list(res.notes),
        "assessments": [(a.client_id, a.incident_id, a.eta.eta_p10_s,
                         a.eta.eta_p50_s) for a in res.assessments],
        "degraded": res.degraded,
        "states": copy.deepcopy(dict(engine.states())),
        "ledger": dict(engine.notified_ledger()),
        "growth": engine.incident_growth(),
        "deesc": dict(engine._deesc_streak),
        "pending": set(engine._resend_pending),
        "sends": [r for r in log if r[0] == "notify"],
    }
