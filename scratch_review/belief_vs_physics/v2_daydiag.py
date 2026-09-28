"""Diagnostic for the v2 daily new-weather test: at chosen forecast steps,
print the evidence days and the forecast days the test compares (peak
severity against the reference, driving wind against the evidence sectors).

    py -3 scratch_review/belief_vs_physics/v2_daydiag.py willow:11 timber:7
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_run as R  # noqa: E402
import v2_eval as V  # noqa: E402
from wgresponse import fire_belief as FB  # noqa: E402

H_S = 3600.0


def state_at(key: str, k_stop: int, kw: dict):
    """Replay the belief up to capture k_stop (before_spread done)."""
    cfg = V.cfg_of(kw)
    fire = R.get_fire(key)
    store = FB.FireBeliefStore(persistent=False)
    for k in range(k_stop + 1):
        t = float(fire.snaps[k]["t_s"])
        inc = fire.incident(k)
        bel = store.belief(fire.irwin)
        if bel is not None and k > 0:
            wx_prev = fire.wx(k - 1)
            h = math.floor(fire.snaps[k - 1]["t_s"] / H_S) * H_S + H_S
            while h < t:
                store._log_weather(bel, wx_prev, h, cfg)
                h += H_S
        store.observe([inc], t, cfg)
        bel = store.belief(fire.irwin)
        store.before_spread(inc, fire.tile, fire.wx(k), t, cfg)
        bel.pending_nominal = None
        if k < k_stop:
            R._pin_curve(bel, V._curve(key, k), t)
    return fire, store, bel, cfg, t


def main(argv) -> int:
    for spec in argv:
        key, k = spec.split(":")
        k = int(k)
        fire, store, bel, cfg, t = state_at(key, k, V.daily_kw(1.5, 90, 5))
        wxf = fire.wx(k)
        off_s = wxf.loc.lon / 15.0 * H_S
        wins = store._evidence(bel, t, cfg)
        fuel = FB._fuel_model(bel.fuel_code)
        info = fire.step_info(k)
        print(f"\n== {key} step {k} {datetime.fromtimestamp(t, tz=timezone.utc):%m-%d %H:%M}Z "
              f"-> +{info['dt_h']:.0f} h, observed {info['gis0_ac']:,.0f} -> "
              f"{info['gis1_ac']:,.0f} ac ({100 * info['growth_frac_per_day']:.1f} %/d); "
              f"local solar offset {off_s / H_S:+.1f} h; fuel {bel.fuel_code}")
        days = {}
        for row in bel.wx_log:
            wmax = max([wt for w, wt in wins if row[0] < w["tb"] and
                        row[0] + H_S > w["ta"]] or [0.0])
            if wmax > 0:
                d = int(math.floor((row[0] + off_s) / 86400.0))
                days.setdefault(d, []).append(
                    (FB.severity_mps(fuel, row[1], row[3], row[4], cfg),
                     row[1], row[2], wmax, row[0]))
        print("  evidence days (local solar):  peak sev  | driving wind  | weight")
        for d in sorted(days):
            hrs = days[d]
            pk, spd, dr, res = FB._day_summary([h[:3] for h in hrs])
            print(f"    day {datetime.fromtimestamp(d * 86400, tz=timezone.utc):%m-%d} "
                  f"({len(hrs):2d} h) peak {pk * 60:6.2f} m/min | {spd:4.1f} m/s "
                  f"from {dr:5.0f} (R {res:.2f}) | w {max(h[3] for h in hrs):.2f}")
        fc = {}
        for h in range(0, 97):
            s = wxf.at(t + h * H_S)
            ws = FB._eff_wind(s, cfg)
            d = int(math.floor((t + h * H_S + off_s) / 86400.0))
            fc.setdefault(d, []).append(
                (FB.severity_mps(fuel, ws, s.rh_pct, s.temp_c, cfg), ws,
                 s.wind_dir_deg % 360.0, h))
        print("  forecast days:")
        for d in sorted(fc):
            hrs = fc[d]
            pk, spd, dr, res = FB._day_summary([h[:3] for h in hrs])
            print(f"    day {datetime.fromtimestamp(d * 86400, tz=timezone.utc):%m-%d} "
                  f"(h {hrs[0][3]:2d}-{hrs[-1][3]:2d}) peak {pk * 60:6.2f} m/min | "
                  f"{spd:4.1f} m/s from {dr:5.0f} (R {res:.2f})")
        h_new, why = store._novelty(bel, wins, wxf, t, cfg)
        print(f"  daily(1.5, 90, 5): h_new {h_new} {why}")
        cfgh = V.cfg_of({})
        h2, why2 = store._novelty(bel, wins, wxf, t, cfgh)
        print(f"  hourly (as built): h_new {h2} {why2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
