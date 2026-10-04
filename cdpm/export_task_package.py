# -*- coding: utf-8 -*-
"""Пакет постановки задачи COSMOS (мост А, отмашка владельца
2026-08-22) — единица обмена CDPM → решатель COSMOS: правила,
целевые, веса, режимы и очищенные данные одним самодостаточным
zip-архивом с контрольными суммами.

Формат закреплён спецификацией docs/spec-postanovka-cosmos-v1.md
(версия 1.0); изменения формата — только ревизией спецификации.
Разрешённые режимы вычисляются из матрицы корректности (ЦФ-1) на
момент выгрузки: они и есть гейт запуска на стороне потребителя.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from cdpm import export_solver, model as m

# Ревизия 1.11 (приказ 0096, 2026-08-28): применённый машинный ответ
# авиакомпании о критерии пейринга включает режим optimization в
# матрице корректности (ветка crit в model.readiness_matrix) — режим
# приходит в allowed_modes из матрицы естественным путём.
# Ревизия 1.15 (аудит 04.09.2026, контракт «ведомость исполнения
# правил» с COSMOS): у правил пакета появляются машинная применимость
# к задачам и режимам, требуемые разделы данных, канон единиц/отношений
# в посадке на шаблон; у манифеста — отпечаток очищенных данных.
# Поля только добавляются: пакеты 1.14 читаются прежним законом.
# Ревизия 1.16 (аудит A9, 05.09.2026): манифест объявляет систему времени
# пакета явно — шкалу меток (time_scale) со сдвигом и IANA-пояс базы.
# Ревизия 1.17 (аудит A7, ступень L, 05.09.2026): раздел manifest.history
# — история сотрудника на момент среза из листа «История» книги данных.
# Ревизия 1.18 (план единой версии 06.09.2026, §3.8): manifest.horizon —
# объявленный горизонт из «Свойств» (end_exclusive считается один раз
# здесь); у записей history.employees[] — свидетельства присутствия
# (location_evidence / location_intervals / conflicts) из листа
# «Присутствие». Читатели принимают 1.18 наравне с 1.17.
SPEC_VERSION = "1.18"

_TASKS_OF_ZONE = {"PAIRING": ("pairing",), "ROSTERING": ("rostering",),
                  "BOTH": ("pairing", "rostering")}


def applicability_of(rule: dict) -> dict:
    """Машинная применимость правила (ревизия 1.15): задачи — из зоны,
    режимы — из вида и веса. HARD и CASE судятся во всех режимах; SOFT
    с числовым весом — в режимах со штрафами (feasibility-soft,
    optimization); SOFT без веса — только в optimization (закон 1.9:
    невзвешенная мягкая строка делает режим со штрафами невозможным);
    DEF, DATA, OUT и строки, исключённые до получения данных, — не
    судятся исполнителем (modes пусты, причина в note). Детерминирована
    и идемпотентна — гейт пересчитывает её и сверяет."""
    tasks = list(_TASKS_OF_ZONE.get(str(rule.get("zone")), ()))
    kind = rule.get("kind")
    if rule.get("excluded_until_data"):
        return {"tasks": tasks, "modes": [],
                "note": "исключено до получения данных по решению "
                        "авиакомпании"}
    if kind in ("HARD", "CASE"):
        return {"tasks": tasks, "modes": list(m.TASK_MODES),
                "note": "жёсткое правило — судится во всех режимах"}
    if kind == "SOFT":
        if export_solver.soft_weight(rule) is not None:
            return {"tasks": tasks,
                    "modes": ["optimization", "feasibility-soft"],
                    "note": "мягкое правило с весом — режимы со штрафами"}
        return {"tasks": tasks, "modes": ["optimization"],
                "note": "мягкое правило без веса — только оптимизация "
                        "(режим со штрафами невозможен без веса)"}
    if kind == "DEF":
        return {"tasks": tasks, "modes": [],
                "note": "определение — исполнителем не судится"}
    if kind == "DATA":
        return {"tasks": tasks, "modes": [],
                "note": "требование к данным — судится разделом данных"}
    return {"tasks": tasks, "modes": [],
            "note": "вне постановки — исполнителем не судится"}


def required_data_of(rule: dict) -> list[str]:
    """Разделы операционных данных, без которых правило не исполнить
    (ревизия 1.15): объединение обязательных разделов задач
    применимости; правила без задач и вне постановки — пусто."""
    app = rule.get("applicability") or applicability_of(rule)
    if not app.get("modes") and rule.get("kind") != "DATA":
        return []
    out: list[str] = []
    for task in app.get("tasks") or ():
        for section in _DATA_SECTIONS.get(task, ()):
            if section not in out:
                out.append(section)
    return out


def canon_fit_params(rule: dict) -> int:
    """Канон единиц и отношений в посадке на шаблон (ревизия 1.15):
    ячейка template_fit.params.<имя> получает unit / relation /
    condition из машинной записи params того же имени (condition —
    уточнитель qualifier). При нескольких записях одного имени берётся
    запись с тем же значением; иначе ячейка не трогается (не угадываем).
    Возвращает число дополненных ячеек.

    ЕДИНИЦА ячейки не переписывается словом записи (исправление В-3
    неавтор-ревью A05, 12.09.2026): ячейка `{12, "ч"}` рядом с записью
    `{720, duration_minutes}` — СОГЛАСНЫЕ носители (720 = 720), и
    прежний перенос единицы делал из ячейки «12 минут», то есть менял
    величину ПОСЛЕ пройденной сверки. Закон: после успешной сверки
    ячейке позволено ровно одно превращение — канонизация тем же
    `family_params.canonical_value` (значение → каноническое число,
    единица → код каталога записи семьи). Не читается — ячейка со
    своей единицей не трогается вовсе.

    ОТНОШЕНИЕ И УСЛОВИЕ — тот же закон (открытый вопрос 2 ранней части
    A05, решение ведущего 12.09.2026): канон НИКОГДА не переписывает
    ЯВНОЕ поле ячейки значением записи. Живой случай: самолечение
    `model.restore_param_relations` чинит запись `params[]` на «min»
    (природа имени и формулировка), ячейка явно говорит «max» — прежде
    канон молча переносил «min» в ячейку, и смысл посадки менялся
    ПОСЛЕ пройденной сверки, беззвучно. Разошедшиеся явные поля — это
    расхождение носителей, и судит его повторная сверка после лечения
    (`refuse_diverging_carriers`, тот же код `param-carriers-diverge`).
    Пустое поле ячейки (ключа нет, `null`, пустая строка) отношение и
    условие записи по-прежнему НАСЛЕДУЕТ: там своей правды нет."""
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or not isinstance(fit.get("params"), dict):
        return 0
    if fit.get("schema_version") == "landing/2":
        # Typed cells already declare the catalog relation and exact scope;
        # null is meaningful, not a slot for legacy eq/qualifier defaults.
        # Invalid typed cells are refused by validation, never healed here.
        return 0
    legacy = [p for p in (rule.get("params") or ())
              if isinstance(p, dict) and p.get("name")]
    fam = fit.get("family")
    changed = 0
    for name, cell in fit["params"].items():
        if not isinstance(cell, dict):
            continue
        same = [p for p in legacy if p.get("name") == name]
        if len(same) > 1:
            same = [p for p in same if p.get("value") == cell.get("value")]
        if len(same) != 1:
            continue
        p = same[0]
        for key, src in (("unit", "unit"), ("relation", "relation"),
                         ("condition", "qualifier")):
            # A scalar old record describes one base, not the scope of an
            # entire current map. Never narrow the map to that old subset.
            if key == "condition":
                from cdpm import family_params as fp
                if fp.base_map_value(cell.get("value")) is not None:
                    continue
            val = p.get(src)
            if not isinstance(val, str) or not val:
                continue
            if key == "unit":
                changed += _canon_cell_unit(cell, m.fit_param_unit(fam, name))
                continue
            cur = cell.get(key)
            # Явное поле ячейки — своя правда: молча её не заменяем,
            # расхождение отдаём повторной сверке носителей.
            if cur is not None and str(cur).strip():
                continue
            if cur != val:
                cell[key] = val
                changed += 1
    return changed


def _canon_cell_unit(cell: dict, canon) -> int:
    """Канонизация единицы ячейки посадки ВМЕСТЕ со значением: 1 —
    ячейка изменена, 0 — трогать нечего либо нельзя. Единицей ячейки
    становится код каталога записи семьи (`canon`), а значением — то же
    значение, прочитанное в этой единице (`canonical_value`). Значение,
    которое в своей единице не читается, и имя без записи семьи
    оставляют ячейку как есть: величину наугад не меняем (В-3)."""
    from cdpm import family_params as fp
    canon = str(canon or "")
    if not canon or cell.get("unit") == canon:
        return 0
    value, bad = fp.canonical_value(cell.get("value"), cell.get("unit"), canon)
    if bad is not None:
        return 0
    if isinstance(value, Decimal):
        value = int(value) if value == value.to_integral_value() \
            else float(value)
    cell["value"], cell["unit"] = value, canon
    return 1


def derive_params_from_landing(rule: dict) -> list[dict]:
    """Совместимая проекция общим читателем; отказ в договоре пакета."""
    from cdpm import param_projection as projection
    # Сохраняем прежние слова/код рубежа выпуска.
    refuse_diverging_carriers({"rules": [rule]})
    try:
        return projection.derive_params_from_landing(rule)
    except projection.ParamProjectionError as exc:
        raise TaskPackageError(str(exc), code=exc.code, items=exc.items) from None


def enrich_rules_for_package(doc: dict) -> list[str]:
    """Поля ревизии 1.15 у правил пакета: применимость, требуемые
    данные, канон параметров посадки. Возвращает пометки по изменённым
    правилам (как прочие самолечения сборки); идемпотентна — повторный
    вызов даёт пустой список, поэтому экспорт байт-детерминирован."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        if not isinstance(r, dict):
            continue
        what: list[str] = []
        app = applicability_of(r)
        if r.get("applicability") != app:
            r["applicability"] = app
            what.append("applicability")
        req = required_data_of(r)
        if r.get("required_data") != req:
            r["required_data"] = req
            what.append("required_data")
        n = canon_fit_params(r)
        if n:
            what.append(f"канон посадки ×{n}")
        if what:
            notes.append(f"{r.get('id')}: поля 1.15 — " + ", ".join(what))
    return notes


def heal_for_package(doc: dict) -> list[str]:
    """ОДНА цепочка машинного лечения свода при сборке пакета: свод
    ПРАВИТСЯ НА МЕСТЕ, возвращаются пометки изменений (пусто — свод уже
    в виде сборки).

    ДЕТЕРМИНИРОВАНА И ИДЕМПОТЕНТНА по построению: каждый шаг — та же
    правка формы, что и прежде (повторный экспорт байт-идентичен), и
    повторный вызов всей цепочки на уже вылеченном своде даёт пустой
    список. Порядок шагов значим (разбор полёта снимается ДО подъёма
    прозы, семьи ставятся после посадки) и меняться вправе только
    вместе с этой функцией.

    Одна функция — потому что цепочку обязаны видеть ОБА читателя
    фактов биекции (A11, ревью п. 3): замок выпуска судит свод ПОСЛЕ
    лечения, и индикатор «Биекция» панели обязан судить его же, иначе
    панель и выпуск расходятся на строках, которые лечение снимает
    (целевая-осколок ответа без метрики и без трассировки). Панель
    зовёт её через cdpm.coverage.package_doc — на КОПИИ; канонический
    model.json прогона не трогается ни сборкой, ни панелью."""
    # Живой урок 2026-08-26 (фантомные −10 стыковок): область только
    # словами следа → машинный уточнитель восстанавливается при
    # сборке пакета с примечанием у правила; миграция детерминирована
    # (повторный экспорт байт-идентичен), двусмысленный след не
    # трогается.
    healed = m.restore_sit_param_from_formal(doc)
    healed += m.restore_area_qualifiers(doc)
    healed += m.restore_crew_variant_qualifiers(doc)
    healed += m.restore_param_relations(doc)
    # Волна условий (этап 5б): разбор полёта — не стыковка (чужая
    # природа снимается ДО подъёма прозы, чтобы правило разбора не
    # получило второй параметр поверх ложного).
    healed += m.restore_debrief_param_nature(doc)
    healed += m.restore_briefing_debrief_params(doc)
    healed += m.restore_sit_prose_params(doc)
    healed += m.restore_sit_from_answer(doc)
    # Консенсус CDPM/COSMOS 2026-08-28 (счёт наихудших трактовок):
    # фантомы заголовков неприменимой таблицы — в OUT; пределы
    # секторов окна циркадной активности — params из формулировок;
    # пояс ночного окна — правда документа (акклиматизация).
    healed += m.retire_frms_phantom_rules(doc)
    healed += m.restore_wocl_sector_params(doc)
    healed += m.restore_night_window_tz(doc)
    # Репетиция демо 2026-08-29 (дефект 1): штрафная строка без веса,
    # изменчиво извлечённая целевой, возвращается мягким правилом и
    # при сборке пакета — второй ремень к точке в извлечении.
    healed += m.restore_soft_penalty_rules(doc)
    # Блокер прохода 06.09.2026 (run-0086): правило с исполнителем-ручкой
    # шаблона не выпускает logic с enforce=required — второй исполнитель
    # одного правила; блок сводится к post-check (детерминировано).
    healed += m.reconcile_logic_with_template_fit(doc)
    # Ремень (а) ночи демо: value-ответы тем носителя — параметрами
    # правил детерминированно (вторая точка того же ремня).
    healed += m.apply_topic_value_answers(doc)
    # Контракт P-N-SHD 2026-08-30: семьи мягких правил обязаны быть
    # в model.json пакета — источник истины привязки шаблонов
    # решателя; вторая точка того же классификатора (первая — путь
    # ответов), пакет не зависит от того, каким путём модель дошла.
    healed += m.assign_soft_families(doc)
    # Ярлыки кодом (2026-09-02): вид и зона строк с посадкой — из
    # атрибутов семьи; своды до детерминизации самолечатся при сборке
    # пакета той же идемпотентной функцией (ревью пакета r54).
    healed += m.derive_all_rule_traits(doc, "сборка пакета постановки")
    # Ревизия 1.15: применимость, требуемые данные и канон параметров
    # посадки — у правил ПАКЕТА (канонический свод прогона не трогается).
    healed += enrich_rules_for_package(doc)
    return healed


def data_fingerprint_of(clean_text: str, clean_data: dict) -> dict:
    """Отпечаток очищенных данных пакета (ревизия 1.15, нужен
    доказательствам невозможности A4): sha256 всего clean-data.json и
    построчный отпечаток каждого листа (sid/лист → sha256 строк)."""
    sheets: dict[str, str] = {}
    tables = clean_data.get("tables") if isinstance(clean_data, dict) else None
    if isinstance(tables, dict):
        for sid, by_name in sorted(tables.items()):
            if not isinstance(by_name, dict):
                continue
            for name, table in sorted(by_name.items()):
                rows = table.get("rows") if isinstance(table, dict) else None
                body = json.dumps(rows if rows is not None else [],
                                  ensure_ascii=False, sort_keys=True)
                sheets[f"{sid}/{name}"] = hashlib.sha256(
                    body.encode("utf-8")).hexdigest()
    return {"clean_data_sha256": hashlib.sha256(
                clean_text.encode("utf-8")).hexdigest(),
            "sheets": sheets}

# Распознавание численности экипажа — у ПРОИЗВОДИТЕЛЯ (ревизия 1.2):
# формулировки свода порождает языковая модель и меняет между
# прогонами; потребитель дважды ломался, паря чужой текст своими
# образцами. Владелец формулировок держит и словарь; не распозналось
# — поле честно отсутствует, потребитель отказывает.
_CREW_SIZE_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"crew_size\s*=\s*(\d+)",
    r"pilot_count\s*\([^)]*\)\s*=\s*(\d+)",
    r"(?:состоит из|входят|входит)\s*(\d+)\s*пилот",
    r"пилот\w*[^0-9=]{0,40}=\s*(\d+)",
    r"(\d+)\s*пилот",
))


def _crew_composition_fits(doc: dict) -> list[dict]:
    """Действующие правила (HARD/DATA/DEF, без блокировок), посаженные
    на семью состава crew_composition категорией A/B."""
    out = []
    for r in doc.get("rules", []):
        if not isinstance(r, dict) or r.get("blockers") \
                or r.get("kind") not in ("HARD", "DATA", "DEF"):
            continue
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("family") == "crew_composition" \
                and fit.get("category") in ("A", "B"):
            out.append(r)
    return out


