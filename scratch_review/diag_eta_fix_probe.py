"""diag_eta_fix_probe.py — DIAGNOSIS ONLY: validate fix option (i) in-principle.

Option (i): a client on an UNREACHED (non-burnable) cell inherits the EARLIEST
arrival of its burnable neighbours within R meters (ember/adjacency threat).
geo.erode_min already computes exactly that (morphological minimum filter over a
square window; UNREACHED=inf sources never pollute, finite burnable arrivals get
pulled inward). This probe shows how many currently-no-ETA NB homes would GAIN a
finite threat ETA + how early, at a few radii — WITHOUT modifying product code.

Run FROM REPO ROOT:  py -3 scratch_review/diag_eta_fix_probe.py
"""
from __future__ import annotations

import math

from wgresponse import geo, spread
from wgresponse.fuels import is_burnable
from wgresponse.simui import SimSession
from wgresponse.contracts import UNREACHED

SEED = "eta-gap-diag-1"


def main():
    sess = SimSession("malibu_ca", 48, 3, seed=SEED)
    tile = sess._tile
    ig = None
    # ignite nearest-burnable to center (same as diag_eta_gap)
    spec = tile.spec
    rc0 = geo.latlon_to_rc(spec, sess._loc.center)
    for rad in range(60):
        done = False
        for r in range(max(0, rc0[0] - rad), min(spec.nrows, rc0[0] + rad + 1)):
            for c in range(max(0, rc0[1] - rad), min(spec.ncols, rc0[1] + rad + 1)):
                if max(abs(r - rc0[0]), abs(c - rc0[1])) == rad and \
                        is_burnable(int(round(tile.fuel.get(r, c)))):
                    ig = geo.rc_to_latlon(spec, r, c)
                    done = True
                    break
            if done:
                break
        if done:
            break
    sess.ignite(ig.lat, ig.lon, intensity="extreme")
    for _ in range(120):
        sess.tick()

    fid = sess._fire_order[0]
    ens = sess._fires[fid].ens
    now = sess._now_s
    p10 = ens.p10

    # NB-cell (no-ETA) clients
    nb_clients = []
    for cl in sess._clients:
        rc = geo.latlon_to_rc(spec, cl.loc)
        code = int(round(tile.fuel.get(*rc)))
        if not is_burnable(code):
            direct = geo.raster_sample(p10, cl.loc)
            nb_clients.append((cl, rc, code, direct))

    print("=" * 78)
    print("FIX OPTION (i) PROBE — proximity fallback via geo.erode_min on p10")
    print("seed=%r  fire=%s  sim_now=+%.0fh  NB(no-ETA) clients=%d"
          % (SEED, fid, (now - sess._fires[fid].t_ignite_s) / 3600.0,
             len(nb_clients)))
    print("=" * 78)
    print("all %d NB clients currently have p10 = UNREACHED (inf): %s"
          % (len(nb_clients),
             all(math.isinf(d) for *_x, d in nb_clients)))

    for radius_m in (90.0, 180.0, 270.0, 450.0):
        eroded = geo.erode_min(p10, radius_m)
        gained = 0
        already_burning = 0
        details = []
        for cl, rc, code, _direct in nb_clients:
            v = geo.raster_sample(eroded, cl.loc)
            if not math.isinf(v):
                gained += 1
                lead_h = (v - now) / 3600.0
                if v <= now:
                    already_burning += 1
                details.append((cl.client_id, lead_h, v <= now))
        print("\n  R = %5.0f m (%.1f cells):  %d/%d NB homes gain a finite threat "
              "ETA  (%d already past-arrival = IMPACTED)"
              % (radius_m, radius_m / spec.cell_m, gained, len(nb_clients),
                 already_burning))
        for cid, lead_h, burning in sorted(details, key=lambda z: z[1])[:8]:
            tag = "IMPACTED (fire already at neighbour)" if burning \
                else "threat ETA in %+.1f h" % lead_h
            print("       %-8s -> %s" % (cid, tag))

    print("\nInterpretation: with R just 2-3 cells (180-270 m), the developed")
    print("homes the fire has reached the EDGE of inherit a threat ETA and would")
    print("alert/dispatch; homes with no burnable fire within R stay UNREACHED")
    print("(correctly — no nearby fire). Polarity: strictly MORE homes threatened.")


if __name__ == "__main__":
    main()
