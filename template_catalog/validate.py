# -*- coding: utf-8 -*-
"""Правила validate() каталога шаблонов (§11 схемы записи).

Возврат — список Problem(where, code, message, severity); пустой —
зелёный. validate() никогда не бросает и ничего не пишет: перечни из
файла проходят через _strs() (элемент не строка — S02, а не падение на
set()/in), а тело проверки обёрнуто страховкой — внутренняя ошибка
любого правила становится Problem S02 «проверка прервана». Группы:
S — схема и форма файлов; D — словари; X — перекрёстные ссылки;
H — шапка и версии; R — заявки; A — обезличенность (классовые образцы
к прозе; перечень имён заказчиков контура подаётся снаружи —
forbidden_terms). jsonschema импортируется лениво: без него — одно
предупреждение S06, структурный минимум судится кодом.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import canon
from .units import CLOCK_WORD, UNIT_FORMS

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "catalog.schema.json"
SCHEMA_070_PATH = SCHEMA_PATH.with_name("catalog-0.7.schema.json")

#: X20: направление критерия — поле ЗАПИСИ (0.7.0), словарь `directions`.
#: Ключ словаря назван здесь, чтобы правило и словарь не разъехались.
DIRECTIONS_KEY = "directions"
#: X21: единицы, у которых карта `unit_words` вправе нести множители;
#: у остальных кодов слово множителя не меняет (счёт есть счёт).
SCALED_UNITS = ("duration_minutes", "time_of_day_minutes")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,50}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-draft\.([1-9]\d*))?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQ_ID_RE = re.compile(r"^req-\d{4,}$")
#: Образец ключа строки очереди изменений COSMOS (шаг 6, 0.3): ключ —
#: проекция номера заявки, закон — template_catalog.requests.cr_key_of.
_CR_KEY_RE = re.compile(r"^cr[0-9a-f]{8}$")

#: Ключи словарей §4 (закрытые перечни строк).
TOKEN_KEYS = (
    "classes", "kinds", "zones", "family_natures", "violation_sizes",
    "history_read_kinds", "nature_witnesses", "directions",
    "param_units", "code_units", "param_relations", "param_tz",
    "area_qualifiers", "qualifier_kinds", "fit_categories",
    "condition_units", "condition_relations", "weekdays", "executor_kinds",
    "stand_task_kinds", "executor_states", "record_statuses",
    "request_states", "request_source_kinds", "request_submitters",
    "actors", "origin_kinds", "history_kinds", "rates_policies", "products",
    "languages", "task_kinds", "stand_results", "param_sources",
    "check_template_groups")
#: Ключи словарей §4, значение которых — ОБЪЕКТ, а не перечень.
#: `violation_units` — карта «код единицы → слово» (0.5.0): членство
#: судится по КЛЮЧАМ, слово единицы живёт рядом с кодом и второй карты
#: перевода у продуктов нет. `unit_words` — карта «код единицы → канон,
#: форма значения, слова записи, множители» (0.7.0): один носитель слов
#: единиц для анкеты CDPM, ведомости COSMOS и стендов (X21).
OBJECT_KEYS = ("synthetic_codes", "acceptance_default", "lexicon_order",
               "world_forms", "check_templates", "unbound_params",
               "violation_units", "unit_words")

_NULLSTR = (str, type(None))
_FAMILY_SHAPE = {
    "id": str, "class": str, "title": str, "subject": str,
    "subject_words": (list, type(None)), "complements": list, "kind": str,
    "zone": str, "nature": str, "nature_confirmed_by": str,
    "violation_unit": _NULLSTR, "violation_size": _NULLSTR,
    "reads_history": (dict, type(None)), "params": list,
    "param_sets": list, "conditions": list, "rule_form": (dict, type(None)),
    "semantics": dict, "executor_kinds": list, "check_templates": list,
    "acceptance": _NULLSTR, "origin": dict, "note": _NULLSTR,
}
_RECORD_TAIL = {"stand_tasks": list, "status": str, "history": list}
_CRITERION_SHAPE = {
    "id": str, "class": str, "title": str, "zone": str, "needs": _NULLSTR,
    "semantics": dict, "instance_form": (dict, type(None)),
    "computable_by": list, "refusal": _NULLSTR, "acceptance": _NULLSTR,
    "origin": dict, "note": _NULLSTR,
}
#: Поля записи критерия сверх закрытой формы S02: `direction` (0.7.0)
#: обязательно по СТАТУСУ, а не по форме — судит его X20. Держать его в
#: `_CRITERION_SHAPE` нельзя: запись без поля выпала бы из разбора S02
#: целиком, и правило A06 промолчало бы там, где обязано назвать беду.
CRITERION_EXTRA = ("direction",)
_HEADER_SHAPE = {
    "form_version": int, "name": str, "version": str, "fingerprint": str,
    "legacy_registry_rev": _NULLSTR, "released_at": str, "released_by": str,
    "note": str, "sections": dict, "gates": dict,
}
_REQUEST_SHAPE = {
    "request_id": str, "state": str, "section": str, "record_id": str,
    "submitted_at": str, "submitted_by": str, "source": dict,
    "draft": (dict, type(None)), "executor_kinds_requested": (list, type(None)),
    "decision": dict, "cr_key": _NULLSTR,
}

#: Форма ожидания стендовой задачи по виду исполнителя (§3).
EXPECTED_FORMS = {
    # Each named export has a closed shape checked by stand_contracts above.
    "export_form": ("symbol",),
    "kotlin_constraint": ("matches",), "kotlin_logic_form": ("matches",),
    "builder_method": ("value", "unit"), "check_template": ("findings",),
    "check_code": ("findings",), "kotlin_metric": ("value",),
}

#: X09: допустимые единицы параметра по уточнителю; area сверх того
#: требует непустого словаря area_qualifiers.
_QUALIFIER_UNITS = {
    "area": ("duration_minutes", "time_of_day_minutes"),
    "base_code": ("count",),
    "crew_variant": ("count",),
}

#: X17: КТО подтверждает природу семьи — независимый свидетель закона
#: паритета L15 (таблица §1.4 проекта выпуска 0.5.0). `solver_tables` —
#: таблицы решателя, `certifier_registry` — перечни сертификата ростера,
#: `change_request` — вердикт судьи заявки на изменение, `none` —
#: отрицательное свидетельство: семьи нет ни у решателя, ни у
#: сертификатора. Свидетель `change_request` природе `history` НЕ
#: положен: историю читает решатель, и без его формы семья истории не
#: работает (X13).
_NATURE_WITNESSES = {
    "history": ("solver_tables",),
    "people": ("certifier_registry", "change_request"),
    "world_param": ("certifier_registry", "change_request"),
    "schedule": ("none", "change_request"),
}
#: X18: слово `origin.kind` записи, пришедшей КОНВЕЙЕРОМ ПОПОЛНЕНИЯ
#: (заявка CDPM → кандидат запроса на изменение → сборка службой).
ORIGIN_REQUEST = "заявка"

# --- образцы обезличенности (A) ---
#: Код станции/базы: латинский (IATA/ICAO) либо кириллический трёх-
#: четырёхбуквенный внутренний код (перевозчики России и Средней Азии).
_A_STATION = re.compile(r"\b(?:[A-Z]{3,4}|[А-ЯЁ]{3,4})\b")
_A_STATION_CTX = re.compile(r"баз|аэропорт|станци", re.IGNORECASE)
_A_FLEET = re.compile(r"\b(A3\d\d|B7\d\d|A2\d\d|E1\d\d|SSJ|MC-?21|Q400|ATR)\b")
_A_TAB = re.compile(r"\b\d{4,}\b")
_A_TAB_CTX = re.compile(r"табельн", re.IGNORECASE)
_A_CLAUSE = re.compile(
    r"(?:(?<![\w])п\.\s*\d+(?:\.\d+)*|§\s*\d+|(?<![\w])раздел\w*\s+\d+(?:\.\d+)*"
    r"|(?<![\w])ст\.\s*\d+)", re.IGNORECASE)
_A_QUOTE = re.compile(r"[«\"]([^»\"]{81,})[»\"]")
_A_RULEKEY = re.compile(r"\b[RO]-S\d+-\S+")
_A_MONEY = re.compile(
    r"\d[\d\s.,]*\s*(?:руб\w*|₽|\$|€|USD|EUR|RUB|сум\w*|тенге|долл\w*)",
    re.IGNORECASE)
_A_DOCDATE = re.compile(r"(?<![\w])от\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*(?:г\.?)?\s*№")
_A_CODE = (
    ("имя файла кода", re.compile(r"[A-Za-z_]+\.(py|kt)\b")),
    ("вызов asConstraint", re.compile(r"asConstraint")),
    ("вызов метода", re.compile(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*\(")),
    ("ключ командной строки", re.compile(r"--[a-z][a-z-]+")),
    ("идентификатор camelCase", re.compile(r"\b[a-z]+[A-Z][A-Za-z]+\b")),
    ("путь", re.compile(r"\b[A-Za-z_][\w.-]*/[A-Za-z_][\w./-]*")),
)
_A_THRESH = re.compile(r"\d+(?:[.,]\d+)?\s*(?:ч|час|мин|сут|дн|ноч|выходн)")
_A_THRESH_CTX = re.compile(r"не более|не менее|≤|≥|(?<![\w])до(?![\w])|(?<![\w])от(?![\w])",
                           re.IGNORECASE)
_A_STRICT_FIELDS = ("title", "semantics", "acceptance")
_A_DOC_CLASS = re.compile(r"документ|положени|регламент|инструкц|правил|"
                          r"коллективн|руководств|приказ", re.IGNORECASE)


@dataclass(frozen=True)
class Problem:
    where: str
    code: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.where}: {self.message}"


class _Ctx:
    """Накопитель проблем и словари для правил."""

    def __init__(self, dicts: dict, forbidden_terms):
        self.problems: list[Problem] = []
        self.dicts = dicts if isinstance(dicts, dict) else {}
        self.forbidden = _compile_terms(forbidden_terms)
        self.catalog_070 = False

    def err(self, where, code, message):
        self.problems.append(Problem(where, code, message, "error"))

    def warn(self, where, code, message):
        self.problems.append(Problem(where, code, message, "warning"))

    def tokens(self, key) -> list:
        """Слова словаря `key`: перечень отдаёт значения, КАРТА — ключи.

        Ветка карты обязательна: `violation_units` с 0.5.0 — объект
        «код единицы → слово», и без неё D01 по единице нарушения
        замолчал бы (пустой перечень словаря `check_token` считает
        предметом D02 и пропускает любое значение)."""
        v = self.dicts.get(key)
        if isinstance(v, dict):
            return [k for k in v if isinstance(k, str)]
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    def check_token(self, where, code, value, key, *, nullable=False):
        if value is None and nullable:
            return True
        toks = self.tokens(key)
        if not toks:
            return True           # словарь сам — предмет D02
        if value not in toks:
            self.err(where, code,
                     f"значение {value!r} вне словаря {key} "
                     f"({', '.join(toks)})")
            return False
        return True


def _compile_terms(terms) -> list:
    out = []
    for t in terms or ():
        if isinstance(t, re.Pattern):
            out.append(t)
        elif isinstance(t, str) and t.strip():
            out.append(re.compile(r"(?<![\w])" + re.escape(t.strip())
                                  + r"(?![\w])", re.IGNORECASE))
    return out


# ------------------------------------------------------------ helpers
def _lst(v) -> list:
    return v if isinstance(v, list) else []


def _dct(v) -> dict:
    return v if isinstance(v, dict) else {}


def _sid(rec) -> str:
    return str(rec.get("id")) if isinstance(rec, dict) else "?"


def _strs(ctx, where, v, *, suffix: str = "", code: str = "S02") -> list:
    """Перечень из файла → список строк. Элемент не строка — Problem
    (S02 по умолчанию) с адресом where[i]suffix, элемент исключается:
    правила дальше строят set() и судят «x in …» только над строками и
    не падают на списке или объекте в перечне."""
    out = []
    for i, x in enumerate(_lst(v)):
        if isinstance(x, str):
            out.append(x)
        else:
            ctx.err(f"{where}[{i}]{suffix}", code,
                    f"элемент перечня не строка ({type(x).__name__})")
    return out


def _only_strs(v) -> set:
    """Множество строк перечня без доклада (для словарей-справок)."""
    return {x for x in _lst(v) if isinstance(x, str)}


def _shape(ctx, where, obj, shape: dict, *, closed=True, extra_allowed=()):
    """Структурный минимум S02 кодом: поля присутствуют, типы верны,
    лишних нет. Возврат True — форма пригодна для дальнейших правил."""
    ok = True
    if not isinstance(obj, dict):
        ctx.err(where, "S02", "запись не объект JSON")
        return False
    for k, typ in shape.items():
        if k not in obj:
            ctx.err(f"{where}.{k}", "S02", "поле отсутствует (все поля "
                    "записи обязательны; «нет значения» = null или [])")
            ok = False
            continue
        v = obj[k]
        if isinstance(v, bool) and typ is int:
            ctx.err(f"{where}.{k}", "S02", "ожидалось целое, дан флаг")
            ok = False
        elif not isinstance(v, typ):
            ctx.err(f"{where}.{k}", "S02",
                    f"неверный тип {type(v).__name__}")
            ok = False
    if closed:
        for k in obj:
            if k not in shape and k not in extra_allowed:
                ctx.err(f"{where}.{k}", "S02", "лишнее поле (схема закрытая)")
                ok = False
    return ok


def _walk_strings(v, where, out):
    if isinstance(v, dict):
        for k, x in v.items():
            _walk_strings(x, f"{where}.{k}", out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk_strings(x, f"{where}[{i}]", out)
    elif isinstance(v, str):
        out.append((where, v))


def _walk_numbers(v, where, out):
    if isinstance(v, dict):
        for k, x in v.items():
            _walk_numbers(x, f"{where}.{k}", out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk_numbers(x, f"{where}[{i}]", out)
    elif isinstance(v, float):
        out.append((where, v))


# ------------------------------------------------------------- S: схема
def _uses_schema_070(version) -> bool:
    # Owner-released 0.6.0 still has the legacy form. New fields belong to
    # the separate 0.7 line; never reinterpret immutable earlier releases.
    match = _SEMVER_RE.fullmatch(str(version))
    return bool(match and tuple(int(match.group(i)) for i in (1, 2, 3)) >= (0, 7, 0))


def schema_path_for_version(version=None) -> Path:
    """Select the declared catalogue contract without upgrading old releases."""
    return SCHEMA_070_PATH if _uses_schema_070(version) else SCHEMA_PATH


def _load_schema(version=None) -> dict | None:
    try:
        return json.loads(schema_path_for_version(version).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _import_jsonschema():
    """Ленивый импорт jsonschema (единственное место в пакете); None —
    пакет не установлен, проверка схемы недоступна."""
    try:
        import jsonschema
    except ImportError:
        return None
    return jsonschema


def schema_messages(doc, schema: dict, label: str) -> list[str]:
    """Сообщения о нарушениях формы документа по ЕГО схеме — строками
    «<label>/<адрес>: <слова>».

    Единственная точка ленивого импорта `jsonschema` в читателе живёт в
    этом файле (гейт независимости пакета), поэтому чужие формы —
    файл-кандидат `candidates.py` и всё, что появится дальше — судятся
    отсюда, а не заводят второй импорт. Пакета нет или схема пуста —
    пустой перечень: у вызывающего есть структурный минимум кодом, и
    молчание здесь не выдаёт негодный файл за годный."""
    if not isinstance(schema, dict) or not schema:
        return []
    jsonschema = _import_jsonschema()
    if jsonschema is None:
        return []
    try:
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(doc),
                        key=lambda e: list(map(str, e.absolute_path)))
    except Exception as e:                                   # noqa: BLE001
        return [f"{label}: проверка схемой не выполнена: {e}"]
    out = []
    for e in errors:
        where = ".".join(str(p) for p in e.absolute_path) or "(корень)"
        out.append(f"{label}/{where}: {e.message}")
    return out


def _where_from_path(section, doc, path) -> str:
    parts = list(path)
    where = section
    if parts and parts[0] in ("families", "criteria", "requests", "versions"):
        recs = _lst(_dct(doc).get(parts[0]))
        if len(parts) >= 2 and isinstance(parts[1], int) and parts[1] < len(recs):
            rec = recs[parts[1]]
            key = "request_id" if parts[0] == "requests" else \
                ("version" if parts[0] == "versions" else "id")
            rid = rec.get(key) if isinstance(rec, dict) else None
            where = f"{parts[0]}/{rid if rid is not None else parts[1]}"
            parts = parts[2:]
            # Черновик заявки адресуется requests/<id>/draft.<поле> —
            # тот же адрес, что у правил R03 (приложение п.6 схемы).
            if parts and parts[0] == "draft" and section == "requests":
                where += "/draft"
                parts = parts[1:]
        else:
            where = parts[0]
            parts = parts[1:]
    for p in parts:
        where += f"[{p}]" if isinstance(p, int) else f".{p}"
    return where


_REQUIRED_MSG_RE = re.compile(r"'(.+?)' is a required property")
_ADDITIONAL_MSG_RE = re.compile(r"\('(.+?)'(?:,| was| were)")


def _schema_where(where: str, e) -> str:
    """Адрес ошибки схемы по полю: отсутствующее (required) и лишнее
    (additionalProperties) поле адресуются как <объект>.<поле> — так же,
    как структурный минимум _shape, иначе две ошибки одного объекта
    сливались бы в один адрес."""
    if e.validator == "required":
        m = _REQUIRED_MSG_RE.match(e.message)
        if m:
            return f"{where}.{m.group(1)}"
    elif e.validator == "additionalProperties":
        m = _ADDITIONAL_MSG_RE.search(e.message)
        if m:
            return f"{where}.{m.group(1)}"
    return where


def _draft_errors(jsonschema, schema: dict, doc, e):
    """Ошибка oneOf у черновика заявки не называет поля: черновик судится
    отдельно по схеме своего раздела (family_draft / criterion_draft), и
    адреса получают вид requests/<id>/draft.<поле>. None — не тот случай."""
    path = list(e.absolute_path)
    if not (e.validator == "oneOf" and len(path) == 3 and path[0] == "requests"
            and path[2] == "draft"):
        return None
    recs = _lst(_dct(doc).get("requests"))
    rq = _dct(recs[path[1]]) if isinstance(path[1], int) and path[1] < len(recs) else {}
    draft = rq.get("draft")
    defname = {"families": "family_draft",
               "criteria": "criterion_draft"}.get(rq.get("section"))
    if not isinstance(draft, dict) or defname is None:
        return None
    sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"],
           "$ref": f"#/$defs/{defname}"}
    try:
        errs = sorted(jsonschema.Draft202012Validator(sub).iter_errors(draft),
                      key=lambda x: list(map(str, x.absolute_path)))
    except Exception:                               # noqa: BLE001
        return None
    if not errs:
        return None
    base = _where_from_path("requests", doc, path)
    out = []
    for e2 in errs:
        w = base
        for p in e2.absolute_path:
            w += f"[{p}]" if isinstance(p, int) else f".{p}"
        out.append((e2, _schema_where(w, e2)))
    return out


def _schema_check(ctx, docs: dict, single_file: bool):
    version = _dct(docs.get("header")).get("version")
    schema = _load_schema(version)
    if schema is None or "$defs" not in schema:
        ctx.warn("catalog", "S06", f"схема {schema_path_for_version(version).name} не читается — "
                 "проверка схемы недоступна, судится структурный минимум")
        return
    jsonschema = _import_jsonschema()
    if jsonschema is None:
        ctx.warn("catalog", "S06", "пакет jsonschema не установлен — проверка "
                 "схемы недоступна, судится структурный минимум")
        return
    plan = (("catalog.json", "header", docs.get("header")),
            ("families", "families_file", docs.get("families")),
            ("criteria", "criteria_file", docs.get("criteria")),
            ("dictionaries", "dictionaries_file", docs.get("dictionaries")),
            ("requests", "requests_file", docs.get("requests")))
    if docs.get("versions") is not None:
        plan += (("versions.json", "versions_file", docs.get("versions")),)
    seen = {p.where for p in ctx.problems if p.code == "S02"}
    for section, defname, doc in plan:
        if doc is None:
            continue
        sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"],
               "$ref": f"#/$defs/{defname}"}
        try:
            validator = jsonschema.Draft202012Validator(sub)
            errors = sorted(validator.iter_errors(doc),
                            key=lambda e: list(map(str, e.absolute_path)))
        except Exception as e:                      # noqa: BLE001
            ctx.warn(section, "S06", f"проверка схемы не выполнена: {e}")
            continue
        # Отказ вложенной ветви allOf сбрасывает её аннотации, и
        # unevaluatedProperties ругается на все поля записи разом —
        # это каскад, а не самостоятельная находка: при других ошибках
        # той же записи он опускается.
        located = []
        for e in errors:
            expanded = _draft_errors(jsonschema, schema, doc, e) \
                if section == "requests" else None
            if expanded:
                located += expanded
            else:
                located.append((e, _schema_where(
                    _where_from_path(section, doc, e.absolute_path), e)))
        others = {w for e, w in located if e.validator != "unevaluatedProperties"}
        for e, where in located:
            if e.validator == "unevaluatedProperties" and \
                    any(o == where or o.startswith(where + ".")
                        or o.startswith(where + "[") for o in others):
                continue
            if where in seen:
                continue
            seen.add(where)
            ctx.err(where, "S02", f"не по схеме: {e.message[:200]}")


def _form_checks(ctx, docs: dict):
    # S01: form_version == 1 у каждого файла раздела
    for name in ("header", "families", "criteria", "dictionaries", "requests",
                 "versions"):
        doc = docs.get(name)
        if doc is None:
            continue
        label = "catalog.json" if name == "header" else name
        if not isinstance(doc, dict):
            ctx.err(label, "S01", "корень файла не объект JSON")
            continue
        if doc.get("form_version") != 1 or isinstance(doc.get("form_version"), bool):
            ctx.err(f"{label}/form_version", "S01",
                    f"ожидалась форма 1, дано {doc.get('form_version')!r}")
    # S03 / S04: числа и строки
    for name in ("header", "families", "criteria", "dictionaries", "requests",
                 "versions"):
        doc = docs.get(name)
        if doc is None:
            continue
        label = "catalog.json" if name == "header" else name
        nums: list = []
        _walk_numbers(doc, label, nums)
        for where, v in nums:
            ctx.err(where, "S03", f"число с дробной частью или экспонентой "
                    f"({v!r}) — только целые")
        strs: list = []
        _walk_strings(doc, label, strs)
        for where, s in strs:
            try:
                s.encode("utf-8")
            except UnicodeEncodeError:
                ctx.err(where, "S04", "строка не кодируется в UTF-8 "
                        "(одиночный суррогат)")
                continue
            if unicodedata.normalize("NFC", s) != s:
                ctx.warn(where, "S04", "строка не в форме NFC — инструмент "
                         "перепишет")


def _order_check(ctx, cat):
    """S05: порядок в файле канонический (§9 п.2) — предупреждение."""
    try:
        body = cat.body()
        normed = canon.normalize(body)
    except ValueError:
        return          # S03/S04 уже названы
    for sec in ("families", "criteria"):
        raw = [r for r in body[sec] if isinstance(r, dict)]
        raw_ids = [str(r.get("id")) for r in raw]
        if raw_ids != sorted(raw_ids):
            ctx.warn(sec, "S05", "записи в файле не по id — порядок не "
                     "канонический")
        by_id = {str(r.get("id")): r for r in normed[sec] if isinstance(r, dict)}
        for r in raw:
            n = by_id.get(str(r.get("id")))
            if n is not None and json.dumps(n, sort_keys=True, ensure_ascii=False) \
                    != json.dumps(canon._deep(r, "x"), sort_keys=True,
                                  ensure_ascii=False):
                ctx.warn(f"{sec}/{r.get('id')}", "S05",
                         "списки записи не в каноническом порядке")
    if json.dumps(normed["dictionaries"], sort_keys=True, ensure_ascii=False) \
            != json.dumps(canon._deep(body["dictionaries"], "d"),
                          sort_keys=True, ensure_ascii=False):
        ctx.warn("dictionaries", "S05", "перечни словарей не в каноническом "
                 "порядке")


# ---------------------------------------------------------- D: словари
def _unit_words_check(ctx, d: dict, where: str) -> None:
    """X21 (0.7.0): полнота и форма карты слов единиц `unit_words`.

    Карта — ОДИН носитель слов и множителей для всех сторон (контракт П0
    §3.3 (б), сторона COSMOS §2.2–2.3): у каждого кода `param_units` ∪
    `condition_units` есть запись с непустыми `canon`, `form`, `words`;
    лишних кодов нет; множители (`scale`) — только у длительности и
    времени суток и только положительными числами по словам, которые
    карта же и объявила. Пустое место в карте — не «нет слов», а дыра,
    через которую вернётся вторая рукописная карта единиц у продукта."""
    if not ctx.catalog_070:
        return
    import math
    from .units import _word
    key = "unit_words"
    words = d.get(key)
    if not isinstance(words, dict):
        if key in d:
            ctx.err(f"{where}.{key}", "X21",
                    "слова единиц — карта «код единицы → {canon, form, "
                    "words, scale}», а не перечень")
        return
    codes = set(ctx.tokens("param_units")) | set(ctx.tokens("condition_units"))
    if codes:
        нет = sorted(codes - set(words))
        лишние = sorted(set(words) - codes)
        if нет:
            ctx.err(f"{where}.{key}", "X21",
                    f"нет записи у кодов единиц: {нет} — слово и форма "
                    f"значения обязаны быть названы у каждой единицы "
                    f"каталога")
        if лишние:
            ctx.err(f"{where}.{key}", "X21",
                    f"коды вне словарей param_units ∪ condition_units: "
                    f"{лишние}")
    for code, entry in sorted(words.items()):
        w = f"{where}.{key}.{code}"
        if not isinstance(entry, dict):
            ctx.err(w, "X21", "запись карты — объект {canon, form, words, "
                    "scale}")
            continue
        if set(entry) != {"canon", "form", "words", "scale"}:
            ctx.err(w, "X21", "закрытая запись требует ровно canon, form, words, scale")
        canon_word = entry.get("canon")
        if not (isinstance(canon_word, str) and canon_word.strip()):
            ctx.err(f"{w}.canon", "X21", "слово канона пусто")
        form = entry.get("form")
        if form not in UNIT_FORMS:
            ctx.err(f"{w}.form", "X21",
                    f"форма значения {form!r} не из перечня "
                    f"{', '.join(UNIT_FORMS)}")
        слова = entry.get("words")
        if not isinstance(слова, list) or not слова:
            ctx.err(f"{w}.words", "X21", "слова записи не названы")
            слова = []
        else:
            чистые = _strs(ctx, f"{w}.words", слова, code="X21")
            if len(set(чистые)) != len(чистые):
                ctx.err(f"{w}.words", "X21", "повторы слов записи")
            for s in чистые:
                if s != s.lower():
                    ctx.err(f"{w}.words", "X21",
                            f"слово с заглавной: {s!r}")
            normalized = [_word(s) for s in чистые]
            if any(not s for s in normalized) or len(set(normalized)) != len(normalized):
                ctx.err(f"{w}.words", "X21",
                        "пустые или одинаковые после нормализации слова единиц")
            слова = чистые
        scale = entry.get("scale")
        if not isinstance(scale, dict):
            ctx.err(f"{w}.scale", "X21",
                    "множители — карта «слово → число» (пустая, если "
                    "слово единицы величины не меняет)")
            continue
        if scale and code not in SCALED_UNITS:
            ctx.err(f"{w}.scale", "X21",
                    f"множители положены только единицам "
                    f"{', '.join(SCALED_UNITS)}: у {code} слово величину "
                    f"не меняет")
        for слово, mult in sorted(scale.items()):
            if слово not in set(слова):
                ctx.err(f"{w}.scale.{слово}", "X21",
                        "множитель у слова, которого нет в words — второй "
                        "носитель слова единицы")
            if isinstance(mult, bool) or not isinstance(mult, (int, float)) \
                    or mult <= 0 or (isinstance(mult, float) and not math.isfinite(mult)):
                ctx.err(f"{w}.scale.{слово}", "X21",
                        f"множитель {mult!r} не положительное число")
        if code in SCALED_UNITS and CLOCK_WORD in set(слова) \
                and CLOCK_WORD in scale:
            ctx.err(f"{w}.scale.{CLOCK_WORD}", "X21",
                    f"у формы «{CLOCK_WORD}» множителя нет: разбор записи "
                    f"сам даёт минуты")


def _dict_checks(ctx, cat, families: list):
    d = ctx.dicts
    where = "dictionaries"
    if not ctx.catalog_070:
        for key in (DIRECTIONS_KEY, "unit_words"):
            if key in d:
                ctx.err(f"{where}.{key}", "S02", "поле доступно начиная с каталога 0.7.0")
    elif set(ctx.tokens(DIRECTIONS_KEY)) != {"minimize", "maximize"}:
        ctx.err(f"{where}.{DIRECTIONS_KEY}", "D02", "закрытый перечень: minimize и maximize")
    for key in TOKEN_KEYS:
        if key == DIRECTIONS_KEY and not ctx.catalog_070:
            continue
        v = d.get(key)
        if not isinstance(v, list) or not v:
            ctx.err(f"{where}.{key}", "D02", "перечень отсутствует или пуст")
            continue
        strs = [x for x in v if isinstance(x, str) and x]
        if len(strs) != len(v):
            ctx.err(f"{where}.{key}", "D02", "перечень не из непустых строк")
        if len(set(strs)) != len(strs):
            ctx.err(f"{where}.{key}", "D02", "повторы в перечне")
    for key in OBJECT_KEYS:
        if key == "unit_words" and not ctx.catalog_070:
            continue
        if key not in d:
            ctx.err(f"{where}.{key}", "D02", "раздел словарей отсутствует")
    vu = d.get("violation_units")
    if isinstance(vu, dict):
        if not vu:
            ctx.err(f"{where}.violation_units", "D02",
                    "карта единиц нарушения пуста")
        for code, word in vu.items():
            if not (isinstance(word, str) and word.strip()):
                ctx.err(f"{where}.violation_units.{code}", "D02",
                        "слово единицы нарушения пусто или не строка")
    elif "violation_units" in d:
        ctx.err(f"{where}.violation_units", "D02",
                "единицы нарушения — карта «код → слово», а не перечень")
    _unit_words_check(ctx, d, where)
    if set(ctx.tokens("code_units")) - set(ctx.tokens("param_units")):
        ctx.err(f"{where}.code_units", "D02", "code_units не ⊆ param_units")
    if set(ctx.tokens("stand_task_kinds")) - set(ctx.tokens("executor_kinds")):
        ctx.err(f"{where}.stand_task_kinds", "D02",
                "stand_task_kinds не ⊆ executor_kinds")
    order_raw = d.get("lexicon_order")
    if isinstance(order_raw, list):
        order = _strs(ctx, f"{where}.lexicon_order", order_raw, code="D02")
        with_words = {_sid(r) for r in families
                      if r.get("status") == "есть"
                      and isinstance(r.get("subject_words"), list)}
        if len(set(order)) != len(order) or set(order) != with_words:
            ctx.err(f"{where}.lexicon_order", "D02",
                    f"не перестановка семей со словами-носителями: "
                    f"лишние {sorted(set(order) - with_words)}, "
                    f"пропущены {sorted(with_words - set(order))}")
    sc = d.get("synthetic_codes")
    if isinstance(sc, dict):
        # dataset_ids — необязательный перечень (форма мира tables, шаг 4):
        # без ключа словарь законен, с ключом судится как stations.
        keys = ["stations", "fleet_types"]
        if "dataset_ids" in sc:
            keys.append("dataset_ids")
        for k in keys:
            if not isinstance(sc.get(k), list) or not sc.get(k):
                ctx.err(f"{where}.synthetic_codes.{k}", "D02", "перечень пуст")
            else:
                vals = _strs(ctx, f"{where}.synthetic_codes.{k}", sc[k],
                             code="D02")
                if len(set(vals)) != len(vals):
                    ctx.err(f"{where}.synthetic_codes.{k}", "D02",
                            "перечень с повторами")
        if not isinstance(sc.get("employee_pattern"), str):
            ctx.err(f"{where}.synthetic_codes.employee_pattern", "D02",
                    "образец табельного отсутствует")
        else:
            try:
                re.compile(sc["employee_pattern"])
            except re.error as e:
                ctx.err(f"{where}.synthetic_codes.employee_pattern", "D02",
                        f"образец не компилируется: {e}")
    elif "synthetic_codes" in d:
        ctx.err(f"{where}.synthetic_codes", "D02", "не объект")
    if "acceptance_default" in d and not (isinstance(d.get("acceptance_default"), str)
                                          and d["acceptance_default"].strip()):
        ctx.err(f"{where}.acceptance_default", "D02", "закон приёмки пуст")
    wf = d.get("world_forms")
    if isinstance(wf, dict):
        if not wf:
            ctx.err(f"{where}.world_forms", "D02", "нет ни одной формы мира")
        for name, spec in wf.items():
            if not isinstance(spec, dict) or not _lst(spec.get("fields")):
                ctx.err(f"{where}.world_forms.{name}", "D02",
                        "форма без перечня полей")
            else:
                _strs(ctx, f"{where}.world_forms.{name}.fields", spec["fields"],
                      code="D02")
    elif "world_forms" in d:
        ctx.err(f"{where}.world_forms", "D02", "не объект")
    ct = d.get("check_templates")
    groups = ctx.tokens("check_template_groups")
    if isinstance(ct, dict):
        if not ct:
            ctx.err(f"{where}.check_templates", "D02", "перечень пуст")
        for name, spec in ct.items():
            if not isinstance(spec, dict):
                ctx.err(f"{where}.check_templates.{name}", "D02", "не объект")
                continue
            for k in ("fields", "optional_fields", "optional_groups",
                      "at_least_one_of"):
                if not isinstance(spec.get(k), list):
                    ctx.err(f"{where}.check_templates.{name}.{k}", "D02",
                            "поле отсутствует или не список")
            if groups and spec.get("group") not in groups:
                ctx.err(f"{where}.check_templates.{name}.group", "D01",
                        f"группа {spec.get('group')!r} вне словаря "
                        f"check_template_groups")
    elif "check_templates" in d:
        ctx.err(f"{where}.check_templates", "D02", "не объект")
    # D03 / D04: unbound_params
    ub = d.get("unbound_params")
    bound = set()
    for r in families:
        bound |= {str(p.get("name")) for p in _lst(r.get("params")) if isinstance(p, dict)}
        bound |= {str(c.get("name")) for c in _lst(r.get("conditions")) if isinstance(c, dict)}
    if isinstance(ub, dict):
        for name, spec in ub.items():
            w = f"{where}.unbound_params.{name}"
            if name in bound:
                ctx.err(w, "D03", "имя уже принадлежит семье (параметр или "
                        "условие) — в unbound_params ему не место")
            if not isinstance(spec, dict):
                ctx.err(w, "D02", "не объект")
                continue
            ctx.check_token(f"{w}.unit", "D01", spec.get("unit"), "param_units")
            ctx.check_token(f"{w}.relation", "D04", spec.get("relation"),
                            "param_relations", nullable=True)
            ctx.check_token(f"{w}.source", "D04", spec.get("source"),
                            "param_sources")
            if not _PNAME_RE.match(str(name)):
                ctx.err(w, "S02", "имя параметра не по образцу")
    elif "unbound_params" in d:
        ctx.err(f"{where}.unbound_params", "D02", "не объект")


# ----------------------------------------------------- A: обезличенность
def _prose_fields_family(rec) -> list[tuple[str, str, str]]:
    """[(поле, адрес, текст)] прозы записи семьи."""
    out = []

    def add(field, addr, v):
        if isinstance(v, str) and v:
            out.append((field, addr, v))

    add("title", "title", rec.get("title"))
    add("subject", "subject", rec.get("subject"))
    for k, v in _dct(rec.get("semantics")).items():
        add("semantics", f"semantics.{k}", v)
    add("acceptance", "acceptance", rec.get("acceptance"))
    for i, p in enumerate(_lst(rec.get("params"))):
        add("note", f"params[{i}].note", _dct(p).get("note"))
    for i, c in enumerate(_lst(rec.get("conditions"))):
        add("note", f"conditions[{i}].note", _dct(c).get("note"))
    for i, s in enumerate(_lst(rec.get("param_sets"))):
        add("note", f"param_sets[{i}].note", _dct(s).get("note"))
    rf = _dct(rec.get("rule_form"))
    add("note", "rule_form.note", rf.get("note"))
    for i, f in enumerate(_lst(rf.get("fields"))):
        add("note", f"rule_form.fields[{i}].note", _dct(f).get("note"))
    for i, t in enumerate(_lst(rec.get("stand_tasks"))):
        add("note", f"stand_tasks[{i}].note", _dct(t).get("note"))
    for i, h in enumerate(_lst(rec.get("history"))):
        add("history", f"history[{i}].note", _dct(h).get("note"))
    add("origin", "origin.note", _dct(rec.get("origin")).get("note"))
    add("note", "note", rec.get("note"))
    return out


def _prose_fields_criterion(rec) -> list[tuple[str, str, str]]:
    out = []

    def add(field, addr, v):
        if isinstance(v, str) and v:
            out.append((field, addr, v))

    add("title", "title", rec.get("title"))
    add("note", "needs", rec.get("needs"))
    sem = _dct(rec.get("semantics"))
    add("semantics", "semantics.formula", sem.get("formula"))
    add("semantics", "semantics.text", sem.get("text"))
    add("semantics", "semantics.rates_form.note",
        _dct(sem.get("rates_form")).get("note"))
    inf = _dct(rec.get("instance_form"))
    add("note", "instance_form.note", inf.get("note"))
    for i, f in enumerate(_lst(inf.get("fields"))):
        add("note", f"instance_form.fields[{i}].note", _dct(f).get("note"))
    add("note", "refusal", rec.get("refusal"))
    add("acceptance", "acceptance", rec.get("acceptance"))
    for i, t in enumerate(_lst(rec.get("stand_tasks"))):
        add("note", f"stand_tasks[{i}].note", _dct(t).get("note"))
    for i, h in enumerate(_lst(rec.get("history"))):
        add("history", f"history[{i}].note", _dct(h).get("note"))
    add("origin", "origin.note", _dct(rec.get("origin")).get("note"))
    add("note", "note", rec.get("note"))
    return out


def _near(text: str, m: re.Match, ctx_re: re.Pattern, span: int = 30) -> bool:
    lo, hi = max(0, m.start() - span), min(len(text), m.end() + span)
    return ctx_re.search(text[lo:hi]) is not None


def _prose_checks(ctx, where, field, text: str):
    synthetic = _only_strs(_dct(ctx.dicts.get("synthetic_codes")).get("stations"))
    for m in _A_STATION.finditer(text):
        if m.group(0) in synthetic:
            continue
        if _near(text, m, _A_STATION_CTX):
            ctx.err(where, "A01", f"похоже на код станции рядом со словом о "
                    f"базе/аэропорте: {m.group(0)!r}")
    for m in _A_FLEET.finditer(text):
        ctx.err(where, "A01", f"тип судна в прозе: {m.group(0)!r}")
    for m in _A_TAB.finditer(text):
        if _near(text, m, _A_TAB_CTX, 40):
            ctx.err(where, "A01", f"похоже на табельный номер: {m.group(0)!r}")
    for m in _A_CLAUSE.finditer(text):
        ctx.err(where, "A01", f"ссылка на пункт документа: {m.group(0)!r}")
    for m in _A_QUOTE.finditer(text):
        ctx.err(where, "A01", "цитата в кавычках длиннее 80 знаков")
    for m in _A_RULEKEY.finditer(text):
        ctx.err(where, "A01", f"ключ правила мира заказчика: {m.group(0)!r}")
    for m in _A_MONEY.finditer(text):
        ctx.err(where, "A01", f"деньги с валютой: {m.group(0).strip()!r}")
    for m in _A_DOCDATE.finditer(text):
        ctx.err(where, "A01", f"реквизиты документа заказчика: {m.group(0)!r}")
    for pat in ctx.forbidden:
        m = pat.search(text)
        if m:
            ctx.err(where, "A02", f"маркер заказчика контура: {m.group(0)!r}")
    for label, pat in _A_CODE:
        m = pat.search(text)
        if m:
            ctx.err(where, "A05", f"символ кода в прозе ({label}): "
                    f"{m.group(0)!r}")
    for m in _A_THRESH.finditer(text):
        if _near(text, m, _A_THRESH_CTX, 40):
            msg = f"порог с единицей времени в прозе: {m.group(0)!r}"
            if field in _A_STRICT_FIELDS:
                ctx.err(where, "A06", msg)
            else:
                ctx.warn(where, "A06", msg)


#: Ячейка-код в таблицах линии данных: латинский код станции либо типа
#: судна (кириллические внутренние коды в стендовых мирах запрещены —
#: синтетика только ST1…ST9 / T1…T9).
_TABLE_CODE = re.compile(r"^[A-Z]{3,4}$")
_TABLE_KEY = re.compile(r"^([^/]+)/(.+)$")
_EMP_COLUMN = "Табельн"


def _tables_codes_check(ctx, where, world: dict):
    """A03 для формы мира `tables` (таблицы линии данных): ключ книги —
    `<идентификатор набора>/<лист>` с синтетическим идентификатором;
    ячейка-код — из синтетических станций и типов; ячейка колонки
    «Табельный …» — по синтетическому образцу."""
    sc = _dct(ctx.dicts.get("synthetic_codes"))
    stations = _only_strs(sc.get("stations"))
    fleets = _only_strs(sc.get("fleet_types"))
    datasets = _only_strs(sc.get("dataset_ids"))
    try:
        emp = re.compile(str(sc.get("employee_pattern") or "^$"))
    except re.error:
        emp = re.compile("^$")
    tables = world.get("tables")
    if not isinstance(tables, dict):
        return                       # форма без таблиц — X05 у поля формы
    for key, sheet in tables.items():
        kw = f"{where}.world.tables[{key}]"
        m = _TABLE_KEY.match(str(key))
        if not m:
            ctx.err(kw, "A03", f"ключ таблицы {key!r} не вида "
                               f"«идентификатор набора/лист»")
            continue
        if not datasets:
            # Молчаливого пропуска нет: без перечня синтетических
            # идентификаторов наборов ключ книги судить НЕЧЕМ, и это
            # говорится словами, а не превращается в зелёный исход —
            # иначе каталог без необязательного поля пропускал бы любой
            # идентификатор (в том числе набор авиакомпании).
            ctx.err(kw, "A03",
                    "словарь synthetic_codes не называет синтетических "
                    "идентификаторов наборов (dataset_ids) — идентификатор "
                    f"книги {m.group(1)!r} судить нечем; задайте dataset_ids "
                    "либо снимите задачи формы tables")
        elif m.group(1) not in datasets:
            ctx.err(kw, "A03", f"идентификатор набора {m.group(1)!r} не "
                               f"синтетический")
        for j, row in enumerate(_lst(_dct(sheet).get("rows"))):
            for col, val in _dct(row).items():
                if not isinstance(val, str):
                    continue
                if str(col).startswith(_EMP_COLUMN):
                    if not emp.match(val):
                        ctx.err(f"{kw}.rows[{j}].{col}", "A03",
                                f"табельный {val!r} не по синтетическому "
                                f"образцу")
                elif _TABLE_CODE.match(val) and val not in stations \
                        and val not in fleets:
                    ctx.err(f"{kw}.rows[{j}].{col}", "A03",
                            f"код {val!r} не синтетический (ни станция, ни "
                            f"тип судна)")
                elif _A_FLEET.search(val):
                    # тип судна настоящего справочника (A3xx, B7xx, SSJ…):
                    # образец кода из букв его не ловит, а данным
                    # авиакомпании в каталоге не место
                    ctx.err(f"{kw}.rows[{j}].{col}", "A03",
                            f"тип судна {val!r} не синтетический")


#: Ключи мира формы `schedule`, называющие СОТРУДНИКА (шаг 4: словарь
#: `world_forms.schedule` завёл назначения, историю и посуточный
#: календарь). Каждый судится синтетическим образцом табельного: иначе
#: настоящий табельный прошёл бы зелёным — молчаливый пропуск против
#: P-N-SHD.
_EMP_REFS = (("assignments", ("crew",)),
             ("history", ("crew", "employee")),
             ("history_calendar", ("employee", "crew")))


def _world_codes_check(ctx, where, world: dict):
    """A03: коды в мире стендовой задачи — только синтетические."""
    sc = _dct(ctx.dicts.get("synthetic_codes"))
    stations = _only_strs(sc.get("stations"))
    fleets = _only_strs(sc.get("fleet_types"))
    try:
        emp = re.compile(str(sc.get("employee_pattern") or "^$"))
    except re.error:
        emp = re.compile("^$")
    if world.get("form") == "tables":
        _tables_codes_check(ctx, where, world)
        return
    if world.get("form") != "schedule":
        return
    for i, f in enumerate(_lst(world.get("flights"))):
        f = _dct(f)
        for k in ("from", "to"):
            if f.get(k) is not None and str(f.get(k)) not in stations:
                ctx.err(f"{where}.world.flights[{i}].{k}", "A03",
                        f"код станции {f.get(k)!r} не синтетический")
        if f.get("fleet_type") is not None and str(f.get("fleet_type")) not in fleets:
            ctx.err(f"{where}.world.flights[{i}].fleet_type", "A03",
                    f"тип судна {f.get('fleet_type')!r} не синтетический")
    for i, c in enumerate(_lst(world.get("crews"))):
        c = _dct(c)
        if c.get("id") is not None and not emp.match(str(c.get("id"))):
            ctx.err(f"{where}.world.crews[{i}].id", "A03",
                    f"табельный {c.get('id')!r} не по синтетическому образцу")
        if c.get("base") is not None and str(c.get("base")) not in stations:
            ctx.err(f"{where}.world.crews[{i}].base", "A03",
                    f"код базы {c.get('base')!r} не синтетический")
        # Допуски сотрудника — типы судов: настоящий тип («A320») здесь
        # так же неуместен, как в поле рейса.
        for j, r in enumerate(_lst(c.get("ratings"))):
            if r is not None and str(r) not in fleets:
                ctx.err(f"{where}.world.crews[{i}].ratings[{j}]", "A03",
                        f"тип судна {r!r} не синтетический")
    for field, keys in _EMP_REFS:
        for i, row in enumerate(_lst(world.get(field))):
            row = _dct(row)
            for k in keys:
                if row.get(k) is not None and not emp.match(str(row.get(k))):
                    ctx.err(f"{where}.world.{field}[{i}].{k}", "A03",
                            f"табельный {row.get(k)!r} не по синтетическому "
                            f"образцу")


# --------------------------------------------------------- записи семей
def _stand_tasks_check(ctx, where, rec, kinds_field: str):
    kinds = _only_strs(rec.get(kinds_field))     # элементы не строки — S02 у записи
    stand_kinds = set(ctx.tokens("stand_task_kinds"))
    forms = _dct(ctx.dicts.get("world_forms"))
    seen = set()
    for i, t in enumerate(_lst(rec.get("stand_tasks"))):
        w = f"{where}.stand_tasks[{i}]"
        if not isinstance(t, dict):
            ctx.err(w, "X05", "стендовая задача не объект")
            continue
        tid = t.get("id")
        if not isinstance(tid, str):
            ctx.err(f"{w}.id", "S02", "id стендовой задачи не строка")
        elif tid in seen:
            ctx.err(w, "X05", f"повтор id стендовой задачи {tid!r}")
        else:
            seen.add(tid)
        world = _dct(t.get("world"))
        form = world.get("form")
        if not isinstance(form, str):
            ctx.err(f"{w}.world.form", "S02", "форма мира не строка")
        elif forms and form not in forms:
            ctx.err(f"{w}.world.form", "X05", f"форма мира {form!r} неизвестна")
        else:
            for fld in _only_strs(_dct(forms.get(form)).get("fields")):
                if fld not in world:
                    ctx.err(f"{w}.world", "X05", f"нет поля формы {fld!r}")
        _world_codes_check(ctx, w, world)
        exp = t.get("expected")
        if not isinstance(exp, dict) or not exp:
            ctx.err(f"{w}.expected", "X05", "ожидания пусты")
            continue
        for kind, val in exp.items():
            if kind in ("kotlin_knob", "kotlin_form"):
                from template_catalog.stand_contracts import component_expectation_problem
                problem = component_expectation_problem(kind, val)
                if problem:
                    ctx.err(f"{w}.expected.{kind}", "X05", problem)
            if kind == "export_form":
                from template_catalog.stand_contracts import export_form_expectation_problem
                problem = export_form_expectation_problem(val)
                if problem:
                    ctx.err(f"{w}.expected.{kind}", "X05", problem)
            if kind not in kinds:
                ctx.err(f"{w}.expected.{kind}", "X05",
                        f"вид {kind!r} не объявлен записью ({kinds_field})")
            if stand_kinds and kind not in stand_kinds:
                ctx.err(f"{w}.expected.{kind}", "X05",
                        f"вид {kind!r} стендом не судится")
            need = EXPECTED_FORMS.get(kind)
            if need and (not isinstance(val, dict)
                         or any(k not in val for k in need)):
                ctx.err(f"{w}.expected.{kind}", "X05",
                        f"форма ожидания вида {kind}: нужны поля {list(need)}")


def _history_check(ctx, where, rec):
    hist = _lst(rec.get("history"))
    if not hist:
        ctx.err(f"{where}.history", "X08", "история пуста")
        return
    prev = ""
    for i, h in enumerate(hist):
        h = _dct(h)
        ctx.check_token(f"{where}.history[{i}].kind", "D01", h.get("kind"),
                        "history_kinds")
        ctx.check_token(f"{where}.history[{i}].by", "D01", h.get("by"), "actors")
        d = str(h.get("date") or "")
        if not _DATE_RE.match(d):
            ctx.err(f"{where}.history[{i}].date", "S02", f"дата не по образцу: {d!r}")
        elif d < prev:
            ctx.err(f"{where}.history[{i}].date", "X08",
                    f"даты истории убывают ({prev} → {d})")
        prev = max(prev, d)


def _origin_check(ctx, where, rec):
    o = _dct(rec.get("origin"))
    ctx.check_token(f"{where}.origin.kind", "D01", o.get("kind"), "origin_kinds")
    ctx.check_token(f"{where}.origin.confirmed_by", "D01", o.get("confirmed_by"),
                    "actors")
    if o.get("date") is not None and not _DATE_RE.match(str(o.get("date"))):
        ctx.err(f"{where}.origin.date", "S02", "дата не по образцу")
    if o.get("kind") == "обобщено из ИДАК":
        if not (isinstance(o.get("note"), str) and _A_DOC_CLASS.search(o["note"])):
            ctx.err(f"{where}.origin.note", "A04",
                    "обобщение из документов заказчика обязано называть класс "
                    "документа")
        if not o.get("confirmed_by"):
            ctx.err(f"{where}.origin.confirmed_by", "A04", "подтвердивший не задан")


def _family_rules(ctx, rec, where, families_by_id: dict, *, draft: bool,
                  gates: dict, all_names: dict, stems: dict):
    """D01, X01–X19, A01–A06 для записи семьи (или черновика)."""
    fid = _sid(rec)
    ctx.check_token(f"{where}.class", "D01", rec.get("class"), "classes")
    ctx.check_token(f"{where}.kind", "D01", rec.get("kind"), "kinds")
    ctx.check_token(f"{where}.zone", "D01", rec.get("zone"), "zones")
    ctx.check_token(f"{where}.nature", "D01", rec.get("nature"), "family_natures")
    ctx.check_token(f"{where}.violation_unit", "D01", rec.get("violation_unit"),
                    "violation_units", nullable=True)
    ctx.check_token(f"{where}.violation_size", "D01", rec.get("violation_size"),
                    "violation_sizes", nullable=True)
    if not draft:
        ctx.check_token(f"{where}.status", "D01", rec.get("status"),
                        "record_statuses")
    _origin_check(ctx, where, rec)
    eks = _strs(ctx, f"{where}.executor_kinds", rec.get("executor_kinds"))
    for i, k in enumerate(eks):
        ctx.check_token(f"{where}.executor_kinds[{i}]", "D01", k, "executor_kinds")
    if not _lst(rec.get("executor_kinds")):
        ctx.err(f"{where}.executor_kinds", "X04", "виды исполнителей не названы")
    # X01 complements
    for c in _strs(ctx, f"{where}.complements", rec.get("complements")):
        if c == fid:
            ctx.err(f"{where}.complements", "X01", "самоссылка")
        elif c not in families_by_id:
            ctx.err(f"{where}.complements", "X01", f"семьи {c!r} нет")
        elif fid not in _lst(families_by_id[c].get("complements")):
            ctx.err(f"{where}.complements", "X01",
                    f"дополнение не симметрично: {c} не называет {fid}")
    # X03
    vu, kind = rec.get("violation_unit"), rec.get("kind")
    if (vu is not None) != (kind == "SOFT"):
        ctx.err(f"{where}.violation_unit", "X03",
                "единица нарушения задана ⇔ kind == SOFT")
    # X16: чем меряется штраф — числом единиц (count) или величиной
    # превышения (magnitude). Задан ровно у мягких — как единица (X03).
    if (rec.get("violation_size") is not None) != (kind == "SOFT"):
        ctx.err(f"{where}.violation_size", "X16",
                "размер нарушения задан ⇔ kind == SOFT")
    # params: D01, X02, X09
    pnames = []
    area_dict = ctx.tokens("area_qualifiers")
    for i, p in enumerate(_lst(rec.get("params"))):
        p = _dct(p)
        w = f"{where}.params[{i}]"
        name = str(p.get("name"))
        pnames.append(name)
        ctx.check_token(f"{w}.unit", "D01", p.get("unit"), "param_units")
        ctx.check_token(f"{w}.relation", "D01", p.get("relation"),
                        "param_relations", nullable=True)
        ok_q = ctx.check_token(f"{w}.qualifier", "D01", p.get("qualifier"),
                               "qualifier_kinds", nullable=True)
        owner = all_names.get(name)
        if owner is not None and owner != fid:
            ctx.err(f"{w}.name", "X02", f"имя {name!r} уже у записи {owner}")
        elif owner is None:
            all_names[name] = fid
        # X06: слова предмета ПАРАМЕТРА (0.7.0) — поле необязательное
        # (`subjects(family)` читает его рядом со словами записи); форма
        # та же, что у слов записи: строчные основы без повторов.
        if "subject_words" in p:
            if ctx.catalog_070:
                _param_subject_words(ctx, w, p["subject_words"])
            else:
                ctx.err(f"{w}.subject_words", "S02", "поле доступно начиная с каталога 0.7.0")
        # X09: уточнитель согласован с единицей параметра и словарём
        q = p.get("qualifier")
        if ok_q and q is not None and q in _QUALIFIER_UNITS:
            allowed = _QUALIFIER_UNITS[q]
            if p.get("unit") not in allowed:
                ctx.err(f"{w}.qualifier", "X09",
                        f"уточнитель {q!r} допустим только у параметров с "
                        f"единицей {', '.join(allowed)}; дано "
                        f"{p.get('unit')!r}")
            if q == "area" and not area_dict:
                ctx.err(f"{w}.qualifier", "X09",
                        "уточнитель area требует непустого словаря "
                        "area_qualifiers")
    if len(set(pnames)) != len(pnames):
        ctx.err(f"{where}.params", "X02", "повтор имени параметра в записи")
    local_names = set(pnames)
    for i, c in enumerate(_lst(rec.get("conditions"))):
        c = _dct(c)
        w = f"{where}.conditions[{i}]"
        ctx.check_token(f"{w}.unit", "D01", c.get("unit"), "condition_units")
        ctx.check_token(f"{w}.relation", "D01", c.get("relation"),
                        "condition_relations")
        name = str(c.get("name"))
        if name in local_names:
            ctx.err(f"{w}.name", "X02", f"имя условия {name!r} совпадает с "
                    f"именем параметра или другого условия записи")
        local_names.add(name)
        owner = all_names.get(name)
        if owner is not None and owner != fid:
            ctx.err(f"{w}.name", "X02", f"имя {name!r} уже у записи {owner}")
        elif owner is None:
            all_names[name] = fid
    # X12 param_sets
    sets = _lst(rec.get("param_sets"))
    if sets:
        ids = [str(_dct(s).get("id")) for s in sets]
        if len(set(ids)) != len(ids):
            ctx.err(f"{where}.param_sets", "X12", "повтор id набора")
        union = set()
        for i, s in enumerate(sets):
            ps = _strs(ctx, f"{where}.param_sets[{i}].params",
                       _dct(s).get("params"))
            if not _lst(_dct(s).get("params")):
                ctx.err(f"{where}.param_sets[{i}]", "X12", "набор пуст")
            for n in ps:
                if n not in pnames:
                    ctx.err(f"{where}.param_sets[{i}]", "X12",
                            f"имя {n!r} не из params записи")
            union |= set(ps)
        if union != set(pnames):
            ctx.err(f"{where}.param_sets", "X12",
                    f"объединение наборов ≠ params: без набора "
                    f"{sorted(set(pnames) - union)}")
    # X10 / D01 rule_form
    rf = rec.get("rule_form")
    if isinstance(rf, dict):
        fields = _lst(rf.get("fields"))
        names = _strs(ctx, f"{where}.rule_form.fields",
                      [_dct(f).get("name") for f in fields], suffix=".name")
        for i, f in enumerate(fields):
            f = _dct(f)
            vals = f.get("values")
            if f.get("name") == "tz" and isinstance(vals, list):
                for v in vals:
                    ctx.check_token(f"{where}.rule_form.fields[{i}].values",
                                    "D01", v, "param_tz")
            if f.get("name") == "days" and isinstance(vals, list):
                for v in vals:
                    ctx.check_token(f"{where}.rule_form.fields[{i}].values",
                                    "D01", v, "weekdays")
        if len(set(names)) != len(names):
            ctx.err(f"{where}.rule_form.fields", "X10", "повтор имени поля формы")
    # X11 check_templates
    cts = _strs(ctx, f"{where}.check_templates", rec.get("check_templates"))
    known_ct = _dct(ctx.dicts.get("check_templates"))
    for i, name in enumerate(cts):
        if known_ct and name not in known_ct:
            ctx.err(f"{where}.check_templates[{i}]", "X11",
                    f"шаблон проверки {name!r} вне словаря check_templates")
    if bool(_lst(rec.get("check_templates"))) != ("check_template" in eks):
        ctx.err(f"{where}.check_templates", "X11",
                "перечень шаблонов проверок непуст ⇔ check_template в "
                "executor_kinds")
    # X13 nature
    nat = rec.get("nature")
    if nat == "history" and "kotlin_logic_form" not in eks:
        ctx.err(f"{where}.executor_kinds", "X13",
                "семья истории обязана иметь вид kotlin_logic_form")
    if nat == "world_param" and kind != "HARD":
        ctx.err(f"{where}.kind", "X13", "параметр мира описательно HARD")
    # X19 / X15 чтение истории (код X14 занят полями экземпляра, см. ниже)
    rh = rec.get("reads_history")
    if nat == "history" and rh is None:
        ctx.err(f"{where}.reads_history", "X19",
                "семья истории обязана назвать, что читает из истории до "
                "горизонта")
    if rh is not None:
        if not isinstance(rh, dict):
            ctx.err(f"{where}.reads_history", "X15",
                    "чтение истории — объект {need, kind} либо null")
        else:
            ctx.check_token(f"{where}.reads_history.kind", "D01",
                            rh.get("kind"), "history_read_kinds")
            need = rh.get("need")
            if not (isinstance(need, str) and need.strip()):
                ctx.err(f"{where}.reads_history.need", "X15",
                        "потребность в истории названа непустым текстом — "
                        "по ней анкета спрашивает данные")
    # X17: природу подтверждает НЕЗАВИСИМЫЙ свидетель, а не сама запись
    want = _NATURE_WITNESSES.get(str(nat))
    got = rec.get("nature_confirmed_by")
    ok_w = ctx.check_token(f"{where}.nature_confirmed_by", "D01", got,
                           "nature_witnesses")
    if ok_w and want is not None and isinstance(got, str) and got not in want:
        ctx.err(f"{where}.nature_confirmed_by", "X17",
                f"природу {nat!r} подтверждает свидетель "
                f"{' либо '.join(want)}; дано {got!r}")
    # X18 (замечание CDPM 10.09.2026, принято): запись, пришедшая
    # КОНВЕЙЕРОМ ПОПОЛНЕНИЯ, не вправе опираться на рукописный перечень
    # сертификата ростера — его правит тот же контур, что завёл запись, и
    # природа новой семьи держалась бы на её собственном слове. Свидетель
    # такой записи — вердикт судьи заявки (change_request); «свидетеля
    # нет» (none) у людской семьи и параметра мира отказывает X17, а у
    # семьи расписания отрицательное свидетельство проверяет код (L15:
    # семьи нет ни в таблицах решателя, ни в перечнях сертификатора) —
    # подделать его заявка не может, и такие записи 0.5.0 законны.
    if (_dct(rec.get("origin")).get("kind") == ORIGIN_REQUEST
            and got == "certifier_registry"):
        ctx.err(f"{where}.nature_confirmed_by", "X18",
                "запись пришла заявкой: перечни сертификата ростера ведёт "
                "тот же контур пополнения — свидетель такой записи не они, "
                "а вердикт судьи заявки (change_request)")
    # X05 stand_tasks
    _stand_tasks_check(ctx, where, rec, "executor_kinds")
    if not draft:
        # X06 subject_words
        sw = rec.get("subject_words")
        if sw is None:
            if _dct(gates).get("subject_words_required"):
                ctx.err(f"{where}.subject_words", "X06",
                        "слова-носители обязательны (гейт включён)")
        elif isinstance(sw, list):
            if not sw:
                ctx.err(f"{where}.subject_words", "X06",
                        "пустой список запрещён — null либо основы")
            seen_local = set()
            for i, s in enumerate(sw):
                if not isinstance(s, str) or not s:
                    ctx.err(f"{where}.subject_words[{i}]", "X06", "основа пуста")
                    continue
                if s != s.lower():
                    ctx.err(f"{where}.subject_words[{i}]", "X06",
                            f"основа с заглавной: {s!r}")
                if s in seen_local:
                    ctx.err(f"{where}.subject_words[{i}]", "X06",
                            f"повтор основы {s!r}")
                seen_local.add(s)
                other = stems.get(s)
                if other is not None and other != fid:
                    ctx.err(f"{where}.subject_words[{i}]", "X06",
                            f"основа {s!r} уже у семьи {other}")
                stems.setdefault(s, fid)
        # X07 stand_tasks
        if rec.get("status") == "есть" and not _lst(rec.get("stand_tasks")):
            msg = "запись «есть» без стендовых задач"
            if _dct(gates).get("stand_tasks_required"):
                ctx.err(f"{where}.stand_tasks", "X07", msg)
            else:
                ctx.warn(f"{where}.stand_tasks", "X07", msg)
        # X08 history
        _history_check(ctx, where, rec)
    # A: проза
    for field, addr, text in _prose_fields_family(rec):
        _prose_checks(ctx, f"{where}.{addr}", field, text)


def _param_subject_words(ctx, where, words) -> None:
    """X06 для необязательного поля `params[].subject_words` (0.7.0).

    Слова предмета параметра живут рядом со словами записи (контракт П0
    §3.3 (в): «предметы — не глобальный словарь, а необязательное поле
    параметра»). Поля нет — молчим; есть — судим той же формой, что и
    слова записи: непустые строчные основы без повторов. Глобальная
    единственность основы тут НЕ спрашивается: у параметров одной семьи
    предмет общий по построению, и правило «основа принадлежит одной
    семье» (X06 записи) их не касается."""
    if not isinstance(words, list) or not words:
        ctx.err(f"{where}.subject_words", "X06",
                "пустой список запрещён — поля нет либо основы названы")
        return
    видели = set()
    for i, s in enumerate(words):
        if not isinstance(s, str) or not s:
            ctx.err(f"{where}.subject_words[{i}]", "X06", "основа пуста")
            continue
        if s != s.lower():
            ctx.err(f"{where}.subject_words[{i}]", "X06",
                    f"основа с заглавной: {s!r}")
        if s in видели:
            ctx.err(f"{where}.subject_words[{i}]", "X06",
                    f"повтор основы {s!r}")
        видели.add(s)


def _direction_check(ctx, rec, where, *, draft: bool) -> None:
    """X20 (0.7.0): направление критерия — из каталога, а не из документа.

    Контракт П0 §3.3 (A06) и сторона COSMOS «0-бис»: у записи критерия со
    статусом «есть» поле `direction` обязательно и по словарю
    `directions`; названное направление сверяют и CDPM
    (`criterion_from_objectives`), и построитель COSMOS
    (`vmp/criteria.apply_criterion`) — расхождение с документом там отказ,
    а здесь отказ тому, кто вида не объявил. У черновика заявки поле
    необязательно, но названное — судится тем же словарём: черновик без
    направления станет записью «есть» только вместе с ним."""
    if not ctx.catalog_070:
        return
    direction = rec.get("direction")
    if direction is None and (draft or rec.get("status") != "есть"):
        return
    if not isinstance(direction, str) or not direction.strip():
        ctx.err(f"{where}.direction", "X20",
                "запись критерия со статусом «есть» обязана назвать "
                "направление (minimize | maximize) — направление зоны и "
                "ставки строит каталог, а не постановка")
        return
    ctx.check_token(f"{where}.direction", "X20", direction, DIRECTIONS_KEY)


def _criterion_rules(ctx, rec, where, *, draft: bool, gates: dict):
    ctx.check_token(f"{where}.class", "D01", rec.get("class"), "classes")
    ctx.check_token(f"{where}.zone", "D01", rec.get("zone"), "zones")
    if not draft:
        ctx.check_token(f"{where}.status", "D01", rec.get("status"),
                        "record_statuses")
    _direction_check(ctx, rec, where, draft=draft)
    _origin_check(ctx, where, rec)
    cb = _strs(ctx, f"{where}.computable_by", rec.get("computable_by"))
    for i, k in enumerate(cb):
        ctx.check_token(f"{where}.computable_by[{i}]", "D01", k, "executor_kinds")
    if not _lst(rec.get("computable_by")):
        ctx.err(f"{where}.computable_by", "X04", "исполнители критерия не названы")
    sem = _dct(rec.get("semantics"))
    rf = sem.get("rates_form")
    if isinstance(rf, dict):
        ctx.check_token(f"{where}.semantics.rates_form.policy", "D01",
                        rf.get("policy"), "rates_policies")
    inf = rec.get("instance_form")
    if isinstance(inf, dict):
        names = _strs(ctx, f"{where}.instance_form.fields",
                      [_dct(f).get("name") for f in _lst(inf.get("fields"))],
                      suffix=".name")
        if len(set(names)) != len(names):
            ctx.err(f"{where}.instance_form.fields", "X14",
                    "повтор имени поля экземпляра")
        # Поля ставок — либо поля экземпляра, либо подполя поля rates
        # экземпляра (форма пакета: rates = {flight, work}).
        if isinstance(rf, dict) and "rates" not in names:
            missing = [n for n in _strs(ctx, f"{where}.semantics.rates_form.fields",
                                        rf.get("fields")) if n not in names]
            if missing:
                ctx.err(f"{where}.semantics.rates_form.fields", "X14",
                        f"поля ставок не из instance_form и поля rates "
                        f"нет: {missing}")
    _stand_tasks_check(ctx, where, rec, "computable_by")
    if not draft:
        if rec.get("status") == "есть" and not _lst(rec.get("stand_tasks")):
            msg = "запись «есть» без стендовых задач"
            if _dct(gates).get("stand_tasks_required"):
                ctx.err(f"{where}.stand_tasks", "X07", msg)
            else:
                ctx.warn(f"{where}.stand_tasks", "X07", msg)
        _history_check(ctx, where, rec)
    for field, addr, text in _prose_fields_criterion(rec):
        _prose_checks(ctx, f"{where}.{addr}", field, text)


# ----------------------------------------------------------- H: шапка
def _header_checks(ctx, cat, header: dict, versions_doc):
    where = "catalog.json"
    ok = _shape(ctx, where, header, _HEADER_SHAPE)
    if not ok:
        return
    ver = header.get("version")
    # H01 is emitted before body checks, so the root cause stays visible.
    m = _SEMVER_RE.fullmatch(str(ver))
    is_draft = bool(m and m.group(4))
    rb = header.get("released_by")
    if m and not is_draft and rb != "владелец":
        ctx.err(f"{where}/released_by", "H01",
                f"выпуск {ver} без суффикса черновика обязан быть выпущен "
                f"владельцем, дано {rb!r}")
    ctx.check_token(f"{where}/released_by", "H01", rb, "actors")
    if not _DATE_RE.match(str(header.get("released_at"))):
        ctx.err(f"{where}/released_at", "H01", "дата не по образцу ГГГГ-ММ-ДД")
    gates = _dct(header.get("gates"))
    for k in ("stand_tasks_required", "subject_words_required"):
        if not isinstance(gates.get(k), bool):
            ctx.err(f"{where}/gates.{k}", "H01", "флаг гейта обязателен")
    if header.get("name") != "template-catalog":
        ctx.err(f"{where}/name", "H01", "имя каталога — template-catalog")
    # H02
    declared = str(header.get("fingerprint") or "")
    if not _SHA256_RE.match(declared):
        ctx.err(f"{where}/fingerprint", "H02", "отпечаток не 64 hex")
    else:
        try:
            computed = cat.fingerprint()
        except Exception as e:                       # noqa: BLE001
            ctx.err(f"{where}/fingerprint", "H02", f"отпечаток не вычислим: {e}")
        else:
            if computed != declared:
                ctx.err(f"{where}/fingerprint", "H02",
                        f"отпечаток шапки {declared[:12]}… не равен "
                        f"вычисленному {computed[:12]}… — файл испорчен или "
                        f"правлен мимо инструмента")
    lrr = header.get("legacy_registry_rev")
    if lrr is not None and not re.match(r"^[0-9a-f]{12}$", str(lrr)):
        ctx.err(f"{where}/legacy_registry_rev", "H01",
                "старая ревизия реестра — 12 hex либо null")
    # H03
    if not cat.single_file:
        for sec, fname in _dct(header.get("sections")).items():
            if not isinstance(fname, str) or not (cat.path / fname).is_file():
                ctx.err(f"{where}/sections.{sec}", "H03",
                        f"файл раздела {fname!r} отсутствует")
    if versions_doc is not None:
        prev = None
        for i, v in enumerate(_lst(_dct(versions_doc).get("versions"))):
            v = _dct(v)
            w = f"versions.json/versions[{i}]"
            vm = _SEMVER_RE.match(str(v.get("version")))
            if not vm:
                ctx.err(w, "H03", f"версия {v.get('version')!r} не semver")
                continue
            if vm.group(4):
                ctx.err(w, "H03", "черновики в журнал версий не записываются")
            triple = tuple(int(vm.group(k)) for k in (1, 2, 3))
            if prev is not None and triple <= prev:
                ctx.err(w, "H03", "версии журнала обязаны строго возрастать")
            prev = triple
            if m and not is_draft and v.get("version") == ver \
                    and v.get("fingerprint") != declared:
                ctx.err(w, "H03", "отпечаток записи журнала не равен шапке")


# ---------------------------------------------------------- R: заявки
def _request_checks(ctx, cat, requests: list, families_by_id: dict,
                    criteria_by_id: dict, gates: dict, all_names: dict,
                    unbound_names: set):
    seen = set()
    for i, rq in enumerate(requests):
        w = f"requests/{_dct(rq).get('request_id', i)}"
        if not _shape(ctx, w, rq, _REQUEST_SHAPE):
            continue
        rid = rq["request_id"]
        if rid in seen:
            ctx.err(w, "R01", f"повтор request_id {rid!r}")
        seen.add(rid)
        if not _REQ_ID_RE.match(str(rid)):
            ctx.err(w, "R01", "request_id не по образцу req-NNNN")
        if not _ID_RE.match(str(rq.get("record_id"))):
            ctx.err(f"{w}.record_id", "R01", "record_id не по образцу id")
        ctx.check_token(f"{w}.state", "D01", rq.get("state"), "request_states")
        ctx.check_token(f"{w}.submitted_by", "D01", rq.get("submitted_by"),
                        "request_submitters")
        src = _dct(rq.get("source"))
        ctx.check_token(f"{w}.source.kind", "D01", src.get("kind"),
                        "request_source_kinds")
        # D01 класса документа: словарь `document_classes` необязателен
        # (он появится первой сборкой службой, шаг 6 решение 0.14), и
        # без него check_token молчит — судить нечем, а перечень
        # читателя (template_catalog.requests.DOCUMENT_CLASSES) — форма
        # экранов, не правило каталога.
        ctx.check_token(f"{w}.source.document_class", "D01",
                        src.get("document_class"), "document_classes",
                        nullable=True)
        for k in ("document_class", "run_class", "ref"):
            if isinstance(src.get(k), str):
                _prose_checks(ctx, f"{w}.source.{k}", "note", src[k])
        dec = _dct(rq.get("decision"))
        state = rq.get("state")
        if state == "подана":
            if dec.get("by") is not None:
                ctx.err(f"{w}.decision", "R02", "у поданной заявки решения нет")
        elif state in ("принята", "отклонена", "исполнитель заявлен", "есть"):
            if dec.get("by") != "владелец" or not dec.get("at") or not dec.get("words"):
                ctx.err(f"{w}.decision", "R02",
                        f"состояние «{state}» требует решения владельца "
                        f"(by == владелец, at, words)")
            if state == "исполнитель заявлен" and not rq.get("cr_key"):
                ctx.err(f"{w}.cr_key", "R02", "состояние «исполнитель заявлен» "
                        "требует ключа CR")
        # Слова решения владельца — та же проза, что и всё в каталоге:
        # они уходят в карточку заявки и на экраны, а строкой CR и
        # заданием агента не становятся (шаг 6, 0.6).
        if isinstance(dec.get("words"), str) and dec["words"].strip():
            _prose_checks(ctx, f"{w}.decision.words", "note", dec["words"])
        # Ключ CR — проекция номера заявки (закон
        # template_catalog.requests.cr_key_of): образец судится здесь,
        # само равенство ключа закону — у службы (422 bad-cr-key), ей
        # известен и номер, и закон.
        crk = rq.get("cr_key")
        if isinstance(crk, str) and not _CR_KEY_RE.match(crk):
            ctx.err(f"{w}.cr_key", "R02",
                    f"ключ CR не по образцу cr<8 hex>: {crk!r}")
        section = rq.get("section")
        registry = families_by_id if section == "families" else criteria_by_id
        exists = rq.get("record_id") in registry
        draft = rq.get("draft")
        req_kinds = rq.get("executor_kinds_requested")
        if state == "есть" and not exists:
            ctx.err(f"{w}.record_id", "R02", "состояние «есть» требует записи "
                    "в разделе")
        if state in ("подана", "принята", "исполнитель заявлен"):
            # Запись с тем же id при открытой заявке законна только у
            # заявки на вид исполнителя — пока заявка «подана» или
            # «принята». У заявки «исполнитель заявлен» запись вправе
            # уже быть: это КАНДИДАТ конвейера в судимом каталоге
            # («живой ⊕ кандидаты», пятый гейт CR); в живом каталоге
            # запись из черновика появляется одним вызовом писателя
            # вместе с состоянием «есть». Живой случай 09.09.2026: первый
            # кандидат новой семьи (duty_series_limit) — временный
            # каталог краснел этой строкой, гейты CR не судились.
            if exists and state != "исполнитель заявлен" \
                    and not (draft is None and _lst(req_kinds)):
                ctx.err(f"{w}.record_id", "R02",
                        "запись с таким id уже есть: допустима только заявка "
                        "на вид исполнителя (draft == null, "
                        "executor_kinds_requested непуст)")
            if draft is None and not _lst(req_kinds):
                ctx.err(f"{w}.draft", "R02", "заявка без черновика и без видов")
        if isinstance(req_kinds, list):
            req_kinds = _strs(ctx, f"{w}.executor_kinds_requested", req_kinds)
            for k in req_kinds:
                ctx.check_token(f"{w}.executor_kinds_requested", "D01", k,
                                "executor_kinds")
            if exists:
                declared = _only_strs(registry[rq["record_id"]].get(
                    "executor_kinds" if section == "families" else "computable_by"))
                bad = [k for k in req_kinds if k not in declared]
                if bad:
                    ctx.err(f"{w}.executor_kinds_requested", "R02",
                            f"виды {bad} не объявлены записью")
        # R03: черновик
        if isinstance(draft, dict):
            dw = f"{w}/draft"
            if draft.get("id") != rq.get("record_id"):
                ctx.err(f"{dw}.id", "R03", "draft.id ≠ record_id")
            for k in ("status", "history", "stand_tasks"):
                if k in draft:
                    ctx.err(f"{dw}.{k}", "R03", f"у черновика нет поля {k}")
            if section == "families":
                if _shape(ctx, dw, draft, _FAMILY_SHAPE):
                    # X02 по всему каталогу: копия перечня занятых имён —
                    # черновик имена не занимает (заявки независимы).
                    _family_rules(ctx, draft, dw, families_by_id, draft=True,
                                  gates=gates, all_names=dict(all_names),
                                  stems={})
                    # D03 у черновика — предупреждение: имя из
                    # unbound_params при принятии заявки уйдёт из словаря
                    # (так параметр обретает семью), но это нужно видеть.
                    for key in ("params", "conditions"):
                        for j, p in enumerate(_lst(draft.get(key))):
                            n = str(_dct(p).get("name"))
                            if n in unbound_names:
                                ctx.warn(f"{dw}.{key}[{j}].name", "D03",
                                         f"имя {n!r} сейчас в unbound_params "
                                         f"словарей — при принятии заявки "
                                         f"уйдёт оттуда")
            else:
                if _shape(ctx, dw, draft, _CRITERION_SHAPE,
                          extra_allowed=CRITERION_EXTRA if ctx.catalog_070 else ()):
                    _criterion_rules(ctx, draft, dw, draft=True, gates=gates)


# --------------------------------------------------------------- вход
def validate_catalog(cat, *, forbidden_terms=()) -> list[Problem]:
    """Все правила §11; никогда не бросает: внутренняя ошибка любого
    правила — Problem S02 «проверка прервана» поверх уже накопленных."""
    docs = cat._docs
    dicts = _dct(docs.get("dictionaries"))
    ctx = _Ctx(dicts, forbidden_terms)
    try:
        _run_rules(ctx, cat, docs, dicts)
    except Exception as e:                                   # noqa: BLE001
        ctx.err("catalog", "S02", f"проверка прервана внутренней ошибкой: "
                f"{type(e).__name__}: {e}")
    # All product doors print the first errors. Keep the selected contract
    # beside the first actionable error, including the no-jsonschema path.
    version = _dct(docs.get("header")).get("version")
    schema_version = "0.7" if _uses_schema_070(version) else "0.5"
    for i, problem in enumerate(ctx.problems):
        if problem.severity == "error":
            ctx.problems[i] = Problem(
                problem.where, problem.code,
                f"версия каталога {version!r}; схема {schema_version}: {problem.message}",
                problem.severity)
            break
    return list(ctx.problems)


def _run_rules(ctx, cat, docs: dict, dicts: dict) -> None:
    header = _dct(docs.get("header"))
    version = header.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
        ctx.err("catalog.json/version", "H01",
                f"версия заголовка {version!r} не разобрана как semver "
                "(MAJOR.MINOR.PATCH[-draft.N]); сначала исправьте версию, "
                "затем проверяйте поля тела каталога")
    ctx.catalog_070 = _uses_schema_070(header.get("version"))
    gates = _dct(header.get("gates"))
    _form_checks(ctx, docs)
    fam_doc = _dct(docs.get("families"))
    crit_doc = _dct(docs.get("criteria"))
    families = [r for r in _lst(fam_doc.get("families"))]
    criteria = [r for r in _lst(crit_doc.get("criteria"))]
    # структурный минимум S02
    good_fams, good_crits = [], []
    for i, r in enumerate(families):
        w = f"families/{_dct(r).get('id', i)}"
        if _shape(ctx, w, r, {**_FAMILY_SHAPE, **_RECORD_TAIL}):
            good_fams.append(r)
    for i, r in enumerate(criteria):
        w = f"criteria/{_dct(r).get('id', i)}"
        if _shape(ctx, w, r, {**_CRITERION_SHAPE, **_RECORD_TAIL},
                  extra_allowed=CRITERION_EXTRA if ctx.catalog_070 else ()):
            good_crits.append(r)
    for i, r in enumerate(good_fams):
        if not _ID_RE.match(str(r.get("id"))):
            ctx.err(f"families/{r.get('id')}", "S02", "id не по образцу")
    for r in good_crits:
        if not _ID_RE.match(str(r.get("id"))):
            ctx.err(f"criteria/{r.get('id')}", "S02", "id не по образцу")
    _schema_check(ctx, docs, cat.single_file)
    _order_check(ctx, cat)
    # словари
    _dict_checks(ctx, cat, good_fams)
    families_by_id = {_sid(r): r for r in good_fams}
    criteria_by_id = {_sid(r): r for r in good_crits}
    ids = [_sid(r) for r in good_fams]
    if len(set(ids)) != len(ids):
        ctx.err("families", "X02", "повтор id семьи")
    cids = [_sid(r) for r in good_crits]
    if len(set(cids)) != len(cids):
        ctx.err("criteria", "X02", "повтор id критерия")
    if set(ids) & set(cids):
        ctx.err("criteria", "X02",
                f"id критерия совпадает с семьёй: {sorted(set(ids) & set(cids))}")
    all_names: dict = {}
    stems: dict = {}
    for r in good_fams:
        _family_rules(ctx, r, f"families/{_sid(r)}", families_by_id,
                      draft=False, gates=gates, all_names=all_names, stems=stems)
    for r in good_crits:
        _criterion_rules(ctx, r, f"criteria/{_sid(r)}", draft=False, gates=gates)
    # словарные заметки
    ub = _dct(dicts.get("unbound_params"))
    for name, spec in ub.items():
        note = _dct(spec).get("note")
        if isinstance(note, str) and note:
            _prose_checks(ctx, f"dictionaries.unbound_params.{name}.note",
                          "note", note)
    if isinstance(dicts.get("acceptance_default"), str):
        _prose_checks(ctx, "dictionaries.acceptance_default", "acceptance",
                      dicts["acceptance_default"])
    # шапка, версии, заявки
    _header_checks(ctx, cat, header, docs.get("versions"))
    _request_checks(ctx, cat, _lst(_dct(docs.get("requests")).get("requests")),
                    families_by_id, criteria_by_id, gates, all_names,
                    {str(k) for k in ub})
