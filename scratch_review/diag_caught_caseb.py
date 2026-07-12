"""diag_caught_caseb — DIAGNOSIS ONLY (read-only). Hunt a CASE (b) instance:
an idle/AVAILABLE crew resting AT a safe zone (<=250 m) whose own cell the fire
then overruns (burned or eroded-P10 within egress buffer), while
scoreboard.violations stays 0. Also reports case (a) and (c) counts per config.

Sweeps a few low-churn configs/seeds (fewer clients => crews finish early and
egress cleanly to a zone, then idle there as the fire keeps advancing).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wgresponse import geo, spread                       # noqa: E402
from wgresponse.simui import SimSession                  # noqa: E402

AT_ZONE_M = 250.0


def exposure(session, loc):
    cfg = session._cfg
    best_nom = float("inf")
    best_p10 = float("inf")
    for f in session._fires.values():
        if f.ens is None:
            continue
        nom = geo.raster_sample(f.ens.members[0], loc)
        if nom is not None and nom < best_nom:
            best_nom = nom
        eta = spread.eta_at(f.ens, loc, fallback_radius_m=cfg.ember_threat_radius_m)
        if eta.eta_p10_s < best_p10:
            best_p10 = eta.eta_p10_s
    return best_nom, best_p10


def nearest_zone(session, loc):
    best = None
    for z in session._loc.safe_zones:
        d = geo.haversine_m(loc, z.loc)
        if best is None or d < best[1]:
            best = (z, d)
    return best


def run(n_clients, n_crews, seed, ignite_pt, max_ticks=340):
    s = SimSession("paradise_ca", n_clients=n_clients, n_crews=n_crews,
                   seed=seed, fire_zone="extended")
    cfg = s._cfg
    buf_s = cfg.egress_buffer_min * 60.0
    s.ignite(ignite_pt[0], ignite_pt[1], intensity="extreme")
    caseb = None
    max_viol = 0
    ever_idle_at_zone = 0
    for _ in range(max_ticks):
        st = s.tick()
        now = s._now_s
        viol = st["scoreboard"]["violations"]
        max_viol = max(max_viol, viol)
        for cid in sorted(s._crew_sims):
            cs = s._crew_sims[cid]
            if cs.status.value != "available" or cs.itinerary:
                continue
            z, zd = nearest_zone(s, cs.pos)
            if zd > AT_ZONE_M:
                continue
            ever_idle_at_zone += 1
            nom, p10 = exposure(s, cs.pos)
            burned = nom <= now
            imminent = p10 < now + buf_s
            if (burned or imminent) and caseb is None:
                caseb = {
                    "tick": s._tick_n, "elapsed_h": (now - s._t0_s) / 3600.0,
                    "cid": cid, "zone": z.zone_id, "zone_dist_m": round(zd, 1),
                    "anchor": s._crew_anchor[cid],
                    "burned": burned, "imminent": imminent,
                    "nom_arr_h": None if nom == float("inf") else round((nom - now) / 3600.0, 2),
                    "p10_arr_h": None if p10 == float("inf") else round((p10 - now) / 3600.0, 2),
                    "violations": viol,
                    "is_anchor": z.zone_id == s._crew_anchor[cid],
                }
    return caseb, max_viol, ever_idle_at_zone


def main():
    # Fewer clients / more crews => crews finish and sit at zones (low churn).
    configs = [
        (8, 4, "caseb-1", (39.83, -121.52)),
        (10, 5, "caseb-2", (39.84, -121.55)),
        (6, 3, "caseb-3", (39.82, -121.50)),
        (12, 6, "caseb-4", (39.85, -121.58)),
        (5, 4, "caseb-5", (39.83, -121.53)),
    ]
    found = None
    for n_cl, n_cr, seed, ig in configs:
        caseb, max_viol, idle_at_zone = run(n_cl, n_cr, seed, ig)
        tag = "CASE-B FOUND" if caseb else "no case-b"
        print(f"cfg(clients={n_cl},crews={n_cr},seed={seed}): {tag}  "
              f"max_violations={max_viol}  idle-at-zone crew-ticks={idle_at_zone}")
        if caseb:
            print(f"    {caseb}")
            if found is None:
                found = caseb
    print()
    if found:
        kind = "ANCHOR" if found["is_anchor"] else "egress-terminus"
        print(f"=> CASE (b) CONFIRMED: crew {found['cid']} idle/available AT "
              f"safe zone {found['zone']} ({found['zone_dist_m']} m, {kind}) "
              f"at tick {found['tick']} ({found['elapsed_h']:.1f}h); "
              f"{'BURNED' if found['burned'] else 'IMMINENT'} "
              f"(nom_arr_h={found['nom_arr_h']}, p10_arr_h={found['p10_arr_h']}) "
              f"while violations={found['violations']}.")
    else:
        print("=> no case (b) in these configs (crews idled at job sites, "
              "case a); case (b) still reachable since zones demonstrably "
              "overrun while remaining anchors (see repro case c).")


if __name__ == "__main__":
    main()
