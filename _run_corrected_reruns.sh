#!/usr/bin/env bash
# Task #16: re-runs on the corrected profile (new jar: sit-30 GERAD core,
# homeBase in GERAD2P) + true-rules validation and structure DP over all
# 14 rosters. Run AFTER the battery: the 600s solver budgets are
# wall-clock-sensitive and need a quiet CPU.
set -u
cd /c/work/Sandbox/Testing/TimeFold/crew-demo
PY=.venv/Scripts/python.exe
SCRATCH="C:/Users/rlgle/AppData/Local/Temp/claude/C--work-Sandbox-Testing-TimeFold/c12666e8-866a-402a-887c-f5220903bed9/scratchpad/kasirzadeh"

# Archive the old-catalog continuity diagnostics before overwriting.
mkdir -p final-report/legacy-anystart
cp final-report/gerad_phase2_direct.json \
   final-report/legacy-anystart/gerad_phase2_direct-continuity-diag.json 2>/dev/null
cp final-report/gerad_phase2_two_stage.json \
   final-report/legacy-anystart/gerad_phase2_two_stage-continuity-diag.json 2>/dev/null

echo "=== direct TF re-run (rules=GERAD2, sit-30 core) $(date +%H:%M:%S) ==="
$PY _gerad_phase2_direct.py 1 7 600 7

echo "=== stage-2 re-run (rules=GERAD2P + continuity + homeBase) $(date +%H:%M:%S) ==="
STAGE2_PORT=9095 $PY _gerad_stage2_roster.py 1 7 600 7

echo "=== true-rules validator + structure DP over all rosters $(date +%H:%M:%S) ==="
for i in 1 2 3 4 5 6 7; do
  $PY _gerad_true_rules_check.py "$SCRATCH/p2_direct_solution_inst$i.json" \
      "$SCRATCH/p2_two_stage_solution_inst$i.json"
  $PY _gerad_structure_eval.py "$SCRATCH/p2_direct_solution_inst$i.json" \
      "$SCRATCH/p2_two_stage_solution_inst$i.json"
done
echo "=== corrected re-runs done $(date +%H:%M:%S) ==="
