"""Belief-vs-physics real-fire study (2026-09-27) -- STEP 4: figures.

Reads scratch_review/belief_vs_physics/results[_wide]_<key>.json and
results_pooled[_wide].json; writes PNGs under docs/review/belief_vs_physics/.
Needs matplotlib: run with the Python that has it on this box
(`python scratch_review/belief_vs_physics/bvp_figs.py`, CPython 3.13 +
matplotlib 3.10; the engine's py -3 has no matplotlib).

Per fire: top panel = burned area (log scale): observed (poly_GISAcres at
each kept capture) and the P50 forecast area at t_{k+1} of the forecast
issued at t_k, physics vs belief-applied (P10-guarded build); a dotted line
marks the domain's total area (a forecast there is capped by the harness
domain). Bottom panel = what TRACKING shows: the learned whole-fire
multiplier on physics (posterior median, P10-P90 band) at each capture; the
dashed line at 1 = physics; markers above the band: R = rapid, x = the
applied belief was cut back to physics from hour 0 (new weather pattern).
Pooled: per phase, the four scores for physics vs the belief.

Palette: the dataviz reference categorical slots (validated: blue / orange /
aqua pass every adjacent and all-pairs check in light mode; aqua is below
3:1 on the surface, so it is always direct-labeled / in the legend).
"""
from __future__ import annotations

import json
import math
import os
import sys
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
BEL = "#eb6834"       # slot 2 orange
BELU = "#1baf7a"      # slot 3 aqua (below 3:1: always labeled)
ACRE_M2 = 4046.8564224

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 9, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlecolor": INK,
    "axes.titlesize": 10, "axes.titleweight": "bold",
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True, "legend.frameon": False,
    "axes.spines.top": False, "axes.spines.right": False})


def _dt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc)


