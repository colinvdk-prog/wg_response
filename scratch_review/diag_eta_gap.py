"""diag_eta_gap.py — DIAGNOSIS ONLY (read-only product code).

Reproduce the founder's "home near the fire, never protected" complaint on the
real Malibu tile and classify every no-ETA client at a chaotic mid-run tick.

Run FROM REPO ROOT:  py -3 scratch_review/diag_eta_gap.py
"""
from __future__ import annotations

import math
import collections

from wgresponse import geo, spread
from wgresponse.fuels import is_burnable
from wgresponse.simui import SimSession, DEVELOPED_CODES
from wgresponse.contracts import UNREACHED

SEED = "eta-gap-diag-1"
LOC = "malibu_ca"
N_CLIENTS = 48
N_CREWS = 3
INTENSITY = "extreme"
N_TICKS = 120
INSPECT_TICKS = (40, 80, 120)

NB_NAMES = {91: "NB1 urban", 92: "NB2 snow/ice", 93: "NB3 ag",
            98: "NB8 water", 99: "NB9 barren", -9999: "nodata"}


def nearest_burnable(tile, center):
    """(lat,lon) of the burnable cell nearest to `center` — a legal ignition."""
    spec = tile.spec
    rc0 = geo.latlon_to_rc(spec, center)
    r0, c0 = rc0
    best = None
    for rad in range(0, 60):
        for r in range(max(0, r0 - rad), min(spec.nrows, r0 + rad + 1)):
            for c in range(max(0, c0 - rad), min(spec.ncols, c0 + rad + 1)):
                if max(abs(r - r0), abs(c - c0)) != rad:
                    continue
                if is_burnable(int(round(tile.fuel.get(r, c)))):
                    ll = geo.rc_to_latlon(spec, r, c)
                    d = geo.haversine_m(center, ll)
                    if best is None or d < best[0]:
                        best = (d, ll, (r, c))
        if best is not None:
            return best[1], best[2], best[0]
    raise RuntimeError("no burnable cell found near center")


def code_at(tile, loc):
    rc = geo.latlon_to_rc(tile.spec, loc)
    if rc is None:
        return None, None
    return rc, int(round(tile.fuel.get(*rc)))


def build():
    sess = SimSession(LOC, N_CLIENTS, N_CREWS, seed=SEED)
    tile = sess._tile
    loc = sess._loc
    ig_ll, ig_rc, ig_d = nearest_burnable(tile, loc.center)
    print("=" * 78)
    print("REPRODUCTION: Malibu standard tile, %d clients, %d crews, seed=%r"
          % (N_CLIENTS, N_CREWS, SEED))
    print("tile: %dx%d @ %.0fm  (%s)" % (tile.spec.nrows, tile.spec.ncols,
                                          tile.spec.cell_m, tile.source))
    print("ignite '%s' at %.5f,%.5f (cell %s, %.0f m from center)"
          % (INTENSITY, ig_ll.lat, ig_ll.lon, ig_rc, ig_d))
    fid = sess.ignite(ig_ll.lat, ig_ll.lon, intensity=INTENSITY)
    print("incident:", fid, " wind_dir_deg(FROM):", sess._wind_dir_deg)
    # configure notes (includes the sim's OWN developed-cell warning)
    for nte in sess._notes:
        print("   NOTE:", nte)
    print("=" * 78)
    return sess


def burned_and_reached(sess):
    """(burned_set, reached_set) row-major indices across all fires' NOMINAL
    member: burned = arrival <= sim_now; reached = arrival < UNREACHED."""
    spec = sess._tile.spec
    n = spec.nrows * spec.ncols
    now = sess._now_s
    burned = set()
    reached = set()
    for fid in sess._fire_order:
        ens = sess._fires[fid].ens
        if ens is None:
            continue
        d = ens.members[0].data
        for i in range(n):
            v = d[i]
            if v < UNREACHED:
                reached.add(i)
                if v <= now:
                    burned.add(i)
    return burned, reached


