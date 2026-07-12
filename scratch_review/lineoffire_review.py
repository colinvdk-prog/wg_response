"""Founder review 2026-07-11: 'homes earlier in the line of fire could have been
protected'. Reproduce the DEPLOYED run path (server turns spotting + grass ON,
real roads, standard zone) and dump every home's outcome + timing so we can tell
SAVABLE-but-lost (a dispatch problem) from genuinely UNSAVABLE (correct triage)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS

SERVER_CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10}

def upwind(key, dist_m):
    loc = LOCATIONS[key]
    return geo.destination(loc.center, loc.hazard_wind_dir_deg, dist_m)

def run(key, n_clients=40, n_crews=4, dist_m=8000.0, intensity="high",
        ticks=48, seed="review"):
    s = simui.SimSession(key, n_clients, n_crews, seed=seed,
                         cfg_overrides=dict(SERVER_CFG), fire_zone="standard")
    ign = upwind(key, dist_m)
    s.ignite(ign.lat, ign.lon, intensity=intensity)
    s.set_speed(1.0)                       # 900 s = 15 min sim per tick
    last = None
    for _ in range(ticks):
        last = s.tick()
    return last

def analyze(st, key):
    sb = st["scoreboard"]
    now = st["sim_now_s"]
    t_ign = min((f["t_ignite_s"] for f in st.get("fires", [])), default=now)
    elapsed_h = (now - t_ign) / 3600.0
    print(f"\n===== {key}  (elapsed since ignition {elapsed_h:.1f} h, "
          f"tick {st['tick']}) =====")
    print(f"served={sb.get('served')} withdrawn={sb.get('withdrawn')} "
          f"violations={sb.get('violations')} relocations={sb.get('relocations')}")
    print("outcomes:", json.dumps(sb.get("outcomes", {}), sort_keys=True))
    # per-home dump — sort by threat so 'line of fire' order is visible
    rows = []
    for c in st["clients"]:
        rows.append({
            "id": c.get("client_id") or c.get("id"),
            "outcome": c.get("outcome"),
            "gel": bool(c.get("gel_applied")),
            "gelled_at_h": (None if c.get("gelled_at_s") in (None,)
                            else round((c["gelled_at_s"] - t_ign) / 3600.0, 2)),
            "alert": c.get("alert") or c.get("alert_state"),
            "p10_h": _rel(c.get("eta_p10_s") or c.get("threat_p10_s"), t_ign),
            "p50_h": _rel(c.get("eta_p50_s") or c.get("threat_p50_s"), t_ign),
        })
    # which keys actually exist on a client? print once for schema truth
    print("client keys:", sorted(st["clients"][0].keys()) if st["clients"] else [])
    # group by outcome
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r["outcome"]].append(r)
    for oc in sorted(by):
        print(f"\n  -- {oc}  (n={len(by[oc])})")
        for r in sorted(by[oc], key=lambda x: (x["p10_h"] is None, x["p10_h"] or 0)):
            print(f"     {r['id']:>10}  p10={_f(r['p10_h'])}  p50={_f(r['p50_h'])}"
                  f"  gel={r['gel']}  gelled@={_f(r['gelled_at_h'])}h  alert={r['alert']}")
    return rows

def _rel(t_s, t_ign):
    if t_s is None:
        return None
    try:
        return round((float(t_s) - t_ign) / 3600.0, 2)
    except (TypeError, ValueError):
        return None

def _f(x):
    return "  inf" if x is None else f"{x:6.2f}"

if __name__ == "__main__":
    for key in ("santa_rosa_ca", "boulder_co"):
        st = run(key)
        analyze(st, key)
