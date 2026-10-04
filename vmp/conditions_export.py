# -*- coding: utf-8 -*-
"""Этап 5б: экспорт файла УСЛОВИЙ расчёта из пакета постановки.

Замер решателя (2026-08-27, диагностика на кнопочном наборе одной
из линий):
точная ярусная форма пределов смены (worktime_exact) вместо плоского
«строжайшего из всех» снимает больше половины ложных непокрытых —
вердикт «путь условий работает, стартуй продуктовую проводку».

Файл условий — форма приёмника решателя (_conditions.Conditions):
обязательная группа A (состав, подготовка/разбор, стыковка, лестницы
отдыха, граница командировки) + группа B (наихудшие трактовки) +
необязательная точная форма worktime_exact. Модуль собирает словарь
ДЕТЕРМИНИРОВАННО из машинных данных пакета: параметров правил модели,
машинных форм порогов (threshold-forms.json), машинного поля
численности манифеста и данных книги. Ни одного числа в модуле нет:
число без машинного носителя в пакете — ПРОБЕЛ, и сборка честно
отказывает с поимённым перечнем пробелов (какой ключ условий, что
искали, чем закрывается: миграцией класса «норма прозой», вопросом
анкеты либо вопросом постановки). Урок r3 обеих линий: норма прозой
не существует; выдумывать значение здесь значило бы считать по
числу, которого нет в постановке.

Ужесточено состязательным ревью 2026-08-27 (находки «проверено
исполнением»): носители отбираются по имени И единице И отношению;
стыковка ищется настоящим словарным именем min_turnaround с областью;
верх лестниц отдыха учитывает продлённые пределы; запасная ветвь
b12 приёмника (жёсткие 12 часов) собирается только при машинном поле
ровно 12 часов; условные полы фильтруются по своему порогу; окно
матрицы через полночь — честный отказ.

След: каждый ключ условий несёт source с идентификаторами правил
модели — та же дисциплина, что у контракта построителя (Г-П5)."""
from __future__ import annotations

import re

_ACTIVE_KINDS = ("HARD", "DATA", "DEF")


DETECTORS_FILE = "vmp-condition-detectors.json"


def _load_condition_detectors():
    """Детекторные формулировки правил контура — файлом-носителем
    (конвенция 2026-08-29): [(key, compiled_regex), ...] либо None
    (файла нет — детекторы спят). Кривой файл — честный отказ."""
    import json
    import os
    from pathlib import Path
    override = os.environ.get("CREW_VMP_DETECTORS")
    if override:
        p = Path(override)
    else:
        try:
            import _data_paths
            p = Path(_data_paths.data_root()) / DETECTORS_FILE
        except Exception:                                 # noqa: BLE001
            return None
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(doc, dict) and doc.get("customer")
        out = []
        for d in doc["detectors"]:
            out.append((str(d["key"]),
                        re.compile(str(d["pattern"]), re.IGNORECASE)))
        return out
    except Exception as e:                                # noqa: BLE001
        raise ConditionsExportError([{
            "key": "condition_detectors",
            "missing": f"носитель детекторов контура {p} кривой: {e}",
            "closure": "исправить файл по конвенции 2026-08-29 "
                       "(form_version, customer, detectors)",
        }]) from e


class ConditionsExportError(ValueError):
    """Условия не собираются; пробелы названы поимённо (fail-closed)."""

    def __init__(self, gaps: list[dict]):
        self.gaps = gaps
        lines = ["файл условий расчёта не собирается — пробелы "
                 "машинной формы пакета:"]
        for g in gaps:
            lines.append("  - %s: %s (закрытие: %s)"
                         % (g["key"], g["missing"], g["closure"]))
        super().__init__("\n".join(lines))


def _active_rules(model: dict) -> list:
    """Правила модели, участвующие в расчёте: без пометки «исключено
    до получения данных по решению авиакомпании» (структура ВМП,
    владелец 2026-09-02) — исключённое правило не носитель условий
    расчёта; его перечень несёт манифест пакета и сертификат."""
    from vmp.registry_check import is_excluded_until_data
    return [r for r in (model.get("rules") or [])
            if isinstance(r, dict) and not is_excluded_until_data(r)]


def _params(model: dict, name: str, *, unit: str | None,
            relation: str | None, kinds=_ACTIVE_KINDS):
    """(правило, параметр) для машинных носителей имени. Единица и
    отношение проверяются (ревью, находка 17): носитель в часах или
    с отношением-потолком — не пол в минутах, брать его нельзя."""
    out = []
    for r in _active_rules(model):
        if r.get("kind") not in kinds:
            continue
        for p in (r.get("params") or []):
            if p.get("name") != name \
                    or not isinstance(p.get("value"), (int, float)) \
                    or isinstance(p.get("value"), bool):
                continue
            if unit is not None and p.get("unit") != unit:
                continue
            if relation is not None and p.get("relation") != relation:
                continue
            out.append((r, p))
    return out


def _floor_params(model: dict, name: str):
    return _params(model, name, unit="duration_minutes",
                   relation="min")


def _strictest_min(model: dict, name: str):
    """Строжайший пол (наибольшее значение) с носителем, либо None."""
    best = None
    for r, p in _floor_params(model, name):
        if best is None or p["value"] > best[1]["value"]:
            best = (r, p)
    return best


