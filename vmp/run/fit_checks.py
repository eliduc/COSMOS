# -*- coding: utf-8 -*-
"""Детерминированные проверки сертификата из ПОСАДКИ правил (решение
владельца 05.09.2026, пункт 3 после аудита: «детерминированная
компиляция проверок для всех семей с исполнителями»).

Класс ошибки, который это закрывает: компилятор проверок (модель)
переинтерпретирует текст правила между прогонами — на проходе 6 H0-К
он выбрал единицу «смена» для состава экипажа и дал 31 ложное нарушение
при честном решении. Там, где правило ПОСАЖЕНО (template_fit категории
A/B с параметром семьи), форма проверки известна коду заранее: семья
задаёт единицу, лист и способ счёта, параметр посадки — число. Такие
правила компилятору не подаются вовсе.

Две дорожки:
  * спецификация шаблона словаря контракта (там, где шаблон выражает
    правило точно): состав экипажа (счёт-состава-группы по рейсу),
    предел смены (максимальная-длительность-часов; адресные потолки
    продления применяет apply_extension_caps, как к спецификациям
    компилятора);
  * исполнитель кодом (там, где шаблон словаря правило не выражает):
    полы отдыха дома / вне базы (порог по месту отдыха предыдущей
    смены), минимальная стыковка внутри смены, посадок за смену (без
    перегонов), суток командировки, налёт за месяц, допуск на тип по
    исходной книге, недоступность члена экипажа (отпуска) по исходному
    листу «Отпуска» (семья crew_unavailability, каталог 0.6.0).

Семьи без посадки или без исполнителя здесь остаются компилятору (с
последующим приведением единицы, см. roster_certify.normalize_composition_specs).

Единая версия 06.09.2026 (строки C-21..C-24, план §3.10.3):
  * полы отдыха — покрытие по сотруднику за объявленный горизонт
    (window_coverage, granularity employee_rest_sequence), каждый отдых
    адресован в rest_assessments, включая первый по «Истории» (только
    при срезе = начало горизонта и конце последней смены ≤ начала);
    отсутствие факта — unknown, не ноль; сотрудник без смен — в покрытии
    (rest_expected 0); «есть назначения, но нет смен» — unknown;
  * история сотрудника с несколькими строками либо «Есть история» ≠ да
    — проблема сотрудника (unknown), не «последняя строка побеждает»;
  * налёт за месяц — календарные месяцы, пересекающие горизонт, по
    каждому сотруднику (и без назначений); префикс месяца — только
    точный агрегат истории при срезе = начало горизонта, иначе unknown;
    хвост месяца за горизонтом — unknown; известный подытог сверх
    предела — доказанное нарушение (lower_bound_violation); рейс — в
    месяц вылета целиком; перегон не считается;
  * политика раздела history 1.17.2 (main): continue_zero → сотрудник
    без записи судится по названному допущению нулевых остатков;
  * допуск на тип судна судится кодом по замороженной исходной книге
    (единственные листы «Рейсы» и «Допуски» вне решения) на дату рейса
    источника, границы включительно, пустые границы открыты, нечитаемое
    либо неоднозначное — вся проверка «не прогнана» без находок; без
    исходных листов нужной схемы правило остаётся пути main (лист
    «Допуски» решения и компилятор).
"""
from __future__ import annotations

import math
from datetime import datetime

SOLUTION_SID = "SOL"
SEVERITY = "БЛОКИРУЮЩЕЕ"
VIOLATION_CLASS = "нарушение правила"
COMPILED_BY = "код (посадка правила)"


def _fit_records(rule: dict) -> list[dict]:
    """Read current landing through its strict, lossless consumer view.

    Explicit landing versions never fall back to raw cells or params[].
    Unversioned legacy fits retain their established canonical-value shape,
    but a second, conflicting carrier cannot silently be ignored.
    """
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return []
    if "schema_version" in fit:
        from webapp.threshold_gate import ThresholdGateError, projected_params
        if fit["schema_version"] != "landing/2":
            raise ValueError(f"{rule.get('id')}: версия посадки {fit['schema_version']!r} "
                             "не разрешает проверку текущего формата")
        try:
            records = projected_params(rule)
        except ThresholdGateError as exc:
            raise ValueError(str(exc)) from exc
        # Projection validates the entire rule, but a fit reader must not
        # acquire a missing landing cell from a legacy-only record.
        records = [p for p in records if p.get("name") in (fit.get("params") or {})]
    else:
        from cdpm import model as m
        problems = m.reconcile_carriers(rule)
        if problems:
            raise ValueError("; ".join(problems))
        cells = fit.get("params") or {}
        if not isinstance(cells, dict):
            raise ValueError("параметры посадки не объект")
        records = [dict(cell, name=name) if isinstance(cell, dict)
                   else {"name": name, "value": cell} for name, cell in cells.items()]
    names = [p.get("name") for p in records]
    if len(names) != len(set(names)):
        raise ValueError("несколько записей параметра; выбор первой запрещён")
    # These deterministic forms do not implement per-parameter scope. A
    # qualified number must not become an unconditional threshold here.
    if any(p.get("qualifier") is not None or p.get("condition") is not None
           for p in records):
        raise ValueError("уточнитель параметра не выражен формой проверки посадки")
    return records


def _fit_record(rule: dict, name: str) -> dict | None:
    records = [p for p in _fit_records(rule) if p.get("name") == name]
    if len(records) > 1:
        raise ValueError(f"{name}: несколько записей параметра; выбор первой запрещён")
    return records[0] if records else None


def fit_param(rule: dict, name: str):
    """Проверенное конечное число посадки A/B либо None; без raw fallback."""
    try:
        record = _fit_record(rule, name)
    except (ValueError, TypeError, AttributeError):
        return None
    v = record.get("value") if record is not None else None
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or isinstance(v, float) and not math.isfinite(v)):
        return None
    return v


def _family(rule: dict) -> str | None:
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        return fit.get("family")
    return (rule.get("export_solver") or {}).get("family")


