"""DIAGNOSIS ONLY (task item 3) — quantify P10 COMPRESSION and whether ranking
homes by p50 (honest median urgency) vs p10 (the compressed, safety-pessimistic
arrival) produces a materially different, better-ordered protection set.

The Council-1 wind-DIRECTION ensemble (+-dir_perturb_deg=30 at max lead) makes
p10 pessimistically early over a wide arc, so many homes read p10 ~ now even when
p50 is 20-70 h out. Everything urgency-relevant in dispatch keys off p10:
  - latest_service_start_s = p10 - buffers - deploy  (feasibility + EDF order)
  - late_shift cap        = latest_depart - slack    (p10-derived)
  - greedy-EDF order       = ascending latest_service_start_s (p10)
So the URGENCY signal the planner uses is the COMPRESSED p10, not p50.

Read-only; drives SimSession in-process.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wgresponse import geo, simui  # noqa: E402
from wgresponse.fuels import is_burnable  # noqa: E402

TILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "tiles")
H = 3600.0


def nearest_burnable(sess, lat, lon):
    spec = sess._tile.spec
    r0, c0 = geo.latlon_to_rc(spec, geo.LatLon(lat, lon))
    for rad in range(40):
        for dr in range(-rad, rad + 1):
            for dc in range(-rad, rad + 1):
                if max(abs(dr), abs(dc)) != rad:
                    continue
                r, c = r0 + dr, c0 + dc
                if 0 <= r < spec.nrows and 0 <= c < spec.ncols and \
                        is_burnable(int(round(sess._tile.fuel.get(r, c)))):
                    return geo.rc_to_latlon(spec, r, c)


def spearman(xs, ys):
    """Rank correlation (no scipy)."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    if n < 2:
        return 1.0
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 1.0


def main():
    sess = simui.SimSession("malibu_ca", 48, 3, seed="sim-7", tile_dir=TILE)
    p = nearest_burnable(sess, 34.0796, -118.6803)
    sess.ignite(p.lat, p.lon, intensity="extreme")

    print("=== P10 COMPRESSION over the run (Malibu sim-7, 48/3 extreme) ===")
    print("tick  +h   nThreat  p10<=2h  p10<=2h&p50>=10h   median(p50-p10)h  "
          "spearman(p10,p50)  disagree@k=n_crews")
    samples = []
    for t in range(120):
        st = sess.tick()
        now = st["sim_now_s"]
        rows = [(c["client_id"], c["eta_p10_s"], c["eta_p50_s"])
                for c in st["clients"]
                if c["eta_p10_s"] is not None and c["eta_p50_s"] is not None
                and not c["gel_applied"] and c["eta_p50_s"] > now]
        if len(rows) < 3:
            continue
        p10s = [(r[1] - now) / H for r in rows]
        p50s = [(r[2] - now) / H for r in rows]
        near = sum(1 for x in p10s if x <= 2.0)
        near_far = sum(1 for i in range(len(rows))
                       if p10s[i] <= 2.0 and p50s[i] >= 10.0)
        spreads = sorted(p50s[i] - p10s[i] for i in range(len(rows)))
        med_spread = spreads[len(spreads) // 2]
        rho = spearman(p10s, p50s)
        # top-k (k=3 crews) by p10 vs by p50: how many differ?
        k = 3
        by10 = {r[0] for r in sorted(rows, key=lambda r: r[1])[:k]}
        by50 = {r[0] for r in sorted(rows, key=lambda r: r[2])[:k]}
        disagree = k - len(by10 & by50)
        if t % 6 == 0 or t < 8:
            print(f"{t:4d} {(now-sess._t0_s)/H:4.1f}  {len(rows):5d}    {near:5d}   "
                  f"{near_far:8d}          {med_spread:8.1f}         {rho:6.2f}"
                  f"           {disagree}/{k}")
        samples.append((len(rows), near, near_far, med_spread, rho, disagree, k))

    if samples:
        n = len(samples)
        print("\n--- averages across active ticks ---")
        print(f"  threatened-ungelled homes/tick    : {sum(s[0] for s in samples)/n:.1f}")
        print(f"  homes with p10<=+2h (look imminent): {sum(s[1] for s in samples)/n:.1f}")
        print(f"  ...of those, p50>=+10h (compressed): {sum(s[2] for s in samples)/n:.1f}")
        print(f"  median (p50 - p10) spread          : {sum(s[3] for s in samples)/n:.1f} h")
        print(f"  mean spearman rank corr(p10,p50)   : {sum(s[4] for s in samples)/n:.2f}")
        tot_dis = sum(s[5] for s in samples)
        tot_k = sum(s[6] for s in samples)
        print(f"  top-{samples[0][6]} (=crews) protect set differs p10-vs-p50: "
              f"{tot_dis}/{tot_k} slots ({100*tot_dis/max(1,tot_k):.0f}%) would be "
              f"a DIFFERENT home if ranked by p50")

    # snapshot of the ignition-tick compression for the eventually-gelled homes
    print("\n--- example: p10 vs p50 lead at the FIRST tick each home is jobbed ---")
    print("  (a big p50-lead with a small p10-lead = compression = gelled too "
          "early / feasibility falsely tight)")


if __name__ == "__main__":
    main()
