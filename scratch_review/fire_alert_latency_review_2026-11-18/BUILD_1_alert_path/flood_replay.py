"""BUILD lane 1, item 3 (SAFETY SEMANTICS 51): replay the live operator-event
flood of 2026-11-18 17:29Z on the build branch.

The live /api/state (orchestrator/live_state_2026-11-18T1729Z.json, read
only) holds 15,894 'recovered' events — one per tracked alert pair, after one
blind cycle: 15,259 pairs at none, 556 cleared, 79 impacted, over 464
incidents, none of which is on the map any more. This rebuilds exactly those
pairs in a real AlertEngine behind a real ThreatMonitor + _WatchDaemon (the
69 WFIGS fires of that cycle as the feed; no home in range, so no spread
runs) and runs: a feed-blind cycle, a healthy cycle, a second blind cycle —
with the three knobs OFF, then ON (summary + prune + priority).

Per cycle it prints alerts_s (the monitor's own phase timer), the operator
events, the daemon's /api/state bytes (server.jsonable + json.dumps, the
_send_json path), what the 100-entry operator_log and the priority rings
hold, and the records left. It also prints the live /api/state's size with
its operator_events / operator_log swapped for the replay's, so the gain is
read against the real 6.49 MB payload.

Run from the build worktree root:  python <this file> [snapshot.json]"""
import copy
import json
import os
import sys
import time

WT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, WT)
sys.path.insert(0, os.path.join(WT, "tests"))

from wgresponse import server  # noqa: E402
from wgresponse.alerts import AlertEngine  # noqa: E402
from wgresponse.cli import _WatchDaemon  # noqa: E402
from wgresponse.contracts import (AlertRecord, AlertState, BBox,  # noqa: E402
                                  FireIncident, IncidentSource, LatLon,
                                  ResponseConfig, SafeZone)
from wgresponse.persist import StateStore  # noqa: E402
from wgresponse.threat import ThreatMonitor  # noqa: E402

SNAP = (sys.argv[1] if len(sys.argv) > 1 else
        r"C:\Users\colin\wg_response\scratch_review"
        r"\fire_alert_latency_review_2026-11-18\orchestrator"
        r"\live_state_2026-11-18T1729Z.json")


class ToggleFeed:
    def __init__(self, incs):
        self.incs = incs
        self.blind = False
        self.last_fetch_ok = True

    def fetch_active(self, bbox=None):
        self.last_fetch_ok = not self.blind
        return [] if self.blind else list(self.incs)


class NoHomes:
    def protected_clients(self, bbox=None):
        return []


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def nbytes(obj):
    return len(json.dumps(server.jsonable(obj)).encode("utf-8"))


def build(snap, on, tmp):
    t_live = float(snap["now_s"])
    recs = {}
    for e in snap["operator_events"]:
        det = e.get("detail") or {}
        cid, iid, st = det["client_id"], e["incident_id"], det["state"]
        state = AlertState(st)
        hist = [] if state is AlertState.NONE else [(t_live - 86400.0, state)]
        recs[(cid, iid)] = AlertRecord(cid, iid, state,
                                       entered_at_s=t_live - 86400.0,
                                       history=hist)
    incs = [FireIncident(r["incident_id"], r.get("name") or "",
                         IncidentSource.WFIGS, LatLon(r["lat"], r["lon"]),
                         float(r.get("updated_at_s") or t_live - 600.0))
            for r in snap["incidents"] if r["source"] == "wfigs"]
    cfg = ResponseConfig(alert_recovery_summary=on,
                         alert_prune_idle_pairs=on, operator_log_priority=on)
    eng = AlertEngine(cfg)
    eng.restore(copy.deepcopy(recs), notified_ledger={
        (k[0], k[1], r.state.value): r.entered_at_s
        for k, r in recs.items() if r.state is not AlertState.NONE})
    feed = ToggleFeed(incs)
    mon = ThreatMonitor(feeds=[feed], weather_provider=None,
                        terrain_provider=None, registry=NoHomes(),
                        alert_engine=eng, cfg=cfg,
                        safe_zones=[SafeZone("z", LatLon(39.0, -105.0))])
    clock = Clock(t_live)
    d = _WatchDaemon(monitor=mon, engine=eng, registry=NoHomes(),
                     tracker=None, store=StateStore(tmp), cfg=cfg,
                     aoi=BBox(-125.0, 31.0, -102.0, 49.0), period_s=300.0,
                     clock=clock, sleep=lambda s: None)
    return d, feed, eng, clock, len(recs), len(incs)


def run(snap, on, tmp):
    d, feed, eng, clock, n_pairs, n_inc = build(snap, on, tmp)
    rows = []
    for label, blind in (("blind cycle", True), ("healthy cycle", False),
                         ("2nd blind cycle", True)):
        clock.t += 300.0
        feed.blind = blind
        t = time.perf_counter()
        res = d.run_cycle()
        wall = time.perf_counter() - t
        t = time.perf_counter()
        st = d.state()
        body = nbytes(st)
        t_state = time.perf_counter() - t
        kinds = {}
        for e in res.operator_events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        rows.append({
            "cycle": label, "alerts_s": round(res.timings["alerts_s"], 3),
            "cycle_wall_s": round(wall, 2),
            "operator_events": len(res.operator_events), "kinds": kinds,
            "op_events_bytes": nbytes(list(res.operator_events)),
            "op_log_kinds": sorted({e.kind for e in st["operator_log"]}),
            "priority_kinds": sorted({e.kind for e in
                                      st.get("operator_log_priority", [])}),
            "state_bytes": body, "state_build_s": round(t_state, 3),
            "records_left": len(eng.states()),
            "swapped_live_state_bytes": (
                LIVE_BYTES - LIVE_OPS - LIVE_LOG
                + nbytes(list(res.operator_events))
                + nbytes(st["operator_log"])
                + (nbytes(st["operator_log_priority"])
                   if "operator_log_priority" in st else 0)),
        })
    return n_pairs, n_inc, rows


if __name__ == "__main__":
    import tempfile
    snap = json.load(open(SNAP, encoding="utf-8"))
    LIVE_BYTES = os.path.getsize(SNAP)
    LIVE_OPS = len(json.dumps(snap["operator_events"]).encode("utf-8"))
    LIVE_LOG = len(json.dumps(snap["operator_log"]).encode("utf-8"))
    print(f"live /api/state {LIVE_BYTES:,} B; operator_events "
          f"{len(snap['operator_events']):,} ({LIVE_OPS:,} B); operator_log "
          f"{LIVE_LOG:,} B; live alerts_s "
          f"{snap['last_cycle_phases']['alerts_s']:.2f}")
    import logging
    # the live daemon logs at INFO to its container's stdout: log to a real
    # file here (REPLAY_NO_LOG=1 silences the logging instead)
    if os.environ.get("REPLAY_NO_LOG"):
        logging.disable(logging.WARNING)
    else:
        logging.basicConfig(
            level=logging.INFO,
            filename=os.path.join(tempfile.gettempdir(), "flood_replay.log"),
            filemode="w",
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = {}
    for on in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            n_pairs, n_inc, rows = run(snap, on, tmp)
        out["ON" if on else "OFF"] = rows
        print(f"\n=== knobs {'ON (summary + prune + priority)' if on else 'OFF'}"
              f" — {n_pairs:,} tracked pairs, {n_inc} fires on the map ===")
        for r in rows:
            print(json.dumps(r))
    with open(os.path.join(os.path.dirname(__file__),
                           "flood_replay_out.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, indent=1)
