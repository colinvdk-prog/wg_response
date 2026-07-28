"""Extreme-regime signature: for every scored hindcast window (calibration +
OOS), compute what a FORECAST-TIME detector could see — domain fuel mix
(FBFM40 class fractions over burnable cells) and window weather (max
sustained / gust / effective@0.5 wind, min RH) — joined with the window's
residual late-miss under the grass-floor config. Goal: thresholds that
separate the blow-up windows (Porcupine +12.9h, Gifford +18h, Dillon +8.2h,
Madre +6.6h) from the rest."""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import hindcast_lib as H  # noqa: E402
from tools.hindcast_harness import (_incident_domain, _load_all_incidents,  # noqa: E402
                                    _read_json)
from wgresponse.contracts import LatLon, ResponseConfig  # noqa: E402
from wgresponse.terrain import LfpsTerrain, load_ascii_tile  # noqa: E402

SETS = [("cal", "tools/hindcast_data",
         "scratch_review/hindcast_oos_data/cal_realism.json"),
        ("oos", "scratch_review/hindcast_oos_data",
         "scratch_review/hindcast_oos_data/oos_realism.json")]


def floored_lates(path):
    rows = json.load(open(path))
    out = {}
    for r in rows:
        if r.get("variant") == "grass_floor":
            out[(r["incident"], r["t0_s"])] = 100.0 * r["late_miss"] / r["n_new_burnable"]
    return out


def fuel_mix(tile):
    counts = {}
    burnable = 0
    for v in tile.fuel.data:
        code = int(v)
        if not H.is_burnable(code):
            continue
        burnable += 1
        cls = H.fuel_class(code)
        counts[cls] = counts.get(cls, 0) + 1
    return {c: n / burnable for c, n in counts.items()} if burnable else {}


def wx_window(wx, t0, t1):
    sel = [s for s in wx.samples if t0 - 3600 <= s.t_s <= t1 + 3600]
    if not sel:
        return None
    eff = [s.wind_speed_mps + 0.5 * max(0.0, s.wind_gust_mps - s.wind_speed_mps)
           for s in sel]
    return dict(max_wind=max(s.wind_speed_mps for s in sel),
                max_gust=max(s.wind_gust_mps for s in sel),
                max_eff=max(eff),
                min_rh=min(s.rh_pct for s in sel))


def fdt(s):
    return datetime.fromtimestamp(s, timezone.utc).strftime("%m-%d")


print(f"{'window':<26}{'flrLate%':>9}{'GR':>5}{'GS':>5}{'SH':>5}{'TL':>5}{'TU':>5}"
      f"{'mxWind':>7}{'mxGust':>7}{'mxEff':>7}{'minRH':>6}")
rows_out = []
for label, data_dir, realism_path in SETS:
    lates = floored_lates(realism_path)
    for slug, inc in _load_all_incidents(data_dir):
        wx_path = os.path.join(data_dir, "weather", f"{slug}.json")
        if not os.path.exists(wx_path):
            continue
        wd = _read_json(wx_path)
        wx = H.parse_archive_weather(wd["payload"],
                                     LatLon(wd["loc"][0], wd["loc"][1]))
        cfg0 = ResponseConfig()
        bbox, cell_m = _incident_domain(inc, cfg0)
        lfps = LfpsTerrain(cache_dir=os.path.join(data_dir, "lfps"))
        cp = lfps.cache_path(bbox, cell_m)
        if not (cp and os.path.exists(cp)):
            continue
        tile = load_ascii_tile(cp)
        mix = fuel_mix(tile)
        for a, b in zip(inc.snapshots, inc.snapshots[1:]):
            key = (inc.irwin, a.t_s)
            if key not in lates:
                continue
            w = wx_window(wx, a.t_s, b.t_s)
            if w is None:
                continue
            name = f"{inc.name} {fdt(a.t_s)} +{(b.t_s - a.t_s) / 3600:.1f}h"
            print(f"{name:<26}{lates[key]:>9.1f}"
                  f"{100 * mix.get('GR', 0):>5.0f}{100 * mix.get('GS', 0):>5.0f}"
                  f"{100 * mix.get('SH', 0):>5.0f}{100 * mix.get('TL', 0):>5.0f}"
                  f"{100 * mix.get('TU', 0):>5.0f}"
                  f"{w['max_wind']:>7.1f}{w['max_gust']:>7.1f}{w['max_eff']:>7.1f}"
                  f"{w['min_rh']:>6.0f}")
            rows_out.append(dict(set=label, name=inc.name, t0_s=a.t_s,
                                 floored_late=lates[key], mix=mix, **w))
json.dump(rows_out, open("scratch_review/extreme_signature.json", "w"), indent=1)
print("\nwrote scratch_review/extreme_signature.json")