def _crew_size_from_fit(doc: dict) -> dict[int, str]:
    """Ревизия 1.19 (09.09.2026, отказ потребителя по пакету run-0114):
    первичный машинный носитель численности места — параметр посадки
    `crew_size` семьи crew_composition (категория A/B). Запись с условием
    применения (`condition`) — вариант состава, не глобальная численность;
    отношение, если названо, только «равно». Формулировка правила здесь
    не читается: её порождает модель, и она меняется между чтениями
    (run-0111 «состоит ровно из 2 пилотов» → образец сработал; run-0114
    «crew_size(смена) = 2» → ни один образец не сработал, поле молча
    отсутствовало, потребитель отказал на кнопке пейринга)."""
    found: dict[int, str] = {}
    for r in _crew_composition_fits(doc):
        params = r["template_fit"].get("params")
        p = params.get("crew_size") if isinstance(params, dict) else None
        if not isinstance(p, dict) or p.get("condition") \
                or p.get("relation") not in (None, "eq"):
            continue
        if m.CREW_VARIANT_RE.search(f"{r.get('title') or ''} "
                                    f"{r.get('statement') or ''}"):
            continue
        v = p.get("value")
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and v > 0:
            found.setdefault(v, str(r.get("id")))
    return found


def crew_size_sources(doc: dict) -> dict[int, str]:
    """Все кандидаты численности места {число: правило-источник} из трёх
    носителей по старшинству имени источника: параметр посадки семьи
    состава (1.19), машинный параметр правила (1.3), текст формулировки
    (1.2, только перекрёстная сверка). Разные числа = конфликт у
    вызывающего."""
    found = dict(_crew_size_from_fit(doc))
    for r in doc.get("rules", []):
        if not isinstance(r, dict) or r.get("blockers") \
                or r.get("kind") not in ("HARD", "DATA", "DEF"):
            continue
        params = r.get("params")
        for p in (params if isinstance(params, list) else []):
            if isinstance(p, dict) and p.get("name") == "crew_size" \
                    and p.get("qualifier") is None \
                    and p.get("relation") == "eq" \
                    and isinstance(p.get("value"), int) \
                    and not isinstance(p.get("value"), bool) \
                    and p["value"] > 0:
                found.setdefault(p["value"], str(r.get("id")))
    text = _crew_size_from_text(doc)
    if text is not None:
        # Перекрёстная сверка: текст другого правила с ДРУГИМ числом
        # делает производное неоднозначным (посадка и params первее
        # как имя источника при совпадении).
        found.setdefault(text["value"], text["rule"])
    return found


def crew_size_of(doc: dict):
    """{'value', 'rule'} либо None. Ревизия 1.3: манифестное поле —
    ПРОИЗВОДНОЕ от машинного носителя правила (с 1.19 — параметр
    посадки crew_size семьи состава, до того — params: name crew_size),
    два источника одного числа не расходятся (решение с COSMOS
    2026-08-22); текст-распознавание 1.2 остаётся запасным путём и
    перекрёстной сверкой. Конфликт — None (не угадываем).

    Границы params-пути (ревью 1.3): только действующие типы
    HARD/DATA/DEF (OUT/SOFT не задают численность места), только
    relation=eq (граница — не численность) и только записи БЕЗ
    qualifier (контекстное значение — не глобальное число)."""
    found = crew_size_sources(doc)
    if len(found) != 1:
        return None
    n, rid = next(iter(found.items()))
    return {"value": n, "rule": rid}


def crew_size_problem(doc: dict) -> str | None:
    """Fail-closed (ревизия 1.19): правило состава посажено на семью
    crew_composition, а численность места не выводится — пакет НЕ
    собирается, причина названа. Прежде поле молча отсутствовало, и
    потребитель отказывал уже на кнопке пейринга («численность экипажа
    не задана», run-0114, 09.09.2026). Без посадки на семью состава
    закон 1.12 прежний: отсутствие ключа законно."""
    if not _crew_composition_fits(doc):
        return None
    sources = crew_size_sources(doc)
    if len(sources) == 1:
        return None
    if not sources:
        return ("численность экипажа не выводится: правило состава сидит "
                "на семье crew_composition, а параметр посадки crew_size "
                "не читается (нет числа, стоит условие варианта или "
                "отношение не «равно»)")
    parts = ", ".join(f"{n} ({rid})" for n, rid in sorted(sources.items()))
    return f"численность экипажа не выводится: источники расходятся — {parts}"


def _crew_size_from_text(doc: dict):
    """Запасной путь 1.2: распознавание из формулировок."""
    found: dict[int, str] = {}
    for r in doc.get("rules", []):
        if r.get("kind") not in ("HARD", "DATA", "DEF") \
                or r.get("blockers"):
            continue
        text = f"{r.get('title') or ''} {r.get('statement') or ''}"
        # Вариант состава (расширенный/усиленный) — контекстное
        # значение, не глобальная численность места (живой случай
        # одной из линий, run-0060) — исключается и на запасном
        # текстовом пути.
        if m.CREW_VARIANT_RE.search(text):
            continue
        for rx in _CREW_SIZE_RES:
            mm = rx.search(text)
            if mm:
                n = int(mm.group(1))
                if n > 0:
                    found.setdefault(n, str(r.get("id")))
                break
    if len(found) != 1:
        return None
    n, rid = next(iter(found.items()))
    return {"value": n, "rule": rid}


class TaskPackageError(ValueError):
    """Пакет не собрался; причина названа (fail-closed).

    `code` — необязательный машинный код отказа: ручки выпуска кладут
    его добавочным ключом тела 409 рядом со словами (`detail` остаётся
    строкой). У прежних отказов кода нет — ключ `code` пустой, и
    читатель, который его не знает, читает ответ как раньше."""

    def __init__(self, words: str, code: str | None = None,
                 items: list[str] | None = None):
        super().__init__(words)
        self.code = code
        #: Перечень идентификаторов, к которым относится отказ (правила,
        #: параметры, ячейки) — ручки выпуска кладут его ключом `items`
        #: тела 409 (контракт П0 §1.4); пустой, когда перечня нет.
        self.items = list(items or [])


#: Коды отказов гейта непротиворечивости (СМТ-1) при выпуске пакета.
#: `smt-contradiction` — вердикт красный: свод противоречив, названо
#: ядро. `smt-unavailable` — вердикта НЕТ (решателя нет, перевод не
#: состоялся, канал переводчика не построился, решатель не пришёл к
#: вердикту): «обязательный» значит, что без вердикта пакет не
#: выпускается (fail-closed), а не «выпускается без проверки».
#: `smt-incomplete` (A01, 12.09.2026) — переведены не все жёсткие
#: правила: вердикта нет, перечень непереведённых — в `items`.
SMT_CONTRADICTION = "smt-contradiction"
SMT_UNAVAILABLE = "smt-unavailable"
SMT_INCOMPLETE = "smt-incomplete"
#: Барьеры волны 17а (контракт П0 §5, §9; 12.09.2026) — коды одних и
#: тех же расчётов, что у готовности и гейта: `param-carriers-diverge`
#: — носители параметра расходятся (A05, `model.reconcile_carriers`);
#: `family-incomplete` — обязательный параметр использованной семьи не
#: задан и не отклонён либо перепосадка требуется (K01,
#: `family_params.family_completeness`); `support-missing` — семья без
#: подтверждённой поддержки исполнителя (K06, `catalog_bridge.
#: family_support`).
PARAM_CARRIERS_DIVERGE = m.PARAM_CARRIERS_DIVERGE
FAMILY_INCOMPLETE = m.FAMILY_INCOMPLETE
SUPPORT_MISSING = m.SUPPORT_MISSING


def refuse_nameless_hard_rules(doc: dict) -> None:
    """Keep the precise identity refusal before migration and after healing."""
    from cdpm import smt_verify as _nameless_mod
    nameless = _nameless_mod.nameless_hard_positions(doc)
    if nameless:
        raise TaskPackageError(
            "выпуск пакета остановлен: " + _nameless_mod.NAMELESS_WORDS
            + ", ".join(str(i) for i in nameless[:12])
            + ". Поправьте свод (правило без идентификатора незаконно) и "
              "повторите выпуск", SMT_UNAVAILABLE)


def refuse_invalid_logic_units(doc: dict) -> None:
    """CDPM-292: package release must enforce the same role-unit contract."""
    problems = []
    for rule in doc.get("rules", []):
        if isinstance(rule, dict) and isinstance(rule.get("logic"), dict):
            problems.extend(f"{rule.get('id')}: {problem}"
                            for problem in m.logic_unit_problems(rule["logic"], rule))
    if problems:
        raise TaskPackageError("выпуск пакета остановлен: единицы параметров "
                               "не соответствуют логике — " + "; ".join(problems))


def refuse_answer_provenance(doc: dict) -> None:
    """Keep the answer's structured refusal before any derived projection."""
    from cdpm import provenance
    problems, items = provenance.package_refusal(doc)
    if problems:
        raise TaskPackageError(
            "происхождение значения из ответа не подтверждено: "
            + "; ".join(problems), code=provenance.ANSWER_PROVENANCE, items=items)


def refuse_diverging_carriers(doc: dict) -> None:
    """A05-барьер (контракт П0 §3.1 закон 1, §12.5): сверка ИСХОДНЫХ
    носителей параметра — ячейки посадки и записи `params[]` — по
    значению, единице, отношению и условию ДО любого лечения и
    выведения `params[]` сборкой (иначе «3 против 30» исчезло бы до
    проверки). Расхождение — отказ выпуска `param-carriers-diverge` с
    перечнем «правило/параметр» в `items`."""
    found = m.carrier_problems(doc)
    if not found:
        return
    words = "; ".join(d["words"] for d in found[:4])
    if len(found) > 4:
        words += f" (и ещё {len(found) - 4})"
    raise TaskPackageError(
        f"выпуск пакета остановлен: у параметра два носителя с разной "
        f"правдой — {words}. Согласуйте оба носителя полной согласованной "
        f"заплатой цикла: передайте посадку и соответствующие params[] "
        f"по исходной норме, сохранив подтверждённое происхождение значений, "
        f"затем повторите выпуск. Не удаляйте params[] ради обхода проверки",
        PARAM_CARRIERS_DIVERGE,
        [f"{d['rule']}/{d['param']}" for d in found])


def _other_grounds(doc: dict, named, spec_version: str | None = None) -> str:
    """Основания остановки задач, КРОМЕ уже названных барьером: по
    жёстким причинам матрицы корректности той задачи, у которой не
    осталось ни одного режима.

    Ответ ревьюеру на вопрос исполнителя (неавтор-ревью 12.09.2026):
    отказ рубежа несёт код одного барьера (`family-incomplete` старше),
    но владельцу нужен ВЕСЬ список — иначе, ответив на вопрос о
    параметре, он получит второй отказ о другом основании другой
    задачи."""
    matrix = m.readiness_matrix(doc, spec_version)
    parts = []
    for task, word in (("pairing", "пейринг"), ("rostering", "ростеринг")):
        cells = matrix.get(task) or {}
        if any((cells.get(mode) or {}).get("ready") for mode in m.TASK_MODES):
            continue
        reasons = (cells.get("feasibility-hard") or {}).get("reasons") or {}
        rest = [f"{tag} ({n})" for tag, n in sorted(reasons.items())
                if tag not in named]
        if rest:
            parts.append(f"{word}: " + "; ".join(rest))
    return ". Прочие основания остановки — " + ". ".join(parts) if parts else ""


def refuse_by_barriers(doc: dict, modes: dict,
                      spec_version: str | None = None) -> None:
    """K01/K06-барьеры на рубеже выпуска (контракт П0 §3.2, §3.5):
    когда матрица корректности не оставила ни одного режима, а среди
    оснований есть проблема полноты семьи или поддержки исполнителя, —
    отказ с её кодом и перечнем (`family-incomplete` старше:
    неполную семью не исполнить никакому исполнителю). Отказ называет
    и ПРОЧИЕ основания остановленных задач (`_other_grounds`): код у
    отказа один, а список причин — полный.

    Незатронутая задача при готовности «б» передаётся по-прежнему
    (политика частичной передачи, уточнение аудитора K01): затронутые
    режимы сняты матрицей, а слова их оснований едут в манифесте —
    в `notes` столбца ключа `readiness_matrix` (см. `readiness_ladder`;
    в счёте открытых вопросов барьер не считается — С-2)."""
    if any(modes.values()):
        return
    barriers = m.readiness_barriers(doc, spec_version)
    named = {pr["words"] for pr in barriers}
    fam = [pr for pr in barriers if pr.get("code") == FAMILY_INCOMPLETE]
    sup = [pr for pr in barriers if pr.get("code") == SUPPORT_MISSING]
    if fam:
        words = "; ".join(pr["words"] for pr in fam[:4])
        if len(fam) > 4:
            words += f" (и ещё {len(fam) - 4})"
        # Совет даётся ПО ДЕЛУ (В-1 и К-2 неавтор-ревью): «повторите
        # прогон по текущему каталогу» относится ТОЛЬКО к доказанной
        # перепосадке — клаузам (б)/(в) §12.15. Прежде этот совет ехал
        # с любой недостачей и был неправдой; ужесточение наборов
        # (клауза (а)) — тоже недостача, и её лечит ответ анкеты.
        #
        # К-1 неавтор-ревью 12.09.2026: лечимость считается по СЕМЬЕ, а
        # не по признаку `proven` отдельного основания. У семьи,
        # требующей перепосадки (доказанные клаузы (б)/(в) ЛИБО записи
        # семьи в каталоге нет), анкета вопроса НЕ ставит
        # (`family_params.ensure_family_param_questions`), а ответ на
        # вопрос, заданный законно ДО смены каталога, живой путь панели
        # отвергает целиком. Значит ВСЕ основания такой семьи — и её
        # недостача с `proven=False`, и «нет контракта» (тоже
        # `proven=False`) — ответом не лечатся, и звать к ответу
        # нельзя: это ложное обещание того же класса, что К-2.
        #
        # Правка 12.09.2026: у нелечимости ВТОРАЯ причина того же
        # класса — запись семьи каталога ЧИТАЕТСЯ НЕ ЦЕЛИКОМ
        # (`catalog_broken`). Перепосадки такая семья не требует, и
        # вердикт у неё «полна»/«неполна», поэтому прежний счёт по
        # одному вердикту звал «Ответьте на вопрос анкеты» — а кривую
        # запись каталога ответ АВИАКОМПАНИИ не чинит: свод держится
        # барьером и с ответом. Лечит исправление записи каталога.
        #
        # Лечимость считает ОДИН закон (`family_params.family_curable`)
        # в ОДНОЙ точке — расчёте `family_completeness`; здесь только
        # читаются поля основания: `curable` (лечится ли ответом),
        # `verdict` и `catalog_broken` (какой путь назвать).
        from cdpm import family_params as _fp
        curable = [pr for pr in fam if pr.get("curable")]
        reland = [pr for pr in fam
                  if pr.get("verdict") == _fp.VERDICT_RELAND]
        broken = [pr for pr in fam if pr.get("catalog_broken")]
        references = [pr for pr in fam if pr.get("reference_broken")]
        answer = ("Ответьте на вопрос анкеты о параметре (значение "
                  "либо «не применяется»)")
        rerun = "повторите прогон по текущему каталогу"
        fix_catalog = ("исправьте запись семьи в каталоге и повторите "
                       "прогон по исправленному каталогу")
        tails = []
        if references:
            tails.append("восстановите закреплённый выпуск и проверьте исходную "
                         "ссылку набора; не подменяйте её текущим набором")
        if reland:
            tails.append(f"при «{_fp.VERDICT_RELAND}» — {rerun}"
                         if curable else rerun)
        if broken:
            tails.append(f"при «читается не целиком» — {fix_catalog}"
                         if curable else fix_catalog)
        if curable:
            advice = answer + (", а " + "; ".join(tails) if tails else "")
        else:
            advice = ("Ответом анкеты такой свод не лечится: "
                      + "; ".join(tails))
        raise TaskPackageError(
            f"выпуск пакета остановлен: полнота семьи — {words}"
            f"{_other_grounds(doc, named, spec_version)}. {advice}, и повторите выпуск",
            FAMILY_INCOMPLETE,
            [f"{pr['family']}/{pr['param']}" for pr in fam])
    if sup:
        words = "; ".join(pr["words"] for pr in sup[:4])
        if len(sup) > 4:
            words += f" (и ещё {len(sup) - 4})"
        # `items` — АДРЕСА ТОЧНЫХ ЗАПИСЕЙ каталога `families/<id>`
        # (§12.11 п. 3), а не имена семей: модели пакета при отказе нет,
        # и по этим адресам COSMOS подаёт заявку на вид тем же путём
        # `submit_kind_requests`. Слова хвоста называют закон поправки
        # 12.11 — поддержка судится по ОБОИМ файлам привязок, «есть»
        # хотя бы у одного продукта (прежний хвост говорил «продуктом
        # COSMOS» и противоречил собственному расчёту барьера).
        raise TaskPackageError(
            f"выпуск пакета остановлен: {words}"
            f"{_other_grounds(doc, named, spec_version)}. Исполнимость даёт только "
            f"подтверждённая поддержка записи каталога по привязкам "
            f"обоих продуктов (клетка «есть» хотя бы у одного при "
            f"каталоге того же отпечатка): дождитесь выпуска исполнителя "
            f"(заявка в каталог) либо обновите привязки, и повторите "
            f"выпуск",
            SUPPORT_MISSING, [pr["item"] for pr in sup])

