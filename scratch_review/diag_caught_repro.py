"""diag_caught_repro — DIAGNOSIS ONLY (read-only).

Reproduces the founder's safety bug in-process (NO :8878 server touched):
crews idle/AVAILABLE standing INSIDE the advancing fire while
scoreboard.violations stays 0, because _run_audit only inspects serviced-stop
DEPARTURES, never live crew positions / idle crews / static safe zones.

Paradise extended tile, ~64 clients, 5 crews, extreme fire, driven tick-by-tick.
For every crew every tick we independently compute (from the SAME engine physics
the audit uses):
  * BURNED (ground truth): nominal ensemble member arrival at crew cell <= now
  * IMMINENT: eroded-P10 arrival at crew cell < now + egress_buffer
and cross-check against state()['scoreboard']['violations'].
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wgresponse import geo, spread                       # noqa: E402
from wgresponse.simui import SimSession                  # noqa: E402


def crew_fire_exposure(session, cs):
    """(min_nominal_arrival_s, min_p10_s) at the crew's current cell across all
    fires, using the exact engine samplers the audit/dispatch use."""
    cfg = session._cfg
    now = session._now_s
    best_nom = float("inf")
    best_p10 = float("inf")
    for f in session._fires.values():
        if f.ens is None:
            continue
        nom = geo.raster_sample(f.ens.members[0], cs.pos)   # sim ground truth
        if nom is not None and nom < best_nom:
            best_nom = nom
        eta = spread.eta_at(f.ens, cs.pos,
                            fallback_radius_m=cfg.ember_threat_radius_m)
        if eta.eta_p10_s < best_p10:
            best_p10 = eta.eta_p10_s
    return best_nom, best_p10


def nearest_zone(session, loc):
    """(zone, dist_m) nearest safe zone to loc."""
    best = None
    for z in session._loc.safe_zones:
        d = geo.haversine_m(loc, z.loc)
        if best is None or d < best[1]:
            best = (z, d)
    return best


def zone_exposure(session, zone):
    cfg = session._cfg
    now = session._now_s
    best_nom = float("inf")
    best_p10 = float("inf")
    for f in session._fires.values():
        if f.ens is None:
            continue
        nom = geo.raster_sample(f.ens.members[0], zone.loc)
        if nom is not None and nom < best_nom:
            best_nom = nom
        eta = spread.eta_at(f.ens, zone.loc,
                            fallback_radius_m=cfg.ember_threat_radius_m)
        if eta.eta_p10_s < best_p10:
            best_p10 = eta.eta_p10_s
    return best_nom, best_p10


def main():
    s = SimSession("paradise_ca", n_clients=64, n_crews=5, seed="caught-1",
                   fire_zone="extended")
    cfg = s._cfg
    buf_s = cfg.egress_buffer_min * 60.0
    print(f"# cfg: fire_zone={s._fire_zone} horizon_h={cfg.horizon_h} "
          f"replan_period_s={cfg.replan_period_s} egress_buffer_min="
          f"{cfg.egress_buffer_min} ember_buffer_min={cfg.ember_buffer_min} "
          f"grid_cell_m={cfg.grid_cell_m} ember_threat_radius_m="
          f"{cfg.ember_threat_radius_m} roads_on={s._road_graph is not None}")
    spec = s._tile.spec
    print(f"# tile spec rows={spec.nrows} cols={spec.ncols} "
          f"cell_m~{cfg.grid_cell_m}")
    print(f"# center={s._loc.center}  safe_zones=" +
          ", ".join(f"{z.zone_id}({z.loc.lat:.3f},{z.loc.lon:.3f})"
                    for z in s._loc.safe_zones))

    # Ignite an extreme fire NE (upwind of the canonical FROM-45 NE foehn) so it
    # drives SW across the central community and on toward the SW/S/SE zones.
    fid = s.ignite(39.83, -121.52, intensity="extreme")
    print(f"# ignited {fid} at (39.83,-121.52) extreme, "
          f"wind FROM {s._wind_dir_deg} deg\n")

    t0 = s._t0_s
    MAX_TICKS = 320
    hit_ticks = []                 # every tick with >=1 exposed crew
    idle_burned_hit = None         # founder's EXACT bug: idle/avail crew BURNED
    any_hit = None                 # earliest exposed crew while violations==0
    for _ in range(MAX_TICKS):
        st = s.tick()
        now = s._now_s
        viol = st["scoreboard"]["violations"]
        offenders = []
        for cid in sorted(s._crew_sims):
            cs = s._crew_sims[cid]
            nom, p10 = crew_fire_exposure(s, cs)
            burned = nom <= now
            imminent = p10 < now + buf_s
            if burned or imminent:
                z, zd = nearest_zone(s, cs.pos)
                offenders.append({
                    "cid": cid, "status": cs.status.value, "mode": cs._mode,
                    "burned": burned, "imminent": imminent,
                    "nom_arr_h": (nom - now) / 3600.0 if nom != float("inf") else None,
                    "p10_arr_h": (p10 - now) / 3600.0 if p10 != float("inf") else None,
                    "at_zone": z.zone_id, "zone_dist_m": zd,
                    "anchor": s._crew_anchor[cid],
                    "egress_zone_id": cs.egress_zone_id,
                    "itinerary": len(cs.itinerary),
                })
        if offenders:
            hit_ticks.append((s._tick_n, now, viol, offenders,
                              dict(st["scoreboard"])))
            if any_hit is None and viol == 0:
                any_hit = (s._tick_n, now, viol, offenders,
                           dict(st["scoreboard"]), st)
            idle_burned = [o for o in offenders
                           if o["status"] == "available" and o["burned"]]
            if idle_burned_hit is None and viol == 0 and idle_burned:
                idle_burned_hit = (s._tick_n, now, viol, offenders,
                                   dict(st["scoreboard"]), st)

    # Prefer the founder's EXACT end-state (idle/available crew standing in
    # the burned mask); fall back to the earliest exposed-crew tick.
    first_hit = idle_burned_hit or any_hit
    if first_hit is None:
        print("!! NO reproduction in", MAX_TICKS, "ticks")
        return
    if idle_burned_hit is not None:
        print("### milestone: earliest IDLE/AVAILABLE crew standing in the "
              "BURNED mask while violations=0 (founder's exact bug)\n")
    else:
        print("### milestone: earliest exposed crew while violations=0 "
              "(no fully-idle-burned tick in window)\n")

    tn, now, viol, offenders, sb, st = first_hit
    elapsed_h = (now - t0) / 3600.0
    now  # keep name for helpers below
    print(f"# elapsed since sim start at this tick: {elapsed_h:.1f} h")
    print("=" * 78)
    print(f"BUG CONFIRMED at tick {tn} (sim_now={now:.0f}s = {now/3600.0:.1f} h)")
    print(f"scoreboard.violations = {viol}   (MUST be >0 -- it is not)")
    print(f"scoreboard = {sb}")
    print("=" * 78)
    print(f"\nCrews standing in / imminently under the fire while violations={viol}:")
    for o in offenders:
        tag = "BURNED(in fire)" if o["burned"] else "IMMINENT"
        print(f"  {o['cid']}: status={o['status']} mode={o['mode']} {tag} "
              f"nom_arr_h={o['nom_arr_h']} p10_arr_h="
              f"{None if o['p10_arr_h'] is None else round(o['p10_arr_h'],2)} "
              f"nearest_zone={o['at_zone']}@{o['zone_dist_m']:.0f}m "
              f"anchor={o['anchor']} egress_zone={o['egress_zone_id']!r} "
              f"itinerary_len={o['itinerary']}")

    # --- classification a/b/c/d -------------------------------------------
    print("\n--- classification ---")
    at_zone_thresh_m = 200.0   # ~ one 90m/coarse cell: "resting AT the zone"
    a = b = c = 0
    for o in offenders:
        resting = o["status"] == "available"
        at_zone = o["zone_dist_m"] <= at_zone_thresh_m
        if resting and at_zone and o["at_zone"] == o["anchor"]:
            b += 1
            print(f"  {o['cid']}: (b) idle at ANCHOR safe zone "
                  f"{o['anchor']} which the fire overran")
        elif resting and at_zone:
            b += 1
            print(f"  {o['cid']}: (b) idle at a safe zone {o['at_zone']} "
                  f"(egress terminus) which the fire overran")
        elif resting:
            a += 1
            print(f"  {o['cid']}: (a) idle/overrun at its resting spot "
                  f"(not a zone; {o['zone_dist_m']:.0f} m from nearest)")
        else:
            print(f"  {o['cid']}: non-idle ({o['status']}/{o['mode']}) exposed "
                  f"-- monitored-state gap too")

    # (c) any safe zone overrun yet still an active egress/anchor target?
    print("\n--- safe-zone validity vs fire (case c) ---")
    targeted_anchor = {s._crew_anchor[cid] for cid in s._crew_sims}
    targeted_egress = {s._crew_sims[cid].egress_zone_id
                       for cid in s._crew_sims
                       if s._crew_sims[cid].egress_zone_id}
    for z in s._loc.safe_zones:
        znom, zp10 = zone_exposure(s, z)
        zburn = znom <= now
        zimm = zp10 < now + buf_s
        used = []
        if z.zone_id in targeted_anchor:
            used.append("anchor")
        if z.zone_id in targeted_egress:
            used.append("egress-target")
        flag = ("OVERRUN" if zburn else "IMMINENT" if zimm else "safe")
        note = ("  <-- (c) still used as " + "/".join(used)
                if (zburn or zimm) and used else "")
        print(f"  {z.zone_id}: {flag} nom_arr_h="
              f"{(znom-now)/3600.0 if znom!=float('inf') else None} "
              f"p10_arr_h={round((zp10-now)/3600.0,2) if zp10!=float('inf') else None} "
              f"used_as={used}{note}")

    # --- prove the audit is blind: it only saw serviced-stop departures ----
    print("\n--- why the audit reports 0 (the gap) ---")
    serviced = st["serviced"]
    audit = st["audit"]
    print(f"  len(serviced stops) = {len(serviced)}   "
          f"len(audit rows) = {len(audit)}   "
          f"(audit rows == serviced stops: {len(audit) == len(serviced)})")
    neg = [a for a in audit if a.get("violation")]
    print(f"  audit rows flagged violation = {len(neg)}")
    print(f"  withdrawn (safety withdrawals) = {len(st['withdrawn'])}")
    print("  => _run_audit iterates self._serviced ONLY; every offender above "
          "is an\n     idle/available crew with NO serviced-departure row being "
          "re-checked,\n     so its live position is never compared to the fire.")

    # persistence: print only when the exposed set CHANGES (compact) + prove
    # violations never leaves 0 across the whole run
    print("\n--- persistence: exposed-set transitions (violations stays 0 "
          "throughout) ---")
    max_burned = (0, None)
    prev_key = None
    max_viol = 0
    n_exposed_ticks = 0
    for rec in hit_ticks:
        rtn, rnow, rviol, roffs, rsb = rec
        n_exposed_ticks += 1
        max_viol = max(max_viol, rviol)
        burned_ids = [o["cid"] for o in roffs if o["burned"]]
        imm_ids = [o["cid"] for o in roffs if o["imminent"] and not o["burned"]]
        idle_burned = [o["cid"] for o in roffs
                       if o["burned"] and o["status"] == "available"]
        if len(idle_burned) > max_burned[0]:
            max_burned = (len(idle_burned), rtn)
        key = (tuple(burned_ids), tuple(imm_ids))
        if key != prev_key:
            print(f"  tick {rtn:>3} ({(rnow-t0)/3600.0:5.1f}h): violations="
                  f"{rviol} burned={burned_ids} imminent={imm_ids} "
                  f"idle&burned={idle_burned}")
            prev_key = key
    print(f"  ...")
    print(f"  ticks with >=1 exposed crew: {n_exposed_ticks} ; "
          f"MAX violations seen across ALL of them: {max_viol}")

    # end-state: the founder's report = ALL crews available/0 stops in fire
    print("\n--- END-STATE (final tick) — founder's report signature ---")
    fst = s.state()
    fnow = s._now_s
    fviol = fst["scoreboard"]["violations"]
    n_avail = n_avail_exposed = 0
    rows = []
    for cid in sorted(s._crew_sims):
        cs = s._crew_sims[cid]
        nom, p10 = crew_fire_exposure(s, cs)
        burned = nom <= fnow
        imminent = p10 < fnow + buf_s
        avail = cs.status.value == "available" and not cs.itinerary
        if avail:
            n_avail += 1
        if avail and (burned or imminent):
            n_avail_exposed += 1
        rows.append((cid, cs.status.value, len(cs.itinerary), burned, imminent))
    print(f"  final tick {s._tick_n} ({(fnow-t0)/3600.0:.1f}h): "
          f"violations={fviol}  crews available&idle={n_avail}/5  "
          f"of which in/near fire={n_avail_exposed}")
    for cid, statv, itin, burned, imminent in rows:
        cs = s._crew_sims[cid]
        z, zd = nearest_zone(s, cs.pos)
        tag = "BURNED" if burned else "imminent" if imminent else "clear"
        atzone = ("at " + z.zone_id if zd <= 200.0
                  else f"{zd/1000.0:.1f}km from {z.zone_id}")
        print(f"    {cid}: status={statv} stops={itin} egress_zone="
              f"{cs.egress_zone_id!r} anchor={s._crew_anchor[cid]} "
              f"pos={atzone} -> {tag}")
    print(f"\n  max idle&burned crews at any single tick: {max_burned[0]} "
          f"(tick {max_burned[1]})")


if __name__ == "__main__":
    main()