def _sheet(dataset: dict, name: str) -> tuple[list[dict], list[str]] | None:
    table = ((dataset.get("tables") or {}).get(SOLUTION_SID) or {}).get(name)
    if not isinstance(table, dict):
        return None
    return list(table.get("rows") or []), list(table.get("columns") or [])


def _moment(v):
    from cdpm import data_checks as dc
    try:
        return dc.parse_moment(str(v or ""))
    except Exception:                                    # noqa: BLE001
        return None


def _check(rid: str, rule: dict, template: str, unit: str, statement: str,
           params: dict) -> dict:
    return {"id": f"C-{rid}-посадка", "basis": {"rule": rid},
            "template": template, "params": params,
            "statement": statement, "unit": unit, "severity": SEVERITY,
            "compiled_by": COMPILED_BY}


class _Counter:
    def __init__(self):
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"K-{self.n:04d}"


def _finding(counter: _Counter, check: dict, sheet: str, row: int,
             text: str, evidence: str, rid: str, column: str | None = None) -> dict:
    target = {"sid": SOLUTION_SID, "sheet": sheet, "row": row}
    if column:
        target["column"] = column
    return {"id": counter.next(), "check": check["id"], "severity": SEVERITY,
            "class": VIOLATION_CLASS, "target": target, "text": text,
            "evidence": evidence, "rule_refs": [rid]}


# ---------------------------------------------------------------- исполнители

def _rest_floor(rid, rule, dataset, counter, kind: str, min_min: float):
    """Пол отдыха между сменами сотрудника: судятся ТОЛЬКО отдыхи
    названного вида (дома — место отдыха равно базе приписки; вне базы —
    иначе). Покрытие — по сотруднику за объявленный горизонт
    (window_coverage), каждый отдых адресован в rest_assessments,
    включая первый — по «Истории» (конец последней смены при срезе =
    начало горизонта); место отдыха не названо, история отсутствует или
    неоднозначна — отдых unknown с причиной, не ноль; сотрудник без
    записи при continue_zero — первый отдых судится по названному
    допущению (до горизонта свободен)."""
    from vmp.run import logic_checks as lc

    where = "дома" if kind == "home" else "вне базы"
    check = _check(rid, rule, "пол-отдыха-между-сменами", "отдых",
                   f"отдых {where} между сменами сотрудника не короче "
                   f"{min_min:g} мин — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Смены", "kind": kind,
                    "min_minutes": min_min})
    lc._coverage(check, "employee_rest_sequence")
    got = lc._need(check, _sheet(dataset, "Смены"), "Смены",
                   (lc.EMP, "Начало", "Конец", "Место отдыха", lc.BASE))
    if got is None:
        return check, []
    rows, _columns = got
    context = lc._window_context(check, dataset, rows, None, ("Начало", "Конец"))
    if context is None:
        return check, []
    lo, until, by_emp, scope_problems = context
    horizon_start = datetime.combine(lo, datetime.min.time())
    policy = lc._zero_policy(dataset)
    found = []
    histories, history_problems = _history_evidence(dataset)
    assignment_sheet = _sheet(dataset, "Назначения")
    assigned = {str(r.get(lc.EMP) or "").strip()
                for r in assignment_sheet[0]} if assignment_sheet else set()
    judged_history = 0
    for emp, lst in by_emp.items():
        lst.sort(key=lambda x: (lc._moment(x[1].get("Начало")) or datetime.max, x[0]))
        problems = [scope_problems[emp]] if emp in scope_problems else []
        assessments = []
        assumed_basis = None
        finding_start = len(found)
        if not lst and emp in assigned:
            problems.append("есть назначения, но нет разметки смен сотрудника")
        for pos, (i1, nxt) in enumerate(lst):
            b, end = lc._moment(nxt.get("Начало")), lc._moment(nxt.get("Конец"))
            base = str(nxt.get(lc.BASE) or "").strip()
            reason = ""
            assumed_here = False
            if b is None or end is None or end <= b or not lo <= b.date() < until:
                reason = "нечитаемый, неположительный либо внегоризонтный интервал смены"
            if pos == 0:
                h = histories.get(emp) or {}
                a = lc._moment(h.get("Конец последней смены"))
                station = str(h.get("Место последней смены") or "").strip()
                source = "История"
                if (not reason and not h and emp not in history_problems
                        and lc._assumed_zero(policy, lo, None)):
                    # continue_zero (main): записи нет — до горизонта
                    # сотрудник свободен по принятому допущению; первый
                    # отдых не короче любого пола и так называется.
                    assumed_here = True
                    assumed_basis = policy["basis"]
                    source = "История (допущение нулевых остатков)"
                elif not reason and (emp in history_problems or not h
                        or lc._moment(h.get("Срез")) != horizon_start
                        or a is None or a > horizon_start):
                    reason = history_problems.get(emp) or (
                        "нет конца последней смены при точном срезе истории на начало горизонта"
                        + ("" if h else " (" + lc._missing_record_reason(policy) + ")"))
            else:
                i0, prev = lst[pos - 1]
                a = lc._moment(prev.get("Конец"))
                prev_start = lc._moment(prev.get("Начало"))
                station = str(prev.get("Место отдыха") or "").strip()
                if not reason and (a is None or prev_start is None or a <= prev_start):
                    reason = "нечитаемый либо неположительный интервал предыдущей смены"
                source = f"Смены!строка {i0}"
            if not reason and not assumed_here and (not base or not station):
                reason = "не установлены база приписки или место отдыха"
            assessment = {"duty_row": i1, "duty_id": nxt.get("Идентификатор"),
                          "duty_start": nxt.get("Начало"), "previous_source": source,
                          "rest_start": a.isoformat() if a is not None else None,
                          "rest_end": b.isoformat() if b is not None else None}
            if reason:
                assessment.update(status="unknown", reason=reason)
                problems.append(f"Смены!строка {i1}: {reason}")
            elif assumed_here:
                assessment.update(status="evaluated", outcome="pass", assumed=True,
                                  reason="до горизонта сотрудник свободен по допущению "
                                         "нулевых остатков (%s)" % assumed_basis)
            elif (kind == "home") != (station == base):
                assessment.update(status="evaluated", outcome="not_applicable",
                                  reason="доказано другое место отдыха, вне области этого пола")
            else:
                if pos == 0:
                    judged_history += 1
                gap = (b - a).total_seconds() / 60.0
                violation = gap < min_min
                assessment.update(status="evaluated", outcome="violation" if violation else "pass",
                                  observed_minutes=gap, reason="времена, место и база установлены")
                if violation:
                    found.append(_finding(counter, check, "Смены", i1,
                        f"отдых {where} {gap:g} мин короче пола {min_min:g} мин"
                        + (" (история до первой смены горизонта)" if pos == 0 else ""),
                        f"{SOLUTION_SID}!{source}: Табельный номер=«{emp}», конец=«{a.isoformat()}»; "
                        f"Смены!строка {i1}: начало=«{b.isoformat()}», место=«{station}», база=«{base}»",
                        rid, "Начало"))
            assessments.append(assessment)
        entry = lc._window(check, emp, lo, until, "; ".join(dict.fromkeys(problems)),
                           "violation" if len(found) > finding_start else None if problems else "pass",
                           assumed_basis if not problems else None)
        entry["rest_assessments"] = assessments
        entry["rest_expected"] = len(assessments)
        entry["rest_evaluated"] = sum(a["status"] == "evaluated" for a in assessments)
        entry["rest_unknown"] = sum(a["status"] == "unknown" for a in assessments)
        if not lst and not problems:
            entry["reason"] = "у сотрудника нет назначенных смен; отдых перед сменой не требуется"
    check["note"] = ("покрытие по сотруднику за объявленный горизонт; каждый отдых адресован "
                     "в rest_assessments, включая первый по истории")
    if histories:
        idle = sorted(emp for emp in histories if not by_emp.get(emp))
        check["note"] += (f"; история передана: отдых до первой смены горизонта судился у "
                          f"{judged_history} сотрудников со сменами этого вида отдыха"
                          + (f"; без смен в решении: {', '.join(idle)}" if idle else ""))
    return lc._finish_windows(check, len(rows)), found


