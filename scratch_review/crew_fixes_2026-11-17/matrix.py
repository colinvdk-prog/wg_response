"""(Copied 2026-11-17 from scratch_review/crew_program_review_2026-11-11/A_safety/
matrix.py for the crew-program fixes; ROOT is this worktree, --cfg JSON adds
ResponseConfig overrides to the page payload. NOT product code.)

Lane A (crew safety) review 2026-11-11 — fire matrix on NON-valley maps.

Builds every session through server.SimSessionManager exactly as the public
page does (scenario preset + hold_h 12, hold_per_home, proximity_first,
crews_3h 0, spotting / grass realism on, fire_zone), then ticks the session
(15-min steps) and records the full safety record per run:

  * every crew-overrun row (no truncation), each classified at its own pass
    time: own-cell fuel, own nominal arrival, own RAW P10, eroded P10, the
    cell that sets the eroded value (its nominal + age), whether any cell
    within the erosion radius is burning now (nominal in the last 2 h)
  * departure violations, withdrawals, relocations, crews off the map,
    closest P10 margin per crew
  * per-tick crew log (mode, status, pos, zone) for tracing
  * WAITING-CREW EXITS (question 3): for every idle crew at a catalog zone,
    the zone's keep-until (SEM 38 _dest_keep_until) each tick; flagged when a
    crew is still idle there after keep-until passed

Read-only on the repo: nothing tracked is modified; no monkeypatching here
except an optional hook (WAIT_RULE=1) that tests the waiting-crew rule.

Usage: python matrix.py OUT_DIR [--workers N] [--only id1,id2] [--hours H]
       [--set NAME]
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

PAGE = {"hold_h": 12, "hold_per_home": True, "proximity_first": True,
        "crews_3h": 0, "spotting": True, "grass_realism": True}

SCEN = {
    "malibu_ca": "malibu_santa_ana",
    "paradise_ca": "paradise_ne_wind",
    "santa_rosa_ca": "santa_rosa_diablo",
    "boulder_co": "boulder_chinook",
    "park_city_ut": "park_city_windstorm",
    "rancho_santa_fe_ca": "rancho_santa_fe_santa_ana",
    "aspen_co": "aspen_southwesterly",
    "glenwood_springs_co": "glenwood_springs_westerly",
    "basalt_carbondale_co": "basalt_carbondale_southwesterly",
    "vail_co": "vail_westerly",
    "beaver_creek_co": "beaver_creek_westerly",
}


def runs(which="main"):
    out = []
    if which == "main":
        maps = ["malibu_ca", "paradise_ca", "santa_rosa_ca", "boulder_co",
                "park_city_ut", "rancho_santa_fe_ca", "aspen_co",
                "glenwood_springs_co"]
        for m in maps:
            out.append((f"{m}|std|canon|extreme", m, "standard", "canon", "extreme", 12.0))
            out.append((f"{m}|std|canon|high", m, "standard", "canon", "high", 12.0))
            out.append((f"{m}|std|side|extreme", m, "standard", "side", "extreme", 12.0))
        for m in ["malibu_ca", "paradise_ca", "santa_rosa_ca", "park_city_ut",
                  "boulder_co"]:
            out.append((f"{m}|ext|canon|extreme", m, "extended", "canon", "extreme", 12.0))
        out.append(("paradise_ca|ext|far|high", "paradise_ca", "extended", "far", "high", 24.0))
        out.append(("malibu_ca|std|canon|extreme|24h", "malibu_ca", "standard", "canon", "extreme", 24.0))
        out.append(("park_city_ut|std|side|extreme|24h", "park_city_ut", "standard", "side", "extreme", 24.0))
    elif which == "valley24":
        out.append(("vail_co|std|canon|extreme|24h", "vail_co", "standard", "canon", "extreme", 24.0))
        out.append(("beaver_creek_co|std|canon|extreme|24h", "beaver_creek_co", "standard", "canon", "extreme", 24.0))
    elif which == "more24":
        # 2026-11-17: 24-h runs of more maps (the long-fire waiting defect)
        for m in ("aspen_co", "glenwood_springs_co", "paradise_ca", "boulder_co"):
            out.append((f"{m}|std|canon|extreme|24h", m, "standard", "canon", "extreme", 24.0))
        for m in ("vail_co", "beaver_creek_co"):
            out.append((f"{m}|std|side|extreme|24h", m, "standard", "side", "extreme", 24.0))
            out.append((f"{m}|std|canon|high|24h", m, "standard", "canon", "high", 24.0))
    return out


def _burnable_start(s, bearing, dist):
    from wgresponse import geo
    from wgresponse.fuels import is_burnable
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
            if is_burnable(int(round(s._tile.fuel.get(*rc)))):
                return q
    raise RuntimeError("no burnable start")


EXTRA_CFG = {}


def build(loc, zone, ign, inten):
    from wgresponse import server, geo
    mgr = server.SimSessionManager()
    payload = dict(PAGE, scenario=SCEN[loc], fire_zone=zone)
    if EXTRA_CFG:
        payload["cfg"] = dict(EXTRA_CFG)
    code, out = mgr.new_session(payload)
    if code != 200:
        raise RuntimeError(out)
    s = mgr._entry(out["session_id"]).session
    f = s._fires.get("sim-f1")
    if f is None:
        raise RuntimeError("scenario lit no fire")
    canon_origin, canon_wind = f.origin, f.wind_dir_deg
    s.reset()
    if ign == "canon":
        s.ignite(canon_origin.lat, canon_origin.lon, intensity=inten,
                 wind_dir_deg=canon_wind)
    elif ign == "side":
        # a fire 90 deg clockwise of the canonical bearing, the wind from it
        b = (canon_wind + 90.0) % 360.0
        p = _burnable_start(s, b, 6000.0)
        s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=b)
    elif ign == "far":
        b = canon_wind
        p = _burnable_start(s, b, 13500.0)
        s.ignite(p.lat, p.lon, intensity=inten, wind_dir_deg=b)
    return mgr, s


def classify(s, lat, lon, t):
    """Where a row's exposure comes from."""
    from wgresponse import geo
    from wgresponse.fuels import is_burnable
    pos = geo.LatLon(lat, lon)
    spec = s._tile.spec
    rc = geo.latlon_to_rc(spec, pos)
    out = {}
    if rc is None:
        return {"off_tile": True}
    r0, c0 = rc
    fuel = int(round(s._tile.fuel.get(r0, c0)))
    out["fuel"] = fuel
    out["burnable"] = bool(is_burnable(fuel))
    rad = int(math.ceil(s._cfg.corridor_erosion_m / spec.cell_m))
    nc = spec.ncols
    best = None
    for fid in s._fire_order:
        f = s._fires[fid]
        if f.ens is None:
            continue
        nom = f.ens.members[0].data
        p10 = f.ens.p10.data
        er = s._eroded_p10(f)
        i0 = r0 * nc + c0
        out.setdefault("own", []).append({
            "fid": fid, "nom": None if math.isinf(nom[i0]) else round((nom[i0] - t) / 60.0, 1),
            "raw_p10": None if math.isinf(p10[i0]) else round((p10[i0] - t) / 60.0, 1),
            "eroded_p10": None if (er is None or math.isinf(er.data[i0])) else round((er.data[i0] - t) / 60.0, 1)})
        burning_near = 0
        old_near = 0
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                rr, cc = r0 + dr, c0 + dc
                if not (0 <= rr < spec.nrows and 0 <= cc < nc):
                    continue
                if math.hypot(dr, dc) * spec.cell_m > s._cfg.corridor_erosion_m + spec.cell_m:
                    continue
                i = rr * nc + cc
                v = p10[i]
                if best is None or v < best[0]:
                    best = (v, nom[i], fid, dr, dc)
                if not math.isinf(nom[i]):
                    if t - 7200.0 <= nom[i] <= t:
                        burning_near += 1
                    elif nom[i] < t - 7200.0:
                        old_near += 1
        out.setdefault("near", []).append({"fid": fid, "burning_2h_cells": burning_near,
                                           "old_burned_cells": old_near})
    if best is not None:
        v, n, fid, dr, dc = best
        out["eroded_source"] = {
            "fid": fid, "d_m": round(math.hypot(dr, dc) * spec.cell_m),
            "p10_min_rel": None if math.isinf(v) else round((v - t) / 60.0, 1),
            "nom_min_rel": None if math.isinf(n) else round((n - t) / 60.0, 1)}
    return out


