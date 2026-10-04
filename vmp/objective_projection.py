# -*- coding: utf-8 -*-
"""Проекция целевой функции: канон ВМП пакета → содержимое objective.json.

Находка аудита A08 (11.09.2026): ни один читатель не сверял
`objective.json` с `model.json` ТОГО ЖЕ пакета — байты, подписанные
манифестом, принимались за правду, и подмена веса компонента, ставки
критерия или направления зоны проходила и гейт производителя, и приёмный
путь. Находка A06: направление зоны писалось КОНСТАНТОЙ
(`cdpm/export_solver.py:300` — `"direction": "minimize"`), а направление
вида критерия не было записано нигде — ни в каталоге, ни у построителя.

Контракт (П0): `P0-CONTRACTS-v1.4-20260912.md` §3.3, §3.4, §5, §11.2
п. 11; `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис» (строка «Проекция
целевой (В-7)» — действующая редакция подписей), §3.6, §6.1–6.3 и
приложение Е. Модуль — зона COSMOS; CDPM его ИМПОРТИРУЕТ
(`from vmp.objective_projection import project, compare`) и переводит на
него `export_solver.build_objective_json` и гейт пакета. Поэтому
зависимости здесь — только стандартная библиотека и читатель каталога
`template_catalog`: импорта `cdpm` в модуле нет (иначе цикл), импортов
прочих `vmp.*` — тоже (сторож источника CDPM
`tests/test_cdpm_catalog_source.py`).

Три вопроса, на которые отвечает модуль:

1. `project(model, catalog)` — что ОБЯЗАНО быть в `objective.json` по
   документу модели и каталогу: компоненты (мягкие правила зоны с их
   весами), целевые, машинный критерий пейринга и НАПРАВЛЕНИЕ — из поля
   записи каталога `criteria[].direction` (каталог 0.6.0), а не из
   константы кода. Каталог направления не даёт — отказ словами
   (`ObjectiveProjectionError`), не «minimize по умолчанию»: молчаливое
   умолчание и есть находка A06.
2. `canonical_bytes(objective)` — байты для записи в пакет и подписи
   манифестом (ключи отсортированы, без пробелов, строки в NFC):
   проекция одного документа обязана давать одни и те же байты.
3. `compare(objective_json, model, catalog)` — перечень расхождений
   предъявленного файла с проекцией СЛОВАМИ и кодами отказа выпуска
   (`objective-mismatch`, `criterion-direction`, раздел 5 контракта).
   Пустой `{}` — расхождение (сегодня проходил гейт и останавливал
   прогон много позже).

ЧТО СУДИТСЯ, а что нет (граница названа явно — молчаливого умолчания в
модуле нет). Судятся значения, ВЫВОДИМЫЕ из документа модели и каталога:
направление зоны, состав и веса компонентов, состав и направления
целевых, машинный критерий (когда его носитель — блок `criterion`
целевой), сырой ответ авиакомпании о целевой, существование источников.
Не судятся значения, которых в
документе модели нет: готовность зоны (`ready`), матрица режимов
(`matrix`), состояние готовности (`readiness`) — их считает CDPM
(`readiness`, `readiness_matrix`), и модуль переносит их из `context`,
когда вызывающая сторона их дала, а без `context` просто не печатает.
Так приёмный путь COSMOS и гейт производителя судят ОДНО И ТО ЖЕ и
обязаны дать один вердикт (контракт §3.4).

Ещё одна названная граница — критерий, выведенный НЕ из документа: когда
ни одна целевая зоны не несёт исполнимого блока `criterion`, производитель
выводит критерий из ответа анкеты либо из прозы (два запасных источника
`cdpm.model.derive_pairing_criterion`). Такой критерий из
модели не выводится, и модуль его СОДЕРЖИМОЕ не судит; судятся только вид
(запись каталога обязана существовать) и направление по каталогу. Хочет
производитель судить и содержимое — передаёт выведенный блок в
`context["criterion"]`, и он становится проекцией.
"""
from __future__ import annotations

# Local integration of the COSMOS tree 100c180d32b425febeae42db95ca2d24ec42d851.
# Hardened against lost/duplicate proof fields under the current P0 v1.13.
# Catalogue release, bindings and activation of producer spec 1.20 are separate.
import copy
import json
import re
import unicodedata

import template_catalog

#: Ревизия формы проекции (контракт §11.2 п. 1 — ревизия спецификации
#: пакета 1.20 вводит проекцию; форма самой проекции — своя).
PROJECTION_VERSION = "1"

#: Ревизия спецификации пакета, вводящая проекцию (контракт §11.2 п. 1).
#: Пакеты младших ревизий сверкой НЕ судятся — режим закреплённой версии
#: (контракт §6.3: «сравнение не судится, о чём пишется в паспорт
#: словами»), тот же закон, что у производителя
#: (`cdpm.export_task_package._spec_at_least`): правило, введённое
#: ревизией N, не применяется задним числом к замороженным пакетам.
PROJECTION_SPEC_FLOOR = "1.20"

#: Коды отказа выпуска (контракт §5): расхождение файла с проекцией и
#: расхождение направления с таблицей каталога.
CODE_MISMATCH = "objective-mismatch"
CODE_DIRECTION = "criterion-direction"

