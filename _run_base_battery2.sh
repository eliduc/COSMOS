#!/usr/bin/env bash
# Base-anchored battery, part 2: inst4-7 + inst3 re-run. Fixes vs part 1:
# start rotation between capped passes (tail starvation), drain-phase
# per-start emission caps (diversity over concentration), k=60000 default,
# pool cap raised (drain columns are bulky). inst1-2 already proven (0.00%)
# and saved in gerad_colgen_results_base.json.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24

declare -A CAP=( [3]=18 [4]=18 [5]=12 [6]=14 [7]=14 )

echo "=== base-anchored colgen battery part 2 $(date +%H:%M:%S) ==="
for i in 4 5 6 7 3; do
  echo "--- colgen inst$i (cap ${CAP[$i]}) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --max-legs "${CAP[$i]}" \
      --mp $MP --price-cap 300 --final-tl 900 --dump-selected \
      --pool-cap 1500000 \
      --out final-report/gerad_colgen_results_base.json
done
echo "=== battery part 2 done $(date +%H:%M:%S) ==="