WAIT_RULE = os.environ.get("WAIT_RULE") == "1"


def wait_rule_hook(s):
    """The candidate waiting-crew rule (question 3): an idle crew at a
    catalog zone whose keep-until (SEM 38) falls within le_s + one step
    leaves by the 28b last-exit choice now, whatever its own cell's life."""
    from wgresponse import geo
    now = s._now_s
    lead_s = s._judge_lead_s()
    le_s = s._cfg.escape_last_exit_lead_min * 60.0
    dur_s = s._cfg.egress_durable_min_h * 3600.0
    fired = []
    for cid in sorted(s._crew_sims):
        cs = s._crew_sims[cid]
        if cid in s._left_map or not cs.idle():
            continue
        z = None
        for zz in s._loc.safe_zones:
            if geo.haversine_m(cs.pos, zz.loc) <= 250.0:
                z = zz
                break
        if z is None:
            # a crew parked on a road refuge ("escape"): the same law at its spot
            from wgresponse.contracts import SafeZone
            z = SafeZone(zone_id=f"pos:{cs.pos.lat:.6f},{cs.pos.lon:.6f}",
                         loc=cs.pos, name="parked spot")
        ku = s._dest_keep_until(z, cs.speed)
        if math.isinf(ku) or ku - now > le_s + lead_s:
            continue
        # keep-until is close: leave by the last exit (28b's choice, which
        # stays while some durable exit is open longer than le_s + step)
        zz, why = s._last_exit_choice(cid, cs, now, lead_s, dur_s, le_s)
        if zz is None and s._net_escape_capable():
            # 28b searches a road refuge only once the crew's own margin is
            # under half of dur_s; the keep-until says the way closes now,
            # so search it now (lawful only, durable dur_s, as 28b does)
            found = s._road_escape_search(
                cs.pos, now, cs.speed, lead_s, lawful_only=True,
                min_p10_s=now + dur_s + s._cfg.egress_buffer_min * 60.0,
                on_map_only=s._refuge_on_map())
            if found is not None:
                from wgresponse.contracts import SafeZone
                loc = found["loc"]
                s._escape_pins[(cs.pos.lat, cs.pos.lon, loc.lat, loc.lon)] = found["route"]
                zz = SafeZone(zone_id="escape", loc=loc, name="road escape point")
                why = "wait rule: keep-until reached, lawful durable road refuge"
        if zz is None and s._cfg.escape_pocket_exit:
            zz, why = s._pocket_exit_choice(cid, cs, now, lead_s, dur_s)
        if zz is not None and cs.pos != zz.loc:
            for d in cs.relocate_to(zz.loc, zz.zone_id):
                s._assigned.discard(d)
                s._superseded.discard(d)
                s._stop_rule.pop(d, None)
            s._n_relocations += 1
            if zz.zone_id == "escape":
                s._net_escape[cid] = zz.loc
            else:
                s._net_escape.pop(cid, None)
            s._relocating[cid] = "wait_rule"
            fired.append((cid, z.zone_id, zz.zone_id, round((ku - now) / 60.0, 1), why))
    return fired


