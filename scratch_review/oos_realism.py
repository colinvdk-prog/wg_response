"""Replay the strict-OOS pool (t0 >= 2026-07-13) under the default-OFF
realism knobs, at the live gust blend 0.5. Answers: would the grass-ROS
floor / spotting have caught the Porcupine Ridge blow-up? Writes per-pair
rows to oos_realism.json progressively."""
import dataclasses
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import (_incident_domain, _load_all_incidents,  # noqa: E402
                                    _read_json)
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

CUT = datetime.datetime(2026, 7, 13, tzinfo=datetime.timezone.utc).timestamp()
DATA = "scratch_review/hindcast_oos_data"
OUT = os.path.join(DATA, "oos_realism.json")

VARIANTS = {
    "grass_floor": dict(wind_gust_blend=0.5, grass_ros_floor_frac=0.10),
    "realism_pack": dict(wind_gust_blend=0.5, spotting_enabled=True,
                         grass_ros_floor_frac=0.10),
}

# only replay windows the default replay already showed have real growth
_d5 = json.load(open(os.path.join(DATA, "replay_b0.5.json")))
GOOD = {(p["incident"], p["t0_s"]) for p in _d5["pairs"]
        if p["n_new_burnable"] > 0 and p["t0_s"] >= CUT}

rows = []
for slug, inc in _load_all_incidents(DATA):
    if not any(k[0] == inc.irwin for k in GOOD):
        continue
    wx_path = os.path.join(DATA, "weather", f"{slug}.json")
    if not os.path.exists(wx_path):
        continue
    wd = _read_json(wx_path)
    wx = H.parse_archive_weather(wd["payload"],
                                 LatLon(wd["loc"][0], wd["loc"][1]))
    cfg0 = ResponseConfig()
    bbox, cell_m = _incident_domain(inc, cfg0)
    lfps = LfpsTerrain(cache_dir=os.path.join(DATA, "lfps"))
    cp = lfps.cache_path(bbox, cell_m)
    if not (cp and os.path.exists(cp)):
        continue
    tile = load_ascii_tile(cp)
    for a, b in zip(inc.snapshots, inc.snapshots[1:]):
        dt_h = (b.t_s - a.t_s) / 3600.0
        if (not (0.0 < dt_h <= cfg0.horizon_h - 6.0)
                or (inc.irwin, a.t_s) not in GOOD):
            continue
        for label, over in VARIANTS.items():
            cfg = dataclasses.replace(cfg0, **over)
            p = H.replay_pair(tile, wx, a, b, cfg, inc.irwin, name=inc.name)
            if p.n_new_burnable <= 0:
                continue
            row = dataclasses.asdict(p)
            row["variant"] = label
            rows.append(row)
            print(f"[{label}] {p.name} +{p.dt_h:.1f}h "
                  f"late={100 * p.late_miss / p.n_new_burnable:.1f}% "
                  f"cover={100 * p.covered_possible / p.n_new_burnable:.1f}%",
                  flush=True)
            json.dump(rows, open(OUT, "w"), indent=1)

print("\n=== pooled (strict OOS, blend 0.5) ===")
for label in VARIANTS:
    ps = [r for r in rows if r["variant"] == label]
    n = sum(r["n_new_burnable"] for r in ps)
    print(f"  {label:<13} pairs={len(ps)} coverage="
          f"{100 * sum(r['covered_possible'] for r in ps) / n:5.1f}%  "
          f"late={100 * sum(r['late_miss'] for r in ps) / n:5.1f}%  "
          f"early={100 * sum(r['early_miss'] for r in ps) / n:4.1f}%")
print("wrote", OUT)
