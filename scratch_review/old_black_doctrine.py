"""scratch_review/old_black_doctrine.py — the doctrine nuance of the founder's
2026-09-26 ruling ("old burned ground does not count as a violation"),
measured on the two pinned scenarios where crews actually rest on old black:

  * coarse   — tests/test_simui_policy.py's 600x coarse-jump session (the
               burned-out map: 900 h of 150 h jumps, measured at every
               internal 1-h step);
  * placed   — tests/test_sim_crew_placement.py's worst case (a HIGH fire
               lit ON a placed crew on the controlled grass tile; 15-min
               ticks).

For every crew-tick the STRICT audit would count that the armed audit does
not (the crew rests on old black) it records whether the crew sits inside
the eroded-P10 imminent band of an ACTIVE front (recomputed from every cell
that is not old black) and the distance to the nearest actively burning cell
(scratch_review/old_black_metrics.py). Doctrine: a safety zone in the black
must be large enough around the crew (~4x flame length); the model's rule is
per 90-500 m cell.

Usage: py -3 scratch_review/old_black_doctrine.py
Writes scratch_review/old_black/doctrine.json.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scratch_review.old_black_metrics import Acc, dist_buckets  # noqa: E402


def coarse():
    from tests.test_simui_policy import _session
    s = _session(pathlib.Path(tempfile.mkdtemp()), n_clients=8, n_crews=2)
    acc = Acc()
    s.tick()
    s.set_speed(600.0)
    orig = s._tick_step
    k = {"n": 0}

    def step(stp, *, emit=True, count=True):
        out = orig(stp, emit=emit, count=count)
        k["n"] += 1
        acc.add_tick(s, {"crew_overruns": list(s._crew_overruns),
                         "scoreboard": {}}, k["n"])
        return out
    s._tick_step = step
    for _ in range(6):
        st = s.tick()
    d = acc.as_dict()
    d["relocations"] = st["scoreboard"]["relocations"]
    return d


def placed():
    from tests.test_sim_crew_placement import _on_ew_road, _session
    s = _session(pathlib.Path(tempfile.mkdtemp()), seed="worst")
    p = _on_ew_road(8_000.0)
    s.place_crew("sim-g1", p.lat, p.lon)
    s.ignite(p.lat, p.lon, intensity="high")
    acc = Acc()
    for k in range(1, 13):
        st = s.tick()
        acc.add_tick(s, st, k)
    d = acc.as_dict()
    d["relocations"] = st["scoreboard"]["relocations"]
    return d


def main():
    out = {"coarse": coarse(), "placed": placed()}
    for name, d in out.items():
        print(f"\n== {name}: rows {d['rows_by_reason_mode']}  stopped crew-ticks "
              f"{d['stopped_crew_ticks']} (inside an active front's band "
              f"{d['stopped_in_active_band']})  relocations {d['relocations']}")
        print("   nearest actively burning cell:",
              dist_buckets(d["stopped_dist_burning_m"]))
        print("   nearest front cell (arrival within +-15 min):",
              dist_buckets(d.get("stopped_dist_front_m") or []))
        dfs = [x for x in (d.get("stopped_dist_front_m") or []) if x is not None]
        if dfs:
            print(f"   front: min {min(dfs):.0f} m, median "
                  f"{sorted(dfs)[len(dfs) // 2]:.0f} m, max {max(dfs):.0f} m")
        ds = [x for x in d["stopped_dist_burning_m"] if x is not None]
        if ds:
            print(f"   min {min(ds):.0f} m, median {sorted(ds)[len(ds) // 2]:.0f} m, "
                  f"max {max(ds):.0f} m")
        ages = [a for a in d["stopped_ages_min"] if a is not None]
        if ages:
            print(f"   black age: min {min(ages):.0f} min, max {max(ages):.0f} min "
                  f"(windows {sorted(set(d['stopped_windows_min']))} min)")
        print("   examples:", d["examples"][:3])
    dest = ROOT / "scratch_review" / "old_black" / "doctrine.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\nwrote", dest)


if __name__ == "__main__":
    main()