def _history_evidence(dataset):
    """Строки «Истории» по сотруднику (C-22, ревью группы C 06.09.2026):
    несколько строк — история неоднозначна (проблема сотрудника, unknown
    при любом решении); единственная строка с «Есть история» ≠ да —
    записи истории у сотрудника НЕТ: она не в valid и не в problems, и
    судится политикой раздела history 1.17.2 как отсутствие записи
    (continue_zero → названное допущение нулевых остатков через
    logic_checks._assumed_zero; без решения — unknown с причиной
    logic_checks._missing_record_reason), то же прочтение, что у
    _history_by_employee и исполнителей форм. Ни то ни другое не
    становится нулевым остатком молча."""
    got = _sheet(dataset, "История")
    groups = {}
    for row in got[0] if got else []:
        groups.setdefault(str(row.get("Табельный номер") or "").strip(), []).append(row)
    valid, problems = {}, {}
    for emp, rows in groups.items():
        if len(rows) != 1:
            problems[emp] = "история сотрудника неоднозначна: несколько строк"
        elif str(rows[0].get("Есть история") or "").strip().lower() != "да":
            continue
        else:
            valid[emp] = rows[0]
    return valid, problems


def _history_by_employee(dataset: dict) -> dict[str, dict]:
    """Строки листа «История» (ревизия 1.17) по табельному номеру —
    только сотрудники с историей («Есть история» = да)."""
    got = _sheet(dataset, "История")
    if got is None:
        return {}
    rows, _cols = got
    out: dict[str, dict] = {}
    for r in rows:
        if str(r.get("Есть история") or "").strip().lower() != "да":
            continue
        out[str(r.get("Табельный номер") or "").strip()] = r
    return out


