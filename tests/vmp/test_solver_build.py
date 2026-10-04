# -*- coding: utf-8 -*-
"""Судьи сборки решателя и эфемерного решателя (§7 проекта
«Kotlin-исполнители агентом», тесты 1–16).

Настоящей JVM здесь нет: подделываются подпроцессы, перечень процессов,
опрос слушающих портов и клиент готовности — набор идёт секундами и
ничего не собирает. Единственный настоящий прогон (сборка, старт, стенд)
живёт отдельно: `tests/vmp/test_solver_build_real.py`, только при
`COSMOS_SLOW_SOLVER_BUILD=1`.

Правило безопасности набора: перечень процессов машины подделан на ВЕСЬ
модуль (`_без_настоящих_процессов`) — ни один тест не может снять живой
решатель контура или чужую JVM компилятора.
"""
from __future__ import annotations

import os

import inspect
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from vmp import solver_build as sb

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <artifactId>panorama-cos-solver-kotlin</artifactId>
  <version>1.0.0</version>
  <properties>
    <version.io.quarkus>3.34.5</version.io.quarkus>
    <version.kotlin>2.3.20</version.kotlin>
    <version.ai.timefold.solver>2.0.0</version.ai.timefold.solver>
    <version.surefire.plugin>3.5.2</version.surefire.plugin>
  </properties>
