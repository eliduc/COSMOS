"""Этап Г, Ш-2: живой дым прогонов по идентификатору (Г-S1).

Зелёный путь: JVM БЕЗ процессного пакета; POST /schedules/run с пакетом
нано БАЙТАМИ (base64) → runId → статус до «готов» → результат → отчёт
применённого этого прогона → повкладовый счёт под пакетом прогона.

Красный набор (по браку на гейт, §6 постановки):
  - мусор вместо пакета → 400 словами разбора;
  - неизвестный runId → 404;
  - результат до готовности → 400 словами состояния;
  - двусмысленность: JVM С env-пакетом + пакет запросом → 400 словами
    двусмысленности (не контрольной суммы).

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_g_run_smoke
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

NANO = mv.HANDOFF.parent / "2026-08-24-REISSUE-LOGIC" / "nano-task-package.zip"


def _post(path: str, payload, timeout: int = 120):
    req = urllib.request.Request(
        mv.BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _get(path: str, timeout: int = 30):
    try:
        with urllib.request.urlopen(mv.BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def start_jvm_no_package():
    import os
    import subprocess
    env = dict(os.environ)
    env.pop("CREW_TASK_PACKAGE", None)
    log = mv.REPO / "tools" / "_most_v_jvm.log"
    proc = subprocess.Popen(
        [mv._java(), "-Xms512m", "-Xmx2g", "-XX:+UseG1GC",
         "-Dquarkus.http.port=%d" % mv.PORT,
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(mv.JAR)],
        stdin=subprocess.DEVNULL, stdout=log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT, env=env, cwd=str(mv.JAR.parent.parent))
    for _ in range(120):
        try:
            urllib.request.urlopen(mv.BASE + "/schedules/applied-rules", timeout=2).read()
            return proc
        except Exception:                                       # noqa: BLE001
            if proc.poll() is not None:
                raise SystemExit("JVM умерла при старте; журнал: %s" % log)
            time.sleep(1)
    proc.kill()
    raise SystemExit("JVM не поднялась за 120 с")


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    pkg = tp.load(NANO, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    zip_b64 = base64.b64encode(NANO.read_bytes()).decode("ascii")
    body = {"problem": payload, "packageZipBase64": zip_b64}
    results: list[tuple[str, bool, str]] = []

    def outcome(name, ok, detail):
        results.append((name, ok, detail))
        print("[%s] %-46s %s" % ("ГОДЕН " if ok else "ДЕФЕКТ", name, detail))

    proc = start_jvm_no_package()
    try:
        # 1. Зелёный путь.
        code, text = _post("/schedules/run?mode=feasibility-soft&spentLimitSeconds=5", body)
        run = json.loads(text) if code == 200 else {}
        run_id = run.get("runId") or ""
        outcome("1) старт прогона: runId и отпечаток", code == 200 and bool(run_id)
                and bool(run.get("packageFingerprint")),
                "HTTP %d, runId=%s..." % (code, run_id[:8]))
        state = ""
        for _ in range(120):
            code, text = _get("/schedules/run/%s/status" % run_id)
            state = json.loads(text).get("state") if code == 200 else "?"
            if state != "считает":
                break
            time.sleep(1)
        outcome("2) статус дошёл до «готов»", state == "готов", "состояние: %s" % state)
        code, text = _get("/schedules/run/%s/result" % run_id, timeout=60)
        solved = json.loads(text) if code == 200 else {}
        outcome("3) результат по runId", code == 200 and bool(solved.get("flightAssignments")),
                "HTTP %d, score=%s" % (code, solved.get("score")))
        code, text = _get("/schedules/run/%s/applied-rules" % run_id)
        rep = json.loads(text) if code == 200 else {}
        last = rep.get("lastRun") or {}
        outcome("4) отчёт применённого ЭТОГО прогона",
                code == 200 and rep.get("runId") == run_id
                and bool(last.get("profileReadings")) and "logic" in last,
                "HTTP %d, показаний %d" % (code, len(last.get("profileReadings") or {})))
        code, text = _post("/schedules/run/%s/score-breakdown" % run_id, solved, timeout=300)
        bd = json.loads(text) if code == 200 else {}
        outcome("5) повкладовый счёт под пакетом прогона",
                code == 200 and bd.get("packageFingerprint") == run.get("packageFingerprint"),
                "HTTP %d, hard=%s" % (code, bd.get("hard")))
        # 2. Красные.
        code, text = _post("/schedules/run?mode=feasibility-soft",
                           {"problem": payload, "packageZipBase64": base64.b64encode(b"garbage").decode()})
        outcome("6) мусор вместо пакета — отказ разбора", code == 400 and "zip" in text,
                "HTTP %d" % code)
        code, text = _get("/schedules/run/no-such-run/status")
        outcome("7) неизвестный runId — 404", code == 404, "HTTP %d" % code)
        code, text = _post("/schedules/run?mode=feasibility-soft&spentLimitSeconds=30", body)
        rid2 = json.loads(text).get("runId") if code == 200 else ""
        code, text = _get("/schedules/run/%s/result" % rid2)
        outcome("8) результат до готовности — отказ словами состояния",
                code == 400 and "не готов" in text, "HTTP %d" % code)
    finally:
        mv.stop_jvm(proc)

    # 3. Двусмысленность: JVM С процессным пакетом + пакет запросом.
    proc = mv.start_jvm(str(NANO))
    try:
        code, text = _post("/schedules/run?mode=feasibility-soft", body)
        outcome("9) env+запрос — отказ словами двусмысленности",
                code == 400 and "двусмысленность" in text, "HTTP %d: %s" % (code, text[:120]))
    finally:
        mv.stop_jvm(proc)

    bad = sum(1 for _, ok, _ in results if not ok)
    print("исходов проверено: %d, незелёных: %d" % (len(results), bad))
    print("итог: %s" % ("сходится" if bad == 0 else "ДЕФЕКТОВ %d" % bad))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
