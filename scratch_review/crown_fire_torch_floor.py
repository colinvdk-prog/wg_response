"""Crown-fire study: the torching-reach floor and its minimum fire size
(2026-10-12; NOT product code; docs/review/crown_fire_model_2026-10-12.md
section 3.5). For one-click scenarios under the `crown` variant (grass-only
floor + spot_fire_size + crown_fire), the NOMINAL fire is re-solved with
spread._TORCH_MIN_FIRE_HA set to 0 (every crowning cell, even a lone spot
fire, relays torching embers), 2 (shipped), 10 ha and inf (no torching floor:
the SEMANTICS 19 size-scaled reach alone). Reported: acres at 1 / 3 / 6 h,
the head's fastest 1-h gain, spotting (first spot beyond 0.5 / 1 km, spot
ignitions, those thrown by one-cell fires, the spot-from-spot chain depth).

Usage: py -3 scratch_review/crown_fire_torch_floor.py [scenarios]
Output: scratch_review/crown_fire_torch_floor.json
"""
from __future__ import annotations

import bisect
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_scenarios as C  # noqa: E402
import ember_spotting_probe as E  # noqa: E402
from wgresponse import geo, spread  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_torch_floor.json")
FLOORS = (0.0, 2.0, 10.0, math.inf)
DEFAULT = ("park_city_windstorm", "vail_westerly", "aspen_southwesterly",
           "paradise_ne_wind")


def run(scen):
    rows = []
    s, fid = E.build(scen, C.VARIANTS["crown"])
    f = s._fires[fid]
    spec = s._tile.spec
    cell_ac = spec.cell_m ** 2 / 4046.8564224
    keep = spread._TORCH_MIN_FIRE_HA
    try:
        for fl in FLOORS:
            spread._TORCH_MIN_FIRE_HA = fl
            data, strace, ctrace, _s = C.nominal(s, fid)
            t0 = f.t_ignite_s
            order = sorted((v, i) for i, v in enumerate(data) if v < math.inf)
            times = [v for v, _ in order]
            heads = []
            m = 0.0
            for _v, i in order:
                m = max(m, geo.haversine_m(f.origin, geo.rc_to_latlon(
                    spec, *divmod(i, spec.ncols))) / 1000.0)
                heads.append(m)

            def at(t_h):
                j = bisect.bisect_right(times, t0 + t_h * 3600.0)
                return round(j * cell_ac), (heads[j - 1] if j else 0.0)
            hq = [at(q * 0.25)[1] for q in range(25)]
            row = {"scenario": scen, "torch_min_fire_ha": fl if fl < math.inf
                   else None,
                   "acres": {f"{h:g}h": at(h)[0] for h in (1, 3, 6)},
                   "head_km_6h": round(at(6)[1], 2),
                   "fastest_1h_head_gain_kmh": round(max(
                       hq[i + 4] - hq[i] for i in range(21)), 2),
                   "spotting": C.spot_stats(s, fid, data, strace)}
            rows.append(row)
            sp = row["spotting"]
            print(f"{scen:28s} floor {fl!s:>4} ha | ac {row['acres']} | "
                  f"fastest {row['fastest_1h_head_gain_kmh']} km/h | spots "
                  f"{sp['spot_ignitions_6h']} one-cell {sp['thrown_by_one_cell_fire']}"
                  f" chain {sp['max_chain_depth']} | >0.5 km "
                  f"{sp['first_spot_over_0.5km']} >1 km {sp['first_spot_over_1km']}",
                  flush=True)
    finally:
        spread._TORCH_MIN_FIRE_HA = keep
    return rows


def main(argv):
    scens = argv[0].split(",") if argv else list(DEFAULT)
    out = []
    for scen in scens:
        out.extend(run(scen))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
