"""Build the auditor hand-off zip (audit 2026-07-12, round 5, item 6:
arcname-level dedup — glob patterns like inst*.json also match
inst*_p2.json, which previously produced duplicate zip entries).

Usage: python _make_audit_package.py <suffix>
       (e.g. `e` -> final-report/audit-package-20260712e.zip)
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                          text=True).stdout.strip()


def collect() -> list[Path]:
    fr = HERE / "final-report"
    kt = HERE / ("kotlin-solver/src/main/kotlin/org/acme/"
                 "flighcrewscheduling")
    files: list[Path] = []
    files += sorted(HERE.glob("_gerad_*.py"))
    files += sorted(HERE.glob("_coral*.py"))
    files += [HERE / n for n in (
        "_coverage_validator.py", "_run_orlib_spp.py", "_run_spp_timefold.py",
        "_make_report_pdfs.py", "_make_manifest.py", "_make_audit_package.py",
        "_run_tests.sh", "pyproject.toml", "experiment-manifest.json",
        "LICENSE", "README.md",
        # Root helper modules imported by the packaged scripts
        # (_gerad_stage2_roster.py / _gerad_phase2_direct.py import
        # IsolatedJvmFleet from here) — the zip must be self-contained.
        "_compr_isolated.py")]
    # The whole Python package: packaged scripts import benchmark,
    # demo_data etc., so shipping a subset broke self-containment.
    files += sorted((HERE / "src" / "flight_crew_scheduling").glob("*.py"))
    files += sorted((HERE / "tests").glob("*.py"))
    files += [HERE / "kotlin-solver" / "pom.xml"]
    files += sorted((kt / "solver").glob("*.kt"))
    files += [kt / "domain" / "FlightAssignment.kt",
              kt / "rest" / "FlightCrewSchedulingResource.kt"]
    files += [fr / n for n in (
        "PROFESSIONAL-REPORT-RU.md", "PROFESSIONAL-REPORT-RU.pdf",
        "EXEC-SUMMARY-RU.md", "EXEC-SUMMARY-RU.pdf",
        "README-REPORTS.md", "comparison-matrix.md",
        "corrected-space-notes.md")]
    files += sorted(fr.glob("gerad_*.json"))
    files += sorted(fr.glob("gerad_pool_inst*.json.gz"))
    files += sorted(fr.glob("catalog_b_inst*.json"))
    files += sorted(fr.glob("stage1/inst*.json"))
    files += sorted(fr.glob("coral*.json"))
    files += sorted(fr.glob("coral*_scip.log"))     # raw solver log (cert)
    files += sorted(fr.glob("bts_*.json"))          # BTS conversion check
    files += sorted(fr.glob("best/meta_*.json"))    # durable-best metadata
    files += [fr / n for n in ("orlib_spp_results.csv",
                               "orlib_spp_timefold.csv", "spp_optima.json")]
    pay = HERE / "data" / "gerad" / "payloads"
    files += sorted(pay.glob("inst*.json"))       # matches *_p2.json too;
    files += sorted(pay.glob("inst*_p2.json"))    # dedup handles the overlap
    files += sorted((HERE / "data" / "gerad").glob(
        "p2_*_solution_inst*.json"))
    # GERAD raw dataset zip: include only if it stays under the 45 MB
    # package budget (currently ~2.4 MB); otherwise the README notes the
    # omission and the manifest carries its SHA-256.
    g1422 = HERE / "data" / "gerad" / "G1422-DataSets.zip"
    if g1422.exists() and g1422.stat().st_size < 45 * 1024 * 1024:
        files += [g1422]
    return files


def main() -> int:
    suffix = sys.argv[1] if len(sys.argv) > 1 else "e"
    out = HERE / "final-report" / f"audit-package-20260712{suffix}.zip"
    commit = git("rev-parse", "HEAD")
    tag = git("describe", "--tags", "--always")
    readme = (
        "AUDIT PACKAGE (crew-demo PoC)\n"
        f"built: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"git commit: {commit}\n"
        f"git tag/describe: {tag}\n"
        "license: Apache-2.0 (LICENSE)\n\n"
        "Actual reports: final-report/PROFESSIONAL-REPORT-RU.md (full),\n"
        "final-report/EXEC-SUMMARY-RU.md (summary); navigation in\n"
        "final-report/README-REPORTS.md.\n\n"
        "Canonical stage-1 records: final-report/gerad_stage1_results.json\n"
        "(aggregate) + final-report/stage1/inst{1..7}.json (per instance:\n"
        "initial budget result -> feasibility certification -> monotonic\n"
        "catalog completion). Validation artifacts:\n"
        "gerad_coverage_check.json, gerad_trajectory_check.json.\n\n"
        "Integrity: experiment-manifest.json (SHA-256 of inputs, code and\n"
        "results). Tests: _run_tests.sh (full pytest battery).\n\n"
        "Environment\n"
        "-----------\n"
        "To reproduce the Python side:\n"
        "    python -m venv .venv\n"
        "    .venv/Scripts/pip install ortools httpx fastapi pytest\n"
        "_run_tests.sh expects that venv at .venv/. The Kotlin solver\n"
        "builds from kotlin-solver/pom.xml (Maven, JDK 21).\n"
        "External raw datasets are NOT in this zip: BTS on-time data\n"
        "(expected at data/bts) and COR@L instances (expected at\n"
        "C:/work/Sandbox/Testing/TimeFold/coral-data). Their SHA-256\n"
        "checksums are recorded in experiment-manifest.json. The GERAD\n"
        "dataset zip (data/gerad/G1422-DataSets.zip) IS included when it\n"
        "fits the 45 MB package budget; if absent here, fetch it and\n"
        "verify against the manifest checksum.\n")
    seen: set[str] = set()
    n_dup = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README-AUDIT.txt", readme)
        for p in collect():
            if not p.exists() or not p.is_file():
                continue
            arc = p.relative_to(HERE).as_posix()
            if arc in seen:
                n_dup += 1
                continue
            seen.add(arc)
            z.write(p, arc)
    print(f"{out.name}: {len(seen) + 1} entries "
          f"({n_dup} duplicate paths skipped), "
          f"{out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