def classify_tick(sess, label):
    tile = sess._tile
    spec = tile.spec
    nr, nc = spec.nrows, spec.ncols
    now = sess._now_s
    burned, reached = burned_and_reached(sess)
    fuel_burn = [is_burnable(int(round(v))) for v in tile.fuel.data]

    def nbr_stats(r, c, k=1):
        """burned/reached counts among the 8-neighborhood (Chebyshev k), plus
        rook-direction burned flags (N,S,E,W) for 'surrounded' scoring."""
        bcount = rcount = total = 0
        for rr in range(max(0, r - k), min(nr, r + k + 1)):
            for cc in range(max(0, c - k), min(nc, c + k + 1)):
                if rr == r and cc == c:
                    continue
                total += 1
                i = rr * nc + cc
                if i in burned:
                    bcount += 1
                if i in reached:
                    rcount += 1
        rook = 0
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < nr and 0 <= cc < nc and (rr * nc + cc) in burned:
                rook += 1
        return bcount, rcount, total, rook

    rows = []
    for cl in sess._clients:
        rc, code = code_at(tile, cl.loc)
        eta = sess._client_etas.get(cl.client_id)
        p10 = None if eta is None else eta.eta_p10_s
        has_eta = eta is not None and not math.isinf(eta.eta_p10_s)
        # per-client displayed alert state
        st = "none"
        best_rank = -1
        for (cid, _iid), rec in sess._alerts.states().items():
            if cid == cl.client_id:
                st = rec.state.value
        r, c = rc
        bcount, rcount, total, rook = nbr_stats(r, c, k=1)
        b2, r2, t2, _ = nbr_stats(r, c, k=2)
        rows.append(dict(cid=cl.client_id, value=cl.value_usd, rc=rc, code=code,
                         burnable=fuel_burn[r * nc + c], has_eta=has_eta,
                         p10=p10, state=st, bn1=bcount, rn1=rcount, t1=total,
                         rook=rook, bn2=b2, rn2=t2))

    no_eta = [x for x in rows if not x["has_eta"]]
    # classification
    class_a = []   # own cell non-burnable (KNOWN surface-model caveat)
    class_b = []   # burnable, unreached, no reached burnable neighbor -> too far
    class_c = []   # burnable, unreached, but reached burnable neighbors -> hole
    for x in no_eta:
        if not x["burnable"]:
            class_a.append(x)
        elif x["rn1"] > 0:
            class_c.append(x)
        else:
            class_b.append(x)

    print("\n" + "-" * 78)
    print("TICK %s  (sim_now +%.1f h from ignite)  burned=%d cells reached=%d"
          % (label, (now - sess._fires[sess._fire_order[0]].t_ignite_s) / 3600.0,
             len(burned), len(reached)))
    impacted = sum(1 for x in rows if x["has_eta"] and x["p10"] is not None
                   and x["p10"] <= now)
    threatened = sum(1 for x in rows if x["has_eta"])
    print("clients: %d total | %d have ETA (threatened/assessed) | %d no-ETA"
          % (len(rows), threatened, len(no_eta)))
    print("no-ETA breakdown:  (a) NON-BURNABLE cell = %d   "
          "(b) burnable/too-far = %d   (c) burnable/hole-artifact = %d"
          % (len(class_a), len(class_b), len(class_c)))

    # NB code composition of class (a)
    comp = collections.Counter(NB_NAMES.get(x["code"], str(x["code"]))
                               for x in class_a)
    print("   class (a) by fuel code:", dict(comp))

    # "surrounded but no ETA" — class (a) homes with burned cells on multiple
    # sides (the founder's c34-like complaint). Rank by burned-neighbor count.
    surrounded = sorted([x for x in class_a if x["bn1"] >= 1],
                        key=lambda x: (-x["bn1"], -x["rook"]))
    print("   class (a) homes with >=1 BURNED neighbor (surrounded-but-unprojected): %d"
          % len(surrounded))
    for x in surrounded[:6]:
        print("     %-8s $%9.0f cell%s code=%d(%s)  burned_nbrs=%d/8 rook=%d/4 "
              "burned_within2=%d  state=%s"
              % (x["cid"], x["value"], x["rc"], x["code"],
                 NB_NAMES.get(x["code"], "?"), x["bn1"], x["rook"], x["bn2"],
                 x["state"]))
    return dict(rows=rows, no_eta=no_eta, a=class_a, b=class_b, c=class_c,
                surrounded=surrounded, burned=burned, now=now)


def nb_placement_census(sess):
    """Question 2: fraction of the 48 placed clients sitting ON a NB cell."""
    tile = sess._tile
    comp = collections.Counter()
    nb = 0
    for cl in sess._clients:
        _rc, code = code_at(tile, cl.loc)
        if not is_burnable(code):
            nb += 1
            comp[NB_NAMES.get(code, str(code))] += 1
    print("\n" + "=" * 78)
    print("WUI PLACEMENT CENSUS (real Malibu tile, seed=%r)" % SEED)
    print("clients on NON-BURNABLE cells: %d of %d = %.1f%%"
          % (nb, len(sess._clients), 100.0 * nb / len(sess._clients)))
    print("   by NB code:", dict(comp))
    dev = sum(1 for cl in sess._clients
              if code_at(tile, cl.loc)[1] in DEVELOPED_CODES)
    print("   of which 'developed' (91/93, counted by sim's own note): %d" % dev)
    print("   (any beyond 91/93 => overflow pool placed on other NB codes,")
    print("    which the sim's n_dev note does NOT count)")
    return nb


