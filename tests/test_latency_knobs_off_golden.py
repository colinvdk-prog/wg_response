"""tests/test_latency_knobs_off_golden.py — every fire -> alert latency knob
OFF is byte-identical to the build's base (2026-11-18, build lane 1;
contracts SAFETY SEMANTICS 49-52 and the cycle_step_timings,
stale_gate_missing_time, fuse_name_join_strict, notify_urgent_first knobs).

One mixed world where each knob would bite — an existing fire with assessed,
roster-blind, provider-blind and absent-clearing pairs, three new fires, a
point-only agency record that FIRMS heat joins, a FIRMS-only cluster, a
record whose update time is missing, two same-named records of different
IRWIN ids 2 km apart (the older one contained), a feed-blind second cycle
and a restored-unsent ledger — is run for three cycles with the default
config. The digest of everything a cycle decides (events, sends in their
order, operator events, notes, assessments, incidents, terrain calls in
their order, the engine's records, ledger and growth clocks) is pinned to
the value computed on commit 9d11793, before any of these knobs existed.

The world builder uses only APIs that exist on 9d11793 (the file runs there
unchanged: `python tests/test_latency_knobs_off_golden.py` prints it)."""
from __future__ import annotations

import copy
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_threat import (  # noqa: E402
    NOW,
    ORIGIN,
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
    LatLon,
    SafeZone,
)
from wgresponse.threat import ThreatMonitor  # noqa: E402

#: computed on commit 9d11793 (the base of the latency build)
GOLDEN = "683bbeffbf594b1eb74a03de5c47118eccce8dfb5783db3991eb535cce48e2a2"

N1 = geo.destination(ORIGIN, 0.0, 150_000.0)
N2 = geo.destination(ORIGIN, 270.0, 150_000.0)
N3 = geo.destination(ORIGIN, 90.0, 150_000.0)
F0 = geo.destination(ORIGIN, 180.0, 150_000.0)
P0 = geo.destination(ORIGIN, 315.0, 300_000.0)      # point + heat
H0 = geo.destination(ORIGIN, 135.0, 300_000.0)      # FIRMS-only heat
M0 = geo.destination(ORIGIN, 225.0, 300_000.0)      # missing update time
J0 = geo.destination(ORIGIN, 45.0, 300_000.0)       # name-join collision


def east(p, m):
    return geo.destination(p, 90.0, m)


def _fire(iid, origin, **kw):
    kw.setdefault("source", IncidentSource.WFIGS)
    kw.setdefault("updated_at_s", NOW - 1800.0)
    kw.setdefault("irwin_id", iid)
    kw.setdefault("source_ids", {"wfigs": iid})
    return FireIncident(incident_id=iid, name=kw.pop("name", f"Fire {iid}"),
                        origin=origin, **kw)


def _cluster(cid, pts, t):
    return FireIncident(
        incident_id=cid, name="FIRMS cluster", source=IncidentSource.FIRMS,
        origin=LatLon(sum(p.lat for p in pts) / len(pts),
                      sum(p.lon for p in pts) / len(pts)),
        updated_at_s=t, hotspots=list(pts), source_ids={"firms": cid},
        meta={"n_detections": len(pts), "hotspot_t_s": [t] * len(pts)})


def incidents():
    north = geo.destination(P0, 0.0, 2000.0)
    return [
        _fire("E", ORIGIN), _fire("F", F0), _fire("N1", N1), _fire("N2", N2),
        _fire("N3", N3), _fire("P", P0),
        _cluster("firms:p", [north, east(north, 375.0)], NOW - 4 * 3600.0),
        _cluster("firms:h", [geo.destination(H0, 0.0, 500.0)],
                 NOW - 2 * 3600.0),
        _fire("M", M0, updated_at_s=0.0, discovered_at_s=NOW - 5400.0),
        _fire("J-old", J0, name="Junction Fire", containment_pct=100.0,
              updated_at_s=NOW - 40 * 3600.0,
              discovered_at_s=NOW - 30 * 86400.0,
              meta={"contained_at_s": NOW - 60 * 3600.0}),
        _fire("J-new", geo.destination(J0, 0.0, 2000.0),
              name="Junction Fire", discovered_at_s=NOW - 3600.0),
    ]


def clients():
    out = [ClientProperty("c-e1", east(ORIGIN, 1500.0), name="E one"),
           ClientProperty("c-f", east(F0, 1500.0), name="F one"),
           ClientProperty("c-g", geo.destination(ORIGIN, 45.0, 600_000.0)),
           ClientProperty("c-n1", east(N1, 1500.0), name="N1 near"),
           ClientProperty("c-n1b", east(N1, 9000.0), name="N1 far"),
           ClientProperty("c-n2", east(N2, 6000.0), name="N2 one"),
           ClientProperty("c-n3", east(N3, 1500.0), name="N3 one"),
           ClientProperty("c-p", east(P0, 1500.0), name="P one"),
           ClientProperty("c-h", east(H0, 1500.0), name="H one"),
           ClientProperty("c-m", east(M0, 1500.0), name="M one"),
           ClientProperty("c-j", east(geo.destination(J0, 0.0, 2000.0),
                                      1500.0), name="J one")]
    return out