#: Копия отчёта проверки непротиворечивости В ПАКЕТЕ (A01-F1,
#: 12.09.2026): тот отчёт прогона, которым вынесен вердикт раздела
#: `manifest.consistency` (путь в прогоне — ключ `report`); по нему гейт
#: производителя сверяет строки перевода с жёсткими правилами model.json
#: пакета.
SMT_REPORT_NAME = "smt-report.json"

#: `family-incomplete` (контракт П0 §5) — обязательный параметр
#: использованной семьи не даёт готовности. K04-барьер (§2.4 (а),
#: 12.09.2026) пользуется ТЕМ ЖЕ кодом с отдельными словами
#: «пересмотр открыт»: своего кода у K04 в §5 не объявлено, а
#: предмет отказа тот же — параметр семьи, значения которого у
#: выпуска нет (прежнее решением уже не является). Имя одно на оба
#: барьера и живёт в `model` — перечень `items` у обоих одной
#: грамматики «<семья>/<параметр>».


def _spec_at_least(spec_version: str | None, floor: str) -> bool:
    """Пакет судится законом СВОЕЙ версии спецификации: правило,
    введённое ревизией N, не применяется задним числом к
    замороженным пакетам младших ревизий (эталоны вехи А — 1.3).
    None = текущая сборка (всегда новейший закон)."""
    if spec_version is None:
        return True
    try:
        parts = [int(x) for x in str(spec_version).split(".")[:2]]
        floor_parts = [int(x) for x in floor.split(".")[:2]]
        return parts >= floor_parts
    except ValueError:
        return True


def allowed_modes(doc: dict,
                  spec_version: str | None = None) -> tuple[dict, dict]:
    """(режимы per задача, целевые per задача) из матрицы
    корректности: ячейка ready → режим разрешён. spec_version —
    версия проверяемого пакета (для гейта); None = текущая."""
    matrix = m.readiness_matrix(doc, spec_version)
    modes: dict[str, list[str]] = {}
    objectives: dict[str, list[str]] = {}
    for task, cells in matrix.items():
        modes[task] = [mode for mode in m.TASK_MODES
                       if (cells.get(mode) or {}).get("ready")]
        objectives[task] = list(
            (cells.get("optimization") or {}).get("objectives", []))
    # Спецификация 1.9 (решение владельца 2026-08-26): оптимизация
    # пейринга существует только при ОБЪЯВЛЕННОМ авиакомпанией
    # критерии (строка данных → машинный блок); без него режим
    # честно снимается — остаются feasibility/soft.
    from cdpm import export_solver as _es
    if _spec_at_least(spec_version, "1.9") \
            and "optimization" in modes.get("pairing", []) \
            and _es.derive_pairing_criterion(doc) is None:
        modes["pairing"] = [x for x in modes["pairing"]
                            if x != "optimization"]
    # Ревизия 1.11: критерий из ответа включает optimization пейринга
    # (ветка crit в матрице). Пакет МЛАДШЕ 1.11 судится своим законом:
    # там режим появлялся только с целевыми строками — пришедший из
    # матрицы optimization при пустых objectives честно снимается,
    # иначе гейт красит замороженные пакеты задним числом (ревью
    # пакета 1.11, живой пример — пакет кнопки №23 spec 1.10).
    if not _spec_at_least(spec_version, "1.11") \
            and "optimization" in modes.get("pairing", []) \
            and not objectives.get("pairing"):
        modes["pairing"] = [x for x in modes["pairing"]
                            if x != "optimization"]
    # Живой случай одной из линий, run-0060 (2026-08-27):
    # авиакомпания подтвердила
    # отказ от оптимизации И от весов — SOFT-строка зоны без
    # числового веса делает режим со штрафами невозможным по
    # построению (штрафовать нечем; молча игнорировать строку —
    # обман). Режимы со штрафами честно снимаются — остаётся
    # «любое допустимое» (feasibility-hard), ровно ответ а/к.
    if _spec_at_least(spec_version, "1.9"):
        unweighted: dict[str, bool] = {}
        for r in doc.get("rules", []):
            if r.get("kind") != "SOFT":
                continue
            if _es.soft_weight(r) is not None:
                continue
            if r.get("blockers"):
                continue          # вес ещё придёт ответом анкеты
            for t in m._ZONE_TASKS.get(r.get("zone"), ()):
                unweighted[t] = True
        for t, has in unweighted.items():
            if has:
                modes[t] = [x for x in modes.get(t, [])
                            if x not in ("optimization",
                                         "feasibility-soft")]
    return modes, objectives


LADDER_STATES = ("stop", "feasible_hard", "feasible_soft",
                 "optimization")

# Разделы данных, обязательные задаче (ревизия 1.12). Детекция — по
# именам листов clean-data и колонкам листа экипажей; закрытые
# перечни, не грамматика. Fail-closed: нет раздела — столбец в stop.
_DATA_SECTIONS = {
    "pairing": ("рейсы", "экипажи"),
    "rostering": ("рейсы", "экипажи", "недоступности"),
}
_SECTION_SHEETS = {
    "рейсы": ("рейсы", "legs", "flights"),
    "экипажи": ("экипажи", "employees", "crew"),
    "недоступности": ("отпуска", "недоступности", "unavailability",
                      "unavailabledays", "vacations"),
}
_UNAVAIL_COLUMNS = ("unavailabledays", "unavailable_days")

# Маркер временного строгого ответа анкеты (порядок владельца
# 2026-08-28: строгая трактовка ДО ответа авиакомпании; ответ
# обязан начинаться этим словом — иначе строгость не объявлена).
_STRICT_INTERIM_PREFIX = "ВРЕМЕННЫЙ ОТВЕТ"


def _clean_sections(clean_data: dict | None) -> set[str]:
    """Обязательные разделы, присутствующие в clean-data. Раздел
    «есть», когда есть лист с его именем (пустой лист = заявленное
    отсутствие записей — раздел присутствует); недоступности
    признаются и колонкой unavailableDays на любом листе."""
    present: set[str] = set()
    if not isinstance(clean_data, dict):
        return present
    tables = clean_data.get("tables")
    if not isinstance(tables, dict):
        return present
    for sheets in tables.values():
        if not isinstance(sheets, dict):
            continue
        for name, sheet in sheets.items():
            low = str(name).strip().lower()
            for section, aliases in _SECTION_SHEETS.items():
                if low in aliases:
                    present.add(section)
            if isinstance(sheet, dict):
                cols = [str(c).strip().lower()
                        for c in sheet.get("columns", [])
                        if isinstance(c, str)]
                if any(c in _UNAVAIL_COLUMNS for c in cols):
                    present.add("недоступности")
    return present


def readiness_ladder(doc: dict, clean_data: dict | None = None,
                     spec_version: str | None = None) -> dict:
    """Матрица готовности ВМП (директива владельца 2026-08-29):
    столбцы pairing/rostering, состояние клетки — лестница
    stop → feasible_hard → feasible_soft → optimization.

    Состояние — детерминированная функция оснований, ручных
    назначений нет: ступень читается из allowed_modes (наследуя ВСЕ
    законы матрицы корректности, включая вето 1.9/1.11 и fail-closed
    немаркированной жёсткости — вопрос по строке без метки SOFT
    судится жёстким), затем отсутствие обязательного раздела данных
    опускает столбец в stop. Временные строгие ответы анкеты
    состояние НЕ понижают — только объявляются числом (решение
    владельца). Верификатор COSMOS обязан получить те же состояния
    независимым пересчётом; расхождение любой клетки = остановка
    запуска (граница: матрица приходит из CDPM, верифицируется
    COSMOS).

    Барьеры 17а (K01/K06) едут в столбце ЧЕСТНО (С-2 неавтор-ревью
    12.09.2026): их слова ложатся в `notes`, а в счётчик
    `grounds.questions.open_blocking` они НЕ попадают — прежде барьер
    считался «открытым блокирующим вопросом», и страница COSMOS
    показывала «открытых блокирующих вопросов: 1» при нуле вопросов.
    Ключа `grounds.barriers` НЕ заводим намеренно: форма лестницы
    объявлена спецификацией (`form_version: 1`, ревизия 1.12), гейт и
    верификатор COSMOS сверяют клетку ПОБАЙТНО независимым пересчётом,
    а замороженные пакеты 1.18 (наборы H0/H0-B/H0-C) несут старую форму
    — новый ключ покраснил бы их все. `notes` же у пакета БЕЗ барьеров
    не меняется ни на байт, поэтому замороженные пакеты остаются
    зелёными. Ключ `families` с контрактом семьи — волна 18 (там и
    место структурным основаниям, вместе с подъёмом версии формы)."""
    matrix = m.readiness_matrix(doc, spec_version)
    # Основания барьеров — ТЕ ЖЕ, что у матрицы (иначе слова A05 попали
    # бы в счёт открытых вопросов анкеты: С-2 — барьер не вопрос).
    barrier_words = [pr["words"] for pr in
                     m.carrier_grounds(doc) + m.readiness_barriers(doc, spec_version)]
    modes, _objectives = allowed_modes(doc, spec_version)
    from cdpm.export_solver import _criterion_from_answer
    rules = [r for r in doc.get("rules", []) if isinstance(r, dict)]
    rules_by_id = {r.get("id"): r for r in rules}
    sections = _clean_sections(clean_data)
    columns: dict = {}
    for task, part in (("pairing", "3a"), ("rostering", "3b")):
        zones = ("PAIRING", "BOTH") if task == "pairing" \
            else ("ROSTERING", "BOTH")
        zone_total = sum(1 for r in rules if r.get("zone") in zones)
        strict = 0
        for q in doc.get("questions", []):
            if not isinstance(q, dict):
                continue
            a = q.get("answer") or {}
            if a.get("status") != "применён" \
                    or not str(a.get("raw") or "").startswith(
                        _STRICT_INTERIM_PREFIX):
                continue
            q_part = q.get("part")
            if q_part == part:
                strict += 1
            elif q_part not in ("3a", "3b"):
                refs = q.get("rule_refs") \
                    if isinstance(q.get("rule_refs"), list) else []
                q_zones = {rules_by_id.get(rid, {}).get("zone")
                           for rid in refs if isinstance(rid, str)}
                if q_zones & set(zones):
                    strict += 1
        cell = matrix.get(task) or {}
        hard_reasons = (cell.get("feasibility-hard") or {}) \
            .get("reasons") or {}
        soft_all = (cell.get("feasibility-soft") or {}) \
            .get("reasons") or {}
        # С-2: барьер — не вопрос анкеты; в счёт вопросов он не идёт, а
        # его слова едут ниже в `notes` (порядок — порядок расчёта
        # барьеров: семьи и параметры каталога, затем поддержка).
        held = [w for w in barrier_words
                if w in hard_reasons or w in soft_all]
        open_blocking = sum(n for tag, n in hard_reasons.items()
                            if tag not in barrier_words)
        open_soft = sum(v for k, v in soft_all.items()
                        if k not in hard_reasons and k not in barrier_words)
        opt_cell = cell.get("optimization") or {}
        crit = _criterion_from_answer(doc) if task == "pairing" \
            else None
        active_objs = list(opt_cell.get("objectives") or [])
        crit_defined = bool(active_objs) or crit is not None
        crit_source = (crit or {}).get("source_rule") \
            or (active_objs[0] if active_objs else None)
        missing = [s for s in _DATA_SECTIONS[task]
                   if s not in sections]
        task_modes = modes.get(task) or []
        if "optimization" in task_modes:
            state = "optimization"
        elif "feasibility-soft" in task_modes:
            state = "feasible_soft"
        elif "feasibility-hard" in task_modes:
            state = "feasible_hard"
        else:
            state = "stop"
        notes: list[str] = []
        # Основания барьеров (K01/K06) — словами, ПЕРВЫМИ: это то, из-за
        # чего столбец опущен, и потребителю их незачем выводить из
        # счётчиков (С-2: прежде они молча складывались в счёт открытых
        # вопросов и слов не оставляли вовсе).
        for words in held:
            notes.append(f"барьер: {words}")
        if missing:
            state = "stop"
            notes.append("нет обязательных разделов данных: "
                         + ", ".join(missing))
        # Авиакомпания ПОДТВЕРДИЛА, что критерия нет, а задача решаема:
        # сообщение о режиме едет в пакете (страница COSMOS показывает
        # его при нажатии кнопки; ИДАК H0-К, 2026-09-04). Текст — один
        # на весь путь, из матрицы корректности (m._NO_CRITERION_NOTE).
        # Только подтверждённый отказ: открытый или незаданный вопрос о
        # целевой — переходное состояние, а не сообщение о режиме, и
        # заметка меняла бы лестницу всех пакетов без целевой части
        # (гейт и верификатор COSMOS сверяют пересчёт побайтно).
        if not crit_defined and state in ("feasible_soft", "feasible_hard") \
                and opt_cell.get("state") == "отказ подтверждён":
            notes.append(str(opt_cell.get("note") or m._NO_CRITERION_NOTE))
        if strict:
            notes.append("временные строгие ответы анкеты = строгие "
                         "трактовки внутри состояния")
        columns[task] = {
            "state": state,
            "grounds": {
                "questions": {"zone_total": zone_total,
                              "open_blocking": open_blocking,
                              "open_soft": open_soft,
                              "strict_interim_answers": strict},
                "criterion": {"defined": crit_defined,
                              "source_rule": crit_source},
                "data_declared": {"complete": not missing,
                                  "missing_fields": missing},
            },
            "notes": notes,
        }
    return {"form_version": 1,
            "model_revision": int((doc.get("meta") or {})
                                  .get("revision", 1)),
            "columns": columns}


_RULE_MAPS_SECTIONS = ("crew_size_rules", "zone_executors",
                       "rule_to_constraint", "objective_to_constraint",
                       "acceptance_rules")


def _rule_maps_for(doc: dict):
    """Секция rule_maps манифеста (ревизия 1.14, пакет Б): карты
    «правило свода → имя ограничения решателя» — данные КОНТУРА,
    носитель cdpm-rule-maps.json (переопределение CDPM_RULE_MAPS;
    пусто = носителя нет). Нет носителя = секции нет (потребитель,
    которому карта нужна, честно отказывает словами). Принадлежность
    контуру — поле customers: СПИСОК ТОЧНЫХ значений meta.customer
    (дефект 2 репетиции демо 2026-08-29: подстрока не разводила миры
    «Полярис» и «Полярис-нано», а их координатные id совпадают при
    разных правилах — молчаливая подмена исполнителя); чужая карта в
    чужой пакет не попадает (секция законно не кладётся). Кривой
    носитель = честный отказ сборки."""
    import os
    override = os.environ.get("CDPM_RULE_MAPS")
    if override is not None:
        if not override.strip():
            return None
        paths = [Path(override)] if Path(override).is_file() else []
    else:
        import _data_paths
        # Семейство носителей контура: по одному на мир (дефект 2
        # репетиции: один файл не обслуживает несколько миров).
        paths = sorted(_data_paths.data_root()
                       .glob("cdpm-rule-maps*.json"))
    customer = str((doc.get("meta") or {}).get("customer") or "")
    matched: list[tuple[Path, dict]] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:                            # noqa: BLE001
            raise TaskPackageError(
                f"носитель карт правил не читается "
                f"({path.name}): {e}")
        if not isinstance(raw, dict):
            raise TaskPackageError(
                f"носитель карт правил ({path.name}): форма "
                f"нарушена — ожидается объект")
        customers = raw.get("customers")
        if not isinstance(customers, list) or not customers \
                or not all(isinstance(x, str) and x.strip()
                           for x in customers):
            raise TaskPackageError(
                f"носитель карт правил ({path.name}) без customers "
                f"(список ТОЧНЫХ значений meta.customer) — "
                f"принадлежность контуру не объявлена")
        if customer in customers:
            matched.append((path, raw))
    if not matched:
        return None
    if len(matched) > 1:
        names = ", ".join(p.name for p, _ in matched)
        raise TaskPackageError(
            f"заказчик {customer!r} объявлен в НЕСКОЛЬКИХ носителях "
            f"карт ({names}) — двусмысленность, уберите дубль")
    raw = matched[0][1]
    out = {k: raw[k] for k in _RULE_MAPS_SECTIONS if k in raw}
    return out or None


def excluded_rules_slice(doc: dict) -> dict:
    """Модель без правил с пометкой «исключено до получения данных по
    решению авиакомпании» (структура ВМП 2026-09-02). Один предикат
    для сборщика пакета и его гейта: машинные файлы (целевая, формы
    порогов, контрпримеры логики) строятся и пересобираются по одному
    и тому же срезу (аудит №2)."""
    rules = doc.get("rules") or []
    kept = [r for r in rules
            if not (isinstance(r, dict) and r.get("excluded_until_data"))]
    return doc if len(kept) == len(rules) else dict(doc, rules=kept)


