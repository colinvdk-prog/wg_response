"""STEP 3 — replay the fetched August-2026 incidents at two configs and pool
with guards. Clones oos_realism.py's replay_pair(dataclasses.replace(...))
pattern and oos_pool.py's guards (t0 >= cutoff, real spatial growth,
window-inside-archived-weather span check).

Config "calibration": wind_gust_blend=0.5 (matches the 14w-calibration and
July-OOS anchor runs already on file).
Config "as-deployed": wind_gust_blend=0.5, grass_ros_floor_frac=0.10 — the
live shadow-ops watcher's exact physics (wgresponse/server.py's
'grass_realism' flag: grass_ros_floor_frac = 0.10 on / 0.0 off).

Guards applied per pair, in order:
  1. dt_h in (0, horizon_h - 6]                    (replay horizon law)
  2. t0_s >= 2026-08-01 00:00 UTC                    (never-calibrated-on gate)
  3. [t0,t1] inside the incident's cached archive weather span (+1h slack)
Pairs failing any guard are logged to `excluded` with a reason and never
replayed. Pairs that replay but yield n_new_burnable == 0 (e.g. a
NON-MONOTONE perimeter pair, or a step with no observed growth) are also
excluded from pooling/tables and logged with the engine's own note.

Writes scratch_review/aug_hindcast_replay.json progressively (rows + pooled
+ excluded), and prints every table STEP 4 needs."""
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

DATA = "scratch_review/hindcast_aug_data"
OUT = os.path.join(DATA, "aug_replay.json")
CUTOFF_S = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc).timestamp()
FLAG_RATIO = 0.3
FLAG_LATE_PCT = 60.0

CONFIGS = {
    "calibration": dict(wind_gust_blend=0.5),
    "as-deployed": dict(wind_gust_blend=0.5, grass_ros_floor_frac=0.10),
}


def wx_span(slug):
    p = os.path.join(DATA, "weather", f"{slug}.json")
    if not os.path.exists(p):
        return None
    wd = json.load(open(p))
    wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
    return wx.samples[0].t_s, wx.samples[-1].t_s


cfg0 = ResponseConfig()
rows = []
excluded = []   # (incident, t0_s|None, t1_s|None, reason)

for slug, inc in _load_all_incidents(DATA):
    wx_path = os.path.join(DATA, "weather", f"{slug}.json")
    if not os.path.exists(wx_path):
        excluded.append((inc.name, None, None, "no cached weather"))
        continue
    wd = _read_json(wx_path)
    wx = H.parse_archive_weather(wd["payload"], LatLon(wd["loc"][0], wd["loc"][1]))
    span = wx_span(slug)
    bbox, cell_m = _incident_domain(inc, cfg0)
    lfps = LfpsTerrain(cache_dir=os.path.join(DATA, "lfps"))
    cp = lfps.cache_path(bbox, cell_m)
    if not (cp and os.path.exists(cp)):
        excluded.append((inc.name, None, None, "no cached LFPS tile"))
        continue
    tile = load_ascii_tile(cp)
    print(f"[replay] {inc.name}: {len(inc.snapshots)} snapshots cached "
          f"({tile.spec.nrows}x{tile.spec.ncols} @ {cell_m:.0f} m)")
    for a, b in zip(inc.snapshots, inc.snapshots[1:]):
        dt_h = (b.t_s - a.t_s) / 3600.0
        why = None
        if not (0.0 < dt_h <= cfg0.horizon_h - 6.0):
            why = f"dt {dt_h:.1f}h outside (0, horizon-6]"
        elif a.t_s < CUTOFF_S:
            why = "t0 before 2026-08-01 (pre-cutoff)"
        elif span is None or a.t_s < span[0] or b.t_s > span[1] + 3600:
            why = "window outside archived weather"
        if why:
            excluded.append((inc.name, a.t_s, b.t_s, why))
            continue
        pair_zero_logged = False
        for label, over in CONFIGS.items():
            cfg = dataclasses.replace(cfg0, **over)
            p = H.replay_pair(tile, wx, a, b, cfg, inc.irwin, name=inc.name)
            row = dataclasses.asdict(p)
            row["variant"] = label
            rows.append(row)
            if p.n_new_burnable > 0:
                print(f"  [{label:<12}] {p.name} +{p.dt_h:5.1f}h "
                      f"cells={p.n_new_burnable:<6} "
                      f"late={100 * p.late_miss / p.n_new_burnable:5.1f}% "
                      f"cover={100 * p.covered_possible / p.n_new_burnable:5.1f}%",
                      flush=True)
            elif not pair_zero_logged:
                note = p.notes[0] if p.notes else "no scorable (burnable) growth"
                excluded.append((inc.name, a.t_s, b.t_s, note))
                pair_zero_logged = True
        json.dump({"rows": rows,
                   "excluded": [dict(incident=n, t0=a, t1=b, why=w)
                                for n, a, b, w in excluded]},
                  open(OUT, "w"), indent=1)

# ---------------------------------------------------------------------------
# pooled (cell-weighted), per config
# ---------------------------------------------------------------------------