class SwitchFeed:
    """Healthy, then blind (last_fetch_ok False, nothing), then healthy."""

    def __init__(self, incs):
        self.incs = incs
        self.k = 0
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        self.k += 1
        self.last_fetch_ok = self.k != 2
        return list(self.incs) if self.last_fetch_ok else []


class LogNotifier:
    def __init__(self, log):
        self.log = log

    def send(self, event, client):
        self.log.append(("notify", event.client_id, event.incident_id,
                         event.state.value, event.message))
        return True


class LogTerrain(StubTerrain):
    def __init__(self, log):
        super().__init__()
        self.log = log

    def tile(self, bbox, cell_m):
        self.log.append(("terrain", round(bbox.west, 5), round(bbox.south, 5),
                         round(bbox.east, 5), round(bbox.north, 5), cell_m))
        if bbox.contains(F0):
            raise RuntimeError("landfire down for F")
        return super().tile(bbox, cell_m)


def records():
    t0 = NOW - 3600.0
    return {
        ("c-e1", "E"): AlertRecord("c-e1", "E", AlertState.EXTREME_24H, t0,
                                   blind_cycles=1,
                                   history=[(t0, AlertState.EXTREME_24H)]),
        ("c-e2", "E"): AlertRecord("c-e2", "E", AlertState.WATCH, t0,
                                   history=[(t0, AlertState.WATCH)]),
        ("c-f", "F"): AlertRecord("c-f", "F", AlertState.NOTICE_72H, t0,
                                  history=[(t0, AlertState.NOTICE_72H)]),
        ("c-g", "G"): AlertRecord("c-g", "G", AlertState.WATCH, t0,
                                  clear_streak=1,
                                  history=[(t0, AlertState.WATCH)]),
        ("c-n1", "G"): AlertRecord("c-n1", "G", AlertState.WATCH, t0,
                                   clear_streak=1,
                                   history=[(t0, AlertState.WATCH)]),
        ("c-z", "Z"): AlertRecord("c-z", "Z"),
    }


def world(cfg):
    log: list = []
    cl = clients()
    eng = AlertEngine(cfg, notifier=LogNotifier(log),
                      clients_by_id={c.client_id: c for c in cl})
    recs = records()
    ledger = {(k[0], k[1], r.state.value): r.entered_at_s
              for k, r in recs.items() if r.state is not AlertState.NONE
              and k != ("c-e1", "E")}
    eng.restore(copy.deepcopy(recs), notified_ledger=ledger)
    mon = ThreatMonitor(
        feeds=[SwitchFeed(incidents())], weather_provider=StubWeather(),
        terrain_provider=LogTerrain(log), registry=StubRegistry(cl),
        alert_engine=eng, cfg=cfg,
        safe_zones=[SafeZone("z1", geo.destination(ORIGIN, 180.0,
                                                   20_000.0))])
    return mon, eng, log


def _num(x):
    if isinstance(x, float):
        return "inf" if math.isinf(x) else repr(round(x, 6))
    return repr(x)


def digest(cfg) -> str:
    mon, eng, log = world(cfg)
    h = hashlib.sha256()
    for k in range(3):
        res = mon.cycle(NOW + 300.0 * k)
        parts = [
            [(e.client_id, e.incident_id, e.prev.value, e.state.value,
              _num(e.eta_p10_h), _num(e.eta_p50_h), e.message)
             for e in res.events],
            [(o.kind, o.incident_id, o.message,
              sorted((str(a), repr(b)) for a, b in (o.detail or {}).items()))
             for o in res.operator_events],
            list(res.notes),
            [(a.client_id, a.incident_id, _num(a.eta.eta_p10_s),
              _num(a.eta.eta_p50_s), _num(a.eta.eta_p90_s))
             for a in res.assessments],
            [(i.incident_id, i.name, _num(i.updated_at_s),
              len(i.hotspots or []), i.containment_pct)
             for i in res.incidents],
            res.degraded,
            sorted((k2, r.state.value, _num(r.entered_at_s),
                    _num(r.last_eval_s), r.clear_streak, r.blind_cycles,
                    [(_num(t), s.value) for t, s in r.history])
                   for k2, r in eng.states().items()),
            sorted((k2, _num(t)) for k2, t in eng.notified_ledger().items()),
            sorted((k2, repr(sorted(g.items())))
                   for k2, g in eng.incident_growth().items()),
            list(log),
        ]
        h.update(repr(parts).encode("utf-8"))
        log.clear()
    return h.hexdigest()


def golden_cfg():
    return make_cfg(fuse_hotspots=True, notice_h=20.0, extreme_h=2.0,
                    extreme_p10_h=1.0, clear_margin_h=1.0)


def test_every_latency_knob_off_is_the_base_cycle_byte_for_byte():
    assert digest(golden_cfg()) == GOLDEN


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)
    print(digest(golden_cfg()))
