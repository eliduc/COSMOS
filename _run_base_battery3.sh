#!/usr/bin/env bash
# Base-anchored battery, part 3: inst4-7 + inst3 from scratch after the
# app restart froze the surviving inst4 on a dead output pipe. All fixes
# active: rotation, drain diversity + abort, focus passes, row-relax of
# hopeless legs, worker-side top-K cap, pool cap 1.5M.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24

declare -A CAP=( [3]=18 [4]=18 [5]=12 [6]=14 [7]=14 )

echo "=== base-anchored colgen battery part 3 $(date +%H:%M:%S) ==="
for i in 4 5 6 7 3; do
  echo "--- colgen inst$i (cap ${CAP[$i]}) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --max-legs "${CAP[$i]}" \
      --mp $MP --price-cap 300 --final-tl 900 --dump-selected \
      --pool-cap 1500000 \
      --out final-report/gerad_colgen_results_base.json
done
echo "=== battery part 3 done $(date +%H:%M:%S) ==="