#: Словарь направлений (каталог 0.6.0, словарь `directions`).
DIRECTIONS = ("minimize", "maximize")

#: Направление суммы штрафов: зона БЕЗ целевых минимизирует сумму
#: штрафов мягких правил по определению режима feasibility-soft
#: (`criterion_note` ниже). Это не направление вида критерия и не
#: замена таблице каталога: у зоны без целевых вида критерия нет.
PENALTY_SUM_DIRECTION = "minimize"

#: Задача пакета → зоны правил и целевых документа.
TASK_ZONES = {"pairing": ("PAIRING", "BOTH"),
              "rostering": ("ROSTERING", "BOTH")}
TASKS = ("pairing", "rostering")

#: Часть анкеты с ответом авиакомпании о целевой — по задачам.
ANSWER_PART = {"pairing": "3a", "rostering": "3b"}

#: Метка вида машинного критерия (форма `cdpm.model.CRITERION_KINDS`,
#: переносится в блок критерия как `label` — читатели пакета её ждут).
CRITERION_LABELS = {"min_person_days": "MIN_PERSON_DAYS",
                    "rank_normalized_cost": "NORMALIZED_COST"}

#: Режимы задачи (форма пакета; печатается только с `context`).
TASK_MODES = ("optimization", "feasibility-soft", "feasibility-hard")

#: Пояснение к целевой — дословно то, что пишет пакет сегодня.
CRITERION_NOTE = (
    "optimization: критерий = целевая + Σ штрафов (minimize) "
    "либо целевая − Σ штрафов (maximize); каждая целевая — "
    "отдельная задача. feasibility-soft: минимум Σ штрафов. "
    "feasibility-hard: любое допустимое.")

# Вес мягкой строки: машинное поле главнее распознавания текста, регэксп
# — запасной путь для строк, взвешенных до появления поля. Производитель
# CDPM делегирует сюда: legacy и modern не могут судить вес по-разному.
# Маркер допускает короткий идентификатор («штраф W_night = 10»), но
# совпадение первых цифр само по себе не подтверждает скалярный вес.
_WEIGHT_RE = re.compile(
    r"(?:вес(?:ом|а)?|weight|penalty|штраф(?:ом|а)?)\s*"
    r"(?:величиной|в размере|равен|равна|равный|составляет)?\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]{0,24}\s*)?"
    r"[—\-:=]?\s*(\d+)",
    re.IGNORECASE)

# Positive phrase boundaries, not a blacklist of mathematical symbols.
# Punctuation closes a scalar only at the end or before separated prose;
# a decimal, range, operator, numeric/combining/format suffix is not prose.
_WEIGHT_PHRASE_END_RE = re.compile(
    r"[)\]}»”’\"']*[.,;!?]?[)\]}»”’\"']*(?:\s*\Z|\s+[^\W\d_])")
_WEIGHT_PROSE_PREFIX_RE = re.compile(r"(?:[-–—]\s+|[(\[{«“\"]\s*)")


class ObjectiveProjectionError(ValueError):
    """Проекция невозможна: документ спорит с каталогом либо каталог
    направления вида не объявил. Умолчания нет — отказ словами."""


def judged_for_spec(spec_version) -> bool:
    """Судится ли пакет этой ревизии спецификации сверкой проекции.

    `None` — текущая сборка (судится). Ревизия ниже
    `PROJECTION_SPEC_FLOOR` — режим закреплённой версии: сверка не
    судится, читатель пишет об этом словами в паспорт (контракт §6.3).
    Нечитаемая ревизия — судится (fail-closed: непонятное не повод
    снять проверку)."""
    if spec_version in (None, ""):
        return True
    try:
        parts = [int(x) for x in str(spec_version).split(".")[:2]]
        floor = [int(x) for x in PROJECTION_SPEC_FLOOR.split(".")[:2]]
    except ValueError:
        return True
    return parts >= floor


def archive_words(spec_version) -> str:
    """Слова паспорта для пакета младшей ревизии (архивное чтение)."""
    return ("пакет ревизии спецификации %s ниже %s: objective.json с "
            "моделью не сверяется (режим закреплённой версии, контракт "
            "§6.3) — архивное чтение не есть разрешение исполнения"
            % (spec_version, PROJECTION_SPEC_FLOOR))


# ------------------------------------------------------- канон и каталог

