"""Determinism test — run Compr I twice back-to-back, compare per-island finals.

Goal: see if Timefold solver is INTERNALLY deterministic given:
  - same seed=0
  - same problem (we already verified problem builder is deterministic)
  - same SolverConfig
  - same multi-island execution pattern
  - JVM kill+restart between runs (fresh JIT, fresh GC)

If both runs produce IDENTICAL per-island scores: solver IS deterministic,
variance is purely from cross-restart JVM state (acceptable random noise).

If they DIFFER: there is residual non-determinism we need to track —
possibly time-based termination interacting with non-deterministic JVM
work amount per second.
"""
from __future__ import annotations

import shutil
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

LOG_FILE = HERE / "_standalone_compr_I_S5_1200s.log"


def ts():
    return datetime.now().strftime("%H:%M:%S")


def run_one(label):
    """Run I baseline; archive the kotlin log under a label-specific name."""
    t0 = time.monotonic()
    print(f"\n========= [{ts()}] {label}: launching =========", flush=True)
    proc = subprocess.run(
        [sys.executable, str(HERE / "_standalone_compr.py"), "I", "4", "1200"],
        cwd=str(HERE),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        timeout=1200 * 2 + 300,
    )
    elapsed = time.monotonic() - t0
    # Echo last few output lines
    for line in proc.stdout.splitlines()[-5:]:
        print(line, flush=True)

    # Archive kotlin log for later comparison
    archive = HERE / f"_det_test_{label}.log"
    if LOG_FILE.exists():
        shutil.copy(LOG_FILE, archive)
        print(f"  log archived to {archive.name}", flush=True)
    print(f"  [{ts()}] {label} DONE rc={proc.returncode} elapsed={elapsed:.1f}s",
          flush=True)


def main():
    print(f"[{ts()}] === Determinism test (Compr I × 2 on S5/1200s) ===", flush=True)
    run_one("run1")
    print(f"\n[{ts()}] cooling 60s before run2 ...", flush=True)
    time.sleep(60)
    run_one("run2")

    # Compare per-island finals
    print(f"\n========= [{ts()}] COMPARISON =========", flush=True)
    for label in ("run1", "run2"):
        archive = HERE / f"_det_test_{label}.log"
        if not archive.exists():
            print(f"  {label}: log missing — skip", flush=True)
            continue
        print(f"\n--- {label} per-island finals ---", flush=True)
        with archive.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "island=" in line and "finished score=" in line:
                    # Extract just the relevant pieces
                    idx = line.find("island=")
                    print("  " + line[idx:].rstrip(), flush=True)
        # also extract "interval N/M ended" lines
        print(f"\n--- {label} interval progress ---", flush=True)
        with archive.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if "interval " in line and "ended" in line:
                    idx = line.find("interval ")
                    print("  " + line[idx:].rstrip(), flush=True)
    print(f"\n[{ts()}] === Determinism test done ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