def _monthly_block(rid, rule, dataset, counter, max_min: float):
    """Налёт за календарный месяц (рабочие назначения без перегонов) с
    точным известным префиксом месяца до горизонта из «Истории» (C-23).
    Неизвестные префикс / хвост — не ноль; известный подытог сверх
    предела доказывает нарушение и при неизвестном полном итоге. Рейс
    относится к месяцу вылета целиком; законный прилёт за горизонтом
    не рождает нового планового месяца. Сотрудник без записи при
    continue_zero — префикс нулевой по названному допущению."""
    from vmp.run import logic_checks as lc

    check = _check(rid, rule, "налёт-за-месяц-с-остатком", "сотрудник × месяц",
                   f"налёт за месяц с остатком на начало периода не больше "
                   f"{max_min:g} мин — {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "max_minutes": max_min})
    lc._coverage(check, "calendar_month")
    got = lc._need(check, _sheet(dataset, "Назначения"), "Назначения",
                   (lc.EMP, "Время вылета", "Время прилёта"))
    if got is None:
        return check, []
    rows, _columns = got
    context = lc._window_context(check, dataset, rows, None)
    if context is None:
        return check, []
    lo, until, by_emp, scope_problems = context
    horizon_start = datetime.combine(lo, datetime.min.time())
    policy = lc._zero_policy(dataset)
    histories, history_problems = _history_evidence(dataset)
    history_sheet = _sheet(dataset, "История")
    history_rows = {str(row.get(lc.EMP) or "").strip(): i
                    for i, row in enumerate(history_sheet[0] if history_sheet else [], 2)}
    windows = []
    start = lo.replace(day=1)
    while start < until:
        finish = (start.replace(year=start.year + 1, month=1) if start.month == 12
                  else start.replace(month=start.month + 1))
        windows.append((start, finish))
        start = finish
    found = []
    carried = 0
    for emp, assignments in by_emp.items():
        totals, first_rows, bad_months = {}, {}, set()
        invalid_unlocated = False
        for i, row in assignments:
            if str(row.get("Перегон") or "").strip().lower() == "да":
                continue
            a, b = lc._moment(row.get("Время вылета")), lc._moment(row.get("Время прилёта"))
            month = a.date().replace(day=1) if a is not None else None
            if a is None or b is None or b <= a or not lo <= a.date() < until:
                if month is None or month not in {w[0] for w in windows}:
                    invalid_unlocated = True
                else:
                    bad_months.add(month)
                continue
            totals[month] = totals.get(month, 0.0) + (b - a).total_seconds() / 60.0
            first_rows.setdefault(month, i)
        for start, finish in windows:
            reasons = [scope_problems[emp]] if emp in scope_problems else []
            if invalid_unlocated or start in bad_months:
                reasons.append("есть нечитаемые, неположительные либо внегоризонтные назначения")
            if finish > until:
                reasons.append(f"неизвестен остаток календарного месяца [{until.isoformat()}, {finish.isoformat()})")
            carry, proof = 0.0, None
            assumed_basis = None
            if start < lo:
                h = histories.get(emp) or {}
                amount = lc._num(h.get("Налёт с начала месяца, мин"))
                if not h and emp not in history_problems and lc._assumed_zero(policy, lo, None):
                    assumed_basis = policy["basis"]
                    proof = {"kind": "assumed_zero_prefix", "basis": assumed_basis,
                             "start_inclusive": start.isoformat(), "end_exclusive": lo.isoformat(),
                             "block_minutes": 0.0}
                elif emp in history_problems or not h:
                    reasons.append(history_problems.get(emp) or (
                        "нет истории месячного остатка сотрудника ("
                        + lc._missing_record_reason(policy) + ")"))
                elif lc._moment(h.get("Срез")) != horizon_start:
                    reasons.append("срез месячного остатка не совпадает точно с началом объявленного горизонта")
                elif amount is None or amount < 0 or amount > (lo - start).days * 1440:
                    reasons.append("нет достоверного неотрицательного налёта полного префикса месяца")
                else:
                    carry = amount
                    carried += 1
                    proof = {"kind": "exact_month_prefix_aggregate",
                             "start_inclusive": start.isoformat(), "end_exclusive": lo.isoformat(),
                             "block_minutes": carry}
            known_total = totals.get(start, 0.0) + carry
            violation = known_total > max_min
            reason = "; ".join(dict.fromkeys(reasons))
            entry = lc._window(check, emp, start, finish, reason,
                               "violation" if violation else None if reason else "pass",
                               assumed_basis if not reason else None)
            entry["known_subtotal_minutes"] = known_total
            entry["total_complete"] = not bool(reason)
            if proof:
                entry["history_proof"] = proof
            if violation:
                entry["lower_bound_violation"] = bool(reason)
                sheet = "Назначения" if start in first_rows else "История"
                row_i = first_rows.get(start, history_rows.get(emp, 2))
                found.append(_finding(counter, check, sheet, row_i,
                    f"налёт {totals.get(start, 0.0):g} мин + остаток {carry:g} мин = "
                    f"{known_total:g} больше предела {max_min:g} мин за {start.strftime('%Y-%m')}"
                    + ("; полный итог неизвестен, нарушение доказано известным подытогом" if reason else ""),
                    f"{SOLUTION_SID}!{sheet}: Табельный номер=«{emp}», окно "
                    f"[{start.isoformat()}, {finish.isoformat()}), строка {row_i}"
                    + (f"; История: Налёт с начала месяца, мин=«{carry:g}»" if proof and carry else ""),
                    rid, lc.EMP))
    check["note"] = ("каждый сотрудник, включая неназначенных, и каждый пересекаемый календарный месяц; "
                     "нулевой префикс известен только при начале горизонта с первого числа")
    if histories:
        check["note"] += f"; история передана: остаток прибавлен у {carried} пар сотрудник × месяц"
    return lc._finish_windows(check, len(rows)), found


def _turnaround(rid, rule, dataset, counter, min_min: float):
    """Минимальная стыковка между соседними назначениями сотрудника
    внутри одной смены (колонка «Смена» листа «Назначения»)."""
    check = _check(rid, rule, "минимальная-стыковка-в-смене", "стыковка",
                   f"стыковка между рейсами одной смены не короче {min_min:g} мин "
                   f"— {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "min_minutes": min_min})
    got = _sheet(dataset, "Назначения")
    if got is None:
        check.update(status="не прогнана", rows_checked=0,
                     reason="в решении нет листа «Назначения»")
        return check, []
    rows, columns = got
    need = ("Табельный номер", "Смена", "Время вылета", "Время прилёта")
    missing = [c for c in need if c not in columns]
    if missing:
        check.update(status="не прогнана", rows_checked=0,
                     reason="у листа «Назначения» нет колонок: " + ", ".join(missing))
        return check, []
    groups: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for i, r in enumerate(rows, start=2):
        key = (str(r.get("Табельный номер") or ""), str(r.get("Смена") or ""))
        if not key[1]:
            continue
        groups.setdefault(key, []).append((i, r))
    found: list[dict] = []
    for (emp, duty), lst in groups.items():
        lst.sort(key=lambda x: str(x[1].get("Время вылета") or ""))
        for (i0, prev), (i1, nxt) in zip(lst, lst[1:]):
            a = _moment(prev.get("Время прилёта"))
            b = _moment(nxt.get("Время вылета"))
            if a is None or b is None:
                continue
            gap = (b - a).total_seconds() / 60.0
            if gap < min_min:
                found.append(_finding(
                    counter, check, "Назначения", i1,
                    f"стыковка {gap:g} мин короче минимума {min_min:g} мин",
                    f"{SOLUTION_SID}!строки {i0}→{i1}: Табельный номер=«{emp}», "
                    f"Смена=«{duty}», прилёт «{prev.get('Время прилёта', '')}», "
                    f"вылет «{nxt.get('Время вылета', '')}»", rid, "Время вылета"))
    check.update(status="прогнана", rows_checked=len(rows))
    return check, found


def _landings_per_duty(rid, rule, dataset, counter, max_n: float):
    """Посадок за смену — рабочие назначения смены без перегонов."""
    check = _check(rid, rule, "посадок-за-смену", "смена",
                   f"посадок за смену не более {max_n:g} (перегоны не считаются) "
                   f"— {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Назначения", "max_count": max_n})
    got = _sheet(dataset, "Назначения")
    if got is None:
        check.update(status="не прогнана", rows_checked=0,
                     reason="в решении нет листа «Назначения»")
        return check, []
    rows, columns = got
    if "Смена" not in columns:
        check.update(status="не прогнана", rows_checked=0,
                     reason="у листа «Назначения» нет колонки «Смена»")
        return check, []
    groups: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows, start=2):
        if str(r.get("Перегон") or "").strip().lower() == "да":
            continue
        key = (str(r.get("Табельный номер") or ""), str(r.get("Смена") or ""))
        if not key[1]:
            continue
        groups.setdefault(key, []).append(i)
    found: list[dict] = []
    for (emp, duty), idx in groups.items():
        if len(idx) > max_n:
            found.append(_finding(
                counter, check, "Назначения", idx[0],
                f"посадок за смену {len(idx)} больше предела {max_n:g}",
                f"{SOLUTION_SID}!строки {', '.join(map(str, idx))}: Табельный "
                f"номер=«{emp}», Смена=«{duty}»", rid, "Смена"))
    check.update(status="прогнана", rows_checked=len(rows))
    return check, found


