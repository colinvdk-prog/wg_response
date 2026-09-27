"""scratch_review/old_black_hashcmp.py — per-tick state-hash comparison of two
roads_offroad_probe.py result files (the byte-identity proofs of the
2026-09-26 founder rulings; docs/SIMULATOR_DESIGN.md "Old black is a refuge;
crews that leave the map are out (2026-09-26)").

The probe hashes every tick's state with map_view and the rulings' additive
keys (crews[i].left_map / left_map_at_s / on_old_black, scoreboard.
crews_left_map) stripped — `hashes` — and additionally with cmd_log stripped
— `engine_hashes` (a knob pinned explicitly echoes in cmd_log). Prints, per
session, how many ticks match and the first differing tick, and whether any
crew left the map in either run.

Usage: py -3 scratch_review/old_black_hashcmp.py A.json B.json [--field engine_hashes]
"""
from __future__ import annotations

import argparse
import json


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    return {(r["key"], r["zone"]): r for r in d["runs"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--field", default="engine_hashes")
    args = ap.parse_args(argv)
    A, B = load(args.a), load(args.b)
    n_same = n_sess = 0
    n_ticks = n_ticks_same = 0
    for k in sorted(set(A) | set(B)):
        if k not in A or k not in B:
            print(f"{k}: only in {'A' if k in A else 'B'}")
            continue
        ha, hb = A[k][args.field], B[k][args.field]
        same = [x == y for x, y in zip(ha, hb)]
        first = next((i for i, v in enumerate(same) if not v), None)
        la = len(A[k].get("left_map") or {})
        lb = len(B[k].get("left_map") or {})
        n_sess += 1
        n_ticks += len(same)
        n_ticks_same += sum(same)
        if first is None and len(ha) == len(hb):
            n_same += 1
        print(f"{k[0]:<22}{k[1]:<9} ticks {len(same):>3} identical "
              f"{sum(same):>3}  first diff {first}  left A/B {la}/{lb}")
    print(f"\n{n_same}/{n_sess} sessions identical at every tick "
          f"({n_ticks_same}/{n_ticks} states)")


if __name__ == "__main__":
    main()
