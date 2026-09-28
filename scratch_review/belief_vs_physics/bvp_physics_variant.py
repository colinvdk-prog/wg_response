"""Side experiment (context for WHAT the belief is correcting): the same
physics replay with ONE config difference from the deployed posture,
scored exactly like bvp_run's physics arm.

  grassonly  ros_floor_grass_only=True: the deployed fuel-class ROS floor
             (grass_ros_floor_frac 0.10 x U10) stays on grass (GR/GS) but no
             longer lifts timber (TU/TL). contracts.py documents that the
             floor "also lifts TU/TL timber to 19-45x Rothermel"; these
             fires burn mostly in TU/TL (fuel_mix.json).

Not a recommendation to flip the knob -- a measurement of how much of the
physics error the belief learns on these fires is that one floor.
Writes scratch_review/belief_vs_physics/results_phys_<variant>.json.

    py -3 scratch_review/belief_vs_physics/bvp_physics_variant.py grassonly
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_run as R  # noqa: E402
from wgresponse import spread  # noqa: E402

VARIANTS = {"grassonly": {"ros_floor_grass_only": True}}


def task(key: str, k: int, variant: str) -> dict:
    t0c = time.time()
    cfg = dataclasses.replace(R.CFG_PROD, **VARIANTS[variant])
    fire = R.get_fire(key)
    info = fire.step_info(k)
    t0, t1 = info["t0_s"], info["t1_s"]
    ens = spread.ensemble_arrival(fire.tile, [], fire.wx(k), t0, cfg,
                                  incident_id=fire.irwin,
                                  perimeter=fire.seed_rings(k),
                                  perimeter_at_s=t0)
    t0m = R.seed_mask(ens, t0)
    t1m, burn = R.obs_masks(fire, k + 1)
    m = R.score(R.footprints(ens, t1), t0m, t1m, burn, fire.tile.spec)
    return {"key": key, "k": k, "s": time.time() - t0c, "m": m,
            "phase": info["phase"]}


def main(argv) -> int:
    variant = argv[0] if argv else "grassonly"
    fires = json.load(open(os.path.join(_HERE, "fires.json")))
    tasks = []
    for key in fires:
        fire = R.get_fire(key)
        for k in range(fire.n_steps()):
            if fire.step_info(k)["scoreable"]:
                tasks.append((key, k, variant))
    out: dict = {}
    t_start = time.time()
    with ProcessPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(task, *t) for t in tasks]
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            out.setdefault(r["key"], {})[str(r["k"])] = {
                "phase": r["phase"], "phys": r["m"]}
            if i % 20 == 0:
                print(f"[{variant}] {i}/{len(tasks)} "
                      f"({time.time() - t_start:.0f} s)", flush=True)
    path = os.path.join(_HERE, f"results_phys_{variant}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"schema": "bvp-phys-variant/1", "variant": variant,
                   "config_change": VARIANTS[variant], "fires": out}, f,
                  indent=1, sort_keys=True, default=R._jdefault)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
