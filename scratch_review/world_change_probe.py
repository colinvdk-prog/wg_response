"""The rug-pull choreography at nearby geometries (2026-10-14; contracts SAFETY
SEMANTICS 23, docs/SIMULATOR_DESIGN.md "A world change re-judges committed
work"). NOT product code.

The scripted world change of tests/test_simui_policy.py (value objective,
FAST_TIGHT, 16 clients / 1 crew, a high fire 4.6 km upwind of the boulder_co
checkerboard tile, straight legs): tick until the crew deploys at its Nth
distinct client, plant a second fire D m dead upwind of that client at
INTENSITY with the domain wind swung onto it (the phase-2 spot fire), then
tick TICKS more with no slider move (the control twin). Every SEM-10c crew-
overrun row is attributed to the crew's mode / leg kind BEFORE the end-of-
step sweep ran (the sweep's relocation rewrites the mode to egress), and
flagged world_change when it is a SEMANTICS 23 instant row.

Usage:
  py -3 scratch_review/world_change_probe.py ANY(0|1) OUT.jsonl
        [--d 750,1000,...] [--int moderate,high,extreme] [--nth 1,2]
        [--seed m1] [--ign 4600] [--ticks 12] [--cfg JSON]
  py -3 scratch_review/world_change_probe.py --compare BEFORE.jsonl AFTER.jsonl

--cfg '{"crew_work_judged": false}' measures the law off on the current code
(byte-identical to commit 3dd341a, before the rule). Recorded 2026-10-14:
world_change_probe_before.jsonl (the law off) and world_change_probe_after
.jsonl (the simulator default), both engines (ANY 0 and 1), D 750-3000,
moderate / high / extreme, first and second deploy: 96 runs each.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]


def _inject(s, st, stop, dist_m, intensity):
    from wgresponse import geo
    from wgresponse.contracts import LatLon
    victim = stop["client_id"]
    cl = next(c for c in st["clients"] if c["client_id"] == victim)
    vloc = LatLon(cl["lat"], cl["lon"])
    f1 = next(f for f in st["fires"] if f["incident_id"] == stop["incident_id"])
    w_new = (geo.bearing_deg(LatLon(f1["lat"], f1["lon"]), vloc) + 180.0) % 360.0
    d = float(dist_m)
    for _ in range(60):
        p = geo.destination(vloc, w_new, d)
        try:
            return s.ignite(p.lat, p.lon, intensity=intensity,
                            wind_dir_deg=w_new), d
        except ValueError:
            d += 250.0
    return None, None


def run(any_angle, d, inten, ign, ticks, extra, seed="m1", nth=1):
    import test_simui_policy as T
    from wgresponse import geo
    from wgresponse.simui import SimSession, LOCATIONS
    tmp = pathlib.Path(tempfile.mkdtemp())
    dd = T._checkerboard_dir(tmp)
    cfg = {**T.FAST_TIGHT, "dispatch_objective": "value",
           "spread_any_angle": any_angle}
    cfg.update(extra or {})
    s = SimSession(T.KEY, 16, 1, seed=seed, cfg_overrides=cfg, tile_dir=dd,
                   roads_module=None)
    loc = LOCATIONS[T.KEY]
    p = geo.destination(loc.center, loc.hazard_wind_dir_deg, float(ign))
    s.ignite(p.lat, p.lon, intensity="high")
    seen = []
    st = None
    for _ in range(60):
        st = s.tick()
        c = st["crews"][0]
        if c["status"] == "deploying" and c["itinerary"]:
            if c["itinerary"][0]["client_id"] not in seen:
                seen.append(c["itinerary"][0]["client_id"])
        if len(seen) >= nth:
            break
    if len(seen) < nth:
        return {"armed": False}
    cur = st["crews"][0]["itinerary"][0]
    arm_tick, t0 = st["tick"], st["t0_s"]
    pre = {}
    orig = s._crew_safety_sweep

    def sweep(lead_s, **kw):
        if not kw.get("emergency_only"):
            pre.clear()
            for cid, cs in s._crew_sims.items():
                leg = cs._leg
                pre[cid] = (cs._mode, None if leg is None else leg.get("kind"))
        return orig(lead_s, **kw)

    s._crew_safety_sweep = sweep
    iid, dact = _inject(s, st, cur, d, inten)
    if iid is None:
        return {"armed": True, "inject": False}
    rows = []
    dep = 0
    for _ in range(ticks):
        st = s.tick()
        dep = max(dep, len(s._violations))
        for o in st["crew_overruns"]:
            rows.append({
                "tick": st["tick"], "reason": o["reason"],
                "mode_after": o["mode"], "pre": pre.get(o["crew_id"]),
                "world_change": bool(o.get("world_change")),
                "p10e_min": (None if o["eroded_p10_s"] is None
                             else round((o["eroded_p10_s"] - t0) / 60, 1))})
    sb = st["scoreboard"]
    return {"armed": True, "arm_tick": arm_tick, "victim": cur["client_id"],
            "spot_d": dact, "overrun_rows": rows, "dep_viol_max": dep,
            "served": sb["served"], "withdrawn": sb["withdrawn"]}


def _load(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                out[(r["any"], r["seed"], r["ign"], r["int"], r["nth"],
                     r["d"])] = r
    return out


def compare(a_path, b_path):
    a, b = _load(a_path), _load(b_path)
    for eng in (0, 1):
        keys = sorted(k for k in a if k in b and k[0] == eng)
        t = collections.Counter()
        for k in keys:
            ra, rb = a[k], b[k]
            for tag, r in (("a", ra), ("b", rb)):
                rows = r.get("overrun_rows") or []
                t[f"rows_{tag}"] += len(rows)
                t[f"runs_{tag}"] += bool(rows)
                t[f"instant_{tag}"] += sum(o.get("world_change", False)
                                           for o in rows)
                t[f"dep_{tag}"] += r.get("dep_viol_max", 0)
                t[f"served_{tag}"] += r.get("served", 0)
                t[f"wd_{tag}"] += r.get("withdrawn", 0)
        print(("any-angle" if eng else "16-direction"), len(keys), "runs:",
              dict(t))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        compare(sys.argv[2], sys.argv[3])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("any", type=int)
    ap.add_argument("out")
    ap.add_argument("--d", default="750,1000,1250,1500,1750,2000,2500,3000")
    ap.add_argument("--int", default="moderate,high,extreme")
    ap.add_argument("--ign", default="4600")
    ap.add_argument("--nth", default="1,2")
    ap.add_argument("--seed", default="m1")
    ap.add_argument("--ticks", type=int, default=12)
    ap.add_argument("--cfg", default="{}")
    a = ap.parse_args()
    extra = json.loads(a.cfg)
    for ign in [float(x) for x in a.ign.split(",")]:
        for inten in a.int.split(","):
            for nth in [int(x) for x in a.nth.split(",")]:
                for seed in a.seed.split(","):
                    for d in [float(x) for x in a.d.split(",")]:
                        r = run(bool(a.any), d, inten, ign, a.ticks, extra,
                                seed=seed, nth=nth)
                        r.update({"any": a.any, "d": d, "int": inten,
                                  "ign": ign, "nth": nth, "seed": seed})
                        line = json.dumps(r, sort_keys=True)
                        print(line, flush=True)
                        with open(a.out, "a", encoding="utf-8") as fh:
                            fh.write(line + "\n")


if __name__ == "__main__":
    main()
