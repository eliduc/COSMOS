# -*- coding: utf-8 -*-
"""Машинная модель CDPM: свод правил и анкета как ОДИН документ.

Ключевое контрактное решение (план первой очереди, §7.2): единый
JSON-документ — источник истины; md-свод и md-анкета рендерятся из него
(`cdpm.render`). Урок методики §6 «ответы вносятся в свод, иначе анкета
и свод расходятся» закрыт по построению: расходиться нечему.

Слои проверки:

- структурный — опубликованная JSON-схема
  (`cdpm/schemas/cdpm-model.schema.json`, проверка `validate_schema`);
- инвариантный — перекрёстные ссылки и правила методики, которые схеме
  не выразить (`validate_model`); каждый инвариант оплачен уроком линий
  обеих линий и назван в сообщении об ошибке.

Условность типа (вида HARD/SOFT из свода пилотной линии) в машинной форме —
не строка-тип, а объект `conditional`: вопрос + явная ветвь результата
для КАЖДОГО варианта ответа (урок B7). Тип-строка всегда одиночный.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from cdpm.diagnostics import summarize_items

# Шаг 7 переезда на каталог шаблонов (стадия 1а): словарь параметров,
# перечни и карта семей — ТОЛЬКО из каталога через один экземпляр на
# процесс. Запаса из кода нет: без каталога — отказ словами
# (catalog_bridge.CatalogRequired), а не молчаливый словарь кода.
from cdpm import catalog_bridge as _catalog_bridge

#: Имена словарей каталога, которые модуль отдаёт модульным
#: `__getattr__` (PEP 562), а не держит в globals(): импорт модуля без
#: каталога обязан проходить, отказ возникает при ОБРАЩЕНИИ. Внутри
#: модуля голых имён из этого перечня нет — только `_cat("ИМЯ")`
#: (машинный сторож — AST-гейт tests/test_cdpm_catalog_source.py).
_CATALOG_NAMES = ("PARAMS", "PARAM_UNITS", "CODE_UNITS", "PARAM_RELATIONS",
                  "PARAM_TZ", "AREA_QUALIFIERS", "AREA_PARAM_NAMES",
                  "FIT_CATEGORIES", "METRICS")


def _cat(name: str):
    """Словарь каталога по имени — ЕДИНСТВЕННАЯ дверь модуля к словарям
    (снимок экземпляра каталога у моста; два обращения подряд отдают тот
    же объект). Без каталога — `CatalogRequired` словами."""
    if name == "PARAMS":
        return _catalog_bridge.params()
    if name == "METRICS":
        return _catalog_bridge.metrics(_METRIC_WORDS)
    return _catalog_bridge.vocab(name.lower())


def __getattr__(name: str):
    """PEP 562: словари каталога отдаются при обращении к модулю —
    и `m.PARAMS`, и `from cdpm.model import PARAMS`."""
    if name in _CATALOG_NAMES:
        return _cat(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(list(globals()) + list(_CATALOG_NAMES)))


class DuplicateJsonKey(ValueError):
    """Дубль ключа в ИСХОДНЫХ байтах JSON (A05, находка Н-3; класс X-04
    карты предпроверки — молчаливая подмена значения)."""


def _json_path_of(doc, target_id: int) -> str:
    """Путь объекта в документе по ЕГО ИДЕНТИЧНОСТИ («rules[3].
    template_fit.params.max_pairing_days»), либо пусто — объект
    корневой (или выброшен дублем у родителя). Обход в ширину в порядке
    документа: путь один и тот же у двух чтений одних байт."""
    stack = [(doc, "")]
    while stack:
        node, path = stack.pop(0)
        if id(node) == target_id:
            return path
        if isinstance(node, dict):
            stack.extend((v, f"{path}.{k}" if path else str(k))
                         for k, v in node.items())
        elif isinstance(node, list):
            stack.extend((v, f"{path}[{i}]") for i, v in enumerate(node))
    return ""


def loads_strict(text):
    """`json.loads` БЕЗ молчаливой подмены значения (A05, находка Н-3).

    Библиотека читает `{"value": 3, "value": 30}` как 30: последнее
    объявление беззвучно побеждает первое. В байтах `model.json` это
    подмена величины БЕЗ СЛЕДА — ни свод, ни экран, ни сверка носителей
    первого значения больше не видят, а пакет уезжает с тридцатью
    (класс X-04). Закон: дубль ключа — ОТКАЗ словами, называющими и
    ключ, и его путь в документе; догадки «взять первое» тоже нет.

    Вход — текст либо байты UTF-8; отказ — `DuplicateJsonKey` (потомок
    `ValueError`, поэтому прежние ловушки `json.JSONDecodeError`
    дополняются, а не подменяются). Честные байты читаются как прежде:
    результат равен `json.loads` тех же байт. Нестандартные NaN/Infinity
    и переполнение конечного литерала в infinity отвергаются до миграции."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    marks: list = []
    keep: list = []          # держим объекты живыми: id не переиспользуется
    dups: dict = {}

    def hook(pairs):
        obj: dict = {}
        found: list = []
        for key, value in pairs:
            if key in obj and key not in found:
                found.append(key)
            obj[key] = value
        if found:
            keep.append(obj)
            dups[id(obj)] = found
            marks.append(id(obj))
        return obj

    def reject_nonfinite(token):
        # Hooks do not receive an offset. On this error-only path locate the
        # first matching JSON number, skipping complete quoted strings (and
        # their escaped quotes). Earlier non-finite numbers could not have
        # passed the same hooks, so this is the token the parser just saw.
        tokens = re.finditer(
            r'"(?:\\.|[^"\\])*"|(?P<number>NaN|-?Infinity|'
            r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)', text)
        pos = next(match.start("number") for match in tokens
                   if match.group("number") == token)
        raise json.JSONDecodeError(f"недопустимое неконечное JSON-число: {token}", text, pos)

    def finite_float(token):
        import math
        value = float(token)
        if not math.isfinite(value):
            reject_nonfinite(token)
        return value

    doc = json.loads(text, object_pairs_hook=hook,
                     parse_constant=reject_nonfinite, parse_float=finite_float)
    if not marks:
        return doc
    # Путь считается ПОСЛЕ разбора: `object_pairs_hook` зовётся снизу
    # вверх и о родителе не знает, а готовое дерево знает всё.
    words = []
    for oid in marks:
        where = _json_path_of(doc, oid) or "корень документа"
        for key in dups[oid]:
            words.append(f"ключ {key!r} объявлен дважды в {where}")
    raise DuplicateJsonKey(
        "дубль ключа в исходных байтах JSON: " + "; ".join(words[:4])
        + (f" (и ещё {len(words) - 4})" if len(words) > 4 else "")
        + " — последнее объявление молча заменило бы первое; оставьте "
          "один ключ и повторите")


ZONES = ("PAIRING", "ROSTERING", "BOTH")
KINDS = ("HARD", "SOFT", "DEF", "DATA", "CASE", "OUT")
PARTS = ("1", "2", "3a", "3b", "4")
ANSWER_TYPES = ("choice", "value", "registry", "text")
# ЦФ-1 (постановка владельца 2026-08-21): целевые функции — ОТДЕЛЬНАЯ
# коллекция модели (не строки свода — конвенция пилотной линии, где
# целевая жила отдельной таблицей, сохраняется). Каждая целевая —
# отдельная задача
# оптимизации своей зоны; направление определяет знак свёртки со
# штрафами мягких (minimize: цель + штрафы; maximize: цель − штрафы).
DIRECTIONS = ("minimize", "maximize")
# Режимы задачи: optimization (целевая + штрафы), feasibility/soft
# (минимум штрафов мягких), feasibility/hard (любое допустимое).
TASK_MODES = ("optimization", "feasibility-soft", "feasibility-hard")
# Статусы принятого ответа авиакомпании (интерактивный цикл, ИР-1):
# «применён» — следствие внесено в свод машинно; «ожидает обработчика»
# — ответ разобран, но внесение в строки требует модельного патча под
# контрактом; «не разобран» — ответ не сопоставился с форматом вопроса
# и в новой анкете вопрос задаётся повторно.
REVISION_CONFLICT_STATUS = "пересмотр: конфликт"
PARTIAL_ANSWER_STATUS = "применён частично"
ANSWER_STATUSES = ("применён", "ожидает обработчика", "не разобран",
                   "пересмотрен", REVISION_CONFLICT_STATUS, PARTIAL_ANSWER_STATUS)

SCHEMA_PATH = Path(__file__).parent / "schemas" / "cdpm-model.schema.json"

# Человеческие имена частей анкеты — единственное место, где они заданы;
# рендеры берут отсюда, чтобы свод и анкета не разъехались в названиях.
PART_TITLES = {
    "1": "Часть 1. Противоречия источников",
    "2": "Часть 2. Неоднозначности (развилки чтения)",
    "3a": "Часть 3а. Вопросы постановки — пейринг",
    "3b": "Часть 3б. Вопросы постановки — ростеринг",
    "4": "Часть 4. Несоответствия формализации третьей стороны",
}


def is_open_question(q: dict, *, doc: dict | None = None) -> bool:
    """Открытый вопрос анкеты текущей ревизии: без ответа либо ответ
    не разобрался (задаётся повторно). Единственное место определения —
    им пользуются рендеры, Excel-издание и гейты (ИР-1). Мусорный тип
    ответа считается открытым вопросом (fail-closed), падать нельзя."""
    from cdpm import question_resolutions
    if doc is not None and question_resolutions.problems(doc, q):
        return True
    if question_resolutions.shape_problems(q):
        return True
    if question_resolutions.complete(q):
        return False
    ans = q.get("answer")
    return not isinstance(ans, dict) \
        or ans.get("status") in ("не разобран", REVISION_CONFLICT_STATUS,
                                  PARTIAL_ANSWER_STATUS)


def current_questions(doc: dict, part: str | None = None) -> list[dict]:
    """Current editions in document order; invalid successors retire nothing."""
    from cdpm import question_editions
    return [q for q in doc.get("questions", [])
            if isinstance(q, dict) and (part is None or q.get("part") == part)
            and not question_editions.is_superseded(doc, q)]


def open_questions(doc: dict, part: str | None = None) -> list[dict]:
    """Actionable open editions; superseded records remain in history."""
    return [q for q in current_questions(doc, part) if is_open_question(q, doc=doc)]


def question_rules(doc: dict, q: dict) -> list[dict] | None:
    """Resolve the whole ordinary question scope, never just its valid subset.

    None means unknown scope and must block both tasks. A dangling, ambiguous
    or malformed reference cannot disappear beside an otherwise valid one.
    """
    refs = q.get("rule_refs")
    rules = doc.get("rules")
    if not isinstance(refs, list) or not refs or not isinstance(rules, list):
        return None
    resolved = []
    for rid in refs:
        if not isinstance(rid, str) or not rid:
            return None
        matches = [r for r in rules if isinstance(r, dict) and r.get("id") == rid]
        if len(matches) != 1 or matches[0].get("zone") not in ZONES:
            return None
        resolved.append(matches[0])
    return resolved


def family_consumer_tasks(family: str) -> tuple[str, ...]:
    """Declared family consumers shared by missing-value and question gates.

    A carrier's zone does not bound its family's consumers: e.g. rest_floors
    is a rostering family but its missing thresholds also stop the pairing
    builder. Unknown identity/catalogue/consumer kinds cannot narrow scope.
    This is declared consumption, not a claim of approved runtime support.
    """
    both = ("pairing", "rostering")
    try:
        kinds = set(_catalog_bridge.executor_kinds(family))
    except _catalog_bridge.CATALOG_ERRORS:
        return both
    roster = {"kotlin_constraint", "kotlin_knob", "kotlin_logic_form", "kotlin_form", "kotlin_metric"}
    shared = {"check_code", "check_template", "export_form"}
    if not kinds or kinds - (roster | shared | {"builder_method"}):
        return both
    tasks = tuple(task for task, present in (
        ("pairing", "builder_method" in kinds), ("rostering", bool(kinds & roster))) if present)
    return tasks or both


def parameter_question_tasks(q: dict) -> tuple[str, ...] | None:
    """Consumer tasks for a catalogue parameter; None for an ordinary question."""
    from cdpm import family_params
    if not family_params.is_param_question(q.get("revision_of") or q.get("id")):
        return None
    try:
        target = family_params.question_target(q)
    except _catalog_bridge.CATALOG_ERRORS:
        return ("pairing", "rostering")
    return family_consumer_tasks(target[0]) if target else ("pairing", "rostering")


def question_tasks(doc: dict, q: dict) -> tuple[str, ...]:
    """Tasks addressed by an active question; unknown scope is fail-closed.

    Ordinary questions follow rule identities, not the questionnaire part.
    Parameter questions additionally address the catalogue's consumer tasks.
    Goal/criterion questions have their own established identity contract.
    This is a scope filter, not package readiness or a mode authorization.
    """
    both = ("pairing", "rostering")
    qid = q.get("id")
    if "edition" in q or q.get("edition_of"):
        from cdpm import question_editions
        questions = doc.get("questions")
        if not isinstance(questions, list) or not all(isinstance(x, dict) for x in questions):
            return both
        ids = [x.get("id") for x in questions]
        if not all(isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            return both
        try:
            qid = question_editions.lineage_root(doc, q)
        except question_editions.EditionRefusal:
            return both
    if is_goal_family(qid):
        return ("pairing",) if qid.startswith("Q-3a-") else ("rostering",)
    if qid == CRITERION_QUESTION_ID:
        return ("pairing",)
    resolved = question_rules(doc, q)
    if resolved is None:
        return both
    zones = {r["zone"] for r in resolved}
    consumers = parameter_question_tasks(q) or ()
    return tuple(task for task, zone in (("pairing", "PAIRING"), ("rostering", "ROSTERING"))
                 if zone in zones or "BOTH" in zones or task in consumers)


def loads_model(text: str | bytes | bytearray) -> dict:
    """Decode one model snapshot, reject duplicate keys, migrate in memory.

    Input bytes remain evidence: no rewrite, invented receipt facts or repair
    of an existing invalid identity. Raw artifact/manifest readers must keep
    using their own exact-byte validation, not silently migrate evidence.
    """
    from cdpm import question_editions, answer_versions, answer_migration, landing_migration
    doc = loads_strict(text)
    question_editions.migrate_legacy_document(doc)
    answer_versions.migrate_document(doc)
    answer_migration.migrate_document(doc)
    return landing_migration.migrate_document(doc)


def load_model(path: str | Path) -> dict:
    """Read once without disk writes; share migration with byte/text readers."""
    return loads_model(Path(path).read_text(encoding="utf-8"))


def dump_model(doc: dict, path: str | Path) -> None:
    """Publish complete model bytes without truncating the previous snapshot."""
    import uuid
    from cdpm.file_replace import replace_with_retry

    text = json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    stage = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        stage.write_text(text, encoding="utf-8")
        replace_with_retry(stage, path)
    except BaseException:
        # Only our uncommitted staging file is disposable, never the input.
        # A cleanup error must not hide the write failure or cancellation.
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def publish_model(doc: dict, path: str | Path) -> None:
    """Persist question snapshots BEFORE publishing a book from this model.

    A pre-existing book is a legacy publication, not an unissued draft.
    Answered questions also retain their v1 identity so old provenance does
    not change when a new output directory is created. Ordinary dump_model
    stays a draft serializer; rendering remains read-only.
    """
    import copy
    from cdpm import question_editions
    trial = copy.deepcopy(doc)
    legacy_book = Path(path).with_name("anketa.xlsx").is_file()
    for question in trial.get("questions", []):
        question_editions.freeze(question, legacy=legacy_book or bool(
            question.get("answer")))
    dump_model(trial, path)
    doc.clear()
    doc.update(trial)


def _slug(text: str) -> str:
    """Слаг якоря для стабильного идентификатора: кириллица сохраняется
    (идентификаторы читает специалист авиакомпании — П.1.9 узнаваемее
    транслита), пробелы и разделители сводятся к дефису."""
    text = unicodedata.normalize("NFC", text).strip()
    text = re.sub(r"[\s/\\.,;:]+", "-", text)
    return text.strip("-")


def stable_rule_id(sid: str, anchor: str, n: int = 0) -> str:
    """Стабильный идентификатор строки свода из якоря источника: при
    пересборке той же клаузы идентификатор не меняется, поэтому diff
    двух пересборок читаем (решение §7.4 — пересборка вместо
    инкрементальности). `n` — разрешение коллизий, когда один пункт
    источника даёт несколько строк."""
    base = f"R-{_slug(sid)}-{_slug(anchor)}"
    return base if n == 0 else f"{base}-{n}"


def stable_question_id(part: str, key: str, n: int = 0) -> str:
    base = f"Q-{_slug(part)}-{_slug(key)}"
    return base if n == 0 else f"{base}-{n}"


def normalized_question_text(text: str) -> str:
    """Нормализация формулировки для ловли вопросов-дублей (обязательное
    свойство 4 постановки): регистр, пробелы и незначащая пунктуация не
    делают вопрос новым."""
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"[\s\.\,;:!\?—–\-«»\"']+", " ", text).strip()


def validate_schema(doc: dict) -> list[str]:
    """Структурный слой: документ против опубликованной JSON-схемы.
    Возвращает список нарушений (пустой = чисто)."""
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: str(e.path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<корень>"
        # Keep the machine path unchanged; array indices alone are poor
        # directions for a person with more than one rule or parameter.
        labels = []
        path = list(err.absolute_path)
        rules = doc.get("rules") if isinstance(doc, dict) else None
        if (len(path) >= 2 and path[0] == "rules" and isinstance(rules, list)
                and isinstance(path[1], int) and 0 <= path[1] < len(rules)):
            rule = rules[path[1]]
            if isinstance(rule, dict):
                if isinstance(rule.get("id"), str) and rule["id"].strip():
                    labels.append(f"правило {rule['id']!r}")
                params = rule.get("params")
                if (len(path) >= 4 and path[2] == "params" and isinstance(params, list)
                        and isinstance(path[3], int) and 0 <= path[3] < len(params)):
                    param = params[path[3]]
                    if (isinstance(param, dict) and isinstance(param.get("name"), str)
                            and param["name"].strip()):
                        labels.append(f"параметр {param['name']!r}")
                    if (isinstance(param, dict) and isinstance(param.get("qualifier"), str)
                            and param["qualifier"].strip()):
                        labels.append(f"уточнитель {param['qualifier']!r}")
        context = f" [{', '.join(labels)}]" if labels else ""
        problems.append(f"схема: {where}: {err.message}{context}")
    return problems


def _weighted_rules(questions: list[dict],
                    rules: list[dict]) -> set[str]:
    """Строки с ПУТЁМ к весу: SOFT без блокировки для них законен
    (ИР-2 — применение ответов обработчиком). Путь к весу — это
    (а) вес фактически известен (машинное поле weight; запасной
    текстовый путь — единая точка export_solver.soft_weight) либо
    (б) владелец применённым ответом частей 3а/3б отказался от
    оптимизации (goal_decline) — без веса законно. Членство строки
    в rule_refs ЛЮБОГО применённого вопроса части 3 путём к весу НЕ
    является (живой корень прод-демо 2026-08-30: применённый вопрос
    порога разброса назвал порог, но не вес — строка судилась
    «взвешенной» и ушла в пакет с weight=null без блокера)."""
    from cdpm import export_solver as _es
    out: set[str] = set()
    for q in questions:
        a = q.get("answer") or {}
        if q.get("part") in ("3a", "3b") \
                and a.get("status") == "применён" \
                and a.get("goal_decline"):
            out.update(r for r in q.get("rule_refs", [])
                       if isinstance(r, str))
    for r in rules:
        if not isinstance(r, dict) or r.get("kind") != "SOFT":
            continue
        rid = r.get("id")
        if isinstance(rid, str) and _es.soft_weight(r) is not None:
            out.add(rid)
    return out


def validate_model(doc: dict) -> list[str]:
    """Инвариантный слой. Возвращает ВСЕ найденные нарушения, не первое:
    аудит-циклы пилотной линии показали, что находки надо предъявлять
    пакетом.

    Каждое сообщение начинается с идентификатора объекта — по нему гейт
    и тесты адресуют находку.

    РУБЕЖ словарной дисциплины (шаг 7 каталога): без каталога шаблонов
    словари брать негде — возвращается ОДНА строка проблемы словами,
    безусловно (не зависит от meta.fit_contract: без словаря не судится
    ни один параметр). Так гейт пакета отдаёт список проблем, а не
    трассировку, и кнопка COSMOS называет остановку словами."""
    words = _catalog_bridge.problem_or_none()
    if words:
        return [words]
    problems: list[str] = []
    meta = doc.get("meta", {})
    from cdpm import answer_versions, answer_parsing
    problems.extend(answer_versions.document_problems(doc))
    problems.extend(answer_parsing.document_problems(doc))
    mode = meta.get("mode")

    # Валидатор — линия обороны от предложений модели: он обязан
    # выдерживать ЛЮБОЙ JSON и возвращать нарушения, а не падать
    # (живой цикл 2026-08-19: объект вместо строки в blockers уронил
    # проверку на нехешируемом ключе — патч не отбраковался).
    def _str_ids(values, label: str) -> list[str]:
        out = []
        for v in values:
            if isinstance(v, str):
                out.append(v)
            else:
                problems.append(f"{label}: идентификатор не строка: "
                                f"{v!r}")
        return out

    sources = doc.get("sources", [])
    sids = _str_ids((s.get("sid", "") for s in sources), "источник")
    for sid in sorted({s for s in sids if sids.count(s) > 1}):
        problems.append(f"{sid}: дубликат идентификатора источника")
    known_sids = set(sids)

    rules = doc.get("rules", [])
    rule_ids = _str_ids((r.get("id", "") for r in rules), "правило")
    for rid in sorted({r for r in rule_ids if rule_ids.count(r) > 1}):
        problems.append(f"{rid}: дубликат идентификатора правила")
    known_rules = set(rule_ids)

    questions = doc.get("questions", [])
    q_ids = _str_ids((q.get("id", "") for q in questions), "вопрос")
    for qid in sorted({q for q in q_ids if q_ids.count(q) > 1}):
        problems.append(f"{qid}: дубликат идентификатора вопроса")
    q_by_id = {q.get("id"): q for q in questions
               if isinstance(q.get("id"), str)}

    # --- правила ---
    weighted = _weighted_rules(questions, rules)
    for r in rules:
        rid = r.get("id") if isinstance(r.get("id"), str) \
            else "<нестроковый id>"
        if r.get("zone") not in ZONES:
            problems.append(f"{rid}: зона {r.get('zone')!r} вне перечня "
                            f"{ZONES}")
        if r.get("kind") not in KINDS:
            problems.append(f"{rid}: тип {r.get('kind')!r} вне перечня "
                            f"{KINDS}")
        srcs = r.get("sources", [])
        if not srcs:
            # Обязательное свойство 2 постановки: утверждение без
            # источника не существует (У-06).
            problems.append(f"{rid}: правило без трассировки к источнику")
        for ref in srcs:
            sid = ref.get("sid") if isinstance(ref, dict) else None
            if not isinstance(sid, str) or sid not in known_sids:
                problems.append(f"{rid}: трассировка на неизвестный "
                                f"источник {sid!r}")
        blockers = r.get("blockers", [])
        if not isinstance(blockers, list):
            problems.append(f"{rid}: blockers не список: {blockers!r}")
            blockers = []
        for qid in blockers:
            if not isinstance(qid, str) or qid not in q_by_id:
                problems.append(f"{rid}: блокировка ссылается на "
                                f"несуществующий вопрос {qid!r}")
        cond = r.get("conditional")
        if cond and not isinstance(cond, dict):
            problems.append(f"{rid}: conditional не объект: {cond!r}")
            cond = None
        if cond:
            cq = cond.get("question")
            if not isinstance(cq, str) or cq not in q_by_id:
                problems.append(f"{rid}: условный тип ссылается на "
                                f"несуществующий вопрос {cq!r}")
            else:
                fmt = q_by_id[cq].get("answer_format", {})
                if fmt.get("type") != "choice":
                    problems.append(
                        f"{rid}: условный тип требует вопроса-развилки, "
                        f"а формат ответа {cq} — {fmt.get('type')!r}")
                else:
                    opt_keys = [o.get("key") for o in fmt.get("options", [])]
                    branches = cond.get("branches", {})
                    if not isinstance(branches, dict):
                        problems.append(f"{rid}: branches не объект: "
                                        f"{branches!r}")
                        branches = {}
                    if sorted(map(str, branches)) \
                            != sorted(map(str, opt_keys)):
                        # Урок B7: каждая условная развилка называет
                        # ветвь результата для КАЖДОГО ответа.
                        problems.append(
                            f"{rid}: ветви условного типа {sorted(branches)} "
                            f"не совпадают с вариантами ответа {cq} "
                            f"{sorted(opt_keys)} — ветвь нужна каждому "
                            f"ответу (урок B7)")
                    for k, v in sorted(cond.get("branches", {}).items()):
                        if v not in KINDS:
                            problems.append(f"{rid}: ветвь {k!r} условного "
                                            f"типа даёт {v!r} вне перечня "
                                            f"{KINDS}")
            if cq and cq not in blockers:
                # До ответа условная строка заблокирована — как в
                # контракте компиляции свода пилотной линии.
                problems.append(f"{rid}: условный тип не заблокирован "
                                f"своим вопросом {cq!r}")
        r_params = r.get("params")
        if r_params is not None:
            if not isinstance(r_params, list):
                problems.append(f"{rid}: params не список: "
                                f"{r_params!r}")
            else:
                seen_names: set = set()
                for p in r_params:
                    for msg in validate_param(p, r.get("kind")):
                        problems.append(f"{rid}: {msg}")
                    if isinstance(p, dict) \
                            and isinstance(p.get("name"), str) \
                            and isinstance(p.get("qualifier"),
                                           (str, type(None))):
                        # Несколько записей одного имени законны при
                        # РАЗНЫХ уточнителях (норма резерва по базам —
                        # живой отказ извлечения 2026-08-22). Ключ
                        # строится только из строковых полей: мусорные
                        # типы уже красны у validate_param, а
                        # нехешируемый ключ ронял бы валидатор.
                        key = (p.get("name"), p.get("qualifier"))
                        if key in seen_names:
                            problems.append(
                                f"{rid}: параметр {key[0]!r}"
                                + (f" ({key[1]})" if key[1] else "")
                                + " дважды без различающего "
                                  "уточнителя")
                        seen_names.add(key)
        r_logic = r.get("logic")
        if r_logic is not None:
            for msg in validate_logic(r_logic, r):
                problems.append(f"{rid}: logic: {msg}")
        w = r.get("weight")
        if w is not None:
            if not isinstance(w, int) or isinstance(w, bool) or w < 0:
                problems.append(f"{rid}: weight не целое "
                                f"неотрицательное: {w!r}")
            elif r.get("kind") != "SOFT":
                problems.append(f"{rid}: weight у строки типа "
                                f"{r.get('kind')!r} — вес бывает "
                                f"только у мягких")
        if r.get("kind") == "SOFT" and not blockers \
                and rid not in weighted:
            # Целевая функция: веса — только из ответов; SOFT без
            # вопроса о весе — неразрешённый компонент без пути к
            # разрешению. Исключение (ИР-2): вопрос частей 3а/3б о
            # строке ОТВЕЧЕН и применён — веса известны из ответа.
            problems.append(f"{rid}: SOFT-правило без блокировки на вопрос "
                            f"о весе целевой функции")

    # --- вопросы ---
    seen_texts: dict[str, str] = {}
    for q in questions:
        qid = q.get("id", "<без id>")
        part = q.get("part")
        if part not in PARTS:
            problems.append(f"{qid}: часть {part!r} вне перечня {PARTS}")
        if part == "4":
            if mode != 2:
                problems.append(f"{qid}: часть 4 существует только в "
                                f"режиме 2, а meta.mode={mode!r}")
            if not q.get("third_party_refs"):
                problems.append(f"{qid}: пункт части 4 без ссылки на файл "
                                f"третьей стороны")
        refs = q.get("rule_refs", [])
        if not refs:
            # Постановка §3.2: каждый пункт анкеты несёт трассировку к
            # строкам свода И к исходным документам.
            problems.append(f"{qid}: вопрос без ссылки на строки свода")
        for rid in refs:
            if not isinstance(rid, str) or rid not in known_rules:
                problems.append(f"{qid}: ссылка на несуществующее правило "
                                f"{rid!r}")
        srefs = q.get("source_refs", [])
        if not srefs:
            problems.append(f"{qid}: вопрос без трассировки к источникам")
        for ref in srefs:
            sid = ref.get("sid") if isinstance(ref, dict) else None
            if not isinstance(sid, str) or sid not in known_sids:
                problems.append(f"{qid}: трассировка на неизвестный "
                                f"источник {sid!r}")
        if part == "1" and len(srefs) < 2:
            # Противоречие — это ДВА места источников, которые нельзя
            # удовлетворить разом; одно место противоречием не бывает.
            problems.append(f"{qid}: противоречие (часть 1) требует не "
                            f"менее двух ссылок на источники, дано "
                            f"{len(srefs)}")
        fmt = q.get("answer_format", {})
        if not isinstance(fmt, dict):
            problems.append(f"{qid}: answer_format не объект: {fmt!r}")
            fmt = {}
        ftype = fmt.get("type")
        if ftype not in ANSWER_TYPES:
            problems.append(f"{qid}: формат ответа {ftype!r} вне перечня "
                            f"{ANSWER_TYPES}")
        options = fmt.get("options", [])
        if part == "2" and ftype != "choice":
            # У-07: спорное чтение — развилка, не выбор; неоднозначность
            # без перечня чтений — это выбор, отложенный на потом.
            problems.append(f"{qid}: неоднозначность (часть 2) требует "
                            f"развилки (choice), дан {ftype!r}")
        if ftype == "choice":
            keys = [o.get("key") if isinstance(o, dict) else None
                    for o in options]
            if len(options) < 2:
                problems.append(f"{qid}: развилка из {len(options)} "
                                f"вариантов — развилке нужно не менее двух")
            if len({str(k) for k in keys}) != len(keys):
                problems.append(f"{qid}: повторяющиеся ключи вариантов "
                                f"{keys}")
        else:
            if options:
                problems.append(f"{qid}: варианты ответа заданы при "
                                f"формате {ftype!r} — варианты бывают "
                                f"только у развилки")
            if ftype == "value" and not fmt.get("note"):
                # Замкнутый «Формат ответа»: значение без единицы или
                # формы — незамкнутый вопрос (уроки анкет v4–v7).
                problems.append(f"{qid}: формат ответа value без указания "
                                f"единицы или формы (note)")
        text = q.get("text", "")
        if not isinstance(text, str):
            problems.append(f"{qid}: текст вопроса не строка: {text!r}")
            text = ""
        norm = normalized_question_text(text)
        if norm in seen_texts:
            from cdpm import question_editions
            previous = next(x for x in questions if x.get("id") == seen_texts[norm])
            same_lineage = False
            try:
                same_lineage = (question_editions.lineage_root(doc, q)
                                == question_editions.lineage_root(doc, previous))
            except question_editions.EditionRefusal:
                pass
            if not same_lineage:
                problems.append(f"{qid}: дубль вопроса {seen_texts[norm]} "
                                f"(совпадает нормализованная формулировка)")
        else:
            seen_texts[norm] = qid

    # --- ответы (интерактивный цикл, ИР-1) ---
    revision = doc.get("meta", {}).get("revision", 1)
    blockers_by_q: dict[str, list[str]] = {}
    cond_by_q: dict[str, list[str]] = {}
    for r in rules:
        raw_blockers = r.get("blockers", [])
        for qid in (raw_blockers if isinstance(raw_blockers, list)
                    else []):
            if isinstance(qid, str):
                blockers_by_q.setdefault(qid, []).append(
                    r.get("id", "?"))
        cond = r.get("conditional")
        if isinstance(cond, dict) \
                and isinstance(cond.get("question"), str):
            cond_by_q.setdefault(cond["question"],
                                 []).append(r.get("id", "?"))
    for q in questions:
        qid = q.get("id", "<без id>")
        from cdpm import question_editions
        problems += question_editions.problems(q)
        from cdpm import question_resolutions
        problems += [f"{qid}: {p}" for p in question_resolutions.problems(doc, q)]
        if "edition" in q:
            try:
                question_editions.lineage_root(doc, q)
            except question_editions.EditionRefusal as exc:
                problems.append(f"{qid}: {exc}")
        if q.get("edition_of") is not None:
            parent = next((x for x in questions
                           if x.get("id") == q["edition_of"]), None)
            if parent is None or parent is q:
                problems.append(f"{qid}: отсутствует предыдущее издание вопроса")
            elif not isinstance(q.get("edition"), dict) or not isinstance(
                    parent.get("edition"), dict):
                problems.append(f"{qid}: родословная изданий без снимков")
            elif type(q["edition"].get("revision")) is int and type(
                    parent["edition"].get("revision")) is int and (
                    q["edition"]["revision"] != parent["edition"]["revision"] + 1):
                problems.append(f"{qid}: нарушена последовательность изданий")
        if q.get("revision_of") is not None:
            # A3.4: вопрос-пересмотр указывает на существующий корень.
            root = q.get("revision_of")
            if not any(x.get("id") == root for x in questions):
                problems.append(f"{qid}: revision_of указывает на "
                                f"несуществующий вопрос {root!r}")
        ans = q.get("answer")
        if ans is None:
            continue
        status = ans.get("status")
        if status not in ANSWER_STATUSES:
            problems.append(f"{qid}: статус ответа {status!r} вне "
                            f"перечня {ANSWER_STATUSES}")
        if status == REVISION_CONFLICT_STATUS:
            if not is_revision_question(q):
                problems.append(f"{qid}: конфликт пересмотра у вопроса, "
                                "который не является пересмотром")
            conflicts = ans.get("conflicts")
            if not isinstance(conflicts, list) or not conflicts \
                    or any(not isinstance(c, str) or not c.strip() for c in conflicts):
                problems.append(f"{qid}: конфликт пересмотра без перечня причин")
        if not (ans.get("raw") or "").strip():
            problems.append(f"{qid}: ответ без дословного текста (raw)")
        if "worker_unchanged" in ans:
            from cdpm.provenance import norm_raw
            unchanged = ans["worker_unchanged"]
            if not isinstance(unchanged, dict) or not unchanged:
                problems.append(f"{qid}: worker_unchanged не непустой объект оснований")
            else:
                edition = q.get("edition")
                refs = edition.get("targets") if isinstance(edition, dict) else q.get("rule_refs")
                scope = {rid for rid in refs if isinstance(rid, str)} if isinstance(refs, list) else set()
                for rid, basis in unchanged.items():
                    if rid not in scope:
                        problems.append(f"{qid}: unchanged строки {rid!r} вне области вопроса")
                    if (not isinstance(basis, str) or not norm_raw(basis)
                            or norm_raw(basis) not in norm_raw(ans.get("raw"))):
                        problems.append(f"{qid}/{rid}: unchanged не подтверждено дословной цитатой ответа")
        if not isinstance(ans.get("revision"), int) \
                or not (1 <= ans.get("revision", 0) <= revision):
            problems.append(f"{qid}: ревизия ответа "
                            f"{ans.get('revision')!r} вне диапазона "
                            f"1..{revision} (meta.revision)")
        key = ans.get("key")
        fmt = q.get("answer_format", {})
        if fmt.get("type") == "choice":
            keys = [o.get("key") for o in fmt.get("options", [])]
            # «неприменимо» — служебный ключ «перечень ложный»
            # (запрос одной из линий 2026-08-27): строка-носитель снята
            # вне контура, ключа в перечне не бывает по построению.
            if status in ("применён", "ожидает обработчика", PARTIAL_ANSWER_STATUS):
                if key not in keys and key != "неприменимо":
                    problems.append(f"{qid}: ответ развилки с ключом "
                                    f"{key!r} вне вариантов {keys}")
        elif key is not None:
            problems.append(f"{qid}: ключ варианта у формата "
                            f"{fmt.get('type')!r} — ключи бывают "
                            f"только у развилки")
        if status == "пересмотрен":
            # A3.4: пересмотренный ответ живёт только вместе с открытым
            # (или отвеченным) вопросом-пересмотром на тот же корень;
            # строки, оставшиеся под этим вопросом после отката, обязаны
            # быть заблокированы и вопросом-пересмотром — иначе тупик.
            root = q.get("revision_of") or qid
            rb = ans.get("revised_by")
            succ = next((x for x in questions if x.get("id") == rb), None)
            if not rb or succ is None:
                problems.append(f"{qid}: ответ «пересмотрен» без вопроса-"
                                f"пересмотра (revised_by)")
            elif succ.get("revision_of") != root:
                problems.append(f"{qid}: вопрос-пересмотр {rb} указывает не "
                                f"на корень {root}")
            successors = {x.get("id") for x in questions
                          if x.get("revision_of") == root}
            for rid in blockers_by_q.get(qid, []) + cond_by_q.get(qid, []):
                rule = next((r for r in rules if r.get("id") == rid), None)
                bl = set((rule or {}).get("blockers") or [])
                if not (bl & successors):
                    problems.append(f"{qid}: строка {rid} осталась под "
                                    f"пересмотренным вопросом без блокировки "
                                    f"вопросом-пересмотра")
        if status == "применён":
            if blockers_by_q.get(qid):
                problems.append(
                    f"{qid}: ответ «применён», но строки "
                    f"{blockers_by_q[qid]} всё ещё заблокированы этим "
                    f"вопросом — применение не завершено")
            if cond_by_q.get(qid):
                problems.append(
                    f"{qid}: ответ «применён», но условный тип строк "
                    f"{cond_by_q[qid]} не разрешён этим ответом")

    # --- признак открытого пересмотра у ячеек посадки (K04, §2.4) ---
    # Признак, за которым нет неразрешённого вопроса-пересмотра
    # (вопроса нет, вопрос не пересмотр, ответ уже применён), —
    # нарушение контракта: барьер выпуска стоит по нему, и молча
    # считать его зелёным нельзя.
    problems += pending_revision_problems(doc)

    # --- целевые функции (ЦФ-1) ---
    # Секция обязана выдерживать ЛЮБОЙ JSON (урок живого цикла
    # 2026-08-19): мусорный тип — нарушение, не падение валидатора.
    objectives = doc.get("objectives", [])
    if not isinstance(objectives, list):
        problems.append(f"objectives: не список: {type(objectives).__name__}")
        objectives = []
    kept = []
    for i, o in enumerate(objectives):
        if isinstance(o, dict):
            kept.append(o)
        else:
            problems.append(f"objectives[{i}]: элемент не объект: "
                            f"{o!r}")
    objectives = kept
    o_ids = _str_ids((o.get("id", "") for o in objectives),
                     "целевая")
    for oid in sorted({o for o in o_ids if o_ids.count(o) > 1}):
        problems.append(f"{oid}: дубликат идентификатора целевой")
    for o in objectives:
        oid = o.get("id") if isinstance(o.get("id"), str) \
            else "<нестроковый id>"
        if o.get("zone") not in ZONES:
            problems.append(f"{oid}: зона {o.get('zone')!r} вне "
                            f"перечня {ZONES}")
        if o.get("direction") not in DIRECTIONS:
            problems.append(f"{oid}: направление "
                            f"{o.get('direction')!r} вне перечня "
                            f"{DIRECTIONS}")
        statement = o.get("statement")
        if not (isinstance(statement, str) and statement.strip()):
            problems.append(f"{oid}: целевая без формальной записи")
        srcs = o.get("sources")
        if not isinstance(srcs, list):
            if srcs is not None:
                problems.append(f"{oid}: sources не список: {srcs!r}")
            srcs = []
        if not srcs:
            # У-06 действует и для целевых: без источника не бывает.
            problems.append(f"{oid}: целевая без трассировки к "
                            f"источнику")
        for ref in srcs:
            sid = ref.get("sid") if isinstance(ref, dict) else None
            if not isinstance(sid, str) or sid not in known_sids:
                problems.append(f"{oid}: трассировка на неизвестный "
                                f"источник {sid!r}")
        o_blockers = o.get("blockers", [])
        if not isinstance(o_blockers, list):
            problems.append(f"{oid}: blockers не список: "
                            f"{o_blockers!r}")
            o_blockers = []
        for qid in o_blockers:
            if not isinstance(qid, str) or qid not in q_by_id:
                problems.append(f"{oid}: блокировка ссылается на "
                                f"несуществующий вопрос {qid!r}")
        metric = o.get("metric")
        if metric is not None:
            metrics = _cat("METRICS")
            if metric not in metrics:
                problems.append(f"{oid}: метрика {metric!r} вне "
                                f"словаря {tuple(metrics)}")
            else:
                zone_tasks = _ZONE_TASKS.get(o.get("zone"), ())
                if zone_tasks and not (set(zone_tasks)
                                       & set(metrics[metric]["tasks"])):
                    problems.append(
                        f"{oid}: метрика {metric!r} неприменима "
                        f"зоне {o.get('zone')!r} (задачи метрики: "
                        f"{metrics[metric]['tasks']})")

    problems += objective_direction_problems(objectives)
    problems += validate_template_fit(doc)
    # K01-барьер (контракт П0 §3.2 и §9, 12.09.2026): полноту семьи
    # считает ОДИН расчёт (`family_params.family_completeness`), и его
    # слова читают готовность, гейт производителя, рубеж выпуска и
    # валидатор. Валидатор — инвариантный слой, и он берёт из расчёта
    # ровно то, что инвариант: ДОКАЗАННУЮ несовместимость контракта
    # семьи (`proven`) — посадка сделана ВЫПУЩЕННЫМ каталогом, чья
    # запись семьи РАЗОШЛАСЬ с действующей ПО СМЫСЛУ: у общего
    # параметра сменились единица, отношение или уточнитель, либо
    # несомое посадкой имя из записи убрано (клаузы (б)/(в) §12.15).
    # Такой свод нельзя починить ответом анкеты: посаженные значения
    # означают уже не то, что означали.
    #
    # УЖЕСТОЧЕНИЕ наборов (клауза (а) §12.15 — действующая запись
    # требует больше, чем несёт посадка) нарушением тоже НЕ
    # объявляется: это обычная недостача (§12.12 п. 1), её лечит
    # ответ анкеты, и объявлять её здесь значило бы запретить сам
    # ответ — тот самый круг, ради разрыва которого правился §12.15
    # (находка К-2 неавтор-ревью 12.09.2026: посадка объявлялась
    # негодной, ответ её всё равно лечил, и она выпускалась годной).
    #
    # Ни обычную недостачу параметра, ни недоказанную совместимость
    # (отпечаток посадки не числится ни за одним выпуском) валидатор
    # нарушением НЕ объявляет — это сознательное отступление от буквы
    # §3.2 (поправка контракта §12.12): анкета — следствие расчёта
    # (§3.2), и ответ на вопрос параметра ещё не получен по замыслу;
    # объявить их здесь значило бы уронить сборку модели
    # (`extract._assemble`: «собранная модель не прошла инварианты
    # контракта») и запретить применение ответов (`answers`, `exchange`
    # судят пробный свод тем же валидатором) — то есть закрыть путь,
    # которым недостача и закрывается (регресс В-1 неавтор-ревью).
    # Держат такой свод готовность, гейт и рубеж выпуска.
    from cdpm import family_params as _fp
    try:
        found = _fp.family_barrier_problems(doc)
    except _catalog_bridge.CATALOG_ERRORS as e:   # fail-closed, не молча
        problems.append(f"каталог шаблонов недоступен: "
                        f"{_catalog_bridge.words_of(e)} — полнота семей "
                        f"не судится")
    else:
        for pr in found:
            if not pr.get("proven"):
                continue
            head = (pr["rules"] or [pr["family"]])[0]
            problems.append(f"{head}: {pr['words']}")
    return problems


#: Основание готовности «строка без шаблона» (слово владельца 09.09.2026,
#: набор Н0-C): машинное правило с посадкой категории C исполнять некому.
COVERAGE_GAP_TAG = "строки без шаблона (посадка C — пробел покрытия)"


def coverage_gap_rules(doc: dict) -> list:
    """Машинные правила (HARD/SOFT) без шаблона — посадка категории C
    (пробел покрытия). Слово владельца 09.09.2026 (набор Н0-C): пока у
    правила нет шаблона, линия данных вопрос «нет данных» ему не задаёт
    (проверять нечем — нет шаблона, а не данных), а пакет постановки не
    выдаётся, готовность ниже «в»: стоп → заявка в каталог → шаблон →
    повтор прогона."""
    out: list = []
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict) or r.get("kind") not in ("HARD", "SOFT"):
            continue
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("category") == "C":
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Пересмотр как явное состояние — барьер (K04, контракт П0 §2.4 (а)/(б),
# 12.09.2026)
# ---------------------------------------------------------------------------
#: Основание готовности «пересмотр открыт»: ячейка посадки несёт признак
#: `pending_revision` — идентификатор открытого вопроса-пересмотра. Пока
#: признак есть, прежнее значение ячейки решением авиакомпании НЕ
#: является: затронутый режим ниже «в» независимо от присутствия
#: старого значения (§2.4 (а)).
PENDING_REVISION_TAG = "ячейки под открытым пересмотром"

#: ЕДИНСТВЕННЫЙ кодовый исход открытого вопроса-пересмотра (§2.4 (б)):
#: все носители и эффекты параметра правомерно исчезли (строки не стало
#: либо она потеряла посадку) — спрашивать больше нечего. Это отдельный
#: исход, а не ответ и не применение: он называется словами в записи
#: вопроса (`closed_by`, `close_reason`).
CODE_CLOSE_OUTCOME = "вопрос больше не требуется"

#: Категории посадки, при которых строка ещё НЕСЁТ параметры семьи.
LANDED_CATEGORIES = ("A", "B")


class CodeCloseRefused(ValueError):
    """Код попытался снять открытый вопрос-пересмотр при живом
    носителе параметра — отказ словами (K04, §2.4 (б))."""


def landing_of(rule) -> dict | None:
    """Посадка строки, ПОКА она несёт параметры семьи (категории A/B).
    Нет посадки или посадка C/D — None: носителем параметра такая
    строка не является (тот же закон, что у покрытия семей)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if isinstance(fit, dict) and fit.get("category") in LANDED_CATEGORIES:
        return fit
    return None


def cell_answer_root(cell) -> str | None:
    """Корневой вопрос, чьим ответом внесено значение ячейки посадки.
    Читатель ОДИН на обе формы носителя происхождения: строка `answer`
    (форма 09.09.2026) и объект `source` (§2.3) — запись точного
    происхождения не требует второго закона чтения."""
    if not isinstance(cell, dict):
        return None
    src = cell.get("source")
    if isinstance(src, dict):
        root = src.get("root")
        if isinstance(root, str) and root:
            return root
        q = src.get("question")
        if isinstance(q, dict) and isinstance(q.get("id"), str) \
                and q.get("id"):
            return str(q["id"])
        if isinstance(q, str) and q:
            return q
    ans = cell.get("answer")
    return ans if isinstance(ans, str) and ans else None


def is_revision_question(q) -> bool:
    """Вопрос-пересмотр: несёт `revision_of` (ссылку на корень)."""
    return isinstance(q, dict) and isinstance(q.get("revision_of"), str) \
        and bool(q.get("revision_of"))


def _revision_resolved(q: dict) -> bool:
    """Пересмотр разрешён, когда его ответ ПРИМЕНЁН. «Ожидает
    обработчика», «не разобран», отсутствие ответа — не разрешение
    (fail-closed: применение не состоялось)."""
    ans = q.get("answer")
    return isinstance(ans, dict) and ans.get("status") == "применён"


def question_param_family(q) -> str | None:
    """Семья каталога, о ПАРАМЕТРЕ которой спрашивает вопрос (у
    пересмотра — по корню `revision_of`). None — вопрос не о параметре
    семьи (развилка, линия данных, история) ЛИБО каталога нет: семью
    сверять нечем, и носители судятся как прежде (fail-closed). Ключ
    читает один закон — `family_params.question_target`; ввоз поздний,
    потому что модуль полноты сам стоит на `model`."""
    if not isinstance(q, dict):
        return None
    try:
        from cdpm import family_params as _fp
        tgt = _fp.question_target(q)
    except _catalog_bridge.CATALOG_ERRORS:
        return None                   # каталога нет — семью не сверить
    return str(tgt[0]) if tgt else None


def revision_carriers(doc: dict, q: dict) -> list[str]:
    """Живые носители и эффекты параметра вопроса: (1) названные
    вопросом строки свода, которые ЕЩЁ несут свой параметр, и (2)
    строки с ячейками посадок, помеченными ответом этого вопроса или
    самим пересмотром. Носитель исчезает ПРАВОМЕРНО, когда строки нет
    в своде, она потеряла посадку (категория вне A/B) ЛИБО её посадка
    переехала на ДРУГУЮ семью — параметру прежней семьи в такой строке
    больше негде жить (закон §2.4 (б): носитель — только посадка НА
    СЕМЬЕ спрашиваемого параметра). Строка БЕЗ посадки вовсе считается
    носителем только у машинного правила (HARD/SOFT): под контрактом
    посадки его посадка обязана быть, и её отсутствие — беда свода, а
    не исчезновение носителя (fail-closed); прочим строкам параметр
    семьи не принадлежит."""
    rules = [r for r in doc.get("rules", []) or [] if isinstance(r, dict)]
    by_id = {str(r.get("id")): r for r in rules}
    root = str(q.get("revision_of") or q.get("id") or "")
    qid = str(q.get("id") or "")
    fam = question_param_family(q)
    out: set = set()
    refs = q.get("rule_refs")
    for rid in (refs if isinstance(refs, list) else []):
        rule = by_id.get(str(rid))
        if rule is None:
            continue
        if isinstance(rule.get("template_fit"), dict):
            landing = landing_of(rule)
            if landing is None:
                continue              # посадка ушла — параметра тут нет
            landed = str(landing.get("family") or "")
            # Правомерно исчезает только посадка на ИМЕНОВАННУЮ ДРУГУЮ
            # семью; посадка без семьи — брак свода (его судит
            # валидатор), и носителем она остаётся (fail-closed).
            if fam is not None and landed and landed != fam:
                continue              # перепосадка на ДРУГУЮ семью
        elif rule.get("kind") not in ("HARD", "SOFT"):
            continue                  # не машинная строка — не носитель
        out.add(str(rule.get("id")))
    for rule in rules:
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        # Эффект ответа судится по ЛЮБОЙ посадке: ячейка, пережившая
        # смену категории, остаётся живым следом прежнего значения.
        params = fit.get("params") if isinstance(fit, dict) else None
        if not isinstance(params, dict):
            continue
        for cell in params.values():
            if not isinstance(cell, dict):
                continue
            if cell.get("pending_revision") == qid \
                    or (root and cell_answer_root(cell) == root):
                out.add(str(rule.get("id")))
    return sorted(out)


def code_close_refusal(doc: dict, q: dict) -> str | None:
    """ЗАКОН K04 (§2.4 (б)) — одно место расчёта: код НЕ закрывает
    неразрешённый вопрос-пересмотр, пока жив хоть один носитель или
    эффект его параметра. Единственный кодовый исход —
    «вопрос больше не требуется» при исчезновении ВСЕХ носителей.
    Возвращает слова отказа либо None (закрывать кодом можно)."""
    if (q.get("answer") or {}).get("status") == PARTIAL_ANSWER_STATUS:
        from cdpm import answers
        pending = answers._affected_rules(doc, q)
        if pending:
            return (f"{q.get('id')}: ответ применён частично; строки без результата "
                    f"({', '.join(str(r.get('id')) for r in pending)}) остаются "
                    "заблокированными — наличие значения у другой строки "
                    "не закрывает вопрос")
    if not is_revision_question(q) or _revision_resolved(q):
        return None
    carriers = revision_carriers(doc, q)
    if not carriers:
        return None
    named = ", ".join(carriers[:6]) + (f" и ещё {len(carriers) - 6}"
                                       if len(carriers) > 6 else "")
    return (f"{q.get('id')}: пересмотр открыт, а носители параметра живы "
            f"({named}) — код снимает пересмотр только исходом "
            f"«{CODE_CLOSE_OUTCOME}», когда исчезли ВСЕ носители; "
            f"прежнее значение ответом на пересмотр не является")


def close_question_by_code(doc: dict, q: dict, *, raw: str, note: str,
                           key: str | None = None,
                           rule_resolution: dict | None = None) -> None:
    """ЕДИНСТВЕННЫЙ писатель кодового снятия вопроса: каждое место, где
    код закрывает вопрос, проходит здесь. Неразрешённый
    вопрос-пересмотр при живом носителе — отказ `CodeCloseRefused`
    (звать после `code_close_refusal`, который называет причину).
    Любое снятие помечается `closed_by=code`: это не полученный ответ
    человека. У ПЕРЕСМОТРА исход дополнительно назван `close_reason`,
    и с ячеек снимается признак `pending_revision`
    ЭТОГО вопроса — иначе осиротевший признак держал бы готовность
    вечно. У вопроса НЕ-пересмотра снимать нечего: чужого признака
    писатель не трогает (§2.4 (б))."""
    refusal = code_close_refusal(doc, q)
    if refusal:
        raise CodeCloseRefused(refusal)
    revision = int((doc.get("meta") or {}).get("revision", 1) or 1)
    if rule_resolution is not None:
        # Published modality: record each retired target separately. The
        # question closes only when every target is retired, without changing
        # a previously supplied (even unparsed or queued) airline answer.
        from cdpm import question_resolutions
        rec = {**rule_resolution, "reason": note, "revision": max(1, revision)}
        trial = {**q, "code_resolutions": [*(q.get("code_resolutions") or []), rec]}
        issues = question_resolutions.problems(doc, trial)
        if issues:
            raise CodeCloseRefused("; ".join(issues))
        q["code_resolutions"] = trial["code_resolutions"]
        return
    # Порядок ключей записи ответа сохранён прежним у всех читателей
    # (raw, status, [key], revision, note): байты модели у повторного
    # прогона не меняются от переезда записи в общий писатель.
    ans = {"raw": raw, "status": "применён"}
    if key is not None:
        ans["key"] = key
    ans["revision"] = max(1, revision)
    ans["note"] = note
    ans["closed_by"] = "code"
    if is_revision_question(q):
        ans["close_reason"] = CODE_CLOSE_OUTCOME
    q["answer"] = ans
    # K04 (§2.4 (б)): признак `pending_revision` снимает ТОЛЬКО снятие
    # САМОГО пересмотра. Законных путей снятия ровно два — ответ
    # человека и кодовый исход «вопрос больше не требуется» у
    # вопроса-ПЕРЕСМОТРА; безусловный зов заводил третий: снятие
    # ПОСТОРОННЕГО вопроса (линия данных, история) молча гасило чужой
    # признак, а с ним барьер, отказ выпуска и слова валидатора.
    # `code_close_refusal` выше тут не сторож: у не-пересмотра он
    # `None` по построению (`is_revision_question` требует
    # `revision_of`). Происхождение честное: дефект предсуществующий —
    # ветвь «посадка C» ходила через этого же писателя в ПРИНЯТОЙ
    # единице K04 (3322ca1e:cdpm/model.py:4965); композиция 12.09.2026
    # перевела на него ВТОРУЮ ветвь («правило судится решением») и
    # удвоила поверхность.
    if is_revision_question(q):
        clear_pending_revision(doc, str(q.get("id") or ""))


def mark_pending_revision(doc: dict, root: str, qid: str) -> list[str]:
    """Пометить открытым пересмотром `qid` ячейки посадок, внесённые
    ответом на корневой вопрос `root` (§2.4 (а)). Зовётся при открытии
    пересмотра: помечаются ячейки, ПЕРЕЖИВШИЕ откат (журнала нет либо
    он перекрыт позднейшими правками) — ровно те, где прежнее значение
    осталось стоять. Возвращает «<строка>.<параметр>»."""
    out: list[str] = []
    for rule in doc.get("rules", []) or []:
        params = (landing_of(rule) or {}).get("params")
        if not isinstance(params, dict):
            continue
        for name in sorted(params, key=str):
            cell = params[name]
            if isinstance(cell, dict) and cell_answer_root(cell) == root:
                cell["pending_revision"] = qid
                out.append(f"{rule.get('id')}.{name}")
    return out


def clear_pending_revision(doc: dict, qid: str) -> list[str]:
    """Снять признак `pending_revision`, указывающий на вопрос `qid`, со
    всех ячеек посадок. Законных путей снятия ровно два (§2.4 (б)):
    применение ответа на пересмотр человеком и кодовый исход
    «вопрос больше не требуется». Возвращает id затронутых строк."""
    out: list[str] = []
    for rule in doc.get("rules", []) or []:
        fit = rule.get("template_fit") if isinstance(rule, dict) else None
        params = fit.get("params") if isinstance(fit, dict) else None
        if not isinstance(params, dict):
            continue
        touched = False
        for cell in params.values():
            if isinstance(cell, dict) \
                    and cell.get("pending_revision") == qid:
                cell.pop("pending_revision", None)
                touched = True
        if touched:
            out.append(str(rule.get("id")))
    return out


def settle_applied_revisions(doc: dict) -> list[str]:
    """Человек применил ответ на пересмотр — признак `pending_revision`
    этого вопроса снимается (второй законный путь §2.4 (б)). Зовётся
    в конце обоих путей применения (книга ответов и патч обработчика):
    место снятия одно, кто бы ни применил. Возвращает id строк."""
    out: list[str] = []
    for q in doc.get("questions", []) or []:
        if is_revision_question(q) and _revision_resolved(q):
            out += clear_pending_revision(doc, str(q.get("id") or ""))
    return sorted(set(out))


def pending_revision_facts(doc: dict) -> list[dict]:
    """ОДИН расчёт факта «пересмотр открыт» (§2.4 (а)) для готовности,
    матрицы корректности, выпуска и гейта производителя: ячейки посадок
    с признаком `pending_revision`. У каждой названы строка, семья,
    параметр, вопрос и слова. `problem` непусто, когда признак указывает
    НЕ на неразрешённый вопрос-пересмотр ЭТОЙ ячейки (признак не по
    форме, вопроса нет, вопрос не пересмотр, пересмотр уже применён,
    пересматривается ЧУЖОЙ ответ): это не повод для зелёного — барьер
    стоит, а беда признака называется словами.

    Признак судится по НАЛИЧИЮ ключа, а не по годности значения: `true`,
    `5`, `0`, `""`, `null`, `{}`, `[]` — подделанный признак, то есть
    беда словами (как «вопроса нет»), а НЕ отсутствие признака. Иначе
    значение не по форме молча давало бы зелёную готовность и выпуск
    пакета (выпуск схему не проверяет), — ход, которым барьер
    обходился."""
    q_by_id = {str(q.get("id")): q for q in doc.get("questions", []) or []
               if isinstance(q, dict)}
    out: list[dict] = []
    for rule in doc.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        fit = rule.get("template_fit")
        params = fit.get("params") if isinstance(fit, dict) else None
        if not isinstance(params, dict):
            continue
        for name in sorted(params, key=str):
            cell = params[name]
            if not isinstance(cell, dict) or "pending_revision" not in cell:
                continue
            raw_qid = cell["pending_revision"]
            if not isinstance(raw_qid, str) or not raw_qid:
                qid = repr(raw_qid)
                problem = (f"признак пересмотра не по форме: ожидается "
                           f"непустая строка — идентификатор "
                           f"вопроса-пересмотра, а стоит {qid}")
            else:
                qid = raw_qid
                q = q_by_id.get(qid)
                root = cell_answer_root(cell)
                if q is None:
                    problem = f"вопроса {qid} в анкете нет"
                elif not is_revision_question(q):
                    problem = (f"вопрос {qid} не является пересмотром "
                               f"(нет ссылки на корень revision_of)")
                elif _revision_resolved(q):
                    problem = (f"ответ на пересмотр {qid} применён, а признак "
                               f"в ячейке остался")
                elif root is not None and root != q.get("revision_of"):
                    problem = (f"пересмотр {qid} пересматривает ЧУЖОЙ ответ "
                               f"{q.get('revision_of')}, а ячейку внёс ответ "
                               f"{root} — своего пересмотра у ячейки нет")
                else:
                    problem = None
            family = str((fit or {}).get("family") or "")
            words = (f"строка {rule.get('id')}: параметр «{name}» семьи "
                     f"«{family or 'без семьи'}» под открытым пересмотром "
                     f"{qid} — прежнее значение решением авиакомпании не "
                     f"является")
            if problem:
                words += f"; беда признака: {problem}"
            out.append({"rule": str(rule.get("id")), "param": str(name),
                        "family": family, "question": qid,
                        "cell": f"{rule.get('id')}.{name}",
                        "zone": rule.get("zone") or "BOTH",
                        "kind": rule.get("kind"),
                        "problem": problem, "words": words})
    return out


def pending_revision_problems(doc: dict) -> list[str]:
    """Нарушения контракта признака пересмотра для валидатора модели:
    признак в ячейке есть, а неразрешённого вопроса-пересмотра за ним
    нет. Подделанный признак — беда словами, не молчаливый зелёный."""
    return [f["words"] for f in pending_revision_facts(doc)
            if f.get("problem")]


def pending_revision_words(facts: list[dict], limit: int = 3) -> str:
    """Слова перечня для подписи готовности и отказа выпуска: семья,
    параметр, строка и вопрос — по контракту «матрица называет
    семью/параметр/вопрос словами»."""
    named = "; ".join(
        f"{f['family'] or 'без семьи'}/{f['param']} у строки {f['rule']} "
        f"— пересмотр {f['question']}" for f in facts[:limit])
    if len(facts) > limit:
        named += f"; и ещё {len(facts) - limit}"
    return named


#: Код отказа выпуска у K04-барьера — ТОТ ЖЕ `family-incomplete`, что у
#: полноты семьи K01 (контракт П0 §5 своего кода K04 не объявляет, а
#: предмет один: параметр семьи без действующего решения). Живёт в
#: `model`, чтобы запись барьера и рубеж выпуска звали одно имя.
FAMILY_INCOMPLETE_CODE = "family-incomplete"


def pending_revision_barriers(doc: dict) -> list[dict]:
    """K04-факты в ЗАПИСИ барьера K01 (`readiness_barriers` в дереве
    K01): {code, family, param, item, question, rules, zones, hard,
    words} — по одной записи на (семья, параметр, вопрос-пересмотр),
    порядок — по этой же тройке (от порядка строк свода не зависит).

    Зачем: интегратор волны 17а меняет ПОЛНЫЙ отказ выпуска K04 на путь
    частичной передачи K01 (`refuse_by_barriers`: отказ, только когда
    матрица не оставила ни одного режима) — для этого запись обязана
    читаться теми же полями. `hard` — по виду носителей (есть не-SOFT →
    затронуты все режимы задачи; только мягкие → режимы со штрафами,
    fail-closed у неизвестного вида), `zones` — зоны носителей,
    `item` — `<семья>/<параметр>` (грамматика перечня K01), строки
    названы словами. Сам отказ выпуска сейчас по-прежнему полный:
    проводку меняет интеграция, не эта часть."""
    groups: dict = {}
    for f in pending_revision_facts(doc):
        fam = f.get("family") or "без семьи"
        key = (fam, str(f.get("param")), str(f.get("question")))
        cell = groups.setdefault(key, {"rules": [], "zones": set(),
                                       "hard": False, "troubles": []})
        if f["rule"] not in cell["rules"]:
            cell["rules"].append(f["rule"])
        cell["zones"].add(str(f.get("zone") or "BOTH"))
        if f.get("kind") != "SOFT":
            cell["hard"] = True
        if f.get("problem"):
            cell["troubles"].append(f["problem"])
    out: list[dict] = []
    for (fam, param, question) in sorted(groups):
        cell = groups[(fam, param, question)]
        named = ", ".join(cell["rules"])
        words = (f"пересмотр открыт: параметр {param} семьи {fam} у "
                 f"{'строк' if len(cell['rules']) > 1 else 'строки'} "
                 f"{named} пересматривается вопросом {question} — прежнее "
                 f"значение решением авиакомпании не является")
        if cell["troubles"]:
            words += "; беда признака: " + "; ".join(cell["troubles"][:3])
        out.append({"code": FAMILY_INCOMPLETE_CODE, "family": fam,
                    "param": param, "item": f"{fam}/{param}",
                    "question": question, "rules": list(cell["rules"]),
                    "zones": sorted(cell["zones"]), "hard": cell["hard"],
                    "words": words})
    return out


def readiness(doc: dict, spec_version: str | None = None) -> dict:
    """Состояние линии правил (директива владельца 2026-08-19): (а)
    данных для пейринга/ростеринга недостаточно, (б) для пейринга
    достаточно, для ростеринга нет, (в) достаточно для обоих.

    Пробел покрытия (слово владельца 09.09.2026): машинное правило без
    шаблона (посадка C) держит задачу своей зоны ниже готовности —
    основание COVERAGE_GAP_TAG в перечне причин, слова в подписи.

    Машинный критерий: задача зоны обеспечена данными, когда в её
    зонах (своя + BOTH) нет блокированных строк свода и нет открытых
    вопросов анкеты: части 3а/3б принадлежат своей задаче целиком,
    части 1/2/4 относятся к задачам через зоны затронутых строк.
    Ответ «ожидает обработчика» готовности не даёт: строки ещё
    заблокированы — это ловится по блокировкам.

    Вопросы о целевой функции (ЦФ-1, GOAL_QUESTION_IDS) сюда не
    входят: они относятся только к режиму optimization матрицы —
    открытый вопрос о целевой не отменяет достаточности данных для
    задач выполнимости и не закрывает экспорт."""
    rules_by_id = {r.get("id"): r for r in doc.get("rules", [])}
    reasons = {"PAIRING": {}, "ROSTERING": {}}

    def hit(zone: str, tag: str) -> None:
        for task in ("PAIRING", "ROSTERING"):
            if zone in (task, "BOTH"):
                reasons[task][tag] = reasons[task].get(tag, 0) + 1

    for r in doc.get("rules", []):
        if r.get("blockers"):
            hit(r.get("zone", "BOTH"), "блокированные строки")
    for r in coverage_gap_rules(doc):
        hit(r.get("zone", "BOTH"), COVERAGE_GAP_TAG)
    # K04 (§2.4 (а)): ячейка под открытым пересмотром держит задачу
    # своей зоны ниже «в» независимо от присутствия старого значения —
    # тот же расчёт читают матрица, выпуск и гейт производителя.
    pending_facts = pending_revision_facts(doc)
    for fact in pending_facts:
        hit(fact.get("zone") or "BOTH", PENDING_REVISION_TAG)
    # Барьеры K01/K06 (контракт П0 §3.2, §3.5, §9; 12.09.2026): недостача
    # обязательного параметра использованной семьи — с вопросом или без
    # — и семья без подтверждённой поддержки исполнителя держат задачи
    # зон носителей ниже «в»; основание — самими словами проблемы.
    # Третье основание того же вида — A05 (Н-4): два носителя параметра
    # с разной правдой (`carrier_grounds`); рубеж и гейт отказывали, а
    # экран показывал «в».
    barriers = carrier_grounds(doc) + readiness_barriers(doc, spec_version)
    for pr in barriers:
        if pr.get("modes") == ["optimization"]:
            continue  # Criterion availability does not remove basic feasibility.
        for task in sorted(barrier_tasks(pr)):
            reasons[task][pr["words"]] = reasons[task].get(pr["words"], 0) + 1
    for q in open_questions(doc):
        if is_goal_family(q.get("id")) \
                or q.get("id") == CRITERION_QUESTION_ID:
            continue
        part = q.get("part")
        if part == "3a":
            hit("PAIRING", "открытые вопросы части 3а")
        elif part == "3b":
            hit("ROSTERING", "открытые вопросы части 3б")
        else:
            zones = {rules_by_id.get(rid, {}).get("zone", "BOTH")
                     for rid in q.get("rule_refs", [])} or {"BOTH"}
            for zone in zones:
                hit(zone, f"открытые вопросы части {part}")
    pairing = not reasons["PAIRING"]
    rostering = not reasons["ROSTERING"]
    if pairing and rostering:
        state, label = "в", ("Данных достаточно для пейринга и "
                             "ростеринга")
    elif pairing:
        state, label = "б", ("Данных достаточно для пейринга; для "
                             "ростеринга — недостаточно")
    elif rostering:
        # Практически редкий, но честно показываемый случай.
        state, label = "б", ("Данных достаточно для ростеринга; для "
                             "пейринга — недостаточно")
    else:
        # Рабочая ситуация начала конвейера, не ошибка (замечание
        # владельца 2026-08-21) — интерфейс показывает её жёлтым.
        state, label = "а", ("Данных для пейринга и ростеринга "
                             "недостаточно")
    if any(COVERAGE_GAP_TAG in reasons[t] for t in ("PAIRING", "ROSTERING")):
        gap_ids = summarize_items((r.get("id") for r in coverage_gap_rules(doc)), limit=4)
        label += (f"; у правил нет шаблона в каталоге ({gap_ids}) — пакет "
                  f"не выдаётся до выпуска шаблона")
    if pending_facts:
        label += (f"; открыт пересмотр значений ("
                  + pending_revision_words(pending_facts)
                  + ") — прежние значения решением не являются, пакет не "
                    "выдаётся до ответа авиакомпании")
    if barriers:
        label += "; " + "; ".join(
            f"{pr['words']} (правила {summarize_items(pr['rules'], limit=4)})"
            for pr in barriers[:3])
        if len(barriers) > 3:
            label += f" (и ещё {len(barriers) - 3})"
    return {"state": state, "label": label,
            "pairing_ready": pairing, "rostering_ready": rostering,
            "pairing_open": reasons["PAIRING"],
            "rostering_open": reasons["ROSTERING"]}


# ---------------------------------------------------------------------------
# Барьеры готовности K01 (полнота семьи) и K06 (подтверждённая поддержка)
# ---------------------------------------------------------------------------

#: Код отказа выпуска «семья без подтверждённой поддержки» (контракт П0 §5).
SUPPORT_MISSING = "support-missing"
#: Код отказа выпуска «полнота семьи» (контракт П0 §5). Слово кода живёт
#: ЗДЕСЬ, рядом с двумя соседями, а `family_params` и рубеж выпуска его
#: повторяют ссылкой: одинаковый вердикт у готовности, гейта и выпуска
#: не должен зависеть от двух одинаковых строковых литералов.
# Слияние 17а: K04 объявил этот код именем FAMILY_INCOMPLETE_CODE,
# барьеры — именем FAMILY_INCOMPLETE, и оба ЛИТЕРАЛОМ. Ровно то,
# что запрещает абзац выше. Одно имя — псевдоним другого, литерал
# один (выше, строка объявления K04); оба имени оставлены, потому
# что на каждое ссылается принятый код.
FAMILY_INCOMPLETE = FAMILY_INCOMPLETE_CODE
#: Продукт, чей выпуск судит K06-барьер (он же адресат отказа). Клетки
#: поддержки читаются по ОБОИМ файлам привязок — `catalog_bridge.
#: SUPPORT_PRODUCTS` (§12.11 п. 1): исполнимость вида даёт «есть» хотя
#: бы у одного продукта.
SUPPORT_PRODUCT = "COSMOS"
#: Задача словами человека — их называет отказ барьера («режим прогона:
#: ростеринг», §12.11 п. 3).
TASK_WORDS = {"PAIRING": "пейринг", "ROSTERING": "ростеринг"}


def _landed_carriers(doc: dict) -> dict:
    """Семья → правила-носители, порядок свода. НОСИТЕЛЬ — ЛЮБАЯ посадка
    A/B: ВИДА строки этот читатель НЕ спрашивает.

    Вид не спрашивается намеренно (правка 12.09.2026, проверка указания
    соседней сессии): закон 1 — исполнимость семьи
    (`catalog_bridge.family_has_executor`) — о виде строки тоже ничего
    не знает, и пока этот читатель по умолчанию отбирал только машинные
    HARD/SOFT, между ДВУМЯ барьерами одного свода стояла АСИММЕТРИЯ:
    правило вида DATA с посадкой A/B проходило мимо барьера поддержки
    (закон 1 истина, `support_problems` пуст, барьеров нет, готовность
    «в»), и рубеж выпуска — который `validate_model` не зовёт — такой
    пакет отдавал. Сам свод при этом незаконен (валидатор: «посадка на
    шаблон допустима только у машинных правил»), но стык двух барьеров
    обязан держать и БЕЗ валидатора — fail-closed.

    Прежнего отбора по видам больше нет вовсе: оставить его умолчанием
    значило бы оставить и ловушку — следующий читатель молча получил бы
    ту же асимметрию. Полнота параметров семьи (K01) судится по этим же
    носителям, как и прежде (её семью применяет и линия данных)."""
    out: dict = {}
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict):
            continue
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("category") in ("A", "B") \
                and fit.get("family"):
            out.setdefault(str(fit["family"]), []).append(r)
    return out


def support_problems(doc: dict, spec_version: str | None = None) -> list[dict]:
    """K06-барьер (контракт П0 §3.5, §9 и поправка 12.11 от 12.09.2026):
    каждая семья, использованная сводом ПОСАДКОЙ A/B, обязана иметь
    ПОДТВЕРЖДЁННУЮ поддержку точной записи каталога —
    `catalog_bridge.family_support` (закон 2: у каждого объявленного
    записью вида состояние «есть» хотя бы у одного продукта по
    объединению `vmp/bindings.json` и `cdpm/bindings.json` при каталоге
    того же отпечатка); иначе затронутый режим не исполним.

    ВИД строки-носителя барьер не спрашивает (правка 12.09.2026):
    носителей считает `_landed_carriers` — тот же перечень, что у
    барьера полноты семьи K01. Прежде здесь стоял отбор по машинным
    HARD/SOFT, и правило вида DATA с посадкой A/B проходило мимо
    барьера, хотя закон 1 (`family_has_executor`) вида тоже не
    спрашивает; стык двух барьеров обязан быть симметричен. Признак
    `hard` при этом честно fail-closed: у смешанного носителя (любой
    вид, кроме SOFT) он истина — держатся все режимы задачи.

    Слова проблемы — те же, что уходят в отказ выпуска (§12.11 п. 3):
    семья с АДРЕСОМ ЗАПИСИ, виды без исполнителя с состоянием у каждого
    продукта, режим прогона и переходная строгость. Запись проблемы:
    [{code, family, item, record, words, rules, zones, hard, evidence}];
    `item` — идентификатор точной записи `families/<id>` для `items`
    отказа (по нему COSMOS подаёт заявку на вид тем же путём)."""
    from cdpm import support_manifest
    if spec_version is None:
        from cdpm.export_task_package import SPEC_VERSION
        spec_version = SPEC_VERSION
    if support_manifest.required(spec_version):
        return support_manifest.readiness_problems(doc)
    # Preserve the accepted 17a B01-B03 law for archived revisions.
    out: list[dict] = []
    for fam, rules in sorted(_landed_carriers(doc).items()):
        got = _catalog_bridge.family_support(fam, product=SUPPORT_PRODUCT)
        if got.get("supported"):
            continue
        ev = got.get("evidence") or {}
        item = str(ev.get("record") or f"families/{fam}")
        zones = sorted({str(r.get("zone") or "BOTH") for r in rules})
        tasks = sorted({TASK_WORDS[t] for z in zones for t in TASK_WORDS
                        if z in (t, "BOTH")})
        out.append({
            "code": SUPPORT_MISSING, "family": fam, "item": item,
            "record": item,
            "words": (f"семья {fam} ({item}) без подтверждённой поддержки "
                      f"— {ev.get('words') or 'свидетельства нет'}; "
                      f"режим прогона: {', '.join(tasks) or 'не назван'}; "
                      f"{_catalog_bridge.SUPPORT_TRANSITIONAL}"),
            "rules": [str(r.get("id")) for r in rules],
            "zones": zones,
            "hard": any(r.get("kind") != "SOFT" for r in rules),
            "evidence": ev})
    return out


def readiness_barriers(doc: dict, spec_version: str | None = None) -> list[dict]:
    """Проблемы барьеров K01 и K06 одним перечнем — ОДИН расчёт для
    готовности (`readiness`, `readiness_matrix`), гейта производителя и
    рубежа выпуска (закон 0.4 контракта: одинаковый вердикт). Запись:
    {code, family, words, rules, zones, hard[, param, question,
    evidence]}. Без каталога шаблонов семьи судить нечем: при наличии
    посадок — одна проблема словами читателя на все зоны носителей
    (fail-closed), без посадок — пусто."""
    # Посадок нет вовсе — судить нечего и каталог трогать незачем.
    # Носителем здесь считается ЛЮБАЯ посадка A/B (в том числе строки
    # DATA/DEF) — ТОТ ЖЕ перечень, что судит поддержку исполнителя
    # (`support_problems`): один закон носителя на оба барьера, вида
    # строки не спрашивает ни один.
    carriers = _landed_carriers(doc)
    from cdpm import family_params as _fp
    try:
        out = list(_fp.family_barrier_problems(doc)) if carriers else []
        out.extend(support_problems(doc, spec_version))
    except _catalog_bridge.CATALOG_ERRORS as e:
        ids = [str(r.get("id")) for rs in carriers.values() for r in rs]
        return [{"code": _catalog_bridge.CatalogRequired.code,
                 "family": None, "rules": ids,
                 "zones": sorted({str(r.get("zone") or "BOTH")
                                  for rs in carriers.values() for r in rs}
                                 | {str(o.get("zone") or "BOTH")
                                    for o in doc.get("objectives") or []
                                    if isinstance(o, dict)}) or ["BOTH"],
                 "hard": True,
                 "words": (f"каталог шаблонов недоступен: "
                           f"{_catalog_bridge.words_of(e)} — полнота семей и "
                           f"поддержка исполнителей не судятся")}]
    return out


def barrier_tasks(problem: dict) -> set:
    """Задачи (PAIRING/ROSTERING), затронутые проблемой барьера, — по
    зонам носителей; K01 дополнительно держит всех потребителей семьи.

    Creating a parameter question must not widen a missing-value barrier:
    both use the same declared consumers. Original zones stay provenance;
    K06's own product/mode-specific support scope is not broadened here.
    """
    tasks: set = set()
    for zone in problem.get("zones") or ():
        for task in ("PAIRING", "ROSTERING"):
            if zone in (task, "BOTH"):
                tasks.add(task)
    if problem.get("code") == FAMILY_INCOMPLETE and problem.get("family"):
        tasks.update(task.upper() for task in family_consumer_tasks(problem["family"]))
    return tasks


def barrier_affects(problem: dict) -> set:
    """Пары (задача, режим), которые проблема барьера держит: жёсткие
    носители — все режимы задачи; только мягкие — режимы со штрафами
    (та же применимость, что у `export_task_package.applicability_of`)."""
    modes = problem.get("modes")
    if modes is None:
        modes = tuple(TASK_MODES) if problem.get("hard") \
            else ("optimization", "feasibility-soft")
    return {(task.lower(), mode) for task in barrier_tasks(problem)
            for mode in modes}


# Закрытый словарь машинных метрик целевых (спецификация 1.1,
# предложение сессии COSMOS 2026-08-22): таблица соответствий по id
# прогонозависима — у следующего заказчика её пришлось бы писать
# заново; закрытый перечень модель заполняет надёжно (в отличие от
# свободной грамматики). Поле metric у целевой НЕОБЯЗАТЕЛЬНО:
# целевая без метрики исполняется таблицей соответствий.
# tasks — задачи, которым метрика применима.
#
# С 09.09 (просьба 2 извещения о паритете) сам ПЕРЕЧЕНЬ — проекция
# каталога шаблонов (`METRICS` через `__getattr__` → мост
# `catalog_bridge.metrics`): имена и задачи берутся из критериев
# каталога со статусом «есть» и исполнителем `kotlin_metric`, задачи —
# по зоне записи. Ниже ОСТАЮТСЯ только слова CDPM (`title`, `note`) —
# они часть спецификации 1.1 и соли кэша извлечения, каталог их не
# знает (его заголовки другие); метрика каталога без слов CDPM получает
# заголовок каталога и заметки не имеет. Новая метрика решателя
# (класс правки К-табл) попадает в перечень сама — без правки кода.
_METRIC_WORDS: dict[str, dict] = {
    "total_pairing_cost": {
        "title": "суммарная стоимость пейрингов",
        "note": "вычислима только раздельным прогоном пейринга — на "
                "объединённой модели ростеринга стоимость цепочек не "
                "вычислима в принципе"},
    "layover_nights_total": {
        "title": "суммарное число ночей вне базы"},
    "credit_hours_spread": {
        "title": "разброс кредитных часов между членами экипажа",
        "note": "решатель вправе заявить приближение (например, "
                "минуты рейсов вместо кредитных часов) в отчёте "
                "применённого"},
    "positioning_cost": {
        "title": "стоимость перегонов (позиционирования)"},
    "uncovered_seats": {
        "title": "число непокрытых мест"},
    "preferred_days_off_violations": {
        "title": "нарушения предпочитаемых выходных"},
}

_ZONE_TASKS = {"PAIRING": ("pairing",), "ROSTERING": ("rostering",),
               "BOTH": ("pairing", "rostering")}

# Ревизия 1.3 (этап В, дизайн 17-DIZAJN-1.3-CDPM.md ревизия 2):
# машинные параметры правил — «ни одного голого числа». Единицы
# различают ПРИРОДУ величины; времяподобное канонизируется в минуты
# производителем; направления отношений кредита объявлены именем
# единицы; окно применения (window) — не граница и с границами не
# сравнивается; time_of_day требует tz из закрытого перечня
# (station-local запрещён до IANA у обеих сторон — смещение не пояс,
# DST уезжает молча).
# Ревизия 1.4: закрытый словарь уточнителей ОБЛАСТИ у параметров,
# различающих базу/вне базы одной ручкой решателя (просьба COSMOS
# 2026-08-23: свободный текст «<код станции>-base» ложился в
# ignored молча).
# Канонические значения + таблица синонимов; новые пакеты несут
# канон, старые с синонимами законны.
# Шаг 7 (стадия 1б): канон уточнителей — словарь каталога
# `area_qualifiers` (имя AREA_QUALIFIERS обслуживает `__getattr__`),
# константы кода нет. Таблица синонимов ниже — ЗАКОН CDPM: это
# нормализация языка документа, а не словарь каталога (схема §14 п. 9);
# гейт источника держит её значения внутри `area_qualifiers`.
_AREA_SYNONYMS = {
    "база": "база", "в базе": "база", "на базе": "база",
    "базовый аэропорт": "база", "дома": "база",
    "вне базы": "вне базы", "внебазовый": "вне базы",
    "вне базы приписки": "вне базы",
}
# Имена, у которых уточнитель означает ОБЛАСТЬ (отдых различён
# именами min_rest_home/min_rest_outstation — без уточнителей).
# Шаг 7: перечень вычисляет читатель каталога (параметры с уточнителем
# `area`) и отдаёт словарём `area_param_names` — имя AREA_PARAM_NAMES
# обслуживает `__getattr__` выше.


def canon_area_qualifier(q):
    """Канон уточнителя области либо None (не из словаря)."""
    if not isinstance(q, str):
        return None
    return _AREA_SYNONYMS.get(" ".join(q.strip().lower().split()))


# Живой урок 2026-08-26 (вскрытие фантомных потерь стыковок):
# параметр базовой стыковки пришёл БЕЗ машинного уточнителя —
# область была только словами следа — и потребитель по правилу «без
# уточнителя — обе ручки» ужесточил вне-базовый порог базовым
# значением. Область, однозначно названная в следе,
# восстанавливается машинно; двусмысленный след (обе области или ни
# одной) не трогается.
_AREA_OUT_RE = re.compile(r"вне[\s-]*баз|outstation", re.IGNORECASE)
_AREA_HOME_RE = re.compile(r"баз|base|дома|\bhome\b", re.IGNORECASE)


def area_from_trace(text):
    """Область из следа «как было в правиле»: «база» / «вне базы»,
    если след называет РОВНО ОДНУ область; иначе None."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    out = _AREA_OUT_RE.search(t) is not None
    home = _AREA_HOME_RE.search(_AREA_OUT_RE.sub(" ", t)) is not None
    if out and not home:
        return "вне базы"
    if home and not out:
        return "база"
    return None


# Вторая половина того же класса: извлечение предложило параметр со
# следом-числом БЕЗ единицы — канонизация честно отбросила его
# (голое число не трактуется, ревью 1.3), хотя формулировка правила
# несёт значение с единицей явно. Закрытая форма формальной записи
# стыковки разбирается детерминированно.
_FORMAL_SIT_RE = re.compile(
    r"(?:turnaround_time|sit_time)\s*\(\s*(база|вне\s+базы)\s*\)\s*"
    r"(?:≥|>=)\s*(\d+)\s*мин", re.IGNORECASE)


def restore_sit_param_from_formal(doc) -> list[str]:
    """Миграция: правило стыковки БЕЗ параметров, чьё примечание несёт
    след отброса min_turnaround (голое число без единицы), а
    формулировка — закрытую форму «turnaround_time/sit_time(область)
    ≥ N мин», получает параметр обратно детерминированно, с
    примечанием. Живой класс: вне-базовый порог стыковки терялся, и
    обе ручки потребителя брали базовое значение."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        # Имя, которое строка УЖЕ несёт любым носителем (ячейка посадки
        # тоже носитель!), вторым носителем не дописывается — иначе
        # лечение само порождает расхождение (A05, находка В-2 ревью).
        if r.get("params") or "min_turnaround" in rule_param_cells(r):
            continue
        note = r.get("note") or ""
        if "параметр отброшен: min_turnaround" not in note:
            continue
        text = " ".join(str(r.get(k) or "")
                        for k in ("formal", "statement"))
        mm = _FORMAL_SIT_RE.search(text)
        if not mm:
            continue
        area = "вне базы" if "вне" in mm.group(1).lower() else "база"
        val = int(mm.group(2))
        r["params"] = [{"name": "min_turnaround", "value": val,
                        "unit": "duration_minutes",
                        "relation": "min", "qualifier": area,
                        "as_written": f"{val} мин"}]
        extra = (f"параметр min_turnaround({area}) = {val} мин "
                 f"восстановлен из формальной записи (след '{val}' "
                 f"был без единицы)")
        r["note"] = (note + "; " + extra).strip("; ")
        notes.append(f"{r.get('id')}: {extra}")
    return notes


# Живой случай одной из линий, run-0060 (2026-08-27, ночь): ответ
# а/к различил БАЗОВЫЙ состав (действует) и РАСШИРЕННЫЙ (только
# продлённый FDP), но расширенная запись crew_size осталась без
# машинного уточнителя — производная манифеста честно видела
# конфликт двух значений и молчала, кнопка стояла. Вариант состава —
# контекстное значение, не глобальная численность места.
CREW_VARIANT_RE = re.compile(r"расширенн\w*|усиленн\w*",
                             re.IGNORECASE)


def restore_crew_variant_qualifiers(doc) -> list[str]:
    """Миграция: запись crew_size у правила, чья формулировка
    называет РАСШИРЕННЫЙ/УСИЛЕННЫЙ состав, получает уточнитель
    «расширенный» (базовая запись остаётся без уточнителя и задаёт
    численность места). Детерминирована, со следом-примечанием."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        text = (str(r.get("statement") or "") + " "
                + str(r.get("formal") or ""))
        if not CREW_VARIANT_RE.search(text):
            continue
        for p in (r.get("params") or []):
            if p.get("name") == "crew_size" \
                    and not p.get("qualifier"):
                p["qualifier"] = "расширенный"
                extra = ("параметр crew_size: уточнитель "
                         "«расширенный» восстановлен из формулировки "
                         "(вариант состава условный, не глобальная "
                         "численность места)")
                r["note"] = ((r.get("note", "") + "; " + extra)
                             .strip("; "))
                notes.append(f"{r.get('id')}: {extra}")
    return notes


# Живой класс случая (run-0060): формулировка минимального порога
# отдыха с оборотом «наибольшее из двух величин», а запись параметра
# min_rest_outstation несла relation «max» (модель спутала «максимум
# из двух величин» с отношением порога) — решатель честно отверг ВСЕ
# пороги пакета. Имя словаря само несёт природу порога (min_*/max_*);
# отношение, противоречащее природе, чинится при ПОДТВЕРЖДЕНИИ
# формулировкой; двусмысленная формулировка не трогается.
_MIN_NATURE_RE = re.compile(r"не менее|минимальн|>=|≥", re.IGNORECASE)
_MAX_NATURE_RE = re.compile(r"не более|максимальн|<=|≤", re.IGNORECASE)


def _relation_from_name_and_text(name, relation, text):
    """Правильное отношение либо None (чинить нечего/нельзя)."""
    if not isinstance(name, str):
        return None
    is_min = name.startswith("min_")
    is_max = name.startswith("max_")
    lo = _MIN_NATURE_RE.search(text) is not None
    hi = _MAX_NATURE_RE.search(text) is not None
    if is_min and relation == "max" and lo and not hi:
        return "min"
    if is_max and relation == "min" and hi and not lo:
        return "max"
    return None


def restore_param_relations(doc) -> list[str]:
    """Миграция: отношение параметра, противоречащее природе
    словарного имени (min_*/max_*), восстанавливается по
    формулировке правила — с примечанием; детерминирована."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        text = (str(r.get("statement") or "") + " "
                + str(r.get("formal") or ""))
        for p in (r.get("params") or []):
            fixed = _relation_from_name_and_text(
                p.get("name"), p.get("relation"), text)
            if fixed:
                old = p["relation"]
                p["relation"] = fixed
                extra = (f"параметр {p['name']}: отношение "
                         f"{old!r} противоречило природе имени и "
                         f"формулировке — исправлено на {fixed!r}")
                r["note"] = ((r.get("note", "") + "; " + extra)
                             .strip("; "))
                notes.append(f"{r.get('id')}: {extra}")
    return notes


# Волна условий (этап 5б, 2026-08-27; ужесточено ревью той же даты —
# 20 находок «проверено исполнением»): нормы подготовки и разбора
# полёта лежали в живых моделях прозой — без машинных параметров файл
# условий расчёта не собирается. Значение берётся ТОЛЬКО из
# формулировки; «в авиакомпании установлено N» строже нормы и
# берётся установленным. Дисциплина зазоров: между якорем величины и
# нормой запрещены слова ЧУЖИХ величин и запятая (клауза не
# пересекается) — иначе «отдых не менее 12 часов» в фразе про
# подготовку поднимался как подготовка 720 мин.
_DUR_RE = (r"(\d+:\d{2}"
           r"|\d+(?:[.,]\d+)?\s*час\w*(?:\s+\d+\s*мин\w*)?"
           r"|\d+(?:[.,]\d+)?\s*мин\w*)")
_NORM_RE = r"(?:не менее|не может быть менее|>=|≥|составляет)\s*"


def _gap(banned: str) -> str:
    """Зазор внутри одной клаузы без слов чужих величин."""
    return r"(?:(?!" + banned + r")[^.;,])*?"


# Бан «\bпри\b» и перегонного контекста (живой прогон run-0060:
# нормы подготовки и разбора вокруг перегона (DH) в документах —
# УСЛОВНЫЕ нормы перегона, не общие; клауза с «при» как общая норма
# не поднимается).
_DH_BAN = r"\bdh\b|перегон|пассажир|\bпри\b"
_BRIEF_BAN = (r"отдых|стыковк|перерыв|смен[аыу]|полет|послеполетн"
              r"|разбор|" + _DH_BAN)
_BRIEF_RE = re.compile(
    r"предполетн\w*\s+подготовк\w*" + _gap(_BRIEF_BAN)
    + _NORM_RE + _DUR_RE, re.IGNORECASE)
_BRIEF_ANCHOR_RE = re.compile(r"предполетн\w*\s+подготовк\w*",
                              re.IGNORECASE)
# «Установлено» — только клаузой самой авиакомпании, без чужих
# величин между началом клаузы и глаголом (ревью: «для послеполетного
# разбора установлено 30» уводило чужое число в подготовку).
_BRIEF_SET_RE = re.compile(
    r"(?:^|[.;])" + _gap(r"послеполетн|разбор|стыковк|отдых|смен")
    + r"в авиакомпании установлен\w*(?:\s+норм\w*)?\s*" + _DUR_RE,
    re.IGNORECASE)
_DEBRIEF_BAN = r"отдых|стыковк|перерыв|предполетн|" + _DH_BAN
_DEBRIEF_RE = re.compile(
    r"послеполетн\w*\s+(?:подготовк\w*|разбор\w*)"
    + _gap(_DEBRIEF_BAN) + _NORM_RE + _DUR_RE, re.IGNORECASE)
_DEBRIEF_NATURE_RE = re.compile(
    r"послеполетн\w*|де-?брифинг|разбор\w*\s+полет", re.IGNORECASE)
# Подтверждение числа при снятии чужой природы: норма без требования
# конкретного якоря («Де-брифинг не менее 30 минут» — якорь другой,
# природа уже установлена _DEBRIEF_NATURE_RE).
_ANY_NORM_RE = re.compile(_NORM_RE + _DUR_RE, re.IGNORECASE)
_SIT_WORD_RE = re.compile(r"стыковк", re.IGNORECASE)


def _fold(text) -> str:
    """Нормализация для поиска: ё=е (re.IGNORECASE их не равняет —
    ревью: «предполётной подготовки» не находилось вовсе)."""
    return str(text or "").replace("ё", "е").replace("Ё", "Е")


def _dur_to_min(expr: str) -> int:
    """Минуты из выражения длительности: «1:30», «1,5 часа»,
    «1 час 30 минут», «45 мин»."""
    e = expr.strip().lower()
    m = re.fullmatch(r"(\d+):(\d{2})", e)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*час\w*"
                     r"(?:\s+(\d+)\s*мин\w*)?", e)
    if m:
        return (int(round(float(m.group(1).replace(",", ".")) * 60))
                + int(m.group(2) or 0))
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*мин\w*", e)
    if m:
        return int(round(float(m.group(1).replace(",", "."))))
    raise ValueError(f"длительность не разбирается: {expr!r}")


def _single_value(rx, text):
    """Единственное значение нормы либо None. Несколько СОВПАДЕНИЙ с
    разными числами — двузначная норма (области в одной фразе):
    выбирать между ними машина не вправе (ревью, находка 9)."""
    vals = {}
    for m in rx.finditer(text):
        vals[_dur_to_min(m.group(1))] = m
    if len(vals) != 1:
        return None if not vals else -1
    return next(iter(vals))


def _foreign_norm_nearby(text, span, covered):
    """Есть ли в предложении выбранной нормы ЧУЖАЯ норма (число вне
    покрытых совпадений)? «…в базовом не менее 60, во внебазовом не
    менее 30» — вторая ветвь без якоря исчезала без следа (ревью,
    находка 9): при чужой норме рядом значение не поднимается."""
    lo = max(text.rfind(".", 0, span[0]),
             text.rfind(";", 0, span[0])) + 1
    his = [i for i in (text.find(".", span[1]),
                       text.find(";", span[1])) if i != -1]
    hi = min(his) if his else len(text)
    for m in _ANY_NORM_RE.finditer(text[lo:hi]):
        a, b = m.start() + lo, m.end() + lo
        if not any(c[0] <= a and b <= c[1] for c in covered):
            return True
    return False


def restore_briefing_debrief_params(doc) -> list[str]:
    """Миграция класса «норма прозой»: действующее правило, чья
    формулировка задаёт время предполётной подготовки либо
    послеполётного разбора («не менее N», «установлено N»), получает
    машинный параметр briefing_min/debriefing_min со значением ИЗ
    формулировки и примечанием. Детерминирована; правило, уже несущее
    параметр этого имени, не трогается."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        if r.get("kind") not in ("HARD", "DATA", "DEF"):
            continue
        text = _fold(r.get("statement"))
        # Носитель имени — И ячейка посадки (A05, находка В-2 ревью):
        # лечение не вправе дописать второго носителя тому имени,
        # которое строка уже несёт.
        have = set(rule_param_cells(r)) \
            | {p.get("name") for p in (r.get("params") or [])}
        # Конвейерный конфликт (ревью, находка 4): на правиле разбора
        # остался min_turnaround с НЕПОДТВЕРЖДЁННЫМ числом (миграция
        # природы его честно не тронула) — добавлять debriefing_min
        # рядом нельзя: правило несло бы конфликтную пару. След — и
        # никаких параметров.
        stale_nature = ("min_turnaround" in have
                        and _DEBRIEF_NATURE_RE.search(text)
                        and not _SIT_WORD_RE.search(text))
        adds: list[tuple[str, int, str]] = []
        if "briefing_min" not in have \
                and _BRIEF_ANCHOR_RE.search(text):
            briefs = list(_BRIEF_RE.finditer(text))
            sets = list(_BRIEF_SET_RE.finditer(text))
            debs = list(_DEBRIEF_RE.finditer(text))
            covered = [mm.span() for mm in briefs + sets + debs]
            chosen = sets or briefs
            vals = {_dur_to_min(mm.group(1)) for mm in chosen}
            if chosen and (
                    len(vals) > 1
                    or _foreign_norm_nearby(text, chosen[0].span(),
                                            covered)):
                extra = ("норма подготовки двузначна (несколько "
                         "разных чисел в одной фразе) — параметр не "
                         "поднят, области значений — вопрос "
                         "постановки")
                r["note"] = ((r.get("note", "") + "; " + extra)
                             .strip("; "))
                notes.append(f"{r.get('id')}: {extra}")
            elif chosen:
                adds.append(("briefing_min", vals.pop(),
                             "установленное авиакомпанией" if sets
                             else "норма формулировки"))
        if "debriefing_min" not in have and not stale_nature:
            v_deb = _single_value(_DEBRIEF_RE, text)
            if v_deb == -1:
                extra = ("норма разбора двузначна — параметр не "
                         "поднят, области значений — вопрос "
                         "постановки")
                r["note"] = ((r.get("note", "") + "; " + extra)
                             .strip("; "))
                notes.append(f"{r.get('id')}: {extra}")
            elif v_deb is not None:
                adds.append(("debriefing_min", v_deb,
                             "норма формулировки"))
        elif stale_nature and _single_value(_DEBRIEF_RE, text):
            extra = ("конфликт природы не разрешён (min_turnaround "
                     "с неподтверждённым числом на правиле разбора) "
                     "— debriefing_min не добавлен")
            r["note"] = ((r.get("note", "") + "; " + extra)
                         .strip("; "))
            notes.append(f"{r.get('id')}: {extra}")
        for name, val, why in adds:
            r.setdefault("params", []).append(
                {"name": name, "value": val,
                 "unit": "duration_minutes", "relation": "min",
                 "as_written": f"{val} мин"})
            extra = (f"параметр {name} = {val} мин восстановлен из "
                     f"формулировки ({why}; норма лежала прозой)")
            r["note"] = ((r.get("note", "") + "; " + extra)
                         .strip("; "))
            notes.append(f"{r.get('id')}: {extra}")
    return notes


def restore_debrief_param_nature(doc) -> list[str]:
    """Миграция: параметр min_turnaround (стыковка) у правила,
    формулировка которого говорит о ПОСЛЕПОЛЁТНОЙ подготовке (разборе,
    де-брифинге) и не упоминает стыковку, — чужая природа: разбор
    полёта не стыковка. Имя исправляется на debriefing_min при
    ПОДТВЕРЖДЕНИИ числом формулировки. Живой класс: кнопки №13/№14
    строили пейринг со «стыковкой 30 минут», взятой из нормы
    разбора."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        text = _fold(r.get("statement"))
        if not _DEBRIEF_NATURE_RE.search(text) \
                or _SIT_WORD_RE.search(text):
            continue
        for p in (r.get("params") or []):
            if p.get("name") != "min_turnaround":
                continue
            # Подтверждение числом: любая норма формулировки (якорь
            # уже установлен природным выражением; «Де-брифинг не
            # менее 30 минут» — законный носитель без слова
            # «послеполётный», ревью, находка 11).
            vals = {_dur_to_min(m.group(1))
                    for m in _ANY_NORM_RE.finditer(text)}
            if p.get("value") not in vals:
                continue
            p["name"] = "debriefing_min"
            extra = (f"параметр min_turnaround = {p['value']} мин "
                     f"был чужой природы (правило о послеполётном "
                     f"разборе, не о стыковке) — имя исправлено на "
                     f"debriefing_min по формулировке")
            r["note"] = ((r.get("note", "") + "; " + extra)
                         .strip("; "))
            notes.append(f"{r.get('id')}: {extra}")
    return notes


# Стыковка прозой (волна условий): безусловная норма «минимальное
# время стыковки … составляет N» / «стыковка … не менее N». Условные
# формулировки — ветвления, не полы: их машинная форма — вопрос
# постановки, миграция их не трогает. Страж условности закрыт по
# классу (ревью, находка 3): ЛЮБОЕ «при <слово>», не перечень
# конкретных условий — денилист по построению fail-open.
_SIT_PROSE_RE = re.compile(
    r"(?:(?:минимальн\w*\s+)?врем\w*\s+)?стыковк\w*"
    + _gap(r"подготовк|разбор|отдых|брифинг")
    + _NORM_RE + _DUR_RE, re.IGNORECASE)
_SIT_COND_RE = re.compile(
    r"допустим\w*\s+только|\bпри\s+[а-я]|\bесли\b|в случае",
    re.IGNORECASE)
_SIT_BASE_AREA_RE = re.compile(
    r"в базов\w*\s+аэропорт|в аэропорт\w*\s+баз", re.IGNORECASE)
_SIT_BASE_NEG_RE = re.compile(
    r"не\s+в\s+базов\w*|вне\s+базов\w*|вне\s+баз\w*", re.IGNORECASE)


def restore_sit_prose_params(doc) -> list[str]:
    """Миграция класса «норма прозой»: действующее правило с
    безусловной нормой времени стыковки получает машинный параметр
    min_turnaround со значением из формулировки (и областью «база»,
    когда формулировка называет базовый аэропорт БЕЗ отрицания).
    Нужна файлу условий и построителю: после снятия чужой природы с
    разбора полёта других жёстких носителей стыковки в живых моделях
    нет."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        if r.get("kind") not in ("HARD", "DATA", "DEF"):
            continue
        text = _fold(r.get("statement"))
        if _SIT_COND_RE.search(text):
            continue
        if any(p.get("name") == "min_turnaround"
               for p in (r.get("params") or [])):
            continue
        val = _single_value(_SIT_PROSE_RE, text)
        # Носителем имени бывает и ЯЧЕЙКА ПОСАДКИ: дописать рядом с ней
        # вторую правду прозой нельзя — так лечение сборки выпускало
        # пакет с ячейкой 45 и записью 60 (находка В-2 ревью A05).
        # Величина прозы, не совпавшая с ячейкой, не пропадает молча:
        # она остаётся примечанием (спор ячейки и прозы разбирает
        # вопрос конфликта стыковки, а не второй носитель).
        carried = rule_param_cells(r)
        if "min_turnaround" in carried:
            extra = (f"норма стыковки прозой ({val} мин) не поднята "
                     f"параметром: имя min_turnaround уже несёт ячейка "
                     f"посадки ({carried['min_turnaround']})")
            if val is not None and val != -1 \
                    and carried["min_turnaround"] != val \
                    and extra not in (r.get("note") or ""):
                r["note"] = ((r.get("note", "") + "; " + extra)
                             .strip("; "))
                notes.append(f"{r.get('id')}: {extra}")
            continue
        if val is None:
            continue
        if val == -1:
            extra = ("норма стыковки двузначна (несколько разных "
                     "чисел) — параметр не поднят, области значений "
                     "— вопрос постановки")
            r["note"] = ((r.get("note", "") + "; " + extra)
                         .strip("; "))
            notes.append(f"{r.get('id')}: {extra}")
            continue
        p = {"name": "min_turnaround", "value": val,
             "unit": "duration_minutes", "relation": "min",
             "as_written": f"{val} мин"}
        if _SIT_BASE_AREA_RE.search(text) \
                and not _SIT_BASE_NEG_RE.search(text):
            p["qualifier"] = "база"
        r.setdefault("params", []).append(p)
        extra = (f"параметр min_turnaround = {val} мин восстановлен "
                 f"из формулировки (норма стыковки лежала прозой)")
        r["note"] = (r.get("note", "") + "; " + extra).strip("; ")
        notes.append(f"{r.get('id')}: {extra}")
    return notes


_SIT_ANSWER_RE = re.compile(
    r"стыковк\w*[^.;]*?=\s*(\d+)\s*мин", re.IGNORECASE)
_HHMM_IN_TEXT_RE = None  # заполняется ниже (после _DUR_RE не нужен)


def restore_sit_from_answer(doc) -> list[str]:
    """Финал конфликта стыковки (живой ход 2026-08-27): применённый
    ответ авиакомпании на вопрос конфликта называет число («стыковка
    … = N минут»), обработчик снимает ложные носители, но машинный
    параметр внести некуда автоматически. Миграция вносит
    min_turnaround=N в ДЕЙСТВУЮЩЕЕ правило из rule_refs вопроса, чья
    формулировка ПОДТВЕРЖДАЕТ то же число (та же дисциплина «значение
    из носителя»: число есть и в ответе а/к, и в формулировке
    правила). Нет подтверждающего правила — ничего не вносится
    (пробел назовёт экспорт условий). Идемпотентна."""
    notes: list[str] = []
    for q in doc.get("questions", []):
        if q.get("id") != stable_question_id("3a",
                                             "условия-стыковка"):
            continue
        a = q.get("answer") or {}
        if a.get("status") != "применён":
            continue
        m_ans = _SIT_ANSWER_RE.search(_fold(a.get("raw")))
        if not m_ans:
            continue
        val = int(m_ans.group(1))
        rules_by = {r.get("id"): r for r in doc.get("rules", [])}
        for rid in q.get("rule_refs") or []:
            r = rules_by.get(rid)
            if r is None or r.get("kind") not in ("HARD", "DATA",
                                                  "DEF"):
                continue
            if any(p.get("name") == "min_turnaround"
                   for p in (r.get("params") or [])):
                break                      # уже внесено (идемпотентность)
            text = _fold(r.get("statement"))
            # Подтверждение числом: N минут либо 0:NN в формулировке.
            hhmm = re.search(r"(\d+):(\d{2})", text)
            plain = re.search(r"\b(\d+)\s*мин", text)
            carried = (hhmm and int(hhmm.group(1)) * 60
                       + int(hhmm.group(2)) == val) \
                or (plain and int(plain.group(1)) == val)
            if not carried or not _SIT_WORD_RE.search(text):
                continue
            r.setdefault("params", []).append(
                {"name": "min_turnaround", "value": val,
                 "unit": "duration_minutes", "relation": "min",
                 "as_written": f"{val} мин"})
            extra = (f"параметр min_turnaround = {val} мин внесён "
                     f"ответом авиакомпании на {q['id']} (ревизия "
                     f"{a.get('revision')}); число подтверждено "
                     f"формулировкой правила")
            r["note"] = ((r.get("note", "") + "; " + extra)
                         .strip("; "))
            notes.append(f"{rid}: {extra}")
            break
    return notes


def restore_area_qualifiers(doc) -> list[str]:
    """Миграция существующих моделей: у параметров области без
    машинного qualifier область восстанавливается из следа параметра
    ЛИБО формальной записи правила (если однозначна) с примечанием у
    правила. Возвращает список заметок; детерминирована — повторный
    вызов ничего не меняет. Живой класс: run-0057, «45 мин» при
    formal «turnaround_time(база) ≥ 45 мин»."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        for p in r.get("params") or []:
            if p.get("name") in _cat("AREA_PARAM_NAMES") \
                    and not p.get("qualifier"):
                area = (area_from_trace(p.get("as_written"))
                        or area_from_trace(r.get("formal"))
                        or area_from_trace(r.get("statement")))
                if area:
                    p["qualifier"] = area
                    note = (f"параметр {p['name']}: уточнитель "
                            f"области «{area}» восстановлен из следа "
                            f"(область была только словами; без него "
                            f"потребитель ужесточает обе ручки)")
                    r["note"] = ((r.get("note", "") + "; " + note)
                                 .strip("; "))
                    notes.append(f"{r.get('id')}: {note}")
    return notes


# Закрытые перечни значений параметров шаблона отдаёт КАТАЛОГ (шаг 7,
# стадия 1б: имена PARAM_UNITS, CODE_UNITS, PARAM_RELATIONS, PARAM_TZ
# обслуживает модульный `__getattr__`, констант кода нет). Смысл,
# оплаченный уроками, остаётся здесь словами:
#   * «code» и «code_list» (словарь `code_units`) — обозначения
#     справочников авиакомпании (тип судна, база, роль): шаблон
#     остаётся обезличенным, конкретика приходит из документа и обязана
#     встречаться в данных (решение владельца 03.09);
#   * «acclimatized» в поясах — консенсус CDPM/COSMOS 2026-08-28 (счёт
#     наихудших, пункты 14–15): документы задают ночное окно в поясе
#     акклиматизации; форма несёт правду документа, приёмник до
#     доработки применяет базовый пояс как приближение с явной пометкой;
#   * окно применения (window) в отношениях — не граница и с границами
#     не сравнивается.
# Закрытый словарь имён «имя → допустимая единица» (PARAMS) — тоже
# каталог: параметры записей семей и `unbound_params`, кроме имён с
# источником «словарь решателя», порядком ЧИТАТЕЛЯ; имя обслуживает
# модульный `__getattr__`, и без каталога первое обращение отказывает
# словами.

ID_IMMUTABILITY_RULE = """

ЖЁСТКОЕ ПРАВИЛО ИДЕНТИФИКАТОРОВ (нарушение = ответ отбраковывается
машинной сверкой целиком): любой идентификатор, на который ты
ссылаешься, — номер замечания, id строки свода, id вопроса анкеты,
id тупика, id находки, якорь клаузы, sid документа — КОПИРУЙ
БАЙТ-В-БАЙТ из перечней этого задания. ЗАПРЕЩЕНО выдумывать
идентификаторы, дополнять существующие суффиксами, префиксами или
смысловыми пометками (живые нарушения: «A3-005-agg»,
«A4-002-question_patch» — таких номеров не существовало), сокращать
их или сливать несколько в один. Составной или агрегатный характер
ответа выражай в его полях, а не в идентификаторе. Единственное
исключение — поля, где задание ЯВНО велит поставить новое значение
(например rule_id = "+" для новой строки)."""


# The same boundary reaches extraction, audit, worker and enforcement. Do not
# advertise the legacy input serialization as the canonical landing/2 value:
# accept_fit_candidate already upgrades it through legacy_structural_value.
_BASE_VALUE_GUIDANCE = """ФОРМАТ ЗНАЧЕНИЙ ПО БАЗАМ (параметр count с объявленным
уточнителем base_code, например reserve_crews). В template_fit.params
каждому имени соответствует ОДИН объект ячейки, не список ячеек.
Для нескольких баз его value — объект «код базы: целое неотрицательное
число», например {"AAA": 1, "BBB": 2}, condition = null; все пары код/норма
подтверждаются исходной цитатой. Объект value сам по себе не дефект.
Поддерживаемый legacy-вход без явной версии допускает строку
«AAA:1,BBB:2»; код преобразует в карту только корректную запись.
В landing/2 строка вместо карты не канон: не переписывай правильную
карту строкой и не стирай schema_version/family_contract ради неё.
Для ОДНОЙ базы законен скаляр с condition={"kind":"base_code","value":"AAA"}.
Это не condition.base_code и не способ заменить многобазовую норму
одной базой. Уточнитель каталога не разрешает список ячеек одного имени.
Сохраняй остальные поля ячейки (unit, relation, quote, source) и полный
контракт посадки. Коды, количества и цитаты не выдумывай; неправильные
значения, области действия и происхождение остаются дефектами."""


def machine_dictionary_listing() -> str:
    """ЗАКРЫТЫЙ словарь машинных имён — генератору в задание (П.1
    системного лечения изменчивости, решение владельца 2026-08-31):
    имена и единицы — из контракта модели (PARAMS), исполнимость —
    семья имени по записям КАТАЛОГА шаблонов (шаг 7: обёртки решателя
    `vmp.template_registry` больше нет; без каталога — отказ словами
    ещё на PARAMS). Правило поведения генератора — частью листинга.

    Пометка семьи текстуально прежняя: карта «имя → семья» каталога
    совпадает с прежней `PARAM_TO_FAMILY` обёртки (26 имён, сверено на
    `0.2.0-draft.1`) — задание модели этой правкой не меняется."""
    fams = _catalog_bridge.param_families()
    lines = ["ЗАКРЫТЫЙ СЛОВАРЬ ИМЁН ПАРАМЕТРОВ (имя — единица"
             " [семья-исполнитель решателя]):"]
    for name, unit in _cat("PARAMS").items():
        fam = fams.get(name)
        mark = f" [исполнитель: {fam}]" if fam else ""
        lines.append(f"- {name} ({unit}){mark}")
    lines += [
        "",
        "ВИДЫ ПРАВИЛ (закрытый перечень, определения):",
        "- HARD — жёсткое ограничение планирования (обязано "
        "исполняться счётом);",
        "- SOFT — мягкое предпочтение со штрафом-весом (вес — "
        "только из ответа авиакомпании);",
        "- DEF — определение/машинная константа мира (шкалы, "
        "классификации); КОНВЕНЦИЯ ВМП: перечень с кодами или "
        "определение термина без слов обязательности — DEF общего "
        "раздела, не HARD (ограничение домена возникает только правилом, "
        "которое на перечень ссылается);",
        "- DATA — СПРАВКА ДАННЫХ: перечни, реестры, описания — "
        "счётом не исполняется, параметров не требует;",
        "- CASE — условный вид: каким правилом читать, решает "
        "ответ авиакомпании (вопрос-развилка);",
        "- OUT — снятое/вне контура (ссылки на отсутствующие "
        "документы и т.п.).",
        "",
        "ПРАВИЛО ИМЁН: имя параметра — ТОЛЬКО из словаря выше и "
        "ТОЛЬКО при точном смысловом соответствии. Если норма "
        "машинная, но НИ ОДНО имя не подходит — имя НЕ выдумывай: "
        "оставь числа в формулировке (statement) и добавь в note "
        "пометку «нужен новый шаблон: <какой механики не хватает>» "
        "— конвейер шаблонов родит исполнителя штатно.",
        "",
        "ПРАВИЛО НЕЗАНИЖЕНИЯ ВИДА (живой регресс run-0026/0027: "
        "предел смены записан SOFT, закрытый перечень баз — DATA): "
        "обязательная норма, предел или закрытый перечень из "
        "документа НЕ занижается — это HARD (или CASE при честной "
        "развилке чтения). SOFT — только когда ТЕКСТ документа "
        "называет штраф, предпочтение или «нежелательно»; DATA — "
        "только для справок, не несущих нормы планирования. "
        "Занижение вида без дословного текстового основания — "
        "дефект формализации.",
        "",
        _BASE_VALUE_GUIDANCE,
    ]
    return "\n".join(lines)


# ── Пакет «Посадка правил на шаблоны» (контракт согласован
# CDPM+COSMOS 2026-09-01; постановка PAKET-POSADKA-SHABLONOV-CLD).
# Категории A/B/C/D с проверяемыми критериями; валидатор — линия
# обороны от предложений модели (выдерживает любой JSON).

# Шаг 7 (стадия 1б): перечень категорий — словарь каталога
# `fit_categories` (имя FIT_CATEGORIES обслуживает `__getattr__`),
# константы кода нет; смысл категорий и правила их проверки остаются
# законом кода CDPM.


def template_families() -> dict:
    """Семьи базы шаблонов → перечень имён параметров словаря.
    С шага 7 — ТОЛЬКО по записям каталога шаблонов (один экземпляр на
    процесс): запаса реестра решателя и режима среза базы нет, без
    каталога — отказ словами. Пустая карта каталога остаётся законом
    fail-closed у `validate_template_fit`."""
    return _catalog_bridge.families()


def family_conditions(fam: str | None = None) -> dict:
    """Условия исключения семьи (каталог шаблонов, шаг 2, 07.09.2026):
    имя → {family, unit, relation, note}. В PARAMS условия не входят:
    имя условия допускается в template_fit.params только у семьи-
    владельца; единица — из перечня единиц условий каталога (code_list
    — перечень кодов канонической строкой, flag — булево true/false),
    отношение — из записи условия (in, yes) — его читает потребитель
    по имени. Без каталога — отказ словами (`CatalogRequired`), а не
    пустая карта: пустая карта соврала бы «условий у семьи нет»."""
    return _catalog_bridge.conditions(fam)


def fit_param_unit(fam, name):
    """Единица имени в params посадки: параметр словаря — из PARAMS,
    условие семьи-владельца — из записи условия каталога; иначе None."""
    unit = _cat("PARAMS").get(str(name))
    if unit:
        return unit
    spec = family_conditions(fam).get(str(name)) if fam else None
    return spec.get("unit") if isinstance(spec, dict) else None


def condition_value_problem(name, unit, value) -> str | None:
    """Нарушение значения условия по его единице: code_list — непустой
    канонический перечень кодов (строка, как у параметров с этой
    единицей), flag — булево true/false; иная единица — вне перечня."""
    if unit == "code_list":
        if not isinstance(value, str) or not value.strip():
            return (f"условие {name!r}: value перечня кодов — непустая "
                    f"строка, не {value!r}")
        if canon_code_list(value) != value:
            return (f"условие {name!r}: перечень кодов не канонизирован "
                    f"(ожидалось {canon_code_list(value)!r})")
        return None
    if unit == "flag":
        if not isinstance(value, bool):
            return (f"условие {name!r}: value флага — true/false, "
                    f"не {value!r}")
        return None
    return (f"условие {name!r}: единица {unit!r} вне перечня единиц "
            f"условий каталога")


def canon_fit_conditions(fit: dict) -> dict:
    """Канон значений условий в params посадки (перечень кодов — как у
    параметров: без повторов, отсортирован): порядок обозначений в
    ответе модели — не повод ронять посадку в C."""
    if not isinstance(fit, dict) or not isinstance(fit.get("params"), dict):
        return fit
    conds = family_conditions(fit.get("family")) if fit.get("family") else {}
    for name, cell in fit["params"].items():
        spec = conds.get(str(name))
        if not spec or spec.get("unit") != "code_list":
            continue
        if isinstance(cell, dict) and isinstance(cell.get("value"), str) \
                and cell["value"].strip():
            cell["value"] = canon_code_list(cell["value"])
    return fit


def registry_fingerprint() -> str:
    """Отпечаток базы шаблонов на момент посадки (`registry_rev`):
    «по какому каталогу посажено». C-посадки машинно пересматриваются
    при выходе нового шаблона — сравнением этого отпечатка с текущим.

    Шаг 7 переезда: ОДНА ветка — отпечаток каталога (64 hex) из одной
    отметки моста; тот же экземпляр, что у производителя пакета, значит
    у строк и у `manifest.catalog` отпечаток один по построению. Без
    каталога — отказ словами (`CatalogRequired`), а не выдуманная
    константа; режим среза базы мостом не читается (§3 проекта).

    Прежние ветки — реестр решателя (`registry_rev` обёртки) и запасная
    формула 12 знаков с константой-заглушкой — сняты: обёртку
    `vmp.template_registry` CDPM не читает вовсе, а отметка моста либо
    есть, либо отказывает словами. Половинчатого отпечатка не бывает."""
    return str(_catalog_bridge.stamp()["fingerprint"])


def validate_template_fit(doc: dict) -> list[str]:
    """Словарная и категорийная дисциплина посадок. Цитаты против
    клауз заморозки судит validate_fit_quotes — там, где тексты клауз
    есть (извлечение, цикл)."""
    problems: list[str] = []
    fams = template_families()
    traits = template_family_traits()
    cats = _cat("FIT_CATEGORIES")
    data_sids = data_source_sids(doc)
    q_ids = {q.get("id") for q in doc.get("questions", [])}
    fit_required = bool((doc.get("meta") or {}).get("fit_contract"))
    if fit_required and not fams and any(
            isinstance(r, dict) and r.get("kind") in ("HARD", "SOFT")
            for r in doc.get("rules", [])):
        # П3/П5 (2026-09-02): без базы шаблонов посадки судить нечем —
        # честный стоп, а не молчание (fail-closed при контракте).
        problems.append("база шаблонов недоступна (template_families "
                        "пуст) — контракт посадки не судится")
    for r in doc.get("rules", []):
        rid = r.get("id") if isinstance(r.get("id"), str) \
            else "<нестроковый id>"
        # Детерминизация ярлыков (2026-09-02): вид DATA — только у
        # правил из файлов данных; справка документа правил — DEF.
        if fit_required and r.get("kind") == "DATA":
            sids = {s.get("sid") for s in (r.get("sources") or [])
                    if isinstance(s, dict)}
            if not sids or not sids <= data_sids:
                problems.append(f"{rid}: вид DATA у правила из "
                                f"документа правил (не файла данных) "
                                f"— ярлык ставит код (DEF)")
        fit = r.get("template_fit")
        if fit is None:
            if fit_required and r.get("kind") in ("HARD", "SOFT"):
                problems.append(f"{rid}: контракт посадки действует "
                                f"(meta.fit_contract), а машинное "
                                f"правило без template_fit")
            continue
        if not isinstance(fit, dict):
            problems.append(f"{rid}: template_fit не объект: {fit!r}")
            continue
        if "schema_version" in fit:
            from template_catalog import landing
            problems.extend(p["words"] for p in landing.problems(r, _catalog_bridge.catalog()))
        if r.get("kind") not in ("HARD", "SOFT"):
            problems.append(f"{rid}: посадка на шаблон допустима "
                            f"только у машинных правил (HARD/SOFT), "
                            f"вид {r.get('kind')!r}")
        cat = fit.get("category")
        if cat not in cats:
            # Ревью пакета: раннее continue давало ремонтному циклу
            # по ОДНОМУ дефекту за круг — валидатор-аккумулятор
            # обязан выдавать полный список; словарная часть ниже
            # судится и при кривой категории.
            problems.append(f"{rid}: категория посадки {cat!r} вне "
                            f"{cats}")
        if not str(fit.get("registry_rev") or "").strip():
            problems.append(f"{rid}: посадка без registry_rev")
        if "family_contract" in fit:
            from cdpm import family_contract
            reference = family_contract.read(fit)
            problems.extend(f"{rid}: {words}" for words in reference["issues"])
        need = {"A": ("family",),
                "B": ("family", "assumption"),
                "C": ("gap",), "D": ("candidates",)}.get(cat, ())
        deny = {"A": ("assumption", "gap", "candidates",
                      "pending_question"),
                "B": ("gap", "candidates"),
                "C": ("family", "params", "assumption", "quote",
                      "candidates", "pending_question"),
                "D": ("family", "params", "assumption", "quote",
                      "gap", "pending_question")}.get(cat, ())
        for f in need:
            if not fit.get(f):
                problems.append(f"{rid}: посадка {cat} без поля "
                                f"{f!r}")
        for f in deny:
            if fit.get(f) is not None:
                problems.append(f"{rid}: поле {f!r} запрещено при "
                                f"категории {cat}")
        fam = fit.get("family")
        if fam is not None and fams and fam not in fams:
            problems.append(f"{rid}: семья {fam!r} вне базы шаблонов "
                            f"({sorted(fams)})")
        # Детерминизация ярлыков: у посадки A/B зона и вид — атрибуты
        # семьи (derive_rule_traits); расхождение = путь, забывший
        # вызвать код ярлыков, а не мнение модели.
        if fit_required and cat in ("A", "B") and fam in traits:
            tr = traits[fam]
            if tr.get("zone") and r.get("zone") != tr["zone"]:
                problems.append(f"{rid}: зона {r.get('zone')!r} "
                                f"расходится с семьёй {fam!r} "
                                f"({tr['zone']}) — ярлык ставит код")
            if tr.get("kind") and r.get("kind") in ("HARD", "SOFT"):
                if _conditional_open(r):
                    if _fork_is_modality_only(r):
                        problems.append(
                            f"{rid}: развилка модальности у семьи "
                            f"{fam!r} с фиксированным видом "
                            f"({tr['kind']}) — снимает код")
                elif r.get("kind") != tr["kind"]:
                    problems.append(f"{rid}: вид {r.get('kind')!r} "
                                    f"расходится с семьёй {fam!r} "
                                    f"({tr['kind']}) — ярлык ставит код")
        # П4 (2026-09-02): у мягкой строки с посадкой A/B семья для
        # решателя (export_solver.family) — та же, что у посадки; две
        # правды о семье — нарушение «ярлык ставит код».
        if fit_required and cat in ("A", "B") and fam \
                and r.get("kind") == "SOFT":
            es = r.get("export_solver")
            es_fam = es.get("family") if isinstance(es, dict) else None
            if es_fam is not None and es_fam != fam:
                problems.append(f"{rid}: семья решателя {es_fam!r} "
                                f"расходится с посадкой {fam!r} — "
                                f"ярлык ставит код")
        # К-1: семья со словарными параметрами обязана дать params;
        # беспараметрная — params либо цитату-носитель quote.
        if cat in ("A", "B"):
            has_params = bool(fit.get("params"))
            has_quote = bool(str(fit.get("quote") or "").strip())
            if fams and fam in fams and fams[fam]:
                # An explicitly empty modern container is a K01 shortage,
                # not malformed input. Completeness still blocks every
                # affected consumer; an unrelated task may be transferred.
                typed_container = (fit.get("schema_version") == "landing/2"
                                   and isinstance(fit.get("params"), dict))
                if not has_params and not typed_container:
                    problems.append(
                        f"{rid}: семья {fam!r} со словарными "
                        f"параметрами — params обязателен")
            elif not has_params and not has_quote:
                problems.append(
                    f"{rid}: беспараметрная семья {fam!r} — "
                    f"обязателен params либо quote")
        params = fit.get("params")
        if params is not None:
            if (not isinstance(params, dict)
                    or (not params and fit.get("schema_version") != "landing/2")):
                problems.append(f"{rid}: params посадки не непустой "
                                f"объект")
            else:
                conds_all = family_conditions()
                names = _cat("PARAMS")
                for name, cell in params.items():
                    cond = conds_all.get(str(name)) \
                        if name not in names else None
                    if name not in names and cond is None:
                        problems.append(
                            f"{rid}: параметр посадки {name!r} вне "
                            f"словаря PARAMS и условий каталога")
                    elif cond is not None:
                        # Условие исключения (каталог, шаг 2): только у
                        # семьи-владельца, значение — по единице условия.
                        if cond.get("family") != fam:
                            problems.append(
                                f"{rid}: условие {name!r} принадлежит "
                                f"семье {cond.get('family')!r}, не {fam!r}")
                        elif fit.get("schema_version") != "landing/2":
                            bad = condition_value_problem(
                                name, cond.get("unit"),
                                cell.get("value") if isinstance(cell, dict)
                                else None)
                            if bad:
                                problems.append(f"{rid}: {bad}")
                    elif fams and fam in fams \
                            and name not in fams[fam]:
                        # П3 (ревью 2026-09-02): имя обязано быть
                        # параметром СВОЕЙ семьи; чужое или ничьё —
                        # нарушение (прежде ничьё проходило A).
                        owner_fam = next((f for f, ps in fams.items()
                                          if name in ps), None)
                        problems.append(
                            f"{rid}: параметр {name!r} принадлежит "
                            + (f"другой семье {owner_fam!r}" if owner_fam
                               else "ни одной семье базы")
                            + f", не {fam!r}")
                    if not isinstance(cell, dict) \
                            or not str(cell.get("quote")
                                       or "").strip() \
                            or cell.get("value") in (None, ""):
                        problems.append(
                            f"{rid}: запись параметра {name!r} без "
                            f"value/quote")
                    if isinstance(cell, dict):
                        from cdpm import family_params as _fp
                        spec = _fp._param_spec(str(fam or ""), name) or {}
                        base_qualified = spec.get("unit") == "count" \
                            and spec.get("qualifier") == "base_code"
                        table = _fp.base_map_value(cell.get("value"))
                        if isinstance(cell.get("value"), dict) or (base_qualified and table is not None):
                            if not base_qualified or table is None or any(v < 0 for v in table.values()):
                                problems.append(f"{rid}: параметр {name!r} — карта значений допустима "
                                                "только как уникальные нормы count/base_code своей семьи")
                            else:
                                problems.extend(f"{rid}: параметр {name!r}: {p}" for p in
                                                _fp.base_map_quote_problems(table, cell.get("quote")))
                    # Полнота параметров семьи (09.09.2026): запись с
                    # признаком answer внесена ответом анкеты — вопрос
                    # обязан быть в анкете и быть применённым (после
                    # пересмотра журнал снимает запись; уцелевшая при
                    # перекрытом пересмотре — законна до замены).
                    if isinstance(cell, dict) \
                            and cell.get("answer") is not None:
                        aq = next((q for q in doc.get("questions", [])
                                   if isinstance(q, dict)
                                   and q.get("id") == cell["answer"]),
                                  None)
                        st = ((aq or {}).get("answer") or {}).get("status")
                        if aq is None:
                            problems.append(
                                f"{rid}: параметр {name!r} помечен "
                                f"ответом {cell['answer']!r}, которого "
                                f"нет в анкете")
                        elif st not in ("применён", "пересмотрен", PARTIAL_ANSWER_STATUS):
                            problems.append(
                                f"{rid}: параметр {name!r} помечен "
                                f"ответом {cell['answer']!r} со статусом "
                                f"{st!r}, а не применённым")
        cands = fit.get("candidates")
        if cands is not None:
            if not isinstance(cands, list) or len(cands) < 2:
                problems.append(f"{rid}: candidates посадки D — "
                                f"минимум две семьи")
            else:
                for c in cands:
                    if fams and c not in fams:
                        problems.append(f"{rid}: кандидат {c!r} вне "
                                        f"базы шаблонов")
        pq = fit.get("pending_question")
        if pq is not None and pq not in q_ids:
            problems.append(f"{rid}: pending_question {pq!r} не "
                            f"существует в анкете")
    # Семья решателя у ЛЮБОЙ мягкой строки (ревью r55: схема больше не
    # перечисляет семьи — судит код по срезу базы и её атрибутам):
    # семья вне базы или не мягкая по атрибутам — нарушение; посадка
    # calendar_windows без распознанного окна дней — нарушение
    # (форма окна решателя не соберётся молча).
    for r in doc.get("rules", []):
        if not isinstance(r, dict) or r.get("kind") != "SOFT":
            continue
        rid = r.get("id") if isinstance(r.get("id"), str) \
            else "<нестроковый id>"
        es = r.get("export_solver")
        es_fam = es.get("family") if isinstance(es, dict) else None
        if es_fam is not None:
            if fams and es_fam not in fams:
                problems.append(f"{rid}: семья решателя {es_fam!r} вне "
                                f"базы шаблонов ({sorted(fams)})")
            elif (traits.get(es_fam) or {}).get("kind") == "HARD":
                problems.append(f"{rid}: семья решателя {es_fam!r} — "
                                f"жёсткая по атрибутам базы, у мягкой "
                                f"строки быть не может")
        fit = r.get("template_fit")
        if fit_required and isinstance(fit, dict) \
                and fit.get("category") in ("A", "B") \
                and fit.get("family") == "calendar_windows":
            # Окно судится по тому, что реально соберёт код: запись
            # export_solver, а без неё — вывод из посадки (свод до
            # assign_soft_families, эталоны). Находка COSMOS 02.09:
            # эталон H0 без export_solver краснел, хотя формулировка
            # несла «пятница или суббота».
            window = es if (isinstance(es, dict) and es.get("days")) \
                else (soft_family_from_fit(r) or {})
            if not window.get("days"):
                problems.append(f"{rid}: посадка calendar_windows без "
                                f"окна дней недели — ни export_solver."
                                f"days, ни формулировка/цитата не дают "
                                f"дней словаря {sorted(SOFT_FAMILY_DAYS)}"
                                f" — форма окна не соберётся")
    # Блок criterion целевой — только исполнимый (ревью r55).
    for o in doc.get("objectives", []) or []:
        if not isinstance(o, dict) or o.get("criterion") is None:
            continue
        if not criterion_block_complete(o.get("criterion")):
            problems.append(f"{o.get('id')}: блок criterion неполон "
                            f"для вида {(o.get('criterion') or {}).get('kind')!r}"
                            f" — ставка/ставки рангов, версия и дата "
                            f"обязательны")
    return problems


def template_fit_listing() -> str:
    """Задание посадки — генератору: перечень семей базы шаблонов с
    исполнимыми параметрами + категории с проверяемыми критериями.
    База недоступна — секция честно говорит об этом (посадка тогда
    даёт только C)."""
    lines = ["ПЕРЕЧЕНЬ ШАБЛОНОВ БАЗЫ (семья — механика; параметры "
             "семьи; вид/зона семьи — их правилу проставляет код):"]
    # Перечень — из каталога (template_families): модель и валидатор
    # судят по ОДНОМУ набору семей. Шаг 7: режима среза базы больше нет
    # — область применения закрепляется версией каталога.
    fams = template_families()
    traits = template_family_traits()
    # Заголовки — титулы ЗАПИСЕЙ каталога (шаг 7): прежде их давала
    # обёртка решателя (RULE_MECHANIC_TYPES[...]["title"]), которая сама
    # брала их из записи, — текст задания этой правкой не меняется.
    titles = _catalog_bridge.family_titles()
    if fams:
        for fam in sorted(fams):
            ps = ", ".join(sorted(fams[fam])) or "—"
            tr = traits.get(fam) or {}
            kz = ""
            if tr.get("kind") or tr.get("zone"):
                kz = (f"; вид/зона: {tr.get('kind') or '?'}/"
                      f"{tr.get('zone') or '?'}")
            conds = family_conditions(fam)
            cz = ""
            if conds:
                cz = "; условия: " + ", ".join(
                    f"{n} ({'перечень кодов' if c.get('unit') == 'code_list' else 'да/нет'}, {c.get('relation')})"
                    for n, c in sorted(conds.items()))
            lines.append(f"- {fam} — {titles.get(fam) or '—'}; "
                         f"параметры: {ps}{kz}{cz}")
    else:
        lines.append("(база шаблонов недоступна — любая посадка "
                     "машинного правила даёт категорию C)")
    lines += [
        "",
        "ПОСАДКА НА ШАБЛОН (template_fit) — ОБЯЗАТЕЛЬНА для каждого "
        "правила вида HARD, SOFT и УСЛОВНЫЙ; для DEF/DATA/CASE/OUT — "
        "null. Категории:",
        "- A — точная посадка: семья названа, КАЖДЫЙ параметр семьи, "
        "заданный текстом, извлечён с value и quote — ДОСЛОВНОЙ "
        "цитатой из текста клаузы (не пересказом); у семьи БЕЗ "
        "словарных параметров (в перечне «параметры: —») params не "
        "нужны — вместо них ОБЯЗАТЕЛЬНО поле quote посадки: дословная "
        "цитата-носитель нормы из клаузы (штраф за ночёвку, окно "
        "дней недели, равномерность — это A, не C);",
        "- B — посадка с РОВНО ОДНИМ явным интерпретирующим "
        "допущением (поле assumption); параметры — как в A;",
        "- C — правило машинное, но НИ ОДНА семья не покрывает его "
        "механику: поле gap = какой механики не хватает; семью и "
        "параметры НЕ называй;",
        "- D — правило ложится на ДВЕ и более семей и выбор не "
        "очевиден: поле candidates = перечень этих семей (минимум "
        "две); семью не выбирай — решит арбитраж.",
        "Условия исключения семьи (в перечне «условия:») пишутся в "
        "params ТОЙ ЖЕ семьи записью {value, quote}, как параметры: "
        "«перечень кодов» — обозначения из клаузы через запятую "
        "(например, коды баз), «да/нет» — true или false; условие "
        "чужой семьи и условие без цитаты — нарушение.",
        "Правила посадки: семья — ТОЛЬКО из перечня выше; имена "
        "параметров — ТОЛЬКО из закрытого словаря имён и ТОЛЬКО "
        "своей семьи; quote копируй из клаузы байт-в-байт; "
        "натягивать правило на чужой шаблон запрещено — честная C "
        "лучше ложной A; confidence — целое 0-100, твоя уверенность "
        "в категории (телеметрия, на решения не влияет). Несколько "
        "норм одного параметра по базам — одна ячейка с картой "
        "код/норма в value (полный формат значений приведён ниже). "
        "Жёсткий предел и "
        "его штрафуемое продление — ДВЕ посадки: предел — семья "
        "пределов (базовое значение), продление — семья продления "
        "(порог + потолок).",
        "Ярлыки ставит код, не ты: у правила с посадкой A/B вид "
        "(HARD/SOFT) и зона берутся из атрибутов семьи перечня — твои "
        "zone/kind у таких правил справочны; вид DATA допустим ТОЛЬКО "
        "правилам из файлов данных — справка документа правил "
        "(определение, перечень) это DEF; второй кандидат той же "
        "клаузы с той же семьёй и теми же параметрами — дубль, код его "
        "отбросит (одна клауза — одно правило на семью).",
        "",
        _BASE_VALUE_GUIDANCE,
    ]
    return "\n".join(lines)


_FIT_KEYS = ("category", "family", "params", "assumption", "quote",
             "gap", "candidates")


def _fit_value_number(value):
    """Значение параметра посадки числом (схема допускает и строку —
    живой брак ревью: '720' против 12:00 бракавался строгим int)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def _fit_shape_reason(fit: dict, clause_text: str) -> str | None:
    """Причина брака посадки КАНДИДАТА либо None. Категорийные поля,
    закрытый перечень ключей (ревью пакета: посторонний ключ, пройдя
    приём, детонировал бы схему на финальном гейте извлечения и губил
    прогон), словарь семей/имён, дословность цитат против клаузы.
    pending_question кандидату запрещён ПРИ ЛЮБОЙ категории — его
    ставит только код (строитель B-вопросов)."""
    typed = fit.get("schema_version") == "landing/2"
    keys = _FIT_KEYS + (("schema_version", "family_contract", "registry_rev") if typed else ())
    extra = [k for k in fit if k not in keys]
    if extra:
        return f"посторонние поля посадки: {sorted(extra)}"
    cat = fit.get("category")
    cats = _cat("FIT_CATEGORIES")
    if cat not in cats:
        return f"категория {cat!r} вне {cats}"
    if typed:
        rev = fit.get("registry_rev")
        if not isinstance(rev, str) or not rev.strip():
            return "registry_rev — непустая строка отпечатка каталога"
        # Unresolved candidates have no selected family. Check their header
        # here too: the pinned-cell validator below only handles A/B.
        if cat in ("C", "D") and "family_contract" in fit:
            return f"поле 'family_contract' запрещено при категории {cat}"
    need = {"A": ("family",),
            "B": ("family", "assumption"),
            "C": ("gap",), "D": ("candidates",)}[cat]
    deny = {"A": ("assumption", "gap", "candidates"),
            "B": ("gap", "candidates"),
            "C": ("family", "params", "assumption", "quote",
                  "candidates"),
            "D": ("family", "params", "assumption", "quote",
                  "gap")}[cat]
    for f in need:
        if not fit.get(f):
            return f"категория {cat} без поля {f!r}"
    for f in deny:
        if fit.get(f) is not None:
            return f"поле {f!r} запрещено при категории {cat}"
    fams = template_families()
    fam = fit.get("family")
    if fam is not None and fams and fam not in fams:
        return f"семья {fam!r} вне базы шаблонов"
    # К-1 (эталон нано): семья СО словарными параметрами обязана
    # дать params; беспараметрная семья — params либо цитату-носитель.
    if cat in ("A", "B"):
        has_params = bool(fit.get("params"))
        has_quote = bool(str(fit.get("quote") or "").strip())
        if fams and fam in fams and fams[fam]:
            # Same shape/completeness distinction as validate_template_fit.
            if not has_params and not (typed and isinstance(fit.get("params"), dict)):
                return (f"семья {fam!r} со словарными параметрами — "
                        f"params обязателен")
        elif not has_params and not has_quote:
            return (f"беспараметрная семья {fam!r} — обязателен "
                    f"params либо quote (цитата-носитель)")
        top_q = str(fit.get("quote") or "").strip()
        if top_q:
            norm_clause0 = _fit_norm_text(clause_text)
            if norm_clause0 and _fit_norm_text(top_q) \
                    not in norm_clause0:
                return ("цитата-носитель посадки не является "
                        "дословной подстрокой клаузы")
    cands = fit.get("candidates")
    if cands is not None:
        if not isinstance(cands, list) or len(cands) < 2:
            return "candidates — минимум две семьи"
        bad = [c for c in cands if fams and c not in fams]
        if bad:
            return f"кандидаты вне базы шаблонов: {bad}"
    params = fit.get("params")
    if params is not None:
        if not isinstance(params, dict) or (not params and not typed):
            return "params — непустой объект {имя: {value, quote}}"
        norm_clause = _fit_norm_text(clause_text)
        conds_all = family_conditions()
        names = _cat("PARAMS")
        for name, cell in params.items():
            cond = conds_all.get(str(name)) if name not in names else None
            if name not in names and cond is None:
                return f"параметр {name!r} вне словаря PARAMS"
            if cond is not None and cond.get("family") != fam:
                return (f"условие {name!r} принадлежит семье "
                        f"{cond.get('family')!r}, не {fam!r}")
            if cond is None and fams and fam in fams \
                    and name not in fams[fam] \
                    and any(name in ps for ps in fams.values()):
                return (f"параметр {name!r} принадлежит другой "
                        f"семье, не {fam!r}")
            if cond is not None and isinstance(cell, dict) and not typed:
                bad = condition_value_problem(name, cond.get("unit"),
                                              cell.get("value"))
                if bad:
                    return bad
            if not isinstance(cell, dict):
                return f"запись параметра {name!r} не объект"
            cell_keys = ("value", "quote", "unit", "relation", "condition", "source", "tz") \
                if typed else ("value", "quote")
            cell_extra = [k for k in cell if k not in cell_keys]
            if cell_extra:
                return (f"посторонние поля записи {name!r}: "
                        f"{sorted(cell_extra)}")
            quote = str(cell.get("quote") or "").strip()
            if not quote or cell.get("value") in (None, ""):
                return f"параметр {name!r} без value/quote"
            if norm_clause and _fit_norm_text(quote) \
                    not in norm_clause:
                return (f"цитата параметра {name!r} не является "
                        f"дословной подстрокой клаузы")
            # Времена цитаты: value обязан совпасть с ОДНИМ ИЗ времён
            # (ревью: цитата-диапазон «с 22:00 до 06:00» судилась по
            # первому матчу и ложно роняла посадку в C).
            times = [int(h) * 60 + int(mn) for h, mn in
                     re.findall(r"\b(\d{1,2}):(\d{2})\b", quote)]
            if times and names.get(name) in ("duration_minutes",
                                             "time_of_day_minutes"):
                val = _fit_value_number(cell.get("value"))
                if val is None or val not in times:
                    return (f"value параметра {name!r} "
                            f"({cell.get('value')!r}) не совпадает "
                            f"ни с одним временем цитаты "
                            f"({times} мин)")
    return None


#: Код перечня — две формы (замечание COSMOS 03.09: типы судов пишутся
#: кириллицей и с цифрами, «Полярис-Р200», «Ту-204»):
#:  (а) короткая аббревиатура ЗАГЛАВНЫМИ любого алфавита, 2–4 знака
#:      («SVG», «PLY», «ЧКЭ», «КВС»);
#:  (б) обозначение модели: слово с большой буквы, содержащее цифру,
#:      с дефисами или без («A320», «B737-800», «ЛТ-100», «Ту-204»,
#:      «Полярис-Р200»). Обычное слово без цифры кодом не становится.
#: Обозначение кода перечня — слово, начинающееся с прописной буквы
#: любого алфавита, вместе с дефисными частями («SVG», «ЧКЭ»,
#: «A320», «B737-800», «ЛТ-100», «Ту-204», «Полярис-Р200»).
#: Кодом запись становится по признаку (см. codes_from_clause):
#: короткая аббревиатура ЗАГЛАВНЫМИ 2–4 знака либо наличие цифры.
#: Обычное слово предложения кодом не становится.
_LIST_CODE_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9_-])"
    r"(?P<code>[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9]*(?:-[A-Za-zА-Яа-яЁё0-9]+)*)"
    r"(?![A-Za-zА-Яа-яЁё0-9_-])"
    r"(?P<gloss>\s*\([^()]{1,60}\))?")


def _is_item_number(code: str, text: str, start: int, end: int) -> bool:
    """Номер записи документа, а не код справочника: запись стоит В
    НАЧАЛЕ клаузы и отделена точкой либо скобкой — «VN-003. Базы
    приписки…» есть нумерация строки (набор H0 03.09, находка
    приёмника COSMOS: номер уезжал в перечень кодов, и приёмник
    честно говорил «кода VN-003 в данных нет»).

    Судит ПОЛОЖЕНИЕ, а не форма записи: по форме номер записи
    неотличим от настоящего кода перечня — «ЛТ-100» и «КТ-001»
    написаны одинаково, и формальный признак съедал бы живые коды
    парка. Потерять код хуже, чем пропустить ссылку на запись в теле
    клаузы: та ловится реквизитами измерителя."""
    return not text[:start].strip() and text[end:end + 1] in (".", ")")


def codes_from_clause(text) -> list:
    """Перечень кодов определения-перечня из клаузы: латинский код 2–4
    знака с необязательной расшифровкой в скобках; цитата — дословный
    фрагмент клаузы (код с расшифровкой). Порядок и повторы — как в
    клаузе (повтор кода не дублируется). Служебные слова-коды формы
    (DEF, DATA, HARD, SOFT, OUT, CASE, BOTH, UTC) не считаются."""
    skip = {"DEF", "DATA", "HARD", "SOFT", "OUT", "CASE", "BOTH", "UTC", "VMP",
            "GOAL", "LP", "SMT", "JSON", "PDF", "ВМП", "ИДАК", "CDPM"}
    out: list = []
    seen: set = set()
    for mo in _LIST_CODE_RE.finditer(str(text or "")):
        code = mo.group("code")
        has_digit = any(ch.isdigit() for ch in code)
        abbrev = code.isupper() and "-" not in code and 2 <= len(code) <= 4
        if not (has_digit or abbrev):
            continue                     # обычное слово, не код
        if code in skip or code in seen:
            continue
        if _is_item_number(code, str(text or ""), mo.start("code"),
                           mo.end("code")):
            continue                     # номер записи документа
        seen.add(code)
        out.append({"code": code, "quote": " ".join(mo.group(0).split())})
    return out


def normalize_fit_shape(fit):
    """Носители посадки к канону формы — нормализация, не суд: пустой
    params ({}) — отсутствие; строка params.quote — цитата-носитель,
    перенесённая в верхний quote (модели кладут её то сверху, то в
    params — проходы №8 и №10 H0 роняли верную посадку в C за форму).
    Возвращает (посадка, перечень нормализаций)."""
    notes: list[str] = []
    if not isinstance(fit, dict):
        return fit, notes
    fit = dict(fit)
    if fit.get("schema_version") == "landing/2":
        # Explicit typed fields (including params={}) are evidence. The
        # typed validator, not legacy empty-slot repair, judges them.
        return fit, notes
    # Пустой необязательный слот (null) — ОТСУТСТВИЕ слота, а не
    # значение: модели пишут «assumption: null» у посадки категории A,
    # и схема честно валит модель («should not be valid under anyOf»
    # — у A не бывает допущения). Форма ответа, не смысл: проход 19
    # финальной серии H0 (03.09) остановился на одной такой записи.
    for key in ("assumption", "gap", "candidates", "pending_question",
                "params", "quote"):
        if key in fit and fit[key] is None:
            fit.pop(key)
            notes.append(f"пустой слот {key} (null) снят как отсутствующий")
    params = fit.get("params")
    if isinstance(params, dict) and isinstance(params.get("quote"), str):
        params = dict(params)
        q = params.pop("quote")
        if not str(fit.get("quote") or "").strip():
            fit["quote"] = q
        fit["params"] = params
        notes.append("цитата-носитель из params перенесена в quote")
    if fit.get("params") == {}:
        fit.pop("params")
    return fit, notes


_HOMOGLYPH_LAT2CYR = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
                                    "x": "х", "y": "у", "k": "к", "m": "м", "t": "т",
                                    "h": "н", "b": "в", "A": "А", "B": "В", "C": "С",
                                    "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О",
                                    "P": "Р", "T": "Т", "X": "Х", "Y": "У"})
_HOMOGLYPH_CYR2LAT = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
                                    "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
                                    "н": "h", "в": "b", "А": "A", "В": "B", "С": "C",
                                    "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O",
                                    "Р": "P", "Т": "T", "Х": "X", "У": "Y"})
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_LAT_RE = re.compile(r"[A-Za-z]")


def normalize_mixed_script(text) -> tuple:
    """Слова со смешением алфавитов (омоглифы: латинская «a» в
    кириллическом слове и наоборот) — к алфавиту большинства букв, если
    после замены слово становится однородным. Возвращает (текст,
    перечень замен «было→стало»). Проход №9 H0: «субботa» с латинской
    «a» в формулировке — аудитор ловил редакционным замечанием."""
    s = str(text or "")
    fixes: list[str] = []

    def _fix(mo):
        w = mo.group(0)
        cyr, lat = len(_CYR_RE.findall(w)), len(_LAT_RE.findall(w))
        if not cyr or not lat:
            return w
        cand = w.translate(_HOMOGLYPH_LAT2CYR) if cyr >= lat else w.translate(_HOMOGLYPH_CYR2LAT)
        if _CYR_RE.search(cand) and _LAT_RE.search(cand):
            return w                     # неоднородно и после замены — не трогаем
        fixes.append(f"{w}→{cand}")
        return cand
    return _WORD_RE.sub(_fix, s), fixes


def fill_fit_params_from_rule(fit: dict, rule_params) -> dict:
    """К-1 принципом, не заплаткой (находка COSMOS на нано 04.09):
    семья со словарными параметрами, а посадка модели пришла без params,
    хотя у самого правила параметры извлечены (workload_spread_threshold
    = 10 рядом с посадкой balanced_workload). Параметры посадки берутся
    из params ПРАВИЛА по именам семьи: значение как есть, цитата —
    как было в правиле (as_written). Ничего не найдено — посадка
    остаётся без params, и К-1 честно отбрасывает её."""
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B") \
            or fit.get("params") or not isinstance(rule_params, list):
        return fit
    fams = template_families()
    fam = fit.get("family")
    names = fams.get(fam) if fams and fam in fams else None
    if not names:
        return fit
    out: dict = {}
    for p in rule_params:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        quote = str(p.get("as_written") or "").strip()
        if name in names and name not in out \
                and p.get("value") not in (None, "") and quote:
            out[name] = {"value": p["value"], "quote": quote}
    if not out:
        return fit
    fit = dict(fit)
    fit["params"] = out
    return fit


def rule_cell_value_problem(cell: dict, family: str, name: str, origin) -> str | None:
    """Read a proposed rule value from its quote under the pinned release.

    Shared by new extraction output and exchange patches. Shape, provenance,
    and quote membership in the actual clause are separate required checks.
    This function neither repairs a cell nor changes its version or pin.
    """
    from cdpm import family_params
    from template_catalog import semantics, units
    unit = cell["unit"]
    reading = semantics.cell_reading(origin, "families", family, name)
    if reading["unresolved"]:
        return f"{name}: исходная ячейка не разрешается"
    form = reading["dictionaries"]["unit"]["form"]
    structured = isinstance(cell["value"], dict)
    numeric = form in units.UNIT_FORMS and form not in units.NON_NUMERIC_FORMS and not structured
    if numeric:
        parsed = family_params.parse_rule_quote(cell["quote"], unit, source=origin.path)
        matches = (parsed is not None and parsed[0] == cell["value"]
                   and isinstance(parsed[0], bool) == isinstance(cell["value"], bool))
        if not matches and unit == "time_of_day_minutes":
            # One clock range proves either endpoint, not extra numbers,
            # malformed clocks, or a guessed timezone.
            clock = r"([01]?[0-9]|2[0-3]):([0-5][0-9])"
            window = re.fullmatch(r"(?:с\s+|от\s+)?" + clock
                + r"\s*(?:до|по|[-–—])\s*" + clock,
                str(cell["quote"]).strip(), re.IGNORECASE)
            if window:
                h1, m1, h2, m2 = map(int, window.groups())
                matches = cell["value"] in (h1 * 60 + m1, h2 * 60 + m2)
        if not matches:
            return f"{name}: значение не подтверждено цитатой клаузы"
    elif structured:
        problems = family_params.base_map_quote_problems(cell["value"], cell["quote"])
        if problems:
            return "; ".join(problems)
    return None


def accept_fit_candidate(c: dict, clause_text: str, *, new_output=False):
    """Judge a candidate; new extraction/repair output has a typed header.

    Legacy input remains accepted on the existing compatibility path. Only
    the explicit new-output caller supplies a code-owned final rule ID and
    can establish rule provenance from the actual clause. This is not a
    migration of saved evidence and creates no migration history.
    """
    import copy
    fit, conf, note = _accept_fit_candidate(copy.deepcopy(c), clause_text)
    if not new_output or fit is None:
        return fit, conf, note
    typed = fit.get("schema_version") == "landing/2"
    fit["schema_version"] = "landing/2"
    if fit.get("category") not in ("A", "B"):
        return fit, conf, note

    def unresolved(words):
        return ({"schema_version": "landing/2", "category": "C",
                 "registry_rev": registry_fingerprint(),
                 "gap": f"новая посадка не подтверждена: {words}"},
                conf, f"новая посадка не подтверждена: {words}")

    if not isinstance(c.get("id"), str) or not c["id"].strip() or not clause_text:
        return unresolved("нужны идентификатор правила-носителя и текст клаузы")
    from template_catalog import landing, semantics, units
    from cdpm import family_params
    from cdpm.extract import _whole_value_quote
    try:
        catalog = _catalog_bridge.catalog()
        origin = catalog
        if typed:
            pin = fit["family_contract"]
            origin = semantics.resolve_release(catalog, fingerprint=pin["catalog_fingerprint"],
                                               version=pin["catalog_version"])
        # Only a newly constructed legacy candidate receives missing fields;
        # an explicit typed candidate must not be silently completed.
        if not typed:
            fit.setdefault("params", {})
        proposed = {**c, "template_fit": fit}
        for name, cell in list(fit.get("params", {}).items()):
            if not _whole_value_quote(clause_text, str(cell.get("quote") or "")):
                return unresolved(f"{name}: цитата обрезает число/единицу или содержит номер ссылки")
            if not typed:
                cell = landing.prepare_cell(proposed, name, cell, catalog)
                # The legacy candidate judge allowed value/quote only; an
                # untrusted answer/code source cannot be relabelled as rule.
                cell["source"] = {"kind": "rule", "rule_id": c["id"]}
            unit = cell["unit"]
            reading = semantics.cell_reading(origin, "families", fit["family"], name)
            if reading["unresolved"]:
                return unresolved(f"{name}: исходная ячейка не разрешается")
            form = reading["dictionaries"]["unit"]["form"]
            if not typed:
                cell["value"] = family_params.legacy_structural_value(cell["value"], reading)
            structured = isinstance(cell["value"], dict)
            numeric = form in units.UNIT_FORMS and form not in units.NON_NUMERIC_FORMS and not structured
            if numeric:
                if not typed:
                    cell["value"] = units.to_canonical(cell["value"], unit, unit, source=origin.path)
            problem = rule_cell_value_problem(cell, fit["family"], name, origin)
            if problem:
                return unresolved(problem)
            fit["params"][name] = cell
        problems = landing.problems(proposed, catalog)
        if problems:
            return unresolved("; ".join(p["words"] for p in problems))
    except (_catalog_bridge.CATALOG_ERRORS + (ValueError, TypeError, KeyError)) as error:
        return unresolved(str(error))
    return fit, conf, note


def _accept_fit_candidate(c: dict, clause_text: str):
    """Приём посадки кандидата извлечения (fail-closed): грязная или
    отсутствующая посадка машинного правила НЕ гибнет и НЕ проходит
    на веру — синтезируется честная категория C со следом (гейт
    запуска увидит её явным статусом). Возврат: (fit|None,
    confidence|None, note|None). Legacy получает ссылку от кода;
    explicit landing/2 сохраняет исходную ссылку после строгой проверки.
    Это вход извлечения из клаузы, не миграция и не применение ответа."""
    kind = c.get("kind")
    machine = kind in ("HARD", "SOFT", "УСЛОВНЫЙ")
    fit = c.get("template_fit")
    if not machine:
        note = ("посадка у немашинного вида отброшена"
                if fit is not None else None)
        return None, None, note
    rev = registry_fingerprint()
    if not isinstance(fit, dict):
        return ({"category": "C", "registry_rev": rev,
                 "gap": "модель не выполнила посадку"},
                None, "посадка: не выполнена моделью — категория C")
    fit, _norm_notes = normalize_fit_shape(fit)
    typed = fit.get("schema_version") == "landing/2"
    if typed:
        import copy
        fit = copy.deepcopy(fit)  # adopting a second reading must not alias its cells
    else:
        fit.pop("registry_rev", None)      # legacy rev ставит только код
    conf = fit.pop("confidence", None)
    if not isinstance(conf, int) or isinstance(conf, bool) \
            or not 0 <= conf <= 100:
        conf = None
    if not typed:
        fit = fill_fit_params_from_rule(fit, c.get("params"))
        fit = canon_fit_conditions(fit)
    reason = _fit_shape_reason(fit, clause_text)
    if typed and reason is None and fit.get("category") in ("A", "B"):
        from template_catalog import landing
        if not isinstance(c.get("id"), str) or not c["id"].strip() or not clause_text:
            reason = "typed посадка требует известного правила-носителя и текста клаузы"
        else:
            try:
                problems = landing.problems({**c, "template_fit": fit}, _catalog_bridge.catalog())
                if problems:
                    reason = "; ".join(p["words"] for p in problems)
                elif any(cell["source"]["kind"] != "rule" for cell in fit.get("params", {}).values()):
                    reason = "извлечение из клаузы не подтверждает происхождение ответа или миграции"
            except _catalog_bridge.CATALOG_ERRORS as error:
                reason = str(error)
    if reason:
        return ({"category": "C", "registry_rev": rev,
                 "gap": f"посадка модели отброшена: {reason}"},
                conf, f"посадка отброшена: {reason}")
    # Лексический контракт семьи (слой 1 против неверного чтения, план
    # «прямой ход» 2.7): посадка A/B законна, только если клауза несёт
    # слово-носитель предмета семьи («12:00» из клаузы про отдых не
    # ложится в предел смены — в клаузе нет слов класса «смена»).
    if fit.get("category") in ("A", "B") and clause_text:
        from cdpm import family_lexicon as _lex
        fam = str(fit.get("family") or "")
        conflict = _lex.subject_conflict(fam, clause_text) \
            if _lex.family_subject_words(fam) else None
        if conflict is not None:
            why = (f"клауза несёт предмет семьи {conflict[0]!r} "
                   f"(«{conflict[1]}») и ни одного слова-носителя семьи "
                   f"{fam!r} — спор о чтении, не посадка")
            return ({"category": "C", "registry_rev": rev,
                     "gap": f"посадка модели отброшена: {why}"},
                    conf, f"посадка отброшена: {why}")
    if not typed:
        fit["registry_rev"] = rev
    if not typed and fit.get("category") in ("A", "B"):
        from cdpm import family_contract
        fit["family_contract"] = family_contract.snapshot(
            str(fit["family"]), (fit.get("params") or {}).keys())
    return fit, conf, None


def ensure_fit_questions(doc: dict) -> list[str]:
    """П-3 пакета посадки: B-посадка (ровно одно явное допущение)
    обязана получить вопрос-ПОДТВЕРЖДЕНИЕ авиакомпании со
    СТРУКТУРНЫМ ключом fit-{семья}-{параметр} — анкета и книга
    регрессии ключуются машинно, не текстом. Идемпотентен: живой
    pending_question второго вопроса не рождает. Возвращает id
    созданных вопросов."""
    created: list[str] = []
    q_ids = {q.get("id") for q in doc.get("questions", [])}
    for r in doc.get("rules", []):
        fit = r.get("template_fit")
        if not isinstance(fit, dict) or fit.get("category") != "B":
            continue
        if fit.get("pending_question") in q_ids:
            continue
        refs = [s for s in (r.get("sources") or [])
                if isinstance(s, dict)]
        if not refs:
            continue           # строку без трассировки судит валидатор
        fam = fit.get("family")
        params = sorted((fit.get("params") or {}).keys())
        key = f"fit-{fam}-{params[0] if params else 'посадка'}"
        qid = stable_question_id("2", key)
        n = 1
        while qid in q_ids:
            n += 1
            qid = stable_question_id("2", key, n)
        doc.setdefault("questions", []).append({
            "id": qid, "part": "2",
            "title": f"Подтверждение допущения посадки: {fam}",
            "text": (f"Правило {r.get('id')} посажено на шаблон "
                     f"«{fam}» с интерпретирующим допущением: "
                     f"{fit.get('assumption')}. Подтвердите "
                     f"прочтение."),
            "answer_format": {"type": "choice", "options": [
                {"key": "а",
                 "text": "допущение верно — подтверждаем"},
                {"key": "б",
                 "text": "неверно — уточнение приложим свободным "
                         "текстом"}]},
            "rule_refs": [r.get("id")],
            "source_refs": refs[:1]})
        fit["pending_question"] = qid
        q_ids.add(qid)
        created.append(qid)
    return created


def align_fit_with_kind(doc: dict, rule: dict, reason: str) -> list[str]:
    """Согласование посадки с видом после смены вида правила:
    немашинный вид посадку не несёт (снимается); машинный при
    действующем контракте посадки обязан её нести — отсутствующая
    синтезируется честной категорией C со следом (не выдумкой).
    Затем — ярлыки вида и зоны КОДОМ (derive_rule_traits); возврат —
    перечень изменений ярлыков (след для журналов)."""
    kind = rule.get("kind")
    if kind in ("HARD", "SOFT"):
        if bool((doc.get("meta") or {}).get("fit_contract")) \
                and not isinstance(rule.get("template_fit"), dict):
            rule["template_fit"] = {
                "category": "C",
                "registry_rev": registry_fingerprint(),
                "gap": f"посадка не выполнялась: {reason}"}
    else:
        rule.pop("template_fit", None)
    return derive_rule_traits(doc, rule, reason)


# ------------------------------------------------------------------
# Детерминизация ярлыков (решение владельца 2026-09-02, прогон №2 H0
# run-0007): вид и зону правила, посаженного на семью базы, ставит
# КОД по атрибутам семьи; вид DATA законен только у правил из файлов
# данных; дубли кандидатов на клаузе и чужие якоря судит код, а не
# модель. Живой корень: три расхождения с эталоном H0 (VN-003 DATA
# вместо DEF, VN-081 и VN-D91 — зоны) были ярлыками модели при верном
# содержании правил; ни цикл, ни анкета их не двигали.
# ------------------------------------------------------------------

def template_family_traits() -> dict:
    """Атрибуты семей базы шаблонов: {семья: {"kind": HARD|SOFT|None,
    "zone": PAIRING|ROSTERING|BOTH|None}} по ЗАПИСЯМ каталога шаблонов
    (шаг 7: прежде — `vmp.template_registry.families_meta()` с режимом
    среза; обёртки CDPM больше не читает, среза нет — область
    закрепляется версией каталога). Без каталога — отказ словами: ярлык
    ставит код по свойству семьи, и выдумывать его нечем.

    Вид и зона сверяются с законом формы модели (KINDS и ZONES кода):
    значение вне закона — None, ярлык модели тогда остаётся."""
    out: dict = {}
    for fam, spec in _catalog_bridge.family_traits().items():
        kind = spec.get("kind")
        zone = spec.get("zone")
        out[str(fam)] = {
            "kind": kind if kind in ("HARD", "SOFT") else None,
            "zone": zone if zone in ZONES else None}
    return out


def data_source_sids(doc: dict) -> set:
    """sid источников-файлов данных (meta.data_sids) — единственный
    законный носитель вида DATA. Извлечение правил идёт по документам
    правил (файлы данных живут линией данных), поэтому у обычной
    сборки перечень пуст."""
    ids = (doc.get("meta") or {}).get("data_sids") or []
    return {str(s) for s in ids if isinstance(s, str) and s}


def _conditional_open(rule: dict) -> bool:
    cond = rule.get("conditional")
    return isinstance(cond, dict) \
        and cond.get("question") in (rule.get("blockers") or [])


def _fork_is_modality_only(rule: dict) -> bool:
    """Развилка только о модальности (все ветви — HARD/SOFT). У семьи
    с фиксированным видом она беспредметна: вид задаёт шаблон, а не
    ответ; развилка о применимости (ветвь OUT и т.п.) — предметна."""
    cond = rule.get("conditional")
    if not isinstance(cond, dict):
        return False
    branches = cond.get("branches") or {}
    return bool(branches) and set(branches.values()) <= {"HARD", "SOFT"}


def _resolve_moot_fork(doc: dict, rule: dict, fam, fk: str) -> str:
    """Снятие беспредметной развилки кодом (ревью r54: иначе ответ
    авиакомпании на неё молча отменялся семьёй сразу после
    применения): вид = вид семьи, развилка и блокировка снимаются;
    черновик может быть сокращён/удалён. Опубликованное издание и
    реальный ответ сохраняются; решение кода по строке записывается
    отдельно, общий вопрос закрывается лишь после всех его строк."""
    cond = rule["conditional"]
    qid = cond.get("question")
    qs = doc.get("questions") or []
    q = next((x for x in qs if isinstance(x, dict)
              and x.get("id") == qid), None)
    if q is not None and "edition" in q:
        from cdpm import question_editions, question_resolutions
        question_editions.require_valid(q)
        issues = question_resolutions.problems(doc, q)
        if issues or rule.get("id") not in question_editions.targets(q):
            raise question_editions.EditionRefusal("; ".join(issues) or "строка вне области издания")
        if question_editions.latest(doc, q) is not q:
            raise question_editions.EditionRefusal("развилка адресует прежнее издание")
        refusal = code_close_refusal(doc, q)
        if refusal:
            raise CodeCloseRefused(refusal)
    rule.pop("conditional")
    rule["kind"] = fk
    if fk == "HARD":
        rule.pop("weight", None)
        rule.pop("export_solver", None)
    elif cond.get("weight") is not None and rule.get("weight") is None:
        rule["weight"] = cond["weight"]      # П7: вес ждал в развилке
    rule["blockers"] = [b for b in (rule.get("blockers") or [])
                        if b != qid]
    if not rule["blockers"]:
        rule.pop("blockers", None)
    if q is not None and "edition" in q:
        close_question_by_code(doc, q, raw="снят кодом", note=f"вид {fk} задаёт семья {fam}",
                               rule_resolution={"rule": rule["id"], "family": fam, "kind": fk})
    elif q is not None:
        refs = [r for r in (q.get("rule_refs") or [])
                if r != rule.get("id")]
        if refs:
            q["rule_refs"] = refs
        elif not q.get("answer"):
            qs.remove(q)
    return f"развилка {qid} снята: вид {fk} задаёт семья {fam}"


def derive_rule_traits(doc: dict, rule: dict, reason: str,
                       traits: dict | None = None) -> list[str]:
    """Ярлыки правила КОДОМ из посадки и источника (действует только
    при контракте посадки meta.fit_contract; идемпотентна):
    1) вид DATA у правила не из файла данных → DEF (справка документа
       правил — определение, не данные набора);
    2) правило с посадкой A/B: зона и вид — атрибуты семьи; вид не
       трогается, пока открыта развилка модальности (УСЛОВНЫЙ) —
       её решает ответ авиакомпании.
    Возвращает перечень изменений (пусто — ничего не менялось);
    след пишется в note правила."""
    if not bool((doc.get("meta") or {}).get("fit_contract")):
        return []
    changes: list[str] = []
    kind = rule.get("kind")
    if kind == "DATA":
        sids = {s.get("sid") for s in (rule.get("sources") or [])
                if isinstance(s, dict)}
        if not sids or not sids <= data_source_sids(doc):
            rule["kind"] = "DEF"
            rule.pop("weight", None)
            rule.pop("export_solver", None)
            changes.append("вид DATA→DEF: источник — документ правил, "
                           "не файл данных")
            kind = "DEF"
    fit = rule.get("template_fit")
    if isinstance(fit, dict) and fit.get("category") in ("A", "B") \
            and kind in ("HARD", "SOFT"):
        fam = fit.get("family")
        tr = (traits if traits is not None
              else template_family_traits()).get(fam) or {}
        fz, fk = tr.get("zone"), tr.get("kind")
        if fz and rule.get("zone") != fz:
            changes.append(f"зона {rule.get('zone')}→{fz} по семье "
                           f"{fam}")
            rule["zone"] = fz
        if fk:
            if _conditional_open(rule):
                # Развилка только о модальности у семьи с фиксированным
                # видом беспредметна — снимает код (вопрос авиакомпании
                # не задаётся); развилка о применимости (OUT) — вид
                # ждёт ответа авиакомпании.
                if _fork_is_modality_only(rule):
                    changes.append(_resolve_moot_fork(doc, rule, fam, fk))
            elif kind != fk:
                changes.append(f"вид {kind}→{fk} по семье {fam}")
                rule["kind"] = fk
                if fk == "HARD":
                    rule.pop("weight", None)
                    rule.pop("export_solver", None)
    if changes:
        note = f"ярлыки кодом ({reason}): " + "; ".join(changes)
        rule["note"] = ((str(rule.get("note") or "") + "; " + note)
                        .strip("; "))
    return changes


def derive_all_rule_traits(doc: dict, reason: str) -> list[str]:
    """Сквозной проход ярлыков по всем строкам свода (идемпотентен)."""
    traits = template_family_traits()
    out: list[str] = []
    for r in doc.get("rules", []):
        if isinstance(r, dict):
            out += [f"{r.get('id')}: {c}"
                    for c in derive_rule_traits(doc, r, reason, traits)]
    return out


#: Машинные критерии оптимизации: метка документа → вид словаря
#: приёмника (vmp.criteria.SPEC). Расширение — только вместе с
#: исполнителем критерия у решателя.
CRITERION_KINDS = {"MIN_PERSON_DAYS": "min_person_days",
                   "NORMALIZED_COST": "rank_normalized_cost"}

#: Закрытый перечень законных смен вида строки правкой (заплата цикла,
#: патч ответов): всё остальное — конвенция ВМП, а не чтение (вид и
#: зона машинного правила — атрибуты семьи посадки; DEF→HARD у перечня
#: без модальности запрещён). Один перечень для всех путей правки.
KIND_MIGRATIONS = frozenset({("HARD", "DATA"), ("CASE", "DATA"),
                             ("DEF", "DATA"), ("CASE", "SOFT"),
                             ("HARD", "SOFT")})


def kind_change_allowed(old_kind, new_kind, new_fit) -> bool:
    """Смена вида законна по перечню миграций либо когда новый вид —
    атрибут семьи посадки A/B новой строки (вид ставит семья)."""
    if old_kind == new_kind or new_kind not in KINDS:
        return True
    if new_kind == "OUT":                 # снятие строки со следом — законно
        return True
    if (old_kind, new_kind) in KIND_MIGRATIONS:
        return True
    fam = new_fit.get("family") if isinstance(new_fit, dict) \
        and new_fit.get("category") in ("A", "B") else None
    fam_kind = (template_family_traits().get(fam) or {}).get("kind") if fam else None
    return fam_kind == new_kind


def accept_objective_criterion(crit, clause_text: str = ""):
    """Приём машинного критерия, названного ДОКУМЕНТОМ (носитель —
    целевая; П6/П11, 2026-09-02). Возврат (блок|None, причина|None):
    вид — из закрытого словаря CRITERION_KINDS (метка либо вид
    приёмника), ставка — целое > 0 (если дана), версия и дата — как
    написано, as_written — дословный фрагмент клаузы (если текст
    клаузы дан — проверяется подстрокой). Грязный блок — None с
    причиной, не выдумка."""
    if not isinstance(crit, dict):
        return None, f"критерий не объект: {repr(crit)[:40]}"
    raw_kind = str(crit.get("kind") or "").strip()
    kind = CRITERION_KINDS.get(raw_kind.upper()) \
        or (raw_kind if raw_kind in CRITERION_KINDS.values() else None)
    if kind is None:
        return None, (f"вид критерия {raw_kind!r} вне словаря "
                      f"{sorted(CRITERION_KINDS)}")
    aw = str(crit.get("as_written") or "").strip()
    if not aw:
        return None, "критерий без дословной цитаты as_written"
    if clause_text and not (quote_in_text(aw, clause_text)
                            or value_quote_in_text(aw, crit.get("rate_per_duty"), clause_text)):
        return None, f"цитата критерия {aw[:40]!r} не из клаузы"
    block = {"kind": kind, "as_written": aw}
    # «За что» начисляется ставка (форма ВМП, строка в): rate.per) —
    # дословно из клаузы, если названо (план «прямой ход» 2.1: поле
    # контракта, которого извлечение не знало — прогон №4).
    per = crit.get("per")
    if per not in (None, ""):
        per_s = " ".join(str(per).split())
        if clause_text and _fit_norm_text(per_s) not in _fit_norm_text(clause_text):
            return None, f"«за что» ставки {per_s[:40]!r} не из клаузы"
        block["per"] = per_s
    # Полнота ПО ВИДУ — как у исполнителя приёмника (vmp.criteria,
    # политика MUST_BE_EXTERNAL): неисполнимый блок документ не несёт
    # (ревью r55: неполный блок глушил вопрос анкеты и ронял исполнителя).
    for f in ("version", "effective_from"):
        v = crit.get(f)
        if v in (None, ""):
            return None, (f"критерий без поля {f} — версию и дату "
                          f"действия ставок спросит анкета")
        block[f] = str(v)
    if kind == "min_person_days":
        rate = crit.get("rate_per_duty")
        if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
            return None, ("критерий без ставки rate_per_duty (целое > 0) "
                          "— ставку спросит анкета")
        block["rate_per_duty"] = rate
    else:                                     # rank_normalized_cost
        rates = crit.get("rates")
        if not isinstance(rates, dict):
            return None, ("критерий нормированной стоимости без ставок "
                          "рангов rates {flight, work} — ставки спросит "
                          "анкета")
        out_rates = {}
        for f in ("flight", "work"):
            v = rates.get(f)
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or v <= 0:
                return None, f"ставка рангов {f} не число > 0: {v!r}"
            out_rates[f] = float(v)
        block["rates"] = out_rates
    return block, None


def accept_objective_rate(rate, clause_text: str = ""):
    """Ставка НЕмашинного критерия (форма ВМП: каждая строка в) несёт
    ставку в валюте; носитель в модели — objectives[].rate). Возврат
    (блок|None, причина|None): value — число > 0, per — «за что» (если
    названо), as_written — дословный фрагмент клаузы со ставкой
    (проверяется подстрокой, когда текст клаузы дан)."""
    if not isinstance(rate, dict):
        return None, f"ставка не объект: {repr(rate)[:40]}"
    v = rate.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None, f"ставка без числа > 0: {v!r}"
    aw = " ".join(str(rate.get("as_written") or "").split())
    if not aw:
        return None, "ставка без дословной цитаты as_written"
    if clause_text and not value_quote_in_text(aw, v, clause_text):
        return None, f"цитата ставки {aw[:40]!r} не из клаузы"
    block = {"value": v, "as_written": aw}
    per = rate.get("per")
    if per not in (None, ""):
        per_s = " ".join(str(per).split())
        if clause_text and _fit_norm_text(per_s) not in _fit_norm_text(clause_text):
            return None, f"«за что» ставки {per_s[:40]!r} не из клаузы"
        block["per"] = per_s
    return block, None


def objective_direction_problems(objectives: list) -> list[str]:
    """A06: one catalogue comparison for model validation and readiness.

    Legacy 0.5 has no direction contract. Its existing archive/data path is
    unchanged; this does not authorize execution without the builder's gate.
    """
    problems = []
    try:
        if "directions" not in _catalog_bridge.catalog().dictionaries():
            return []
        for objective in objectives:
            if not isinstance(objective, dict):
                continue
            criterion = objective.get("criterion")
            kinds = [objective.get("metric")]
            if isinstance(criterion, dict) and criterion.get("kind"):
                kinds.append(criterion["kind"])
            for kind in dict.fromkeys(k for k in kinds if isinstance(k, str) and k):
                try:
                    expected = _catalog_bridge.criterion_direction(kind)
                except _catalog_bridge.CATALOG_ERRORS as exc:
                    problems.append(f"criterion-direction: {objective.get('id')}: "
                                    + _catalog_bridge.words_of(exc))
                    continue
                if objective.get("direction") != expected:
                    problems.append(
                        f"criterion-direction: {objective.get('id')}: направление "
                        f"{objective.get('direction')!r} не совпадает с каталогом "
                        f"({kind} → {expected})")
    except _catalog_bridge.CATALOG_ERRORS as exc:
        problems.append("criterion-direction: " + _catalog_bridge.words_of(exc))
    return problems


def _criterion_with_catalogue_direction(criterion: dict) -> dict:
    if "directions" not in _catalog_bridge.catalog().dictionaries():
        return criterion
    return {**criterion, "direction": _catalog_bridge.criterion_direction(criterion["kind"])}


def criterion_block_complete(c, *, objective=None) -> bool:
    """Исполним ли блок criterion целевой (те же требования, что у
    accept_objective_criterion): вид словаря, версия и дата, ставка
    (min_person_days) либо ставки рангов (rank_normalized_cost).
    При переданной целевой также сверяется её направление с каталогом;
    проверка только структуры блока остаётся доступной без objective."""
    if not isinstance(c, dict) or c.get("kind") not in CRITERION_KINDS.values():
        return False
    if isinstance(objective, dict) and objective_direction_problems([objective]):
        return False
    if not c.get("version") or not c.get("effective_from"):
        return False
    if c["kind"] == "min_person_days":
        r = c.get("rate_per_duty")
        return isinstance(r, int) and not isinstance(r, bool) and r > 0
    from vmp.objective_projection import positive_finite_rate
    rates = c.get("rates")
    return isinstance(rates, dict) and all(
        positive_finite_rate(rates.get(f)) for f in ("flight", "work"))


def criterion_from_objectives(doc: dict) -> dict | None:
    """Критерий пейринга из блока criterion целевой зоны PAIRING/BOTH
    (носитель — документ, не ответ анкеты). Форма — как у
    _criterion_from_answer: kind, label, rate_per_duty, rates_version,
    as_written, source_rule (= id целевой)."""
    for o in doc.get("objectives", []) or []:
        if not isinstance(o, dict) \
                or o.get("zone") not in ("PAIRING", "BOTH"):
            continue
        c = o.get("criterion")
        # Носителем считается только ИСПОЛНИМЫЙ блок (ревью r55):
        # неполный блок не перекрывает ответ анкеты и не глушит вопрос.
        if not criterion_block_complete(c, objective=o):
            continue
        label = next(k for k, v in CRITERION_KINDS.items()
                     if v == c["kind"])
        out = {"kind": c["kind"], "label": label,
               "source_rule": str(o.get("id")),
               "as_written": str(c.get("as_written") or ""),
               "source_objective": str(o.get("id")),
               "rates_version": {
                   "source": str(o.get("id")),
                   "version": str(c.get("version")),
                   "effective_from": str(c.get("effective_from"))}}
        if "per" in c:
            out["per"] = c["per"]
        if c["kind"] == "min_person_days":
            out["rate_per_duty"] = c["rate_per_duty"]
        else:
            out["rates"] = {"flight": c["rates"]["flight"],
                            "work": c["rates"]["work"]}
        return _criterion_with_catalogue_direction(out)
    return None


class CarrierDivergence:
    """Значение параметра, у которого два носителя строки (ячейка посадки
    и запись `params[]`) РАСХОДЯТСЯ (A05-барьер, контракт П0 §3.1 закон 3,
    §12.5): `rule_param_cells` отдаёт ЭТО вместо первого попавшегося
    значения. Объект несёт слова проблемы; имя по-прежнему «несёт
    строка» (`in` работает — темы и внесения не плодят второй носитель),
    но ни одному числу не равен, и любая арифметика над ним падает
    громко, а не молчит одной из двух правд."""

    __slots__ = ("words",)

    def __init__(self, words: str) -> None:
        self.words = str(words)

    def __str__(self) -> str:
        return self.words

    def __repr__(self) -> str:
        return f"CarrierDivergence({self.words!r})"

    def __bool__(self) -> bool:
        return True


#: Код отказа выпуска при расхождении носителей параметра (контракт П0 §5).
PARAM_CARRIERS_DIVERGE = "param-carriers-diverge"


def _canon_condition(value) -> tuple | None:
    """Условие/область носителя одной формой для сверки: структурное
    условие `{kind, value}` → (вид, значение), строка уточнителя →
    (None, канон области либо нормализованный текст); пусто → None
    (вся область)."""
    if isinstance(value, dict):
        kind = str(value.get("kind") or "") or None
        raw = value.get("value")
        text = canon_area_qualifier(raw) if isinstance(raw, str) else None
        return (kind, text or _fit_norm_text(raw))
    if isinstance(value, str):
        if not value.strip():
            return None
        return (None, canon_area_qualifier(value) or _fit_norm_text(value))
    return None


def _carrier_words(value, unit_word, unit: str) -> str:
    """Значение носителя словами «как записано (единица)»."""
    return f"{value!r} ({unit_word or unit})"


def _unit_text(word_or_code) -> str:
    """Слово единицы носителя одной формой для сравнения ЗАПИСЕЙ (не
    для приведения значения): без краевых пробелов, в нижнем регистре."""
    return " ".join(str(word_or_code or "").split()).lower()


def _carrier_unit_text(word_or_code, unit: str) -> str:
    """Единица носителя для сравнения ЗАПИСЕЙ с законом «нет единицы =
    каноническая единица записи» (контракт П0 §12.5; ровно это уже
    делает `family_params.unit_scale` при приведении значения).
    Исправление В-4 неавтор-ревью: ячейки посадки единицу не несут
    вовсе (её ставит канон сборки `unit = PARAMS[имя]`), и требование
    БУКВАЛЬНОГО совпадения слов единиц объявляло «сверка невозможна»
    там, где обе записи одинаковы и законны."""
    return _unit_text(word_or_code) or _unit_text(unit)


def _base_code_of(value) -> str | None:
    """Код базы, названный условием ячейки либо уточнителем записи:
    строка уточнителя или структурное условие {kind, value}. None —
    база не названа."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _identical_raw(a, b) -> bool:
    """Две записи носителя БУКВАЛЬНО одинаковы: равны по типу и по
    содержанию (логическое `True` не равно числу 1; перечни и объекты
    сравниваются поэлементно, порядок ключей объекта значения не
    имеет). Нужно там, где значение параметра — не число: массив или
    объект обозначений (контракт П0 §3.1 — «строка/массив обозначений,
    объект {код базы: целое}») грамматикой числа не читается, и два
    носителя с ОДНОЙ И ТОЙ ЖЕ записью не расходятся, сколько бы её ни
    разбирать. Отличие хоть в чём-нибудь НЕ означает расхождения сразу:
    оно возвращает сверку числам, грамматике и структурному закону
    (`_structural_verdict`), которые и решают, одна ли это правда."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_identical_raw(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) \
            and all(_identical_raw(x, y) for x, y in zip(a, b))
    if isinstance(a, (dict, list, tuple)) or isinstance(b, (dict, list, tuple)):
        return False
    return type(a) is type(b) and a == b


def _num_words(value) -> str:
    """Число словами без экспоненты и хвостовых нулей (Decimal);
    логическое значение единицы `flag` — словами true/false (умножать
    и округлять его нечего)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    try:
        if value == value.to_integral_value():
            return str(int(value))
        return format(value.normalize(), "f")
    except (AttributeError, TypeError, ValueError):
        return str(value)


def _structural_verdict(cell, p, unit: str, base_qualified=False):
    """Сверка носителей, у которых хотя бы одно значение — НЕ скаляр
    (исправление В-1 неавтор-ревью A05): None — случай не структурный
    (обе записи скаляры либо единица обозначений, где перечень читает
    канон кодов); "" — носители согласны; иначе — слова расхождения.

    Закон (контракт П0 §3.1): структурная запись грамматике числа не
    показывается никогда. Два объекта/перечня сверяются ЗАПИСЬЮ
    (`_identical_raw`). Объект формы «норма по базам» против скаляра с
    уточнителем базы сверяется записью ТОЙ базы, которую назвал
    скалярный носитель; нет такой записи — расхождение. «Согласие» из
    несводимых форм не выдумывается ни в одном случае."""
    from cdpm import family_params as _fp
    cell_raw, leg_raw = cell.get("value"), p.get("value")
    a_s, b_s = _fp.is_structural(cell_raw), _fp.is_structural(leg_raw)
    if base_qualified:
        a_s = a_s or (isinstance(cell_raw, str) and ":" in cell_raw)
        b_s = b_s or (isinstance(leg_raw, str) and ":" in leg_raw)
    if not a_s and not b_s:
        return None
    if unit in _cat("CODE_UNITS"):
        return None                 # перечень обозначений — канон кодов
    both = (f"посадка {cell_raw!r} против params[] {leg_raw!r}")
    if a_s and b_s:
        if base_qualified:
            a_map, b_map = _fp.base_map_value(cell_raw), _fp.base_map_value(leg_raw)
            if a_map is not None and b_map is not None and a_map == b_map:
                return ""
        return "" if _identical_raw(cell_raw, leg_raw) else \
            f"значение: {both} (записи не числа — сверяются записью)"
    table = (_fp.base_map_value if base_qualified else _fp.base_map)(cell_raw if a_s else leg_raw)
    obj_side = "посадка" if a_s else "params[]"
    num_side = "params[]" if a_s else "посадка"
    base = _base_code_of(p.get("qualifier") if a_s
                         else cell.get("condition"))
    if table is None:
        return (f"значение: {both} — запись {obj_side} не число и не "
                f"форма «{{код базы: целое}}» (§3.1): со скаляром к "
                f"одной правде не сводится")
    if not base:
        return (f"значение: {both} — «{obj_side}» несёт норму по базам, "
                f"а «{num_side}» базу не назвал (уточнителя нет)")
    if base not in table:
        return (f"значение: {both} — записи базы {base} у носителя "
                f"«{obj_side}» нет, а «{num_side}» её заявил")
    got, bad = _fp.canonical_value(
        leg_raw if a_s else cell_raw,
        (p if a_s else cell).get("unit"), unit)
    if bad is not None:
        return f"значение: {both} — {num_side}: {bad}"
    if table[base] == got:
        return ""
    return (f"значение: {both} — база {base}: "
            f"{_num_words(table[base])} против {_num_words(got)} {unit}")


def carrier_divergences(rule: dict) -> list[dict]:
    """Расхождения носителей параметра ОДНОЙ строки (A05-барьер, контракт
    П0 §3.1 закон 1/3 и §12.5, 12.09.2026): у имени, которое несут ОБА
    носителя — ячейка посадки A/B `template_fit.params.<имя>` и запись
    `params[]`, — сверяются значение, единица, отношение и
    условие/область. Единица и отношение имени — из записи каталога:
    у параметра семьи, а у имени условия исключения — из перечня
    условий той же семьи (ячейка посадки несёт и их).

    Значения приводятся к канонической единице семьи
    (`family_params.canonical_value`: код единицы каталога или слово
    минимального перечня K02; носитель без единицы — уже в канонической
    единице). Отношение ячейки без своего `relation` (и с пустым
    `relation: null`) — отношение записи семьи: пустое поле его не
    отменяет, а наследует. Условие сверяется структурно, когда оба
    структурны, иначе по нормализованному тексту (канон области);
    ячейка без ключа `condition` область не заявляет и по ней не
    судится (её канон берёт уточнитель записи), а `condition: null` —
    «вся область», и против уточнителя это расхождение.

    Имя только у одного носителя расхождением не является. Два
    носителя с буквально одинаковой записью согласны и без разбора —
    так сверяются значения, которые числом не читаются (перечень или
    объект обозначений); форму такой записи судит проверка посадки.
    Единица у «близнецов» сравнивается с поправкой на закон §12.5:
    ОТСУТСТВИЕ единицы равно канонической единице записи семьи (ячейки
    посадки единицу не несут вовсе — её ставит канон сборки), и
    одинаковая запись без единицы против записи с кодом каталога —
    согласие, а не «сверка невозможна» (В-4).

    Значение-НЕскаляр грамматике числа не показывается никогда:
    объекты и перечни сверяются структурно (`_structural_verdict`,
    В-1), а объект формы «{код базы: целое}» против скаляра с
    уточнителем базы — записью ТОЙ базы. Нечитаемое значение или
    единица у РАЗНЫХ записей — тоже проблема: сверка невозможна,
    «совпало» не выдумывается. Возвращает [{rule, param, words}] без
    повторов (две одинаковые записи params[] — одна беда)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return []
    cells = fit.get("params") if isinstance(fit.get("params"), dict) else {}
    legacy = [p for p in (rule.get("params") or [])
              if isinstance(p, dict) and isinstance(p.get("name"), str)]
    if not cells or not legacy:
        return []
    from cdpm import family_params as _fp
    rid = str(rule.get("id"))
    fam = str(fit.get("family") or "")
    specs = {str(p.get("name")): p for p in _fp._family_params(fam)} \
        if fam else {}
    # Условия исключения семьи ячейка посадки несёт наравне с
    # параметрами (их единица и отношение живут в перечне условий
    # каталога, а не в словаре PARAMS) — носитель у них такой же
    # двойной, и прятать расхождение за именем условия нельзя.
    for cname, cond in ((_catalog_bridge.conditions(fam) or {}).items()
                        if fam else ()):
        specs.setdefault(str(cname), cond)
    names = _cat("PARAMS")
    out: list[dict] = []
    for name, cell in cells.items():
        name = str(name)
        same = [p for p in legacy if p.get("name") == name]
        if not same or not isinstance(cell, dict):
            continue
        spec = specs.get(name) or {}
        unit = str(spec.get("unit") or names.get(name) or "")
        if not unit:
            continue        # имя не из записи семьи — судит валидатор
        cell_val, cell_bad = _fp.canonical_value(
            cell.get("value"), cell.get("unit"), unit)
        for p in same:
            leg_val, leg_bad = _fp.canonical_value(
                p.get("value"), p.get("unit"), unit)
            head = f"{rid}: параметр {name} — два носителя"
            # Несколько записей одного имени: слова называют, О КАКОЙ
            # записи речь (находка Н-1 ревью — при уточнителях «база» и
            # «вне базы» адрес находки был неразличим).
            if len(same) > 1 and p.get("qualifier"):
                head += f" (запись params[] с уточнителем {p['qualifier']!r})"
            # Одна и та же запись у обоих носителей (значение и единица
            # буквально совпали, причём ОТСУТСТВИЕ единицы равно
            # канонической единице записи, В-4) — расхождения нет и
            # разбирать нечего: так сверяются значения, которые числом
            # не читаются (массив или объект обозначений). Форму такой
            # записи судит проверка посадки, а не этот барьер.
            twins = _identical_raw(cell.get("value"), p.get("value")) \
                and _carrier_unit_text(cell.get("unit"), unit) \
                == _carrier_unit_text(p.get("unit"), unit)
            # Структурные записи (объект/перечень) — свой закон: они
            # грамматике числа не показываются (В-1).
            struct = None if twins else _structural_verdict(
                cell, p, unit, base_qualified=(unit == "count" and spec.get("qualifier") == "base_code"))
            if struct is None and (cell_bad or leg_bad) and not twins:
                out.append({"rule": rid, "param": name, "words":
                            f"{head}, сверка невозможна: "
                            f"{'посадка: ' + cell_bad if cell_bad else 'params[]: ' + leg_bad}"})
                continue
            diffs: list[str] = []
            if unit == "time_of_day_minutes" and "tz" in cell \
                    and cell.get("tz") != p.get("tz"):
                diffs.append(f"tz: посадка {cell.get('tz')!r} против params[] {p.get('tz')!r}")
            if struct:
                diffs.append(struct)
            elif struct is None and cell_val != leg_val:
                if unit == _fp.FLAG_UNIT:
                    diffs.append(f"значение: посадка {_num_words(cell_val)} "
                                 f"против params[] {_num_words(leg_val)}")
                else:
                    diffs.append(
                        "значение: посадка "
                        + _carrier_words(cell.get("value"),
                                         cell.get("unit"), unit)
                        + f" = {_num_words(cell_val)} {unit} против params[] "
                        + _carrier_words(p.get("value"), p.get("unit"), unit)
                        + f" = {_num_words(leg_val)} {unit}")
            # `relation: null` — то же, что ключа нет: по контракту
            # (§3.1) ячейка несёт отношение записи семьи, и пустое поле
            # его не отменяет, а наследует (ровно это делает канон
            # посадки). Иначе одним `null` в ячейке сверку отношений
            # можно было бы выключить.
            rel_cell = cell.get("relation")
            if rel_cell is None:
                rel_cell = spec.get("relation")
            rel_leg = p.get("relation")
            # Only absence/null is unspecified. False-like explicit values
            # must participate in comparison before projection can overwrite
            # a legacy scalar or expand its map into qualified records.
            if (rel_cell is not None and rel_leg is not None
                    and (type(rel_cell) is not type(rel_leg) or rel_cell != rel_leg)):
                diffs.append(f"отношение: посадка {rel_cell!r} против "
                             f"params[] {rel_leg!r}")
            if "condition" in cell:
                c_cell = _canon_condition(cell.get("condition"))
                c_leg = _canon_condition(p.get("qualifier"))
                if (fit.get("schema_version") == "landing/2"
                        and unit == "count" and spec.get("qualifier") == "base_code"
                        and isinstance(cell.get("value"), dict)
                        and cell["condition"] is None
                        and isinstance(p.get("qualifier"), str)
                        and p["qualifier"] in cell["value"]):
                    # A typed keyed map already scopes each value to its
                    # base. Null adds no OTHER restriction; it does not
                    # turn that map into a global scalar. Value and key
                    # agreement still pass through _structural_verdict.
                    c_cell = _canon_condition({"kind": "base_code", "value": p["qualifier"]})
                both_structured = isinstance(cell.get("condition"), dict) \
                    and isinstance(p.get("qualifier"), dict)
                same_cond = (c_cell == c_leg) if both_structured else (
                    (c_cell[1] if c_cell else None)
                    == (c_leg[1] if c_leg else None))
                if not same_cond:
                    shown_cell = str(cell.get("condition")) if c_cell \
                        else "вся область"
                    shown_leg = str(p.get("qualifier")) if c_leg \
                        else "вся область"
                    diffs.append(f"условие: посадка {shown_cell} против "
                                 f"params[] {shown_leg}")
            if diffs:
                out.append({"rule": rid, "param": name,
                            "words": f"{head} расходятся — "
                                     + "; ".join(diffs)})
    # Две БУКВАЛЬНО одинаковые записи params[] — одна беда, а не две:
    # перечень `items` рубежа выпуска не двоится (находка Н-1 ревью).
    seen: set = set()
    uniq: list[dict] = []
    for d in out:
        key = (d["rule"], d["param"], d["words"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq


def reconcile_carriers(rule: dict) -> list[str]:
    """Сверка носителей параметра строки словами (A05-барьер): пусто —
    носители согласны либо имя несёт только один из них; иначе — по
    строке проблемы на каждое расхождение (правило, параметр, оба
    значения с единицами). Тот же расчёт зовут рубеж выпуска
    (`export_task_package`, отказ `param-carriers-diverge`) и гейт
    производителя (`gate_task_package`)."""
    return [d["words"] for d in carrier_divergences(rule)]


def carrier_problems(doc: dict) -> list[dict]:
    """Расхождения носителей по всему своду: [{rule, param, words}]."""
    out: list[dict] = []
    for r in doc.get("rules", []) or []:
        if isinstance(r, dict):
            out.extend(carrier_divergences(r))
    return out


def carrier_grounds(doc: dict) -> list[dict]:
    """Расхождение носителей параметра как ОСНОВАНИЕ ГОТОВНОСТИ (A05,
    находка Н-4 неавтор-ревью 12.09.2026): у правила две правды об одном
    параметре — исполнять его нечем, и готовность затронутых задач
    обязана быть ниже «в» ЕЩЁ ДО рубежа выпуска. Прежде рубеж и гейт
    отказывали, а экран показывал «в»: владелец узнавал о беде, только
    нажав «Выпустить».

    Запись — та же, что у барьеров K01/K06 ({code, words, rules, zones,
    hard}), и читают её те же `readiness` / `readiness_matrix` через
    `barrier_tasks`: жёсткий носитель держит выполнимость задачи,
    мягкий — режимы со штрафами."""
    rules_by_id = {str(r.get("id")): r for r in doc.get("rules", []) or []
                   if isinstance(r, dict)}
    out: list[dict] = []
    for d in carrier_problems(doc):
        r = rules_by_id.get(str(d["rule"])) or {}
        fit = r.get("template_fit") if isinstance(r, dict) else None
        out.append({
            "code": PARAM_CARRIERS_DIVERGE,
            "family": (fit or {}).get("family") if isinstance(fit, dict)
            else None,
            "param": d["param"], "words": d["words"],
            "rules": [str(d["rule"])],
            "zones": [str(r.get("zone") or "BOTH")],
            "hard": r.get("kind") != "SOFT"})
    return out


def rule_param_cells(rule: dict) -> dict:
    """Все машинные параметры строки одним словарём {имя: значение}:
    параметры посадки A/B (template_fit.params, значение числом — как
    в fit_signature) и старые params. Единая точка для вопросов тем,
    конфликтов параметров и внесения ответов: «параметр уже несёт
    строка» судится по обоим носителям (П6, 2026-09-02).

    Посадка первее; но если имя несут ОБА носителя и они расходятся
    (`reconcile_carriers`), значением становится `CarrierDivergence` со
    словами проблемы, а не первое из двух (A05-барьер, контракт П0 §3.1
    закон 3): читатели видят «имя несёт строка» и не плодят третьего
    носителя, а расхождение ловят рубеж выпуска и гейт."""
    from cdpm import family_params as _fp
    out: dict = {}
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if isinstance(fit, dict) and fit.get("category") in ("A", "B") \
            and isinstance(fit.get("params"), dict):
        # Типоустойчиво (дисциплина `validate_param`): `params` посадки
        # СПИСКОМ — нарушение формы, и судит его проверка посадки;
        # читатель носителей обязан не падать TypeError — его зовут и
        # самолечения сборки, и анкета.
        for name, cell in fit["params"].items():
            raw = cell.get("value") if isinstance(cell, dict) else cell
            # Структурная запись (объект нормы по базам, перечень
            # обозначений) числом не читается и грамматике не
            # показывается — она остаётся собой (В-1).
            num = None if _fp.is_structural(raw) else _fit_value_number(raw)
            if num is None and raw is not None \
                    and not _fp.is_structural(raw):
                num = canon_minutes(str(raw))
            if isinstance(num, float) and num.is_integer():
                num = int(num)
            out[str(name)] = num if num is not None else raw
    overlap = False
    for p in (rule.get("params") or []) if isinstance(rule, dict) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            if p["name"] in out:
                overlap = True
            out.setdefault(p["name"], p.get("value"))
    if overlap:
        for d in carrier_divergences(rule):
            out[d["param"]] = CarrierDivergence(d["words"])
    return out


# РВ-113 (03.10.2026; Stage run-0024, вопрос второго чтения
# S1-абз-11): два имени одного смысла у семьи каталога — свод в ОДНОМ
# месте. Каталожное поле эквивалентности (same_as) — сторона каталога;
# до него — эта таблица, и проба держит, что обе стороны каждой пары
# есть в каталоге. Догадок по окончанию имени в коде нет.
PARAM_SAME_AS: dict[tuple[str, str], str] = {
    ("fleet_exclusions", "fleet_type"): "fleet_types",
}

_CODE_SEP_RE = re.compile(r"[,;/]")


def canon_param_name(fam, name) -> str:
    """Каноническое имя параметра посадки семьи (PARAM_SAME_AS)."""
    return PARAM_SAME_AS.get((str(fam), str(name)), str(name))


def canon_fit_cell(fam, name, raw) -> tuple[str, str]:
    """Канон ячейки посадки для суда чтений (РВ-113, Р9 — один канон
    всех мест сравнения): (каноническое имя, значение строкой). Одно
    значение в разной форме — одна строка: перечень кодов списком или
    строкой, порядок, повторы, регистр и пробелы кода; скаляр кода и
    перечень из одного; число строкой и числом; «12:00» и 720. Единица —
    из каталога по каноническому имени (fit_param_unit); без каталога —
    прежнее чтение (число либо сырое значение)."""
    cname = canon_param_name(fam, name)
    try:
        unit = fit_param_unit(fam, cname)
    except Exception:                       # без каталога — прежний путь
        unit = None
    if unit in _cat("CODE_UNITS") and raw is not None \
            and not isinstance(raw, (dict, bool)):
        items = (list(raw) if isinstance(raw, (list, tuple, set, frozenset))
                 else _CODE_SEP_RE.split(str(raw)))
        norm = sorted({" ".join(str(x).split()).casefold()
                       for x in items if str(x).strip()})
        return cname, "коды: " + ", ".join(norm)
    num = _fit_value_number(raw)
    if num is None and raw is not None:
        num = canon_minutes(str(raw))      # «12:00» ≡ 720
    if isinstance(num, float) and num.is_integer():
        num = int(num)
    return cname, str(num if num is not None else raw)


def fit_signature(rule: dict):
    """Подпись посадки A/B для суда дублей: (семья, кортеж (имя,
    значение числом) параметров); у беспараметрной семьи — (семья, ()).
    Посадки C/D и отсутствие посадки — None (дубль не судится)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return None
    fam = fit.get("family")
    if not fam:
        return None
    vals = []
    # Типоустойчиво, как `rule_param_cells` (Н-4 неавтор-ревью
    # 12.09.2026): `params` посадки СПИСКОМ — нарушение формы, и судит
    # его проверка посадки; читатель подписи обязан не падать
    # AttributeError (его зовут суд дублей и самолечения сборки —
    # падение здесь роняло бы весь прогон вместо отказа словами).
    cells = fit.get("params")
    # РВ-113: канон имени и значения (canon_fit_cell) — одно значение в
    # разной форме больше не «два чтения». Две ячейки одной ручки
    # (fleet_type и fleet_types) с ОДНИМ значением — одна запись; с
    # РАЗНЫМИ — конфликт: имена остаются как написаны, обе записи живут в
    # подписи и ни одна не поглощает другую (ревью GPT 04.10 00:20).
    canon: list[tuple[str, str, str]] = []
    for name, cell in (cells.items() if isinstance(cells, dict) else ()):
        raw = cell.get("value") if isinstance(cell, dict) else cell
        cname, cval = canon_fit_cell(fam, name, raw)
        canon.append((str(name), cname, cval))
    by_name: dict[str, set] = {}
    for _n, cname, cval in canon:
        by_name.setdefault(cname, set()).add(cval)
    for name, cname, cval in canon:
        if len(by_name[cname]) > 1:
            vals.append((name, cval))          # конфликт — имя как написано
        elif (cname, cval) not in vals:
            vals.append((cname, cval))
    if not vals:
        # Беспараметрная семья (К-1: носитель нормы — цитата): две
        # разные нормы одной клаузы (ночёвки в выходные / в праздники)
        # различаются цитатой, а не параметрами (ревью r54).
        # Знаки препинания по краям цитаты — не различитель (проход №13
        # H0: «… за месяц.» против «… за месяц» разошлись как спор).
        vals.append(("quote", _fit_norm_text(fit.get("quote")).strip(" .,;:!?«»\"'()")))
    return (str(fam), tuple(sorted(vals)))


def _primary_ref(item: dict):
    for s in (item.get("sources") or []):
        if isinstance(s, dict):
            return (s.get("sid"), s.get("anchor"))
    return None


def find_duplicate_rule(doc: dict, rule: dict):
    """Дубль строки: другая строка свода с той же первичной клаузой
    (sid, якорь) и той же подписью посадки. Возвращает её id либо None.
    Две посадки одной клаузы РАЗНЫХ семей (предел и продление) —
    законны."""
    sig = fit_signature(rule)
    ref = _primary_ref(rule)
    if sig is None or ref is None:
        return None
    for other in doc.get("rules", []):
        if not isinstance(other, dict) or other.get("id") == rule.get("id"):
            continue
        if other.get("kind") not in ("HARD", "SOFT"):
            continue
        if _primary_ref(other) == ref and fit_signature(other) == sig:
            return other.get("id")
    return None


def alien_anchors(doc: dict, rule: dict, prev: dict | None) -> list:
    """Чужие якоря заплаты: источники, ДОБАВЛЕННЫЕ строке заплатой,
    которые уже являются первичной клаузой другой строки или целевой
    свода. Возвращает [(якорь, id владельца), …]. Живой корень: цикл
    приписал правилу ночёвок выходного дня вторую клаузу, и его вес лёг
    ещё и на чужую целевую (гейт H0 run-0007)."""
    prev_refs = {(s.get("sid"), s.get("anchor"))
                 for s in ((prev or {}).get("sources") or [])
                 if isinstance(s, dict)}
    added = [(s.get("sid"), s.get("anchor"))
             for s in (rule.get("sources") or [])
             if isinstance(s, dict)
             and (s.get("sid"), s.get("anchor")) not in prev_refs]
    if not added:
        return []
    owners: dict = {}
    for coll in ("rules", "objectives"):
        for item in doc.get(coll, []):
            if not isinstance(item, dict) \
                    or item.get("id") == rule.get("id"):
                continue
            ref = _primary_ref(item)
            if ref is not None:
                owners.setdefault(ref, item.get("id"))
    return [(ref[1], owners[ref]) for ref in added if ref in owners]


def _fit_norm_text(s) -> str:
    return " ".join(str(s or "").split()).casefold()


_ELLIPSIS_RE = re.compile(r"\.{3}|…")


def quote_in_text(quote, text) -> bool:
    """Дословность цитаты: цитата — подстрока текста (нормализация
    пробелов и регистра) ЛИБО несколько фрагментов через многоточие,
    каждый из которых — подстрока текста, в порядке текста (план «прямой
    ход» 2.3: фрагменты цитаты; прогон №5 H0: критерий O-PAIR был
    отвергнут целиком из-за «...» между двумя дословными фрагментами).
    Фрагмент короче трёх знаков цитатой не считается."""
    q, t = _fit_norm_text(quote), _fit_norm_text(text)
    if not q or not t:
        return False
    if q in t:
        return True
    parts = [p.strip() for p in _ELLIPSIS_RE.split(str(quote or "")) if p.strip()]
    if len(parts) < 2:
        return False
    pos = 0
    for p in parts:
        pn = _fit_norm_text(p)
        if len(pn) < 3:
            return False
        i = t.find(pn, pos)
        if i < 0:
            return False
        pos = i + len(pn)
    return True


def value_quote_in_text(quote, value, text) -> bool:
    """Цитата величины, дословная ОТ ВЕЛИЧИНЫ И ДО КОНЦА цитаты: она
    содержит число `value`, и фрагмент от числа до конца цитаты —
    дословная подстрока текста. Слова ДО числа вправе стоять в ином
    падеже («штраф» против «штрафом» — проход №8 H0: вес 15 отброшен
    из-за падежа вне величины, три попытки ремонта — тоже); сама
    величина, её единица и всё, что цитата говорит после неё, — только
    дословно. Голое число без слова рядом доказательством не считается.

    Хвост судится целиком (было — три слова вперёд) по живой находке
    кругов 10.09.2026 (run-0143, run-0035 на Test): модель СКЛЕИВАЛА в
    `as_written` две фразы клаузы, первые слова после числа сходились,
    и склейка проходила приём — а гейт выпуска панели честно отказывал
    измерителем, у которого цитата-носитель обязана быть подстрокой
    клаузы целиком. Цитата из НЕСКОЛЬКИХ мест одной клаузы законна, но
    названа ею и быть: дословность здесь судит ЗАКОН НОСИТЕЛЯ выпуска
    (`measure.quote_verbatim` — подстрока либо фрагменты через
    многоточие, запасным швом «;», в порядке текста), чтобы приём
    извлечения не был строже гейта выпуска и не мягче него."""
    if value is None or isinstance(value, bool):
        return quote_in_text(quote, text)
    sval = str(value)
    if isinstance(value, float) and value.is_integer():
        sval = str(int(value))
    toks = str(quote or "").split()
    idx = next((i for i, tk in enumerate(toks)
                if re.fullmatch(r"\W*" + re.escape(sval) + r"\W*", tk)), None)
    if idx is None:
        return False                 # цитата величины обязана нести саму величину
    # Дословность судит ЗАКОН НОСИТЕЛЯ выпуска (`measure.quote_verbatim`
    # — подстрока либо названные фрагменты в порядке текста): приём не
    # вправе быть строже гейта, который эту же цитату будет судить при
    # выпуске пакета.
    from cdpm import measure as _ms
    if _ms.quote_verbatim(quote, text):
        return True
    frag = " ".join(toks[idx:])
    if len(_fit_norm_text(frag)) < len(sval) + 3:
        return False
    # Хвост от величины — тем же законом: склейка двух фраз клаузы (без
    # многоточия и без «;») дословной цитатой не считается.
    return _ms.quote_verbatim(frag, text)


def validate_fit_quotes(doc: dict, clause_text_by_key: dict) -> list:
    """Цитатный слой посадки (анти-самоподтверждение): quote каждого
    параметра — дословная подстрока клаузы-ИСТОЧНИКА правила
    (нормализация пробелов/регистра), не текста правила. Вызывается
    там, где тексты клауз есть: извлечение и цикл. clause_text_by_key:
    {(sid, anchor): текст клаузы}.

    Значение, внесённое ОТВЕТОМ, судится не клаузой, а точной версией
    ответа (контракт П0 §2.3, A03-мин/K05-мин): цитата обязана быть
    дословной подстрокой `raw` ИМЕННО той версии, на которую ссылается
    ячейка, а значение — читаться из этой цитаты грамматикой K02.

    ТРЕБУЕТСЯ ПОЛНЫЙ ДОКУМЕНТ. Вызов со срезом строк (без ключа
    `questions`), несущим ячейку с происхождением из ответа, — ошибка
    контракта (`ValueError`), а не молчаливое доверие признаку:
    отсутствие анкеты в срезе — причина, по которой выдуманное
    значение получало фиктивное основание в ответе (находка A03)."""
    from cdpm import provenance as _prov
    if "questions" not in doc:
        bad = [f"{rid}:{name}" for rid, _fam, name, _cell
               in _prov.answer_cells(doc)]
        if bad:
            raise ValueError(
                "validate_fit_quotes вызван срезом без анкеты (ключа "
                "questions), а посадка несёт значения из ответов "
                f"({', '.join(bad[:5])}): происхождение значения судится "
                "только по полному документу модели: вызовите с "
                '{"questions": doc["questions"], "rules": [строка]} '
                "(контракт П0 §2.3 п. 2)")
    problems: list[str] = []
    for r in doc.get("rules", []):
        fit = r.get("template_fit")
        top_quote = str((fit or {}).get("quote") or "").strip() \
            if isinstance(fit, dict) else ""
        if not isinstance(fit, dict) \
                or (not isinstance(fit.get("params"), dict)
                    and not top_quote):
            continue
        rid = r.get("id")
        texts = [_fit_norm_text(clause_text_by_key.get(
            (s.get("sid"), s.get("anchor"))))
            for s in (r.get("sources") or [])
            if isinstance(s, dict)]
        texts = [t for t in texts if t]
        if not texts:
            problems.append(f"{rid}: цитаты посадки нечем судить — "
                            f"клаузы источников недоступны")
            continue
        # К-1: цитата-носитель беспараметрной посадки судится той же
        # дословностью, что и цитаты параметров.
        if top_quote and not any(_fit_norm_text(top_quote) in t
                                 for t in texts):
            problems.append(
                f"{rid}: цитата-носитель посадки не является "
                f"дословной подстрокой клауз источников")
        for name, cell in (fit.get("params") or {}).items():
            if not isinstance(cell, dict):
                problems.append(f"{rid}: запись параметра {name!r} "
                                f"не объект")
                continue
            q = _fit_norm_text(cell.get("quote"))
            if _prov.is_answer_cell(cell):
                # Точное происхождение (A03-мин/K05-мин, 12.09.2026):
                # источник значения — ТОЧНАЯ ВЕРСИЯ ответа, не клауза и
                # не «вопрос вообще». Отсутствующий вопрос и разошедшийся
                # отпечаток — нарушение, не доверие признаку; значение,
                # не читающееся из цитаты, — фиктивное основание.
                problems += _prov.cell_problems(
                    doc, rid, fit.get("family"), name, cell)
                continue
            if not q or not any(q in t for t in texts):
                problems.append(
                    f"{rid}: цитата параметра {name!r} не является "
                    f"дословной подстрокой клауз источников")
    # Цитатный слой на все машинные поля (RAZDELENIE раздел 3 п.2):
    # цитата веса мягкой строки — дословна в окрестности числа и из
    # клауз источников (или из ответа авиакомпании, когда вес внесён
    # ответом: тогда клаузы её не несут, судит путь ответов).
    for r in doc.get("rules", []):
        wq = r.get("weight_quote")
        if not wq or r.get("kind") != "SOFT" or r.get("weight") is None:
            continue
        rid = r.get("id")
        texts = [str(clause_text_by_key.get((s.get("sid"), s.get("anchor"))) or "")
                 for s in (r.get("sources") or []) if isinstance(s, dict)]
        joined = " ".join(t for t in texts if t)
        if not joined:
            continue
        if not value_quote_in_text(wq, r.get("weight"), joined) \
                and not any(str(rid) in str(q.get("rule_refs") or [])
                            and (q.get("answer") or {}).get("status") == "применён"
                            for q in doc.get("questions", []) or []):
            problems.append(f"{rid}: цитата веса {str(wq)[:40]!r} не из клауз "
                            f"источников и не из применённого ответа")
    return problems


# ── Ревизия 1.5 (ступень 3, дизайн утверждён владельцем
# 2026-08-23): блок logic — машинное представление правила
# произвольного вида НАД params. Три закрытых словаря первой волны
# согласованы обеими сессиями (02-SLOVARI-PERVOJ-VOLNY, поправки
# COSMOS: классы exists, единица подряд/окна явная). Расширение —
# ревизией спецификации, как PARAMS.
LOGIC_SUBJECTS = ("assignment", "flight", "duty", "reserve_duty",
                  "rest", "sit", "employee", "base", "day", "month",
                  "rolling_window", "day_off", "qualification")
# Предикат → обязательные аргументы (помимо следа as_written).
LOGIC_PREDICATES: dict = {
    "starts_in": ("from_param", "to_param"),
    "longer_than": ("param",),
    "shorter_than": ("param",),
    "at_base": (),
    "at_outstation": (),
    "consecutive": ("unit", "param"),
    "in_window": ("window",),
    "follows": ("subject",),
    "has_qualification_for": ("type_value",),
    "overlaps_in_time": (),
    "marked_unavailable": (),
    "in_list": ("allowed",),
}
# Требование → обязательные аргументы. exists распался на два
# класса (поправка COSMOS): внутри одного человека (дёшево) и по
# клеткам универсума (мера клеток обязательна в пакете).
# Ревизия 1.6 (7 пунктов согласованы с COSMOS 2026-08-23):
# measure у длительности БЕЗ умолчания (налёт размахом — тот
# же класс, что «голое 12»); счёт по клеткам универсума —
# необязательный universe у count_within.
LOGIC_DEMANDS: dict = {
    "count_within": ("relation", "param"),
    "duration_within": ("relation", "param", "measure"),
    "exists_person": ("subject",),
    "exists_universe": ("subject", "universe"),
    "not_exists": ("subject",),
    "combination_forbidden": ("subject",),
    # Мягкие формы волны 1 (П-2 этап 2, word-в-word с решателем
    # 2026-08-25): demand.kind = словарное имя шаблона, числа —
    # ТОЛЬКО ссылками на params правила (роли *_param, стиль
    # count_within/starts_in); литерал исполнителя 1 за единицу
    # счёта, вес компоненты едет весами пакета.
    "штраф-ступенчатый-за-стыковку": ("threshold_param",
                                      "step_param"),
    "штраф-за-разрыв-смены": ("threshold_param",),
    "однородность-цепочки-ночь-день": ("window_from_param",
                                       "window_to_param"),
    "мягкий-предел-дней-подряд": ("bound_param",),
    "неудовлетворённая-выполнимая-заявка": (),
}
# Имена мягких форм волны (порождаются produce_logic ТОЛЬКО для
# SOFT-правил; включатель решателя ведёт их словарём TEMPLATE_TO_FORM
# по паре (rule_id, kind)).
LOGIC_SOFT_KINDS = ("штраф-ступенчатый-за-стыковку",
                    "штраф-за-разрыв-смены",
                    "однородность-цепочки-ночь-день",
                    "мягкий-предел-дней-подряд",
                    "неудовлетворённая-выполнимая-заявка")
LOGIC_UNITS = ("day", "duty")      # «подряд»/окно: по чему считаем
LOGIC_MEASURES = ("span", "block")   # размах смены | налёт
# Отношение ТРЕБОВАНИЯ уже параметровых: window у demand
# незаконна (окно живёт условием/узлом, не отношением).
LOGIC_DEMAND_RELATIONS = ("max", "min", "eq")
LOGIC_UNIVERSES = ("employee×month", "employee×day",
                   "base×day", "employee×rolling_window")
LOGIC_ENFORCE = ("required", "post-check")


#: Доказанные эквивалентности «семья реестра + ручки ↔ форма блока logic»
#: (ревью GPT-036/037/038, 06.09.2026): сведение logic → post-check допустимо
#: ТОЛЬКО для пар из этой таблицы — там второе описание того же правила
#: доказуемо избыточно (ручка исполняет ровно это требование). Форма =
#: (subject, предикаты условия, вид требования). Расширение таблицы —
#: только с доказательством (тест эквивалентности у исполнителя), не по
#: наличию посадки: метка A — утверждение модели, не машинное
#: доказательство совпадения формы с исполнителем.
LOGIC_FIT_EQUIVALENT: dict[tuple[str, frozenset], dict] = {
    # допуск по типу ВС: ручка fleet_types исключает суда, на которые нет
    # действующего допуска, — то же, что «рейс выполняет пилот с допуском
    # на этот тип» (run-0086 R-S1-абз-11; Type rating — штатный исполнитель).
    # Аргументы (ревью GPT-043): значения ручки fleet_types обязаны совпадать
    # с type_value условий has_qualification_for один к одному — TYPE-A у
    # ручки и TYPE-B в условии (или лишнее условие) эквивалентностью не
    # являются, required остаётся.
    ("fleet_exclusions", frozenset({"fleet_types"})): {
        "forms": {("assignment", frozenset({"has_qualification_for"}),
                   "exists_person", "employee")},
        "args": {"fleet_types": ("has_qualification_for", "type_value")},
    },
}


def _knob_values(raw) -> frozenset:
    """Значения ручки как множество нормализованных строк: список, либо
    строка с разделителями «,» / «;»; пусто — frozenset()."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return frozenset()
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = str(raw).replace(";", ",").split(",")
    return frozenset(str(x).strip().casefold() for x in items if str(x).strip())


def _logic_form(logic: dict) -> tuple | None:
    """(subject, предикаты условия, вид требования, предмет требования)
    блока logic; None — форма не читается (судит валидатор). Предмет
    требования (demand.subject) — часть формы (ревью GPT-045): требование
    «существует сотрудник» и «существует смена» — разные требования."""
    if not isinstance(logic, dict):
        return None
    conds = logic.get("condition")
    demand = logic.get("demand")
    if not isinstance(conds, list) or not isinstance(demand, dict):
        return None
    preds = frozenset(str(c.get("predicate")) for c in conds
                      if isinstance(c, dict))
    return (str(logic.get("subject")), preds, str(demand.get("kind")),
            str(demand.get("subject")))


def _logic_covered_by_fit(rule: dict) -> bool:
    """Второе описание правила ДОКАЗУЕМО избыточно: ЖЁСТКОЕ правило с
    ТОЧНОЙ посадкой A, ручки посадки и форма logic — пара из таблицы
    LOGIC_FIT_EQUIVALENT. Иначе (посадка B, мягкое правило, ручки без
    доказанной эквивалентности, иная форма) — не трогаем: решатель судит
    блок logic как прежде, отказ при несовпадении остаётся честным."""
    if rule.get("kind") != "HARD":
        return False
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") != "A":
        return False
    params = fit.get("params")
    if not isinstance(params, dict) or not params:
        return False
    # РВ-113: ручки — каноническими именами (PARAM_SAME_AS): fleet_type
    # и fleet_types — одна ручка, ключ таблицы не промахивается формой.
    # Две ячейки одной ручки с РАЗНЫМИ значениями — конфликт, а не
    # перезапись: эквивалентность не доказана (ревью GPT 04.10 00:20).
    fam = fit.get("family")
    canon_params: dict = {}
    for k, v in params.items():
        cname = canon_param_name(fam, k)
        if cname in canon_params:
            raw_new = v.get("value") if isinstance(v, dict) else v
            old_v = canon_params[cname]
            raw_old = old_v.get("value") if isinstance(old_v, dict) else old_v
            if canon_fit_cell(fam, cname, raw_new) != \
                    canon_fit_cell(fam, cname, raw_old):
                return False
            continue
        canon_params[cname] = v
    params = canon_params
    entry = LOGIC_FIT_EQUIVALENT.get((str(fit.get("family")),
                                      frozenset(str(k) for k in params)))
    if not entry:
        return False
    logic = rule.get("logic")
    if _logic_form(logic) not in entry["forms"]:
        return False
    # Аргументы (GPT-043): для каждой ручки — ровно те же значения в
    # условиях logic названного предиката, по одному условию на значение,
    # и никаких условий сверх этого.
    conds = [c for c in logic.get("condition") or [] if isinstance(c, dict)]
    bound = 0
    for knob, (predicate, arg) in entry["args"].items():
        want = _knob_values(params.get(knob))
        atoms = [c for c in conds if str(c.get("predicate")) == predicate]
        got = [str(c.get(arg)).strip().casefold() for c in atoms
               if c.get(arg) not in (None, "")]
        if not want or len(atoms) != len(want) or len(got) != len(atoms) \
                or frozenset(got) != want:
            return False
        bound += len(atoms)
    return bound == len(conds)


_LOGIC_FIT_NOTE = "блок logic сведён к post-check: правило исполняет ручка шаблона"


def logic_fit_conflicts(doc: dict) -> list[str]:
    """Жёсткие правила, у которых блок logic с enforce=required (или без
    enforce — умолчание решателя required) стоит рядом с точной посадкой
    A на шаблон, чья ручка ДОКАЗАННО исполняет эту же форму (таблица
    LOGIC_FIT_EQUIVALENT): два исполнителя одного правила — двойной счёт
    нарушения, решатель такой пакет отвергает (LogicCompiler, 06.09.2026,
    run-0086 R-S1-абз-11). Пары вне таблицы здесь не судятся — их судит
    решатель. Возвращает нарушения словами (пусто = чисто)."""
    out: list[str] = []
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict) or not isinstance(r.get("logic"), dict):
            continue
        if not _logic_covered_by_fit(r):
            continue
        if r["logic"].get("enforce", "required") == "required":
            fit = r["template_fit"]
            out.append(f"{r.get('id')}: logic с enforce=required рядом с "
                       f"точной посадкой A/{fit.get('family')} "
                       f"(ручки {', '.join(sorted(fit['params']))}, "
                       f"эквивалентность доказана) — второй исполнитель "
                       f"одного правила; допустим только post-check")
    return out


def reconcile_logic_with_template_fit(doc: dict) -> list[str]:
    """Нормализация (детерминирована, повторный вызов ничего не меняет):
    у ЖЁСТКОГО правила с точной посадкой A, чьи ручки ДОКАЗАННО исполняют
    форму блока logic (таблица LOGIC_FIT_EQUIVALENT), блок сводится к
    enforce=post-check — исполнитель один (ручка), логика остаётся
    сведением для сертификата; сведение записывается в note правила
    (видно в пакете и сертификате). Всё вне таблицы (посадка B, мягкие
    правила, ручки без доказанной эквивалентности, иная форма) не
    трогается — решатель судит как прежде. Возвращает список
    исправленных правил."""
    healed: list[str] = []
    for r in doc.get("rules", []) or []:
        if not isinstance(r, dict) or not isinstance(r.get("logic"), dict):
            continue
        if not _logic_covered_by_fit(r):
            continue
        if r["logic"].get("enforce", "required") == "required":
            r["logic"]["enforce"] = "post-check"
            fit = r["template_fit"]
            knobs = ", ".join(sorted(fit["params"]))
            extra = (f"{_LOGIC_FIT_NOTE} {fit.get('family')} ({knobs}), посадка A, "
                     f"эквивалентность формы и аргументов доказана "
                     f"(LOGIC_FIT_EQUIVALENT)")
            note = r.get("note") or ""
            if _LOGIC_FIT_NOTE not in note:
                r["note"] = (note + "; " + extra).strip("; ")
            healed.append(f"{r.get('id')}: logic.enforce required → post-check "
                          f"(исполнитель — ручка шаблона {fit.get('family')}: "
                          f"{knobs}, посадка A)")
    return healed


def _logic_param_names(logic) -> set[str]:
    """Actual atoms AND declarations define the projection, not declarations alone."""
    names: set[str] = set()

    def ref(value):
        name = value.get("name") if isinstance(value, dict) else value
        if isinstance(name, str):
            names.add(name)

    def atom(node):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(key, str) and key.endswith("param"):
                ref(value)
        window = node.get("window")
        if isinstance(window, dict):
            ref(window.get("param"))

    if isinstance(logic, dict):
        atom(logic)
        atom(logic.get("demand"))
        conditions = logic.get("condition")
        for node in conditions if isinstance(conditions, list) else []:
            atom(node)
        declared = logic.get("source_params")
        for value in declared if isinstance(declared, list) else []:
            ref(value)
    return names


def logic_param_records(rule: dict, *, selected_names: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """Один совместимый читатель для ссылок, пересказа и доказательства.

    Проекция только в памяти: исходные params/посадка не переписываются.
    Нет догадки и отката на legacy при ошибке посадки или расхождении.
    """
    from cdpm import param_projection as projection
    try:
        return projection.derive_params_from_landing(rule, selected_names=selected_names), []
    except projection.ParamProjectionError as exc:
        return [], [str(exc)]


# CDPM-292: types belong to semantic roles, not merely parameter names.
# The catalog owns name -> unit; these tables own what each logic role reads.
_LOGIC_ROLE_UNITS = {
    "condition": {
        "starts_in": {"from_param": "time_of_day_minutes", "to_param": "time_of_day_minutes"},
        "longer_than": {"param": "duration_minutes"},
        "shorter_than": {"param": "duration_minutes"},
        "consecutive": {"param": "count"},
    },
    "demand": {
        "duration_within": {"param": "duration_minutes"},
        "count_within": {"param": "count"},
        "exists_person": {"param": "count"},
        "exists_universe": {"param": "count"},
        "not_exists": {"param": "count"},
        "штраф-ступенчатый-за-стыковку": {
            "threshold_param": "duration_minutes", "step_param": "duration_minutes"},
        "штраф-за-разрыв-смены": {"threshold_param": "duration_minutes"},
        "однородность-цепочки-ночь-день": {
            "window_from_param": "time_of_day_minutes", "window_to_param": "time_of_day_minutes"},
        "мягкий-предел-дней-подряд": {"bound_param": "count"},
    },
}


def _logic_param_unit_problems(logic, records) -> list[str]:
    """Check role types on original projected records; never convert units."""
    if not isinstance(logic, dict):
        return []
    out = []

    def check(ref, expected, where):
        name = ref.get("name") if isinstance(ref, dict) else ref
        if not isinstance(name, str):
            return  # reference shape/existence is checked by validate_logic
        for record in records:
            if not isinstance(record, dict) or record.get("name") != name:
                continue
            if isinstance(ref, dict) and record.get("qualifier") != ref.get("qualifier"):
                continue
            # A lightweight legacy logic fixture may omit unit. Its name has
            # an authoritative catalog type; explicit null/wrong units do not
            # fall back. Full document validation still requires the field.
            actual = record.get("unit", _cat("PARAMS").get(name))
            if actual != expected:
                out.append(f"{where}: параметр {name!r} имеет единицу "
                           f"{actual!r}, требуется {expected!r}; "
                           "время суток, длительность и счёт не взаимозаменяемы")

    def window(node, where):
        if isinstance(node, dict) and node.get("kind") == "rolling":
            check(node.get("param"), "count", where + ".param")

    window(logic.get("window"), "window")
    conditions = logic.get("condition")
    if not isinstance(conditions, list):
        conditions = []
    atoms = [("condition", node, f"condition[{i}]")
             for i, node in enumerate(conditions)]
    atoms.append(("demand", logic.get("demand"), "demand"))
    for group, node, where in atoms:
        if not isinstance(node, dict):
            continue
        kind = node.get("predicate" if group == "condition" else "kind")
        if not isinstance(kind, str):
            continue
        for arg, expected in _LOGIC_ROLE_UNITS[group].get(kind, {}).items():
            if arg in node:
                check(node[arg], expected, f"{where} ({kind}).{arg}")
        if group == "condition" and kind == "in_window":
            window(node.get("window"), where + ".window")
    return out


def logic_unit_problems(logic, rule) -> list[str]:
    """Shared unit barrier for direct consumers, including the proof compiler."""
    records, problems = logic_param_records(rule, selected_names=_logic_param_names(logic))
    return problems + _logic_param_unit_problems(logic, records)



def validate_logic(logic, rule) -> list[str]:
    """Нарушения блока logic одного правила (fail-closed,
    типоустойчиво — гейт судит недоверенный пакет). Контракт:
    имена из словарей; числа ТОЛЬКО через ссылки на существующие
    params правила; след as_written у каждого атома."""
    if not isinstance(logic, dict):
        return [f"logic не объект: {logic!r}"]
    out: list = []
    # Живой прогон H0 run-0005 (2.09): обработчик дал МЯГКОМУ правилу
    # logic с enforce=required — жёсткое исполнение штрафуемого
    # предпочтения; отчёт покрытия решателя клал такую логику в
    # «вне жёсткого исполнения», кнопка останавливалась «без
    # исполнителя» при живой посадке A. Мягкое правило исполняется
    # штрафом семьи, его логика — только post-check.
    # Исключение — волновые мягкие формы (LOGIC_SOFT_KINDS, волна П-2):
    # для них решатель порождает исполнителя, logic_gate пропускает
    # их по demand.kind; там enforce=required законен.
    _demand_kind = (logic.get("demand") or {}).get("kind") \
        if isinstance(logic.get("demand"), dict) else None
    if rule.get("kind") == "SOFT" \
            and logic.get("enforce") == "required" \
            and _demand_kind not in LOGIC_SOFT_KINDS:
        out.append("logic: у мягкого правила (SOFT) enforce=required "
                   "недопустим — мягкое правило исполняется штрафом "
                   "семьи, логика может быть только post-check (кроме "
                   "волновых мягких форм)")
    param_records, param_problems = logic_param_records(rule, selected_names=_logic_param_names(logic))
    out.extend(param_problems)
    out.extend(_logic_param_unit_problems(logic, param_records))
    entries: dict = {}
    for p in param_records:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            entries.setdefault(p["name"], []).append(
                p.get("qualifier"))

    used: set = set()

    def _param_ref(ref, where, track=True):
        # 1.6: строка-имя законна ТОЛЬКО при одной записи имени
        # в правиле; при нескольких (690/780 «без/с согласия»,
        # резерв по базам) — объект {name, qualifier}: сведение
        # «строжайшим» запрещено, области разные. track — учёт
        # применённых ссылок для сверки с source_params.
        if isinstance(ref, dict):
            nm, ql = ref.get("name"), ref.get("qualifier")
            if track and isinstance(nm, str):
                used.add(nm)
            quals = entries.get(nm)
            if quals is None:
                out.append(f"{where}: ссылка (name={nm!r}) — "
                           f"такого params у правила нет")
            elif ql not in quals:
                out.append(f"{where}: уточнитель {ql!r} не "
                           f"найден у параметра {nm!r} "
                           f"(есть: {sorted(map(str, quals))})")
            return
        if track and isinstance(ref, str):
            used.add(ref)
        if not isinstance(ref, str) or ref not in entries:
            out.append(f"{where}: ссылка на параметр {ref!r} — "
                       f"такого params у правила нет (числа только "
                       f"через source_params)")
        elif len(entries[ref]) > 1:
            out.append(f"{where}: имя {ref!r} несёт "
                       f"{len(entries[ref])} записи — ссылка "
                       f"обязана быть объектом (name, qualifier); "
                       f"уточнители: "
                       f"{sorted(map(str, entries[ref]))}")

    def _window_node(w, where):
        # 1.6: окно — узел; форма подтверждена COSMOS.
        if not isinstance(w, dict) or w.get("kind") \
                not in ("month", "rolling"):
            out.append(f"{where}: window {w!r} — узел вида "
                       "kind=month либо kind=rolling+unit+param")
        elif w.get("kind") == "rolling":
            if w.get("unit") not in LOGIC_UNITS:
                out.append(f"{where}: window.unit "
                           f"{w.get('unit')!r} вне "
                           f"перечня {LOGIC_UNITS}")
            _param_ref(w.get("param"), f"{where} window")

    known = {"subject", "scope", "condition", "demand",
             "window", "source_params", "enforce"}
    for k in sorted(set(logic) - known):
        out.append(f"чужой ключ блока logic: {k!r}")
    subj = logic.get("subject")
    if not isinstance(subj, str) or subj not in LOGIC_SUBJECTS:
        out.append(f"subject {subj!r} вне словаря")
    if "scope" in logic and (not isinstance(logic["scope"], str)
                             or not logic["scope"].strip()):
        out.append(f"scope {logic.get('scope')!r} — обязан быть "
                   f"непустой строкой (область словаря либо код "
                   f"базы)")
    if "window" in logic:
        _window_node(logic["window"], "window")
    enforce = logic.get("enforce", "required")
    if enforce not in LOGIC_ENFORCE:
        out.append(f"enforce {enforce!r} вне перечня "
                   f"{LOGIC_ENFORCE}")
    conds = logic.get("condition", [])
    if not isinstance(conds, list):
        out.append("condition не список")
        conds = []
    for i, c in enumerate(conds):
        if not isinstance(c, dict):
            out.append(f"condition[{i}]: не объект")
            continue
        name = c.get("predicate")
        if not isinstance(name, str) \
                or name not in LOGIC_PREDICATES:
            out.append(f"condition[{i}]: предикат {name!r} вне "
                       f"словаря")
            continue
        if not isinstance(c.get("as_written"), str) \
                or not c["as_written"].strip():
            out.append(f"condition[{i}] ({name}): атом без следа "
                       f"as_written")
        for arg in LOGIC_PREDICATES[name]:
            if arg not in c:
                out.append(f"condition[{i}] ({name}): нет "
                           f"аргумента {arg!r}")
            elif arg.endswith("param"):
                _param_ref(c[arg], f"condition[{i}] ({name})")
            elif arg == "window":
                _window_node(c[arg], f"condition[{i}] ({name})")
            elif arg == "subject" and (
                    not isinstance(c[arg], str)
                    or c[arg] not in LOGIC_SUBJECTS):
                out.append(f"condition[{i}] ({name}): subject "
                           f"{c[arg]!r} вне словаря")
            elif arg == "type_value" and (
                    not isinstance(c[arg], str)
                    or not c[arg].strip()):
                out.append(f"condition[{i}] ({name}): type_value "
                           f"обязан быть непустой строкой")
            elif arg == "allowed" and (
                    not isinstance(c[arg], list) or not c[arg]
                    or not all(isinstance(x, str) and x.strip()
                               for x in c[arg])):
                out.append(f"condition[{i}] ({name}): allowed — "
                           f"непустой список строк")
            elif arg == "unit" and c[arg] not in LOGIC_UNITS:
                out.append(f"condition[{i}] ({name}): unit "
                           f"{c[arg]!r} вне перечня {LOGIC_UNITS} "
                           f"— «подряд» по суткам и по сменам дают "
                           f"разные ответы, единица обязана быть "
                           f"явной")
    dem = logic.get("demand")
    if not isinstance(dem, dict):
        out.append("demand отсутствует или не объект")
    else:
        name = dem.get("kind")
        if not isinstance(name, str) or name not in LOGIC_DEMANDS:
            out.append(f"demand.kind {name!r} вне словаря")
        else:
            if name in LOGIC_SOFT_KINDS \
                    and rule.get("kind") != "SOFT":
                out.append(f"demand.kind {name!r} — мягкая форма "
                           f"волны, законна только у SOFT-правила")
            if not isinstance(dem.get("as_written"), str) \
                    or not dem["as_written"].strip():
                out.append(f"demand ({name}): без следа as_written")
            for arg in LOGIC_DEMANDS[name]:
                if arg not in dem:
                    out.append(f"demand ({name}): нет аргумента "
                               f"{arg!r}")
                elif arg == "param" or arg.endswith("_param"):
                    # Роли-ссылки мягких форм (threshold_param и
                    # родня) — тем же правилом, что param.
                    _param_ref(dem[arg], f"demand ({name})")
                elif arg == "relation" \
                        and dem[arg] not in LOGIC_DEMAND_RELATIONS:
                    out.append(f"demand ({name}): relation "
                               f"{dem[arg]!r} вне перечня "
                               f"{LOGIC_DEMAND_RELATIONS}")
                elif arg == "subject" and (
                        not isinstance(dem[arg], str)
                        or dem[arg] not in LOGIC_SUBJECTS):
                    out.append(f"demand ({name}): subject "
                               f"{dem[arg]!r} вне словаря")
                elif arg == "measure" and dem[arg] \
                        not in LOGIC_MEASURES:
                    out.append(f"demand ({name}): measure "
                               f"{dem[arg]!r} вне перечня "
                               f"{LOGIC_MEASURES}")
                elif arg == "universe" and dem[arg] \
                        not in LOGIC_UNIVERSES:
                    out.append(f"demand ({name}): universe "
                               f"{dem[arg]!r} вне словаря "
                               f"{LOGIC_UNIVERSES}")
            # 1.6: необязательный universe у count_within — из
            # того же словаря.
            if name == "count_within" and "universe" in dem \
                    and dem["universe"] not in LOGIC_UNIVERSES:
                out.append(f"demand (count_within): universe "
                           f"{dem['universe']!r} вне словаря "
                           f"{LOGIC_UNIVERSES}")
            # 1.6: число в требовании без параметра — «число
            # ниоткуда» (образец R-U: «не менее 8» терялось).
            if name in ("exists_person", "exists_universe",
                        "not_exists") \
                    and re.search(r"[0-9]",
                                  str(dem.get("as_written", ""))) \
                    and "param" not in dem:
                out.append(f"demand ({name}): число в следе "
                           f"без параметра — используйте "
                           f"count_within с param либо добавьте "
                           f"ссылку param")
            if "param" in dem and name in (
                    "exists_person", "exists_universe",
                    "not_exists"):
                _param_ref(dem["param"], f"demand ({name})")
    sp = logic.get("source_params", [])
    if not isinstance(sp, list):
        out.append("source_params не список")
    else:
        declared: set = set()
        for name in sp:
            _param_ref(name, "source_params", track=False)
            if isinstance(name, str):
                declared.add(name)
            elif isinstance(name, dict) \
                    and isinstance(name.get("name"), str):
                declared.add(name["name"])
        # Гейт потребителя сверяет source_params с фактом ссылок
        # в обе стороны — производитель краснеет первым.
        if declared != used:
            out.append(f"source_params {sorted(declared)} "
                       f"расходится с применёнными ссылками "
                       f"{sorted(used)} — след источников обязан "
                       f"быть точным в обе стороны")
    return out




_LOGIC_SUBJ_RU = {
    "assignment": "назначение", "flight": "рейс",
    "duty": "полётная смена", "reserve_duty": "резервная смена",
    "rest": "отдых", "sit": "стыковка", "employee": "сотрудник",
    "base": "база приписки", "day": "календарные сутки",
    "month": "календарный месяц",
    "rolling_window": "скользящее окно",
    "day_off": "выходной день", "qualification": "допуск",
}
_LOGIC_REL_RU = {"max": "не более", "min": "не менее",
                 "eq": "ровно", "window": "в окне"}
_LOGIC_UNIVERSE_RU = {
    "employee\u00d7month": "на сотрудника за календарный месяц",
    "employee\u00d7day": "на сотрудника за сутки",
    "base\u00d7day": "на базу за сутки",
    "employee\u00d7rolling_window": "на сотрудника в скользящем "
                                    "окне",
}


def _logic_num(rule: dict, ref):
    """Число параметра правила человеком: минуты — «чч:мм»;
    ссылка-объект 1.6 разрешается по (имя, уточнитель) и
    проговаривает область — сверщик обязан видеть то же число,
    что и валидатор."""
    name, qual = ref, None
    if isinstance(ref, dict):
        name, qual = ref.get("name"), ref.get("qualifier")
    records, problems = logic_param_records(rule, selected_names={name} if isinstance(name, str) else set())
    if problems:
        return "<параметр не прочитан: " + "; ".join(problems) + ">"
    for p in records:
        if isinstance(p, dict) and p.get("name") == name \
                and (not isinstance(ref, dict)
                     or p.get("qualifier") == qual):
            v = p.get("value")
            tail = f" ({qual})" if qual else ""
            if p.get("unit") in ("duration_minutes",
                                 "time_of_day_minutes") \
                    and isinstance(v, int):
                if p.get("unit") == "time_of_day_minutes":
                    return f"{v // 60:02d}:{v % 60:02d}{tail}"
                return f"{v // 60}:{v % 60:02d}{tail}"
            return f"{v}{tail}"
    return f"<{name}?>"


def render_logic(logic: dict, rule: dict) -> str:
    """Обратный пересказ машинной логики детерминированным русским
    предложением (рубеж 1 главы 2 дизайна ступени 3): по нему
    независимый сверщик судит эквивалентность формулировке свода;
    числа разворачиваются из params правила."""
    problems = logic_unit_problems(logic, rule)
    if problems:
        return "Логика не может быть пересказана: " + "; ".join(problems)
    subj = _LOGIC_SUBJ_RU.get(logic.get("subject"),
                              str(logic.get("subject")))
    conds = []
    for c in (logic.get("condition") or []):
        n = c.get("predicate")
        if n == "starts_in":
            conds.append(f"начинается в интервале "
                         f"[{_logic_num(rule, c.get('from_param'))}"
                         f"–{_logic_num(rule, c.get('to_param'))}]")
        elif n == "longer_than":
            conds.append(f"длится больше "
                         f"{_logic_num(rule, c.get('param'))}")
        elif n == "shorter_than":
            conds.append(f"длится меньше "
                         f"{_logic_num(rule, c.get('param'))}")
        elif n == "at_base":
            conds.append("в базе приписки")
        elif n == "at_outstation":
            conds.append("вне базы приписки")
        elif n == "consecutive":
            unit = ("суткам" if c.get("unit") == "day" else "сменам")
            conds.append(f"подряд {_logic_num(rule, c.get('param'))} "
                         f"(счёт по {unit})")
        elif n == "in_window":
            w = c.get("window")
            w = w if isinstance(w, dict) else {}
            if w.get("kind") == "month":
                conds.append("в пределах календарного месяца")
            else:
                wu = ("суткам" if w.get("unit") == "day"
                      else "сменам")
                conds.append(
                    f"в скользящем окне "
                    f"{_logic_num(rule, w.get('param'))} "
                    f"(счёт по {wu})")
        elif n == "follows":
            conds.append(f"следует после: "
                         f"{_LOGIC_SUBJ_RU.get(c.get('subject'), c.get('subject'))}")
        elif n == "has_qualification_for":
            conds.append(f"с допуском на тип «{c.get('type_value')}»")
        elif n == "overlaps_in_time":
            conds.append("пересекается во времени")
        elif n == "marked_unavailable":
            # A generic flag has no cause argument. The trace as_written is
            # evidence, not a missing machine filter for vacation/illness/etc.
            conds.append("сотрудник помечен как недоступный")
        elif n == "in_list":
            conds.append(f"из перечня {c.get('allowed')}")
        else:
            conds.append(f"<{n}?>")
    dem = logic.get("demand") or {}
    k = dem.get("kind")
    dsubj = _LOGIC_SUBJ_RU.get(dem.get("subject"), dem.get("subject"))
    if k in ("count_within", "duration_within"):
        if k == "count_within":
            what = "число"
        elif dem.get("measure") == "block":
            what = "суммарное полётное время"
        else:
            what = "длительность по размаху"
        d = (f"{what} — {_LOGIC_REL_RU.get(dem.get('relation'), '?')} "
             f"{_logic_num(rule, dem.get('param'))}")
        # 1.6: универсум — часть смысла (сверка «не менее 8» без
        # «за месяц» честно краснила у сверщика — run-0044).
        uni = dem.get("universe")
        if uni:
            d += (f" ({_LOGIC_UNIVERSE_RU.get(uni, uni)}, пустая "
                  f"клетка считается нулём)")
    elif k == "exists_person":
        d = f"у этого сотрудника обязан существовать: {dsubj}"
    elif k == "exists_universe":
        uni = dem.get("universe")
        d = (f"в каждой клетке "
             f"«{_LOGIC_UNIVERSE_RU.get(uni, uni)}» обязан "
             f"существовать: {dsubj}")
    elif k == "not_exists":
        d = f"не должно существовать: {dsubj}"
    elif k == "combination_forbidden":
        d = f"сочетание запрещено: {dsubj}"
    else:
        d = f"<{k}?>"
    head = f"Для предмета «{subj}»"
    cond_part = ("; если: " + ", ".join(conds)) if conds else ""
    enforce = logic.get("enforce", "required")
    tail = (" [обязательно к исполнению решателем]"
            if enforce == "required"
            else " [проверяется после счёта сертификатором]")
    return f"{head}{cond_part} — требование: {d}.{tail}"


# Только ASCII-цифры: \d матчит юникод-десятичные, и след с
# арабско-индийскими цифрами канонизировался бы молча (ревью 1.3).
_HHMM_RE = re.compile(r"^([0-9]{1,3}):([0-5][0-9])$")
_CANON_WORD_RES: tuple = (
    # «12 часов», «11 часов 30 минут», «12 ч», «12h»
    (re.compile(r"^([0-9]{1,3})\s*(?:час(?:а|ов)?|ч|h|hours?)\.?"
                r"(?:\s*([0-9]{1,2})\s*(?:мин(?:ут[аы]?)?|м|min)\.?)?$",
                re.IGNORECASE), 60),
    # «45 минут», «45 мин»
    (re.compile(r"^([0-9]{1,4})\s*(?:мин(?:ут[аы]?)?|min)\.?$",
                re.IGNORECASE), 1),
    # «7 суток», «7 дней», «7 days»
    (re.compile(r"^([0-9]{1,2})\s*(?:сут(?:ок|ки)?|дн(?:ей|я)?|дни|"
                r"days?|день)\.?$", re.IGNORECASE), 1440),
)


def canon_minutes(as_written: str):
    """Канонизация времяподобного следа в минуты: «чч:мм» и словесные
    формы с ЯВНОЙ единицей (часы/минуты/сутки). None — не разобралось
    (разбор двоеточия и слов ЖИВЁТ ЗДЕСЬ и только здесь — потребитель
    его не делает никогда). Голое число БЕЗ единицы не канонизируется
    сознательно: «12» может быть и часами, и минутами — трактовка
    угадыванием инвертировала сверку (ревью 1.3: верное 720 при следе
    «12» краснело, ослабленное ×60 проходило)."""
    s = str(as_written).strip()
    mm = _HHMM_RE.match(s)
    if mm:
        return int(mm.group(1)) * 60 + int(mm.group(2))
    for rx, mul in _CANON_WORD_RES:
        mm = rx.match(s)
        if mm:
            groups = mm.groups()
            extra = int(groups[1]) if len(groups) > 1 and groups[1] \
                else 0
            return int(groups[0]) * mul + extra
    # Посадка K02 уже умеет точные дробные часы. Старый след params
    # обязан читать ТОТ ЖЕ одиночный токен, а не отказывать на 0,1ч
    # или округлять его. Только весь литерал; прозу здесь не извлекаем.
    from cdpm import family_params as fp
    tokens = fp._sentence_tokens(s, "duration_minutes")
    if tokens and len(tokens) == 1:
        value, span = tokens[0]
        if span == (0, len(s)) and value.denominator == 1:
            return int(value)
    return None


def validate_param(p, rule_kind: str) -> list[str]:
    """Нарушения одной записи params (fail-closed, типоустойчиво)."""
    if not isinstance(p, dict):
        return [f"параметр не объект: {p!r}"]
    out: list[str] = []
    # Членство в словаре хеширует ключ: нехешируемые name/unit/…
    # из ответа LLM обязаны стать нарушением, не TypeError (ревью
    # 1.3 — гейт судит недоверенный пакет).
    name = p.get("name")
    unit = p.get("unit")
    names = _cat("PARAMS")
    code_units = _cat("CODE_UNITS")
    if not isinstance(name, str) or name not in names:
        out.append(f"имя параметра {name!r} вне словаря")
        name = name if isinstance(name, str) else repr(name)
    elif unit != names[name]:
        out.append(f"{name}: единица {unit!r} не согласована со "
                   f"словарём ({names[name]})")
    if not isinstance(unit, str) or unit not in _cat("PARAM_UNITS"):
        out.append(f"{name}: единица {unit!r} вне перечня")
        unit = unit if isinstance(unit, str) else None
    v = p.get("value")
    if unit in code_units:
        # Значение кода — обозначение справочника: непустая строка;
        # перечень — сортированные обозначения через запятую.
        if not isinstance(v, str) or not v.strip():
            out.append(f"{name}: value кода — непустая строка, не {v!r}")
        elif unit == "code" and "," in v:
            out.append(f"{name}: единица code несёт ОДНО обозначение; "
                       f"перечень пишется единицей code_list")
        elif unit == "code_list" and canon_code_list(v) != v:
            out.append(f"{name}: перечень кодов не канонизирован "
                       f"(ожидалось {canon_code_list(v)!r})")
        aw_c = p.get("as_written")
        if isinstance(aw_c, str) and isinstance(v, str):
            miss = [c for c in split_code_list(v)
                    if _fit_norm_text(c) not in _fit_norm_text(aw_c)]
            if miss:
                out.append(f"{name}: обозначений {miss} нет в следе "
                           f"as_written — код берётся из документа")
    elif isinstance(v, bool) or not isinstance(v, (int, float)):
        out.append(f"{name}: value не число: {v!r}")
    elif unit in ("duration_minutes", "time_of_day_minutes", "count") \
            and (not isinstance(v, int) or v < 0):  # noqa: E501
        out.append(f"{name}: value для {unit} — целое ≥ 0, не {v!r}")
    elif isinstance(v, (int, float)) and v < 0:
        out.append(f"{name}: value отрицательное: {v!r}")
    if unit == "time_of_day_minutes" and isinstance(v, int) \
            and not isinstance(v, bool) and v > 1439:
        out.append(f"{name}: время суток вне 0..1439: {v}")
    rel = p.get("relation")
    relations = _cat("PARAM_RELATIONS")
    if not isinstance(rel, str) or rel not in relations:
        out.append(f"{name}: relation {rel!r} вне перечня "
                   f"{relations}")
    q = p.get("qualifier")
    if q is not None and (not isinstance(q, str) or not q.strip()):
        out.append(f"{name}: qualifier не непустая строка: {q!r}")
    elif q is not None and name in _cat("AREA_PARAM_NAMES") \
            and canon_area_qualifier(q) is None:
        # 1.4: у параметров области свободный текст уточнителя ложился
        # бы у потребителя в «не применено» молча — словарь закрыт.
        out.append(f"{name}: уточнитель области {q!r} вне закрытого "
                   f"словаря {_cat('AREA_QUALIFIERS')}")
    aw = p.get("as_written")
    if not isinstance(aw, str) or not aw.strip():
        out.append(f"{name}: без следа as_written")
    elif unit in ("duration_minutes", "time_of_day_minutes") \
            and isinstance(v, int):
        got = canon_minutes(aw)
        if got is None:
            # Fail-closed: неразбираемый след выключал бы сверку —
            # ослабление ×60/×1440 проходило бы молча (ревью 1.3).
            out.append(f"{name}: след {aw!r} не канонизируется "
                       f"(нет явной единицы времени) — сверка "
                       f"канонизации невозможна")
        elif got != v:
            out.append(f"{name}: канонизация не сходится: "
                       f"as_written {aw!r} = {got}, value {v}")
    tz = p.get("tz")
    if not isinstance(tz, (str, type(None))):
        out.append(f"{name}: tz не строка: {tz!r}")
        tz = None
    if unit == "time_of_day_minutes":
        zones = _cat("PARAM_TZ")
        if tz not in zones:
            out.append(f"{name}: time_of_day требует tz из "
                       f"{zones}, получено {tz!r}")
    elif tz is not None:
        out.append(f"{name}: tz допустим только у time_of_day")
    return out

def split_code_list(value) -> list:
    """Обозначения перечня из значения параметра: «A320, B737-800» →
    ['A320', 'B737-800']. Typed-массив сохраняет границы и текст
    обозначений; у прежней строки пустые части отбрасываются.
    Это читатель, не подтверждение правильности формы посадки."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def canon_code_list(value) -> str:
    """Канон перечня кодов: обозначения как в документе, без повторов,
    отсортированы (детерминизм диффа), через запятую с пробелом."""
    seen: list = []
    for c in split_code_list(value):
        if c not in seen:
            seen.append(c)
    return ", ".join(sorted(seen))


def parameter_codes(doc: dict) -> list:
    """Обозначения справочников, названные параметрами ВМП:
    [{rule, param, code}]. Источник — единицы code/code_list словаря
    параметров (посадка A/B) и параметры строк (params[])."""
    out: list = []
    code_units = _cat("CODE_UNITS")
    for r in doc.get("rules") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id"))
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        for name, cell in (fit.get("params") or {}).items():
            # Единица имени: параметр словаря либо условие семьи (каталог).
            if fit_param_unit(fit.get("family"), name) not in code_units:
                continue
            val = cell.get("value") if isinstance(cell, dict) else cell
            for code in split_code_list(val):
                out.append({"rule": rid, "param": str(name), "code": code})
        for p in r.get("params") or []:
            if isinstance(p, dict) and p.get("unit") in code_units:
                for code in split_code_list(p.get("value")):
                    out.append({"rule": rid, "param": str(p.get("name")),
                                "code": code})
        for c in r.get("codes") or []:
            if isinstance(c, dict) and c.get("code"):
                out.append({"rule": rid, "param": "codes",
                            "code": str(c["code"])})
    return out


def codes_absent_in_data(doc: dict, dataset: dict) -> list:
    """Обозначения, названные ВМП, которых НЕТ в данных авиакомпании
    (решение владельца 03.09: конкретика подставляется из документов,
    но обязана существовать в данных). Сравнение — по значениям всех
    ячеек набора, без регистра и лишних пробелов."""
    values: set = set()
    for _sid, sheets in (dataset or {}).get("tables", {}).items():
        if not isinstance(sheets, dict):
            continue
        for _name, table in sheets.items():
            for row in (table or {}).get("rows") or []:
                if isinstance(row, dict):
                    values |= {_fit_norm_text(v) for v in row.values()
                               if isinstance(v, (str, int, float))}
    out: list = []
    for item in parameter_codes(doc):
        if _fit_norm_text(item["code"]) not in values:
            out.append(item)
    return out


def sanitize_params(items, rule_kind, rule_text: str = ""):
    """(kept, notes): чистые записи params по словарю + следы
    отброшенного. Общий для ОБОИХ производящих путей — извлечения и
    патча обработчика (живой Лайт run-0040: обработчик выдумал
    'min_rest_away'/'minutes', отказ валил весь патч и строка
    оставалась заблокированной; чужая запись обязана отброситься со
    следом, как при извлечении, а не топить ответ)."""
    kept: list = []
    notes: list[str] = []
    seen: set = set()
    for p in (items if isinstance(items, list) else []):
        errs = validate_param(p, rule_kind)
        if errs and rule_text \
                and any("не канонизируется" in e for e in errs):
            # Живой класс строки-11: след — голое число, а единица
            # явно стоит в формулировке правила рядом с ТЕМ ЖЕ
            # числом — след дополняется дословным фрагментом
            # формулировки (не угадыванием).
            aw = str(p.get("as_written") or "").strip()
            if aw.isdigit():
                mm = re.search(
                    aw + r"\s*(?:мин(?:ут\w*)?|час\w*|ч)\b",
                    rule_text, re.IGNORECASE)
                if mm:
                    p = dict(p, as_written=mm.group(0))
                    errs = validate_param(p, rule_kind)
                    if not errs:
                        notes.append(
                            f"параметр {p.get('name')}: след {aw!r} "
                            f"дополнен единицей из формулировки "
                            f"правила ({mm.group(0)!r})")
        if errs:
            notes.append(f"параметр отброшен: {errs[0]}")
            continue
        entry = {k: p[k] for k in ("name", "value", "unit",
                                   "as_written", "relation")
                 if k in p}
        if isinstance(p.get("qualifier"), str) \
                and p["qualifier"].strip():
            entry["qualifier"] = p["qualifier"].strip()
            if entry.get("name") in _cat("AREA_PARAM_NAMES"):
                canon = canon_area_qualifier(entry["qualifier"])
                if canon:
                    entry["qualifier"] = canon
        fixed_rel = _relation_from_name_and_text(
            entry.get("name"), entry.get("relation"), rule_text)
        if fixed_rel:
            old_rel = entry["relation"]
            entry["relation"] = fixed_rel
            notes.append(f"параметр {entry['name']}: отношение "
                         f"{old_rel!r} противоречило природе имени "
                         f"и формулировке — исправлено на "
                         f"{fixed_rel!r}")
        if "qualifier" in entry:
            pass
        elif entry.get("name") in _cat("AREA_PARAM_NAMES"):
            # Живой урок 2026-08-26: область только словами (след
            # параметра либо формальная запись правила) →
            # восстановить машинно (однозначную), не пропускать молча.
            area = (area_from_trace(entry.get("as_written"))
                    or area_from_trace(rule_text))
            if area:
                entry["qualifier"] = area
                notes.append(f"параметр {entry['name']}: уточнитель "
                             f"области «{area}» восстановлен из следа")
        if p.get("tz") is not None:
            entry["tz"] = p["tz"]
        key = (entry.get("name"), entry.get("qualifier"))
        if key in seen:
            dup = next(e for e in kept
                       if (e.get("name"), e.get("qualifier")) == key)
            if dup == entry:
                continue          # точный дубль — след не нужен
            notes.append(f"параметр {key[0]!r} дважды без "
                         f"различающего уточнителя — вторая запись "
                         f"({entry.get('value')!r}) отброшена")
            continue
        seen.add(key)
        kept.append(entry)
    return kept, notes


# Жёсткие типы строк: определяют допустимость решения.
_HARD_KINDS = ("HARD", "DATA", "DEF", "CASE")

# Вопросы о целевой функции (ЦФ-1): относятся только к режиму
# optimization — их исключают и матрица (hard/soft-ячейки), и
# прежний индикатор а/б/в readiness().
GOAL_QUESTION_IDS = ("Q-3a-GOAL", "Q-3b-GOAL")
# Семейство вопросов о ЦЕЛЕВОЙ: базовые Q-*-GOAL и доборы целевой
# Q-*-GOAL-T[-N] (живая дыра ночи 30→31.08, run-0002: GOAL применён
# ответом только о весах — целевой у зоны нет, спросить некому).
# Их опубликованные издания -E<N> идут в тот же обработчик.
# Вопросы весов (-W) в семейство НЕ входят: их применяет реестр.
_GOAL_FAMILY_RE = re.compile(r"Q-3[ab]-GOAL(?:-T(?:-\d+)?)?(?:-E(?:[2-9]|[1-9]\d+))?$")


def is_goal_family(qid) -> bool:
    """Вопрос семейства целевой и его издание — ответ
    рождает целевые и веса, применяется веткой _apply_goal_answer."""
    return isinstance(qid, str) \
        and _GOAL_FAMILY_RE.fullmatch(qid) is not None


def _goal_question(doc: dict, part: str, *, strict: bool = False) -> dict | None:
    from cdpm import question_editions
    q = next((x for x in doc.get("questions", []) if isinstance(x, dict)
              and x.get("id") == f"Q-{part}-GOAL"), None)
    if q is None:
        return None
    try:
        return question_editions.latest(doc, q)
    except question_editions.EditionRefusal:
        if strict:
            raise
        # A broken lineage cannot supply an authoritative applied decline;
        # schema/model diagnostics retain the underlying invalid record.
        return None

# Вопрос о машинном критерии пейринга (приказ 0096): как и GOAL,
# относится ТОЛЬКО к режиму optimization — открытый вопрос критерия
# не блокирует задачи выполнимости (ревью пакета 1.11).
CRITERION_QUESTION_ID = "Q-3a-критерий-пейринга"

# Фраза опции отказа от оптимизации в вопросе о целевой: по ней
# применённый ответ распознаётся как подтверждённый отказ (текст
# опции печатает extract.ensure_goal_questions — менять согласованно).
_GOAL_DECLINE_PHRASE = "любое допустимое"
# Равносильные формы отказа (ИДАК H0-К, 2026-09-04): авиакомпания
# может сказать не «любое допустимое», а прямо «критерия оптимизации
# нет» — смысл тот же: режим optimization недоступен, решение ищется
# в режиме взвешенных штрафов (feasibility-soft).
# Только полные формы: голое «критерия нет» ловило бы ответы, которые
# критерий как раз называют («другого критерия нет, кроме стоимости»).
_GOAL_DECLINE_PHRASES = (_GOAL_DECLINE_PHRASE, "критерия оптимизации нет",
                         "нет критерия оптимизации",
                         "критерий оптимизации отсутствует")



def goal_answer_declines(goal_q: dict | None) -> bool:
    """Применённый ответ на вопрос о целевой = подтверждённый отказ от
    оптимизации. Машинный вердикт обработчика (goal_decline) главнее
    текстовой эвристики; фраза — только для ответов, применённых до
    появления признака (легаси/вручную), и НЕ срабатывает при явном
    отрицании рядом («любое допустимое нас не устраивает» — ревью
    фазы Б 2026-08-21). Одна правда для матрицы корректности и для
    вопроса о машинном критерии (ensure_criterion_question)."""
    if not isinstance(goal_q, dict):
        return False
    ans = goal_q.get("answer") or {}
    if ans.get("status") != "применён":
        return False
    gd = ans.get("goal_decline")
    if gd is True:
        return True
    if gd is not None:
        return False
    raw_l = str(ans.get("raw") or "").lower()
    return any(p in raw_l for p in _GOAL_DECLINE_PHRASES) \
        and not re.search(r"\bне\s+(устраив|подтвержда|соглас|"
                          r"подходит|годит)", raw_l)


# Сообщение матрицы и лестницы готовности при подтверждённом отсутствии
# критерия — одно на весь путь (панель CDPM, пакет постановки, страница
# COSMOS читают его отсюда; формулировка владельца 04.09.2026).
_NO_CRITERION_NOTE = (
    "критерия оптимизации нет (подтверждено авиакомпанией): режим "
    "optimization недоступен; возможен только поиск feasibility-soft — "
    "оптимальное решение, удовлетворяющее всем жёстким ограничениям, с "
    "критерием, равным взвешенной сумме штрафов мягких правил (без "
    "взвешенных мягких правил — feasibility-hard, любое допустимое)")


def _criterion_from_answer(doc: dict) -> dict | None:
    """Критерий из ПРИМЕНЁННОГО ответа анкеты на вопрос
    Q-3a-критерий-пейринга (приказ владельца 2026-08-28: расчёт по
    критерию а/к 0096; машинного носителя вида/ставок/версии в своде
    нет — носитель = ответ авиакомпании). Ответ несёт машинный
    JSON-блок; разбор строгий, форма блока — контракт приёмной части
    COSMOS (vmp/criteria, строгий базлайн): не разобралось — None
    (вопрос остаётся виден в анкете)."""
    import json as _json
    import re as _re
    # Семейство вопросов критерия: базовый id + доборы «-2», «-3»…
    # (ремень (б): применённый ответ без машинного блока порождает
    # вопрос-добор — прецедент «повторного запроса весов»). Носителем
    # берётся ПОСЛЕДНИЙ применённый ответ семейства с разобравшимся
    # блоком.
    fam = [x for x in doc.get("questions", [])
           if isinstance(x, dict)
           and str(x.get("id") or "").startswith(CRITERION_QUESTION_ID)]
    fam.sort(key=lambda x: (len(str(x.get("id"))), str(x.get("id"))),
             reverse=True)
    q = a = m_json = None
    for cand in fam:
        ca = cand.get("answer") or {}
        if ca.get("status") != "применён" or not ca.get("raw"):
            continue
        mj = _re.search(r"\{.*\}", str(ca["raw"]), _re.DOTALL)
        if mj:
            q, a, m_json = cand, ca, mj
            break
    if m_json is None:
        return None
    # Кривой ответ (JSON пишет авиакомпания/LLM) — None, не падение:
    # функция стоит на пути readiness_matrix, обещание докстроки
    # «не разобралось — None» держится для ЛЮБОЙ кривизны формы
    # (ревью пакета 1.11: TypeError/AttributeError валили матрицу).
    try:
        d = _json.loads(m_json.group(0))
        crit_label = str(d.get("criterion") or "")
        # Ремень (б) ночи демо 2026-08-29 (согласован COSMOS):
        # второй вид закрытого словаря — MIN_PERSON_DAYS; kind
        # строчными (имя словаря видов приёмника), rate_per_duty —
        # ЦЕЛОЕ > 0, source_rule обязателен, версия ставки —
        # MUST_BE_EXTERNAL единообразно.
        if crit_label == "MIN_PERSON_DAYS":
            rate = d.get("rate_per_duty")
            if not isinstance(rate, int) or isinstance(rate, bool) \
                    or rate <= 0:
                return None
            if not d.get("rule") or not d.get("version") \
                    or not d.get("effective_from"):
                return None
            return {
                "kind": "min_person_days",
                "label": "MIN_PERSON_DAYS",
                "source_rule": str(d.get("rule")),
                "rate_per_duty": rate,
                "rates_version": {
                    "source": str(d.get("rule")),
                    "version": str(d["version"]),
                    "effective_from": str(d["effective_from"])},
                "as_written": str(a["raw"]),
                "source_answer": {"question": q["id"],
                                  "revision": a.get("revision")},
            }
        # Ревизия 1.13 (принцип «шаблон обезличен»): канон метки —
        # generic NORMALIZED_COST; легаси-метка с номером правила
        # конкретного заказчика законна в СТАРЫХ ответах (тихо не
        # ломаем применённые ответы), новым не рекомендуется.
        if crit_label not in (
                "NORMALIZED_COST", "NORMALIZED_COST_0096"):
            return None
        from vmp.objective_projection import positive_finite_rate
        rates = d.get("rates")
        if not isinstance(rates, dict) or not rates or not all(
                isinstance(row, dict) and all(positive_finite_rate(row.get(field))
                    for field in ("flight", "work")) for row in rates.values()):
            return None
        flights = {row["flight"] for row in rates.values()}
        works = {row["work"] for row in rates.values()}
        if len(flights) != 1 or len(works) != 1:
            return None    # ставки рангов расходятся — не сплющивать
        if not d.get("version") or not d.get("effective_from"):
            return None    # политика MUST_BE_EXTERNAL: версия обязательна
        # Зачистка 2026-08-29 (принцип «шаблон обезличен»): номера
        # правила и параметра — ТОЛЬКО из ответа авиакомпании;
        # умолчаний с идентификаторами конкретного заказчика в коде
        # нет — ответ без них честно не разбирается.
        if not d.get("rule") or not d.get("rates_param"):
            return None
        return {
            "kind": "rank_normalized_cost",
            "label": "NORMALIZED_COST",
            "source_rule": str(d.get("rule")),
            "rates": {"flight": flights.pop(), "work": works.pop()},
            "rates_version": {
                "source": str(d.get("rates_param")),
                "version": str(d["version"]),
                "effective_from": str(d["effective_from"])},
            "as_written": str(a["raw"]),
            "source_answer": {"question": q["id"],
                              "revision": a.get("revision")},
        }
    except (TypeError, ValueError, AttributeError):
        return None


def _prose_person_day_rate(text: str) -> int | None:
    """A whole positive scalar, never the first/last operand of a formula."""
    import re
    from vmp.objective_projection import prose_integer
    # Select the first separator BEFORE looking for digits: an invalid rate
    # such as «— -100» must not fall through to the inner minus or unit suffix.
    marker = re.search(r"[—=]|(?<!\w)-(?!\w)", text)
    if marker:
        token = re.match(r"\s*(\d+)", text[marker.end():])
        if token is None:
            return None
        start, end = (marker.end() + token.start(1),
                      marker.end() + token.end(1))
    else:
        token = re.search(r"(?<!\S)(\d+)\s+условн", text)
        if token is None:
            return None
        # Undelimited prose must have a plain-text prefix, not another operand.
        prefix = re.split(r"[;:]", text[:token.start(1)])[-1]
        if any(not (c.isalpha() or c.isspace() or c in "()«»\"'") for c in prefix):
            return None
        start, end = token.start(1), token.end(1)
    rate = prose_integer(text, start, end)
    return rate if rate is not None and rate > 0 else None


def derive_pairing_criterion(doc: dict) -> dict | None:
    """Критерий оптимизации пейринга из данных авиакомпании
    (спецификация 1.9, решение владельца 2026-08-26): ищется строка
    «критерий оптимизации пейринга …» среди целевых и мягких строк
    зоны пейринга; вид — по канону формулировки (перечень видов
    открыт одним значением min_person_days), ставка — числом из
    формулировки. Нет строки или не разобралась — None (пакет без
    критерия не получает режим optimization пейринга).

    Живёт в модели (перенос из export_solver, ревизия 1.11): критерий
    — свойство модели, его видит матрица корректности."""
    import re as _re
    # Носитель критерия — ДОКУМЕНТ (П6/П11, 2026-09-02): машинный блок
    # criterion целевой пейринга первичен; ответ анкеты — когда
    # документ критерия не назвал; деривация из формулировок — запас.
    from_doc = criterion_from_objectives(doc)
    if from_doc is not None:
        return from_doc
    from_answer = _criterion_from_answer(doc)
    if from_answer is not None:
        return _criterion_with_catalogue_direction(from_answer)
    cand = []
    for coll in (doc.get("objectives", []), doc.get("rules", [])):
        if not isinstance(coll, list):
            continue        # мусор в модели — дело validate_model,
        for r in coll:      # деривация не падает (контракт матрицы)
            if not isinstance(r, dict) \
                    or r.get("zone") not in ("PAIRING", "BOTH"):
                continue
            text = (str(r.get("title") or "") + " "
                    + str(r.get("statement") or "")).lower()
            # Распознавание по СОДЕРЖАНИЮ, не по слову «критерий»:
            # извлечение законно переформулирует строку (живой урок
            # run-0057: «Минимум человеко-дней командировок»).
            if ("критерий" in text and "пейринг" in text) \
                    or ("человеко-дн" in text
                        and ("миним" in text or "minimize" in text)):
                cand.append(r)
    for r in cand:
        stmt = (str(r.get("title") or "") + ": "
                + str(r.get("statement") or ""))
        low = stmt.lower()
        if "человеко-дн" in low \
                and ("миним" in low or "minimize" in low):
            rate = _prose_person_day_rate(stmt)
            if rate is not None:
                return _criterion_with_catalogue_direction({"kind": "min_person_days",
                        "rate_per_duty": rate,
                        "as_written": str(r.get("statement") or ""),
                        "source_rule": str(r.get("id"))})
    return None


def objective_is_blocked(objective: dict, questions_by_id: dict) -> bool:
    """An objective under an open or unknown blocker is not active."""
    blockers = objective.get("blockers")
    if not isinstance(blockers, list):
        return bool(blockers)
    for qid in blockers:
        blocker = questions_by_id.get(qid) if isinstance(qid, str) else None
        if blocker is None or is_open_question(blocker):
            return True
    return False


def readiness_matrix(doc: dict, spec_version: str | None = None) -> dict:
    """ЦФ-1 (постановка владельца 2026-08-21): матрица корректности
    свода 3×2 — столбцы пейринг/ростеринг, строки режимы задачи.
    Ячейки вложены: optimization ⊆ feasibility-soft ⊆
    feasibility-hard.

    - feasibility-hard: жёсткие строки зоны не блокированы и не под
      открытыми вопросами — любое допустимое решение оптимально;
    - feasibility-soft: + мягкие строки зоны разблокированы (веса
      известны из ответов) — оптимум = минимум штрафов;
    - optimization: + определена хотя бы одна незаблокированная
      целевая зоны; если целевых нет, но вопрос о целевой (Q-*-GOAL)
      закрыт ПРИМЕНЁННЫМ ответом с опцией «любое допустимое
      решение» либо «критерия оптимизации нет» — зафиксирован отказ
      от оптимизации: режим optimization недоступен, решение ищется
      в режиме взвешенных штрафов feasibility-soft (все жёсткие
      соблюдены, минимум взвешенной суммы штрафов мягких), без
      взвешенных мягких — feasibility-hard (сообщение
      _NO_CRITERION_NOTE, ИДАК H0-К 2026-09-04). Разобранный, но не применённый
      ответ отказом не считается. Ревизия 1.11 (приказ 0096): для
      пейринга режим включается и ПРИМЕНЁННЫМ машинным ответом о
      критерии (CRITERION_QUESTION_ID, только строгий разбор
      _criterion_from_answer — текстовая деривация матрице
      запрещена); критерий перекрывает прежний отказ громко, а
      доказанно более поздний по ревизии отказ — критерий.

    Вопросы GOAL_QUESTION_IDS (обе колонки) относятся ТОЛЬКО к режиму
    optimization и режимы выполнимости не блокируют. Открытый вопрос
    без пригодной привязки к строкам (нет rule_refs, висячие или
    нестроковые ссылки) блокирует feasibility-hard ОБЕИХ задач —
    fail-closed, согласованно с readiness()."""
    rules_by_id = {r.get("id"): r for r in doc.get("rules", [])
                   if isinstance(r, dict)}
    q_by_id = {q.get("id"): q for q in doc.get("questions", [])
               if isinstance(q, dict)}
    # K04 (§2.4 (а)): один расчёт факта «пересмотр открыт» на всю
    # матрицу — тот же, что читают готовность, выпуск и гейт.
    pending_facts = pending_revision_facts(doc)
    # Барьеры K01/K06 (контракт П0 §3.2, §3.5, §9) и расхождение
    # носителей A05 (Н-4): один расчёт на матрицу; жёсткие носители
    # держат выполнимость задачи, мягкие — режимы со штрафами (как
    # пробел покрытия).
    barriers = carrier_grounds(doc) + readiness_barriers(doc, spec_version)
    out: dict = {}
    for task, part in (("pairing", "3a"), ("rostering", "3b")):
        zones = ("PAIRING", "BOTH") if task == "pairing" \
            else ("ROSTERING", "BOTH")
        hard_reasons: dict = {}
        soft_reasons: dict = {}
        optimization_support = []

        def _hit(box: dict, tag: str) -> None:
            box[tag] = box.get(tag, 0) + 1

        # Ячейка под открытым пересмотром: прежнее значение решением не
        # является — жёсткий носитель держит выполнимость, мягкий —
        # режим штрафов; носитель неизвестного вида судится жёстким
        # (fail-closed).
        for fact in pending_facts:
            if fact.get("zone") not in zones:
                continue
            _hit(soft_reasons if fact.get("kind") == "SOFT"
                 else hard_reasons, PENDING_REVISION_TAG)
        for pr in barriers:
            affected = barrier_affects(pr)
            if (task, "feasibility-hard") in affected:
                _hit(hard_reasons, pr["words"])
            elif (task, "feasibility-soft") in affected:
                _hit(soft_reasons, pr["words"])
            elif (task, "optimization") in affected:
                optimization_support.append(pr["words"])
        for r in rules_by_id.values():
            if r.get("zone") not in zones:
                continue
            # Пробел покрытия (слово владельца 09.09.2026): строка без
            # шаблона исполнять некому — жёсткая держит выполнимость,
            # мягкая — режим штрафов; пакет при пробеле не выдаётся.
            fit = r.get("template_fit")
            if r.get("kind") in ("HARD", "SOFT") \
                    and isinstance(fit, dict) and fit.get("category") == "C":
                _hit(hard_reasons if r.get("kind") == "HARD"
                     else soft_reasons, COVERAGE_GAP_TAG)
            if not r.get("blockers"):
                continue
            if r.get("kind") in _HARD_KINDS:
                _hit(hard_reasons, "блокированные жёсткие строки")
            elif r.get("kind") == "SOFT":
                _hit(soft_reasons,
                     "мягкие строки без весов (блокированы)")
        goal_id = f"Q-{part}-GOAL"
        goal_q = _goal_question(doc, part)
        for q in open_questions(doc):
            if is_goal_family(q.get("id")) \
                    or q.get("id") == CRITERION_QUESTION_ID:
                continue
            resolved = question_rules(doc, q)
            if resolved is None:
                # Вопрос без пригодной привязки: неизвестно, какие
                # строки под ним, — жёсткий блокер обеих задач.
                _hit(hard_reasons,
                     f"открытые вопросы части {q.get('part')} без "
                     f"привязки к строкам")
                continue
            touched = [r for r in resolved
                       if r.get("zone") in zones]
            if task in (parameter_question_tasks(q) or ()):
                # A parameter is shared by its family's declared consumers,
                # even if every source carrier belongs to the other zone.
                touched = resolved
            if any(r.get("kind") not in ("SOFT",) for r in touched):
                _hit(hard_reasons,
                     f"открытые вопросы части {q.get('part')} по "
                     f"жёстким строкам")
            elif touched:
                _hit(soft_reasons,
                     f"открытые вопросы части {q.get('part')} по "
                     f"мягким строкам")
        objectives = [o for o in doc.get("objectives", [])
                      if isinstance(o, dict)
                      and o.get("zone") in zones]

        active = [o for o in objectives if not objective_is_blocked(o, q_by_id)]
        hard_ok = not hard_reasons
        soft_ok = hard_ok and not soft_reasons
        goal_ans = (goal_q.get("answer") or {}) \
            if isinstance(goal_q, dict) else {}
        goal_applied = goal_ans.get("status") == "применён"
        # Признак отказа: машинный вердикт обработчика (goal_decline)
        # главнее текстовой эвристики; фраза — только для ответов,
        # применённых до появления признака (легаси/вручную), и НЕ
        # срабатывает при явном отрицании рядом («любое допустимое
        # нас не устраивает» — ревью фазы Б 2026-08-21).
        goal_declined = goal_applied and goal_answer_declines(goal_q)
        # Приказ 0096 (2026-08-28): применённый машинный ответ
        # авиакомпании о критерии пейринга — законная целевая; целевых
        # строк в модели при этом может не быть (носитель — ответ).
        # СТРОГО ответ (_criterion_from_answer), НЕ полная деривация:
        # текстовый фолбэк derive_pairing_criterion не проверяет
        # блокеры строк и открытые вопросы — матрице он запрещён
        # (ревью пакета 1.11), его место — экспортный путь.
        crit = _criterion_from_answer(doc) \
            if task == "pairing" else None
        crit_newer = None
        decline_newer = False
        if crit is not None and goal_declined:
            g_rev = goal_ans.get("revision")
            c_rev = (crit.get("source_answer") or {}).get("revision")
            crit_newer = (isinstance(g_rev, int)
                          and isinstance(c_rev, int)
                          and c_rev > g_rev)
            if isinstance(g_rev, int) and isinstance(c_rev, int) \
                    and g_rev > c_rev:
                # Отказ ДОКАЗАННО позже ответа о критерии: критерий
                # не перекрывает его молча (пометка о порядке обязана
                # быть машинной правдой — ревью пакета 1.11).
                crit = None
                decline_newer = True
        pending_edition = (isinstance(goal_q, dict)
                           and bool(goal_q.get("edition_of"))
                           and not goal_applied)
        if pending_edition:
            opt_state = "нет"
            opt_note = ("изменились условия целевой — требуется ответ на "
                        + goal_q["id"])
        elif active:
            opt_state = "готово" if soft_ok else "нет"
            opt_note = f"целевых: {len(active)}"
            if len(active) < len(objectives):
                opt_note += (f"; под открытыми вопросами: "
                             f"{len(objectives) - len(active)}")
            if goal_declined:
                # Дыра из ревью ЦФ-1: целевые появились после
                # применённого отказа — тихо перекрывать его нельзя.
                opt_note += ("; ВНИМАНИЕ: ранее применён отказ от "
                             "оптимизации — требуется подтверждение "
                             "авиакомпании")
        elif crit is not None:
            # Критерий из ответа перекрывает прежний отказ от
            # оптимизации — громко, не молча (симметрия дыры из
            # ревью ЦФ-1: тихо перекрывать применённые ответы
            # нельзя в обе стороны); доказанно более поздний отказ
            # обнулил crit выше.
            opt_state = "готово" if soft_ok else "нет"
            opt_note = (f"критерий авиакомпании: "
                        f"{crit.get('label')} "
                        f"({crit.get('source_rule')}) из ответа "
                        f"анкеты")
            if goal_declined:
                opt_note += ("; применён ПОВЕРХ прежнего отказа "
                             "от оптимизации"
                             + (" — ответ о критерии позже отказа"
                                if crit_newer else
                                " — порядок ответов по ревизиям "
                                "не доказан, критерий главнее "
                                "как машинная форма"))
        elif objectives:
            opt_state = "нет"
            opt_note = (f"все целевые ({len(objectives)}) под "
                        f"открытыми вопросами")
        elif goal_declined:
            opt_state = "отказ подтверждён"
            opt_note = _NO_CRITERION_NOTE
            if decline_newer:
                opt_note += ("; есть ответ о критерии, но отказ "
                             "НОВЕЕ по ревизии — подтвердите "
                             "актуальность критерия у авиакомпании")
        elif goal_q is not None and not is_open_question(goal_q):
            # Ответ есть (разобран или даже применён), но целевые в
            # модель не внесены и отказа в ответе нет — готовности
            # НЕТ и отказ авиакомпании не выдумывается.
            opt_state = "нет"
            opt_note = ("ответ о целевой получен, но целевые в "
                        "модель не внесены")
        elif goal_q is not None:
            opt_state = "нет"
            opt_note = ("целевая не определена — открыт вопрос "
                        "части " + part)
        else:
            opt_state = "нет"
            opt_note = "целевая не определена, вопрос не задавался"
        direction_problems = objective_direction_problems(active)
        if direction_problems:
            opt_state = "нет"
            opt_note = "; ".join(direction_problems)
        if optimization_support:
            opt_state = "нет"
            opt_note += "; " + "; ".join(optimization_support)
        out[task] = {
            "feasibility-hard": {"ready": hard_ok,
                                 "reasons": hard_reasons},
            "feasibility-soft": {"ready": soft_ok,
                                 "reasons": {**hard_reasons,
                                             **soft_reasons}},
            "optimization": {"ready": soft_ok and not pending_edition
                             and not direction_problems
                             and not optimization_support
                             and (bool(active) or crit is not None),
                             "state": opt_state, "note": opt_note,
                             "objectives": [o.get("id")
                                            for o in active]},
        }
    return out


_GOAL_TASK = {"3a": ("пейринга", ("PAIRING", "BOTH")),
              "3b": ("ростеринга", ("ROSTERING", "BOTH"))}

_GOAL_TABLE_SECTION = (
    "В документах не обнаружено целевой функции {task} (критерия "
    "оптимизации). Заполните "
    "таблицу целевых (каждая строка — отдельная задача оптимизации): "
    "компонент; метрика и единица; направление "
    "(минимизировать/максимизировать); ярус важности; вес внутри "
    "яруса; правило разрешения ничьих. Либо явно подтвердите, что "
    "критерия оптимизации нет («критерия нет» / «любое допустимое "
    "решение») — тогда режим optimization недоступен: решение ищется "
    "в режиме взвешенных штрафов (feasibility-soft) — все жёсткие "
    "правила соблюдаются, минимизируется взвешенная сумма штрафов "
    "мягких правил; если взвешенных мягких правил нет — любое "
    "допустимое решение (feasibility-hard).")

_GOAL_WEIGHTS_SECTION = (
    "Мягкие правила {task} обязаны иметь штрафы (ЦФ-1): для каждой "
    "затронутой мягкой строки задайте вес или разряд важности — "
    "мягкое правило без штрафа числится неразрешённым компонентом.")


def _uniq_source_refs(rules: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rules:
        for s in r.get("sources", []):
            ref = dict(s)
            if ref not in out:
                out.append(ref)
    return out


def ensure_weights_question(doc: dict, part: str,
                            rules: list[dict]) -> str:
    """Новый вопрос весов для мягких строк, оставшихся без пути к
    весу ПОСЛЕ ответа на Q-{part}-GOAL: поздний SOFT из условной
    ветки либо веса, не названные в принятом ответе (ревью фазы Б
    2026-08-21 — иначе дедлок: применённым вопросом блокировать
    нельзя, а без блокировки строка выпадает из анкеты). Это ОБЫЧНЫЙ
    вопрос части 3 — матрица корректности честно считает его блокером
    soft-ячейки. Блокирует переданные строки; id уникален."""
    task, _zones = _GOAL_TASK[part]
    base = f"Q-{part}-GOAL-W"
    existing = {q.get("id") for q in doc.get("questions", [])}
    qid, n = base, 1
    while qid in existing:
        n += 1
        qid = f"{base}-{n}"
    doc.setdefault("questions", []).append({
        "id": qid, "part": part,
        "title": f"Веса мягких правил {task} (повторный запрос)",
        "text": (_GOAL_WEIGHTS_SECTION.format(task=task)
                 + " Перечисленные строки остались без веса после "
                   "ранее принятого ответа — задайте вес каждой: "
                 + ", ".join(str(r.get("id")) for r in rules) + "."),
        "answer_format": {"type": "registry",
                          "note": "веса мягких правил"},
        "rule_refs": [r["id"] for r in rules],
        "source_refs": _uniq_source_refs(rules),
    })
    for r in rules:
        r.setdefault("blockers", []).append(qid)
    return qid


def _param_conflict_contains_mapping(value):
    """Mappings anywhere in JSON lists require the group's raw relation."""
    if isinstance(value, dict):
        return True
    return (isinstance(value, list)
            and any(_param_conflict_contains_mapping(item) for item in value))


def _param_conflict_values_equal(left, right, *, code_list=False):
    """Use the existing canonical contract only for a typed code-list group.

    Raw/legacy scalars and mappings keep their prior equality; in particular,
    do not recursively canonicalize a mapping's string/list values.
    """
    if code_list:
        from cdpm.contradiction import _canon_value
        return _canon_value(left) == _canon_value(right)
    if isinstance(left, list) and isinstance(right, list):
        return (all(any(_param_conflict_values_equal(x, y) for y in right)
                    for x in left)
                and all(any(_param_conflict_values_equal(y, x) for x in left)
                        for y in right))
    if isinstance(left, dict) and isinstance(right, dict):
        return (left.keys() == right.keys()
                and all(_param_conflict_values_equal(left[k], right[k])
                        for k in left))
    return left == right


def _conflict_source_refs(items, values, *, code_list=False):
    out, seen = [], set()
    for v in values:
        for r, p in items:
            if not _param_conflict_values_equal(
                    p.get("value"), v, code_list=code_list):
                continue
            for src in (r.get("sources") or [])[:1]:
                key = (src.get("sid"), src.get("anchor"))
                if key not in seen:
                    seen.add(key)
                    out.append(dict(src))
            break
    # Обе стороны из ОДНОЙ клаузы (один якорь) — валидатор
    # части 1 всё равно требует две ссылки: добираем без
    # дедупликации.
    if len(out) < 2:
        for r, _p in items:
            for src in (r.get("sources") or [])[:1]:
                out.append(dict(src))
            if len(out) >= 2:
                break
    return out


def ensure_param_conflict_questions(doc: dict) -> list[str]:
    """Хвост Лайта 15 (живой случай 2026-08-23): ДВА незаблокированных
    правила действующих видов несут ОДИН параметр (имя, уточнитель) с
    РАЗНЫМИ числами — min_rest_home 720 жёстким правилом и 750
    правилом данных; прогон применял 720, правило 750 жило и
    нарушалось, а видно это стало только сертификату. Конфликт
    обязан блокироваться вопросом ЧАСТИ 1 (противоречие источников)
    ещё у производителя — детерминированно, по машинным полям, без
    модели. Блокируются ВСЕ строки-носители (как штатный поиск
    противоречий). Возвращает id созданных вопросов."""
    ACTIVE = ("HARD", "DATA", "DEF")
    carriers: dict = {}
    for r in doc.get("rules", []):
        if not isinstance(r, dict) or r.get("blockers")                 or r.get("kind") not in ACTIVE:
            continue
        seen_names: set = set()
        for p in (r.get("params") or []):
            if not isinstance(p, dict):
                continue
            key = (p.get("name"), p.get("qualifier"))
            if not isinstance(key[0], str):
                continue
            seen_names.add(key[0])
            carriers.setdefault(key, []).append((r, p))
        # П6 (2026-09-02): параметры посадки A/B — такие же носители
        # значений (у эталонных сводов params пуст, значение только в
        # template_fit.params); уточнителя у посадки нет.
        fit = r.get("template_fit")
        if isinstance(fit, dict) and fit.get("category") in ("A", "B"):
            for name, cell in (fit.get("params") or {}).items():
                if name in seen_names or not isinstance(cell, dict):
                    continue
                val = rule_param_cells(r).get(name)
                carriers.setdefault((str(name), None), []).append(
                    (r, {"name": str(name), "value": val,
                         "unit": cell.get("unit"),
                         "as_written": cell.get("quote"),
                         "qualifier": None}))
    existing = {q.get("id") for q in doc.get("questions", [])}
    created: list[str] = []
    for (name, qual), items in sorted(carriers.items(),
                                      key=lambda kv: str(kv[0])):
        # K-5: A/B landing cells can contain code_list or mapping values.
        # Use the same equality as option/source grouping below; hashing
        # crashes on containers, while string/JSON keys can change equality
        # for existing mixed scalars (e.g. 1 == 1.0 == True).
        # Z-14: code_list uses contradiction.evidence's element canon too,
        # not merely its set ordering. The whole group must declare that
        # contract: unknown/mixed units are not silently canonicalized.
        # Mappings, including those inside nested lists, retain structural
        # key-order equality and case-sensitive values. One such carrier
        # selects the same raw relation for every value in this group.
        code_list = all(p.get("unit") == "code_list"
                        and isinstance(p.get("value"), list)
                        and not _param_conflict_contains_mapping(p["value"])
                        for _r, p in items)
        values = []
        for _r, p in items:
            value = p.get("value")
            if not any(_param_conflict_values_equal(
                           value, old, code_list=code_list)
                       for old in values):
                values.append(value)
        values.sort(key=str)
        if len(values) < 2:
            continue
        suffix = f"парам-{name}" + (f"-{qual}" if qual else "")
        qid = stable_question_id("1", suffix)
        if qid in existing:
            continue
        options = []
        for i, v in enumerate(values):
            letter = "абвгде"[i] if i < 6 else str(i)
            rids = ", ".join(str(r.get("id")) for r, p in items
                             if _param_conflict_values_equal(
                                 p.get("value"), v, code_list=code_list))
            aw = "; ".join(sorted({str(p.get("as_written"))
                                   for _r, p in items
                                   if _param_conflict_values_equal(
                                       p.get("value"), v, code_list=code_list)}))
            options.append({"key": letter,
                            "text": f"действует {aw} "
                                    f"(строки: {rids})"})
        doc.setdefault("questions", []).append({
            "id": qid, "part": "1",
            "title": f"Разные значения параметра {name}"
                     + (f" ({qual})" if qual else ""),
            "text": (f"Правила свода несут параметр {name}"
                     + (f" с уточнителем «{qual}»" if qual else "")
                     + f" с разными значениями: "
                     + ", ".join(str(v) for v in values)
                     + ". Какое значение действует?"),
            "answer_format": {"type": "choice", "options": options},
            "rule_refs": [r["id"] for r, _p in items],
            # Противоречие части 1 несёт трассировку ОБЕИХ
            # сторон: по одному источнику от каждого
            # значения-носителя (валидатор требует две).
            "source_refs": _conflict_source_refs(items,
                                                 values, code_list=code_list),
        })
        for r, _p in items:
            bl = r.setdefault("blockers", [])
            if qid not in bl:
                bl.append(qid)
        created.append(qid)
        existing.add(qid)
    return created


def _canon_rule_ref(rid):
    """Канон ссылки проверки на правило: компилятор порой заключает
    якорную часть в квадратные скобки — два написания одного id
    рождали ДВА вопроса данных (живой дубль run-0044/0046)."""
    if isinstance(rid, str):
        return rid.replace("[", "").replace("]", "")
    return rid


_MISSING_DATA_MARKERS = ("нет листа", "нет колонки", "нет колонок",
                         "нет справочника", "в решении нет",
                         "в книге нет", "не размечены")


def _executor_covers(rule: dict) -> bool:
    """Есть ли у правила ИСПОЛНИТЕЛЬ: валидная посадка (категории A/B)
    на семью, за которой каталог ОБЪЯВИЛ исполнителя (закон 1,
    `bridge.family_has_executor`). Такое правило ограничивает решение, и
    его соблюдение судит сертификат решения — по данным решения, а не по
    входной книге.

    Кого судит этот предикат: ЛИНИЮ ДАННЫХ (задавать ли авиакомпании
    вопрос «данных нет — считать ли до их получения?» и компилировать ли
    проверку по входной книге). Здесь работает именно ОБЪЯВЛЕНИЕ, а не
    подтверждённая поддержка (закон 2, `bridge.family_support`):
    поправка 12.11 контракта П0 и находка F2 неавторского ревью
    12.09.2026. Правило объявленной семьи БЕЗ подтверждённой поддержки
    останавливает K06-барьер со своим следом (как пробел покрытия) —
    авиакомпанию спрашивать не о чем: данных у неё не хватает не
    больше, чем было, а вопрос «считать ли без данных» предлагал бы
    ложный выбор (ответ «да» снял бы с расчёта действующее
    ограничение), и после появления поддержки он оставался бы открытым,
    держа готовность на «б» по устаревшему основанию.

    Исполнимость (закон 2) судят `support_problems`, готовность, гейт
    производителя и рубеж выпуска — там и только там."""
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return False
    fam = fit.get("family")
    return bool(fam) and bool(_catalog_bridge.family_has_executor(str(fam)))


def ensure_missing_data_questions(doc: dict, report: dict) -> list:
    """Publish missing-data editions and retire targets only on positive evidence."""
    from . import missing_data
    return missing_data.ensure(doc, report)


# Условие в формулировке снятого носителя (живой класс: условный
# пол отдыха при большой разнице часовых поясов).
_REINSTATE_COND_RE = re.compile(
    r"разниц\w*\s+(?:в\s+)?час\w*\s+пояс\w*", re.IGNORECASE)


def ensure_reinstate_questions(doc: dict) -> list:
    """Живой случай одной из линий, run-0060 (2026-08-27):
    вопрос-конфликт значений применён выбором одного значения, а
    СНЯТЫЕ им носители другого
    значения несут в формулировке УСЛОВИЕ (разница часовых поясов) —
    возможно, это условное правило, действующее НАРЯДУ с базовым, а
    не конфликт. Строка-кандидат получает штатную условную ветвь
    «вернуть в действие / снято верно» с вопросом части 1 следующей
    ревизии; возврат — ТОЛЬКО ответом авиакомпании (код правило не
    воскрешает). Один вопрос на семейство (имя параметра, значение);
    кандидат — носитель с уточнителем, иначе первый по id."""
    families: dict = {}
    for r in doc.get("rules", []):
        if r.get("kind") != "OUT" or r.get("conditional"):
            continue
        note = str(r.get("note") or "")
        if "отменено: выбрано значение" not in note:
            continue
        if "пересмотр" in note:
            continue              # семейство уже под вопросом
        if not _REINSTATE_COND_RE.search(
                str(r.get("statement") or "")):
            continue
        for p in (r.get("params") or []):
            if isinstance(p.get("value"), int) \
                    and not isinstance(p.get("value"), bool):
                families.setdefault(
                    (str(p.get("name")), p["value"]), []).append(r)
                break
    existing = {q.get("id") for q in doc.get("questions", [])}
    created: list = []
    for (pname, val) in sorted(families):
        members = families[(pname, val)]
        members.sort(key=lambda r: (
            0 if any(p.get("qualifier")
                     for p in (r.get("params") or [])) else 1,
            str(r.get("id"))))
        carrier = members[0]
        rid = str(carrier.get("id"))
        qid = stable_question_id("1", f"пересмотр-{rid}")
        if qid in existing:
            continue
        others = [str(r.get("id")) for r in members[1:]]
        extra = (f" Прочие снятые носители того же значения "
                 f"({', '.join(others)}) остаются снятыми — при "
                 f"возврате действует один носитель." if others
                 else "")
        # Контракт части 1 (живой отказ одной из линий 2026-08-27):
        # противоречие требует НЕ МЕНЕЕ ДВУХ ссылок на источники —
        # собираются
        # источники ВСЕГО семейства носителей; единственный источник
        # — вопрос честно не задаётся, след на носителе.
        src_refs, seen_src = [], set()
        for r2 in members:
            for s in (r2.get("sources") or []):
                k2 = (s.get("sid"), s.get("anchor"))
                if k2 not in seen_src:
                    seen_src.add(k2)
                    src_refs.append(dict(s))
                if len(src_refs) >= 5:
                    break
        if len(src_refs) < 2:
            note0 = ("вопрос-пересмотр не задан: у семейства один "
                     "источник, а контракт части 1 требует двух")
            carrier["note"] = ((carrier.get("note", "") + "; "
                               + note0).strip("; "))
            continue
        doc.setdefault("questions", []).append({
            "id": qid, "part": "1",
            "title": f"Пересмотр: {pname} = {val} — условное "
                     f"правило?",
            "text": (f"Правило {rid} "
                     f"(«{carrier.get('statement') or ''}») "
                     f"было снято ответом на вопрос о конфликте "
                     f"значений {pname}. Формулировка несёт УСЛОВИЕ "
                     f"(разница часовых поясов) — возможно, это не "
                     f"конфликт, а условное правило, действующее "
                     f"НАРЯДУ с выбранным базовым значением."
                     f"{extra}"),
            "answer_format": {"type": "choice", "options": [
                {"key": "а", "text": "условное правило действует — "
                                     "вернуть строку в действие "
                                     "наряду с базовым значением"},
                {"key": "б", "text": "снято верно — условие не "
                                     "действует"},
            ]},
            "rule_refs": [rid],
            "source_refs": src_refs,
        })
        carrier["conditional"] = {"question": qid,
                                  "branches": {"а": "HARD",
                                               "б": "OUT"}}
        carrier["blockers"] = sorted(
            set(carrier.get("blockers") or []) | {qid})
        note2 = ("вопрос-пересмотр задан: формулировка несёт "
                 "условие, снятие могло быть трактовкой конфликта")
        carrier["note"] = ((carrier.get("note", "") + "; " + note2)
                           .strip("; "))
        for r2 in members[1:]:
            extra2 = f"пересмотр семейства задан вопросом {qid}"
            r2["note"] = ((r2.get("note", "") + "; " + extra2)
                          .strip("; "))
        created.append(qid)
        existing.add(qid)
    return created


# Волна условий (этап 5б, 2026-08-27): темы файла условий расчёта,
# чьи таблицы при извлечении легли в модель обрывками (по одной
# ячейке) либо только снятыми/словесными носителями. Достраивать
# таблицу миграцией нельзя (это угадывание, не подъём числа из
# формулировки) — данные возвращаются штатным каналом: вопрос
# анкеты → ответ авиакомпании → обработчик вносит машинную форму.
def _conditions_topics(customer=None) -> tuple:
    """Темы файла условий — данные КОНТУРА (решение владельца
    2026-08-29, пункт 2: тот же класс, что гарантированные вопросы;
    второй носитель того же механизма). Файл
    cdpm-conditions-topics.json в каталоге данных, переопределение —
    CDPM_CONDITIONS_TOPICS (пусто = механизм спит); форма записей и
    отказ на кривом файле — как у _clarification_topics. Ключи тем
    (id вопросов) — машинная связка с применением ответов
    (vmp/conditions_export) — живут в носителе и сохраняются при
    выносе."""
    return _contour_topics("CDPM_CONDITIONS_TOPICS",
                           "cdpm-conditions-topics.json",
                           "темы файла условий", customer)


def ensure_sit_conflict_question(doc: dict) -> list:
    """Конфликт стыковки (живой урок кнопки №15, 2026-08-27): в
    документах два числа минимальной стыковки — машинный HARD-носитель
    и ДРУГОЕ число прозой в действующих правилах со словом «стыковка».
    Наихудшая трактовка (большее) убила короткие развороты
    (непокрытых стало заметно больше) — выбор числа не машинный, а
    вопрос авиакомпании. Вопрос ставится, когда действующий машинный
    носитель
    стыковки сосуществует с ДРУГИМ числом стыковки в формулировках
    действующих правил без параметра; ответ вносит обработчик
    (патчи затронутых строк). Идемпотентна."""
    qid = stable_question_id("3a", "условия-стыковка")
    if any(q.get("id") == qid for q in doc.get("questions", [])):
        return []
    machine = []
    prose = []
    sitw = re.compile(r"стыковк", re.IGNORECASE)
    num = re.compile(r"(\d+:\d{2}|\d+(?:[.,]\d+)?\s*час|\d+\s*мин)",
                     re.IGNORECASE)
    for r in doc.get("rules", []):
        if r.get("kind") not in ("HARD", "DATA", "DEF"):
            continue
        text = str(r.get("statement") or "")
        has_p = [p for p in (r.get("params") or [])
                 if p.get("name") == "min_turnaround"]
        cells = rule_param_cells(r)
        if has_p:
            machine.append((r, has_p[0]))
        elif "min_turnaround" in cells:
            # Посадка A/B — такой же машинный носитель (П6, 2026-09-02).
            machine.append((r, {"name": "min_turnaround",
                                "value": cells["min_turnaround"]}))
        elif sitw.search(text) and num.search(text):
            prose.append(r)
    if not machine or not prose:
        return []
    src_refs, seen = [], set()
    for r in [m[0] for m in machine] + prose:
        for s in (r.get("sources") or [])[:1]:
            k = (s.get("sid"), s.get("anchor"))
            if k not in seen:
                seen.add(k)
                src_refs.append(dict(s))
        if len(src_refs) >= 3:
            break
    vals = ", ".join(sorted({f"{p['value']} мин"
                             for _r, p in machine}))
    doc.setdefault("questions", []).append({
        "id": qid, "part": "3a",
        "title": "Конфликт значений минимальной стыковки",
        "text": (f"В документах разные величины минимальной стыковки "
                 f"экипажа: машинно принято {vals} "
                 f"({', '.join(str(r.get('id')) for r, _p in machine)}), "
                 f"а в формулировках действующих правил "
                 f"({', '.join(str(r.get('id')) for r in prose[:4])}) "
                 f"— другое число. Назовите минимальную стыковку "
                 f"экипажа между рейсами (минуты) и укажите для "
                 f"каждого расходящегося правила, относится ли оно к "
                 f"стыковке экипажа (если нет — строка снимается)."),
        "answer_format": {"type": "value",
                          "note": "минуты + вердикт по правилам"},
        "rule_refs": [str(r.get("id")) for r, _p in machine]
        + [str(r.get("id")) for r in prose[:4]],
        "source_refs": src_refs,
    })
    return [qid]


def ensure_criterion_question(doc: dict) -> list:
    """Вопрос о машинном критерии оптимизации пейринга (приказ
    владельца 2026-08-28: расчёт — по критерию авиакомпании 0096;
    в своде машинного носителя вида/ставок/версии нет — носителем
    становится ответ анкеты, применяется машинно). Якорь — вопрос
    целевой пейринга (Q-3a-GOAL): его строки и источники наследуются.
    Идемпотентна; без GOAL-вопроса не ставится (чужая модель)."""
    qid = stable_question_id("3a", "критерий-пейринга")
    existing = {q.get("id") for q in doc.get("questions", [])}
    goal = _goal_question(doc, "3a", strict=True)
    if goal is None:
        return []
    # ИДАК H0-К (2026-09-04): авиакомпания ПОДТВЕРДИЛА, что критерия
    # нет (применённый отказ у Q-3a-GOAL) — спрашивать машинный критерий
    # значило бы спорить с применённым ответом; режим — взвешенные
    # штрафы, вопрос не ставится.
    if goal_answer_declines(goal):
        return []
    # П6 (2026-09-02): критерий, названный ДОКУМЕНТОМ (блок criterion
    # целевой пейринга), вопроса не рождает — о написанном не спрашивают.
    if criterion_from_objectives(doc) is not None:
        return []
    if qid in existing:
        # Ремень (б), вопрос-ДОБОР (прецедент «повторного запроса
        # весов»): все вопросы семейства отвечены, но машинный блок
        # ни из одного не разобрался — критерий по-прежнему без
        # носителя. Пере-ответ поверх применённого запрещён отказом
        # apply_answers, поэтому честный путь — новый вопрос
        # следующей ревизии с точным указанием, чего не хватило.
        fam = [x for x in doc.get("questions", [])
               if str(x.get("id") or "").startswith(qid)]
        if any((x.get("answer") or {}).get("status")
               not in ("применён", "не разобран") for x in fam):
            return []          # есть неотвеченный — добор не нужен
        if _criterion_from_answer(doc) is not None:
            return []          # носитель уже есть
        n = len(fam) + 1
        qid = f"{qid}-{n}"
        if qid in existing:
            return []
        doc.setdefault("questions", []).append({
            "id": qid, "part": "3a",
            "title": "Машинный критерий оптимизации пейринга "
                     "(добор)",
            "text": ("Прежний ответ о критерии принят как текст, но "
                     "машинный JSON-блок из него не разобрался — "
                     "критерий остался без машинного носителя. "
                     "Пришлите, пожалуйста, точный JSON-блок: "
                     "criterion (вид из словаря COSMOS), параметры "
                     "вида (например rate_per_duty — целое), rule "
                     "(правило-источник), version и effective_from."),
            "answer_format": {"type": "value",
                              "note": "точный JSON-блок критерия"},
            "rule_refs": list(goal.get("rule_refs") or []),
            "source_refs": [dict(s) for s in
                            (goal.get("source_refs") or [])],
        })
        return [qid]
    doc.setdefault("questions", []).append({
        "id": qid, "part": "3a",
        "title": "Машинный критерий оптимизации пейринга",
        "text": ("Назовите критерий оптимизации пейринга машинно: "
                 "вид (например, нормированная стоимость по рангам), "
                 "формулу стоимости цепочки, ставки по рангам, "
                 "версию ставок и дату начала действия. Машинный "
                 "JSON-блок в ответе приветствуется — он будет "
                 "прочитан дословно."),
        "answer_format": {"type": "value",
                          "note": "вид + формула + ставки + версия "
                                  "+ дата действия (JSON допустим)"},
        "rule_refs": list(goal.get("rule_refs") or []),
        "source_refs": [dict(s) for s in
                        (goal.get("source_refs") or [])],
    })
    return [qid]


def _ensure_topic_question(doc, qid, q, carriers, title, text, note):
    """Shared append-only writer for conditions and clarification topics."""
    from . import question_editions

    carriers.sort(key=lambda r: str(r.get("id")))
    refs = {str(r["id"]) for r in carriers}
    content = {
        "title": title,
        "text": text,
        "answer_format": {"type": "value", "note": note},
        "source_refs": question_editions.normative_rule_sources(
            carriers, q.get("source_refs", []) if q else []),
    }
    if q is None:
        doc.setdefault("questions", []).append({
            "id": qid, "part": "3a", "rule_refs": sorted(refs), **content})
        return qid
    # Even a legacy answer is evidence of publication, not a mutable draft.
    if q.get("answer") and "edition" not in q:
        question_editions.freeze(q)
    refs |= question_editions.targets(q)
    delta = {k: v for k, v in content.items() if q.get(k) != v}
    for field in ("title", "text"):
        if field in delta and (
                normalized_question_text(delta[field])
                == normalized_question_text(q.get(field, ""))):
            delta.pop(field)
    if "answer_format" in delta:
        old_format = q.get("answer_format") or {}
        if (old_format.get("type") == "value"
                and not old_format.get("options")
                and normalized_question_text(old_format.get("note", ""))
                == normalized_question_text(note)):
            delta.pop("answer_format")
    if refs == question_editions.targets(q) and not delta:
        return None
    if "edition" in q:
        new = question_editions.expand(doc, q, sorted(refs), content=delta)
        return new["id"]
    else:
        q.update(content, rule_refs=sorted(refs))
    return None


def ensure_conditions_questions(doc: dict) -> list:
    """Вопросы анкеты по темам файла условий расчёта (этап 5б).

    Вопрос задаётся только когда тема ПРЕДСТАВЛЕНА действующими
    правилами модели (чужой авиакомпании темы не навязываются) и
    область или нормативная форма ещё не задана текущим изданием.
    Опубликованный вопрос и его ответ не меняются: добор создаёт
    новое издание. Возвращает id новых вопросов/изданий."""
    from . import question_editions

    created: list = []
    customer = (doc.get("meta") or {}).get("customer")
    param_specs = _topic_param_specs("CDPM_CONDITIONS_TOPICS",
                                     "cdpm-conditions-topics.json",
                                     customer)
    for key, topic_re, title, text, note in _conditions_topics(customer):
        qid = stable_question_id("3a", key)
        q = question_editions.current_for_root(doc, qid)
        carriers = [r for r in doc.get("rules", [])
                    if r.get("kind") in ("HARD", "DATA", "DEF")
                    and topic_re.search(str(r.get("statement") or ""))]
        if not carriers:
            continue
        # Тема с машинным параметром МОЛЧИТ, когда ВСЕ носители его
        # уже несут (мини-пакет 31.08: универсальные темы — стыковка,
        # состав экипажа — живут в носителе навсегда и не мусорят чужим
        # прогонам лишним вопросом при уже внесённом значении). П6
        # (2026-09-02): носитель параметра — и посадка A/B, и старые
        # params (rule_param_cells): значение, записанное посадкой,
        # тоже «написано» — о нём не спрашивают.
        spec = param_specs.get(key)
        if spec and spec.get("name"):
            pname = spec["name"]
            missing = [r for r in carriers
                       if pname not in rule_param_cells(r)]
            if not missing and q is None:
                continue
        new_id = _ensure_topic_question(doc, qid, q, carriers, title, text, note)
        if new_id is not None:
            created.append(new_id)
    return created


# Запрос владельца 2026-08-28 («ВМП не должен допускать
# двусмысленностей»): три предмета разбора сверки «правило ↔ шаблон».
# Каждая тема ставится только при живых правилах-носителях; id
# начинаются с Q-3a-условия- (машинное применение ответов как у тем
# файла условий). Формулировки показывают противоречие/развилку
# и живое следствие — по урокам дня.
def _clarification_topics(customer=None) -> tuple:
    """Гарантированные вопросы анкеты — данные КОНТУРА (формулировки,
    термины и пороги конкретного заказчика), носитель — файл
    cdpm-ensure-questions.json в каталоге данных ВНЕ git (дефект
    границы, найденный владельцем 2026-08-29: содержимое заказчика в
    коде CDPM запрещено — код несёт только механизм). Конвенция
    носителей контура: form_version/customer/_comment; НЕТ файла или
    пустая таблица topics = механизм спит; кривой файл = честный
    отказ словами. Путь переопределяется переменной окружения
    CDPM_ENSURE_QUESTIONS (пустое значение = механизм спит) —
    изоляция тестов: набор судит МЕХАНИЗМ на синтетической таблице,
    не данные контура."""
    return _contour_topics("CDPM_ENSURE_QUESTIONS",
                           "cdpm-ensure-questions.json",
                           "гарантированные вопросы контура", customer)


_DURATION_RE = re.compile(
    r"(?:(\d+)\s*(?:час|ч\b))?\s*(?:(\d+)\s*мин)?|(\d{1,2}):(\d{2})")


def _parse_duration_span(raw: str) -> tuple[int, str] | None:
    """«10 часов», «40 минут», «10 ч 30 мин», «10:30» → (минуты,
    ДОСЛОВНЫЙ совпавший след); не разобралось — None (не выдумываем).
    След короткий по построению — только совпавшие фрагменты, без
    хвостов-пояснений ответа: контракт модели сверяет канонизацию
    as_written строгим полным совпадением (живой отказ круга 2
    run-0064: след «0 минут (все времена…)» не канонизировался)."""
    s = str(raw or "").strip()
    mm = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if mm:
        return int(mm.group(1)) * 60 + int(mm.group(2)), s
    low = s.lower()
    h = re.search(r"(\d+)\s*(?:час\w*|ч\b)", low)
    m_ = re.search(r"(\d+)\s*мин\w*", low)
    if not h and not m_:
        return None
    val = (int(h.group(1)) * 60 if h else 0) \
        + (int(m_.group(1)) if m_ else 0)
    trace = " ".join(s[g.start():g.end()] for g in (h, m_) if g)
    return val, trace


def _parse_duration_minutes(raw: str) -> int | None:
    got = _parse_duration_span(raw)
    return got[0] if got else None


def _topic_param_specs(env_name: str, filename: str,
                       customer=None) -> dict:
    """{key темы: {name, unit, relation}} для тем носителя с машинным
    параметром (ремень (а) ночи демо 2026-08-29): value-ответ темы
    вносится в правило ДЕТЕРМИНИРОВАННО, без модели. Ограждение по
    заказчику (applies_to) — то же, что у тем (ревью r55)."""
    path = _carrier_path(env_name, filename)
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return {}
    if not isinstance(raw, dict) or not _carrier_applies(raw, customer):
        return {}
    out = {}
    for t in (raw.get("topics") or []):
        if isinstance(t, dict) and t.get("param_name"):
            out[str(t.get("key"))] = {
                "name": str(t["param_name"]),
                "unit": str(t.get("param_unit")
                            or "duration_minutes"),
                "relation": str(t.get("param_relation") or "min"),
                # Прод-демо run-0001 (30.08): тема-ОПРЕДЕЛЕНИЕ мира
                # (шкала времени) вправе повышать вид правила-носителя
                # DATA→DEF — потребитель применяет параметры только
                # видов HARD и DEF, и параметр-определение в
                # DATA-строке мёртв. Класс, не экземпляр: вид несёт
                # носитель темы, код только исполняет.
                "rule_kind": (str(t["param_rule_kind"])
                              if t.get("param_rule_kind")
                              in ("DEF", "HARD") else None)}
    return out


def apply_topic_value_answers(doc: dict) -> list[str]:
    """Ремень (а): применённый value-ответ на вопрос темы носителя с
    param_name вносит машинный параметр в правила-носители вопроса
    КОДОМ (двойная вариативность LLM-применения за вечер 29.08 —
    класс закрыт детерминизмом). Только duration_minutes; значение
    не разобралось — честно пропускаем (LLM-путь остаётся).
    Идемпотентно."""
    from . import question_editions

    specs = _topic_param_specs("CDPM_CONDITIONS_TOPICS",
                               "cdpm-conditions-topics.json",
                               (doc.get("meta") or {}).get("customer"))
    if not specs:
        return []
    rules_by_id = {r.get("id"): r for r in doc.get("rules", [])
                   if isinstance(r, dict)}
    notes: list[str] = []
    # Validate every selected lineage before changing any rule. A pending or
    # invalid successor must never reactivate an old applied answer.
    selected = []
    for key, spec in specs.items():
        for part in ("3a", "3b"):
            q = question_editions.current_for_root(doc, stable_question_id(part, key))
            if q is not None:
                selected.append((q, spec))
    for q, spec in selected:
        qid = q["id"]
        a = q.get("answer") or {}
        if a.get("status") != "применён":
            continue
        got = _parse_duration_span(str(a.get("raw") or ""))
        if got is None:
            continue
        val, trace = got
        for rid in sorted(question_editions.targets(q)):
            r = rules_by_id.get(rid)
            if r is None:
                continue
            promote = spec.get("rule_kind")
            if promote and r.get("kind") == "DATA":
                r["kind"] = promote
                align_fit_with_kind(doc, r,
                                    "вид повышен темой носителя")
                r["note"] = ((r.get("note", "") + "; вид повышен "
                              f"DATA→{promote} темой носителя "
                              f"({qid}): параметр-определение мира")
                             .strip("; "))
                notes.append(f"{rid}: вид DATA→{promote} "
                             f"(тема {qid})")
            params = r.setdefault("params", []) or []
            if spec["name"] in rule_param_cells(r):
                continue        # носитель уже несёт (params или посадка)
            params.append({
                "name": spec["name"], "value": val,
                "unit": spec["unit"], "relation": spec["relation"],
                "as_written": trace})
            r["params"] = params
            notes.append(f"{rid}: параметр {spec['name']}={val} "
                         f"внесён из ответа темы ({qid})")
    return notes


def _carrier_path(env_name: str, filename: str):
    import os
    override = os.environ.get(env_name)
    if override is not None:
        if not override.strip():
            return None
        return Path(override)
    import _data_paths
    return _data_paths.data_root() / filename


def _carrier_applies(raw: dict, customer) -> bool:
    """Носитель контура применяется к прогону заказчика, если поле
    applies_to (список имён заказчиков) отсутствует или пусто —
    носитель общий; иначе — только при совпадении имени заказчика
    свода (без регистра и лишних пробелов). П12 (ревью 2026-09-02):
    темы одного заказчика не должны формировать анкету другого."""
    applies = raw.get("applies_to") if isinstance(raw, dict) else None
    if not applies:
        return True
    if not isinstance(applies, list):
        return False
    me = " ".join(str(customer or "").split()).casefold()
    return any(" ".join(str(a).split()).casefold() == me for a in applies)


def contour_carriers_report(customer=None) -> list:
    """След носителей контура для сводок прогона: файл, метка customer
    носителя, applies_to, применён ли к этому заказчику, число тем."""
    out: list = []
    for env_name, filename in (
            ("CDPM_CONDITIONS_TOPICS", "cdpm-conditions-topics.json"),
            ("CDPM_ENSURE_QUESTIONS", "cdpm-ensure-questions.json")):
        path = _carrier_path(env_name, filename)
        if path is None or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                 # noqa: BLE001
            out.append({"file": path.name, "readable": False})
            continue
        applies = raw.get("applies_to") if isinstance(raw, dict) else None
        bad_form = applies not in (None, [], "") and (
            not isinstance(applies, list)
            or not all(isinstance(a, str) and a.strip() for a in applies))
        out.append({"file": path.name,
                    "customer": (raw.get("customer")
                                 if isinstance(raw, dict) else None),
                    "applies_to": applies,
                    "applied": (False if bad_form
                                else _carrier_applies(raw, customer)),
                    "form_error": ("applies_to не список имён заказчиков"
                                   if bad_form else None),
                    "topics": len((raw.get("topics") or [])
                                  if isinstance(raw, dict) else [])})
    return out


def _contour_topics(env_name: str, filename: str,
                    label: str, customer=None) -> tuple:
    """Общий загрузчик таблиц тем из носителей контура данных
    (конвенция 2026-08-29): пусто в env = спит; нет файла = спит;
    кривой файл = честный отказ словами; каждая тема — пять строковых
    полей, регекс компилируется. Носитель с applies_to применяется
    только к названным заказчикам (_carrier_applies)."""
    path = _carrier_path(env_name, filename)
    if path is None or not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                                # noqa: BLE001
        raise ValueError(
            f"{label}: файл не читается ({path.name}): {e}")
    topics = raw.get("topics") if isinstance(raw, dict) else None
    if not isinstance(topics, list):
        raise ValueError(
            f"{label}: без таблицы topics ({path.name}) — форма "
            f"нарушена")
    applies = raw.get("applies_to")
    if applies not in (None, [], "") and (
            not isinstance(applies, list)
            or not all(isinstance(a, str) and a.strip() for a in applies)):
        raise ValueError(f"{label}: applies_to ({path.name}) — не список "
                         f"имён заказчиков, форма нарушена")
    if not _carrier_applies(raw, customer):
        return ()
    out = []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            raise ValueError(f"{label}: тема {i} — не объект")
        fields = {}
        for name in ("key", "trigger_regex", "title", "text", "note"):
            v = t.get(name)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(
                    f"{label}: тема {i} — поле {name} пустое или "
                    f"не строка")
            fields[name] = v
        try:
            rx = re.compile(fields["trigger_regex"], re.IGNORECASE)
        except re.error as e:
            raise ValueError(
                f"{label}: тема {fields['key']!r} — trigger_regex "
                f"не компилируется: {e}")
        out.append((fields["key"], rx, fields["title"],
                    fields["text"], fields["note"]))
    return tuple(out)


def ensure_pairing_clarification_questions(doc: dict) -> list:
    """Вопросы-уточнения по предметам сверки «правило ↔ шаблон»
    (запрос владельца 2026-08-28). Темы — из файла контура данных
    (_clarification_topics); механика — как у тем файла условий:
    тема ставится только при живых правилах-носителях. Добор области
    или источников публикуется новым изданием, прежний ответ не
    переносится. Возвращает id созданных вопросов/изданий."""
    from . import question_editions

    created: list = []
    customer = (doc.get("meta") or {}).get("customer")
    for key, topic_re, title, text, note in _clarification_topics(customer):
        qid = stable_question_id("3a", key)
        q = question_editions.current_for_root(doc, qid)
        carriers = [r for r in doc.get("rules", [])
                    if r.get("kind") in ("HARD", "DATA", "DEF", "CASE")
                    and topic_re.search(str(r.get("statement") or ""))]
        if not carriers:
            continue
        new_id = _ensure_topic_question(doc, qid, q, carriers, title, text, note)
        if new_id is not None:
            created.append(new_id)
    return created


# Консенсус CDPM/COSMOS 2026-08-28 (счёт наихудших трактовок,
# постановка владельца «ВМП не допускает неоднозначностей»):
# миграции ниже закрывают подтверждённые недочёты выпуска
# детерминированно, с носителями в note.

def _migration_params() -> dict:
    """Параметры миграций выпуска — данные КОНТУРА (решение владельца
    2026-08-29, пункт 1: координата правила свода конкретного
    заказчика не живёт константой кода). Файл
    cdpm-migration-params.json в каталоге данных; переопределение —
    CDPM_MIGRATION_PARAMS (пусто = параметров нет, зависящие миграции
    спят); кривой файл = честный отказ словами."""
    import os
    override = os.environ.get("CDPM_MIGRATION_PARAMS")
    if override is not None:
        if not override.strip():
            return {}
        path = Path(override)
    else:
        import _data_paths
        path = _data_paths.data_root() / "cdpm-migration-params.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                                # noqa: BLE001
        raise ValueError(
            f"параметры миграций контура не читаются "
            f"({path.name}): {e}")
    if not isinstance(raw, dict):
        raise ValueError(
            f"параметры миграций контура ({path.name}): форма "
            f"нарушена — ожидается объект")
    return raw


def retire_frms_phantom_rules(doc: dict) -> list[str]:
    """Пункты 7–10 счёта: «правила» из ЗАГОЛОВКОВ КОЛОНОК вложенной
    таблицы (ось «число секторов»), к тому же вся таблица неприменима
    по условию первоисточника. Снятие в OUT с носителем;
    идемпотентно. Префикс фантомных id — параметр контура
    (frms_phantom_prefix в cdpm-migration-params.json): координата
    свода конкретного заказчика в коде не живёт; без параметра
    миграция честно спит (решение владельца 2026-08-29)."""
    prefix = _migration_params().get("frms_phantom_prefix")
    if not isinstance(prefix, str) or not prefix:
        return []
    notes: list[str] = []
    for r in doc.get("rules", []):
        rid = str(r.get("id") or "")
        if not rid.startswith(prefix):
            continue
        if r.get("kind") == "OUT":
            continue
        r["kind"] = "OUT"
        align_fit_with_kind(doc, r, "снят фантом FRMS")
        r["note"] = ((str(r.get("note") or "") + "; ").lstrip("; ")
                     + "снято миграцией 2026-08-28: заголовок колонки "
                       "вложенной таблицы (ось «число секторов»), а "
                       "вся таблица неприменима по условию "
                       "первоисточника")
        notes.append(f"{rid}: снят в OUT (фантом заголовка "
                     f"неприменимой таблицы FRMS)")
    return notes


_WOCL_SECTORS_RE = re.compile(
    r"максимум\s+(\d)\s+сектор", re.IGNORECASE)


def restore_wocl_sector_params(doc: dict) -> list[str]:
    """Пункты 4–6 счёта: правила пределов секторов по окну минимальной
    циркадной активности (S2-стр-6-абз-18..20) без params — значение
    поднимается из формулировки («Максимум N секторов…»);
    детерминированно, идемпотентно."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        if r.get("kind") not in ("HARD", "DEF") or r.get("params"):
            continue
        # Ячейка посадки — такой же носитель имени (A05, В-2 ревью).
        if "max_landings_per_duty" in rule_param_cells(r):
            continue
        stmt = str(r.get("statement") or "")
        if "циркадн" not in stmt.lower():
            continue
        mm = _WOCL_SECTORS_RE.search(stmt)
        if not mm:
            continue
        r["params"] = [{
            "name": "max_landings_per_duty",
            "value": int(mm.group(1)), "unit": "count",
            "as_written": f"{mm.group(1)} секторов",
            "relation": "max"}]
        notes.append(f"{r.get('id')}: params max_landings_per_duty="
                     f"{mm.group(1)} из формулировки (окно МЦА)")
    return notes


def restore_night_window_tz(doc: dict) -> list[str]:
    """Пункты 14–15 счёта: формулировка ночного окна однозначна
    («в часовом поясе, в котором член экипажа является
    акклиматизированным» — S2-стр-3-абз-14 ≡ S3-стр-3-абз-14), а
    параметры несли tz='base-local'. Параметр приводится к правде
    документа; идемпотентно."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        stmt = str(r.get("statement") or "")
        if "поясе акклиматизации" not in stmt \
                and "является акклиматизированным" not in stmt \
                and "акклиматизации члена экипажа" not in stmt:
            continue
        for p in (r.get("params") or []):
            if str(p.get("name") or "").startswith("night_duty_") \
                    and p.get("tz") == "base-local":
                p["tz"] = "acclimatized"
                notes.append(f"{r.get('id')}: {p.get('name')} tz "
                             f"base-local -> acclimatized (правда "
                             f"документа)")
    return notes


def ensure_data_scope_questions(doc: dict, report: dict) -> list:
    """Хвост Лайта 16 (живой случай ARH, 2026-08-23): операционные
    данные несут значение вне ЗАКРЫТОГО ПЕРЕЧНЯ свода (база, которой
    свод не знает) — 39 нарушений на решении, а спросить авиакомпанию
    никто не догадался. Находки проверок «перечень-значений» с
    основанием-правилом становятся вопросом части 3b: добавить
    значение в свод или исключить записи из планирования. БЕЗ
    блокировки правила: перечень свода верен до ответа, данные уже
    честно краснеют находкой. Возвращает id созданных вопросов."""
    import re as _re
    from . import question_editions, data_scope
    checks = {c.get("id"): c for c in report.get("checks", [])}
    offenders: dict = {}
    for f in report.get("findings", []):
        c = checks.get(f.get("check"))
        if not c or c.get("template") != "перечень-значений":
            continue
        rid = _canon_rule_ref((c.get("basis") or {}).get("rule"))
        if not rid:
            continue
        # New reports carry the exact compared value, not a parsed display.
        # Read old reports conservatively; never split a value on commas.
        value = f.get("scope_value")
        if "scope_value" not in f:
            mm = _re.search(r"«([^»]+)» вне перечня", str(f.get("text") or ""))
            value = mm.group(1) if mm else None
        if isinstance(value, str) and value:
            offenders.setdefault(rid, set()).add(value)
    rules_by_id = {r.get("id"): r for r in doc.get("rules", [])}
    created: list = []
    pending = []
    for rid, values in sorted(offenders.items()):
        rule = rules_by_id.get(rid)
        if rule is None:
            continue
        zone = rule.get("zone")
        part = "3a" if zone == "PAIRING" else "3b"
        qid = stable_question_id(part, f"данные-{rid}")
        current = question_editions.current_for_root(doc, qid)
        evidence = {"rule": rid, "values": sorted(values),
                    "statement": rule.get("statement") or ""}
        draft = {
            "id": qid, "part": part,
            "title": f"Данные вне перечня правила {rid}",
            **data_scope.wording(evidence),
            "data_scope": evidence,
            "rule_refs": [rid],
            "source_refs": question_editions.normative_rule_sources(
                [rule], current.get("source_refs", []) if current else []),
        }
        pending.append((current, draft))
    # Resolve and validate every current lineage before changing any question.
    for current, draft in pending:
        if current is None:
            doc.setdefault("questions", []).append(draft)
            created.append(draft["id"])
            continue
        if current.get("answer") and "edition" not in current:
            question_editions.freeze(current)
        delta = {k: v for k, v in draft.items()
                 if k not in ("id", "part", "rule_refs") and current.get(k) != v}
        for field in ("title",):
            if field in delta and normalized_question_text(delta[field]) == normalized_question_text(current.get(field, "")):
                delta.pop(field)
        if not delta:
            continue
        if "edition" in current:
            new = question_editions.expand(doc, current, draft["rule_refs"], content=delta)
            created.append(new["id"])
        else:
            current.update(delta)
    return created


_PENALTY_NO_WEIGHT_RE = re.compile(
    r"(?:вес|штраф)\w*[^.]{0,60}не\s+установлен", re.IGNORECASE)


_ANCHOR_ROW_RE = re.compile(
    r"^(?P<carrier>.*?)[\s,;]*строк\w*\s*\d+\s*$", re.IGNORECASE)


def _anchor_carrier(anchor) -> str | None:
    """Носитель якоря-записи: «лист «Заявки», строка 3» → «лист
    «Заявки»» (нормализовано). Якорь без хвоста «строка N» носителем
    записей не является."""
    mt = _ANCHOR_ROW_RE.match(str(anchor or "").strip())
    if not mt:
        return None
    carrier = " ".join(mt.group("carrier").split()).casefold() \
        .strip(" ,;")
    return carrier or None


def resolve_case_orphans(doc: dict) -> list[str]:
    """CASE-сироты (живой стоп run-0023 на гейте запуска, 1.09):
    вопрос-развилка «как читать записи листа» задан к строке-носителю
    и применён, носитель разрешён контрактом conditional, а ОДНОТИПНЫЕ
    записи того же листа остались CASE без привязки — гейт сверки ВМП
    честно держал запуск («CASE без машинных параметров»). Тот же
    сиротский класс — строки-«запросы к авиакомпании», рождённые
    заплатами обмена, чей предмет закрыт применённым вопросом целевой.

    Детерминированное разрешение БЕЗ гадания (fail-closed):
    (а) сирота, чьи якоря источников совпадают с якорями ПРИМЕНЁННОГО
        вопроса целевой зоны (Q-*-GOAL / вопрос критерия), — снимается
        в OUT со следом: предметом владеет целевая запись/критерий;
    (б) сирота-запись листа наследует вид РАЗРЕШЁННОЙ строки-носителя
        своего листа (общий носитель якоря, применённый вопрос-
        развилка) — записи листа читаются единообразно по смыслу
        такого вопроса; наследуются только машинно-безопасные виды
        (DATA/HARD/DEF/OUT — SOFT не наследуется: вес приходит только
        ответом, выдумывать его нельзя);
    всё прочее не трогается — пусть держит гейт. Сиротой считается
    CASE зоны решателя без conditional, без blockers и без ОТКРЫТОГО
    вопроса на нём (решение за авиакомпанией не подменяется).
    Идемпотентно: разрешённая строка перестаёт быть CASE."""
    solver_zones = {"PAIRING", "ROSTERING", "BOTH"}
    rules_by_id = {r.get("id"): r for r in doc.get("rules", [])}
    open_refs: set[str] = set()
    goal_anchors: dict[tuple, str] = {}
    carrier_donors: dict[tuple, list[tuple[str, str, str]]] = {}
    for q in doc.get("questions", []):
        qid = str(q.get("id"))
        ans = q.get("answer") or {}
        applied = ans.get("status") == "применён"
        if not applied:
            for rid in (q.get("rule_refs") or []):
                if isinstance(rid, str):
                    open_refs.add(rid)
            continue
        refs = [ref for ref in (q.get("source_refs") or [])
                if isinstance(ref, dict)]
        if is_goal_family(qid) or "-критерий-" in qid:
            for ref in refs:
                goal_anchors[(ref.get("sid"), ref.get("anchor"))] = qid
        if (q.get("answer_format") or {}).get("type") == "choice" \
                and ans.get("key"):
            for ref in refs:
                carrier = _anchor_carrier(ref.get("anchor"))
                if not carrier:
                    continue
                for rid in (q.get("rule_refs") or []):
                    donor = rules_by_id.get(rid)
                    if donor and donor.get("kind") in ("DATA", "HARD",
                                                       "DEF", "OUT"):
                        carrier_donors.setdefault(
                            (ref.get("sid"), carrier), []).append(
                            (qid, donor.get("kind"), rid))
    notes: list[str] = []
    for rule in doc.get("rules", []):
        if rule.get("kind") != "CASE":
            continue
        if rule.get("zone") not in solver_zones:
            continue
        if rule.get("conditional") or rule.get("blockers"):
            continue
        if rule.get("id") in open_refs:
            continue
        sources = [s for s in (rule.get("sources") or [])
                   if isinstance(s, dict)]
        hit_goal = next((goal_anchors[(s.get("sid"), s.get("anchor"))]
                         for s in sources
                         if (s.get("sid"), s.get("anchor"))
                         in goal_anchors), None)
        if hit_goal:
            rule["kind"] = "OUT"
            rule.pop("weight", None)
            rule.pop("export_solver", None)
            align_fit_with_kind(doc, rule, "снята целевой")
            extra = (f"строка-запрос закрыта применённым ответом "
                     f"анкеты ({hit_goal}): предметом владеет "
                     f"целевая запись/критерий")
            rule["note"] = ((rule.get("note", "") + "; "
                             + extra).strip("; "))
            notes.append(f"{rule.get('id')} → OUT ({hit_goal})")
            continue
        donors = {d for s in sources
                  for d in carrier_donors.get(
                      (s.get("sid"),
                       _anchor_carrier(s.get("anchor"))), [])}
        kinds = {d[1] for d in donors}
        if len(donors) >= 1 and len(kinds) == 1:
            qid, kind, donor_id = sorted(donors)[0]
            rule["kind"] = kind
            align_fit_with_kind(
                doc, rule, "вид унаследован от строки-носителя, "
                           "посадка ждёт цикла")
            if kind in ("DATA", "OUT"):
                rule.pop("weight", None)
                rule.pop("export_solver", None)
            extra = (f"вид унаследован от строки-носителя {donor_id} "
                     f"по применённому ответу {qid}: записи листа "
                     f"читаются единообразно")
            rule["note"] = ((rule.get("note", "") + "; "
                             + extra).strip("; "))
            notes.append(f"{rule.get('id')} → {kind} ({qid})")
    return notes


def restore_soft_penalty_rules(doc: dict) -> list[str]:
    """Дефект 1 репетиции демо (2026-08-29): извлечение ИЗМЕНЧИВО
    классифицирует штрафную строку без веса — в одном прогоне мягким
    правилом (E2E 25.08), в другом целевой (run-0062); кадр пробела и
    вопрос весов на «повезёт в этот раз» не строятся. Миграция
    детерминированно возвращает такую строку МЯГКИМ ПРАВИЛОМ зоны:
    целевая с машинным маркером «вес/штраф … не установлен» в
    формулировке переносится в rules (kind SOFT, id с координатами
    строки: O- → R-), из objectives удаляется. Настоящая целевая
    (без маркера) не трогается. Вес придёт ответом анкеты — блокеры
    поставит ensure_goal_question. Идемпотентно."""
    # Чистка ОСКОЛКОВ применения (живой круг полноты run-0002, ночь
    # 30→31.08): применение ответа целевой рождало целевые-осколки
    # пояснений («вес штрафа = 15», «разброс, вес 3 — уже взвешено»)
    # — без машинной метрики они не целевые, а норма уже живёт
    # правилами и весами того же ответа; вторая ветка миграции ниже
    # легализовала бы осколок правилом-дублем. Узкий класс: только
    # рождённые ответом (origin.question) и без metric; легаси-
    # целевые без origin не тронуты. Чистка идёт ДО миграции.
    notes: list[str] = []
    objectives = doc.get("objectives") or []
    shards: list[str] = []
    survivors: list[dict] = []
    for o in objectives:
        if isinstance(o, dict) \
                and isinstance(o.get("origin"), dict) \
                and o["origin"].get("question") \
                and not o.get("metric"):
            shards.append(
                f"{o.get('id')}: целевая-осколок ответа "
                f"{o['origin'].get('question')} без машинной метрики "
                "снята — норма живёт строками и весами ответа")
            continue
        survivors.append(o)
    if shards:
        notes += shards
        doc["objectives"] = survivors
        objectives = survivors
    rule_ids = {r.get("id") for r in doc.get("rules", [])}
    kept: list[dict] = []
    for o in objectives:
        stmt = str(o.get("statement") or "")
        # Вторая ветка классификации (живой круг полноты run-0002,
        # согласовано с решателем 2026-08-30, вариант «а»): целевая,
        # чья формулировка КЛАССИФИЦИРУЕТСЯ семьёй мягких правил
        # («минимизировать число ночей вне базы» = штраф за событие
        # семьи layover_penalty), — неполная форма той же нормы:
        # контракт P-N-SHD исполняет штраф за событие SOFT-правилом
        # с family и весом, целевая без ставки неисполнима.
        looks_soft = (_PENALTY_NO_WEIGHT_RE.search(stmt) is not None
                      or classify_soft_family({"statement": stmt})
                      is not None)
        # Лестница Ш-2 (сухая сборка ВМП нано, 1.09): НАСТОЯЩАЯ
        # целевая с машинной метрикой закрытого словаря мигрировала
        # в бесвесное SOFT-правило по семейной формулировке текста —
        # и блок 1.9 снимал у зоны optimization. Машинная метрика =
        # настоящая целевая по определению (та же грань, что у чистки
        # осколков выше) — миграции не подлежит.
        if isinstance(o, dict) and o.get("metric") in _cat("METRICS"):
            kept.append(o)
            continue
        if not isinstance(o, dict) or not looks_soft:
            kept.append(o)
            continue
        oid = str(o.get("id") or "")
        rid = ("R-" + oid[2:]) if oid.startswith("O-") else \
            ("R-" + oid)
        if rid in rule_ids:
            kept.append(o)       # правило уже есть — не дублировать
            continue
        rule = {
            "id": rid,
            "title": str(o.get("title") or ""),
            "statement": stmt,
            "zone": str(o.get("zone") or "BOTH"),
            "kind": "SOFT",
            "sources": [dict(s) for s in (o.get("sources") or [])],
            "note": ("классификация выпуска: штрафная строка без "
                     "веса — мягкое правило, не целевая (миграция "
                     "2026-08-29); вес — ответом анкеты"),
        }
        # Пакет посадки (ревью): мигрированная из целевой SOFT-строка
        # при контракте обязана нести посадку — честная C от кода.
        align_fit_with_kind(doc, rule,
                            "мигрирована из целевой без посадки")
        doc.setdefault("rules", []).append(rule)
        rule_ids.add(rid)
        notes.append(f"{oid}: штрафная строка без веса возвращена "
                     f"мягким правилом {rid}")
    if notes:
        doc["objectives"] = kept
    return notes


def ensure_goal_question(doc: dict, part: str) -> str | None:
    """ЦФ-1 (склейка решением владельца 2026-08-21): ЕДИНЫЙ вопрос
    зоны о целевой функции и весах — Q-{part}-GOAL; отдельного
    Q-*-OBJ больше не существует. Создаёт вопрос, когда у зоны нет
    целевых (секция таблицы целевых с опцией отказа) и/или есть
    мягкие строки без пути к весу (секция весов); такие строки
    блокируются этим вопросом — вес приходит только из ответа.
    Возвращает id актуального вопроса либо None (вопрос не нужен).

    Кандидат на блокировку — SOFT-строка зоны, у которой нет ни
    обычных блокеров (противоречие, условный тип, повторный запрос
    весов — путь к весу уже есть), ни веса из применённого ответа
    (_weighted_rules); блокеры-GOAL другой колонки кандидатуру НЕ
    снимают — BOTH-строка блокируется ОБОИМИ вопросами (ревью фазы
    Б 2026-08-21). Если вопрос уже отвечен, поздние строки уходят в
    НОВЫЙ вопрос весов (ensure_weights_question) — применённый
    вопрос не переоткрывается. Изменение нормативных источников его
    области создаёт новое издание и требует нового ответа. Идемпотентен."""
    task, zones = _GOAL_TASK[part]
    qid = f"Q-{part}-GOAL"
    q = _goal_question(doc, part, strict=True)
    if q is not None:
        qid = q["id"]
    zone_rules = [r for r in doc.get("rules", [])
                  if r.get("zone") in zones]
    if not zone_rules:
        return None          # зоны нет в документах вовсе
    has_obj = any(o.get("zone") in zones
                  for o in doc.get("objectives", []))
    weighted = _weighted_rules(doc.get("questions", []),
                               doc.get("rules", []))

    def _candidate(r: dict) -> bool:
        if r.get("kind") != "SOFT" or r.get("id") in weighted:
            return False
        blockers = r.get("blockers") or []
        if qid in blockers:
            return False
        return all(is_goal_family(b) for b in blockers)

    soft_unbound = [r for r in zone_rules if _candidate(r)]
    if q is not None and "edition" in q:
        from cdpm import question_editions
        refs = set(q["rule_refs"])
        sources = question_editions.normative_rule_sources(
            [r for r in zone_rules if r.get("id") in refs], q["source_refs"])
        source_changed = sources != q["source_refs"]
        # Late soft rows after an applied answer keep the dedicated weights
        # path. A new normative source for the old scope needs fresh consent,
        # whether the previous answer is open, queued or already applied.
        # Reattaching a row already in the frozen scope changes only its
        # blocker, not the question. Do not manufacture an identical edition.
        new_refs = {r["id"] for r in soft_unbound} - refs
        if source_changed or (is_open_question(q) and new_refs):
            refs.update(r["id"] for r in soft_unbound)
            sources = question_editions.normative_rule_sources(
                [r for r in zone_rules if r.get("id") in refs], sources)
            old_id = qid
            q = question_editions.expand(doc, q, sorted(refs), content={"source_refs": sources})
            qid = q["id"]
            for r in doc.get("rules", []):
                if old_id in (r.get("blockers") or []):
                    r["blockers"] = [qid if b == old_id else b for b in r["blockers"]]
                if source_changed and r.get("kind") == "SOFT" and r.get("id") in refs:
                    if qid not in r.setdefault("blockers", []):
                        r["blockers"].append(qid)
    if q is not None and not is_open_question(q):
        # P-N-SHD 2026-08-30: членство в rule_refs применённого
        # вопроса путём к весу НЕ является — кандидат (SOFT без
        # веса, без отказа, без блокеров) перевешивается на новый
        # вопрос весов, даже если применённый GOAL его называл
        # (вес из ответа не пришёл — leftover применения мог его
        # пропустить в моделях до этой правки).
        if soft_unbound:
            return ensure_weights_question(doc, part, soft_unbound)
        # Добор ЦЕЛЕВОЙ (живая дыра ночи 30→31.08, run-0002): GOAL
        # применён ответом только о весах — целевых у зоны нет и
        # отказа от оптимизации нет; матрица честно держит stop, а
        # спросить некому (пере-ответ поверх применённого запрещён).
        # Прецедент добора критерия: НОВЫЙ вопрос следующей ревизии.
        if not has_obj and not (q.get("answer") or {}) \
                .get("goal_decline"):
            open_t = next(
                (x for x in doc.get("questions", [])
                 if str(x.get("id") or "")
                 .startswith(f"Q-{part}-GOAL-T")
                 and is_goal_family(x.get("id"))
                 and is_open_question(x)), None)
            if open_t is not None:
                return open_t["id"]
            base_t = f"Q-{part}-GOAL-T"
            existing_ids = {x.get("id")
                            for x in doc.get("questions", [])}
            tid, n = base_t, 1
            while tid in existing_ids:
                n += 1
                tid = f"{base_t}-{n}"
            # Повторный добор несёт причину в тексте — и вопрос
            # содержательнее, и контракт дублей формулировок не
            # спутает его с первым (живой затык run-0002).
            again = ("" if n == 1 else
                     f" Повтор {n}: прежний ответ добора целевую с "
                     "машинной метрикой не породил — назовите "
                     "метрику из закрытого словаря явно.")
            doc.setdefault("questions", []).append({
                "id": tid, "part": part,
                "title": f"Целевая функция {task} (добор)",
                "text": (f"Прежний ответ на Q-{part}-GOAL принят "
                         "(веса мягких правил), но целевую функцию "
                         f"{task} не назвал — целевых у зоны нет. "
                         "Приведите таблицу целевых (компонент, "
                         "метрика, ярус важности, вес, правило "
                         "ничьих) либо подтвердите «любое допустимое "
                         "решение» («критерия оптимизации нет» — тогда "
                         "решение ищется в режиме взвешенных штрафов "
                         "feasibility-soft)." + again),
                "answer_format": {
                    "type": "registry",
                    "note": "таблица целевых функций либо отказ"},
                "rule_refs": list(q.get("rule_refs") or []),
                "source_refs": [dict(s) for s in
                                (q.get("source_refs") or [])],
            })
            return tid
        return qid
    if q is None:
        if has_obj and not soft_unbound:
            return None
        sections = []
        if not has_obj:
            sections.append(_GOAL_TABLE_SECTION.format(task=task))
        if soft_unbound:
            sections.append(_GOAL_WEIGHTS_SECTION.format(task=task))
        ref_rules = soft_unbound or [zone_rules[0]]
        q = {
            "id": qid, "part": part,
            "title": f"Целевая функция {task}: состав и веса",
            "text": "\n\n".join(sections),
            "answer_format": {
                "type": "registry",
                "note": "таблица целевых функций и/или веса мягких "
                        "правил; либо подтверждение «любое "
                        "допустимое решение»"},
            "rule_refs": [r["id"] for r in ref_rules],
            "source_refs": _uniq_source_refs(ref_rules),
        }
        doc.setdefault("questions", []).append(q)
    else:
        for r in soft_unbound:
            if r["id"] not in q.get("rule_refs", []):
                q.setdefault("rule_refs", []).append(r["id"])
        if "edition" not in q:
            from cdpm import question_editions
            q["source_refs"] = question_editions.normative_rule_sources(
                [r for r in zone_rules if r.get("id") in q["rule_refs"]], q["source_refs"])
    for r in soft_unbound:
        if qid not in r.setdefault("blockers", []):
            r["blockers"].append(qid)
    return qid


def ensure_goal_questions_sweep(doc: dict) -> list[str]:
    """Классовый инвариант P-N-SHD (живой корень демо 2026-08-30):
    НИ ОДНА мягкая строка не остаётся без пути к весу, КОГДА БЫ она
    ни появилась. Правило, ставшее мягким заплатой ПОСЛЕ применения
    Q-3b-GOAL, не попадало ни под сам GOAL (применён), ни под
    повторный запрос весов (тот видит только строки применяемого
    вопроса) — прод-модель ушла в пакет с weight=null без блокера.
    Проход обеих частей общим строителем на КАЖДОМ пути ответов и
    пересборе вопросов; строитель идемпотентен. В чужой модели
    (режим 2, сверка) вопросы постановки не ставятся — тот же
    предохранитель, что у вопроса критерия. Возвращает id
    созданных вопросов."""
    if (doc.get("meta") or {}).get("mode") == 2:
        return []
    before = {q.get("id") for q in doc.get("questions", [])}
    for part in sorted(_GOAL_TASK):
        ensure_goal_question(doc, part)
    return [q["id"] for q in doc.get("questions", [])
            if q.get("id") not in before]


# ── Семьи мягких правил (контракт P-N-SHD с решателем 2026-08-30):
# решатель привязывает шаблон ПО СЕМЬЕ, вес читает из weight, числовые
# параметры типа — из params. Перечня мягких семей код CDPM не держит
# (шаг 7, стадия 1б): вид семьи объявляет запись каталога (`kind`),
# и читатель отдаёт его через `template_family_traits()`; расширение —
# только новой записью каталога по отмашке владельца. Мягкое правило
# БЕЗ семьи — честный пробел покрытия (стоп → заявка → шаблон), не
# повод для карт-носителей по координатным id: те дрейфуют между
# извлечениями. Классификатор формулировок ниже — ЗАКОН CDPM (разбор
# языка документа), а не словарь каталога.
# Дни недели — закрытый словарь семи имён строчными (имена, не
# числа: ISO/питон/крон нумеруют по-разному — класс сдвига на
# единицу исключён по построению); корни без окончаний.
SOFT_FAMILY_DAYS = {
    "понедельник": "monday", "вторник": "tuesday",
    "среда": "wednesday", "четверг": "thursday",
    "пятниц": "friday", "суббот": "saturday",
    "воскресень": "sunday",
}
_WEEK_ORDER = ("monday", "tuesday", "wednesday", "thursday",
               "friday", "saturday", "sunday")
_FAM_BASE_LOCAL_RE = re.compile(
    r"по\s+местному\s+времени\s+базов", re.IGNORECASE)
# Корень без конечной «к»: родительный множественного «ночёвок»
# несёт беглую гласную (живой пропуск формы окон ночи 30.08).
# Вторая альтернатива — «ночей/ночи … вне» с допуском слов между
# (живой круг полноты run-0002: целевая «минимизировать суммарное
# число ночей ЭКИПАЖА вне АЭРОПОРТА базы приписки» — та же норма
# без слова «ночёвка»); только буквы и пробелы в промежутке —
# урок «т.п.»: точки сокращений не считаются границей предложения.
_FAM_LAYOVER_RE = re.compile(
    r"ноч[её]в|ноч(?:ей|и)\b[а-яё\s]{0,30}вне", re.IGNORECASE)
# «вне базы приписки», «вне базовой станции», «вне аэропорта базы»,
# «вне основного места базирования» — канонические формы документов.
_FAM_OUTSTATION_RE = re.compile(
    r"вне\s+(?:аэропорт\w*\s+)?баз|вне\s+основн\w+\s+мест",
    re.IGNORECASE)
_FAM_CREDIT_RE = re.compile(r"кредитн\w*\s+час", re.IGNORECASE)
_FAM_SPREAD_RE = re.compile(r"равномерн|разброс", re.IGNORECASE)
_FAM_GROUP_BASE_RE = re.compile(r"одн\w{1,2}\s+баз|той\s+же\s+баз",
                                re.IGNORECASE)
# Семья duty_extension_penalty (первое живое расширение словаря
# каноническим процессом P-N-SHD: стоп → заявка → шаблон 154603b →
# семья, ночь 30→31.08): продление смены сверх мягкого порога с
# согласия — штраф за каждую смену длиннее порога. Промежутки —
# только буквы и пробелы (уроки морфологии и точек «т.п.»).
_FAM_EXTENSION_RE = re.compile(
    r"продл\w*[а-яё\s]{0,40}смен|смен\w*[а-яё\s]{0,40}продл"
    r"|превышени\w*[а-яё\s]{0,30}порог\w*[а-яё\s]{0,30}смен",
    re.IGNORECASE)
_FAM_SPREAD_THRESHOLD_RE = re.compile(
    r"разброс\w*\s+(?:бол\w+\s+)?(\d+(?:[.,]\d+)?)\s*час\w*",
    re.IGNORECASE)


def soft_family_from_fit(rule: dict) -> dict | None:
    """Семья мягкой механики из ПОСАДКИ A/B (П4, 2026-09-02): семья —
    атрибут посадки, не регэкспа; форма записи export_solver та же,
    что у классификатора (days/tz у календарного окна — из формулировки
    и цитаты-носителя по закрытому словарю дней; group у равномерности
    — по признаку «одной базы»). Семья посадки не мягкая по атрибутам
    базы или посадка не A/B — None (тогда судит классификатор)."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return None
    fam = fit.get("family")
    if not fam:
        return None
    tr = template_family_traits().get(fam)
    if tr is not None and tr.get("kind") not in (None, "SOFT"):
        return None
    out: dict = {"family": str(fam)}
    low = (str(rule.get("statement") or "") + " "
           + str(fit.get("quote") or "")).lower()
    if fam == "calendar_windows":
        days = sorted({eng for ru, eng in SOFT_FAMILY_DAYS.items()
                       if ru in low}, key=_WEEK_ORDER.index)
        if days:
            out["days"] = days
            out["tz"] = "base-local"
    elif fam == "balanced_workload":
        out["group"] = ("base" if _FAM_GROUP_BASE_RE.search(low)
                        or "внутри баз" in low else "crew")
    return out


def classify_soft_family(rule: dict) -> dict | None:
    """Семья механики мягкого правила по классовым признакам
    формулировки — детерминированно, без модели и без знания
    конкретной авиакомпании. None — семьи нет (пробел покрытия).
    Порядок ветвей значим: календарное окно — частный случай
    ночёвки (дни недели + явный пояс базовой станции), судится
    раньше общего штрафа ночёвок."""
    low = str(rule.get("statement") or "").lower()
    if _FAM_LAYOVER_RE.search(low):
        days = sorted({eng for ru, eng in SOFT_FAMILY_DAYS.items()
                       if ru in low}, key=_WEEK_ORDER.index)
        if days and _FAM_BASE_LOCAL_RE.search(low):
            return {"family": "calendar_windows",
                    "days": days, "tz": "base-local"}
        if _FAM_OUTSTATION_RE.search(low):
            return {"family": "layover_penalty"}
        return None
    if _FAM_CREDIT_RE.search(low) and _FAM_SPREAD_RE.search(low):
        group = ("base" if _FAM_GROUP_BASE_RE.search(low)
                 else "crew")
        return {"family": "balanced_workload", "group": group}
    if _FAM_EXTENSION_RE.search(low):
        return {"family": "duty_extension_penalty"}
    return None


def assign_soft_families(doc: dict) -> list[str]:
    """Проставляет export_solver.family мягким правилам по классовым
    признакам; у семьи balanced_workload добирает порог разброса
    параметром закрытого словаря (workload_spread_threshold, след —
    совпавший фрагмент формулировки). Правило, потерявшее мягкость,
    семью теряет. Идемпотентно; возвращает заметки изменений."""
    notes: list[str] = []
    for r in doc.get("rules", []):
        if not isinstance(r, dict):
            continue
        if r.get("kind") != "SOFT":
            if r.pop("export_solver", None) is not None:
                notes.append(f"{r.get('id')}: семья снята — правило "
                             f"больше не мягкое ({r.get('kind')})")
            continue
        fam = soft_family_from_fit(r)
        origin = "посадка"
        if fam is None:
            # Без посадки A/B — классификатор признаков формулировки
            # (эвристика, со следом); при посадке семья — атрибут
            # посадки, не регэкспа (П4, ревью 2026-09-02: две правды о
            # семье расходились на эталоне H0).
            fam = classify_soft_family(r)
            origin = "эвристика"
        if fam is None:
            if r.pop("export_solver", None) is not None:
                notes.append(f"{r.get('id')}: семья снята — признаки "
                             "формулировки больше не совпадают")
            continue
        if r.get("export_solver") != fam:
            r["export_solver"] = fam
            notes.append(f"{r.get('id')}: семья {fam['family']} "
                         f"({origin})")
        if fam["family"] != "balanced_workload":
            continue
        # Носитель имени — и ячейка посадки (A05, В-2 ревью): порог
        # разброса из формулировки не дописывается второй правдой
        # рядом с уже посаженным параметром.
        have = set(rule_param_cells(r)) \
            | {p.get("name") for p in (r.get("params") or [])
               if isinstance(p, dict)}
        if "workload_spread_threshold" in have:
            continue
        mm = _FAM_SPREAD_THRESHOLD_RE.search(
            str(r.get("statement") or ""))
        if not mm:
            continue
        num = mm.group(1).replace(",", ".")
        val = float(num)
        if val.is_integer():
            val = int(val)
        r.setdefault("params", []).append({
            "name": "workload_spread_threshold", "value": val,
            "unit": "credit_hours", "as_written": mm.group(0),
            "relation": "max"})
        notes.append(f"{r.get('id')}: порог разброса {val} ч — "
                     "параметром workload_spread_threshold")
    return notes


def count_objects(doc: dict) -> dict:
    """Счётчики для гейтов и статусов: гейт обязан печатать, сколько
    объектов рассмотрел (У-05); числа берутся отсюда, одним способом."""
    rules = doc.get("rules", [])
    questions = doc.get("questions", [])
    per_part = {p: sum(1 for q in questions if q.get("part") == p)
                for p in PARTS}
    links = (
        sum(len(r.get("sources", [])) + len(r.get("blockers", []))
            for r in rules)
        + sum(len(q.get("rule_refs", [])) + len(q.get("source_refs", []))
              for q in questions))
    return {
        "sources": len(doc.get("sources", [])),
        "rules": len(rules),
        "questions": len(questions),
        "questions_per_part": per_part,
        "blocked_rules": sum(1 for r in rules if r.get("blockers")),
        "conditional_rules": sum(1 for r in rules if r.get("conditional")),
        "objectives": len(doc.get("objectives", [])),
        "links": links,
    }
