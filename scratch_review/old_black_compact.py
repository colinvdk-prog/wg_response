"""scratch_review/old_black_compact.py — keep the roads-offroad-probe arms of the
founder-rulings validation (2026-09-26; docs/SIMULATOR_DESIGN.md "Old black is
a refuge; crews that leave the map are out") in a compact, committable form:
per session the summary row, the per-tick state hashes (`hashes`,
`engine_hashes` — what scratch_review/old_black_hashcmp.py compares), the
rulings' measurements (`old_black`) and the crews that left the map; the bulky
per-leg / per-move records are dropped.

Usage: py -3 scratch_review/old_black_compact.py SRC.json DEST.json
"""
from __future__ import annotations

import json
import sys

KEEP = ("key", "zone", "zone_req", "ignition", "roads_on", "graph_nodes",
        "relocations", "violations", "served", "overrun_rows", "overrun_ticks",
        "overrun_kinds", "departure_viol_max", "old_black", "left_map",
        "hashes", "engine_hashes", "wall_s")


def main(src, dest):
    with open(src, encoding="utf-8") as f:
        d = json.load(f)
    out = {"tag": d.get("tag"), "params": d.get("params"),
           "summary": d.get("summary"),
           "runs": [{k: r.get(k) for k in KEEP} for r in d["runs"]]}
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, separators=(",", ":"))
    print("wrote", dest)


if __name__ == "__main__":
    main(*sys.argv[1:3])
