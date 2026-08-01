"""caught_full.py -- combines caught_dissect's per-event forensic dissection
with caught_legtrace's _begin_leg probe so every catch event carries BOTH:
  (1) the ARRIVING leg (how the crew got to the catch cell this tick: real
      road route vs spur/chord, edge composition, distance) -- recovered via
      a read-only monkeypatch since SEM-10's relocate_to() clears cs._leg the
      same tick it fires, so the post-tick leg is None at audit time;
  (2) the PENDING departing route (what the sweep just re-targeted the crew
      onto for next tick) walked with the same pass-time/cold-black-aware
      clearance logic as caught_dissect.routed_clear, answering "how many
      more seconds of transit would clear it".
solver_budget_s is a wall-clock (time.monotonic) ILS deadline (dispatch.py),
so the exact dispatch solution -- and therefore which crew(s) end up caught
-- is NOT perfectly reproducible run to run even though the fire/ensemble
physics are fully seeded; this loops each scenario until a catch reproduces
and reports what it found. Read-only on wgresponse/. Writes
scratch_review/caught_full.json.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, scenarios  # noqa: E402
from scratch_review.caught_dissect import (  # noqa: E402
    classify_code, fuel_at, neighbor_fuels, nominal_min, ring_search_clear,
    _chain_dist,
)
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

SEED = "paradise_ca|+0|8000|high"
LEGLOG = []
_orig_begin_leg = simui._SimCrew._begin_leg


def _patched_begin_leg(self, kind, target):
    _orig_begin_leg(self, kind, target)
    leg = self._leg
    kinds = leg.get("edge_kind") or []
    dist_m = _chain_dist([self.pos] + list(leg["pts"]))
    LEGLOG.append(dict(
        crew_id=self.crew.crew_id, kind=kind,
        target=(round(target.lat, 6), round(target.lon, 6)),
        on_roads=leg["on_roads"], n_pts=len(leg["pts"]), dist_m=round(dist_m, 1),
        kind_counts={k: kinds.count(k) for k in set(kinds)} if kinds else {},
        start_pos=(round(self.pos.lat, 6), round(self.pos.lon, 6)),
    ))


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


def pending_route_clear(session, cs, now):
    """Walk the PENDING route (cs.pos -> cs.egress_loc, the target the sweep
    just assigned THIS tick for next tick's advance()) with the same pass-
    time + cold-black-aware clearance test as the engine's own
    _route_metrics. None if no pending egress_loc."""
    if cs.egress_loc is None:
        return dict(available=False, note="no pending egress_loc")
    speed = max(cs.speed, 0.1)
    pts = session._route_points_with_times(cs.pos, cs.egress_loc, speed)
    if len(pts) < 2:
        return dict(available=False, note="degenerate route")
    for q, dt in pts[1:]:
        pass_t = now + dt
        nom = nominal_min(session, q)
        cold_black = (math.isfinite(nom)
                      and (pass_t - nom) >= simui._COLD_BLACK_PASS_S)
        burned_here = math.isfinite(nom) and nom <= pass_t and not cold_black
        if not burned_here:
            return dict(available=True, cleared=True, transit_s=round(dt, 1))
    total_dt = pts[-1][1]
    return dict(available=True, cleared=False, transit_s=None,
                route_total_s=round(total_dt, 1),
                note="pending route still nominal-burned pass-time-aware "
                     "all the way to its end")


def on_road_full(session, arriving_leg, pos):
    rg = session._road_graph
    near = rg.nearest_node(pos) if rg is not None else None
    out = dict(nearest_road_node_dist_m=(round(near[1], 1)
                                         if near is not None else None))
    if arriving_leg is not None:
        kc = arriving_leg["kind_counts"]
        out.update(
            arriving_leg_on_roads=arriving_leg["on_roads"],
            arriving_leg_kind=arriving_leg["kind"],
            arriving_leg_dist_m=arriving_leg["dist_m"],
            arriving_leg_edge_counts=kc,
            arriving_leg_pct_road_edges=(
                round(100.0 * kc.get("road", 0)
                      / max(1, sum(kc.values())), 1) if kc else None),
            note="this crew arrived at the catch cell THIS tick via the "
                 "logged leg -- SEM-10's relocate_to() clears cs._leg the "
                 "same tick it re-targets a crew, so the post-tick leg is "
                 "always None at audit time; this is the leg that actually "
                 "carried it here.")
    else:
        out["note"] = "no leg logged into this crew this tick (already at rest)"
    return out


def run(tag, n_crews, overrides, max_attempts=15):
    for attempt in range(1, max_attempts + 1):
        LEGLOG.clear()
        s = build(n_crews, overrides)
        cfg_obj = s._cfg
        tile = s._tile
        t0 = s._t0_s
        per_tick_legs = {}
        all_events = []
        timeline = []
        for _ in range(60):
            before = len(LEGLOG)
            st = s.tick()
            now = s._now_s
            tick_n = st["tick"]
            arriving = {r["crew_id"]: r for r in LEGLOG[before:]}
            per_tick_legs[tick_n] = arriving
            for ov in (st.get("crew_overruns") or []):
                cid = ov["crew_id"]
                cs = s._crew_sims[cid]
                timeline.append(dict(tick=tick_n, crew_id=cid,
                                     reason=ov["reason"], status=ov["status"],
                                     mode=ov["mode"]))
                pos = cs.pos
                cell = fuel_at(tile, pos)
                neighbors = (neighbor_fuels(tile, (cell["r"], cell["c"]))
                             if cell["r"] is not None else [])
                nom, p10 = ov["nominal_arrival_s"], ov["eroded_p10_s"]
                gap_s = (None if (nom is None or p10 is None)
                         else round(p10 - nom, 1))
                residence_s = (None if (nom is None or ov["reason"] != "burned")
                              else round(now - nom, 1))
                arriving_leg = arriving.get(cid)
                road = on_road_full(s, arriving_leg, pos)
                pending = pending_route_clear(s, cs, now)
                ring = ring_search_clear(s, tile, pos, now)
                all_events.append(dict(
                    tick=tick_n, sim_elapsed_h=round((now - t0) / 3600.0, 3),
                    crew_id=cid, reason=ov["reason"], status=ov["status"],
                    mode=ov["mode"], lat=round(pos.lat, 6), lon=round(pos.lon, 6),
                    cell=cell, neighbors=neighbors,
                    nominal_arrival_s=nom, eroded_p10_s=p10,
                    burn_residence_s=residence_s, p10_minus_nominal_gap_s=gap_s,
                    on_road=road, pending_route_clear=pending,
                    straight_line_clear=ring,
                    egress_zone_id_after=cs.egress_zone_id,
                    relocate_reason_now=s._relocating.get(cid),
                    anchor_zone_id=s._crew_anchor.get(cid),
                ))
        if all_events:
            per_crew_ticks = {}
            for row in timeline:
                per_crew_ticks.setdefault(row["crew_id"], []).append(row["tick"])
            return dict(tag=tag, attempt=attempt, n_crews=n_crews,
                        overrides=overrides, seed=SEED,
                        crews_caught=sorted(per_crew_ticks),
                        per_crew_tick_list=per_crew_ticks,
                        cfg_echo=dict(
                            deploy_min=cfg_obj.deploy_min,
                            egress_buffer_min=cfg_obj.egress_buffer_min,
                            corridor_erosion_m=cfg_obj.corridor_erosion_m,
                            corridor_erosion_euclidean=cfg_obj.corridor_erosion_euclidean,
                            route_alternatives=cfg_obj.route_alternatives,
                            dispatch_plan_on_roads=cfg_obj.dispatch_plan_on_roads,
                            dispatch_hold_h=cfg_obj.dispatch_hold_h,
                            cell_m=tile.spec.cell_m,
                            roads_on=s._road_graph is not None),
                        events=all_events)
    return dict(tag=tag, attempt=max_attempts, n_crews=n_crews,
                overrides=overrides, seed=SEED, crews_caught=[],
                events=[], note="no catch reproduced")


def main():
    simui._SimCrew._begin_leg = _patched_begin_leg
    try:
        out = {}
        out["a_4crew"] = run("a", 4,
            {"deploy_min": 10, "dispatch_plan_on_roads": True,
             "dispatch_hold_h": 24, "corridor_erosion_euclidean": True,
             "route_alternatives": 3})
        out["b_8crew"] = run("b", 8, {"corridor_erosion_euclidean": True})
    finally:
        simui._SimCrew._begin_leg = _orig_begin_leg

    for key, res in out.items():
        print(f"\n==== {key} (attempt {res.get('attempt')}) ====")
        print(f"crews_caught={res['crews_caught']}")
        for e in res["events"]:
            print(f"\n-- tick={e['tick']} {e['crew_id']} reason={e['reason']} "
                  f"mode={e['mode']} status={e['status']}")
            print(f"   pos=({e['lat']},{e['lon']})  cell={e['cell']}")
            print(f"   neighbors={[(n['dr'],n['dc'],n['cls'],n['code']) for n in e['neighbors']]}")
            print(f"   nominal_arrival_s={e['nominal_arrival_s']} "
                  f"eroded_p10_s={e['eroded_p10_s']} "
                  f"residence_s={e['burn_residence_s']} gap_s={e['p10_minus_nominal_gap_s']}")
            print(f"   on_road={e['on_road']}")
            print(f"   pending_route_clear={e['pending_route_clear']}")
            print(f"   straight_line_clear={e['straight_line_clear']}")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "caught_full.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\nwrote", out_path)


if __name__ == "__main__":
    main()
