"""caught_trace.py -- tick-by-tick narrative for the specific crews SEM-10
flags caught in caught_dissect.py's scenario (a) (paradise_ca|+0|8000|high,
4 crews, deploy_min=10 + dispatch_plan_on_roads + dispatch_hold_h=24 +
corridor_erosion_euclidean + route_alternatives=3). Read-only; prints only
(no JSON) -- this is a diagnostic trace to understand HOW a crew arrived at
its tick-48 catch position (deploying/aborting/relocating/on a road leg),
not a scored artifact.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, scenarios  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

SEED = "paradise_ca|+0|8000|high"


def build(n_crews, overrides):
    sc = next(s for s in scenarios() if s["seed"] == SEED)
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(BASE)
    cfg.update(overrides)
    s = simui.SimSession(sc["map"], 40, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    return s


def nominal_min(session, p):
    best = math.inf
    for f in session._fires.values():
        if f.ens is None:
            continue
        v = geo.raster_sample(f.ens.members[0], p)
        if v is not None and v < best:
            best = v
    return best


def nearest_client(session, p):
    best = None
    for cl in session._clients:
        d = geo.haversine_m(p, cl.loc)
        if best is None or d < best[1]:
            best = (cl.client_id, d)
    return best


def trace(tag, n_crews, overrides, watch_crews, up_to_tick=52):
    s = build(n_crews, overrides)
    t0 = s._t0_s
    print(f"\n########## {tag}: n_crews={n_crews} overrides={overrides} "
          f"##########")
    for cid in watch_crews:
        z = s._crew_anchor.get(cid)
        print(f"  {cid} anchor_zone={z}")
    for _ in range(up_to_tick):
        st = s.tick()
        now = s._now_s
        tick_n = st["tick"]
        ovs = {o["crew_id"]: o for o in (st.get("crew_overruns") or [])}
        relocating = {c["crew_id"]: (c["relocating"], c["relocate_reason"])
                      for c in st["crews"]}
        for cid in watch_crews:
            if cid not in s._crew_sims:
                continue
            cs = s._crew_sims[cid]
            leg = cs._leg
            leg_desc = "None"
            if leg is not None:
                leg_desc = (f"kind={leg['kind']} on_roads={leg['on_roads']} "
                           f"target=({leg['target'].lat:.5f},"
                           f"{leg['target'].lon:.5f}) npts={len(leg['pts'])}")
            nom = nominal_min(s, cs.pos)
            nom_h = "inf" if math.isinf(nom) else f"{(now - nom) / 60.0:+.1f}min"
            reloc, reason = relocating.get(cid, (False, None))
            ov = ovs.get(cid)
            flag = ""
            if ov:
                flag = f"  <<<< OVERRUN reason={ov['reason']}"
            print(f"[t{tick_n:>3}] {cid}: mode={cs._mode:<7} "
                  f"status={cs.status.value:<10} pos=({cs.pos.lat:.5f},"
                  f"{cs.pos.lon:.5f}) itin={len(cs.itinerary)} "
                  f"egress_zone={cs.egress_zone_id!r} "
                  f"egress_loc={'set' if cs.egress_loc else 'None'} "
                  f"reloc={reloc}/{reason} leg=[{leg_desc}] "
                  f"nom_front={nom_h}{flag}")
            if ov:
                nc = nearest_client(s, cs.pos)
                print(f"        nearest client: {nc[0]} @ {nc[1]:.1f} m")
    print(f"final tick={s._tick_n}  t0={t0}")


def main():
    trace("a_4crew", 4,
          {"deploy_min": 10, "dispatch_plan_on_roads": True,
           "dispatch_hold_h": 24, "corridor_erosion_euclidean": True,
           "route_alternatives": 3},
          watch_crews=["sim-g1", "sim-g2", "sim-g3", "sim-g4"])


if __name__ == "__main__":
    main()
