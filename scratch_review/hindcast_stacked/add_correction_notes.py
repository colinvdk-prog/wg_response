"""Add the dated stacked-rings correction note to every document that cites
the pre-fix hindcast numbers (the citation inventory of 2026-09-27; record
docs/calibration/stacked_rings_fix_2026-09-27.md §6). Only a note is added:
no number in any document is edited. wgresponse/contracts.py is not touched
(it is the law; its eight citations are listed in the record for the owner).
Idempotent: a document that already carries the note is skipped.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
REC = "docs/calibration/stacked_rings_fix_2026-09-27.md"
MARK = "Correction note (2026-09-27): stacked perimeter rows"
HEAD = ("**" + MARK + ".** Hindcast numbers in this document were computed on corpora whose "
        "snapshots stacked every WFIGS Daily row sharing a capture time, and the even-odd "
        "rasterizer partly cancelled them (Madre 2025-07-02's 8,396-ac capture rasterized to 0 "
        "cells). On branch `fix/hindcast-stacked-rings` the corpora were repaired and every "
        "documented replay was re-run. Corrected values, and every number here they replace, are "
        "in [{link}]({link}) {sec}. **Affected here:** {what} The text below is left as written.")
NOTES = {
    "docs/calibration/hindcast_first_pass.md": ("§4.2",
        "the pooled figures (:45–52; 14 pairs / 60,062 cells / 68.8 % becomes 15 / 65,078 / "
        "69.4 %), the fuel table (:62–69) and the Madre rows (:35–37). The 07-02 pair scored "
        "35,071 ac of growth because its 8,396-ac T0 rasterized to nothing; corrected, 27,023 ac. "
        "The 07-03 16:16 NON-MONOTONE pair was an artifact and now scores 87.1 %."),
    "docs/calibration/hindcast_oos_2026-07-28.md": ("§4.3–4.4 and §5",
        "nearly every number. The OOS pool is 13 windows / 71,261 cells / 6 fires (was "
        "8 / 60,249 / 4). Blend-0.5 coverage is 85.8 % (was 48.6 %), and 100 % with the grass "
        "floor (was 64.8 %). The Porcupine Ridge blow-up was an artifact: its T0 cancelled to zero "
        "cells and its T1 stacked 31 versions of one record. Corrected, it is 3.7 km / 11,139 ac / "
        "0.52× with 54.7 % late at blend 0.5, 0 % late with the floor, and 7.5 % of the pool. The "
        "Crosswhite and BISCAR exclusions were artifacts. The extreme-regime signature now has no "
        "window above 50 % floored-late (was n = 1)."),
    "docs/calibration/calibration_matrix_2026-09-03.md": ("§4.1",
        "every row. August moves by at most 3.4 pp (D_gate_me071 98.8 → 98.5 %). July moves "
        "14–22 pp for every floored variant (D_gate_me071 83.1 → 99.7 %), and skill is "
        "1.01–1.11 rather than ≈ 1.0. Full corrected tables in this format: "
        "`calibration_matrix_2026-09-27_corrected.md`."),
    "docs/calibration/calibration_matrix_2026-09-11.md": ("§4.1",
        "every row, including round 2. August moves by at most 3.4 pp (D_gate_me071 98.8 → "
        "98.5 %, N_D071_phi 97.7 → 97.6 %). July moves 14–22 pp for every floored variant "
        "(D_gate_me071 83.1 → 99.7 %). Full corrected tables in this format: "
        "`calibration_matrix_2026-09-27_corrected.md`."),
    "docs/calibration/SCORECARD.md": ("§4.5",
        ":33–34. The August headline 93.8 % becomes 93.6 %, the 9.5 h Bug pair's share of late "
        "cells 91 % becomes 92 %, and effective incidents 3.24 become 3.07. The metric definitions "
        "are unaffected."),
    "docs/calibration_review_2026-07-31.md": ("§4.2–4.4 and §5",
        "§1.2–1.7 (the calibration-set and OOS tables, the Porcupine blow-up and the regime "
        "signature), :37–38, :354–355 and :907 (the corpus is now 42 windows, 41 of them scored, "
        "206,533 cells, 13 incidents), :312–314 and :957–958. The largest corrections: the OOS "
        "pool goes from 48.6 to 85.8 % at blend 0.5 and from 64.8 to 100 % with the floor; "
        "Porcupine goes from 22.5 km, 13× under, to 3.7 km at 0.52×, with 0 % late under the "
        "floor; and the extreme-regime evidence goes from n = 1 to n = 0."),
    "docs/TRI_MODEL_REVIEW_2026-08-31.md": ("§4.5",
        ":24–25, :39–50, :114–115 and :124. The August figures move little: 93.8 → 93.6 %, "
        "1.85 → 1.88×, sd(log) 0.710 → 0.698, effective incidents 3.24 → 3.07. The July ones move "
        "a lot: 64.8 → 100 % with the floor, PIT extreme mass 88.3 → 83.9 %, and the n = 1 "
        "blow-up is gone."),
    "docs/FIX_PROGRAM_2026-09-03.md": ("§4.1, §5 and §7",
        ":155–216, the matrix comparison. D071 still leads, but July skill is 1.01–1.11, not "
        "≈ 1.0, and the July blow-ups behind the reasoning that I is held back were one artifact. "
        "Also :40–41 and :58–59 (sd(log) 0.71 is re-measured at 0.698)."),
    "docs/FIX_PROGRAM_2026-09-11.md": ("§4.1",
        ":98–151. That is the round-2 table; the regime-pair counts, where 13/27 and 2/18 become "
        "13/34 and 2/26; and the per-pair July table. Porcupine drops out of that table, and only "
        "Aspen Acres 06-29 and Cottonwood 06-24 exceed 6 % late."),
    "docs/MODEL_ASSESSMENT_HANDOFF.md": ("§4.2–4.5",
        ":66, :93–105, :161–163 and :175–176. In the validation table July OOS goes from 48.6 to "
        "85.8 % at blend 0.5 and from 64.8 to 100 % with the floor; the August row goes from "
        "93.8 to 93.6 %; Porcupine goes from 13× under to 0.52×."),
    "docs/rancho_santa_fe_plan.md": ("§4.3–4.4",
        ":114–115, :172–183, :203–207, :872–878 and :975. The Porcupine 13× under-forecast is "
        "0.52×. The 22-window signature is 28 windows with no blow-up. OOS late miss by fuel goes "
        "from GS 64.2 / GR 52.2 % to 12.6 / 20.8 %."),
    "docs/SPRINT2_PLAN.md": ("§4.2",
        ":330 and :412–425, the first-pass figures: 14 pairs / 60,062 cells / 68.8 % becomes "
        "15 / 65,078 / 69.4 %, and grass-class late miss 52.6 % becomes 44.6 %."),
    "docs/vail_aspen_response_program.md": ("§4.3",
        ":262–264, :860–862 and :906. OOS late miss by fuel goes from TU 8 / TL 16 against "
        "GS 64 / GR 52 % to TU 10 / TL 17 against GS 13 / GR 21 %, and Porcupine's 13× becomes "
        "0.52×."),
    "docs/FIRE_BELIEF.md": ("§4.5",
        ":19–20, :101 and :313. The 0.71 model error is re-measured at 0.698 on the corrected "
        "corpus, so the prior is unaffected in practice."),
    "docs/LIVE_WORLD_PLAN.md": ("§4.1 and §4.4",
        ":830–831, the August guard A/B on the N variant: 27 pairs / 145,760 cells / 97.7 % / "
        "2.3 % becomes 34 / 190,223 / 97.6 % / 2.4 %, and the byte-identity conclusion stands. "
        "Also :1302, where the extreme-regime evidence goes from n = 1 to n = 0."),
    "docs/ENGINE_REVIEW_2026-07-11.md": ("§4.2", ":78–79: hindcast coverage 68.8 % becomes 69.4 %."),
    "docs/LIVE_OPS_PLAN.md": ("§4.2", ":41: 68.8 % becomes 69.4 %."),
}
README_NOTE = ("§4.3–4.5", "the knob notes at :378, :395–401 and :423 below. Porcupine's 13× "
               "under-forecast becomes 0.52×, and no blow-up is left in the regime signature. The "
               "measured sd(log) of 0.71 becomes 0.698.")


def link_for(path: str) -> str:
    return os.path.relpath(REC, os.path.dirname(path) or ".").replace("\\", "/")


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(os.path.join(ROOT, path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    for path, (sec, what) in NOTES.items():
        s = read(path)
        if MARK in s:
            print("already noted", path)
            continue
        first, rest = s.split("\n", 1)
        assert first.startswith("# "), path
        note = "> " + HEAD.format(link=link_for(path), sec=sec, what=what)
        write(path, first + "\n\n" + note + "\n" + ("" if rest.startswith("\n") else "\n") + rest)
        print("noted", path)

    path = "README.md"
    s = read(path)
    anchor = "### Sprint 2 (Council-1 remediations)\n\n"
    if MARK not in s:
        assert s.count(anchor) == 1
        note = "> " + HEAD.format(link=link_for(path), sec=README_NOTE[0], what=README_NOTE[1])
        write(path, s.replace(anchor, anchor + note + "\n\n"))
        print("noted", path)

    path = "docs/review/belief_vs_physics_2026-09-27.md"
    s = read(path)
    anchor = ("   This study replays rows in OBJECTID (publication) order with the watcher's "
              "newer-time rule\n   instead.\n")
    if "Status 2026-09-27: fixed" not in s:
        assert s.count(anchor) == 1
        link = link_for(path)
        status = ("\n   > **Status 2026-09-27: fixed** on branch `fix/hindcast-stacked-rings`. The "
                  "harness now takes the first publication per capture time and keeps a same-time "
                  "row only when it is a spatially separate piece. The three corpora were repaired "
                  f"and every documented replay was re-run. See [{link}]({link}).\n")
        write(path, s.replace(anchor, anchor + status))
        print("status", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
