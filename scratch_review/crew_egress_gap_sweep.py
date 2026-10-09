"""Crew-safety sweep over the one-click scenarios (2026-10-09; the egress-gap
fix, docs/SIMULATOR_DESIGN.md "No crew drives an unjudged leg"). NOT product
code.

Every one-click scenario (server.SCENARIOS) exactly as the page builds it
(spotting on, grass floor 0.10, plan on roads, 24 h hold, the simulator's
defaults), at low / moderate / high / extreme, under city and generic weather,
with the grass-only floor (the simulator default) and the old all-fuel floor,
for HOURS hours (default 6). A scenario at an intensity other than its preset
is re-lit at the preset's own ignition point and wind (as the page does).

Per run: the per-tick maximum of the serviced-stop departure violations, the
ticks with a SEM-10c crew-overrun row (and the first row), homes served (gel
applied), withdrawals, relocations, crews that left the map, acres burned,
wall seconds.

Usage:
  py -3 scratch_review/crew_egress_gap_sweep.py OUT.jsonl [--workers N]
        [--hours H] [--only KEY[,KEY]] [--cfg JSON]
  py -3 scratch_review/crew_egress_gap_sweep.py --table A.jsonl [B.jsonl]
  py -3 scratch_review/crew_egress_gap_sweep.py --doc A.jsonl B.jsonl

--cfg adds ResponseConfig overrides to every run (e.g.
'{"corridor_cell_exact": false, "crew_egress_judged": false}' measures the
law off on the current code). Runs already in OUT are skipped (resume).
--table prints the per-combination table (A = before, B = after); --doc the
markdown table of docs/SIMULATOR_DESIGN.md "No crew drives an unjudged leg".

Recorded 2026-10-09 (4-5 workers): crew_egress_gap_sweep_before.jsonl (commit
8973cd9, before SAFETY SEMANTICS 20) and crew_egress_gap_sweep_after.jsonl
(both knobs armed, the simulator default).
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

INTENSITIES = ("low", "moderate", "high", "extreme")
PROFILES = ("city", "generic")
FLOORS = (("grass", True), ("all", False))


def combos(only=None):
    from wgresponse import server
    out = []
    for key in sorted(server.SCENARIOS):
        if only and key not in only:
            continue
        for inten in INTENSITIES:
            for prof in PROFILES:
                for fname, _flag in FLOORS:
                    out.append((key, inten, prof, fname))
    return out


def run_one(args):
    key, inten, prof, fname, hours, extra = args
    from wgresponse import server
    t0 = time.time()
    mgr = server.SimSessionManager()
    cfg = {"sim_weather_profile": prof,
           "ros_floor_grass_only": dict(FLOORS)[fname]}
    cfg.update(extra or {})
    code, out = mgr.new_session({"scenario": key, "cfg": cfg})
    if code != 200:
        return {"key": key, "intensity": inten, "profile": prof,
                "floor": fname, "error": out}
    s = mgr._entry(out["session_id"]).session
    f = s._fires["sim-f1"]
    if inten != f.intensity:
        lat, lon, wd = f.origin.lat, f.origin.lon, f.wind_dir_deg
        s.reset()
        s.ignite(lat, lon, intensity=inten, wind_dir_deg=wd)
    dep_max, viol_max, ov_ticks, ov_rows, first = 0, 0, 0, 0, None
    st = None
    for k in range(int(round(hours * 4))):
        st = s.tick()
        sb = st["scoreboard"]
        dep_max = max(dep_max, len(s._violations))
        viol_max = max(viol_max, sb["violations"])
        rows = st.get("crew_overruns") or []
        if rows:
            ov_ticks += 1
            ov_rows += len(rows)
            if first is None:
                r = rows[0]
                first = (f"T+{(k + 1) * 0.25:.2f}h {r['crew_id']} "
                         f"{r['reason']} {r['mode']}")
    sb = st["scoreboard"]
    res = {"key": key, "intensity": inten, "profile": prof, "floor": fname,
           "hours": hours, "dep_viol_max": dep_max, "viol_max": viol_max,
           "overrun_ticks": ov_ticks, "overrun_rows": ov_rows,
           "first_overrun": first, "served": sb["served"],
           "withdrawn": sb["withdrawn"], "relocations": sb["relocations"],
           "left_map": sb.get("crews_left_map", 0),
           "acres": round(st["fire_view"]["areas_km2"]["fire"] * 247.105),
           "secs": round(time.time() - t0, 1)}
    with mgr._lock:
        mgr._sessions.clear()
    return res


def _done(path):
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    done.add((r["key"], r["intensity"], r["profile"],
                              r["floor"]))
    return done


def sweep(out_path, workers=1, hours=6.0, only=None, extra=None):
    todo = [c for c in combos(only) if c not in _done(out_path)]
    args = [c + (hours, extra) for c in todo]
    print(f"{len(todo)} runs to go -> {out_path}", flush=True)
    if workers <= 1:
        it = map(run_one, args)
        pool = None
    else:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(workers)
        it = pool.imap_unordered(run_one, args)
    for res in it:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(res, sort_keys=True) + "\n")
        print(json.dumps(res, sort_keys=True), flush=True)
    if pool is not None:
        pool.close()
        pool.join()


def _load(path):
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[(r["key"], r["intensity"], r["profile"], r["floor"])] = r
    return rows


def table(a_path, b_path=None):
    a = _load(a_path)
    b = _load(b_path) if b_path else {}
    keys = sorted(a, key=lambda k: (k[0], INTENSITIES.index(k[1]), k[2], k[3]))
    hdr = ("scenario", "int", "wx", "floor", "dep", "ovt", "served")
    print(" | ".join(hdr) + (" || dep' | ovt' | served' | first' " if b else
                             " | first"))
    tot = {"a_ov": 0, "a_dep": 0, "a_srv": 0, "b_ov": 0, "b_dep": 0,
           "b_srv": 0, "n": 0}
    for k in keys:
        r = a[k]
        line = (f"{k[0]} | {k[1]} | {k[2]} | {k[3]} | {r['dep_viol_max']} | "
                f"{r['overrun_ticks']} | {r['served']}")
        tot["a_ov"] += r["overrun_ticks"]
        tot["a_dep"] += r["dep_viol_max"]
        tot["a_srv"] += r["served"]
        if b:
            q = b.get(k)
            if q is None:
                line += " || - | - | - |"
            else:
                tot["b_ov"] += q["overrun_ticks"]
                tot["b_dep"] += q["dep_viol_max"]
                tot["b_srv"] += q["served"]
                line += (f" || {q['dep_viol_max']} | {q['overrun_ticks']} | "
                         f"{q['served']} | {q['first_overrun'] or ''}")
        else:
            line += f" | {r['first_overrun'] or ''}"
        tot["n"] += 1
        print(line)
    print(tot)


def doc_table(a_path, b_path):
    """Markdown table for docs/SIMULATOR_DESIGN.md: one row per scenario x
    intensity, one column per (weather, floor); each cell "served before ->
    after", with the overrun ticks "[ovt b -> a]" where either is nonzero and
    the departure violations "[dep b -> a]" likewise."""
    a, b = _load(a_path), _load(b_path)
    cols = [(p, f) for p in PROFILES for f, _flag in FLOORS]
    print("| Scenario | Intensity | " + " | ".join(
        f"{p} / {'grass-only' if f == 'grass' else 'all-fuel'} floor"
        for p, f in cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    keys = sorted({k[0] for k in a})
    for key in keys:
        for inten in INTENSITIES:
            cells = []
            for p, f in cols:
                r, q = a.get((key, inten, p, f)), b.get((key, inten, p, f))
                if r is None or q is None:
                    cells.append("-")
                    continue
                c = f"{r['served']} → {q['served']}"
                if r["overrun_ticks"] or q["overrun_ticks"]:
                    c += f" [ovt {r['overrun_ticks']} → {q['overrun_ticks']}]"
                if r["dep_viol_max"] or q["dep_viol_max"]:
                    c += f" [dep {r['dep_viol_max']} → {q['dep_viol_max']}]"
                cells.append(c)
            print(f"| {key} | {inten} | " + " | ".join(cells) + " |")
    both = [k for k in a if k in b]
    print()
    print(f"runs {len(both)}; overrun ticks "
          f"{sum(a[k]['overrun_ticks'] for k in both)} -> "
          f"{sum(b[k]['overrun_ticks'] for k in both)}; overrun rows "
          f"{sum(a[k]['overrun_rows'] for k in both)} -> "
          f"{sum(b[k]['overrun_rows'] for k in both)}; departure violations "
          f"{sum(a[k]['dep_viol_max'] for k in both)} -> "
          f"{sum(b[k]['dep_viol_max'] for k in both)}; served "
          f"{sum(a[k]['served'] for k in both)} -> "
          f"{sum(b[k]['served'] for k in both)}; withdrawn "
          f"{sum(a[k]['withdrawn'] for k in both)} -> "
          f"{sum(b[k]['withdrawn'] for k in both)}; relocations "
          f"{sum(a[k]['relocations'] for k in both)} -> "
          f"{sum(b[k]['relocations'] for k in both)}; left map "
          f"{sum(a[k]['left_map'] for k in both)} -> "
          f"{sum(b[k]['left_map'] for k in both)}; wall s "
          f"{round(sum(a[k]['secs'] for k in both))} -> "
          f"{round(sum(b[k]['secs'] for k in both))}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--table":
        table(*argv[1:3])
        sys.exit(0)
    if argv and argv[0] == "--doc":
        doc_table(argv[1], argv[2])
        sys.exit(0)
    out_path = argv[0]
    workers, hours, only, extra = 1, 6.0, None, None
    i = 1
    while i < len(argv):
        if argv[i] == "--workers":
            workers = int(argv[i + 1])
            i += 2
        elif argv[i] == "--hours":
            hours = float(argv[i + 1])
            i += 2
        elif argv[i] == "--only":
            only = set(argv[i + 1].split(","))
            i += 2
        elif argv[i] == "--cfg":
            extra = json.loads(argv[i + 1])
            i += 2
        else:
            raise SystemExit(f"unknown arg {argv[i]}")
    sweep(out_path, workers=workers, hours=hours, only=only, extra=extra)