def _nfc(value):
    """NFC рекурсивно: одна и та же строка в разных нормализациях дала
    бы разные байты подписи."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_nfc(str(k)): _nfc(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nfc(v) for v in value]
    return value


def canonical_bytes(objective: dict) -> bytes:
    """Байты проекции: ключи отсортированы, без пробелов, строки в NFC,
    UTF-8. Ими пакет подписывается манифестом, по ним сверяются стороны.

    Канон каталога (`template_catalog.canon`) здесь не годится: он
    запрещает дробные числа, а ставки критерия рангов законно десятичные
    (`vmp.criteria._deci` — кратность 0,1)."""
    return json.dumps(_nfc(objective), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _catalog(catalog):
    """Экземпляр читателя каталога: объект читателя — как есть; путь
    (строка/Path) — читается `template_catalog.load`. None — отказ
    словами: направление берётся из каталога, и «каталога нет» не повод
    подставить константу."""
    if catalog is None:
        raise ObjectiveProjectionError(
            "каталог шаблонов не передан: направление критерия и зоны "
            "берётся из записи каталога (criteria[].direction), "
            "константы «minimize» в проекции нет")
    if hasattr(catalog, "criteria"):
        return catalog
    return template_catalog.load(catalog)


def _catalog_version(catalog) -> str:
    try:
        return str(catalog.version())
    except Exception:                                     # noqa: BLE001
        return "?"


def criterion_direction(catalog, kind: str) -> str:
    """Направление вида критерия (или метрики) по ТАБЛИЦЕ каталога —
    единственный источник закона A06 для обеих сторон.

    Записи со статусом «есть»; поля `direction` у записи нет (каталог
    формы до 0.7.0) — отказ словами, а не «minimize по умолчанию».
    Значение — точный токен каталога, не свободный текст ответа."""
    cat = _catalog(catalog)
    kind_s = str(kind)
    records = cat.criteria()
    record = records.get(kind_s)
    if record is None:
        известные = ", ".join(sorted(records)) or "нет ни одной"
        raise ObjectiveProjectionError(
            "вида критерия %r нет среди записей каталога %s (есть: %s) — "
            "направление брать неоткуда"
            % (kind_s, _catalog_version(cat), известные))
    direction = record.get("direction")
    if direction in (None, ""):
        raise ObjectiveProjectionError(
            "направление вида %r в каталоге %s не задано (поле записи "
            "criteria[].direction, форма каталога 0.7.0) — проекция не "
            "подставляет «minimize» константой (находка A06)"
            % (kind_s, _catalog_version(cat)))
    # Match X20 exactly even through the public path/raw-reader entry point.
    # Normalizing malformed data here would make projection accept a catalogue
    # that the validated CDPM/COSMOS doors correctly refuse.
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise ObjectiveProjectionError(
            "направление вида %r в каталоге %s — %r, а словарь directions "
            "знает только %s"
            % (kind_s, _catalog_version(cat), record.get("direction"),
               ", ".join(DIRECTIONS)))
    return direction


# ------------------------------------------------------------ разбор модели

def _weight_prose_follows(text: str) -> bool:
    """A full prose word, not a single-letter or word/numeric operation.

    Two actual letters are required; accents may accompany them but do not
    count as letters. A number/symbol attached to the word or immediately
    following it is not proof of prose (e.g. x 2, до 20, до́ 20, to 20).
    """
    letters = end = 0
    for char in text:
        if char.isalpha():
            letters += 1
        elif not (letters and unicodedata.category(char).startswith("M")):
            break
        end += 1
    if letters < 2:
        return False
    tail = text[end:]
    if not tail or not tail.strip():
        return True
    if tail[0].isspace() and tail.lstrip()[0].isalpha():
        return True
    return bool(_WEIGHT_PHRASE_END_RE.match(tail.lstrip()))


def prose_integer(text: str, start: int, end: int) -> int | None:
    """Read a complete unsigned integer token with a proven prose boundary.

    The caller identifies its semantic marker; this helper never searches
    farther ahead after an unknown expression to pick another operand.
    """
    token = text[start:end]
    if not token or not token.isdecimal():
        return None
    if text[max(0, start - 1):start] in ("-", "+", "−"):
        return None
    tail = text[end:]
    suffix = tail.lstrip()
    if not suffix:
        return int(token)
    if tail[0].isspace():
        prefix = _WEIGHT_PROSE_PREFIX_RE.match(suffix)
        prose = suffix[prefix.end():] if prefix else suffix
        if _weight_prose_follows(prose):
            return int(token)
    if _WEIGHT_PHRASE_END_RE.match(suffix):
        return int(token)
    return None


def soft_weight(rule: dict) -> int | None:
    """Machine integer first; otherwise a complete legacy scalar token.

    This recognizes a numeric token and ordinary phrase boundaries, not
    the semantics of arbitrary natural-language formulas. Unknown numeric
    continuations remain unconfirmed, rather than becoming a first operand.
    """
    w = rule.get("weight")
    if isinstance(w, int) and not isinstance(w, bool):
        return w
    text = str(rule.get("statement", ""))
    mm = _WEIGHT_RE.search(text)
    if mm is None:
        return None
    return prose_integer(text, mm.start(1), mm.end(1))


def _family_of(rule: dict) -> str | None:
    """Семья строки: машинное поле либо посадка (`template_fit.family`)."""
    fam = rule.get("family")
    if not fam:
        fam = ((rule.get("template_fit") or {}) if isinstance(
            rule.get("template_fit"), dict) else {}).get("family")
    return str(fam) if fam else None


def _blockers_of(row: dict) -> list[str]:
    raw = row.get("blockers")
    return [str(q) for q in raw] if isinstance(raw, list) else []


def _excluded(rule: dict) -> bool:
    """Пометка «исключено до получения данных» (закон
    `vmp.registry_check.is_excluded_until_data`): строка остаётся в
    модели пакета, но в машинные формы решателя не идёт — в проекции
    она с `active: false` (контракт §11.2 п. 11)."""
    mark = rule.get("excluded_until_data")
    return isinstance(mark, dict) and bool(mark.get("question"))


def _criterion_block_complete(block) -> bool:
    """Исполним ли блок `criterion` целевой (закон
    `cdpm.model.criterion_block_complete`): вид словаря, версия и дата,
    ставка (min_person_days) либо ставки рангов."""
    if not isinstance(block, dict) \
            or block.get("kind") not in CRITERION_LABELS:
        return False
    if not block.get("version") or not block.get("effective_from"):
        return False
    if block["kind"] == "min_person_days":
        r = block.get("rate_per_duty")
        return isinstance(r, int) and not isinstance(r, bool) and r > 0
    rates = block.get("rates")
    return isinstance(rates, dict) and all(
        positive_finite_rate(rates.get(f)) for f in ("flight", "work"))


def positive_finite_rate(value) -> bool:
    """A JSON number in the positive finite rate domain, never bool/coerced text."""
    import math
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _sids(model: dict) -> set:
    return {str(s.get("sid")) for s in (model.get("sources") or [])
            if isinstance(s, dict) and s.get("sid")}


def _missing_sources(row: dict, sids: set) -> list[str]:
    """Источники строки, которых нет в разделе `sources` модели."""
    if not sids:
        # Модель источников не объявила — судить существование нечем
        # (архивные документы); молча «не существует» не говорим.
        return []
    out = []
    for s in (row.get("sources") or []):
        sid = str((s or {}).get("sid")) if isinstance(s, dict) else str(s)
        if sid and sid not in sids:
            out.append(sid)
    return out


# ------------------------------------------------------------------ план

def _objective_direction(obj: dict, cat) -> tuple[str | None, str]:
    """Направление целевой по каталогу и словами — чем оно обосновано.

    Опора (по старшинству): вид машинного критерия целевой, затем её
    метрика. Ни того, ни другого — опоры в каталоге нет, направление
    остаётся объявленным документом и таблицей не судится."""
    block = obj.get("criterion")
    if _criterion_block_complete(block):
        kind = str(block.get("kind"))
        return criterion_direction(cat, kind), \
            "каталог %s: %s → %%s" % (_catalog_version(cat), kind)
    metric = obj.get("metric")
    if metric:
        return criterion_direction(cat, str(metric)), \
            "каталог %s: метрика %s → %%s" % (_catalog_version(cat),
                                              str(metric))
    return None, ("документ: целевая %s объявила %%s, а опоры в каталоге "
                  "нет — ни машинного критерия, ни метрики"
                  % str(obj.get("id")))


def _zone_plan(model: dict, catalog, task: str) -> dict:
    """Что обязано быть в зоне `objective.json` по документу и каталогу.

    Один расчёт на обе стороны: `project` его печатает, `compare` по
    нему судит (иначе у производителя и приёмника разошлись бы законы)."""
    cat = _catalog(catalog)
    zones = TASK_ZONES[task]

    components = []
    for rule in (model.get("rules") or []):
        if not isinstance(rule, dict) or rule.get("kind") != "SOFT" \
                or rule.get("zone") not in zones:
            continue
        family = _family_of(rule)
        unit = None
        if family:
            try:
                record = cat.family(family)
            except Exception:                             # noqa: BLE001
                record = None
            if isinstance(record, dict):
                unit = {"violation_unit": record.get("violation_unit"),
                        "violation_size": record.get("violation_size")}
        blockers = _blockers_of(rule)
        components.append({
            "rule_id": str(rule.get("id")) if rule.get("id") else None,
            "role": "soft_penalty",
            "title": rule.get("title"),
            "statement": str(rule.get("statement", "")),
            "weight": soft_weight(rule),
            "family": family,
            "unit": unit,
            "zone": rule.get("zone"),
            # Исключённая либо заблокированная строка остаётся в перечне
            # (полнота сверки, §11.2 п. 11), но весом не участвует —
            # производитель вправе её не печатать.
            "active": not (_excluded(rule) or bool(blockers)),
            "blockers": blockers,
            "sources": rule.get("sources", []),
        })

    objectives, directions = [], []
    for obj in (model.get("objectives") or []):
        if not isinstance(obj, dict) or obj.get("zone") not in zones:
            continue
        declared = str(obj.get("direction") or "").strip().lower()
        by_catalog, basis = _objective_direction(obj, cat)
        if by_catalog is not None and declared and declared != by_catalog:
            raise ObjectiveProjectionError(
                "%s.objectives[%s].direction: документ требует %s, %s — "
                "направление целевой берётся из таблицы каталога "
                "(контракт §3.3), спор молча не разрешается"
                % (task, str(obj.get("id")), declared, basis % by_catalog))
        direction = by_catalog or (declared or None)
        if direction is not None:
            directions.append((direction, basis % direction))
        metric = obj.get("metric") or None
        row = {"id": str(obj.get("id")) if obj.get("id") else None,
               "role": "objective",
               "title": obj.get("title"),
               "statement": obj.get("statement"),
               "direction": direction,
               "sources": obj.get("sources", [])}
        if metric:
            row["metric"] = metric
            row["metric_direction"] = criterion_direction(cat, str(metric))
            if direction is not None \
                    and row["metric_direction"] != direction:
                # Машинный критерий целевой и её метрика тянут в разные
                # стороны — противоречие каталога и документа, а не
                # повод выбрать одно из двух молча.
                raise ObjectiveProjectionError(
                    "%s.objectives[%s]: метрика %s объявлена каталогом "
                    "как %s, а направление целевой — %s (%s)"
                    % (task, row["id"], metric, row["metric_direction"],
                       direction, basis % direction))
        if obj.get("rate") is not None:
            row["rate"] = obj.get("rate")
        row["active"] = not _blockers_of(obj)
        row["blockers"] = _blockers_of(obj)
        objectives.append(row)

    # Направление зоны: одно на зону. Целевые спорят между собой —
    # отказ словами; целевых нет — минимизация суммы штрафов.
    уникальные = sorted({d for d, _ in directions})
    if len(уникальные) > 1:
        raise ObjectiveProjectionError(
            "%s.direction: целевые зоны требуют разных направлений (%s) "
            "— у зоны одно направление (%s)"
            % (task, ", ".join(уникальные),
               "; ".join(слова for _, слова in directions)))
    if уникальные:
        direction = уникальные[0]
        basis = next(слова for d, слова in directions if d == direction)
    else:
        direction = PENALTY_SUM_DIRECTION
        basis = ("целевых у зоны нет: направление суммы штрафов "
                 "(feasibility-soft) — %s" % PENALTY_SUM_DIRECTION)

    criterion = _pairing_criterion(model, cat) if task == "pairing" else None

    return {"task": task, "catalog": cat, "direction": direction,
            "direction_basis": basis,
            "components": components, "objectives": objectives,
            "criterion": criterion,
            "airline_answer": _airline_answer(model, task)}


def _pairing_criterion(model: dict, cat) -> dict | None:
    """Машинный критерий пейринга из ДОКУМЕНТА (блок `criterion` целевой
    зоны — закон `cdpm.model.criterion_from_objectives`), с направлением
    из каталога. Носителя в документе нет — `None`: критерий
    производителя выведен из ответа анкеты либо прозы, содержимое такого
    критерия проекцией не судится (граница названа в шапке модуля)."""
    for obj in (model.get("objectives") or []):
        if not isinstance(obj, dict) \
                or obj.get("zone") not in TASK_ZONES["pairing"]:
            continue
        block = obj.get("criterion")
        if not _criterion_block_complete(block):
            continue
        kind = str(block["kind"])
        out = {"kind": kind,
               "label": CRITERION_LABELS[kind],
               "direction": criterion_direction(cat, kind),
               "source_rule": str(obj.get("id")),
               "as_written": str(block.get("as_written") or ""),
               "source_objective": str(obj.get("id")),
               "rates_version": {"source": str(obj.get("id")),
                                 "version": str(block.get("version")),
                                 "effective_from":
                                     str(block.get("effective_from"))}}
        if block.get("per"):
            out["per"] = str(block.get("per"))
        if kind == "min_person_days":
            out["rate_per_duty"] = block["rate_per_duty"]
        else:
            out["rates"] = {"flight": block["rates"]["flight"],
                            "work": block["rates"]["work"]}
        return out
    return None


def _airline_answer(model: dict, task: str) -> dict | None:
    """Первый применённый ответ части 3а/3б в порядке questions модели.

    Это правило выбора сырого слова о целевой, не старшинство источников
    машинного критерия: его источник определяется отдельно. Перестановка
    вопросов может изменить этот сырой ответ; writer и compare используют
    одну функцию и один порядок, без предпочтения специального question_id.
    """
    part = ANSWER_PART[task]
    for q in (model.get("questions") or []):
        if not isinstance(q, dict) or q.get("part") != part:
            continue
        answer = q.get("answer") or {}
        if answer.get("status") == "применён":
            return {"question_id": q.get("id"),
                    "raw": answer.get("raw", "")}
    return None


# --------------------------------------------------------------- проекция

def _render_component(plan_row: dict) -> dict:
    out = {k: plan_row[k] for k in
           ("rule_id", "role", "title", "statement", "weight", "family",
            "unit", "zone", "active", "sources")}
    if plan_row["blockers"]:
        out["blockers"] = plan_row["blockers"]
    return out


def _render_objective(plan_row: dict) -> dict:
    out = {"id": plan_row["id"], "role": plan_row["role"],
           "title": plan_row["title"], "statement": plan_row["statement"],
           "direction": plan_row["direction"]}
    if plan_row.get("metric"):
        out["metric"] = plan_row["metric"]
        out["metric_direction"] = plan_row["metric_direction"]
    if plan_row.get("rate") is not None:
        out["rate"] = plan_row["rate"]
    out["active"] = plan_row["active"]
    if plan_row["blockers"]:
        out["blockers"] = plan_row["blockers"]
    out["sources"] = plan_row["sources"]
    return out


def _plans(model: dict, cat, context: dict | None) -> dict:
    plans = {task: _zone_plan(model, cat, task) for task in TASKS}
    supplied = (context or {}).get("criterion")
    has_context_criterion = context is not None and "criterion" in context
    if has_context_criterion and supplied is not None and not isinstance(supplied, dict):
        raise ObjectiveProjectionError("context.criterion: нужен объект либо явное null")
    if plans["pairing"]["criterion"] is None and has_context_criterion:
        # An explicit null from the model reader means known absence, unlike
        # a caller that has not supplied the external derivation at all.
        plans["pairing"]["criterion_from_context"] = True
    if plans["pairing"]["criterion"] is None and isinstance(supplied, dict):
        criterion = copy.deepcopy(supplied)
        kind = str(criterion.get("kind"))
        direction = criterion_direction(cat, kind)
        if criterion.get("direction", direction) != direction:
            raise ObjectiveProjectionError(
                "pairing.criterion.direction: контекст спорит с каталогом "
                f"({criterion.get('direction')!r} != {direction!r})")
        criterion["direction"] = direction
        plans["pairing"]["criterion"] = criterion
    return plans


def project(model: dict, catalog, *, context: dict | None = None) -> dict:
    """Содержимое `objective.json` по документу модели и каталогу.

    `model` — канон ВМП пакета (`model.json`: meta, sources, rules,
    objectives, questions); `catalog` — экземпляр читателя
    `template_catalog` (или путь к каталогу). `context` — необязательные
    значения, которых в документе НЕТ и которые считает производитель
    (`readiness`, `task_modes`, `matrix`, `ready` по задачам, выведенный
    `criterion`); без него проекция их просто не печатает и сверка их не
    судит.

    Отказ `ObjectiveProjectionError`: документ спорит с таблицей каталога
    о направлении, каталог направления вида не объявил, целевые одной
    зоны требуют разных направлений."""
    if not isinstance(model, dict):
        raise ObjectiveProjectionError(
            "документ модели — не объект: %r" % (type(model).__name__,))
    cat = _catalog(catalog)
    ctx = context or {}
    планы = _plans(model, cat, ctx)

    out: dict = {"projection_version": PROJECTION_VERSION,
                 "catalog": {"version": _catalog_version(cat),
                             "fingerprint": _fingerprint(cat)},
                 "customer": (model.get("meta") or {}).get("customer")}
    if ctx.get("spec_version"):
        out["spec_version"] = ctx["spec_version"]
    if ctx.get("readiness") is not None:
        out["readiness"] = ctx["readiness"]
    if ctx.get("task_modes") is not None:
        out["task_modes"] = list(ctx["task_modes"])
    if ctx.get("matrix") is not None:
        out["matrix"] = ctx["matrix"]
    out["criterion_note"] = CRITERION_NOTE

    ready = ctx.get("ready") or {}
    notes = ctx.get("note") or {}
    for task in TASKS:
        план = планы[task]
        зона: dict = {}
        if task in ready:
            зона["ready"] = bool(ready[task])
        зона["direction"] = план["direction"]
        зона["direction_basis"] = план["direction_basis"]
        зона["components"] = [_render_component(c)
                              for c in план["components"]]
        if план["airline_answer"] is not None:
            зона["airline_answer"] = план["airline_answer"]
        if notes.get(task):
            зона["note"] = notes[task]
        зона["objectives"] = [_render_objective(o) for o in план["objectives"]]
        if task == "pairing":
            зона["criterion"] = план["criterion"]
        out[task] = зона
    return out


def _fingerprint(cat) -> str | None:
    try:
        return str(cat.fingerprint())
    except Exception:                                     # noqa: BLE001
        return None


# ----------------------------------------------------------------- сверка

def _problem(code: str, where: str, expected, got, words: str) -> dict:
    return {"code": code, "where": where, "expected": expected,
            "got": got, "words": words}


def problem_words(problems) -> str:
    """Слова перечня расхождений одной строкой — для отказа читателю."""
    return "; ".join(str(p.get("words") if isinstance(p, dict) else p)
                     for p in (problems or []))


def _same_value(expected, got) -> bool:
    # JSON booleans are not numeric weights/rates (Python True == 1).
    # Numeric 1 and 1.0 remain equivalent for decimal criterion rates.
    if isinstance(expected, bool) or isinstance(got, bool):
        return type(expected) is type(got) and expected == got
    if isinstance(expected, dict) or isinstance(got, dict):
        return (isinstance(expected, dict) and isinstance(got, dict)
                and expected.keys() == got.keys()
                and all(_same_value(v, got[k]) for k, v in expected.items()))
    if isinstance(expected, list) or isinstance(got, list):
        return (isinstance(expected, list) and isinstance(got, list)
                and len(expected) == len(got)
                and all(_same_value(a, b) for a, b in zip(expected, got)))
    return expected == got


def _cmp_scalar(problems: list, where: str, expected, got, *,
                code: str = CODE_MISMATCH) -> None:
    if _same_value(expected, got):
        return
    problems.append(_problem(
        code, where, expected, got,
        "%s: %r ≠ %r (модель)" % (where, got, expected)))


def compare(objective_json, model: dict, catalog, *,
            context: dict | None = None) -> list[dict]:
    """Расхождения предъявленного `objective.json` с проекцией модели.

    Возврат — перечень проблем `{code, where, expected, got, words}`;
    пустой перечень = файл равен проекции в судимой части (граница
    судимого названа в шапке модуля). Коды — раздела 5 контракта:
    `objective-mismatch` и `criterion-direction`.

    Правило терпимости, без которого сверка давала бы ложные отказы:
    поля, которых производитель мог не печатать (обогащение формы 6.2 —
    `family`, `unit`, `active`, `role`, `blockers`), судятся ТОЛЬКО
    когда предъявленный файл их несёт; строка, помеченная проекцией как
    неактивная (исключённая до данных либо заблокированная), в файле
    может и отсутствовать — производитель режет её срезом."""
    try:
        cat = _catalog(catalog)
        планы = _plans(model, cat, context)
    except ObjectiveProjectionError as e:
        return [_problem(CODE_DIRECTION, "objective.json", None, None,
                         "проекция невозможна: %s" % e)]
    if not isinstance(objective_json, dict) or not objective_json:
        return [_problem(
            CODE_MISMATCH, "objective.json", "проекция модели пакета",
            objective_json,
            "objective.json пуст либо не объект — сверять нечего, "
            "а пакет обязан нести целевую, равную проекции модели")]

    problems: list[dict] = []
    customer = (model.get("meta") or {}).get("customer")
    if "customer" in objective_json and customer is not None:
        _cmp_scalar(problems, "customer", customer,
                    objective_json.get("customer"))
    for task in TASKS:
        _cmp_zone(problems, objective_json.get(task), планы[task], model)
    return problems


def _cmp_zone(problems: list, got, план: dict, model: dict) -> None:
    task = план["task"]
    if not isinstance(got, dict):
        problems.append(_problem(
            CODE_MISMATCH, task, "зона целевой", got,
            "%s: зоны в objective.json нет (ожидались компоненты, "
            "целевые и направление модели)" % task))
        return

    # 1. Направление зоны — таблицей каталога (находка A06).
    if got.get("direction") != план["direction"]:
        problems.append(_problem(
            CODE_DIRECTION, "%s.direction" % task, план["direction"],
            got.get("direction"),
            "%s.direction %s ≠ %s (%s)"
            % (task, got.get("direction"), план["direction"],
               план["direction_basis"])))

    # 2. Компоненты — состав, веса, существование источников.
    sids = _sids(model)
    ожидаемые = {c["rule_id"]: c for c in план["components"] if c["rule_id"]}
    предъявленные: dict = {}
    for row in (got.get("components") or []):
        if not isinstance(row, dict):
            problems.append(_problem(
                CODE_MISMATCH, "%s.components" % task, "объект", row,
                "%s.components: строка перечня не объект (%r)"
                % (task, row)))
            continue
        rid = str(row.get("rule_id")) if row.get("rule_id") else None
        if rid in предъявленные:
            problems.append(_problem(
                CODE_MISMATCH, f"{task}.components[{rid}]", "unique rule_id", rid,
                f"{task}.components: повтор rule_id {rid!r}"))
        предъявленные[rid] = row
        план_строки = ожидаемые.get(rid)
        адрес = "%s.components[%s]" % (task, rid)
        if план_строки is None:
            problems.append(_problem(
                CODE_MISMATCH, адрес, "правило модели зоны %s" % task, rid,
                "%s: правила нет среди мягких строк зоны в модели пакета"
                % адрес))
            continue
        _cmp_scalar(problems, адрес + ".weight", план_строки["weight"],
                    row.get("weight"))
        for поле in ("family", "zone", "active"):
            if поле in row:
                _cmp_scalar(problems, "%s.%s" % (адрес, поле),
                            план_строки[поле], row.get(поле))
        for sid in _missing_sources(row, sids):
            problems.append(_problem(
                CODE_MISMATCH, адрес + ".sources", "источник модели", sid,
                "%s.sources: %s — источника нет в model.json"
                % (адрес, sid)))
    for rid, строка in ожидаемые.items():
        if rid in предъявленные or not строка["active"]:
            continue
        problems.append(_problem(
            CODE_MISMATCH, "%s.components[%s]" % (task, rid),
            строка["weight"], None,
            "%s.components: мягкого правила %s модели в objective.json "
            "нет (вес %r)" % (task, rid, строка["weight"])))

    # 3. Целевые — состав, направления, метрики.
    ожид_цели = {o["id"]: o for o in план["objectives"] if o["id"]}
    предъяв_цели: dict = {}
    for row in (got.get("objectives") or []):
        if not isinstance(row, dict):
            problems.append(_problem(
                CODE_MISMATCH, "%s.objectives" % task, "объект", row,
                "%s.objectives: строка перечня не объект (%r)"
                % (task, row)))
            continue
        oid = str(row.get("id")) if row.get("id") else None
        if oid in предъяв_цели:
            problems.append(_problem(
                CODE_MISMATCH, f"{task}.objectives[{oid}]", "unique id", oid,
                f"{task}.objectives: повтор id {oid!r}"))
        предъяв_цели[oid] = row
        план_цели = ожид_цели.get(oid)
        адрес = "%s.objectives[%s]" % (task, oid)
        if план_цели is None:
            problems.append(_problem(
                CODE_MISMATCH, адрес, "целевая модели зоны %s" % task, oid,
                "%s: целевой нет среди целевых зоны в модели пакета"
                % адрес))
            continue
        if план_цели["direction"] is not None:
            direction = str(row.get("direction") or "").strip().lower()
            if direction != план_цели["direction"]:
                problems.append(_problem(
                    CODE_DIRECTION, адрес + ".direction",
                    план_цели["direction"], row.get("direction"),
                    "%s.direction %s ≠ %s (%s)"
                    % (адрес, row.get("direction"), план_цели["direction"],
                       план["direction_basis"])))
        _cmp_scalar(problems, адрес + ".metric", план_цели.get("metric"),
                    row.get("metric") or None)
        # The declared price of one metric unit is not an optional display
        # enrichment or the objective's tier. Losing or changing it changes
        # the objective's contribution relative to the soft penalties.
        _cmp_scalar(problems, адрес + ".rate", план_цели.get("rate"),
                    row.get("rate"))
        for поле in ("title", "statement"):
            if поле in row and план_цели.get(поле) is not None:
                _cmp_scalar(problems, "%s.%s" % (адрес, поле),
                            план_цели[поле], row.get(поле))
        for sid in _missing_sources(row, sids):
            problems.append(_problem(
                CODE_MISMATCH, адрес + ".sources", "источник модели", sid,
                "%s.sources: %s — источника нет в model.json"
                % (адрес, sid)))
    for oid, строка in ожид_цели.items():
        if oid in предъяв_цели or not строка["active"]:
            continue
        problems.append(_problem(
            CODE_MISMATCH, "%s.objectives[%s]" % (task, oid),
            строка["direction"], None,
            "%s.objectives: целевой %s модели в objective.json нет"
            % (task, oid)))

    # 4. Сырой ответ авиакомпании о целевой: судится, когда предъявлен
    # (потеря справочного поля — не подмена, а вот чужой ответ — да).
    предъявлен = got.get("airline_answer")
    if isinstance(предъявлен, dict):
        ожидаемый = план["airline_answer"]
        if ожидаемый is None:
            problems.append(_problem(
                CODE_MISMATCH, "%s.airline_answer" % task, None, предъявлен,
                "%s.airline_answer: ответа авиакомпании о целевой нет "
                "среди применённых ответов модели пакета" % task))
        else:
            for поле in ("question_id", "raw"):
                if поле in предъявлен:
                    _cmp_scalar(problems,
                                "%s.airline_answer.%s" % (task, поле),
                                ожидаемый.get(поле), предъявлен.get(поле))

    # 5. Машинный критерий пейринга.
    if task == "pairing":
        first_problem = len(problems)
        _cmp_criterion(problems, got.get("criterion"), план)
        origin = _criterion_origin_words(план["criterion"])
        if origin:
            for problem in problems[first_problem:]:
                problem["words"] += "; эталон из " + origin


def _criterion_origin_words(expected) -> str:
    """Name the trusted model carrier, never the presented file's claim."""
    if not isinstance(expected, dict):
        return ""
    if expected.get("source_objective"):
        return "блока criterion целевой " + str(expected["source_objective"])
    answer = expected.get("source_answer")
    if isinstance(answer, dict) and answer.get("question"):
        return "ответа " + str(answer["question"])
    if expected.get("source_rule"):
        return "формулировки правила " + str(expected["source_rule"])
    return ""


