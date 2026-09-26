"""Live smoke test of the deployed public simulator (read-only demo API;
creates one throwaway in-memory session). Checks the three 2026-09-25
changes end to end over HTTP:
  1. the whole-valley preset eagle_vail_co loads real terrain + roads and a
     fire near Eagle can be ignited;
  2. the wind dial path: POST /config {wind_dir_deg} is accepted (was 400)
     and the engine's wind follows it;
  3. crews carry map_view (driven road paths + reasons for straight bits).
Usage: py -3 scratch_review/live_smoke_corridor.py [base_url]
"""
import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "https://m897yqgsmt.us-west-2.awsapprunner.com").rstrip("/")


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


t0 = time.time()
code, out = call("POST", "/api/sim/new",
                 {"location": "eagle_vail_co", "clients": 30, "crews": 4,
                  "seed": "live-smoke"})
print("new:", code, f"{time.time() - t0:.1f}s")
assert code == 200, out
sid = out["session_id"]
st = out["state"]
terr = st.get("terrain", {})
print("  terrain synthetic:", terr.get("synthetic"), "| notes:",
      [n[:90] for n in st.get("notes", []) if "road" in n.lower()][:3])
assert terr.get("synthetic") is False, "whole-valley tile did not load"

# CREW PLACEMENT (2026-09-26): stage crew g1 at Edwards before any fire —
# the engine snaps it to the nearest road node
code, out = call("POST", f"/api/sim/{sid}/crew",
                 {"crew_id": "sim-g1", "lat": 39.6452, "lon": -106.5930})
print("place sim-g1 at Edwards:", code,
      {k: out.get(k) for k in ("snapped_m", "lat", "lon", "note") if k in out}
      or str(out)[:160])
assert code == 200, out

# ignite upwind (WSW) of the Eagle base — the base's own hazard-axis fire;
# scan bearing / range until a burnable cell takes (the harness's rule)
import math


def dest(lat, lon, brg_deg, dist_m):
    r = 6_371_000.0
    b = math.radians(brg_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    d = dist_m / r
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


code = None
for db in (0, 10, -10, 20, -20, 30, -30):
    for dist in (7000, 6000, 5000, 8000, 4000):
        lat, lon = dest(39.6605, -106.8285, 240 + db, dist)
        code, out = call("POST", f"/api/sim/{sid}/ignite",
                         {"lat": lat, "lon": lon, "intensity": "high",
                          "wind_dir_deg": 240})
        if code == 200:
            break
    if code == 200:
        break
print("ignite:", code, f"at {lat:.4f},{lon:.4f}")
assert code == 200, out
# placement locks once a fire is lit
code, out = call("POST", f"/api/sim/{sid}/crew",
                 {"crew_id": "sim-g2", "lat": 39.6452, "lon": -106.5930})
print("place after ignition (expect 409):", code)
assert code == 409, out
for _ in range(4):
    code, out = call("POST", f"/api/sim/{sid}/ctl", {"action": "step"})
    assert code == 200, out

code, out = call("POST", f"/api/sim/{sid}/config", {"wind_dir_deg": 315})
print("config wind 315:", code, out.get("wind_dir_deg", ""))
assert code == 200, out
code, out = call("POST", f"/api/sim/{sid}/ctl", {"action": "step"})
code, st = call("GET", f"/api/sim/{sid}/state")
st = st.get("state", st)
winds = {st.get("wind_dir_deg")} | {f.get("wind_dir_deg") for f in st.get("fires", [])}
print("  engine wind after step:", winds)
crews = st.get("crews", [])
mv = [c for c in crews if c.get("map_view")]
print(f"  crews {len(crews)}, with map_view {len(mv)}; served "
      f"{st.get('scoreboard', {}).get('served')}, violations "
      f"{st.get('scoreboard', {}).get('violations')}")
assert 315 in winds or 315.0 in winds, "wind change did not reach the engine"
assert mv, "no crew map_view in the live state"
# FIRE SHADING (2026-09-26): the drawn fire is the simulated actual fire
fv = st.get("fire_view")
print("  fire_view present:", bool(fv), "| keys:", sorted(fv)[:8] if isinstance(fv, dict) else type(fv).__name__)
assert fv, "no fire_view in the live state"
pl = st.get("crew_placement") or {}
print("  crew_placement:", {k: pl.get(k) for k in ("setup", "locked_reason") if k in pl})
print(f"LIVE SMOKE OK in {time.time() - t0:.0f}s (session {sid} left to expire)")
