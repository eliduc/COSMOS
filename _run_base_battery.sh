#!/usr/bin/env bash
# Base-anchored battery (audit fix 1 + corrected connection profile):
# per instance — CG to a converged covering LP (rigorous bound), CP-SAT
# set-COVERING integerization (overlap = TDH riders, as in the GENCOL
# reference), dump with per-leg active owners for stage-2.
# Per-instance leg cap = max pairing length in that month's reference.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24

declare -A CAP=( [1]=14 [2]=16 [3]=18 [4]=18 [5]=12 [6]=14 [7]=14 )

echo "=== base-anchored colgen battery $(date +%H:%M:%S) ==="
for i in 1 2 3 4 5 6 7; do
  echo "--- colgen inst$i (cap ${CAP[$i]}) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --max-legs "${CAP[$i]}" \
      --mp $MP --price-cap 300 --final-tl 900 --dump-selected \
      --out final-report/gerad_colgen_results_base.json
done
echo "=== battery done $(date +%H:%M:%S) ==="
