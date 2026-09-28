"""How long does the belief get to act? For every scored step whose belief
state was 'learned', the first NEW-weather-pattern hour of the forecast
(h_new; the prior applies from it to the end of the horizon) against the
step length, and which clause of the new-pattern rule fired."""
import json
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def reason_class(r: str) -> str:
    if not r:
        return "none within the horizon"
    if r.startswith("severity"):
        return "severity >= 1.5x evidence P90"
    if r.startswith("wind turns"):
        return "wind turns > 60 deg at >= 4 m/s"
    if "no steady direction" in r:
        return "evidence winds had no steady direction (any >= 4 m/s hour)"
    if r.startswith("spreading weather"):
        return "spreading weather where evidence could not carry fire"
    return r[:60]


def main(tag: str = "results") -> dict:
    fires = json.load(open(os.path.join(_HERE, "fires.json")))
    cover, reasons, hn = [], {}, []
    for key in fires:
        p = os.path.join(_HERE, f"{tag}_{key}.json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        for s in d["steps"]:
            b = s["belief_at_k"]
            if b["state"] != "learned" or "phys" not in s:
                continue
            h = b["h_new"]
            dt = s["dt_h"]
            h_eff = dt if h is None else min(h, dt)
            cover.append(h_eff / dt)
            hn.append(97 if h is None else h)
            c = reason_class(b["novel_reason"])
            reasons[c] = reasons.get(c, 0) + 1
    out = {"n_learned_steps": len(cover),
           "same_pattern_share_of_window_median": statistics.median(cover),
           "same_pattern_share_of_window_mean": statistics.mean(cover),
           "share_full_window": sum(1 for c in cover if c >= 0.999) / len(cover),
           "share_zero": sum(1 for c in cover if c <= 1e-9) / len(cover),
           "h_new_median": statistics.median(hn),
           "reasons": reasons}
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "results")
    tag = sys.argv[1] if len(sys.argv) > 1 else "results"
    json.dump(res, open(os.path.join(_HERE, f"novelty_stats_{tag}.json"), "w"),
              indent=1, sort_keys=True)
