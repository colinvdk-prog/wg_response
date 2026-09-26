"""tools/fetch_sim_roads.py — one-time OSM road fetch for the five simulator locations.

Pulls the drivable road network (Overpass API, OpenStreetMap data) for every
location in ``wgresponse.simui.LOCATIONS`` — keys, centers and safe zones are
IMPORTED from that registry, never duplicated — simplifies it into a
``wgresponse.roads.RoadGraph`` and saves it dep-free at ``data/roads/<key>.wgr``
(roads.save_road_graph), after which the simulator's crew routing
(roads.route) runs fully OFFLINE and stdlib-only.

Usage (from the repo root; LIVE network — Overpass is a shared public service):

    py -3 tools/fetch_sim_roads.py                   # fetch whatever is missing
    py -3 tools/fetch_sim_roads.py --force           # refetch (fresh queries)
    py -3 tools/fetch_sim_roads.py --only malibu_ca  # subset (repeatable)

BBOX LAW: roads must cover every travel endpoint the simulator produces —
clients live inside the ~18x18 km terrain tile (center +- 9 km, the
fetch_sim_tiles law) but three safe zones sit just OUTSIDE that box
(malibu z1 Zuma Beach, santa_rosa z3 Windsor, park_city z3 Jeremy Ranch).
The road bbox is therefore the UNION of geo.bbox_around([center], 9 km)
and geo.bbox_around(safe_zone_locs, 1.5 km), all read from LOCATIONS.
PER-LOCATION GEOMETRY (2026-09-25): the center pad is the location's own
STANDARD-tile half-width, SimLocation.tile_half_m — 9 km for every preset
except the whole-valley eagle_vail_co (30 km: Gypsum to East Vail), whose
roads must cover its entire ~60 km tile. At the 9 km default the bbox of
every existing key is byte-identical (their provenance records it).

OVERPASS ETIQUETTE (https://overpass-api.de policy): ONE request at a time
(strictly sequential), identifying User-Agent with a contact address, a
courtesy pause between locations, and 1 + RETRIES attempts with growing
backoff on failure/throttle (429/504). A location that still fails after all
attempts is reported LOUDLY, recorded in data/roads/provenance.json, and the
run continues (exit 1) — roads.route falls back to straight legs
(on_roads=False) for that location, never silence.

SIMPLIFICATION (per the roads seam):
  * ways matching the drivable-highway regex, with per-node geometry
    (``out geom``);
  * junction nodes = OSM nodes referenced >= 2 times across/within ways, or
    way endpoints; each way is cut into segments between junctions;
  * degree-2 chain collapse ACROSS ways (same highway class only): the
    shared node becomes edge geometry, lengths sum — polylines keep every
    original curve vertex, the graph keeps only real decision points;
  * self-loop edges (u == v, e.g. a ring road with a single junction) are
    dropped for routing (counted, logged);
  * LARGEST connected component only: island fragments (disconnected
    subdivisions, service stubs clipped by the bbox) are dropped and the
    dropped node % is logged + stored in meta.

Deterministic given identical OSM data: node renumbering is sorted, edge
order canonicalized by roads.save_road_graph; no RNG anywhere. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from wgresponse import geo  # noqa: E402
from wgresponse.contracts import BBox, LatLon  # noqa: E402
from wgresponse.roads import (RoadEdge, RoadGraph, load_road_graph,  # noqa: E402
                              road_file, save_road_graph)
from wgresponse.simui import LOCATIONS  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
#: Drivable classes the simulator routes on (matches the roads.py speed table).
HIGHWAY_REGEX = ("^(motorway|trunk|primary|secondary|tertiary|unclassified|"
                 "residential|motorway_link|trunk_link|primary_link|"
                 "secondary_link|tertiary_link|service)$")
USER_AGENT = "wg-response/0.1 (simulator road fetch; ops@thewildfireguardian.com)"

TILE_HALF_M = 9000.0        # center pad — the fetch_sim_tiles ~18x18 km tile law
ZONE_PAD_M = 1500.0         # safe-zone pad so staging points snap to real roads
OVERPASS_TIMEOUT_S = 300    # server-side [timeout:]; HTTP timeout adds slack
RETRIES = 2                 # extra attempts after the first failure
BACKOFF_BASE_S = 30.0       # sleep 30 s, then 60 s between attempts
POLITE_PAUSE_S = 8.0        # courtesy pause between consecutive live queries

ROAD_DIR = os.path.join(_REPO_ROOT, "data", "roads")
PROVENANCE_PATH = os.path.join(ROAD_DIR, "provenance.json")


# ---------------------------------------------------------------------------
# BBox (all coords come from wgresponse.simui.LOCATIONS)
# ---------------------------------------------------------------------------

def tile_half_m(loc) -> float:
    """The location's STANDARD-tile half-width (SimLocation.tile_half_m;
    TILE_HALF_M = the 9 km law when a registry entry predates the field)."""
    return float(getattr(loc, "tile_half_m", TILE_HALF_M))


def bbox_for(loc) -> BBox:
    """Union of the tile box around the center (the location's own tile
    half-width — ~18x18 km for every preset but eagle_vail_co's ~60 km) and
    1.5 km pads around every safe zone (three zones sit outside the plain
    18 km tile box)."""
    center_box = geo.bbox_around([loc.center], pad_m=tile_half_m(loc))
    zone_box = geo.bbox_around([z.loc for z in loc.safe_zones], pad_m=ZONE_PAD_M)
    return BBox(west=min(center_box.west, zone_box.west),
                south=min(center_box.south, zone_box.south),
                east=max(center_box.east, zone_box.east),
                north=max(center_box.north, zone_box.north))


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------

def overpass_query(bbox: BBox) -> str:
    return (f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];\n"
            f'way["highway"~"{HIGHWAY_REGEX}"]'
            f"({bbox.south:.6f},{bbox.west:.6f},{bbox.north:.6f},{bbox.east:.6f});\n"
            "out geom;\n")


def fetch_ways(bbox: BBox) -> list:
    """POST the query; return the way elements (each with tags + geometry).
    Raises RuntimeError on HTTP/parse/remark failure."""
    query = overpass_query(bbox)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL, data=data,
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT_S + 60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:      # 429 Too Many / 504 timeout etc.
        raise RuntimeError(f"Overpass HTTP {e.code}: {e.reason}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise RuntimeError(f"Overpass transport error: {e}") from e
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise RuntimeError(f"Overpass returned non-JSON ({e}); first bytes: "
                           f"{body[:200]!r}") from e
    remark = payload.get("remark")
    if remark and ("error" in remark.lower() or "timed out" in remark.lower()):
        raise RuntimeError(f"Overpass remark: {remark}")
    ways = [el for el in payload.get("elements", [])
            if el.get("type") == "way" and el.get("nodes")
            and el.get("geometry") and (el.get("tags") or {}).get("highway")]
    if not ways:
        raise RuntimeError("Overpass returned zero highway ways for the bbox")
    return ways


# ---------------------------------------------------------------------------
# Simplification: ways -> RoadGraph
# ---------------------------------------------------------------------------

def _seg_length_m(coords: list) -> float:
    return sum(geo.haversine_m(LatLon(*coords[i]), LatLon(*coords[i + 1]))
               for i in range(len(coords) - 1))


def build_graph(ways: list, meta: dict) -> "tuple[RoadGraph, dict]":
    """Simplify raw Overpass ways into a RoadGraph (see module docstring).
    Returns (graph, stats)."""
    # 1) junction detection: count every occurrence of every node id.
    usage: dict = {}
    endpoint: set = set()
    for w in ways:
        nids = w["nodes"]
        for nid in nids:
            usage[nid] = usage.get(nid, 0) + 1
        endpoint.add(nids[0])
        endpoint.add(nids[-1])
    raw_node_count = len(usage)

    def is_junction(nid: int) -> bool:
        return usage[nid] >= 2 or nid in endpoint

    # 2) cut ways into segments between junctions (coords include endpoints).
    segs: dict = {}                  # sid -> {"u","v","cls","len","coords"}
    incident: dict = {}              # osm node id -> [sid, ...]
    next_sid = 0
    self_loops_dropped = 0

    def add_seg(u: int, v: int, cls: str, coords: list) -> None:
        nonlocal next_sid, self_loops_dropped
        if u == v:
            self_loops_dropped += 1
            return
        sid = next_sid
        next_sid += 1
        segs[sid] = {"u": u, "v": v, "cls": cls,
                     "len": _seg_length_m(coords), "coords": coords}
        incident.setdefault(u, []).append(sid)
        incident.setdefault(v, []).append(sid)

    for w in ways:
        cls = w["tags"]["highway"]
        nids = w["nodes"]
        coords = [(g["lat"], g["lon"]) for g in w["geometry"]]
        if len(nids) != len(coords) or len(nids) < 2:
            continue                                   # malformed element
        start = 0
        for i in range(1, len(nids)):
            if is_junction(nids[i]) or i == len(nids) - 1:
                add_seg(nids[start], nids[i], cls, coords[start:i + 1])
                start = i

    # 3) degree-2 chain collapse across ways (same class only). Merging at a
    # node never changes any OTHER node's degree, so ONE pass over sorted
    # node ids is complete and deterministic.
    for nid in sorted(incident):
        inc = incident.get(nid, ())
        if len(inc) != 2 or inc[0] == inc[1]:
            continue
        s1, s2 = segs[inc[0]], segs[inc[1]]
        if s1["cls"] != s2["cls"]:
            continue
        # orient s1 to END at nid, s2 to START at nid
        if s1["u"] == nid:
            s1["coords"].reverse()
            s1["u"], s1["v"] = s1["v"], s1["u"]
        if s2["v"] == nid:
            s2["coords"].reverse()
            s2["u"], s2["v"] = s2["v"], s2["u"]
        a_end, b_end = s1["u"], s2["v"]
        sid1, sid2 = inc
        if a_end == b_end:
            # parallel pair / ring bulb: merging would create a self-loop —
            # keep both segments and the node instead (routing stays exact)
            continue
        s1["coords"] = s1["coords"] + s2["coords"][1:]
        s1["len"] += s2["len"]
        s1["v"] = b_end
        incident[b_end] = [sid1 if s == sid2 else s for s in incident[b_end]]
        del segs[sid2], incident[nid]

    # 4) largest connected component (union-find, path halving).
    parent: dict = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in segs.values():
        for n in (s["u"], s["v"]):
            parent.setdefault(n, n)
        ru, rv = find(s["u"]), find(s["v"])
        if ru != rv:
            parent[max(ru, rv)] = min(ru, rv)          # deterministic union
    comp_nodes: dict = {}
    for n in parent:
        comp_nodes.setdefault(find(n), set()).add(n)
    if not comp_nodes:
        raise RuntimeError("no routable segments survived simplification")
    lcc_root = min(comp_nodes, key=lambda r: (-len(comp_nodes[r]), r))
    keep = comp_nodes[lcc_root]
    total_nodes = sum(len(v) for v in comp_nodes.values())
    lcc_pct = 100.0 * len(keep) / total_nodes

    # 5) renumber + emit RoadEdge list (geometry = intermediates only).
    kept_segs = [s for s in segs.values() if s["u"] in keep]
    node_ids = sorted({n for s in kept_segs for n in (s["u"], s["v"])})
    id_map = {osm: i for i, osm in enumerate(node_ids)}
    node_coord: dict = {}
    for s in kept_segs:
        node_coord[s["u"]] = s["coords"][0]
        node_coord[s["v"]] = s["coords"][-1]
    nodes = {id_map[osm]: LatLon(*node_coord[osm]) for osm in node_ids}
    edges = []
    for s in kept_segs:
        geom = tuple(LatLon(lat, lon) for lat, lon in s["coords"][1:-1])
        edges.append(RoadEdge(u=id_map[s["u"]], v=id_map[s["v"]],
                              highway_class=s["cls"], length_m=s["len"],
                              geometry=geom))
    stats = {
        "ways_fetched": len(ways),
        "raw_way_nodes": raw_node_count,
        "nodes": len(nodes),
        "edges": len(edges),
        "components": len(comp_nodes),
        "largest_component_node_pct": round(lcc_pct, 2),
        "component_nodes_dropped": total_nodes - len(keep),
        "self_loops_dropped": self_loops_dropped,
    }
    return RoadGraph(nodes=nodes, edges=edges, meta=dict(meta, **stats)), stats


# ---------------------------------------------------------------------------
# Per-location driver
# ---------------------------------------------------------------------------

def _summary_from_disk(key: str) -> dict:
    g = load_road_graph(key)
    path = str(road_file(key))
    out = {k: g.meta.get(k) for k in ("ways_fetched", "nodes", "edges",
                                      "largest_component_node_pct",
                                      "fetch_seconds", "fetched_utc")}
    out["file_kb"] = round(os.path.getsize(path) / 1024.0, 1)
    return out


def fetch_one(loc, force: bool) -> "tuple[str, object]":
    """Fetch/refresh one location. Returns ('cached'|'fetched', summary) or
    ('FAILED', error string)."""
    key = loc.key
    path = str(road_file(key))
    bbox = bbox_for(loc)
    print(f"[{key}] {loc.name}")
    print(f"  bbox W {bbox.west:.5f}  S {bbox.south:.5f}  E {bbox.east:.5f}  "
          f"N {bbox.north:.5f}  (tile {tile_half_m(loc) / 1000.0:g} km + "
          f"safe-zone 1.5 km pads)")
    if os.path.exists(path) and not force:
        print(f"  exists — skipping (use --force to refetch): {path}")
        return "cached", _summary_from_disk(key)
    if force and os.path.exists(path):
        os.remove(path)
        print(f"  --force: removed {path}")
    last_err = "never attempted"
    for attempt in range(1, RETRIES + 2):
        t0 = time.monotonic()
        try:
            print(f"  Overpass query (attempt {attempt}/{RETRIES + 1}) ...",
                  flush=True)
            ways = fetch_ways(bbox)
            fetch_s = time.monotonic() - t0
            print(f"  {len(ways):,} ways in {fetch_s:.1f} s; simplifying ...",
                  flush=True)
            meta = {
                "key": key,
                "name": loc.name,
                "bbox_wsen": [round(bbox.west, 6), round(bbox.south, 6),
                              round(bbox.east, 6), round(bbox.north, 6)],
                "source": "overpass-api.de (OpenStreetMap, ODbL)",
                "highway_regex": HIGHWAY_REGEX,
                "fetched_utc": datetime.now(timezone.utc)
                                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generator": "tools/fetch_sim_roads.py",
                "fetch_seconds": round(fetch_s, 1),
            }
            g, stats = build_graph(ways, meta)
            save_road_graph(g, path)
            kb = os.path.getsize(path) / 1024.0
            summary = dict(stats, fetch_seconds=round(fetch_s, 1),
                           file_kb=round(kb, 1))
            print(f"  saved {path} ({kb:,.0f} KB): nodes {stats['nodes']:,} "
                  f"edges {stats['edges']:,} "
                  f"LCC {stats['largest_component_node_pct']}% "
                  f"(dropped {stats['component_nodes_dropped']:,} island nodes "
                  f"across {stats['components'] - 1} islands, "
                  f"{stats['self_loops_dropped']} self-loops)")
            return "fetched", summary
        except Exception as e:  # noqa: BLE001 — one location must not kill the run
            last_err = f"{type(e).__name__}: {e}"
            print(f"  !! [{key}] attempt {attempt}/{RETRIES + 1} FAILED: "
                  f"{last_err}", flush=True)
            if attempt <= RETRIES:
                back = BACKOFF_BASE_S * attempt
                print(f"  retrying in {back:.0f} s (Overpass etiquette) ...")
                time.sleep(back)
    print(f"  ** [{key}] FAILED after {RETRIES + 1} attempts — road graph NOT "
          f"written.\n  ** The simulator falls back to STRAIGHT legs "
          f"(roads.route on_roads=False) for this location; "
          f"tests/test_roads.py will skip it with a loud reason.")
    return "FAILED", last_err


def write_provenance(results: dict) -> dict:
    """Merge this run's results into data/roads/provenance.json (an --only run
    never erases other locations' records)."""
    doc = {"source": "overpass-api.de (OpenStreetMap, ODbL)",
           "highway_regex": HIGHWAY_REGEX, "locations": {}}
    if os.path.exists(PROVENANCE_PATH):
        try:
            with open(PROVENANCE_PATH, "r", encoding="utf-8") as f:
                doc.update(json.load(f))
        except (ValueError, OSError):
            pass                     # rewrite from scratch on a corrupt file
    doc.setdefault("locations", {})
    doc["locations"].update(results)
    os.makedirs(ROAD_DIR, exist_ok=True)
    tmp = PROVENANCE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, PROVENANCE_PATH)
    return doc


