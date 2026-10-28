"""Eagle River valley crew-management review (founder 2026-10-20: "review the
response crew management for the eagle river, avon, vail minturn area only.
run the simulation. analyze it ... recommend improvements and perform at
least two new iterations"). NOT product code.

The simulator's three locations covering the area (Vail / East Vail / Minturn;
Beaver Creek / Avon / Edwards; the Eagle -> Vail corridor tile), built through
the server exactly as the page builds them (city weather, crown fire, the
spread-direction fix, calibrated embers, every crew-safety law the simulator
arms), 12 h of 15-min ticks per run:

  ignition sets   canonical  the one-click scenario's start (WSW wind 240)
                  south      a fire SSW of town, wind from 200 (Minturn /
                             Beaver Creek side)
                  northwest  a fire NW of town, wind from 300 (post-frontal)
                  pair       canonical + south lit together (wind 240)
                  late       canonical, then a second fire 3 km SSW of the
                             centre at T+2 h (a world change near working crews)
  intensities     moderate / high / extreme (pair / late: high)

Per run: the safety record (departure violations, crew-overrun rows, closest
P10 margin a crew stood at, withdrawals, relocations, escapes, crews off the
map), service (served, outcomes, unserved reasons, time to first gel,
protection lead per home, homes the fire reached unprotected), crew
utilisation (crew-ticks by status, distance driven), the fire (acres per hour,
fastest 1-h head gain, its share of the wind, crowned acres, first home
reached). Per-tick crew logs go to <out>/ticks/<run>.json.

Usage: py -3 scratch_review/eagle_valley/run_matrix.py OUT_DIR [--workers N]
       [--hours H] [--only RUN_ID[,RUN_ID]] [--cfg JSON] [--start-hour H]

--start-hour H (decimal LOCAL standard hours, e.g. 13.22 for 13:13): every
session starts at that hour of the epoch's day (POST /api/sim/new
start_local_hour; the default is the fixed epoch, 07:13 local) — the
afternoon-start arm of the iteration-3 review. Recorded per run.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

LOCS = {
    # location: (one-click scenario key, south bearing / dist, nw bearing / dist)
    "vail_co": "vail_westerly",
    "beaver_creek_co": "beaver_creek_westerly",
    "eagle_vail_co": "eagle_vail_corridor_westerly",
}
SOUTH = (200.0, 5000.0, 200.0)       # (bearing from centre, distance, wind FROM)
NORTHWEST = (300.0, 5000.0, 300.0)
LATE_SECOND = (200.0, 3000.0)        # second fire at T+2 h (wind unchanged)
INTENSITIES = ("moderate", "high", "extreme")


def runs():
    out = []
    for loc in LOCS:
        for ign in ("canonical", "south", "northwest"):
            for inten in INTENSITIES:
                out.append((f"{loc}|{ign}|{inten}", loc, ign, inten))
        out.append((f"{loc}|pair|high", loc, "pair", "high"))
        out.append((f"{loc}|late|high", loc, "late", "high"))
    return out


def _start(s, bearing, dist):
    """The scenario law for a start: the nearest grass / brush patch
    (SimSession.fine_fuel_start), else the first burnable cell outward."""
    from wgresponse import geo
    p = s.fine_fuel_start(bearing, dist)
    if p is not None:
        return p
    center = s._loc.center
    for db in (0, 10, -10, 20, -20, 30, -30):
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
            q = geo.destination(center, bearing + db, dist * shrink)
            rc = geo.latlon_to_rc(s._tile.spec, q)
            if rc is None:
                continue
            from wgresponse.fuels import is_burnable
            if is_burnable(int(round(s._tile.fuel.get(*rc)))):
                return q
    raise RuntimeError("no burnable start")


def build(loc, ign, inten, extra_cfg, start_hour=None):
    from wgresponse import server
    mgr = server.SimSessionManager()
    key = LOCS[loc]
    # the one-click roster for every run of a location (seed scenario-KEY):
    # runs differ by their fire, not by their homes and crews
    payload = {"scenario": key}
    if extra_cfg:
        payload["cfg"] = dict(extra_cfg)
    if start_hour is not None:
        payload["start_local_hour"] = float(start_hour)
    code, out = mgr.new_session(payload)
    if code != 200:
        raise RuntimeError(out)
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    canon = (f.origin, f.wind_dir_deg)
    s.reset()
    if ign in ("canonical", "pair", "late"):
        s.ignite(canon[0].lat, canon[0].lon, intensity=inten,
                 wind_dir_deg=canon[1])
        if ign == "pair":
            p = _start(s, SOUTH[0], SOUTH[1])
            s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=canon[1])
    else:
        b, d, w = SOUTH if ign == "south" else NORTHWEST
        p = _start(s, b, d)
        s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=w)
    return mgr, s


def head_stats(s, fid, hours):
    """Acres per hour, the head's distance from the origin per hour and the
    fastest 1-h gain (km/h), from the nominal field."""
    from wgresponse import geo
    f = s._fires[fid]
    spec = s._tile.spec
    data = f.ens.members[0].data
    t0 = f.t_ignite_s
    cell_ac = spec.cell_m ** 2 / 4046.8564224
    burned_by_h = []
    cells = [(i, v) for i, v in enumerate(data) if v <= t0 + hours * 3600.0]
    dist_cache = {}
    head = []
    acres = []
    for h in range(1, int(hours) + 1):
        th = t0 + h * 3600.0
        mx = 0.0
        n = 0
        for i, v in cells:
            if v <= th:
                n += 1
                d = dist_cache.get(i)
                if d is None:
                    r, c = divmod(i, spec.ncols)
                    d = geo.haversine_m(geo.rc_to_latlon(spec, r, c), f.origin)
                    dist_cache[i] = d
                if d > mx:
                    mx = d
        head.append(round(mx / 1000.0, 2))
        acres.append(round(n * cell_ac))
    gains = [head[0]] + [head[k] - head[k - 1] for k in range(1, len(head))]
    return acres, head, round(max(gains), 2) if gains else 0.0


def crowned_acres(s, fid, hours):
    from wgresponse import rothermel, spread
    f = s._fires[fid]
    crown = {}
    data = spread.compute_arrival(s._tile, [f.origin], s._wx, f.t_ignite_s,
                                  s._cfg, incident_id=fid, member_k=0,
                                  crown_trace=crown).data
    spec = s._tile.spec
    cell_ac = spec.cell_m ** 2 / 4046.8564224
    t_end = f.t_ignite_s + hours * 3600.0
    act = sum(1 for i, k in crown.items() if k == rothermel.CROWN_ACTIVE and data[i] <= t_end)
    pas = sum(1 for i, k in crown.items() if k == rothermel.CROWN_PASSIVE and data[i] <= t_end)
    return round(act * cell_ac), round(pas * cell_ac)


def run_one(args):
    run_id, loc, ign, inten, hours, extra_cfg, out_dir = args[:7]
    start_hour = args[7] if len(args) > 7 else None
    from wgresponse import geo, dispatch as _dispatch
    t_wall = time.time()
    # COATING WINDOW + PROXIMITY FIRST review (2026-10-27): which proximity
    # path each plan call took (fleet | route), counted on the planner itself
    prox_tags = {}
    if not getattr(_dispatch, "_wg_counted", False):
        _orig_plan = _dispatch.plan

        def _counted(*a, **kw):
            pl = _orig_plan(*a, **kw)
            tag = pl.objective_breakdown.get("proximity")
            if tag:
                _dispatch._wg_tags[tag] = _dispatch._wg_tags.get(tag, 0) + 1
            return pl
        _dispatch.plan = _counted
        _dispatch._wg_counted = True
    _dispatch._wg_tags = prox_tags
    try:
        mgr, s = build(loc, ign, inten, extra_cfg, start_hour)
    except Exception as exc:                       # pragma: no cover - probe
        return {"run": run_id, "error": repr(exc)}
    ticks = int(round(hours * 4))
    status_counts = {}
    crew_dist = {}
    last_pos = {}
    min_margin = {}
    overrun_rows = []
    dep_viol_max = 0
    viol_max = 0
    first_gel_s = None
    coat_eta = {}
    t0 = s._now_s
    log = []
    late_fired = False
    st = None
    blocked_ticks = 0
    idle_blocked = 0
    for k in range(ticks):
        if ign == "late" and not late_fired and k == 8:
            p = _start(s, LATE_SECOND[0], LATE_SECOND[1])
            s.ignite(p.lat, p.lon, intensity=inten,
                     wind_dir_deg=s._fires["sim-f1"].wind_dir_deg)
            late_fired = True
        step_t = time.time()
        st = s.tick()
        now = st["sim_now_s"]
        sb = st["scoreboard"]
        viol_max = max(viol_max, sb["violations"])
        dep_viol_max = max(dep_viol_max, len(s._violations))
        for r in st.get("crew_overruns") or []:
            overrun_rows.append(dict(r, t_h=round((now - t0) / 3600.0, 2)))
        crews_tick = []
        for c in st["crews"]:
            cid = c["crew_id"]
            key = ("relocating" if c.get("relocating") else c["status"])
            if c.get("left_map"):
                key = "left_map"
            status_counts.setdefault(cid, {})
            status_counts[cid][key] = status_counts[cid].get(key, 0) + 1
            pos = (c["lat"], c["lon"])
            if cid in last_pos and None not in pos and None not in last_pos[cid]:
                crew_dist[cid] = crew_dist.get(cid, 0.0) + geo.haversine_m(
                    geo.LatLon(*last_pos[cid]), geo.LatLon(*pos))
            last_pos[cid] = pos
            margin_h = None
            cs = s._crew_sims.get(cid)
            if cs is not None and not c.get("inbound") and not c.get("left_map"):
                nom, p10 = s._exposure_at(cs.pos, now)
                if not math.isinf(p10):
                    margin_h = round((p10 - now) / 3600.0, 2)
                    if margin_h >= 0 and (cid not in min_margin or margin_h < min_margin[cid]):
                        min_margin[cid] = margin_h
            crews_tick.append({"id": cid, "st": key, "lat": round(pos[0], 5) if pos[0] else None,
                               "lon": round(pos[1], 5) if pos[1] else None,
                               "itin": len(c.get("itinerary") or []),
                               "zone": c.get("egress_zone_id") or c.get("anchor_zone_id"),
                               "why": c.get("relocate_reason"), "p10_margin_h": margin_h})
        for cl in st["clients"]:
            if cl.get("gelled_at_s") and (first_gel_s is None or cl["gelled_at_s"] < first_gel_s):
                first_gel_s = cl["gelled_at_s"]
            if cl.get("gel_applied") and cl["client_id"] not in coat_eta:
                coat_eta[cl["client_id"]] = (cl["gelled_at_s"], cl.get("eta_p10_s"),
                                             cl.get("eta_p50_s"))
        # iteration 3: homes WAITING behind a blocked road (dispatch's
        # no_safe_corridor / no_safe_egress) with at least 1 h before the
        # pessimistic front, and the crews idle meanwhile (on the map, no
        # stop): idle-while-blocked crew-ticks = idle crews x [any blocked]
        blocked = 0
        for u in st.get("unserved") or []:
            if u.get("reason") in ("no_safe_corridor", "no_safe_egress"):
                eta = s._client_etas.get(u["client_id"])
                if (eta is not None and math.isfinite(eta.eta_p10_s)
                        and eta.eta_p10_s > now + 3600.0):
                    blocked += 1
        idle_now = sum(1 for c in crews_tick
                       if c["st"] == "available" and c["itin"] == 0)
        blocked_ticks += blocked
        if blocked:
            idle_blocked += idle_now
        log.append({"tick": k + 1, "t_h": round((now - t0) / 3600.0, 2),
                    "blocked_homes": blocked, "idle_crews": idle_now,
                    "acres": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
                    "served": sb["served"], "violations": sb["violations"],
                    "overruns": len(st.get("crew_overruns") or []),
                    "withdrawn": sb["withdrawn"], "relocations": sb["relocations"],
                    "left_map": sb.get("crews_left_map", 0),
                    "step_s": round(time.time() - step_t, 1),
                    "crews": crews_tick,
                    "notes": [n for n in (st.get("notes") or []) if "CREW" in n or "crew" in n][-4:]})
    sb = st["scoreboard"]
    # per-home protection lead (gel time vs the nominal fire's arrival)
    spec = s._tile.spec
    leads, unprotected, reached = [], 0, 0
    end = s._now_s
    for cl in st["clients"]:
        arr = math.inf
        for f in s._fires.values():
            if f.ens is None:
                continue
            v = geo.raster_sample(f.ens.members[0], geo.LatLon(cl["lat"], cl["lon"]))
            if v is not None and v < arr:
                arr = v
        if arr <= end:
            reached += 1
            if cl.get("gelled_at_s"):
                leads.append(round((arr - cl["gelled_at_s"]) / 3600.0, 2))
            else:
                unprotected += 1
    acres, head, fastest = head_stats(s, "sim-f1", hours)
    act, pas = crowned_acres(s, "sim-f1", hours)
    wx = s._wx
    winds = [smp.wind_speed_mps * 2.23694 for smp in wx.samples
             if t0 <= smp.t_s <= t0 + hours * 3600.0]
    # union area of every fire per hour (the state's fire area), the transit
    # rows of the path audit (crew_path_audit), and run flags: a stall (<= 20
    # acres at the end) and a head that reached the tile edge
    union_by_h = [row["acres"] for row in log if abs(row["t_h"] - round(row["t_h"])) < 1e-6
                  and row["t_h"] >= 1.0]
    transit_rows = sum(1 for r in overrun_rows
                       if r.get("transit") or r.get("mode") == "transit")
    f1 = s._fires["sim-f1"]
    half_w = min(spec.nrows, spec.ncols) * spec.cell_m / 2000.0
    edge_reach = (head[-1] >= half_w - 1.0) if head else False
    res = {
        "run": run_id, "loc": loc, "ign": ign, "intensity": inten, "hours": hours,
        "cfg": extra_cfg or {},
        "origins": [{"fid": fid, "lat": round(f.origin.lat, 5), "lon": round(f.origin.lon, 5),
                     "wind_from": f.wind_dir_deg, "t_h": round((f.t_ignite_s - t0) / 3600.0, 2)}
                    for fid, f in s._fires.items()],
        "safety": {"departure_violations_max": dep_viol_max, "violations_max": viol_max,
                   "overrun_rows": len(overrun_rows), "transit_rows": transit_rows,
                   "overrun_detail": overrun_rows[:20],
                   "withdrawn": sb["withdrawn"], "relocations": sb["relocations"],
                   "left_map": sb.get("crews_left_map", 0),
                   "closest_p10_margin_h": {k3: v for k3, v in sorted(min_margin.items())}},
        "service": {"served": sb["served"], "outcomes": sb.get("outcomes"),
                    "unserved_by_reason": sb.get("unserved_by_reason"),
                    "first_gel_h": None if first_gel_s is None else round((first_gel_s - t0) / 3600.0, 2),
                    "homes_reached": reached, "reached_unprotected": unprotected,
                    "protection_lead_h": sorted(leads)},
        "crews": {"status_ticks": status_counts,
                  "km_driven": {k3: round(v / 1000.0, 1) for k3, v in sorted(crew_dist.items())},
                  "idle_while_blocked": idle_blocked,
                  "blocked_home_ticks": blocked_ticks},
        "fire": {"acres_by_h": acres, "head_km_by_h": head, "fastest_head_kmh": fastest,
                 "wind_mph_mean": round(sum(winds) / len(winds), 1) if winds else None,
                 "wind_mph_max": round(max(winds), 1) if winds else None,
                 "crowned_acres": {"active": act, "passive": pas},
                 "union_acres_by_h": union_by_h,
                 "flags": {"stall": bool(union_by_h and union_by_h[-1] <= 20),
                           "edge_reach": bool(edge_reach)}},
        "wall_s": round(time.time() - t_wall, 1),
    }
    rep_ = st.get("report") or {}
    nr = rep_.get("nearest") or {}
    res["report"] = {k3: rep_.get(k3) for k3 in (
        "homes", "coated", "reached", "reached_coated", "reached_uncoated",
        "caught")}
    res["report"]["nearest_min"] = nr.get("minutes")
    res["report"]["nearest_kind"] = nr.get("kind")
    adv10 = sorted(round((e10 - g) / 3600.0, 2) for g, e10, _e50 in coat_eta.values()
                   if g is not None and e10 is not None)
    adv50 = sorted(round((e50 - g) / 3600.0, 2) for g, _e10, e50 in coat_eta.values()
                   if g is not None and e50 is not None)
    res["coat_window"] = {
        "coated": len(coat_eta),
        "over_12h_vs_p10": sum(1 for a in adv10 if a > 12.05),
        "over_12h_vs_p50": sum(1 for a in adv50 if a > 12.05),
        "advance_p10_h": adv10}
    res["proximity_tags"] = dict(sorted(prox_tags.items()))
    res["step_s_mean"] = round(sum(r3["step_s"] for r3 in log[1:]) / max(1, len(log) - 1), 3)
    if start_hour is not None:
        res["start_local_hour"] = start_hour
    os.makedirs(os.path.join(out_dir, "ticks"), exist_ok=True)
    with open(os.path.join(out_dir, "ticks", run_id.replace("|", "__") + ".json"), "w",
              encoding="utf-8") as fh:
        json.dump(log, fh, default=str)
    with mgr._lock:
        mgr._sessions.clear()
    return res


def main(argv):
    out_dir = argv[0]
    workers, hours, only, extra = 1, 12.0, None, None
    start_hour = None
    i = 1
    while i < len(argv):
        if argv[i] == "--workers":
            workers = int(argv[i + 1]); i += 2
        elif argv[i] == "--start-hour":
            start_hour = float(argv[i + 1]); i += 2
            if not 0.0 <= start_hour < 24.0:
                raise SystemExit("--start-hour must be within [0, 24)")
        elif argv[i] == "--hours":
            hours = float(argv[i + 1]); i += 2
        elif argv[i] == "--only":
            only = set(argv[i + 1].split(",")); i += 2
        elif argv[i] == "--cfg":
            extra = json.loads(argv[i + 1]); i += 2
        else:
            raise SystemExit(f"unknown arg {argv[i]}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "runs.jsonl")
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                done.add(json.loads(line)["run"])
    todo = [r for r in runs() if r[0] not in done and (not only or r[0] in only)]
    print(f"{len(todo)} runs -> {path}", flush=True)
    args = [r + (hours, extra, out_dir)
            + ((start_hour,) if start_hour is not None else ()) for r in todo]
    if workers <= 1:
        it = map(run_one, args)
    else:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(workers)
        it = pool.imap_unordered(run_one, args)
    for res in it:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True, default=str) + "\n")
        sf = res.get("safety", {})
        print(f"{res['run']:40s} served {res.get('service', {}).get('served')} viol "
              f"{sf.get('violations_max')} ovr {sf.get('overrun_rows')} wd {sf.get('withdrawn')} "
              f"rel {sf.get('relocations')} acres12 {(res.get('fire') or {}).get('acres_by_h', [None])[-1]} "
              f"{res.get('wall_s')} s {res.get('error', '')}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
