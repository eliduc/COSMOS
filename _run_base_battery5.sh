#!/usr/bin/env bash
# Base-anchored battery, FINAL: duty-network pricing, reference depths.
# No artificial leg cap (rules bound the space naturally: <= 5 landings
# per duty [rule 6], duty span <= 14h, rests >= 10h, pairing span <= 96h,
# block <= 30h) — exhaustive pricing passes => provable LP bounds on all
# 7 months. Cross-checked: duty pricer reproduces the leg-DFS proven
# bound 114372.0 on inst1 (bases, cap 14, landings off) exactly.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24

echo "=== duty-pricing battery (reference depths, landings<=5) $(date +%H:%M:%S) ==="
for i in 1 2 3 4 5 6 7; do
  echo "--- colgen inst$i (duty) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --pricer duty --max-landings 5 \
      --mp $MP --price-cap 900 --final-tl 900 --dump-selected \
      --pool-cap 1500000 \
      --out final-report/gerad_colgen_results_duty.json
done
echo "=== duty battery done $(date +%H:%M:%S) ==="