def print_report(doc: dict) -> None:
    print("\n---- per-location road-graph report ----")
    print(f"{'key':<14} {'ways':>7} {'nodes':>7} {'edges':>7} "
          f"{'LCC %':>6} {'KB':>8} {'fetch s':>8}")
    for key in sorted(LOCATIONS):
        rec = doc["locations"].get(key)
        if not rec:
            print(f"{key:<14} NO RECORD (never fetched)")
            continue
        if rec["status"] == "FAILED":
            print(f"{key:<14} FAILED — {rec['error']}")
            continue
        s = rec["summary"]
        print(f"{key:<14} {s.get('ways_fetched') or '?':>7} "
              f"{s['nodes']:>7,} {s['edges']:>7,} "
              f"{s['largest_component_node_pct']:>6} "
              f"{s['file_kb']:>8,.1f} {s.get('fetch_seconds') or '?':>8}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch OSM road graphs for the five simulator locations "
                    "(wgresponse.simui.LOCATIONS) into data/roads/*.wgr "
                    "(live Overpass network).")
    ap.add_argument("--force", action="store_true",
                    help="refetch even if the .wgr exists")
    ap.add_argument("--only", action="append", metavar="KEY",
                    choices=sorted(LOCATIONS),
                    help="restrict to one location key (repeatable)")
    args = ap.parse_args(argv)
    # A display name the console cannot encode ('Eagle → Vail corridor, CO'
    # on a Windows cp1252 pipe) prints as backslash escapes, never a crash.
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results: dict = {}
    did_network = False
    for key in sorted(LOCATIONS):
        if args.only and key not in args.only:
            continue
        if did_network:
            time.sleep(POLITE_PAUSE_S)             # one query at a time, politely
        loc = LOCATIONS[key]
        status, payload = fetch_one(loc, args.force)
        did_network = status != "cached"
        rec = {"status": status, "name": loc.name,
               "bbox_wsen": [round(v, 6) for v in
                             (bbox_for(loc).west, bbox_for(loc).south,
                              bbox_for(loc).east, bbox_for(loc).north)],
               "checked_utc": now}
        if status == "FAILED":
            rec["error"] = payload
        else:
            rec["summary"] = payload
            if status == "fetched":
                rec["fetched_utc"] = now
            else:   # cached: keep the .wgr's own fetch timestamp on record
                prev = rec["summary"].pop("fetched_utc", None)
                if prev:
                    rec["fetched_utc"] = prev
        results[key] = rec

    doc = write_provenance(results)
    print(f"\nprovenance written: {PROVENANCE_PATH}")
    print_report(doc)
    failed = [k for k, r in results.items() if r["status"] == "FAILED"]
    if failed:
        print(f"\nEXIT 1 — locations FAILED: {', '.join(failed)} "
              f"(rerun: py -3 tools/fetch_sim_roads.py --only {failed[0]})")
        return 1
    print("\nall requested road graphs present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
