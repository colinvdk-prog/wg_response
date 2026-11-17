"""Lane C probe P6b (scratch_review/crew_program_review_2026-11-11/C_liveops/
probe_deploying_overrun.py), AFTER: the same world — G1 planned for c1, then
reporting DEPLOYING at c1 while the feed's fire jumps to 1.2 km upwind — with
the SAFETY SEMANTICS 45 knobs armed (crew_watch + crew_hold_worked). Prints
what the watch says about G1, the operator events and the hold.

Run from the repo root: python scratch_review/live_crew_groundwork/after_deploying_overrun.py [--client-id]
(--client-id: the deploying reports name c1; without it the hold infers c1 as
the one enrolled home within crew_work_radius_m.)"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("WGRESP_STATE_S3", None)

import seeded_stack as ss  # noqa: E402
from wgresponse import crews as crews_mod, geo  # noqa: E402
from wgresponse.contracts import CrewBeacon, CrewStatus, LatLon  # noqa: E402

T0 = ss.T0
C1 = ss.NEAR_LOC
CLIENT_ID = "c1" if "--client-id" in sys.argv else ""
cfg = ss.small_cfg(crew_watch=True, crew_hold_worked=True)
tr = crews_mod.CrewTracker(cfg)
clock = ss.SettableClock(T0)
d, m, feed, eng, notif, store, wx, ter = ss.make_stack(
    tempfile.mkdtemp(prefix="laneC_after_"), cfg=cfg, clock=clock, tracker=tr,
    test_world=False)


def beacon(cid, loc, t, st=CrewStatus.AVAILABLE, client_id=""):
    tr.ingest(CrewBeacon(crew_id=cid, loc=loc, t_s=t, status=st,
                         client_id=client_id))


beacon("G1", LatLon(39.10, -121.05), T0 + 30)
clock.t = T0 + 60
res = d.run_cycle()
print("cycle A plan:", {c: [s.client_id for s in cp.stops]
                        for c, cp in res.plan.plans.items()})
beacon("G1", C1, T0 + 1800, CrewStatus.DEPLOYING, CLIENT_ID)
near = geo.destination(C1, 270.0, 1200.0)
feed.incidents = [ss.feed_incident(origin=near, updated_at_s=T0 + 1700,
                                   acreage=800.0)]
wx.wind_mps = 14.0
for k in range(3):
    clock.t = T0 + 1860 + k * 300
    beacon("G1", C1, clock.t - 10, CrewStatus.DEPLOYING, CLIENT_ID)
    res = d.run_cycle()
    a = [x for x in res.assessments if x.client_id == "c1"]
    lead = (a[0].eta.eta_p10_s - clock.t) / 60 if a else float("nan")
    print(f"t=+{(clock.t - T0) / 60:.0f}min c1 P10 lead {lead:.1f} min | "
          f"plan crews {sorted(res.plan.plans) if res.plan else None} | "
          f"unserved {[(u.client_id, u.reason) for u in (res.plan.unserved if res.plan else [])]} | "
          f"operator_events {[(o.kind, o.detail.get('crew_id')) for o in res.operator_events]}")
    for n in res.notes:
        if "G1" in n:
            print("   note:", n)
    for row in res.crew_watch["crews"]:
        print(f"   watch row: {row['crew_id']} level={row['level']} "
              f"margin_min={row['margin_min']} binding={row['binding']} "
              f"spot={row['spot']['margin_min']} "
              f"home={row['home'] and (row['home']['client_id'], row['home']['source'], row['home']['margin_min'])} "
              f"way_out={row['way_out']}")
st = d.state()
print("/api/state crew_watch crews:",
      [(r["crew_id"], r["level"], r["margin_min"])
       for r in st["crew_watch"]["crews"]])
