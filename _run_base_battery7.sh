#!/usr/bin/env bash
# Duty battery, parallel night lane: inst5, 6, 7, 3 time-boxed (75 min CG
# cap each) while inst4 keeps its full 4h run in the other lane. Small
# inst3 should certify fully (like inst1/2 did); the big months get
# honest solutions with an explicitly-uncertified bound — certifying at
# this scale needs SPPRC dominance pricing (documented as future work).
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=12

for i in 3 5 6 7; do
  echo "--- colgen inst$i (duty, night lane) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --pricer duty --max-landings 5 \
      --mp $MP --price-cap 600 --final-tl 900 --dump-selected \
      --pool-cap 1500000 --total-cap 4500 \
      --out final-report/gerad_colgen_results_duty.json
done
echo "=== night lane done $(date +%H:%M:%S) ==="