def _pairing_days(rid, rule, dataset, counter, max_days: float):
    """Суток командировки (цепочки) — календарные дни от начала до конца."""
    check = _check(rid, rule, "суток-командировки", "цепочка",
                   f"командировка не длиннее {max_days:g} календарных суток "
                   f"— {str(rule.get('statement') or '')[:120]}",
                   {"sheet": f"{SOLUTION_SID}/Цепочки", "max_days": max_days})
    got = _sheet(dataset, "Цепочки")
    if got is None:
        check.update(status="не прогнана", rows_checked=0,
                     reason="в решении нет листа «Цепочки»")
        return check, []
    rows, columns = got
    found: list[dict] = []
    for i, r in enumerate(rows, start=2):
        a = _moment(r.get("Начало"))
        b = _moment(r.get("Конец"))
        if a is None or b is None:
            continue
        days = (b.date() - a.date()).days + 1
        if days > max_days:
            found.append(_finding(
                counter, check, "Цепочки", i,
                f"командировка {days} суток длиннее предела {max_days:g}",
                f"{SOLUTION_SID}!строка {i}: Идентификатор=«{r.get('Идентификатор', '')}», "
                f"Начало=«{r.get('Начало', '')}», Конец=«{r.get('Конец', '')}»", rid))
    check.update(status="прогнана", rows_checked=len(rows))
    return check, found


# ---------------------------------------------------------------- допуск на тип

#: Схема исходных листов книги для допуска на тип (книга Н0: «Рейсы» —
#: Номер рейса, Дата, Вылет, Время вылета, Прилёт, Время прилёта, Тип ВС;
#: «Допуски» — Табельный номер, Тип ВС, [Действует с], Действует до).
_FLEET_SOURCE_SCHEMA = (("Рейсы", {"Номер рейса", "Дата", "Время вылета", "Тип ВС"}),
                        ("Допуски", {"Табельный номер", "Тип ВС"}))


