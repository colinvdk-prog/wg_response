"""Crown-fire study: sensitivity of the one-click timber demos to the model's
choices (2026-10-12; NOT product code; docs/review/crown_fire_model_
2026-10-12.md section 3.6). Nominal fire only, the `crown` variant (grass-
only floor + spot_fire_size + crown_fire) under the city weather, each row
changing ONE thing:

  default        as shipped (FMC 100 %, EFFM +3 under >= 50 % cover, CBD
                 >= 0.02 is crown fuel)
  fmc85 / fmc120 the foliar moisture at the FBP spring dip / late summer
  seasonal       cfg.crown_foliar_moisture_seasonal (the scenario's date)
  no_shade       EFFM without the canopy-shading correction
  hardwood       LANDFIRE's hardwood code (CBD 0.01) allowed to crown
  any_angle      cfg.spread_any_angle on
  spotting_off   no ember spotting (the continuous front alone)
  delay10/20/30  spot_ignition_delay_min 10 / 20 / 30 (the shipped 5): the
                 time a spot fire landing in forest takes to spread

Usage: py -3 scratch_review/crown_fire_sensitivity.py [scenarios [rows]]
                                                     [--out PATH]
       py -3 scratch_review/crown_fire_sensitivity.py --merge PATH...
Output: scratch_review/crown_fire_sensitivity.json (or --out PATH; --merge
combines partial outputs into it, in scenario and ROWS order)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import crown_fire_scenarios as C  # noqa: E402
import ember_spotting_probe as E  # noqa: E402
from wgresponse import rothermel  # noqa: E402

OUT = os.path.join(HERE, "crown_fire_sensitivity.json")
#: Aspen's ignition (TL1 litter without canopy) never leaves its cell in 6 h
#: under any row, so Basalt (an ignition under aspen that reaches conifer)
#: stands in for it.
DEFAULT = ("park_city_windstorm", "vail_westerly", "glenwood_springs_westerly",
           "basalt_carbondale_southwesterly")
ROWS = {
    "default": ({}, {}),
    "fmc85": ({"crown_foliar_moisture_pct": 85.0}, {}),
    "fmc120": ({"crown_foliar_moisture_pct": 120.0}, {}),
    "seasonal": ({"crown_foliar_moisture_seasonal": True}, {}),
    "no_shade": ({}, {"CROWN_EFFM_SHADE_PCT": 0.0}),
    "hardwood": ({}, {"CROWN_MIN_CBD_KGM3": 0.0}),
    "any_angle": ({"spread_any_angle": True}, {}),
    "spotting_off": ({"spotting_enabled": False}, {}),
    "delay10": ({"spot_ignition_delay_min": 10.0}, {}),
    "delay20": ({"spot_ignition_delay_min": 20.0}, {}),
    "delay30": ({"spot_ignition_delay_min": 30.0}, {}),
}


def run(scen, rows=None):
    out = []
    for name, (cfg_kw, patch) in ROWS.items():
        if rows and name not in rows:
            continue
        keep = {k: getattr(rothermel, k) for k in patch}
        try:
            for k, v in patch.items():
                setattr(rothermel, k, v)
            cfg = dict(C.VARIANTS["crown"], **cfg_kw)
            s, fid = E.build(scen, cfg)
            data, strace, ctrace, _t = C.nominal(s, fid)
            nm = C.nominal_marks(s, fid, data)
            cr = C.crown_stats(s, fid, data, ctrace)
        finally:
            for k, v in keep.items():
                setattr(rothermel, k, v)
        row = {"scenario": scen, "row": name,
               "acres": {h: nm["marks"][h]["acres"] for h in nm["marks"]},
               "fastest_kmh": nm["max_head_gain_km_per_h"],
               "crown_6h": cr["6h"]}
        out.append(row)
        print(f"{scen:30s} {name:10s} ac {row['acres']} fastest "
              f"{row['fastest_kmh']} km/h crown6h act {cr['6h']['active']} "
              f"pas {cr['6h']['passive']}", flush=True)
    return out


def merge(paths):
    got = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in json.load(fh):
                got[(r["scenario"], r["row"])] = r
    scens = [s for s in DEFAULT if any(k[0] == s for k in got)]
    scens += sorted({k[0] for k in got} - set(scens))
    rows = [got[(s, n)] for s in scens for n in ROWS if (s, n) in got]
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"merged {len(rows)} rows into {OUT}")


def main(argv):
    if argv and argv[0] == "--merge":
        merge(argv[1:])
        return
    out = OUT
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    scens = argv[0].split(",") if argv else list(DEFAULT)
    want = set(argv[1].split(",")) if len(argv) > 1 else None
    if want and want - set(ROWS):
        raise SystemExit(f"unknown rows {sorted(want - set(ROWS))}")
    rows = []
    for s in scens:
        rows.extend(run(s, want))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
