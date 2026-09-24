"""scratch_review/corridor_viol.py — characterize every safety-audit
violation the Vail -> Aspen corridor grid reported (corridor_grid.py rows
with viol > 0): re-run those scenarios (deterministic seeds -> identical
trajectories) and classify, tick by tick, what the audit actually counted:

  * departure-audit rows (SEMANTICS 10a/10b: a serviced stop departed later
    than its latest-depart — the crew-in-the-fire failure the law exists to
    prevent), and
  * 10c crew-overrun rows by (reason, mode): `burned` = a crew standing on a
    cell the nominal front has already passed; `imminent` = inside the
    eroded-P10 band within the egress buffer, split by what the crew was
    doing (egress / idle / transit / service).

The 4-hour experiment (2026-09-11) found every base-arm violation was an
imminent-while-egressing row with zero departure violations and no crew in
burned fuel; this script asks the same question of the corridor, per arm and
community, and writes scratch_review/corridor/violations.json + a markdown
table. Usage: py -3 scratch_review/corridor_viol.py [--workers 12]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONHASHSEED", "0")

from scratch_review.corridor_grid import (ARMS, CFG, N_CLIENTS, OUT_DIR,  # noqa: E402
                                          _ignite, base_arm, crew_delay_min,
                                          road_homes)
from wgresponse import simui  # noqa: E402
from wgresponse.simui import LOCATIONS  # noqa: E402


def rerun(job):
    """(arm, row) -> per-scenario classification of every audit row."""
    arm, row = job
    n_local, n_eagle, fire_zone, ticks, base = ARMS[base_arm(arm)]
    loc = LOCATIONS[row["map"]]
    wd = (loc.hazard_wind_dir_deg + row["wind_off"]) % 360.0
    cfg = dict(CFG)
    if n_eagle:
        cfg["crew_reinforce_1h_min"] = crew_delay_min(row["map"], base)
    locs = road_homes(row["map"], row["seed"], N_CLIENTS) if arm.endswith("_road") else None
    s = simui.SimSession(row["map"], N_CLIENTS, n_local, seed=row["seed"],
                         cfg_overrides=cfg, fire_zone=fire_zone, n_crews_1h=n_eagle,
                         client_locs=locs)
    assert _ignite(s, loc, wd, row["dist"], row["inten"], fire_zone == "extended")
    kinds = Counter()          # (reason, mode) -> crew-ticks
    departure_rows_max = 0
    overrun_ticks = 0
    worst_past_p10_min = 0.0   # how far inside the band the worst imminent row sat
    burned_rows = []           # every burned row: age of the black + zone context
    zones = [(z.zone_id, z.loc) for z in loc.safe_zones]
    for _ in range(ticks):
        st = s.tick()
        sb = st["scoreboard"]
        dep = sb.get("violations", 0) - sb.get("crew_overruns", 0)
        departure_rows_max = max(departure_rows_max, dep)
        if st.get("crew_overruns"):
            overrun_ticks += 1
        now = st["sim_now_s"]
        for o in st["crew_overruns"]:
            kinds[(o["reason"], o["mode"])] += 1
            if o["reason"] == "imminent" and o.get("eroded_p10_s") is not None:
                worst_past_p10_min = max(worst_past_p10_min, (now - o["eroded_p10_s"]) / 60.0)
            if o["reason"] == "burned":
                from wgresponse import geo
                from wgresponse.contracts import LatLon
                p = LatLon(o["lat"], o["lon"])
                zid, zd = min(((zid, geo.haversine_m(p, zl)) for zid, zl in zones),
                              key=lambda t: t[1])
                burned_rows.append(dict(
                    crew=o["crew_id"], mode=o["mode"], status=o["status"],
                    age_min=round((now - o["nominal_arrival_s"]) / 60.0, 1),
                    nearest_zone=zid, zone_m=round(zd),
                    tick_h=round((now - s._fires[next(iter(s._fires))].t_ignite_s) / 3600.0, 2)
                    if s._fires else None))
    return dict(arm=arm, map=row["map"], seed=row["seed"], viol_reported=row["viol"],
                departure_rows_max=departure_rows_max, overrun_ticks=overrun_ticks,
                kinds={f"{r}|{m}": n for (r, m), n in sorted(kinds.items())},
                worst_past_p10_min=round(worst_past_p10_min, 1),
                burned_rows=burned_rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--arms", nargs="*", default=None)
    args = ap.parse_args()
    jobs = []
    for f in sorted(glob.glob(str(OUT_DIR / "*__*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if args.arms and d["arm"] not in args.arms:
            continue
        for r in d["rows"]:
            if not r.get("error") and r.get("viol"):
                jobs.append((d["arm"], r))
    print(f"{len(jobs)} violating scenarios to characterize", flush=True)
    with ProcessPoolExecutor(max_workers=min(args.workers, max(1, len(jobs)))) as ex:
        out = list(ex.map(rerun, jobs))
    OUT_DIR.mkdir(exist_ok=True)
    json.dump(out, open(OUT_DIR / "violations.json", "w"), indent=1)
    # ---- summary per arm ------------------------------------------------
    print("\n| arm | violating scenarios | departure-audit rows (max in any tick) | crew-tick exposures by reason / mode | worst minutes past eroded P10 |")
    print("|---|---|---|---|---|")
    by_arm: dict = {}
    for o in out:
        by_arm.setdefault(o["arm"], []).append(o)
    for arm, rows in by_arm.items():
        kinds = Counter()
        for o in rows:
            for k, n in o["kinds"].items():
                kinds[k] += n
        dep = max(o["departure_rows_max"] for o in rows)
        worst = max(o["worst_past_p10_min"] for o in rows)
        print(f"| {arm} | {len(rows)} | {dep} | "
              + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) + f" | {worst} |")
    print("\n| arm | community | violating scenarios | exposures by reason / mode |")
    print("|---|---|---|---|")
    for arm, rows in by_arm.items():
        by_map: dict = {}
        for o in rows:
            by_map.setdefault(o["map"], []).append(o)
        for m, rr in by_map.items():
            kinds = Counter()
            for o in rr:
                for k, n in o["kinds"].items():
                    kinds[k] += n
            print(f"| {arm} | {m} | {len(rr)} | " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) + " |")
    burned = [dict(arm=o["arm"], map=o["map"], seed=o["seed"], **b)
              for o in out for b in o["burned_rows"]]
    if burned:
        print("\n| arm | community | seed | crew | mode | status | minutes since the front passed | nearest zone (m) | h after ignition |")
        print("|---|---|---|---|---|---|---|---|---|")
        for b in burned:
            print(f"| {b['arm']} | {b['map']} | {b['seed']} | {b['crew']} | {b['mode']} | {b['status']} "
                  f"| {b['age_min']} | {b['nearest_zone']} ({b['zone_m']}) | {b['tick_h']} |")
    print("\nwrote", OUT_DIR / "violations.json")


if __name__ == "__main__":
    main()
