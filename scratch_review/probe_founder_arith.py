"""Founder arithmetic check (2026-07-29): '1 crew, 30-min response, 30-min
safety buffer, 30-min coats, fire 3 h away => 4 houses at 100%.'

Builds EXACTLY that scenario in the engine: 4 homes chosen from the ignited
fire's own ground-truth arrival raster so the fire is genuinely ~3 h from
each (2.8-3.2 h, homes within ~500 m of each other), 1 crew from the default
depot (median ~1.7 km => response BETTER than the founder's 30 min).
Variants: uncertainty {real = shipped ensemble (wind +/-25%, dir +/-30 deg,
spotting on), certain = collapsed ensemble (no perturbation, spotting off =>
P10 ~= truth)} x deploy {30 founder, 15 ours}. Records per-home truth
arrival vs the tick-1 P10/P50 the planner actually saw, the deadline window
math, and who got coated. Writes probe_founder_arith.json.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS, load_tile, wui_cells  # noqa: E402

MAP = "boulder_co"
DIST = 8000.0
INTEN = "high"
SEED = "founder-arith"
TARGET_H = (2.8, 3.2)
TICKS = 20                       # 5 h at 15-min ticks


def variant_cfg(mode: str, deploy: float) -> dict:
    cfg = dict(BASE)
    cfg.update({"deploy_min": deploy, "dispatch_plan_on_roads": True,
                "dispatch_hold_h": 24.0})
    if mode == "certain":
        cfg.update({"wind_perturb_frac": 0.0, "dir_perturb_deg": 0.0,
                    "spotting_enabled": False})
    return cfg


def ignite(s):
    loc = LOCATIONS[MAP]
    wd = loc.hazard_wind_dir_deg
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, DIST * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=INTEN, wind_dir_deg=wd)
            return
        except ValueError:
            continue
    raise RuntimeError("ignition failed")


def pick_homes(cfg: dict):
    """4 burnable cells whose TRUTH arrival is ~3 h, clustered <= ~500 m."""
    s = simui.SimSession(MAP, 4, 1, seed=SEED, cfg_overrides=dict(cfg))
    t0 = s._now_s
    ignite(s)
    s.tick()
    fire = next(iter(s._fires.values()))
    truth = (fire.ens.members[0] if getattr(fire.ens, "members", None)
             else fire.ens.p50)
    spec = truth.spec
    # REAL homes sit on the developed WUI band with road access — raw brush
    # cells measure access failure, not response arithmetic (the interface
    # bench learned this; snap the candidate set to WUI-band cells).
    tile, _syn = load_tile(MAP, cell_m=90.0)
    band = {(round(p.lat, 6), round(p.lon, 6))
            for p in (geo.rc_to_latlon(tile.spec, r, c)
                      for (r, c) in wui_cells(tile))}
    cands = []
    for r in range(spec.nrows):
        for c in range(spec.ncols):
            t = truth.data[r * spec.ncols + c]
            if math.isfinite(t):
                h = (t - t0) / 3600.0
                if TARGET_H[0] <= h <= TARGET_H[1]:
                    p = geo.rc_to_latlon(spec, r, c)
                    if (round(p.lat, 6), round(p.lon, 6)) in band:
                        cands.append((p, h))
    if len(cands) < 4:
        raise RuntimeError(f"only {len(cands)} WUI candidate cells at ~3 h")
    anchor = cands[len(cands) // 2]
    near = sorted(cands, key=lambda ch: geo.haversine_m(ch[0], anchor[0]))
    picked = near[:4]
    span = max(geo.haversine_m(p[0], picked[0][0]) for p in picked)
    return [p[0] for p in picked], [round(p[1], 2) for p in picked], span


def run(mode: str, deploy: float) -> dict:
    cfg = variant_cfg(mode, deploy)
    homes, truth_h, span_m = pick_homes(cfg)
    s = simui.SimSession(MAP, 4, 1, seed=SEED, cfg_overrides=dict(cfg),
                         client_locs=homes)
    t0 = s._now_s
    ignite(s)
    st = s.tick()
    now = s._now_s
    tick1 = []
    for row in st["clients"]:
        p10, p50 = row.get("eta_p10_s"), row.get("eta_p50_s")
        f = lambda t: (None if not isinstance(t, (int, float)) or t > 1e17
                       else round((t - t0) / 3600.0, 2))
        p10_h, p50_h = f(p10), f(p50)
        window_min = (None if p10_h is None else
                      round((p10_h * 60) - ((now - t0) / 60.0) - 30 - deploy, 1))
        tick1.append(dict(client=row["client_id"], p10_h=p10_h, p50_h=p50_h,
                          service_window_min_at_tick1=window_min))
    unserved_t1 = [dict(client=u.get("client_id"), reason=u.get("reason"))
                   for u in (st.get("unserved") or [])]
    crew_pos = None
    for cr in st.get("crews", []):
        crew_pos = (cr.get("lat"), cr.get("lon"))
    depot_km = (None if crew_pos is None or crew_pos[0] is None else
                round(min(geo.haversine_m(
                    geo.LatLon(crew_pos[0], crew_pos[1]), h) for h in homes)
                    / 1000.0, 2))
    viol = 0
    for _ in range(TICKS - 1):
        st = s.tick()
        viol = max(viol, st["scoreboard"].get("violations", 0))
    coated = [row["client_id"] for row in st["clients"] if row.get("gel_applied")]
    return dict(mode=mode, deploy=deploy, homes_span_m=round(span_m),
                truth_arrival_h=truth_h, tick1=tick1,
                unserved_tick1=unserved_t1, crew_depot_to_homes_km=depot_km,
                coated=len(coated), coated_ids=coated, violations=viol,
                outcomes={row["client_id"]: row["outcome"]
                          for row in st["clients"]},
                unserved_final=[dict(client=u.get("client_id"),
                                     reason=u.get("reason"))
                                for u in (st.get("unserved") or [])])


def main():
    out = []
    for mode in ("certain", "real"):
        for deploy in (30.0, 15.0):
            r = run(mode, deploy)
            out.append(r)
            t1 = r["tick1"]
            p10s = [t["p10_h"] for t in t1]
            print(f"{mode:<8} deploy={deploy:>4.0f}  truth~{r['truth_arrival_h']}h "
                  f"span={r['homes_span_m']}m  tick1-P10={p10s}  "
                  f"windows(min)={[t['service_window_min_at_tick1'] for t in t1]}  "
                  f"coated={r['coated']}/4  viol={r['violations']}")
    json.dump(out, open("scratch_review/probe_founder_arith.json", "w"),
              indent=1)
    print("wrote scratch_review/probe_founder_arith.json")


if __name__ == "__main__":
    main()
