"""Measurement wrapper for the 2026-09-27 national validation run (NOT
product code; the W6 run's wrapper, extended). Runs `wgresponse.cli watch`
with the given argv, INFO logging, and one telemetry line per cycle in
out/contained/live_cycles.jsonl: duration, phases, incidents on the map,
removed as contained, domains, events, clients by worst ladder state,
Chileno / Willow status, memory. The daemon is untouched: run_cycle is
wrapped from outside."""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
CHILENO = "df96eda9-9b27-47fb-86d5-dfb97a879a22"
WILLOW = "548a8d28-1a04-48ee-9011-aba023175d43"


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


_k32 = ctypes.windll.kernel32
_k32.GetCurrentProcess.restype = wt.HANDLE
_psapi = ctypes.windll.psapi
_psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
_psapi.GetProcessMemoryInfo.restype = wt.BOOL


def mem_mb():
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(pmc),
                                pmc.cb)
    return {"ws_mb": round(pmc.WorkingSetSize / 1e6, 1),
            "peak_ws_mb": round(pmc.PeakWorkingSetSize / 1e6, 1),
            "private_mb": round(pmc.PrivateUsage / 1e6, 1)}


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from wgresponse import cli
    orig = cli._WatchDaemon.run_cycle

    def run_cycle(self):
        t0 = time.time()
        res = orig(self)
        st = self.state()
        by_state = {}
        for c in st.get("clients") or []:
            by_state[c["state"]] = by_state.get(c["state"], 0) + 1
        ph = dict(getattr(res, "timings", {}) or {})
        inc_t = ph.pop("incidents", {}) or {}
        try:
            states = self._engine.states()
        except Exception:
            states = {}

        def pairs_of(iid):
            out = {}
            for (_c, i), r in states.items():
                if i == iid:
                    out[r.state.value] = out.get(r.state.value, 0) + 1
            return out
        rec = {
            "cycle": self.cycles_run, "t_start": round(t0),
            "duration_s": round(time.time() - t0, 1),
            "phases_s": {k: round(v, 2) for k, v in ph.items()
                         if isinstance(v, (int, float))},
            "terrain_s": round(sum(float(d.get("terrain_s", 0.0))
                                   for d in inc_t.values()), 1),
            "spread_s": round(sum(float(d.get("spread_s", 0.0))
                                  for d in inc_t.values()), 1),
            "incidents": len(res.incidents),
            "removed_contained": st.get("removed_contained"),
            "domains": len(res.ensembles),
            "assessments": len(res.assessments),
            "events": len(res.events),
            "degraded": bool(res.degraded),
            "operator_events": [(e.kind, e.message[:160])
                                for e in res.operator_events],
            "clients_by_state": dict(sorted(by_state.items())),
            "chileno": {"on_map": any(i.incident_id == CHILENO
                                      for i in res.incidents),
                        "removed": any(r.incident.incident_id == CHILENO
                                       for r in res.removed_incidents),
                        "pairs": pairs_of(CHILENO)},
            "willow": {"on_map": any(i.incident_id == WILLOW
                                     for i in res.incidents),
                       "domain": WILLOW in res.ensembles,
                       "pairs": pairs_of(WILLOW),
                       "notes": [n[:400] for n in res.notes
                                 if n.startswith("incident " + WILLOW)]},
            "per_domain": {iid: {k: (round(v, 2) if isinstance(v, float) else v)
                                 for k, v in d.items()}
                           for iid, d in inc_t.items()},
            "mem": mem_mb(),
        }
        with open(ROOT / "out" / "contained" / "live_cycles.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        with open(ROOT / "out" / "contained" / "live_state_last.json", "w", encoding="utf-8") as f:
            json.dump(st, f, default=str)
        logging.getLogger("contained").info(
            "CYCLE %s done: %s", self.cycles_run, json.dumps(
                {k: rec[k] for k in ("duration_s", "phases_s", "incidents",
                                     "removed_contained", "domains", "events",
                                     "clients_by_state", "mem")}))
        return res

    cli._WatchDaemon.run_cycle = run_cycle
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
