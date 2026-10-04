"""Controlled benchmark — randomized order with cooldown.

Runs all 8 island-migration variants on S5/1200s with:
  * randomized execution order (fixed seed for reproducibility)
  * 120s cool-down between runs (mitigate CPU thermal throttling)
  * pure Python sleep, no JVM activity, between cells

Goal: amortize thermal/cache bias across variants so the comparison is
closer to true algorithmic performance.

Total wall-clock: 8 × (~1220s + 120s cooldown) ≈ 175 min.
"""
from __future__ import annotations

import random
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


VARIANTS = ["I_3", "I", "I_6", "I_7", "I_8", "I_10", "Ip", "Ia"]
DATASET_IDX = 4   # S5 (medium-lg)
TIMEOUT_S = 1200
COOLDOWN_S = 120
SHUFFLE_SEED = 42  # reproducible randomization


def ts():
    return datetime.now().strftime("%H:%M:%S")


def main():
    random.seed(SHUFFLE_SEED)
    order = VARIANTS.copy()
    random.shuffle(order)

    n = len(order)
    est_min = n * (TIMEOUT_S + COOLDOWN_S) / 60.0

    py = sys.executable
    print(f"[{ts()}] === Controlled S5/1200s benchmark ===", flush=True)
    print(f"  variants: {VARIANTS}", flush=True)
    print(f"  shuffled order: {order}  (seed={SHUFFLE_SEED})", flush=True)
    print(f"  cooldown: {COOLDOWN_S}s between runs", flush=True)
    print(f"  estimated total: {est_min:.0f} min ({n} runs)", flush=True)

    overall_t0 = time.monotonic()

    for i, v in enumerate(order):
        t0 = time.monotonic()
        print(f"\n========= [{ts()}] [{i+1}/{n}] {v} on S5 1200s =========",
              flush=True)
        proc = subprocess.run(
            [py, str(HERE / "_standalone_compr.py"), v, str(DATASET_IDX), str(TIMEOUT_S)],
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S * 2 + 300,
        )
        elapsed = time.monotonic() - t0
        # echo last lines (FINAL line + a few before)
        for line in proc.stdout.splitlines()[-5:]:
            print(line, flush=True)
        print(f"  [{ts()}] {v} DONE rc={proc.returncode} elapsed={elapsed:.1f}s",
              flush=True)

        # Cool-down between runs (skip last)
        if i < n - 1:
            print(f"  [{ts()}] cooling down for {COOLDOWN_S}s ...", flush=True)
            time.sleep(COOLDOWN_S)

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === Controlled benchmark done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
