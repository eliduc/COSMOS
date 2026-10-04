# -*- coding: utf-8 -*-
"""Запись о прогоне: единый словарь полей для CDPM, COSMOS и раннера
наборов.

Основание — проект `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md` (разделы
3 «Словарь полей», 6 «Решения владельца», 7 «Дерево прогонов», 8
«Заключение») и слово владельца 10.09.2026 «Начинай»: шаг 1 раскладки —
словарь полей и схема записи ОДНИМ носителем, который читают обе системы
и раннер.

Зачем один носитель. Сегодня одни и те же величины лежат под разными
именами в разных местах: каталог — штампом в манифесте пакета и в
паспорте прогона, решатель — в записи попытки COSMOS, круги обмена — в
состоянии прогона CDPM. Пока имена расходятся, «прогон» существует как
рассказ человека, а не как сущность: его нельзя ни повторить, ни
сравнить с соседним. Модуль называет поля один раз; схема
`run_record.schema.json` рядом описывает те же поля машинно, и она —
судья: модуль её ЧИТАЕТ, а не повторяет.

Три части записи (раздел 2 проекта):

  * `declaration` — ГОЛОВА: параметры прогона, названные ДО старта.
    После старта неизменна: её отпечаток лежит в записи (`
    declaration_sha256`), и `append_stage` отказывается дописывать
    журнал к подменённой голове. Голова — рецепт повторения;
  * `journal` — ТЕЛО: этапы и подэтапы по мере хода, только
    дописывается. Непройденный этап помечается исходом «не начат», а не
    отсутствует: план этапов объявлен, и молчание о ненайденном этапе
    было бы потерей;
  * `results` — ХВОСТ: вердикты, выдачи, сравнения, признак золотого
    прогона, хронометраж и состояние.

Слова значений — русские там, где это слова (цель, исход, состояние,
источник ответов); имена полей — английские `snake_case`, потому что их
читают обе системы и они уже так называются у сегодняшних носителей
(`spent_limit_s`, `solver_run_id`, `catalog_fingerprint`).

Чего модуль НЕ делает намеренно: не ходит в дерево за умолчаниями.
Объявление без каталога, без привязок исполнителей или без документов
правил — ошибка проверки словами, а не повод «взять то, что лежит»
(требование 2 раздела 8 проекта; живой случай Н0-C 09.09.2026, где
каталог прогона и привязки дерева разошлись на середине прогона).
"""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import os
import re
from pathlib import Path

#: Версия словаря полей. Меняется вместе со схемой.
SCHEMA = "cosmos-run-record/v1"

#: Схема записи: тот же словарь полей машинно. Модуль её ЧИТАЕТ —
#: второго перечня полей в проекте нет.
SCHEMA_PATH = Path(__file__).resolve().with_name("run_record.schema.json")

#: Имя файла записи в папке прогона (раздел 4 проекта).
RECORD_FILE = "run-record.json"

# ---------------------------------------------------------------- слова
#: Цель прогона.
PURPOSES = ("приёмка", "демонстрация", "регресс", "исследование", "рабочий")
#: Площадка прогона.
CONTOURS = ("local", "test", "stage", "prod")
#: Исход этапа журнала.
OUTCOMES = ("успех", "предупреждение", "отказ по правилу", "сбой",
            "остановлен человеком", "не начат")
#: Состояние прогона (раздел 1 проекта).
STATES = ("объявлен", "идёт", "завершён", "остановлен по правилу", "сбой",
          "остановлен человеком")
#: Кто отвечает на анкеты.
ANSWER_POLICIES = ("человек", "книга")
#: Источник ревизии ответов.
ANSWER_SOURCES = ("человек", "книга набора")

STATE_DECLARED, STATE_RUNNING, STATE_DONE = STATES[0], STATES[1], STATES[2]
STATE_RULE_STOP, STATE_FAILURE, STATE_HUMAN_STOP = STATES[3], STATES[4], STATES[5]

OUTCOME_OK, OUTCOME_WARN, OUTCOME_RULE = OUTCOMES[0], OUTCOMES[1], OUTCOMES[2]
OUTCOME_FAILURE, OUTCOME_HUMAN, OUTCOME_NOT_STARTED = (OUTCOMES[3], OUTCOMES[4],
                                                       OUTCOMES[5])

# --------------------------------------------------------- поля записи
# Перечни ниже — ЕДИНСТВЕННОЕ место, где модуль называет состав каждой
# части; из них же строится нормализация. Схема описывает ровно эти
# поля, и согласие модуля со схемой судится стендом перебором.

#: Части записи.
RECORD_FIELDS = ("schema", "declaration", "declaration_sha256", "journal",
                 "results")

#: Объявление (голова).
DECLARATION_FIELDS = (
    "run_id", "created_at", "created_by", "purpose", "contour", "airline",
    "rules_documents", "data_files", "answers", "catalog", "catalog_extension",
    "bindings", "models", "models_note", "solvers", "code", "libraries",
    "planned_stages", "stop_after", "expectations", "lineage", "answer_policy")

#: Единица журнала (тело).
JOURNAL_FIELDS = (
    "stage", "substage", "started_at", "finished_at", "duration_machine_s",
    "duration_waiting_human_s", "outcome", "reason_code", "words", "actor",
    "artifacts", "counters", "resources", "system")

#: Система-летописец единицы журнала (шаг 2, 10.09.2026): панель CDPM,
#: раннер наборов, COSMOS. Пусто — запись хозяина записи (обратная
#: совместимость записей шага 1). Хранитель записи (панель) ставит слово
#: отправителя append-а; COSMOS помечает свои записи сам.
#:
#: ТОТ ЖЕ словарь называет систему модели в голове (`declaration.
#: models[]. system`, договор с CDPM 11.09.2026): голову чеканит хозяин
#: записи, и до 11.09 в ней стояли только модели CDPM, а модели COSMOS
#: (сертификатор, читатель ИДАК) в голову не попадали вовсе (находка К5).
#: Теперь строка модели называет свою систему сама; поля нет — модель
#: CDPM (совместимость со всеми прежними записями).
SYSTEMS = ("cdpm", "runner", "cosmos")
SYSTEM_CDPM, SYSTEM_RUNNER, SYSTEM_COSMOS = SYSTEMS

#: Итоги (хвост).
RESULTS_FIELDS = ("verdicts", "deliveries", "comparison", "golden", "timing",
                  "state")

