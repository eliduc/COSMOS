# -*- coding: utf-8 -*-
"""Раздел «история сотрудника» (спецификация 1.17, аудит A7, ступень L).

Источник у производителя — лист «История» книги операционных данных:
остатки на начало периода по сотруднику (налёт и рабочее время с начала
месяца, последняя смена, серии, местоположение, окна по правилам) и,
отдельным листом «Утверждённые назначения», назначения, переносимые как
закрепления. Модуль даёт три вещи:

1. канон колонок листа (русские заголовки, синонимы) и разбор строки;
2. встроенные проверки линии данных (шаблоны «история-…» для
   cdpm.data_checks: табельный номер есть в «Экипажах», даты читаются и
   последняя смена не позже среза, числа неотрицательны, каждый
   сотрудник «Экипажей» либо имеет строку истории, либо честно
   отсутствует — это уходит в coverage раздела);
3. сборку раздела пакета `manifest.history` из очищенных данных с
   провенансом по сотруднику.

Принцип: неизвестное не превращается в отсутствие ограничения — пустая
ячейка даёт `null`, сотрудник без строки — `null` во всех полях и
не входит в `with_history`; признак `assume_zero_when_missing` у
производителя всегда false.

Ревизия 1.18 (план единой версии 06.09.2026, §3.8):

- объявленный горизонт `manifest.horizon` (1.18.1) — из ключей
  planSince/planUntil листа «Свойства»: end_exclusive = end_inclusive +
  1 минута считается здесь один раз; без обоих ключей раздела нет;
  встроенная проверка «горизонт-объявлен» (класс «пропуск данных»)
  показывает отсутствие и нечитаемость в отчёте данных;
- свидетельства присутствия сотрудника (1.18.2) — лист «Присутствие»
  («Табельный номер», «Начало», «Конец», «Станция», «Вид»): у записи
  сотрудника появляются `location_evidence[]` (строки как есть с
  провенансом), `location_intervals[]` (объединение по паре «станция +
  вид», конфликтные области вырезаны) и `conflicts[]` (перекрытие
  разных станций). Сотрудник без строк листа — КЛЮЧЕЙ НЕТ (не пустые
  списки): читатель отличает «неизвестно» от «пусто». Из среза, снимка
  `location`, решения continue_zero и базы приписки интервалы не
  выводятся; в `with_history` присутствие не входит.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from cdpm import history_journal as hj
from cdpm.data_checks import DataChecksError, parse_moment, parse_num

HISTORY_SHEETS = ("история", "history", "остатки на начало",
                  "остатки на начало периода", "остатки")
JOURNAL_SHEETS = hj.JOURNAL_SHEETS
#: Лист «Календарь истории» (1.17.2): посуточный календарь до среза для
#: листа интегратора — строки «Табельный номер», «Дата», «Вид», «Ночная»;
#: отсутствие строки на дату = unknown (не выходной).
CALENDAR_SHEETS = ("календарь истории", "календарь", "history_calendar", "calendar")
_CALENDAR_KIND_RU = {"работа": "work", "смена": "work", "резерв": "reserve",
                     "выходной": "day_off", "отпуск": "leave", "болезнь": "sick",
                     "больничный": "sick", "work": "work", "reserve": "reserve",
                     "standby": "reserve", "day_off": "day_off", "leave": "leave",
                     "sick": "sick", "unknown": "unknown", "неизвестно": "unknown"}
#: Вопросы анкеты об истории (1.17.2), часть 3б: ОДИН ПРЕФИКС на все
#: издания. С каталога 0.5.0 вопрос задаётся ПО ВИДУ ЧТЕНИЯ истории —
#: один блок анкеты на вид, встретившийся среди семей ВМП, и хвост
#: идентификатора — слово словаря `history_read_kinds`. Книги ответов
#: наборов ловят вопрос по этому префиксу.
HISTORY_QUESTION_PREFIX = "Q-3b-история-"
#: Идентификаторы ПРЕЖНЕЙ формы: вопрос назывался полнотой данных
#: (частично / нет), а не видом чтения. Своды и книги ответов, записанные
#: до каталога 0.5.0, их несут — они остаются допустимыми и читаются
#: наравне с блоками по видам.
Q_HISTORY_PARTIAL = "Q-3b-история-частично"
Q_HISTORY_NONE = "Q-3b-история-нет"
#: Полнота данных, которую называл сам идентификатор прежней формы.
LEGACY_HISTORY_COVERAGE = {Q_HISTORY_PARTIAL: "partial",
                           Q_HISTORY_NONE: "none"}
HISTORY_QUESTION_IDS = tuple(LEGACY_HISTORY_COVERAGE)
#: Хвост блока для строк, зависящих от истории по СВОЕМУ блоку логики
#: (предмет rest / day_off / rolling_window / month, предикат
#: consecutive), а не по семье с чтением истории: вида чтения каталог им
#: не даёт, а молча терять их вопрос нельзя — это был бы пакет с
#: историей в проверках и без вопроса о ней.
HISTORY_KIND_BY_LOGIC = "логика"
#: Полнота данных истории, при которой вопрос задан (`coverage` блока
#: `history` вопроса): ответ, данный при другой полноте, решением не
#: является — его снимает линия данных.
HISTORY_COVERAGE_KINDS = ("partial", "none")


def history_question_id(read_kind: str) -> str:
    """Идентификатор блока анкеты об истории по виду чтения."""
    return f"{HISTORY_QUESTION_PREFIX}{read_kind}"


def is_history_question(qid) -> bool:
    """Вопрос об истории сотрудников — узнаётся ПО ПРЕФИКСУ: под него
    подходят и блоки по видам чтения, и вопросы прежней формы."""
    return str(qid or "").startswith(HISTORY_QUESTION_PREFIX)


def history_read_kinds() -> tuple:
    """Виды чтения истории — закрытый словарь каталога
    `history_read_kinds`. Каталог словаря не знает (издание до 0.5.0) —
    отказ словами: перечня видов в коде CDPM нет."""
    from cdpm import catalog_bridge as bridge
    return bridge.vocab("history_read_kinds")


def history_reads() -> dict:
    """Семья → {"kind": вид чтения, "need": потребность словами} по полю
    `reads_history` ЗАПИСИ КАТАЛОГА: что семья читает из истории до
    горизонта и каким видом чтения. Носитель один — ни перечня семей, ни
    текстов потребности в коде CDPM нет.

    Запись, назвавшая чтение наполовину (вид вне словаря либо пустая
    потребность), — отказ словами: анкета спрашивает данные ровно этими
    словами, и выдумывать их неоткуда."""
    from cdpm import catalog_bridge as bridge
    kinds = set(history_read_kinds())
    out: dict = {}
    for fid, traits in bridge.family_traits().items():
        reads = traits.get("reads_history") if isinstance(traits, dict) else None
        if reads is None:
            continue
        if not isinstance(reads, dict):
            kind, need = None, ""
        else:
            kind = reads.get("kind")
            need = " ".join(str(reads.get("need") or "").split())
        if kind not in kinds or not need:
            raise bridge.CatalogRequired(
                f"запись семьи {str(fid)!r} называет чтение истории "
                f"неполно: нужен вид из словаря history_read_kinds и "
                f"потребность непустым текстом")
        out[str(fid)] = {"kind": str(kind), "need": need}
    return out


def history_families() -> frozenset:
    """Семьи, читающие историю: записи каталога с заданным
    `reads_history` (каталог 0.5.0). Явный перечень границы горизонта и
    вывод по природе записи сняты — носитель один. Каталога нет —
    отказ словами от моста: молчание тут означало бы пакет с историей в
    проверках и без вопроса о ней."""
    return frozenset(history_reads())


_LOGIC_HISTORY_SUBJECTS = ("rest", "day_off", "rolling_window", "month")
#: Параметры окна счётчиков в сутках (count) — по семьям базы шаблонов:
#: days_off_floor → days_off_window_days (с min_days_off_in_window),
#: night_series → night_window_days (с max_night_duties_in_window).
#: Общего имени быть не может: параметр принадлежит ровно одной семье
#: (PARAM_TO_FAMILY реестра; согласовано с COSMOS 05.09.2026).
WINDOW_DAY_PARAMS = ("days_off_window_days", "night_window_days")
APPROVED_SHEETS = ("утверждённые назначения", "утвержденные назначения",
                   "approved_assignments", "approved")
EMPLOYEE_SHEETS = ("экипажи", "employees", "crew")
PROPERTY_SHEETS = ("свойства", "properties")
FLIGHT_SHEETS = ("рейсы", "flights")
AIRPORT_SHEETS = ("аэропорты", "airports", "stations")
#: Ключи листа «Свойства», объявляющие горизонт (1.18.1); регистр ключа не
#: важен, в манифест идут канонические имена.
HORIZON_KEYS = ("planSince", "planUntil")
#: Лист «Присутствие» (1.18.2): свидетельства местоположения сотрудника.
PRESENCE_SHEETS = ("присутствие", "presence", "location_evidence")
KIND_HISTORY_FACT = "history_fact"
KIND_DECLARED = "declared_presence"
#: Значения колонки «Вид» листа «Присутствие» → kind записи.
PRESENCE_KINDS = {"факт": KIND_HISTORY_FACT, "fact": KIND_HISTORY_FACT,
                  KIND_HISTORY_FACT: KIND_HISTORY_FACT,
                  "объявлено": KIND_DECLARED, "declared": KIND_DECLARED,
                  KIND_DECLARED: KIND_DECLARED}
_PRESENCE_START = ("начало", "start")
_PRESENCE_END = ("конец", "end", "окончание")
_PRESENCE_STATION = ("станция", "station")
_PRESENCE_KIND = ("вид", "kind", "тип", "type")
#: Ключи записи сотрудника, которые несёт присутствие (не история накопителей).
PRESENCE_RECORD_KEYS = ("location_evidence", "location_intervals", "conflicts")
#: Колонки станций листа рейсов / кода листа аэропортов: точное имя, затем
#: подстрока (дисциплина export_meridian / map_projection).
_DEP_EXACT = ("вылет", "откуда", "departure", "dep")
_DEP_MARKS = ("вылет", "откуда", "departure")
_ARR_EXACT = ("прилёт", "прилет", "куда", "arrival", "arr")
_ARR_MARKS = ("прилёт", "прилет", "куда", "arrival")
_CODE_EXACT = ("код", "code", "iata", "аэропорт", "airport", "станция", "station")
FLIGHT_NUMBER_COLUMNS = ("номер рейса", "рейс", "flight", "flight number")
FLIGHT_DATE_COLUMNS = ("дата", "date", "дата вылета")
#: Канон колонок листа «Утверждённые назначения» (ревизия 1.17.1): заголовок
#: в нижнем регистре → канонический ключ записи approved_assignments.
#: Потребитель (COSMOS) читает ровно «Рейс» и «Дата»; прочие колонки
#: переносятся дословно.
APPROVED_COLUMN_CANON = {"рейс": "Рейс", "номер рейса": "Рейс", "flight": "Рейс",
                         "дата": "Дата", "date": "Дата", "дата вылета": "Дата"}


def approved_key(col) -> str:
    """Канонический ключ записи закрепления для заголовка колонки."""
    return APPROVED_COLUMN_CANON.get(str(col).strip().lower(), str(col))

# Колонка листа (в нижнем регистре) → путь поля раздела.
COLUMNS: dict[str, tuple[str, ...]] = {
    "табельный номер": ("employee_id",),
    "employee_id": ("employee_id",),
    "срез": ("as_of",),
    "as_of": ("as_of",),
    "налёт с начала месяца, мин": ("month_to_date", "block_min"),
    "налет с начала месяца, мин": ("month_to_date", "block_min"),
    "рабочее время с начала месяца, мин": ("month_to_date", "work_min"),
    "лётное время с начала месяца, мин": ("month_to_date", "flight_time_min"),
    "летное время с начала месяца, мин": ("month_to_date", "flight_time_min"),
    "дней отработано": ("month_to_date", "days_worked"),
    "посадок": ("month_to_date", "landings"),
    "последняя смена: конец": ("last_duty", "end"),
    "последняя смена: станция": ("last_duty", "end_station"),
    "последняя смена: работа, мин": ("last_duty", "work_min"),
    "последняя смена: размах, мин": ("last_duty", "span_min"),
    "последняя смена: ночная": ("last_duty", "night"),
    "серия рабочих дней": ("series", "consecutive_duty_days"),
    "серия ночных смен": ("series", "consecutive_night_duties"),
    "дней с выходного": ("series", "days_since_day_off"),
    "местоположение": ("location",),
    "location": ("location",),
    # Синонимы заголовков листа «История» набора сертификатора COSMOS
    # (vmp/run/roster_certify.py): единая версия 06.09.2026, план §3.8 —
    # если компилятор сертификатора выдаёт формы истории, книга набора
    # читается тем же каноном, иначе окна истории были бы «неизвестно».
    # «Отдых до горизонта, мин» и «Есть история» — производные величины,
    # полей раздела у них нет: они не читаются и не считаются дефектом.
    "полётное время с начала месяца, мин": ("month_to_date", "flight_time_min"),
    "полетное время с начала месяца, мин": ("month_to_date", "flight_time_min"),
    "рабочих дней с начала месяца": ("month_to_date", "days_worked"),
    "посадок с начала месяца": ("month_to_date", "landings"),
    "конец последней смены": ("last_duty", "end"),
    "место последней смены": ("last_duty", "end_station"),
    "последняя смена ночная": ("last_duty", "night"),
    "рабочих дней подряд": ("series", "consecutive_duty_days"),
    "ночных смен подряд": ("series", "consecutive_night_duties"),
    "дней с последнего выходного": ("series", "days_since_day_off"),
}
_ROLLING_RE = re.compile(
    r"^окно\s*(\d+)\s*(?:дн|д)\.?\s*[:—-]?\s*"
    r"(работа|налёт|налет|лётное|летное|рабочих дней|ночных смен|выходных)"
    r"(?:\s*,\s*мин)?\s*$")
# Минуты окна (1.17) и счётчики окна (1.17.2: рабочих дней, ночных смен,
# дней без работы) — один ключ rolling.last_{N}d_{мера}.
_ROLLING_MEASURE = {"работа": "work_min", "налёт": "block_min",
                    "налет": "block_min", "лётное": "flight_time_min",
                    "летное": "flight_time_min",
                    "рабочих дней": "duty_days", "ночных смен": "night_duties",
                    "выходных": "days_off"}
_INT_FIELDS = {("month_to_date", "block_min"), ("month_to_date", "work_min"),
               ("month_to_date", "flight_time_min"),
               ("month_to_date", "days_worked"), ("month_to_date", "landings"),
               ("last_duty", "work_min"), ("last_duty", "span_min"),
               ("series", "consecutive_duty_days"),
               ("series", "consecutive_night_duties"),
               ("series", "days_since_day_off")}
_MOMENT_FIELDS = {("last_duty", "end"), ("as_of",)}
_BOOL_FIELDS = {("last_duty", "night")}
_YES = ("да", "yes", "true", "1", "ночная")
_NO = ("нет", "no", "false", "0", "дневная")
EMPLOYEE_ID_COLUMNS = ("табельный номер", "employee_id", "табельный")


def find_sheet(tables: dict, aliases: tuple[str, ...]):
    """(sid, имя листа, таблица) первого листа с именем из перечня."""
    for sid, sheets in sorted((tables or {}).items()):
        if not isinstance(sheets, dict):
            continue
        for name, table in sheets.items():
            if str(name).strip().lower() in aliases and isinstance(table, dict):
                return sid, name, table
    return None


def column_map(columns: list) -> tuple[dict, dict, list]:
    """(колонка → путь поля, колонка → окно rolling, неопознанные)."""
    fields: dict[str, tuple[str, ...]] = {}
    rolling: dict[str, str] = {}
    unknown: list[str] = []
    for col in columns or []:
        low = str(col).strip().lower()
        if low in COLUMNS:
            fields[col] = COLUMNS[low]
            continue
        m = _ROLLING_RE.match(low)
        if m:
            rolling[col] = f"last_{int(m.group(1))}d_{_ROLLING_MEASURE[m.group(2)]}"
            continue
        unknown.append(col)
    return fields, rolling, unknown


def _cell(row: dict, col: str) -> str:
    v = row.get(col)
    return "" if v is None else str(v).strip()


def parse_row(row: dict, fields: dict, rolling: dict) -> tuple[dict, list[str]]:
    """Строка листа → запись сотрудника; пустая ячейка = null (неизвестно).
    Возврат: (запись, проблемы разбора словами с именем колонки)."""
    rec: dict = {"employee_id": None, "as_of": None,
                 "month_to_date": {}, "rolling": {}, "last_duty": {},
                 "series": {}, "location": None}
    problems: list[str] = []
    for col, path in fields.items():
        raw = _cell(row, col)
        if raw == "":
            _set(rec, path, None)
            continue
        if path in _INT_FIELDS:
            n = parse_num(raw)
            if n is None or n < 0 or int(n) != n:
                problems.append(f"«{col}»: ожидалось целое неотрицательное "
                                f"число минут/дней, получено «{raw}»")
                _set(rec, path, None)
            else:
                _set(rec, path, int(n))
        elif path in _MOMENT_FIELDS:
            m = parse_moment(raw)
            if m is None:
                problems.append(f"«{col}»: дата-время не читается «{raw}» "
                                f"(формы дд.мм.гггг чч:мм | гггг-мм-дд чч:мм)")
                _set(rec, path, None)
            else:
                _set(rec, path, m.strftime("%Y-%m-%dT%H:%M"))
        elif path in _BOOL_FIELDS:
            low = raw.lower()
            if low in _YES:
                _set(rec, path, True)
            elif low in _NO:
                _set(rec, path, False)
            else:
                problems.append(f"«{col}»: ожидалось «да»/«нет», получено «{raw}»")
                _set(rec, path, None)
        else:
            _set(rec, path, raw)
    for col, key in rolling.items():
        raw = _cell(row, col)
        if raw == "":
            rec["rolling"][key] = None
            continue
        n = parse_num(raw)
        if n is None or n < 0 or int(n) != n:
            problems.append(f"«{col}»: ожидалось целое неотрицательное число "
                            f"(минут или дней/смен окна), получено «{raw}»")
            rec["rolling"][key] = None
        else:
            rec["rolling"][key] = int(n)
    return rec, problems


def _set(rec: dict, path: tuple[str, ...], value) -> None:
    if len(path) == 1:
        rec[path[0]] = value
    else:
        rec.setdefault(path[0], {})[path[1]] = value


def has_any_value(rec: dict) -> bool:
    """Есть ли у записи хоть одно известное поле истории: табельный,
    срез (подставляется из свойств), провенанс, перечень утверждённых
    назначений и свидетельства присутствия (1.18.2 — не история
    накопителей, в with_history не входят) сами по себе историей не
    считаются."""
    for k, v in rec.items():
        if k in ("employee_id", "as_of", "provenance", "approved_assignments") \
                or k in PRESENCE_RECORD_KEYS:
            continue
        if isinstance(v, dict):
            if any(x is not None for x in v.values()):
                return True
        elif v is not None:
            return True
    return False


def property_pairs(table: dict) -> list[tuple[str, str, int]]:
    """Строки листа «Свойства» как (ключ, значение, номер строки листа):
    первая колонка — ключ, вторая — значение (заголовки у книг разные:
    «ключ / значение», «Свойство / Значение»); строки нумеруются как в
    книге (заголовок — 1)."""
    cols = table.get("columns") or []
    out: list[tuple[str, str, int]] = []
    for i, row in enumerate(table.get("rows") or [], 2):
        if not isinstance(row, dict):
            continue
        vals = [row.get(c) for c in cols] if len(cols) >= 2 else list(row.values())
        vals = ["" if v is None else str(v).strip() for v in vals]
        if len(vals) >= 2 and vals[0]:
            out.append((vals[0], vals[1], i))
    return out


def plan_since(tables: dict) -> str | None:
    """planSince из листа «Свойства» (канон дат 1.15) — срез по умолчанию."""
    found = find_sheet(tables, PROPERTY_SHEETS)
    if not found:
        return None
    _sid, _name, table = found
    for key, value, _row in property_pairs(table):
        if key.lower() in ("plansince", "начало горизонта", "срез"):
            m = parse_moment(value)
            return m.strftime("%Y-%m-%dT%H:%M") if m else None
    return None


# ── объявленный горизонт (1.18.1) ──

def horizon_source(tables: dict) -> dict | None:
    """Ключи planSince/planUntil листа «Свойства» как записаны:
    {sid, sheet, values: {ключ: значение}, rows: {ключ: строка листа}};
    отсутствующий ключ в словарях не значится. None — листа «Свойства»
    нет. Регистр ключа не важен, первая запись ключа — действующая."""
    found = find_sheet(tables, PROPERTY_SHEETS)
    if not found:
        return None
    sid, name, table = found
    wanted = {k.lower(): k for k in HORIZON_KEYS}
    values: dict[str, str] = {}
    rows: dict[str, int] = {}
    for key, value, i in property_pairs(table):
        canon = wanted.get(key.lower())
        if canon and canon not in values:
            values[canon] = value
            rows[canon] = i
    return {"sid": sid, "sheet": name, "values": values, "rows": rows}


def iso_minute(raw: str) -> datetime | None:
    """Момент канона дат для горизонта — ровно так, как его читает
    потребитель (datetime.fromisoformat): наивный, точностью до минуты.
    Пояс в значении или секунды — не канон (система времени пакета
    объявлена манифестом 1.16, шкала горизонта — минута) → None."""
    try:
        m = datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if m.tzinfo is not None or m.second or m.microsecond:
        return None
    return m


def declared_horizon(data: dict | None, tz: str | None = None) -> dict | None:
    """manifest.horizon (1.18.1) из книги данных (dataset / clean-data с
    ключом tables): {start, end_inclusive, end_exclusive, scale, tz,
    source}. end_inclusive — минута planUntil как есть; end_exclusive =
    end_inclusive + 1 минута — считается ЗДЕСЬ один раз, читатели окон
    берут [start, end_exclusive) и минуту не прибавляют. tz — пояс базы из
    объявления системы времени пакета (1.16); нет объявления — null.
    None — нет обоих ключей, ключ нечитаем либо start ≥ end_inclusive:
    манифест не угадывает, дефект называет проверка «горизонт-объявлен».
    Гейт пакета зовёт ту же функцию с теми же файлами пакета."""
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, dict):
        return None
    src = horizon_source(tables)
    if not src or any(k not in src["values"] for k in HORIZON_KEYS):
        return None
    start = iso_minute(src["values"]["planSince"])
    end = iso_minute(src["values"]["planUntil"])
    if start is None or end is None or not start < end:
        return None
    return {"start": start.strftime(hj.ISO),
            "end_inclusive": end.strftime(hj.ISO),
            "end_exclusive": (end + timedelta(minutes=1)).strftime(hj.ISO),
            "scale": "minute",
            "tz": tz,
            "source": {"sheet": src["sheet"], "keys": list(HORIZON_KEYS),
                       "sid": src["sid"]}}


def horizon_check_specs(dataset: dict) -> list[dict]:
    """Встроенные проверки объявленного горизонта (1.18.1, класс «пропуск
    данных»): «горизонт-объявлен» — оба ключа есть, читаются каноном,
    start < end_inclusive (нет ключей → находка «горизонт не объявлен»,
    КРИТИЧЕСКОЕ, адрес — заголовок листа «Свойства» либо первого листа
    книги); «горизонт-сутки» — показание (РЕДАКЦИОННОЕ, класс «аномалия»)
    о границах не на полных сутках, судится только у читаемого горизонта."""
    tables = dataset.get("tables") or {}
    src = horizon_source(tables)
    if src is None:
        first = next(((sid, name) for sid, sheets in sorted(tables.items())
                      if isinstance(sheets, dict) for name in sheets), None)
        if first is None:
            return []
        sid, name = first
    else:
        sid, name = src["sid"], src["sheet"]
    ref = f"{sid}/{name}"
    specs = [
        {"id": f"C-горизонт-объявлен-{sid}", "basis": {"builtin": "пропуск данных"},
         "template": "горизонт-объявлен", "params": {"sheet": ref},
         "statement": "Горизонт планирования объявлен: лист «Свойства» несёт "
                      "ключи planSince и planUntil, оба читаются каноном дат "
                      "(ГГГГ-ММ-ДДTчч:мм), начало раньше конца",
         "unit": "объявление", "severity": "КРИТИЧЕСКОЕ",
         "status": "скомпилирована"},
    ]
    if declared_horizon(dataset) is not None:
        specs.append(
            {"id": f"C-горизонт-сутки-{sid}", "basis": {"builtin": "аномалия"},
             "template": "горизонт-сутки", "params": {"sheet": ref},
             "statement": "Границы горизонта лежат на полных сутках "
                          "(planSince в 00:00, planUntil в 23:59) — показание, "
                          "не запрет",
             "unit": "граница", "severity": "РЕДАКЦИОННОЕ",
             "status": "скомпилирована"})
    return specs


def _run_horizon_check(template: str, params: dict, dataset: dict) -> tuple[int, list[dict]]:
    tables = dataset.get("tables") or {}
    sid, name = str(params["sheet"]).split("/", 1)
    src = horizon_source(tables)
    out: list[dict] = []

    def tgt(row: int) -> dict:
        return {"sid": sid, "sheet": name, "row": row}

    if template == "горизонт-объявлен":
        # Одно объявление рассмотрено всегда (иначе «прогнана» невозможна,
        # а пользователь обязан видеть «горизонт не объявлен» в отчёте).
        if src is None:
            out.append({"target": tgt(1),
                        "text": "горизонт не объявлен: в книге нет листа «Свойства» "
                                "с ключами planSince и planUntil",
                        "evidence": f"{sid}!{name}: листа «Свойства» нет"})
            return 1, out
        vals, rows = src["values"], src["rows"]
        missing = [k for k in HORIZON_KEYS if k not in vals]
        if missing:
            have = ", ".join(f"{k}={vals[k]!r}" for k in HORIZON_KEYS if k in vals) or "нет ни одного"
            out.append({"target": tgt(1),
                        "text": "горизонт не объявлен: в листе «%s» нет ключей %s"
                                % (name, ", ".join(missing)),
                        "evidence": f"{sid}!{name}: ключи горизонта — {have}"})
            return 1, out
        parsed = {}
        for k in HORIZON_KEYS:
            m = iso_minute(vals[k])
            parsed[k] = m
            if m is None:
                out.append({"target": tgt(rows[k]),
                            "text": f"«{k}» не читается каноном дат "
                                    f"(ГГГГ-ММ-ДДTчч:мм, без пояса и секунд): «{vals[k]}»",
                            "evidence": f"{sid}!{name} строка {rows[k]}: {k} = «{vals[k]}»"})
        if all(parsed.values()) and not parsed["planSince"] < parsed["planUntil"]:
            out.append({"target": tgt(rows["planUntil"]),
                        "text": f"начало горизонта {vals['planSince']} не раньше "
                                f"конца {vals['planUntil']}",
                        "evidence": f"{sid}!{name} строки {rows['planSince']} и "
                                    f"{rows['planUntil']}: planSince = «{vals['planSince']}», "
                                    f"planUntil = «{vals['planUntil']}»"})
        return 1, out
    if template == "горизонт-сутки":
        h = declared_horizon(dataset)
        if h is None or src is None:
            raise DataChecksError("горизонт не объявлен или нечитаем — границы "
                                  "судить не по чему")
        bounds = (("planSince", datetime.strptime(h["start"], hj.ISO), "начало"),
                  ("planUntil", datetime.strptime(h["end_exclusive"], hj.ISO), "конец"))
        for key, moment, word in bounds:
            if moment.hour or moment.minute:
                out.append({"target": tgt(src["rows"][key]),
                            "text": f"{word} горизонта не на полных сутках: {key} = "
                                    f"«{src['values'][key]}» (показание, не запрет)",
                            "evidence": f"{sid}!{name} строка {src['rows'][key]}: "
                                        f"{key} = «{src['values'][key]}»"})
        return 2, out
    raise ValueError(f"неизвестный шаблон горизонта {template!r}")


def employee_rows(tables: dict) -> list[tuple[str, dict]]:
    """[(табельный, адрес строки листа сотрудников {sid, sheet, row})]
    в порядке листа; строки нумеруются как в книге (заголовок — 1)."""
    found = find_sheet(tables, EMPLOYEE_SHEETS)
    if not found:
        return []
    sid, name, table = found
    col = next((c for c in table.get("columns") or []
                if str(c).strip().lower() in EMPLOYEE_ID_COLUMNS), None)
    if col is None:
        return []
    return [(_cell(r, col), {"sid": sid, "sheet": name, "row": i})
            for i, r in enumerate(table.get("rows") or [], 2) if _cell(r, col)]


def employee_ids(tables: dict) -> list[str]:
    return [eid for eid, _addr in employee_rows(tables)]


# ── свидетельства присутствия (1.18.2): лист «Присутствие» ──

def _pick_col(columns: list, exact: tuple[str, ...],
              marks: tuple[str, ...] = ()) -> str | None:
    """Колонка по точному имени (без регистра), затем по подстроке."""
    cols = [(c, str(c).strip().lower()) for c in columns or []]
    for c, low in cols:
        if low in exact:
            return c
    for c, low in cols:
        if any(m in low for m in marks):
            return c
    return None


def presence_columns(table: dict) -> dict[str, str | None]:
    """Колонки листа «Присутствие» по канону: табельный, начало, конец,
    станция, вид (None — колонки нет)."""
    cols = table.get("columns") or []
    return {"id": _pick_col(cols, EMPLOYEE_ID_COLUMNS),
            "start": _pick_col(cols, _PRESENCE_START),
            "end": _pick_col(cols, _PRESENCE_END),
            "station": _pick_col(cols, _PRESENCE_STATION),
            "kind": _pick_col(cols, _PRESENCE_KIND)}


def parse_presence_row(row: dict, cols: dict) -> tuple[dict, list[str]]:
    """Строка листа «Присутствие» → {employee_id, start, end, station,
    kind}; нечитаемое поле — None и проблема словами с именем колонки.
    Код станции — в верхнем регистре (один регистр везде: сеть, база,
    интервалы); kind — из перечня PRESENCE_KINDS."""
    problems: list[str] = []
    rec: dict = {"employee_id": _cell(row, cols["id"]) if cols["id"] else "",
                 "start": None, "end": None, "station": None, "kind": None}
    for key, word in (("start", "Начало"), ("end", "Конец")):
        col = cols[key]
        raw = _cell(row, col) if col else ""
        if not col or raw == "":
            problems.append(f"«{col or word}»: пусто")
            continue
        m = parse_moment(raw)
        if m is None:
            problems.append(f"«{col}»: дата-время не читается «{raw}» "
                            f"(формы дд.мм.гггг чч:мм | гггг-мм-дд чч:мм)")
        else:
            rec[key] = m.strftime(hj.ISO)
    if rec["start"] and rec["end"] and not rec["start"] < rec["end"]:
        problems.append(f"«{cols['start']}»: начало {rec['start']} не раньше "
                        f"конца {rec['end']}")
    station = _cell(row, cols["station"]) if cols["station"] else ""
    if station:
        rec["station"] = station.upper()
    else:
        problems.append(f"«{cols['station'] or 'Станция'}»: пусто")
    kind_raw = _cell(row, cols["kind"]) if cols["kind"] else ""
    kind = PRESENCE_KINDS.get(kind_raw.lower())
    if kind is None:
        problems.append(f"«{cols['kind'] or 'Вид'}»: ожидалось «факт» или "
                        f"«объявлено» (fact / declared), получено «{kind_raw}»")
    rec["kind"] = kind
    return rec, problems


def presence_rows(tables: dict) -> tuple[tuple | None, list[dict]]:
    """(адрес листа «Присутствие» (sid, имя, таблица) | None, записи):
    каждая запись — разбор строки + provenance {sid, sheet, row} +
    problems (пусто = читаема целиком)."""
    found = find_sheet(tables, PRESENCE_SHEETS)
    if not found:
        return None, []
    sid, name, table = found
    cols = presence_columns(table)
    out: list[dict] = []
    for i, row in enumerate(table.get("rows") or [], 2):
        if not isinstance(row, dict):
            continue
        rec, problems = parse_presence_row(row, cols)
        rec["provenance"] = {"sid": sid, "sheet": name, "row": i}
        rec["problems"] = problems
        out.append(rec)
    return found, out


def network_stations(tables: dict) -> set[str] | None:
    """Станции сети: вылет/прилёт листа «Рейсы» плюс коды листа
    «Аэропорты» (верхний регистр). None — сеть не определить: нет листа
    рейсов с колонками вылета и прилёта."""
    flights = find_sheet(tables, FLIGHT_SHEETS)
    if not flights:
        return None
    _sid, _name, table = flights
    cols = table.get("columns") or []
    dep = _pick_col(cols, _DEP_EXACT, _DEP_MARKS)
    arr = _pick_col(cols, _ARR_EXACT, _ARR_MARKS)
    if dep is None or arr is None:
        return None
    net: set[str] = set()
    for row in table.get("rows") or []:
        for col in (dep, arr):
            code = _cell(row, col).upper()
            if code:
                net.add(code)
    airports = find_sheet(tables, AIRPORT_SHEETS)
    if airports:
        _asid, _aname, atable = airports
        code_col = _pick_col(atable.get("columns") or [], _CODE_EXACT)
        if code_col is not None:
            for row in atable.get("rows") or []:
                code = _cell(row, code_col).upper()
                if code:
                    net.add(code)
    return net


def _provenance_key(p: dict) -> tuple:
    return (str(p.get("sid")), str(p.get("sheet")), int(p.get("row") or 0))


def _sorted_provenances(items) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for p in items:
        seen.setdefault(_provenance_key(p), p)
    return [seen[k] for k in sorted(seen)]


def location_intervals_of(evidence: list[dict]) -> tuple[list[dict], list[dict]]:
    """Канон 1.18.2 из читаемых свидетельств {start, end, station, kind,
    provenance} (ISO-минуты, start < end): (location_intervals, conflicts).

    1. Объединение (union) дублей и перекрытий по паре «станция + вид» в
       неперекрывающиеся интервалы; смежные сливаются; sources — провенанс
       ВСЕХ исходных строк (ночь не удваивается).
    2. Развёртка по элементарным отрезкам между всеми границами: отрезок
       с РАЗНЫМИ станциями — конфликт {start, end, stations, rows}
       (местоположение unknown, интервала там нет, «последняя строка» не
       выбирается); отрезок одной станции с наложением видов — сегмент
       вида «факт» там, где факт есть (объявленное до факта не
       повышается), иначе «объявлено»; sources сегмента — строки его вида.
    3. Смежные сегменты одной станции, вида и источников склеиваются."""
    def dt(s: str) -> datetime:
        return datetime.strptime(s, hj.ISO)

    groups: dict[tuple, list[dict]] = {}
    for e in evidence:
        groups.setdefault((e["station"], e["kind"]), []).append(e)
    merged: list[dict] = []
    for (station, kind), items in sorted(groups.items()):
        cur = None
        for e in sorted(items, key=lambda x: (x["start"], x["end"])):
            s, t = dt(e["start"]), dt(e["end"])
            if cur is not None and s <= cur["end"]:
                cur["end"] = max(cur["end"], t)
                cur["sources"].append(e["provenance"])
            else:
                cur = {"station": station, "kind": kind, "start": s, "end": t,
                       "sources": [e["provenance"]]}
                merged.append(cur)
    points = sorted({x for m in merged for x in (m["start"], m["end"])})
    segments: list[dict] = []
    conflicts: list[dict] = []
    for p, q in zip(points, points[1:]):
        active = [m for m in merged if m["start"] <= p and m["end"] >= q]
        if not active:
            continue
        stations = sorted({m["station"] for m in active})
        if len(stations) > 1:
            conflicts.append({"start": p, "end": q, "stations": stations,
                              "rows": _sorted_provenances(
                                  src for m in active for src in m["sources"])})
            continue
        kind = KIND_HISTORY_FACT if any(m["kind"] == KIND_HISTORY_FACT for m in active) \
            else KIND_DECLARED
        segments.append({"start": p, "end": q, "station": stations[0], "kind": kind,
                         "sources": _sorted_provenances(
                             src for m in active if m["kind"] == kind
                             for src in m["sources"])})

    def coalesce(items: list[dict], same) -> list[dict]:
        out: list[dict] = []
        for it in items:
            if out and out[-1]["end"] == it["start"] and same(out[-1], it):
                out[-1]["end"] = it["end"]
            else:
                out.append(dict(it))
        return out

    segments = coalesce(segments, lambda a, b: (a["station"], a["kind"], a["sources"])
                        == (b["station"], b["kind"], b["sources"]))
    conflicts = coalesce(conflicts, lambda a, b: (a["stations"], a["rows"])
                         == (b["stations"], b["rows"]))
    intervals = [{"start": s["start"].strftime(hj.ISO), "end": s["end"].strftime(hj.ISO),
                  "station": s["station"], "kind": s["kind"], "sources": s["sources"]}
                 for s in segments]
    conflicts_out = [{"start": c["start"].strftime(hj.ISO), "end": c["end"].strftime(hj.ISO),
                      "stations": c["stations"], "rows": c["rows"]} for c in conflicts]
    return intervals, conflicts_out


def presence_by_employee(tables: dict) -> dict[str, list[dict]]:
    """Читаемые свидетельства присутствия по табельному: записи
    {start, end, station, kind, provenance} в порядке листа. Нечитаемые
    строки пропускаются (их вычитает линия данных); строка вида «факт» с
    концом позже среза остаётся фактом — дефект называет проверка, а не
    молчаливая перетипизация."""
    _found, rows = presence_rows(tables)
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["problems"] or not r["employee_id"]:
            continue
        out.setdefault(r["employee_id"], []).append(
            {"start": r["start"], "end": r["end"], "station": r["station"],
             "kind": r["kind"], "provenance": r["provenance"]})
    return out


# ── проверки линии данных (шаблоны «история-…», исполняются run_spec) ──

def check_specs(dataset: dict) -> list[dict]:
    """Спецификации встроенных проверок листов раздела истории: «История»
    (если лист есть), «Утверждённые назначения», «Журнал назначений»."""
    tables = dataset.get("tables") or {}
    found = find_sheet(tables, HISTORY_SHEETS)
    if not found:
        return hj.check_specs(dataset) + _approved_specs(tables) \
            + _presence_specs(tables)
    sid, name, table = found
    ref = f"{sid}/{name}"
    specs = [
        {"id": f"C-история-табельный-{sid}", "basis": {"builtin": "история"},
         "template": "история-табельный", "params": {"sheet": ref},
         "statement": f"Табельный номер каждой строки листа «{name}» ({sid}) "
                      f"есть в листе «Экипажи»",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ",
         "status": "скомпилирована"},
        {"id": f"C-история-значения-{sid}", "basis": {"builtin": "история"},
         "template": "история-значения", "params": {"sheet": ref},
         "statement": f"Значения листа «{name}» ({sid}) читаются: даты по "
                      f"канону, числа целые неотрицательные, «ночная» — да/нет",
         "unit": "ячейка", "severity": "БЛОКИРУЮЩЕЕ",
         "status": "скомпилирована"},
        {"id": f"C-история-срез-{sid}", "basis": {"builtin": "история"},
         "template": "история-срез", "params": {"sheet": ref},
         "statement": f"Конец последней смены на листе «{name}» ({sid}) не "
                      f"позже среза (начала горизонта)",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ",
         "status": "скомпилирована"},
        {"id": f"C-история-полнота-{sid}", "basis": {"builtin": "история"},
         "template": "история-полнота", "params": {"sheet": ref},
         "statement": f"У каждого сотрудника листа «Экипажи» есть строка "
                      f"истории на листе «{name}» ({sid}); отсутствие — "
                      f"сотрудник без истории (null), не ноль",
         "unit": "сотрудник", "severity": "СЕРЬЁЗНОЕ",
         "status": "скомпилирована"},
    ]

    return specs + _approved_specs(tables) + hj.check_specs(dataset) \
        + _presence_specs(tables)


def _presence_specs(tables: dict) -> list[dict]:
    """Встроенные проверки листа «Присутствие» (1.18.2, класс «история»):
    табельный в «Экипажах» (как у других листов истории), значения
    (даты, начало < конец, станция, вид из перечня) — строки с
    нечитаемым вычитаются; станция в сети, факт не позже среза, конфликт
    станций и противоречие концу последней смены — находки с адресом,
    строку не вычитают (СЕРЬЁЗНОЕ)."""
    found = find_sheet(tables, PRESENCE_SHEETS)
    if not found:
        return []
    psid, pname, _ptable = found
    ref = f"{psid}/{pname}"

    def spec(tail: str, template: str, statement: str, unit: str, severity: str) -> dict:
        return {"id": f"C-история-присутствие-{tail}-{psid}",
                "basis": {"builtin": "история"},
                "template": f"история-присутствие-{template}",
                "params": {"sheet": ref}, "statement": statement,
                "unit": unit, "severity": severity, "status": "скомпилирована"}

    return [
        spec("табельный", "табельный",
             f"Табельный номер каждой строки листа «{pname}» ({psid}) есть в "
             f"листе «Экипажи»", "строка", "БЛОКИРУЮЩЕЕ"),
        spec("значения", "значения",
             f"Значения листа «{pname}» ({psid}) читаются: «Начало» и «Конец» "
             f"по канону дат и начало раньше конца, «Станция» заполнена, "
             f"«Вид» — «факт» или «объявлено»", "ячейка", "БЛОКИРУЮЩЕЕ"),
        spec("станция", "станция",
             f"Станция каждой строки листа «{pname}» ({psid}) есть в сети "
             f"(вылеты и прилёты листа «Рейсы», коды листа «Аэропорты»)",
             "строка", "СЕРЬЁЗНОЕ"),
        spec("срез", "срез",
             f"Строка вида «факт» листа «{pname}» ({psid}) кончается не позже "
             f"среза (начала горизонта): свершившееся не лежит в горизонте, "
             f"объявленное условие пишется видом «объявлено»",
             "строка", "СЕРЬЁЗНОЕ"),
        spec("конфликт", "конфликт",
             f"Свидетельства листа «{pname}» ({psid}) не противоречат друг "
             f"другу и истории: разные станции в одно время — конфликт "
             f"(местоположение неизвестно), факт, накрывающий конец последней "
             f"смены другой станцией, — противоречие",
             "строка", "СЕРЬЁЗНОЕ"),
    ]


def _run_presence_check(template: str, params: dict, dataset: dict) -> tuple[int, list[dict]]:
    from unicode_data import confusable_hint
    tables = dataset.get("tables") or {}
    sid, name = str(params["sheet"]).split("/", 1)
    table = (tables.get(sid) or {}).get(name) or {}
    rows = table.get("rows") or []
    cols = presence_columns(table)
    out: list[dict] = []

    def tgt(i: int, col=None) -> dict:
        t = {"sid": sid, "sheet": name, "row": i}
        if col:
            t["column"] = col
        return t

    parsed = [(i, parse_presence_row(row, cols)) for i, row in enumerate(rows, 2)
              if isinstance(row, dict)]
    if template == "история-присутствие-табельный":
        # GPT-022 B1: пустой или отсутствующий реестр «Экипажи» — не
        # пропуск проверки, а её отрицательный исход для каждой строки:
        # присутствие без объявленного сотрудника в раздел не попадает.
        known = set(employee_ids(tables))
        for i, (rec, _problems) in parsed:
            eid = rec["employee_id"]
            if not eid:
                out.append({"target": tgt(i, cols["id"]), "text": "табельный номер пуст",
                            "evidence": f"{sid}!{name} строка {i}"})
            elif not known:
                out.append({"target": tgt(i, cols["id"]),
                            "text": f"табельный номер «{eid}»: лист «Экипажи» пуст или "
                                    f"отсутствует — сотрудник не объявлен, присутствие "
                                    f"отнести некому",
                            "evidence": f"{sid}!{name} строка {i}: «{eid}»; «Экипажи»: "
                                        f"записей нет"})
            elif eid not in known:
                out.append({"target": tgt(i, cols["id"]),
                            "text": f"табельный номер «{eid}» отсутствует в листе «Экипажи»"
                                    + confusable_hint(eid, known),
                            "evidence": f"{sid}!{name} строка {i}: «{eid}»"})
        return len(rows), out
    if template == "история-присутствие-значения":
        for i, (_rec, problems) in parsed:
            for p in problems:
                col = p.split("»", 1)[0].lstrip("«")
                out.append({"target": tgt(i, col), "text": p,
                            "evidence": f"{sid}!{name} строка {i}"})
        return len(rows), out
    if template == "история-присутствие-станция":
        net = network_stations(tables)
        if net is None:
            raise DataChecksError("сеть станций не определена: нет листа «Рейсы» "
                                  "с колонками вылета и прилёта — станции "
                                  "присутствия сверить не с чем")
        for i, (rec, _problems) in parsed:
            if rec["station"] and rec["station"] not in net:
                out.append({"target": tgt(i, cols["station"]),
                            "text": f"станция «{rec['station']}» вне сети "
                                    f"(нет среди вылетов/прилётов и кодов аэропортов)"
                                    + confusable_hint(rec["station"], net),
                            "evidence": f"{sid}!{name} строка {i}: «{rec['station']}»; "
                                        f"сеть: {', '.join(sorted(net)) or 'пуста'}"})
        return len(rows), out
    if template == "история-присутствие-срез":
        as_of = plan_since(tables)
        if not as_of:
            raise DataChecksError("срез (planSince листа «Свойства») не объявлен — "
                                  "факт присутствия сверить не с чем")
        for i, (rec, problems) in parsed:
            if rec["kind"] == KIND_HISTORY_FACT and rec["end"] and rec["end"] > as_of:
                out.append({"target": tgt(i, cols["end"]),
                            "text": f"факт присутствия кончается {rec['end']} — позже "
                                    f"среза {as_of}; свершившееся не лежит в "
                                    f"горизонте, объявленное условие пишется "
                                    f"видом «объявлено»",
                            "evidence": f"{sid}!{name} строка {i}: «Вид» = факт, "
                                        f"«Конец» = {rec['end']}, срез {as_of}"})
        return len(rows), out
    if template == "история-присутствие-конфликт":
        section = history_section(dataset)
        for emp in (section or {}).get("employees") or []:
            eid = emp.get("employee_id")
            for c in emp.get("conflicts") or []:
                first = c["rows"][0]
                out.append({"target": {"sid": first["sid"], "sheet": first["sheet"],
                                       "row": first["row"]},
                            "text": f"сотрудник «{eid}»: разные станции в одно время "
                                    f"{c['start']}–{c['end']}: {', '.join(c['stations'])} "
                                    f"— местоположение в этой области неизвестно",
                            "evidence": "строки " + ", ".join(
                                f"{r['sheet']}!{r['row']}" for r in c["rows"])})
            ld = emp.get("last_duty") if isinstance(emp.get("last_duty"), dict) else {}
            end, station = ld.get("end"), ld.get("end_station")
            if not end or not station:
                continue
            for iv in emp.get("location_intervals") or []:
                if iv["kind"] != KIND_HISTORY_FACT or iv["station"] == str(station).upper():
                    continue
                if iv["start"] <= end < iv["end"]:
                    first = iv["sources"][0]
                    out.append({"target": {"sid": first["sid"], "sheet": first["sheet"],
                                           "row": first["row"]},
                                "text": f"сотрудник «{eid}»: факт присутствия в "
                                        f"{iv['station']} {iv['start']}–{iv['end']} "
                                        f"накрывает конец последней смены {end} в "
                                        f"{station} — противоречие истории",
                                "evidence": "строки " + ", ".join(
                                    f"{r['sheet']}!{r['row']}" for r in iv["sources"])
                                + f"; «Последняя смена: станция» = {station}"})
        return len(rows), out
    raise ValueError(f"неизвестный шаблон присутствия {template!r}")


def _approved_specs(tables: dict) -> list[dict]:
    approved = find_sheet(tables, APPROVED_SHEETS)
    if not approved:
        return []
    asid, aname, _atable = approved
    return [
        {"id": f"C-история-закрепления-{asid}", "basis": {"builtin": "история"},
         "template": "история-закрепления", "params": {"sheet": f"{asid}/{aname}"},
         "statement": f"Каждая строка листа «{aname}» ({asid}): табельный номер "
                      f"есть в «Экипажах», «Рейс» и «Дата» заполнены и читаются, "
                      f"рейс с таким номером есть в листе «Рейсы» на эту дату "
                      f"(канон 1.17.1; иначе потребитель откажет нажатию)",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ",
         "status": "скомпилирована"}]


def _flights_by_day(tables: dict) -> set[tuple[str, str]] | None:
    """{(номер рейса, дата ISO)} по листу «Рейсы»; None — листа или колонок нет."""
    found = find_sheet(tables, FLIGHT_SHEETS)
    if not found:
        return None
    _sid, _name, table = found
    cols = table.get("columns") or []
    num_col = next((c for c in cols if str(c).strip().lower() in FLIGHT_NUMBER_COLUMNS), None)
    day_col = next((c for c in cols if str(c).strip().lower() in FLIGHT_DATE_COLUMNS), None)
    if num_col is None or day_col is None:
        return None
    out: set[tuple[str, str]] = set()
    for r in table.get("rows") or []:
        num = _cell(r, num_col)
        when = parse_moment(_cell(r, day_col))
        if num and when is not None:
            out.add((num, when.date().isoformat()))
    return out


def run_check(template: str, params: dict, dataset: dict) -> tuple[int, list[dict]]:
    """Исполнение шаблонов «история-…»: (записей рассмотрено, находки
    без id/серьёзности — их назначает сборка отчёта)."""
    from unicode_data import confusable_hint
    if template.startswith("история-журнал-"):
        return hj.run_check(template, params, dataset)
    if template.startswith("горизонт-"):
        return _run_horizon_check(template, params, dataset)
    if template.startswith("история-присутствие-"):
        return _run_presence_check(template, params, dataset)
    tables = dataset.get("tables") or {}
    sid, name = str(params["sheet"]).split("/", 1)
    table = (tables.get(sid) or {}).get(name) or {}
    rows = table.get("rows") or []
    columns = table.get("columns") or []
    fields, rolling, _unknown = column_map(columns)
    id_col = next((c for c, p in fields.items() if p == ("employee_id",)), None)
    out: list[dict] = []

    def tgt(i, col=None):
        t = {"sid": sid, "sheet": name, "row": i}
        if col:
            t["column"] = col
        return t

    if template == "история-табельный":
        known = set(employee_ids(tables))
        for i, row in enumerate(rows, 2):
            eid = _cell(row, id_col) if id_col else ""
            if not eid:
                out.append({"target": tgt(i, id_col), "text": "табельный номер пуст",
                            "evidence": f"{sid}!{name} строка {i}"})
            elif known and eid not in known:
                out.append({"target": tgt(i, id_col),
                            "text": f"табельный номер «{eid}» отсутствует в листе «Экипажи»"
                                    + confusable_hint(eid, known),
                            "evidence": f"{sid}!{name} строка {i}: «{eid}»"})
        return len(rows), out
    if template == "история-значения":
        for i, row in enumerate(rows, 2):
            _rec, problems = parse_row(row, fields, rolling)
            for p in problems:
                col = p.split("»", 1)[0].lstrip("«")
                out.append({"target": tgt(i, col), "text": p,
                            "evidence": f"{sid}!{name} строка {i}"})
        return len(rows), out
    if template == "история-срез":
        default_as_of = plan_since(tables)
        for i, row in enumerate(rows, 2):
            rec, _problems = parse_row(row, fields, rolling)
            as_of = rec.get("as_of") or default_as_of
            end = (rec.get("last_duty") or {}).get("end")
            if as_of and end and end > as_of:
                out.append({"target": tgt(i, "Последняя смена: конец"),
                            "text": f"конец последней смены {end} позже среза {as_of}",
                            "evidence": f"{sid}!{name} строка {i}"})
        return len(rows), out
    if template == "история-полнота":
        crew = employee_rows(tables)
        have = {_cell(r, id_col) for r in rows} if id_col else set()
        for eid, addr in crew:
            if eid not in have:
                # Адрес находки — строка сотрудника на листе экипажей:
                # это запись, у которой нет истории (на листе «История»
                # строки нет, и адресовать там нечего).
                out.append({"target": dict(addr),
                            "text": f"у сотрудника «{eid}» нет строки истории — "
                                    f"поля раздела будут null (не ноль)",
                            "evidence": f"«{addr['sheet']}» строка {addr['row']}: "
                                        f"{eid}; «{name}»: строки нет"})
        return max(len(crew), 1), out
    if template == "история-закрепления":
        # Лист «Утверждённые назначения» (канон 1.17.1): табельный в «Экипажах»,
        # «Рейс» и «Дата» заполнены и читаются, рейс есть в «Рейсах» на дату.
        # Потребитель отказывает всему нажатию по любой из этих причин —
        # ловим до пакета.
        known = set(employee_ids(tables))
        flights = _flights_by_day(tables)
        canon = {approved_key(c): c for c in columns}
        num_col, day_col = canon.get("Рейс"), canon.get("Дата")
        for i, row in enumerate(rows, 2):
            eid = _cell(row, id_col) if id_col else ""
            if not eid or eid not in known:
                out.append({"target": tgt(i, id_col), "text": f"табельный номер «{eid}» "
                            f"не найден в листе «Экипажи»" if eid else "табельный номер пуст",
                            "evidence": f"{sid}!{name} строка {i}"})
                continue
            num = _cell(row, num_col) if num_col else ""
            when = parse_moment(_cell(row, day_col)) if day_col else None
            if not num or when is None:
                out.append({"target": tgt(i, num_col if not num else day_col),
                            "text": "закрепление без читаемых «Рейс» и «Дата» "
                                    "(канон: «Рейс», «Дата» ДД.ММ.ГГГГ)",
                            "evidence": f"{sid}!{name} строка {i}: «Рейс»=«{num}», "
                                        f"«Дата»=«{_cell(row, day_col) if day_col else ''}»"})
                continue
            if flights is None:
                out.append({"target": tgt(i, num_col),
                            "text": "рейс закрепления нельзя сверить: листа «Рейсы» "
                                    "с колонками номера и даты нет",
                            "evidence": f"{sid}!{name} строка {i}"})
                continue
            day = when.date().isoformat()
            if (num, day) not in flights:
                out.append({"target": tgt(i, num_col),
                            "text": f"рейса «{num}» нет в листе «Рейсы» на {day}",
                            "evidence": f"{sid}!{name} строка {i}: {eid} → {num} {day}"})
        return len(rows), out
    raise ValueError(f"неизвестный шаблон истории {template!r}")


# ── раздел пакета ──

def _legacy_param(rule: dict, name: str) -> dict | None:
    """Запись машинного параметра правила (rule.params — СПИСОК записей
    {name, value, unit, tz, as_written} по контракту модели)."""
    params = rule.get("params")
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict) and p.get("name") == name:
                return p
        return None
    if isinstance(params, dict):          # терпимость к словарной форме
        cell = params.get(name)
        if isinstance(cell, dict):
            return dict(cell, name=name)
        if cell is not None:
            return {"name": name, "value": cell}
    return None


def _rule_param_value(rule: dict, name: str):
    """Значение параметра правила по имени: посадка (template_fit.params) или
    машинный params-список; None — параметра нет."""
    fit = rule.get("template_fit") if isinstance(rule.get("template_fit"), dict) else {}
    cell = (fit.get("params") or {}).get(name) if isinstance(fit.get("params"), dict) else None
    if isinstance(cell, dict) and cell.get("value") is not None:
        return cell.get("value")
    if isinstance(cell, (int, float)):
        return cell
    p = _legacy_param(rule, name)
    return p.get("value") if p else None


def _rule_param_tz(rule: dict, name: str) -> str | None:
    """Пояс параметра времени суток (tz машинной записи; у посадки поля tz
    нет — тогда None)."""
    p = _legacy_param(rule, name)
    return str(p.get("tz")) if p and p.get("tz") else None


def _rule_logic_windows(rule: dict) -> list[int]:
    """Окна rolling из блока logic (в сутках): узел окна {kind: rolling,
    unit: day, param} → значение параметра; unit duty окном дней не является."""
    out: list[int] = []
    logic = rule.get("logic")
    if not isinstance(logic, dict):
        return out

    def walk(node):
        if isinstance(node, dict):
            if node.get("kind") == "rolling" and node.get("unit") == "day" \
                    and node.get("param"):
                v = _rule_param_value(rule, str(node["param"]))
                n = parse_num(str(v)) if v is not None else None
                if n is not None and n > 0:
                    out.append(int(n))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(logic)
    return out


def rule_windows_days(doc: dict | None) -> list[int]:
    """Канонические окна (7, 14, 28 суток) плюс окна из параметров правил
    пакета: weekly_rest_period / accumulation_window (минуты, кратные суткам),
    окна счётчиков в сутках WINDOW_DAY_PARAMS (count; «выходных в окне не
    менее min_days_off_in_window» при семье days_off_floor, «ночных смен в
    окне не более max_night_duties_in_window» при семье night_series —
    имена согласованы с COSMOS 05.09: параметр принадлежит ровно одной семье)
    и окна rolling блоков logic."""
    windows = set(hj.CANON_WINDOWS)
    for r in (doc or {}).get("rules", []) or []:
        if not isinstance(r, dict):
            continue
        for name in ("weekly_rest_period", "accumulation_window"):
            v = _rule_param_value(r, name)
            n = parse_num(str(v)) if v is not None else None
            if n is not None and n > 0 and int(n) % 1440 == 0:
                windows.add(int(n) // 1440)
        for name in WINDOW_DAY_PARAMS:
            v = _rule_param_value(r, name)
            n = parse_num(str(v)) if v is not None else None
            if n is not None and n > 0:
                windows.add(int(n))
        windows.update(_rule_logic_windows(r))
    return sorted(windows)


def night_window_of(doc: dict | None) -> tuple[int, int] | None:
    """Окно ночи пакета (минуты суток, [от, до)) из параметров
    night_duty_start / night_duty_end правил; None — параметров нет."""
    for r in (doc or {}).get("rules", []) or []:
        if not isinstance(r, dict):
            continue
        a = _rule_param_value(r, "night_duty_start")
        b = _rule_param_value(r, "night_duty_end")
        if a is None or b is None:
            continue
        na, nb = parse_num(str(a)), parse_num(str(b))
        if na is None or nb is None:
            continue
        # Окно ночи читается в шкале книги (base-local, manifest.time); параметр
        # в ином поясе (UTC, acclimatized) для вывода признака не годится.
        tz = _rule_param_tz(r, "night_duty_start") or _rule_param_tz(r, "night_duty_end")
        if tz and tz != "base-local":
            continue
        return int(na) % 1440, int(nb) % 1440
    return None


def _logic_reads_history(logic) -> bool:
    found = False

    def walk(node):
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            if node.get("subject") in _LOGIC_HISTORY_SUBJECTS \
                    or node.get("kind") == "rolling" \
                    or node.get("predicate") == "consecutive" \
                    or node.get("kind") == "consecutive":
                found = True
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(logic)
    return found


def rule_families(rule: dict | None) -> set:
    """Семьи, которыми строка легла на базу шаблонов: посадка документа и
    семья выгрузки решателю (контракт один — расхождение судит
    валидатор)."""
    fit = (rule or {}).get("template_fit")
    exp = (rule or {}).get("export_solver")
    fit = fit if isinstance(fit, dict) else {}
    exp = exp if isinstance(exp, dict) else {}
    return {f for f in (fit.get("family"), exp.get("family")) if f}


def history_dependent_rules(doc: dict | None) -> list[dict]:
    """Строки ростеринга (зоны ROSTERING/BOTH), которые судятся с остатками
    истории: семьи history_families() (записи каталога с `reads_history`)
    по посадке либо блок logic с предметами rest / day_off /
    rolling_window / month или предикатом consecutive. Пустой список —
    истории пакету не нужно, вопросов о ней не задаётся."""
    out: list[dict] = []
    readers = history_families()
    for r in (doc or {}).get("rules", []) or []:
        if not isinstance(r, dict) or r.get("kind") in ("OUT", "DEF"):
            continue
        if r.get("zone") not in ("ROSTERING", "BOTH"):
            continue
        if rule_families(r) & readers or _logic_reads_history(r.get("logic")):
            out.append(r)
    return out


def history_question_coverage(q: dict | None) -> str | None:
    """Полнота данных истории, при которой ЗАДАН блок анкеты: слово
    `history.coverage` вопроса. У вопросов прежней формы полноту называл
    сам идентификатор — она читается оттуда. Не вопрос об истории или
    полнота не названа — None."""
    if not isinstance(q, dict) or not is_history_question(q.get("id")):
        return None
    block = q.get("history")
    if isinstance(block, dict) and block.get("coverage") in HISTORY_COVERAGE_KINDS:
        return str(block["coverage"])
    return LEGACY_HISTORY_COVERAGE.get(str(q.get("id")))


#: Решение авиакомпании по ключу ответа и полноте данных, при которой
#: вопрос задан: «а» — подать данные и ждать расчёта, «б» — считать по
#: тому, что есть (нулевые остатки либо частичная история).
HISTORY_DECISIONS = {
    ("partial", "а"): "supplement_requested",
    ("partial", "б"): "continue_partial",
    ("none", "а"): "enter_requested",
    ("none", "б"): "continue_zero",
}


#: Обратный взгляд на HISTORY_DECISIONS: решения, законные при данной
#: полноте данных истории (раздел пакета сверяет решение с полнотой).
_DECISIONS_OF_COVERAGE = {
    cov: frozenset(v for (c, _k), v in HISTORY_DECISIONS.items() if c == cov)
    for cov in HISTORY_COVERAGE_KINDS}


def history_decision_of(doc: dict | None) -> dict | None:
    """Решение авиакомпании по трём исходам из применённых ответов анкеты:
    {question, key, decision} последнего по ревизии применённого ответа на
    вопросы Q-3b-история-* (блоки по видам чтения и вопросы прежней
    формы); None — вопроса или ответа нет.

    Решение называется по ПОЛНОТЕ, при которой вопрос задан: один и тот же
    ключ «б» при полноте «нет» означает нулевые остатки, при «частично» —
    расчёт по частичным данным."""
    best = None
    superseded = {q.get("edition_of") for q in (doc or {}).get("questions", [])
                  if isinstance(q, dict)}
    for q in (doc or {}).get("questions", []) or []:
        if not isinstance(q, dict) or not is_history_question(q.get("id")):
            continue
        if q.get("id") in superseded:
            continue
        from cdpm import question_editions
        if question_editions.problems(q):
            continue  # invalid semantics cannot authorize partial/zero history
        ans = q.get("answer") if isinstance(q.get("answer"), dict) else None
        if not ans or ans.get("status") != "применён" or not ans.get("key"):
            continue
        if ans.get("key") not in ("а", "б"):
            continue           # снят кодом («неприменимо») — не решение
        coverage = history_question_coverage(q)
        if coverage is None:
            continue           # полнота не названа — решением не является
        rev = int(ans.get("revision") or 0)
        if best is None or rev > best[0]:
            best = (rev, q["id"], str(ans["key"]), coverage)
    if best is None:
        return None
    _rev, qid, key, coverage = best
    # Форма решения закрыта тремя ключами: её читает раздел пакета
    # (manifest.history.user_decision) и потребитель за ним.
    return {"question": qid, "key": key,
            "decision": HISTORY_DECISIONS[(coverage, key)]}


def _null_record(eid: str, as_of: str | None) -> dict:
    return {"employee_id": eid, "as_of": as_of,
            "month_to_date": None, "rolling": None, "last_duty": None,
            "series": None, "location": None, "rest_before_horizon_min": None,
            "calendar": None, "approved_assignments": [], "provenance": None}


def calendar_from_sheet(tables: dict, as_of: str | None, days: int) -> dict[str, list[dict]]:
    """Посуточный календарь из листа «Календарь истории» (1.17.2): по
    сотруднику — список суток as_of−days … as_of−1 с видом из словаря и
    признаком ночной; суток без строки — unknown. Нечитаемые строки
    пропускаются (их вычитает линия данных)."""
    found = find_sheet(tables, CALENDAR_SHEETS)
    if not found or not as_of:
        return {}
    _sid, _name, table = found
    cols = table.get("columns") or []
    id_col = next((c for c in cols if str(c).strip().lower() in EMPLOYEE_ID_COLUMNS), None)
    date_col = next((c for c in cols if str(c).strip().lower() in ("дата", "date")), None)
    kind_col = next((c for c in cols if str(c).strip().lower() in ("вид", "тип", "kind")), None)
    night_col = next((c for c in cols if str(c).strip().lower() in ("ночная", "night")), None)
    if id_col is None or date_col is None or kind_col is None:
        return {}
    as_of_dt = datetime.strptime(as_of, hj.ISO)
    first = (as_of_dt - timedelta(days=days)).date()
    last = (as_of_dt - timedelta(minutes=1)).date()
    cells: dict[str, dict] = {}
    rows_of: dict[str, list[int]] = {}
    for i, row in enumerate(table.get("rows") or [], 2):
        eid = _cell(row, id_col)
        when = parse_moment(_cell(row, date_col))
        kind = _CALENDAR_KIND_RU.get(_cell(row, kind_col).lower())
        if not eid or when is None or kind is None:
            continue
        d = when.date()
        if d < first or d > last:
            continue
        night_raw = _cell(row, night_col).lower() if night_col else ""
        night = True if night_raw in _YES else (False if night_raw in _NO else None)
        if kind != "work":
            night = False if kind != "unknown" else None
        cells.setdefault(eid, {})[d] = {"kind": kind, "night": night}
        rows_of.setdefault(eid, []).append(i)
    out: dict[str, dict] = {}
    for eid, by_day in cells.items():
        cal = []
        d = first
        while d <= last:
            c = by_day.get(d)
            cal.append({"date": d.isoformat(), "kind": c["kind"] if c else "unknown",
                        "night": c["night"] if c else None})
            d += timedelta(days=1)
        if any(c["kind"] != "unknown" for c in cal):
            out[eid] = {"calendar": cal,
                        "provenance": {"sid": _sid, "sheet": _name, "rows": rows_of[eid]}}
    return out


def _rest_before_horizon(rec: dict) -> None:
    """rest_before_horizon_min = срез − конец последней смены (минуты)."""
    ld = rec.get("last_duty") if isinstance(rec.get("last_duty"), dict) else None
    end, as_of = (ld or {}).get("end"), rec.get("as_of")
    if end and as_of:
        try:
            a = datetime.strptime(as_of, hj.ISO)
            b = datetime.strptime(end, hj.ISO)
            rec["rest_before_horizon_min"] = int((a - b).total_seconds() // 60)
            return
        except ValueError:
            pass
    rec["rest_before_horizon_min"] = None


def history_section(clean_data: dict | None, doc: dict | None = None) -> dict | None:
    """manifest.history из очищенных данных и ответов анкеты (1.17 → 1.17.2).

    Источник: лист «История» (integrator_sheet), иначе производный раздел
    из листа «Журнал назначений» (journal), иначе none. None (раздела нет)
    — только когда нет ни листа, ни журнала, ни решения авиакомпании по
    вопросу трёх исходов, ни листа «Присутствие» (1.18.2 — свидетельства
    едут в employees[], потому лист присутствия сам по себе даёт раздел
    с coverage none): потребитель заявляет «история не передана».
    Гейт пакета зовёт ту же функцию с теми же двумя файлами пакета."""
    tables = clean_data.get("tables") if isinstance(clean_data, dict) else None
    if not isinstance(tables, dict):
        return None
    sheet = find_sheet(tables, HISTORY_SHEETS)
    journal = find_sheet(tables, JOURNAL_SHEETS)
    decision = history_decision_of(doc)
    presence = presence_by_employee(tables) \
        if find_sheet(tables, PRESENCE_SHEETS) else None
    if sheet is None and journal is None and decision is None and presence is None:
        return None
    default_as_of = plan_since(tables)
    crew = employee_ids(tables)
    windows = rule_windows_days(doc)
    night_window = night_window_of(doc)
    # Глубина посуточного календаря (1.17.2): не меньше 28 суток и не меньше
    # наибольшего окна правил пакета.
    calendar_days = max(28, max(windows) if windows else 0)
    by_id: dict[str, dict] = {}
    employees: list[dict] = []
    unknown: list = []
    source: dict = {"kind": "none", "sid": None, "sheet": None, "unknown_columns": []}

    if sheet is not None:
        sid, name, table = sheet
        fields, rolling, unknown = column_map(table.get("columns") or [])
        source = {"kind": "integrator_sheet", "sid": sid, "sheet": name,
                  "unknown_columns": sorted(str(c) for c in unknown)}
        for i, row in enumerate(table.get("rows") or [], 2):
            rec, _problems = parse_row(row, fields, rolling)
            eid = rec.get("employee_id")
            if not eid:
                continue
            rec["as_of"] = rec.get("as_of") or default_as_of
            rec["provenance"] = {"sid": sid, "sheet": name, "row": i}
            rec["approved_assignments"] = []
            if not has_any_value(rec):
                # Ревизия 1.17.1: строка есть, но ни одного значения — та же
                # «запись без истории», что и отсутствие строки: блоки null
                # (не словари из null — читатель не должен угадывать),
                # провенанс строки сохраняется.
                rec.update(month_to_date=None, rolling=None, last_duty=None,
                           series=None, location=None)
            _rest_before_horizon(rec)
            by_id[eid] = rec
            employees.append(rec)

    depth_available = None
    if journal is not None and default_as_of:
        jsid, jname, jtable = journal
        keys, junknown = hj.column_map(jtable.get("columns") or [])
        if not hj.missing_columns(jtable.get("columns") or []):
            as_of_dt = datetime.strptime(default_as_of, hj.ISO)
            entries: dict[str, list[dict]] = {}
            rows_of: dict[str, list[int]] = {}
            for i, row in enumerate(jtable.get("rows") or [], 2):
                entry, problems = hj.parse_entry(row, keys, night_window)
                if entry is None:
                    continue           # нечитаемая строка — вычтена линией данных
                entries.setdefault(entry["employee_id"], []).append(entry)
                rows_of.setdefault(entry["employee_id"], []).append(i)
            # Глубина журнала — ПО СОТРУДНИКУ: полнота требует глубины у каждого;
            # в разделе — наименьшая среди выведенных из журнала.
            depths: list[int] = []
            derived = 0
            for eid, lst in sorted(entries.items()):
                if eid in by_id or (crew and eid not in crew):
                    continue       # лист «История» имеет приоритет; чужие — вне
                d_emp = hj.depth_days(lst, as_of_dt)
                if d_emp is not None:
                    depths.append(d_emp)
                rec = hj.derive_record(lst, as_of_dt, windows, calendar_days)
                rec.update(employee_id=eid, as_of=default_as_of,
                           approved_assignments=[],
                           provenance={"sid": jsid, "sheet": jname,
                                       "rows": sorted(rows_of[eid])})
                if not has_any_value(rec):
                    rec.update(month_to_date=None, rolling=None, last_duty=None,
                               series=None, location=None)
                by_id[eid] = rec
                employees.append(rec)
                derived += 1
            depth_available = min(depths) if depths else None
            if sheet is None:
                source = {"kind": "journal", "sid": jsid, "sheet": jname,
                          "unknown_columns": sorted(str(c) for c in junknown)}
            elif derived:
                source["mixed"] = True
                source["journal"] = {"sid": jsid, "sheet": jname}

    # Сотрудники «Экипажей» без записи — явные null.
    for eid in crew:
        if eid not in by_id:
            rec = _null_record(eid, default_as_of)
            by_id[eid] = rec
            employees.append(rec)
    # Свидетельства присутствия (1.18.2): у сотрудника со строками листа
    # «Присутствие» — три ключа; без строк ключей нет. Чужие табельные
    # (не в «Экипажах») — вне раздела, как у журнала.
    for eid, evidence in sorted((presence or {}).items()):
        rec = by_id.get(eid)
        if rec is None:
            # GPT-022 B1: без реестра сотрудников присутствие некому
            # отнести — запись не создаётся (как и для чужого табельного).
            if not crew or eid not in crew:
                continue
            rec = _null_record(eid, default_as_of)
            by_id[eid] = rec
            employees.append(rec)
        intervals, conflicts = location_intervals_of(evidence)
        rec["location_evidence"] = evidence
        rec["location_intervals"] = intervals
        rec["conflicts"] = conflicts
    approved = find_sheet(tables, APPROVED_SHEETS)
    if approved:
        asid, aname, atable = approved
        cols = atable.get("columns") or []
        id_col = next((c for c in cols if str(c).strip().lower()
                       in EMPLOYEE_ID_COLUMNS), None)
        for i, row in enumerate(atable.get("rows") or [], 2):
            eid = _cell(row, id_col) if id_col else ""
            rec = by_id.get(eid)
            if rec is None:
                continue
            # Канон 1.17.1: «Рейс» и «Дата» под каноническими ключами,
            # прочие колонки — дословно.
            item = {approved_key(c): _cell(row, c) for c in cols if c != id_col}
            item["provenance"] = {"sid": asid, "sheet": aname, "row": i}
            rec["approved_assignments"].append(item)
    # Посуточный календарь листа интегратора (1.17.2): дополняет записи без
    # календаря (лист «История» и записи без строки); счётчики окон — из него.
    for eid, cal in calendar_from_sheet(tables, default_as_of, calendar_days).items():
        rec = by_id.get(eid)
        if rec is None or rec.get("calendar"):
            continue
        rec["calendar"] = cal["calendar"]
        rolling = dict(rec["rolling"]) if isinstance(rec.get("rolling"), dict) else {}
        for n in windows:
            rolling.update(hj.counters_from_calendar(cal["calendar"], n))
        rec["rolling"] = rolling
        if rec.get("provenance") is None:
            rec["provenance"] = cal["provenance"]
    employees.sort(key=lambda r: str(r.get("employee_id")))
    total = len(crew) if crew else len(employees)
    with_history = sum(1 for r in employees if r.get("provenance") is not None
                       and has_any_value(r))
    coverage: dict = {"employees_total": total, "with_history": with_history}
    depth_ok = True
    if journal is not None and default_as_of and depths:
        # Глубина судится у записей, выведенных из журнала (и в смешанном
        # режиме тоже): требуемая — не меньше 28 суток, начала месяца среза
        # и наибольшего окна правил.
        as_of_dt = datetime.strptime(default_as_of, hj.ISO)
        required = max(28, as_of_dt.day - 1, max(windows) if windows else 0)
        coverage["depth_days_required"] = required
        coverage["depth_days_available"] = depth_available
        depth_ok = depth_available is not None and depth_available >= required
    if with_history == 0:
        coverage["kind"] = "none"
    elif with_history >= total and depth_ok:
        coverage["kind"] = "full"
    else:
        coverage["kind"] = "partial"
    # Решение пользователя — только по вопросу ТЕКУЩЕЙ полноты: применённый
    # ответ, данный при другой полноте, устарел и раздел не определяет
    # (открытый вопрос текущей полноты держит замок потребителя). Полноту
    # называет сам вопрос (блок `history`, у прежней формы — идентификатор),
    # а не его имя: имя блока называет вид чтения истории. При полной
    # истории прежняя просьба «дополнить/ввести» становится «данные дошли».
    user_decision = None
    if decision is not None:
        if coverage["kind"] == "full":
            if decision["decision"] in ("supplement_requested", "enter_requested"):
                user_decision = dict(decision)
                user_decision["decision"] = "supplemented"
        elif decision["decision"] in _DECISIONS_OF_COVERAGE[coverage["kind"]]:
            user_decision = dict(decision)
    body = json.dumps(employees, ensure_ascii=False, sort_keys=True)
    return {
        "as_of": default_as_of,
        "source": source,
        "coverage": coverage,
        "user_decision": user_decision,
        "assume_zero_when_missing": bool(user_decision
                                         and user_decision["decision"] == "continue_zero"),
        "employees": employees,
        "data_fingerprint": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
