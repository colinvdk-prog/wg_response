#!/usr/bin/env bash
# Hindcast calibration matrix for fix-program items 2/3/5
# (docs/FIX_PROGRAM_2026-09-03.md): which ROS-floor physics and which
# ensemble model-error term the live watcher should run, judged by the
# two-sided scorecard (tools/hindcast_lib.py, docs/calibration/SCORECARD.md).
#
# Usage:  bash scratch_review/calmatrix_run.sh [aug|oos|both] [variant-filter]
# Runs offline on the cached corpora (LFPS tiles + ERA5 weather already on
# disk); each variant writes scratch_review/calmatrix/<corpus>_<label>.json.
set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONIOENCODING=utf-8 PYTHONHASHSEED=0
mkdir -p scratch_review/calmatrix
which=${1:-both}
filter=${2:-}

declare -A CORPUS=(
  [aug]="scratch_review/hindcast_aug_data"
  [oos]="scratch_review/hindcast_oos_data"
)

# label => extra flags (every variant carries the as-deployed gust blend)
VARIANTS=(
  "A_deployed|--grass-floor 0.10"
  "F_nofloor|"
  "B_gate|--grass-floor 0.10 --floor-gate"
  "C_gate_grass|--grass-floor 0.10 --floor-gate --grass-only"
  "D_gate_me035|--grass-floor 0.10 --floor-gate --model-error 0.35"
  "D_gate_me050|--grass-floor 0.10 --floor-gate --model-error 0.5"
  "D_gate_me071|--grass-floor 0.10 --floor-gate --model-error 0.71"
  "E_gategrass_me050|--grass-floor 0.10 --floor-gate --grass-only --model-error 0.5"
  "G_gate_p50|--grass-floor 0.10 --floor-gate --p50-bound"
)

for corpus in aug oos; do
  [[ "$which" == "both" || "$which" == "$corpus" ]] || continue
  dir=${CORPUS[$corpus]}
  for v in "${VARIANTS[@]}"; do
    label=${v%%|*}
    flags=${v#*|}
    [[ -z "$filter" || "$label" == *"$filter"* ]] || continue
    out="scratch_review/calmatrix/${corpus}_${label}.json"
    if [[ -s "$out" ]]; then
      echo "skip $corpus $label (exists)"
      continue
    fi
    echo "=== $corpus $label  [$flags]  $(date +%H:%M:%S)"
    t0=$(date +%s)
    # shellcheck disable=SC2086
    py -3 tools/hindcast_harness.py replay --data-dir "$dir" --gust-blend 0.5 \
        $flags --label "$label" --out "$out" 2>&1 | grep -E "scored|skip:|Traceback|Error" | tail -5
    echo "=== $corpus $label done in $(( $(date +%s) - t0 )) s"
  done
done
