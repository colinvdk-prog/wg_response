"""New-weather timing and triggers: the belief as built (hourly rule) vs
v2 (daily rule), over every forecast with a learned belief (harness maps,
v2_final.json). Writes v2_novelty_stats.json and prints a summary."""
from __future__ import annotations

import json
import os
import statistics

_HERE = os.path.dirname(os.path.abspath(__file__))


def classify(r: str) -> str:
    if not r:
        return "none within the 96 h horizon"
    if r.startswith("severity") or "severity" in r and "harshest" in r:
        return "harsher weather (severity)"
    if r.startswith("wind turns"):
        return "wind turns > 60 deg at >= 4 m/s (hourly)"
    if "no steady direction" in r:
        return "evidence winds had no steady direction (hourly)"
    if "is " in r and "from every evidence day" in r:
        return "a day's driving wind from a new sector (daily)"
    if "where no evidence day had one" in r:
        return "a driving wind where no evidence day had one (daily)"
    if "could not carry fire" in r:
        return "spreading weather where the evidence could not carry fire"
    return r[:50]


def main() -> int:
    d = json.load(open(os.path.join(_HERE, "v2_final.json")))
    out = {}
    for label, cname in (("as built", "as_built"), ("v2", d["_v2_name"])):
        steps = {(s["fire"], s["k"]): s for s in d[cname]["steps"]}
        hs, cov, why = [], [], {}
        for fire, rows in d[cname]["belief_rows"].items():
            for r in rows[:-1]:
                s = steps.get((fire, r["k"]))
                if s is None or r["state"] != "learned":
                    continue
                h = 97 if r["h_new"] is None else r["h_new"]
                hs.append(h)
                cov.append(min(1.0, min(h, s["dt_h"]) / s["dt_h"]))
                c = classify(r["novel_reason"])
                why[c] = why.get(c, 0) + 1
        out[label] = {
            "n_learned_forecasts": len(hs),
            "first_new_hour_median": statistics.median(hs),
            "new_within_horizon": sum(1 for h in hs if h <= 96),
            "new_within_next_capture": sum(1 for h, c in zip(hs, cov)
                                           if c < 0.999),
            "window_share_median": statistics.median(cov),
            "whole_window_share": sum(1 for c in cov if c >= 0.999) / len(cov),
            "triggers": why}
    with open(os.path.join(_HERE, "v2_novelty_stats.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