_AIRPORT_SHEETS = ("аэропорты", "airports", "stations")
_TZ_COLUMNS = ("timezone", "time_zone", "tz", "часовой пояс", "пояс")
_CODE_COLUMNS = ("код", "code", "iata", "аэропорт", "airport")
_MAIN_BASE_KEYS = ("главная база", "main_base", "main base", "база")


def time_declaration_of(doc: dict, clean_data: dict | None) -> dict | None:
    """Объявление системы времени пакета (ревизия 1.16, аудит A9).
    time_scale — из параметра base_tz_offset_min правила семьи time_scale
    («метки местные, сдвиг 0» → base-local; ненулевой сдвиг → shifted с
    числом); base_time_zone — IANA-имя главной базы из листа
    «Аэропорты» очищенных данных: единственная зона всех аэропортов
    либо зона кода из свойства «главная база»; неоднозначно — ключа нет
    (fail-closed, не угадываем). Без правила шкалы — None (ключей нет)."""
    offset = None
    source_rule = None
    for r in doc.get("rules", []):
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("family") == "time_scale":
            cell = (fit.get("params") or {}).get("base_tz_offset_min")
            if isinstance(cell, dict) and isinstance(cell.get("value"), int) \
                    and not isinstance(cell.get("value"), bool):
                offset, source_rule = cell["value"], r.get("id")
                break
    if offset is None:
        return None
    out: dict = {"time_scale": "base-local" if offset == 0 else "shifted",
                 "base_tz_offset_min": offset, "source_rule": source_rule}
    zones: dict[str, str] = {}
    main_base = None
    tables = clean_data.get("tables") if isinstance(clean_data, dict) else None
    for sheets in (tables or {}).values():
        if not isinstance(sheets, dict):
            continue
        for name, table in sheets.items():
            if not isinstance(table, dict):
                continue
            low = str(name).strip().lower()
            rows = table.get("rows") or []
            if low in _AIRPORT_SHEETS:
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = next((str(v).strip() for k, v in row.items()
                                 if str(k).strip().lower() in _CODE_COLUMNS
                                 and v not in (None, "")), None)
                    tz = next((str(v).strip() for k, v in row.items()
                               if str(k).strip().lower() in _TZ_COLUMNS
                               and v not in (None, "")), None)
                    if code and tz:
                        zones[code] = tz
            elif low in ("свойства", "properties"):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    vals = list(row.values())
                    if len(vals) >= 2 and str(vals[0]).strip().lower() \
                            in _MAIN_BASE_KEYS and vals[1] not in (None, ""):
                        main_base = str(vals[1]).strip()
    distinct = sorted(set(zones.values()))
    if len(distinct) == 1:
        out["base_time_zone"] = distinct[0]
        out["base_time_zone_basis"] = "единственный пояс всех аэропортов листа"
    elif main_base and main_base in zones:
        out["base_time_zone"] = zones[main_base]
        out["base_time_zone_basis"] = f"пояс главной базы {main_base} по свойству набора"
    return out


def _status_of(run_dir: Path) -> dict:
    """Статус прогона словарём; пустой словарь — файла нет либо он
    нечитаем. Один читатель у имени прогона в манифесте и у поиска
    источника повтора: сборщик пакета остаётся годен там, где панели
    нет, поэтому статус читается файлом, а не `webui._read_status`."""
    path = Path(run_dir) / "status.json"
    try:
        got = json.loads(path.read_text(encoding="utf-8")) \
            if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def replay_source_dir(run_dir: Path) -> Path | None:
    """Каталог прогона-ИСТОЧНИКА у потомка повтора (None — прогон
    первичный). Паспорт повтора и правило поиска источника — те же, что
    у водителя повтора (`replay.replay_of`, `replay.source_dir_of`):
    второго закона о корне прогонов у сборщика пакета нет."""
    from cdpm import replay as replay_mod
    return replay_mod.source_dir_of(Path(run_dir), _status_of(run_dir))


def package_run_name(run_dir: Path) -> str:
    """Имя прогона для манифеста: у потомка повтора «из журнала» — имя
    источника (пакет байт в байт с исходным, 2.15), иначе своё."""
    from cdpm import replay as replay_mod
    rep = replay_mod.replay_of(_status_of(run_dir)) or {}
    if rep.get("mode") == "replay-journal" and rep.get("source_run"):
        return str(rep["source_run"])
    return Path(run_dir).name


def made_by_of(run_dir: Path) -> dict | None:
    """Ключ манифеста made_by по статусу и каталогу прогона (Дополнение к
    1.18): тот же код, что показывает паспорт прогона в панели
    (cdpm/run_passport.py). Нет статуса или он нечитаем — None."""
    from cdpm import run_passport as passport_mod
    path = Path(run_dir) / "status.json"
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    return passport_mod.made_by_of(Path(run_dir), status)


def catalog_stamp_of() -> dict:
    """Ключ манифеста `catalog` (Дополнение к 1.18, второе, 07.09.2026):
    каталог шаблонов, КОТОРЫМ ВЫПУЩЕН пакет — ровно два поля, версия и
    отпечаток. Источник — одна отметка моста (`catalog_bridge.stamp()`),
    тот же экземпляр каталога, которым код сажает правила: у строк
    (`template_fit.registry_rev`) и у манифеста отпечаток совпадает по
    построению, а не по добросовестности сборщика.

    Шаг 7 переезда: ветка режима среза базы снята — отметка ВСЕГДА у
    каталога, переменная среза продуктом CDPM не читается (§3 проекта:
    до части COSMOS 7-Б мост её ни уважает, ни отвергает; её имя в
    `cdpm/**` запрещено гейтом источника). Без каталога отметки нет —
    мост отказывает словами, и
    выпуск пакета останавливается там, а не кладёт пакет без ключа."""
    from cdpm import catalog_bridge as bridge
    return bridge.stamp()


def fits_catalog_of(doc: dict, catalog: dict | None) -> dict | None:
    """Ключ манифеста `fits_catalog` (Дополнение к 1.18, второе) —
    сводка единства ревизий посадок ПО СТРОКАМ ПАКЕТА: какие
    `template_fit.registry_rev` встретились, одна ли это ревизия и та ли
    она, которой пакет выпущен. Это ПРЕДУПРЕЖДЕНИЕ, а не отказ: пакет с
    посадками прежнего каталога (в том числе 12-значными до переезда)
    выпускается и принимается — потребитель видит словами, что строки
    сажались не тем каталогом, и решает сам.

    Ни одной посадки с ревизией — ключа нет (судить нечего). Нет ключа
    `catalog` — `matches_catalog` null: сравнивать не с чем."""
    revs = set()
    for r in doc.get("rules") or []:
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if not isinstance(fit, dict):
            continue
        rev = fit.get("registry_rev")
        if isinstance(rev, str) and rev.strip():
            revs.add(rev.strip())
    if not revs:
        return None
    ordered = sorted(revs)                    # порядок машинный: сортировка
    uniform = len(ordered) == 1
    fp = (catalog or {}).get("fingerprint")
    if not fp:
        matches = None
        note = ("посадки разных ревизий каталога: " + ", ".join(ordered)
                if not uniform else None)
    elif not uniform:
        matches = False
        note = "посадки разных ревизий каталога: " + ", ".join(ordered)
    else:
        matches = ordered[0] == fp
        note = None if matches else \
            f"посадки по прежнему каталогу (rev {ordered[0]})"
    return {"revs": ordered, "uniform": uniform,
            "matches_catalog": matches, "note": note}


def kit_scope_of(run_dir: Path) -> dict | None:
    """Ключ манифеста kit по статусу прогона: класс данных и
    ДЕЙСТВУЮЩИЙ круг моделей по правилу признака комплекта (cdpm/kit.py —
    тот же код, что судит панель). Нет статуса или он нечитаем — None:
    пакет без ключа, потребитель обязан судить «только локальные»."""
    from cdpm.kit import package_kit
    path = Path(run_dir) / "status.json"
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return package_kit(status)


def coverage_gap_problem(doc: dict) -> str | None:
    """Пробел покрытия (слово владельца 09.09.2026, набор Н0-C): пока у
    машинного правила нет шаблона (посадка C), пакет постановки не
    выдаётся — исполнять правило некому, и «готовый» пакет молча
    потерял бы его. Путь: стоп → заявка в каталог → шаблон → повтор
    прогона. None — пробела нет."""
    gaps = m.coverage_gap_rules(doc)
    if not gaps:
        return None
    ids = ", ".join(str(r.get("id")) for r in gaps[:6])
    if len(gaps) > 6:
        ids += f" и ещё {len(gaps) - 6}"
    return (f"пробел покрытия: у правил {ids} нет шаблона в каталоге "
            f"(посадка C) — пакет постановки не выдаётся до выпуска "
            f"шаблона: стоп → заявка в каталог → шаблон → повтор прогона")


#: Публичный адрес панели для ключа `manifest.record_api` (шаг 2 проекта
#: «Запись о прогоне», 11.2): им COSMOS дописывает записи этапов
#: хозяину. Переменная старше адреса запроса — за обратным прокси адрес
#: запроса внутренний.
PUBLIC_URL_ENV = "CDPM_PUBLIC_URL"


def record_api_of(run_dir: Path, base_url: str | None = None) -> str | None:
    """Полный адрес записи о прогоне `<база>/api/runs/<run>/record`
    (append — тот же адрес плюс `/append`). База — переменная
    `CDPM_PUBLIC_URL`, иначе адрес запроса на выпуск пакета; нет ни того,
    ни другого — ключа в манифесте нет (пакет читается прежним законом,
    у COSMOS остаётся запасной путь — переменная площадки)."""
    base = (os.environ.get(PUBLIC_URL_ENV) or "").strip() or (base_url or "")
    base = str(base).strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/runs/{Path(run_dir).name}/record"


def _panel_smt_channel(run_dir: Path):
    """Умолчание шва `smt_channel_factory`: канал переводчика берётся
    ТОЙ ЖЕ горловиной каналов, что у кнопки панели (`webui._channel_for`
    через `webui._smt_gate_channel`), — роль, класс данных, признак
    комплекта и журнал вызовов у гейта и у кнопки одни. Импорт панели
    ленивый: сборщик пакета остаётся годен там, где панели нет (гейт
    производителя, проверка замороженного пакета)."""
    from cdpm import webui
    return webui._smt_gate_channel(run_dir)


#: Чем гейт непротиворечивости берёт переводчика SMT: `fn(run_dir) -> llm`.
#: Умолчание — канал панели; тесты ставят подменного переводчика, а
#: площадка без панели получает честный отказ «канал не построился».
smt_channel_factory = _panel_smt_channel


def _panel_smt_scope(run_dir: Path):
    """Умолчание шва `smt_check_scope`: проверка рубежа идёт ДЕЙСТВИЕМ
    журнала прогона — тем же «smt-verify», что у кнопки панели
    (`webui._smt_gate_scope`). Внутри области у вызова переводчика есть
    номер действия, и запись о прогоне считает его наравне с прочими
    вызовами моделей: без области вызов ложился в журнал «ничьим»
    (живая находка run-0163 11.09.2026 — 26 сочтённых вызовов из 27).
    Импорт панели ленивый, как у `smt_channel_factory`: там, где панели
    нет (гейт производителя, проверка замороженного пакета), область
    пуста и проверка идёт, как шла."""
    from cdpm import webui
    return webui._smt_gate_scope(run_dir)


#: Чем гейт непротиворечивости ОБЪЯВЛЯЕТ свою проверку:
#: `fn(run_dir) -> контекст`. Умолчание — действие журнала панели;
#: тесты ставят свою область, а площадка без панели получает пустую
#: (`contextlib.nullcontext`) — учёт действия проверку не отменяет.
smt_check_scope = _panel_smt_scope


def _check_scope(run_dir: Path):
    """Область проверки по шву `smt_check_scope`; шва нет или он не
    построился — пустая область: рубеж судит свод, а не журнал, и
    несостоявшаяся запись действия выпуск не роняет (как отказ записи
    о прогоне — печатается и догоняет следующим действием)."""
    scope = smt_check_scope
    if scope is None:
        return contextlib.nullcontext()
    try:
        return scope(Path(run_dir)) or contextlib.nullcontext()
    except Exception as e:                                # noqa: BLE001
        print(f"CDPM: {Path(run_dir).name}: проверка непротиворечивости не "
              f"записана действием ({str(e)[:200]})", flush=True)
        return contextlib.nullcontext()


@dataclass(frozen=True)
class _StandExportContext:
    """Call-local evidence created by the synthetic stand builder, never JSON input.

    This is an internal API boundary, not authentication against Python code
    execution. No HTTP, environment or received-manifest flag constructs it.
    """
    run_dir: Path
    model_bytes: bytes
    source_bytes: bytes


def _check_stand_context(context, run_dir: Path, canon_bytes: bytes) -> str | None:
    if context is None:
        return None
    try:
        if type(context) is not _StandExportContext:
            raise ValueError("нет контекста стендового вызова")
        if Path(context.run_dir).resolve() != Path(run_dir).resolve() \
                or context.model_bytes != canon_bytes:
            raise ValueError("контекст не о данном прогоне/своде")
        if (Path(run_dir) / "extract" / "model.json").read_bytes() != canon_bytes:
            raise ValueError("сохранённый свод изменился")
        source = (Path(run_dir) / "extract" / "stand-world.json").read_bytes()
        if source != context.source_bytes:
            raise ValueError("исходный стендовый мир изменился")
        src = m.loads_strict(source)
        doc = m.loads_strict(canon_bytes)
        stamp = hashlib.sha256(source).hexdigest()
        if not isinstance(src, dict) or set(src) != {"стенд"} \
                or not isinstance(src["стенд"], dict) \
                or src["стенд"].get("kind") != "synthetic-code-world" \
                or not isinstance(src["стенд"].get("world"), dict) \
                or not isinstance(src["стенд"].get("rules"), list) \
                or (doc.get("meta") or {}).get("customer") != "стенд" \
                or doc.get("sources") != [{"sid": "S1", "file": "stand-world.json",
                                           "sha256": stamp}]:
            raise ValueError("это не свод синтетического мира стенда")
        return stamp
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        raise TaskPackageError(f"стендовый контекст не подтверждён: {exc}",
                               "stand-context") from None


def _stand_untranslatable_exception(report: dict, completeness: dict,
                                    source_sha: str | None) -> dict | None:
    """Owner decision 18.09: only explicit refusals in an isolated stand.

    Missing/foreign/malformed rows are not honest refusals. A mixed translated
    subset must actually parse and be SAT; its result is never a full verdict.
    Customer export never reaches this exception without the call-local proof.
    """
    if source_sha is None or report.get("verdict") != "none" \
            or any(completeness[k] for k in ("foreign", "duplicate", "bad", "malformed")):
        return None
    rows = report.get("translations") or []
    refused = [r for r in rows if r.get("status") == "untranslatable"]
    if not refused or len(rows) != completeness["n_rules"] \
            or any(r.get("status") not in ("translated", "untranslatable") for r in rows) \
            or {r["rule"] for r in refused} != set(completeness["missing"]):
        return None
    if any(not isinstance(r.get("note"), str) or not r["note"].strip()
           or r["note"].strip() in ("причина не названа", "...", "…", "-") or "smt" in r
           for r in refused):
        return None
    decls = report.get("decls")
    if not isinstance(decls, list) or any(not isinstance(d, str) for d in decls):
        raise TaskPackageError("стенд: в отчёте нет исходных объявлений SMT; "
                               "получите новый отчёт", SMT_UNAVAILABLE)
    from cdpm import smt_verify as smt_mod
    translated = [{"rule": r["rule"], "smt": r["smt"]}
                  for r in rows if r["status"] == "translated"]
    try:
        partial, _core = smt_mod._run_z3(smt_mod.build_smt_script(decls, translated))
    except smt_mod.SmtVerifyError as exc:
        raise TaskPackageError(f"стенд: перевод не прошёл парсер SMT: {exc}",
                               SMT_UNAVAILABLE) from None
    if partial != "sat":
        raise TaskPackageError(f"стенд: переведённая часть дала {partial}, выпуск остановлен",
                               SMT_CONTRADICTION if partial == "unsat" else SMT_UNAVAILABLE)
    return {"policy": "stand-untranslatable/1", "source_sha256": source_sha,
            "rules": [{"rule": r["rule"], "note": r["note"]} for r in refused]}


