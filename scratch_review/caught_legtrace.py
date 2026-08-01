"""caught_legtrace.py -- monkeypatches _SimCrew._begin_leg (read-only probe,
no engine logic changed) to record the EXACT leg (kind/target/on_roads/
edge_kind composition/distance) issued to each watched crew every tick, so
we can see precisely what leg carried a crew INTO its tick-48 catch position
-- the post-tick cs._leg is often None at catch time (SEM-10's relocate_to
clears it to force a re-resolve), so this is the only way to recover what
was actually driven that tick. Loops caught_dissect's scenario (a) until a
catch reproduces (solver_budget_s is wall-clock -> outcome is not perfectly
deterministic run to run; see caught_dissect.py header)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch_review.bench_dispatch import BASE, scenarios  # noqa: E402
from wgresponse import geo, simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402

SEED = "paradise_ca|+0|8000|high"
LEGLOG = []


def _patched_begin_leg(self, kind, target):
    _orig_begin_leg(self, kind, target)
    leg = self._leg
    kinds = leg.get("edge_kind") or []
    dist_m = 0.0
    prev = self.pos
    for p in leg["pts"]:
        dist_m += geo.haversine_m(prev, p)
        prev = p
    LEGLOG.append(dict(
        crew_id=self.crew.crew_id, kind=kind,
        target=(round(target.lat, 6), round(target.lon, 6)),
        on_roads=leg["on_roads"], n_pts=len(leg["pts"]),
        dist_m=round(dist_m, 1),
        kind_counts={k: kinds.count(k) for k in set(kinds)} if kinds else {},
        start_pos=(round(self.pos.lat, 6), round(self.pos.lon, 6)),
    ))


_orig_begin_leg = simui._SimCrew._begin_leg


def build(n_crews, overrides):
    sc = next(s for s in scenarios() if s["seed"] == SEED)
    loc = LOCATIONS[sc["map"]]
    wd = (loc.hazard_wind_dir_deg + sc["wind_off"]) % 360.0
    cfg = dict(BASE)
    cfg.update(overrides)
    s = simui.SimSession(sc["map"], 40, n_crews, seed=sc["seed"],
                         cfg_overrides=dict(cfg), fire_zone="standard")
    for shrink in (1.0, 0.85, 0.7, 0.55):
        p = geo.destination(loc.center, wd, sc["dist"] * shrink)
        try:
            s.ignite(p.lat, p.lon, intensity=sc["inten"], wind_dir_deg=wd)
            break
        except ValueError:
            continue
    return s


def main():
    simui._SimCrew._begin_leg = _patched_begin_leg
    try:
        for attempt in range(1, 13):
            LEGLOG.clear()
            s = build(4, {"deploy_min": 10, "dispatch_plan_on_roads": True,
                         "dispatch_hold_h": 24,
                         "corridor_erosion_euclidean": True,
                         "route_alternatives": 3})
            per_tick_legs = {}
            caught = []
            for _ in range(60):
                before = len(LEGLOG)
                st = s.tick()
                tick_n = st["tick"]
                per_tick_legs[tick_n] = list(LEGLOG[before:])
                for ov in (st.get("crew_overruns") or []):
                    caught.append((tick_n, ov["crew_id"], ov["reason"]))
            if caught:
                print(f"attempt {attempt}: caught={caught}")
                watch = sorted({c for _, c, _ in caught})
                catch_ticks = sorted({t for t, _, _ in caught})
                lo = min(catch_ticks) - 6
                hi = max(catch_ticks)
                for t in range(max(1, lo), hi + 1):
                    for rec in per_tick_legs.get(t, []):
                        if rec["crew_id"] in watch:
                            print(f"  [t{t:>3}] {rec}")
                break
            else:
                print(f"attempt {attempt}: clean run, retrying...")
        else:
            print("no catch reproduced in 12 attempts")
    finally:
        simui._SimCrew._begin_leg = _orig_begin_leg


if __name__ == "__main__":
    main()
