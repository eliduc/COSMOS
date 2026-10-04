"""Манифест сборки Kotlin-солвера (аудит-2, P2-06).

Запускается ПОСЛЕ `kotlin-solver/mvnw clean verify` и фиксирует
воспроизводимое свидетельство сборки: коммит + чистота дерева, тулчейн
(JDK/Maven из wrapper-пина), число прогнанных тестов из свежих
surefire-отчётов и SHA-256 исполняемых артефактов. Результат —
final-report/kotlin-build-manifest.json (перезаписывается каждой сборкой;
история — в git).

Использование:
    python _kotlin_build_manifest.py            # собрать манифест
    python _kotlin_build_manifest.py --check    # только проверить свежесть
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

HERE = Path(__file__).resolve().parent
SOLVER = HERE / "kotlin-solver"
OUT = HERE / "final-report" / "kotlin-build-manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(HERE), *args],
                          capture_output=True, text=True).stdout.strip()


def surefire_counts() -> dict:
    """Суммы из target/surefire-reports/*.xml — только СВЕЖИЕ отчёты
    (аудит-2: сталый XML удалённого теста завышал счёт на 25 vs 24)."""
    reports = sorted((SOLVER / "target" / "surefire-reports").glob("TEST-*.xml"))
    total = failures = errors = skipped = 0
    suites = []
    for p in reports:
        root = ElementTree.parse(p).getroot()
        total += int(root.get("tests") or 0)
        failures += int(root.get("failures") or 0)
        errors += int(root.get("errors") or 0)
        skipped += int(root.get("skipped") or 0)
        suites.append({"suite": root.get("name"),
                       "tests": int(root.get("tests") or 0)})
    return {"tests": total, "failures": failures, "errors": errors,
            "skipped": skipped, "suites": suites}


def source_test_count() -> int:
    """@Test-аннотации в исходниках — сверка с surefire (paritycheck)."""
    n = 0
    for p in (SOLVER / "src" / "test").rglob("*.kt"):
        n += len(re.findall(r"@Test\b", p.read_text(encoding="utf-8")))
    return n


def toolchain() -> dict:
    java = subprocess.run(["java", "-version"], capture_output=True,
                          text=True).stderr.splitlines()
    props = (SOLVER / ".mvn" / "wrapper" / "maven-wrapper.properties")
    dist = ""
    if props.exists():
        m = re.search(r"distributionUrl=.*apache-maven-([\d.]+)-bin",
                      props.read_text(encoding="utf-8"))
        dist = m.group(1) if m else ""
    return {"java": java[0] if java else "", "maven_wrapper_pin": dist}


def build_manifest() -> dict:
    qa = SOLVER / "target" / "quarkus-app"
    run_jar = qa / "quarkus-run.jar"
    app_jars = sorted((qa / "app").glob("*.jar")) if qa.exists() else []
    sf = surefire_counts()
    src_tests = source_test_count()
    return {
        "schema": "kotlin-build-manifest/v1",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "commit": git("rev-parse", "HEAD"),
        "commit_short": git("rev-parse", "--short", "HEAD"),
        "tree_dirty": bool(git("status", "--porcelain", "--",
                               "kotlin-solver/src", "kotlin-solver/pom.xml")),
        "toolchain": toolchain(),
        "surefire": sf,
        "source_test_annotations": src_tests,
        "tests_match_sources": sf["tests"] == src_tests,
        "artifacts": {
            "quarkus_run_jar_sha256":
                sha256_file(run_jar) if run_jar.exists() else None,
            "app_jars": {p.name: sha256_file(p) for p in app_jars},
        },
    }


def main(argv: list[str]) -> int:
    m = build_manifest()
    if "--check" in argv:
        ok = (m["surefire"]["tests"] > 0
              and m["surefire"]["failures"] == 0
              and m["surefire"]["errors"] == 0
              and m["tests_match_sources"])
        print(json.dumps({k: m[k] for k in
                          ("commit_short", "surefire",
                           "source_test_annotations",
                           "tests_match_sources")}, indent=1))
        return 0 if ok else 2
    OUT.write_text(json.dumps(m, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"manifest -> {OUT.name}: tests={m['surefire']['tests']} "
          f"(sources={m['source_test_annotations']}, "
          f"match={m['tests_match_sources']}), "
          f"jar={str(m['artifacts']['quarkus_run_jar_sha256'])[:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
