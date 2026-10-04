"""Generate experiment-manifest.json: SHA-256 of primary inputs, code and
result artifacts (audit requirement, 2026-07-12)."""
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


import os
import platform

# Stage/prod split (2026-07-12): PROART is the development stand, legion is
# production — every manifest must say which machine produced it.
_MACHINE = os.environ.get(
    "CREW_MACHINE",
    f"{platform.node()} ({os.cpu_count()} cores, "
    f"{platform.system()} {platform.release()})")

manifest = {
    "generated": "2026-07-12",
    "machine": _MACHINE,
    "python": sys.version.split()[0],
    "note": ("SHA-256 of primary inputs, code and result artifacts. Raw "
             "COR@L files live outside git at "
             "C:/work/Sandbox/Testing/TimeFold/coral-data (official "
             "downloads); raw BTS at data/bts (excluded from git)."),
}

groups = {
    "inputs": (["data/gerad/G1422-DataSets.zip"]
               + [f"data/gerad/payloads/inst{i}.json" for i in range(1, 8)]
               + [f"data/gerad/payloads/inst{i}_p2.json" for i in range(1, 8)]
               + ["data/bts_schedules/DL_ATL_2024-08-15.json"]),
    "code": (sorted(str(p) for p in Path(".").glob("_gerad_*.py"))
             + sorted(str(p) for p in Path(".").glob("_coral*.py"))
             + ["_run_orlib_spp.py", "_run_spp_timefold.py",
                "_make_report_pdfs.py"]
             + sorted(str(p) for p in Path(
                 "kotlin-solver/src/main/kotlin/org/acme/"
                 "flighcrewscheduling/solver").glob("Gerad*.kt"))),
    "results": (sorted(str(p) for p in Path("final-report").glob("*.json"))
                + sorted(str(p) for p in
                         Path("final-report").glob("stage1/*.json"))
                + sorted(str(p) for p in
                         Path("final-report").glob("best/meta_*.json"))
                + sorted(str(p) for p in
                         Path("final-report").glob("coral*_scip.log"))
                + sorted(str(p) for p in
                         Path("final-report").glob("*.json.gz"))
                + sorted(str(p) for p in Path("final-report").glob("*.csv"))
                + ["final-report/PROFESSIONAL-REPORT-RU.md",
                   "final-report/EXEC-SUMMARY-RU.md",
                   "final-report/PROFESSIONAL-REPORT-RU.pdf",
                   "final-report/EXEC-SUMMARY-RU.pdf",
                   "final-report/comparison-matrix.md",
                   "final-report/corrected-space-notes.md",
                   "final-report/README-REPORTS.md"]),
    "validators_tests": (
        ["_coverage_validator.py", "_gerad_trajectory_check.py",
         "_gerad_leg_feasibility.py", "_gerad_complete_catalogs.py",
         "_run_tests.sh", "src/flight_crew_scheduling/ortools_engine.py",
         "src/flight_crew_scheduling/pairing_to_schedule.py",
         "src/flight_crew_scheduling/pairing_selector.py",
         "src/flight_crew_scheduling/rest_api.py", "pyproject.toml",
         "LICENSE", "README.md", "_make_manifest.py"]
        + sorted(str(p) for p in Path("tests").glob("*.py"))
        + sorted(str(p) for p in Path(
            "kotlin-solver/src/main/kotlin/org/acme/flighcrewscheduling")
            .glob("**/*.kt"))),
    "flight_level_solutions": sorted(
        str(p) for p in Path("data/gerad").glob("p2_*_solution_inst*.json")),
    "coral_raw_external": sorted(
        str(p) for p in
        Path("C:/work/Sandbox/Testing/TimeFold/coral-data").glob("a320*")),
}

out = {}
for g, files in groups.items():
    out[g] = {}
    for f in files:
        p = Path(f)
        if p.exists() and p.is_file():
            out[g][str(f).replace("\\", "/")] = sha(p)
manifest["files"] = out
jar = Path("kotlin-solver/target/quarkus-app/quarkus-run.jar")
if jar.exists():
    manifest["kotlin_jar_sha256"] = sha(jar)
Path("experiment-manifest.json").write_text(json.dumps(manifest, indent=1))
n = sum(len(v) for v in out.values())
print(f"experiment-manifest.json: {n} files hashed")
for g, v in out.items():
    print(f"  {g}: {len(v)}")
