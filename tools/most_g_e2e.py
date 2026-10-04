"""Этап Г, Г-S4 — сквозная приёмка кнопки, половина решателя (каркас).

Одна команда проходит цепочку швов решателя и сборки входа сертификатора:

  пакет → /schedules/run (Ш-2, по идентификатору) → статус (Ш-2а) →
  результат → applied-rules прогона → certify_input.build (Ш-3, гейт
  показаний) → [сертификация CDPM, если задан адрес] → выдача (Ш-4).

Сценарии (§1 и §6 постановки):
  - Г-1 зелёный путь: нано до нулевых жёстких и собранного входа
    сертификатора с трассировкой (runId + отпечаток пакета);
  - Г-3 недопустимость: негативный эталон (резерв структурно невыполним)
    даёт жёсткие нарушения — выдача обязана нести «недопустимо», не успех;
  - Ш-2 красный: подмена байта пакета → 400 словами контрольной суммы;
  - Ш-3 красный: битые показания → вход сертификатора НЕ строится
    (CertifyInputError словами гейта).

Шов сертификации через панель CDPM (POST /api/runs/{id}/certify,
контракт 2026-08-24) СНЯТ 04.09.2026 решением владельца: работа CDPM
заканчивается пакетом ВМП, сертификат решения выпускает нажатие COSMOS
«Пакет постановки» (vmp/run/roster_certify через webapp/package_runs).
Переменные COSMOS_CDPM_BASE / COSMOS_CDPM_RUN_ID_* больше ничего не
включают: шов помечается «не прогнан» словами о переезде (имитаций нет). Порт решателя — 9098: инструмент не
трогает бегущие прогоны на штатном 9097 и НЕ пересобирает jar.

Запуск (из каталога crew-demo):
    .venv\\Scripts\\python.exe -m tools.most_g_e2e
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _cli_console import force_utf8_console                    # noqa: E402
from tools import most_v_thresholds as mv                      # noqa: E402
from webapp import certify_input as ci                         # noqa: E402
from webapp import task_package as tp                          # noqa: E402

force_utf8_console()

PORT = 9098
BASE = "http://127.0.0.1:%d" % PORT
GREEN_PKG = mv.HANDOFF.parent / "2026-08-24-REISSUE-LOGIC" / "nano-task-package.zip"
NEG_PKG = mv.HANDOFF / "etalon-nano" / "run-0027-infeasible-reserve.zip"
CDPM_BASE = os.environ.get("COSMOS_CDPM_BASE", "").strip().rstrip("/")
CDPM_RUN_GREEN = os.environ.get("COSMOS_CDPM_RUN_ID_GREEN", "").strip()
CDPM_RUN_NEG = os.environ.get("COSMOS_CDPM_RUN_ID_NEG", "").strip()


def _post(path: str, payload, timeout: int = 120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _get(path: str, timeout: int = 30):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def start_jvm() -> subprocess.Popen:
    """Второй экземпляр решателя с УЖЕ собранного jar (без пересборки)."""
    env = dict(os.environ)
    env.pop("CREW_TASK_PACKAGE", None)
    log = mv.REPO / "tools" / "_most_g_e2e_jvm.log"
    extra = []
    mt = os.environ.get("CREW_MOVE_THREADS", "").strip()
    if mt:
        extra.append("-Dcosmos.move.threads=%s" % mt)
    proc = subprocess.Popen(
        [mv._java(), "-Xms512m", "-Xmx4g", "-XX:+UseG1GC", *extra,
         "-Dquarkus.http.port=%d" % PORT,
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(mv.JAR)],
        stdin=subprocess.DEVNULL, stdout=log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT, env=env, cwd=str(mv.JAR.parent.parent))
    for _ in range(120):
        try:
            urllib.request.urlopen(BASE + "/schedules/applied-rules", timeout=2).read()
            return proc
        except Exception:                                       # noqa: BLE001
            if proc.poll() is not None:
                raise SystemExit("JVM умерла при старте; журнал: %s" % log)
            time.sleep(1)
    proc.kill()
    raise SystemExit("JVM не поднялась за 120 с")


def run_package(pkg_path: Path, budget: int) -> tuple[dict, dict, dict, dict]:
    """Пакет через /schedules/run до конца: (пакет, набор, решение, отчёт)."""
    pkg = tp.load(pkg_path, run_gate=False)
    payload = {k: v for k, v in tp.to_payload(pkg).items() if not k.startswith("_")}
    body = {"problem": payload,
            "packageZipBase64": base64.b64encode(pkg_path.read_bytes()).decode("ascii")}
    code, text = _post("/schedules/run?mode=feasibility-soft&spentLimitSeconds=%d" % budget,
                       body, timeout=60)
    if code != 200:
        raise SystemExit("старт прогона: HTTP %d %s" % (code, text[:300]))
    run_id = json.loads(text)["runId"]
    state = "считает"
    for _ in range(budget * 4 + 120):
        code, text = _get("/schedules/run/%s/status" % run_id)
        state = json.loads(text).get("state") if code == 200 else "?"
        if state != "считает":
            break
        time.sleep(1)
    if state != "готов":
        code, text = _get("/schedules/run/%s/status" % run_id)
        raise SystemExit("прогон %s: состояние «%s» — %s" % (run_id, state, text[:300]))
    _, text = _get("/schedules/run/%s/result" % run_id, timeout=120)
    solved = json.loads(text)
    _, text = _get("/schedules/run/%s/applied-rules" % run_id)
    report = json.loads(text)
    report["runId"] = run_id
    return pkg, payload, solved, report


def hard_of(score: str | None) -> int:
    return int(str(score or "0hard").split("hard")[0])


def certify(inp: dict, cdpm_run_id: str,
            run_info: dict | None = None) -> tuple[str, dict | None]:
    """Шов сертификации по контракту CDPM: multipart → ожидание → машинный
    certificate.json. Не настроен — честное «не прогнан», без имитаций.
    run_info (контракт 2026-08-24): ключ runInfo внутрь JSON решения —
    score (обязателен для certificate.solver_hard) и трассировка."""
    # Решение владельца 04.09.2026: сертификация — у COSMOS; ручки
    # панели CDPM /certify нет. Заданные переменные — не ошибка,
    # а устаревшая настройка: шов честно «не прогнан».
    del inp, run_info
    if CDPM_BASE or cdpm_run_id:
        return ("не прогнан: шов сертификации через панель CDPM снят "
                "04.09.2026 — сертификат выпускает нажатие COSMOS "
                "«Пакет постановки»"), None
    return "не прогнан: COSMOS_CDPM_BASE/RUN_ID не заданы", None


def main() -> int:
    if not mv.JAR.exists():
        print("нет сборки решателя: %s" % mv.JAR)
        return 1
    out_dir = Path(tempfile.mkdtemp(prefix="most-g-e2e-"))
    results: list[tuple[str, bool, str]] = []

    def outcome(name, ok, detail):
        results.append((name, ok, detail))
        print("[%s] %-52s %s" % ("ГОДЕН " if ok else "ДЕФЕКТ", name, detail))

    proc = start_jvm()
    try:
        # --- Г-1: зелёный путь на нано --------------------------------
        pkg, payload, solved, report = run_package(GREEN_PKG, budget=10)
        hard = hard_of(solved.get("score"))
        outcome("Г-1а) нано решён по идентификатору", hard == 0,
                "score=%s" % solved.get("score"))
        inp, coverage = ci.build(pkg, payload, solved, report, name="Г-S4 зелёный путь (нано)")
        head = ci.head(pkg, payload, solved, report, inp, coverage)
        trace_ok = bool(report.get("runId")) and bool(head.get("package_fingerprint"))
        outcome("Г-1б) вход сертификатора собран с трассировкой", trace_ok,
                "runId=%s..., отпечаток=%s..." % (
                    str(report.get("runId"))[:8], str(head.get("package_fingerprint"))[:12]))
        (out_dir / "green-certifier-input.json").write_text(
            json.dumps(inp, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "green-head.json").write_text(
            json.dumps(head, ensure_ascii=False, indent=2), encoding="utf-8")
        state, cert = certify(inp, CDPM_RUN_GREEN, run_info={
            "score": solved.get("score"),
            "package_fingerprint": head.get("package_fingerprint"),
            "source_run": head.get("source_run"),
            "mode": "feasibility-soft"})
        if cert is None:
            outcome("Г-1в) сертификация CDPM", True, "шов " + state)
        else:
            certified = (cert.get("certificate") or {}).get("certified")
            outcome("Г-1в) сертификация CDPM: certified", certified is True,
                    "certified=%s" % certified)

        # --- Г-3: недопустимость на негативном эталоне ----------------
        pkg2, payload2, solved2, report2 = run_package(NEG_PKG, budget=10)
        hard2 = hard_of(solved2.get("score"))
        outcome("Г-3а) негативный эталон даёт жёсткие нарушения", hard2 < 0,
                "score=%s" % solved2.get("score"))
        inp2, cov2 = ci.build(pkg2, payload2, solved2, report2, name="Г-S4 негативный (нано)")
        verdict = "недопустимо" if hard2 < 0 else "допустимо"
        outcome("Г-3б) выдача несёт вердикт, не «успех»", verdict == "недопустимо",
                "verdict=%s (по счёту решателя)" % verdict)
        head2 = ci.head(pkg2, payload2, solved2, report2, inp2, cov2)
        state2, cert2 = certify(inp2, CDPM_RUN_NEG, run_info={
            "score": solved2.get("score"),
            "package_fingerprint": head2.get("package_fingerprint"),
            "source_run": head2.get("source_run"),
            "mode": "feasibility-soft"})
        if cert2 is None:
            outcome("Г-3в) сертификация негативного", True, "шов " + state2)
        elif cert2.get("refused"):
            # Отказ сертификации (например, «универсум пуст» на однодневном
            # нано) — годный красный исход: недопустимое НЕ сертифицировано,
            # причина машинно читаема. Пустая причина — дефект.
            outcome("Г-3в) сертификация негативного: отказ с причиной",
                    bool(cert2.get("certify_error")),
                    "отказ: %s" % (cert2.get("certify_error") or "БЕЗ ПРИЧИНЫ")[:80])
        else:
            certified2 = (cert2.get("certificate") or {}).get("certified")
            outcome("Г-3в) сертификация негативного: certified=False", certified2 is False,
                    "certified=%s" % certified2)
            # Семантика тела (контракт CDPM 2026-08-24): на однодневных
            # данных проверка выходных не прогоняется, а честно помечается.
            chk = next((c for c in (cert2.get("checks") or [])
                        if c.get("template") == "выходных-в-месяце"), None)
            ok_body = (chk is not None
                       and str(chk.get("status")) == "не прогнана"
                       and "универсум пуст" in str(chk.get("reason") or ""))
            outcome("Г-3г) тело: выходные «не прогнана: универсум пуст»", ok_body,
                    "нет такой проверки в checks[]" if chk is None
                    else "status=%s, reason=%s" % (chk.get("status"),
                                                   str(chk.get("reason"))[:60]))

        # --- Ш-2 красный: подмена байта пакета ------------------------
        raw = bytearray(GREEN_PKG.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        code, text = _post("/schedules/run?mode=feasibility-soft",
                           {"problem": payload,
                            "packageZipBase64": base64.b64encode(bytes(raw)).decode("ascii")})
        outcome("Ш-2) подмена байта — отказ словами пакета", code == 400,
                "HTTP %d: %s" % (code, text[:100]))

        # --- Ш-3 красный: битые показания не сертифицируются ----------
        bad = json.loads(json.dumps(report))
        (bad.get("lastRun") or {}).setdefault("logic", {})["registered"] = ["Чужое имя"]
        try:
            ci.build(pkg, payload, solved, bad, name="битые показания")
            outcome("Ш-3) битые показания — вход не строится", False, "построился (дефект)")
        except ci.CertifyInputError as e:
            outcome("Ш-3) битые показания — вход не строится", "не подтверждены" in str(e),
                    "CertifyInputError словами гейта")
    finally:
        mv.stop_jvm(proc)

    bad_n = sum(1 for _, ok, _ in results if not ok)
    print("артефакты: %s" % out_dir)
    print("исходов проверено: %d, незелёных: %d" % (len(results), bad_n))
    print("итог: %s" % ("сходится" if bad_n == 0 else "ДЕФЕКТОВ %d" % bad_n))
    return 0 if bad_n == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
