# -*- coding: utf-8 -*-
"""Проверка постановки: даёт ли ВМП корректную ЗАДАЧУ пейринга и
ростеринга (половина COSMOS по разделению функций, решение владельца
2026-09-03, final-report/RAZDELENIE-CDPM-COSMOS-CLD.md, раздел 4).

Граница: проверке не нужны исходные документы авиакомпании — она
смотрит на ВМП как принятый пакет (форма, модель, данные набора) и
отвечает на один вопрос: строится ли из этой постановки осмысленная
задача. Соответствие ВМП документам — зона CDPM, здесь не судится.

Классы (каждый закреплён ступенью стенда open-bench/vmp-stand/):

  contradiction      противоречие жёстких: одна величина, разные числа
                     в двух жёстких строках — пара названа поимённо;
                     тот же класс между правилами разных видов —
                     через webapp.threshold_gate.kind_conflicts;
  code_not_in_data   код базы / типа судна из ВМП отсутствует в
                     данных набора;
  criterion_no_metric  критерий без метрики и без машинного вида, либо
                     с метрикой вне словаря или не для этой задачи,
                     либо без направления;
  criterion_no_rate  ставка не задана (ни value, ни rates); машинный
                     критерий без версии и даты действия ставок;
  violation_unit     единица размера нарушения мягкого правила не
                     объявлена, вне словаря или не равна единице
                     семьи; коэффициент отсутствует;
  tiers_disordered   ярусы критериев части: два критерия на одном
                     ярусе, пропуск номера, нет первого яруса, ярус
                     старше первого без допуска;
  degenerate         вырожденность (предупреждение): все коэффициенты
                     нулевые; правило с нулевым коэффициентом; порог
                     длительности, который длиннее горизонта набора и
                     потому ни на что не влияет;
  no_form            формы ВМП в пакете нет — постановку судить нечем.

Два уровня: «stop» — постановка негодна, расчёт не начинается;
«warn» — предупреждение, расчёт возможен. Вердикт: «accepted» (ни
одного stop) либо «rejected». Ничего не чинится: каждая находка
называет строку (id) и поле. Языковые модели не вызываются.

Словари (метрики, единицы нарушения, единицы семей) берутся из тех
модулей, что уже держат их для приёмника; копии здесь нет.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

STOP = "stop"
WARN = "warn"

CONTRADICTION = "contradiction"
CODE_NOT_IN_DATA = "code_not_in_data"
CRITERION_NO_METRIC = "criterion_no_metric"
CRITERION_NO_RATE = "criterion_no_rate"
VIOLATION_UNIT = "violation_unit"
TIERS_DISORDERED = "tiers_disordered"
DEGENERATE = "degenerate"
NO_FORM = "no_form"

CLASSES = (CONTRADICTION, CODE_NOT_IN_DATA, CRITERION_NO_METRIC,
           CRITERION_NO_RATE, VIOLATION_UNIT, TIERS_DISORDERED,
           DEGENERATE, NO_FORM)

ACCEPTED = "accepted"
REJECTED = "rejected"

PARTS = ("pairing", "rostering")
HARD_KINDS = ("HARD", "CASE")

#: Признаки определения-перечня баз и определения-перечня типов судов —
#: по словам заголовка и формулировки (жанр деловых документов, а не
#: конкретный набор). Структурное поле перечня кодов (заявка CDPM:
#: machine.codes) читается раньше слов.
#: Слова — ЦЕЛЫЕ (границы слова): подстрока «вс» ловила «Второй» и
#: «всех», и определение должностей судилось как перечень типов судов
#: (расширенный H0, 2026-09-03).
_BASE_WORDS_RE = re.compile(
    r"\b(баз[аыеу]|базы|базах|приписк\w*|base|bases|home\s*base)\b", re.I)
_FLEET_WORDS_RE = re.compile(
    r"\b(тип\w*|судн\w*|вс|флот\w*|парк\w*|fleet|aircraft\w*)\b", re.I)
# Перечень ДОЛЖНОСТЕЙ — не базы и не типы судов, хотя расшифровка «КВС
# (командир воздушного судна)» несёт слово «судна» (живой случай
# 06.09.2026, CLD0011-KVS: определение «Должности пилотов» судилось
# перечнем типов судов, «КВС» искался в колонке «Тип ВС»).
_POSITION_WORDS_RE = re.compile(
    r"\b(должност\w*|позици\w*|рол[ьи]|квалификаци\w*|position\w*|"
    r"role\w*|rank\w*)\b", re.I)
_AIRPORT_CODE = re.compile(r"(?<![A-Z0-9])[A-Z]{3,4}(?![A-Z0-9])")
_TYPE_CODE = re.compile(
    r"(?<![\wА-Яа-я-])[A-ZА-Я]{1,3}-?\d{2,4}[A-Z]?(?:-\d{1,4})?(?![\w-])")

_DURATION_UNITS = ("duration_minutes",)


def finding(cls: str, level: str, row_id, field: str, message: str,
            part: str | None = None, **extra) -> dict:
    out = {"class": cls, "level": level, "id": row_id, "field": field,
           "part": part, "message": message}
    out.update(extra)
    return out


# ------------------------------------------------------------ данные

def _rows(clean_data: dict | None, sheet: str) -> list[dict]:
    out: list[dict] = []
    for sheets in ((clean_data or {}).get("tables") or {}).values():
        block = (sheets or {}).get(sheet)
        if isinstance(block, dict):
            out.extend(r for r in (block.get("rows") or [])
                       if isinstance(r, dict))
    return out


def _columns(clean_data: dict | None, sheet: str) -> list[str]:
    for sheets in ((clean_data or {}).get("tables") or {}).values():
        block = (sheets or {}).get(sheet)
        if isinstance(block, dict):
            return list(block.get("columns") or [])
    return []


def data_facts(clean_data: dict | None) -> dict:
    """Что данные набора говорят сами о себе: базы, аэропорты, типы
    судов, горизонт в минутах, число сотрудников. Конвенции листов —
    те же закрытые имена, что у конвертера приёмника (webapp.
    task_package): «Рейсы», «Экипажи», «Свойства»."""
    if not isinstance(clean_data, dict):
        return {"present": False}
    bases: set[str] = set()
    airports: set[str] = set()
    types: set[str] = set()
    dates: list[datetime] = []
    crew = 0
    for r in _rows(clean_data, "Экипажи"):
        crew += 1
        b = str(r.get("База") or "").strip()
        if b:
            bases.add(b)
            airports.add(b)
    has_type_col = "Тип ВС" in _columns(clean_data, "Рейсы")
    for r in _rows(clean_data, "Рейсы"):
        for key in ("Вылет", "Прилёт"):
            c = str(r.get(key) or "").strip()
            if c:
                airports.add(c)
        t = str(r.get("Тип ВС") or "").strip()
        if t:
            types.add(t)
        d = str(r.get("Дата") or "").strip()[:10]
        try:
            dates.append(datetime.strptime(d, "%Y-%m-%d"))
        except ValueError:
            pass
    horizon_min = None
    if dates:
        horizon_min = int((max(dates) - min(dates)).total_seconds()
                          // 60) + 24 * 60
    return {"present": True, "bases": bases, "airports": airports,
            "aircraft_types": types, "has_type_column": has_type_col,
            "horizon_min": horizon_min, "crew_count": crew}


# ------------------------------------------------------------ форма

def _part_rows(form: dict, part: str, section: str) -> list[dict]:
    return [r for r in (((form.get(part) or {}).get(section)) or [])
            if isinstance(r, dict)]


def _world_rows(form: dict) -> list[dict]:
    return [r for r in (((form.get("common") or {}).get("world")) or [])
            if isinstance(r, dict)]


def _definitions(form: dict) -> list[dict]:
    return [r for r in (((form.get("common") or {}).get("definitions"))
                        or []) if isinstance(r, dict)]


def _excluded(row: dict) -> bool:
    mark = row.get("excluded_until_data")
    return isinstance(mark, dict) and bool(mark.get("question"))


# ------------------------------------------------------------ классы

def _projected_params(rule: dict, findings: list[dict], check_class: str) -> list[dict]:
    """An unreadable canonical carrier is a stop, not an empty check."""
    from webapp.threshold_gate import ThresholdGateError, projected_params

    try:
        return projected_params(rule, numeric_only=True)
    except ThresholdGateError as exc:
        findings.append(finding(check_class, STOP, rule.get("id"),
                                "template_fit.params", str(exc)))
        return []


def check_contradiction(model: dict, pkg: dict | None = None) -> list[dict]:
    """Одна величина (имя параметра + уточнитель) с разными числами в
    двух жёстких строках. Судится по модели: там у параметра есть
    уточнитель (ревизия 1.3), форма его не несёт. Правила, исключённые
    до получения данных, и снятые (OUT) не участвуют. Тот же класс
    между правилами РАЗНЫХ видов ловит kind_conflicts приёмника — он
    вызывается, не переписывается."""
    from vmp.registry_check import is_excluded_until_data
    from webapp.threshold_gate import ThresholdGateError, conflict_qualifier
    seen: dict[tuple, dict[float, list[str]]] = {}
    out: list[dict] = []
    for rule in (model.get("rules") or []):
        if not isinstance(rule, dict):
            continue
        if rule.get("kind") not in HARD_KINDS or is_excluded_until_data(rule):
            continue
        for p in _projected_params(rule, out, CONTRADICTION):
            if not isinstance(p, dict):
                continue
            v = p.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            try:
                q = conflict_qualifier(p)
            except ThresholdGateError as exc:
                out.append(finding(CONTRADICTION, STOP, rule.get("id"),
                                   "params." + str(p.get("name")), str(exc)))
                continue
            key = (str(p.get("name")), q, str(p.get("relation") or ""))
            seen.setdefault(key, {}).setdefault(float(v), []).append(
                str(rule.get("id")))
    for (name, q, rel), by_value in sorted(seen.items()):
        if len(by_value) < 2:
            continue
        # Порядок появления в модели: первой названа строка, стоящая в
        # ВМП раньше, — адрес находки не зависит от чисел.
        pairs = list(by_value.items())
        first_v, first_ids = pairs[0]
        for v, ids in pairs[1:]:
            out.append(finding(
                CONTRADICTION, STOP, first_ids[0], "params." + name,
                "постановка противоречива: строка %s против строки %s — "
                "величина %s%s задана числами %s и %s"
                % (first_ids[0], ids[0], name,
                   " (%s)" % q if q else "", _num(first_v), _num(v)),
                rules=[first_ids[0], ids[0]], values=[first_v, v]))
    if pkg is not None:
        from webapp.threshold_gate import ThresholdGateError, kind_conflicts
        try:
            for c in kind_conflicts(pkg):
                rules = list(c.get("rules") or [])
                out.append(finding(
                    CONTRADICTION, STOP, rules[0] if rules else None,
                    "params." + str(c.get("name")),
                    "постановка противоречива: величина %s%s задана "
                    "разными числами %s в правилах разных видов: %s"
                    % (c.get("name"),
                       " (%s)" % c["qualifier"] if c.get("qualifier") else "",
                       ", ".join(_num(x) for x in c.get("values") or []),
                       "; ".join("%s: %s" % (k, ", ".join(v))
                                 for k, v in (c.get("by_kind") or {}).items())),
                    rules=rules, values=list(c.get("values") or [])))
        except ThresholdGateError as e:
            out.append(finding(CONTRADICTION, STOP, None, "template_fit.params",
                               "сравнение по видам не выполнено: %s" % e))
        except Exception as e:                            # noqa: BLE001
            out.append(finding(CONTRADICTION, WARN, None, "params",
                               "сравнение по видам не выполнено: %s" % e))
    return out


def _kind_of(text: str) -> str | None:
    """Класс предмета текста — «base», «aircraft_type» или «position» —
    по слову, встреченному РАНЬШЕ всех; None — ни одного слова."""
    best: tuple[int, str] | None = None
    for kind, rx in (("base", _BASE_WORDS_RE),
                     ("aircraft_type", _FLEET_WORDS_RE),
                     ("position", _POSITION_WORDS_RE)):
        m = rx.search(text or "")
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind)
    return best[1] if best else None


def _codes_of_definition(row: dict) -> tuple[str, set[str]] | None:
    """(род перечня, коды) определения-перечня либо None. Род — «base»
    или «aircraft_type» по словам заголовка/формулировки; коды — из
    machine.codes (структурное поле), иначе по форме записи кода."""
    # Род — по ПРЕДМЕТУ определения: заголовок решает первым; без
    # заголовка — часть формулировки до двоеточия (что определяется),
    # иначе вся формулировка. Внутри текста побеждает класс слова,
    # встреченного РАНЬШЕ: «Должности пилотов: КВС (командир воздушного
    # судна)» — должности (слово «судна» в расшифровке не делает
    # перечень типами судов); «Типы судов для квалификации пилотов:
    # B737» — типы судов (слово «квалификации» не делает перечень
    # должностями). Уточнение GPT CLD0011-KVS-GUARD-20260906-002:
    # прежний guard по одному слову «должност*/квалификац*» глушил
    # настоящие определения флота.
    kind = _kind_of(row.get("title") or "")
    if kind is None:
        stmt_text = str(row.get("statement") or "")
        head = stmt_text.split(":", 1)[0] if ":" in stmt_text else stmt_text
        kind = _kind_of(head) or _kind_of(stmt_text)
    if kind in (None, "position"):
        return None
    machine = row.get("machine") if isinstance(row.get("machine"), dict) else {}
    codes = machine.get("codes")
    if isinstance(codes, list) and codes:
        # Решение CDPM 03.09: элемент — объект {"code", "quote"} (код и
        # дословная цитата клаузы); строка-код принимается тоже.
        got = set()
        for c in codes:
            val = c.get("code") if isinstance(c, dict) else c
            if val is not None and str(val).strip():
                got.add(str(val).strip())
        if got:
            return kind, got
    stmt = str(row.get("statement") or "")
    if kind == "base":
        return kind, set(_AIRPORT_CODE.findall(stmt))
    return kind, set(_TYPE_CODE.findall(stmt))


def check_codes(form: dict, model: dict, facts: dict) -> list[dict]:
    """Коды баз и типов судов, объявленные ВМП (определения-перечни;
    уточнители и эффективные параметры жёстких правил), существуют в
    данных набора. Без данных сверки нет — об этом говорится, а не
    молчится. Значение типа читается тем же rule_params, что у ведомости
    исполнения: ответ в template_fit.params не теряется при пустом
    params[]. Исходные носители не переписываются; их согласованность
    остаётся отдельным барьером A05."""
    from vmp.execution_manifest import rule_params
    from unicode_data import confusable_hint
    out: list[dict] = []
    if not facts.get("present"):
        return [finding(CODE_NOT_IN_DATA, WARN, None, "clean_data",
                        "данных набора в пакете нет — сверка кодов баз и "
                        "типов судов не проводилась")]
    airports = facts["airports"]
    types = facts["aircraft_types"]
    for row in _definitions(form):
        got = _codes_of_definition(row)
        if not got:
            continue
        kind, codes = got
        if kind == "base":
            missing = sorted(c for c in codes if c not in airports)
            if missing:
                out.append(finding(
                    CODE_NOT_IN_DATA, STOP, row.get("id"), "statement",
                    "код базы %s из ВМП в данных набора отсутствует "
                    "(базы данных: %s)"
                    % (", ".join(missing),
                       ", ".join(sorted(facts["bases"])) or "нет")
                    + "".join(confusable_hint(c, airports) for c in missing),
                    codes=missing, kind=kind))
        else:
            if not facts.get("has_type_column"):
                out.append(finding(
                    CODE_NOT_IN_DATA, WARN, row.get("id"), "statement",
                    "ВМП объявляет типы судов %s, а лист «Рейсы» "
                    "колонки «Тип ВС» не несёт — сверить не с чем"
                    % ", ".join(sorted(codes)), codes=sorted(codes),
                    kind=kind))
                continue
            missing = sorted(c for c in codes if c not in types)
            if missing:
                out.append(finding(
                    CODE_NOT_IN_DATA, STOP, row.get("id"), "statement",
                    "тип судна %s из ВМП в данных набора отсутствует "
                    "(типы данных: %s)"
                    % (", ".join(missing),
                       ", ".join(sorted(types)) or "нет")
                    + "".join(confusable_hint(c, types) for c in missing),
                    codes=missing, kind=kind))
    # Уточнители параметров жёстких правил: «база XXX» и подобное;
    # параметры-коды семьи флота (fleet_types — перечень кодов,
    # fleet_type — код): каждый названный тип судна обязан быть в
    # колонке «Тип ВС» книги. Связь «правило допуска ↔ данные»
    # замыкается через правило независимо от определений-перечней
    # (решение с CDPM 2026-09-03: codes — доказательный слой без
    # обещания полноты, параметр — второй путь).
    for rule in (model.get("rules") or []):
        if not isinstance(rule, dict) or rule.get("kind") not in HARD_KINDS:
            continue
        for p in (rule.get("params") or []):
            if not isinstance(p, dict):
                continue
            q = str(p.get("qualifier") or "")
            for code in sorted(set(_AIRPORT_CODE.findall(q))):
                if code not in airports:
                    out.append(finding(
                        CODE_NOT_IN_DATA, STOP, rule.get("id"),
                        "params.%s.qualifier" % p.get("name"),
                        "уточнитель «%s» называет код %s, которого в "
                        "данных набора нет" % (q, code)
                        + confusable_hint(code, airports), codes=[code],
                        kind="base"))
        # Уточнитель выше сохраняет свой прежний источник. Значение
        # флота ниже — из общего читателя ИСПОЛНЕНИЯ, не копии его
        # правил приоритета. Иначе ответ только в ячейке не проверялся,
        # а при двух носителях проверялось не исполняемое значение.
        for p in rule_params(rule):
            name = str(p.get("name") or "")
            if name in _FLEET_PARAMS:
                named = _code_values(p.get("value"))
                if not named:
                    continue
                source = ("template_fit.params" if p.get("from") == "template_fit"
                          else "params")
                field = "%s.%s" % (source, name)
                if not facts.get("has_type_column"):
                    out.append(finding(
                        CODE_NOT_IN_DATA, WARN, rule.get("id"),
                        field,
                        "правило называет типы судов %s, а лист «Рейсы» "
                        "колонки «Тип ВС» не несёт — сверить не с чем"
                        % ", ".join(named), codes=named, kind="aircraft_type"))
                    continue
                missing = [c for c in named if c not in types]
                if missing:
                    out.append(finding(
                        CODE_NOT_IN_DATA, STOP, rule.get("id"),
                        field,
                        "тип судна %s из правила в данных набора "
                        "отсутствует (типы данных: %s)"
                        % (", ".join(missing), ", ".join(sorted(types)) or "нет")
                        + "".join(confusable_hint(c, types) for c in missing),
                        codes=missing, kind="aircraft_type"))
    return out


#: Параметры семьи флота с единицами «код» / «перечень кодов» (словарь
#: CDPM 2026-09-03): значение — строка-код либо перечень через запятую.
_FLEET_PARAMS = ("fleet_types", "fleet_type")


def _code_values(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    elif value is None:
        return []
    else:
        items = str(value).split(",")
    out: list[str] = []
    for it in items:
        s = it.strip()
        if s and s not in out:
            out.append(s)
    return out


def check_criteria(form: dict) -> list[dict]:
    """Метрика/вид, направление, ставка и версия ставок каждого
    критерия части; порядок ярусов части."""
    from webapp.task_package import METRICS
    out: list[dict] = []
    for part in PARTS:
        crits = _part_rows(form, part, "criteria")
        for c in crits:
            cid = c.get("id")
            metric = c.get("metric")
            kind = c.get("kind")
            if not metric and not kind:
                out.append(finding(
                    CRITERION_NO_METRIC, STOP, cid, "metric",
                    "критерий без метрики и без машинного вида — "
                    "оптимум не определён", part))
            elif metric:
                tasks = METRICS.get(str(metric))
                if tasks is None:
                    out.append(finding(
                        CRITERION_NO_METRIC, STOP, cid, "metric",
                        "метрика «%s» вне словаря приёмника" % metric,
                        part))
                elif part not in tasks:
                    out.append(finding(
                        CRITERION_NO_METRIC, STOP, cid, "metric",
                        "метрика «%s» не применима к задаче %s (её "
                        "задачи: %s)" % (metric, part, ", ".join(tasks)),
                        part))
            if c.get("direction") not in ("minimize", "maximize"):
                out.append(finding(
                    CRITERION_NO_METRIC, STOP, cid, "direction",
                    "направление критерия не задано (minimize или "
                    "maximize)", part))
            rate = c.get("rate") if isinstance(c.get("rate"), dict) else {}
            has_value = isinstance(rate.get("value"), (int, float)) \
                and not isinstance(rate.get("value"), bool) \
                and rate.get("value") > 0
            rates = rate.get("rates") if isinstance(rate.get("rates"), dict) \
                else {}
            has_rates = all(
                isinstance(rates.get(k), (int, float))
                and not isinstance(rates.get(k), bool) and rates.get(k) > 0
                for k in ("flight", "work")) if rates else False
            if not has_value and not has_rates:
                out.append(finding(
                    CRITERION_NO_RATE, STOP, cid, "rate",
                    "ставка критерия не задана (ни rate.value, ни "
                    "rate.rates) — критерий и штрафы несоизмеримы", part))
            if kind and (not c.get("version") or not c.get("effective_from")):
                out.append(finding(
                    CRITERION_NO_RATE, STOP, cid, "version",
                    "машинный критерий «%s» без версии или даты начала "
                    "действия ставок" % kind, part))
        out.extend(_check_tiers(part, crits))
    return out


def _check_tiers(part: str, crits: list[dict]) -> list[dict]:
    out: list[dict] = []
    if not crits:
        return out
    by_tier: dict[int, list] = {}
    for c in crits:
        t = c.get("tier")
        if isinstance(t, bool) or not isinstance(t, int) or t < 1:
            out.append(finding(
                TIERS_DISORDERED, STOP, c.get("id"), "tier",
                "ярус критерия не задан целым числом от 1", part))
            continue
        by_tier.setdefault(t, []).append(c)
    if not by_tier:
        return out
    tiers = sorted(by_tier)
    if tiers[0] != 1:
        out.append(finding(
            TIERS_DISORDERED, STOP, by_tier[tiers[0]][0].get("id"), "tier",
            "нет критерия яруса 1: ярусы %s — сравнивать нечем"
            % ", ".join(str(t) for t in tiers), part))
    for prev, cur in zip(tiers, tiers[1:]):
        if cur != prev + 1:
            out.append(finding(
                TIERS_DISORDERED, STOP, by_tier[cur][0].get("id"), "tier",
                "пропуск номера яруса: после %d идёт %d" % (prev, cur),
                part))
    for t, group in by_tier.items():
        if len(group) > 1:
            ids = [str(c.get("id")) for c in group]
            out.append(finding(
                TIERS_DISORDERED, STOP, ids[0], "tier",
                "на ярусе %d стоят критерии %s — критерии не "
                "упорядочены, сравнивать нечем" % (t, ", ".join(ids)),
                part, rules=ids))
        for c in group:
            tol = c.get("tolerance")
            if t > 1 and not (isinstance(tol, dict)
                              and isinstance(tol.get("value"), (int, float))
                              and tol.get("unit") in ("percent", "absolute")):
                out.append(finding(
                    TIERS_DISORDERED, STOP, c.get("id"), "tolerance",
                    "ярус %d без допуска ухудшения предыдущего яруса" % t,
                    part))
            if t == 1 and isinstance(tol, dict):
                out.append(finding(
                    TIERS_DISORDERED, STOP, c.get("id"), "tolerance",
                    "у яруса 1 допуск не задаётся", part))
    return out


def check_soft(form: dict) -> list[dict]:
    """Каждое мягкое правило: коэффициент и единица размера нарушения
    из словаря, равная единице семьи шаблона. У жёсткого штрафа нет."""
    from vmp.template_registry import FAMILY_VIOLATION_UNIT, VIOLATION_UNITS
    out: list[dict] = []
    for part in PARTS:
        for row in _part_rows(form, part, "soft"):
            rid = row.get("id")
            pen = row.get("penalty")
            if not isinstance(pen, dict):
                out.append(finding(
                    VIOLATION_UNIT, STOP, rid, "penalty",
                    "мягкое правило без коэффициента и единицы размера "
                    "нарушения", part))
                continue
            coef = pen.get("coefficient")
            if isinstance(coef, bool) or not isinstance(coef, int) or coef < 0:
                out.append(finding(
                    VIOLATION_UNIT, STOP, rid, "penalty.coefficient",
                    "коэффициент мягкого правила не целое ≥ 0: %r" % (coef,),
                    part))
            unit = pen.get("violation_unit")
            if not unit:
                out.append(finding(
                    VIOLATION_UNIT, STOP, rid, "penalty.violation_unit",
                    "единица размера нарушения не объявлена", part))
                continue
            if unit not in VIOLATION_UNITS:
                out.append(finding(
                    VIOLATION_UNIT, STOP, rid, "penalty.violation_unit",
                    "единица размера нарушения «%s» вне словаря %s"
                    % (unit, ", ".join(VIOLATION_UNITS)), part))
                continue
            fam = (row.get("template") or {}).get("family") \
                if isinstance(row.get("template"), dict) else None
            fam_unit = FAMILY_VIOLATION_UNIT.get(fam) if fam else None
            if fam_unit and fam_unit != unit:
                out.append(finding(
                    VIOLATION_UNIT, STOP, rid, "penalty.violation_unit",
                    "единица «%s» не равна единице семьи %s («%s»)"
                    % (unit, fam, fam_unit), part))
        for row in _part_rows(form, part, "hard"):
            if isinstance(row.get("penalty"), dict):
                out.append(finding(
                    VIOLATION_UNIT, STOP, row.get("id"), "penalty",
                    "у жёсткого правила штрафа быть не должно", part))
    return out


def check_degenerate(form: dict, model: dict, facts: dict) -> list[dict]:
    """Предупреждения: нулевые коэффициенты; порог длительности длиннее
    горизонта набора (правило записано, но ни на что не влияет)."""
    out: list[dict] = []
    for part in PARTS:
        soft = [r for r in _part_rows(form, part, "soft") if not _excluded(r)]
        coefs = [((r.get("penalty") or {}).get("coefficient"), r)
                 for r in soft if isinstance(r.get("penalty"), dict)]
        zero = [r for c, r in coefs if c == 0]
        if coefs and len(zero) == len(coefs):
            out.append(finding(
                DEGENERATE, WARN, zero[0].get("id"), "penalty.coefficient",
                "все коэффициенты мягких правил части нулевые — "
                "мягких ограничений по существу нет", part,
                rules=[str(r.get("id")) for r in zero]))
        else:
            for r in zero:
                out.append(finding(
                    DEGENERATE, WARN, r.get("id"), "penalty.coefficient",
                    "коэффициент нулевой: правило записано, но ни на что "
                    "не влияет", part))
    horizon = facts.get("horizon_min") if facts.get("present") else None
    if horizon:
        from vmp.registry_check import is_excluded_until_data
        for rule in (model.get("rules") or []):
            if not isinstance(rule, dict) or rule.get("kind") not in HARD_KINDS \
                    or is_excluded_until_data(rule):
                continue
            for p in _projected_params(rule, out, DEGENERATE):
                if not isinstance(p, dict) or p.get("relation") != "max":
                    continue
                if p.get("unit") not in _DURATION_UNITS:
                    continue
                v = p.get("value")
                if isinstance(v, (int, float)) and not isinstance(v, bool) \
                        and v >= horizon:
                    out.append(finding(
                        DEGENERATE, WARN, rule.get("id"),
                        "params." + str(p.get("name")),
                        "предел %s = %s мин не короче горизонта набора "
                        "(%d мин): правило записано, но ни на что не "
                        "влияет" % (p.get("name"), _num(v), horizon)))
    return out


# ------------------------------------------------------------ гейт

def check(pkg: dict) -> dict:
    """Вердикт постановки по пакету (словарь webapp.task_package.load:
    form, model, clean_data, manifest). Возврат: verdict, findings,
    counts по классам, facts данных."""
    form = pkg.get("form")
    model = pkg.get("model") or {}
    facts = data_facts(pkg.get("clean_data"))
    findings: list[dict] = []
    if not isinstance(form, dict):
        findings.append(finding(
            NO_FORM, STOP, None, "vmp-form.json",
            "формы ВМП в пакете нет — постановку судить нечем"))
    else:
        findings += check_contradiction(model, pkg)
        findings += check_codes(form, model, facts)
        findings += check_criteria(form)
        findings += check_soft(form)
        findings += check_degenerate(form, model, facts)
    stops = [f for f in findings if f["level"] == STOP]
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["class"]] = counts.get(f["class"], 0) + 1
    return {
        "verdict": REJECTED if stops else ACCEPTED,
        "stops": len(stops),
        "warnings": len(findings) - len(stops),
        "counts": counts,
        "findings": findings,
        "data": {k: (sorted(v) if isinstance(v, set) else v)
                 for k, v in facts.items()},
    }


def summary(pkg: dict, limit: int = 50) -> dict:
    """Сжатый вердикт для приёмника (карточка ВМП, вердикт канала):
    checked=False, когда формы в пакете нет (пакеты до формы) или
    проверка упала — тогда это показание «не проверено», не отказ.
    Сегодня — показание рядом с матрицей готовности, не замок на
    запуск: перевод в замок — решение владельца."""
    try:
        res = check(pkg)
    except Exception as e:                                # noqa: BLE001
        return {"checked": False, "error": str(e)}
    no_form = any(f["class"] == NO_FORM for f in res["findings"])
    findings = [{k: f.get(k) for k in ("class", "level", "id", "field",
                                       "part", "message")}
                for f in res["findings"]]
    return {"checked": not no_form, "verdict": res["verdict"],
            "stops": res["stops"], "warnings": res["warnings"],
            "counts": res["counts"], "findings": findings[:limit],
            "findings_total": len(findings)}


def report(result: dict) -> str:
    lines = ["ПОСТАНОВКА: %s (стоп %d, предупреждений %d)"
             % ("ПРИНЯТА" if result["verdict"] == ACCEPTED else "ОТВЕРГНУТА",
                result["stops"], result["warnings"])]
    for f in result["findings"]:
        where = f.get("id") or "-"
        if f.get("part"):
            where = "%s/%s" % (f["part"], where)
        lines.append("  [%s] %s %s.%s: %s"
                     % (f["level"], f["class"], where, f["field"],
                        f["message"]))
    return "\n".join(lines)


def load_package_dir(path: Path) -> dict:
    """Пакет из каталога с членами (manifest.json, model.json,
    vmp-form.json, clean-data.json, objective.json) — для стенда и
    командной строки; zip читает webapp.task_package.load."""
    def _j(name, required=False):
        p = path / name
        if not p.is_file():
            if required:
                raise FileNotFoundError(str(p))
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    return {"path": str(path), "manifest": _j("manifest.json") or {},
            "objective": _j("objective.json"), "model": _j("model.json") or {},
            "clean_data": _j("clean-data.json"), "form": _j("vmp-form.json")}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("использование: python -m vmp.formulation_check "
              "<пакет.zip | каталог пакета> [--json]")
        return 2
    target = Path(argv[0])
    if target.is_dir():
        pkg = load_package_dir(target)
    else:
        from webapp.task_package import load
        pkg = load(target, run_gate=False)
    result = check(pkg)
    if "--json" in argv:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(report(result))
    return 0 if result["verdict"] == ACCEPTED else 1


def _num(v) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


if __name__ == "__main__":
    sys.exit(main())
