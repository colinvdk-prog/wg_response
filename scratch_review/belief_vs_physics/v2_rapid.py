"""Fire belief v2 (2026-09-28): the RAPID rule on the 11 real fires.

The rapid flag only reads the belief's growth windows (their ends' areas
and times), so it is evaluated here directly with the REAL
FireBeliefStore._rapid on windows built from each fire's perimeter series
-- no forecasts needed. Two series per fire:
  daily   the study's daily captures (what the hindcast belief saw)
  live    every capture the live belief would have observed (the watcher-
          order replay, >= 3 h apart: shorter, noisier windows -- the stress
          test for the persistence trigger)
At each daily forecast time t_k the flag is judged against the phase of
the NEXT step k -> k+1 (what the forecast needed: growing >= 10 %/day,
moderate 2-10, stalled < 2) and against the fire's actual recent growth
(the last 24 h window's raw growth >= 1,000 ac/day or >= 25 %/day with
>= 100 ac: the rule's own target).

Variants: legacy (area-fraction noise), calibrated noise (k = 0.5, 1.0,
2.0), persistence only, calibrated k = 1.0 + persistence (v2).
Writes scratch_review/belief_vs_physics/v2_rapid.json; prints the tables.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bvp_fetch as F  # noqa: E402
import bvp_run as R  # noqa: E402
import v2_noise as N  # noqa: E402
from wgresponse import fire_belief as FB  # noqa: E402

ACRE = FB.ACRE_M2
VARIANTS = {
    "legacy": {},
    "calibrated_k0.5": {"fire_belief_rapid_noise": "calibrated",
                        "fire_belief_rapid_perim_noise_k": 0.5},
    "calibrated_k1": {"fire_belief_rapid_noise": "calibrated",
                      "fire_belief_rapid_perim_noise_k": 1.0},
    "calibrated_k2": {"fire_belief_rapid_noise": "calibrated",
                      "fire_belief_rapid_perim_noise_k": 2.0},
    "persist_only": {"fire_belief_rapid_persist_windows": 2},
    "v2 (calibrated k1 + persist 2)": {
        "fire_belief_rapid_noise": "calibrated",
        "fire_belief_rapid_perim_noise_k": 1.0,
        "fire_belief_rapid_persist_windows": 2},
}


def windows_of(series: list) -> list:
    """Belief windows (perimeter kind) between consecutive observations."""
    out = []
    for (ta, aa), (tb, ab) in zip(series, series[1:]):
        out.append({"kind": "perimeter", "ta": ta, "tb": tb, "aa": aa * ACRE,
                    "ab": ab * ACRE, "static": False, "vobs": 0.0,
                    "sdv": 1.0, "vnom": None})
    return out


def rapid_at(store, bel, wins_all: list, t: float, cfg) -> tuple:
    bel.windows = [w for w in wins_all if w["tb"] <= t + 1e-6]
    return store._rapid(bel, t, cfg)


def main() -> int:
    out = {"variants": {}, "little_giant": {}, "per_step": []}
    fires = F.load_fires()
    base = dataclasses.replace(R.CFG_BELIEF)
    data = {}
    for key in fires:
        fire = R.get_fire(key)
        daily = [(s["t_s"], s["acres"]) for s in fire.snaps]
        rows = F.read_json(os.path.join(F.DATA_DIR, "rows", key + ".json"))["rows"]
        live = N.observed_series(rows)
        data[key] = (fire, windows_of(daily), windows_of(live))
    for vname, kw in VARIANTS.items():
        cfg = dataclasses.replace(base, **kw)
        tally = {}
        for series in ("daily", "live"):
            t = {"growing": [0, 0], "moderate": [0, 0], "stalled": [0, 0],
                 "recent_true": [0, 0], "recent_false": [0, 0],
                 "stalled_with_1000ac_day": [0, 0]}
            for key, (fire, wd, wl) in data.items():
                wins = wd if series == "daily" else wl
                store = FB.FireBeliefStore(persistent=False)
                bel = FB.IncidentBelief(fire.irwin, fire.snaps[0]["t_s"])
                for k in range(fire.n_steps()):
                    info = fire.step_info(k)
                    tk = info["t0_s"]
                    rapid, why = rapid_at(store, bel, wins, tk, cfg)
                    t[info["phase"]][0] += rapid
                    t[info["phase"]][1] += 1
                    # the rule's own target: the last daily window's raw growth
                    if k >= 1:
                        prev = fire.step_info(k - 1)
                        days = prev["dt_h"] / 24.0
                        g = prev["gis1_ac"] - prev["gis0_ac"]
                        true_rapid = days > 0 and g > 0 and (
                            g / days >= 1000.0 or (
                                g >= 100.0 and g / prev["gis0_ac"] / days >= 0.25))
                        slot = "recent_true" if true_rapid else "recent_false"
                        t[slot][0] += rapid
                        t[slot][1] += 1
                    if info["phase"] == "stalled":
                        gd = (info["gis1_ac"] - info["gis0_ac"]) / (info["dt_h"] / 24.0)
                        if gd >= 1000.0:
                            t["stalled_with_1000ac_day"][0] += rapid
                            t["stalled_with_1000ac_day"][1] += 1
                    if series == "daily":
                        out["per_step"].append({
                            "variant": vname, "fire": key, "k": k,
                            "t_s": tk, "phase": info["phase"],
                            "rapid": rapid, "why": list(why)})
                    if key == "littlegiant" and series == "daily" and \
                            datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp() <= tk <= \
                            datetime(2026, 8, 8, 12, tzinfo=timezone.utc).timestamp():
                        out["little_giant"].setdefault(vname, []).append({
                            "t": datetime.fromtimestamp(tk, tz=timezone.utc)
                            .strftime("%m-%d %H:%M"), "acres": info["gis0_ac"],
                            "rapid": rapid, "why": list(why)})
            tally[series] = {k: {"flagged": v[0], "n": v[1],
                                 "rate": (v[0] / v[1]) if v[1] else None}
                             for k, v in t.items()}
        out["variants"][vname] = tally
    with open(os.path.join(_HERE, "v2_rapid.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    for series in ("daily", "live"):
        print(f"\n{series} series: rapid flag at each daily forecast time")
        print("| variant | growing steps flagged (TP) | moderate | stalled steps "
              "flagged (FP) | of which grew >= 1,000 ac/day | recent window "
              "truly rapid: flagged | recent window not rapid: flagged |")
        print("|---|---|---|---|---|---|---|")
        for vname, tally in out["variants"].items():
            t = tally[series]

            def f(x):
                return f"{t[x]['flagged']}/{t[x]['n']} ({100 * t[x]['rate']:.0f} %)" \
                    if t[x]["n"] else "-"
            print(f"| {vname} | {f('growing')} | {f('moderate')} | {f('stalled')} | "
                  f"{f('stalled_with_1000ac_day')} | {f('recent_true')} | "
                  f"{f('recent_false')} |")
    print("\nLittle Giant, Aug 4 - Aug 8 forecasts (daily series):")
    for vname, rows in out["little_giant"].items():
        print(f"  {vname}: " + "; ".join(
            f"{r['t']} {'RAPID' if r['rapid'] else 'no'}" for r in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
