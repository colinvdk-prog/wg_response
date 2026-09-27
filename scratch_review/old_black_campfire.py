"""scratch_review/old_black_campfire.py — the pinned Camp-Fire-scale extended
run (tests/test_simui.py::test_sem10d_extended_campfire_scale_no_overrun_ever:
paradise_ca EXTENDED, 20 homes, 2 crews, HIGH 13.5 km up the hazard bearing,
30-min ticks, 120 ticks) before/after the founder rulings of 2026-09-26 (old
black is a refuge; a crew that leaves the map is out), with the per-tick
measurements of scratch_review/old_black_metrics.py.

Usage: py -3 scratch_review/old_black_campfire.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scratch_review.old_black_metrics import Acc  # noqa: E402
from wgresponse import geo, simui  # noqa: E402

ARMS = {
    "off": {"old_black_refuge": False, "left_map_out": False,
            "escape_prefer_on_map": False},
    "on": {},
}


def run(extra):
    key = "paradise_ca"
    loc = simui.LOCATIONS[key]
    s = simui.SimSession(
        key, 20, 2, seed="alerted|paradise_ca",
        cfg_overrides={"spotting_enabled": True, "grass_ros_floor_frac": 0.10,
                       "deploy_min": 15.0, **extra},
        fire_zone="extended")
    ign = geo.destination(loc.center, loc.hazard_wind_dir_deg, 13_500.0)
    s.ignite(ign.lat, ign.lon, intensity="high",
             wind_dir_deg=loc.hazard_wind_dir_deg)
    s.set_speed(2.0)
    acc = Acc()
    rows = 0
    for k in range(120):
        st = s.tick()
        acc.add_tick(s, st, k + 1)
        rows += len(st["crew_overruns"])
    sb = st["scoreboard"]
    return {"rows": rows, "served": sb["served"], "relocations": sb["relocations"],
            "crews_left_map": sb.get("crews_left_map", 0),
            "stopped": acc.stopped, "stopped_in_active_band": acc.in_active,
            "dist_burning": acc.dist, "departure_rows": len(st["audit"]) and
            sum(1 for a in st["audit"] if a["violation"])}


def main():
    out = {arm: run(extra) for arm, extra in ARMS.items()}
    for arm, r in out.items():
        print(arm, {k: v for k, v in r.items() if k != "dist_burning"},
              "dist_burning", r["dist_burning"][:10])
    dest = ROOT / "scratch_review" / "old_black" / "campfire.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(dest, "w", encoding="utf-8"), indent=1)
    print("wrote", dest)


if __name__ == "__main__":
    main()