#: Вложенные словари: путь в схеме → состав полей. Путь читается
#: `schema_properties`: точка — вложенный словарь, «[]» — элемент списка.
NESTED_FIELDS = {
    "declaration.contour": ("name", "services"),
    "declaration.airline": ("name", "data_class", "llm_scope"),
    "declaration.rules_documents[]": ("name", "kind", "sha256", "size"),
    "declaration.data_files[]": ("name", "sheets", "rows", "sha256"),
    "declaration.answers[]": ("revision", "sha256", "source", "at"),
    "declaration.catalog": ("name", "version", "fingerprint", "path"),
    "declaration.catalog_extension": ("name", "version", "fingerprint"),
    "declaration.bindings": ("catalog_version", "catalog_fingerprint", "path"),
    "declaration.models[]": ("role", "model", "provider", "channel", "effort",
                             "system"),
    "declaration.solvers[]": (
        "task", "engine", "version", "build_sha256", "spent_limit_s",
        "unimproved_spent_limit_s", "moves", "seed", "step_limit",
        "unimproved_step_limit", "deterministic_time", "accepted_uncovered"),
    "declaration.code": ("cdpm_commit", "cosmos_commit", "runner_fingerprint"),
    "declaration.libraries": ("python", "packages_sha256", "packages"),
    "declaration.libraries.packages[]": ("name", "version"),
    "declaration.expectations": ("kind", "ref"),
    "declaration.lineage": ("root_sha256", "root_answers_sha256", "parent_run",
                            "branch_of", "package_sha256", "source_run"),
    "journal[].artifacts[]": ("kind", "path", "sha256"),
    "journal[].resources": ("models", "solver_seconds"),
    "journal[].resources.models[]": ("role", "model", "provider", "calls",
                                     "input_tokens", "output_tokens",
                                     "counted", "note"),
    "results.deliveries[]": ("kind", "path", "sha256"),
    "results.comparison": ("expectations", "previous_run", "golden"),
    "results.golden": ("confirmed", "by", "at", "words"),
    "results.timing": ("machine_s", "waiting_human_s", "total_s"),
}

#: Поля-списки: умолчание — пустой список, а не пусто.
_LIST_FIELDS = frozenset((
    "rules_documents", "data_files", "answers", "models", "solvers",
    "planned_stages", "packages", "artifacts", "deliveries", "accepted_uncovered"))

#: Поля — свободные словари: умолчание — пустой словарь.
_FREE_DICT_FIELDS = frozenset((
    "services", "counters", "verdicts", "expectations", "previous_run",
    "golden"))

#: Поля ГОЛОВЫ, которых у записи может не быть ВОВСЕ: путь в объявлении →
#: имена полей. Не назвали — поле опускается, а не пишется пустотой.
#:
#: Так появление поля в словаре не сдвигает отпечаток объявления прежних
#: записей: `declaration_sha256` считается по каноническому виду головы, и
#: лишний ключ со значением «пусто» сделал бы все записи, объявленные до
#: 11.09.2026, «изменёнными после старта». Правило действует только в
#: голове: журнал и итоги в отпечаток не входят, и там пустое поле стоит
#: как прежде (`journal[].system` — пусто значит «запись хозяина»).
OMITTED_WHEN_EMPTY = {
    "declaration": ("models_note",),
    "declaration.models[]": ("system",),
}


class RunRecordError(ValueError):
    """Запись не принята: слова расхождения. Отказ — не исключение
    «что-то пошло не так», а названная причина: её печатают человеку."""


# ------------------------------------------------------------ мелочи
def sha256_text(text: str) -> str:
    """Отпечаток строки (UTF-8)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def short(value, n: int = 12) -> str:
    """Короткий вид отпечатка для слов человеку."""
    s = "" if value is None else str(value)
    return s[:n] if s else "нет"


def now_iso(moment: _dt.datetime | None = None) -> str:
    """Отметка времени ISO с поясом ПЛОЩАДКИ: часы у прогона одни
    (раздел 4 проекта). Без пояса отметка не говорит, когда это было."""
    m = moment or _dt.datetime.now()
    if m.tzinfo is None:
        m = m.astimezone()
    return m.isoformat(timespec="seconds")


def mint_run_id(root: str | None = None, at: _dt.datetime | None = None) -> str:
    """Номер прогона, когда система его ещё не выдала.

    Запись рождается ДО первой стадии, а номер прогона CDPM появляется
    только после заморозки: без собственного номера объявление некуда
    записать. Номер строится из момента объявления и короткого отпечатка
    корня дерева — он читается человеком и не повторяется у соседних
    прогонов одного корня."""
    m = at or _dt.datetime.now()
    tail = short(root, 8) if root else "0" * 8
    return f"{m.strftime('%Y%m%d-%H%M%S')}-{tail}"


def _num(value):
    """Число или пусто: счётчики и хронометраж не хранят слова."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _seconds(value):
    """Секунды: отрицательных длительностей не бывает."""
    v = _num(value)
    return None if v is None or v < 0 else v


def _moment(value):
    """Отметка времени строкой либо пусто."""
    return str(value) if isinstance(value, str) and value.strip() else None


def _text(value):
    """Слова строкой либо пусто (пустая строка — то же молчание)."""
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def _parse(moment: str | None):
    """Разбор отметки времени; не разобралась — пусто (отметки прежних
    носителей бывают без пояса и в чужом виде, ронять запись из-за них
    нельзя)."""
    if not moment:
        return None
    try:
        return _dt.datetime.fromisoformat(str(moment))
    except ValueError:
        return None


def between(started_at: str | None, finished_at: str | None):
    """Секунды между двумя отметками; одна не разобралась или пояса
    разные — пусто, а не выдуманное число."""
    a, b = _parse(started_at), _parse(finished_at)
    if a is None or b is None:
        return None
    try:
        delta = (b - a).total_seconds()
    except TypeError:                       # одна с поясом, другая без
        return None
    return round(delta, 3) if delta >= 0 else None


