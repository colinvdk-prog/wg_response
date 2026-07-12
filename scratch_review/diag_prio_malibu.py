"""DIAGNOSIS ONLY — reproduce the founder's Malibu sim-7 and dump the outcome.

Malibu, 48 clients, 3 crews, EXTREME fire near center. seed='sim-7' (the server
default seed for the 7th session == its id, wgresponse/server.py:258). Read-only:
drives SimSession in-process (no server, no disk writes).

Prints the outcome census so we can compare to the founder's live-session facts:
  13 IMPACTED (10 gelled-then-burned, 3 burned ungelled incl. c6 $1.1M);
  24 served/gelled; 6 threatened-not-impacted UNGELLED (p10 22-43h);
  inversion c47 (p10 36.4h $2.35M) gelled while c44 (p10 22.9h $1.4M) NOT.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "tiles")


def central_ignition(loc, tile):
    """A burnable cell as close to the tile CENTER as possible (founder: 'near
    center'). Spiral out from center until a burnable cell is found."""
    spec = tile.spec
    r0, c0 = spec.nrows // 2, spec.ncols // 2
    for rad in range(0, max(spec.nrows, spec.ncols)):
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                if max(abs(dr), abs(dc)) != rad:
                    continue
                r, c = r0 + dr, c0 + dc
                if 0 <= r < spec.nrows and 0 <= c < spec.ncols:
                    if is_burnable(int(round(tile.fuel.get(r, c)))):
                        return geo.rc_to_latlon(spec, r, c)
    raise RuntimeError("no burnable cell near center")


def run(seed="sim-7", n_clients=48, n_crews=3, ticks=80, intensity="extreme",
        ignition="center"):
    loc = simui.LOCATIONS["malibu_ca"]
    sess = simui.SimSession("malibu_ca", n_clients, n_crews, seed=seed,
                            tile_dir=TILE_DIR)
    if ignition == "center":
        p = central_ignition(loc, sess._tile)
    else:
        # wind-dir offset ignition (diag_util style), ~6 km from center
        import wgresponse.geo as g
        a = math.radians(loc.hazard_wind_dir_deg)
        dlat = (6000 * math.cos(a)) / 111_000.0
        dlon = (6000 * math.sin(a)) / (111_000.0 * math.cos(math.radians(loc.center.lat)))
        p = g.LatLon(loc.center.lat + dlat, loc.center.lon + dlon)
    fid = sess.ignite(p.lat, p.lon, intensity=intensity)
    print(f"seed={seed!r} clients={n_clients} crews={n_crews} intensity={intensity} "
          f"ignition@({p.lat:.4f},{p.lon:.4f}) fire={fid}")

    # per-client tracking across ticks
    impacted_at = {}          # client -> tick its p50 arrival first <= now
    gelled_at = {}            # client -> gelled_at_s
    last_p10 = {}
    last_p50 = {}
    value = {c.client_id: c.value_usd for c in sess._clients}

    for t in range(ticks):
        st = sess.tick()
        now = st["sim_now_s"]
        for cl in st["clients"]:
            cid = cl["client_id"]
            p50 = cl["eta_p50_s"]
            p10 = cl["eta_p10_s"]
            if p50 is not None:
                last_p50[cid] = p50
                if p50 <= now and cid not in impacted_at:
                    impacted_at[cid] = t
            if p10 is not None:
                last_p10[cid] = p10
            if cl["gel_applied"] and cid not in gelled_at:
                gelled_at[cid] = cl["gelled_at_s"]

    st = sess.state()
    served_ids = {s["client_id"] for s in st["serviced"]}
    impacted = set(impacted_at)
    # protected-then-impacted vs burned-ungelled
    prot_then_imp = sorted(i for i in impacted if i in gelled_at)
    burned_ungelled = sorted(i for i in impacted if i not in gelled_at)
    # threatened but never impacted, ungelled (the founder's "comfortable" set)
    threatened = set(last_p50)   # ever had a finite p50
    threat_not_imp_ungelled = sorted(
        c for c in threatened if c not in impacted and c not in served_ids)

    print(f"\n=== OUTCOME after {ticks} ticks (sim_now +{(st['sim_now_s']-st['t0_s'])/3600:.1f}h) ===")
    print(f"  served/gelled          : {len(served_ids)}")
    print(f"  IMPACTED (p50<=now)    : {len(impacted)}")
    print(f"    - gelled-then-impacted (protected): {len(prot_then_imp)}  {prot_then_imp}")
    print(f"    - burned UNGELLED                 : {len(burned_ungelled)}  {burned_ungelled}")
    print(f"  threatened-not-impacted UNGELLED    : {len(threat_not_imp_ungelled)}")
    print(f"  scoreboard: {st['scoreboard']}")

    def row(cid):
        p10 = last_p10.get(cid)
        p50 = last_p50.get(cid)
        p10h = "inf" if p10 is None else f"{(p10-st['t0_s'])/3600:.1f}"
        p50h = "inf" if p50 is None else f"{(p50-st['t0_s'])/3600:.1f}"
        tag = ("GELLED" if cid in served_ids else "-")
        return (f"    {cid:9s} ${value.get(cid,0)/1e6:.2f}M  "
                f"final p10 +{p10h}h  p50 +{p50h}h  {tag}"
                f"{'  IMPACTED@t'+str(impacted_at[cid]) if cid in impacted_at else ''}")

    # founder's referenced clients
    print("\n  founder-referenced clients:")
    for cid in ("sim-c6", "sim-c47", "sim-c44", "sim-c42"):
        if cid in value:
            print(row(cid))

    print("\n  threatened-not-impacted-UNGELLED (comfortable-margin, should be savable):")
    for cid in threat_not_imp_ungelled:
        print(row(cid))
    print("\n  burned-UNGELLED:")
    for cid in burned_ungelled:
        print(row(cid))
    return sess, dict(impacted_at=impacted_at, gelled_at=gelled_at,
                      last_p10=last_p10, last_p50=last_p50, served=served_ids,
                      value=value)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "center"
    run(ignition=mode)
