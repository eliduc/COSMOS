# -*- coding: utf-8 -*-
"""Кнопка С СЕРТИФИКАЦИЕЙ на готовом пакете набора: последнее звено
цепочки доказательств — «решение соответствует своду», судимое
независимо от решателя (Д4 плана «прямой ход»).

Граница (решение владельца 04.09.2026): CDPM заканчивается пакетом
постановки; расчёт пейринга и ростеринга, сертификация решений и их
выгрузка — у COSMOS. Инструмент берёт пакет, собранный раннером набора
(<каталог --out>/<run>-task-package.zip или --package), загружает его в
COSMOS ручками «Пакет постановки» (tools/cosmos_press_client.py),
нажимает пейринг (движок отбора — --engine) и ростеринг, ждёт, читает
выдачу и сертификат каждой задачи и пишет карточку. Реестр исполнителей
и канал сертификатора — забота COSMOS (кнопка выводит реестр при
нажатии; модели назначает площадка по признаку kit пакета). Прогон с
моделью — только по отмашке владельца.

Честность карточки: задача засчитывается сертифицированной ТОЛЬКО по
выдаче этого нажатия (delivery.certified), а не по файлу сертификата,
который может остаться от прежнего нажатия на том же пакете; нажатие
судится по отпечатку пакета (текущий пакет площадки — наш); гейт
запуска канала, закрытый для задачи, — стоп до нажатия; превышение
времени — стоп всей серии (нажатие оставлено на площадке, следующую
задачу поверх него не жмём).

Вход в COSMOS: локальная площадка в dev-режиме не требует входа;
площадки с сессией/Cloudflare Access требуют машинного пути ручек
нажатия (заявка COSMOS) — заголовки задаются --header "Имя: значение".

    .venv\\Scripts\\python.exe -m tools.certify_button <каталог --out раннера>
        --cosmos-url http://127.0.0.1:8123 [--run run-0001] [--spent 60]
        [--engine cpsat] [--timeout 3600] [--out карточка.json]
        [--package путь.zip] [--header "X-Channel-Token: …"]
        [--task both|pairing|rostering] [--on-pairing]
        [--pairing-run-id явно-выбранный-runId] [--moves chain-seed]

Единая версия 06.09.2026 (W-16/W-17): --task rostering — отдельный
ростеринг ТОЛЬКО на явно названном --pairing-run-id (без повторного
пейринга и холодного шага); ожидание и чтение артефактов — ровно своей
попытки (attempt_id из ответа нажатия), чужое нажатие за своё не
принимается; passed судится по выбранным задачам.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from _cli_console import force_utf8_console                    # noqa: E402
from tools.cosmos_press_client import (CosmosPress, CosmosPressError,  # noqa: E402
                                       launch_gate)

force_utf8_console()

# Алиасы артефактов нажатия — словарь COSMOS (webapp/package_runs.py
# ALLOWED_FILES: тот же, что был у панели CDPM); задача зашита в алиас.
ALIASES = {"pairing": ("выдача-кнопки-пейринга", "сертификат-кнопки-пейринга"),
           "rostering": ("выдача-кнопки", "сертификат-кнопки"),
           # Шаг 3 плана 05.09.2026: ростеринг НА утверждённом пейринге —
           # третье, ЯВНОЕ нажатие (pairingRunId), свои артефакты.
           "rostering_on_pairing": ("выдача-кнопки-на-пейринге",
                                    "сертификат-кнопки-на-пейринге")}
STATUS_KEYS = ("package_sha", "task", "engine", "state", "verdict", "error",
               "llm_scope", "tasks_done")


def _summary(cert: dict, delivery: dict) -> dict:
    out: dict = {}
    if delivery:
        out["verdict"] = delivery.get("verdict")
        out["detail"] = delivery.get("detail")
        out["chains"] = delivery.get("chains")
        out["score"] = (delivery.get("schedule") or {}).get("score")
        out["solver_run_id"] = (delivery.get("schedule") or {}).get("solver_run_id")
        # Сертифицированность — из ВЫДАЧИ этого нажатия (fail-closed:
        # остановка кнопки перезаписывает delivery без certified, а
        # старый certificate.json на том же пакете остаётся).
        out["certified"] = delivery.get("certified")
        # A8 (04.09.2026): трёхчастный результат и перечень
        # непроверенных правил — из выдачи, рядом с флагом; карточка
        # серии читает три статуса, а не одно слово.
        out["result"] = delivery.get("result")
        out["uncovered"] = delivery.get("uncovered")
        out["certificate_gate"] = delivery.get("certificate_gate")
        # Единая версия 06.09.2026 (W-18): выбранный пейринг ростеринга,
        # отпечаток каталога и следующие шаги после неудачи на выбранном
        # каталоге — из выдачи; у штатных задач — None, это не ошибка.
        out["pairing_selection"] = delivery.get("pairing_selection")
        out["catalog_sha256"] = delivery.get("catalog_sha256")
        out["next_actions"] = delivery.get("next_actions")
        out["selection_note"] = delivery.get("selection_note")
    if cert and out.get("certified") is True:
        v = cert.get("certificate") or {}
        out["note"] = v.get("note")
        # В шапке certificate findings — ЧИСЛО, перечень находок — в
        # корне отчёта (форма отчёта данных 3.3).
        fl = v.get("findings")
        out["findings"] = fl if isinstance(fl, int) else len(fl or [])
        out["violated_rules"] = v.get("violated_rules")
        out["rules_uncovered"] = v.get("rules_uncovered")
        out["checks_not_compiled"] = v.get("checks_not_compiled")
        out["coverage_strict"] = v.get("coverage_strict")
        out["penalty_reconciliation_clean"] = v.get("penalty_reconciliation_clean")
        out["pairing_objective_reconciliation"] = (
            v.get("pairing_objective_reconciliation") or {}).get("verdict")
        out["counted_share"] = v.get("counted_share")
        checks = cert.get("checks") or []
        out["checks_total"] = len(checks)
        out["checks_run"] = sum(1 for x in checks if x.get("status") == "прогнана")
        out["findings_list"] = [
            {"rule": (f.get("rule_refs") or [None])[0], "severity": f.get("severity"),
             "text": str(f.get("text"))[:160]}
            for f in (cert.get("findings") or [])[:20]]
    elif cert:
        out["certificate_note"] = ("файл сертификата есть, но выдача этого нажатия "
                                   "его не подтверждает — не засчитан")
    return out


def _read_optional(press: CosmosPress, alias: str,
                   attempt: str | None = None) -> dict:
    """Артефакт СВОЕЙ попытки (снимок по ID, W-17); без ID — текущий."""
    try:
        return press.file_json(alias, attempt=attempt)
    except CosmosPressError as e:
        print("   артефакт %s не получен: %s" % (alias, str(e)[:200]), flush=True)
        return {}


def _press_and_read(press: CosmosPress, task: str, alias_key: str, *,
                    engine: str | None, spent: int, timeout: int, sha: str,
                    pairing_run_id: str | None = None,
                    moves: str | None = None) -> tuple[dict, str | None]:
    """Одно нажатие серии: нажать, ждать ровно СВОЮ попытку (attempt_id
    из ответа нажатия — чужое нажатие за свой done не принимается) и
    прочитать выдачу с сертификатом из её снимка. Возвращает (сводка,
    None) либо ({error, wall_s, …}, слова остановки)."""
    t0 = time.time()
    try:
        started = press.press(task, engine=engine, spent=spent,
                              pairing_run_id=pairing_run_id, moves=moves)
        attempt = started.get("attempt_id") if isinstance(started, dict) else None
        st = press.wait(task, timeout_s=timeout, package_sha=sha,
                        attempt_id=attempt,
                        on_state=lambda s: print("   %s" % s[:140], flush=True))
    except CosmosPressError as e:
        print("   %s" % e, flush=True)
        err = {"error": str(e), "wall_s": round(time.time() - t0, 1)}
        if pairing_run_id:
            err["pairing_run_id"] = str(pairing_run_id)
        return err, str(e)[:200]
    wall = round(time.time() - t0, 1)
    d_alias, c_alias = ALIASES[alias_key]
    s = _summary(_read_optional(press, c_alias, attempt),
                 _read_optional(press, d_alias, attempt))
    s["wall_s"] = wall
    s["attempt_id"] = attempt
    s["error"] = st.get("error")
    s["state"] = st.get("state")
    # Факты площадки, не просьбы: движок, которым считали, круг
    # моделей сертификатора по признаку пакета, отпечаток.
    s["select_engine"] = st.get("engine")
    if pairing_run_id:
        s["pairing_run_id"] = str(pairing_run_id)
        s["pairing_run_id_requested"] = str(pairing_run_id)
    s["llm_scope"] = st.get("llm_scope")
    s["package_sha"] = st.get("package_sha")
    print("   вердикт: %s | сертифицирован: %s | находок: %s | "
          "проверок прогнано %s/%s | движок: %s | круг моделей: %s | %.1f с"
          % (s.get("verdict"), s.get("certified"), s.get("findings"),
             s.get("checks_run"), s.get("checks_total"), s.get("select_engine"),
             s.get("llm_scope"), wall), flush=True)
    if s.get("note"):
        print("   %s" % str(s["note"])[:300])
    if s.get("selection_note"):
        print("   %s" % str(s["selection_note"])[:300])
    for f in s.get("findings_list") or []:
        print("   ! %s [%s]: %s" % (f["rule"], f["severity"], f["text"]))
    return s, None


def _parse_headers(items) -> dict:
    out = {}
    for it in items or []:
        if ":" not in it:
            raise SystemExit("--header ждёт форму «Имя: значение», получено %r" % it)
        k, v = it.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="каталог --out раннера (в нём <run>-task-package.zip)")
    ap.add_argument("--run", default="run-0001")
    ap.add_argument("--cosmos-url", default=None,
                    help="адрес веб-приложения COSMOS (ручки «Пакет постановки»)")
    ap.add_argument("--header", action="append", default=[],
                    help="заголовок входа COSMOS «Имя: значение» (повторяемый); "
                         "локальный dev-режим входа не требует")
    ap.add_argument("--package", default=None,
                    help="явный путь пакета постановки (умолчание — "
                         "<out_dir>/<run>-task-package.zip)")
    ap.add_argument("--engine", default="cpsat", choices=("cpsat", "cplex", "highs"),
                    help="движок точного отбора пейринга (точка выбора — ручка COSMOS)")
    ap.add_argument("--spent", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--out", default=None)
    # Единая версия 06.09.2026 (W-16): выбор задач серии. both — серия
    # main (пейринг → штатный ростеринг → по --on-pairing/--pairing-run-id
    # третье нажатие на пейринге); pairing — только пейринг; rostering —
    # ТОЛЬКО ростеринг на явно названном --pairing-run-id, без повторного
    # пейринга и без холодного шага (прогон уже есть на площадке).
    ap.add_argument("--task", default="both", choices=("both", "pairing", "rostering"),
                    help="both — серия целиком; pairing — только пейринг; rostering — "
                         "только ростеринг на явном --pairing-run-id (обязателен)")
    ap.add_argument("--on-pairing", action="store_true",
                    help="после двух нажатий — третье: ростеринг НА утверждённом "
                         "пейринге (pairingRunId из выдачи нажатия pairing, "
                         "называется явно; молчаливой подстановки нет)")
    ap.add_argument("--pairing-run-id", default=None,
                    help="явный прогон пейринга для нажатия «на пейринге» (при "
                         "--task both умолчание — прогон из выдачи нажатия pairing "
                         "этой же серии; при --task rostering обязателен)")
    ap.add_argument("--moves", default=None,
                    help="набор ходов решателя для нажатия на пейринге (chain | "
                         "chain-seed | pillar | pillar-rr; "
                         "пусто — умолчание решателя), шаг 4 плана 05.09.2026")
    a = ap.parse_args(argv)
    a.pairing_run_id = (a.pairing_run_id or "").strip() or None
    if a.task == "rostering" and not a.pairing_run_id:
        raise SystemExit("--task rostering требует явный --pairing-run-id: "
                         "последний доступный прогон пейринга не подставляется")
    if a.task == "pairing" and (a.pairing_run_id or a.on_pairing):
        raise SystemExit("--pairing-run-id/--on-pairing бессмысленны при --task "
                         "pairing: ростеринг не нажимается")
    if client is None and not a.cosmos_url:
        raise SystemExit("нужен --cosmos-url: расчёт и сертификация — у COSMOS")
    out_dir = Path(a.out_dir).resolve()
    pkg = Path(a.package) if a.package else out_dir / ("%s-task-package.zip" % a.run)
    if not pkg.is_file():
        raise SystemExit("нет пакета постановки: %s" % pkg)
    zip_bytes = pkg.read_bytes()

    if client is None:
        import httpx
        client = httpx.Client(base_url=a.cosmos_url, headers=_parse_headers(a.header),
                              timeout=httpx.Timeout(900.0, connect=30.0))
    press = CosmosPress(client)
    try:
        up = press.upload(zip_bytes, name=pkg.name)
        st0 = press.status()
    except CosmosPressError as e:
        raise SystemExit(str(e))
    sha = up["sha"]
    print("пакет загружен в COSMOS: %s, sha %s…, вердикт приёма: %s"
          % (pkg.name, sha[:16], up.get("verdict")), flush=True)
    if st0.get("present") is False or st0.get("package_sha") not in (None, sha):
        raise SystemExit("текущий пакет площадки — не наш (%s… против %s…): "
                         "площадка держит другой пакет (CREW_TASK_PACKAGE или "
                         "чужая загрузка)" % (str(st0.get("package_sha"))[:12], sha[:12]))
    card: dict = {"package": str(pkg), "sha256": sha,
                  "upload_verdict": up.get("verdict"),
                  "upload_detail": up.get("detail"),
                  "cosmos": a.cosmos_url or "в процессе",
                  "engine_requested": a.engine, "task_requested": a.task,
                  "pairing_run_id_requested": a.pairing_run_id,
                  "passed": False}
    stopped = None
    # Задачи серии по --task: both — пейринг и штатный (холодный)
    # ростеринг, как в серии main; pairing — только пейринг; rostering —
    # ни того ни другого, только нажатие на пейринге ниже.
    series = {"both": ("pairing", "rostering"), "pairing": ("pairing",),
              "rostering": ()}[a.task]
    # Задачи, по которым судится passed: ровно выбранные (W-16).
    judged: list[str] = list(series)
    for task in series:
        print("\n%s: кнопка с сертификацией" % task, flush=True)
        gate = launch_gate(up, task)
        if gate:
            print("   %s" % gate, flush=True)
            card[task] = {"error": gate, "skipped": True}
            continue
        s, stop = _press_and_read(press, task, task,
                                  engine=(a.engine if task == "pairing" else None),
                                  spent=a.spent, timeout=a.timeout, sha=sha)
        card[task] = s
        if stop:
            stopped = "%s: %s" % (task, stop)
            break
    on_pairing = a.task == "rostering" or a.on_pairing or bool(a.pairing_run_id)
    if on_pairing and not stopped:
        # Нажатие «ростеринг на утверждённом пейринге». Прогон называется
        # ЯВНО: аргументом либо из выдачи нажатия pairing этой серии;
        # COSMOS сверяет его с попытками пейринга пакета (пакет, каталог,
        # состав рейсов) и отказывает словами при несоответствии
        # (webapp.package_runs). Подстановки «последнего» нет.
        judged.append("rostering_on_pairing")
        p_run = a.pairing_run_id or (card.get("pairing") or {}).get("solver_run_id")
        if p_run in ("None", ""):
            p_run = None
        print("\nrostering на утверждённом пейринге: кнопка с сертификацией "
              "(pairingRunId = %s)" % p_run, flush=True)
        gate = launch_gate(up, "rostering")
        if gate:
            print("   %s" % gate, flush=True)
            card["rostering_on_pairing"] = {"error": gate, "skipped": True}
        elif not p_run:
            card["rostering_on_pairing"] = {
                "error": "прогона пейринга нет в выдаче нажатия pairing", "skipped": True}
            print("   прогона пейринга нет в выдаче нажатия pairing — нажатие "
                  "на пейринге пропущено", flush=True)
        else:
            s, stop = _press_and_read(press, "rostering", "rostering_on_pairing",
                                      engine=None, spent=a.spent, timeout=a.timeout,
                                      sha=sha, pairing_run_id=str(p_run),
                                      moves=a.moves)
            card["rostering_on_pairing"] = s
            if stop:
                stopped = "rostering на пейринге: %s" % stop
    if stopped:
        card["stopped"] = stopped
        print("\nСЕРИЯ ОСТАНОВЛЕНА: %s — нажатие оставлено на площадке, "
              "следующая задача не нажималась" % stopped)
    both = bool(judged) and all((card.get(t) or {}).get("certified") is True
                                for t in judged)
    card["passed"] = both
    card["judged_tasks"] = judged
    print("\nСЕРТИФИКАЦИЯ: %s" % ("ВЫБРАННЫЕ ЗАДАЧИ СЕРТИФИЦИРОВАНЫ (%s)"
                                  % ", ".join(judged) if both
                                  else "НЕ СЕРТИФИЦИРОВАНО"))
    if a.out:
        Path(a.out).write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
        print("карточка: %s" % a.out)
    return 0 if both else 1


if __name__ == "__main__":
    raise SystemExit(main())
