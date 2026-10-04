"""Run OR-Tools CP-SAT on S5/1200s with different num_search_workers values.

Tests:
  * 8 workers (= num CPU cores, baseline)
  * 12 workers (modest oversubscription)
  * 16 workers (2x oversubscription)

60s cooldown between runs. Fresh Python subprocess each time = clean state.
Total wall-clock ≈ 65 min.
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

WORKER_COUNTS = [8, 12, 16]
DATASET_IDX = 3   # S4 (medium, 42 flights) — fits in memory at 8/12/16 workers
TIMEOUT_S = 900   # match S4 historical baseline
COOLDOWN_S = 60


def ts():
    return datetime.now().strftime("%H:%M:%S")


def main():
    py = sys.executable
    print(f"[{ts()}] === OR-Tools worker-count sweep ===", flush=True)
    print(f"  configs: {WORKER_COUNTS} workers", flush=True)
    print(f"  dataset: idx={DATASET_IDX} (S5)", flush=True)
    print(f"  timeout: {TIMEOUT_S}s per run", flush=True)
    n = len(WORKER_COUNTS)
    est_min = n * (TIMEOUT_S + 30) / 60 + (n - 1) * COOLDOWN_S / 60
    print(f"  estimated wall-time: {est_min:.0f} min", flush=True)

    overall_t0 = time.monotonic()

    for i, w in enumerate(WORKER_COUNTS):
        t0 = time.monotonic()
        print(f"\n========= [{ts()}] [{i+1}/{n}] OR-Tools workers={w} =========",
              flush=True)
        proc = subprocess.run(
            [py, str(HERE / "_standalone_ortools.py"), str(w),
             str(DATASET_IDX), str(TIMEOUT_S)],
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S * 2 + 600,
        )
        elapsed = time.monotonic() - t0
        for line in proc.stdout.splitlines()[-5:]:
            print(line, flush=True)
        print(f"  [{ts()}] workers={w} DONE rc={proc.returncode} elapsed={elapsed:.1f}s",
              flush=True)

        if i < n - 1:
            print(f"  [{ts()}] cooling down {COOLDOWN_S}s ...", flush=True)
            time.sleep(COOLDOWN_S)

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === workers sweep done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