def _cmp_criterion(problems: list, got, план: dict) -> None:
    ожидаемый = план["criterion"]
    if ожидаемый is None:
        if план.get("criterion_from_context"):
            if got is not None:
                problems.append(_problem(
                    CODE_MISMATCH, "pairing.criterion", None, got,
                    "pairing.criterion: модель не выводит критерий, "
                    "но objective.json его объявляет"))
            return
        if isinstance(got, dict) and got.get("kind"):
            # Критерий выведен производителем не из документа (ответ
            # анкеты либо проза): судим вид и направление, содержимое —
            # нет (граница названа в шапке модуля).
            kind = str(got.get("kind"))
            try:
                direction = criterion_direction(план["catalog"], kind)
            except ObjectiveProjectionError as e:
                problems.append(_problem(
                    CODE_DIRECTION, "pairing.criterion.kind", "вид каталога",
                    kind,
                    "pairing.criterion: %s" % e))
                return
            if got.get("direction") \
                    and str(got.get("direction")).strip().lower() != direction:
                problems.append(_problem(
                    CODE_DIRECTION, "pairing.criterion.direction",
                    direction, got.get("direction"),
                    "pairing.criterion.direction %s ≠ %s (каталог: %s → %s)"
                    % (got.get("direction"), direction, kind, direction)))
        return
    if not isinstance(got, dict) or not got:
        problems.append(_problem(
            CODE_MISMATCH, "pairing.criterion", ожидаемый.get("kind"), got,
            "pairing.criterion: машинный критерий модели (%s, носитель %s) "
            "в objective.json не перенесён"
            % (ожидаемый.get("kind"), ожидаемый.get("source_objective"))))
        return
    # CDPM-420: every field of the trusted projection is required, regardless
    # of whether its carrier was a document, an answer or prose. In particular,
    # nested rate provenance is not weaker than the amount/version itself.
    # CDPM-423: the trusted projection also defines the closed field set.
    # Unknown fields must not travel to consumers as unjudged criterion data.
    for field in sorted(set(got) - set(ожидаемый)):
        where = "pairing.criterion." + field
        problems.append(_problem(
            CODE_MISMATCH, where, None, got[field],
            "%s: лишнее поле в pairing.criterion, отсутствует в модели" % where))
    for field, expected in ожидаемый.items():
        where = "pairing.criterion." + field
        code = CODE_DIRECTION if field == "direction" else CODE_MISMATCH
        if field not in got:
            problems.append(_problem(
                code, where, expected, None,
                "%s: поле модели отсутствует в objective.json" % where))
        else:
            _cmp_scalar(problems, where, expected, got[field], code=code)
