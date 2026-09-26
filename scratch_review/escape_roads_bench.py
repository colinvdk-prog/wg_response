"""scratch_review/escape_roads_bench.py — what does the SEM-10d road-network
escape search cost (contracts escape_road_network)?

Two measurements on the task's two graphs — eagle_vail_co (standard tile,
~6.8k nodes) and boulder_co EXTENDED (~42.9k nodes, the largest graph):

1. SEARCH COST: grow a fire (page defaults, HIGH, the probe's upwind
   ignition), then time _road_escape_search from probe points — every safe
   zone, every crew, and a ring of points around the fire's edge — cold
   (first search in the session: edge samples computed) and warm (cache
   filled), plus the settled-node count; and the legacy _best_escape ray
   scoring from the same points for scale.
2. TICK COST: the same scenario run twice, escape_road_network off vs on
   (explicit), per-tick wall ms (median / p95 / max) and the ticks on which a
   crew fled.

Usage: py -3 scratch_review/escape_roads_bench.py [--ticks N]
Writes scratch_review/escape_roads/bench.json and prints tables.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wgresponse import geo, simui  # noqa: E402

OUT = ROOT / "scratch_review" / "escape_roads"
PAGE_CFG = {"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
            "dispatch_late_shift": True, "dispatch_plan_on_roads": True,
            "dispatch_hold_h": 24.0, "deploy_min": 15.0,
            "dispatch_sequence": "auto"}
CASES = [
    # key, zone, intensity, ignition distance (m), ticks for the tick-cost
    # run (boulder extended: the offroad probe's extended-stress scenario,
    # where a crew flees at ~t70)
    ("eagle_vail_co", "standard", "high", 7000.0, 40),
    ("boulder_co", "extended", "extreme", 14000.0, 80),
]


def _session(key, zone, extra):
    return simui.SimSession(key, 40, 6, seed="roads-probe",   # the offroad probe's sessions
                            cfg_overrides=dict(PAGE_CFG, **extra),
                            fire_zone=zone, n_crews_1h=1, n_crews_3h=1)


def _ignite(s, key, intensity, dist):
    loc = simui.LOCATIONS[key]
    for db in (0, 10, -10, 20, -20):
        for shrink in (1.0, 0.8, 0.6, 0.4):
            p = geo.destination(loc.center, loc.hazard_wind_dir_deg + db,
                                dist * shrink)
            try:
                s.ignite(p.lat, p.lon, intensity=intensity,
                         wind_dir_deg=loc.hazard_wind_dir_deg)
                return p
            except ValueError:
                continue
    raise RuntimeError("no ignition")


def _probe_points(s, n_ring=16):
    pts = [z.loc for z in s._loc.safe_zones]
    pts += [cs.pos for cs in s._crew_sims.values()
            if cs.status.value != "inbound"]
    # a ring of points around the burned area's centroid at the eroded-P10
    # "imminent" distance: where fleeing crews actually start from
    now = s._now_s
    f = next(iter(s._fires.values()))
    er = s._eroded_p10(f)
    spec = er.spec
    cells = [(r, c) for r in range(0, spec.nrows, 3) for c in range(0, spec.ncols, 3)
             if now + 1800.0 <= er.data[r * spec.ncols + c] <= now + 5400.0]
    cells.sort()
    step = max(1, len(cells) // n_ring)
    for r, c in cells[::step][:n_ring]:
        pts.append(geo.rc_to_latlon(spec, r, c))
    return pts


def search_cost(key, zone, intensity, dist, ticks):
    s = _session(key, zone, {"escape_road_network": True})
    _ignite(s, key, intensity, dist)
    for _ in range(ticks):
        s.tick()
    now = s._now_s
    lead = s._cfg.replan_period_s
    speed = next(iter(s._crew_sims.values())).speed
    pts = _probe_points(s)
    rows = []
    s._esc_edge_cache.clear()
    for i, p in enumerate(pts):
        t0 = time.perf_counter()
        found = s._road_escape_search(p, now, speed, lead)
        cold = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        found2 = s._road_escape_search(p, now, speed, lead)
        warm = (time.perf_counter() - t0) * 1000.0
        assert (found is None) == (found2 is None)
        # legacy scoring of the ray proposals from the same point, for scale
        keep = s._cfg.escape_road_network
        s._cfg.escape_road_network = False
        t0 = time.perf_counter()
        s._best_escape(p, now, speed, lead)
        legacy = (time.perf_counter() - t0) * 1000.0
        s._cfg.escape_road_network = keep
        rows.append({"i": i, "cold_ms": round(cold, 1), "warm_ms": round(warm, 1),
                     "legacy_best_escape_ms": round(legacy, 1),
                     "settled": None if found is None else found["settled"],
                     "found": found is not None,
                     "exposure_s": None if found is None else round(found["exposure_s"], 1),
                     "horizon_h": (None if found is None else
                                   ("inf" if math.isinf(found["horizon_s"]) else
                                    round(found["horizon_s"] / 3600.0, 2))),
                     "drive_min": None if found is None else round(found["total_s"] / 60.0, 1)})
    return {"key": key, "zone": zone, "graph_nodes": len(s._road_graph.nodes),
            "ticks_grown": ticks, "points": len(pts), "rows": rows}


def tick_cost(key, zone, intensity, dist, ticks):
    out = {}
    for arm, extra in (("off", {"escape_road_network": False}),
                       ("on", {"escape_road_network": True})):
        s = _session(key, zone, extra)
        _ignite(s, key, intensity, dist)
        ms = []
        flee_ticks = []
        n_notes = 0
        for k in range(ticks):
            reloc0 = s._n_relocations
            t0 = time.perf_counter()
            st = s.tick()
            ms.append((time.perf_counter() - t0) * 1000.0)
            if s._n_relocations != reloc0 or any(
                    "escape" in n for n in st["notes"][n_notes:]):
                flee_ticks.append(k + 1)
            n_notes = len(st["notes"])
        q = sorted(ms)
        out[arm] = {"median_ms": round(statistics.median(ms), 1),
                    "p95_ms": round(q[int(0.95 * (len(q) - 1))], 1),
                    "max_ms": round(max(ms), 1), "total_s": round(sum(ms) / 1000.0, 1),
                    "flee_ticks": flee_ticks,
                    "flee_tick_ms": [round(ms[k - 1], 1) for k in flee_ticks],
                    "relocations": st["scoreboard"]["relocations"],
                    "violations": st["scoreboard"]["violations"]}
    return {"key": key, "zone": zone, "ticks": ticks, **out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=0, help="override every case's tick count")
    ap.add_argument("--grow", type=int, default=16)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    res = {"search": [], "tick": []}
    for key, zone, inten, dist, n_ticks in CASES:
        sc = search_cost(key, zone, inten, dist, a.grow)
        res["search"].append(sc)
        cold = [r["cold_ms"] for r in sc["rows"]]
        warm = [r["warm_ms"] for r in sc["rows"]]
        leg = [r["legacy_best_escape_ms"] for r in sc["rows"]]
        settled = [r["settled"] for r in sc["rows"] if r["settled"] is not None]
        print(f"SEARCH {key}/{zone} nodes={sc['graph_nodes']} points={sc['points']}: "
              f"cold median {statistics.median(cold):.1f} max {max(cold):.1f} ms; "
              f"warm median {statistics.median(warm):.1f} max {max(warm):.1f} ms; "
              f"legacy _best_escape median {statistics.median(leg):.1f} max {max(leg):.1f} ms; "
              f"settled median {statistics.median(settled) if settled else '-'} "
              f"max {max(settled) if settled else '-'}", flush=True)
        tc = tick_cost(key, zone, inten, dist, a.ticks or n_ticks)
        res["tick"].append(tc)
        for arm in ("off", "on"):
            r = tc[arm]
            print(f"TICK {key}/{zone} {arm}: median {r['median_ms']} p95 {r['p95_ms']} "
                  f"max {r['max_ms']} ms, total {r['total_s']} s, flee ticks "
                  f"{r['flee_ticks']} ({r['flee_tick_ms']} ms), relocations "
                  f"{r['relocations']}", flush=True)
    (OUT / "bench.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("wrote", OUT / "bench.json")


if __name__ == "__main__":
    main()