def _fleets_of(payload: dict) -> list[str]:
    """Действующие флоты — типы судов ОБЯЗАТЕЛЬСТВ покрытия набора
    (транспорт составом не комплектуется)."""
    fleets: list[str] = []
    for f in payload.get("flights") or []:
        if f.get("transportOnly"):
            continue
        t = str(f.get("aircraftType") or "").strip()
        if t and t not in fleets:
            fleets.append(t)
    return fleets


def _base_of(payload: dict):
    return sorted({e.get("homeAirport") or e.get("base")
                   for e in payload.get("employees") or []
                   if e.get("homeAirport") or e.get("base")})


def _ladder(floor_min: int, top_min: int, source: str) -> dict:
    """Лестница «отдых не меньше предыдущей смены и не меньше пола»
    в табличной форме приёмника: ступени по получасу от пола до
    максимально возможной смены. Таблица — точная запись правила
    max(смена, пол): ни одного числа сверх пары машинных границ."""
    rows = []
    h = floor_min / 60.0
    top_h = max(top_min / 60.0, h)
    while True:
        rows.append({"work_upto_h": round(h, 2), "rest_h": round(h, 2)})
        if h >= top_h:
            break
        h += 0.5
    return {"values": rows, "source": source}


# Вопросы условий (ensure_conditions_questions): применённые ответы
# авиакомпании хранятся в модели дословно — законный машинный
# носитель таблиц, которых закрытый словарь параметров не выражает.
# Разбор ДЕТЕРМИНИРОВАННЫЙ по заданной вопросом форме; не разобрался
# — прежний пробел (fail-closed), никаких догадок.
_Q_APP4 = "Q-3a-условия-прил4"
_Q_APP6 = "Q-3a-условия-прил6"
_Q_ACCLIM = "Q-3a-условия-акклиматизация"
_Q_SPAN = "Q-3a-условия-размах-командировки"
_HHMM = r"(\d{1,2}):(\d{2})"


def _parse_span(raw: str) -> int | None:
    """Минуты непрерывного размаха командировки из ответа а/к:
    «11520 минут» либо «8 суток»."""
    m = re.search(r"(\d{3,6})\s*мин", raw)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,2})\s*сут", raw)
    if m:
        return int(m.group(1)) * 1440
    return None


def _answer_raw(model: dict, qid: str):
    """Only the current explicit edition supplies an applied answer."""
    from cdpm import question_editions

    try:
        q = question_editions.current_for_root(model, qid)
    except question_editions.EditionRefusal as exc:
        raise ConditionsExportError([{
            "key": "question_edition." + qid,
            "missing": str(exc),
            "closure": "согласованное издание вопроса и ответ на него",
        }]) from exc
    if q is not None:
        a = q.get("answer") or {}
        if a.get("status") == "применён" and a.get("raw"):
            return str(a["raw"]), a.get("revision")
    return None, None


def _parse_app4(raw: str) -> dict | None:
    """Прил. 4: часы по корзинам посадок из ответа а/к."""
    out = {}
    for key, pat in (("1-2", r"1-2"), ("3", r"3"), ("4", r"4"),
                     ("5+", r"5(?:\+|\s*и\s*более)")):
        m = re.search(pat + r"\s*сектор\w*\s*=\s*" + _HHMM, raw,
                      re.IGNORECASE)
        if not m:
            return None
        out[key] = int(m.group(1)) + int(m.group(2)) / 60.0
    return out


def _parse_app6(raw: str):
    """Прил. 6: (классы по типам ВС, продлённые часы по классам)."""
    classes = {m.group(1): int(m.group(2)) for m in re.finditer(
        r"([\wА-Яа-я][\w.А-Яа-я-]*)\s*=\s*класс\s*(\d)", raw)}
    ext = {m.group(1): int(m.group(2)) + int(m.group(3)) / 60.0
           for m in re.finditer(
               r"класс\s*(\d)\s*=\s*" + _HHMM, raw)}
    if not classes or not ext:
        return None
    return classes, ext


def _parse_acclim(raw: str) -> dict | None:
    """Пороги акклиматизации: машинные скобки ответа либо словесная
    форма («более N часов», «до N часов после прибытия»)."""
    tz = re.search(r"tz_from_h\s*=\s*(\d+)", raw) \
        or re.search(r"более\s+(\d+)\s*час", raw, re.IGNORECASE)
    el = re.search(r"elapsed_b_h\s*=\s*(\d+)", raw) \
        or re.search(r"до\s+(\d+)\s*час[а-я]*\s+после\s+прибыти",
                     raw, re.IGNORECASE)
    if not tz or not el:
        return None
    return {"elapsed_b_h": int(el.group(1)),
            "tz_from_h": int(tz.group(1))}


# Гомоглифы кириллица/латиница (живой брак девятки добора,
# 2026-08-27 вечер): ключ класса вида «A990-1» латиницей против типа
# «А990-1» кириллицей в данных книги — класс молча не находился,
# продлённый предел исчезал. Невидимая буква = невидимая норма:
# ключи словарей по флоту канонизируются к ФАКТИЧЕСКИМ типам набора
# байт-в-байт.
_HOMOGLYPHS = str.maketrans(
    "АВЕКМНОРСТУХаверскухот", "ABEKMHOPCTYXabepckyxot")


def _fold_glyphs(s: str) -> str:
    return str(s).translate(_HOMOGLYPHS).upper().strip()


