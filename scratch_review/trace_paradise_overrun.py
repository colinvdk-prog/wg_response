"""Root-cause the 2 crew overruns on paradise_ca extended (bench_alerted probe).
Reruns the exact scenario, logs every tick's crew positions/status/mode, the
overrun rows when they appear, relocations, and the fire's arrival field at the
crew positions at overrun time."""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wgresponse import simui, geo
from wgresponse.simui import LOCATIONS

CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10, "deploy_min": 15.0}
key = "paradise_ca"
loc = LOCATIONS[key]
wd = loc.hazard_wind_dir_deg
s = simui.SimSession(key, 40, 2, seed=f"alerted|{key}",
                     cfg_overrides=dict(CFG), fire_zone="extended")
err = None
used = None
for db in (0, 10, -10, 20, -20, 30, -30):
    for shrink in (1.0, 0.9, 0.8, 0.7, 0.6):
        ign = geo.destination(loc.center, wd + db, 15000.0 * shrink)
        try:
            s.ignite(ign.lat, ign.lon, intensity="high", wind_dir_deg=wd)
            err = None; used = (db, shrink, ign)
            break
        except ValueError as e:
            err = str(e)
    if err is None:
        break
print(f"ignition: bearing_offset={used[0]} shrink={used[1]} "
      f"({used[2].lat:.5f},{used[2].lon:.5f})  dist={15000*used[1]/1000:.1f}km")

prev_reloc = 0
for t in range(240):
    st = s.tick()
    sb = st["scoreboard"]
    ovr = st.get("crew_overruns") or []
    rel = sb.get("relocations", 0)
    if rel != prev_reloc:
        for c in st["crews"]:
            if c.get("relocating"):
                print(f"t={t} ({(st['sim_now_s']-min(f['t_ignite_s'] for f in st['fires']))/3600:.1f}h) "
                      f"RELOCATE {c['crew_id']} reason={c.get('relocate_reason')} "
                      f"pos=({c['lat']:.4f},{c['lon']:.4f}) status={c.get('status')}")
        prev_reloc = rel
    if ovr:
        el = (st["sim_now_s"] - min(f["t_ignite_s"] for f in st["fires"])) / 3600.0
        print(f"\n=== t={t} ({el:.1f}h) OVERRUN rows ===")
        for o in ovr:
            print("   ", o)
        for c in st["crews"]:
            print(f"    crew {c['crew_id']}: status={c.get('status')} mode?={c.get('relocating')} "
                  f"pos=({c['lat']:.4f},{c['lon']:.4f}) region={c.get('region')} "
                  f"inbound={c.get('inbound')} avail_eta={c.get('arrival_eta_s')}")
        if sb.get("violations", 0) >= 2:
            break
print(f"\nfinal: violations={sb.get('violations')} overruns_cum={sb.get('crew_overruns', 0) if isinstance(sb.get('crew_overruns'), int) else 'see rows'} "
      f"relocations={sb.get('relocations')} served={sb.get('served')}")
