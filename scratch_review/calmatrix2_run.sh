#!/usr/bin/env bash
# Calibration matrix, round 2 (fix program 2026-09-11): the regime-conditional
# floor (contracts extreme_regime_floor_frac) and the two physics knobs
# (edge_weather_piecewise, slope_phi_vector) judged on the two-sided scorecard.
# A NEW driver file on purpose: never edit a bash driver while instances run
# (bash reads scripts by byte offset — the 2026-09-03 corruption).
#
# Usage:  bash scratch_review/calmatrix2_run.sh [aug|oos|both] [variant-filter]
set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONIOENCODING=utf-8 PYTHONHASHSEED=0
mkdir -p scratch_review/calmatrix
ROOT=$(cygpath -m "$PWD")
which=${1:-both}
filter=${2:-}

declare -A CORPUS=(
  [aug]="scratch_review/hindcast_aug_data"
  [oos]="scratch_review/hindcast_oos_data"
)

# label => flags (every variant carries the as-deployed gust blend 0.5)
VARIANTS=(
  "J_regime_I071|--grass-floor 0.05 --floor-gate --grass-only --model-error 0.71 --extreme-regime --regime-floor 0.10"
  "L_regime_I_noband|--grass-floor 0.05 --floor-gate --grass-only --extreme-regime --regime-floor 0.10"
  "M_D071_piecewise|--grass-floor 0.10 --floor-gate --model-error 0.71 --edge-piecewise"
  "N_D071_phi|--grass-floor 0.10 --floor-gate --model-error 0.71 --phi-vector"
  "O_D071_both|--grass-floor 0.10 --floor-gate --model-error 0.71 --edge-piecewise --phi-vector"
  "P_J_both|--grass-floor 0.05 --floor-gate --grass-only --model-error 0.71 --extreme-regime --regime-floor 0.10 --edge-piecewise --phi-vector"
)

for corpus in aug oos; do
  [[ "$which" == "both" || "$which" == "$corpus" ]] || continue
  dir=${CORPUS[$corpus]}
  for v in "${VARIANTS[@]}"; do
    label=${v%%|*}
    flags=${v#*|}
    [[ -z "$filter" || "$label" == *"$filter"* ]] || continue
    out="$ROOT/scratch_review/calmatrix/${corpus}_${label}.json"
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
