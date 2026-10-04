#!/usr/bin/env bash
# Base-anchored battery, part 4 — PROOF TIER: uniform 10-leg cap for all
# 7 months. At reference depths (14-18) the naive DFS pricing cannot
# certify the LP bound (the branch bound is too weak; the supply of
# mildly-negative deep columns never dries up — inst4 evidence). Cap 10
# keeps the corrected semantics (base anchoring, sit-30, covering+TDH)
# and makes every bound provable. inst1@14 / inst2@16 proven results stay
# as the deep-tier reference. Uncoverable-at-cap-10 legs, if any, are
# handled honestly by the partial-cover mechanism.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
BASES=BASE1,BASE2,BASE3
MP=24
CAP=10

echo "=== base-anchored PROOF battery (uniform cap $CAP) $(date +%H:%M:%S) ==="
for i in 4 5 6 7 3 1 2; do
  echo "--- colgen inst$i (cap $CAP) $(date +%H:%M:%S) ---"
  $PY _gerad_colgen.py "$i" --bases $BASES --max-legs $CAP \
      --mp $MP --price-cap 900 --final-tl 900 --dump-selected \
      --pool-cap 1500000 \
      --out final-report/gerad_colgen_results_base10.json
done
echo "=== proof battery done $(date +%H:%M:%S) ==="
