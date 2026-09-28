"""Fire belief v2 (2026-09-28): figures (python 3.13 + matplotlib:
`python scratch_review/belief_vs_physics/v2_figs.py`).

  docs/review/belief_vs_physics/v2_pooled.png   per phase, harness and wide
      maps: physics vs the belief as built vs v2 (all P10 guarded) -- P50
      area error, P50 catch, late-P50 steps on growing ground (>= 2 %/day),
      false-alarm area
  docs/review/belief_vs_physics/v2_willow.png   Willow: the next-capture P50
      forecast area of physics / as built / v2 against the observed area,
      and how many hours of each next day the belief was allowed to act
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
OUT = os.path.join(_REPO, "docs", "review", "belief_vs_physics")

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
PHYS = "#2a78d6"      # slot 1 blue
BEL = "#eb6834"       # slot 2 orange  (as built)
V2C = "#1baf7a"       # slot 3 aqua    (v2; below 3:1, always labeled)

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 9, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlecolor": INK,
    "axes.titlesize": 10, "axes.titleweight": "bold",
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False})

PHASES = ("growing", "moderate", "stalled")


def load(name):
    return json.load(open(os.path.join(_HERE, name)))


def pooled(out_path: str) -> None:
    finals = [("harness maps", load("v2_final.json")),
              ("wide maps", load("v2_final_wide.json"))]
    fig, axes = plt.subplots(2, 4, figsize=(13, 6.6))
    arms = (("physics", "as_built", "phys", PHYS, "o"),
            ("belief as built", "as_built", "bel", BEL, "D"),
            ("belief v2", None, "bel", V2C, "s"))
    for row, (label, final) in enumerate(finals):
        v2 = final["_v2_name"]
        x = list(range(len(PHASES)))
        for j, (alabel, cname, arm, col, mk) in enumerate(arms):
            cn = cname or v2
            summ = final[cn]["summary"]
            off = (j - 1) * 0.2
            vals = {
                "area": [math.exp(summ[p][arm]["area_err_median"]) for p in PHASES],
                "catch": [100 * summ[p][arm]["p50_catch_cells"] for p in PHASES],
                "late": [summ[p][arm]["late_steps_grow2"] for p in PHASES],
                "fa": [100 * summ[p][arm]["fa_rel_median"] for p in PHASES]}
            for k, key in enumerate(("area", "catch", "late", "fa")):
                ax = axes[row][k]
                ax.plot([i + off for i in x], vals[key], mk, color=col,
                        ms=8 if mk != "D" else 7,
                        label=alabel if (row == 0 and k == 0) else None)
        n = {p: final["as_built"]["summary"][p]["phys"]["n"] for p in PHASES}
        labels = [f"{p}\n{n[p]} steps" for p in PHASES]
        titles = ("P50 area error (median factor)", "P50 catch (% of new ground)",
                  "late P50 on >= 2 %/day growth (steps)",
                  "false alarm (median % of fire)")
        for k in range(4):
            ax = axes[row][k]
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=7)
            ax.set_xlim(-0.6, len(x) - 0.4)
            ax.set_title(f"{titles[k]}\n{label}", loc="left", fontsize=8.5)
        axes[row][0].set_yscale("log")
        axes[row][0].set_ylim(1, 12)
        axes[row][0].set_yticks([1, 2, 3, 5, 10])
        axes[row][0].set_yticklabels(["1x", "2x", "3x", "5x", "10x"])
        axes[row][0].minorticks_off()
        axes[row][1].set_ylim(40, 108)
        axes[row][1].axhline(50, color=MUTED, lw=1, ls="--")
        axes[row][2].set_ylim(-0.5, 8)
        axes[row][3].set_ylim(0, 620)
    h, lab = axes[0][0].get_legend_handles_labels()
    fig.legend(h, lab, loc="upper center", ncol=3, fontsize=8.5,
               bbox_to_anchor=(0.5, 1.0))
    fig.suptitle("Fire belief v2 on 11 real fires (all arms P10-guarded): "
                 "per observed phase (numbers: v2_tables.md)", x=0.01,
                 ha="left", y=0.955,
                 fontsize=10, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def willow(out_path: str) -> None:
    final = load("v2_final.json")
    res = load("results_willow.json")
    v2 = final["_v2_name"]
    snaps = res["snapshots"]

    def steps(cn):
        return sorted((s for s in final[cn]["steps"] if s["fire"] == "willow"),
                      key=lambda s: s["k"])
    ab, vv = steps("as_built"), steps(v2)
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True,
                                 gridspec_kw={"height_ratios": [2.1, 1]})
    dt = lambda t: datetime.fromtimestamp(t, tz=timezone.utc)  # noqa: E731
    ax.plot([dt(s["t_s"]) for s in snaps], [s["acres"] for s in snaps],
            color=INK, lw=2, marker="o", ms=4, label="observed perimeter",
            zorder=5)
    t1 = {s["k"]: s["t1_s"] for s in res["steps"]}
    ax.plot([dt(t1[s["k"]]) for s in ab], [s["phys"]["area_p50_ac"] for s in ab],
            color=PHYS, lw=1.1, marker="o", ms=5, label="physics P50")
    ax.plot([dt(t1[s["k"]]) for s in ab], [s["bel"]["area_p50_ac"] for s in ab],
            color=BEL, lw=1.1, marker="D", ms=4.5, label="belief as built P50")
    ax.plot([dt(t1[s["k"]]) for s in vv], [s["bel"]["area_p50_ac"] for s in vv],
            color=V2C, lw=1.6, marker="s", ms=5, label="belief v2 P50")
    ax.set_yscale("log")
    ax.set_ylabel("burned area at the next capture, acres (log)")
    ax.set_title("Willow (CO): the next-capture P50 forecast, physics vs the "
                 "belief as built vs v2 (harness map)", loc="left")
    ax.legend(loc="lower right", fontsize=8)
    w = 0.18

    def slow_share(s):
        """share of the window the belief may SLOW: 0 when it is not
        applied or the fire is rapid (then it may only speed up)."""
        if not s["applied"] or s["rapid"]:
            return 0.0
        return min(s["h_eff"], s["dt_h"]) / s["dt_h"]
    for s_ab, s_v2 in zip(ab, vv):
        x0 = mdates.date2num(dt(s_ab["t0_s"]))
        for off, s, col in ((-w, s_ab, BEL), (0.0, s_v2, V2C)):
            bx.bar(x0 + off, 100 * slow_share(s), width=2 * w * 0.9,
                   color=col, align="edge")
            if s["rapid"]:
                bx.text(x0 + off + w * 0.9, 3, "R", color=INK2, fontsize=7,
                        ha="center", va="bottom")
    bx.set_ylim(0, 105)
    bx.set_ylabel("% of the next day\nthe belief may slow")
    bx.set_title("how much of each forecast the belief was allowed to slow "
                 "(orange = as built, cut at the afternoon wind turn; aqua = "
                 "v2; R = rapid: physics or faster)", loc="left", fontsize=9,
                 fontweight="normal")
    bx.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    pooled(os.path.join(OUT, "v2_pooled.png"))
    willow(os.path.join(OUT, "v2_willow.png"))
    print("wrote v2_pooled.png, v2_willow.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
