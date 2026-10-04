"""Сравнение порогов пакета постановки с порогами живого решателя.

Мост CDPM -> COSMOS, этап В, пункт (б). Печатает трёхзначный вердикт по
каждому машинному параметру пакета: совпадает / расходится / несравнимо.

Числа НАШЕЙ стороны берутся у решателя, а не из копии в коде: инструмент
поднимает изолированную JVM с пакетом, делает короткий прогон (чтобы
отчёт применённого знал, КАКИМ профилем считали) и читает
`lastRun.profileReadings`. Копия порога в python была бы вторым
источником правды и разошлась бы с первым молча — на этом уже обжигались
(ревизия 2 отчёта приёмки этапа Б).

Код возврата: 0 — сравнение выполнено и расхождений нет, 1 — выполнить
не удалось либо ЕСТЬ РАСХОЖДЕНИЯ. С переносом порогов (пункт «г»)
расхождение перестало быть фактом для сведения и стало поломкой:
значит пороги пакета до прогона не доехали. «Несравнимо» красным не
считается — это объявленная граница, а не сбой.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_v_thresholds
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from webapp import task_package as tp                          # noqa: E402
from webapp import threshold_gate as tg                        # noqa: E402

force_utf8_console()

REPO = Path(__file__).resolve().parents[1]
JAR = REPO / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"
HANDOFF = REPO.parent / "handoff" / "2026-08-22-MOST-A"
ETALONS = (
    ("нано", HANDOFF / "etalon-nano" / "run-0027-task-package.zip"),
    ("мини", HANDOFF / "etalon-mini" / "mini-task-package.zip"),
)
PORT = 9097
BASE = "http://127.0.0.1:%d" % PORT
BUDGET_S = 10


def _java() -> str:
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "java.exe").exists():
        return str(Path(home) / "bin" / "java.exe")
    return shutil.which("java") or "java"


def start_jvm(package_path: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["CREW_TASK_PACKAGE"] = str(package_path)
    log = REPO / "tools" / "_most_v_jvm.log"
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


def solve(payload: dict) -> int:
    """Короткий прогон: он нужен только чтобы отчёт знал профиль."""
    url = "%s/schedules/single?mode=feasibility-soft&spentLimitSeconds=%d" % (
        BASE, BUDGET_S)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=BUDGET_S + 120) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def applied() -> dict:
    with urllib.request.urlopen(BASE + "/schedules/applied-rules",
                                timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    if not JAR.exists():
        print("нет сборки решателя: %s" % JAR)
        return 1
    total = {tg.MATCH: 0, tg.DIVERGE: 0, tg.INCOMPARABLE: 0}
    executed_incomparable: set = set()
    for name, path in ETALONS:
        if not path.exists():
            print("нет эталона %s: %s" % (name, path))
            return 1
        pkg = tp.load(path, run_gate=False)
        payload = {k: v for k, v in tp.to_payload(pkg).items()
                   if not k.startswith("_")}
        proc = start_jvm(str(path))
        try:
            code = solve(payload)
            rep = applied()
        finally:
            stop_jvm(proc)
        run = (rep.get("lastRun") or {})
        profile = run.get("rulesProfile")
        readings = run.get("profileReadings") or {}
        thr = run.get("thresholds") or {}
        print("=" * 72)
        print("ЭТАЛОН %s: пакет %s, прогон %s, профиль %s"
              % (name.upper(), pkg["manifest"].get("source_run"), code, profile))
        if not readings:
            print("  ОТКАЗ: решатель не сообщил показания профиля — "
                  "сравнивать не с чем")
            return 1
        # Соответствие и перечень неприменённого — ИЗ ОТЧЁТА решателя.
        # Своей копии таблицы у гейта нет: она была бы вторым источником
        # правды, а показания — фактические, от созданного провайдера, а
        # не от намерения резолвера (ревизия 1 сравнивала пакет с собой).
        for a in (thr.get("applied") or []):
            for d in (a.get("declarations") or []):
                print("  заявление %s: %s" % (a.get("threshold"), d))
        # Литералы В СИЛЕ — ручки, о которых пакет молчит: число, по
        # которому судили, не бывает невидимым.
        for lit in (thr.get("literalsInForce") or []):
            print("  в силе литерал %s = %s (%s)"
                  % (lit.get("threshold"), lit.get("value"), lit.get("note")))
        verdicts = tg.compare(pkg, readings, profile=profile,
                              mapping=thr.get("paramToThreshold"),
                              ignored=thr.get("ignored"),
                              applied=thr.get("applied"),
                              qualified=thr.get("qualifiedParamToThreshold"),
                              off_literals=thr.get("offLiterals"),
                              code_readings=run.get("profileCodeReadings"),
                              policy_readings=run.get("profilePolicyReadings"))
        print(tg.report(verdicts, tg.excluded_params(pkg),
                        considered=len(tg.collect_params(pkg))))
        executed_incomparable.update(tg.unjudged_claimed_params(verdicts, thr.get("applied")))
        for v in verdicts:
            total[v["verdict"]] = total.get(v["verdict"], 0) + 1
            if v["verdict"] == tg.INCOMPARABLE:
                for n in v["params"]:
                    if n in ("crew_size", "reserve_crews"):
                        executed_incomparable.add(n)
    print("=" * 72)
    print("ИТОГО по обоим эталонам: %s"
          % ", ".join("%s: %d" % (k, n) for k, n in sorted(total.items())))
    if total.get(tg.DIVERGE):
        print("ВЕРДИКТ: расхождения есть — пороги пакета не доехали до прогона")
        return 1
    # Строки 7 и 8 свода получили исполнителей: «несравнимо» по их именам
    # больше не граница, а дефект — красим.
    if executed_incomparable:
        print("ВЕРДИКТ: «несравнимо» по именам с исполнителем: %s"
              % ", ".join(sorted(executed_incomparable)))
        return 1
    print("Пороги пакета ПРИМЕНЯЮТСЯ (этап В, пункт «г»): расхождение "
          "означает, что перенос не сработал, и это повод разбираться.")
    print("«Несравнимо» — не расхождение: одна ручка решателя против "
          "нескольких требований либо встречной величины нет вовсе; "
          "сведение объявлено в отчёте применённого.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
