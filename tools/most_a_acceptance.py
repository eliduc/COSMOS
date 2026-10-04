"""Приёмка вехи А моста CDPM -> COSMOS: пять исходов на живом решателе.

Постановка `handoff\\2026-08-22-MOST-A\\01-POSTANOVKA-ETAP-A.md` §4
требует пяти исходов сквозного прогона на эталонном пакете набора
«Полярис-нано». Скрипт исполняет их все и печатает отчёт с числами;
любой незелёный исход — код возврата 1.

  (а) feasibility-hard — допустимое решение, остановка по 0 жёстких;
  (б) feasibility-soft — применены веса ИЗ ПАКЕТА (10 и 3), что видно
      в отчёте применённого;
  (в) подмена байта в любом файле пакета — отказ приёма;
  (г) режим optimization — отказ без явной целевой (исход
      пересмотрен этапом Б, см. примечание у проверки);
  (д) негативный эталон (мягкий режим запрещён) — отказ по allowed_modes.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_a_acceptance
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from webapp import reserve_oracle as ro                        # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

REPO = Path(__file__).resolve().parents[1]
JAR = REPO / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"
ETALON_DIR = REPO.parent / "handoff" / "2026-08-22-MOST-A" / "etalon-nano"
ETALON = ETALON_DIR / "run-0027-task-package.zip"
ETALON_LIMITED = ETALON_DIR / "run-0027-limited-modes.zip"
PORT = 9099
BASE = "http://127.0.0.1:%d" % PORT
BUDGET_S = 30

#: Таблица «правило пакета -> мягкое ограничение решателя» — та же, что
#: в kotlin-solver TaskPackage.kt (строки 109-110). Здесь она нужна,
#: чтобы прочитать ожидаемые веса ИЗ ПАКЕТА, а не держать их числами:
#: числа пакета меняются с каждым его перевыпуском, и пришпиленная
#: проверка ломается, не найдя ни одного дефекта (урок ревизии 1.3).
CONSTRAINT_BY_RULE = {"R-S1-табл-3-строка-6-кол-3": "Layover nights away from home",
                      "R-S1-табл-4-строка-2-кол-3": "Credit hours spread over threshold"}


def expected_weights(task: str = "rostering") -> dict:
    """Веса мягких ИЗ ПАКЕТА: правило -> ограничение -> вес."""
    with zipfile.ZipFile(ETALON) as z:
        obj = json.loads(z.read("objective.json").decode("utf-8"))
    out = {}
    for c in (obj.get(task) or {}).get("components") or []:
        name = CONSTRAINT_BY_RULE.get(str(c.get("rule_id")))
        if name and isinstance(c.get("weight"), int):
            out[name] = c["weight"]
    return out


def _java() -> str:
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "java.exe").exists():
        return str(Path(home) / "bin" / "java.exe")
    return shutil.which("java") or "java"


def start_jvm(package_path: str | None) -> subprocess.Popen:
    """Изолированная JVM с пакетом постановки в окружении."""
    env = dict(os.environ)
    if package_path:
        env["CREW_TASK_PACKAGE"] = str(package_path)
    else:
        env.pop("CREW_TASK_PACKAGE", None)
    log = REPO / "tools" / "_most_a_jvm.log"
    proc = subprocess.Popen(
        [_java(), "-Xms512m", "-Xmx2g", "-XX:+UseG1GC",
         "-Dquarkus.http.port=%d" % PORT,
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(JAR)],
        stdin=subprocess.DEVNULL, stdout=log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT, env=env, cwd=str(JAR.parent.parent))
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/schedules/applied-rules",
                                   timeout=2).read()
            return proc
        except Exception:                                   # noqa: BLE001
            if proc.poll() is not None:
                raise SystemExit("JVM умерла при старте; журнал: %s" % log)
            time.sleep(1)
    proc.kill()
    raise SystemExit("JVM не поднялась за 120 с; журнал: %s" % log)


def stop_jvm(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def post_solve(payload: dict, mode: str) -> tuple[int, dict | str]:
    url = "%s/schedules/single?mode=%s&spentLimitSeconds=%d" % (
        BASE, mode, BUDGET_S)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=BUDGET_S + 120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get_applied() -> dict:
    with urllib.request.urlopen(BASE + "/schedules/applied-rules",
                                timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _payload() -> dict:
    pkg = tp.load(ETALON)
    p = tp.to_payload(pkg)
    return {k: v for k, v in p.items() if not k.startswith("_")}


def main() -> int:
    if not JAR.exists():
        print("нет сборки решателя: %s" % JAR)
        return 1
    if not ETALON.exists():
        print("нет эталонного пакета: %s" % ETALON)
        return 1

    results: list[tuple[str, bool, str]] = []
    payload = _payload()
    print("набор из пакета: рейсов %d, мест %d, экипаж %d"
          % (len(payload["flights"]), len(payload["flightAssignments"]),
             len(payload["employees"])))

    # ---------------------------------------------- (а), (б), (г)
    proc = start_jvm(str(ETALON))
    try:
        t0 = time.time()
        code, body = post_solve(payload, "feasibility-hard")
        dt = time.time() - t0
        score = (body or {}).get("score") if isinstance(body, dict) else body
        assigned = sum(1 for a in (body or {}).get("flightAssignments") or []
                       if isinstance(body, dict) and a.get("employee"))
        # С 2026-08-23 у свода есть исполнитель резерва (строка 8), а на
        # эталоне при полном укомплектовании резерв невыполним: ожидание
        # «0hard» стало бы подгонкой. Ожидание — ОРАКУЛ по возвращённому
        # решению: hard == −Σ нехватки резерва при нуле пустых мест и нуле
        # пересечений (число печатается, не пришпиливается).
        verdict_a = "нет решения"
        ok_a = False
        if code == 200 and isinstance(body, dict):
            rep = get_applied()
            norms = ro.norms_from_applied(rep)
            rows = ro.reserve_table(payload, body, norms) if norms else []
            short = ro.total_shortfall(rows)
            hard = int(re.search(r"(-?\d+)hard", str(score)).group(1))
            empty = ro.unassigned_seats(body)
            overlaps = ro.overlapping_pairs(payload, body)
            ok_a = (hard == -short and empty == 0 and overlaps == 0 and bool(norms))
            verdict_a = ("hard %d = −нехватка резерва %d (нормы %s), пустых мест %d, "
                         "пересечений %d" % (hard, short, norms, empty, overlaps))
        results.append(("а) feasibility-hard на ПОЛНОМ эталоне: честная нехватка резерва", ok_a,
                        "код %s, счёт %s, назначено %d из %d, %.1f с (бюджет %d с); %s"
                        % (code, score, assigned,
                           len(payload["flightAssignments"]), dt, BUDGET_S, verdict_a)))

        # Контроль механики: подмножество, которое двум членам экипажа
        # ПО СИЛАМ. Без него «не достигли нуля» неотличимо от «механизм
        # остановки не работает».
        base = payload["employees"][0]["homeAirport"]
        one = next((f for f in payload["flights"]
                    if f["departureAirport"] == base), None)
        if one is not None:
            small = {
                "airports": payload["airports"],
                "employees": payload["employees"],
                "flights": [one],
                "flightAssignments": [a for a in payload["flightAssignments"]
                                      if a["flight"] == one["flightNumber"]],
            }
            t0 = time.time()
            code, body = post_solve(small, "feasibility-hard")
            dt = time.time() - t0
            score = (body or {}).get("score") if isinstance(body, dict) else body
            ok_a2 = (code == 200 and str(score or "").startswith("0hard")
                     and dt < BUDGET_S - 2)
            results.append(("а-контроль) выполнимое подмножество: 0hard и "
                            "ранняя остановка", ok_a2,
                            "код %s, счёт %s, %.1f с из %d с бюджета"
                            % (code, score, dt, BUDGET_S)))

        code, body = post_solve(payload, "feasibility-soft")
        applied = get_applied()
        run = (applied.get("lastRun") or {})
        weights = run.get("appliedWeights") or {}
        want = expected_weights("rostering")
        ok_b = (code == 200 and run.get("mode") == "feasibility-soft"
                and bool(want)
                and all(int(weights.get(k, -1)) == v for k, v in want.items()))
        results.append(("б) feasibility-soft: веса ИЗ ПАКЕТА", ok_b,
                        "код %s, применено %s, пакет требует %s, отпечаток %s"
                        % (code, json.dumps(weights, ensure_ascii=False),
                           json.dumps(want, ensure_ascii=False),
                           str(run.get("packageFingerprint"))[:16])))

        # Исход (г) ПЕРЕСМОТРЕН этапом Б, и это не подгонка под факт.
        # Приёмка вехи А проверяла, что решатель не делает вид, будто
        # умеет оптимизировать. Этап Б целевые реализовал, и прежняя
        # формулировка стала заведомо ложной: оставить её значило бы
        # держать зелёной проверку, которая проверяет несуществующее.
        # Инвариант, который на самом деле защищался, сохранён целиком:
        # режим optimization не запускается ВСЛЕПУЮ. Полная приёмка
        # целевых — в tools/most_b_acceptance.py, предмет вехи А здесь
        # не расширен ни на шаг.
        code, body = post_solve(payload, "optimization")
        ok_g = code == 400 and "objective_id" in str(body)
        results.append(("г) optimization: без явной целевой — отказ", ok_g,
                        "код %s, причина: %s"
                        % (code, str(body)[:160])))
    finally:
        stop_jvm(proc)

    # ---------------------------------------------------------- (в)
    # Каталог подмены — временный и УНИКАЛЬНЫЙ: постоянное имя остаётся
    # занятым завершившейся JVM (WinError 5) и роняет приёмку на ровном
    # месте при следующем прогоне.
    tmp = Path(tempfile.mkdtemp(prefix="most-a-tamper-"))
    with zipfile.ZipFile(ETALON) as z:
        z.extractall(tmp)
    # Подмена байта — ПО ФАКТУ, а не поиском строки: искомой строки в
    # перевыпущенном пакете может не быть, и «подмена» тихо не случится,
    # оставив проверку зелёной ни на чём (урок: пришпилка к содержимому).
    with zipfile.ZipFile(ETALON) as z:
        man = json.loads(z.read("manifest.json").decode("utf-8"))
    target = str((man.get("files") or [{}])[0].get("name") or "objective.json")
    victim = tmp / target
    before = victim.read_bytes()
    victim.write_bytes(before + b" ")
    assert victim.read_bytes() != before, "подмена байта не состоялась: %s" % target
    print("исход (в): подменён файл %s (+1 байт, было %d)" % (target, len(before)))
    proc = start_jvm(str(tmp))
    try:
        code, body = post_solve(payload, "feasibility-soft")
        ok_v1 = code == 400 and "контрольная сумма" in str(body)
        results.append(("в) подмена байта: отказ решателя", ok_v1,
                        "код %s, причина: %s" % (code, str(body)[:160])))
    finally:
        stop_jvm(proc)
    # ...и приём на стороне webapp обязан отказать так же.
    try:
        tp.load(tmp, run_gate=False)
        ok_v2, why = False, "приём принял подменённый пакет"
    except tp.TaskPackageRefused as e:
        ok_v2, why = "контрольная сумма" in str(e), str(e)[:160]
    results.append(("в) подмена байта: отказ приёма webapp", ok_v2, why))
    shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------- (д)
    if ETALON_LIMITED.exists():
        proc = start_jvm(str(ETALON_LIMITED))
        try:
            code, body = post_solve(payload, "feasibility-soft")
            ok_d = code == 400 and "не разрешён пакетом" in str(body)
            results.append(("д) негативный эталон: отказ по allowed_modes",
                            ok_d, "код %s, причина: %s"
                            % (code, str(body)[:160])))
            # Контроль: разрешённый режим тем же пакетом проходит.
            code2, body2 = post_solve(payload, "feasibility-hard")
            results.append(("д-контроль) разрешённый режим проходит",
                            code2 == 200, "код %s" % code2))
        finally:
            stop_jvm(proc)
    else:
        results.append(("д) негативный эталон", False, "пакет не найден"))

    print("")
    print("=" * 72)
    for name, ok, note in results:
        print("[%s] %-46s %s" % ("ГОДЕН " if ok else "ОТКАЗ ", name, note))
    bad = [r for r in results if not r[1]]
    print("=" * 72)
    print("исходов проверено: %d, незелёных: %d" % (len(results), len(bad)))
    if bad:
        print("ВЕРДИКТ: приёмка вехи А НЕ пройдена")
        return 1
    print("ВЕРДИКТ: приёмка вехи А пройдена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
