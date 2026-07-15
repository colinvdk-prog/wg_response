"""Gust-blend calibration sweep (founder 2026-07-13 'calibrate the weather
input to increase accuracy'). Replays the cached 14-window hindcast at each
wind_gust_blend and pools the safety-relevant statistics. Selection criteria:
maximize p10-necessary coverage (target >= 90%) and cut LATE misses (the
dangerous, missed-alert polarity) while watching EARLY misses + p90-unreached
(the over-prediction cost, which for an alerting product is cheap but not
free). Offline: cached perimeters/weather/terrain only."""
import json
import os
import subprocess
import sys

BLENDS = [0.0, 0.25, 0.5, 0.75, 1.0]
DATA = "tools/hindcast_data"


def pooled(path):
    d = json.load(open(path))
    pairs = d["pairs"]
    n = sum(p["n_new_burnable"] for p in pairs)

    def w(key):
        # fields are newly-burned CELL COUNTS per pair -> pooled fraction
        return sum(p[key] for p in pairs) / n

    heads = sorted(p["head_m_p50"] / p["obs_head_m"] for p in pairs
                   if p.get("obs_head_m"))
    med_head = heads[len(heads) // 2]
    return dict(n=n,
                cover=100 * w("covered_possible"),
                p10_nec=100 * w("p10_not_after_window"),
                late=100 * w("late_miss"),
                early=100 * w("early_miss"),
                p50_in=100 * w("p50_in_window"),
                head=med_head)


rows = []
for b in BLENDS:
    subprocess.run([sys.executable, "tools/hindcast_harness.py", "replay",
                    "--offline", "--gust-blend", str(b), "--data-dir", DATA],
                   check=True, capture_output=True)
    r = pooled(os.path.join(DATA, "replay_results.json"))
    r["blend"] = b
    rows.append(r)
    print(f"blend={b:<5} coverage={r['cover']:5.1f}%  p10-nec={r['p10_nec']:5.1f}%  "
          f"late={r['late']:5.1f}%  early={r['early']:4.1f}%  "
          f"p50-in-win={r['p50_in']:5.1f}%  med-head-ratio={r['head']:.2f}x")

json.dump(rows, open("scratch_review/gust_sweep.json", "w"), indent=2)
print("wrote scratch_review/gust_sweep.json")
