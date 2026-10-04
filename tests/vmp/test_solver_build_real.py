# -*- coding: utf-8 -*-
"""Единственный НАСТОЯЩИЙ прогон сборки решателя и эфемерного решателя
(§7 проекта «Kotlin-исполнители агентом», тест 17).

Здесь всё по-настоящему: `git archive HEAD kotlin-solver` в scratch-папку
под `_repro/`, сборка обёрткой Maven оффлайн, тесты решателя после
упаковки, запуск собранного артефакта на свободном порту, живой стенд
записи и остановка. Прогон идёт МИНУТАМИ, поэтому в обычном наборе он
пропускается словами: включается переменной `COSMOS_SLOW_SOLVER_BUILD=1`,
которая до гейтового `pytest` конвейера не доезжает (окружение гейтов
чистится по префиксу `COSMOS_`) — бюджет 900 с у каждого запроса на
изменение остаётся целым.

    set COSMOS_SLOW_SOLVER_BUILD=1
    .venv\\Scripts\\python.exe -m pytest -q -s ^
        tests\\vmp\\test_solver_build_real.py

Числа, которые печатает прогон (секунды сборки, тестов, старта, стенда и
пик памяти), — те самые, что стоят в §5 проекта и в руководстве 13.10.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vmp import solver_build as sb

#: Запись каталога с зелёной живой строкой вида `kotlin_constraint`
#: (`duty_limits` сегодня красна — отметка владельца в стенде).
RECORD = "layover_penalty"

#: Предел прогона (сторож зависаний набора; §7: 30 минут).
LIMIT_S = 1800


def _java_working_sets(baseline: set) -> int:
    """Сумма рабочих наборов ТОЛЬКО новых JVM, байты.

    Решатели контуров (они же `java.exe`) в замер не входят: их PID
    сняты до начала прогона.
    """
    # Имя процесса — ШАБЛОНОМ: перечень имён, из которых одного нет
    # (`javaw`), уводит PowerShell в код возврата 1 даже при удачном
    # ответе, и замер молча обнулялся бы.
    _rc, out = sb._powershell(
        "Get-Process -Name java* -ErrorAction SilentlyContinue | "
        "ForEach-Object { [pscustomobject]@{ id = $_.Id; "
        "ws = $_.WorkingSet64 } } | ConvertTo-Json -Compress")
    if not out.strip():
        return 0
    try:
        data = json.loads(out.strip())
    except ValueError:
        return 0
    if isinstance(data, dict):
        data = [data]
    return sum(int(r.get("ws") or 0) for r in data
               if int(r.get("id") or 0) not in baseline)


class _Пик:
    """Часовой памяти: опрашивает новые JVM, помнит наибольшее."""

    def __init__(self, baseline: set, period_s: float = 3.0):
        self.baseline = baseline
        self.period_s = period_s
        self.peak = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, _java_working_sets(self.baseline))
            self._stop.wait(self.period_s)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=10)
        return False

    @property
    def гб(self) -> float:
        return round(self.peak / (1024 ** 3), 2)


@pytest.mark.hang_limit(LIMIT_S)
def test_настоящая_сборка_старт_и_стенд_записи(capsys):
    """17. Сборка из коммита, тесты, эфемерный решатель, стенд записи,
    остановка — с числами для §5 и руководства 13.10."""
    if os.environ.get("COSMOS_SLOW_SOLVER_BUILD", "").strip() != "1":
        pytest.skip(
            "медленный прогон: настоящая сборка решателя, его тесты и "
            "запуск JVM идут минутами — включается переменной "
            "COSMOS_SLOW_SOLVER_BUILD=1")
    repo = sb.REPO
    проба = sb.probe(repo)
    if not проба["ok"]:
        pytest.skip("тулчейна для настоящей сборки нет: " + проба["words"])
    java_home = sb.java_home_order(repo, None)[0]

    attempt = repo / "_repro" / "6k-real"
    import shutil
    shutil.rmtree(attempt, ignore_errors=True)
    attempt.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()
    baseline = {r["pid"] for r in sb._processes()}
    печать = []

    with _Пик(baseline) as пик_сборки:
        сборка = sb.build(repo, attempt=attempt, source="commit",
                          commit=head, with_tests=True, java_home=java_home)
    assert сборка["available"] is True, сборка["words"]
    assert сборка["ok"] is True, сборка["words"]
    assert сборка["rc_package"] == 0 and сборка["rc_test"] == 0
    sf = сборка["surefire"]
    assert sf["failures"] == 0 and sf["errors"] == 0, sf
    assert сборка["tests_match_sources"] is True, (
        f"surefire {sf['tests']} против "
        f"{сборка['test_annotations']['branch']} аннотаций @Test")
    assert сборка["artifact"]["unchanged_after_tests"] is True, (
        "артефакт изменился между упаковкой и тестами")
    assert len(сборка["artifact"]["app_jar_sha256"]) == 64
    assert сборка["hs_err"] == []
    for корень in (str(repo), str(attempt)):
        assert корень not in json.dumps(сборка, ensure_ascii=False), (
            "в словаре сборки не должно быть машинных путей")
    печать.append(f"сборка без тестов: {сборка['duration_package_s']} с")
    печать.append(f"тесты решателя:    {сборка['duration_test_s']} с "
                  f"({sf['tests']} тестов, {sf['classes']} классов, "
                  f"пропущено {sf['skipped']})")
    печать.append(f"пик памяти сборки: {пик_сборки.гб} ГБ")
    печать.append(f"снято демонов компилятора: "
                  f"{сборка['compiler_daemons_killed']}")

    qa = attempt / "kotlin-solver" / "target" / "quarkus-app"
    assert (qa / "quarkus-run.jar").exists()
    решатель = sb.EphemeralSolver(qa, cr_key="6k-real",
                                  log_path=attempt / "solver.log",
                                  java_home=java_home,
                                  tag=sb.contour_tag(repo))
    стенд_rc = None
    with _Пик(baseline) as пик_стенда:
        with решатель as url:
            assert url.startswith("http://127.0.0.1:")
            assert решатель.pid_matches_port is True, (
                "порт слушает не наша JVM")
            assert решатель.alive() is True
            from vmp.run.button import SolverHttp
            assert SolverHttp(url, timeout_s=30).health() is True
            печать.append(f"старт решателя:    {решатель.started_in_s} с "
                          f"(попыток {решатель.attempts})")
            начало = time.time()
            стенд_rc, стенд_сек = sb._run_to_file(
                [sb.clone_python(repo), "-m", "vmp.stand_tasks",
                 "--with-solver", url, "--records", RECORD,
                 "--kinds", "kotlin_constraint"],
                cwd=repo, log_path=attempt / "stand.log",
                timeout_s=sb.STAND_TIMEOUT_S)
            печать.append(f"стенд записи:      {стенд_сек} с "
                          f"({RECORD}, код {стенд_rc})")
            assert time.time() - начало < sb.STAND_TIMEOUT_S
    рапорт = решатель.report()
    assert рапорт["stopped"] in ("terminated", "killed"), рапорт
    assert рапорт["port_free_after"] is True, "порт не освободился"
    assert решатель.alive() is False
    печать.append(f"пик памяти стенда: {пик_стенда.гб} ГБ")
    печать.append(f"остановка:         {рапорт['stopped']}, порт свободен: "
                  f"{рапорт['port_free_after']}")
    печать.append(f"артефакт: {сборка['artifact']['app_jar_sha256'][:12]}, "
                  f"дерево {сборка['artifact']['tree_sha256'][:12]}")
    печать.append(f"коммит сборки: {head[:12]}")

    хвост = sb._tail(attempt / "stand.log", 30, roots=[str(repo),
                                                       str(attempt)])
    print("\n=== числа настоящего прогона (§5, 13.10) ===")
    for строка in печать:
        print("  " + строка)
    print("=== хвост стенда ===\n" + хвост)
    assert стенд_rc == 0, ("стенд записи красный — смотрите "
                           "_repro/6k-real/stand.log:\n" + хвост)

    # Артефакт не хранится — хранится рецепт и отпечатки (§1, шаг 10).
    shutil.rmtree(attempt / "kotlin-solver" / "target", ignore_errors=True)
    (attempt / "numbers.json").write_text(
        json.dumps({"build": сборка, "run": рапорт,
                    "stand_rc": стенд_rc,
                    "peak_build_gb": пик_сборки.гб,
                    "peak_stand_gb": пик_стенда.гб},
                   ensure_ascii=False, indent=2), encoding="utf-8")