# ------------------------------------------------------------- схема
@functools.lru_cache(maxsize=1)
def schema_document() -> dict:
    """Схема записи из файла рядом с модулем."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _resolve(node: dict, root: dict) -> dict:
    """Разыменование локальной ссылки `#/$defs/<имя>`.

    Поле, которого может не быть вовсе (`oneOf: [словарь, пусто]` —
    например расширение каталога), разыменовывается до той ветви, что
    описывает поля: читателю схемы нужен состав словаря, а не выбор."""
    seen = 0
    while isinstance(node, dict) and seen < 20:
        if "$ref" in node:
            ref = str(node["$ref"])
            if not ref.startswith("#/"):
                break
            cur = root
            for part in ref[2:].split("/"):
                cur = (cur or {}).get(part)
            node, seen = cur, seen + 1
            continue
        branches = node.get("oneOf") or node.get("anyOf")
        if not branches or "properties" in node:
            break
        described = [b for b in branches if isinstance(b, dict)
                     and ("$ref" in b or "properties" in b)]
        if not described:
            break
        node, seen = described[0], seen + 1
    return node if isinstance(node, dict) else {}


def schema_node(path: str = "") -> dict:
    """Узел схемы по пути: точка — вложенный словарь, «[]» — элемент
    списка. Пустой путь — корень записи. Пути нет — RunRecordError."""
    root = schema_document()
    node = _resolve(root, root)
    for step in [s for s in str(path).split(".") if s]:
        name, arrays = step, 0
        while name.endswith("[]"):
            name, arrays = name[:-2], arrays + 1
        props = (node.get("properties") or {})
        if name not in props:
            raise RunRecordError(f"схема записи не описывает поле {path!r} "
                                 f"(нет {name!r})")
        node = _resolve(props[name], root)
        for _ in range(arrays):
            node = _resolve(node.get("items") or {}, root)
    return node


def schema_properties(path: str = "") -> dict:
    """Поля схемы по пути: {имя поля: узел схемы}."""
    return dict(schema_node(path).get("properties") or {})


@functools.lru_cache(maxsize=8)
def _validator(part: str = ""):
    """Проверяющий по схеме для всей записи (`part` пуст) либо для одной
    её части (`#/$defs/<part>`)."""
    import jsonschema
    root = schema_document()
    doc = root if not part else {"$schema": root["$schema"],
                                 "$ref": f"#/$defs/{part}",
                                 "$defs": root["$defs"]}
    return jsonschema.Draft202012Validator(doc)


_RU_TYPE = {"string": "строка", "integer": "целое", "number": "число",
            "object": "словарь", "array": "список", "boolean": "признак",
            "null": "пусто"}


def _place(error) -> str:
    """Место расхождения словами: путь полей записи."""
    path = "/".join(str(p) for p in error.absolute_path)
    return path or "запись"


def _quoted(message: str) -> list:
    """Имена полей, названные в сообщении проверяющего."""
    return re.findall(r"'([^']+)'", str(message))


def _error_words(error) -> str:
    """Расхождение по схеме — словами делового русского."""
    place, kind = _place(error), error.validator
    if kind == "required":
        names = _quoted(error.message) or ["?"]
        return f"{place}: поля {', '.join(names)} нет — схема требует его"
    if kind == "additionalProperties":
        names = _quoted(error.message) or ["?"]
        return (f"{place}: поля {', '.join(names)} схема не описывает — "
                f"словарь полей один, дописывать мимо него нельзя")
    if kind in ("enum", "const"):
        allowed = error.validator_value if kind == "enum" else [error.validator_value]
        allowed = [("пусто" if a is None else str(a)) for a in allowed]
        return (f"{place}: «{error.instance}» не из перечня слов "
                f"({', '.join(allowed)})")
    if kind == "type":
        want = error.validator_value
        want = want if isinstance(want, list) else [want]
        got = _RU_TYPE.get(
            "null" if error.instance is None else
            "boolean" if isinstance(error.instance, bool) else
            "integer" if isinstance(error.instance, int) else
            "number" if isinstance(error.instance, float) else
            "string" if isinstance(error.instance, str) else
            "array" if isinstance(error.instance, list) else
            "object" if isinstance(error.instance, dict) else "?", "неизвестно")
        return (f"{place}: ожидалось {', '.join(_RU_TYPE.get(w, w) for w in want)}"
                f", а лежит {got}")
    if kind == "pattern":
        return (f"{place}: значение «{error.instance}» не той формы, что "
                f"объявлена схемой")
    if kind == "minimum":
        return f"{place}: значение {error.instance} меньше {error.validator_value}"
    if kind in ("minLength", "minItems"):
        return f"{place}: значение пусто, а схема требует непустое"
    return f"{place}: {error.message}"


def _schema_words(value, part: str = "") -> list:
    """Расхождения значения со схемой — перечнем слов."""
    errors = sorted(_validator(part).iter_errors(value),
                    key=lambda e: (list(map(str, e.absolute_path)), str(e.validator)))
    return [_error_words(e) for e in errors]


# --------------------------------------------------------- нормализация
def _pick(fields, src, defaults: dict | None = None) -> dict:
    """Словарь ровно из объявленных полей: чего нет — умолчание части
    (список, свободный словарь либо пусто)."""
    src = src if isinstance(src, dict) else {}
    defaults = defaults or {}
    out = {}
    for f in fields:
        if f in src and src[f] is not None:
            out[f] = src[f]
        elif f in defaults:
            out[f] = defaults[f]
        elif f in _LIST_FIELDS:
            out[f] = []
        elif f in _FREE_DICT_FIELDS:
            out[f] = {}
        else:
            out[f] = None
    return out


def _rows(fields, rows, norm=None) -> list:
    """Список однородных словарей по объявленным полям."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = _pick(fields, row)
        out.append(norm(item) if norm else item)
    return out


def _drop_empty(row: dict, path: str) -> dict:
    """Убрать из словаря НЕОБЯЗАТЕЛЬНЫЕ поля этого пути, которых не
    назвали (`OMITTED_WHEN_EMPTY`): пусто — поля нет вовсе."""
    for field in OMITTED_WHEN_EMPTY.get(path, ()):
        if not row.get(field):
            row.pop(field, None)
    return row


def _counters(src) -> dict:
    """Счётчики: только числа (имена — по носителям систем, раздел 3.2
    проекта). Слова в счётчики не попадают: их место — `words`."""
    src = src if isinstance(src, dict) else {}
    return {str(k): _num(v) for k, v in src.items() if _num(v) is not None}


def _services(src) -> dict:
    """Адреса служб площадки: имя службы → адрес словами."""
    src = src if isinstance(src, dict) else {}
    return {str(k): _text(v) for k, v in src.items()}


def normalize_declaration(declaration) -> dict:
    """Объявление во всех объявленных полях: чего не назвали — пусто, а
    не «как в дереве». Пустой каталог, пустые привязки и пустой перечень
    документов ловит `validate` словами."""
    d = _pick(DECLARATION_FIELDS, declaration)
    d["run_id"] = _text(d["run_id"])
    d["created_at"] = _moment(d["created_at"])
    d["created_by"] = _text(d["created_by"])
    d["purpose"] = _text(d["purpose"])
    d["stop_after"] = _text(d["stop_after"])
    d["answer_policy"] = _text(d["answer_policy"])
    d["contour"] = _pick(NESTED_FIELDS["declaration.contour"], d["contour"])
    d["contour"]["services"] = _services(d["contour"]["services"])
    d["airline"] = _pick(NESTED_FIELDS["declaration.airline"], d["airline"])
    d["rules_documents"] = _rows(NESTED_FIELDS["declaration.rules_documents[]"],
                                 d["rules_documents"], _rules_document)
    d["data_files"] = _rows(NESTED_FIELDS["declaration.data_files[]"],
                            d["data_files"], _data_file)
    d["answers"] = _rows(NESTED_FIELDS["declaration.answers[]"], d["answers"])
    d["catalog"] = _pick(NESTED_FIELDS["declaration.catalog"], d["catalog"])
    d["catalog_extension"] = (
        _pick(NESTED_FIELDS["declaration.catalog_extension"],
              d["catalog_extension"]) if d["catalog_extension"] else None)
    d["bindings"] = _pick(NESTED_FIELDS["declaration.bindings"], d["bindings"])
    d["models"] = _rows(NESTED_FIELDS["declaration.models[]"], d["models"],
                        _model)
    d["models_note"] = _text(d["models_note"])
    d["solvers"] = _rows(NESTED_FIELDS["declaration.solvers[]"], d["solvers"],
                         _solver)
    d["code"] = _pick(NESTED_FIELDS["declaration.code"], d["code"])
    d["libraries"] = _pick(NESTED_FIELDS["declaration.libraries"],
                           d["libraries"])
    d["libraries"]["packages"] = _rows(
        NESTED_FIELDS["declaration.libraries.packages[]"],
        d["libraries"]["packages"])
    d["planned_stages"] = [str(s) for s in (d["planned_stages"] or [])]
    d["expectations"] = _pick(NESTED_FIELDS["declaration.expectations"],
                              d["expectations"])
    d["lineage"] = _pick(NESTED_FIELDS["declaration.lineage"], d["lineage"])
    return _drop_empty(d, "declaration")


