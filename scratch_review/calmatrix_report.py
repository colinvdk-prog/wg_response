"""Pool and tabulate the hindcast calibration matrix
(scratch_review/calmatrix_run.sh -> scratch_review/calmatrix/<corpus>_<label>.json)
on the two-sided scorecard (tools/hindcast_lib.pool_metrics; docs/calibration/
SCORECARD.md). Writes docs/calibration/calibration_matrix_2026-09-03.md.

Usage: py -3 scratch_review/calmatrix_report.py [aug|oos|both]
"""
import glob
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import hindcast_lib as H  # noqa: E402

FIELDS = set(H.PairMetrics.__dataclass_fields__)
ORDER = ["F_nofloor", "H_nofloor_me050", "H_nofloor_me071", "A_deployed",
         "B_gate", "C_gate_grass", "D_gate_me035", "D_gate_me050",
         "D_gate_me071", "E_gategrass_me050", "E_gategrass_me071",
         "I_gategrass05_me050", "I_gategrass05_me071", "G_gate_p50",
         "J_regime_I071", "L_regime_I_noband",
         "M_D071_piecewise", "N_D071_phi", "O_D071_both"]
DESC = {
    "F_nofloor": "gust blend 0.5, no ROS floor (the raw engine + blend)",
    "H_nofloor_me050": "F + model-error sd(log) 0.50 (physics + honest band, no floor)",
    "H_nofloor_me071": "F + model-error sd(log) 0.71 (physics + honest band, no floor)",
    "A_deployed": "as deployed since 2026-07-28: blend 0.5 + floor 0.10 (ungated, all classes)",
    "B_gate": "A + extinction gate",
    "C_gate_grass": "A + extinction gate + GR/GS only",
    "D_gate_me035": "B + model-error sd(log) 0.35",
    "D_gate_me050": "B + model-error sd(log) 0.50",
    "D_gate_me071": "B + model-error sd(log) 0.71 (the review's measured error)",
    "E_gategrass_me050": "C + model-error sd(log) 0.50",
    "E_gategrass_me071": "C + model-error sd(log) 0.71",
    "I_gategrass05_me050": "HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.50",
    "I_gategrass05_me071": "HALF floor 0.05, gated, GR/GS only + model-error sd(log) 0.71",
    "G_gate_p50": "B + p50 nominal bound (ladder median; p10 metrics unchanged by construction)",
    "J_regime_I071": "ROUND 2: I + regime-conditional floor 0.10 on regime pairs (fix program 2026-09-11)",
    "L_regime_I_noband": "ROUND 2: J without the model-error band",
    "M_D071_piecewise": "ROUND 2: D071 (deployed) + edge-weather piecewise integration",
    "N_D071_phi": "ROUND 2: D071 (deployed) + phi-vector slope/wind combination",
    "O_D071_both": "ROUND 2: D071 (deployed) + both physics knobs",
}


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    pairs = [H.PairMetrics(**{k: v for k, v in p.items() if k in FIELDS})
             for p in d["pairs"]]
    label = d.get("label", os.path.basename(path))
    # INTEGRITY: the stored label must be the filename's variant (2026-09-03:
    # editing the running driver script made three chains write another
    # variant's content under their own output name — bash reads scripts by
    # byte offset). A mismatch is fatal, never silently pooled.
    stem = os.path.basename(path).split("_", 1)[1].rsplit(".", 1)[0]
    if label != stem:
        raise SystemExit(f"INTEGRITY: {path} stores label {label!r} != {stem!r}")
    return label, d.get("config", {}), pairs


