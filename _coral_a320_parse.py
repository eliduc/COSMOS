"""Parse COR@L A320 (.sp format) into cached CSC .npy arrays.

Format: 10 files a320-2.0.sp .. a320-2.9.sp. Each file:
  line 1 (header): <n_rows> <n_cols_in_file> <n_nonzeros_in_file>
  each column line: <cost> <row> <row> ... -100   (-100 = end sentinel)
Rows are 0-based (0..189). Published: LP relaxation 940.65, integer
optimum 1078 (proven).
"""
from __future__ import annotations

import array
import json
import sys
import time
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CORAL = Path("C:/work/Sandbox/Testing/TimeFold/coral-data")
FILES = [CORAL / f"a320-2.{n}.sp" for n in range(10)]
OUT = CORAL / "_parsed_a320"


def main():
    costs = array.array("d")
    indices = array.array("i")
    indptr = array.array("q", [0])
    max_row = -1
    min_row = 1 << 30
    n_rows_hdr = None
    t0 = time.monotonic()
    for f in FILES:
        with open(f) as fh:
            header = fh.readline().split()
            n_rows_hdr = int(header[0])
            for line in fh:
                p = line.split()
                if not p:
                    continue
                costs.append(float(p[0]))
                for tok in p[1:]:
                    r = int(tok)
                    if r < 0:            # -100 sentinel
                        break
                    indices.append(r)
                    if r > max_row:
                        max_row = r
                    if r < min_row:
                        min_row = r
                indptr.append(len(indices))
        print(f"  {f.name}: cols {len(costs):,} nnz {len(indices):,} "
              f"({time.monotonic()-t0:.0f}s)", flush=True)
    idx = np.frombuffer(indices, dtype=np.int32).copy()
    if min_row == 1:
        idx -= 1
    n_rows = max(n_rows_hdr, max_row - min_row + 1)
    OUT.mkdir(exist_ok=True)
    np.save(OUT / "costs.npy", np.frombuffer(costs, dtype=np.float64).copy())
    np.save(OUT / "indptr.npy", np.frombuffer(indptr, dtype=np.int64).copy())
    np.save(OUT / "indices.npy", idx)
    (OUT / "meta.json").write_text(json.dumps(
        {"n_rows": int(n_rows), "n_cols": len(costs), "nnz": len(idx)}))
    print(f"[A320 parse] cols={len(costs):,} rows={n_rows} "
          f"(idx {min_row}..{max_row}) nnz={len(idx):,} "
          f"in {time.monotonic()-t0:.0f}s -> cached {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