def _model(row: dict) -> dict:
    """Строка модели головы: система-владелец роли — словом словаря
    `SYSTEMS`, и поля нет вовсе, когда систему не назвали (тогда это
    модель CDPM — так голова читалась до 11.09.2026)."""
    row["system"] = _text(row["system"])
    return _drop_empty(row, "declaration.models[]")


def _rules_document(row: dict) -> dict:
    row["name"] = _text(row["name"])
    row["kind"] = _text(row["kind"]) or _kind_of(row["name"])
    row["size"] = _num(row["size"])
    return row


def _data_file(row: dict) -> dict:
    row["name"] = _text(row["name"])
    row["sheets"] = _num(row["sheets"])
    row["rows"] = _num(row["rows"])
    return row


def _solver(row: dict) -> dict:
    for key in ("spent_limit_s", "unimproved_spent_limit_s",
                "deterministic_time"):
        row[key] = _seconds(row[key])
    for key in ("seed", "step_limit", "unimproved_step_limit"):
        row[key] = _num(row[key])
    row["moves"] = _text(row["moves"])
    unc = row["accepted_uncovered"]
    row["accepted_uncovered"] = [str(x) for x in unc] if isinstance(unc, list) \
        else None
    return row


def _kind_of(name: str | None) -> str | None:
    """Тип документа по имени файла: docx, xlsx, jpg и прочее."""
    suffix = Path(str(name or "")).suffix.lstrip(".").lower()
    return suffix or None


def normalize_entry(entry) -> dict:
    """Единица журнала во всех объявленных полях."""
    e = _pick(JOURNAL_FIELDS, entry)
    e["stage"] = _text(e["stage"])
    e["substage"] = _text(e["substage"])
    e["started_at"] = _moment(e["started_at"])
    e["finished_at"] = _moment(e["finished_at"])
    e["duration_machine_s"] = _seconds(e["duration_machine_s"])
    if e["duration_machine_s"] is None:
        e["duration_machine_s"] = between(e["started_at"], e["finished_at"])
    e["duration_waiting_human_s"] = _seconds(e["duration_waiting_human_s"])
    e["outcome"] = _text(e["outcome"])
    e["reason_code"] = _text(e["reason_code"])
    e["words"] = _text(e["words"])
    e["actor"] = _text(e["actor"])
    e["artifacts"] = _rows(NESTED_FIELDS["journal[].artifacts[]"],
                           e["artifacts"])
    e["counters"] = _counters(e["counters"])
    e["resources"] = _pick(NESTED_FIELDS["journal[].resources"], e["resources"])
    e["resources"]["models"] = _rows(
        NESTED_FIELDS["journal[].resources.models[]"],
        e["resources"]["models"], _resource_model)
    e["resources"]["solver_seconds"] = _seconds(e["resources"]["solver_seconds"])
    e["system"] = _text(e["system"])
    return e


def _resource_model(row: dict) -> dict:
    for key in ("calls", "input_tokens", "output_tokens"):
        row[key] = _num(row[key])
    row["counted"] = bool(row["counted"])
    row["note"] = _text(row["note"])
    return row


def empty_results() -> dict:
    """Хвост только что объявленного прогона: вердиктов ещё нет,
    золотым он не назначен (решение владельца № 3: признак ставит
    только владелец после приёмки на Prod)."""
    return normalize_results({"state": STATE_DECLARED})


def normalize_results(results) -> dict:
    """Итоги во всех объявленных полях."""
    r = _pick(RESULTS_FIELDS, results)
    r["verdicts"] = dict(r["verdicts"]) if isinstance(r["verdicts"], dict) else {}
    r["deliveries"] = _rows(NESTED_FIELDS["results.deliveries[]"],
                            r["deliveries"])
    r["comparison"] = _pick(NESTED_FIELDS["results.comparison"], r["comparison"])
    for key in NESTED_FIELDS["results.comparison"]:
        r["comparison"][key] = dict(r["comparison"][key]) \
            if isinstance(r["comparison"][key], dict) else {}
    r["golden"] = _pick(NESTED_FIELDS["results.golden"], r["golden"],
                        {"confirmed": False})
    r["golden"]["confirmed"] = bool(r["golden"]["confirmed"])
    r["golden"]["by"] = _text(r["golden"]["by"])
    r["golden"]["at"] = _moment(r["golden"]["at"])
    r["golden"]["words"] = _text(r["golden"]["words"])
    r["timing"] = _pick(NESTED_FIELDS["results.timing"], r["timing"])
    for key in NESTED_FIELDS["results.timing"]:
        r["timing"][key] = _seconds(r["timing"][key])
    r["state"] = _text(r["state"]) or STATE_DECLARED
    return r


