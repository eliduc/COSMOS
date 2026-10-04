"""Extended I-tuning runs.

1. I_6 on S5 (1200s)   — between I_5 (-68907) and I_8 (-59766)
2. I_7 on S5 (1200s)   — closer to I_8 sweet spot
3. I_8 on S6 (900s)    — was I_5 baseline (-102605); see if 8 intervals scales
4. I_8 on S1 (900s)    — universal-winner check
5. I_8 on S2 (900s)
6. I_8 on S3 (900s)
7. I_8 on S4 (900s)

Total wall time ~115 min.
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

# (variant, dataset_idx, timeout_s)
RUNS = [
    ("I_6", 4, 1200),   # S5
    ("I_7", 4, 1200),   # S5
    ("I_8", 5, 900),    # S6
    ("I_8", 0, 900),    # S1
    ("I_8", 1, 900),    # S2
    ("I_8", 2, 900),    # S3
    ("I_8", 3, 900),    # S4
]


def ts():
    return datetime.now().strftime("%H:%M:%S")


def main():
    py = sys.executable
    print(f"[{ts()}] === I-extended runner starting ===", flush=True)
    print(f"  runs: {RUNS}", flush=True)
    print(f"  estimated wall-time: {sum(t for _, _, t in RUNS) / 60:.0f} min "
          f"(plus ~5s startup per run)", flush=True)

    overall_t0 = time.monotonic()

    for v, ds_idx, timeout in RUNS:
        t0 = time.monotonic()
        print(f"\n========= [{ts()}] {v} on dataset_idx={ds_idx} ({timeout}s) =========",
              flush=True)
        proc = subprocess.run(
            [py, str(HERE / "_standalone_compr.py"), v, str(ds_idx), str(timeout)],
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout * 2 + 300,
        )
        elapsed = time.monotonic() - t0
        # Echo last lines
        out_lines = proc.stdout.splitlines()
        for line in out_lines[-5:]:
            print(line, flush=True)
        print(f"  [{ts()}] {v}/idx={ds_idx} DONE rc={proc.returncode} "
              f"elapsed={elapsed:.1f}s", flush=True)

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === I-extended runner done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