def load(tag: str, key: str):
    p = os.path.join(_HERE, f"{tag}_{key}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def domain_area_ac(d) -> float:
    nr, nc = d["tile_shape"]
    return nr * nc * d["cell_m"] ** 2 / ACRE_M2


def fire_figure(d, out_path: str, subtitle: str) -> None:
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True,
                                 gridspec_kw={"height_ratios": [2.1, 1]})
    snaps = d["snapshots"]
    ax.plot([_dt(s["t_s"]) for s in snaps], [s["acres"] for s in snaps],
            color=INK, lw=2, marker="o", ms=4, label="observed perimeter",
            zorder=5)
    xs, yp, yb = [], [], []
    for s in d["steps"]:
        if "phys" not in s:
            continue
        xs.append(_dt(s["t1_s"]))
        yp.append(s["phys"]["area_p50_ac"])
        yb.append(s["bel_g"]["area_p50_ac"])
    ax.plot(xs, yp, color=PHYS, lw=1.2, marker="o", ms=5,
            label="physics P50 forecast for the next capture", zorder=3)
    ax.plot(xs, yb, color=BEL, lw=1.2, marker="D", ms=4.5,
            label="belief applied (P10-guarded) P50 forecast", zorder=4)
    dom = domain_area_ac(d)
    ax.axhline(dom, color=MUTED, lw=1, ls=":", zorder=1)
    ax.text(xs[0] if xs else _dt(snaps[0]["t_s"]), dom * 1.06,
            f"domain area {dom:,.0f} ac (forecast cap)", color=MUTED,
            fontsize=8, va="bottom")
    ax.set_yscale("log")
    ax.set_ylabel("burned area, acres (log)")
    ax.set_title(f"{d['name']}: burned area vs next-capture forecasts "
                 f"{subtitle}", loc="left")
    ax.legend(loc="lower right", fontsize=8)
    # --- the belief (what tracking displays) ---
    ts, m10, m50, m90, rap, cut = [], [], [], [], [], []
    for r in d["belief_rows"]:
        st = r["status"]
        if st["mult_p50"] is None:
            continue
        t = _dt(r["t_s"])
        ts.append(t)
        m10.append(st["mult_p10"])
        m50.append(st["mult_p50"])
        m90.append(st["mult_p90"])
        if st["rapid"]:
            rap.append(t)
        if st["state"] == "learned" and not r["applied"]:
            cut.append(t)
    if ts:
        bx.fill_between(ts, m10, m90, color=BEL, alpha=0.18, lw=0,
                        label="learned multiplier, P10-P90")
        bx.plot(ts, m50, color=BEL, lw=2, marker="D", ms=3.5,
                label="learned multiplier, median")
        top = 3.2
        for t in rap:
            bx.text(t, top, "R", color=INK2, fontsize=7, ha="center",
                    va="center")
        for t in cut:
            bx.text(t, top * 0.62, "x", color=INK2, fontsize=8, ha="center",
                    va="center")
    bx.axhline(1.0, color=PHYS, lw=1, ls="--")
    bx.text(_dt(snaps[0]["t_s"]), 1.08, "physics = 1x", color=PHYS,
            fontsize=8, va="bottom")
    bx.set_yscale("log")
    bx.set_ylim(0.01, 5)
    bx.set_yticks([0.01, 0.03, 0.1, 0.3, 1, 3])
    bx.set_yticklabels(["0.01x", "0.03x", "0.1x", "0.3x", "1x", "3x"])
    bx.set_ylabel("spread rate vs physics")
    bx.set_title("what tracking shows: the rate learned from the perimeters "
                 "(R = rapid, x = new weather at hour 0: physics)",
                 loc="left", fontsize=9, fontweight="normal")
    bx.legend(loc="lower left", fontsize=8, ncol=2)
    bx.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def pooled_figure(pooled: dict, out_path: str, title: str) -> None:
    phases = [p for p in ("growing", "moderate", "stalled")
              if p in pooled["by_phase"]]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.8))
    labels = [f"{p}\n{pooled['by_phase'][p]['n_steps']} steps\n"
              f"{pooled['by_phase'][p]['n_fires']} fires" for p in phases]
    x = list(range(len(phases)))
    off = 0.14

    def dots(ax, vals_p, vals_b, fmt, vals_u=None):
        ax.plot([i - off for i in x], vals_p, "o", color=PHYS, ms=8,
                label="physics")
        ax.plot([i + (0 if vals_u is None else 0) + off for i in x], vals_b,
                "D", color=BEL, ms=7, label="belief applied (guarded)")
        if vals_u is not None:
            ax.plot([i + 2.6 * off for i in x], vals_u, "s", color=BELU,
                    ms=6, label="belief, unguarded P10")
            for i in x:
                ax.text(i + 2.6 * off, vals_u[i], " " + fmt(vals_u[i]),
                        color=INK2, fontsize=7, ha="left", va="center")
        for i in x:
            ax.text(i - off, vals_p[i], " " + fmt(vals_p[i]), color=INK2,
                    fontsize=7, ha="right", va="bottom", rotation=0)
            ax.text(i + off, vals_b[i], " " + fmt(vals_b[i]), color=INK2,
                    fontsize=7, ha="left", va="top")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7.5)
        ax.set_xlim(-0.6, len(x) - 0.4)
    bp = pooled["by_phase"]
    a = axes[0]
    dots(a, [math.exp(bp[p]["phys"]["area_err_median"]) for p in phases],
         [math.exp(bp[p]["bel"]["area_err_median"]) for p in phases],
         lambda v: f"{v:.1f}x")
    a.set_yscale("log")
    a.set_ylim(1, 12)
    a.set_yticks([1, 2, 3, 5, 10])
    a.set_yticklabels(["1x", "2x", "3x", "5x", "10x"])
    a.minorticks_off()
    a.set_title("P50 area error\n(median factor, 1x = exact)", loc="left",
                fontsize=9)
    a = axes[1]
    dots(a, [100 * bp[p]["phys"]["p10_adequacy_cells"] for p in phases],
         [100 * bp[p]["bel"]["p10_adequacy_cells"] for p in phases],
         lambda v: f"{v:.1f}%",
         vals_u=[100 * bp[p]["belu"]["p10_adequacy_cells"] for p in phases])
    a.set_ylim(90, 100.8)
    a.set_title("P10 adequacy, % of newly burned\ncells inside P10 "
                "(higher = safer)", loc="left", fontsize=9)
    a = axes[2]
    a.axhline(50, color=MUTED, lw=1, ls="--")
    a.text(-0.55, 46, "a calibrated median: >= ~50 %", color=MUTED,
           fontsize=7, ha="left", va="top")
    dots(a, [100 * bp[p]["phys"]["p50_hit_cells"] for p in phases],
         [100 * bp[p]["bel"]["p50_hit_cells"] for p in phases],
         lambda v: f"{v:.0f}%")
    a.set_ylim(0, 105)
    a.set_title("P50 hit rate, % of newly burned\ncells inside P50",
                loc="left", fontsize=9)
    a = axes[3]
    dots(a, [100 * bp[p]["phys"]["fa_p50_rel_median"] for p in phases],
         [100 * bp[p]["bel"]["fa_p50_rel_median"] for p in phases],
         lambda v: f"{v:.0f}%")
    a.set_ylim(bottom=0)
    a.set_title("P50 false-alarm area\n(median, % of the fire's area)",
                loc="left", fontsize=9)
    h, lab = axes[1].get_legend_handles_labels()
    fig.legend(h, lab, loc="upper center", ncol=3, fontsize=8,
               bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(title, x=0.01, ha="left", y=0.93, fontsize=10,
                 fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def variants_figure(out_path: str) -> None:
    """The trade-off the belief's two safety rules buy, per phase: median
    P50 area error (y) against the P50 hit rate (x) for physics, the belief
    as built and the what-if rule settings (same learning). Identity is
    carried by direct labels and marker shape, not by color alone."""
    sets = [("", "physics", PHYS, "o", True),
            ("", "belief as built", BEL, "D", True),
            ("nonovel", "new-weather rule off", BEL, "v", False),
            ("norapid", "rapid rule off", BEL, "^", False),
            ("free", "both rules off", BEL, "s", False)]
    pooled = {}
    for bv, *_rest in sets:
        p = os.path.join(_HERE, f"results_pooled{'_' + bv if bv else ''}.json")
        if os.path.exists(p):
            pooled[bv] = json.load(open(p))
    if "" not in pooled:
        return
    phases = ("growing", "moderate", "stalled")
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), sharey=True)
    for ax, ph in zip(axes, phases):
        for i, (bv, label, col, mk, filled) in enumerate(sets):
            if bv not in pooled or ph not in pooled[bv]["by_phase"]:
                continue
            rec = pooled[bv]["by_phase"][ph]
            arm = rec["phys"] if label == "physics" else rec["bel"]
            xv = 100 * arm["p50_hit_cells"]
            yv = math.exp(arm["area_err_median"])
            # filled (physics, as built) drawn large underneath; the hollow
            # what-if markers smaller on top, so coincident results (a rule
            # that never fired in that phase) stay visible
            ax.plot([xv], [yv], mk, ms=12 if filled else 7, color=col,
                    mfc=col if filled else SURFACE, mew=1.6,
                    label=label if ph == "growing" else None,
                    zorder=2 if filled else 3)
        n = pooled[""]["by_phase"][ph]
        ax.set_title(f"{ph}: {n['n_steps']} steps, {n['n_fires']} fires",
                     loc="left", fontsize=9)
        ax.set_yscale("log")
        ax.set_ylim(1, 8)
        ax.set_yticks([1, 1.5, 2, 3, 5])
        ax.set_yticklabels(["1x", "1.5x", "2x", "3x", "5x"])
        ax.minorticks_off()
        ax.set_xlim(40, 102)
        ax.axvline(50, color=MUTED, lw=1, ls="--")
        ax.set_xlabel("P50 hit rate, % of newly burned cells inside P50")
    axes[0].set_ylabel("P50 area error (median factor)")
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=5, fontsize=8,
               bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("What the two safety rules cost and buy (harness domain; "
                 "P10 guarded in every belief arm; lower-left = better area, "
                 "more cells burning before their P50)", x=0.01, ha="left",
                 fontsize=10, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0.07, 1, 0.93))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main(argv) -> int:
    os.makedirs(OUT, exist_ok=True)
    fires = json.load(open(os.path.join(_HERE, "fires.json")))
    for tag, sub in (("results", "(harness domain, 4 km pad)"),
                     ("results_wide", "(wide domain, 20 km pad)")):
        for key in fires:
            if tag.endswith("wide") and key != "willow":
                continue            # wide map: Willow only (repo size)
            d = load(tag, key)
            if d is None:
                continue
            name = f"{'wide_' if tag.endswith('wide') else ''}{key}.png"
            fire_figure(d, os.path.join(OUT, name), sub)
            print("wrote", name)
    for tag, title in (("results_pooled", "Real fires, harness domain: "
                        "physics vs the belief applied, by observed phase"),
                       ("results_pooled_wide", "Real fires, wide domain "
                        "(20 km pad): physics vs the belief applied")):
        p = os.path.join(_HERE, tag + ".json")
        if os.path.exists(p):
            pooled_figure(json.load(open(p)), os.path.join(
                OUT, f"pooled_{'wide' if 'wide' in tag else 'harness'}.png"),
                title)
            print("wrote pooled", tag)
    variants_figure(os.path.join(OUT, "variants_harness.png"))
    print("wrote variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