def pooled(label):
    ps = [r for r in rows if r["variant"] == label and r["n_new_burnable"] > 0]
    n = sum(r["n_new_burnable"] for r in ps)
    if n == 0:
        return None
    w = lambda key: 100.0 * sum(r[key] for r in ps) / n
    heads = sorted(r["head_m_p50"] / r["obs_head_m"] for r in ps
                   if r.get("obs_head_m"))
    med = heads[len(heads) // 2] if heads else float("nan")
    return dict(n_pairs=len(ps), n_cells=n,
                coverage_possible=w("covered_possible"),
                late_miss=w("late_miss"), early_miss=w("early_miss"),
                p50_in_window=w("p50_in_window"), median_head_ratio=med)


print("\n=== POOLED (cell-weighted) ===")
pooled_out = {}
for label in CONFIGS:
    r = pooled(label)
    pooled_out[label] = r
    if r is None:
        print(f"  {label}: no scored pairs")
    else:
        print(f"  {label:<12} pairs={r['n_pairs']:<3} cells={r['n_cells']:<6} "
              f"coverage_possible={r['coverage_possible']:5.1f}%  "
              f"late_miss={r['late_miss']:5.1f}%  "
              f"early_miss={r['early_miss']:4.1f}%  "
              f"p50_in_window={r['p50_in_window']:5.1f}%  "
              f"median_head_ratio={r['median_head_ratio']:.2f}x")

# ---------------------------------------------------------------------------
# per-pair table (config "as-deployed", since that's the live physics)
# ---------------------------------------------------------------------------

print("\n=== PER-PAIR TABLE (config = as-deployed) ===")
print(f"  {'incident':<16} {'T0 (UTC)':<12} {'dt_h':>6} {'cells':>7} "
      f"{'obs_head_km':>11} {'obs_rate_kmh':>12} {'p50_head_km':>11} "
      f"{'ratio':>6} {'late%':>7}")
b_rows = [r for r in rows if r["variant"] == "as-deployed"
          and r["n_new_burnable"] > 0]
flagged = []
for r in sorted(b_rows, key=lambda r: r["t0_s"]):
    t0 = datetime.datetime.fromtimestamp(r["t0_s"], tz=datetime.timezone.utc)
    obs_head_km = r["obs_head_m"] / 1000.0
    obs_rate_kmh = (r["obs_head_m"] / r["dt_h"] / 1000.0) if r["dt_h"] else float("nan")
    p50_head_km = r["head_m_p50"] / 1000.0
    ratio = (r["head_m_p50"] / r["obs_head_m"]) if r["obs_head_m"] else float("nan")
    late_pct = 100.0 * r["late_miss"] / r["n_new_burnable"]
    flag = ""
    if (r["obs_head_m"] and ratio < FLAG_RATIO) or late_pct > FLAG_LATE_PCT:
        flag = "  <== FLAG (Porcupine-class)"
        flagged.append((r["name"], t0, ratio, late_pct))
    print(f"  {r['name']:<16} {t0:%m-%d %H:%M}  {r['dt_h']:6.1f} "
          f"{r['n_new_burnable']:7d} {obs_head_km:11.2f} {obs_rate_kmh:12.2f} "
          f"{p50_head_km:11.2f} {ratio:6.2f} {late_pct:6.1f}%{flag}")

# ---------------------------------------------------------------------------
# per-fuel-class late-miss (config "as-deployed", pooled)
# ---------------------------------------------------------------------------

print("\n=== BY FUEL CLASS (config = as-deployed, pooled) ===")
fuel_tot = {}
for r in b_rows:
    for cls, rec in r["by_fuel_class"].items():
        agg = fuel_tot.setdefault(cls, {"n": 0, "late": 0, "cert": 0, "poss": 0})
        for k in agg:
            agg[k] += rec.get(k, 0)
for cls in sorted(fuel_tot):
    rec = fuel_tot[cls]
    nn = max(1, rec["n"])
    print(f"  {cls:<4} n={rec['n']:<7} late={100 * rec['late'] / nn:5.1f}%  "
          f"cert={100 * rec['cert'] / nn:5.1f}%  poss={100 * rec['poss'] / nn:5.1f}%")

# ---------------------------------------------------------------------------
# excluded windows + blow-up flags
# ---------------------------------------------------------------------------

print("\n=== EXCLUDED WINDOWS/PAIRS ===")
for n, a, b, w in excluded:
    if a is not None:
        ta = datetime.datetime.fromtimestamp(a, tz=datetime.timezone.utc)
        tb = datetime.datetime.fromtimestamp(b, tz=datetime.timezone.utc)
        print(f"  {n:<16} {ta:%m-%d %H:%M} .. {tb:%m-%d %H:%M}  ({w})")
    else:
        print(f"  {n:<16} (whole incident)  ({w})")

print(f"\n=== PORCUPINE-CLASS BLOW-UP FLAGS (ratio<{FLAG_RATIO} or "
      f"late%>{FLAG_LATE_PCT:.0f}, config as-deployed) ===")
if flagged:
    for name, t0, ratio, late_pct in flagged:
        print(f"  FLAG: {name} {t0:%m-%d %H:%M}  ratio={ratio:.2f}x  "
              f"late={late_pct:.1f}%")
else:
    print("  none")

json.dump({"rows": rows,
           "excluded": [dict(incident=n, t0=a, t1=b, why=w)
                        for n, a, b, w in excluded],
           "pooled": pooled_out}, open(OUT, "w"), indent=1)
print("\nwrote", OUT)
