"""Calibrate contracts.solver_budget_evals: how many budget consults does the
wall-clock budget (solver_budget_s, default 5 s in the engine; the simulator
preset value) actually spend per plan on the simulator's scenarios? The
counted budget should let typical plans COMPLETE (deterministic by
construction) while bounding pathological sizes.

Usage: py -3 scratch_review/w9_budget_calib.py
"""
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONHASHSEED", "0")

from wgresponse.contracts import DispatchPlan  # noqa: E402
from wgresponse.simui import SimSession  # noqa: E402


def _last_plan(s):
    for name in ("_plan", "_last_plan", "_incumbent", "_dispatch_plan"):
        p = getattr(s, name, None)
        if isinstance(p, DispatchPlan):
            return p
    for name in dir(s):
        p = getattr(s, name, None)
        if isinstance(p, DispatchPlan):
            return p
    return None


def _ignite_burnable(s, dist0_m=6000.0, intensity="high"):
    """Ignite on the first BURNABLE cell along the hazard-wind bearing from
    dist0_m outward (real tiles put non-burnable developed cells upwind of
    some centers)."""
    from wgresponse import geo
    loc = s._loc
    for d in range(int(dist0_m), 15001, 500):
        for db in (0.0, 10.0, -10.0, 20.0, -20.0):
            p = geo.destination(loc.center, loc.hazard_wind_dir_deg + db, float(d))
            try:
                s.ignite(p.lat, p.lon, intensity=intensity)
                return p
            except ValueError:
                continue
    raise RuntimeError("no burnable ignition cell found")


CAPTURED = []


def _install_capture():
    from wgresponse import dispatch
    orig = dispatch.plan

    def wrapped(*a, **kw):
        plan = orig(*a, **kw)
        CAPTURED.append(dict(plan.objective_breakdown or {}))
        return plan
    dispatch.plan = wrapped


def run(key, n_clients, n_crews, ticks=10, **cfg):
    s = SimSession(key, n_clients, n_crews, seed="budget", cfg_overrides=cfg)
    _ignite_burnable(s, dist0_m=3500.0, intensity="extreme")
    rows = []
    for i in range(ticks):
        n0 = len(CAPTURED)
        t = time.monotonic()
        st = s.tick()
        dt = time.monotonic() - t
        for b in CAPTURED[n0:]:
            rows.append((i, round(dt, 1), b.get("budget_mode"), b.get("budget_spent"),
                         round(b.get("solver_s", 0.0), 2), b.get("candidate"),
                         b.get("seed"), b.get("budget_runaway"), b.get("served")))
    return rows


if __name__ == "__main__":
    _install_capture()
    for key, nc, ncr in (("boulder_co", 16, 2), ("paradise_ca", 40, 8),
                         ("rancho_santa_fe_ca", 100, 10)):
        print(f"== {key} clients={nc} crews={ncr} (wall clock)")
        for r in run(key, nc, ncr):
            print("  ", r)