def row(label, m):
    pit = m.get("pit_summary") or {}
    bins = {b["label"]: b for b in m.get("window_length_bins", [])}

    def b(lbl, key):
        v = bins.get(lbl, {}).get(key)
        return "-" if v is None else (f"{v:.1%}" if key != "cells" else str(v))

    return (f"| {label} | {m['n_pairs']} / {m['n_cells']:,} / {m.get('n_incidents', '-')} "
            f"| {m['p10_adequacy_frac']:.1%} | {m['late_miss_frac']:.1%} (never reached {m.get('never_reached_frac', 0):.1%}) "
            f"| {m.get('median_area_ratio_p10', 0):.2f}x | {m.get('null_adequacy_frac', 0):.1%} "
            f"| {m.get('skill', 0):.2f}x | {pit.get('extreme_bins_frac', 0):.1%} "
            f"({pit.get('lo_bin_frac', 0):.0%} early / {pit.get('hi_bin_frac', 0):.0%} late) "
            f"| {pit.get('chi2_flatness', 0):,.0f} "
            f"| {m.get('median_head_ratio_p10', 0):.2f} / {m.get('median_head_ratio_p50', 0):.2f} / {m.get('median_head_ratio_nominal', 0):.2f} "
            f"| {b('<=6h', 'p10_adequacy')} / {b('6-12h', 'p10_adequacy')} / {b('12-24h', 'p10_adequacy')} / {b('>24h', 'p10_adequacy')} "
            f"| {m.get('equal_incident_mean_p10_adequacy', 0):.1%} "
            f"[{m.get('loo_incident_p10_adequacy_min', 0):.1%}–{m.get('loo_incident_p10_adequacy_max', 0):.1%}] |")


HEADER = ("| variant | pairs / cells / incidents | p10 adequacy | late miss "
          "| area p10 (median) | null adequacy | skill | PIT extreme mass (obs earlier than all members / later than all) | PIT chi2 "
          "| head ratio p10 / p50 / nominal (median) | p10 adequacy ≤6h / 6-12h / 12-24h / >24h "
          "| equal-incident mean [LOO min–max] |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    dest_name = sys.argv[2] if len(sys.argv) > 2 else "calibration_matrix_2026-09-03.md"
    stamp = dest_name.replace("calibration_matrix_", "").replace(".md", "")
    out = [f"# Hindcast calibration matrix — {stamp}",
           "",
           "Round 1 (2026-09-03) fix-program items 2/3/5 and round 2 (2026-09-11: the",
           "regime-conditional floor J/L and the physics knobs M/N/O on the deployed",
           "configuration; docs/FIX_PROGRAM_2026-09-11.md): which ROS-floor",
           "physics and which ensemble model-error term the live watcher should run,",
           "judged on the two-sided scorecard (docs/calibration/SCORECARD.md). Every",
           "variant carries the as-deployed gust blend 0.5. Pairing = the harness's",
           "(every consecutive WFIGS snapshot pair with 0 < dt <= horizon-6 h), so the",
           "cell counts differ from the review-era 18-window August table; the",
           "comparison is variant-vs-variant on identical pairs. Dangerous polarity:",
           "late miss UP, PIT extreme mass UP. Area/skill show what coverage costs.",
           ""]
    for corpus in ("aug", "oos"):
        if which not in ("both", corpus):
            continue
        files = sorted(glob.glob(str(ROOT / "scratch_review" / "calmatrix" / f"{corpus}_*.json")))
        if not files:
            continue
        rows = {}
        for f in files:
            label, cfg, pairs = load(f)
            rows[label] = H.pool_metrics(pairs)
        name = {"aug": "August 2026 corpus (scratch_review/hindcast_aug_data)",
                "oos": "July 2026 OOS corpus (scratch_review/hindcast_oos_data)"}[corpus]
        out += [f"## {name}", "", HEADER]
        for label in ORDER:
            if label in rows:
                out.append(row(f"{label} — {DESC.get(label, '')}", rows[label]))
        for label in rows:
            if label not in ORDER:
                out.append(row(label, rows[label]))
        out.append("")
    text = "\n".join(out) + "\n"
    dest = ROOT / "docs" / "calibration" / (sys.argv[2] if len(sys.argv) > 2
                                            else "calibration_matrix_2026-09-03.md")
    dest.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
