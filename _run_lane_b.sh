#!/usr/bin/env bash
# Morning recovery lane B: inst4, 7 (time-boxed duty CG, 14 workers).
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
for i in 4 7; do
  echo "--- colgen inst$i (lane B) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases BASE1,BASE2,BASE3 --pricer duty \
      --max-landings 5 --mp 14 --price-cap 600 --final-tl 900 \
      --dump-selected --pool-cap 1500000 --total-cap 4500 \
      --out final-report/gerad_colgen_results_duty.json
done
echo "=== lane B done $(date +%H:%M:%S) ==="
