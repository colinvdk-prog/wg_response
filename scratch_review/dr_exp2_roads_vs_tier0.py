"""dr_exp2 — TIER-0 CHORD TRAVEL vs THE REAL ROAD NETWORK, per location.

The planner (dispatch.plan) prices and time-checks every leg with the tier-0
model: haversine * circuity 1.35 * fire_traffic 1.4 / 17.9 m/s. The repo now
carries REAL OSM road graphs for all five simulator locations
(data/roads/<key>.wgr) that the SIMULATOR uses for crew movement — but the
planner never consults them.

This experiment samples 100 planner-representative legs per location
(WUI client<->client, safe-zone<->client — the exact leg population the
solver evaluates), and compares:

    tier0_s = travel.travel_time_s(a, b, cfg)          (what the plan uses)
    road_s  = roads.route(g, a, b, cfg).time_s         (what driving takes)

ratio > 1 means the plan is OPTIMISTIC (crew arrives later than planned —
eats the SAFETY SEMANTICS 7 slack floor). Geometric circuity =
road_dist / haversine (how honest the 1.35 constant is per area).
Note the caveat: road_s uses OSM free-flow class speeds (11-29 m/s) x the
same fire_traffic 1.4, so the ratio mixes geometry and speed-table effects;
the circuity column isolates geometry.
"""
from __future__ import annotations

import math
import statistics as st

import dr_common  # noqa: F401  (repo-root sys.path)

from wgresponse import roads, travel
from wgresponse.contracts import ResponseConfig, seeded_rng
from wgresponse.geo import haversine_m
from wgresponse.simui import LOCATIONS, load_tile, place_clients

N_LEGS = 100
N_CLIENTS = 30


def sample_legs(key: str, cfg: ResponseConfig):
    loc = LOCATIONS[key]
    tile, synthetic = load_tile(key)
    assert not synthetic, f"{key}: real tile missing — fetch data/tiles first"
    g = roads.load_road_graph(key)
    assert g is not None, f"{key}: no cached road graph"
    clients = place_clients(tile, N_CLIENTS, seed=f"dr2|{key}")
    pts = [c.loc for c in clients] + [z.loc for z in loc.safe_zones]
    rng = seeded_rng("dr_exp2", key)
    rows = []
    fallbacks = 0
    seen = set()
    while len(rows) < N_LEGS and len(seen) < len(pts) * (len(pts) - 1):
        i = rng.randrange(len(pts))
        j = rng.randrange(len(pts))
        if i == j or (i, j) in seen:
            continue
        seen.add((i, j))
        a, b = pts[i], pts[j]
        hav = haversine_m(a, b)
        if hav < 400.0:            # sub-cell neighbors: snap noise dominates
            continue
        t0 = travel.travel_time_s(a, b, cfg)
        rr = roads.route(g, a, b, cfg)
        if not rr.on_roads:
            fallbacks += 1
            continue
        rows.append((hav, t0, rr.time_s, rr.dist_m))
    return rows, fallbacks


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))]


def main():
    cfg = ResponseConfig()
    eff = cfg.road_speed_mps / (cfg.circuity * cfg.fire_traffic_factor)
    print(f"tier-0 effective chord speed: {eff:.2f} m/s "
          f"({eff*3.6:.1f} km/h) | assumed circuity {cfg.circuity}")
    print(f"\n{'location':<16}{'legs':>5}{'fb':>4}{'med km':>8}"
          f"{'road/t0 mean':>13}{'med':>6}{'p90':>6}{'>1.0x':>7}{'>1.2x':>7}"
          f"{'mean slip':>10}{'circuity':>9}")
    grand = []
    for key in sorted(LOCATIONS):
        rows, fb = sample_legs(key, cfg)
        ratios = [r / t for _h, t, r, _d in rows]
        slips = [(r - t) / 60.0 for _h, t, r, _d in rows]
        circ = [d / h for h, _t, _r, d in rows]
        grand.extend(rows)
        print(f"{key:<16}{len(rows):>5}{fb:>4}"
              f"{pct([h for h, *_ in rows], 0.5)/1000:>8.1f}"
              f"{st.mean(ratios):>13.2f}{pct(ratios, 0.5):>6.2f}"
              f"{pct(ratios, 0.9):>6.2f}"
              f"{100*sum(1 for x in ratios if x > 1.0)/len(ratios):>6.0f}%"
              f"{100*sum(1 for x in ratios if x > 1.2)/len(ratios):>6.0f}%"
              f"{st.mean(slips):>9.1f}m{st.mean(circ):>9.2f}")
    ratios = [r / t for _h, t, r, _d in grand]
    slips = [(r - t) / 60.0 for _h, t, r, _d in grand]
    circ = [d / h for h, _t, _r, d in grand]
    late = [s for x, s in zip(ratios, slips) if x > 1.0]
    print(f"\nALL {len(grand)} legs: mean ratio {st.mean(ratios):.2f}, "
          f"median {pct(ratios, 0.5):.2f}, p90 {pct(ratios, 0.9):.2f}; "
          f"tier-0 optimistic on {100*len(late)/len(ratios):.0f}% of legs "
          f"(mean slip on those {st.mean(late):.1f} min, "
          f"max {max(slips):.1f} min)")
    print(f"measured geometric circuity: mean {st.mean(circ):.2f}, "
          f"p90 {pct(circ, 0.9):.2f}  (model constant: {cfg.circuity})")


if __name__ == "__main__":
    main()
