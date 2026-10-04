#!/usr/bin/env bash
# Duty-pricing battery, remaining months (inst1-2 proven: 116589 / 58787,
# gap 0.00%). New vs battery5: tight per-duty step bound in the pricer and
# drain passes over one-longest-duty-per-first-leg starts (restricted
# passes never certify; the proof pass runs on the full start set).
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24

echo "=== duty battery, remaining months $(date +%H:%M:%S) ==="
for i in 4 5 6 7 3; do
  echo "--- colgen inst$i (duty) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --pricer duty --max-landings 5 \
      --mp $MP --price-cap 900 --final-tl 900 --dump-selected \
      --pool-cap 1500000 \
      --out final-report/gerad_colgen_results_duty.json
done
echo "=== duty battery remaining done $(date +%H:%M:%S) ==="
