# -*- coding: utf-8 -*-
"""Сертификатор решения (мост CDPM → COSMOS, ступень 1; отмашка
владельца 2026-08-22).

Проверка ГОТОВОГО решения решателя (ростера) против ВСЕГО свода
CDPM пост-фактум: «соблюдать правило в поиске» дорого и требует кода
в решателе, «проверить правило на готовом решении» дёшево — одна
детерминированная проверка. Ни одно правило не теряется молча:
каждое проверяемое правило свода получает ровно один исход —
«прогнана (нарушений N)» либо «не прогнана: причина».

Механизм переиспользует линию данных (ДЛ-2): решение превращается в
таблицы, модель-КОМПИЛЯТОР предлагает спецификацию проверки по
закрытому перечню шаблонов, КОД валидирует и исполняет (fail-closed).

Входной контракт решения (JSON):
    {"solution": "<имя решения>",
     "assignments": [{"employee","flight","dep_time","arr_time",
                      "dep_airport"?, "arr_airport"?, "date"?,
                      "role"?, "base"?, "duty_id"?}, ...],
     "duties":   [{"employee","id"?,"start","end"}, ...] # опционально
     "pairings": [{"employee","id","start","end"}, ...]  # опционально
    }
Поля base (база приписки) и duty_id (привязка назначения к смене,
той же нарезкой, что у решателя) открывают проверки области
(в базе / вне базы) и состава экипажа (4б моста, 2026-08-23).
Поле deadhead (перегон: сотрудник летит пассажиром) едет колонкой
«Перегон» («да»/«нет») у КАЖДОЙ строки, если объявлено хоть у одной;
смены могут нести extension_consent («да»/«нет») — явное согласие на
продление смены для адресного исключения (аудит A8, 04.09.2026).
Времена — ISO («2026-01-15T06:00») либо «дд.мм.гггг чч:мм». Смены и
цепочки сертификатор НЕ вычисляет сам (границы смен — интерпретация,
её даёт решатель/оркестратор); правила о сменах без переданной
разметки честно получают «не прогнана: в решении нет разметки смен».

Аудит 04.09.2026, находка A8 (класс ошибки — итог сильнее
доказательства): сертификат допустимости выдаётся ТОЛЬКО при полном
покрытии — у каждого жёсткого правила ВСЕ его проверки прогнаны (либо
правило законно исключено «до получения данных» / параметр мира),
нескомпилированных проверок нет, нарушений нет, решатель и показания
не спорят. Иначе допустимость «не установлена» — третий исход,
отличный и от «допустимо», и от «недопустимо» (vmp.run.verdicts).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import timezone
from pathlib import Path

from cdpm import data_checks as dc
from cdpm import data_report as dr

SOLUTION_SID = "SOL"

# Поля-времена контракта: ISO с таймзоной (Z или смещение)
# нормализуются в UTC без суффикса — иначе разбор линии данных честно
# отказал бы и форматный дефект лёг бы «нарушением правила» (ревью
# ступени 1, 2026-08-22).
_TIME_KEYS = ("dep_time", "arr_time", "start", "end")


class CertifyError(ValueError):
    """Сертификация не состоялась; причина названа."""


def _validate_typed_landing(rule: dict, *, unconditional: tuple[str, ...] = ()) -> None:
    """Validate the current carrier before reading its canonical values.

    Typed scalar values and extension lists need no string conversion here.
    They still require the shared pin/shape/carrier checks; legacy inputs
    keep their established reader behaviour.
    """
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and "schema_version" in fit:
        from webapp.threshold_gate import projected_params, ThresholdGateError
        try:
            projected = projected_params(rule)
        except ThresholdGateError as exc:
            raise CertifyError(str(exc)) from exc
        for param in projected:
            if param.get("name") in unconditional and (
                    param.get("condition") is not None or param.get("qualifier") is not None):
                raise CertifyError(f"{rule.get('id')}: {param['name']}: условный параметр "
                                   "нельзя применять как безусловную норму")


def _norm_time(value):
    s = str(value).strip()
    if not s or ("+" not in s[10:] and not s.endswith("Z")):
        return s
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s          # не ISO — пусть честно откажет разбор ниже
    return dt.astimezone(timezone.utc).replace(tzinfo=None) \
        .isoformat(timespec="minutes")


def _rows(items, name: str,
          mapping: list[tuple[str, str]]) -> dict:
    if not isinstance(items, list):
        raise CertifyError(f"{name}: не список, а "
                           f"{type(items).__name__}")
    columns = [ru for ru, _k in mapping]
    rows = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise CertifyError(f"{name}[{i}]: запись не объект: "
                               f"{it!r}")
        row = {}
        for ru, k in mapping:
            v = it.get(k)
            s = "" if v is None else str(v).strip()
            row[ru] = _norm_time(s) if k in _TIME_KEYS else s
        rows.append(row)
    # Колонка, пустая во ВСЕХ строках, — это ОТСУТСТВУЮЩЕЕ поле
    # контракта, а не данные: материализованная пустой она выглядит
    # для компилятора настоящей, проверка по ней прогоняется и
    # рождает ложные нарушения «значение "" вне перечня» (живой
    # случай: 12 ложных на нано, вход без аэропортов, 2026-08-22).
    # Без колонки компилятор честно отвечает «не прогнана: нет
    # данных».
    empty = [ru for ru in columns
             if all(not r[ru] for r in rows)]
    if empty:
        columns = [ru for ru in columns if ru not in empty]
        for r in rows:
            for ru in empty:
                r.pop(ru, None)
    return {"columns": columns, "rows": rows}


def _consent_flag(value) -> str:
    """Факт согласия: неизвестное не равно явному отказу от согласия."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("да", "true"):
            return "да"
        if word in ("нет", "false"):
            return "нет"
    raise CertifyError("extension_consent: требуется явное true/false или «да»/«нет»")


def _materialize_flag(items: list, key: str) -> None:
    """Прежние флаги (включая deadhead) полны по колонке. Согласие
    на продление — отдельный явный факт: отсутствие не создаёт «нет»."""
    if not any(isinstance(it, dict) and key in it for it in items):
        return
    for it in items:
        if isinstance(it, dict):
            v = it.get(key)
            if key == "extension_consent":
                try:
                    flag = _consent_flag(v)
                except CertifyError as exc:
                    raise CertifyError(f"смена {it.get('id', '?')}, сотрудник "
                                       f"{it.get('employee', '?')}: {exc}") from exc
                if flag:
                    it[key] = flag
                continue
            it[key] = "да" if (v is True or str(v).strip().lower()
                               in ("да", "true", "1", "yes")) else "нет"


#: Поля контракта решения, которые ОБЯЗАНЫ доехать колонкой до таблиц
#: сертификатора, если объявлены хоть у одной записи: (ключ решения →
#: (список решения, лист, колонка)). Проверка полноты входа — против
#: класса ошибки «поле контракта потеряно между выгрузкой и таблицей».
_CONTRACT_COLUMNS = (
    ("deadhead", "assignments", "Назначения", "Перегон"),
    ("base", "assignments", "Назначения", "База приписки"),
    ("duty_id", "assignments", "Назначения", "Смена"),
    ("role", "assignments", "Назначения", "Роль"),
    ("extension_consent", "duties", "Смены", "Согласие на продление"),
    ("base", "duties", "Смены", "База приписки"),
)


def verify_input_completeness(solution: dict, dataset: dict) -> None:
    """Полнота входа сертификатора (аудит A8, 04.09.2026): поле
    контракта, объявленное в решении, обязано присутствовать колонкой.
    Согласие сохраняется построчно, включая неизвестное; прочие флаги
    полны по колонке. Потеря — отказ, не сертификация «без перегонов»."""
    sheets = (dataset.get("tables") or {}).get(SOLUTION_SID) or {}
    lost: list[str] = []
    for key, lst, sheet, column in _CONTRACT_COLUMNS:
        items = solution.get(lst) if isinstance(solution, dict) else None
        if key == "extension_consent" and isinstance(items, list):
            expected = [_consent_flag(it.get(key)) for it in items
                        if isinstance(it, dict)]
            table = sheets.get(sheet) or {}
            rows = table.get("rows") or []
            actual = [str(r.get(column) or "").strip() for r in rows]
            if expected != actual or (any(expected) and column not in table.get("columns", [])):
                lost.append(f"{sheet}: колонка «{column}» (поле {key}) "
                            "не сохраняет явные и неизвестные факты строк")
            continue
        if not isinstance(items, list) or not any(
                isinstance(it, dict) and key in it
                and str(it.get(key) or "").strip() for it in items):
            continue
        table = sheets.get(sheet) or {}
        if column not in (table.get("columns") or []):
            lost.append(f"{sheet}: колонка «{column}» (поле {key})")
            continue
        if key in ("deadhead", "extension_consent") and any(
                str(r.get(column) or "").strip() not in ("да", "нет")
                for r in table.get("rows") or []):
            lost.append(f"{sheet}: колонка «{column}» (поле {key}) не "
                        f"заполнена у каждой строки")
    if lost:
        raise CertifyError(
            "вход сертификатора неполон — признак контракта потерян "
            "между решением и таблицами: " + "; ".join(lost))