# ------------------------------------------------------- корень дерева
def canonical(obj) -> bytes:
    """Каноническая запись значения для отпечатков — ОДНА на обе системы
    (договор COSMOS ↔ CDPM 10.09.2026): ключи сортируются, отступов нет,
    не-ASCII не экранируется, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _shas(rows) -> list:
    return sorted(str((r or {}).get("sha256") or "")
                  for r in (rows or []) if isinstance(r, dict)
                  and (r or {}).get("sha256"))


def root_sha256(rules_documents, data_files) -> str:
    """Корень дерева прогонов ПО ДОКУМЕНТАМ И ДАННЫМ — известен до
    старта: sha256(canonical({"rules": [sha…], "data": [sha…]})),
    перечни отсортированы, имена файлов в счёт не входят (один и тот же
    документ под другим именем — тот же корень). Ответы на анкеты — в
    `root_answers_sha256`: они закрываются по ходу прогона."""
    return hashlib.sha256(canonical(
        {"rules": _shas(rules_documents), "data": _shas(data_files)})).hexdigest()


def answers_fingerprint(pairs) -> str | None:
    """Отпечаток ревизии ответов на анкеты ПО СОДЕРЖАНИЮ (пункт 12.6
    проекта, договор с CDPM 10.09.2026 22:4x): один носитель формулы для
    обеих систем — `sha256(canonical(sorted([[question_id, ответ], …])))`,
    где ответ нормализован (обрезка краёв и схлопывание пробелов), пустые
    ответы опускаются, пары отсортированы по номеру вопроса. Байты файла
    книги не участвуют: книга набора и книга, собранная панелью под прогон,
    с теми же ответами дают ОДИН отпечаток. Пар нет — None."""
    rows = []
    for pair in pairs or []:
        if isinstance(pair, dict):
            qid, answer = pair.get("question_id"), pair.get("answer")
        else:
            qid, answer = (list(pair) + [None, None])[:2]
        text = " ".join(str(answer).split()) if answer is not None else ""
        if qid is None or not text:
            continue
        rows.append([str(qid), text])
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    return hashlib.sha256(canonical(rows)).hexdigest()


def root_answers_sha256(rules_documents, data_files, answers) -> str | None:
    """Корень дерева С ОТВЕТАМИ (раздел 7 проекта — ответы тоже исходные
    документы): sha256(canonical({"rules", "data", "answers": [sha
    ревизий…]})). Смена одного ответа даёт другой корень — новую ветвь.
    Ответов ещё нет — None (ставится, когда ответы закрыты; у наборов с
    книгой ответов — сразу)."""
    shas = _shas(answers)
    if not shas:
        return None
    return hashlib.sha256(canonical(
        {"rules": _shas(rules_documents), "data": _shas(data_files),
         "answers": shas})).hexdigest()


#: Поля объявления, которые растут по ходу прогона и в отпечаток головы
#: не входят: ревизии ответов на анкеты (договор с CDPM 10.09.2026).
DECLARATION_SHA_EXCLUDED = ("answers",)


def declaration_sha256(declaration) -> str:
    """Отпечаток объявления: им судится неизменность головы после
    старта. Счёт канонический (`canonical`), без ключей
    `DECLARATION_SHA_EXCLUDED`; `created_at` входит."""
    body = {k: v for k, v in normalize_declaration(declaration).items()
            if k not in DECLARATION_SHA_EXCLUDED}
    return hashlib.sha256(canonical(body)).hexdigest()


# ------------------------------------------------------------- запись
def new_record(declaration) -> dict:
    """Запись о прогоне из объявления: голова сверяется по схеме,
    корень дерева считается, отпечаток головы запоминается.

    Номер прогона обязателен: запись без номера нельзя ни найти, ни
    сослаться на неё из пакета постановки. Объявление без каталога,
    привязок или документов правил не принимается — умолчаний «из
    дерева» у прогона нет."""
    d = normalize_declaration(declaration)
    if not d["run_id"]:
        raise RunRecordError(
            "объявление без номера прогона: поле run_id обязательно — "
            "запись без номера нельзя ни найти, ни назвать в пакете")
    if not d["created_at"]:
        d["created_at"] = now_iso()
    if not d["lineage"]["root_sha256"]:
        d["lineage"]["root_sha256"] = root_sha256(
            d["rules_documents"], d["data_files"])
    if not d["lineage"].get("root_answers_sha256"):
        d["lineage"]["root_answers_sha256"] = root_answers_sha256(
            d["rules_documents"], d["data_files"], d["answers"])
    record = {"schema": SCHEMA, "declaration": d,
              "declaration_sha256": declaration_sha256(d),
              "journal": [], "results": empty_results()}
    words = validate(record)
    if words:
        raise RunRecordError(_refusal("объявление прогона не принято", words))
    return record


def append_stage(record: dict, entry) -> dict:
    """Дописать этап в журнал (тело записи только дописывается).

    Перед дописыванием сверяется ГОЛОВА: её отпечаток обязан равняться
    записанному при создании. Подменили объявление на ходу — отказ
    словами: рецепт повторения, который менялся по ходу прогона, не
    рецепт."""
    if not isinstance(record, dict):
        raise RunRecordError("запись о прогоне не словарь — дописывать некуда")
    kept = record.get("declaration_sha256")
    now = declaration_sha256(record.get("declaration"))
    if kept and kept != now:
        raise RunRecordError(
            "объявление изменено после старта — голова записи неизменна: "
            f"объявлено {short(kept)} / на деле {short(now)}")
    item = normalize_entry(entry)
    words = _schema_words(item, "journal_entry")
    if words:
        raise RunRecordError(_refusal(
            f"этап {item.get('stage') or '?'} в журнал не принят", words))
    record.setdefault("journal", []).append(item)
    results = record.setdefault("results", empty_results())
    if results.get("state") == STATE_DECLARED:
        results["state"] = STATE_RUNNING
    return record


def state_from_journal(record: dict) -> str:
    """Состояние прогона по журналу: сбой сильнее остановки человеком,
    остановка человеком — остановки по правилу. Ни того, ни другого —
    прогон завершён."""
    outcomes = {str(e.get("outcome")) for e in (record or {}).get("journal") or []
                if isinstance(e, dict)}
    if OUTCOME_FAILURE in outcomes:
        return STATE_FAILURE
    if OUTCOME_HUMAN in outcomes:
        return STATE_HUMAN_STOP
    if OUTCOME_RULE in outcomes:
        return STATE_RULE_STOP
    return STATE_DONE


def finish(record: dict, results=None, state: str | None = None) -> dict:
    """Закрыть запись: итоги, состояние и общий хронометраж.

    Хронометраж не назвали — он считается по журналу: время машины и
    время ожидания человека суммируются по этапам. Состояние не
    назвали — оно выводится из исходов журнала."""
    merged = {**(record.get("results") or {}), **(results or {})}
    r = normalize_results(merged)
    machine = [e.get("duration_machine_s") for e in record.get("journal") or []
               if isinstance(e, dict) and e.get("duration_machine_s") is not None]
    waiting = [e.get("duration_waiting_human_s")
               for e in record.get("journal") or []
               if isinstance(e, dict)
               and e.get("duration_waiting_human_s") is not None]
    if r["timing"]["machine_s"] is None and machine:
        r["timing"]["machine_s"] = round(sum(machine), 3)
    if r["timing"]["waiting_human_s"] is None and waiting:
        r["timing"]["waiting_human_s"] = round(sum(waiting), 3)
    if r["timing"]["total_s"] is None:
        total = (r["timing"]["machine_s"] or 0) + (r["timing"]["waiting_human_s"] or 0)
        r["timing"]["total_s"] = round(total, 3) if (machine or waiting) else None
    r["state"] = _text(state) or state_from_journal(record)
    record["results"] = r
    words = validate(record)
    if words:
        raise RunRecordError(_refusal("итоги прогона не приняты", words))
    return record


def _refusal(title: str, words: list) -> str:
    return f"{title} ({len(words)}):\n" + "\n".join("   ! " + w for w in words)


# ------------------------------------------------------------ проверка
def validate(record) -> list:
    """Слова расхождений записи со словарём полей. Пусто — запись
    сходится.

    Судится и схема (состав и виды полей), и смысл: голова не менялась,
    корень дерева сходится с отпечатками входов, каталог/привязки/
    документы названы, золотой прогон подписан."""
    words = _schema_words(record, "")
    if not isinstance(record, dict):
        return words
    words += _meaning_words(record)
    return words


def _meaning_words(record: dict) -> list:
    """Расхождения по смыслу — то, чего схема не судит."""
    out = []
    d = record.get("declaration")
    if not isinstance(d, dict):
        return ["объявление прогона отсутствует — записывать нечего"]
    kept = record.get("declaration_sha256")
    now = declaration_sha256(d)
    if kept and kept != now:
        out.append("объявление изменено после старта — голова записи "
                   f"неизменна: объявлено {short(kept)} / на деле {short(now)}")
    cat = d.get("catalog") or {}
    if not (cat.get("version") or cat.get("fingerprint")):
        out.append("объявление без каталога правил: каталог называется до "
                   "старта (версия и отпечаток) — умолчаний «из дерева» у "
                   "прогона нет")
    bind = d.get("bindings") or {}
    if not (bind.get("catalog_version") or bind.get("path")):
        out.append("объявление без привязок исполнителей COSMOS: привязки "
                   "называются до старта и выпускаются под каталог этого "
                   "прогона")
    if not (d.get("rules_documents") or []):
        out.append("объявление без документов правил авиакомпании: без ИДАК "
                   "прогона нет — сверять и повторять нечего")
    root = (d.get("lineage") or {}).get("root_sha256")
    counted = root_sha256(d.get("rules_documents"), d.get("data_files"))
    if root and root != counted:
        out.append("корень дерева прогонов не сходится с отпечатками входов: "
                   f"объявлено {short(root)} / на деле {short(counted)}")
    root_a = (d.get("lineage") or {}).get("root_answers_sha256")
    counted_a = root_answers_sha256(d.get("rules_documents"),
                                    d.get("data_files"), d.get("answers"))
    if root_a and counted_a and root_a != counted_a:
        out.append("корень дерева с ответами не сходится с ревизиями "
                   f"ответов: объявлено {short(root_a)} / на деле "
                   f"{short(counted_a)}")
    golden = (record.get("results") or {}).get("golden") or {}
    if golden.get("confirmed") and not (golden.get("by") and golden.get("at")
                                        and golden.get("words")):
        out.append("золотым прогон назван без подписи: признак ставится "
                   "только словом владельца после приёмки на Prod — нужны "
                   "кто (by), когда (at) и слова (words)")
    for n, e in enumerate((record.get("journal") or []), start=1):
        if not isinstance(e, dict):
            continue
        if e.get("outcome") == OUTCOME_NOT_STARTED and e.get("finished_at"):
            out.append(f"этап {n} ({e.get('stage')}): исход «не начат», а "
                       f"время окончания записано — одно из двух неверно")
    return out


# ------------------------------------------------------------ хранение
def load(path):
    """Запись из файла. Путь может указывать на папку прогона: тогда имя
    файла — RECORD_FILE."""
    p = Path(path)
    if p.is_dir():
        p = p / RECORD_FILE
    if not p.is_file():
        raise RunRecordError(f"записи о прогоне нет: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RunRecordError(f"запись о прогоне не разобрана как JSON: {p} — "
                             f"{e}") from e
    if not isinstance(doc, dict):
        raise RunRecordError(f"запись о прогоне не словарь: {p}")
    return doc


def save(path, record: dict):
    """Запись файлом АТОМАРНО: сперва соседний временный файл, затем
    замена. Прогон дописывает журнал по ходу, и оборванная запись
    посреди сохранения оставила бы половину строки вместо истории."""
    p = Path(path)
    if p.is_dir():
        p = p / RECORD_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


# ------------------------------------- паспорт раннера → объявление
def passport_to_declaration(passport: dict, *, run_id: str | None = None,
                            purpose: str | None = None,
                            contour: dict | str | None = None,
                            created_by: str | None = None,
                            planned_stages=None, lineage: dict | None = None,
                            answers=None, at: str | None = None) -> dict:
    """Объявление записи из паспорта прогона раннера
    (`vmp/run_passport.py`, схема `cosmos-run-passport/v1`).

    Паспорт — первое воплощение головы записи (раздел 8 проекта): он уже
    собирает и сверяет каталог, привязки, раскладку моделей, решателя,
    документы, данные, библиотеки и код. Здесь эти же величины
    называются именами записи — второго сбора параметров не заводится.

    Чего в паспорте нет и что называет вызывающий: номер прогона, цель,
    площадка, кто объявил, план этапов, родство. Номер не назвали — он
    чеканится по моменту и корню дерева (`mint_run_id`)."""
    p = passport if isinstance(passport, dict) else {}
    kit = p.get("kit") or {}
    refs = kit.get("references") or {}
    cat = p.get("catalog") or {}
    bind = p.get("bindings") or {}
    code = p.get("code") or {}
    py = p.get("python") or {}
    solver = p.get("solver") or {}
    stages = p.get("stages") or {}

    rules = [{"name": r.get("name"), "kind": _kind_of(r.get("name")),
              "sha256": r.get("sha256"), "size": r.get("bytes")}
             for r in (p.get("rules_documents") or []) if isinstance(r, dict)]
    data = [{"name": r.get("name"), "sheets": None, "rows": None,
             "sha256": r.get("sha256")}
            for r in (p.get("data_documents") or []) if isinstance(r, dict)]

    # Ответы на анкеты — часть корня дерева, но К ОБЪЯВЛЕНИЮ они
    # неизвестны: раннер идёт по книге ответов набора, а ревизии ответов
    # пишет панель по книге, СОБРАННОЙ ПОД ПРОГОН (байты другие по
    # построению) — объявленный по книге корень с ответами не сходился с
    # ревизиями (живая находка 10.09.2026, run-0141). Книга остаётся
    # ссылкой паспорта (kit.references.answer_book); answers[] и
    # root_answers_sha256 закрываются ревизиями по ходу прогона, корень с
    # ответами читатель дерева считает по ним функцией словаря. Явно
    # переданные answers (ревизии) — как есть. Книга — только политика
    # ответов («книга» против «человек»).
    book = refs.get("answer_book") or {}
    if answers is None:
        answers = []

    models = []
    for role, channels in sorted((p.get("agents_layout") or {}).get("roles",
                                                                    {}).items()):
        for channel, cfg in sorted((channels or {}).items()):
            cfg = cfg if isinstance(cfg, dict) else {}
            models.append({"role": role, "model": cfg.get("model"),
                           "provider": cfg.get("provider"), "channel": channel,
                           "effort": cfg.get("effort")})

    build = solver.get("build") or {}
    solvers = []
    if solver.get("url") or build:
        solvers.append({"task": "rostering", "engine": solver.get("engine"),
                        "version": build.get("timefold_version"),
                        "build_sha256": build.get("app_jar_sha256"),
                        "spent_limit_s": None, "unimproved_spent_limit_s": None,
                        "moves": None, "seed": None, "step_limit": None,
                        "unimproved_step_limit": None,
                        "deterministic_time": None, "accepted_uncovered": None})

    if isinstance(contour, str) or contour is None:
        services = {"panel": (p.get("panel") or {}).get("base_url"),
                    "solver": solver.get("url"),
                    "catalog": cat.get("path")}
        contour = {"name": contour or CONTOURS[0], "services": services}

    root = root_sha256(rules, data)
    root_answers = root_answers_sha256(rules, data, answers)
    declaration = {
        "run_id": run_id or mint_run_id(root),
        "created_at": at or now_iso(),
        "created_by": created_by,
        "purpose": purpose,
        "contour": contour,
        "airline": {"name": kit.get("customer"),
                    "data_class": kit.get("data_class"),
                    "llm_scope": kit.get("llm_scope")},
        "rules_documents": rules,
        "data_files": data,
        "answers": answers,
        # Название каталога — имя его папки: библиотека каталогов
        # (расширения авиакомпаний) различает их по названию, а не по
        # одному лишь номеру версии.
        "catalog": {"name": Path(str(cat.get("path") or "")).name or None,
                    "version": cat.get("version"),
                    "fingerprint": cat.get("fingerprint"),
                    "path": cat.get("path")},
        "catalog_extension": None,
        "bindings": {"catalog_version": bind.get("catalog_version"),
                     "catalog_fingerprint": bind.get("catalog_fingerprint"),
                     "path": bind.get("file")},
        "models": models,
        "solvers": solvers,
        # Обе системы живут в одном репозитории единой версии: коммит у
        # них общий, и записан он в оба поля честно, а не разными
        # числами ради вида.
        "code": {"cdpm_commit": code.get("commit"),
                 "cosmos_commit": code.get("commit"),
                 "runner_fingerprint": code.get("fingerprint")},
        "libraries": {"python": py.get("version"),
                      "packages_sha256": py.get("packages_sha256"),
                      "packages": py.get("packages") or []},
        "planned_stages": list(planned_stages or []),
        "stop_after": stages.get("until"),
        "expectations": {"kind": "манифест набора",
                         "ref": kit.get("manifest")},
        "lineage": {**{"root_sha256": root,
                       "root_answers_sha256": root_answers,
                       "parent_run": None, "branch_of": None,
                       "package_sha256": None, "source_run": None},
                    **(lineage or {})},
        "answer_policy": (ANSWER_POLICIES[1] if book.get("sha256")
                          else ANSWER_POLICIES[0]),
    }
    return normalize_declaration(declaration)


# ------------------------------------- попытка COSMOS → запись этапа
#: Вердикты COSMOS, которые значат «успех» (те же слова, что судит
#: раннер наборов).
COSMOS_OK_VERDICTS = ("пейринг построен", "допустимо")

#: Задача попытки → имя этапа записи.
COSMOS_STAGES = {"pairing": "пейринг", "rostering": "ростер"}

#: Роль сертификатора в расходе попытки: КОД (имена файлов обменов и
#: отпечаток вызова — их сверяет повтор из журнала) и СЛОВА (сводка
#: расхода `vmp/run/llm_exchanges.py` и карточка). Читаются оба: свод
#: пишет слова, записи прежних попыток — код.
CERTIFIER_ROLE_CODE = "certifier"
CERTIFIER_ROLE_WORDS = "сертификатор"

#: Код расхода попытки, означающий «вызовов моделей НЕ БЫЛО» (его
#: ставит писатель обменов COSMOS, `webapp/package_runs._resources`).
#: ТОЛЬКО он позволяет назвать число вызовов нулём: прочие коды значат
#: «расход не прочитан», а это не то же самое, что ноль.
NO_MODEL_CALLS_CODE = "no-model-calls"


def _is_certifier_row(row) -> bool:
    """Строка расхода — про сертификатора? Роль зовут и кодом, и
    словами, и обе формы законны."""
    r = row if isinstance(row, dict) else {}
    return str(r.get("role_code") or r.get("role") or "") in (
        CERTIFIER_ROLE_CODE, CERTIFIER_ROLE_WORDS)


def _certifier_row(cert: dict, known_zero: bool) -> dict:
    """Строка ОБЪЯВЛЕННОГО сертификатора в расходе попытки.

    Модель объявлена — значит она обязана быть ВИДНА в итогах: строки
    нет только там, где модель не назвали, и её отсутствие читается
    «сертификатор не объявлен». Поэтому нулевой расход показывается
    явным «вызовов 0» с нулевыми токенами — ноль СОСЧИТАН (`counted`),
    а не пропущен. Число вызовов неизвестно (расход попытки не
    прочитан, запись прежней версии) — честное «не считается» с
    причиной словами: выдавать неизвестное за ноль нельзя."""
    channel = cert.get("channel") or "не назван"
    return {"role": CERTIFIER_ROLE_WORDS, "role_code": CERTIFIER_ROLE_CODE,
            "model": cert.get("model"), "provider": cert.get("provider"),
            "calls": 0 if known_zero else None,
            "input_tokens": 0 if known_zero else None,
            "output_tokens": 0 if known_zero else None,
            "counted": bool(known_zero),
            "note": ("канал %s: вызовов не было" % channel) if known_zero
            else ("канал %s: расход попытки не прочитан" % channel)}


def attempt_to_journal(attempt: dict) -> dict:
    """Запись этапа из записи попытки COSMOS (`webapp/package_runs.py`,
    папка нажатий `package-runs/<sha пакета>/`).

    Имена читаются по разделу 3.2.3 проекта: параметры — `request` (у
    сегодняшнего носителя они лежат под именем `params`), сводка выдачи
    — `summary`, кто нажал — `actor`, код — `code`. Расход моделей
    берётся из попытки, если она его уже несёт; ОБЪЯВЛЕННЫЙ
    сертификатор стоит строкой всегда — при нуле вызовов с явным
    «вызовов 0», а там, где число вызовов неизвестно, — с честным «не
    считается» и причиной словами.

    Попытка ещё идёт — исход назвать нечем: этап помечается «не начат»
    со словами «попытка идёт». Выдумывать вердикт незаконченному расчёту
    запись не имеет права."""
    a = attempt if isinstance(attempt, dict) else {}
    req = a.get("request") if isinstance(a.get("request"), dict) \
        else (a.get("params") if isinstance(a.get("params"), dict) else {})
    summary = a.get("summary") if isinstance(a.get("summary"), dict) else {}
    task = str(a.get("task") or req.get("task") or "")
    stage = COSMOS_STAGES.get(task, task or "нажатие COSMOS")
    if task == "rostering" and (a.get("pairing_selection")
                                or req.get("pairing_run_id")):
        stage = "ростер на пейринге"

    verdict = a.get("verdict") if a.get("verdict") is not None \
        else summary.get("verdict")
    error = _text(a.get("error"))
    done = bool(a.get("done"))
    reason = _text(a.get("reason_code"))
    if error:
        outcome = OUTCOME_RULE if reason else OUTCOME_FAILURE
        words = error
    elif not done:
        outcome, reason = OUTCOME_NOT_STARTED, reason or "attempt-unfinished"
        words = "попытка идёт: исход ещё не назван"
    elif verdict in COSMOS_OK_VERDICTS:
        outcome, words = OUTCOME_OK, f"вердикт: {verdict}"
    elif verdict:
        outcome, words = OUTCOME_WARN, f"вердикт: {verdict}"
        reason = reason or "verdict-not-ok"
    else:
        outcome, words = OUTCOME_WARN, "попытка закончена без вердикта"
        reason = reason or "no-verdict"

    artifacts = []
    if a.get("package_sha"):
        artifacts.append({"kind": "пакет постановки", "path": None,
                          "sha256": a.get("package_sha")})
    if a.get("catalog"):
        artifacts.append({"kind": "каталог выдачи", "path": a.get("catalog"),
                          "sha256": None})

    counters = {"chains": summary.get("chains"), "findings": summary.get("findings"),
                "uncovered": summary.get("uncovered"),
                "certificate_gate": summary.get("certificate_gate"),
                "score": summary.get("score")}

    resources = a.get("resources") if isinstance(a.get("resources"),
                                                 dict) else {}
    models = [m for m in (resources.get("models") or []) if isinstance(m, dict)]
    # ОБЪЯВЛЕННЫЙ сертификатор — всегда строкой расхода (поручение
    # владельца о нулевых вызовах, 11.09.2026). Свод расхода строит
    # строки по СОСТОЯВШИМСЯ вызовам, и при нуле вызовов объявленная
    # модель из итогов пропадала — а пропажа читается «сертификатор не
    # объявлен». Ноль вызовов ЗНАЕТСЯ в двух случаях: свод вернул
    # строки, но сертификатора среди них нет (он не звался), либо свод
    # прямо сказал «вызовов не было» кодом. Иначе число вызовов
    # неизвестно, и строка честно говорит «не считается».
    cert = a.get("certifier") if isinstance(a.get("certifier"), dict) else {}
    if cert.get("model") and not any(_is_certifier_row(m) for m in models):
        known_zero = bool(models) or \
            str(resources.get("models_code") or "") == NO_MODEL_CALLS_CODE
        models = models + [_certifier_row(cert, known_zero)]

    return normalize_entry({
        "stage": stage,
        "substage": a.get("id"),
        "started_at": a.get("started_at"),
        "finished_at": a.get("finished_at"),
        "duration_machine_s": None,
        "duration_waiting_human_s": None,
        "outcome": outcome,
        "reason_code": reason,
        "words": words,
        "actor": a.get("actor"),
        "artifacts": artifacts,
        "counters": counters,
        "resources": {"models": models,
                      "solver_seconds": a.get("solver_seconds")},
        "system": SYSTEM_COSMOS,
    })


# ------------------------------------------------------ слова человеку
def summary(record: dict) -> str:
    """Запись словами: голова, путь по этапам, итоги. Из этого текста
    собираются карточка прогона и строки журнала — человек читает не
    JSON."""
    r = record if isinstance(record, dict) else {}
    d = r.get("declaration") or {}
    res = r.get("results") or {}
    cat = d.get("catalog") or {}
    bind = d.get("bindings") or {}
    air = d.get("airline") or {}
    lin = d.get("lineage") or {}
    tim = res.get("timing") or {}
    golden = res.get("golden") or {}

    rows = [f"ЗАПИСЬ О ПРОГОНЕ {d.get('run_id')} ({r.get('schema')}) — "
            f"{res.get('state')}",
            f"   цель: {d.get('purpose') or 'не названа'}; площадка: "
            f"{(d.get('contour') or {}).get('name')}; объявлена "
            f"{d.get('created_at')}"
            + (f" ({d.get('created_by')})" if d.get("created_by") else ""),
            f"   авиакомпания: {air.get('name') or 'не названа'}"
            + (f"; класс данных {air.get('data_class')}"
               if air.get("data_class") else "")
            + (f"; допуск моделей {air.get('llm_scope')}"
               if air.get("llm_scope") else ""),
            f"   входы: документов {len(d.get('rules_documents') or [])}, "
            f"книг данных {len(d.get('data_files') or [])}, ревизий ответов "
            f"{len(d.get('answers') or [])}; корень дерева "
            f"{short(lin.get('root_sha256'))}",
            f"   каталог {cat.get('version')} ({short(cat.get('fingerprint'))})"
            + (f", расширение {(d.get('catalog_extension') or {}).get('name')}"
               if d.get("catalog_extension") else "")
            + f"; привязки под каталог {bind.get('catalog_version')} "
              f"({short(bind.get('catalog_fingerprint'))})",
            # Систему модели строка называет только тогда, когда та
            # названа: у записей до 11.09.2026 поля нет, и дописывать
            # «cdpm» за них было бы выдумкой.
            "   модели: " + (", ".join(
                f"{m.get('role')}={m.get('model')}"
                + (f" [{m['system']}]" if m.get("system") else "")
                for m in (d.get("models") or [])) or "не названы")
            + (f"; {d['models_note']}" if d.get("models_note") else ""),
            "   решатели: " + (", ".join(
                f"{s.get('task')}={s.get('engine')}"
                for s in (d.get("solvers") or [])) or "не названы"),
            ]
    if lin.get("parent_run") or lin.get("source_run"):
        rows.append(f"   происхождение: родитель {lin.get('parent_run') or 'нет'}"
                    f", прогон-источник {lin.get('source_run') or 'нет'}"
                    + (f", ветвь по: {lin.get('branch_of')}"
                       if lin.get("branch_of") else ""))
    rows.append("   путь по этапам:")
    for e in r.get("journal") or []:
        if not isinstance(e, dict):
            continue
        name = e.get("stage") + (f"/{e.get('substage')}" if e.get("substage")
                                 else "")
        sec = e.get("duration_machine_s")
        wait = e.get("duration_waiting_human_s")
        rows.append(f"      {name}: {e.get('outcome')}"
                    + (f" ({e.get('reason_code')})" if e.get("reason_code") else "")
                    + (f", {sec:.0f} с машины" if isinstance(sec, (int, float))
                       else "")
                    + (f", {wait:.0f} с ожидания человека"
                       if isinstance(wait, (int, float)) else "")
                    + (f" — {e.get('words')}" if e.get("words") else ""))
    if not (r.get("journal") or []):
        rows.append("      этапов ещё нет — прогон объявлен")
    plan = [s for s in (d.get("planned_stages") or [])]
    if plan:
        rows.append("   план этапов: " + ", ".join(plan)
                    + (f"; остановка после {d.get('stop_after')}"
                       if d.get("stop_after") else ""))
    verdicts = res.get("verdicts") or {}
    rows.append("   вердикты: " + (", ".join(f"{k}={v}" for k, v in
                                             sorted(verdicts.items()))
                                   or "нет"))
    if res.get("deliveries"):
        rows.append("   выдачи: " + ", ".join(
            f"{x.get('kind')} {short(x.get('sha256'))}"
            for x in res["deliveries"]))
    rows.append("   хронометраж: машина {}, ожидание человека {}, всего {}"
                .format(*(f"{v} с" if v is not None else "не считалось"
                          for v in (tim.get("machine_s"),
                                    tim.get("waiting_human_s"),
                                    tim.get("total_s")))))
    rows.append("   золотой прогон: "
                + (f"да — {golden.get('by')}, {golden.get('at')}: "
                   f"{golden.get('words')}" if golden.get("confirmed")
                   else "не подтверждён"))
    return "\n".join(rows)