def _canon_fleet_keys(d: dict, fleets: list[str]) -> tuple[dict,
                                                           list[str]]:
    """Ключи словаря по флоту → фактические типы набора (сравнение с
    нормализацией гомоглифов и регистра). Неузнанный ключ остаётся
    как есть и называется в заметках (не молчит)."""
    from unicode_data import confusable_hint
    by_norm = {_fold_glyphs(f): f for f in fleets}
    out, notes = {}, []
    for k, v in d.items():
        canon = by_norm.get(_fold_glyphs(k))
        if canon is None:
            out[str(k)] = v
            notes.append("ключ %r не найден среди типов набора — "
                         "оставлен как есть" % str(k)
                         + confusable_hint(str(k), fleets))
        else:
            if str(k) != canon:
                notes.append("ключ %r канонизирован к типу набора "
                             "%r (гомоглифы/регистр)" % (str(k),
                                                         canon))
            out[canon] = v
    return out, notes


def _sit_carriers(model: dict):
    """Носители стыковки по областям: (база, вне базы, без области).
    Словарное имя — min_turnaround (ревью, находка 12: прежнее
    'sit_min' не производит ни одна миграция — пробел был вечным)."""
    base, off, anyq = [], [], []
    for r, p in _floor_params(model, "min_turnaround"):
        q = str(p.get("qualifier") or "").lower()
        if "вне" in q:
            off.append((r, p))
        elif "баз" in q:
            base.append((r, p))
        else:
            anyq.append((r, p))
    return base, off, anyq