def consistency_of(run_dir: Path, canon_bytes: bytes,
                   released_doc: dict | None = None, *,
                   _stand_context: _StandExportContext | None = None) -> dict:
    """РУБЕЖ непротиворечивости (СМТ-1, слово владельца 11.09.2026):
    пакет постановки не выпускается без свежего машинного вердикта о
    совместности жёстких правил свода. Возвращает раздел манифеста
    `consistency`; красный вердикт и невозможность проверки —
    `TaskPackageError` с кодом.

    Свежесть — по отпечатку канонической модели: отчёт кнопки панели
    (`extract/smt-report.json`) годится ровно тогда, когда он о ТОМ ЖЕ
    своде. Отчёта нет или он о прежнем своде — гейт проверяет свод сам
    (тем же `smt_verify.verify` и тем же каналом роли) и сохраняет
    отчёт: выпуск не отсылает владельца нажимать кнопку, но и не
    выпускает пакет на слово.

    У ПОТОМКА ПОВТОРА своего отчёта обычно нет, а модель звать нечем:
    вызовы повтора «из журнала» отвечаются из журнала источника, и
    запроса переводчика там не было. Поэтому перед вызовом модели гейт
    берёт вердикт ИСТОЧНИКА (`_report_from_replay_source`) — законно
    ровно потому, что своды потомка и источника байт-идентичны (Ф-1.7)
    и отпечаток отчёта это доказывает.

    Судится ВЫПУСКАЕМЫЙ свод (A01-F1 аудита, 12.09.2026): `released_doc`
    — модель после всех преобразований сборки (ярлыки по каталогу
    SOFT→HARD, семьи, поля 1.15), та, что ложится в model.json пакета.
    Предмет проверки (`smt_verify.subject_of`: идентификаторы, виды и
    формулировки жёстких правил) сравнивается с предметом канонического
    свода:
    - предметы равны (обычный случай) — отчёт кнопки о том же самом
      наборе правил; он и берётся, лишнего вызова модели нет. Свеж он
      по отпечатку канонической модели И по отпечатку предмета: отчёт,
      не назвавший своего предмета (форма до 12.09.2026), вердиктом о
      выпускаемом наборе правил не является — пересчёт. Устаревший
      отчёт ПАКЕТА (о предмете, которого в выпуске больше нет) здесь
      снимается: иначе он читался бы как отчёт о выпуске;
    - предметы разошлись (сборка сделала правило жёстким, сняла его,
      переформулировала) — отчёт кнопки о выпускаемом наборе НЕ судит:
      берётся отчёт ПАКЕТА (`extract/smt-report-package.json`), если он
      свеж для этого канонического свода и этого предмета; иначе рубеж
      проверяет выпускаемый свод сам тем же переводчиком и сохраняет
      отчёт пакета отдельно — отчёт кнопки не переписывается, у
      потомка повтора без модели — честный отказ со словами о различии.
    Без `released_doc` (прежние вызовы) выпускаемым считается сам
    канонический свод. В любом пути строки отчёта сверяются с точным
    набором жёстких правил ВЫПУСКАЕМОГО свода (`report_completeness`),
    а раздел манифеста называет предмет (`subject_sha256`, `subject`) и
    отчёт (`report`), которым вынесен вердикт."""
    from cdpm import smt_verify as smt_mod
    stand_source_sha = _check_stand_context(_stand_context, run_dir, canon_bytes)
    canon_doc = json.loads(canon_bytes.decode("utf-8"))
    if released_doc is None:
        released_doc = canon_doc
    model_sha = smt_mod.model_fingerprint(canon_bytes)
    subject_sha = smt_mod.subject_fingerprint(released_doc)
    changes: list[str] = []
    if subject_sha == smt_mod.subject_fingerprint(canon_doc):
        which, report_rel = smt_mod.CANONICAL, smt_mod.REPORT_REL
        # Отчёт ПАКЕТА о другом предмете (сборка прежнего выпуска
        # делала правило жёстким, а нынешняя — уже нет: каталог
        # шаблонов между выпусками сменился) о выпускаемом своде не
        # говорит и остаться в прогоне не вправе: иначе читатель
        # отчёта о выпуске (`smt_verify.read_report` без указания
        # какого) взял бы его вместо отчёта кнопки, которым вынесен
        # вердикт. Отчёт регенерируем и входным документом
        # пользователя не является (П0 §12.1) — устаревший снимается.
        _drop_stale_package_report(run_dir, subject_sha)
        report = smt_mod.read_report(run_dir, smt_mod.CANONICAL)
        # Свежесть — и о том же своде, и о том же ПРЕДМЕТЕ: отчёт, не
        # назвавший своего предмета (форма до 12.09.2026), вердиктом о
        # выпускаемом наборе правил не является — пересчёт (закон
        # §12.1: отчёт регенерируем, неизвестная форма — «не свеж»).
        if not smt_mod.is_fresh(report, model_sha, subject_sha):
            report = _report_from_replay_source(
                run_dir, model_sha, smt_mod.CANONICAL, subject_sha) \
                or _run_smt_check(run_dir, canon_doc, model_sha,
                                  subject_sha=subject_sha)
    else:
        which, report_rel = smt_mod.PACKAGE, smt_mod.PACKAGE_REPORT_REL
        changes = smt_mod.subject_changes(canon_doc, released_doc)
        report = smt_mod.read_report(run_dir, smt_mod.PACKAGE)
        if not smt_mod.is_fresh(report, model_sha, subject_sha):
            report = _report_from_replay_source(
                run_dir, model_sha, smt_mod.PACKAGE, subject_sha) \
                or _run_smt_check(run_dir, released_doc, model_sha,
                                  subject_sha=subject_sha, changes=changes,
                                  which=smt_mod.PACKAGE)
    tail = _subject_words(changes)
    verdict = str(report.get("verdict") or "")
    shape = smt_mod.report_shape_problems(report)
    if shape:
        raise TaskPackageError(
            "выпуск пакета остановлен: форма отчёта непротиворечивости "
            f"{report_rel} нарушена — " + "; ".join(p["words"] for p in shape)
            + f". Получите корректный отчёт и повторите выпуск.{tail}",
            SMT_UNAVAILABLE, [p["path"] for p in shape])
    # A01 (REPLY-AUDITOR-01): счётчикам отчёта рубеж не верит — точный
    # набор жёстких правил ВЫПУСКАЕМОГО свода сверяется со строками
    # отчёта (пропущенные, лишние, повторные, без тела — неполнота;
    # записи вне формы — A01-F2 — тоже).
    completeness = smt_mod.report_completeness(report, released_doc)
    n_rules = completeness["n_rules"]
    n_translated = completeness["n_translated"]
    if verdict == "unsat":
        core = [str(x) for x in report.get("core") or []]
        named = ", ".join(core[:6]) + (f" и ещё {len(core) - 6}"
                                       if len(core) > 6 else "")
        raise TaskPackageError(
            f"выпуск пакета остановлен: противоречие правил по SMT — "
            f"ядро {named or 'не названо решателем'} (эти правила нельзя "
            f"выполнить одновременно). Поправьте формулировки строк свода "
            f"или ответьте на вопрос части 1 анкеты, снимающий конфликт, и "
            f"повторите выпуск; перевод каждого правила — в отчёте "
            f"{report_rel}.{tail}", SMT_CONTRADICTION)
    stand_exception = _stand_untranslatable_exception(report, completeness, stand_source_sha)
    if stand_exception is not None:
        return {"verdict": "none", "model_sha256": model_sha,
                "n_rules": n_rules, "n_translated": n_translated,
                "translator": report.get("translator") or {
                    "role": smt_mod.TRANSLATOR_ROLE, "provider": None, "model": None},
                "report": report_rel, "subject_sha256": subject_sha, "subject": which,
                "stand_exception": stand_exception}
    if (n_rules and (n_translated < n_rules or completeness["foreign"])) \
            or completeness["malformed"]:
        # A01: переведены не все жёсткие правила выпускаемого свода
        # (либо отчёт говорит о чужих правилах, либо в ответе переводчика
        # сохранились записи вне формы) — «sat» по части свода вердиктом
        # не является; правила и позиции называются.
        untranslated = list(completeness["missing"])
        items = untranslated + list(completeness["foreign"]) \
            + list(completeness["malformed"])
        named = ", ".join(untranslated[:8]) + (
            f" и ещё {len(untranslated) - 8}" if len(untranslated) > 8
            else "")
        extra = ""
        if completeness["foreign"]:
            extra = (" лишние записи (правила свода им не соответствуют; "
                     "запись без идентификатора названа позицией): "
                     + ", ".join(completeness["foreign"][:8]) + ".")
        if completeness["duplicate"]:
            extra += (" повторные строки отчёта: "
                      + ", ".join(completeness["duplicate"][:8]) + ".")
        if completeness["malformed"]:
            extra += (" записи вне формы в ответе переводчика: "
                      + "; ".join(completeness["malformed_words"][:8])
                      + " — такой ответ переводом не считается.")
        advice = ("Упростите формулировки непереведённых правил или "
                  if untranslated else "")
        raise TaskPackageError(
            f"выпуск пакета остановлен: перевод свода в SMT "
            + ("неполон" if untranslated else "неточен")
            + f" — переведено {n_translated} из {n_rules} жёстких правил"
            + (f", не переведены: {named}" if untranslated else "")
            + f".{extra} {advice}запустите «Проверить непротиворечивость» "
            f"в блоке «Итог» и посмотрите отчёт {report_rel}.{tail}",
            SMT_INCOMPLETE, items)
    if verdict != "sat":
        why = report.get("status") or verdict or "вердикт пуст"
        raise TaskPackageError(
            f"выпуск пакета остановлен: вердикта о непротиворечивости нет — "
            f"{why}. Упростите формулировки жёстких правил или запустите "
            f"«Проверить непротиворечивость» в блоке «Итог» и посмотрите "
            f"отчёт {report_rel}.{tail}", SMT_UNAVAILABLE)
    # Ноль утверждений при непустом своде — частный случай неполного
    # перевода (A01): отказ `smt-incomplete` выше называет все правила.
    # Замок машиной здесь — явный отказ, а не assert (под `python -O`
    # assert исчезает, и замок пропал бы вместе с ним).
    if n_rules and not n_translated:
        raise TaskPackageError(
            f"выпуск пакета остановлен: ни одно из {n_rules} жёстких "
            f"правил выпускаемого свода не переведено — вердикта о "
            f"непротиворечивости по такому отчёту нет; запустите "
            f"«Проверить непротиворечивость» в блоке «Итог» и посмотрите "
            f"отчёт {report_rel}.{tail}", SMT_INCOMPLETE,
            list(completeness["missing"]))
    return {
        # «none» — жёстких правил в своде нет, проверять было нечего;
        # «sat» — переведённые утверждения совместны.
        "verdict": "none" if not n_rules else "sat",
        "model_sha256": model_sha,
        "n_rules": n_rules,
        "n_translated": n_translated,
        "translator": report.get("translator")
        or {"role": smt_mod.TRANSLATOR_ROLE, "provider": None,
            "model": None},
        # Каким отчётом вынесен вердикт (путь в прогоне) и о каком
        # предмете он: отпечаток жёстких правил ВЫПУСКАЕМОГО model.json
        # (A01-F1) и слово — «canonical» (предмет равен каноническому,
        # отчёт кнопки) либо «package» (сборка изменила предмет, отчёт
        # пакета).
        "report": report_rel,
        "subject_sha256": subject_sha,
        "subject": which,
    }


def _drop_stale_package_report(run_dir: Path, subject_sha: str) -> bool:
    """Снять отчёт ПАКЕТА, который не о выпускаемом предмете (A01-F1).

    Отчёт пакета существует ровно ради случая «сборка изменила набор
    жёстких правил». Стоит предмету выпуска вернуться к каноническому
    (сменился каталог шаблонов — правило больше не становится жёстким),
    как прежний отчёт пакета говорит о наборе правил, которого в
    выпуске нет. Молчаливо оставленный, он становится «зелёным»
    производным о другом предмете: `smt_verify.read_report` без
    указания какого читает отчёт О ВЫПУСКЕ и взял бы именно его.
    Возвращает True, если файл снят."""
    from cdpm import smt_verify as smt_mod
    path = smt_mod.report_path(run_dir, smt_mod.PACKAGE)
    stale = smt_mod.read_report(run_dir, smt_mod.PACKAGE)
    if stale is None or stale.get("subject_sha256") == subject_sha:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _subject_words(changes: list[str]) -> str:
    """Приписка к отказу рубежа, когда выпускаемый свод отличается от
    канонического (A01-F1): называет правила, чей вид или формулировка
    изменились при сборке, — иначе слова зовут к кнопке, чей отчёт о
    выпускаемом наборе не судит."""
    from cdpm import smt_verify as smt_mod
    if not changes:
        return ""
    shown = "; ".join(changes[:6]) + (f" и ещё {len(changes) - 6}"
                                      if len(changes) > 6 else "")
    return (f" Выпускаемый свод отличается от канонического (того, что "
            f"судит кнопка «Проверить непротиворечивость»): {shown} — "
            f"отчёт кнопки о выпускаемом наборе правил не судит, рубеж "
            f"проверяет выпускаемый свод отдельно (отчёт пакета "
            f"{smt_mod.PACKAGE_REPORT_REL}).")


def _report_write_problem(run_dir: Path, which: str) -> str | None:
    """Чем отчёт проверки НЕВОЗМОЖНО положить на своё место — словами;
    None — путь свободен и записываем.

    Судится ДО вызова переводчика нарочно (П0 §1.4/§12.1: беда самой
    проверки — отказ словами с машинным кодом, а не молчание и не
    падение наружу): каталог на месте файла отчёта и каталог прогона,
    в котором не создать `extract`, видны заранее — тратить на такой
    выпуск вызов модели незачем. Беды, заранее не видные (файл только
    для чтения, кончился диск), ловит сама запись
    (`_write_report_or_refuse`)."""
    from cdpm import smt_verify as smt_mod
    path = smt_mod.report_path(run_dir, which)
    if path.exists() and not path.is_file():
        return (f"путь отчёта {path} занят не файлом (каталог либо иной "
                f"объект файловой системы)")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"каталог отчёта {path.parent} не создаётся — {e}"
    return None


def _write_report_or_refuse(run_dir: Path, report: dict, which: str,
                            tail: str = "") -> None:
    """Отчёт проверки на диск; беда записи — ОТКАЗ выпуска словами с
    кодом `smt-unavailable`, а не `OSError` наружу из сборки.

    Находка неавторского ревью 12.09.2026: ручка панели ловит только
    `TaskPackageError`, и незаписанный отчёт (каталог на месте файла)
    давал HTTP 500 — уже ПОСЛЕ потраченного вызова модели и уже после
    того, как журнал действий записал «отказ 409»: журнал и ответ
    расходились. Теперь любая беда записи — тот же отказ с кодом, что
    и прочие беды проверки."""
    from cdpm import smt_verify as smt_mod
    try:
        smt_mod.write_report(run_dir, report, which)
    except OSError as e:
        raise TaskPackageError(
            f"выпуск пакета остановлен: вердикт о непротиворечивости "
            f"получен, но отчёт {smt_mod.report_path(run_dir, which)} не "
            f"записан — {type(e).__name__}: {str(e)[:200]}. Освободите "
            f"путь отчёта (на его месте каталог или файл только для "
            f"чтения) и повторите выпуск{tail}", SMT_UNAVAILABLE) from None


def _report_from_replay_source(run_dir: Path, model_sha: str,
                               which: str | None = None,
                               subject_sha: str | None = None) -> dict | None:
    """Вердикт ИСТОЧНИКА у потомка повтора, если он о ТОМ ЖЕ своде.

    Потомок повторяет стадии источника, и его свод байт в байт равен
    исходному (Ф-1.7) — отпечаток отчёта это и доказывает: свежий по
    `is_fresh` отчёт источника есть вердикт и о своде потомка, звать
    переводчика второй раз незачем (а у повтора «из журнала» и нечем:
    ответы моделей берутся из журнала источника, запроса переводчика в
    нём нет). Отчёт копируется в потомка `write_report` — он остаётся
    ключом `manifest.consistency.report` в СВОЁМ прогоне. Копия не
    легла (путь отчёта у потомка занят) — отказ выпуска словами с
    кодом `smt-unavailable` (`_write_report_or_refuse`), а не
    `OSError` наружу: вердикта у потомка нет, и молча выпускать его
    нельзя.

    `which` — какой отчёт источника берётся: кнопки (`CANONICAL`,
    умолчание) либо ПАКЕТА (`PACKAGE` — A01-F1: выпускаемый свод
    отличается от канонического, и отчёт кнопки источника тут не
    судья). `subject_sha` — предмет, о котором отчёт ОБЯЗАН быть: у
    рубежа он задан всегда (отчёт, не назвавший своего предмета, о
    выпускаемом наборе правил не свидетельствует).

    None — прогон первичный, каталога источника нет либо его отчёт о
    другом своде/предмете: тогда гейт идёт прежним путём (вызов модели)."""
    from cdpm import smt_verify as smt_mod
    source = replay_source_dir(run_dir)
    if source is None or not Path(source).is_dir():
        return None
    which = which or smt_mod.CANONICAL
    report = smt_mod.read_report(source, which)
    if not smt_mod.is_fresh(report, model_sha, subject_sha):
        return None
    # Приписки `_replay_words` тут нет нарочно: у источника вердикт
    # ЕСТЬ, и звать его нажимать кнопку незачем — беда в самом пути
    # отчёта у потомка, о ней слова и говорят.
    _write_report_or_refuse(run_dir, report, which)
    return report


