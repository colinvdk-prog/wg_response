"""wgresponse.liveroads — the live watcher's road graphs, one per service area
(2026-11-17; founder: "start with the Eagle valley and Aspen, download
Colorado").

Each service area is a road graph cut from an OpenStreetMap extract by
tools/fetch_roads_pbf.py (the simulator's own cutter, same highway classes,
same simplification) and stored as data/roads/live/<area>.wgr, with
data/roads/live/areas.json naming each area, its box and its source. Road
data © OpenStreetMap contributors, ODbL.

A leg is routed on the graph of the service area whose box holds BOTH ends
(the first such area by name). Any other leg — a crew outside every area, a
leg between two areas — gets no road route, and the planner keeps its
straight chord (tier 0) exactly as it did before roads. Nothing here judges
safety: the dispatcher's corridor law samples a road route the way it
samples a chord, and roads.route itself falls back to a loud chord when an
end lies beyond cfg.road_spur_max_m of the network.

The provider is keyed on the EXACT endpoints (the simulator's
plan_route_exact rule, SAFETY SEMANTICS 27a): a leg is judged on the road
the crew would drive from where it stands, never on a neighbour's route."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from . import roads
from .contracts import LatLon, ResponseConfig

#: the live service areas' graphs and their index
DEFAULT_LIVE_ROAD_DIR = roads.DEFAULT_ROAD_DIR / "live"
AREAS_FILE = "areas.json"
#: provider memo bound (cleared whole on overflow, like the simulator's)
_MEMO_MAX = 20_000


class ServiceAreaRoads:
    """The service areas' road graphs, loaded from road_dir (areas.json lists
    {key: {name, bbox_wsen, ...}}; each key's graph is <key>.wgr). A listed
    graph that is missing raises: a configured area must have its roads."""

    def __init__(self, cfg: ResponseConfig, road_dir=None) -> None:
        self._cfg = cfg
        self._dir = Path(road_dir) if road_dir is not None \
            else DEFAULT_LIVE_ROAD_DIR
        index = json.loads((self._dir / AREAS_FILE).read_text(
            encoding="utf-8"))
        self._areas: dict = {}
        for key in sorted((index.get("areas") or {})):
            spec = index["areas"][key]
            g = roads.load_road_graph(key, self._dir)
            if g is None:
                raise FileNotFoundError(
                    f"service area {key!r} is listed in {AREAS_FILE} but "
                    f"{roads.road_file(key, self._dir)} is missing")
            w, s, e, n = (float(x) for x in spec["bbox_wsen"])
            self._areas[key] = {"name": spec.get("name", key),
                                "bbox": (w, s, e, n), "graph": g,
                                "source": spec.get("source"),
                                "extract": spec.get("extract")}
        self._memo: dict = {}

    @property
    def keys(self) -> list:
        return list(self._areas)

    def area_for(self, a: LatLon, b: LatLon) -> Optional[str]:
        """The first area (by key) whose box holds both ends, else None."""
        for key, ar in self._areas.items():
            w, s, e, n = ar["bbox"]
            if (w <= a.lon <= e and s <= a.lat <= n
                    and w <= b.lon <= e and s <= b.lat <= n):
                return key
        return None

    def route(self, a: LatLon, b: LatLon) -> Optional["roads.RoadRoute"]:
        """The road route a -> b inside one service area, or None (no area
        holds both ends: the planner keeps its chord)."""
        k = (a.lat, a.lon, b.lat, b.lon)
        if k in self._memo:
            return self._memo[k]
        key = self.area_for(a, b)
        rr = (None if key is None else
              roads.route(self._areas[key]["graph"], a, b, self._cfg))
        if len(self._memo) >= _MEMO_MAX:
            self._memo.clear()
        self._memo[k] = rr
        return rr

    def provider(self) -> Callable:
        """dispatch.plan's road_provider: (a, b, mode="ingress") -> RoadRoute
        or None. The mode hint is accepted and not used (the fastest road;
        route_alternatives is the simulator's, and stays 1 here)."""
        def _provider(a: LatLon, b: LatLon, mode: str = "ingress"):
            return self.route(a, b)
        return _provider

    def describe(self) -> dict:
        """The /api/state + startup-note summary."""
        return {key: {"name": ar["name"], "bbox_wsen": list(ar["bbox"]),
                      "nodes": len(ar["graph"].nodes),
                      "edges": len(ar["graph"].edges),
                      "source": ar["source"], "extract": ar["extract"]}
                for key, ar in self._areas.items()}
