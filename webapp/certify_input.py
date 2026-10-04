# -*- coding: utf-8 -*-
"""Сборка входа сертификатора с показаниями ступени 3 (этап Г, шов Ш-3).

Модуль для ОРКЕСТРАТОРА кнопки (Г-C1 вызывает его, не инструмент):
из набора решателя, решения и отчёта применённого прогона строится один
JSON — назначения + смены той же нарезкой + `lastRunLogic` внутри
(пара «решение ↔ корзины» атомарна; формат согласован с производителем
2026-08-24, принимается веб-эндпоинтом сертификации и командной строкой).

Гейт шва: корзины показаний подтверждаются `webapp.logic_gate` против
пакета ДО сборки — вход с неподтверждёнными показаниями не строится
(сертификация чужих или битых корзин — «зелёная проверка ни на чём»).

Инструмент tools/most_g_certify_logic.py — тонкая обёртка над этим
модулем (Г-5: его вывод байт-идентичен прежнему, что держит тест).
"""

from __future__ import annotations

from webapp import logic_gate as lg
from webapp import quals_export as qe
from webapp import reserve_oracle as ro
from webapp import solution_export as se


class CertifyInputError(ValueError):
    """Вход сертификатора не собран — с названной причиной."""


def build(pkg: dict, payload: dict, solved: dict, applied_report: dict,
          name: str) -> tuple[dict, dict]:
    """(Вход сертификатора {assignments, duties, solution, lastRunLogic},
    покрытие ручек отдыха для шапки).

    `pkg` — разобранный пакет (webapp.task_package.load): против него
    подтверждаются корзины; `applied_report` — ответ решателя
    (/schedules/applied-rules либо /schedules/run/{id}/applied-rules).
    Показания не подтвердились — [CertifyInputError] с вердиктами гейта.
    """
    run = applied_report.get("lastRun") or {}
    logic = run.get("logic") or {}
    verdicts = lg.check(pkg, logic)
    if not lg.is_green(verdicts):
        raise CertifyInputError(
            "показания logic прогона не подтверждены гейтом — вход "
            "сертификатора не строится:\n" + lg.report(verdicts))
    rest = se.min_rest_from_applied(applied_report)
    coverage: dict = {}
    inp = se.certifier_input(
        payload, solved, rest, name=name, coverage=coverage,
        solver_layover_cost=solver_layover_report(solved, applied_report))
    inp["lastRunLogic"] = logic
    # У входа сертификатора ЗАКРЫТЫЙ перечень полей: покрытие ручек отдыха
    # едет вызывающему отдельным значением, не внутри входа.
    return inp, coverage


def solver_layover_report(solved: dict, applied_report: dict) -> dict | None:
    """Отчёт цены ночёвок решателя ДЛЯ ЭТОГО прогона (H11-WIRE-1, рецензия
    GPT-024 от 06.09.2026).

    Решатель считает отчёт RosterLayoverCost один раз после счёта и хранит
    его в записи прогона: `run/{id}/result` (с единой версии) и
    `run/{id}/applied-rules` отдают один и тот же объект. Берётся из
    решения; в решении нет (решатель прежней сборки) — из показаний
    применённого ТОГО ЖЕ runId, которые вызывающий уже сверил по
    идентификатору прогона (кнопка, Ш-3). Глобального «последнего отчёта»
    здесь нет. Оба источника есть и расходятся — [CertifyInputError]:
    чужое свидетельство сертификатор не получит. Нет ни там, ни там —
    None: экспортёр поле не пишет, сверка цены честно даёт «несравнимо»
    (H11-R2 не ослабляется).
    """
    own = solved.get("layoverCost") if isinstance(solved, dict) else None
    applied = (applied_report.get("layoverCost")
               if isinstance(applied_report, dict) else None)
    own = own if isinstance(own, dict) else None
    applied = applied if isinstance(applied, dict) else None
    if own is not None and applied is not None and own != applied:
        raise CertifyInputError(
            "отчёт цены ночёвок в решении и в показаниях прогона "
            f"{applied_report.get('runId')!r} расходится — вход "
            "сертификатора не строится")
    return own if own is not None else applied


def head(pkg: dict, payload: dict, solved: dict, applied_report: dict,
         inp: dict, rest_places: dict | None = None) -> dict:
    """Шапка прогона для трассировки (Ш-4): счёт, резерв, корзины, пороги."""
    run = applied_report.get("lastRun") or {}
    logic = run.get("logic") or {}
    norms = ro.norms_from_applied(applied_report)
    reserve_rows = ro.reserve_table(payload, solved, norms) if norms else []
    coverage = rest_places or {}
    return {
        "package_fingerprint": (applied_report.get("taskPackage") or {}).get("fingerprint")
                               or run.get("packageFingerprint"),
        "source_run": (pkg.get("manifest") or {}).get("source_run"),
        "spec_version": (pkg.get("manifest") or {}).get("spec_version"),
        "rules_profile": run.get("rulesProfile"),
        "mode": run.get("mode"),
        "objective": run.get("objective"),
        "profile_readings": run.get("profileReadings"),
        "score": solved.get("score"),
        "seats": len(solved.get("flightAssignments") or []),
        "seats_unassigned": ro.unassigned_seats(solved),
        "overlapping_pairs": ro.overlapping_pairs(payload, solved),
        "reserve_norms": norms,
        "reserve_shortfall_total": ro.total_shortfall(reserve_rows),
        "coverage": run.get("coverage"),
        "logic_baskets": {b: len(logic.get(b) or []) for b in lg.BASKETS},
        "logic_generated": [p.get("constraint") for p in (logic.get("plans") or [])],
        "assignments": len(inp.get("assignments") or []),
        "duties": len(inp.get("duties") or []),
        "rest_places": coverage,
    }


def reserve_sheet(payload: dict, solved: dict, applied_report: dict) -> dict:
    """Лист «Резерв» рядом со входом — оракул по набору и решению."""
    norms = ro.norms_from_applied(applied_report)
    rows = ro.reserve_table(payload, solved, norms) if norms else []
    return {"norms": norms, "rows": rows,
            "shortfall_total": ro.total_shortfall(rows)}


def quals_sheet(payload: dict) -> dict:
    """Лист «Допуски» рядом со входом — сроки решателя датами книги."""
    return {"rows": qe.qualification_rows(payload), "reading": qe.READING}