def take_replay_source_verdict(run_dir: Path) -> bool:
    """Взять вердикт ИСТОЧНИКА в потомка повтора ЗАРАНЕЕ — тем же
    законом, каким его берёт рубеж выпуска (`_report_from_replay_source`:
    отчёт годен, пока он о том же своде). True — отчёт источника лёг в
    потомка.

    Зовёт водитель повтора, встретив в журнале источника действие рубежа
    («smt-verify» происхождения `actions_journal.SMT_GATE_ORIGIN`):
    воспроизводить проверку нечестно — она позвала бы модель, переписала
    бы отчёт СВОИМ моментом проверки и заняла линию SMT, которой у
    источника нет. Отчёт же у потомка появляется тогда же, когда и у
    источника, — и сверка артефактов видит его на своём месте, а не
    «только у источника»."""
    run_dir = Path(run_dir)
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        return False
    from cdpm import smt_verify as smt_mod
    model_sha = smt_mod.model_fingerprint(model_path.read_bytes())
    try:
        took = _report_from_replay_source(run_dir, model_sha) is not None
        # A01-F1: отчёт ПАКЕТА источника о том же каноническом своде
        # берётся заранее тем же законом (предмет выпуска сверит сам
        # рубеж при сборке потомка — свод байт в байт исходный); без
        # него потомок разошёлся бы с источником артефактом
        # extract/smt-report-package.json на ровном месте.
        source = replay_source_dir(run_dir)
        if source is not None and Path(source).is_dir():
            pkg = smt_mod.read_report(source, smt_mod.PACKAGE)
            if smt_mod.is_fresh(pkg, model_sha) and pkg.get("subject_sha256"):
                _write_report_or_refuse(run_dir, pkg, smt_mod.PACKAGE)
                took = True
    except TaskPackageError as e:
        # Помощник ЗАРАНЕЕ — не рубеж: отчёт не лёг (путь у потомка
        # занят) — повтор от этого не падает, а слова скажет рубеж
        # выпуска, когда за вердиктом придут (`consistency_of`).
        print(f"CDPM: {run_dir.name}: вердикт источника заранее не взят "
              f"({str(e)[:200]})", flush=True)
        return False
    return took


def _replay_words(run_dir: Path, package: bool = False) -> str:
    """Приписка к отказу рубежа у ПОТОМКА ПОВТОРА: до вызова модели гейт
    уже пытался взять вердикт источника и не смог, а сами слова отказа
    («запроса с таким отпечатком в журнале источника нет», «канал не
    построился») о повторе не говорят и зовут править свод — править у
    потомка нечего. У первичного прогона приписки нет. `package` —
    искали отчёт ПАКЕТА источника (A01-F1: выпускаемый свод отличается
    от канонического, и отчёт кнопки не судья): совет — выпустить пакет
    в источнике, а не нажимать там кнопку."""
    source = replay_source_dir(run_dir)
    if source is None:
        return ""
    if package:
        return (f" Прогон — повтор прогона {Path(source).name}: у потомка "
                f"вердикт берётся из отчёта источника, а отчёта ПАКЕТА о "
                f"том же выпускаемом своде у источника нет (выпускаемый "
                f"свод отличается от канонического, и отчёт кнопки о нём "
                f"не судит). Выпустите пакет В ИСТОЧНИКЕ — рубеж проверит "
                f"выпускаемый свод и сохранит отчёт пакета — и повторите "
                f"выпуск потомка: свод потомка байт в байт исходный, и "
                f"отчёт пакета источника — отчёт и о нём.")
    return (f" Прогон — повтор прогона {Path(source).name}: у потомка "
            f"вердикт берётся из отчёта источника, а свежего отчёта о том "
            f"же своде у источника нет. Получите вердикт кнопкой "
            f"«Проверить непротиворечивость» В ИСТОЧНИКЕ и повторите "
            f"выпуск: свод потомка байт в байт исходный, и вердикт "
            f"источника — вердикт о нём.")


def _run_smt_check(run_dir: Path, doc: dict, model_sha: str, *,
                   subject_sha: str | None = None,
                   changes: list[str] | None = None,
                   which: str | None = None) -> dict:
    """Проверка свода гейтом выпуска: решатель на площадке → канал
    переводчика → `smt_verify.verify` → отчёт прогона. Любая беда на
    этом пути — отказ выпуска словами с кодом `smt-unavailable`, а не
    молчаливое «проверку пропустили». У потомка повтора к словам
    добавляется приписка `_replay_words`: сюда он доходит только тогда,
    когда вердикта нет и у источника.

    `doc` — свод, который переводится: канонический (отчёт кнопки,
    `extract/smt-report.json`) либо выпускаемый (`which == PACKAGE` —
    A01-F1): тогда отчёт ложится ОТДЕЛЬНЫМ файлом
    `extract/smt-report-package.json` с перечнем отличий `changes`
    (`subject_changes`), отчёт кнопки не трогается, а слова отказов
    называют, чем выпускаемый свод отличается от канонического.
    `subject_sha` — предмет переводимого свода: отчёт обязан выйти
    именно о нём (проверяется машиной, оба пути).

    Канал и сама проверка строятся ВНУТРИ области `smt_check_scope`:
    область — действие журнала прогона («smt-verify», как у кнопки), и
    номер действия обёртка канала снимает в момент ПОСТРОЕНИЯ канала.
    Отсутствие решателя на площадке судится до области: делать в этом
    случае нечего, и действия в журнале не появляется. Тем же законом
    и до области судится ПУТЬ ОТЧЁТА (`_report_write_problem`,
    12.09.2026): отчёт, который некуда положить, делает проверку
    несостоявшейся — отказ выносится ДО вызова модели, и вызов не
    тратится."""
    from cdpm import smt_verify as smt_mod
    package = which == smt_mod.PACKAGE
    tail = _replay_words(run_dir, package) + _subject_words(changes or [])
    problem = smt_mod.z3_problem()
    if problem:
        raise TaskPackageError(
            f"выпуск пакета остановлен: непротиворечивость свода проверить "
            f"нечем — {problem}{tail}", SMT_UNAVAILABLE)
    trouble = _report_write_problem(
        run_dir, smt_mod.PACKAGE if package else smt_mod.CANONICAL)
    if trouble:
        raise TaskPackageError(
            f"выпуск пакета остановлен: вердикт о непротиворечивости "
            f"некуда положить — {trouble}. Освободите путь отчёта и "
            f"повторите выпуск{tail}", SMT_UNAVAILABLE)
    with _check_scope(run_dir):
        factory = smt_channel_factory
        try:
            llm = factory(run_dir) if factory is not None else None
        except Exception as e:                            # noqa: BLE001
            words = getattr(e, "detail", None) or str(e)
            raise TaskPackageError(
                f"выпуск пакета остановлен: канал переводчика SMT не "
                f"построился — {str(words)[:300]}. Объявите модель роли "
                f"«переводчик SMT» (узел роли → модель) и повторите "
                f"выпуск{tail}", SMT_UNAVAILABLE) from None
        if llm is None:
            raise TaskPackageError(
                "выпуск пакета остановлен: переводчика SMT некем вызвать — "
                "канал роли «переводчик SMT» не задан. Выпускайте пакет из "
                "панели либо сначала получите вердикт кнопкой «Проверить "
                f"непротиворечивость»{tail}", SMT_UNAVAILABLE)
        try:
            report = smt_mod.verify(doc, llm, model_sha256=model_sha)
        except Exception as e:                            # noqa: BLE001
            raise TaskPackageError(
                f"выпуск пакета остановлен: проверка непротиворечивости не "
                f"состоялась — {str(e)[:300]}. Поправьте формулировки "
                f"жёстких правил либо запустите «Проверить "
                f"непротиворечивость» в блоке «Итог» и посмотрите "
                f"отчёт{tail}", SMT_UNAVAILABLE) from None
        # Замок машиной — ЯВНЫЙ отказ, а не assert: под `python -O`
        # assert исчезает, и замок, стерегущий выпуск, исчез бы вместе
        # с ним (находка неавторского ревью 12.09.2026).
        if subject_sha is not None \
                and report.get("subject_sha256") != subject_sha:
            raise TaskPackageError(
                f"выпуск пакета остановлен: отчёт проверки — о ДРУГОМ "
                f"предмете, чем переведённый свод (предмет отчёта "
                f"{report.get('subject_sha256') or 'не назван'}, переведён "
                f"{subject_sha}): вердиктом о выпускаемом наборе жёстких "
                f"правил такой отчёт не является{tail}", SMT_UNAVAILABLE)
        if package:
            report["subject_changes"] = list(changes or [])
            _write_report_or_refuse(run_dir, report, smt_mod.PACKAGE, tail)
        else:
            _write_report_or_refuse(run_dir, report, smt_mod.CANONICAL, tail)
        return report


def _report_bytes_of(run_dir: Path, consistency: dict) -> bytes:
    """Байты отчёта, которым вынесен вердикт раздела `consistency` (путь
    `report` в прогоне): едут в пакет файлом `smt-report.json` — гейт
    производителя и аудитор сверяют его строки с жёсткими правилами
    model.json пакета, не доверяя счётчикам манифеста (A01-F1)."""
    path = Path(run_dir) / str(consistency.get("report") or "")
    try:
        return path.read_bytes()
    except OSError as e:
        raise TaskPackageError(
            f"выпуск пакета остановлен: отчёт проверки непротиворечивости "
            f"{consistency.get('report')!r} не читается ({e}) — без него "
            f"справка о непротиворечивости в пакете не проверяема",
            SMT_UNAVAILABLE) from None


def pending_revision_problem(doc: dict) -> tuple[str, str, list[str]] | None:
    """Отказ выпуска по открытому пересмотру (K04, §2.4 (а)) либо None.
    Один расчёт с готовностью и гейтом: `model.pending_revision_facts`.
    Возвращает (слова, код, перечень) для `TaskPackageError`. Перечень
    `items` — той же грамматики, что у K01: `<семья>/<параметр>`
    (`model.pending_revision_barriers`), а строки названы словами
    отказа — чтобы читатель перечня не различал два барьера."""
    facts = m.pending_revision_facts(doc)
    if not facts:
        return None
    items = [b["item"] for b in m.pending_revision_barriers(doc)]
    troubles = [f["words"] for f in facts if f.get("problem")]
    words = ("выпуск пакета остановлен: пересмотр открыт — "
             + m.pending_revision_words(facts)
             + ". Прежнее значение ячейки решением авиакомпании больше "
               "не является: ответьте на вопрос-пересмотр в анкете "
               "(или дождитесь, пока носитель параметра исчезнет) и "
               "повторите выпуск")
    if troubles:
        words += ". Беды признака пересмотра: " + "; ".join(troubles[:3])
    return words, FAMILY_INCOMPLETE, items


def _obligations_evidence(run_dir: Path, model_bytes: bytes, *,
                          package_files: dict[str, bytes] | None = None) -> tuple[dict[str, bytes], dict | None]:
    """Carry optional A12 evidence against the EXACT emitted model bytes.

    This is a report, not a new semantic-completeness or execution gate. Old
    runs without a ledger keep their previous package surface. Invalid/stale
    authored evidence is carried with its explicit non-success status; it is
    neither repaired nor turned into an empty successful extraction.
    """
    from cdpm import obligations as ob
    root = Path(run_dir).resolve()
    try:
        path = ob._local_file(root, ob.LEDGER_REL)
        if not path.exists():
            return {}, None
        raw = path.read_bytes()
        report = ob.run_report(root, model_bytes=model_bytes)
        recorded_sha = (report.get("ledger") or {}).get("sha256")
        digest = hashlib.sha256(raw).hexdigest()
        if path.read_bytes() != raw or (recorded_sha is not None and recorded_sha != digest):
            raise ValueError("ведомость обязательств изменилась при подготовке пакета")
        binding = report.get("input_binding")
        if package_files is not None and binding is not None:
            # The run may change after the caller copied its clauses. A fresh
            # report about new disk bytes must not accompany old ZIP bytes.
            manifest_raw = ob._local_file(root, "freeze/manifest.json").read_bytes()
            frozen = m.loads_strict(manifest_raw)
            if not isinstance(frozen, dict) or not isinstance(frozen.get("files"), list):
                raise ValueError("форма заморозки изменилась при подготовке свидетельств A12")
            members = [{"path": "manifest.json", "sha256": hashlib.sha256(manifest_raw).hexdigest()}]
            for entry in frozen["files"]:
                if not isinstance(entry, dict) or "sid" not in entry:
                    raise ValueError("запись источника заморозки изменилась при подготовке свидетельств A12")
                relative = "clauses/" + ob._filename(entry["sid"], "freeze.sid") + ".json"
                emitted = package_files.get("freeze/" + relative)
                if emitted is None:
                    raise ValueError(f"в пакете отсутствует источник ведомости: freeze/{relative}")
                members.append({"path": relative, "sha256": hashlib.sha256(emitted).hexdigest()})
            inventory = json.dumps({"schema_version": "obligation-freeze-members/1", "files": members},
                                   sort_keys=True, separators=(",", ":")).encode("utf-8")
            if hashlib.sha256(inventory).hexdigest() != binding["freeze_sha256"]:
                raise ValueError("заморозка отчёта обязательств отличается от файлов выпускаемого пакета")
    except (OSError, ValueError) as exc:
        raise TaskPackageError(f"свидетельства A12 не могут быть переданы без потерь: {exc}",
                               "obligation-evidence-unavailable") from exc
    report["package_binding"] = {"model_sha256": hashlib.sha256(model_bytes).hexdigest(),
                                 "ledger_sha256": digest}
    files = {"obligations-ledger.json": raw,
             "obligations-report.json": (json.dumps(report, ensure_ascii=False, sort_keys=True,
                                                    indent=1) + "\n").encode("utf-8")}
    summary = {"ledger": "obligations-ledger.json", "report": "obligations-report.json",
               "form": report["form"], "freshness": report["freshness"],
               "meaning": report["meaning"], "independence": report["independence"]}
    return files, summary


def build_package(run_dir: Path, base_url: str | None = None, *,
                  spec_version: str | None = None,
                  _stand_context: _StandExportContext | None = None) -> dict[str, bytes]:
    """Файлы пакета {имя: байты}. Отказ, если постановки нет, у правила
    нет шаблона (пробел покрытия — coverage_gap_problem) или ни один
    режим ни одной задачи не разрешён (пустой пакет — обман).

    РУБЕЖ каталога (шаг 7): каталог шаблонов обязателен — им сажаются
    правила и им подписан ключ `manifest.catalog`. Без каталога выпуск
    ОТКАЗЫВАЕТ словами (`TaskPackageError` → 409 у ручки), а не кладёт
    пакет без отметки и не отдаёт наружу трассировку из внутренностей
    сборки.

    РУБЕЖ непротиворечивости (СМТ-1, 11.09.2026): у пакета обязан быть
    свежий машинный вердикт о совместности жёстких правил
    (`consistency_of`); красный вердикт и невозможность проверки
    останавливают выпуск с кодами `smt-contradiction` и
    `smt-unavailable`.

    `base_url` — адрес панели, которым выпущен пакет (ручка передаёт
    адрес запроса): из него складывается ключ `record_api`.
    `spec_version` выбирает контракт только этого пакета; None использует
    явный CDPM_EXPORT_SPEC_VERSION контура либо текущий SPEC_VERSION.
    Выбор не выпускает каталог и не утверждает
    поддержку: все соответствующие проверки остаются обязательными."""
    from cdpm import catalog_bridge as bridge, threshold_forms as tf
    try:
        return _build_package(Path(run_dir), base_url, spec_version=spec_version,
                              _stand_context=_stand_context)
    except bridge.CATALOG_ERRORS as e:
        # Н-5 неавтор-ревью 12.09.2026: у отказа каталога код ЕСТЬ
        # (`catalog-unavailable` — тот же, что у двери COSMOS и у ручек
        # панели), и рубеж обязан его передать: ручка выпуска кладёт код
        # в тело 409 (§1.4), и читатель различает «каталога нет» и
        # «пакет не собрался» машинно, а не по словам.
        raise TaskPackageError(
            f"выпуск пакета невозможен: каталог шаблонов недоступен: "
            f"{bridge.words_of(e)}",
            getattr(e, "code", None) or bridge.CatalogRequired.code) from None
    except tf.ThresholdFormError as e:
        raise TaskPackageError(f"выпуск пакета остановлен: {e}",
                               e.code, e.items) from None


