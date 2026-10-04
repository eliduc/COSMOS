"""Read-only landing/2 cell laws from the *pinned* catalog release.

This module does not migrate a model, infer missing fields, rebind a pin,
or authorize execution. The package reader owns archive/compatibility mode.
"""
from __future__ import annotations

import copy
import math
import re
from fractions import Fraction

from . import family_contract, semantics, units
from .errors import CatalogError


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _value_problem(value, reading, catalog):
    unit = reading["fields"]["unit"]
    qualifier = reading["fields"]["qualifier"]
    form = reading["dictionaries"]["unit"]["form"]
    if qualifier == "base_code" and isinstance(value, dict):
        if unit != "count" or not value:
            return "value: карта норм разрешена лишь для count/base_code"
        for base, number in value.items():
            if not isinstance(base, str) or not re.fullmatch(r"[A-Z0-9]{2,4}", base):
                return "value: ключ карты не канонический код базы (2–4 A–Z/0–9)"
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                return "value: норма каждой базы — целое неотрицательное число"
        return None
    if form == "boolean":
        return None if isinstance(value, bool) else "value: требуется JSON true/false"
    if form == "code":
        return None if _text(value) else "value: требуется непустое строковое обозначение"
    if form == "code_list":
        if (not isinstance(value, list) or not value
                or any(not _text(v) for v in value) or len(set(value)) != len(value)):
            return "value: требуется непустой массив уникальных строковых обозначений"
        return None
    if form not in units.UNIT_FORMS:
        return f"value: форма канона {form!r} не разрешается"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "value: канон числовой единицы — JSON number, не строка или флаг"
    if isinstance(value, float) and not math.isfinite(value):
        return "value: число должно быть конечным"
    try:
        # Already canonical: use the converter's SAME form/domain check
        # on the pinned reading, without reloading a mutable catalog path
        # or interpreting a canonical number as words a second time.
        units._canon_number(Fraction(str(value)), form, unit, value, unit)
    except units.UnitsError as error:
        return f"value: {error}"
    return None


def _condition_problem(cell, reading):
    condition = cell.get("condition")
    qualifier = reading["fields"]["qualifier"]
    value = cell.get("value")
    if condition is None:
        if qualifier == "base_code" and not isinstance(value, dict):
            return "condition: скалярная норма обязана назвать base_code"
        return None
    if (not isinstance(condition, dict) or set(condition) != {"kind", "value"}
            or not _text(condition.get("kind")) or not _text(condition.get("value"))):
        return "condition: нужен null либо объект {kind, value}, не свободный текст"
    if condition["kind"] != qualifier:
        return f"condition: kind {condition['kind']!r} не уточнитель исходной записи {qualifier!r}"
    q = reading["dictionaries"]["qualifier"]
    if not q["declared"]:
        return "condition: вид уточнителя не объявлен каталогом"
    if q["values"] is not None and condition["value"] not in q["values"]:
        return "condition: значение не входит в словарь уточнителя исходного выпуска"
    if qualifier == "base_code":
        base = condition["value"]
        if not re.fullmatch(r"[A-Z0-9]{2,4}", base):
            return "condition: требуется канонический код базы (2–4 A–Z/0–9)"
        if isinstance(value, dict) and set(value) != {base}:
            return "condition: ограничение базы расходится с ключами карты норм"
    return None


def _source_problem(source, name, rule_id):
    if not isinstance(source, dict):
        return "source: происхождение не объект"
    kind = source.get("kind")
    if kind == "rule":
        if set(source) != {"kind", "rule_id"} or source.get("rule_id") != rule_id:
            return "source: клауза должна называть правило-носитель и только rule_id"
    elif kind == "code":
        if set(source) != {"kind", "note"} or not _text(source.get("note")):
            return "source: кодовая миграция должна нести непустое note"
    elif kind == "answer":
        if set(source) != {"kind", "question", "version", "effect", "root"}:
            return "source: ответ требует точные question/version/effect/root"
        q, version, effect = source["question"], source["version"], source["effect"]
        if (not isinstance(q, dict) or set(q) != {"id", "revision", "fingerprint"}
                or not _text(q.get("id")) or not _text(q.get("fingerprint"))
                or isinstance(q.get("revision"), bool) or not isinstance(q.get("revision"), int)
                or q["revision"] < 1 or not _text(source["root"])
                or not isinstance(version, dict) or set(version) != {"fingerprint"}
                or not _text(version.get("fingerprint"))):
            return "source: точное издание вопроса/версия ответа не разрешаются"
        # Established P0 effect address: rule is the PARAMETER name, not
        # the carrier rule ID. Ownership against the document is A03's law.
        if effect != {"rule": name, "field": "value"}:
            return "source: effect должен называть данный параметр и поле value"
    else:
        return "source: неизвестный вид происхождения"
    return None


