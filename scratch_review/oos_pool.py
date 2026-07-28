"""Pool OUT-OF-SAMPLE hindcast pairs (windows starting after the 2026-07-13
gust-blend calibration) from replay_b*.json in hindcast_oos_data. Guards:
  - t0_s >= 2026-07-13 00:00 UTC  (post-calibration only)
  - n_new_burnable > 0            (spatially real growth window)
  - weather coverage: [t0, t1] inside the incident's non-null archive series
    (ERA5 lags ~5 days; a window past the last sample would score against
    stale weather and flatter nobody)
Prints the per-blend pooled row (same fields as gust_sweep) + a per-pair
late-miss table at blend 0.0 vs 0.5."""
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import _irwin_slug  # noqa: E402
from wgresponse.contracts import LatLon  # noqa: E402

DATA = "scratch_review/hindcast_oos_data"
CUTOFF_S = datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp()


def wx_span(slug):
    p = os.path.join(DATA, "weather", f"{slug}.json")
    if not os.path.exists(p):
        return None
    wd = json.load(open(p))
    wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
    return wx.samples[0].t_s, wx.samples[-1].t_s


def load(blend):
    d = json.load(open(os.path.join(DATA, f"replay_b{blend}.json")))
    keep, drop = [], []
    for p in d["pairs"]:
        span = wx_span(_irwin_slug(p["incident"]))
        why = None
        if p["t0_s"] < CUTOFF_S:
            why = "pre-cutoff"
        elif p["n_new_burnable"] <= 0:
            why = "no spatial growth"
        elif span is None or p["t0_s"] < span[0] or p["t1_s"] > span[1] + 3600:
            why = "window outside archived weather"
        (drop if why else keep).append((p, why))
    return keep, drop


def pooled(keep):
    n = sum(p["n_new_burnable"] for p, _ in keep)
    def w(key):
        return 100.0 * sum(p[key] for p, _ in keep) / n
    heads = sorted(p["head_m_p50"] / p["obs_head_m"] for p, _ in keep
                   if p.get("obs_head_m"))
    med = heads[len(heads) // 2] if heads else float("nan")
    return dict(n_pairs=len(keep), n=n, cover=w("covered_possible"),
                p10_nec=w("p10_not_after_window"), late=w("late_miss"),
                early=w("early_miss"), p50_in=w("p50_in_window"), head=med)


def fdt(s):
    return datetime.fromtimestamp(s, timezone.utc).strftime("%m-%d %H:%M")


rows = []
for b in ("0.0", "0.25", "0.5", "0.75", "1.0"):
    keep, drop = load(b)
    if not keep:
        print(f"blend={b}: no OOS pairs")
        continue
    r = pooled(keep)
    r["blend"] = b
    rows.append(r)
    print(f"blend={b:<5} pairs={r['n_pairs']:<3} cells={r['n']:<7} "
          f"coverage={r['cover']:5.1f}%  late={r['late']:5.1f}%  "
          f"early={r['early']:4.1f}%  p50-in-win={r['p50_in']:5.1f}%  "
          f"med-head-ratio={r['head']:.2f}x")

print("\n=== per-pair (blend 0.0 vs 0.5): late-miss % of newly-burned cells ===")
k0 = {(p["incident"], p["t0_s"]): p for p, _ in load("0.0")[0]}
k5, _ = load("0.5")
for p5, _why in sorted(k5, key=lambda t: t[0]["t0_s"]):
    p0 = k0.get((p5["incident"], p5["t0_s"]))
    nb = p5["n_new_burnable"]
    l0 = 100.0 * p0["late_miss"] / nb if p0 else float("nan")
    l5 = 100.0 * p5["late_miss"] / nb
    c5 = 100.0 * p5["covered_possible"] / nb
    print(f"  {p5['name']:<20} {fdt(p5['t0_s'])} +{p5['dt_h']:5.1f}h "
          f"cells={nb:<6} late {l0:5.1f}% -> {l5:5.1f}%   cover@0.5 {c5:5.1f}%")

_, drop = load("0.5")
if drop:
    print("\n=== excluded windows ===")
    for p, why in drop:
        print(f"  {p['name']:<20} {fdt(p['t0_s'])} +{p['dt_h']:5.1f}h  ({why})")

json.dump(rows, open(os.path.join(DATA, "oos_pooled.json"), "w"), indent=2)
print("\nwrote", os.path.join(DATA, "oos_pooled.json"))
