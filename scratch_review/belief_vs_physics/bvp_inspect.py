"""Inspection aid: a fire's capture picks (from the cached raw rows, no
network) or its fetched incident file.

    py -3 scratch_review/belief_vs_physics/bvp_inspect.py picks willow
    py -3 scratch_review/belief_vs_physics/bvp_inspect.py incident willow
"""
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _REPO)
sys.path.insert(0, _HERE)
import bvp_fetch as F  # noqa: E402
from wgresponse.contracts import LatLon  # noqa: E402
from wgresponse.fire_belief import rings_area_m2  # noqa: E402


def _fmt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d %H:%M")


def picks(key: str) -> None:
    rows = F.read_json(os.path.join(F.DATA_DIR, "rows", key + ".json"))["rows"]
    out, drops = F.pick_captures(rows)
    print(key, len(rows), "rows ->", len(out), "daily captures")
    for d in drops:
        print("   DROP", d)
    prev = None
    for t, r, nrev in out:
        g = ""
        if prev is not None:
            dt_h = (t - prev[0]) / 3600.0
            gr = (r["acres"] - prev[1]["acres"]) / prev[1]["acres"] / (dt_h / 24.0)
            g = f"dt {dt_h:5.1f} h  growth {100 * gr:7.1f} %/d"
        print(f"  {_fmt(t)}  {r['acres']:>10,.0f} ac  oid {r['oid']}  "
              f"ignored revs {nrev:2d}  pct {r.get('attr_PercentContained')}  {g}")
        prev = (t, r)


def incident(key: str) -> None:
    d = F.read_json(os.path.join(F.DATA_DIR, "incidents", key + ".json"))
    print(d["name"], d["irwin"], "raw rows", d["n_raw_rows"], "captures",
          len(d["snapshots"]))
    for s in d["snapshots"]:
        rings = [[LatLon(v[0], v[1]) for v in ring] for ring in s["rings"]]
        a = rings_area_m2(rings) / 4046.8564224
        print(f"  {_fmt(s['t_s'])}  GIS {s['acres']:>10,.0f}  rings {a:>10,.0f}  "
              f"nring {len(s['rings'])}  pct {s.get('pct_contained')}")


if __name__ == "__main__":
    {"picks": picks, "incident": incident}[sys.argv[1]](sys.argv[2])