def run_one(args):
    run_id, loc, zone, ign, inten, hours, out_dir = args[:7]
    if len(args) > 7 and args[7]:
        EXTRA_CFG.clear()
        EXTRA_CFG.update(args[7])
    t_wall = time.time()
    try:
        mgr, s = build(loc, zone, ign, inten)
    except Exception as exc:
        return {"run": run_id, "error": repr(exc), "tb": traceback.format_exc()}
    from wgresponse import geo
    ticks = int(round(hours * 4))
    t0 = s._now_s
    overruns, log, waits, wait_fired = [], [], [], []
    min_margin = {}
    dep_viol_max = 0
    st = None
    notes_seen = len(s._notes)
    rebases = []
    anchors = {fid: None for fid in s._fires}
    try:
        for k in range(ticks):
            if WAIT_RULE:
                for w in wait_rule_hook(s):
                    wait_fired.append({"tick": k, "t_h": round((s._now_s - t0) / 3600.0, 2),
                                       "crew": w[0], "from": w[1], "to": w[2],
                                       "keep_until_min": w[3], "why": w[4]})
            st = s.tick()
            now = st["sim_now_s"]
            for fid, f in s._fires.items():
                a = getattr(f, "anchor_t0_s", None)
                if anchors.get(fid) != a:
                    if anchors.get(fid) is not None:
                        rebases.append({"fid": fid, "t_h": round((now - t0) / 3600.0, 2),
                                        "anchor_h": round((a - t0) / 3600.0, 2)})
                    anchors[fid] = a
            dep_viol_max = max(dep_viol_max, len(s._violations))
            for r in st.get("crew_overruns") or []:
                t_pass = r.get("at_s") or now
                row = dict(r, tick=k + 1, t_h=round((now - t0) / 3600.0, 2))
                row["cls"] = classify(s, r["lat"], r["lon"], t_pass)
                overruns.append(row)
            crews_tick = []
            for c in st["crews"]:
                cid = c["crew_id"]
                cs = s._crew_sims.get(cid)
                mode = getattr(cs, "_mode", None)
                margin_h = None
                if cs is not None and not c.get("inbound") and not c.get("left_map"):
                    nom, p10 = s._exposure_at(cs.pos, now)
                    if not math.isinf(p10):
                        margin_h = round((p10 - now) / 3600.0, 2)
                        if cid not in min_margin or margin_h < min_margin[cid]:
                            min_margin[cid] = margin_h
                    # waiting at a catalog zone: its keep-until
                    if cs.idle():
                        from wgresponse.contracts import SafeZone
                        zsel = None
                        for zz in s._loc.safe_zones:
                            if geo.haversine_m(cs.pos, zz.loc) <= 250.0:
                                zsel = zz
                                break
                        if zsel is None:
                            zsel = SafeZone(zone_id=f"pos:{cs.pos.lat:.6f},{cs.pos.lon:.6f}",
                                            loc=cs.pos, name="parked spot")
                        ku = s._dest_keep_until(zsel, cs.speed)
                        waits.append({"tick": k + 1, "crew": cid, "zone": zsel.zone_id,
                                      "ku_min": None if math.isinf(ku) else round((ku - now) / 60.0, 1),
                                      "own_p10_h": margin_h})
                crews_tick.append({"id": cid, "st": c["status"], "mode": mode,
                                   "lat": c.get("lat"), "lon": c.get("lon"),
                                   "zone": c.get("egress_zone_id") or c.get("anchor_zone_id"),
                                   "reloc": c.get("relocating"), "left": c.get("left_map"),
                                   "p10_margin_h": margin_h})
            new_notes = [n for n in s._notes[notes_seen:]]
            notes_seen = len(s._notes)
            log.append({"tick": k + 1, "t_h": round((now - t0) / 3600.0, 2),
                        "crews": crews_tick,
                        "notes": [n for n in new_notes if ("crew" in n.lower() or "SAFETY" in n)][-12:]})
    except Exception as exc:
        return {"run": run_id, "error": repr(exc), "tb": traceback.format_exc(),
                "ticks_done": len(log)}
    sb = st["scoreboard"]
    res = {
        "run": run_id, "loc": loc, "zone": zone, "ign": ign, "intensity": inten,
        "hours": hours, "fire_zone_loaded": st.get("fire_zone"),
        "horizon_h": s._cfg.horizon_h, "rebases": rebases,
        "origins": [{"fid": fid, "lat": round(f.origin.lat, 5), "lon": round(f.origin.lon, 5),
                     "wind_from": f.wind_dir_deg} for fid, f in s._fires.items()],
        "safety": {"departure_violations_max": dep_viol_max,
                   "violations_score": sb["violations"],
                   "overrun_rows": len(overruns),
                   "overruns_total": sb.get("crew_overruns_total"),
                   "transit_total": sb.get("transit_overruns_total"),
                   "withdrawn": sb["withdrawn"], "relocations": sb["relocations"],
                   "left_map": sb.get("crews_left_map", 0),
                   "closest_p10_margin_h": dict(sorted(min_margin.items()))},
        "overruns": overruns,
        "served": sb["served"], "n_clients": len(st["clients"]),
        "n_crews": len(st["crews"]),
        "acres_end": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
        "wait_fired": wait_fired,
        "wall_s": round(time.time() - t_wall, 1),
    }
    os.makedirs(os.path.join(out_dir, "ticks"), exist_ok=True)
    with open(os.path.join(out_dir, "ticks", run_id.replace("|", "__") + ".json"), "w",
              encoding="utf-8") as fh:
        json.dump({"log": log, "waits": waits}, fh, default=str)
    with mgr._lock:
        mgr._sessions.clear()
    return res


