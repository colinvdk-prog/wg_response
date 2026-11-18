#!/bin/sh
# BEFORE runs (2026-11-17 crew-program fixes) on a clean snapshot of main
# e43c24e (git archive into the session scratchpad), 2 workers, one job at a
# time. Usage: sh chain_before.sh SNAPSHOT_DIR OUT_DIR
SNAP="$1"
OUT="$2"
PAGE='{"dispatch_hold_h":12.0,"dispatch_hold_per_home":true,"dispatch_proximity_rank":true}'
cd "$SNAP" || exit 1
python scratch_review/eagle_valley/run_matrix.py "$OUT/before_valley_0713" --workers 2 --cfg "$PAGE"
python scratch_review/eagle_valley/run_matrix.py "$OUT/before_valley_1313" --workers 2 --cfg "$PAGE" --start-hour 13.22
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/before_main" --set main --workers 2
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/before_more24" --set more24 --workers 2
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/before_valley24" --set valley24 --workers 2
python scratch_review/eagle_valley/run_matrix.py "$OUT/before_valley_24h" --workers 2 --cfg "$PAGE" --hours 24
echo CHAIN_BEFORE_DONE