def _load_modern_package_model(raw: bytes) -> dict:
    """Semantic migration for a NEW export, never for received package bytes.

    The caller retains raw as the canonical consistency evidence and judges
    the original carriers before calling. History alone cannot authorize the
    resulting shape: use the same actual-data gate as the receiver.
    """
    from cdpm import catalog_bridge, landing_contract
    try:
        doc = m.loads_model(raw)
    except (ValueError, TypeError) as error:
        raise TaskPackageError(f"миграция модели перед выпуском не подтверждена: {error}",
                               "landing-incomplete") from None
    inspection = landing_contract.inspect(doc, {"catalog": catalog_bridge.stamp()},
                                          for_execution=True)
    if inspection["problems"]:
        first = inspection["problems"][0]
        raise TaskPackageError("; ".join(p["words"] for p in inspection["problems"]),
                               first["code"], sorted({str(p["rule"]) for p in inspection["problems"]
                                                     if p.get("rule") is not None}))
    return doc


def _build_package(run_dir: Path, base_url: str | None = None, *,
                   spec_version: str | None = None,
                   _stand_context: _StandExportContext | None = None) -> dict[str, bytes]:
    prepared = _prepare_native_model(run_dir, spec_version=spec_version)
    doc, spec_version = prepared.doc, prepared.spec_version
    modes, objective_ids = allowed_modes(doc, spec_version)
    # Admission stays exclusively on the ordinary task-package path. The
    # shared serializer below never constructs an executable envelope.
    refuse_by_barriers(doc, modes, spec_version)
    if not any(modes.values()):
        raise TaskPackageError(
            "ни один режим ни одной задачи не разрешён матрицей "
            "корректности — постановка не готова к передаче в "
            "решатель")
    files, content, clean_data = _serialize_native_content(
        prepared, base_url=base_url, _stand_context=_stand_context)
    from cdpm import support_manifest
    support = None
    if support_manifest.required(spec_version):
        try:
            # Snapshot the bytes actually emitted, as before the content split.
            support = support_manifest.project(json.loads(files["model.json"].decode("utf-8")))
        except support_manifest.SupportProjectionError as exc:
            raise TaskPackageError(str(exc), code=exc.code) from exc
    # Preserve the established task manifest's field order and exact bytes.
    support_after = next(key for key in ("obligations", "stand_only", "families")
                         if key in content)
    manifest = {}
    for key, value in content.items():
        manifest[key] = value
        if key == "files":
            manifest.update(readiness=m.readiness(doc, spec_version).get("state"),
                            allowed_modes=modes, objectives=objective_ids)
        elif key == "has_data":
            manifest["readiness_matrix"] = readiness_ladder(doc, clean_data, spec_version)
        if key == support_after and support is not None:
            manifest["support"] = support
    files["manifest.json"] = (json.dumps(
        manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files


@dataclass(frozen=True)
class _NativeModel:
    """Validated content, not a task-package admission or support approval."""
    run_dir: Path
    spec_version: str
    canon_bytes: bytes
    model_bytes: bytes
    doc: dict
    coverage_summary: dict | None
    ms_report: dict | None


def _normalize_native_model(model_bytes: bytes) -> tuple[bytes, dict]:
    """The actual source-to-native normalization, without files or admission.

    Component readers use this same path to bind the complete native model to
    its declared source. Expected measurement values are never an input.
    """
    # A05, находка Н-3 (класс X-04): дубль ключа ячейки в БАЙТАХ свода
    # библиотека читает молча — побеждает последнее объявление, и пакет
    # уезжает с чужой величиной. Отказ формы — кода в §5 контракта у
    # него нет, поэтому он идёт тем же путём, что «форма ВМП с
    # потерями»: слова без кода (`code: null`, §1.4).
    try:
        doc = m.loads_strict(model_bytes)
    except (m.DuplicateJsonKey, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise TaskPackageError(
            f"выпуск пакета остановлен: модель прогона нечитаема — "
            f"{e}") from None
    if not isinstance(doc, dict):
        raise TaskPackageError(
            "выпуск пакета остановлен: модель прогона должна быть JSON-объектом")
    # A05-барьер: исходные носители параметра сверяются ДО лечения и
    # канона посадки (§12.5) — расхождение останавливает выпуск здесь.
    refuse_diverging_carriers(doc)
    refuse_invalid_logic_units(doc)
    gap = coverage_gap_problem(doc)
    if gap:
        raise TaskPackageError(gap)
    # Ревизия 1.19: численность места при посадке на семью состава —
    # обязательное производное; невыводимое — отказ здесь, а не отказ
    # потребителя на кнопке пейринга по пакету без поля.
    crew_problem = crew_size_problem(doc)
    if crew_problem:
        raise TaskPackageError(crew_problem)
    # РУБЕЖ пересмотра (K04, контракт П0 §2.4 (а)): ячейка посадки под
    # открытым пересмотром — параметр без действующего решения
    # авиакомпании; прежнее значение выпуску не годится. Тот же расчёт
    # (`model.pending_revision_facts`) читают готовность, матрица
    # корректности и гейт производителя по `model.json` пакета.
    pending_problem = pending_revision_problem(doc)
    if pending_problem:
        raise TaskPackageError(*pending_problem)
    # Schema migration also rejects a missing ID, but its general landing
    # error must not hide the established refusal naming the HARD position.
    refuse_nameless_hard_rules(doc)
    original_doc = doc
    doc = _load_modern_package_model(model_bytes)
    migrated = doc != original_doc
    # Answer migration can itself require a new explicit confirmation.
    pending_problem = pending_revision_problem(doc)
    if pending_problem:
        raise TaskPackageError(*pending_problem)
    # Машинное лечение свода — ОДНОЙ цепочкой (heal_for_package): её же
    # зовёт панель на копии, чтобы индикатор «Биекция» судил тот свод,
    # что судит замок выпуска (A11, ревью п. 3).
    healed = heal_for_package(doc)
    if migrated:
        healed.insert(0, "модель мигрирована в памяти перед выпуском; исходный файл сохранён")
    # A05-барьер, ВТОРАЯ точка (исправление В-2 неавтор-ревью,
    # 12.09.2026): пакет — это ВЫЛЕЧЕННЫЙ свод, и судить его обязан тот
    # же расчёт. Самолечения выше дописывают `params[]` по формулировке
    # и правят единицы/отношения/уточнители — то есть вправе СОЗДАТЬ
    # второго носителя с другой правдой уже после первой сверки. Байты
    # пакета берутся строкой ниже: сверка обязана стоять между лечением
    # и байтами, иначе конфликтный пакет уезжает, а гейт производителя
    # на нём краснеет (ручка `/export/task-package` гейт не зовёт).
    refuse_diverging_carriers(doc)
    # Сверки исходного и вылеченного свода выше обязательны ДО проекции:
    refuse_invalid_logic_units(doc)
    # иначе выведение params[] затёрло бы свидетельство «3 против 30».
    # An invalid answer cell must report answer-provenance before projection
    # can replace that reason with an untyped canonicalization error.
    refuse_answer_provenance(doc)
    for rule in doc.get("rules", []):
        projected = derive_params_from_landing(rule)
        if projected != (rule.get("params") or []):
            rule["params"] = projected
            canon_fit_params(rule)
            healed.append(f"{rule.get('id')}: params выведены из посадки")
    refuse_diverging_carriers(doc)
    refuse_invalid_logic_units(doc)
    if healed:
        model_bytes = json.dumps(
            doc, ensure_ascii=False, indent=1).encode("utf-8")
    # Наблюдение неавторского ревью 12.09.2026: жёсткое правило БЕЗ
    # ключа `id` роняло сборку `KeyError: 'id'` в `vmp_form._rule_to_row`
    # — раньше рубежа, который сказал бы о нём словами (правило с
    # ПУСТЫМ `id` до рубежа доходило и получало честный отказ). Свод с
    # таким правилом незаконен, и выпуск отказывает СЛОВАМИ с позицией
    # — теми же, что у проверки непротиворечивости. Судится
    # ВЫПУСКАЕМЫЙ свод (после ярлыков по каталогу): правило, ставшее
    # жёстким при сборке, тоже обязано быть названным.
    refuse_nameless_hard_rules(doc)
    # Точное происхождение значения из ответа (A03-мин/K05-мин,
    # контракт П0 §2.3 п. 3, 12.09.2026): значение ячейки посадки,
    # внесённое ответом авиакомпании, выпускается только с
    # подтверждённым основанием — вопрос-издание существует, ответ
    # применён, отпечатки издания и версии совпадают, цитата дословна
    # в raw ИМЕННО той версии, а значение читается из этой цитаты
    # грамматикой K02. Тот же расчёт повторяет гейт пакета по
    # model.json (общий закон П0 № 4): вердикт производителя и
    # приёмника один.
    refuse_answer_provenance(doc)
    return model_bytes, doc


def _prepare_native_model(run_dir: Path, *, spec_version: str | None = None) -> _NativeModel:
    """Shared non-admission checks for task and declared component inputs."""
    if spec_version is None:
        spec_version = os.environ.get("CDPM_EXPORT_SPEC_VERSION", SPEC_VERSION)
    if spec_version not in (SPEC_VERSION, "1.20", "1.21"):
        raise TaskPackageError(f"неподдерживаемая версия пакета: {spec_version!r}")
    run_dir = Path(run_dir)
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise TaskPackageError("модель прогона не построена — "
                               "сначала извлечение")
    # Canonical source bytes remain the consistency report's exact subject;
    # normalization only returns in-memory native output.
    canon_bytes = model_path.read_bytes()
    model_bytes, doc = _normalize_native_model(canon_bytes)
    # Замок биекции «не потерять» (4П владельца, 2026-09-02): каждая
    # клауза документов правил обязана иметь вердикт — покрыта строкой
    # свода, целевой или вопросом, либо явно признана аудитором «не
    # правило» с причиной (exchange/coverage.json). Клауза без вердикта
    # останавливает выпуск пакета: молчание модели вердиктом не
    # считается. Без комплекта заморозки (лабораторные сборки из одной
    # модели) судить нечего — сводка в манифесте пуста.
    coverage_summary = None
    #: Отчёт измерителя постановки по своду и клаузам заморозки — один
    #: на сборку: судит замок содержания здесь и едет протоколом в пакет.
    ms_report = None
    kit_dir = run_dir / "freeze"
    if (kit_dir / "manifest.json").is_file():
        from cdpm import coverage as cov
        from cdpm import extract as extract_mod
        from cdpm import measure as ms_mod
        kit_entries = extract_mod._load_kit(kit_dir)
        # Замок содержания (аудит 2026-09-04, п. 2): измеритель считает
        # остаток величин клауз и нормативные носители без строк на том
        # же своде, что уходит в пакет; вердикты аудитора по клаузам и
        # по величинам (coverage.json) — законные адресаты остатка.
        # Сбой измерителя — отказ сборки, а не молчаливая справка:
        # пакет без измерения не выпускается. Файл вердиктов читается
        # ДО измерителя и со своим текстом отказа: испорченный
        # coverage.json — вина файла, а не измерителя, и причина
        # называется верно (ревью G5, 04.09).
        try:
            all_verdicts = cov.run_all_verdicts(run_dir)
        except Exception as exc:                              # noqa: BLE001
            raise TaskPackageError(
                f"файл вердиктов coverage.json не читается — без "
                f"вердиктов аудитора остаток судить нельзя, пакет не "
                f"выпускается: {exc}"[:400]) from exc
        try:
            ms_report = ms_mod.measure(
                doc, [{"sid": e["sid"], "anchor": c["anchor"],
                       "text": c.get("text") or ""}
                      for e in kit_entries for c in e.get("clauses") or []],
                verdicts=all_verdicts)
        except Exception as exc:                              # noqa: BLE001
            raise TaskPackageError(
                f"измеритель постановки не отработал — пакет без "
                f"измерения не выпускается: {exc}"[:400]) from exc
        cov_rep = cov.report(kit_entries, doc, cov.run_verdicts(run_dir),
                             measure_report=ms_report)
        # A11 (единый расчёт фактов покрытия, аудит 11.09.2026): те же
        # факты и тот же вердикт, что у индикатора «Биекция» панели и у
        # гейта пакета, — cdpm.coverage.bijection_facts: клаузы без
        # вердикта, величины без адресата и нормативные носители без
        # строк (замок содержания — слова measure.content_lock),
        # частично покрытые клаузы, строки без источника, висячие якоря
        # по комплекту заморозки. Структурный вердикт измерителя
        # (measure.ok) едет в манифест честно и краснится гейтом пакета
        # — перевод его в замок сборки за владельцем.
        facts = cov.bijection_facts(doc, cov_rep, measure_report=ms_report,
                                    kit_entries=kit_entries)
        if not facts["ok"]:
            raise TaskPackageError(" || ".join(cov.refusal_words(facts)))
        coverage_summary = {"clauses": cov_rep["clauses"],
                            "covered_by_rows": cov_rep["covered_by_rows"],
                            "not_rule": cov_rep["not_rule"],
                            "missing": 0,
                            # Слой содержания: после замка оба нуля —
                            # гейт пересчитывает их из протокола.
                            "values_without_addressee":
                                cov_rep["values_without_addressee"],
                            "normative_without_rows":
                                len(cov_rep["normative_without_rows"]),
                            "values_by_verdict": cov_rep["values_by_verdict"],
                            "normative_not_rule": cov_rep["normative_not_rule"],
                            # A12-заявления (контракт П0 v1.7 §9): там,
                            # где манифест предъявляет факт покрытия,
                            # он тут же называет ПРЕДЕЛ этого факта —
                            # покрытая клауза не равна всем
                            # обязательствам внутри неё. Поле
                            # справочное (потребитель читает как
                            # карточку пакета, запуск оно не блокирует)
                            # и по построению не бывает «доказано»:
                            # машинного признака смысловой полноты нет
                            # до ведомости атомарных обязательств.
                            "meaning_completeness": cov.MEANING_GAP}
    return _NativeModel(run_dir, spec_version, canon_bytes, model_bytes, doc,
                        coverage_summary, ms_report)


def _serialize_native_content(prepared: _NativeModel, *, base_url: str | None = None,
                              _stand_context: _StandExportContext | None = None
                              ) -> tuple[dict[str, bytes], dict, dict | None]:
    """The actual native serializers and remaining content/proof checks.

    Returns payloads and descriptive metadata only. No task manifest, support
    approval, allowed modes or admission is created by this function.
    """
    run_dir, spec_version = prepared.run_dir, prepared.spec_version
    canon_bytes, model_bytes, doc = prepared.canon_bytes, prepared.model_bytes, prepared.doc
    coverage_summary, ms_report = prepared.coverage_summary, prepared.ms_report
    files: dict[str, bytes] = {"model.json": model_bytes}
    # The blind source checker must receive the airline's actual answered
    # questionnaire, not infer its norm from a value we already put in VMP.
    from cdpm import source_answer_evidence as answer_sources
    try:
        files.update(answer_sources.export_files(
            run_dir, model_bytes, package_run_name(run_dir)))
    except ValueError as exc:
        raise TaskPackageError(str(exc), code="source-answer-evidence") from exc
    # Правила, исключённые до получения данных по решению авиакомпании
    # (форма ВМП, 2026-09-02): в модели пакета остаются с пометкой, но
    # в машинные формы для решателя (целевая, пороги, контрпримеры) не
    # идут; перечень — в манифесте, приёмник и сертификат его читают.
    excluded_ids = [r.get("id") for r in doc.get("rules", [])
                    if isinstance(r, dict) and r.get("excluded_until_data")]
    doc_x = excluded_rules_slice(doc)
    from vmp import objective_projection as op
    try:
        files["objective.json"] = export_solver.build_objective_json(
            doc_x, spec_version=spec_version).encode("utf-8")
    except op.ObjectiveProjectionError as exc:
        raise TaskPackageError(str(exc), code=op.CODE_DIRECTION) from None
    # Ревизия 1.10 (отмашка владельца 2026-08-27): машинные формы
    # порогов (матрица прил.3, условные полы отдыха) — отдельным
    # файлом пакета, собранным детерминированно из ячеек модели;
    # файл автоматически объявляется манифестом с суммой (железное
    # правило приёмника решателя).
    from cdpm import threshold_forms as tf
    tf_bytes = tf.forms_bytes(doc_x, spec_version)
    if tf_bytes is not None:
        files["threshold-forms.json"] = tf_bytes
    for name in ("svod.md", "anketa.md"):
        p = run_dir / "extract" / name
        if p.is_file():
            files[name] = p.read_bytes()
    # Форма ВМП (решение владельца 2026-09-02, шаг 1): пакет несёт форму
    # выдачи/приёмки рядом с моделью — vmp-form.json (проекция без
    # потерь; потеря = отказ пакета) и vmp-form.md (документ). Проверки
    # формы (§8 спецификации) — в манифесте; жёстким гейтом они
    # становятся при CDPM_FORM_GATE=strict (иначе — честный отчёт).
    from cdpm import vmp_form as vf
    form = vf.to_form(doc)
    lost = vf.canonical_diff(doc, vf.from_form(form))
    if lost:
        raise TaskPackageError("форма ВМП строится с потерями: "
                               + "; ".join(lost[:3]))
    # Цитаты формы судятся против клауз заморозки, когда она есть (план
    # «прямой ход» 2.3): формулировка строки — формулировка ВМП.
    clause_texts_form = None
    try:
        from cdpm import extract as _ex
        if (run_dir / "freeze" / "manifest.json").is_file():
            clause_texts_form = {
                (str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
                for e in _ex._load_kit(run_dir / "freeze")
                for c in e.get("clauses") or []}
    except Exception:                                    # noqa: BLE001
        clause_texts_form = None
    form_problems = vf.validate_form(form, strict=True, clause_texts=clause_texts_form)
    # Протокол измерения на границе CDPM → COSMOS (решение владельца
    # 03.09, RAZDELENIE-CDPM-COSMOS раздел 4): пакет несёт числа и вердикт
    # измерителя постановки (cdpm/measure.py) — учёт ответственности за
    # соответствие ВМП документам, не вторая проверка у приёмника.
    measure_summary = {"available": False, "reason": "нет заморозки — измерять не по чему"}
    if ms_report is not None:
        # Отчёт уже посчитан замком содержания выше (один отчёт на
        # сборку); здесь — протокол в пакет и сводка в манифест. Замок
        # гарантирует нули остатка (величины без адресата, нормативные
        # носители без строк); структурный вердикт ok едет честно и
        # краснится гейтом пакета, который пересчитывает вердикт и
        # счётчики из протокола и краснит расхождение.
        from cdpm import measure as ms_mod
        ms_ok, ms_problems = ms_mod.verdict(ms_report)
        measure_summary = {"available": True, "ok": ms_ok, "problems": ms_problems,
                           "counts": ms_report["counts"]}
        files["measure-protocol.json"] = (json.dumps(ms_report, ensure_ascii=False, indent=1)
                                          + "\n").encode("utf-8")
    if form_problems and os.environ.get("CDPM_FORM_GATE") == "strict":
        raise TaskPackageError("форма ВМП не готова к приёмке: "
                               + "; ".join(form_problems[:3]))
    files["vmp-form.json"] = (json.dumps(form, ensure_ascii=False, indent=1)
                              + "\n").encode("utf-8")
    files["vmp-form.md"] = vf.render_form_md(form).encode("utf-8")

    # Ступень 3 (9б-3): контрпримерные пары для правил с logic —
    # секция logic-examples; правило с logic, но без пары, честно
    # перечисляется в манифесте logic_unproven с причиной, его
    # логика едет как ФОРМАЛИЗОВАННАЯ, но потребитель не вправе
    # принимать её required-исполнителем до пары (глава 2 дизайна).
    from cdpm import logic_proof as lp_mod
    examples: dict = {}
    unproven: list[dict] = []
    for r in doc_x.get("rules", []):
        if not isinstance(r.get("logic"), dict) or r.get("blockers"):
            continue
        pair, why = lp_mod.generate_pair(r)
        if pair is None:
            unproven.append({"rule": r.get("id"), "reason": why})
        else:
            gate_probs = lp_mod.check_pair(pair, r.get("id"), rule=r)
            if gate_probs:
                raise TaskPackageError(
                    "контрпримерная пара не прошла механический "
                    "гейт: " + "; ".join(gate_probs[:2]))
            examples[r.get("id")] = pair
    if examples:
        files["logic-examples.json"] = json.dumps(
            examples, ensure_ascii=False, indent=1).encode("utf-8")

    # Human and machine package surfaces describe the same computed facts.
    # Append before the manifest hashes are built, including a zero count.
    files["vmp-form.md"] += ("\n## Доказанность логики\n\n"
                             + lp_mod.render_unproven_summary(unproven)
                             + "\n").encode("utf-8")

    has_data = False
    clean_data: dict | None = None
    data_path = run_dir / "data-freeze" / "dataset.json"
    report_path = run_dir / "data-report" / "data-report.json"
    if data_path.is_file() and report_path.is_file():
        from cdpm import export_clean_data as clean_mod
        dataset = json.loads(data_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        clean_text = clean_mod.export_json(dataset, report)
        files["clean-data.json"] = clean_text.encode("utf-8")
        # Матрица готовности судит РОВНО упакованный артефакт (тот же
        # текст парсит гейт при пересчёте — паритет по построению).
        clean_data = json.loads(clean_text)
        has_data = True

    # Дополнение к 1.17 (слово владельца 06.09.2026): клаузы заморозки
    # едут С ПАКЕТОМ — записи freeze/clauses/S*.json один в один с
    # cdpm-runs/<run>/freeze/clauses (те же имена и байты). Потребитель
    # сверяет решения с исходными документами (проверки по ИДАК) без
    # доступа к каталогу прогонов CDPM: пакет — единственный вход.
    clauses_dir = run_dir / "freeze" / "clauses"
    freeze_clauses: dict | None = None
    if clauses_dir.is_dir():
        n_clauses = 0
        sids: list[str] = []
        for p in sorted(clauses_dir.glob("*.json")):
            body = p.read_bytes()
            files[f"freeze/clauses/{p.name}"] = body
            sids.append(p.stem)
            try:
                got = json.loads(body.decode("utf-8"))
                n_clauses += len(got) if isinstance(got, list) \
                    else len(got.get("clauses", []))
            except Exception:                             # noqa: BLE001
                pass
        if sids:
            freeze_clauses = {"files": len(sids), "clauses": n_clauses,
                              "sids": sids,
                              "path": "freeze/clauses/<sid>.json"}

    # РУБЕЖ непротиворечивости (слово владельца 11.09.2026): последний
    # перед манифестом — вызов модели не тратится на пакет, который всё
    # равно не выпустится по рубежам выше. Красный вердикт и
    # невозможность проверки останавливают выпуск здесь.
    # Судится ВЫПУСКАЕМЫЙ свод — ровно байты model.json пакета (A01-F1
    # аудита, 12.09.2026): преобразования сборки выше (ярлыки по
    # каталогу SOFT→HARD, семьи, поля 1.15) могли изменить набор жёстких
    # правил, а отчёт кнопки судит канонический свод; после рубежа
    # правил уже никто не трогает — манифест только читает `doc`.
    released_doc = json.loads(model_bytes.decode("utf-8"))
    consistency = consistency_of(run_dir, canon_bytes, released_doc,
                                 _stand_context=_stand_context)
    # Отчёт, которым вынесен вердикт, едет С ПАКЕТОМ: гейт производителя
    # (и аудитор по настоящему ZIP) сверяет его строки с жёсткими
    # правилами model.json пакета, а не верит счётчикам манифеста.
    files[SMT_REPORT_NAME] = _report_bytes_of(run_dir, consistency)

    obligation_files, obligation_summary = _obligations_evidence(
        run_dir, files["model.json"], package_files=files)
    files.update(obligation_files)

    from cdpm.family_manifest import project as project_families
    manifest = {
        "spec_version": spec_version,
        # Ф-1.7: метки времени created в манифесте больше НЕТ — два
        # одинаковых по смыслу экспорта несут байт-идентичный манифест
        # (отпечаток решателя считается по содержимому манифеста), и
        # сертификаты разных нажатий кнопки сравнимы по построению.
        # Живое доказательство нужности — отказ пятого нажатия
        # 2026-08-24 («пейринг считан по другому пакету»). Время
        # экспорта остаётся в журналах прогона; потребители created
        # не читали (сверено обеими сторонами).
        "customer": (doc.get("meta") or {}).get("customer"),
        # Потомок «из журнала» выпускает пакет байт в байт с исходным
        # (правило 2.15 проекта повтора): имя прогона в манифесте — имя
        # источника, как и made_by; личность потомка — только в его
        # паспорте и отчёте повтора.
        "source_run": package_run_name(run_dir),
        "revision": int((doc.get("meta") or {}).get("revision", 1)),
        "files": [
            {"name": name,
             "sha256": hashlib.sha256(body).hexdigest(),
             "bytes": len(body)}
            for name, body in sorted(files.items())],
        "has_data": has_data,
        # Ревизия 1.12 (директива владельца 2026-08-29): матрица
        # готовности ВМП — часть пакета постановки; верифицируется
        # потребителем независимым пересчётом, расхождение любой
        # клетки останавливает запуск. Слова оснований барьеров 17а
        # (K01/K06) едут здесь же — строками «барьер: …» в `notes`
        # столбца (С-2); формы лестницы это не меняет.
        # Правила, исключённые до получения данных по решению
        # авиакомпании: в модели с пометкой, в машинных формах пакета
        # отсутствуют; приёмник не требует их исполнения, сертификат
        # пишет «не проверено по решению авиакомпании».
        "excluded_until_data": excluded_ids,
        # Замок биекции: покрытие клауз документов правил (null — сборка
        # без комплекта заморозки, судить нечего).
        "coverage": coverage_summary,
        # Форма ВМП: проверки формы приёмки (пусто = чиста) и режим гейта.
        "measure": measure_summary,
        "form": {"problems": form_problems,
                 "strict_gate": os.environ.get("CDPM_FORM_GATE") == "strict",
                 "sections": {
                     "common": {"definitions": len(form["common"]["definitions"]),
                                "world": len(form["common"]["world"])},
                     "pairing": {k: len(form["pairing"][k])
                                 for k in ("hard", "soft", "criteria")},
                     "rostering": {k: len(form["rostering"][k])
                                   for k in ("hard", "soft", "criteria")}}},
        # Дополнение к 1.18 (третье, 11.09.2026): машинный вердикт о
        # непротиворечивости свода, которым выпущен пакет. Ключ
        # необязательный — пакеты, выпущенные до рубежа, читаются
        # прежним законом «непротиворечивость не объявлена».
        "consistency": consistency,
        "families": project_families(released_doc),
    }
    if "stand_exception" in consistency:
        # Not a customer-package consistency claim: the ordinary receiving gate
        # rejects this marker, even if someone removes/rewrites consistency.
        manifest["stand_only"] = True
    if obligation_summary is not None:
        manifest["obligations"] = obligation_summary
    rule_maps = _rule_maps_for(doc)
    if rule_maps is not None:
        manifest["rule_maps"] = rule_maps
    if unproven:
        manifest["logic_unproven"] = unproven
    crew = crew_size_of(doc)
    if crew is not None:
        manifest["crew_size"] = crew
    # Признак допуска моделей комплекта (04.09, после переезда расчёта
    # в COSMOS): класс данных и разрешённый круг моделей едут С ПАКЕТОМ —
    # потребитель выбирает модели сертификатора по признаку комплекта,
    # а не по площадке. Без признака ключа нет (закон 1.12: отсутствие
    # ключа законно); потребитель без ключа судит «только локальные».
    kit = kit_scope_of(run_dir)
    if kit:
        manifest["kit"] = kit
    # Номер записи о прогоне (шаг 1 проекта, 10.09.2026): пакет несёт
    # номер записи панели — COSMOS кладёт его в происхождение попытки.
    from cdpm import run_record as run_record_mod
    manifest["record_id"] = run_record_mod.record_id_of(run_dir)
    decl_sha = run_record_mod.declaration_sha256_of(run_dir)
    if decl_sha:
        manifest["declaration_sha256"] = decl_sha
    # Шаг 2 (просьба COSMOS, 11.2): адрес записи у ХРАНИТЕЛЯ — по нему
    # COSMOS дописывает свои этапы (`record_api + "/append"`). Ключа нет
    # — у COSMOS остаётся запасной путь (переменная площадки).
    record_api = record_api_of(run_dir, base_url)
    if record_api:
        manifest["record_api"] = record_api
    # Дополнение к 1.17 (06.09.2026): сводка клауз заморозки в пакете —
    # сколько записей и клауз, какие sid; сами записи — в files с sha256.
    if freeze_clauses:
        manifest["freeze_clauses"] = freeze_clauses
    # Ревизия 1.15: отпечаток очищенных данных — к нему привязываются
    # доказательства невозможности покрытия (A4) и ведомость исполнения.
    if has_data:
        manifest["data_fingerprint"] = data_fingerprint_of(clean_text,
                                                           clean_data)
    # Ревизия 1.16 (A9): система времени пакета явно — шкала меток из
    # правила семьи time_scale, пояс базы из листа «Аэропорты». Без
    # правила шкалы ключей нет; пояс — только когда он однозначен.
    time_decl = time_declaration_of(doc, clean_data)
    if time_decl:
        manifest["time"] = time_decl
    # Ревизия 1.18.1 (C-27b): объявленный горизонт — ключи planSince и
    # planUntil листа «Свойства» очищенных данных; end_exclusive =
    # end_inclusive + 1 минута считается здесь ровно один раз, читатели
    # окон минуту не прибавляют; пояс — из объявления системы времени
    # (1.16), без него null. Нет обоих ключей — ключа horizon нет (как у
    # history): потребитель судит «горизонт не объявлен».
    if has_data:
        from cdpm.history_section import declared_horizon
        horizon = declared_horizon(
            clean_data, tz=(time_decl or {}).get("base_time_zone"))
        if horizon:
            manifest["horizon"] = horizon
    # Ревизия 1.17 (A7, ступень L): раздел «история сотрудника» из листа
    # «История» очищенных данных; без листа ключа нет — потребитель
    # заявляет «история не передана» и не подставляет ноль.
    if has_data:
        from cdpm.history_section import history_section
        # 1.17.2: раздел зависит и от ответов анкеты (решение по истории,
        # окна правил) — тот же вызов у гейта с теми же двумя файлами пакета.
        # 1.18.2: у записей сотрудников — свидетельства присутствия.
        hist = history_section(clean_data, doc)
        if hist:
            manifest["history"] = hist
    # Дополнение к 1.18 (слова владельца 07.09.2026, форма согласована с
    # COSMOS): «чем сделан пакет» — необязательный ключ made_by: коммит
    # панели, модели ролей формализации, инструменты проверок, входы по
    # отпечаткам. Метки времени экспорта нет (Ф-1.7: байт-идентичный
    # манифест между нажатиями) — только стабильная дата рождения прогона.
    # Без статуса прогона ключа нет; кривой статус — без ключа, не падение.
    made_by = made_by_of(run_dir)
    if made_by:
        manifest["made_by"] = made_by
    # Дополнение к 1.18 (второе, 07.09.2026; шаг 3 переезда на каталог
    # шаблонов): каким каталогом пакет выпущен (`catalog`) и сходятся ли
    # с ним ревизии посадок строк (`fits_catalog`). Оба ключа
    # необязательны — пакет без них читается прежним законом «каталог не
    # объявлен»; сводка посадок — предупреждение, экспорт она не держит.
    catalog_stamp = catalog_stamp_of()
    if catalog_stamp:
        manifest["catalog"] = catalog_stamp
    fits_catalog = fits_catalog_of(doc, catalog_stamp)
    if fits_catalog:
        manifest["fits_catalog"] = fits_catalog
    # A01-F1 (12.09.2026): предмет, о котором вынесен вердикт рубежа,
    # обязан остаться предметом ВЫПУСКАЕМОГО model.json — после рубежа
    # правил не трогают, манифест только читает `doc`. Это не обещание
    # в комментарии, а замок машиной: стоит кому-то добавить между
    # рубежом и манифестом преобразование правил — выпуск ОТКАЗЫВАЕТ
    # словами, а не выпустит пакет со справкой о другом наборе правил
    # (ровно находка A01-F1 независимого аудита).
    #
    # Замок — явный `raise`, а не `assert` (находка неавторского ревью
    # 12.09.2026): под `python -O` утверждения не исполняются, и замок,
    # стерегущий ВЫПУСК, обязан быть отказом словами, а не проверкой,
    # которой на площадке может не быть.
    from cdpm import smt_verify as _smt_mod
    judged = consistency["subject_sha256"]
    in_package = _smt_mod.subject_fingerprint(
        json.loads(files["model.json"].decode("utf-8")))
    in_memory = _smt_mod.subject_fingerprint(doc)
    if judged != in_package or judged != in_memory:
        raise TaskPackageError(
            f"выпуск пакета остановлен: после рубежа непротиворечивости "
            f"изменился предмет проверки — справка вынесена о наборе "
            f"жёстких правил {judged}, а в пакет идёт {in_package} (свод "
            f"сборки — {in_memory}). Пакет со справкой о ДРУГОМ наборе "
            f"правил не выпускается: между рубежом и манифестом встало "
            f"преобразование правил — уберите его", SMT_UNAVAILABLE)
    return files, manifest, clean_data


#: Время записей архива — постоянное: отпечаток пакета (sha256 zip) у
#: потребителя считается по байтам, и два одинаковых по содержанию
#: нажатия обязаны давать один отпечаток (Ф-1.7 доведено до архива:
#: живая проверка 07.09.2026 — два нажатия подряд давали разные sha из-за
#: времени записей zip, и канал COSMOS принимал тот же пакет как новый).
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def write_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])
    return buf.getvalue()


def export_zip(run_dir: Path, base_url: str | None = None, *,
               spec_version: str | None = None,
               _stand_context: _StandExportContext | None = None) -> bytes:
    return write_zip(build_package(run_dir, base_url, spec_version=spec_version,
                                   _stand_context=_stand_context))