def prepare_cell(rule, name, cell, catalog):
    """Complete metadata for a TRUSTED parsed write, never migrate a rule.

    Explicit fields remain evidence and are not repaired here. The strict
    atomic writer still validates the candidate and both original carriers.
    No source or timezone is invented, and an existing scope is retained.
    """
    fit = rule.get("template_fit") or {}
    proposed = copy.deepcopy(cell)
    if fit.get("schema_version") != "landing/2":
        return proposed
    reference = family_contract.read(fit.get("family_contract"), catalog,
        family=fit.get("family"), registry_rev=fit.get("registry_rev"))
    if reference["issues"] or reference["verdict"] == semantics.INCOMPATIBLE:
        raise CatalogError("family_contract: " + "; ".join(reference["issues"] + reference["differences"]))
    pin = fit["family_contract"]
    origin = semantics.resolve_release(catalog, fingerprint=pin["catalog_fingerprint"],
                                       version=pin["catalog_version"])
    reading = semantics.cell_reading(origin, "families", pin["family_id"], name)
    if reading["unresolved"]:
        raise CatalogError("; ".join(p.get("why", "не разрешается") for p in reading["unresolved"]))
    for field in ("unit", "relation"):
        proposed.setdefault(field, reading["fields"][field])
    cells = fit.get("params")
    previous = cells.get(name) if isinstance(cells, dict) else None
    previous = previous if isinstance(previous, dict) else {}
    proposed.setdefault("condition", copy.deepcopy(previous.get("condition")))
    if "tz" in previous:
        proposed.setdefault("tz", previous["tz"])
    return proposed


def problems(rule, catalog):
    """Named problems; untouched old-format rules stay on their old path."""
    fit = rule.get("template_fit") if isinstance(rule, dict) else None
    if not isinstance(fit, dict) or "schema_version" not in fit:
        return []
    version = fit["schema_version"]
    if version == "landing/1":
        return []  # archive/compatibility and execution are separate gates
    rid = str(rule.get("id"))
    found = []

    def bad(words, name=None, code="landing-incomplete"):
        found.append({"code": code, "rule": rid, "param": name,
                      "words": f"{rid}" + (f"/{name}" if name else "") + f": {code}: {words}"})

    if version != "landing/2":
        bad(f"schema_version {version!r} неизвестна, откат на legacy запрещён", code="landing-version")
        return found
    if fit.get("category") not in ("A", "B"):
        return found
    reference = family_contract.read(fit.get("family_contract"), catalog,
                                     family=fit.get("family"), registry_rev=fit.get("registry_rev"))
    if reference["issues"] or reference["verdict"] == semantics.INCOMPATIBLE:
        bad("family_contract: " + "; ".join(reference["issues"] + reference["differences"]), code="landing-contract")
        return found
    pin = fit["family_contract"]
    try:
        origin = semantics.resolve_release(catalog, fingerprint=pin["catalog_fingerprint"],
                                           version=pin["catalog_version"])
    except CatalogError as error:
        bad(f"family_contract: {error}", code="landing-contract")
        return found
    cells = fit.get("params", {})
    if not isinstance(cells, dict):
        bad("params должен быть объектом")
        return found
    required = {"value", "unit", "relation", "condition", "quote", "source"}
    allowed = required | {"tz", "answer", "pending_revision"}
    for name, cell in cells.items():
        if not isinstance(cell, dict):
            bad("ячейка не объект", name)
            continue
        missing, extra = required - cell.keys(), cell.keys() - allowed
        if missing or extra:
            bad(f"неполная форма: отсутствует {sorted(missing)}, лишнее {sorted(extra)}", name)
            continue
        try:
            reading = semantics.cell_reading(origin, "families", pin["family_id"], name)
        except CatalogError as error:
            bad(str(error), name, "landing-contract")
            continue
        if reading["unresolved"]:
            bad("; ".join(p.get("why", "не разрешается") for p in reading["unresolved"]), name, "landing-contract")
            continue
        fields = reading["fields"]
        for field in ("unit", "relation"):
            if type(cell[field]) is not type(fields[field]) or cell[field] != fields[field]:
                bad(f"{field}: {cell[field]!r} расходится с исходной записью {fields[field]!r}", name)
        for problem in (_value_problem(cell["value"], reading, origin),
                        _condition_problem(cell, reading), _source_problem(cell["source"], name, rid)):
            if problem:
                bad(problem, name)
        if not _text(cell["quote"]):
            bad("quote: дословная цитата должна быть непустой", name)
        if fields["unit"] == "time_of_day_minutes":
            if cell.get("tz") not in origin.dictionaries().get("param_tz", []):
                bad("tz: время суток требует пояс из словаря исходного выпуска", name)
        elif "tz" in cell:
            bad("tz: пояс запрещён у единицы, отличной от времени суток", name)
    return found