def build_conditions(model: dict, forms: dict | None, manifest: dict,
                     payload: dict, *,
                     period_semantics: str = "per_duty",
                     chain_days_cap: int | None = None) -> dict:
    """Словарь условий расчёта либо ConditionsExportError с перечнем.

    period_semantics — решение ПОСТАНОВКИ (трактовка предела: per_duty
    — вариант Б, фактический план авиакомпании; duty_period — строгая;
    вопрос интегратору открыт), приходит параметром, не выдумывается.
    chain_days_cap — граница поиска командировки в календарных днях;
    None — весь горизонт рейсов (верхняя граница без нормы)."""
    gaps: list[dict] = []
    A: dict = {}
    B: dict = {}

    # --- база мира ---------------------------------------------------
    bases = _base_of(payload)
    if not bases:
        raise ConditionsExportError([{
            "key": "base",
            "missing": "в данных нет ни одной базы приписки",
            "closure": "базы сотрудников (homeAirport) — данные "
                       "набора"}])
    # Многобазовый мир ЗАКОНЕН (постановка 2026-08-30, вопрос линии
    # нано): суды «в базе/вне базы» ведёт построитель по СВОЕМУ
    # множеству баз из данных, поле base формы — трассировка мира.
    # Одна база кладётся СТРОКОЙ (байт-паритет прежних файлов условий
    # однобазовых линий), несколько — отсортированным списком.
    base = bases[0] if len(bases) == 1 else list(bases)

    # --- состав экипажа ----------------------------------------------
    crew = (manifest.get("crew_size") or {})
    fleets = _fleets_of(payload)
    if isinstance(crew.get("value"), int) and fleets:
        src = "манифест пакета: crew_size=%d (%s)" % (
            crew["value"], crew.get("rule"))
        A["crew_min_by_fleet"] = {
            "values": {f: crew["value"] for f in fleets}, "source": src}
        A["crew_std_by_fleet"] = {
            "values": {f: crew["value"] for f in fleets}, "source": src}
    else:
        gaps.append({"key": "crew_min_by_fleet",
                     "missing": "нет машинного crew_size манифеста "
                                "либо флотов в данных",
                     "closure": "пакет версии 1.2+ с машинным полем"})

    # --- подготовка и разбор -----------------------------------------
    for cond_key, par in (("briefing_base_min", "briefing_min"),
                          ("briefing_offbase_min", "briefing_min"),
                          ("debrief_base_min", "debriefing_min"),
                          ("debrief_offbase_min", "debriefing_min")):
        hit = _strictest_min(model, par)
        if hit is None:
            gaps.append({
                "key": cond_key,
                "missing": "в модели нет машинного параметра %r "
                           "(нормы подготовки/разбора лежат прозой)"
                           % par,
                "closure": "миграция класса «норма прозой» "
                           "(значение в формулировке)"})
            continue
        r, p = hit
        A[cond_key] = {"value": int(p["value"]), "source": r.get("id")}

    # --- стыковка ----------------------------------------------------
    sit_base, sit_off, sit_any = _sit_carriers(model)
    def _mx(carriers):
        r, p = max(carriers, key=lambda t: t[1]["value"])
        return int(p["value"]), str(r.get("id"))
    if sit_off or sit_any or sit_base:
        if sit_off or sit_any:
            v, s = _mx(sit_off + sit_any)
            A["sit_offbase_min"] = {"value": v, "source": s}
        else:
            v, s = _mx(sit_base)
            A["sit_offbase_min"] = {
                "value": v,
                "source": s + " (наихудшая трактовка: внебазового "
                              "носителя нет, применён базовый)"}
        if sit_base or sit_any:
            v, s = _mx(sit_base + sit_any)
            B["b6_sit_base_min"] = {"value": v, "source": s}
        else:
            v, s = _mx(sit_off)
            B["b6_sit_base_min"] = {
                "value": v,
                "source": s + " (наихудшая трактовка: базового "
                              "носителя нет, применён внебазовый)"}
    else:
        gaps.append({
            "key": "sit_offbase_min",
            "missing": "в модели нет машинного параметра "
                       "'min_turnaround' (носители стыковки прозой "
                       "либо условные)",
            "closure": "миграция стыковки + вопрос авиакомпании при "
                       "конфликте значений"})

    # --- точная ярусная форма предела смены (СНАЧАЛА: её продлённые
    # --- пределы задают верх лестниц отдыха — ревью, находка 15) ----
    exact_gaps: list[dict] = []
    we: dict = {}
    mats = [m for m in (forms or {}).get("matrices") or []
            if m.get("threshold") == "max_duty_span_min"]
    matrix_top = 0
    if mats:
        def _bucket(s_from: int, s_to: int) -> str | None:
            if s_from <= 2 and s_to <= 2:
                return "1-2"
            if s_from == s_to and s_from in (3, 4):
                return str(s_from)
            if s_from >= 5:
                return "5+"
            return None
        periods: dict[tuple[int, int], dict] = {}
        ok = True
        for m in mats:
            for row in m.get("rows") or []:
                lo, hi = int(row["start_from_min"]), \
                    int(row["start_to_min"])
                if lo >= hi:
                    # Окно через полночь: инклюзивная проверка
                    # приёмника lo <= t <= hi его не выразит
                    # (ревью, находка 19) — честный отказ.
                    exact_gaps.append({
                        "key": "worktime_exact.acclimatized_by_period",
                        "missing": "окно матрицы через полночь "
                                   "(%d..%d) не выражается формой "
                                   "приёмника" % (lo, hi),
                        "closure": "разбор формы окна с решателем"})
                    ok = False
                    continue
                key = _bucket(int(row["sectors_from"]),
                              int(row["sectors_to"]))
                if key is None:
                    ok = False
                    continue
                matrix_top = max(matrix_top, int(row["value_min"]))
                tbl = periods.setdefault((lo, hi), {})
                v_h = int(row["value_min"]) / 60.0
                tbl[key] = min(tbl.get(key, v_h), v_h)
        if ok and periods:
            we["acclimatized_by_period"] = [
                {"from": "%02d:%02d" % divmod(lo, 60),
                 "to": "%02d:%02d" % divmod(hi - 1, 60),
                 "by_landings": tbl}
                for (lo, hi), tbl in sorted(periods.items())]
        elif ok:
            exact_gaps.append({
                "key": "worktime_exact.acclimatized_by_period",
                "missing": "матрица пуста",
                "closure": "формы порогов ревизии 1.10"})
        elif not any(g["key"] == "worktime_exact.acclimatized_by_period"
                     for g in exact_gaps):
            exact_gaps.append({
                "key": "worktime_exact.acclimatized_by_period",
                "missing": "колонки матрицы не ложатся в корзины "
                           "посадок приёмника",
                "closure": "разбор калибровки матрицы"})
    else:
        exact_gaps.append({
            "key": "worktime_exact.acclimatized_by_period",
            "missing": "в пакете нет машинной матрицы пределов смены",
            "closure": "формы порогов ревизии 1.10"})
    # Таблицы прил. 4/6 и пороги акклиматизации — из ПРИМЕНЁННЫХ
    # ответов авиакомпании на вопросы условий (дословный текст в
    # модели, строгий разбор формы вопроса).
    raw4, rev4 = _answer_raw(model, _Q_APP4)
    app4 = _parse_app4(raw4) if raw4 else None
    if app4:
        we["unknown_by_landings"] = app4
        we.setdefault("_sources", {})["unknown_by_landings"] = \
            "ответ авиакомпании %s (ревизия %s)" % (_Q_APP4, rev4)
    else:
        exact_gaps.append({
            "key": "worktime_exact.unknown_by_landings",
            "missing": "нет применённого разбираемого ответа на "
                       "вопрос прил. 4 (%s)" % _Q_APP4,
            "closure": "ответ анкеты по форме вопроса"})
    raw6, rev6 = _answer_raw(model, _Q_APP6)
    app6 = _parse_app6(raw6) if raw6 else None
    if app6:
        classes, ext = app6
        canon, glyph_notes = _canon_fleet_keys(
            {str(k): v for k, v in classes.items()}, fleets)
        we["rest_class_by_fleet"] = canon
        we["extended_by_class_h"] = {str(k): v
                                     for k, v in ext.items()}
        src6 = "ответ авиакомпании %s (ревизия %s)" % (_Q_APP6, rev6)
        if glyph_notes:
            src6 += "; " + "; ".join(glyph_notes)
        we.setdefault("_sources", {})["прил6"] = src6
    else:
        for key in ("extended_by_class_h", "rest_class_by_fleet"):
            exact_gaps.append({
                "key": "worktime_exact." + key,
                "missing": "нет применённого разбираемого ответа на "
                           "вопрос прил. 6 (%s)" % _Q_APP6,
                "closure": "ответ анкеты по форме вопроса"})
    # Лестница продления по числу дополнительных членов экипажа —
    # машинная форма пакета (временная форма по документам, вердикт
    # выпуска ВМП 2026-08-28: ответ анкеты ревизии 9 противоречит
    # документам авиакомпании и до пересмотра не применяется).
    # Валидная лестница ЗАМЕЩАЕТ плоский продлённый предел прил. 6:
    # её верхняя ступень и есть предел наибольшего состава.
    lad = (forms or {}).get("extended_ladder")
    if lad is not None:
        lad_gaps: list[dict] = []
        if str(lad.get("unit")) != "minutes":
            lad_gaps.append({
                "key": "worktime_exact.extended_ladder",
                "missing": "единица лестницы %r, форма несёт минуты"
                           % lad.get("unit"),
                "closure": "форма порогов с unit='minutes'"})
        steps_out: list[dict] = []
        srcs: list[str] = []
        prev_crew, prev_tbl = 0, None
        for s in lad.get("steps") or []:
            crew, tbl, rule = s.get("extra_crew"), \
                s.get("by_class_min") or {}, s.get("rule")
            if not isinstance(crew, int) or isinstance(crew, bool) \
                    or crew <= prev_crew or not tbl or not rule \
                    or not all(isinstance(v, int)
                               and not isinstance(v, bool)
                               for v in tbl.values()):
                lad_gaps.append({
                    "key": "worktime_exact.extended_ladder",
                    "missing": "ступень лестницы неполна либо ступени "
                               "не строго возрастают по составу "
                               "(extra_crew=%r)" % (crew,),
                    "closure": "форма порогов: целые минуты по классам "
                               "+ правило-носитель каждой ступени"})
                break
            ntbl = {str(k): int(v) for k, v in tbl.items()}
            if prev_tbl is not None and any(
                    k in prev_tbl and v < prev_tbl[k]
                    for k, v in ntbl.items()):
                lad_gaps.append({
                    "key": "worktime_exact.extended_ladder",
                    "missing": "ступень extra_crew=%d мягче предыдущей "
                               "по классу — лестница немонотонна"
                               % crew,
                    "closure": "вопрос авиакомпании (противоречие "
                               "носителей ступеней)"})
                break
            steps_out.append({"extra_crew": crew, "by_class_min": ntbl})
            srcs.append(str(rule))
            prev_crew, prev_tbl = crew, ntbl
        if not steps_out and not lad_gaps:
            lad_gaps.append({
                "key": "worktime_exact.extended_ladder",
                "missing": "лестница без ступеней",
                "closure": "форма порогов со ступенями состава"})
        bonus_out = None
        b = lad.get("long_sector_bonus")
        if b is not None and not lad_gaps:
            cond = b.get("condition") or {}
            if cond.get("kind") != "one_sector_over_h" \
                    or not isinstance(b.get("add_min"), int) \
                    or not isinstance(cond.get("value"), (int, float)) \
                    or not isinstance(cond.get("max_sectors"), int) \
                    or not b.get("rule"):
                lad_gaps.append({
                    "key": "worktime_exact.extended_ladder",
                    "missing": "надбавка длинного сектора не по форме "
                               "(kind=%r)" % cond.get("kind"),
                    "closure": "форма порогов: one_sector_over_h + "
                               "add_min + max_sectors + правило"})
            else:
                bonus_out = {"add_min": int(b["add_min"]),
                             "condition": {
                                 "kind": "one_sector_over_h",
                                 "value": cond["value"],
                                 "max_sectors": int(cond["max_sectors"])}}
                srcs.append(str(b["rule"]))
        if lad_gaps:
            exact_gaps.extend(lad_gaps)
        else:
            we["extended_ladder"] = {"steps": steps_out}
            if bonus_out is not None:
                we["extended_ladder"]["long_sector_bonus"] = bonus_out
            top = steps_out[-1]["by_class_min"]
            we["extended_by_class_h"] = {k: v / 60.0
                                         for k, v in top.items()}
            we.setdefault("_sources", {})["extended_ladder"] = \
                "машинная форма пакета: " + ", ".join(srcs)
            # Плоский предел прил. 6 замещён верхней ступенью — его
            # пробел (если ответа прил. 6 нет) закрыт лестницей.
            exact_gaps = [g for g in exact_gaps if g["key"]
                          != "worktime_exact.extended_by_class_h"]
    rawA, revA = _answer_raw(model, _Q_ACCLIM)
    accl = _parse_acclim(rawA) if rawA else None
    if accl:
        we["acclimatization"] = accl
        we.setdefault("_sources", {})["acclimatization"] = \
            "ответ авиакомпании %s (ревизия %s)" % (_Q_ACCLIM, revA)
    else:
        exact_gaps.append({
            "key": "worktime_exact.acclimatization",
            "missing": "нет применённого разбираемого ответа на "
                       "вопрос порогов акклиматизации (%s)"
                       % _Q_ACCLIM,
            "closure": "ответ анкеты по форме вопроса"})
    # Точная форма собирается только ЦЕЛИКОМ: приёмник требует все
    # подполя, а «пустое» значение любого из них — не нейтрализатор,
    # а другое правило (запрет всех смен либо молчаливое ослабление).
    exact_ready = not exact_gaps
    ext_top = 0
    if exact_ready:
        we["period_semantics"] = period_semantics
        A["worktime_exact"] = {"values": we,
                               "source": "машинные формы пакета"}
        ext_top = int(max((float(v) * 60 for v in
                           (we.get("extended_by_class_h") or {})
                           .values()), default=0))

    # --- лестницы отдыха ---------------------------------------------
    # Машинные полы + семантика формулировки «не менее предыдущей
    # смены» дают точную табличную форму max(смена, пол). Верх —
    # наибольший предел смены: матрица И продлённые пределы (за
    # последней ступенью _pick приёмника отдаёт последнюю строку —
    # лестница короче максимальной смены ослабляла бы отдых).
    top = max(matrix_top, ext_top)
    prev_re = re.compile(r"предыдущ|предшеств", re.IGNORECASE)
    # Условные полы («после продления — 14 ч», «при разнице поясов —
    # 16 ч») — ветвления, не общий пол: живой прогон run-0060 выбрал
    # бы 840 мин пол базы из правила о продлении.
    cond_re = re.compile(r"продлен|\bпри\b|\bесли\b|в случае",
                         re.IGNORECASE)
    all_floors = (forms or {}).get("conditional_floors") or []
    tz_floors_off = [f for f in all_floors
                     if (f.get("condition") or {}).get("kind")
                     == "tz_diff_hours"
                     and f.get("threshold")
                     == "min_rest_outstation_min"]
    tz_floors_home = [f for f in all_floors
                      if (f.get("condition") or {}).get("kind")
                      == "tz_diff_hours"
                      and f.get("threshold") == "min_rest_home_min"]
    for cond_key, par, tzf in (
            ("rest_base_by_work_h", "min_rest_home", tz_floors_home),
            ("rest_offbase_by_work_h", "min_rest_outstation",
             tz_floors_off)):
        carriers = [(r, p) for r, p in _floor_params(model, par)
                    if prev_re.search(str(r.get("statement") or ""))
                    and not cond_re.search(
                        str(r.get("statement") or ""))]
        if not carriers or not top:
            gaps.append({
                "key": cond_key,
                "missing": "нет машинного пола %r с формулировкой "
                           "«не менее предыдущей смены» либо матрицы "
                           "пределов смены (верх лестницы)" % par,
                "closure": "миграция класса «норма прозой»"})
            continue
        r, p = max(carriers, key=lambda t: t[1]["value"])
        lad = _ladder(int(p["value"]), top,
                      "%s: max(предыдущая смена, %d мин) ступенями "
                      "получаса" % (r.get("id"), int(p["value"])))
        # Дальняя колонка обязательна приёмнику всегда: при условном
        # поле СВОЕГО порога — своя лестница, иначе значения обычной
        # колонки («нет ужесточения»; чужой порог не применяется —
        # ревью, находка 18).
        if tzf:
            tf = max(int(f["value_min"]) for f in tzf)
            lad["transmeridian"] = _ladder(
                tf, top, "условный пол машинных форм")["values"]
        else:
            lad["transmeridian"] = [dict(x) for x in lad["values"]]
        A[cond_key] = lad
    # Запасная ветвь приёмника для базы: смены до 12 часов судятся
    # ПЛОСКИМ значением b12 (12 часов зашиты в приёмнике). Машинный
    # пол ровно 720 мин — формы совпадают; иной пол несовместим с
    # ветвью — честный пробел (ревью, находка 16). Порог «смена до»
    # едет в данные ключом work_upto_h (пакет «умолчания-в-данные»
    # 2026-08-29): носитель — тот же машинный пол лестницы базы;
    # нынешний приёмник лишний ключ не читает, приёмник этапа 3
    # начнёт его требовать.
    if "rest_base_by_work_h" in A:
        floor_h = A["rest_base_by_work_h"]["values"][0]["rest_h"]
        if floor_h == 12.0:
            B["b12_rest_base_upto_12h"] = {
                "values": {"work_upto_h": floor_h,
                           "rest_h_if_ambiguous": 12},
                "source": A["rest_base_by_work_h"]["source"]}
        else:
            gaps.append({
                "key": "b12_rest_base_upto_12h",
                "missing": "машинный пол отдыха в базе %.1f ч, а "
                           "запасная ветвь приёмника зашита на 12 ч "
                           "— формы несовместимы" % floor_h,
                "closure": "разбор формы b12 с решателем"})

    # --- граница командировки ----------------------------------------
    from datetime import date
    days = []
    for f in payload.get("flights") or []:
        for k in ("departureUTCDateTime", "arrivalUTCDateTime"):
            v = f.get(k)
            if isinstance(v, str) and len(v) >= 10:
                try:
                    days.append(date.fromisoformat(v[:10]))
                except ValueError:
                    pass
    horizon = (max(days) - min(days)).days + 1 if days else None
    raw_sp, rev_sp = _answer_raw(model, _Q_SPAN)
    span_val = _parse_span(raw_sp) if raw_sp else None
    if span_val:
        # Предел размаха командировки — ответ авиакомпании: минутная
        # форма (непрерывные сутки С ПЕРЕГОНАМИ, от первого вылета до
        # последнего прилёта) + календарные дни как оболочка границы
        # поиска.
        A["max_pairing_span_min"] = {
            "value": span_val,
            "source": "ответ авиакомпании %s (ревизия %s)"
                      % (_Q_SPAN, rev_sp)}
        A["chain_max_days"] = {
            "value": (span_val - 1) // 1440 + 2,
            "source": "календарная оболочка минутного предела "
                      "размаха (%d мин)" % span_val}
    elif chain_days_cap is not None:
        A["chain_max_days"] = {
            "value": int(chain_days_cap),
            "source": "граница поиска постановки (параметр кнопки)"}
    elif horizon:
        A["chain_max_days"] = {
            "value": horizon,
            "source": "весь горизонт рейсов — безнормовая верхняя "
                      "граница (нормативного предела в своде нет)"}
    else:
        gaps.append({"key": "chain_max_days",
                     "missing": "горизонт рейсов не вычисляется",
                     "closure": "данные с датами рейсов"})

    # --- стандартная ночь --------------------------------------------
    ns = _params(model, "night_duty_start",
                 unit="time_of_day_minutes", relation=None)
    ne = _params(model, "night_duty_end",
                 unit="time_of_day_minutes", relation=None)
    if ns and ne:
        s_vals = {int(p["value"]) for _r, p in ns}
        e_vals = {int(p["value"]) for _r, p in ne}
        if len(s_vals) == 1 and len(e_vals) == 1:
            sm, em = s_vals.pop(), e_vals.pop()
            A["standard_night"] = {
                "values": {"start": "%02d:%02d" % divmod(sm, 60),
                           "end": "%02d:%02d" % divmod(em, 60),
                           # «любая часть в окне» — квантор
                           # формулировки носителей, не число:
                           # минимально возможная доля.
                           "share": 1e-9},
                "source": ", ".join(sorted({str(r.get("id"))
                                            for r, _p in ns + ne}))}
        else:
            gaps.append({"key": "standard_night",
                         "missing": "носители ночного окна расходятся "
                                    "числами: start %s, end %s"
                                    % (sorted(s_vals), sorted(e_vals)),
                         "closure": "вопрос авиакомпании"})
    else:
        gaps.append({"key": "standard_night",
                     "missing": "нет машинных night_duty_start/end",
                     "closure": "миграция класса «норма прозой»"})

    # --- интервал между соседними перегонами --------------------------
    # Живой урок сверки с эталоном (2026-08-27, вечер): без этого
    # ключа построитель добрал обязательство парой перегонов со
    # стыковкой 60 минут — нарушение правила «между соседними
    # перелётами пассажиром не менее 2 часов». Носитель — правило о
    # стыковке МЕЖДУ перегонами (DH-контекст, min_turnaround);
    # мягкий вид носителя применяется жёстко по эталонной постановке
    # (правило исполняет расчёт, проверяет приёмка).
    dh_sit = []
    dh_re = re.compile(r"\bdh\b|перегон|пассажир", re.IGNORECASE)
    for r in _active_rules(model):
        if r.get("kind") not in ("HARD", "DATA", "DEF", "SOFT"):
            continue
        if not dh_re.search(str(r.get("statement") or "")):
            continue
        for p in (r.get("params") or []):
            if p.get("name") == "min_turnaround" \
                    and p.get("unit") == "duration_minutes" \
                    and p.get("relation") == "min" \
                    and isinstance(p.get("value"), int):
                dh_sit.append((r, p))
    if dh_sit:
        r, p = max(dh_sit, key=lambda t: t[1]["value"])
        A["adjacent_deadhead_min"] = {
            "value": int(p["value"]),
            "source": "%s%s" % (r.get("id"),
                                " (мягкий носитель применён жёстко: "
                                "правило исполняет расчёт)"
                                if r.get("kind") == "SOFT" else "")}

    # --- типы ВС вне автоматического планирования ---------------
    # Носитель перечня — правило вида DATA («…типов 'Q7|Q9|…'
    # маркируются как deadhead» — идентификаторы правил-носителей в
    # машинной форме пакета). КОНСЕНСУС выпуска ВМП 2026-08-28
    # (срочный пункт о сотнях рейсов, решение ОБ-1): инструкция источника
    # противоречит фактической разметке данных той же мастер-системы —
    # СПЛОШНОЕ применение перечня по типам ЗАПРЕЩЕНО до ответа анкеты
    # о границе исключения; носитель истины — поле «Перегон» книги данных
    # (транспортные метки набора). Поэтому ключ исключения собирается
    # ТОЛЬКО когда машинная форма пакета (fleet_dh_marked) явным
    # значением semantics разрешает сплошное применение; описательная
    # семантика (master_data_marking_description) и отсутствие формы —
    # ключа нет, правило исполняют данные. Живой урок: кнопка №26
    # остановилась на половине обязательств — сплошной ключ,
    # снятый в CPLEX-конвейере вручную, жил в кнопочном пути.
    fdm = (forms or {}).get("fleet_dh_marked") or {}
    if str(fdm.get("semantics") or "") == "apply_by_type":
        excl_types = [str(t).strip() for t in fdm.get("types") or []
                      if str(t).strip()]
        excl_rules = [str(fdm.get("rule") or "")]
    else:
        excl_types, excl_rules = [], []
    if excl_types:
        all_types = sorted({str(f.get("aircraftType") or "").strip()
                            for f in payload.get("flights") or []
                            if f.get("aircraftType")})
        canon, excl_notes = _canon_fleet_keys(
            {t: True for t in excl_types}, all_types)
        src = ", ".join(excl_rules)
        if excl_notes:
            src += "; " + "; ".join(excl_notes)
        A["fleet_auto_plan_excluded"] = {
            "types": sorted(canon), "source": src}

    # --- ранние вылеты из базы (пороги UTC в формулировках) -----------
    # Принцип шаблона (владелец, 2026-08-29): механика разбора — в
    # коде, ДЕТЕКТОРНЫЕ ФОРМУЛИРОВКИ правил — файлом-носителем контура
    # данных (vmp-condition-detectors.json, конвенция 2026-08-29:
    # нет файла = детекторы спят, кривой = честный отказ). Порог без
    # разбираемого времени — честный пробел; правил нет — ключей нет.
    # Страж молчаливого ослабления: правило с порогом «до HH:MM UTC»
    # при спящих детекторах — громкий пробел, а не тишина.
    utc_re = re.compile(r"до\s+(\d{1,2}):(\d{2})\s*UTC", re.IGNORECASE)
    detectors = _load_condition_detectors()
    if detectors is None:
        for r in _active_rules(model):
            if r.get("kind") not in _ACTIVE_KINDS:
                continue
            if utc_re.search(str(r.get("statement") or "")):
                gaps.append({
                    "key": "condition_detectors",
                    "missing": "правило %s несёт порог «до HH:MM "
                               "UTC», а носитель детекторов контура "
                               "(vmp-condition-detectors.json) не "
                               "задан — пороги ранних вылетов не "
                               "собраны" % r.get("id"),
                    "closure": "выпустить носитель детекторов "
                               "контура (конвенция 2026-08-29)"})
    for key, det in (detectors or ()):
        hits = []
        for r in _active_rules(model):
            if r.get("kind") not in _ACTIVE_KINDS:
                continue
            st = str(r.get("statement") or "")
            if not det.search(st):
                continue
            m = utc_re.search(st)
            if not m:
                gaps.append({
                    "key": key,
                    "missing": "правило %s ограничивает ранние вылеты "
                               "из базы, но порог «до HH:MM UTC» в "
                               "формулировке не разбирается"
                               % r.get("id"),
                    "closure": "машинный порог в правиле (вопрос "
                               "постановки)"})
                continue
            hits.append((int(m.group(1)) * 60 + int(m.group(2)),
                         str(r.get("id"))))
        if hits:
            # Строже — больший порог (шире окно запрета).
            A[key] = {"value": max(h[0] for h in hits),
                      "source": ", ".join(sorted(h[1] for h in hits))}

    # --- дальняя стоянка и прочие машинные ключи ----------------------
    tz_all = [f for f in all_floors
              if (f.get("condition") or {}).get("kind")
              == "tz_diff_hours"]
    if tz_all:
        A["trans_tz_from_h"] = {
            "value": min(int(f["condition"]["value"]) for f in tz_all),
            "source": ", ".join(sorted({str(f.get("rule"))
                                        for f in tz_all}))}
    B["b13_transmeridian_unknown"] = {
        "value": True,
        "source": "станция без пояса — наихудшая трактовка"}

    # --- нейтрализаторы отсутствующих у авиакомпании правил ----------
    # (та же дисциплина, что у контракта Г-П5: незаданный ключ формы —
    # чужая калибровка, гасится явным недостижимым значением со следом)
    A["estafeta_rest_bonus_min"] = {
        "value": 0, "source": "нейтрализатор: правил эстафеты в "
                              "своде нет — добавки нет"}
    A["deadhead_credit_to_work_pct"] = {
        "values": {"without_rest_12h_before": 100},
        "source": "наихудшая трактовка: перегон пассажиром целиком "
                  "считается рабочим временем (машинного правила "
                  "иного зачёта в своде нет)"}
    A["c_class_attendant_required"] = {
        "c_class_required_from_h": 999,
        "source": "нейтрализатор: кабинная норма, контингента "
                  "расчёта не касается"}
    B["b7_lowcost_vs_charter"] = {
        # Выключение держится на недостижимой ДЛИНЕ номера (99 цифр):
        # приёмник сравнивает длину цифр с charter_digits, а префикс
        # проверяется по цифровой строке и вакуумен (ревью, 20) —
        # 7-значные номера реальны, «§» не спасал.
        "values": {"lowcost_prefix": "§", "charter_digits": 99},
        "source": "нейтрализатор: правил чартер/лоукостер в своде "
                  "нет — признак выключен недостижимой длиной номера"}
    B["b8_airport_group"] = {
        "values": {"c_class_required_from_h": 999},
        "source": "нейтрализатор (запасная форма прежних файлов)"}

    # --- запасная плоская таблица (b3) -------------------------------
    # Обязательна приёмнику всегда; при живой worktime_exact в суде не
    # участвует. Наихудшая строка машинной матрицы — по корзинам.
    if mats:
        worst: dict[str, float] = {}
        for m in mats:
            for row in m.get("rows") or []:
                sf, st = int(row["sectors_from"]), \
                    int(row["sectors_to"])
                if sf <= 2 and st <= 2:
                    key = "1-2"
                elif sf == st and sf in (3, 4):
                    key = str(sf)
                elif sf >= 5:
                    key = "5+"
                else:
                    continue
                v_h = int(row["value_min"]) / 60.0
                worst[key] = min(worst.get(key, v_h), v_h)
        if set(worst) >= {"1-2", "3", "4", "5+"}:
            B["b3_worktime_table"] = {
                "day_by_landings_h": dict(worst),
                "night_by_landings_h": dict(worst),
                "source": "наихудшая строка машинной матрицы пределов "
                          "(запасной путь; при worktime_exact в суде "
                          "не участвует)"}
    if "b3_worktime_table" not in B:
        gaps.append({"key": "b3_worktime_table",
                     "missing": "запасная плоская таблица не "
                                "собирается из матрицы форм",
                     "closure": "формы порогов ревизии 1.10"})

    if gaps or not exact_ready:
        raise ConditionsExportError(gaps + exact_gaps)

    return {"base": base,
            "meta": {"generator": "cdpm.conditions_export",
                     "period_semantics": period_semantics},
            "A": A, "B": B}
