# -*- coding: utf-8 -*-
"""Гейт сертификата решения (ступень 1 моста CDPM → COSMOS).

Проверяет честность сертификата против свода:

1. самотест: сертификат с «потерянным» правилом обязан ловиться;
2. ПОЛНОТА: каждое проверяемое правило свода (HARD/DATA/CASE)
   имеет хотя бы один исход в checks (через basis.rule) — правило,
   молча выпавшее из сертификации, недопустимо; правило с пометкой
   «исключено до получения данных по решению авиакомпании»
   (структура ВМП 2026-09-02) исхода не имеет по замыслу, но обязано
   быть НАЗВАНО в шапке certificate.excluded_until_data — иначе оно
   выпало молча;
3. счётчики отчёта равны пересчёту контрактной функцией;
4. шапка certificate согласована с телом (число находок, перечень
   нарушенных правил, вердикт certified ⇔ находок нет);
5. каждая находка ссылается на прогнанную или частично прогнанную
   проверку;
6. ревизия 1.18.3: любая «частично прогнана», «не прогнана» или
   покрытие окон с неизвестными окнами (`window_coverage.unknown ≠ 0`)
   делает `certified = false` обязательным; правило непрерывного окна
   (семья rolling_rest, K-15) без раздела continuous_critical_cells
   читается как «не прогнана», а не «прогнана» — всегда (пропуск
   доказательства целиком гейт не выключает).

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный —
код возврата 1.

Запуск: python -m cdpm.gates.gate_certify МОДЕЛЬ СЕРТИФИКАТ.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdpm import data_report as dr


def _selftest() -> None:
    svod = {"rules": [{"id": "R-1", "kind": "HARD"}]}
    report = {"checks": [], "findings": [], "counters":
              dr.metrics_of({"checks": [], "findings": []}),
              "certificate": {"findings": 0, "violated_rules": [],
                              "certified": True,
                              "checks_run": 0, "checks_not_run": 0}}
    problems, _ = check(svod, report)
    assert any("нет ни одного исхода" in p for p in problems), \
        "самотест: потерянное правило не поймано"


def check(svod: dict, report: dict) -> tuple[list[str], dict]:
    problems: list[str] = []
    checks = report.get("checks", [])
    findings = report.get("findings", [])
    cert = report.get("certificate") or {}
    counters = {"rules": 0, "checks": len(checks),
                "findings": len(findings)}

    from vmp.registry_check import is_excluded_until_data
    covered = {(c.get("basis") or {}).get("rule") for c in checks}
    named_excluded = {str(x.get("rule_id")) for x in
                      (cert.get("excluded_until_data") or [])
                      if isinstance(x, dict)}
    for r in svod.get("rules", []):
        if r.get("kind") not in dr.CHECKABLE_KINDS:
            continue
        counters["rules"] += 1
        if is_excluded_until_data(r):
            # Исключено по решению авиакомпании: исхода нет по замыслу,
            # но сертификат обязан назвать правило непроверенным.
            if str(r.get("id")) not in named_excluded:
                problems.append(f"{r.get('id')}: правило исключено до "
                                f"получения данных, но не названо в "
                                f"сертификате (excluded_until_data) — "
                                f"потеряно молча")
            continue
        if r.get("id") not in covered:
            problems.append(f"{r.get('id')}: у проверяемого правила "
                            f"свода нет ни одного исхода в "
                            f"сертификате — потеряно молча")

    got = report.get("counters")
    want = dr.metrics_of(report)
    if got != want:
        problems.append(f"счётчики отчёта {got} ≠ пересчёту {want}")

    # Находка допустима по прогнанной и по частично прогнанной проверке
    # (1.18.3: доказанное нарушение сохраняется при неизвестности
    # других окон).
    run_ids = {c.get("id") for c in checks
               if c.get("status") in dr.RUN_STATUSES}
    for f in findings:
        if f.get("check") not in run_ids:
            problems.append(f"{f.get('id')}: находка ссылается на "
                            f"непрогнанную проверку "
                            f"{f.get('check')!r}")

    if cert.get("findings") != len(findings):
        problems.append(f"шапка: findings {cert.get('findings')} ≠ "
                        f"{len(findings)}")
    violated = sorted({rid for f in findings
                       for rid in f.get("rule_refs", [])})
    if cert.get("violated_rules") != violated:
        problems.append(f"шапка: violated_rules "
                        f"{cert.get('violated_rules')} ≠ {violated}")
    n_run = sum(1 for c in checks if c.get("status") == "прогнана")
    # Вакуумный «зелёный» запрещён: certified ⇔ (есть прогнанные И
    # нарушений нет) — ревью ступени 1, 2026-08-22. Отрицательный
    # solver_hard — САМОСТОЯТЕЛЬНОЕ основание «не сертифицирован»
    # (решатель сам назвал решение недопустимым): тело при этом
    # вправе быть чистым — живой красный Г-S4 на однодневном нано
    # с runInfo −4hard (2026-08-24).
    hard = cert.get("solver_hard")
    hard_neg = isinstance(hard, int) and hard < 0
    # Третье самостоятельное основание «не сертифицирован» — красный
    # гейт показаний решателя по правилам с logic (аудит №2): тело
    # при этом вправе быть чистым.
    lg_red = cert.get("logic_gate_green") is False
    # Д4: в строгом режиме непокрытое правило и нескомпилированная
    # проверка — самостоятельные основания «не сертифицирован».
    strict = bool(cert.get("coverage_strict"))
    strict_red = strict and bool(cert.get("rules_uncovered")
                                 or int(cert.get("checks_not_compiled") or 0))
    # Д4, пункт 3: вердикт сверки пересчитывается ИЗ ТЕЛА (результат
    # сверки лежит в шапке целиком), а не берётся готовым флагом.
    pr = cert.get("penalty_reconciliation")
    if pr is not None:
        from vmp import penalty_reconcile as _pr
        pr_clean = bool(_pr.is_clean(pr))
        if cert.get("penalty_reconciliation_clean") is not None \
                and bool(cert.get("penalty_reconciliation_clean")) != pr_clean:
            problems.append("шапка: признак штрафной сверки расходится "
                            "с её же разбором в теле")
        if strict and not pr_clean:
            strict_red = True
    # Сверка целевой пейринга судится тем же порядком: вердикт из тела.
    po = cert.get("pairing_objective_reconciliation")
    if po is not None:
        from vmp import penalty_reconcile as _pr
        po_clean = bool(_pr.is_clean(po))
        if cert.get("pairing_objective_clean") is not None \
                and bool(cert.get("pairing_objective_clean")) != po_clean:
            problems.append("шапка: признак сверки целевой пейринга "
                            "расходится с её же разбором в теле")
        if strict and not po_clean:
            strict_red = True
    # Ревизия 1.18.3: неполное доказательство — самостоятельное
    # основание «не сертифицирован»: любая «частично прогнана» / «не
    # прогнана» проверка либо покрытие окон с неизвестными окнами.
    incomplete = any(dr.check_is_incomplete(c) for c in checks)
    # K-15 / H12 (план §3.8, GPT-022, сигнал COSMOS «красить всегда»
    # после появления непрерывной формы в сертификате): проверка правила
    # семьи rolling_rest без раздела continuous_critical_cells читается
    # как «не прогнана» ВСЕГДА — пропуск доказательства целиком (omitted
    # proof) не выключает обязательный гейт. Прежнее переходное условие
    # «только если сертификат уже несёт непрерывное доказательство» снято.
    rules_by_id = {str(r.get("id")): r for r in svod.get("rules", [])
                   if isinstance(r, dict)}
    for c in checks:
        rid = str((c.get("basis") or {}).get("rule") or "")
        if c.get("status") in dr.RUN_STATUSES \
                and dr.rule_needs_continuous_proof(rules_by_id.get(rid)) \
                and not dr.has_continuous_proof(c):
            problems.append(f"{c.get('id')}: правило {rid} непрерывного "
                            f"окна (rolling_rest) без непрерывного "
                            f"доказательства (continuous_critical_cells) "
                            f"— читается как «не прогнана», не "
                            f"«{c.get('status')}»")
            incomplete = True
    expected = ((n_run > 0 and not findings) and not hard_neg
                and not lg_red and not strict_red and not incomplete)
    if bool(cert.get("certified")) != expected:
        problems.append("шапка: certified противоречит телу (находки, "
                        "ноль прогнанных проверок, отрицательный "
                        "solver_hard, красный гейт показаний logic, "
                        "непокрытые правила в строгом режиме Д4 либо "
                        "неполное доказательство — частично/не прогнанные "
                        "проверки и неизвестные окна)")
    if cert.get("checks_run") != n_run \
            or cert.get("checks_not_run") != len(checks) - n_run:
        problems.append("шапка: счёт прогнанных/непрогнанных "
                        "расходится с телом")
    considered = len({(c.get("basis") or {}).get("rule")
                      for c in checks
                      if (c.get("basis") or {}).get("rule")})
    if cert.get("rules_considered") != considered:
        problems.append(f"шапка: rules_considered "
                        f"{cert.get('rules_considered')} ≠ "
                        f"{considered}")
    # Сертификат обязан оставаться валидным отчётом контракта 3.3 —
    # его читают штатные потребители линии данных.
    problems += dr.validate_report(report, svod)
    return problems, counters


def run(model_path: str, report_path: str) -> int:
    _selftest()
    svod = json.loads(Path(model_path).read_text(encoding="utf-8"))
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    problems, c = check(svod, report)
    print(f"рассмотрено: проверяемых правил {c['rules']}, проверок "
          f"{c['checks']}, находок {c['findings']} (самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: сертификат честен — полнота и счётчики сходятся")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Гейт сертификата решения")
    ap.add_argument("model", help="model.json свода")
    ap.add_argument("report", help="certificate.json")
    args = ap.parse_args(argv)
    return run(args.model, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