def build_solution_dataset(solution: dict,
                           reference_tables: dict | None = None) -> dict:
    """Решение → набор таблиц формата линии данных. Обязательные поля
    назначений проверяются fail-closed; пустое решение — отказ."""
    if not isinstance(solution, dict):
        raise CertifyError("решение не объект JSON")
    assignments = solution.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise CertifyError("в решении нет назначений (assignments) — "
                           "сертифицировать нечего")
    for i, a in enumerate(assignments):
        if not isinstance(a, dict):
            raise CertifyError(f"assignments[{i}]: не объект")
        for field in ("employee", "flight", "dep_time", "arr_time"):
            v = a.get(field)
            if v is None or not str(v).strip():
                raise CertifyError(
                    f"assignments[{i}]: обязательное поле "
                    f"{field!r} пусто")
    # Дата назначения = дата вылета: решатель несёт dep_time, а
    # компилятор строит «действительность-на-дату» по колонке «Дата»
    # — без неё допуск по типу не проверялся («колонки даты в листе
    # назначений нет», сертификат ростеринга H0 03.09).
    for a in assignments:
        if not str(a.get("date") or "").strip():
            dep = str(a.get("dep_time") or "")
            if len(dep) >= 10:
                a["date"] = dep[:10]
    # Признак перегона (аудит A8, 04.09.2026): solution_export ставит
    # deadhead=True только у перегонных строк, а таблица копировала
    # лишь перечисленные поля — колонка перегона терялась, и пассажир
    # становился пилотом для проверок состава/налёта/допусков. Если
    # признак объявлен хоть у одной строки, он материализуется у
    # КАЖДОЙ («да»/«нет»); без объявления колонки нет (отсутствующее
    # поле — не данные).
    _materialize_flag(assignments, "deadhead")
    duties = solution.get("duties")
    if isinstance(duties, list):
        _materialize_flag([d for d in duties if isinstance(d, dict)],
                          "extension_consent")
    sheets: dict = {
        "Назначения": _rows(assignments, "assignments", [
            ("Табельный номер", "employee"), ("Рейс", "flight"),
            ("Дата", "date"),
            ("Аэропорт вылета", "dep_airport"),
            ("Аэропорт прилёта", "arr_airport"),
            ("Время вылета", "dep_time"),
            ("Время прилёта", "arr_time"), ("Роль", "role"),
            # 4б моста: база приписки и привязка к смене — для
            # проверок области (в базе/вне базы) и состава экипажа.
            ("База приписки", "base"), ("Смена", "duty_id"),
            ("Перегон", "deadhead")]),
    }
    # Производный лист «Действующие назначения» — строки без перегона
    # (единый план 05.09.2026, п. 5.12, согласовано с CDPM): правило
    # состава экипажа говорит о действующих, «Перегон = да» — поле
    # контракта решения COSMOS (пилот летит пассажиром), фильтр — свойство
    # представления решения, а не параметр правила. Семья crew_composition
    # (и кредитные часы) считаются по этому листу; исполнитель
    # «счёт-состава-группы» у CDPM остаётся общим. Без объявленного
    # признака лист совпадает с «Назначениями».
    active = [a for a in assignments
              if str(a.get("deadhead") if a.get("deadhead") is not None else "").strip().lower()
              not in ("да", "true", "1", "yes")]
    sheets["Действующие назначения"] = _rows(active, "assignments", [
        ("Табельный номер", "employee"), ("Рейс", "flight"), ("Дата", "date"),
        ("Аэропорт вылета", "dep_airport"), ("Аэропорт прилёта", "arr_airport"),
        ("Время вылета", "dep_time"), ("Время прилёта", "arr_time"),
        ("Роль", "role"), ("База приписки", "base"), ("Смена", "duty_id")])
    if solution.get("duties"):
        sheets["Смены"] = _rows(solution["duties"], "duties", [
            ("Табельный номер", "employee"),
            ("Идентификатор", "id"),
            ("Начало", "start"), ("Конец", "end"),
            # 4г моста: место отдыха после смены (аэропорт прилёта
            # последнего рейса) и база — отдых дома/вне дома
            # становится прогоняемым по сменам.
            ("База приписки", "base"),
            ("Место отдыха", "last_arr_airport"),
            # A8: явное согласие на продление смены — условие
            # адресного исключения (потолок продления).
            ("Согласие на продление", "extension_consent")])
    if solution.get("pairings"):
        sheets["Цепочки"] = _rows(solution["pairings"], "pairings", [
            ("Табельный номер", "employee"),
            ("Идентификатор", "id"),
            ("Начало", "start"), ("Конец", "end")])
    if isinstance(solution.get("history"), list):
        # Ревизия 1.17 (ступень L): остатки на начало периода — лист
        # «История»; проверки полов отдыха и накопителей читают его
        # кодом (vmp/run/fit_checks). Сотрудник без истории — строка с
        # «нет данных» = «нет», не ноль.
        _materialize_flag([r for r in solution["history"] if isinstance(r, dict)],
                          "has_history")
        sheets["История"] = _rows(solution["history"], "history", [
            ("Табельный номер", "employee"),
            ("Срез", "as_of"),
            ("Есть история", "has_history"),
            ("Налёт с начала месяца, мин", "month_block_min"),
            ("Рабочее время с начала месяца, мин", "month_work_min"),
            ("Полётное время с начала месяца, мин", "month_flight_time_min"),
            ("Рабочих дней с начала месяца", "month_days_worked"),
            ("Посадок с начала месяца", "month_landings"),
            ("Конец последней смены", "last_duty_end"),
            ("Место последней смены", "last_duty_station"),
            ("Последняя смена ночная", "last_duty_night"),
            ("Отдых до горизонта, мин", "rest_before_horizon_min"),
            ("Рабочих дней подряд", "consecutive_duty_days"),
            ("Ночных смен подряд", "consecutive_night_duties"),
            ("Дней с последнего выходного", "days_since_day_off"),
            ("Местоположение", "location")])
    if isinstance(solution.get("history_calendar"), list):
        # Ревизия 1.17.2: посуточный календарь до горизонта — лист
        # «Календарь истории»; исполнители счётчиков окон судят по нему
        # окна через границу горизонта (vmp/run/logic_checks).
        _materialize_flag([r for r in solution["history_calendar"] if isinstance(r, dict)],
                          "night")
        sheets["Календарь истории"] = _rows(solution["history_calendar"], "history_calendar", [
            ("Табельный номер", "employee"),
            ("Дата", "date"), ("Вид", "kind"), ("Ночная", "night")])
    if solution.get("qualifications"):
        # Свежесть допусков (обе половины правила 52 закрыты COSMOS
        # 2026-08-23): лист «Допуски» с датированными окнами — шаблон
        # «действительность-на-дату» дождался данных. Даты, не
        # минуты: сертификатору удобнее, «Действует до» — конец
        # суток включительно (чтение конвертера).
        sheets["Допуски"] = _rows(
            solution["qualifications"], "qualifications", [
                ("Табельный номер", "employee"),
                ("Тип ВС", "type"),
                ("Действует с", "valid_from"),
                ("Действует до", "valid_to")])
    if solution.get("reserve"):
        # Лист «Резерв» (заявка моста 2026-08-23): по строке на пару
        # (база, сутки) от решателя — правила о нормах резерва
        # становятся прогоняемыми. free-словарь ролей расплющивается.
        flat = []
        for r in solution["reserve"]:
            if not isinstance(r, dict):
                raise CertifyError("reserve: запись не объект")
            free = r.get("free") or {}
            flat.append({
                "base": r.get("base"), "day": r.get("day"),
                "free_captain": free.get("Captain"),
                "free_first_officer": free.get("FirstOfficer"),
                "crews": r.get("crews"), "norm": r.get("norm"),
                "shortfall": r.get("shortfall"),
                "employees_of_base": r.get("employees_of_base")})
        sheets["Резерв"] = _rows(flat, "reserve", [
            ("База", "base"), ("Дата", "day"),
            ("Свободно командиров", "free_captain"),
            ("Свободно вторых", "free_first_officer"),
            ("Экипажей", "crews"), ("Норма", "norm"),
            ("Нехватка", "shortfall"),
            ("Людей базы", "employees_of_base")])
    if isinstance(solution.get("cost_history_intervals"), list):
        # Контракт Б (C-9, план §3.10.3 K-1): явные интервалы
        # местоположения до/после назначений — доказательство ночёвок и
        # отдыха вместо домыслов; лист «История местоположений» — только
        # канонические интервалы (одна станция на интервал). У Н0 таких
        # данных нет — лист пуст, вклад «неизвестен» по замыслу.
        sheets["История местоположений"] = _rows(
            solution["cost_history_intervals"], "cost_history_intervals", [
                ("Табельный номер", "employee"), ("Начало", "start"),
                ("Конец", "end"), ("Станция", "station")])
    rows_seen = sum(len(t["rows"]) for t in sheets.values())
    # Отпечаток решения: контракт отчёта 3.3 требует SHA источника
    # («данные не заморожены» иначе) — хеш детерминирован от
    # содержимого, каким бы путём решение ни пришло (файл, веб).
    sha = hashlib.sha256(json.dumps(
        solution, ensure_ascii=False, sort_keys=True)
        .encode("utf-8")).hexdigest()
    sources = [{"sid": SOLUTION_SID,
                "file": str(solution.get("solution") or "решение"),
                "sha256": sha, "rows_seen": rows_seen}]
    tables: dict = {SOLUTION_SID: sheets}
    # Справочники ВХОДНОЙ книги — рядом с решением, своим sid: правило
    # о допуске на тип судна судит назначение против справочника
    # допусков и типа рейса, и без книги компилятор честно отвечал
    # «данных о типе ВС и допусках нет» (первые сертификаты H0,
    # 03.09). Листы книги остаются под именами книги; их sid не
    # совпадает с sid решения, так что одноимённые листы не спорят.
    for sid, book in (reference_tables or {}).items():
        if not isinstance(book, dict) or str(sid) == SOLUTION_SID:
            continue
        clean = {name: t for name, t in book.items()
                 if isinstance(t, dict) and isinstance(t.get("rows"), list)
                 and isinstance(t.get("columns"), list)}
        if not clean:
            continue
        tables[str(sid)] = clean
        sources.append({
            "sid": str(sid), "file": "входная книга данных пакета",
            "sha256": hashlib.sha256(json.dumps(
                clean, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")).hexdigest(),
            "rows_seen": sum(len(t["rows"]) for t in clean.values())})
    dataset = {"sources": sources, "tables": tables}
    # Область учёта (контракт Б, 05.09.2026): что у этого решения
    # неопределённо для меры ночёвок (пейринг: отдых на любой базе и
    # конец цепочки; ростеринг: только граница горизонта).
    if isinstance(solution.get("accounting"), dict):
        dataset["accounting"] = dict(solution["accounting"])
    # Политика окон через границу горизонта — решение пользователя по
    # вопросу истории (раздел history 1.17.2), см. logic_checks._rolling_rest.
    if isinstance(solution.get("history_policy"), dict):
        dataset["history_policy"] = dict(solution["history_policy"])
    # Объявленная область учёта (H11/C-10, контракт Б, единая версия
    # 06.09.2026): объявленный горизонт проверок окон, состав сотрудников
    # (включая незадействованных), контекст цены, запись решателя о
    # ночёвках и интервалы местоположений едут в набор как есть — их
    # читают исполнители окон (vmp/run/logic_checks), меры ночёвок,
    # cost_scope (независимая оценка ночёвок) и cost_comparison (паритет
    # событий); verify_input_completeness лишних ключей не отвергает.
    for key in ("window_horizon", "window_employees", "cost_context",
                "solver_layover_cost", "cost_history_intervals"):
        if key in solution:
            dataset[key] = solution[key]
    verify_input_completeness(solution, dataset)
    return dataset


def _check_thresholds(thresholds):
    """Показания порогов решателя (lastRun.thresholds того же
    прогона): валидация fail-closed — сертификат не вправе нести
    неразборчивый машинный раздел."""
    if not isinstance(thresholds, dict):
        raise CertifyError("thresholds: не объект JSON, а "
                           f"{type(thresholds).__name__}")
    for key in ("applied", "ignored", "literalsInForce"):
        items = thresholds.get(key, [])
        if not isinstance(items, list) or any(
                not isinstance(x, dict) for x in items):
            raise CertifyError(f"thresholds.{key}: не список "
                               f"объектов")
    off = thresholds.get("offLiterals", {})
    if not isinstance(off, dict):
        raise CertifyError("thresholds.offLiterals: не объект")
    return {"applied": thresholds.get("applied", []),
            "ignored": thresholds.get("ignored", []),
            "literals_in_force": thresholds.get("literalsInForce",
                                                []),
            "off_literals": off}


_RUN_TRACE_KEYS = ("package_fingerprint", "rules_profile",
                   "source_run", "spec_version", "mode", "score")


def _hard_of(run_info) -> int | None:
    """Жёсткая часть счёта решателя из шапки прогона (например,
    «-29273hard/-509799soft») либо None."""
    import re as _re
    s = (run_info or {}).get("score")
    mm = _re.search(r"(-?\d+)\s*hard", str(s or ""))
    return int(mm.group(1)) if mm else None


def certify(svod: dict, solution: dict, llm,
            control=None, thresholds=None, run_info=None,
            logic_report=None, strict_coverage: bool = True,
            penalty_reconciliation: dict | None = None) -> dict:
    """Сертификат решения: отчёт контракта 3.3 (checks + findings +
    counters) + сертификационная шапка с честным вердиктом. Шапка несёт
    трёхчастный результат (certificate.result, vmp.run.verdicts):
    допустимость подтверждена / нарушена / не установлена, корректность
    стоимости, качество оптимизации, полнота покрытия. certified=True
    ⇔ допустимость подтверждена (только при ПОЛНОМ покрытии) И сверка
    стоимости, если проводилась, сошлась (vmp.run.verdicts.certified_of).

    thresholds (опционально) — lastRun.thresholds того же прогона
    решателя: applied[] и ignored[] входят в сертификат машинным
    разделом «формализовано, решателем не исполнялось» (мост, пункт
    (в)). run_info (опционально) — шапка прогона для трассировки:
    отпечаток пакета, профиль, source_run, spec_version, режим.
    logic_report (опционально, пункт 19 моста) — lastRun.logic того
    же прогона: пять корзин показаний решателя по правилам с logic;
    судятся гейтом ступени 3 (webapp.logic_gate) — дефекты гейта
    отзывают сертификат допустимости. Показания могут ехать и внутри
    решения ключом lastRunLogic.

    strict_coverage: строгое покрытие (Д4 плана «прямой ход») — с
    аудита A8 (04.09.2026) ЕДИНСТВЕННЫЙ режим: «чист» требует, чтобы у
    КАЖДОГО нормативного правила были прогнаны ВСЕ его проверки либо
    правило законно исключено («исключено до получения данных» по
    решению авиакомпании, параметр мира). Прежний нестрогий режим
    («нарушений нет по прогнанным = certified») закреплял класс ошибки
    «итог сильнее доказательства» и снят: strict_coverage=False —
    отказ словами, не тихое ослабление.

    penalty_reconciliation (Д4, пункт 3; спецификация COSMOS 03.09,
    vmp.penalty_reconcile.reconcile): сверка штрафов ВМП с разбором
    счёта решателя. Результат кладётся в тело сертификата целиком —
    гейт пересчитывает вердикт из тела, а не верит шапке. Расхождение
    отзывает сертификат; «несравнимо» — стоимость не проверена."""
    if not strict_coverage:
        raise CertifyError(
            "нестрогое покрытие снято (аудит A8, 04.09.2026): сертификат "
            "допустимости без полного покрытия правил не выдаётся — "
            "вызывайте certify без strict_coverage=False")
    # Пороги решателя и справочники входной книги могут ехать внутри
    # решения (кнопка кладёт их туда тем же каналом, что runInfo):
    # без порогов применённые ручки не засчитываются, без справочников
    # допуск по типу «не проверяем — нет данных о типе ВС и допусках».
    if thresholds is None and isinstance(solution, dict) \
            and isinstance(solution.get("thresholds"), dict):
        thresholds = solution["thresholds"]
    solver = _check_thresholds(thresholds) \
        if thresholds is not None else None
    if run_info is not None and not isinstance(run_info, dict):
        raise CertifyError("run_info: не объект JSON")
    trace = {k: run_info[k] for k in _RUN_TRACE_KEYS
             if isinstance(run_info, dict) and run_info.get(k)} \
        if run_info is not None else None
    if logic_report is None and isinstance(solution, dict):
        logic_report = solution.get("lastRunLogic")
    # Шапка прогона может ехать и внутри решения (веб-путь /certify
    # принимает один файл — как lastRunLogic): без неё сертификат
    # не знает жёсткий счёт решателя (solver_hard=null — живой
    # вопрос Г-S4).
    if run_info is None and isinstance(solution, dict):
        run_info = solution.get("runInfo")
        if run_info is not None and not isinstance(run_info, dict):
            raise CertifyError("runInfo решения: не объект JSON")
        trace = {k: run_info[k] for k in _RUN_TRACE_KEYS
                 if isinstance(run_info, dict) and run_info.get(k)}             if run_info is not None else trace
    dataset = build_solution_dataset(
        solution, reference_tables=(solution.get("reference_tables")
                                    if isinstance(solution, dict) else None))
    # Пометка «исключено до получения данных по решению авиакомпании»
    # (структура ВМП, владелец 2026-09-02): такие правила не
    # проверяются и не считаются, сертификат помечает их
    # непроверенными отдельной строкой.
    svod, excluded_until_data = split_excluded_until_data(svod)
    # Что код проверяет точно, код и проверяет: правила о нормах
    # резерва судятся по сводке «база × сутки» (колонка «Нехватка»),
    # и компилятору-модели тут читать нечего. Проход 21 финальной
    # серии H0 (03.09): модель дала спецификацию «диапазон без min и
    # без max», её отбраковали, и правило осталось непокрытым — при
    # той же сводке, что в соседних проходах.
    code_specs, svod_for_model = reserve_code_checks(svod, dataset)
    # Параметры мира (шкала времени) применены конфигурацией прогона —
    # компилятору не подаются, в отчёт идут записью (проход 6 H0-К).
    world_checks, svod_for_model = world_param_checks(
        svod_for_model, thresholds=thresholds, run_info=run_info)
    # Детерминированные проверки из ПОСАДКИ правил (решение владельца
    # 05.09.2026, пункт 3): посаженное правило семьи с исполнителем
    # компилятору не подаётся — форму задаёт код, число — посадка
    # (vmp/run/fit_checks). Спецификации словаря идут тем же путём, что
    # у компилятора (потолки продления, отчёт); проверки кодом — в отчёт.
    from vmp.run import fit_checks as _fitc
    fit_specs, fit_checks, fit_findings, svod_for_model = \
        _fitc.fit_code_checks(svod_for_model, dataset)
    code_specs = code_specs + fit_specs
    # Правила с блоком logic по формам решателя — кодом, с историей
    # сотрудника (решение владельца 05.09.2026 «нужно учитывать»):
    # форма — из показаний lastRun.logic того же прогона, нарушения
    # пересчитываются независимо по листам решения (vmp/run/logic_checks).
    from vmp.run import logic_checks as _lgc
    logic_checks_, logic_findings, svod_for_model = \
        _lgc.logic_code_checks(svod_for_model, dataset, logic_report)
    specs = code_specs + dc.compile_checks(svod_for_model, dataset, llm,
                                           control=control)
    # Единица счёта состава — от семьи, не от компилятора (проход 6
    # H0-К: «смена» вместо «рейс» дала 31 ложное нарушение).
    composition_notes = normalize_composition_specs(svod, specs)
    specs, cap_notes, cap_checks, cap_findings = apply_extension_caps(
        svod, specs, dataset)
    # Санитарный порог лавин выключен: он писан для линии данных
    # (промах нацеленности компилятора), а решение с массовыми
    # нарушениями — штатный стресс-случай сертификации, каждое
    # нарушение обязано быть в счёте (ревью ступени 1, 2026-08-22).
    report = dc.build_data_report(
        customer=str((svod.get("meta") or {}).get("customer") or "?"),
        dataset=dataset, specs=specs, sanity_threshold=False)
    # A8: адресные проверки потолка продления исполнены КОДОМ (предел
    # зависит от строки — условий исключения) и входят в отчёт тем же
    # порядком, что меры мягких правил.
    if cap_checks:
        report["checks"] = list(report["checks"]) + cap_checks
        report["findings"] = list(report["findings"]) + cap_findings
        report["counters"] = dr.metrics_of(report)
    if world_checks:
        report["checks"] = list(report["checks"]) + world_checks
        report["counters"] = dr.metrics_of(report)
    if fit_checks:
        report["checks"] = list(report["checks"]) + fit_checks
        report["findings"] = list(report["findings"]) + fit_findings
        report["counters"] = dr.metrics_of(report)
    if logic_checks_:
        report["checks"] = list(report["checks"]) + logic_checks_
        report["findings"] = list(report["findings"]) + logic_findings
        report["counters"] = dr.metrics_of(report)
    if composition_notes:
        report["composition_notes"] = composition_notes
    # Меры мягких правил (Д4, пункт 3): число совпадений по каждой
    # мягкой строке считается КОДОМ на решении и едет в отчёт
    # находками класса «мера» — вход штрафной сверки против разбора
    # счёта решателя. Допустимости они не касаются: сертификат судит
    # жёсткие правила, штраф судит сверка.
    soft_checks, soft_findings = measure_soft_rules(svod, dataset)
    # H11-6: оценка области учёта ночёвок (объявленная область → cost_scope
    # + паритет событий с решателем; прежний путь — явно частичная
    # диагностика) едет в тело отчёта и в шапку — её читает штрафная
    # сверка (vmp/penalty_reconcile, политика P-417).
    if "cost_assessment" in dataset:
        report["cost_assessment"] = dataset["cost_assessment"]
    if soft_checks:
        report["checks"] = list(report["checks"]) + soft_checks
        report["findings"] = list(report["findings"]) + soft_findings
        report["counters"] = dr.metrics_of(report)
    checks = report["checks"]
    # Проверки ДОПУСТИМОСТИ — без мер мягких правил (те судят штраф).
    feas_checks = [c for c in checks
                   if c.get("template") != SOFT_MEASURE_TEMPLATE]
    run_ok = [c for c in feas_checks if c.get("status") == "прогнана"]
    not_run = [c for c in feas_checks if c.get("status") != "прогнана"]
    violations = [f for f in report["findings"]
                  if f.get("class") != SOFT_MEASURE_CLASS]
    violated_rules = sorted({rid for f in violations
                             for rid in f.get("rule_refs", [])})
    soft_rules = [r.get("id") for r in svod.get("rules", [])
                  if r.get("kind") == "SOFT"]
    # A8 (04.09.2026): правило ПОКРЫТО, когда прогнаны ВСЕ его
    # проверки. Прежнее «хоть одна прогнана» объявляло составное
    # правило (например, предел смены + выходные в месяце) покрытым
    # после одной проверки — итог сильнее доказательства.
    by_rule: dict[str, list[dict]] = {}
    for c in feas_checks:
        rid = (c.get("basis") or {}).get("rule")
        if rid:
            by_rule.setdefault(str(rid), []).append(c)
    # Шаг 2 плана 05.09.2026 и C-12 единой версии: проверка окон с
    # неизвестными окнами (windows_unjudged > 0 либо
    # window_coverage.unknown > 0 — оба носителя) либо со статусом
    # «частично прогнана» правило ПОЛНОСТЬЮ не покрывает — оно покрыто
    # частично, допустимость по нему не установлена; адреса окон — в
    # самой проверке (window_coverage.windows / unjudged_sample).
    from vmp.run.logic_checks import (NOT_APPLICABLE_STATUS as _NA,
                                      PARTIAL_RUN_STATUS as _PARTIAL)

    def _windows_unknown(c: dict) -> int:
        cov = c.get("window_coverage")
        n = int((cov or {}).get("unknown") or 0) if isinstance(cov, dict) else 0
        return max(n, int(c.get("windows_unjudged") or 0))
    # C-31 (ревью группы C, 06.09.2026): форма без единого окна в
    # объявленном горизонте — «не применима» (например, помесячная
    # норма выходных на семисуточном горизонте без полного месяца):
    # правило ПОКРЫТО — проверять нечего, и это не пробел покрытия;
    # но в checks_run / run_ok такая проверка не входит: вакуумный
    # сертификат из одних «не применима» невозможен.
    # C-27b (план §3.10.3): горизонт, выведенный по назначениям решения
    # (horizon_inferred, source = inferred — пакеты без раздела
    # manifest.horizon), даёт только частичную диагностику, не pass:
    # правило покрыто частично, допустимость по нему не установлена.
    rules_with_run = {rid for rid, lst in by_rule.items()
                      if all(c.get("status") in ("прогнана", _NA)
                             and not _windows_unknown(c)
                             and not c.get("horizon_inferred")
                             for c in lst)}
    rules_partial = {rid for rid, lst in by_rule.items()
                     if rid not in rules_with_run
                     and any(c.get("status") in ("прогнана", _PARTIAL, _NA)
                             for c in lst)}
    not_applicable = [c for c in feas_checks if c.get("status") == _NA]
    rules_horizon_inferred = sorted({
        str((c.get("basis") or {}).get("rule")) for c in feas_checks
        if c.get("horizon_inferred") and (c.get("basis") or {}).get("rule")})
    excluded_ids = {str(x.get("rule_id")) for x in (excluded_until_data or [])}
    # Область сертификата допустимости — ЖЁСТКИЕ правила (мягкие
    # объявлены вне области полем rules_soft_out_of_scope: они
    # судятся штрафом, а не допустимостью).
    # Правило-ПАРАМЕТР МИРА (шкала времени данных) задаёт систему
    # отсчёта, а не ограничение на решение: «смещение 0 минут» на
    # расписании проверять нечем и незачем — оно применено при чтении
    # всех времён. Первые сертификаты H0 (03.09) называли его
    # непокрытым: «в схеме нет колонки со смещением шкалы».
    world_params = sorted(
        str(r.get("id")) for r in (svod.get("rules") or [])
        if r.get("kind") in ("HARD", "CASE")
        and _rule_family(r) in WORLD_PARAM_FAMILIES)
    uncovered = sorted(
        str(r.get("id")) for r in (svod.get("rules") or [])
        if r.get("kind") in ("HARD", "CASE")
        and str(r.get("id")) not in rules_with_run
        and str(r.get("id")) not in excluded_ids
        and str(r.get("id")) not in world_params)
    rules_partially_covered = sorted(rid for rid in rules_partial
                                     if rid in uncovered)
    # Нескомпилированная проверка: в отчёте она «не прогнана» с
    # причиной компилятора (отбракована, пропущена, канал без JSON,
    # блокирована вопросом) — прежний счёт по статусу был мёртв
    # (build_data_report такого статуса не пишет).
    not_compiled = [c for c in not_run
                    if "компил" in str(c.get("reason") or "").lower()]
    # A8: certified ⇔ допустимость ПОДТВЕРЖДЕНА — прогнанные проверки
    # есть, нарушений нет, покрытие полное (непокрытых правил и
    # нескомпилированных проверок нет). Fail-closed: вакуумный
    # «зелёный» (ноль прогнанных) по-прежнему не сертификат.
    certified = (bool(run_ok) and not violations
                 and not uncovered and not not_compiled)
    # Д4, пункт 3: «чист» требует, чтобы штраф по ВМП сошёлся с
    # разбором счёта решателя по каждой мягкой строке и целевой.
    penalty_clean = None
    if penalty_reconciliation is not None:
        from vmp import penalty_reconcile as _pr
        penalty_clean = bool(_pr.is_clean(penalty_reconciliation))
        if not penalty_clean:
            certified = False
    # Решение, которое САМ решатель зовёт недопустимым (hard < 0),
    # сертификатом допустимости не награждается — проверки остаются
    # доказательствами пост-фактум (заявка моста, Лайт −29273hard).
    solver_hard = _hard_of(run_info)
    if solver_hard is not None and solver_hard < 0:
        certified = False
    # Пункт 19: показания решателя по правилам с logic судятся тем
    # же гейтом, что и на прогоне (webapp.logic_gate) — одна логика
    # у обеих сторон; дефект показаний = недоверие прогону.
    logic_rules = [r.get("id") for r in svod.get("rules", [])
                   if isinstance(r.get("logic"), dict)]
    lg_section = None
    if logic_report is not None:
        if not isinstance(logic_report, dict):
            raise CertifyError("lastRun.logic: не объект JSON")
        from webapp import logic_gate as lg
        try:
            lg_verdicts = lg.check(svod, logic_report)
        except lg.LogicGateError as e:
            raise CertifyError(
                f"показания logic не судятся: {e}") from None
        lg_defects = [x for x in lg_verdicts
                      if x.get("verdict") == lg.DEFECT]
        lg_section = {"green": not lg_defects,
                      "verdicts": lg_verdicts}
        if lg_defects:
            certified = False
    if solver_hard is not None and solver_hard < 0:
        note = (f"решатель сообщил недопустимость решения "
                f"({solver_hard}hard) — сертификат допустимости не "
                f"выдаётся; прогнанные проверки — доказательства "
                f"пост-фактум"
                + (", нарушения перечислены"
                   if violations else ""))
    elif violations:
        note = ("решение НЕ прошло сертификацию — нарушения "
                "перечислены с доказательствами")
    elif not run_ok:
        note = ("сертификация НЕ состоялась: ни одна проверка не "
                "прогнана (все исходы — честные отказы с причинами); "
                "решение не удостоверено")
    elif uncovered or not_compiled:
        # A8: третий исход словами — не «допустимо» и не
        # «недопустимо», а «не удостоверено» с перечнем.
        note = (f"не удостоверено: непроверено {len(uncovered)} правил "
                f"({', '.join(uncovered) if uncovered else '—'})"
                + (f", проверок не скомпилировано {len(not_compiled)}"
                   if not_compiled else "")
                + (f"; составных правил, покрытых не всеми проверками: "
                   f"{', '.join(rules_partially_covered)}"
                   if rules_partially_covered else "")
                + (f"; правил, судимых на горизонте, выведенном по "
                   f"назначениям (объявленного window_horizon нет — "
                   f"частичная диагностика, не pass): "
                   f"{', '.join(rules_horizon_inferred)}"
                   if rules_horizon_inferred else "")
                + " — сертификат допустимости не выдаётся: нарушений по "
                  "прогнанным проверкам нет, но допустимость НЕ "
                  "установлена; непроверенное правило на однозначном "
                  "наборе есть красный, а не пропуск")
    elif penalty_clean is False:
        note = ("штрафная сверка (Д4, пункт 3) не сошлась: штраф по ВМП "
                "расходится с разбором счёта решателя либо несравним — "
                "сертификат не выдаётся; построчный разбор в теле")
    else:
        note = ("допустимость подтверждена: покрытие полное — у каждого "
                "жёсткого правила прогнаны все проверки (исключённые по "
                "решению авиакомпании и параметры мира названы отдельно), "
                "нарушений нет"
                + (f"; проверок форм без окон в объявленном горизонте "
                   f"(«{_NA}»): {len(not_applicable)} — правило покрыто, "
                   f"проверять нечего" if not_applicable else ""))
    if lg_section is not None and not lg_section["green"]:
        note = ("показания решателя по правилам с logic расходятся "
                "с пакетом (дефекты гейта ступени 3) — сертификат "
                "допустимости не выдаётся; расхождения — в разделе "
                "показаний")
    verdict = {
        "solution": str(solution.get("solution") or "решение"),
        # Счёт шапки — по проверкам ДОПУСТИМОСТИ: меры мягких правил
        # судят штраф и в «нарушения» не входят (A8: прежде findings
        # шапки считал и меры, и «нарушений N» лгало).
        "rules_considered": len(by_rule),
        "rules_soft_out_of_scope": soft_rules,
        # Контракт Б (05.09.2026): определённость стоимости — по области
        # учёта мер; у ростеринга приписка известна, неопределённа
        # только граница горизонта (смены, оконченные вне базы без
        # известного продолжения): при таких событиях полная стоимость
        # не установлена, и это называется словами, а не нулём.
        # Показание о перегонах (единый план 05.09.2026, раздел 3): разрешены
        # ли, основание (порог пакета / параметр / умолчание решателя),
        # кресел на рейс, использовано мест — словами, не молча.
        "deadheads": (dict(solution["deadheads"])
                      if isinstance(solution.get("deadheads"), dict) else None),
        # H11-4 (единая версия 06.09.2026): без объявленной области учёта
        # (window_horizon) мера ночёвок — явно частичная диагностика, и
        # полного «подтверждена» она не даёт; с объявленной областью
        # определённость = ярлык независимой оценки (cost_scope.certainty).
        "cost_certainty": ("partial" if any(
            int(c.get("uncertain_events") or 0) > 0
            or (c.get("cost_scope") or {}).get("certainty") == "partial"
            for c in soft_checks)
            else "full"),
        "cost_uncertain_events": sum(
            int(c.get("uncertain_events") or 0) for c in soft_checks),
        # H11-6: область учёта ночёвок и паритет с решателем — рядом с
        # cost_certainty, не вместо (None — мер ночёвок не было).
        "cost_assessment": dataset.get("cost_assessment"),
        "checks_run": len(run_ok),
        "checks_not_run": len(not_run),
        # C-31: проверки форм без окон в объявленном горизонте — «не
        # применима»: правило покрыто, но в прогнанные не входит
        # (счёт шапки checks_not_run их включает — гейт сверяет
        # checks_run + checks_not_run с телом).
        "checks_not_applicable": len(not_applicable),
        # C-27b: правила, судимые на выведенном горизонте (source =
        # inferred) — частичная диагностика; они же в
        # rules_partially_covered.
        "rules_horizon_inferred": rules_horizon_inferred,
        "findings": len(violations),
        "soft_measures": len(report["findings"]) - len(violations),
        "violated_rules": violated_rules,
        "rules_uncovered": uncovered,
        "rules_partially_covered": rules_partially_covered,
        "world_params": world_params,
        "extension_caps": cap_notes,
        "checks_not_compiled": len(not_compiled),
        "coverage_strict": True,
        "penalty_reconciliation": penalty_reconciliation,
        "penalty_reconciliation_clean": penalty_clean,
        "certified": certified,
        "note": note,
        "excluded_until_data": excluded_until_data,
        # Ревизия 1.17: остатки на начало периода дошли до сертификата —
        # сводка листа «История» в шапке (None — листа нет).
        "history": _history_summary(dataset or {}),
        # C-11 (план §3.8): покрытие окон проверок допустимости в шапке —
        # суммы expected / evaluated / unknown и перечень проверок с их
        # окнами; неизвестное окно — пробел покрытия (vmp.run.verdicts).
        "window_coverage": _window_coverage_summary(feas_checks),
    }
    if solver_hard is not None:
        verdict["solver_hard"] = solver_hard
    if solver is not None:
        verdict["solver_applied"] = len(solver["applied"])
        verdict["solver_ignored"] = len(solver["ignored"])
        report["solver_thresholds"] = solver
    # Доля свода в состоянии ПОСЧИТАНО (приёмка 9г, 2026-08-24):
    # метрика лестницы считается МАШИННО и живёт в документе.
    # Жёсткое правило без блокеров посчитано, если его параметр
    # применён порогами решателя, ИЛИ его логика в корзинах
    # generated/executedElsewhere, ИЛИ его проверка прогнана
    # сертификатором. Непосчитанные — поимённая карта второй волны.
    hard_ids = [r.get("id") for r in svod.get("rules", [])
                if r.get("kind") == "HARD" and not r.get("blockers")]
    counted_set: set = set()
    if solver is not None:
        for a in solver.get("applied") or []:
            counted_set.update(a.get("rules") or [])
    if isinstance(logic_report, dict):
        for basket in ("generated", "executedElsewhere"):
            for e in logic_report.get(basket) or []:
                if isinstance(e, dict):
                    counted_set.add(e.get("rule"))
    for c in checks:
        if c.get("status") == "прогнана":
            rid = (c.get("basis") or {}).get("rule")
            if rid:
                counted_set.add(rid)
    verdict["counted_share"] = {
        "counted": sum(1 for rid in hard_ids if rid in counted_set),
        "of": len(hard_ids),
        "rules_counted": [rid for rid in hard_ids
                          if rid in counted_set],
        "rules_uncounted": [rid for rid in hard_ids
                            if rid not in counted_set],
        "reading": ("жёсткое правило без блокеров считается "
                    "посчитанным, если его параметр применён "
                    "порогами решателя, либо его логика в корзинах "
                    "generated/executedElsewhere показаний, либо "
                    "его проверка прогнана сертификатором"),
    }
    if logic_rules:
        verdict["logic_rules"] = len(logic_rules)
        if lg_section is None:
            verdict["logic_note"] = (
                "показания решателя (lastRun.logic) не переданы — "
                "корзины ступени 3 не сверены; сертификат на "
                "исполнение логики не распространяется")
    if lg_section is not None:
        verdict["logic_gate_green"] = lg_section["green"]
        report["solver_logic"] = lg_section
    if trace:
        report["run_trace"] = trace
    # A8: трёхчастный результат — из шапки, детерминированно; флаг
    # certified — производная трёх частей (допустимость подтверждена и
    # сверка стоимости, если была, сошлась), не источник.
    from vmp.run import verdicts as _vd
    verdict["result"] = _vd.result_of_certificate(verdict)
    if _vd.certified_of(verdict, verdict["result"]) != bool(certified):
        raise CertifyError("сертификат: флаг certified расходится с "
                           "трёхчастным результатом — дефект вывода вердикта")
    report["certificate"] = verdict
    return report


def _window_coverage_summary(feas_checks: list[dict]) -> dict:
    """Агрегат покрытия окон по проверкам допустимости (C-11): у проверки
    с window_coverage — её числа; у проверки прежней формы (только
    windows_judged / windows_unjudged) — производные: expected = judged
    + unjudged, evaluated = judged, unknown = unjudged."""
    total = {"expected": 0, "evaluated": 0, "unknown": 0, "checks": []}
    for c in feas_checks:
        cov = c.get("window_coverage")
        if isinstance(cov, dict):
            e, v, u = (int(cov.get(k) or 0) for k in ("expected", "evaluated", "unknown"))
            total["checks"].append({"check": c.get("id"),
                                    "rule": (c.get("basis") or {}).get("rule"),
                                    **cov})
        elif "windows_judged" in c or "windows_unjudged" in c:
            v = int(c.get("windows_judged") or 0)
            u = int(c.get("windows_unjudged") or 0)
            e = v + u
            total["checks"].append({"check": c.get("id"),
                                    "rule": (c.get("basis") or {}).get("rule"),
                                    "expected": e, "evaluated": v, "unknown": u,
                                    "granularity": "windows",
                                    "expected_unit": "windows",
                                    "window_kind": "legacy_daily",
                                    "windows": list(c.get("unjudged_sample") or [])})
        else:
            continue
        total["expected"] += e
        total["evaluated"] += v
        total["unknown"] += u
    return total


def refresh_verdict(cert: dict) -> dict:
    """Пересчёт флага certified и трёхчастного результата из тела шапки
    — после того как к сертификату ДОБАВИЛИ показание (штрафную
    сверку, сверку целевой). Аудит A8: прежде кнопка клала сверку в
    шапку после вердикта, и разошедшаяся сверка оставляла прежний
    положительный статус. Качество оптимизации и признак «поиск не
    завершён» сохраняются от прежнего результата."""
    from vmp.run import verdicts as _vd
    prev = cert.get("result") if isinstance(cert.get("result"), dict) else {}
    res = _vd.result_of_certificate(
        cert, optimality=prev.get("optimality"),
        search_incomplete=bool(prev.get("search_incomplete")))
    cert["result"] = res
    cert["certified"] = _vd.certified_of(cert, res)
    return cert


def attach_penalty_reconciliation(report: dict, reconciliation: dict) -> dict:
    """Штрафная сверка (Д4, пункт 3) кладётся в шапку ЦЕЛИКОМ и вердикт
    пересчитывается из тела: несошедшаяся сверка отзывает флаг
    certified и даёт «стоимость: расхождение» в результате."""
    cert = report.get("certificate") if isinstance(report, dict) else None
    if not isinstance(cert, dict):
        raise CertifyError("сертификат без шапки — сверку прикрепить некуда")
    if not isinstance(reconciliation, dict) or "verdict" not in reconciliation:
        raise CertifyError("штрафная сверка: не объект с вердиктом")
    from vmp import penalty_reconcile as _pr
    cert["penalty_reconciliation"] = reconciliation
    clean = bool(_pr.is_clean(reconciliation))
    cert["penalty_reconciliation_clean"] = clean
    refresh_verdict(cert)
    if not clean and cert["result"]["feasibility"] == "confirmed":
        cert["note"] = (
            "допустимость подтверждена, но штрафная сверка (Д4, пункт 3) "
            "не сошлась: штраф по ВМП расходится с разбором счёта решателя "
            "либо несравним — сертификат не выдаётся; построчный разбор в теле")
    return report


def feasibility_view(report: dict) -> dict:
    """Сертификат допустимости без мер мягких правил — вид для гейта
    cdpm.gates.gate_certify: меры (класс «мера мягкого правила») не
    входят в контракт 3.3 и в допустимость не судят; счётчики
    пересчитаны по контрактной функции. Копия, оригинал не трогается."""
    view = json.loads(json.dumps(report, ensure_ascii=False))
    view["checks"] = [c for c in view.get("checks") or []
                      if c.get("template") != SOFT_MEASURE_TEMPLATE]
    view["findings"] = [f for f in view.get("findings") or []
                        if f.get("class") != SOFT_MEASURE_CLASS]
    view["counters"] = dr.metrics_of(view)
    return view


def split_excluded_until_data(svod: dict) -> tuple[dict, list[dict]]:
    """Свод без правил с пометкой «исключено до получения данных по
    решению авиакомпании» и перечень исключённых (id, заголовок,
    вопрос анкеты, ревизия) — для сертификатов обеих задач."""
    from vmp.registry_check import is_excluded_until_data
    kept, excluded = [], []
    for r in svod.get("rules") or []:
        if is_excluded_until_data(r):
            ex = r["excluded_until_data"]
            excluded.append({"rule_id": str(r.get("id")),
                             "title": str(r.get("title") or ""),
                             "question": str(ex.get("question")),
                             "revision": ex.get("revision")})
        else:
            kept.append(r)
    if not excluded:
        return svod, []
    out = dict(svod)
    out["rules"] = kept
    return out, excluded


def _cost_assessment_line(assessment) -> str:
    """Строка шапки об области учёта ночёвок (H11-6): область, число
    доказанных событий и неизвестных вкладов, паритет событий с
    решателем словами; полная стоимость подтверждается только сверкой
    всех членов цены (политика P-417), не этой строкой."""
    if not isinstance(assessment, dict):
        return ""
    comparison = assessment.get("solver_comparison")
    if isinstance(comparison, dict):
        parity = (f"паритет событий с решателем: {comparison.get('verdict')}"
                  + (f" ({', '.join(map(str, comparison.get('reasons') or []))})"
                     if comparison.get("reasons") else ""))
    else:
        parity = "сверка событий с записью решателя не проводилась"
    reasons: dict[str, int] = {}
    for u in assessment.get("unknown_events") or []:
        if isinstance(u, dict):
            reasons[str(u.get("reason"))] = reasons.get(str(u.get("reason")), 0) + 1
    return ("ОБЛАСТЬ УЧЁТА НОЧЁВОК: "
            + str(assessment.get("scope") or assessment.get("task") or "?")
            + (f" [{assessment.get('scope_start')} — {assessment.get('scope_end')}]"
               if assessment.get("scope_start") else " (область не объявлена)")
            + f"; доказанных событий {assessment.get('known_nights', 0)}, "
            + f"неизвестных вкладов {assessment.get('unknown_count', 0)}"
            + (" (" + ", ".join(f"{k} {n}" for k, n in sorted(reasons.items())) + ")"
               if reasons else "")
            + f"; {parity}. "
            + ("Область полностью известна; подтверждение полной стоимости "
               "зависит от сверки всех членов цены."
               if assessment.get("complete")
               else "Показан известный подытог; полная стоимость не подтверждена."))


def render_certificate(report: dict) -> str:
    """Человекочитаемый сертификат поверх штатного рендера отчёта:
    шапка с вердиктом, затем полный отчёт проверок."""
    from vmp.run.verdicts import feasibility_of as _feasibility_of
    v = report.get("certificate") or {}
    hard = v.get("solver_hard")
    if hard is not None and hard < 0:
        verdict = (f"**ВЕРДИКТ: РЕШАТЕЛЬ СООБЩАЕТ НЕДОПУСТИМОСТЬ: "
                   f"{hard}hard — сертификат допустимости не "
                   f"выдаётся** (проверки ниже — доказательства "
                   f"пост-фактум"
                   + (f"; нарушений {v.get('findings')}"
                      if v.get("findings") else "; нарушений по "
                      "прогнанным проверкам нет") + ") ")
    elif _feasibility_of(v) == "confirmed":
        # C-13: допустимость — своя часть итога; несошедшаяся либо
        # неустановленная стоимость не печатается как «допустимость не
        # установлена». Когда сертификат при этом не выдан (certified
        # False), заголовок называет причину, чтобы «ПОДТВЕРЖДЕНА» не
        # читалось как выдача сертификата.
        verdict = ("**ВЕРДИКТ: ДОПУСТИМОСТЬ ПОДТВЕРЖДЕНА — покрытие "
                   "полное, нарушений нет** ")
        if not v.get("certified"):
            res = v.get("result") if isinstance(v.get("result"), dict) else {}
            cost = res.get("cost")
            if cost == "mismatch":
                why = "стоимость: расхождение сверки"
            elif cost == "not_checked":
                why = "стоимость: не проверена (сверка несравнима)"
            elif v.get("logic_gate_green") is False:
                why = "показания решателя расходятся с пакетом"
            else:
                why = "стоимость не подтверждена"
            verdict = ("**ВЕРДИКТ: ДОПУСТИМОСТЬ ПОДТВЕРЖДЕНА — покрытие "
                       f"полное, нарушений нет; сертификат не выдан: {why}** ")
    elif v.get("findings"):
        verdict = (f"**ВЕРДИКТ: НЕ СЕРТИФИЦИРОВАНО — нарушений "
                   f"{v.get('findings')}, правил с нарушениями "
                   f"{len(v.get('violated_rules') or [])}** ")
    elif not v.get("checks_run"):
        verdict = ("**ВЕРДИКТ: СЕРТИФИКАЦИЯ НЕ СОСТОЯЛАСЬ — ни одной "
                   "прогнанной проверки** ")
    else:
        # A8: третий исход — нарушений нет, но допустимость НЕ
        # установлена (непроверенные правила, красный гейт показаний,
        # разошедшаяся сверка стоимости).
        unc = v.get("rules_uncovered") or []
        verdict = ("**ВЕРДИКТ: НЕ УДОСТОВЕРЕНО — допустимость не "
                   "установлена"
                   + (f": непроверено правил {len(unc)} "
                      f"({', '.join(map(str, unc))})" if unc else "")
                   + "** ")
    res = v.get("result")
    result_line = ""
    if isinstance(res, dict):
        from vmp.run import verdicts as _vd
        try:
            result_line = "Итог (три части): " + _vd.result_line(res) + "."
        except ValueError as e:
            result_line = f"Итог: результат вне словаря — {e}."
    dh = v.get("deadheads")
    if isinstance(dh, dict):
        # Показание о перегонах — словами в шапке (единый план 05.09.2026).
        result_line += (
            " Перегоны: " + ("разрешены" if dh.get("allowed") else "не разрешены")
            + f" — {dh.get('basis')}; кресел на рейс {dh.get('seatsPerFlight')}"
            + (f", использовано мест {dh.get('used')}" if dh.get("used") is not None else "")
            + ".")
    soft = v.get("rules_soft_out_of_scope") or []
    judged = v.get("judged_on_rostering") or []
    judged_line = ""
    if judged:
        judged_line = (
            f"СУДЯТСЯ НА РОСТЕРИНГЕ ({len(judged)}): "
            + ", ".join(f"{x.get('rule_id')} ({x.get('family')})"
                        for x in judged)
            + " — правила о людях: у цепочек каталога нет "
              "назначенных людей.")
    excluded = v.get("excluded_until_data") or []
    excluded_line = ""
    if excluded:
        excluded_line = (
            f"НЕ ПРОВЕРЕНО ПО РЕШЕНИЮ АВИАКОМПАНИИ ({len(excluded)}): "
            + ", ".join(f"{x.get('rule_id')} (вопрос {x.get('question')}, "
                        f"ревизия {x.get('revision')})" for x in excluded)
            + " — исключено до получения данных; сертификат на эти "
              "правила не распространяется.")
    hist = v.get("history")
    history_line = ""
    if isinstance(hist, dict):
        history_line = (
            f"ИСТОРИЯ СОТРУДНИКОВ (раздел пакета 1.17): срез {hist.get('as_of')}, "
            f"с историей {hist.get('with_history')} из {hist.get('employees_total')}"
            + (f" (без истории: {', '.join(hist['without_history'][:10])})"
               if hist.get("without_history") else "")
            + f"; остаток налёта месяца {hist.get('month_block_min_total')} мин "
              f"(известен у {hist.get('month_block_known')} из {hist.get('with_history')}), "
              f"рабочего времени {hist.get('month_work_min_total')} мин "
              f"(известен у {hist.get('month_work_known')} из {hist.get('with_history')}); "
              f"последняя смена известна у {hist.get('with_last_duty')} — "
              "остатки учтены проверками полов отдыха и накопителей; "
              "неизвестный остаток нулём не считается.")
    window = v.get("window_coverage") or {}
    window_line = ""
    if isinstance(window, dict) and window.get("checks"):
        # C-14: покрытие окон словами в человекочитаемом сертификате.
        window_line = ("ПОКРЫТИЕ ОКОН: доказательных ячеек ожидается "
                       f"{window.get('expected', 0)}, проверено {window.get('evaluated', 0)}, "
                       f"неизвестно {window.get('unknown', 0)}. "
                       "Для непрерывных окон отдыха это конечное разбиение области "
                       "концов окон (критические ячейки), а не количество всех "
                       "моментов времени; неизвестное окно не считается выдержанным.")
    cost_line = _cost_assessment_line(v.get("cost_assessment"))
    head = [
        "# Сертификат решения против свода CDPM", "",
        f"Решение: {v.get('solution', '?')}. Авиакомпания: "
        f"{report.get('customer', '?')}.",
        "",
        verdict,
        f"(правил рассмотрено {v.get('rules_considered')}, проверок "
        f"прогнано {v.get('checks_run')}, не прогнано "
        f"{v.get('checks_not_run')}"
        + (f", из них не применимо к горизонту "
           f"{v.get('checks_not_applicable')} — форма без окон в "
           f"объявленном горизонте, правило покрыто"
           if v.get("checks_not_applicable") else "")
        + " — непроверенное перечислено ниже, сертификат на него не "
          "распространяется).",
        result_line,
        "",
        (f"Мягкие правила свода вне сертификации допустимости "
         f"({len(soft)}): {', '.join(map(str, soft))} — их штрафы "
         f"считает решатель, не сертификатор." if soft else ""),
        judged_line,
        excluded_line,
        history_line,
        window_line,
        cost_line,
        "Нумерация строк в доказательствах — конвенция листа: "
        "строка 2 = первая запись листа.",
        "",
    ]
    trace = report.get("run_trace") or {}
    if trace:
        head += [
            "Трассировка прогона решателя: "
            + "; ".join(f"{k} = {trace[k]}" for k in
                        ("package_fingerprint", "rules_profile",
                         "source_run", "spec_version", "mode")
                        if trace.get(k)),
            "",
        ]
    solver = report.get("solver_thresholds")
    if solver is not None:
        head += ["## Пороги решателя того же прогона", ""]
        applied = solver["applied"]
        head.append(f"Применено решателем: {len(applied)}.")
        for a in applied:
            head.append(
                f"- {a.get('threshold', '?')} = {a.get('value', '?')}"
                f" (литерал профиля {a.get('literal', '?')}; из "
                f"параметров {', '.join(map(str, a.get('fromParams') or []))})")
            for d in a.get("declarations") or []:
                head.append(f"  - {d}")
        ignored = solver["ignored"]
        head.append("")
        head.append(f"ФОРМАЛИЗОВАНО, РЕШАТЕЛЕМ НЕ ИСПОЛНЯЛОСЬ: "
                    f"{len(ignored)}. Эти правила проверены только "
                    f"настоящим сертификатом пост-фактум — решатель "
                    f"о них не знал:")
        for g in ignored:
            rules = ", ".join(map(str, g.get("rules") or []))
            head.append(f"- {g.get('param', '?')}"
                        + (f" (правила {rules})" if rules else "")
                        + f": {g.get('reason', 'причина не названа')}")
        # «Число ниоткуда» глазами читателя сертификата: ручки, о
        # которых пакет молчит, считались литералами профиля — без
        # этого перечня внебазовая стыковка судится по числу,
        # которого в постановке нет, и читатель об этом не знает.
        lits = solver.get("literals_in_force") or []
        if lits:
            head.append("")
            head.append(f"СЧИТАЛОСЬ ЛИТЕРАЛАМИ ПРОФИЛЯ (пакет о них "
                        f"молчит): {len(lits)} — эти пороги НЕ из "
                        f"постановки заказчика:")
            for entry in lits:
                head.append(
                    f"- {entry.get('threshold', '?')} = "
                    f"{entry.get('value', '?')}"
                    + (f" ({entry['note']})" if entry.get("note")
                       else ""))
        off = solver.get("off_literals") or {}
        # «Выключено» — по показанию: ручка, которую пакет ВКЛЮЧИЛ
        # (есть в applied), выключателем не считается (заявка моста
        # 2026-08-23: перечень ключей лгал бы при включённых
        # посадках).
        applied_names = {a.get("threshold") for a in applied}
        off_names = sorted(k for k in off
                           if k not in applied_names)
        if off_names:
            head.append("")
            head.append("Выключено в профиле (правило не "
                        "действовало): " + ", ".join(off_names))
        head.append("")
    slog = report.get("solver_logic")
    if slog is not None:
        head += ["## Ступень 3 — показания решателя по правилам с "
                 "logic", ""]
        head.append("Гейт показаний: "
                    + ("зелёный." if slog.get("green")
                       else "КРАСНЫЙ — расхождения помечены ниже."))
        for x in slog.get("verdicts") or []:
            head.append(f"- {x.get('rule', '?')}: "
                        f"{x.get('verdict', '?')}"
                        + (f" — {x['note']}" if x.get("note")
                           else ""))
        head.append("")
    ln = (report.get("certificate") or {}).get("logic_note")
    if ln:
        head += [f"ПОКАЗАНИЯ СТУПЕНИ 3: {ln}", ""]
    cs = (report.get("certificate") or {}).get("counted_share")
    if cs:
        pct = (100 * cs["counted"] / cs["of"]) if cs["of"] else 0
        head += [
            f"## Доля свода в состоянии ПОСЧИТАНО: "
            f"{cs['counted']}/{cs['of']} ({pct:.0f}%)", "",
            f"Методика: {cs['reading']}.", "",
        ]
        if cs["rules_uncounted"]:
            head.append(
                f"КАРТА ВТОРОЙ ВОЛНЫ — жёсткие правила, которые "
                f"пока никем не считаются "
                f"({len(cs['rules_uncounted'])}): "
                + ", ".join(map(str, cs["rules_uncounted"])))
            head.append("")
    return "\n".join(head) + dr.render_data_report(report)


# ------------------------------------------- сертификация пейринга

#: Общие ТИПЫ людских семей механик (принцип шаблона: тип в коде,
#: конкретика — параметрами ВМП): правила этих семей судят
#: НАЗНАЧЕННЫХ ЛЮДЕЙ, а у цепочек каталога пейринга людей ещё нет —
#: сертификат пейринга честно относит их на ростеринг, как ФПР.
#:
#: ПЕРЕЧЕНЬ ПИШЕТСЯ РУКОЙ ПО ЗАМЫСЛУ, и каталог его не заменяет (разбор
#: 10.09.2026, пакет «один носитель перечней»). Он — СВИДЕТЕЛЬ ЗАКОНА
#: ПАРИТЕТА: правило L15 (`catalog/tools/parity.py`) судит природу
#: записи каталога (`nature`) ПО КОДУ, беря сторону кода отсюда, а
#: конвейер заявок держит перечень неприкосновенным для агента
#: (`webapp/change_requests.py`, PARITY_WITNESS_CONSTS, просьба В-2:
#: снимок до и после работы агента обязан совпасть). Чтение каталога
#: здесь сделало бы L15 тождеством («запись сходится сама с собой»), а
#: у заявки на людскую семью — красным по построению: снимок ветки
#: разошёлся бы со снимком main. Природы `history` держит сторона
#: решателя (`LogicModel.HISTORY_FIT_FAMILIES`), у природ `people` и
#: `world_param` второй стороны в коде нет — её и заменяет этот
#: перечень. Растёт он рукой человека вместе с исполнителем.
PEOPLE_FAMILIES = frozenset({
    "crew_composition", "reserve_floors",
    # Записей этих двух семей в каталоге 0.4.0 НЕТ: они названы описью
    # переноса кода в каталог (final-report/KATALOG-SHABLONOV-SHEMA-
    # ZAPISI-CLD.md, §14: «qualification_currency,
    # unavailability_windows (в PEOPLE_FAMILIES, семей нет) → заявки»).
    # Свидетелю лишние имена не мешают: L15 судит ЗАПИСИ каталога и об
    # имени без записи молчит, а сертификат готов отнести такое правило
    # на ростеринг с первого же пакета — до выпуска записи.
    "qualification_currency", "unavailability_windows",
    # Допуск по типу судна судит НАЗНАЧЕННОГО пилота (есть ли у него
    # допуск на тип рейса) — у цепочки каталога пилота нет. Первый
    # сертификат пейринга H0 (03.09) назвал правило непокрытым:
    # «нет таблиц с типом ВС рейса и допусками пилотов» — их и не
    # бывает в каталоге, это правило ростеринга.
    "fleet_exclusions",
})

#: Семьи-ПАРАМЕТРЫ МИРА: задают систему отсчёта данных (шкала времени
#: и смещение), а не ограничение на решение. Применяются при чтении
#: всех времён; на готовом расписании проверять их нечем — сертификат
#: называет их применёнными, а не непокрытыми. Тип семьи — в коде,
#: конкретика (смещение) — параметром ВМП.
#:
#: Перечень — тот же СВИДЕТЕЛЬ ЗАКОНА ПАРИТЕТА, что и PEOPLE_FAMILIES
#: выше (L15, PARITY_WITNESS_CONSTS): чтением каталога он не заменяется.
WORLD_PARAM_FAMILIES = frozenset({"time_scale"})


#: Условия исключения (продления), которые сертификатор умеет судить
#: по строке смены: имя параметра посадки правила-потолка → колонка
#: листа «Смены» и способ сравнения. Тип условия — в коде, значения —
#: параметрами ВМП (принцип P-N-SHD). Условие вне словаря — честный
#: отказ («не прогнана: не поддаётся проверке»), а не «потолок всем».
EXTENSION_CONDITIONS = {
    # база приписки: потолок только сменам сотрудников названных баз
    "extension_bases": ("База приписки", "in"),
    # явное согласие экипажа на продление — по строке смены («да»)
    "extension_consent": ("Согласие на продление", "yes"),
}
_EXTENSION_PARAMS = frozenset({"long_duty_threshold", "duty_extension_cap"})


def _cap_rules(svod: dict) -> list[dict]:
    """Независимые политики продления; семья не является ключом дедупликации.

    Отсутствующее условие свободно, объявленное неполное — отказ, не
    подстановка false. Ошибки едут в адресный результат проверки HARD.
    """
    from vmp.registry_check import is_excluded_until_data
    caps: list[dict] = []
    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or r.get("kind") == "OUT" or is_excluded_until_data(r):
            continue
        fam = _rule_family(r)
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        params = fit.get("params")
        if not fam or not isinstance(params, dict) or "duty_extension_cap" not in params:
            continue
        conditions: dict = {}
        unknown: list[str] = []
        problems: list[str] = []
        try:
            _validate_typed_landing(r)
        except CertifyError as exc:
            problems.append(str(exc))

        def value(name):
            cell = params[name]
            if not isinstance(cell, dict) or cell.get("value") is None \
                    or "pending_revision" in cell or cell.get("status") == "pending":
                problems.append(f"{name}: неполная ячейка или открытый пересмотр")
                return None
            return cell["value"]

        val = value("duty_extension_cap")
        if isinstance(val, bool) or not isinstance(val, (int, float)) \
                or not math.isfinite(val) or val <= 0 or int(val) != val:
            problems.append("duty_extension_cap: требуется целое число минут > 0")
            val = None
        for name in params:
            if not str(name).startswith("extension_") or name in _EXTENSION_PARAMS:
                continue
            if name not in EXTENSION_CONDITIONS:
                unknown.append(str(name))
                continue
            v = value(name)
            if v is None:
                continue
            if EXTENSION_CONDITIONS[name][1] == "in":
                items = v.split(",") if isinstance(v, str) else v
                if not isinstance(items, list) or not items or any(
                        not isinstance(x, str) or not x.strip() for x in items):
                    problems.append(f"{name}: требуется непустой перечень кодов баз")
                else:
                    conditions[name] = sorted({x.strip() for x in items})
            else:
                if not isinstance(v, bool):
                    problems.append(f"{name}: требуется true/false")
                else:
                    conditions[name] = v
        caps.append({"cap_min": int(val) if val is not None else None,
                     "rule": str(r.get("id")), "family": fam, "zone": r.get("zone"),
                     "conditions": conditions, "unknown": sorted(unknown),
                     "problems": problems})
    return caps


def apply_extension_caps(svod: dict, specs: list[dict], dataset: dict | None = None,
                         ) -> tuple[list[dict], list[str], list[dict], list[dict]]:
    """Жёсткий предел семьи с ПРОДЛЕНИЕМ — это потолок продления, но
    АДРЕСНО (аудит A8, 04.09.2026).

    Семьи предела и его продления дополняют друг друга по построению
    базы (duty_limits ↔ duty_extension_penalty): базовый предел судит
    мягкое правило штрафом, а недопустимым решение делает только
    превышение ПОТОЛКА — у тех смен, что удовлетворяют условиям
    исключения (база, явное согласие; EXTENSION_CONDITIONS). Прежняя
    редакция поднимала порог проверки до потолка для ВСЕХ смен без
    проверки условий (класс ошибки — исключение шире своей области:
    продление базы А повышало предел базе Б). Теперь компилированная
    проверка предела семьи заменяется проверкой КОДА: предел строки =
    потолок, если условия выполнены, иначе базовый; условие вне
    словаря или без требуемой базы — «не прогнана: причина». Без
    положительного факта требуемого согласия сохраняется базовый
    предел. Совпадающие применимые потолки совместимы; различные
    без заданного порядка — адресная неоднозначность, не min/max.

    Возврат: (спецификации без заменённых, заметки, проверки кода,
    находки кода). Без потолка в своде — всё как есть; каталог шаблонов
    (дополняющие семьи) читается только при потолке в своде."""
    by_id = {str(r.get("id")): r for r in (svod.get("rules") or [])
             if isinstance(r, dict)}
    caps = _cap_rules(svod)
    notes: list[str] = []
    if not caps:
        return specs, notes, [], []
    kept: list[dict] = []
    checks: list[dict] = []
    findings: list[dict] = []
    for spec in specs:
        rid = str((spec.get("basis") or {}).get("rule") or "")
        rule = by_id.get(rid)
        fam = _rule_family(rule) if rule else None
        params = spec.get("params")
        applicable = []
        if fam and isinstance(params, dict) \
                and spec.get("template") == "максимальная-длительность-часов" \
                and spec.get("status") == "скомпилирована":
            applicable = [c for c in caps if _complementary_families(fam, c["family"])]
        base_h = params.get("max_hours") if isinstance(params, dict) else None
        if not applicable or not isinstance(base_h, (int, float)) or isinstance(base_h, bool):
            kept.append(spec)
            continue
        applicable = [c for c in applicable if c["problems"] or c["unknown"]
                      or c["cap_min"] > float(base_h) * 60]
        if not applicable:
            kept.append(spec)
            continue
        check, found = _run_cap_check(spec, dataset or {}, float(base_h),
                                      applicable, len(findings))
        checks.append(check)
        findings.extend(found)
        notes.append(f"{rid}: {check.get('cap_note') or check.get('reason')}")
    return kept, notes, checks, findings


def _complementary_families(fam_a, fam_b) -> bool:
    """Дополняют ли семьи друг друга (предел ↔ продление) — по полю
    complements записей каталога шаблонов (симметрия X01), тем же
    экземпляром, что у реестра (шаг 2 каталога: лексикон CDPM с его
    тремя уровнями запаса ушёл; без каталога — отказ словами
    CatalogError, запаса из кода нет)."""
    if not fam_a or not fam_b or fam_a == fam_b:
        return False
    from vmp import catalog_access as _ca
    comps = _ca.complements()
    return fam_b in comps.get(fam_a, ()) or fam_a in comps.get(fam_b, ())


def _run_cap_check(spec: dict, dataset: dict, base_h: float,
                   caps: list[dict], n_found: int) -> tuple[dict, list[dict]]:
    """Проверка предела смены с адресным потолком — кодом по листу
    спецификации. Исход — ровно один: «прогнана (rows_checked)» либо
    «не прогнана: причина»."""
    p = spec["params"]
    rid = str((spec.get("basis") or {}).get("rule") or "")
    sheet_ref = str(p.get("sheet") or "")
    sid, _, sheet = sheet_ref.partition("/")
    table = ((dataset.get("tables") or {}).get(sid) or {}).get(sheet) or {}
    rows = table.get("rows") or []
    conditions = {name for cap in caps for name in cap["conditions"]}
    cp = dict(p, max_hours=base_h, cap_policies=caps)
    if len(caps) == 1:
        cap = caps[0]
        cp.update(cap_hours=cap["cap_min"] / 60.0 if cap["cap_min"] is not None else None,
                  cap_rule=cap["rule"], conditions=cap["conditions"])
    check = {"id": spec["id"], "basis": {"rule": rid},
             "template": spec.get("template"),
             "params": cp,
             "statement": spec.get("statement") or "",
             "unit": spec.get("unit") or "смена",
             "severity": spec.get("severity", "КРИТИЧЕСКОЕ"),
             "compiled_by": "код (адресный потолок продления)"}
    reason = None
    for cap in caps:
        if cap["unknown"]:
            reason = (f"условие продления {', '.join(cap['unknown'])} правила "
                      f"{cap['rule']} не поддаётся проверке сертификатором "
                      f"(словарь: {', '.join(sorted(EXTENSION_CONDITIONS))})")
            break
        if cap["problems"]:
            reason = f"правило {cap['rule']}: " + "; ".join(cap["problems"])
            break
    if not reason and not rows:
        reason = f"в решении нет листа {sheet_ref} — разметки смен нет"
    if reason:
        check.update(status="не прогнана", rows_checked=0, reason=reason)
        return check, []
    found: list[dict] = []
    limits: list[dict] = []
    for i, r in enumerate(rows, start=2):
        a = dc.parse_moment(r.get(p["start_column"], ""))
        b = dc.parse_moment(r.get(p["end_column"], ""))
        selected = []
        for cap in caps:
            # Без явного «да» требующая согласия политика неприменима;
            # её неизвестная база не должна снимать базовую HARD-проверку.
            if cap["conditions"].get("extension_consent") is True \
                    and _consent_flag(r.get("Согласие на продление")) != "да":
                continue
            matched = True
            for name, want in cap["conditions"].items():
                col, kind = EXTENSION_CONDITIONS[name]
                if kind == "in":
                    cell = str(r.get(col) or "").strip()
                    if not cell:
                        check.update(status="не прогнана", rows_checked=0,
                                     reason=f"{sheet_ref}: строка {i}, правило {cap['rule']}: "
                                            f"нет «{col}», область продления не проверить")
                        return check, []
                    matched = matched and cell in want
                # false означает отсутствие требования, true уже проверено.
            if matched:
                selected.append(cap)
        if len({c["cap_min"] for c in selected}) > 1:
            check.update(status="не прогнана", rows_checked=0,
                         reason=f"{sheet_ref}: строка {i}, сотрудник "
                                f"{r.get('Табельный номер', '?')}: неоднозначные "
                                "применимые потолки без заданного порядка: " +
                                ", ".join(f"{c['rule']}={c['cap_min']} мин" for c in selected))
            return check, []
        extended = bool(selected)
        limit = selected[0]["cap_min"] / 60.0 if selected else base_h
        limits.append({
            "row": i, "employee": r.get("Табельный номер"), "limit_hours": limit,
            "cap_rules": [c["rule"] for c in selected]})
        if a is None or b is None or b < a \
                or (b - a).total_seconds() > limit * 3600:
            n_found += 1
            span = ((b - a).total_seconds() / 3600.0) if (a and b) else None
            why = ("потолок продления" if extended
                   else "базовый предел: условия продления не выполнены")
            found.append({
                "id": f"E-{n_found:04d}", "check": spec["id"],
                "severity": check["severity"], "class": "нарушение правила",
                "target": {"sid": sid, "sheet": sheet, "row": i},
                "text": (f"длительность {span:.2f} ч вне предела {limit:g} ч "
                         f"({why})" if span is not None
                         else "времена смены не читаются"),
                "evidence": (f"{sid}!строка {i}: {p['start_column']}="
                             f"«{r.get(p['start_column'], '')}», "
                             f"{p['end_column']}=«{r.get(p['end_column'], '')}»"
                             + (f", Табельный номер="
                                f"«{r.get('Табельный номер', '')}»"
                                if r.get("Табельный номер") else "")
                             + "".join(f", {EXTENSION_CONDITIONS[n][0]}="
                                       f"«{r.get(EXTENSION_CONDITIONS[n][0], '')}»"
                                       for n in sorted(conditions))),
                "rule_refs": [rid]})
    check.update(status="прогнана", rows_checked=len(rows), duty_limits=limits)
    check["cap_note"] = (
        "предел строки: потолок продления из применимых политик " +
        "; ".join(f"{c['rule']} ({c['family']}): {c['cap_min']} мин, "
                  f"{c['conditions'] or 'без условий'}" for c in caps) +
        f"; иначе базовый предел {base_h:g} ч; "
        f"превышение базового предела у продлённой смены судится штрафом")
    return check, found


def world_param_checks(svod: dict, thresholds=None,
                       run_info=None) -> tuple[list[dict], dict]:
    """Правила семей-параметров мира (шкала времени) — записью
    «применена конфигурацией», компилятору они не подаются. Возврат:
    (проверки-записи, свод без этих правил). Свидетельство — сдвиг
    шкалы из порогов прогона (lastRun.thresholds / applied) либо из
    параметра посадки правила; слова, не догадка."""
    keep: list[dict] = []
    checks: list[dict] = []
    applied_tz = None
    if isinstance(thresholds, dict):
        for a in thresholds.get("applied") or []:
            if isinstance(a, dict) and a.get("threshold") == "base_tz_offset_min":
                applied_tz = a.get("value")
    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or _rule_family(r) not in WORLD_PARAM_FAMILIES \
                or r.get("kind") not in ("HARD", "CASE"):
            keep.append(r)
            continue
        _validate_typed_landing(r)
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        p = (fit.get("params") or {}).get("base_tz_offset_min")
        declared = p.get("value") if isinstance(p, dict) else p
        value = applied_tz if applied_tz is not None else declared
        words = ("применена конфигурацией прогона: сдвиг шкалы меток %s мин "
                 "действует при чтении всех времён решения (%s); построчного "
                 "исхода у параметра мира нет по замыслу"
                 % ("не задан" if value is None else "%+d" % int(value),
                    "порог прогона решателя" if applied_tz is not None
                    else "параметр посадки правила"))
        checks.append({"id": "C-%s-мир" % r.get("id"),
                       "basis": {"rule": str(r.get("id"))},
                       "template": WORLD_PARAM_TEMPLATE,
                       "params": {"base_tz_offset_min": value},
                       "statement": str(r.get("statement") or r.get("title") or ""),
                       "unit": "прогон", "severity": "СПРАВОЧНО",
                       "status": WORLD_PARAM_STATUS, "rows_checked": 0,
                       "reason": words,
                       "compiled_by": "код (параметр мира)"})
    out = dict(svod)
    out["rules"] = keep
    return checks, out


def normalize_composition_specs(svod: dict, specs: list[dict]) -> list[str]:
    """Единица счёта состава — от семьи, не от компилятора: у правила
    семьи crew_composition группа — рейс листа «Назначения» (различные
    табельные номера на рейсе). Правит спецификации на месте, возвращает
    заметки словами о каждой правке."""
    by_id = {str(r.get("id")): r for r in svod.get("rules") or []
             if isinstance(r, dict)}
    notes: list[str] = []
    for spec in specs:
        if spec.get("template") != "счёт-состава-группы":
            continue
        rid = str((spec.get("basis") or {}).get("rule") or "")
        fam = _rule_family(by_id[rid]) if rid in by_id else None
        want = COMPOSITION_GROUP_BY_FAMILY.get(fam or "")
        if not want:
            continue
        _validate_typed_landing(by_id[rid], unconditional=("crew_size",))
        sheet, column, unit = want
        params = spec.get("params")
        if not isinstance(params, dict):
            continue
        sid = str(params.get("sheet") or "SOL/").partition("/")[0] or "SOL"
        changes = []
        if params.get("group_column") != column:
            changes.append("группа «%s» → «%s»" % (params.get("group_column"), column))
            params["group_column"] = column
        if params.get("sheet") != "%s/%s" % (sid, sheet):
            changes.append("лист «%s» → «%s/%s»" % (params.get("sheet"), sid, sheet))
            params["sheet"] = "%s/%s" % (sid, sheet)
        if not params.get("distinct_column"):
            params["distinct_column"] = "Табельный номер"
            changes.append("счёт различных «Табельный номер»")
        # Численность — из посадки правила (crew_size), не из текста:
        # детерминированная компиляция там, где параметр посажен.
        fit = by_id[rid].get("template_fit") if rid in by_id else None
        cs = ((fit.get("params") or {}).get("crew_size")
              if isinstance(fit, dict) else None)
        cs = cs.get("value") if isinstance(cs, dict) else cs
        if isinstance(cs, int) and not isinstance(cs, bool) and cs >= 1 \
                and params.get("count") != cs:
            changes.append("численность %r → %d (параметр посадки crew_size)"
                           % (params.get("count"), cs))
            params["count"] = cs
        if spec.get("unit") != unit:
            spec["unit"] = unit
        if changes:
            note = ("%s: единица счёта состава задана семьёй %s — %s "
                    "(предложение компилятора не принято)"
                    % (rid, fam, "; ".join(changes)))
            spec["unit_note"] = note
            notes.append(note)
    return notes


def reserve_code_checks(svod: dict, dataset: dict) -> tuple[list[dict], dict]:
    """Проверки норм резерва — КОДОМ по сводке «Резерв» решения
    (строка = база × сутки, колонка «Нехватка» = max(0, норма −
    свободные экипажи)): нехватка 0 у каждой строки. Правила семьи
    reserve_floors из компиляции моделью изымаются — детерминизм
    вместо вероятностной спецификации. Возврат: (спецификации, свод
    без изъятых правил). Нет сводки — ничего не изымается: компилятор
    честно скажет «листа нет»."""
    sheets = (dataset.get("tables") or {}).get(SOLUTION_SID) or {}
    if "Резерв" not in sheets:
        return [], svod
    specs: list[dict] = []
    taken: set[str] = set()
    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or r.get("kind") not in ("HARD", "CASE"):
            continue
        if _rule_family(r) != "reserve_floors":
            continue
        rid = str(r.get("id"))
        specs.append({
            "id": f"C-{rid}-код", "basis": {"rule": rid},
            "template": "значение-в-диапазоне",
            "params": {"sheet": f"{SOLUTION_SID}/Резерв",
                       "column": "Нехватка", "min": 0, "max": 0},
            "unit": "база × сутки", "severity": "КРИТИЧЕСКОЕ",
            "status": "скомпилирована", "compiled_by": "код",
            "statement": f"Нехватка резерва равна 0 в каждой паре "
                         f"(база, сутки) — {str(r.get('statement') or '')[:120]}"})
        taken.add(rid)
    if not specs:
        return [], svod
    out = dict(svod)
    out["rules"] = [r for r in svod.get("rules") or []
                    if str(r.get("id")) not in taken]
    return specs, out


#: Класс находки-МЕРЫ: число совпадений мягкого правила на решении.
#: Не нарушение допустимости — вход штрафной сверки Д4 (пункт 3):
#: совпадения по сертификату против совпадений решателя по той же
#: строке. Вердикт допустимости такие находки не трогают.
SOFT_MEASURE_CLASS = "мера мягкого правила"
SOFT_MEASURE_TEMPLATE = "мера-мягкого-правила"
#: Проверка-запись параметра мира (семьи WORLD_PARAM_FAMILIES): правило
#: применено конфигурацией прогона, построчной проверки у него нет и
#: быть не может (проход 6 H0-К, 05.09.2026: правило шкалы времени шло
#: компилятору, тот честно отказывал, а строгое покрытие A8 считало его
#: нескомпилированным — сертификат пейринга не выдавался).
WORLD_PARAM_TEMPLATE = "параметр-мира-применён"
#: Статус записи — из перечня контракта отчёта 3.3 («не применима»:
#: построчного исхода у параметра мира нет по замыслу); слова о том,
#: что он применён конфигурацией, — в reason. Гейт сертификата (CDPM)
#: судит статусы по перечню и счёт шапки по телу — запись живёт в теле
#: и считается «не прогнанной», как всякая проверка без строк.
WORLD_PARAM_STATUS = "не применима"

#: Единица счёта состава по семье: состав экипажа судится НА РЕЙС (у
#: смены решения идентификатор на сотрудника — «в смене» всегда один
#: пилот). Компилятор-модель волен предложить иную единицу (проход 6
#: H0-К: «смена» → 31 ложное нарушение), код её не принимает.
COMPOSITION_GROUP_BY_FAMILY = {
    # Состав экипажа — по действующим назначениям (без перегонных строк;
    # единый план 05.09.2026, п. 5.12).
    "crew_composition": ("Действующие назначения", "Рейс", "рейс"),
}
_WEEKDAY_INDEX = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}


def _history_summary(dataset: dict) -> dict | None:
    """Шапка сертификата об истории сотрудников (ревизия 1.17): сколько
    строк листа «История» несут историю, срез, суммы остатков месяца —
    чтобы читатель сертификата видел, что остатки дошли, не открывая
    вход. None — листа нет (заявление «история не передана» — у кнопки)."""
    sheet = ((dataset.get("tables") or {}).get(SOLUTION_SID) or {}).get("История")
    if not isinstance(sheet, dict):
        return None
    rows = [r for r in (sheet.get("rows") or []) if isinstance(r, dict)]
    with_hist = [r for r in rows
                 if str(r.get("Есть история") or "").strip().lower() == "да"]

    def _num(v):
        """Число либо None: неизвестный остаток — не ноль (ревью CDPM
        05.09: сумма шапки печатала пустые ячейки нулём)."""
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    blocks = [_num(r.get("Налёт с начала месяца, мин")) for r in with_hist]
    works = [_num(r.get("Рабочее время с начала месяца, мин")) for r in with_hist]
    last = sum(1 for r in with_hist if r.get("Конец последней смены"))
    as_of = next((r.get("Срез") for r in rows if r.get("Срез")), None)
    return {"passed": True, "as_of": as_of,
            "employees_total": len(rows), "with_history": len(with_hist),
            "without_history": sorted(str(r.get("Табельный номер") or "")
                                      for r in rows if r not in with_hist),
            # суммы — только по известным значениям, с числом известных
            "month_block_min_total": int(sum(b for b in blocks if b is not None)),
            "month_block_known": sum(1 for b in blocks if b is not None),
            "month_work_min_total": int(sum(w for w in works if w is not None)),
            "month_work_known": sum(1 for w in works if w is not None),
            "with_last_duty": last}


def _duties_by_employee(dataset: dict) -> dict[str, list[dict]]:
    """Смены решения по сотрудникам, по началу; времена — datetime."""
    from datetime import datetime
    sheet = ((dataset.get("tables") or {}).get(SOLUTION_SID) or {}).get("Смены")
    out: dict[str, list[dict]] = {}
    for i, row in enumerate((sheet or {}).get("rows") or [], start=2):
        try:
            t0 = datetime.fromisoformat(str(row.get("Начало") or "")[:19])
            t1 = datetime.fromisoformat(str(row.get("Конец") or "")[:19])
        except ValueError:
            continue
        out.setdefault(str(row.get("Табельный номер") or ""), []).append({
            "row": i, "id": row.get("Идентификатор"), "start": t0, "end": t1,
            "base": str(row.get("База приписки") or "").strip(),
            "rest_at": str(row.get("Место отдыха") or "").strip()})
    for lst in out.values():
        lst.sort(key=lambda d: d["start"])
    return out


def _layover_nights(duties: dict[str, list[dict]],
                    uncertain_bases: set[str] | None = None,
                    uncertain: list[dict] | None = None,
                    homes_complete: bool | None = None) -> list[dict]:
    """Доказанные ночёвки вне базы (контракт Б, план 05.09.2026): смена
    окончена не в базе приписки, и до следующей смены того же человека
    проходит хотя бы одна календарная ночь (единица семьи — ночь);
    следующая смена в тот же день — ночи нет (дневной отдых).

    Неопределённые события НЕ начисляются автоматически и НЕ считаются
    нулём — они собираются в `uncertain` (список вызывателя): (1) отдых
    на станции из `uncertain_bases` (сертификат пейринга: у цепочки
    людей нет, любая база может быть домом); (2) граница горизонта —
    после ПОСЛЕДНЕЙ смены продолжение неизвестно, ночь не начисляется
    (прежде начислялась одна: «пилот не переносится домой полуночью» —
    это и была выдуманная ночёвка по границе; аудит 05.09.2026);
    (3) станция вне НЕПОЛНОГО универсума приписок (`homes_complete`
    False — у кого-то из сотрудников постановки нет приписки, P-425/
    P-402, единая версия 06.09.2026): ночёвка не доказана — для
    сотрудника без приписки эта станция может быть домом; та же
    причина `incomplete_home_universe`, что у цены цепочки
    (vmp.chain_price.assess_nights), поэтому доказанные ночёвки
    сертификата и пересчёта сходятся (0 у обоих). None — универсум не
    объявлен (каталог без области учёта, ростеринг): как прежде."""
    from datetime import timedelta
    nights: list[dict] = []
    ub = set(uncertain_bases or ())
    for emp, lst in duties.items():
        for i, d in enumerate(lst):
            if not d["base"] or not d["rest_at"]:
                continue
            # Пейринг (uncertain_bases непусто): «база» смены — станция
            # старта цепочки, а не приписка; окончание на ней так же
            # неопределённо, как на любой базе (первый живой проход
            # контракта Б: 94 события у сертификата против 125 у
            # пересчёта — 31 цепочка кончалась на базе старта).
            if d["rest_at"] == d["base"] and d["rest_at"] not in ub:
                continue
            nxt = lst[i + 1] if i + 1 < len(lst) else None
            if nxt is None:
                if uncertain is not None:
                    uncertain.append({"employee": emp, "row": d["row"], "duty": d["id"],
                                      "airport": d["rest_at"], "kind": "boundary",
                                      "note": "граница горизонта: продолжение неизвестно"})
                continue
            if d["rest_at"] in ub:
                if uncertain is not None and nxt["start"].date() > d["end"].date():
                    uncertain.append({"employee": emp, "row": d["row"], "duty": d["id"],
                                      "airport": d["rest_at"], "kind": "rest_at_base",
                                      "note": "отдых на базе: приписка неизвестна"})
                continue
            if homes_complete is False:
                # P-425: станция вне неполного универсума приписок —
                # не доказанная ночь, а неопределённое событие.
                if uncertain is not None and nxt["start"].date() > d["end"].date():
                    uncertain.append({"employee": emp, "row": d["row"], "duty": d["id"],
                                      "airport": d["rest_at"],
                                      "kind": "incomplete_home_universe",
                                      "note": "универсум приписок неполон: станция "
                                              "вне объявленных баз ночёвкой не "
                                              "доказана"})
                continue
            n = (nxt["start"].date() - d["end"].date()).days
            for k in range(n):
                night = d["end"].date() + timedelta(days=k)
                nights.append({"employee": emp, "row": d["row"], "duty": d["id"],
                               "night": night, "airport": d["rest_at"],
                               "horizon_end": False})
    return nights


#: H11-10 (единая версия 06.09.2026): словарь видов неопределённых событий
#: меры (main: kind) ↔ причин независимой оценки области учёта
#: (vmp/run/cost_scope: reason). Граница горизонта у меры = «продолжение
#: после последней смены неизвестно» у оценки — шапка и сверка A6 читают
#: одно событие одним словом.
_KIND_OF_REASON = {"terminal_continuation_unknown": "boundary"}
_REASON_OF_KIND = {"boundary": "terminal_continuation_unknown",
                   "rest_at_base": "rest_at_base_unknown",
                   # P-425: имя причины то же, что у цены цепочки.
                   "incomplete_home_universe": "incomplete_home_universe"}


def _night_assessment(dataset: dict, duties: dict[str, list[dict]],
                      uncertain_bases: set[str],
                      uncertain: list[dict],
                      homes_complete: bool | None = None) -> tuple[list[dict], dict | None]:
    """Диспетчер области учёта ночёвок (H11-4, единая версия 06.09.2026).

    Ростер с ОБЪЯВЛЕННОЙ областью (dataset.window_horizon — горизонт
    пакета, C-27b) судится независимой оценкой vmp/run/cost_scope по
    листу «Назначения»: весь штат (window_employees, включая незанятых),
    весь горизонт, интервалы местоположений; каждое неизвестное — адресом
    и причиной, не нулём; запись решателя о ночёвках
    (solver_layover_cost) сверяется по СОБЫТИЯМ (vmp/run/cost_comparison),
    не по сумме — одинаковая сумма ≠ одинаковые ночи. Без объявленной
    области — прежний путь (_layover_nights по листу «Смены»), но как
    явно частичная диагностика: область учёта не объявлена
    (declared_accounting_horizon_unknown / employee_universe_unknown),
    полная стоимость не подтверждена, сверка событий не проводилась.
    Пейринг — всегда прежний путь (ветка цепочек — H10/H11-9).

    Возврат: (ночи для находок меры, оценка области либо None у пейринга).
    Неопределённые события обеих веток попадают в `uncertain` словарём
    меры (kind; boundary ↔ terminal_continuation_unknown — H11-10)."""
    from datetime import date
    accounting = dataset.get("accounting") if isinstance(dataset.get("accounting"), dict) else {}
    context = dataset.get("cost_context") if isinstance(dataset.get("cost_context"), dict) else {}
    task = str(accounting.get("task") or context.get("task") or "rostering")
    if task == "pairing":
        return _layover_nights(duties, uncertain_bases, uncertain,
                               homes_complete=homes_complete), None
    if isinstance(dataset.get("window_horizon"), dict):
        from vmp.run.cost_scope import assess_roster_cost
        from vmp.run.cost_comparison import compare_roster_cost
        assessment = assess_roster_cost(dataset)
        assessment["source"] = "declared_scope"
        assessment["available"] = True
        assessment["solver_comparison"] = compare_roster_cost(
            assessment, dataset.get("solver_layover_cost"))
        sheet = ((dataset.get("tables") or {}).get(SOLUTION_SID) or {}).get("Назначения") or {}
        duty_of_row = {i: (str(r.get("Смена") or "").strip() or None)
                       for i, r in enumerate(sheet.get("rows") or [], start=2)}
        nights: list[dict] = []
        for e in assessment["known_events"]:
            row = e.get("row")
            sheet_name = e.get("sheet") or "Назначения"
            duty = (duty_of_row.get(row) if sheet_name == "Назначения" else None) \
                or e.get("source") or "назначения"
            nights.append({"employee": e["employee"], "row": row, "sheet": sheet_name,
                           "duty": duty, "night": date.fromisoformat(e["night"]),
                           "airport": e["airport"], "horizon_end": False})
        for u in assessment["unknown_events"]:
            reason = str(u.get("reason"))
            uncertain.append({"employee": u.get("employee"), "row": None, "duty": None,
                              "airport": u.get("station"),
                              "kind": _KIND_OF_REASON.get(reason, reason),
                              "reason": reason, "start": u.get("start"),
                              "end": u.get("end"),
                              "note": f"область учёта: {reason}"})
        return nights, assessment
    nights = _layover_nights(duties, uncertain_bases, uncertain,
                             homes_complete=homes_complete)
    unknown: list[dict] = [{"employee": None, "reason": "declared_accounting_horizon_unknown",
                            "start": None, "end": None, "station": None}]
    if not isinstance(dataset.get("window_employees"), list) or not dataset.get("window_employees"):
        unknown.append({"employee": None, "reason": "employee_universe_unknown",
                        "start": None, "end": None, "station": None})
    for u in uncertain:
        reason = _REASON_OF_KIND.get(u["kind"], u["kind"])
        u.setdefault("reason", reason)
        unknown.append({"employee": u["employee"], "reason": reason,
                        "start": None, "end": None, "station": u.get("airport"),
                        "row": u.get("row"), "duty": u.get("duty")})
    assessment = {
        "version": 2, "contract": "B", "task": task, "source": "legacy_duties_sheet",
        "scope": "duties_sheet_adjacent_gaps_without_declared_scope",
        "night_definition": "calendar_date_transition_at_station",
        "duty_semantics": "duties_sheet_identity", "timezone": "input_timestamp_calendar",
        "scope_start": None, "scope_end": None, "employees": sorted(duties),
        "known_nights": len(nights),
        "known_events": [{"employee": x["employee"], "night": x["night"].isoformat(),
                          "airport": x["airport"], "row": x["row"], "sheet": "Смены",
                          "duty": x["duty"], "source": "duties_sheet"} for x in nights],
        "unknown_count": len(unknown), "unknown_events": unknown,
        "complete": False, "certainty": "partial", "available": True,
        "full_cost_confirmed": False, "solver_comparison": None,
        "note": ("область учёта не объявлена (в решении нет window_horizon): "
                 "ночёвки по листу «Смены» — частичная диагностика; сверка "
                 "событий с записью решателя не проводилась"),
    }
    return nights, assessment


def _extension_base(svod: dict, extension: dict) -> int:
    """Строгая общая HARD-база duty_limits из того же среза ВМП.

    Как у package reader, при наличии legacy params первичен этот
    носитель; иначе используется посадка. Не использует SOFT/OUT,
    исключённые правила, потолок продления или константу исполнителя.
    """
    from vmp.chain_price import ChainPriceError, extension_threshold
    from vmp.registry_check import is_excluded_until_data

    values = []
    zone = extension.get("zone")
    for rule in svod.get("rules") or []:
        if (not isinstance(rule, dict) or rule.get("kind") != "HARD"
                or is_excluded_until_data(rule) or _rule_family(rule) != "duty_limits"):
            continue
        if zone in ("PAIRING", "ROSTERING") and rule.get("zone") not in (zone, "BOTH"):
            continue
        try:
            _validate_typed_landing(rule)
        except CertifyError as exc:
            raise ChainPriceError(str(exc)) from exc
        if "params" in rule:
            cells = [p for p in (rule.get("params") or [])
                     if isinstance(p, dict) and p.get("name") == "max_duty_duration"]
        else:
            fit = rule.get("template_fit") or {}
            params = fit.get("params") or {}
            cells = [params["max_duty_duration"]] if "max_duty_duration" in params else []
        for cell in cells:
            if (not isinstance(cell, dict) or "pending_revision" in cell
                    or cell.get("status") == "pending"
                    or cell.get("unit") not in (None, "duration_minutes")
                    or cell.get("relation") not in (None, "max")
                    or cell.get("condition") is not None or cell.get("qualifier")):
                raise ChainPriceError("базовый предел правила %s неполон или не является "
                                      "безусловной верхней границей в минутах" % rule.get("id"))
            values.append(extension_threshold(cell.get("value"), {}, rule.get("id")))
    if not values:
        raise ChainPriceError("правило продления %s: нет базового HARD-предела "
                              "max_duty_duration семьи duty_limits" % extension.get("id"))
    return min(values)


def measure_soft_rules(svod: dict, dataset: dict) -> tuple[list[dict], list[dict]]:
    """Меры мягких правил на решении — КОДОМ, по семье посадки, без
    модели: единицы совпадений те же, что у исполнителей решателя
    (ночь, смена). Возврат: (проверки, находки-меры).

    Семьи: layover_penalty — ночь вне базы; calendar_windows — ночь
    вне базы, начатая в день окна (export_solver.days, местное время
    базы); duty_extension_penalty — смена длиннее базового HARD-предела,
    long_duty_threshold допустим только как равный ему alias.
    Семья без согласованной единицы совпадения
    (balanced_workload) — честная «не прогнана» с причиной, не догадка."""
    checks: list[dict] = []
    findings: list[dict] = []
    duties = _duties_by_employee(dataset)
    # Без листа смен меры по ночам и продлениям не считаются (их ветви
    # дадут «не прогнана»), но мера равномерности идёт по назначениям
    # — досрочного выхода нет.
    n_rows = sum(len(v) for v in duties.values())
    nights = None
    n_find = 0
    # Область учёта (контракт Б): у пейринга отдых на любой базе и конец
    # цепочки неопределённы; у ростеринга приписка известна, неопределённа
    # только граница горизонта. Неопределённые события — в проверку
    # словами и числом, не нулём.
    accounting = dataset.get("accounting") if isinstance(dataset.get("accounting"), dict) else {}
    uncertain_bases = set(accounting.get("uncertain_bases") or ())
    # P-425/P-402: полнота универсума приписок из области учёта подписи
    # каталога (pairing_input_from_catalog); None у каталогов без области
    # и у ростеринга — как прежде.
    homes_complete = accounting.get("eligible_bases_complete")
    if not isinstance(homes_complete, bool):
        homes_complete = None
    uncertain: list[dict] = []
    # H11-4/H11-5: оценка области учёта (объявленная область → cost_scope +
    # паритет событий с решателем; иначе — явно частичная диагностика по
    # «Сменам») считается один раз и питает меры ночёвок; она едет в
    # проверку (cost_scope) и в набор (cost_assessment → шапка сертификата).
    assessment: dict | None = None

    def _nights():
        nonlocal nights, assessment
        if nights is None:
            nights, assessment = _night_assessment(dataset, duties, uncertain_bases, uncertain,
                                                   homes_complete=homes_complete)
            if assessment is not None:
                dataset["cost_assessment"] = assessment
        return nights

    def _mark_uncertain(spec: dict) -> None:
        if assessment is not None:
            spec["cost_scope"] = assessment
        if uncertain:
            kinds: dict[str, int] = {}
            reasons: dict[str, int] = {}
            for u in uncertain:
                kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
                reason = u.get("reason") or _REASON_OF_KIND.get(u["kind"], u["kind"])
                reasons[reason] = reasons.get(reason, 0) + 1
            spec["uncertain_events"] = len(uncertain)
            spec["uncertain_by_kind"] = kinds
            spec["uncertain_by_reason"] = reasons
            spec["uncertain_sample"] = [
                {"employee": u["employee"], "duty": u["duty"], "airport": u["airport"],
                 "kind": u["kind"],
                 "reason": u.get("reason") or _REASON_OF_KIND.get(u["kind"], u["kind"])}
                for u in uncertain[:20]]
            spec["note"] = ((spec.get("note") + "; ") if spec.get("note") else "") + (
                "неопределённый вклад: %d событий (%s) — не начислен и не ноль, "
                "судится там, где известны приписка и продолжение"
                % (len(uncertain), ", ".join("%s %d" % kv for kv in sorted(kinds.items()))))

    def _check(rid: str, unit: str, statement: str, status: str,
               reason: str | None = None, rows: int | None = None) -> dict:
        spec = {"id": f"C-{rid}-мера", "basis": {"rule": rid},
                "template": SOFT_MEASURE_TEMPLATE, "unit": unit,
                "status": status, "statement": statement}
        if reason:
            spec["reason"] = reason
        if rows is not None:
            spec["rows_checked"] = rows
        checks.append(spec)
        return spec

    def _find(spec: dict, rid: str, row: int, text: str, evidence: str,
              sheet: str = "Смены") -> None:
        # H11-5: находка адресуется в лист-источник события (ночь по
        # объявленной области — строка «Назначений» либо «Истории
        # местоположений»; прежний путь — «Смены»).
        nonlocal n_find
        n_find += 1
        findings.append({"id": f"M-{n_find:04d}", "check": spec["id"],
                         "severity": "СПРАВОЧНО", "class": SOFT_MEASURE_CLASS,
                         "target": {"sid": SOLUTION_SID, "sheet": sheet, "row": row},
                         "text": text, "evidence": evidence, "rule_refs": [rid]})

    for r in svod.get("rules") or []:
        if not isinstance(r, dict) or r.get("kind") != "SOFT":
            continue
        rid = str(r.get("id"))
        fam = _rule_family(r)
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        params = fit.get("params") or {}
        stmt = str(r.get("statement") or "")[:140]
        if fam == "layover_penalty":
            nights = _nights()
            spec = _check(rid, "ночь", stmt, "прогнана", rows=n_rows)
            for x in nights:
                _find(spec, rid, x["row"],
                      f"ночёвка вне базы: {x['employee']} в {x['airport']}",
                      f"смена {x['duty']}, ночь {x['night'].isoformat()}",
                      sheet=x.get("sheet") or "Смены")
            _mark_uncertain(spec)
        elif fam == "calendar_windows":
            days = [str(d).lower() for d in ((r.get("export_solver") or {}).get("days") or [])]
            idx = {_WEEKDAY_INDEX[d] for d in days if d in _WEEKDAY_INDEX}
            if not idx:
                _check(rid, "ночь", stmt, "не прогнана",
                       reason="окно дней недели у правила не задано (export_solver.days)")
                continue
            nights = _nights()
            spec = _check(rid, "ночь", stmt, "прогнана", rows=n_rows)
            for x in nights:
                if x["night"].weekday() in idx:
                    _find(spec, rid, x["row"],
                          f"ночёвка вне базы в день окна: {x['employee']} в {x['airport']}",
                          f"смена {x['duty']}, ночь {x['night'].isoformat()} "
                          f"({x['night'].strftime('%A').lower()})",
                          sheet=x.get("sheet") or "Смены")
            _mark_uncertain(spec)
        elif fam == "duty_extension_penalty":
            from vmp.chain_price import ChainPriceError, extension_alias_params, extension_threshold
            try:
                thr = extension_threshold(_extension_base(svod, r), extension_alias_params(r), rid)
            except ChainPriceError as exc:
                _check(rid, "смена", stmt, "не прогнана",
                       reason=str(exc))
                continue
            spec = _check(rid, "смена", stmt, "прогнана", rows=n_rows)
            for emp, lst in duties.items():
                for d in lst:
                    span = int((d["end"] - d["start"]).total_seconds() // 60)
                    if span > thr:
                        _find(spec, rid, d["row"],
                              f"смена сверх базового предела: {emp}, {span} мин > {int(thr)}",
                              f"смена {d['id']}: {d['start'].isoformat()} → {d['end'].isoformat()}")
        elif fam == "balanced_workload":
            # Решение владельца 03.09 по VN-050 («входит в разброс»):
            # исполнитель следует ТЕКСТУ правила — кредитные часы,
            # штатный список (пилот без назначений — 0 часов), порог
            # правила. Мера: √Σ(xᵢ−x̄)² по часам налёта всех сотрудников
            # штата; единицы штрафа = max(0, floor(мера − порог)); порог
            # — параметр посадки workload_spread_threshold. Это
            # величина, не счёт нарушений: сверка по строке идёт
            # равенством measure.value.
            try:
                _validate_typed_landing(r)
            except CertifyError as exc:
                _check(rid, "кредитные часы разброса", stmt, "не прогнана", reason=str(exc))
                continue
            cell = params.get("workload_spread_threshold")
            thr = cell.get("value") if isinstance(cell, dict) else cell
            m = _workload_spread(dataset, thr)
            if m is None:
                _check(rid, "кредитные часы разброса", stmt, "не прогнана",
                       reason="лист назначений решения без времён рейсов — "
                              "налёт по сотрудникам не считается")
                continue
            spec = _check(rid, "кредитные часы разброса", stmt, "прогнана",
                          rows=m["n"])
            spec["measure"] = {"kind": "spread_hours_over_threshold",
                               "value": m["units"],
                               "spread_hours": m["spread_hours"],
                               "threshold_hours": m["threshold"],
                               "n": m["n"], "mean_hours": m["mean_hours"],
                               "reading": "√Σ(xᵢ−x̄)² по кредитным часам всех "
                                          "сотрудников штата (без назначений — "
                                          "0), единицы = превышение порога "
                                          "правила, усечено до целого"}
        elif fam:
            _check(rid, "?", stmt, "не прогнана",
                   reason=f"единица совпадения семьи {fam!r} у решателя не "
                          f"согласована — мера не считается, чтобы не выдавать "
                          f"догадку за сверку")
    return checks, findings


def _workload_spread(dataset: dict, threshold_hours) -> dict | None:
    """Разброс кредитных часов: √Σ(xᵢ−x̄)² по часам налёта ВСЕХ
    сотрудников штата (справочник «Экипажи» входной книги; без
    назначений — 0 часов; без справочника — сотрудники с назначениями),
    единицы штрафа = max(0, floor(разброс − порог)). Решение владельца
    03.09: пилот без рейсов входит в разброс, порог правила читается.
    Возврат: {units, spread_hours, threshold, n, mean_hours} либо None,
    если времён рейсов нет."""
    import math
    from datetime import datetime
    tables = dataset.get("tables") or {}
    sheet = (tables.get(SOLUTION_SID) or {}).get("Назначения") or {}
    minutes: dict[str, int] = {}
    for row in sheet.get("rows") or []:
        emp = str(row.get("Табельный номер") or "").strip()
        try:
            t0 = datetime.fromisoformat(str(row.get("Время вылета") or "")[:19])
            t1 = datetime.fromisoformat(str(row.get("Время прилёта") or "")[:19])
        except ValueError:
            continue
        if not emp:
            continue
        minutes[emp] = minutes.get(emp, 0) + int((t1 - t0).total_seconds() // 60)
    if not minutes:
        return None
    for sid, book in tables.items():
        if sid == SOLUTION_SID or not isinstance(book, dict):
            continue
        crew = book.get("Экипажи") or {}
        for row in crew.get("rows") or []:
            emp = str(row.get("Табельный номер") or "").strip()
            if emp:
                minutes.setdefault(emp, 0)
    hours = [v / 60.0 for v in minutes.values()]
    n = len(hours)
    mean = sum(hours) / n
    spread = math.sqrt(sum((x - mean) ** 2 for x in hours))
    thr = float(threshold_hours) if isinstance(threshold_hours, (int, float)) \
        and not isinstance(threshold_hours, bool) else 0.0
    units = max(0, int(math.floor(spread - thr)))
    return {"units": units, "spread_hours": round(spread, 2), "threshold": thr,
            "n": n, "mean_hours": round(mean, 2)}


def _rule_family(rule: dict) -> str | None:
    """Семья механики правила: посадка (A/B) первична, легаси
    export_solver.family — обратная совместимость сводов до пакета
    посадки (тот же порядок, что у решателя, 8e21ed8)."""
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
        return fit.get("family")
    return (rule.get("export_solver") or {}).get("family")


def pairing_scope_svod(svod: dict) -> tuple[dict, list[dict]]:
    """Свод для сертификации пейринга: правила зоны PAIRING|BOTH;
    людские семьи — отложены поимённо (судятся на ростеринге).
    Возврат: (свод-срез, перечень отложенных).

    Правило с пометкой «исключено до получения данных по решению
    авиакомпании» в перечень отложенных НЕ уходит, даже если его семья
    людская: у него своя причина непроверки, и сертификат называет её
    отдельной строкой (аудит №2) — иначе исключённое правило зоны
    пейринга объявлялось бы «судится на ростеринге», где его тоже не
    проверят."""
    from vmp.registry_check import is_excluded_until_data
    rules_in: list[dict] = []
    judged: list[dict] = []
    for r in svod.get("rules") or []:
        if r.get("zone") not in ("PAIRING", "BOTH"):
            continue
        if is_excluded_until_data(r):
            rules_in.append(r)     # certify отделит и назовёт причину
            continue
        fam = _rule_family(r)
        if fam in PEOPLE_FAMILIES:
            judged.append({"rule_id": str(r.get("id")),
                           "family": fam,
                           "title": r.get("title") or ""})
            continue
        rules_in.append(r)
    out = dict(svod)
    out["rules"] = rules_in
    return out, judged


def pairing_input_from_catalog(catalog: dict,
                               flights_map: dict) -> dict:
    """Каталог цепочек построителя -> входной контракт сертификатора:
    цепочка — псевдо-сотрудник со своей линией времени; разметка смен
    — duty_fns ПОСТРОИТЕЛЯ (границы смен — его интерпретация,
    сертификатор их не вычисляет). Плечо без записи в карте рейсов —
    отказ: молчаливый пропуск исказил бы вердикты."""
    assignments: list[dict] = []
    duties: list[dict] = []
    pairings: list[dict] = []
    missing: list[str] = []
    for i, chain in enumerate(catalog.get("selected") or ()):
        cid = f"chain-{i}"
        duty_lists = chain.get("duty_fns") \
            or [list(chain.get("fns") or ())]
        # A6: база псевдо-сотрудника — аэропорт вылета первого по
        # времени перелёта цепочки (цепочка построителя якорена на
        # базу); место отдыха после смены — прилёт последнего по
        # времени перелёта смены. Без них проверки «в базе / вне
        # базы» и ночёвок по сменам на пейринге были слепы.
        first = None
        for fn in (chain.get("fns") or ()):
            rec = flights_map.get(fn)
            if rec is not None and (first is None or rec["t0"] < first["t0"]):
                first = rec
        base = (first.get("dep") or "") if first else ""
        # Контракт Б (C-18): старт цепочки — свойство каталога, не
        # приписка несуществующего сотрудника; он едет отдельным полем
        # chain_start_airport, а «база» остаётся станцией старта только
        # для проверок «в базе / вне базы» на пейринге (accounting ниже
        # называет отдых на объявленных базах неопределённым).
        start_airport = base
        p0 = p1 = None
        for j, fns in enumerate(duty_lists):
            duty_id = f"{cid}-duty-{j}"
            d0 = d1 = None
            last_arr = ""
            for fn in fns:
                rec = flights_map.get(fn)
                if rec is None:
                    missing.append(str(fn))
                    continue
                assignments.append({
                    "employee": cid, "flight": str(fn),
                    "dep_time": rec["t0"], "arr_time": rec["t1"],
                    "dep_airport": rec.get("dep") or "",
                    "arr_airport": rec.get("arr") or "",
                    "base": base,
                    "chain_start_airport": start_airport,
                    "duty_id": duty_id})
                d0 = rec["t0"] if d0 is None else min(d0, rec["t0"])
                if d1 is None or rec["t1"] >= d1:
                    d1 = rec["t1"]
                    last_arr = rec.get("arr") or ""
            if d0 and d1:
                duties.append({"employee": cid, "id": duty_id,
                               "start": d0, "end": d1,
                               "base": base,
                               "last_arr_airport": last_arr})
                p0 = d0 if p0 is None else min(p0, d0)
                p1 = d1 if p1 is None else max(p1, d1)
        if p0 and p1:
            pairings.append({"employee": cid, "id": cid,
                             "start": p0, "end": p1})
    if missing:
        raise CertifyError(
            "плечи каталога без записи в карте рейсов: "
            + ", ".join(sorted(set(missing))[:5]))
    if not assignments:
        raise CertifyError("каталог пуст — сертифицировать нечего")
    # Контракт Б (05.09.2026): у цепочки нет людей — отдых на любой
    # объявленной базе и конец цепочки для меры ночёвок неопределённы;
    # область учёта сертификата пейринга та же, что у цены построителя:
    # универсум приписок из config_sig.price_scope (единая версия
    # 06.09.2026, P-402); строка --bases — только у каталогов без области.
    sig = catalog.get("config_sig") if isinstance(catalog.get("config_sig"), dict) else {}
    scope = sig.get("price_scope") if isinstance(sig.get("price_scope"), dict) else None
    complete = None
    if scope is not None and isinstance(scope.get("eligible_bases"), (list, tuple)):
        bases = sorted(str(b).strip() for b in scope["eligible_bases"] if str(b).strip())
        complete = scope.get("eligible_bases_complete")
    else:
        bases = sorted(b.strip() for b in str(sig.get("bases") or "").split(",") if b.strip())
    return {"solution": "пейринг (каталог цепочек)",
            "assignments": assignments,
            "duties": duties, "pairings": pairings,
            "accounting": {"task": "pairing", "uncertain_bases": bases,
                           # P-402/P-425: полнота универсума приписок
                           # из области учёта подписи (None — каталог без
                           # области: как прежде); мера ночёвок читает её
                           # как homes_complete.
                           "eligible_bases_complete": complete,
                           "boundary": "uncertain",
                           # C-18: подпись постановки — для контекста
                           # цены пересчёта (H-19, после переноса
                           # chain_price GPT); контракт Б.
                           "contract": "B",
                           "config_sig": dict(sig)}}


def recompute_chain_measures(catalog: dict, flights_map: dict) -> list[dict] | None:
    """A6 (05.09.2026): НЕЗАВИСИМЫЙ пересчёт мер мягких правил по каждой
    выбранной цепочке каталога — по карте рейсов и подписи постановки
    (базы, пояс, окно дней недели, порог продления), не по числам
    построителя. Периоды — как у цены: каждая смена (duty_fns) целиком,
    каждый перегон своим периодом. Контекст цены (универсум приписок,
    его полнота, пояс) — строго из области учёта подписи
    (config_sig.price_scope, vmp.chain_price.pricing_context_from_signature);
    строка --bases универсумом не считается. None — подписи постановки
    нет, либо она чужой модели цены / без области учёта: независимого
    пересчёта по контракту Б нет, причину словами называет
    reconcile_pairing («подпись цены: …», несравнимо). Возвращает записи
    vmp.chain_price.comparison_record (nights, uncertain, measures,
    weights, область, события, определённость). Плечо без записи в
    карте рейсов — отказ."""
    from datetime import datetime
    from vmp import chain_price as _cp
    sig = catalog.get("config_sig")
    if not isinstance(sig, dict):
        return None
    try:
        context = _cp.pricing_context_from_signature(sig)
        rules = _cp.soft_rules_from_signature(sig)
    except _cp.ChainPriceError:
        return None

    def _dt(v):
        return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))

    out: list[dict] = []
    missing: list[str] = []
    for chain in catalog.get("selected") or ():
        if not isinstance(chain, dict):
            out.append({})
            continue
        active = list(chain.get("active_fns") or ())
        groups = [list(g) for g in (chain.get("duty_fns")
                                    or [active or list(chain.get("fns") or ())])]
        dh = chain.get("dh")
        if dh is None:
            seen = {fn for g in groups for fn in g}
            dh = [fn for fn in (chain.get("fns") or ()) if fn not in seen]
        groups += [[fn] for fn in dh]
        legs: list[list[dict]] = []
        for g in groups:
            recs = []
            for fn in g:
                rec = flights_map.get(fn)
                if rec is None:
                    missing.append(str(fn))
                    continue
                recs.append({"id": str(fn), "t0": _dt(rec["t0"]),
                             "t1": _dt(rec["t1"]),
                             "arr": rec.get("arr") or "",
                             "dep": rec.get("dep") or ""})
            if recs:
                legs.append(recs)
        if not legs:
            out.append({})
            continue
        periods = _cp.periods_from_legs(legs)
        # Контракт Б (05.09.2026): та же область учёта, что у цены
        # построителя — доказанные внутренние ночёвки; отдых на базе и
        # открытый конец — неопределённый вклад, считается отдельно.
        # Запись сверки — общая форма (comparison_record), ветка критерия
        # — из подписи (иначе области учёта разойдутся по criterion_kind).
        pr = _cp.price(0, periods, rules, crew=1,
                       criterion_kind=sig.get("price_criterion"), **context)
        out.append(_cp.comparison_record(pr))
    if missing:
        raise CertifyError(
            "пересчёт мер: плечи каталога без записи в карте рейсов: "
            + ", ".join(sorted(set(missing))[:5]))
    return out


def certify_pairing(svod: dict, catalog: dict, flights_map: dict,
                    llm, control=None, thresholds=None,
                    run_info=None, strict_coverage: bool = True,
                    builder_report: dict | None = None) -> dict:
    """Сертификат решения ПЕЙРИНГА (поручение владельца 2026-09-01):
    тот же механизм, что у ростеринга — компилятор предлагает, код
    судит, fail-closed — но свод сужен зоной пейринга, а правила о
    людях честно отнесены на ростеринг (у цепочек каталога нет
    назначенных людей).

    strict_coverage — Д4 плана «прямой ход» (см. certify).
    builder_report — отчёт построителя того же прогона: включает
    сверку целевой пейринга по каталогу (сумма стоимости выбранных
    цепочек против общей стоимости отчёта, vmp.penalty_reconcile.
    reconcile_pairing). Расхождение в строгом режиме отзывает
    сертификат; вне строгого — показание."""
    scoped, judged = pairing_scope_svod(svod)
    if not scoped.get("rules"):
        raise CertifyError("в своде нет правил зоны пейринга — "
                           "сертифицировать нечем")
    solution = pairing_input_from_catalog(catalog, flights_map)
    report = certify(scoped, solution, llm, control=control,
                     thresholds=thresholds, run_info=run_info,
                     strict_coverage=strict_coverage)
    cert = report.get("certificate")
    if isinstance(cert, dict):
        cert["task"] = "pairing"
        cert["judged_on_rostering"] = judged
        # Отчёт построителя может ехать внутри каталога (кнопка кладёт
        # его туда тем же каналом, что runInfo в решение): без него
        # сверка целевой пейринга не проводилась вовсе (первые
        # сертификаты H0, 03.09 — pairing_objective_reconciliation null).
        if builder_report is None and isinstance(catalog.get("builder_report"), dict):
            builder_report = catalog["builder_report"]
        if builder_report is not None:
            from vmp import penalty_reconcile as _pr
            # A6: сверка по мерам — независимый пересчёт мер по карте
            # рейсов и подписи постановки против расшифровки цены.
            recomputed = recompute_chain_measures(catalog, flights_map)
            obj = _pr.reconcile_pairing(catalog, builder_report,
                                        recomputed=recomputed)
            # Контракт Б (план 05.09.2026, H0-КИ-окна): третья сторона
            # сверки — мера сертификата по листу «Смены» на ТОЙ ЖЕ
            # области учёта, что цена построителя и пересчёт: доказанные
            # внутренние ночёвки (отдых вне всех баз до смены другого
            # дня). Отдых на базе и конец цепочки — неопределённый
            # вклад: он считается отдельно, и при нём полная стоимость
            # каталога не установлена (cost_certainty = partial), а не
            # «подтверждена». Пересчёт и мера обязаны сойтись по числу
            # доказанных ночёвок; иначе — две правды об одной ночёвке.
            if isinstance(recomputed, list):
                from vmp.run.fit_checks import _family as _rule_family_of
                rec_nights = sum(int(r.get("nights") or 0)
                                 for r in recomputed if isinstance(r, dict))
                rec_uncertain = sum(int(r.get("uncertain") or 0)
                                    for r in recomputed if isinstance(r, dict))
                lay_ids = {str(r.get("id")) for r in (scoped.get("rules") or [])
                           if isinstance(r, dict)
                           and _rule_family_of(r) == "layover_penalty"}
                cert_nights = sum(
                    1 for f in (report.get("findings") or [])
                    if isinstance(f, dict) and f.get("class") == SOFT_MEASURE_CLASS
                    and any(str(x) in lay_ids for x in (f.get("rule_refs") or [])))
                cert_uncertain = sum(
                    int(c.get("uncertain_events") or 0)
                    for c in (report.get("checks") or [])
                    if isinstance(c, dict)
                    and str((c.get("basis") or {}).get("rule")) in lay_ids)
                obj["certificate_nights"] = cert_nights
                obj["recomputed_nights"] = rec_nights
                obj["uncertain_events"] = rec_uncertain
                obj["certificate_uncertain_events"] = cert_uncertain
                obj["accounting"] = ("доказанные внутренние ночёвки по определению "
                                     "ВМП; отдых на базе и открытый конец — "
                                     "неопределённый вклад, не ноль")
                if lay_ids and cert_nights != rec_nights:
                    obj.setdefault("problems", []).append(
                        "доказанных ночёвок по мере сертификата (лист «Смены») "
                        "%d ≠ пересчёт цены по каталогу %d"
                        % (cert_nights, rec_nights))
                    # P-432: расхождение ночёвок — расхождение ИЗВЕСТНОЙ
                    # части, а не только итога: три поля согласованно,
                    # чтобы шапка и политика цены не читали «известная
                    # часть сверена» при разошедшихся ночёвках.
                    obj["verdict"] = _pr.DIVERGE
                    obj["known_subtotal_verdict"] = _pr.DIVERGE
                    obj["full_cost_confirmed"] = False
                    obj["note"] = "; ".join(obj.get("problems") or [])
                cert["cost_certainty"] = ("partial" if (rec_uncertain or cert_uncertain)
                                          else "full")
                if cert["cost_certainty"] == "partial":
                    cert["cost_certainty_note"] = (
                        "полная стоимость каталога не установлена: неопределённый "
                        "вклад %d событий (пересчёт) / %d (сертификат) — отдых на "
                        "базе при неизвестной приписке и открытые концы цепочек; "
                        "судится в ростеринге по фактической приписке"
                        % (rec_uncertain, cert_uncertain))
            cert["pairing_objective_reconciliation"] = obj
            clean = bool(_pr.is_clean(obj))
            cert["pairing_objective_clean"] = clean
            if not clean:
                # Контракт Б (C-20 + группа P, политика H11/P-417):
                # совпавшая известная часть при неизвестной полной цене —
                # не расхождение; шапку и журнал кнопки (первые 200
                # знаков) читают отдельно от тела сверки, поэтому три
                # случая различаются словами, и оба факта первой строки
                # («расхождение», «стоимость не подтверждена») стоят до
                # 200-го знака: настоящее расхождение (DIVERGE, красный);
                # известная часть сверена — носитель ТОЛЬКО
                # known_subtotal_verdict сверки (P-424: при отсутствующем
                # обязательном свидетельстве — ip_objective, подпись цены,
                # расшифровка, пересчёт — известная часть не сверена,
                # совпавшие ночёвки меры (cost_certainty = partial) её не
                # заменяют); иначе — известная часть не подтверждена.
                known_ok = obj.get("known_subtotal_verdict") == _pr.OK
                if obj.get("verdict") == _pr.DIVERGE:
                    cert["note"] = (
                        "сверка целевой пейринга (Д4): расхождение; стоимость "
                        "не подтверждена — сумма стоимости выбранных цепочек, "
                        "общая стоимость отчёта построителя, область учёта "
                        "или меры мягких правил расходятся с независимым "
                        "пересчётом (A6); сертификат не выдаётся; разбор в теле")
                elif known_ok:
                    cert["note"] = (
                        "известная часть стоимости пейринга сверена; полная "
                        "стоимость не установлена (сверка целевой Д4: "
                        "несравнимо); разбор в теле")
                else:
                    cert["note"] = (
                        "полная стоимость пейринга не установлена (сверка "
                        "целевой Д4: несравнимо — нет обязательного "
                        "свидетельства); известная часть не подтверждена; "
                        "сертификат не выдаётся; разбор в теле")
        # A8: флаг и трёхчастный результат пересчитываются после сверки
        # целевой — стоимость и допустимость из тела, не из флага.
        refresh_verdict(cert)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14
    ap = argparse.ArgumentParser(
        description="Сертификация решения против свода CDPM")
    ap.add_argument("model", help="model.json свода")
    ap.add_argument("solution", help="решение (JSON, контракт в "
                                     "докстроке модуля)")
    ap.add_argument("out", help="каталог результата")
    ap.add_argument("--llm", default="cloud",
                    help="канал компилятора: local|cloud|auto")
    ap.add_argument("--thresholds", default=None,
                    help="lastRun.thresholds того же прогона "
                         "решателя (JSON) — машинный раздел "
                         "«формализовано, не исполнялось»")
    ap.add_argument("--run-info", default=None,
                    help="шапка прогона (JSON) для трассировки "
                         "сертификата")
    ap.add_argument("--quals", default=None,
                    help="датированные окна допусков (JSON: "
                         "rows либо список) — правила о "
                         "свежести допуска становятся "
                         "прогоняемыми")
    ap.add_argument("--reserve", default=None,
                    help="лист резерва решателя (JSON: rows либо "
                         "список) — правила о нормах резерва "
                         "становятся прогоняемыми")
    ap.add_argument("--logic", default=None,
                    help="lastRun.logic того же прогона (JSON) — "
                         "пять корзин показаний по правилам с "
                         "logic; судятся гейтом ступени 3")
    args = ap.parse_args(argv)
    from cdpm import llm as llm_mod
    # Коды возврата: 0 — сертифицировано; 1 — не сертифицировано
    # (нарушения либо вакуум); 2 — сертификация не запускалась
    # (вход/канал), чтобы оркестратор различал исходы.
    try:
        svod = json.loads(Path(args.model)
                          .read_text(encoding="utf-8"))
        solution = json.loads(Path(args.solution)
                              .read_text(encoding="utf-8-sig"))
        thresholds = json.loads(
            Path(args.thresholds).read_text(encoding="utf-8-sig")) \
            if args.thresholds else None
        run_info = json.loads(
            Path(args.run_info).read_text(encoding="utf-8-sig")) \
            if args.run_info else None
        logic_report = json.loads(
            Path(args.logic).read_text(encoding="utf-8-sig")) \
            if args.logic else None
        if args.quals:
            qv = json.loads(Path(args.quals)
                            .read_text(encoding="utf-8-sig"))
            solution["qualifications"] = qv["rows"] \
                if isinstance(qv, dict) else qv
        if args.reserve:
            rv = json.loads(Path(args.reserve)
                            .read_text(encoding="utf-8-sig"))
            solution["reserve"] = rv["rows"] \
                if isinstance(rv, dict) else rv
    except (OSError, ValueError) as e:
        print(f"ОТКАЗ: вход не читается: {e}")
        return 2
    try:
        # cosmos_llm(scope, requested=…): первый аргумент — контур
        # доступа, канал просят requested (ревью ступени 1: --llm
        # уезжал в scope и CLI падал при любом значении).
        llm = llm_mod.cosmos_llm("any", requested=args.llm)
    except Exception as e:                                # noqa: BLE001
        print(f"ОТКАЗ: канал компилятора не построился: {e}")
        return 2
    try:
        report = certify(svod, solution, llm,
                         thresholds=thresholds, run_info=run_info,
                         logic_report=logic_report)
    except CertifyError as e:
        print(f"ОТКАЗ: {e}")
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "certificate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    (out / "certificate.md").write_text(
        render_certificate(report), encoding="utf-8")
    v = report["certificate"]
    print(f"проверок прогнано {v['checks_run']}, не прогнано "
          f"{v['checks_not_run']}; нарушений {v['findings']}")
    print(f"записано: {out / 'certificate.json'}")
    return 0 if v["certified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
