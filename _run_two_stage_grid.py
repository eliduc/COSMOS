"""Run the two-stage pipeline on remaining datasets sequentially.

S2 and S4 already done. Run S1, S3, S5, S6 with 900s Timefold each.
~1 hour total wall-clock.
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

DATASETS = [0, 2, 4, 5]   # S1, S3, S5, S6
TIMEOUT_S = 900
COOLDOWN_S = 30


def ts():
    return datetime.now().strftime("%H:%M:%S")


def main():
    print(f"[{ts()}] === Two-stage grid on remaining datasets ===", flush=True)
    print(f"  datasets: {DATASETS} (S1, S3, S5, S6)", flush=True)
    print(f"  timeout:  {TIMEOUT_S}s per run", flush=True)

    py = sys.executable
    overall_t0 = time.monotonic()

    for i, ds_idx in enumerate(DATASETS):
        t0 = time.monotonic()
        print(f"\n========= [{ts()}] [{i+1}/{len(DATASETS)}] dataset_idx={ds_idx} =========",
              flush=True)
        proc = subprocess.run(
            [py, str(HERE / "_two_stage_runner.py"),
             str(ds_idx), str(TIMEOUT_S), "1"],
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S * 2 + 600,
        )
        elapsed = time.monotonic() - t0
        for line in proc.stdout.splitlines()[-10:]:
            print(line, flush=True)
        print(f"  [{ts()}] dataset_idx={ds_idx} DONE rc={proc.returncode} "
              f"elapsed={elapsed:.1f}s", flush=True)
        if i < len(DATASETS) - 1:
            print(f"  [{ts()}] cooling {COOLDOWN_S}s ...", flush=True)
            time.sleep(COOLDOWN_S)

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === Two-stage grid done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