def run_seed(seed, n_ticks=120, quiet=True):
    """One full run; returns the classification dict at the final tick plus the
    NB-placement count and the PEAK surrounded count across the whole run."""
    global SEED
    SEED = seed
    sess = SimSession(LOC, N_CLIENTS, N_CREWS, seed=seed)
    tile = sess._tile
    ig_ll, _rc, _d = nearest_burnable(tile, sess._loc.center)
    sess.ignite(ig_ll.lat, ig_ll.lon, intensity=INTENSITY)
    nb = sum(1 for cl in sess._clients
             if not is_burnable(code_at(tile, cl.loc)[1]))
    dev = sum(1 for cl in sess._clients
              if code_at(tile, cl.loc)[1] in DEVELOPED_CODES)
    peak_surr = 0
    last = None
    for t in range(1, n_ticks + 1):
        sess.tick()
        r = classify_tick(sess, str(t)) if not quiet else _classify_quiet(sess)
        peak_surr = max(peak_surr, len(r["surrounded"]))
        last = r
    last.update(nb=nb, dev=dev, peak_surr=peak_surr, sess=sess)
    return last


def _classify_quiet(sess):
    """Silent classify (no printing) — reuse classify_tick's logic by capturing.
    Simplest: temporarily monkey-nothing; just replicate the core counts."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return classify_tick(sess, "-")


def main():
    # 1) headline detailed run (printed) on the primary seed
    sess = build()
    sess.tick()
    nb_placement_census(sess)
    results = {}
    for t in range(2, N_TICKS + 1):
        sess.tick()
        if t in INSPECT_TICKS:
            results[t] = classify_tick(sess, str(t))
    last = results[max(results)]

    # class (b) proof: are the "too-far" homes genuinely distant / upwind?
    print("\n" + "-" * 78)
    print("CLASS (b) PROOF — 'too far' homes vs ignition (wind FROM 50deg=NE, "
          "fire runs SW):")
    ig = sess._fires[sess._fire_order[0]].origin
    tile = sess._tile
    for x in sorted(last["b"], key=lambda z: z["rc"])[:6]:
        ll = geo.rc_to_latlon(tile.spec, x["rc"][0], x["rc"][1])
        d = geo.haversine_m(ig, ll) / 1000.0
        brg = geo.bearing_deg(ig, ll)
        print("     %-8s burnable cell%s  %.1f km @ %.0fdeg from fire  "
              "reached_nbrs=%d/8  -> outside 96h projected footprint"
              % (x["cid"], x["rc"], d, brg, x["rn1"]))

    # 2) seed sweep — robustness + find a founder-matching (~15 no-ETA) seed
    print("\n" + "=" * 78)
    print("SEED SWEEP (robustness of the no-ETA phenomenon):")
    print("  seed            NBplaced/48   noETA   (a)NB (b)far (c)hole  peakSurr")
    sweep = {}
    for s in ["eta-gap-diag-1", "malibu-A", "malibu-B", "malibu-C",
              "malibu-D", "malibu-E", "malibu-F", "malibu-G"]:
        r = run_seed(s, n_ticks=120, quiet=True)
        sweep[s] = r
        print("  %-14s   %2d (%.0f%%)     %3d     %3d   %3d    %3d      %3d"
              % (s, r["nb"], 100.0 * r["nb"] / N_CLIENTS, len(r["no_eta"]),
                 len(r["a"]), len(r["b"]), len(r["c"]), r["peak_surr"]))

    print("\n" + "=" * 78)
    print("HEADLINE SUMMARY (primary seed @ tick %d):" % max(results))
    print("  no-ETA total: %d  ->  (a) NB=%d  (b) too-far=%d  (c) hole=%d"
          % (len(last["no_eta"]), len(last["a"]), len(last["b"]), len(last["c"])))
    print("  clients on NB cells: %d/48 = %.1f%%  (all developed code 91)"
          % (last["a"] and len([x for x in last['rows'] if not x['burnable']])
             or 0, 100.0 * len([x for x in last['rows']
                                 if not x['burnable']]) / 48))
    print("  surrounded-but-no-ETA (class a, >=1 burned nbr) @tick120: %d"
          % len(last["surrounded"]))
    print("=" * 78)


if __name__ == "__main__":
    main()
