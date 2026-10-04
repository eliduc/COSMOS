"""Run the 6 island-migration variant tuning experiments sequentially.

Each variant runs on S5 (medium-lg, 56 flights) at 1200s — so we can
compare directly to the baseline I (run_index=187) on the same dataset.

Variants:
  1. I_3   — fewer (3) intervals, longer per interval (400s each)
  2. I_8   — more (8) intervals, shorter per interval (150s each)
  3. I_10  — even more (10) intervals (120s each)
  4. Ip    — symmetric migration + 5% perturbation per island per interval
  5. Ia    — asymmetric: top-2 islands keep their own state, bottom-2 reset
  6. HI    — multi-phase pipeline per island (CH→LA→SA→Tabu→LA) + migration

Each takes ~20 min wall-clock. Total ~2h.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


VARIANTS = ["I_3", "I_8", "I_10", "Ip", "Ia", "HI"]
DATASET_IDX = 4   # S5
TIMEOUT_S = 1200


def ts():
    return datetime.now().strftime("%H:%M:%S")


def main():
    py = sys.executable
    print(f"[{ts()}] === I-tuning runner starting ===", flush=True)
    print(f"  variants: {VARIANTS}", flush=True)
    print(f"  dataset:  S5 (idx={DATASET_IDX})", flush=True)
    print(f"  timeout:  {TIMEOUT_S}s per variant", flush=True)

    overall_t0 = time.monotonic()

    for v in VARIANTS:
        t0 = time.monotonic()
        print(f"\n========= [{ts()}] {v} on S5 1200s =========", flush=True)
        proc = subprocess.run(
            [py, str(HERE / "_standalone_compr.py"), v, str(DATASET_IDX), str(TIMEOUT_S)],
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S * 2 + 300,
        )
        elapsed = time.monotonic() - t0
        # show only last few lines from each run
        out_lines = proc.stdout.splitlines()
        for line in out_lines[-5:]:
            print(line, flush=True)
        print(f"  [{ts()}] {v} DONE rc={proc.returncode} elapsed={elapsed:.1f}s", flush=True)

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === I-tuning runner done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
