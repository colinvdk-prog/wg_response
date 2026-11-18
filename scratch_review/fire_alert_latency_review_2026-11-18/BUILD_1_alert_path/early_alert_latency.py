"""BUILD lane 1, item 1 (alert_new_fires_early, SAFETY SEMANTICS 49): when
does a new fire's first NOTIFY go out, measured from the start of the cycle,
with the knob off and on?

World: four EXISTING fires (tracked WATCH pairs, homes 1.5 km downwind) whose
terrain fetch is slow — the stand-in for lane C's cold LFPS domains — and one
NEW fire whose incident id sorts LAST (the legacy loop reaches it last) with a
warm tile. The terrain provider sleeps COLD_S per existing-fire call and
WARM_S for the new fire; real AlertEngine, real ThreatMonitor, stub weather,
uniform grass. The notifier stamps time.monotonic() on every send.

Also measured: the cost of one mid-cycle evaluate (only_incidents) on an
engine holding the 15,894 live pairs (it walks every record once).

Run from the build worktree root:  python <this file> [COLD_S] [WARM_S]"""
import copy
import json
import os
import sys
import time

WT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "tests"))

from test_threat import (NOW, ORIGIN, StubFeed, StubRegistry,  # noqa: E402
                         StubTerrain, StubWeather, make_cfg)
from wgresponse import geo  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.contracts import (AlertRecord, AlertState,  # noqa: E402
                                  ClientProperty, FireIncident,
                                  IncidentSource, SafeZone)
from wgresponse.threat import ThreatMonitor  # noqa: E402
from tests.test_alerts import assess  # noqa: E402

COLD_S = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
WARM_S = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
SPOTS = {f"E{k}": geo.destination(ORIGIN, 90.0 * k, 150_000.0)
         for k in range(4)}
NEW = geo.destination(ORIGIN, 45.0, 300_000.0)


class SlowTerrain(StubTerrain):
    def tile(self, bbox, cell_m):
        time.sleep(WARM_S if bbox.contains(NEW) else COLD_S)
        return super().tile(bbox, cell_m)


class StampNotifier:
    def __init__(self):
        self.sent = []

    def send(self, event, client):
        self.sent.append((time.monotonic(), event.incident_id,
                          event.state.value))
        return True


def run(on):
    cfg = make_cfg(alert_new_fires_early=on, notice_h=20.0, extreme_h=2.0,
                   extreme_p10_h=1.0, clear_margin_h=1.0)
    incs = [FireIncident(iid, iid, IncidentSource.WFIGS, p, NOW - 600.0)
            for iid, p in SPOTS.items()]
    incs.append(FireIncident("zz-new", "New Fire", IncidentSource.WFIGS, NEW,
                             NOW - 60.0))
    homes = [ClientProperty(f"h-{iid}", geo.destination(p, 90.0, 1500.0))
             for iid, p in list(SPOTS.items()) + [("zz-new", NEW)]]
    note = StampNotifier()
    eng = AlertEngine(cfg, notifier=note)
    t0 = NOW - 3600.0
    eng.restore({(f"h-{iid}", iid): AlertRecord(
        f"h-{iid}", iid, AlertState.WATCH, t0,
        history=[(t0, AlertState.WATCH)]) for iid in SPOTS},
        notified_ledger={(f"h-{iid}", iid, "watch"): t0 for iid in SPOTS})
    mon = ThreatMonitor(feeds=[StubFeed(incs)], weather_provider=StubWeather(),
                        terrain_provider=SlowTerrain(),
                        registry=StubRegistry(homes), alert_engine=eng,
                        cfg=cfg, safe_zones=[SafeZone("z", ORIGIN)])
    t_start = time.monotonic()
    res = mon.cycle(NOW)
    first_new = min(t for t, iid, _ in note.sent if iid == "zz-new")
    return {"knob": on,
            "new_fire_first_notify_s": round(first_new - t_start, 3),
            "cycle_s": round(time.monotonic() - t_start, 3),
            "assess_s": round(res.timings["assess_s"], 3),
            "events": sorted((e.incident_id, e.state.value)
                             for e in res.events)}


def early_eval_cost():
    """One mid-cycle evaluate on an engine with the live 15,894 pairs."""
    snap = (r"C:\Users\colin\wg_response\scratch_review"
            r"\fire_alert_latency_review_2026-11-18\orchestrator"
            r"\live_state_2026-11-18T1729Z.json")
    if not os.path.exists(snap):
        return None
    d = json.load(open(snap, encoding="utf-8"))
    recs = {}
    for e in d["operator_events"]:
        det = e["detail"]
        recs[(det["client_id"], e["incident_id"])] = AlertRecord(
            det["client_id"], e["incident_id"], AlertState(det["state"]))
    eng = AlertEngine(make_cfg())
    eng.restore(copy.deepcopy(recs))
    out = []
    for k in range(5):
        a = [assess("new-home", "brand-new", NOW + k, 3.0)]
        t = time.perf_counter()
        eng.evaluate(a, NOW + k, blind_pairs=set(), feeds_healthy=True,
                     incident_meta={"brand-new": {"name": "x"}},
                     only_incidents={"brand-new"}, flush_unsent=False)
        out.append(time.perf_counter() - t)
    return {"records": len(eng.states()),
            "mid_cycle_evaluate_ms_median": round(
                sorted(out)[len(out) // 2] * 1000.0, 2)}


if __name__ == "__main__":
    import logging
    logging.disable(logging.WARNING)
    rows = [run(False), run(True)]
    for r in rows:
        print(json.dumps(r))
    gain = rows[0]["new_fire_first_notify_s"] - rows[1]["new_fire_first_notify_s"]
    print(json.dumps({"COLD_S": COLD_S, "WARM_S": WARM_S,
                      "gain_s": round(gain, 3),
                      "same_events": rows[0]["events"] == rows[1]["events"]}))
    cost = early_eval_cost()
    if cost:
        print(json.dumps(cost))
