"""Trace the eventually-lost homes tick-by-tick: were they EVER feasible (a crew
could have reached+coated+egressed safely), and did an idle crew exist then? This
separates 'savable but skipped' (fixable dispatch) from 'no safe corridor ever'
(correct — the safety law forbids it)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS

CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}

def upwind(key, d):
    loc = LOCATIONS[key]
    return geo.destination(loc.center, loc.hazard_wind_dir_deg, d)

key = "santa_rosa_ca"
s = simui.SimSession(key, 40, 4, seed="review", cfg_overrides=dict(CFG),
                     fire_zone="standard")
ign = upwind(key, 8000.0)
s.ignite(ign.lat, ign.lon, intensity="high")
s.set_speed(1.0)

WATCH = {"sim-c33", "sim-c25", "sim-c8"}     # the eventually-abandoned homes
# per-tick: for each watched home, its unserved reason + assigned + how many
# crews were idle (available, no job) that tick.
hdr = f"{'tick':>4} {'elapsed_h':>9} {'idleCrews':>9}  " + "  ".join(
    f"{w:>8}" for w in sorted(WATCH))
print(hdr)
first_reason = {}
for t in range(48):
    st = s.tick()
    now = st["sim_now_s"]
    t_ign = min(f["t_ignite_s"] for f in st["fires"])
    el = (now - t_ign) / 3600.0
    # idle crews: available and not currently assigned/en-route/deploying
    idle = sum(1 for c in st["crews"]
               if (c.get("status") or c.get("state")) in ("available", "idle"))
    reasons = {u["client_id"]: u["reason"] for u in st.get("unserved", [])}
    cli = {c["client_id"]: c for c in st["clients"]}
    cells = []
    for w in sorted(WATCH):
        c = cli.get(w, {})
        asg = "A" if c.get("assigned") else "."
        srv = "S" if c.get("serviced") else "."
        rsn = reasons.get(w, c.get("outcome", "-"))
        cells.append(f"{asg}{srv}:{rsn[:5]:>5}")
        if w not in first_reason and rsn not in ("-", "monitoring", "not_threatened"):
            first_reason[w] = (t, round(el, 2), rsn)
    # only print every 4th tick to keep it readable, plus always the last
    if t % 4 == 0 or t == 47:
        print(f"{t:>4} {el:>9.2f} {idle:>9}  " + "  ".join(cells))

print("\nFirst non-trivial reason per watched home (tick, elapsed_h, reason):")
for w in sorted(WATCH):
    print(f"  {w}: {first_reason.get(w, 'never flagged')}")

# final crew utilization: how many total gel jobs did each crew do, and were any
# crews idle while c33/c25 were still (p10) feasible?
print("\nFinal crew stop counts:")
for c in st["crews"]:
    cid = c.get("crew_id") or c.get("id")
    print(f"  {cid}: status={c.get('status') or c.get('state')} "
          f"stops_done~ (see serviced list)")
from collections import Counter
served_by_crew = Counter(x.get("crew_id") for x in st.get("serviced", []))
print("  serviced-by-crew:", dict(served_by_crew))
