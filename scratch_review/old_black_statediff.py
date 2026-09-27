"""scratch_review/old_black_statediff.py — the first tick at which two arms of
one offroad-probe session differ, and WHAT differs there (a recursive diff of
the two state() snapshots with the rulings' additive keys and map_view /
cmd_log stripped). Founder rulings 2026-09-26 (old black is a refuge; a crew
that leaves the map is out).

Usage: py -3 scratch_review/old_black_statediff.py KEY [--zone standard]
         [--ticks 40] [--arm-a "..."] [--arm-b "..."]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from scratch_review.old_black_trace import _cfg  # noqa: E402
from scratch_review.roads_offroad_probe import (EXT_DIST_M, N_1H, N_3H,  # noqa: E402
                                                N_CLIENTS, N_CREWS, PAGE_CFG,
                                                SEED, _ignite)
from wgresponse import simui  # noqa: E402


def _clean(st):
    st = json.loads(json.dumps(st, sort_keys=True, allow_nan=False))
    for c in st.get("crews", []):
        for k in ("map_view", "left_map", "left_map_at_s", "on_old_black"):
            c.pop(k, None)
    st.get("scoreboard", {}).pop("crews_left_map", None)
    st.pop("cmd_log", None)
    return st


def _diff(a, b, path="", out=None, limit=40):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append((path, a, b))
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
            else:
                _diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((f"{path}[len]", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append((path, a, b))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--zone", default="standard")
    ap.add_argument("--ticks", type=int, default=40)
    ap.add_argument("--intensity", default="high")
    ap.add_argument("--dist", type=float, default=7000.0)
    ap.add_argument("--ext-dist", type=float, default=EXT_DIST_M)
    ap.add_argument("--arm-a", default="old_black_refuge=false,left_map_out=false,"
                                       "escape_prefer_on_map=false")
    ap.add_argument("--arm-b", default="")
    a = ap.parse_args()
    sess = []
    for spec in (a.arm_a, a.arm_b):
        loc = simui.LOCATIONS[a.key]
        s = simui.SimSession(a.key, N_CLIENTS, N_CREWS, seed=SEED,
                             cfg_overrides=dict(PAGE_CFG, **_cfg(spec)),
                             fire_zone=a.zone, n_crews_1h=N_1H, n_crews_3h=N_3H)
        _ignite(s, loc, a.zone, a.intensity, a.dist, a.ext_dist)
        sess.append(s)
    for k in range(1, a.ticks + 1):
        sa, sb = (_clean(s.tick()) for s in sess)
        if sa != sb:
            print(f"first difference at tick {k}:")
            for p, x, y in _diff(sa, sb):
                print(f"  {p}: {str(x)[:160]}  |  {str(y)[:160]}")
            return
    print("identical")


if __name__ == "__main__":
    main()
