"""Timefold on OR-Library SPP — the '2 solvers vs 1' thesis test.

Runs Timefold local search on the same set-partitioning instances CP-SAT
solved to proven optimality (_run_orlib_spp.py), same-or-larger budgets.
Reports feasibility (hard=0 → exact cover) and cost gap vs proven optimum.

Usage:
    python _run_spp_timefold.py <file-or-dir> [budget_s] [seed] [csv_out]
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from _run_orlib_spp import parse_spp, upsert_csv_rows  # noqa: E402

PORT = 9099


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else \
        __import__("_data_paths").results_dir() / "orlib_spp_timefold.csv"
    if target is None:
        print(__doc__)
        return 2
    files = sorted(target.glob("spp*")) if target.is_dir() else [target]

    _kill_port(PORT)
    time.sleep(1)
    log_f = (HERE / "_spp_jvm.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [_java_exe(), "-Xms1g", "-Xmx8g", "-XX:+UseG1GC",
         f"-Dquarkus.http.port={PORT}",
         "-Dquarkus.http.limits.max-body-size=256M",
         "-jar", str(KOTLIN_SOLVER_JAR)],
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent))
    rows_out = []
    try:
        if not _wait_http_ready(f"http://127.0.0.1:{PORT}/q/openapi", timeout_s=90):
            raise RuntimeError("JVM not ready")
        with httpx.Client(timeout=budget * 2 + 120) as cli:
            print(f"{'instance':<12}{'status':>10}{'hard':>7}{'cost':>10}"
                  f"{'sec':>8}", flush=True)
            for path in files:
                n_rows, cols = parse_spp(path)
                payload = {"rowCount": n_rows, "columns": [
                    {"id": i, "cost": c, "rows": rr}
                    for i, (c, rr) in enumerate(cols)]}
                t0 = time.monotonic()
                try:
                    r = cli.post(
                        f"http://127.0.0.1:{PORT}/spp/solve",
                        json=payload,
                        params={"spentLimitSeconds": budget,
                                "randomSeed": seed})
                    r.raise_for_status()
                    res = r.json()
                    sec = time.monotonic() - t0
                    hard = res.get("hard")
                    cost = res.get("cost")
                    status = "FEASIBLE" if hard == 0 else "INFEAS"
                except Exception as e:  # noqa: BLE001
                    sec = time.monotonic() - t0
                    hard, cost, status = None, None, f"ERR:{str(e)[:40]}"
                rows_out.append({"instance": path.stem, "budget_s": budget,
                                 "seed": seed, "status": status,
                                 "hard": hard, "cost": cost,
                                 "seconds": round(sec, 1)})
                print(f"{path.stem:<12}{status:>10}{str(hard):>7}"
                      f"{str(cost):>10}{sec:>8.1f}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        _kill_port(PORT)

    out.parent.mkdir(exist_ok=True)
    # Latest-run-wins policy: replace rows for re-run instances (see
    # upsert_csv_rows in _run_orlib_spp.py).
    fields = ["instance", "budget_s", "seed", "status", "hard", "cost",
              "seconds"]
    upsert_csv_rows(out, fields, rows_out)
    print(f"\nCSV: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
