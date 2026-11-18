#!/bin/sh
# AFTER runs (2026-11-17 crew-program fixes) on a snapshot of the branch
# (git archive; the simulator arms the six new knobs), 2 workers, one job at
# a time — the same sets and payloads as chain_before.sh.
# Usage: sh chain_after.sh SNAPSHOT_DIR OUT_DIR TAG
SNAP="$1"
OUT="$2"
TAG="${3:-after}"
PAGE='{"dispatch_hold_h":12.0,"dispatch_hold_per_home":true,"dispatch_proximity_rank":true}'
cd "$SNAP" || exit 1
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/${TAG}_valley24" --set valley24 --workers 2
python scratch_review/eagle_valley/run_matrix.py "$OUT/${TAG}_valley_0713" --workers 2 --cfg "$PAGE"
python scratch_review/eagle_valley/run_matrix.py "$OUT/${TAG}_valley_1313" --workers 2 --cfg "$PAGE" --start-hour 13.22
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/${TAG}_main" --set main --workers 2
python scratch_review/crew_fixes_2026-11-17/matrix.py "$OUT/${TAG}_more24" --set more24 --workers 2
python scratch_review/eagle_valley/run_matrix.py "$OUT/${TAG}_valley_24h" --workers 2 --cfg "$PAGE" --hours 24
echo CHAIN_AFTER_DONE
