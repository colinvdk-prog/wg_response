"""scratch_review/corridor_roadless.py — how many of the corridor grid's
placed homes have no road? Resort tiles carry LANDFIRE 'developed' cells on
the ski mountains (lift stations, on-mountain restaurants, maintenance
yards) that sit inside the WUI band the simulator seeds clients into but
kilometres from any drivable OSM way — the reruns of the corridor grid
logged `roads.route straight-leg fallback ... > road_spur_max_m=8000 m` for
some of them. Reproduces the grid's exact placement (simui.place_clients with
the scenario seeds) and reports, per community, the share of homes farther
than 2 km / 8 km from the nearest road node. Usage:
py -3 scratch_review/corridor_roadless.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scratch_review.corridor_grid import CORRIDOR, N_CLIENTS, scenarios  # noqa: E402
from wgresponse import roads, simui  # noqa: E402
from wgresponse.simui import LOCATIONS, load_tile  # noqa: E402


def main():
    print("| community | homes placed (18 seeds) | > 2 km from a road | > 8 km (chord fallback) | median m to road |")
    print("|---|---|---|---|---|")
    for key in CORRIDOR:
        tile, syn = load_tile(key, cell_m=90.0)
        assert not syn, key
        g = roads.load_road_graph(key)
        assert g is not None, key
        dists = []
        for sc in scenarios(key, "local2"):
            for c in simui.place_clients(tile, N_CLIENTS, sc["seed"]):
                hit = g.nearest_node(c.loc, max_m=20000.0)
                dists.append(hit[1] if hit is not None else 20000.0)
        dists.sort()
        n = len(dists)
        far2 = sum(1 for d in dists if d > 2000.0)
        far8 = sum(1 for d in dists if d > 8000.0)
        print(f"| {LOCATIONS[key].name} | {n} | {far2} ({100.0 * far2 / n:.1f}%) "
              f"| {far8} ({100.0 * far8 / n:.1f}%) | {dists[n // 2]:.0f} |")


if __name__ == "__main__":
    main()