</project>
"""

SOLVER_TESTS = "kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling"
EXISTING_TEST = f"{SOLVER_TESTS}/solver/CrewSizeConstraintTest.kt"
NEW_TEST = f"{SOLVER_TESTS}/solver/LayoverNightsTest.kt"
LITERAL = "Layover nights away from home"

SUREFIRE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="{name}" tests="{tests}" failures="{failures}" '
    'errors="{errors}" skipped="{skipped}"/>\n')


# --------------------------------------------------------- обстановка

@pytest.fixture(autouse=True)
def _без_настоящих_процессов(monkeypatch):
    """Перечень процессов машины — пустой, снятие процессов — в журнал.

    Без этой изоляции тест сборщика сирот снял бы живой решатель
    контура: он зовётся тем же `java.exe`.
    """
    killed: list = []
    monkeypatch.setattr(sb, "_processes", lambda: [])
    monkeypatch.setattr(sb, "_kill_tree", lambda pid: killed.append(int(pid))
                        or True)
    return killed


def _клон_с_решателем(tmp_path: Path) -> Path:
    """Клон с исходниками решателя (pom, обёртка, один тест) — без target."""
    repo = tmp_path / "clone"
    (repo / f"{SOLVER_TESTS}/solver").mkdir(parents=True)
    (repo / "kotlin-solver" / "pom.xml").write_text(POM, encoding="utf-8")
    (repo / "kotlin-solver" / "mvnw.cmd").write_text("@echo off\n",
                                                     encoding="utf-8")
    (repo / EXISTING_TEST).write_text(
        "class CrewSizeConstraintTest {\n"
        "  @Test fun один() {}\n"
        "  @Test fun два() {}\n"
        "}\n", encoding="utf-8")
    (repo / f"{SOLVER_TESTS}/solver/Fixtures.kt").write_text(
        "object Fixtures\n", encoding="utf-8")
    return repo


def _maven_подделка(monkeypatch, command="mvnw.cmd"):
    monkeypatch.setattr(sb, "maven_order", lambda *a, **k: {
        "ok": True, "kind": "обёртка", "command": command,
        "dist": "apache-maven-3.9.16", "words": "Maven — обёртка"})


def _capture_подделка(monkeypatch, repo: Path, журнал: list):
    """git archive собирает настоящий tar; java -version отвечает словами."""
    def fake(argv, *, cwd=None, timeout_s=60, env=None):
        argv = [str(a) for a in argv]
        журнал.append(argv)
        if argv[0] == "git" and "archive" in argv:
            out = Path(argv[argv.index("-o") + 1])
            with tarfile.open(out, "w") as tf:
                tf.add(repo / "kotlin-solver", arcname="kotlin-solver")
            return 0, ""
        if argv[1:2] == ["-version"]:
            return 0, 'openjdk version "21.0.11" 2026-04-21 LTS'
        return 0, ""
    monkeypatch.setattr(sb, "_capture", fake)


def _сборка_подделка(monkeypatch, журнал: list, *, artifacts=True,
                     mutate_on_test=False, tests=2, failures=0, errors=0,
                     skipped=0, hs_err=False, rc=0):
    """Подделка Maven: пишет артефакт, отчёты surefire и журнал вызовов."""
    def fake(argv, *, cwd, log_path, timeout_s, env=None):
        argv = [str(a) for a in argv]
        журнал.append({"argv": argv, "cwd": Path(cwd),
                       "log": Path(log_path), "env": dict(env or {})})
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("BUILD SUCCESS\n", encoding="utf-8")
        qa = Path(cwd) / "target" / "quarkus-app"
        jar = qa / "app" / "panorama-cos-solver-kotlin-1.0.0.jar"
        if "package" in argv and artifacts:
            (qa / "app").mkdir(parents=True, exist_ok=True)
            jar.write_bytes(b"code-1")
            (qa / "quarkus-run.jar").write_bytes(b"run")
            (qa / "quarkus-app-dependencies.txt").write_text(
                "deps", encoding="utf-8")
        if "test" in argv:
            reports = Path(cwd) / "target" / "surefire-reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "TEST-CrewSizeConstraintTest.xml").write_text(
                SUREFIRE_XML.format(name="CrewSizeConstraintTest",
                                    tests=tests, failures=failures,
                                    errors=errors, skipped=skipped),
                encoding="utf-8")
            if mutate_on_test:
                jar.write_bytes(b"code-2-podmena")
            if hs_err:
                (Path(cwd) / "hs_err_pid1.log").write_text(
                    "# There is insufficient memory\n", encoding="utf-8")
        return rc, 1.0
    monkeypatch.setattr(sb, "_run_to_file", fake)
    return журнал


class FakeProc:
    """Подделка JVM: живёт, пока её не сняли (или умирает сразу)."""

    def __init__(self, pid=4242, dead_code=None):
        self.pid = pid
        self.returncode = dead_code
        self.terminated = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired("java", timeout or 0)
        return self.returncode


class _FakeFile:
    def close(self):
        return None


def _решатель_подделка(monkeypatch, tmp_path, *, procs, owner=4242,
                       healthy=True, log_text="started in 1.1s"):
    """Обстановка эфемерного решателя: порт, JVM, готовность, порт-PID."""
    qa = tmp_path / "quarkus-app"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "quarkus-run.jar").write_bytes(b"run")
    порты: list = []
    очередь = list(procs)

    def spawn(argv, *, cwd, log_path, env):
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(log_text + "\n")
        return очередь.pop(0), _FakeFile()

    monkeypatch.setattr(sb, "_spawn", spawn)
    monkeypatch.setattr(sb, "_free_port",
                        lambda: порты.append(50000 + len(порты)) or порты[-1])
    monkeypatch.setattr(sb, "_port_owner", lambda port: owner)
    monkeypatch.setattr(sb, "_port_free", lambda port: True)

    class Client:
        def health(self):
            return healthy

    monkeypatch.setattr(sb, "_health_client", lambda url: Client())
    return qa, порты


# ------------------------------------------------------- 0. контракт

def test_контракт_модуля_имена_и_подписи():
    """Контракт §9.1: имена, подписи и константы — то, что читает
    дорожка Б. Меняется подпись — краснеет здесь, а не в конвейере."""
    подписи = {
        "probe": ["repo", "java_home", "mvn", "venv_python"],
        "build": ["repo", "attempt", "source", "commit", "with_tests",
                  "timeout_package_s", "timeout_test_s", "java_home",
                  "mvn", "xmx_maven", "xmx_fork", "m2"],
        "test_hygiene": ["repo", "base", "seed_literals"],
        "tables_growth": ["before", "after"],
        "jvm_env": ["java_home", "maven_opts", "m2"],
        "reap_orphans": ["marker", "since"],
        "find_orphans": ["marker", "since"],
        "contour_tag": ["repo"],
        "contour_marker": ["repo"],
        "scrub": ["text", "roots"],
        "main": ["argv"],
    }
    for имя, ожидание in подписи.items():
        fn = getattr(sb, имя)
        assert list(inspect.signature(fn).parameters) == ожидание, имя
    методы = ["__init__", "__enter__", "__exit__", "alive", "stop",
              "report"]
    for имя in методы:
        assert callable(getattr(sb.EphemeralSolver, имя)), имя
    # §9.1 плюс признак контура в метке (находка ревью о межконтурном
    # сборщике сирот): ключ необязательный, прежняя связка ключей цела.
    assert list(inspect.signature(sb.EphemeralSolver.__init__).parameters) \
        == ["self", "artifact_dir", "cr_key", "log_path", "java_home",
            "xmx", "start_timeout_s", "tag"]
    inspect.signature(sb.EphemeralSolver.__init__).bind(
        None, "artifact", cr_key="cr1", log_path="solver.log",
        java_home=None, xmx="1g", start_timeout_s=120)
    assert sb.MARKER == "-Dcosmos.ephemeral="
    assert sb.KOTLIN_DAEMON_FLAG == "-Dkotlin.compiler.daemon=false"
    assert (sb.CLASS_ISP, sb.CLASS_TABL) == ("К-исп", "К-табл")
    assert (sb.RC_OK, sb.RC_RED, sb.RC_USAGE, sb.RC_NO_TOOLCHAIN) \
        == (0, 1, 2, 3)
    assert sb.TABLE_KEYS == ("knobs", "sit_knobs", "sit_param", "constants",
                             "history_families", "history_units",
                             "family_to_constraint", "logic_forms",
                             "metrics")
    print(f"рассмотрено имён контракта: {len(подписи) + len(методы)}")


# ----------------------------------------------------------- 1–3. проба

def test_проба_без_java_отказ_словами(tmp_path, monkeypatch):
    """1. Java нет: `ok=False`, слова называют Java и порядок поиска."""
    repo = _клон_с_решателем(tmp_path)
    monkeypatch.setattr(sb, "java_home_order", lambda *a, **k: (None, "нигде"))
    monkeypatch.setattr(sb, "java_exe", lambda home: "java")
    monkeypatch.setattr(sb, "_capture", lambda *a, **k: (127, "нет такой"))
    data = sb.probe(repo)
    assert data["ok"] is False and data["java"]["ok"] is False
    for слово in ("Java", "COSMOS_SOLVER_JAVA_HOME", "JAVA_HOME", "PATH"):
        assert слово in data["words"], слово
    assert "недоступна" in data["words"]


def test_проба_кэш_без_поставщика_surefire_называет_прогрев(
        tmp_path, monkeypatch):
    """2. Нет surefire-junit-platform: слова называют прогрев С ТЕСТАМИ."""
    repo = _клон_с_решателем(tmp_path)
    m2 = tmp_path / "m2" / "repository"
    (m2 / "org/jetbrains/kotlin/kotlin-maven-plugin/2.3.20").mkdir(
        parents=True)
    (m2 / "io/quarkus/quarkus-maven-plugin/3.34.5").mkdir(parents=True)
    monkeypatch.setattr(sb, "_m2_repository",
                        lambda m2=None: tmp_path / "m2" / "repository")
    _maven_подделка(monkeypatch)

    def fake(argv, *, cwd=None, timeout_s=60, env=None):
        if "-c" in [str(a) for a in argv]:
            return 0, "[]"
        return 0, 'openjdk version "21.0.11"'
    monkeypatch.setattr(sb, "_capture", fake)
    data = sb.probe(repo)
    assert data["cache"]["ok"] is False
    assert data["cache"]["missing"] == [
        "org/apache/maven/surefire/surefire-junit-platform/3.5.2"]
    assert "С ТЕСТАМИ" in data["words"] and "test" in data["words"]
    assert data["ok"] is False
    print(f"свидетелей кэша рассмотрено: {len(data['cache']['witnesses'])}")


def test_проба_импортов_венва_клона(tmp_path, monkeypatch):
    """3. Нет библиотеки CDPM: слова называют срез шагов 4–7."""
    repo = _клон_с_решателем(tmp_path)
    _maven_подделка(monkeypatch)
    m2 = tmp_path / "m2" / "repository"
    for w in sb.cache_witnesses(sb.pom_versions(repo / "kotlin-solver")):
        (m2 / w).mkdir(parents=True)
    monkeypatch.setattr(sb, "_m2_repository",
                        lambda m2=None: tmp_path / "m2" / "repository")

    def fake(argv, *, cwd=None, timeout_s=60, env=None):
        if "-c" in [str(a) for a in argv]:
            return 0, json.dumps(["cdpm.export_task_package: ImportError"])
        return 0, 'openjdk "21"'
    monkeypatch.setattr(sb, "_capture", fake)
    data = sb.probe(repo)
    assert data["imports"]["ok"] is False
    assert data["imports"]["missing"] == [
        "cdpm.export_task_package: ImportError"]
    assert "шагов 4–7" in data["words"] and "httpx" in data["words"]


# ----------------------------------------------------------- 4–7. сборка

def test_сборка_из_коммита_в_scratch_копии(tmp_path, monkeypatch):
    """4. Собирается КОПИЯ коммита, а не дерево; ключи Maven на месте;
    вывод идёт в открытый файл, а не в канал (ловушка демона)."""
    repo = _клон_с_решателем(tmp_path)
    настоящий_запуск = sb._run_to_file        # до подмены Maven
    _maven_подделка(monkeypatch)
    _capture_подделка(monkeypatch, repo, [])
    журнал = _сборка_подделка(monkeypatch, [])
    attempt = tmp_path / "attempt-1"
    data = sb.build(repo, attempt=attempt, source="commit",
                    commit="0" * 40, with_tests=False)
    assert data["available"] is True and data["ok"] is True
    assert len(журнал) == 1, "при with_tests=False Maven зовётся один раз"
    вызов = журнал[0]
    assert вызов["cwd"] == attempt / "kotlin-solver"
    assert вызов["cwd"] != repo / "kotlin-solver", "в дереве не собираем"
    for ключ in ("-o", "-B", sb.KOTLIN_DAEMON_FLAG, "-DskipTests",
                 "package"):
        assert ключ in вызов["argv"], ключ
    assert data["rc_test"] is None and data["command_test"] is None

    # Вывод — в ФАЙЛ: подделываем Popen и смотрим на kwargs.
    записи: list = []

    class P:
        pid = 1
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def popen(argv, **kw):
        записи.append(kw)
        return P()
    monkeypatch.setattr(sb.subprocess, "Popen", popen)
    настоящий_запуск(["cmd"], cwd=tmp_path, log_path=tmp_path / "x.log",
                     timeout_s=5)
    kw = записи[0]
    assert kw["stdout"] is not subprocess.PIPE
    assert hasattr(kw["stdout"], "write") and hasattr(kw["stdout"], "fileno")
    assert kw["stderr"] is subprocess.STDOUT
    assert kw["stdin"] is subprocess.DEVNULL


def test_тесты_после_упаковки_и_хеши_равны(tmp_path, monkeypatch):
    """5. Порядок: package → отпечаток → test → отпечаток; подмена
    архива между вызовами — красное словами."""
    repo = _клон_с_решателем(tmp_path)
    _maven_подделка(monkeypatch)
    _capture_подделка(monkeypatch, repo, [])
    журнал = _сборка_подделка(monkeypatch, [])
    data = sb.build(repo, attempt=tmp_path / "a1", source="commit",
                    commit="1" * 40, with_tests=True)
    порядок = [("test" if "test" in в["argv"] else "package")
               for в in журнал]
    assert порядок == ["package", "test"], порядок
    assert журнал[1]["argv"].count("-DargLine=-Xmx1g") == 1
    assert data["artifact"]["unchanged_after_tests"] is True
    assert data["ok"] is True and data["surefire"]["tests"] == 2
    assert data["tests_match_sources"] is True

    журнал2 = _сборка_подделка(monkeypatch, [], mutate_on_test=True)
    плохо = sb.build(repo, attempt=tmp_path / "a2", source="commit",
                     commit="2" * 40, with_tests=True)
    assert len(журнал2) == 2
    assert плохо["artifact"]["unchanged_after_tests"] is False
    assert плохо["ok"] is False
    assert "измерялось не то, что собрано" in плохо["words"]


def test_окружение_белый_список(monkeypatch, tmp_path):
    """6. Окружение Maven и JVM — белый список: ни секретов контура, ни
    ручек, молча меняющих поведение любой JVM."""
    for имя in ("CREW_TASK_PACKAGE", "CREW_RULES_CONFIG", "COSMOS_TEST_DIR",
                "CLAUDE_CODE_OAUTH_TOKEN", "TUNNEL_TOKEN",
                "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "MAVEN_CONFIG"):
        monkeypatch.setenv(имя, "значение контура")
    java_home = tmp_path / "jdk"
    (java_home / "bin").mkdir(parents=True)
    env = sb.jvm_env(java_home, maven_opts="-Xmx2g")
    for имя in ("CREW_TASK_PACKAGE", "CREW_RULES_CONFIG", "COSMOS_TEST_DIR",
                "CLAUDE_CODE_OAUTH_TOKEN", "TUNNEL_TOKEN",
                "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "MAVEN_CONFIG"):
        assert имя not in env, имя
    assert env["JAVA_HOME"] == str(java_home)
    assert env["PATH"].split(os.pathsep)[0] == str(java_home / "bin")
    assert env["MAVEN_OPTS"] == "-Xmx2g"
    # системные имена Windows идут в белый список только на Windows;
    # на Linux (CI GitHub) их нет и быть не должно
    if os.name == "nt":
        assert "SystemRoot" in env and "TEMP" in env
    else:
        assert "SystemRoot" not in env and "windir" not in env
    print(f"переменных в белом списке: {len(env)}")


def test_счёт_surefire_и_личность_артефакта(tmp_path):
    """7. Счёт — из машинных отчётов; личность артефакта — четыре
    отпечатка, сводный детерминирован от содержимого."""
    reports = tmp_path / "surefire-reports"
    reports.mkdir()
    (reports / "TEST-A.xml").write_text(
        SUREFIRE_XML.format(name="A", tests=10, failures=0, errors=0,
                            skipped=1), encoding="utf-8")
    (reports / "TEST-B.xml").write_text(
        SUREFIRE_XML.format(name="B", tests=7, failures=1, errors=2,
                            skipped=0), encoding="utf-8")
    счёт = sb.surefire_counts(reports)
    assert счёт == {"tests": 17, "failures": 1, "errors": 2, "skipped": 1,
                    "classes": 2}

    qa = tmp_path / "quarkus-app"
    (qa / "app").mkdir(parents=True)
    app_jar = qa / "app" / "panorama-cos-solver-kotlin-1.0.0.jar"
    app_jar.write_bytes("код".encode("utf-8"))
    (qa / "quarkus-run.jar").write_bytes("пусковой".encode("utf-8"))
    (qa / "quarkus-app-dependencies.txt").write_text("граф", encoding="utf-8")
    (qa / "lib").mkdir()
    (qa / "lib" / "main.jar").write_bytes("библиотека".encode("utf-8"))
    один = sb.artifact_identity(qa)
    два = sb.artifact_identity(qa)
    assert один["tree_sha256"] == два["tree_sha256"]
    assert len(один["app_jar_sha256"]) == 64
    assert один["app_jar"].endswith("panorama-cos-solver-kotlin-1.0.0.jar")
    assert один["files"] == 4
    (qa / "lib" / "main.jar").write_bytes("другая".encode("utf-8"))
    три = sb.artifact_identity(qa)
    assert три["tree_sha256"] != один["tree_sha256"], (
        "сводный отпечаток обязан видеть lib/, а не только app/*.jar")
    assert три["app_jar_sha256"] == один["app_jar_sha256"]
    assert sb._same_identity(один, два) and not sb._same_identity(один, три)
    assert sb.artifact_identity(tmp_path / "нет") is None


# ---------------------------------------------------------- 8. гигиена

def test_гигиена_тестов_новый_файл_с_литералом(tmp_path):
    """8. Новый файл теста с литералом посева — зелено; шесть подмен —
    шесть разных слов."""
    repo = _клон_с_решателем(tmp_path)

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", "-A")
    g("commit", "-m", "исходное состояние")

    новый = repo / NEW_TEST
    новый.write_text(
        "class LayoverNightsTest {\n"
        f'  @Test fun штраф_есть() {{ верни("{LITERAL}") }}\n'
        "  @Test fun штрафа_нет() {}\n"
        "}\n", encoding="utf-8")
    g("add", "-A")
    итог = sb.test_hygiene(repo, "HEAD", [LITERAL])
    assert итог["ok"] is True, итог["words"]
    assert итог["tests_main"] == 2 and итог["tests_branch"] == 4
    assert итог["new_files"] == [NEW_TEST]
    assert итог["literals"][NEW_TEST] == [LITERAL]
    assert итог["disabled_added"] == 0

    def проба(текст, *, файл=NEW_TEST, литералы=(LITERAL,)):
        (repo / файл).write_text(текст, encoding="utf-8")
        g("add", "-A")
        return sb.test_hygiene(repo, "HEAD", list(литералы))

    слова = []
    без_литерала = проба("class LayoverNightsTest {\n  @Test fun a() {}\n"
                         "  @Test fun b() {}\n}\n")
    assert без_литерала["ok"] is False
    слова.append("литерала")
    assert any("литерала строки посева" in p
               for p in без_литерала["problems"])

    с_disabled = проба("class LayoverNightsTest {\n"
                       f'  @Disabled @Test fun a() {{ "{LITERAL}" }}\n'
                       "  @Test fun b() {}\n}\n")
    assert с_disabled["ok"] is False and с_disabled["disabled_added"] == 1
    слова.append("@Disabled")
    assert any("@Disabled" in p for p in с_disabled["problems"])

    с_assume = проба("class LayoverNightsTest {\n"
                     f'  @Test fun a() {{ assumeTrue(false); "{LITERAL}" }}\n'
                     "  @Test fun b() {}\n}\n")
    assert any("assumeTrue" in p for p in с_assume["problems"])
    слова.append("assumeTrue")

    (repo / NEW_TEST).unlink()
    плохое_имя = repo / f"{SOLVER_TESTS}/solver/LayoverNights.kt"
    плохое_имя.write_text("class LayoverNights {\n"
                          f'  @Test fun a() {{ "{LITERAL}" }}\n'
                          "  @Test fun b() {}\n}\n", encoding="utf-8")
    g("add", "-A")
    итог = sb.test_hygiene(repo, "HEAD", [LITERAL])
    assert any("не оканчивается на Test.kt" in p for p in итог["problems"])
    слова.append("имя файла")
    плохое_имя.unlink()

    новый.write_text("class LayoverNightsTest {\n"
                     f'  @Test fun a() {{ "{LITERAL}" }}\n'
                     "  @Test fun b() {}\n}\n", encoding="utf-8")
    (repo / EXISTING_TEST).write_text(
        "class CrewSizeConstraintTest {\n  @Test fun один() {}\n}\n",
        encoding="utf-8")
    g("add", "-A")
    итог = sb.test_hygiene(repo, "HEAD", [LITERAL])
    assert итог["ok"] is False
    assert any("существующие файлы тестов" in p for p in итог["problems"])
    слова.append("правка существующего")
    g("checkout", "HEAD", "--", EXISTING_TEST)
    g("add", "-A")

    ресурс = (repo / "kotlin-solver/src/test/resources"
              / "junit-platform.properties")
    ресурс.parent.mkdir(parents=True, exist_ok=True)
    ресурс.write_text("junit.jupiter.conditions.deactivate=*",
                      encoding="utf-8")
    g("add", "-A")
    итог = sb.test_hygiene(repo, "HEAD", [LITERAL])
    assert any("гасит утверждения набора" in p for p in итог["problems"])
    слова.append("ресурс")
    print(f"подмен рассмотрено: {len(слова)}")


# ------------------------------------------------------- 9. рост таблиц

def test_рост_таблиц_называет_каждое_имя():
    """9. Каждое разошедшееся имя названо; кортежи после JSON равны."""
    до = {"knobs": {"max_duty_duration": {"unit": "duration_minutes",
                                          "relation": "max"}},
          "sit_knobs": {}, "sit_param": "min",
          "constants": {"FLEET_THRESHOLD": "fleet_types"},
          "history_families": ["night_series"],
          "history_units": {"night_duty_start": ("time_of_day_minutes",
                                                 "min")},
          "family_to_constraint": {}, "logic_forms": {}, "metrics": {}}
    после = json.loads(json.dumps({
        "knobs": {"max_duty_duration": {"unit": "hours",
                                        "relation": "max"},
                  "max_night_duties": {"unit": "count", "relation": "max"}},
        "sit_knobs": {}, "sit_param": "min",
        "constants": {"FLEET_THRESHOLD": "fleet_kinds"},
        "history_families": ["night_series", "rolling_rest"],
        "history_units": {"night_duty_start": ["time_of_day_minutes",
                                               "min"]},
        "family_to_constraint": {}, "logic_forms": {"NEW_FORM": "FORM_NEW"},
        "metrics": {"new_metric": {"constraint": "Имя"}}}))
    рост = sb.tables_growth(до, после)
    assert рост["knobs"] == ["max_duty_duration", "max_night_duties"]
    assert рост["constants"] == ["FLEET_THRESHOLD"]
    assert рост["history_families"] == ["rolling_rest"]
    assert рост["history_units"] == [], "кортеж и список после JSON равны"
    assert рост["logic_forms"] == ["NEW_FORM"]
    assert рост["metrics"] == ["new_metric"]
    assert рост["sit_param"] == []
    assert set(рост) == set(sb.TABLE_KEYS), "форма §4 не расширяется"
    класс = sb.growth_class(рост)
    assert класс["class"] == sb.CLASS_TABL
    for имя in ("knobs: max_night_duties", "logic_forms: NEW_FORM",
                "metrics: new_metric", "constants: FLEET_THRESHOLD"):
        assert имя in класс["words"], имя

    пусто = sb.tables_growth(до, json.loads(json.dumps(_как_json(до))))
    assert all(v == [] for v in пусто.values())
    assert sb.growth_class(пусто)["class"] == sb.CLASS_ISP
    print(f"ключей таблиц рассмотрено: {len(sb.TABLE_KEYS)}")


def _как_json(value):
    if isinstance(value, dict):
        return {k: _как_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_как_json(v) for v in value]
    return value


# ------------------------------------------------ 10–12. решатель

def test_эфемерный_решатель_порт_pid_готовность_остановка_в_finally(
        tmp_path, monkeypatch, _без_настоящих_процессов):
    """10. Порт от ОС, PID сошёлся с портом, исключение внутри `with` —
    решатель всё равно остановлен, порт свободен."""
    настоящий_порт = sb._free_port            # до подделки порта
    proc = FakeProc(pid=4242)
    qa, порты = _решатель_подделка(monkeypatch, tmp_path, procs=[proc])
    solver = sb.EphemeralSolver(qa, cr_key="cr-1",
                                log_path=tmp_path / "solver.log",
                                java_home=None, start_timeout_s=5)
    with pytest.raises(RuntimeError, match="гейт упал"):
        with solver as url:
            assert url == f"http://127.0.0.1:{порты[0]}"
            отметка = json.loads(
                (tmp_path / "solver.pid").read_text(encoding="utf-8"))
            assert отметка["pid"] == 4242 and отметка["port"] == порты[0]
            assert отметка["marker"] == sb.MARKER + "cr-1"
            raise RuntimeError("гейт упал")
    assert not (tmp_path / "solver.pid").exists(), (
        "отметка снимается вместе с решателем")
    рапорт = solver.report()
    assert рапорт["pid_matches_port"] is True and рапорт["pid"] == 4242
    assert рапорт["stopped"] == "terminated" and proc.terminated is True
    assert рапорт["port_free_after"] is True
    assert рапорт["attempts"] == 1 and рапорт["started_in_s"] is not None
    assert рапорт["env"] == "белый список"
    assert f"{sb.MARKER}cr-1" in рапорт["jvm"]
    assert "-Xmx1g" in рапорт["jvm"] and "-XX:+UseG1GC" in рапорт["jvm"]
    assert рапорт["limits"]["start_timeout_s"] == 5
    assert рапорт["limits"]["poll_s"] is not None, "пределы — импортом"
    assert рапорт["pid_file"] == "solver.pid"
    assert "started in 1.1s" in рапорт["banner"]
    assert рапорт["artifact_unchanged_after"] is None

    # Настоящий выбор порта (подделки процессов при этом остаются на
    # месте): ОС на этой машине выдаёт порты и ниже 49152, поэтому
    # резервное окно служб пропускается.
    низ, верх = sb.RESERVED_PORT_WINDOW
    порты_ос = [настоящий_порт() for _ in range(3)]
    assert all(not (низ <= p <= верх) for p in порты_ос), порты_ос
    print(f"портов от ОС рассмотрено: {len(порты_ос)}")


def test_эфемерный_решатель_повтор_при_занятом_порте_и_смерти(
        tmp_path, monkeypatch, _без_настоящих_процессов):
    """11. Первая JVM умерла — вторая попытка на другом порту; обе
    неудачны — слова и снятие процесса."""
    мёртвая = FakeProc(pid=1, dead_code=1)
    живая = FakeProc(pid=2)
    qa, порты = _решатель_подделка(monkeypatch, tmp_path,
                                   procs=[мёртвая, живая], owner=2)
    solver = sb.EphemeralSolver(qa, cr_key="cr-2",
                                log_path=tmp_path / "s.log",
                                java_home=None, start_timeout_s=5)
    with solver as url:
        assert solver.attempts == 2
        assert url == f"http://127.0.0.1:{порты[1]}"
        assert порты[0] != порты[1]
    assert solver.report()["stopped"] == "terminated"

    висящая = FakeProc(pid=3)
    qa2, _ = _решатель_подделка(monkeypatch, tmp_path / "два",
                                procs=[FakeProc(pid=1, dead_code=1),
                                       висящая],
                                healthy=False,
                                log_text="Address already in use")
    плохой = sb.EphemeralSolver(qa2, cr_key="cr-3",
                                log_path=tmp_path / "s2.log",
                                java_home=None, start_timeout_s=0.2)
    with pytest.raises(sb.SolverBuildError, match="не поднялся"):
        with плохой:
            pass
    assert плохой.attempts == 2
    assert 3 in _без_настоящих_процессов, "висящая JVM снята taskkill"
    assert "хвост журнала" in плохой.words


def test_pid_на_порту_чужой_это_отказ(tmp_path, monkeypatch,
                                      _без_настоящих_процессов):
    """12. Порт слушает не наш процесс: отказ словами, решатель снят."""
    proc = FakeProc(pid=4242)
    qa, _ = _решатель_подделка(monkeypatch, tmp_path, procs=[proc],
                               owner=9999)
    solver = sb.EphemeralSolver(qa, cr_key="cr-4",
                                log_path=tmp_path / "s.log",
                                java_home=None, start_timeout_s=5)
    with pytest.raises(sb.SolverBuildError, match="чужой процесс"):
        solver.start()
    рапорт = solver.report()
    assert рапорт["pid_matches_port"] is False
    assert proc.terminated is True and рапорт["stopped"] == "terminated"
    assert рапорт["port_free_after"] is True


# ------------------------------------------------------- 13. сироты

def test_сборщик_сирот_только_по_метке_и_командной_строке(monkeypatch):
    """13. Снимается помеченный и демон компилятора этой сборки;
    решатель контура по имени `java` не трогается никогда."""
    снятые: list = []
    процессы = [
        {"pid": 11, "created": 100.0,
         "cmd": "java.exe -Dquarkus.http.port=24680 -jar quarkus-run.jar"},
        {"pid": 12, "created": 100.0,
         "cmd": f"java.exe {sb.MARKER}cr-7 -jar quarkus-run.jar"},
        {"pid": 13, "created": 50.0,
         "cmd": "java.exe -cp kotlin-build-tools-impl.jar Daemon"},
        {"pid": 14, "created": 300.0,
         "cmd": "java.exe -cp kotlin-build-tools-impl.jar Daemon"},
        {"pid": 15, "created": 300.0,
         "cmd": "java.exe org.jetbrains.kotlin.daemon.KotlinCompileDaemon"},
    ]
    monkeypatch.setattr(sb, "_processes", lambda: процессы)
    monkeypatch.setattr(sb, "_kill_tree",
                        lambda pid: снятые.append(int(pid)) or True)
    assert sb.reap_orphans() == [12]
    снятые.clear()
    assert sorted(sb.reap_orphans(sb.COMPILER_MARKERS, since=200.0)) \
        == [14, 15], "демон, живший до сборки, не наш"
    assert 11 not in снятые, "решатель контура не тронут"
    print(f"процессов рассмотрено: {len(процессы)}")


# ------------------------------------------------------ 14. скруббер

def test_скруббер_путей_не_портит_адреса():
    """14. Корни — словами, машинные пути — «<путь>», адрес решателя и
    относительные пути целы."""
    текст = (r"C:\cosmos\cosmos-test\_repro\attempt-1\build.log и "
             r"C:\cosmos\cosmos-test\vmp\stand_tasks.py и "
             "/home/lev/.m2/repository и http://127.0.0.1:53127 и "
             "https://cosmos.fregene.ai/x и kotlin-solver/target/квк.jar")
    вышло = sb.scrub(текст, [r"C:\cosmos\cosmos-test",
                             r"C:\cosmos\cosmos-test\_repro\attempt-1"])
    assert "<попытка>" in вышло and "<клон>" in вышло
    assert "C:\\cosmos" not in вышло and "/home/lev" not in вышло
    assert "http://127.0.0.1:53127" in вышло, "адрес решателя цел"
    assert "https://cosmos.fregene.ai/x" in вышло
    assert "kotlin-solver/target/квк.jar" in вышло, "путь от корня цел"
    assert sb.scrub("C:/cosmos/cosmos-test/vmp", [r"C:\cosmos\cosmos-test"]) \
        == "<клон>/vmp", "вид разделителя не важен"
    assert sb.scrub(None) == ""


# --------------------------------------------------------- 15. hs_err

def test_hs_err_найден_и_попал_в_свидетельство(tmp_path, monkeypatch):
    """15. Отчёт JVM о падении — в свидетельстве, файл убран за собой,
    итог сборки красный."""
    repo = _клон_с_решателем(tmp_path)
    _maven_подделка(monkeypatch)
    _capture_подделка(monkeypatch, repo, [])
    _сборка_подделка(monkeypatch, [], hs_err=True)
    attempt = tmp_path / "attempt-9"
    data = sb.build(repo, attempt=attempt, source="commit",
                    commit="3" * 40, with_tests=True)
    assert len(data["hs_err"]) == 1
    assert "insufficient memory" in data["hs_err"][0]
    assert not list(attempt.rglob("hs_err_pid*.log")), "файл убран"
    assert data["ok"] is False and "JVM падала" in data["words"]


# ----------------------------------------------------------- 16. CLI

def test_команда_check_порядок_и_код_возврата(tmp_path, monkeypatch,
                                              capsys):
    """16. `check`: сборка → старт → стенд `--with-solver` → остановка;
    код возврата — код стенда; без Java — слова и код 3."""
    repo = _клон_с_решателем(tmp_path)
    журнал: list = []

    monkeypatch.setattr(sb, "probe", lambda *a, **k: {
        "ok": True, "words": "проба тулчейна: всё на месте"})

    def fake_build(repo_, **kw):
        журнал.append(("build", kw["source"], kw["with_tests"]))
        return {"available": True, "ok": True, "words": "сборка зелёная"}
    monkeypatch.setattr(sb, "build", fake_build)

    class FakeSolver:
        def __init__(self, artifact_dir, *, cr_key, log_path,
                     java_home=None, xmx="1g", start_timeout_s=120,
                     tag=None):
            журнал.append(("solver", str(cr_key), str(tag)))

        def __enter__(self):
            журнал.append(("start",))
            return "http://127.0.0.1:1"

        def __exit__(self, *a):
            журнал.append(("stop",))
            return False

        def report(self):
            return {"stopped": "terminated", "port_free_after": True}
    monkeypatch.setattr(sb, "EphemeralSolver", FakeSolver)

    def fake_run(argv, *, cwd, log_path, timeout_s, env=None):
        журнал.append(("стенд", [str(a) for a in argv]))
        return 0, 2.0
    monkeypatch.setattr(sb, "_run_to_file", fake_run)

    код = sb.main(["check", "--repo", str(repo), "--records",
                   "layover_penalty"])
    assert код == sb.RC_OK
    шаги = [ш[0] for ш in журнал]
    assert шаги == ["build", "solver", "start", "стенд", "stop"], шаги
    assert журнал[0] == ("build", "tree", True)
    # Признак контура в метке: самопроверка агента поднимает решатель с
    # тем же тегом, каким его снимет сборщик сирот ЭТОГО клона.
    assert журнал[1] == ("solver", "check", sb.contour_tag(repo))
    argv = журнал[3][1]
    assert "vmp.stand_tasks" in argv
    assert argv[argv.index("--with-solver") + 1] == "http://127.0.0.1:1"
    assert argv[argv.index("--records") + 1] == "layover_penalty"
    assert argv[argv.index("--kinds") + 1] == ",".join(sb.LIVE_KINDS)
    assert "--write-results" not in argv, (
        "стенд отказывает кодом 2 на паре --records и --write-results")
    вывод = capsys.readouterr().out
    assert "эфемерный решатель" in вывод and "остановлен" in вывод

    журнал.clear()
    monkeypatch.setattr(sb, "_run_to_file",
                        lambda *a, **k: журнал.append(("стенд",)) or (1, 1.0))
    assert sb.main(["check", "--repo", str(repo), "--records", "x"]) \
        == sb.RC_RED
    assert ("stop",) in журнал, "решатель остановлен и при красном стенде"

    monkeypatch.setattr(sb, "probe", lambda *a, **k: {
        "ok": False, "words": "сборка решателя недоступна: Java не найдена"})
    assert sb.main(["check", "--repo", str(repo), "--records", "x"]) \
        == sb.RC_NO_TOOLCHAIN
    assert "Java не найдена" in capsys.readouterr().out
    assert sb.main([]) == sb.RC_USAGE


# ------------------------------ 17–19. находки ревью пакета (08.09.2026)

def test_сборщик_сирот_не_трогает_соседний_контур(tmp_path, monkeypatch,
                                                  _без_настоящих_процессов):
    """17. Метка несёт признак КОНТУРА, и снимается только своя JVM.

    Контуры Test, Stage и Prod — зеркальные копии дерева на одной машине
    под одной учётной записью: снятие «всех эфемерных решателей машины»
    убивало бы суд соседнего контура на полпути (стенд идёт минутами), и
    его строка уходила бы в «нужен человек» со словами «умер сам».
    """
    свой = tmp_path / "cosmos-test"
    чужой = tmp_path / "cosmos-stage"
    свой.mkdir()
    чужой.mkdir()
    assert sb.contour_tag(свой) != sb.contour_tag(чужой)
    assert sb.contour_marker(свой).startswith(sb.MARKER)

    процессы = [
        {"pid": 21, "created": 100.0,
         "cmd": f"java.exe {sb.contour_marker(свой)}cr-7 -jar run.jar"},
        {"pid": 22, "created": 100.0,
         "cmd": f"java.exe {sb.contour_marker(чужой)}cr-9 -jar run.jar"},
        {"pid": 23, "created": 100.0,
         "cmd": "java.exe -Dquarkus.http.port=24680 -jar run.jar"},
    ]
    monkeypatch.setattr(sb, "_processes", lambda: процессы)
    assert sb.reap_orphans(sb.contour_marker(свой)) == [21]
    assert _без_настоящих_процессов == [21], "снят только свой контур"

    # Половина «найти, не снимая» — для пробы и самопроверки агента.
    _без_настоящих_процессов.clear()
    найдено = sb.find_orphans(sb.contour_marker(чужой))
    assert [r["pid"] for r in найдено] == [22]
    assert _без_настоящих_процессов == [], "поиск сирот никого не снимает"


def test_проба_сирот_не_снимает_и_называет_самопроверку(
        tmp_path, monkeypatch, _без_настоящих_процессов):
    """18. Проба (её зовёт и самопроверка агента) сирот НЕ снимает, а
    называет словами; модуль сборки в клоне — слова, но не отказ: судью
    конвейер берёт из продукта, клоном не подменяя."""
    repo = _клон_с_решателем(tmp_path)
    _maven_подделка(monkeypatch)
    хранилище = tmp_path / "m2" / "repository"
    for w in sb.cache_witnesses(sb.pom_versions(repo / "kotlin-solver")):
        (хранилище / w).mkdir(parents=True)
    monkeypatch.setattr(sb, "_m2_repository", lambda m2=None: хранилище)
    monkeypatch.setattr(sb, "_processes", lambda: [
        {"pid": 31, "created": 1.0,
         "cmd": f"java.exe {sb.contour_marker(repo)}cr-1 -jar run.jar"}])

    def fake(argv, *, cwd=None, timeout_s=60, env=None):
        argv = [str(a) for a in argv]
        if "-c" in argv and "import vmp.solver_build" in argv:
            return 1, "No module named vmp.solver_build"
        if "-c" in argv:
            return 0, "[]"
        return 0, 'openjdk version "21.0.11"'
    monkeypatch.setattr(sb, "_capture", fake)

    data = sb.probe(repo)
    assert data["ok"] is True, data["words"]
    assert data["orphans"] == [31]
    assert _без_настоящих_процессов == [], "проба сирот не снимает"
    assert "не снимает" in data["words"]
    assert data["agent_self_check"]["ok"] is False
    assert "самопроверка агента" in data["words"]


def test_гигиена_считает_условные_пропуски_и_кириллицу(tmp_path):
    """19. Мера «пропущенных не стало больше» считает ВСЮ семью
    механизмов пропуска JUnit 5, а имя нового файла теста может быть
    русским.

    В дереве нет ни одного `@Disabled`, но surefire стабильно даёт
    `skipped=1` из-за `@EnabledIfSystemProperty(named = "bench")`: счёт
    по двум аннотациям краснил бы КАЖДЫЙ CR с видами решателя по
    построению. Имя файла с кириллицей git печатает в кавычках и
    восьмеричных последовательностях — без `core.quotePath=false` он
    переставал оканчиваться на «.kt», и гигиена говорила «нового файла
    теста нет» при наличии файла.
    """
    repo = _клон_с_решателем(tmp_path)
    (repo / f"{SOLVER_TESTS}/solver/BenchTest.kt").write_text(
        "class BenchTest {\n"
        '  @EnabledIfSystemProperty(named = "bench", matches = "true")\n'
        "  @Test fun долгий() {}\n"
        "}\n", encoding="utf-8")

    def g(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", "-A")
    g("commit", "-m", "база с условным тестом")

    новый = repo / f"{SOLVER_TESTS}/solver/НочёвкиTest.kt"
    новый.write_text(
        "class НочёвкиTest {\n"
        f'  @Test fun штраф_есть() {{ верни("{LITERAL}") }}\n'
        "  @Test fun штрафа_нет() {}\n"
        "}\n", encoding="utf-8")
    g("add", "-A")
    итог = sb.test_hygiene(repo, "HEAD", [LITERAL])
    assert итог["skipped_main"] == 1, (
        "условная аннотация базы считается пропуском — иначе судья "
        "«skipped ≤ skipped_main» красен по построению")
    assert итог["new_files"] == [f"{SOLVER_TESTS}/solver/НочёвкиTest.kt"]
    assert итог["ok"] is True, итог["words"]
    print(f"пропусков на базе: {итог['skipped_main']}")
