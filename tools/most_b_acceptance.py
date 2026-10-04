"""Приёмка этапа Б моста CDPM -> COSMOS: целевые функции на живом решателе.

Веха А сдана и её приёмка (`tools/most_a_acceptance.py`) не расширяется:
предмет сданной вехи задним числом не меняют. Здесь — СВОЙ предмет:
режим `optimization`, выбор целевой, критерий и заявление приближения,
и оба эталона — «Полярис-нано» и «Полярис-мини».

Исходы:

  (а) нано, целевая ростеринга — решение получено, отчёт применённого
      называет целевую, её метрику, исполнителя и вес;
  (б) критерий сложен верно: целевая и штраф метят в одно ограничение,
      эффективный вес = 1 + 3, а слагаемые названы порознь;
  (в) приближение ЗАЯВЛЕНО в отчёте: кредитные часы считаются минутами
      рейсов, потому что свод не несёт правил кредита;
  (г) мини — то же на другом наборе: работает словарь, а не совпадение;
  (д) без явной целевой — отказ (каждая целевая = отдельная задача);
  (е) чужая целевая — отказ с перечнем объявленных;
  (ж) целевая зоны пейринга в задаче ростеринга — отказ, а не похожая
      величина;
  (з) веха А не сломана: feasibility-soft по-прежнему даёт веса пакета
      и НЕ выдумывает целевую;
  (и) смены выгружаются для сертификатора по порогу отдыха ИЗ ОТЧЁТА
      применённого, а не из конфигурации установки.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_b_acceptance
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from webapp import solution_export as se                       # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

REPO = Path(__file__).resolve().parents[1]
JAR = REPO / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"
HANDOFF = REPO.parent / "handoff" / "2026-08-22-MOST-A"
ETALON_NANO = HANDOFF / "etalon-nano" / "run-0027-task-package.zip"
ETALON_MINI = HANDOFF / "etalon-mini" / "mini-task-package.zip"
PORT = 9098
BASE = "http://127.0.0.1:%d" % PORT
BUDGET_S = 30

# Ограничение-исполнитель метрики credit_hours_spread. Вес штрафа НЕ
# пришпиливается числом: он приходит из ответов авиакомпании и законно
# меняется при перевыпуске эталонов — прежняя редакция держала 3 и
# покраснела на новом эталоне, где вес равен 1, не найдя ни одного
# дефекта. Проверяется ОТНОШЕНИЕ: эффективный вес равен весу штрафа
# ИЗ ОТЧЁТА плюс вес целевой. Вес целевой равен 1 по ярусу
# спецификации — это единственное число, которое здесь уместно.
OBJ_CONSTRAINT = "Credit hours spread"
OBJECTIVE_WEIGHT = 1


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
    log = REPO / "tools" / "_most_b_jvm.log"
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


def post_solve(payload: dict, mode: str,
               objective_id: str | None = None) -> tuple[int, dict | str]:
    url = "%s/schedules/single?mode=%s&spentLimitSeconds=%d" % (
        BASE, mode, BUDGET_S)
    if objective_id is not None:
        url += "&objective_id=" + urllib.parse.quote(objective_id)
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


def _payload(pkg: dict) -> dict:
    p = tp.to_payload(pkg)
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _objective_ids(pkg: dict) -> tuple[str, str]:
    """Идентификаторы целевых ростеринга и пейринга эталона."""
    ros = tp.objectives(pkg, "rostering")
    par = tp.objectives(pkg, "pairing")
    return (str(ros[0]["id"]) if ros else ""), (str(par[0]["id"]) if par else "")


def main() -> int:
    if not JAR.exists():
        print("нет сборки решателя: %s" % JAR)
        print("собрать: kotlin-solver\\mvnw.cmd -q -o package -DskipTests")
        return 1
    results: list[tuple[str, bool, str]] = []

    # ------------------------------------------------ нано: (а)-(в), (д)-(з)
    if not ETALON_NANO.exists():
        print("нет эталона нано: %s" % ETALON_NANO)
        return 1
    pkg = tp.load(ETALON_NANO, run_gate=False)
    payload = _payload(pkg)
    ros_id, par_id = _objective_ids(pkg)
    print("нано: рейсов %d, мест %d, людей %d; целевая ростеринга %s"
          % (len(payload["flights"]), len(payload["flightAssignments"]),
             len(payload["employees"]), ros_id))

    proc = start_jvm(str(ETALON_NANO))
    solved = None
    try:
        code, body = post_solve(payload, "optimization", ros_id)
        run = (get_applied().get("lastRun") or {}) if code == 200 else {}
        obj = (run.get("objective") or {}) if run else {}
        ok_a = (code == 200 and obj.get("id") == ros_id
                and obj.get("metric") == "credit_hours_spread"
                and obj.get("constraint") == OBJ_CONSTRAINT
                and int(obj.get("weight") or 0) == OBJECTIVE_WEIGHT)
        if code == 200:
            solved = body
        results.append(("а) нано: целевая исполнена и названа", ok_a,
                        "код %s, целевая %s, метрика %s, исполнитель %s"
                        % (code, obj.get("id"), obj.get("metric"),
                           obj.get("constraint"))))

        eff = (run.get("effectiveWeights") or {}) if run else {}
        applied = (run.get("appliedWeights") or {}) if run else {}
        penalty = applied.get(OBJ_CONSTRAINT)
        ok_b = (isinstance(penalty, int) and penalty > 0
                and int(eff.get(OBJ_CONSTRAINT, -1))
                == OBJECTIVE_WEIGHT + penalty
                and obj.get("sharesConstraintWithPenalty") is True)
        results.append(("б) критерий = целевая + Σ штрафов", ok_b,
                        "эффективный вес %s = целевая %s + штраф %s (штраф из пакета)"
                        % (eff.get(OBJ_CONSTRAINT), OBJECTIVE_WEIGHT, penalty)))

        appr = obj.get("approximation")
        ok_v = bool(appr) and "ПРИБЛИЖ" in str(appr).upper()
        results.append(("в) приближение заявлено в отчёте", ok_v,
                        (str(appr)[:120] + "…") if appr else "не заявлено"))

        code, body = post_solve(payload, "optimization")
        ok_d = code == 400 and "objective_id" in str(body)
        results.append(("д) без явной целевой — отказ", ok_d,
                        "код %s, причина: %s" % (code, str(body)[:120])))

        code, body = post_solve(payload, "optimization", "O-ЧУЖАЯ")
        ok_e = code == 400 and "не объявлена пакетом" in str(body)
        results.append(("е) чужая целевая — отказ", ok_e,
                        "код %s, причина: %s" % (code, str(body)[:120])))

        code, body = post_solve(payload, "optimization", par_id)
        ok_zh = code == 400
        results.append(("ж) целевая пейринга в ростеринге — отказ", ok_zh,
                        "код %s, причина: %s" % (code, str(body)[:120])))

        code, body = post_solve(payload, "feasibility-soft")
        run2 = (get_applied().get("lastRun") or {}) if code == 200 else {}
        soft_w = (run2.get("appliedWeights") or {}).get(OBJ_CONSTRAINT)
        ok_z = (code == 200 and run2.get("mode") == "feasibility-soft"
                and run2.get("objective") is None
                and isinstance(soft_w, int) and soft_w == penalty)
        results.append(("з) веха А цела: мягкий режим без целевой", ok_z,
                        "код %s, целевая %s, вес %s"
                        % (code, run2.get("objective"),
                           (run2.get("appliedWeights") or {})
                           .get(OBJ_CONSTRAINT))))

        # (и) смены для сертификатора — порог ТОЛЬКО из отчёта решателя.
        if solved is not None:
            try:
                rest = se.min_rest_from_applied(get_applied())
                inp = se.certifier_input(payload, solved, rest,
                                         name="нано, целевая " + ros_id)
                ok_i = bool(inp["duties"]) and all(
                    {"id", "employee", "start", "end"} <= set(d)
                    for d in inp["duties"])
                note = ("пороги отдыха дома %s / вне базы %s мин из отчёта, "
                        "назначений %d, смен %d"
                        % (rest["home"], rest["outstation"],
                           len(inp["assignments"]), len(inp["duties"])))
            except se.SolutionExportError as e:
                ok_i, note = False, "отказ выгрузки: %s" % e
        else:
            ok_i, note = False, "решения нет — сегментировать нечего"
        results.append(("и) смены выгружены для сертификатора", ok_i, note))
    finally:
        stop_jvm(proc)

    # ------------------------------------------------------------ (г) мини
    if ETALON_MINI.exists():
        pkg_m = tp.load(ETALON_MINI, run_gate=False)
        payload_m = _payload(pkg_m)
        ros_m, _ = _objective_ids(pkg_m)
        print("мини: рейсов %d, мест %d, людей %d; целевая ростеринга %s"
              % (len(payload_m["flights"]),
                 len(payload_m["flightAssignments"]),
                 len(payload_m["employees"]), ros_m))
        proc = start_jvm(str(ETALON_MINI))
        try:
            code, body = post_solve(payload_m, "optimization", ros_m)
            run = (get_applied().get("lastRun") or {}) if code == 200 else {}
            obj = (run.get("objective") or {}) if run else {}
            eff = (run.get("effectiveWeights") or {}) if run else {}
            applied_m = (run.get("appliedWeights") or {}) if run else {}
            pen_m = applied_m.get(OBJ_CONSTRAINT)
            ok_g = (code == 200 and obj.get("metric") == "credit_hours_spread"
                    and isinstance(pen_m, int) and pen_m > 0
                    and int(eff.get(OBJ_CONSTRAINT, -1))
                    == OBJECTIVE_WEIGHT + pen_m)
            results.append(("г) мини: та же метрика, тот же критерий", ok_g,
                            "код %s, целевая %s, эффективный вес %s"
                            % (code, obj.get("id"), eff.get(OBJ_CONSTRAINT))))
        finally:
            stop_jvm(proc)
    else:
        results.append(("г) мини", False, "эталон не найден: %s" % ETALON_MINI))

    print("")
    print("=" * 72)
    for name, ok, note in results:
        print("[%s] %-44s %s" % ("ГОДЕН " if ok else "ОТКАЗ ", name, note))
    bad = [r for r in results if not r[1]]
    print("=" * 72)
    print("исходов проверено: %d, незелёных: %d" % (len(results), len(bad)))
    if bad:
        print("ВЕРДИКТ: приёмка этапа Б НЕ пройдена")
        return 1
    print("ВЕРДИКТ: приёмка этапа Б пройдена")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
