"""scratch_review/worlds_cost_run.py — the TWO WORLDS cost measurement
(docs/LIVE_WORLD_PLAN.md "Test map and live map (2026-09-27)"): drives a
REAL watch daemon (started separately, detached, WGRESP_DEV_OPEN=1) through

    A  IDLE          no drill: the test world costs nothing (baseline)
    B  drill, 0 seed POST /api/test/crews/reset: the test world runs alerts +
                     dispatch over the live snapshot every live cycle
    C  drill, 1 seed POST /api/test/seed: the first test cycle may fetch a
                     fresh LANDFIRE tile (cold), then warm cycles
    D  drill, 5 seeds POST 4 more seeds: cold, then warm

Two independent streams are recorded while it polls every 2 s: every LIVE
cycle (/api/state: wall time + phases) and every TEST cycle (/api/test/state:
wall time, trigger live|action, phases, per-seed terrain / weather / spread
times, shared live domains, plan crews, payload sizes, the test summary build
time) — plus the daemon process's working set / private bytes every 2 s
(GetProcessMemoryInfo via ctypes, argtypes set: the Win64 lesson of the W3.2
note). Writes a JSON report. Seeds: 6 km WSW of five corridor presets, HIGH,
wind FROM 245 (blowing at the homes).

Usage:
    py -3 scratch_review/worlds_cost_run.py --base http://127.0.0.1:18989 \
        --pid <daemon python.exe pid> --out out/worlds_run/cost_report.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

sys.path.insert(0, __file__.rsplit("scratch_review", 1)[0])
from wgresponse import geo  # noqa: E402
from wgresponse.contracts import LatLon  # noqa: E402

PRESETS = [("vail_co", 39.635, -106.36), ("eagle_co", 39.65, -106.89),
           ("aspen_co", 39.21, -106.87),
           ("glenwood_springs_co", 39.545, -107.32),
           ("basalt_carbondale_co", 39.395, -107.125)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC),
                                        wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def mem_mb(handle):
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(c), c.cb):
        return None
    return {"ws_mb": c.WorkingSetSize / 2 ** 20,
            "private_mb": c.PrivateUsage / 2 ** 20,
            "peak_ws_mb": c.PeakWorkingSetSize / 2 ** 20}


def get(base, path, timeout=120):
    t0 = time.monotonic()
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        body = r.read()
    return json.loads(body), len(body), time.monotonic() - t0


def post(base, path, body, method="POST"):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18989")
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--warm-cycles", type=int, default=3)
    ap.add_argument("--max-wait-s", type=float, default=7200.0)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    handle = _k32.OpenProcess(0x1000 | 0x0010, False, args.pid)
    if not handle:
        raise SystemExit(f"cannot open pid {args.pid}")
    phase = {"name": "boot"}
    samples: list = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            m = mem_mb(handle)
            if m:
                samples.append(dict(m, t=time.time(), phase=phase["name"]))
            stop.wait(2.0)
    threading.Thread(target=sampler, daemon=True).start()
    report = {"live": [], "test": [], "phases": {}, "seeds": []}
    t_start = time.time()
    seen = {"live": 0, "test": 0}

    def poll():
        """Record any new live / test cycle; returns (live, test) states."""
        st = get(base, "/api/state")[0]
        if (st.get("cycles_run") or 0) > seen["live"] and \
                st.get("last_cycle_t_s") is not None:
            seen["live"] = st["cycles_run"]
            ph = st.get("last_cycle_phases") or {}
            rec = {"phase": phase["name"], "cycle": seen["live"],
                   "t_s": st["last_cycle_t_s"],
                   "duration_s": st.get("last_cycle_duration_s"),
                   "phases": {k: v for k, v in ph.items() if k != "incidents"},
                   "incidents": len(st.get("incidents") or []),
                   "domains": len(ph.get("incidents") or {}),
                   "wall": time.time() - t_start}
            report["live"].append(rec)
            print("LIVE", json.dumps(rec, default=str), flush=True)
        ts, ts_bytes, _ = get(base, "/api/test/state")
        if (ts.get("cycles_run") or 0) > seen["test"] and \
                ts.get("last_cycle_duration_s") is not None:
            seen["test"] = ts["cycles_run"]
            ph = ts.get("last_cycle_phases") or {}
            inc = ph.get("incidents") or {}
            sm, sm_bytes, sm_dt = get(base, "/api/world/summary?world=test")
            rec = {"phase": phase["name"], "cycle": seen["test"],
                   "t_s": ts.get("last_cycle_t_s"),
                   "live_cycle_t_s": ts.get("live_cycle_t_s"),
                   "trigger": ts.get("last_cycle_trigger"),
                   "duration_s": ts.get("last_cycle_duration_s"),
                   "phases": {k: v for k, v in ph.items() if k != "incidents"},
                   "seed_domains": {iid: v for iid, v in inc.items()
                                    if iid.startswith("seeded:")},
                   "shared_live_domains": sum(
                       1 for v in inc.values() if v.get("shared_from_live")),
                   "plan_crews": len(((ts.get("plan") or {}).get("plans")
                                      or {})),
                   "state_bytes": ts_bytes, "summary_bytes": sm_bytes,
                   "summary_s": round(sm_dt, 3),
                   "wall": time.time() - t_start}
            report["test"].append(rec)
            print("TEST", json.dumps(rec, default=str), flush=True)
        return st, ts

    def run_until(name, cond):
        phase["name"] = name
        while True:
            if time.time() - t_start > args.max_wait_s:
                raise SystemExit(f"run too long in phase {name}")
            poll()
            if cond():
                return
            time.sleep(2.0)

    def n_live(name):
        return sum(1 for r in report["live"] if r["phase"] == name)

    def n_test(name, pred=lambda r: True):
        return sum(1 for r in report["test"]
                   if r["phase"] == name and pred(r))

    w = args.warm_cycles
    try:
        run_until("boot", lambda: seen["live"] >= 1)
        run_until("A_idle", lambda: n_live("A_idle") >= w)
        report["drill_start"] = post(base, "/api/test/crews/reset",
                                     {"by": "cost-run"}).get("drill")
        run_until("B_drill_0_seeds",
                  lambda: n_test("B_drill_0_seeds",
                                 lambda r: r["trigger"] == "live") >= w)
        ids = []
        for k, (key, lat, lon) in enumerate(PRESETS):
            p = geo.destination(LatLon(lat, lon), 245.0, 6000.0)
            out = post(base, "/api/test/seed",
                       {"lat": round(p.lat, 5), "lon": round(p.lon, 5),
                        "intensity": "high", "wind_dir_deg": 245,
                        "note": f"cost seed {k + 1} ({key})",
                        "created_by": "cost-run"})
            ids.append(out["incident_id"])
            report["seeds"].append({"k": k + 1, "preset": key,
                                    "lat": round(p.lat, 5),
                                    "lon": round(p.lon, 5),
                                    "incident_id": out["incident_id"],
                                    "posted_wall": time.time() - t_start})
            print("SEEDED", key, out["incident_id"], flush=True)
            if k == 0:
                run_until("C_1_seed_cold",
                          lambda: n_test("C_1_seed_cold",
                                         lambda r: ids[0] in r["seed_domains"]
                                         ) >= 1)
                run_until("C_1_seed_warm",
                          lambda: n_test("C_1_seed_warm",
                                         lambda r: r["trigger"] == "live"
                                         and ids[0] in r["seed_domains"])
                          >= w)
        run_until("D_5_seeds_cold",
                  lambda: n_test("D_5_seeds_cold",
                                 lambda r: len(r["seed_domains"]) >= 5) >= 1)
        run_until("D_5_seeds_warm",
                  lambda: n_test("D_5_seeds_warm",
                                 lambda r: r["trigger"] == "live"
                                 and len(r["seed_domains"]) >= 5) >= w)
        phase["name"] = "end"
        ts = get(base, "/api/test/state")[0]
        report["final_seeds"] = [
            {"incident_id": s["incident_id"], "note": s["note"],
             "domain": s["domain"], "pairs": s["pairs"],
             "in_last_cycle": s["in_last_cycle"]} for s in ts["seeds"]]
        report["final_notes"] = [n for n in ts["notes"]
                                 if "NON-BURNABLE" in n][:20]
        report["final_drill_log_lines"] = len(ts.get("drill_log") or [])
        report["final_clients_alerted"] = sum(
            1 for c in ts["clients"] if c["state"] not in ("none", "cleared"))
        live = get(base, "/api/state")[0]
        report["final_live_clients_alerted"] = sum(
            1 for c in live["clients"] if c["state"] not in ("none",
                                                             "cleared"))
        report["final_live_has_seed"] = "seeded:" in json.dumps(live)
    finally:
        stop.set()
        time.sleep(2.5)
        by = {}
        for s in samples:
            by.setdefault(s["phase"], []).append(s)
        for name, ss in by.items():
            ws = [s["ws_mb"] for s in ss]
            pv = [s["private_mb"] for s in ss]
            report["phases"].setdefault(name, {})["memory"] = {
                "n": len(ss), "ws_median_mb": round(statistics.median(ws), 1),
                "ws_max_mb": round(max(ws), 1),
                "private_median_mb": round(statistics.median(pv), 1),
                "private_max_mb": round(max(pv), 1),
                "peak_ws_mb": round(max(s["peak_ws_mb"] for s in ss), 1)}
        for name in {r["phase"] for r in report["live"] + report["test"]}:
            agg = report["phases"].setdefault(name, {})
            agg["live_duration_s"] = [round(r["duration_s"] or 0, 2)
                                      for r in report["live"]
                                      if r["phase"] == name]
            agg["test_duration_s"] = [
                [r["trigger"], round(r["duration_s"] or 0, 2)]
                for r in report["test"] if r["phase"] == name]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1, default=str)
        print(json.dumps(report["phases"], indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
