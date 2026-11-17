"""Lane C probes P4-P6 (scratch_review/crew_program_review_2026-11-11/
C_liveops/probe_beacons.py), AFTER, on the same live stack over stub feeds
and the REAL HTTP server (127.0.0.1), with a crew roster and the SAFETY
SEMANTICS 45 knobs armed (crew_watch + crew_hold_worked). P1-P3 (bad
timestamps) were closed by SEMANTICS 44 (ba1f21a) and are pinned in
tests/test_crew_reports.py.

  P4  a ghost crew and a spoofed "off duty" under the open demo: refused.
  P5  G1 reports deploying at c1 (no client_id: the one home within 150 m):
      c1 is held for G1, not sent to G2.
  P6  the wind rises to 18 m/s while G1 works c1: the watch's level and its
      operator events, cycle by cycle.

Run from the repo root: python scratch_review/live_crew_groundwork/after_probe_beacons.py"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("WGRESP_STATE_S3", None)
os.environ.pop("WGRESP_API_KEY", None)
os.environ["WGRESP_DEV_OPEN"] = "1"

import seeded_stack as ss  # noqa: E402
from wgresponse import crews as crews_mod, server as server_mod  # noqa: E402
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon  # noqa: E402

T0 = ss.T0
C1 = ss.NEAR_LOC
tmp = tempfile.mkdtemp(prefix="laneC_after_")
rp = Path(tmp) / "roster.json"
rp.write_text(json.dumps([
    {"crew_id": "G1", "key_sha256": crews_mod.key_sha256("g1-key")},
    {"crew_id": "G2", "key_sha256": crews_mod.key_sha256("g2-key")}]),
    encoding="utf-8")
roster = crews_mod.CrewRoster(rp)
cfg = ss.small_cfg(crew_watch=True, crew_hold_worked=True)
tr = crews_mod.CrewTracker(cfg, roster=roster)
clock = ss.SettableClock(T0)
d, m, feed, eng, notif, store, wx, ter = ss.make_stack(
    tmp, cfg=cfg, clock=clock, tracker=tr, test_world=False)
httpd = server_mod.serve(port=0, host="127.0.0.1", state_provider=d.state,
                         beacon_sink=tr, plan_provider=d.plan)
httpd.app.crew_roster = roster
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def post(obj, key):
    req = urllib.request.Request(
        base + "/api/beacon", data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json", "X-Beacon-Key": key},
        method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def plan_str(res):
    p = res.plan
    if p is None:
        return "None"
    return (str({c: [(s.client_id, round((s.arrive_s - T0) / 60, 1))
                     for s in cp.stops] for c, cp in sorted(p.plans.items())})
            + " unserved=" + str([(u.client_id, u.reason) for u in p.unserved]))


def report(cid, loc, t, st=CrewStatus.AVAILABLE):
    # stub time is not the wall clock the server checks t_s against: the
    # tracker is fed directly once the HTTP path has been shown
    tr.ingest(CrewBeacon(crew_id=cid, loc=loc, t_s=t, status=st))


print("=== P4 ghost crew + spoofed off duty (open demo, roster armed) ===")
now = time.time()
print("  G1 with its own key ->", post({"crew_id": "G1", "lat": 39.10,
                                        "lon": -121.05, "t_s": now}, "g1-key"))
print("  ghost X ->", post({"crew_id": "X", "lat": C1.lat,
                            "lon": C1.lon - 0.005, "t_s": now}, "g1-key"))
print("  spoof G1 off_duty with G2's key ->", post(
    {"crew_id": "G1", "lat": 39.10, "lon": -121.05, "t_s": now + 1,
     "status": "off_duty"}, "g2-key"))
print("  tracker:", [(c.crew_id, c.status.value) for c in tr.crews()])

print("\n=== P5 crew DEPLOYING at c1: held for it, not sent to G2 ===")
tr2 = crews_mod.CrewTracker(cfg, roster=roster)
d, m, feed, eng, notif, store, wx, ter = ss.make_stack(
    tempfile.mkdtemp(prefix="laneC_after_"), cfg=cfg, clock=clock,
    tracker=tr2, test_world=False)
tr = tr2
report("G1", LatLon(39.10, -121.05), T0 + 30)
report("G2", LatLon(39.12, -121.06), T0 + 30)
clock.t = T0 + 60
res = d.run_cycle()
print("  cycle A:", plan_str(res))
report("G1", C1, T0 + 2400, CrewStatus.DEPLOYING)
report("G2", LatLon(39.12, -121.06), T0 + 2400)
clock.t = T0 + 2460
res = d.run_cycle()
print("  cycle B (G1 reports deploying AT c1):", plan_str(res))
print("  notes mentioning G1:", [n[:120] for n in res.notes if "G1" in n])
print("  operator events:", [(o.kind, o.detail.get("crew_id"))
                             for o in res.operator_events])

print("\n=== P6 the deploying crew's home falls inside the egress buffer ===")
wx.wind_mps = 18.0
for mins in (50, 60, 70, 80):
    report("G1", C1, T0 + mins * 60 - 30, CrewStatus.DEPLOYING)
    report("G2", LatLon(39.12, -121.06), T0 + mins * 60 - 30)
    clock.t = T0 + mins * 60
    res = d.run_cycle()
    p10 = [a.eta.eta_p10_s for a in res.assessments if a.client_id == "c1"]
    lead = (p10[0] - clock.t) / 60 if p10 else None
    (row,) = [r for r in res.crew_watch["crews"] if r["crew_id"] == "G1"]
    print(f"  t=+{mins}min c1 P10 lead={lead:.1f} min | plan: {plan_str(res)}"
          f" | watch G1: {row['level']} (spot {row['spot']['margin_min']}, "
          f"home {row['home'] and row['home']['margin_min']}, way out "
          f"{row['way_out']['margin_min']}) | op events: "
          f"{[o.kind for o in res.operator_events]}")
httpd.shutdown()