def main(argv):
    out_dir = argv[0]
    workers, only, which = 1, None, "main"
    extra = None
    hours_override = None
    i = 1
    while i < len(argv):
        if argv[i] == "--workers":
            workers = int(argv[i + 1]); i += 2
        elif argv[i] == "--only":
            only = set(argv[i + 1].split(",")); i += 2
        elif argv[i] == "--set":
            which = argv[i + 1]; i += 2
        elif argv[i] == "--hours":
            hours_override = float(argv[i + 1]); i += 2
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
    todo = [r for r in runs(which) if r[0] not in done and (not only or r[0] in only)]
    if hours_override:
        todo = [r[:5] + (hours_override,) for r in todo]
    print(f"{len(todo)} runs -> {path}", flush=True)
    args = [r + (out_dir, extra) for r in todo]
    if workers <= 1:
        it = map(run_one, args)
    else:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(workers, maxtasksperchild=1)
        it = pool.imap_unordered(run_one, args)
    for res in it:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True, default=str) + "\n")
        sf = res.get("safety", {})
        deep = [o.get("margin_min") for o in res.get("overruns", []) if o.get("margin_min") is not None]
        print(f"{res['run']:42s} served {res.get('served')}/{res.get('n_clients')} "
              f"dv {sf.get('departure_violations_max')} ovr {sf.get('overrun_rows')} "
              f"worst {min(deep) if deep else None} wd {sf.get('withdrawn')} rel {sf.get('relocations')} "
              f"left {sf.get('left_map')} acres {res.get('acres_end')} rb {res.get('rebases')} "
              f"{res.get('wall_s')} s {res.get('error', '')}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