def _source_day(value):
    """Дата исходной книги — день, а не усечённая метка времени."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for form in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, form).date()
        except ValueError:
            pass
    raise ValueError(f"нечитаемая дата источника: {raw!r}")


def _fleet_sources(dataset: dict) -> dict:
    """Единственные исходные листы «Рейсы» и «Допуски» вне решения с
    нужной схемой: {имя: (sid, таблица)}; иначе ValueError словами."""
    source = {}
    for name, needed in _FLEET_SOURCE_SCHEMA:
        matches = [(sid, book[name]) for sid, book in (dataset.get("tables") or {}).items()
                   if sid != SOLUTION_SID and isinstance(book, dict) and name in book]
        if len(matches) != 1 or not isinstance(matches[0][1], dict):
            raise ValueError(f"нет единственного исходного листа «{name}»")
        sid, table = matches[0]
        if not needed.issubset(table.get("columns") or []) or not isinstance(table.get("rows"), list):
            raise ValueError(f"неполная схема исходного листа «{name}»")
        source[name] = (sid, table)
    return source


def _fleet_source_ready(dataset: dict) -> bool:
    """Есть ли у решения исходные листы допусков нужной схемы — тогда
    допуск на тип судится кодом; иначе правило остаётся пути main
    (лист «Допуски» решения и компилятор)."""
    try:
        _fleet_sources(dataset)
    except ValueError:
        return False
    return True


def _fleet_qualification(rid, rule, dataset, counter):
    """Допуск на тип по ИСХОДНОЙ книге на дату рейса источника (C-24).

    Тип судна и допуски — из замороженной исходной книги, не из показаний
    решателя. Пустые границы действия открыты по контракту книги;
    непустые нечитаемые — никогда не открыты. Нечитаемое / неоднозначное
    закрывает ВСЮ проверку («не прогнана», без находок); перегон — вне
    области (пассажир допуска не требует)."""
    check = _check(rid, rule, "допуск-на-тип-по-источнику", "назначение",
                   str(rule.get("statement") or "Действующий допуск на тип рейса"),
                   {"sheet": f"{SOLUTION_SID}/Назначения",
                    "date_basis": "дата рейса в исходной книге"})
    try:
        fit = rule.get("template_fit") or {}
        if fit.get("category") not in ("A", "B") or (rule.get("logic") or {}).get("scope"):
            raise ValueError("нет однозначной посадки без дополнительных условий")
        if set(fit.get("params") or {}) != {"fleet_types"}:
            raise ValueError("параметры допуска выходят за поддержанную форму fleet_types")
        record = _fit_record(rule, "fleet_types")
        value = record.get("value") if record is not None else None
        if fit.get("schema_version") == "landing/2":
            # The shared reader already validated list shape, pin, provenance
            # and carriers. Keep opaque labels intact, including their commas.
            fleet = set(value)
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("не задан явный перечень fleet_types")
            fleet = {item.strip() for item in value.split(",") if item.strip()}
        if not fleet:
            raise ValueError("пустой перечень fleet_types")
        check["params"]["fleet_types"] = sorted(fleet)
        source = _fleet_sources(dataset)
        flights = {}
        fsid, ftable = source["Рейсы"]
        for row_no, row in enumerate(ftable["rows"], 2):
            num, typ = str(row.get("Номер рейса") or "").strip(), str(row.get("Тип ВС") or "").strip()
            day = _source_day(row.get("Дата"))
            dep = _moment(f"{day.isoformat()}T{row.get('Время вылета')}" if day else "")
            if not num or not typ or dep is None:
                raise ValueError(f"{fsid}/Рейсы!{row_no}: неполная личность или тип рейса")
            key = (num, dep)
            if key in flights:
                raise ValueError(f"{fsid}/Рейсы!{row_no}: неоднозначная личность рейса")
            flights[key] = (typ, day, row_no)
        qsid, qtable = source["Допуски"]
        qualifications = {}
        for row_no, row in enumerate(qtable["rows"], 2):
            emp, typ = str(row.get("Табельный номер") or "").strip(), str(row.get("Тип ВС") or "").strip()
            lo, hi = _source_day(row.get("Действует с")), _source_day(row.get("Действует до"))
            if not emp or not typ or (lo is not None and hi is not None and hi < lo):
                raise ValueError(f"{qsid}/Допуски!{row_no}: некорректный допуск")
            qualifications.setdefault((emp, typ), []).append((lo, hi, row_no))
        got = _sheet(dataset, "Назначения")
        if got is None or not got[0] or not {"Табельный номер", "Рейс", "Время вылета"}.issubset(got[1]):
            raise ValueError("нет полного листа назначений")
        assessments, violations = [], []
        for row_no, row in enumerate(got[0], 2):
            emp = str(row.get("Табельный номер") or "").strip()
            flight = str(row.get("Рейс") or "").strip()
            dep = _moment(row.get("Время вылета"))
            number, separator, stamp = flight.partition("|")
            if not emp or dep is None or (separator and _moment(stamp) != dep):
                raise ValueError(f"SOL/Назначения!{row_no}: неполная или противоречивая личность назначения")
            item = flights.get((number, dep))
            if item is None:
                raise ValueError(f"SOL/Назначения!{row_no}: нет однозначного исходного рейса")
            typ, day, flight_row = item
            flag = str(row.get("Перегон") or "нет").strip().lower()
            if flag not in ("да", "нет", "true", "false", "1", "0", "yes", "no"):
                raise ValueError(f"SOL/Назначения!{row_no}: нечитаемый признак перегона")
            applicable = typ in fleet and flag not in ("да", "true", "1", "yes")
            windows = qualifications.get((emp, typ), [])
            valid = [qrow for lo, hi, qrow in windows
                     if (lo is None or lo <= day) and (hi is None or day <= hi)]
            assessments.append({"assignment_row": row_no, "employee": emp,
                                "flight": flight, "source_flight": f"{fsid}/Рейсы!{flight_row}",
                                "source_qualifications": f"{qsid}/Допуски",
                                "aircraft_type": typ, "flight_date": day.isoformat(),
                                "applicable": applicable, "valid_qualification_rows": valid})
            if applicable and not valid:
                violations.append((row_no, emp, flight, typ, day, flight_row))
        findings = [_finding(counter, check, "Назначения", row_no,
                            f"{emp}: нет действующего допуска на {typ} на дату рейса {day.isoformat()}",
                            f"SOL/Назначения!{row_no}: {flight}; {fsid}/Рейсы!{flight_row}; "
                            f"исходный справочник {qsid}/Допуски: действующее окно отсутствует", rid)
                    for row_no, emp, flight, typ, day, flight_row in violations]
        check.update(status="прогнана", rows_checked=len(got[0]),
                     qualification_assessments=assessments)
        return check, findings
    except (ValueError, TypeError, AttributeError) as exc:
        check.update(status="не прогнана", rows_checked=0, reason=str(exc))
        return check, []


#: Исходный лист недоступности членов экипажа (семья crew_unavailability,
#: каталог 0.6.0, 17.09.2026): «Отпуска» — Табельный номер, С, По (дни,
#: границы включительно; контракт книги приёмника COSMOS). Тот же лист
#: решатель читает в Employee.unavailableDays, сверка с ИДАК — видом
#: unavailability; здесь — третий исполнитель семьи, сертификат по ВМП.
_UNAVAILABILITY_SHEET = "Отпуска"
_UNAVAILABILITY_COLUMNS = {"Табельный номер", "С", "По"}


def _unavailability_source(dataset: dict) -> tuple[str, dict]:
    """Единственный исходный лист «Отпуска» вне решения с нужной схемой:
    (sid, таблица); иначе ValueError словами."""
    matches = [(sid, book[_UNAVAILABILITY_SHEET])
               for sid, book in (dataset.get("tables") or {}).items()
               if sid != SOLUTION_SID and isinstance(book, dict)
               and _UNAVAILABILITY_SHEET in book]
    if len(matches) != 1 or not isinstance(matches[0][1], dict):
        raise ValueError(f"нет единственного исходного листа «{_UNAVAILABILITY_SHEET}»")
    sid, table = matches[0]
    if not _UNAVAILABILITY_COLUMNS.issubset(table.get("columns") or []) \
            or not isinstance(table.get("rows"), list):
        raise ValueError(f"неполная схема исходного листа «{_UNAVAILABILITY_SHEET}»")
    return sid, table


def _day_of(moment):
    """День метки решения (datetime либо date) — для сравнения с днями
    исходной книги."""
    return moment.date() if hasattr(moment, "date") and callable(moment.date) else moment


def _crew_unavailability(rid, rule, dataset, counter):
    """Недоступность члена экипажа по ИСХОДНОЙ книге (семья
    crew_unavailability): назначение, у которого день вылета или день
    прилёта попадает в интервал [С, По] сотрудника из листа «Отпуска», —
    нарушение. Семья без параметров: форму задаёт код, число — строки
    книги.

    Первый проход Н0-С через COSMOS (17.09.2026): решатель исполнил
    ограничение «Employee unavailable», сверка с ИДАК прошла, а сертификат
    остался «не удостоверено: проверок не скомпилировано 1» — компилятору
    проверок интервал двух колонок не выразить («действительность-на-дату»
    знает одну колонку). Отсюда исполнитель кодом.

    Fail-closed: нечитаемая дата, обратный интервал, отсутствие или
    неоднозначность исходного листа, нечитаемое назначение — вся проверка
    «не прогнана» с причиной, без находок (правило остаётся непокрытым,
    сертификат не выдаётся). Пустой лист «Отпуска» — законное «никто не
    недоступен»: проверка прогнана по всем назначениям без находок.
    Перегон (пассажир) от недоступности не освобождает: человек в отпуске
    не летит и пассажиром по наряду."""
    check = _check(rid, rule, "недоступность-по-источнику", "назначение",
                   str(rule.get("statement") or "Назначений в недоступные дни нет"),
                   {"sheet": f"{SOLUTION_SID}/Назначения",
                    "source_sheet": _UNAVAILABILITY_SHEET,
                    "date_basis": "дни вылета и прилёта назначения; границы "
                                  "интервала «С»–«По» включительно"})
    try:
        fit = rule.get("template_fit") or {}
        if fit.get("category") not in ("A", "B") or (rule.get("logic") or {}).get("scope"):
            raise ValueError("нет однозначной посадки без дополнительных условий")
        _fit_records(rule)  # Parameterless landing still owns a contract/pin.
        sid, table = _unavailability_source(dataset)
        windows: dict[str, list] = {}
        for row_no, row in enumerate(table["rows"], 2):
            emp = str(row.get("Табельный номер") or "").strip()
            lo, hi = _source_day(row.get("С")), _source_day(row.get("По"))
            if not emp or lo is None or hi is None or hi < lo:
                raise ValueError(f"{sid}/{_UNAVAILABILITY_SHEET}!{row_no}: "
                                 f"некорректный интервал недоступности")
            windows.setdefault(emp, []).append((lo, hi, row_no))
        got = _sheet(dataset, "Назначения")
        if got is None or not {"Табельный номер", "Рейс", "Время вылета"}.issubset(got[1]):
            raise ValueError("нет полного листа назначений")
        has_arr = "Время прилёта" in got[1]
        assessments, violations = [], []
        for row_no, row in enumerate(got[0], 2):
            emp = str(row.get("Табельный номер") or "").strip()
            flight = str(row.get("Рейс") or "").strip()
            dep = _moment(row.get("Время вылета"))
            arr_raw = str(row.get("Время прилёта") or "").strip() if has_arr else ""
            arr = _moment(arr_raw) if arr_raw else None
            if not emp or dep is None or (arr_raw and arr is None):
                raise ValueError(f"SOL/Назначения!{row_no}: неполная или нечитаемая "
                                 f"личность назначения")
            days = sorted({_day_of(dep)} | ({_day_of(arr)} if arr is not None else set()))
            hits = [wrow for lo, hi, wrow in windows.get(emp, [])
                    if any(lo <= d <= hi for d in days)]
            assessments.append({"assignment_row": row_no, "employee": emp,
                                "flight": flight,
                                "days": [d.isoformat() for d in days],
                                "source_unavailability": f"{sid}/{_UNAVAILABILITY_SHEET}",
                                "unavailability_rows": [w for _lo, _hi, w in windows.get(emp, [])],
                                "violating_rows": hits})
            if hits:
                violations.append((row_no, emp, flight, days, hits))
        findings = [_finding(counter, check, "Назначения", row_no,
                            f"{emp}: назначение на рейс {flight} в недоступный день "
                            f"({', '.join(d.isoformat() for d in days)})",
                            f"SOL/Назначения!{row_no}: {flight}; {sid}/{_UNAVAILABILITY_SHEET}!"
                            f"{', '.join(str(w) for w in hits)}: интервал недоступности "
                            f"сотрудника", rid)
                    for row_no, emp, flight, days, hits in violations]
        check.update(status="прогнана", rows_checked=len(got[0]),
                     unavailable_employees=len(windows),
                     unavailability_assessments=assessments)
        return check, findings
    except (ValueError, TypeError, AttributeError) as exc:
        check.update(status="не прогнана", rows_checked=0, reason=str(exc))
        return check, []


# ---------------------------------------------------------------- сборка

def fit_code_checks(svod: dict, dataset: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    """(спецификации словаря, проверки кодом, находки, свод без взятых
    правил). Правило берётся, когда у него посадка A/B с параметром
    семьи, у которого здесь есть форма; остальные — компилятору."""
    specs: list[dict] = []
    checks: list[dict] = []
    findings: list[dict] = []
    taken: set[str] = set()
    counter = _Counter()
    from vmp.run import logic_checks as _lgc
    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or r.get("kind") not in ("HARD", "CASE"):
            continue
        rid = str(r.get("id"))
        fam = _family(r)
        if fam in ({"crew_composition", "duty_limits", "rest_floors",
                    "connection_windows", "span_accumulators", "fleet_exclusions",
                    "crew_unavailability"} | _lgc.FAMILY_FORMS.keys()):
            try:
                _fit_records(r)
            except (ValueError, TypeError, AttributeError) as exc:
                check = _check(rid, r, "параметры-посадки", "правило",
                               str(r.get("statement") or ""), {})
                check.update(status="не прогнана", rows_checked=0, reason=str(exc))
                checks.append(check)
                taken.add(rid)
                continue
        if fam == "crew_composition":
            n = fit_param(r, "crew_size")
            if n is None or n < 1:
                continue
            specs.append({
                "id": f"C-{rid}-посадка", "basis": {"rule": rid},
                "template": "счёт-состава-группы",
                # Действующие назначения — без перегонных строк («Перегон =
                # да» — пилот пассажиром, в состав экипажа не входит);
                # единый план 05.09.2026, п. 5.12.
                "params": {"sheet": f"{SOLUTION_SID}/Действующие назначения",
                           "group_column": "Рейс", "count": int(n),
                           "distinct_column": "Табельный номер"},
                "unit": "рейс", "severity": SEVERITY,
                "status": "скомпилирована", "compiled_by": COMPILED_BY,
                "statement": f"на каждом рейсе ровно {int(n)} различных членов "
                             f"экипажа (по табельному номеру) — "
                             f"{str(r.get('statement') or '')[:120]}"})
            taken.add(rid)
        elif fam == "duty_limits":
            span = fit_param(r, "max_duty_duration")
            landings = fit_param(r, "max_landings_per_duty")
            days = fit_param(r, "max_pairing_days")
            if span is not None and span > 0:
                specs.append({
                    "id": f"C-{rid}-посадка", "basis": {"rule": rid},
                    "template": "максимальная-длительность-часов",
                    "params": {"sheet": f"{SOLUTION_SID}/Смены",
                               "start_column": "Начало", "end_column": "Конец",
                               "max_hours": float(span) / 60.0},
                    "unit": "смена", "severity": SEVERITY,
                    "status": "скомпилирована", "compiled_by": COMPILED_BY,
                    "statement": f"смена от начала до конца не длиннее {span:g} мин "
                                 f"— {str(r.get('statement') or '')[:120]}"})
                taken.add(rid)
            elif landings is not None and landings >= 1:
                c, f = _landings_per_duty(rid, r, dataset, counter, landings)
                checks.append(c); findings.extend(f); taken.add(rid)
            elif days is not None and days >= 1:
                c, f = _pairing_days(rid, r, dataset, counter, days)
                checks.append(c); findings.extend(f); taken.add(rid)
        elif fam == "rest_floors":
            home = fit_param(r, "min_rest_home")
            away = fit_param(r, "min_rest_outstation")
            if home is not None and home > 0:
                c, f = _rest_floor(rid, r, dataset, counter, "home", home)
                checks.append(c); findings.extend(f); taken.add(rid)
            elif away is not None and away > 0:
                c, f = _rest_floor(rid, r, dataset, counter, "outstation", away)
                checks.append(c); findings.extend(f); taken.add(rid)
        elif fam == "connection_windows":
            sit = fit_param(r, "min_turnaround")
            if sit is not None and sit > 0:
                c, f = _turnaround(rid, r, dataset, counter, sit)
                checks.append(c); findings.extend(f); taken.add(rid)
        elif fam == "span_accumulators":
            mb = fit_param(r, "max_monthly_block")
            if mb is not None and mb > 0:
                c, f = _monthly_block(rid, r, dataset, counter, mb)
                checks.append(c); findings.extend(f); taken.add(rid)
        elif fam == "fleet_exclusions":
            # Допуск на тип — первая ступень посадки A/B: кодом по
            # исходной книге (детерминирован, fail-closed), когда
            # исходные листы нужной схемы переданы; иначе — путь main
            # (лист «Допуски» решения, шаблон «действительность-на-дату»
            # компилятора).
            if _fleet_source_ready(dataset):
                c, f = _fleet_qualification(rid, r, dataset, counter)
                checks.append(c); findings.extend(f); taken.add(rid)
        elif fam == "crew_unavailability":
            # Недоступность членов экипажа (семья 0.6.0, 17.09.2026):
            # исполнитель сертификата кодом по исходному листу «Отпуска».
            # Правило берётся ВСЕГДА (в отличие от допуска на тип): у
            # компилятора проверок формы для интервала двух колонок нет,
            # и без исходного листа честнее «не прогнана» с причиной, чем
            # «проверок не скомпилировано».
            c, f = _crew_unavailability(rid, r, dataset, counter)
            checks.append(c); findings.extend(f); taken.add(rid)
        elif fam in _lgc.FAMILY_FORMS:
            # Семьи истории — ИЗ КОДА ПРОВЕРОК (logic_checks.FAMILY_FORMS),
            # не перечнем здесь: первая семья конвейера пополнения
            # (duty_series_limit, 0.4.0, 09.09.2026) в перечне отсутствовала,
            # и сертификат оставлял правило непроверенным («проверок не
            # скомпилировано 1») при исполнившем его решателе.
            # Семьи истории сотрудника (решение владельца 05.09.2026,
            # путь А): та же форма, что у решателя, — исполнитель
            # сертификата кодом по посадке, с остатками листа «История»
            # (vmp/run/logic_checks); окно ночи — в шкале меток тем же
            # сдвигом, что у решателя (посадка правила шкалы времени).
            from vmp.run import logic_checks as _lgc
            tz = 0
            time_problem = None
            for r2 in svod.get("rules") or []:
                if isinstance(r2, dict) and _family(r2) == "time_scale":
                    try:
                        record = _fit_record(r2, "base_tz_offset_min")
                        if record is None:
                            continue
                        v = fit_param(r2, "base_tz_offset_min")
                        if v is None:
                            raise ValueError("base_tz_offset_min не конечное число")
                        tz = int(v)
                        break
                    except (ValueError, TypeError, AttributeError) as exc:
                        time_problem = str(exc)
                        break
            if time_problem and fam == "night_series":
                c = _check(rid, r, "параметры-посадки", "правило",
                           str(r.get("statement") or ""), {})
                c.update(status="не прогнана", rows_checked=0,
                         reason="шкала времени не установлена: " + time_problem)
                checks.append(c)
                taken.add(rid)
                continue
            plan = _lgc.plan_from_fit(fam, r, base_tz_offset_min=tz)
            if plan is not None:
                c, f = _lgc.run_plan(rid, r, plan, dataset, counter)
                checks.append(c); findings.extend(f); taken.add(rid)
    if not taken:
        return [], [], [], svod
    out = dict(svod)
    out["rules"] = [r for r in svod.get("rules") or []
                    if str(r.get("id")) not in taken]
    return specs, checks, findings, out
