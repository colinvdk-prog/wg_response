"""Lane C trace (scratch_review/crew_program_review_2026-11-11/C_liveops/
trace_live_crew.py), AFTER: the same production watch wiring over stub feeds
and the REAL HTTP server, with every SAFETY SEMANTICS 45 piece armed —
crew_watch, crew_hold_worked, crew_state_persist, the October laws
(--crew-laws), and a crew ROSTER (G1 with its own key). Since SEMANTICS 44 a
report needs a key, so this trace sends G1's own key.

Run from the repo root: python scratch_review/live_crew_groundwork/after_trace_live_crew.py"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("WGRESP_STATE_S3", None)
os.environ.pop("WGRESP_API_KEY", None)
os.environ["WGRESP_DEV_OPEN"] = "1"
os.environ["WGRESP_BEACON_KEY"] = "shared-key"     # SEM 44 (now unused)

import seeded_stack as ss  # noqa: E402
from wgresponse import cli, crews as crews_mod, server as server_mod  # noqa: E402

tmp = tempfile.mkdtemp(prefix="laneC_after_")
roster_path = Path(tmp) / "roster.json"
roster_path.write_text(json.dumps([
    {"crew_id": "G1", "name": "Engine 1", "members": 3,
     "key_sha256": crews_mod.key_sha256("g1-key")},
    {"crew_id": "G2", "name": "Engine 2",
     "key_sha256": crews_mod.key_sha256("g2-key")}]), encoding="utf-8")
os.environ["WGRESP_CREW_ROSTER"] = str(roster_path)

T0 = ss.T0
args = cli._build_parser().parse_args(cli._normalize_argv(
    ["watch", "--aoi", "-121.2,38.8,-120.8,39.2", "--crew-watch",
     "--hold-worked-homes", "--persist-crews", "--crew-laws"]))
cfg, notes = cli._crew_program_cfg(ss.small_cfg(), args)
roster, rnotes = cli._watch_roster()
print("startup notes:")
for n in notes + rnotes:
    print("  -", n[:150] + ("..." if len(n) > 150 else ""))
tracker = crews_mod.CrewTracker(cfg, roster=roster)
clock = ss.SettableClock(T0)
daemon, monitor, feed, engine, notifier, store, weather, terrain = \
    ss.make_stack(tmp, cfg=cfg, clock=clock, tracker=tracker,
                  test_world=False)
httpd = server_mod.serve(port=0, host="127.0.0.1",
                         state_provider=daemon.state, beacon_sink=tracker,
                         plan_provider=daemon.plan)
httpd.app.crew_roster = roster
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def post(path, obj, key):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json", "X-Beacon-Key": key},
        method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(path):
    with urllib.request.urlopen(base + path) as r:
        return json.loads(r.read())


def show(tag, res):
    p = res.plan
    print(f"\n== {tag}: t=+{(res.t_s - T0) / 60:.0f} min")
    if p is None:
        print("  plan: None")
    else:
        for cid, cp in sorted(p.plans.items()):
            print(f"  crew {cid} egress={cp.egress_zone_id} stops=" +
                  str([(s.client_id, round((s.arrive_s - T0) / 60, 1),
                        round(s.margin_h, 2)) for s in cp.stops]))
        print("  unserved:", [(u.client_id, u.reason) for u in p.unserved])
    cw = getattr(res, "crew_watch", None)
    if cw is not None:
        print("  crew watch:", [(r["crew_id"], r["status"], r["level"],
                                 r.get("margin_min")) for r in cw["crews"]])
    print("  crew operator events:", [(o.kind, o.detail.get("crew_id"))
                                      for o in res.operator_events
                                      if o.kind.startswith("crew_")])


st = get("/api/state")
print("\n/api/state before any report: crews",
      [(c["crew_id"], c["name"], c["status"]) for c in st["crews"]],
      "| crew_roster", st["crew_roster"])
clock.t = T0 + 60
show("cycle 1, no crew has reported", daemon.run_cycle())

print("\nPOST G1 with G2's key ->", post("/api/beacon", {
    "crew_id": "G1", "lat": 39.10, "lon": -121.05, "t_s": T0 + 300}, "g2-key"))
print("POST ghost X ->", post("/api/beacon", {
    "crew_id": "X", "lat": 39.0, "lon": -120.955, "t_s": T0 + 300}, "g1-key"))
print("POST G1 with the shared key ->", post("/api/beacon", {
    "crew_id": "G1", "lat": 39.10, "lon": -121.05, "t_s": T0 + 300},
    "shared-key"))
# (the real server checks t_s against its own wall clock: these stub-time
# reports are fed to the tracker directly from here on)
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon  # noqa: E402
tracker.ingest(CrewBeacon("G1", LatLon(39.10, -121.05), T0 + 300))
clock.t = T0 + 360
res = daemon.run_cycle()
show("cycle 2, G1 available", res)
st = get("/api/state")
print("  /api/state: plan stops for G1 (the console builds its routes from "
      "these):", [s["client_id"] for s in st["plan"]["plans"]["G1"]["stops"]])

c1 = ss.NEAR_LOC
tracker.ingest(CrewBeacon("G1", c1, T0 + 600, CrewStatus.DEPLOYING,
                          client_id="c1"))
weather.wind_mps = 18.0
clock.t = T0 + 660
res = daemon.run_cycle()
show("cycle 3, G1 DEPLOYING at c1, wind 8->18 m/s", res)
for a in res.assessments:
    if a.client_id == "c1":
        print(f"  c1 eta_p10 = +{(a.eta.eta_p10_s - clock.t) / 60:.1f} min")

clock.t = T0 + 600 + cfg.beacon_stale_s + 60
res = daemon.run_cycle()
show("cycle 4, G1 silent > beacon_stale_s", res)

tracker2 = crews_mod.CrewTracker(cfg, roster=roster)
d2, m2, *_ = ss.make_stack(tmp, cfg=cfg, clock=clock, tracker=tracker2,
                           test_world=False)
print("\nrestart restore() ->", d2.restore(), "| tracker crews:",
      [(c.crew_id, c.last_beacon and c.last_beacon.status.value,
        c.last_beacon and c.last_beacon.client_id) for c in tracker2.crews()])
clock.t += 300
show("cycle after restart, no report yet", d2.run_cycle())
httpd.shutdown()
