"""scratch_review/corridor_roads.py — corridor road graph + drive-time matrix
for the Vail -> Aspen response program (founder 2026-09-23).

Fetches the MAJOR drivable network (motorway..tertiary + links; the per-tile
graphs keep residential/service too, but a ~107 x 83 km corridor of those
would be 100k+ ways for no routing gain BETWEEN towns) over the corridor
bbox from Overpass with tools/fetch_sim_roads' own fetch/simplify machinery,
saves it dep-free as data/roads/corridor_vail_aspen.wgr (roads.save_road_graph),
and routes with the shipped wgresponse.roads.route (OSM class speeds;
cfg.fire_traffic_factor 1.4 = the "fire" column, 1.0 = free-flow) from the
Eagle base (I-70 exit 147, PD Mountain Resorts HQ) to every community center
and safe zone, plus community-to-community.

Independence Pass (SH-82 east of Aspen, closed ~Nov-May) is reported both
ways: the graph as fetched (summer) and an ALL-SEASON variant with the pass
segment's edges removed, because a program that assumes the pass is open in
October is wrong for a month of fire season.

Usage: py -3 scratch_review/corridor_roads.py [--force]
Writes scratch_review/corridor_drive_times.json and prints the markdown.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fetch_sim_roads as F  # noqa: E402
from wgresponse import geo, roads  # noqa: E402
from wgresponse.contracts import BBox, LatLon, ResponseConfig  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

KEY = "corridor_vail_aspen"
BBOX = BBox(west=-107.45, south=39.05, east=-106.20, north=39.80)
MAJOR_REGEX = ("^(motorway|trunk|primary|secondary|tertiary|motorway_link|"
               "trunk_link|primary_link|secondary_link|tertiary_link)$")
CORRIDOR = ("vail_co", "beaver_creek_co", "eagle_co", "glenwood_springs_co",
            "basalt_carbondale_co", "aspen_co")
EAGLE_BASE = LOCATIONS["eagle_co"].safe_zones[0]          # I-70 exit 147 / Eagle
#: SH-82 between Aspen's east edge and Twin Lakes (Independence Pass, summit
#: 39.108 / -106.564, 3,687 m): edges touching this box are dropped for the
#: all-season variant. Aspen itself (lon -106.82) lies west of the box; US-24
#: (Leadville - Twin Lakes, lon ~-106.37) lies east of it.
PASS_BOX = BBox(west=-106.78, south=39.05, east=-106.40, north=39.17)
PASS_SUMMIT = LatLon(39.108, -106.564)
#: I-70 through Glenwood Canyon (east of No Name to Dotsero): the corridor's
#: single severance point (Grizzly Creek 2020 closed it for 13 days; rockfall
#: closures are routine). Edges touching this box are dropped for the
#: "canyon closed" variant. Glenwood Springs (lon -107.32) and the No Name
#: interchange (-107.284) lie west of the box; Dotsero (-107.06) east of it.
CANYON_BOX = BBox(west=-107.28, south=39.54, east=-107.07, north=39.66)
#: Cottonwood Pass (Eagle CR-10 / Garfield CR-113, Gypsum -> Carbondale):
#: UNPAVED, closed in winter, mapped as OSM tertiary and therefore charged at
#: paved tertiary speed by roads.CLASS_SPEED_MPS — any route flagged
#: via_cottonwood is an optimistic summer-only figure (real-world ~1.5 h).
COTTONWOOD_SUMMIT = LatLon(39.485, -107.025)
OUT = ROOT / "scratch_review" / "corridor_drive_times.json"


def fetch(force: bool):
    path = roads.road_file(KEY)
    if path.exists() and not force:
        return roads.load_road_graph(KEY), "cached"
    F.HIGHWAY_REGEX = MAJOR_REGEX          # read by overpass_query at call time
    last = None
    for attempt in range(1, 4):
        t0 = time.monotonic()
        try:
            print(f"Overpass corridor query (attempt {attempt}/3) ...", flush=True)
            ways = F.fetch_ways(BBOX)
            fetch_s = time.monotonic() - t0
            print(f"  {len(ways):,} major-road ways in {fetch_s:.1f} s; simplifying ...")
            meta = {
                "key": KEY,
                "name": "Vail -> Aspen corridor (major roads: motorway..tertiary)",
                "bbox_wsen": [BBOX.west, BBOX.south, BBOX.east, BBOX.north],
                "source": "overpass-api.de (OpenStreetMap, ODbL)",
                "highway_regex": MAJOR_REGEX,
                "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generator": "scratch_review/corridor_roads.py",
                "fetch_seconds": round(fetch_s, 1),
            }
            g, stats = F.build_graph(ways, meta)
            roads.save_road_graph(g, path)
            print(f"  saved {path}: nodes {stats['nodes']:,} edges {stats['edges']:,} "
                  f"LCC {stats['largest_component_node_pct']}%")
            return g, "fetched"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            print(f"  !! attempt {attempt} FAILED: {last}", flush=True)
            if attempt < 3:
                time.sleep(30.0 * attempt)
    raise SystemExit(f"corridor road fetch failed: {last}")


def without_box(g: roads.RoadGraph, box: BBox, variant: str) -> roads.RoadGraph:
    """The graph with every edge touching `box` removed (and the nodes those
    edges alone referenced)."""
    keep = []
    for e in g.edges:
        pts = (g.nodes[e.u], g.nodes[e.v]) + tuple(e.geometry)
        if any(box.contains(p) for p in pts):
            continue
        keep.append(e)
    nodes = {n: g.nodes[n] for e in keep for n in (e.u, e.v)}
    return roads.RoadGraph(nodes, keep,
                           dict(g.meta, variant=variant,
                                edges_dropped=len(g.edges) - len(keep)))


def all_season(g: roads.RoadGraph) -> roads.RoadGraph:
    return without_box(g, PASS_BOX, "all_season_no_independence_pass")


def canyon_closed(g: roads.RoadGraph) -> roads.RoadGraph:
    return without_box(g, CANYON_BOX, "glenwood_canyon_closed")


def leg(g, a: LatLon, b: LatLon, cfg: ResponseConfig) -> dict:
    r = roads.route(g, a, b, cfg)
    via_pass = min(geo.haversine_m(p, PASS_SUMMIT) for p in r.polyline) < 3000.0
    via_cottonwood = min(geo.haversine_m(p, COTTONWOOD_SUMMIT) for p in r.polyline) < 4000.0
    # A route between the Eagle Valley and the Roaring Fork that never comes
    # within 5 km of Glenwood Springs is on the unpaved seasonal back roads
    # (Crooked Creek Pass via Sylvan Lake, or Cottonwood Pass) — OSM tertiary,
    # charged at paved speed: optimistic and summer-only.
    glenwood = LOCATIONS["glenwood_springs_co"].center
    bypasses_glenwood = min(geo.haversine_m(p, glenwood) for p in r.polyline) > 5000.0
    return dict(min=round(r.time_s / 60.0, 1), km=round(r.dist_m / 1000.0, 1),
                on_roads=bool(r.on_roads), via_pass=bool(via_pass),
                via_cottonwood=bool(via_cottonwood),
                bypasses_glenwood=bool(bypasses_glenwood))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    g, status = fetch(args.force)
    g2 = all_season(g)
    cfg_fire = ResponseConfig()                       # fire_traffic_factor 1.4 (shipped)
    cfg_free = ResponseConfig()
    cfg_free.fire_traffic_factor = 1.0
    out = {
        "graph": {k: g.meta.get(k) for k in ("nodes", "edges", "largest_component_node_pct",
                                              "fetched_utc", "highway_regex", "bbox_wsen")},
        "graph_status": status,
        "all_season_edges_dropped": g2.meta.get("edges_dropped"),
        "eagle_base": dict(zone_id=EAGLE_BASE.zone_id, name=EAGLE_BASE.name,
                           lat=EAGLE_BASE.loc.lat, lon=EAGLE_BASE.loc.lon),
        "fire_traffic_factor": cfg_fire.fire_traffic_factor,
        "from_eagle_base": {}, "from_eagle_base_all_season": {},
        "zones_from_eagle_base": {}, "center_to_center": {},
    }
    print(f"\n| community | km | free-flow min | fire-traffic min | via Independence Pass | all-season fire min |")
    print("|---|---|---|---|---|---|")
    for k in CORRIDOR:
        loc = LOCATIONS[k]
        rf = leg(g, EAGLE_BASE.loc, loc.center, cfg_free)
        rr = leg(g, EAGLE_BASE.loc, loc.center, cfg_fire)
        r2 = leg(g2, EAGLE_BASE.loc, loc.center, cfg_fire)
        out["from_eagle_base"][k] = dict(free_min=rf["min"], fire_min=rr["min"], km=rr["km"],
                                         on_roads=rr["on_roads"], via_pass=rr["via_pass"])
        out["from_eagle_base_all_season"][k] = dict(fire_min=r2["min"], km=r2["km"],
                                                    on_roads=r2["on_roads"])
        print(f"| {loc.name} | {rr['km']} | {rf['min']} | {rr['min']} | {rr['via_pass']} | {r2['min']} |")
        for z in loc.safe_zones:
            rz = leg(g, EAGLE_BASE.loc, z.loc, cfg_fire)
            out["zones_from_eagle_base"][z.zone_id] = dict(
                fire_min=rz["min"], km=rz["km"], on_roads=rz["on_roads"], name=z.name)
    print("\n| from \\ to (fire-traffic min, summer graph; all-season in brackets) | " +
          " | ".join(LOCATIONS[k].name.split(" /")[0].split(",")[0] for k in CORRIDOR) + " |")
    print("|---|" + "---|" * len(CORRIDOR))
    for a in CORRIDOR:
        cells = []
        for b in CORRIDOR:
            if a == b:
                cells.append("—")
                continue
            r = leg(g, LOCATIONS[a].center, LOCATIONS[b].center, cfg_fire)
            r2 = leg(g2, LOCATIONS[a].center, LOCATIONS[b].center, cfg_fire)
            out["center_to_center"][f"{a}->{b}"] = dict(
                fire_min=r["min"], km=r["km"], via_pass=r["via_pass"],
                all_season_fire_min=r2["min"], all_season_km=r2["km"],
                on_roads=r["on_roads"] and r2["on_roads"])
            cells.append(f"{r['min']:.0f} [{r2['min']:.0f}]" + (" P" if r["via_pass"] else ""))
        print(f"| {LOCATIONS[a].name.split(' /')[0].split(',')[0]} | " + " | ".join(cells) + " |")
    # SEVERANCE: I-70 Glenwood Canyon closed (summer graph, so Independence
    # Pass is available; the all-season answer for the Roaring Fork is then
    # "no paved route" — Cottonwood Pass is unpaved/seasonal and not a major
    # road, so it is not in this graph).
    gc = canyon_closed(g)
    gc2 = without_box(gc, PASS_BOX, "glenwood_canyon_closed_all_season")
    out["glenwood_canyon_closed"] = {}
    print("\n| Eagle base -> community, I-70 Glenwood Canyon CLOSED | km | fire-traffic min | via Independence Pass | on roads | all-season on roads |")
    print("|---|---|---|---|---|---|")
    for k in CORRIDOR:
        loc = LOCATIONS[k]
        r = leg(gc, EAGLE_BASE.loc, loc.center, cfg_fire)
        r2 = leg(gc2, EAGLE_BASE.loc, loc.center, cfg_fire)
        backroad = k in ("glenwood_springs_co", "basalt_carbondale_co", "aspen_co") and r["bypasses_glenwood"]
        out["glenwood_canyon_closed"][k] = dict(
            fire_min=r["min"], km=r["km"], via_pass=r["via_pass"],
            unpaved_backroad=bool(backroad), on_roads=r["on_roads"],
            all_season_fire_min=r2["min"], all_season_on_roads=r2["on_roads"])
        print(f"| {loc.name} | {r['km']} | {r['min']} | {r['via_pass']} | "
              f"{'UNPAVED SEASONAL BACK ROAD (optimistic)' if backroad else r['on_roads']} | "
              f"{r2['on_roads']} |")
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print("\nwrote", OUT)
    bad = [k for k, v in out["from_eagle_base"].items() if not v["on_roads"]]
    if bad:
        print(f"!! chord fallback (endpoint > {cfg_fire.road_spur_max_m:.0f} m from a major road "
              f"or disconnected) for: {bad}")


if __name__ == "__main__":
    main()
