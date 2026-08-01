"""caught_dissect.py -- founder question: are our recorded 'crew caught in
fire' events TRUE ENTRAPMENTS or FIRE-AT-THE-ROADSIDE (a survivable transit
past a burning roadside)? Read-only forensic dissection, in-process (no
:8878 server touched), of two known SEM-10 crew_overrun events referenced in
contracts.py's CORRIDOR HONESTY note (2026-07-29 A/B measurement):
  (a) paradise_ca|+0|8000|high, 4 crews, deploy_min=10 + dispatch_plan_on_roads
      + dispatch_hold_h=24 + corridor_erosion_euclidean + route_alternatives=3
      -> "Route candidates as built: REJECTED (... +3 caught crews)"
  (b) same map/wind/dist/inten, 8 crews, erosion-only (euclidean, no other
      overrides) -> "2 crews caught in burn at 8 crews @ radius 500"
Construction mirrors scratch_review/opt_ember.py:run_one (BASE dict from
scratch_review.bench_dispatch, same SimSession/ignite pattern, default
placement='scattered'/staging='default' i.e. client_locs=None, crew_locs=None).

For every recorded crew_overrun event in each run this dissects, using the
LIVE engine samplers (session._crew_exposure's own inputs, never a
reimplementation):
  * crew lat/lon, mode/status/reason at the catch tick
  * FBFM40 fuel code+class at the crew's cell, and at its 8 neighbor cells
  * whether the crew was ON its road-graph route at that instant
    (leg.on_roads) vs off-road/stationary, + distance to nearest road node
    when no leg is active
  * nominal (member-0) arrival at the cell vs the catch time (burn residence)
  * eroded-P10 vs member-0 gap at the cell (erosion-import signal)
  * time-to-nearest non-burning cell ROUTED along the crew's actual active
    leg / precomputed egress plan, pass-time aware with the same cold-black
    carve-out simui._route_metrics uses (seconds of transit that would have
    cleared it), plus a straight-line ring-search distance as a model-
    independent cross-check

Read-only on wgresponse/. Writes scratch_review/caught_dissect.json.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, scenarios  # noqa: E402
from wgresponse import fuels, geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

HORIZON_TICKS = 60
N_CLIENTS = 40
SEED = "paradise_ca|+0|8000|high"

NB_CODES = {91, 92, 93, 98, 99}
FUEL_CLASS_RANGES = [
    ("GR", (101, 109)),
    ("GS", (121, 124)),
    ("SH", (141, 149)),
    ("TU", (161, 165)),
    ("TL", (181, 189)),
    ("SB", (201, 204)),
]


def classify_code(code):
    if code is None:
        return "??"
    c = int(code)
    if c in NB_CODES:
        return "NB"
    for tag, rng in FUEL_CLASS_RANGES:
        if rng[0] <= c <= rng[1]:
            return tag
    return "??"


def fuel_name(code):
    fm = fuels.FUEL_TABLE.get(int(code))
    return fm.name if fm is not None else f"unknown code {code}"


def fuel_at(tile, p):
    rc = geo.latlon_to_rc(tile.spec, p)
    if rc is None:
        return dict(r=None, c=None, code=None, cls="??", name="OFF-TILE")
    code = int(round(tile.fuel.get(*rc)))
    return dict(r=rc[0], c=rc[1], code=code, cls=classify_code(code),
                name=fuel_name(code))


def neighbor_fuels(tile, rc):
    r0, c0 = rc
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r, c = r0 + dr, c0 + dc
            if 0 <= r < tile.spec.nrows and 0 <= c < tile.spec.ncols:
                code = int(round(tile.fuel.get(r, c)))
                out.append(dict(dr=dr, dc=dc, code=code,
                                 cls=classify_code(code), name=fuel_name(code)))
            else:
                out.append(dict(dr=dr, dc=dc, code=None, cls=None,
                                 name="OFF-GRID"))
    return out


def nominal_min(session, p):
    """min member[0] (ground-truth) arrival across live fires at p -- the
    EXACT input simui._crew_exposure uses for `nom`."""
    best = math.inf
    for f in session._fires.values():
        if f.ens is None:
            continue
        v = geo.raster_sample(f.ens.members[0], p)
        if v is not None and v < best:
            best = v
    return best


def ring_search_clear(session, tile, p, now, cap_m=4000.0):
    """Straight-line distance to the nearest cell whose member-0 (nominal)
    arrival is NOT <= now (unburned as of `now`) -- outward ring search on
    the ground-truth raster. Model-independent cross-check on the routed
    number below (ignores roads entirely)."""
    spec = tile.spec
    rc = geo.latlon_to_rc(spec, p)
    if rc is None:
        return None
    fire = next(iter(session._fires.values()), None)
    if fire is None or fire.ens is None:
        return None
    truth = fire.ens.members[0]
    r0, c0 = rc
    max_k = int(cap_m / spec.cell_m) + 1
    best = None
    for k in range(max_k + 1):
        if best is not None and (k - 1) * spec.cell_m > best[0]:
            break
        for r in range(r0 - k, r0 + k + 1):
            if not (0 <= r < spec.nrows):
                continue
            cols = (range(c0 - k, c0 + k + 1) if r in (r0 - k, r0 + k)
                    else (c0 - k, c0 + k))
            for c in cols:
                if not (0 <= c < spec.ncols):
                    continue
                t = truth.data[r * spec.ncols + c]
                if (not math.isfinite(t)) or t > now:
                    q = geo.rc_to_latlon(spec, r, c)
                    d = geo.haversine_m(p, q)
                    if best is None or d < best[0]:
                        best = (d, r, c)
        if best is not None:
            break
    if best is None:
        return None
    d, r, c = best
    return dict(dist_m=round(d, 1), r=r, c=c)


def _chain_dist(points):
    d = 0.0
    for i in range(1, len(points)):
        d += geo.haversine_m(points[i - 1], points[i])
    return d


def routed_clear(session, cs, now):
    """Walk the crew's ACTUAL active path (current leg if travel/egress,
    else its precomputed egress plan for the current stop, else nothing) and
    sample the nominal (member-0) front at each waypoint, PASS-TIME aware
    exactly like simui._route_metrics: a sample is 'clear' when the nominal
    front has not reached it by the crew's pass time (now + cumulative
    transit at the path's own implied speed), applying the same COLD-BLACK
    carve-out the engine uses. Returns a dict; source=None => no active path
    existed at the catch tick (crew was stationary, nothing assigned)."""
    leg = cs._leg
    pts, speed, source, on_roads = None, None, None, None
    if leg is not None and leg.get("pts"):
        pts = [cs.pos] + list(leg["pts"])
        speed = max(leg.get("speed", cs.speed), 0.1)
        on_roads = leg.get("on_roads")
        source = f"active_leg({leg.get('kind')})"
    elif getattr(cs, "_egress_plan", None) and cs._egress_plan.get("points"):
        ep = cs._egress_plan
        pts = list(ep["points"])
        if pts and geo.haversine_m(pts[0], cs.pos) > 1.0:
            pts = [cs.pos] + pts
        total_d = _chain_dist(pts)
        speed = max(total_d / ep["time_s"], 0.1) if ep.get("time_s") else cs.speed
        on_roads = ep.get("on_roads")
        source = "precomputed_egress_plan"
    if not pts or len(pts) < 2:
        return dict(source=None, on_roads=None, cleared=False, transit_s=None,
                    note="no active leg / egress plan at the catch tick -- "
                         "crew was stationary with no assigned path")
    cum_d = 0.0
    for i in range(1, len(pts)):
        cum_d += geo.haversine_m(pts[i - 1], pts[i])
        dt = cum_d / speed
        pass_t = now + dt
        nom = nominal_min(session, pts[i])
        cold_black = (math.isfinite(nom)
                      and (pass_t - nom) >= simui._COLD_BLACK_PASS_S)
        burned_here = math.isfinite(nom) and nom <= pass_t and not cold_black
        if not burned_here:
            return dict(source=source, on_roads=on_roads, cleared=True,
                        transit_s=round(dt, 1), n_waypoints_checked=i,
                        speed_mps=round(speed, 2))
    total_dt = cum_d / speed
    return dict(source=source, on_roads=on_roads, cleared=False,
                transit_s=None, route_total_s=round(total_dt, 1),
                speed_mps=round(speed, 2),
                note="entire remaining path is still nominal-burned "
                     "(pass-time aware) all the way to route end")


def on_road_check(session, cs):
    leg = cs._leg
    if leg is not None:
        nxt = leg["pts"][0] if leg.get("pts") else None
        return dict(has_active_leg=True, leg_kind=leg.get("kind"),
                    on_roads=bool(leg.get("on_roads")),
                    dist_to_next_waypoint_m=(
                        round(geo.haversine_m(cs.pos, nxt), 1)
                        if nxt is not None else None))
    rg = session._road_graph
    near = rg.nearest_node(cs.pos) if rg is not None else None
    return dict(has_active_leg=False, leg_kind=None, on_roads=None,
                nearest_road_node_dist_m=(round(near[1], 1)
                                          if near is not None else None),
                note=f"no active leg at the catch tick (mode={cs._mode!r}) "
                     "-- distance is to nearest road-graph NODE, not a route")


def dissect_event(session, tile, ov, tick_n, now, t0):
    cid = ov["crew_id"]
    cs = session._crew_sims[cid]
    pos = cs.pos
    cell = fuel_at(tile, pos)
    neighbors = (neighbor_fuels(tile, (cell["r"], cell["c"]))
                 if cell["r"] is not None else [])
    nom = ov["nominal_arrival_s"]
    p10 = ov["eroded_p10_s"]
    gap_s = (None if (nom is None or p10 is None) else round(p10 - nom, 1))
    residence_s = (None if (nom is None or ov["reason"] != "burned")
                   else round(now - nom, 1))
    road = on_road_check(session, cs)
    routed = routed_clear(session, cs, now)
    ring = ring_search_clear(session, tile, pos, now)
    itin = [s["client_id"] for s in cs.itinerary]
    return dict(
        tick=tick_n, sim_elapsed_h=round((now - t0) / 3600.0, 3),
        crew_id=cid, reason=ov["reason"], status=ov["status"], mode=ov["mode"],
        lat=round(pos.lat, 6), lon=round(pos.lon, 6),
        cell=cell, neighbors=neighbors,
        nominal_arrival_s=nom, eroded_p10_s=p10,
        nominal_vs_now_burn_residence_s=residence_s,
        p10_minus_nominal_gap_s=gap_s,
        on_road=road, routed_clear=routed, straight_line_clear=ring,
        current_itinerary=itin, egress_zone_id=cs.egress_zone_id,
        anchor_zone_id=session._crew_anchor.get(cid),
    )


def run(tag, n_crews, cfg_overrides):
    sc = next(s for s in scenarios() if s["seed"] == SEED)
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(BASE)
    cfg.update(cfg_overrides)
    s = simui.SimSession(sc["map"], N_CLIENTS, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("off_tile")
    t0 = s._t0_s
    tile = s._tile
    cfg_obj = s._cfg
    events = []
    timeline = []
    for _ in range(HORIZON_TICKS):
        st = s.tick()
        now = s._now_s
        tick_n = st["tick"]
        for ov in (st.get("crew_overruns") or []):
            timeline.append(dict(tick=tick_n, crew_id=ov["crew_id"],
                                 reason=ov["reason"], status=ov["status"],
                                 mode=ov["mode"]))
            events.append(dissect_event(s, tile, ov, tick_n, now, t0))
    cfg_echo = dict(
        deploy_min=cfg_obj.deploy_min,
        egress_buffer_min=cfg_obj.egress_buffer_min,
        corridor_erosion_m=cfg_obj.corridor_erosion_m,
        corridor_erosion_euclidean=cfg_obj.corridor_erosion_euclidean,
        route_alternatives=cfg_obj.route_alternatives,
        dispatch_plan_on_roads=cfg_obj.dispatch_plan_on_roads,
        dispatch_hold_h=cfg_obj.dispatch_hold_h,
        grid_cell_m=cfg_obj.grid_cell_m, cell_m_actual=tile.spec.cell_m,
        roads_on=s._road_graph is not None,
    )
    per_crew_ticks = {}
    for row in timeline:
        per_crew_ticks.setdefault(row["crew_id"], []).append(row["tick"])
    return dict(tag=tag, n_crews=n_crews, cfg=cfg_echo, seed=SEED,
                n_events=len(events), crews_caught=sorted(per_crew_ticks),
                per_crew_tick_list=per_crew_ticks, timeline=timeline,
                events=events)


def main():
    out = {}
    out["a_4crew_roads_erosion_hold24_alt3"] = run(
        "a", 4, {"deploy_min": 10, "dispatch_plan_on_roads": True,
                 "dispatch_hold_h": 24, "corridor_erosion_euclidean": True,
                 "route_alternatives": 3})
    out["b_8crew_erosion_only"] = run(
        "b", 8, {"corridor_erosion_euclidean": True})

    for key, res in out.items():
        print(f"\n==== {key} ====")
        print(f"crews_caught={res['crews_caught']}  n_events={res['n_events']}")
        for cid, ticks in res["per_crew_tick_list"].items():
            print(f"  {cid}: ticks {ticks}")
        for e in res["events"]:
            print(f"  tick={e['tick']:>3} {e['crew_id']} reason={e['reason']} "
                  f"status={e['status']} mode={e['mode']} "
                  f"cell={e['cell']['cls']}/{e['cell']['name']} "
                  f"on_road={e['on_road']['on_roads']} "
                  f"residence_s={e['nominal_vs_now_burn_residence_s']} "
                  f"gap_s={e['p10_minus_nominal_gap_s']} "
                  f"routed_clear_s={e['routed_clear'].get('transit_s')} "
                  f"straight_clear_m={(e['straight_line_clear'] or {}).get('dist_m')}")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "caught_dissect.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print("\nwrote", out_path)


if __name__ == "__main__":
    main()
